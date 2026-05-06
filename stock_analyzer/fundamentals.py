def fundamental_score(financials: dict) -> tuple[float, dict]:
    """
    Returns a score 0–100 and a dict of signal details.
    Higher = stronger fundamentals.
    """
    signals = {}
    points = 0
    max_points = 0

    # Valuation: Forward P/E (lower is cheaper, better value)
    fwd_pe = financials.get("forward_pe")
    if fwd_pe and fwd_pe > 0:
        max_points += 20
        if fwd_pe < 15:
            points += 20
            signals["Forward P/E"] = f"{fwd_pe:.1f} — Undervalued"
        elif fwd_pe < 25:
            points += 15
            signals["Forward P/E"] = f"{fwd_pe:.1f} — Fair value"
        elif fwd_pe < 40:
            points += 8
            signals["Forward P/E"] = f"{fwd_pe:.1f} — Moderately expensive"
        else:
            points += 2
            signals["Forward P/E"] = f"{fwd_pe:.1f} — Expensive"

    # Revenue growth
    rev_growth = financials.get("revenue_growth")
    if rev_growth is not None:
        max_points += 20
        pct = rev_growth * 100
        if pct > 20:
            points += 20
            signals["Revenue Growth"] = f"{pct:.1f}% — Strong growth"
        elif pct > 10:
            points += 15
            signals["Revenue Growth"] = f"{pct:.1f}% — Healthy growth"
        elif pct > 0:
            points += 8
            signals["Revenue Growth"] = f"{pct:.1f}% — Slow growth"
        else:
            points += 2
            signals["Revenue Growth"] = f"{pct:.1f}% — Declining revenue"

    # Earnings growth
    earn_growth = financials.get("earnings_growth")
    if earn_growth is not None:
        max_points += 20
        pct = earn_growth * 100
        if pct > 25:
            points += 20
            signals["Earnings Growth"] = f"{pct:.1f}% — Accelerating"
        elif pct > 10:
            points += 15
            signals["Earnings Growth"] = f"{pct:.1f}% — Solid"
        elif pct > 0:
            points += 8
            signals["Earnings Growth"] = f"{pct:.1f}% — Modest"
        else:
            points += 2
            signals["Earnings Growth"] = f"{pct:.1f}% — Contracting earnings"

    # Profit margins
    margins = financials.get("profit_margins")
    if margins is not None:
        max_points += 20
        pct = margins * 100
        if pct > 25:
            points += 20
            signals["Profit Margin"] = f"{pct:.1f}% — Excellent"
        elif pct > 15:
            points += 15
            signals["Profit Margin"] = f"{pct:.1f}% — Good"
        elif pct > 5:
            points += 8
            signals["Profit Margin"] = f"{pct:.1f}% — Thin"
        else:
            points += 2
            signals["Profit Margin"] = f"{pct:.1f}% — Marginal/Loss"

    # Analyst price target vs current (upside potential)
    target = financials.get("analyst_target")
    current = financials.get("52_week_high")  # used as proxy if no current price here
    # We pass current price separately; skip if not available
    if target:
        signals["Analyst Target"] = f"${target:.2f}"

    # Debt-to-equity (lower = safer balance sheet)
    de = financials.get("debt_to_equity")
    if de is not None:
        max_points += 20
        if de < 30:
            points += 20
            signals["Debt/Equity"] = f"{de:.0f}% — Very low debt"
        elif de < 80:
            points += 14
            signals["Debt/Equity"] = f"{de:.0f}% — Manageable"
        elif de < 150:
            points += 8
            signals["Debt/Equity"] = f"{de:.0f}% — Elevated"
        else:
            points += 2
            signals["Debt/Equity"] = f"{de:.0f}% — High leverage"

    # FCF Yield — the primary valuation metric used by Goldman Sachs / institutional analysts
    # (harder to manipulate than P/E since it measures actual cash generation)
    fcf_yield = financials.get("fcf_yield")
    if fcf_yield is not None:
        max_points += 20
        if fcf_yield >= 5:
            points += 20
            signals["FCF Yield"] = f"{fcf_yield:.1f}% — Excellent cash generation"
        elif fcf_yield >= 3:
            points += 15
            signals["FCF Yield"] = f"{fcf_yield:.1f}% — Good"
        elif fcf_yield >= 1:
            points += 8
            signals["FCF Yield"] = f"{fcf_yield:.1f}% — Modest"
        elif fcf_yield >= 0:
            points += 3
            signals["FCF Yield"] = f"{fcf_yield:.1f}% — Low"
        else:
            points += 0
            signals["FCF Yield"] = f"{fcf_yield:.1f}% — Negative FCF (cash burn)"

    score = (points / max_points * 100) if max_points > 0 else 50
    return round(score, 1), signals


def upside_potential(current_price: float, financials: dict) -> str | None:
    target = financials.get("analyst_target")
    if target and current_price:
        pct = (target - current_price) / current_price * 100
        direction = "upside" if pct > 0 else "downside"
        return f"{abs(pct):.1f}% {direction} to analyst target (${target:.2f})"
    return None
