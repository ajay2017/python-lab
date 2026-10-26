"""Decisions behind the ⚙️ App Settings "🤖 AI models" section.

No Streamlit import. What the Test role button sends (one case per editable
role, mirroring that role's strictest production site so the test exercises the
production request shape), whether a model setting may be saved (Test before
Save), which effort levels to offer, and the wording of a save outcome
(docs/plans/llm-model-routing.md section 13.2 F, section 14 B3 / B6 / N8 / N9).
The app only renders what this returns.

Site prompts are imported lazily inside the builders, so importing this module
stays cheap.
"""
from __future__ import annotations

import json
import time
import types

from stock_analyzer import llm
from stock_analyzer.constants import (
    ANALYST_EXTRACT_MAX_TOKENS,
    ANALYST_EXTRACT_TIMEOUT_SEC,
    LLM_REQUEST_TIMEOUT_SEC,
)

_PREVIEW_CHARS = 400
_THESIS_VERDICTS = ("intact", "weakening", "broken")


# --- Test cases: one per editable role -----------------------------------------
_SAMPLE_STANCE_INPUTS = {
    "futures": [
        {"name": "S&P 500", "chg_pct": -0.42},
        {"name": "Nasdaq 100", "chg_pct": -0.61},
        {"name": "Dow", "chg_pct": -0.18},
        {"name": "Russell 2000", "chg_pct": -0.35},
    ],
    "global_markets": [
        {"name": "Nikkei 225", "chg_pct": -0.9},
        {"name": "Hang Seng", "chg_pct": 0.3},
        {"name": "FTSE 100", "chg_pct": -0.2},
        {"name": "DAX", "chg_pct": -0.4},
        {"name": "Shanghai Composite", "chg_pct": 0.1},
    ],
    "events": [
        {"event": "Consumer Price Index", "time": "8:30 AM", "impact": "HIGH"},
        {"event": "Fed speaker", "time": "1:00 PM", "impact": "MEDIUM"},
        {"event": "10-year Treasury auction", "time": "1:00 PM", "impact": "MEDIUM"},
    ],
    "movers": [
        {"ticker": "AAA", "chg_pct": -2.4},
        {"ticker": "BBB", "chg_pct": 1.8, "xcheck_ok": False},
        {"ticker": "CCC", "chg_pct": -1.1},
    ],
    "regime_label": "Late-cycle, sticky inflation",
    "regime_rationale": ("Sample text for a connectivity test: inflation has "
                         "stayed above target while growth is slowing."),
    "top_holdings": [
        {"ticker": "AAA", "weight": 14.2, "sector": "Technology", "signal": "Hold"},
        {"ticker": "BBB", "weight": 11.5, "sector": "Energy", "signal": "Strong Buy"},
        {"ticker": "CCC", "weight": 9.8, "sector": "Financials", "signal": "Hold"},
        {"ticker": "DDD", "weight": 8.1, "sector": "Healthcare", "signal": "Buy"},
        {"ticker": "EEE", "weight": 7.4, "sector": "Industrials", "signal": "Hold"},
    ],
    "news_headlines": [
        "Sample headline one: chipmaker guides cautiously for next quarter",
        "Sample headline two: oil slips as inventories rise more than expected",
        "Sample headline three: regional bank flags higher credit costs",
        "Sample headline four: retailer raises full-year outlook",
        "Sample headline five: Treasury yields edge higher ahead of CPI",
    ],
}

_SAMPLE_THESIS = ("I own SAMPLE because its subscription revenue keeps growing "
                  "above 15% a year while margins widen, so I expect the "
                  "earnings power to compound for several years.")
_SAMPLE_THESIS_EVIDENCE = {
    "technical": {"above_sma50": False, "rsi": 41.0, "momentum_1m_pct": -6.3},
    "fundamentals": {"revenue_growth": 11.2, "profit_margin": 18.4,
                     "earnings_trend": "decelerating"},
    "news_headlines": [
        "Sample headline: SAMPLE trims full-year revenue outlook",
        "Sample headline: SAMPLE announces new enterprise customers",
        "Sample headline: analyst lowers SAMPLE price target",
    ],
    "last_earnings": {"result": "missed EPS by 3%",
                      "guidance": "lowered FY revenue guidance"},
    "analyst_consensus": {"consensus_rating": "Buy (6 Buy / 3 Hold / 0 Sell)",
                          "avg_pt": 142.5, "n_firms": 9, "as_of": "2026-09-30",
                          "thesis": ["Sample point: durable recurring revenue",
                                     "Sample point: margin expansion"]},
}

# Four fictional firms and stocks: the shape extract_report sends (raw article
# text), about 4-8k characters. Every name and figure is invented.
_SAMPLE_REPORT_ENTRIES = (
    ("Northwind Capital", "A. Example", "Northwind Systems (NWND)", "Buy", 118, 22,
     "the cloud migration backlog", "enterprise budget delays"),
    ("Harbor Securities", "B. Sample", "Harbor Freight Rail (HFRL)", "Overweight", 64, 15,
     "intermodal volume recovery", "fuel cost volatility"),
    ("Meridian Research", "C. Placeholder", "Meridian Biologics (MRBX)", "Neutral", 41, 3,
     "a Phase 3 readout in the second half", "trial delay or a failed endpoint"),
    ("Summit Partners Equity", "D. Fictional", "Summit Payments (SMPY)", "Sell", 27, -9,
     "merchant churn after the pricing change", "regulatory caps on card fees"),
)


def _sample_report_text() -> str:
    parts = [
        "SAMPLE ARTICLE - fictional firms, analysts and figures written for a "
        "connectivity test; none of it is real.",
        "Published 2026-10-12. Analyst notes roundup: four coverage updates.",
    ]
    for firm, analyst, stock, rating, pt, upside, catalyst, risk in _SAMPLE_REPORT_ENTRIES:
        parts.append(
            f"{firm} analyst {analyst} reiterated a {rating} rating on {stock} and "
            f"set a price target of ${pt}, which the note describes as {upside}% "
            f"upside from the prior close. The note argues the shares are priced "
            f"for the current run rate and that {catalyst} is the main reason to "
            f"revisit the estimate. Management has pointed to steady unit "
            f"economics and a disciplined cost base, and the analyst expects "
            f"revenue growth to hold through the next two reporting periods "
            f"while operating margin improves by roughly one point a year. "
            f"Near-term catalysts named in the note: {catalyst}, the next "
            f"quarterly report, and any update to full-year guidance. The main "
            f"risk named is {risk}; the analyst also lists competition from "
            f"larger peers and a weaker consumer backdrop as secondary risks. "
            f"The note stresses that its estimates assume no change in the "
            f"rate environment and that a miss on either revenue or margin "
            f"would put the target at risk. Supporting points: recurring "
            f"revenue is a growing share of the mix, customer concentration "
            f"is low, free cash flow conversion is above the sector median, "
            f"and the balance sheet carries modest net debt. Shares of {stock} "
            f"have moved with the broader market over the past three months, "
            f"and the firm's model puts the stock on a mid-teens multiple of "
            f"next year's earnings."
        )
    parts.append("End of sample article.")
    return "\n\n".join(parts)


def _stance_prompts():
    from stock_analyzer import premarket_stance
    return (premarket_stance._SYSTEM_PROMPT,
            premarket_stance.format_user_prompt(_SAMPLE_STANCE_INPUTS))


def _thesis_prompts():
    from stock_analyzer import thesis_advisor
    inputs = thesis_advisor.build_review_inputs(**_SAMPLE_THESIS_EVIDENCE)
    return (thesis_advisor._SYSTEM_PROMPT,
            thesis_advisor._format_prompt("SAMPLE", _SAMPLE_THESIS, inputs))


def _extract_prompts():
    from stock_analyzer import analyst_intel
    return analyst_intel._system_prompt(), _sample_report_text()


def _stance_format_ok(text) -> bool:
    # parse_stance reads the LAST line containing "Stance:" and silently maps
    # anything without "defensive" / "constructive" to Neutral, so require one of
    # the three words on that line. The line is found exactly as parse_stance
    # finds it (case-sensitive "Stance:" / "stance:"), so "STANCE: Defensive",
    # which parse_stance never sees, fails here too.
    lines = [ln.strip() for ln in (text or "").strip().split("\n") if ln.strip()]
    last = next((ln for ln in reversed(lines)
                 if "Stance:" in ln or "stance:" in ln), None)
    return last is not None and any(
        w in last.lower() for w in ("defensive", "neutral", "constructive"))


def _thesis_format_ok(text) -> bool:
    # _parse_response defaults to WEAKENING, so check the last "verdict:" line
    # itself (the line it reads) for one of the three verdict words.
    lines = [ln.strip().lower() for ln in (text or "").strip().splitlines()]
    idx = next((i for i in range(len(lines) - 1, -1, -1) if "verdict:" in lines[i]), -1)
    return idx >= 0 and any(w in lines[idx] for w in _THESIS_VERDICTS)


def _extract_format_ok(text) -> bool:
    # analyst_intel's own parse of the reply: a list (possibly empty) is a
    # reply extract_report accepts; a parse failure is not.
    from stock_analyzer import analyst_intel
    try:
        return isinstance(analyst_intel.parse_extract_text(text or ""), list)
    except Exception:  # noqa: BLE001
        return False


# max_tokens mirror the production defaults (generate_stance 500,
# review_thesis 300); tests pin each against the function signature. The
# production stance passes no timeout, so the fast test uses the common 30 s
# budget (LLM_REQUEST_TIMEOUT_SEC).
TEST_CASES = types.MappingProxyType({
    llm.ROLE_FAST: types.MappingProxyType({
        "site": "premarket_stance.generate_stance",
        "build": _stance_prompts,
        "max_tokens": 500,
        "expects_json": True,
        "timeout": LLM_REQUEST_TIMEOUT_SEC,
        "format_check": _stance_format_ok,
        "site_timeout": False,
        "format_label": "a Stance: line naming Defensive, Neutral or Constructive",
    }),
    llm.ROLE_DEEP: types.MappingProxyType({
        "site": "thesis_advisor.review_thesis",
        "build": _thesis_prompts,
        "max_tokens": 300,
        "expects_json": True,
        "timeout": LLM_REQUEST_TIMEOUT_SEC,
        "format_check": _thesis_format_ok,
        "site_timeout": True,
        "format_label": "a Verdict: line (INTACT, WEAKENING or BROKEN)",
    }),
    llm.ROLE_EXTRACT: types.MappingProxyType({
        "site": "analyst_intel.extract_report",
        "build": _extract_prompts,
        "max_tokens": ANALYST_EXTRACT_MAX_TOKENS,
        "expects_json": None,
        "timeout": ANALYST_EXTRACT_TIMEOUT_SEC,
        "format_check": _extract_format_ok,
        "site_timeout": True,
        "format_label": "JSON it can parse",
    }),
})


# --- Candidate identity and the Test role run ------------------------------------
def candidate_key(entry):
    """Hashable identity of a candidate: model, effort and the canonical
    capabilities (so a changed check result is a different candidate)."""
    return (entry.get("model"), entry.get("effort"),
            json.dumps(entry.get("capabilities"), sort_keys=True))


def _int_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def run_test(role, entry, api_key, *, complete=llm.complete,
             clock=time.monotonic) -> dict:
    """One real call on ``role`` with ``entry`` as its App Settings setting,
    shaped like the role's strictest production site. Never raises."""
    case = TEST_CASES.get(role)
    out = {
        "role": role, "site": case["site"] if case else None,
        "ok": False, "error": None, "stop_reason": None, "truncated": False,
        "served_model": None, "requested_model": None, "fell_back": False,
        "model_source": None, "input_tokens": None, "output_tokens": None,
        "latency_s": None, "timeout_s": case["timeout"] if case else None,
        "cost_usd": None, "format_ok": None, "preview": "",
        "key": None,
    }
    started = None
    try:
        out["key"] = candidate_key(entry)
        if case is None:
            out["error"] = f"role {role!r} has no test case"
            return out
        system, user = case["build"]()
        caps = entry.get("capabilities")
        setting = {role: llm.RoleSetting(
            entry["model"], entry.get("effort"),
            types.MappingProxyType(dict(caps)) if caps else None)}
        started = clock()
        result = complete(
            role,
            api_key=api_key,
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=case["max_tokens"],
            expects_json=case["expects_json"],
            timeout=case["timeout"],
            setting=setting,
        )
        out["latency_s"] = clock() - started
        text = getattr(result, "text", None)
        usage = getattr(result, "usage", None)
        out["ok"] = text is not None
        out["error"] = None if out["ok"] else (getattr(result, "error", None)
                                               or "no reply")
        out["stop_reason"] = getattr(result, "stop_reason", None)
        out["truncated"] = bool(getattr(result, "truncated", False))
        out["served_model"] = getattr(result, "model", None)
        out["requested_model"] = getattr(result, "requested_model", None)
        out["fell_back"] = bool(getattr(result, "fell_back", False))
        out["model_source"] = getattr(result, "model_source", None)
        if isinstance(usage, dict):
            out["input_tokens"] = _int_or_none(usage.get("input_tokens"))
            out["output_tokens"] = _int_or_none(usage.get("output_tokens"))
        out["cost_usd"] = llm.approx_cost_usd(getattr(result, "model", None), usage)
        out["preview"] = (text or "")[:_PREVIEW_CHARS]
        out["format_ok"] = case["format_check"](text) if out["ok"] else None
    except Exception as e:  # noqa: BLE001 - never raises by contract
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"[:300]
        out["format_ok"] = None
        if started is not None and out["latency_s"] is None:
            try:
                out["latency_s"] = clock() - started
            except Exception:  # noqa: BLE001
                pass
    return out


def can_save(entry, last_test) -> tuple[bool, str]:
    """Save is allowed only after a passing Test role of this exact candidate
    that ran on the App Settings model. Returns (allowed, reason)."""
    if not isinstance(entry, dict):
        return False, "check the model first"
    if not isinstance(last_test, dict):
        return False, "run Test role first"
    if last_test.get("key") != candidate_key(entry):
        return False, "the last test was for a different model or effort"
    if not last_test.get("ok"):
        return False, f"the last test failed: {last_test.get('error') or 'no reply'}"
    if last_test.get("fell_back"):
        return False, (f"the candidate declined the sample and "
                       f"{last_test.get('served_model') or 'another model'} answered "
                       f"instead; the test didn't exercise {entry.get('model')}")
    if last_test.get("model_source") != llm.SOURCE_SETTING:
        return False, ("the last test didn't run on the model being saved "
                       f"(it ran on {last_test.get('served_model') or 'another model'}, "
                       f"source {last_test.get('model_source')}); run Test role again")
    if last_test.get("format_ok") is False:
        return False, ("the reply didn't have the format "
                       f"{last_test.get('site') or 'the production site'} parses")
    return True, ""


# --- Choices ------------------------------------------------------------------
def effort_options(model, capabilities) -> list:
    """The effort values to offer: None (no explicit effort) plus each allowed
    level the checked capabilities list. Legacy and Opus 4.x models take none."""
    if llm.profile_for(model) in (llm.PROFILE_LEGACY, llm.PROFILE_OPUS_4X):
        return [None]
    listed = None
    if isinstance(capabilities, dict) and isinstance(capabilities.get("efforts"), list):
        listed = capabilities["efforts"]
    return [None] + [lvl for lvl in llm.ALLOWED_EFFORTS
                     if listed is None or lvl in listed]


def effort_label(model, effort, capabilities=None) -> str:
    if effort is not None:
        return str(effort)
    profile = llm.profile_for(model)
    if profile == llm.PROFILE_KNOWN_MODERN:
        # llm.build_request sends ``effort or "low"`` on this profile
        return "default (low)"
    if profile == llm.PROFILE_GENERIC:
        # the same request builder sends "low" on a generic model only when
        # its stored capabilities list it
        listed = capabilities.get("efforts") if isinstance(capabilities, dict) else None
        if isinstance(listed, list) and "low" in listed:
            return "default (low)"
        return "model default (not sent)"
    return "none (this model takes no effort setting)"


def model_choices(listed_ids, current_model) -> list[str]:
    ids = sorted({i for i in (listed_ids or [])
                  if isinstance(i, str) and i.startswith("claude-")})
    if isinstance(current_model, str) and current_model and current_model not in ids:
        ids.insert(0, current_model)
    return ids


def edit_block_reason(row) -> str | None:
    """Why a role's controls are hidden, or None when it can be edited."""
    if row.get("source") == llm.SOURCE_ENV:
        notes = row.get("notes")
        note = next((n for n in (notes if isinstance(notes, list) else [])
                     if n.startswith("Set by an environment variable")), None)
        note = note or ("Set by an environment variable; an App Settings save "
                        "won't take effect while it is set")
        return (f"{note}. Save and Test role are disabled for this role: a test "
                "would run the environment model, not one picked here.")
    if not row.get("editable"):
        return row.get("lock_reason") or "This role isn't editable here."
    return None


# --- History and save outcome ----------------------------------------------------
def _roles_of(payload) -> dict:
    roles = payload.get("roles") if isinstance(payload, dict) else None
    return roles if isinstance(roles, dict) else {}


def _describe(entry) -> str:
    if not isinstance(entry, dict):
        return "code default"
    text = str(entry.get("model"))
    if entry.get("effort"):
        text += f" (effort {entry['effort']})"
    return text


def history_lines(payload, older_payload) -> list[str]:
    """One line per role change between two llm_models payloads; ``checked_at``
    is ignored. ``older_payload`` None means this is the first capture."""
    roles = _roles_of(payload)
    if older_payload is None:
        if not roles:
            return ["Initial capture: no role set (every role on its code default)"]
        return ["Initial capture: " + ", ".join(
            f"{r}: {_describe(roles[r])}" for r in sorted(roles))]
    older = _roles_of(older_payload)
    lines = []
    for r in sorted(set(roles) | set(older)):
        new, old = roles.get(r), older.get(r)
        if new is None and old is None:
            continue
        if new is None:
            lines.append(f"{r}: {_describe(old)} → code default (reset)")
        elif old is None or (new.get("model"), new.get("effort")) != (
                old.get("model"), old.get("effort")):
            lines.append(f"{r}: {_describe(old)} → {_describe(new)}")
        elif new.get("capabilities") != old.get("capabilities"):
            lines.append(f"{r}: {_describe(new)}, stored capabilities updated")
    return lines or ["No role changes"]


def format_checked_at(iso) -> str:
    """"HH:MM ET" from a stored ISO timestamp, else "an unknown time"."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M") + " ET"
    except (TypeError, ValueError):
        return "an unknown time"


def save_outcome_message(result, snapshot_state, action="save") -> tuple[str, str]:
    """(level, text) for a save or reset result (``action`` "save" or "reset");
    level is one of "success", "info", "warning", "error"."""
    from stock_analyzer import llm_settings
    status = result.get("status") if isinstance(result, dict) else None
    if status == "saved":
        return ("success",
                "Saved. The app uses it from its next AI call, and each cron "
                "lane picks it up at its next run.")
    if status == "no_change":
        if snapshot_state == llm_settings.STATE_INVALID:
            return ("warning",
                    "Nothing was written: the stored AI model settings row is "
                    "still invalid and is being ignored (code defaults are in "
                    "use), because its content hash matches what you just "
                    "saved. Make a different change, or use Reset to code "
                    "default, to replace it.")
        if action == "reset":
            return ("info", "Nothing to reset: this role has no saved App Settings choice.")
        return ("info", "Nothing changed: this role already has that model and effort.")
    detail = result.get("detail") if isinstance(result, dict) else None
    return ("error", f"Save failed: {detail or 'unknown error'}")
