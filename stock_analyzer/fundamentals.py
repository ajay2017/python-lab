"""
Fundamental scoring with sector-relative benchmarks.

Each metric is judged against typical norms for the stock's sector
rather than absolute universal thresholds. A 35x P/E is expensive for a
utility but fair for a high-growth semiconductor company.
"""

# Sector-relative benchmarks.
# Keys: pe_cheap, pe_fair_hi, pe_exp  (forward P/E tier boundaries)
#       rev_strong, rev_healthy        (revenue growth %, annual)
#       mgn_excel, mgn_good            (net profit margin %)
# pe_cheap  → below this = cheap for sector         (20 pts)
# pe_fair_hi → below this (above cheap) = fair value (15 pts)
# pe_exp     → above this = expensive                (2 pts)
# between pe_fair_hi and pe_exp = moderately expensive (8 pts)

_SECTOR_NORMS: dict[str, dict] = {
    "Technology": dict(
        pe_cheap=20, pe_fair_hi=45, pe_exp=65,
        rev_strong=15, rev_healthy=8,
        mgn_excel=20, mgn_good=12,
    ),
    "Healthcare": dict(
        pe_cheap=15, pe_fair_hi=30, pe_exp=50,
        rev_strong=12, rev_healthy=6,
        mgn_excel=20, mgn_good=10,
    ),
    "Financial Services": dict(
        pe_cheap=10, pe_fair_hi=18, pe_exp=28,
        rev_strong=10, rev_healthy=5,
        mgn_excel=25, mgn_good=15,
    ),
    "Consumer Cyclical": dict(
        pe_cheap=14, pe_fair_hi=25, pe_exp=40,
        rev_strong=12, rev_healthy=6,
        mgn_excel=12, mgn_good=6,
    ),
    "Consumer Defensive": dict(
        pe_cheap=15, pe_fair_hi=24, pe_exp=35,
        rev_strong=8, rev_healthy=4,
        mgn_excel=10, mgn_good=5,
    ),
    "Industrials": dict(
        pe_cheap=13, pe_fair_hi=22, pe_exp=35,
        rev_strong=10, rev_healthy=5,
        mgn_excel=15, mgn_good=8,
    ),
    "Basic Materials": dict(
        pe_cheap=10, pe_fair_hi=20, pe_exp=30,
        rev_strong=8, rev_healthy=4,
        mgn_excel=15, mgn_good=8,
    ),
    "Energy": dict(
        pe_cheap=10, pe_fair_hi=18, pe_exp=28,
        rev_strong=10, rev_healthy=5,
        mgn_excel=12, mgn_good=6,
    ),
    "Utilities": dict(
        pe_cheap=14, pe_fair_hi=20, pe_exp=28,
        rev_strong=5, rev_healthy=2,
        mgn_excel=15, mgn_good=8,
    ),
    "Communication Services": dict(
        pe_cheap=15, pe_fair_hi=28, pe_exp=45,
        rev_strong=10, rev_healthy=5,
        mgn_excel=20, mgn_good=10,
    ),
    "Real Estate": dict(
        pe_cheap=25, pe_fair_hi=45, pe_exp=70,
        rev_strong=8, rev_healthy=4,
        mgn_excel=30, mgn_good=15,
    ),
    "_default": dict(
        pe_cheap=15, pe_fair_hi=28, pe_exp=45,
        rev_strong=15, rev_healthy=8,
        mgn_excel=18, mgn_good=10,
    ),
}


def fundamental_score(financials: dict, sector: str = "") -> tuple[float, dict]:
    """
    Returns a score 0–100 and a dict of signal details.
    Metrics are benchmarked against sector norms so high-growth tech
    companies are not penalised for sector-appropriate P/E multiples.
    """
    norms = _SECTOR_NORMS.get(sector, _SECTOR_NORMS["_default"])
    sector_label = sector if sector else "market"

    signals: dict = {}
    points = 0
    max_points = 0

    # ── Valuation: Forward P/E (sector-relative) ─────────────────────────────
    fwd_pe = financials.get("forward_pe")
    if fwd_pe and fwd_pe > 0:
        max_points += 20
        if fwd_pe < norms["pe_cheap"]:
            points += 20
            signals["Forward P/E"] = f"{fwd_pe:.1f} — Cheap vs {sector_label} peers"
        elif fwd_pe < norms["pe_fair_hi"]:
            points += 15
            signals["Forward P/E"] = f"{fwd_pe:.1f} — Fair value for {sector_label}"
        elif fwd_pe < norms["pe_exp"]:
            points += 8
            signals["Forward P/E"] = f"{fwd_pe:.1f} — Moderately expensive for {sector_label}"
        else:
            points += 2
            signals["Forward P/E"] = f"{fwd_pe:.1f} — Expensive vs {sector_label} peers"

    # ── Revenue growth (sector-relative) ─────────────────────────────────────
    rev_growth = financials.get("revenue_growth")
    if rev_growth is not None:
        max_points += 20
        pct = rev_growth * 100
        if pct > norms["rev_strong"]:
            points += 20
            signals["Revenue Growth"] = f"{pct:.1f}% — Strong for {sector_label}"
        elif pct > norms["rev_healthy"]:
            points += 15
            signals["Revenue Growth"] = f"{pct:.1f}% — Healthy"
        elif pct > 0:
            points += 8
            signals["Revenue Growth"] = f"{pct:.1f}% — Slow growth"
        else:
            points += 2
            signals["Revenue Growth"] = f"{pct:.1f}% — Declining revenue"

    # ── Earnings growth ───────────────────────────────────────────────────────
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

    # ── Profit margins (sector-relative) ─────────────────────────────────────
    margins = financials.get("profit_margins")
    if margins is not None:
        max_points += 20
        pct = margins * 100
        if pct > norms["mgn_excel"]:
            points += 20
            signals["Profit Margin"] = f"{pct:.1f}% — Excellent for {sector_label}"
        elif pct > norms["mgn_good"]:
            points += 15
            signals["Profit Margin"] = f"{pct:.1f}% — Good"
        elif pct > 5:
            points += 8
            signals["Profit Margin"] = f"{pct:.1f}% — Thin"
        else:
            points += 2
            signals["Profit Margin"] = f"{pct:.1f}% — Marginal/Loss"

    # ── Analyst price target ──────────────────────────────────────────────────
    target = financials.get("analyst_target")
    if target:
        signals["Analyst Target"] = f"${target:.2f}"

    # ── Debt-to-equity (lower = safer balance sheet) ──────────────────────────
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

    # ── FCF Yield ─────────────────────────────────────────────────────────────
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

    # ── Data quality check ────────────────────────────────────────────────────
    # The 5 scoreable metrics are: fwd_pe, rev_growth, earn_growth, margins,
    # debt_to_equity (fcf_yield and analyst_target are supplemental).
    _scored_keys = ["forward_pe", "revenue_growth", "earnings_growth", "profit_margins", "debt_to_equity"]
    _missing = sum(1 for k in _scored_keys if financials.get(k) is None)
    if _missing >= 3:
        signals["⚠ Data Quality"] = (
            f"{_missing}/5 core metrics missing from Yahoo Finance — "
            "fundamental score is based on limited data and may be unreliable"
        )

    score = (points / max_points * 100) if max_points > 0 else 50
    return round(score, 1), signals


def upside_potential(current_price: float, financials: dict) -> str | None:
    target = financials.get("analyst_target")
    if target and current_price:
        pct = (target - current_price) / current_price * 100
        direction = "upside" if pct > 0 else "downside"
        return f"{abs(pct):.1f}% {direction} to analyst target (${target:.2f})"
    return None
