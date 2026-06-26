"""
2-ticker side-by-side comparison engine.

Takes two load_all() bundles plus optional portfolio context and produces
a structured side-by-side breakdown plus a one-line verdict. Used by the
Compare page in the sidebar to help the user decide between similar
candidates (e.g. OKTA vs CRWD for cybersecurity exposure).

The verdict logic considers composite score as the primary signal, then
falls back to sub-factor and portfolio-fit analysis when scores are close.
Returns "tie" with a "compare sub-factors" message rather than picking
arbitrarily — the user should never get a recommendation that the data
doesn't support.
"""

from stock_analyzer.constants import (
    PORTFOLIO_BETA_ELEVATED,
    PORTFOLIO_BETA_CEILING,
    SECTOR_ELEVATED,
    SECTOR_CEILING,
)


def _f(val, default=None):
    """Safe float conversion — None / NaN → default."""
    if val is None:
        return default
    try:
        v = float(val)
        return default if (v != v) else v
    except (TypeError, ValueError):
        return default


def _winner(a, b, higher_better: bool = True, tolerance: float = 0.0) -> str | None:
    """
    Return 'a', 'b', 'tie', or None (unknown).
    tolerance allows treating near-equal values as ties (absolute diff).
    """
    av = _f(a)
    bv = _f(b)
    if av is None and bv is None:
        return None
    if av is None:
        return "b"
    if bv is None:
        return "a"
    if abs(av - bv) <= tolerance:
        return "tie"
    if higher_better:
        return "a" if av > bv else "b"
    return "a" if av < bv else "b"


def _fmt_pct(val) -> str:
    v = _f(val)
    return f"{v*100:+.1f}%" if v is not None else "—"


def _fmt_money(val) -> str:
    v = _f(val)
    if v is None:
        return "—"
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.1f}B"
    if v >= 1e6:  return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


def _fmt_num(val, fmt: str = "{:.2f}") -> str:
    v = _f(val)
    return fmt.format(v) if v is not None else "—"


# Signal label → rank (higher = more bullish, for winner picking)
_SIGNAL_RANK = {
    "Strong Buy":  5,
    "Buy":         4,
    "Hold":        3,
    "Weak Hold":   2,
    "Sell":        1,
    "Strong Sell": 0,
    "Avoid":       0,
}


def _signal_winner(label_a: str, label_b: str) -> str | None:
    ra = _SIGNAL_RANK.get(str(label_a or "").strip(), -1)
    rb = _SIGNAL_RANK.get(str(label_b or "").strip(), -1)
    if ra < 0 and rb < 0:
        return None
    if ra == rb:
        return "tie"
    return "a" if ra > rb else "b"


def _trend_winner(trend_a: str, trend_b: str) -> str | None:
    """Rank trend labels — Strong Uptrend best, Strong Downtrend worst."""
    rank = {
        "Strong Uptrend":   5,
        "Uptrend":          4,
        "Sideways":         3,
        "Mixed":            3,
        "Weak":             2,
        "Downtrend":        1,
        "Strong Downtrend": 0,
    }
    ra = rank.get(str(trend_a or "").strip(), -1)
    rb = rank.get(str(trend_b or "").strip(), -1)
    if ra < 0 and rb < 0:
        return None
    if ra == rb:
        return "tie"
    return "a" if ra > rb else "b"


def _row(label: str, value_a, value_b, winner=None, note: str = "") -> dict:
    return {
        "label":   label,
        "value_a": value_a,
        "value_b": value_b,
        "winner":  winner,
        "note":    note,
    }


def build_comparison(
    bundle_a: dict,
    bundle_b: dict,
    ticker_a: str,
    ticker_b: str,
    port_df=None,
) -> dict:
    """
    Build a structured comparison between two load_all() bundles.

    Returns:
      ticker_a, ticker_b    : symbols (uppercase)
      name_a,   name_b      : company names
      sections              : list of {name, rows}
      verdict               : {preferred, reason, confidence}
      portfolio_fit         : {a, b} — per-ticker portfolio-fit note
    """
    name_a = bundle_a.get("name") or ticker_a
    name_b = bundle_b.get("name") or ticker_b
    fin_a  = bundle_a.get("financials") or {}
    fin_b  = bundle_b.get("financials") or {}
    rm_a   = bundle_a.get("risk_metrics") or {}
    rm_b   = bundle_b.get("risk_metrics") or {}

    sections: list[dict] = []

    # ── Headline ────────────────────────────────────────────────────────────
    sections.append({
        "name": "Headline",
        "rows": [
            _row("Composite Score",
                 f"{bundle_a.get('total', 0):.0f}/100",
                 f"{bundle_b.get('total', 0):.0f}/100",
                 _winner(bundle_a.get("total"), bundle_b.get("total"), True, tolerance=2)),
            _row("Signal",
                 (bundle_a.get("rec") or {}).get("label", "—"),
                 (bundle_b.get("rec") or {}).get("label", "—"),
                 _signal_winner(
                     (bundle_a.get("rec") or {}).get("label"),
                     (bundle_b.get("rec") or {}).get("label"),
                 )),
        ],
    })

    # ── Overview ────────────────────────────────────────────────────────────
    sections.append({
        "name": "Overview",
        "rows": [
            _row("Sector",
                 bundle_a.get("sector") or "—",
                 bundle_b.get("sector") or "—",
                 None),
            _row("Industry",
                 bundle_a.get("industry") or "—",
                 bundle_b.get("industry") or "—",
                 None),
            _row("Market Cap",
                 _fmt_money(bundle_a.get("market_cap")),
                 _fmt_money(bundle_b.get("market_cap")),
                 None),  # informational — bigger isn't inherently better
            _row("Current Price",
                 f"${bundle_a.get('current_price', 0):.2f}" if bundle_a.get("current_price") else "—",
                 f"${bundle_b.get('current_price', 0):.2f}" if bundle_b.get("current_price") else "—",
                 None),
        ],
    })

    # ── Technicals ──────────────────────────────────────────────────────────
    t_sig_a = bundle_a.get("t_signals") or {}
    t_sig_b = bundle_b.get("t_signals") or {}
    sections.append({
        "name": "Technicals",
        "rows": [
            _row("Technical Score",
                 f"{bundle_a.get('t_score', 0):.0f}/100",
                 f"{bundle_b.get('t_score', 0):.0f}/100",
                 _winner(bundle_a.get("t_score"), bundle_b.get("t_score"), True, tolerance=3)),
            _row("RSI",
                 _fmt_num(t_sig_a.get("RSI"), "{:.0f}"),
                 _fmt_num(t_sig_b.get("RSI"), "{:.0f}"),
                 None),  # RSI sweet spot is 40-60 — winner depends on intent
            _row("Trend",
                 t_sig_a.get("Trend") or "—",
                 t_sig_b.get("Trend") or "—",
                 _trend_winner(t_sig_a.get("Trend"), t_sig_b.get("Trend"))),
        ],
    })

    # ── Fundamentals ────────────────────────────────────────────────────────
    sections.append({
        "name": "Fundamentals",
        "rows": [
            _row("Fundamental Score",
                 f"{bundle_a.get('f_score', 0):.0f}/100",
                 f"{bundle_b.get('f_score', 0):.0f}/100",
                 _winner(bundle_a.get("f_score"), bundle_b.get("f_score"), True, tolerance=3)),
            _row("Forward P/E",
                 _fmt_num(fin_a.get("forward_pe"), "{:.1f}"),
                 _fmt_num(fin_b.get("forward_pe"), "{:.1f}"),
                 _winner(fin_a.get("forward_pe"), fin_b.get("forward_pe"),
                         higher_better=False, tolerance=0.5)),
            _row("FCF Yield",
                 f"{fin_a['fcf_yield']:.2f}%" if fin_a.get("fcf_yield") is not None else "—",
                 f"{fin_b['fcf_yield']:.2f}%" if fin_b.get("fcf_yield") is not None else "—",
                 _winner(fin_a.get("fcf_yield"), fin_b.get("fcf_yield"), True, tolerance=0.3)),
            _row("Revenue Growth",
                 _fmt_pct(fin_a.get("revenue_growth")),
                 _fmt_pct(fin_b.get("revenue_growth")),
                 _winner(fin_a.get("revenue_growth"), fin_b.get("revenue_growth"),
                         True, tolerance=0.01)),
            _row("Profit Margin",
                 _fmt_pct(fin_a.get("profit_margins")),
                 _fmt_pct(fin_b.get("profit_margins")),
                 _winner(fin_a.get("profit_margins"), fin_b.get("profit_margins"),
                         True, tolerance=0.005)),
            _row("Debt/Equity",
                 _fmt_num(fin_a.get("debt_to_equity"), "{:.0f}"),
                 _fmt_num(fin_b.get("debt_to_equity"), "{:.0f}"),
                 _winner(fin_a.get("debt_to_equity"), fin_b.get("debt_to_equity"),
                         higher_better=False, tolerance=5)),
        ],
    })

    # ── Sentiment & Analyst ─────────────────────────────────────────────────
    rev_a = (bundle_a.get("revisions") or {}).get("net")
    rev_b = (bundle_b.get("revisions") or {}).get("net")
    sections.append({
        "name": "Sentiment & Analyst",
        "rows": [
            _row("Sentiment Score",
                 f"{bundle_a.get('s_score', 0):.0f}/100",
                 f"{bundle_b.get('s_score', 0):.0f}/100",
                 _winner(bundle_a.get("s_score"), bundle_b.get("s_score"), True, tolerance=5)),
            _row("News Sentiment (avg)",
                 f"{bundle_a.get('avg_sent', 0):+.2f}" if bundle_a.get("avg_sent") is not None else "—",
                 f"{bundle_b.get('avg_sent', 0):+.2f}" if bundle_b.get("avg_sent") is not None else "—",
                 _winner(bundle_a.get("avg_sent"), bundle_b.get("avg_sent"),
                         True, tolerance=0.05)),
            _row("Analyst Net Revisions (90d)",
                 f"{int(rev_a):+d}" if rev_a is not None else "—",
                 f"{int(rev_b):+d}" if rev_b is not None else "—",
                 _winner(rev_a, rev_b, True, tolerance=0)),
        ],
    })

    # ── Risk ────────────────────────────────────────────────────────────────
    beta_a, beta_b = rm_a.get("beta"), rm_b.get("beta")
    sections.append({
        "name": "Risk",
        "rows": [
            _row("Beta",
                 _fmt_num(beta_a, "{:.2f}"),
                 _fmt_num(beta_b, "{:.2f}"),
                 _winner(beta_a, beta_b, higher_better=False, tolerance=0.05)),
            _row("Sharpe Ratio",
                 _fmt_num(rm_a.get("sharpe"), "{:.2f}"),
                 _fmt_num(rm_b.get("sharpe"), "{:.2f}"),
                 _winner(rm_a.get("sharpe"), rm_b.get("sharpe"), True, tolerance=0.05)),
            _row("Annual Volatility",
                 f"{rm_a['ann_volatility']:.0f}%" if rm_a.get("ann_volatility") is not None else "—",
                 f"{rm_b['ann_volatility']:.0f}%" if rm_b.get("ann_volatility") is not None else "—",
                 _winner(rm_a.get("ann_volatility"), rm_b.get("ann_volatility"),
                         higher_better=False, tolerance=1)),
            _row("Max Drawdown",
                 f"{rm_a['max_drawdown']:.0f}%" if rm_a.get("max_drawdown") is not None else "—",
                 f"{rm_b['max_drawdown']:.0f}%" if rm_b.get("max_drawdown") is not None else "—",
                 _winner(rm_a.get("max_drawdown"), rm_b.get("max_drawdown"), True, tolerance=1)),
        ],
    })

    # ── Setup ───────────────────────────────────────────────────────────────
    price_a, price_b = bundle_a.get("current_price"), bundle_b.get("current_price")
    stop_a,  stop_b  = bundle_a.get("stop"), bundle_b.get("stop")
    # R:R = (target - price) / (price - stop) — higher is better
    def _rr(price, stop, target):
        if not price or not stop or not target or price <= stop:
            return None
        try:
            return (target - price) / (price - stop)
        except (TypeError, ZeroDivisionError):
            return None
    rr_a = _rr(price_a, stop_a, (bundle_a.get("targets") or {}).get("base"))
    rr_b = _rr(price_b, stop_b, (bundle_b.get("targets") or {}).get("base"))
    sections.append({
        "name": "Setup",
        "rows": [
            _row("Entry Zone",
                 (f"${bundle_a.get('entry_lo'):.2f}–${bundle_a.get('entry_hi'):.2f}"
                  if bundle_a.get("entry_lo") and bundle_a.get("entry_hi") else "—"),
                 (f"${bundle_b.get('entry_lo'):.2f}–${bundle_b.get('entry_hi'):.2f}"
                  if bundle_b.get("entry_lo") and bundle_b.get("entry_hi") else "—"),
                 None),
            _row("Stop Loss",
                 f"${stop_a:.2f}" if stop_a else "—",
                 f"${stop_b:.2f}" if stop_b else "—",
                 None),
            _row("R:R Ratio",
                 f"{rr_a:.1f}:1" if rr_a is not None else "—",
                 f"{rr_b:.1f}:1" if rr_b is not None else "—",
                 _winner(rr_a, rr_b, True, tolerance=0.2)),
            _row("Earnings",
                 bundle_a.get("earnings") or "—",
                 bundle_b.get("earnings") or "—",
                 None),
        ],
    })

    # ── Verdict ─────────────────────────────────────────────────────────────
    verdict = _compute_verdict(
        bundle_a, bundle_b, ticker_a, ticker_b, fin_a, fin_b, rm_a, rm_b, port_df,
    )

    # ── Portfolio fit ───────────────────────────────────────────────────────
    portfolio_fit = _portfolio_fit(bundle_a, bundle_b, ticker_a, ticker_b, port_df)

    return {
        "ticker_a":      ticker_a.upper(),
        "ticker_b":      ticker_b.upper(),
        "name_a":        name_a,
        "name_b":        name_b,
        "sections":      sections,
        "verdict":       verdict,
        "portfolio_fit": portfolio_fit,
    }


def _compute_verdict(bundle_a, bundle_b, ticker_a, ticker_b,
                     fin_a, fin_b, rm_a, rm_b, port_df) -> dict:
    """
    Pick a preferred ticker with reasoning. Composite score is the primary
    signal; sub-factors break ties.
    """
    score_a = _f(bundle_a.get("total"), 0)
    score_b = _f(bundle_b.get("total"), 0)
    gap = abs(score_a - score_b)

    # Sub-factor reasons assembled regardless of winner so the verdict can
    # cite specific evidence rather than just "higher score."
    reasons: list[str] = []
    if fin_a.get("fcf_yield") is not None and fin_b.get("fcf_yield") is not None:
        if abs(fin_a["fcf_yield"] - fin_b["fcf_yield"]) >= 0.5:
            if fin_a["fcf_yield"] > fin_b["fcf_yield"]:
                reasons.append(
                    f"{ticker_a} better FCF yield ({fin_a['fcf_yield']:.1f}% vs {fin_b['fcf_yield']:.1f}%)"
                )
            else:
                reasons.append(
                    f"{ticker_b} better FCF yield ({fin_b['fcf_yield']:.1f}% vs {fin_a['fcf_yield']:.1f}%)"
                )
    if rm_a.get("beta") is not None and rm_b.get("beta") is not None:
        if abs(rm_a["beta"] - rm_b["beta"]) >= 0.15:
            if rm_a["beta"] < rm_b["beta"]:
                reasons.append(f"{ticker_a} lower beta ({rm_a['beta']:.2f} vs {rm_b['beta']:.2f})")
            else:
                reasons.append(f"{ticker_b} lower beta ({rm_b['beta']:.2f} vs {rm_a['beta']:.2f})")
    if rm_a.get("sharpe") is not None and rm_b.get("sharpe") is not None:
        if abs(rm_a["sharpe"] - rm_b["sharpe"]) >= 0.2:
            if rm_a["sharpe"] > rm_b["sharpe"]:
                reasons.append(f"{ticker_a} stronger Sharpe ({rm_a['sharpe']:.2f} vs {rm_b['sharpe']:.2f})")
            else:
                reasons.append(f"{ticker_b} stronger Sharpe ({rm_b['sharpe']:.2f} vs {rm_a['sharpe']:.2f})")

    # Composite-tight tie — defer to the user with specific factors to weigh.
    if gap < 3:
        if reasons:
            return {
                "preferred":  "tie",
                "reason": (
                    f"Composites are nearly identical ({score_a:.0f} vs {score_b:.0f}). "
                    f"Tie-breakers: {' · '.join(reasons[:3])}."
                ),
                "confidence": "low",
            }
        return {
            "preferred":  "tie",
            "reason": (
                f"Composites are nearly identical ({score_a:.0f} vs {score_b:.0f}) and "
                "sub-factors are also close. Decide on portfolio fit (sector overlap, "
                "current weight) rather than stock-level metrics."
            ),
            "confidence": "low",
        }

    # Clear winner
    pref       = "a" if score_a > score_b else "b"
    pref_tick  = ticker_a if pref == "a" else ticker_b
    confidence = "high" if gap >= 10 else "medium"
    return {
        "preferred":  pref,
        "reason": (
            f"Prefer **{pref_tick}** — composite {max(score_a, score_b):.0f} vs "
            f"{min(score_a, score_b):.0f} (+{gap:.0f})"
            + (f". Supporting evidence: {' · '.join(reasons[:3])}." if reasons else ".")
        ),
        "confidence": confidence,
    }


def _portfolio_fit(bundle_a, bundle_b, ticker_a, ticker_b, port_df) -> dict:
    """
    Per-ticker portfolio-fit notes — already held? sector concentration?
    Returns {a: note, b: note}; either may be empty when no concerns surface.
    """
    out = {"a": "", "b": ""}
    if port_df is None or port_df.empty:
        return out

    held = set(str(t).upper() for t in port_df.get("Ticker", []))
    total_val = float(port_df.get("Market Value", [0]).sum() or 1)

    for key, ticker, bundle in [("a", ticker_a, bundle_a), ("b", ticker_b, bundle_b)]:
        ticker_u = ticker.upper()
        notes = []
        if ticker_u in held:
            row = port_df[port_df["Ticker"] == ticker_u].iloc[0]
            weight = float(row.get("Weight (%)", 0) or 0)
            notes.append(f"⚠️ Already held at {weight:.1f}% of portfolio")
        sector = bundle.get("sector") or ""
        if sector and "Sector" in port_df.columns:
            # Margin-aware gate basis (Phase 2): sum the net-capital Gate Weight
            # when present so this entry-fit ceiling matches the hard gate; falls
            # back to equity "Weight (%)" otherwise. See gating_denominator.
            _gcol = "Gate Weight (%)" if "Gate Weight (%)" in port_df.columns else "Weight (%)"
            sector_weight = float(
                port_df[port_df["Sector"] == sector][_gcol].sum() or 0
            )
            if sector_weight >= SECTOR_CEILING:
                notes.append(
                    f"🚫 Sector ({sector}) at {sector_weight:.0f}% — "
                    f"hard ceiling {SECTOR_CEILING:.0f}% breached"
                )
            elif sector_weight >= SECTOR_ELEVATED:
                notes.append(
                    f"⚠️ Sector ({sector}) at {sector_weight:.0f}% — "
                    f"elevated (target ≤ {SECTOR_ELEVATED:.0f}%)"
                )
        out[key] = " · ".join(notes)
    return out
