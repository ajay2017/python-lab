"""Regression tests for stock_analyzer/perf_advisor.py — per-position
performance attribution (holding return vs SPY vs sector ETF) and the
prioritised Alpha Generator / Sector Rider / Alpha Destroyer recommendation
ladder. Pure computation over DataFrames (no I/O). See
docs/plans/test-automation.md for scope.
"""
import pandas as pd

from stock_analyzer import perf_advisor as pa
from stock_analyzer.constants import COMPOSITE_HOLD
import pytest

pytestmark = pytest.mark.fast


# ── _f ───────────────────────────────────────────────────────────────────

def test_f_none_returns_default():
    assert pa._f(None) == 0.0
    assert pa._f(None, default=5) == 5


def test_f_nan_returns_default():
    assert pa._f(float("nan"), default=-1) == -1


def test_f_unparseable_returns_default():
    assert pa._f("bad", default=2) == 2


def test_f_parses_valid_value():
    assert pa._f("3.5") == 3.5


# ── _opt ─────────────────────────────────────────────────────────────────

def test_opt_none_returns_none():
    assert pa._opt(None) is None


def test_opt_nan_returns_none():
    assert pa._opt(float("nan")) is None


def test_opt_unparseable_returns_none():
    assert pa._opt("bad") is None


def test_opt_parses_valid_value():
    assert pa._opt("2.5") == 2.5


def test_opt_zero_is_preserved_not_none():
    # _opt is None-preserving but must not treat a legitimate 0.0 as missing.
    assert pa._opt(0.0) == 0.0


# ── compute_attribution ────────────────────────────────────────────────────

def _closes(prices):
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="D")
    return pd.DataFrame({"Close": prices}, index=idx)


def _port_row(ticker, weight, mval, sector="Technology", score=70.0, signal="BUY"):
    return {
        "Ticker": ticker, "Weight (%)": weight, "Market Value": mval,
        "Sector": sector, "Score": score, "Signal": signal,
    }


def test_compute_attribution_empty_port_df_returns_empty():
    result = pa.compute_attribution(pd.DataFrame(), {}, _closes([100, 101]), 5)
    assert result.empty


def test_compute_attribution_empty_spy_df_returns_empty():
    port_df = pd.DataFrame([_port_row("AAA", 10, 1000)])
    result = pa.compute_attribution(port_df, {}, pd.DataFrame(), 5)
    assert result.empty


def test_compute_attribution_none_spy_df_returns_empty():
    port_df = pd.DataFrame([_port_row("AAA", 10, 1000)])
    result = pa.compute_attribution(port_df, {}, None, 5)
    assert result.empty


def test_compute_attribution_insufficient_spy_history_returns_empty():
    port_df = pd.DataFrame([_port_row("AAA", 10, 1000)])
    spy_df = _closes([100, 101])  # only 1 usable interval, n_days huge but len-1 clamps
    result = pa.compute_attribution(port_df, {}, spy_df, 1)
    # n = min(1, 1) = 1 < 2 -> empty
    assert result.empty


def test_compute_attribution_missing_weight_skips_position():
    port_df = pd.DataFrame([{
        "Ticker": "AAA", "Weight (%)": None, "Market Value": 1000,
        "Sector": "Technology", "Score": 70.0, "Signal": "BUY",
    }])
    held_data = {"AAA": {"df": _closes([100] * 10)}}
    spy_df = _closes([100] * 10)
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)
    assert result.empty


def test_compute_attribution_missing_market_value_skips_position():
    port_df = pd.DataFrame([{
        "Ticker": "AAA", "Weight (%)": 10, "Market Value": None,
        "Sector": "Technology", "Score": 70.0, "Signal": "BUY",
    }])
    held_data = {"AAA": {"df": _closes([100] * 10)}}
    spy_df = _closes([100] * 10)
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)
    assert result.empty


def test_compute_attribution_zero_or_negative_market_value_skips_position():
    port_df = pd.DataFrame([_port_row("AAA", 10, 0)])
    held_data = {"AAA": {"df": _closes([100] * 10)}}
    spy_df = _closes([100] * 10)
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)
    assert result.empty


def test_compute_attribution_missing_held_data_skips_position():
    port_df = pd.DataFrame([_port_row("AAA", 10, 1000)])
    spy_df = _closes([100] * 10)
    result = pa.compute_attribution(port_df, {}, spy_df, 5)
    assert result.empty


def test_compute_attribution_empty_holding_history_skips_position():
    port_df = pd.DataFrame([_port_row("AAA", 10, 1000)])
    held_data = {"AAA": {"df": pd.DataFrame()}}
    spy_df = _closes([100] * 10)
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)
    assert result.empty


def test_compute_attribution_missing_close_column_skips_position():
    port_df = pd.DataFrame([_port_row("AAA", 10, 1000)])
    held_data = {"AAA": {"df": pd.DataFrame({"Open": [1, 2, 3]})}}
    spy_df = _closes([100] * 10)
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)
    assert result.empty


def test_compute_attribution_insufficient_holding_history_skips_position():
    port_df = pd.DataFrame([_port_row("AAA", 10, 1000)])
    held_data = {"AAA": {"df": _closes([100])}}  # only 1 row -> nh<2
    spy_df = _closes([100] * 10)
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)
    assert result.empty


def test_compute_attribution_no_valid_rows_returns_empty():
    # All positions skipped -> rows stays [] -> early return before sort.
    port_df = pd.DataFrame([_port_row("AAA", 10, 1000)])
    spy_df = _closes([100] * 10)
    result = pa.compute_attribution(port_df, {}, spy_df, 5)
    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_compute_attribution_basic_alpha_calc():
    # SPY flat at 100 across the window -> spy_ret = 0%.
    spy_df = _closes([100] * 6)
    # AAA rises from 100 to 110 over same window -> holding_ret = 10%.
    held_data = {"AAA": {"df": _closes([100, 100, 100, 100, 100, 110])}}
    port_df = pd.DataFrame([_port_row("AAA", 20, 5000)])
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)
    assert len(result) == 1
    row = result.iloc[0]
    assert row["Ticker"] == "AAA"
    assert row["SPY Ret (%)"] == 0.0
    assert row["Holding Ret (%)"] == 10.0
    assert row["Alpha vs SPY (%)"] == 10.0
    # Dollar alpha = alpha_spy/100 * mval = 0.10 * 5000 = 500
    assert row["Dollar Alpha ($)"] == 500.0


def test_compute_attribution_category_alpha_generator_requires_sector_beat():
    spy_df = _closes([100] * 6)
    held_data = {"AAA": {"df": _closes([100, 100, 100, 100, 100, 110])}}  # +10%
    # "Consumer Tech" -> XLY per stock_analyzer.portfolio.SECTOR_ETF.
    port_df = pd.DataFrame([_port_row("AAA", 20, 5000, sector="Consumer Tech")])
    sector_etf_rets = {"XLY": {"3M": 2.0}}  # sector +2%, alpha_sec = 10-2=8 >= 3
    result = pa.compute_attribution(port_df, held_data, spy_df, 5, sector_etf_rets, "3M")
    assert result.iloc[0]["Category"] == "Alpha Generator"


def test_compute_attribution_category_sector_rider_when_alpha_spy_high_but_sector_close():
    spy_df = _closes([100] * 6)
    held_data = {"AAA": {"df": _closes([100, 100, 100, 100, 100, 110])}}  # +10% vs SPY
    port_df = pd.DataFrame([_port_row("AAA", 20, 5000, sector="Consumer Tech")])
    sector_etf_rets = {"XLY": {"3M": 9.0}}  # alpha_sec = 10-9=1 < 3 -> sector rider
    result = pa.compute_attribution(port_df, held_data, spy_df, 5, sector_etf_rets, "3M")
    assert result.iloc[0]["Category"] == "Sector Rider"


def test_compute_attribution_category_sector_rider_when_no_sector_data():
    spy_df = _closes([100] * 6)
    held_data = {"AAA": {"df": _closes([100, 100, 100, 100, 100, 110])}}
    port_df = pd.DataFrame([_port_row("AAA", 20, 5000)])
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)  # no sector_etf_rets
    assert result.iloc[0]["Category"] == "Sector Rider"
    assert result.iloc[0]["Alpha vs Sector (%)"] is None


def test_compute_attribution_category_alpha_destroyer():
    spy_df = _closes([100] * 6)
    held_data = {"AAA": {"df": _closes([100, 100, 100, 100, 100, 90])}}  # -10%
    port_df = pd.DataFrame([_port_row("AAA", 20, 5000)])
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)
    assert result.iloc[0]["Category"] == "Alpha Destroyer"


def test_compute_attribution_category_in_line():
    spy_df = _closes([100] * 6)
    held_data = {"AAA": {"df": _closes([100, 100, 100, 100, 100, 102])}}  # +2%, alpha=2
    port_df = pd.DataFrame([_port_row("AAA", 20, 5000)])
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)
    assert result.iloc[0]["Category"] == "In Line"


def test_compute_attribution_sorted_by_dollar_alpha_descending():
    spy_df = _closes([100] * 6)
    held_data = {
        "AAA": {"df": _closes([100, 100, 100, 100, 100, 105])},  # +5%
        "BBB": {"df": _closes([100, 100, 100, 100, 100, 120])},  # +20%
    }
    port_df = pd.DataFrame([
        _port_row("AAA", 10, 5000),
        _port_row("BBB", 10, 5000),
    ])
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)
    assert list(result["Ticker"]) == ["BBB", "AAA"]


def test_compute_attribution_sector_etf_lookup_defaults_to_spy_for_unknown_sector():
    spy_df = _closes([100] * 6)
    held_data = {"AAA": {"df": _closes([100, 100, 100, 100, 100, 110])}}
    port_df = pd.DataFrame([_port_row("AAA", 20, 5000, sector="Unmapped Made-Up Sector")])
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)
    assert result.iloc[0]["ETF"] == "SPY"


def test_compute_attribution_tz_aware_index_is_localized():
    idx = pd.date_range("2026-01-01", periods=6, freq="D", tz="UTC")
    spy_df = pd.DataFrame({"Close": [100] * 6}, index=idx)
    held_df = pd.DataFrame({"Close": [100, 100, 100, 100, 100, 110]}, index=idx)
    held_data = {"AAA": {"df": held_df}}
    port_df = pd.DataFrame([_port_row("AAA", 20, 5000)])
    result = pa.compute_attribution(port_df, held_data, spy_df, 5)
    assert len(result) == 1
    assert result.iloc[0]["Holding Ret (%)"] == 10.0


# ── build_perf_recommendations ─────────────────────────────────────────────

def _attr_row(ticker="AAA", category="Alpha Generator", h_ret=10.0, spy_ret=0.0,
              alpha_spy=10.0, alpha_sec=8.0, sect_ret=2.0, dollar_alpha=500.0,
              mval=5000.0, score=70.0, signal="BUY", sector="Technology", etf="XLK"):
    return {
        "Ticker": ticker, "Category": category,
        "Holding Ret (%)": h_ret, "SPY Ret (%)": spy_ret,
        "Alpha vs SPY (%)": alpha_spy, "Alpha vs Sector (%)": alpha_sec,
        "Sector Ret (%)": sect_ret, "Dollar Alpha ($)": dollar_alpha,
        "Market Value": mval, "Score": score, "Signal": signal,
        "Sector": sector, "ETF": etf,
    }


def test_build_perf_recommendations_empty_attr_df_returns_empty():
    assert pa.build_perf_recommendations(pd.DataFrame(), 100000) == []


def test_build_perf_recommendations_none_portfolio_value_returns_empty():
    attr_df = pd.DataFrame([_attr_row()])
    assert pa.build_perf_recommendations(attr_df, None) == []


def test_build_perf_recommendations_zero_portfolio_value_returns_empty():
    attr_df = pd.DataFrame([_attr_row()])
    assert pa.build_perf_recommendations(attr_df, 0) == []


def test_build_perf_recommendations_negative_portfolio_value_returns_empty():
    attr_df = pd.DataFrame([_attr_row()])
    assert pa.build_perf_recommendations(attr_df, -1000) == []


def test_build_perf_recommendations_alpha_generator_priority_ok():
    attr_df = pd.DataFrame([_attr_row(category="Alpha Generator")])
    recs = pa.build_perf_recommendations(attr_df, 100000)
    assert len(recs) == 1
    assert recs[0]["priority"] == "OK"
    assert recs[0]["type"] == "alpha_generator"
    assert "AAA" in recs[0]["title"]


def test_build_perf_recommendations_sector_rider_priority_monitor():
    attr_df = pd.DataFrame([_attr_row(category="Sector Rider", alpha_sec=1.0)])
    recs = pa.build_perf_recommendations(attr_df, 100000)
    assert recs[0]["priority"] == "MONITOR"
    assert recs[0]["type"] == "sector_rider"


def test_build_perf_recommendations_sector_rider_title_omits_sector_when_alpha_sec_none():
    row = _attr_row(category="Sector Rider")
    row["Alpha vs Sector (%)"] = None
    attr_df = pd.DataFrame([row])
    recs = pa.build_perf_recommendations(attr_df, 100000)
    assert "Sector Tailwind" in recs[0]["title"]


def test_build_perf_recommendations_alpha_destroyer_high_priority_below_neg15():
    attr_df = pd.DataFrame([_attr_row(category="Alpha Destroyer", alpha_spy=-16.0, alpha_sec=-14.0)])
    recs = pa.build_perf_recommendations(attr_df, 100000)
    assert recs[0]["priority"] == "HIGH"


def test_build_perf_recommendations_alpha_destroyer_medium_priority_above_neg15():
    attr_df = pd.DataFrame([_attr_row(category="Alpha Destroyer", alpha_spy=-10.0, alpha_sec=-8.0)])
    recs = pa.build_perf_recommendations(attr_df, 100000)
    assert recs[0]["priority"] == "MEDIUM"


def test_build_perf_recommendations_alpha_destroyer_thesis_solid_when_score_high():
    attr_df = pd.DataFrame([_attr_row(category="Alpha Destroyer", alpha_spy=-10.0, score=65.0)])
    recs = pa.build_perf_recommendations(attr_df, 100000)
    assert "fundamentals still solid" in recs[0]["root_cause"]
    assert "30-day review" in recs[0]["recommendation"]


def test_build_perf_recommendations_alpha_destroyer_thesis_borderline_at_hold_floor():
    attr_df = pd.DataFrame([_attr_row(category="Alpha Destroyer", alpha_spy=-10.0, score=float(COMPOSITE_HOLD))])
    recs = pa.build_perf_recommendations(attr_df, 100000)
    assert "borderline" in recs[0]["root_cause"]
    assert "Trim 40" in recs[0]["recommendation"]


def test_build_perf_recommendations_alpha_destroyer_thesis_broken_below_hold_floor():
    attr_df = pd.DataFrame([_attr_row(category="Alpha Destroyer", alpha_spy=-10.0, score=float(COMPOSITE_HOLD) - 1)])
    recs = pa.build_perf_recommendations(attr_df, 100000)
    assert "broken thesis" in recs[0]["root_cause"]
    assert "Exit or reduce" in recs[0]["recommendation"]


def test_build_perf_recommendations_in_line_produces_no_rec():
    attr_df = pd.DataFrame([_attr_row(category="In Line")])
    recs = pa.build_perf_recommendations(attr_df, 100000)
    assert recs == []


def test_build_perf_recommendations_sort_order_high_medium_monitor_ok():
    attr_df = pd.DataFrame([
        _attr_row(ticker="OK1", category="Alpha Generator"),
        _attr_row(ticker="MON1", category="Sector Rider", alpha_sec=1.0),
        _attr_row(ticker="MED1", category="Alpha Destroyer", alpha_spy=-10.0, score=65.0),
        _attr_row(ticker="HIGH1", category="Alpha Destroyer", alpha_spy=-20.0, score=65.0),
    ])
    recs = pa.build_perf_recommendations(attr_df, 100000)
    assert [r["ticker"] for r in recs] == ["HIGH1", "MED1", "MON1", "OK1"]


def test_build_perf_recommendations_metrics_include_dollar_impact():
    attr_df = pd.DataFrame([_attr_row(category="Alpha Generator", dollar_alpha=750.0)])
    recs = pa.build_perf_recommendations(attr_df, 100000)
    metrics = recs[0]["metrics"]
    assert any("750" in v for v in metrics.values())


def test_build_perf_recommendations_alpha_destroyer_opportunity_cost_metric():
    attr_df = pd.DataFrame([_attr_row(category="Alpha Destroyer", alpha_spy=-10.0, mval=5000.0)])
    recs = pa.build_perf_recommendations(attr_df, 100000)
    assert recs[0]["metrics"]["Opportunity Cost"] == "-$500"


# ── PerfWindowCollapse: date-aligned window, left_out, band constant ───────
# Window rule mirrors perf_backtest.backtest_vs_spy: SPY's own last n sessions,
# a holding must start within _START_SLACK_SESSIONS of the window start.

from stock_analyzer.constants import PERF_ALPHA_BAND_PCT  # noqa: E402


def _bidx(n):
    return pd.bdate_range("2026-01-01", periods=n)


def _spy70():
    idx = _bidx(70)
    return pd.DataFrame({"Close": [100.0 + i for i in range(70)]}, index=idx), idx


def _hold(idx, closes):
    return {"df": pd.DataFrame({"Close": closes}, index=idx)}


def test_two_session_holding_on_63_window_is_left_out_not_dropped_silently():
    spy_df, idx = _spy70()
    held = {
        "ANT": _hold(idx[-2:], [10.0, 11.0]),
        "FULL": _hold(idx, [50.0 + i for i in range(70)]),
    }
    port = pd.DataFrame([_port_row("ANT", 5, 500), _port_row("FULL", 20, 5000)])
    left = []
    res = pa.compute_attribution(port, held, spy_df, 63, left_out=left)
    assert list(res["Ticker"]) == ["FULL"]
    assert left == [{"ticker": "ANT", "reason": "short_history", "sessions": 2}]


def test_holding_starting_within_slack_is_measured_from_its_first_date():
    spy_df, idx = _spy70()
    # p0 = 70 - 64 = 6; the holding starts 3 sessions into the window (idx[9]).
    closes = [50.0] + [60.0] * 60
    held = {"NEW": _hold(idx[9:], closes)}
    port = pd.DataFrame([_port_row("NEW", 10, 1000)])
    left = []
    res = pa.compute_attribution(port, held, spy_df, 63, left_out=left)
    assert left == []
    row = res.iloc[0]
    spy_base = 100.0 + 9  # SPY close on the holding's first date
    assert row["SPY Ret (%)"] == round((169.0 / spy_base - 1) * 100, 1)
    assert row["Holding Ret (%)"] == round((60.0 / 50.0 - 1) * 100, 1)


def test_holding_starting_ten_sessions_in_is_short_history():
    spy_df, idx = _spy70()
    held = {"LATE": _hold(idx[16:], [10.0 + i for i in range(54)])}
    port = pd.DataFrame([_port_row("LATE", 10, 1000)])
    left = []
    res = pa.compute_attribution(port, held, spy_df, 63, left_out=left)
    assert res.empty
    assert left == [{"ticker": "LATE", "reason": "short_history", "sessions": 54}]


def test_full_history_holding_uses_the_same_start_date_as_spy():
    spy_df, idx = _spy70()
    keep = [i for i in range(70) if i != 30]  # a missing day inside the window
    held = {"FULL": _hold(idx[keep], [10.0 * (i + 1) for i in keep])}
    port = pd.DataFrame([_port_row("FULL", 10, 1000)])
    res = pa.compute_attribution(port, held, spy_df, 63)
    row = res.iloc[0]
    # window start = idx[6]: holding 70.0 -> 700.0, SPY 106.0 -> 169.0.
    # (A positional read from the holding's own 63rd-last row would differ.)
    assert row["Holding Ret (%)"] == 900.0
    assert row["SPY Ret (%)"] == round((169.0 / 106.0 - 1) * 100, 1)


def test_left_out_reasons_for_missing_value_and_missing_history():
    spy_df, idx = _spy70()
    port = pd.DataFrame([
        {"Ticker": "NOVAL", "Weight (%)": 10, "Market Value": None,
         "Sector": "Technology", "Score": 70.0, "Signal": "BUY"},
        _port_row("NOBUNDLE", 10, 1000),
        _port_row("EMPTY", 10, 1000),
        _port_row("OK", 10, 1000),
    ])
    held = {
        "NOVAL": _hold(idx, [10.0 + i for i in range(70)]),
        "EMPTY": {"df": pd.DataFrame()},
        "OK": _hold(idx, [10.0 + i for i in range(70)]),
    }
    left = []
    res = pa.compute_attribution(port, held, spy_df, 63, left_out=left)
    assert list(res["Ticker"]) == ["OK"]
    by = {d["ticker"]: d for d in left}
    assert by["NOVAL"] == {"ticker": "NOVAL", "reason": "no_value", "sessions": 70}
    assert by["NOBUNDLE"] == {"ticker": "NOBUNDLE", "reason": "no_history", "sessions": 0}
    assert by["EMPTY"]["reason"] == "no_history" and by["EMPTY"]["sessions"] == 0


def test_left_out_default_none_still_works():
    spy_df, idx = _spy70()
    port = pd.DataFrame([_port_row("LATE", 10, 1000)])
    held = {"LATE": _hold(idx[-2:], [10.0, 11.0])}
    assert pa.compute_attribution(port, held, spy_df, 63).empty


def test_non_positive_base_price_is_no_history():
    spy_df, idx = _spy70()
    port = pd.DataFrame([_port_row("ZERO", 10, 1000)])
    held = {"ZERO": _hold(idx, [5.0] * 6 + [0.0] + [5.0] * 63)}  # 0.0 on the window start
    left = []
    res = pa.compute_attribution(port, held, spy_df, 63, left_out=left)
    assert res.empty
    assert left[0]["reason"] == "no_history"


def _band_row(alpha_pct, sector_ret):
    spy_df = _closes([100] * 6)
    held = {"AAA": {"df": _closes([100, 100, 100, 100, 100, 100 * (1 + alpha_pct / 100)])}}
    port = pd.DataFrame([_port_row("AAA", 20, 5000, sector="Consumer Tech")])
    rets = {"XLY": {"3M": sector_ret}} if sector_ret is not None else None
    return pa.compute_attribution(port, held, spy_df, 5, rets, "3M").iloc[0]


def test_alpha_exactly_at_band_with_sector_beat_is_alpha_generator():
    assert _band_row(PERF_ALPHA_BAND_PCT, 1.0)["Category"] == "Alpha Generator"


def test_alpha_exactly_at_band_with_sector_close_is_sector_rider():
    assert _band_row(PERF_ALPHA_BAND_PCT, PERF_ALPHA_BAND_PCT - 1.0)["Category"] == "Sector Rider"


def test_alpha_at_band_with_sector_missing_is_sector_rider():
    assert _band_row(PERF_ALPHA_BAND_PCT, None)["Category"] == "Sector Rider"


def test_alpha_exactly_minus_band_is_alpha_destroyer():
    assert _band_row(-PERF_ALPHA_BAND_PCT, None)["Category"] == "Alpha Destroyer"


# ── left_out_note ──────────────────────────────────────────────────────────

def test_left_out_note_empty_is_none():
    assert pa.left_out_note([], "3M") is None


def test_left_out_note_wording_per_reason():
    note = pa.left_out_note([
        {"ticker": "ANT", "reason": "short_history", "sessions": 2},
        {"ticker": "ONE", "reason": "short_history", "sessions": 1},
        {"ticker": "XYZ", "reason": "no_value", "sessions": 5},
        {"ticker": "NOH", "reason": "no_history", "sessions": 0},
    ], "3M")
    assert note == (
        "Left out: ANT (2 sessions of price history, too short for the 3M window); "
        "ONE (1 session of price history, too short for the 3M window); "
        "XYZ (no market value); NOH (no price history)."
    )
    assert "$" not in note


def test_left_out_note_dedups_tickers_in_input_order():
    note = pa.left_out_note([
        {"ticker": "B", "reason": "no_value", "sessions": 0},
        {"ticker": "A", "reason": "no_history", "sessions": 0},
        {"ticker": "B", "reason": "no_history", "sessions": 0},
    ], "1M")
    assert note.startswith("Left out: B (no market value); A (no price history).")
    assert note.count("B (") == 1


def test_left_out_note_stale_history_wording_uses_the_date():
    from datetime import date
    note = pa.left_out_note([
        {"ticker": "OLD", "reason": "stale_history", "sessions": 40,
         "last_date": date(2026, 3, 4)},
    ], "3M")
    assert note == "Left out: OLD (price history ends on Mar 4 (stale))."


# ── stale history ──────────────────────────────────────────────────────────

def test_holding_whose_history_ends_well_before_spy_is_stale_history():
    spy_df, idx = _spy70()
    held = {"OLD": _hold(idx[:64], [10.0 + i for i in range(64)])}  # 6 SPY dates after
    port = pd.DataFrame([_port_row("OLD", 10, 1000)])
    left = []
    res = pa.compute_attribution(port, held, spy_df, 63, left_out=left)
    assert res.empty
    assert left == [{"ticker": "OLD", "reason": "stale_history", "sessions": 64,
                     "last_date": idx[63].date()}]


def test_holding_ending_within_the_slack_of_spy_is_kept():
    spy_df, idx = _spy70()
    held = {"OK": _hold(idx[:65], [10.0 + i for i in range(65)])}  # 5 SPY dates after
    port = pd.DataFrame([_port_row("OK", 10, 1000)])
    left = []
    res = pa.compute_attribution(port, held, spy_df, 63, left_out=left)
    assert list(res["Ticker"]) == ["OK"]
    assert left == []


# ── cover cut agrees with backtest_vs_spy ──────────────────────────────────

def test_holding_starting_on_the_cover_cut_is_in_both_backtest_and_attribution():
    from stock_analyzer.perf_backtest import backtest_vs_spy
    spy_df, idx = _spy70()
    spy_close = spy_df["Close"]
    port = pd.DataFrame([_port_row("H", 10, 1000)])
    # backtest cut for n=63 over 70 sessions = idx[len - n + 5] = idx[12]
    on_cut = idx[12:]
    bt = backtest_vs_spy({"H": pd.Series([10.0 + i for i in range(len(on_cut))], index=on_cut)},
                         {"H": 1.0}, spy_close, 63)
    assert bt["ok"] and bt["included"] == ["H"]
    held = {"H": _hold(on_cut, [10.0 + i for i in range(len(on_cut))])}
    assert len(pa.compute_attribution(port, held, spy_df, 63)) == 1


def test_holding_starting_after_the_cover_cut_is_out_of_both():
    from stock_analyzer.perf_backtest import backtest_vs_spy
    spy_df, idx = _spy70()
    port = pd.DataFrame([_port_row("H", 10, 1000)])
    late = idx[13:]
    vals = [10.0 + i for i in range(len(late))]
    bt = backtest_vs_spy({"H": pd.Series(vals, index=late)}, {"H": 1.0}, spy_df["Close"], 63)
    assert not bt["ok"] and [r["ticker"] for r in bt["left_out"]] == ["H"]
    left = []
    res = pa.compute_attribution(port, {"H": _hold(late, vals)}, spy_df, 63, left_out=left)
    assert res.empty and left[0]["reason"] == "short_history"
