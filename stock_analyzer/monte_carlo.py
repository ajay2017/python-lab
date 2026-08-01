"""
Portfolio Outcome-Range Simulator — historical block-bootstrap Monte Carlo.

Resamples REAL historical daily returns for the held tickers (preserving the
realized cross-ticker correlation structure via a shared block-date sample
across all tickers) to produce a percentile-band distribution of portfolio
outcomes over a horizon.

This is a historical-bootstrap ESTIMATE, not a regime-probability claim — no
probability is attached to any macro regime anywhere in this module (the
daily_regime table has too little history for a base rate; see F-200's
retreat from that framing). Diagnostic/awareness only — never gates a
recommendation, same class as stress_test.py and regime_targets.py.
"""

import numpy as np
import pandas as pd

from stock_analyzer import data as _data
from stock_analyzer.constants import (
    MC_HISTORY_PERIOD,
    MC_MIN_HISTORY_DAYS,
    MC_TRIALS,
    MC_BLOCK_DAYS,
)


def _f(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


def _weight_fraction(val) -> float:
    """Weight (%) column -> fraction (e.g. 12.5 -> 0.125). Missing/NaN -> 0.0."""
    return _f(val) / 100.0


def fetch_long_history(
    tickers: list[str],
    period: str = MC_HISTORY_PERIOD,
) -> dict[str, pd.DataFrame | None]:
    """
    Fetch multi-year price history per ticker via the existing multi-source
    failover (data.fetch_price_history — yfinance -> Finnhub -> FMP). Returns
    {ticker: df}; df is None on fetch failure so callers can report it rather
    than silently treating a failed fetch as "no history".
    """
    result: dict[str, pd.DataFrame | None] = {}
    for ticker in tickers:
        try:
            result[ticker] = _data.fetch_price_history(ticker, period=period)
        except Exception:
            result[ticker] = None
    return result


def build_return_matrix(
    long_history: dict[str, pd.DataFrame | None],
    min_days: int = MC_MIN_HISTORY_DAYS,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Align tickers with sufficient history into one date-indexed daily-return
    DataFrame. Tickers with fewer than `min_days` rows of usable Close data
    (fetch failure, recent IPO, delisted) are excluded — returned in
    `excluded` so the caller can report them rather than silently dropping
    them from the user's view.

    Returns (returns_df, excluded_tickers).
    """
    series = {}
    excluded: list[str] = []
    for ticker, df in long_history.items():
        if df is None or df.empty or "Close" not in df.columns:
            excluded.append(ticker)
            continue
        close = df["Close"].dropna()
        if len(close) < min_days:
            excluded.append(ticker)
            continue
        series[ticker] = close

    if len(series) < 1:
        return pd.DataFrame(), excluded

    prices = pd.DataFrame(series).dropna()
    returns = prices.pct_change().dropna()
    return returns, excluded


def block_bootstrap_paths(
    returns_df: pd.DataFrame,
    weights: dict[str, float],
    n_trials: int = MC_TRIALS,
    block_days: int = MC_BLOCK_DAYS,
    horizon_days: int = 63,
    seed: int | None = None,
) -> np.ndarray:
    """
    Resample contiguous blocks of REAL historical trading days — the SAME
    sampled dates applied across every ticker in a given trial — so a
    historically correlated move (e.g. a broad tech selloff day) hits all
    held tech names together in the resample, exactly as it did historically,
    instead of being destroyed by independent per-ticker resampling.

    Weights are renormalized over whichever tickers are actually present in
    `returns_df` (i.e. after `build_return_matrix`'s exclusions), so an
    excluded ticker's weight is redistributed rather than silently vanishing
    from the portfolio total.

    Returns an (n_trials, horizon_days) array of CUMULATIVE portfolio return
    (e.g. 0.05 == +5%) at each day of the simulated horizon. Weights are held
    CONSTANT (implicitly rebalanced to target each simulated day) rather than
    left to drift with performance — negligible at short horizons, more
    material at the 1yr horizon than a true buy-and-hold path would show.
    """
    tickers = [t for t in returns_df.columns if t in weights]
    if not tickers:
        return np.zeros((n_trials, horizon_days))

    w = np.array([weights[t] for t in tickers], dtype=float)
    w_sum = w.sum()
    if w_sum <= 0:
        return np.zeros((n_trials, horizon_days))
    w = w / w_sum

    rets = returns_df[tickers].to_numpy()
    n_days = rets.shape[0]
    if n_days < 1:
        return np.zeros((n_trials, horizon_days))
    block_days = max(1, min(block_days, n_days))

    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(horizon_days / block_days))

    paths = np.empty((n_trials, horizon_days))
    for trial in range(n_trials):
        chunks = []
        for _ in range(n_blocks):
            start = rng.integers(0, n_days - block_days + 1)
            chunks.append(rets[start:start + block_days])
        sampled = np.concatenate(chunks, axis=0)[:horizon_days]
        port_daily = sampled @ w
        paths[trial] = np.cumprod(1.0 + port_daily) - 1.0

    return paths


def summarize_paths(
    paths: np.ndarray,
    percentiles: tuple[int, ...] = (5, 25, 50, 75, 95),
) -> dict:
    """
    Per-day percentile bands (feeds a fan chart) plus horizon-endpoint
    distribution stats (feeds a summary table). `paths` is cumulative
    portfolio return, shape (n_trials, horizon_days).
    """
    if paths.size == 0:
        return {}

    bands = {p: np.percentile(paths, p, axis=0) for p in percentiles}
    endpoint = paths[:, -1]
    endpoint_pct = {p: float(np.percentile(endpoint, p)) for p in percentiles}

    return {
        "percentiles":  list(percentiles),
        "bands":        {p: bands[p].tolist() for p in percentiles},
        "endpoint_pct": endpoint_pct,
        "horizon_days": paths.shape[1],
        "n_trials":     paths.shape[0],
    }


def run_monte_carlo(
    port_df: pd.DataFrame,
    horizon_days: int,
    n_trials: int = MC_TRIALS,
    block_days: int = MC_BLOCK_DAYS,
    min_days: int = MC_MIN_HISTORY_DAYS,
    history_period: str = MC_HISTORY_PERIOD,
    seed: int | None = None,
) -> dict:
    """
    Top-level orchestrator: fetch long history for held tickers, build the
    aligned return matrix, run the block bootstrap, and summarize.

    Returns {} if there's no portfolio to simulate; otherwise a dict with
    `excluded` (tickers dropped for insufficient history), `included`
    (tickers actually simulated), `summary` (see summarize_paths), and
    `portfolio_value` — the $ basis for the endpoint table, scoped to the
    INCLUDED tickers only (not the full portfolio) so it stays consistent
    with the simulated % return, which is itself computed over the included
    subset with renormalized weights. Using the full portfolio value here
    would silently overstate the $ range whenever an excluded ticker (e.g. a
    recent IPO) carries material weight.
    """
    if port_df is None or port_df.empty:
        return {}

    tickers = port_df["Ticker"].tolist()
    weights: dict[str, float] = {
        str(row["Ticker"]): _weight_fraction(row.get("Weight (%)"))
        for _, row in port_df.iterrows()
    }
    market_values: dict[str, float] = {
        str(row["Ticker"]): _f(row.get("Market Value"))
        for _, row in port_df.iterrows()
    }

    long_history = fetch_long_history(tickers, period=history_period)
    returns_df, excluded = build_return_matrix(long_history, min_days=min_days)

    included = [t for t in returns_df.columns if t in weights]
    included_value = sum(market_values.get(t, 0.0) for t in included)

    if not included:
        return {
            "excluded":        excluded,
            "included":        [],
            "summary":         {},
            "portfolio_value": 0.0,
        }

    paths = block_bootstrap_paths(
        returns_df, weights,
        n_trials=n_trials, block_days=block_days,
        horizon_days=horizon_days, seed=seed,
    )

    return {
        "excluded":        excluded,
        "included":        included,
        "summary":         summarize_paths(paths),
        "portfolio_value": included_value,
    }
