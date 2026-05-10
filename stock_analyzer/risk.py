import numpy as np
import pandas as pd
from stock_analyzer.indicators import atr as _atr_series


def _atr_value(df: pd.DataFrame, length: int = 14) -> float:
    s = _atr_series(df["High"], df["Low"], df["Close"], length).dropna()
    if s.empty:
        return float((df["High"] - df["Low"]).tail(length).mean())
    return float(s.iloc[-1])


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


def compute_all_risk(
    df: pd.DataFrame,
    spy_df: pd.DataFrame | None = None,
    risk_free_rate: float = 0.045,
) -> dict:
    return {
        "sharpe": sharpe_ratio(df, risk_free_rate),
        "sortino": sortino_ratio(df, risk_free_rate),
        "max_drawdown": max_drawdown_pct(df),
        "var_95": var_95_daily(df),
        "beta": beta_vs_market(df, spy_df) if spy_df is not None else None,
    }


def compute_portfolio_risk_metrics(
    port_df: pd.DataFrame,
    held_data: dict,
    spy_df: pd.DataFrame | None = None,
    risk_free_annual: float = 0.045,
) -> dict:
    """
    Portfolio-level risk metrics from weighted daily returns.
    Returns Beta, Ann. Volatility, Sharpe, Sortino, VaR 95%, CVaR, Max Drawdown,
    plus drawdown_series and cum_returns Series for charting.
    Returns empty dict if insufficient data.
    """
    series: dict[str, pd.Series] = {}
    for ticker, data in held_data.items():
        hist = data.get("df") if data.get("df") is not None else data.get("history")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            closes = hist["Close"].dropna().copy()
            if closes.index.tz is not None:
                closes.index = closes.index.tz_localize(None)
            if len(closes) >= 10:
                series[ticker] = closes

    if not series:
        return {}

    prices = pd.DataFrame(series).ffill().dropna()
    if len(prices) < 10:
        return {}

    daily_returns = prices.pct_change().dropna()

    weights: dict[str, float] = {}
    for _, row in port_df.iterrows():
        t = row["Ticker"]
        if t in daily_returns.columns:
            weights[t] = float(row["Weight (%)"]) / 100.0

    if not weights:
        return {}

    total_w = sum(weights.values())
    if total_w == 0:
        return {}
    weights = {t: w / total_w for t, w in weights.items()}

    port_returns = pd.Series(0.0, index=daily_returns.index)
    for t, w in weights.items():
        port_returns += daily_returns[t] * w

    std_ret = port_returns.std()
    rf_daily = risk_free_annual / 252
    excess = port_returns - rf_daily

    sharpe = round(float((excess.mean() / std_ret) * np.sqrt(252)), 2) if std_ret > 0 else 0.0

    downside_std = port_returns[port_returns < rf_daily].std()
    sortino = (
        round(float((excess.mean() / downside_std) * np.sqrt(252)), 2)
        if (downside_std and not np.isnan(downside_std) and downside_std > 0)
        else 0.0
    )

    ann_vol = round(float(std_ret * np.sqrt(252) * 100), 1)

    var_pct = round(float(np.percentile(port_returns, 5) * 100), 2)
    threshold = np.percentile(port_returns, 5)
    bad_days = port_returns[port_returns <= threshold]
    cvar_pct = round(float(bad_days.mean() * 100), 2) if len(bad_days) > 0 else var_pct

    cum_ret = (1 + port_returns).cumprod()
    rolling_max = cum_ret.cummax()
    drawdown_series = (cum_ret - rolling_max) / rolling_max * 100
    max_dd = round(float(drawdown_series.min()), 1)

    beta = None
    if spy_df is not None and not spy_df.empty and "Close" in spy_df.columns:
        spy_ret = spy_df["Close"].pct_change().dropna().copy()
        if spy_ret.index.tz is not None:
            spy_ret.index = spy_ret.index.tz_localize(None)
        combined = pd.concat([port_returns, spy_ret], axis=1, keys=["port", "spy"]).dropna()
        if len(combined) >= 20:
            cov_val = combined.cov().loc["port", "spy"]
            mkt_var = combined["spy"].var()
            if mkt_var > 0:
                beta = round(float(cov_val / mkt_var), 2)

    return {
        "beta":            beta,
        "ann_volatility":  ann_vol,
        "sharpe":          sharpe,
        "sortino":         sortino,
        "var_95_pct":      var_pct,
        "cvar_95_pct":     cvar_pct,
        "max_drawdown":    max_dd,
        "drawdown_series": drawdown_series,
        "cum_returns":     cum_ret,
    }
