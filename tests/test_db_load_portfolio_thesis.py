"""Regression tests for stock_analyzer/db.py::load_portfolio_thesis_or_none().

load_portfolio_thesis() cannot distinguish "no thesis written this week"
from "the query itself failed" -- both its no-creds branch and its except
branch return the same empty list. That's harmless for its existing
consumer (Summary's F-232 standing-thesis card, which degrades gracefully
to "nothing to show" either way), but unsafe for a consumer that must never
treat a failed load as "no thesis exists yet" -- e.g. a weekly
"already written this week" guard, which would otherwise silently permit a
duplicate weekly thesis on a transient Supabase hiccup.
load_portfolio_thesis_or_none() makes the distinction explicit: None on ANY
failure (no creds or a raised exception), an empty list ONLY on a genuine
zero-row result. Mirrors test_db_load_exit_signals.py's pattern exactly,
adapted for a list[dict] return type instead of a DataFrame.
"""
from stock_analyzer import db


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    """Mimics the .select().gte().order().execute() chain
    load_portfolio_thesis()/load_portfolio_thesis_or_none() build."""

    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows or []
        self._raise = raise_on_execute

    def select(self, *_a, **_kw):
        return self

    def gte(self, *_a, **_kw):
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

def test_no_creds_load_portfolio_thesis_returns_empty(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    out = db.load_portfolio_thesis(lookback_days=7)
    assert out == []


def test_no_creds_load_portfolio_thesis_or_none_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_portfolio_thesis_or_none(lookback_days=7) is None


# ── Query raises (creds present, transient failure) ─────────────────────────

def test_query_failure_load_portfolio_thesis_returns_empty_not_none(monkeypatch):
    """The PRE-EXISTING behavior load_portfolio_thesis() must keep for its
    existing caller: a failure degrades to empty, never raises, never
    returns None. This function is left untouched by this build."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    out = db.load_portfolio_thesis(lookback_days=7)
    assert out == []


def test_query_failure_load_portfolio_thesis_or_none_returns_none(monkeypatch):
    """THE new sibling's behavior: with creds present, a raised exception
    must surface as None, not an empty list indistinguishable from a
    genuine zero-row result."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    assert db.load_portfolio_thesis_or_none(lookback_days=7) is None


# ── Genuine zero rows (query succeeds, nothing on file) ─────────────────────

def test_genuine_empty_result_load_portfolio_thesis_or_none_returns_empty_list(monkeypatch):
    """A successful query with zero rows is a valid state, distinct from a
    failure -- must return an empty list, NOT None."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    out = db.load_portfolio_thesis_or_none(lookback_days=7)
    assert out is not None
    assert out == []


# ── Real rows ────────────────────────────────────────────────────────────────

def test_real_rows_both_functions_return_matching_data(monkeypatch):
    rows = [{"thesis_date": "2026-08-24", "iso_year": 2026, "iso_week": 35, "prose": "steady"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))

    out_plain = db.load_portfolio_thesis(lookback_days=7)
    out_or_none = db.load_portfolio_thesis_or_none(lookback_days=7)

    assert out_or_none is not None
    assert out_plain == rows
    assert out_or_none == rows
