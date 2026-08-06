"""
Self Track Record — "is MY instinct good?" vs the Engine Track Record's
"is the ENGINE good?" (🎯 Summary pointer card, recommendations_history.py).

Every BUY trade in the journal is classified into one of four buckets by
whether a matching app recommendation exists on file within a short lookback
window before the trade date:

  app_aligned        — a matching new_pick/buy_candidate rec exists within
                        SELF_TRACK_MATCH_LOOKBACK_DAYS before (inclusive) the
                        trade date. This is the App's own call, not the
                        user's self-initiated instinct — graded here only as
                        the comparison baseline.
  self_in_scope      — no matching rec, ticker IS in the scanned universe or
                        the watchlist, trade date is on/after
                        SELF_TRACK_RELIABLE_LOG_START (cron-side rec logging
                        is trustworthy from that date) — a genuine
                        self-initiated call the App could plausibly have
                        surfaced but didn't. Graded.
  self_out_of_scope  — no matching rec, ticker is OUTSIDE the scanned
                        universe/watchlist — the App had no way to surface
                        it regardless of logging coverage. Graded, always
                        (date-independent — scope, not coverage, is the
                        reason).
  coverage_limited   — no matching rec, ticker IS in scope, but the trade
                        predates SELF_TRACK_RELIABLE_LOG_START — recommendation
                        coverage from that period is unreliable (the cron
                        scan didn't yet persist its own new_pick rows), so a
                        missing rec here is AMBIGUOUS, not evidence of a
                        genuinely self-initiated call. Disclosed, never
                        graded either way.

`trigger_type` (MANUAL / RECOMMENDATION on the trades table) has ZERO
influence on the bucket — that field records what the USER said at trade
time; this module independently re-derives the same-instinct-vs-app-aligned
split from the actual recommendation history, which is the more reliable
signal (a user can mis-tag trigger_type, or the field can be absent on
older/imported rows).

Pure logic — no Streamlit, no DB/API calls. Caller supplies trades_df,
recs_df, the universe/watchlist scope sets, current prices, and the SPY
close series; reuses recommendations_history.compute_outcomes for the actual
alpha-vs-SPY math (never reimplemented here).
"""

from datetime import date, timedelta

from stock_analyzer.recommendations_history import _to_date


def _f(v, default=0.0):
    if v is None:
        return default
    try:
        x = float(v)
        return default if x != x else x
    except (TypeError, ValueError):
        return default


# ── Classification ──────────────────────────────────────────────────────────

def classify_buys(
    trades_df,
    recs_df,
    universe_set: set,
    watchlist_set: set,
    reliable_log_start: date,
    match_lookback_days: int,
) -> list[dict] | None:
    """
    Classify every BUY row in `trades_df` into one of the four buckets above.

    Returns `None` when `recs_df is None` — the offline-sentinel-collapse
    guard (CLAUDE.md): a FAILED recommendations load must never be silently
    treated as "zero recs exist," or every BUY would wrongly classify as
    self-initiated. A genuinely empty-but-not-None `recs_df` (zero rows, a
    valid "no recs yet" state) still classifies normally — every trade
    simply finds no rec match.

    Each returned dict carries the public classification fields (id, ticker,
    trade_date, shares, price, bucket, user_thesis) PLUS the fields
    recommendations_history.compute_outcomes needs to grade this trade's
    alpha-vs-SPY (rec_date, acted_on, acted_trade) — one record per BUY row,
    never collapsed across multiple buys of the same ticker.
    """
    if recs_df is None:
        return None

    rec_index: dict[str, list[date]] = {}
    if len(recs_df) > 0:
        for _, r in recs_df.iterrows():
            rt = str(r.get("rec_type", "") or "").strip()
            if rt not in ("new_pick", "buy_candidate"):
                continue
            tk = str(r.get("ticker", "") or "").strip().upper()
            rd = _to_date(r.get("rec_date"))
            if not tk or rd is None:
                continue
            rec_index.setdefault(tk, []).append(rd)

    out: list[dict] = []
    if trades_df is None or len(trades_df) == 0:
        return out

    for _, t in trades_df.iterrows():
        action = str(t.get("action", "") or "").strip().upper()
        if action != "BUY":
            continue

        D = _to_date(t.get("traded_at"))
        tk = str(t.get("ticker", "") or "").strip().upper()
        if D is None or not tk:
            continue

        lo = D - timedelta(days=match_lookback_days)
        has_rec = any(lo <= rd <= D for rd in rec_index.get(tk, []))
        in_scope = tk in universe_set or tk in watchlist_set

        if has_rec:
            bucket = "app_aligned"
        elif not in_scope:
            bucket = "self_out_of_scope"
        elif D >= reliable_log_start:
            bucket = "self_in_scope"
        else:
            bucket = "coverage_limited"

        shares = _f(t.get("shares"))
        price  = _f(t.get("price"))
        out.append({
            "id":          t.get("id"),
            "ticker":      tk,
            "trade_date":  D,
            "shares":      shares,
            "price":       price,
            "bucket":      bucket,
            "user_thesis": t.get("user_thesis"),
            # ── fields compute_outcomes() reads (same shape as
            # recommendations_history.match_recs_to_trades' acted rows) ──
            "rec_date":    D,       # alpha is benchmarked from the trade's
                                     # OWN date — a self-initiated buy has no
                                     # rec_date of its own to anchor to, and
                                     # app_aligned buys are graded on what
                                     # was actually executed, same as any
                                     # other trade here (per-trade framing).
            "acted_on":    True,
            "acted_trade": {
                "action":       "BUY",
                "shares":       shares,
                "price":        price,
                "cost_basis":   None,
                "realized_pnl": None,
                "traded_at":    t.get("traded_at"),
            },
        })
    return out


# ── Summary ──────────────────────────────────────────────────────────────────

def self_vs_engine_summary(
    classified: list[dict] | None,
    current_prices: dict | None,
    spy_close_by_date: dict | None,
    today: date,
    min_sample_n: int,
) -> dict:
    """
    Grade `classified` (the output of classify_buys) into the two comparison
    averages: app_aligned vs self_graded (= self_out_of_scope + self_in_scope).
    `coverage_limited` is counted but never averaged.

    Returns {"available": False} when `classified is None` (propagates the
    same offline-sentinel signal classify_buys returned) — matches the
    convention this codebase already uses for "producer failed" cache state
    (see CLAUDE.md coordination pattern: caches set to None, not an empty
    container, on failure).

    Reuses recommendations_history.compute_outcomes for the actual
    alpha-vs-SPY math per trade (never reimplemented here); REC_SCORE_MIN_DAYS
    maturity gating is applied via that same function so a same-week self buy
    can't yet inflate/deflate either average.

    Returns:
        {
          "available": True,
          "n_app_aligned": int, "n_self_out_of_scope": int,
          "n_self_in_scope": int, "n_self_graded": int,
          "n_coverage_limited": int,
          "app_aligned":  {"n": int, "avg_alpha_pct": float|None, "sufficient": bool},
          "self_graded":  {"n": int, "avg_alpha_pct": float|None, "sufficient": bool},
          "min_sample_n": int,
        }

    Each bucket's "n" inside app_aligned/self_graded is the MATURE, PRICED
    (alpha_pct not None) population actually averaged — the same population
    the "sufficient" gate and the caller's "building (N/min_sample_n)" caption
    both read, so they can never disagree.
    """
    if classified is None:
        return {"available": False}

    from stock_analyzer.constants import REC_SCORE_MIN_DAYS
    from stock_analyzer.recommendations_history import compute_outcomes

    enriched = compute_outcomes(
        classified, current_prices, today,
        spy_close_by_date=spy_close_by_date, min_days=REC_SCORE_MIN_DAYS,
    )

    buckets: dict[str, list[dict]] = {
        "app_aligned": [], "self_out_of_scope": [],
        "self_in_scope": [], "coverage_limited": [],
    }
    for r in enriched:
        b = r.get("bucket")
        if b in buckets:
            buckets[b].append(r)

    def _graded(items: list[dict]) -> dict:
        vals = [
            r["alpha_pct"] for r in items
            if r.get("alpha_pct") is not None and not r.get("outcome_maturing")
        ]
        n = len(vals)
        avg = round(sum(vals) / n, 2) if n else None
        return {"n": n, "avg_alpha_pct": avg, "sufficient": n >= min_sample_n}

    self_graded_items = buckets["self_out_of_scope"] + buckets["self_in_scope"]

    return {
        "available":           True,
        "n_app_aligned":        len(buckets["app_aligned"]),
        "n_self_out_of_scope":  len(buckets["self_out_of_scope"]),
        "n_self_in_scope":      len(buckets["self_in_scope"]),
        "n_self_graded":        len(self_graded_items),
        "n_coverage_limited":   len(buckets["coverage_limited"]),
        "app_aligned":          _graded(buckets["app_aligned"]),
        "self_graded":          _graded(self_graded_items),
        "min_sample_n":         min_sample_n,
    }
