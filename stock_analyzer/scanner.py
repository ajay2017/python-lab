import yfinance as yf
import pandas as pd
import pandas_ta as ta

SECTOR_UNIVERSE = {
    "AI & Cloud": ["MSFT", "GOOGL", "META", "AMZN", "CRM", "NOW", "DDOG", "SNOW"],
    "Cybersecurity": ["PANW", "CRWD", "ZS", "NET", "FTNT", "OKTA", "S"],
    "Semiconductors": ["NVDA", "AMD", "AVGO", "MU", "QCOM", "AMAT", "ASML", "INTC"],
    "Consumer Tech": ["AAPL", "NFLX", "SHOP", "UBER", "ABNB"],
    "AI & Data Platforms": ["PLTR", "AI", "MDB", "SNOW", "PATH", "IONQ"],
    "EV & Clean Energy": ["TSLA", "ENPH", "FSLR", "NEE", "RIVN"],
    "Healthcare & Biotech": ["LLY", "NVO", "ABBV", "ISRG", "MRNA", "REGN", "AMGN"],
    "Financials & Fintech": ["JPM", "V", "MA", "GS", "SQ", "COIN", "PYPL"],
    "Enterprise Tech": ["DELL", "ORCL", "IBM", "HPE", "SAP"],
    "Defense & Aerospace": ["LMT", "RTX", "NOC", "GD", "BA"],
    "Energy": ["XOM", "CVX", "OXY", "COP", "SLB"],
    "Consumer Staples & Retail": ["COST", "NKE", "TJX", "WMT", "TGT"],
}


def _quick_score(ticker: str, df: pd.DataFrame) -> dict | None:
    try:
        close = df["Close"].dropna()
        volume = df["Volume"].dropna()
        if len(close) < 30:
            return None

        rsi_s = ta.rsi(close, length=14)
        sma20_s = ta.sma(close, length=20)
        sma50_s = ta.sma(close, length=50)

        price = float(close.iloc[-1])
        rsi = float(rsi_s.iloc[-1]) if rsi_s is not None and not rsi_s.empty else 50.0
        sma20 = float(sma20_s.iloc[-1]) if sma20_s is not None and not sma20_s.empty else price
        sma50 = float(sma50_s.iloc[-1]) if sma50_s is not None and not sma50_s.empty else price

        mom_1m = (price / float(close.iloc[-21]) - 1) * 100 if len(close) > 21 else 0.0
        mom_3m = (price / float(close.iloc[-63]) - 1) * 100 if len(close) > 63 else 0.0

        score = 0

        # RSI (30 pts) — reward the sweet spot 40–65, slightly oversold is also good
        if 40 <= rsi <= 65:
            score += 30
        elif rsi < 40:
            score += 22
        elif rsi < 75:
            score += 12
        else:
            score += 2

        # Trend alignment (35 pts)
        if price > sma20 > sma50:
            score += 35
        elif price > sma20:
            score += 20
        elif price > sma50:
            score += 10

        # 1-month momentum (20 pts)
        if mom_1m > 8:
            score += 20
        elif mom_1m > 3:
            score += 14
        elif mom_1m > 0:
            score += 7
        elif mom_1m > -5:
            score += 2

        # 3-month momentum (15 pts)
        if mom_3m > 15:
            score += 15
        elif mom_3m > 5:
            score += 10
        elif mom_3m > 0:
            score += 5

        # Trend label
        if price > sma20 > sma50:
            trend = "⬆⬆ Strong Uptrend"
        elif price > sma20:
            trend = "⬆ Uptrend"
        elif price > sma50:
            trend = "↔ Mixed"
        else:
            trend = "⬇ Downtrend"

        # Volume ratio (recent vs 20-day avg)
        vol_ratio = (
            float(volume.iloc[-5:].mean() / volume.iloc[-20:].mean())
            if len(volume) >= 20 and volume.iloc[-20:].mean() > 0 else 1.0
        )

        # Signal label
        if score >= 80:
            signal = "⬆⬆ Strong Buy"
        elif score >= 65:
            signal = "⬆ Buy"
        elif score >= 45:
            signal = "➡ Hold / Watch"
        elif score >= 30:
            signal = "⬇ Weak"
        else:
            signal = "⬇⬇ Avoid"

        return {
            "Ticker": ticker,
            "Price": round(price, 2),
            "Score": min(score, 100),
            "Signal": signal,
            "RSI": round(rsi, 1),
            "1M Momentum": round(mom_1m, 1),
            "3M Momentum": round(mom_3m, 1),
            "Trend": trend,
            "Vol Ratio": round(vol_ratio, 1),
        }
    except Exception:
        return None


def scan_sectors(selected_sectors: list[str], period: str = "6mo") -> pd.DataFrame:
    all_tickers, ticker_sector = [], {}
    for sector in selected_sectors:
        for t in SECTOR_UNIVERSE.get(sector, []):
            if t not in ticker_sector:
                all_tickers.append(t)
                ticker_sector[t] = sector

    if not all_tickers:
        return pd.DataFrame()

    try:
        raw = yf.download(
            all_tickers, period=period,
            auto_adjust=True, progress=False, threads=True,
        )
    except Exception:
        return pd.DataFrame()

    results = []
    for ticker in all_tickers:
        try:
            if len(all_tickers) == 1:
                df = raw
            elif isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(ticker, axis=1, level=1).dropna()
            else:
                continue
            if df.empty:
                continue
            result = _quick_score(ticker, df)
            if result:
                result["Sector"] = ticker_sector[ticker]
                results.append(result)
        except Exception:
            continue

    if not results:
        return pd.DataFrame()

    out = (
        pd.DataFrame(results)
        .sort_values("Score", ascending=False)
        .reset_index(drop=True)
    )
    out.insert(0, "Rank", range(1, len(out) + 1))
    return out
