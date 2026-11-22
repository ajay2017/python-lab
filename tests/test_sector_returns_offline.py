"""AST pins for SectorRetsEmptyCached (docs/bug-register.md §A). app.py is never
imported (no test does), so the wiring is pinned by AST:

  * `_sector_returns_ok` raises on a failed / empty read (never `return pd.DataFrame()`),
    so st.cache_data never stores a failure for the hour;
  * `_fetch_sector_returns` turns the failure into None, cached 5 minutes only;
  * Home's market context carries `leading_checked` (False on both fallback dicts) and
    `_leading_sectors_cache` is published as None when the read failed;
  * the market card reads the flag with a `True` default (an old bundle reads "checked");
  * Performance Diagnostics tests for None before touching the frame.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

_APP = Path(__file__).parent.parent / "app.py"
_TREE = parse_file_cached(_APP)


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


def _str_keys(d: ast.Dict) -> set:
    return {k.value for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _dicts_with_key(key: str) -> list[ast.Dict]:
    return [n for n in ast.walk(_TREE) if isinstance(n, ast.Dict) and key in _str_keys(n)]


def _is_empty_frame_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "DataFrame" and not node.args and not node.keywords)


# -- the two cache layers -----------------------------------------------------

def test_sector_returns_ok_is_cached_for_an_hour_and_raises():
    fn = _module_func("_sector_returns_ok")
    assert _cache_ttl(fn) == 3600
    assert any(isinstance(n, ast.Raise) for n in ast.walk(fn))
    assert not any(isinstance(n, ast.Try) and n.handlers
                   and any(isinstance(s, ast.Return) for h in n.handlers for s in ast.walk(h))
                   for n in ast.walk(fn)), "a handler that returns would cache a failure"
    for r in (n for n in ast.walk(fn) if isinstance(n, ast.Return)):
        assert not _is_empty_frame_call(r.value), "return pd.DataFrame() caches a failure"


def test_fetch_sector_returns_is_the_five_minute_none_wrapper():
    fn = _module_func("_fetch_sector_returns")
    assert _cache_ttl(fn) == 300
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert tries
    assert any(
        isinstance(c, ast.Call) and _call_name(c) == "_sector_returns_ok"
        for t in tries for stmt in t.body for c in ast.walk(stmt)
    )
    handler_returns = [r.value for t in tries for h in t.handlers
                       for r in ast.walk(h) if isinstance(r, ast.Return)]
    assert handler_returns and all(
        isinstance(v, ast.Constant) and v.value is None for v in handler_returns)


# -- Home ---------------------------------------------------------------------

def test_market_context_carries_leading_checked_on_every_dict():
    ctxs = [d for d in _dicts_with_key("leading_sectors") if "tone_available" in _str_keys(d)]
    assert len(ctxs) == 3, "expected the success dict and both fallback dicts"
    fallbacks = 0
    for d in ctxs:
        assert "leading_checked" in _str_keys(d), ast.unparse(d)[:80]
        val = next(v for k, v in zip(d.keys, d.values)
                   if isinstance(k, ast.Constant) and k.value == "leading_checked")
        if isinstance(val, ast.Constant):
            assert val.value is False
            fallbacks += 1
        else:
            assert isinstance(val, ast.Name) and val.id == "_lead_checked"
    assert fallbacks == 2


def test_home_checks_the_frame_is_not_none_before_reading_it():
    assigns = [n for n in ast.walk(_TREE)
               if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "_lead_checked" for t in n.targets)]
    assert len(assigns) == 1
    src = ast.unparse(assigns[0].value)
    assert "_sect_df_ctx is not None" in src
    assert "'1W' in _sect_df_ctx.columns" in src


def test_leading_sectors_cache_can_publish_none_on_the_success_path():
    found = []
    for n in ast.walk(_TREE):
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.IfExp)
                and any(isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                        and t.slice.value == "_leading_sectors_cache" for t in n.targets)):
            found.append(n.value)
    assert len(found) == 1
    ife = found[0]
    assert isinstance(ife.orelse, ast.Constant) and ife.orelse.value is None
    assert "leading_checked" in ast.unparse(ife.test)
    assert "True" in ast.unparse(ife.test), "an old bundle (no key) must read as checked"


def test_market_card_reads_leading_checked_with_a_true_default():
    srcs = []
    for n in ast.walk(_TREE):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and n.args
                and isinstance(n.args[0], ast.Constant) and n.args[0].value == "leading_checked"
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "_market_context"):
            assert len(n.args) == 2
            assert isinstance(n.args[1], ast.Constant) and n.args[1].value is True
            srcs.append(n)
    assert len(srcs) >= 2, "the card and the cache publish must both read the flag"
    strings = {n.value for n in ast.walk(_TREE) if isinstance(n, ast.Constant)
               and isinstance(n.value, str)}
    assert " · Leading sectors not checked (sector prices unavailable; the Brief's next refresh retries)" in strings


# -- Performance Diagnostics --------------------------------------------------

def test_performance_diagnostics_tests_for_none():
    assigns = [n for n in ast.walk(_TREE)
               if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
               and _call_name(n.value) == "_fetch_sector_returns"
               and any(isinstance(t, ast.Name) and t.id == "_sect_df" for t in n.targets)]
    assert len(assigns) == 1
    tests = [ast.unparse(n.test) for n in ast.walk(_TREE) if isinstance(n, ast.If)]
    assert any("_sect_df is None" in t for t in tests)
    assert any("_sect_df is not None" in t and "_sect_df.empty" in t for t in tests)
