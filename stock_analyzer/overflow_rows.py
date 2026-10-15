"""Overflow-pick recommendation rows (grow-today-owns-buys D3/N5, P3).

Pure row-shaping, no I/O, no streamlit. An "overflow pick" is a name that
cleared every Grow Today check but was held back only by pacing (pick cap,
one-per-sector slot, mover cap). It is logged as rec_type="overflow_pick" so
its forward alpha can later be compared with that day's sized picks. It
carries NO sizing: the sizing columns are simply absent from the row, and
db.save_recommendations stores them NULL. `composite_score` is the gated,
trusted composite the pick was already ranked on.

Both writers (the interactive app and the cron scan lane) call this one
builder so the row shape cannot drift between them. Field names mirror
cron_runner._build_new_pick_rows.
"""
from __future__ import annotations

OVERFLOW_REC_TYPE = "overflow_pick"


def build_overflow_rec_rows(overflow_picks, rec_date, surfaced_at=None) -> list[dict]:
    """Shape `grow_today["overflow_picks"]` into save_recommendations rows.

    `None`/empty input -> []. Items without a ticker are skipped. The input
    list and its dicts are never mutated. `rec_date` is passed through as
    given (the writer normalizes date objects and ISO strings). `surfaced_at`
    is accepted for interface symmetry only and is NOT persisted:
    db.save_recommendations never sends it, the DB stamps it server-side.
    """
    if not overflow_picks:
        return []
    rows: list[dict] = []
    for p in overflow_picks:
        if not isinstance(p, dict):
            continue
        tk = p.get("ticker")
        if not tk:
            continue
        rows.append({
            "ticker":           str(tk),
            "rec_date":         rec_date,
            "rec_type":         OVERFLOW_REC_TYPE,
            "surfaced_at":      surfaced_at,
            "price_at_surface": p.get("price"),
            "composite_score":  p.get("composite_score"),
            "momentum_score":   p.get("momentum_score"),
            "sector":           p.get("sector") or "",
            "verdict":          p.get("verdict") or "",
            "asset_type":       "stock",   # overflow is stocks-only
            "hold_reason":      p.get("reason"),
        })
    return rows
