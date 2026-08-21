"""Regression tests for stock_analyzer/forward_sim.py — the Forward Portfolio
Simulator (E1 Phase 1), which replays the app's OWN mechanical rules (protective
stop, deterioration ladder, risk-off overlay) against a shocked book.

The load-bearing test here is `test_zero_shock_matches_assess_holding` — the
anti-drift invariant. `forward_sim` must re-extract the deterioration scalars at
a substituted price, which means it duplicates `assess_holding`'s extraction
math (peak window, trend MA, below-MA count). That duplication is the single
biggest risk in the module: if it silently diverges from the engine, the
simulator reports a book the app would never actually produce, and it does so
authoritatively. So at ZERO shock the two must agree exactly — tier and scalars.

Also pinned: the day1-vs-confirmed bracket (the one stated assumption), that a
missing stop is None rather than a falsy "no breach" (G-11 / OP-03 fail-loud),
that a TRIM is never liquidated as if it were an exit, and that no aggregate
fabricates a beta.
"""
import pandas as pd
import pytest

from stock_analyzer import forward_sim
from stock_analyzer.constants import (
    DETERIORATION_TREND_MA,
    GAP_TO_STOP_ROUND_DECIMALS,
)
from stock_analyzer.exit_advisor import EXIT, TRIM, WATCH, TIER_RANK, assess_holding

_MA = f"SMA_{DETERIORATION_TREND_MA}"


def _frame(closes):
    """Indicator frame with the Close + trend-MA columns the engine reads."""
    close = pd.Series([float(c) for c in closes])
    return pd.DataFrame({
        "Close": close,
        _MA: close.rolling(DETERIORATION_TREND_MA).mean(),
    })


def _flat_frame(n=80, level=100.0):
    return _frame([level] * n)


def _declining_frame(pad=0):
    """Flat at 100 for 60(+pad) bars, then a linear slide to 88 over 20 bars.

    Lands 12% off the peak with the last close below the trend MA — i.e. at the
    deep-drawdown EXIT shortcut, which fires WITHOUT trend confirmation. That
    makes it a clean fixture for the zero-shock identity check. `pad` extends
    the flat head so the frame outruns DETERIORATION_PEAK_FALLBACK_BARS and the
    peak-window branches actually discriminate.
    """
    tail = [100.0 - 12.0 * (i + 1) / 20.0 for i in range(20)]
    return _frame([100.0] * (60 + pad) + tail)


def _port_row(**kw):
    row = {
        "Ticker": "AAA", "Sector": "Semiconductors", "Shares": 10.0,
        "Avg Cost": 50.0, "Price": 100.0, "Market Value": 1000.0,
        "P&L ($)": 500.0, "P&L (%)": 100.0, "Weight (%)": 100.0,
        "Stop": 80.0,
    }
    row.update(kw)
    return row


def _held(df=None, *, atr=1.0, beta=1.0, age_days=400, material=None):
    return {
        "df": df if df is not None else _flat_frame(),
        "atr": atr,
        "position_age_days": age_days,
        "material_add_age_days": material,
        "risk_metrics": {"beta": beta},
    }


def _scenario(spy_move=-10.0, sector_key=None):
    return {"id": "test", "label": f"Test (SPY {spy_move:+.0f}%)",
            "spy_move": float(spy_move), "sector_key": sector_key}


# ── The anti-drift invariant ──────────────────────────────────────────────────

def _assert_identity(df, spy_df, *, age_days, peak_window_days, atr=1.0,
                     avg_cost=50.0, shares=10.0):
    """Engine vs sim at ZERO shock — every scalar and the tier must agree."""
    price = float(df["Close"].dropna().iloc[-1])
    engine = assess_holding(
        "AAA", df, spy_df, price=price, atr=atr, avg_cost=avg_cost,
        shares=shares, age_days=age_days, peak_window_days=peak_window_days,
    )
    sim = forward_sim.replay_position(
        ticker="AAA", df=df, price_now=price, move_pct=0.0, spy_move=0.0,
        atr=atr, avg_cost=avg_cost, shares=shares, stop=80.0, spy_df=spy_df,
        age_days=age_days, peak_window_days=peak_window_days,
    )
    if engine is None:
        # The engine found no tier; the sim must agree there is no tier — but it
        # still returns its scalars, so only the tier is comparable.
        assert sim is None or sim[forward_sim.TIER_DAY1] is None
        return sim
    assert sim is not None
    assert sim[forward_sim.TIER_DAY1] == engine["tier"]
    assert sim["dd_from_peak_pct"] == engine["dd_from_peak_pct"]
    assert sim["atr_pct"] == engine["atr_pct"]
    assert sim["sma"] == engine["sma"]
    assert sim["below_ma_count_now"] == engine["below_ma_count"]
    assert sim["peak"] == engine["peak"]
    assert sim["dollar_pnl_shocked"] == engine["dollar_pnl"]
    assert sim["rel_strength"] == engine["rel_strength"]
    return sim


def test_zero_shock_matches_assess_holding():
    """At zero shock, forward_sim's extraction must equal the live engine's.

    The anti-drift invariant. `spy_df` here is a DIFFERENT series from the
    position's, so the engine's real relative strength is genuinely non-zero —
    which is what makes this test bind on RS at all. (An earlier version passed
    the same frame for both, making RS 0.0 on both sides and leaving the RS path
    completely unconstrained; that gap hid a real defect.)
    """
    df = _declining_frame()
    spy = _frame([100.0] * 70 + [101.0 + i * 0.1 for i in range(10)])  # SPY rising
    sim = _assert_identity(df, spy, age_days=400, peak_window_days=None)
    assert sim["rel_strength"] != 0.0, "fixture must exercise a real RS value"


@pytest.mark.parametrize("age_days", [None, 30, 400])
@pytest.mark.parametrize("peak_window_days", [None, 1, 3, 14, 60])
def test_zero_shock_identity_across_every_peak_window(age_days, peak_window_days):
    """The peak-window math is shared, but pin it across the whole branch space.

    Covers the `max(2, ...)` floor (peak_window_days=1), the mid-range 5/7
    conversion, and the DETERIORATION_PEAK_FALLBACK_BARS branch (both None) —
    on a frame LONGER than the fallback so `tail(n)` actually discriminates
    between window sizes instead of always returning the whole series.
    """
    df = _declining_frame(pad=80)          # 160 bars > the 63-bar fallback
    spy = _frame([100.0] * 160)
    _assert_identity(df, spy, age_days=age_days, peak_window_days=peak_window_days)


def test_zero_shock_identity_with_a_nan_close_bar():
    """`_series_close` drops NaN Close bars; the below-MA zip must stay aligned."""
    closes = [100.0] * 60 + [100.0 - 12.0 * (i + 1) / 20.0 for i in range(20)]
    closes[75] = float("nan")
    df = _frame(closes)
    spy = _frame([100.0] * 80)
    _assert_identity(df, spy, age_days=400, peak_window_days=None)


def test_zero_shock_identity_holds_with_a_material_add_window():
    """The peak-window re-anchor must be mirrored too, not just the default."""
    df = _declining_frame()
    spy = _frame([100.0] * 80)
    sim = _assert_identity(df, spy, age_days=400, peak_window_days=14)
    # A 14-day re-anchor clips the window, so the measured drawdown must SHRINK
    # relative to the full-history read — the safe-by-construction direction.
    assert sim["peak"] < float(df["Close"].max())


# ── Relative strength must be ADDITIVE, not a replacement ─────────────────────

def test_rel_strength_adds_the_shock_to_the_engines_real_reading():
    """The engine's live RS must survive into the replay, not be overwritten.

    The defect this pins: using the scenario differential ALONE meant a name
    whose sector is absent from `_SECTOR_SHOCKS` (est_move = 0.0 in a
    sector-targeted scenario) got `0 - (-10) = +10` — a fabricated POSITIVE
    relative strength that switched TRIM off on a name the Brief is calling
    TRIM/EXIT right now, in the same session.
    """
    df = _flat_frame()
    spy = _frame([100.0] * 79 + [110.0])       # SPY +10% over the lookback
    live = forward_sim._live_rel_strength(df["Close"], spy)
    assert live < 0, "fixture must have the name genuinely lagging SPY"

    # A sector-targeted scenario that does not touch this name: est_move = 0.
    sim = forward_sim.replay_position(
        ticker="AAA", df=df, price_now=100.0, move_pct=0.0, spy_move=-10.0,
        atr=1.0, avg_cost=50.0, shares=10.0, stop=1.0, spy_df=spy, age_days=400,
    )
    assert sim["rel_strength"] == pytest.approx(live + 10.0)
    # The real weakness is still in there — it is not replaced by a clean +10.
    assert sim["rel_strength"] != 10.0


def test_missing_benchmark_degrades_the_trailing_leg_and_says_so():
    """Without a benchmark the trailing leg drops out — and that is REPORTED.

    Deliberately fixtured with a NON-ZERO differential. An earlier version used
    `move_pct == spy_move`, so the differential was zero too and the assertion
    `rel_strength == 0.0` would have held even if the fallback were deleted —
    the exact "passes for the wrong reason" pattern that let the RS defect
    through in the first place.

    It also pins the honest bad news: the engine's 0.0 is a fail-safe meaning
    "unknown RS must never open an action tier", and that guarantee does NOT
    survive composition here — 0.0 plus a negative differential is still
    negative. Hence `rel_strength_live=False` so the UI can name the row.
    """
    sim = forward_sim.replay_position(
        ticker="AAA", df=_flat_frame(), price_now=100.0, move_pct=-20.0,
        spy_move=-5.0, atr=1.0, avg_cost=50.0, shares=10.0, stop=1.0,
        spy_df=None, age_days=400,
    )
    assert sim["rel_strength_live"] is False
    assert sim["rel_strength"] == -15.0     # 0.0 trailing + (−20 − −5)
    assert sim["rel_strength"] != 0.0, "a non-zero differential must survive"


def test_rel_strength_live_is_false_on_too_little_history():
    """<= REL_STRENGTH_LOOKBACK_DAYS bars is the other degraded path."""
    short = _frame([100.0] * 12)            # MA is NaN here, so use the helper
    assert forward_sim._live_rel_strength(short["Close"], short) is None


def test_rel_strength_live_is_true_with_a_real_benchmark():
    flat = _flat_frame()
    sim = forward_sim.replay_position(
        ticker="AAA", df=flat, price_now=100.0, move_pct=-9.0, spy_move=-9.0,
        atr=1.0, avg_cost=50.0, shares=10.0, stop=1.0, spy_df=flat, age_days=400,
    )
    assert sim["rel_strength_live"] is True


def test_simulate_reports_rel_strength_degraded_names():
    port_df = pd.DataFrame([_port_row(Ticker="AAA")])
    out = forward_sim.simulate(
        _scenario(-20.0), port_df, {"AAA": _held()}, spy_df=None
    )
    assert out["rel_strength_degraded"] == ["AAA"]


# ── The one stated assumption: the day1 / confirmed bracket ───────────────────

def test_day1_and_confirmed_bracket_a_trim():
    """A fresh 9% gap-down: WATCH immediately, TRIM once the trend confirms.

    This is the whole point of rendering two columns. On day 1 the 2-of-3
    below-MA confirmation genuinely has not happened, so TRIM cannot activate;
    modelling the scenario's multi-week duration, it does.
    """
    df = _flat_frame()          # flat at 100 → SMA == 100, nothing below it yet
    sim = forward_sim.replay_position(
        ticker="AAA", df=df, price_now=100.0, move_pct=-9.0, spy_move=-5.0,
        atr=1.0, avg_cost=50.0, shares=10.0, stop=50.0, age_days=400,
    )
    assert sim is not None
    assert sim["below_ma_count_now"] == 0          # real pre-shock history
    assert sim["dd_from_peak_pct"] == 9.0
    assert sim["trend_broken_now"] is True
    assert sim["rel_strength"] == -4.0             # scenario differential
    assert sim[forward_sim.TIER_DAY1] == WATCH
    assert sim[forward_sim.TIER_CONFIRMED] == TRIM


@pytest.mark.parametrize("move_pct", [-5.0, -7.0, -9.0, -13.0, -25.0, -40.0])
def test_confirmed_is_never_weaker_than_day1(move_pct):
    """Monotonicity: assuming confirmation can only strengthen a tier.

    `below_ma_count` only ever enables TRIM; it cannot disable a deep EXIT
    (which skips confirmation by design). If this ever inverts, the bracket is
    lying about which end is the conservative one.
    """
    sim = forward_sim.replay_position(
        ticker="AAA", df=_flat_frame(), price_now=100.0, move_pct=move_pct,
        spy_move=-5.0, atr=1.0, avg_cost=50.0, shares=10.0, stop=1.0,
        age_days=400,
    )
    assert sim is not None
    day1 = TIER_RANK.get(sim[forward_sim.TIER_DAY1], 0)
    confirmed = TIER_RANK.get(sim[forward_sim.TIER_CONFIRMED], 0)
    assert confirmed >= day1


def test_a_name_shocked_in_line_with_spy_does_not_trip_the_trim_gate():
    """Flat real RS + a zero scenario differential ⇒ RS 0, so TRIM stays off."""
    flat = _flat_frame()
    sim = forward_sim.replay_position(
        ticker="AAA", df=flat, price_now=100.0, move_pct=-9.0,
        spy_move=-9.0, atr=1.0, avg_cost=50.0, shares=10.0, stop=1.0,
        spy_df=flat, age_days=400,
    )
    assert sim["rel_strength"] == 0.0
    # RS is not negative, so trim_active is gated off even with confirmation.
    assert sim[forward_sim.TIER_CONFIRMED] == WATCH


# ── Stop handling: the ratcheted stop, and fail-loud on a missing one ──────────

def test_missing_stop_is_none_never_false():
    """G-11 / OP-03: absent stop data is a gap, not a clean bill of health."""
    for bad in (None, 0.0, -1.0, float("nan")):
        sim = forward_sim.replay_position(
            ticker="AAA", df=_flat_frame(), price_now=100.0, move_pct=-30.0,
            spy_move=-30.0, atr=1.0, avg_cost=50.0, shares=10.0, stop=bad,
            age_days=400,
        )
        assert sim is not None
        assert sim["stop_breached"] is None, f"stop={bad!r} must not read as 'no breach'"
        assert sim["stop_available"] is False
        assert sim["gap_to_stop_shocked"] is None


def test_stop_breach_uses_the_briefs_exact_rounded_test():
    """Mirrors round((price-stop)/price*100, 1) <= 0 so the surfaces agree."""
    # Shocked to exactly the stop → gap 0.0 → breached (boundary is inclusive,
    # matching the Brief).
    sim = forward_sim.replay_position(
        ticker="AAA", df=_flat_frame(), price_now=100.0, move_pct=-20.0,
        spy_move=-20.0, atr=1.0, avg_cost=50.0, shares=10.0, stop=80.0,
        age_days=400,
    )
    assert sim["price_shocked"] == 80.0
    assert sim["gap_to_stop_shocked"] == 0.0
    assert sim["stop_breached"] is True

    # Comfortably above the stop → not breached.
    safe = forward_sim.replay_position(
        ticker="AAA", df=_flat_frame(), price_now=100.0, move_pct=-5.0,
        spy_move=-5.0, atr=1.0, avg_cost=50.0, shares=10.0, stop=80.0,
        age_days=400,
    )
    assert safe["stop_breached"] is False
    assert safe["gap_to_stop_shocked"] == round(
        (95.0 - 80.0) / 95.0 * 100, GAP_TO_STOP_ROUND_DECIMALS
    )


# ── Honest gaps: never fabricate a tier on absent data ────────────────────────

@pytest.mark.parametrize("df", [
    None,
    pd.DataFrame(),
    pd.DataFrame({"Open": [1.0, 2.0]}),                      # no Close
    pd.DataFrame({"Close": [1.0, 2.0]}),                     # no trend-MA column
])
def test_unjudgeable_history_returns_none(df):
    assert forward_sim.replay_position(
        ticker="AAA", df=df, price_now=100.0, move_pct=-10.0, spy_move=-10.0,
        atr=1.0, avg_cost=50.0, shares=10.0, stop=80.0, age_days=400,
    ) is None


def test_nan_trend_ma_returns_none_not_a_fabricated_break():
    """Too little history for the MA → no signal, matching assess_holding."""
    df = _frame([100.0] * 10)          # < DETERIORATION_TREND_MA bars → MA is NaN
    assert df[_MA].iloc[-1] != df[_MA].iloc[-1]
    assert forward_sim.replay_position(
        ticker="AAA", df=df, price_now=100.0, move_pct=-10.0, spy_move=-10.0,
        atr=1.0, avg_cost=50.0, shares=10.0, stop=80.0, age_days=400,
    ) is None


def test_simulate_reports_uncovered_names_rather_than_dropping_them():
    port_df = pd.DataFrame([_port_row(Ticker="AAA"), _port_row(Ticker="BBB")])
    held = {"AAA": _held(), "BBB": _held(df=_frame([100.0] * 10))}   # BBB: MA NaN
    out = forward_sim.simulate(_scenario(), port_df, held)
    assert out is not None
    assert out["uncovered"] == ["BBB"]
    assert [p["ticker"] for p in out["positions"]] == ["AAA"]


# ── Aggregation ───────────────────────────────────────────────────────────────

def test_survivors_exits_on_stop_or_exit_tier_but_holds_trims():
    positions = [
        {"ticker": "AAA", "price_shocked": 10.0, "shares": 10.0, "sector": "Tech",
         "stop_breached": True, forward_sim.TIER_CONFIRMED: None},
        {"ticker": "BBB", "price_shocked": 20.0, "shares": 10.0, "sector": "Tech",
         "stop_breached": False, forward_sim.TIER_CONFIRMED: EXIT},
        {"ticker": "CCC", "price_shocked": 30.0, "shares": 10.0, "sector": "Energy",
         "stop_breached": False, forward_sim.TIER_CONFIRMED: TRIM},
        {"ticker": "DDD", "price_shocked": 40.0, "shares": 10.0, "sector": "Energy",
         "stop_breached": None, forward_sim.TIER_CONFIRMED: WATCH},
    ]
    held = {t: _held(beta=1.0) for t in ("AAA", "BBB", "CCC", "DDD")}
    out = forward_sim._survivors(positions, forward_sim.TIER_CONFIRMED, held)

    assert out["exited_tickers"] == ["AAA", "BBB"]
    assert out["n_kept"] == 2                 # a TRIM is NOT a liquidation
    assert out["proceeds"] == 300.0           # (10+20) × 10
    assert out["kept_value"] == 700.0         # (30+40) × 10
    assert out["proceeds_pct"] == 30.0
    # A breach flag of None (stop unavailable) must not be treated as an exit.
    assert "DDD" not in out["exited_tickers"]


def test_surviving_beta_is_none_when_no_position_carries_one():
    positions = [{"ticker": "AAA", "price_shocked": 10.0, "shares": 10.0,
                  "sector": "Tech", "stop_breached": False,
                  forward_sim.TIER_CONFIRMED: None}]
    held = {"AAA": {"risk_metrics": {"beta": None}}}
    out = forward_sim._survivors(positions, forward_sim.TIER_CONFIRMED, held)
    assert out["surviving_beta"] is None, "must not fabricate a 1.0 beta"


def test_surviving_beta_is_value_weighted():
    positions = [
        {"ticker": "AAA", "price_shocked": 100.0, "shares": 1.0, "sector": "Tech",
         "stop_breached": False, forward_sim.TIER_CONFIRMED: None},
        {"ticker": "BBB", "price_shocked": 100.0, "shares": 3.0, "sector": "Tech",
         "stop_breached": False, forward_sim.TIER_CONFIRMED: None},
    ]
    held = {"AAA": _held(beta=2.0), "BBB": _held(beta=1.0)}
    out = forward_sim._survivors(positions, forward_sim.TIER_CONFIRMED, held)
    assert out["surviving_beta"] == 1.25      # (2×100 + 1×300) / 400


# ── Stop-out clustering (the headline finding) ────────────────────────────────

def _corr(matrix, names):
    return pd.DataFrame(matrix, index=names, columns=names)


def test_mean_pairwise_corr_averages_the_upper_triangle():
    corr = _corr([[1.0, 0.9, 0.5],
                  [0.9, 1.0, 0.7],
                  [0.5, 0.7, 1.0]], ["AAA", "BBB", "CCC"])
    # (0.9 + 0.5 + 0.7) / 3
    assert forward_sim.mean_pairwise_corr(["AAA", "BBB", "CCC"], corr) == 0.7
    assert forward_sim.mean_pairwise_corr(["AAA", "BBB"], corr) == 0.9


def test_mean_pairwise_corr_is_none_not_zero_when_unresolvable():
    """A missing correlation read must never render as 'uncorrelated'."""
    corr = _corr([[1.0, 0.9], [0.9, 1.0]], ["AAA", "BBB"])
    assert forward_sim.mean_pairwise_corr(["AAA"], corr) is None          # needs a pair
    assert forward_sim.mean_pairwise_corr([], corr) is None
    assert forward_sim.mean_pairwise_corr(["AAA", "BBB"], None) is None
    assert forward_sim.mean_pairwise_corr(["AAA", "BBB"], pd.DataFrame()) is None
    assert forward_sim.mean_pairwise_corr(["XXX", "YYY"], corr) is None   # absent names


def test_mean_pairwise_corr_survives_duplicate_labels():
    """Duplicate labels make .loc[a,b] return a frame — must not crash or lie."""
    corr = _corr([[1.0, 0.9, 0.4],
                  [0.9, 1.0, 0.4],
                  [0.4, 0.4, 1.0]], ["AAA", "AAA", "BBB"])
    out = forward_sim.mean_pairwise_corr(["AAA", "BBB"], corr)
    assert out is None or isinstance(out, float)


def test_mean_pairwise_corr_skips_nan_cells():
    corr = _corr([[1.0, float("nan"), 0.6],
                  [float("nan"), 1.0, 0.8],
                  [0.6, 0.8, 1.0]], ["AAA", "BBB", "CCC"])
    # AAA-BBB is NaN and skipped; (0.6 + 0.8) / 2
    assert forward_sim.mean_pairwise_corr(["AAA", "BBB", "CCC"], corr) == 0.7


# ── Shocked-input builders ────────────────────────────────────────────────────

def test_shock_spy_frame_moves_only_the_final_bar():
    spy = _flat_frame(n=250, level=100.0)
    out = forward_sim.shock_spy_frame(spy, -20.0)
    assert out is not None
    assert out["Close"].iloc[-1] == pytest.approx(80.0)
    assert out["Close"].iloc[-2] == pytest.approx(100.0)
    assert spy["Close"].iloc[-1] == pytest.approx(100.0), "must not mutate the caller's frame"


@pytest.mark.parametrize("bad", [None, pd.DataFrame(), pd.DataFrame({"Open": [1.0]})])
def test_shock_spy_frame_returns_none_without_a_close(bad):
    assert forward_sim.shock_spy_frame(bad, -20.0) is None


def test_shock_port_df_reprices_and_renormalises_weights():
    port_df = pd.DataFrame([
        _port_row(Ticker="AAA", Price=100.0, Shares=10.0),
        _port_row(Ticker="BBB", Price=100.0, Shares=10.0),
    ])
    out = forward_sim.shock_port_df(port_df, {"AAA": -50.0, "BBB": 0.0})
    aaa = out[out["Ticker"] == "AAA"].iloc[0]
    bbb = out[out["Ticker"] == "BBB"].iloc[0]
    assert aaa["Price"] == 50.0
    assert aaa["Market Value"] == 500.0
    assert bbb["Market Value"] == 1000.0
    # 500 / 1500 and 1000 / 1500
    assert aaa["Weight (%)"] == pytest.approx(100 / 3, abs=0.01)
    assert bbb["Weight (%)"] == pytest.approx(200 / 3, abs=0.01)
    assert port_df.iloc[0]["Price"] == 100.0, "must not mutate the caller's frame"


def test_shock_port_df_leaves_untouched_rows_alone():
    """A row run_scenario skipped (no market value) must not get an invented move."""
    port_df = pd.DataFrame([_port_row(Ticker="AAA"), _port_row(Ticker="ZZZ")])
    out = forward_sim.shock_port_df(port_df, {"AAA": -50.0})
    zzz = out[out["Ticker"] == "ZZZ"].iloc[0]
    assert zzz["Price"] == 100.0


# ── End to end ────────────────────────────────────────────────────────────────

def test_simulate_end_to_end_shape_and_risk_off_derived_not_assumed():
    port_df = pd.DataFrame([_port_row(Ticker="AAA")])
    held = {"AAA": _held(beta=1.5)}
    spy = _flat_frame(n=250, level=100.0)

    out = forward_sim.simulate(
        _scenario(-20.0), port_df, held,
        spy_trend_df=spy, vix_level=None,      # no VIX at all
        fragility={"severity": "fragile", "implied_move": -14.0, "pullback_pct": -10.0},
        portfolio_beta=1.2,
    )
    assert out is not None
    assert out["spy_move"] == -20.0
    assert out["n_positions"] == 1
    assert out["positions"][0]["move_pct"] == -30.0      # β 1.5 × −20%
    assert set(out["counts"]) == {forward_sim.TIER_DAY1, forward_sim.TIER_CONFIRMED}
    assert set(out["survivors"]) == {forward_sim.TIER_DAY1, forward_sim.TIER_CONFIRMED}

    # The trend leg alone arms risk-off — no VIX assumption required. This is the
    # derived-not-assumed property the design depends on.
    assert out["risk_off"]["available"] is True
    assert out["risk_off"]["armed"] is True
    assert any("200-day" in r or "below its" in r for r in out["risk_off"]["reasons"])


def test_simulate_marks_risk_off_unavailable_without_spy_history():
    """No SPY frame → 'unavailable', never a silent 'not armed'."""
    port_df = pd.DataFrame([_port_row(Ticker="AAA")])
    out = forward_sim.simulate(
        _scenario(-20.0), port_df, {"AAA": _held()},
        spy_trend_df=None, fragility={"severity": "fragile"},
    )
    assert out["risk_off"]["available"] is False
    assert out["risk_off"]["armed"] is False
    assert out["risk_off"]["cards"] == []


def test_simulate_risk_off_not_armed_when_book_is_calm():
    """Fragility is the outer AND-gate — a calm book never gets the overlay."""
    port_df = pd.DataFrame([_port_row(Ticker="AAA")])
    out = forward_sim.simulate(
        _scenario(-20.0), port_df, {"AAA": _held(beta=1.5)},
        spy_trend_df=_flat_frame(n=250), fragility={"severity": "calm"},
    )
    assert out["risk_off"]["armed"] is True     # the REGIME is risk-off
    assert out["risk_off"]["cards"] == []       # but assess_ gates on fragility


def test_simulate_returns_none_on_an_unshockable_book():
    empty = pd.DataFrame([_port_row(Ticker="AAA", **{"Market Value": 0.0})])
    assert forward_sim.simulate(_scenario(), empty, {"AAA": _held()}) is None


def test_simulate_excludes_already_reducing_names_from_the_risk_off_overlay():
    """Single-surface: a name already stopped out must not also get a risk-off card."""
    port_df = pd.DataFrame([
        _port_row(Ticker="AAA", Stop=99.0),      # a −30% shock breaches this
        _port_row(Ticker="BBB", Stop=1.0),       # survives
    ])
    held = {"AAA": _held(beta=1.5), "BBB": _held(beta=1.5)}
    out = forward_sim.simulate(
        _scenario(-20.0), port_df, held,
        spy_trend_df=_flat_frame(n=250),
        fragility={"severity": "fragile", "implied_move": -14.0, "pullback_pct": -10.0},
    )
    assert "AAA" in out["stop_outs"]
    assert "AAA" not in [c["ticker"] for c in out["risk_off"]["cards"]]


def test_risk_off_overlay_never_coexists_with_a_watch_on_the_same_name():
    """The H6 invariant: a WATCH card and a risk-off 'trim now' card must not pair.

    The live path passes `decision_bucket.all_flagged_tickers()`, which
    deliberately includes WATCH-tier names — the 2026-07-29 audit found a
    narrower TRIM/EXIT-only filter let exactly this contradiction through. The
    simulator must use the same breadth, or it reproduces the bug on one screen.
    """
    port_df = pd.DataFrame([_port_row(Ticker="AAA", Stop=1.0)])
    out = forward_sim.simulate(
        _scenario(-20.0), port_df, {"AAA": _held(beta=1.5)},
        spy_trend_df=_flat_frame(n=250),
        fragility={"severity": "fragile", "implied_move": -14.0, "pullback_pct": -10.0},
    )
    flagged = {
        p["ticker"] for p in out["positions"]
        if p[forward_sim.TIER_DAY1] is not None
        or p[forward_sim.TIER_CONFIRMED] is not None
    }
    assert flagged, "fixture must produce at least one flagged name"
    assert not (flagged & {c["ticker"] for c in out["risk_off"]["cards"]})


@pytest.mark.parametrize("frag", [None, {}, {"severity": None}, {"severity": "unknown"}])
def test_offline_fragility_is_unknown_not_a_calm_book(frag):
    """`_fragility_cache` is None when its PRODUCER FAILED, not when calm.

    Collapsing those two would render an offline read as the benign "the overlay
    would not arm" — the offline-sentinel class this app treats as a defect.
    """
    port_df = pd.DataFrame([_port_row(Ticker="AAA")])
    out = forward_sim.simulate(
        _scenario(-20.0), port_df, {"AAA": _held(beta=1.5)},
        spy_trend_df=_flat_frame(n=250), fragility=frag,
    )
    assert out["risk_off"]["fragility_available"] is False
    assert out["risk_off"]["cards"] == []


def test_fragility_available_is_true_for_every_real_severity():
    for sev in ("calm", "caution", "fragile"):
        out = forward_sim.simulate(
            _scenario(-20.0), pd.DataFrame([_port_row(Ticker="AAA")]),
            {"AAA": _held(beta=1.5)}, spy_trend_df=_flat_frame(n=250),
            fragility={"severity": sev},
        )
        assert out["risk_off"]["fragility_available"] is True


def test_a_position_with_no_market_value_gets_its_own_bucket():
    """A holding the sim can't see is a gap, not a non-holding."""
    port_df = pd.DataFrame([
        _port_row(Ticker="AAA"),
        _port_row(Ticker="ZZZ", **{"Market Value": 0.0}),
    ])
    out = forward_sim.simulate(
        _scenario(), port_df, {"AAA": _held(), "ZZZ": _held()}
    )
    assert out["no_value"] == ["ZZZ"]
    assert "ZZZ" not in [p["ticker"] for p in out["positions"]]
    assert "ZZZ" not in out["uncovered"]      # distinct reason, distinct bucket
