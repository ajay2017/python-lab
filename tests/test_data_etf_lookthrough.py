"""Boundary tests for stock_analyzer/data.py::fetch_etf_lookthrough() — ETF-
support Phase 3a (docs/plans/etf-multi-asset-support.md). Mocks yfinance's
`funds_data` API surface — a DIFFERENT surface than `.info` (used by
fetch_etf_facts_from_info), a separate network call — so this runs pure, no
network. Proves the three-state contract: provider failure -> None; fetched
successfully with no equity exposure (a bond fund) -> a PRESENT, non-None,
empty-contents dict; fetched with real data -> the full shape."""
import pandas as pd
import pytest

from stock_analyzer import data

pytestmark = pytest.mark.fast


class _FakeFundsData:
    def __init__(self, sector_weightings, top_holdings_df):
        self.sector_weightings = sector_weightings
        self.top_holdings = top_holdings_df


class _FakeTicker:
    def __init__(self, funds_data):
        self._funds_data = funds_data

    @property
    def funds_data(self):
        return self._funds_data


class _RaisingFundsDataTicker:
    """Simulates the real failure shape: the network call happens lazily on
    `.funds_data` access, not on `yf.Ticker(t)` construction."""

    @property
    def funds_data(self):
        raise Exception("HTTPError: invalid or delisted ticker")


def _holdings_df(rows):
    """rows: list of (symbol, holding_pct, name) tuples."""
    if not rows:
        return pd.DataFrame(columns=["Name", "Holding Percent"])
    return pd.DataFrame(
        {"Name": [r[2] for r in rows], "Holding Percent": [r[1] for r in rows]},
        index=[r[0] for r in rows],
    )


def test_successful_fetch_with_real_sector_weights(monkeypatch):
    fd = _FakeFundsData(
        {"technology": 0.30, "financial_services": 0.15},
        _holdings_df([("AAPL", 0.07, "Apple Inc."), ("MSFT", 0.06, "Microsoft Corp.")]),
    )
    monkeypatch.setattr(data.yf, "Ticker", lambda t: _FakeTicker(fd))

    out = data.fetch_etf_lookthrough("SPY")

    assert out is not None
    assert out["sector_weightings"] == {"technology": 0.30, "financial_services": 0.15}
    assert out["top_holdings"] == [
        {"ticker": "AAPL", "weight": 0.07},
        {"ticker": "MSFT", "weight": 0.06},
    ]
    assert isinstance(out["fetched_at"], str) and out["fetched_at"]


def test_bond_fund_returns_present_but_empty_not_none(monkeypatch):
    """A fund with no equity sector exposure (e.g. TLT) — confirmed empty in
    the live probe. Must be a PRESENT dict, never confused with a fetch
    failure, and never defaulted to any bucket by a consumer."""
    fd = _FakeFundsData({}, _holdings_df([]))
    monkeypatch.setattr(data.yf, "Ticker", lambda t: _FakeTicker(fd))

    out = data.fetch_etf_lookthrough("TLT")

    assert out is not None
    assert out["sector_weightings"] == {}
    assert out["top_holdings"] == []
    assert isinstance(out["fetched_at"], str) and out["fetched_at"]


def test_ticker_construction_raising_returns_none(monkeypatch):
    def _raise(t):
        raise Exception("HTTPError: invalid ticker")
    monkeypatch.setattr(data.yf, "Ticker", _raise)

    assert data.fetch_etf_lookthrough("INVALIDXYZ") is None


def test_funds_data_property_raising_returns_none(monkeypatch):
    """The realistic failure path: yf.Ticker(t) itself succeeds (it's a thin
    object), but the lazy .funds_data network call raises HTTPError."""
    monkeypatch.setattr(data.yf, "Ticker", lambda t: _RaisingFundsDataTicker())

    assert data.fetch_etf_lookthrough("DEADTICKER") is None


def test_top_holdings_none_handled_gracefully(monkeypatch):
    """funds_data.top_holdings can itself be None -- must not raise, and must
    still return sector_weightings if that part succeeded."""
    fd = _FakeFundsData({"technology": 1.0}, None)
    monkeypatch.setattr(data.yf, "Ticker", lambda t: _FakeTicker(fd))

    out = data.fetch_etf_lookthrough("XLK")

    assert out is not None
    assert out["sector_weightings"] == {"technology": 1.0}
    assert out["top_holdings"] == []
