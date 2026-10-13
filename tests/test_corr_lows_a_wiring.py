"""Wiring for audit 2026-10-11 Low-A (fixed 2026-10-13). app.py can't be
imported, so each producer->consumer link is pinned from the AST, both ends
(feedback_ast_test_pins_one_end):

  * Hidden Same-Bet: classify_clusters gets correlation_verified=<name>, and
    that name is assigned from correlation_claim_verified(...).
  * Structural banner: resolve_new_clusters gets corr_coverage=_corr_cov.
  * F-230: regime_delta_withhold_reason gets (_cus_delta, _pi_cov), and its
    result is what the render branches on.
"""
import ast
from pathlib import Path

import pytest

from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

APP_PY = Path(__file__).resolve().parent.parent / "app.py"


def _tree():
    return parse_file_cached(APP_PY)


def _calls(tree, attr):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == attr]


def _kw(call):
    return {k.arg: k.value for k in call.keywords}


def test_hidden_same_bet_passes_a_real_verification():
    tree = _tree()
    calls = _calls(tree, "classify_clusters")
    assert len(calls) == 1
    v = _kw(calls[0]).get("correlation_verified")
    assert isinstance(v, ast.Name), "must be a variable, never a constant True"
    sources = [n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == v.id for t in n.targets)]
    calls_in = [s for s in sources if isinstance(s, ast.Call) and isinstance(s.func, ast.Name)
                and s.func.id == "correlation_claim_verified"]
    assert calls_in, f"{v.id} must be assigned from correlation_claim_verified(...)"
    # ...on the SAME inputs the Structural Scan's own A-H1 gate reads.
    for c in calls_in:
        assert [a.id for a in c.args if isinstance(a, ast.Name)] == ["_ss_unchecked", "_ss_cov"]
    # The only other assignment allowed is the conservative default False.
    others = [s for s in sources if s not in calls_in]
    assert all(isinstance(s, ast.Constant) and s.value is False for s in others)


def test_structural_resolver_gets_the_same_matrix_coverage():
    calls = _calls(_tree(), "resolve_new_clusters")
    assert len(calls) == 1
    cov = _kw(calls[0]).get("corr_coverage")
    assert isinstance(cov, ast.Name) and cov.id == "_corr_cov"
    # ...and _corr_cov is unpacked from the correlation bundle's own
    # "corr_coverage" (the SAME matrix the resolver reads), position-matched.
    tree = _tree()
    found = False
    for n in ast.walk(tree):
        if (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Tuple)
                and isinstance(n.value, ast.Tuple)):
            names = [t.id if isinstance(t, ast.Name) else None for t in n.targets[0].elts]
            if "_corr_cov" in names:
                v = n.value.elts[names.index("_corr_cov")]
                assert isinstance(v, ast.Subscript) and isinstance(v.slice, ast.Constant)                     and v.slice.value == "corr_coverage"
                found = True
    assert found, "_corr_cov must come from the correlation bundle's corr_coverage"


def test_f230_withhold_reason_reads_both_legs_and_drives_the_render():
    tree = _tree()
    calls = _calls(tree, "regime_delta_withhold_reason")
    assert len(calls) == 1
    args = calls[0].args
    assert [a.id for a in args if isinstance(a, ast.Name)] == ["_cus_delta", "_pi_cov"]
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and n.value is calls[0]]
    assert len(assigns) == 1 and assigns[0].targets[0].id == "_cus_why"
    reads = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)
             and isinstance(n.left, ast.Name) and n.left.id == "_cus_why"]
    assert reads, "the render must branch on _cus_why"
    # The comparison renders ONLY in the else of `_cus_why is not None`.
    guard = [n for n in ast.walk(tree) if isinstance(n, ast.If)
             and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name)
             and n.test.left.id == "_cus_why" and isinstance(n.test.ops[0], ast.IsNot)
             and isinstance(n.test.comparators[0], ast.Constant)
             and n.test.comparators[0].value is None]
    assert len(guard) == 1 and guard[0].orelse, "comparison must sit in the else branch"
    renders = [c for c in ast.walk(ast.Module(body=guard[0].orelse, type_ignores=[]))
               if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
               and c.func.attr == "markdown"]
    assert renders, "the else branch must be the comparison render"
