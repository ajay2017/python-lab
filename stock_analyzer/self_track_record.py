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

SELL-side sibling (classify_sells / self_vs_engine_sell_summary /
detect_missed_exits, below): the mirrored question — "is MY OWN exit
instinct good?" vs the engine's EXIT/TRIM signals — graded against
exit_signals instead of recommendations. Three buckets, not four (no
self_out_of_scope equivalent — every held ticker is inherently something the
engine could have signalled on). See the section header above those
functions for the full bucket definitions and the deliberate
missed-rec-framing inversion.
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
          "n_self_in_scope_graded": int, "n_self_out_of_scope_graded": int,
          "n_coverage_limited": int,
          "app_aligned":  {"n": int, "avg_alpha_pct": float|None, "sufficient": bool},
          "self_graded":  {"n": int, "avg_alpha_pct": float|None, "sufficient": bool},
          "min_sample_n": int,
        }

    Each bucket's "n" inside app_aligned/self_graded is the MATURE, PRICED
    (alpha_pct not None) population actually averaged — the same population
    the "sufficient" gate and the caller's "building (N/min_sample_n)" caption
    both read, so they can never disagree.

    **`self_graded` averages ACROSS self_in_scope + self_out_of_scope, and those
    two answer different questions** — in-scope means the App had a view and
    stayed silent (a real head-to-head), out-of-scope means it never looked (no
    engine call to beat). So `n_self_in_scope_graded` /
    `n_self_out_of_scope_graded` give that split **on the graded population
    only**, i.e. the exact rows behind `self_graded["avg_alpha_pct"]`; they sum
    to `self_graded["n"]` by construction. The unfiltered `n_self_in_scope` /
    `n_self_out_of_scope` count every classified trade including immature and
    unpriced ones, so captioning the average with THOSE would describe a
    different population than the number above it — the F-246 mistake. Added
    2026-08-22: all three of `n_self_in_scope`, `n_self_out_of_scope` and
    `n_self_graded` had been returned since V1 and read by nothing but tests,
    so the panel showed a merged average with its composition invisible.
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

    # Single predicate so the per-bucket graded counts below cannot drift from
    # the population _graded() actually averages — the two must agree or the
    # caption would describe a different set than the metric above it.
    def _is_graded(r: dict) -> bool:
        return r.get("alpha_pct") is not None and not r.get("outcome_maturing")

    def _graded(items: list[dict]) -> dict:
        vals = [r["alpha_pct"] for r in items if _is_graded(r)]
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
        # The same split, restricted to the rows behind self_graded's average.
        # These two sum to self_graded["n"]; the unfiltered pair above does not.
        "n_self_in_scope_graded":
            sum(1 for r in buckets["self_in_scope"] if _is_graded(r)),
        "n_self_out_of_scope_graded":
            sum(1 for r in buckets["self_out_of_scope"] if _is_graded(r)),
        "n_coverage_limited":   len(buckets["coverage_limited"]),
        "app_aligned":          _graded(buckets["app_aligned"]),
        "self_graded":          _graded(self_graded_items),
        "min_sample_n":         min_sample_n,
    }


# ── SELL-side ("is my own EXIT instinct good?") ─────────────────────────────
#
# Sibling of the BUY-side classification above, answering the mirrored
# question against exit_signals (EXIT/TRIM) instead of recommendations
# (new_pick/buy_candidate). Three buckets, not four — sells have no
# "out of scope" concept the way buys do against a scanned universe (every
# held ticker is inherently something the engine could have signalled on):
#
#   engine_aligned    — an EXIT or TRIM exit_signals row for the ticker falls
#                        within SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS before
#                        (inclusive) the sell date. Matched, date-independent
#                        — a real signal match is unambiguous regardless of
#                        where the sell date sits relative to the log-
#                        reliability boundary.
#   self_initiated     — no matching signal, sell date is on/after
#                        SELF_TRACK_SELL_RELIABLE_LOG_START (exit_signals
#                        cron capture is trustworthy from that date) — a
#                        genuine self-initiated exit the engine had no active
#                        call on. Graded.
#   coverage_limited   — no matching signal, but the sell predates
#                        SELF_TRACK_SELL_RELIABLE_LOG_START — signal coverage
#                        from that period is unreliable (the cron didn't yet
#                        persist exit_signals rows reliably), so a missing
#                        signal here is AMBIGUOUS, not evidence of a genuinely
#                        self-initiated exit. Disclosed, never graded either
#                        way. WATCH-tier signals never count as a match —
#                        WATCH is a soft heads-up, not an exit decision.

def classify_sells(
    trades_df,
    exit_signals_df,
    reliable_log_start: date,
    signal_window_days: int,
) -> list[dict] | None:
    """
    Classify every SELL row in `trades_df` into one of the three buckets above.

    Returns `None` when `exit_signals_df is None` — the same offline-
    sentinel-collapse guard classify_buys uses for `recs_df is None`: a
    FAILED exit_signals load must never be silently treated as "no signals
    exist," or every SELL would wrongly classify as self-initiated. A
    genuinely empty-but-not-None `exit_signals_df` (zero rows, a valid "no
    signals yet" state) still classifies normally.

    *** CRITICAL FRAMING — READ BEFORE "FIXING" THIS ***
    Each returned dict deliberately frames the sell as a MISSED/UNACTED rec
    (acted_on=False, acted_trade=None), NOT as an acted SELL, even though a
    real SELL trade obviously happened. This is intentional and inverted from
    how classify_buys frames a BUY (acted_on=True there):
    recommendations_history.compute_outcomes() UNCONDITIONALLY returns
    alpha_pct=None for any acted SELL (realized P&L spans an unknown holding
    period that can't be benchmarked to a single SPY window — see that
    function's `acted_sell_unbenchmarkable` branch). Framing the sell as a
    missed/unacted rec instead routes it through compute_outcomes' OTHER
    branch: "what happened to the price after this rec, benchmarked to SPY"
    — using `price_at_surface` = the sell price and `rec_date` = the sell
    date. That is exactly the question this feature asks: did the stock
    outperform SPY after I sold? Setting acted_on=True or adding an
    acted_trade dict here would silently force alpha_pct to None for every
    single graded sell — a future editor "cleaning this up" to look like
    classify_buys would quietly zero the entire feature.
    """
    if exit_signals_df is None:
        return None

    sig_index: dict[str, list[date]] = {}
    if len(exit_signals_df) > 0:
        for _, s in exit_signals_df.iterrows():
            st_ = str(s.get("signal_type", "") or "").strip().upper()
            if st_ not in ("EXIT", "TRIM"):
                continue
            tk = str(s.get("ticker", "") or "").strip().upper()
            sd = _to_date(s.get("signal_date"))
            if not tk or sd is None:
                continue
            sig_index.setdefault(tk, []).append(sd)

    out: list[dict] = []
    if trades_df is None or len(trades_df) == 0:
        return out

    for _, t in trades_df.iterrows():
        action = str(t.get("action", "") or "").strip().upper()
        if action != "SELL":
            continue

        D = _to_date(t.get("traded_at"))
        tk = str(t.get("ticker", "") or "").strip().upper()
        if D is None or not tk:
            continue

        lo = D - timedelta(days=signal_window_days)
        has_signal = any(lo <= sd <= D for sd in sig_index.get(tk, []))

        if has_signal:
            bucket = "engine_aligned"
        elif D >= reliable_log_start:
            bucket = "self_initiated"
        else:
            bucket = "coverage_limited"

        shares = _f(t.get("shares"))
        price  = _f(t.get("price"))
        out.append({
            "id":          t.get("id"),
            "ticker":      tk,
            "sell_date":   D,
            "shares":      shares,
            "price":       price,
            "bucket":      bucket,
            "user_thesis": t.get("user_thesis"),
            # ── fields compute_outcomes() reads — deliberately a MISSED/
            # UNACTED shape, see the docstring above. ──
            "rec_date":         D,
            "acted_on":         False,
            "acted_trade":      None,
            "price_at_surface": price,
        })
    return out


def self_vs_engine_sell_summary(
    classified: list[dict] | None,
    current_prices: dict | None,
    spy_close_by_date: dict | None,
    today: date,
    min_sample_n: int,
) -> dict:
    """
    SELL-side sibling of self_vs_engine_summary(). Grades `classified` (the
    output of classify_sells) into engine_aligned vs self_graded (=
    self_initiated only — there is no self_out_of_scope equivalent here,
    sells don't have a "scope" concept the way buys do against a scanned
    universe). `coverage_limited` is counted but never averaged.

    Returns {"available": False} when `classified is None` (propagates the
    same offline-sentinel signal classify_sells returned).

    Reuses recommendations_history.compute_outcomes for the actual
    alpha-vs-SPY math per sell (never reimplemented here); REC_SCORE_MIN_DAYS
    maturity gating is applied via that same function.

    Sign convention — do NOT invert anything: compute_outcomes' raw
    alpha_pct = (post-sell stock return) − (SPY return over the same
    window). Negative = a GOOD exit (the stock underperformed the market
    after you sold it). Positive = you sold too early (the stock kept
    beating the market without you). This falls out correctly from the
    "missed rec" framing in classify_sells with no sign flip needed.

    Returns:
        {
          "available": True,
          "n_engine_aligned": int, "n_self_initiated": int,
          "n_coverage_limited": int,
          "engine_aligned": {"n": int, "avg_alpha_pct": float|None, "sufficient": bool},
          "self_graded":    {"n": int, "avg_alpha_pct": float|None, "sufficient": bool},
          "min_sample_n": int,
        }
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
        "engine_aligned": [], "self_initiated": [], "coverage_limited": [],
    }
    for r in enriched:
        b = r.get("bucket")
        if b in buckets:
            buckets[b].append(r)

    def _is_graded(r: dict) -> bool:
        return r.get("alpha_pct") is not None and not r.get("outcome_maturing")

    def _graded(items: list[dict]) -> dict:
        vals = [r["alpha_pct"] for r in items if _is_graded(r)]
        n = len(vals)
        avg = round(sum(vals) / n, 2) if n else None
        return {"n": n, "avg_alpha_pct": avg, "sufficient": n >= min_sample_n}

    return {
        "available":          True,
        "n_engine_aligned":    len(buckets["engine_aligned"]),
        "n_self_initiated":    len(buckets["self_initiated"]),
        "n_coverage_limited":  len(buckets["coverage_limited"]),
        "engine_aligned":      _graded(buckets["engine_aligned"]),
        "self_graded":         _graded(buckets["self_initiated"]),
        "min_sample_n":        min_sample_n,
    }


def detect_missed_exits(
    exit_signals_df,
    trades_df,
    held_tickers: set,
    today: date,
    signal_window_days: int,
) -> list[dict] | None:
    """
    Flag currently-HELD tickers whose most recent EXIT/TRIM signal has been
    active long enough (>= signal_window_days) that the user has had a full
    window to act on it, but no SELL of that ticker has occurred on/after the
    signal date. Awareness-only: this must NEVER be wired into risk_advisor,
    exit_advisor, or any gate — it exists purely to surface "the engine
    called this, you haven't acted" on the Self vs Engine tab, and does not
    influence any recommendation.

    Returns `None` when `exit_signals_df is None` (offline-sentinel guard,
    same convention as classify_sells).

    Returns a list of {"ticker", "signal_date", "signal_type", "days_since"}
    dicts, one per flagged ticker (its most recent qualifying EXIT/TRIM
    signal only — WATCH-tier signals never qualify, same as classify_sells).
    """
    if exit_signals_df is None:
        return None

    # Most recent EXIT/TRIM signal per ticker, with its signal_type attached.
    latest: dict[str, tuple[date, str]] = {}
    if len(exit_signals_df) > 0:
        for _, s in exit_signals_df.iterrows():
            st_ = str(s.get("signal_type", "") or "").strip().upper()
            if st_ not in ("EXIT", "TRIM"):
                continue
            tk = str(s.get("ticker", "") or "").strip().upper()
            sd = _to_date(s.get("signal_date"))
            if not tk or sd is None:
                continue
            prev = latest.get(tk)
            if prev is None or sd > prev[0]:
                latest[tk] = (sd, st_)

    # Most recent SELL date per ticker.
    last_sell: dict[str, date] = {}
    if trades_df is not None and len(trades_df) > 0:
        for _, t in trades_df.iterrows():
            action = str(t.get("action", "") or "").strip().upper()
            if action != "SELL":
                continue
            tk = str(t.get("ticker", "") or "").strip().upper()
            D = _to_date(t.get("traded_at"))
            if not tk or D is None:
                continue
            prev = last_sell.get(tk)
            if prev is None or D > prev:
                last_sell[tk] = D

    out: list[dict] = []
    for tk in held_tickers or set():
        tk_u = str(tk).strip().upper()
        sig = latest.get(tk_u)
        if sig is None:
            continue
        sd, sig_type = sig

        window_elapsed = sd <= (today - timedelta(days=signal_window_days))
        if not window_elapsed:
            continue

        sold_since = last_sell.get(tk_u)
        if sold_since is not None and sold_since >= sd:
            continue

        out.append({
            "ticker":      tk_u,
            "signal_date": sd,
            "signal_type": sig_type,
            "days_since":  (today - sd).days,
        })
    return out
