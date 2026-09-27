"""Unit tests for stock_analyzer/etf_scoring.py — ETF-support Phase 2
(docs/plans/etf-multi-asset-support.md §11). Every function tested at its
boundaries per the approved build spec.
"""
import pytest

from stock_analyzer.constants import (
    COMPOSITE_BUY,
    COMPOSITE_HOLD,
    ETF_COST_SCORE_FLOOR,
    ETF_EXPENSE_RATIO_CHEAP_PCT,
    ETF_EXPENSE_RATIO_EXPENSIVE_PCT,
    ETF_AUM_THIN_FLOOR_USD,
)
from stock_analyzer.etf_scoring import (
    expense_ratio_score,
    etf_available,
    etf_composite,
    etf_recommendation,
    etf_aum_thin,
)

pytestmark = pytest.mark.fast


# ── expense_ratio_score ──────────────────────────────────────────────────────

def test_expense_ratio_score_at_cheap_boundary_is_ceiling():
    assert expense_ratio_score(ETF_EXPENSE_RATIO_CHEAP_PCT) == 100.0


def test_expense_ratio_score_below_cheap_is_still_ceiling():
    assert expense_ratio_score(ETF_EXPENSE_RATIO_CHEAP_PCT - 0.05) == 100.0


def test_expense_ratio_score_at_expensive_boundary_is_floor():
    assert expense_ratio_score(ETF_EXPENSE_RATIO_EXPENSIVE_PCT) == float(ETF_COST_SCORE_FLOOR)


def test_expense_ratio_score_above_expensive_is_still_floor():
    assert expense_ratio_score(ETF_EXPENSE_RATIO_EXPENSIVE_PCT + 0.50) == float(ETF_COST_SCORE_FLOOR)


def test_expense_ratio_score_midpoint_linear_interpolation():
    midpoint = (ETF_EXPENSE_RATIO_CHEAP_PCT + ETF_EXPENSE_RATIO_EXPENSIVE_PCT) / 2
    expected = round(100.0 - 0.5 * (100.0 - ETF_COST_SCORE_FLOOR), 1)
    assert expense_ratio_score(midpoint) == expected


def test_expense_ratio_score_none_input_returns_none():
    assert expense_ratio_score(None) is None


# ── etf_available ────────────────────────────────────────────────────────────

def test_etf_available_true_when_net_expense_ratio_present():
    assert etf_available({"net_expense_ratio": 0.03}) is True


def test_etf_available_false_when_net_expense_ratio_missing():
    assert etf_available({"category": "Large Blend"}) is False


def test_etf_available_false_when_net_expense_ratio_is_none():
    assert etf_available({"net_expense_ratio": None}) is False


def test_etf_available_false_for_none_input():
    assert etf_available(None) is False


def test_etf_available_false_for_empty_dict():
    assert etf_available({}) is False


# ── etf_composite ────────────────────────────────────────────────────────────

def test_etf_composite_matches_worked_example_65():
    # technical=50, cost=100 -> 50*0.7 + 100*0.3 = 65.0
    assert etf_composite(50.0, 100.0) == 65.0


def test_etf_composite_cheap_fund_at_high_technical_clears_buy():
    # technical=80, cost=100 (cheap fund) -> 86.0, clears COMPOSITE_BUY.
    score = etf_composite(80.0, 100.0)
    assert score == 86.0
    assert score >= COMPOSITE_BUY


def test_etf_composite_expensive_fund_at_same_high_technical_caps_below_buy():
    # The whole point of the cost pillar: an otherwise-identical technical=80
    # read caps BELOW COMPOSITE_BUY when the fund is expensive (cost=25 ==
    # ETF_COST_SCORE_FLOOR) instead of clearing Buy the way the cheap-fund
    # case above does. 80*0.7 + 25*0.3 = 63.5 -> Hold, not Buy.
    score = etf_composite(80.0, 25.0)
    assert score == 63.5
    assert score < COMPOSITE_BUY
    assert score >= COMPOSITE_HOLD


# ── etf_recommendation ───────────────────────────────────────────────────────

def test_etf_recommendation_reuses_equity_label_and_boundaries():
    rec = etf_recommendation(COMPOSITE_BUY + 5)
    assert rec["label"] == "Buy"
    assert "icon" in rec and "color" in rec


def test_etf_recommendation_rationale_is_etf_specific_not_equity_wording():
    rec = etf_recommendation(COMPOSITE_BUY + 5)
    # The equity rationale text talks about "fundamentals" -- must not leak
    # through for a fund with no fundamentals leg at all.
    assert "fundamentals" not in rec["rationale"].lower()
    assert "cost" in rec["rationale"].lower() or "expense" in rec["rationale"].lower() or "trend" in rec["rationale"].lower()


def test_etf_recommendation_hold_rationale_differs_from_equity_hold():
    from stock_analyzer.scoring import recommendation
    equity_rec = recommendation(COMPOSITE_HOLD + 1)
    etf_rec = etf_recommendation(COMPOSITE_HOLD + 1)
    assert etf_rec["label"] == equity_rec["label"] == "Hold"
    assert etf_rec["rationale"] != equity_rec["rationale"]


# ── etf_aum_thin ──────────────────────────────────────────────────────────────

def test_etf_aum_thin_at_floor_boundary_is_not_thin():
    assert etf_aum_thin(ETF_AUM_THIN_FLOOR_USD) is False


def test_etf_aum_thin_below_floor_is_thin():
    assert etf_aum_thin(ETF_AUM_THIN_FLOOR_USD - 1) is True


def test_etf_aum_thin_above_floor_is_not_thin():
    assert etf_aum_thin(ETF_AUM_THIN_FLOOR_USD * 10) is False


def test_etf_aum_thin_none_input_returns_false():
    assert etf_aum_thin(None) is False
