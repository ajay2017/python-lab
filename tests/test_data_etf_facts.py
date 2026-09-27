"""Boundary tests for stock_analyzer/data.py::fetch_etf_facts_from_info() —
Phase 1 ETF support (F-279 §11). Every field is optional/None-safe via
.get() — no arithmetic that assumes presence, unlike fetch_financials_from_info
(which is left byte-identical and untouched by this feature)."""
import pytest

from stock_analyzer.data import fetch_etf_facts_from_info

pytestmark = pytest.mark.fast

_EXPECTED_KEYS = {
    "quote_type", "name", "category", "fund_family", "net_expense_ratio",
    "total_assets", "nav_price", "distribution_yield", "ytd_return",
    "legal_type", "trailing_annual_dividend_yield",
}


def test_empty_info_returns_all_none_without_raising():
    out = fetch_etf_facts_from_info({})
    assert set(out.keys()) == _EXPECTED_KEYS
    assert all(v is None for v in out.values())


def test_partial_info_extracts_only_present_keys():
    info = {"quoteType": "ETF", "category": "Large Blend", "netExpenseRatio": 0.03}
    out = fetch_etf_facts_from_info(info)
    assert out["quote_type"] == "ETF"
    assert out["category"] == "Large Blend"
    assert out["net_expense_ratio"] == 0.03
    # Everything else stays None, not fabricated.
    assert out["fund_family"] is None
    assert out["total_assets"] is None
    assert out["nav_price"] is None
    assert out["distribution_yield"] is None
    assert out["ytd_return"] is None
    assert out["legal_type"] is None
    assert out["trailing_annual_dividend_yield"] is None


def test_stock_shaped_info_returns_all_none_cleanly():
    """An ordinary equity .info dict has none of the ETF-shaped keys — must
    not raise, and must not fabricate any ETF-facing value."""
    stock_info = {
        "quoteType": "EQUITY",
        "trailingPE": 28.4,
        "marketCap": 3_000_000_000_000,
        "sector": "Technology",
        "shortName": "Apple Inc.",
    }
    out = fetch_etf_facts_from_info(stock_info)
    assert set(out.keys()) == _EXPECTED_KEYS
    assert out["quote_type"] == "EQUITY"
    assert out["category"] is None
    assert out["net_expense_ratio"] is None


def test_name_prefers_long_name_over_short_name():
    out = fetch_etf_facts_from_info({"longName": "SPDR S&P 500", "shortName": "SPY"})
    assert out["name"] == "SPDR S&P 500"


def test_name_falls_back_to_short_name():
    out = fetch_etf_facts_from_info({"shortName": "SPY"})
    assert out["name"] == "SPY"


def test_units_are_not_converted_or_compared():
    """netExpenseRatio (percent units) and yield (fraction units) are
    extracted verbatim, no unit coercion — the docstring's own caveat."""
    out = fetch_etf_facts_from_info({"netExpenseRatio": 0.0945, "yield": 0.0098})
    assert out["net_expense_ratio"] == 0.0945
    assert out["distribution_yield"] == 0.0098
