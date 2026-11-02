"""Which market session a live `change_pct` describes (pure, no I/O).

A live quote's change is last price vs the prior close. On a weekend, a market
holiday, or before 09:30 ET on a trading day, "last price" is still the LAST
COMPLETED session's close, so the move is that session's, not today's. UI text
that says "moved X% today" must use `move_when_phrase` instead of the literal.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from stock_analyzer.data import is_trading_day

_OPEN = time(9, 30)
_WALKBACK_DAYS = 10   # bounded so a broken calendar can never loop forever


def price_move_session(now_et: datetime) -> tuple[bool, date | None]:
    """(True, today) once today's session has begun; else (False, the most
    recent trading day strictly before today), or (False, None) if none found
    within the bounded walk-back."""
    today = now_et.date()
    if is_trading_day(today) and now_et.time() >= _OPEN:
        return True, today
    d = today
    for _ in range(_WALKBACK_DAYS):
        d = d - timedelta(days=1)
        if is_trading_day(d):
            return False, d
    return False, None


def move_when_phrase(now_et: datetime) -> str:
    """"today", or "in the last session (Fri Oct 30)"."""
    is_today, d = price_move_session(now_et)
    if is_today or d is None:
        return "today" if is_today else "in the last session"
    return f"in the last session ({d.strftime('%a %b')} {d.day})"
