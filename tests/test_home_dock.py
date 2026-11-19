"""Home dock (docs/plans/home-dock.md, C0 probe): the pure mode / CSS helpers and a
light source pin on app.py's wiring (app.py is never imported by a test).

Checked in streamlit 1.57.0: ``st.query_params.get(key)`` returns a str (the LAST
item of a repeated key, "" for an empty list) or None; it never returns a list.
``dock_mode`` still tolerates a list, falling back to the default.
"""
from pathlib import Path

import pytest

from stock_analyzer import home_dock as hd

_SRC = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("raw, expected", [
    (None, "sticky"),
    ("", "sticky"),
    ("  STICKY ", "sticky"),
    ("sticky", "sticky"),
    ("fixed", "fixed"),
    ("Fixed", "fixed"),
    (" FIXED\n", "fixed"),
    (["fixed"], "sticky"),
    (3, "sticky"),
    ("<script>", "sticky"),
    ("fixed;color:red", "sticky"),
])
def test_dock_mode(raw, expected):
    assert hd.dock_mode(raw) == expected


def test_dock_mode_defaults_are_consistent():
    assert hd.DEFAULT_DOCK_MODE in hd.DOCK_MODES
    assert hd.DOCK_MODES == ("sticky", "fixed")


def test_dock_css_shape_and_key():
    assert hd.DOCK_KEY == "home_dock"
    for mode in hd.DOCK_MODES:
        css = hd.dock_css(mode)
        assert isinstance(css, str)
        assert css.startswith("<style>") and css.endswith("</style>")
        assert f".st-key-{hd.DOCK_KEY}" in css
        assert "#131722" in css and "1px solid #31333f" in css
        assert "max-height: 45vh" in css and "overflow: auto" in css


def test_sticky_css():
    css = hd.dock_css("sticky")
    assert "position: sticky" in css
    assert "order: 9999" in css
    assert "position: fixed" not in css
    assert "stMainBlockContainer" not in css


def test_fixed_css():
    css = hd.dock_css("fixed")
    assert "position: fixed" in css
    assert "stMainBlockContainer" in css
    assert "position: sticky" not in css
    assert "order: 9999" not in css


def test_unknown_mode_is_sticky_exactly():
    sticky = hd.dock_css("sticky")
    for odd in ("", None, "FIXED", "bogus", 3, ["fixed"]):
        assert hd.dock_css(odd) == sticky


def test_css_never_contains_the_input():
    hostile = "}</style><script>alert(1)</script>{"
    css = hd.dock_css(hostile)
    assert hostile not in css and "<script>" not in css
    assert css == hd.dock_css("sticky")


def test_app_wires_the_dock_and_guards_the_index_strip():
    assert "st.container(key=home_dock.DOCK_KEY)" in _SRC
    assert "home_dock.dock_css(_dock_mode)" in _SRC
    # The module-level strip call is skipped on Home (the dock shows it there).
    guarded = 'if page != "🏠 Home":\n    _index_strip()\n'
    assert guarded in _SRC
    # No other column-0 (module-level) call of the strip remains.
    top_level = [ln for ln in _SRC.splitlines() if ln.startswith("_index_strip()")]
    assert top_level == []
