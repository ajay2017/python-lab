"""Tests for stock_analyzer/news_sentiment.py — the Finnhub sentiment fetch
wrapper (`fetch_sentiment_for_tickers`), the bullish_pct display bucket
(`sentiment_label`), and the held-position shift alert (`is_sentiment_shift`).
`FinnhubProvider` is mocked (a real I/O boundary); the two pure functions
need no mocking. Constants (from stock_analyzer/constants.py):
NEWS_SENTIMENT_BULLISH_THRESHOLD=0.60, NEWS_SENTIMENT_BEARISH_THRESHOLD=0.40,
NEWS_SENTIMENT_SHIFT_ALERT_BULLISH=0.40, NEWS_SENTIMENT_SHIFT_BUZZ_MIN=1.0.
Previously zero test coverage.
"""
from stock_analyzer import news_sentiment as ns


# ─── fetch_sentiment_for_tickers — provider not configured ──────────────────

class _FakeProvider:
    def __init__(self, configured=True, per_ticker=None, raise_on=None):
        self._configured = configured
        self._per_ticker = per_ticker or {}
        self._raise_on = raise_on or set()

    def is_configured(self):
        return self._configured

    def fetch_news_sentiment(self, ticker):
        if ticker in self._raise_on:
            raise RuntimeError("boom")
        return self._per_ticker.get(ticker)


def test_fetch_sentiment_for_tickers_not_configured_returns_empty(monkeypatch):
    monkeypatch.setattr(ns, "FinnhubProvider", lambda: _FakeProvider(configured=False))
    result = ns.fetch_sentiment_for_tickers(["AAPL"])
    assert result == {}


def test_fetch_sentiment_for_tickers_empty_ticker_list_returns_empty(monkeypatch):
    monkeypatch.setattr(ns, "FinnhubProvider", lambda: _FakeProvider(configured=True))
    result = ns.fetch_sentiment_for_tickers([])
    assert result == {}


def test_fetch_sentiment_for_tickers_per_ticker_exception_is_skipped(monkeypatch):
    provider = _FakeProvider(
        configured=True,
        per_ticker={"GOOD": {"bullish_pct": 0.7}},
        raise_on={"BAD"},
    )
    monkeypatch.setattr(ns, "FinnhubProvider", lambda: provider)
    result = ns.fetch_sentiment_for_tickers(["BAD", "GOOD"])
    assert "BAD" not in result
    assert result["GOOD"] == {"bullish_pct": 0.7}


def test_fetch_sentiment_for_tickers_ticker_returning_none_is_omitted(monkeypatch):
    provider = _FakeProvider(configured=True, per_ticker={"NONE_TICKER": None})
    monkeypatch.setattr(ns, "FinnhubProvider", lambda: provider)
    result = ns.fetch_sentiment_for_tickers(["NONE_TICKER"])
    assert result == {}


def test_fetch_sentiment_for_tickers_successful_ticker_is_included(monkeypatch):
    provider = _FakeProvider(configured=True, per_ticker={"AAPL": {"bullish_pct": 0.65}})
    monkeypatch.setattr(ns, "FinnhubProvider", lambda: provider)
    result = ns.fetch_sentiment_for_tickers(["AAPL"])
    assert result == {"AAPL": {"bullish_pct": 0.65}}


# ─── sentiment_label — 3-bucket boundary ─────────────────────────────────────

def test_sentiment_label_at_bullish_boundary_060_is_bullish():
    assert ns.sentiment_label(0.60) == ("Bullish", "🟢")


def test_sentiment_label_just_below_bullish_boundary_is_neutral():
    assert ns.sentiment_label(0.599) == ("Neutral", "🟡")


def test_sentiment_label_at_bearish_boundary_040_is_neutral():
    # bearish check is strict `<`, so exactly 0.40 falls to neutral.
    assert ns.sentiment_label(0.40) == ("Neutral", "🟡")


def test_sentiment_label_just_below_bearish_boundary_is_bearish():
    assert ns.sentiment_label(0.399) == ("Bearish", "🔴")


def test_sentiment_label_midpoint_is_neutral():
    assert ns.sentiment_label(0.50) == ("Neutral", "🟡")


# ─── is_sentiment_shift — both fields required + AND of two strict conditions ──

def test_is_sentiment_shift_bullish_pct_missing_is_false():
    assert ns.is_sentiment_shift({"buzz_score": 2.0}) is False


def test_is_sentiment_shift_buzz_score_missing_is_false():
    assert ns.is_sentiment_shift({"bullish_pct": 0.2}) is False


def test_is_sentiment_shift_both_conditions_hold_is_true():
    assert ns.is_sentiment_shift({"bullish_pct": 0.2, "buzz_score": 2.0}) is True


def test_is_sentiment_shift_only_bullish_condition_holds_is_false():
    # bullish_pct is below the alert threshold, but buzz is not above minimum.
    assert ns.is_sentiment_shift({"bullish_pct": 0.2, "buzz_score": 0.5}) is False


def test_is_sentiment_shift_only_buzz_condition_holds_is_false():
    # buzz is above the minimum, but bullish_pct is not below the alert threshold.
    assert ns.is_sentiment_shift({"bullish_pct": 0.8, "buzz_score": 2.0}) is False


def test_is_sentiment_shift_bullish_exactly_at_boundary_is_false():
    # bullish_pct < 0.40 is strict -- exactly 0.40 does not qualify.
    assert ns.is_sentiment_shift({"bullish_pct": 0.40, "buzz_score": 2.0}) is False


def test_is_sentiment_shift_buzz_exactly_at_boundary_is_false():
    # buzz_score > 1.0 is strict -- exactly 1.0 does not qualify.
    assert ns.is_sentiment_shift({"bullish_pct": 0.2, "buzz_score": 1.0}) is False


def test_is_sentiment_shift_just_past_both_boundaries_is_true():
    assert ns.is_sentiment_shift({"bullish_pct": 0.399, "buzz_score": 1.001}) is True
