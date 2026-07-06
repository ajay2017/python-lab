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

from stock_analyzer.constants import ANALYST_EXTRACT_MAX_TOKENS

# Diagnostic: reason for the most recent extract_report() failure (or None on
# success). extract_report always returns None on failure for clean degradation,
# which hides WHY — a timeout, a model/param error, and a JSON parse fail all
# look identical to the caller. The UI reads this to show the actual cause.
LAST_EXTRACT_ERROR: str | None = None


# ── Prompt ────────────────────────────────────────────────────────────────────

def _system_prompt() -> str:
    """Build the system prompt, interpolating COMPOSITE_BUY from constants at
    call time (never a hand-transcribed literal) so the prompt tracks any
    policy change automatically."""
    from stock_analyzer.constants import COMPOSITE_BUY
    return f"""You are a precise financial-data extraction engine. Your only job is to read the article text provided and extract explicitly stated analyst coverage facts. You NEVER infer, estimate, or fabricate any number or claim.

Rules:
- An article may cover ONE stock (possibly rated by SEVERAL firms) OR SEVERAL stocks (e.g. one firm's "top picks" list). Return a SEPARATE record for EACH stock that has its own rating, price target, or substantive write-up.
- CRITICAL: each analyst's rating / price_target / upside_pct applies ONLY to the specific stock that analyst is discussing. NEVER attach an analyst who covers stock A to stock B, and NEVER merge analysts covering different stocks into one record. If a single firm's note covers three stocks, that is THREE records, each with that stock's own analyst + target.
- SKIP stocks that are only listed or mentioned WITHOUT a rating, price target, or write-up (e.g. a ticker appearing only in a list with a YTD% and no target).
- Extract the article's publish date ONCE and apply it to every record in the top-level "article_date" field.
- For each stock record: firm name, analyst name (if stated, else null), rating (exact wording from the article, else null), price_target as a plain number (if explicitly stated, else null), upside_pct as a plain number (if explicitly stated — do NOT compute it yourself, else null).
- Do NOT compute averages, means, or any aggregate — the application computes those from the per-firm facts you provide.
- Never fabricate a number; use null for anything not stated.
- article_date must be in YYYY-MM-DD format if stated; otherwise null.
- report_type for each stock must be exactly one of: initiation, upgrade, downgrade, reiteration, pt_change, other.
- thesis: a list of distinct investment thesis points stated in the article for THIS stock (NOT your own interpretation). Empty list if none.
- catalysts: a list of explicitly named near-term catalysts for THIS stock from the article. Empty list if none.
- risks: a list of explicitly named risks for THIS stock from the article. Empty list if none.

Context (for your awareness only — do not include in output): the downstream application's own entry gate is composite score >= {COMPOSITE_BUY}. Analyst ratings captured here are awareness context only and never a directive.

Output STRICT JSON ONLY — no prose, no markdown fences, no commentary before or after the JSON object. Always use the top-level "reports" array even when only one stock is present:
{{"article_date":"YYYY-MM-DD","reports":[{{"ticker":"SPOT","company":"Spotify","report_type":"other","analysts":[{{"firm":"Bank of America","analyst":"Jessica Reif Ehrlich","rating":"Buy","price_target":685,"upside_pct":41}}],"thesis":["..."],"catalysts":["..."],"risks":["..."]}},{{"ticker":"V","company":"Visa","report_type":"other","analysts":[{{"firm":"Bank of America","analyst":"Matthew O'Neill","rating":"Buy","price_target":410,"upside_pct":13}}],"thesis":["..."],"catalysts":[],"risks":[]}}]}}"""


# ── Public API ────────────────────────────────────────────────────────────────

def extract_report(
    raw_text: str,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = ANALYST_EXTRACT_MAX_TOKENS,
) -> list[dict] | None:
    """
    Call the LLM to extract structured analyst coverage from raw article text.

    Returns a list of record dicts — one per stock covered (possibly empty) —
    or None on any hard failure (missing anthropic package, no API key, API
    error, JSON parse error).  Each record contains atomic per-firm facts;
    callers derive consensus separately via derive_consensus().

    The contract was changed from dict→list to fix a multi-stock merge bug:
    a "top picks" article covering N stocks previously collapsed into one
    corrupt record with all analysts and price targets mixed together.
    """
    global LAST_EXTRACT_ERROR
    LAST_EXTRACT_ERROR = None
    if not api_key or not raw_text or not raw_text.strip():
        LAST_EXTRACT_ERROR = "no API key configured or empty text"
        return None
    try:
        import anthropic
        from stock_analyzer.constants import ANALYST_EXTRACT_TIMEOUT_SEC
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_system_prompt(),
            messages=[{"role": "user", "content": raw_text}],
            timeout=ANALYST_EXTRACT_TIMEOUT_SEC,
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
        parsed = json.loads(cleaned)

        # Normalize into a list of self-contained per-stock records.
        # Handles three shapes the LLM might return:
        #   1. {"article_date":..., "reports":[...]}  ← new canonical format
        #   2. {"ticker":..., ...}                    ← bare single-record dict (old format)
        #   3. [...]                                  ← raw array (defensive)
        top_date: str | None = None
        if isinstance(parsed, dict):
            reports = parsed.get("reports")
            top_date = parsed.get("article_date") or None
            if isinstance(reports, list):
                records: list[dict] = reports
            elif "ticker" in parsed:
                # Bare single-record dict — wrap it
                records = [parsed]
            else:
                records = []
        elif isinstance(parsed, list):
            records = parsed
        else:
            records = []

        # Keep only well-formed dict records — defends the app-side `.get()`
        # calls against a stray non-dict element in a bare-list / reports response.
        records = [r for r in records if isinstance(r, dict)]

        # Stamp article_date into each record if the record itself lacks one
        for rec in records:
            if not rec.get("article_date") and top_date:
                rec["article_date"] = top_date

        return records
    except Exception as e:
        LAST_EXTRACT_ERROR = f"{type(e).__name__}: {e}"[:300]
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
