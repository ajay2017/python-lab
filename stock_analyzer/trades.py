import pandas as pd


def performance_stats(df: pd.DataFrame) -> dict:
    """Compute performance statistics from a trades DataFrame."""
    empty = {
        "total_trades": 0, "sell_trades": 0, "buy_trades": 0,
        "total_realized_pnl": 0.0, "wins": 0, "losses": 0,
        "win_rate": 0.0, "avg_winner": 0.0, "avg_loser": 0.0,
        "best_trade": None, "worst_trade": None,
        "realized_by_ticker": {},
    }
    if df is None or df.empty:
        return empty

    sells = df[df["action"] == "SELL"].copy()
    buys  = df[df["action"] == "BUY"].copy()

    if sells.empty:
        return {**empty, "total_trades": len(df), "buy_trades": len(buys)}

    with_pnl  = sells.dropna(subset=["realized_pnl"])
    winners   = with_pnl[with_pnl["realized_pnl"] > 0]
    losers    = with_pnl[with_pnl["realized_pnl"] < 0]

    total_pnl = float(with_pnl["realized_pnl"].sum())
    win_rate  = len(winners) / len(with_pnl) * 100 if len(with_pnl) else 0.0

    best  = with_pnl.loc[with_pnl["realized_pnl"].idxmax()].to_dict() if not with_pnl.empty else None
    worst = with_pnl.loc[with_pnl["realized_pnl"].idxmin()].to_dict() if not with_pnl.empty else None

    by_ticker = (
        with_pnl.groupby("ticker")["realized_pnl"]
        .sum().round(2).sort_values(ascending=False).to_dict()
    )

    return {
        "total_trades":       len(df),
        "sell_trades":        len(sells),
        "buy_trades":         len(buys),
        "total_realized_pnl": round(total_pnl, 2),
        "wins":               len(winners),
        "losses":             len(losers),
        "win_rate":           round(win_rate, 1),
        "avg_winner":         round(float(winners["realized_pnl"].mean()), 2) if not winners.empty else 0.0,
        "avg_loser":          round(float(losers["realized_pnl"].mean()),  2) if not losers.empty else 0.0,
        "best_trade":         best,
        "worst_trade":        worst,
        "realized_by_ticker": by_ticker,
    }


def compute_realized_pnl(shares: float, price: float, cost_basis: float | None) -> float | None:
    """(price - cost_basis) × shares for a SELL trade."""
    if cost_basis is None or cost_basis <= 0:
        return None
    return round((price - cost_basis) * shares, 2)
