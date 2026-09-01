"""
App Settings — the reference-data layer (Commit 1 of 3, 2026-09-01), the
save/validate/confirm decision helpers Commit 2 of 3 added the same day, and
the staged cutover Commit 3 completed (same day): the module-level
SECTOR_UNIVERSE/DISCOVERY_UNIVERSE/_SECTOR_CANDIDATES dicts these tables were
originally seeded FROM are now deleted from scanner.py/discovery_universe.py/
portfolio.py — the DB is the sole source, no code fallback anywhere.

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
                       surface. Every real importer (scanner.py/portfolio.py/
                       ticker_liveness.py/cron_runner.py/app.py) reads
                       through this function, and (as of Commit 3) their own
                       `universe`/`sector_candidates`/`discovery_universe`
                       parameters are REQUIRED with no default at all — there
                       is no module-level dict left anywhere to fall back to.
  validate_payload  — the two structural rules a proposed edit must pass
                       BEFORE it is ever saved (the UI's provider/network
                       ticker-existence validation is Commit 2's job, per the
                       design doc's Q2 resolution — it needs the offline-block
                       flow that belongs with the actual editing page).
  resolve_universe_or_none — non-raising counterpart to resolve_universe, for
                       every REAL caller (app.py / cron_runner.py / the pure
                       importer functions' orchestration) that needs to render
                       a fail-loud banner or route to _handle_db_unavailable
                       rather than catch an exception at every call site.
  decide_large_drop_confirmation / decide_save_action — Commit 2's App
                       Settings save-flow DECISION logic, extracted out of
                       app.py per this project's "extract the DECISION, not
                       just the helper" convention (CLAUDE.md) — app.py's
                       ⚙️ App Settings page is render-only wiring around these.

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
    today". As of Commit 2, every real importer calls this directly (the
    ⚙️ App Settings editing page) or via `resolve_universe_or_none` (every
    other real caller).
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


def resolve_universe_or_none(name: str) -> "tuple[dict | None, date | None, str | None]":
    """Non-raising counterpart to `resolve_universe`, for every real caller
    (Commit 2's rewired `scanner.py`/`portfolio.py`/`ticker_liveness.py`
    orchestration, `cron_runner.py`, and every `app.py` read site) that needs
    to render a fail-loud banner — or route into `cron_runner._handle_db_
    unavailable` — rather than wrap every call site in its own try/except.

    Returns `(payload, as_of, None)` on success, or `(None, None, <error
    message>)` when the table is unavailable. The `None` payload on failure
    is the SAME offline sentinel a bare `except ReferenceDataUnavailable`
    would see — this just pre-unpacks it. Callers must render/log the error
    (never silently substitute an empty dict without saying so) before
    deciding how to degrade for that render — see the design doc's "Never
    silently substitute different data" invariant.

    Never raises.
    """
    try:
        payload, as_of = resolve_universe(name)
        return payload, as_of, None
    except ReferenceDataUnavailable as exc:
        return None, None, str(exc)


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
       `portfolio.TICKER_SECTORS` entry, AND that entry's VALUE must equal
       the bucket key the ticker is being placed under (bucket-key
       EQUALITY, not mere presence).

       Tightened 2026-09-01 per a Commit-1 Opus review finding: the original
       check only confirmed *some* `TICKER_SECTORS` entry existed, which
       would have let e.g. AAPL (`TICKER_SECTORS["AAPL"] == "Consumer
       Tech"`) be added into a `Healthcare` bucket — passing this validator
       while creating exactly the roster incoherence
       `_SECTOR_CANDIDATES`'s real invariant (`portfolio.py:1258-1259`,
       guarded by `tests/test_portfolio.py::
       test_roster_ticker_sector_matches_its_roster_key`) forbids. This is
       what lets the roster stay UI-editable without dragging that
       dangerous dict's own macro-coverage invariant along with it.
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

        unknown: set[str] = set()
        mismatched: dict[str, "tuple[str, str]"] = {}
        for bucket, tickers in payload.items():
            for t in (tickers or []):
                tu = str(t).strip().upper()
                if not tu:
                    continue
                curated = TICKER_SECTORS.get(tu)
                if curated is None:
                    unknown.add(tu)
                elif curated != bucket:
                    mismatched[tu] = (bucket, curated)

        if unknown:
            errors.append(
                "the following ticker(s) have no portfolio.TICKER_SECTORS "
                "entry and cannot be added to sector_candidates: "
                f"{', '.join(sorted(unknown))}"
            )
        if mismatched:
            detail = ", ".join(
                f"{t} (placed under '{placed}', but TICKER_SECTORS says '{curated}')"
                for t, (placed, curated) in sorted(mismatched.items())
            )
            errors.append(
                "the following ticker(s) are classified under a DIFFERENT "
                "sector in portfolio.TICKER_SECTORS than the bucket they're "
                f"being placed in — this is the exact roster incoherence the "
                f"structure lock exists to prevent: {detail}"
            )

    return errors


def decide_large_drop_confirmation(
    old_payload: dict,
    new_payload: dict,
    threshold_pct: float,
) -> dict:
    """Decide whether a proposed reference-table save needs an explicit
    confirmation click before it's allowed to proceed — the design doc's Q8
    resolution (replace semantics + a confirmation gate on a large drop or a
    newly-emptied bucket, `stock_analyzer.constants.
    REFERENCE_TABLE_LARGE_DROP_CONFIRM_PCT`).

    Pure — canonicalizes both payloads internally so callers never have to
    remember to, and reordering/recasing alone can never trigger this (same
    canonicalize() the no-op-save mechanism depends on).

    Two INDEPENDENT triggers; either alone requires confirmation:

    1. TOTAL COUNT DROP — the new payload has strictly fewer tickers than
       the old one, by MORE than `threshold_pct` percent of the old total.
       Boundary is the same "== is still normal" shape as
       `TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT`: a drop of EXACTLY
       `threshold_pct` does NOT trigger this — only strictly more does.
       An old total of 0 can never trigger it (nothing to drop from).
    2. NEWLY EMPTIED BUCKET — any bucket that had >=1 ticker in the old
       payload has 0 in the new payload. UNCONDITIONAL — not gated by
       `threshold_pct` at all, because a single bucket's raw count is too
       small a sample for a percentage floor to mean anything, and v1's
       locked bucket structure (Q3) means this can only be a membership
       edit, never a removed bucket.

    Returns `{"needs_confirmation": bool, "reasons": [str, ...]}` — reasons
    is always populated when `needs_confirmation` is True, human-readable,
    so the UI can say WHY it's asking rather than blocking silently.
    """
    old_c = canonicalize(old_payload)
    new_c = canonicalize(new_payload)

    old_total = sum(len(v) for v in old_c.values())
    new_total = sum(len(v) for v in new_c.values())
    reasons: list[str] = []

    if old_total > 0 and new_total < old_total:
        drop_pct = (old_total - new_total) / old_total * 100.0
        if drop_pct > threshold_pct:
            reasons.append(
                f"total ticker count would drop {drop_pct:.0f}% "
                f"({old_total} → {new_total}) — more than the "
                f"{threshold_pct:.0f}% confirmation threshold"
            )

    for bucket, old_tickers in old_c.items():
        if old_tickers and not new_c.get(bucket):
            reasons.append(
                f"'{bucket}' would go from {len(old_tickers)} ticker(s) to "
                "0 — that sector would stop being scanned/considered entirely"
            )

    return {"needs_confirmation": bool(reasons), "reasons": reasons}


def decide_save_action(
    *,
    structure_errors: "list[str] | None" = None,
    validator_offline: bool = False,
    unresolved_tickers: "list[str] | None" = None,
    large_drop: "dict | None" = None,
    confirmed: bool = False,
) -> dict:
    """Single decision point for what the ⚙️ App Settings Save button does,
    given the independently-computed validation results — app.py should call
    this rather than re-deriving the precedence itself, so the ordering
    (structure lock → validator-offline → unresolved ticker → large-drop
    confirmation) lives in one tested place.

    Returns `{"action": "blocked" | "needs_confirmation" | "proceed",
    "reasons": [str, ...]}`. Precedence, each a hard stop before the next is
    even considered:

      1. `structure_errors` (from `validate_payload`) — always blocks.
      2. `validator_offline` — the offline-contract-applied-to-the-validator
         rule (Q2): the provider ticker-existence check itself could not
         run, so BLOCK rather than save unverified.
      3. `unresolved_tickers` — a symbol that failed to resolve on the
         provider layer — always blocks.
      4. `large_drop` (from `decide_large_drop_confirmation`) — requires
         `confirmed=True` to proceed; without it, "needs_confirmation" (not
         blocked — one click clears it).
      5. Otherwise "proceed".
    """
    if structure_errors:
        return {"action": "blocked", "reasons": list(structure_errors)}

    if validator_offline:
        return {
            "action": "blocked",
            "reasons": [
                "couldn't validate ticker symbols right now — the provider "
                "layer is unreachable. Try again rather than save unverified."
            ],
        }

    if unresolved_tickers:
        return {
            "action": "blocked",
            "reasons": [
                "the following ticker(s) do not resolve on any provider: "
                f"{', '.join(sorted(unresolved_tickers))}"
            ],
        }

    large_drop = large_drop or {"needs_confirmation": False, "reasons": []}
    if large_drop.get("needs_confirmation") and not confirmed:
        _ldr_reasons = large_drop.get("reasons")
        return {
            "action": "needs_confirmation",
            "reasons": list(_ldr_reasons) if _ldr_reasons else [],
        }

    return {"action": "proceed", "reasons": []}


def changed_tickers(old_payload: dict, new_payload: dict) -> "set[str]":
    """Every ticker present in `new_payload` that was NOT present, in the
    SAME bucket, in `old_payload` — i.e. a genuinely new addition or a
    moved-bucket ticker, never a pure reorder/recase (both payloads are
    canonicalized first).

    Used to scope the App Settings save flow's provider ticker-existence
    validation (Q2: "only validate tickers that actually changed... don't
    re-validate all ~200 on every save") to just the deltas, not the whole
    roster on every save.
    """
    old_c = canonicalize(old_payload)
    new_c = canonicalize(new_payload)
    out: set[str] = set()
    for bucket, tickers in new_c.items():
        old_bucket = set(old_c.get(bucket, []))
        for t in tickers:
            if t not in old_bucket:
                out.add(t)
    return out


def classify_ticker_resolution(
    tickers: "set[str]",
    prices: "dict | None",
    provider_health_red: bool,
) -> dict:
    """Classify the result of a provider ticker-existence check
    (`stock_analyzer.data.fetch_live_prices(list(tickers))`) for the
    App Settings save flow's changed tickers.

    `prices` is that call's raw return value, or `None` if the call itself
    raised (defensive — `fetch_live_prices` is documented never to raise,
    but the caller should still wrap it).

    IMPORTANT, disclosed limitation: the provider layer
    (`stock_analyzer.providers.orchestrator.get_live_prices`) has NO signal
    that distinguishes "every provider is down" from "every one of these
    tickers genuinely doesn't exist" — both return an empty `{}`. Rather
    than silently pick one interpretation, this function uses the ALREADY
    -TRACKED `stock_analyzer.api_health.overall_level()` reading as a
    disclosed heuristic: if `prices` is `None`, OR every ticker checked came
    back unresolved AND the overall provider health is already reporting
    red, classify as `validator_offline` (the Q2 offline-contract-applied-
    to-the-validator rule) rather than confidently reporting every single
    changed ticker as non-existent. This is a heuristic on an existing
    signal, not a new investment threshold.

    Returns `{"validator_offline": bool, "unresolved": [str, ...]}` (sorted).
    """
    if not tickers:
        return {"validator_offline": False, "unresolved": []}
    if prices is None:
        return {"validator_offline": True, "unresolved": []}

    unresolved = sorted(
        t for t in tickers
        if not (prices.get(t).get("price") if prices.get(t) else None)
    )
    if unresolved and len(unresolved) == len(tickers) and provider_health_red:
        return {"validator_offline": True, "unresolved": []}
    return {"validator_offline": False, "unresolved": unresolved}


def history_delta(newer_payload: dict, older_payload: "dict | None") -> dict:
    """Diff two reference-table payloads for the ⚙️ App Settings history
    readout's "what changed" tags (per the mockup's history table).

    `older_payload=None` means `newer_payload` is the OLDEST row on file —
    nothing to diff against, so this returns the "initial capture" shape
    rather than reporting every ticker as newly "added" (which would be
    technically true but misleading — it wasn't a delta, it was the seed).

    Returns `{"added": sorted[str], "removed": sorted[str],
    "buckets_touched": sorted[str], "initial": bool}`. Both payloads are
    canonicalized first so a pure reorder/recase between two history rows
    (which can't actually happen — `save_reference_table` never writes a
    non-canonical payload — but a defensive habit worth keeping) never
    reports a false delta.
    """
    if older_payload is None:
        return {"added": [], "removed": [], "buckets_touched": [], "initial": True}
    newer_c = canonicalize(newer_payload)
    older_c = canonicalize(older_payload)
    newer_flat = {t for tickers in newer_c.values() for t in tickers}
    older_flat = {t for tickers in older_c.values() for t in tickers}
    added = sorted(newer_flat - older_flat)
    removed = sorted(older_flat - newer_flat)
    buckets: set[str] = set()
    for k in set(newer_c) | set(older_c):
        if set(newer_c.get(k, [])) != set(older_c.get(k, [])):
            buckets.add(k)
    return {
        "added": added, "removed": removed,
        "buckets_touched": sorted(buckets), "initial": False,
    }
