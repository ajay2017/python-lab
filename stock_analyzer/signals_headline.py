"""Signals page headline: act / attention / all-clear (bug register Bal / Reb).

Pure: no streamlit, no db. The 📡 Signals banner used to read only the action
cards and showed a green all-clear over breached stops. The decision lives here.

The "Today's Brief says sell" claim is made only when the Brief is built and
still lists the breach as active: `brief_state` is "ok" / "offline" /
"not_built", and `active_breach` / `resolved` come from `act_today_view`
(the same read Home and Summary use). A breach the Brief resolved (live price
recovered) never reads as an act call and never falls back to the green.

An action is NOT actionable when it is a trim that sizes under one share; that
reuses `decision_context.is_unsized_trim`, the predicate `act_today_n` uses.
"""

from __future__ import annotations

from stock_analyzer.decision_context import is_unsized_trim
from stock_analyzer.stop_breach_text import BRIEF_SELL_CLAUSE

CLEAR_TEXT = "✅ Portfolio is well-balanced — no signal-driven actions needed at this time."
_RULE_CLAUSE = "the stop rule says sell at the next open"
_OFFLINE_TAIL = "Today's Brief couldn't be built this run — open 🏠 Home."
_NOT_BUILT_TAIL = "Open 🏠 Home to build today's Brief."


def _norm(t) -> str:
    return str(t or "").strip().upper()


def _items(view: dict, key: str) -> list:
    v = view.get(key)
    return v if isinstance(v, list) else []


def brief_context(daily_brief, brief_offline, act_view) -> tuple[str, frozenset, frozenset]:
    """(brief_state, active_breach, resolved) from the Brief and its act_today_view."""
    if daily_brief is None:
        return "not_built", frozenset(), frozenset()
    if brief_offline or not isinstance(act_view, dict) or act_view.get("state") == "offline":
        return "offline", frozenset(), frozenset()
    active = frozenset(
        _norm(i.get("ticker")) for i in _items(act_view, "active")
        if isinstance(i, dict) and i.get("kind") == "stop_breach"
    ) - {""}
    resolved = frozenset(
        _norm(i.get("ticker")) for i in _items(act_view, "resolved") if isinstance(i, dict)
    ) - {""}
    return "ok", active, resolved


def _tail(brief_state: str) -> str:
    return {"offline": _OFFLINE_TAIL, "not_built": _NOT_BUILT_TAIL}.get(brief_state, "")


def decide(actions, alert_list, offline: bool, *, brief_state: str,
           active_breach=frozenset(), resolved=frozenset()) -> dict:
    """Return {"kind": offline|act|attention|clear|resolved|none, "text", "note"}."""
    if offline:
        return {"kind": "offline", "text": None, "note": None}
    alerts = [a for a in (alert_list or []) if isinstance(a, dict)]
    ok = brief_state == "ok"
    active_breach = frozenset(_norm(t) for t in active_breach)
    resolved = frozenset(_norm(t) for t in resolved) if ok else frozenset()

    breached = [a for a in alerts if a.get("breached")]
    b_names = {_norm(a.get("ticker")) for a in breached} - {""}
    anon = any(not _norm(a.get("ticker")) for a in breached)
    act = b_names - resolved
    res = b_names & resolved

    note = None
    if res:
        note = (f"ℹ️ {', '.join(sorted(res))}: stop breach resolved — live price "
                "recovered above the stop; see 🏠 Home.")

    if act or anon:
        who = f" {', '.join(sorted(act))}" if act else ""
        word = "stops breached" if len(act) > 1 else "stop breached"
        if ok and not anon and act <= active_breach:
            text = f"⛔ Act first:{who} — {word}; {BRIEF_SELL_CLAUSE}."
        else:
            text = f"⛔ Act first:{who} — {word}; {_RULE_CLAUSE}."
            if not ok:
                text += " " + _tail(brief_state)
        return {"kind": "act", "text": text, "note": note}

    n_danger = sum(1 for a in alerts if a.get("level") == "danger" and not a.get("breached"))
    if n_danger:
        text = ("🔴 1 danger alert below — review it." if n_danger == 1
                else f"🔴 {n_danger} danger alerts below — review each.")
        return {"kind": "attention", "text": text, "note": note}

    if not any(not is_unsized_trim(a) for a in (actions or [])):
        if res:
            return {"kind": "resolved", "text": None, "note": note}
        n_warn = sum(1 for a in alerts if a.get("level") == "warning")
        if n_warn == 0:
            return {"kind": "clear", "text": CLEAR_TEXT, "note": None}
        noun = "warning" if n_warn == 1 else "warnings"
        return {"kind": "clear", "note": None, "text":
                f"✅ No signal-driven actions needed — {n_warn} {noun} below to monitor."}
    return {"kind": "none", "text": None, "note": note}


def _is_resolved(alert: dict, brief_state: str, resolved) -> bool:
    return (brief_state == "ok" and bool(alert.get("breached"))
            and _norm(alert.get("ticker")) in {_norm(t) for t in resolved})


def effective_level(alert: dict, *, brief_state: str, resolved=frozenset()) -> str:
    """The alert's level, with a breach the Brief resolved shown as info."""
    return "info" if _is_resolved(alert, brief_state, resolved) else alert.get("level")


def split_alert_levels(alert_list, *, brief_state: str, resolved=frozenset()):
    """(danger, warning, info) lists using effective_level."""
    out = {"danger": [], "warning": [], "info": []}
    for a in alert_list or []:
        lvl = effective_level(a, brief_state=brief_state, resolved=resolved)
        if lvl in out:
            out[lvl].append(a)
    return out["danger"], out["warning"], out["info"]


def alert_line(alert: dict, *, brief_state: str, resolved=frozenset(),
               active_breach=frozenset()) -> str:
    """The alert's msg, corrected for the Brief's state when it is a breached stop."""
    msg = alert.get("msg", "")
    if not alert.get("breached"):
        return msg
    tk = _norm(alert.get("ticker"))
    if _is_resolved(alert, brief_state, resolved):
        return (f"🟢 **{tk}** breached its stop earlier, but the live price has "
                "recovered above it — see 🏠 Home.")
    if brief_state != "ok":
        return msg.replace(BRIEF_SELL_CLAUSE,
                           f"{_RULE_CLAUSE} (Today's Brief isn't available this run)")
    if tk and tk in {_norm(t) for t in active_breach}:
        return msg
    # Brief built but carries no active stop-breach card for this name (e.g. a
    # locked morning snapshot): credit the stop rule, not the Brief.
    return msg.replace(BRIEF_SELL_CLAUSE, _RULE_CLAUSE)
