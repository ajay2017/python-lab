"""Regression tests for stock_analyzer/db.py::save_trade() — 2026-08-04
audit finding: the `trades` table had no DB-level idempotency backstop
(unlike recommendations/daily_snapshots, which use real unique-constraint
upserts), so a retried/double-submitted interactive save could create a
duplicate row. Fixed with a nullable `idempotency_key` column (a UUID set
once at record-staging time in app.py) + a unique index — a violation on
that specific constraint is treated as an idempotent no-op success, not an
error, and the column gracefully degrades (same pattern as every other
optional trades column) until the DDL is applied.
"""
import pytest

from stock_analyzer import db


class _FakeExecuteResult:
    pass


class _FakeInsertBuilder:
    def __init__(self, table, calls, raise_exc=None, raise_on_call=1):
        self._table = table
        self._calls = calls
        self._raise_exc = raise_exc
        self._raise_on_call = raise_on_call

    def insert(self, record):
        self._calls.append(dict(record))
        return self

    def execute(self):
        call_n = len(self._calls)
        if self._raise_exc is not None and call_n == self._raise_on_call:
            raise self._raise_exc
        return _FakeExecuteResult()


class _FakeClient:
    def __init__(self, raise_exc=None, raise_on_call=1):
        self.calls: list[dict] = []
        self._raise_exc = raise_exc
        self._raise_on_call = raise_on_call

    def table(self, name):
        return _FakeInsertBuilder(name, self.calls, self._raise_exc, self._raise_on_call)


@pytest.fixture(autouse=True)
def _patch_has_db(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    yield


def test_save_trade_normal_insert_succeeds():
    fake = _FakeClient()
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        ok = db.save_trade({"ticker": "AAPL", "action": "BUY", "idempotency_key": "abc-123"})
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    assert len(fake.calls) == 1
    assert fake.calls[0]["idempotency_key"] == "abc-123"


def test_save_trade_idempotency_key_unique_violation_is_treated_as_success():
    """The exact scenario this fix exists for: the same staged record is
    submitted twice (e.g. a double-clicked Confirm). The first insert
    already won; the retry's unique-violation must not surface as a save
    failure to the user."""
    exc = Exception(
        'duplicate key value violates unique constraint "trades_idempotency_key_unique"'
    )
    fake = _FakeClient(raise_exc=exc, raise_on_call=1)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        ok = db.save_trade({"ticker": "AAPL", "action": "BUY", "idempotency_key": "dup-key"})
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    assert len(fake.calls) == 1  # no retry attempted -- treated as success immediately


def test_save_trade_missing_idempotency_key_column_degrades_and_retries():
    """DDL not applied yet -- idempotency_key column doesn't exist. Must
    drop it and retry once, same as every other optional column, rather
    than failing the whole trade save."""
    exc = Exception("Could not find the 'idempotency_key' column of 'trades' in the schema cache")
    fake = _FakeClient(raise_exc=exc, raise_on_call=1)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        ok = db.save_trade({"ticker": "AAPL", "action": "BUY", "idempotency_key": "abc-123"})
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    assert len(fake.calls) == 2
    assert "idempotency_key" not in fake.calls[1]  # dropped on retry


def test_save_trade_unrelated_unique_violation_not_swallowed(monkeypatch):
    """A unique violation on some OTHER constraint must still surface as a
    real failure -- only trades_idempotency_key_unique is treated as a
    benign idempotent retry."""
    exc = Exception('duplicate key value violates unique constraint "some_other_constraint"')
    fake = _FakeClient(raise_exc=exc, raise_on_call=1)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    # save_trade's failure path calls st.error() -- outside a real Streamlit
    # script run (bare pytest) that reaches into UI-rendering internals that
    # aren't the point of this test; stub it out rather than exercise real
    # Streamlit rendering machinery.
    monkeypatch.setattr(_db_mod.st, "error", lambda *a, **k: None)
    try:
        ok = db.save_trade({"ticker": "AAPL", "action": "BUY", "idempotency_key": "abc-123"})
    finally:
        _db_mod._CLIENT = None
    assert ok is False


def test_save_trade_readonly_is_noop():
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = None
    orig_is_readonly = db.is_readonly
    db.is_readonly = lambda: True
    try:
        ok = db.save_trade({"ticker": "AAPL", "action": "BUY"})
    finally:
        db.is_readonly = orig_is_readonly
    assert ok is False
