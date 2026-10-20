"""Period-report C3: load_snaptrade_income_events_or_none and the paginated
load_trades_or_none. None = could not read; [] / empty frame = read and empty;
a failure on ANY page is None, never a partial result."""
import pandas as pd
import pytest

from stock_analyzer import db

pytestmark = pytest.mark.fast


class _Exec:
    def __init__(self, data):
        self.data = data


class _Builder:
    def __init__(self, rows, fail_on_call=None, state=None):
        self._rows = rows
        self._fail_on_call = fail_on_call
        self._state = state
        self._rng = None
        self.orders: list = []

    def select(self, *_a, **_k): return self
    def gte(self, *_a, **_k): return self

    def order(self, *a, **k):
        self.orders.append((a, k))
        return self

    def range(self, s, e):
        self._rng = (s, e)
        return self

    def execute(self):
        self._state["n"] += 1
        if self._fail_on_call is not None and self._state["n"] == self._fail_on_call:
            raise RuntimeError("simulated page failure")
        if self._rng is None:
            return _Exec(self._rows)
        return _Exec(self._rows[self._rng[0]:self._rng[1] + 1])


class _Client:
    def __init__(self, rows=None, fail_on_call=None):
        self._rows = rows or []
        self._fail_on_call = fail_on_call
        self.state = {"n": 0}
        self.builders: list = []

    def table(self, _name):
        b = _Builder(self._rows, self._fail_on_call, self.state)
        self.builders.append(b)
        return b


def _use(monkeypatch, client, has=True):
    monkeypatch.setattr(db, "has_db", lambda: has)
    monkeypatch.setattr(db, "_client", lambda: client)


def _trade(i):
    return {"id": i, "ticker": f"T{i}", "action": "BUY", "shares": 1.0,
            "price": 10.0, "traded_at": f"2026-01-{30 - i:02d}T12:00:00+00:00"}


def _income(i):
    return {"id": i, "event_type": "dividend", "ticker": "AAA", "amount": 1.0,
            "event_date": f"2026-01-{i + 1:02d}"}


# ── income events ───────────────────────────────────────────────────────────

def test_income_no_db_is_none(monkeypatch):
    _use(monkeypatch, _Client(), has=False)
    assert db.load_snaptrade_income_events_or_none() is None


def test_income_exception_is_none_not_empty(monkeypatch):
    _use(monkeypatch, _Client(fail_on_call=1))
    assert db.load_snaptrade_income_events_or_none() is None
    # the lenient sibling is unchanged: still collapses to []
    _use(monkeypatch, _Client(fail_on_call=1))
    assert db.load_snaptrade_income_events() == []


def test_income_empty_success_is_empty_list(monkeypatch):
    _use(monkeypatch, _Client(rows=[]))
    assert db.load_snaptrade_income_events_or_none() == []


def test_income_rows_match_old_function(monkeypatch):
    rows = [_income(i) for i in range(3)]
    _use(monkeypatch, _Client(rows=rows))
    assert db.load_snaptrade_income_events_or_none() == rows
    assert db.load_snaptrade_income_events_or_none() == db.load_snaptrade_income_events()


def test_income_paginates_and_orders_with_tiebreak(monkeypatch):
    monkeypatch.setattr(db, "_INCOME_EVENTS_PAGE_SIZE", 2)
    rows = [_income(i) for i in range(5)]
    c = _Client(rows=rows)
    _use(monkeypatch, c)
    assert db.load_snaptrade_income_events_or_none() == rows
    assert [a[0] for a, _k in c.builders[0].orders] == ["event_date", "id"]
    # Direction pinned too (Opus C3 review N4): ascending on both keys.
    assert [_k for _a, _k in c.builders[0].orders] == [{"desc": False}, {"desc": False}]


def test_income_failure_on_page_2_is_none(monkeypatch):
    monkeypatch.setattr(db, "_INCOME_EVENTS_PAGE_SIZE", 2)
    _use(monkeypatch, _Client(rows=[_income(i) for i in range(5)], fail_on_call=2))
    assert db.load_snaptrade_income_events_or_none() is None


# ── trades pagination ───────────────────────────────────────────────────────

def test_trades_multi_page_concatenates_in_order(monkeypatch):
    monkeypatch.setattr(db, "_TRADES_PAGE_SIZE", 2)
    rows = [_trade(i) for i in range(5)]
    c = _Client(rows=rows)
    _use(monkeypatch, c)
    out = db.load_trades_or_none()
    assert out is not None
    assert list(out["id"]) == [0, 1, 2, 3, 4]
    assert c.state["n"] == 3  # 2 + 2 + 1 (short page stops the loop)
    assert [a[0] for a, _k in c.builders[0].orders] == ["traded_at", "id"]
    # Newest first on both keys (thesis_cluster / app.py read the latest BUY first).
    assert [_k for _a, _k in c.builders[0].orders] == [{"desc": True}, {"desc": True}]


def test_trades_failure_on_page_2_is_none_never_partial(monkeypatch):
    monkeypatch.setattr(db, "_TRADES_PAGE_SIZE", 2)
    _use(monkeypatch, _Client(rows=[_trade(i) for i in range(5)], fail_on_call=2))
    assert db.load_trades_or_none() is None


def test_trades_single_short_page_stops_after_one_call(monkeypatch):
    monkeypatch.setattr(db, "_TRADES_PAGE_SIZE", 10)
    c = _Client(rows=[_trade(i) for i in range(3)])
    _use(monkeypatch, c)
    out = db.load_trades_or_none()
    assert len(out) == 3
    assert c.state["n"] == 1


def test_trades_empty_is_empty_frame_not_none(monkeypatch):
    _use(monkeypatch, _Client(rows=[]))
    out = db.load_trades_or_none()
    assert isinstance(out, pd.DataFrame) and out.empty


def test_trades_legacy_column_backfill_still_applies_across_pages(monkeypatch):
    monkeypatch.setattr(db, "_TRADES_PAGE_SIZE", 2)
    _use(monkeypatch, _Client(rows=[_trade(i) for i in range(3)]))
    out = db.load_trades_or_none()
    assert "situational_category" in out.columns
    assert list(out["asset_type"]) == ["stock"] * 3


def test_load_trades_lenient_wrapper_shares_the_paginated_path(monkeypatch):
    monkeypatch.setattr(db, "_TRADES_PAGE_SIZE", 2)
    _use(monkeypatch, _Client(rows=[_trade(i) for i in range(5)]))
    assert len(db.load_trades()) == 5
