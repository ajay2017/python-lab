from stock_analyzer.providers.finnhub_provider import FinnhubProvider
from stock_analyzer.constants import (
    NEWS_SENTIMENT_BULLISH_THRESHOLD,
    NEWS_SENTIMENT_BEARISH_THRESHOLD,
    NEWS_SENTIMENT_SHIFT_ALERT_BULLISH,
    NEWS_SENTIMENT_SHIFT_BUZZ_MIN,
)


def fetch_sentiment_for_tickers(tickers: list[str]) -> dict[str, dict]:
    """Fetch Finnhub news sentiment for a list of tickers.

    Returns {ticker: sentiment_dict} for tickers where data is available.
    Tickers that fail or return None are omitted from the result.
    Returns {} if Finnhub is not configured, tickers is empty, or every
    per-ticker fetch failed/raised — these three cases are intentionally
    indistinguishable to the caller (all mean "no sentiment data available").
    """
    provider = FinnhubProvider()
    if not provider.is_configured():
        return {}
    results: dict[str, dict] = {}
    for ticker in tickers:
        try:
            data = provider.fetch_news_sentiment(ticker)
            if data is not None:
                results[ticker] = data
        except Exception:
            continue
    return results


def sentiment_label(bullish_pct: float) -> tuple[str, str]:
    """Map a bullish_pct (0–1) to a (label, emoji) display pair."""
    if bullish_pct >= NEWS_SENTIMENT_BULLISH_THRESHOLD:
        return ("Bullish", "🟢")
    if bullish_pct < NEWS_SENTIMENT_BEARISH_THRESHOLD:
        return ("Bearish", "🔴")
    return ("Neutral", "🟡")


def is_sentiment_shift(sentiment: dict) -> bool:
    """True when a held position's sentiment warrants a brief awareness note.

    Requires BOTH: bullish_pct is below the alert threshold AND coverage
    is above-average (buzz_score > minimum). Low-buzz bearishness = thin/stale
    data; don't alert on it.
    """
    bullish_pct = sentiment.get("bullish_pct")
    if bullish_pct is None:
        return False
    buzz_score = sentiment.get("buzz_score")
    if buzz_score is None:
        return False
    return bullish_pct < NEWS_SENTIMENT_SHIFT_ALERT_BULLISH and buzz_score > NEWS_SENTIMENT_SHIFT_BUZZ_MIN
