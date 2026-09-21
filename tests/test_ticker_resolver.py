"""Tests for stock_analyzer/ticker_resolver.py.

Contracts being locked:
  1. looks_like_ticker: real ticker shapes (incl. class-share suffixes) ->
     True; company names, 6+ letter words, empty/whitespace -> False.
  2. resolve_company_name: mocks yfinance.Search (no live network) --
     top-scored EQUITY-type match wins even when a higher-scoring
     non-EQUITY result (future/ETF) is present; no EQUITY match, empty
     quotes, or an exception -> None, never raises; empty/whitespace query
     short-circuits without calling yfinance at all; a quote missing
     longname/shortname falls back to its own symbol as the name.
"""
import pytest

from stock_analyzer.ticker_resolver import looks_like_ticker, resolve_company_name

pytestmark = pytest.mark.fast


# ── looks_like_ticker ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["AAPL", "MSFT", "F", "GM", "aapl", "  TSLA  "])
def test_looks_like_ticker_plain_tickers(raw):
    assert looks_like_ticker(raw) is True


@pytest.mark.parametrize("raw", ["BRK.B", "BRK-A", "brk.b"])
def test_looks_like_ticker_class_share_suffix(raw):
    assert looks_like_ticker(raw) is True


@pytest.mark.parametrize("raw", ["microsoft", "Apple Inc", "coca cola", "GOOGLE"])
def test_looks_like_ticker_company_names_and_long_words(raw):
    # "GOOGLE" is ticker-shaped letters but 6 core letters -- real tickers
    # cap at 5, so it must read as a company name, not a ticker.
    assert looks_like_ticker(raw) is False


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_looks_like_ticker_empty_input(raw):
    assert looks_like_ticker(raw) is False


# ── resolve_company_name ─────────────────────────────────────────────────────

class _FakeSearch:
    """Stand-in for yfinance.Search(query, max_results=N)."""
    def __init__(self, quotes):
        self.quotes = quotes


def test_resolve_company_name_clean_single_match(monkeypatch):
    quotes = [
        {"symbol": "MSFT", "longname": "Microsoft Corporation",
         "quoteType": "EQUITY", "exchange": "NMS", "score": 147839.0},
    ]
    monkeypatch.setattr("yfinance.Search", lambda q, max_results=8: _FakeSearch(quotes))

    result = resolve_company_name("microsoft")

    assert result == {"symbol": "MSFT", "name": "Microsoft Corporation", "score": 147839.0}


def test_resolve_company_name_picks_top_equity_over_higher_scoring_nonequity(monkeypatch):
    quotes = [
        {"symbol": "SMSFT=F", "longname": "Micro Soybean Futures",
         "quoteType": "FUTURE", "exchange": "CBT", "score": 999999.0},
        {"symbol": "MSFT.TO", "longname": "Microsoft Corporation (Toronto)",
         "quoteType": "EQUITY", "exchange": "TOR", "score": 20000.0},
        {"symbol": "MSFT", "longname": "Microsoft Corporation",
         "quoteType": "EQUITY", "exchange": "NMS", "score": 147839.0},
        {"symbol": "MSFT.DE", "quoteType": "ETF", "exchange": "XETRA", "score": 500000.0},
    ]
    monkeypatch.setattr("yfinance.Search", lambda q, max_results=8: _FakeSearch(quotes))

    result = resolve_company_name("microsoft")

    assert result is not None
    assert result["symbol"] == "MSFT"
    assert result["name"] == "Microsoft Corporation"


def test_resolve_company_name_no_equity_results(monkeypatch):
    quotes = [
        {"symbol": "SMSFT=F", "quoteType": "FUTURE", "score": 999999.0},
        {"symbol": "QQQ", "quoteType": "ETF", "score": 500000.0},
    ]
    monkeypatch.setattr("yfinance.Search", lambda q, max_results=8: _FakeSearch(quotes))

    assert resolve_company_name("something") is None


def test_resolve_company_name_empty_quotes_list(monkeypatch):
    monkeypatch.setattr("yfinance.Search", lambda q, max_results=8: _FakeSearch([]))

    assert resolve_company_name("nonexistent company xyz") is None


def test_resolve_company_name_search_raises(monkeypatch):
    def _boom(q, max_results=8):
        raise RuntimeError("network error")

    monkeypatch.setattr("yfinance.Search", _boom)

    assert resolve_company_name("microsoft") is None


@pytest.mark.parametrize("query", ["", "   ", None])
def test_resolve_company_name_empty_query_short_circuits(monkeypatch, query):
    def _should_not_be_called(*a, **k):
        raise AssertionError("yfinance.Search should not be called for an empty query")

    monkeypatch.setattr("yfinance.Search", _should_not_be_called)

    assert resolve_company_name(query) is None


def test_resolve_company_name_missing_name_falls_back_to_symbol(monkeypatch):
    quotes = [
        {"symbol": "XYZ", "quoteType": "EQUITY", "score": 100.0},
    ]
    monkeypatch.setattr("yfinance.Search", lambda q, max_results=8: _FakeSearch(quotes))

    result = resolve_company_name("xyz corp")

    assert result == {"symbol": "XYZ", "name": "XYZ", "score": 100.0}
