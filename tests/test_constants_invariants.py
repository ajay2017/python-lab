"""Ordering/consistency invariants across stock_analyzer/constants.py.

These do NOT assert literal values (a policy change is allowed to move a
number and these tests should not need touching for that) — they assert
relationships between constants that the code silently assumes. A fat-finger
edit that breaks one of these (e.g. setting a WATCH floor above its TRIM floor)
would not raise an ImportError anywhere; it would just quietly make a tier
unreachable or a gate fire backwards.
"""
from stock_analyzer import constants as c


def test_composite_score_boundaries_are_strictly_ordered():
    assert c.COMPOSITE_SELL < c.COMPOSITE_HOLD < c.COMPOSITE_BUY < c.COMPOSITE_STRONG_BUY


def test_composite_buy_flat_day_is_at_least_as_strict_as_buy():
    # The flat-day bar must be a stricter (or equal) gate than the normal Buy floor.
    assert c.COMPOSITE_BUY_FLAT_DAY >= c.COMPOSITE_BUY


def test_sector_elevated_below_sector_ceiling():
    assert c.SECTOR_ELEVATED < c.SECTOR_CEILING


def test_single_name_ceiling_below_its_trim_trigger():
    assert c.SINGLE_NAME_CEILING < c.SINGLE_NAME_TRIM_TRIGGER


def test_deterioration_tiers_strictly_ordered():
    assert c.DETERIORATION_WATCH_DD_PCT < c.DETERIORATION_TRIM_DD_PCT < c.DETERIORATION_EXIT_DD_PCT


def test_deterioration_atr_floors_below_their_ceilings():
    assert c.DETERIORATION_TRIM_DD_PCT <= c.DETERIORATION_TRIM_DD_CEILING
    assert c.DETERIORATION_EXIT_DD_PCT <= c.DETERIORATION_EXIT_DD_CEILING
    assert c.DETERIORATION_TRIM_DD_CEILING < c.DETERIORATION_EXIT_DD_CEILING


def test_risk_on_vix_below_risk_off_vix():
    assert c.RISK_ON_VIX_LEVEL < c.RISK_OFF_VIX_LEVEL


def test_cpi_regime_ladder_strictly_ordered():
    assert c.REGIME_CPI_CONTROLLED_MAX < c.REGIME_CPI_ELEVATED_MIN < c.REGIME_CPI_HOT_MIN


def test_news_sentiment_cutoffs_strictly_ordered():
    assert (
        c.NEWS_SENTIMENT_CRITICAL
        < c.NEWS_SENTIMENT_NEGATIVE
        < c.NEWS_SENTIMENT_WARN
        < c.NEWS_SENTIMENT_POSITIVE
    )


def test_analyst_consensus_fractions_are_valid_fractions():
    for frac in (
        c.ANALYST_CONSENSUS_STRONG_BUY_FRAC,
        c.ANALYST_CONSENSUS_BUY_FRAC,
        c.ANALYST_CONSENSUS_SELL_FRAC,
    ):
        assert 0.0 <= frac <= 1.0


def test_portfolio_beta_bands_strictly_ordered():
    assert c.PORTFOLIO_BETA_TARGET < c.PORTFOLIO_BETA_ELEVATED < c.PORTFOLIO_BETA_CEILING


def test_fmp_soft_cap_below_hard_cap():
    assert c.FMP_DAILY_SOFT_CAP < c.FMP_DAILY_CALL_CAP
