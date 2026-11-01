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
from datetime import datetime, timezone

from stock_analyzer import (llm, llm_decision_settings, llm_settings, market_time,
                            sentiment_cache)

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

_PINNED_REASON = ("Switched in ⚙️ App Settings → Decision roles (eval-gated) "
                  "after a passing eval: its output feeds the composite, "
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


def _decision_setting(decision_snapshot, today) -> Mapping | None:
    """role -> ``llm.RoleSetting`` the decision roles resolve to on ``today`` (an ISO
    ``score_date``), from an OK decision snapshot; None when there is none (every
    decision role then shows its code default)."""
    if getattr(decision_snapshot, "state", None) != llm_decision_settings.STATE_OK:
        return None
    entries = getattr(decision_snapshot, "entries", None)
    if not isinstance(entries, Mapping):
        return None
    out = {}
    for role, entry in entries.items():
        try:
            if role == llm.ROLE_SCORE:
                rs = llm_decision_settings.score_entry_for(entry, today)
            else:
                rs = llm_decision_settings._to_setting(entry)
        except Exception:  # noqa: BLE001 - a malformed entry shows the code default
            rs = None
        if rs is not None:
            out[role] = rs
    return out


def _fmt_pts(v) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:+.2f}"
    return "n/a"


def _decision_extras(role, entry, today) -> tuple[dict, list[str], list[str]]:
    """(detail fields, detail lines, grey notes) for a decision role's row."""
    if not isinstance(entry, Mapping):
        return ({"eval_id": None, "criteria_version": None, "pending": None,
                 "cumulative_shift_pts": None}, [], [])
    lines: list[str] = []
    eval_id = entry.get("eval_id")
    crit = entry.get("criteria_version")
    if eval_id is not None:
        lines.append(f"Eval #{eval_id}" + (f" (criteria v{crit})" if crit is not None else ""))
    else:
        lines.append("No eval behind the saved entry (it is the code default)")
    pending = cum = None
    if role == llm.ROLE_SCORE:
        eff = entry.get("effective_from")
        if isinstance(eff, str) and today < eff:
            pending = f"switches to {entry.get('model')} from 00:00 UTC on {eff}"
            lines.append(pending[0].upper() + pending[1:])
        try:
            cum = llm_decision_settings.score_cumulative_for(entry, today)
        except Exception:  # noqa: BLE001
            cum = None
        line = f"Cumulative score shift across switches today: {_fmt_pts(cum)} pts"
        if pending:
            line += f" ({_fmt_pts(entry.get('cumulative_shift_pts'))} pts after the pending switch)"
        lines.append(line)
    grey: list[str] = []
    # The plan's grey note is for a switch that has TAKEN EFFECT, not one still pending.
    if entry.get("model") != llm.CODE_DEFAULTS[role] and not pending:
        grey.append(f"Saved model {entry.get('model')} differs from the code default "
                    f"{llm.CODE_DEFAULTS[role]}: code default not yet moved to match")
    return ({"eval_id": eval_id, "criteria_version": crit, "pending": pending,
             "cumulative_shift_pts": cum}, lines, grey)


def build_rows(snapshot, *, env=None, available_ids=None, usage=None,
               decision_snapshot=None, today=None) -> list[dict]:
    """One dict per role. ``decision_snapshot`` (``llm_decision_settings``) makes
    the score / filter / extract_trigger rows show the saved model in effect on
    ``today`` (default ``sentiment_cache.score_date_today()``); without it they
    show the code default."""
    environ = os.environ if env is None else env
    setting = getattr(snapshot, "setting", None)
    warnings = _warnings(snapshot)
    dec_warnings = _warnings(decision_snapshot)
    today = today or sentiment_cache.score_date_today()
    dec_setting = _decision_setting(decision_snapshot, today)
    dec_entries = (getattr(decision_snapshot, "entries", None)
                   if getattr(decision_snapshot, "state", None)
                   == llm_decision_settings.STATE_OK else None)
    rows = []
    for role in ROLE_ORDER:
        editable = role in llm.EDITABLE_ROLES
        decision = role in llm.DECISION_ROLES
        model, source = llm.resolve_model_with_source(
            role, setting=dec_setting if decision else setting, env=environ)
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
            elif decision:
                notes.append(f"{var} is set but ignored for this role")
            else:
                notes.append(f"Environment variable {var} is ignored: "
                             "this role isn't editable")
        extra, detail_lines, grey_notes = {}, [], []
        if decision:
            saved = dec_entries.get(role) if isinstance(dec_entries, Mapping) else None
            extra, detail_lines, grey_notes = _decision_extras(role, saved, today)
        if editable:
            editable_label = "yes"
        elif decision:
            editable_label = "yes, after a passing eval (⚙️ App Settings)"
        else:
            editable_label = LOCK_REASONS.get(role)
        rows.append({
            "role": role,
            "description": ROLE_DESCRIPTIONS[role],
            "editable": editable,
            "lock_reason": None if editable else LOCK_REASONS.get(role),
            "editable_label": editable_label,
            "model": model,
            "source": source,
            "source_label": _source_label(source, role),
            "key": key_status(model, available_ids),
            "notes": notes,
            "grey_notes": grey_notes,
            "detail": " · ".join(detail_lines) if detail_lines else None,
            "warnings": [w for w in (dec_warnings if decision else warnings)
                         if w.startswith(f"{role}:")],
            "usage": usage.get(role) if isinstance(usage, dict) else None,
            **extra,
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
                      "(see each role below)", others)
    if state == llm_settings.STATE_INVALID:
        return ("error", "AI model settings row is invalid; code defaults in use "
                         "unless an environment variable is set", others)
    # unreadable: the status line already says it, so don't repeat the warning
    others = [w for w in others if w != llm_settings.WARN_UNREADABLE]
    return ("warn", "AI model settings couldn't be read; code defaults in use "
                    "unless an environment variable is set", others)


def _et_text(iso) -> str:
    """An ISO UTC timestamp as ``YYYY-MM-DD HH:MM ET``; the raw text when it can't
    be parsed."""
    try:
        d = datetime.fromisoformat(str(iso))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(market_time.ET).strftime("%Y-%m-%d %H:%M ET")
    except Exception:  # noqa: BLE001
        return str(iso)


def decision_status(snapshot) -> tuple[str, str, list[str]]:
    """(level, text, warnings not tied to a decision role) for the decision-model
    row (``llm_decision_settings``). Unreadable / invalid are red (owner decision
    U4: the roles fall back to their code defaults); a stale copy is amber."""
    others = [w for w in _warnings(snapshot)
              if not any(w.startswith(f"{r}:") for r in llm.DECISION_ROLES)
              and w not in (llm_decision_settings.WARN_UNREADABLE,
                            llm_decision_settings.WARN_STALE)]
    state = getattr(snapshot, "state", None)
    if state == llm_decision_settings.STATE_OK:
        if getattr(snapshot, "stale", False):
            since = getattr(snapshot, "stale_since", None)
            when = f" since {_et_text(since)}" if since else ""
            return ("warn", "AI decision-model settings couldn't be re-read: "
                            f"serving the last good copy{when}", others)
        as_of = getattr(snapshot, "as_of", None) or "an unknown date"
        by = getattr(snapshot, "updated_by", None) or "unknown"
        return ("ok", f"AI decision-model settings saved {as_of} by {by}", others)
    if state == llm_decision_settings.STATE_NO_ROW:
        return ("ok", "No decision-model settings row: all decision roles on code "
                      "defaults", others)
    if state == llm_decision_settings.STATE_INVALID:
        return ("error", "AI decision-model settings row is invalid; all decision "
                         "roles on code defaults", others)
    return ("error", "AI decision-model settings couldn't be read; all decision "
                     "roles on code defaults", others)


def model_counts_text(counts) -> str:
    """The "Today's news-score rows by model" line from
    ``db.count_sentiment_cache_models`` (None = couldn't read)."""
    label = "Today's news-score rows by model: "
    if counts is None:
        return label + "couldn't be read"
    if not isinstance(counts, Mapping) or not counts:
        return label + "none yet today"
    parts = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return label + " · ".join(f"{m}: {n:,}" for m, n in parts)


def todays_model_counts_line() -> str:
    """``model_counts_text`` for today's ``score_date`` (one select of the model
    column). Never raises."""
    try:
        from stock_analyzer import db
        return model_counts_text(
            db.count_sentiment_cache_models(sentiment_cache.score_date_today()))
    except Exception:  # noqa: BLE001
        return model_counts_text(None)
