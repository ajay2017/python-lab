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
