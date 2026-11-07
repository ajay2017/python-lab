"""TrPage: paged Supabase readers must survive a server-side max-rows cap below
the page size. `db._read_all_pages` advances by rows actually returned and stops
only on an EMPTY page; every paged reader in db.py goes through it."""
import re
from pathlib import Path

import pytest

from stock_analyzer import db


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeBuilder:
    """Supports the filter/order surface the readers use; `.range(a, b)` +
    `.execute()` return rows[a : min(b + 1, a + server_cap)] -- a PostgREST
    max-rows cap."""

    def __init__(self, rows, server_cap, calls, fail_on_call=None):
        self._rows = rows
        self._cap = server_cap
        self._calls = calls
        self._fail_on_call = fail_on_call
        self._range = None

    def _self(self, *a, **k):
        return self

    select = order = eq = gte = lte = in_ = neq = is_ = limit = _self

    @property
    def not_(self):
        return self

    def range(self, a, b):
        self._range = (a, b)
        self._calls.append((a, b))
        return self

    def execute(self):
        if self._fail_on_call is not None and len(self._calls) == self._fail_on_call:
            raise RuntimeError("boom")
        a, b = self._range
        return _Resp(self._rows[a: min(b + 1, a + self._cap)])


class _FakeClient:
    def __init__(self, tables, server_cap, fail_on_call=None):
        self._tables = tables
        self._cap = server_cap
        self._fail_on_call = fail_on_call
        self.calls: list = []

    def table(self, name):
        return _FakeBuilder(self._tables.get(name, []), self._cap, self.calls,
                            self._fail_on_call)


def _rows(n, key="id"):
    return [{key: i, "v": i} for i in range(n)]


def _build_for(client, table="t"):
    return lambda: client.table(table).select("*").order("id")


# ── _read_all_pages ──────────────────────────────────────────────────────────

def test_server_cap_below_page_size_does_not_truncate():
    rows = _rows(2345)
    c = _FakeClient({"t": rows}, server_cap=500)
    out = db._read_all_pages(_build_for(c), 1000)
    assert out == rows


def test_cap_at_or_above_page_size_returns_all():
    rows = _rows(2345)
    for cap in (1000, 5000):
        c = _FakeClient({"t": rows}, server_cap=cap)
        assert db._read_all_pages(_build_for(c), 1000) == rows


def test_empty_table_returns_empty_list():
    c = _FakeClient({"t": []}, server_cap=500)
    assert db._read_all_pages(_build_for(c), 1000) == []


def test_exactly_one_page_no_duplicates_and_terminates():
    rows = _rows(1000)
    c = _FakeClient({"t": rows}, server_cap=5000)
    out = db._read_all_pages(_build_for(c), 1000)
    assert out == rows
    # one full page, then one empty page -- and no more
    assert c.calls == [(0, 999), (1000, 1999)]


def test_offsets_advance_by_rows_returned():
    rows = _rows(1200)
    c = _FakeClient({"t": rows}, server_cap=500)
    db._read_all_pages(_build_for(c), 1000)
    assert [a for a, _ in c.calls] == [0, 500, 1000, 1200]


def test_failure_on_second_page_raises():
    c = _FakeClient({"t": _rows(2000)}, server_cap=500, fail_on_call=2)
    with pytest.raises(RuntimeError, match="boom"):
        db._read_all_pages(_build_for(c), 1000)


def test_duplicate_id_across_page_boundary_kept_once():
    class _Shifted:
        """Page 2 repeats the last row of page 1 (a writer inserted between)."""
        def __init__(self):
            self.pages = [
                [{"id": 1}, {"id": 2}, {"id": 3}],
                [{"id": 3}, {"id": 4}],
                [],
            ]
            self.i = 0

        def build(self):
            return self

        def range(self, a, b):
            return self

        def execute(self):
            p = self.pages[self.i]
            self.i += 1
            return _Resp(p)

    s = _Shifted()
    out = db._read_all_pages(s.build, 3)
    assert [r["id"] for r in out] == [1, 2, 3, 4]


def test_dedupe_key_none_keeps_duplicates():
    class _Dups:
        def __init__(self):
            self.pages = [[{"id": 1}, {"id": 1}], [{"id": 1}], []]
            self.i = 0

        def build(self):
            return self

        def range(self, a, b):
            return self

        def execute(self):
            p = self.pages[self.i]
            self.i += 1
            return _Resp(p)

    d = _Dups()
    out = db._read_all_pages(d.build, 2, dedupe_key=None)
    assert len(out) == 3


def test_rows_without_the_key_are_all_kept():
    rows = [{"v": 1}, {"v": 1}, {"id": None, "v": 2}, {"id": None, "v": 2}]
    c = _FakeClient({"t": rows}, server_cap=3)
    out = db._read_all_pages(_build_for(c), 1000)
    assert out == rows


def test_backend_ignoring_range_cannot_loop_forever():
    class _Stuck:
        def build(self):
            return self

        def range(self, a, b):
            return self

        def execute(self):
            return _Resp([{"v": 1}])

    s = _Stuck()
    with pytest.raises(RuntimeError, match="did not terminate"):
        db._read_all_pages(s.build, 10, dedupe_key=None)


# ── end to end through real readers ──────────────────────────────────────────

def _patch(monkeypatch, client):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: client)


def test_load_trades_or_none_not_truncated_by_server_cap(monkeypatch):
    trades = [{"id": i, "ticker": "AAA", "traded_at": f"2026-01-01T00:00:{i % 60:02d}"}
              for i in range(1200)]
    _patch(monkeypatch, _FakeClient({"trades": trades}, server_cap=500))
    df = db.load_trades_or_none()
    assert df is not None
    assert len(df) == 1200


def test_load_recommendations_all_pages_not_truncated(monkeypatch):
    recs = [{"id": i, "ticker": "AAA"} for i in range(1200)]
    _patch(monkeypatch, _FakeClient({"recommendations": recs}, server_cap=500))
    out = db._load_recommendations_all_pages()
    assert [r["id"] for r in out] == list(range(1200))


def test_rec_events_no_id_column_not_truncated(monkeypatch):
    ev = [{"rec_type": "x", "ticker": f"T{i}", "fired_date": "2026-01-01", "source": "s"}
          for i in range(1200)]
    _patch(monkeypatch, _FakeClient({"rec_events": ev}, server_cap=500))
    out = db.load_rec_events()
    assert out is not None and len(out) == 1200


def test_failed_page_still_returns_none_from_none_contract_reader(monkeypatch):
    recs = [{"id": i} for i in range(2000)]
    _patch(monkeypatch, _FakeClient({"gate_suppressions": recs}, server_cap=500,
                                    fail_on_call=2))
    assert db.load_gate_suppressions() is None


# ── source pin ───────────────────────────────────────────────────────────────

_DB_SRC = (Path(__file__).resolve().parent.parent / "stock_analyzer" / "db.py").read_text(
    encoding="utf-8")


def test_no_short_page_break_loop_remains():
    # The old stop rule, in either spelling: `if len(page) < page_size:` or
    # `if len(_page) < _TRADES_PAGE_SIZE:`. Case-insensitive; `if` keeps the
    # helper's docstring (which names the old rule) from matching.
    pat = re.compile(r"\bif\s+len\(\w+\)\s*<\s*\w*page\w*", re.IGNORECASE)
    assert not pat.search(_DB_SRC)
    # The pattern really does catch both old forms.
    assert pat.search("if len(page) < page_size:")
    assert pat.search("if len(_page) < _TRADES_PAGE_SIZE:")


def test_range_call_only_inside_read_all_pages():
    # An actual `.range(<arg>` call; docstrings write "`.range()`" (empty parens).
    calls = re.findall(r"\.range\(\s*\w", _DB_SRC)
    assert len(calls) == 1
    helper = _DB_SRC[_DB_SRC.index("def _read_all_pages"):]
    helper = helper[: helper.index("\n\n\n")]
    assert re.search(r"\.range\(\s*\w", helper)


# ── composite dedupe key (N1 C7 / TrPage f/u 4) ──────────────────────────────

class _Pages:
    def __init__(self, pages):
        self.pages, self.i = pages, 0

    def build(self):
        return self

    def _self(self, *a, **k):
        return self

    select = gte = lte = order = _self

    def range(self, a, b):
        return self

    def execute(self):
        p = self.pages[self.i]
        self.i += 1
        return _Resp(p)


def _snap(d, t, **kw):
    return {"snapshot_date": d, "ticker": t, **kw}


def test_composite_dedupe_drops_a_repeated_pair_and_keeps_distinct_ones():
    p = _Pages([
        [_snap("2026-11-05", "AAA"), _snap("2026-11-05", "BBB")],
        [_snap("2026-11-05", "BBB"), _snap("2026-11-05", "CCC"), _snap("2026-11-06", "AAA")],
        [],
    ])
    out = db._read_all_pages(p.build, 2, dedupe_key=("snapshot_date", "ticker"))
    assert [(r["snapshot_date"], r["ticker"]) for r in out] == [
        ("2026-11-05", "AAA"), ("2026-11-05", "BBB"), ("2026-11-05", "CCC"),
        ("2026-11-06", "AAA")]


def test_composite_dedupe_keeps_first_seen_and_ignores_rows_with_a_none_part():
    p = _Pages([
        [_snap("2026-11-05", "AAA", shares=1), _snap("2026-11-05", "AAA", shares=2),
         _snap("2026-11-05", None, v=1), _snap("2026-11-05", None, v=1),
         {"v": 3}, {"v": 3}],
        [],
    ])
    out = db._read_all_pages(p.build, 10, dedupe_key=("snapshot_date", "ticker"))
    assert [r.get("shares") for r in out if r.get("ticker") == "AAA"] == [1]
    assert len(out) == 5          # the None-part and no-key rows are all kept


def test_str_dedupe_key_unchanged_by_the_tuple_support():
    p = _Pages([[{"id": 1}, {"id": 1}, {"id": 2}], []])
    assert [r["id"] for r in db._read_all_pages(p.build, 10)] == [1, 2]
    assert [r["id"] for r in db._read_all_pages(_Pages([[{"id": 1}, {"id": 1}], []]).build, 10,
                                                dedupe_key=None)] == [1, 1]


def test_daily_snapshots_reader_dedupes_on_the_composite_pk(monkeypatch):
    rows = [{"snapshot_date": "2026-11-05", "ticker": "AAA", "shares": 1, "close_price": 5.0},
            {"snapshot_date": "2026-11-05", "ticker": "BBB", "shares": 1, "close_price": 5.0}]
    # a past-dated row inserted mid-read shifts the offsets: page 2 repeats the
    # last row of page 1 before the new one
    seen = _Pages([rows[:2], [rows[1], {"snapshot_date": "2026-11-05", "ticker": "CCC",
                                        "shares": 2, "close_price": 5.0}], []])

    class _C:
        def table(self, name):
            assert name == "daily_snapshots"
            return seen

    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _C())
    monkeypatch.setattr(db, "_DAILY_SNAPSHOTS_PAGE_SIZE", 2)
    out = db._load_daily_snapshots_all_pages("2026-11-01")
    assert [r["ticker"] for r in out] == ["AAA", "BBB", "CCC"]
