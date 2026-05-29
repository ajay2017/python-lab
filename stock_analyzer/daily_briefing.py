"""
Daily Briefing module.

Synthesizes all available intelligence into three prioritized action buckets:
  - Act Today      : stop triggers, sell signals, critical news, today's macro, REDUCE flags
  - Buy Candidates : scanner picks + add-to-winner, each with a multi-signal confidence verdict
  - Review Before Close : approaching stops, near-term earnings, weak large positions,
                          warning news, upcoming macro catalysts

Confidence verdict system (Buy Candidates)
------------------------------------------
Each candidate is cross-referenced across every available signal layer:
  Layer 1 — Technical setup   (scanner: RSI, trend, momentum)
  Layer 2 — Composite signal  (port_df Signal/Score — held positions only)
  Layer 3 — News sentiment    (news_items avg compound for ticker)
  Layer 4 — Earnings risk     (held_data earnings date)
  Layer 5 — Analyst revisions (held_data revisions.net — held only)

Verdict tiers:
  Confirmed  — all available layers agree (green)
  Mixed      — 1 soft conflict (amber)
  Conflicted — hard conflict: composite ≠ technical OR strong negative news (red)
  Caution    — earnings within 7 days regardless of other signals (amber)
"""

from datetime import date, timedelta

from stock_analyzer.constants import (
    COMPOSITE_BUY,
    COMPOSITE_STRONG_BUY,
    COMPOSITE_HIGH_CONVICTION,
    COMPOSITE_BUY_FLAT_DAY,
    SINGLE_NAME_CEILING,
    MACRO_IMMINENT_DAYS,
    RISK_PCT_PER_TRADE,
    ADD_WINNER_MIN_GAP_PCT,
    APPROACHING_STOP_GAP_PCT,
    LARGE_POSITION_WEIGHT_PCT,
    WEAK_CONVICTION_SCORE,
    NEWS_SENTIMENT_CRITICAL,
    NEWS_SENTIMENT_NEGATIVE,
    NEWS_SENTIMENT_WARN,
    NEWS_SENTIMENT_POSITIVE,
    STOP_PROFIT_LOCK_PNL_PCT,
    STOP_PROFIT_LOCK_TRIM_PCT,
    STOP_TIGHTEN_ATR_MULT,
    EARNINGS_OVERWEIGHT_TRIM_PCT,
    EARNINGS_OVERWEIGHT_TRIM_TO_PCT,
    WEAK_LARGE_TRIM_TO_PCT,
    MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
    MACRO_AFFECTED_TRIM_REDUCTION_PP,
)
from stock_analyzer.signal_reconciliation import (
    reconcile_signals,
    lookup_composite,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _f(val, default=0.0):
    if val is None:
        return default
    try:
        v = float(val)
        return default if v != v else v
    except (TypeError, ValueError):
        return default


def _days_until(date_str: str, today: date) -> int | None:
    try:
        d = date.fromisoformat(str(date_str)[:10])
        return (d - today).days
    except Exception:
        return None


def _trim_targets(risk_recs: list | None) -> dict[str, dict]:
    """
    Extract tickers Risk Advisor is recommending the user trim.

    Only beta and sharpe recs concretely identify trim targets (volatility,
    drawdown, and tail-risk recs instead recommend adding diversifiers).
    Returns {ticker: {"reason": rec_type, "title": rec_title, "priority": priority}}.
    """
    if not risk_recs:
        return {}
    targets: dict[str, dict] = {}
    for rec in risk_recs:
        pri = rec.get("priority")
        if pri not in ("HIGH", "MEDIUM"):
            continue
        rec_type = rec.get("type", "")
        if rec_type not in ("beta", "sharpe"):
            continue
        for rt in rec.get("root_tickers", []):
            t = rt.get("ticker")
            if not t or t in targets:
                continue
            targets[str(t).upper()] = {
                "reason":   rec_type,
                "title":    rec.get("title", ""),
                "priority": pri,
            }
    return targets


# ── Cross-reference engine ────────────────────────────────────────────────────

def _cross_reference(ticker: str, scanner_row: dict, port_df, news_items: list,
                     held_data: dict, today: date,
                     earnings_lookup: dict | None = None,
                     composites: dict | None = None) -> dict:
    """
    Cross-check a buy candidate across all available signal layers.

    earnings_lookup (optional): {ticker: earnings_date_str}. Sourced from BOTH
       held_data and pre-fetched composites, so the earnings risk check applies
       to non-held new picks too.
    composites (optional): {ticker: load_all() bundle} for pre-fetched scanner
       picks. When present, lets the composite signal participate in the verdict
       for non-held candidates too — without this, every new pick collapses to
       "Verify — Run Analysis First" even when the composite already exists in
       session_state (the TSLA-shows-as-buy-but-Stock-Analysis-says-sell case).

    Returns a dict with:
      verdict             — 'confirmed' | 'mixed' | 'conflicted' | 'caution' | 'unverified'
      verdict_label       — display string (the legacy tier badge)
      verdict_color       — hex colour
      verdict_one_liner   — explicit resolution sentence (NEW — surfaces should
                            render this prominently; the central reconciliation
                            engine populates it consistently across all callers)
      verdict_reconciled  — full dict from reconcile_signals() — has its own
                            verdict tier ('go'/'verify'/'caution'/'skip')
      agreed              — list of supporting signals
      conflicts           — list of conflicting signals
      layers_checked      — int
      is_held             — bool
      composite_available — bool (now True whenever ANY source has composite)
    """
    agreed:    list[str] = []
    conflicts: list[str] = []

    # ── Determine whether ticker is held (composite signal available) ─────────
    is_held = False
    if port_df is not None and not port_df.empty:
        is_held = not port_df[port_df["Ticker"] == ticker].empty

    # ── Layer 1: Technical setup (always present from scanner) ────────────────
    scan_sig   = str(scanner_row.get("Signal", ""))
    scan_score = _f(scanner_row.get("Score", 0))
    rsi        = _f(scanner_row.get("RSI", 0))
    trend      = str(scanner_row.get("Trend", ""))
    agreed.append(f"Technical: {scan_sig} ({scan_score:.0f}/100) · {trend} · RSI {rsi:.0f}")

    # ── Layer 2: Composite signal — held OR pre-fetched scanner pick ─────────
    # Previously this only consulted port_df (held positions). That left every
    # non-held candidate falling through to "Verify First" even when composite
    # was pre-fetched into composites dict. Now we look at both sources.
    comp_sig, comp_scr = lookup_composite(ticker, port_df, composites)
    composite_conflict   = False
    composite_available  = comp_sig is not None or comp_scr is not None
    if composite_available:
        buy_words       = ("Strong Buy", "Buy")
        hold_sell_words = ("Hold", "Sell", "Avoid", "Weak")
        _sig_disp = comp_sig or "n/a"
        _scr_disp = f"{comp_scr:.0f}/100" if comp_scr is not None else "—"
        if comp_sig and any(w in comp_sig for w in buy_words):
            agreed.append(f"Composite signal: {_sig_disp} ({_scr_disp})")
        elif comp_sig and any(w in comp_sig for w in hold_sell_words):
            conflicts.append(
                f"Composite signal: {_sig_disp} ({_scr_disp}) — "
                "technicals say Buy but full multi-factor analysis is more cautious"
            )
            composite_conflict = True
        else:
            agreed.append(f"Composite signal: {_sig_disp} ({_scr_disp})")

    # ── Layer 3: News sentiment ───────────────────────────────────────────────
    ticker_news = [n for n in (news_items or []) if str(n.get("ticker", "")).upper() == ticker]
    sentiment_conflict = False
    if ticker_news:
        avg_compound  = sum(n.get("compound", 0) for n in ticker_news) / len(ticker_news)
        best_headline = max(ticker_news, key=lambda n: abs(n.get("compound", 0)))
        if avg_compound <= NEWS_SENTIMENT_NEGATIVE:
            conflicts.append(
                f"News sentiment: Negative (avg {avg_compound:+.2f}) — "
                f"\"{best_headline.get('headline','')[:60]}\""
            )
            sentiment_conflict = True
        elif avg_compound >= NEWS_SENTIMENT_POSITIVE:
            agreed.append(f"News sentiment: Positive (avg {avg_compound:+.2f})")
        else:
            agreed.append(f"News sentiment: Neutral (avg {avg_compound:+.2f})")
    # No news available — don't add to agreed (absence of data ≠ confirmation)

    # ── Layer 4: Earnings risk ────────────────────────────────────────────────
    # Earnings date is looked up from a UNION of held_data + earnings_lookup so
    # non-held new picks are also screened. Previously the check fired only on
    # held positions, so a brand-new scanner pick with earnings tomorrow could
    # be marked "Confirmed" with no caution.
    earnings_conflict = False
    earn_days = None
    earn_date = None
    if earnings_lookup and ticker in earnings_lookup:
        earn_date = earnings_lookup[ticker]
    elif held_data and ticker in held_data:
        earn_date = (held_data[ticker] or {}).get("earnings")
    if earn_date:
        earn_days = _days_until(earn_date, today)
        if earn_days is not None and 0 <= earn_days <= 7:
            label = "today" if earn_days == 0 else f"in {earn_days}d"
            conflicts.append(
                f"Earnings {label} ({earn_date}) — binary event risk; "
                "signals may not hold post-release"
            )
            earnings_conflict = True
        elif earn_days is not None and 8 <= earn_days <= 21:
            agreed.append(f"Earnings in {earn_days}d — manageable window")

    # ── Layer 5: Analyst revisions (held positions with held_data) ────────────
    if held_data and ticker in held_data:
        revs = ((held_data[ticker] or {}).get("revisions") or {})
        net  = revs.get("net")
        if net is not None:
            if net >= 2:
                agreed.append(f"Analyst revisions: +{net} net upgrades (90d)")
            elif net <= -2:
                conflicts.append(f"Analyst revisions: {net} net downgrades (90d)")
            else:
                agreed.append(f"Analyst revisions: flat ({net:+d} net)")

    # ── Verdict ───────────────────────────────────────────────────────────────
    layers = len(agreed) + len(conflicts)

    # Earnings within 7 days is a binary risk event. When combined with other signal
    # conflicts it must ESCALATE severity, not override to a lower "caution" level.
    if earnings_conflict and (composite_conflict or sentiment_conflict):
        verdict       = "conflicted"
        verdict_label = "❌ Conflicted — Earnings + Signal Conflict"
        verdict_color = "#ef4444"
    elif composite_conflict and sentiment_conflict:
        verdict       = "conflicted"
        verdict_label = "❌ Conflicted — Multiple Conflicts"
        verdict_color = "#ef4444"
    elif composite_conflict:
        verdict       = "conflicted"
        verdict_label = "❌ Conflicted — Composite vs Technical"
        verdict_color = "#ef4444"
    elif earnings_conflict:
        verdict       = "caution"
        verdict_label = "⚠️ Caution — Earnings Within 7 Days"
        verdict_color = "#f59e0b"
    elif sentiment_conflict:
        verdict       = "mixed"
        verdict_label = "⚠️ Mixed — Negative News"
        verdict_color = "#f59e0b"
    elif conflicts:
        verdict       = "mixed"
        verdict_label = "⚠️ Mixed"
        verdict_color = "#f59e0b"
    elif not composite_available:
        # Composite signal isn't loaded for this ticker — can't issue
        # "Confirmed" regardless of held status. The "Verify" amber treatment
        # surfaces the data gap. Held vs non-held only changes the message
        # (the user knows whether they own it or not), not the verdict tier.
        verdict       = "unverified"
        verdict_label = (
            "🔍 Verify — Composite Signal Missing"
            if is_held else "🔍 Verify — Run Analysis First"
        )
        verdict_color = "#f59e0b"   # amber — action required, not a green light
    else:
        # Composite IS available, no conflicts above — full Confirmed regardless
        # of whether the user already holds the position. Previously this branch
        # only fired for is_held=True, leaving non-held picks at "Unverified"
        # even when the pre-fetched composite cleared the gate. That made every
        # new scanner pick look amber on the Brief and stored an "Unverified"
        # verdict in the recommendations log for things that were actually
        # fully confirmed.
        verdict       = "confirmed"
        verdict_label = "✅ Confirmed — All Signals Aligned"
        verdict_color = "#22c55e"

    # ── Central reconciliation — populates the explicit one-liner the UI ─────
    # surfaces render alongside the tier badge. Calling reconcile_signals here
    # keeps the resolution copy consistent everywhere: same wording in Daily
    # Briefing, Grow Today, Market Scanner, and Watchlist.
    _ticker_news = [n for n in (news_items or []) if str(n.get("ticker", "")).upper() == ticker]
    _news_compound = (
        sum(n.get("compound", 0) for n in _ticker_news) / len(_ticker_news)
        if _ticker_news else None
    )
    _reconciled = reconcile_signals(
        ticker=ticker,
        momentum_score=scan_score,
        momentum_signal=scan_sig,
        composite_score=comp_scr,
        composite_signal=comp_sig,
        is_held=is_held,
        earnings_days=earn_days,
        news_sentiment=_news_compound,
    )

    return {
        "verdict":             verdict,
        "verdict_label":       verdict_label,
        "verdict_color":       verdict_color,
        "verdict_one_liner":   _reconciled["one_liner"],
        "verdict_reconciled":  _reconciled,
        "agreed":              agreed,
        "conflicts":           conflicts,
        "layers_checked":      layers,
        "earn_days":           earn_days,
        "is_held":             is_held,
        "composite_available": composite_available,
    }


# ── Grow Today ───────────────────────────────────────────────────────────────

def _thesis(ticker: str, scanner_row: dict, is_sector_leader: bool) -> str:
    parts = []
    trend  = str(scanner_row.get("Trend", ""))
    mom_1m = _f(scanner_row.get("1M Momentum", 0))
    mom_3m = _f(scanner_row.get("3M Momentum", 0))
    rsi    = _f(scanner_row.get("RSI", 50))

    if "Strong Uptrend" in trend:
        parts.append("price above both 20d and 50d MAs — primary trend intact")
    elif "Uptrend" in trend:
        parts.append("price above 20d MA — trend building")

    if mom_1m > 10:
        parts.append(f"1M momentum +{mom_1m:.1f}% — strong recent acceleration")
    elif mom_1m > 5:
        parts.append(f"1M momentum +{mom_1m:.1f}%")

    if mom_3m > 20:
        parts.append(f"3M momentum +{mom_3m:.1f}% — sustained move")

    if 40 <= rsi <= 60:
        parts.append("RSI in ideal entry zone (not overbought)")
    elif rsi < 40:
        parts.append(f"RSI {rsi:.0f} — oversold, mean-reversion potential")
    elif rsi > 70:
        parts.append(f"RSI {rsi:.0f} — extended, wait for pullback")

    if is_sector_leader:
        parts.append("sector leading the market today — institutional tailwind")

    return (". ".join(parts).capitalize() + ".") if parts else "Momentum and trend aligned."


def _suggest_size(price: float, trend: str, portfolio_value: float) -> dict:
    """Estimate position size using a trend-based stop approximation."""
    stop_pct = 0.05 if "Strong" in trend else 0.07 if "Uptrend" in trend else 0.08
    stop     = price * (1 - stop_pct)
    risk_dollars  = portfolio_value * RISK_PCT_PER_TRADE
    risk_per_share = price - stop
    if risk_per_share <= 0:
        return {}
    shares     = max(1, int(risk_dollars / risk_per_share))
    total_cost = round(shares * price, 0)
    # Entry zone: buy anywhere from the stop buffer up to a small premium above current price
    entry_lo   = round(price * (1 - stop_pct * 0.40), 2)   # 40% of stop-distance below current
    entry_hi   = round(price * (1 + stop_pct * 0.15), 2)   # 15% of stop-distance above current
    return {
        "shares":      shares,
        "total_cost":  total_cost,
        "stop":        round(stop, 2),
        "stop_pct":    round(stop_pct * 100, 1),
        "port_pct":    round(total_cost / portfolio_value * 100, 1) if portfolio_value else 0,
        "risk_budget": round(risk_dollars, 0),
        "entry_lo":    entry_lo,
        "entry_hi":    entry_hi,
    }


def _grow_today(port_df, scanner_results, news_items, held_data, today,
                portfolio_value: float, market_context: dict,
                act_today: list | None = None,
                composites: dict | None = None,
                risk_recs: list | None = None,
                earnings_lookup: dict | None = None,
                macro_events: list | None = None,
                movers: list | None = None) -> dict:
    """
    Build growth-oriented action list calibrated to today's market tone.

    market_context keys: sp500_pct, nasdaq_pct, tone ('bull'|'bear'|'flat'),
                         leading_sectors [{"sector", "etf", "return_1w"}]
    act_today  : output of _act_today — tickers flagged here are excluded.
    composites : {ticker: load_all() result} for top scanner picks — used to
                 validate conviction using the full composite score so the label
                 reflects more than just momentum.
    risk_recs  : Risk Advisor recommendations — tickers flagged for trim (beta
                 or sharpe root_tickers) are suppressed from add-to-winner so
                 the briefing never tells the user to trim X and add to X.
    """
    tone        = market_context.get("tone", "flat")
    sp500_pct   = _f(market_context.get("sp500_pct", 0))
    nasdaq_pct  = _f(market_context.get("nasdaq_pct", 0))
    lead_secs   = market_context.get("leading_sectors", [])
    lead_names  = {ls.get("sector", "") for ls in lead_secs}
    held_tickers = set(port_df["Ticker"].tolist()) if port_df is not None else set()

    # ── Cross-reference Act Today to block conflicting picks ─────────────────
    # Any ticker already flagged in Act Today (sell, stop, risk reduce) must not
    # appear in Grow Today — contradicting signals in the same briefing is dangerous.
    _act_blocked: set = set()
    _act_risk_flags: list[str] = []
    for _ai in (act_today or []):
        if _ai.get("ticker"):
            _act_blocked.add(str(_ai["ticker"]).upper())
        _action = str(_ai.get("action", ""))
        if "RISK —" in _action:
            _act_risk_flags.append(_action.replace("RISK — ", ""))
    risk_banner = _act_risk_flags[:3] if _act_risk_flags else None

    # Risk Advisor trim targets — used to suppress add-to-winner conflicts.
    _trim_set = _trim_targets(risk_recs)

    # Drift-trim set: positions overweight enough that the equal-weight
    # rebalancer would flag them for trim. Adding to a position the rebalance
    # logic would trim creates a same-day contradiction. Default target =
    # 100/N (equal weight); trim threshold = target + 5pp.
    _drift_trim_set: set = set()
    if port_df is not None and not port_df.empty:
        _n_pos = len(port_df)
        if _n_pos > 0:
            _eq_target = 100.0 / _n_pos
            _trim_floor = _eq_target + 5.0   # TOLERANCE_WATCH from rebalancer.py
            for _, _row in port_df.iterrows():
                if _f(_row.get("Weight (%)"), 0) > _trim_floor:
                    _drift_trim_set.add(str(_row["Ticker"]).upper())

    # Macro gate — hard-suppress new picks in sectors with imminent HIGH-impact
    # macro events. Opening a fresh position into a known binary catalyst is
    # exactly the institutional anti-pattern this gate prevents.
    from stock_analyzer.macro_calendar import HIGH as _MC_HIGH, affected_sectors as _aff_sectors
    _macro_blocked_sectors: set = set()
    _macro_block_reasons:   dict = {}   # sector -> event description
    for _ev in (macro_events or []):
        if _ev.get("impact") != _MC_HIGH:
            continue
        _d = _days_until(_ev.get("date"), today)
        if _d is None or _d < 0 or _d > MACRO_IMMINENT_DAYS:
            continue
        _affected = _aff_sectors(_ev.get("category", ""))
        if "__ALL__" in _affected:
            # All sectors affected — block every new pick (rare but intentional)
            _macro_blocked_sectors = {"__ALL__"}
            _macro_block_reasons["__ALL__"] = (
                f"{_ev.get('event','')} ({_ev.get('date')}) — {_d}d away"
            )
            break
        for _s in _affected:
            _macro_blocked_sectors.add(_s)
            _macro_block_reasons.setdefault(_s, f"{_ev.get('event','')} ({_ev.get('date')}) — {_d}d away")

    # On bear days — no new entries, return protection message
    if tone == "bear":
        return {
            "tone":          "bear",
            "message":       (
                f"S&P 500 {sp500_pct:+.2f}% today — market in risk-off mode. "
                "Focus on protecting existing positions. "
                "Defer new entries until conditions stabilise."
            ),
            "new_picks":                  [],
            "add_positions":              [],
            "risk_blocked_adds":          [],
            "concentration_blocked_adds": [],
            "macro_blocked_picks":        [],
            "composite_skipped":          [],
            "composite_unavailable":      [],
            "deploy_note":                None,
            "risk_banner":                risk_banner,
        }

    # Score threshold: higher bar on flat days, standard on bull days
    min_score   = COMPOSITE_BUY if tone == "bull" else COMPOSITE_BUY_FLAT_DAY
    max_picks   = 3  if tone == "bull" else 1

    new_picks: list[dict] = []
    _confirmed_picks: list[dict] = []
    _unverified_picks: list[dict] = []
    macro_blocked_picks: list[dict] = []
    composite_skipped:  list[dict] = []
    # Picks where the composite fetch FAILED (load_all raised / not in cache).
    # Distinct from composite_skipped (where composite loaded but < BUY).
    # Kept out of new_picks entirely so we never surface a half-validated
    # recommendation; the Brief renders an aggregate banner with a Refresh
    # button so the user can retry the data load.
    composite_unavailable: list[dict] = []

    # Build ONE candidate pool from two sources so the user sees a single
    # "New Positions to Initiate" list regardless of where a ticker came from:
    #   (a) curated scanner picks — qualify on momentum Score ≥ min_score
    #   (b) movers — qualify on today's 1-day gain (applied upstream in
    #       scan_movers); NOT momentum-gated, since a fresh breakout may not
    #       show in 1M/3M momentum yet.
    # Both then face the SAME gates below (act-today block, macro suppression,
    # cross-ref verdict, composite ≥ BUY, sector diversity) and compete for the
    # SAME max_picks cap. This is what stops the flat-day contradiction where
    # the main section said "no new entries" while a separate Movers section
    # listed four. Movers are a SOURCE of candidates, not a parallel pipeline.
    def _sector_bonus(sector: str) -> int:
        return 5 if any(ls.get("sector", "") in str(sector) for ls in lead_secs) else 0

    candidate_rows: list[dict] = []
    if scanner_results is not None and not scanner_results.empty:
        _curated = scanner_results[
            (scanner_results["Score"] >= min_score) &
            (~scanner_results["Ticker"].isin(held_tickers))
        ]
        for _, r in _curated.iterrows():
            d = dict(r)
            d["_rank"]       = _f(d.get("Score", 0)) + _sector_bonus(d.get("Sector", ""))
            d["_is_mover"]   = False
            d["_day_change"] = None
            candidate_rows.append(d)

    _seen_ct = {str(d.get("Ticker", "")).upper() for d in candidate_rows}
    for m in (movers or []):
        mt = str(m.get("ticker", "")).upper()
        if not mt or mt in held_tickers or mt in _seen_ct:
            continue
        _sector = str(m.get("sector", ""))
        candidate_rows.append({
            "Ticker":       mt,
            "Price":        _f(m.get("price")),
            "Sector":       _sector,
            "Trend":        str(m.get("trend", "")),
            "Signal":       str(m.get("scanner_signal", "")),
            "Score":        _f(m.get("score")),          # momentum (may be modest)
            "RSI":          _f(m.get("rsi", 50)),
            "1M Momentum":  _f(m.get("mom_1m", 0)),
            "3M Momentum":  _f(m.get("mom_3m", 0)),
            # Rank movers by composite (their quality signal) + sector bonus so
            # they sort sensibly against momentum-ranked curated picks.
            "_rank":        _f(m.get("composite_score")) + _sector_bonus(_sector),
            "_is_mover":    True,
            "_day_change":  _f(m.get("day_change")),
        })
        _seen_ct.add(mt)

    if candidate_rows:
        # Wider pool so sector-diversity filtering has enough to draw from.
        candidate_rows.sort(key=lambda d: d.get("_rank", 0), reverse=True)
        candidate_rows = candidate_rows[: max_picks * 4]

        for row in candidate_rows:
            ticker   = str(row["Ticker"])
            price    = _f(row.get("Price", 0))
            sector   = str(row.get("Sector", ""))
            trend    = str(row.get("Trend", ""))
            is_leader = any(ls.get("sector","") in sector for ls in lead_secs)
            is_mover  = bool(row.get("_is_mover"))
            day_change = row.get("_day_change")

            # Skip if Act Today already has an action on this ticker
            if ticker in _act_blocked:
                continue

            # Macro gate — hard-suppress if sector has an imminent HIGH-impact
            # macro event. Opening into a known binary catalyst is the
            # anti-pattern; surface the suppression so the user knows.
            _macro_block = None
            if "__ALL__" in _macro_blocked_sectors:
                _macro_block = _macro_block_reasons.get("__ALL__")
            elif sector in _macro_blocked_sectors:
                _macro_block = _macro_block_reasons.get(sector)
            if _macro_block:
                macro_blocked_picks.append({
                    "ticker": ticker, "sector": sector,
                    "score":  _f(row.get("Score", 0)),
                    "reason": _macro_block,
                })
                continue

            xref = _cross_reference(ticker, row, port_df, news_items, held_data, today,
                                    earnings_lookup=earnings_lookup, composites=composites)

            # Skip hard conflicts; on flat days skip anything but confirmed/unverified
            if xref["verdict"] == "conflicted":
                continue
            if tone == "flat" and xref["verdict"] not in ("confirmed", "unverified"):
                continue

            sizing = _suggest_size(price, trend, portfolio_value) if price > 0 and portfolio_value > 0 else {}
            _base_thesis = _thesis(ticker, row, is_leader)
            # Movers lead with today's move (their entry trigger), then the
            # standard trend/momentum thesis.
            thesis = (
                f"Up {day_change:+.1f}% today — breakout outside your core universe. {_base_thesis}"
                if is_mover and day_change is not None else _base_thesis
            )

            # Validate conviction using full composite score when available.
            # Scanner score measures momentum only; composite includes fundamentals
            # and sentiment — a pick can score 100 on momentum but 63 composite.
            _comp_data       = (composites or {}).get(ticker, {})
            _composite_score = _f(_comp_data.get("total")) if _comp_data else None
            _composite_label = str((_comp_data.get("rec") or {}).get("label", "")) if _comp_data else ""

            # Exclude picks where composite is known and below the Buy threshold.
            # Scanner score measures momentum only — composite (technical + fundamental
            # + sentiment) is the authoritative signal. A 100/100 momentum score with
            # a sub-COMPOSITE_BUY composite is not a high-conviction entry.
            #
            # Record the rejection so the Brief can render a visible "Filtered Out"
            # bucket — silent drops leave the user wondering why a hot momentum
            # ticker isn't being recommended. This is the TSLA case: Momentum 90
            # but composite 48.8 Hold.
            if _composite_score is not None and _composite_score < COMPOSITE_BUY:
                composite_skipped.append({
                    "ticker":          ticker,
                    "sector":          sector,
                    "momentum_score":  _f(row.get("Score", 0)),
                    "composite_score": _composite_score,
                    "composite_label": _composite_label or "Hold",
                })
                continue

            # Composite fetch failed — pick is held out of new_picks entirely.
            # Surfacing a half-validated card next to high-conviction picks led to
            # user-visible contradictions (e.g. INTC presented as a new position
            # to initiate while Analysis page rated it Sell at 38.3). Per the
            # "decides, not informs" posture: if we can't validate, we don't
            # recommend. The Brief renders an aggregate "Pending Verification"
            # banner with a Refresh button so the user can retry the fetch.
            if _composite_score is None:
                composite_unavailable.append({
                    "ticker":         ticker,
                    "sector":         sector,
                    "momentum_score": _f(row.get("Score", 0)),
                })
                continue

            # Conviction tier: drives the label shown on the card.
            # "high" requires the same bar as Strong Buy (no looser tier here —
            # previously a custom 68 magic number existed that called scores
            # 68-74 "high conviction" even though they don't clear COMPOSITE_
            # STRONG_BUY=75; that asymmetry contradicted scoring.py's label).
            if _composite_score is None:
                conviction = "unverified"
            elif _composite_score >= COMPOSITE_HIGH_CONVICTION:
                conviction = "high"
            elif _composite_score >= COMPOSITE_BUY:
                conviction = "moderate"
            else:
                conviction = "low"

            pick = {
                "ticker":          ticker,
                "score":           _f(row.get("Score", 0)),    # momentum / scanner score
                "composite_score": _composite_score,           # full composite, or None
                "composite_label": _composite_label,
                "conviction":      conviction,
                "sector":          sector,
                "price":           price,
                "trend":           trend,
                "scanner_signal":  str(row.get("Signal", "")),
                "is_leader":       is_leader,
                "is_mover":        is_mover,
                "day_change":      day_change,
                "thesis":          thesis,
                "sizing":          sizing,
                "xref":            xref,
            }
            if xref["verdict"] == "confirmed":
                _confirmed_picks.append(pick)
            else:
                _unverified_picks.append(pick)

        # On flat days, confirmed picks take priority over unverified.
        # Enforce 1-per-sector so all slots don't land in the same sector.
        _seen_sectors: set[str] = set()
        for _pick in _confirmed_picks + _unverified_picks:
            _ps = _pick.get("sector", "")
            if _ps and _ps in _seen_sectors:
                continue
            new_picks.append(_pick)
            if _ps:
                _seen_sectors.add(_ps)
            if len(new_picks) >= max_picks:
                break

    # Add-to-winner: held Strong Buy, Score ≥ COMPOSITE_BUY (65), Gap ≥ 8%
    # — only on bull days. Composite bar aligned with new-pick threshold
    # (was 68; raised the bar on adds and lowered the bar on new picks created
    # an asymmetric system where it was easier to start a position than to add
    # to one you'd already vetted — backwards from "press your winners.")
    add_positions:     list[dict] = []
    risk_blocked_adds: list[dict] = []
    concentration_blocked_adds: list[dict] = []
    if tone == "bull" and port_df is not None:
        for _, row in port_df.iterrows():
            sig = str(row.get("Signal", ""))
            gap = _f(row.get("Gap to Stop (%)"), 0)
            scr = _f(row.get("Score"), 0)
            if "Strong Buy" in sig and scr >= COMPOSITE_BUY and gap >= ADD_WINNER_MIN_GAP_PCT:
                ticker  = str(row["Ticker"])
                # Skip if Act Today already flags this ticker for action
                if ticker in _act_blocked:
                    continue
                # Skip — and record — if Risk Advisor is recommending trim on this ticker
                _trim_info = _trim_set.get(ticker.upper())
                if _trim_info:
                    risk_blocked_adds.append({
                        "ticker":   ticker,
                        "reason":   _trim_info["reason"],
                        "title":    _trim_info["title"],
                        "priority": _trim_info["priority"],
                        "score":    scr,
                        "pnl_pct":  _f(row.get("P&L (%)")),
                        "gap":      gap,
                    })
                    continue
                # Single-name ceiling — hard suppress if current weight ≥ ceiling.
                # No matter how strong the signal, additional concentration here
                # creates asymmetric idiosyncratic risk that the institutional
                # framework caps at SINGLE_NAME_CEILING.
                _cur_wt = _f(row.get("Weight (%)"), 0)
                if _cur_wt >= SINGLE_NAME_CEILING:
                    concentration_blocked_adds.append({
                        "ticker":    ticker,
                        "weight":    _cur_wt,
                        "score":     scr,
                        "pnl_pct":   _f(row.get("P&L (%)")),
                        "gap":       gap,
                        "reason":    (
                            f"Position already at {_cur_wt:.1f}% of portfolio "
                            f"(≥ {SINGLE_NAME_CEILING:.0f}% single-name ceiling). "
                            "Trim to target before adding."
                        ),
                    })
                    continue
                # Drift-trim conflict — Rebalancer would trim this position to
                # target; the briefing must not simultaneously recommend adding.
                if ticker.upper() in _drift_trim_set:
                    concentration_blocked_adds.append({
                        "ticker":    ticker,
                        "weight":    _cur_wt,
                        "score":     scr,
                        "pnl_pct":   _f(row.get("P&L (%)")),
                        "gap":       gap,
                        "reason":    (
                            f"Position at {_cur_wt:.1f}% — overweight vs equal-weight target "
                            f"(would be flagged for drift-trim by Rebalancer). "
                            "Don't add to a position you'd trim."
                        ),
                    })
                    continue
                price   = _f(row.get("Price", 0))
                sector  = str(row.get("Sector", ""))
                is_lead = any(ls.get("sector", "") in sector for ls in lead_secs)
                sizing  = _suggest_size(price, "Strong Uptrend", portfolio_value) if price > 0 else {}
                add_positions.append({
                    "ticker":    ticker,
                    "score":     scr,
                    "signal":    sig,
                    "pnl_pct":   _f(row.get("P&L (%)")),
                    "gap":       gap,
                    "sector":    sector,
                    "is_leader": is_lead,
                    "thesis":    (
                        f"Already profitable (+{_f(row.get('P&L (%)')):+.1f}%), "
                        f"{gap:.1f}% above stop — trend intact, adds within existing position."
                        + (" Sector leading today." if is_lead else "")
                    ),
                    "sizing":    sizing,
                })
        add_positions.sort(key=lambda x: (-x["score"], -x["gap"]))

    # Capital deployment note
    deploy_note = None
    if portfolio_value > 0 and (new_picks or add_positions):
        n_trades = len(new_picks) + len(add_positions)
        deploy   = portfolio_value * RISK_PCT_PER_TRADE * n_trades
        _risk_pct_str = f"{RISK_PCT_PER_TRADE * 100:.1f}%"
        if _act_risk_flags:
            deploy_note = (
                f"⚠️ Resolve Act Today risk alerts before deploying. "
                f"If proceeding: {_risk_pct_str} risk per trade across {n_trades} setup{'s' if n_trades > 1 else ''} "
                f"= ~${deploy:,.0f}."
            )
        else:
            deploy_note = (
                f"At {_risk_pct_str} risk per trade across {n_trades} setup{'s' if n_trades > 1 else ''}, "
                f"consider deploying ~${deploy:,.0f} today."
            )

    return {
        "tone":                       tone,
        "message":                    None,
        "new_picks":                  new_picks,
        "add_positions":              add_positions,
        "risk_blocked_adds":          risk_blocked_adds,
        "concentration_blocked_adds": concentration_blocked_adds,
        "macro_blocked_picks":        macro_blocked_picks,
        "composite_skipped":          composite_skipped,
        "composite_unavailable":      composite_unavailable,
        "deploy_note":                deploy_note,
        "risk_banner":                risk_banner,
        "sp500_pct":                  sp500_pct,
        "nasdaq_pct":                 nasdaq_pct,
        "leading_sectors":            lead_secs,
    }


# ── Act Today ─────────────────────────────────────────────────────────────────

def _act_today(port_df, alert_list, risk_recs, news_items, macro_events, today) -> list[dict]:
    """
    Build the Act Today list. Each item carries a structured directive so the
    UI renders a concrete ACT/Why/Trigger block (matching Review Before Close)
    rather than a prose one-liner.

    item shape:
      priority  : "critical" | "high"
      icon, ticker, action (headline)
      kind      : "stop_breach" | "sell_signal" | "critical_news" | "macro" | "risk"
      directive : the concrete action line (what to do)
      why       : 1-line rationale
      trigger   : escalation / confirmation condition
      risk_flags: [{title, recommendation, problem}] — populated for kind="risk";
                  multiple flags on one ticker are merged into a single card
      weight, pnl_pct

    After building the flat list, items are CONSOLIDATED per-ticker so a ticker
    never appears in more than one card. Rule (user-chosen): a mechanical exit
    (stop breach / Sell signal) WINS — it suppresses any risk-advisor trim on
    the same ticker, because if you're exiting, a "trim 50% for beta" rec is
    moot. Multiple risk flags on one non-exiting ticker merge into one card.
    """
    items: list[dict] = []

    # 1 — Stop-loss breaches (Gap to Stop ≤ 0)
    for _, row in port_df.iterrows():
        gap = _f(row.get("Gap to Stop (%)"), None)
        if gap is None:
            continue
        if gap <= 0:
            _shares = int(_f(row.get("Shares")))
            items.append({
                "priority": "critical",
                "icon":     "🛑",
                "ticker":   row["Ticker"],
                "kind":     "stop_breach",
                "action":   "SELL — Stop Breached",
                "directive": (
                    f"Sell all {_shares} shares at next open — mechanical stop rule."
                ),
                "why": (
                    f"Price ${_f(row.get('Price')):.2f} closed below the "
                    f"{row.get('Stop Type','')} stop ${_f(row.get('Stop')):.2f} "
                    f"(gap {gap:+.1f}%)."
                ),
                "trigger": "Already breached — this is the exit signal, not a watch.",
                "weight":  _f(row.get("Weight (%)")),
                "pnl_pct": _f(row.get("P&L (%)")),
            })

    # 2 — Sell / Avoid signals on held positions
    sell_words = ("Sell", "Avoid", "Weak Hold")
    for _, row in port_df.iterrows():
        sig = str(row.get("Signal", ""))
        if any(w in sig for w in sell_words) and row["Ticker"] not in {i["ticker"] for i in items}:
            items.append({
                "priority": "high",
                "icon":     "📉",
                "ticker":   row["Ticker"],
                "kind":     "sell_signal",
                "action":   f"REVIEW — Signal: {sig}",
                "directive": (
                    f"Reduce or exit — composite signal has shifted to {sig}."
                ),
                "why": (
                    f"Composite score {_f(row.get('Score')):.0f}/100 in the Sell zone. "
                    f"P&L {_f(row.get('P&L (%)')):+.1f}%, weight {_f(row.get('Weight (%)')):.1f}%."
                ),
                "trigger": "Confirm before close; if price is also below stop, exit fully.",
                "weight":  _f(row.get("Weight (%)")),
                "pnl_pct": _f(row.get("P&L (%)")),
            })

    # 3 — Critical news on held positions (compound ≤ -0.25, tier ≤ 2)
    held_tickers = set(port_df["Ticker"].tolist())
    for item in (news_items or []):
        ticker = str(item.get("ticker", "")).upper()
        if (ticker in held_tickers
                and item.get("compound", 0) <= NEWS_SENTIMENT_CRITICAL
                and item.get("tier", 3) <= 2):
            if ticker not in {i["ticker"] for i in items}:
                pm = port_df[port_df["Ticker"] == ticker]
                row = pm.iloc[0] if not pm.empty else {}
                items.append({
                    "priority": "high",
                    "icon":     "🚨",
                    "ticker":   ticker,
                    "kind":     "critical_news",
                    "action":   "MONITOR — Critical News",
                    "directive": (
                        "Hold for now, but tighten your stop and re-evaluate the thesis "
                        "after the news is confirmed."
                    ),
                    "why": (
                        f"One tier-{item.get('tier',3)} headline at sentiment "
                        f"{item.get('compound',0):+.2f}: \"{item.get('headline','news alert')[:80]}\" "
                        "— material, but a single headline isn't a mechanical sell."
                    ),
                    "trigger": (
                        "A second negative headline OR price −3% intraday → exit."
                    ),
                    "weight":  _f(row.get("Weight (%)") if hasattr(row, "get") else 0),
                    "pnl_pct": _f(row.get("P&L (%)") if hasattr(row, "get") else 0),
                })

    # 4 — Today's HIGH-impact macro events
    from stock_analyzer.macro_calendar import HIGH as MC_HIGH
    today_macro = [e for e in (macro_events or []) if e.get("date") == today and e.get("impact") == MC_HIGH]
    for ev in today_macro:
        _affected = ", ".join(ev.get("affected_tickers", [])[:5]) or "broad market"
        items.append({
            "priority": "high",
            "icon":     "🌐",
            "ticker":   None,
            "kind":     "macro",
            "action":   f"MACRO — {ev.get('event', 'Economic Event')}",
            "directive": (
                "Hold through the print — no new entries in affected names until it clears."
            ),
            "why": (
                f"{ev.get('category','Economic')} release today. Affected: {_affected}."
            ),
            "trigger": (
                ev.get("playbook_note")
                or "Hawkish surprise → existing stops protect; dovish → reconsider adds."
            ),
            "weight":  None,
            "pnl_pct": None,
        })

    # 5 — HIGH-priority risk advisor flags
    for rec in (risk_recs or []):
        if rec.get("priority") != "HIGH":
            continue
        rt = rec.get("root_tickers", [])
        items.append({
            "priority": "high",
            "icon":     "⚠️",
            "ticker":   rt[0]["ticker"] if rt else None,
            "kind":     "risk",
            "action":   f"RISK — {rec.get('title','Risk Alert')}",
            # risk recs already carry rich directive text in `recommendation`;
            # keep it as a flag entry so multiple flags on one ticker can merge.
            "risk_flags": [{
                "title":          rec.get("title", "Risk Alert"),
                "recommendation": rec.get("recommendation", rec.get("problem", "")),
                "problem":        rec.get("problem", ""),
            }],
            "weight":  None,
            "pnl_pct": None,
        })

    return _consolidate_act_today(items, port_df)


# Kinds that represent a mechanical exit decision — these suppress softer
# advisories (risk trims, news monitors) on the same ticker.
_MECHANICAL_KINDS = {"stop_breach", "sell_signal"}


def _consolidate_act_today(items: list[dict], port_df) -> list[dict]:
    """Collapse Act Today items so each ticker appears at most once.

    - Ticker-less items (macro) pass through unchanged.
    - For each ticker: if a mechanical-exit item exists, emit only the
      highest-priority mechanical one (drop risk + news — moot if exiting).
    - Otherwise merge: the highest-priority item becomes the card; all
      risk_flags across the group are gathered onto it so a ticker with
      two risk recs (e.g. beta + volatility) renders as one card.
    """
    passthrough = [it for it in items if not it.get("ticker")]
    by_ticker: dict[str, list[dict]] = {}
    for it in items:
        t = it.get("ticker")
        if t:
            by_ticker.setdefault(str(t).upper(), []).append(it)

    _pri_rank = {"critical": 0, "high": 1}
    consolidated: list[dict] = []
    for ticker, group in by_ticker.items():
        # Pull weight/pnl from port_df so merged cards always have them.
        pm = port_df[port_df["Ticker"] == ticker] if port_df is not None else None
        w  = _f(pm["Weight (%)"].iloc[0]) if pm is not None and not pm.empty else None
        p  = _f(pm["P&L (%)"].iloc[0]) if pm is not None and not pm.empty else None

        mechanical = [it for it in group if it.get("kind") in _MECHANICAL_KINDS]
        if mechanical:
            # Mechanical wins. Highest priority (critical before high), and if
            # tied, stop_breach before sell_signal.
            mechanical.sort(key=lambda x: (
                _pri_rank.get(x["priority"], 9),
                0 if x.get("kind") == "stop_breach" else 1,
            ))
            primary = dict(mechanical[0])
            primary["weight"]  = w if w is not None else primary.get("weight")
            primary["pnl_pct"] = p if p is not None else primary.get("pnl_pct")
            consolidated.append(primary)
            continue

        # No mechanical exit — merge softer advisories into one card.
        group.sort(key=lambda x: _pri_rank.get(x["priority"], 9))
        primary = dict(group[0])
        # Gather all risk flags across the group (could be multiple recs).
        all_flags: list[dict] = []
        for it in group:
            all_flags.extend(it.get("risk_flags", []) or [])
        if all_flags:
            primary["risk_flags"] = all_flags
            # If the primary is a risk card, reflect the flag count in the header.
            if primary.get("kind") == "risk" and len(all_flags) > 1:
                primary["action"] = f"RISK — {len(all_flags)} flags on {ticker}"
        primary["weight"]  = w if w is not None else primary.get("weight")
        primary["pnl_pct"] = p if p is not None else primary.get("pnl_pct")
        consolidated.append(primary)

    # Back-compat: synthesize a flat `reason` string on every item from the
    # structured fields. Older consumers (quick_research _portfolio_bullet,
    # premarket movers tooltip) read item["reason"] directly — keep it
    # populated so they don't KeyError on the new structured shape.
    def _synth_reason(it: dict) -> str:
        if it.get("reason"):
            return it["reason"]
        parts = []
        if it.get("directive"):
            parts.append(it["directive"])
        elif it.get("risk_flags"):
            parts.append(" ".join(f.get("recommendation", "") for f in it["risk_flags"]))
        if it.get("why"):
            parts.append(it["why"])
        return " ".join(p for p in parts if p).strip()

    out = passthrough + consolidated
    for it in out:
        it["reason"] = _synth_reason(it)

    # Critical first, then by weight desc (None weights last).
    out.sort(key=lambda x: (
        0 if x["priority"] == "critical" else 1,
        -(x.get("weight") or 0),
    ))
    return out


# ── Buy Candidates ─────────────────────────────────────────────────────────────

def _buy_candidates(port_df, scanner_results, news_items, held_data, today,
                    act_today: list | None = None,
                    risk_recs: list | None = None,
                    earnings_lookup: dict | None = None,
                    composites: dict | None = None) -> list[dict]:
    """
    Build buy candidate list with multi-signal confidence verdict for each pick.
    act_today: output of _act_today — tickers already flagged are excluded.
    risk_recs: Risk Advisor recs — tickers flagged for trim are suppressed from
               the add-to-winner block to avoid same-ticker capital conflicts.
    """
    items: list[dict] = []
    held_tickers = set(port_df["Ticker"].tolist())

    # Block any ticker already flagged in Act Today
    _act_blocked: set = set()
    for _ai in (act_today or []):
        if _ai.get("ticker"):
            _act_blocked.add(str(_ai["ticker"]).upper())

    # Risk Advisor trim targets — suppress same-ticker add-to-winner conflicts.
    _trim_set = _trim_targets(risk_recs)

    # Drift-trim positions — same suppression as _grow_today
    _drift_trim_set: set = set()
    if port_df is not None and not port_df.empty:
        _n_pos = len(port_df)
        if _n_pos > 0:
            _eq_target = 100.0 / _n_pos
            _trim_floor = _eq_target + 5.0
            for _, _row in port_df.iterrows():
                if _f(_row.get("Weight (%)"), 0) > _trim_floor:
                    _drift_trim_set.add(str(_row["Ticker"]).upper())

    # 1 — Scanner picks not in portfolio (Score ≥ COMPOSITE_BUY)
    if scanner_results is not None and not scanner_results.empty:
        top_picks = scanner_results[
            (scanner_results["Score"] >= COMPOSITE_BUY) &
            (~scanner_results["Ticker"].isin(held_tickers)) &
            (~scanner_results["Ticker"].isin(_act_blocked))
        ].copy().sort_values("Score", ascending=False).head(5)

        for _, row in top_picks.iterrows():
            ticker = str(row["Ticker"])
            xref   = _cross_reference(ticker, row.to_dict(), port_df, news_items, held_data, today,
                                      composites=composites)
            items.append({
                "type":           "new_pick",
                "icon":           "🆕",
                "ticker":         ticker,
                "action":         "BUY — Scanner Pick",
                "score":          _f(row.get("Score")),
                "scanner_signal": str(row.get("Signal", "")),
                "sector":         str(row.get("Sector", "—")),
                "rsi":            _f(row.get("RSI")),
                "mom_1m":         _f(row.get("1M Momentum")),
                "trend":          str(row.get("Trend", "")),
                "xref":           xref,
            })

    # 2 — Add-to-winner: held, Strong Buy composite, Score ≥ COMPOSITE_BUY (65),
    # Gap ≥ 8%, and current weight below the single-name ceiling.
    for _, row in port_df.iterrows():
        sig = str(row.get("Signal", ""))
        gap = _f(row.get("Gap to Stop (%)"), 0)
        scr = _f(row.get("Score"), 0)
        if "Strong Buy" in sig and scr >= COMPOSITE_BUY and gap >= ADD_WINNER_MIN_GAP_PCT:
            ticker = str(row["Ticker"])
            # Skip if Act Today already flags this ticker for any action
            if ticker in _act_blocked:
                continue
            # Skip if Risk Advisor is recommending trim — same-ticker conflict
            if ticker.upper() in _trim_set:
                continue
            # Single-name ceiling — hard suppress; concentration risk overrides signal
            if _f(row.get("Weight (%)"), 0) >= SINGLE_NAME_CEILING:
                continue
            # Drift-trim conflict — position is drift-overweight; don't add
            if ticker.upper() in _drift_trim_set:
                continue
            # Build a minimal scanner_row from portfolio data for cross-reference
            _synthetic = {
                "Signal": sig, "Score": scr,
                "RSI": 0, "1M Momentum": 0, "Trend": sig,
            }
            xref = _cross_reference(ticker, _synthetic, port_df, news_items, held_data, today,
                                    earnings_lookup=earnings_lookup, composites=composites)
            items.append({
                "type":           "add_winner",
                "icon":           "➕",
                "ticker":         ticker,
                "action":         "ADD — Winning Position",
                "score":          scr,
                "scanner_signal": sig,
                "sector":         str(row.get("Sector", "—")),
                "rsi":            0,
                "mom_1m":         _f(row.get("1M Momentum", 0)),
                "trend":          sig,
                "gap_to_stop":    gap,
                "pnl_pct":        _f(row.get("P&L (%)")),
                "xref":           xref,
            })

    _verdict_order = {"confirmed": 0, "mixed": 1, "caution": 1, "unverified": 2, "conflicted": 3}
    items.sort(key=lambda x: (_verdict_order.get(x["xref"]["verdict"], 2), -x["score"]))
    return items


# ── Review Before Close ────────────────────────────────────────────────────────

def _review_list(port_df, news_items, macro_events, held_data, today,
                 portfolio_value: float = 0.0) -> list[dict]:
    """
    Build the Review Before Close list.

    Each item carries:
      headline — one-line description of the trigger
      action   — structured directive dict (type + quantitative fields)
      why      — 1-line rationale (refs numbers from the trigger)
      trigger  — what condition tomorrow escalates this to ACT

    The renderer (app.py) formats action into a display string and, for
    weak-large items, appends "reallocate to" alternatives sourced from
    the brief's new_picks / add_positions (two complementary buckets).

    portfolio_value is required to compute dollar amounts for trim
    directives; passed in by build_daily_briefing.
    """
    items: list[dict] = []
    held_tickers = set(port_df["Ticker"].tolist())
    from stock_analyzer.macro_calendar import HIGH as MC_HIGH, affected_sectors as _aff_sectors

    # 1 — Approaching stop (0–8% gap)
    # Action policy:
    #   - If gap critical (<3%) AND P&L < profit-lock threshold: just tighten stop.
    #   - If gap critical AND P&L ≥ profit-lock threshold: trim partial AND tighten.
    #   - If gap 3-8%: tighten stop only (still room to run).
    # New stop = price − STOP_TIGHTEN_ATR_MULT × ATR.
    #
    # Suppression: if the current active stop (manual override or ratchet) is
    # already at or tighter than the recommended new_stop, don't re-fire — the
    # user has already actioned (manual_stops table) or the ratchet has already
    # provided equivalent protection. Closes the loop the user's "Mark Done"
    # button opens.
    for _, row in port_df.iterrows():
        gap = _f(row.get("Gap to Stop (%)"), None)
        if gap is None:
            continue
        if not (0 < gap <= APPROACHING_STOP_GAP_PCT):
            continue
        ticker = str(row["Ticker"])
        price  = _f(row.get("Price"))
        shares = int(_f(row.get("Shares")))
        pnl_pct = _f(row.get("P&L (%)"))
        weight  = _f(row.get("Weight (%)"))
        current_stop = _f(row.get("Stop"))
        atr_val = _f((held_data or {}).get(ticker, {}).get("atr")) if held_data else 0.0
        new_stop = round(price - STOP_TIGHTEN_ATR_MULT * atr_val, 2) if (price and atr_val) else None
        # Suppress if current stop already meets the recommendation. A 1-cent
        # buffer absorbs floating-point rounding so a manual stop set at exactly
        # the recommended level doesn't keep re-firing on rounding noise.
        if new_stop is not None and current_stop >= new_stop - 0.01:
            continue
        is_critical = gap <= 3.0
        lock_profits = pnl_pct >= STOP_PROFIT_LOCK_PNL_PCT
        if is_critical and lock_profits:
            trim_shares = max(1, int(round(shares * STOP_PROFIT_LOCK_TRIM_PCT / 100)))
            trim_dollars = round(trim_shares * price, 0)
            action = {
                "type":         "TRIM_AND_TIGHTEN",
                "trim_shares":  trim_shares,
                "trim_dollars": trim_dollars,
                "trim_pct":     STOP_PROFIT_LOCK_TRIM_PCT,
                "new_stop":     new_stop,
            }
            why = (
                f"Gap critical ({gap:.1f}% < 3%) and P&L +{pnl_pct:.1f}% above "
                f"{STOP_PROFIT_LOCK_PNL_PCT:.0f}% — lock part of the gain, tighten stop on the rest."
            )
        else:
            action = {"type": "TIGHTEN_ONLY", "new_stop": new_stop}
            why = (
                f"Gap {gap:.1f}% above stop; P&L {pnl_pct:+.1f}%. "
                "Tighten stop to protect downside; position still has room."
            )
        items.append({
            "priority": "medium" if is_critical else "low",
            "icon":     "📍",
            "ticker":   ticker,
            "headline": f"gap {gap:.1f}% above {row.get('Stop Type','ATR')} stop, P&L {pnl_pct:+.1f}%",
            "action":   action,
            "why":      why,
            "trigger":  "Stop break = full exit next open.",
            "weight":   weight,
        })

    # 2 — Earnings within 7 days
    # Action policy:
    #   - Weight > EARNINGS_OVERWEIGHT_TRIM_PCT (12%) → trim to TRIM_TO_PCT (10%).
    #   - Weight ≤ threshold → WATCH; sizing already conservative for the binary risk.
    seen_earn: set = set()
    for ticker, data in (held_data or {}).items():
        earn_date = (data or {}).get("earnings")
        if not earn_date or ticker in seen_earn:
            continue
        days = _days_until(earn_date, today)
        if days is None or not (0 <= days <= 7):
            continue
        seen_earn.add(ticker)
        pm = port_df[port_df["Ticker"] == ticker]
        if pm.empty:
            continue
        weight = _f(pm["Weight (%)"].iloc[0])
        shares = int(_f(pm["Shares"].iloc[0]))
        price  = _f(pm["Price"].iloc[0])
        label  = "TODAY" if days == 0 else f"in {days}d"
        if weight > EARNINGS_OVERWEIGHT_TRIM_PCT and portfolio_value > 0:
            target_value = portfolio_value * (EARNINGS_OVERWEIGHT_TRIM_TO_PCT / 100)
            current_value = portfolio_value * (weight / 100)
            trim_dollars = round(current_value - target_value, 0)
            trim_shares  = max(1, int(round(trim_dollars / price))) if price > 0 else 0
            action = {
                "type":           "TRIM_TO_TARGET",
                "trim_shares":    trim_shares,
                "trim_dollars":   trim_dollars,
                "from_weight":    weight,
                "target_weight":  EARNINGS_OVERWEIGHT_TRIM_TO_PCT,
                "reason_key":     "earnings",
            }
            why = (
                f"Weight {weight:.1f}% × binary earnings risk above the "
                f"{EARNINGS_OVERWEIGHT_TRIM_PCT:.0f}% overweight threshold. "
                "Trim to size-down the binary event exposure."
            )
            trigger = "Beat → re-enter post-print if composite holds; Miss → existing stop protects rest."
        else:
            action = {"type": "WATCH"}
            why = (
                f"Weight {weight:.1f}% within tolerance "
                f"(≤ {EARNINGS_OVERWEIGHT_TRIM_PCT:.0f}% overweight threshold). "
                "Existing sizing already conservative for the binary risk."
            )
            trigger = "Surprise miss + price gap-down → existing stop protects; no pre-event action needed."
        items.append({
            "priority": "medium" if days <= 3 else "low",
            "icon":     "📅",
            "ticker":   ticker,
            "headline": f"earnings {label} ({earn_date}), weight {weight:.1f}%",
            "action":   action,
            "why":      why,
            "trigger":  trigger,
            "weight":   weight,
        })

    # 3 — Weak large positions (weight ≥ LARGE_POSITION_WEIGHT_PCT, Score < WEAK_CONVICTION_SCORE)
    # Action: trim to WEAK_LARGE_TRIM_TO_PCT (below re-flag threshold).
    # Alternatives are wired in by the renderer (app.py has access to new_picks/add_positions).
    for _, row in port_df.iterrows():
        weight = _f(row.get("Weight (%)"))
        score  = _f(row.get("Score"))
        if not (weight >= LARGE_POSITION_WEIGHT_PCT and score < WEAK_CONVICTION_SCORE):
            continue
        ticker = str(row["Ticker"])
        shares = int(_f(row.get("Shares")))
        price  = _f(row.get("Price"))
        if portfolio_value > 0 and price > 0:
            target_value = portfolio_value * (WEAK_LARGE_TRIM_TO_PCT / 100)
            current_value = portfolio_value * (weight / 100)
            trim_dollars = round(current_value - target_value, 0)
            trim_shares  = max(1, int(round(trim_dollars / price)))
        else:
            trim_dollars, trim_shares = 0, 0
        items.append({
            "priority": "medium",
            "icon":     "🔍",
            "ticker":   ticker,
            "headline": f"largest weak holding — {weight:.1f}% weight, score {score:.0f}/100",
            "action":   {
                "type":          "TRIM_TO_TARGET",
                "trim_shares":   trim_shares,
                "trim_dollars":  trim_dollars,
                "from_weight":   weight,
                "target_weight": WEAK_LARGE_TRIM_TO_PCT,
                "reason_key":    "weak_large",
            },
            "why": (
                f"Weight {weight:.1f}% ≥ {LARGE_POSITION_WEIGHT_PCT:.0f}% AND score "
                f"{score:.0f} < {WEAK_CONVICTION_SCORE} — largest position with weakest "
                "conviction. Free capital for higher-conviction names."
            ),
            "trigger": "Already flagged — act before close, don't wait.",
            "weight":  weight,
        })

    # 4 — Warning news on held positions (NEWS_SENTIMENT_CRITICAL < compound ≤ NEWS_SENTIMENT_WARN)
    # Always WATCH — one negative headline below critical threshold doesn't warrant a
    # quantitative action. Trigger condition tells the user what would escalate.
    warned: set = set()
    for item in (news_items or []):
        ticker = str(item.get("ticker", "")).upper()
        sent   = item.get("compound", 0)
        if (ticker in held_tickers
                and NEWS_SENTIMENT_CRITICAL < sent <= NEWS_SENTIMENT_WARN
                and ticker not in warned):
            warned.add(ticker)
            headline_text = item.get("headline", "news")[:80]
            items.append({
                "priority": "low",
                "icon":     "📰",
                "ticker":   ticker,
                "headline": f"negative headline (sentiment {sent:+.2f}): \"{headline_text}\"",
                "action":   {"type": "WATCH"},
                "why":      (
                    f"One negative headline (sentiment {sent:+.2f}), below the "
                    f"critical threshold ({NEWS_SENTIMENT_CRITICAL:+.2f}). Insufficient "
                    "signal alone — wait for confirmation."
                ),
                "trigger":  (
                    f"Second negative headline OR sentiment drops below {NEWS_SENTIMENT_CRITICAL:+.2f} "
                    "→ tighten stop on this position."
                ),
                "weight":   0,
            })

    # 5 — Upcoming macro events (1–3 days)
    # Action policy:
    #   - Compute user's exposure to affected sectors from port_df.
    #   - If exposure > MACRO_AFFECTED_TRIM_THRESHOLD_PCT → recommend trimming
    #     the lowest-conviction holding in the affected sectors by
    #     MACRO_AFFECTED_TRIM_REDUCTION_PP percentage points of portfolio.
    #   - Else → WATCH (exposure within tolerance).
    for ev in (macro_events or []):
        ev_date = ev.get("date")
        if not ev_date or ev.get("impact") != MC_HIGH:
            continue
        days = _days_until(ev_date, today)
        if days is None or not (1 <= days <= 3):
            continue

        # Compute user's exposure to the event's affected sectors.
        ev_affected_sectors = set(_aff_sectors(ev.get("category", "")))
        if "__ALL__" in ev_affected_sectors:
            # Event affects everything — exposure check is "all your equity."
            exposure_pct = 100.0
            sector_rows  = port_df.copy()
        else:
            sector_rows  = port_df[port_df["Sector"].isin(ev_affected_sectors)] if not port_df.empty else port_df
            exposure_pct = _f(sector_rows["Weight (%)"].sum()) if not sector_rows.empty else 0.0

        affected_tickers_str = ", ".join(ev.get("affected_tickers", [])[:4]) or "macro-sensitive"
        playbook = ev.get("playbook_note") or ""

        if exposure_pct > MACRO_AFFECTED_TRIM_THRESHOLD_PCT and not sector_rows.empty and portfolio_value > 0:
            # Find lowest-conviction-score holding in the affected sectors.
            weakest = sector_rows.sort_values("Score", ascending=True).iloc[0]
            weak_ticker = str(weakest["Ticker"])
            weak_score  = _f(weakest["Score"])
            weak_weight = _f(weakest["Weight (%)"])
            weak_price  = _f(weakest["Price"])
            # Trim by MACRO_AFFECTED_TRIM_REDUCTION_PP percentage points of portfolio
            # — but cap at the position's own weight so we don't try to trim more
            # than the position holds.
            reduction_pp = min(MACRO_AFFECTED_TRIM_REDUCTION_PP, weak_weight)
            trim_dollars = round(portfolio_value * (reduction_pp / 100), 0)
            trim_shares  = max(1, int(round(trim_dollars / weak_price))) if weak_price > 0 else 0
            new_exposure = max(0.0, exposure_pct - reduction_pp)
            action = {
                "type":           "PROTECTIVE_TRIM",
                "trim_ticker":    weak_ticker,
                "trim_shares":    trim_shares,
                "trim_dollars":   trim_dollars,
                "from_exposure":  exposure_pct,
                "to_exposure":    new_exposure,
                "reduction_pp":   reduction_pp,
                "weakest_score":  weak_score,
            }
            why = (
                f"Affected-sector exposure {exposure_pct:.1f}% > "
                f"{MACRO_AFFECTED_TRIM_THRESHOLD_PCT:.0f}% trim threshold. "
                f"Trimming weakest holding first preserves higher-conviction names."
            )
        else:
            action = {
                "type":          "WATCH",
                "from_exposure": exposure_pct,
                "threshold":     MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
            }
            why = (
                f"Affected-sector exposure {exposure_pct:.1f}% ≤ "
                f"{MACRO_AFFECTED_TRIM_THRESHOLD_PCT:.0f}% trim threshold — within tolerance, "
                "no pre-event trim warranted."
            )

        items.append({
            "priority": "low",
            "icon":     "🌐",
            "ticker":   None,
            "event":    ev.get("event", "Macro event"),
            "headline": (
                f"{ev.get('event','Macro event')} in {days}d ({ev_date}) · "
                f"affected: {affected_tickers_str}"
            ),
            "action":   action,
            "why":      why,
            "trigger":  (
                playbook
                if playbook
                else f"Hawkish surprise → existing stops protect; dovish surprise → reconsider add."
            ),
            "weight":   None,
        })

    pri_order = {"medium": 0, "low": 1}
    items.sort(key=lambda x: (pri_order.get(x.get("priority","low"), 1), -(x.get("weight") or 0)))
    return items


# ── Public API ─────────────────────────────────────────────────────────────────

def build_daily_briefing(
    port_df,
    alert_list:      list,
    risk_recs:       list,
    news_items:      list,
    macro_events:    list,
    held_data:       dict,
    scanner_results,
    portfolio_value: float,
    today:           date,
    market_context:  dict | None = None,
    grow_composites: dict | None = None,
    movers:          list | None = None,
) -> dict:
    """
    Build a Start-Your-Day briefing synthesising all available intelligence.

    grow_composites: optional dict {ticker: load_all() result} pre-fetched for top
                     scanner picks so _grow_today can validate conviction using the
                     full composite score, not just the momentum scanner score.

    Returns dict with: act_today, buy_candidates, review_list, grow_today.
    """
    ctx    = market_context or {"tone": "flat", "sp500_pct": 0, "nasdaq_pct": 0, "leading_sectors": []}

    # Build a unified earnings_lookup from held_data + grow_composites so the
    # earnings-risk check in _cross_reference applies to non-held new picks too.
    earnings_lookup: dict = {}
    for _t, _d in (held_data or {}).items():
        _ed = (_d or {}).get("earnings")
        if _ed:
            earnings_lookup[_t] = _ed
    for _t, _d in (grow_composites or {}).items():
        _ed = (_d or {}).get("earnings")
        if _ed:
            earnings_lookup.setdefault(_t, _ed)

    act    = _act_today(port_df, alert_list, risk_recs, news_items, macro_events, today)
    buys   = _buy_candidates(port_df, scanner_results, news_items, held_data, today,
                             act_today=act, risk_recs=risk_recs,
                             earnings_lookup=earnings_lookup,
                             composites=grow_composites or {})
    review = _review_list(port_df, news_items, macro_events, held_data, today,
                          portfolio_value=portfolio_value)
    grow   = _grow_today(port_df, scanner_results, news_items, held_data, today, portfolio_value, ctx,
                         act_today=act, composites=grow_composites or {}, risk_recs=risk_recs,
                         earnings_lookup=earnings_lookup, macro_events=macro_events,
                         movers=movers)
    return {"act_today": act, "buy_candidates": buys, "review_list": review, "grow_today": grow}
