"""Pure decisions behind System Trust's "AI models" section.

No Streamlit import. ``build_rows`` turns the App Settings snapshot, the raw
environment and the process usage tally into one dict per LLM role; the page
only renders them (docs/plans/llm-model-routing.md section 13.2 G, section 14
B10 / N8 / N10).
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping

from stock_analyzer import llm, llm_settings

ROLE_ORDER = ("fast", "deep", "extract", "extract_strong",
              "extract_trigger", "filter", "score")

ROLE_DESCRIPTIONS = {
    "fast": ("🏠 Home pre-market stance; the entry and exit multi-agent debates, "
             "all four rounds and the judge, whose verdict shows on Grow Today "
             "and on Act Today's deterioration TRIM/EXIT cards; 💬 Ask (question "
             "parsing and answers); Pre-Mortem case against; thesis red team; "
             "missed-opportunity patterns; thesis clusters; catalyst and regime "
             "stress narratives; structural scanner narrative. 🤖 AI Snapshot "
             "also uses this role, with the model picked in its own selector"),
    "deep": ("thesis reviews (🧠 AI Insights and the weekly cron), thesis drafts, "
             "earnings thesis updates, the weekly debrief and the monthly "
             "intelligence report; the 🔎 Investigator also uses this role, with "
             "the model picked below"),
    "extract": ("analyst report extraction (feeds the Valuation pillar's "
                "price-target fallback) and earnings playbook extraction "
                "(🧠 AI Insights). The debate judge uses this role's JSON "
                "handling but runs on the fast model"),
    "extract_strong": ("broker import (📋 Paste history): resolving tickers the "
                       "local table doesn't recognise"),
    "extract_trigger": "📒 Trade Journal Pre-Mortem trigger extraction at buy time",
    "filter": ("news noise filter: re-scores headlines for the 🏠 Brief's "
               "news checks and the sidebar news list"),
    "score": "headline sentiment rescoring (the composite's sentiment pillar)",
}

_PINNED_REASON = ("Pinned in reviewed code: its output feeds the composite, "
                  "Act Today or the R-Multiple declared stop")
LOCK_REASONS = {
    "score": _PINNED_REASON,
    "filter": _PINNED_REASON,
    "extract_trigger": _PINNED_REASON,
    "extract_strong": ("Code only until the broker import preview is checked "
                       "(owner decision D2-1)"),
}


def key_status(model, available_ids) -> str:
    """"unknown" (never treated as good) when the key's model list wasn't
    fetched, "available" for a listed id or a dated variant of an alias,
    else "not_listed"."""
    if available_ids is None:
        return "unknown"
    if not isinstance(model, str) or not model:
        return "not_listed"
    ids = [i for i in available_ids if isinstance(i, str)]
    dated = re.compile(re.escape(model) + r"-\d{8}")
    if model in ids or any(dated.fullmatch(i) for i in ids):
        return "available"
    return "not_listed"


def _warnings(snapshot) -> tuple:
    w = getattr(snapshot, "warnings", None)
    return tuple(w) if isinstance(w, (tuple, list)) else ()


def _source_label(source, role) -> str:
    if source == llm.SOURCE_CODE:
        return "code default"
    if source == llm.SOURCE_SETTING:
        return "App Settings"
    if source == llm.SOURCE_ENV:
        return f"env {llm.ENV_PREFIX}{role.upper()}"
    if source == llm.SOURCE_EXPLICIT:
        return "explicit"
    return str(source)


def build_rows(snapshot, *, env=None, available_ids=None, usage=None) -> list[dict]:
    environ = os.environ if env is None else env
    setting = getattr(snapshot, "setting", None)
    warnings = _warnings(snapshot)
    rows = []
    for role in ROLE_ORDER:
        editable = role in llm.EDITABLE_ROLES
        model, source = llm.resolve_model_with_source(role, setting=setting, env=environ)
        var = llm.ENV_PREFIX + role.upper()
        raw = environ.get(var)
        raw_set = isinstance(raw, str) and bool(raw.strip())
        notes = []
        if source == llm.SOURCE_ENV:
            note = ("Set by an environment variable; an App Settings save "
                    "won't take effect while it is set")
            entry = setting.get(role) if isinstance(setting, Mapping) else None
            if isinstance(entry, llm.RoleSetting):
                note += f" (App Settings has {entry.model} saved)"
            notes.append(note)
        elif raw_set:
            if editable:
                notes.append(f"Environment variable {var} is set but ignored "
                             "(not a claude- model id)")
            else:
                notes.append(f"Environment variable {var} is ignored: "
                             "this role isn't editable")
        rows.append({
            "role": role,
            "description": ROLE_DESCRIPTIONS[role],
            "editable": editable,
            "lock_reason": None if editable else LOCK_REASONS.get(role),
            "model": model,
            "source": source,
            "source_label": _source_label(source, role),
            "key": key_status(model, available_ids),
            "notes": notes,
            "warnings": [w for w in warnings if w.startswith(f"{role}:")],
            "usage": usage.get(role) if isinstance(usage, dict) else None,
        })
    return rows


def settings_status(snapshot) -> tuple[str, str, list[str]]:
    """(level, text, warnings not tied to a role)."""
    others = [w for w in _warnings(snapshot)
              if not any(w.startswith(f"{r}:") for r in ROLE_ORDER)]
    state = getattr(snapshot, "state", None)
    if state == llm_settings.STATE_OK:
        as_of = getattr(snapshot, "as_of", None) or "an unknown date"
        by = getattr(snapshot, "updated_by", None) or "unknown"
        return ("ok", f"App Settings row saved {as_of} by {by}", others)
    if state == llm_settings.STATE_NO_ROW:
        return ("ok", "No App Settings row: every editable role is on its "
                      "code default unless an environment variable is set "
                      "(see the table)", others)
    if state == llm_settings.STATE_INVALID:
        return ("error", "AI model settings row is invalid; code defaults in use "
                         "unless an environment variable is set", others)
    # unreadable: the status line already says it, so don't repeat the warning
    others = [w for w in others if w != llm_settings._WARN_UNREADABLE]
    return ("warn", "AI model settings couldn't be read; code defaults in use "
                    "unless an environment variable is set", others)
