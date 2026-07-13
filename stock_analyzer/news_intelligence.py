"""
News Intelligence module.

Transforms raw curated news items into actionable portfolio intelligence:
  - Significance scoring (source tier × |sentiment| × position weight × recency)
  - Alert classification: negative news on held positions ranked by urgency
  - Opportunity detection: positive signals on high-scoring held positions
  - Sector pattern digest: 2+ aligned items for the same sector = signal
  - Full portfolio news map: every news item cross-referenced to holdings
"""

import time as _time

from stock_analyzer.constants import (
    NEWS_OPPORTUNITY_COMPOUND_MIN,
    NEWS_OPPORTUNITY_SCORE_MIN,
)


def _significance(item: dict, weight: float) -> float:
    """Score a news item's relevance to the portfolio (higher = more urgent)."""
    tier_mult = {1: 1.5, 2: 1.2, 3: 1.0}.get(item.get("tier", 3), 1.0)
    compound  = abs(item.get("compound", 0.0))
    pos_mult  = 1.0 + min(weight / 30.0, 1.0)   # caps at 2× for 30%+ positions
    age_h     = (_time.time() - (item.get("ts") or 0)) / 3600
    recency   = max(0.4, 1.0 - age_h / 24.0)    # 1.0 → 0.4 over 24 h
    return round(compound * tier_mult * pos_mult * recency, 3)


def build_news_intelligence(news_items: list, port_df, reduce_tickers=None) -> dict:
    """
    Build actionable news intelligence from curated news and portfolio data.

    Parameters
    ----------
    news_items : list of dicts from curate_news_items()
    port_df    : enriched portfolio DataFrame (Ticker, Weight (%), Score,
                 Signal, P&L (%), Market Value, Sector columns)
    reduce_tickers : iterable of tickers currently under an active Reduce/Exit
                 call on the Daily Brief (any trim / exit / sell / stop / risk-off
                 directive). Positive news on these names is split OUT of
                 `opportunities` into `opportunities_suppressed` — a name you're
                 being told to reduce is not an "add on a pullback" candidate,
                 and surfacing it as one contradicts the Brief. Optional; when
                 None/empty, behaviour is unchanged.

    Returns
    -------
    dict with keys:
      summary        — headline counts and portfolio coverage stats
      alerts         — negative stories on held positions, ranked by urgency
      opportunities  — positive signals on held, decent-quality positions
      opportunities_suppressed — would-be opportunities dropped because the name
                       is under a Reduce/Exit call (for a UI reconciliation note)
      sector_digest  — sectors with 2+ aligned stories (rotation signals)
      held_news      — every news item for held tickers with position context
    """
    if not news_items:
        return {
            "summary": {"positive": 0, "negative": 0, "neutral": 0,
                        "total": 0, "held_count": 0},
            "alerts": [], "opportunities": [], "opportunities_suppressed": [],
            "sector_digest": [], "held_news": [],
        }

    # Build position lookup from portfolio
    port_lookup: dict = {}
    if port_df is not None and not port_df.empty:
        for _, row in port_df.iterrows():
            t = str(row.get("Ticker", "")).strip().upper()
            if t:
                port_lookup[t] = {
                    "weight":  float(row.get("Weight (%)",   0) or 0),
                    "score":   float(row.get("Score",       50) or 50),
                    "signal":  str(row.get("Signal",        "")),
                    "pnl_pct": float(row.get("P&L (%)",     0) or 0),
                    "sector":  str(row.get("Sector",        "")),
                    "mval":    float(row.get("Market Value", 0) or 0),
                }

    # Enrich each item with position context + significance score
    enriched: list[dict] = []
    for item in news_items:
        ticker  = str(item.get("ticker", "")).strip().upper()
        pos     = port_lookup.get(ticker, {})
        weight  = pos.get("weight", 0.0)
        is_held = ticker in port_lookup
        enriched.append({
            **item,
            "is_held":  is_held,
            "weight":   weight,
            "score":    pos.get("score",    50.0),
            "signal":   pos.get("signal",   ""),
            "pnl_pct":  pos.get("pnl_pct",  0.0),
            "sector":   pos.get("sector",   ticker),
            "mval":     pos.get("mval",     0.0),
            "sig":      _significance(item, weight),
        })

    # ── Summary counts ────────────────────────────────────────────────────
    pos_count  = sum(1 for i in enriched if i["compound"] >=  0.05)
    neg_count  = sum(1 for i in enriched if i["compound"] <= -0.05)
    summary = {
        "positive":   pos_count,
        "negative":   neg_count,
        "neutral":    len(enriched) - pos_count - neg_count,
        "total":      len(enriched),
        "held_count": sum(1 for i in enriched if i["is_held"]),
    }

    # ── Alerts: negative news on held positions ───────────────────────────
    alerts: list[dict] = []
    for item in enriched:
        if not item["is_held"] or item["compound"] > -0.05:
            continue
        level = (
            "critical"
            if item["compound"] <= -0.25 and item["weight"] >= 8.0 and item["tier"] <= 2
            else "warning"
        )
        alerts.append({**item, "alert_level": level})
    alerts.sort(key=lambda x: (0 if x["alert_level"] == "critical" else 1,
                                 -x["weight"], x["compound"]))

    # ── Opportunities: positive news on held, quality positions ──────────
    # A position under an active Reduce/Exit call (deterioration ladder, Sell
    # signal, or breached stop — passed in via reduce_tickers) is NOT an "add on
    # a pullback" candidate: that protect-capital directive leads the composite
    # score, so framing its positive news as an opportunity contradicts the
    # Brief. Split those out so the UI can show a reconciliation note instead of
    # a conflicting green Buy card.
    _reduce = {str(t).strip().upper() for t in (reduce_tickers or [])}
    _opp_all: list[dict] = [
        item for item in enriched
        if item["is_held"]
        and item["compound"] >= NEWS_OPPORTUNITY_COMPOUND_MIN
        and item["score"] >= NEWS_OPPORTUNITY_SCORE_MIN
    ]
    opportunities: list[dict] = [
        i for i in _opp_all if str(i.get("ticker", "")).strip().upper() not in _reduce
    ]
    opportunities_suppressed: list[dict] = [
        i for i in _opp_all if str(i.get("ticker", "")).strip().upper() in _reduce
    ]
    opportunities.sort(key=lambda x: (-x["sig"], -x["score"]))
    opportunities_suppressed.sort(key=lambda x: (-x["sig"], -x["score"]))

    # ── Sector digest: 2+ aligned stories = rotation signal ──────────────
    sec_buckets: dict = {}
    for item in enriched:
        if not item["is_held"]:
            continue
        sec = item["sector"]
        if sec not in sec_buckets:
            sec_buckets[sec] = {"positive": [], "negative": []}
        if item["compound"] >= 0.05:
            sec_buckets[sec]["positive"].append(item)
        elif item["compound"] <= -0.05:
            sec_buckets[sec]["negative"].append(item)

    sector_digest: list[dict] = []
    for sec, buckets in sec_buckets.items():
        if len(buckets["negative"]) >= 2:
            sector_digest.append({
                "sector": sec, "direction": "negative",
                "count": len(buckets["negative"]), "items": buckets["negative"],
            })
        elif len(buckets["positive"]) >= 2:
            sector_digest.append({
                "sector": sec, "direction": "positive",
                "count": len(buckets["positive"]), "items": buckets["positive"],
            })
    sector_digest.sort(key=lambda x: -x["count"])

    # ── All news for held tickers ─────────────────────────────────────────
    held_news = sorted(
        [i for i in enriched if i["is_held"]],
        key=lambda x: (x["compound"], -abs(x["compound"])),
    )

    return {
        "summary":       summary,
        "alerts":        alerts,
        "opportunities": opportunities,
        "opportunities_suppressed": opportunities_suppressed,
        "sector_digest": sector_digest,
        "held_news":     held_news,
    }


def rescore_news_items_llm(items: list[dict], api_key: str, timeout: float = 8.0) -> list[dict]:
    """
    Re-score VADER compound scores using Claude Haiku financial-domain scoring.

    SUPPRESS-ONLY: the LLM score is accepted only when it is HIGHER than the VADER score
    (moves toward neutral/positive). The LLM can never newly push a score into negative
    territory — it acts as a false-positive noise filter, not a new signal generator.

    Falls back to original VADER scores on any failure (API down, timeout, parse error,
    out-of-range score). Never blocks the caller — exceptions are swallowed.
    """
    if not items or not api_key:
        return items

    try:
        import anthropic
        import json

        headline_lines = []
        for i, item in enumerate(items):
            title = item.get("title") or item.get("headline", "")
            ticker = item.get("ticker", "")
            headline_lines.append(f'{i}. {ticker}: "{title}"')

        prompt = (
            "You are a financial sentiment analyst. Score each headline for financial sentiment "
            "from -1.0 (very bearish) to +1.0 (very bullish) from an investor's perspective. "
            "Apply financial domain knowledge: FDA approval = bullish, earnings beat = bullish, "
            "analyst upgrade = bullish, 'don't sell' = bullish, regulatory delay = bearish, "
            "earnings miss = bearish, analyst downgrade = bearish, layoffs = bearish. "
            "Return ONLY a JSON array with no other text:\n"
            '[{"idx": 0, "score": 0.3}, {"idx": 1, "score": -0.6}, ...]\n\n'
            "Headlines:\n" + "\n".join(headline_lines)
        )

        client = anthropic.Anthropic(
            api_key=api_key,
            max_retries=0,
            timeout=anthropic.Timeout(timeout),
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        # Strip markdown code fences if the model wraps the JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        scores = json.loads(raw.strip())

        result = [dict(item) for item in items]  # shallow copy each dict
        for entry in scores:
            idx = entry.get("idx")
            llm_score = entry.get("score")
            if not isinstance(idx, int) or idx < 0 or idx >= len(items):
                continue
            if not isinstance(llm_score, (int, float)):
                continue
            llm_score = float(llm_score)
            if not (-1.0 <= llm_score <= 1.0):
                continue
            vader_score = items[idx].get("compound", 0.0)
            if llm_score <= vader_score:
                continue  # suppress-only: LLM cannot lower a VADER score
            new_score = round(llm_score, 3)
            label = ("Positive" if new_score >= 0.05 else
                     "Negative" if new_score <= -0.05 else "Neutral")
            result[idx]["compound"] = new_score
            result[idx]["label"] = label

        return result

    except Exception:
        return items
