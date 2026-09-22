"""Tests for stock_analyzer/trade_economics.py — trade-level dollar economics.

Pure display arithmetic (never a sizing/gate decision), so no _GATE_FILES
touch and no Opus review required. Covers the four explicit states, NaN
safety (the `x != x` idiom, not a bare `is None`/truthy check — see memory
`project_engine_track_record`, `feedback_none_sentinel_meets_pandas`), and
the hand-checked 2.2 R:R -> 31.25% breakeven arithmetic the task spec pins.
"""
import math

import pytest

from stock_analyzer.trade_economics import breakeven_hit_rate, entry_tier, trade_economics

pytestmark = pytest.mark.fast

NAN = float("nan")


# ── breakeven_hit_rate ───────────────────────────────────────────────────────

def test_breakeven_hit_rate_2_2_rr_is_31_25_pct():
    be = breakeven_hit_rate(2.2)
    assert be is not None
    assert math.isclose(be * 100.0, 31.25, rel_tol=1e-9)


def test_breakeven_hit_rate_1_to_1_is_50_pct():
    be = breakeven_hit_rate(1.0)
    assert math.isclose(be, 0.5)


def test_breakeven_hit_rate_none_returns_none():
    assert breakeven_hit_rate(None) is None


def test_breakeven_hit_rate_nan_returns_none():
    assert breakeven_hit_rate(NAN) is None


def test_breakeven_hit_rate_zero_returns_none():
    assert breakeven_hit_rate(0) is None


def test_breakeven_hit_rate_negative_returns_none():
    assert breakeven_hit_rate(-1.5) is None


def test_breakeven_hit_rate_string_garbage_returns_none():
    assert breakeven_hit_rate("not a number") is None


# ── trade_economics — state: unpriced ───────────────────────────────────────

def test_unpriced_when_price_none():
    out = trade_economics(None, 90.0, 2.0)
    assert out["state"] == "unpriced"
    assert out["risk_per_share"] is None
    assert out["risk_pct"] is None
    assert out["gain_per_share"] is None
    assert out["target"] is None
    assert out["breakeven_pct"] is None


def test_unpriced_when_stop_none():
    out = trade_economics(100.0, None, 2.0)
    assert out["state"] == "unpriced"


def test_unpriced_when_price_nan():
    out = trade_economics(NAN, 90.0, 2.0)
    assert out["state"] == "unpriced"


def test_unpriced_when_stop_nan():
    out = trade_economics(100.0, NAN, 2.0)
    assert out["state"] == "unpriced"


def test_unpriced_when_price_zero():
    out = trade_economics(0.0, 90.0, 2.0)
    assert out["state"] == "unpriced"


def test_unpriced_when_price_negative():
    out = trade_economics(-5.0, 90.0, 2.0)
    assert out["state"] == "unpriced"


def test_unpriced_when_stop_zero_or_negative():
    assert trade_economics(100.0, 0.0, 2.0)["state"] == "unpriced"
    assert trade_economics(100.0, -1.0, 2.0)["state"] == "unpriced"


# ── trade_economics — state: no_stop_room ───────────────────────────────────

def test_no_stop_room_when_price_equals_stop():
    out = trade_economics(100.0, 100.0, 2.0)
    assert out["state"] == "no_stop_room"
    assert out["risk_per_share"] is None


def test_no_stop_room_when_price_below_stop():
    out = trade_economics(90.0, 100.0, 2.0)
    assert out["state"] == "no_stop_room"


# ── trade_economics — state: no_rr (dollar-risk half populated) ────────────

def test_no_rr_when_rr_is_none_still_populates_risk_half():
    out = trade_economics(100.0, 90.0, None)
    assert out["state"] == "no_rr"
    assert out["risk_per_share"] == pytest.approx(10.0)
    assert out["risk_pct"] == pytest.approx(10.0)
    assert out["gain_per_share"] is None
    assert out["target"] is None
    assert out["breakeven_pct"] is None


def test_no_rr_when_rr_is_zero():
    out = trade_economics(100.0, 90.0, 0.0)
    assert out["state"] == "no_rr"
    assert out["risk_per_share"] == pytest.approx(10.0)


def test_no_rr_when_rr_is_negative():
    out = trade_economics(100.0, 90.0, -1.0)
    assert out["state"] == "no_rr"


def test_no_rr_when_rr_is_nan():
    out = trade_economics(100.0, 90.0, NAN)
    assert out["state"] == "no_rr"


# ── trade_economics — state: ok (hand-checked arithmetic) ───────────────────

def test_ok_state_hand_checked_2_2_rr():
    """price=202.20, stop=193.32 -> risk_per_share=8.88, risk_pct~4.39%;
    rr=2.2 -> target=221.7336, gain_per_share=19.5336, breakeven~31.25%.
    Matches the task spec's own worked example (risk $8.88/sh (-4.4%),
    target $221.74 (+$19.54/sh), needs >31% win rate to break even)."""
    out = trade_economics(202.20, 193.32, 2.2)
    assert out["state"] == "ok"
    assert out["risk_per_share"] == pytest.approx(8.88, abs=1e-6)
    assert out["risk_pct"] == pytest.approx(4.39, abs=0.01)
    assert out["target"] == pytest.approx(221.74, abs=0.01)
    assert out["gain_per_share"] == pytest.approx(19.53, abs=0.01)
    assert out["breakeven_pct"] == pytest.approx(31.25, abs=1e-6)


def test_ok_state_simple_round_numbers():
    out = trade_economics(100.0, 90.0, 2.0)
    assert out["state"] == "ok"
    assert out["risk_per_share"] == pytest.approx(10.0)
    assert out["risk_pct"] == pytest.approx(10.0)
    assert out["target"] == pytest.approx(120.0)
    assert out["gain_per_share"] == pytest.approx(20.0)
    assert out["breakeven_pct"] == pytest.approx(100.0 / 3.0, abs=1e-6)


# ── entry_tier ───────────────────────────────────────────────────────────────

def test_entry_tier_clean_when_none():
    assert entry_tier(None) == "clean"


def test_entry_tier_clean_when_empty_string():
    assert entry_tier("") == "clean"


def test_entry_tier_clean_when_not_a_string():
    assert entry_tier(42) == "clean"
    assert entry_tier([]) == "clean"


def test_entry_tier_caution_when_non_empty_string():
    assert entry_tier("chart looks broken") == "caution"
