import yfinance as yf
import pandas as pd
from datetime import datetime
import pytz

_ET = pytz.timezone("America/New_York")


DEFAULT_TICKERS = {
    "Micron Technology": "MU",
    "AMD": "AMD",
    "Intel": "INTC",
    "NVIDIA": "NVDA",
    "Qualcomm": "QCOM",
    "Texas Instruments": "TXN",
    "Broadcom": "AVGO",
    "Applied Materials": "AMAT",
}


def fetch_price_history(ticker: str, period: str = "6mo") -> pd.DataFrame:
    stock = yf.Ticker(ticker)
    df = stock.history(period=period)
    df.index = pd.to_datetime(df.index)
    return df


def fetch_live_prices(tickers: list[str]) -> dict[str, dict]:
    """
    Lightweight batch fetch of current prices only — bypasses the full history load.
    Returns {ticker: {"price": float, "prev_close": float, "change_pct": float, "fetched_at": str}}.
    """
    results = {}
    if not tickers:
        return results
    try:
        raw = yf.download(
            tickers, period="2d", auto_adjust=True,
            progress=False, threads=True,
        )
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
        for t in tickers:
            try:
                col = close[t] if t in close.columns else close.iloc[:, 0]
                col = col.dropna()
                if len(col) < 1:
                    continue
                price = float(col.iloc[-1])
                prev  = float(col.iloc[-2]) if len(col) >= 2 else price
                results[t] = {
                    "price":      round(price, 2),
                    "prev_close": round(prev, 2),
                    "change_pct": round((price - prev) / prev * 100, 2) if prev else 0.0,
                    "fetched_at": datetime.now(_ET).strftime("%H:%M:%S ET"),
                }
            except Exception:
                continue
    except Exception:
        pass
    return results


def market_status() -> dict:
    """Returns current NYSE market status and a human-readable label."""
    now_et = datetime.now(_ET)
    weekday = now_et.weekday()          # 0=Mon … 4=Fri
    hour    = now_et.hour + now_et.minute / 60

    if weekday >= 5:
        label, color, is_open = "Market Closed (Weekend)", "#888", False
    elif 9.5 <= hour < 16.0:
        label, color, is_open = "Market Open", "#00C851", True
    elif 4.0 <= hour < 9.5:
        label, color, is_open = "Pre-Market", "#ffbb33", False
    elif 16.0 <= hour < 20.0:
        label, color, is_open = "After-Hours", "#ffbb33", False
    else:
        label, color, is_open = "Market Closed", "#888", False

    return {
        "label":   label,
        "color":   color,
        "is_open": is_open,
        "time_et": now_et.strftime("%H:%M ET"),
    }


def fetch_info(ticker: str) -> dict:
    stock = yf.Ticker(ticker)
    return stock.info


def fetch_news(ticker: str) -> list[dict]:
    stock = yf.Ticker(ticker)
    return stock.news or []


def fetch_spy(period: str = "6mo") -> pd.DataFrame:
    return fetch_price_history("SPY", period)


def fetch_earnings_date(ticker: str) -> str | None:
    try:
        cal = yf.Ticker(ticker).calendar
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date") or cal.get("earningsDate")
            if dates:
                return str(dates[0])[:10]
    except Exception:
        pass
    return None


def fetch_financials(ticker: str) -> dict:
    stock = yf.Ticker(ticker)
    info = stock.info
    return {
        "pe_ratio": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "eps": info.get("trailingEps"),
        "revenue_growth": info.get("revenueGrowth"),
        "earnings_growth": info.get("earningsGrowth"),
        "profit_margins": info.get("profitMargins"),
        "debt_to_equity": info.get("debtToEquity"),
        "return_on_equity": info.get("returnOnEquity"),
        "current_ratio": info.get("currentRatio"),
        "market_cap": info.get("marketCap"),
        "52_week_high": info.get("fiftyTwoWeekHigh"),
        "52_week_low": info.get("fiftyTwoWeekLow"),
        "analyst_target": info.get("targetMeanPrice"),
        "recommendation": info.get("recommendationMean"),
    }
