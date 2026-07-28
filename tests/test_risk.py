"""
Tests for stock_analyzer/risk.py — position sizing, ATR stops, and the
Sharpe/Sortino/VaR/beta risk metrics feeding 🔗 Risk Analysis. Previously
zero test coverage despite `position_sizing()` being a real, user-facing
sizing calculation and `atr_stop_loss()` feeding the app's stop-ladder.
"""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer.risk import (
    atr_stop_loss,
    position_sizing,
    sharpe_ratio,
    sortino_ratio,
    max_drawdown_pct,
    var_95_daily,
    beta_vs_market,
    pearson_corr_vs_benchmark,
    rate_sensitivity_per_ticker,
    compute_all_risk,
    compute_portfolio_risk_metrics,
)
from stock_analyzer.constants import ATR_STOP_MULT


def _flat_range_df(close: float, half_range: float, n: int = 30) -> pd.DataFrame:
    """Constant daily High-Low range around a constant Close -> ATR converges
    exactly to that constant range (EWM of a truly constant series stays at
    the constant), giving a deterministic, non-brittle ATR value to assert on."""
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"Close": [close] * n, "High": [close + half_range] * n, "Low": [close - half_range] * n},
        index=idx,
    )


def _price_df(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"Close": closes, "High": [c + 1.0 for c in closes], "Low": [c - 1.0 for c in closes]},
        index=idx,
    )


# ─── atr_stop_loss ────────────────────────────────────────────────────────────

def test_atr_stop_loss_constant_range_gives_exact_atr():
    df = _flat_range_df(close=100.0, half_range=1.0)  # daily range = 2.0
    stop, atr_val = atr_stop_loss(df)
    assert atr_val == 2.0
    assert stop == round(100.0 - ATR_STOP_MULT * 2.0, 2)


def test_atr_stop_loss_wider_range_gives_wider_stop():
    tight = _flat_range_df(close=100.0, half_range=0.5)
    wide = _flat_range_df(close=100.0, half_range=2.0)
    _, tight_atr = atr_stop_loss(tight)
    _, wide_atr = atr_stop_loss(wide)
    assert wide_atr > tight_atr


def test_atr_stop_loss_custom_multiplier():
    df = _flat_range_df(close=100.0, half_range=1.0)
    stop, atr_val = atr_stop_loss(df, multiplier=3.0)
    assert stop == round(100.0 - 3.0 * atr_val, 2)


# ─── position_sizing ──────────────────────────────────────────────────────────

def test_position_sizing_none_when_entry_at_or_below_stop():
    assert position_sizing(100_000, 0.01, entry=50.0, stop=50.0) is None
    assert position_sizing(100_000, 0.01, entry=50.0, stop=55.0) is None


def test_position_sizing_none_when_stop_non_positive():
    assert position_sizing(100_000, 0.01, entry=50.0, stop=0) is None
    assert position_sizing(100_000, 0.01, entry=50.0, stop=-5) is None


def test_position_sizing_basic_calculation():
    result = position_sizing(portfolio_value=100_000, risk_pct=0.01, entry=50.0, stop=45.0)
    # risk_dollars = 1000; risk_per_share = 5; shares = 1000/5 = 200
    assert result["shares"] == 200
    assert result["risk_budget"] == 1000.0
    assert result["risk_per_share"] == 5.0
    assert result["actual_risk"] == 1000.0
    assert result["total_cost"] == 10_000.0
    assert result["portfolio_pct"] == 10.0


def test_position_sizing_shares_floor_at_one():
    # A tiny risk budget with a large risk-per-share would compute < 1 share
    # -- must floor at 1, never 0 or negative.
    result = position_sizing(portfolio_value=1_000, risk_pct=0.001, entry=500.0, stop=400.0)
    assert result["shares"] == 1


def test_position_sizing_zero_portfolio_value_guards_division():
    result = position_sizing(portfolio_value=0, risk_pct=0.01, entry=50.0, stop=45.0)
    assert result["portfolio_pct"] == 0.0
    assert result["risk_pct_actual"] == 0.0


def test_position_sizing_no_ceiling_omits_ceiling_keys():
    result = position_sizing(100_000, 0.01, entry=50.0, stop=45.0)
    assert "ceiling_pct" not in result
    assert "ceiling_capped" not in result
    assert "uncapped_shares" not in result


def test_position_sizing_ceiling_caps_when_risk_math_breaches_it():
    # Tight stop -> risk math would want a huge dollar position; ceiling
    # must cap it and flag ceiling_capped=True, preserving the uncapped figure.
    result = position_sizing(
        portfolio_value=100_000, risk_pct=0.02, entry=100.0, stop=99.0,  # 1% stop distance
        max_position_pct=15.0,
    )
    # Uncapped: risk_dollars=2000, risk_per_share=1 -> 2000 shares -> $200k (200% of book)
    assert result["uncapped_shares"] == 2000
    assert result["ceiling_capped"] is True
    # Capped to 15% of 100k / $100 entry = 150 shares
    assert result["shares"] == 150
    assert result["total_cost"] == 15_000.0


def test_position_sizing_ceiling_not_capped_when_under_limit():
    result = position_sizing(
        portfolio_value=100_000, risk_pct=0.01, entry=50.0, stop=45.0,
        max_position_pct=50.0,
    )
    assert result["ceiling_capped"] is False
    assert result["shares"] == result["uncapped_shares"]


# ─── sharpe_ratio / sortino_ratio ─────────────────────────────────────────────

def test_sharpe_ratio_flat_price_returns_zero():
    # Zero returns every day -> excess is a negative constant. Pinned as a
    # regression test for a real bug found+fixed 2026-07-27: averaging the
    # repeating-binary-fraction rf_daily constant across N identical rows
    # left ~1e-19-scale floating-point noise in std(), which an exact
    # `std == 0` check missed -- dividing by that noise blew this up to
    # roughly +/-3.4e16 instead of the intended 0.0. Fixed via a tolerance
    # check (_ZERO_VOL_EPS) in risk.py. This test would have caught it.
    df = _price_df([100.0] * 30)
    assert sharpe_ratio(df) == 0.0


def test_sortino_ratio_flat_price_returns_zero():
    # Same root cause and fix as test_sharpe_ratio_flat_price_returns_zero.
    df = _price_df([100.0] * 30)
    assert sortino_ratio(df) == 0.0


def test_sortino_ratio_no_negative_days_returns_99():
    # Strong, consistent uptrend well above the risk-free rate every day ->
    # downside is empty -> the "excellent, not zero" special case (99.0)
    closes = [100.0]
    for _ in range(29):
        closes.append(closes[-1] * 1.02)  # +2%/day, way above rf_daily (~0.00018)
    df = _price_df(closes)
    assert sortino_ratio(df) == 99.0


def test_sharpe_ratio_positive_for_uptrend_with_real_variance():
    # A perfectly smooth exponential trend has genuinely ZERO return
    # variance by construction (every daily return is exactly identical) --
    # that's a degenerate input, not real market data, and correctly falls
    # into the "no volatility" 0.0 branch. Alternate the daily move size so
    # there's real variance around the (positive) trend, like an actual
    # price series would have.
    closes = [100.0]
    for i in range(29):
        closes.append(closes[-1] * (1.02 if i % 2 == 0 else 1.005))
    df = _price_df(closes)
    assert sharpe_ratio(df) > 0


def test_sharpe_ratio_negative_for_downtrend_with_real_variance():
    closes = [100.0]
    for i in range(29):
        closes.append(closes[-1] * (0.98 if i % 2 == 0 else 0.995))
    df = _price_df(closes)
    assert sharpe_ratio(df) < 0


# ─── max_drawdown_pct ─────────────────────────────────────────────────────────

def test_max_drawdown_pct_known_peak_and_trough():
    df = _price_df([100.0, 120.0, 90.0, 110.0])
    # rolling max: [100,120,120,120]; dd: [0, 0, -0.25, -0.0833] -> min -25.0%
    assert max_drawdown_pct(df) == -25.0


def test_max_drawdown_pct_monotonic_uptrend_is_zero():
    df = _price_df([100.0, 105.0, 110.0, 115.0])
    assert max_drawdown_pct(df) == 0.0


# ─── var_95_daily ─────────────────────────────────────────────────────────────

def test_var_95_daily_flat_price_is_exactly_zero():
    df = _price_df([100.0] * 30)
    assert var_95_daily(df) == 0.0


def test_var_95_daily_is_negative_for_volatile_series():
    closes = [100, 95, 105, 90, 110, 85, 115, 80, 120, 75] * 3
    df = _price_df([float(c) for c in closes])
    assert var_95_daily(df) < 0


# ─── beta_vs_market ───────────────────────────────────────────────────────────

def test_beta_vs_market_insufficient_overlap_returns_none():
    df = _price_df([100.0, 101.0, 102.0])  # only 2 return days, needs >= 20
    market = _price_df([100.0, 101.0, 102.0])
    assert beta_vs_market(df, market) is None


def test_beta_vs_market_identical_returns_gives_beta_one():
    closes = [100.0]
    for i in range(29):
        closes.append(closes[-1] * (1.0 + 0.01 * ((-1) ** i)))  # oscillating, non-trivial variance
    df = _price_df(closes)
    market = _price_df(closes)  # identical series
    beta = beta_vs_market(df, market)
    assert beta == pytest.approx(1.0, abs=0.01)


def test_beta_vs_market_zero_market_variance_returns_none():
    df = _price_df([100.0 * (1.01 ** i) for i in range(25)])
    flat_market = _price_df([100.0] * 25)  # zero variance
    assert beta_vs_market(df, flat_market) is None


def test_beta_vs_market_double_leveraged_approx_two():
    market_closes = [100.0]
    for i in range(29):
        market_closes.append(market_closes[-1] * (1.0 + 0.01 * ((-1) ** i)))
    stock_closes = [100.0]
    for i in range(29):
        market_ret = (market_closes[i + 1] / market_closes[i]) - 1.0
        stock_closes.append(stock_closes[-1] * (1.0 + 2 * market_ret))
    df = _price_df(stock_closes)
    market = _price_df(market_closes)
    beta = beta_vs_market(df, market)
    assert beta == pytest.approx(2.0, abs=0.01)


# ─── pearson_corr_vs_benchmark ────────────────────────────────────────────────

def test_pearson_corr_insufficient_overlap_returns_none():
    df = _price_df([100.0, 101.0, 102.0])
    bench = _price_df([100.0, 101.0, 102.0])
    assert pearson_corr_vs_benchmark(df, bench) is None


def test_pearson_corr_identical_series_is_one():
    closes = [100.0]
    for i in range(29):
        closes.append(closes[-1] * (1.0 + 0.01 * ((-1) ** i)))
    df = _price_df(closes)
    bench = _price_df(closes)
    assert pearson_corr_vs_benchmark(df, bench) == pytest.approx(1.0, abs=0.001)


def test_pearson_corr_inverse_series_is_negative_one():
    base = [100.0]
    for i in range(29):
        base.append(base[-1] * (1.0 + 0.01 * ((-1) ** i)))
    inverse = [100.0]
    for i in range(29):
        ret = (base[i + 1] / base[i]) - 1.0
        inverse.append(inverse[-1] * (1.0 - ret))
    df = _price_df(inverse)
    bench = _price_df(base)
    assert pearson_corr_vs_benchmark(df, bench) == pytest.approx(-1.0, abs=0.001)


def test_pearson_corr_nan_guard_returns_none_for_constant_series():
    # A flat (zero-variance) series makes correlation mathematically undefined
    # (NaN) -- must be caught and returned as None, not a NaN float.
    df = _price_df([100.0] * 25)
    bench = _price_df([100.0 * (1.01 ** i) for i in range(25)])
    assert pearson_corr_vs_benchmark(df, bench) is None


# ─── rate_sensitivity_per_ticker ──────────────────────────────────────────────

def _port_row(ticker, sector, weight):
    return {"Ticker": ticker, "Sector": sector, "Weight (%)": weight}


def test_rate_sensitivity_falls_back_to_sector_score_without_tlt():
    port_df = pd.DataFrame([_port_row("NVDA", "Semiconductors", 10.0)])
    rows = rate_sensitivity_per_ticker(port_df, held_data={}, tlt_df=None)
    assert rows[0]["TLT Corr"] is None
    assert rows[0]["Sector Score"] == -0.70
    assert "Rate-sensitive" in rows[0]["Implication"]


def test_rate_sensitivity_implication_tiers_from_sector_score():
    # Financials (+0.70) -> rate beneficiary; Healthcare (-0.15) -> mild headwind;
    # Other (0.00) -> roughly neutral; Defense (+0.05) -> roughly neutral (< 0.1)
    port_df = pd.DataFrame([
        _port_row("JPM", "Financials", 5.0),
        _port_row("UNH", "Healthcare", 5.0),
        _port_row("XYZ", "Other", 5.0),
        _port_row("LMT", "Defense", 5.0),
    ])
    rows = rate_sensitivity_per_ticker(port_df, held_data={}, tlt_df=None)
    by_ticker = {r["Ticker"]: r["Implication"] for r in rows}
    assert "beneficiary" in by_ticker["JPM"]
    assert "headwind" in by_ticker["UNH"]
    assert "neutral" in by_ticker["XYZ"]
    assert "neutral" in by_ticker["LMT"]


def test_rate_sensitivity_sorted_ascending_most_sensitive_first():
    port_df = pd.DataFrame([
        _port_row("JPM", "Financials", 5.0),   # +0.70
        _port_row("NVDA", "Semiconductors", 5.0),  # -0.70
    ])
    rows = rate_sensitivity_per_ticker(port_df, held_data={}, tlt_df=None)
    assert rows[0]["Ticker"] == "NVDA"  # most negative (most rate-sensitive) first
    assert rows[1]["Ticker"] == "JPM"


def test_rate_sensitivity_unknown_sector_defaults_to_zero_score():
    port_df = pd.DataFrame([_port_row("XYZ", "Not A Real Sector", 5.0)])
    rows = rate_sensitivity_per_ticker(port_df, held_data={}, tlt_df=None)
    assert rows[0]["Sector Score"] == 0.0


def test_rate_sensitivity_uses_live_tlt_corr_when_available():
    closes = [100.0]
    for i in range(29):
        closes.append(closes[-1] * (1.0 + 0.01 * ((-1) ** i)))
    df = _price_df(closes)
    tlt_df = _price_df(closes)  # identical -> corr ~1.0, overrides the sector score
    port_df = pd.DataFrame([_port_row("NVDA", "Semiconductors", 5.0)])
    held_data = {"NVDA": {"df": df}}
    rows = rate_sensitivity_per_ticker(port_df, held_data, tlt_df)
    assert rows[0]["TLT Corr"] == pytest.approx(1.0, abs=0.001)
    assert "beneficiary" in rows[0]["Implication"]  # TLT corr overrides the sector's own -0.70 read


def test_rate_sensitivity_missing_held_data_for_ticker_falls_back_to_sector():
    tlt_df = _price_df([100.0 * (1.01 ** i) for i in range(25)])
    port_df = pd.DataFrame([_port_row("NVDA", "Semiconductors", 5.0)])
    rows = rate_sensitivity_per_ticker(port_df, held_data={}, tlt_df=tlt_df)
    assert rows[0]["TLT Corr"] is None
    assert rows[0]["Sector Score"] == -0.70


# ─── compute_all_risk ─────────────────────────────────────────────────────────

def test_compute_all_risk_returns_all_expected_keys():
    df = _price_df([100.0 * (1.01 ** i) for i in range(25)])
    spy = _price_df([100.0 * (1.005 ** i) for i in range(25)])
    result = compute_all_risk(df, spy)
    assert set(result.keys()) == {"sharpe", "sortino", "max_drawdown", "var_95", "beta"}
    assert result["beta"] is not None


def test_compute_all_risk_beta_none_without_spy():
    df = _price_df([100.0 * (1.01 ** i) for i in range(25)])
    result = compute_all_risk(df, spy_df=None)
    assert result["beta"] is None


# ─── compute_portfolio_risk_metrics ──────────────────────────────────────────

def test_compute_portfolio_risk_metrics_empty_held_data_returns_empty_dict():
    port_df = pd.DataFrame([_port_row("AAPL", "Consumer Tech", 100.0)])
    assert compute_portfolio_risk_metrics(port_df, held_data={}) == {}


def test_compute_portfolio_risk_metrics_insufficient_history_returns_empty_dict():
    short_df = _price_df([100.0, 101.0, 102.0])  # only 3 rows, needs >= 10
    port_df = pd.DataFrame([_port_row("AAPL", "Consumer Tech", 100.0)])
    held_data = {"AAPL": {"df": short_df}}
    assert compute_portfolio_risk_metrics(port_df, held_data) == {}


def test_compute_portfolio_risk_metrics_no_matching_weights_returns_empty_dict():
    df = _price_df([100.0 * (1.01 ** i) for i in range(15)])
    port_df = pd.DataFrame([_port_row("MSFT", "Enterprise Tech", 100.0)])  # different ticker
    held_data = {"AAPL": {"df": df}}  # AAPL has data but isn't in port_df
    assert compute_portfolio_risk_metrics(port_df, held_data) == {}


def test_compute_portfolio_risk_metrics_happy_path_returns_full_shape():
    df1 = _price_df([100.0 * (1.01 ** i) for i in range(25)])
    df2 = _price_df([50.0 * (1.005 ** i) for i in range(25)])
    port_df = pd.DataFrame([
        _port_row("AAPL", "Consumer Tech", 60.0),
        _port_row("MSFT", "Enterprise Tech", 40.0),
    ])
    held_data = {"AAPL": {"df": df1}, "MSFT": {"df": df2}}
    spy = _price_df([100.0 * (1.003 ** i) for i in range(25)])
    result = compute_portfolio_risk_metrics(port_df, held_data, spy_df=spy)
    assert set(result.keys()) == {
        "beta", "ann_volatility", "sharpe", "sortino",
        "var_95_pct", "cvar_95_pct", "max_drawdown",
        "drawdown_series", "cum_returns",
    }
    assert result["beta"] is not None
    assert result["ann_volatility"] >= 0


def test_compute_portfolio_risk_metrics_weights_renormalize_to_100pct():
    # port_df's weights don't need to sum to 100 on their own (e.g. only a
    # subset of holdings has price history) -- the function renormalizes
    # among the tickers it can actually price.
    df = _price_df([100.0 * (1.01 ** i) for i in range(15)])
    port_df = pd.DataFrame([_port_row("AAPL", "Consumer Tech", 60.0)])  # 60%, not 100%
    held_data = {"AAPL": {"df": df}}
    result = compute_portfolio_risk_metrics(port_df, held_data)
    assert result != {}  # should not fail just because weights don't sum to 100


def test_compute_portfolio_risk_metrics_flat_portfolio_sortino_stays_zero():
    # Regression test for the same floating-point-noise bug fixed in
    # sortino_ratio(): an all-flat-price portfolio's excess-of-risk-free
    # returns hits the identical downside_std near-zero-noise issue in
    # compute_portfolio_risk_metrics' own inline sortino calc -- confirmed
    # to reproduce at n=29 rows before the _ZERO_VOL_EPS fix (would blow up
    # to +/-quadrillions instead of 0.0).
    df = _price_df([100.0] * 30)
    port_df = pd.DataFrame([_port_row("AAPL", "Consumer Tech", 100.0)])
    held_data = {"AAPL": {"df": df}}
    result = compute_portfolio_risk_metrics(port_df, held_data)
    assert result["sortino"] == 0.0
    assert result["sharpe"] == 0.0


def test_compute_portfolio_risk_metrics_beta_absent_without_spy():
    df = _price_df([100.0 * (1.01 ** i) for i in range(15)])
    port_df = pd.DataFrame([_port_row("AAPL", "Consumer Tech", 100.0)])
    held_data = {"AAPL": {"df": df}}
    result = compute_portfolio_risk_metrics(port_df, held_data, spy_df=None)
    assert result["beta"] is None
