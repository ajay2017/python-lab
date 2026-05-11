"""
Watchlist Buy Readiness Advisor.

For each ticker on the watchlist, generates a structured buy recommendation:
when conditions are right to open a position, when to wait, and when to remove
the ticker because the thesis is broken.
"""

from datetime import date as _date, datetime as _datetime

from stock_analyzer.constants import (
    PORTFOLIO_BETA_CEILING,
    PORTFOLIO_BETA_ELEVATED,
    TICKER_BETA_HIGH,
    TICKER_BETA_CRITICAL,
    SECTOR_CEILING,
    SECTOR_ELEVATED,
    SINGLE_NAME_CEILING,
    COMPOSITE_BUY,
    COMPOSITE_HOLD,
)


def _f(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


_ACTION_PRIORITY = {
    "REMOVE":             "HIGH",
    "HOLD_OFF_EARNINGS":  "MEDIUM",
    "ENTER_NOW":          "OK",
    "NEAR_ENTRY":         "MONITOR",
    "WAIT_ENTRY":         "MONITOR",
    "WAIT_CATALYST":      "MONITOR",
}


def _earn_days_until(earn_str: str | None) -> int | None:
    if not earn_str:
        return None
    try:
        return (_datetime.strptime(earn_str, "%Y-%m-%d").date() - _date.today()).days
    except Exception:
        return None


def _pct_from_entry(price: float, entry_hi: float) -> float | None:
    if not entry_hi or entry_hi <= 0:
        return None
    return (price - entry_hi) / entry_hi * 100


def _portfolio_risk_gate(ticker_beta, portfolio_ctx: dict | None) -> dict | None:
    """
    Check whether portfolio risk state breaches limits for opening a new
    position. ENTER_NOW from this advisor only sees the single stock; it must
    also respect the portfolio it would be opened into.

    Returns None if all clear, else:
      {"severity": "hard"|"soft", "kind": "sector"|"beta"|"mixed",
       "reason": <user-facing explanation>}

    Hard breach → downgrade ENTER_NOW to NEAR_ENTRY.
    Soft concern → keep ENTER_NOW but surface a caution banner.
    """
    if not portfolio_ctx:
        return None

    sector_wt    = _f(portfolio_ctx.get("sector_weight_pct"))
    port_beta    = portfolio_ctx.get("portfolio_beta")
    high_alerts  = portfolio_ctx.get("active_high_risk_alerts") or []
    sector_name  = portfolio_ctx.get("sector_of_ticker") or "this sector"
    grow_sectors = portfolio_ctx.get("grow_today_sectors") or set()

    # ── Hard breach: sector ≥ ceiling ────────────────────────────────────────
    if sector_wt >= SECTOR_CEILING:
        return {
            "severity": "hard",
            "kind":     "sector",
            "reason": (
                f"{sector_name} is already at **{sector_wt:.0f}%** of your portfolio. "
                f"Opening here would push concentration beyond the {SECTOR_CEILING:.0f}% "
                "institutional single-sector ceiling — a single sector shock could swamp the rest of the book."
            ),
        }

    # ── Hard breach: high portfolio beta + critical ticker beta ─────────────
    if (port_beta is not None and ticker_beta is not None
            and port_beta > PORTFOLIO_BETA_CEILING and ticker_beta > TICKER_BETA_CRITICAL):
        return {
            "severity": "hard",
            "kind":     "beta",
            "reason": (
                f"Portfolio beta is already **{port_beta:.2f}** (above the {PORTFOLIO_BETA_CEILING:.1f} risk-team ceiling). "
                f"Adding a β **{ticker_beta:.2f}** name compounds market sensitivity — "
                "in a 10% correction this position would amplify the existing drag, not diversify it."
            ),
        }

    # ── Soft concerns: warn but keep ENTER_NOW ───────────────────────────────
    soft: list[str] = []
    if sector_wt >= SECTOR_ELEVATED:
        soft.append(
            f"{sector_name} already at {sector_wt:.0f}% of portfolio — consider a half-size entry."
        )
    if (port_beta is not None and ticker_beta is not None
            and port_beta > PORTFOLIO_BETA_ELEVATED and ticker_beta > TICKER_BETA_HIGH):
        soft.append(
            f"Portfolio beta {port_beta:.2f} + ticker β {ticker_beta:.2f} — use conservative sizing."
        )
    if high_alerts:
        soft.append(
            f"Active HIGH risk alert{'s' if len(high_alerts) > 1 else ''} "
            f"({', '.join(high_alerts[:2])}) — resolve in Portfolio → Risk Advisor first."
        )
    # Same-sector overlap with Grow Today: following both stacks sector exposure.
    if sector_name and sector_name in grow_sectors:
        soft.append(
            f"Daily Briefing → Grow Today is also recommending a **{sector_name}** pick today. "
            "Opening both stacks sector exposure — pick the higher-conviction setup, "
            "or wait a day so each sector trade gets evaluated on its own merits."
        )

    if soft:
        return {"severity": "soft", "kind": "mixed", "reason": " ".join(soft)}

    return None


def build_watchlist_recommendation(
    ticker: str,
    data: dict,
    portfolio_ctx: dict | None = None,
) -> dict:
    """
    Returns a recommendation dict for a single watchlist candidate.

    portfolio_ctx (optional): {
        "sector_of_ticker": str, "sector_weight_pct": float,
        "portfolio_beta": float | None,
        "active_high_risk_alerts": list[str],   # titles of HIGH risk recs
    }
    When supplied, ENTER_NOW is gated against portfolio-level risk state so the
    advisor never blindly says "enter" while a hard concentration or beta limit
    is breached.

    Keys: ticker, action, priority, score, signal, price, entry_lo, entry_hi,
          stop, rr, earn_days, ps, title, readiness_pct, summary, detail,
          conditions_met, conditions_missing, institutional_lens,
          portfolio_caution (str | None — soft warning rendered inside the card)
    """
    score       = _f(data.get("total"))
    rec_label   = str((data.get("rec") or {}).get("label", ""))
    price       = _f(data.get("current_price"))
    entry_lo    = data.get("entry_lo")
    entry_hi    = data.get("entry_hi")
    stop        = _f(data.get("stop"))
    targets     = data.get("targets") or {}
    earn_str    = data.get("earnings")
    t_signals   = data.get("t_signals") or {}
    f_signals   = data.get("f_signals") or {}
    revisions   = data.get("revisions") or {}

    earn_days   = _earn_days_until(earn_str)
    earn_soon   = earn_days is not None and 0 <= earn_days <= 7

    # Risk/reward
    base_target = _f(targets.get("base")) if targets.get("base") else None
    rr = None
    if price and stop and base_target and price > stop and base_target > price:
        rr = round((base_target - price) / (price - stop), 2)

    # Entry zone analysis
    in_zone  = False
    near_zone = False
    pct_above = None
    if price and entry_hi and entry_lo:
        in_zone   = entry_lo <= price <= entry_hi
        pct_above = _pct_from_entry(price, entry_hi)
        near_zone = pct_above is not None and pct_above <= 3.0

    net_rev = int(_f(revisions.get("net", 0)))

    # ── REMOVE ────────────────────────────────────────────────────────────────
    if "Sell" in rec_label or "Strong Sell" in rec_label or score < 44:
        return _card(
            ticker, "REMOVE", score, rec_label, price, entry_lo, entry_hi,
            stop, rr, earn_days,
            title=f"{ticker} — Thesis Broken (score {score:.0f}/100)",
            summary=(
                f"Composite score {score:.0f}/100 with a **{rec_label}** signal. "
                "The thesis that put this on the watchlist has not materialised — "
                "or conditions have deteriorated since you added it."
            ),
            detail=(
                f"Score {score:.0f}/100 and a {rec_label} signal means both technical "
                "and fundamental signals are warning you away. "
                "There is no thesis to wait on here. "
                "**Remove from watchlist.** If you still believe in the long-term story, "
                "set a price alert for when the composite score recovers above 55 — "
                "then re-evaluate from scratch with fresh data."
            ),
            conditions_met=[],
            conditions_missing=[
                f"Score {score:.0f}/100 — below the 44 threshold for any buy consideration",
                f"Signal: {rec_label} — active bearish signal",
            ],
            institutional_lens=(
                "Institutional framework for watchlist management: a stock on your watchlist is a "
                "'thesis in progress.' When the composite signal turns bearish, the thesis has failed. "
                "Keeping a broken-thesis stock on the watchlist introduces cognitive bias — "
                "you start hoping for recovery rather than waiting for a genuine setup. "
                "The cost of removing is zero; the cost of anchoring to a broken thesis is real. "
                "Remove it. You can always re-add when conditions improve."
            ),
        )

    # ── HOLD OFF — EARNINGS IMMINENT ─────────────────────────────────────────
    if earn_soon and score >= 55:
        return _card(
            ticker, "HOLD_OFF_EARNINGS", score, rec_label, price, entry_lo, entry_hi,
            stop, rr, earn_days,
            title=f"{ticker} — Good Setup, But Earnings in {earn_days}d",
            summary=(
                f"Score {score:.0f}/100 is strong and the setup looks constructive. "
                f"But earnings are {earn_days} day(s) away — opening a position now "
                "means your first session is a binary earnings event."
            ),
            detail=(
                f"The technical and fundamental picture is solid (score {score:.0f}/100). "
                f"However, with earnings in {earn_days} day(s), the next major move "
                "is determined by whether the company beats or misses consensus — "
                "not by any of the signals that drove this score. "
                "**Wait for the earnings report. Then evaluate the post-earnings setup.** "
                "A beat-and-raise typically creates a new entry opportunity on the post-print "
                "pullback. A miss gives you data that may change the thesis entirely."
            ),
            conditions_met=[
                f"Score {score:.0f}/100 — above threshold",
                f"Signal: {rec_label}",
            ],
            conditions_missing=[
                f"Earnings in {earn_days}d — binary risk makes entry timing poor",
                "Wait for post-earnings price action to establish new entry zone",
            ],
            institutional_lens=(
                "Opening a new position into an earnings event is paying for uncertainty twice — "
                "you haven't validated the thesis yet, and you're immediately facing a binary outcome. "
                "Institutional PM approach: 'New positions get established after confirmation, not before binary events.' "
                "If the report is strong, you'll have a clear thesis and better information. "
                "If it's weak, you'll have avoided a potentially significant loss on a brand-new position. "
                "Patience here is not timidity — it's precision."
            ),
        )

    # ── ENTER NOW ─────────────────────────────────────────────────────────────
    # R:R must be VALIDATED (>= 2:1), not merely "unknown." A missing R:R means
    # we don't have a target price — that's incomplete homework, not a green light.
    if score >= COMPOSITE_BUY and (in_zone or near_zone) and rr is not None and rr >= 2.0:
        # Portfolio risk gate: ENTER_NOW from this advisor only sees the single
        # stock — it must also respect portfolio-level concentration and beta.
        ticker_beta = (data.get("risk_metrics") or {}).get("beta")
        gate        = _portfolio_risk_gate(ticker_beta, portfolio_ctx)

        if gate and gate["severity"] == "hard":
            # Hard breach — downgrade to NEAR_ENTRY with explicit portfolio-fit messaging
            return _card(
                ticker, "NEAR_ENTRY", score, rec_label, price, entry_lo, entry_hi,
                stop, rr, earn_days,
                title=f"{ticker} — Setup Ready, But Portfolio Fit Blocks Entry",
                summary=(
                    f"Score {score:.0f}/100 and price in entry zone — the stock-level setup is a go. "
                    "However, opening this position now would breach a portfolio risk limit."
                ),
                detail=(
                    f"{gate['reason']} "
                    "**Do not open the position at full size.** "
                    "Either wait for the portfolio risk state to normalise "
                    "(trim the over-concentrated sector, or reduce beta exposure first), "
                    "or open with a deliberately small half/quarter position so the "
                    "limit isn't pushed further out of band. "
                    "The watchlist alert is right; the timing relative to your book is wrong."
                ),
                conditions_met=[
                    f"Score {score:.0f}/100 — above 65 threshold",
                    f"Signal: {rec_label}",
                    f"Price {'in' if in_zone else 'near'} entry zone (${entry_lo:.2f}–${entry_hi:.2f})" if entry_lo else "Entry zone aligned",
                    f"R:R {rr:.1f}:1 — above 2:1 minimum" if rr else "Risk/reward acceptable",
                ],
                conditions_missing=[
                    f"Portfolio fit: {gate['reason']}",
                ],
                institutional_lens=(
                    "Stock-level signals and portfolio-level fit are two separate filters. "
                    "A stock can be a perfect entry in isolation and a poor decision relative to "
                    "what you already own. Institutional risk frameworks apply both gates: "
                    "thesis confirmation first, portfolio fit second. "
                    "When the fit gate fails, the discipline is to wait — or to size down enough "
                    "that the position doesn't push the portfolio further out of risk tolerance. "
                    "Skipping this check is how concentration and beta drift quietly accumulate."
                ),
                portfolio_caution=gate["reason"],
            )

        soft_caution = gate["reason"] if (gate and gate["severity"] == "soft") else None

        return _card(
            ticker, "ENTER_NOW", score, rec_label, price, entry_lo, entry_hi,
            stop, rr, earn_days,
            title=f"{ticker} — Ready to Enter",
            summary=(
                f"Score {score:.0f}/100 · price is {'in' if in_zone else 'near'} the entry zone"
                + (f" · R:R {rr:.1f}:1" if rr else "")
                + ". All conditions align for opening a position."
            ),
            detail=(
                f"The setup you were waiting for has arrived. Score {score:.0f}/100 confirms "
                f"the thesis is intact. "
                f"Price {'is inside' if in_zone else f'is within 3% of'} the entry zone "
                f"(${entry_lo:.2f}–${entry_hi:.2f}). "
                + (f"Risk/reward is {rr:.1f}:1 — well above the 2:1 minimum. " if rr else "")
                + f"**Open the position.** Use the position sizing below to calibrate shares. "
                "Place the ATR stop immediately on entry — do not hold without a stop."
            ),
            conditions_met=[
                f"Score {score:.0f}/100 — above 65 threshold",
                f"Signal: {rec_label}",
                f"Price {'in' if in_zone else 'near'} entry zone (${entry_lo:.2f}–${entry_hi:.2f})" if entry_lo else "Entry zone aligned",
                f"R:R {rr:.1f}:1 — above 2:1 minimum" if rr else "Risk/reward acceptable",
                f"No imminent earnings risk" if not earn_soon else "",
            ],
            conditions_missing=(
                [f"Portfolio fit caution: {soft_caution}"] if soft_caution else []
            ),
            institutional_lens=(
                "The hardest discipline in investing is not the analysis — it's the execution. "
                "When a watchlist stock finally hits its entry zone with a confirmed thesis, "
                "the tendency is to hesitate: 'Maybe I should wait a bit longer.' "
                "Professional traders call this 'entry paralysis' — it's the flip side of chasing. "
                "You did the work by building the watchlist. The setup you planned for is here. "
                "Execute the plan. Adjust the stop as the position matures."
            ),
            portfolio_caution=soft_caution,
        )

    # ── NEAR ENTRY ───────────────────────────────────────────────────────────
    if score >= COMPOSITE_BUY and pct_above is not None and pct_above <= 8:
        return _card(
            ticker, "NEAR_ENTRY", score, rec_label, price, entry_lo, entry_hi,
            stop, rr, earn_days,
            title=f"{ticker} — Approaching Entry Zone ({pct_above:+.1f}% above zone)",
            summary=(
                f"Score {score:.0f}/100 and strong signal. Price is {abs(pct_above):.1f}% "
                "above the ideal entry zone — close but not yet optimal. "
                "Set a limit order or watch for a small pullback."
            ),
            detail=(
                f"The thesis is solid (score {score:.0f}/100, {rec_label}). "
                f"Price at ${price:.2f} is {abs(pct_above):.1f}% above the entry zone top "
                f"(${entry_hi:.2f}). Chasing 5% above entry erodes your R:R meaningfully. "
                "**Set a limit order at $"
                + (f"{entry_hi:.2f}" if entry_hi else "entry zone top")
                + " and monitor.** "
                "If price keeps moving away, the entry zone will update as ATR adjusts — "
                "do not chase. If it pulls back to the zone, you're positioned perfectly."
            ),
            conditions_met=[
                f"Score {score:.0f}/100 — above 65 threshold",
                f"Signal: {rec_label}",
                f"Thesis intact — within 8% of entry zone",
            ],
            conditions_missing=[
                f"Price {abs(pct_above):.1f}% above entry zone — wait for pullback or set limit",
                (f"R:R {rr:.1f}:1 — currently below 2:1 target at this price" if rr and rr < 2.0 else ""),
            ],
            institutional_lens=(
                "Entry discipline is where most retail investors give back the edge their analysis creates. "
                "Professional trading desks rule: if a stock has moved more than 5% above its technical entry zone, "
                "the position size is cut by 50% OR you wait for the pullback. "
                "The reason: buying 8% above your planned entry means your stop is now 8% further from breakeven "
                "and your R:R may no longer justify the trade. "
                "The stock hasn't changed — your entry price has. Respect the levels."
            ),
        )

    # ── WAIT FOR ENTRY ───────────────────────────────────────────────────────
    if score >= COMPOSITE_BUY:
        dist_str = f"{abs(pct_above):.1f}% above entry zone" if pct_above is not None else "above entry zone"
        return _card(
            ticker, "WAIT_ENTRY", score, rec_label, price, entry_lo, entry_hi,
            stop, rr, earn_days,
            title=f"{ticker} — Strong Thesis, Wrong Price ({dist_str})",
            summary=(
                f"Score {score:.0f}/100 confirms the thesis is intact. "
                "But the price has run past the optimal entry zone. "
                "Wait for a pullback — do not chase."
            ),
            detail=(
                f"Everything about {ticker} looks right at the fundamental and technical level "
                f"(score {score:.0f}/100, {rec_label}). "
                "The problem is price: the stock has moved away from the entry zone, "
                "which means your stop would be set further from current price and "
                "your risk/reward is now worse than when you added it to the watchlist. "
                "**Do not chase. Hold the watchlist position and wait for a pullback to the entry zone.** "
                "Set a price alert at $" + (f"{entry_hi:.2f}" if entry_hi else "the entry zone") + ". "
                "Re-evaluate if the score changes materially."
            ),
            conditions_met=[
                f"Score {score:.0f}/100 — above 65 threshold",
                f"Signal: {rec_label}",
                "Thesis intact",
            ],
            conditions_missing=[
                f"Price {dist_str} — entry R:R no longer optimal",
                "Set a price alert at the entry zone; revisit on pullback",
            ],
            institutional_lens=(
                "The watchlist is where patience creates alpha. Institutional PMs explicitly separate "
                "'I like this stock' from 'I like this stock AT THIS PRICE.' "
                "A stock with a great thesis trading 15% above your entry zone is not a buy — "
                "it's a stock to buy when it corrects back to your level. "
                "The patience to wait for the right price is what separates disciplined investors "
                "from those who are always buying tops and selling bottoms. "
                "Set the alert. Move on. Come back when price cooperates."
            ),
        )

    # ── WAIT FOR CATALYST ────────────────────────────────────────────────────
    return _card(
        ticker, "WAIT_CATALYST", score, rec_label, price, entry_lo, entry_hi,
        stop, rr, earn_days,
        title=f"{ticker} — Monitoring for Catalyst (score {score:.0f}/100)",
        summary=(
            f"Score {score:.0f}/100 — above the remove threshold but not yet at "
            "conviction level (65+). Mixed signals suggest waiting for a clearer setup."
        ),
        detail=(
            f"Score {score:.0f}/100 places {ticker} in the 'monitoring' zone — "
            "fundamentals and technical signals are mixed. "
            "This is not a clear buy setup yet. "
            "**Keep on watchlist and wait for one of:** a composite score break above 65, "
            "a positive earnings surprise that re-rates the fundamentals, "
            "or a sector rotation that confirms the thesis. "
            "The stock is worth watching but does not yet meet the bar for capital deployment."
        ),
        conditions_met=[
            f"Score {score:.0f}/100 — above remove threshold",
            "Thesis partially supported",
        ],
        conditions_missing=[
            f"Score {score:.0f}/100 — needs to reach 65+ for conviction entry",
            f"Signal {rec_label} — needs Buy or Strong Buy",
            "No specific catalyst confirmed yet",
        ],
        institutional_lens=(
            "A 'monitoring' position on the watchlist is intentional capital preservation. "
            "Institutional approach: the watchlist is not just a holding area — it's an active "
            "queue of theses at various stages of validation. "
            "A score below 65 means the market hasn't yet confirmed your thesis. "
            "Deploying capital before confirmation is speculation; waiting for confirmation is investing. "
            "The catalysts to watch for: earnings revision momentum turning positive, "
            "a sector re-rating, or a technical breakout above a well-defined resistance level. "
            "Until one of those arrives, the watchlist is the right home for this name."
        ),
    )


def _card(
    ticker, action, score, signal, price, entry_lo, entry_hi,
    stop, rr, earn_days, title, summary, detail,
    conditions_met, conditions_missing, institutional_lens,
    portfolio_caution: str | None = None,
) -> dict:
    priority = _ACTION_PRIORITY.get(action, "MONITOR")

    # Readiness score 0–100: how close is this to a full green-light?
    pts = 0
    if score >= COMPOSITE_BUY:    pts += 35
    elif score >= 55:             pts += 20
    elif score >= COMPOSITE_HOLD: pts += 10
    if action in ("ENTER_NOW",):       pts += 35
    elif action in ("NEAR_ENTRY",):    pts += 25
    elif action in ("WAIT_ENTRY",):    pts += 15
    elif action in ("WAIT_CATALYST",): pts += 5
    if rr and rr >= 2.0:              pts += 20
    elif rr and rr >= 1.5:            pts += 10
    if earn_days is None or earn_days > 14: pts += 10
    elif earn_days > 7:                     pts += 5
    readiness_pct = min(100, pts)

    return {
        "ticker":             ticker,
        "action":             action,
        "priority":           priority,
        "score":              score,
        "signal":             signal,
        "price":              price,
        "entry_lo":           entry_lo,
        "entry_hi":           entry_hi,
        "stop":               stop,
        "rr":                 rr,
        "earn_days":          earn_days,
        "readiness_pct":      readiness_pct,
        "title":              title,
        "summary":            summary,
        "detail":             detail,
        "conditions_met":     [c for c in conditions_met if c],
        "conditions_missing": [c for c in conditions_missing if c],
        "institutional_lens":       institutional_lens,
        "portfolio_caution":  portfolio_caution,
    }
