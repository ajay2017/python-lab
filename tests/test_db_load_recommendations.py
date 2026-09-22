"""Regression tests for stock_analyzer/db.py::load_recommendations_or_none()
-- 2026-08-06 Opus review finding on the Self Track Record feature.

load_recommendations() cannot distinguish "zero recommendations exist" from
"the query itself failed" -- both its no-creds branch and its except branch
return the same empty DataFrame. That's harmless for its existing consumers
(all degrade to a "nothing to show" state either way), but unsafe for
self_track_record.classify_buys(), which must never treat a failed load as
"zero recs exist" (it would silently misclassify every app-aligned BUY as
self-initiated). load_recommendations_or_none() makes the distinction
explicit: None on ANY failure (no creds or a raised exception), an empty
DataFrame ONLY on a genuine zero-row result.
"""
import pandas as pd

from stock_analyzer import db
import pytest

pytestmark = pytest.mark.fast


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    """Mimics the .select().gte().lte().order().range().execute() chain
    load_recommendations()/load_recommendations_or_none() build (via their
    shared _load_recommendations_all_pages helper)."""

    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows or []
        self._raise = raise_on_execute
        self._range = None

    def select(self, *_a, **_kw):
        return self

    def gte(self, *_a, **_kw):
        return self

    def lte(self, *_a, **_kw):
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


# ── No credentials ──────────────────────────────────────────────────────────

def test_no_creds_load_recommendations_returns_empty(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    out = db.load_recommendations()
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_no_creds_load_recommendations_or_none_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_recommendations_or_none() is None


# ── Query raises (creds present, transient failure) ─────────────────────────

def test_query_failure_load_recommendations_returns_empty_not_none(monkeypatch):
    """The PRE-FIX behavior this function must keep for its ~20 existing
    callers: a failure degrades to empty, never raises, never returns None."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    out = db.load_recommendations()
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_query_failure_load_recommendations_or_none_returns_none(monkeypatch):
    """THE fix: with creds present, a raised exception must surface as None,
    not an empty DataFrame indistinguishable from a genuine zero-row result."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    assert db.load_recommendations_or_none() is None


# ── Genuine zero rows (query succeeds, nothing on file) ─────────────────────

def test_genuine_empty_result_load_recommendations_or_none_returns_empty_df(monkeypatch):
    """A successful query with zero rows is a valid state, distinct from a
    failure -- must return an empty DataFrame, NOT None."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    out = db.load_recommendations_or_none()
    assert out is not None
    assert isinstance(out, pd.DataFrame)
    assert out.empty


# ── Real rows ────────────────────────────────────────────────────────────────

def test_real_rows_both_functions_return_matching_data(monkeypatch):
    rows = [{"ticker": "AAPL", "rec_date": "2026-08-01", "rec_type": "new_pick"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))

    out_plain = db.load_recommendations()
    out_or_none = db.load_recommendations_or_none()

    assert out_or_none is not None
    assert list(out_plain["ticker"]) == ["AAPL"]
    assert list(out_or_none["ticker"]) == ["AAPL"]


# ── Pagination past PostgREST's default row cap ─────────────────────────────

def test_load_recommendations_paginates_past_page_size(monkeypatch):
    """2026-09-22: recommendations confirmed live at 1473 rows, over
    PostgREST's default 1000-row page cap -- the same silent-truncation bug
    class already fixed on model_predictions (confirmed live 2026-09-04).
    A result set bigger than one page must still come back whole via
    `.range()` looping, not silently capped at the first page."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_RECOMMENDATIONS_PAGE_SIZE", 2)
    rows = [{"ticker": f"T{i}", "rec_date": "2026-08-01", "rec_type": "new_pick"} for i in range(5)]
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))

    out = db.load_recommendations()
    assert len(out) == 5
    assert list(out["ticker"]) == [f"T{i}" for i in range(5)]


def test_load_recommendations_or_none_paginates_past_page_size(monkeypatch):
    """Same pagination fix, verified on the _or_none sibling too."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_RECOMMENDATIONS_PAGE_SIZE", 2)
    rows = [{"ticker": f"T{i}", "rec_date": "2026-08-01", "rec_type": "new_pick"} for i in range(5)]
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))

    out = db.load_recommendations_or_none()
    assert out is not None
    assert len(out) == 5
    assert list(out["ticker"]) == [f"T{i}" for i in range(5)]
