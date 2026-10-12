"""C1 -- every thin-sample decision routes through
portfolio.correlation_sample_state; nothing compares n_obs to
CORR_MIN_OBS_TRUSTED itself outside portfolio.py."""
from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# One parse per file, shared: app.py is ~44K lines and a single ast.parse
# measured 37.7s on 2026-10-12. Re-parsing per test pushed the pre-commit
# suite past its 450s timeout. Same fix as test_check_antipatterns.py:
# a module-level cache, plus an xdist_group so every test here lands on one
# worker and the cache is actually shared. Read-only use of the trees.
pytestmark = pytest.mark.xdist_group(name="corr_sample_state_wiring_ast")
_TREES: dict = {}


def _tree(rel):
    if rel not in _TREES:
        _TREES[rel] = ast.parse((_ROOT / rel).read_text(encoding="utf-8"))
    return _TREES[rel]


def _names(node):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def _floor_compares(tree):
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Compare) and "CORR_MIN_OBS_TRUSTED" in _names(n)]


@pytest.mark.parametrize("rel", [
    "stock_analyzer/pair_add_gate.py", "stock_analyzer/cluster_add_gate.py",
    "stock_analyzer/util.py", "app.py",
])
def test_no_compare_against_the_floor_outside_portfolio(rel):
    assert _floor_compares(_tree(rel)) == [], rel


def test_the_detector_sees_a_planted_compare():
    assert _floor_compares(ast.parse("x = n < CORR_MIN_OBS_TRUSTED")) == [1]
    assert _floor_compares(ast.parse("x = n < portfolio.CORR_MIN_OBS_TRUSTED")) == [1]
    assert _floor_compares(ast.parse("x = f(CORR_MIN_OBS_TRUSTED)")) == []


def _assigned_from_state(tree, local):
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == local for t in n.targets):
            if isinstance(n.value, ast.Call) and "correlation_sample_state" in _names(n.value.func):
                return True
    return False


def _is_eq_test(test, local, const):
    """`test` is exactly `<local> == <const>` -- one Eq, both sides bare Names.
    Pinning the STATE, not just the variable: an If that merely mentions
    `local` let thin/unknown be swapped, or `== UNKNOWN` widened to
    `!= TRUSTED`, with the wiring test still green (C1 review, blocking)."""
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name) and test.left.id == local
        and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Name) and test.comparators[0].id == const
    )


def _guarded_by(tree, local, needle, const):
    """True if a string constant containing `needle` sits in the BODY of an
    If/elif whose test is exactly `<local> == <const>`."""
    for n in ast.walk(tree):
        if isinstance(n, ast.If) and _is_eq_test(n.test, local, const):
            for sub in n.body:
                for c in ast.walk(sub):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) and needle in c.value:
                        return True
    return False


def test_the_guard_detector_pins_the_state_not_just_the_name():
    src = 'if s == THIN:\n    x = "warn thin"\nelif s != TRUSTED:\n    x = "warn unknown"\n'
    t = ast.parse(src)
    assert _guarded_by(t, "s", "warn thin", "THIN")
    assert not _guarded_by(t, "s", "warn thin", "UNKNOWN"), "wrong state must not match"
    assert not _guarded_by(t, "s", "warn unknown", "UNKNOWN"), "a widened != test must not match"
    assert not _guarded_by(t, "other", "warn thin", "THIN"), "wrong local must not match"


@pytest.mark.parametrize("needle,const", [
    ("observations this app requires", "CORR_SAMPLE_THIN"),
    ("is **unknown**", "CORR_SAMPLE_UNKNOWN"),
])
def test_app_risk_analysis_block_pinned_both_ends(needle, const):
    t = _tree("app.py")
    assert _assigned_from_state(t, "_corr_sample")
    assert _guarded_by(t, "_corr_sample", needle, const)


@pytest.mark.parametrize("needle,const", [
    ("too thin to trust", "CORR_SAMPLE_THIN"),
    ("sample size behind it is unknown", "CORR_SAMPLE_UNKNOWN"),
])
def test_app_f245_line_pinned_both_ends(needle, const):
    t = _tree("app.py")
    assert _assigned_from_state(t, "_fs_sample")
    assert _guarded_by(t, "_fs_sample", needle, const)
