"""
🔎 Portfolio Investigator — multi-step, tool-composing LLM orchestration over
a FIXED, already-reviewed toolbox of pure `stock_analyzer/*.py` functions.

See docs/plans/portfolio-investigator.md for the full ratified design — the
"`planner` pass — 2026-09-18" section near the bottom is the load-bearing
implementation spec this module follows. Grew out of a 2026-09-17 brainstorm
session that manually composed several already-reviewed functions to answer
real questions about the owner's own trading; this module turns that
*workflow* into an on-demand capability instead of a one-off session artifact.

Hard redlines enforced in CODE here, not by asking the LLM nicely:
  - The agent may only name a function already registered in TOOLBOX.
    `validate_plan()` rejects any unrecognized `fn_id` UNCONDITIONALLY — no
    fuzzy matching, no "close enough."
  - Refusal is a closed-enum CODE decision (`validate_plan`'s return status),
    never the LLM's own self-report of "I can't answer this."
  - Never computes on partial/failed data — `verify_fetch()` gates
    `execute_plan()`; a missing/failed required input stops the investigation
    with a visible message, never a silent degrade.
  - A fixed, bounded pipeline, never an open-ended agentic loop —
    `INVESTIGATOR_MAX_LLM_CALLS` caps total LLM calls per investigation (2
    normal: plan + report; a 3rd only for exactly one re-plan attempt).
  - `build_sync_draft()` performs ZERO filesystem I/O. The running container's
    filesystem is ephemeral and has no path to the owner's git working tree or
    local memory files — an in-app "write" would create a phantom edit nobody
    ever sees while looking like it succeeded. The owner copies/downloads the
    draft and pastes it into the repo themselves (see the plan doc's
    "THE DEFECT" section for the full reasoning).
  - Read-only always: nothing in this module writes to `trades`,
    `constants.py`, or any production table. Awareness-only — never
    influences a gate, score, or recommendation.

Pure logic — no Streamlit imports, no app.py imports. Deliberately does NOT
import `stock_analyzer.ai_provider` (a sibling module built in parallel): the
caller injects an `llm_fn` callable so this module never depends on which
provider/model is configured.
"""

from __future__ import annotations

import json

from stock_analyzer.constants import (
    INVESTIGATOR_MAX_LLM_CALLS,
    INVESTIGATOR_MAX_TOKENS_PLAN,
    INVESTIGATOR_MAX_TOKENS_REPORT,
    QA_HISTORY_TURNS,
    QA_REC_OUTCOME_DEFAULT_HORIZON_DAYS,
    REC_SCORE_MIN_DAYS,
    PROTECT_TRACK_MIN_CALLS,
    PROTECT_TRACK_FIRM_CALLS,
    SELF_TRACK_SELL_RELIABLE_LOG_START,
    SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
)
# Reused verbatim, not reimplemented — portfolio_qa.py's balanced-brace JSON
# extraction already handles the "LLM wraps JSON in prose despite being told
# not to" case. Importing the exact function, not copy-pasting it, so a future
# fix there can't silently drift out of sync with this module's copy.
from stock_analyzer.portfolio_qa import _extract_json_object


# ─── Fixed input vocabulary ──────────────────────────────────────────────────
# The ONLY data_bundle keys any toolbox entry's "needs" may name. Closed by
# design (docs/plans/portfolio-investigator.md: "the toolbox IS the
# data-access boundary") — extending this is a deliberate, reviewed toolbox
# change, never something a plan step can request ad hoc.
AVAILABLE_INPUT_VOCAB = frozenset({
    "trades_df", "exit_signals_df", "recs_df",
    "current_prices", "spy_close_by_date", "port_df",
})

# Keys where an EMPTY-but-present container is treated the same as a failed
# fetch (see verify_fetch). port_df mirrors this app's own existing
# convention (portfolio_qa.current_holding/portfolio_summary already treat an
# empty port_df as "portfolio_not_loaded", not "checked, nothing held").
# current_prices/spy_close_by_date mirror the design doc's own example
# ("fetch_live_prices returning {} when it should have live prices") — an
# empty result from a live fetch that was actually attempted is suspicious,
# not a legitimate "nothing to report" state. trades_df/exit_signals_df/
# recs_df are deliberately EXCLUDED — a genuinely empty trade history, zero
# exit signals ever fired, or zero recommendations on record are all
# legitimate real states elsewhere in this app (e.g. classify_sells'
# documented None-vs-empty distinction for exit_signals_df).
_EMPTY_IS_FAILURE_KEYS = frozenset({"port_df", "current_prices", "spy_close_by_date"})


# ─── Ship-gate: models with a recorded refusal-eval PASS ────────────────────
# A model may only be offered in the tab's selector once scripts/investigator_eval.py
# has recorded a clean pass for it (docs/plans/portfolio-investigator.md, ratified
# decision 3). Add an entry here ONLY after running that eval for real against a
# live API key and confirming 0 misclassifications -- never speculative, never
# automatic. Mirrors how ai_provider.AI_PROVIDERS itself only grows one deliberate,
# reviewed model at a time.
EVAL_PASSED_MODELS: frozenset = frozenset({
    ("Claude (Anthropic)", "claude-sonnet-4-6"),  # 34/34, 2026-09-18
    ("Claude (Anthropic)", "claude-opus-5"),      # 34/34, 2026-09-18 (after the
                                                    # ThinkingBlock content[0] fix)
})


def _or_empty(value, empty):
    """Explicit `is None` check — never `value or empty`. Behaviorally the
    same for every call site in this module (verify_fetch has already gated
    None/failed-empty required inputs upstream of any adapter call, so an
    adapter-level default never needs to distinguish "offline" from "checked,
    empty" the way a session_state cache read does), but written as an
    explicit check anyway so the source matches this project's standing
    offline-vs-checked-empty convention everywhere, not just where it
    currently matters (stock_analyzer.util.get_or_offline)."""
    return empty if value is None else value


def _plan_steps(plan) -> list:
    """Explicit `is None`-safe extraction of plan["steps"] — same rationale
    as _or_empty above."""
    if plan is None:
        return []
    steps = plan.get("steps")
    return [] if steps is None else steps


def _df_to_records(df) -> list:
    """DataFrame -> list[dict] for JSON-friendly facts; passes a plain list
    through unchanged; anything else (None, a dict) degrades to []. Adapters
    use this so execute_plan's collected `facts` are always safe to
    json.dumps in build_report_prompt."""
    if df is None:
        return []
    if isinstance(df, list):
        return df
    if hasattr(df, "to_dict"):
        try:
            if getattr(df, "empty", False):
                return []
        except Exception:
            pass
        try:
            return df.to_dict(orient="records")
        except Exception:
            return []
    return []


# ─── TOOLBOX ─────────────────────────────────────────────────────────────────
# Each entry: adapter (data_bundle -> result dict, never raises to the caller
# uncaught — execute_plan wraps every call anyway, but adapters still try to
# degrade gracefully on their own missing inputs), needs (frozenset drawn from
# AVAILABLE_INPUT_VOCAB), summary (shown to the plan LLM), cannot (1-3
# out-of-scope example phrasings for THIS function, shown as negative examples
# in the plan prompt — kept PER-ENTRY rather than one global list, since each
# function's blind spots are specific to what it actually measures and a
# global list would either be too vague to guide the plan step or would grow
# unboundedly as more functions join the toolbox).

def _adapt_closed_lots(data_bundle: dict) -> dict:
    from stock_analyzer.investor_mirror import build_closed_lots
    trades_df = data_bundle.get("trades_df")
    if trades_df is None:
        return {"error": "no trade history available"}
    lots_df = build_closed_lots(trades_df)
    return {"closed_lots": _df_to_records(lots_df)}


def _adapt_classify_sells(data_bundle: dict) -> dict:
    from stock_analyzer.self_track_record import classify_sells
    trades_df = data_bundle.get("trades_df")
    exit_signals_df = data_bundle.get("exit_signals_df")
    classified = classify_sells(
        trades_df, exit_signals_df,
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    if classified is None:
        return {"error": "exit-signal history unavailable — can't classify sells"}
    return {"classified_sells": classified}


def _adapt_protective_outcomes(data_bundle: dict) -> dict:
    from stock_analyzer.protective_track_record import (
        compute_protective_outcomes, collapse_by_ticker, protective_headline,
    )
    from stock_analyzer.market_time import today_et
    signals_df = data_bundle.get("exit_signals_df")
    current_prices = _or_empty(data_bundle.get("current_prices"), {})
    spy_close_by_date = _or_empty(data_bundle.get("spy_close_by_date"), {})
    if signals_df is None:
        return {"error": "no exit-signal history available"}
    scoped = signals_df
    try:
        if not signals_df.empty and "signal_type" in signals_df.columns:
            scoped = signals_df[signals_df["signal_type"].isin(["EXIT", "TRIM"])]
    except AttributeError:
        pass  # list-of-dicts form — compute_protective_outcomes filters scope itself
    enriched = compute_protective_outcomes(
        scoped, current_prices, today_et(), spy_close_by_date, REC_SCORE_MIN_DAYS,
    )
    collapsed = collapse_by_ticker(enriched)
    headline = protective_headline(collapsed, PROTECT_TRACK_MIN_CALLS, PROTECT_TRACK_FIRM_CALLS)
    return {"protective_outcomes_by_ticker": collapsed, "protective_headline": headline}


def _adapt_recalculate_holdings(data_bundle: dict) -> dict:
    from stock_analyzer.db import recalculate_from_trades
    trades_df = data_bundle.get("trades_df")
    if trades_df is None:
        return {"error": "no trade history available"}
    result = recalculate_from_trades(trades_df)
    return {
        "holdings": _df_to_records(result.get("holdings_df")),
        "realized_pnl_corrections": result.get("realized_pnl_corrections", {}),
        "warnings": result.get("warnings", []),
    }


def _adapt_rec_outcomes(data_bundle: dict) -> dict:
    from stock_analyzer.recommendations_history import match_recs_to_trades, compute_outcomes
    from stock_analyzer.market_time import today_et
    recs_df = data_bundle.get("recs_df")
    trades_df = data_bundle.get("trades_df")
    current_prices = _or_empty(data_bundle.get("current_prices"), {})
    spy_close_by_date = _or_empty(data_bundle.get("spy_close_by_date"), {})
    if recs_df is None:
        return {"error": "no recommendation history available"}
    matched = match_recs_to_trades(recs_df, trades_df)
    outcomes = compute_outcomes(
        matched, current_prices, today_et(), spy_close_by_date, min_days=REC_SCORE_MIN_DAYS,
    )
    return {"recommendation_outcomes": outcomes}


def _adapt_ticker_sectors(data_bundle: dict) -> dict:
    from stock_analyzer.portfolio import TICKER_SECTORS
    return {"ticker_sectors": dict(TICKER_SECTORS)}


def _adapt_sector_exposure(data_bundle: dict) -> dict:
    """Dollar-weighted sector concentration for currently-held positions --
    added 2026-09-18 after a real live investigation ("how concentrated is my
    portfolio by sector?") could only answer by POSITION COUNT, because no
    toolbox function surfaced a per-position market value or weight
    (recalculate_from_trades's holdings_df is deliberately just
    [Ticker, Shares, Avg Cost ($)] -- market value/weight is out of scope for
    what that function exists to fix). Reuses portfolio_qa.sector_composition()
    verbatim (itself a thin, already-reviewed wrapper around
    portfolio.sector_exposure()) rather than re-deriving the sum/pct math --
    same numbers Portfolio Overview's own sector chart shows, so this can
    never disagree with that page. Also more complete than the ticker_sectors
    toolbox entry's bare static lookup: port_df's own Sector column already
    went through the live app's full curated-map -> .info -> cache -> "Other"
    resolution chain, not just the raw curated dict."""
    from stock_analyzer.portfolio_qa import sector_composition
    port_df = data_bundle.get("port_df")
    if port_df is None:
        return {"error": "portfolio not loaded this session"}
    exposure = sector_composition(port_df)
    if not exposure:
        return {"error": "no sector exposure could be computed for the current portfolio"}
    return {"sector_exposure": exposure}


def _adapt_live_prices(data_bundle: dict) -> dict:
    """Trivial passthrough — the live fetch itself already happened in the
    app's own Fetch stage (stage 2), BEFORE investigate() is ever called;
    Compute (stage 4) only reads already-fetched data, it never re-fetches."""
    return {"current_prices": _or_empty(data_bundle.get("current_prices"), {})}


def _adapt_spy_history(data_bundle: dict) -> dict:
    """Trivial passthrough — see _adapt_live_prices; SPY history is fetched
    upstream, not re-fetched here."""
    return {"spy_close_by_date": _or_empty(data_bundle.get("spy_close_by_date"), {})}


TOOLBOX: dict = {
    "closed_lots": {
        "adapter": _adapt_closed_lots,
        "needs": frozenset({"trades_df"}),
        "summary": "Holding-period distributions and realized P&L for every "
                   "closed (fully sold) lot, FIFO-matched from the trade log.",
        "cannot": [
            "cannot answer about a position that is still open (not yet sold)",
            "cannot simulate a hypothetical alternate exit date or price",
        ],
    },
    "classify_sells": {
        "adapter": _adapt_classify_sells,
        "needs": frozenset({"trades_df", "exit_signals_df"}),
        "summary": "Classifies every SELL trade as app-aligned (followed an "
                   "active EXIT/TRIM signal within the matching window) vs. "
                   "self-initiated (no matching signal on file).",
        "cannot": [
            "cannot judge a BUY-side decision (see rec_outcomes for that)",
            "cannot classify a sell from before exit-signal capture went live",
        ],
    },
    "protective_outcomes": {
        "adapter": _adapt_protective_outcomes,
        "needs": frozenset({"exit_signals_df", "current_prices", "spy_close_by_date"}),
        "summary": "Alpha vs. SPY for every EXIT/TRIM protective call ever "
                   "issued, per ticker and in aggregate (the Defense facet's "
                   "own track-record calculation).",
        "cannot": [
            "cannot evaluate a WATCH or RISK_OFF signal — scope is EXIT/TRIM only",
            "cannot simulate a hypothetical stop width or earlier/later exit",
        ],
    },
    "recalculate_holdings": {
        "adapter": _adapt_recalculate_holdings,
        "needs": frozenset({"trades_df"}),
        "summary": "Replays the full trade log chronologically to derive the "
                   "truthful current holdings and corrected realized P&L for "
                   "every SELL — the same reconciliation used to catch "
                   "holdings drift after a deleted/corrected trade.",
        "cannot": [
            "cannot explain WHY a trade was made — only replays the arithmetic",
        ],
    },
    "rec_outcomes": {
        "adapter": _adapt_rec_outcomes,
        "needs": frozenset({"recs_df", "trades_df", "current_prices", "spy_close_by_date"}),
        "summary": "Matches past recommendations to same-day trades and "
                   "scores acted vs. skipped outcomes against SPY (the "
                   "Offense facet's own BUY-side track-record calculation).",
        "cannot": [
            "cannot score an acted SELL's alpha (unbenchmarkable holding period by design)",
            "cannot answer about a ticker with no recommendation ever surfaced for it",
            "cannot isolate the outcome of ONE single named recommendation by "
            "ticker and date (e.g. \"AAPL's alpha after the March 3rd BUY\") "
            "-- only returns the full acted-vs-skipped track record across "
            "every recommendation, never a per-instance result",
        ],
    },
    # forward_alpha_at_horizon is DELIBERATELY not registered for v1 — see
    # docs/plans/portfolio-investigator.md's "Deferred v1.1" note. It answers
    # a SINGLE ticker/date question, but the plan step's {"fn_id","why"} shape
    # has no per-step argument slot for a ticker/date/price scalar, and no
    # question-side resolver exists yet to populate one. Registering it would
    # let the plan step select a function that can only ever degrade to an
    # error fact today -- a known-incomplete option, not a working one.
    "ticker_sectors": {
        "adapter": _adapt_ticker_sectors,
        "needs": frozenset(),
        "summary": "Static ticker-to-sector lookup for grouping any other "
                   "result by sector (no network call, no fetch needed).",
        "cannot": [
            "cannot classify a ticker not already in the curated sector map",
        ],
    },
    "sector_exposure": {
        "adapter": _adapt_sector_exposure,
        "needs": frozenset({"port_df"}),
        "summary": "Dollar-weighted sector concentration for currently-held "
                   "positions -- market value and % of book per sector, the "
                   "SAME numbers the live Portfolio Overview sector chart "
                   "shows. Use this, not ticker_sectors + manual counting, "
                   "for any question about how concentrated the portfolio "
                   "actually is by dollar exposure.",
        "cannot": [
            "cannot answer about a candidate/not-currently-held ticker",
            "cannot answer about a past point in time -- reflects only the "
            "currently-loaded portfolio snapshot",
        ],
    },
    "live_prices": {
        "adapter": _adapt_live_prices,
        "needs": frozenset({"current_prices"}),
        "summary": "Already-fetched current market prices, keyed by ticker "
                   "(supporting data for other steps, not an answer on its own).",
        "cannot": [
            "cannot fetch a NEW ticker's price — only returns what was already loaded",
        ],
    },
    "spy_history": {
        "adapter": _adapt_spy_history,
        "needs": frozenset({"spy_close_by_date"}),
        "summary": "Already-fetched SPY daily closes by date (supporting "
                   "benchmark data for other steps, not an answer on its own).",
        "cannot": [
            "cannot fetch a different benchmark ticker's history",
        ],
    },
}


# ─── Plan-prompt construction ────────────────────────────────────────────────

def build_plan_prompt(question: str, history_questions: list | None = None) -> str:
    """Full, self-contained planning prompt: enumerates the fixed toolbox
    (id, summary, needs, cannot), states the question, and instructs
    strict-JSON-only output in the exact shape validate_plan()/parse_plan()
    expect. If history_questions is given, only the prior QUESTION TEXT is
    included (never a prior report/answer) — a deliberate, ratified redline
    against feeding narrated conclusions back into planning (see the plan
    doc's Session Shape section): a soft conclusion trusted as hard fact is
    exactly the failure mode this avoids."""
    lines = [
        "You are the planning step of a portfolio investigation tool. You may "
        "ONLY select from the fixed toolbox of already-reviewed functions "
        "listed below — you cannot write new analysis logic, run arbitrary "
        "code, or read any data source not named here. \"I don't have a "
        "reviewed function for that\" is a normal, honest, and frequently "
        "correct answer.",
        "",
        "Toolbox (id — summary — needs — cannot):",
    ]
    for fn_id, entry in TOOLBOX.items():
        needs_str = ", ".join(sorted(entry["needs"])) or "(none)"
        cannot_str = "; ".join(entry.get("cannot", [])) or "(none noted)"
        lines.append(f"- {fn_id}: {entry['summary']} | needs: {needs_str} | cannot: {cannot_str}")

    if history_questions:
        turns = list(history_questions)[-QA_HISTORY_TURNS:]
        lines.append("")
        lines.append(
            "Prior questions this session (most recent last) — use ONLY to "
            "resolve a referential follow-up (e.g. \"what about X instead\"); "
            "you are given the QUESTION TEXT only, never a prior answer, and "
            "must still produce a fresh, complete plan for the NEW question "
            "below:"
        )
        for q in turns:
            lines.append(f"- {str(q).strip()[:200]}")

    lines.append("")
    lines.append(f"Question: {str(question).strip()}")
    lines.append("")
    lines.append(
        "Respond with ONLY a JSON object, no other text before or after. "
        "Exactly one of these two shapes:\n"
        '1) {"answerable": true, "steps": [{"fn_id": "<toolbox id>", '
        '"why": "<short reason>"}, ...]} — name ONLY real toolbox ids from '
        "the list above, in the order they should run. At least one step is "
        "required.\n"
        '2) {"answerable": false, "reason": "<short, honest explanation of '
        'why this can\'t be answered with the current toolbox>", '
        '"closest_function_or_none": "<toolbox id closest to being useful, '
        'or null>"}\n\n'
        "Rules:\n"
        "- Use shape 2 whenever no combination of the toolbox functions "
        "above actually answers the question — do not force a poor-fit "
        "function into shape 1 just to produce an answer.\n"
        "- A question asking for an open-ended overall verdict with no "
        "named analytical angle (e.g. \"is my portfolio good?\", \"am I "
        "doing well as an investor?\") is NOT answered by chaining together "
        "several functions that each cover a different slice — that produces "
        "a pile of facts, not an answer to the question asked. Use shape 2 "
        "and ask the investor to name a specific angle (a metric, a time "
        "period, a comparison) instead of guessing one.\n"
        "- Never invent a fn_id that isn't in the list above.\n"
        "- Output the JSON object and NOTHING else — no markdown code fence, "
        "no explanation before or after it."
    )
    return "\n".join(lines)


def parse_plan(raw_text: str) -> dict | None:
    """Extract and shape-validate a plan JSON object from raw LLM output.
    Returns None on ANY parse/shape failure — never raises. Reuses
    portfolio_qa._extract_json_object for the same balanced-brace extraction
    (LLMs sometimes wrap JSON in prose despite instructions not to)."""
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].strip()
    extracted = _extract_json_object(cleaned)
    if extracted is not None:
        cleaned = extracted
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None

    answerable = parsed.get("answerable")
    if answerable is True:
        steps = parsed.get("steps")
        if not isinstance(steps, list):
            return None
        clean_steps = []
        for s in steps:
            if not isinstance(s, dict):
                return None
            fn_id = s.get("fn_id")
            if not isinstance(fn_id, str) or not fn_id.strip():
                return None
            clean_steps.append({"fn_id": fn_id.strip(), "why": str(s.get("why") or "").strip()})
        return {"answerable": True, "steps": clean_steps}

    if answerable is False:
        reason = parsed.get("reason")
        reason = str(reason).strip() if reason else "not answerable with the current toolbox"
        cfn = parsed.get("closest_function_or_none")
        cfn = str(cfn).strip() if isinstance(cfn, str) and cfn.strip() else None
        return {"answerable": False, "reason": reason, "closest_function_or_none": cfn}

    return None


def validate_plan(plan: dict, available_inputs) -> tuple:
    """THE safety-critical code gate — refusal is a closed-enum CODE decision
    over the plan's own structure, never the LLM's self-report. Returns
    (status, human_reason):
      "refuse"      — answerable is False, OR steps is empty/missing, OR any
                      step names a fn_id not in TOOLBOX. Unconditional — no
                      fuzzy matching, no partial credit.
      "infeasible"  — every fn_id is real and steps is non-empty, but the
                      union of their `needs` isn't a subset of
                      available_inputs (names the missing input).
      "ok"          — a valid, satisfiable, non-empty plan.
    `available_inputs`: the subset of AVAILABLE_INPUT_VOCAB the caller has
    actually fetched for this session (set|frozenset of strings) — this is a
    FEASIBILITY check only; whether a present input is genuinely usable
    (non-empty, not a failed fetch) is verify_fetch's separate job, run right
    before execute_plan."""
    if not isinstance(plan, dict):
        return ("refuse", "no valid plan was produced")

    if plan.get("answerable") is False:
        return ("refuse", plan.get("reason") or "not answerable with the current toolbox")

    steps = plan.get("steps")
    if not steps:
        return ("refuse", "the plan named no toolbox functions to run")

    unknown = sorted({
        str(s.get("fn_id")) for s in steps if s.get("fn_id") not in TOOLBOX
    })
    if unknown:
        return ("refuse", f"plan named a function not in the toolbox: {', '.join(unknown)}")

    avail = frozenset(available_inputs or ())
    needed: frozenset = frozenset()
    for s in steps:
        needed = needed | TOOLBOX[s["fn_id"]]["needs"]
    missing = sorted(needed - avail)
    if missing:
        return ("infeasible", f"missing required input(s): {', '.join(missing)}")

    return ("ok", "")


def verify_fetch(fetched: dict) -> tuple:
    """Given ONLY the subset of the data_bundle the plan's combined `needs`
    actually asked for (the caller narrows to just those keys before calling
    this — a key never requested by the plan must never be checked here),
    returns (True, "") when everything requested looks genuinely populated,
    or (False, "<what failed>") the first time something looks like a failed
    fetch. A value of None is always a failure. For port_df/current_prices/
    spy_close_by_date specifically, an empty-but-present container also
    counts as a failure (see _EMPTY_IS_FAILURE_KEYS for why — mirrors this
    app's own existing "empty port_df means not loaded" convention).
    trades_df/exit_signals_df/recs_df being genuinely empty (zero trades
    ever made, no signal ever fired, no recommendation on record) is a
    legitimate state, never flagged here."""
    if not fetched:
        return (True, "")
    failed = []
    for key, value in fetched.items():
        if value is None:
            failed.append(key)
            continue
        if key in _EMPTY_IS_FAILURE_KEYS:
            is_empty = getattr(value, "empty", None)
            if is_empty is None:
                try:
                    is_empty = len(value) == 0
                except TypeError:
                    is_empty = False
            if is_empty:
                failed.append(key)
    if failed:
        return (False, f"the following required data didn't load: {', '.join(sorted(failed))}")
    return (True, "")


def execute_plan(plan: dict, data_bundle: dict) -> dict:
    """Calls ONLY the adapters named in plan["steps"], in order, collecting
    results keyed by fn_id. Each call is individually wrapped so one failing
    step never blanks the others — a partial result plus an "errors"
    {fn_id: str} entry is always returned rather than raising."""
    results: dict = {}
    errors: dict = {}
    for step in _plan_steps(plan):
        fn_id = step.get("fn_id")
        entry = TOOLBOX.get(fn_id)
        if entry is None:
            # Should never happen if validate_plan ran first — defensive only.
            errors[str(fn_id)] = "unknown toolbox function"
            continue
        try:
            results[fn_id] = entry["adapter"](data_bundle)
        except Exception as e:
            errors[fn_id] = f"{type(e).__name__}: {e}"[:300]
    results["errors"] = errors
    return results


# ─── Report-prompt construction ──────────────────────────────────────────────
# Narration discipline adapted verbatim from portfolio_qa._NARRATE_SYSTEM_PROMPT
# — never invent a number, never combine/rescale given figures, no fabricated
# date-recency language, plus a MANDATORY caveats section (this feature's own
# addition — every scripts/*.py analysis in this repo ends in one).

_REPORT_INSTRUCTIONS = (
    "You are the report-writing step of a portfolio investigation tool. "
    "Write a plain-English report answering the investor's question, using "
    "ONLY the facts given below — never invent a number, a reason, or a data "
    "point that isn't present. If a value is missing or null, say plainly "
    "that it wasn't available rather than guessing why.\n\n"
    "Never add up, average, or otherwise combine two or more of the given "
    "numbers into a NEW total that isn't itself one of the given facts — "
    "state each fact's own number individually. Never rescale or abbreviate "
    "a dollar figure (e.g. turning $14,860 into '$14.9M' or '$14.9K') — "
    "write it exactly as given, in its own units and magnitude.\n\n"
    "Never characterize how recent, old, or long ago a given date is (e.g. "
    "'recent', 'a while ago', 'just happened') — you are not reliably told "
    "today's actual date; state the date itself and let the reader judge.\n\n"
    "Do not recommend any future action, do not restate a decision threshold, "
    "and do not suggest a trade — this is a retrospective/diagnostic report "
    "only, never a recommendation. Same standing posture as every other "
    "awareness-only surface in this app.\n\n"
    "End the report with a section titled exactly 'Caveats:' listing what "
    "limits how much weight this finding should carry — at minimum address "
    "sample size (how many data points this is really based on), "
    "correlation vs. causation (a pattern in the data is not proof of why it "
    "happened), and the time window actually covered. This section is "
    "MANDATORY — never omit it, even when the finding looks clean."
)


def build_report_prompt(question: str, facts: dict, plan: dict) -> str:
    """Full, self-contained report-synthesis prompt: the narration discipline
    above, the functions used (with the plan's own stated "why" for each, so
    the LLM narrates WHY each figure is relevant), and the facts themselves as
    JSON. facts is execute_plan()'s output; plan is the validated plan that
    produced it."""
    steps = _plan_steps(plan)
    fn_lines = [f"- {s.get('fn_id')}: {s.get('why', '')}" for s in steps]
    try:
        facts_json = json.dumps(facts, indent=2, default=str)
    except Exception:
        facts_json = str(facts)

    parts = [
        _REPORT_INSTRUCTIONS,
        "",
        f"Investor's question: {str(question).strip()}",
        "",
        "Functions used to answer this (already-reviewed, deterministic — "
        "you are narrating their output, not deriving new numbers):",
        *fn_lines,
        "",
        "Facts (JSON):",
        facts_json,
    ]
    return "\n".join(parts)


def build_sync_draft(question: str, report_text: str, targets: list) -> list:
    """PURE — zero I/O, zero filesystem access of any kind. Builds candidate
    markdown snippets describing what COULD be pasted into each named target
    (a docs/*.md file, a memory file name, etc.) for the owner to review,
    copy, and paste into the repo themselves. This function must NEVER open
    or write a file — the running container's filesystem is ephemeral and has
    no path to the owner's actual git working tree or local memory files, so
    an in-app write would create a phantom edit nobody ever sees while
    looking like it succeeded (see docs/plans/portfolio-investigator.md,
    "THE DEFECT"). `targets`: list of {"target": str, "hint": str} — `hint`
    is optional freeform context (e.g. "append to the queue section"); this
    function only formats text, it never resolves or touches an actual path.
    """
    drafts = []
    for t in (targets or []):
        target_name = str((t or {}).get("target", "")).strip() or "unspecified target"
        hint = str((t or {}).get("hint", "")).strip()
        header = f"<!-- Portfolio Investigator draft — target: {target_name} -->"
        if hint:
            header += f"\n<!-- {hint} -->"
        body = f"{header}\n\n**Question:** {question}\n\n{report_text}\n"
        drafts.append({"target": target_name, "draft_markdown": body})
    return drafts


# ─── Orchestrator ────────────────────────────────────────────────────────────

LAST_PLAN_ERROR: str | None = None
LAST_REPORT_ERROR: str | None = None


def _available_inputs(data_bundle: dict) -> frozenset:
    """Which of the fixed vocabulary's keys are present (not None) in
    data_bundle — used only for plan FEASIBILITY (validate_plan). An
    empty-but-present container still counts as "available" here; whether it
    came back genuinely usable is verify_fetch's separate, later check."""
    data_bundle = data_bundle or {}
    return frozenset(k for k in AVAILABLE_INPUT_VOCAB if data_bundle.get(k) is not None)


def _needed_keys(plan: dict) -> frozenset:
    needed: frozenset = frozenset()
    for step in _plan_steps(plan):
        entry = TOOLBOX.get(step.get("fn_id"))
        if entry is not None:
            needed = needed | entry["needs"]
    return needed


def investigate(question: str, data_bundle: dict, llm_fn, history_questions: list | None = None,
                 max_calls: int | None = None) -> dict:
    """The 5-stage pipeline (plan / fetch-already-done / verify / compute /
    report), fixed and bounded — never an open-ended agentic loop.

    `llm_fn`: callable (system: str, user: str, max_tokens: int) -> str|None
    — matches ai_provider.call_llm's shape minus the provider/model/key args,
    which the caller has already bound. Must fail open (return None), never
    raise.
    `data_bundle`: plain dict of already-fetched inputs keyed by
    AVAILABLE_INPUT_VOCAB.
    `history_questions`: optional list of prior QUESTION TEXT ONLY
    (never a prior report/answer — see build_plan_prompt).
    `max_calls`: defaults to INVESTIGATOR_MAX_LLM_CALLS.

    Returns a dict:
      question, answered (bool), status (one of "ok"/"refused"/"no_plan"/
      "incomplete_data"/"report_failed"), reason (str|None — the human-
      readable explanation, always populated when answered is False), plan
      (dict|None), facts (dict|None — execute_plan's output), report_text
      (str|None), trace (list[str] — fn_ids actually planned, for the UI's
      always-available trace expander), calls_made (int).

    Never raises. On an LLM-call failure, LAST_PLAN_ERROR / LAST_REPORT_ERROR
    carry the real reason (mirrors portfolio_qa.LAST_PARSE_ERROR).
    """
    global LAST_PLAN_ERROR, LAST_REPORT_ERROR
    LAST_PLAN_ERROR = None
    LAST_REPORT_ERROR = None

    max_calls = INVESTIGATOR_MAX_LLM_CALLS if max_calls is None else max_calls
    data_bundle = data_bundle or {}
    available = _available_inputs(data_bundle)

    calls_made = 0
    plan = None
    status = "no_plan"
    reason = "no attempt was made"
    note = None

    # Plan stage: at most ONE re-plan attempt (2 tries total), and never more
    # than max_calls total regardless — a hard, non-negotiable ceiling.
    for _attempt in range(2):
        if calls_made >= max_calls:
            break
        prompt = build_plan_prompt(question, history_questions)
        if note:
            prompt += (
                f"\n\nYour previous attempt was rejected: {note}\n"
                "Produce a corrected plan."
            )
        raw = llm_fn(prompt, str(question), INVESTIGATOR_MAX_TOKENS_PLAN)
        calls_made += 1
        if raw is None:
            LAST_PLAN_ERROR = "the planning model call failed"
            plan, status, reason = None, "no_plan", "the planning model call failed"
            note = reason
            continue
        plan = parse_plan(raw)
        if plan is None:
            status, reason = "no_plan", "the model's response wasn't valid JSON in the expected shape"
            note = reason
            continue
        status, reason = validate_plan(plan, available)
        if status == "refuse":
            return {
                "question": question, "answered": False, "status": "refused",
                "reason": reason, "plan": plan, "facts": None,
                "report_text": None, "trace": [], "calls_made": calls_made,
            }
        if status == "ok":
            break
        note = reason  # "infeasible" -> retry once with the note, if budget allows

    if status != "ok":
        return {
            "question": question, "answered": False, "status": "no_plan",
            "reason": f"couldn't converge on a plan — {reason}",
            "plan": plan, "facts": None, "report_text": None, "trace": [],
            "calls_made": calls_made,
        }

    trace = [s.get("fn_id") for s in _plan_steps(plan)]

    needed = _needed_keys(plan)
    fetched_subset = {k: data_bundle.get(k) for k in needed}
    ok, why = verify_fetch(fetched_subset)
    if not ok:
        return {
            "question": question, "answered": False, "status": "incomplete_data",
            "reason": f"couldn't complete this — {why}",
            "plan": plan, "facts": None, "report_text": None, "trace": trace,
            "calls_made": calls_made,
        }

    if calls_made >= max_calls:
        return {
            "question": question, "answered": False, "status": "no_plan",
            "reason": "couldn't converge on a plan within the call budget for this investigation",
            "plan": plan, "facts": None, "report_text": None, "trace": trace,
            "calls_made": calls_made,
        }

    facts = execute_plan(plan, data_bundle)

    report_prompt = build_report_prompt(question, facts, plan)
    raw_report = llm_fn(report_prompt, str(question), INVESTIGATOR_MAX_TOKENS_REPORT)
    calls_made += 1
    if raw_report is None:
        LAST_REPORT_ERROR = "the report-synthesis model call failed"
        return {
            "question": question, "answered": False, "status": "report_failed",
            "reason": "the investigation completed but the report couldn't be written",
            "plan": plan, "facts": facts, "report_text": None, "trace": trace,
            "calls_made": calls_made,
        }

    return {
        "question": question, "answered": True, "status": "ok", "reason": None,
        "plan": plan, "facts": facts, "report_text": raw_report, "trace": trace,
        "calls_made": calls_made,
    }
