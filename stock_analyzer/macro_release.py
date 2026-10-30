"""Same-day macro release timing: has today's release already happened?

Pre-release advice ("reduce before the release", "hold through the print",
"be at your terminal") is wrong once a same-day release time has passed. The
Brief is memoized per composition, so the build cannot check the clock; these
pure helpers let the RENDER layer swap the wording instead.

Pure: no I/O, no clock reads. ``now`` is passed in (an aware or naive ET
datetime from ``market_time.now_et()``); an aware ``now`` is converted to ET.
"""
from __future__ import annotations

from stock_analyzer.market_time import ET

_TRIGGER_AFTER_RELEASE = "Released: check how it landed before acting on affected names."
# entry_eligibility.macro_block_map never lifts a same-day Fed Policy hold, so
# only that category may say new entries stay held.
_FED_HELD_TAIL = " New entries in affected names stay held today."
_FED_CATEGORY = "Fed Policy"


def _parse_hhmm(time_et):
    """Return (hour, minute) for a valid "HH:MM" string, else None."""
    if not isinstance(time_et, str):
        return None
    parts = time_et.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def release_passed(ev_date, time_et, now):
    """Has the release happened? False for a future day; True for a past day
    (e.g. a memoized Brief carried past midnight); on the event day None when
    the time is unknown, else whether ``now`` is at or past the release time.
    An aware ``now`` is converted to ET first."""
    try:
        if getattr(now, "tzinfo", None) is not None:
            now = now.astimezone(ET)
        today = now.date()
        if ev_date > today:
            return False
        if ev_date < today:
            return True
    except Exception:
        return False
    hm = _parse_hhmm(time_et)
    if hm is None:
        return None
    return (now.hour, now.minute) >= hm


def released_text(time_et) -> str:
    hm = _parse_hhmm(time_et)
    at = f" at {hm[0]:02d}:{hm[1]:02d} ET" if hm else ""
    return f"Released{at} — see 📅 Economic Calendar → 📊 Post-Event Results."


def refresh_macro_card(item, now):
    """Return a copy of a Brief macro card with post-release wording when its
    release has passed; otherwise ``item`` unchanged. Never raises."""
    try:
        if not isinstance(item, dict) or item.get("kind") != "macro":
            return item
        t = item.get("_event_time_et")
        if release_passed(item.get("_event_date"), t, now) is not True:
            return item
        out = dict(item)
        out["directive"] = released_text(t)
        _now = now.astimezone(ET) if getattr(now, "tzinfo", None) is not None else now
        # The entry gate holds a Fed Policy event's names only on its own day.
        _same_day = item.get("_event_date") == _now.date()
        out["trigger"] = _TRIGGER_AFTER_RELEASE + (
            _FED_HELD_TAIL
            if item.get("_event_category") == _FED_CATEGORY and _same_day else ""
        )
        return out
    except Exception:
        return item


def post_rules_caption(released, time_et) -> str:
    """Caption above the Post-Event Decision Rules; names the event's own time."""
    if released:
        return "The number is out: apply these rules now."
    hm = _parse_hhmm(time_et)
    when = f" ({hm[0]:02d}:{hm[1]:02d} ET)" if hm else ""
    return (
        f"These rules apply when the release drops{when}. "
        "Have a plan before the release — not after."
    )


def pre_event_view(released) -> dict:
    """Render flags for a Pre-Event Playbook: once the release has passed the
    PROTECT count is no longer a live instruction (no "action needed" delta,
    expander not auto-expanded, header says so)."""
    r = bool(released)
    return {
        "protect_delta_on": not r,
        "auto_expand_ok": not r,
        "header_suffix": " (pre-release call)" if r else "",
    }
