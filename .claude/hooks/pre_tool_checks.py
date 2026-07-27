#!/usr/bin/env python3
"""Pre-tool-use hook: mechanically enforce hard rules #3 and #4 from CLAUDE.md,
plus a regression-test gate on `git commit`/`git push` (docs/testing-strategy.md).
"""
import json
import os
import re
import subprocess
import sys


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    # Hard rule #3: Never run the app locally
    if re.search(r"\bstreamlit\s+run\b", command, re.IGNORECASE):
        print(
            "BLOCKED (Hard rule #3): App runs on Streamlit Cloud only.\n"
            "Push to `main` and wait ~2 min for auto-redeploy, then hard-refresh (Ctrl+F5).",
            file=sys.stderr,
        )
        sys.exit(2)

    is_commit = bool(re.search(r"\bgit\s+commit\b", command))
    is_push = bool(re.search(r"\bgit\s+push\b", command))

    # Hard rule #4: Commits touching gate files require an Opus review citation
    if is_commit:
        staged = _get_staged_files()
        triggered = _gate_files_staged(staged)

        if triggered:
            if not _has_review_citation(command):
                print(
                    f"BLOCKED (Hard rule #4): Staged gate file(s) {triggered} require an Opus review before commit.\n"
                    "Add 'Review = Opus reviewer: SHIP/FIX-FIRST, N blocking; ...' to the commit body.\n"
                    "Invoke the `reviewer` subagent first, then cite its verdict.",
                    file=sys.stderr,
                )
                sys.exit(2)

        # Regression-test gate (docs/testing-strategy.md): block the commit if
        # `pytest tests/` fails, scoped to commits that actually touch tested
        # code so an unrelated docs-only commit isn't slowed down. A missing/
        # broken test environment does NOT block -- that's an infra gap, not a
        # code problem -- it just warns.
        if _touches_tested_code(staged):
            _gate_on_pytest("commit", "committing")

    # Always re-check before push, regardless of which files are in the
    # commits being pushed -- push sends whatever HEAD currently is, so one
    # suite run against the working tree covers it. Catches the case where a
    # commit landed before this gate existed, or from another session/tool.
    if is_push:
        _gate_on_pytest("push", "pushing")

    sys.exit(0)


def _gate_on_pytest(noun: str, gerund: str) -> None:
    ok, detail = _run_pytest()
    if ok is False:
        print(
            f"BLOCKED (regression suite failing): `pytest tests/` did not pass.\n"
            f"{detail}\n"
            f"Fix the failure (or update the test if this is a deliberate policy "
            f"change) before {gerund}.",
            file=sys.stderr,
        )
        sys.exit(2)
    elif ok is None:
        print(f"WARNING: could not run the regression suite ({detail}) -- not blocking {noun}.", file=sys.stderr)


def _get_staged_files() -> list[str]:
    try:
        r = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip().splitlines() if r.returncode == 0 else []
    except Exception:
        return []


# Files whose presence in a commit requires an Opus review citation
_GATE_FILES = {
    "stock_analyzer/constants.py",
    "stock_analyzer/risk_advisor.py",
    "stock_analyzer/exit_advisor.py",
    "stock_analyzer/daily_briefing.py",
    "stock_analyzer/portfolio.py",
}


def _gate_files_staged(staged: list[str]) -> list[str]:
    return [f for f in staged if f in _GATE_FILES]


def _touches_tested_code(staged: list[str]) -> bool:
    return any(f.startswith("stock_analyzer/") or f.startswith("tests/") for f in staged)


def _has_review_citation(command: str) -> bool:
    if re.search(r"Review\s*=\s*Opus reviewer", command):
        return True
    # Also check commit message file when -F flag is used
    m = re.search(r"-F\s+[\"']?(\S+?)[\"']?(?:\s|$)", command)
    if m:
        try:
            with open(m.group(1), encoding="utf-8") as f:
                return bool(re.search(r"Review\s*=\s*Opus reviewer", f.read()))
        except Exception:
            pass
    return False


def _find_python() -> str | None:
    """Locate the project .venv's python, assuming cwd is the repo root (same
    assumption _get_staged_files already relies on via bare `git` calls)."""
    here = os.getcwd()
    for candidate in (
        os.path.join(here, ".venv", "Scripts", "python.exe"),  # Windows
        os.path.join(here, ".venv", "bin", "python"),          # POSIX
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _run_pytest() -> tuple:
    """Returns (True, "") on pass, (False, detail) on failure, (None, detail)
    when the suite couldn't be run at all (missing venv/pytest, timeout)."""
    py = _find_python()
    if not py:
        return None, ".venv python not found -- run `pip install -r requirements-dev.txt` in .venv"
    try:
        r = subprocess.run(
            [py, "-m", "pytest", "tests/", "-q"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "pytest timed out after 120s -- investigate a hang before proceeding"
    except Exception as e:
        return None, f"could not invoke pytest ({e})"

    if r.returncode == 0:
        return True, ""
    tail = "\n".join((r.stdout or "").strip().splitlines()[-15:])
    return False, tail


if __name__ == "__main__":
    main()
