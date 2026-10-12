"""AST wiring test on app.py for audit A-M2: the 🏆 Health Score page must
pass the correlation sample (`_corr_coverage_cache`) and the missing-holdings
list (`_corr_unchecked_cache`) into compute_health_score.

app.py has no import-level coverage. If the `corr_coverage=` kwarg were
dropped, portfolio_health would read the sample as UNKNOWN and withhold the
diversification sub-score on EVERY visit -- safe, but silently killing the
dimension. Both ends are pinned: the kwarg name AND the session key it
reads, and that it reads them raw (no `or {}` laundering None into a value).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

APP_PY = Path(__file__).resolve().parent.parent / "app.py"


def _health_calls():
    tree = parse_file_cached(APP_PY)
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "compute_health_score"
    ]


def _is_raw_session_get(node, key):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "session_state"
        and node.args and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == key
        and len(node.args) == 1
    )


def test_exactly_one_health_score_call_site():
    assert len(_health_calls()) == 1


@pytest.mark.parametrize("kwarg, key", [
    ("corr_coverage", "_corr_coverage_cache"),
    ("corr_unchecked", "_corr_unchecked_cache"),
])
def test_health_score_reads_the_correlation_caches_raw(kwarg, key):
    (call,) = _health_calls()
    kws = {k.arg: k.value for k in call.keywords}
    assert kwarg in kws, f"compute_health_score is no longer passed {kwarg}="
    val = kws[kwarg]
    assert not isinstance(val, ast.BoolOp), f"{kwarg}= must not use `or` (sentinel collapse)"
    assert _is_raw_session_get(val, key), (
        f'{kwarg}= must be st.session_state.get("{key}") with no default'
    )
