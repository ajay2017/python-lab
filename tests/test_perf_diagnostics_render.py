"""AST pins for the 📊 Diagnostics render (PerfWindowCollapse): bar colours come
from the Category compute_attribution assigned (no threshold comparison in
app.py), the call passes `left_out=`, and the vlines use the governed band."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

_TREE = parse_file_cached(Path(__file__).parent.parent / "app.py")


def _named_assign(name: str) -> list[ast.Assign]:
    return [n for n in ast.walk(_TREE) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]


def test_bar_colours_are_keyed_on_category_not_a_threshold():
    assigns = _named_assign("_bar_clrs")
    diag = [a for a in assigns if "_asc" in ast.unparse(a.value)]
    assert len(diag) == 1
    src = ast.unparse(diag[0].value)
    assert "Category" in src and "_CAT_CLRS" in src
    assert not any(isinstance(n, ast.Compare) for n in ast.walk(diag[0].value))
    cat = _named_assign("_CAT_CLRS")
    assert len(cat) == 1
    d = cat[0].value
    assert isinstance(d, ast.Dict)
    mapping = {k.value: v.value for k, v in zip(d.keys, d.values)}
    assert mapping == {
        "Alpha Generator": "#00C851", "Sector Rider": "#ffbb33",
        "Alpha Destroyer": "#ff4444", "In Line": "#888888",
    }


def test_no_v_comparison_against_the_band_remains():
    for n in ast.walk(_TREE):
        if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) and n.left.id == "v":
            assert "PERF_ALPHA_BAND_PCT" not in ast.unparse(n)


def test_compute_attribution_call_passes_left_out_list():
    calls = [n for n in ast.walk(_TREE) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "compute_attribution"]
    assert len(calls) == 1
    kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
    assert kw.get("left_out") == "_attr_left_out"


def test_vlines_use_the_governed_band():
    xs = []
    for n in ast.walk(_TREE):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "add_vline"
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "_attr_fig"):
            for k in n.keywords:
                if k.arg == "x":
                    xs.append(ast.unparse(k.value))
    assert "PERF_ALPHA_BAND_PCT" in xs and "-PERF_ALPHA_BAND_PCT" in xs


def test_skill_ratio_count_is_not_rebuilt_from_the_truncated_percent():
    assigns = _named_assign("_n_beating")
    assert len(assigns) == 1
    assert "Alpha vs SPY (%)" in ast.unparse(assigns[0].value)
    src = ast.unparse(_TREE)
    assert "int(_skill_pct / 100 * _n_total)" not in src
    assert "{_n_beating}/{_n_total} positions beating SPY" in src


def test_left_out_tail_names_what_the_waterfall_covers():
    strings = {n.value for n in ast.walk(_TREE)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert any("the P&L waterfall further down includes every position." in s
               for s in strings)
