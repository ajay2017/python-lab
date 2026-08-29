"""Tests for `act_today_precedence` — the held-position banner precedence
extracted from app.py's 📈 Analysis "already held, considering an add" block
(Part 2 #3 of the 2026-08-26 app review, commit style matching `1b12779`).

Two things must hold: the stop-breach arithmetic matches the formula the
rest of the app already uses (`portfolio.py`'s "Gap to Stop (%)"), and the
PRECEDENCE (stop-breach > reduce-call > plain-held > not-held) can never be
silently reordered by a future edit.
"""
from stock_analyzer.act_today_precedence import (
    HELD_PLAIN,
    NOT_HELD,
    REDUCE_CALL,
    STOP_BREACH,
    gap_to_stop_pct,
    held_position_state,
    is_stop_breached,
)


# ─── gap_to_stop_pct ──────────────────────────────────────────────────────────

def test_gap_to_stop_pct_normal_case():
    # Price $100, stop $95 -> 5% above the stop.
    assert gap_to_stop_pct(100.0, 95.0) == 5.0


def test_gap_to_stop_pct_at_the_stop_is_zero():
    assert gap_to_stop_pct(100.0, 100.0) == 0.0


def test_gap_to_stop_pct_past_the_stop_is_negative():
    assert gap_to_stop_pct(90.0, 100.0) == -11.1


def test_gap_to_stop_pct_none_on_missing_or_falsy_inputs():
    assert gap_to_stop_pct(None, 95.0) is None
    assert gap_to_stop_pct(100.0, None) is None
    assert gap_to_stop_pct(0.0, 95.0) is None
    assert gap_to_stop_pct(100.0, 0.0) is None


def test_gap_to_stop_pct_rounds_a_sub_threshold_gap_to_zero():
    # A gap under half the rounding unit rounds to 0.0 -- the comment this
    # mirrors calls this deliberate: "a sub-0.05% gap can't split the two
    # surfaces." Confirms rounding happens BEFORE any zero comparison.
    price, stop = 1000.0, 999.6   # raw gap = 0.04%
    assert gap_to_stop_pct(price, stop) == 0.0


# ─── is_stop_breached ─────────────────────────────────────────────────────────

def test_is_stop_breached_true_at_or_past_the_stop():
    assert is_stop_breached(95.0, 100.0) is True   # past
    assert is_stop_breached(100.0, 100.0) is True  # exactly at


def test_is_stop_breached_false_above_the_stop():
    assert is_stop_breached(110.0, 100.0) is False


def test_is_stop_breached_false_when_gap_missing_even_if_arithmetic_would_breach():
    # gap_missing is a DATA problem, not a decision -- must never read as a
    # genuine breach, matching app.py's own `not _gap_missing` guard.
    assert is_stop_breached(90.0, 100.0, gap_missing=True) is False


def test_is_stop_breached_false_on_missing_or_falsy_price_or_stop():
    assert is_stop_breached(None, 100.0) is False
    assert is_stop_breached(90.0, None) is False
    assert is_stop_breached(0.0, 100.0) is False
    assert is_stop_breached(90.0, 0.0) is False


# ─── held_position_state — the precedence decision ───────────────────────────

def test_not_holding_always_wins_regardless_of_other_flags():
    assert held_position_state(
        is_holding=False, stop_breached=True, has_reduce_call=True
    ) == NOT_HELD


def test_stop_breach_outranks_a_reduce_call():
    # The precedence this module exists to protect: when BOTH the mechanical
    # stop and a Brief reduce call are true, stop-breach must win -- the more
    # actionable ("sell at next open") signal, not a softer "under review" one.
    assert held_position_state(
        is_holding=True, stop_breached=True, has_reduce_call=True
    ) == STOP_BREACH


def test_reduce_call_wins_when_stop_not_breached():
    assert held_position_state(
        is_holding=True, stop_breached=False, has_reduce_call=True
    ) == REDUCE_CALL


def test_held_plain_when_neither_fires():
    assert held_position_state(
        is_holding=True, stop_breached=False, has_reduce_call=False
    ) == HELD_PLAIN


def test_stop_breach_wins_even_with_no_reduce_call():
    assert held_position_state(
        is_holding=True, stop_breached=True, has_reduce_call=False
    ) == STOP_BREACH
