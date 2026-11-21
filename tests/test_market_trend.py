"""Tests for stock_analyzer/market_trend.py: the market and sector trend labels
(display only; docs/plans/market-sector-trend.md).

All series are synthetic (no network) and dated on REAL NYSE sessions (built with
data.is_trading_day), because the module counts sessions by that calendar.

Scenario shorthand: unless a test says otherwise the S&P 500 and every fund are
flat at 100, so the S&P's 4-week return is 0 and a fund's `vs_spx` is just its own
4-week return.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import market_trend as mt
from stock_analyzer.constants import (
    RISK_OFF_TREND_MA,
    RISK_OFF_VIX_LEVEL,
    TREND_CONFIRM_SESSIONS,
    TREND_HIGH_LOOKBACK_SESSIONS,
    TREND_LAG_SPX_PTS,
    TREND_MA_SESSIONS,
    TREND_MA_SLOPE_SESSIONS,
    TREND_RETURN_SESSIONS,
)
from stock_analyzer.data import is_trading_day
from stock_analyzer.exit_advisor import risk_off_regime
from stock_analyzer.portfolio import SECTOR_ETF

pytestmark = pytest.mark.fast

AS_OF = date(2026, 11, 20)  # a Friday and a normal NYSE session
N = 300                     # sessions in a default frame
ET = ZoneInfo("America/New_York")


# ── builders ─────────────────────────────────────────────────────────────────

def _sessions_to(end: date, n: int) -> list[date]:
    out, d = [end], end
    while len(out) < n:
        d -= timedelta(days=1)
        if is_trading_day(d):
            out.append(d)
    return list(reversed(out))


def _flat(n=N, v=100.0):
    return np.full(n, v, dtype=float)


def _tail(values, n=N, base=100.0):
    """Flat `base` for n bars, with the last len(values) bars replaced."""
    a = _flat(n, base)
    a[n - len(values):] = values
    return a


def _padded(values, n=N):
    """Only the last len(values) bars exist; earlier bars are NaN (young fund)."""
    a = np.full(n, np.nan)
    a[n - len(values):] = values
    return a


def _make(cols=None, n=N, end=AS_OF):
    """Frame over trend_symbols(). cols: symbol -> array (None = all-NaN column,
    'drop' = column absent). Unlisted symbols are flat at 100."""
    cols = cols or {}
    idx = pd.DatetimeIndex(_sessions_to(end, n))
    data = {}
    for sym in mt.trend_symbols():
        v = cols.get(sym, _flat(n))
        if isinstance(v, str) and v == "drop":
            continue
        data[sym] = np.full(n, np.nan) if v is None else v
    return pd.DataFrame(data, index=idx)


def _run(cols=None, n=N, as_of=AS_OF):
    return mt.compute_trend(_make(cols, n=n), as_of=as_of)


def _row(res, sym):
    for r in res["indices"] + res["sectors"]:
        if r["symbol"] == sym:
            return r
    raise KeyError(sym)


FUND = "SOXX"  # Semiconductors; any single-sector fund would do


def _fund(cols_fund, spx=None):
    cols = {FUND: cols_fund}
    if spx is not None:
        cols["^GSPC"] = spx
    return _row(_run(cols), FUND)


# ── legs: SMA equality, the −3.04 / −3.05 boundary, flat slope ───────────────

def test_close_equal_to_sma50_is_neither_below_nor_above():
    r = _fund(_flat())
    assert r["ma50_side"] == "at"
    assert r["label"] == "steady"
    assert r["reason"] is None


def test_close_one_tick_below_sma_is_below():
    r = _fund(_tail([99.99]))
    assert r["ma50_side"] == "below"
    assert r["label"] == "weakening"
    assert r["reason"] == f"below its {TREND_MA_SESSIONS}-day average"


def test_lag_boundary_minus_3_04_does_not_trail():
    # fund ends -3.04% over the window, S&P flat: displayed -3.0, NOT trailing
    r = _fund(_tail([96.96]))
    assert r["vs_spx_pts"] == -3.0
    assert -3.0 >= -TREND_LAG_SPX_PTS
    assert r["label"] == "weakening"
    assert r["reason"] == f"below its {TREND_MA_SESSIONS}-day average"  # one leg only


def test_lag_boundary_minus_3_05_trails():
    # -3.05% shows -3.1 (half away from zero) and trails: both legs, day 1 of 3
    r = _fund(_tail([96.95]))
    assert r["vs_spx_pts"] == -3.1
    assert r["label"] == "weakening"
    assert r["reason"] == f"both signs, day 1 of {TREND_CONFIRM_SESSIONS}"


def test_trailing_uses_each_return_rounded_before_subtracting():
    # fund -3.04 -> -3.0, S&P +0.04 -> 0.0: unrounded gap would be -3.08 (trailing),
    # rounded gap is -3.0 (not trailing). The decision uses the displayed figure.
    spx = _tail(_flat(1, 100.04))
    r = _fund(_tail([96.96]), spx=spx)
    assert r["vs_spx_pts"] == -3.0
    assert r["reason"] == f"below its {TREND_MA_SESSIONS}-day average"


def test_flat_sma_slope_is_not_rising():
    # period-5 pattern: the 50-bar mean is constant, the close sits above it
    pat = np.array([110.0 if (N - 1 - i) % 5 == 0 else 100.0 for i in range(N)])
    r = _fund(pat)
    assert r["ma50_side"] == "above"
    assert r["label"] == "steady"  # above50 but rising50 is strict, so not Rising


def test_rising_needs_above_50_and_a_rising_average():
    growth = 100.0 * 1.002 ** np.arange(N)
    spx = 100.0 * 1.002 ** np.arange(N)
    r = _fund(growth, spx=spx)
    assert r["label"] == "rising"
    assert r["reason"] is None


def test_above_a_rising_50day_but_trailing_is_weakening_not_rising():
    fund = 100.0 * 1.002 ** np.arange(N)
    spx = 100.0 * 1.005 ** np.arange(N)
    r = _fund(fund, spx=spx)
    assert r["ma50_side"] == "above"
    assert r["vs_spx_pts"] < -TREND_LAG_SPX_PTS
    assert r["label"] == "weakening"
    assert r["reason"].startswith("trails the S&P 500 by ")


def test_trailing_alone_reason_names_the_gap():
    spx = _tail(_flat(TREND_RETURN_SESSIONS, 105.0))  # S&P +5% over the window
    r = _fund(_flat(), spx=spx)
    assert r["ma50_side"] == "at"
    assert r["label"] == "weakening"
    assert r["reason"] == "trails the S&P 500 by 5.0 pts"
    assert r["vs_spx_pts"] == -5.0


# ── confirmation ─────────────────────────────────────────────────────────────

def test_three_sessions_of_both_legs_is_falling():
    r = _fund(_tail([95.0] * TREND_CONFIRM_SESSIONS))
    assert r["label"] == "falling"
    assert r["confirm_k"] == TREND_CONFIRM_SESSIONS
    assert r["reason"] is None


def test_two_sessions_of_both_legs_is_weakening_day_2_of_3():
    r = _fund(_tail([95.0] * (TREND_CONFIRM_SESSIONS - 1)))
    assert r["label"] == "weakening"
    assert r["confirm_k"] == TREND_CONFIRM_SESSIONS - 1
    assert r["reason"] == f"both signs, day {TREND_CONFIRM_SESSIONS - 1} of {TREND_CONFIRM_SESSIONS}"


def test_falling_ends_the_first_session_both_legs_fail_to_steady():
    # three bad sessions, then a bounce back above the average: not Falling, and
    # with no weakness leg left it reads Steady (Falling exits instantly)
    r = _fund(_tail([95.0, 95.0, 95.0, 100.2]))
    assert r["label"] == "steady"
    assert r["confirm_k"] == 0


def test_falling_then_a_partial_bounce_leaves_one_leg_weakening():
    r = _fund(_tail([95.0, 95.0, 95.0, 98.0]))
    assert r["label"] == "weakening"
    assert r["reason"] == f"below its {TREND_MA_SESSIONS}-day average"
    assert r["confirm_k"] == 0


def test_gap_in_the_confirmation_window_cannot_confirm():
    # both legs hold on the as-of session, the fund has no bar the session before
    vals = _tail([95.0, 95.0, 95.0])
    vals[-2] = np.nan
    r = _fund(vals)
    assert r["label"] == "weakening"
    assert r["confirm_k"] == 1
    assert r["reason"] == "couldn't confirm: no close for Thu Nov 19"


def test_gap_two_sessions_back_names_that_session():
    vals = _tail([95.0, 95.0, 95.0])
    vals[-3] = np.nan
    r = _fund(vals)
    assert r["label"] == "weakening"
    assert r["confirm_k"] == 2
    assert r["reason"] == "couldn't confirm: no close for Wed Nov 18"


def test_missing_spx_bar_in_the_window_cannot_confirm():
    spx = _flat()
    spx[-2] = np.nan
    r = _fund(_tail([95.0, 95.0, 95.0]), spx=spx)
    assert r["label"] == "weakening"
    assert r["reason"] == "couldn't confirm: no S&P 500 close for Thu Nov 19"


def test_each_single_leg_is_weakening_with_its_own_reason():
    below_only = _fund(_tail([98.0]))
    assert (below_only["label"], below_only["reason"]) == (
        "weakening", f"below its {TREND_MA_SESSIONS}-day average")
    trail_only = _fund(_flat(), spx=_tail(_flat(TREND_RETURN_SESSIONS, 105.0)))
    assert trail_only["label"] == "weakening"
    assert trail_only["reason"].startswith("trails the S&P 500")


# ── no look-ahead ────────────────────────────────────────────────────────────

def test_a_bar_after_as_of_changes_nothing():
    base = _make({FUND: _tail([95.0, 95.0, 95.0])})
    later = pd.concat([base, pd.DataFrame(
        {c: [1.0] for c in base.columns}, index=pd.DatetimeIndex([date(2026, 11, 23)]))])
    a = mt.compute_trend(base, as_of=AS_OF)
    b = mt.compute_trend(later, as_of=AS_OF)
    assert a == b


def test_editing_bars_after_a_session_does_not_change_that_sessions_read():
    prior = AS_OF - timedelta(days=1)  # Thu Nov 19
    base = _make({FUND: _tail([95.0, 95.0, 95.0])})
    edited = base.copy()
    edited.loc[edited.index > pd.Timestamp(prior), :] = 500.0
    assert mt.compute_trend(base, as_of=prior) == mt.compute_trend(edited, as_of=prior)


def test_date_index_and_datetime_index_agree():
    f = _make({FUND: _tail([95.0, 95.0, 95.0])})
    g = f.copy()
    g.index = [ts.date() for ts in f.index]
    assert mt.compute_trend(f, as_of=AS_OF) == mt.compute_trend(g, as_of=AS_OF)


# ── index rows ───────────────────────────────────────────────────────────────

def _below200_above_rising50():
    a = np.empty(N)
    a[:150] = 150.0
    a[150:260] = 100.0
    a[260:] = 110.0
    return a


def test_index_below_200_only_is_weakening_and_never_rising():
    r = _row(_run({"^GSPC": _below200_above_rising50()}), "^GSPC")
    assert r["ma50_side"] == "above"
    assert r["ma200_side"] == "below"
    assert r["label"] == "weakening"
    assert r["reason"] == f"below its {RISK_OFF_TREND_MA}-day average"


def test_index_above_both_averages_and_rising_is_rising():
    r = _row(_run({"^GSPC": 100.0 * 1.002 ** np.arange(N)}), "^GSPC")
    assert (r["ma50_side"], r["ma200_side"]) == ("above", "above")
    assert r["label"] == "rising"


def test_index_below_both_for_three_sessions_is_falling():
    r = _row(_run({"^GSPC": 200.0 - 0.5 * np.arange(N)}), "^GSPC")
    assert r["label"] == "falling"
    assert r["confirm_k"] == TREND_CONFIRM_SESSIONS


def test_index_below_both_for_two_sessions_is_weakening_both_signs():
    a = _tail([80.0, 80.0])  # flat 100 then two drops under both averages
    r = _row(_run({"^GSPC": a}), "^GSPC")
    assert r["label"] == "weakening"
    assert r["reason"] == f"both signs, day 2 of {TREND_CONFIRM_SESSIONS}"


def test_index_below_200_can_never_read_steady():
    # flat 100 then one bar a hair under: below both the 50 and the 200
    r = _row(_run({"^GSPC": _tail([99.9])}), "^GSPC")
    assert r["label"] in ("weakening", "falling")


def test_spx_row_has_no_relative_figure_but_nasdaq_does():
    res = _run({"^IXIC": _tail([90.0])})
    assert _row(res, "^GSPC")["vs_spx_pts"] is None
    assert _row(res, "^IXIC")["vs_spx_pts"] == -10.0
    # the relative figure is display only: the Nasdaq label ignores it
    assert _row(res, "^IXIC")["label"] == "weakening"


def test_nasdaq_trailing_alone_does_not_change_its_label():
    spx = _tail(_flat(TREND_RETURN_SESSIONS, 110.0))
    res = _run({"^GSPC": spx})
    r = _row(res, "^IXIC")
    assert r["vs_spx_pts"] == -10.0
    assert r["label"] == "steady"


def test_index_matches_risk_off_regime_on_seeded_random_walks():
    """Wherever risk_off_regime trips on the index series, the label is Weakening
    or Falling: the tile can never contradict the Posture gauge's trend leg."""
    tripped = clear = 0
    n = 420
    for seed, drift in [(1, -0.0015), (2, -0.0008), (3, 0.0), (4, 0.0004), (5, -0.0012), (6, 0.0008)]:
        rng = np.random.default_rng(seed)
        walk = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
        frame = _make({"^GSPC": walk}, n=n)
        for pos in range(n - 100, n, 5):
            as_of = frame.index[pos].date()
            closes = pd.Series(walk[:pos + 1], index=frame.index[:pos + 1])
            trips, _ = risk_off_regime(pd.DataFrame({"Close": closes}), None,
                                       trend_ma=RISK_OFF_TREND_MA,
                                       vix_threshold=RISK_OFF_VIX_LEVEL)
            res = mt.compute_trend(frame, as_of=as_of)
            row = _row(res, "^GSPC")
            if trips:
                tripped += 1
                assert row["label"] in ("weakening", "falling"), (seed, as_of)
                assert row["ma200_side"] == "below"
            else:
                clear += 1
                assert row["ma200_side"] in ("above", "at")
    assert tripped > 0 and clear > 0  # the invariant was exercised both ways


def test_module_does_not_import_exit_advisor():
    assert not hasattr(mt, "exit_advisor")
    assert not hasattr(mt, "risk_off_regime")


# ── drawdown words ───────────────────────────────────────────────────────────

def _spx_last(last, n=N):
    return _tail([last], n=n)


@pytest.mark.parametrize("last,from_high,word", [
    (90.06, -9.9, None),             # -9.94 shows -9.9: no word
    (90.05, -10.0, "correction"),    # -9.95 shows -10.0 (half away): inclusive
    (90.0, -10.0, "correction"),
    (80.06, -19.9, "correction"),
    (80.05, -20.0, "bear market"),   # -19.95 shows -20.0: inclusive
    (70.0, -30.0, "bear market"),
    (100.0, 0.0, None),
])
def test_drawdown_word_is_decided_on_the_rounded_value(last, from_high, word):
    r = _row(_run({"^GSPC": _spx_last(last)}), "^GSPC")
    assert r["from_high_pct"] == from_high
    assert r["drawdown_word"] == word


def test_a_sector_gets_no_drawdown_word_even_at_minus_25():
    r = _row(_run({FUND: _tail([75.0])}), FUND)
    assert r["from_high_pct"] == -25.0
    assert r["drawdown_word"] is None


def test_a_high_older_than_the_lookback_is_ignored():
    a = _flat()
    a[N - TREND_HIGH_LOOKBACK_SESSIONS - 5] = 200.0  # just outside the window
    a[-1] = 95.0
    r = _row(_run({"^GSPC": a}), "^GSPC")
    assert r["from_high_pct"] == -5.0
    assert r["drawdown_word"] is None


def test_a_high_inside_the_lookback_counts():
    a = _flat()
    a[N - TREND_HIGH_LOOKBACK_SESSIONS] = 200.0  # first bar inside the window
    a[-1] = 95.0
    r = _row(_run({"^GSPC": a}), "^GSPC")
    assert r["from_high_pct"] == -52.5
    assert r["drawdown_word"] == "bear market"


def test_fewer_closes_than_the_lookback_gives_no_from_high():
    n = TREND_HIGH_LOOKBACK_SESSIONS - 1
    r = _row(_run({"^GSPC": _tail([70.0], n=n)}, n=n), "^GSPC")
    assert r["from_high_pct"] is None
    assert r["drawdown_word"] is None
    assert r["label"] is not None  # the label itself still reads


# ── as_of_session ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("now,expected", [
    (datetime(2026, 11, 21, 12, 0, tzinfo=ET), date(2026, 11, 20)),   # Saturday
    (datetime(2026, 11, 22, 12, 0, tzinfo=ET), date(2026, 11, 20)),   # Sunday
    (datetime(2026, 11, 23, 8, 0, tzinfo=ET), date(2026, 11, 20)),    # Monday pre-market
    (datetime(2026, 11, 23, 17, 0, tzinfo=ET), date(2026, 11, 20)),   # Monday after close
    (datetime(2026, 11, 26, 10, 0, tzinfo=ET), date(2026, 11, 25)),   # Thanksgiving
    (datetime(2026, 11, 27, 14, 0, tzinfo=ET), date(2026, 11, 25)),   # day after, half day
    (datetime(2026, 11, 28, 10, 0, tzinfo=ET), date(2026, 11, 27)),   # Saturday after
    (datetime(2026, 3, 8, 12, 0, tzinfo=ET), date(2026, 3, 6)),       # spring-forward Sunday
    (datetime(2026, 11, 1, 12, 0, tzinfo=ET), date(2026, 10, 30)),    # fall-back Sunday
    (datetime(2026, 11, 17, 23, 30, tzinfo=ET), date(2026, 11, 16)),  # stays the ET date
    (datetime(2026, 11, 18, 10, 0, tzinfo=ET), date(2026, 11, 17)),   # ordinary Wednesday
])
def test_as_of_session_is_the_last_completed_session_before_today(now, expected):
    assert mt.as_of_session(now) == expected


def test_as_of_session_ignores_the_clock_within_a_day():
    mornings = [mt.as_of_session(datetime(2026, 11, 18, h, 0, tzinfo=ET)) for h in (0, 9, 16, 23)]
    assert len(set(mornings)) == 1


def test_history_start_reaches_back_enough_for_the_high_and_200_day():
    start = mt.history_start(AS_OF)
    assert (AS_OF - start).days == 400
    sessions = sum(1 for i in range(400) if is_trading_day(start + timedelta(days=i)))
    assert sessions >= TREND_HIGH_LOOKBACK_SESSIONS + 5


# ── missing bars and short history ───────────────────────────────────────────

def test_fund_without_the_as_of_bar_is_not_checked():
    vals = _flat()
    vals[-1] = np.nan
    res = _run({FUND: vals})
    r = _row(res, FUND)
    assert r["label"] is None
    assert r["reason"] == "not checked: no close for Fri Nov 20"
    assert FUND in res["not_checked"]
    assert r["ret_4w"] is None and r["ma50_side"] is None


def test_spx_without_the_as_of_bar_gives_no_result():
    a = _flat()
    a[-1] = np.nan
    assert _run({"^GSPC": a}) is None


def test_spx_column_absent_gives_no_result():
    assert _run({"^GSPC": "drop"}) is None


def test_spx_with_too_little_history_gives_no_result():
    assert _run({"^GSPC": _padded(np.full(100, 100.0))}) is None


def test_none_and_empty_frames_give_no_result():
    assert mt.compute_trend(None, as_of=AS_OF) is None
    assert mt.compute_trend(pd.DataFrame(), as_of=AS_OF) is None


def test_all_nan_fund_column_is_not_checked():
    res = _run({FUND: None})
    r = _row(res, FUND)
    assert r["label"] is None
    assert r["reason"] == "not checked: no price data"
    assert FUND in res["not_checked"]


def test_fund_column_absent_is_not_checked():
    res = _run({FUND: "drop"})
    assert _row(res, FUND)["label"] is None
    assert FUND in res["not_checked"]


def test_sector_needs_enough_closes_for_the_average_the_slope_and_the_window():
    need = TREND_MA_SESSIONS + TREND_MA_SLOPE_SESSIONS + TREND_CONFIRM_SESSIONS - 1
    ok = _row(_run({FUND: _padded(np.full(need, 100.0))}), FUND)
    assert ok["label"] == "steady"
    short = _row(_run({FUND: _padded(np.full(need - 1, 100.0))}), FUND)
    assert short["label"] is None
    assert short["reason"] == f"not enough price history ({need - 1} sessions)"


def test_index_needs_enough_closes_for_the_200_day_and_the_window():
    need = RISK_OFF_TREND_MA + TREND_CONFIRM_SESSIONS - 1
    ok = _row(_run({"^IXIC": _padded(np.full(need, 100.0))}), "^IXIC")
    assert ok["label"] is not None
    short = _row(_run({"^IXIC": _padded(np.full(need - 1, 100.0))}), "^IXIC")
    assert short["label"] is None
    assert short["reason"] == f"not enough price history ({need - 1} sessions)"
    assert "^IXIC" in _run({"^IXIC": _padded(np.full(need - 1, 100.0))})["not_checked"]


def test_result_shape_and_funds():
    res = _run()
    assert res["as_of"] == AS_OF
    assert [r["symbol"] for r in res["indices"]] == ["^GSPC", "^IXIC"]
    assert [r["name"] for r in res["indices"]] == ["S&P 500", "Nasdaq"]
    assert res["not_checked"] == []
    for r in res["indices"] + res["sectors"]:
        assert set(r) >= set(mt._ROW_KEYS)


# ── rows: funds, names, windows, run length ──────────────────────────────────

def test_every_non_spy_fund_appears_exactly_once_and_spy_never():
    res = _run()
    syms = [r["symbol"] for r in res["sectors"]]
    assert len(syms) == len(set(syms))
    assert set(syms) == {f for f in SECTOR_ETF.values() if f != "SPY"}
    assert "SPY" not in syms
    assert mt.trend_symbols() == ["^GSPC", "^IXIC"] + syms


def test_fund_names():
    res = _run()
    igv = _row(res, "IGV")
    assert igv["name"] == "Software"
    assert igv["app_sectors"] == ["AI & Cloud", "AI & Data", "Enterprise Tech"]
    assert _row(res, "SOXX")["name"] == "Semiconductors"
    assert _row(res, "XLE")["app_sectors"] == ["Energy"]
    # a multi-sector fund with no display name joins its sectors
    assert mt._fund_name("ZZZ", ["A", "B"]) == "A · B"


def test_ma50_run_counts_consecutive_closes_on_the_same_side():
    r = _fund(_tail([90.0] * 14))
    assert r["ma50_side"] == "below"
    assert r["ma50_run"] == 14
    assert r["ma50_run_capped"] is False
    row = [x for x in mt.table_rows(_run({FUND: _tail([90.0] * 14)}), None) if x["symbol"] == FUND][0]
    assert row["ma50_text"] == "below, 14 sessions"


def test_ma50_run_is_capped_when_history_runs_out_on_the_same_side():
    # 60 closes, steadily falling: below its average on every computable day
    vals = _padded(200.0 - np.arange(60))
    res = _run({FUND: vals})
    r = _row(res, FUND)
    assert r["ma50_side"] == "below"
    assert r["ma50_run"] == 60 - TREND_MA_SESSIONS + 1
    assert r["ma50_run_capped"] is True
    row = [x for x in mt.table_rows(res, None) if x["symbol"] == FUND][0]
    assert row["ma50_text"] == f"below, ≥{60 - TREND_MA_SESSIONS + 1} sessions"


def test_ma50_text_for_at_and_one_session():
    res = _run()
    flat_row = [x for x in mt.table_rows(res, None) if x["symbol"] == FUND][0]
    assert flat_row["ma50_text"] == f"at its {TREND_MA_SESSIONS}-day"
    one = _run({FUND: _tail([99.0])})
    r1 = [x for x in mt.table_rows(one, None) if x["symbol"] == FUND][0]
    assert r1["ma50_text"] == "below, 1 session"


def test_display_returns_are_rounded_and_anchored_by_session():
    a = 100.0 * 1.01 ** np.arange(N)
    r = _row(_run({FUND: a}), FUND)
    for key, k in (("ret_1w", 5), ("ret_4w", 20), ("ret_3m", 63), ("ret_6m", 126)):
        expect = (a[-1] / a[-1 - k] - 1) * 100
        assert r[key] == pytest.approx(expect, abs=0.051)
        assert r[key] == round(r[key], 1)


def test_return_is_none_when_the_anchor_predates_the_data():
    need = TREND_MA_SESSIONS + TREND_MA_SLOPE_SESSIONS + TREND_CONFIRM_SESSIONS - 1
    r = _row(_run({FUND: _padded(np.full(need, 100.0))}), FUND)
    assert r["ret_4w"] == 0.0
    assert r["ret_6m"] is None  # 57 closes cannot reach 126 sessions back
    assert r["ret_3m"] is None


def test_base_date_is_shared_with_the_spx_when_a_fund_misses_an_interior_bar():
    # The fund misses one bar INSIDE the 4-week window. Calendar anchoring takes
    # the close ON the session 20 before the as-of (100.0); counting 20 valid
    # closes back would skip the gap and land on the 200.0 (about -50%).
    vals = np.full(N, 100.0)
    vals[-10] = np.nan   # interior gap, outside the 3-session confirmation window
    vals[-22] = 200.0    # the session 21 before the as-of
    vals[-21] = 100.0    # the anchor session: 20 before the as-of
    r = _row(_run({FUND: vals}), FUND)
    assert r["ret_4w"] == 0.0


def test_slope_compares_with_the_average_five_closes_back_not_one():
    # The 50-day average ticks UP on the as-of close but is still below its value
    # 5 closes earlier: four high closes (130) drop out of the window over the
    # 4 sessions before, the last close to drop out is 100 and every new close
    # is 101. With a 1-close slope this would read Rising; with 5 it is Steady.
    assert TREND_MA_SLOPE_SESSIONS == 5
    vals = np.full(N, 100.0)
    vals[-55:-51] = 130.0   # closes 54..51 before the as-of
    vals[-5:] = 101.0       # the last 5 closes, incl. the as-of
    r = _fund(vals)
    assert r["ma50_side"] == "above"
    assert r["vs_spx_pts"] == 1.0          # no weakness leg
    assert r["label"] == "steady"


# ── held_weights ─────────────────────────────────────────────────────────────

def _book(rows):
    return pd.DataFrame(rows, columns=["Ticker", "Sector", "Market Value"])


def test_weights_none_when_the_book_is_not_loaded():
    assert mt.held_weights(None) is None
    assert mt.held_weights(pd.DataFrame()) is None
    assert mt.held_weights(pd.DataFrame({"Ticker": ["A"]})) is None  # no columns to read


def test_weights_sum_igv_across_its_three_sectors():
    w = mt.held_weights(_book([
        ("CRM", "AI & Cloud", 100.0),
        ("PLTR", "AI & Data", 200.0),
        ("NOW", "Enterprise Tech", 300.0),
        ("NVDA", "Semiconductors", 400.0),
    ]))
    assert w["IGV"]["pct"] == pytest.approx(60.0)
    assert w["IGV"]["tickers"] == ["CRM", "PLTR", "NOW"]
    assert w["IGV"]["sectors"] == ["AI & Cloud", "AI & Data", "Enterprise Tech"]
    assert w["SOXX"]["pct"] == pytest.approx(40.0)
    assert sum(v["pct"] for v in w.values()) == pytest.approx(100.0)


def test_weights_other_and_unmapped_sectors_go_to_no_fund():
    w = mt.held_weights(_book([
        ("AAA", "Other", 100.0),
        ("BBB", "Technology", 100.0),     # not a SECTOR_ETF key
        ("CCC", None, 100.0),             # no sector at all
        ("SPY", "Other", 100.0),
        ("XOM", "Energy", 600.0),
    ]))
    assert w["_no_fund"]["pct"] == pytest.approx(40.0)
    assert w["_no_fund"]["tickers"] == ["AAA", "BBB", "CCC", "SPY"]
    assert w["XLE"]["pct"] == pytest.approx(60.0)
    assert "SPY" not in w


def test_weights_skip_nan_and_non_positive_values():
    w = mt.held_weights(_book([
        ("NVDA", "Semiconductors", 100.0),
        ("BAD", "Energy", float("nan")),
        ("ZERO", "Energy", 0.0),
        ("NEG", "Energy", -50.0),
        ("TXT", "Energy", "n/a"),
    ]))
    assert set(w) == {"SOXX"}
    assert w["SOXX"]["pct"] == pytest.approx(100.0)


def test_weights_none_when_no_row_has_a_positive_value():
    assert mt.held_weights(_book([("A", "Energy", float("nan")), ("B", "Energy", 0.0)])) is None
    assert mt.held_weights(_book([("A", "Energy", -5.0)])) is None


# ── chip_groups / table_rows / tile_line ─────────────────────────────────────

def _mk(symbol, label, ret=0.0, name=None, **kw):
    row = {k: None for k in mt._ROW_KEYS}
    row.update({"symbol": symbol, "name": name or symbol, "app_sectors": [symbol],
                "label": label, "ret_4w": ret, "ma50_run_capped": False,
                "reason": None, "confirm_k": 0})
    row.update(kw)
    return row


def _result(sectors, indices=None):
    idx = indices or [
        _mk("^GSPC", "steady", name="S&P 500", app_sectors=[]),
        _mk("^IXIC", "steady", name="Nasdaq", app_sectors=[]),
    ]
    return {"as_of": AS_OF, "indices": idx, "sectors": sectors, "not_checked": []}


def _w(**pcts):
    return {s: {"pct": p, "tickers": [], "sectors": []} for s, p in pcts.items()}


def test_chip_groups_exclude_steady_and_not_checked_and_sort_held_first():
    res = _result([
        _mk("F1", "falling", -8.0),
        _mk("F2", "falling", -5.0),
        _mk("F3", "falling", -9.0),
        _mk("W1", "weakening", -1.0),
        _mk("R1", "rising", 6.0),
        _mk("S1", "steady", 0.0),
        _mk("N1", None, None),
    ])
    g = mt.chip_groups(res, _w(F2=12.0, F3=3.0, R1=0.0))
    assert [c["symbol"] for c in g["falling"]] == ["F2", "F3", "F1"]  # held desc, then unheld
    assert [c["symbol"] for c in g["weakening"]] == ["W1"]
    assert [c["symbol"] for c in g["rising"]] == ["R1"]
    assert g["falling"][0] == {"name": "F2", "symbol": "F2", "ret_4w": -5.0, "held_pct": 12.0}
    assert g["falling"][2]["held_pct"] is None
    assert g["rising"][0]["held_pct"] == 0.0  # held at 0 is listed but sorts last


def test_chip_groups_unheld_sort_by_weakest_return_and_none_weights():
    res = _result([_mk("A", "falling", -3.0), _mk("B", "falling", -7.0)])
    g = mt.chip_groups(res, None)
    assert [c["symbol"] for c in g["falling"]] == ["B", "A"]
    assert all(c["held_pct"] is None for c in g["falling"])


def test_table_rows_order_and_label_text():
    res = _result([
        _mk("R1", "rising", 6.0),
        _mk("S1", "steady", 0.0),
        _mk("N1", None, None),
        _mk("W1", "weakening", -1.0),
        _mk("F1", "falling", -8.0),
        _mk("F2", "falling", -5.0),
        _mk("F3", "falling", -9.0),
    ])
    rows = mt.table_rows(res, _w(F2=12.0))
    assert [r["symbol"] for r in rows] == [
        "^GSPC", "^IXIC", "F2", "F3", "F1", "W1", "S1", "R1", "N1"]
    by = {r["symbol"]: r for r in rows}
    assert by["F1"]["label_text"] == "Falling"
    assert by["W1"]["label_text"] == "Weakening"
    assert by["S1"]["label_text"] == "Steady"
    assert by["R1"]["label_text"] == "Rising"
    assert by["N1"]["label_text"] == "Not checked"
    assert by["F2"]["held"] is True and by["F1"]["held"] is False


@pytest.mark.parametrize("weights,sym,text", [
    (None, "F1", "open 🏠 Home first"),
    ({}, "F1", "–"),
    (_w(F1=14.6), "F1", "15%"),
    (_w(F1=0.3), "F1", "<1%"),
    (_w(F1=0.5), "F1", "1%"),
    (_w(F1=0.49), "F1", "<1%"),
    (_w(OTHER=20.0), "F1", "–"),
    (_w(F1=0.0), "F1", "–"),
])
def test_weight_text_for_sector_rows(weights, sym, text):
    rows = mt.table_rows(_result([_mk("F1", "falling", -5.0)]), weights)
    assert {r["symbol"]: r for r in rows}[sym]["weight_text"] == text


def test_weight_text_on_the_index_rows():
    res = _result([_mk("F1", "falling", -5.0)])
    rows = {r["symbol"]: r for r in mt.table_rows(res, _w(_no_fund=18.4))}
    assert rows["^GSPC"]["weight_text"] == "18% no sector fund"
    assert rows["^IXIC"]["weight_text"] == ""
    assert {r["symbol"]: r for r in mt.table_rows(res, _w(F1=5.0))}["^GSPC"]["weight_text"] == ""
    assert {r["symbol"]: r for r in mt.table_rows(res, None)}["^GSPC"]["weight_text"] == ""
    small = {r["symbol"]: r for r in mt.table_rows(res, _w(_no_fund=0.2))}
    assert small["^GSPC"]["weight_text"] == "<1% no sector fund"
    # the no-fund bucket is not a fund row
    assert "_no_fund" not in [r["symbol"] for r in mt.table_rows(res, _w(_no_fund=18.4))]


def test_integration_table_rows_cover_every_fund_once_with_real_weights():
    cols = {
        "SOXX": _tail([95.0] * 3),                 # falling
        "XLE": 100.0 * 1.002 ** np.arange(N),      # not trailing a flat S&P, rising
    }
    res = _run(cols)
    book = _book([("NVDA", "Semiconductors", 300.0), ("CRM", "AI & Cloud", 100.0),
                  ("XYZ", "Other", 100.0)])
    w = mt.held_weights(book)
    rows = mt.table_rows(res, w)
    funds = [r["symbol"] for r in rows if r["symbol"] not in ("^GSPC", "^IXIC")]
    assert len(funds) == len(set(funds)) == len({f for f in SECTOR_ETF.values() if f != "SPY"})
    assert funds[0] == "SOXX"  # Falling and held sorts first
    by = {r["symbol"]: r for r in rows}
    assert by["SOXX"]["label"] == "falling" and by["SOXX"]["weight_text"] == "60%"
    assert by["IGV"]["weight_text"] == "20%"
    assert by["XLE"]["label"] == "rising"
    assert by["^GSPC"]["weight_text"] == "20% no sector fund"
    assert funds[-1] == "XLE" or by[funds[-1]]["label"] == "rising"


def test_tile_line_strings():
    weakening = _mk("^GSPC", "weakening", from_high_pct=-7.8, ma50_side="below", ma200_side="above")
    assert mt.tile_line(weakening) == "Weakening · −7.8% from high · below 50-day · above 200-day"
    falling = _mk("^IXIC", "falling", from_high_pct=-13.6, drawdown_word="correction",
                  ma50_side="below", ma200_side="below")
    assert mt.tile_line(falling) == (
        "Falling · −13.6% from high · correction · below 50-day · below 200-day")
    rising = _mk("^GSPC", "rising", from_high_pct=0.0, ma50_side="above", ma200_side="above")
    assert mt.tile_line(rising) == "Rising · 0.0% from high · above 50-day · above 200-day"
    assert mt.tile_line(_mk("^GSPC", None)) is None
    na = _mk("^GSPC", "steady", from_high_pct=None, ma50_side="at", ma200_side="at")
    assert mt.tile_line(na) == "Steady · from high n/a · at 50-day · at 200-day"


def test_tile_line_from_a_real_read():
    res = _run({"^GSPC": _spx_last(80.05)})
    line = mt.tile_line(res["indices"][0])
    assert line.startswith("Weakening · −20.0% from high · bear market")  # one session below both: day 1
    assert "below 50-day · below 200-day" in line
