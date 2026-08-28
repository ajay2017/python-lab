"""Repo-hygiene gates that run as part of the normal pytest suite.

These exist so the deterministic pre-push gate (the `pytest` hook in
`.claude/hooks/pre_tool_checks.py`, plus CI) covers the checks that used to live
only in the `test-runner` agent's manual checklist — so that agent is no longer
a mandatory, token-costing per-change stage (see CLAUDE.md "Review & test
economy" and memory feedback_recurring_defect_gate / project_test_runner_agent).

Covered here:
- **py_compile of app.py / cron_runner.py** — the two scripts pytest never
  *imports* (app.py is a Streamlit entrypoint; importing it would boot the app),
  so a syntax error in them would otherwise slip past the whole suite and only
  surface at deploy. py_compile is syntax-only: it byte-compiles without
  executing, so it never triggers streamlit/network imports.
- **constants-doc drift** — every constant in constants.py is documented or
  allowlisted (the same invariant scripts/check_constants_documented.py enforces
  in CI, now also asserted locally via the suite).
"""
import importlib.util
import py_compile
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(script_name: str):
    path = ROOT / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(script_name[:-3], path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("script", ["app.py", "cron_runner.py"])
def test_entrypoint_scripts_compile(script):
    """Syntax gate for the scripts pytest never imports."""
    path = ROOT / script
    if not path.exists():
        pytest.skip(f"{script} not present")
    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as exc:
        pytest.fail(f"{script} failed to compile:\n{exc}")


def test_no_undocumented_constants():
    """Every constants.py constant is documented in docs/ or allowlisted."""
    ccd = _load("check_constants_documented.py")
    allow = ccd.load_allowlist()
    drift = [n for n in ccd.undocumented() if n not in allow]
    assert not drift, (
        "Undocumented constants (document in docs/architecture.md or add to "
        f"scripts/constants_doc_allowlist.txt): {drift}"
    )


# ── Anti-fork guard for the concentration-gate basis (F-260 Phase 0) ─────────
# `_acct_gate_cache` is the concentration GATE's denominator. It has exactly two
# producers — 🏠 Home and _refresh_portfolio_cache_after_trade — and both must
# call stock_analyzer.portfolio.gate_basis(). If either ever re-inlines its own
# literal, the basis the 15%/35% ceilings judge against becomes a function of
# WHICH PAGE YOU LAST VISITED, which is a real gate defect and an invisible one.
# A structural test is the only thing that catches a re-inline, because both
# paths calling one function makes any value-comparison test trivially true.

_APP_PY = Path(__file__).resolve().parents[1] / "app.py"


def _app_source() -> str:
    return _APP_PY.read_text(encoding="utf-8-sig")


def test_acct_gate_cache_has_exactly_the_two_sanctioned_producers():
    src = _app_source()
    # Regex, not literal string counting: the two call sites use different
    # alignment padding, and a space-sensitive count silently UNDER-reports
    # (the first draft of this test did exactly that and read 2 writers as 1).
    writes = len(re.findall(
        r'st\.session_state\["_acct_gate_cache"\]\s*=[^=]', src
    ))
    assert writes == 2, (
        f"expected exactly 2 writers of _acct_gate_cache (Home + the post-trade "
        f"republisher), found {writes}. A third producer of the concentration "
        f"gate's denominator must be reviewed, not added silently."
    )


def test_no_producer_reinlines_the_gate_basis_literal():
    """Both writers must delegate to portfolio.gate_basis(). The literal shape
    appearing anywhere in app.py means someone rebuilt it by hand."""
    src = _app_source()
    # Regex, not a quote-sensitive literal: a single-quoted or differently
    # spaced re-inline would slip straight past an exact-string check
    # (2026-08-28 review finding ④).
    assert not re.search(r"""['"]over_levered['"]\s*:\s*False""", src), (
        "app.py contains an inline _acct_gate_cache literal. Call "
        "stock_analyzer.portfolio.gate_basis(port_df) instead — two "
        "hand-written copies of a gate denominator WILL drift."
    )
    assert src.count("gate_basis(") >= 2, (
        "expected both _acct_gate_cache producers to call gate_basis()"
    )


def test_gate_weight_column_is_attached_by_the_shared_helper_only():
    """Same fork risk for the column the gate reads. Home used to assign it
    inline and the republisher omitted it entirely, so after any trade the
    column silently vanished and six consumers fell through to Weight (%)."""
    src = _app_source()
    assert 'port_df["Gate Weight (%)"] = ' not in src, (
        "app.py assigns Gate Weight (%) inline; use "
        "stock_analyzer.portfolio.attach_gate_weight(port_df)."
    )
    assert src.count("attach_gate_weight(") >= 2, (
        "both port_df producers (Home and the post-trade republisher) must "
        "attach the gate-weight column, or it disappears after a trade"
    )
