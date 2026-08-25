"""Regression tests for stock_analyzer/concentration.py — entry-time sizing gates.

gating_denominator() implements the "tighter-of-both" margin/cash policy
(docs/plans/account-baseline.md); assess_add_concentration() is what warns the
Trade Journal a BUY would breach the single-name/sector ceilings. Both are pure
functions with no I/O, so exact boundary behaviour is pinned here rather than
only being exercised implicitly through the UI.
"""
from stock_analyzer.concentration import assess_add_concentration, gating_denominator, high_beta_share
from stock_analyzer.constants import (
    SECTOR_CEILING, SECTOR_ELEVATED, SINGLE_NAME_CEILING, NET_CAPITAL_POSITION_CAP_PCT,
)


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


# ── assess_add_concentration — capital-basis (F-255, additive/optional) ───────

def test_assess_add_concentration_omitting_capital_kwargs_leaves_shape_unchanged():
    """Default-off: capital_breach always False, post_name_capital_pct always
    None when net_capital/capital_ceiling are omitted -- and a would-be-clean
    add (no gross-basis breach) still returns None, same as before F-255."""
    result = _assess()
    assert result is None

    breach = _assess(existing_name_mv=15_000.0, add_shares=100, price=100.0)
    assert breach is not None
    assert breach["capital_breach"] is False
    assert breach["post_name_capital_pct"] is None


def test_assess_add_concentration_flags_capital_breach():
    """A buy that clears the gross-book ceiling entirely can still breach the
    SEPARATE net-capital cap once net_capital/capital_ceiling are supplied
    (ALB/OXY-shaped: small net capital relative to gross book)."""
    result = assess_add_concentration(
        ticker="OXY",
        add_shares=59,
        price=60.10,
        existing_name_mv=0.0,
        sector_mv=0.0,
        portfolio_value=24_503.0,
        single_ceiling=SINGLE_NAME_CEILING,
        sector_ceiling=SECTOR_CEILING,
        sector_elevated=SECTOR_ELEVATED,
        net_capital=7_802.0,
        capital_ceiling=NET_CAPITAL_POSITION_CAP_PCT,
    )
    assert result is not None
    assert result["capital_breach"] is True
    assert result["post_name_capital_pct"] is not None
    assert result["post_name_capital_pct"] >= NET_CAPITAL_POSITION_CAP_PCT
    # Gross-book basis alone would not have flagged this add.
    assert result["name_breach"] is False


def test_assess_add_concentration_capital_kwargs_partial_stays_inert():
    """Supplying only ONE of net_capital/capital_ceiling must not compute a
    capital reading -- both are required together."""
    only_net_capital = assess_add_concentration(
        ticker="AAPL", add_shares=100, price=100.0, existing_name_mv=15_000.0,
        sector_mv=0.0, portfolio_value=100_000.0,
        single_ceiling=SINGLE_NAME_CEILING, sector_ceiling=SECTOR_CEILING,
        sector_elevated=SECTOR_ELEVATED, net_capital=7_802.0, capital_ceiling=None,
    )
    assert only_net_capital["capital_breach"] is False
    assert only_net_capital["post_name_capital_pct"] is None


# ── high_beta_share ───────────────────────────────────────────────────────────

def test_high_beta_share_no_positions_returns_zero():
    assert high_beta_share([], beta_threshold=1.3) == 0.0


def test_high_beta_share_all_unknown_beta_returns_zero():
    assert high_beta_share([(50.0, None), (50.0, None)], beta_threshold=1.3) == 0.0


def test_high_beta_share_computes_share_of_known_weight_only():
    # 30% weight at high beta, 20% at low beta, 50% unknown -- unknown is
    # excluded from BOTH numerator and denominator, so the share is computed
    # over the 50% with known beta, not the full 100%.
    positions = [(30.0, 1.5), (20.0, 0.8), (50.0, None)]
    assert high_beta_share(positions, beta_threshold=1.3) == 60.0  # 30 / (30+20) * 100


def test_high_beta_share_boundary_is_inclusive():
    assert high_beta_share([(100.0, 1.3)], beta_threshold=1.3) == 100.0
    assert high_beta_share([(100.0, 1.29)], beta_threshold=1.3) == 0.0


def test_high_beta_share_ignores_rows_with_unknown_weight():
    positions = [(None, 2.0), (40.0, 1.5)]
    assert high_beta_share(positions, beta_threshold=1.3) == 100.0
