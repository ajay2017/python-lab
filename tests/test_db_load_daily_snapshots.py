"""Regression tests for stock_analyzer/db.py::load_daily_snapshots_or_none().

load_daily_snapshots() cannot distinguish "zero snapshots exist" from "the
query itself failed" -- both its no-creds branch and its except branch
return the same empty DataFrame. That's harmless for its existing consumers
(judgment_grading, the F-247 Alpha Attribution readiness panel, Self Track
Record, Predictive Analytics, structural trend analysis, Weekly Debrief --
all of which already degrade gracefully to "no history yet"), but unsafe
for a consumer that must never treat a failed load as "zero snapshots
exist". load_daily_snapshots_or_none() makes the distinction explicit: None
on ANY failure (no creds or a raised exception), an empty DataFrame ONLY on
a genuine zero-row result. Mirrors test_db_load_recommendations.py's
pattern exactly.
"""
import pandas as pd

from stock_analyzer import db


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    """Mimics the .select().gte().lte().order().execute() chain
    load_daily_snapshots()/load_daily_snapshots_or_none() build."""

    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows or []
        self._raise = raise_on_execute

    def select(self, *_a, **_kw):
        return self

    def gte(self, *_a, **_kw):
        return self

    def lte(self, *_a, **_kw):
        return self

    def order(self, *_a, **_kw):
        return self

    def execute(self):
        if self._raise:
            raise RuntimeError("simulated transient Supabase failure")
        return _FakeExecResult(self._rows)


class _FakeClient:
    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows
        self._raise = raise_on_execute

    def table(self, _name):
        return _FakeQueryBuilder(self._rows, self._raise)


# ── No credentials ──────────────────────────────────────────────────────────

def test_no_creds_load_daily_snapshots_returns_empty(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    out = db.load_daily_snapshots()
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_no_creds_load_daily_snapshots_or_none_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_daily_snapshots_or_none() is None


# ── Query raises (creds present, transient failure) ─────────────────────────

def test_query_failure_load_daily_snapshots_returns_empty_not_none(monkeypatch):
    """The PRE-EXISTING behavior load_daily_snapshots() must keep for its
    existing callers: a failure degrades to empty, never raises, never
    returns None. This function is left untouched by this build."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    out = db.load_daily_snapshots()
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_query_failure_load_daily_snapshots_or_none_returns_none(monkeypatch):
    """THE new sibling's behavior: with creds present, a raised exception
    must surface as None, not an empty DataFrame indistinguishable from a
    genuine zero-row result."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    assert db.load_daily_snapshots_or_none() is None


# ── Genuine zero rows (query succeeds, nothing on file) ─────────────────────

def test_genuine_empty_result_load_daily_snapshots_or_none_returns_empty_df(monkeypatch):
    """A successful query with zero rows is a valid state, distinct from a
    failure -- must return an empty DataFrame, NOT None."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    out = db.load_daily_snapshots_or_none()
    assert out is not None
    assert isinstance(out, pd.DataFrame)
    assert out.empty


# ── Real rows ────────────────────────────────────────────────────────────────

def test_real_rows_both_functions_return_matching_data(monkeypatch):
    rows = [{"snapshot_date": "2026-08-01", "ticker": "AAPL", "shares": 10, "close_price": 200.0}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))

    out_plain = db.load_daily_snapshots()
    out_or_none = db.load_daily_snapshots_or_none()

    assert out_or_none is not None
    assert list(out_plain["ticker"]) == ["AAPL"]
    assert list(out_or_none["ticker"]) == ["AAPL"]
