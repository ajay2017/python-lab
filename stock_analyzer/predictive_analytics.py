"""
Predictive Analytics — signal calibration helpers (Option A).

Pure functions only; no Streamlit imports. All inputs are plain Python
lists/dicts produced by recommendations_history.compute_outcomes().

Called lazily from the "📊 Predictive Analytics" page in app.py.
"""
from __future__ import annotations

from typing import Any


# ── Signal Calibration ─────────────────────────────────────────────────────────

def calibration_by_score_band(
    enriched: list[dict],
    band_size: int = 5,
) -> list[dict]:
    """
    Fine-grained calibration: group mature, alpha-priced outcomes into
    ``band_size``-point composite-score intervals.

    Only rows where ``outcome_maturing`` is False AND ``alpha_pct`` is not None
    are included — the same population the Recommendations History scorecard
    counts as "graded."

    Returns a list of band dicts sorted by ``band_floor`` ascending. Each dict:

    band_floor        int   — lower bound of the interval (inclusive)
    band_label        str   — e.g. "65–69"
    n                 int   — graded rows in this band
    n_acted           int
    n_missed          int
    p_positive_alpha  float | None  — proportion where alpha_pct > 0
    avg_alpha         float | None  — mean alpha across all (acted + missed)
    avg_alpha_acted   float | None
    avg_alpha_missed  float | None
    avg_outcome_pct   float | None  — raw outcome (not SPY-adjusted)
    """
    buckets: dict[int, dict] = {}

    for r in enriched:
        if r.get("outcome_maturing"):
            continue
        alpha = r.get("alpha_pct")
        if alpha is None:
            continue
        try:
            score = float(r["composite_score"])
            floor = int((score // band_size) * band_size)
        except (TypeError, ValueError):
            continue
        if floor not in buckets:
            buckets[floor] = {
                "band_floor":    floor,
                "band_label":    f"{floor}–{floor + band_size - 1}",
                "_all_alpha":    [],
                "_acted_alpha":  [],
                "_missed_alpha": [],
                "_outcome_pcts": [],
                "_n_acted":      0,
                "_n_missed":     0,
            }
        b = buckets[floor]
        b["_all_alpha"].append(float(alpha))
        op = r.get("outcome_pct")
        if op is not None:
            b["_outcome_pcts"].append(float(op))
        if r.get("acted_on"):
            b["_n_acted"] += 1
            b["_acted_alpha"].append(float(alpha))
        else:
            b["_n_missed"] += 1
            b["_missed_alpha"].append(float(alpha))

    rows = []
    for floor, b in sorted(buckets.items()):
        all_a = b["_all_alpha"]
        n     = len(all_a)
        aa    = b["_acted_alpha"]
        am    = b["_missed_alpha"]
        op    = b["_outcome_pcts"]
        rows.append({
            "band_floor":       floor,
            "band_label":       b["band_label"],
            "n":                n,
            "n_acted":          b["_n_acted"],
            "n_missed":         b["_n_missed"],
            "p_positive_alpha": round(sum(1 for a in all_a if a > 0) / n, 3) if n else None,
            "avg_alpha":        round(sum(all_a) / n, 2) if n else None,
            "avg_alpha_acted":  round(sum(aa) / len(aa), 2) if aa else None,
            "avg_alpha_missed": round(sum(am) / len(am), 2) if am else None,
            "avg_outcome_pct":  round(sum(op) / len(op), 2) if op else None,
        })
    return rows


def calibration_by_sector(
    enriched: list[dict],
    min_n: int = 3,
) -> dict[str, dict[str, Any]]:
    """
    Sector × broad score-band cross-tabulation for a heatmap.

    Uses three broad bands (< 65 / 65–74 / 75+) matching the engine's own
    tier labels so each cell has enough data points to be meaningful on a
    personal portfolio history.

    Returns::

        {
            "Technology": {
                "65–74": {"avg_alpha": 2.3, "n": 8},
                "75+":   {"avg_alpha": 5.1, "n": 4},
            },
            ...
        }

    Cells with n < ``min_n`` are omitted so the heatmap never shows an
    average built on a single data point.
    """
    def _broad(score: float) -> str:
        if score < 65:
            return "< 65"
        if score < 75:
            return "65–74"
        return "75+"

    acc: dict[str, dict[str, list[float]]] = {}

    for r in enriched:
        if r.get("outcome_maturing"):
            continue
        alpha = r.get("alpha_pct")
        if alpha is None:
            continue
        try:
            score = float(r["composite_score"])
            band  = _broad(score)
        except (TypeError, ValueError):
            continue
        sector = str(r.get("sector") or "Unknown").strip() or "Unknown"
        acc.setdefault(sector, {}).setdefault(band, []).append(float(alpha))

    result: dict[str, dict[str, Any]] = {}
    for sector, bands in sorted(acc.items()):
        for band, alphas in bands.items():
            n = len(alphas)
            if n < min_n:
                continue
            result.setdefault(sector, {})[band] = {
                "avg_alpha": round(sum(alphas) / n, 2),
                "n":         n,
            }
    return result


def personal_alpha_threshold(
    bands: list[dict],
    min_n: int = 5,
) -> int | None:
    """
    From ``calibration_by_score_band`` output, find the lowest ``band_floor``
    where every band at or above that floor satisfies BOTH:

    * n >= ``min_n``
    * p_positive_alpha >= 0.5

    This is the score level above which the engine has consistently delivered
    positive alpha in this user's personal history. Returns None when data
    is insufficient or no such threshold exists.
    """
    eligible = [
        b for b in sorted(bands, key=lambda x: x["band_floor"])
        if b["n"] >= min_n and b["p_positive_alpha"] is not None
    ]
    if not eligible:
        return None
    for i, b in enumerate(eligible):
        if all(x["p_positive_alpha"] >= 0.5 for x in eligible[i:]):
            return b["band_floor"]
    return None


def total_graded(enriched: list[dict]) -> int:
    """Count of mature rows that have a non-None alpha_pct — the working set
    for all calibration functions."""
    return sum(
        1 for r in enriched
        if not r.get("outcome_maturing") and r.get("alpha_pct") is not None
    )


# ── Decision Quality (Tab 2) ───────────────────────────────────────────────────

def acted_vs_missed_comparison(enriched: list[dict]) -> dict:
    """
    Compare alpha outcomes between recs you acted on vs recs you passed.

    Returns a dict with top-level summaries for each side plus the overall
    discretion verdict:

      acted   — {"n", "avg_alpha", "p_positive_alpha", "avg_outcome_pct"}
      missed  — {"n", "avg_alpha", "p_positive_alpha", "avg_outcome_pct"}
      edge    — "acting" | "passing" | "neutral" | "insufficient"
      edge_pp — float | None  (magnitude of the alpha gap, positive always)
    """
    def _side(rows: list[dict]) -> dict:
        alphas   = [float(r["alpha_pct"]) for r in rows if r.get("alpha_pct") is not None]
        outcomes = [float(r["outcome_pct"]) for r in rows if r.get("outcome_pct") is not None]
        n = len(rows)
        return {
            "n":                n,
            "avg_alpha":        round(sum(alphas) / len(alphas), 2) if alphas else None,
            "p_positive_alpha": round(sum(1 for a in alphas if a > 0) / len(alphas), 3) if alphas else None,
            "avg_outcome_pct":  round(sum(outcomes) / len(outcomes), 2) if outcomes else None,
        }

    graded = [r for r in enriched if not r.get("outcome_maturing") and r.get("alpha_pct") is not None]
    acted_rows  = [r for r in graded if r.get("acted_on")]
    missed_rows = [r for r in graded if not r.get("acted_on")]

    acted_stats  = _side(acted_rows)
    missed_stats = _side(missed_rows)

    aa = acted_stats["avg_alpha"]
    am = missed_stats["avg_alpha"]

    if aa is None or am is None or (acted_stats["n"] < 3 and missed_stats["n"] < 3):
        edge, edge_pp = "insufficient", None
    elif abs(aa - am) < 0.5:
        edge, edge_pp = "neutral", round(abs(aa - am), 2)
    elif aa > am:
        edge, edge_pp = "acting", round(aa - am, 2)
    else:
        edge, edge_pp = "passing", round(am - aa, 2)

    return {
        "acted":   acted_stats,
        "missed":  missed_stats,
        "edge":    edge,
        "edge_pp": edge_pp,
    }


# ── Signal Breakdown (Tab 3) ───────────────────────────────────────────────────

_REC_TYPE_LABELS = {
    "new_pick":      "New Position",
    "add_winner":    "Add to Winner",
    "buy_candidate": "Opportunity Watch",
}


def by_conviction(enriched: list[dict], min_n: int = 3) -> list[dict]:
    """
    Group mature graded outcomes by conviction level (BUY / Strong BUY / other).

    Returns list of dicts sorted by avg_alpha descending:
      conviction, n, avg_alpha, p_positive_alpha, avg_outcome_pct
    """
    acc: dict[str, list[dict]] = {}
    for r in enriched:
        if r.get("outcome_maturing") or r.get("alpha_pct") is None:
            continue
        conv = str(r.get("conviction") or "Unknown").strip() or "Unknown"
        acc.setdefault(conv, []).append(r)

    rows = []
    for conv, recs in acc.items():
        alphas   = [float(r["alpha_pct"]) for r in recs]
        outcomes = [float(r["outcome_pct"]) for r in recs if r.get("outcome_pct") is not None]
        n = len(alphas)
        rows.append({
            "conviction":       conv,
            "n":                n,
            "avg_alpha":        round(sum(alphas) / n, 2) if n else None,
            "p_positive_alpha": round(sum(1 for a in alphas if a > 0) / n, 3) if n else None,
            "avg_outcome_pct":  round(sum(outcomes) / len(outcomes), 2) if outcomes else None,
        })
    rows.sort(key=lambda x: (x["avg_alpha"] or -999), reverse=True)
    return [r for r in rows if r["n"] >= min_n]


def by_rec_type_stats(enriched: list[dict], min_n: int = 3) -> list[dict]:
    """
    Group mature graded outcomes by rec_type.

    Returns list of dicts sorted by avg_alpha descending:
      rec_type, label, n, avg_alpha, p_positive_alpha, avg_outcome_pct
    """
    acc: dict[str, list[dict]] = {}
    for r in enriched:
        if r.get("outcome_maturing") or r.get("alpha_pct") is None:
            continue
        rt = str(r.get("rec_type") or "unknown").strip()
        acc.setdefault(rt, []).append(r)

    rows = []
    for rt, recs in acc.items():
        alphas   = [float(r["alpha_pct"]) for r in recs]
        outcomes = [float(r["outcome_pct"]) for r in recs if r.get("outcome_pct") is not None]
        n = len(alphas)
        rows.append({
            "rec_type":         rt,
            "label":            _REC_TYPE_LABELS.get(rt, rt),
            "n":                n,
            "avg_alpha":        round(sum(alphas) / n, 2) if n else None,
            "p_positive_alpha": round(sum(1 for a in alphas if a > 0) / n, 3) if n else None,
            "avg_outcome_pct":  round(sum(outcomes) / len(outcomes), 2) if outcomes else None,
        })
    rows.sort(key=lambda x: (x["avg_alpha"] or -999), reverse=True)
    return [r for r in rows if r["n"] >= min_n]


# ── Sector Alpha (Tab 4) ───────────────────────────────────────────────────────

def by_sector_alpha(enriched: list[dict], min_n: int = 3) -> list[dict]:
    """
    Group mature graded outcomes by sector regardless of score band.

    Returns list of dicts sorted by avg_alpha descending:
      sector, n, avg_alpha, p_positive_alpha, avg_outcome_pct
    """
    acc: dict[str, list[dict]] = {}
    for r in enriched:
        if r.get("outcome_maturing") or r.get("alpha_pct") is None:
            continue
        sector = str(r.get("sector") or "Unknown").strip() or "Unknown"
        acc.setdefault(sector, []).append(r)

    rows = []
    for sector, recs in acc.items():
        alphas   = [float(r["alpha_pct"]) for r in recs]
        outcomes = [float(r["outcome_pct"]) for r in recs if r.get("outcome_pct") is not None]
        n = len(alphas)
        rows.append({
            "sector":           sector,
            "n":                n,
            "avg_alpha":        round(sum(alphas) / n, 2) if n else None,
            "p_positive_alpha": round(sum(1 for a in alphas if a > 0) / n, 3) if n else None,
            "avg_outcome_pct":  round(sum(outcomes) / len(outcomes), 2) if outcomes else None,
        })
    rows.sort(key=lambda x: (x["avg_alpha"] or -999), reverse=True)
    return [r for r in rows if r["n"] >= min_n]
