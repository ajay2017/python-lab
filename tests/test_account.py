"""
Tests for stock_analyzer/account.py's annualization_caveat() — added 2026-07-28
alongside the Benchmark Mirror total-account-value fix, to explain when an
annualized money-weighted return (short window and/or a levered account) can
look far more dramatic than the underlying period return warrants.
"""
from stock_analyzer.account import annualization_caveat, _ANNUALIZE_CAVEAT_MAX_DAYS


def test_none_days_returns_none():
    assert annualization_caveat(None) is None


def test_long_unlevered_window_returns_none():
    assert annualization_caveat(_ANNUALIZE_CAVEAT_MAX_DAYS + 1, is_levered=False) is None


def test_exactly_at_max_days_boundary_returns_none():
    # "< max_days" is the short-window condition — the boundary itself is not short
    assert annualization_caveat(_ANNUALIZE_CAVEAT_MAX_DAYS, is_levered=False) is None


def test_short_window_unlevered_mentions_days():
    msg = annualization_caveat(_ANNUALIZE_CAVEAT_MAX_DAYS - 1, is_levered=False)
    assert msg is not None
    assert str(_ANNUALIZE_CAVEAT_MAX_DAYS - 1) in msg


def test_short_window_levered_mentions_both_leverage_and_days():
    msg = annualization_caveat(33, is_levered=True)
    assert msg is not None
    assert "33" in msg
    assert "leverag" in msg.lower()


def test_long_window_levered_mentions_leverage_only():
    msg = annualization_caveat(_ANNUALIZE_CAVEAT_MAX_DAYS + 10, is_levered=True)
    assert msg is not None
    assert "margin" in msg.lower() or "leverag" in msg.lower()


def test_short_unlevered_and_short_levered_messages_differ():
    unlevered = annualization_caveat(30, is_levered=False)
    levered = annualization_caveat(30, is_levered=True)
    assert unlevered != levered
