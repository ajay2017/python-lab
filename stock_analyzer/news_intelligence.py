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
    SENTIMENT_LLM_MAX_SWING,
    NEWS_SENTIMENT_CRITICAL,
    NEWS_CRITICAL_MAX_TIER,
    NEWS_CRITICAL_MIN_WEIGHT_PCT,
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
            if item["compound"] <= NEWS_SENTIMENT_CRITICAL
            and item["weight"] >= NEWS_CRITICAL_MIN_WEIGHT_PCT
            and item["tier"] <= NEWS_CRITICAL_MAX_TIER
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
            updated = _apply_suppress_only(items[idx], llm_score)
            if updated is not None:
                result[idx] = updated

        return result

    except Exception:
        return items


# ── Brief-scoped suppress-only rescore (docs/plans/held-news-coverage.md) ──
# Module plumbing, not policy.
_RESCORE_BATCH = 25
_RESCORE_WORKERS = 3
_RESCORE_MAX_ITEMS = 75
_RESCORE_RETRY_S = 600


def _apply_suppress_only(item: dict, llm_score) -> dict | None:
    """Updated COPY of `item` when `llm_score` is valid and HIGHER than its VADER
    compound (suppress-only: the LLM never lowers a score); else None."""
    if isinstance(llm_score, bool) or not isinstance(llm_score, (int, float)):
        return None
    llm_score = float(llm_score)
    if not (-1.0 <= llm_score <= 1.0):
        return None
    vader_score = item.get("compound", 0.0)
    if llm_score <= vader_score:
        return None
    new_score = round(llm_score, 3)
    label = ("Positive" if new_score >= 0.05 else
             "Negative" if new_score <= -0.05 else "Neutral")
    out = dict(item)
    out["compound"] = new_score
    out["label"] = label
    return out


def llm_score_batch(items: list[dict], api_key: str, timeout: float = 8.0) -> list | None:
    """One Anthropic call scoring <= 25 items. Returns scores aligned to `items`
    (None where missing/invalid), or None on any exception / parse failure."""
    if not items or not api_key:
        return None
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
            max_tokens=64 + 32 * len(items),
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())

        out: list = [None] * len(items)
        for entry in parsed:
            idx = entry.get("idx")
            sc = entry.get("score")
            if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(items)):
                continue
            if isinstance(sc, bool) or not isinstance(sc, (int, float)):
                continue
            if not (-1.0 <= float(sc) <= 1.0):
                continue
            out[idx] = float(sc)
        return out
    except Exception:
        return None


def _is_neg(item: dict) -> bool:
    from stock_analyzer.constants import NEWS_SENTIMENT_WARN
    c = item.get("compound")
    return (not isinstance(c, bool) and isinstance(c, (int, float))
            and c == c and c <= NEWS_SENTIMENT_WARN)


def rescore_failure_status(items: list[dict], window_start_ts) -> dict:
    """Status for a Home rescore that raised: nothing was filtered."""
    n = len(items)
    selected = n
    if window_start_ts is not None:
        try:
            from stock_analyzer.held_news_check import rescore_selection
            send, over = rescore_selection(items, window_start_ts, max_items=_RESCORE_MAX_ITEMS)
            selected = len(send) + len(over)
            idx = send + over
        except Exception:
            idx = list(range(n))
    else:
        idx = list(range(n))
    return {
        "selected": selected, "sent": 0, "from_memo": 0, "filtered": 0,
        "unfiltered": selected,
        "unfiltered_negative": sum(1 for i in idx if _is_neg(items[i])),
        "failed_batches": 0, "reason": "error",
    }


def _memo_key(item: dict) -> tuple:
    title = item.get("title") or item.get("headline", "") or ""
    return (str(item.get("ticker", "")).strip().upper(), str(title).lower()[:70])


def rescore_brief_news(
    items: list[dict],
    api_key: str,
    *,
    window_start_ts: float,
    memo: dict,
    now_ts: float,
    timeout: float = 8.0,
) -> tuple[list[dict], dict | None]:
    """Decision-scoped suppress-only rescore of the full held headline list.

    Sends the first 20 distinct titles plus in-window items <= NEWS_SENTIMENT_WARN (at
    most _RESCORE_MAX_ITEMS), batched and memoized. Returns copies of ALL items
    (order unchanged) and a status dict (None when no api_key). `memo` is
    mutated in place: {"scores": {(TICKER, title[:70]): score}, "failed_at": ts}.
    """
    result = [dict(i) for i in items]
    if not api_key:
        return result, None

    from concurrent.futures import ThreadPoolExecutor
    from stock_analyzer.held_news_check import rescore_selection

    memo.setdefault("scores", {})
    memo.setdefault("failed_at", None)
    memo.setdefault("noscore", set())
    scores = memo["scores"]
    noscore = memo["noscore"]

    send, over = rescore_selection(items, window_start_ts, max_items=_RESCORE_MAX_ITEMS)
    from_memo = [i for i in send if _memo_key(items[i]) in scores or _memo_key(items[i]) in noscore]
    todo = [i for i in send if _memo_key(items[i]) not in scores and _memo_key(items[i]) not in noscore]

    failed_at = memo.get("failed_at")
    backoff = failed_at is not None and (now_ts - failed_at) < _RESCORE_RETRY_S
    batches: list[list[int]] = []
    if todo and not backoff:
        batches = [todo[k:k + _RESCORE_BATCH] for k in range(0, len(todo), _RESCORE_BATCH)]

    failed_batches = 0
    n_sent = 0
    failed_idx: set[int] = set()
    if batches:
        def _run(b):
            return llm_score_batch([items[i] for i in b], api_key, timeout)

        with ThreadPoolExecutor(max_workers=_RESCORE_WORKERS) as ex:
            outs = list(ex.map(_run, batches))
        for b, got in zip(batches, outs):
            if got is None:
                failed_batches += 1
                failed_idx.update(b)
                continue
            n_sent += len(b)
            for i, sc in zip(b, got):
                if sc is not None:
                    scores[_memo_key(items[i])] = sc
                else:
                    noscore.add(_memo_key(items[i]))   # checked, no usable score
        if failed_batches:
            memo["failed_at"] = now_ts

    changed = 0
    for i in send:
        sc = scores.get(_memo_key(items[i]))
        if sc is None:
            continue
        updated = _apply_suppress_only(items[i], sc)
        if updated is not None:
            result[i] = updated
            changed += 1

    skipped_backoff = len(todo) if (todo and backoff) else 0
    # Selected items left raw: over-bound, failed/backed-off, or scored None.
    raw_idx = set(over) | failed_idx
    if todo and backoff:
        raw_idx |= set(todo)
    for i in send:
        if scores.get(_memo_key(items[i])) is None:
            raw_idx.add(i)
    unfiltered = len(raw_idx)
    unfiltered_neg = sum(1 for i in raw_idx if _is_neg(items[i]))
    reason = ("error" if failed_batches else
              "over_bound" if over else
              "backoff" if skipped_backoff else None)
    status = {
        "selected": len(send) + len(over),
        "sent": n_sent,
        "from_memo": len(from_memo),
        "filtered": changed,
        "unfiltered": unfiltered,
        "unfiltered_negative": unfiltered_neg,
        "failed_batches": failed_batches,
        "reason": reason,
    }
    return result, status


def rescore_headlines_llm(
    headlines: list[dict],
    api_key: str,
    ticker: str = "",
    timeout: float = 8.0,
) -> list[dict] | None:
    """Bidirectional LLM rescore for VADER-annotated headline dicts (from analyze_news).

    Works on the {"headline", "score", "label", "url"} format returned by analyze_news —
    distinct from rescore_news_items_llm which targets the raw Yahoo news format.

    Unlike the suppress-only sidebar version, this is fully bidirectional: the LLM can
    both raise and lower scores using financial domain context. Per-headline adjustment
    is capped at SENTIMENT_LLM_MAX_SWING to prevent a single LLM outlier from dominating
    the average. Falls back to original VADER scores on any failure.

    Ticker context is injected into the prompt so the LLM scores relative to THIS stock's
    investment thesis (e.g. "IBM's earnings warning is great for CrowdStrike" scores very
    bullish for CRWD even though the headline is bearish for IBM).
    """
    if not headlines or not api_key:
        return headlines

    try:
        import anthropic
        import json

        headline_lines = [
            f'{i}. "{h.get("headline", "")}"' for i, h in enumerate(headlines)
        ]

        prompt = (
            f"You are a financial sentiment analyst. Score each headline for {ticker or 'this stock'} investors.\n"
            f"Question: how does each headline affect the {ticker or 'this stock'} investment thesis specifically?\n\n"
            "SCORING RULES (apply in order):\n"
            f"1. Direct {ticker or 'stock'} news (earnings, guidance, analyst call, product, price target, "
            f"   stock move, market share for {ticker or 'this company'}) → high magnitude ≥ 0.25\n"
            "2. Analyst turns bullish on this stock / hits buy point = +0.3 to +0.5\n"
            "3. Analyst turns bearish on this stock / stock slips on fundamentals = -0.3 to -0.5\n"
            "4. This stock gains market share, widens competitive lead = +0.3 to +0.5\n"
            "5. Competitor's bad earnings: only bearish for this stock if business models are DIRECTLY correlated "
            "   (e.g. same customer segment). An enterprise IT company missing earnings is NOT bearish for a "
            "   consumer tech company. When in doubt → 0.0.\n"
            "6. Generic roundup or multi-stock list where this stock is one of many → 0.0\n"
            "7. Macro/sector noise with no direct implication for this stock's fundamentals → 0.0\n\n"
            "Be decisive: a clearly bullish or bearish signal scores ≥ 0.25 in magnitude. "
            "Only use 0.0 for genuinely ambiguous or irrelevant headlines.\n\n"
            "Return ONLY a JSON array with no other text:\n"
            '[{"idx": 0, "score": 0.4}, {"idx": 1, "score": -0.3}, ...]\n\n'
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
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        scores = json.loads(raw.strip())

        result = [dict(h) for h in headlines]
        for entry in scores:
            idx = entry.get("idx")
            llm_score = entry.get("score")
            if not isinstance(idx, int) or idx < 0 or idx >= len(headlines):
                continue
            if not isinstance(llm_score, (int, float)):
                continue
            llm_score = float(llm_score)
            if not (-1.0 <= llm_score <= 1.0):
                continue
            vader_score = headlines[idx].get("score", 0.0)
            # Apply swing cap: LLM can't shift a score more than MAX_SWING from VADER
            delta = llm_score - vader_score
            if abs(delta) > SENTIMENT_LLM_MAX_SWING:
                llm_score = round(
                    vader_score + SENTIMENT_LLM_MAX_SWING * (1.0 if delta > 0 else -1.0),
                    3,
                )
            else:
                llm_score = round(llm_score, 3)
            label = (
                "Positive" if llm_score >= 0.05 else
                "Negative" if llm_score <= -0.05 else
                "Neutral"
            )
            result[idx]["score"] = llm_score
            result[idx]["label"] = label

        return result

    except Exception:
        return None  # signals "LLM did not run" — caller must not write this to the day-cache
