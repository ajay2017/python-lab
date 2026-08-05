"""
Quick Research: wraps load_all() output into an entry-timing-aware
actionable summary for the Daily Briefing "Research a Stock" feature.
"""
import pandas as pd

from stock_analyzer.constants import (
    PORTFOLIO_BETA_ELEVATED,
    TICKER_BETA_HIGH,
    TICKER_BETA_CRITICAL,
    SECTOR_CEILING,
    SECTOR_ELEVATED,
    QUICK_RESEARCH_RSI_SEVERE_OVERBOUGHT,
    QUICK_RESEARCH_MOVE_1D_EXTREME_PCT,
    QUICK_RESEARCH_MOVE_5D_EXTREME_PCT,
    QUICK_RESEARCH_RSI_ELEVATED,
    QUICK_RESEARCH_MOVE_1D_ELEVATED_PCT,
    QUICK_RESEARCH_MOVE_5D_ELEVATED_PCT,
    QUICK_RESEARCH_RSI_OVERSOLD,
)


def _entry_timing(rsi_val: float | None, move_1d: float, move_5d: float) -> dict:
    rsi = rsi_val if rsi_val is not None else 50.0

    if (rsi >= QUICK_RESEARCH_RSI_SEVERE_OVERBOUGHT or move_1d >= QUICK_RESEARCH_MOVE_1D_EXTREME_PCT
            or move_5d >= QUICK_RESEARCH_MOVE_5D_EXTREME_PCT):
        why = []
        if move_1d >= QUICK_RESEARCH_MOVE_1D_EXTREME_PCT:
            why.append(f"surged {move_1d:+.1f}% today")
        if rsi >= QUICK_RESEARCH_RSI_SEVERE_OVERBOUGHT:
            why.append(f"RSI {rsi:.0f} — severely overbought")
        if move_5d >= QUICK_RESEARCH_MOVE_5D_EXTREME_PCT and move_1d < QUICK_RESEARCH_MOVE_1D_EXTREME_PCT:
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
    elif (rsi >= QUICK_RESEARCH_RSI_ELEVATED or move_1d >= QUICK_RESEARCH_MOVE_1D_ELEVATED_PCT
            or move_5d >= QUICK_RESEARCH_MOVE_5D_ELEVATED_PCT):
        why = []
        if move_1d >= QUICK_RESEARCH_MOVE_1D_ELEVATED_PCT:
            why.append(f"up {move_1d:+.1f}% today")
        if rsi >= QUICK_RESEARCH_RSI_ELEVATED:
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
    elif rsi_val is not None and rsi_val <= QUICK_RESEARCH_RSI_OVERSOLD:
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


def _portfolio_bullet(ticker: str, ctx: dict) -> str:
    """Build a portfolio-context bullet for the Quick Research panel."""
    # 1 — Act Today flag on THIS ticker takes highest priority
    flags = ctx.get("act_today_flags", [])
    if flags:
        f = flags[0]
        reason = f["reason"]
        short = reason[:110].rstrip(".") + "…" if len(reason) > 110 else reason
        return f"**⚠ Act Today — {ticker}:** {f['action']}. {short}"

    # 2 — Act Today flags on OTHER tickers in the SAME sector. If the user is
    # already being told to act on multiple positions in this sector, the
    # sector itself may be under stress — adding fresh exposure is the wrong
    # signal even if the queried ticker looks individually fine.
    sec        = ctx.get("sector_of_ticker", "")
    sec_acts   = ctx.get("sector_act_today", [])
    if sec and sec_acts:
        _tickers = [str(a.get("ticker", "")) for a in sec_acts if a.get("ticker")]
        _tickers = [t for t in _tickers if t and t != ticker]
        if _tickers:
            _short = ", ".join(_tickers[:3])
            _more  = f" (+{len(_tickers)-3} more)" if len(_tickers) > 3 else ""
            return (
                f"**⚠ Sector Under Stress — {sec}:** Act Today flags on "
                f"{_short}{_more} in the same sector. "
                f"Even if {ticker} looks individually fine, the sector is rotating "
                "out — defer new entries until the broader sector picture stabilises."
            )

    # 3 — Already held
    if ctx.get("held"):
        shares = ctx.get("held_shares")
        avg    = ctx.get("held_avg_cost")
        pnl    = ctx.get("held_pnl_pct")
        sig    = ctx.get("held_signal") or "—"
        desc   = (f"{shares:.0f} shares @ avg ${avg:.2f}" if (shares and avg) else "Already held")
        if pnl is not None:
            desc += f" (P&L {pnl:+.1f}%)"
        note = (
            " Signal suggests reducing rather than adding." if any(w in sig for w in ("Sell", "Avoid")) else
            " Hold-rated: monitor existing position; no new entry needed unless signal upgrades." if "Hold" in sig else ""
        )
        return f"**Your Position:** {desc}. Signal: **{sig}**.{note}"

    # 4 — New position: sector concentration + beta fit
    parts = []
    sec    = ctx.get("sector_of_ticker", "")
    sec_wt = ctx.get("sector_weight_pct") or 0.0
    if sec:
        if sec_wt >= SECTOR_CEILING:
            parts.append(
                f"{sec} already at {sec_wt:.0f}% of portfolio (≥ {SECTOR_CEILING:.0f}% ceiling) — adding would over-concentrate; size down or skip."
            )
        elif sec_wt >= SECTOR_ELEVATED:
            parts.append(
                f"{sec} at {sec_wt:.0f}% — moderate concentration; consider a half-size entry."
            )
        else:
            wt_str = f"{sec_wt:.0f}%" if sec_wt > 0 else "not currently held"
            parts.append(f"{sec} at {wt_str} of portfolio — room to add without over-concentrating.")

    port_beta = ctx.get("portfolio_beta")
    tick_beta = ctx.get("ticker_beta")
    if tick_beta is not None and port_beta is not None:
        if tick_beta > TICKER_BETA_CRITICAL and port_beta > PORTFOLIO_BETA_ELEVATED:
            parts.append(
                f"Beta {tick_beta:.1f} would add to an already elevated portfolio beta of {port_beta:.1f} — use smaller position (≤ 5% weight)."
            )
        elif tick_beta > TICKER_BETA_HIGH:
            parts.append(f"High beta {tick_beta:.1f} — volatile stock; use conservative sizing.")

    if not parts:
        parts.append("No concentration or beta concerns — standard position sizing applies.")

    return "**Portfolio Fit:** " + " ".join(parts)


def research_ticker(ticker: str, data: dict, portfolio_ctx: dict | None = None) -> dict:
    """
    ticker:        stock symbol (e.g. "RKLB")
    data:          result dict from load_all(ticker)
    portfolio_ctx: optional dict with held-position and portfolio-level context

    Returns a structured research summary with entry-timing verdict
    and up to 5-bullet actionable summary (5th bullet injected when portfolio_ctx provided).
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
    # Honor load_all's fundamentals gate (bundle_loader sets fundamentals_available):
    # when company data can't be sourced from ANY provider, `total` is a FABRICATED
    # neutral 50, and showing it as a confident "Hold 50/100" contradicts the Analysis
    # page, which WITHHOLDS the verdict (the PINS/HUBS fundamentals-gate class). Withhold
    # here too so the two surfaces can't disagree on the same name. Momentum and entry
    # timing below are purely technical and stay valid. (Audit §9 P1.) Also honor the
    # Valuation pillar's own availability flag (2026-08-04 audit finding) — same
    # fabricated-neutral-50 failure mode, just the other 30%-weighted leg.
    fundamentals_available = data.get("fundamentals_available", True) and data.get("val_available", True)

    target     = fins.get("analyst_target")
    upside_pct = float((target - price) / price * 100) if target and price else None

    # Bullet 1: overall signal + composite score — withheld when fundamentals absent.
    if fundamentals_available:
        b1 = f"**Signal: {rec['icon']} {rec['label']} ({score:.0f}/100)** — {rec['rationale']}"
        sig_label, sig_icon, sig_color, sig_score = rec["label"], rec["icon"], rec["color"], score
    else:
        b1 = (
            "**Signal: ❔ Verdict withheld** — fundamentals couldn't be sourced "
            "(company data unavailable from all providers), so a composite score "
            "would be guessing rather than measuring. Momentum and entry timing "
            "below are still valid."
        )
        sig_label, sig_icon, sig_color, sig_score = "Verdict withheld", "❔", "#dc2626", None

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

    bullets = [b1, b2, b3, b4]
    if portfolio_ctx is not None:
        bullets.append(_portfolio_bullet(ticker, portfolio_ctx))

    return {
        "ticker":         ticker,
        "name":           data.get("name", ticker),
        "sector":         data.get("sector", ""),
        "price":          price,
        "score":          sig_score,
        "signal":         sig_label,
        "signal_color":   sig_color,
        "signal_icon":    sig_icon,
        "fundamentals_available": fundamentals_available,
        "entry":          entry,
        "bullets":        bullets,
        "move_1d":        move_1d,
        "move_5d":        move_5d,
        "move_1m":        move_1m,
        "rsi":            rsi_val,
        "trend":          trend_short,
        "headlines":      data.get("headlines", [])[:3],
        "upside_pct":     upside_pct,
        "analyst_target": target,
    }
