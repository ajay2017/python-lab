"""
Exit-signal velocity: detect held positions where a WATCH tier signal is
accelerating toward TRIM, so an alert can fire BEFORE the TRIM threshold
is crossed.

Pure module — no Streamlit, no DB, no network calls. Consumes the
`exit_signals` DataFrame already loaded by the caller (cron_runner or app).

Prerequisite: `exit_signals` table populated daily by the cron premarket run
(live since 2026-07-21). Velocity computation silently returns None for any
ticker with < 2 days of WATCH signal history; results fill in as data
accumulates.
"""

from __future__ import annotations

import pandas as pd


def compute_watch_velocity(
    signals_df: pd.DataFrame,
    ticker: str,
    lookback_days: int,
) -> dict | None:
    """Return a velocity dict for `ticker` if it has been in WATCH tier for
    at least 2 days within `lookback_days` AND its composite score has dropped
    by a meaningful amount over that window.

    Returns None when:
      - fewer than 2 WATCH rows exist for the ticker in the window
      - composite_score is missing on the oldest or newest row

    Return shape when data is sufficient:
      {
        "ticker":        str,
        "delta":         float,   # newest_score − oldest_score (negative = deteriorating)
        "n_days":        int,     # number of WATCH signal days in window
        "newest_score":  float,
        "oldest_score":  float,
        "newest_date":   str,     # YYYY-MM-DD
        "oldest_date":   str,     # YYYY-MM-DD
      }
    """
    if signals_df is None or signals_df.empty:
        return None

    required = {"ticker", "signal_date", "signal_type", "composite_score"}
    if not required.issubset(signals_df.columns):
        return None

    watch_rows = signals_df[
        (signals_df["ticker"].astype(str).str.upper() == ticker.upper()) &
        (signals_df["signal_type"] == "WATCH")
    ].copy()

    if watch_rows.empty:
        return None

    watch_rows["signal_date"] = pd.to_datetime(watch_rows["signal_date"], errors="coerce")
    watch_rows = watch_rows.dropna(subset=["signal_date"])

    if watch_rows.empty:
        return None

    # Restrict to the lookback window based on the most recent date present
    cutoff = watch_rows["signal_date"].max() - pd.Timedelta(days=lookback_days)
    watch_rows = watch_rows[watch_rows["signal_date"] >= cutoff]

    if len(watch_rows) < 2:
        return None

    watch_rows = watch_rows.sort_values("signal_date", ascending=True)

    oldest = watch_rows.iloc[0]
    newest = watch_rows.iloc[-1]

    oldest_score = oldest["composite_score"]
    newest_score = newest["composite_score"]

    if pd.isna(oldest_score) or pd.isna(newest_score):
        return None

    oldest_score = float(oldest_score)
    newest_score = float(newest_score)
    delta = newest_score - oldest_score

    return {
        "ticker":       ticker.upper(),
        "delta":        round(delta, 2),
        "n_days":       len(watch_rows),
        "newest_score": round(newest_score, 1),
        "oldest_score": round(oldest_score, 1),
        "newest_date":  str(newest["signal_date"].date()),
        "oldest_date":  str(oldest["signal_date"].date()),
    }


def find_accelerating_watches(
    signals_df: pd.DataFrame,
    watch_tickers: list[str],
    lookback_days: int,
    drop_threshold: float,
) -> list[dict]:
    """Return velocity dicts for each ticker in `watch_tickers` whose composite
    score has dropped >= `drop_threshold` points over `lookback_days`.

    `drop_threshold` is a positive number (e.g. 8 means an 8-point composite
    drop). Tickers with insufficient history are silently skipped.
    """
    results = []
    for ticker in watch_tickers:
        v = compute_watch_velocity(signals_df, ticker, lookback_days)
        if v is None:
            continue
        if v["delta"] <= -abs(drop_threshold):
            results.append(v)
    # Most deteriorated first
    results.sort(key=lambda x: x["delta"])
    return results
