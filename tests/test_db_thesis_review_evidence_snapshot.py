"""Regression tests for stock_analyzer/db.py Chunk B additions (🧵 Thesis
evidence-snapshot capture):

  - save_thesis_review() gains the same graceful drop-and-retry degradation
    every other optional column in this file already has (previously a bare
    `.insert(record).execute()` with ZERO backward-compatibility handling --
    a genuine gap, since `thesis_reviews` had grown zero optional columns
    until evidence_snapshot).
  - load_thesis_reviews_or_none() -- a new additive sibling to
    load_thesis_reviews(), mirroring load_analyst_coverage_or_none()'s
    exact three-state contract (None on offline/failure, empty DataFrame
    on a genuine zero-row result, populated DataFrame otherwise).

Mirrors tests/test_db_save_trade.py and tests/test_db_load_analyst_coverage.py's
patterns exactly.
"""
import pandas as pd
import pytest

from stock_analyzer import db

pytestmark = pytest.mark.fast


# ── save_thesis_review() drop-and-retry ─────────────────────────────────────

class _FakeExecuteResult:
    def __init__(self, data=None):
        self.data = data


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


def test_save_thesis_review_normal_insert_succeeds():
    fake = _FakeClient()
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        ok = db.save_thesis_review({
            "ticker": "AAPL", "status": "INTACT", "summary": "ok",
            "evidence_snapshot": {"schema_v": 1},
        })
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    assert len(fake.calls) == 1
    assert fake.calls[0]["evidence_snapshot"] == {"schema_v": 1}


def test_save_thesis_review_missing_evidence_snapshot_column_degrades_and_retries():
    """DDL not applied yet -- evidence_snapshot column doesn't exist. Must
    drop it and retry once (mirrors save_trade()'s single-generation
    drop-and-retry), not fail the whole review save."""
    exc = Exception(
        "Could not find the 'evidence_snapshot' column of 'thesis_reviews' in the schema cache"
    )
    fake = _FakeClient(raise_exc=exc, raise_on_call=1)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    record = {"ticker": "AAPL", "status": "INTACT", "summary": "ok",
              "evidence_snapshot": {"schema_v": 1}}
    try:
        ok = db.save_thesis_review(record)
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    assert len(fake.calls) == 2
    assert "evidence_snapshot" in fake.calls[0]       # first attempt carried it
    assert "evidence_snapshot" not in fake.calls[1]   # dropped on retry


def test_save_thesis_review_generic_pgrst204_wording_also_triggers_retry():
    """A PGRST204 error that doesn't literally repeat the column name in a
    substring db.py checks for must still be recognized via the generic
    missing-column wording ("could not find the" / pgrst204), same two
    signals save_recommendations()'s _col_missing() checks."""
    exc = Exception("PGRST204: schema cache miss")
    fake = _FakeClient(raise_exc=exc, raise_on_call=1)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    record = {"ticker": "MSFT", "status": "WEAKENING", "summary": "x",
              "evidence_snapshot": {"schema_v": 1}}
    try:
        ok = db.save_thesis_review(record)
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    assert len(fake.calls) == 2
    assert "evidence_snapshot" not in fake.calls[1]


def test_save_thesis_review_unrelated_error_not_retried():
    """An error that names no optional column and matches none of the
    generic missing-column patterns must fail cleanly, exactly as before --
    no retry attempted."""
    exc = Exception("connection timeout")
    fake = _FakeClient(raise_exc=exc, raise_on_call=1)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        ok = db.save_thesis_review({
            "ticker": "AAPL", "status": "INTACT", "summary": "ok",
            "evidence_snapshot": {"schema_v": 1},
        })
    finally:
        _db_mod._CLIENT = None
    assert ok is False
    assert len(fake.calls) == 1  # no retry attempted


def test_save_thesis_review_record_without_evidence_snapshot_key_not_retried_on_unrelated_error():
    """The retry guard requires the optional column to actually be present
    in the record -- a record that never carried evidence_snapshot at all
    (e.g. the earnings-checkpoint site can still pass it as None explicitly,
    but a hypothetical caller that omits it outright) gets no benefit from
    stripping something that isn't there, so an unrelated failure must not
    spuriously retry."""
    exc = Exception("relation \"thesis_reviews\" does not exist")
    fake = _FakeClient(raise_exc=exc, raise_on_call=1)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        ok = db.save_thesis_review({"ticker": "AAPL", "status": "INTACT", "summary": "ok"})
    finally:
        _db_mod._CLIENT = None
    # "does not exist" matches the generic pattern, but the record has no
    # evidence_snapshot key to strip -- the guard's `any(c in record ...)`
    # correctly declines to retry a no-op strip.
    assert ok is False
    assert len(fake.calls) == 1


def test_save_thesis_review_readonly_is_noop():
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = None
    orig_is_readonly = db.is_readonly
    db.is_readonly = lambda: True
    try:
        ok = db.save_thesis_review({"ticker": "AAPL", "status": "INTACT", "summary": "ok"})
    finally:
        db.is_readonly = orig_is_readonly
    assert ok is False


# ── load_thesis_reviews_or_none() ───────────────────────────────────────────

class _FakeQueryBuilder:
    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows or []
        self._raise = raise_on_execute
        self._eq_calls: list = []
        self.order_calls: list = []

    def select(self, *_a, **_kw):
        return self

    def eq(self, col, val):
        self._eq_calls.append((col, val))
        return self

    def order(self, *_a, **_kw):
        self.order_calls.append((_a, _kw))
        return self

    def execute(self):
        if self._raise:
            raise RuntimeError("simulated transient Supabase failure")
        return _FakeExecuteResult(self._rows)


class _FakeQueryClient:
    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows
        self._raise = raise_on_execute
        self.builders: list = []

    def table(self, _name):
        b = _FakeQueryBuilder(self._rows, self._raise)
        self.builders.append(b)
        return b


def test_no_creds_load_thesis_reviews_or_none_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_thesis_reviews_or_none() is None


def test_query_failure_load_thesis_reviews_or_none_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeQueryClient(raise_on_execute=True))
    assert db.load_thesis_reviews_or_none() is None


def test_query_failure_load_thesis_reviews_still_returns_empty_df(monkeypatch):
    """The PRE-EXISTING function must keep its own behavior unchanged --
    still degrades to an empty DataFrame on failure, never None, never
    raises. This build does not modify load_thesis_reviews() itself."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeQueryClient(raise_on_execute=True))
    out = db.load_thesis_reviews()
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_genuine_empty_result_returns_empty_df_not_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeQueryClient(rows=[]))
    out = db.load_thesis_reviews_or_none()
    assert out is not None
    assert isinstance(out, pd.DataFrame)
    assert out.empty
    assert list(out.columns) == db._THESIS_REVIEW_COLS


def test_real_rows_returned_and_backfills_missing_columns(monkeypatch):
    rows = [{"ticker": "AAPL", "status": "INTACT", "reviewed_at": "2026-09-28T10:00:00"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeQueryClient(rows=rows))
    out = db.load_thesis_reviews_or_none()
    assert out is not None
    assert list(out["ticker"]) == ["AAPL"]
    # evidence_snapshot column backfilled to None when absent from the raw row
    assert "evidence_snapshot" in out.columns
    assert out.iloc[0]["evidence_snapshot"] is None


def test_ticker_filter_narrows_server_side(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeQueryClient(rows=[{"ticker": "AAPL", "status": "INTACT"}])
    monkeypatch.setattr(db, "_client", lambda: fake)
    db.load_thesis_reviews_or_none(ticker="aapl")
    assert fake.builders[0]._eq_calls == [("ticker", "AAPL")]


def test_no_ticker_filter_when_omitted(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeQueryClient(rows=[])
    monkeypatch.setattr(db, "_client", lambda: fake)
    db.load_thesis_reviews_or_none()
    assert fake.builders[0]._eq_calls == []


def test_ordered_by_reviewed_at_desc(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeQueryClient(rows=[{"ticker": "AAPL"}])
    monkeypatch.setattr(db, "_client", lambda: fake)
    db.load_thesis_reviews_or_none()
    assert fake.builders[0].order_calls == [(("reviewed_at",), {"desc": True})]


# ── _THESIS_REVIEW_COLS backfill generically covers the new column ─────────

def test_load_thesis_reviews_backfills_evidence_snapshot_when_absent(monkeypatch):
    """load_thesis_reviews()'s existing generic backfill loop (fills any
    _THESIS_REVIEW_COLS member missing from the raw row with None) already
    covers evidence_snapshot without any code change -- confirms that still
    holds after the column was added to the list."""
    rows = [{"ticker": "AAPL", "status": "INTACT"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeQueryClient(rows=rows))
    out = db.load_thesis_reviews()
    assert "evidence_snapshot" in out.columns
    assert out.iloc[0]["evidence_snapshot"] is None
