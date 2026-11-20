"""AST pins on 🏠 Home's Act Today wiring in app.py (app.py is never imported by a test).

C1c retired the one-line Act Today pointer and its dock tab; these are the
invariants that outlived `tests/test_act_today_pointer.py`:
1. exactly one `act_today_view()` call inside Home's branch (a second call site
   reopens the count-drift class act_today_view.py exists to close);
2. the full-book data-outage branch tells the owner, on the page itself, that
   Act Today was NOT checked, before its `st.stop()` (the no-holdings path
   says nothing of the kind: nothing failed there);
3. nothing in app.py or stock_analyzer/ still names the retired pointer.
"""
import ast
from pathlib import Path

import pytest

from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "app.py"
NOT_CHECKED = "Act Today was NOT checked"


def _home_if_node(tree: ast.Module) -> ast.If:
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "page"
            and len(node.test.ops) == 1
            and isinstance(node.test.ops[0], ast.Eq)
            and node.test.comparators
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value == "🏠 Home"
        ):
            return node
    raise AssertionError("couldn't find the top-level `if page == \"🏠 Home\":` block")


def _walk_body(body):
    """Walk only `body`'s own statements, not the If node's `orelse` chain (which
    holds every other page's elif branch)."""
    for stmt in body:
        yield from ast.walk(stmt)


def _is_act_today_view_call(n) -> bool:
    return (
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "act_today_view"
    )


def test_exactly_one_act_today_view_call_inside_home():
    home = _home_if_node(parse_file_cached(APP_PY))
    calls = [n for n in _walk_body(home.body) if _is_act_today_view_call(n)]
    assert len(calls) == 1, (
        f"expected exactly one act_today_view() call inside Home's render path, "
        f"found {len(calls)}"
    )


def _stmt_lists(tree):
    lists = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if isinstance(body, list) and body and all(isinstance(s, ast.stmt) for s in body):
                lists.append(body)
    return lists


def _is_st_call(stmt, attr) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Attribute)
        and isinstance(stmt.value.func.value, ast.Name)
        and stmt.value.func.value.id == "st"
        and stmt.value.func.attr == attr
    )


def _says_not_checked(stmt) -> bool:
    """A direct `st.error("... Act Today was NOT checked ...")` statement."""
    if not _is_st_call(stmt, "error"):
        return False
    return any(
        isinstance(n, ast.Constant) and isinstance(n.value, str) and NOT_CHECKED in n.value
        for n in ast.walk(stmt.value)
    )


def test_outage_branch_renders_an_in_page_not_checked_error_before_st_stop():
    home = _home_if_node(parse_file_cached(APP_PY))
    view_line = min(n.lineno for n in _walk_body(home.body) if _is_act_today_view_call(n))
    checked = 0
    for stmts in _stmt_lists(home):
        for i, stmt in enumerate(stmts):
            if not _is_st_call(stmt, "stop") or stmt.lineno >= view_line:
                continue
            if i == 0 or not isinstance(stmts[i - 1], ast.If):
                continue
            branch = stmts[i - 1]
            if "held_tickers" not in ast.dump(branch.test):
                continue
            checked += 1
            assert any(_says_not_checked(s) for s in branch.body), (
                f"the outage branch before the st.stop() at app.py:{stmt.lineno} has no "
                f"in-page st.error() saying {NOT_CHECKED!r}"
            )
            assert not any(
                _says_not_checked(s) for s in ast.walk(ast.Module(body=branch.orelse, type_ignores=[]))
                if isinstance(s, ast.stmt)
            ), "the no-holdings path must not claim Act Today was NOT checked"
    assert checked == 1, f"expected exactly one outage / no-holdings branch, found {checked}"


def test_no_stop_before_the_view_without_an_outage_message():
    """Every st.stop() reached before act_today_view() sits right after the
    outage / no-holdings if-else, so a new early stop can't skip the message."""
    home = _home_if_node(parse_file_cached(APP_PY))
    view_line = min(n.lineno for n in _walk_body(home.body) if _is_act_today_view_call(n))
    for stmts in _stmt_lists(home):
        for i, stmt in enumerate(stmts):
            if _is_st_call(stmt, "stop") and stmt.lineno < view_line:
                assert i > 0 and isinstance(stmts[i - 1], ast.If), (
                    f"st.stop() at app.py:{stmt.lineno} is not preceded by the outage "
                    "if/else, so the page could end with no 'not checked' message"
                )


def test_retired_pointer_is_gone():
    src = APP_PY.read_text(encoding="utf-8")
    for name in ("_act_ptr", "act_today_pointer", "act_pointer", "_act_pointer_mod"):
        assert name not in src, f"{name} still appears in app.py"
    assert not (ROOT / "stock_analyzer" / "act_today_pointer.py").exists()
    for p in (ROOT / "stock_analyzer").glob("*.py"):
        assert "act_today_pointer" not in p.read_text(encoding="utf-8"), p.name
