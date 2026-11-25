"""risk_window.book_returns / coverage_note (RiskListwiseYoung, C1: unwired).

Plan: docs/plans/risk-young-holding.md. The module is pure, so every case here
is a frame shape; none of it needs the app or a clock.
"""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer import market_time
from stock_analyzer import risk
from stock_analyzer.constants import (
    BETA_MIN_RETURNS,
    PORTFOLIO_RISK_MIN_DAYS,
    PORTFOLIO_RISK_UNMEASURED_MAX_PCT,
)
from stock_analyzer.risk_window import book_returns, coverage_note

pytestmark = pytest.mark.fast

N_BOOK = 126   # closes -> 125 returns


# ── fixtures / helpers ───────────────────────────────────────────────────────
def _sessions(n, end="2026-11-20"):
    return pd.bdate_range(end=end, periods=n)


def _closes(n, seed, end="2026-11-20", drift=0.0005):
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, 0.012, n)
    return 100.0 * np.cumprod(1 + r), _sessions(n, end)


def _frame(n, seed, end="2026-11-20"):
    vals, idx = _closes(n, seed, end)
    return pd.DataFrame({"Close": vals, "High": vals + 1, "Low": vals - 1}, index=idx)


def _port(rows):
    return pd.DataFrame(
        [{"Ticker": t, "Sector": "X", "Weight (%)": w} for t, w in rows]
    )


def _held(**frames):
    return {t: {"df": df} for t, df in frames.items()}


def _old_listwise(port_df, held_data):
    """risk.compute_portfolio_risk_metrics' port_returns, copied verbatim
    (risk.py ~521-556 at the time C1 was written)."""
    series = {}
    for ticker, data in held_data.items():
        hist = data.get("df") if data.get("df") is not None else data.get("history")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            closes = hist["Close"].dropna().copy()
            if closes.index.tz is not None:
                closes.index = closes.index.tz_localize(None)
            if len(closes) >= 10:
                series[ticker] = closes
    prices = pd.DataFrame(series).ffill().dropna()
    daily_returns = prices.pct_change().dropna()
    weights = {}
    for _, row in port_df.iterrows():
        t = row["Ticker"]
        if t in daily_returns.columns:
            weights[t] = float(row["Weight (%)"]) / 100.0
    total_w = sum(weights.values())
    weights = {t: w / total_w for t, w in weights.items()}
    port_returns = pd.Series(0.0, index=daily_returns.index)
    for t, w in weights.items():
        port_returns += daily_returns[t] * w
    return port_returns


def _book_with_young(n_young, young_pct, seed=7):
    """A 126-close book (AAA, BBB) plus a young name with n_young closes."""
    big = 100.0 - young_pct
    port = _port([("AAA", big * 0.6), ("BBB", big * 0.4), ("YNG", young_pct)])
    held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2), YNG=_frame(n_young, seed))
    return port, held


def _left_out(cov):
    return {x["ticker"]: x for x in cov["left_out"]}


# ── young vs counted at exactly the beta day ─────────────────────────────────
def test_19_returns_is_young_20_returns_is_counted():
    port, held = _book_with_young(20, 5.0)       # 20 closes = 19 returns
    s, cov = book_returns(port, held)
    assert s is not None
    lo = _left_out(cov)
    assert "YNG" in lo and lo["YNG"]["reason"] == "short_history"
    assert lo["YNG"]["n_returns"] == 19
    assert cov["state"] == "partial"

    port, held = _book_with_young(21, 5.0)       # 21 closes = 20 returns
    s, cov = book_returns(port, held)
    assert "YNG" not in _left_out(cov)
    assert cov["late_start"] == ["YNG"]


@pytest.mark.parametrize("trailing_nan_bar", [False, True])
def test_counted_day_equals_first_day_beta_vs_market_is_non_none(trailing_nan_bar):
    spy = _frame(N_BOOK, 99)
    for n_closes in range(18, 25):
        young = _frame(n_closes, 5)
        if trailing_nan_bar:
            # A final bar with no close yet (intraday): dropped everywhere.
            nxt = young.index[-1] + pd.offsets.BDay(1)
            young.loc[nxt] = [np.nan, np.nan, np.nan]
            spy.loc[nxt] = [np.nan, np.nan, np.nan]
        port = _port([("AAA", 60.0), ("BBB", 35.0), ("YNG", 5.0)])
        held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2), YNG=young)
        _, cov = book_returns(port, held)
        counted = "YNG" not in _left_out(cov)
        beta = risk.beta_vs_market(young, spy)
        assert counted == (beta is not None), (n_closes, trailing_nan_bar)
        spy = spy.iloc[:N_BOOK]


# ── the window never shrinks for a holding at or under the cap ───────────────
def test_young_name_under_cap_never_shortens_the_window():
    lengths = set()
    for n in range(1, 31):
        port, held = _book_with_young(n, 5.0)
        s, cov = book_returns(port, held)
        assert s is not None, n
        lengths.add(len(s))
        assert cov["window_returns"] == len(s)
    assert lengths == {N_BOOK - 1}


def test_share_exactly_at_cap_is_partial_and_above_cap_is_none():
    cap = PORTFOLIO_RISK_UNMEASURED_MAX_PCT
    port, held = _book_with_young(10, cap)
    s, cov = book_returns(port, held)
    assert s is not None and cov["state"] == "partial"
    assert len(s) == N_BOOK - 1

    port, held = _book_with_young(10, cap + 0.01)
    s, cov = book_returns(port, held)
    assert s is None and cov["state"] == "unmeasured"
    assert _left_out(cov)["YNG"]["reason"] == "short_history"


def test_float_sum_at_the_cap_still_counts_as_at_the_cap():
    # 6.1 + 3.9 is 10.000000000000002 in floats; the cap is inclusive.
    port = _port([("AAA", 54.0), ("BBB", 36.0), ("Y1", 6.1), ("Y2", 3.9)])
    held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2),
                 Y1=_frame(5, 3), Y2=_frame(8, 4))
    s, cov = book_returns(port, held)
    assert s is not None and cov["state"] == "partial"


def test_several_young_names_add_against_the_cap():
    port = _port([("AAA", 54.0), ("BBB", 36.0), ("Y1", 5.0), ("Y2", 5.0)])
    held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2),
                 Y1=_frame(5, 3), Y2=_frame(8, 4))
    s, cov = book_returns(port, held)
    assert s is not None and len(cov["left_out"]) == 2
    assert cov["left_out_weight_pct"] == pytest.approx(10.0)

    port = _port([("AAA", 54.0), ("BBB", 34.0), ("Y1", 6.0), ("Y2", 6.0)])
    s, cov = book_returns(port, held)
    assert s is None and cov["state"] == "unmeasured"
    assert {x["ticker"] for x in cov["left_out"]} == {"Y1", "Y2"}
    assert cov["left_out_weight_pct"] == pytest.approx(12.0)


def test_only_young_book_is_none_and_lists_every_name():
    port = _port([("Y1", 60.0), ("Y2", 40.0)])
    held = _held(Y1=_frame(8, 3), Y2=_frame(12, 4))
    s, cov = book_returns(port, held)
    assert s is None
    assert cov["state"] == "unmeasured"
    assert {x["ticker"] for x in cov["left_out"]} == {"Y1", "Y2"}
    assert cov["window_returns"] == 0


# ── no-op for normal books ───────────────────────────────────────────────────
def test_no_young_names_equals_the_old_listwise_series_exactly():
    port = _port([("AAA", 50.0), ("BBB", 30.0), ("CCC", 20.0)])
    held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2), CCC=_frame(N_BOOK, 3))
    s, cov = book_returns(port, held)
    assert cov["state"] == "full" and cov["left_out"] == []
    assert coverage_note(cov) is None
    expected = _old_listwise(port, held)
    pd.testing.assert_series_equal(s, expected, check_exact=True, check_freq=False)


def test_no_op_holds_with_unnormalised_weights_and_a_trailing_bar_gap():
    # Weights that do not sum to 100, and one name missing the last session
    # (forward-filled to a zero return, as the old builder did).
    port = _port([("AAA", 40.0), ("BBB", 25.0)])
    bbb = _frame(N_BOOK, 2).iloc[:-1]
    held = _held(AAA=_frame(N_BOOK, 1), BBB=bbb)
    s, cov = book_returns(port, held)
    assert cov["state"] == "full"
    pd.testing.assert_series_equal(
        s, _old_listwise(port, held), check_exact=True, check_freq=False
    )


def test_counted_late_starter_above_cap_matches_the_old_listwise_window():
    # 30 closes at 15% (> cap): the early days are dropped, which is exactly what
    # the old listwise cut did for a name this size.
    port = _port([("AAA", 51.0), ("BBB", 34.0), ("LATE", 15.0)])
    held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2), LATE=_frame(30, 5))
    s, cov = book_returns(port, held)
    assert s is not None and len(s) == 29
    assert cov["state"] == "full"
    pd.testing.assert_series_equal(
        s, _old_listwise(port, held), check_exact=False, check_freq=False
    )


def test_counted_late_starter_under_cap_keeps_the_long_window_and_discloses():
    port = _port([("AAA", 57.0), ("BBB", 38.0), ("LATE", 5.0)])
    held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2), LATE=_frame(30, 5))
    s, cov = book_returns(port, held)
    assert len(s) == N_BOOK - 1
    assert cov["state"] == "partial" and cov["late_start"] == ["LATE"]
    note = coverage_note(cov)
    assert "LATE" in note and "$" not in note and "**" not in note
    # An early day (LATE not trading) is the AAA / BBB weighted mix alone.
    first = s.index[0]
    base = _old_listwise(
        _port([("AAA", 57.0), ("BBB", 38.0)]),
        _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2)),
    )
    assert s.loc[first] == pytest.approx(base.loc[first])


# ── gaps, time zones, DST ────────────────────────────────────────────────────
def test_halted_name_with_interior_gap_is_counted_with_real_closes_only():
    full = _frame(N_BOOK, 2)
    halted = full.drop(full.index[60:65])           # 5 sessions with no bar
    port = _port([("AAA", 60.0), ("HLT", 40.0)])
    held = _held(AAA=_frame(N_BOOK, 1), HLT=halted)
    s, cov = book_returns(port, held)
    assert cov["state"] == "full" and cov["left_out"] == []
    assert len(s) == N_BOOK - 1
    pd.testing.assert_series_equal(
        s, _old_listwise(port, held), check_exact=True, check_freq=False
    )
    # And as a young name its n_returns counts real closes, not calendar span.
    young = _frame(25, 9)
    young = young.drop(young.index[10:16])          # 19 real closes -> 18 returns
    port = _port([("AAA", 95.0), ("HLT", 5.0)])
    held = _held(AAA=_frame(N_BOOK, 1), HLT=young)
    _, cov = book_returns(port, held)
    assert _left_out(cov)["HLT"]["n_returns"] == 18


def test_mixed_tz_aware_and_naive_indexes_give_one_row_per_session():
    naive = _frame(N_BOOK, 1)
    aware = _frame(N_BOOK, 2)
    # 16:00 ET closes stamped tz-aware; another holding on a UTC-aware index.
    aware.index = aware.index.tz_localize("America/New_York") + pd.Timedelta(hours=16)
    utc = _frame(N_BOOK, 3)
    utc.index = (utc.index.tz_localize("America/New_York") + pd.Timedelta(hours=16)).tz_convert("UTC")
    port = _port([("AAA", 40.0), ("BBB", 30.0), ("CCC", 30.0)])
    held = _held(AAA=naive, BBB=aware, CCC=utc)
    s, cov = book_returns(port, held)
    assert cov["state"] == "full"
    assert s.index.is_unique and len(s) == N_BOOK - 1
    assert s.index.tz is None
    assert (s.index == s.index.normalize()).all()


def test_dst_week_frames_stay_one_row_per_session():
    # Spans the 2026-03-08 spring-forward and 2026-11-01 fall-back days.
    for end in ("2026-03-13", "2026-11-06"):
        frames = {}
        for i, t in enumerate(("AAA", "BBB")):
            f = _frame(60, i + 1, end=end)
            f.index = f.index.tz_localize("America/New_York") + pd.Timedelta(hours=16)
            frames[t] = f
        port = _port([("AAA", 50.0), ("BBB", 50.0)])
        s, cov = book_returns(port, _held(**frames))
        assert cov["state"] == "full"
        assert s.index.is_unique and len(s) == 59


def test_duplicate_session_dates_keep_the_last_row():
    f = _frame(N_BOOK, 1)
    dup = pd.concat([f, f.iloc[[-1]].assign(Close=f["Close"].iloc[-1] * 1.5)])
    port = _port([("AAA", 100.0)])
    s, _ = book_returns(port, _held(AAA=dup))
    assert len(s) == N_BOOK - 1
    assert s.iloc[-1] == pytest.approx(f["Close"].iloc[-1] * 1.5 / f["Close"].iloc[-2] - 1)


# ── Q3: a name with no prices counts toward the share ────────────────────────
def test_holding_with_no_prices_counts_toward_the_unmeasured_share():
    port = _port([("AAA", 56.4), ("BBB", 37.6), ("GHOST", 6.0)])
    held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2))
    s, cov = book_returns(port, held)
    assert s is not None and cov["state"] == "partial"
    assert _left_out(cov)["GHOST"]["reason"] == "no_prices"
    assert _left_out(cov)["GHOST"]["weight_pct"] == pytest.approx(6.0)

    port = _port([("AAA", 52.0), ("BBB", 36.0), ("GHOST", 12.0)])
    s, cov = book_returns(port, held)
    assert s is None and cov["state"] == "unmeasured"
    assert cov["left_out_weight_pct"] == pytest.approx(12.0)


def test_unpriced_row_with_nan_weight_is_listed_but_not_in_the_share():
    port = _port([("AAA", 60.0), ("BBB", 40.0), ("NOPX", float("nan"))])
    held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2))
    s, cov = book_returns(port, held)
    assert s is not None and cov["state"] == "partial"
    assert _left_out(cov)["NOPX"]["weight_pct"] is None
    assert _left_out(cov)["NOPX"]["reason"] == "no_prices"
    assert cov["left_out_weight_pct"] == 0.0
    assert "NOPX (size unknown: no price history)" in coverage_note(cov)


def test_priced_long_enough_row_with_nan_weight_is_no_weight_not_no_prices():
    port = _port([("AAA", 60.0), ("BBB", 40.0), ("NOWT", float("nan"))])
    held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2), NOWT=_frame(N_BOOK, 3))
    s, cov = book_returns(port, held)
    assert s is not None and cov["state"] == "partial"
    row = _left_out(cov)["NOWT"]
    assert row["reason"] == "no_weight" and row["weight_pct"] is None
    assert cov["left_out_weight_pct"] == 0.0
    note = coverage_note(cov)
    assert "NOWT (size unknown)" in note and "no price history" not in note
    assert "$" not in note and "**" not in note


# ── bad input never raises ───────────────────────────────────────────────────
@pytest.mark.parametrize("port_df,held", [
    (None, None),
    (None, {}),
    (pd.DataFrame(), {}),
    (pd.DataFrame(columns=["Ticker", "Weight (%)"]), {}),
    (pd.DataFrame({"Foo": [1]}), {"AAA": {"df": pd.DataFrame()}}),
    (_port([("AAA", 100.0)]), None),
    (_port([("AAA", 100.0)]), {}),
    (_port([("AAA", 100.0)]), {"AAA": None}),
    (_port([("AAA", 100.0)]), {"AAA": {"df": pd.DataFrame()}}),
    (_port([("AAA", 100.0)]), {"AAA": {"df": pd.DataFrame({"Open": [1.0]})}}),
    (_port([("AAA", 100.0)]), {"AAA": {"df": "not a frame"}}),
    (_port([("AAA", 0.0)]), {"AAA": {"df": _frame(N_BOOK, 1)}}),
    (_port([("AAA", 100.0)]), {"AAA": {"history": _frame(3, 1)}}),
])
def test_bad_input_never_raises_and_is_unmeasured(port_df, held):
    s, cov = book_returns(port_df, held)
    assert s is None
    assert cov["state"] == "unmeasured"
    assert cov["window_returns"] == 0
    assert isinstance(cov["left_out"], list)
    coverage_note(cov)   # must not raise either


def test_history_key_is_read_when_df_is_absent():
    port = _port([("AAA", 100.0)])
    s, cov = book_returns(port, {"AAA": {"history": _frame(N_BOOK, 1)}})
    assert s is not None and cov["state"] == "full"


def test_too_few_kept_days_is_none():
    # Counted holding with 21 closes = 20 returns, min_days above that.
    port = _port([("AAA", 100.0)])
    held = _held(AAA=_frame(21, 1))
    s, _ = book_returns(port, held, min_days=21)
    assert s is None
    s, _ = book_returns(port, held, min_days=20)
    assert s is not None


def test_defaults_are_the_named_constants():
    import inspect
    sig = inspect.signature(book_returns)
    assert sig.parameters["min_returns"].default == BETA_MIN_RETURNS == 20
    assert sig.parameters["max_unmeasured_pct"].default == PORTFOLIO_RISK_UNMEASURED_MAX_PCT
    assert sig.parameters["min_days"].default == PORTFOLIO_RISK_MIN_DAYS


# ── clock-free ───────────────────────────────────────────────────────────────
def test_book_returns_never_reads_the_clock(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("risk_window must not read the clock")
    monkeypatch.setattr(market_time, "now_et", _boom)
    monkeypatch.setattr(market_time, "today_et", _boom)
    port, held = _book_with_young(12, 4.0)
    s, cov = book_returns(port, held)
    assert s is not None
    coverage_note(cov)


# ── coverage_note ────────────────────────────────────────────────────────────
def test_note_names_ticker_share_and_sessions():
    port, held = _book_with_young(10, 3.6)           # 9 returns
    _, cov = book_returns(port, held)
    note = coverage_note(cov)
    assert note == (
        "Portfolio risk measured without YNG (3.6% of the book: 9 trading days since "
        f"its first close, {BETA_MIN_RETURNS} needed); its weight is spread over the other holdings."
    )
    assert "$" not in note and "**" not in note


def test_note_for_two_names_and_a_no_price_name():
    port = _port([("AAA", 60.0), ("BBB", 30.0), ("Y1", 4.0), ("GHOST", 3.0), ("Y2", 3.0)])
    held = _held(AAA=_frame(N_BOOK, 1), BBB=_frame(N_BOOK, 2), Y1=_frame(2, 3), Y2=_frame(6, 4))
    _, cov = book_returns(port, held)
    note = coverage_note(cov)
    assert "Y1 (4.0% of the book: 1 trading day since its first close" in note
    assert "GHOST (3.0% of the book: no price history)" in note
    assert "Y2 (3.0% of the book: 5 trading days since its first close" in note
    assert "their weight is spread" in note
    assert "$" not in note and "**" not in note


def test_note_for_unmeasured_names_the_share_and_the_cap():
    port, held = _book_with_young(10, 15.0)
    s, cov = book_returns(port, held)
    assert s is None
    note = coverage_note(cov)
    assert "YNG" in note and "15.0%" in note
    assert f"{PORTFOLIO_RISK_UNMEASURED_MAX_PCT:g}%" in note
    assert "$" not in note and "**" not in note


def test_note_for_an_only_young_book_and_for_no_info():
    port = _port([("Y1", 100.0)])
    _, cov = book_returns(port, _held(Y1=_frame(8, 3)))
    note = coverage_note(cov)
    assert note and "Y1" in note
    assert coverage_note(None) is None
    assert coverage_note({}) is None
    assert coverage_note({"state": "full"}) is None
    _, bad = book_returns(None, None)
    assert "can't be measured" in coverage_note(bad)


@pytest.mark.parametrize("coverage", [
    {"state": "partial", "left_out": ["YNG"]},                                  # non-dict item
    {"state": "partial", "left_out": "YNG"},                                    # not a list
    {"state": "partial", "left_out": [{"ticker": "Y", "reason": "short_history",
                                       "n_returns": 5, "weight_pct": 3.0}],
     "min_returns": None},                                                      # present-but-None
    {"state": "unmeasured", "left_out": [{"ticker": "Y", "reason": "short_history",
                                          "n_returns": 5, "weight_pct": 12.0}],
     "unmeasured_pct": 12.0, "max_unmeasured_pct": None},                       # None cap
    {"state": "partial", "left_out": [{"ticker": "Y", "reason": "no_prices",
                                       "n_returns": 0, "weight_pct": "3.0"}]},  # string weight
    {"state": "partial", "left_out": [{"ticker": "Y", "reason": "short_history",
                                       "weight_pct": 3.0}]},                    # older dict: no n_returns
    {"state": "partial", "left_out": [], "late_start": 5},                      # not iterable
])
def test_malformed_coverage_reads_the_generic_line_never_raises(coverage):
    note = coverage_note(coverage)
    assert note == "Portfolio risk was measured without some holdings."
    assert "$" not in note and "**" not in note


# ── review fixes: non-US listings, ET-aware no-op ────────────────────────────
@pytest.mark.parametrize("zone", ["Asia/Seoul", "Europe/Berlin", "UTC"])
def test_non_us_tz_aware_listing_keeps_its_own_session_dates(zone):
    us = _frame(N_BOOK, 1)
    us.index = us.index.tz_localize("America/New_York")
    us2 = _frame(N_BOOK, 2)
    us2.index = us2.index.tz_localize("America/New_York")
    foreign = _frame(N_BOOK, 3)
    foreign.index = foreign.index.tz_localize(zone)
    port = _port([("AAA", 50.0), ("BBB", 30.0), ("FOR", 20.0)])
    held = _held(AAA=us, BBB=us2, FOR=foreign)
    s, cov = book_returns(port, held)
    assert cov["state"] == "full"
    assert len(s) == N_BOOK - 1
    assert (s.index.dayofweek < 5).all()      # no Sunday rows
    pd.testing.assert_series_equal(
        s, _old_listwise(port, held), check_exact=True, check_freq=False
    )


def test_et_book_plus_seoul_and_berlin_names_matches_the_old_series():
    frames = {}
    for i, (t, zone) in enumerate((
        ("AAA", "America/New_York"), ("BBB", "America/New_York"),
        ("KS", "Asia/Seoul"), ("DE", "Europe/Berlin"),
    )):
        f = _frame(N_BOOK, i + 1)
        f.index = f.index.tz_localize(zone)
        frames[t] = f
    port = _port([("AAA", 40.0), ("BBB", 30.0), ("KS", 15.0), ("DE", 15.0)])
    held = _held(**frames)
    s, cov = book_returns(port, held)
    assert cov["state"] == "full" and len(s) == N_BOOK - 1
    assert (s.index.dayofweek < 5).all()
    pd.testing.assert_series_equal(
        s, _old_listwise(port, held), check_exact=True, check_freq=False
    )


def test_et_aware_book_with_an_interior_gap_equals_the_old_listwise_series():
    frames = {}
    for i, t in enumerate(("AAA", "BBB", "CCC")):
        f = _frame(N_BOOK, i + 1)
        f.index = f.index.tz_localize("America/New_York")
        frames[t] = f
    frames["BBB"] = frames["BBB"].drop(frames["BBB"].index[40:43])
    port = _port([("AAA", 33.3), ("BBB", 33.3), ("CCC", 33.4)])
    held = _held(**frames)
    s, cov = book_returns(port, held)
    assert cov["state"] == "full"
    pd.testing.assert_series_equal(
        s, _old_listwise(port, held), check_exact=True, check_freq=False
    )
