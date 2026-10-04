"""
ETF new-pick candidate resolution — ETF-support Phase 2b
(`docs/plans/etf-multi-asset-support.md` "Phase 2b" section, owner-approved
2026-09-28). Deliberately a NEW module, not grown inside `daily_briefing.py`
or `etf_scoring.py`, so it carries its own `_GATE_FILES` review history
(matches the established `outage_gate.py`/`coord_freshness.py` pattern).

Phase 2 (`etf_scoring.py`) gave an EXISTING/HELD ETF a real composite verdict.
This module answers the next question — can an ETF become a NEW Grow Today
buy candidate at all — which Phase 2 deliberately left closed. Three owner
decisions are encoded here, baked in exactly as approved, never re-derived:

  D-M (macro scope): block an ETF new-pick on ANY imminent HIGH-impact macro
      event with a non-empty affected-sector set, not just the "__ALL__"
      (FOMC/GDP) sentinel. Same bar the stock new-pick path already applies.
  D-A (unknown AUM): fail CLOSED. `total_assets is None`/NaN at pick time is
      "fund size unknown", not "assume fine" — the opposite default from
      `etf_scoring.etf_aum_thin`'s own awareness-only fail-open-on-unknown
      posture, because this is a different, stricter, hard NEW-PICK gate.
  D-G (same-index duplicates): if ANY member of a registry group (e.g.
      "Broad Market": SPY/VOO/IVV) is already held, every OTHER member of
      that group is screened out too — never suggest "buy VOO" while SPY is
      held.

Pure functions only — no Streamlit/DB imports, mirrors `etf_scoring.py`'s
own style.
"""
from __future__ import annotations

import math

from stock_analyzer.constants import (
    COMPOSITE_STRONG_BUY,
    ETF_AUM_THIN_FLOOR_USD,
)
from stock_analyzer import asset_type as _asset_type_mod


def resolve_etf_candidates(registry_payload: "dict | None", held_tickers) -> "list[dict] | None":
    """Resolve the `etf_registry` reference-table payload + currently-held
    tickers into the day's ETF new-pick candidate list.

    `registry_payload`: {group_name: [ticker, ...]} — the `etf_registry`
        reference-table payload (owner-editable via ⚙️ App Settings).
        `None` means the registry is offline/unseeded — the caller must
        distinguish "not evaluated this session" (`None`) from "evaluated,
        nothing eligible" (`[]`). Returns `None` right back in that case.
    `held_tickers`: iterable of currently-held tickers, any case.

    Returns a list of two kinds of dict (never mixed into one shape so a
    consumer can branch on `kind`):
      - normal candidate:  {"ticker": <UPPER>, "group": <group name>}
      - D-G screen-out:    {"ticker": <UPPER>, "group": <group name>,
                            "kind": "held_group",
                            "reason": "already hold <held_ticker> (same
                                       <group> exposure)"}

    D-G semantics: a group is skipped ENTIRELY — every OTHER member becomes
    a "held_group" entry — the moment ANY of its members (case-insensitive)
    is already held. The held ticker itself is never a candidate (it isn't
    "new" by definition) and never gets its own "held_group" entry (it's
    simply absent from the output, same as any other held ticker would be).

    Tickers are deduped across groups (a ticker appearing in two groups
    yields at most one candidate/screen-out entry, first group wins by
    payload iteration order — registry groups are not expected to overlap
    in practice, but this keeps the output well-formed if they ever do).
    """
    if registry_payload is None:
        return None

    held_upper = {str(t).strip().upper() for t in (held_tickers or [])}
    out: list[dict] = []
    seen_tickers: set[str] = set()

    for group_name, members in (registry_payload or {}).items():
        member_list = [str(m).strip().upper() for m in (members or []) if str(m).strip()]
        if not member_list:
            continue
        held_members = [m for m in member_list if m in held_upper]
        if held_members:
            # D-G: skip every OTHER member of this group. The held member(s)
            # themselves are not candidates (already owned) and are not
            # emitted as a "held_group" screen-out either — only the NOT-held
            # siblings get that treatment.
            _held_name = held_members[0]
            for m in member_list:
                if m in held_upper:
                    continue
                if m in seen_tickers:
                    continue
                seen_tickers.add(m)
                out.append({
                    "ticker": m,
                    "group":  group_name,
                    "kind":   "held_group",
                    "reason": f"already hold {_held_name} (same {group_name} exposure)",
                })
            continue

        for m in member_list:
            if m in seen_tickers:
                continue
            seen_tickers.add(m)
            out.append({"ticker": m, "group": group_name})

    return out


def etf_newpick_eligible(bundle: "dict | None", tone: str) -> "tuple[bool, str | None]":
    """Is `bundle` (a `bundle_loader.load_bundle` result for an ETF registry
    candidate) eligible to become a NEW Grow Today pick today?

    Checks run in this exact order, first failure wins — data-integrity
    checks before policy checks, matching the stock new-pick path's own
    ordering convention (staleness/fundamentals/composite gates all run
    before the sector-unknown abstention there too):

      1. bundle falsy                          -> "ETF data unavailable"
      2. asset_type != ETF                      -> "not classified as an ETF"
      3. stale_as_of is not None                -> "prices served from stale cache"
      4. not etf_available                      -> "expense ratio unknown"
      5. etf_total missing/non-finite           -> "ETF composite unavailable"
      6. total_assets missing/NaN (D-A)         -> "fund size unknown"
         total_assets < ETF_AUM_THIN_FLOOR_USD  -> "AUM below floor"
         (strict "<", mirrors etf_aum_thin — a value exactly AT the floor passes)
      7. tone != "bull"                         -> "bull days only"
      8. etf_total < COMPOSITE_STRONG_BUY       -> "composite {x} below {y}"
      9. otherwise                              -> (True, None)
    """
    if not bundle:
        return False, "ETF data unavailable"

    if bundle.get("asset_type") != _asset_type_mod.ASSET_TYPE_ETF:
        return False, "not classified as an ETF"

    if bundle.get("stale_as_of") is not None:
        return False, "prices served from stale cache"

    if not bundle.get("etf_available"):
        return False, "expense ratio unknown"

    etf_total = bundle.get("etf_total")
    if etf_total is None:
        return False, "ETF composite unavailable"
    try:
        etf_total = float(etf_total)
    except (TypeError, ValueError):
        return False, "ETF composite unavailable"
    if not math.isfinite(etf_total):
        return False, "ETF composite unavailable"

    # Explicit is-None check (not `... or {}`) -- etf_facts legitimately being
    # None (a real ETF whose facts fetch failed) must fail closed to "unknown"
    # here, same outcome either way, but written as an explicit check to stay
    # clear of the OFFLINE_SENTINEL_COLLAPSE antipattern shape.
    _etf_facts = bundle.get("etf_facts")
    total_assets = _etf_facts.get("total_assets") if _etf_facts is not None else None
    if total_assets is None:
        return False, "fund size unknown"
    try:
        total_assets = float(total_assets)
    except (TypeError, ValueError):
        return False, "fund size unknown"
    if total_assets != total_assets:  # NaN
        return False, "fund size unknown"
    if total_assets < ETF_AUM_THIN_FLOOR_USD:
        return False, "AUM below floor"

    if tone != "bull":
        return False, "bull days only"

    if etf_total < COMPOSITE_STRONG_BUY:
        return False, f"composite {etf_total:.1f} below {COMPOSITE_STRONG_BUY}"

    return True, None


def etf_macro_block_reason(macro_blocked_sectors: set, macro_block_reasons: dict) -> "str | None":
    """[D-M] Decide on SET MEMBERSHIP only, never on whether a reason string
    happens to be truthy — the stock new-pick path's own `if _macro_block:`
    check fails OPEN if the resolved reason text is empty; this function must
    fail CLOSED instead (a non-empty blocked-sector set always blocks,
    regardless of whether a human-readable reason was recorded for it).

    - `"__ALL__"` in `macro_blocked_sectors` -> the FOMC/GDP all-sector
      sentinel; returns the recorded reason, or a generic fallback if the
      reason text is itself empty/missing.
    - `macro_blocked_sectors` non-empty (no `"__ALL__"`) -> a deterministic
      sector-keyed event (e.g. CPI/NFP-class); returns the reason for the
      alphabetically-first blocked sector if recorded, else a generic
      fallback naming every blocked sector.
    - empty set -> None (no macro block).
    """
    if "__ALL__" in (macro_blocked_sectors or set()):
        return (macro_block_reasons or {}).get("__ALL__") or "imminent all-sector macro event"

    if macro_blocked_sectors:
        _sorted = sorted(macro_blocked_sectors)
        _first = _sorted[0]
        _reason = (macro_block_reasons or {}).get(_first)
        if _reason:
            return _reason
        return f"imminent high-impact macro event affecting {_sorted}"

    return None
