"""
App Settings — the reference-data layer (Commit 1 of 3, 2026-09-01).

Pure logic only. Nothing in this module touches the database or the network
— see `stock_analyzer/db.py::load_reference_table` / `save_reference_table`
for the Supabase-backed half. This module owns three decisions:

  canonicalize      — the deterministic normal form a payload must be reduced
                       to before its content hash is compared, so reordering
                       tickers or changing case is never mistaken for a real
                       edit (the "snooze-button" trap this whole feature
                       exists to avoid — see docs/plans/app-settings.md,
                       "The trap this design exists to avoid").
  resolve_universe  — the single-source, no-fallback read: `(payload, as_of)`
                       together from ONE row, or `ReferenceDataUnavailable`.
                       An earlier hybrid draft (code fallback when the DB is
                       offline) was designed and explicitly REJECTED — see
                       the design doc's "Resolution" section — because a
                       silent fallback to a frozen list is exactly the 2026-
                       07-14 INTC stale-data incident repeated on this
                       surface. Nothing calls this yet in this commit; every
                       existing caller of SECTOR_UNIVERSE / DISCOVERY_UNIVERSE
                       / _SECTOR_CANDIDATES is untouched until Commit 2.
  validate_payload  — the two structural rules a proposed edit must pass
                       BEFORE it is ever saved (the UI's provider/network
                       ticker-existence validation is Commit 2's job, per the
                       design doc's Q2 resolution — it needs the offline-block
                       flow that belongs with the actual editing page).

Redline reminder: this module and everything it resolves is INPUT-SET data —
which names a rule is applied to — never a decision rule itself (a threshold,
a weight, a gate). See the design doc's "redline, agreed up front".
"""
from __future__ import annotations

from datetime import date


class ReferenceDataUnavailable(Exception):
    """Raised by `resolve_universe()` when the DB-backed reference table for
    `name` cannot be read at all, OR was read successfully but is empty.

    Both cases are UNAVAILABLE, deliberately never treated as "a
    legitimately empty universe" — per the design doc, "scanned nothing,
    found nothing" is indistinguishable from a working scan that found no
    gaps, which is the single most dangerous output this feature could
    produce (it looks identical to a clean bill of health)."""


def canonicalize(payload: dict) -> dict:
    """Deterministic, idempotent normal form for a bucket/sector -> [tickers]
    payload: sort the bucket/sector keys, upper-case every ticker, and sort
    each bucket's own ticker list.

    This exact function is what `db.save_reference_table`'s content-hash
    "snooze-button" mechanism depends on — canonicalize(x) must equal
    canonicalize(shuffled/recased x), and canonicalize(canonicalize(x)) must
    equal canonicalize(x), for the no-op-save invariant to hold. Deliberately
    does NOT deduplicate a bucket's ticker list — collapsing a genuine
    duplicate would silently change the payload's content, which is a
    different thing than merely normalizing its order/case.

    Never raises on a well-shaped `dict[str, list[str]]`; a `None`/falsy
    bucket value is treated as an empty list rather than erroring, since a
    freshly-emptied bucket is a valid (if attention-worthy) state.
    """
    if not payload:
        return {}
    out: dict[str, list[str]] = {}
    for key, tickers in payload.items():
        out[str(key)] = sorted(str(t).strip().upper() for t in (tickers or []))
    return dict(sorted(out.items()))


def resolve_universe(name: str) -> "tuple[dict, date]":
    """Resolve the current `(payload, as_of)` for a DB-backed reference
    table, reading both from the SAME row so no caller can ever have them
    disagree.

    Single source of truth, no fallback — raises `ReferenceDataUnavailable`
    when:
      - `db.load_reference_table(name)` returns `None` (the offline
        sentinel: DB unreachable, the table doesn't exist yet because the
        DDL hasn't been applied, or RLS is misconfigured), OR
      - the row's payload is empty — `{}`, or every bucket's ticker list is
        empty.

    Callers are expected to surface `ReferenceDataUnavailable` the way Home
    already surfaces a failed bundle load: a fail-loud message naming the
    cause and no recommendations from this input set — never a silent
    partial or empty scan that could be mistaken for "no opportunities
    today". Nothing calls this function yet in this commit (Commit 2 wires
    it into the real importers).
    """
    from stock_analyzer import db

    row = db.load_reference_table(name)
    if row is None:
        raise ReferenceDataUnavailable(
            f"reference table '{name}' is unavailable — the database "
            "couldn't be reached, this table hasn't been seeded yet, or "
            "row-level security is misconfigured"
        )

    payload = row.get("payload")
    if not payload or not any(payload.values()):
        raise ReferenceDataUnavailable(
            f"reference table '{name}' has an empty payload — refusing to "
            "treat this as a legitimately empty universe"
        )

    as_of_raw = row.get("as_of")
    if isinstance(as_of_raw, date):
        as_of = as_of_raw
    elif as_of_raw:
        as_of = date.fromisoformat(str(as_of_raw)[:10])
    else:
        # A non-empty payload with no resolvable `as_of` is a malformed row,
        # not a legitimate state -- payload and as_of must always travel
        # together (see module docstring), so this is UNAVAILABLE too rather
        # than silently returning a broken (payload, None) tuple.
        raise ReferenceDataUnavailable(
            f"reference table '{name}' has a payload but no resolvable "
            "as_of date -- treating the row as malformed rather than "
            "guessing a date"
        )

    return payload, as_of


def validate_payload(
    name: str,
    payload: dict,
    existing_bucket_keys: "set[str] | None" = None,
) -> "list[str]":
    """Return a list of validation error strings; an empty list means the
    payload is valid. Deliberately does NOT do provider/network
    ticker-existence validation — that belongs to Commit 2's editing page,
    which needs the offline-block UI flow the design doc's Q2 resolution
    describes (a validator that can itself be down must block the save, not
    save anyway).

    Two rules, both from the resolved design doc:

    1. STRUCTURE LOCK — if `existing_bucket_keys` is given, the payload's
       bucket/sector key SET must match it exactly. v1 locks the bucket
       label set (only ticker membership is editable) specifically to
       protect `portfolio._DIVERSIFY_TO_DISCOVERY`'s key coupling — you
       cannot orphan a map key you cannot rename or delete.
    2. SECTOR_CANDIDATES-SPECIFIC — when `name == "sector_candidates"`,
       every ticker across every bucket must already have a
       `portfolio.TICKER_SECTORS` entry. This is what lets this roster stay
       UI-editable without dragging that dangerous dict's own
       macro-coverage invariant along with it.
    """
    errors: list[str] = []

    if existing_bucket_keys is not None:
        proposed = set(payload.keys())
        if proposed != existing_bucket_keys:
            missing = sorted(existing_bucket_keys - proposed)
            unexpected = sorted(proposed - existing_bucket_keys)
            errors.append(
                "bucket/sector structure is locked — cannot add, remove, or "
                "rename buckets in this editor "
                f"(missing: {missing}, unexpected: {unexpected})"
            )

    if name == "sector_candidates":
        from stock_analyzer.portfolio import TICKER_SECTORS

        unknown = sorted({
            str(t).strip().upper()
            for tickers in payload.values()
            for t in (tickers or [])
            if str(t).strip().upper() not in TICKER_SECTORS
        })
        if unknown:
            errors.append(
                "the following ticker(s) have no portfolio.TICKER_SECTORS "
                "entry and cannot be added to sector_candidates: "
                f"{', '.join(unknown)}"
            )

    return errors
