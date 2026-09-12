#!/usr/bin/env python3
"""SessionStart hook: detect whether pre_tool_checks.py's Hard Rule #4/#5
gates are ACTUALLY enforcing, not just configured.

Why this exists: a 2026-09-09 app review found that pre_tool_checks.py --
the script CLAUDE.md calls "the real pre-deploy safety net" -- did not fire
at all for `git commit` invocations in a VSCode-extension/Agent-SDK session,
despite being correctly wired in .claude/settings.json's PreToolUse hooks and
proven correct when manually piped a synthetic payload (the script's own
logic was never the bug). A feat( commit went through that session with no
Design=/Build= trailers and no BLOCKED message. "Configured in settings.json"
and "actually invoked by this runtime" turned out to be two different facts,
and nothing distinguished them without a deliberate manual test.

Rather than repeat that manual test every session, this checks git history
directly for the TELLTALE SIGN the hook would have prevented: a recent
commit that violates Hard Rule #4 (a _GATE_FILES-touching commit with no
Review= citation) or Hard Rule #5 (a feat( commit missing Design=/Build=
trailers). If one exists, the hook did NOT block it -- direct, retroactive
evidence of non-enforcement in whatever runtime made that commit, and a
stronger signal than a synthetic self-test (which only proves the SCRIPT's
own logic is correct, not that the harness actually invokes it as a hook).

Reuses pre_tool_checks.py's own gate/citation logic directly via import
(never reimplemented) so this can never silently drift from what the real
hook checks.

Fails open on ANY infra problem (not a git repo, git missing, unreadable
history, import failure) -- same "never false-block on infra" convention as
the sibling session_start_ci_check.py. Report-only: never blocks or slows a
session, and is silent when nothing is found (matching that sibling's own
"say nothing on success" philosophy).
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import pre_tool_checks as _ptc
except Exception:
    sys.exit(0)  # can't import the sibling module -- fail open

# How far back to look. Bounded, not "all history" -- Hard Rule #5's
# Design=/Build= trailer requirement was only adopted 2026-08-15, so
# scanning ancient commits would false-flag pre-adoption history. This
# repo's actual commit velocity means 40 commits comfortably covers recent
# sessions without reaching back anywhere near that date under normal use.
_SCAN_COMMIT_COUNT = 40
_GIT_TIMEOUT_SEC = 10


def _recent_commits() -> list:
    """[(sha, full_message, [changed_files])], newest first. Excludes merge
    commits -- a merge's own diff doesn't mean what a normal commit's does,
    and this repo's direct-push workflow rarely produces one anyway."""
    try:
        r = subprocess.run(
            ["git", "log", f"-{_SCAN_COMMIT_COUNT}", "--no-merges", "--format=%H"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_SEC,
        )
        if r.returncode != 0:
            return []
        shas = [s for s in r.stdout.strip().splitlines() if s]
    except Exception:
        return []

    out = []
    for sha in shas:
        try:
            msg = subprocess.run(
                ["git", "log", "-1", "--format=%B", sha],
                capture_output=True, text=True, timeout=_GIT_TIMEOUT_SEC,
            ).stdout
            files = subprocess.run(
                ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
                capture_output=True, text=True, timeout=_GIT_TIMEOUT_SEC,
            ).stdout.strip().splitlines()
        except Exception:
            continue  # one unreadable commit shouldn't sink the whole scan
        out.append((sha, msg, files))
    return out


def main() -> None:
    try:
        commits = _recent_commits()
        if not commits:
            sys.exit(0)

        violations = []  # (sha, reason, gate_files_hit)
        for sha, message, files in commits:
            gate_hit = [f for f in files if f in _ptc._GATE_FILES]
            if gate_hit and not _ptc._has_review_citation(message):
                violations.append((sha, "Hard Rule #4: gate file(s) staged with no Review= citation", gate_hit))
                continue  # one flagged reason per commit is enough signal
            if _ptc._is_feature_commit(message):
                missing = _ptc._missing_provenance_trailers(message)
                if missing:
                    violations.append(
                        (sha, f"Hard Rule #5: feat( commit missing {'/'.join(missing)} trailer(s)", [])
                    )

        if not violations:
            sys.exit(0)

        lines = [
            f"{sha[:7]} — {reason}" + (f" [{', '.join(gate_hit)}]" if gate_hit else "")
            for sha, reason, gate_hit in violations[:5]
        ]
        detail = "; ".join(lines)
        print(json.dumps({
            "systemMessage": (
                f"⚠ HOOK LIVENESS: {len(violations)} recent commit(s) violate a rule "
                "pre_tool_checks.py should have blocked — it may not be firing in this "
                "runtime. Don't rely on the automatic commit gate this session; invoke "
                "reviewer/planner and verify citations by hand before committing to a "
                "gate file or shipping a feat( commit."
            ),
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": (
                    "pre_tool_checks.py is configured in .claude/settings.json's "
                    f"PreToolUse hooks, but the following recent commit(s) should have "
                    f"been BLOCKED and weren't: {detail}. This exact gap was first found "
                    "2026-09-09 in a VSCode-extension/Agent-SDK runtime — the hook script "
                    "itself is correct when invoked directly, but the harness did not "
                    "invoke it for that session's Bash tool calls. 'Hooks are configured' "
                    "and 'hooks are actually enforcing' are not the same fact in every "
                    "runtime. Treat Hard Rules #4/#5 as manual obligations for the rest "
                    "of this session — invoke the reviewer/planner subagents explicitly "
                    "and add Design=/Build=/Review= trailers by hand — rather than "
                    "trusting the commit gate to catch a missed one. See memory "
                    "feedback_hook_enforcement."
                ),
            },
        }))
    except Exception:
        pass  # never block or fail a session start on an infra hiccup
    sys.exit(0)


if __name__ == "__main__":
    main()
