"""How fresh and usable 🏠 Home's market-tone read is, for the pages that borrow it
(buy-check C4a, docs/plans/entry-fail-closed-c4-spec.md; owner U1 2026-11-27).

📋 Watchlist / 📈 Analysis / 📡 Signals reuse Home's read of the S&P 500 only
while it is **under ``TONE_HOME_READ_MAX_AGE_MINUTES`` old AND from the same
trading session as now** (a read taken before 09:30 stops counting at 09:30).
Otherwise they read the S&P live; if that also fails the new-entry check HOLDS
(``tone_unread``), it never falls back to the old read and never reads an
unreadable market as a flat day.

Pure: no clock (``now_et`` is always passed in), no streamlit, no db.  It must
NOT import ``entry_eligibility`` (that module imports this one).  Every
comparison against ``TONE_HOME_READ_MAX_AGE_MINUTES`` lives here, never in
app.py.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from stock_analyzer import session_label
from stock_analyzer.constants import TONE_HOME_READ_MAX_AGE_MINUTES

_ET = ZoneInfo("America/New_York")

# home_read_state states
STATE_USABLE = "usable"
STATE_NOT_TODAY = "not_today"
STATE_FAILED = "failed"
STATE_AGED = "aged"
STATE_OTHER_SESSION = "other_session"
STATE_NO_READ_TIME = "no_read_time"
STATE_UNREADABLE = "unreadable"

# `why` values carried on a resolved tone state (None = Home's read was used).
WHY_HOME_NOT_TODAY = "home_not_today"
WHY_HOME_AGED = "home_aged"
WHY_HOME_OTHER_SESSION = "home_other_session"
WHY_HOME_FAILED = "home_failed"
WHY_HOME_UNREADABLE = "home_unreadable"
WHY_LIVE_FAILED = "live_failed"

_STATE_TO_WHY = {
    STATE_NOT_TODAY: WHY_HOME_NOT_TODAY,
    STATE_FAILED: WHY_HOME_FAILED,
    STATE_AGED: WHY_HOME_AGED,
    STATE_OTHER_SESSION: WHY_HOME_OTHER_SESSION,
    STATE_NO_READ_TIME: WHY_HOME_UNREADABLE,
    STATE_UNREADABLE: WHY_HOME_UNREADABLE,
}


def _finite(x) -> bool:
    """A real, finite number (NaN / inf / bool / None / str are not)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _aware(dt) -> bool:
    return (isinstance(dt, datetime) and dt.tzinfo is not None
            and dt.tzinfo.utcoffset(dt) is not None)


def _result(state, read_at=None, age_min=None) -> dict:
    return {"state": state, "read_at": read_at, "age_min": age_min}


def home_read_state(home_ctx, home_is_today, now_et) -> dict:
    """``{"state", "read_at", "age_min"}`` for Home's market-tone read.

    ``state`` is ``usable`` only when Home was built today, its read succeeded
    (``tone_available is True``, a tone, a finite ``sp500_pct``), the read time
    ``tone_read_at`` is an aware datetime, it is not later than ``now_et``, it is
    STRICTLY under ``TONE_HOME_READ_MAX_AGE_MINUTES`` old (the Home watchdog
    rebuilds at >= that age) and it falls in the same price-move session as
    ``now_et``.  Otherwise, in this order: ``not_today`` (Home not built today),
    ``failed`` (Home's own read failed or its % isn't finite), ``no_read_time``
    (no ``tone_read_at`` key), ``unreadable`` (naive / garbage time, or one later
    than ``now_et``: same server clock, so a future time is bad data), ``aged``,
    ``other_session``.  ``read_at`` is the aware read time when it was readable;
    ``age_min`` the age in minutes when it could be computed.

    Ages use UTC subtraction so the repeated DST hour is exact (same-zone
    aware subtraction would read it as wall-clock).  Never raises."""
    try:
        if not home_is_today:
            return _result(STATE_NOT_TODAY)
        if not (
            isinstance(home_ctx, dict)
            and home_ctx.get("tone_available") is True
            and home_ctx.get("tone") is not None
            and _finite(home_ctx.get("sp500_pct"))
        ):
            return _result(STATE_FAILED)
        if "tone_read_at" not in home_ctx:
            return _result(STATE_NO_READ_TIME)
        read_at = home_ctx.get("tone_read_at")
        if not _aware(read_at) or not _aware(now_et):
            return _result(STATE_UNREADABLE)
        delta_s = (now_et.astimezone(timezone.utc)
                   - read_at.astimezone(timezone.utc)).total_seconds()
        if delta_s < 0:
            return _result(STATE_UNREADABLE)
        age_min = delta_s / 60.0
        if delta_s >= TONE_HOME_READ_MAX_AGE_MINUTES * 60:
            return _result(STATE_AGED, read_at, age_min)
        if (session_label.price_move_session(read_at.astimezone(_ET))
                != session_label.price_move_session(now_et.astimezone(_ET))):
            return _result(STATE_OTHER_SESSION, read_at, age_min)
        return _result(STATE_USABLE, read_at, age_min)
    except Exception:
        return _result(STATE_UNREADABLE)


def why_for_state(state) -> "str | None":
    """The ``why`` a resolved tone state carries when Home's read wasn't used."""
    return _STATE_TO_WHY.get(state)


# ── Wording ──────────────────────────────────────────────────────────────────
def _clock(dt) -> "str | None":
    try:
        d = dt.astimezone(_ET) if _aware(dt) else dt
        hour = d.hour % 12 or 12
        return f"{hour}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"
    except Exception:
        return None


def unread_reason(ts=None) -> str:
    """The short hold reason when the market move couldn't be read."""
    return "the market move couldn't be read"


def unread_detail(ts) -> str:
    """One sentence saying why no usable read exists, from the tone state's
    ``why`` (and Home's read time when it had one).  Never raises."""
    ts = ts if isinstance(ts, dict) else {}
    why = ts.get("why")
    clock = _clock(ts.get("home_read_at"))
    home = f"Home's read from {clock} ET" if clock else "Home's read"
    n = TONE_HOME_READ_MAX_AGE_MINUTES
    if why == WHY_HOME_AGED:
        return f"{home} is more than {n:g} minutes old and a fresh S&P 500 read failed."
    if why == WHY_HOME_OTHER_SESSION:
        return f"{home} is from an earlier session and a fresh S&P 500 read failed."
    if why == WHY_HOME_UNREADABLE:
        return f"{home} has no readable time and a fresh S&P 500 read failed."
    if why == WHY_HOME_FAILED:
        return ("🏠 Home's Brief couldn't read the S&P 500 this run and a fresh "
                "S&P 500 read failed.")
    if why == WHY_HOME_NOT_TODAY:
        return "🏠 Home's Brief isn't built today and the S&P 500 read failed."
    return "The S&P 500 read failed."


def session_phrase(ts) -> "str | None":
    """``session_label.move_when_phrase`` ("in the last session (Wed Nov 25)")
    when the read's S&P move belongs to an earlier session than its own date,
    else None (today's move, no usable time, or a non-datetime).  Never raises."""
    try:
        as_of = ts.get("as_of") if isinstance(ts, dict) else None
        if not isinstance(as_of, datetime):
            return None
        d = as_of.astimezone(_ET) if _aware(as_of) else as_of
        is_today, _day = session_label.price_move_session(d)
        if is_today:
            return None
        return session_label.move_when_phrase(d)
    except Exception:
        return None
