"""An unreadable macro-event calendar holds new buys (buy-check C4m,
docs/plans/entry-fail-closed-c4m-spec.md; owner Q1-Q4 2026-11-27).

The macro calendar (G-07) decides whether a CPI / jobs / Fed release is due in
the next ``MACRO_IMMINENT_DAYS`` days.  When the calendar could not be built
(``None``), "no event" is not a fact: Grow Today holds the whole day
(``macro_unread``) and the Watchlist / email / Analysis hold the name.  An empty
list ``[]`` is a real "checked, none" and never a hold.  A FRED failure alone is
NOT unread (the hand-typed static dates are the source of truth).

Pure: no clock, no streamlit, no db.  Imports only ``constants``,
``macro_sector_gate`` and ``tone_freshness``; it must NOT import
``entry_eligibility`` (that module imports this one).
"""
from __future__ import annotations

from stock_analyzer import macro_sector_gate as _msg
from stock_analyzer import tone_freshness as _tf
from stock_analyzer.constants import BRIEF_AUTO_REFRESH_MINUTES, MACRO_IMMINENT_DAYS

CHECK_UNREAD = "macro_unread"


def events_or_none(raw):
    """A list of events (``[]`` included) as is, else None (= couldn't be read)."""
    return raw if isinstance(raw, list) else None


def events_unread(events) -> bool:
    """True when the calendar was not read (anything but a list)."""
    return not isinstance(events, list)


def map_unread(block_map) -> bool:
    """True when there is no usable macro block map (the one "no usable map"
    predicate, read from ``macro_sector_gate.decide``)."""
    try:
        return _msg.decide(None, block_map)["state"] == "unchecked"
    except Exception:
        return True


def unread_reason() -> str:
    """The short hold reason when the calendar couldn't be read."""
    return "the macro-event calendar couldn't be read"


def entry_hold() -> dict:
    """The per-name hold ``check_macro`` returns when the macro map is unusable
    (an abstention: no ledger gate)."""
    return {
        "kind": "hold", "check": CHECK_UNREAD, "gate_id": None,
        "reason": unread_reason(),
        "clears_when": "the macro calendar can be read — re-checked every time you open this page",
        "gate_value": None, "gate_threshold": None,
    }


def grow_hold(tone_hold=None) -> dict:
    """Grow Today's whole-day hold dict (strings only: JSON-safe for the Lock
    snapshot).  A ``tone_not_today`` ``tone_hold`` is merged: the banner names
    both conditions and the session is copied."""
    reason = unread_reason()
    clears = "when the macro calendar is read again"
    session = None
    tail = ""
    if isinstance(tone_hold, dict) and tone_hold.get("check") == _tf.CHECK_NOT_TODAY:
        t_clears = tone_hold.get("clears_when")
        if t_clears:
            tail = f", and {t_clears}"
            clears = f"{clears}{tail}"
        session = tone_hold.get("session")
    return {
        "check": CHECK_UNREAD,
        "reason": reason,
        "clears_when": clears,
        "banner": (f"⏸ New picks, ETF picks and adds are held: {reason}, so Grow "
                   f"Today can't tell whether a CPI, jobs or Fed release is due in "
                   f"the next {MACRO_IMMINENT_DAYS} days. They return {clears}."),
        "session": session,
    }


def retry_caption(hold) -> "str | None":
    """The retry hint under Grow Today's banner for a macro hold, else None."""
    if isinstance(hold, dict) and hold.get("check") == CHECK_UNREAD:
        return (f"🔄 Refresh macro re-reads the calendar; the Brief also rebuilds on "
                f"its own every {BRIEF_AUTO_REFRESH_MINUTES} minutes in pre-market "
                f"and market hours.")
    return None


def failure_line(entry_context, entry_run=None) -> "str | None":
    """The cron failure line when this run's macro calendar was unread, else None.

    Fires when ``entry_context`` is a dict that HAS a ``macro_events`` key equal
    to None, or ``entry_run["macro_available"]`` is False.  Never raises."""
    try:
        unread = (isinstance(entry_context, dict) and "macro_events" in entry_context
                  and entry_context["macro_events"] is None)
        if not unread and isinstance(entry_run, dict) and entry_run.get("macro_available") is False:
            unread = True
        if unread:
            return ("macro_unread: the macro calendar couldn't be read — no buy-list, "
                    "intraday pick or Watchlist Enter Now this run")
        return None
    except Exception:
        return None
