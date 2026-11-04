"""The macro-event sector gate's one decision (docs/plans/macro-sector-gate.md, C2).

``decide`` says, for one candidate sector against the macro block map
(``entry_eligibility.macro_block_map``), whether the entry is blocked by an
imminent HIGH-impact event, clear, unknown (the sector can't be matched, so the
check couldn't run: HOLD, owner ruling 2026-11-04), or unchecked (no usable map).

The block set holds CURATED labels only, so a provider label is translated with
``sector_vocab.to_curated`` first; membership is tested before anything else, so
translation can only ever add a block, never remove one. Every wording the user
reads for the unknown branch is built here; app.py and the cron only render it.

Pure: no streamlit, no db, no clock.
"""
from __future__ import annotations

from stock_analyzer.sector_vocab import is_not_a_sector, to_curated

# Event wording when the block map names the sector set but no reason string
# (the hold is decided on SET MEMBERSHIP, never on reason truthiness).
_GENERIC_BLOCK_REASON = "imminent HIGH-impact macro event"
_GENERIC_EVENT_PHRASE = "an imminent HIGH-impact macro event"

UNCHECKED_NO_MAP = "macro calendar unavailable — the macro-event check couldn't run"
UNCHECKED_MALFORMED = "macro calendar unreadable — the macro-event check couldn't run"

BASIS_UNCLASSIFIED = "unclassified"
BASIS_MACRO_UNKNOWN = "macro_unknown"


def _raw_label(sector) -> "str | None":
    """The stripped provider label, or None when there is none, it is a
    placeholder, or it is the app's own "Other" (none of those is a label the
    data provider gave)."""
    if sector is None or is_not_a_sector(sector):
        return None
    return str(sector).strip()


def _first_event_reason(sectors, reasons) -> str:
    """The alphabetically-first blocked sector's reason, else the generic."""
    for s in sorted(str(x) for x in sectors):
        r = reasons.get(s)
        if r:
            return str(r)
    return _GENERIC_EVENT_PHRASE


def unknown_reason(label, event_reason: str, *, is_etf: bool = False) -> str:
    """Why an unknown-sector name is held: true on every path."""
    if is_etf:
        return (f"an ETF has no single sector in the app's map, so it's held while "
                f"{event_reason} (the same rule Grow Today uses for ETF picks)")
    if label:
        return (f"sector unknown — “{label}” isn't on the app's sector map, so the "
                f"macro-event check couldn't run while {event_reason}")
    return ("sector unknown — no sector label from the data provider, so the "
            f"macro-event check couldn't run while {event_reason}")


def unknown_clears_when(*, is_etf: bool = False) -> str:
    if is_etf:
        return "the release is out"
    return ("the release is out, or the ticker gets a sector on the app's curated "
            "map that the event doesn't affect")


def decide(sector, block_map, *, is_etf: bool = False) -> dict:
    """``{"state", "reason", "event_reason", "label"}``.

    state: "unchecked" (the map is missing or malformed), "blocked" (the sector
    is in the imminent-event set, or the event is market-wide), "clear",
    or "unknown" (the sector can't be matched against a non-empty block set).
    ``reason`` is the user-facing sentence for unchecked / unknown (None
    otherwise); ``event_reason`` the event text for blocked / unknown; ``label``
    the raw provider label on the unknown branch (None when there was none)."""
    out = {"state": "clear", "reason": None, "event_reason": None, "label": None}
    if block_map is None:
        out.update(state="unchecked", reason=UNCHECKED_NO_MAP)
        return out
    if not isinstance(block_map, dict) or block_map.get("sectors") is None:
        out.update(state="unchecked", reason=UNCHECKED_MALFORMED)
        return out
    sectors = block_map["sectors"]
    reasons = block_map.get("reasons")
    reasons = {} if reasons is None else reasons

    if "__ALL__" in sectors:
        out.update(state="blocked",
                   event_reason=reasons.get("__ALL__") or _GENERIC_BLOCK_REASON)
        return out

    # Either label blocks, so translation can only ever ADD a block, even if a
    # raw provider or bucket label is ever keyed into the impact map.
    raw = None if sector is None else str(sector).strip()
    for s in (to_curated(sector), raw):
        if s and s in sectors:
            out.update(state="blocked", event_reason=reasons.get(s) or _GENERIC_BLOCK_REASON)
            return out

    label = _raw_label(sector)
    if is_etf and sectors:
        ev = _first_event_reason(sectors, reasons)
        out.update(state="unknown", event_reason=ev, label=label,
                   reason=unknown_reason(label, ev, is_etf=True))
        return out
    if not sectors:
        return out
    if to_curated(sector) is not None:
        return out
    ev = _first_event_reason(sectors, reasons)
    out.update(state="unknown", event_reason=ev, label=label,
               reason=unknown_reason(label, ev))
    return out


def sector_unknown_banner(items) -> "str | None":
    """The Grow Today 🧭 banner line for held-back unknown-sector picks, each
    basis worded truthfully: an "Other" name couldn't have either check run; a
    ``macro_unknown`` name's sector ceiling DID run, only the macro-event check
    couldn't. None when there are no items."""
    rows = [b for b in (items if items is not None else []) if isinstance(b, dict)]
    if not rows:
        return None
    other = [str(b.get("ticker", "?")) for b in rows
             if b.get("basis") != BASIS_MACRO_UNKNOWN]
    macro = [str(b.get("ticker", "?")) for b in rows
             if b.get("basis") == BASIS_MACRO_UNKNOWN]
    n = len(rows)
    if not macro:
        return (f"🧭 Sector unknown — {n} pick(s) held back: "
                "the sector ceiling and macro sector checks couldn't run, so "
                f"these aren't recommended today: {', '.join(other)}")
    parts = []
    if other:
        parts.append(f"{', '.join(other)} (the sector ceiling and macro sector "
                     "checks couldn't run)")
    parts.append(f"{', '.join(macro)} (the macro-event check couldn't run on "
                 "their sector label)")
    return (f"🧭 Sector unknown — {n} pick(s) held back, so these aren't "
            f"recommended today: {'; '.join(parts)}")
