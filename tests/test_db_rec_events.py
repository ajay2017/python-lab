"""Regression tests for stock_analyzer/db.py::save_rec_events() /
load_rec_events() — Recommendation-Outcomes-Measurement Phase 1b.

Mirrors test_db_load_exit_signals.py / save_gate_suppressions's own test
coverage: the load-bearing offline-sentinel contract is `load_rec_events()`
returns None (never []) on ANY read failure, kept distinct from a genuine
zero-row result. `save_rec_events` mirrors `save_gate_suppressions`'s
upsert-only, read-only-guard, TypeError-compat-fallback shape.
"""
import pytest

from stock_analyzer import db

pytestmark = pytest.mark.fast


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeSelectBuilder:
    """Mimics the .select().order().range().execute() chain
    load_rec_events() builds."""

    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows or []
        self._raise = raise_on_execute
        self._range = None
        self.order_calls: list = []

    def select(self, *_a, **_kw):
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


class _FakeUpsertBuilder:
    def __init__(self, records, raise_type_error=False, raise_other=False):
        self.records = records
        self._raise_type_error = raise_type_error
        self._raise_other = raise_other
        self.upsert_calls = []

    def upsert(self, records, **kwargs):
        self.upsert_calls.append((records, kwargs))
        if self._raise_type_error and "ignore_duplicates" in kwargs:
            raise TypeError("ignore_duplicates unsupported")
        if self._raise_other:
            raise RuntimeError("boom")
        return self

    def execute(self):
        return _FakeExecResult(self.records)


class _FakeClient:
    def __init__(self, rows=None, raise_on_select=False,
                 raise_type_error_on_upsert=False, raise_other_on_upsert=False):
        self._rows = rows
        self._raise_on_select = raise_on_select
        # Built ONCE (not per .table() call) — save_rec_events's TypeError
        # retry path calls `_client().table(...).upsert(...)` a SECOND time,
        # and the test must observe both calls on the same builder rather
        # than silently starting a fresh one that erases the first.
        self.upsert_builder = _FakeUpsertBuilder(
            rows, raise_type_error_on_upsert, raise_other_on_upsert,
        )

    def table(self, name):
        assert name == "rec_events"
        # Return an object that supports BOTH select() (load) and upsert() (save).
        _sel = _FakeSelectBuilder(self._rows, self._raise_on_select)
        _ups = self.upsert_builder
        self.last_select_builder = _sel

        class _Combined:
            def select(_self, *a, **kw):
                return _sel

            def upsert(_self, records, **kw):
                return _ups.upsert(records, **kw)

        return _Combined()


_ROW = {
    "rec_type": "beta_trim", "ticker": "AAA", "fired_date": "2026-09-15",
    "source": "cron", "metric_name": "portfolio_beta",
    "metric_before": 1.5, "metric_predicted_after": 1.3, "rec_dollars": 1000.0,
}


# ── load_rec_events: offline sentinel ────────────────────────────────────────

def test_load_no_creds_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_rec_events() is None


def test_load_query_failure_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_select=True))
    assert db.load_rec_events() is None


def test_load_genuine_empty_result_returns_empty_list_not_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    out = db.load_rec_events()
    assert out is not None
    assert out == []


def test_load_real_rows_returned(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[_ROW]))
    out = db.load_rec_events()
    assert out is not None
    assert out[0]["ticker"] == "AAA"


def test_load_paginates_past_page_size(monkeypatch):
    """2026-09-22 data-foundation pass: PostgREST's own server-side default
    row cap on an unpaginated `.select()` (already confirmed live on
    model_predictions and recommendations) applies identically here. A
    result set bigger than one page must still come back whole via
    `.range()` looping, not silently capped at the first page."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_REC_EVENTS_PAGE_SIZE", 2)
    rows = [dict(_ROW, ticker=f"T{i}") for i in range(5)]
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))
    out = db.load_rec_events()
    assert out is not None
    assert len(out) == 5
    assert [r["ticker"] for r in out] == [f"T{i}" for i in range(5)]


def test_load_rec_events_orders_by_full_unique_key_as_tie_breaker(monkeypatch):
    """Regression for the Opus review finding (2026-09-23): rec_events has
    no `id` column, so `fired_date` alone ties across every ticker that
    fired the same rec_type the same day. `.range()` pagination re-executes
    the query per page and cannot safely tie-break a non-unique ORDER BY
    across those separate executions. Asserts the query orders by the
    table's full unique key (rec_type, ticker, fired_date, source), not
    just fired_date -- a prior version of this test could not have caught
    the missing tie-breaker by construction (it only proved pagination
    assembles correctly, not that ordering is deterministic)."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[dict(_ROW)])
    monkeypatch.setattr(db, "_client", lambda: fake)

    db.load_rec_events()

    ordered_cols = [args[0] for args, _kw in fake.last_select_builder.order_calls]
    assert ordered_cols == ["fired_date", "ticker", "rec_type", "source"]


# ── save_rec_events ──────────────────────────────────────────────────────────

def test_save_readonly_is_a_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    out = db.save_rec_events([_ROW])
    assert out == {"attempted": 0, "saved": 0, "error": "read-only"}


def test_save_no_db_is_a_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: False)
    out = db.save_rec_events([_ROW])
    assert out == {"attempted": 0, "saved": 0, "error": None}


def test_save_empty_rows_is_a_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    out = db.save_rec_events([])
    assert out == {"attempted": 0, "saved": 0, "error": None}


def test_save_drops_rows_missing_required_keys(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[])
    monkeypatch.setattr(db, "_client", lambda: fake)
    bad_rows = [
        {"rec_type": "beta_trim", "ticker": "", "fired_date": "2026-09-15", "source": "cron"},
        {"rec_type": "", "ticker": "AAA", "fired_date": "2026-09-15", "source": "cron"},
        {"rec_type": "beta_trim", "ticker": "AAA", "fired_date": None, "source": "cron"},
        {"rec_type": "beta_trim", "ticker": "AAA", "fired_date": "2026-09-15", "source": ""},
    ]
    out = db.save_rec_events(bad_rows)
    assert out == {"attempted": 0, "saved": 0, "error": None}


def test_save_happy_path_upserts_with_correct_conflict_key(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[])
    monkeypatch.setattr(db, "_client", lambda: fake)

    out = db.save_rec_events([_ROW])
    assert out["attempted"] == 1
    assert out["saved"] == 1
    assert out["error"] is None
    records, kwargs = fake.upsert_builder.upsert_calls[0]
    assert kwargs.get("on_conflict") == "rec_type,ticker,fired_date,source"
    assert kwargs.get("ignore_duplicates") is True
    assert records[0]["ticker"] == "AAA"
    assert records[0]["metric_before"] == pytest.approx(1.5)


def test_save_type_error_falls_back_without_ignore_duplicates(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[], raise_type_error_on_upsert=True)
    monkeypatch.setattr(db, "_client", lambda: fake)

    out = db.save_rec_events([_ROW])
    assert out["saved"] == 1
    assert "compat" in out["error"]
    # Second call must have been retried WITHOUT ignore_duplicates.
    assert len(fake.upsert_builder.upsert_calls) == 2
    _, second_kwargs = fake.upsert_builder.upsert_calls[1]
    assert "ignore_duplicates" not in second_kwargs


def test_save_other_exception_reports_zero_saved(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[], raise_other_on_upsert=True)
    monkeypatch.setattr(db, "_client", lambda: fake)
    monkeypatch.setattr(db, "_record_db_error", lambda *a, **kw: None)

    out = db.save_rec_events([_ROW])
    assert out["attempted"] == 1
    assert out["saved"] == 0
    assert out["error"] is not None


def test_save_null_preserves_numeric_fields_never_fabricates_zero(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[])
    monkeypatch.setattr(db, "_client", lambda: fake)

    row = dict(_ROW)
    row["metric_before"] = None
    row["metric_predicted_after"] = None
    row["rec_dollars"] = None
    row["price_at_rec"] = None
    db.save_rec_events([row])
    records, _ = fake.upsert_builder.upsert_calls[0]
    assert records[0]["metric_before"] is None
    assert records[0]["metric_predicted_after"] is None
    assert records[0]["rec_dollars"] is None
    assert records[0]["price_at_rec"] is None
