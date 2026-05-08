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


def _significance(item: dict, weight: float) -> float:
    """Score a news item's relevance to the portfolio (higher = more urgent)."""
    tier_mult = {1: 1.5, 2: 1.2, 3: 1.0}.get(item.get("tier", 3), 1.0)
    compound  = abs(item.get("compound", 0.0))
    pos_mult  = 1.0 + min(weight / 30.0, 1.0)   # caps at 2× for 30%+ positions
    age_h     = (_time.time() - (item.get("ts") or 0)) / 3600
    recency   = max(0.4, 1.0 - age_h / 24.0)    # 1.0 → 0.4 over 24 h
    return round(compound * tier_mult * pos_mult * recency, 3)


def build_news_intelligence(news_items: list, port_df) -> dict:
    """
    Build actionable news intelligence from curated news and portfolio data.

    Parameters
    ----------
    news_items : list of dicts from curate_news_items()
    port_df    : enriched portfolio DataFrame (Ticker, Weight (%), Score,
                 Signal, P&L (%), Market Value, Sector columns)

    Returns
    -------
    dict with keys:
      summary        — headline counts and portfolio coverage stats
      alerts         — negative stories on held positions, ranked by urgency
      opportunities  — positive signals on held, decent-quality positions
      sector_digest  — sectors with 2+ aligned stories (rotation signals)
      held_news      — every news item for held tickers with position context
    """
    if not news_items:
        return {
            "summary": {"positive": 0, "negative": 0, "neutral": 0,
                        "total": 0, "held_count": 0},
            "alerts": [], "opportunities": [],
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
    opportunities: list[dict] = [
        item for item in enriched
        if item["is_held"] and item["compound"] >= 0.1 and item["score"] >= 55
    ]
    opportunities.sort(key=lambda x: (-x["sig"], -x["score"]))

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
        "sector_digest": sector_digest,
        "held_news":     held_news,
    }
