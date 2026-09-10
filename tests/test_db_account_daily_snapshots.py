"""Tests for the account_daily_snapshots db.py functions -- leverage/margin-
cushion history (cron_runner._run_eod step 1b). Mirrors the offline-sentinel
contract test style already established in test_db_model_predictions.py /
test_db_load_daily_snapshots.py: None on ANY failure (no creds, or a raised
exception including a pre-DDL "relation does not exist"), a genuine empty
DataFrame ONLY on a zero-row result, and save_* never raises."""
import pandas as pd

from stock_analyzer import db
import pytest

pytestmark = pytest.mark.fast


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    def __init__(self, rows=None, raise_on_execute=False, upsert_calls=None):
        self._rows = rows or []
        self._raise = raise_on_execute
        # Shared across every builder a given _FakeClient produces, so
        # repeated .table(...) calls (one per save_*/load_* call) still
        # accumulate into ONE list the test can inspect afterwards.
        self.upsert_calls = upsert_calls if upsert_calls is not None else []

    def select(self, *_a, **_kw):
        return self

    def gte(self, *_a, **_kw):
        return self

    def lte(self, *_a, **_kw):
        return self

    def order(self, *_a, **_kw):
        return self

    def upsert(self, payload, on_conflict=None):
        self.upsert_calls.append((payload, on_conflict))
        return self

    def execute(self):
        if self._raise:
            raise RuntimeError("simulated relation does not exist / transient failure")
        return _FakeExecResult(self._rows)


class _FakeClient:
    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows
        self._raise = raise_on_execute
        self.builder = None
        self.calls = []
        self._upsert_calls: list = []

    def table(self, name):
        self.calls.append(name)
        self.builder = _FakeQueryBuilder(self._rows, self._raise, self._upsert_calls)
        return self.builder


_ROW = {
    "snapshot_date": "2026-09-10",
    "gross_book": 4000.0,
    "cash_balance": None,
    "net_equity": None,
    "leverage": None,
    "cushion": None,
    "call_distance_pct": None,
    "maintenance_rate": 0.25,
    "cash_as_of": None,
}


# ── save_account_daily_snapshot ──────────────────────────────────────────────

def test_save_readonly_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    assert db.save_account_daily_snapshot(_ROW) is False


def test_save_no_creds_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.save_account_daily_snapshot(_ROW) is False


def test_save_empty_row_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    assert db.save_account_daily_snapshot({}) is False


def test_save_row_missing_snapshot_date_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    bad = dict(_ROW)
    del bad["snapshot_date"]
    assert db.save_account_daily_snapshot(bad) is False


def test_save_never_raises_on_pre_ddl_failure(monkeypatch):
    """A missing table (pre-DDL) must degrade to False, never an exception --
    the EOD cron lane must survive a session before the owner applies the
    DDL to production."""
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    assert db.save_account_daily_snapshot(_ROW) is False  # no raise


def test_save_success_returns_true(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[])
    monkeypatch.setattr(db, "_client", lambda: fake)
    assert db.save_account_daily_snapshot(_ROW) is True


def test_save_upserts_on_snapshot_date_conflict_key(monkeypatch):
    """The conflict key must be snapshot_date -- a same-day re-run (e.g. the
    cron's `force` flag) overwrites the one row rather than duplicating it."""
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[])
    monkeypatch.setattr(db, "_client", lambda: fake)
    db.save_account_daily_snapshot(_ROW)
    assert fake.builder.upsert_calls
    payload, on_conflict = fake.builder.upsert_calls[0]
    assert on_conflict == "snapshot_date"
    assert payload["snapshot_date"] == "2026-09-10"


def test_save_called_twice_same_date_both_upsert_same_key(monkeypatch):
    """Two writes for the SAME snapshot_date each go through upsert() with
    the same conflict key -- at the DB layer (no live DB available to
    assert row-count=1), this is the guarantee that idempotency rests on."""
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[])
    monkeypatch.setattr(db, "_client", lambda: fake)
    db.save_account_daily_snapshot(_ROW)
    db.save_account_daily_snapshot(_ROW)
    assert len(fake.builder.upsert_calls) == 2
    assert all(oc == "snapshot_date" for _, oc in fake.builder.upsert_calls)
    assert all(p["snapshot_date"] == "2026-09-10" for p, _ in fake.builder.upsert_calls)


# ── load_account_daily_snapshots ─────────────────────────────────────────────

def test_load_no_creds_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_account_daily_snapshots() is None


def test_load_query_failure_returns_none(monkeypatch):
    """A pre-DDL missing table or a transient error must surface as None,
    never an empty DataFrame indistinguishable from a genuine zero-row
    result -- the chart's 'not enough data yet' state depends on this."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    assert db.load_account_daily_snapshots() is None


def test_load_genuine_empty_result_returns_empty_df(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    out = db.load_account_daily_snapshots()
    assert out is not None
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_load_real_rows(monkeypatch):
    rows = [dict(_ROW)]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))
    out = db.load_account_daily_snapshots()
    assert list(out["snapshot_date"]) == ["2026-09-10"]


def test_load_queries_account_daily_snapshots_table(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[])
    monkeypatch.setattr(db, "_client", lambda: fake)
    db.load_account_daily_snapshots()
    assert fake.calls == ["account_daily_snapshots"]
