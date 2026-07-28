"""
Predictive Analytics — signal calibration helpers (Option A).

Pure functions only; no Streamlit imports. All inputs are plain Python
lists/dicts produced by recommendations_history.compute_outcomes().

Called lazily from the "📊 Predictive Analytics" page in app.py.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable


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


def calibration_by_verdict(
    enriched: list[dict],
    min_n: int = 0,
) -> list[dict]:
    """
    Group graded outcomes by cross-check verdict to measure whether
    sentiment-aligned recs (Confirmed) outperform conflicted/unverified ones.

    Same graded-population filter as calibration_by_score_band:
    outcome_maturing=False AND alpha_pct is not None.

    Returns a list sorted Confirmed first, then by n descending. Each dict:
        verdict           str
        n                 int
        n_acted           int
        n_missed          int
        p_positive_alpha  float | None
        avg_alpha         float | None
        avg_composite     float | None
        avg_outcome_pct   float | None
    """
    buckets: dict[str, dict] = {}
    for r in enriched:
        if r.get("outcome_maturing"):
            continue
        alpha = r.get("alpha_pct")
        if alpha is None:
            continue
        v = str(r.get("verdict") or "").strip() or "Unknown"
        if v not in buckets:
            buckets[v] = {"verdict": v, "n": 0, "n_acted": 0, "n_missed": 0,
                          "_alphas": [], "_composites": [], "_outcomes": []}
        b = buckets[v]
        b["n"] += 1
        if r.get("acted_on"):
            b["n_acted"] += 1
        else:
            b["n_missed"] += 1
        b["_alphas"].append(alpha)
        if r.get("composite_score") is not None:
            try:
                b["_composites"].append(float(r["composite_score"]))
            except (TypeError, ValueError):
                pass
        op = r.get("outcome_pct")
        if op is not None:
            b["_outcomes"].append(op)

    result = []
    for v, b in buckets.items():
        alphas = b["_alphas"]
        comps  = b["_composites"]
        outs   = b["_outcomes"]
        result.append({
            "verdict":          v,
            "n":                b["n"],
            "n_acted":          b["n_acted"],
            "n_missed":         b["n_missed"],
            "p_positive_alpha": sum(1 for a in alphas if a > 0) / len(alphas) if alphas else None,
            "avg_alpha":        round(sum(alphas) / len(alphas), 2) if alphas else None,
            "avg_composite":    round(sum(comps) / len(comps), 1) if comps else None,
            "avg_outcome_pct":  round(sum(outs) / len(outs), 2) if outs else None,
        })

    # Sort: Confirmed/confirmed first, then by n desc
    def _sort_key(x):
        is_conf = x["verdict"].lower() == "confirmed"
        return (0 if is_conf else 1, -x["n"])

    return sorted(result, key=_sort_key)


def sentiment_alignment_summary(
    by_verdict: list[dict],
    min_n: int = 3,
) -> dict:
    """
    Binary Confirmed vs all-others comparison.

    Returns:
        confirmed_avg_alpha  float | None
        other_avg_alpha      float | None
        edge_pp              float | None   (confirmed - other; positive = Confirmed wins)
        confirmed_n          int
        other_n              int
        conclusion           str — 'confirmed_wins' | 'no_edge' | 'insufficient_data'
    """
    conf = next((b for b in by_verdict if b["verdict"].lower() == "confirmed"), None)
    others = [b for b in by_verdict if b["verdict"].lower() != "confirmed"]

    conf_alpha = conf["avg_alpha"] if conf else None
    conf_n     = conf["n"] if conf else 0
    other_n    = sum(b["n"] for b in others)
    # weighted mean for the "other" group
    if others:
        _w_sum = sum(b["n"] * (b["avg_alpha"] or 0) for b in others if b["avg_alpha"] is not None)
        _w_cnt = sum(b["n"] for b in others if b["avg_alpha"] is not None)
        other_alpha = round(_w_sum / _w_cnt, 2) if _w_cnt else None
    else:
        other_alpha = None

    edge_pp = (
        round(conf_alpha - other_alpha, 2)
        if (conf_alpha is not None and other_alpha is not None)
        else None
    )

    if conf_n < min_n or other_n < min_n:
        conclusion = "insufficient_data"
    elif edge_pp is not None and edge_pp > 0:
        conclusion = "confirmed_wins"
    else:
        conclusion = "no_edge"

    return {
        "confirmed_avg_alpha": conf_alpha,
        "other_avg_alpha":     other_alpha,
        "edge_pp":             edge_pp,
        "confirmed_n":         conf_n,
        "other_n":             other_n,
        "conclusion":          conclusion,
    }


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
    sentiment_alignment=None,
    entry_timing_bands: list[dict] | None = None,
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
                f"Treat signals below {thresh} as speculative — consider reducing size or skipping."
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
                    f"({best['avg_alpha']:+.1f}pp avg alpha, {best['n']} outcomes). "
                    f"When composites are borderline, prioritise signals here — "
                    f"this is where the engine has worked best for you."
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
                    f"Signals in {worst['sector']} have cost the most alpha "
                    f"({worst['avg_alpha']:+.1f}pp avg, {worst['n']} outcomes). "
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
                    f"{rt_best['label']} signals outperform {rt_worst['label']} "
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

    # Sentiment alignment directive
    if sentiment_alignment is not None and sentiment_alignment.get("conclusion") != "insufficient_data":
        edge = sentiment_alignment.get("edge_pp")
        if sentiment_alignment["conclusion"] == "confirmed_wins" and edge is not None and edge >= 2.0:
            directives.append({
                "type": "action",
                "text": (
                    f"Sentiment alignment adds {edge:+.1f}pp of alpha in your history — "
                    f"favour Confirmed-verdict picks over Conflicted or Unverified ones "
                    f"when conviction is similar."
                ),
                "source_tab": "🧭 Sentiment Alignment",
            })
        elif sentiment_alignment["conclusion"] == "no_edge":
            directives.append({
                "type": "watch",
                "text": (
                    "Sentiment alignment (Confirmed vs others) has not produced a measurable "
                    "alpha edge in your history yet — continue tracking as the dataset grows."
                ),
                "source_tab": "🧭 Sentiment Alignment",
            })

    # Entry Timing directive (Phase 1) — caution only, never action; gated on
    # the top (Extreme) divergence band clearing min_n so a 1-2-pick anecdote
    # isn't narrated as a pattern. This never feeds back into the composite
    # score or the 5-gate pipeline — awareness only.
    if entry_timing_bands:
        _et_extreme = next(
            (b for b in entry_timing_bands if b.get("band_label") == "Extreme"), None
        )
        if (_et_extreme and _et_extreme.get("day1_n", 0) >= min_n
                and _et_extreme.get("day1_pct_red") is not None):
            directives.append({
                "type": "caution",
                "text": (
                    f"New Position picks where momentum ran far ahead of the composite "
                    f"score (Extreme divergence) have opened red on Day+1 "
                    f"{_et_extreme['day1_pct_red']:.0%} of the time in your history — "
                    f"though the effect has tended to fade by the time the outcome matures. "
                    f"Worth a second look before sizing up on a hot-momentum, "
                    f"barely-qualifying pick."
                ),
                "source_tab": "⏱️ Entry Timing",
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


# ── Entry Timing (Tab 6) ────────────────────────────────────────────────────
# Diagnostic only — see docs/plans/entry-timing-tab.md. Never feeds back into
# the composite score or the 5-gate new-position pipeline.

def dedupe_repeated_tickers(
    enriched: list[dict],
    window_days: int = 5,
    rec_types: tuple = ("new_pick",),
) -> list[dict]:
    """
    Collapse same-ticker firings of `rec_types` that recur within a rolling
    `window_days` calendar-day window into a single kept row — the daily
    scanner re-firing an unbought name (e.g. AMD 5x in 2 weeks) is the same
    opportunity measured repeatedly, not N independent data points.

    For each ticker, sort its scoped firings by rec_date. Walk them in order;
    a firing is DROPPED (collapsed into the current cluster) if it falls
    within `window_days` of the last KEPT firing's rec_date, otherwise it is
    KEPT and becomes the new cluster anchor. This keeps the FIRST firing of
    each cluster (the moment the pattern first appeared).

    Rows outside `rec_types` pass through untouched. Rows with no rec_date
    can't be clustered and are kept as-is.
    """
    scoped = [r for r in enriched if r.get("rec_type") in rec_types]
    other  = [r for r in enriched if r.get("rec_type") not in rec_types]

    by_ticker: dict[str, list[dict]] = {}
    for r in scoped:
        by_ticker.setdefault(r.get("ticker"), []).append(r)

    kept: list[dict] = []
    for _ticker, rows in by_ticker.items():
        dated   = sorted((r for r in rows if r.get("rec_date") is not None),
                         key=lambda r: r["rec_date"])
        undated = [r for r in rows if r.get("rec_date") is None]
        kept.extend(undated)

        last_kept_date = None
        for r in dated:
            if last_kept_date is None or (r["rec_date"] - last_kept_date).days > window_days:
                kept.append(r)
                last_kept_date = r["rec_date"]
            # else: within window_days of the cluster anchor — collapsed, dropped.

    return kept + other


def divergence_at_entry(rec: dict) -> float | None:
    """
    momentum_score minus composite_score at the moment a rec fired — how far
    technical momentum ran ahead of the overall composite consensus.

    Only positive divergence (momentum outrunning composite) is meaningful
    for the "hot momentum, thin composite → rough first few days" question
    this tab answers. Negative divergence (composite > momentum) is a
    different question (early/unconfirmed setup vs. value trap) and is
    filtered out downstream by `by_divergence_band`, not here — this function
    just computes the raw gap.
    """
    m, c = rec.get("momentum_score"), rec.get("composite_score")
    if m is None or c is None:
        return None
    try:
        return float(m) - float(c)
    except (TypeError, ValueError):
        return None


def _advance_trading_days(start_d: date, n: int) -> date:
    """Return the NYSE trading day that is `n` sessions after `start_d`.

    Reimplements data.is_trading_day's weekday+holiday check locally (reading
    constants.NYSE_HOLIDAYS directly) rather than importing stock_analyzer.data
    — that module pulls in the full providers/db import chain (a hard
    streamlit dependency), which this module's docstring promises to stay
    free of. Early-close half-days are still trading days, same as
    data.is_trading_day.
    """
    from stock_analyzer.constants import NYSE_HOLIDAYS
    d = start_d
    count = 0
    while count < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS:
            count += 1
    return d


def forward_alpha_at_horizon(
    ticker: str,
    rec_date: date,
    price_at_entry: float | None,
    horizon_trading_days: int,
    spy_close_by_date: dict | None,
    historical_close_fn: Callable[[str, date, date], float | None] | None = None,
) -> float | None:
    """
    Alpha (stock return minus SPY return) from `rec_date` to `rec_date` +
    `horizon_trading_days` NYSE trading sessions.

    Needs the stock's forward close, which — unlike the SPY leg — isn't in
    any dataset this app already loads, so this makes a live fetch via
    `historical_close_fn(ticker, start, end)`: first close on/after `start`
    within `[start, end]`, same shape as
    `providers.orchestrator.get_historical_close` /
    `analyst_intel.fetch_anchor_price`. Defaults to that orchestrator
    function directly (lazy-imported to avoid a module-load-time provider
    dependency), but callers with a caching layer (e.g. app.py's
    `_cached_historical_close`, mirroring `_cached_spy`) should inject it here
    so a page load doesn't re-fetch the same ticker/date per row.

    Returns None when price_at_entry is missing/non-positive, the forward
    close can't be found (delisted, no data, transport failure), or the SPY
    benchmark series doesn't cover the window — never raises.
    """
    if not ticker or rec_date is None or not price_at_entry or price_at_entry <= 0:
        return None

    if historical_close_fn is None:
        from stock_analyzer.providers.orchestrator import get_historical_close
        historical_close_fn = get_historical_close

    target_date = _advance_trading_days(rec_date, horizon_trading_days)
    try:
        price_fwd = historical_close_fn(ticker, target_date, target_date + timedelta(days=7))
    except Exception:
        return None
    if price_fwd is None:
        return None
    try:
        price_fwd = float(price_fwd)
    except (TypeError, ValueError):
        return None
    if price_fwd != price_fwd or price_fwd <= 0:   # NaN guard
        return None

    stock_ret = (price_fwd - price_at_entry) / price_at_entry * 100.0

    from stock_analyzer.recommendations_history import _spy_return_pct
    spy_ret = _spy_return_pct(spy_close_by_date, rec_date, target_date)
    if spy_ret is None:
        return None
    return round(stock_ret - spy_ret, 2)


def by_divergence_band(
    rows: list[dict],
    aligned_max: float,
    diverging_max: float,
) -> list[dict]:
    """
    Group deduped new_pick rows into three positive-divergence bands and
    report Day+1 / Day+5 / Day+20 alpha per band.

    Each input row is expected to already carry:
      - `divergence`   (from `divergence_at_entry`) — rows with divergence
        <= 0 or None are excluded; only momentum-ahead-of-composite is in
        scope for this analysis.
      - `day1_alpha` / `day5_alpha` (from `forward_alpha_at_horizon`, may be
        None where the forward fetch failed).
      - `alpha_pct` / `outcome_maturing` (already on every row from
        `compute_outcomes`) — reused as the Day+20/mature-outcome leg.

    Bands (using the ENTRY_TIMING_DIVERGENCE_* constants as aligned_max /
    diverging_max):
      Aligned    — divergence <= aligned_max
      Diverging  — aligned_max < divergence <= diverging_max
      Extreme    — divergence > diverging_max

    Returns a list of band dicts, Aligned → Diverging → Extreme, each with:
      band_label, n (rows with any horizon data),
      day1_alpha, day1_pct_red, day1_n,
      day5_alpha, day5_pct_red, day5_n,
      day20_alpha, p_positive_alpha, day20_n
    Per-horizon stats are None until that horizon has >= 1 data point; the
    caller is responsible for greying out any horizon whose *_n falls below
    PREDICTIVE_MIN_BAND_N (same convention as calibration_by_score_band,
    which also returns thin bands rather than dropping them).
    """
    def _band_for(div: float) -> str:
        if div <= aligned_max:
            return "Aligned"
        if div <= diverging_max:
            return "Diverging"
        return "Extreme"

    order = ["Aligned", "Diverging", "Extreme"]
    buckets: dict[str, dict] = {label: {"_n": 0, "_day1": [], "_day5": [], "_day20": []}
                                 for label in order}

    for r in rows:
        div = r.get("divergence")
        if div is None or div <= 0:
            continue
        b = buckets[_band_for(div)]
        b["_n"] += 1
        if r.get("day1_alpha") is not None:
            b["_day1"].append(float(r["day1_alpha"]))
        if r.get("day5_alpha") is not None:
            b["_day5"].append(float(r["day5_alpha"]))
        if not r.get("outcome_maturing") and r.get("alpha_pct") is not None:
            b["_day20"].append(float(r["alpha_pct"]))

    def _stats(vals: list[float]) -> dict:
        n = len(vals)
        return {
            "n":       n,
            "avg":     round(sum(vals) / n, 2) if n else None,
            "pct_red": round(sum(1 for v in vals if v < 0) / n, 3) if n else None,
        }

    out: list[dict] = []
    for label in order:
        b = buckets[label]
        if b["_n"] == 0:
            continue
        d1, d5, d20 = _stats(b["_day1"]), _stats(b["_day5"]), _stats(b["_day20"])
        out.append({
            "band_label":       label,
            "n":                b["_n"],
            "day1_alpha":       d1["avg"],
            "day1_pct_red":     d1["pct_red"],
            "day1_n":           d1["n"],
            "day5_alpha":       d5["avg"],
            "day5_pct_red":     d5["pct_red"],
            "day5_n":           d5["n"],
            "day20_alpha":      d20["avg"],
            "p_positive_alpha": (round(1 - d20["pct_red"], 3) if d20["pct_red"] is not None else None),
            "day20_n":          d20["n"],
        })
    return out
