"""
Quick Research: wraps load_all() output into an entry-timing-aware
actionable summary for the Daily Briefing "Research a Stock" feature.
"""
import pandas as pd


def _entry_timing(rsi_val: float | None, move_1d: float, move_5d: float) -> dict:
    rsi = rsi_val if rsi_val is not None else 50.0

    if rsi > 80 or move_1d > 15 or move_5d > 25:
        why = []
        if move_1d > 15:
            why.append(f"surged {move_1d:+.1f}% today")
        if rsi > 80:
            why.append(f"RSI {rsi:.0f} — severely overbought")
        if move_5d > 25 and move_1d <= 15:
            why.append(f"up {move_5d:+.1f}% over 5 days")
        return {
            "verdict": "high_risk_avoid",
            "label": "High Risk — Avoid Chasing",
            "color": "#ef4444",
            "bg": "#450a0a",
            "icon": "🚫",
            "explanation": (
                f"Stock has {' and '.join(why)}. "
                "Post-catalyst buyers statistically underperform — the easy move is already priced in. "
                "Wait for a defined pullback (10–15% from peak) or multi-session consolidation before entering."
            ),
        }
    elif rsi > 68 or move_1d > 5 or move_5d > 12:
        why = []
        if move_1d > 5:
            why.append(f"up {move_1d:+.1f}% today")
        if rsi > 68:
            why.append(f"RSI {rsi:.0f}")
        return {
            "verdict": "wait_pullback",
            "label": "Wait for Pullback",
            "color": "#f59e0b",
            "bg": "#422006",
            "icon": "⏳",
            "explanation": (
                (", ".join(why) if why else "Momentum elevated")
                + ". Better risk/reward to wait for a 5–8% pullback to support "
                "or let the stock consolidate at current levels for several sessions."
            ),
        }
    elif rsi_val is not None and rsi_val < 35:
        return {
            "verdict": "oversold",
            "label": "Oversold — Potential Entry",
            "color": "#60a5fa",
            "bg": "#1e3a5f",
            "icon": "🔵",
            "explanation": (
                f"RSI {rsi_val:.0f} — stock is in oversold territory. "
                "Watch for price stabilization before entering; "
                "confirm the fundamental thesis still holds (not a falling knife)."
            ),
        }
    else:
        rsi_str = f"RSI {rsi_val:.0f}" if rsi_val is not None else ""
        return {
            "verdict": "buy_now",
            "label": "Normal Entry Conditions",
            "color": "#22c55e",
            "bg": "#052e16",
            "icon": "✅",
            "explanation": (
                (rsi_str + " — not overextended. " if rsi_str else "")
                + "Technical setup supports an entry near current levels."
            ),
        }


def research_ticker(ticker: str, data: dict) -> dict:
    """
    ticker: stock symbol (e.g. "RKLB")
    data:   result dict from load_all(ticker)

    Returns a structured research summary with entry-timing verdict
    and 4-bullet actionable summary.
    """
    df    = data["df"]
    close = df["Close"]

    move_1d = float((close.iloc[-1] / close.iloc[-2]  - 1) * 100) if len(close) > 1  else 0.0
    move_5d = float((close.iloc[-1] / close.iloc[-6]  - 1) * 100) if len(close) > 5  else 0.0
    move_1m = float((close.iloc[-1] / close.iloc[-22] - 1) * 100) if len(close) > 21 else 0.0

    rsi_val = None
    if "RSI" in df.columns:
        r = df["RSI"].iloc[-1]
        if pd.notna(r):
            rsi_val = float(r)

    ma_trend = data["t_signals"].get("MA Trend", "")
    trend_short = (
        "Strong Uptrend" if "strong uptrend" in ma_trend.lower() else
        "Uptrend"        if "bullish"        in ma_trend.lower() else
        "Downtrend"      if "below"          in ma_trend.lower() else
        "Mixed"
    )

    entry    = _entry_timing(rsi_val, move_1d, move_5d)
    rec      = data["rec"]
    score    = data["total"]
    price    = data["current_price"]
    fins     = data["financials"]
    revs     = data.get("revisions", {})
    earnings = data.get("earnings")

    target     = fins.get("analyst_target")
    upside_pct = float((target - price) / price * 100) if target and price else None

    # Bullet 1: overall signal + composite score
    b1 = (
        f"**Signal: {rec['icon']} {rec['label']} ({score:.0f}/100)** — {rec['rationale']}"
    )

    # Bullet 2: momentum snapshot
    b2_parts = [p for p in [
        f"RSI {rsi_val:.0f}" if rsi_val is not None else "",
        f"1-day {move_1d:+.1f}%",
        f"1-month {move_1m:+.1f}%",
        trend_short,
    ] if p]
    b2 = "**Momentum:** " + " · ".join(b2_parts)

    # Bullet 3: entry timing
    b3 = f"**Entry Timing: {entry['icon']} {entry['label']}** — {entry['explanation']}"

    # Bullet 4: key contextual factors (earnings, analyst revisions, price target, short interest)
    b4_parts = []
    if earnings:
        b4_parts.append(
            f"⚠ Earnings {earnings} — high volatility risk; avoid initiating just before"
        )
    net = revs.get("net", 0)
    if net >= 2:
        b4_parts.append(
            f"📈 {revs['upgrades_90d']} analyst upgrades in 90 days (net {net:+d})"
        )
    elif net <= -2:
        b4_parts.append(
            f"📉 {revs['downgrades_90d']} analyst downgrades in 90 days (net {net:+d})"
        )
    if upside_pct is not None:
        b4_parts.append(
            f"🎯 Analyst target ${target:.2f} = {upside_pct:+.0f}% from current price"
        )
    short_pct = fins.get("short_pct_float") or 0
    if short_pct > 15:
        b4_parts.append(
            f"⚡ Short interest {short_pct:.0f}% of float — squeeze potential"
        )
    b4 = "**Key Context:** " + (
        "; ".join(b4_parts) if b4_parts else "No additional signals available"
    )

    return {
        "ticker":         ticker,
        "name":           data.get("name", ticker),
        "sector":         data.get("sector", ""),
        "price":          price,
        "score":          score,
        "signal":         rec["label"],
        "signal_color":   rec["color"],
        "signal_icon":    rec["icon"],
        "entry":          entry,
        "bullets":        [b1, b2, b3, b4],
        "move_1d":        move_1d,
        "move_5d":        move_5d,
        "move_1m":        move_1m,
        "rsi":            rsi_val,
        "trend":          trend_short,
        "headlines":      data.get("headlines", [])[:3],
        "upside_pct":     upside_pct,
        "analyst_target": target,
    }
