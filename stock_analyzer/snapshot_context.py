"""
🤖 AI Snapshot x Today's Brief.

The Snapshot LLM used to see only holdings / alerts / indices / news and was
asked for "3 concrete action items", so it invented trades the Brief never made
(2026-10-28: "Trim AAPL toward 7-8%" while the Brief said Watch). Now:

* ``brief_calls_view`` turns the Brief's own calls into structured rows;
* ``brief_calls_lines`` renders them as prompt CONTEXT (the model is told not to
  propose or contradict trades);
* ``section_html`` renders them as the Snapshot card's "what the app is telling
  you today" list DETERMINISTICALLY from the current Brief -- the LLM does not
  write that section and it is never cached;
* ``generate_allowed`` refuses to generate while the Brief is offline, so the
  Snapshot can never read as "no trades today" when nothing was checked.

Pure logic -- no Streamlit / no I/O; every field is read defensively and no
function raises.
"""

from __future__ import annotations

import hashlib
import re
from typing import Callable

from stock_analyzer.util import safe_text

HEADER = "## TODAY'S BRIEF (the app's decisions — context only, do not change or add to them)"
SECTION_TITLE = "WHAT THE APP IS TELLING YOU TODAY"
OFFLINE_NOTE = ("Today's Brief was not checked this run — no trade calls are "
                "available. This is not an all-clear.")
PAUSED_REASON = ("Today's Brief couldn't be built this run, so the AI Snapshot is "
                 "paused — it can't say which calls the app made. Reload once the "
                 "Brief loads.")
NO_TRADES = "No trades today per the Brief."
RESOLVED_CALL = "stop breach resolved — live price back above the stop; monitor"
_AWARE_CAP = 12
_WHY_CAP = 160
_TRIGGER_REF = re.compile(r"\s*[—–-]?\s*\(?see Trigger[^.)]*[.)]*", re.I)


def _clean(s) -> str:
    return " ".join(str(s).replace("**", "").split())


def _is_offline(act_view) -> bool:
    return not isinstance(act_view, dict) or act_view.get("state") == "offline"


def generate_allowed(act_view) -> tuple[bool, str]:
    """May the Snapshot be generated this render? Not while the Brief is offline."""
    if _is_offline(act_view):
        return False, PAUSED_REASON
    return True, ""


def _strip_trigger_ref(text: str, trigger) -> str:
    """Drop 'see Trigger below...' (the Trigger isn't shown here); append the
    item's own trigger text instead when it has one."""
    if "trigger" not in text.lower():
        return text
    out = _TRIGGER_REF.sub("", text)
    out = re.sub(r"\s+([.;,])", r"\1", out).strip()
    if out and not out.endswith("."):
        out += "."
    if isinstance(trigger, str) and trigger.strip():
        out = f"{out} Trigger: {_clean(trigger)}".strip()
    return out


def _subject(item: dict) -> str:
    t = item.get("ticker")
    if t:
        return str(t).upper()
    act = item.get("action")
    if isinstance(act, dict) and act.get("trim_ticker"):
        return str(act["trim_ticker"]).upper()
    for k in ("event", "headline"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return _clean(v)
    return "portfolio"


def _risk_parts(item: dict):
    """(subject, call) for a kind == 'risk' Act item: its ``ticker`` is
    attribution only; the real call is each flag's recommendation."""
    flags = item.get("risk_flags")
    flags = [f for f in flags if isinstance(f, dict)] if isinstance(flags, list) else []
    titles = [_clean(f["title"]) for f in flags if isinstance(f.get("title"), str) and f["title"].strip()]
    recs = [_clean(f["recommendation"]) for f in flags
            if isinstance(f.get("recommendation"), str) and f["recommendation"].strip()]
    subject = "portfolio"
    if titles:
        subject += " — " + " / ".join(titles)
    if item.get("ticker"):
        subject += f" (largest holding {str(item['ticker']).upper()})"
    if recs:
        call = " | ".join(recs)
    else:
        act = item.get("action")
        call = _clean(act) if isinstance(act, str) and act.strip() else "see the Brief card"
    return subject, call


def _why(item: dict) -> str:
    why = item.get("why")
    if not (isinstance(why, str) and why.strip()):
        why = item.get("reason")
    if not (isinstance(why, str) and why.strip()):
        return ""
    w = _clean(why)
    return w[:_WHY_CAP] + "…" if len(w) > _WHY_CAP else w


def _row(item, section: str, fmt_action: Callable):
    if not isinstance(item, dict):
        return None
    if item.get("kind") == "risk":
        subject, call = _risk_parts(item)
        return {"section": section, "subject": subject, "call": call, "why": ""}
    directive = item.get("directive")
    act = item.get("action")
    if isinstance(directive, str) and directive.strip():
        call = _clean(directive)
    elif isinstance(act, dict):
        try:
            _c, label, text = fmt_action(act)
            call = _clean(f"{label}: {_strip_trigger_ref(_clean(text), item.get('trigger'))}")
        except Exception:
            call = "see the Brief card"
    elif isinstance(act, str) and act.strip():
        call = _clean(act)
    else:
        call = "see the Brief card"
    head = item.get("headline")
    if (item.get("_source") == "review" and isinstance(head, str) and head.strip()
            and _clean(head) not in call):
        call = f"{_clean(head)} — {call}"
    return {"section": section, "subject": _subject(item), "call": call, "why": _why(item)}


def _rows(items, section, fmt_action) -> list[dict]:
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        try:
            r = _row(it, section, fmt_action)
        except Exception:
            r = None
        if r:
            out.append(r)
    return out


def _grow_rows(daily_brief) -> list[dict]:
    grow = daily_brief.get("grow_today") if isinstance(daily_brief, dict) else None
    grow = grow if isinstance(grow, dict) else {}
    rows = []
    for key, label in (("new_picks", "new pick"), ("add_positions", "add to held name")):
        items = grow.get(key)
        for it in (items if isinstance(items, list) else []):
            if not (isinstance(it, dict) and it.get("ticker")):
                continue
            call = label
            sig = it.get("signal")
            if isinstance(sig, str) and sig.strip():
                call += f" — {_clean(sig)}"
            sz = it.get("sizing")
            sh = sz.get("shares") if isinstance(sz, dict) else None
            if isinstance(sh, (int, float)) and not isinstance(sh, bool) and sh > 0:
                call += f", {int(sh)} share{'s' if int(sh) != 1 else ''}"
            rows.append({"section": "grow", "subject": str(it["ticker"]).upper(),
                         "call": call, "why": ""})
    if not rows:
        msg = grow.get("message")
        call = _clean(msg) if isinstance(msg, str) and msg.strip() else "no new picks or adds today"
        rows.append({"section": "grow", "subject": "Grow Today", "call": call, "why": ""})
    return rows


def brief_calls_view(act_view, daily_brief, fmt_action) -> dict:
    """{"state": "offline"|"ok", "rows": [{"section": "act"|"aware"|"grow",
    "subject", "call", "why"}]}. Offline -> no rows (never an all-clear)."""
    try:
        if _is_offline(act_view):
            return {"state": "offline", "rows": []}
        rows = _rows(act_view.get("active"), "act", fmt_action)
        aware = _rows(act_view.get("aware"), "aware", fmt_action)
        resolved = act_view.get("resolved")
        for it in (resolved if isinstance(resolved, list) else []):
            if isinstance(it, dict):
                aware.append({"section": "aware", "subject": _subject(it),
                              "call": RESOLVED_CALL, "why": ""})
        return {"state": "ok", "rows": rows + aware + _grow_rows(daily_brief)}
    except Exception:
        return {"state": "offline", "rows": []}


def _fmt(r: dict) -> str:
    line = f"- {r['subject']}: {r['call']}"
    return line + (f" (why: {r['why']})" if r.get("why") else "")


def brief_calls_lines(act_view, daily_brief, fmt_action) -> list[str]:
    """The Brief's calls as prompt CONTEXT lines."""
    view = brief_calls_view(act_view, daily_brief, fmt_action)
    if view["state"] == "offline":
        return [HEADER, "Today's Brief could not be built this run — "
                        "no app decisions are available."]
    rows = view["rows"]
    lines = [HEADER, "Act Today:"]
    act = [_fmt(r) for r in rows if r["section"] == "act"]
    lines += act or ["- none (no trades today)"]
    lines.append("Monitoring / Awareness:")
    aware = [_fmt(r) for r in rows if r["section"] == "aware"]
    if aware:
        lines += aware[:_AWARE_CAP]
        if len(aware) > _AWARE_CAP:
            lines.append(f"- … and {len(aware) - _AWARE_CAP} more")
    else:
        lines.append("- none")
    lines.append("Grow Today:")
    lines += [_fmt(r) for r in rows if r["section"] == "grow"]
    return lines


def calls_hash(view: dict) -> str:
    """Short hash of subject + call only (not why), for the stale-narrative note."""
    try:
        if view.get("state") == "offline":
            payload = "offline"
        else:
            payload = "\n".join(f"{r['section']}|{r['subject']}|{r['call']}" for r in view["rows"])
        return hashlib.md5(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    except Exception:
        return "unknown"


def section_html(view: dict) -> str:
    """The card's deterministic action list (HTML; every cell escaped)."""
    e = safe_text
    h = f"<h2>{SECTION_TITLE}</h2>"
    if not isinstance(view, dict) or view.get("state") == "offline":
        return h + f"<p>{e(OFFLINE_NOTE)}</p>"
    rows = view.get("rows")
    rows = rows if isinstance(rows, list) else []

    def block(title, sect, empty, cap=None):
        items = [r for r in rows if r["section"] == sect]
        out = f"<p><strong>{e(title)}</strong></p><ul>"
        if not items:
            return out + f"<li>{e(empty)}</li></ul>"
        extra = 0
        if cap is not None and len(items) > cap:
            extra, items = len(items) - cap, items[:cap]
        for r in items:
            li = f"<strong>{e(r['subject'])}</strong>: {e(r['call'])}"
            if r.get("why"):
                li += f" <em>(why: {e(r['why'])})</em>"
            out += f"<li>{li}</li>"
        if extra:
            out += f"<li>… and {extra} more</li>"
        return out + "</ul>"

    return (h + block("Act Today", "act", NO_TRADES)
            + block("Monitoring / Awareness", "aware", "none", _AWARE_CAP)
            + block("Grow Today", "grow", "no new picks or adds today"))
