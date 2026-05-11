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
                     earnings_lookup: dict | None = None) -> dict:
    """
    Cross-check a buy candidate across all available signal layers.

    earnings_lookup (optional): {ticker: earnings_date_str}. Sourced from BOTH
       held_data and pre-fetched composites, so the earnings risk check applies
       to non-held new picks too — the previous behaviour was to skip the check
       silently if a candidate wasn't in held_data, which could have the app
       recommend buying a stock the day before its earnings call.

    Returns a dict with:
      verdict        — 'confirmed' | 'mixed' | 'conflicted' | 'caution' | 'unverified'
      verdict_label  — display string
      verdict_color  — hex colour
      agreed         — list of supporting signals
      conflicts      — list of conflicting signals
      layers_checked — int (how many independent layers evaluated)
      is_held        — bool (composite signal available only for held positions)
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

    # ── Layer 2: Composite signal (held positions only) ───────────────────────
    composite_conflict   = False
    composite_available  = False
    if is_held:
        pm = port_df[port_df["Ticker"] == ticker]
        comp_sig = str(pm.iloc[0].get("Signal", "")).strip()
        comp_scr = _f(pm.iloc[0].get("Score", 0))
        buy_words       = ("Strong Buy", "Buy")
        hold_sell_words = ("Hold", "Sell", "Avoid", "Weak")
        # Empty/missing Signal is a DATA gap — must not be coerced into agreement.
        # Treat it as "composite not available" so the verdict falls through to
        # "unverified" rather than "Confirmed — All Signals Aligned."
        if not comp_sig:
            composite_available = False
        else:
            composite_available = True
            if any(w in comp_sig for w in buy_words):
                agreed.append(f"Composite signal: {comp_sig} ({comp_scr:.0f}/100)")
            elif any(w in comp_sig for w in hold_sell_words):
                conflicts.append(
                    f"Composite signal: {comp_sig} ({comp_scr:.0f}/100) — "
                    "technicals say Buy but full multi-factor analysis is more cautious"
                )
                composite_conflict = True
            else:
                agreed.append(f"Composite signal: {comp_sig} ({comp_scr:.0f}/100)")
    # Non-held: composite signal NOT available — must not issue "Confirmed"

    # ── Layer 3: News sentiment ───────────────────────────────────────────────
    ticker_news = [n for n in (news_items or []) if str(n.get("ticker", "")).upper() == ticker]
    sentiment_conflict = False
    if ticker_news:
        avg_compound  = sum(n.get("compound", 0) for n in ticker_news) / len(ticker_news)
        best_headline = max(ticker_news, key=lambda n: abs(n.get("compound", 0)))
        if avg_compound <= -0.15:
            conflicts.append(
                f"News sentiment: Negative (avg {avg_compound:+.2f}) — "
                f"\"{best_headline.get('headline','')[:60]}\""
            )
            sentiment_conflict = True
        elif avg_compound >= 0.1:
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
    elif is_held and not composite_available:
        # Held position but composite Signal is missing — must not issue
        # "Confirmed." Surface the data gap as "verify" instead.
        verdict       = "unverified"
        verdict_label = "🔍 Verify — Composite Signal Missing"
        verdict_color = "#60a5fa"
    elif not is_held:
        # Not held — composite signal was never computed.
        # Technical momentum looks good but we cannot confirm without full analysis.
        verdict       = "unverified"
        verdict_label = "🔍 Verify — Run Stock Analysis First"
        verdict_color = "#60a5fa"   # blue — informational, not alarm
    else:
        verdict       = "confirmed"
        verdict_label = "✅ Confirmed — All Signals Aligned"
        verdict_color = "#22c55e"

    return {
        "verdict":             verdict,
        "verdict_label":       verdict_label,
        "verdict_color":       verdict_color,
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
    risk_dollars  = portfolio_value * 0.015          # 1.5% portfolio risk per trade
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
                earnings_lookup: dict | None = None) -> dict:
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

    # On bear days — no new entries, return protection message
    if tone == "bear":
        return {
            "tone":          "bear",
            "message":       (
                f"S&P 500 {sp500_pct:+.2f}% today — market in risk-off mode. "
                "Focus on protecting existing positions. "
                "Defer new entries until conditions stabilise."
            ),
            "new_picks":         [],
            "add_positions":     [],
            "risk_blocked_adds": [],
            "deploy_note":       None,
            "risk_banner":       risk_banner,
        }

    # Score threshold: higher bar on flat days, standard on bull days
    min_score   = 65 if tone == "bull" else 78
    max_picks   = 3  if tone == "bull" else 1

    new_picks: list[dict] = []
    if scanner_results is not None and not scanner_results.empty:
        candidates = scanner_results[
            (scanner_results["Score"] >= min_score) &
            (~scanner_results["Ticker"].isin(held_tickers))
        ].copy()

        # Bonus sort key: sector leader gets +5 to score for ranking
        def _rank_score(row):
            sector_bonus = 5 if any(ls.get("sector","") in str(row.get("Sector",""))
                                    for ls in lead_secs) else 0
            return _f(row.get("Score", 0)) + sector_bonus

        candidates["_rank"] = candidates.apply(_rank_score, axis=1)
        # Wider pool so sector-diversity filtering has enough candidates to draw from
        candidates = candidates.sort_values("_rank", ascending=False).head(max_picks * 4)

        _confirmed_picks: list[dict] = []
        _unverified_picks: list[dict] = []

        for _, row in candidates.iterrows():
            ticker   = str(row["Ticker"])
            price    = _f(row.get("Price", 0))
            sector   = str(row.get("Sector", ""))
            trend    = str(row.get("Trend", ""))
            is_leader = any(ls.get("sector","") in sector for ls in lead_secs)

            # Skip if Act Today already has an action on this ticker
            if ticker in _act_blocked:
                continue

            xref = _cross_reference(ticker, row.to_dict(), port_df, news_items, held_data, today,
                                    earnings_lookup=earnings_lookup)

            # Skip hard conflicts; on flat days skip anything but confirmed/unverified
            if xref["verdict"] == "conflicted":
                continue
            if tone == "flat" and xref["verdict"] not in ("confirmed", "unverified"):
                continue

            sizing = _suggest_size(price, trend, portfolio_value) if price > 0 and portfolio_value > 0 else {}
            thesis = _thesis(ticker, row.to_dict(), is_leader)

            # Validate conviction using full composite score when available.
            # Scanner score measures momentum only; composite includes fundamentals
            # and sentiment — a pick can score 100 on momentum but 63 composite.
            _comp_data       = (composites or {}).get(ticker, {})
            _composite_score = _f(_comp_data.get("total")) if _comp_data else None
            _composite_label = str((_comp_data.get("rec") or {}).get("label", "")) if _comp_data else ""

            # Exclude picks where composite is known and below the Buy threshold (65).
            # Scanner score measures momentum only — composite (technical + fundamental
            # + sentiment) is the authoritative signal. A 100/100 momentum score with
            # a 63 composite is not a high-conviction entry.
            if _composite_score is not None and _composite_score < 65:
                continue

            # Conviction tier: drives the label shown on the card
            if _composite_score is None:
                conviction = "unverified"
            elif _composite_score >= 68:
                conviction = "high"
            elif _composite_score >= 65:
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

    # Add-to-winner: held Strong Buy, Score ≥ 68, Gap ≥ 8% — only on bull days
    add_positions:     list[dict] = []
    risk_blocked_adds: list[dict] = []
    if tone == "bull" and port_df is not None:
        for _, row in port_df.iterrows():
            sig = str(row.get("Signal", ""))
            gap = _f(row.get("Gap to Stop (%)"), 0)
            scr = _f(row.get("Score"), 0)
            if "Strong Buy" in sig and scr >= 68 and gap >= 8:
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
        deploy   = portfolio_value * 0.015 * n_trades
        if _act_risk_flags:
            deploy_note = (
                f"⚠️ Resolve Act Today risk alerts before deploying. "
                f"If proceeding: 1.5% risk per trade across {n_trades} setup{'s' if n_trades > 1 else ''} "
                f"= ~${deploy:,.0f}."
            )
        else:
            deploy_note = (
                f"At 1.5% risk per trade across {n_trades} setup{'s' if n_trades > 1 else ''}, "
                f"consider deploying ~${deploy:,.0f} today."
            )

    return {
        "tone":              tone,
        "message":           None,
        "new_picks":         new_picks,
        "add_positions":     add_positions,
        "risk_blocked_adds": risk_blocked_adds,
        "deploy_note":       deploy_note,
        "risk_banner":       risk_banner,
        "sp500_pct":         sp500_pct,
        "nasdaq_pct":        nasdaq_pct,
        "leading_sectors":   lead_secs,
    }


# ── Act Today ─────────────────────────────────────────────────────────────────

def _act_today(port_df, alert_list, risk_recs, news_items, macro_events, today) -> list[dict]:
    items: list[dict] = []

    # 1 — Stop-loss breaches (Gap to Stop ≤ 0)
    for _, row in port_df.iterrows():
        gap = _f(row.get("Gap to Stop (%)"), None)
        if gap is None:
            continue
        if gap <= 0:
            items.append({
                "priority": "critical",
                "icon":     "🛑",
                "ticker":   row["Ticker"],
                "action":   "SELL — Stop Breached",
                "reason":   (
                    f"Price ${_f(row.get('Price')):.2f} has breached the "
                    f"{row.get('Stop Type', '')} stop at ${_f(row.get('Stop')):.2f} "
                    f"(gap {gap:+.1f}%). Mechanical sell rule triggered."
                ),
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
                "action":   f"REVIEW — Signal: {sig}",
                "reason":   (
                    f"Composite signal has shifted to **{sig}** with conviction "
                    f"score {_f(row.get('Score')):.0f}/100. "
                    f"P&L {_f(row.get('P&L (%)')):+.1f}%, weight {_f(row.get('Weight (%)')):+.1f}%."
                ),
                "weight":  _f(row.get("Weight (%)")),
                "pnl_pct": _f(row.get("P&L (%)")),
            })

    # 3 — Critical news on held positions (compound ≤ -0.25, tier ≤ 2)
    held_tickers = set(port_df["Ticker"].tolist())
    for item in (news_items or []):
        ticker = str(item.get("ticker", "")).upper()
        if (ticker in held_tickers
                and item.get("compound", 0) <= -0.25
                and item.get("tier", 3) <= 2):
            if ticker not in {i["ticker"] for i in items}:
                pm = port_df[port_df["Ticker"] == ticker]
                row = pm.iloc[0] if not pm.empty else {}
                items.append({
                    "priority": "high",
                    "icon":     "🚨",
                    "ticker":   ticker,
                    "action":   "MONITOR — Critical News",
                    "reason":   (
                        f"Tier-{item.get('tier',3)} source: \"{item.get('headline','news alert')[:80]}\" "
                        f"(sentiment {item.get('compound',0):+.2f}). "
                        "Verify thesis integrity before next open."
                    ),
                    "weight":  _f(row.get("Weight (%)") if hasattr(row, "get") else 0),
                    "pnl_pct": _f(row.get("P&L (%)") if hasattr(row, "get") else 0),
                })

    # 4 — Today's HIGH-impact macro events
    from stock_analyzer.macro_calendar import HIGH as MC_HIGH
    today_macro = [e for e in (macro_events or []) if e.get("date") == today and e.get("impact") == MC_HIGH]
    for ev in today_macro:
        items.append({
            "priority": "high",
            "icon":     "🌐",
            "ticker":   None,
            "action":   f"MACRO — {ev.get('event', 'Economic Event')}",
            "reason":   (
                f"{ev.get('category','')} release today. "
                f"Affected: {', '.join(ev.get('affected_tickers',[])[:5]) or 'All'}. "
                f"{ev.get('playbook_note','') or 'Review macro playbook for positioning.'}"
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
            "action":   f"RISK — {rec.get('title','Risk Alert')}",
            "reason":   rec.get("recommendation", rec.get("problem", "")),
            "weight":  None,
            "pnl_pct": None,
        })

    items.sort(key=lambda x: (0 if x["priority"] == "critical" else 1, -(x.get("weight") or 0)))
    return items


# ── Buy Candidates ─────────────────────────────────────────────────────────────

def _buy_candidates(port_df, scanner_results, news_items, held_data, today,
                    act_today: list | None = None,
                    risk_recs: list | None = None,
                    earnings_lookup: dict | None = None) -> list[dict]:
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

    # 1 — Scanner picks not in portfolio (Score ≥ 65)
    if scanner_results is not None and not scanner_results.empty:
        top_picks = scanner_results[
            (scanner_results["Score"] >= 65) &
            (~scanner_results["Ticker"].isin(held_tickers)) &
            (~scanner_results["Ticker"].isin(_act_blocked))
        ].copy().sort_values("Score", ascending=False).head(5)

        for _, row in top_picks.iterrows():
            ticker = str(row["Ticker"])
            xref   = _cross_reference(ticker, row.to_dict(), port_df, news_items, held_data, today)
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

    # 2 — Add-to-winner: held, Strong Buy composite signal, Score ≥ 68, Gap ≥ 8%
    for _, row in port_df.iterrows():
        sig = str(row.get("Signal", ""))
        gap = _f(row.get("Gap to Stop (%)"), 0)
        scr = _f(row.get("Score"), 0)
        if "Strong Buy" in sig and scr >= 68 and gap >= 8:
            ticker = str(row["Ticker"])
            # Skip if Act Today already flags this ticker for any action
            if ticker in _act_blocked:
                continue
            # Skip if Risk Advisor is recommending trim — same-ticker conflict
            if ticker.upper() in _trim_set:
                continue
            # Build a minimal scanner_row from portfolio data for cross-reference
            _synthetic = {
                "Signal": sig, "Score": scr,
                "RSI": 0, "1M Momentum": 0, "Trend": sig,
            }
            xref = _cross_reference(ticker, _synthetic, port_df, news_items, held_data, today,
                                    earnings_lookup=earnings_lookup)
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

def _review_list(port_df, news_items, macro_events, held_data, today) -> list[dict]:
    items: list[dict] = []
    held_tickers = set(port_df["Ticker"].tolist())
    from stock_analyzer.macro_calendar import HIGH as MC_HIGH

    # 1 — Approaching stop (0–8% gap)
    for _, row in port_df.iterrows():
        gap = _f(row.get("Gap to Stop (%)"), None)
        if gap is None:
            continue
        if 0 < gap <= 8:
            items.append({
                "priority": "medium" if gap > 3 else "low",
                "icon":     "📍",
                "ticker":   row["Ticker"],
                "reason":   (
                    f"Only **{gap:.1f}%** above {row.get('Stop Type','ATR')} stop "
                    f"(${_f(row.get('Stop')):.2f}). P&L {_f(row.get('P&L (%)')):+.1f}%. "
                    "Tighten stop or plan exit if level breaks."
                ),
                "weight": _f(row.get("Weight (%)")),
            })

    # 2 — Earnings within 7 days
    seen_earn: set = set()
    for ticker, data in (held_data or {}).items():
        earn_date = (data or {}).get("earnings")
        if not earn_date or ticker in seen_earn:
            continue
        days = _days_until(earn_date, today)
        if days is None or not (0 <= days <= 7):
            continue
        seen_earn.add(ticker)
        pm     = port_df[port_df["Ticker"] == ticker]
        weight = _f(pm["Weight (%)"].iloc[0]) if not pm.empty else 0
        label  = "TODAY" if days == 0 else f"in {days}d"
        items.append({
            "priority": "medium" if days <= 3 else "low",
            "icon":     "📅",
            "ticker":   ticker,
            "reason":   (
                f"Earnings **{label}** ({earn_date}). Weight {weight:.1f}%. "
                "Consider sizing down before event or confirm thesis."
            ),
            "weight": weight,
        })

    # 3 — Weak large positions (weight ≥ 10%, Score < 55)
    for _, row in port_df.iterrows():
        if _f(row.get("Weight (%)")) >= 10 and _f(row.get("Score")) < 55:
            items.append({
                "priority": "medium",
                "icon":     "🔍",
                "ticker":   row["Ticker"],
                "reason":   (
                    f"Large position ({_f(row.get('Weight (%)')):+.1f}% of portfolio) "
                    f"but weak conviction score {_f(row.get('Score')):.0f}/100. "
                    "Reassess or trim to free capital for higher-conviction names."
                ),
                "weight": _f(row.get("Weight (%)")),
            })

    # 4 — Warning news on held positions (compound ≤ -0.05, not critical-level)
    warned: set = set()
    for item in (news_items or []):
        ticker = str(item.get("ticker", "")).upper()
        if (ticker in held_tickers
                and -0.25 < item.get("compound", 0) <= -0.05
                and ticker not in warned):
            warned.add(ticker)
            items.append({
                "priority": "low",
                "icon":     "📰",
                "ticker":   ticker,
                "reason":   (
                    f"Negative headline: \"{item.get('headline','news')[:70]}\" "
                    f"(sentiment {item.get('compound',0):+.2f}). Monitor for confirmation."
                ),
                "weight": 0,
            })

    # 5 — Upcoming macro events (1–3 days)
    for ev in (macro_events or []):
        ev_date = ev.get("date")
        if not ev_date or ev.get("impact") != MC_HIGH:
            continue
        days = _days_until(ev_date, today)
        if days is None or not (1 <= days <= 3):
            continue
        items.append({
            "priority": "low",
            "icon":     "🌐",
            "ticker":   None,
            "reason":   (
                f"**{ev.get('event')}** in {days}d ({ev_date}). "
                f"Affected: {', '.join(ev.get('affected_tickers',[])[:4]) or 'macro-sensitive'}. "
                f"{ev.get('playbook_note','') or 'Review playbook before event.'}"
            ),
            "weight": None,
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
                             earnings_lookup=earnings_lookup)
    review = _review_list(port_df, news_items, macro_events, held_data, today)
    grow   = _grow_today(port_df, scanner_results, news_items, held_data, today, portfolio_value, ctx,
                         act_today=act, composites=grow_composites or {}, risk_recs=risk_recs,
                         earnings_lookup=earnings_lookup)
    return {"act_today": act, "buy_candidates": buys, "review_list": review, "grow_today": grow}
