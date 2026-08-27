"""Grow Today transparency helpers — firmness badge and drop-off trace.

Pure logic: no Streamlit, no DB, no API calls. All inputs are supplied by the
caller (app.py), which assembles them from session state and the Grow Today
grow_buckets dict. The output dicts deliberately carry NO sizing/entry/stop/
shares keys — the trace is structurally incapable of rendering as a buy signal.
"""
from __future__ import annotations

from stock_analyzer.constants import COMPOSITE_BUY, COMPOSITE_STRONG_BUY


def firmness(composite: float, tier_floor: float, margin: float) -> str:
    """Return 'at_line' when (composite - tier_floor) <= margin, else 'well_clear'.

    The boundary is inclusive: a pick whose composite is exactly (tier_floor +
    margin) is still 'at_line'. Used to badge picks that are clearing the entry
    bar by a slim margin — a routine intraday move could push the composite
    below the floor.

    Args:
        composite:  The pick's current composite score (0–100).
        tier_floor: The tier boundary the pick sits above (e.g. 65 or 75).
        margin:     Band width above the floor (constants.COMPOSITE_FIRMNESS_MARGIN).

    Returns:
        'at_line' or 'well_clear'.
    """
    return "at_line" if (composite - tier_floor) <= margin else "well_clear"


def tier_floor_for(composite: float) -> float:
    """Return the tier floor the pick currently sits above.

    composite >= COMPOSITE_STRONG_BUY (75) → floor 75 (Strong Buy line).
    composite >= COMPOSITE_BUY (65)        → floor 65 (Buy line).

    Callers should only pass a composite that already cleared COMPOSITE_BUY —
    the result is undefined for composites below that floor.
    """
    if composite >= COMPOSITE_STRONG_BUY:
        return float(COMPOSITE_STRONG_BUY)
    return float(COMPOSITE_BUY)


def derive_dropoffs(
    surfaced_today: list[dict],
    current_new_pick_tickers: set[str],
    grow_buckets: dict,
    reduce_calls: dict | None,
    acted_or_held_tickers: set[str],
) -> list[dict]:
    """Identify picks that surfaced today as new_pick but are no longer clearing.

    Scoped to new_pick only (spec constraint) — buy_candidate and add_winner
    are intentionally excluded.

    Parameters
    ----------
    surfaced_today:
        One dict per ticker that surfaced today as a new_pick. Each dict must
        carry ``ticker``, ``first_seen_at``, and ``composite_at_surface``.
        Caller must pass the EARLIEST-surfaced row per ticker (min by
        surfaced_at). All ticker strings are normalised to uppercase internally.
    current_new_pick_tickers:
        Uppercased set of tickers still showing as new_picks this pass.
    grow_buckets:
        The Grow Today output dict (from daily_briefing._grow_today()). Keys
        read: ``composite_skipped``, ``sector_blocked_picks``,
        ``macro_blocked_picks``, ``composite_unavailable``.
    reduce_calls:
        Dict of {ticker_upper: item} from session_state["_reduce_calls"].
        Pass None when the Brief was offline — the None path falls through to
        the next reason in the priority chain (no crash, no fabricated reason).
    acted_or_held_tickers:
        Uppercased union of today's BUY trades and current holdings tickers. A
        ticker present here is excluded from the dropped set — the user already
        acted on it or holds it, so its absence from new_picks is expected, not
        a drop.

    Returns
    -------
    list[dict]
        One entry per dropped ticker, sorted ascending by ticker. Each dict
        carries:
            ticker               (str)
            first_seen_at        (str | None) — surfaced_at from the log row
            composite_at_surface (float | None)
            current_composite    (float | None) — from composite_skipped if present
            reason_code          (str)  — one of:
                                    reduce_call | composite_below_bar |
                                    sector_blocked | macro_blocked |
                                    composite_unavailable | unattributed
            reason_text          (str)  — human-readable sentence
            has_confident_reason (bool) — False only on 'unattributed'

        Deliberately NO sizing/entry/stop/shares keys.
    """
    # Build lookup maps from grow_buckets, keyed by uppercase ticker.
    # Each bucket is a list in the grow_today output; `.get(k, [])` guards a
    # missing key. These are internal buckets built fresh each pass (never an
    # offline session-cache sentinel), so an absent bucket legitimately means
    # "no names here" — there is no None-vs-empty distinction to preserve.
    _comp_skip_map: dict[str, dict] = {}
    for _item in grow_buckets.get("composite_skipped", []):
        _t = str(_item.get("ticker", "")).upper()
        if _t:
            _comp_skip_map[_t] = _item

    _sector_blocked_map: dict[str, dict] = {}
    for _item in grow_buckets.get("sector_blocked_picks", []):
        _t = str(_item.get("ticker", "")).upper()
        if _t:
            _sector_blocked_map[_t] = _item

    _macro_blocked_map: dict[str, dict] = {}
    for _item in grow_buckets.get("macro_blocked_picks", []):
        _t = str(_item.get("ticker", "")).upper()
        if _t:
            _macro_blocked_map[_t] = _item

    _comp_unavail_set: set[str] = {
        str(_item.get("ticker", "")).upper()
        for _item in grow_buckets.get("composite_unavailable", [])
        if _item.get("ticker")
    }

    # Build deduplicated surfaced-today map: earliest row per ticker.
    # Caller is expected to pass min-by-surfaced_at rows, but this layer also
    # guards: if two rows arrive for the same ticker, keep the earlier one.
    _surfaced_map: dict[str, dict] = {}
    for _row in surfaced_today:
        _t = str(_row.get("ticker", "")).upper()
        if not _t:
            continue
        if _t not in _surfaced_map:
            _surfaced_map[_t] = _row
        else:
            _existing_ts = str(_surfaced_map[_t].get("first_seen_at") or "")
            _new_ts = str(_row.get("first_seen_at") or "")
            if _new_ts and _new_ts < _existing_ts:
                _surfaced_map[_t] = _row

    # Normalise input sets to uppercase for case-insensitive comparison.
    _upper_current = {str(_t).upper() for _t in current_new_pick_tickers}
    _upper_acted   = {str(_t).upper() for _t in acted_or_held_tickers}

    # Dropped = surfaced today, but not currently showing AND not acted/held.
    _dropped_tickers = set(_surfaced_map.keys()) - _upper_current - _upper_acted

    result: list[dict] = []
    for _t in sorted(_dropped_tickers):
        _row = _surfaced_map[_t]
        _first_seen_at       = _row.get("first_seen_at")
        _composite_at_surf   = _row.get("composite_at_surface")
        _current_composite: float | None = None
        _reason_code         = "unattributed"
        _reason_text         = ""
        _has_confident       = False

        # Reason attribution priority:
        # reduce_calls → composite_skipped → sector_blocked_picks
        # → macro_blocked_picks → composite_unavailable → unattributed
        if reduce_calls is not None and _t in reduce_calls:
            _reason_code   = "reduce_call"
            _reason_text   = f"a Reduce/Exit call is now active on {_t}"
            _has_confident = True

        elif _t in _comp_skip_map:
            _ci  = _comp_skip_map[_t]
            _cur_c = _ci.get("composite_score")
            _cur_l = _ci.get("composite_label", "")
            _current_composite = _cur_c
            _parts: list[str] = []
            if _cur_c is not None:
                _parts.append(f"composite now {_cur_c:.0f}/100")
            if _cur_l:
                _parts.append(f"({_cur_l})")
            _reason_code   = "composite_below_bar"
            _reason_text   = (
                "composite re-priced below the entry bar: "
                + (" ".join(_parts) if _parts else "below Buy threshold")
            )
            _has_confident = True

        elif _t in _sector_blocked_map:
            _si = _sector_blocked_map[_t]
            _reason_code   = "sector_blocked"
            _reason_text   = _si.get("reason", "sector concentration cap reached")
            _has_confident = True

        elif _t in _macro_blocked_map:
            _mi = _macro_blocked_map[_t]
            _reason_code   = "macro_blocked"
            _reason_text   = _mi.get("reason", "imminent macro event suppression")
            _has_confident = True

        elif _t in _comp_unavail_set:
            _reason_code   = "composite_unavailable"
            _reason_text   = "fundamentals momentarily unavailable this pass"
            _has_confident = True

        else:
            _reason_code   = "unattributed"
            # Deliberately NO timestamp in this sentence. The render layer
            # already prints first_seen_at two clauses earlier, formatted to
            # ET by _fmt_first_seen(). Interpolating the raw field repeated
            # the SAME instant as an unformatted UTC ISO string with
            # microseconds, directly beside its own ET rendering - so it read
            # as a second, later event, and broke the app-wide ET convention
            # for user-facing dates. Found on a live screenshot 2026-08-26.
            _reason_text   = (
                f"no longer clearing this pass — its momentum or composite "
                f"re-priced below the entry bar; "
                f"re-run Refresh Signals for a fresh pass."
            )
            _has_confident = False

        result.append({
            "ticker":               _t,
            "first_seen_at":        _first_seen_at,
            "composite_at_surface": _composite_at_surf,
            "current_composite":    _current_composite,
            "reason_code":          _reason_code,
            "reason_text":          _reason_text,
            "has_confident_reason": _has_confident,
        })

    return result
