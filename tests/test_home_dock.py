"""Bottom dock (docs/plans/home-dock.md, C0c): the pure CSS helper and AST pins on
app.py's wiring (app.py is never imported by a test).

C0c made the dock app-wide: one module-level ``with st.container(key=DOCK_KEY)``
in app.py, before page dispatch, holding the only ``_index_strip()`` call.
"""
import ast
from pathlib import Path

from stock_analyzer import home_dock as hd
from tests._ast_cache import parse_file_cached

_APP = Path(__file__).parent.parent / "app.py"
_SRC = _APP.read_text(encoding="utf-8")


def test_dock_css_shape_and_key():
    assert hd.DOCK_KEY == "home_dock"
    css = hd.dock_css()
    assert isinstance(css, str)
    assert css.startswith("<style>") and css.endswith("</style>")
    assert f".st-key-{hd.DOCK_KEY}" in css
    assert "#131722" in css and "1px solid #31333f" in css
    assert "max-height: 45vh" in css and "overflow: auto" in css


def test_sticky_css():
    css = hd.dock_css()
    assert "position: sticky" in css
    assert "order: 9999" in css
    assert "position: fixed" not in css
    # no blank band under the dock at the end of the page (C0 probe finding)
    assert '[data-testid="stMainBlockContainer"] { padding-bottom: 0; }' in css


def test_probe_switch_is_gone():
    for name in ("DOCK_MODES", "DEFAULT_DOCK_MODE", "dock_mode", "_FIXED_CSS"):
        assert not hasattr(hd, name), name


def _is_dock_container_call(node) -> bool:
    """`st.container(key=home_dock.DOCK_KEY)`."""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "container"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
    ):
        return False
    for kw in node.keywords:
        if (
            kw.arg == "key"
            and isinstance(kw.value, ast.Attribute)
            and kw.value.attr == "DOCK_KEY"
            and isinstance(kw.value.value, ast.Name)
            and kw.value.value.id == "home_dock"
        ):
            return True
    return False


def _is_index_strip_call(node) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_index_strip"
    )


def test_app_has_one_module_level_dock_container():
    tree = parse_file_cached(_APP)
    all_dock = [n for n in ast.walk(tree) if _is_dock_container_call(n)]
    assert len(all_dock) == 1, f"expected one dock container in app.py, found {len(all_dock)}"
    top_level = [
        stmt for stmt in tree.body
        if isinstance(stmt, ast.With)
        and any(_is_dock_container_call(item.context_expr) for item in stmt.items)
    ]
    assert len(top_level) == 1, (
        "the dock container must be a module-level `with`, not nested in a page branch"
    )


def test_app_has_one_index_strip_call_inside_the_dock():
    tree = parse_file_cached(_APP)
    calls = [n for n in ast.walk(tree) if _is_index_strip_call(n)]
    assert len(calls) == 1, f"expected exactly one _index_strip() call, found {len(calls)}"
    dock_with = next(
        stmt for stmt in tree.body
        if isinstance(stmt, ast.With)
        and any(_is_dock_container_call(item.context_expr) for item in stmt.items)
    )
    inside = [n for n in ast.walk(dock_with) if _is_index_strip_call(n)]
    assert inside == [calls[0]]


def test_app_has_no_dock_query_switch():
    assert 'st.query_params.get("dock")' not in _SRC
    assert "dock_mode" not in _SRC
