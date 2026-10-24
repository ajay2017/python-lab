"""G-32 C2 shadow capture: measure-only entry R:R on every Grow Today name.

Pure, no I/O. `shadow_measure` builds the per-item `entry_rr` field that
`daily_briefing._grow_today` attaches AFTER every routing decision; nothing
reads it except `build_shadow_rows`, which the scan cron persists to
`g32_shadow` so outcomes can be analysed later (docs/plans/grow-today-rr-gate.md).
It never routes, sizes, orders, adds or removes anything.
"""
from __future__ import annotations

import math

from stock_analyzer import entry_rr
from stock_analyzer.constants import RR_ENTRY_MIN


def _num(x):
    """float for a real finite number, else None (bool / str / NaN / inf -> None)."""
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def shadow_measure(bundle, lane_entry=None, lane_stop=None) -> dict:
    """Bundle-basis R:R (U1 default) plus the lane basis the card was sized on.

    Keys: rr, state, why, base_source, entry, stop, base (bundle basis) and
    lane_entry, lane_stop, rr_lane (the price / stop the lane sized with).
    No bundle -> state "unknown", why "no_bundle". Never raises on odd input.
    """
    out = dict(entry_rr.from_bundle(bundle))
    b = bundle if isinstance(bundle, dict) else {}
    targets = b.get("targets")
    base = targets.get("base") if isinstance(targets, dict) else None
    out["asset_type"] = b.get("asset_type")
    out["entry"] = _num(b.get("current_price"))
    out["stop"] = _num(b.get("stop"))
    out["base"] = _num(base)
    out["lane_entry"] = _num(lane_entry)
    out["lane_stop"] = _num(lane_stop)
    out["rr_lane"] = entry_rr.measure(lane_entry, lane_stop, base)["rr"]
    return out


_LANES = (
    # (grow key, lane label, composite field on that item)
    ("new_picks", "pick", "composite_score"),
    ("add_positions", "add", "score"),
    ("overflow_picks", "overflow", "composite_score"),
)


def build_shadow_rows(grow, rec_date, source: str) -> list[dict]:
    """One row per Grow Today pick / add / overflow item, for `g32_shadow`.

    A non-dict `grow` or a missing / non-list bucket yields no rows for it. An
    item without the `entry_rr` field still gets a row (rr None, state
    "unknown"). Deduped on (rec_date, lane, ticker), first kept.
    """
    if not isinstance(grow, dict):
        return []
    rows: list[dict] = []
    seen: set = set()
    for key, lane, comp_field in _LANES:
        items = grow.get(key)
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            tk = str(it.get("ticker") or "").strip().upper()
            if not tk or (rec_date, lane, tk) in seen:
                continue
            seen.add((rec_date, lane, tk))
            m = it.get("entry_rr")
            if not isinstance(m, dict):
                m = {}
            mover = it.get("is_mover")
            rows.append({
                "rec_date":    rec_date,
                "lane":        lane,
                "ticker":      tk,
                "source":      source,
                "entry":       m.get("entry"),
                "stop":        m.get("stop"),
                "base":        m.get("base"),
                "base_source": m.get("base_source"),
                "rr":          m.get("rr"),
                "rr_state":    m.get("state") or "unknown",
                "lane_entry":  m.get("lane_entry"),
                "lane_stop":   m.get("lane_stop"),
                "rr_lane":     m.get("rr_lane"),
                "composite":   it.get(comp_field),
                "is_mover":    bool(mover) if lane != "add" and mover is not None else None,
                "asset_type":  it.get("asset_type") or m.get("asset_type") or "stock",
                "rr_min":      RR_ENTRY_MIN,
            })
    return rows
