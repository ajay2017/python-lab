"""Regression tests for stock_analyzer/db.py::save_score_history_batch() /
load_score_history() -- roadmap B1 (docs/plans/score-history-capture.md §1a).

Modeled on test_db_save_analyst_target_snapshots.py: True iff the upsert
itself actually executed, False for every reason it didn't (readonly, no
rows, no db, or a raised exception).

The single most important test in this file is the anti-coalesce regression
(test_second_upsert_with_null_does_not_inherit_earlier_non_null) -- this
function must NOT copy save_exit_signals_batch's coalesce-on-write pre-read.
A NULL here means "not measurable this run", which IS the information; a
future editor "harmonising" the two would silently resurrect a stale
fabricated-neutral-free reading over an honest NULL.
"""
import pytest

from stock_analyzer import db

pytestmark = pytest.mark.fast


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeUpsertBuilder:
    def __init__(self, store, records):
        self._store = store
        self._records = records

    def execute(self):
        for r in self._records:
            key = (r.get("ticker"), str(r.get("score_date")))
            self._store[key] = dict(r)
        return _FakeExecResult([])


class _FakeUpsertBuilderRaises:
    def execute(self):
        raise RuntimeError("relation \"score_history\" does not exist")


class _FakeSelectBuilder:
    """Minimal chain for load_score_history: .select().gte().limit().execute()."""
    def __init__(self, rows):
        self._rows = rows

    def gte(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return _FakeExecResult(self._rows)


class _FakeTable:
    def __init__(self, store, raise_on_upsert=False):
        self._store = store
        self._raise_on_upsert = raise_on_upsert

    def upsert(self, records, on_conflict=None):
        if self._raise_on_upsert:
            return _FakeUpsertBuilderRaises()
        return _FakeUpsertBuilder(self._store, records)

    def select(self, *_a, **_k):
        return _FakeSelectBuilder(list(self._store.values()))


class _FakeClient:
    def __init__(self, raise_on_upsert=False):
        self.store: dict[tuple, dict] = {}
        self._raise_on_upsert = raise_on_upsert

    def table(self, name):
        assert name == "score_history"
        return _FakeTable(self.store, raise_on_upsert=self._raise_on_upsert)


def _row(ticker="AAPL", score_date="2026-09-13", **overrides):
    row = {
        "ticker": ticker, "score_date": score_date, "composite": 62.0,
        "t_score": 55.0, "bq_score": 70.0, "val_score": 65.0, "s_score": 60.0,
        "bq_available": True, "val_available": True, "price": 210.0,
        "source": "cron",
    }
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
        assert db.save_score_history_batch([_row()]) is True
        assert fake.store[("AAPL", "2026-09-13")]["composite"] == 62.0
    finally:
        _teardown()


def test_upsert_exception_returns_false_not_true():
    fake = _FakeClient(raise_on_upsert=True)
    _install(fake)
    try:
        assert db.save_score_history_batch([_row()]) is False
    finally:
        _teardown()


def test_readonly_reports_false_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_score_history_batch([_row()]) is False
        assert fake.store == {}
    finally:
        _teardown()


def test_empty_batch_reports_false_and_writes_nothing():
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_score_history_batch([]) is False
        assert fake.store == {}
    finally:
        _teardown()


def test_no_db_reports_false_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_score_history_batch([_row()]) is False
        assert fake.store == {}
    finally:
        _teardown()


def test_idempotent_same_day_rerun_upserts_to_one_row_not_two():
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_score_history_batch([_row(composite=62.0)]) is True
        assert db.save_score_history_batch([_row(composite=63.0)]) is True
        assert len(fake.store) == 1
        assert fake.store[("AAPL", "2026-09-13")]["composite"] == 63.0
    finally:
        _teardown()


def test_second_upsert_with_null_does_not_inherit_earlier_non_null():
    """The single most important test in this change: unlike
    save_exit_signals_batch, this function must NOT coalesce. A same-day
    re-run that honestly carries val_score=None (val_available flipped False
    between runs, e.g. a provider outage) must leave the stored value NULL --
    never resurrect the earlier run's non-null 65.0.
    """
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_score_history_batch([_row(val_score=65.0)]) is True
        assert fake.store[("AAPL", "2026-09-13")]["val_score"] == 65.0

        assert db.save_score_history_batch([_row(val_score=None)]) is True
        assert fake.store[("AAPL", "2026-09-13")]["val_score"] is None
    finally:
        _teardown()


def test_load_score_history_returns_empty_frame_when_no_db(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    result = db.load_score_history()
    assert result.empty


def test_load_score_history_returns_empty_frame_on_exception():
    class _RaisingClient:
        def table(self, name):
            raise RuntimeError("boom")

    _install(_RaisingClient())
    try:
        result = db.load_score_history()
        assert result.empty
    finally:
        _teardown()


def test_load_score_history_returns_rows_on_success():
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_score_history_batch([_row()])
        result = db.load_score_history(days_back=365, limit=5000)
        assert len(result) == 1
        assert result.iloc[0]["ticker"] == "AAPL"
    finally:
        _teardown()
