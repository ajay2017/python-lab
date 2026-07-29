"""Tests for stock_analyzer/fundamentals.py — sector-relative Business
Quality scoring. Zero imports, fully pure, no I/O or mocking needed.
"""
import pytest

from stock_analyzer import fundamentals as fnd


# ─── count_core_metrics ───────────────────────────────────────────────────────

def test_count_core_metrics_none_is_zero():
    assert fnd.count_core_metrics(None) == 0


def test_count_core_metrics_partial_presence():
    financials = {
        "revenue_growth":  0.10,
        "earnings_growth": None,
        "profit_margins":  0.05,
        "debt_to_equity":  None,
    }
    assert fnd.count_core_metrics(financials) == 2


def test_count_core_metrics_all_present():
    financials = {k: 1.0 for k in fnd.CORE_BQ_KEYS}
    assert fnd.count_core_metrics(financials) == 4


# ─── resolve_fundamentals ─────────────────────────────────────────────────────

_SUFFICIENT = {"revenue_growth": 0.1, "earnings_growth": 0.1, "profit_margins": 0.1}  # 3 metrics
_INSUFFICIENT = {"revenue_growth": 0.1}  # 1 metric


def test_resolve_fundamentals_live_sufficient_always_wins():
    result = fnd.resolve_fundamentals(_SUFFICIENT, _SUFFICIENT, cached_age_days=1, max_age_days=30, min_metrics=3)
    assert result == (_SUFFICIENT, 3, "live", 0)


def test_resolve_fundamentals_cache_wins_when_sufficient_and_fresh_at_exact_boundary():
    result = fnd.resolve_fundamentals(
        _INSUFFICIENT, _SUFFICIENT, cached_age_days=30, max_age_days=30, min_metrics=3,
    )
    assert result == (_SUFFICIENT, 3, "cache", 30)


def test_resolve_fundamentals_cache_stale_falls_back_to_live():
    result = fnd.resolve_fundamentals(
        _INSUFFICIENT, _SUFFICIENT, cached_age_days=31, max_age_days=30, min_metrics=3,
    )
    assert result == (_INSUFFICIENT, 1, "live", 0)


def test_resolve_fundamentals_both_insufficient_returns_live():
    result = fnd.resolve_fundamentals(
        _INSUFFICIENT, _INSUFFICIENT, cached_age_days=1, max_age_days=30, min_metrics=3,
    )
    assert result == (_INSUFFICIENT, 1, "live", 0)


def test_resolve_fundamentals_cached_age_none_with_sufficient_cache_uses_live():
    result = fnd.resolve_fundamentals(
        _INSUFFICIENT, _SUFFICIENT, cached_age_days=None, max_age_days=30, min_metrics=3,
    )
    assert result == (_INSUFFICIENT, 1, "live", 0)


# ─── business_quality_score — sector fallback ────────────────────────────────

def test_business_quality_score_unrecognized_sector_uses_default_norms():
    # Default norms: mgn_excel=18, mgn_good=10. Technology norms: mgn_excel=20,
    # mgn_good=12. 19% margin scores "Excellent" under default but only
    # "Good" under Technology -- proving the default (not Technology) norms
    # were actually applied for an unrecognized sector string.
    financials = {"profit_margins": 0.19}
    score_unknown, signals_unknown = fnd.business_quality_score(financials, sector="NotARealSector")
    score_tech, signals_tech = fnd.business_quality_score(financials, sector="Technology")

    assert score_unknown == pytest.approx(100.0)
    assert "Excellent" in signals_unknown["Profit Margin"]
    assert score_tech == pytest.approx(75.0)
    assert "Good" in signals_tech["Profit Margin"]


# ─── business_quality_score — revenue growth buckets (sector-relative) ──────

def test_revenue_growth_strong_bucket():
    _, signals = fnd.business_quality_score({"revenue_growth": 0.16}, sector="")
    assert "Strong" in signals["Revenue Growth"]


def test_revenue_growth_healthy_bucket():
    _, signals = fnd.business_quality_score({"revenue_growth": 0.10}, sector="")
    assert "Healthy" in signals["Revenue Growth"]


def test_revenue_growth_slow_bucket():
    _, signals = fnd.business_quality_score({"revenue_growth": 0.05}, sector="")
    assert "Slow growth" in signals["Revenue Growth"]


def test_revenue_growth_declining_bucket():
    _, signals = fnd.business_quality_score({"revenue_growth": -0.02}, sector="")
    assert "Declining" in signals["Revenue Growth"]


# ─── business_quality_score — earnings growth buckets (module-level bands) ──

def test_earnings_growth_accelerating_bucket():
    _, signals = fnd.business_quality_score({"earnings_growth": 0.30}, sector="")
    assert "Accelerating" in signals["Earnings Growth"]


def test_earnings_growth_solid_bucket():
    _, signals = fnd.business_quality_score({"earnings_growth": 0.15}, sector="")
    assert "Solid" in signals["Earnings Growth"]


def test_earnings_growth_modest_bucket():
    _, signals = fnd.business_quality_score({"earnings_growth": 0.05}, sector="")
    assert "Modest" in signals["Earnings Growth"]


def test_earnings_growth_contracting_bucket():
    _, signals = fnd.business_quality_score({"earnings_growth": -0.05}, sector="")
    assert "Contracting" in signals["Earnings Growth"]


# ─── business_quality_score — profit margin buckets ─────────────────────────

def test_profit_margin_excellent_bucket():
    _, signals = fnd.business_quality_score({"profit_margins": 0.20}, sector="")
    assert "Excellent" in signals["Profit Margin"]


def test_profit_margin_good_bucket():
    _, signals = fnd.business_quality_score({"profit_margins": 0.12}, sector="")
    assert "Good" in signals["Profit Margin"]


def test_profit_margin_thin_bucket():
    _, signals = fnd.business_quality_score({"profit_margins": 0.07}, sector="")
    assert "Thin" in signals["Profit Margin"]


def test_profit_margin_marginal_bucket():
    _, signals = fnd.business_quality_score({"profit_margins": 0.03}, sector="")
    assert "Marginal" in signals["Profit Margin"]


# ─── business_quality_score — debt-to-equity buckets (lower is better) ──────

def test_debt_equity_very_low_bucket():
    _, signals = fnd.business_quality_score({"debt_to_equity": 20}, sector="")
    assert "Very low debt" in signals["Debt/Equity"]


def test_debt_equity_manageable_bucket():
    _, signals = fnd.business_quality_score({"debt_to_equity": 50}, sector="")
    assert "Manageable" in signals["Debt/Equity"]


def test_debt_equity_elevated_bucket():
    _, signals = fnd.business_quality_score({"debt_to_equity": 100}, sector="")
    assert "Elevated" in signals["Debt/Equity"]


def test_debt_equity_high_leverage_bucket():
    _, signals = fnd.business_quality_score({"debt_to_equity": 200}, sector="")
    assert "High leverage" in signals["Debt/Equity"]


# ─── business_quality_score — missing-metric handling ────────────────────────

def test_missing_metric_excluded_from_max_points():
    score, signals = fnd.business_quality_score({"revenue_growth": 0.10}, sector="")
    # Only revenue growth scored (max_points=20, points=15 "Healthy") ->
    # score = 15/20*100 = 75, not diluted by the 3 absent metrics.
    assert score == pytest.approx(75.0)
    assert "Earnings Growth" not in signals
    assert "Profit Margin" not in signals
    assert "Debt/Equity" not in signals


def test_two_missing_metrics_no_data_quality_warning():
    financials = {"revenue_growth": 0.10, "earnings_growth": 0.10}
    _, signals = fnd.business_quality_score(financials, sector="")
    assert "⚠ Data Quality" not in signals


def test_three_missing_metrics_triggers_data_quality_warning():
    financials = {"revenue_growth": 0.10}
    _, signals = fnd.business_quality_score(financials, sector="")
    assert "⚠ Data Quality" in signals
    assert "3/4 core BQ metrics unavailable" in signals["⚠ Data Quality"]


def test_all_metrics_missing_falls_back_to_score_50():
    score, _ = fnd.business_quality_score({}, sector="")
    assert score == 50.0


# ─── upside_potential ─────────────────────────────────────────────────────────

def test_upside_potential_no_analyst_target_returns_none():
    assert fnd.upside_potential(100.0, {}) is None


def test_upside_potential_falsy_current_price_returns_none():
    assert fnd.upside_potential(0, {"analyst_target": 120.0}) is None
    assert fnd.upside_potential(None, {"analyst_target": 120.0}) is None


def test_upside_potential_target_above_price_is_upside():
    result = fnd.upside_potential(100.0, {"analyst_target": 120.0})
    assert "upside" in result
    assert "20.0%" in result
    assert "$120.00" in result


def test_upside_potential_target_below_price_is_downside():
    result = fnd.upside_potential(100.0, {"analyst_target": 80.0})
    assert "downside" in result
    assert "20.0%" in result
