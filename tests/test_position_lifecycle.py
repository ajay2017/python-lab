"""Regression tests for stock_analyzer/position_lifecycle.py — the
calm-advisor layer's held-position lifecycle classifier: exit > at_risk >
settling > winning > established, with the critical rule that a missing
age_days must NEVER yield "settling" (no trade-journal history must not
silence management). Pure logic, no I/O. See docs/plans/test-automation.md.
"""
import pytest

from stock_analyzer import position_lifecycle as pl
from stock_analyzer.constants import (
    POSITION_AT_RISK_GAP_PCT,
    POSITION_SETTLING_DAYS,
    POSITION_WINNING_PNL_PCT,
)


# ── classify_position_state — precedence order ─────────────────────────────

def test_exit_on_stop_breach_regardless_of_age():
    # A freshly-opened position (age 1 day, would otherwise be "settling")
    # that's already breaching its stop must be "exit", not "settling".
    assert pl.classify_position_state(age_days=1, pnl_pct=-5.0, gap_to_stop_pct=0.0) == "exit"


def test_exit_on_negative_gap():
    assert pl.classify_position_state(age_days=100, pnl_pct=5.0, gap_to_stop_pct=-1.0) == "exit"


def test_exit_on_explicit_sell_signal_even_with_healthy_gap():
    assert pl.classify_position_state(
        age_days=100, pnl_pct=5.0, gap_to_stop_pct=20.0, has_exit_signal=True
    ) == "exit"


def test_exit_beats_at_risk_when_both_conditions_true():
    assert pl.classify_position_state(age_days=100, pnl_pct=5.0, gap_to_stop_pct=0.0) == "exit"


def test_at_risk_within_critical_gap_band():
    assert pl.classify_position_state(
        age_days=100, pnl_pct=5.0, gap_to_stop_pct=POSITION_AT_RISK_GAP_PCT
    ) == "at_risk"


def test_at_risk_boundary_just_above_band_is_not_at_risk():
    assert pl.classify_position_state(
        age_days=100, pnl_pct=5.0, gap_to_stop_pct=POSITION_AT_RISK_GAP_PCT + 0.01
    ) == "established"


def test_at_risk_beats_settling_for_a_fresh_position():
    assert pl.classify_position_state(
        age_days=1, pnl_pct=5.0, gap_to_stop_pct=POSITION_AT_RISK_GAP_PCT
    ) == "at_risk"


def test_settling_when_younger_than_threshold():
    assert pl.classify_position_state(
        age_days=POSITION_SETTLING_DAYS - 1, pnl_pct=0.0, gap_to_stop_pct=20.0
    ) == "settling"


def test_settling_boundary_at_threshold_is_not_settling():
    assert pl.classify_position_state(
        age_days=POSITION_SETTLING_DAYS, pnl_pct=0.0, gap_to_stop_pct=20.0
    ) == "established"


def test_settling_beats_winning_for_a_fresh_big_gainer():
    assert pl.classify_position_state(
        age_days=1, pnl_pct=POSITION_WINNING_PNL_PCT + 5.0, gap_to_stop_pct=20.0
    ) == "settling"


def test_age_none_never_yields_settling_even_when_otherwise_fresh_shaped():
    # Critical rule: missing trade-journal history must not silence
    # management -- age_days=None should fall straight through to
    # winning/established, never "settling".
    assert pl.classify_position_state(age_days=None, pnl_pct=0.0, gap_to_stop_pct=20.0) == "established"
    assert pl.classify_position_state(age_days=None, pnl_pct=POSITION_WINNING_PNL_PCT, gap_to_stop_pct=20.0) == "winning"


def test_winning_at_threshold():
    assert pl.classify_position_state(
        age_days=100, pnl_pct=POSITION_WINNING_PNL_PCT, gap_to_stop_pct=20.0
    ) == "winning"


def test_winning_boundary_just_below_threshold_is_not_winning():
    assert pl.classify_position_state(
        age_days=100, pnl_pct=POSITION_WINNING_PNL_PCT - 0.01, gap_to_stop_pct=20.0
    ) == "established"


def test_established_default_state():
    assert pl.classify_position_state(age_days=100, pnl_pct=0.0, gap_to_stop_pct=20.0) == "established"


def test_established_when_gap_is_none_and_not_winning():
    assert pl.classify_position_state(age_days=100, pnl_pct=0.0, gap_to_stop_pct=None) == "established"


def test_winning_when_gap_is_none():
    assert pl.classify_position_state(
        age_days=100, pnl_pct=POSITION_WINNING_PNL_PCT, gap_to_stop_pct=None
    ) == "winning"


def test_settling_when_gap_is_none():
    assert pl.classify_position_state(age_days=1, pnl_pct=0.0, gap_to_stop_pct=None) == "settling"


def test_pnl_none_does_not_crash_and_falls_to_established():
    assert pl.classify_position_state(age_days=100, pnl_pct=None, gap_to_stop_pct=20.0) == "established"


def test_has_exit_signal_false_by_default():
    assert pl.classify_position_state(age_days=100, pnl_pct=0.0, gap_to_stop_pct=20.0) == "established"


# ── lifecycle_badge ─────────────────────────────────────────────────────────

def test_lifecycle_badge_settling():
    badge = pl.lifecycle_badge("settling")
    assert badge["emoji"] == "🌱"
    assert badge["label"] == "Settling"


def test_lifecycle_badge_winning():
    assert pl.lifecycle_badge("winning")["label"] == "Winning"


def test_lifecycle_badge_at_risk():
    assert pl.lifecycle_badge("at_risk")["label"] == "At Risk"


def test_lifecycle_badge_exit():
    assert pl.lifecycle_badge("exit")["label"] == "Exit"


def test_lifecycle_badge_established_is_unbadged():
    assert pl.lifecycle_badge("established") is None


def test_lifecycle_badge_unknown_state_is_none():
    assert pl.lifecycle_badge("nonexistent") is None


@pytest.mark.parametrize("state", ["settling", "winning", "at_risk", "exit"])
def test_lifecycle_badge_all_have_required_keys(state):
    badge = pl.lifecycle_badge(state)
    assert set(badge.keys()) == {"emoji", "label", "color", "tip"}
