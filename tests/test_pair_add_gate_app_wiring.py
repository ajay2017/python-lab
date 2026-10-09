"""AST-based wiring tests on app.py for G-26 (owner decisions 2026-10-08,
pair_add_gate.py). app.py has no import-level test coverage, so these
structural checks are the only guard against a future edit silently dropping
pair_add_blocks= from one of the two build_daily_briefing() call sites, or
removing _pair_add_blocks from Home's _home_synth_cache bundle dict.

Also covers the disclosure-only G-25/G-26 follow-on (docs/plans/
pair-add-gate.md): every build_daily_briefing() call must pass
corr_unchecked=, build_correlation_bundle() must pass held_tickers=, the
_home_synth_cache bundle must carry _corr_unchecked alongside
_cluster_add_blocks, and app.py must never call
portfolio.correlation_unchecked() directly -- it must be derived exactly
once (inside build_correlation_bundle) so every render site reads the SAME
computed value rather than risking a second, possibly-divergent call.
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
    assert "_corr_unchecked" in keys, (
        "_home_synth_cache's bundle dict is missing \"_corr_unchecked\" -- "
        "a HIT-path render would restore G-25/G-26's block maps but never "
        "the disclosure-only correlation-unchecked follow-on."
    )


def test_every_build_daily_briefing_call_passes_corr_unchecked():
    tree = _parse_app_py()
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "build_daily_briefing"
    ]
    assert len(calls) >= 1, "no build_daily_briefing( call sites found in app.py"
    missing = [
        c.lineno for c in calls
        if not any(kw.arg == "corr_unchecked" for kw in c.keywords)
    ]
    assert missing == [], (
        f"build_daily_briefing( call(s) at line(s) {missing} don't pass "
        "corr_unchecked= -- the correlation-unchecked disclosure caption "
        "would silently never populate on that build path."
    )


def test_build_correlation_bundle_call_passes_held_tickers():
    tree = _parse_app_py()
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "build_correlation_bundle")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "build_correlation_bundle")
        )
    ]
    assert len(calls) >= 1, "no build_correlation_bundle( call sites found in app.py"
    missing = [
        c.lineno for c in calls
        if not any(kw.arg == "held_tickers" for kw in c.keywords)
    ]
    assert missing == [], (
        f"build_correlation_bundle( call(s) at line(s) {missing} don't pass "
        "held_tickers= -- corr_unchecked would always resolve to None on "
        "that build path."
    )


def test_app_py_never_calls_correlation_unchecked_directly():
    """correlation_unchecked() must be derived exactly once, inside
    build_correlation_bundle -- a direct app.py call site would risk a second,
    possibly-divergent computation of "which held tickers are unpriced"."""
    tree = _parse_app_py()
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "correlation_unchecked")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "correlation_unchecked")
        )
    ]
    assert calls == [], (
        f"app.py calls correlation_unchecked() directly at line(s) "
        f"{[c.lineno for c in calls]} -- it must be derived exactly once, "
        "inside build_correlation_bundle, not re-derived at a render site."
    )


def test_home_synth_cache_hit_path_restores_corr_unchecked():
    """The cache-HIT branch must assign _corr_unchecked from the bundle.

    Opus review, 2026-10-09: the other AST tests here pin the bundle-dict KEY
    and the call-site kwargs, but nothing pinned the RESTORE. Deleting that
    one line leaves `_corr_unchecked` undefined on every cache hit, which is
    a NameError on the composite-freshness rebuild and the render path -- a
    crash, not a silently-missing caption, and invisible to every other test
    here because `app.py` is never imported by the suite.
    """
    tree = _parse_app_py()
    found = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "_corr_unchecked" for t in n.targets
        )
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Attribute)
        and n.value.func.attr == "get"
        and any(
            isinstance(a, ast.Constant) and a.value == "_corr_unchecked"
            for a in n.value.args
        )
    ]
    assert found, (
        "app.py's _home_synth_cache HIT path no longer restores "
        '_corr_unchecked via .get("_corr_unchecked") -- on a cache hit the '
        "name would be undefined (NameError on render), not merely absent."
    )
