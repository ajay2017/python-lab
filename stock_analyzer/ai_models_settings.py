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
import math
import threading
import time
import types
from datetime import datetime, timedelta, timezone

from stock_analyzer import llm, llm_decision_settings, llm_settings, market_time
from stock_analyzer.constants import (
    ANALYST_EXTRACT_MAX_TOKENS,
    ANALYST_EXTRACT_TIMEOUT_SEC,
    LLM_EVAL_CRITERIA_VERSION,
    LLM_EVAL_MAX_AGE_DAYS,
    LLM_EVAL_SCORE_MAX_CUMULATIVE_SHIFT_PTS,
    LLM_REQUEST_TIMEOUT_SEC,
    THESIS_REVIEW_MAX_TOKENS,
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


# --- Decision-role samples and runners (score / filter / extract_trigger) --------
# Every name and figure is invented. The runners call the PRODUCTION function, so
# its prompt, request, timeout and parser are the ones under test.
_NEWS_SITE_TIMEOUT_SEC = 8.0   # mirror of rescore_headlines_llm / llm_score_batch's default (test-pinned)

_SAMPLE_SCORE_HEADLINES = (
    {"headline": "Sample headline: SAMPLE raises full-year revenue outlook after a strong quarter",
     "score": 0.40, "label": "Positive", "url": ""},
    {"headline": "Sample headline: analyst lowers SAMPLE price target on slowing demand",
     "score": -0.30, "label": "Negative", "url": ""},
    {"headline": "Sample headline: SAMPLE announces a new enterprise customer",
     "score": 0.20, "label": "Positive", "url": ""},
    {"headline": "Sample headline: ten stocks to watch this week, SAMPLE among them",
     "score": 0.0, "label": "Neutral", "url": ""},
    {"headline": "Sample headline: rival warns on margins, SAMPLE shares slip in sympathy",
     "score": -0.10, "label": "Negative", "url": ""},
)

_FILTER_SAMPLE_N = 25   # one production batch (news_intelligence._RESCORE_BATCH)
_FILTER_SAMPLE_TITLES = (
    "chipmaker cuts guidance as orders slow", "regional bank flags rising credit losses",
    "retailer beats estimates and lifts outlook", "drugmaker wins approval for new therapy",
    "oil producer halts output after pipeline leak", "software firm announces layoffs",
    "airline reports record bookings", "automaker recalls vehicles over a brake defect",
    "insurer's profit misses forecasts", "utility settles a regulatory dispute",
    "payments company wins a large contract", "shipping firm warns of weaker rates",
    "biotech's trial misses its main goal", "grocer raises its dividend",
    "cloud provider announces an outage review", "steelmaker plans a plant closure",
    "hotel chain upgraded by an analyst", "solar maker downgraded on pricing pressure",
    "medical device firm issues a safety notice", "bank prices a debt offering",
    "restaurant chain reports weaker traffic", "defense contractor lands a new award",
    "chemical maker flags higher input costs", "streaming service adds subscribers",
    "mining company reports a tailings spill",
)
_FILTER_SAMPLE_TICKERS = ("AAA", "BBB", "CCC", "DDD", "EEE")

_SAMPLE_COMMITMENT = ("I would be wrong about SAMPLE if it closes below $42.50, "
                      "which would break the support it has held since spring.")


def _strip_fence(text) -> str:
    # exactly the fence handling rescore_headlines_llm / llm_score_batch use
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _full_scores_ok(text, n) -> bool:
    """The reply is a JSON array holding a numeric score in [-1, 1] for every
    idx 0..n-1 (production tolerates gaps; the test wants none)."""
    try:
        parsed = json.loads(_strip_fence(text))
        seen = set()
        for entry in parsed:
            idx, sc = entry.get("idx"), entry.get("score")
            if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < n:
                continue
            if isinstance(sc, bool) or not isinstance(sc, (int, float)):
                continue
            if -1.0 <= float(sc) <= 1.0:
                seen.add(idx)
        return len(seen) == n
    except Exception:  # noqa: BLE001
        return False


def _run_score(api_key, setting):
    from stock_analyzer import news_intelligence
    return news_intelligence.rescore_headlines_llm(
        [dict(h) for h in _SAMPLE_SCORE_HEADLINES], api_key, "SAMPLE",
        setting=setting)


def _run_filter(api_key, setting):
    from stock_analyzer import news_intelligence
    items = [{"ticker": _FILTER_SAMPLE_TICKERS[i % len(_FILTER_SAMPLE_TICKERS)],
              "title": f"Sample headline: {_FILTER_SAMPLE_TITLES[i]}"}
             for i in range(_FILTER_SAMPLE_N)]
    return news_intelligence.llm_score_batch(items, api_key, setting=setting)


def _run_trigger(api_key, setting):
    from stock_analyzer import premortem_monitor
    out = premortem_monitor.extract_trigger_outcome(_SAMPLE_COMMITMENT, api_key,
                                                    setting=setting)
    # remembered for run_test: a reply refused ONLY by the in-text price check
    _tl.refused_price = out["refused_price"]
    return out["trigger"]


def _score_accepts(ret) -> bool:
    return isinstance(ret, list) and len(ret) == len(_SAMPLE_SCORE_HEADLINES)


def _filter_accepts(ret) -> bool:
    return isinstance(ret, list) and len(ret) == _FILTER_SAMPLE_N


def _trigger_accepts(ret) -> bool:
    return isinstance(ret, dict)


def _score_format_ok(text) -> bool:
    return _full_scores_ok(text, len(_SAMPLE_SCORE_HEADLINES))


def _filter_format_ok(text) -> bool:
    return _full_scores_ok(text, _FILTER_SAMPLE_N)


def _trigger_format_ok(text) -> bool:
    from stock_analyzer import premortem_monitor
    try:
        return premortem_monitor._parse_trigger((text or "").strip()) is not None
    except Exception:  # noqa: BLE001
        return False


class _Recorder:
    """Stands in for ``llm.complete`` inside the production module while one
    test runs: calls the real (or injected) ``complete`` and keeps its result."""

    def __init__(self, complete):
        self._complete = complete
        self.result = None
        self.exc = None

    def __call__(self, role, **kw):
        try:
            self.result = self._complete(role, **kw)
        except Exception as e:  # noqa: BLE001 - production swallows it too
            self.exc = e
            raise
        return self.result


class _LLMProxy:
    """The ``llm`` module as the production modules see it, with ``complete``
    redirected to a recorder only for the thread that is running a test."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def complete(self, role, **kw):
        rec = getattr(_tl, "rec", None)
        if rec is not None:
            return rec(role, **kw)
        return self._real.complete(role, **kw)


_tl = threading.local()
_run_lock = threading.Lock()   # one decision test at a time: the swap below is process-wide


def _production_call(case, api_key, setting, complete):
    """Run ``case["run"]`` with the production modules' ``llm`` swapped for a
    proxy that records the one request. Returns (function result, recorder)."""
    from stock_analyzer import news_intelligence, premortem_monitor
    mods = (news_intelligence, premortem_monitor)
    rec = _Recorder(complete)
    with _run_lock:
        originals = [m.llm for m in mods]
        try:
            # inside the try: a failure part-way still restores every module
            for m in mods:
                m.llm = _LLMProxy(llm)
            _tl.rec = rec
            ret = case["run"](api_key, setting)
        finally:
            _tl.rec = None
            for m, orig in zip(mods, originals):
                m.llm = orig
    return ret, rec


# max_tokens mirror the production defaults (generate_stance 500,
# review_thesis THESIS_REVIEW_MAX_TOKENS); tests pin each against the
# function signature. The production stance passes no timeout, so the fast
# test uses the common 30 s budget (LLM_REQUEST_TIMEOUT_SEC).
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
        "max_tokens": THESIS_REVIEW_MAX_TOKENS,
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
    # Decision roles: the case RUNS the production function itself (its own
    # prompt, request, timeout and parser) with the candidate as
    # ``setting={role: RoleSetting}``; ``accepts`` is what that function returned
    # being usable, ``format_check`` reads the raw reply. ``max_tokens`` and
    # ``timeout`` mirror the site for display and are pinned by tests.
    llm.ROLE_SCORE: types.MappingProxyType({
        "site": "news_intelligence.rescore_headlines_llm",
        "run": _run_score,
        "accepts": _score_accepts,
        "max_tokens": 512,
        "expects_json": None,
        "timeout": _NEWS_SITE_TIMEOUT_SEC,
        "format_check": _score_format_ok,
        "site_timeout": True,
        "format_label": "a JSON array scoring every sample headline (idx 0-4, -1 to 1)",
    }),
    llm.ROLE_FILTER: types.MappingProxyType({
        "site": "news_intelligence.llm_score_batch",
        "run": _run_filter,
        "accepts": _filter_accepts,
        "max_tokens": 64 + 32 * _FILTER_SAMPLE_N,
        "expects_json": None,
        "timeout": _NEWS_SITE_TIMEOUT_SEC,
        "format_check": _filter_format_ok,
        "site_timeout": True,
        "format_label": (f"a JSON array scoring all {_FILTER_SAMPLE_N} sample "
                         "headlines (idx 0-24, -1 to 1)"),
    }),
    llm.ROLE_EXTRACT_TRIGGER: types.MappingProxyType({
        "site": "premortem_monitor.extract_trigger",
        "run": _run_trigger,
        "accepts": _trigger_accepts,
        "max_tokens": 200,
        "expects_json": None,
        "timeout": LLM_REQUEST_TIMEOUT_SEC,
        "format_check": _trigger_format_ok,
        "site_timeout": True,
        "format_label": "a JSON object production's trigger parser accepts",
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
        "key": None, "refused_price": None,
    }
    started = None
    try:
        out["key"] = candidate_key(entry)
        if case is None:
            out["error"] = f"role {role!r} has no test case"
            return out
        caps = entry.get("capabilities")
        setting = {role: llm.RoleSetting(
            entry["model"], entry.get("effort"),
            types.MappingProxyType(dict(caps)) if caps else None)}
        accepted = True
        if case.get("run") is not None:
            # decision role: the production function itself makes the call
            started = clock()
            ret, rec = _production_call(case, api_key, setting, complete)
            out["latency_s"] = clock() - started
            result = rec.result
            if result is None:
                out["error"] = (f"{type(rec.exc).__name__}: {rec.exc}"[:300]
                                if rec.exc is not None else
                                "no request was sent (missing API key?)")
                return out
            accepted = bool(case["accepts"](ret))
            out["refused_price"] = getattr(_tl, "refused_price", None)
            _tl.refused_price = None
        else:
            system, user = case["build"]()
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
        out["format_ok"] = ((accepted and bool(case["format_check"](text)))
                            if out["ok"] else None)
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
        if last_test.get("refused_price") is not None:
            return False, (f"the reply's price {last_test['refused_price']:,.2f} "
                           "isn't in the sample commitment, so the in-text price "
                           "check refused it")
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


# =============================================================================
# Decision roles (score / filter / extract_trigger): eval-gated Save
# (docs/plans/llm-model-routing.md section 17, chunk C5)
# =============================================================================
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# Tolerated gap between this machine's clock and the eval's recorded ``ran_at``
# (an eval can't have run in the future). Clock plumbing, not investment policy.
_CLOCK_SKEW_SEC = 300


def utc_now() -> datetime:
    """Aware UTC "now", derived from market_time's clock."""
    return market_time.now_et().astimezone(timezone.utc)


def _as_utc(v):
    """An aware UTC datetime from a datetime or an ISO string (a trailing Z or
    a naive value is read as UTC), else None."""
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, str):
        s = v.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _clock12(et: datetime) -> str:
    return f"{et.hour % 12 or 12}:{et.minute:02d} {'AM' if et.hour < 12 else 'PM'}"


def _et_stamp(dt_utc: datetime) -> str:
    et = dt_utc.astimezone(market_time.ET)
    return (f"{_DAYS[et.weekday()]} {_MONTHS[et.month - 1]} {et.day}, "
            f"{_clock12(et)} ET")


def _effort_text(effort) -> str:
    return f"effort {effort}" if effort else "no explicit effort"


def eval_command(role, model, effort) -> str:
    """The exact shell command that writes the eval Save needs."""
    cmd = f"python scripts/llm_role_eval.py --role {role} --model {model}"
    if effort:
        cmd += f" --effort {effort}"
    return cmd


def score_effective_label(effective_from: str) -> str:
    """e.g. "from 00:00 UTC on Tue 2026-11-03 (7:00 PM ET on Mon Nov 2)": the
    first score_date a score switch applies to, and when that midnight is in ET."""
    try:
        d = datetime.fromisoformat(effective_from).date()
    except (TypeError, ValueError):
        return f"from {effective_from} (UTC date)"
    et = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).astimezone(market_time.ET)
    return (f"from 00:00 UTC on {_DAYS[d.weekday()]} {d.isoformat()} "
            f"({_clock12(et)} ET on {_DAYS[et.weekday()]} {_MONTHS[et.month - 1]} {et.day})")


def save_effective_line(role, now_utc: datetime):
    """The line shown before a score Save (None for the other roles)."""
    if role != llm.ROLE_SCORE:
        return None
    label = score_effective_label(llm_decision_settings.score_effective_from(now_utc))
    return (f"If saved now, the new score model applies {label}. Until then the "
            "current model keeps scoring, so a UTC score date (after the US close) "
            "isn't split between two models in normal operation.")


def decide_decision_candidate(role, model, effort, capabilities, retrieve_error,
                              checked_at, *, error_kind=None):
    """The Check-model step for a decision role: (entry, []) or (None, errors).
    Reuses ``llm_settings.decide_save``'s checks, but a decision candidate must
    have been CHECKED (an unreachable Models API refuses): the eval's capabilities
    hash is taken from the checked snapshot."""
    if role not in llm.DECISION_ROLES:
        return None, [f"role {role!r} is not an eval-gated decision role"]
    if retrieve_error is not None:
        return None, [f"couldn't check {model} with the Models API ({retrieve_error}); "
                      "a decision role can only be saved after a successful check"]
    # decide_save's rules are role-agnostic apart from the editable-role test
    return llm_settings.decide_save(llm.ROLE_FAST, model, effort, capabilities,
                                    retrieve_error, checked_at, error_kind=error_kind)


def _stored_pair(role, current):
    if isinstance(current, dict) and current.get("model"):
        return current.get("model"), current.get("effort") or None
    return llm.CODE_DEFAULTS[role], None


def _finite(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def _cumulative_parts(current, eval_row, today):
    """(cumulative of what resolves on ``today`` (an ISO score_date), this eval's
    signed net mean shift), or None when the eval has no usable
    ``mean_shift_net``."""
    metrics = eval_row.get("metrics") if isinstance(eval_row, dict) else None
    shift = metrics.get("mean_shift_net") if isinstance(metrics, dict) else None
    if not _finite(shift):
        return None
    cur = llm_decision_settings.score_cumulative_for(current, today)
    return float(cur), float(shift)


def _resolved_today_pair(role, stored, today):
    """(model, effort) the role resolves to on ``today`` given its stored entry."""
    if role == llm.ROLE_SCORE and isinstance(stored, dict):
        try:
            rs = llm_decision_settings.score_entry_for(stored, today)
        except Exception:  # noqa: BLE001 - a malformed entry resolves to the default
            rs = None
        if rs is not None:
            return rs.model, rs.effort or None
        return llm.CODE_DEFAULTS[role], None
    return _stored_pair(role, stored)


def _model_text(pair) -> str:
    return f"{pair[0]} ({_effort_text(pair[1])})"


def can_save_decision(role, entry, last_test, eval_row, current_score_entry,
                      now_utc: datetime, snapshot_state=None) -> tuple[bool, str]:
    """Save for a decision role: a passing Test role of this exact candidate AND
    a stored passing eval of it that is current. Returns (allowed, reason).
    ``current_score_entry`` is the stored entry of ``role`` in the decision row
    (None when none; named for score, whose cumulative shift it carries)."""
    if role not in llm.DECISION_ROLES:
        return False, f"{role} isn't an eval-gated decision role"
    now = _as_utc(now_utc)
    if now is None:
        return False, "the current time is unavailable, so the eval's age can't be checked"
    ok, why = can_save(entry, last_test)
    if not ok:
        return False, why
    model, effort = entry.get("model"), entry.get("effort") or None
    cmd = eval_command(role, model, effort)
    today = now.date().isoformat()
    if snapshot_state == llm_decision_settings.STATE_INVALID:
        # Any Save would rewrite the row from an empty base, silently dropping the
        # other roles' entries and score's running drift total: Reset only.
        return False, ("the saved decision-role row is invalid; Reset score first "
                       "(its running drift total then restarts at 0), then save again")
    if _stored_pair(role, current_score_entry) == (model, effort):
        where = ("the saved choice" if isinstance(current_score_entry, dict)
                 else "the code default")
        return False, (f"{model} ({_effort_text(effort)}) is already {where} for {role}")
    if (role == llm.ROLE_SCORE and isinstance(current_score_entry, dict)
            and today < str(current_score_entry.get("effective_from"))):
        return False, (f"a score switch to {current_score_entry.get('model')} is already "
                       f"pending from {current_score_entry.get('effective_from')} "
                       "(00:00 UTC); wait until it applies, then re-run the eval")
    if not isinstance(eval_row, dict):
        return False, (f"no stored eval of {model} ({_effort_text(effort)}) for {role}; "
                       f"run `{cmd}` from a shell, then reload this page")
    if eval_row.get("role") != role:
        return False, (f"the stored eval is for role {eval_row.get('role')}, not {role}; "
                       f"run `{cmd}`")
    if eval_row.get("passed") is not True:
        return False, ("the latest stored eval of this model and effort did not pass "
                       "(failures listed below); a failed eval can't enable Save")
    if eval_row.get("model") != model:
        return False, (f"the stored eval is for {eval_row.get('model')}, not {model}; "
                       f"run `{cmd}`")
    if (eval_row.get("effort") or None) != effort:
        return False, (f"the stored eval used {_effort_text(eval_row.get('effort') or None)}, "
                       f"not {_effort_text(effort)}; run `{cmd}`")
    from stock_analyzer import llm_role_eval
    if eval_row.get("capabilities_hash") != llm_role_eval.capabilities_hash(
            entry.get("capabilities")):
        return False, ("the stored eval was run against different model capabilities "
                       f"than the ones just checked; re-run `{cmd}`")
    if eval_row.get("criteria_version") != LLM_EVAL_CRITERIA_VERSION:
        return False, (f"the stored eval used pass criteria version "
                       f"{eval_row.get('criteria_version')}, but the current version is "
                       f"{LLM_EVAL_CRITERIA_VERSION}; re-run `{cmd}`")
    ran = _as_utc(eval_row.get("ran_at"))
    if ran is None:
        return False, "the stored eval has no readable run time, so its age can't be confirmed"
    if ran - now > timedelta(seconds=_CLOCK_SKEW_SEC):
        return False, (f"the stored eval's run time ({_et_stamp(ran)}) is in the future "
                       "of this clock, so its age can't be trusted; re-run "
                       f"`{cmd}`")
    if role != llm.ROLE_EXTRACT_TRIGGER:
        raw_m = eval_row.get("metrics")
        em = raw_m if isinstance(raw_m, dict) else {}
        today_pair = _resolved_today_pair(role, current_score_entry, today)
        if ("current_model" not in em
                or (em.get("current_model"), em.get("current_effort") or None) != today_pair):
            measured = (_model_text((em["current_model"], em.get("current_effort") or None))
                        if "current_model" in em else "an unrecorded baseline model")
            return False, (f"this eval measured {_model_text((model, effort))} against "
                           f"{measured}, but {_model_text(today_pair)} is in effect now; "
                           "re-run the eval")
    if now - ran > timedelta(days=LLM_EVAL_MAX_AGE_DAYS):
        days = (now - ran).total_seconds() / 86400
        return False, (f"the stored eval is {days:.1f} days old, older than the "
                       f"{LLM_EVAL_MAX_AGE_DAYS}-day limit; re-run `{cmd}`")
    if role == llm.ROLE_SCORE:
        parts = _cumulative_parts(current_score_entry, eval_row, today)
        if parts is None:
            return False, ("the stored eval has no measured net mean shift, so the "
                           "cumulative score shift can't be checked")
        total = parts[0] + parts[1]
        if abs(round(total, 9)) > LLM_EVAL_SCORE_MAX_CUMULATIVE_SHIFT_PTS:
            return False, (f"this switch would take the cumulative score shift across "
                           f"switches to {total:+.2f} pts, beyond the "
                           f"{LLM_EVAL_SCORE_MAX_CUMULATIVE_SHIFT_PTS:g} pt limit")
    return True, ""


def build_decision_entry(role, entry, eval_row, current_entry,
                         now_utc: datetime) -> dict:
    """The ``llm_models_decision`` entry to store for a candidate that passed
    ``can_save_decision``. Score adds ``effective_from``, ``prior`` (what the
    current entry resolves to TODAY, UTC) and the running ``cumulative_shift_pts``."""
    out = {
        "model": entry["model"],
        "effort": entry.get("effort"),
        "capabilities": entry.get("capabilities"),
        "checked_at": entry.get("checked_at"),
        "eval_id": eval_row.get("id"),
        "criteria_version": eval_row.get("criteria_version"),
    }
    if role == llm.ROLE_SCORE:
        now = _as_utc(now_utc)
        parts = _cumulative_parts(current_entry, eval_row, now.date().isoformat())
        if parts is None:
            raise ValueError("the eval has no mean_shift_net")
        prior = None
        if isinstance(current_entry, dict):
            rs = llm_decision_settings.score_entry_for(
                current_entry, now.date().isoformat())
            if rs is not None:
                prior = {"model": rs.model, "effort": rs.effort,
                         "capabilities": dict(rs.capabilities) if rs.capabilities else None}
        out["effective_from"] = llm_decision_settings.score_effective_from(now)
        out["prior"] = prior
        out["cumulative_before"] = round(parts[0], 4) + 0.0
        out["cumulative_shift_pts"] = round(parts[0] + parts[1], 4) + 0.0
    return out


def base_payload_for_decision(status, row) -> tuple[dict | None, list[str]]:
    """(payload, notes) a decision Save / Reset builds on; mirrors
    ``llm_settings.base_payload_for_save`` for the ``llm_models_decision`` row."""
    lds = llm_decision_settings
    if status == lds.STATE_NO_ROW:
        return {"version": lds.PAYLOAD_VERSION, "roles": {}}, []
    if status != lds.STATE_OK or not isinstance(row, dict):
        return None, ["couldn't read the current AI decision-model settings, so a save "
                      "now could overwrite another role's setting; try again"]
    if lds.build_snapshot(status, row).state == lds.STATE_INVALID:
        return ({"version": lds.PAYLOAD_VERSION, "roles": {}},
                ["the stored AI decision-model row is invalid and is being ignored "
                 "(code defaults are in use); a save or reset replaces it with a new "
                 "row holding only this role's change"])
    payload = lds.canonicalize(row.get("payload"))
    roles: dict = {}
    notes: list[str] = []
    for role, entry in payload["roles"].items():
        if lds.validate_role_entry(role, entry):
            notes.append(f"the stored setting for {role} is invalid and is being "
                         "ignored (its code default is in use); it is dropped")
        else:
            roles[role] = entry
    return {"version": lds.PAYLOAD_VERSION, "roles": roles}, notes


def can_reset_decision(role, snapshot, now_utc=None) -> tuple[bool, str]:
    """Reset to the code default is offered when the role has a stored entry or
    the stored row is invalid (a reset replaces it). A score entry that is already
    the code default (no effort, nothing pending) is a no-op and isn't offered."""
    lds = llm_decision_settings
    state = getattr(snapshot, "state", None)
    if state == lds.STATE_INVALID:
        return True, ""
    entries = snapshot.entries if state == lds.STATE_OK else {}
    if role in entries:
        if role == llm.ROLE_SCORE:
            e = entries[role]
            now = _as_utc(now_utc) or utc_now()
            pending = now.date().isoformat() < str(e.get("effective_from"))
            if (e.get("model") == llm.CODE_DEFAULTS[role] and not e.get("effort")
                    and not pending):
                return False, "score already runs on the code default"
        return True, ""
    return False, "this role has no saved App Settings choice"


def decision_save_message(result, snapshot_state, role, effective_from=None,
                          action="save") -> tuple[str, str]:
    """(level, text) for a decision save or reset. A saved score change names
    when it applies; the other roles use their next-call wording."""
    level, text = save_outcome_message(result, snapshot_state, action=action)
    if level == "success" and role == llm.ROLE_SCORE and effective_from:
        text = (f"Saved. The score model changes {score_effective_label(effective_from)}; "
                "until then the current model keeps scoring.")
    return level, text


def _snap_warnings(snapshot) -> tuple:
    w = getattr(snapshot, "warnings", None)
    return tuple(w) if isinstance(w, (tuple, list)) else ()


def decision_status(snapshot) -> tuple[str, str, list[str]]:
    """(level, text, warnings not tied to a role) for the decision row."""
    lds = llm_decision_settings
    warnings = _snap_warnings(snapshot)
    others = [w for w in warnings
              if not any(w.startswith(f"{r}:") for r in llm.DECISION_ROLES)]
    state = getattr(snapshot, "state", None)
    if state == lds.STATE_OK:
        as_of = getattr(snapshot, "as_of", None) or "an unknown date"
        by = getattr(snapshot, "updated_by", None) or "unknown"
        text = f"Decision-model row saved {as_of} by {by}"
        if getattr(snapshot, "stale", False):
            return ("warn",
                    text + " (the last re-read failed; serving the last good copy)",
                    [w for w in others if w != lds.WARN_STALE])
        return ("ok", text, others)
    if state == lds.STATE_NO_ROW:
        return ("ok", "No decision-model row: score, filter and extract_trigger are on "
                      "their code defaults", others)
    if state == lds.STATE_INVALID:
        return ("error", "The decision-model row is invalid; code defaults in use", others)
    return ("error", "The decision-model row couldn't be read; code defaults in use",
            [w for w in others if w != lds.WARN_UNREADABLE])


def decision_role_view(role, snapshot, now_utc: datetime) -> dict:
    """What a decision role runs on today and why, for rendering."""
    lds = llm_decision_settings
    entries = snapshot.entries if getattr(snapshot, "state", None) == lds.STATE_OK else {}
    entry = entries.get(role)
    today = _as_utc(now_utc).date().isoformat()
    rs = None
    if isinstance(entry, dict):
        rs = (lds.score_entry_for(entry, today) if role == llm.ROLE_SCORE
              else lds._to_setting(entry))
    model = rs.model if rs else llm.CODE_DEFAULTS[role]
    effort = rs.effort if rs else None
    out = {
        "role": role, "model": model, "effort": effort,
        "source": llm.SOURCE_SETTING if rs else llm.SOURCE_CODE,
        "source_label": "App Settings (eval-gated)" if rs else "code default",
        "effort_text": _effort_text(effort),
        "effective_line": None, "prior_line": None, "cumulative_line": None,
        "warnings": [w for w in _snap_warnings(snapshot)
                     if w.startswith(f"{role}:")],
    }
    if role == llm.ROLE_SCORE and isinstance(entry, dict):
        eff = entry.get("effective_from")
        label = score_effective_label(eff)
        if today >= eff:
            out["effective_line"] = f"This entry applies {label}."
        else:
            out["effective_line"] = (f"Pending: {entry.get('model')} applies {label}; "
                                     f"until then {model} scores.")
        prior = entry.get("prior")
        out["prior_line"] = "Prior model: " + (
            f"{prior.get('model')} ({_effort_text(prior.get('effort'))})"
            if isinstance(prior, dict) else
            f"code default ({llm.CODE_DEFAULTS[role]})")
        try:
            cum = lds.score_cumulative_for(entry, today)
        except Exception:  # noqa: BLE001
            cum = 0.0
        cum = float(cum) if _finite(cum) else 0.0
        after = entry.get("cumulative_shift_pts")
        pend_txt = ""
        if (isinstance(entry.get("effective_from"), str) and today < entry["effective_from"]
                and _finite(after)):
            pend_txt = f"; {float(after):+.2f} pts after the pending switch"
        out["cumulative_line"] = (
            f"Cumulative score shift across switches today: {cum:+.2f} pts{pend_txt} "
            f"(limit ±{LLM_EVAL_SCORE_MAX_CUMULATIVE_SHIFT_PTS:g}, checked at Save).")
    return out


def _num_text(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "not measured"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return f"{round(v, 4):g}"
    return str(v)


def eval_summary(eval_row):
    """A renderable view of one ``llm_role_evals`` row (None for no row)."""
    if not isinstance(eval_row, dict):
        return None
    from stock_analyzer import llm_role_eval
    metrics = eval_row.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    try:
        table = llm_role_eval.limit_table(eval_row.get("role"))
    except KeyError:
        table = []
    rows = []
    for key, const, limit, label in table:
        v = metrics.get(key)
        rows.append({"label": label, "value": _num_text(v), "limit": _num_text(limit),
                     "constant": const, "within": _finite(v) and v <= limit})
    mr, total = metrics.get("max_rows"), metrics.get("sample_total")
    subsample = (f"subsample {min(mr, total)} of {total}"
                 if _finite(mr) and _finite(total) and mr > 0 else None)
    ran = _as_utc(eval_row.get("ran_at"))
    failures = eval_row.get("failures")
    return {
        "subsample": subsample,
        "verdict": "PASS" if eval_row.get("passed") else "FAIL",
        "passed": bool(eval_row.get("passed")),
        "ran_at": _et_stamp(ran) if ran else "an unknown time",
        "sample_n": eval_row.get("sample_n"),
        "baseline": eval_row.get("baseline"),
        "criteria_version": eval_row.get("criteria_version"),
        "eval_id": eval_row.get("id"),
        "failures": [str(f) for f in failures] if isinstance(failures, list) else [],
        "metrics": rows,
    }


def _describe_decision(entry) -> str:
    if not isinstance(entry, dict):
        return "code default"
    text = _describe(entry)
    if entry.get("effective_from"):
        text += f" from {entry['effective_from']}"
    return text


def decision_history_lines(payload, older_payload) -> list[str]:
    """One line per decision-role change between two ``llm_models_decision``
    payloads (``checked_at`` ignored); ``older_payload`` None = first capture."""
    roles = _roles_of(payload)
    if older_payload is None:
        if not roles:
            return ["Initial capture: no role set (every decision role on its code default)"]
        return ["Initial capture: " + ", ".join(
            f"{r}: {_describe_decision(roles[r])}" for r in sorted(roles))]
    older = _roles_of(older_payload)
    lines = []
    for r in sorted(set(roles) | set(older)):
        new, old = roles.get(r), older.get(r)
        if new is None and old is None:
            continue
        if new is None:
            lines.append(f"{r}: {_describe_decision(old)} → code default (reset)")
        elif old is None or (new.get("model"), new.get("effort"),
                             new.get("effective_from")) != (
                old.get("model"), old.get("effort"), old.get("effective_from")):
            lines.append(f"{r}: {_describe_decision(old)} → {_describe_decision(new)}")
        elif new.get("capabilities") != old.get("capabilities"):
            lines.append(f"{r}: {_describe_decision(new)}, stored capabilities updated")
    return lines or ["No role changes"]
