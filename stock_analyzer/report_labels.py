"""Pure labels for saved periodic reports (no Streamlit, no I/O).

A monthly report covers a window such as 2026-10-04 -> 2026-11-01. Naming it
after its END date's month ("November 2026") mislabels it, so a report is
named by the month most of its days fall in, and the date range is shown when
the window crosses a month boundary. `generated_at` is stored in UTC; the
date shown to the owner is the ET date.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from stock_analyzer.market_time import ET


def _as_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _majority_month(start: date, end: date) -> tuple[int, int] | None:
    """(year, month) holding strictly more than half the window's days, else None."""
    counts: dict[tuple[int, int], int] = {}
    d = start
    total = 0
    while d <= end:
        counts[(d.year, d.month)] = counts.get((d.year, d.month), 0) + 1
        total += 1
        d += timedelta(days=1)
    for k, n in counts.items():
        if n * 2 > total:
            return k
    return None


def period_month_key(start, end) -> tuple | None:
    """Archive de-dup key: the window's majority month, or the exact window when
    no month has a majority. None when the dates can't be read."""
    s, e = _as_date(start), _as_date(end)
    if s is None or e is None or e < s:
        return None
    return _majority_month(s, e) or (s, e)


def period_label(start, end) -> str | None:
    """"October 2026" for a window inside a month; "October 2026 (Oct 04 – Nov 01)"
    for a window that crosses a month but mostly falls in one; the bare range
    "Oct 04 – Nov 01" when no month has a majority. None when unreadable."""
    s, e = _as_date(start), _as_date(end)
    if s is None or e is None or e < s:
        return None
    rng = f"{s.strftime('%b %d')} – {e.strftime('%b %d')}"
    major = _majority_month(s, e)
    if (s.year, s.month) == (e.year, e.month):
        return s.strftime("%B %Y")
    if major is None:
        return rng
    return f"{date(major[0], major[1], 1).strftime('%B %Y')} ({rng})"


def generated_et_date(ts) -> str:
    """The ET calendar date (YYYY-MM-DD) of a stored generated_at timestamp.
    A tz-naive value is treated as UTC (how the DB stores it). "" if unreadable."""
    if ts is None:
        return ""
    try:
        if isinstance(ts, datetime):
            dt = ts
        else:
            import pandas as pd
            dt = pd.to_datetime(ts).to_pydatetime()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET).date().isoformat()
    except Exception:
        return ""
