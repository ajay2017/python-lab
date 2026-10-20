"""
Self Track Record — "is MY instinct good?" vs the Engine Track Record's
"is the ENGINE good?" (🎯 Summary pointer card, recommendations_history.py).

Every BUY trade in the journal is classified into one of four buckets by
whether a matching app recommendation exists on file within a short lookback
window before the trade date:

  app_aligned        — a matching new_pick / overflow_pick / add_winner /
                        enter_now (not already held) rec, a diversify_add
                        rec_event, or a historical buy_candidate whose stored
                        composite_score >= COMPOSITE_BUY, exists within
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
influence on the BUY bucket (the one SELL-side exception is STOP_HIT, below) — that field records what the USER said at trade
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
instinct good?" vs the engine's exit calls (exit_signals EXIT / TRIM / RISK_OFF, plus
rec_events rebal_trim / beta_trim) — graded against those instead of
recommendations. Three graded buckets plus a counted-but-ungraded
stop_unlogged bucket (no
self_out_of_scope equivalent — every held ticker is inherently something the
engine could have signalled on). See the section header above those
functions for the full bucket definitions and the deliberate
missed-rec-framing inversion.
"""

from datetime import date, timedelta
from numbers import Real as _Real

from stock_analyzer.recommendations_history import _to_date


def _f(v, default=0.0):
    if v is None:
        return default
    try:
        x = float(v)
        return default if x != x else x
    except (TypeError, ValueError):
        return default


def _truthy_flag(v) -> bool:
    """True only for a real affirmative (True / 1 / "true"); None/NaN/absent
    is NOT affirmative."""
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "t", "1", "yes")
    try:
        return bool(v) and v == v
    except (TypeError, ValueError):
        return False


def _rec_event_index(rec_events_df, types) -> dict:
    """{rec_type: {TICKER: [fired_date, ...]}} for the wanted rec_events types.
    None / empty / column-less frame -> {} (nothing to match)."""
    out: dict = {}
    try:
        if rec_events_df is None or len(rec_events_df) == 0:
            return out
        for _, r in rec_events_df.iterrows():
            rt = str(r.get("rec_type", "") or "").strip()
            if rt not in types:
                continue
            tk = str(r.get("ticker", "") or "").strip().upper()
            fd = _to_date(r.get("fired_date"))
            if not tk or fd is None:
                continue
            out.setdefault(rt, {}).setdefault(tk, []).append(fd)
    except Exception:
        return out
    return out


# Call types the matcher recognises (single source for both sides + coverage).
_BUY_REC_TYPES = ("new_pick", "overflow_pick", "buy_candidate",
                  "add_winner", "enter_now")
_BUY_EVENT_TYPES = ("diversify_add",)
_SELL_SIGNAL_TYPES = ("EXIT", "TRIM", "RISK_OFF")
_SELL_EVENT_TYPES = ("rebal_trim", "beta_trim")


def _first_match(index: dict, lo: date, hi: date):
    """Name of the first call type (insertion order) with a date in [lo, hi]."""
    for name, dates in index.items():
        if any(lo <= d <= hi for d in dates):
            return name
    return None


def matched_call_coverage(recs_df, exit_signals_df, rec_events_df=None) -> dict:
    """Earliest stored date per call type, so a page can say "matched since".
    Keys: every buy rec type that counts as a call (buy_candidate included),
    diversify_add, exit_EXIT / exit_TRIM / exit_RISK_OFF, rebal_trim, beta_trim.
    A type with no stored row (or an unreadable frame) -> None."""
    cov: dict = {k: None for k in (
        list(_BUY_REC_TYPES) + list(_BUY_EVENT_TYPES)
        + [f"exit_{t}" for t in _SELL_SIGNAL_TYPES] + list(_SELL_EVENT_TYPES))}

    def _upd(key, d):
        if d is not None and (cov[key] is None or d < cov[key]):
            cov[key] = d

    try:
        if recs_df is not None and len(recs_df) > 0:
            for _, r in recs_df.iterrows():
                rt = str(r.get("rec_type", "") or "").strip()
                if rt in _BUY_REC_TYPES:
                    _upd(rt, _to_date(r.get("rec_date")))
        if exit_signals_df is not None and len(exit_signals_df) > 0:
            for _, sg in exit_signals_df.iterrows():
                st_ = str(sg.get("signal_type", "") or "").strip().upper()
                if st_ in _SELL_SIGNAL_TYPES:
                    _upd(f"exit_{st_}", _to_date(sg.get("signal_date")))
        ev = _rec_event_index(rec_events_df,
                              _BUY_EVENT_TYPES + _SELL_EVENT_TYPES)
        for rt, by_tk in ev.items():
            for ds in by_tk.values():
                for d in ds:
                    _upd(rt, d)
    except Exception:
        pass
    return cov


# Plain names + which source frame holds each call type, for the page caption.
_COVERAGE_LABELS = (
    ("new_pick", "Grow Today picks", "recs"),
    ("overflow_pick", "overflow picks", "recs"),
    ("add_winner", "add-to-winner", "recs"),
    ("enter_now", "Watchlist ready-to-enter", "recs"),
    ("buy_candidate", "earlier buy candidates", "recs"),
    ("diversify_add", "diversify adds", "events"),
    ("exit_EXIT", "EXIT signals", "exits"),
    ("exit_TRIM", "TRIM signals", "exits"),
    ("exit_RISK_OFF", "risk-off signals", "exits"),
    ("rebal_trim", "rebalance trims", "events"),
    ("beta_trim", "beta trims", "events"),
)


def matched_call_coverage_caption(recs_df, exit_signals_df, rec_events_df=None) -> str:
    """One caption: which app calls the matcher knows and since when. A call
    type whose SOURCE frame is None (couldn't be read) says "couldn't be read",
    never "no rows yet" (that wording is only for a readable, empty source)."""
    cov = matched_call_coverage(recs_df, exit_signals_df, rec_events_df)
    src_none = {"recs": recs_df is None, "exits": exit_signals_df is None,
                "events": rec_events_df is None}
    parts = []
    for key, name, src in _COVERAGE_LABELS:
        if src_none[src]:
            parts.append(f"{name} couldn't be read")
        else:
            parts.append(f"{name} {cov.get(key) or 'no rows yet'}")
    return "Matched app calls (and since when): " + " · ".join(parts) + "."


# ── Classification ──────────────────────────────────────────────────────────

def classify_buys(
    trades_df,
    recs_df,
    universe_set: set,
    watchlist_set: set,
    reliable_log_start: date,
    match_lookback_days: int,
    rec_events_df=None,
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
    trade_date, shares, price, bucket, user_thesis, situational_category) PLUS
    the fields
    recommendations_history.compute_outcomes needs to grade this trade's
    alpha-vs-SPY (rec_date, acted_on, acted_trade) — one record per BUY row,
    never collapsed across multiple buys of the same ticker.
    """
    if recs_df is None:
        return None

    from stock_analyzer.constants import COMPOSITE_BUY

    # Two indexes. `rec_index` = app_aligned matches (new_pick, overflow_pick,
    # and a historical buy_candidate ONLY when its stored composite cleared the
    # Buy bar). `weak_bc_index` = buy_candidate rows that did NOT clear it
    # (below-bar, NULL, NaN, non-numeric): the app was steering away from those
    # names, so a trade matching only them is self-initiated, tagged
    # rule_reclassified so the page can disclose the rule change.
    # rec_index is keyed ticker -> {call_type: [dates]} so each match can name
    # WHICH stored call it matched (`matched_call`).
    rec_index: dict[str, dict[str, list[date]]] = {}
    weak_bc_index: dict[str, list[date]] = {}
    if len(recs_df) > 0:
        for _, r in recs_df.iterrows():
            rt = str(r.get("rec_type", "") or "").strip()
            if rt not in _BUY_REC_TYPES:
                continue
            # A Watchlist row for a name already held was shown as "Already in
            # portfolio", not an entry call -- never a match.
            if rt == "enter_now" and _truthy_flag(r.get("already_held")):
                continue
            tk = str(r.get("ticker", "") or "").strip().upper()
            rd = _to_date(r.get("rec_date"))
            if not tk or rd is None:
                continue
            if rt == "buy_candidate":
                cs = r.get("composite_score")
                ok = False
                # A real number only: a numeric-looking string is NOT a score.
                # NaN and -inf fail the >= comparison by themselves; only +inf
                # would pass it, hence the explicit upper guard. (bool is a
                # Real but 0/1 is below the bar anyway.)
                if isinstance(cs, _Real):
                    try:
                        cf = float(cs)
                        ok = cf < float("inf") and cf >= COMPOSITE_BUY
                    except (TypeError, ValueError, OverflowError):
                        ok = False
                if not ok:
                    weak_bc_index.setdefault(tk, []).append(rd)
                    continue
            rec_index.setdefault(tk, {}).setdefault(rt, []).append(rd)

    # rec_events diversify_add: ticker = the named ADD candidate.
    for _rt, _by_tk in _rec_event_index(rec_events_df, _BUY_EVENT_TYPES).items():
        for _tk, _ds in _by_tk.items():
            rec_index.setdefault(_tk, {}).setdefault(_rt, []).extend(_ds)

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
        matched_call = _first_match(rec_index.get(tk, {}), lo, D)
        has_rec = matched_call is not None
        rule_reclassified = (not has_rec) and any(
            lo <= rd <= D for rd in weak_bc_index.get(tk, []))
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
            "rule_reclassified": rule_reclassified,
            "matched_call": matched_call,
            "user_thesis": t.get("user_thesis"),
            "situational_category": t.get("situational_category"),
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


def last_buy_candidate_date(recs_df) -> str | None:
    """ISO date (YYYY-MM-DD) of the newest `buy_candidate` row in `recs_df`, or
    None when there is none or the frame is unreadable. Derived from data only
    -- the retirement date is never typed in."""
    try:
        if recs_df is None or len(recs_df) == 0:
            return None
        if "rec_type" not in recs_df.columns or "rec_date" not in recs_df.columns:
            return None
        ds = []
        for _, r in recs_df.iterrows():
            if str(r.get("rec_type", "") or "").strip() != "buy_candidate":
                continue
            d = _to_date(r.get("rec_date"))
            if d is not None:
                ds.append(d)
        return max(ds).isoformat() if ds else None
    except Exception:
        return None


def rule_reclassified_caption(n, last_bc_date=None) -> str:
    """My Edge caption explaining the D5(ii) rule change. "" unless n > 0.
    `last_bc_date` None -> the parenthetical date clause is dropped, never
    invented."""
    from stock_analyzer.constants import COMPOSITE_BUY
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    when = f" ({last_bc_date})" if last_bc_date else ""
    return (
        "ℹ️ How app-aligned is counted: a buy counts as the app's call only if "
        "it matched a Grow Today pick, an overflow (\"Also cleared\") name, an "
        "add-to-winner, a Watchlist ready-to-enter (not already held) or a "
        "diversify-add call — or, before the "
        f"More Buy Candidates feed was retired{when}, a More Buy Candidates name "
        f"whose composite was ≥ {COMPOSITE_BUY}. {n} earlier graded buy(s) matched only "
        "a lower-scored or unscored More Buy Candidates name — names the app was "
        "steering away from — so they count as self-initiated."
    )


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
          "n_rule_reclassified": int,   # graded self trades that matched ONLY a
                                        # below-bar/unscored buy_candidate
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
        # Same graded self population as self_graded["n"] (never hardcoded).
        "n_rule_reclassified":
            sum(1 for r in self_graded_items
                if _is_graded(r) and r.get("rule_reclassified")),
        "app_aligned":          _graded(buckets["app_aligned"]),
        "self_graded":          _graded(self_graded_items),
        "min_sample_n":         min_sample_n,
    }


def situational_category_breakdown(
    classified: list[dict] | None,
    current_prices: dict | None,
    spy_close_by_date: dict | None,
    today: date,
    min_sample_n: int,
) -> list[dict] | None:
    """
    Slices the self-initiated BUY population (self_out_of_scope +
    self_in_scope only -- never app_aligned or coverage_limited) by the
    situational_category tag captured at Log Trade time, showing each
    category's graded alpha-vs-SPY average.

    Returns None when `classified is None` (propagates the same
    offline-sentinel signal classify_buys returns).

    Reuses recommendations_history.compute_outcomes for the alpha math
    (never reimplemented) -- the SAME call self_vs_engine_summary() makes,
    so this breakdown's population is identical to what the head-to-head
    caption above it describes. This is the reconciliation invariant: the
    sum of every category's graded n here MUST equal
    self_vs_engine_summary(...)["self_graded"]["n"] on the same inputs --
    this is the exact class of bug F-246 found elsewhere (captioning an
    average against a population different from the number stated beside
    it). Do not filter differently than self_vs_engine_summary does.

    Rows with no situational_category (None/empty/missing -- logged before
    this shipped, or the dropdown was skipped) are grouped under the fixed
    key "Uncategorized" rather than a None key, so the UI never has to
    special-case a missing tag.

    Returns a list of dicts, one per category in SITUATIONAL_CATEGORIES
    order, then "Uncategorized" last -- always all 6 rows, even at n=0, so
    the table is stable across renders:
        [{"category": str, "n": int, "avg_alpha_pct": float | None,
          "sufficient": bool}, ...]
    `n` = graded (matured, priced) count in that category. `avg_alpha_pct`
    is None when n < min_sample_n (not yet "sufficient"). Uses the SAME
    _is_graded predicate and averaging/rounding shape as the existing
    _graded() helper in this module -- don't reinvent the math.
    """
    if classified is None:
        return None

    from stock_analyzer.constants import REC_SCORE_MIN_DAYS
    from stock_analyzer.decision_journal import SITUATIONAL_CATEGORIES
    from stock_analyzer.recommendations_history import compute_outcomes

    enriched = compute_outcomes(
        classified, current_prices, today,
        spy_close_by_date=spy_close_by_date, min_days=REC_SCORE_MIN_DAYS,
    )

    self_rows = [r for r in enriched
                 if r.get("bucket") in ("self_out_of_scope", "self_in_scope")]

    # Same predicate/averaging shape as self_vs_engine_summary's _graded()
    # (duplicated rather than shared — same precedent as the SELL-side
    # sibling below, which duplicates its own copy rather than importing).
    def _is_graded(r: dict) -> bool:
        return r.get("alpha_pct") is not None and not r.get("outcome_maturing")

    grouped: dict[str, list[dict]] = {}
    for r in self_rows:
        cat_raw = str(r.get("situational_category") or "").strip()
        # Any value outside the locked vocabulary (missing, empty, or a
        # stray/legacy string) falls into "Uncategorized" rather than being
        # silently dropped -- required so sum(n) across this breakdown
        # always equals self_vs_engine_summary's self_graded["n"] exactly.
        cat = cat_raw if cat_raw in SITUATIONAL_CATEGORIES else "Uncategorized"
        grouped.setdefault(cat, []).append(r)

    out: list[dict] = []
    for cat in list(SITUATIONAL_CATEGORIES) + ["Uncategorized"]:
        items = grouped.get(cat, [])
        vals = [r["alpha_pct"] for r in items if _is_graded(r)]
        n = len(vals)
        # Gated on min_sample_n (unlike self_vs_engine_summary's _graded(),
        # which returns an average at any n>0 and leaves the sufficient-gate
        # decision to the caller) -- deliberate for this breakdown, since a
        # 5-way split shrinks each cell's N far below the two-bucket summary
        # above it, and per-category "not yet sufficient" must read as
        # "building", never a real average, wherever it's read from.
        avg = round(sum(vals) / n, 2) if n >= min_sample_n else None
        out.append({
            "category":      cat,
            "n":              n,
            "avg_alpha_pct":  avg,
            "sufficient":     n >= min_sample_n,
        })
    return out


# ── SELL-side ("is my own EXIT instinct good?") ─────────────────────────────
#
# Sibling of the BUY-side classification above, answering the mirrored
# question against exit_signals (EXIT/TRIM) instead of recommendations
# (new_pick/buy_candidate). Three buckets, not four — sells have no
# "out of scope" concept the way buys do against a scanned universe (every
# held ticker is inherently something the engine could have signalled on):
#
#   engine_aligned    — an EXIT / TRIM / RISK_OFF exit_signals row, or a
#                        rebal_trim / beta_trim rec_event, for the ticker falls
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
#   stop_unlogged      — no matching call AND the user tagged the sell
#                        trigger_type STOP_HIT. The app does not log stop
#                        breaches, so whose stop it was is unknown: counted
#                        and disclosed, never graded, never "self-initiated".
#                        (The only place this module reads a user field; a
#                        matching call always wins over the tag.)

def classify_sells(
    trades_df,
    exit_signals_df,
    reliable_log_start: date,
    signal_window_days: int,
    rec_events_df=None,
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

    # ticker -> {matched_call name: [dates]}; WATCH never counts.
    sig_index: dict[str, dict[str, list[date]]] = {}
    if len(exit_signals_df) > 0:
        for _, sg in exit_signals_df.iterrows():
            st_ = str(sg.get("signal_type", "") or "").strip().upper()
            if st_ not in _SELL_SIGNAL_TYPES:
                continue
            tk = str(sg.get("ticker", "") or "").strip().upper()
            sd = _to_date(sg.get("signal_date"))
            if not tk or sd is None:
                continue
            sig_index.setdefault(tk, {}).setdefault(f"exit_{st_}", []).append(sd)
    for _rt, _by_tk in _rec_event_index(rec_events_df, _SELL_EVENT_TYPES).items():
        for _tk, _ds in _by_tk.items():
            sig_index.setdefault(_tk, {}).setdefault(_rt, []).extend(_ds)

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
        matched_call = _first_match(sig_index.get(tk, {}), lo, D)

        # The ONLY use of a user field in this module: a sell with no matching
        # call that the user tagged STOP_HIT is not called "self-initiated" --
        # the app does not log stop breaches, so whose stop it was is unknown.
        # A matching call always wins over the tag. Absent/None -> not a stop.
        _tt = t.get("trigger_type")
        is_stop = str(_tt or "").strip().upper() == "STOP_HIT"

        if matched_call is not None:
            bucket = "engine_aligned"
        elif is_stop:
            bucket = "stop_unlogged"
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
            "matched_call": matched_call,
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
          "n_coverage_limited": int, "n_stop_unlogged": int,
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
        "stop_unlogged": [],   # counted, never graded (stop sales aren't logged)
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
        "n_stop_unlogged":     len(buckets["stop_unlogged"]),
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
