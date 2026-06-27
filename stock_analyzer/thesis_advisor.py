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
