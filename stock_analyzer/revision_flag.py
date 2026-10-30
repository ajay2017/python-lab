"""Pre-earnings analyst rating-change flag: the one shared rule and wording.

Owner decision 2026-10-30 (finding #22): the analyst-revision signal is a
WATCH-level flag on every surface. It never sizes a trim; it is shadow-measured
before it may ever size one again.

The data is RATING changes, not earnings-estimate revisions: `bundle["revisions"]`
(built from Yahoo `upgrades_downgrades`) counts upgrades + initiations, downgrades
and `net` = upgrades - downgrades over 90 days. Every sentence here says "rating
changes" and never claims estimates were cut.

Every surface (Catalyst Watch playbook, Brief, Signals) must use this module so
the rule and the wording are identical everywhere. Pure; never raises.
"""
from __future__ import annotations

import math
from numbers import Real

from stock_analyzer.constants import (
    EARNINGS_HOLD_OR_ADD_MIN_NET_REVISIONS,
    EARNINGS_REVISION_WATCH_NET,
)

_TAIL = (
    " These are rating changes, not earnings-estimate cuts. Flagged to watch, "
    "not acted on: the app is recording whether this predicts a weak report "
    "and won't size a trim from it unless that record supports it."
)
_AMBER = "#f59e0b"
_GREY = "#9ca3af"


def _num(v):
    """int of a finite real number, else None (bools rejected)."""
    if isinstance(v, bool) or not isinstance(v, Real):
        return None
    try:
        if not math.isfinite(v):
            return None
        return int(v)
    except Exception:
        return None


def _unknown() -> dict:
    return {"state": "unknown", "net": None, "ups": None, "downs": None,
            "window_days": None}


def read(bundle) -> dict:
    """Read the 90d rating-change counts from a bundle. Never raises."""
    try:
        if not isinstance(bundle, dict):
            return _unknown()
        rev = bundle.get("revisions")
        if not isinstance(rev, dict) or not rev:
            return _unknown()
        if rev.get("basis") == "consensus_snapshot":
            return _unknown()
        net = _num(rev.get("net"))
        if net is None:
            return _unknown()
        return {
            "state": "ok",
            "net": net,
            "ups": _num(rev.get("upgrades_90d")),
            "downs": _num(rev.get("downgrades_90d")),
            # Missing key (cached bundles) = today's producer behaviour, 90d.
            "window_days": _num(rev.get("window_days")) if "window_days" in rev else 90,
        }
    except Exception:
        return _unknown()


def _ok(r) -> bool:
    return isinstance(r, dict) and r.get("state") == "ok" and _num(r.get("net")) is not None


def is_flagged(r) -> bool:
    """True when the reading is ok and net is at or below the watch bound."""
    try:
        return _ok(r) and _num(r["net"]) <= EARNINGS_REVISION_WATCH_NET
    except Exception:
        return False


def _win(r) -> bool:
    """True when the counts cover a 90-day window (missing key = 90)."""
    try:
        return r.get("window_days", 90) == 90
    except Exception:
        return False


def flag_line(r) -> str | None:
    """The Watch-flag sentence, or None when not flagged. Plain text."""
    if not is_flagged(r):
        return None
    net = _num(r["net"])
    ups, downs = _num(r.get("ups")), _num(r.get("downs"))
    span = "the last 90 days" if _win(r) else "recent months"
    if ups is None or downs is None or ups - downs != net:
        head = f"Analyst ratings: net {net:+d} in rating changes over {span}."
    else:
        d = f"{downs} downgrade" + ("" if downs == 1 else "s")
        u = f"{ups} upgrade or initiation" if ups == 1 else f"{ups} upgrades or initiations"
        head = f"Analyst ratings: {d} vs {u} in {span} (net {net:+d})."
    return head + _TAIL


def revisions_phrase(r) -> str:
    """Short phrase for the playbook's HOLD sentence."""
    if not _ok(r):
        return "unavailable"
    net = _num(r["net"])
    if is_flagged(r):
        return f"net {net:+d} (flagged to watch)"
    if EARNINGS_REVISION_WATCH_NET < net < EARNINGS_HOLD_OR_ADD_MIN_NET_REVISIONS:
        return "neutral"
    return f"net {net:+d}"


def checklist_line(r) -> tuple[str, str, str]:
    """(emoji, text, css colour) for the Signals checklist."""
    line = flag_line(r)
    if line is not None:
        return ("⚑", line, _AMBER)
    if _ok(r):
        return ("•", f"Analyst rating changes ({'90d' if _win(r) else 'recent'}): net {_num(r['net']):+d}.", _GREY)
    return ("•", "Analyst rating changes unavailable.", _GREY)
