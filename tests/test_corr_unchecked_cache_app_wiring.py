"""AST-based wiring tests on app.py for the F-290 disclosure follow-on
(Commit 3 of 3, docs/plans/pair-add-gate.md correlation-verification work):
publishing `_corr_unchecked_cache` beside the existing `_corr_coverage_cache`
writes on both the Home memo-HIT and memo-MISS paths.

app.py has no import-level test coverage, so this structural check is the
only guard against a future edit silently dropping the publish from one of
the two paths -- which would leave 🔗 Risk Analysis / 🧾 Summary reading a
stale or absent value on whichever path forgot it.

`test_app_py_never_calls_correlation_unchecked_directly` in
tests/test_pair_add_gate_app_wiring.py already covers the "app.py must never
call portfolio.correlation_unchecked() directly" requirement -- not
duplicated here.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "app.py"


def _parse_app_py() -> ast.Module:
    src = APP_PY.read_text(encoding="utf-8-sig")
    return ast.parse(src, filename=str(APP_PY))


def _is_session_state_subscript(node: ast.expr, key: str) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "session_state"
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == key
    )


def test_corr_unchecked_cache_assigned_from_corr_unchecked_on_both_paths():
    """`st.session_state["_corr_unchecked_cache"] = _corr_unchecked` must
    appear (at least) twice -- once on the memo-HIT path (restoring from the
    memoized bundle) and once on the memo-MISS path (the freshly computed
    value) -- exactly mirroring `_corr_coverage_cache`'s own two write sites.
    A single site would mean one of the two paths silently never publishes.
    """
    tree = _parse_app_py()
    found = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and _is_session_state_subscript(n.targets[0], "_corr_unchecked_cache")
        and isinstance(n.value, ast.Name)
        and n.value.id == "_corr_unchecked"
    ]
    assert len(found) >= 2, (
        f'found {len(found)} site(s) assigning '
        'st.session_state["_corr_unchecked_cache"] = _corr_unchecked '
        f"(lines {[n.lineno for n in found]}) -- expected at least 2, one "
        "per HIT/MISS path, matching _corr_coverage_cache's own precedent."
    )


def test_corr_unchecked_cache_never_assigned_from_something_else():
    """Every assignment to `_corr_unchecked_cache` must come from the
    `_corr_unchecked` name -- never an `or []`/`or {}` collapse, which would
    launder the None/offline sentinel into a fabricated "checked, clean"
    reading (the exact bug class this project's antipattern gate exists to
    catch).
    """
    tree = _parse_app_py()
    all_assigns = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and _is_session_state_subscript(n.targets[0], "_corr_unchecked_cache")
    ]
    assert len(all_assigns) >= 2
    bad = [
        n.lineno for n in all_assigns
        if not (isinstance(n.value, ast.Name) and n.value.id == "_corr_unchecked")
    ]
    assert bad == [], (
        f'st.session_state["_corr_unchecked_cache"] assigned from something '
        f"other than the bare `_corr_unchecked` name at line(s) {bad}."
    )


def test_miss_path_local_assignment_is_a_bare_subscript():
    """`_corr_unchecked = _crb["corr_unchecked"]` must stay a bare subscript.

    Opus review 2026-10-09 mutation-tested the two assertions above and found
    ONE survivor: putting the collapse on the LOCAL assignment
    (`_corr_unchecked = _crb["corr_unchecked"] or []`) rather than on the
    session_state write. The bare `_corr_unchecked` name still reaches
    session_state, so both existing tests pass -- and `check_antipatterns`
    misses it too, because its sentinel rule matches a `.get(...)` CALL, not
    a subscript.

    That collapse would turn the None/offline sentinel into a fabricated
    "checked, clean" reading for BOTH the caption and the label qualifier,
    which is the whole contract this commit rests on. Pinned here because
    nothing else in the repo can see it.
    """
    tree = _parse_app_py()
    assigns = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "_corr_unchecked"
    ]
    assert len(assigns) >= 1, (
        "no `_corr_unchecked = ...` local assignment found in app.py -- the "
        "MISS path must derive it from the correlation bundle."
    )
    # Three legitimate shapes exist and all are fine: the HIT path's
    # `_b.get("_corr_unchecked")`, the MISS path's `_crb["corr_unchecked"]`,
    # and Risk Analysis's `st.session_state.get("_corr_unchecked_cache")`.
    # The property that actually matters is narrower than "which shape":
    # NONE of them may be a BoolOp, because `X or []` is what converts the
    # None sentinel into a fabricated clean reading.
    bad = [n.lineno for n in assigns if isinstance(n.value, ast.BoolOp)]
    assert bad == [], (
        f"`_corr_unchecked` assigned via an `or` fallback at line(s) {bad} "
        "-- that launders the None/offline sentinel into a fake "
        '"checked, clean" read for both the caption and the label qualifier.'
    )
