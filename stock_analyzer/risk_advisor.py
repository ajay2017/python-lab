"""
Institutional-style Risk Advisor.

Reads 7 portfolio-level risk metrics plus per-stock risk data and produces
ranked, evidence-backed action recommendations — each with a problem
statement, root-cause tickers, a specific actionable step, a quantified
expected outcome, and a Institutional Lens teaching moment.
"""

import numpy as np
import pandas as pd

from collections import defaultdict

from stock_analyzer.constants import (
    PORTFOLIO_BETA_CEILING,
    PORTFOLIO_BETA_ELEVATED,
    PORTFOLIO_BETA_TARGET,
    SECTOR_CEILING,
    SECTOR_ELEVATED,
    SINGLE_NAME_CEILING,
    WEAK_CONVICTION_SCORE,
    UNCLASSIFIED_SECTOR,
)


def _f(val, default=0.0):
    """Safe float conversion — returns default for None / NaN."""
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


def _beta_ok(val):
    """Return the beta float or None — never coerce None to 0."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if (f != f) else f
    except (TypeError, ValueError):
        return None


def build_risk_advisor_recommendations(
    port_df: pd.DataFrame,
    held_data: dict,
    port_risk: dict,
    h_rets: dict,
    portfolio_value: float,
    gate_denom: float | None = None,
) -> list[dict]:
    """
    Returns a ranked list of recommendation dicts.  Each dict has:

      priority        : "HIGH" | "MEDIUM" | "OK"
      type            : "beta" | "sharpe" | "volatility" | "drawdown" | "tail_risk" | "ok_*"
      title           : short headline
      problem         : plain-English problem statement with $ impact
      root_cause      : which tickers are driving it (text)
      root_tickers    : [{"ticker", "value", "weight", "label"}]
      recommendation  : specific, actionable advice
      expected_outcome: quantified result if recommendation is followed
      institutional_lens    : teaching moment / professional context
    """
    if not port_risk or port_df.empty:
        return []

    recs: list[dict] = []

    # Use None-preserving coercion for every metric that gates a recommendation.
    # Coercing missing data to 0.0 produces confidently-wrong HIGH-priority
    # panels (e.g. "Sharpe 0.00 — Risk Taken Is Not Being Rewarded" when the
    # cache is cold). Each downstream branch now gates on `is not None`.
    beta    = _beta_ok(port_risk.get("beta"))
    ann_vol = _beta_ok(port_risk.get("ann_volatility"))
    sharpe  = _beta_ok(port_risk.get("sharpe"))
    sortino = _beta_ok(port_risk.get("sortino"))
    var_pct  = _beta_ok(port_risk.get("var_95_pct"))    # negative %
    cvar_pct = _beta_ok(port_risk.get("cvar_95_pct"))   # negative %
    max_dd   = _beta_ok(port_risk.get("max_drawdown"))  # negative %

    # Don't fabricate a $50k portfolio when the real value is missing —
    # every "saving ~$X in a 10% correction" number would otherwise be
    # plausible-shaped but unrelated to the user's actual capital. Bail
    # with empty list so the caller renders a "portfolio value missing"
    # banner instead of confident wrong-data numbers.
    if portfolio_value is None or portfolio_value <= 0:
        return []
    pv = portfolio_value

    # ── Concentration-rec basis: whatever `gate_denom` the caller passes ───────
    # This is a basis-agnostic seam: the recs scale weights by _acct_f = pv/gate_denom
    # and size trims off _gd. CURRENT POLICY (2026-07-09, reqs G-19): app.py passes
    # gate_denom = invested equity, so _acct_f = 1.0, _gd = pv → the recs are pure
    # EQUITY-basis (no scaling). The seam is retained so the basis stays a one-line
    # policy choice at the call site (a prior 2026-06-26 policy passed net capital
    # here to tighten under margin; reversed — do not re-tighten without a policy
    # discussion). Only the two concentration recs use it — beta/Sharpe recs keep
    # equity weight (a different risk dimension).
    _acct_f     = (pv / gate_denom) if (gate_denom and 0 < gate_denom < pv) else 1.0
    _acct_basis = _acct_f > 1.0
    _gd         = gate_denom if _acct_basis else pv   # denominator for trim-$ math

    # ── Per-ticker risk lookup ────────────────────────────────────────────────
    tr_map: dict[str, dict] = {}
    for _, row in port_df.iterrows():
        t  = row["Ticker"]
        rm = (held_data.get(t) or {}).get("risk_metrics") or {}
        tr_map[t] = {
            "beta":         _beta_ok(rm.get("beta")),
            "sharpe":       _f(rm.get("sharpe")),
            "sortino":      _f(rm.get("sortino")),
            "max_drawdown": _f(rm.get("max_drawdown")),
            "var_95":       _f(rm.get("var_95")),
            "weight":       _f(row.get("Weight (%)")),
            "market_value": _f(row.get("Market Value")),
            "price":        _f(row.get("Price")),
            "pnl_pct":      _f(row.get("P&L (%)")),
            "score":        _f(row.get("Score")),
            # None-preserving composite (never coerced to 0.0) — a priced name
            # with a missing score must NOT masquerade as "lowest conviction" in
            # the trim ranking below. `_beta_ok` is a generic None-safe float.
            "score_raw":    _beta_ok(row.get("Score")),
            "signal":       str(row.get("Signal", "")),
            "ret_6mo":      _f(h_rets.get(t)),
            "sector":       str(row.get("Sector", "Other")),
        }

    # ── 1. BETA ───────────────────────────────────────────────────────────────
    if beta is not None:
        if beta > PORTFOLIO_BETA_CEILING:
            beta_priority = "HIGH"
        elif beta > PORTFOLIO_BETA_ELEVATED:
            beta_priority = "MEDIUM"
        else:
            beta_priority = "OK"

        if beta_priority in ("HIGH", "MEDIUM"):
            contribs = []
            for t, tr in tr_map.items():
                b = tr["beta"]
                w = tr["weight"]
                if b is not None and b > 0 and w > 0:
                    contribs.append({
                        "ticker":       t,
                        "value":        round(b, 2),
                        "weight":       w,
                        "market_value": tr["market_value"],
                        "label":        f"β {b:.2f}  ·  {w:.1f}% of portfolio",
                        "_contrib":     b * w / 100,
                    })
            contribs.sort(key=lambda x: -x["_contrib"])
            top_beta = contribs[:3]

            target  = PORTFOLIO_BETA_ELEVATED
            excess  = beta - target
            loss_10 = excess * pv * 0.10
            loss_20 = excess * pv * 0.20
            top_names = "  ·  ".join(
                f"**{x['ticker']}** (β {x['value']:.2f})"
                for x in top_beta
            ) if top_beta else "your highest-weight positions"

            # Compute exact new portfolio beta after selling 50% of the top contributor.
            # Formula: new_beta = (beta - w_i × b_i × f) / (1 - w_i × f)
            # where w_i is weight as fraction and f is the sell fraction.
            trim_ticker = top_beta[0]["ticker"] if top_beta else None
            if top_beta:
                _tw   = top_beta[0]["weight"] / 100          # weight as fraction
                _tb   = top_beta[0]["value"]                  # position beta
                _tf   = 0.50                                   # sell 50%
                # Guard against w_i × f → 1 (would remove >99.9% of portfolio).
                # Explicit if/else replaces a fragile `and/or` ternary that could
                # return the wrong branch if `beta` were ever falsy.
                if _tw * _tf > 0.999:
                    _new_beta = beta
                else:
                    _new_beta = round((beta - _tw * _tb * _tf) / (1 - _tw * _tf), 2)
                _new_beta    = round(max(float(_new_beta), 0.3), 2)
                _beta_drop   = round(beta - _new_beta, 2)
                _trim_dollar = round(top_beta[0]["market_value"] * _tf)
                _saved_10    = round(_beta_drop * pv * 0.10)
                _saved_20    = round(_beta_drop * pv * 0.20)
            else:
                _new_beta = round(beta * 0.85, 2)
                _beta_drop = round(beta - _new_beta, 2)
                _trim_dollar = _saved_10 = _saved_20 = 0

            recs.append({
                "priority": beta_priority,
                "type":     "beta",
                "title":    f"Portfolio Beta {beta:.2f} — Amplifying Market Risk",
                "problem": (
                    f"Your portfolio moves **{beta:.2f}× the S&P 500**. In a 10% market correction "
                    f"you'd lose approximately **${beta * pv * 0.10:,.0f}** versus "
                    f"**${pv * 0.10:,.0f}** for a market-weight index fund — "
                    f"**${loss_10:,.0f} of unnecessary extra loss** for the same market event. "
                    f"In a 20% bear-market correction that gap widens to **${loss_20:,.0f}**."
                ),
                "root_cause": (
                    f"Highest weighted-beta contributors: {top_names}. "
                    "High-beta names dominate the book, amplifying both rallies and corrections."
                ),
                "root_tickers": top_beta,
                "recommendation": (
                    (
                        f"Sell 50% of **{trim_ticker}** (~${_trim_dollar:,.0f}): "
                        f"portfolio beta drops **{beta:.2f} → {_new_beta:.2f}** "
                        f"(saving ~${_saved_10:,.0f} in a 10% correction). "
                    ) if trim_ticker else ""
                ) + (
                    f"To reach target beta of {target:.1f}, also consider adding 8–10% in a "
                    "defensive sector (Healthcare XLV, Consumer Staples XLP, or Utilities XLU) "
                    "to dilute beta without fully exiting high-conviction names."
                ),
                "expected_outcome": (
                    (
                        f"Trimming 50% of **{trim_ticker}** reduces portfolio beta "
                        f"from {beta:.2f} to **{_new_beta:.2f}** — "
                        f"saving ~${_saved_10:,.0f} in a 10% correction and ~${_saved_20:,.0f} "
                        f"in a 20% bear market. "
                    ) if trim_ticker else ""
                ) + (
                    f"Full reduction to target {target:.1f} eliminates "
                    f"~${loss_10:,.0f} / ~${loss_20:,.0f} of extra loss in 10% / 20% corrections."
                ),
                "institutional_lens": (
                    f"Institutional risk teams impose a hard Beta ceiling of {PORTFOLIO_BETA_CEILING:.1f} for managed equity accounts, "
                    f"dropping to {PORTFOLIO_BETA_ELEVATED:.1f} during 'risk-off' macro regimes. Above {PORTFOLIO_BETA_CEILING:.1f}, the asymmetry turns against you: "
                    "high-beta stocks fall faster during corrections than they rise during rallies on a "
                    "risk-adjusted basis. The PM's job is not to eliminate beta — it's to ensure you are "
                    "being paid for it through a Sharpe Ratio that compensates for the extra volatility."
                ),
            })
        else:
            recs.append({
                "priority": "OK",
                "type":     "ok_beta",
                "title":    f"Beta {beta:.2f} — Market Sensitivity Well Managed",
                "problem": "", "root_cause": "", "root_tickers": [],
                "recommendation": "No beta action required.",
                "expected_outcome": "",
                "institutional_lens": (
                    f"Portfolio Beta of {beta:.2f} sits below the {PORTFOLIO_BETA_ELEVATED:.1f} soft ceiling. "
                    "Watch for beta drift: as winners grow to a larger portfolio weight, beta creeps upward "
                    "without any new purchases. Re-check after significant P&L moves."
                ),
            })

    # ── 2. SHARPE / RISK-ADJUSTED RETURN ─────────────────────────────────────
    if sharpe is not None and sharpe < 0.8:
        sh_priority = "HIGH" if sharpe < 0.4 else "MEDIUM"

        drags = []
        for t, tr in tr_map.items():
            ts = tr["sharpe"]
            w  = tr["weight"]
            if ts < sharpe * 0.7 and w >= 3.0:
                drags.append({
                    "ticker": t,
                    "value":  round(ts, 2),
                    "weight": w,
                    "label": (
                        f"Sharpe {ts:.2f}  ·  {w:.1f}% weight  ·  "
                        f"{tr['ret_6mo']:+.1f}% 6mo return"
                    ),
                    "_drag": (sharpe - ts) * w / 100,
                })
        drags.sort(key=lambda x: x["value"])
        top_drags = drags[:2]

        drag_names = "  ·  ".join(
            f"**{x['ticker']}** (Sharpe {x['value']:.2f})"
            for x in top_drags
        ) if top_drags else "positions with high volatility relative to their return"

        recs.append({
            "priority": sh_priority,
            "type":     "sharpe",
            "title":    f"Sharpe {sharpe:.2f} — Risk Taken Is Not Being Rewarded",
            "problem": (
                f"A Sharpe of **{sharpe:.2f}** means you earn **{sharpe:.2f} units of return "
                f"per unit of risk**. The S&P 500 historically delivers ~0.9–1.0 Sharpe "
                f"at far lower volatility ({f'{ann_vol:.0f}%' if ann_vol is not None else 'unavailable'} annualised). "
                "You're carrying more risk than the index without earning proportionate returns for it."
            ),
            "root_cause": (
                f"Primary Sharpe drag: {drag_names}. "
                "These holdings contribute disproportionate volatility without enough return "
                "to justify their place in the portfolio."
            ),
            "root_tickers": top_drags,
            "recommendation": (
                "For each Sharpe drag position: if 6-month return is negative AND composite score "
                "is below 50, trim 40–50%. Redeploy into the highest-scoring name in the same sector "
                "— maintains sector exposure while improving return per unit of risk. "
                "A position can have a positive absolute return and still be a portfolio drag "
                "if its volatility contribution exceeds its return contribution."
            ),
            "expected_outcome": (
                "Removing the two lowest Sharpe contributors typically improves portfolio Sharpe "
                "by 0.15–0.30. A Sharpe above 1.0 means every unit of risk taken earns more "
                "than the market index — the definition of genuine alpha generation."
            ),
            "institutional_lens": (
                "Professional PM frameworks evaluates every position by its marginal Sharpe contribution. "
                "A stock with a 5% gain but 40% individual volatility is destroying portfolio Sharpe "
                "if SPY returned 6% at 15% vol. "
                "'Return per unit of risk' — not absolute return — is the correct measure of "
                "a position's value to the portfolio. This is the mindset shift from retail to professional."
            ),
        })
    elif sharpe is not None and sharpe >= 1.0:
        recs.append({
            "priority": "OK",
            "type":     "ok_sharpe",
            "title":    f"Sharpe {sharpe:.2f} — Strong Risk-Adjusted Returns",
            "problem": "", "root_cause": "", "root_tickers": [],
            "recommendation": "No Sharpe action required.",
            "expected_outcome": "",
            "institutional_lens": (
                f"Sharpe of {sharpe:.2f} is strong — you're generating meaningful return per unit of risk. "
                "Protect it by avoiding low-quality, high-volatility additions that would dilute it. "
                "Every new position you add should improve or at minimum preserve portfolio Sharpe."
            ),
        })

    # ── 3. PORTFOLIO VOLATILITY ───────────────────────────────────────────────
    if ann_vol is None:
        vol_priority = None
    elif ann_vol > 30:
        vol_priority = "HIGH"
    elif ann_vol > 25:
        vol_priority = "MEDIUM"
    else:
        vol_priority = None

    if vol_priority:
        vol_contribs = []
        for t, tr in tr_map.items():
            v = abs(tr["var_95"])
            w = tr["weight"]
            if v > 0 and w > 0:
                vol_contribs.append({
                    "ticker": t,
                    "value":  round(v, 2),
                    "weight": w,
                    "label":  f"Daily VaR {v:.1f}%  ·  {w:.1f}% of portfolio",
                    "_wt_var": v * w / 100,
                })
        vol_contribs.sort(key=lambda x: -x["_wt_var"])
        top_vol = vol_contribs[:2]

        daily_1sd = ann_vol / 100 / (252 ** 0.5) * pv
        weekly_2sd = daily_1sd * 2 * (5 ** 0.5)

        recs.append({
            "priority": vol_priority,
            "type":     "volatility",
            "title":    f"Annualised Volatility {ann_vol:.0f}% — Elevated Portfolio Turbulence",
            "problem": (
                f"At **{ann_vol:.0f}% annualised volatility**, your portfolio swings "
                f"an average of **±${daily_1sd:,.0f} per day** (1 standard deviation). "
                f"On a bad week (2 standard deviations), that's ±${weekly_2sd:,.0f}. "
                "Sustained high volatility increases the probability of panic-selling at "
                "the worst possible moment — the single biggest destroyer of retail investor returns."
            ),
            "root_cause": (
                "High-volatility contributors: "
                + "  ·  ".join(
                    f"**{x['ticker']}** (VaR {x['value']:.1f}%, {x['weight']:.0f}% weight)"
                    for x in top_vol
                )
            ),
            "root_tickers": top_vol,
            "recommendation": (
                "Add 8–12% allocation to a lower-volatility sector: Healthcare (XLV ~16% vol), "
                "Consumer Staples (XLP ~14% vol), or Utilities (XLU ~15% vol). "
                f"A 10% defensive allocation at 15% vol reduces portfolio volatility by "
                f"approximately {(ann_vol - ann_vol * 0.88):.1f}–{(ann_vol - ann_vol * 0.85):.1f}% annualised."
            ),
            "expected_outcome": (
                f"A 10% defensive allocation typically reduces portfolio volatility from "
                f"{ann_vol:.0f}% to ~{ann_vol - 3:.0f}%. Daily average swing drops "
                f"from ${daily_1sd:,.0f} to ~${daily_1sd * 0.85:,.0f} — "
                "meaningful improvement in day-to-day portfolio stability."
            ),
            "institutional_lens": (
                "High volatility is not inherently bad — it must be compensated by proportionally higher "
                "Sharpe. When Sharpe is below 1.0 AND volatility is above 25%, you have the worst "
                "combination: significant risk without adequate reward. "
                "Institutional equity desks always pair volatility with Sharpe analysis. "
                "Volatility in isolation is noise; Sharpe-adjusted volatility is signal."
            ),
        })

    # ── 4. MAX DRAWDOWN ───────────────────────────────────────────────────────
    if max_dd is not None and max_dd < -20:
        dd_priority = "HIGH" if max_dd < -30 else "MEDIUM"

        dd_contribs = []
        for t, tr in tr_map.items():
            d = tr["max_drawdown"]
            w = tr["weight"]
            if d < -15 and w > 0:
                dd_contribs.append({
                    "ticker": t,
                    "value":  round(d, 1),
                    "weight": w,
                    "label": (
                        f"Max DD {d:.0f}%  ·  {w:.1f}% weight  ·  "
                        f"P&L {tr['pnl_pct']:+.1f}%"
                    ),
                    "_impact": abs(d) * w / 100,
                })
        dd_contribs.sort(key=lambda x: x["value"])
        top_dd = dd_contribs[:2]

        # Recovery math: to recover from -X% you need +X/(1-X)*100%
        rec_needed = round(abs(max_dd) / (1 - abs(max_dd) / 100), 1) if abs(max_dd) < 100 else 999

        recs.append({
            "priority": dd_priority,
            "type":     "drawdown",
            "title":    f"Max Drawdown {max_dd:.0f}% — Portfolio Spent Time Significantly Underwater",
            "problem": (
                f"Your portfolio was **{max_dd:.0f}% below its peak** at its worst point "
                f"within the last 6 months. "
                f"A {abs(max_dd):.0f}% drawdown requires a **{rec_needed:.0f}% rally** just to break even — "
                "the asymmetric math of losses that most investors underestimate."
            ),
            "root_cause": (
                "  ·  ".join(
                    f"**{x['ticker']}** (DD {x['value']:.0f}%, {x['weight']:.0f}% weight)"
                    for x in top_dd
                ) if top_dd else "Review individual position drawdowns in the Overview drill-down."
            ),
            "root_tickers": top_dd,
            "recommendation": (
                "Verify that ratchet stops on deepest-drawdown positions were calibrated correctly. "
                "If any position drew down more than 25% without triggering a stop, the ATR multiplier "
                "was too wide. Tighten to ATR × 1.5 (instead of × 2.0) on your highest-drawdown names. "
                "For positions currently near their prior lows, re-evaluate whether the thesis still holds."
            ),
            "expected_outcome": (
                "Tighter stops on deep-drawdown positions historically reduce max drawdown depth by 5–10% "
                "while only marginally increasing stop-out frequency. "
                "The goal is never to avoid losses entirely — it's to make losses shallow and recoverable."
            ),
            "institutional_lens": (
                "Institutional wealth management uses a 15% portfolio drawdown as a mandatory review checkpoint — "
                "not an automatic stop, but a structured re-evaluation of every position thesis. "
                "The key diagnostic: is this drawdown caused by (a) broad market conditions — ride it out "
                "with stops in place — or (b) fundamental deterioration specific to these holdings — "
                "act immediately regardless of paper loss. Confusing (a) and (b) is the most expensive "
                "mistake in portfolio management."
            ),
        })
    elif max_dd is not None and max_dd > -10:
        recs.append({
            "priority": "OK",
            "type":     "ok_drawdown",
            "title":    f"Max Drawdown {max_dd:.0f}% — Well Controlled",
            "problem": "", "root_cause": "", "root_tickers": [],
            "recommendation": "No drawdown action required.",
            "expected_outcome": "",
            "institutional_lens": (
                f"A {max_dd:.0f}% max drawdown is modest for an equity portfolio. "
                "The ratchet stop system is working — gains are being protected as they accumulate. "
                "Keep monitoring: drawdown tends to deepen quickly when market conditions shift."
            ),
        })

    # ── 5. TAIL RISK (CVaR / VaR ratio) ──────────────────────────────────────
    if var_pct is not None and cvar_pct is not None and var_pct < 0 and cvar_pct < 0 and abs(var_pct) > 0:
        tail_ratio  = abs(cvar_pct) / abs(var_pct)
        var_dollar  = abs(var_pct  / 100 * pv)
        cvar_dollar = abs(cvar_pct / 100 * pv)

        if tail_ratio > 1.7:
            tail_priority = "HIGH" if tail_ratio > 2.2 else "MEDIUM"
            recs.append({
                "priority": tail_priority,
                "type":     "tail_risk",
                "title":    f"Fat Tail Risk — Crash Days Are {tail_ratio:.1f}× Worse Than Normal Bad Days",
                "problem": (
                    f"On a normal bad day your VaR says you won't lose more than "
                    f"**${var_dollar:,.0f} ({abs(var_pct):.1f}%)**. "
                    f"But your CVaR — the average loss on the *worst* 5% of days — "
                    f"is **${cvar_dollar:,.0f} ({abs(cvar_pct):.1f}%)**. "
                    f"That's **{tail_ratio:.1f}× worse**. "
                    "This fat-tail profile means your portfolio behaves normally most days "
                    "but has unusually severe drawdowns when something goes wrong."
                ),
                "root_cause": (
                    "Fat tails in equity portfolios come from concentrated sector exposure "
                    "(multiple positions crash simultaneously on the same catalyst) or from "
                    "high-beta names that gap down sharply on earnings misses or macro shocks. "
                    "Both conditions currently exist in a semiconductor-heavy portfolio."
                ),
                "root_tickers": [],
                "recommendation": (
                    "Add uncorrelated exposure to dampen crash-day severity: "
                    "5–8% cash buffer (immediately reduces CVaR by absorbing the worst tail moves), "
                    "or a 5% Healthcare position (near-zero correlation to semiconductor cycle). "
                    f"Goal: bring CVaR/VaR ratio from {tail_ratio:.1f}× toward 1.3–1.4×."
                ),
                "expected_outcome": (
                    f"A 5% cash buffer reduces CVaR proportionally from ${cvar_dollar:,.0f} "
                    f"to ~${cvar_dollar * 0.93:,.0f}. Adding a 5% uncorrelated sector position "
                    "typically narrows the CVaR/VaR ratio by 0.2–0.3× by smoothing the tails."
                ),
                "institutional_lens": (
                    "VaR tells you about normal bad days. CVaR tells you about disaster scenarios. "
                    "The 2008 financial crisis and March 2020 COVID crash were CVaR events — "
                    "portfolios that looked 'safe' on VaR lost 40–60% because nobody measured CVaR. "
                    "Institutional risk desks use CVaR as the primary stress metric precisely because "
                    "it captures what happens when correlations spike to 1.0 and diversification "
                    "disappears exactly when you need it most."
                ),
            })

    # ── 6. SECTOR CONCENTRATION ──────────────────────────────────────────────
    # Same thresholds Trade Review uses for sector-mix flagging and Grow Today
    # uses for add-to-winner suppression — Risk Advisor now also surfaces them
    # as a first-class portfolio risk so the user gets a coherent story.
    sector_weights: dict[str, float]      = defaultdict(float)
    sector_holdings: dict[str, list[dict]] = defaultdict(list)
    for t, tr in tr_map.items():
        sec = tr.get("sector") or "Other"
        sector_weights[sec]  += tr["weight"]
        sector_holdings[sec].append({
            "ticker":       t,
            "weight":       tr["weight"],
            "market_value": tr["market_value"],
            "price":        tr["price"],
            "pnl_pct":      tr["pnl_pct"],
            "score_raw":    tr["score_raw"],   # None-preserving; for conviction rank
        })

    # The "Other" catch-all is not a real correlated sector — it's the bucket
    # unclassified holdings land in. A hard-cap "breach" on it is a classification
    # artifact, and "trim Other / redeploy" is incoherent advice. Exclude it from
    # the concentration scan (top-sector pick AND redeploy targets); surface it
    # separately as a data-hygiene note so the gap is visible, not silent.
    real_sector_weights = {
        s: w for s, w in sector_weights.items() if s != UNCLASSIFIED_SECTOR
    }
    other_wt = sector_weights.get(UNCLASSIFIED_SECTOR, 0.0)
    if other_wt >= SECTOR_ELEVATED:
        other_names = sorted(
            sector_holdings[UNCLASSIFIED_SECTOR], key=lambda h: -h["weight"]
        )
        other_str = ", ".join(h["ticker"] for h in other_names[:6])
        if len(other_names) > 6:
            other_str += f" +{len(other_names) - 6} more"
        recs.append({
            "priority": "LOW",
            "type":     "unclassified_holdings",
            "title":    f"{other_wt:.1f}% Unclassified — Sector Tags Pending",
            "problem": (
                f"**{other_wt:.1f}% of your portfolio ({other_str})** has no sector "
                "classification, so it sits in the catch-all \"Other\" bucket. "
                "These names are excluded from sector concentration caps until "
                "they're tagged — a real sector overweight could hide here."
            ),
            "recommendation": (
                "These holdings need a sector mapping before concentration caps "
                "can cover them. They are NOT a real sector, so no trim is implied "
                "— this is a data-quality note, not a risk action."
            ),
        })

    if real_sector_weights:
        top_sec, _top_wt_eq = max(real_sector_weights.items(), key=lambda x: x[1])
        # Account-basis (tighter-of-both): == equity weight when there's no margin.
        top_wt = _top_wt_eq * _acct_f
        if top_wt >= SECTOR_CEILING:
            sec_priority = "HIGH"
        elif top_wt >= SECTOR_ELEVATED:
            sec_priority = "MEDIUM"
        else:
            sec_priority = None

        if sec_priority:
            sec_top_holdings = sorted(
                sector_holdings[top_sec],
                key=lambda h: -h["weight"],
            )[:3]
            root_tickers = [
                {
                    "ticker":       h["ticker"],
                    "value":        round(h["weight"] * _acct_f, 1),
                    "weight":       h["weight"] * _acct_f,
                    "market_value": h["market_value"],
                    "label":        f"{h['weight'] * _acct_f:.1f}% weight  ·  P&L {h['pnl_pct']:+.1f}%",
                }
                for h in sec_top_holdings
            ]
            top_names_str = "  ·  ".join(
                f"**{h['ticker']}** ({h['weight'] * _acct_f:.1f}%)"
                for h in sec_top_holdings
            )
            # Excess over the elevated threshold = how much weight needs to move
            excess_pp     = top_wt - SECTOR_ELEVATED
            excess_dollar = round(excess_pp / 100.0 * _gd)
            # Other sectors with low weight — natural redeployment targets (also
            # account-basis so the redeploy figures match the top-sector number).
            under_sectors = sorted(
                [(s, w * _acct_f) for s, w in real_sector_weights.items()
                 if s != top_sec and w * _acct_f < SECTOR_ELEVATED],
                key=lambda x: x[1],
            )[:3]
            under_str = (
                "  ·  ".join(f"{s} ({w:.1f}%)" for s, w in under_sectors)
                if under_sectors else "no under-represented sectors detected"
            )
            # Structured redeploy targets (render layer scores real tickers per
            # sector via the diversification helpers). Account-basis weights match
            # the top-sector figure above.
            redeploy_sectors = [
                {"sector": s, "weight": round(w, 1)} for s, w in under_sectors
            ]
            # Conviction-ranked trim order — the concrete backing for the copy's
            # "trim the lowest-conviction names first." Ranked by composite ASCENDING;
            # names with no real composite (score_raw is None) are EXCLUDED so a
            # missing score can't masquerade as lowest conviction. Account-basis
            # weight, consistent with root_tickers.
            trim_candidates = [
                {
                    "ticker":       h["ticker"],
                    "score":        round(float(h["score_raw"]), 0),
                    "weight":       round(h["weight"] * _acct_f, 1),
                    "pnl_pct":      round(h["pnl_pct"], 1),
                    "market_value": h["market_value"],   # for greedy trim allocation
                    "price":        h["price"],
                }
                for h in sorted(
                    (h for h in sector_holdings[top_sec] if h["score_raw"] is not None),
                    key=lambda h: h["score_raw"],
                )
            ]

            recs.append({
                "priority": sec_priority,
                "type":     "sector_concentration",
                "title": (
                    f"{top_sec} {top_wt:.1f}% — "
                    + ("Hard Cap Breach" if sec_priority == "HIGH"
                       else "Elevated Sector Concentration")
                ),
                "problem": (
                    f"**{top_wt:.1f}% of your portfolio sits in {top_sec}** — "
                    f"{'above' if sec_priority == 'HIGH' else 'approaching'} the "
                    f"{SECTOR_CEILING:.0f}% institutional sector ceiling "
                    f"(elevated warn level {SECTOR_ELEVATED:.0f}%)."
                    + f" A single sector-wide shock (regulatory action, earnings cycle, "
                    f"macro regime change) hits roughly **${top_wt / 100.0 * _gd:,.0f}** of "
                    f"capital at once — diversification breaks down precisely when you need it."
                ),
                "root_cause": f"Largest {top_sec} positions: {top_names_str}.",
                "root_tickers": root_tickers,
                # Rebalance-plan payload (render layer, app.py _render_act_card):
                # trim_candidates = conviction-ranked names to trim first;
                # redeploy_sectors = under-represented targets to score buy names in.
                # trim_target_* = the headline directive's target ($/pp + denom),
                # so the render's greedy allocation adds up to the same figure.
                "trim_candidates":    trim_candidates,
                "redeploy_sectors":   redeploy_sectors,
                "trim_target_pp":     round(excess_pp, 1),
                "trim_target_dollar": excess_dollar,
                "trim_target_denom":  _gd,
                "recommendation": (
                    f"Trim {top_sec} exposure by approximately "
                    f"**{excess_pp:.0f}pp (~${excess_dollar:,.0f})** to bring the sector under "
                    f"the {SECTOR_ELEVATED:.0f}% warn level. Redeploy into under-represented sectors: "
                    f"{under_str}. Trim the lowest-conviction {top_sec} names first — keep your "
                    "best ideas, prune the marginal ones."
                ),
                "expected_outcome": (
                    f"Pulling {top_sec} from {top_wt:.1f}% to {SECTOR_ELEVATED:.0f}% cuts your "
                    f"single-sector exposure by roughly **${excess_dollar:,.0f}** while preserving "
                    f"capital deployment. Sector-shock loss in a -10% {top_sec} move drops "
                    f"by ~${round(excess_pp / 100.0 * _gd * 0.10):,.0f}."
                ),
                "institutional_lens": (
                    "Sector concentration is the most under-priced risk in retail portfolios. "
                    "Single-stock risk gets attention via the 15% single-name ceiling, but "
                    "five names in the same sector creates the same correlated-loss exposure "
                    "without tripping any single-name alarm. Institutional mandates cap sector "
                    f"exposure at {SECTOR_CEILING:.0f}% precisely because correlations within "
                    "a sector spike to 0.7-0.9 in stress, while inter-sector correlations stay "
                    "near 0.4 — diversification across sectors is what actually pays off when "
                    "things go wrong."
                ),
            })

    # ── Single-name concentration (conviction-INDEPENDENT overweight) ──────────
    # The weak-large flag (daily_briefing) catches overweight + WEAK names
    # (score < WEAK_CONVICTION_SCORE). It misses overweight + STRONG names — a
    # high-conviction name at 23% trips no alarm today, yet the 15% single-name
    # cap is a RISK limit, not a conviction call. Fill exactly that gap (score ≥
    # WEAK_CONVICTION_SCORE so we never double-surface with weak-large). MEDIUM →
    # Portfolio Tune-up (structural/standing, not Act-Today churn).
    for t, tr in tr_map.items():
        w = tr["weight"] * _acct_f        # account-basis (== equity when no margin)
        score = tr["score"]
        if w >= SINGLE_NAME_CEILING and score >= WEAK_CONVICTION_SCORE:
            excess_pp     = w - SINGLE_NAME_CEILING
            excess_dollar = round(excess_pp / 100.0 * _gd)
            recs.append({
                "priority": "MEDIUM",
                "type":     "single_name_concentration",
                "title":    f"{t} {w:.1f}% — Single-Name Overweight",
                "problem": (
                    f"**{t} is {w:.1f}% of your book** — above the "
                    f"{SINGLE_NAME_CEILING:.0f}% single-name ceiling. Conviction is fine "
                    f"(score {score:.0f}); this is a SIZE limit."
                    + f" At this weight one bad "
                    f"print or downgrade on a single name can swing the whole portfolio — "
                    f"roughly **${w / 100.0 * _gd:,.0f}** rides on {t} alone."
                ),
                "root_cause": f"{t} weight {w:.1f}% (score {score:.0f} — a size issue, not a quality one).",
                "root_tickers": [{
                    "ticker":       t,
                    "value":        round(w, 1),
                    "weight":       w,
                    "market_value": tr["market_value"],
                    "label":        f"{w:.1f}% weight  ·  score {score:.0f}",
                }],
                "recommendation": (
                    f"Trim **{t}** by ~**{excess_pp:.0f}pp (~${excess_dollar:,.0f})** back toward the "
                    f"{SINGLE_NAME_CEILING:.0f}% ceiling. Keep the thesis — just cap the size so a "
                    "single name can't dominate the outcome."
                ),
                "expected_outcome": (
                    f"Bringing {t} from {w:.1f}% to {SINGLE_NAME_CEILING:.0f}% frees "
                    f"~${excess_dollar:,.0f} to diversify and bounds single-name shock."
                ),
                "institutional_lens": (
                    "The single-name ceiling is a risk limit INDEPENDENT of conviction. Funds "
                    "cap position size precisely so that being wrong on one name — however "
                    "high-conviction — can't blow up the book. Sizing is risk management; "
                    "conviction is idea generation. They are separate disciplines."
                ),
            })

    return recs
