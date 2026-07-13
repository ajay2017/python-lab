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


def synthesize_directives(
    bands: list[dict],
    thresh: int | None,
    avm: dict,
    conv: list[dict],
    rtype: list[dict],
    sec_alph: list[dict],
    n_graded: int,
    min_n: int = 5,
) -> list[dict]:
    """
    Synthesize 2–5 ranked directives from all model outputs.

    Reads across score calibration, decision quality, sector alpha, and
    signal breakdown to produce concrete, actionable guidance — even when
    data is thin (in that case, directives tell you what to *watch for*
    rather than what to act on now).

    Each directive dict:
        type       — "action" | "caution" | "watch" | "context"
        text       — 1–2 sentences, plain English
        source_tab — which tab holds the supporting evidence

    Ordered: action → caution → watch → context.
    """
    directives: list[dict] = []

    # ── Score Calibration ──────────────────────────────────────────────────────
    thick_bands = [b for b in bands if b["n"] >= min_n]
    all_neg     = thick_bands and all((b["avg_alpha"] or 0) <= 0 for b in thick_bands)

    if thresh is not None:
        directives.append({
            "type": "action",
            "text": (
                f"Your alpha turns consistently positive at composite ≥ {thresh}. "
                f"Treat sub-{thresh} recs as speculative — consider reducing size or skipping."
            ),
            "source_tab": "🎯 Score Calibration",
        })
    elif all_neg:
        directives.append({
            "type": "watch",
            "text": (
                "All score bands are showing negative alpha vs SPY right now. "
                "In a persistent bull market, beating SPY is a high bar — this "
                "reflects the regime, not necessarily engine failure. "
                "Don't tune thresholds down; watch for the first band to flip green "
                "as conditions shift."
            ),
            "source_tab": "🎯 Score Calibration",
        })
    elif any((b["avg_alpha"] or 0) > 0 for b in thick_bands):
        directives.append({
            "type": "watch",
            "text": (
                "Positive alpha appears in some score bands but not consistently "
                "enough to confirm a personal threshold yet. "
                "Sector and decision-quality patterns are more actionable than score alone right now."
            ),
            "source_tab": "🎯 Score Calibration",
        })

    # ── Decision Quality ───────────────────────────────────────────────────────
    edge    = avm.get("edge", "insufficient")
    edge_pp = avm.get("edge_pp")

    if edge == "acting" and edge_pp is not None and edge_pp >= 0.5:
        directives.append({
            "type": "action",
            "text": (
                f"Your discretion is adding {edge_pp:.1f}pp of alpha — you're filtering "
                f"signal from noise effectively. Don't feel pressure to act on every signal; "
                f"your selectivity is working."
            ),
            "source_tab": "⚖️ Decision Quality",
        })
    elif edge == "passing" and edge_pp is not None and edge_pp >= 0.5:
        directives.append({
            "type": "caution",
            "text": (
                f"Following every engine signal would have added {edge_pp:.1f}pp more alpha "
                f"than your current act rate. Review what's making you pass — the engine "
                f"may be seeing something you're discounting."
            ),
            "source_tab": "⚖️ Decision Quality",
        })
    elif edge in ("neutral", "insufficient"):
        directives.append({
            "type": "context",
            "text": (
                "Your act/pass decisions are producing similar alpha to passing on everything. "
                "Discretion isn't adding or removing measurable edge yet — "
                "use score and sector patterns as your primary guide for now."
            ),
            "source_tab": "⚖️ Decision Quality",
        })

    # ── Sector Alpha ───────────────────────────────────────────────────────────
    if sec_alph:
        best  = sec_alph[0]
        worst = sec_alph[-1]

        if (best["avg_alpha"] or 0) > 0:
            directives.append({
                "type": "action",
                "text": (
                    f"Your strongest sector is {best['sector']} "
                    f"({best['avg_alpha']:+.1f}pp avg alpha, n={best['n']}). "
                    f"When composites are borderline, prioritize recs here — "
                    f"this is where the engine's signal has worked best for you."
                ),
                "source_tab": "🌐 Sector Alpha",
            })
        else:
            directives.append({
                "type": "watch",
                "text": (
                    "No sector shows consistently positive alpha yet. "
                    "As history grows, sector patterns will be the first reliable "
                    "edge to emerge — check back each quarter."
                ),
                "source_tab": "🌐 Sector Alpha",
            })

        if len(sec_alph) > 1 and (worst["avg_alpha"] or 0) < -3:
            directives.append({
                "type": "caution",
                "text": (
                    f"Recs in {worst['sector']} have cost the most alpha "
                    f"({worst['avg_alpha']:+.1f}pp avg, n={worst['n']}). "
                    f"Be more skeptical of engine signals here until the pattern reverses."
                ),
                "source_tab": "🌐 Sector Alpha",
            })

    # ── Signal Breakdown ───────────────────────────────────────────────────────
    if len(rtype) >= 2:
        rt_best, rt_worst = rtype[0], rtype[-1]
        if (rt_best["avg_alpha"] is not None and rt_worst["avg_alpha"] is not None
                and rt_best["avg_alpha"] - rt_worst["avg_alpha"] >= 1.0):
            directives.append({
                "type": "action",
                "text": (
                    f"{rt_best['label']} recs outperform {rt_worst['label']} "
                    f"by {rt_best['avg_alpha'] - rt_worst['avg_alpha']:.1f}pp. "
                    f"Lean into {rt_best['label']} signals — that's where your alpha edge is strongest."
                ),
                "source_tab": "🏷️ Signal Breakdown",
            })

    if len(conv) >= 2:
        cv_best, cv_worst = conv[0], conv[-1]
        if (cv_best["avg_alpha"] is not None and cv_worst["avg_alpha"] is not None
                and cv_best["avg_alpha"] - cv_worst["avg_alpha"] >= 1.5):
            directives.append({
                "type": "action",
                "text": (
                    f"{cv_best['conviction']} signals outperform {cv_worst['conviction']} "
                    f"by {cv_best['avg_alpha'] - cv_worst['avg_alpha']:.1f}pp. "
                    f"Use conviction tier as a sizing signal — larger positions on "
                    f"{cv_best['conviction']} when score and sector also align."
                ),
                "source_tab": "🏷️ Signal Breakdown",
            })

    # ── Context (always) ───────────────────────────────────────────────────────
    thin_count = sum(1 for b in bands if b["n"] < min_n)
    directives.append({
        "type": "context",
        "text": (
            f"Based on {n_graded} graded outcomes"
            + (
                f" — {thin_count} score band{'s' if thin_count != 1 else ''} still "
                f"below the {min_n}-outcome confidence floor"
                if thin_count > 0 else ""
            )
            + ". Patterns will sharpen as recommendations mature over the coming weeks."
        ),
        "source_tab": "all models",
    })

    _order = {"action": 0, "caution": 1, "watch": 2, "context": 3}
    directives.sort(key=lambda d: _order.get(d["type"], 99))
    return directives


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
