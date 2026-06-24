"""
Pre-Earnings Playbook advisor.

For each portfolio position with earnings in the next 30 days, generates
a structured playbook: analyst expectations, position risk vs estimated
earnings volatility, a specific pre-earnings action, and what to watch
during the report itself.
"""

import pytz as _pytz
from datetime import date as _date, datetime as _datetime

from stock_analyzer.constants import (
    EARNINGS_IMMINENT_DAYS,
    EARNINGS_URGENCY_SOON_DAYS,
)

_ET = _pytz.timezone("America/New_York")


def _today_et() -> _date:
    """ET-localized today (Streamlit Cloud runs UTC)."""
    return _datetime.now(_ET).date()


def _f(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


_SECTOR_DEFAULTS = {
    "Semiconductors":  10.0,
    "AI & Data":        9.0,
    "AI & Cloud":       8.0,
    "Cybersecurity":    9.0,
    "Healthcare":       8.0,
    "Energy":           6.0,
    "Defense":          5.0,
    "Financials":       6.0,
    "Clean Energy":     8.0,
    "Consumer Tech":    8.0,
    "EV & Auto":       10.0,
    "Enterprise Tech":  7.0,
}

_SECTOR_WATCH = {
    "Semiconductors": [
        "**Data center / AI revenue split** — this segment drives the growth premium; any deceleration moves the stock",
        "**Gross margin trajectory** — expanding margins signal pricing power; contracting = competitive pressure or cost headwinds",
        "**Inventory commentary** — excess inventory = near-term pricing and volume risk",
        "**Next-quarter guidance** — management's forward outlook moves the stock more than the actual results",
    ],
    "AI & Cloud": [
        "**Cloud segment revenue growth rate** — acceleration vs deceleration vs consensus",
        "**Operating margin expansion** — when does the heavy AI investment phase start converting to profit?",
        "**AI workload demand signals** — hyperscaler capex commentary is the read-through",
        "**Next-quarter guidance** — most important number in the release",
    ],
    "AI & Data": [
        "**Revenue growth and Rule of 40** (growth % + FCF margin %)",
        "**Net Revenue Retention** — above 120% signals strong existing-customer expansion",
        "**Customer count and ACV growth** — size of new logos and deal values",
        "**Next-quarter guidance** — forward revenue guidance sets the tone",
    ],
    "Cybersecurity": [
        "**Annual Recurring Revenue (ARR) and growth rate**",
        "**Net Revenue Retention rate** — above 115% = strong customer stickiness",
        "**Platform consolidation wins** — customers moving to broader platform = switching cost moat building",
        "**Next-quarter guidance** — especially any macro commentary on deal timelines",
    ],
    "Healthcare": [
        "**Pipeline or trial readout commentary** — binary catalysts that can gap the stock 20%+",
        "**Drug pricing and reimbursement signals** — regulatory and payer commentary",
        "**Revenue from key products** vs consensus — concentrate on the top 1-2 revenue drivers",
        "**R&D spend trajectory** — investing for growth vs managing margins",
    ],
    "Financials": [
        "**Net Interest Margin (NIM)** — key sensitivity to rate environment",
        "**Loan growth and credit quality** — provision build signals stress; reserve release signals confidence",
        "**Fee income and trading revenue** — volatile lines that drive beats/misses",
        "**Capital return commentary** — buybacks and dividend guidance",
    ],
    "Energy": [
        "**Production volumes vs guidance** — operational execution",
        "**Realized prices vs benchmark** — hedging gains/losses",
        "**Capex discipline commentary** — is management prioritising returns or growth?",
        "**Balance sheet and debt levels** — leverage sensitivity to commodity prices",
    ],
    "Defense": [
        "**Book-to-bill ratio** — backlog growth above 1.0× signals demand exceeds supply",
        "**Program execution** — delays or cost overruns on key contracts",
        "**Government budget commentary** — continuations, new awards, potential cancellations",
        "**International revenue** — foreign military sales and export licence progress",
    ],
    "Clean Energy": [
        "**Installation volumes vs target** — execution of project pipeline",
        "**Module costs and efficiency improvements** — technology cost curve trajectory",
        "**Policy / IRA subsidy commentary** — regulatory environment for forward projects",
        "**Project backlog depth** — contracted revenue visibility for the next 12-24 months",
    ],
    "Consumer Tech": [
        "**Revenue vs consensus** — top-line beat/miss sets direction",
        "**Gross margin trend** — platform margins expanding = operating leverage",
        "**User growth or engagement metrics** — the leading indicator before revenue",
        "**New product cycle commentary** — hardware supercycle timing or services attach rate",
    ],
    "EV & Auto": [
        "**Delivery volumes vs consensus** — the headline number that moves the stock most",
        "**Gross margin per vehicle** — unit economics trajectory toward profitability",
        "**Energy / services revenue mix** — higher-margin segments de-risk the model",
        "**Guidance and production ramp commentary**",
    ],
}

_DEFAULT_WATCH = [
    "**Revenue vs consensus** — top-line beat/miss sets the initial direction",
    "**Gross margin trend** — expanding = operating leverage; contracting = cost or pricing pressure",
    "**Forward guidance** — management's next-quarter outlook moves the stock more than the actual results",
    "**Analyst revision impact** — watch price target changes in the 48 hours post-report",
]


def _estimate_move(risk_metrics: dict, sector: str) -> float:
    """Estimate typical earnings-day move from daily VaR × 3."""
    var95 = abs(_f(risk_metrics.get("var_95")))
    est = max(3.0, min(25.0, var95 * 3.0))
    if est <= 3.1:
        est = _SECTOR_DEFAULTS.get(sector, 7.0)
    return round(est, 1)


def _recommend(
    days: int,
    score: float,
    weight: float,
    pnl_pct: float,
    gap_to_stop: float | None,
    net_rev: int,
    signal: str,
    shares: int,
    market_value: float,
    est_move: float,
) -> tuple[str, str, str, str]:
    """
    Returns (action, priority, detail, institutional_lens).
    Actions: EXIT | REDUCE | MONITOR | HOLD | HOLD_OR_ADD
    """
    # ── EXIT ─────────────────────────────────────────────────────────────────
    if "Sell" in signal or "Strong Sell" in signal:
        return (
            "EXIT",
            "HIGH",
            (
                f"You already have a **Sell signal** on this position. "
                "Holding through a binary earnings event with a bearish signal is one of the highest-risk "
                "decisions in portfolio management — you are hoping the event reverses the trend. "
                f"**Sell all {shares:,} shares before the report.** "
                "If the report is positive and you want back in, you can re-enter after confirmation."
            ),
            (
                "The cardinal rule in earnings management: never hold through a report when your "
                "composite signal is already bearish. You are paying for uncertainty you can't price. "
                "Institutional PMs call this 'event risk on a broken thesis' — the worst combination. "
                "A position can always be re-entered; a gap-down loss with a negative signal attached is very hard to recover from psychologically."
            ),
        )

    # ── REDUCE — oversized position ───────────────────────────────────────────
    if weight > 18:
        target_w  = 15.0
        trim_frac = (weight - target_w) / weight
        trim_sh   = max(1, int(shares * trim_frac))
        trim_val  = round(trim_frac * market_value)
        return (
            "REDUCE",
            "HIGH",
            (
                f"Position is **{weight:.0f}% of your portfolio** — too concentrated for a binary earnings event. "
                f"An estimated ±{est_move:.0f}% earnings move on a ${market_value:,.0f} position = "
                f"±${est_move / 100 * market_value:,.0f} in a single session. "
                f"**Trim to ~15% weight: sell {trim_sh:,} shares (~${trim_val:,.0f})** before earnings. "
                "Keep the core position for the upside scenario — just cap the binary risk."
            ),
            (
                "Institutional position sizing rule for binary events: no single position above 15% of "
                "portfolio going into an earnings report, regardless of conviction. "
                "The reason is not that you're wrong — it's that earnings are inherently uncertain "
                "even with perfect fundamental analysis. Managing size IS the risk management. "
                "You can always add back after the report confirms the thesis."
            ),
        )

    # ── REDUCE — weak fundamentals ────────────────────────────────────────────
    if score < 44 and weight >= 5:
        trim_sh = max(1, shares // 2)
        return (
            "REDUCE",
            "HIGH",
            (
                f"Composite score **{score:.0f}/100** is in the Sell zone — going into a binary event "
                "with weak fundamentals compounds the risk significantly. "
                f"**Trim 50%: sell {trim_sh:,} shares** before the report. "
                "If earnings surprise to the upside and fundamentals improve, re-enter from a smaller base. "
                "If they confirm the weakness, you've protected yourself from a larger loss."
            ),
            (
                "A low composite score heading into earnings means both your technical and fundamental "
                "signals are warning you — then you're adding a binary event on top. "
                "Institutional PM framework: each risk layer (fundamental weakness + binary event) independently "
                "justifies reducing size. Both together mandate action. "
                "Position size is the only risk control you have complete authority over."
            ),
        )

    # ── REDUCE — negative revision momentum ───────────────────────────────────
    if net_rev <= -2 and weight >= 5:
        trim_sh = max(1, int(shares * 0.35))
        return (
            "REDUCE",
            "MEDIUM",
            (
                f"Analysts have been **cutting estimates** heading into this report "
                f"(net revisions: {net_rev:+d} over 90 days). "
                "Negative revision momentum into earnings is one of the strongest miss-risk signals. "
                f"**Trim 35%: sell {trim_sh:,} shares** before the report. "
                "Negative revisions suggest analysts already know something is wrong — "
                "the report may confirm it."
            ),
            (
                "Earnings revision momentum is one of the most reliable alpha factors in institutional investing. "
                "When analysts are cutting estimates heading into a report, it's usually because they have "
                "channel checks, supplier data, or management conversations that signal trouble. "
                "Quantitative research teams show that stocks with negative pre-earnings revisions miss consensus "
                "at twice the rate of those with positive revisions. Reduce before the report; re-evaluate after."
            ),
        )

    # ── MONITOR — stop unavailable (data integrity gap) ──────────────────────
    if gap_to_stop is None:
        return (
            "MONITOR",
            "MEDIUM",
            (
                "**Stop data unavailable** for this position — the earnings-risk gate "
                f"cannot evaluate stop vs estimated ±{est_move:.0f}% earnings move. "
                "Set a manual stop in your broker before the report and **be at your "
                "terminal for the pre-market open**."
            ),
            (
                "Earnings reports without a defined stop level is the worst combination of "
                "binary event risk plus unstructured risk management. Institutional rule: "
                "no position into earnings without a stop. If price feed is the issue, "
                "treat it as an unscoped-risk position and reduce size on principle."
            ),
        )

    # ── MONITOR — stop close to estimated move ───────────────────────────────
    if gap_to_stop < est_move * 0.85:
        return (
            "MONITOR",
            "MEDIUM",
            (
                f"Your stop is **{gap_to_stop:.1f}% below current price** "
                f"while the estimated earnings move is ±{est_move:.0f}%. "
                "A negative surprise could gap through the stop in a single session — stops don't protect "
                "against overnight gaps. "
                "**Be prepared to exit at market open if the report is negative** — don't wait for the stop "
                "to trigger at a price that may be 10% lower than it shows."
            ),
            (
                "Stops are daily risk management tools — they don't protect against overnight gaps. "
                "When earnings day volatility is larger than your gap to stop, the stop becomes theoretical. "
                "Professional trading desks uses a simple rule: if estimated earnings move > gap to stop, "
                "treat the position as 'manual stop' — be at your terminal for the pre-market move "
                "and exit if the thesis is broken, at whatever price is available."
            ),
        )

    # ── HOLD_OR_ADD — high conviction + positive revisions ───────────────────
    if score >= 68 and net_rev >= 2:
        return (
            "HOLD_OR_ADD",
            "OK",
            (
                f"Strong setup heading into earnings: score **{score:.0f}/100** with "
                f"**+{net_rev} net analyst upgrades** in 90 days — analysts are raising estimates, "
                "not cutting. Positive revision momentum into earnings is historically the strongest "
                "predictor of a beat-and-raise. "
                "**Hold full position.** If conviction is high, a small add (5–10% of current size) "
                "on any pre-earnings weakness could be warranted."
            ),
            (
                "'Earnings revision momentum' is one of the most robust alpha factors in quantitative "
                "investing — stocks with rising analyst estimates consistently outperform those with "
                "falling estimates. When you have both a strong composite score AND rising estimates "
                "heading into a report, Institutional guidance is to hold and potentially lean in. "
                "The risk is real but the setup is genuinely positive."
            ),
        )

    # ── HOLD — general case, score is decent ─────────────────────────────────
    _gap_str = f"gap to stop {gap_to_stop:.1f}%" if gap_to_stop is not None else "stop unavailable"
    return (
        "HOLD",
        "OK",
        (
            f"No major pre-earnings risk flags. Score {score:.0f}/100, "
            f"{_gap_str}, and revisions are neutral. "
            "**Hold current position with stops in place.** "
            "The report is the next major thesis checkpoint — watch forward guidance closely."
        ),
        (
            "Earnings reports are thesis checkpoints, not just price catalysts. "
            "The number that matters most is almost never the EPS — it's the guidance. "
            "Institutional analysts always ask: 'Did management raise, maintain, or cut guidance?' "
            "A beat with cut guidance is a sell. A miss with raised guidance is often a hold or buy. "
            "Listen for management tone, not just the headline numbers."
        ),
    )


def build_earnings_playbook(
    port_df,
    held_data: dict,
    today: _date | None = None,
    lookahead_days: int = 30,
) -> list[dict]:
    """
    Returns a list of playbook dicts for all holdings with earnings
    within the next `lookahead_days` days, sorted by urgency.
    """
    if today is None:
        today = _today_et()

    playbook = []

    for _, row in port_df.iterrows():
        ticker     = row["Ticker"]
        weight     = _f(row.get("Weight (%)"))
        mval       = _f(row.get("Market Value"))
        pnl_pct    = _f(row.get("P&L (%)"))
        score      = _f(row.get("Score"))
        signal     = str(row.get("Signal", ""))
        # Preserve None when stop data is unavailable — defaulting to 0 here
        # would falsely declare the position "at risk vs estimated move" when
        # the real issue is that there's no stop to evaluate.
        gap        = _f(row.get("Gap to Stop (%)"), None)
        stop_price = _f(row.get("Stop"), None)
        stop_type  = str(row.get("Stop Type", "ATR Stop"))
        shares     = int(_f(row.get("Shares", 0)))
        sector     = str(row.get("Sector", "Other"))

        data = held_data.get(ticker) or {}
        earn_str = data.get("earnings")
        if not earn_str:
            continue

        try:
            earn_date = _datetime.strptime(earn_str, "%Y-%m-%d").date()
        except Exception:
            continue

        days_until = (earn_date - today).days
        if days_until < 0 or days_until > lookahead_days:
            continue

        info      = data.get("info") or {}
        rev       = data.get("revisions") or {}
        rm        = data.get("risk_metrics") or {}

        fwd_eps      = info.get("forwardEps")
        trail_eps    = info.get("trailingEps")
        rev_growth   = info.get("revenueGrowth")
        earn_growth  = info.get("earningsGrowth")
        fwd_pe       = info.get("forwardPE")
        company      = info.get("shortName") or info.get("longName") or ticker

        net_rev  = int(_f(rev.get("net", 0)))
        ups_90   = int(_f(rev.get("upgrades_90d", 0)))
        dns_90   = int(_f(rev.get("downgrades_90d", 0)))
        latest_rev = rev.get("latest", [])

        est_move = _estimate_move(rm, sector)
        earn_risk = round(est_move / 100 * mval)

        urgency = (
            "IMMINENT" if days_until <= EARNINGS_IMMINENT_DAYS
            else "SOON" if days_until <= EARNINGS_URGENCY_SOON_DAYS
            else "AHEAD"
        )

        action, priority, detail, inst_lens = _recommend(
            days_until, score, weight, pnl_pct,
            gap, net_rev, signal, shares, mval, est_move,
        )

        watch = _SECTOR_WATCH.get(sector, _DEFAULT_WATCH)

        playbook.append({
            "ticker":        ticker,
            "company":       company,
            "earnings_date": earn_date,
            "days_until":    days_until,
            "urgency":       urgency,
            # Position state
            "weight":        weight,
            "shares":        shares,
            "market_value":  mval,
            "pnl_pct":       pnl_pct,
            "score":         score,
            "signal":        signal,
            "gap_to_stop":   gap,
            "stop_price":    stop_price,
            "stop_type":     stop_type,
            # Analyst expectations
            "fwd_eps":       fwd_eps,
            "trail_eps":     trail_eps,
            "rev_growth":    rev_growth,
            "earn_growth":   earn_growth,
            "fwd_pe":        fwd_pe,
            "net_rev":       net_rev,
            "ups_90":        ups_90,
            "dns_90":        dns_90,
            "latest_rev":    latest_rev[:3],
            # Volatility
            "est_move":      est_move,
            "earn_risk":     earn_risk,
            "stop_at_risk":  (gap is not None and gap < est_move * 0.85),
            # Playbook
            "action":        action,
            "priority":      priority,
            "detail":        detail,
            "institutional_lens":  inst_lens,
            "watch_for":     watch,
        })

    playbook.sort(key=lambda x: x["days_until"])
    return playbook
