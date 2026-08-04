"""Single source of truth for NY-timezone "now" / "today".

The project convention is America/New_York (Streamlit Cloud runs UTC), but the
2026-08-04 audit found `_today_et()` independently redefined in ~7 modules plus
app.py, and naive `datetime.utcnow()` / `date.today()` calls scattered across
unrelated modules producing an off-by-one date-boundary bug class (the Critical
`premortem_monitor.py` same-day fire was a cousin). This module centralizes the
tz-aware primitives those call sites need.

Additive by design: this ships the shared helpers; call sites are migrated onto
them incrementally, and any rewire that touches a decision/gate path gets its
own review. `is_trading_day()` / `market_status()` already live in
`stock_analyzer.data` (they carry the hardcoded NYSE calendar) — this module is
deliberately dependency-light and does not duplicate them.
"""
from __future__ import annotations

from datetime import date, datetime

import pytz

# The one canonical Eastern-time zone object for the whole project.
ET = pytz.timezone("America/New_York")


def now_et() -> datetime:
    """Timezone-aware current time in America/New_York.

    Use instead of naive `datetime.now()` / `datetime.utcnow()` anywhere a value
    feeds a date comparison or a decision.
    """
    return datetime.now(ET)


def today_et() -> date:
    """Current calendar date in America/New_York.

    Use instead of bare `date.today()` / `datetime.today()`, which resolve
    against the server's UTC clock and are off by one between ~8pm and midnight
    ET — the exact class the 2026-08-04 audit flagged in `portfolio.py` and
    `trade_analytics.py`.
    """
    return now_et().date()
