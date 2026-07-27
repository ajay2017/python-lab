"""Regression tests for stock_analyzer/scoring.py — the composite label gate.

recommendation() is what turns a raw composite number into the Buy/Hold/Sell
label every other gate (Grow Today, Brief verdict, add-to-winner) keys off.
A past bug (72/58 hardcoded here vs 75/65 in constants.py) meant a stock
labelled "Buy" on Analysis was silently filtered out of Grow Today as "Composite
Says No (Hold)" — these tests pin the label at and around every constants.py
boundary so that class of drift fails loudly instead of shipping quietly.
"""
from stock_analyzer import scoring
from stock_analyzer.constants import (
    COMPOSITE_STRONG_BUY,
    COMPOSITE_BUY,
    COMPOSITE_HOLD,
    COMPOSITE_SELL,
    COMPOSITE_WEIGHTS,
)


def test_composite_weights_sum_to_one():
    assert round(sum(COMPOSITE_WEIGHTS.values()), 6) == 1.0


def test_recommendation_at_strong_buy_boundary():
    assert scoring.recommendation(COMPOSITE_STRONG_BUY)["label"] == "Strong Buy"
    assert scoring.recommendation(COMPOSITE_STRONG_BUY - 0.1)["label"] != "Strong Buy"


def test_recommendation_at_buy_boundary():
    assert scoring.recommendation(COMPOSITE_BUY)["label"] == "Buy"
    assert scoring.recommendation(COMPOSITE_BUY - 0.1)["label"] != "Buy"


def test_recommendation_at_hold_boundary():
    assert scoring.recommendation(COMPOSITE_HOLD)["label"] == "Hold"
    assert scoring.recommendation(COMPOSITE_HOLD - 0.1)["label"] != "Hold"


def test_recommendation_at_sell_boundary():
    assert scoring.recommendation(COMPOSITE_SELL)["label"] == "Sell"
    assert scoring.recommendation(COMPOSITE_SELL - 0.1)["label"] == "Strong Sell"


def test_recommendation_labels_are_monotonic_with_score():
    # Higher score must never map to a "worse" label than a lower score.
    order = ["Strong Sell", "Sell", "Hold", "Buy", "Strong Buy"]
    scores = [0, 20, COMPOSITE_SELL, COMPOSITE_HOLD, COMPOSITE_BUY, COMPOSITE_STRONG_BUY, 100]
    ranks = [order.index(scoring.recommendation(s)["label"]) for s in scores]
    assert ranks == sorted(ranks)


def test_combined_score_uses_constants_weights():
    # All-100 input on every pillar must return 100 regardless of weight split,
    # since the weights must sum to 1.0 (guards against a weights edit that
    # breaks the sum-to-1 invariant silently changing the scale of every score).
    assert scoring.combined_score(100, 100, 100, 100) == 100.0


def test_combined_score_matches_manual_weighted_sum():
    technical, business_quality, valuation, sentiment = 80, 60, 40, 20
    expected = round(
        technical * COMPOSITE_WEIGHTS["technical"]
        + business_quality * COMPOSITE_WEIGHTS["business_quality"]
        + valuation * COMPOSITE_WEIGHTS["valuation"]
        + sentiment * COMPOSITE_WEIGHTS["sentiment"],
        1,
    )
    assert scoring.combined_score(technical, business_quality, valuation, sentiment) == expected
