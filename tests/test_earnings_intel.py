"""
Tests for stock_analyzer/earnings_intel.py — Earnings Intelligence Phases 1 & 2:
LLM extraction of pre-earnings playbook facts and post-earnings results from raw
article text, a no-LLM Finnhub auto-fetch for recent reported quarters, and the
shared `_parse_json_response` helper. Zero coverage before this batch.
`extract_playbook`/`extract_results` lazily `import anthropic` inside a
try/except — exercised via a fake `sys.modules["anthropic"]` module for the
success/malformed-JSON/exception paths; their guard clauses return before the
import runs and need no mocking. `fetch_recent_results` lazily `import requests`
inside a try/except — the real installed `requests` module is monkeypatched
directly (its local import resolves to the same cached module object).
"""
import sys
import types
from datetime import date, datetime, timedelta

import pytest
import pytz
import requests

from stock_analyzer import earnings_intel as ei


# ─── fake anthropic module helper ────────────────────────────────────────────

class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, response_text=None, raise_exc=None):
        self._response_text = response_text
        self._raise_exc = raise_exc

    def create(self, **kwargs):
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeResponse(self._response_text)


class _FakeClient:
    def __init__(self, response_text=None, raise_exc=None, **kwargs):
        self.messages = _FakeMessages(response_text, raise_exc)


def _install_fake_anthropic(response_text=None, raise_exc=None):
    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = lambda **kwargs: _FakeClient(response_text, raise_exc)
    sys.modules["anthropic"] = fake_mod


@pytest.fixture(autouse=True)
def _cleanup_fake_anthropic():
    yield
    sys.modules.pop("anthropic", None)


# ─── extract_playbook ─────────────────────────────────────────────────────────

def test_extract_playbook_no_api_key_returns_none_and_sets_error():
    result = ei.extract_playbook("some article text", date(2026, 7, 15), api_key="")
    assert result is None
    assert ei.LAST_PLAYBOOK_ERROR == "no API key configured or empty text"


def test_extract_playbook_blank_text_returns_none_guard():
    result = ei.extract_playbook("   ", date(2026, 7, 15), api_key="fake-key")
    assert result is None
    assert ei.LAST_PLAYBOOK_ERROR == "no API key configured or empty text"


def test_extract_playbook_round_trip_stamps_article_date():
    raw = ('{"article_date": "2026-07-15", "records": '
           '[{"ticker": "JPM"}, {"ticker": "GS", "article_date": "2026-07-16"}]}')
    _install_fake_anthropic(raw)
    result = ei.extract_playbook("article text", date(2026, 7, 15), api_key="fake-key")
    assert result[0]["ticker"] == "JPM"
    assert result[0]["article_date"] == "2026-07-15"
    assert result[1]["article_date"] == "2026-07-16"  # already has its own -- not overwritten
    assert ei.LAST_PLAYBOOK_ERROR is None


def test_extract_playbook_malformed_json_returns_none_and_sets_error():
    _install_fake_anthropic("not json at all")
    result = ei.extract_playbook("article text", date(2026, 7, 15), api_key="fake-key")
    assert result is None
    assert ei.LAST_PLAYBOOK_ERROR == "JSON parse failed"


def test_extract_playbook_exception_sets_last_error_and_returns_none():
    _install_fake_anthropic(raise_exc=RuntimeError("boom"))
    result = ei.extract_playbook("article text", date(2026, 7, 15), api_key="fake-key")
    assert result is None
    assert ei.LAST_PLAYBOOK_ERROR.startswith("RuntimeError")


# ─── extract_results ──────────────────────────────────────────────────────────

def test_extract_results_no_api_key_returns_none_and_sets_error():
    result = ei.extract_results("some article text", date(2026, 7, 15), api_key="")
    assert result is None
    assert ei.LAST_RESULTS_ERROR == "no API key configured or empty text"


def test_extract_results_blank_text_returns_none_guard():
    result = ei.extract_results("   ", date(2026, 7, 15), api_key="fake-key")
    assert result is None
    assert ei.LAST_RESULTS_ERROR == "no API key configured or empty text"


def test_extract_results_round_trip_stamps_article_date():
    raw = ('{"article_date": "2026-07-15", "records": '
           '[{"ticker": "JPM"}, {"ticker": "GS", "article_date": "2026-07-16"}]}')
    _install_fake_anthropic(raw)
    result = ei.extract_results("article text", date(2026, 7, 15), api_key="fake-key")
    assert result[0]["ticker"] == "JPM"
    assert result[0]["article_date"] == "2026-07-15"
    assert result[1]["article_date"] == "2026-07-16"
    assert ei.LAST_RESULTS_ERROR is None


def test_extract_results_malformed_json_returns_none_and_sets_error():
    _install_fake_anthropic("not json at all")
    result = ei.extract_results("article text", date(2026, 7, 15), api_key="fake-key")
    assert result is None
    assert ei.LAST_RESULTS_ERROR == "JSON parse failed"


def test_extract_results_exception_sets_last_error_and_returns_none():
    _install_fake_anthropic(raise_exc=ValueError("nope"))
    result = ei.extract_results("article text", date(2026, 7, 15), api_key="fake-key")
    assert result is None
    assert ei.LAST_RESULTS_ERROR.startswith("ValueError")


# ─── fetch_recent_results ─────────────────────────────────────────────────────

def _today_et() -> date:
    return datetime.now(pytz.timezone("America/New_York")).date()


class _FakeFinnhubResp:
    def __init__(self, json_data, status_ok=True, raise_exc=None):
        self._json_data = json_data
        self._status_ok = status_ok
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("bad status")

    def json(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._json_data


def test_fetch_recent_results_no_finnhub_key_returns_empty_no_request(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("requests.get should not be called")
    monkeypatch.setattr(requests, "get", _boom)
    assert ei.fetch_recent_results(["AAPL"], finnhub_key="") == []


def test_fetch_recent_results_no_tickers_returns_empty_no_request(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("requests.get should not be called")
    monkeypatch.setattr(requests, "get", _boom)
    assert ei.fetch_recent_results([], finnhub_key="key") == []


def test_fetch_recent_results_excludes_period_older_than_lookback(monkeypatch):
    old_period = (_today_et() - timedelta(days=91)).strftime("%Y-%m-%d")

    def fake_get(url, params=None, timeout=None):
        return _FakeFinnhubResp([{"period": old_period, "actual": 1.0, "estimate": 0.9}])

    monkeypatch.setattr(requests, "get", fake_get)
    result = ei.fetch_recent_results(["AAPL"], finnhub_key="key", lookback_days=90)
    assert result == []


def test_fetch_recent_results_includes_period_within_lookback(monkeypatch):
    recent_period = (_today_et() - timedelta(days=10)).strftime("%Y-%m-%d")

    def fake_get(url, params=None, timeout=None):
        return _FakeFinnhubResp([{"period": recent_period, "actual": 1.0, "estimate": 0.9}])

    monkeypatch.setattr(requests, "get", fake_get)
    result = ei.fetch_recent_results(["AAPL"], finnhub_key="key", lookback_days=90)
    assert len(result) == 1
    assert result[0]["ticker"] == "AAPL"
    assert result[0]["quarter_period"] == recent_period


def test_fetch_recent_results_actual_none_excluded(monkeypatch):
    recent_period = (_today_et() - timedelta(days=1)).strftime("%Y-%m-%d")

    def fake_get(url, params=None, timeout=None):
        return _FakeFinnhubResp([{"period": recent_period, "actual": None, "estimate": 0.9}])

    monkeypatch.setattr(requests, "get", fake_get)
    result = ei.fetch_recent_results(["AAPL"], finnhub_key="key")
    assert result == []


def test_fetch_recent_results_eps_beat_computed_correctly(monkeypatch):
    recent_period = (_today_et() - timedelta(days=1)).strftime("%Y-%m-%d")

    def fake_get(url, params=None, timeout=None):
        return _FakeFinnhubResp([{"period": recent_period, "actual": 1.10, "estimate": 0.90,
                                   "surprisePercent": 22.2}])

    monkeypatch.setattr(requests, "get", fake_get)
    result = ei.fetch_recent_results(["AAPL"], finnhub_key="key")
    assert result[0]["eps_beat"] is True
    assert result[0]["actual_eps"] == 1.10
    assert result[0]["estimated_eps"] == 0.90
    assert result[0]["eps_surprise_pct"] == 22.2


def test_fetch_recent_results_eps_beat_equal_values_boundary_false(monkeypatch):
    recent_period = (_today_et() - timedelta(days=1)).strftime("%Y-%m-%d")

    def fake_get(url, params=None, timeout=None):
        return _FakeFinnhubResp([{"period": recent_period, "actual": 1.0, "estimate": 1.0}])

    monkeypatch.setattr(requests, "get", fake_get)
    result = ei.fetch_recent_results(["AAPL"], finnhub_key="key")
    assert result[0]["eps_beat"] is False  # strictly > required, equal -> False not None


def test_fetch_recent_results_per_ticker_exception_skips_and_continues(monkeypatch):
    recent_period = (_today_et() - timedelta(days=1)).strftime("%Y-%m-%d")

    def fake_get(url, params=None, timeout=None):
        symbol = params["symbol"]
        if symbol == "BAD":
            raise RuntimeError("network error")
        return _FakeFinnhubResp([{"period": recent_period, "actual": 1.0, "estimate": 0.9}])

    monkeypatch.setattr(requests, "get", fake_get)
    result = ei.fetch_recent_results(["BAD", "GOOD"], finnhub_key="key")
    assert len(result) == 1
    assert result[0]["ticker"] == "GOOD"


def test_fetch_recent_results_non_list_json_skipped_not_crash(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeFinnhubResp({"error": "not found"})

    monkeypatch.setattr(requests, "get", fake_get)
    result = ei.fetch_recent_results(["AAPL"], finnhub_key="key")
    assert result == []


def test_fetch_recent_results_empty_list_json_skipped_not_crash(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeFinnhubResp([])

    monkeypatch.setattr(requests, "get", fake_get)
    result = ei.fetch_recent_results(["AAPL"], finnhub_key="key")
    assert result == []


# ─── _parse_json_response ─────────────────────────────────────────────────────

def test_parse_json_response_markdown_fenced_json_stripped():
    raw = "```json\n" + '{"records": [{"ticker": "AAPL"}]}' + "\n```"
    result = ei._parse_json_response(raw, "records")
    assert result == [{"ticker": "AAPL"}]


def test_parse_json_response_non_brace_opening_sliced_first_to_last_brace():
    raw = 'Here is the data: {"records": [{"ticker": "AAPL"}]} -- done'
    result = ei._parse_json_response(raw, "records")
    assert result == [{"ticker": "AAPL"}]


def test_parse_json_response_list_key_present_filtered_to_dicts_only():
    raw = '{"records": [{"ticker": "AAPL"}, "not a dict", 42]}'
    result = ei._parse_json_response(raw, "records")
    assert result == [{"ticker": "AAPL"}]


def test_parse_json_response_list_key_absent_bare_ticker_wrapped_as_list():
    raw = '{"ticker": "AAPL", "company": "Apple"}'
    result = ei._parse_json_response(raw, "records")
    assert result == [{"ticker": "AAPL", "company": "Apple"}]


def test_parse_json_response_neither_list_key_nor_ticker_returns_empty_list():
    raw = '{"unrelated": "value"}'
    result = ei._parse_json_response(raw, "records")
    assert result == []


def test_parse_json_response_bare_top_level_array_used_directly():
    raw = '[{"ticker": "AAPL"}, {"ticker": "MSFT"}, "junk"]'
    result = ei._parse_json_response(raw, "records")
    assert result == [{"ticker": "AAPL"}, {"ticker": "MSFT"}]


def test_parse_json_response_malformed_json_returns_none():
    result = ei._parse_json_response("not json at all {{{", "records")
    assert result is None
