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
