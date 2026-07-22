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

# Shared rating taxonomy — used by both derive_consensus() (aggregate label) and
# any per-firm classification (e.g. the Research Scorecard firm leaderboard) so
# the two never disagree on what counts as a bullish/bearish individual rating.
BULLISH_RATINGS = frozenset({
    "buy", "overweight", "outperform", "strong buy", "accumulate", "add", "positive",
})
BEARISH_RATINGS = frozenset({
    "sell", "underweight", "underperform", "reduce", "negative",
})


def is_bullish_rating(rating: str) -> bool:
    """True if a single per-firm rating string reads as bullish (BULLISH_RATINGS
    substring match). Used outside derive_consensus() to classify one analyst's
    call without re-deriving a full consensus."""
    r = (rating or "").strip().lower()
    return any(b in r for b in BULLISH_RATINGS)


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
    _NEUTRAL = frozenset({
        "hold", "neutral", "equal-weight", "equalweight",
        "market perform", "sector perform", "in-line", "peer perform",
    })

    empty: dict = {"consensus_rating": None, "avg_pt": None, "high_pt": None, "low_pt": None}
    if not analysts:
        return empty

    bull_n = neut_n = bear_n = 0
    pts: list[float] = []

    for a in analysts:
        raw_rating = (a.get("rating") or "").strip().lower()
        if any(b in raw_rating for b in BULLISH_RATINGS):
            bull_n += 1
        elif any(b in raw_rating for b in BEARISH_RATINGS):
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


# ── Research Scorecard — accuracy classification (Phase 2, display-only) ──────

def classify_call(row: dict, sell_date_after, today_et, fetch_window) -> dict:
    """
    Classify one analyst_coverage row's directional/PT accuracy.

    Pure Python — no LLM, no network call, no DB write. Accuracy is retrospective
    awareness only: it NEVER feeds valuation_score() or any gate.

    row              — one analyst_coverage record (dict-like) with at least
                        ticker, article_date (date), price_at_article_date,
                        avg_pt, consensus_rating.
    sell_date_after  — the earliest SELL trade date after article_date for this
                        ticker, or None if never sold (natural-exit precedence
                        over the fixed window).
    today_et         — today's date in America/New_York (caller resolves).
    fetch_window(ticker, start_date, end_date) -> {"close": float|None,
        "high": float|None} | None — ONE OHLC fetch for [start, end]; caller
        supplies a cached implementation so close-at-end and window-max-High
        are derived from the same frame (no double fetch).

    Returns a dict with "status" always present:
      "no_anchor"    — no price_at_article_date (not yet backfilled)
      "no_consensus" — no consensus_rating (legacy row with no per-firm rating
                       data) — there is no directional CALL to grade, so this
                       is never scored as a hit/miss (a missing rating is NOT
                       the same as an implicit Sell)
      "pending"      — inside the measurement window, not yet evaluable
      "no_price"     — fetch_window returned no usable close
      "hit" / "miss" — directional call verdict, plus ret_pct, exit_price,
                       window, directional_hit, pt_hit, pt_proximity, window_end.
    """
    from datetime import timedelta as _td
    from stock_analyzer.constants import (
        ANALYST_ACCURACY_DIRECTION_DAYS,
        ANALYST_ACCURACY_PT_HIT_PCT,
    )

    price_at_article = row.get("price_at_article_date")
    article_date      = row.get("article_date")
    if article_date is None:
        return {"status": "no_anchor"}
    try:
        price_at_article = float(price_at_article)
    except (TypeError, ValueError):
        return {"status": "no_anchor"}
    if not (price_at_article > 0):   # catches NaN (a DB NULL comes back as np.nan, not None) and <= 0
        return {"status": "no_anchor"}
    if not (row.get("consensus_rating") or "").strip():
        return {"status": "no_consensus"}

    if sell_date_after is not None:
        window_end = sell_date_after
        window     = "sold"
    elif (today_et - article_date).days < ANALYST_ACCURACY_DIRECTION_DAYS:
        return {"status": "pending"}
    else:
        window_end = min(article_date + _td(days=ANALYST_ACCURACY_DIRECTION_DAYS), today_et)
        window     = f"{ANALYST_ACCURACY_DIRECTION_DAYS}d"

    fetched = fetch_window(row.get("ticker"), article_date, window_end)
    if not fetched or fetched.get("close") is None:
        return {"status": "no_price"}

    exit_price  = fetched["close"]
    window_high = fetched.get("high")

    ret_pct = (exit_price - price_at_article) / price_at_article * 100
    # consensus_rating is stored as "Label (N Buy / N Hold / N Sell)" (see
    # derive_consensus() above) — match the LEADING label only. A bare
    # substring check for "buy" would false-positive on every row, since the
    # parenthetical tally always contains the literal word "Buy" regardless
    # of which label ("Sell", "Hold", "Mixed"...) actually won.
    consensus  = (row.get("consensus_rating") or "").strip().lower()
    is_bullish = consensus.startswith(("strong buy", "buy"))
    directional_hit = (is_bullish and ret_pct > 0) or (not is_bullish and ret_pct < 0)

    avg_pt = row.get("avg_pt")
    # PT hit uses the window's INTRA-PERIOD HIGH, not the endpoint close above —
    # a genuine 75%-of-target touch counts even if price pulled back by window_end.
    pt_hit = bool(avg_pt and window_high and window_high >= float(avg_pt) * ANALYST_ACCURACY_PT_HIT_PCT)
    pt_proximity = (window_high / float(avg_pt) * 100) if (avg_pt and window_high) else None

    return {
        "status":          "hit" if directional_hit else "miss",
        "ret_pct":         ret_pct,
        "exit_price":      exit_price,
        "window":          window,
        "window_end":      window_end,
        "directional_hit": directional_hit,
        "pt_hit":          pt_hit,
        "pt_proximity":    pt_proximity,   # based on window HIGH, not endpoint — may exceed 100%
    }


def fetch_anchor_price(ticker: str, article_date) -> float | None:
    """Next-trading-day close for one analyst_coverage row's article_date —
    the same anchor-price logic used by scripts/backfill_analyst_prices.py,
    shared here so a live "fetch now" button (Research Scorecard) and the
    batch backfill never drift apart.

    Unlike derive_consensus()/classify_call() this makes a live network call
    (yfinance) — not "pure", but the same offline-degrades-to-None contract
    as the rest of this module: any failure (missing package, no data,
    network error, NaN close) returns None, never raises.
    """
    if not ticker or article_date is None:
        return None
    try:
        import yfinance as yf
        from datetime import timedelta as _td
        hist = yf.download(
            ticker,
            start=str(article_date),
            end=str(article_date + _td(days=7)),
            auto_adjust=True,
            progress=False,
            multi_level_index=False,
        )
        if hist is None or hist.empty or "Close" not in hist.columns:
            return None
        price = float(hist["Close"].iloc[0])
        if price != price or price <= 0:   # NaN guard (NaN != NaN) + sanity floor
            return None
        return price
    except Exception:
        return None
