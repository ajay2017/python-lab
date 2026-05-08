"""
Daily Briefing module.

Synthesizes all available intelligence into three prioritized action buckets:
  - Act Today   : stop triggers, sell signals, critical news, today's macro, REDUCE flags
  - Buy Candidates : scanner picks not held + add-to-winner signals
  - Review Before Close : approaching stops, near-term earnings, weak large positions,
                          warning news, upcoming macro catalysts
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


# ── Act Today ─────────────────────────────────────────────────────────────────

def _act_today(port_df, alert_list, risk_recs, news_items, macro_events, today) -> list[dict]:
    items: list[dict] = []

    # 1 — Stop-loss breaches (Gap to Stop ≤ 0 means price AT or BELOW stop)
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
                "weight":   _f(row.get("Weight (%)")),
                "pnl_pct":  _f(row.get("P&L (%)")),
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
                "weight":   _f(row.get("Weight (%)")),
                "pnl_pct":  _f(row.get("P&L (%)")),
            })

    # 3 — Critical news on held positions (compound ≤ -0.25, tier ≤ 2)
    held_tickers = set(port_df["Ticker"].tolist())
    for item in (news_items or []):
        ticker = str(item.get("ticker", "")).upper()
        if (ticker in held_tickers
                and item.get("compound", 0) <= -0.25
                and item.get("tier", 3) <= 2):
            if ticker not in {i["ticker"] for i in items}:
                row = port_df[port_df["Ticker"] == ticker].iloc[0] if any(port_df["Ticker"] == ticker) else {}
                items.append({
                    "priority": "high",
                    "icon":     "🚨",
                    "ticker":   ticker,
                    "action":   "MONITOR — Critical News",
                    "reason":   (
                        f"Tier-{item.get('tier',3)} source: \"{item.get('headline', 'news alert')[:80]}\" "
                        f"(sentiment {item.get('compound', 0):+.2f}). "
                        f"Verify thesis integrity before next open."
                    ),
                    "weight":   _f(row.get("Weight (%)") if hasattr(row, 'get') else 0),
                    "pnl_pct":  _f(row.get("P&L (%)") if hasattr(row, 'get') else 0),
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
                f"{ev.get('category', '')} release today. "
                f"Affected positions: {', '.join(ev.get('affected_tickers', [])[:5]) or 'All'}. "
                f"{ev.get('playbook_note', '') or 'Review macro playbook for positioning.'}"
            ),
            "weight":   None,
            "pnl_pct":  None,
        })

    # 5 — REDUCE flags from risk advisor (HIGH priority)
    for rec in (risk_recs or []):
        if rec.get("priority") != "HIGH":
            continue
        rt = rec.get("root_tickers", [])
        tickers_text = ", ".join(r.get("ticker", "") for r in rt) if rt else "portfolio"
        items.append({
            "priority": "high",
            "icon":     "⚠️",
            "ticker":   rt[0]["ticker"] if rt else None,
            "action":   f"RISK — {rec.get('title', 'Risk Alert')}",
            "reason":   rec.get("recommendation", rec.get("problem", "")),
            "weight":   None,
            "pnl_pct":  None,
        })

    # Sort: critical first, then by weight desc
    def _sort_key(x):
        pri = 0 if x["priority"] == "critical" else 1
        w   = -(x["weight"] or 0)
        return (pri, w)

    items.sort(key=_sort_key)
    return items


# ── Buy Candidates ─────────────────────────────────────────────────────────────

def _buy_candidates(port_df, scanner_results) -> list[dict]:
    items: list[dict] = []
    held_tickers = set(port_df["Ticker"].tolist())

    # 1 — Scanner picks not in portfolio (Score ≥ 65)
    if scanner_results is not None and not scanner_results.empty:
        top_picks = scanner_results[
            (scanner_results["Score"] >= 65) &
            (~scanner_results["Ticker"].isin(held_tickers))
        ].copy()
        top_picks = top_picks.sort_values("Score", ascending=False).head(5)
        for _, row in top_picks.iterrows():
            sig = str(row.get("Signal", ""))
            items.append({
                "type":    "new_pick",
                "icon":    "🆕",
                "ticker":  row["Ticker"],
                "action":  "BUY — Scanner Pick",
                "reason":  (
                    f"Score {_f(row.get('Score')):.0f}/100 · Signal: {sig} · "
                    f"Sector: {row.get('Sector', '—')}. "
                    f"Not currently held — consider initiating position."
                ),
                "score":   _f(row.get("Score")),
            })

    # 2 — Add-to-winner: held, Strong Buy, Score ≥ 68, Gap to Stop ≥ 8%
    for _, row in port_df.iterrows():
        sig  = str(row.get("Signal", ""))
        gap  = _f(row.get("Gap to Stop (%)"), 0)
        scr  = _f(row.get("Score"), 0)
        if "Strong Buy" in sig and scr >= 68 and gap >= 8:
            items.append({
                "type":   "add_winner",
                "icon":   "➕",
                "ticker": row["Ticker"],
                "action": "ADD — Winning Position",
                "reason": (
                    f"**{sig}** signal, score {scr:.0f}/100, "
                    f"{gap:.1f}% above stop ({row.get('Stop Type', 'ATR')}). "
                    f"P&L {_f(row.get('P&L (%)')):+.1f}% — trend intact, room to add."
                ),
                "score":  scr,
            })

    items.sort(key=lambda x: -x["score"])
    return items


# ── Review Before Close ────────────────────────────────────────────────────────

def _review_list(port_df, news_items, macro_events, held_data, today) -> list[dict]:
    items: list[dict] = []
    held_tickers = set(port_df["Ticker"].tolist())
    from stock_analyzer.macro_calendar import HIGH as MC_HIGH

    # 1 — Approaching stop (3–8% gap)
    for _, row in port_df.iterrows():
        gap = _f(row.get("Gap to Stop (%)"), None)
        if gap is None:
            continue
        if 0 < gap <= 8:
            urgency = "medium" if gap > 3 else "low"
            items.append({
                "priority": urgency,
                "icon":     "📍",
                "ticker":   row["Ticker"],
                "reason":   (
                    f"Only **{gap:.1f}%** above {row.get('Stop Type', 'ATR')} stop "
                    f"(${_f(row.get('Stop')):.2f}). P&L {_f(row.get('P&L (%)')):+.1f}%. "
                    "Tighten stop or plan exit if level breaks."
                ),
                "weight":   _f(row.get("Weight (%)")),
            })

    # 2 — Earnings within 7 days
    seen_earn = set()
    for ticker, data in (held_data or {}).items():
        earn_date = (data or {}).get("earnings")
        if not earn_date:
            continue
        days = _days_until(earn_date, today)
        if days is None or not (0 <= days <= 7):
            continue
        if ticker in seen_earn:
            continue
        seen_earn.add(ticker)
        row_match = port_df[port_df["Ticker"] == ticker]
        weight = _f(row_match["Weight (%)"].iloc[0]) if not row_match.empty else 0
        label  = "TODAY" if days == 0 else f"in {days}d"
        items.append({
            "priority": "medium" if days <= 3 else "low",
            "icon":     "📅",
            "ticker":   ticker,
            "reason":   (
                f"Earnings **{label}** ({earn_date}). "
                f"Position weight {weight:.1f}%. "
                "Consider sizing down before event or confirm thesis."
            ),
            "weight":   weight,
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
                "weight":   _f(row.get("Weight (%)")),
            })

    # 4 — Warning news on held positions (compound ≤ -0.05, not already flagged critical)
    warned = set()
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
                    f"Negative headline: \"{item.get('headline', 'news')[:70]}\" "
                    f"(sentiment {item.get('compound', 0):+.2f}). Monitor for confirmation."
                ),
                "weight":   0,
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
                f"Affected: {', '.join(ev.get('affected_tickers', [])[:4]) or 'macro-sensitive'}. "
                f"{ev.get('playbook_note', '') or 'Review playbook before event.'}"
            ),
            "weight":   None,
        })

    # Sort: medium first, then by weight desc
    pri_order = {"medium": 0, "low": 1}
    items.sort(key=lambda x: (pri_order.get(x.get("priority", "low"), 1), -(x.get("weight") or 0)))
    return items


# ── Public API ─────────────────────────────────────────────────────────────────

def build_daily_briefing(
    port_df,
    alert_list: list,
    risk_recs: list,
    news_items: list,
    macro_events: list,
    held_data: dict,
    scanner_results,
    portfolio_value: float,
    today: date,
) -> dict:
    """
    Build a Start-Your-Day briefing from all available intelligence.

    Returns
    -------
    dict with keys:
      act_today       — list of urgent, must-act items
      buy_candidates  — list of buy/add opportunities
      review_list     — list of things to watch before close
    """
    act     = _act_today(port_df, alert_list, risk_recs, news_items, macro_events, today)
    buys    = _buy_candidates(port_df, scanner_results)
    review  = _review_list(port_df, news_items, macro_events, held_data, today)
    return {
        "act_today":      act,
        "buy_candidates": buys,
        "review_list":    review,
    }
