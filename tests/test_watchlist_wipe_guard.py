"""WLWipe: a failed watchlist read must never become an all-rows watchlist sweep.

A failed startup read degrades `st.session_state.watchlist` to `[]`; the old
full-replace `save_watchlist` fed from it deleted every row (and its
`added_at`). Writes are now intent-only:
  1. `db.add_to_watchlist` upserts only the named tickers, never deletes.
  2. `db.remove_from_watchlist` deletes only the named tickers (`in_` filter),
     never an unfiltered delete.
  3. app.py (no test imports it, so source/AST pins): `save_watchlist` is gone
     and no write is ever fed the whole session list.
"""
import ast
from pathlib import Path

import pytest

from stock_analyzer import db
from tests._ast_cache import parse_file_cached

_APP = Path(__file__).resolve().parents[1] / "app.py"


class _Rec:
    """Records every table() call and every builder method invoked on it."""
    def __init__(self, log, table, boom=False):
        self._log, self._table, self._boom = log, table, boom

    def _rec(self, op, *a):
        self._log.append((self._table, op, a))
        return self

    def upsert(self, records, on_conflict=None): return self._rec("upsert", list(records))
    def delete(self): return self._rec("delete")
    def neq(self, *a, **k): return self._rec("neq", *a)
    def in_(self, *a, **k): return self._rec("in_", *a)

    @property
    def not_(self):
        # Recorded, so the old sweep `.delete().not_.in_(...)` can't pass as a
        # filtered delete.
        return self._rec("not_")

    def execute(self):
        if self._boom:
            raise RuntimeError("boom")
        return None


class _FakeClient:
    def __init__(self, boom=False):
        self.log: list = []
        self.table_calls: list = []
        self.boom = boom

    def table(self, name):
        self.table_calls.append(name)
        return _Rec(self.log, name, self.boom)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    c = _FakeClient()
    monkeypatch.setattr(db, "_client", lambda: c)
    return c


def _ops(fake):
    return [op for _, op, _ in fake.log]


# ── add ─────────────────────────────────────────────────────────────────────

def test_add_upserts_cleaned_deduped_names_and_never_deletes(fake):
    assert db.add_to_watchlist([" aapl ", "MSFT", "aapl", "", "  "]) is True
    assert fake.log == [("watchlist", "upsert", ([{"ticker": "AAPL"}, {"ticker": "MSFT"}],))]
    assert "delete" not in _ops(fake)
    assert "neq" not in _ops(fake)
    assert "not_" not in _ops(fake)


@pytest.mark.parametrize("empty", [[], ["", "  "]])
def test_add_empty_is_noop_with_no_db_call(fake, empty):
    assert db.add_to_watchlist(empty) is False
    assert fake.table_calls == []


# ── remove ──────────────────────────────────────────────────────────────────

def test_remove_deletes_only_named_tickers(fake):
    assert db.remove_from_watchlist([" nvda", "amd", "NVDA"]) is True
    assert _ops(fake) == ["delete", "in_"]
    assert fake.log[1] == ("watchlist", "in_", ("ticker", ["NVDA", "AMD"]))
    assert "neq" not in _ops(fake)
    assert "not_" not in _ops(fake)
    assert "upsert" not in _ops(fake)


@pytest.mark.parametrize("empty", [[], ["", "  "]])
def test_remove_empty_never_issues_an_unfiltered_delete(fake, empty):
    assert db.remove_from_watchlist(empty) is False
    assert fake.table_calls == []


# ── guards ──────────────────────────────────────────────────────────────────

def test_readonly_is_a_noop_for_both(fake, monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    assert db.add_to_watchlist(["AAPL"]) is False
    assert db.remove_from_watchlist(["AAPL"]) is False
    assert fake.table_calls == []


def test_no_db_returns_false(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    assert db.add_to_watchlist(["AAPL"]) is False
    assert db.remove_from_watchlist(["AAPL"]) is False


def test_execute_exception_returns_false(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(boom=True))
    monkeypatch.setattr(db.st, "error", lambda *a, **k: None)
    monkeypatch.setattr(db, "_record_db_error", lambda *a, **k: None)
    assert db.add_to_watchlist(["AAPL"]) is False
    assert db.remove_from_watchlist(["AAPL"]) is False


def test_full_replace_save_is_gone():
    assert not hasattr(db, "save_watchlist")


# ── app.py source / AST pins ────────────────────────────────────────────────

def _is_session_watchlist(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "watchlist"
            and isinstance(node.value, ast.Attribute) and node.value.attr == "session_state")


def test_app_has_no_save_watchlist_and_uses_intent_only_writes():
    src = _APP.read_text(encoding="utf-8")
    assert "save_watchlist" not in src
    assert src.count("add_to_watchlist(") >= 4
    assert src.count("remove_from_watchlist(") >= 2


def test_app_never_feeds_the_whole_session_list_to_a_write():
    tree = parse_file_cached(_APP)
    names = {"add_to_watchlist", "remove_from_watchlist"}
    seen = 0
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in names):
            seen += 1
            assert node.args, f"line {node.lineno}: write called with no args"
            assert not _is_session_watchlist(node.args[0]), (
                f"line {node.lineno}: whole session watchlist passed to a write")
    assert seen >= 6
