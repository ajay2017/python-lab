"""
Earnings Intelligence — Phases 1 & 2.

Extracts structured earnings data from raw CNBC Pro / broker article text
using an LLM, following the same idiom as analyst_intel.py.

Design contract (identical to the rest of the AI layer):
- Strictly additive / zero runtime dependency: every public function returns
  None on ANY failure (missing package, API error, JSON parse error, no key).
  Other pages are never affected.
- Zero-hallucination defence: the LLM extracts only atomic stated facts; all
  aggregates and posture decisions are computed in Python.
- The engine still decides: beat_rate and reaction_direction are enrichment
  signals; they cannot originate a new verdict on their own.

Public API:
  extract_playbook()   — Phase 1: pre-earnings CNBC preview article
  extract_results()    — Phase 2: post-earnings results article
"""

import json

from stock_analyzer.constants import ANALYST_EXTRACT_MAX_TOKENS, ANALYST_EXTRACT_TIMEOUT_SEC

# Diagnostic: last-failure reason for each extractor (None on success).
LAST_PLAYBOOK_ERROR: str | None = None
LAST_RESULTS_ERROR:  str | None = None


# ── Phase 1 — Pre-Earnings Playbook ──────────────────────────────────────────

def _playbook_system_prompt() -> str:
    """Build the pre-earnings extraction prompt, interpolating COMPOSITE_BUY
    from constants at call time so it tracks any policy change automatically."""
    from stock_analyzer.constants import COMPOSITE_BUY
    return f"""You are a precise financial-data extraction engine. Your only job is to read the article text provided and extract explicitly stated earnings-preview facts. You NEVER infer, estimate, or fabricate any number or claim.

Rules:
- An article may preview ONE stock OR SEVERAL stocks. Return a SEPARATE record for EACH stock with its own earnings date, beat rate, or substantive preview write-up.
- SKIP stocks only mentioned in passing without any earnings-specific data.
- For EACH stock record extract ONLY what is explicitly stated. Use null for anything not stated.
- "article_date" is the article's publish date; apply it to the top-level field.
- "earnings_date": the specific date the earnings report is expected (YYYY-MM-DD). Resolve day-of-week references ("Tuesday", "Wednesday") to absolute dates using the article_date context. null if not stated.
- "earnings_time": exactly one of pre_market / post_market / intraday / unknown. null if not stated.
- "beat_rate_pct": a float extracted from Bespoke-style phrasing ONLY ("tops estimates X% of the time", "beats estimates in X% of cases"). NEVER extract from qualitative language like "strong history" or "tends to beat". null if this exact phrasing is absent.
- "recent_reaction_summary": verbatim or near-verbatim quote of the article's stated post-earnings reaction history (e.g. "fell after last four earnings releases"). null if not stated.
- "recent_reaction_direction": exactly one of bullish / bearish / mixed / unknown, derived ONLY from an explicit stated reaction pattern. null if not stated.
- "consensus_growth_pct": a float from phrasing like "earnings expected to grow ~10% YoY". null if not stated.
- "what_to_watch_cnbc": the article's curated narrative for what to watch (1-2 sentences max). null if absent.
- Do NOT extract analyst price targets here — those are handled by the analyst coverage extractor.
- Do NOT compute any aggregate or average.

Context (do not include in output): the app's entry gate is composite score >= {COMPOSITE_BUY}.

Output STRICT JSON ONLY — no prose, no markdown fences, no commentary before or after:
{{"article_date":"YYYY-MM-DD","records":[{{"ticker":"JPM","company":"JPMorgan Chase","earnings_date":"2026-07-15","earnings_time":"pre_market","beat_rate_pct":81.0,"recent_reaction_summary":"fell after last four earnings releases","recent_reaction_direction":"bearish","consensus_growth_pct":10.0,"what_to_watch_cnbc":"NII trajectory; management commentary on regulatory capital requirements"}},{{"ticker":"GS","company":"Goldman Sachs","earnings_date":null,"earnings_time":null,"beat_rate_pct":75.0,"recent_reaction_summary":null,"recent_reaction_direction":null,"consensus_growth_pct":null,"what_to_watch_cnbc":"Trading revenue and IB pipeline"}}]}}"""


def extract_playbook(
    raw_text: str,
    article_date,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = ANALYST_EXTRACT_MAX_TOKENS,
) -> list[dict] | None:
    """
    Extract pre-earnings facts from a CNBC Pro preview article.

    Returns a list of record dicts (one per stock) or None on any failure.
    article_date (date) is passed as context for day-of-week date resolution.
    """
    global LAST_PLAYBOOK_ERROR
    LAST_PLAYBOOK_ERROR = None
    if not api_key or not raw_text or not raw_text.strip():
        LAST_PLAYBOOK_ERROR = "no API key configured or empty text"
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        user_msg = f"Article date: {article_date}\n\n{raw_text.strip()}"
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_playbook_system_prompt(),
            messages=[{"role": "user", "content": user_msg}],
            timeout=ANALYST_EXTRACT_TIMEOUT_SEC,
        )
        text = response.content[0].text if response.content else ""
        records = _parse_json_response(text, "records")
        if records is None:
            LAST_PLAYBOOK_ERROR = "JSON parse failed"
            return None
        # Stamp article_date into each record that lacks it
        _ad_str = str(article_date)
        for rec in records:
            if not rec.get("article_date"):
                rec["article_date"] = _ad_str
        return records
    except Exception as e:
        LAST_PLAYBOOK_ERROR = f"{type(e).__name__}: {e}"[:300]
        return None


# ── Phase 2 — Post-Earnings Results ──────────────────────────────────────────

def _results_system_prompt() -> str:
    return """You are a precise financial-data extraction engine. Your only job is to read the article text and extract explicitly stated post-earnings results. You NEVER infer, estimate, or fabricate any number or claim.

Rules:
- An article may cover ONE stock OR SEVERAL stocks. Return a SEPARATE record for EACH stock.
- SKIP stocks only mentioned in passing without actual earnings data.
- For EACH record extract ONLY what is explicitly stated. Use null for anything not stated.
- "report_date": the date the results were reported (YYYY-MM-DD). Resolve day-of-week to absolute using article_date context. null if not stated.
- "actual_eps" / "estimated_eps": plain numeric (e.g. 4.96). null if not stated.
- "eps_beat": boolean — true if actual_eps > estimated_eps and both are stated; false if actual < estimated; null if either is missing.
- "eps_surprise_pct": float as stated in the article ("beat by 7.6%"). Do NOT compute it yourself. null if not stated.
- "actual_revenue" / "estimated_revenue": billions, plain numeric. null if not stated.
- "rev_beat": boolean — same logic as eps_beat. null if either is missing.
- "guidance_direction": exactly one of raised / lowered / maintained / withdrawn / unknown. null if guidance not mentioned.
- "key_narrative": 1-2 sentence verbatim or near-verbatim management commentary summary from the article. null if not available.
- Do NOT compute any aggregate.

Output STRICT JSON ONLY — no prose, no markdown fences:
{"article_date":"YYYY-MM-DD","records":[{"ticker":"JPM","company":"JPMorgan Chase","report_date":"2026-07-15","actual_eps":4.96,"estimated_eps":4.61,"eps_beat":true,"eps_surprise_pct":7.6,"actual_revenue":45.3,"estimated_revenue":44.1,"rev_beat":true,"guidance_direction":"maintained","key_narrative":"Record trading revenue offset by higher credit reserves; management maintained 2026 NII guidance despite rate uncertainty."}]}"""


def extract_results(
    raw_text: str,
    article_date,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = ANALYST_EXTRACT_MAX_TOKENS,
) -> list[dict] | None:
    """
    Extract post-earnings results from a results article.

    Returns a list of record dicts (one per stock) or None on any failure.
    """
    global LAST_RESULTS_ERROR
    LAST_RESULTS_ERROR = None
    if not api_key or not raw_text or not raw_text.strip():
        LAST_RESULTS_ERROR = "no API key configured or empty text"
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        user_msg = f"Article date: {article_date}\n\n{raw_text.strip()}"
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_results_system_prompt(),
            messages=[{"role": "user", "content": user_msg}],
            timeout=ANALYST_EXTRACT_TIMEOUT_SEC,
        )
        text = response.content[0].text if response.content else ""
        records = _parse_json_response(text, "records")
        if records is None:
            LAST_RESULTS_ERROR = "JSON parse failed"
            return None
        _ad_str = str(article_date)
        for rec in records:
            if not rec.get("article_date"):
                rec["article_date"] = _ad_str
        return records
    except Exception as e:
        LAST_RESULTS_ERROR = f"{type(e).__name__}: {e}"[:300]
        return None


# ── Auto-fetch from Finnhub (no LLM) ─────────────────────────────────────────

def fetch_recent_results(
    tickers: list[str],
    finnhub_key: str,
    lookback_days: int = 90,
) -> list[dict]:
    """
    Fetch the most recent reported quarter EPS data from Finnhub for each ticker.

    Returns a list of partial result dicts (one per ticker with data within
    lookback_days of today). Fields: ticker, actual_eps, estimated_eps, eps_beat,
    eps_surprise_pct, quarter_period (Finnhub's quarter-end date string).

    Revenue and guidance_direction are NOT fetched — Finnhub free tier omits them.
    report_date is intentionally absent; the caller sets it to the actual report
    date (shown as an editable field in the UI, defaulting to today).

    Returns [] on total failure or empty data; partial successes included.
    """
    if not finnhub_key or not tickers:
        return []
    try:
        import requests
        from datetime import datetime, timedelta
        import pytz
        _et   = pytz.timezone("America/New_York")
        today = datetime.now(_et).date()
        cutoff = today - timedelta(days=lookback_days)
        results: list[dict] = []
        for ticker in tickers:
            try:
                resp = requests.get(
                    "https://finnhub.io/api/v1/stock/earnings",
                    params={"symbol": ticker.upper(), "limit": 1, "token": finnhub_key},
                    timeout=8,
                )
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list) or not data:
                    continue
                row = data[0]
                # `period` is the quarter-end date (e.g. "2026-06-30") — not the
                # actual report date, but a reliable proxy for "quarter just ended"
                period_str = row.get("period") or ""
                try:
                    period_date = datetime.strptime(period_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if period_date < cutoff:
                    continue  # older than lookback window
                actual   = row.get("actual")
                estimate = row.get("estimate")
                surprise = row.get("surprise")
                surprise_pct = row.get("surprisePercent")
                if actual is None:
                    continue
                eps_beat = None
                if actual is not None and estimate is not None:
                    try:
                        eps_beat = bool(float(actual) > float(estimate))
                    except (TypeError, ValueError):
                        pass
                results.append({
                    "ticker":            ticker.upper(),
                    "actual_eps":        float(actual) if actual is not None else None,
                    "estimated_eps":     float(estimate) if estimate is not None else None,
                    "eps_beat":          eps_beat,
                    "eps_surprise_pct":  round(float(surprise_pct), 2) if surprise_pct is not None else None,
                    "quarter_period":    period_str,  # quarter-end date for display
                })
            except Exception:
                continue
        return results
    except Exception:
        return []


# ── Shared JSON helper ────────────────────────────────────────────────────────

def _parse_json_response(text: str, list_key: str) -> list[dict] | None:
    """
    Robustly parse an LLM JSON response that should contain a top-level
    `list_key` array. Handles markdown fences and bare offsets.

    Returns a filtered list[dict] or None on parse failure.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")]
        cleaned = cleaned.strip()
    if not cleaned.startswith(("{", "[")):
        start = cleaned.find("{")
        end   = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return None

    if isinstance(parsed, dict):
        records = parsed.get(list_key)
        if isinstance(records, list):
            return [r for r in records if isinstance(r, dict)]
        # Bare single-record dict
        if "ticker" in parsed:
            return [parsed]
        return []
    if isinstance(parsed, list):
        return [r for r in parsed if isinstance(r, dict)]
    return []
