"""Offline-sentinel regression tests for the two new db.py readers added for
the data-maintenance framework (docs/plans/data-maintenance-framework.md,
Phase 1): `load_ticker_last_touched` and `load_account_flows_for_dedup_check`.

Both must return None (never []) when the DB is offline or the query
raises -- collapsing a real failure to an empty list is exactly the
offline-sentinel-collapse bug class this repo's audits keep re-finding
(feedback_sentinel_is_present / feedback_none_sentinel_meets_pandas).
"""
from stock_analyzer import db
import pytest

pytestmark = pytest.mark.fast


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_kw):
        return self

    def order(self, *_a, **_kw):
        return self

    def execute(self):
        return _FakeExecResult(self._rows)


class _RaisingQueryBuilder:
    def select(self, *_a, **_kw):
        raise Exception('relation "bundle_cache" does not exist')


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _FakeQueryBuilder(self._rows)


class _RaisingClient:
    def table(self, _name):
        return _RaisingQueryBuilder()


# ── load_ticker_last_touched ──────────────────────────────────────────────────

def test_load_ticker_last_touched_offline_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_ticker_last_touched("bundle_cache", "fetched_at") is None


def test_load_ticker_last_touched_raises_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _RaisingClient())
    assert db.load_ticker_last_touched("bundle_cache", "fetched_at") is None


def test_load_ticker_last_touched_returns_rows_on_success(monkeypatch):
    rows = [{"ticker": "AAPL", "fetched_at": "2026-09-01T00:00:00+00:00"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows))
    out = db.load_ticker_last_touched("bundle_cache", "fetched_at")
    assert out == rows


def test_load_ticker_last_touched_empty_table_returns_empty_list_not_none(monkeypatch):
    """A genuinely empty table (read succeeded, zero rows) is [], distinct
    from a failed read (None)."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient([]))
    out = db.load_ticker_last_touched("bundle_cache", "fetched_at")
    assert out == []


# ── load_account_flows_for_dedup_check ───────────────────────────────────────

def test_load_account_flows_for_dedup_check_offline_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_account_flows_for_dedup_check() is None


def test_load_account_flows_for_dedup_check_raises_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _RaisingClient())
    assert db.load_account_flows_for_dedup_check() is None


def test_load_account_flows_for_dedup_check_returns_rows_with_txn_id(monkeypatch):
    rows = [{
        "id": 1, "flow_date": "2026-09-01", "flow_type": "deposit",
        "amount": 500.0, "note": None, "snaptrade_txn_id": None,
        "created_at": "2026-09-01T00:00:00+00:00",
    }]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows))
    out = db.load_account_flows_for_dedup_check()
    assert out == rows
    assert "snaptrade_txn_id" in out[0]
