"""Regression tests for stock_analyzer/db.py::load_analyst_coverage_or_none().

load_analyst_coverage() cannot distinguish "zero coverage rows exist" from
"the query itself failed" -- both its no-creds branch and its except branch
return the same empty DataFrame. That's harmless for its existing consumers
(Ideas Inbox, Research Scorecard, My Edge, Predictive Analytics, the
analyst-vs-engine calibration matrix -- all of which already degrade
gracefully to "no coverage yet"), but unsafe for a consumer that must never
treat a failed load as "zero coverage rows exist".
load_analyst_coverage_or_none() makes the distinction explicit: None on ANY
failure (no creds or a raised exception), an empty DataFrame ONLY on a
genuine zero-row result. Mirrors test_db_load_recommendations.py's pattern
exactly.
"""
import pandas as pd

from stock_analyzer import db
import pytest

pytestmark = pytest.mark.fast


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    """Mimics the .select().eq().gte().order().limit().execute() chain
    load_analyst_coverage()/load_analyst_coverage_or_none() build for a real
    int `limit`, and the .select().eq().gte().order().range().execute()
    chain the same functions build (via their shared
    _load_analyst_coverage_all_pages helper) for `limit=None`."""

    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows or []
        self._raise = raise_on_execute
        self._limit = None
        self._range = None
        self.order_calls: list = []

    def select(self, *_a, **_kw):
        return self

    def eq(self, *_a, **_kw):
        return self

    def gte(self, *_a, **_kw):
        return self

    def order(self, *_a, **_kw):
        self.order_calls.append((_a, _kw))
        return self

    def limit(self, n, *_a, **_kw):
        self._limit = n
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
        if self._limit is not None:
            return _FakeExecResult(self._rows[:self._limit])
        return _FakeExecResult(self._rows)


class _FakeClient:
    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows
        self._raise = raise_on_execute
        # Records every builder .table() hands out, so a test can assert on
        # the LAST one's ._limit/._range -- load_analyst_coverage's int-limit
        # path calls .table() once per invocation, but the `limit=None`
        # pagination path calls it once PER PAGE (a fresh builder each time,
        # per _load_recommendations_all_pages's own reviewed pattern).
        self.builders: list = []

    def table(self, _name):
        b = _FakeQueryBuilder(self._rows, self._raise)
        self.builders.append(b)
        return b


# ── No credentials ──────────────────────────────────────────────────────────

def test_no_creds_load_analyst_coverage_returns_empty(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    out = db.load_analyst_coverage()
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_no_creds_load_analyst_coverage_or_none_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_analyst_coverage_or_none() is None


# ── Query raises (creds present, transient failure) ─────────────────────────

def test_query_failure_load_analyst_coverage_returns_empty_not_none(monkeypatch):
    """The PRE-EXISTING behavior load_analyst_coverage() must keep for its
    existing callers: a failure degrades to empty, never raises, never
    returns None. This function is left untouched by this build."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    out = db.load_analyst_coverage()
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_query_failure_load_analyst_coverage_or_none_returns_none(monkeypatch):
    """THE new sibling's behavior: with creds present, a raised exception
    must surface as None, not an empty DataFrame indistinguishable from a
    genuine zero-row result."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    assert db.load_analyst_coverage_or_none() is None


# ── Genuine zero rows (query succeeds, nothing on file) ─────────────────────

def test_genuine_empty_result_load_analyst_coverage_or_none_returns_empty_df(monkeypatch):
    """A successful query with zero rows is a valid state, distinct from a
    failure -- must return an empty DataFrame, NOT None."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    out = db.load_analyst_coverage_or_none()
    assert out is not None
    assert isinstance(out, pd.DataFrame)
    assert out.empty


# ── Real rows ────────────────────────────────────────────────────────────────

def test_real_rows_both_functions_return_matching_data(monkeypatch):
    rows = [{"ticker": "AAPL", "article_date": "2026-08-01", "source": "CNBC Pro"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))

    out_plain = db.load_analyst_coverage()
    out_or_none = db.load_analyst_coverage_or_none()

    assert out_or_none is not None
    assert list(out_plain["ticker"]) == ["AAPL"]
    assert list(out_or_none["ticker"]) == ["AAPL"]


# ── Batch B: `limit` gains a real unbounded mode (2026-09-22) ───────────────
#
# load_analyst_coverage() already called .limit(N) explicitly with a
# caller-facing `limit: int = 100` parameter -- NOT the silent-PostgREST-cap
# bug class Batch A fixes. The bug here is different: several callers needed
# "the complete history" and, with no way to ask for "all of them", guessed
# an ever-bigger magic number (5000, then 10000) instead. `limit=None` is
# the fix; a real int (including the unchanged default of 100) must keep
# behaving EXACTLY as before -- a single non-paginating `.limit()` call.

def test_explicit_small_limit_still_a_single_non_paginating_call(monkeypatch):
    """A real, small `limit` (e.g. 5) must return AT MOST that many rows via
    ONE `.limit()` call -- byte-identical to pre-Batch-B behavior. This is
    the regression test: a future change must not accidentally start
    paginating under a small limit."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    rows = [{"ticker": f"T{i}", "article_date": "2026-08-01", "source": "CNBC Pro"} for i in range(20)]
    fake = _FakeClient(rows=rows)
    monkeypatch.setattr(db, "_client", lambda: fake)

    out = db.load_analyst_coverage(limit=5)
    assert len(out) == 5
    assert len(fake.builders) == 1  # exactly one .table() call, no pagination
    assert fake.builders[0]._limit == 5
    assert fake.builders[0]._range is None  # never touched .range() under a real limit


def test_limit_none_pages_through_more_than_one_page(monkeypatch):
    """`limit=None` ("give me everything") must return ALL rows, paginated
    via `.range()`, when the result spans more than one page."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_ANALYST_COVERAGE_PAGE_SIZE", 2)
    rows = [{"ticker": f"T{i}", "article_date": "2026-08-01", "source": "CNBC Pro"} for i in range(5)]
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))

    out = db.load_analyst_coverage(limit=None)
    assert len(out) == 5
    assert list(out["ticker"]) == [f"T{i}" for i in range(5)]

    out_or_none = db.load_analyst_coverage_or_none(limit=None)
    assert out_or_none is not None
    assert len(out_or_none) == 5


def test_limit_none_orders_by_id_as_tie_breaker(monkeypatch):
    """Regression for the Opus review finding (2026-09-23): the `limit=None`
    pagination path ordered by article_date alone, which ties for any two
    articles saved the same day. `.range()` pagination re-executes the query
    per page and cannot safely tie-break a non-unique ORDER BY across those
    separate executions -- this table has an unused `id` PK, so the fix
    orders by (article_date desc, id desc). Only applies to the `limit=None`
    path; a real int limit never enters this loop at all."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[{"ticker": "AAA", "article_date": "2026-08-01",
                               "source": "CNBC Pro", "id": 1}])
    monkeypatch.setattr(db, "_client", lambda: fake)

    db.load_analyst_coverage(limit=None)

    assert len(fake.builders) == 1
    order_calls = fake.builders[0].order_calls
    assert [args[0] for args, _kw in order_calls] == ["article_date", "id"]
    # Direction matters too: both must stay descending (newest-first) --
    # a flipped asc would pass a column-only check but change the result order.
    assert order_calls[0][1].get("desc") is True
    assert order_calls[1][1].get("desc") is True


def test_default_limit_omitted_entirely_unchanged_100_row_cap(monkeypatch):
    """THE most important regression test in this batch: calling with NO
    `limit` argument at all must still behave exactly as today -- a single
    non-paginating `.limit(100)` call, never pagination. Accidentally
    changing this default's behavior would be a real behavior change nobody
    asked for."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    rows = [{"ticker": f"T{i}", "article_date": "2026-08-01", "source": "CNBC Pro"} for i in range(150)]
    fake = _FakeClient(rows=rows)
    monkeypatch.setattr(db, "_client", lambda: fake)

    out = db.load_analyst_coverage()
    assert len(out) == 100
    assert len(fake.builders) == 1
    assert fake.builders[0]._limit == 100
    assert fake.builders[0]._range is None

    fake_or_none = _FakeClient(rows=rows)
    monkeypatch.setattr(db, "_client", lambda: fake_or_none)
    out_or_none = db.load_analyst_coverage_or_none()
    assert out_or_none is not None
    assert len(out_or_none) == 100
    assert len(fake_or_none.builders) == 1
    assert fake_or_none.builders[0]._limit == 100
    assert fake_or_none.builders[0]._range is None
