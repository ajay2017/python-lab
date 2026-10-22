"""S-D C4: structural pins on app.py (no test imports it). Uses the shared
cached parse so the suite doesn't pay the ~15-90s uncached app.py parse."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

APP_PY = Path(__file__).resolve().parent.parent / "app.py"


def _calls(attr_owner: str, attr: str):
    tree = parse_file_cached(APP_PY)
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == attr and isinstance(n.func.value, ast.Name)
                and n.func.value.id == attr_owner):
            out.append(n)
    return out


def test_watchlist_calls_may_show_size_with_watchlist_surface():
    calls = _calls("_sizing_path", "may_show_size")
    assert any(c.args and isinstance(c.args[0], ast.Constant) and c.args[0].value == "watchlist"
               for c in calls)


def test_watchlist_calls_no_size_caption():
    assert _calls("_sizing_path", "watchlist_no_size_caption")


def test_analysis_calls_context_caption():
    assert _calls("_trade_caption", "context_caption")
