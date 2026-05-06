import numpy as np
import pandas as pd
import pandas_ta as ta


def _atr_value(df: pd.DataFrame, length: int = 14) -> float:
    atr = ta.atr(df["High"], df["Low"], df["Close"], length=length)
    if atr is None or atr.dropna().empty:
        return float((df["High"] - df["Low"]).tail(length).mean())
    return float(atr.dropna().iloc[-1])


def atr_stop_loss(df: pd.DataFrame, multiplier: float = 2.0) -> tuple[float, float]:
    """Returns (stop_loss_price, atr_value)."""
    atr_val = _atr_value(df)
    current_price = float(df["Close"].iloc[-1])
    stop = round(current_price - multiplier * atr_val, 2)
    return stop, round(atr_val, 2)


def position_sizing(
    portfolio_value: float, risk_pct: float, entry: float, stop: float
) -> dict | None:
    if entry <= stop or stop <= 0:
        return None
    risk_dollars = portfolio_value * risk_pct
    risk_per_share = entry - stop
    shares = max(1, int(risk_dollars / risk_per_share))
    total_cost = round(shares * entry, 2)
    actual_risk = round(shares * risk_per_share, 2)
    return {
        "shares": shares,
        "risk_budget": round(risk_dollars, 2),
        "actual_risk": actual_risk,
        "risk_per_share": round(risk_per_share, 2),
        "total_cost": total_cost,
        "portfolio_pct": round(total_cost / portfolio_value * 100, 1),
        "risk_pct_actual": round(actual_risk / portfolio_value * 100, 2),
    }


def sharpe_ratio(df: pd.DataFrame, risk_free_annual: float = 0.045) -> float:
    returns = df["Close"].pct_change().dropna()
    rf_daily = risk_free_annual / 252
    excess = returns - rf_daily
    std = excess.std()
    if std == 0 or np.isnan(std):
        return 0.0
    return round(float((excess.mean() / std) * np.sqrt(252)), 2)


def sortino_ratio(df: pd.DataFrame, risk_free_annual: float = 0.045) -> float:
    returns = df["Close"].pct_change().dropna()
    rf_daily = risk_free_annual / 252
    excess = returns - rf_daily
    downside_std = excess[excess < 0].std()
    if downside_std == 0 or np.isnan(downside_std):
        return 0.0
    return round(float((excess.mean() / downside_std) * np.sqrt(252)), 2)


def max_drawdown_pct(df: pd.DataFrame) -> float:
    prices = df["Close"]
    rolling_max = prices.cummax()
    dd = (prices - rolling_max) / rolling_max
    return round(float(dd.min() * 100), 1)


def var_95_daily(df: pd.DataFrame) -> float:
    """One-day 95% VaR as % of position value (negative = loss)."""
    returns = df["Close"].pct_change().dropna()
    return round(float(np.percentile(returns, 5) * 100), 2)


def beta_vs_market(df: pd.DataFrame, market_df: pd.DataFrame) -> float | None:
    stock_ret = df["Close"].pct_change().dropna()
    mkt_ret = market_df["Close"].pct_change().dropna()
    combined = pd.concat([stock_ret, mkt_ret], axis=1, keys=["s", "m"]).dropna()
    if len(combined) < 20:
        return None
    cov = combined.cov().loc["s", "m"]
    mkt_var = combined["m"].var()
    if mkt_var == 0:
        return None
    return round(float(cov / mkt_var), 2)


def compute_all_risk(df: pd.DataFrame, spy_df: pd.DataFrame | None = None) -> dict:
    return {
        "sharpe": sharpe_ratio(df),
        "sortino": sortino_ratio(df),
        "max_drawdown": max_drawdown_pct(df),
        "var_95": var_95_daily(df),
        "beta": beta_vs_market(df, spy_df) if spy_df is not None else None,
    }
