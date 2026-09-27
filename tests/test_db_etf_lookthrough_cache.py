"""stock_analyzer/db.py::save_etf_lookthrough_cache / load_etf_lookthrough_cache
— ETF-support Phase 3a (docs/plans/etf-multi-asset-support.md).

Mirrors the save_fundamentals_cache/load_fundamentals_cache pattern: same
has_db() guard, .limit(1) select, swallow-all-exceptions try/except, upsert
on_conflict="ticker". Pre-DDL resilience is the load-bearing invariant here —
a table that doesn't exist yet in Supabase must degrade to None/False, never
raise into the caller (bundle_loader.load_bundle has no exception handling
around these calls, by design, since the DB layer is supposed to absorb it).
"""
from stock_analyzer import db

import pytest

pytestmark = pytest.mark.fast


class _Exec:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    """Shares one dict (`store`, keyed by ticker) across every .table() call
    on the same fake client, so a save followed by a load round-trips."""

    def __init__(self, store):
        self._store = store
        self._filter_ticker = None

    def select(self, _cols):
        return self

    def eq(self, _col, val):
        self._filter_ticker = val
        return self

    def limit(self, _n):
        return self

    def upsert(self, record, on_conflict=None):
        self._store[record["ticker"]] = record
        return self

    def execute(self):
        if self._filter_ticker is not None:
            row = self._store.get(self._filter_ticker)
            return _Exec([row] if row else [])
        return _Exec(None)


class _FakeClient:
    def __init__(self):
        self.store: dict = {}

    def table(self, _name):
        return _FakeTable(self.store)


class _RaisingClient:
    """Simulates the table not existing yet — every .table() call blows up,
    same shape as a real Supabase "relation does not exist" error."""

    def table(self, _name):
        raise Exception('relation "etf_lookthrough_cache" does not exist')


_PAYLOAD = {
    "sector_weightings": {"technology": 0.5, "financial_services": 0.2},
    "top_holdings": [{"ticker": "AAPL", "weight": 0.07}],
    "fetched_at": "2026-09-27T00:00:00+00:00",
}


def test_save_then_load_round_trips(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: fake)

    ok = db.save_etf_lookthrough_cache("SPY", _PAYLOAD)
    assert ok is True

    out = db.load_etf_lookthrough_cache("spy")  # lower-case ticker also resolves
    assert out is not None
    assert out["payload"] == _PAYLOAD
    assert "fetched_at" in out


def test_load_missing_ticker_returns_none(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: fake)

    assert db.load_etf_lookthrough_cache("NEVERWRITTEN") is None


def test_load_db_offline_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_etf_lookthrough_cache("SPY") is None


def test_load_table_missing_returns_none_not_raise(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _RaisingClient())

    assert db.load_etf_lookthrough_cache("SPY") is None


def test_save_db_offline_returns_false(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.save_etf_lookthrough_cache("SPY", _PAYLOAD) is False


def test_save_table_missing_returns_false_not_raise(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _RaisingClient())

    assert db.save_etf_lookthrough_cache("SPY", _PAYLOAD) is False


def test_save_rejects_non_dict_payload(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient())

    assert db.save_etf_lookthrough_cache("SPY", None) is False
    assert db.save_etf_lookthrough_cache("SPY", "not-a-dict") is False


def test_load_blank_ticker_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient())

    assert db.load_etf_lookthrough_cache("") is None
    assert db.load_etf_lookthrough_cache(None) is None
