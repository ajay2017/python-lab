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
        self.order_calls: list = []

    def select(self, *_a, **_kw):
        return self

    def gte(self, *_a, **_kw):
        return self

    def lte(self, *_a, **_kw):
        return self

    def order(self, *_a, **_kw):
        self.order_calls.append((_a, _kw))
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
        # Records every builder .table() hands out -- _load_recommendations_
        # all_pages rebuilds a fresh builder each page (per its own reviewed
        # "rebuilt fresh each iteration" pattern), so a test asserting on
        # order-by columns needs the LAST one built, mirroring
        # test_db_load_analyst_coverage.py's identical tracking.
        self.builders: list = []

    def table(self, _name):
        b = _FakeQueryBuilder(self._rows, self._raise)
        self.builders.append(b)
        return b


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


# ── Non-unique sort key tie-break (2026-10-02 review finding H1) ────────────
#
# `surfaced_at` defaults to now() server-side, so every row written by one
# save_recommendations() batch call shares an identical timestamp -- ordering
# by surfaced_at ALONE means `.range()`'s page boundaries (which re-execute
# the query fresh per page) can land inside a tie group, silently skipping or
# duplicating rows across pages. Same class already fixed for
# daily_snapshots/analyst_coverage/rec_events (commit 0f583fe); this entry
# was simply missed in that pass. `id` is the table's own auto-generated
# primary key (see _REC_COLS), always unique -- (surfaced_at desc, id desc)
# is therefore a genuine total order.

def test_load_recommendations_orders_by_id_as_tie_breaker(monkeypatch):
    """Regression for the 2026-10-02 review finding (H1): the query ordered
    by surfaced_at alone, which ties for every row in a single batch insert.
    `.range()` pagination re-executes the query per page and cannot safely
    tie-break a non-unique ORDER BY across those separate executions -- this
    table has an `id` PK (unused for ordering before this fix), so the fix
    orders by (surfaced_at desc, id desc). Mirrors
    test_db_load_analyst_coverage.py's test_limit_none_orders_by_id_as_tie_
    breaker exactly."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[{"ticker": "AAA", "rec_date": "2026-08-01",
                               "rec_type": "new_pick", "id": 1}])
    monkeypatch.setattr(db, "_client", lambda: fake)

    db.load_recommendations()

    assert len(fake.builders) == 1
    order_calls = fake.builders[0].order_calls
    assert [args[0] for args, _kw in order_calls] == ["surfaced_at", "id"]
    # Direction matters too: both must stay descending (newest-first) -- a
    # flipped asc would pass a column-only check but change the result order.
    assert order_calls[0][1].get("desc") is True
    assert order_calls[1][1].get("desc") is True


def test_load_recommendations_pagination_is_complete_across_a_tie_group(monkeypatch):
    """Opus review, 2026-10-02 confirmation pass: this test's fake `.execute()`
    (`_FakeClient`/`_FakeQueryBuilder`) just slices a pre-built list -- it
    never actually re-sorts based on which `.order(...)` columns were
    requested, so it CANNOT by itself distinguish "ordered correctly by the
    (surfaced_at, id) fix" from "ordered correctly because the fixture
    happened to already be in that order" -- it would pass identically
    against the pre-fix single-column `.order("surfaced_at")` code too. The
    test that actually catches the tie-break regression is
    `test_load_recommendations_orders_by_id_as_tie_breaker` above, which
    asserts the real `.order()` call arguments. What THIS test verifies is
    narrower but still real: a tie group (a single save_recommendations()
    batch, all sharing one server-assigned surfaced_at) that's wider than
    one page and straddles the `.range()` boundary is still returned
    completely -- every row exactly once, no duplicates, no silent skips --
    across repeated identical queries (simulated here by running the load
    twice)."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_RECOMMENDATIONS_PAGE_SIZE", 2)
    # 5 rows, same surfaced_at, ids 5..1 -- already in the (surfaced_at desc,
    # id desc) order the real query would request, since the fake execute()
    # just slices rather than re-sorting (consistent with the established
    # pattern in test_db_load_analyst_coverage.py / test_db_rec_events.py,
    # which assert the ORDER BY clause itself rather than modeling Postgres's
    # real tie-break execution).
    rows = [{"ticker": f"T{i}", "rec_date": "2026-08-01", "rec_type": "new_pick",
             "surfaced_at": "2026-10-02T09:00:00+00:00", "id": i}
            for i in (5, 4, 3, 2, 1)]
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))

    out1 = db.load_recommendations()
    out2 = db.load_recommendations()

    assert list(out1["id"]) == [5, 4, 3, 2, 1]
    assert list(out2["id"]) == [5, 4, 3, 2, 1]
    assert len(out1) == 5 and len(out1["id"].unique()) == 5  # no duplicates, nothing skipped
