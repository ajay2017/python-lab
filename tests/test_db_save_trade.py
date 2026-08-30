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


def test_save_trade_missing_decision_context_column_degrades_and_retries():
    """DDL not applied yet -- decision_context column doesn't exist. Same
    strip-and-retry mechanism as idempotency_key above, but exercised via
    decision_context specifically: the 2026-08-04 audit's optional-column
    list treats it identically to every other optional column, even though
    decision_context (Concept E's passive capture of macro regime, portfolio
    beta, top sector and active-recs count AT TRADE TIME) is materially more
    consequential to lose silently than most of that list -- flagged in the
    2026-08-30 data-integrity audit as a real (if not currently manifesting)
    risk for exactly that reason.

    db.py's detection is a bare substring match: `c in _err_str` for each
    name in `_optional` (db.py ~1765-1769). The simulated error text below
    must actually contain the literal substring "decision_context" -- not
    just resemble a real PostgREST error -- for the strip-and-retry branch
    to trigger at all.
    """
    exc = Exception("Could not find the 'decision_context' column of 'trades' in the schema cache")
    fake = _FakeClient(raise_exc=exc, raise_on_call=1)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    record = {
        "ticker": "AAPL",
        "action": "BUY",
        "decision_context": {
            "macro_regime": "risk_on",
            "portfolio_beta": 1.22,
            "top_sector": "Technology",
            "active_recs_count": 4,
        },
    }
    try:
        ok = db.save_trade(record)
    finally:
        _db_mod._CLIENT = None
    # (1) the trade itself is not lost -- the retry succeeds.
    assert ok is True
    assert len(fake.calls) == 2
    # (2) the retried/actually-persisted payload no longer carries
    # decision_context -- confirming it was the column stripped.
    assert "decision_context" not in fake.calls[1]
    # sanity: the first (failed) attempt DID carry it, so the drop is
    # attributable to the strip-and-retry path and not to the caller
    # never having set it in the first place.
    assert "decision_context" in fake.calls[0]
    # (3) observability check: save_trade's return value is a bare bool.
    # There is no structured return, no logged warning, and no session-
    # state flag distinguishing "saved with decision_context intact" from
    # "saved but decision_context was silently dropped" -- the caller
    # (and therefore the user) has no way to tell the two apart from this
    # call alone. This is CONFIRMED CURRENT BEHAVIOR, not a gap this test
    # closes -- no production code was changed to produce this assertion.
    assert ok is True and not isinstance(ok, tuple) and not isinstance(ok, dict)


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
