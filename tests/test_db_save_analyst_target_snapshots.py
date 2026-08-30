"""Regression tests for stock_analyzer/db.py::save_analyst_target_snapshots_batch()
-- return-value contract (2026-08-30 data-integrity audit follow-up).

Before this fix, the function returned None unconditionally, and its sole
cron caller (cron_runner.py's premarket lane) logged "captured" right after
calling it regardless of whether the upsert itself succeeded or silently
failed via warnings.warn(). This mirrors save_exit_signals_batch's own
2026-08-30 fix and save_model_predictions_batch's pre-existing contract:
True iff the upsert actually executed, False for every reason it didn't
(readonly, no snapshots, no db, or a raised exception).
"""
import pytest

from stock_analyzer import db


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeUpsertBuilder:
    def __init__(self, store, records):
        self._store = store
        self._records = records

    def execute(self):
        for r in self._records:
            key = (r.get("ticker"), str(r.get("snapshot_date")))
            self._store[key] = dict(r)
        return _FakeExecResult([])


class _FakeUpsertBuilderRaises:
    def execute(self):
        raise RuntimeError("relation \"analyst_target_snapshots\" does not exist")


class _FakeTable:
    def __init__(self, store, raise_on_upsert=False):
        self._store = store
        self._raise_on_upsert = raise_on_upsert

    def upsert(self, records, on_conflict=None):
        if self._raise_on_upsert:
            return _FakeUpsertBuilderRaises()
        return _FakeUpsertBuilder(self._store, records)


class _FakeClient:
    def __init__(self, raise_on_upsert=False):
        self.store: dict[tuple, dict] = {}
        self._raise_on_upsert = raise_on_upsert

    def table(self, name):
        assert name == "analyst_target_snapshots"
        return _FakeTable(self.store, raise_on_upsert=self._raise_on_upsert)


def _snap(ticker="AAPL", snapshot_date="2026-08-30", **overrides):
    row = {"ticker": ticker, "snapshot_date": snapshot_date,
           "target_mean": 220.0, "num_analysts": 30, "info_source": "yfinance"}
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _patch_db_flags(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    yield


def _install(fake):
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake


def _teardown():
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = None


def test_successful_upsert_returns_true_and_writes_the_row():
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_analyst_target_snapshots_batch([_snap()]) is True
        assert fake.store[("AAPL", "2026-08-30")]["target_mean"] == 220.0
    finally:
        _teardown()


def test_upsert_exception_returns_false_not_true():
    fake = _FakeClient(raise_on_upsert=True)
    _install(fake)
    try:
        assert db.save_analyst_target_snapshots_batch([_snap()]) is False
    finally:
        _teardown()


def test_readonly_reports_false_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_analyst_target_snapshots_batch([_snap()]) is False
        assert fake.store == {}
    finally:
        _teardown()


def test_empty_batch_reports_false_and_writes_nothing():
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_analyst_target_snapshots_batch([]) is False
        assert fake.store == {}
    finally:
        _teardown()


def test_no_db_reports_false_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_analyst_target_snapshots_batch([_snap()]) is False
        assert fake.store == {}
    finally:
        _teardown()
