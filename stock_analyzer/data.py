import time
import yfinance as yf
import pandas as pd
from datetime import datetime
import pytz
from stock_analyzer import api_health as _ah

_ET = pytz.timezone("America/New_York")


def _retry(fn, *args, retries: int = 3, backoff: float = 3.0, **kwargs):
    """Retry fn on Yahoo Finance 429 / rate-limit errors with linear backoff."""
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            msg = str(exc).lower()
            if any(k in msg for k in ("429", "too many", "rate limit", "rate-limit")):
                _ah.record("yahoo_finance", "rate_limit")
                if attempt < retries - 1:
                    time.sleep(backoff * (attempt + 1))
                    continue
            _ah.record("yahoo_finance", "error", msg=str(exc)[:120])
            raise


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
    def _fetch():
        df = yf.Ticker(ticker).history(period=period)
        df.index = pd.to_datetime(df.index)
        return df
    return _retry(_fetch)


def fetch_ticker_bundle(ticker: str, period: str = "6mo") -> dict:
    """Single yf.Ticker session — fetches history, info, news and earnings in one go."""
    def _fetch():
        t = yf.Ticker(ticker)
        hist = t.history(period=period)
        hist.index = pd.to_datetime(hist.index)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            pass
        news = []
        try:
            news = t.news or []
        except Exception:
            pass
        earnings = None
        try:
            cal = t.calendar
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date") or cal.get("earningsDate")
                if dates:
                    earnings = str(dates[0])[:10]
        except Exception:
            pass

        revisions = {}
        try:
            upg = t.upgrades_downgrades
            if upg is not None and not upg.empty:
                upg = upg.copy()
                try:
                    upg.index = pd.to_datetime(upg.index, utc=True)
                    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=90)
                    recent = upg[upg.index >= cutoff]
                except Exception:
                    recent = upg.head(20)
                actions = recent["Action"].str.lower() if not recent.empty else pd.Series([], dtype=str)
                ups   = int(actions.isin(["up", "init"]).sum())
                downs = int((actions == "down").sum())
                maint = int(actions.isin(["main", "reit"]).sum())
                revisions = {
                    "upgrades_90d":   ups,
                    "downgrades_90d": downs,
                    "maintained_90d": maint,
                    "net":            ups - downs,
                    "latest": [
                        {
                            "firm":       str(row.get("Firm", "")),
                            "to_grade":   str(row.get("ToGrade", "")),
                            "from_grade": str(row.get("FromGrade", "")),
                            "action":     str(row.get("Action", "")),
                        }
                        for _, row in upg.head(5).iterrows()
                    ],
                }
        except Exception:
            pass

        return {
            "history": hist, "info": info, "news": news,
            "earnings": earnings, "revisions": revisions,
        }

    result = _retry(_fetch)
    _ah.record("yahoo_finance", "success")
    return result


_INDICES = [
    ("^DJI",  "DOW",     "Dow Jones"),
    ("^GSPC", "S&P 500", "S&P 500"),
    ("^IXIC", "NASDAQ",  "Nasdaq Comp"),
]


def fetch_market_indices() -> list[dict]:
    """Fetch DOW, S&P 500 and NASDAQ last price + daily change."""
    results = []
    try:
        tickers = [t for t, _, _ in _INDICES]
        raw = _retry(
            yf.download, tickers,
            period="2d", auto_adjust=True, progress=False, threads=True,
        )
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
        for ticker, short, full in _INDICES:
            try:
                col = close[ticker] if ticker in close.columns else close.iloc[:, 0]
                col = col.dropna()
                if len(col) < 1:
                    continue
                price  = float(col.iloc[-1])
                prev   = float(col.iloc[-2]) if len(col) >= 2 else price
                change = price - prev
                change_pct = change / prev * 100 if prev else 0.0
                results.append({
                    "short":      short,
                    "full":       full,
                    "price":      price,
                    "change":     change,
                    "change_pct": round(change_pct, 2),
                    "fetched_at": datetime.now(_ET).strftime("%H:%M ET"),
                })
            except Exception:
                continue
    except Exception as _e:
        _ah.record("yahoo_finance", "error", msg=str(_e)[:80])
    if results:
        _ah.record("yahoo_finance", "success")
    else:
        _ah.record("yahoo_finance", "empty")
    return results


def fetch_live_prices(tickers: list[str]) -> dict[str, dict]:
    """
    Lightweight batch fetch of current prices only — bypasses the full history load.
    Returns {ticker: {"price": float, "prev_close": float, "change_pct": float, "fetched_at": str}}.
    """
    results = {}
    if not tickers:
        return results
    try:
        raw = _retry(
            yf.download, tickers,
            period="2d", auto_adjust=True, progress=False, threads=True,
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
    except Exception as _e:
        _ah.record("yahoo_finance", "error", msg=str(_e)[:80])
    if results:
        _ah.record("yahoo_finance", "success")
    else:
        _ah.record("yahoo_finance", "empty")
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


def fetch_spy(period: str = "6mo") -> pd.DataFrame:
    return fetch_price_history("SPY", period)


def fetch_risk_free_rate(fallback: float = 0.045) -> float:
    """
    Return the current annualised risk-free rate from the 13-week T-bill (^IRX).
    ^IRX quotes in percentage points (e.g. 5.25 = 5.25%), so we divide by 100.
    Falls back to `fallback` on any error.
    """
    try:
        hist = _retry(lambda: yf.Ticker("^IRX").history(period="5d"))
        if hist is not None and not hist.empty:
            return round(float(hist["Close"].iloc[-1]) / 100, 4)
    except Exception:
        pass
    return fallback


def fetch_financials_from_info(info: dict) -> dict:
    """Extract financials from a pre-fetched .info dict — no extra API call."""
    fcf = info.get("freeCashflow")
    mkt_cap = info.get("marketCap")
    fcf_yield = round(fcf / mkt_cap * 100, 2) if fcf and mkt_cap and mkt_cap > 0 else None

    short_pct = info.get("shortPercentOfFloat")

    return {
        # Core valuation
        "pe_ratio":         info.get("trailingPE"),
        "forward_pe":       info.get("forwardPE"),
        "eps":              info.get("trailingEps"),
        "market_cap":       mkt_cap,
        # Cash flow (primary institutional metric)
        "free_cashflow":    fcf,
        "fcf_yield":        fcf_yield,
        # Growth & quality
        "revenue_growth":   info.get("revenueGrowth"),
        "earnings_growth":  info.get("earningsGrowth"),
        "profit_margins":   info.get("profitMargins"),
        "return_on_equity": info.get("returnOnEquity"),
        "current_ratio":    info.get("currentRatio"),
        # Balance sheet
        "debt_to_equity":   info.get("debtToEquity"),
        # Price targets
        "52_week_high":     info.get("fiftyTwoWeekHigh"),
        "52_week_low":      info.get("fiftyTwoWeekLow"),
        "analyst_target":   info.get("targetMeanPrice"),
        "target_high":      info.get("targetHighPrice"),
        "target_low":       info.get("targetLowPrice"),
        "target_median":    info.get("targetMedianPrice"),
        # Analyst consensus
        "recommendation":           info.get("recommendationMean"),
        "num_analyst_opinions":     info.get("numberOfAnalystOpinions"),
        # Smart money signals
        "short_pct_float":          round(short_pct * 100, 1) if short_pct else None,
        "short_ratio":              info.get("shortRatio"),
        "held_pct_institutions":    round(info.get("heldPercentInstitutions", 0) * 100, 1)
                                    if info.get("heldPercentInstitutions") else None,
        "held_pct_insiders":        round(info.get("heldPercentInsiders", 0) * 100, 1)
                                    if info.get("heldPercentInsiders") else None,
    }


def fetch_financials(ticker: str) -> dict:
    """Fetch financials by ticker — prefer fetch_ticker_bundle for batch loads."""
    info = _retry(lambda: yf.Ticker(ticker).info or {})
    return fetch_financials_from_info(info)


# ── Curated news ──────────────────────────────────────────────────────────────

_TIER1 = frozenset([
    "reuters", "associated press", "ap", "wall street journal", "wsj",
    "financial times", "bloomberg", "cnbc", "barron's", "barrons",
])
_TIER2 = frozenset([
    "marketwatch", "yahoo finance", "seeking alpha", "zacks", "benzinga",
    "the motley fool", "motley fool", "forbes", "business insider",
    "investing.com", "thestreet", "nasdaq",
])


def fetch_curated_news(tickers: list[str], max_items: int = 20) -> list[dict]:
    """
    Aggregate and curate news across a set of tickers.
    Deduplicates by headline, scores sentiment with VADER, and
    ranks tier-1 sources (Reuters, Bloomberg, WSJ …) above the rest.
    Returns items sorted by (source tier, recency).
    """
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _va = SentimentIntensityAnalyzer()

    seen: set[str] = set()
    items: list[dict] = []

    for ticker in tickers:
        try:
            news = _retry(lambda t=ticker: yf.Ticker(t).news or [])
            for item in (news or [])[:8]:
                title, publisher, url, ts = _parse_news_item(item)
                if not title:
                    continue
                key = title.lower()[:70]
                if key in seen:
                    continue
                seen.add(key)

                pub_l = publisher.lower()
                tier = (1 if any(p in pub_l for p in _TIER1) else
                        2 if any(p in pub_l for p in _TIER2) else 3)

                compound = _va.polarity_scores(title)["compound"]
                label = ("Positive" if compound >= 0.05 else
                         "Negative" if compound <= -0.05 else "Neutral")

                items.append({
                    "ticker":    ticker,
                    "title":     title,
                    "url":       url,
                    "publisher": publisher,
                    "ts":        ts,
                    "compound":  round(compound, 2),
                    "label":     label,
                    "tier":      tier,
                })
        except Exception:
            continue

    # Tier-1 sources first; within same tier, newest first
    items.sort(key=lambda x: (x["tier"], -x["ts"]))
    return items[:max_items]


def _parse_news_item(item: dict) -> tuple[str, str, str, int]:
    """
    Extract (title, publisher, url, unix_ts) from a yfinance news item.
    Handles both the old flat structure (yfinance <1.3) and the new nested
    content structure (yfinance 1.3.x: item["content"]["title"], etc.).
    """
    content = item.get("content") or {}
    title = (item.get("title") or content.get("title") or "").strip()
    publisher = (
        item.get("publisher") or
        content.get("provider", {}).get("displayName") or
        "Unknown"
    )
    url = (
        item.get("link") or
        content.get("canonicalUrl", {}).get("url") or
        content.get("clickThroughUrl", {}).get("url") or
        ""
    )
    ts = item.get("providerPublishTime") or 0
    if not ts:
        pub_date = content.get("pubDate") or ""
        if pub_date:
            try:
                dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                ts = int(dt.timestamp())
            except Exception:
                ts = 0
    return title, publisher, url, ts


def curate_news_items(data_by_ticker: dict, max_items: int = 20) -> list[dict]:
    """
    Build curated news from already-fetched load_all() results — zero extra API calls.
    data_by_ticker: {ticker: load_all_result_dict, ...}  (each must have "news_raw" key)
    """
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _va = SentimentIntensityAnalyzer()

    seen: set[str] = set()
    items: list[dict] = []

    for ticker, r in data_by_ticker.items():
        for item in (r.get("news_raw") or [])[:8]:
            title, publisher, url, ts = _parse_news_item(item)
            if not title:
                continue
            key = title.lower()[:70]
            if key in seen:
                continue
            seen.add(key)

            pub_l = publisher.lower()
            tier = (1 if any(p in pub_l for p in _TIER1) else
                    2 if any(p in pub_l for p in _TIER2) else 3)

            compound = _va.polarity_scores(title)["compound"]
            label = ("Positive" if compound >= 0.05 else
                     "Negative" if compound <= -0.05 else "Neutral")

            items.append({
                "ticker":    ticker,
                "title":     title,
                "url":       url,
                "publisher": publisher,
                "ts":        ts,
                "compound":  round(compound, 2),
                "label":     label,
                "tier":      tier,
            })

    # Sort by recency first; within the same 30-minute window, prefer higher-tier sources
    items.sort(key=lambda x: (-(x["ts"] // 1800), x["tier"]))
    return items[:max_items]
