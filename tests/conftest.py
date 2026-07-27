# Regression tests for stock_analyzer/ pure-logic modules — no Streamlit, no
# Supabase, no live network. See docs/plans/test-automation.md for scope and
# batch order.
import pandas as pd


def make_risk_advisor_inputs(
    rows,
    *,
    portfolio_value: float = 100_000.0,
    gate_denom: float | None = None,
    beta: float | None = 1.0,
    ann_volatility: float | None = 15.0,
    sharpe: float | None = 1.2,
    sortino: float | None = 1.0,
    var_95_pct: float | None = None,
    cvar_95_pct: float | None = None,
    max_drawdown: float | None = -5.0,
):
    """Build (port_df, held_data, port_risk, h_rets, portfolio_value, gate_denom)
    for stock_analyzer.risk_advisor.build_risk_advisor_recommendations().

    `rows` is a list of dicts, each describing one held ticker. Any field not
    given falls back to a "safe" default so a test can vary only the field(s)
    relevant to the recommendation it's checking, without triggering unrelated
    branches. Defaults for the top-level port_risk metrics (beta/sharpe/vol/
    drawdown) are likewise all "safe" (no rec expected) unless overridden.

    Per-row fields: ticker, weight (%), market_value ($), price, pnl_pct,
    score, sector, signal, beta, sharpe, sortino, max_drawdown, var_95, ret_6mo.
    """
    defaults = dict(
        weight=10.0, market_value=10_000.0, price=100.0, pnl_pct=0.0, score=60.0,
        sector="Tech", signal="Hold", beta=1.0, sharpe=1.0, sortino=1.0,
        max_drawdown=-5.0, var_95=-2.0, ret_6mo=0.0,
    )
    filled = []
    for r in rows:
        row = {**defaults, **r}
        filled.append(row)

    port_df = pd.DataFrame([
        {
            "Ticker": r["ticker"],
            "Weight (%)": r["weight"],
            "Market Value": r["market_value"],
            "Price": r["price"],
            "P&L (%)": r["pnl_pct"],
            "Score": r["score"],
            "Signal": r["signal"],
            "Sector": r["sector"],
        }
        for r in filled
    ])
    held_data = {
        r["ticker"]: {
            "risk_metrics": {
                "beta": r["beta"],
                "sharpe": r["sharpe"],
                "sortino": r["sortino"],
                "max_drawdown": r["max_drawdown"],
                "var_95": r["var_95"],
            }
        }
        for r in filled
    }
    h_rets = {r["ticker"]: r["ret_6mo"] for r in filled}
    port_risk = {
        "beta": beta,
        "ann_volatility": ann_volatility,
        "sharpe": sharpe,
        "sortino": sortino,
        "var_95_pct": var_95_pct,
        "cvar_95_pct": cvar_95_pct,
        "max_drawdown": max_drawdown,
    }
    return port_df, held_data, port_risk, h_rets, portfolio_value, gate_denom


def find_rec(recs, rec_type):
    """First rec dict of the given `type`, or None."""
    return next((r for r in recs if r["type"] == rec_type), None)
