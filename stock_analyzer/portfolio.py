import math
import pandas as pd


def _safe_float(val, default: float = 0.0) -> float:
    """Convert val to float, returning default for None/NaN/empty-string."""
    if val is None:
        return default
    try:
        f = float(val)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


TICKER_SECTORS = {
    "AVGO": "Semiconductors", "NVDA": "Semiconductors", "AMD": "Semiconductors",
    "MU": "Semiconductors", "QCOM": "Semiconductors", "INTC": "Semiconductors",
    "AMAT": "Semiconductors", "ASML": "Semiconductors", "TXN": "Semiconductors",
    "AAPL": "Consumer Tech", "AMZN": "Consumer Tech", "NFLX": "Consumer Tech",
    "SHOP": "Consumer Tech", "UBER": "Consumer Tech", "ABNB": "Consumer Tech",
    "TSLA": "EV & Auto", "ENPH": "Clean Energy", "FSLR": "Clean Energy",
    "NEE": "Clean Energy", "BEP": "Clean Energy",
    "CRWD": "Cybersecurity", "NET": "Cybersecurity", "PANW": "Cybersecurity",
    "ZS": "Cybersecurity", "FTNT": "Cybersecurity", "OKTA": "Cybersecurity", "S": "Cybersecurity",
    "DELL": "Enterprise Tech", "ORCL": "Enterprise Tech", "IBM": "Enterprise Tech",
    "HPE": "Enterprise Tech", "SAP": "Enterprise Tech",
    "PLTR": "AI & Data", "AI": "AI & Data", "MDB": "AI & Data", "SNOW": "AI & Data",
    "PATH": "AI & Data", "IONQ": "AI & Data",
    "MSFT": "AI & Cloud", "GOOGL": "AI & Cloud", "META": "AI & Cloud",
    "CRM": "AI & Cloud", "NOW": "AI & Cloud", "DDOG": "AI & Cloud",
    "LLY": "Healthcare", "NVO": "Healthcare", "ABBV": "Healthcare",
    "ISRG": "Healthcare", "MRNA": "Healthcare", "REGN": "Healthcare",
    "JPM": "Financials", "V": "Financials", "MA": "Financials",
    "GS": "Financials", "SQ": "Financials", "COIN": "Financials",
    "LMT": "Defense", "RTX": "Defense", "NOC": "Defense", "GD": "Defense",
    "XOM": "Energy", "CVX": "Energy", "OXY": "Energy", "COP": "Energy",
}

# Ratchet stop levels: as gains grow, floor the stop to protect accumulated profit
_RATCHET_LEVELS = [
    (75, 0.40, "Protect 40% gain"),
    (50, 0.25, "Protect 25% gain"),
    (25, 0.10, "Protect 10% gain"),
    (10, 0.02, "Breakeven guard"),
]


def protective_stop(
    current_price: float, avg_cost: float, atr_stop: float
) -> tuple[float, str]:
    """
    Ratchet stop upward as gains accumulate so profits are never fully surrendered.
    Returns (stop_price, label).
    """
    if avg_cost <= 0:
        return atr_stop, "ATR Stop"
    gain_pct = (current_price - avg_cost) / avg_cost * 100
    for threshold, multiplier, label in _RATCHET_LEVELS:
        if gain_pct >= threshold:
            floor = avg_cost * (1 + multiplier)
            return round(max(atr_stop, floor), 2), label
    return round(atr_stop, 2), "ATR Stop"


def build_portfolio_df(
    holdings: list[dict], loaded_data: dict
) -> pd.DataFrame:
    """
    holdings: [{"ticker": "AVGO", "shares": 10, "avg_cost": 200.0}, ...]
    loaded_data: dict of ticker -> load_all() result
    """
    rows = []
    for h in holdings:
        ticker = str(h.get("Ticker", h.get("ticker", "")) or "").strip().upper()
        shares = _safe_float(h.get("Shares", h.get("shares")))
        avg_cost = _safe_float(h.get("Avg Cost ($)", h.get("avg_cost")))
        if not ticker or shares <= 0 or avg_cost <= 0:
            continue
        r = loaded_data.get(ticker)
        if not r or not r.get("current_price"):
            continue

        price = r["current_price"]
        market_val = round(price * shares, 2)
        cost_basis = round(avg_cost * shares, 2)
        pnl_dollar = round(market_val - cost_basis, 2)
        pnl_pct = round((price - avg_cost) / avg_cost * 100, 1)

        atr_stop = r.get("stop") or price * 0.92
        stop, stop_label = protective_stop(price, avg_cost, atr_stop)
        gap_to_stop = round((price - stop) / price * 100, 1)

        rows.append({
            "Ticker": ticker,
            "Sector": TICKER_SECTORS.get(ticker, "Other"),
            "Shares": int(shares),
            "Avg Cost": avg_cost,
            "Price": price,
            "Market Value": market_val,
            "P&L ($)": pnl_dollar,
            "P&L (%)": pnl_pct,
            "Weight (%)": 0.0,
            "Stop": stop,
            "Stop Type": stop_label,
            "Gap to Stop (%)": gap_to_stop,
            "Signal": f"{r['rec']['icon']} {r['rec']['label']}",
            "Score": r["total"],
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        total_val = df["Market Value"].sum()
        df["Weight (%)"] = (df["Market Value"] / total_val * 100).round(1)
    return df


def sector_exposure(portfolio_df: pd.DataFrame) -> pd.DataFrame:
    if portfolio_df.empty:
        return pd.DataFrame()
    return (
        portfolio_df.groupby("Sector")["Market Value"]
        .sum()
        .reset_index()
        .rename(columns={"Market Value": "Value"})
        .assign(Pct=lambda d: (d["Value"] / d["Value"].sum() * 100).round(1))
        .sort_values("Pct", ascending=False)
    )


def alerts(portfolio_df: pd.DataFrame) -> list[dict]:
    """Returns list of {level, message} dicts. Levels: danger, warning, info."""
    result = []
    if portfolio_df.empty:
        return result

    for _, row in portfolio_df.iterrows():
        w = row["Weight (%)"]
        gap = row["Gap to Stop (%)"]
        pnl = row["P&L (%)"]
        signal = row["Signal"]
        ticker = row["Ticker"]

        if gap < 3:
            result.append({
                "level": "danger",
                "msg": f"🔴 **{ticker}** is within {gap:.1f}% of stop loss ${row['Stop']:.2f} — review immediately",
            })
        elif gap < 7:
            result.append({
                "level": "warning",
                "msg": f"🟡 **{ticker}** is {gap:.1f}% above stop ${row['Stop']:.2f} — monitor closely",
            })
        if w > 20:
            result.append({
                "level": "warning",
                "msg": f"⚠️ **{ticker}** is {w:.1f}% of portfolio — above 20% concentration threshold",
            })
        if "Sell" in signal and pnl > 15:
            result.append({
                "level": "warning",
                "msg": f"📉 **{ticker}** signal turned bearish with {pnl:.1f}% gain — consider taking partial profits",
            })
        if "Sell" in signal and pnl < -8:
            result.append({
                "level": "danger",
                "msg": f"⛔ **{ticker}** bearish signal with {pnl:.1f}% loss — stop at ${row['Stop']:.2f}",
            })

    # Sector concentration
    sector_exp = sector_exposure(portfolio_df)
    for _, row in sector_exp.iterrows():
        if row["Pct"] > 40:
            result.append({
                "level": "warning",
                "msg": f"🏭 **{row['Sector']}** represents {row['Pct']:.0f}% of portfolio — high sector concentration",
            })
    return result


def rebalance_actions(portfolio_df: pd.DataFrame) -> list[str]:
    actions = []
    if portfolio_df.empty:
        return actions
    for _, row in portfolio_df.iterrows():
        w = row["Weight (%)"]
        pnl = row["P&L (%)"]
        ticker = row["Ticker"]
        price = row["Price"]
        shares = row["Shares"]
        signal = row["Signal"]

        if w > 18 and pnl > 20:
            trim_val = row["Market Value"] * (w - 15) / 100
            trim_shares = max(1, int(trim_val / price))
            actions.append(
                f"**Trim {ticker}**: Sell ~{trim_shares} shares (${trim_val:,.0f}) — "
                f"reduce from {w:.0f}% → ~15% weight while locking in {pnl:.0f}% gain"
            )
        if "Strong Buy" in signal and w < 5 and row["Score"] > 70:
            actions.append(
                f"**Add to {ticker}**: Strong conviction ({row['Score']:.0f}/100) but "
                f"only {w:.1f}% of portfolio — consider building to 8–10%"
            )
        if "Sell" in signal and pnl > 0:
            actions.append(
                f"**Review {ticker}**: Bearish signal with {pnl:.1f}% gain — "
                "sell half now and trail the rest with stop"
            )
    return actions
