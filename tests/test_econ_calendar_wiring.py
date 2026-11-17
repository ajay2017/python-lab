"""Wiring tests for the 📅 Economic Calendar page's app.py render (CpiYoyGap +
CalLabels chunk C4). app.py is never imported (no test does) -- the wiring is
pinned by AST, in the style of tests/test_period_report_render.py.

The decisions themselves (is_done, the split, the KPI strip, the day tags, the
card value row) are unit-tested in tests/test_econ_calendar_view.py; this file
pins that the page CALLS them and no longer decides done-ness inline.
Plan: docs/plans/cpi-yoy-gap-spec.md (section 4, C4).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._ast_cache import parse_file_cached, source_segment

pytestmark = pytest.mark.fast

_APP = Path(__file__).parent.parent / "app.py"
_SRC = _APP.read_text(encoding="utf-8")
_TREE = parse_file_cached(_APP)

_LINES = _SRC.splitlines()
_START = next(i for i, ln in enumerate(_LINES, 1)
              if ln.startswith('elif page == "📅 Economic Calendar":'))
_END = next(i for i, ln in enumerate(_LINES, 1)
            if i > _START and ln.startswith("elif page == "))

_ECV = "_ecv"        # app.py's alias for stock_analyzer.econ_calendar_view


def _page_nodes(kind):
    return [n for n in ast.walk(_TREE)
            if isinstance(n, kind) and _START < getattr(n, "lineno", 0) < _END]


def _ecv_calls(attr):
    """`_ecv.<attr>(...)` calls inside the Economic Calendar page block."""
    return [n for n in _page_nodes(ast.Call)
            if isinstance(n.func, ast.Attribute) and n.func.attr == attr
            and isinstance(n.func.value, ast.Name) and n.func.value.id == _ECV]


def _kw(call, name):
    return next((k.value for k in call.keywords if k.arg == name), None)


def _is_ecv_call(node, attr):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == attr and isinstance(node.func.value, ast.Name)
            and node.func.value.id == _ECV)


# --- the module is imported under the alias the page uses ----------------------

def test_view_module_is_imported_as_ecv():
    assert any(isinstance(n, ast.ImportFrom) and n.module == "stock_analyzer"
               and any(a.name == "econ_calendar_view" and a.asname == _ECV for a in n.names)
               for n in _TREE.body)


# --- the page calls the pure decisions -----------------------------------------

@pytest.mark.parametrize("attr", [
    "split_events", "kpis", "upcoming_day_tag", "completed_day_tag",
    "fred_enriched_count", "tag_markdown",
])
def test_page_calls_view_helper(attr):
    assert _ecv_calls(attr), f"the Economic Calendar page no longer calls {_ECV}.{attr}"


def test_card_value_row_on_both_sides_with_explicit_done():
    calls = _ecv_calls("card_value_row")
    assert len(calls) == 2, "expected one card_value_row call per side (upcoming + completed)"
    for c in calls:
        done = _kw(c, "done")
        assert done is not None, "card_value_row must be given done= explicitly"
        assert _is_ecv_call(done, "is_done"), "done= must be the is_done decision"


def test_kpi_strip_reads_kpis_not_a_local_filter():
    names = {n.id for n in _page_nodes(ast.Name) if isinstance(n.ctx, ast.Store)}
    # The old inline KPI lists must be gone (high / week / next were derived from
    # a date >= today filter).
    assert not names & {"_ec_high", "_ec_week", "_ec_next"}
    k = _ecv_calls("kpis")[0]
    assert len(k.args) == 2


def test_split_events_replaces_the_inline_split():
    stores = {}
    for n in _page_nodes(ast.Assign):
        if _is_ecv_call(n.value, "split_events"):
            for t in n.targets:
                if isinstance(t, ast.Tuple):
                    stores = {e.id for e in t.elts if isinstance(e, ast.Name)}
    assert stores == {"_ec_upcoming", "_ec_past_rev"}


# --- done-ness is one decision everywhere on the page --------------------------

def test_release_status_badge_uses_is_done():
    fn = next(n for n in _page_nodes(ast.FunctionDef) if n.name == "_release_status_html")
    assert any(_is_ecv_call(n, "is_done") for n in ast.walk(fn)), (
        "the ⏳ Awaiting badge must not decide done-ness itself (same-day, before release time)")


def test_post_event_filter_uses_is_done():
    assigns = [n for n in _page_nodes(ast.Assign)
               if any(isinstance(t, ast.Name) and t.id == "_past_events" for t in n.targets)]
    assert len(assigns) == 1
    assert any(_is_ecv_call(n, "is_done") for n in ast.walk(assigns[0].value))


def _mentions_today(node):
    for x in ast.walk(node):
        if isinstance(x, ast.Name) and "today" in x.id.lower():
            return True
        if isinstance(x, ast.Attribute) and "today" in x.attr.lower():
            return True
    return False


# Compares that touch "today" on the page and are NOT a done-ness decision:
#  * `_ec_fwd = [... e["date"] >= _today_et()]` is the Pre-Event Playbook's input. It keeps a
#    released-today event on purpose: the playbook shows its own "was released" notice for it.
#  * the Post-Event TODAY badge compares the completed_day_tag string, a label, not done-ness
#    (every event in that loop already passed is_done).
_TODAY_COMPARE_ALLOWLIST = {
    'e["date"] >= _today_et()',
    '_ecv.completed_day_tag(_pe_date, _today_et()) == "today"',
}


def test_no_inline_today_comparison_decides_done_ness():
    found = {source_segment(_SRC, n) for n in _page_nodes(ast.Compare)
             if _mentions_today(n)}
    assert found <= _TODAY_COMPARE_ALLOWLIST, (
        f"a new date-vs-today comparison on the calendar page: {found - _TODAY_COMPARE_ALLOWLIST}; "
        "route it through econ_calendar_view.is_done")


# --- the regime dict's new keys are optional (a cached pre-deploy dict lacks them)

def test_cpi_month_and_basis_are_only_read_through_get():
    bad = [n.lineno for n in ast.walk(_TREE)
           if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
           and n.slice.value in ("cpi_yoy_month", "cpi_yoy_basis")]
    assert not bad, f"direct subscript of cpi_yoy_month / cpi_yoy_basis at app.py lines {bad}"
    gets = {n.args[0].value for n in ast.walk(_TREE)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
            and n.args and isinstance(n.args[0], ast.Constant)
            and n.args[0].value in ("cpi_yoy_month", "cpi_yoy_basis")}
    assert gets == {"cpi_yoy_month", "cpi_yoy_basis"}


def test_regime_banner_cpi_tag_branches_on_basis_without_a_threshold():
    # The tag is chosen by the basis string alone; no comparison against a number.
    seg = next(n for n in _page_nodes(ast.Assign)
               if any(isinstance(t, ast.Name) and t.id == "_regime_cpi_basis" for t in n.targets))
    assert seg is not None
    tag_assigns = [n for n in _page_nodes(ast.Assign)
                   if any(isinstance(t, ast.Name) and t.id == "_regime_cpi_tag" for t in n.targets)]
    assert len(tag_assigns) == 4          # unavailable / unknown / fallback / latest
