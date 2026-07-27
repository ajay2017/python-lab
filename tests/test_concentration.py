"""Regression tests for stock_analyzer/concentration.py — entry-time sizing gates.

gating_denominator() implements the "tighter-of-both" margin/cash policy
(docs/plans/account-baseline.md); assess_add_concentration() is what warns the
Trade Journal a BUY would breach the single-name/sector ceilings. Both are pure
functions with no I/O, so exact boundary behaviour is pinned here rather than
only being exercised implicitly through the UI.
"""
from stock_analyzer.concentration import assess_add_concentration, gating_denominator
from stock_analyzer.constants import SECTOR_CEILING, SECTOR_ELEVATED, SINGLE_NAME_CEILING


# ── gating_denominator ───────────────────────────────────────────────────────

def test_gating_denominator_unknown_cash_falls_back_to_equity():
    denom, basis = gating_denominator(100_000.0, None)
    assert (denom, basis) == (100_000.0, "equity")


def test_gating_denominator_stale_cash_falls_back_to_equity():
    denom, basis = gating_denominator(100_000.0, 150_000.0, stale=True)
    assert (denom, basis) == (100_000.0, "equity")


def test_gating_denominator_cash_on_hand_never_loosens():
    # account_total > equity (net cash) must NOT relax the gate below equity.
    denom, basis = gating_denominator(100_000.0, 130_000.0)
    assert (denom, basis) == (100_000.0, "equity")


def test_gating_denominator_margin_tightens():
    # account_total < equity (margin debit) must gate on the SMALLER figure.
    denom, basis = gating_denominator(100_000.0, 80_000.0)
    assert (denom, basis) == (80_000.0, "account")


def test_gating_denominator_wiped_capital_floors_over_levered():
    denom, basis = gating_denominator(100_000.0, -5_000.0)
    assert basis == "over-levered"
    assert denom == max(100_000.0 * 0.01, 1.0)


# ── assess_add_concentration ─────────────────────────────────────────────────

def _assess(
    *,
    add_shares: float = 10,
    price: float = 100.0,
    existing_name_mv: float = 0.0,
    sector_mv: float = 0.0,
    portfolio_value: float = 100_000.0,
):
    return assess_add_concentration(
        ticker="AAPL",
        add_shares=add_shares,
        price=price,
        existing_name_mv=existing_name_mv,
        sector_mv=sector_mv,
        portfolio_value=portfolio_value,
        single_ceiling=SINGLE_NAME_CEILING,
        sector_ceiling=SECTOR_CEILING,
        sector_elevated=SECTOR_ELEVATED,
    )


def test_assess_add_concentration_no_breach_returns_none():
    # $1,000 add on a $100k book is nowhere near any ceiling.
    assert _assess() is None


def test_assess_add_concentration_zero_or_negative_inputs_return_none():
    assert _assess(add_shares=0) is None
    assert _assess(price=0) is None
    assert _assess(portfolio_value=0) is None


def test_assess_add_concentration_flags_single_name_breach():
    # Existing position already at the ceiling; any add pushes it over.
    result = _assess(existing_name_mv=15_000.0, add_shares=100, price=100.0)
    assert result is not None
    assert result["name_breach"] is True
    assert result["post_name_wt"] >= SINGLE_NAME_CEILING
    assert result["suggested_trim_shares"] > 0


def test_assess_add_concentration_trim_brings_weight_to_ceiling():
    result = _assess(existing_name_mv=15_000.0, add_shares=100, price=100.0)
    assert result is not None
    trim = result["suggested_trim_shares"]
    remaining_name_mv = (15_000.0 + 100 * 100.0) - trim * 100.0
    remaining_total = (100_000.0 + 100 * 100.0) - trim * 100.0
    resulting_wt = remaining_name_mv / remaining_total * 100.0
    assert resulting_wt <= SINGLE_NAME_CEILING + 0.5  # rounding-up tolerance


def test_assess_add_concentration_flags_sector_hard_breach_without_name_breach():
    result = _assess(sector_mv=34_000.0, add_shares=20, price=100.0)
    assert result is not None
    assert result["sector_hard"] is True
    assert result["name_breach"] is False


def test_assess_add_concentration_flags_sector_elevated_only():
    result = _assess(sector_mv=24_000.0, add_shares=20, price=100.0)
    assert result is not None
    assert result["sector_elevated"] is True
    assert result["sector_hard"] is False
