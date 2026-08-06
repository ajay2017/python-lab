"""Tests for stock_analyzer/vol_forecast.py — Predictive Modeling Shadow
Layer Phase 1 (F-234), MEASUREMENT-ONLY. These are the leakage-boundary
tests the build spec calls out explicitly: forecast_vol_ewma must use ONLY
the observations passed in, and must never be sensitive to data appended
AFTER the point it was called with."""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer.vol_forecast import forecast_vol_ewma, realized_vol


def _returns(seed: int, n: int, scale: float = 0.01) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0, scale, n))


# ── Minimum-observation floor ────────────────────────────────────────────────

def test_forecast_vol_ewma_below_min_observations_returns_none():
    assert forecast_vol_ewma(pd.Series([0.01, -0.02, 0.005, 0.0])) is None  # 4 obs


def test_realized_vol_below_min_observations_returns_none():
    assert realized_vol(pd.Series([0.01, -0.02, 0.005, 0.0])) is None  # 4 obs


def test_forecast_vol_ewma_exactly_min_observations_returns_a_number():
    out = forecast_vol_ewma(pd.Series([0.01, -0.02, 0.005, 0.0, 0.01]))  # 5 obs
    assert out is not None
    assert isinstance(out, float)
    assert out >= 0


def test_realized_vol_exactly_min_observations_returns_a_number():
    out = realized_vol(pd.Series([0.01, -0.02, 0.005, 0.0, 0.01]))  # 5 obs
    assert out is not None
    assert out >= 0


def test_empty_series_returns_none():
    assert forecast_vol_ewma(pd.Series([], dtype=float)) is None
    assert realized_vol(pd.Series([], dtype=float)) is None


def test_none_input_returns_none():
    assert forecast_vol_ewma(None) is None
    assert realized_vol(None) is None


# ── Basic correctness ────────────────────────────────────────────────────────

def test_realized_vol_zero_for_constant_returns():
    # Zero variance -> zero annualized vol, not None (0 is a legitimate,
    # finite value here, distinct from the None "insufficient data" sentinel).
    r = pd.Series([0.01] * 10)
    out = realized_vol(r)
    assert out == pytest.approx(0.0, abs=1e-9)


def test_realized_vol_known_value():
    # std([r1..rn], ddof=1) * sqrt(252) -- verify against a hand-computed value.
    r = pd.Series([0.01, -0.01, 0.02, -0.02, 0.0])
    expected = float(np.std(r.to_numpy(), ddof=1) * np.sqrt(252))
    assert realized_vol(r) == pytest.approx(expected, rel=1e-9)


def test_forecast_vol_ewma_positive_and_finite_on_random_data():
    r = _returns(seed=1, n=60)
    out = forecast_vol_ewma(r)
    assert out is not None
    assert np.isfinite(out)
    assert out > 0


def test_forecast_vol_ewma_higher_vol_series_forecasts_higher():
    calm = _returns(seed=2, n=80, scale=0.003)
    volatile = _returns(seed=2, n=80, scale=0.03)
    calm_f = forecast_vol_ewma(calm)
    volatile_f = forecast_vol_ewma(volatile)
    assert calm_f is not None and volatile_f is not None
    assert volatile_f > calm_f


def test_lambda_parameter_changes_the_forecast():
    r = _returns(seed=3, n=60)
    low_lam = forecast_vol_ewma(r, lam=0.80)
    high_lam = forecast_vol_ewma(r, lam=0.99)
    assert low_lam is not None and high_lam is not None
    assert low_lam != high_lam


# ── Leakage-boundary contract (THE load-bearing test) ────────────────────────

def test_forecast_vol_ewma_is_pure_and_deterministic():
    """Calling twice with the identical slice yields the identical result --
    the function has no hidden state and cannot be influenced by anything
    other than what's passed in."""
    r = _returns(seed=4, n=50)
    assert forecast_vol_ewma(r) == forecast_vol_ewma(r)


def test_forecast_vol_ewma_unaffected_by_data_appended_after_the_asof_point():
    """THE leakage-boundary test: a forecast computed on a point-in-time
    slice must be byte-identical whether or not the caller later appends
    'future' observations to a COPY of the underlying data -- because the
    function only ever sees what's explicitly passed to it. This is the
    literal contract stated in the module docstring: 'the last element of
    `returns` is the most recent observation as-of which the forecast is
    made' -- appending rows the function never sees cannot change its
    output, by construction of it being a pure array transform."""
    full = _returns(seed=5, n=80)
    as_of_slice = full.iloc[:40].copy()

    forecast_before = forecast_vol_ewma(as_of_slice)

    # Simulate 10 more trading days having since occurred "in the world" --
    # a NEW object, not mutating as_of_slice.
    extended = pd.concat([as_of_slice, full.iloc[40:50]], ignore_index=True)

    # Re-running on the ORIGINAL as-of slice must be unchanged.
    forecast_after = forecast_vol_ewma(as_of_slice)
    assert forecast_before == forecast_after

    # And the extended series (which legitimately has more information) is
    # free to differ -- proving the function isn't just a constant, and that
    # the earlier result was genuinely scoped to the smaller slice.
    forecast_extended = forecast_vol_ewma(extended)
    assert forecast_extended is not None
    assert forecast_extended != forecast_before


def test_realized_vol_unaffected_by_data_appended_after_the_window():
    full = _returns(seed=6, n=80)
    window = full.iloc[:40].copy()
    before = realized_vol(window)
    extended = pd.concat([window, full.iloc[40:50]], ignore_index=True)
    after = realized_vol(window)
    assert before == after
    assert realized_vol(extended) != before
