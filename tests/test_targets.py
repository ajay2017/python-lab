"""Tests for stock_analyzer/targets.py — price target / entry-zone / support-
resistance / risk-reward math. Previously zero test coverage despite
compute_price_targets carrying 13 magic-number ratios recently promoted to
named constants in constants.py (the audit's specific concern) and a
documented prior production crash (a `max()`-on-empty-sequence ValueError,
now guarded by a `default=`). Pure pandas math, no I/O.
"""
import math

import pandas as pd

from stock_analyzer import targets
from stock_analyzer.constants import (
    TARGETS_ENTRY_ZONE_LOW_ATR_FRAC,
    TARGETS_ENTRY_ZONE_HIGH_ATR_FRAC,
    TARGETS_MODEST_UPSIDE_MULT,
    TARGETS_BEAR_ATR_MULT,
    TARGETS_BEAR_SUPPORT_CUSHION_MULT,
    TARGETS_BEAR_52W_LOW_CUSHION_MULT,
)


# ─── builders ───────────────────────────────────────────────────────────────

def _mk_ohlc(closes, highs=None, lows=None):
    n = len(closes)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    if highs is None:
        highs = [c + 1 for c in closes]
    if lows is None:
        lows = [c - 1 for c in closes]
    return pd.DataFrame({"High": highs, "Low": lows, "Close": closes}, index=idx)


def _flat_df(n=30, price=100.0):
    return _mk_ohlc([price] * n)


# ─── entry_zone ──────────────────────────────────────────────────────────────

def test_entry_zone_formula():
    low, high = targets.entry_zone(100.0, atr_val=10.0)
    assert low == round(100.0 - TARGETS_ENTRY_ZONE_LOW_ATR_FRAC * 10.0, 2)
    assert high == round(100.0 + TARGETS_ENTRY_ZONE_HIGH_ATR_FRAC * 10.0, 2)
    assert low == 97.5
    assert high == 101.0


# ─── support_resistance ──────────────────────────────────────────────────────

def test_support_resistance_captures_clear_peak_and_trough():
    n = 15
    high = [100.0 + (i % 2) for i in range(n)]
    high[7] = 200.0  # a single, unambiguous local high
    low = [50.0 - (i % 2) for i in range(n)]
    low[7] = 10.0  # a single, unambiguous local low
    closes = [(h + l) / 2 for h, l in zip(high, low)]
    df = _mk_ohlc(closes, highs=high, lows=low)

    result = targets.support_resistance(df, lookback=n)
    assert result["nearest_resistance"] == 200.0
    assert 200.0 in result["resistances"]
    assert result["nearest_support"] == 10.0
    assert 10.0 in result["supports"]


def test_support_resistance_nearest_resistance_is_closest_not_most_extreme():
    """2026-08-04 audit finding: nearest_resistance used to always be the
    single most-extreme local high regardless of distance to price. With
    current_price supplied, "nearest" must mean nearest by distance -- a
    closer, less-extreme level beats a farther, more-extreme one. A tiny
    monotonic-slope baseline (no ties) plus 2 isolated spikes >= window(5)
    apart, away from the edges, so exactly those 2 points register as local
    highs -- no spurious ties from a flat/oscillating baseline."""
    n = 22
    high = [90.0 + 0.001 * i for i in range(n)]
    high[8], high[15] = 200.0, 105.0  # far-above-price, close-above-price
    low = [h - 30 for h in high]
    df = _mk_ohlc([(h + l) / 2 for h, l in zip(high, low)], highs=high, lows=low)

    result = targets.support_resistance(df, lookback=n, current_price=100.0)
    assert result["resistances"][0] == 200.0        # strongest-3 unaffected by price
    assert result["nearest_resistance"] == 105.0     # closest ABOVE price, not the most extreme


def test_support_resistance_nearest_support_is_closest_not_most_extreme():
    n = 22
    low = [150.0 - 0.001 * i for i in range(n)]  # baseline well ABOVE both dips below
    low[8], low[15] = 10.0, 92.0  # far-below-price, close-below-price
    high = [l + 30 for l in low]
    df = _mk_ohlc([(h + l) / 2 for h, l in zip(high, low)], highs=high, lows=low)

    result = targets.support_resistance(df, lookback=n, current_price=100.0)
    assert result["supports"][0] == 10.0            # strongest-3 unaffected by price
    assert result["nearest_support"] == 92.0         # closest BELOW price, not the deepest


def test_support_resistance_no_level_in_direction_falls_back_to_extreme():
    """When every local high is already below current price (fully breached
    resistance), there's no "above" candidate -- fall back to the single
    most extreme level rather than returning None."""
    n = 15
    high = [100.0 + (i % 2) for i in range(n)]
    high[7] = 105.0  # still below current_price=200 -- all "resistances" breached
    low = [50.0 - (i % 2) for i in range(n)]
    low[7] = 10.0
    closes = [(h + l) / 2 for h, l in zip(high, low)]
    df = _mk_ohlc(closes, highs=high, lows=low)

    result = targets.support_resistance(df, lookback=n, current_price=200.0)
    assert result["nearest_resistance"] == 105.0  # max of local highs, not None


def test_support_resistance_without_current_price_keeps_magnitude_fallback():
    """Backward compatibility: a caller that doesn't supply current_price
    gets the old magnitude-based nearest_* (documented, not a silent
    behavior change for that call shape)."""
    n = 15
    high = [100.0 + (i % 2) for i in range(n)]
    high[7] = 200.0
    low = [50.0 - (i % 2) for i in range(n)]
    low[7] = 10.0
    closes = [(h + l) / 2 for h, l in zip(high, low)]
    df = _mk_ohlc(closes, highs=high, lows=low)

    result = targets.support_resistance(df, lookback=n)
    assert result["nearest_resistance"] == 200.0
    assert result["nearest_support"] == 10.0


def test_support_resistance_monotonic_series_has_no_locals():
    n = 20
    closes = [100.0 + i for i in range(n)]
    df = _mk_ohlc(closes)  # strictly increasing High/Low too
    result = targets.support_resistance(df, lookback=n)
    assert result["nearest_resistance"] is None
    assert result["nearest_support"] is None
    assert result["resistances"] == []
    assert result["supports"] == []


# ─── compute_price_targets — base target ────────────────────────────────────

def test_compute_price_targets_analyst_target_above_current_sets_base():
    df = _flat_df(30, 100.0)
    financials = {"analyst_target": 150.0}
    result = targets.compute_price_targets(df, financials, current_price=100.0)
    assert result["base"] == 150.0
    assert result["above_consensus"] is False  # 150 is NOT < 100


def test_compute_price_targets_analyst_target_below_current_falls_back_to_smallest_candidate():
    # Craft a df whose nearest_resistance (105) sits below both the momentum
    # projection (flat -> ~= current, excluded) and the modest-upside
    # fallback (110), so base picks the smallest qualifying candidate: 105.
    n = 30
    high = [95.0 + (i % 2) for i in range(n)]
    high[15] = 105.0  # unique local resistance just above current price
    low = [90.0 + (i % 2) for i in range(n)]
    closes = [100.0] * n  # flat close -> momentum_target ~= current, excluded
    df = _mk_ohlc(closes, highs=high, lows=low)

    financials = {"analyst_target": 90.0}  # <= current -> triggers fallback branch
    result = targets.compute_price_targets(df, financials, current_price=100.0)
    assert result["base"] == 105.0


def test_compute_price_targets_bull_empty_generator_guard_falls_back_to_modest_upside():
    # Regression test for a real prior production crash: when current_price
    # is degraded (NaN, from an upstream data glitch), every bull_candidate
    # comparison against it is False (NaN comparisons are always False), so
    # the filtered generator is empty. Before the `default=` guard, max() on
    # an empty sequence raised ValueError and crashed load_all entirely.
    df = _flat_df(30, 100.0)
    financials = {"analyst_target": 150.0, "52_week_high": 200.0, "52_week_low": 50.0}
    current_price = float("nan")

    result = targets.compute_price_targets(df, financials, current_price)  # must not raise

    expected_default = current_price * TARGETS_MODEST_UPSIDE_MULT
    assert math.isnan(result["bull"])
    assert math.isnan(expected_default)  # sanity: the fallback itself is NaN too


def test_compute_price_targets_above_consensus_true_when_target_below_current():
    df = _flat_df(30, 100.0)
    financials = {"analyst_target": 80.0}
    result = targets.compute_price_targets(df, financials, current_price=100.0)
    assert result["above_consensus"] is True


def test_compute_price_targets_pct_rounding():
    df = _flat_df(30, 100.0)
    financials = {"analyst_target": 133.33}
    result = targets.compute_price_targets(df, financials, current_price=100.0)
    # base = 133.33 -> pct = (133.33-100)/100*100 = 33.33 -> rounds to 33.3
    assert result["base_pct"] == 33.3


# ─── compute_price_targets — bear floor: each of the 3 candidates wins ──────

def _bear_case_df(low_pattern, high_offset=2.0):
    lows = list(low_pattern)
    highs = [l + high_offset for l in lows]
    closes = [l + high_offset / 2 for l in lows]
    return _mk_ohlc(closes, highs=highs, lows=lows)


def test_compute_price_targets_bear_floor_support_cushion_wins():
    # Shallow, easily-identified trough close to current price -> its
    # cushioned value beats both the 52w-low cushion (deep) and the
    # ATR-based floor (pulled down further by a wider daily range).
    low_pattern = [95, 94, 95, 94, 95, 94, 93, 94, 95, 94, 95, 94, 95, 94, 95]
    df = _bear_case_df(low_pattern, high_offset=2.0)
    current_price = 100.0
    financials = {"52_week_low": 50.0}  # deep -> small candidate

    result = targets.compute_price_targets(df, financials, current_price)

    nearest_support = targets.support_resistance(df, current_price=current_price)["nearest_support"]
    atr_val = targets._atr_val(df)
    atr_bear = current_price - TARGETS_BEAR_ATR_MULT * atr_val
    cand_support = nearest_support * TARGETS_BEAR_SUPPORT_CUSHION_MULT
    cand_52wlow = 50.0 * TARGETS_BEAR_52W_LOW_CUSHION_MULT
    expected = round(max(cand_support, cand_52wlow, atr_bear), 2)

    assert cand_support == max(cand_support, cand_52wlow, atr_bear)  # confirm the intended winner
    assert result["bear"] == expected


def test_compute_price_targets_bear_floor_52w_low_cushion_wins():
    # Deep trough (small support candidate), but week52_low set close to
    # current price -> its cushioned value is the largest of the three.
    low_pattern = [22, 21, 22, 21, 22, 20, 22, 21, 22, 21, 22, 21, 22, 21, 22]
    df = _bear_case_df(low_pattern, high_offset=1.0)
    current_price = 100.0
    financials = {"52_week_low": 95.0}

    result = targets.compute_price_targets(df, financials, current_price)

    nearest_support = targets.support_resistance(df, current_price=current_price)["nearest_support"]
    atr_val = targets._atr_val(df)
    atr_bear = current_price - TARGETS_BEAR_ATR_MULT * atr_val
    cand_support = nearest_support * TARGETS_BEAR_SUPPORT_CUSHION_MULT
    cand_52wlow = 95.0 * TARGETS_BEAR_52W_LOW_CUSHION_MULT
    expected = round(max(cand_support, cand_52wlow, atr_bear), 2)

    assert cand_52wlow == max(cand_support, cand_52wlow, atr_bear)  # confirm the intended winner
    assert result["bear"] == expected


def test_compute_price_targets_bear_floor_atr_based_wins():
    # Both support and 52w-low candidates set deep/low; a tight, low-range
    # df keeps ATR small, so the ATR-based floor (close to current_price)
    # is the largest of the three.
    low_pattern = [50, 49, 50, 49, 50, 48, 50, 49, 50, 49, 50, 49, 50, 49, 50]
    df = _bear_case_df(low_pattern, high_offset=0.2)
    current_price = 100.0
    financials = {"52_week_low": 10.0}

    result = targets.compute_price_targets(df, financials, current_price)

    nearest_support = targets.support_resistance(df, current_price=current_price)["nearest_support"]
    atr_val = targets._atr_val(df)
    atr_bear = current_price - TARGETS_BEAR_ATR_MULT * atr_val
    cand_support = nearest_support * TARGETS_BEAR_SUPPORT_CUSHION_MULT
    cand_52wlow = 10.0 * TARGETS_BEAR_52W_LOW_CUSHION_MULT
    expected = round(max(cand_support, cand_52wlow, atr_bear), 2)

    assert atr_bear == max(cand_support, cand_52wlow, atr_bear)  # confirm the intended winner
    assert result["bear"] == expected


# ─── risk_reward ─────────────────────────────────────────────────────────────

def test_risk_reward_entry_equals_stop_returns_zero():
    assert targets.risk_reward(entry=100.0, stop=100.0, target=120.0) == 0.0


def test_risk_reward_entry_below_stop_returns_zero():
    assert targets.risk_reward(entry=100.0, stop=105.0, target=120.0) == 0.0


def test_risk_reward_normal_case_rounds_to_one_decimal():
    # risk = 10, reward = 20 -> ratio 2.0
    assert targets.risk_reward(entry=100.0, stop=90.0, target=120.0) == 2.0


def test_risk_reward_rounds_non_exact_ratio():
    # risk = 12, reward = 25 -> ratio 2.0833... -> rounds to 2.1
    assert targets.risk_reward(entry=100.0, stop=88.0, target=125.0) == 2.1
