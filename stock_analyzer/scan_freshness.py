"""
The one rule for whether the saved market scan may size today's new picks.

THE RULE (owner 2026-11-26: U5 = S1, U6 = yes, U7 = no in-session limit, U8 = movers held too)
    A saved scan sizes 🏠 Grow Today's new picks (and the 11:30 intraday lane's
    picks) only when it was taken at or after TODAY's 09:30 ET open on a
    trading day. A scan saved before the open, on an earlier session, or read
    on a weekend / holiday / pre-open visit is STALE: its prices are another
    session's, so it must not size a buy. Today's post-open scan stays fresh
    after hours until midnight ET (U6). There is no maximum age inside the
    session (U7).

THE FAIL DIRECTIONS
    - No meta, a naive timestamp, a date-only value (the old app meta stored a
      bare date), an unparseable value, a timestamp more than 5 minutes in the
      future, or a naive `now_et` is UNKNOWN: never guess a freshness, hold.
    - Any exception inside `decide` fails closed to UNKNOWN / `unreadable`.

Pure: no clock (callers pass an aware ET `now_et`), no I/O, never raises.
Callers: `daily_briefing._grow_today` (through `build_daily_briefing`'s
`scan_state`), app.py's Home build and Refresh Signals tooltip, and the cron
intraday lane (`cron_runner._run_intraday`).
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from stock_analyzer import session_label
from stock_analyzer.data import is_trading_day
from stock_analyzer.session_label import SESSION_OPEN

STATE_FRESH, STATE_STALE, STATE_UNKNOWN = "fresh", "stale", "unknown"

_ET = ZoneInfo("America/New_York")
_FUTURE_SLACK = timedelta(minutes=5)   # clock-skew allowance, not a policy value
_NEXT_SESSION_WALK_DAYS = 10           # bounded so a broken calendar can never loop forever


def _unknown(why: str) -> dict:
    return {"state": STATE_UNKNOWN, "why": why, "scanned_at_et": None,
            "scan_session": None, "next_session": None}


def _parse_scanned_at(value) -> datetime | None:
    """An aware datetime, or an ISO string carrying an offset (a trailing Z is
    accepted). Everything else (naive, date-only, garbage) reads None."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s[-1] in ("Z", "z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None
    else:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return None
    return dt


def _next_session_from(today: date) -> date | None:
    """The first trading day on or after `today` (the caller's `now_et` is
    always before midnight of `today`, so a trading `today` is still ahead or
    under way); None when none is found inside the bounded walk."""
    d = today
    for _ in range(_NEXT_SESSION_WALK_DAYS):
        if is_trading_day(d):
            return d
        d = d + timedelta(days=1)
    return None


def decide(scan_meta, now_et: datetime) -> dict:
    """{"state": "fresh" | "stale" | "unknown",
        "why": "ok" | "pre_open" | "closed_day" | "older_session" |
               "before_open_scan" | "no_meta" | "unreadable",
        "scanned_at_et": datetime | None, "scan_session": date | None,
        "next_session": date | None}. Never raises."""
    try:
        if not isinstance(scan_meta, dict) or not scan_meta.get("scanned_at"):
            return _unknown("no_meta")
        if (not isinstance(now_et, datetime) or now_et.tzinfo is None
                or now_et.tzinfo.utcoffset(now_et) is None):
            return _unknown("unreadable")
        now_local = now_et.astimezone(_ET)
        parsed = _parse_scanned_at(scan_meta.get("scanned_at"))
        if parsed is None:
            return _unknown("unreadable")
        if parsed > now_local + _FUTURE_SLACK:
            return _unknown("unreadable")
        scanned_et = parsed.astimezone(_ET)
        scan_session = scanned_et.date()
        today = now_local.date()

        def _out(state: str, why: str) -> dict:
            return {
                "state": state, "why": why,
                "scanned_at_et": scanned_et, "scan_session": scan_session,
                "next_session": (None if state == STATE_FRESH
                                 else _next_session_from(today)),
            }

        same_day = scan_session == today
        trading_today = is_trading_day(today)
        if same_day and trading_today and scanned_et.time() < SESSION_OPEN:
            return _out(STATE_STALE, "before_open_scan")
        is_today, _ = session_label.price_move_session(now_local)
        if is_today:
            if same_day:
                return _out(STATE_FRESH, "ok")
            return _out(STATE_STALE, "older_session")
        if trading_today:
            return _out(STATE_STALE, "pre_open")
        return _out(STATE_STALE, "closed_day")
    except Exception:
        return _unknown("unreadable")


def should_replace(current_meta, cached_meta) -> bool:
    """True only when `cached_meta`'s readable `scanned_at` is strictly newer
    than `current_meta`'s (a current meta with no readable `scanned_at` is
    replaced by any readable cached one). Never raises (False on error)."""
    try:
        cached = _parse_scanned_at(
            cached_meta.get("scanned_at") if isinstance(cached_meta, dict) else None)
        if cached is None:
            return False
        current = _parse_scanned_at(
            current_meta.get("scanned_at") if isinstance(current_meta, dict) else None)
        if current is None:
            return True
        return cached > current
    except Exception:
        return False


def _fmt_time(t: time) -> str:
    """9:30 AM (no zero-padded hour; %-I fails on Windows)."""
    hour12 = t.hour % 12 or 12
    return f"{hour12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def fmt_dt(dt: datetime) -> str:
    """Wed Nov 25, 9:47 AM ET."""
    return f"{dt.strftime('%a %b')} {dt.day}, {_fmt_time(dt.time())} ET"


def _fmt_date(d: date) -> str:
    """Mon Nov 30."""
    return f"{d.strftime('%a %b')} {d.day}"


def hold_banner(decision) -> str | None:
    """The visible reason a held scan sizes nothing. None when fresh or when
    `decision` isn't a dict. Any stale / unknown shape that can't name its scan
    time reads the unknown wording (fail closed)."""
    try:
        if not isinstance(decision, dict):
            return None
        state = decision.get("state")
        if state == STATE_FRESH:
            return None
        why = decision.get("why")
        scanned = decision.get("scanned_at_et")
        open_txt = _fmt_time(SESSION_OPEN)
        if state == STATE_STALE and isinstance(scanned, datetime):
            when = fmt_dt(scanned)
            if why == "older_session":
                return (
                    "⏸ No new-position sizes yet today. The latest saved scan is "
                    f"from {when}, not today's session, so its prices are that "
                    "session's. New picks return after today's scan (about 9:45 AM "
                    "ET) or a 🔄 Refresh Signals once the market is open."
                )
            if why == "before_open_scan":
                return (
                    "⏸ No new-position sizes yet today. The latest saved scan ran "
                    f"at {when}, before the {open_txt} ET open, so its prices are "
                    "the last session's. New picks return after today's scan (about "
                    "9:45 AM ET) or a 🔄 Refresh Signals once the market is open."
                )
            if why == "pre_open":
                return (
                    f"⏸ No new-position sizes before the open. The market opens at "
                    f"{open_txt} ET; the latest saved scan is from {when}. New "
                    "picks return after today's scan (about 9:45 AM ET)."
                )
            if why == "closed_day":
                nxt = decision.get("next_session")
                nxt_txt = (f"next scan {_fmt_date(nxt)} about 9:45 AM ET"
                           if isinstance(nxt, date)
                           else "next scan at the next session's open")
                return (
                    "⏸ Market closed today, so no new-position sizes. The latest "
                    f"saved scan is from {when}; {nxt_txt}."
                )
        if why == "no_meta":
            return (
                "⏸ No new-position sizes: no saved scan to read yet. New picks "
                "return after today's scan (about 9:45 AM ET) or a 🔄 Refresh "
                "Signals once the market is open."
            )
        return (
            "⏸ No new-position sizes: couldn't tell when the saved scan ran. New "
            "picks return after the next scan or a 🔄 Refresh Signals once the "
            "market is open."
        )
    except Exception:
        return (
            "⏸ No new-position sizes: couldn't tell when the saved scan ran. New "
            "picks return after the next scan or a 🔄 Refresh Signals once the "
            "market is open."
        )


def priced_at_label(decision) -> str | None:
    """Fresh only: "Sizes priced at the 9:47 AM ET scan." Else None."""
    try:
        if not isinstance(decision, dict) or decision.get("state") != STATE_FRESH:
            return None
        scanned = decision.get("scanned_at_et")
        if not isinstance(scanned, datetime):
            return None
        return f"Sizes priced at the {_fmt_time(scanned.time())} ET scan."
    except Exception:
        return None


def signals_tooltip(decision, source=None) -> str | None:
    """The 🔄 Refresh Signals tooltip line for a readable scan, from the same
    decision that sizes picks (never a bare scan_date test). Fresh: "Signals
    from today's auto-scan at 9:47 AM ET. ..."; stale: names the scan's own
    date and time. None when the decision carries no readable scan time, so the
    caller falls back to its generic line. `source` "cron" reads as auto-scan."""
    try:
        if not isinstance(decision, dict):
            return None
        scanned = decision.get("scanned_at_et")
        if not isinstance(scanned, datetime):
            return None
        if decision.get("state") == STATE_FRESH:
            phrase = "auto-scan" if source == "cron" else "scan"
            return (f"Signals from today's {phrase} at {_fmt_time(scanned.time())} ET. "
                    "Click to re-score on live prices intraday.")
        return (f"Signals from the {fmt_dt(scanned)} scan — click to score "
                "today's price action.")
    except Exception:
        return None


def _scan_origin(scan_hold) -> str:
    """"from the Wed Nov 25 scan", or "from the saved scan" when the held scan's
    session date can't be read."""
    try:
        raw = scan_hold.get("scan_session") if isinstance(scan_hold, dict) else None
        if isinstance(raw, str) and raw:
            return f"from the {_fmt_date(date.fromisoformat(raw[:10]))} scan"
    except Exception:
        pass
    return "from the saved scan"


def leads_heading(scan_hold, n: int) -> str:
    """Heading of the unsized-leads expander under a scan hold (the grow
    result's `scan_hold` dict), naming the scan the names came from."""
    return f"Momentum leads — {_scan_origin(scan_hold)}, not checked ({n})"


def leads_note(scan_hold) -> str:
    """Caption under that heading: no verdict, no size, why."""
    return (f"These names are {_scan_origin(scan_hold)}, which can't size a pick "
            "today, so they weren't checked and carry no verdict — not buy calls.")
