"""
Analyst Coverage Intel — Ideas Inbox (Phase 1).

Extracts structured analyst coverage data from raw article text using an LLM,
then derives consensus metrics from the per-firm atomic facts in pure Python.

Design contract (same as the rest of the AI layer):
- Strictly additive / zero runtime dependency: extract_report() returns None on
  ANY failure (missing package, API error, JSON parse error, no key). The Ideas
  Inbox feature degrades to an offline notice without affecting any other page.
- The engine still decides: analyst PTs and ratings are awareness context only —
  they never modify a composite score, never gate, never override a verdict.
- Zero-hallucination defence: the LLM extracts only atomic per-firm facts;
  derive_consensus() computes all aggregates (avg/high/low PT, consensus label)
  in pure Python so no arithmetic hallucination is possible.
"""

import json


# ── Prompt ────────────────────────────────────────────────────────────────────

def _system_prompt() -> str:
    """Build the system prompt, interpolating COMPOSITE_BUY from constants at
    call time (never a hand-transcribed literal) so the prompt tracks any
    policy change automatically."""
    from stock_analyzer.constants import COMPOSITE_BUY
    return f"""You are a precise financial-data extraction engine. Your only job is to read the article text provided and extract explicitly stated analyst coverage facts. You NEVER infer, estimate, or fabricate any number or claim.

Rules:
- Extract ONLY facts that are explicitly stated in the article text.
- Identify the PRIMARY stock the article analyzes. Articles sometimes display sidebar widgets, related-quote boxes, or consensus panels for OTHER tickers (e.g., an article about INIO may display an AMZN widget) — ignore those. The primary subject is the company the article's headline and body discuss.
- For each analyst firm mentioned, extract: firm name, analyst name (if stated, else null), rating (exact wording from the article, else null), price_target as a plain number (if explicitly stated, else null), and upside_pct as a plain number (if explicitly stated — do NOT compute it yourself, else null).
- Do NOT compute averages, means, or any aggregate — the application computes those from the per-firm facts you provide.
- Use null for any field not explicitly stated. Never fabricate a number.
- article_date must be in YYYY-MM-DD format if stated; otherwise null.
- report_type must be exactly one of: initiation, upgrade, downgrade, reiteration, pt_change, other.
- thesis: a list of distinct investment thesis points stated in the article (NOT your own interpretation). Empty list if none.
- catalysts: a list of explicitly named near-term catalysts from the article. Empty list if none.
- risks: a list of explicitly named risks from the article. Empty list if none.

Context (for your awareness only — do not include in output): the downstream application's own entry gate is composite score >= {COMPOSITE_BUY}. Analyst ratings captured here are awareness context only and never a directive.

Output STRICT JSON ONLY — no prose, no markdown fences, no commentary before or after the JSON object:
{{"ticker":"INIO","company":"Innio N.V.","article_date":"2026-07-03","report_type":"initiation","analysts":[{{"firm":"Baird","analyst":"Ben Kallo","rating":"Buy","price_target":50,"upside_pct":35}}],"thesis":["Data centers shifting from grid to onsite gas engines"],"catalysts":["AI data-center capex ramp"],"risks":["Demand slowdown vs. gas turbines"]}}"""


# ── Public API ────────────────────────────────────────────────────────────────

def extract_report(
    raw_text: str,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 1500,
) -> dict | None:
    """
    Call the LLM to extract structured analyst coverage from raw article text.

    Returns a dict of atomic per-firm facts (no aggregates) or None on any
    failure (missing anthropic package, no API key, API error, JSON parse error).
    Callers derive consensus separately via derive_consensus().
    """
    if not api_key or not raw_text or not raw_text.strip():
        return None
    try:
        import anthropic
        from stock_analyzer.constants import LLM_REQUEST_TIMEOUT_SEC
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_system_prompt(),
            messages=[{"role": "user", "content": raw_text}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        text = response.content[0].text if response.content else ""
        # Robust JSON parse: strip markdown fences first, then slice to first { ... last }
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Strip opening fence (```json or ```)
            cleaned = cleaned.split("\n", 1)[-1]
            # Strip closing fence
            if cleaned.endswith("```"):
                cleaned = cleaned[: cleaned.rfind("```")]
            cleaned = cleaned.strip()
        if not cleaned.startswith("{"):
            start = cleaned.find("{")
            end   = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                cleaned = cleaned[start : end + 1]
        return json.loads(cleaned)
    except Exception:
        return None


# ── Pure aggregation (no LLM) ─────────────────────────────────────────────────

def derive_consensus(analysts: list[dict]) -> dict:
    """
    Compute consensus metrics from a list of per-firm analyst dicts.

    Pure Python — no LLM call, no external dependency.

    Rating normalization (case-insensitive, substring-safe):
      bullish  = buy / overweight / outperform / strong buy / accumulate / add / positive
      neutral  = hold / neutral / equal-weight / equalweight / market perform /
                 sector perform / in-line / peer perform
      bearish  = sell / underweight / underperform / reduce / negative
    Firms with an unrecognized rating string are excluded from the tally.

    Returns:
        consensus_rating  str | None   e.g. "Strong Buy (5 Buy / 0 Hold / 0 Sell)"
        avg_pt            float | None  mean of numeric price_target values, 2 dp
        high_pt           float | None
        low_pt            float | None
    """
    _BULLISH = frozenset({
        "buy", "overweight", "outperform", "strong buy", "accumulate", "add", "positive",
    })
    _NEUTRAL = frozenset({
        "hold", "neutral", "equal-weight", "equalweight",
        "market perform", "sector perform", "in-line", "peer perform",
    })
    _BEARISH = frozenset({
        "sell", "underweight", "underperform", "reduce", "negative",
    })

    empty: dict = {"consensus_rating": None, "avg_pt": None, "high_pt": None, "low_pt": None}
    if not analysts:
        return empty

    bull_n = neut_n = bear_n = 0
    pts: list[float] = []

    for a in analysts:
        raw_rating = (a.get("rating") or "").strip().lower()
        if any(b in raw_rating for b in _BULLISH):
            bull_n += 1
        elif any(b in raw_rating for b in _BEARISH):
            bear_n += 1
        elif any(n in raw_rating for n in _NEUTRAL):
            neut_n += 1
        # Unrecognized rating → excluded from tally (not counted in n_rated)

        try:
            pt = a.get("price_target")
            if pt is not None:
                pt_val = float(pt)
                if pt_val == pt_val:   # guard against NaN (NaN != NaN)
                    pts.append(pt_val)
        except (TypeError, ValueError):
            pass

    n_rated = bull_n + neut_n + bear_n
    consensus_rating: str | None = None
    if n_rated > 0:
        from stock_analyzer.constants import (
            ANALYST_CONSENSUS_STRONG_BUY_FRAC,
            ANALYST_CONSENSUS_BUY_FRAC,
            ANALYST_CONSENSUS_SELL_FRAC,
        )
        bull_frac = bull_n / n_rated
        bear_frac = bear_n / n_rated
        if bull_frac >= ANALYST_CONSENSUS_STRONG_BUY_FRAC:
            label = "Strong Buy"
        elif bull_frac >= ANALYST_CONSENSUS_BUY_FRAC:
            label = "Buy"
        elif bear_frac >= ANALYST_CONSENSUS_SELL_FRAC:
            label = "Sell"
        else:
            label = "Hold" if neut_n >= max(bull_n, bear_n) else "Mixed"
        consensus_rating = f"{label} ({bull_n} Buy / {neut_n} Hold / {bear_n} Sell)"

    avg_pt  = round(sum(pts) / len(pts), 2) if pts else None
    high_pt = max(pts) if pts else None
    low_pt  = min(pts) if pts else None

    return {
        "consensus_rating": consensus_rating,
        "avg_pt":           avg_pt,
        "high_pt":          high_pt,
        "low_pt":           low_pt,
    }
