"""Anti-rot tests for gate_registry.py.

Two guards:

1. §2A.3 table check — parses docs/requirements.md (by heading, not line numbers)
   and asserts every GATE_IDS entry appears as a row id. Fails intentionally when
   a gate id is added without a documentation row.

2. Literal id check — scans daily_briefing.py and gate_ledger.py for gate id
   string literals and asserts every one is registered in GATE_IDS. A typo'd
   "G-99" at a site would persist an unregistered id; this test catches it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from stock_analyzer.gate_registry import GATE_IDS


def _parse_gate_ids_from_requirements() -> set[str]:
    """Return the set of gate IDs (G-NN) listed in the §2A.3 table."""
    req_path = Path(__file__).parent.parent / "docs" / "requirements.md"
    text = req_path.read_text(encoding="utf-8")

    # Find the §2A.3 section by its heading; collect lines until the next ###
    in_section = False
    ids: set[str] = set()
    for line in text.splitlines():
        if re.match(r"^###\s+2A\.3\b", line):
            in_section = True
            continue
        if in_section and re.match(r"^###\s+", line):
            # Next subsection — stop scanning
            break
        if in_section:
            # Table rows look like: | G-NN | ... |
            m = re.match(r"^\|\s*(G-\d+)\b", line)
            if m:
                ids.add(m.group(1))
    return ids


_REQUIREMENTS_IDS = _parse_gate_ids_from_requirements()


@pytest.mark.parametrize("gate_id", sorted(GATE_IDS.keys()))
def test_registry_id_appears_in_requirements_table(gate_id):
    """Every id in GATE_IDS must have a §2A.3 row in docs/requirements.md.

    Add the row BEFORE adding the id to GATE_IDS — failing this test means
    the registry outran its documentation.
    """
    assert gate_id in _REQUIREMENTS_IDS, (
        f"Gate id '{gate_id}' is in gate_registry.GATE_IDS but has no row in "
        f"docs/requirements.md §2A.3. Add the row first, then re-run."
    )


def _gate_id_literals_in_file(path: Path) -> set[str]:
    """Return every "G-NN" string literal appearing in `path`."""
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"""["'](G-\d+)["']""", text))


_REPO = Path(__file__).parent.parent
_PRODUCER_IDS = (
    _gate_id_literals_in_file(_REPO / "stock_analyzer" / "daily_briefing.py")
    | _gate_id_literals_in_file(_REPO / "stock_analyzer" / "gate_ledger.py")
)


@pytest.mark.parametrize("gate_id", sorted(_PRODUCER_IDS))
def test_producer_gate_id_is_registered(gate_id):
    """Every gate id string literal in daily_briefing.py and gate_ledger.py must
    be a key in GATE_IDS.  A typo'd "G-99" at a suppression site would persist
    an unregistered id with no test noticing — this catches it.
    """
    assert gate_id in GATE_IDS, (
        f"Gate id '{gate_id}' appears as a literal in the producer code but is "
        f"NOT a key in gate_registry.GATE_IDS. Add it to the registry (and a "
        f"§2A.3 row in docs/requirements.md) or fix the typo."
    )
