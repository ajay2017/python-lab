"""Regression tests for stock_analyzer/db.py::load_analyst_target_snapshots()
-- 2026-09-22 data-foundation pass, extending the pagination fix already
shipped (and Opus-reviewed) for model_predictions/recommendations to this
table.

Unlike load_recommendations()/load_exit_signals(), this function has no
has_db() guard and no `_or_none` sibling -- it degrades to an empty
DataFrame on ANY exception (including a raised "no credentials" error from
_client() itself). This suite covers that existing contract plus the new
pagination behaviour; it does not add an offline-sentinel distinction that
was never part of this function's design.
"""
import pandas as pd

from stock_analyzer import db
import pytest

pytestmark = pytest.mark.fast


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    """Mimics the .select().gte().order().range().execute() chain
    load_analyst_target_snapshots() builds."""

    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows or []
        self._raise = raise_on_execute
        self._range = None

    def select(self, *_a, **_kw):
        return self

    def gte(self, *_a, **_kw):
        return self

    def order(self, *_a, **_kw):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        if self._raise:
            raise RuntimeError("simulated transient Supabase failure")
        if self._range is not None:
            start, end = self._range
            return _FakeExecResult(self._rows[start:end + 1])
        return _FakeExecResult(self._rows)


class _FakeClient:
    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows
        self._raise = raise_on_execute

    def table(self, _name):
        return _FakeQueryBuilder(self._rows, self._raise)


# ── Query raises (creds present, transient failure, or missing table) ───────

def test_query_failure_returns_empty_not_raise(monkeypatch):
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    out = db.load_analyst_target_snapshots()
    assert isinstance(out, pd.DataFrame)
    assert out.empty


# ── Genuine zero rows (query succeeds, nothing on file) ─────────────────────

def test_genuine_empty_result_returns_empty_df(monkeypatch):
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    out = db.load_analyst_target_snapshots()
    assert isinstance(out, pd.DataFrame)
    assert out.empty


# ── Real rows ────────────────────────────────────────────────────────────────

def test_real_rows_returned(monkeypatch):
    rows = [{"ticker": "AAPL", "snapshot_date": "2026-08-01", "target_mean": 250.0}]
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))
    out = db.load_analyst_target_snapshots()
    assert list(out["ticker"]) == ["AAPL"]


# ── Pagination past PostgREST's default row cap ─────────────────────────────

def test_paginates_past_page_size(monkeypatch):
    """2026-09-22 data-foundation pass: PostgREST's own server-side default
    row cap on an unpaginated `.select()` (already confirmed live on
    model_predictions and recommendations) applies identically here. A
    result set bigger than one page must still come back whole via
    `.range()` looping, not silently capped at the first page."""
    monkeypatch.setattr(db, "_ANALYST_TARGET_SNAPSHOTS_PAGE_SIZE", 2)
    rows = [{"ticker": f"T{i}", "snapshot_date": "2026-08-01", "target_mean": 250.0} for i in range(5)]
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))

    out = db.load_analyst_target_snapshots()
    assert len(out) == 5
    assert list(out["ticker"]) == [f"T{i}" for i in range(5)]
