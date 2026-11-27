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
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from stock_analyzer import session_label
from stock_analyzer.data import is_trading_day
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
        return ("🏠 Home's Brief couldn't read the S&P 500 this run, so its new "
                "picks are held too.")
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


# ── Grow Today's own hold (buy-check C4b) ────────────────────────────────────
# `grow_entry_hold` is the ONE decision "Grow Today may not make a new-position,
# ETF or add call right now".  It reads the market context Home / the cron built
# and the date the Brief is for; it reads no clock.
CHECK_UNREAD = "tone_unread"
CHECK_NOT_TODAY = "tone_not_today"

_NEXT_SESSION_WALK_DAYS = 10   # bounded so a broken calendar can never loop forever


def _open_text() -> str:
    t = session_label.SESSION_OPEN
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'} ET"


def _day_text(d) -> str:
    """"Wed Nov 25" (no %-d: Windows-safe)."""
    return f"{d.strftime('%a %b')} {d.day}"


def _next_session_after(today: date) -> "date | None":
    """The first trading day strictly after `today`, or None inside the bounded walk."""
    d = today
    for _ in range(_NEXT_SESSION_WALK_DAYS):
        d = d + timedelta(days=1)
        if is_trading_day(d):
            return d
    return None


def tone_unread(market_context) -> bool:
    """True when the context says the S&P 500 read didn't happen: not a dict,
    ``tone_available`` False, or True with a % that isn't a finite number, or a
    ``tone_read_at`` key that is present but not an aware datetime.  A context
    with no ``tone_available`` key at all (legacy / tests) is NOT unread."""
    if not isinstance(market_context, dict):
        return True
    avail = market_context.get("tone_available")
    if avail is False:
        return True
    if avail is True and not _finite(market_context.get("sp500_pct")):
        return True
    if "tone_read_at" in market_context and not _aware(market_context.get("tone_read_at")):
        return True
    return False


def _unread_hold() -> dict:
    reason = unread_reason()
    clears = "once the S&P 500 read succeeds"
    return {
        "check": CHECK_UNREAD,
        "reason": reason,
        "clears_when": clears,
        "banner": (f"⏸ New picks, ETF picks and adds are held: {reason}, so Grow "
                   f"Today can't tell a flat day from a falling one. They return {clears}."),
        "session": None,
    }


CLEARS_READ_AGAIN = "when the market move is read again"


def _not_today_hold(read_et, today_d) -> "dict | None":
    """The `tone_not_today` hold for a read taken at `read_et` (an aware ET
    datetime) when the Brief is for `today_d`, or None when the read is from
    today's own session.  ONE wording helper for Grow Today's hold and the
    redeploy headline's deferral (`redeploy_grow_hold`)."""
    is_today, sess = session_label.price_move_session(read_et)
    if is_today and sess == today_d:
        return None
    sess_text = _day_text(sess) if isinstance(sess, date) else "an earlier session"
    read_day = read_et.date()
    if read_day != today_d:
        # A read dated before (or, across a midnight-straddling build, after)
        # `today`: its move is another day's, whatever the clock says now.
        which = "an earlier" if read_day < today_d else "a different"
        reason = (f"the market read is from {which} day — the latest move it "
                  f"carries is {sess_text}'s")
        clears = CLEARS_READ_AGAIN
    elif is_trading_day(today_d):
        reason = f"today's session hasn't opened yet — the latest move is {sess_text}'s"
        clears = f"after the {_open_text()} open"
    else:
        nxt = _next_session_after(today_d)
        reason = f"the market is closed today — the latest move is {sess_text}'s"
        clears = (f"on {_day_text(nxt)} after the open" if nxt is not None
                  else "at the next session's open")
    return {
        "check": CHECK_NOT_TODAY,
        "reason": reason,
        "clears_when": clears,
        "banner": f"⏸ No new picks, ETF picks or adds yet: {reason}. They return {clears}.",
        "session": sess.isoformat() if isinstance(sess, date) else None,
    }


def redeploy_grow_hold(home_grow_hold, tone_state, today) -> "dict | None":
    """The Grow Today hold the redeploy headline defers to (owner rule #14: the
    headline defers to Grow Today).  `home_grow_hold` (🏠 Home's Brief
    `grow_today["entry_hold"]`) when it is a dict; else, when `tone_state` is an
    available tone read whose `as_of` (an aware datetime) is not today's session,
    the same `tone_not_today` hold Grow Today would make from that read; else None
    (an unavailable tone has its own "couldn't be read" wording).

    A STALE Home hold is dropped: a `tone_not_today` hold from a pre-open /
    closed-day Home build is ignored when the page's own tone read is a post-open
    read of today's session (`price_move_session(as_of) == (True, today)`), because
    its "returns after the open" is no longer true (📡 Signals reads a cached Home
    Brief).  A Home `tone_unread` hold always passes through.  On 🏠 Home itself the
    hold and `tone_state` come from the same build, so a not-today hold never meets
    a post-open read and behaviour there is unchanged.  No clock; never raises."""
    try:
        today_d = today.date() if isinstance(today, datetime) else today
        as_of = tone_state.get("as_of") if isinstance(tone_state, dict) else None
        page_read = (
            as_of.astimezone(_ET)
            if isinstance(tone_state, dict) and tone_state.get("available") is True
            and _aware(as_of) else None
        )
        if isinstance(home_grow_hold, dict):
            if (home_grow_hold.get("check") == CHECK_NOT_TODAY and page_read is not None
                    and session_label.price_move_session(page_read) == (True, today_d)):
                return None
            return home_grow_hold
        if page_read is None:
            return None
        return _not_today_hold(page_read, today_d)
    except Exception:
        return None


def grow_entry_hold(market_context, today) -> "dict | None":
    """``None`` when Grow Today may call new entries, else a JSON-safe dict of
    strings ``{"check", "reason", "clears_when", "banner", "session"}``.

    ``tone_unread``: the S&P 500 read failed (or is unusable): never a flat day.
    ``tone_not_today`` (owner U2, 2026-11-27): the read's price-move session is
    not today's, i.e. a pre-open build, a weekend / holiday, or a read from an
    earlier day: the move it carries is another session's, so ETF picks and adds
    (the scan's picks are held by the scan-freshness gate) wait for today's open.
    A context with no ``tone_available`` and no ``tone_read_at`` key (legacy /
    tests) is not held.  ``today`` is a date; no clock is read here.  Never raises
    and fails CLOSED: a context it can't judge is an unread one."""
    try:
        today_d = today.date() if isinstance(today, datetime) else today
        if tone_unread(market_context):
            return _unread_hold()
        if "tone_read_at" not in market_context:
            return None            # legacy context: no read time to judge
        return _not_today_hold(market_context["tone_read_at"].astimezone(_ET), today_d)
    except Exception:
        return _unread_hold()


# ── Pure view builders (app.py renders what they return) ─────────────────────
def _count_setups(grow) -> int:
    n = 0
    if isinstance(grow, dict):
        for k in ("new_picks", "add_positions"):
            v = grow.get(k)
            n += len(v) if isinstance(v, (list, tuple)) else 0
    return n


def grow_count_text(grow) -> str:
    """The Today's Actions card's Grow Today line (no emoji): "Grow Today held"
    when Grow Today is holding every entry (never "0 Grow Today"), else
    "<n> Grow Today" over picks + adds."""
    if isinstance(grow, dict) and isinstance(grow.get("entry_hold"), dict):
        return "Grow Today held"
    return f"{_count_setups(grow)} Grow Today"


def grow_header_view(grow, tone) -> dict:
    """``{"held", "count", "subtitle", "show_cleared"}`` for the Grow Today header.

    ``count`` is the header suffix (" (held)", " (3 setups)" or ""); ``subtitle``
    the tone line (a hold names itself instead of any tone line);
    ``show_cleared`` whether "Cleared all 5 portfolio checks" may print (a bull
    tone only, never on a held day)."""
    g = grow if isinstance(grow, dict) else {}
    hold = g.get("entry_hold")
    if isinstance(hold, dict):
        why = hold.get("reason") or "the market move isn't usable"
        return {"held": True, "count": " (held)",
                "subtitle": f"New picks and adds are held: {why}.",
                "show_cleared": False}
    n = _count_setups(g)
    count = f" ({n} setup{'' if n == 1 else 's'})" if n else ""
    subtitle = ("Defer new entries — protect capital, reduce exposure." if tone == "bear" else
                "High-conviction only — elevated caution in effect." if tone == "flat" else "")
    return {"held": False, "count": count, "subtitle": subtitle,
            "show_cleared": tone == "bull"}


def tone_card_view(market_context, now_et) -> dict:
    """What the Home market-tone card shows, from the Brief's market context.

    ``{"tone_key", "label", "sp_line", "nq_line", "date_text", "is_stale",
    "last_close_text", "note"}``.  ``tone_key`` is "bull" / "bear" / "flat", or
    "unread" when the S&P read failed (then the label never says "Hold Steady —
    Mixed Market", the S&P line has no %, ``nq_line`` is None and ``note`` says the
    picks are held).  ``is_stale`` / ``last_close_text`` come from
    ``session_label.price_move_session`` (it knows holidays): True and the last
    completed session ("Wed Nov 25") before the open and on a closed day.
    ``date_text`` is today's date plus " · data as of ..." when stale.  Never raises."""
    ctx = market_context if isinstance(market_context, dict) else {}
    try:
        is_today, last = session_label.price_move_session(now_et)
    except Exception:
        is_today, last = True, None
    stale = not is_today
    last_text = last.strftime("%a %b %d") if stale and isinstance(last, date) else None
    try:
        date_text = now_et.strftime("%A, %B %d %Y")
    except Exception:
        date_text = ""
    if stale and last_text:
        date_text += f" · data as of {last_text}"
    base = {"is_stale": stale, "last_close_text": last_text, "date_text": date_text}
    if tone_unread(market_context):
        return {**base, "tone_key": "unread", "label": "⚠️ Market Move Not Read",
                "sp_line": "S&P 500 — not read", "nq_line": None,
                "note": ("Market move couldn't be read this run — new picks, ETF picks "
                         "and adds are held until it can be.")}
    tone = ctx.get("tone", "flat")
    tone = tone if tone in ("bull", "bear") else "flat"
    label = ("📈 Growth Mode — Markets Up" if tone == "bull" else
             "🛡️ Protect Mode — Markets Down" if tone == "bear" else
             "📊 Hold Steady — Mixed Market")
    sp = ctx.get("sp500_pct", 0.0)
    nq = ctx.get("nasdaq_pct", 0.0)
    return {**base, "tone_key": tone, "label": label,
            "sp_line": f"S&P 500 {float(sp):+.2f}%" if _finite(sp) else "S&P 500 — not read",
            "nq_line": f"Nasdaq {float(nq):+.2f}%" if _finite(nq) else None,
            "note": None}
