"""
Goldman Sachs-style Risk Advisor.

Reads 7 portfolio-level risk metrics plus per-stock risk data and produces
ranked, evidence-backed action recommendations — each with a problem
statement, root-cause tickers, a specific actionable step, a quantified
expected outcome, and a Goldman Lens teaching moment.
"""

import numpy as np
import pandas as pd


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
      goldman_lens    : teaching moment / professional context
    """
    if not port_risk or port_df.empty:
        return []

    recs: list[dict] = []

    beta    = _beta_ok(port_risk.get("beta"))
    ann_vol = _f(port_risk.get("ann_volatility"))
    sharpe  = _f(port_risk.get("sharpe"))
    sortino = _f(port_risk.get("sortino"))
    var_pct = _f(port_risk.get("var_95_pct"))    # negative %
    cvar_pct = _f(port_risk.get("cvar_95_pct"))  # negative %
    max_dd  = _f(port_risk.get("max_drawdown"))   # negative %

    pv = portfolio_value if portfolio_value > 0 else 50_000.0

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
            "pnl_pct":      _f(row.get("P&L (%)")),
            "score":        _f(row.get("Score")),
            "signal":       str(row.get("Signal", "")),
            "ret_6mo":      _f(h_rets.get(t)),
            "sector":       str(row.get("Sector", "Other")),
        }

    # ── 1. BETA ───────────────────────────────────────────────────────────────
    if beta is not None:
        if beta > 1.4:
            beta_priority = "HIGH"
        elif beta > 1.2:
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
                        "ticker":  t,
                        "value":   round(b, 2),
                        "weight":  w,
                        "label":   f"β {b:.2f}  ·  {w:.1f}% of portfolio",
                        "_contrib": b * w / 100,
                    })
            contribs.sort(key=lambda x: -x["_contrib"])
            top_beta = contribs[:3]

            target   = 1.2
            excess   = beta - target
            loss_10  = excess * pv * 0.10
            loss_20  = excess * pv * 0.20
            top_names = "  ·  ".join(
                f"**{x['ticker']}** (β {x['value']:.2f})"
                for x in top_beta
            ) if top_beta else "your highest-weight positions"

            trim_ticker = top_beta[0]["ticker"] if top_beta else None
            trim_amt    = round(min(top_beta[0]["weight"] * 0.30, 8) / 100 * pv) if top_beta else 0

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
                    (f"Trim **{trim_ticker}** by ~30% of position (~${trim_amt:,.0f}). " if trim_ticker else "")
                    + f"This brings weighted portfolio Beta toward {target:.1f}. "
                    "Alternatively, add 8–10% in a defensive sector (Healthcare XLV, Consumer Staples XLP, "
                    "or Utilities XLU) to dilute beta without exiting high-conviction names."
                ),
                "expected_outcome": (
                    f"Reducing Beta to {target:.1f} eliminates ~${loss_10:,.0f} of extra loss in a 10% correction "
                    f"and ~${loss_20:,.0f} in a 20% bear market. "
                    "Defensive additions typically bring portfolio Beta from the current "
                    f"{beta:.2f} to {beta - excess * 0.6:.2f} with a single 10% position change."
                ),
                "goldman_lens": (
                    "Goldman Sachs risk teams impose a hard Beta ceiling of 1.4 for managed equity accounts, "
                    "dropping to 1.2 during 'risk-off' macro regimes. Above 1.4, the asymmetry turns against you: "
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
                "goldman_lens": (
                    f"Portfolio Beta of {beta:.2f} sits within Goldman's 0.8–1.2 target range. "
                    "Watch for beta drift: as winners grow to a larger portfolio weight, beta creeps upward "
                    "without any new purchases. Re-check after significant P&L moves."
                ),
            })

    # ── 2. SHARPE / RISK-ADJUSTED RETURN ─────────────────────────────────────
    if sharpe < 0.8:
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
                f"at far lower volatility ({ann_vol:.0f}% annualised). "
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
            "goldman_lens": (
                "Goldman's GS Compass framework evaluates every position by its marginal Sharpe contribution. "
                "A stock with a 5% gain but 40% individual volatility is destroying portfolio Sharpe "
                "if SPY returned 6% at 15% vol. "
                "'Return per unit of risk' — not absolute return — is the correct measure of "
                "a position's value to the portfolio. This is the mindset shift from retail to professional."
            ),
        })
    elif sharpe >= 1.0:
        recs.append({
            "priority": "OK",
            "type":     "ok_sharpe",
            "title":    f"Sharpe {sharpe:.2f} — Strong Risk-Adjusted Returns",
            "problem": "", "root_cause": "", "root_tickers": [],
            "recommendation": "No Sharpe action required.",
            "expected_outcome": "",
            "goldman_lens": (
                f"Sharpe of {sharpe:.2f} is strong — you're generating meaningful return per unit of risk. "
                "Protect it by avoiding low-quality, high-volatility additions that would dilute it. "
                "Every new position you add should improve or at minimum preserve portfolio Sharpe."
            ),
        })

    # ── 3. PORTFOLIO VOLATILITY ───────────────────────────────────────────────
    if ann_vol > 30:
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
            "goldman_lens": (
                "High volatility is not inherently bad — it must be compensated by proportionally higher "
                "Sharpe. When Sharpe is below 1.0 AND volatility is above 25%, you have the worst "
                "combination: significant risk without adequate reward. "
                "Goldman's structured equity desks always pair volatility with Sharpe analysis. "
                "Volatility in isolation is noise; Sharpe-adjusted volatility is signal."
            ),
        })

    # ── 4. MAX DRAWDOWN ───────────────────────────────────────────────────────
    if max_dd < -20:
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
            "goldman_lens": (
                "Goldman private wealth uses a 15% portfolio drawdown as a mandatory review checkpoint — "
                "not an automatic stop, but a structured re-evaluation of every position thesis. "
                "The key diagnostic: is this drawdown caused by (a) broad market conditions — ride it out "
                "with stops in place — or (b) fundamental deterioration specific to these holdings — "
                "act immediately regardless of paper loss. Confusing (a) and (b) is the most expensive "
                "mistake in portfolio management."
            ),
        })
    elif max_dd > -10:
        recs.append({
            "priority": "OK",
            "type":     "ok_drawdown",
            "title":    f"Max Drawdown {max_dd:.0f}% — Well Controlled",
            "problem": "", "root_cause": "", "root_tickers": [],
            "recommendation": "No drawdown action required.",
            "expected_outcome": "",
            "goldman_lens": (
                f"A {max_dd:.0f}% max drawdown is modest for an equity portfolio. "
                "The ratchet stop system is working — gains are being protected as they accumulate. "
                "Keep monitoring: drawdown tends to deepen quickly when market conditions shift."
            ),
        })

    # ── 5. TAIL RISK (CVaR / VaR ratio) ──────────────────────────────────────
    if var_pct < 0 and cvar_pct < 0 and abs(var_pct) > 0:
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
                "goldman_lens": (
                    "VaR tells you about normal bad days. CVaR tells you about disaster scenarios. "
                    "The 2008 financial crisis and March 2020 COVID crash were CVaR events — "
                    "portfolios that looked 'safe' on VaR lost 40–60% because nobody measured CVaR. "
                    "Goldman's risk desks use CVaR as the primary stress metric precisely because "
                    "it captures what happens when correlations spike to 1.0 and diversification "
                    "disappears exactly when you need it most."
                ),
            })

    return recs
