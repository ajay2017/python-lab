"""
Pre-Earnings Playbook advisor.

For each portfolio position with earnings in the next 30 days, generates
a structured playbook: analyst expectations, position risk vs estimated
earnings volatility, a specific pre-earnings action, and what to watch
during the report itself.
"""

import math as _math
import pytz as _pytz
from datetime import date as _date, datetime as _datetime

from stock_analyzer import revision_flag as _revflag
from stock_analyzer.earnings_stop_cover import (
    SECTOR_DEFAULTS,
    estimate_earnings_move,
    stop_cover_state,
)
from stock_analyzer.pillar_availability import unmeasured_pillars, verdict_basis

from stock_analyzer.constants import (
    EARNINGS_IMMINENT_DAYS,
    EARNINGS_URGENCY_SOON_DAYS,
    EARNINGS_BEAT_RATE_REDUCE_THRESHOLD,
    EARNINGS_BEAT_RATE_STRONG_THRESHOLD,
    EARNINGS_BEARISH_REACTION_COMPOSITE_GATE,
    EARNINGS_MIN_BEAT_RATE_ENTRY,
    EARNINGS_HOLD_OR_ADD_MIN_NET_REVISIONS,
    COMPOSITE_BUY,
    COMPOSITE_HOLD,
    COMPOSITE_STRONG_BUY,
    SINGLE_NAME_CEILING,
    SINGLE_NAME_TRIM_TRIGGER,
    EARNINGS_REDUCE_MIN_WEIGHT_PCT,
    EARNINGS_WEAK_TRIM_PCT,
)
from stock_analyzer.trim_sizing import (
    earnings_week_trim,
    fraction_trim_shares,
    exact_shares,
    fmt_shares,
    in_earnings_window,
    shares_to_trim,
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


def _finite_or_none(val):
    """float(val), or None for None / NaN / inf / non-numeric."""
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if _math.isfinite(f) else None


def analyst_fields(data: dict, ticker: str) -> dict:
    """Company name + analyst-estimate fields for one held bundle.

    load_bundle results carry `financials` / `name`, not `info`; raw fetch
    bundles still carry `info`, so it stays as the fallback. Numeric fields
    are finite floats or None (never NaN).
    """
    data = data or {}
    fin = data.get("financials")
    fin = fin if isinstance(fin, dict) else {}
    info = data.get("info")
    info = info if isinstance(info, dict) else {}

    def _pick(fin_key, info_key):
        v = _finite_or_none(fin.get(fin_key))
        return v if v is not None else _finite_or_none(info.get(info_key))

    company = (
        data.get("name") or info.get("shortName") or info.get("longName") or ticker
    )
    return {
        "company":     company,
        "fwd_eps":     _pick("forward_eps", "forwardEps"),
        "trail_eps":   _pick("eps", "trailingEps"),
        "fwd_pe":      _pick("forward_pe", "forwardPE"),
        "rev_growth":  _pick("revenue_growth", "revenueGrowth"),
        "earn_growth": _pick("earnings_growth", "earningsGrowth"),
    }


_SECTOR_DEFAULTS = SECTOR_DEFAULTS  # moved to earnings_stop_cover (finding #23)

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
    return estimate_earnings_move(risk_metrics, sector)


def _sh(n) -> str:
    """'1 share' / '5 shares' / '2.95 shares'."""
    return f"{fmt_shares(n)} share{'s' if n != 1 else ''}"


def _trim_sentence(n, held, pct) -> str:
    """The one wording for a whole-share trim sized by a percentage rule."""
    pct_of = 100.0 * n / held if held else 0.0
    return (
        f"**Sell {_sh(n)}** (about {pct_of:.0f}% of your {fmt_shares(held)} shares) "
        f"before the report. The rule's {pct:.0f}% trim rounds down to whole shares."
    )


def _rev_from_net(net_rev) -> dict:
    """A revision_flag-shaped reading from a bare net count (None = unknown)."""
    if net_rev is None:
        return {"state": "unknown", "net": None, "ups": None, "downs": None,
                "window_days": None}
    return {"state": "ok", "net": int(net_rev), "ups": None, "downs": None,
            "window_days": 90}


def _beat_flag(beat_rate, score, score_available, weight) -> bool:
    return bool(
        beat_rate is not None
        and score_available
        and beat_rate < EARNINGS_BEAT_RATE_REDUCE_THRESHOLD
        and score < COMPOSITE_BUY
        and weight >= EARNINGS_REDUCE_MIN_WEIGHT_PCT
    )


def _reaction_flag(reaction, score, score_available, weight) -> bool:
    return bool(
        reaction == "bearish"
        and score_available
        and score < EARNINGS_BEARISH_REACTION_COMPOSITE_GATE
        and weight >= EARNINGS_REDUCE_MIN_WEIGHT_PCT
    )


def flag_lines_for(rev, beat_rate, reaction, beat_flag, reaction_flag) -> list[str]:
    """Plain-text Watch-flag sentences (no markdown, no `$`). None sizes a trim."""
    lines = []
    rl = _revflag.flag_line(rev)
    if rl:
        lines.append(rl)
    if beat_flag and beat_rate is not None:
        lines.append(
            f"Beat history: {beat_rate:.0f}% of past reports beat estimates "
            f"(below {EARNINGS_BEAT_RATE_REDUCE_THRESHOLD:.0f}%), from pasted CNBC "
            "context. Flagged to watch, not acted on."
        )
    if reaction_flag:
        lines.append(
            "Post-earnings reaction history: bearish, from pasted CNBC context. "
            "Flagged to watch, not acted on."
        )
    return lines


_WATCH_PREFIX = "**Watch — no pre-earnings trim.** "


def _recommend(*args, **kwargs) -> tuple[str, str, str, str]:
    """Returns (action, priority, detail, institutional_lens).
    Actions: EXIT | REDUCE | MONITOR | HOLD | HOLD_OR_ADD. See _recommend_full."""
    return _recommend_core(*args, **kwargs)


def _recommend_full(*args, **kwargs) -> tuple[str, str, str, str, str | None]:
    """_recommend plus watch_reason: "stop" for the stop MONITORs;
    "revision_flag" / "beat_history" / "bearish_reaction" when a Watch flag
    replaced the general HOLD; else None."""
    out: dict = {}
    action, priority, detail, lens = _recommend_core(*args, _out=out, **kwargs)
    return action, priority, detail, lens, out.get("watch_reason")


def _recommend_core(
    days: int,
    score: float,
    weight: float,
    pnl_pct: float,
    gap_to_stop: float | None,
    net_rev: int | None,
    signal: str,
    shares: float,
    market_value: float,
    est_move: float,
    beat_rate: float | None = None,
    reaction: str | None = None,
    n_positions: int | None = None,
    sizing_shares: float | None = None,
    rev: dict | None = None,
    score_available: bool = True,
    _out: dict | None = None,
) -> tuple[str, str, str, str]:
    """
    Returns (action, priority, detail, institutional_lens). When `_out` is a
    dict, `_out["watch_reason"]` is set for the MONITOR returns.

    The analyst rating-change, beat-history and bearish-reaction signals are
    unmeasured, so they are Watch flags only: they never size a trim and never
    override EXIT / REDUCE / the stop MONITORs (owner decision 2026-10-30).
    A withheld composite (score_available False) fires no composite-conditioned rule.
    """
    if rev is None:
        rev = _rev_from_net(net_rev)
    # ── EXIT ─────────────────────────────────────────────────────────────────
    if "Sell" in signal or "Strong Sell" in signal:
        return (
            "EXIT",
            "HIGH",
            (
                f"You already have a **Sell signal** on this position. "
                "Holding through a binary earnings event with a bearish signal is one of the highest-risk "
                "decisions in portfolio management — you are hoping the event reverses the trend. "
                f"**Sell all {fmt_shares(shares)} shares before the report.** "
                "If the report is positive and you want back in, you can re-enter after confirmation."
            ),
            (
                "The cardinal rule in earnings management: never hold through a report when your "
                "composite signal is already bearish. You are paying for uncertainty you can't price. "
                "Institutional PMs call this 'event risk on a broken thesis' — the worst combination. "
                "A position can always be re-entered; a gap-down loss with a negative signal attached is very hard to recover from psychologically."
            ),
        )

    # Trim sizes use the unrounded share count when given, the same input the
    # Brief and Signals size from, so a fractional holding gets the same whole-
    # share trim on every surface. `shares` is now the exact count too.
    _sz = sizing_shares if sizing_shares is not None else shares

    # ── REDUCE — oversized position ───────────────────────────────────────────
    # Earnings week (0 <= days <= EARNINGS_IMMINENT_DAYS): the ONE earnings-week
    # rule (shared with the Brief and Signals) sizes the trim. Outside the
    # window the single-name ceiling rule applies. A 0-share trim never returns
    # REDUCE — it falls through to the next rules.
    if in_earnings_window(days):
        r = earnings_week_trim(weight, n_positions or 0, _sz)
        if r is not None and r["trim_shares"] >= 1:
            trim_sh  = r["trim_shares"]
            trim_val = round(trim_sh * (market_value / _sz)) if _sz > 0 else 0
            return (
                "REDUCE",
                "HIGH",
                (
                    f"Position is **{weight:.0f}% of your portfolio** — above the "
                    f"{r['floor_pct']:.1f}% earnings-week line for a binary earnings event. "
                    f"An estimated ±{est_move:.0f}% earnings move on a ${market_value:,.0f} position = "
                    f"±${est_move / 100 * market_value:,.0f} in a single session. "
                    f"**Trim to ~{r['target_pct']:.0f}% weight: sell {_sh(trim_sh)} (~${trim_val:,.0f})** before earnings. "
                    "Keep the core position for the upside scenario — just cap the binary risk."
                ),
                (
                    f"Institutional position sizing rule for binary events: size down going into an earnings "
                    f"report, regardless of conviction. The earnings-week rule scales with how many positions "
                    f"you hold (N={n_positions or 0}): it fires above {r['floor_pct']:.1f}% and trims to "
                    f"{r['target_pct']:.0f}%. "
                    "The reason is not that you're wrong — it's that earnings are inherently uncertain "
                    "even with perfect fundamental analysis. Managing size IS the risk management. "
                    "You can always add back after the report confirms the thesis."
                ),
            )
    elif weight > SINGLE_NAME_TRIM_TRIGGER:
        trim_sh  = shares_to_trim(_sz, weight, SINGLE_NAME_CEILING)
        trim_val = round(trim_sh * (market_value / _sz)) if _sz > 0 else 0
        if trim_sh >= 1:
            return (
                "REDUCE",
                "HIGH",
                (
                    f"Position is **{weight:.0f}% of your portfolio** — too concentrated for a binary earnings event. "
                    f"An estimated ±{est_move:.0f}% earnings move on a ${market_value:,.0f} position = "
                    f"±${est_move / 100 * market_value:,.0f} in a single session. "
                    f"**Trim to ~{SINGLE_NAME_CEILING:.0f}% weight: sell {_sh(trim_sh)} (~${trim_val:,.0f})** before earnings. "
                    "Keep the core position for the upside scenario — just cap the binary risk."
                ),
                (
                    f"Institutional position sizing rule for binary events: no single position above {SINGLE_NAME_CEILING:.0f}% of "
                    "portfolio going into an earnings report, regardless of conviction. "
                    "The reason is not that you're wrong — it's that earnings are inherently uncertain "
                    "even with perfect fundamental analysis. Managing size IS the risk management. "
                    "You can always add back after the report confirms the thesis."
                ),
            )

    # ── REDUCE — weak fundamentals ────────────────────────────────────────────
    trim_sh = fraction_trim_shares(_sz, EARNINGS_WEAK_TRIM_PCT)
    if (score_available and score < COMPOSITE_HOLD
            and weight >= EARNINGS_REDUCE_MIN_WEIGHT_PCT and trim_sh >= 1):
        return (
            "REDUCE",
            "HIGH",
            (
                f"Composite score **{score:.0f}/100** is in the Sell zone — going into a binary event "
                "with weak fundamentals compounds the risk significantly. "
                f"{_trim_sentence(trim_sh, _sz, EARNINGS_WEAK_TRIM_PCT)} "
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

    # Analyst rating-change, beat-history and bearish-reaction signals are
    # unmeasured Watch flags (owner decision 2026-10-30): none sizes a trim and
    # none overrides the rules above or the stop MONITORs below.
    _beat_flag_on = _beat_flag(beat_rate, score, score_available, weight)
    _react_flag_on = _reaction_flag(reaction, score, score_available, weight)

    # ── MONITOR — stop unavailable (data integrity gap) ──────────────────────
    if gap_to_stop is None:
        if _out is not None:
            _out["watch_reason"] = "stop"
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
    if stop_cover_state(gap_to_stop, est_move) == "at_risk":
        if _out is not None:
            _out["watch_reason"] = "stop"
        return (
            "MONITOR",
            "MEDIUM",
            (
                f"Your stop is **{gap_to_stop:.1f}% below current price** "
                f"while the estimated earnings move is ±{est_move:.0f}%. "
                "A negative surprise could gap through the stop in a single session — stops don't protect "
                "against overnight gaps. "
                "**Be prepared to exit at market open if the report is negative** — don't wait for the stop "
                "to fill at a price well below it."
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
    _net_for_add = rev.get("net") if rev.get("state") == "ok" else None
    if (score_available and score >= COMPOSITE_STRONG_BUY
            and _net_for_add is not None
            and _net_for_add >= EARNINGS_HOLD_OR_ADD_MIN_NET_REVISIONS):
        _hoa_extras = []
        if beat_rate is not None and beat_rate >= EARNINGS_BEAT_RATE_STRONG_THRESHOLD:
            _hoa_extras.append(f"historical beat rate **{beat_rate:.0f}%**")
        if reaction == "bullish":
            _hoa_extras.append("**bullish post-earnings reaction history**")
        _hoa_context = (
            " Combined with " + " and ".join(_hoa_extras) + ", this is a high-quality earnings setup."
            if _hoa_extras else ""
        )
        _hoa_head = (
            f"Strong setup heading into earnings: score **{score:.0f}/100** with "
            f"**+{_net_for_add} net analyst rating upgrades or initiations** over {'the last 90 days' if _revflag._win(rev) else 'recent months'}.{_hoa_context} "
        )
        _hoa_lens_head = (
            "A strong composite score with net analyst rating upgrades is a constructive "
            "setup into a report, though neither signal is a forecast of the result. "
            "Heading into a report, "
        )
        # S-D D6: only one sizing path hands out share counts, so this advisor
        # never sizes an add. With the report inside the imminent window it
        # isn't an add window at all: HOLD.
        if days <= EARNINGS_IMMINENT_DAYS:
            return (
                "HOLD",
                "OK",
                _hoa_head + "**Hold full position** — with the report this close, this isn't the window to add.",
                _hoa_lens_head
                + "institutional guidance is to hold through it rather than add into it. "
                "The risk is real but the setup is genuinely positive.",
            )
        return (
            "HOLD_OR_ADD",
            "OK",
            _hoa_head
            + "**Hold full position.** For an add, check 📈 Analysis — it shows whether "
            "Grow Today is calling one today, and its size.",
            _hoa_lens_head
            + "institutional guidance is to hold and potentially lean in. "
            "The risk is real but the setup is genuinely positive.",
        )

    # ── MONITOR — Watch flags (only where the chain would end in the general HOLD)
    _lines = flag_lines_for(rev, beat_rate, reaction, _beat_flag_on, _react_flag_on)
    if _lines:
        if _out is not None:
            _out["watch_reason"] = (
                "revision_flag" if _revflag.is_flagged(rev)
                else "beat_history" if _beat_flag_on else "bearish_reaction"
            )
        return (
            "MONITOR",
            "MEDIUM",
            _WATCH_PREFIX + " ".join(_lines) + " Hold with your stop in place.",
            (
                "These signals are being measured and have no demonstrated predictive "
                "record in this app yet, so the app sizes no trim from them. They are "
                "listed so you can weigh them yourself; the stop and the sizing rules "
                "above are what protect the position."
            ),
        )

    # ── HOLD — general case, score is decent ─────────────────────────────────
    _gap_str = f"gap to stop {gap_to_stop:.1f}%" if gap_to_stop is not None else "stop unavailable"
    _score_part = (
        f"No major pre-earnings risk flags. Score {score:.0f}/100, "
        if score_available else
        "Composite withheld, so the weak-score check and the CNBC beat-history / "
        "reaction checks couldn't run. "
    )
    if not score_available:
        _gap_str = _gap_str[:1].upper() + _gap_str[1:]
    return (
        "HOLD",
        "OK",
        (
            f"{_score_part}"
            f"{_gap_str}, and analyst rating changes are {_revflag.revisions_phrase(rev)}. "
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
    earnings_context: dict[str, dict] | None = None,
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
        # A withheld composite (Score None / Score Available False) is not 0:
        # no composite-conditioned rule may fire on it. Missing column = available.
        _sa = row.get("Score Available")
        score_available = (
            False if _sa is False or (_sa is not None and str(_sa).lower() in ("false", "0"))
            else _finite_or_none(row.get("Score")) is not None
        )
        signal     = str(row.get("Signal", ""))
        # Preserve None when stop data is unavailable — defaulting to 0 here
        # would falsely declare the position "at risk vs estimated move" when
        # the real issue is that there's no stop to evaluate.
        gap        = _f(row.get("Gap to Stop (%)"), None)
        stop_price = _f(row.get("Stop"), None)
        stop_type  = str(row.get("Stop Type", "ATR Stop"))
        shares     = exact_shares(row)
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

        ctx       = (earnings_context or {}).get(ticker) or {}
        beat_rate = ctx.get("beat_rate_pct")           # float | None
        reaction  = ctx.get("recent_reaction_direction")  # str | None

        rev_raw   = data.get("revisions") or {}
        rev_read  = _revflag.read(data)
        rm        = data.get("risk_metrics") or {}

        # load_bundle results carry `financials` / `name`, not `info`.
        _af          = analyst_fields(data, ticker)
        fwd_eps      = _af["fwd_eps"]
        trail_eps    = _af["trail_eps"]
        rev_growth   = _af["rev_growth"]
        earn_growth  = _af["earn_growth"]
        fwd_pe       = _af["fwd_pe"]
        company      = _af["company"]

        # None (not a fabricated 0) when the rating-change read is unknown.
        net_rev  = None if rev_read["state"] == "unknown" else rev_read["net"]
        ups_90   = rev_read["ups"]   # None when unknown (no fabricated 0)
        dns_90   = rev_read["downs"]
        _rev_win90 = _revflag._win(rev_read)
        latest_rev = rev_raw.get("latest", [])

        est_move = _estimate_move(rm, sector)
        earn_risk = round(est_move / 100 * mval)

        urgency = (
            "IMMINENT" if days_until <= EARNINGS_IMMINENT_DAYS
            else "SOON" if days_until <= EARNINGS_URGENCY_SOON_DAYS
            else "AHEAD"
        )

        action, priority, detail, inst_lens, watch_reason = _recommend_full(
            days_until, score, weight, pnl_pct,
            gap, net_rev, signal, shares, mval, est_move,
            beat_rate=beat_rate, reaction=reaction,
            n_positions=len(port_df),
            sizing_shares=shares,
            rev=rev_read,
            score_available=score_available,
        )
        _rflag = _revflag.is_flagged(rev_read)
        _bflag = _beat_flag(beat_rate, score, score_available, weight)
        _xflag = _reaction_flag(reaction, score, score_available, weight)

        watch = _SECTOR_WATCH.get(sector, _DEFAULT_WATCH)

        playbook.append({
            "ticker":        ticker,
            "company":       company,
            "earnings_date": earn_date,
            "days_until":    days_until,
            "urgency":       urgency,
            # CNBC enrichment (None when no context pasted)
            "beat_rate_pct":             beat_rate,
            "recent_reaction_direction": reaction,
            "recent_reaction_summary":   ctx.get("recent_reaction_summary"),
            "consensus_growth_pct":      ctx.get("consensus_growth_pct"),
            "what_to_watch_cnbc":        ctx.get("what_to_watch_cnbc"),
            "has_cnbc_context":          bool(ctx),
            # Position state
            "weight":        weight,
            "shares":        shares,
            "market_value":  mval,
            "pnl_pct":       pnl_pct,
            "score":         score if score_available else None,
            "score_available": score_available,
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
            "revision_window_90": _rev_win90,
            "latest_rev":    latest_rev[:3],
            # Volatility
            "est_move":      est_move,
            "earn_risk":     earn_risk,
            "stop_at_risk":  (stop_cover_state(gap, est_move) == "at_risk"),
            # Playbook
            "action":        action,
            "priority":      priority,
            "detail":        detail,
            "institutional_lens":  inst_lens,
            "watch_for":     watch,
            # Watch flags (never size a trim)
            "revision_flag":  _rflag,
            "revision_state": rev_read["state"],
            "beat_flag":      _bflag,
            "reaction_flag":  _xflag,
            "watch_reason":   watch_reason,
            "flag_lines":     flag_lines_for(rev_read, beat_rate, reaction, _bflag, _xflag),
        })

    playbook.sort(key=lambda x: x["days_until"])
    return playbook


def build_earnings_catalyst_candidates(
    watchlist_tickers: list,
    held_tickers: set,
    composites: dict,
    earnings_context: dict,
    today: _date | None = None,
    lookahead_days: int = 30,
    withheld_out: list | None = None,
) -> list:
    """
    Surfaces watchlist names near earnings with a strong historical setup.

    Filters (all must pass):
      - not held
      - earnings_context row exists for the ticker (CNBC data pasted)
      - beat_rate_pct >= EARNINGS_MIN_BEAT_RATE_ENTRY
      - recent_reaction_direction != 'bearish'
      - earnings_date within lookahead_days
      - composite score (bundle["total"]) >= COMPOSITE_BUY
      - the verdict is on the equity basis (pillar_availability.verdict_basis
        == "equity": business quality, valuation AND technical all measured).
        A WITHHELD name that clears the filters before this one (held, context,
        beat rate, reaction, date window, bundle present) is appended to the
        optional ``withheld_out`` list as {"ticker", "missing", "t_bars"} so
        the caller can disclose it (never silently dropped); the composite-bar
        filter has not run for it, so it is not claimed to be a would-be
        candidate. An ETF with a real verdict is skipped without being listed.

    Returns empty list when no candidates pass — normal until CNBC articles
    have been pasted for watchlist names via the Pre-Earnings paste flow.
    Sorted by rank_score desc (beat_rate * composite * reaction_multiplier).
    """
    if today is None:
        today = _today_et()

    candidates = []
    for ticker in watchlist_tickers:
        if ticker in held_tickers:
            continue

        ctx = earnings_context.get(ticker)
        if not ctx:
            continue

        beat_rate = ctx.get("beat_rate_pct")
        if beat_rate is None or beat_rate < EARNINGS_MIN_BEAT_RATE_ENTRY:
            continue

        reaction = ctx.get("recent_reaction_direction")
        if reaction == "bearish":
            continue

        earn_date_str = ctx.get("earnings_date")
        if not earn_date_str:
            continue
        try:
            earn_date = _datetime.strptime(str(earn_date_str), "%Y-%m-%d").date()
        except Exception:
            continue
        days_until = (earn_date - today).days
        if days_until < 0 or days_until > lookahead_days:
            continue

        bundle = composites.get(ticker)
        if bundle is None:
            continue
        # An unmeasured pillar (bq/val/technical) leaves a fabricated neutral 50
        # in `total`; a buy-type surface must not rank on it. Held out VISIBLY:
        # a withheld name that passed the earlier (context/date) filters is
        # recorded in `withheld_out` (when the caller supplies one) so the render
        # can say why it's missing. An ETF with a REAL verdict ("etf" basis) is
        # skipped silently instead: its `total` is the equity composite (fabricated
        # legs), so it must not be ranked here, but it is not "withheld" and
        # earnings-entry candidates are equities by construction.
        _basis = verdict_basis(bundle)
        if _basis != "equity":
            if _basis == "withheld" and withheld_out is not None:
                withheld_out.append({
                    "ticker":  ticker,
                    "missing": unmeasured_pillars(bundle),
                    "t_bars":  bundle.get("t_bars"),
                })
            continue
        score = _f(bundle.get("total"), 0.0)
        if score < COMPOSITE_BUY:
            continue

        reaction_mult = 1.2 if reaction == "bullish" else 1.0
        rank_score = beat_rate * score * reaction_mult

        candidates.append({
            "ticker":               ticker,
            "beat_rate":            beat_rate,
            "reaction":             reaction or "mixed",
            "earn_date":            earn_date.isoformat(),
            "days_until":           days_until,
            "score":                round(score, 1),
            "rank_score":           rank_score,
            "consensus_growth_pct": ctx.get("consensus_growth_pct"),
            "what_to_watch_cnbc":   ctx.get("what_to_watch_cnbc"),
        })

    return sorted(candidates, key=lambda x: x["rank_score"], reverse=True)
