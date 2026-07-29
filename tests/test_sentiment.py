"""Tests for stock_analyzer/sentiment.py — headline-level VADER sentiment
scoring (`analyze_news`), the -1..1 -> 0..100 remap (`sentiment_score_0_100`),
and the compound-score bucket labeller (`_sentiment_label`). Uses the REAL
vaderSentiment library (a deterministic local NLP scorer, not a network call)
— headline text below was chosen and verified to land unambiguously on each
side of the label boundary rather than mocked. Previously zero test coverage.
"""
from stock_analyzer import sentiment as sm

# Verified via direct polarity_scores() calls against the real analyzer:
# compound ≈ +0.44 (clearly >= 0.05) and ≈ -0.54 (clearly <= -0.05).
POSITIVE_HEADLINE = "Company beats earnings, stock soars on record profit"
NEGATIVE_HEADLINE = "Company misses guidance, shares plunge amid fraud probe"


# ─── analyze_news — empty / no-title inputs ──────────────────────────────────

def test_analyze_news_empty_list_returns_zero_and_empty():
    avg, results = sm.analyze_news([])
    assert avg == 0.0
    assert results == []


def test_analyze_news_all_titleless_returns_zero_and_empty():
    items = [{"content": {}}, {"title": ""}, {}]
    avg, results = sm.analyze_news(items)
    assert avg == 0.0
    assert results == []


# ─── analyze_news — cap at 10 headlines ──────────────────────────────────────

def test_analyze_news_caps_at_ten_headlines():
    items = [{"title": f"Neutral headline number {i}"} for i in range(15)]
    _, results = sm.analyze_news(items)
    assert len(results) == 10


# ─── analyze_news — title fallback chain ─────────────────────────────────────

def test_analyze_news_title_falls_back_to_content_title():
    items = [{"content": {"title": POSITIVE_HEADLINE}}]
    _, results = sm.analyze_news(items)
    assert len(results) == 1
    assert results[0]["headline"] == POSITIVE_HEADLINE
    assert results[0]["label"] == "Positive"


# ─── analyze_news — URL fallback chain (link -> canonicalUrl -> clickThroughUrl -> "") ──

def test_analyze_news_url_prefers_link():
    items = [{
        "title": "Neutral update",
        "link": "https://link.example/a",
        "content": {
            "canonicalUrl": {"url": "https://canonical.example/a"},
            "clickThroughUrl": {"url": "https://click.example/a"},
        },
    }]
    _, results = sm.analyze_news(items)
    assert results[0]["url"] == "https://link.example/a"


def test_analyze_news_url_falls_back_to_canonical_url_when_no_link():
    items = [{
        "title": "Neutral update",
        "content": {
            "canonicalUrl": {"url": "https://canonical.example/a"},
            "clickThroughUrl": {"url": "https://click.example/a"},
        },
    }]
    _, results = sm.analyze_news(items)
    assert results[0]["url"] == "https://canonical.example/a"


def test_analyze_news_url_falls_back_to_click_through_url_when_no_link_or_canonical():
    items = [{
        "title": "Neutral update",
        "content": {"clickThroughUrl": {"url": "https://click.example/a"}},
    }]
    _, results = sm.analyze_news(items)
    assert results[0]["url"] == "https://click.example/a"


def test_analyze_news_url_defaults_to_empty_string_when_no_fallback_matches():
    items = [{"title": "Neutral update", "content": {}}]
    _, results = sm.analyze_news(items)
    assert results[0]["url"] == ""


# ─── analyze_news — average compound + directional labels (real VADER) ──────

def test_analyze_news_positive_headline_scores_positive_label():
    avg, results = sm.analyze_news([{"title": POSITIVE_HEADLINE}])
    assert avg > 0.05
    assert results[0]["label"] == "Positive"
    assert avg == round(results[0]["score"], 3)


def test_analyze_news_negative_headline_scores_negative_label():
    avg, results = sm.analyze_news([{"title": NEGATIVE_HEADLINE}])
    assert avg < -0.05
    assert results[0]["label"] == "Negative"


def test_analyze_news_average_is_mean_of_compound_scores_rounded():
    items = [{"title": POSITIVE_HEADLINE}, {"title": NEGATIVE_HEADLINE}]
    avg, results = sm.analyze_news(items)
    expected = round((results[0]["score"] + results[1]["score"]) / 2, 3)
    assert avg == expected


# ─── sentiment_score_0_100 — linear remap ────────────────────────────────────

def test_sentiment_score_0_100_at_zero_midpoint():
    assert sm.sentiment_score_0_100(0.0) == 50.0


def test_sentiment_score_0_100_at_negative_one_floor():
    assert sm.sentiment_score_0_100(-1.0) == 0.0


def test_sentiment_score_0_100_at_positive_one_ceiling():
    assert sm.sentiment_score_0_100(1.0) == 100.0


def test_sentiment_score_0_100_rounds_to_one_decimal():
    assert sm.sentiment_score_0_100(0.333) == round((0.333 + 1) / 2 * 100, 1)


# ─── _sentiment_label — boundary values (pure, no VADER needed) ─────────────

def test_sentiment_label_at_positive_boundary_005_is_positive():
    assert sm._sentiment_label(0.05) == "Positive"


def test_sentiment_label_just_below_positive_boundary_is_neutral():
    assert sm._sentiment_label(0.049) == "Neutral"


def test_sentiment_label_at_negative_boundary_neg005_is_negative():
    assert sm._sentiment_label(-0.05) == "Negative"


def test_sentiment_label_just_above_negative_boundary_is_neutral():
    assert sm._sentiment_label(-0.049) == "Neutral"


def test_sentiment_label_exact_zero_is_neutral():
    assert sm._sentiment_label(0.0) == "Neutral"
