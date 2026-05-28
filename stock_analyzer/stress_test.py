"""
Portfolio Stress Testing & Scenario Analysis.

Estimates the dollar and percentage impact on each portfolio position
under predefined and custom market shock scenarios, using individual
position betas (vs SPY) and sector-specific historical drawdown data.
"""

import pandas as pd


def _f(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


def _opt(val):
    """None-preserving float coercion — returns None for None / NaN."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if (f != f) else f
    except (TypeError, ValueError):
        return None


# Historical sector drawdowns (%) for each named scenario.
# Source: approximate peak-to-trough moves during each event.
_SECTOR_SHOCKS = {
    "2022 Rate Shock": {
        # SPY: -25.4%  |  Tech punished hardest, Energy surged
        "Semiconductors":  -35.0,
        "AI & Data":       -60.0,
        "AI & Cloud":      -35.0,
        "Consumer Tech":   -28.0,
        "EV & Auto":       -65.0,
        "Clean Energy":    -30.0,
        "Cybersecurity":   -40.0,
        "Healthcare":       -6.0,
        "Financials":      -15.0,
        "Energy":          +55.0,
        "Defense":          +5.0,
    },
    "2020 COVID Crash": {
        # SPY: -34%  |  Energy destroyed, Healthcare defensive
        "Semiconductors":  -32.0,
        "AI & Data":       -28.0,
        "AI & Cloud":      -28.0,
        "Consumer Tech":   -30.0,
        "EV & Auto":       -40.0,
        "Clean Energy":    -35.0,
        "Cybersecurity":   -25.0,
        "Healthcare":      -18.0,
        "Financials":      -42.0,
        "Energy":          -55.0,
        "Defense":         -22.0,
    },
    "AI Trade Unwind": {
        # Hypothetical: AI/semis reprice -30%, defensives largely spared
        "Semiconductors":  -30.0,
        "AI & Data":       -35.0,
        "AI & Cloud":      -28.0,
        "Consumer Tech":   -20.0,
        "EV & Auto":       -22.0,
        "Clean Energy":    -12.0,
        "Cybersecurity":   -20.0,
        "Healthcare":       -5.0,
        "Financials":       -8.0,
        "Energy":           -6.0,
        "Defense":          -4.0,
    },
    "Rate Spike (+100bps)": {
        # Sudden 100bps rate rise: long-duration assets hurt most
        "Semiconductors":  -18.0,
        "AI & Data":       -22.0,
        "AI & Cloud":      -20.0,
        "Consumer Tech":   -15.0,
        "EV & Auto":       -20.0,
        "Clean Energy":    -25.0,
        "Cybersecurity":   -18.0,
        "Healthcare":       -8.0,
        "Financials":       +8.0,
        "Energy":           +5.0,
        "Defense":          -3.0,
    },
}

# Predefined scenarios: (label, description, spy_move_pct)
# Sector overrides applied on top when available.
SCENARIOS = [
    {
        "id":          "mild_correction",
        "label":       "Mild Correction  (SPY −10%)",
        "description": "A routine pullback — happens 1–2× per year on average. "
                       "Beta-adjusted impact per position.",
        "spy_move":    -10.0,
        "sector_key":  None,
    },
    {
        "id":          "bear_entry",
        "label":       "Bear Market Entry  (SPY −20%)",
        "description": "Standard bear market threshold. Typical during earnings recessions "
                       "or tightening cycles without a hard landing.",
        "spy_move":    -20.0,
        "sector_key":  None,
    },
    {
        "id":          "severe_bear",
        "label":       "Severe Bear  (SPY −30%)",
        "description": "Deep bear market — hard landing or credit event. "
                       "Comparable to 2001–2002 or Q4 2018 extended.",
        "spy_move":    -30.0,
        "sector_key":  None,
    },
    {
        "id":          "rate_shock_2022",
        "label":       "2022 Rate Shock  (SPY −25%)",
        "description": "2022 inflation/rate cycle. SPY −25%, but tech/AI names dropped "
                       "40–65% while Energy surged +55%. Sector overrides applied.",
        "spy_move":    -25.0,
        "sector_key":  "2022 Rate Shock",
    },
    {
        "id":          "covid_crash",
        "label":       "2020 COVID Crash  (SPY −34%)",
        "description": "Feb–Mar 2020 peak-to-trough. Fastest 30% drop in market history. "
                       "Energy −55%, Financials −42%. Sector overrides applied.",
        "spy_move":    -34.0,
        "sector_key":  "2020 COVID Crash",
    },
    {
        "id":          "ai_unwind",
        "label":       "AI Trade Unwind  (AI/Semis −30%)",
        "description": "Sector-specific repricing of AI/semiconductor names. "
                       "Defensives largely spared. Tests concentration in the AI theme.",
        "spy_move":    -10.0,
        "sector_key":  "AI Trade Unwind",
    },
    {
        "id":          "rate_spike",
        "label":       "Sudden Rate Spike  (+100bps)",
        "description": "Emergency rate hike or bond market shock. "
                       "Long-duration growth names repriced; Financials and Energy benefit.",
        "spy_move":    -12.0,
        "sector_key":  "Rate Spike (+100bps)",
    },
]


def run_scenario(
    scenario: dict,
    port_df: pd.DataFrame,
    held_data: dict,
    portfolio_beta: float | None = None,
    custom_spy_move: float | None = None,
) -> dict:
    """
    Run a single stress scenario.

    Returns dict with:
      spy_move, estimated_port_move, estimated_port_pnl,
      portfolio_value, post_shock_value,
      rows (list of per-position dicts, sorted by pnl ascending),
      most_exposed (top 3 losers), any_gainers (positions that benefit)
    """
    spy_move    = custom_spy_move if custom_spy_move is not None else scenario["spy_move"]
    sector_key  = scenario.get("sector_key")
    sector_map  = _SECTOR_SHOCKS.get(sector_key, {}) if sector_key else {}

    portfolio_value = _f(port_df["Market Value"].sum())
    if portfolio_value <= 0:
        return {}

    rows = []
    total_pnl = 0.0

    for _, row in port_df.iterrows():
        ticker  = row["Ticker"]
        # Skip positions with no market value -- a missing yfinance price
        # would otherwise contribute $0 P&L to the stress scenario, making
        # a degraded portfolio look falsely benign.
        mval_v  = _opt(row.get("Market Value"))
        if mval_v is None or mval_v <= 0:
            continue
        mval    = mval_v
        sector  = str(row.get("Sector", "Other"))
        weight  = _f(row.get("Weight (%)"))

        # Determine the estimated move for this position
        if sector in sector_map:
            # Use historical sector drawdown for this scenario, but scale it
            # proportionally with any custom SPY shock. Without this scaling
            # the UI's "Custom SPY shock" control silently has no effect on
            # any sector listed in _SECTOR_SHOCKS — which covers most held
            # sectors — so the user sees "Custom shock: -15%" and runs the
            # canned numbers from the 2022/COVID table instead.
            base_spy = scenario.get("spy_move") or spy_move
            if base_spy:
                est_move = sector_map[sector] * (spy_move / base_spy)
            else:
                est_move = sector_map[sector]
        else:
            # Fall back to beta × SPY move
            pos_beta = None
            data = held_data.get(ticker) or {}
            rm = data.get("risk_metrics") or {}
            if rm.get("beta") is not None:
                try:
                    pos_beta = float(rm["beta"])
                    if pos_beta != pos_beta:  # NaN check
                        pos_beta = None
                except (TypeError, ValueError):
                    pos_beta = None

            if pos_beta is None:
                pos_beta = portfolio_beta if portfolio_beta is not None else 1.0

            est_move = pos_beta * spy_move

        pos_pnl    = round(est_move / 100 * mval, 0)
        total_pnl += pos_pnl

        rows.append({
            "Ticker":          ticker,
            "Sector":          sector,
            "Weight (%)":      round(weight, 1),
            "Market Value ($)": round(mval, 0),
            "Est. Move (%)":   round(est_move, 1),
            "Est. P&L ($)":    pos_pnl,
        })

    rows.sort(key=lambda x: x["Est. P&L ($)"])

    post_shock      = portfolio_value + total_pnl
    port_move_pct   = round(total_pnl / portfolio_value * 100, 1) if portfolio_value else 0.0

    most_exposed = rows[:3]   # biggest losers
    any_gainers  = [r for r in rows if r["Est. P&L ($)"] > 0]

    return {
        "spy_move":             spy_move,
        "estimated_port_move":  port_move_pct,
        "estimated_port_pnl":   round(total_pnl, 0),
        "portfolio_value":      round(portfolio_value, 0),
        "post_shock_value":     round(post_shock, 0),
        "rows":                 rows,
        "most_exposed":         most_exposed,
        "any_gainers":          any_gainers,
    }


def run_all_scenarios(
    port_df: pd.DataFrame,
    held_data: dict,
    portfolio_beta: float | None = None,
) -> list[dict]:
    """Run all predefined scenarios. Returns list of result dicts with scenario metadata."""
    results = []
    for sc in SCENARIOS:
        result = run_scenario(sc, port_df, held_data, portfolio_beta)
        if result:
            results.append({**sc, **result})
    return results
