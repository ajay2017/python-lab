"""
Judgment-layer opinion schema — Phase 0 (log-only instrumentation).

Every "witness" subsystem hands the future Judge (docs/plans/judgment-layer.md)
an opinion in this one common shape. Phase 0 only builds and logs opinions —
it reads no config, computes no posture, and changes no existing behavior.

Routing (decided in the design, NOT yet implemented — Phase 1+):
  - protective dimension vs acquisitive dimension, same ticker -> VETO (hard
    suppression, never averaged)
  - two non-protective dimensions, same ticker -> WEIGHTING
  - same dimension, same ticker, opposite signal -> CONTRADICTION AUDIT

PROTECTIVE_DIMENSIONS is defined here (not just in the plan doc) because Phase 1's
veto logic will need to test dimension membership, and the set must not drift
between the two.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

DIMENSIONS = (
    "quality",
    "momentum",
    "thesis_integrity",
    "position_health",
    "concentration",
    "structural_risk",
    "macro_regime",
    "behavioral_fit",
    "sentiment",
    "leverage",
)

# Protective dimensions veto acquisitive ones on the same ticker (Phase 1) —
# they are never blended into a weighted average. See judgment-layer.md Q1.
PROTECTIVE_DIMENSIONS = frozenset({
    "position_health", "concentration", "structural_risk", "leverage",
})
ACQUISITIVE_DIMENSIONS = frozenset({"quality", "momentum"})

# Advisory-only dimensions never gate and are always excluded from weighting
# (tax-aware lens, catalyst awareness — existing redlines).
ADVISORY_DIMENSIONS = frozenset({"tax", "catalyst"})


def build_opinion(
    *,
    source: str,
    dimension: str,
    signal: float,
    confidence: float,
    evidence: str,
    ticker: Optional[str] = None,
    as_of: Optional[datetime] = None,
    is_live: bool = True,
    advisory: bool = False,
    label: Optional[str] = None,
) -> dict:
    """Build one opinion dict in the Judge's synthesis-contract shape.

    Pure function, no I/O. `signal` must already be normalized to -1..+1.
    `is_live=False` marks an opinion computed from stale/offline data — a
    protective witness with is_live=False must degrade posture confidence in
    the Judge (Phase 1+), never be silently excluded (the None-vs-[] trap
    promoted to portfolio scale — see judgment-layer.md finding #2).
    """
    if dimension not in DIMENSIONS and dimension not in ADVISORY_DIMENSIONS:
        raise ValueError(f"unknown dimension: {dimension!r}")
    if not (-1.0 <= signal <= 1.0):
        raise ValueError(f"signal must be normalized to -1..+1, got {signal!r}")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be 0..1, got {confidence!r}")
    return {
        "source": source,
        "dimension": dimension,
        "ticker": ticker.upper() if ticker else None,
        "signal": float(signal),
        "label": label,
        "confidence": float(confidence),
        "as_of": (as_of or datetime.now(timezone.utc)).isoformat(),
        "is_live": bool(is_live),
        "evidence": evidence,
        "advisory": bool(advisory or dimension in ADVISORY_DIMENSIONS),
    }


def is_protective(dimension: str) -> bool:
    return dimension in PROTECTIVE_DIMENSIONS


def is_acquisitive(dimension: str) -> bool:
    return dimension in ACQUISITIVE_DIMENSIONS
