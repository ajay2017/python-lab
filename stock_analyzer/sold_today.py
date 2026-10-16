"""Which tickers were SOLD on a given ET trading day — the one shared rule.

A just-exited position must never immediately re-surface as a BUY the same
day: the exit advisor stops watching a ticker the moment it leaves the
portfolio, so nothing downstream remembers the exit unless it is read back
from the trade log. Three surfaces need that memory — the Daily Brief's
new-pick / add-to-winner block (`daily_briefing`), the 📋 Watchlist page's
ENTER NOW card, and the cron's "Ready to Enter" email
(`headless_alert_engine.compute_watchlist_entries`) — and they must agree on
what "sold today" means (America/New_York date of `traded_at`, `action ==
SELL`), so the parsing lives here once.

Three-state return, because two of the three callers decide differently on
"couldn't check" than on "checked, nothing sold":
  None   — the trade log is unavailable or unparseable (offline sentinel)
  set()  — read fine, no SELL today
  {...}  — the upper-cased tickers with a SELL today

Pure: no I/O, no Streamlit.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

_REQUIRED_COLUMNS = ("traded_at", "action", "ticker")


def _et_trade_dates(trades_df):
    """(UTC timestamps, America/New_York dates) of `traded_at` -- the ONE
    "what ET day is this trade" rule, shared with `daily_budget` so the two
    can never disagree on "today". May raise; callers own their fail-safe."""
    traded_at = pd.to_datetime(trades_df["traded_at"], utc=True, errors="coerce",
                               format="ISO8601")
    return traded_at, traded_at.dt.tz_convert("America/New_York").dt.date


def tickers_sold_on(trades_df, day: date) -> "set[str] | None":
    if trades_df is None:
        return None
    if getattr(trades_df, "empty", True):
        # An empty but readable trade log genuinely has no SELL today.
        # (`load_trades_or_none` returns None — not an empty frame — on a
        # failed read, so an empty frame here is a real empty log.)
        return set()
    if any(c not in trades_df.columns for c in _REQUIRED_COLUMNS):
        return None
    try:
        _, traded_date = _et_trade_dates(trades_df)
        mask = (trades_df["action"].astype(str).str.upper() == "SELL") & (traded_date == day)
        return {
            str(t).strip().upper() for t in trades_df.loc[mask, "ticker"]
            if pd.notna(t) and str(t).strip()
        }
    except Exception:
        return None
