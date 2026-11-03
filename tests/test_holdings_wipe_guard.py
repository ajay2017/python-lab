"""HoldWipe: a failed journal read must never become an all-rows holdings /
manual_stops sweep.

Three layers are pinned:
  1. `db.save_holdings` refuses an empty frame unless `allow_empty=True`.
  2. `util.journal_read_failed_msg` wording.
  3. app.py (no test imports it, so source/AST pins): BOTH ends of each rebuild
     site -- the read (`load_trades_or_none`, never the lenient `load_trades`)
     AND the save (`allow_empty=` passed on every save fed by a replay).
"""
import ast
import re
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import db, journal_rebuild
from tests._ast_cache import parse_file_cached
from stock_analyzer.util import journal_read_failed_msg

_APP = Path(__file__).resolve().parents[1] / "app.py"


# ── 1. db.save_holdings guard ───────────────────────────────────────────────

class _Rec:
    """Records every table() call and every builder method invoked on it."""
    def __init__(self, log, table):
        self._log, self._table = log, table

    def _rec(self, op, *a):
        self._log.append((self._table, op, a))
        return self

    def upsert(self, records, on_conflict=None): return self._rec("upsert", list(records))
    def delete(self): return self._rec("delete")
    def neq(self, *a, **k): return self._rec("neq", *a)
    def in_(self, *a, **k): return self._rec("in_", *a)

    @property
    def not_(self): return self

    def execute(self): return None


class _FakeClient:
    def __init__(self):
        self.log: list = []
        self.table_calls: list = []

    def table(self, name):
        self.table_calls.append(name)
        return _Rec(self.log, name)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    c = _FakeClient()
    monkeypatch.setattr(db, "_client", lambda: c)
    return c


_COLS = ["Ticker", "Shares", "Avg Cost ($)", "Asset Type"]


def test_empty_frame_refused_with_no_db_call(fake):
    assert db.save_holdings(pd.DataFrame(columns=_COLS)) is False
    assert fake.table_calls == []


def test_all_invalid_rows_refused_with_no_db_call(fake):
    df = pd.DataFrame([{"Ticker": "AAPL", "Shares": 0, "Avg Cost ($)": 10.0,
                        "Asset Type": "stock"}])
    assert db.save_holdings(df) is False
    assert fake.table_calls == []


def test_allow_empty_sweeps_holdings_and_manual_stops(fake):
    assert db.save_holdings(pd.DataFrame(columns=_COLS), allow_empty=True) is True
    swept = {(t, op) for t, op, _ in fake.log if op in ("delete", "neq")}
    assert ("holdings", "delete") in swept and ("holdings", "neq") in swept
    assert ("manual_stops", "delete") in swept and ("manual_stops", "neq") in swept
    assert not [1 for _, op, _ in fake.log if op == "upsert"]


def test_non_empty_frame_upserts_and_sweeps_not_in(fake):
    df = pd.DataFrame([{"Ticker": "AAPL", "Shares": 5.0, "Avg Cost ($)": 100.0,
                        "Asset Type": "stock"}])
    assert db.save_holdings(df) is True
    ops = [(t, op) for t, op, _ in fake.log]
    assert ("holdings", "upsert") in ops
    assert ("holdings", "in_") in ops and ("manual_stops", "in_") in ops
    assert ("holdings", "neq") not in ops and ("manual_stops", "neq") not in ops


# ── 2. util message ─────────────────────────────────────────────────────────

def test_journal_read_failed_msg_content():
    m = journal_read_failed_msg("Trade(s) deleted")
    assert "Trade(s) deleted" in m
    assert "NOT rebuilt" in m and "manual stops" in m
    assert "Run rebuild now" in m and "Fix drift" in m
    assert "$" not in m and "**" not in m


# ── 3. app.py pins (both ends) ──────────────────────────────────────────────

def _tree():
    return parse_file_cached(_APP)


def _is_db_call(node, name):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == name
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "db")


def _assigned_name(node):
    if isinstance(node, ast.Assign) and len(node.targets) == 1 \
            and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return None


_REBUILD_VARS = ("fresh_trades", "fresh", "_bi_fresh", "_ss_fresh")


def test_rebuild_sites_read_strictly():
    """READ end: the four rebuild sites assign from load_trades_or_none()."""
    tree = _tree()
    seen = {}
    for node in ast.walk(tree):
        nm = _assigned_name(node)
        if nm in _REBUILD_VARS and _is_db_call(node.value, "load_trades_or_none"):
            seen[nm] = True
        if nm in _REBUILD_VARS and _is_db_call(node.value, "load_trades"):
            pytest.fail(f"{nm} is read via lenient db.load_trades() (line {node.lineno})")
    assert set(seen) == set(_REBUILD_VARS), f"missing strict reads: {set(_REBUILD_VARS) - set(seen)}"


def _allow_empty_kw(call):
    for k in call.keywords:
        if k.arg == "allow_empty":
            return k.value
    return None


def test_replay_fed_saves_bind_allow_empty_to_the_decision():
    """SAVE end: every db.save_holdings fed by a recalculate_from_trades result
    passes allow_empty= as an expression (the decide() result), never the
    constant True; the replay input is never a direct lenient read."""
    tree = _tree()
    recalc_vars, recalc_holdings_vars = set(), set()
    for node in ast.walk(tree):
        nm = _assigned_name(node)
        if nm and _is_db_call(node.value, "recalculate_from_trades"):
            recalc_vars.add(nm)
            arg = node.value.args[0] if node.value.args else None
            assert not (arg is not None and _is_db_call(arg, "load_trades")), (
                f"recalculate_from_trades fed by db.load_trades() (line {node.lineno})")
    for node in ast.walk(tree):
        nm = _assigned_name(node)
        if nm and isinstance(node.value, ast.Subscript)                 and isinstance(node.value.value, ast.Name)                 and node.value.value.id in recalc_vars:
            recalc_holdings_vars.add(nm)

    def _from_recalc(arg):
        if isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name):
            return arg.value.id in recalc_vars
        return isinstance(arg, ast.Name) and arg.id in recalc_holdings_vars

    checked = 0
    for node in ast.walk(tree):
        if _is_db_call(node, "save_holdings") and node.args and _from_recalc(node.args[0]):
            checked += 1
            kw = _allow_empty_kw(node)
            assert kw is not None and not isinstance(kw, ast.Constant), (
                f"replay-fed save_holdings must bind allow_empty to decide() (line {node.lineno})")
            assert isinstance(kw, ast.Subscript) and isinstance(kw.value, ast.Name)                 and kw.value.id.endswith("decision"), (
                f"allow_empty is not a decide() result (line {node.lineno})")
    assert checked == 4, f"expected 4 replay-fed saves, found {checked}"


def test_each_rebuild_site_calls_decide_with_the_right_inserted_flag():
    tree = _tree()
    flags = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)                 and node.func.id == "_journal_rebuild_decide":
            kw = {k.arg: k.value for k in node.keywords}
            assert isinstance(kw["rows_just_inserted"], ast.Constant)
            flags.append((node.args[0].id, kw["rows_just_inserted"].value))
    assert sorted(flags) == sorted([
        ("fresh_trades", False), ("fresh", False), ("_bi_fresh", True), ("_ss_fresh", True),
    ])


def test_sell_confirm_allow_empty_is_not_a_constant_true():
    tree = _tree()
    found = 0
    for node in ast.walk(tree):
        if _is_db_call(node, "save_holdings") and node.args                 and isinstance(node.args[0], ast.Name) and node.args[0].id == "_ps_h_df":
            found += 1
            kw = _allow_empty_kw(node)
            assert kw is not None and not isinstance(kw, ast.Constant)
    assert found == 1
    src = _APP.read_text(encoding="utf-8")
    assert re.search(r"_ps_allow_empty\s*=\s*_ps_h_df\.empty", src)


# ── 4. journal_rebuild.decide ───────────────────────────────────────────────

_ROW = pd.DataFrame([{"id": 1, "ticker": "AAPL"}])
_NOROWS = pd.DataFrame(columns=["id", "ticker"])


@pytest.mark.parametrize("fresh,inserted,action,allow", [
    (None, False, "unreadable", False),
    (None, True, "unreadable", False),
    (_NOROWS, True, "unreadable", False),
    (_NOROWS, False, "empty", False),
    (pd.DataFrame(), False, "empty", False),
    (pd.DataFrame(), True, "unreadable", False),
    (_ROW, False, "rebuild", True),
    (_ROW, True, "rebuild", True),
])
def test_decide(fresh, inserted, action, allow):
    d = journal_rebuild.decide(fresh, rows_just_inserted=inserted)
    assert d == {"action": action, "allow_empty": allow}
