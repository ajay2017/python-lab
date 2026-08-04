"""Tests for stock_analyzer/sentiment_velocity.py — newest-half vs oldest-half
sentiment-velocity split (`compute_velocity`), the timestamp/title parsing
helpers it depends on (`_parse_ts`, `_title`, `_score`), and the per-holding
dashboard builder (`build_sentiment_dashboard`). Uses the REAL vaderSentiment
library (deterministic local NLP, not a network call). Constants used:
`_VELOCITY_THRESHOLD=0.10`, `_DIVERGENCE_PRICE_PCT=3.0`, `_MIN_ARTICLES=4`.

Headline sets below were verified via direct polarity_scores() calls to
average ≈ +0.33 (POS_HEADLINES) and ≈ -0.52 (NEG_HEADLINES) — comfortably
past the 0.10 velocity threshold either direction, so bucket/divergence tests
don't need to chase an exact boundary value. Previously zero test coverage.
"""
import pandas as pd

from stock_analyzer import sentiment_velocity as sv


# ─── builders ───────────────────────────────────────────────────────────────

POS_HEADLINES = [
    "Company beats earnings, stock soars on record profit",
    "Analysts upgrade stock to buy after blowout quarter",
    "Company announces breakthrough product, shares rally hard",
    "Record revenue growth delights investors, stock jumps",
]
NEG_HEADLINES = [
    "Company misses guidance, shares plunge amid fraud probe",
    "Regulators launch investigation into accounting scandal",
    "Company slashes forecast, stock craters on weak demand",
    "Executives resign amid scandal, shares tumble",
]
NEUTRAL_HEADLINE = "Company reports quarterly results in line with expectations"


def _mk_items(titles, base_ts=2000, step=1):
    """Build news_raw items with ascending providerPublishTime per index."""
    return [{"title": t, "providerPublishTime": base_ts + i * step} for i, t in enumerate(titles)]


def _mk_articles(newest_titles, oldest_titles):
    """8-article news_raw where `newest_titles` land in the newer half
    (higher providerPublishTime) and `oldest_titles` in the older half."""
    return _mk_items(newest_titles, base_ts=2000) + _mk_items(oldest_titles, base_ts=1000)


# ─── _title / _parse_ts / _score — parsing helpers ───────────────────────────

def test_title_prefers_top_level_title():
    assert sv._title({"title": "A", "content": {"title": "B"}}) == "A"


def test_title_falls_back_to_content_title():
    assert sv._title({"content": {"title": "B"}}) == "B"


def test_title_defaults_to_empty_string():
    assert sv._title({}) == ""


def test_parse_ts_prefers_provider_publish_time():
    assert sv._parse_ts({"providerPublishTime": 12345}) == 12345


def test_parse_ts_falls_back_to_iso_pubdate_when_no_provider_time():
    ts = sv._parse_ts({"content": {"pubDate": "2024-01-01T00:00:00Z"}})
    assert ts == 1704067200


def test_parse_ts_unparseable_pubdate_returns_zero():
    assert sv._parse_ts({"content": {"pubDate": "not-a-date"}}) == 0


def test_parse_ts_no_timestamp_at_all_returns_zero():
    assert sv._parse_ts({}) == 0


def test_score_empty_titles_returns_none():
    assert sv._score([]) is None


def test_score_averages_compound_across_titles():
    score = sv._score(POS_HEADLINES)
    assert score is not None
    assert score > 0


# ─── compute_velocity — below _MIN_ARTICLES: single-window branch ──────────

def test_compute_velocity_zero_articles_is_no_news():
    result = sv.compute_velocity("TST", [])
    assert result["direction"] == "No news"
    assert result["recent_count"] == 0
    assert result["prior_count"] == 0
    assert result["prior_score"] is None
    assert result["velocity"] is None


def test_compute_velocity_three_articles_is_single_window_only():
    items = _mk_items(POS_HEADLINES[:3])
    result = sv.compute_velocity("TST", items)
    assert result["direction"] == "Single window only"
    assert result["recent_count"] == 3
    assert result["prior_count"] == 0
    assert result["velocity"] is None
    assert result["recent_score"] is not None


def test_compute_velocity_items_with_missing_title_or_timestamp_are_dropped():
    items = [
        {"title": "", "providerPublishTime": 1000},          # no title
        {"title": "Valid headline", "providerPublishTime": 0},  # no timestamp
        {"title": "Valid headline two", "providerPublishTime": 1000},
    ]
    result = sv.compute_velocity("TST", items)
    assert result["recent_count"] == 1


# ─── compute_velocity — >= _MIN_ARTICLES: direction buckets ────────────────

def test_compute_velocity_four_articles_hits_two_window_branch():
    items = _mk_articles(POS_HEADLINES[:2], NEG_HEADLINES[:2])
    result = sv.compute_velocity("TST", items)
    assert result["recent_count"] == 2
    assert result["prior_count"] == 2
    assert result["velocity"] is not None


def test_compute_velocity_strong_positive_shift_is_improving():
    items = _mk_articles(POS_HEADLINES, NEG_HEADLINES)
    result = sv.compute_velocity("TST", items)
    assert result["velocity"] > sv._VELOCITY_THRESHOLD
    assert result["direction"] == "Improving ↑"
    assert result["signal"] == "📈 Sentiment improving"


def test_compute_velocity_strong_negative_shift_is_deteriorating():
    items = _mk_articles(NEG_HEADLINES, POS_HEADLINES)
    result = sv.compute_velocity("TST", items)
    assert result["velocity"] < -sv._VELOCITY_THRESHOLD
    assert result["direction"] == "Deteriorating ↓"
    assert result["signal"] == "🔴 Sentiment deteriorating"


def test_compute_velocity_near_identical_halves_is_stable():
    items = _mk_articles([NEUTRAL_HEADLINE] * 4, [NEUTRAL_HEADLINE] * 4)
    result = sv.compute_velocity("TST", items)
    assert -sv._VELOCITY_THRESHOLD <= result["velocity"] <= sv._VELOCITY_THRESHOLD
    assert result["direction"] == "Stable →"
    assert result["signal"] == "Stable"


# ─── compute_velocity — price/sentiment divergence ───────────────────────────

def test_compute_velocity_bearish_divergence_price_up_sentiment_down():
    items = _mk_articles(NEG_HEADLINES, POS_HEADLINES)  # deteriorating
    result = sv.compute_velocity("TST", items, price_ret_7d=5.0)
    assert result["divergence"] is True
    assert result["divergence_type"] == "BEARISH"
    assert "Divergence" in result["signal"]


def test_compute_velocity_bullish_divergence_price_down_sentiment_up():
    items = _mk_articles(POS_HEADLINES, NEG_HEADLINES)  # improving
    result = sv.compute_velocity("TST", items, price_ret_7d=-5.0)
    assert result["divergence"] is True
    assert result["divergence_type"] == "BULLISH"
    assert "Divergence" in result["signal"]


def test_compute_velocity_price_condition_alone_does_not_flag_divergence():
    # Price move clears the BEARISH price threshold, but sentiment is
    # IMPROVING (not deteriorating) -- only one half of the AND holds.
    items = _mk_articles(POS_HEADLINES, NEG_HEADLINES)
    result = sv.compute_velocity("TST", items, price_ret_7d=5.0)
    assert result["divergence"] is False
    assert result["divergence_type"] is None


def test_compute_velocity_sentiment_condition_alone_does_not_flag_divergence():
    # Sentiment is deteriorating past the threshold, but price move is too
    # small to clear the BEARISH price threshold -- only one half holds.
    items = _mk_articles(NEG_HEADLINES, POS_HEADLINES)
    result = sv.compute_velocity("TST", items, price_ret_7d=1.0)
    assert result["divergence"] is False
    assert result["divergence_type"] is None


def test_compute_velocity_price_exactly_at_bearish_boundary_does_not_flag():
    # price_ret_7d > 3.0 is strict -- exactly 3.0 must not qualify.
    items = _mk_articles(NEG_HEADLINES, POS_HEADLINES)
    result = sv.compute_velocity("TST", items, price_ret_7d=3.0)
    assert result["divergence"] is False


def test_compute_velocity_price_exactly_at_bullish_boundary_does_not_flag():
    # price_ret_7d < -3.0 is strict -- exactly -3.0 must not qualify.
    items = _mk_articles(POS_HEADLINES, NEG_HEADLINES)
    result = sv.compute_velocity("TST", items, price_ret_7d=-3.0)
    assert result["divergence"] is False


def test_compute_velocity_headline_sample_capped_at_five():
    items = _mk_articles(POS_HEADLINES, NEG_HEADLINES)
    result = sv.compute_velocity("TST", items)
    assert len(result["headline_sample"]) <= 5
    for entry in result["headline_sample"]:
        assert "title" in entry and "score" in entry


# ─── build_sentiment_dashboard ────────────────────────────────────────────────

def _price_df(closes):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=idx)


def test_build_sentiment_dashboard_computes_price_ret_7d_with_enough_closes():
    port_df = pd.DataFrame({"Ticker": ["AAA"]})
    held_data = {
        "AAA": {
            "news_raw": _mk_articles(POS_HEADLINES, NEG_HEADLINES),
            "df": _price_df([100.0 + i for i in range(10)]),  # 10 closes >= 8
        }
    }
    results = sv.build_sentiment_dashboard(port_df, held_data)
    assert len(results) == 1
    assert results[0]["price_ret_7d"] is not None


def test_build_sentiment_dashboard_guards_price_ret_7d_when_fewer_than_eight_closes():
    port_df = pd.DataFrame({"Ticker": ["AAA"]})
    held_data = {
        "AAA": {
            "news_raw": _mk_articles(POS_HEADLINES, NEG_HEADLINES),
            "df": _price_df([100.0, 101.0, 102.0]),  # only 3 closes
        }
    }
    results = sv.build_sentiment_dashboard(port_df, held_data)
    assert results[0]["price_ret_7d"] is None


def test_build_sentiment_dashboard_missing_held_data_defaults_gracefully():
    port_df = pd.DataFrame({"Ticker": ["ZZZ"]})
    results = sv.build_sentiment_dashboard(port_df, {})
    assert len(results) == 1
    assert results[0]["ticker"] == "ZZZ"
    assert results[0]["direction"] == "No news"


def test_build_sentiment_dashboard_sorts_divergence_first_then_by_abs_velocity():
    port_df = pd.DataFrame({"Ticker": ["DIVERGE", "PLAIN_BIG", "PLAIN_SMALL"]})
    held_data = {
        # Bearish divergence: price up, sentiment sharply down.
        "DIVERGE": {
            "news_raw": _mk_articles(NEG_HEADLINES, POS_HEADLINES),
            "df": _price_df([100.0 + i * 2 for i in range(10)]),
        },
        # No divergence, but a large |velocity| (improving).
        "PLAIN_BIG": {
            "news_raw": _mk_articles(POS_HEADLINES, NEG_HEADLINES),
            "df": _price_df([100.0] * 10),
        },
        # No divergence, stable (near-zero velocity).
        "PLAIN_SMALL": {
            "news_raw": _mk_articles([NEUTRAL_HEADLINE] * 4, [NEUTRAL_HEADLINE] * 4),
            "df": _price_df([100.0] * 10),
        },
    }
    results = sv.build_sentiment_dashboard(port_df, held_data)
    tickers_in_order = [r["ticker"] for r in results]
    assert tickers_in_order == ["DIVERGE", "PLAIN_BIG", "PLAIN_SMALL"]
