"""
Analyst consensus price-target (PT) cut detection — F-169 Phase 2.

Closes a gap the Phase-1 "📉 Analyst Revisions" alert (portfolio.py::alerts())
cannot see: a firm that keeps its rating unchanged but guts its price target
is structurally invisible to a rating-ACTION-only detector, regardless of
magnitude (see docs/architecture.md §6.23).

Pure module — no Streamlit, no DB, no network calls. Consumes the
`analyst_target_snapshots` DataFrame already loaded by the caller (one row
per held ticker per trading day, logged since 2026-07-21 via
cron_runner._run_premarket() -> headless_alert_engine.py). Row-count-as-
trading-days is used for the lookback window (same weekend/holiday-agnostic
semantics `exit_velocity.py` already uses) rather than calendar-date math.

Two independent consumers wire this same detector (never two independent
reads of the same underlying question, per CLAUDE.md's coordination rule):
portfolio.py::alerts() (pt_cut_signals param) and
thesis_red_team.py::pt_points_from_signal() (erosion-score component).
"""

from __future__ import annotations

import pandas as pd

from stock_analyzer.constants import (
    PT_TARGET_LOOKBACK_DAYS,
    PT_TARGET_CUT_WARN_PCT,
    PT_TARGET_CUT_DANGER_PCT,
)


def _withheld(ticker: str, n_snapshot_days: int = 0) -> dict:
    """Shared shape for the 'insufficient history' withholding path — never
    fabricate a flat/neutral result from partial data."""
    return {
        "ticker": ticker,
        "insufficient_history": True,
        "source_switch_suppressed": False,
        "direction": None,
        "pct_change": None,
        "level": None,
        "newest_date": None,
        "compare_date": None,
        "newest_target": None,
        "compare_target": None,
        "n_snapshot_days": n_snapshot_days,
    }


def detect_pt_cut(snapshots_df: pd.DataFrame, ticker: str) -> dict:
    """Return a PT-cut signal dict for `ticker`, comparing the newest
    consensus target_mean against the value PT_TARGET_LOOKBACK_DAYS trading
    days earlier. Always returns a dict (never None); withholds a verdict
    (direction=None) when there isn't enough trustworthy history.

    Return shape:
      {
        "ticker": str,
        "insufficient_history": bool,        # < 6 distinct snapshot_date rows
        "source_switch_suppressed": bool,    # info_source mismatch -> untrustworthy
        "direction": "cut" | "flat" | "up" | None,   # None only when withheld
        "pct_change": float | None,          # fraction, e.g. -0.082
        "level": "danger" | "warning" | None,
        "newest_date": str | None, "compare_date": str | None,
        "newest_target": float | None, "compare_target": float | None,
        "n_snapshot_days": int,
      }
    """
    tkr = str(ticker).upper() if ticker is not None else ""

    if snapshots_df is None or snapshots_df.empty:
        return _withheld(tkr)

    required = {"ticker", "snapshot_date", "target_mean"}
    if not required.issubset(snapshots_df.columns):
        return _withheld(tkr)

    df = snapshots_df[snapshots_df["ticker"].astype(str).str.upper() == tkr].copy()
    df = df.dropna(subset=["target_mean"])
    if df.empty:
        return _withheld(tkr)

    # De-dup snapshot_date, keeping the row with the latest captured_at.
    # Defensive: the DB enforces UNIQUE(ticker, snapshot_date), but a pure
    # function shouldn't trust that blindly.
    if "captured_at" in df.columns:
        df["_captured_sort"] = pd.to_datetime(df["captured_at"], errors="coerce", utc=True)
        df = df.sort_values("_captured_sort")
    df = df.drop_duplicates(subset=["snapshot_date"], keep="last")

    n_days = int(df["snapshot_date"].nunique())
    if n_days < PT_TARGET_LOOKBACK_DAYS + 1:
        return _withheld(tkr, n_snapshot_days=n_days)

    df["_sd"] = pd.to_datetime(df["snapshot_date"], errors="coerce")
    df = df.sort_values("_sd", ascending=False)

    newest = df.iloc[0]
    compare = df.iloc[PT_TARGET_LOOKBACK_DAYS]

    def _source(row):
        val = row.get("info_source") if "info_source" in df.columns else None
        return None if pd.isna(val) else val

    newest_source = _source(newest)
    compare_source = _source(compare)

    newest_target = float(newest["target_mean"])
    compare_target = float(compare["target_mean"])
    newest_date = str(newest["snapshot_date"])
    compare_date = str(compare["snapshot_date"])

    # Source-switch suppression: both None -> trusted; both non-None and
    # equal -> trusted; both non-None and different -> suppressed; one None
    # / one non-None -> trusted (deliberate judgment call — info_source is
    # only ever None or "fmp" today, so a lone FMP-backfilled day shouldn't
    # disqualify the whole comparison; FMP is sourcing the same metric, not
    # redefining it).
    if newest_source is not None and compare_source is not None and newest_source != compare_source:
        return {
            "ticker": tkr,
            "insufficient_history": False,
            "source_switch_suppressed": True,
            "direction": None,
            "pct_change": None,
            "level": None,
            "newest_date": newest_date,
            "compare_date": compare_date,
            "newest_target": newest_target,
            "compare_target": compare_target,
            "n_snapshot_days": n_days,
        }

    if compare_target == 0:
        return {
            "ticker": tkr,
            "insufficient_history": False,
            "source_switch_suppressed": False,
            "direction": None,
            "pct_change": None,
            "level": None,
            "newest_date": newest_date,
            "compare_date": compare_date,
            "newest_target": newest_target,
            "compare_target": compare_target,
            "n_snapshot_days": n_days,
        }

    pct_change = (newest_target - compare_target) / compare_target
    pct_pts = pct_change * 100

    if pct_pts <= PT_TARGET_CUT_DANGER_PCT:
        direction, level = "cut", "danger"
    elif pct_pts <= PT_TARGET_CUT_WARN_PCT:
        direction, level = "cut", "warning"
    elif pct_pts < 0:
        direction, level = "cut", None
    elif pct_pts == 0:
        direction, level = "flat", None
    else:
        direction, level = "up", None

    return {
        "ticker": tkr,
        "insufficient_history": False,
        "source_switch_suppressed": False,
        "direction": direction,
        "pct_change": pct_change,
        "level": level,
        "newest_date": newest_date,
        "compare_date": compare_date,
        "newest_target": newest_target,
        "compare_target": compare_target,
        "n_snapshot_days": n_days,
    }
