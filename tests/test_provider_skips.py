"""Certain-to-fail provider calls are stopped before any HTTP (2026-10-27).

Finnhub news-sentiment (FINNHUB_NEWS_SENTIMENT_ENABLED) and the FMP endpoints in
FMP_SKIP_ENDPOINTS (earnings, grades-consensus) returned errors on every call on
the current plans. The skips must make NO HTTP call, spend NO daily FMP quota and
write NO api_health record, while callers keep today's None / {} result.
"""
import pytest

from stock_analyzer import constants as C
from stock_analyzer import news_sentiment as ns
from stock_analyzer.providers import fmp_provider as fp
from stock_analyzer.providers.base import ProviderUnavailable

pytestmark = pytest.mark.fast


def _no_http(*a, **k):
    raise AssertionError("HTTP must not be called")


def test_constants_pin_the_current_plan():
    assert C.FINNHUB_NEWS_SENTIMENT_ENABLED is False
    assert {"earnings", "grades-consensus"} <= set(C.FMP_SKIP_ENDPOINTS)


def test_disabled_sentiment_returns_empty_and_never_builds_provider(monkeypatch):
    monkeypatch.setattr(ns, "FINNHUB_NEWS_SENTIMENT_ENABLED", False)
    monkeypatch.setattr(ns, "FinnhubProvider", _no_http)
    assert ns.fetch_sentiment_for_tickers(["AAPL", "MSFT"]) == {}


def _fmp():
    p = fp.FMPProvider.__new__(fp.FMPProvider)
    p._key = "SECRETKEY123"
    p._info_cache = {}
    return p


@pytest.fixture
def spies(monkeypatch):
    quota, recs = [], []
    monkeypatch.setattr(fp, "http_get_json", _no_http)
    monkeypatch.setattr(fp._db, "increment_daily_quota", lambda *a, **k: quota.append(a))
    monkeypatch.setattr(fp._ah, "record", lambda *a, **k: recs.append((a, k)))
    return quota, recs


def test_next_earnings_skipped_returns_none(spies):
    quota, recs = spies
    assert _fmp()._next_earnings("AAPL") is None
    assert quota == [] and recs == []


def test_fetch_revisions_skipped_returns_empty_dict(spies):
    quota, recs = spies
    assert _fmp()._fetch_revisions("VOO") == {}
    assert quota == [] and recs == []


def test_get_json_skip_raises_provider_unavailable(spies):
    with pytest.raises(ProviderUnavailable) as ei:
        _fmp()._get_json("earnings", {"symbol": "AAPL"})
    assert "SECRETKEY123" not in str(ei.value)


def test_non_skipped_path_still_calls_http(monkeypatch):
    calls, quota = [], []

    def _fake(url, params=None, **k):
        calls.append(url)
        return [{"symbol": "AAPL"}]

    monkeypatch.setattr(fp, "http_get_json", _fake)
    monkeypatch.setattr(fp._db, "increment_daily_quota", lambda *a, **k: quota.append(a))
    assert _fmp()._get_json("profile", {"symbol": "AAPL"}) == [{"symbol": "AAPL"}]
    assert len(calls) == 1 and calls[0].endswith("/profile")
    assert len(quota) == 1
