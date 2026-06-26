"""
Fundamental scoring with sector-relative benchmarks.

Each metric is judged against typical norms for the stock's sector
rather than absolute universal thresholds. A 35x P/E is expensive for a
utility but fair for a high-growth semiconductor company.
"""

# The core scoreable metrics. When NONE are present, fundamental_score returns a
# fabricated neutral 50 — so consumers gate on the count, not the raw score.
# Single source of truth (was duplicated as `_scored_keys` here and
# `_core_fund_keys` in app.py).
CORE_FUNDAMENTAL_KEYS = (
    "forward_pe", "revenue_growth", "earnings_growth",
    "profit_margins", "debt_to_equity",
)


def count_core_metrics(financials: dict | None) -> int:
    """How many core scoreable fundamental metrics are actually present."""
    f = financials or {}
    return sum(1 for k in CORE_FUNDAMENTAL_KEYS if f.get(k) is not None)


def resolve_fundamentals(
    live_financials: dict | None,
    cached_financials: dict | None,
    cached_age_days: int | None,
    max_age_days: int,
    min_metrics: int,
) -> tuple[dict, int, str, int | None]:
    """Choose which fundamentals to score on — live, or last-known-good cache.

    Pure decision (the caller does the I/O: fetch live, read/write the cache).
    Live wins whenever it has enough metrics. Otherwise, if a cached copy is
    both sufficient AND fresh enough (`cached_age_days <= max_age_days`), serve
    that — real data, transparently aged. Else keep live (the verdict will then
    withhold, since its metric count is below the gate).

    Returns (financials, metric_count, source, age_days):
      source  : "live"  — live data used (whether sufficient or not)
                "cache" — served from last-known-good fallback
      age_days: 0 for live; the cache age when source == "cache"
    """
    live = live_financials or {}
    live_count = count_core_metrics(live)
    if live_count >= min_metrics:
        return live, live_count, "live", 0
    if cached_financials:
        cached_count = count_core_metrics(cached_financials)
        if (cached_count >= min_metrics
                and cached_age_days is not None
                and cached_age_days <= max_age_days):
            return cached_financials, cached_count, "cache", cached_age_days
    return live, live_count, "live", 0

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

# Cross-sector scoring band thresholds — metrics that are not sector-relative.
# Changing these is a policy decision; keep them visible next to _SECTOR_NORMS.
_FUND_BANDS = {
    # Earnings growth %
    "earn_accel":  25,   # above this = Accelerating
    "earn_solid":  10,   # above this = Solid

    # Debt-to-equity ratio (reported as %)
    "de_low":      30,   # below this = Very low debt
    "de_mid":      80,   # below this = Manageable
    "de_high":    150,   # below this = Elevated; above = High leverage

    # FCF yield %
    "fcf_excel":    5,   # >= this = Excellent
    "fcf_good":     3,   # >= this = Good
    "fcf_modest":   1,   # >= this = Modest; >= 0 = Low; < 0 = Negative
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
        if pct > _FUND_BANDS["earn_accel"]:
            points += 20
            signals["Earnings Growth"] = f"{pct:.1f}% — Accelerating"
        elif pct > _FUND_BANDS["earn_solid"]:
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
        if de < _FUND_BANDS["de_low"]:
            points += 20
            signals["Debt/Equity"] = f"{de:.0f}% — Very low debt"
        elif de < _FUND_BANDS["de_mid"]:
            points += 14
            signals["Debt/Equity"] = f"{de:.0f}% — Manageable"
        elif de < _FUND_BANDS["de_high"]:
            points += 8
            signals["Debt/Equity"] = f"{de:.0f}% — Elevated"
        else:
            points += 2
            signals["Debt/Equity"] = f"{de:.0f}% — High leverage"

    # ── FCF Yield ─────────────────────────────────────────────────────────────
    fcf_yield = financials.get("fcf_yield")
    if fcf_yield is not None:
        max_points += 20
        if fcf_yield >= _FUND_BANDS["fcf_excel"]:
            points += 20
            signals["FCF Yield"] = f"{fcf_yield:.1f}% — Excellent cash generation"
        elif fcf_yield >= _FUND_BANDS["fcf_good"]:
            points += 15
            signals["FCF Yield"] = f"{fcf_yield:.1f}% — Good"
        elif fcf_yield >= _FUND_BANDS["fcf_modest"]:
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
    _missing = len(CORE_FUNDAMENTAL_KEYS) - count_core_metrics(financials)
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
