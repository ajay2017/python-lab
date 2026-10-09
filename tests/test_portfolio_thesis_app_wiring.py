"""AST-based wiring test on app.py for the state-of-portfolio-thesis
correlation-coverage gate (Commit 1 of 3, docs/plans/
state-of-portfolio-standing-thesis.md correlation-trust follow-on).

app.py has no import-level test coverage, so this structural check is the
only guard against a future edit silently dropping "corr_unchecked"/
"corr_coverage" from the `_pth_bundle` dict literal -- which would make
`_classify_correlation`'s `correlation_claim_verified()` gate see a bundle
with those keys permanently missing, and every correlation_structure claim
would silently degrade to "unavailable" forever rather than its intended
verified/withheld split. Precedent: tests/test_pair_add_gate_app_wiring.py.
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


def _find_pth_bundle_dict(tree: ast.Module) -> ast.Dict | None:
    """Find the dict literal assigned to a name called `_pth_bundle` --
    robust to line-number drift, unlike searching for an exact line number."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "_pth_bundle" for t in node.targets):
            continue
        if isinstance(node.value, ast.Dict):
            return node.value
    return None


def test_pth_bundle_dict_literal_exists():
    tree = _parse_app_py()
    assert _find_pth_bundle_dict(tree) is not None, (
        "couldn't find a `_pth_bundle = {...}` dict-literal assignment in app.py"
    )


def test_pth_bundle_carries_corr_unchecked_from_sm_bundle():
    """Exactly `_sm_bundle.get("_corr_unchecked")` -- not a re-derived call,
    not a bare `or []`/`or {}` default (which would collapse the offline
    sentinel), not read from `st.session_state` directly."""
    tree = _parse_app_py()
    target = _find_pth_bundle_dict(tree)
    assert target is not None

    found = False
    for key_node, val_node in zip(target.keys, target.values):
        if not (isinstance(key_node, ast.Constant) and key_node.value == "corr_unchecked"):
            continue
        if (
            isinstance(val_node, ast.Call)
            and isinstance(val_node.func, ast.Attribute)
            and val_node.func.attr == "get"
            and isinstance(val_node.func.value, ast.Name)
            and val_node.func.value.id == "_sm_bundle"
            and any(
                isinstance(a, ast.Constant) and a.value == "_corr_unchecked"
                for a in val_node.args
            )
        ):
            found = True
    assert found, (
        '_pth_bundle is missing a "corr_unchecked": _sm_bundle.get("_corr_unchecked") '
        "entry -- correlation_claim_verified() would always see an unchecked list of "
        "None, and the correlation_structure claim would permanently degrade to "
        '"unavailable".'
    )


def test_pth_bundle_carries_corr_coverage_from_sm_bundle():
    """Exactly `_sm_bundle.get("corr_coverage")` -- same shape as above."""
    tree = _parse_app_py()
    target = _find_pth_bundle_dict(tree)
    assert target is not None

    found = False
    for key_node, val_node in zip(target.keys, target.values):
        if not (isinstance(key_node, ast.Constant) and key_node.value == "corr_coverage"):
            continue
        if (
            isinstance(val_node, ast.Call)
            and isinstance(val_node.func, ast.Attribute)
            and val_node.func.attr == "get"
            and isinstance(val_node.func.value, ast.Name)
            and val_node.func.value.id == "_sm_bundle"
            and any(
                isinstance(a, ast.Constant) and a.value == "corr_coverage"
                for a in val_node.args
            )
        ):
            found = True
    assert found, (
        '_pth_bundle is missing a "corr_coverage": _sm_bundle.get("corr_coverage") '
        "entry -- correlation_claim_verified() would always see coverage=None, and "
        'the correlation_structure claim would permanently degrade to "unavailable".'
    )


def test_pth_bundle_neither_key_uses_or_fallback():
    """Neither new key may be written as `_sm_bundle.get(...) or []` / `or {}`
    -- that would collapse the offline sentinel (None = couldn't check) into
    a value that reads as checked-and-clean, exactly the class of bug this
    whole follow-on exists to close."""
    tree = _parse_app_py()
    target = _find_pth_bundle_dict(tree)
    assert target is not None

    for key_node, val_node in zip(target.keys, target.values):
        if not (isinstance(key_node, ast.Constant) and key_node.value in (
            "corr_unchecked", "corr_coverage",
        )):
            continue
        assert not isinstance(val_node, ast.BoolOp), (
            f'_pth_bundle["{key_node.value}"] uses an `or` fallback -- this '
            "collapses the None offline sentinel into a fabricated clean value."
        )
