"""
Thesis Advisor — F-1 AI Intelligence Layer.

Periodically reviews a user's written investment thesis against current
evidence (news, fundamentals, technical trend) and returns a structured
verdict: INTACT, WEAKENING, or BROKEN.

Design principles:
- LLM narrates only what it is given — it cannot invent news or events.
- Returns None on any failure so callers surface an explicit offline state.
- Conservative by default: BROKEN requires clear contradicting evidence.
- All thresholds and gates remain with the rule-based engine; this module
  only produces awareness text.

Entry points:
  review_thesis()     — single-position review (on-demand or batch).
  run_batch_review()  — weekly batch across all open positions with a thesis.
  build_review_inputs() — assembles the structured evidence package.
"""

import hashlib
import json
from datetime import date, datetime, timezone


# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a disciplined portfolio analyst helping an individual investor check whether their original investment thesis still holds.

Your job: given the investor's original thesis and current evidence, assess whether the thesis is INTACT, WEAKENING, or BROKEN. Write a concise 2-3 sentence explanation.

Rules:
- Only use facts from the evidence provided. Do not invent events, price targets, or analyst opinions.
- INTACT: evidence is broadly consistent with the original conviction.
- WEAKENING: some evidence contradicts; not yet decisive. Use this when signals are mixed.
- BROKEN: evidence materially contradicts the key condition the investor stated, or the core premise has clearly reversed.
- Be conservative: default to WEAKENING when uncertain. BROKEN requires a clear, specific contradiction.
- Do not recommend buying, selling, or any portfolio action. Observation only.
- Do not add disclaimers. Plain language. No bullet points. Prose only.
- End your response with exactly one verdict line on its own:
    Verdict: INTACT
    Verdict: WEAKENING
    Verdict: BROKEN"""


def _format_prompt(ticker: str, user_thesis: str, inputs: dict) -> str:
    lines = [
        f"Ticker: {ticker}",
        f"\nOriginal investment thesis:\n\"{user_thesis}\"",
        "\nCurrent evidence:",
    ]

    tech = inputs.get("technical", {})
    if tech:
        trend = "above" if tech.get("above_sma50") else "below"
        rsi   = tech.get("rsi")
        mom   = tech.get("momentum_1m_pct")
        parts = [f"Price is {trend} the 50-day moving average."]
        if rsi is not None:
            zone = "overbought (>70)" if rsi > 70 else ("oversold (<30)" if rsi < 30 else "neutral")
            parts.append(f"RSI {rsi:.0f} ({zone}).")
        if mom is not None:
            parts.append(f"1-month price change: {mom:+.1f}%.")
        lines.append("Technical: " + " ".join(parts))

    fund = inputs.get("fundamentals", {})
    if fund:
        parts = []
        if fund.get("revenue_growth") is not None:
            parts.append(f"Revenue growth: {fund['revenue_growth']:+.1f}%.")
        if fund.get("profit_margin") is not None:
            parts.append(f"Profit margin: {fund['profit_margin']:.1f}%.")
        if fund.get("earnings_trend"):
            parts.append(f"Earnings trend: {fund['earnings_trend']}.")
        if parts:
            lines.append("Fundamentals: " + " ".join(parts))

    headlines = inputs.get("news_headlines", [])
    if headlines:
        lines.append(f"Recent news ({len(headlines)} headlines):")
        for h in headlines[:12]:
            lines.append(f"  - {h}")

    earnings = inputs.get("last_earnings", {})
    if earnings:
        parts = []
        if earnings.get("result"):
            parts.append(f"Last earnings: {earnings['result']}.")
        if earnings.get("guidance"):
            parts.append(f"Guidance: {earnings['guidance']}.")
        if parts:
            lines.append("Earnings: " + " ".join(parts))

    return "\n".join(lines)


def _parse_response(text: str) -> dict:
    """Extract status and summary from LLM response."""
    status  = "WEAKENING"  # safe default
    summary = text.strip()

    lines = [ln.strip() for ln in text.strip().splitlines()]
    for ln in reversed(lines):
        low = ln.lower()
        if "verdict:" in low:
            if "intact" in low:
                status = "INTACT"
            elif "broken" in low:
                status = "BROKEN"
            else:
                status = "WEAKENING"
            # Summary is everything before the verdict line
            verdict_idx = next(
                (i for i, l in enumerate(lines) if "verdict:" in l.lower()), -1
            )
            summary = " ".join(lines[:verdict_idx]).strip() if verdict_idx > 0 else summary
            break

    return {"status": status, "summary": summary}


# ── Public API ────────────────────────────────────────────────────────────────

def build_review_inputs(
    technical: dict | None = None,
    fundamentals: dict | None = None,
    news_headlines: list[str] | None = None,
    last_earnings: dict | None = None,
) -> dict:
    """
    Assemble the structured evidence package passed to review_thesis().

    technical keys (all optional):
        above_sma50 (bool), rsi (float), momentum_1m_pct (float)
    fundamentals keys (all optional):
        revenue_growth (float, %), profit_margin (float, %), earnings_trend (str)
    news_headlines: list of plain-text headline strings (last 30 days)
    last_earnings keys (all optional):
        result (str e.g. "beat EPS by 8%"), guidance (str e.g. "raised FY guidance")
    """
    return {
        "technical":      technical      or {},
        "fundamentals":   fundamentals   or {},
        "news_headlines": news_headlines or [],
        "last_earnings":  last_earnings  or {},
    }


def inputs_hash(inputs: dict) -> str:
    """Stable hash of review inputs — used to detect staleness."""
    serialized = json.dumps(inputs, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def review_thesis(
    ticker: str,
    user_thesis: str,
    inputs: dict,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 300,
) -> dict | None:
    """
    Call the LLM to review a single investment thesis against current evidence.

    Returns a dict with keys:
        status   — 'INTACT' | 'WEAKENING' | 'BROKEN'
        summary  — 2-3 sentence explanation (~100 words)
        raw      — original LLM text
        model    — model used
        reviewed_at — ISO timestamp (UTC)

    Returns None on any failure — caller must surface an explicit offline state.
    """
    if not api_key or not user_thesis or not user_thesis.strip():
        return None
    try:
        import anthropic
        client      = anthropic.Anthropic(api_key=api_key)
        user_prompt = _format_prompt(ticker, user_thesis, inputs)
        response    = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text   = response.content[0].text if response.content else ""
        parsed = _parse_response(text)
        return {
            **parsed,
            "raw":         text,
            "model":       model,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None


def run_batch_review(
    positions: list[dict],
    api_key: str,
    model: str = "claude-sonnet-4-6",
) -> list[dict]:
    """
    Run thesis reviews for a list of open positions.

    Each item in `positions` must have:
        ticker       (str)
        trade_date   (str | date)  — earliest BUY date for this position
        user_thesis  (str)
        inputs       (dict)        — from build_review_inputs()

    Returns a list of save-ready records for db.save_thesis_review(), one per
    position that was reviewed. Positions with no thesis or failed LLM calls
    are silently skipped (caller checks returned list length).
    """
    results = []
    for pos in positions:
        ticker      = pos.get("ticker", "")
        user_thesis = pos.get("user_thesis", "")
        trade_date  = pos.get("trade_date", date.today())
        ev_inputs   = pos.get("inputs", {})

        if not ticker or not user_thesis or not user_thesis.strip():
            continue

        result = review_thesis(ticker, user_thesis, ev_inputs, api_key, model)
        if result is None:
            continue

        results.append({
            "ticker":      ticker,
            "trade_date":  str(trade_date),
            "reviewed_at": result["reviewed_at"],
            "status":      result["status"],
            "summary":     result["summary"],
            "inputs_hash": inputs_hash(ev_inputs),
        })

    return results


# ── Thesis authoring (F-5) ──────────────────────────────────────────────────
#
# Generative complement to the reviewer above. Given the engine's evidence for a
# candidate the user is about to buy, draft a CANDIDATE investment thesis the
# user then edits and owns. Advisory only — this never gates and never decides.
#
# Two invariants (see docs/plans/thesis-authoring-analyst-desk.md):
#   - The user is always the author of record. The draft is offered into an
#     editable field and is NEVER persisted without the user accepting it.
#   - The author (this module) reads entry-time evidence; the reviewer
#     (review_thesis) weights post-entry evidence — they are not the same call.

_DRAFT_SYSTEM_PROMPT = """You are helping an individual investor write the investment thesis for a stock they are about to buy. You draft a CANDIDATE thesis; the investor will edit it and own the final words.

Write the thesis as flowing prose (not labelled sections), covering three things:
1. The durable claim — why this company wins over the medium term: competitive position, demand, a catalyst. This is the conviction, not the entry timing.
2. The supporting evidence — drawn ONLY from the evidence provided below. Do not invent a number, an order book, an analyst opinion, or an event.
3. A falsifiable condition — end with one sentence beginning "Breaks if " that names the specific developments which would invalidate the thesis.

Rules:
- Ground every claim in the evidence given. If the evidence is thin, write a shorter, honest thesis — never pad it with invented facts.
- Price levels, moving averages, RSI and momentum are ENTRY TIMING, not the thesis. Do not build the thesis on them.
- No price targets. No probabilities or odds of success. No buy/sell/hold language. No gate or score values.
- Plain language. No preamble, no disclaimer, no bullet points. Prose only.
- Keep it under 500 characters.
- End with exactly one sentence starting "Breaks if "."""


def _format_authoring_prompt(ticker: str, inputs: dict) -> str:
    lines = [f"Ticker: {ticker}"]
    if inputs.get("company_name"):
        lines.append(f"Company: {inputs['company_name']}")
    if inputs.get("sector"):
        lines.append(f"Sector: {inputs['sector']}")

    lines.append("\nEvidence available:")

    eng = inputs.get("engine", {})
    if eng:
        parts = []
        if eng.get("composite") is not None:
            parts.append(f"Composite score {eng['composite']:.0f}/100")
        if eng.get("band"):
            parts.append(f"({eng['band']})")
        if eng.get("conviction"):
            parts.append(f"conviction {eng['conviction']}")
        if parts:
            lines.append(
                "Engine read (context only — do not restate as a recommendation): "
                + " ".join(parts) + "."
            )
        gates = eng.get("gates_cleared") or []
        if gates:
            lines.append("Cleared entry checks: " + ", ".join(gates) + ".")

    fund = inputs.get("fundamentals", {})
    if fund:
        parts = []
        if fund.get("revenue_growth") is not None:
            parts.append(f"Revenue growth {fund['revenue_growth']:+.1f}%.")
        if fund.get("profit_margin") is not None:
            parts.append(f"Profit margin {fund['profit_margin']:.1f}%.")
        if fund.get("earnings_trend"):
            parts.append(f"Earnings trend: {fund['earnings_trend']}.")
        if parts:
            lines.append("Fundamentals: " + " ".join(parts))

    cat = inputs.get("catalyst", {})
    if cat:
        parts = []
        if cat.get("next_earnings_date"):
            parts.append(f"Next earnings {cat['next_earnings_date']}.")
        if cat.get("note"):
            parts.append(str(cat["note"]))
        if parts:
            lines.append("Catalyst: " + " ".join(parts))

    headlines = inputs.get("news_headlines", [])
    if headlines:
        lines.append(f"Recent news ({len(headlines)} headlines):")
        for h in headlines[:12]:
            lines.append(f"  - {h}")

    tech = inputs.get("technical", {})
    if tech:
        trend = "above" if tech.get("above_sma50") else "below"
        parts = [f"Price is {trend} the 50-day moving average."]
        if tech.get("rsi") is not None:
            parts.append(f"RSI {tech['rsi']:.0f}.")
        if tech.get("momentum_1m_pct") is not None:
            parts.append(f"1-month change {tech['momentum_1m_pct']:+.1f}%.")
        lines.append("Entry timing (NOT the thesis): " + " ".join(parts))

    if inputs.get("regime"):
        lines.append(f"Market regime: {inputs['regime']}.")

    lines.append("\nWrite the candidate thesis now.")
    return "\n".join(lines)


def build_authoring_inputs(
    company_name: str | None = None,
    sector: str | None = None,
    engine: dict | None = None,
    fundamentals: dict | None = None,
    catalyst: dict | None = None,
    news_headlines: list[str] | None = None,
    technical: dict | None = None,
    regime: str | None = None,
) -> dict:
    """
    Assemble the structured evidence package passed to draft_thesis().

    engine keys (all optional):
        composite (float 0-100), band (str e.g. "Strong Buy"),
        conviction (str), gates_cleared (list[str])
    fundamentals keys (all optional):
        revenue_growth (float, %), profit_margin (float, %), earnings_trend (str)
    catalyst keys (all optional):
        next_earnings_date (str), note (str)
    news_headlines: list of plain-text headline strings (last ~30 days)
    technical keys (all optional, labelled to the LLM as entry timing only):
        above_sma50 (bool), rsi (float), momentum_1m_pct (float)
    regime: short market-regime tag string
    """
    return {
        "company_name":   company_name,
        "sector":         sector,
        "engine":         engine         or {},
        "fundamentals":   fundamentals   or {},
        "catalyst":       catalyst       or {},
        "news_headlines": news_headlines or [],
        "technical":      technical      or {},
        "regime":         regime,
    }


def draft_thesis(
    ticker: str,
    inputs: dict,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 300,
) -> dict | None:
    """
    Draft a CANDIDATE investment thesis for `ticker` from the engine's evidence.

    Returns a dict with keys:
        draft        — the candidate thesis text (prose; ends with "Breaks if ...")
        model        — model used
        generated_at — ISO timestamp (UTC)

    Returns None on any failure — the caller must surface an explicit offline
    state and fall back to a plain manual text field. The returned draft is a
    CANDIDATE only; the user edits and owns the final text (never auto-saved).
    """
    if not api_key:
        return None
    try:
        import anthropic
        client      = anthropic.Anthropic(api_key=api_key)
        user_prompt = _format_authoring_prompt(ticker, inputs)
        response    = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_DRAFT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip() if response.content else ""
        if not text:
            return None
        return {
            "draft":        text,
            "model":        model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None
