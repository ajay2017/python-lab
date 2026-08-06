"""
Predictive Modeling Shadow Layer — Phase 1 volatility model (F-234).

Pure logic, no Streamlit/DB imports. Predicts RISK (forward realized
volatility), never a stock-level directional/return point-estimate — that
distinction is the §5.8 invariant (`docs/plans/next-evolution-strategy.md`)
this whole layer is built to respect. Nothing in this module is read by any
existing gate, recommendation, or the composite score; it feeds only the
quarantined 🔬 Model Lab page via the `model_predictions` ledger.

See docs/plans/predictive-modeling-shadow-layer.md §1.2/§1.6 for the full
design and leakage-guard rationale.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stock_analyzer.constants import VOL_FORECAST_EWMA_LAMBDA

# Minimum return observations before either function will produce a number.
# Below this, both functions return None (a withheld read, never a garbage
# number computed on a degenerate series) — same "measurement floor, not a
# fabricated value" philosophy as every other producer in this app.
_MIN_OBSERVATIONS = 5

# Trading days per year used to annualize a daily variance/std-dev estimate.
_TRADING_DAYS_PER_YEAR = 252


def forecast_vol_ewma(returns: pd.Series, lam: float = VOL_FORECAST_EWMA_LAMBDA) -> float | None:
    """RiskMetrics-style EWMA forward-volatility forecast (annualized).

    **LEAKAGE CONTRACT (read this before calling):** this function uses ONLY
    the observations present in `returns` — the LAST element of `returns` is
    the most recent observation "as of" which the forecast is being made
    (i.e. the implicit `made_at` point). It is the CALLER's responsibility to
    slice `returns` so that it contains no observation dated after the
    intended `made_at` timestamp. This is the layer's #1 leakage boundary
    (design doc §1.6, item 1): passing a return series that extends past
    `made_at` silently corrupts the entire track record's out-of-sample
    guarantee. This function has no notion of dates at all — it is a pure
    array transform, so it structurally CANNOT peek past what you hand it;
    the guarantee is enforced entirely by what the caller passes in.

    Model: `variance_t = lam * variance_{t-1} + (1 - lam) * return_t^2`,
    seeded with the simple sample variance of `returns` so the recursion
    isn't overly sensitive to the single earliest observation, then walked
    forward through every observation in order. The final variance estimate
    IS the forecast for the next period (and, by the model's own stationarity
    assumption, the forward horizon) — annualized via `* 252` then
    square-rooted.

    `lam` (λ) is RiskMetrics' fixed daily decay factor (0.94) — a classical
    constant, NOT fitted to this app's data. That matters for the backfill
    script: a model with no fitted parameters carries no in-sample/backtest-
    leakage risk the way a fitted model (GARCH-MLE, gradient-boosted trees)
    would if it were ever backfilled (design doc §1.6b).

    Returns `None` (never a fabricated number) when `returns` has fewer than
    `_MIN_OBSERVATIONS` non-null values, or the result is non-finite.
    """
    r = returns.dropna() if returns is not None else pd.Series(dtype=float)
    if len(r) < _MIN_OBSERVATIONS:
        return None
    arr = r.to_numpy(dtype=float)
    variance = float(np.var(arr))
    for x in arr:
        variance = lam * variance + (1.0 - lam) * (x ** 2)
    annualized_variance = variance * _TRADING_DAYS_PER_YEAR
    if not np.isfinite(annualized_variance) or annualized_variance < 0:
        return None
    return float(np.sqrt(annualized_variance))


def realized_vol(returns: pd.Series) -> float | None:
    """Annualized realized volatility (sample std-dev of daily returns × √252)
    over the given return series.

    Used for BOTH: (1) the naive persistence baseline, evaluated over a
    trailing window as-of the forecast date (same leakage discipline as
    `forecast_vol_ewma` — the caller slices the window), and (2) the
    maturation target, evaluated over the FORWARD window after a prediction
    matures (inherently past data at maturation time, so that call site
    cannot leak by construction). Same formula, different windows — per
    design doc §1.2.

    Returns `None` below `_MIN_OBSERVATIONS` non-null values, or on a
    non-finite result.
    """
    r = returns.dropna() if returns is not None else pd.Series(dtype=float)
    if len(r) < _MIN_OBSERVATIONS:
        return None
    std = float(np.std(r.to_numpy(dtype=float), ddof=1))
    annualized = std * np.sqrt(_TRADING_DAYS_PER_YEAR)
    if not np.isfinite(annualized) or annualized < 0:
        return None
    return annualized
