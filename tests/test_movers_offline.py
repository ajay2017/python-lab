"""AST pins for ScanMoversEmptyCached (docs/bug-register.md). app.py is never
imported (no test does), so the wiring is pinned by AST (same style as
tests/test_sector_returns_offline.py):

  * `_scan_movers_ok` (30 min) calls `scanner.scan_movers_checked`, which RAISES on
    a failed read, so st.cache_data never stores a failure for 30 minutes;
  * `_cached_scan_movers` is the 5-minute wrapper that turns the failure into None;
  * Home publishes the three-state `_movers_checked` (False on a None result and in
    the surrounding except; popped when the block does not run), memoizes it in the
    `_home_synth_cache` bundle and restores it with a `True` default;
  * Grow Today counts the discovery universe only when `_movers_checked is True`
    (never `"_movers_candidates" in st.session_state`) and discloses the False case.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

_APP = Path(__file__).parent.parent / "app.py"
_TREE = parse_file_cached(_APP)

_KEY = "_movers_checked"


def _module_func(name: str) -> ast.FunctionDef:
    for n in _TREE.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} is not a module-level function in app.py")


def _cache_ttl(fn: ast.FunctionDef):
    for d in fn.decorator_list:
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr == "cache_data"):
            for kw in d.keywords:
                if kw.arg == "ttl" and isinstance(kw.value, ast.Constant):
                    return kw.value.value
    return None


def _call_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _is_state_key_target(t: ast.AST, key: str) -> bool:
    return (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
            and t.slice.value == key)


def _state_assigns(key: str) -> list[ast.Assign]:
    return [n for n in ast.walk(_TREE) if isinstance(n, ast.Assign)
            and any(_is_state_key_target(t, key) for t in n.targets)]


# -- the two cache layers -----------------------------------------------------

def test_scan_movers_ok_is_cached_for_30_minutes_and_uses_the_raising_scan():
    fn = _module_func("_scan_movers_ok")
    assert _cache_ttl(fn) == 1800
    calls = {_call_name(c) for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "scan_movers_checked" in calls
    assert "scan_movers" not in calls, "the best-effort wrapper would cache a failure"
    assert not any(isinstance(n, ast.Try) and n.handlers for n in ast.walk(fn)), \
        "a handler here would swallow the raise and cache a failure"


def test_cached_scan_movers_is_the_five_minute_none_wrapper():
    fn = _module_func("_cached_scan_movers")
    assert _cache_ttl(fn) == 300
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert tries
    assert any(
        isinstance(c, ast.Call) and _call_name(c) == "_scan_movers_ok"
        for t in tries for stmt in t.body for c in ast.walk(stmt)
    )
    handlers = [h for t in tries for h in t.handlers]
    assert handlers and all(
        isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in handlers)
    handler_returns = [r.value for h in handlers for r in ast.walk(h)
                       if isinstance(r, ast.Return)]
    assert handler_returns and all(
        isinstance(v, ast.Constant) and v.value is None for v in handler_returns)


def test_app_imports_the_checked_scan_not_the_best_effort_one():
    names = set()
    for n in _TREE.body:
        if isinstance(n, ast.ImportFrom) and n.module == "stock_analyzer.scanner":
            names |= {a.name for a in n.names}
    assert "scan_movers_checked" in names
    assert "scan_movers" not in names


# -- Home ---------------------------------------------------------------------

def test_home_publishes_checked_false_on_none_and_in_the_except():
    assigns = _state_assigns(_KEY)
    # one from the result (`_movers_df is not None`), one False in an except handler
    from_result = [a for a in assigns if isinstance(a.value, ast.Compare)
                   and "_movers_df is not None" in ast.unparse(a.value)]
    assert len(from_result) == 1, "None must publish False, a frame (even empty) True"
    in_except = []
    for h in (n for n in ast.walk(_TREE) if isinstance(n, ast.ExceptHandler)):
        for a in ast.walk(h):
            if (isinstance(a, ast.Assign) and any(_is_state_key_target(t, _KEY) for t in a.targets)
                    and isinstance(a.value, ast.Constant) and a.value.value is False):
                in_except.append(a)
    assert len(in_except) == 1


def test_home_pops_the_flag_when_the_movers_block_does_not_run():
    pops = [c for c in ast.walk(_TREE)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            and c.func.attr == "pop" and c.args
            and isinstance(c.args[0], ast.Constant) and c.args[0].value == _KEY]
    # the MISS-path `else` (block skipped) and the HIT restore of a None
    assert len(pops) >= 2


def test_candidates_list_is_never_published_as_none():
    for a in _state_assigns("_movers_candidates"):
        assert not (isinstance(a.value, ast.Constant) and a.value.value is None)


def test_memo_bundle_stores_movers_checked():
    dicts = [n for n in ast.walk(_TREE) if isinstance(n, ast.Dict)
             and any(isinstance(k, ast.Constant) and k.value == "_movers_candidates"
                     for k in n.keys)]
    assert len(dicts) == 1, "expected the single _home_synth_cache bundle dict"
    d = dicts[0]
    assert any(isinstance(k, ast.Constant) and k.value == _KEY for k in d.keys)


def test_hit_restore_reads_the_flag_with_a_true_default():
    found = []
    for n in ast.walk(_TREE):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "_b" and n.args
                and isinstance(n.args[0], ast.Constant) and n.args[0].value == _KEY):
            assert len(n.args) == 2
            assert isinstance(n.args[1], ast.Constant) and n.args[1].value is True
            found.append(n)
    assert len(found) == 1


# -- Grow Today ---------------------------------------------------------------

def test_funnel_no_longer_uses_in_as_liveness_for_movers():
    for n in ast.walk(_TREE):
        if (isinstance(n, ast.Compare) and isinstance(n.left, ast.Constant)
                and n.left.value == "_movers_candidates"
                and any(isinstance(op, (ast.In, ast.NotIn)) for op in n.ops)):
            raise AssertionError('"_movers_candidates" in st.session_state is a liveness test')
    assigns = [n for n in ast.walk(_TREE) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "_movers_ran" for t in n.targets)]
    assert len(assigns) == 1
    src = ast.unparse(assigns[0].value)
    assert _KEY in src and "is True" in src


def test_grow_today_discloses_a_failed_movers_scan():
    strings = {n.value for n in ast.walk(_TREE)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert (
        "⚠️ Breakout movers not checked: the price download for the discovery list "
        "failed, so no mover could be considered for today's picks. The Brief's next "
        "refresh retries it (🔄 Refresh Signals retries it at once)."
    ) in strings
    tests = [ast.unparse(n.test) for n in ast.walk(_TREE) if isinstance(n, ast.If)]
    assert any(_KEY in t and "is False" in t for t in tests)
