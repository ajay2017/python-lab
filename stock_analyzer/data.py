import yfinance as yf
import pandas as pd
from datetime import datetime, date
import pytz

from stock_analyzer import constants as _C
from stock_analyzer.providers.base import ProviderUnavailable
from stock_analyzer.providers.yfinance_provider import YFinanceProvider, _retry
from stock_analyzer.providers import orchestrator as _orch

_ET = pytz.timezone("America/New_York")

# Primary (yfinance) provider, used directly whenever the multi-source layer is
# OFF so behaviour is byte-for-byte the pre-provider code. When
# constants.DATA_MULTISOURCE_ENABLED is True, the public fetch_* functions route
# through `_orch` (failover chain yfinance → Finnhub → FMP + price cross-check).
# `_retry` is imported from the provider module for the one remaining
# direct-yfinance call (fetch_curated_news).
_PRIMARY = YFinanceProvider()


def crosscheck_price(ticker: str, primary_price: float,
                     primary_prev_close: float | None = None) -> dict | None:
    """Deliberate single-ticker price cross-check (see orchestrator.crosscheck_price).
    Returns None when the layer is off so callers can guard on one truthiness check."""
    if not _C.DATA_MULTISOURCE_ENABLED:
        return None
    return _orch.crosscheck_price(ticker, primary_price, primary_prev_close)


def crosscheck_prices(tickers: list[str]) -> dict[str, dict]:
    """Batch price cross-check for held positions (see orchestrator.crosscheck_batch).
    Returns {} when the layer is off."""
    if not _C.DATA_MULTISOURCE_ENABLED:
        return {}
    return _orch.crosscheck_batch(tickers)


def crosscheck_validator_degraded() -> str | None:
    """Validator source name when the live-price cross-check validator is RED in
    api_health (so the cross-check is being skipped), else None. Lets the UI show
    a 'cross-check paused — validator degraded' note (see orchestrator)."""
    if not _C.DATA_MULTISOURCE_ENABLED:
        return None
    return _orch.live_price_validator_degraded()


def divergence_widened(today_gap_pct: float | None, prior_gap_pct: float | None,
                        min_widen_pp: float = 1.0) -> bool:
    """
    True if today's cross-check gap is at least min_widen_pp percentage points
    larger than the prior recorded gap. False if either value is None (can't
    compare), or if the gap has narrowed/stayed flat. Never raises.

    min_widen_pp is a display-annotation threshold, not a policy/gate value —
    it decides whether to APPEND a sentence to an existing banner, nothing more.
    """
    try:
        if today_gap_pct is None or prior_gap_pct is None:
            return False
        # float() coerce defensively — Supabase can return numeric columns as
        # JSON strings under some client configs; a bare subtraction would then
        # raise and silently drop the annotation via the except below. Coercing
        # here keeps the annotation robust without weakening the "never raises,
        # display-only" contract.
        return (float(today_gap_pct) - float(prior_gap_pct)) >= min_widen_pp
    except Exception:
        return False


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
    if _C.DATA_MULTISOURCE_ENABLED:
        return _orch.get_history(ticker, period)
    return _PRIMARY.price_history(ticker, period)


def fetch_ticker_bundle(ticker: str, period: str = "6mo") -> dict:
    """Single session — fetches history, info, news, earnings and revisions in one go."""
    if _C.DATA_MULTISOURCE_ENABLED:
        return _orch.get_bundle(ticker, period)
    return _PRIMARY.bundle(ticker, period)


def fetch_market_indices() -> list[dict]:
    """Fetch DOW, S&P 500 and NASDAQ last price + daily change."""
    if _C.DATA_MULTISOURCE_ENABLED:
        return _orch.get_market_indices()
    return _PRIMARY.market_indices()


def fetch_live_prices(tickers: list[str]) -> dict[str, dict]:
    """
    Lightweight batch fetch of current prices only — bypasses the full history load.
    Returns {ticker: {"price": float, "prev_close": float|None, "change_pct": float|None, "fetched_at": str}}.
    prev_close / change_pct are None when the source omits the prior close (never
    fabricated as prev==price — that would disarm the cross-check's strict leg; M2).
    """
    if _C.DATA_MULTISOURCE_ENABLED:
        return _orch.get_live_prices(tickers)
    return _PRIMARY.live_prices(tickers)


def fetch_earnings_calendar(from_date: str, to_date: str) -> list[dict]:
    """Market-wide upcoming earnings for a date range (Catalyst Watch). Only the
    multi-source layer's FMP provider serves this; returns [] when the layer is
    off or no provider offers it, so the caller degrades gracefully."""
    if _C.DATA_MULTISOURCE_ENABLED:
        return _orch.get_earnings_calendar(from_date, to_date)
    return []


def fetch_next_earnings(ticker: str) -> str | None:
    """Light next-earnings-date for one ticker ('YYYY-MM-DD' or None) via yfinance.
    Per-name fallback for Catalyst Watch when the market-wide FMP calendar is
    unavailable — covers universe names without a full bundle load."""
    return _PRIMARY.next_earnings(ticker)


def is_market_holiday(d: date) -> bool:
    """True if `d` is a full-day NYSE closure (per the hardcoded calendar)."""
    return d.isoformat() in _C.NYSE_HOLIDAYS


def is_trading_day(d: date) -> bool:
    """True if `d` is a regular NYSE session day — weekday AND not a holiday.
    Early-close (half) days are still trading days. The single source of truth
    for 'is the market supposed to be open today'; any new date logic that skips
    weekends should use this so it skips holidays too."""
    return d.weekday() < 5 and not is_market_holiday(d)


def _early_close_hour(d: date) -> float | None:
    """ET hour the NYSE closes early on `d` (e.g. 13.0 = 1pm), else None."""
    return _C.NYSE_EARLY_CLOSES.get(d.isoformat())


def market_status() -> dict:
    """Returns current NYSE market status and a human-readable label.

    Holiday-aware: consults the hardcoded NYSE calendar (constants.NYSE_HOLIDAYS
    / NYSE_EARLY_CLOSES) so it no longer shows "Market Open" on a closed holiday.
    `calendar_stale` is True once the system year passes the last hardcoded year
    (MARKET_CALENDAR_LAST_YEAR) — the holiday set can't be trusted past then, so
    callers should surface a "update the calendar" warning rather than silently
    treat future holidays as open.
    """
    now_et   = datetime.now(_ET)
    today    = now_et.date()
    weekday  = now_et.weekday()          # 0=Mon … 4=Fri
    hour     = now_et.hour + now_et.minute / 60
    stale    = now_et.year > _C.MARKET_CALENDAR_LAST_YEAR
    early_hr = _early_close_hour(today)

    if weekday >= 5:
        label, color, is_open = "Market Closed (Weekend)", "#888", False
    elif is_market_holiday(today):
        label, color, is_open = "Market Closed (Holiday)", "#888", False
    elif early_hr is not None and hour >= early_hr:
        # Half-day: traded this morning, now closed for the early-close holiday.
        label, color, is_open = "Market Closed (Early Close)", "#888", False
    elif 9.5 <= hour < 16.0:
        label, color, is_open = "Market Open", "#00C851", True
    elif 4.0 <= hour < 9.5:
        label, color, is_open = "Pre-Market", "#ffbb33", False
    elif 16.0 <= hour < 20.0:
        label, color, is_open = "After-Hours", "#ffbb33", False
    else:
        label, color, is_open = "Market Closed", "#888", False

    return {
        "label":         label,
        "color":         color,
        "is_open":       is_open,
        "time_et":       now_et.strftime("%H:%M ET"),
        "calendar_stale": stale,
    }


def fetch_spy(period: str = "6mo") -> pd.DataFrame:
    return fetch_price_history("SPY", period)


def fetch_tlt(period: str = "3mo") -> pd.DataFrame:
    """iShares 20-Year Treasury ETF — proxy for long-rate sensitivity."""
    return fetch_price_history("TLT", period)


def fetch_vix(period: str = "1mo") -> pd.DataFrame:
    """CBOE Volatility Index (^VIX) price history — the fear gauge.

    Used by the risk-off de-risk overlay (exit-discipline Phase 2) for the
    volatility regime leg. Same provider path as fetch_spy."""
    return fetch_price_history("^VIX", period)


def fetch_risk_free_rate(fallback: float = 0.045) -> float:
    """
    Return the current annualised risk-free rate from the 13-week T-bill (^IRX).
    ^IRX quotes in percentage points (e.g. 5.25 = 5.25%), so the provider
    divides by 100. Falls back to `fallback` on any provider failure.
    """
    try:
        if _C.DATA_MULTISOURCE_ENABLED:
            return _orch.get_risk_free_rate()
        return _PRIMARY.risk_free_rate()
    except ProviderUnavailable:
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
    info = _PRIMARY.info(ticker)
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
