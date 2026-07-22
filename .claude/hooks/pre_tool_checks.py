#!/usr/bin/env python3
"""Pre-tool-use hook: mechanically enforce hard rules #3 and #4 from CLAUDE.md."""
import json
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

    # Hard rule #4: Commits touching gate files require an Opus review citation
    if re.search(r"\bgit\s+commit\b", command):
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

    sys.exit(0)


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


if __name__ == "__main__":
    main()
