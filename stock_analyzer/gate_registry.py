"""Gate registry — frozen, APPEND-ONLY id → human label map.

Only ids the capture half (gate_ledger.py) can actually emit are listed here.
To add a new id: (1) add it here, (2) add a §2A.3 row in docs/requirements.md.
The anti-rot test (tests/test_gate_registry.py) will fail if an id is present
here but missing from the §2A.3 table — that is the intended behaviour.

These are IDENTIFIERS, not thresholds. Do NOT put this in constants.py.
APPEND-ONLY: never rename or remove an existing id. The gate_suppressions table
uses these as a foreign-key-like string, and a rename silently orphans history.
"""
from __future__ import annotations

# Gate id → short human label.
# Only ids the capture half actually emits; do NOT invent entries for gates
# the ledger never writes.
GATE_IDS: dict[str, str] = {
    "G-01": "Risk Advisor TRIM → add-to-winner suppressed",
    "G-04": "Single-name ceiling → add-to-winner suppressed",
    "G-07": "Imminent HIGH macro → new pick suppressed",
    "G-09": "Rebalancer drift-overweight → add-to-winner suppressed",
    "G-16": "Sector hard cap → pick or add-to-winner suppressed",
    "G-20": "Early-deterioration WATCH → add-to-winner suppressed",
    "G-23": "Bear-day tone → all new entries deferred",
    "G-24": "Post-add cooldown → add-to-winner suppressed",
}
