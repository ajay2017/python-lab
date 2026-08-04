"""
Sentiment Velocity — rate of change in news sentiment.

yfinance only returns the most recent ~10-20 news articles per ticker,
all typically from the last few days.  Fixed time windows (0-7d vs 8-30d)
therefore always leave the prior window empty, producing "Insufficient data."

Fix: sort all available articles by timestamp, split into newest half (recent)
vs oldest half (prior), and compute velocity as recent_score - prior_score.
Works as long as >=4 articles exist.  Also detects price-sentiment divergences.
"""

from datetime import datetime as _dt, timezone as _tz
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer as _VADER

from stock_analyzer.constants import (
    SENTIMENT_VELOCITY_THRESHOLD as _VELOCITY_THRESHOLD,
    SENTIMENT_DIVERGENCE_PRICE_PCT as _DIVERGENCE_PRICE_PCT,
    SENTIMENT_VELOCITY_MIN_ARTICLES as _MIN_ARTICLES,
)

_va = _VADER()


def _score(titles: list[str]) -> float | None:
    if not titles:
        return None
    scores = [_va.polarity_scores(t)["compound"] for t in titles]
    return round(sum(scores) / len(scores), 3)


def _parse_ts(item: dict) -> int:
    ts = item.get("providerPublishTime") or 0
    if not ts:
        content = item.get("content") or {}
        pub_date = content.get("pubDate") or ""
        if pub_date:
            try:
                dt = _dt.fromisoformat(pub_date.replace("Z", "+00:00"))
                ts = int(dt.timestamp())
            except Exception:
                ts = 0
    return ts


def _title(item: dict) -> str:
    content = item.get("content") or {}
    return (item.get("title") or content.get("title") or "").strip()


def compute_velocity(
    ticker: str,
    news_raw: list[dict],
    price_ret_7d: float | None = None,
) -> dict:
    """
    Compute sentiment velocity for a single ticker.

    Splits available articles into newest-half (recent) vs oldest-half (prior)
    by timestamp, then derives velocity = recent_score - prior_score.
    Requires >= _MIN_ARTICLES articles; otherwise returns single-window score only.
    """
    # Collect articles with valid titles and timestamps
    articles = []
    for item in news_raw or []:
        ts  = _parse_ts(item)
        ttl = _title(item)
        if ttl and ts > 0:
            articles.append((ts, ttl))

    # Sort newest first
    articles.sort(key=lambda x: x[0], reverse=True)

    all_titles = [t for _, t in articles]
    current_score = _score(all_titles)

    if len(articles) < _MIN_ARTICLES:
        return {
            "ticker":          ticker,
            "recent_score":    current_score,
            "prior_score":     None,
            "velocity":        None,
            "direction":       "Single window only" if articles else "No news",
            "recent_count":    len(articles),
            "prior_count":     0,
            "price_ret_7d":    price_ret_7d,
            "divergence":      False,
            "divergence_type": None,
            "signal":          f"Score: {current_score:+.3f} ({len(articles)} articles)" if current_score is not None else "No news data",
            "headline_sample": [
                {"title": t, "score": round(_va.polarity_scores(t)["compound"], 3)}
                for _, t in articles[:5]
            ],
        }

    # Split: newest half = recent, oldest half = prior
    mid = len(articles) // 2
    recent_titles = [t for _, t in articles[:mid]]
    prior_titles  = [t for _, t in articles[mid:]]

    recent_score = _score(recent_titles)
    prior_score  = _score(prior_titles)

    velocity = None
    if recent_score is not None and prior_score is not None:
        velocity = round(recent_score - prior_score, 3)

    # Direction
    if velocity is None:
        direction = "Insufficient data"
    elif velocity > _VELOCITY_THRESHOLD:
        direction = "Improving ↑"
    elif velocity < -_VELOCITY_THRESHOLD:
        direction = "Deteriorating ↓"
    else:
        direction = "Stable →"

    # Price-sentiment divergence
    divergence      = False
    divergence_type = None
    if velocity is not None and price_ret_7d is not None:
        if price_ret_7d > _DIVERGENCE_PRICE_PCT and velocity < -_VELOCITY_THRESHOLD:
            divergence      = True
            divergence_type = "BEARISH"
        elif price_ret_7d < -_DIVERGENCE_PRICE_PCT and velocity > _VELOCITY_THRESHOLD:
            divergence      = True
            divergence_type = "BULLISH"

    # Signal label
    if divergence and divergence_type == "BEARISH":
        signal = "⚠️ Divergence — price rising but sentiment falling"
    elif divergence and divergence_type == "BULLISH":
        signal = "🔄 Divergence — price falling but sentiment recovering"
    elif direction == "Improving ↑":
        signal = "✅ Sentiment improving"
    elif direction == "Deteriorating ↓":
        signal = "🔴 Sentiment deteriorating"
    else:
        signal = "Stable"

    headline_sample = [
        {"title": t, "score": round(_va.polarity_scores(t)["compound"], 3)}
        for _, t in articles[:5]
    ]

    return {
        "ticker":          ticker,
        "recent_score":    recent_score,
        "prior_score":     prior_score,
        "velocity":        velocity,
        "direction":       direction,
        "recent_count":    len(recent_titles),
        "prior_count":     len(prior_titles),
        "price_ret_7d":    price_ret_7d,
        "divergence":      divergence,
        "divergence_type": divergence_type,
        "signal":          signal,
        "headline_sample": headline_sample,
    }


def build_sentiment_dashboard(port_df, held_data: dict) -> list[dict]:
    """
    Run compute_velocity for every holding.
    Returns list sorted: divergences first, then by abs(velocity) descending.
    """
    results = []
    for _, row in port_df.iterrows():
        ticker   = row["Ticker"]
        data     = held_data.get(ticker) or {}
        news_raw = data.get("news_raw") or []

        price_ret_7d = None
        df_hist = data.get("df")
        if df_hist is not None and not df_hist.empty and "Close" in df_hist.columns:
            closes = df_hist["Close"].dropna()
            if len(closes) >= 8:
                try:
                    price_ret_7d = round(
                        (float(closes.iloc[-1]) / float(closes.iloc[-8]) - 1) * 100, 2
                    )
                except Exception:
                    pass

        results.append(compute_velocity(ticker, news_raw, price_ret_7d))

    results.sort(
        key=lambda x: (
            x["divergence"],
            abs(x["velocity"]) if x["velocity"] is not None else 0,
        ),
        reverse=True,
    )
    return results
