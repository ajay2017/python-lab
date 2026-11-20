"""The line the bottom dock shows on every page other than 🏠 Home (pure, no streamlit).

🏠 Home builds the Brief, the Act Today count and the Notices stack; the other
pages don't. The dock's ⚠️ Notices tab there tells the owner ONE dated thing:
what Home's last finished build said (the Act Today count and the Needs-a-look
line), and how far to trust it. This module decides both halves:

* `build_digest` normalizes what Home knew at the end of a build into a small
  dict (the `_home_dock_digest` session key, registered in `coord_freshness`);
* `digest_line` turns that dict plus its freshness into the one line, its tier
  and its "may be out of date" caveat.

The three-state rule of the coordination caches holds: no digest (`None`) means
Home has not finished a build this session, and that is NEVER worded as an
all-clear; an `offline` Act Today means the Brief could not be built and carries
a count of `None`, never 0. A stale or earlier-day digest is never shown as a
plain info line: it is raised to a warning and says why. Display only: it
decides what other pages SAY about Act Today, it changes no recommendation.
Date math is in America/New_York (`market_time.ET`); a session can span
midnight or a weekend.
"""
from __future__ import annotations

from datetime import datetime

from stock_analyzer import coord_freshness
from stock_analyzer.market_time import ET

ACT, CLEAR, OFFLINE, NO_HOLDINGS = "act", "clear", "offline", "no_holdings"
_VIEW_STATES = ("act", "clear", "offline")
_TIERS = ("info", "warning", "error")
_RANK = {"info": 0, "warning": 1, "error": 2}

NO_DIGEST_TEXT = (
    "🏠 Home's latest build didn't finish (or hasn't run) this session, so Act "
    "Today and notices weren't checked here. Open 🏠 Home to check."
)
UNKNOWN_TIME = "an unknown time"


def _as_count(value) -> int | None:
    """A real int count, or None (a bool is not a count)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def build_digest(
    act_view,
    notices_summary,
    notices_headline,
    built_at_iso,
    *,
    no_holdings: bool = False,
) -> dict:
    """What 🏠 Home knew when a build finished.

    `act_view` is `act_today_view()`'s result (or anything else, treated as not
    checked). Anything that is not a dict, carries an unknown state, or says
    "offline" is `offline` with a count of None. A non-empty `active` list or an
    `n_active` above zero is `act` with the int count; otherwise `clear`. A view
    that claims "act" without any active item or count cannot be told from a
    broken one, so it reads `offline` rather than as a calm all-clear.
    `no_holdings=True` is its own state: nothing exists to act on, and it is not
    an outage either.
    """
    act, n_act = _normalize_act(act_view, no_holdings)

    tier = None
    headline = None
    if isinstance(notices_summary, dict):
        raw_tier = notices_summary.get("tier")
        tier = raw_tier if raw_tier in _TIERS else None
    if isinstance(notices_headline, str) and notices_headline.strip():
        headline = notices_headline.strip()
    if tier is None or headline is None:
        # A summary without its headline (or the reverse) can't be shown as
        # half a claim; both read "nothing fired".
        tier = headline = None

    return {
        "built_at": built_at_iso if isinstance(built_at_iso, str) else None,
        "act": act,
        "n_act": n_act,
        "notices_tier": tier,
        "notices_headline": headline,
    }


def _normalize_act(act_view, no_holdings: bool) -> tuple[str, int | None]:
    if no_holdings:
        return NO_HOLDINGS, None
    if not isinstance(act_view, dict):
        return OFFLINE, None
    state = act_view.get("state")
    if state not in _VIEW_STATES or state == OFFLINE:
        return OFFLINE, None
    active = act_view.get("active")
    n_active = _as_count(act_view.get("n_active"))
    n_list = len(active) if isinstance(active, (list, tuple)) else 0
    if n_list or (n_active is not None and n_active > 0):
        count = n_active if (n_active is not None and n_active > 0) else n_list
        return ACT, count
    if state == ACT:
        return OFFLINE, None
    return CLEAR, 0


def _parse_et(built_at) -> datetime | None:
    if not isinstance(built_at, str):
        return None
    try:
        dt = datetime.fromisoformat(built_at)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None  # a clock reading with no zone can't be placed on an ET day
    return dt.astimezone(ET)


def _time_label(built: datetime, now_et_dt: datetime) -> str:
    clock = f"{built.strftime('%I:%M %p').lstrip('0')} ET"
    if built.date() == now_et_dt.date():
        return clock
    return f"{built.strftime('%b')} {built.day}, {clock}"


def _act_text(act: str, n_act) -> str:
    if act == ACT:
        return f"🔴 {n_act} Act Today"
    if act == OFFLINE:
        return "Act Today was NOT checked (the Brief couldn't be built)"
    if act == NO_HOLDINGS:
        return "no holdings yet"
    return "✅ nothing to act on"


def _notices_text(headline) -> str:
    if not isinstance(headline, str) or not headline.strip():
        return " · no notices"
    text = headline.strip()
    if text.startswith("·"):
        text = text[1:].strip()   # the quiet "· N notes" form reads "· N notes" once
    return f" · {text}" if text else " · no notices"


def _max_tier(a: str, b: str) -> str:
    return a if _RANK[a] >= _RANK[b] else b


def digest_line(digest, freshness, now_et_dt) -> dict:
    """{"tier": "error"|"warning"|"info", "text": str} for the dock's one line.

    `freshness` is `coord_freshness.classify(...)` for `_home_dock_digest`
    ("fresh" / "stale" / "absent"). Plain markdown text: no raw HTML, no `$`.
    """
    if not isinstance(digest, dict):
        return {"tier": "info", "text": NO_DIGEST_TEXT}

    now = now_et_dt if isinstance(now_et_dt, datetime) else None
    if now is not None:
        now = now.astimezone(ET) if now.tzinfo is not None else ET.localize(now)
    built = _parse_et(digest.get("built_at"))
    time_label = UNKNOWN_TIME if built is None or now is None else _time_label(built, now)

    act = digest.get("act")
    act = act if act in (ACT, CLEAR, OFFLINE, NO_HOLDINGS) else OFFLINE
    n_act = _as_count(digest.get("n_act"))
    if act == ACT and n_act is None:
        act = OFFLINE   # a call to act with no count can't be worded; say not checked

    text = f"🏠 Home at {time_label}: {_act_text(act, n_act)}" + _notices_text(
        digest.get("notices_headline"))

    tier = {ACT: "error", OFFLINE: "warning"}.get(act, "info")
    notices_tier = digest.get("notices_tier")
    if notices_tier in _TIERS:
        tier = _max_tier(tier, notices_tier)

    reason = None
    if freshness == coord_freshness.STALE:
        reason = "the book changed since (a trade or holdings update)"
    elif built is None or now is None:
        reason = "when it was built is unknown"
    elif built.date() < now.date():
        reason = "it is from an earlier day"
    elif freshness != coord_freshness.FRESH:
        reason = "it isn't marked current"
    if reason is not None:
        text += f" · may be out of date: {reason}"
        tier = _max_tier(tier, "warning")

    return {"tier": tier, "text": text}
