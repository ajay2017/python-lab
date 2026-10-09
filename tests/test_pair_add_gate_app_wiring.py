"""AST-based wiring tests on app.py for G-26 (owner decisions 2026-10-08,
pair_add_gate.py). app.py has no import-level test coverage, so these
structural checks are the only guard against a future edit silently dropping
pair_add_blocks= from one of the two build_daily_briefing() call sites, or
removing _pair_add_blocks from Home's _home_synth_cache bundle dict.
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


def test_every_build_daily_briefing_call_passes_pair_add_blocks():
    tree = _parse_app_py()
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "build_daily_briefing"
    ]
    assert len(calls) >= 1, "no build_daily_briefing( call sites found in app.py"
    missing = [
        c.lineno for c in calls
        if not any(kw.arg == "pair_add_blocks" for kw in c.keywords)
    ]
    assert missing == [], (
        f"build_daily_briefing( call(s) at line(s) {missing} don't pass "
        "pair_add_blocks= -- G-26 silently wouldn't apply on that build path."
    )


def test_home_synth_cache_bundle_contains_pair_add_blocks_key():
    """Find the dict literal that already carries "_cluster_add_blocks" (the
    _home_synth_cache bundle) and assert "_pair_add_blocks" is a sibling key
    in the SAME dict -- robust to line-number drift, unlike searching for an
    exact line number."""
    tree = _parse_app_py()
    target = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
        if "_cluster_add_blocks" in keys:
            target = node
            break
    assert target is not None, (
        "couldn't find the _home_synth_cache bundle dict literal "
        "(expected a dict containing key \"_cluster_add_blocks\")"
    )
    keys = [k.value for k in target.keys if isinstance(k, ast.Constant)]
    assert "_pair_add_blocks" in keys, (
        "_home_synth_cache's bundle dict is missing \"_pair_add_blocks\" -- "
        "a HIT-path render would restore G-25's map but never G-26's."
    )
