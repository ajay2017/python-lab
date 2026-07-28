"""Regression tests for stock_analyzer/perf_advisor.py — per-position
performance attribution (holding return vs SPY vs sector ETF) and the
prioritised Alpha Generator / Sector Rider / Alpha Destroyer recommendation
ladder. Pure computation over DataFrames (no I/O). See
docs/plans/test-automation.md for scope.
"""
import pandas as pd

from stock_analyzer import perf_advisor as pa
from stock_analyzer.constants import COMPOSITE_HOLD


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
