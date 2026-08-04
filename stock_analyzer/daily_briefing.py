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

from datetime import date, datetime as _dt, time as _time, timedelta

from stock_analyzer.constants import (
    COMPOSITE_BUY,
    COMPOSITE_STRONG_BUY,
    COMPOSITE_HIGH_CONVICTION,
    COMPOSITE_BUY_FLAT_DAY,
    SINGLE_NAME_CEILING,
    SECTOR_CEILING,
    SECTOR_ELEVATED,
    UNCLASSIFIED_SECTOR,
    MACRO_IMMINENT_DAYS,
    EARNINGS_IMMINENT_DAYS,
    EARNINGS_CRITICAL_DAYS,
    EARNINGS_MANAGEABLE_DAYS,
    RISK_PCT_PER_TRADE,
    ADD_WINNER_MIN_GAP_PCT,
    ADD_WINNER_COOLDOWN_DAYS,
    APPROACHING_STOP_GAP_PCT,
    LARGE_POSITION_WEIGHT_PCT,
    WEAK_CONVICTION_SCORE,
    NEWS_SENTIMENT_CRITICAL,
    NEWS_SENTIMENT_NEGATIVE,
    NEWS_SENTIMENT_WARN,
    NEWS_SENTIMENT_POSITIVE,
    NEWS_CRITICAL_MAX_TIER,
    NEWS_CRITICAL_MIN_HEADLINES,
    STOP_PROFIT_LOCK_PNL_PCT,
    STOP_PROFIT_LOCK_TRIM_PCT,
    STOP_TIGHTEN_ATR_MULT,
    STOP_TIGHTEN_MIN_GAIN_PCT,
    EARNINGS_OVERWEIGHT_TRIM_PCT,
    EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT,
    EARNINGS_OVERWEIGHT_TOLERANCE_PP,
    EARNINGS_OVERWEIGHT_TRIM_TO_PCT,
    WEAK_LARGE_TRIM_TO_PCT,
    MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
    MACRO_AFFECTED_TRIM_REDUCTION_PP,
    MACRO_BROAD_EXPOSURE_PCT,
    MOVER_MAX_PICKS,
    GROW_MAX_PICKS_BULL,
    GROW_MAX_PICKS_DEFAULT,
    GROW_CANDIDATE_OVERFETCH,
    GROW_TODAY_MAX_FUND_AGE_DAYS,
)
from stock_analyzer.signal_reconciliation import (
    reconcile_signals,
    lookup_composite,
)
from stock_analyzer.position_lifecycle import classify_position_state
from stock_analyzer.portfolio import resolve_sector
from stock_analyzer import exit_advisor
from stock_analyzer import decision_bucket
from stock_analyzer.predictive_analytics import divergence_at_entry
from stock_analyzer.personalized_discovery import score_candidate_match


# ── helpers ──────────────────────────────────────────────────────────────────

def _f(val, default=0.0):
    if val is None:
        return default
    try:
        v = float(val)
        return default if v != v else v
    except (TypeError, ValueError):
        return default


def _gate_wt(row) -> float:
    """Weight a concentration GATE compares against the 15%/35% ceilings.

    Reads "Gate Weight (%)" and falls back to "Weight (%)" when the column is
    absent (non-app callers like the headless cron). As of the 2026-07-09
    equity-basis policy (reqs G-19), app.py sets "Gate Weight (%)" == equity
    "Weight (%)", so both are the same equity weight; the column is kept as the
    seam so callers are unchanged if the basis is ever revisited.
    """
    _gw = row.get("Gate Weight (%)")
    # NaN-safe (`_gw == _gw` is False for NaN): a NaN gate weight falls back to
    # the equity column, matching _f's own NaN handling — strict parity with the
    # equity-basis path when Market Value is missing.
    return _f(_gw if (_gw is not None and _gw == _gw) else row.get("Weight (%)"), 0)


def _gate_wt_col(port_df) -> str:
    """Column name the sector gate sums — gate-basis if present, else equity."""
    return "Gate Weight (%)" if "Gate Weight (%)" in port_df.columns else "Weight (%)"


def _days_until(date_str: str, today: date) -> int | None:
    try:
        d = date.fromisoformat(str(date_str)[:10])
        return (d - today).days
    except Exception:
        return None


# Risk-Advisor rec types that are slow-moving statistical metrics (6-month
# Sharpe / beta / volatility / drawdown / tail risk) rather than time-boxed
# decisions. These route to the Brief's "Portfolio Tune-up" awareness lane
# instead of Act Today — §2B: Act Today = decisions to make TODAY; a Sharpe drag
# is a standing portfolio-quality issue, not a same-day call. Concentration
# breaches are NOT here — they stay in Act (structural, user-flagged act-worthy).
_TUNEUP_RISK_TYPES = frozenset({"beta", "sharpe", "volatility", "drawdown", "tail_risk", "single_name_concentration"})

# Of the Tune-up types, only these two name a concrete TRIM target (same scope
# as `_trim_targets`) — so only these can double-surface against an Act Today
# risk-off TRIM on the same high-beta name (both rank by β·weight). vol/drawdown/
# tail/concentration recommend ADDING diversifiers, a different action, so they
# never restate a trim and are left untouched (2026-08-04 audit).
_TUNEUP_TRIM_DRIVER_TYPES = frozenset({"beta", "sharpe"})
# Of the trim-drivers, only the beta card's recommendation prose is anchored to
# a single headline name (`trim_ticker == root_tickers[0]`); sharpe's prose is a
# generic per-position rule that names no anchor. So a beta card whose PRIMARY
# name is already acted must be dropped whole (its "Sell 50% of X" would go
# stale), whereas sharpe just chip-filters and survives on any remaining drag.
_TUNEUP_PRIMARY_ANCHORED_TYPES = frozenset({"beta"})


def _portfolio_tuneup(risk_recs: list | None, acted_tickers: set | None = None) -> list[dict]:
    """Slow-moving risk-metric recommendations surfaced as standing 'Portfolio
    Tune-up' items (awareness), not Act Today decisions. HIGH or MEDIUM only —
    OK/LOW aren't improvements to make. Shape is render-ready for app.py.

    acted_tickers (optional): every ticker already carrying an Act Today / Review
    card this render (same broad basis as the risk-off exclude set — matches the
    sibling block's 2026-07-29 H6 precedent). For the trim-driver types
    (beta/sharpe) this suppresses the redundant restatement of a trim already in
    Act Today (2026-08-04 audit — the beta Tune-up card and a risk-off TRIM both
    rank the same high-beta names, so a fragile+risk-off render otherwise shows
    "trim NVDA" in both lanes). A beta card whose primary trim_ticker is acted is
    dropped whole; a merely SECONDARY overlap is filtered from the chip list only,
    keeping the card's still-valid primary-anchored prose."""
    acted = {str(t).upper() for t in (acted_tickers or set())}
    out: list[dict] = []
    for rec in (risk_recs or []):
        if rec.get("type") not in _TUNEUP_RISK_TYPES:
            continue
        if rec.get("priority") not in ("HIGH", "MEDIUM"):
            continue
        rtype = rec.get("type")
        rt = rec.get("root_tickers", []) or []
        tickers = [r.get("ticker") for r in rt if r.get("ticker")]
        if acted and rtype in _TUNEUP_TRIM_DRIVER_TYPES and tickers:
            # Beta prose is built around root_tickers[0] — if THAT name is being
            # trimmed today, the whole card duplicates the Act Today call.
            if rtype in _TUNEUP_PRIMARY_ANCHORED_TYPES and str(tickers[0]).upper() in acted:
                continue
            tickers = [t for t in tickers if str(t).upper() not in acted]
            if not tickers:
                continue  # every named trim target already acted -> nothing to add
        out.append({
            "type":           rtype,
            "priority":       rec.get("priority"),
            "title":          rec.get("title", "Portfolio metric"),
            "problem":        rec.get("problem", ""),
            "recommendation": rec.get("recommendation", ""),
            "tickers":        tickers,
        })
    return out


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
                     composites: dict | None = None,
                     is_mover: bool = False) -> dict:
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
        if earn_days is not None and 0 <= earn_days <= EARNINGS_IMMINENT_DAYS:
            label = "today" if earn_days == 0 else f"in {earn_days}d"
            conflicts.append(
                f"Earnings {label} ({earn_date}) — binary event risk; "
                "signals may not hold post-release"
            )
            earnings_conflict = True
        elif earn_days is not None and EARNINGS_IMMINENT_DAYS < earn_days <= EARNINGS_MANAGEABLE_DAYS:
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
        verdict_label = f"⚠️ Caution — Earnings Within {EARNINGS_IMMINENT_DAYS} Days"
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
        # Compact form (Consistency #6) — this legacy field only ever renders as a
        # small nowrap badge (app.py Grow Today / scanner-picks pills), never as a
        # card headline; the verbose one-liner for that role is verdict_one_liner.
        verdict_label = "✅ Confirmed"
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
        is_mover=is_mover,
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


def _recently_added(ticker, held_data, cooldown: int = ADD_WINNER_COOLDOWN_DAYS) -> bool:
    """True when the user added shares to `ticker` within the cooldown window —
    used to suppress repeat add-to-winner nudges right after they acted on one
    (the PATH case). days_since_last_buy is the age of the newest still-held lot
    (attached in app.py). None (no trade journal) → False (calm, not blind)."""
    d = (held_data or {}).get(ticker) or (held_data or {}).get(str(ticker).upper()) or {}
    dslb = d.get("days_since_last_buy")
    return dslb is not None and dslb < cooldown


def _grow_today(port_df, scanner_results, news_items, held_data, today,
                portfolio_value: float, market_context: dict,
                act_today: list | None = None,
                review_list: list | None = None,
                composites: dict | None = None,
                risk_recs: list | None = None,
                earnings_lookup: dict | None = None,
                macro_events: list | None = None,
                movers: list | None = None,
                deterioration: list | None = None,
                winner_profile: dict | None = None) -> dict:
    """
    Build growth-oriented action list calibrated to today's market tone.

    market_context keys: sp500_pct, nasdaq_pct, tone ('bull'|'bear'|'flat'),
                         leading_sectors [{"sector", "etf", "return_1w"}]
    act_today  : output of _act_today — tickers flagged here are excluded.
    review_list: output of _review_list — tickers flagged here (e.g. earnings-
                 overweight or weak-large-position TRIM_TO_TARGET) are ALSO
                 excluded. Without this, a review-origin trim never suppressed
                 a same-day add-to-winner pick on the same ticker (2026-07-30
                 coordination-gap fix; earnings-overweight-trim is review-origin,
                 not act_today-origin, so it silently slipped past the earlier
                 act_today-only check).
    composites : {ticker: load_all() result} for top scanner picks — used to
                 validate conviction using the full composite score so the label
                 reflects more than just momentum.
    risk_recs  : Risk Advisor recommendations — tickers flagged for trim (beta
                 or sharpe root_tickers) are suppressed from add-to-winner so
                 the briefing never tells the user to trim X and add to X.
    deterioration : output of deterioration_signals() — a held ticker carrying
                 an active WATCH tier is SUPPRESSED from add-to-winner (moved to
                 deterioration_blocked_adds with a visible reason), not annotated:
                 the app must not nudge to ADD to a name showing early
                 deterioration, even on a Strong Buy composite. WATCH is
                 entry-price-agnostic, which is why this is the principled add
                 gate. (Changed 2026-07-21; was annotate-only, which read as
                 "add more to a name I'm telling you is weakening" — the FSLR case.)
    winner_profile : output of personalized_discovery.build_winner_profile() — the
                 composite/momentum band + top sectors of the user's own REALIZED
                 winning entries. None when there isn't enough history yet (never
                 fabricated at low N). Attached to new_picks ONLY (not
                 add_positions — an add-to-winner is a call already made once, not
                 a fresh entry decision) as pick["personalized_match"]. Diagnostic
                 annotation only, same as "divergence" above — never gates,
                 re-scores, or re-ranks; the pick has already cleared every gate.
    """
    tone        = market_context.get("tone", "flat")
    sp500_pct   = _f(market_context.get("sp500_pct", 0))
    nasdaq_pct  = _f(market_context.get("nasdaq_pct", 0))
    lead_secs   = market_context.get("leading_sectors", [])
    lead_names  = {ls.get("sector", "") for ls in lead_secs}
    held_tickers = set(port_df["Ticker"].tolist()) if port_df is not None else set()

    # ── Cross-reference the Brief to block conflicting picks ──────────────────
    # Any ticker already flagged ANYWHERE in today's Brief (act_today OR
    # review_list — sell, stop, risk reduce, or a review-origin trim) must not
    # appear in Grow Today — contradicting signals in the same briefing is
    # dangerous. decision_bucket.all_flagged_tickers() is the canonical broad
    # set (2026-07-29 audit H6) and resolves ticker=None/action.trim_ticker
    # macro-card shapes correctly, which a naive `.get("ticker")` loop misses.
    _act_blocked: set = decision_bucket.all_flagged_tickers(act_today, review_list)
    _act_risk_flags: list[str] = []
    for _ai in (act_today or []):
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
        # Today's events: check whether the release has already resolved.
        # FOMC (Fed Policy) is conservative — never lift same-day; the
        # post-announcement volatility window makes it the wrong time to
        # open new positions regardless of when the decision prints.
        # All other HIGH events (CPI, NFP, GDP — 08:30 ET pre-market
        # releases) lift when either (a) FRED confirms the actual is posted
        # (secondary) or (b) the wall clock has passed the scheduled release
        # time (primary). Fail-safe: any exception keeps the gate on.
        if _d == 0 and _ev.get("category") != "Fed Policy":
            _resolved = bool(_ev.get("released"))          # secondary: FRED
            if not _resolved and _ev.get("time_et"):       # primary: clock
                try:
                    import pytz as _pytz
                    _now_et = _dt.now(_pytz.timezone("America/New_York")).time()
                    _resolved = _time.fromisoformat(_ev["time_et"]) <= _now_et
                except Exception:
                    pass                                   # keep gate on
            if _resolved:
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

    # Sector concentration gate — sectors already AT/ABOVE the hard cap. Opening
    # or adding to a position in such a sector worsens a breach the Risk Advisor
    # is simultaneously telling the user to TRIM (the ESTC case: a Strong-Buy add
    # surfaced while its sector was 44% over the 35% cap). The deploy-capital
    # signal must defer to the protect-capital signal. Mirrors the macro gate.
    # Elevated (not yet hard-capped) sectors — the same SECTOR_ELEVATED band
    # Risk Advisor already renders as its MEDIUM "Elevated Sector Concentration"
    # card. A pick here isn't suppressed (a 25-35% sector isn't yet a breach),
    # but it's warned so the buy side doesn't stay silent about a sector the
    # trim side is already watching (the SHOP case: a buy recommended while
    # under 35% that, once bought, immediately became the trim engine's #1
    # full-exit candidate the same day — see risk_advisor's trim_excluded_recent
    # for the matching same-day-buy backstop on the trim side).
    _breached_sectors: set = set()
    _elevated_sectors: set = set()
    _sector_wt_map: dict = {}
    if port_df is not None and not port_df.empty and "Weight (%)" in port_df.columns:
        _sec_wt = port_df.groupby("Sector")[_gate_wt_col(port_df)].sum()
        # "Other" is a classification artifact (unclassified holdings), not a
        # real correlated sector — exclude it so picks/adds aren't suppressed by
        # a phantom cap breach. Mirrors risk_advisor's UNCLASSIFIED_SECTOR exclusion.
        _sector_wt_map = {str(_s): _f(_w, 0) for _s, _w in _sec_wt.items()
                          if str(_s) != UNCLASSIFIED_SECTOR}
        _breached_sectors = {_s for _s, _w in _sector_wt_map.items()
                             if _w >= SECTOR_CEILING}
        _elevated_sectors = {_s for _s, _w in _sector_wt_map.items()
                             if SECTOR_ELEVATED <= _w < SECTOR_CEILING}

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
            "sector_blocked_adds":        [],
            "sector_blocked_picks":       [],
            "macro_blocked_picks":        [],
            "composite_skipped":          [],
            "composite_unavailable":      [],
            "deploy_note":                None,
            "risk_banner":                risk_banner,
        }

    # Score threshold: higher bar on flat days, standard on bull days
    min_score   = COMPOSITE_BUY if tone == "bull" else COMPOSITE_BUY_FLAT_DAY
    max_picks   = GROW_MAX_PICKS_BULL if tone == "bull" else GROW_MAX_PICKS_DEFAULT

    new_picks: list[dict] = []
    _confirmed_picks: list[dict] = []
    _unverified_picks: list[dict] = []
    macro_blocked_picks: list[dict] = []
    sector_blocked_picks: list[dict] = []   # new picks suppressed — sector over hard cap
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

    # Curated pool — momentum-gated, ranked by momentum + sector bonus, then
    # truncated to a working set for sector-diversity selection.
    curated_rows: list[dict] = []
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
            curated_rows.append(d)
    curated_rows.sort(key=lambda d: d.get("_rank", 0), reverse=True)
    curated_rows = curated_rows[: max_picks * GROW_CANDIDATE_OVERFETCH]

    # Mover pool — kept SEPARATE so it isn't truncated out by higher-ranked
    # curated names and isn't subject to the curated momentum bar or the
    # flat-day verdict gate. Ranked by composite (their quality signal).
    mover_rows: list[dict] = []
    _seen_ct = {str(d.get("Ticker", "")).upper() for d in curated_rows}
    for m in (movers or []):
        mt = str(m.get("ticker", "")).upper()
        if not mt or mt in held_tickers or mt in _seen_ct:
            continue
        _sector = str(m.get("sector", ""))
        mover_rows.append({
            "Ticker":       mt,
            "Price":        _f(m.get("price")),
            "Sector":       _sector,
            "Trend":        str(m.get("trend", "")),
            "Signal":       str(m.get("scanner_signal", "")),
            "Score":        _f(m.get("score")),          # momentum (may be modest)
            "RSI":          _f(m.get("rsi", 50)),
            "1M Momentum":  _f(m.get("mom_1m", 0)),
            "3M Momentum":  _f(m.get("mom_3m", 0)),
            "_rank":        _f(m.get("composite_score")),
            "_is_mover":    True,
            "_day_change":  _f(m.get("day_change")),
        })
        _seen_ct.add(mt)
    mover_rows.sort(key=lambda d: d.get("_rank", 0), reverse=True)

    _mover_picks: list[dict] = []

    if curated_rows or mover_rows:
        for row in curated_rows + mover_rows:
            ticker   = str(row["Ticker"])
            price    = _f(row.get("Price", 0))
            sector   = resolve_sector(ticker, row.get("Sector", ""))
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

            # Sector concentration gate — same rationale as macro: don't open a
            # fresh position in a sector already over the hard cap.
            if sector in _breached_sectors:
                sector_blocked_picks.append({
                    "ticker": ticker, "sector": sector,
                    "score":  _f(row.get("Score", 0)),
                    "reason": f"{sector} sector already ≥ {SECTOR_CEILING:.0f}% hard cap",
                })
                continue

            xref = _cross_reference(ticker, row, port_df, news_items, held_data, today,
                                    earnings_lookup=earnings_lookup, composites=composites,
                                    is_mover=is_mover)

            # Skip hard conflicts (applies to everything).
            if xref["verdict"] == "conflicted":
                continue
            # Flat-day high-conviction suppression — CURATED ONLY. A composite-Buy
            # mover up 5%+ today is itself the "clearer direction" the flat-day
            # caution waits for, so movers are exempt from this gate (they still
            # face the composite gate, macro gate, act-today block below, and are
            # never reached on bear days due to the early return).
            if tone == "flat" and not is_mover and xref["verdict"] not in ("confirmed", "unverified"):
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

            # Staleness gate: if the bundle was served from the Supabase cache
            # fallback (stale_as_of is not None), the composite could reflect data
            # up to BUNDLE_CACHE_MAX_AGE_DAYS (5) old. That is too stale to back
            # a new-position recommendation — a deteriorating ticker can appear
            # composite-healthy on 5-day-old fundamentals and then score Sell on a
            # fresh Analysis fetch minutes later (the INTC incident, 2026-07-14).
            if _comp_data.get("stale_as_of") is not None:
                composite_unavailable.append({
                    "ticker":         ticker,
                    "sector":         sector,
                    "momentum_score": _f(row.get("Score", 0)),
                })
                continue

            # Fundamentals freshness gate: new-position recs require recent
            # fundamental data. fund_cache_age_days = None means a fresh yfinance
            # .info fetch was used, which always passes. Stale fundamentals (older
            # than GROW_TODAY_MAX_FUND_AGE_DAYS) can distort the composite enough
            # to flip a Sell ticker to a Buy recommendation.
            _fund_age = _comp_data.get("fund_cache_age_days")
            if _fund_age is not None and _fund_age > GROW_TODAY_MAX_FUND_AGE_DAYS:
                composite_unavailable.append({
                    "ticker":         ticker,
                    "sector":         sector,
                    "momentum_score": _f(row.get("Score", 0)),
                })
                continue

            # Fundamentals/Valuation gate: if the composite was computed on a
            # fabricated neutral-50 fundamental OR valuation leg (no real data
            # from any source), the verdict isn't trustworthy — hold it OUT of
            # new_picks exactly like a failed fetch. Otherwise the Brief can
            # surface a "new position to initiate" whose Analysis page
            # withholds its verdict (the PINS/HUBS mismatch, and — since
            # 2026-08-04 — the same class for Valuation's own availability flag).
            # Default True so legacy bundles without either flag aren't gated.
            if _comp_data and not (
                _comp_data.get("fundamentals_available", True)
                and _comp_data.get("val_available", True)
            ):
                composite_unavailable.append({
                    "ticker":         ticker,
                    "sector":         sector,
                    "momentum_score": _f(row.get("Score", 0)),
                })
                continue

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

            # Entry Timing (F-220 Phase 2) — momentum vs composite divergence at
            # the moment this pick fires. Diagnostic annotation only, never a
            # gate: the pick has already cleared every gate above (composite,
            # staleness, fundamentals, macro, sector, act-today) by this point.
            # Grow Today's own render (app.py) decides whether/how to caption it.
            _divergence = divergence_at_entry({
                "momentum_score": _f(row.get("Score", 0)), "composite_score": _composite_score,
            })

            pick = {
                "ticker":          ticker,
                "score":           _f(row.get("Score", 0)),    # momentum / scanner score
                "composite_score":      _composite_score,           # full composite, or None
                "composite_label":      _composite_label,
                "composite_fetched_at": _comp_data.get("fetched_at"),
                "conviction":           conviction,
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
                "divergence":      _divergence,
                # Elevated (not hard-capped) sector warning — see _elevated_sectors
                # above. Not a gate: the pick stands, but the user sees the sector
                # is already in the band Risk Advisor is watching for a trim.
                "sector_elevated_warning": (
                    f"{sector} sector already at {_sector_wt_map.get(sector, 0):.1f}% "
                    f"(warn level {SECTOR_ELEVATED:.0f}%) — this buy adds to a sector "
                    "Risk Advisor already has flagged for trim."
                    if sector in _elevated_sectors else None
                ),
            }
            if is_mover:
                _mover_picks.append(pick)
            elif xref["verdict"] == "confirmed":
                _confirmed_picks.append(pick)
            else:
                _unverified_picks.append(pick)

        # Curated selection: confirmed before unverified, 1-per-sector so all
        # slots don't land in the same sector, capped at max_picks.
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

        # Mover selection: OWN allowance (MOVER_MAX_PICKS), independent of the
        # curated cap. No sector-diversity constraint — multiple breakouts in
        # one sector is itself a signal (a sector-wide move), and forcing
        # 1-per-sector would hide most of them (the HUBS/ESTC/ADBE/INTU case,
        # all Technology). Ranked by composite (already sorted). Deduped
        # against anything already chosen.
        _already = {p["ticker"] for p in new_picks}
        _movers_added = 0
        for _pick in _mover_picks:
            if _pick["ticker"] in _already:
                continue
            new_picks.append(_pick)
            _movers_added += 1
            if _movers_added >= MOVER_MAX_PICKS:
                break

        # Personalized Discovery — flag which of TODAY's already-gated new_picks
        # resemble the user's own REALIZED winning entries. new_picks ONLY (not
        # add_positions below — an add-to-winner is a call already made once,
        # not a fresh entry decision). None-safe: score_candidate_match() returns
        # an empty match when winner_profile is None (not enough history yet).
        for _pick in new_picks:
            _pick["personalized_match"] = score_candidate_match(
                _pick.get("composite_score"), _pick.get("score"),
                _pick.get("sector"), winner_profile,
            )

    # Add-to-winner: held Strong Buy, Score ≥ COMPOSITE_BUY (65), Gap ≥ 8%
    # — only on bull days. Composite bar aligned with new-pick threshold
    # (was 68; raised the bar on adds and lowered the bar on new picks created
    # an asymmetric system where it was easier to start a position than to add
    # to one you'd already vetted — backwards from "press your winners.")
    add_positions:     list[dict] = []
    risk_blocked_adds: list[dict] = []
    concentration_blocked_adds: list[dict] = []
    sector_blocked_adds: list[dict] = []   # adds suppressed — sector over hard cap
    cooldown_adds:     list[dict] = []     # adds suppressed — recently added (post-act cooldown)
    deterioration_blocked_adds: list[dict] = []  # adds suppressed — active early-deterioration WATCH
    # Deterioration WATCH — SUPPRESS add-to-winner (see docstring; changed
    # 2026-07-21 from annotate-only). Same map shape/intent as _buy_candidates's
    # own copy — as of 2026-07-23 (936dff9) _buy_candidates ALSO suppresses at
    # source rather than annotating, so both add lanes are symmetric: a WATCH
    # name cannot appear as an add candidate anywhere in the Brief.
    _watch_by_ticker: dict = {
        str(d["ticker"]).upper(): d for d in (deterioration or []) if d.get("tier") == "WATCH"
    }
    # Pre-populate deterioration_blocked_adds for ALL held WATCH names regardless of
    # tone so the app.py dedup (_grow_shown) catches them in bear/protect mode.
    # In bull mode the inner loop below also catches them (and continues past add);
    # in bear/protect mode the inner loop is skipped and without this pre-pass WATCH
    # names leak into _buy_candidates (the FSLR Protect-mode repro, 2026-07-23).
    if port_df is not None:
        for _, _wr in port_df.iterrows():
            _wt = str(_wr["Ticker"]).upper()
            _dw_pre = _watch_by_ticker.get(_wt)
            if _dw_pre and not any(x["ticker"].upper() == _wt for x in deterioration_blocked_adds):
                deterioration_blocked_adds.append({
                    "ticker":  str(_wr["Ticker"]),
                    "score":   _f(_wr.get("Score"), 0),
                    "pnl_pct": _f(_wr.get("P&L (%)")),
                    "gap":     _f(_wr.get("Gap to Stop (%)"), 0),
                    "reason":  (
                        f"Down {_dw_pre['dd_from_peak_pct']:.1f}% from its "
                        f"${_dw_pre['peak']:.2f} peak, below SMA{_dw_pre['trend_ma']} — "
                        "early deterioration Watch. Don't add to a weakening name."
                    ),
                })
    if tone == "bull" and port_df is not None:
        for _, row in port_df.iterrows():
            sig = str(row.get("Signal", ""))
            gap = _f(row.get("Gap to Stop (%)"), 0)
            scr = _f(row.get("Score"), 0)
            if "Strong Buy" in sig and scr >= COMPOSITE_BUY and gap >= ADD_WINNER_MIN_GAP_PCT:
                ticker  = str(row["Ticker"])
                sector  = str(row.get("Sector", ""))
                # Skip if Act Today already flags this ticker for action
                if ticker in _act_blocked:
                    continue
                # Early-deterioration WATCH — an active WATCH on a held name
                # (below its trend MA, drawing down from its peak) means the app
                # must NOT nudge to ADD, even on a Strong Buy composite. This was
                # previously annotate-only, which read as "add more to a name I'm
                # telling you is weakening" (the FSLR 2026-07-21 case). WATCH is
                # entry-price-agnostic, so this is the principled add gate; the
                # suppression is surfaced (never silent) via deterioration_blocked_adds.
                _dw = _watch_by_ticker.get(ticker.upper())
                if _dw:
                    # Pre-pass (above) already added this in bear/protect mode;
                    # skip duplicate in bull mode.
                    if not any(x["ticker"].upper() == ticker.upper() for x in deterioration_blocked_adds):
                        deterioration_blocked_adds.append({
                            "ticker":  ticker,
                            "score":   scr,
                            "pnl_pct": _f(row.get("P&L (%)")),
                            "gap":     gap,
                            "reason":  (
                                f"Down {_dw['dd_from_peak_pct']:.1f}% from its "
                                f"${_dw['peak']:.2f} peak, below SMA{_dw['trend_ma']} — "
                                "early deterioration Watch. Don't add to a weakening name."
                            ),
                        })
                    continue
                # Post-add cooldown — you already acted on this add recently; let
                # the new shares settle before nudging to add more (anti-churn).
                if _recently_added(ticker, held_data):
                    _dslb = ((held_data or {}).get(ticker) or {}).get("days_since_last_buy")
                    cooldown_adds.append({
                        "ticker":  ticker,
                        "score":   scr,
                        "pnl_pct": _f(row.get("P&L (%)")),
                        "days_since_last_buy": _dslb,
                        "reason":  (
                            f"Added within the last {ADD_WINNER_COOLDOWN_DAYS} days "
                            f"({_dslb}d ago) — letting the new shares settle before "
                            "suggesting more."
                        ),
                    })
                    continue
                # Sector concentration gate — don't add to a position whose sector
                # is over the hard cap (Risk Advisor is recommending a trim there).
                if sector in _breached_sectors:
                    sector_blocked_adds.append({
                        "ticker":  ticker,
                        "sector":  sector,
                        "weight":  _f(row.get("Weight (%)"), 0),
                        "score":   scr,
                        "pnl_pct": _f(row.get("P&L (%)")),
                        "gap":     gap,
                        "reason":  (
                            f"{sector} sector ≥ {SECTOR_CEILING:.0f}% hard cap — "
                            "trim the sector before adding. A Strong Buy here is a "
                            "KEEP, not an add."
                        ),
                    })
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
                _cur_wt = _gate_wt(row)
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
                is_lead = any(ls.get("sector", "") in sector for ls in lead_secs)
                sizing  = _suggest_size(price, "Strong Uptrend", portfolio_value) if price > 0 else {}
                # Honest P&L framing — the composite is entry-price-agnostic, so a
                # Strong Buy add candidate can be up OR down vs the user's entry.
                # State the real P&L with a single correct sign (the old copy hard-
                # coded "Already profitable (+{:+.1f})", printing a false "+-10.8%"
                # on a losing position). (Change 3, FSLR 2026-07-21.)
                _pnl_v = _f(row.get("P&L (%)"))
                _pnl_phrase = (
                    f"Up {_pnl_v:.1f}% vs entry" if _pnl_v > 0
                    else f"Down {abs(_pnl_v):.1f}% vs entry" if _pnl_v < 0
                    else "At breakeven vs entry"
                )
                add_positions.append({
                    "ticker":    ticker,
                    "score":     scr,
                    "signal":    sig,
                    "pnl_pct":   _pnl_v,
                    "gap":       gap,
                    "sector":    sector,
                    "is_leader": is_lead,
                    "thesis":    (
                        f"{_pnl_phrase}, {gap:.1f}% above stop — Strong Buy, "
                        "adds within existing position."
                        + (" Sector leading today." if is_lead else "")
                    ),
                    "sizing":    sizing,
                    # Elevated (not hard-capped) sector warning — see _elevated_sectors.
                    "sector_elevated_warning": (
                        f"{sector} sector already at {_sector_wt_map.get(sector, 0):.1f}% "
                        f"(warn level {SECTOR_ELEVATED:.0f}%) — this add adds to a sector "
                        "Risk Advisor already has flagged for trim."
                        if sector in _elevated_sectors else None
                    ),
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
        "sector_blocked_adds":        sector_blocked_adds,
        "cooldown_adds":              cooldown_adds,
        "deterioration_blocked_adds": deterioration_blocked_adds,
        "sector_blocked_picks":       sector_blocked_picks,
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

def deterioration_signals(port_df, held_data, spy_df=None) -> list[dict]:
    """Held-position deterioration signals (exit_advisor) for every holding.

    Returns the list of non-None payloads (tier WATCH/TRIM/EXIT) — TRIM/EXIT feed
    Act Today, WATCH feeds the Review awareness lane. Pure pass-through to
    exit_advisor.assess_holding; all inputs come from data the brief already has
    (port_df row + held_data[t]'s df/atr/position_age_days + the SPY benchmark).
    """
    if port_df is None or getattr(port_df, "empty", True):
        return []
    out: list[dict] = []
    for _, row in port_df.iterrows():
        ticker = str(row.get("Ticker", "")).upper()
        data = (held_data or {}).get(ticker, {}) or {}
        payload = exit_advisor.assess_holding(
            ticker,
            data.get("df"),
            spy_df,
            price=_f(row.get("Price"), None),
            atr=data.get("atr"),
            avg_cost=_f(row.get("Avg Cost"), None),
            shares=_f(row.get("Shares"), 0),
            pnl_pct=_f(row.get("P&L (%)"), None),
            weight_pct=_f(row.get("Weight (%)"), None),
            age_days=data.get("position_age_days"),
            # Material-add re-anchor (Phase 1.1): when the user averaged in
            # materially, clip the peak window to "since the add" so a stale
            # pre-add high can't fabricate a false EXIT. None → spans the whole
            # holding (oldest lot), as before.
            peak_window_days=data.get("material_add_age_days"),
        )
        if payload:
            out.append(payload)
    return out


def _act_today(port_df, alert_list, risk_recs, news_items, macro_events, today,
               deterioration: list | None = None,
               premortem_triggers: list | None = None) -> list[dict]:
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
                "shares":  _shares,
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

    # 2.5 — Held-position deterioration (TRIM / EXIT). Fills the gap between
    # "Hold" and a score-collapse "Sell (<30)": a name down from its peak and
    # below trend, before the composite ever reacts. WATCH tier is awareness-only
    # → it goes to the Review lane, not here. Dedup: a stop_breach or sell_signal
    # already added above wins (single-surface), so skip a ticker already present.
    _existing = {i["ticker"] for i in items}
    for d in (deterioration or []):
        if d.get("tier") not in ("TRIM", "EXIT"):
            continue
        if d["ticker"] in _existing:
            continue
        _is_exit = d["tier"] == "EXIT"
        _pnl = d.get("pnl_pct") or 0.0
        _wt = d.get("weight_pct") or 0.0
        items.append({
            "priority": "high",
            "icon":     "📉" if _is_exit else "✂️",
            "ticker":   d["ticker"],
            "kind":     "deterioration_exit" if _is_exit else "deterioration_trim",
            "action":   "REDUCE — Deterioration Exit" if _is_exit else "TRIM — Deterioration",
            "directive": (
                f"Reduce aggressively — exit most/all of {d['shares']} shares. Down "
                f"{d['dd_from_peak_pct']:.1f}% from its ${d['peak']:.2f} peak and below "
                f"the {d['trend_ma']}-day trend."
                if _is_exit else
                f"Trim into the weakness. Down {d['dd_from_peak_pct']:.1f}% from its "
                f"${d['peak']:.2f} peak, below the {d['trend_ma']}-day trend, and lagging "
                f"the market."
            ),
            "why": (
                f"Drawdown {d['dd_from_peak_pct']:.1f}% (trigger "
                f"{d['trim_floor']:.0f}%/{d['exit_floor']:.0f}%), {d['below_ma_count']}/3 "
                f"sessions below SMA{d['trend_ma']}, rel-strength {d['rel_strength']:+.1f}pp. "
                f"P&L {_pnl:+.1f}%, weight {_wt:.1f}%."
            ),
            "trigger": "If it keeps closing below the trend and breaks support, exit the rest.",
            "weight":  d.get("weight_pct"),
            "pnl_pct": d.get("pnl_pct"),
            "dollar_risk": d.get("dollar_risk"),
            # Only meaningful for the EXIT tier — a full-position quantity the
            # directive already names ("exit most/all of N shares"). TRIM has no
            # equivalent computed quantity (its directive is qualitative, "trim
            # into the weakness"), so the UI must gate any sell-log button on
            # kind == "deterioration_exit", not just this field's presence.
            "shares": d.get("shares") if _is_exit else None,
        })

    # 2.6 — Pre-Commitment Enforcement (docs/plans/premortem-enforcement.md):
    # a fired pre-commitment trigger is a genuinely DIFFERENT reason from the
    # algorithm's own deterioration read above — the investor's own stated
    # condition, not exit_advisor's technical tier — so this section
    # DELIBERATELY does NOT dedupe against tickers already in `items` the way
    # section 2.5 does (2026-08-03 user-confirmed Q4: show both cards on the
    # same ticker, never merge — never a new gate, purely an audit/confront).
    for pmt in (premortem_triggers or []):
        _pmt_ticker = pmt["ticker"]
        _pmt_prow = port_df[port_df["Ticker"] == _pmt_ticker]
        _pmt_weight = _f(_pmt_prow.iloc[0].get("Weight (%)"), None) if not _pmt_prow.empty else None
        _pmt_pnl    = _f(_pmt_prow.iloc[0].get("P&L (%)"), None) if not _pmt_prow.empty else None
        _pmt_dir_word = "below" if pmt["direction"] == "below" else "above"
        items.append({
            "priority": "high",
            "icon":     "🎯",
            "ticker":   _pmt_ticker,
            "kind":     "premortem_triggered",
            "action":   "YOUR OWN COMMITMENT FIRED",
            "directive": (
                f"You said you'd exit if it broke {_pmt_dir_word} "
                f"${pmt['trigger_price']:.2f}. It happened on "
                f"{pmt['first_breach_date']} — {pmt['days_since']} day(s) ago. "
                f"You're still holding. What's changed?"
            ),
            "why": (
                f"Your Pre-Mortem exit commitment for this position stated a "
                f"{_pmt_dir_word} ${pmt['trigger_price']:.2f} trigger; the price "
                f"is currently ${pmt['current_price']:.2f}."
            ),
            "trigger": (
                "This is your own stated condition, not the algorithm's — "
                "reconsider or explicitly recommit."
            ),
            "weight":  _pmt_weight,
            "pnl_pct": _pmt_pnl,
        })

    # 3 — Critical news on held positions (compound ≤ NEWS_SENTIMENT_CRITICAL, tier ≤ 2,
    #     minimum NEWS_CRITICAL_MIN_HEADLINES qualifying headlines per ticker)
    held_tickers = set(port_df["Ticker"].tolist())
    _crit_by_ticker: dict = {}
    for item in (news_items or []):
        _t = str(item.get("ticker", "")).upper()
        if (_t in held_tickers
                and item.get("compound", 0) <= NEWS_SENTIMENT_CRITICAL
                and item.get("tier", 3) <= NEWS_CRITICAL_MAX_TIER):
            _crit_by_ticker.setdefault(_t, []).append(item)

    for ticker, _crit_items in _crit_by_ticker.items():
        if len(_crit_items) < NEWS_CRITICAL_MIN_HEADLINES:
            continue
        if ticker not in {i["ticker"] for i in items}:
            pm = port_df[port_df["Ticker"] == ticker]
            row = pm.iloc[0] if not pm.empty else {}
            _worst = min(_crit_items, key=lambda x: x.get("compound", 0))
            _n = len(_crit_items)
            items.append({
                "priority": "high",
                "icon":     "🚨",
                "ticker":   ticker,
                "kind":     "critical_news",
                "action":   "Watch — Critical News",
                "directive": (
                    "Hold for now, but tighten your stop and re-evaluate the thesis "
                    "after the news is confirmed."
                ),
                "why": (
                    f"{_n} tier-{_worst.get('tier', 2)} headline{'s' if _n > 1 else ''} "
                    f"at sentiment {_worst.get('compound', 0):+.2f}: "
                    f"\"{(_worst.get('title') or _worst.get('headline', 'news alert'))[:80]}\" "
                    "— material, but not a mechanical sell."
                ),
                "trigger": (
                    "Price −3% intraday or further headline deterioration → re-evaluate."
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
            # Raw list + category kept so the outer builder can re-filter "Affected"
            # against tickers already carrying an Act Today reduce call (2026-07-29
            # audit follow-up: this card's own "Affected: GD, CRWD" list otherwise
            # restates names that already have their own SELL/TRIM card, reading
            # like a contradictory second opinion rather than the same call).
            "_affected_all": list(ev.get("affected_tickers", [])),
            "_event_category": ev.get("category", "Economic"),
        })

    # 5 — HIGH-priority risk advisor flags
    for rec in (risk_recs or []):
        if rec.get("priority") != "HIGH":
            continue
        # Slow-moving metric drags (Sharpe/beta/vol/drawdown/tail) are NOT
        # same-day decisions — they go to the Portfolio Tune-up awareness lane
        # (_portfolio_tuneup), not Act Today. (single_name_concentration is
        # MEDIUM + in _TUNEUP_RISK_TYPES, so it never reaches this HIGH loop.)
        if rec.get("type") in _TUNEUP_RISK_TYPES:
            continue
        rt = rec.get("root_tickers", [])
        # risk recs already carry rich directive text in `recommendation`; keep it
        # as a flag entry so multiple flags on one ticker can merge.
        _flag = {
            "title":          rec.get("title", "Risk Alert"),
            "recommendation": rec.get("recommendation", rec.get("problem", "")),
            "problem":        rec.get("problem", ""),
        }
        # Rebalance-plan payload rides ON THE FLAG (not the item top-level) so it
        # survives _consolidate_act_today, which keeps only the primary item's
        # keys but gathers ALL flags across a merged ticker group. The render
        # (app.py _render_act_card) keys off flag["rec_type"].
        if rec.get("type") == "sector_concentration":
            _flag["rec_type"]           = "sector_concentration"
            _flag["trim_candidates"]    = rec.get("trim_candidates", []) or []
            _flag["redeploy_sectors"]   = rec.get("redeploy_sectors", []) or []
            _flag["trim_target_pp"]     = rec.get("trim_target_pp")
            _flag["trim_target_dollar"] = rec.get("trim_target_dollar")
            _flag["trim_target_denom"]  = rec.get("trim_target_denom")
        items.append({
            "priority": "high",
            "icon":     "⚠️",
            "ticker":   rt[0]["ticker"] if rt else None,
            "kind":     "risk",
            "action":   f"RISK — {rec.get('title','Risk Alert')}",
            "risk_flags": [_flag],
            "weight":  None,
            "pnl_pct": None,
        })

    return _consolidate_act_today(items, port_df)


# Kinds that represent a mechanical exit decision — these suppress softer
# advisories (risk trims, news monitors) on the same ticker. Deterioration
# signals are deliberately NOT here: they are the gentler/earlier tripwire and
# get suppressed BY stop_breach/sell_signal (deduped at build time), never the
# other way round.
_MECHANICAL_KINDS = {"stop_breach", "sell_signal"}

# Display ordering within Act Today (lower = higher up). Mirrors the exit
# hierarchy: a breached stop first, then a composite Sell, then the deterioration
# tiers (aggressive EXIT before a TRIM), then everything else.
_KIND_RANK = {
    "stop_breach":         0,
    "sell_signal":         1,
    "deterioration_exit":  2,
    "deterioration_trim":  3,
    "premortem_triggered": 3,  # your own stated exit condition fired — same tier as
                               # deterioration_trim: a real decision due, not a mechanical stop
    "risk_off_derisk":     4,   # lowest-priority reduce (market-wide overlay, not name-specific)
}


def _consolidate_act_today(items: list[dict], port_df) -> list[dict]:
    """Collapse Act Today items so each ticker appears at most once.

    - Ticker-less items (macro) pass through unchanged.
    - `premortem_triggered` items ALSO pass through unchanged despite having a
      ticker (docs/plans/premortem-enforcement.md, user-confirmed Q4): it is a
      genuinely different reason from the algorithm's own reduce/risk read on
      that same ticker, and the merge logic below would otherwise silently
      drop it (picked or not as the ticker's single "primary" card) — the
      exact double-surface bug this exemption exists to prevent.
    - For each remaining ticker: if a mechanical-exit item exists, emit only
      the highest-priority mechanical one (drop risk + news — moot if
      exiting).
    - Otherwise merge: the highest-priority item becomes the card; all
      risk_flags across the group are gathered onto it so a ticker with
      two risk recs (e.g. beta + volatility) renders as one card.
    """
    passthrough = [
        it for it in items
        if not it.get("ticker") or it.get("kind") == "premortem_triggered"
    ]
    by_ticker: dict[str, list[dict]] = {}
    for it in items:
        if it.get("kind") == "premortem_triggered":
            continue  # exempted above — never merged with same-ticker items
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

    # Critical first; then by the exit hierarchy (stop > Sell > EXIT > TRIM >
    # other); then by dollar risk desc (largest exposure first, weight as
    # fallback). None values sort last within each band.
    out.sort(key=lambda x: (
        0 if x["priority"] == "critical" else 1,
        _KIND_RANK.get(x.get("kind"), 4),
        -(x.get("dollar_risk") or 0),
        -(x.get("weight") or 0),
    ))
    return out


# ── Buy Candidates ─────────────────────────────────────────────────────────────

def _buy_candidates(port_df, scanner_results, news_items, held_data, today,
                    act_today: list | None = None,
                    review_list: list | None = None,
                    risk_recs: list | None = None,
                    earnings_lookup: dict | None = None,
                    composites: dict | None = None,
                    deterioration: list | None = None) -> list[dict]:
    """
    Build buy candidate list with multi-signal confidence verdict for each pick.
    act_today: output of _act_today — tickers already flagged are excluded.
    review_list: output of _review_list — tickers flagged here (e.g. earnings-
               overweight or weak-large-position TRIM_TO_TARGET) are ALSO
               excluded from add-to-winner, same as act_today (2026-07-30
               coordination-gap fix — a review-origin trim wasn't blocking this
               surface's own independent add-to-winner block, a second instance
               of the same gap already fixed in _grow_today).
    risk_recs: Risk Advisor recs — tickers flagged for trim are suppressed from
               the add-to-winner block to avoid same-ticker capital conflicts.
    deterioration: output of deterioration_signals() — a held ticker carrying an
               active WATCH tier is annotated (not suppressed; WATCH is
               explicitly "no action yet") so an add-to-winner card doesn't read
               as contradicting the Review lane's early-deterioration tripwire.
    """
    items: list[dict] = []
    held_tickers = set(port_df["Ticker"].tolist())

    # Block any ticker already flagged ANYWHERE in today's Brief (act_today OR
    # review_list) — same canonical set as _grow_today's gate.
    _act_blocked: set = decision_bucket.all_flagged_tickers(act_today, review_list)

    # Risk Advisor trim targets — suppress same-ticker add-to-winner conflicts.
    _trim_set = _trim_targets(risk_recs)

    # Sector concentration — mirrors _grow_today's gate (same rationale: don't
    # open/add into a sector Risk Advisor is already telling the user to trim).
    # This surface previously had NO sector awareness at all, a gap wider than
    # _grow_today's — closed alongside it in the same pass (SHOP whiplash fix).
    _breached_sectors: set = set()
    _elevated_sectors: set = set()
    _sector_wt_map: dict = {}
    if port_df is not None and not port_df.empty and "Weight (%)" in port_df.columns:
        _sec_wt = port_df.groupby("Sector")[_gate_wt_col(port_df)].sum()
        _sector_wt_map = {str(_s): _f(_w, 0) for _s, _w in _sec_wt.items()
                          if str(_s) != UNCLASSIFIED_SECTOR}
        _breached_sectors = {_s for _s, _w in _sector_wt_map.items() if _w >= SECTOR_CEILING}
        _elevated_sectors = {_s for _s, _w in _sector_wt_map.items()
                             if SECTOR_ELEVATED <= _w < SECTOR_CEILING}

    def _sector_warning(sector: str) -> str | None:
        if sector not in _elevated_sectors:
            return None
        return (
            f"{sector} sector already at {_sector_wt_map.get(sector, 0):.1f}% "
            f"(warn level {SECTOR_ELEVATED:.0f}%) — this buy adds to a sector "
            "Risk Advisor already has flagged for trim."
        )

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

    # Deterioration WATCH — SUPPRESS add-to-winner (same policy as _grow_today).
    # A WATCH ticker must not appear as "ADD — Winning Position" anywhere in the
    # Brief — it contradicts the Review lane's early-deterioration warning.
    _watch_by_ticker: dict = {
        str(d["ticker"]).upper(): d for d in (deterioration or []) if d.get("tier") == "WATCH"
    }

    # 1 — Scanner picks not in portfolio (Score ≥ COMPOSITE_BUY)
    if scanner_results is not None and not scanner_results.empty:
        top_picks = scanner_results[
            (scanner_results["Score"] >= COMPOSITE_BUY) &
            (~scanner_results["Ticker"].isin(held_tickers)) &
            (~scanner_results["Ticker"].isin(_act_blocked))
        ].copy().sort_values("Score", ascending=False).head(5)

        for _, row in top_picks.iterrows():
            ticker = str(row["Ticker"])
            sector = str(row.get("Sector", "—"))
            # Sector concentration gate — same rationale as _grow_today: don't
            # surface a fresh position in a sector already over the hard cap.
            if sector in _breached_sectors:
                continue
            xref   = _cross_reference(ticker, row.to_dict(), port_df, news_items, held_data, today,
                                      composites=composites)
            items.append({
                "type":           "new_pick",
                "icon":           "🆕",
                "ticker":         ticker,
                "action":         "BUY — Scanner Pick",
                "price":          _f(row.get("Price"), None),
                "score":          _f(row.get("Score")),
                "scanner_signal": str(row.get("Signal", "")),
                "sector":         sector,
                "rsi":            _f(row.get("RSI")),
                "mom_1m":         _f(row.get("1M Momentum")),
                "trend":          str(row.get("Trend", "")),
                "xref":           xref,
                "sector_elevated_warning": _sector_warning(sector),
            })

    # 2 — Add-to-winner: held, Strong Buy composite, Score ≥ COMPOSITE_BUY (65),
    # Gap ≥ 8%, and current weight below the single-name ceiling.
    for _, row in port_df.iterrows():
        sig = str(row.get("Signal", ""))
        gap = _f(row.get("Gap to Stop (%)"), 0)
        scr = _f(row.get("Score"), 0)
        if "Strong Buy" in sig and scr >= COMPOSITE_BUY and gap >= ADD_WINNER_MIN_GAP_PCT:
            ticker = str(row["Ticker"])
            # Skip if the Brief already flags this ticker anywhere (Act Today
            # or Review) for any action
            if ticker in _act_blocked:
                continue
            # Post-add cooldown — already acted on this add recently (anti-churn,
            # mirrors the Grow Today add gate so PATH doesn't linger here either)
            if _recently_added(ticker, held_data):
                continue
            # Skip if Risk Advisor is recommending trim — same-ticker conflict
            if ticker.upper() in _trim_set:
                continue
            # Single-name ceiling — hard suppress; concentration risk overrides signal
            if _gate_wt(row) >= SINGLE_NAME_CEILING:
                continue
            # Drift-trim conflict — position is drift-overweight; don't add
            if ticker.upper() in _drift_trim_set:
                continue
            _sector = str(row.get("Sector", "—"))
            # Sector concentration gate — same rationale as _grow_today: don't
            # add to a position whose sector is already over the hard cap.
            if _sector in _breached_sectors:
                continue
            # Build a minimal scanner_row from portfolio data for cross-reference
            _synthetic = {
                "Signal": sig, "Score": scr,
                "RSI": 0, "1M Momentum": 0, "Trend": sig,
            }
            # Deterioration WATCH — suppress entirely (don't annotate-and-include;
            # "ADD — Winning Position" next to a Watch contradicts the Review lane).
            if _watch_by_ticker.get(ticker.upper()):
                continue
            xref = _cross_reference(ticker, _synthetic, port_df, news_items, held_data, today,
                                    earnings_lookup=earnings_lookup, composites=composites)
            items.append({
                "type":           "add_winner",
                "icon":           "➕",
                "ticker":         ticker,
                "action":         "ADD — Winning Position",
                "price":          _f(row.get("Price"), None),
                "score":          scr,
                "scanner_signal": sig,
                "sector":         _sector,
                "rsi":            0,
                "mom_1m":         _f(row.get("1M Momentum", 0)),
                "trend":          sig,
                "gap_to_stop":    gap,
                "pnl_pct":        _f(row.get("P&L (%)")),
                "xref":           xref,
                "sector_elevated_warning": _sector_warning(_sector),
            })

    _verdict_order = {"confirmed": 0, "mixed": 1, "caution": 1, "unverified": 2, "conflicted": 3}
    items.sort(key=lambda x: (_verdict_order.get(x["xref"]["verdict"], 2), -x["score"]))
    return items


# ── Review Before Close ────────────────────────────────────────────────────────

def _dynamic_overweight_floor(n_positions: int) -> float:
    """Equal-weight + EARNINGS_OVERWEIGHT_TOLERANCE_PP buffer, clamped to
    [EARNINGS_OVERWEIGHT_TRIM_PCT, EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT]. A flat
    threshold assumes a fixed portfolio size; this scales with however many
    positions are actually held, while still capping binary earnings-event
    exposure even for a deliberately concentrated book.
    """
    if n_positions <= 0:
        return EARNINGS_OVERWEIGHT_TRIM_PCT
    eq_target = 100.0 / n_positions
    return max(EARNINGS_OVERWEIGHT_TRIM_PCT,
               min(eq_target + EARNINGS_OVERWEIGHT_TOLERANCE_PP, EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT))


def _review_list(port_df, news_items, macro_events, held_data, today,
                 portfolio_value: float = 0.0,
                 act_today: list | None = None,
                 deterioration: list | None = None) -> list[dict]:
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
    # Tickers already surfaced in Act Today — the news-warning block below
    # excludes them so a ticker's negative-news risk shows ONCE, on the
    # higher-priority surface, instead of appearing as a Critical-News ACT here
    # AND a "no action today" WATCH below (the DELL split-brain). Mirrors the
    # act_today dedup that _buy_candidates and _grow_today already do.
    _act_tickers = {str(a.get("ticker", "")).upper() for a in (act_today or [])}
    from stock_analyzer.macro_calendar import HIGH as MC_HIGH, affected_sectors as _aff_sectors

    # 0 — Deterioration WATCH (awareness only). The early tripwire: a held name
    # down ≥ WATCH_DD% from its peak and below trend, but not yet at the
    # TRIM/EXIT action thresholds. Lives here (not Act Today) so it informs
    # without demanding a same-day trade — the calm-advisor split (§2B). Deduped
    # against Act Today so a name escalated to TRIM/EXIT doesn't also WATCH here.
    for d in (deterioration or []):
        if d.get("tier") != "WATCH":
            continue
        if str(d["ticker"]).upper() in _act_tickers:
            continue
        items.append({
            "priority": "low",
            "icon":     "👁️",
            "ticker":   d["ticker"],
            "headline": (
                f"down {d['dd_from_peak_pct']:.1f}% from ${d['peak']:.2f} peak, "
                f"below SMA{d['trend_ma']}"
            ),
            "action":   {"type": "DETERIORATION_WATCH"},
            "why": (
                f"Early deterioration: drawdown {d['dd_from_peak_pct']:.1f}%, below the "
                f"{d['trend_ma']}-day trend. Not an action yet — watching for follow-through."
            ),
            "trigger": "A 2-of-3 close below the trend with market underperformance → TRIM.",
            "weight":  d.get("weight_pct"),
            "lifecycle": "",
        })

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
        # Lifecycle / settling grace: a freshly-opened position (held <
        # POSITION_SETTLING_DAYS) sits at its normal entry-to-stop distance by
        # construction — don't nudge it to tighten while it's still finding its
        # feet. Exits and critical-gap (≤3%) items are unaffected (precedence in
        # classify_position_state + the `not is_critical` guard). age None (no
        # journal) never yields "settling" → management is never silenced blind.
        _lifecycle = classify_position_state(
            (held_data or {}).get(ticker, {}).get("position_age_days"),
            pnl_pct, gap, has_exit_signal=False,
        )
        if _lifecycle == "settling" and not is_critical:
            continue
        # Profit-aware gate: a position that still has ROOM (gap > 3%) with no
        # gain to protect yet is NOT nudged to tighten — that's premature
        # micromanagement. A freshly-opened/flat position sits 3–8% above its
        # own ATR stop by construction (the MSFT case: gap 6.2%, P&L -0.0%);
        # tightening toward break-even removes the room the wider entry stop
        # deliberately gave it and reads as day-trading churn. Let the original
        # stop work until there's an actual gain to lock. CRITICAL-gap (≤3%,
        # about to be stopped out) positions still surface regardless of P&L.
        if not is_critical and pnl_pct < STOP_TIGHTEN_MIN_GAIN_PCT:
            continue
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
            "lifecycle": _lifecycle,
        })

    # 2 — Earnings within 7 days
    # Action policy:
    #   - Weight > dynamic overweight floor (position-count-aware — see
    #     _dynamic_overweight_floor) → trim to TRIM_TO_PCT (10%).
    #   - Weight ≤ floor → WATCH; sizing already conservative for the binary risk.
    seen_earn: set = set()
    _n_pos = len(port_df) if port_df is not None else 0
    _overweight_floor = _dynamic_overweight_floor(_n_pos)
    for ticker, data in (held_data or {}).items():
        earn_date = (data or {}).get("earnings")
        if not earn_date or ticker in seen_earn:
            continue
        days = _days_until(earn_date, today)
        if days is None or not (0 <= days <= EARNINGS_IMMINENT_DAYS):
            continue
        seen_earn.add(ticker)
        if ticker in _act_tickers:
            continue
        pm = port_df[port_df["Ticker"] == ticker]
        if pm.empty:
            continue
        weight = _f(pm["Weight (%)"].iloc[0])
        shares = int(_f(pm["Shares"].iloc[0]))
        price  = _f(pm["Price"].iloc[0])
        label  = "TODAY" if days == 0 else f"in {days}d"
        if weight > _overweight_floor and portfolio_value > 0:
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
                f"Weight {weight:.1f}% × binary earnings risk above your "
                f"{_overweight_floor:.1f}% position-count-adjusted threshold "
                f"(N={_n_pos} positions). Trim to size-down the binary event exposure."
            )
            trigger = "Beat → re-enter post-print if composite holds; Miss → existing stop protects rest."
        else:
            action = {"type": "WATCH"}
            why = (
                f"Weight {weight:.1f}% within tolerance "
                f"(≤ {_overweight_floor:.1f}% position-count-adjusted threshold, "
                f"N={_n_pos} positions). Existing sizing already conservative for the binary risk."
            )
            trigger = "Surprise miss + price gap-down → existing stop protects; no pre-event action needed."
        items.append({
            "priority": "medium" if days <= EARNINGS_CRITICAL_DAYS else "low",
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
    # Tickers already carrying an ACTIONABLE card — in Act Today, OR a stop /
    # trim ACT in the Review blocks above. A negative-news WATCH for them is
    # redundant: its only escalation is "tighten stop on this position", which
    # is moot when the position is already being acted on (NVDA had a raise-stop
    # ACT and a news WATCH). Earnings WATCH (a distinct scheduled catalyst, e.g.
    # AVGO) is type "WATCH", so it does NOT suppress — genuinely different items
    # still coexist; only same-dimension/redundant info is collapsed.
    _actioned = set(_act_tickers) | {
        str(it.get("ticker", "")).upper() for it in items
        if (it.get("action") or {}).get("type") != "WATCH"
    }
    warned: set = set()
    for item in (news_items or []):
        ticker = str(item.get("ticker", "")).upper()
        sent   = item.get("compound", 0)
        if (ticker in held_tickers
                and ticker not in _actioned   # already actioned elsewhere — don't double-surface news
                and NEWS_SENTIMENT_CRITICAL < sent <= NEWS_SENTIMENT_WARN
                and ticker not in warned):
            warned.add(ticker)
            headline_text = item.get("headline", "news")[:80]
            items.append({
                "priority": "low",
                "icon":     "📰",
                "ticker":   ticker,
                "watch_kind": "news",   # discriminator: a negative-news WATCH (vs an earnings/scheduled WATCH)
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
        if days is None or not (1 <= days <= MACRO_IMMINENT_DAYS):
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

        # Don't pre-trim a name that already carries its own Act Today decision
        # (a critical-news hold, a stop breach, a sell signal, a risk trim). That
        # name is already being acted on; a second "trim ahead of the macro event"
        # card on the SAME ticker is the contradictory double-surface §2B kills
        # (AVGO: critical-news "hold & tighten" vs NFP "trim"). Trim the weakest
        # affected name NOT already spoken for; if every affected holding is, the
        # event downgrades to WATCH (awareness), not a conflicting trim.
        _macro_eligible = sector_rows[
            ~sector_rows["Ticker"].astype(str).str.upper().isin(_act_tickers)
        ] if not sector_rows.empty else sector_rows

        # Broad macro print (NFP / CPI / Fed) — the affected sectors cover most
        # of the book, so a bounded single-name trim wouldn't meaningfully cut
        # the exposure; it just reads as pre-event churn (§2B). Downgrade to a
        # "hold through, mind your stops" awareness WATCH. The sized trim is
        # reserved for sector-CONCENTRATED events below.
        _is_broad = exposure_pct >= MACRO_BROAD_EXPOSURE_PCT

        if _is_broad:
            action = {
                "type":          "WATCH",
                "from_exposure": exposure_pct,
                "threshold":     MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
                "broad":         True,
            }
            why = (
                f"This event touches {exposure_pct:.0f}% of your portfolio — a broad "
                "macro print, not a sector-specific shock. A token single-name trim "
                "wouldn't materially reduce that exposure; hold through and let your "
                "existing stops do the work. Sized de-risking is reserved for events "
                "that hit a concentrated sector you can actually prune."
            )
        elif exposure_pct > MACRO_AFFECTED_TRIM_THRESHOLD_PCT and not _macro_eligible.empty and portfolio_value > 0:
            # Find lowest-conviction-score holding in the affected sectors.
            weakest = _macro_eligible.sort_values("Score", ascending=True).iloc[0]
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
            # Reconcile the displayed dollars + pp with the ROUNDED whole-share
            # count so "trim N shares (~$X)" is internally consistent (X = N ×
            # price). The pp-target alone diverges badly on small portfolios /
            # high-priced shares — the "1 share (~$571)" false-precision artifact.
            if weak_price > 0 and trim_shares > 0:
                trim_dollars = round(trim_shares * weak_price, 0)
                reduction_pp = (round(trim_dollars / portfolio_value * 100, 1)
                                if portfolio_value > 0 else reduction_pp)
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
        elif (exposure_pct > MACRO_AFFECTED_TRIM_THRESHOLD_PCT and not sector_rows.empty
              and _macro_eligible.empty):
            # Over threshold, but every affected holding already has an Act Today
            # decision — defer to those instead of issuing a contradictory trim.
            action = {
                "type":          "WATCH",
                "from_exposure": exposure_pct,
                "threshold":     MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
            }
            why = (
                f"Affected-sector exposure {exposure_pct:.1f}% > "
                f"{MACRO_AFFECTED_TRIM_THRESHOLD_PCT:.0f}% threshold, but the affected "
                "holdings already carry their own Act Today decision — no separate "
                "pre-event trim (avoids a contradictory double-call on one name)."
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

    # Final-pass news-WATCH dedup. The inline check above suppresses a
    # negative-news WATCH for any ticker actioned by an EARLIER block or Act
    # Today. But the macro pre-event trim runs AFTER the news block and carries
    # its target in action.trim_ticker (the item-level ticker is None), so a
    # macro trim of a name that also has mild negative news would slip through
    # as a double-surface (MSFT: NFP "trim" in Act + news WATCH in Awareness).
    # Re-derive the full actioned set (incl. trim_ticker) and drop any redundant
    # negative-news WATCH. Only news-WATCH is removed (watch_kind=="news");
    # earnings/scheduled WATCHes are distinct catalysts and are preserved.
    _actioned_final = set(_act_tickers)
    for _it in items:
        _ia = _it.get("action") or {}
        if _ia.get("type") == "WATCH":
            continue
        _itk = str(_it.get("ticker") or _ia.get("trim_ticker") or "").upper()
        if _itk:
            _actioned_final.add(_itk)
    items = [
        _it for _it in items
        if not (
            _it.get("watch_kind") == "news"
            and str(_it.get("ticker") or "").upper() in _actioned_final
        )
    ]

    pri_order = {"medium": 0, "low": 1}
    items.sort(key=lambda x: (pri_order.get(x.get("priority","low"), 1), -(x.get("weight") or 0)))
    return items


def _rewrite_macro_affected(act: list[dict], reduced: set[str]) -> None:
    """Strip tickers already carrying an Act Today reduce card out of every
    macro item's "Affected" list, in place.

    A macro card's "Affected: GD, CRWD" restates names that already have their
    own SELL/TRIM card elsewhere in Act Today — read together, "hold through
    the print" for a ticker with a same-day mechanical stop-breach SELL looks
    like a contradictory second opinion rather than the same call (2026-07-29
    audit follow-up). `reduced` is the act+review reduce-ticker set computed by
    the caller via `decision_bucket._ticker()`.

    This is a deliberate enumeration-level trim, not a full single-surface
    dedup (memory feedback_single_surface_priority): macro exposure and a
    stock-specific stop-breach/trim are genuinely different dimensions, and a
    partially-trimmed name (e.g. review-origin TRIM_TO_TARGET) still holds a
    residual position that IS exposed to today's print. We drop it from the
    enumeration anyway because the card's own directive — "no new entries" —
    is moot for a name already being sold/trimmed, and the reduce card itself
    already carries the caution; the macro event's existence still surfaces
    via the card itself, only the restated ticker name is removed.
    """
    for it in act:
        if it.get("kind") != "macro":
            continue
        affected_all = it.get("_affected_all", [])
        if not affected_all:
            continue  # broad-market card ("Affected: broad market.") — nothing to filter
        remaining = [t for t in affected_all if str(t).upper() not in reduced]
        category = it.get("_event_category", "Economic")
        if remaining:
            it["why"] = f"{category} release today. Affected: {', '.join(remaining[:5])}."
        else:
            it["why"] = (
                f"{category} release today. "
                "Other affected names already carry today's own Act Today calls."
            )
        # `_act_today()`'s consolidation step already synthesized `reason` from
        # the original (unfiltered) directive+why for back-compat consumers
        # (quick_research, premarket movers) — keep it in sync so it can't
        # silently carry the stale, unfiltered ticker list.
        if it.get("reason"):
            it["reason"] = f"{it.get('directive', '')} {it['why']}".strip()


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
    spy_df:          object | None = None,
    fragility:       dict | None = None,
    spy_trend_df:    object | None = None,
    vix_level:       float | None = None,
    winner_profile:  dict | None = None,
    trades_df:       object | None = None,
) -> dict:
    """
    Build a Start-Your-Day briefing synthesising all available intelligence.

    grow_composites: optional dict {ticker: load_all() result} pre-fetched for top
                     scanner picks so _grow_today can validate conviction using the
                     full composite score, not just the momentum scanner score.
    winner_profile:  optional personalized_discovery.build_winner_profile() output,
                     passed straight through to _grow_today (see its own docstring).
    trades_df:       optional full trade journal (db.load_trades()) — feeds
                     Pre-Commitment Enforcement (docs/plans/premortem-
                     enforcement.md); omitted or None degrades to no
                     premortem_triggered cards, never an error.

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

    # Held-position deterioration (exit_advisor) — computed once, fed to both
    # Act Today (TRIM/EXIT) and the Review awareness lane (WATCH).
    deterioration = deterioration_signals(port_df, held_data, spy_df)

    # Pre-Commitment Enforcement (docs/plans/premortem-enforcement.md) — pure
    # Python, zero LLM cost; degrades to [] on any missing/malformed input
    # (no trades_df, no held_data, DDL not yet applied) rather than erroring.
    from stock_analyzer.premortem_monitor import detect_premortem_triggers
    premortem_triggers = detect_premortem_triggers(trades_df, held_data, today)

    act    = _act_today(port_df, alert_list, risk_recs, news_items, macro_events, today,
                        deterioration=deterioration,
                        premortem_triggers=premortem_triggers)
    review = _review_list(port_df, news_items, macro_events, held_data, today,
                          portfolio_value=portfolio_value, act_today=act,
                          deterioration=deterioration)

    # Risk-off protective de-risk (exit-discipline Phase 2) — computed AFTER act +
    # review so we can exclude any ticker already carrying a higher-priority reduce
    # (single-surface: never double-reduce a name). Only arms when the book is
    # fragile AND the market is in a risk-off regime; otherwise returns []. These
    # are the lowest-priority reduce, so they append to the end of Act Today.
    # _buy_candidates runs after this block so it sees the complete act list and
    # cannot surface an ADD for a ticker that risk-off just flagged for TRIM.
    # Exclude every ticker carrying ANY review card (not just the 3 TRIM types) —
    # a WATCH-type review card ("not an action yet") must never coexist with a
    # same-render risk-off "Trim now" Act Today card for the same name (2026-07-29
    # audit H6; the prior narrower TRIM-only filter let that contradiction through).
    _reduced = decision_bucket.all_flagged_tickers(act, review)
    _rewrite_macro_affected(act, _reduced)
    _risk_off = exit_advisor.assess_risk_off_derisk(
        port_df, held_data,
        fragility=fragility, spy_trend_df=spy_trend_df, vix_level=vix_level,
        exclude_tickers=_reduced,
    )
    if _risk_off:
        act = act + _risk_off
    buys   = _buy_candidates(port_df, scanner_results, news_items, held_data, today,
                             act_today=act, review_list=review, risk_recs=risk_recs,
                             earnings_lookup=earnings_lookup,
                             composites=grow_composites or {},
                             deterioration=deterioration)
    grow   = _grow_today(port_df, scanner_results, news_items, held_data, today, portfolio_value, ctx,
                         act_today=act, review_list=review, composites=grow_composites or {},
                         risk_recs=risk_recs,
                         earnings_lookup=earnings_lookup, macro_events=macro_events,
                         deterioration=deterioration,
                         movers=movers,
                         winner_profile=winner_profile)
    # Tune-up beta/sharpe cards restate a trim; if that name is already carrying
    # an Act Today card (incl. the risk-off TRIM appended above) or a Review
    # card, drop the redundant restatement (2026-08-04 audit — same broad
    # flagged basis as the risk-off exclude set, now that `act` includes risk-off).
    _acted_final = decision_bucket.all_flagged_tickers(act, review)
    tuneup = _portfolio_tuneup(risk_recs, acted_tickers=_acted_final)
    return {"act_today": act, "buy_candidates": buys, "review_list": review,
            "grow_today": grow, "portfolio_tuneup": tuneup}
