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
        # `.get(key, {})` only substitutes the default when the KEY is absent --
        # yfinance's news feed can return `"content": None` (present, null) for
        # some articles, which `.get("content", {})` passes through as `None`,
        # not `{}` -- the next `.get("title", ...)` in the chain then raises
        # AttributeError. `... or {}` protects against both "absent" and
        # "present but None" (2026-09-25, root cause of a live "could not load"
        # incident traced via a Railway traceback). Same fix applied to the two
        # latent siblings below (canonicalUrl/clickThroughUrl) -- same shape,
        # not yet observed firing, but provably the same bug if Yahoo ever
        # nulls those fields too.
        content = item.get("content")
        if content is None:
            content = {}
        title = item.get("title") or content.get("title", "")
        if not title:
            continue
        vs = _analyzer.polarity_scores(title)
        compound = vs["compound"]
        scores.append(compound)
        label = _sentiment_label(compound)
        _canonical_url = content.get("canonicalUrl")
        if _canonical_url is None:
            _canonical_url = {}
        _click_url = content.get("clickThroughUrl")
        if _click_url is None:
            _click_url = {}
        url = (
            item.get("link") or
            _canonical_url.get("url") or
            _click_url.get("url") or
            ""
        )
        results.append({
            "headline": title,
            "score": compound,
            "label": label,
            "url": url,
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
