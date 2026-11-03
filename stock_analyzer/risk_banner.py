"""Decision for the 📊 Risk Analysis interpretation banner (⚠️ flags vs ✅ clear).

Each flag fires at its metric's worst label-band edge, so the banner and the
metric labels cannot drift apart. Every threshold is a named constant (RiskLit,
2026-11-03: values unchanged from the bare literals app.py used to compare):
beta strictly above PORTFOLIO_BETA_CEILING, volatility at or above
PORTFOLIO_VOL_HIGH_PCT, Sharpe strictly below PORTFOLIO_SHARPE_WEAK_MAX,
max drawdown strictly below PORTFOLIO_DRAWDOWN_ACTION_MAX. A missing metric
(None) raises no flag. Pure; no I/O.
"""
from __future__ import annotations

from stock_analyzer.constants import (
    PORTFOLIO_BETA_CEILING,
    PORTFOLIO_DRAWDOWN_ACTION_MAX,
    PORTFOLIO_SHARPE_WEAK_MAX,
    PORTFOLIO_VOL_HIGH_PCT,
)


def risk_flags(beta, vol, sharpe, max_drawdown) -> list[str]:
    """Flag sentences for the metrics that breach; empty list = clear."""
    flags: list[str] = []
    if beta is not None and beta > PORTFOLIO_BETA_CEILING:
        flags.append(f"Beta {beta:.2f} — portfolio moves {beta:.1f}× the market in both directions")
    if vol is not None and vol >= PORTFOLIO_VOL_HIGH_PCT:
        flags.append(f"Volatility {vol:.0f}% annualised — expect ±{vol/16:.1f}% daily swings on average")
    if sharpe is not None and sharpe < PORTFOLIO_SHARPE_WEAK_MAX:
        flags.append(f"Sharpe {sharpe:.2f} — poor risk-adjusted return; the risk taken is not being rewarded")
    if max_drawdown is not None and max_drawdown < PORTFOLIO_DRAWDOWN_ACTION_MAX:
        flags.append(f"Drawdown {max_drawdown:.0f}% — portfolio spent time significantly below its high-water mark")
    return flags
