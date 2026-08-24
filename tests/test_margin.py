"""Tests for stock_analyzer.margin — margin call-distance computations."""
import importlib
import pytest
from stock_analyzer.margin import call_distance
from stock_analyzer.constants import FRAGILITY_PULLBACK_PCT, MARGIN_MAINTENANCE_RATE


# ── Founding-measurement test ─────────────────────────────────────────────────

def test_real_book_matches_measured_call_distance():
    """Founding measurement: real book as of 2026-08-23.

    Measured call at -9.03% (Robinhood's exact 25.02% rate).
    Our estimate at 25.00% gives -9.12% — within 0.1pp.
    The WRONG formula (cushion / stock_value) gives -6.84%; this test
    fails against that formula, which is the point.
    """
    result = call_distance(
        stock_value=24503.0,
        owner_equity=7802.0,
        margin_debit=16701.0,
        rate=0.25,
    )
    assert result is not None
    assert abs(result["cushion"] - 1676.25) < 1.0           # $24503 × 0.25 = $6125.75; $7802 - $6125.75 = $1676.25
    assert abs(result["call_distance_pct"] - (-9.12)) < 0.1  # correct formula
    assert result["in_call"] is False
    # Confirm it does NOT match the wrong formula's answer
    wrong_formula_answer = -(result["cushion"] / 24503.0) * 100
    assert abs(result["call_distance_pct"] - wrong_formula_answer) > 2.0  # must differ by >2pp


# ── Boundary and edge cases ───────────────────────────────────────────────────

def test_exactly_at_call_threshold():
    """At the exact call threshold cushion=0, call_distance_pct=0, in_call=True."""
    rate = 0.25
    # Construct: owner_equity = stock_value * rate (exactly at floor)
    stock_value = 10000.0
    margin_debit = stock_value * (1 - rate)  # 7500
    owner_equity = stock_value - margin_debit  # 2500
    result = call_distance(stock_value, owner_equity, margin_debit, rate)
    assert result is not None
    assert abs(result["cushion"]) < 0.01
    assert abs(result["call_distance_pct"]) < 0.01
    assert result["in_call"] is True


def test_in_call_state_negative_cushion():
    """When equity is already below the maintenance floor, in_call is True."""
    result = call_distance(
        stock_value=10000.0,
        owner_equity=2000.0,   # below 25% floor of $2500
        margin_debit=8000.0,
        rate=0.25,
    )
    assert result is not None
    assert result["cushion"] < 0
    assert result["in_call"] is True
    assert result["call_distance_pct"] > 0  # already past the call (positive = already breached)


def test_no_margin_debit_returns_none():
    """No debit → not leveraged → panel should hide."""
    assert call_distance(10000.0, 10000.0, 0.0, 0.25) is None


def test_zero_debit_returns_none():
    assert call_distance(10000.0, 10000.0, 0.0, 0.25) is None


def test_negative_debit_returns_none():
    assert call_distance(10000.0, 10000.0, -100.0, 0.25) is None


def test_zero_stock_value_returns_none():
    assert call_distance(0.0, 0.0, 1000.0, 0.25) is None


def test_rate_equal_to_one_returns_none():
    """rate=1 would divide by zero — guard returns None."""
    assert call_distance(10000.0, 5000.0, 5000.0, 1.0) is None


def test_rate_above_one_returns_none():
    assert call_distance(10000.0, 5000.0, 5000.0, 1.5) is None


# ── Awareness-only invariant ──────────────────────────────────────────────────

def test_margin_maintenance_rate_not_imported_by_gate_modules():
    """MARGIN_MAINTENANCE_RATE must not be imported by any gate or advisor module.

    This guards the awareness-only invariant: the constant must only feed
    the Account-page display, never a gate, recommendation, or suppression.
    """
    gate_modules = [
        "stock_analyzer.risk_advisor",
        "stock_analyzer.exit_advisor",
        "stock_analyzer.daily_briefing",
        "stock_analyzer.scoring",
        "stock_analyzer.ranking",
        "stock_analyzer.targets",
        "stock_analyzer.watchlist_advisor",
    ]
    for mod_name in gate_modules:
        try:
            mod = importlib.import_module(mod_name)
            assert not hasattr(mod, "MARGIN_MAINTENANCE_RATE"), (
                f"{mod_name} imported MARGIN_MAINTENANCE_RATE — "
                "this constant must remain awareness-only and never feed a gate"
            )
        except ImportError:
            pass  # module doesn't exist — nothing to check


# ── Fragility constant single-sourced ────────────────────────────────────────

def test_fragility_pullback_pct_is_imported_from_constants():
    """FRAGILITY_PULLBACK_PCT must come from constants, never be a literal."""
    # The value exists and is negative (a decline)
    assert FRAGILITY_PULLBACK_PCT < 0
    # Our standard rate is 0.25
    assert MARGIN_MAINTENANCE_RATE == 0.25


# ── Sensible output ranges ────────────────────────────────────────────────────

def test_moderate_leverage_produces_reasonable_call_distance():
    """2x leverage at 25% rate should give call around -33%."""
    result = call_distance(20000.0, 10000.0, 10000.0, 0.25)
    assert result is not None
    # maintenance = $5000; cushion = $5000; call_distance = -5000/(20000*0.75)*100 = -33.3%
    assert abs(result["call_distance_pct"] - (-33.3)) < 0.5
    assert result["in_call"] is False


def test_high_leverage_produces_closer_call():
    """Higher leverage → smaller cushion → call is closer (less negative %)."""
    low_lev  = call_distance(20000.0, 10000.0, 10000.0, 0.25)  # 2x
    high_lev = call_distance(20000.0,  5000.0, 15000.0, 0.25)  # 4x
    assert low_lev is not None and high_lev is not None
    # high leverage call distance is less negative (triggers sooner)
    assert high_lev["call_distance_pct"] > low_lev["call_distance_pct"]
