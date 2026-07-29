"""
Regime-Conditional Position Targets — Concept D (next-evolution roadmap,
Wave 3, 2026-07-17 policy conversation with user).

Answers: "given the CURRENT macro regime, is this portfolio's beta and cash
cushion where it should be?" The regime ids consumed here (`rate_cut`,
`inflation_fight`, `recession_fear`, `stagflation_risk`, `neutral`) are the
real ids returned by `stock_analyzer.macro_calendar.detect_macro_regime` —
NOT illustrative Risk-On/Risk-Off labels.

Awareness/diagnostic only. This module NEVER gates, suppresses, resizes, or
recommends a trade — it reports a gap between current positioning and a
regime-conditional target (`REGIME_BETA_CEILING` / `REGIME_CASH_FLOOR_PCT` in
constants.py) so the investor can decide whether/how fast to move toward it.
Fully None-safe: never raises on missing beta, missing cash, an empty
portfolio, or an unrecognized regime id (fails safe to "neutral").
"""

from stock_analyzer.constants import REGIME_BETA_CEILING, REGIME_CASH_FLOOR_PCT


def regime_position_gap(
    regime_id: str,
    port_beta: float | None,
    cash_pct: float | None,
    port_df,          # pandas DataFrame with "Ticker" and "Weight (%)" columns (may be empty)
    held_data: dict,  # {ticker: {"risk_metrics": {"beta": float | None, ...}, ...}}
) -> dict:
    """Compute the regime-conditional beta/cash gap for the current portfolio.

    Returns a dict (see module docstring for the never-raises guarantee):
      {
        "regime_id": str, "beta_ceiling": float, "cash_floor_pct": float,
        "port_beta": float|None, "cash_pct": float|None,
        "beta_gap": float|None, "cash_gap": float|None,
        "beta_breach": bool, "cash_breach": bool,
        "top_contributors": list[dict],
      }
    """
    held_data = held_data or {}
    ceiling = REGIME_BETA_CEILING.get(regime_id, REGIME_BETA_CEILING["neutral"])
    floor = REGIME_CASH_FLOOR_PCT.get(regime_id, REGIME_CASH_FLOOR_PCT["neutral"])

    beta_gap = round(port_beta - ceiling, 2) if port_beta is not None else None
    beta_breach = beta_gap is not None and beta_gap > 0

    cash_gap = round(floor - cash_pct, 1) if cash_pct is not None else None
    cash_breach = cash_gap is not None and cash_gap > 0

    top_contributors = []
    if beta_breach and port_df is not None and hasattr(port_df, "empty") and not port_df.empty:
        _rows = []
        for _, row in port_df.iterrows():
            ticker = row.get("Ticker")
            weight = row.get("Weight (%)")
            beta = ((held_data.get(ticker) or {}).get("risk_metrics") or {}).get("beta")
            if beta is None or beta <= 0 or weight is None or weight <= 0:
                continue
            contrib = beta * weight / 100
            _rows.append({
                "ticker": str(ticker),
                "beta": round(beta, 2),
                "weight": round(weight, 1),
                "contrib": round(contrib, 3),
            })
        _rows.sort(key=lambda r: r["contrib"], reverse=True)
        top_contributors = _rows[:3]

    return {
        "regime_id": regime_id,
        "beta_ceiling": ceiling,
        "cash_floor_pct": floor,
        "port_beta": port_beta,
        "cash_pct": cash_pct,
        "beta_gap": beta_gap,
        "cash_gap": cash_gap,
        "beta_breach": beta_breach,
        "cash_breach": cash_breach,
        "top_contributors": top_contributors,
    }
