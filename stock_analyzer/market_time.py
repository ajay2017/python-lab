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


def et_anchor_iso(day, hour: int | None = None) -> str:
    """A bare trade DATE → an ISO timestamp anchored at `hour` ET that day.

    For the import writers (broker sync / CSV / RH-text), which know the day a
    fill happened but not the time. Sending the bare date instead lets Postgres
    cast it to midnight UTC in a `timestamptz` column — and midnight UTC is the
    PRIOR EVENING in ET, so every ET reader dates the trade a day early. That
    was a live wrong number in Today's P&L and a whiplash suppression that
    failed open.

    `stock_analyzer.trade_time` repairs rows already written that way; this
    stops new ones being created, so the repair only ever covers legacy data.

    `hour` defaults to `IMPORTED_TRADE_ANCHOR_ET_HOUR`, imported lazily so this
    module stays free of a constants dependency for its two original callers.
    """
    from stock_analyzer.constants import IMPORTED_TRADE_ANCHOR_ET_HOUR
    if hour is None:
        hour = IMPORTED_TRADE_ANCHOR_ET_HOUR
    d = date.fromisoformat(str(day)[:10]) if not isinstance(day, date) else day
    # localize() rather than replace(tzinfo=...) — pytz zones carry a historical
    # LMT offset that replace() would silently apply (the classic -04:56 bug).
    return ET.localize(datetime(d.year, d.month, d.day, int(hour), 0, 0)).isoformat()
