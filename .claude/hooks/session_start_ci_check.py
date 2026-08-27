#!/usr/bin/env python3
"""SessionStart hook: surface a red CI run on `main` at the start of every
session, so a broken suite doesn't sit unnoticed the way it did for 4 days
in the 77205a5 incident (discovered 2026-08-26).

Why this exists: the pre-commit/pre-push hook (pre_tool_checks.py) fails OPEN
(warns, does not block) when it cannot resolve `.venv` -- deliberately, to
avoid false-blocking a commit over local infra. GitHub Actions independently
re-runs the same three checks in a clean, `.venv`-independent environment and
reliably catches what the local hook misses (confirmed twice on 2026-08-26/27:
the 77205a5 test failures, and a UTC/ET date-arithmetic bug in
tests/test_macro_playbook.py). But nothing surfaced a red run to anyone -- it
was a red X on GitHub that no session was reading. A branch ruleset making
these checks push-blocking was tried the same day and reverted: GitHub's
required_status_checks needs a commit to already carry a passing check before
it can update the ref, which is incompatible with this repo's direct-push (no
PR) workflow -- see docs/architecture.md 9.2. This hook is the visibility
half of that decision: report, never enforce.

Checks the three GitHub Actions checks this repo treats as its deterministic
gates (CLAUDE.md "Review & test economy"): pytest, no-new-antipatterns,
constants-documented. Reports ONLY when the latest `main` commit is not clean
across all three that have reported -- silent on green, matching the existing
hook's philosophy (pre_tool_checks.py's own gates warn/block on a real
problem, say nothing on success).

Fails open on ANY infra problem -- no `gh`, not authenticated, no network, not
a git repo, rate-limited, malformed output. Never blocks or slows a session
beyond a couple of quick, timeout-bounded `gh api` calls. Same "never
false-block on infra" convention as pre_tool_checks.py._find_python.
"""
import json
import re
import subprocess
import sys

_REQUIRED_CHECKS = ("pytest", "no-new-antipatterns", "constants-documented")
_GH_TIMEOUT_SEC = 8


def _repo_slug() -> str | None:
    """'owner/repo' from the `origin` remote, or None if unavailable."""
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        m = re.search(r"[:/]([^/]+/[^/]+?)(\.git)?$", r.stdout.strip())
        return m.group(1) if m else None
    except Exception:
        return None


def _latest_main_sha(repo: str) -> str | None:
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{repo}/commits/main", "--jq", ".sha"],
            capture_output=True, text=True, timeout=_GH_TIMEOUT_SEC,
        )
        sha = r.stdout.strip()
        return sha if r.returncode == 0 and sha else None
    except Exception:
        return None


def _check_runs(repo: str, sha: str) -> list:
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{repo}/commits/{sha}/check-runs", "--jq", ".check_runs"],
            capture_output=True, text=True, timeout=_GH_TIMEOUT_SEC,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return []
        return json.loads(r.stdout)
    except Exception:
        return []


def main() -> None:
    try:
        repo = _repo_slug()
        if not repo:
            sys.exit(0)
        sha = _latest_main_sha(repo)
        if not sha:
            sys.exit(0)
        runs = _check_runs(repo, sha)
        if not runs:
            sys.exit(0)

        by_name = {r.get("name"): r for r in runs}
        problems = []
        for name in _REQUIRED_CHECKS:
            run = by_name.get(name)
            if run is None:
                continue  # never ran for this commit (path filters) -- not this hook's business
            if run.get("status") == "completed" and run.get("conclusion") not in (
                "success", "neutral", "skipped",
            ):
                problems.append(f"{name}: {run.get('conclusion')}")

        if not problems:
            sys.exit(0)

        detail = "; ".join(problems)
        url = f"https://github.com/{repo}/commit/{sha}/checks"
        print(json.dumps({
            "systemMessage": f"⚠ CI is RED on the latest main commit ({sha[:7]}): {detail}",
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": (
                    f"GitHub Actions CI is currently RED on the latest commit to `main` "
                    f"in {repo} ({sha[:7]}): {detail}. This was silently unnoticed for 4 "
                    "days once before (the 77205a5 incident, 2026-08-26) -- investigate "
                    f"before assuming the suite passes. Detail: {url}"
                ),
            },
        }))
    except Exception:
        pass  # never block or fail a session start on an infra hiccup
    sys.exit(0)


if __name__ == "__main__":
    main()
