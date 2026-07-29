"""Tests for stock_analyzer/benchmark_mirror.py — the shadow-portfolio
comparison (invests the same real cash flows into a benchmark ticker instead
of individual stocks) feeding the Benchmark Mirror panel. Pure computation,
no I/O. `fetch_benchmark_prices()` does a real yfinance network call and is
explicitly out of scope, per house convention for pure-logic passes.
Previously zero test coverage.
"""
from datetime import date, timedelta

import pytest

from stock_analyzer import benchmark_mirror as bm


# ─── price_on_or_before ───────────────────────────────────────────────────────

def test_price_on_or_before_exact_date_match():
    prices = {"2024-01-05": 100.0}
    assert bm.price_on_or_before(prices, date(2024, 1, 5)) == 100.0


def test_price_on_or_before_falls_back_3_days():
    prices = {"2024-01-02": 95.0}
    assert bm.price_on_or_before(prices, date(2024, 1, 5)) == 95.0


def test_price_on_or_before_falls_back_5_days():
    prices = {"2024-01-01": 90.0}  # exactly 5 days back from 01-06 (range(6): 0..5)
    assert bm.price_on_or_before(prices, date(2024, 1, 6)) == 90.0


def test_price_on_or_before_6_days_back_not_found():
    prices = {"2023-12-31": 80.0}  # 6 days back from 2024-01-06 -- outside range(6)
    assert bm.price_on_or_before(prices, date(2024, 1, 6)) is None


def test_price_on_or_before_no_match_anywhere_returns_none():
    prices = {"2020-01-01": 50.0}
    assert bm.price_on_or_before({}, date(2024, 1, 5)) is None
    assert bm.price_on_or_before(prices, date(2024, 1, 5)) is None


# ─── build_shadow_portfolio ───────────────────────────────────────────────────

def _flow(flow_type, amount, flow_date):
    return {"flow_type": flow_type, "amount": amount, "flow_date": flow_date}


def test_build_shadow_portfolio_empty_flows_returns_empty_shape():
    prices = {"2024-01-10": 100.0}
    result = bm.build_shadow_portfolio([], prices, date(2024, 1, 10))
    assert result == {"shadow_ending_value": None, "flow_attribution": [], "total_invested": 0.0}


def test_build_shadow_portfolio_no_price_for_today_returns_empty_shape():
    flows = [_flow("baseline", 10000.0, "2024-01-01")]
    result = bm.build_shadow_portfolio(flows, {}, date(2024, 1, 10))
    assert result["shadow_ending_value"] is None


def test_build_shadow_portfolio_invalid_flow_type_skipped():
    prices = {"2024-01-01": 100.0, "2024-01-10": 110.0}
    flows = [_flow("dividend", 500.0, "2024-01-01")]  # not baseline/deposit/withdrawal
    result = bm.build_shadow_portfolio(flows, prices, date(2024, 1, 10))
    assert result["flow_attribution"] == []
    assert result["shadow_ending_value"] == 0.0


def test_build_shadow_portfolio_non_positive_amount_skipped():
    prices = {"2024-01-01": 100.0, "2024-01-10": 110.0}
    flows = [_flow("deposit", 0.0, "2024-01-01"), _flow("deposit", -50.0, "2024-01-01")]
    result = bm.build_shadow_portfolio(flows, prices, date(2024, 1, 10))
    assert result["flow_attribution"] == []


def test_build_shadow_portfolio_withdrawal_gets_negative_signed_amount():
    prices = {"2024-01-01": 100.0, "2024-01-05": 100.0, "2024-01-10": 100.0}
    flows = [
        _flow("baseline", 1000.0, "2024-01-01"),
        _flow("withdrawal", 200.0, "2024-01-05"),
    ]
    result = bm.build_shadow_portfolio(flows, prices, date(2024, 1, 10))
    withdrawal_row = next(f for f in result["flow_attribution"] if f["flow_type"] == "withdrawal")
    assert withdrawal_row["amount"] == -200.0
    # Total units net: 1000/100 - 200/100 = 8 units * 100 = 800.
    assert result["shadow_ending_value"] == pytest.approx(800.0)


def test_build_shadow_portfolio_flow_with_no_resolvable_price_skipped():
    prices = {"2024-01-10": 100.0}  # no price anywhere near 2024-01-01
    flows = [_flow("deposit", 500.0, "2024-01-01")]
    result = bm.build_shadow_portfolio(flows, prices, date(2024, 1, 10))
    assert result["flow_attribution"] == []
    assert result["shadow_ending_value"] == 0.0


def test_build_shadow_portfolio_total_invested_sums_only_positive_flows():
    prices = {"2024-01-01": 100.0, "2024-01-05": 100.0, "2024-01-10": 100.0}
    flows = [
        _flow("baseline", 1000.0, "2024-01-01"),
        _flow("deposit", 500.0, "2024-01-05"),
        _flow("withdrawal", 300.0, "2024-01-05"),
    ]
    result = bm.build_shadow_portfolio(flows, prices, date(2024, 1, 10))
    # total_invested is NOT reduced by the withdrawal.
    assert result["total_invested"] == pytest.approx(1500.0)


def test_build_shadow_portfolio_per_flow_return_pct_calc():
    prices = {"2024-01-01": 100.0, "2024-01-10": 120.0}
    flows = [_flow("baseline", 1000.0, "2024-01-01")]
    result = bm.build_shadow_portfolio(flows, prices, date(2024, 1, 10))
    row = result["flow_attribution"][0]
    assert row["return_pct"] == pytest.approx(20.0)


# ─── build_benchmark_curve ────────────────────────────────────────────────────

def _daily_prices(start, n, price_fn):
    out = {}
    for i in range(n):
        d = start + timedelta(days=i)
        out[str(d)] = price_fn(i)
    return out


def test_build_benchmark_curve_no_dates_in_range_returns_empty_dict():
    prices = {"2020-01-01": 100.0}
    result = bm.build_benchmark_curve(prices, [], date(2024, 1, 1), date(2024, 1, 10))
    assert result == {}


def test_build_benchmark_curve_no_price_at_start_returns_empty_dict():
    prices = _daily_prices(date(2024, 1, 5), 5, lambda i: 100.0 + i)
    result = bm.build_benchmark_curve(prices, [], date(2024, 1, 1), date(2024, 1, 10))
    assert result == {}


def test_build_benchmark_curve_start_date_indexed_to_100():
    prices = _daily_prices(date(2024, 1, 1), 10, lambda i: 100.0 + i)
    result = bm.build_benchmark_curve(prices, [], date(2024, 1, 1), date(2024, 1, 10))
    assert result[str(date(2024, 1, 1))]["benchmark_idx"] == pytest.approx(100.0, abs=0.01)


def test_build_benchmark_curve_flow_before_start_swept_in_on_first_range_day():
    # A flow dated BEFORE `start` is still in `valid_flows` (sorted) and gets
    # applied on the FIRST day of the iterated range via the
    # `valid_flows[flow_idx][0] <= d` sweep, not silently dropped. Prices must
    # extend a bit before `start` too, so the flow's own date has a
    # resolvable price via price_on_or_before.
    prices = _daily_prices(date(2023, 12, 29), 15, lambda i: 100.0)
    flows = [_flow("baseline", 1000.0, "2023-12-29")]  # before start (2024-01-01)
    result = bm.build_benchmark_curve(prices, flows, date(2024, 1, 1), date(2024, 1, 10))
    first_day = result[str(date(2024, 1, 1))]
    assert first_day["shadow_value"] == pytest.approx(1000.0)


def test_build_benchmark_curve_shadow_value_grows_after_deposit():
    prices = _daily_prices(date(2024, 1, 1), 10, lambda i: 100.0)
    flows = [_flow("deposit", 500.0, "2024-01-05")]
    result = bm.build_benchmark_curve(prices, flows, date(2024, 1, 1), date(2024, 1, 10))
    before = result[str(date(2024, 1, 4))]["shadow_value"]
    after = result[str(date(2024, 1, 5))]["shadow_value"]
    assert before == 0.0
    assert after == pytest.approx(500.0)


# ─── build_drawdown_series ────────────────────────────────────────────────────

def test_build_drawdown_series_no_dates_in_range_returns_empty_dict():
    prices = {"2020-01-01": 100.0}
    assert bm.build_drawdown_series(prices, date(2024, 1, 1), date(2024, 1, 10)) == {}


def test_build_drawdown_series_monotonic_rise_has_zero_drawdown_throughout():
    prices = _daily_prices(date(2024, 1, 1), 5, lambda i: 100.0 + i * 5)
    result = bm.build_drawdown_series(prices, date(2024, 1, 1), date(2024, 1, 5))
    assert all(v == 0.0 for v in result.values())


def test_build_drawdown_series_trough_matches_formula():
    # Peak 120 on day 2, trough 90 on day 4.
    seq = [100.0, 120.0, 110.0, 90.0, 95.0]
    prices = _daily_prices(date(2024, 1, 1), 5, lambda i: seq[i])
    result = bm.build_drawdown_series(prices, date(2024, 1, 1), date(2024, 1, 5))
    expected_trough = round((90.0 / 120.0 - 1) * 100, 2)
    assert result[str(date(2024, 1, 4))] == pytest.approx(expected_trough)


def test_build_drawdown_series_new_high_resets_drawdown_to_zero():
    seq = [100.0, 90.0, 120.0]  # dip then new high
    prices = _daily_prices(date(2024, 1, 1), 3, lambda i: seq[i])
    result = bm.build_drawdown_series(prices, date(2024, 1, 1), date(2024, 1, 3))
    assert result[str(date(2024, 1, 3))] == 0.0


def test_build_drawdown_series_never_exceeds_zero():
    seq = [100.0, 105.0, 95.0, 110.0, 102.0]
    prices = _daily_prices(date(2024, 1, 1), 5, lambda i: seq[i])
    result = bm.build_drawdown_series(prices, date(2024, 1, 1), date(2024, 1, 5))
    assert all(v <= 0.0 for v in result.values())


# ─── compute_shadow_mwr ───────────────────────────────────────────────────────

def test_compute_shadow_mwr_days_leq_zero_returns_none():
    result = bm.compute_shadow_mwr(1000.0, date(2024, 1, 10), 1100.0, date(2024, 1, 10), [])
    assert result is None


def test_compute_shadow_mwr_shadow_ending_value_none_returns_none():
    result = bm.compute_shadow_mwr(1000.0, date(2024, 1, 1), None, date(2024, 1, 10), [])
    assert result is None


def test_compute_shadow_mwr_flow_outside_range_excluded():
    baseline_date = date(2024, 1, 1)
    today = date(2024, 3, 1)
    flows = [_flow("deposit", 100.0, "2023-01-01")]  # before baseline_date
    result = bm.compute_shadow_mwr(1000.0, baseline_date, 1100.0, today, flows)
    # If the out-of-range flow were included it would shift net_flow/weighted;
    # confirm result matches the zero-flow case.
    result_no_flows = bm.compute_shadow_mwr(1000.0, baseline_date, 1100.0, today, [])
    assert result == result_no_flows


def test_compute_shadow_mwr_denom_leq_zero_returns_none():
    baseline_date = date(2024, 1, 1)
    today = date(2024, 2, 1)
    # w = (today - fd).days / days -- a withdrawal dated near baseline_date
    # (i.e. long before `today`) gets weight close to 1.0, so a large enough
    # withdrawal there can drive denom = baseline_value + weighted <= 0.
    flows = [_flow("withdrawal", 5000.0, "2024-01-02")]
    result = bm.compute_shadow_mwr(1000.0, baseline_date, 500.0, today, flows)
    assert result is None


def test_compute_shadow_mwr_annualized_none_below_30_days():
    baseline_date = date(2024, 1, 1)
    today = baseline_date + timedelta(days=29)
    result = bm.compute_shadow_mwr(1000.0, baseline_date, 1100.0, today, [])
    assert result["annualized_pct"] is None


def test_compute_shadow_mwr_annualized_present_at_30_days():
    baseline_date = date(2024, 1, 1)
    today = baseline_date + timedelta(days=30)
    result = bm.compute_shadow_mwr(1000.0, baseline_date, 1100.0, today, [])
    assert result["annualized_pct"] is not None


def test_compute_shadow_mwr_annualized_none_when_total_loss_or_worse():
    baseline_date = date(2024, 1, 1)
    today = baseline_date + timedelta(days=60)
    # shadow_ending_value far below baseline -> period_return <= -1 possible
    result = bm.compute_shadow_mwr(1000.0, baseline_date, 0.0, today, [])
    assert result["period_return_pct"] == pytest.approx(-100.0)
    assert result["annualized_pct"] is None


# ─── beta_adjusted_alpha ──────────────────────────────────────────────────────

def test_beta_adjusted_alpha_none_input_returns_none():
    assert bm.beta_adjusted_alpha(None, 10.0, 1.0) is None
    assert bm.beta_adjusted_alpha(10.0, None, 1.0) is None
    assert bm.beta_adjusted_alpha(10.0, 10.0, None) is None


def test_beta_adjusted_alpha_concrete_spot_check():
    # expected = actual - beta*benchmark = 15 - 1.2*10 = 3.0
    assert bm.beta_adjusted_alpha(15.0, 10.0, 1.2) == pytest.approx(3.0)


def test_beta_adjusted_alpha_negative_beta():
    # expected = actual - beta*benchmark = 5 - (-0.5)*10 = 10.0
    assert bm.beta_adjusted_alpha(5.0, 10.0, -0.5) == pytest.approx(10.0)


def test_beta_adjusted_alpha_negative_benchmark_return():
    # expected = actual - beta*benchmark = 8 - 1.5*(-6) = 17.0
    assert bm.beta_adjusted_alpha(8.0, -6.0, 1.5) == pytest.approx(17.0)
