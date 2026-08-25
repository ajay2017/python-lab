"""Regression tests for stock_analyzer/db.py::load_exit_signals_or_none()
-- F-233's SELL-side extension (Self vs Engine).

load_exit_signals() cannot distinguish "zero exit signals exist" from
"the query itself failed" -- both its no-creds branch and its except branch
return the same empty DataFrame. That's harmless for its existing consumers
(app.py, cron_runner.py, debrief_advisor.py all degrade to a "nothing to
show" state either way), but unsafe for self_track_record.classify_sells(),
which must never treat a failed load as "zero signals exist" (it would
silently misclassify every engine-called SELL as self-initiated).
load_exit_signals_or_none() makes the distinction explicit: None on ANY
failure (no creds or a raised exception), an empty DataFrame ONLY on a
genuine zero-row result. Mirrors test_db_load_recommendations.py's pattern
exactly.
"""
import pandas as pd

from stock_analyzer import db


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    """Mimics the .select().gte().execute() chain
    load_exit_signals()/load_exit_signals_or_none() build."""

    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows or []
        self._raise = raise_on_execute

    def select(self, *_a, **_kw):
        return self

    def gte(self, *_a, **_kw):
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

def test_no_creds_load_exit_signals_returns_empty(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    out = db.load_exit_signals()
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_no_creds_load_exit_signals_or_none_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_exit_signals_or_none() is None


# ── Query raises (creds present, transient failure) ─────────────────────────

def test_query_failure_load_exit_signals_returns_empty_not_none(monkeypatch):
    """The PRE-EXISTING behavior load_exit_signals() must keep for its
    existing callers: a failure degrades to empty, never raises, never
    returns None. This function is left untouched by the SELL-side build."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    out = db.load_exit_signals()
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_query_failure_load_exit_signals_or_none_returns_none(monkeypatch):
    """THE new sibling's behavior: with creds present, a raised exception
    must surface as None, not an empty DataFrame indistinguishable from a
    genuine zero-row result."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    assert db.load_exit_signals_or_none() is None


# ── Genuine zero rows (query succeeds, nothing on file) ─────────────────────

def test_genuine_empty_result_load_exit_signals_or_none_returns_empty_df(monkeypatch):
    """A successful query with zero rows is a valid state, distinct from a
    failure -- must return an empty DataFrame, NOT None."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    out = db.load_exit_signals_or_none()
    assert out is not None
    assert isinstance(out, pd.DataFrame)
    assert out.empty


# ── Real rows ────────────────────────────────────────────────────────────────

def test_real_rows_both_functions_return_matching_data(monkeypatch):
    rows = [{"ticker": "AAPL", "signal_date": "2026-08-01", "signal_type": "EXIT"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))

    out_plain = db.load_exit_signals()
    out_or_none = db.load_exit_signals_or_none()

    assert out_or_none is not None
    assert list(out_plain["ticker"]) == ["AAPL"]
    assert list(out_or_none["ticker"]) == ["AAPL"]
