"""
Tests for stock_analyzer/stress_test.py — Portfolio Stress Testing & Scenario
Analysis: safe-float coercion helpers, the per-position/per-scenario shock
estimator (sector-shock lookup, sector-targeted-but-unmapped zeroing per
review H-12, and the beta*SPY fallback), the all-scenarios runner, a
yfinance-backed historical-drawdown fetch, and the fragility gauge derived
from an already-run mild-correction scenario. Zero coverage before this
batch. `fetch_historical_drawdowns` locally imports `yfinance` inside the
function — the real installed module is monkeypatched directly (its local
import resolves to the same cached module object).
"""
from datetime import date, timedelta

import pandas as pd
import pytest
import yfinance

from stock_analyzer import stress_test as st


# ─── _f ────────────────────────────────────────────────────────────────────────

def test_f_none_returns_default():
    assert st._f(None) == 0.0
    assert st._f(None, default=5.0) == 5.0


def test_f_nan_returns_default():
    assert st._f(float("nan"), default=3.0) == 3.0


def test_f_unparseable_string_returns_default():
    assert st._f("not-a-number", default=1.5) == 1.5


def test_f_normal_value_coerced():
    assert st._f(3.14) == 3.14
    assert st._f("42.5") == 42.5


# ─── _opt ──────────────────────────────────────────────────────────────────────

def test_opt_none_returns_none():
    assert st._opt(None) is None


def test_opt_nan_returns_none():
    assert st._opt(float("nan")) is None


def test_opt_normal_value_coerced():
    assert st._opt(3.14) == 3.14
    assert st._opt("42.5") == 42.5


def test_opt_unparseable_returns_none():
    assert st._opt("not-a-number") is None


# ─── run_scenario ──────────────────────────────────────────────────────────────

def _pos_row(ticker, sector="Other", weight=10.0, mval=10000.0):
    return {"Ticker": ticker, "Sector": sector, "Weight (%)": weight, "Market Value": mval}


BROAD_SCENARIO = {"spy_move": -20.0, "sector_key": None}
SECTOR_SCENARIO = {"spy_move": -25.0, "sector_key": "2022 Rate Shock"}
AI_UNWIND_SCENARIO = {"spy_move": -10.0, "sector_key": "AI Trade Unwind"}


def test_run_scenario_zero_portfolio_value_returns_empty_dict():
    port_df = pd.DataFrame([_pos_row("AAA", mval=0.0)])
    result = st.run_scenario(BROAD_SCENARIO, port_df, {}, portfolio_beta=1.0)
    assert result == {}


def test_run_scenario_empty_port_df_returns_empty_dict():
    port_df = pd.DataFrame(columns=["Ticker", "Sector", "Weight (%)", "Market Value"])
    result = st.run_scenario(BROAD_SCENARIO, port_df, {}, portfolio_beta=1.0)
    assert result == {}


def test_run_scenario_missing_market_value_row_excluded_entirely():
    port_df = pd.DataFrame([
        _pos_row("AAA", mval=None),
        _pos_row("BBB", mval=10000.0),
    ])
    result = st.run_scenario(BROAD_SCENARIO, port_df, {}, portfolio_beta=1.0)
    tickers = [r["Ticker"] for r in result["rows"]]
    assert tickers == ["BBB"]


def test_run_scenario_sector_in_map_unscaled():
    port_df = pd.DataFrame([_pos_row("AAA", sector="Healthcare", mval=10000.0)])
    result = st.run_scenario(SECTOR_SCENARIO, port_df, {}, portfolio_beta=1.0)
    row = result["rows"][0]
    assert row["Est. Move (%)"] == -6.0  # real Healthcare value under 2022 Rate Shock


def test_run_scenario_sector_in_map_scaled_by_custom_spy_move():
    port_df = pd.DataFrame([_pos_row("AAA", sector="Healthcare", mval=10000.0)])
    result = st.run_scenario(SECTOR_SCENARIO, port_df, {}, portfolio_beta=1.0, custom_spy_move=-12.5)
    row = result["rows"][0]
    assert row["Est. Move (%)"] == -3.0  # -6.0 * (-12.5 / -25.0) = -3.0


def test_run_scenario_sector_targeted_but_unmapped_sector_zeroed():
    port_df = pd.DataFrame([_pos_row("AAA", sector="Other", mval=10000.0)])
    result = st.run_scenario(AI_UNWIND_SCENARIO, port_df, {}, portfolio_beta=1.5)
    row = result["rows"][0]
    assert row["Est. Move (%)"] == 0.0  # H-12: no re-broadcast of a broad shock


def test_run_scenario_broad_market_beta_from_held_data():
    port_df = pd.DataFrame([_pos_row("AAA", mval=10000.0)])
    held_data = {"AAA": {"risk_metrics": {"beta": 2.0}}}
    result = st.run_scenario(BROAD_SCENARIO, port_df, held_data, portfolio_beta=1.0)
    row = result["rows"][0]
    assert row["Est. Move (%)"] == -40.0  # 2.0 * -20.0


def test_run_scenario_broad_market_beta_nan_falls_back_to_portfolio_beta():
    port_df = pd.DataFrame([_pos_row("AAA", mval=10000.0)])
    held_data = {"AAA": {"risk_metrics": {"beta": float("nan")}}}
    result = st.run_scenario(BROAD_SCENARIO, port_df, held_data, portfolio_beta=1.3)
    row = result["rows"][0]
    assert row["Est. Move (%)"] == pytest.approx(-26.0)  # 1.3 * -20.0


def test_run_scenario_broad_market_ticker_absent_falls_back_to_portfolio_beta():
    port_df = pd.DataFrame([_pos_row("AAA", mval=10000.0)])
    result = st.run_scenario(BROAD_SCENARIO, port_df, {}, portfolio_beta=1.3)
    row = result["rows"][0]
    assert row["Est. Move (%)"] == pytest.approx(-26.0)


def test_run_scenario_broad_market_both_absent_defaults_to_1():
    port_df = pd.DataFrame([_pos_row("AAA", mval=10000.0)])
    result = st.run_scenario(BROAD_SCENARIO, port_df, {}, portfolio_beta=None)
    row = result["rows"][0]
    assert row["Est. Move (%)"] == -20.0  # 1.0 * -20.0


def test_run_scenario_rows_sorted_ascending_by_pnl():
    port_df = pd.DataFrame([
        _pos_row("GAIN", sector="Energy", mval=10000.0),   # 2022 Rate Shock: Energy +55.0
        _pos_row("LOSS", sector="AI & Data", mval=10000.0),  # -60.0
    ])
    result = st.run_scenario(SECTOR_SCENARIO, port_df, {}, portfolio_beta=1.0)
    pnls = [r["Est. P&L ($)"] for r in result["rows"]]
    assert pnls == sorted(pnls)
    assert result["rows"][0]["Ticker"] == "LOSS"


def test_run_scenario_most_exposed_top_3_losers():
    port_df = pd.DataFrame([
        _pos_row("A", sector="AI & Data", mval=10000.0),   # -60.0
        _pos_row("B", sector="EV & Auto", mval=10000.0),   # -65.0
        _pos_row("C", sector="Cybersecurity", mval=10000.0),  # -40.0
        _pos_row("D", sector="Financials", mval=10000.0),  # -15.0
    ])
    result = st.run_scenario(SECTOR_SCENARIO, port_df, {}, portfolio_beta=1.0)
    assert len(result["most_exposed"]) == 3
    assert result["most_exposed"][0]["Ticker"] == "B"  # -65 is the biggest loser


def test_run_scenario_any_gainers_filters_positive_only():
    port_df = pd.DataFrame([
        _pos_row("GAIN", sector="Energy", mval=10000.0),   # +55.0
        _pos_row("LOSS", sector="AI & Data", mval=10000.0),  # -60.0
    ])
    result = st.run_scenario(SECTOR_SCENARIO, port_df, {}, portfolio_beta=1.0)
    assert [r["Ticker"] for r in result["any_gainers"]] == ["GAIN"]


# ─── run_all_scenarios ─────────────────────────────────────────────────────────

def test_run_all_scenarios_empty_port_df_returns_empty_list():
    port_df = pd.DataFrame(columns=["Ticker", "Sector", "Weight (%)", "Market Value"])
    results = st.run_all_scenarios(port_df, {}, portfolio_beta=1.0)
    assert results == []


def test_run_all_scenarios_nonempty_port_df_returns_all_scenarios_merged():
    port_df = pd.DataFrame([_pos_row("AAA", mval=10000.0)])
    results = st.run_all_scenarios(port_df, {}, portfolio_beta=1.0)
    assert len(results) == len(st.SCENARIOS)
    ids = [r["id"] for r in results]
    assert ids == [sc["id"] for sc in st.SCENARIOS]
    assert "rows" in results[0]  # merged scenario metadata + run_scenario result
    assert "label" in results[0]


# ─── fetch_historical_drawdowns ────────────────────────────────────────────────

def test_fetch_historical_drawdowns_unknown_scenario_id_returns_empty_no_yf_call(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("yfinance.download should not be called")
    monkeypatch.setattr(yfinance, "download", _boom)
    assert st.fetch_historical_drawdowns("not_a_real_scenario", ["AAPL"]) == {}


def test_fetch_historical_drawdowns_empty_df_maps_to_none(monkeypatch):
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: pd.DataFrame())
    result = st.fetch_historical_drawdowns("covid_crash", ["AAPL"])
    assert result == {"AAPL": None}


def test_fetch_historical_drawdowns_fewer_than_5_rows_maps_to_none(monkeypatch):
    df = pd.DataFrame({"Close": [100.0, 95.0, 90.0]})
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: df)
    result = st.fetch_historical_drawdowns("covid_crash", ["AAPL"])
    assert result == {"AAPL": None}


def test_fetch_historical_drawdowns_valid_close_series_computed_correctly(monkeypatch):
    df = pd.DataFrame({"Close": [100.0, 90.0, 66.0, 80.0, 95.0]})
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: df)
    result = st.fetch_historical_drawdowns("covid_crash", ["AAPL"])
    expected = round((66.0 - 100.0) / 100.0 * 100, 1)
    assert result == {"AAPL": expected}


def test_fetch_historical_drawdowns_multi_level_columns_squeezed(monkeypatch):
    df = pd.DataFrame(
        [[100.0, 1000], [90.0, 1000], [66.0, 1000], [80.0, 1000], [95.0, 1000]],
        columns=pd.MultiIndex.from_tuples([("Close", "AAPL"), ("Volume", "AAPL")]),
    )
    # Guard: our synthetic "Close" column is itself a 1-col sub-DataFrame with .columns
    assert hasattr(df["Close"], "columns")
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: df)
    result = st.fetch_historical_drawdowns("covid_crash", ["AAPL"])
    expected = round((66.0 - 100.0) / 100.0 * 100, 1)
    assert result == {"AAPL": expected}


def test_fetch_historical_drawdowns_nonpositive_start_price_maps_to_none(monkeypatch):
    df = pd.DataFrame({"Close": [0.0, -1.0, 5.0, 6.0, 7.0]})
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: df)
    result = st.fetch_historical_drawdowns("covid_crash", ["AAPL"])
    assert result == {"AAPL": None}


def test_fetch_historical_drawdowns_per_ticker_exception_maps_to_none_continues(monkeypatch):
    good_df = pd.DataFrame({"Close": [100.0, 90.0, 66.0, 80.0, 95.0]})

    def fake_download(ticker, **kwargs):
        if ticker == "BAD":
            raise RuntimeError("network error")
        return good_df

    monkeypatch.setattr(yfinance, "download", fake_download)
    result = st.fetch_historical_drawdowns("covid_crash", ["BAD", "AAPL"])
    assert result["BAD"] is None
    expected = round((66.0 - 100.0) / 100.0 * 100, 1)
    assert result["AAPL"] == expected


# ─── assess_fragility ──────────────────────────────────────────────────────────

BASE_SCENARIO_RESULT = {
    "estimated_port_move": -26.0,
    "most_exposed": [{"Ticker": "AAA"}, {"Ticker": "BBB"}, {"Ticker": "CCC"}],
}


def test_assess_fragility_none_portfolio_beta_returns_none():
    result = st.assess_fragility(BASE_SCENARIO_RESULT, None, elevated_beta=1.2, ceiling_beta=1.5, pullback_pct=-10.0)
    assert result is None


def test_assess_fragility_falsy_scenario_result_returns_none():
    result = st.assess_fragility({}, 1.6, elevated_beta=1.2, ceiling_beta=1.5, pullback_pct=-10.0)
    assert result is None


def test_assess_fragility_missing_estimated_port_move_returns_none():
    result = st.assess_fragility({"most_exposed": []}, 1.6, elevated_beta=1.2, ceiling_beta=1.5, pullback_pct=-10.0)
    assert result is None


def test_assess_fragility_severity_fragile_at_ceiling_boundary():
    result = st.assess_fragility(BASE_SCENARIO_RESULT, 1.5, elevated_beta=1.2, ceiling_beta=1.5, pullback_pct=-10.0)
    assert result["severity"] == "fragile"


def test_assess_fragility_severity_caution_just_below_ceiling_at_elevated():
    result = st.assess_fragility(BASE_SCENARIO_RESULT, 1.49, elevated_beta=1.2, ceiling_beta=1.5, pullback_pct=-10.0)
    assert result["severity"] == "caution"
    result_at_elevated = st.assess_fragility(BASE_SCENARIO_RESULT, 1.2, elevated_beta=1.2, ceiling_beta=1.5, pullback_pct=-10.0)
    assert result_at_elevated["severity"] == "caution"


def test_assess_fragility_severity_calm_below_elevated():
    result = st.assess_fragility(BASE_SCENARIO_RESULT, 1.19, elevated_beta=1.2, ceiling_beta=1.5, pullback_pct=-10.0)
    assert result["severity"] == "calm"


def test_assess_fragility_exposed_first_2_tickers():
    result = st.assess_fragility(BASE_SCENARIO_RESULT, 1.6, elevated_beta=1.2, ceiling_beta=1.5, pullback_pct=-10.0)
    assert result["exposed"] == ["AAA", "BBB"]


def test_assess_fragility_mult_computed_correctly():
    result = st.assess_fragility(BASE_SCENARIO_RESULT, 1.6, elevated_beta=1.2, ceiling_beta=1.5, pullback_pct=-10.0)
    assert result["mult"] == 2.6  # abs(-26.0 / -10.0)


def test_assess_fragility_mult_none_when_pullback_pct_zero():
    result = st.assess_fragility(BASE_SCENARIO_RESULT, 1.6, elevated_beta=1.2, ceiling_beta=1.5, pullback_pct=0.0)
    assert result["mult"] is None


# ─── fetch_stress_window_returns ────────────────────────────────────────────────
# Correlation Under Stress (docs/plans/correlation-under-stress.md) — date-based
# (not scenario_id-based), retains each Close series instead of collapsing to a
# scalar, so a caller can build a multi-ticker correlation matrix.

_GOOD_CLOSE = pd.DataFrame({"Close": [100.0, 102.0, 99.0, 101.0, 103.0, 104.0]})
_SHORT_CLOSE = pd.DataFrame({"Close": [100.0, 95.0, 90.0]})   # <5 valid closes


def test_fetch_stress_window_returns_all_downloads_fail_returns_none(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("network error")
    monkeypatch.setattr(yfinance, "download", _boom)
    result = st.fetch_stress_window_returns(["AAPL", "MSFT"], "2020-02-19", "2020-03-23")
    assert result is None


def test_fetch_stress_window_returns_empty_df_for_every_ticker_returns_none(monkeypatch):
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: pd.DataFrame())
    result = st.fetch_stress_window_returns(["AAPL"], "2020-02-19", "2020-03-23")
    assert result is None


def test_fetch_stress_window_returns_never_returns_empty_dataframe_as_fine_signal(monkeypatch):
    # Guard against the exact offline-sentinel-collapse antipattern: total
    # failure must be None, never an empty (but non-None) DataFrame.
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: pd.DataFrame())
    result = st.fetch_stress_window_returns(["AAPL", "MSFT"], "2020-02-19", "2020-03-23")
    assert result is None
    assert not isinstance(result, pd.DataFrame)


def test_fetch_stress_window_returns_ticker_with_fewer_than_5_closes_dropped(monkeypatch):
    def fake_download(ticker, **kwargs):
        return _SHORT_CLOSE if ticker == "SHORT" else _GOOD_CLOSE
    monkeypatch.setattr(yfinance, "download", fake_download)
    result = st.fetch_stress_window_returns(["SHORT", "AAPL"], "2020-02-19", "2020-03-23")
    assert result is not None
    assert "SHORT" not in result.columns
    assert "AAPL" in result.columns


def test_fetch_stress_window_returns_aligned_return_frame_shape(monkeypatch):
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: _GOOD_CLOSE)
    result = st.fetch_stress_window_returns(["AAPL", "MSFT"], "2020-02-19", "2020-03-23")
    assert result is not None
    assert list(result.columns) == ["AAPL", "MSFT"]
    # 6 closes -> 5 pct_change rows after dropna()
    assert len(result) == 5


def test_fetch_stress_window_returns_reparametrized_over_custom_window(monkeypatch):
    # Same <5-closes exclusion re-parametrized over an arbitrary custom
    # (non-preset) date range, not just a named-crash preset window.
    def fake_download(ticker, **kwargs):
        return _SHORT_CLOSE if ticker == "SHORT" else _GOOD_CLOSE
    monkeypatch.setattr(yfinance, "download", fake_download)
    result = st.fetch_stress_window_returns(["SHORT", "AAPL"], "2026-06-01", "2026-06-22")
    assert result is not None
    assert "SHORT" not in result.columns
    assert "AAPL" in result.columns


# ─── stress_correlation_matrix ──────────────────────────────────────────────────

def test_stress_correlation_matrix_none_propagates_on_total_failure(monkeypatch):
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: pd.DataFrame())
    result = st.stress_correlation_matrix(["AAPL", "MSFT"], "2020-02-19", "2020-03-23")
    assert result is None


def test_stress_correlation_matrix_returns_square_corr_df(monkeypatch):
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: _GOOD_CLOSE)
    result = st.stress_correlation_matrix(["AAPL", "MSFT"], "2020-02-19", "2020-03-23")
    assert result is not None
    assert sorted(result.index) == ["AAPL", "MSFT"]
    assert sorted(result.columns) == ["AAPL", "MSFT"]
    assert result.loc["AAPL", "AAPL"] == 1.0


def test_stress_correlation_matrix_date_based_signature_matches_preset_resolved_dates(monkeypatch):
    # Behavior-preservation: calling with a preset's resolved (start, end)
    # produces the same shape of output the scenario_id-based approach would
    # have (fetch_historical_drawdowns used the same HISTORICAL_WINDOWS
    # window internally; this is the new date-based path serving the exact
    # same window for a preset).
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: _GOOD_CLOSE)
    start, end = st.HISTORICAL_WINDOWS["covid_crash"]
    result = st.stress_correlation_matrix(["AAPL", "MSFT"], start, end)
    assert result is not None
    assert result.shape == (2, 2)


# ─── validate_custom_stress_range ───────────────────────────────────────────────

_date, _td = date, timedelta
_TODAY = _date(2026, 8, 5)


def test_validate_custom_stress_range_missing_dates_invalid():
    ok, reason = st.validate_custom_stress_range(None, _TODAY, _TODAY)
    assert ok is False
    assert reason


def test_validate_custom_stress_range_inverted_range_invalid():
    ok, reason = st.validate_custom_stress_range(_TODAY, _TODAY - _td(days=30), _TODAY)
    assert ok is False


def test_validate_custom_stress_range_equal_start_end_invalid():
    ok, reason = st.validate_custom_stress_range(_TODAY, _TODAY, _TODAY)
    assert ok is False


def test_validate_custom_stress_range_end_equal_today_allowed():
    start = _TODAY - _td(days=30)
    ok, reason = st.validate_custom_stress_range(start, _TODAY, _TODAY)
    assert ok is True
    assert reason is None


def test_validate_custom_stress_range_end_after_today_rejected():
    start = _TODAY - _td(days=30)
    future_end = _TODAY + _td(days=1)
    ok, reason = st.validate_custom_stress_range(start, future_end, _TODAY)
    assert ok is False
    assert "future" in reason.lower()


def test_validate_custom_stress_range_shorter_than_14_days_invalid():
    start = _TODAY - _td(days=13)
    ok, reason = st.validate_custom_stress_range(start, _TODAY, _TODAY)
    assert ok is False
    assert "2 weeks" in reason


def test_validate_custom_stress_range_at_least_14_days_valid():
    start = _TODAY - _td(days=14)
    ok, reason = st.validate_custom_stress_range(start, _TODAY, _TODAY)
    assert ok is True


def test_validate_custom_stress_range_before_earliest_start_invalid():
    start = st._EARLIEST_STRESS_START - _td(days=1)
    end = start + _td(days=30)
    ok, reason = st.validate_custom_stress_range(start, end, end + _td(days=1))
    assert ok is False


def test_validate_custom_stress_range_at_earliest_start_valid():
    start = st._EARLIEST_STRESS_START
    end = start + _td(days=30)
    ok, reason = st.validate_custom_stress_range(start, end, end + _td(days=1))
    assert ok is True


# ─── stress_cache_key ────────────────────────────────────────────────────────────

def test_stress_cache_key_preset_scheme_unchanged():
    assert st.stress_cache_key("covid_crash") == "_stress_corr_covid_crash"


def test_stress_cache_key_custom_ranges_distinct():
    key1 = st.stress_cache_key("custom", "2026-06-01", "2026-06-22")
    key2 = st.stress_cache_key("custom", "2026-07-01", "2026-07-22")
    assert key1 != key2


def test_stress_cache_key_same_custom_range_same_key():
    key1 = st.stress_cache_key("custom", "2026-06-01", "2026-06-22")
    key2 = st.stress_cache_key("custom", "2026-06-01", "2026-06-22")
    assert key1 == key2


def test_stress_cache_key_custom_never_collides_with_a_preset_key():
    preset_keys = {st.stress_cache_key(sid) for sid in st.HISTORICAL_WINDOWS}
    custom_key = st.stress_cache_key("custom", "2026-06-01", "2026-06-22")
    assert custom_key not in preset_keys
