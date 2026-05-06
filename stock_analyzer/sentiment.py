from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_analyzer = SentimentIntensityAnalyzer()


def analyze_news(news_items: list[dict]) -> tuple[float, list[dict]]:
    """
    Returns an average compound sentiment score (-1 to 1) and
    a list of annotated headline dicts.
    """
    results = []
    scores = []

    for item in news_items[:10]:  # cap at 10 headlines
        title = item.get("title") or item.get("content", {}).get("title", "")
        if not title:
            continue
        vs = _analyzer.polarity_scores(title)
        compound = vs["compound"]
        scores.append(compound)
        label = _sentiment_label(compound)
        results.append({
            "headline": title,
            "score": compound,
            "label": label,
            "url": item.get("link", ""),
        })

    avg = sum(scores) / len(scores) if scores else 0.0
    return round(avg, 3), results


def sentiment_score_0_100(avg_compound: float) -> float:
    """Normalize compound score (-1..1) to 0..100 scale."""
    return round((avg_compound + 1) / 2 * 100, 1)


def _sentiment_label(compound: float) -> str:
    if compound >= 0.05:
        return "Positive"
    elif compound <= -0.05:
        return "Negative"
    return "Neutral"
