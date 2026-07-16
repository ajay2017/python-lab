#!/usr/bin/env python3
"""Doc-sync tripwire: every module-level constant in stock_analyzer/constants.py
must be documented in docs/ (architecture.md or requirements.md), OR explicitly
listed in scripts/constants_doc_allowlist.txt as intentionally-internal.

Why: features ship faster than docs. New decision thresholds landing in
constants.py without a docs entry is the highest-frequency drift class and the
one that's 100% mechanically checkable (see CLAUDE.md "Definition of Done").
This caught EARNINGS_MIN_BEAT_RATE_ENTRY and PERF_ALPHA_BAND_PCT after the fact
on 2026-07-16; the tripwire makes that impossible to miss going forward.

Usage:
  python scripts/check_constants_documented.py          # check (exit 1 on drift)
  python scripts/check_constants_documented.py --init    # (re)write the allowlist
                                                          # from the current
                                                          # undocumented set
The allowlist is a conscious "I'm choosing not to document this plumbing
constant" checkpoint — adding a name to it should be a deliberate act, not a
reflex to silence the check.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSTANTS = ROOT / "stock_analyzer" / "constants.py"
DOCS = [ROOT / "docs" / "architecture.md", ROOT / "docs" / "requirements.md"]
ALLOWLIST = ROOT / "scripts" / "constants_doc_allowlist.txt"

_CONST_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def module_constants() -> list[str]:
    """Top-level UPPER_CASE names assigned in constants.py (plain + annotated)."""
    tree = ast.parse(CONSTANTS.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:  # top-level only — not names inside funcs/classes
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for t in targets:
            for leaf in ([t] if isinstance(t, ast.Name) else getattr(t, "elts", [])):
                if isinstance(leaf, ast.Name) and _CONST_RE.match(leaf.id):
                    names.append(leaf.id)
    return sorted(set(names))


def documented_names() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in DOCS if p.exists())


def load_allowlist() -> set[str]:
    if not ALLOWLIST.exists():
        return set()
    out = set()
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def undocumented() -> list[str]:
    docs = documented_names()
    missing = []
    for name in module_constants():
        # \b handles the COMPOSITE_BUY vs COMPOSITE_BUY_FLAT_DAY case: the char
        # after a prefix match is "_" (a word char), so \b won't match there.
        if not re.search(rf"\b{re.escape(name)}\b", docs):
            missing.append(name)
    return missing


def main() -> int:
    if "--init" in sys.argv:
        miss = undocumented()
        header = (
            "# Baseline of constants NOT yet documented in docs/ (snapshot at tripwire\n"
            "# setup, 2026-07-16). Mixed bag: some are genuine internal plumbing; others\n"
            "# are real decision values that still deserve a docs/architecture.md row —\n"
            "# a backlog to burn down over time, NOT a permanent 'skip'. The point of the\n"
            "# baseline is only to make CI green on day one so it can catch NEW drift.\n"
            "# A newly added constant should be documented (preferred) or consciously\n"
            "# added here, never left to silently pass. Regenerate (rare — only after\n"
            "# deliberately deciding a batch is intentionally internal):\n"
            "#   python scripts/check_constants_documented.py --init\n"
        )
        ALLOWLIST.write_text(header + "\n".join(miss) + "\n", encoding="utf-8")
        print(f"Wrote {len(miss)} names to {ALLOWLIST.relative_to(ROOT)}")
        return 0

    allow = load_allowlist()
    drift = [n for n in undocumented() if n not in allow]
    if drift:
        print("❌ Undocumented constants in stock_analyzer/constants.py:")
        for n in drift:
            print(f"   - {n}")
        print(
            "\nFix: document each in docs/architecture.md's constants table "
            "(or docs/requirements.md), OR — if it's genuinely internal plumbing "
            "— add it to scripts/constants_doc_allowlist.txt.\n"
            "See CLAUDE.md → 'Definition of Done'."
        )
        return 1
    print("✅ All constants.py constants are documented or allowlisted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
