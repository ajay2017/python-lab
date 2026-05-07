"""
Sentiment Velocity — rate of change in news sentiment over time.

Splits each ticker's raw news into a recent window (0–7 days) and a
prior window (8–30 days), computes VADER sentiment for each window,
then derives velocity (recent − prior) and detects price-sentiment
divergences that often precede reversals.
"""

from datetime import datetime as _dt, timezone as _tz
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer as _VADER

_va = _VADER()

_RECENT_DAYS = 7
_PRIOR_DAYS  = 30
_VELOCITY_THRESHOLD = 0.12   # meaningful shift in compound score
_DIVERGENCE_PRICE_PCT = 3.0  # % price move needed to flag divergence


def _score(titles: list[str]) -> float | None:
    if not titles:
        return None
    scores = [_va.polarity_scores(t)["compound"] for t in titles]
    return round(sum(scores) / len(scores), 3)


def _parse_ts(item: dict) -> int:
    ts = item.get("providerPublishTime") or 0
    if not ts:
        content  = item.get("content") or {}
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
    Returns velocity dict for a single ticker.

    Keys:
      ticker, recent_score, prior_score, velocity, direction,
      recent_count, prior_count, divergence, divergence_type,
      signal, headline_sample (list of recent titles with scores)
    """
    now_ts   = int(_dt.now(_tz.utc).timestamp())
    cutoff_r = now_ts - _RECENT_DAYS * 86400
    cutoff_p = now_ts - _PRIOR_DAYS  * 86400

    recent_titles, prior_titles = [], []
    for item in news_raw or []:
        ts  = _parse_ts(item)
        ttl = _title(item)
        if not ttl or ts <= 0:
            continue
        if ts >= cutoff_r:
            recent_titles.append(ttl)
        elif ts >= cutoff_p:
            prior_titles.append(ttl)

    recent_score = _score(recent_titles)
    prior_score  = _score(prior_titles)

    # Velocity
    velocity = None
    if recent_score is not None and prior_score is not None:
        velocity = round(recent_score - prior_score, 3)

    # Direction label
    if velocity is None:
        direction = "Insufficient data"
    elif velocity >  _VELOCITY_THRESHOLD:
        direction = "Improving ↑"
    elif velocity < -_VELOCITY_THRESHOLD:
        direction = "Deteriorating ↓"
    else:
        direction = "Stable →"

    # Price–sentiment divergence
    divergence      = False
    divergence_type = None
    if velocity is not None and price_ret_7d is not None:
        if price_ret_7d > _DIVERGENCE_PRICE_PCT and velocity < -_VELOCITY_THRESHOLD:
            divergence      = True
            divergence_type = "BEARISH"   # price up but sentiment falling
        elif price_ret_7d < -_DIVERGENCE_PRICE_PCT and velocity > _VELOCITY_THRESHOLD:
            divergence      = True
            divergence_type = "BULLISH"   # price down but sentiment recovering

    # Composite signal
    if divergence and divergence_type == "BEARISH":
        signal = "⚠️ Divergence — price rising but sentiment falling"
    elif divergence and divergence_type == "BULLISH":
        signal = "🔄 Divergence — price falling but sentiment recovering"
    elif direction == "Improving ↑":
        signal = "✅ Sentiment improving"
    elif direction == "Deteriorating ↓":
        signal = "🔴 Sentiment deteriorating"
    else:
        signal = "—"

    # Headline sample (recent, with scores)
    headline_sample = []
    for ttl in recent_titles[:5]:
        sc = _va.polarity_scores(ttl)["compound"]
        headline_sample.append({"title": ttl, "score": round(sc, 3)})

    return {
        "ticker":           ticker,
        "recent_score":     recent_score,
        "prior_score":      prior_score,
        "velocity":         velocity,
        "direction":        direction,
        "recent_count":     len(recent_titles),
        "prior_count":      len(prior_titles),
        "price_ret_7d":     price_ret_7d,
        "divergence":       divergence,
        "divergence_type":  divergence_type,
        "signal":           signal,
        "headline_sample":  headline_sample,
    }


def build_sentiment_dashboard(
    port_df,
    held_data: dict,
) -> list[dict]:
    """
    Runs compute_velocity for every holding.
    Returns list sorted by abs(velocity) descending (biggest moves first).
    """
    import pandas as pd

    results = []
    for _, row in port_df.iterrows():
        ticker    = row["Ticker"]
        data      = held_data.get(ticker) or {}
        news_raw  = data.get("news_raw") or []

        # 7-day price return from history
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

        v = compute_velocity(ticker, news_raw, price_ret_7d)
        results.append(v)

    results.sort(
        key=lambda x: (
            x["divergence"],                          # divergences first
            abs(x["velocity"]) if x["velocity"] is not None else 0,
        ),
        reverse=True,
    )
    return results
