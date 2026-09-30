"""
Predictive Analytics — signal calibration helpers (Option A).

Pure functions only; no Streamlit imports. All inputs are plain Python
lists/dicts produced by recommendations_history.compute_outcomes().

Called lazily from the "📊 Predictive Analytics" page in app.py.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from stock_analyzer.constants import (
    COMPOSITE_BUY,
    COMPOSITE_STRONG_BUY,
    COMPOSITE_WEIGHTS_VERSION,
    PREDICTIVE_MIN_BAND_N,
)


# ── Page population (scope + version + ETF filters, then collapse) ─────────────
# Gated BUY calls only. `buy_candidate` is awareness-only (never gated), so it
# is excluded from this page's grading and disclosed as excluded; 📜
# Recommendations History keeps the all-types view.
ACTIONABLE_REC_TYPES = ("new_pick", "add_winner", "enter_now")


def _is_graded(r: dict) -> bool:
    return not r.get("outcome_maturing") and r.get("alpha_pct") is not None


def collapse_by_ticker_rec_type(
    enriched: list[dict],
    rec_types: tuple = ACTIONABLE_REC_TYPES,
) -> list[dict]:
    """
    One representative row per (ticker, rec_type) for rec_types in scope —
    the per-type sibling of `recommendations_history.collapse_recs_by_ticker`
    (which is per-ticker only), mirroring
    `rec_events_readout.collapse_by_rec_ticker`'s (rec_type, ticker) key.
    Feeds `by_rec_type_stats`, where one ticker legitimately belongs to more
    than one type but must never count twice within one type.

    Pool = the group's acted rows if any, else the whole group. Anchor, in
    order: earliest GRADED row (not outcome_maturing and alpha_pct not None)
    → earliest priced row (outcome_pct not None) → earliest dated row → the
    pool's first row. Earliest-graded first so a young/ungraded first
    surfacing doesn't knock a ticker out of the graded population when a
    later surfacing of the same call is gradable.

    Rows missing ticker or with rec_type out of scope are ignored. Returns
    shallow copies (input not mutated); order not guaranteed.
    """
    groups: dict[tuple, list[dict]] = {}
    for r in enriched:
        tk = r.get("ticker")
        rt = r.get("rec_type")
        if not tk or rt not in rec_types:
            continue
        groups.setdefault((tk, rt), []).append(r)

    def _earliest(rows: list[dict]) -> dict | None:
        dated = [x for x in rows if x.get("rec_date") is not None]
        if dated:
            return min(dated, key=lambda x: x["rec_date"])
        return rows[0] if rows else None

    out: list[dict] = []
    for grp in groups.values():
        acted = [x for x in grp if x.get("acted_on")]
        pool = acted if acted else grp
        rep = (
            _earliest([x for x in pool if _is_graded(x)])
            or _earliest([x for x in pool if x.get("outcome_pct") is not None])
            or _earliest(pool)
        )
        out.append(dict(rep))
    return out


def prepare_population(
    enriched: list[dict],
    rec_types: tuple = ACTIONABLE_REC_TYPES,
    weights_version: int = COMPOSITE_WEIGHTS_VERSION,
) -> dict:
    """
    Build the Predictive Analytics page's single graded population, applied
    ONCE at page level so every tab reads the same thing.

    1. Version + ETF filters over ALL rec_types (buy_candidate rows are kept
       at this stage so cross-type acted detection still sees a buy made on a
       buy_candidate day). A row is kept only when `weights_version ==
       weights_version` exactly — a None version is EXCLUDED and counted
       separately, never assumed current. `asset_type` "etf" (normalized) is
       excluded; None/anything else = stock.
    2. `reps` = `recommendations_history.collapse_recs_by_ticker(filtered,
       rec_types)` — one row per ticker (unchanged function).
    3. `reps_by_type` = `collapse_by_ticker_rec_type(filtered, rec_types)`.

    Returns:
        reps                          list[dict]  one per ticker in scope
        reps_by_type                  list[dict]  one per (ticker, rec_type) in scope
        scoped_raw                    list[dict]  filtered rows with rec_type in scope
        n_raw_rows                    int  len(enriched)
        n_raw_graded                  int  graded rows in scoped_raw
        n_tickers_graded              int  graded reps (== distinct graded tickers)
        n_excluded_version            int  rows with a non-None, non-current version
        n_excluded_version_none       int  rows with version None
        n_excluded_etf                int  ETF rows (that passed the version filter)
        n_excluded_out_of_scope_rows  int  version/ETF-passing rows outside rec_types
        n_out_of_scope_anchor         int  reps whose anchor row's rec_type is out of scope
        surfacings_by_ticker          dict {ticker: in-scope row count}

    Does not mutate its input (collapse functions return copies; filtering
    only builds new lists).
    """
    from stock_analyzer.asset_type import ASSET_TYPE_ETF, normalize
    from stock_analyzer.recommendations_history import collapse_recs_by_ticker

    n_excl_version = 0
    n_excl_version_none = 0
    n_excl_etf = 0
    filtered: list[dict] = []
    for r in enriched:
        wv = r.get("weights_version")
        if wv is None:
            n_excl_version_none += 1
            continue
        if wv != weights_version:
            n_excl_version += 1
            continue
        if normalize(r.get("asset_type")) == ASSET_TYPE_ETF:
            n_excl_etf += 1
            continue
        filtered.append(r)

    scoped_raw = [r for r in filtered if r.get("rec_type") in rec_types]
    n_out_of_scope_rows = len(filtered) - len(scoped_raw)

    reps = collapse_recs_by_ticker(filtered, rec_types=rec_types)
    reps_by_type = collapse_by_ticker_rec_type(filtered, rec_types)

    surfacings: dict[str, int] = {}
    for r in scoped_raw:
        tk = r.get("ticker")
        if tk:
            surfacings[tk] = surfacings.get(tk, 0) + 1

    return {
        "reps":                         reps,
        "reps_by_type":                 reps_by_type,
        "scoped_raw":                   scoped_raw,
        "n_raw_rows":                   len(enriched),
        "n_raw_graded":                 sum(1 for r in scoped_raw if _is_graded(r)),
        "n_tickers_graded":             sum(1 for r in reps if _is_graded(r)),
        "n_excluded_version":           n_excl_version,
        "n_excluded_version_none":      n_excl_version_none,
        "n_excluded_etf":               n_excl_etf,
        "n_excluded_out_of_scope_rows": n_out_of_scope_rows,
        "n_out_of_scope_anchor":        sum(1 for r in reps if r.get("rec_type") not in rec_types),
        "surfacings_by_ticker":         surfacings,
    }


# ── Signal Calibration ─────────────────────────────────────────────────────────

def calibration_by_score_band(
    enriched: list[dict],
    band_size: int = 5,
    min_n: int = PREDICTIVE_MIN_BAND_N,
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
    is_thin           bool  — n < ``min_n`` (indicative only; computed here so
                              the renderer never compares against the constant)

    Feed it the page's collapsed ``reps`` (one row per ticker) so ``n`` counts
    distinct tickers, not daily re-surfacings.
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
            "is_thin":          n < min_n,
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
        if score < COMPOSITE_BUY:
            return f"< {COMPOSITE_BUY:.0f}"
        if score < COMPOSITE_STRONG_BUY:
            return f"{COMPOSITE_BUY:.0f}–{COMPOSITE_STRONG_BUY - 1:.0f}"
        return f"{COMPOSITE_STRONG_BUY:.0f}+"

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
    thin_n: int = PREDICTIVE_MIN_BAND_N,
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
        is_thin           bool  — n < ``thin_n`` (``min_n`` is a pre-existing
                                  unused parameter, left as-is)
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
            "is_thin":          b["n"] < thin_n,
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
        n_unknown            int   — rows with an empty/"Unknown" verdict, excluded
                                     from BOTH sides (no verdict was recorded, so
                                     they are neither aligned nor misaligned)
        conclusion           str — 'confirmed_wins' | 'no_edge' | 'insufficient_data'
    """
    def _is_unknown(b: dict) -> bool:
        return str(b.get("verdict") or "").strip().lower() in ("", "unknown")

    n_unknown = sum(b["n"] for b in by_verdict if _is_unknown(b))
    known = [b for b in by_verdict if not _is_unknown(b)]
    conf = next((b for b in known if b["verdict"].lower() == "confirmed"), None)
    others = [b for b in known if b["verdict"].lower() != "confirmed"]

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
        "n_unknown":           n_unknown,
        "conclusion":          conclusion,
    }


def _band_beats_spy(b: dict) -> bool:
    """D1 (owner decision 2026-09-30): a band "works" only when it beat SPY
    BOTH more often than not (p_positive_alpha >= 0.5) AND on average
    (avg_alpha > 0, strict). Hit rate alone let a band with a -1.7pp mean
    read as "consistently positive"."""
    p = b.get("p_positive_alpha")
    a = b.get("avg_alpha")
    return p is not None and a is not None and p >= 0.5 and a > 0


def personal_alpha_threshold(
    bands: list[dict],
    min_n: int = 5,
) -> int | None:
    """
    From ``calibration_by_score_band`` output, find the lowest ``band_floor``
    such that every ELIGIBLE band (n >= ``min_n``) at or above that floor
    satisfies BOTH:

    * p_positive_alpha >= 0.5
    * avg_alpha > 0 (strict)

    Thin bands (n < ``min_n``) are ignored for eligibility — they neither
    qualify nor poison a floor — and are disclosed by ``threshold_banner``'s
    ``thin_above`` instead. Returns None when data is insufficient or no such
    threshold exists.
    """
    eligible = [
        b for b in sorted(bands, key=lambda x: x["band_floor"])
        if b["n"] >= min_n and b.get("p_positive_alpha") is not None
    ]
    if not eligible:
        return None
    for i, b in enumerate(eligible):
        if all(_band_beats_spy(x) for x in eligible[i:]):
            return b["band_floor"]
    return None


def threshold_banner(
    bands: list[dict],
    thresh: int | None,
    min_n: int,
) -> dict | None:
    """
    The Score Calibration headline for a found ``thresh`` (from
    ``personal_alpha_threshold`` on the same ``bands``/``min_n``). Returns
    None when ``thresh`` is None — the caller renders its own
    "no threshold yet" fallback.

    Returns:
        floor               int
        qualifying          list[str]  labels of bands >= floor with n >= min_n
        k                   int        len(qualifying)
        n_tickers           int        sum of n over qualifying only
        weighted_avg_alpha  float | None  n-weighted avg_alpha over qualifying
        min_hit_rate        float | None  lowest p_positive_alpha over qualifying
        thin_above          list[str]  labels of bands >= floor with n < min_n
        text                str        markdown

    Defensive invariant: if any qualifying band fails the D1 criterion
    (should be impossible for a thresh from personal_alpha_threshold, but a
    caller could pass a mismatched pair), no "beat SPY" text is produced —
    returns None rather than a false claim.
    """
    if thresh is None:
        return None
    above = sorted(
        (b for b in bands if b.get("band_floor") is not None and b["band_floor"] >= thresh),
        key=lambda x: x["band_floor"],
    )
    qual = [b for b in above if b["n"] >= min_n]
    thin = [b for b in above if b["n"] < min_n]
    if not qual or not all(_band_beats_spy(b) for b in qual):
        return None

    k = len(qual)
    n_tickers = sum(b["n"] for b in qual)
    weighted = round(sum(b["n"] * b["avg_alpha"] for b in qual) / n_tickers, 2) if n_tickers else None
    min_hit = min(b["p_positive_alpha"] for b in qual)
    labels = [b["band_label"] for b in qual]
    thin_labels = [b["band_label"] for b in thin]

    if k >= 2:
        text = (
            f"**Where the engine has worked for you: composite ≥ {thresh}.** "
            f"Each of the {k} bands at or above {thresh} with ≥{min_n} tickers "
            f"({', '.join(labels)}) beat SPY on average and more often than not "
            f"({n_tickers} tickers; avg alpha {weighted:+.1f}pp, lowest hit rate {min_hit:.0%})."
        )
    else:
        b = qual[0]
        text = (
            f"**Only one band at or above {thresh} ({b['band_label']}) has enough "
            f"tickers to read** ({b['n']}); it beat SPY on average "
            f"({b['avg_alpha']:+.1f}pp, hit rate {b['p_positive_alpha']:.0%}). "
            f"One band is a lead, not a pattern."
        )
    if thin_labels:
        text += f" Not counted (fewer than {min_n} tickers): {', '.join(thin_labels)}."
    # Blank line, not a single newline: a lone newline does not break a line
    # in Streamlit markdown.
    text += "\n\nCounts are distinct tickers, each at its first surfacing. Outcomes run from the call to today's price."

    return {
        "floor":              thresh,
        "qualifying":         labels,
        "k":                  k,
        "n_tickers":          n_tickers,
        "weighted_avg_alpha": weighted,
        "min_hit_rate":       min_hit,
        "thin_above":         thin_labels,
        "text":               text,
    }


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

    Feed it the page's COLLAPSED outputs (one row per ticker / per
    (ticker, rec_type)); ``n_graded`` is the count of graded distinct tickers.
    Threshold / sector-best / rec-type / conviction readouts are "watch"
    observations, never "action" (D2, 2026-09-30).
    """
    directives: list[dict] = []

    # ── Score Calibration ──────────────────────────────────────────────────────
    thick_bands = [b for b in bands if b["n"] >= min_n]
    all_neg     = thick_bands and all((b["avg_alpha"] or 0) <= 0 for b in thick_bands)

    # D2 (owner decision 2026-09-30): the threshold, sector, rec-type and
    # conviction readouts are OBSERVATIONS about this user's history, typed
    # "watch" and worded without skip/size/lean/prioritise imperatives. A
    # retrospective on a thin, to-today sample is not a basis for an action.
    if thresh is not None:
        directives.append({
            "type": "watch",
            "text": (
                f"Composite ≥ {thresh} has done better in your history — every "
                f"band at or above it with enough tickers beat SPY on average and "
                f"more often than not."
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

    # ── Discretion Value ───────────────────────────────────────────────────────
    edge    = avm.get("edge", "insufficient")
    edge_pp = avm.get("edge_pp")

    # State the two sample sizes in the directive text. This was the only card
    # asserting something about the USER'S judgment with no basis on screen,
    # while the sector and signal cards both quote their n — and the two sides
    # here are structurally lopsided (you act on a small fraction of what the
    # engine surfaces), so a thin acted side is the normal case, not the edge
    # case. acted_vs_missed_comparison's floor is deliberately "BOTH sides < 3"
    # (see its test named for that choice), which means a 1-vs-300 split still
    # classifies; showing the counts is what lets a reader discount it.
    # Deliberately NOT `.get(...) or {}` — that collapses an offline sentinel,
    # and callers legitimately pass an avm carrying only edge/edge_pp.
    _acted  = avm.get("acted")
    _missed = avm.get("missed")
    _acted_n  = _acted.get("n")  if isinstance(_acted,  dict) else None
    _missed_n = _missed.get("n") if isinstance(_missed, dict) else None
    basis = (
        f" ({_acted_n} acted vs {_missed_n} passed)"
        if _acted_n is not None and _missed_n is not None else ""
    )

    if edge == "acting" and edge_pp is not None and edge_pp >= 0.5:
        # "watch", not "action" (owner decision 2026-09-30): this page is a
        # retrospective diagnostic; the live gates own entry and sizing.
        directives.append({
            "type": "watch",
            "text": (
                f"Your discretion is adding {edge_pp:.1f}pp of alpha{basis} — you're filtering "
                f"signal from noise effectively. Don't feel pressure to act on every signal; "
                f"your selectivity is working."
            ),
            "source_tab": "⚖️ Discretion Value",
        })
    elif edge == "passing" and edge_pp is not None and edge_pp >= 0.5:
        directives.append({
            "type": "caution",
            "text": (
                f"Following every engine signal would have added {edge_pp:.1f}pp more alpha "
                f"than your current act rate{basis}. Review what's making you pass — the engine "
                f"may be seeing something you're discounting."
            ),
            "source_tab": "⚖️ Discretion Value",
        })
    elif edge in ("neutral", "insufficient"):
        directives.append({
            "type": "context",
            "text": (
                "Your act/pass decisions are producing similar alpha to passing on everything. "
                "Discretion isn't adding or removing measurable edge yet — "
                "use score and sector patterns as your primary guide for now."
            ),
            "source_tab": "⚖️ Discretion Value",
        })

    # ── Sector Alpha ───────────────────────────────────────────────────────────
    if sec_alph:
        best  = sec_alph[0]
        worst = sec_alph[-1]

        if (best["avg_alpha"] or 0) > 0:
            directives.append({
                "type": "watch",
                "text": (
                    f"{best['sector']} has done better in your history "
                    f"({best['avg_alpha']:+.1f}pp avg alpha, {best['n']} tickers) — "
                    f"the sector where the engine's calls have worked best for you so far."
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
                    f"({worst['avg_alpha']:+.1f}pp avg, {worst['n']} tickers). "
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
                "type": "watch",
                "text": (
                    f"{rt_best['label']} signals have done better in your history, "
                    f"outperforming {rt_worst['label']} by "
                    f"{rt_best['avg_alpha'] - rt_worst['avg_alpha']:.1f}pp."
                ),
                "source_tab": "🏷️ Signal Breakdown",
            })

    if len(conv) >= 2:
        cv_best, cv_worst = conv[0], conv[-1]
        if (cv_best["avg_alpha"] is not None and cv_worst["avg_alpha"] is not None
                and cv_best["avg_alpha"] - cv_worst["avg_alpha"] >= 1.5):
            directives.append({
                "type": "watch",
                "text": (
                    f"{cv_best['conviction']} signals have done better in your history, "
                    f"outperforming {cv_worst['conviction']} by "
                    f"{cv_best['avg_alpha'] - cv_worst['avg_alpha']:.1f}pp."
                ),
                "source_tab": "🏷️ Signal Breakdown",
            })

    # ── Context (always) ───────────────────────────────────────────────────────
    thin_count = sum(1 for b in bands if b["n"] < min_n)
    directives.append({
        "type": "context",
        "text": (
            f"Based on {n_graded} graded tickers"
            + (
                f" — {thin_count} score band{'s' if thin_count != 1 else ''} still "
                f"below the {min_n}-ticker confidence floor"
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
                "type": "watch",
                "text": (
                    f"Engine-Confirmed picks have beaten Conflicted/Unverified ones by "
                    f"{edge:+.1f}pp of alpha in your history."
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
    #
    # Gated on day20_n/day20_alpha (not day1_pct_red) because the validated
    # shape of this band is "looks calm at Day+1/Day+5, damage shows up by
    # Day+20" (see band_narrative()'s primary branch) — the OPPOSITE of an
    # earlier version of this text, which wrongly said picks "open red on
    # Day+1... though the effect fades by maturity." That was backwards
    # relative to the actual data (caught 2026-07-28 during Phase 2 design
    # review, after the user's own live data showed Day+1 ~0%, Day+20 -14pp)
    # and would have misdirected the user to expect the WRONG horizon's risk.
    if entry_timing_bands:
        _et_extreme = next(
            (b for b in entry_timing_bands if b.get("band_label") == "Extreme"), None
        )
        if (_et_extreme and _et_extreme.get("day20_n", 0) >= min_n
                and _et_extreme.get("day20_alpha") is not None
                and _et_extreme["day20_alpha"] < 0):
            directives.append({
                "type": "caution",
                "text": (
                    f"New Position picks where momentum ran far ahead of the composite "
                    f"score (Extreme divergence) have often looked calm in the first few "
                    f"days but underperformed SPY by {_et_extreme['day20_alpha']:+.1f}pp "
                    f"on average by Day+20 — the real cost has "
                    f"tended to show up late, not at entry. Worth a second look before "
                    f"sizing up on a hot-momentum, barely-qualifying pick."
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


# ── Discretion Value (Tab 2) ───────────────────────────────────────────────────

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
    "enter_now":     "Watchlist Enter Now",
}


def by_conviction(enriched: list[dict], min_n: int = PREDICTIVE_MIN_BAND_N) -> list[dict]:
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


def by_rec_type_stats(enriched: list[dict], min_n: int = PREDICTIVE_MIN_BAND_N) -> list[dict]:
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

def by_sector_alpha(enriched: list[dict], min_n: int = PREDICTIVE_MIN_BAND_N) -> list[dict]:
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
    min_n: int = PREDICTIVE_MIN_BAND_N,
) -> list[dict]:
    """
    Group deduped new_pick rows into three positive-divergence bands and
    report Day+1 / Day+5 / Day+20 alpha per band.

    Each input row is expected to already carry:
      - `divergence`   (from `divergence_at_entry`) — rows with divergence
        <= 0 or None are excluded; only momentum-ahead-of-composite is in
        scope for this analysis.
      - `day1_alpha` / `day5_alpha` / `day20_alpha` (from
        `forward_alpha_at_horizon` at 1 / 5 / 20 trading days, may be None
        where the forward fetch failed or the horizon hasn't elapsed).
        Day+20 is a TRUE fixed-horizon forward alpha (D6, 2026-09-30) — it
        used to reuse the to-today `alpha_pct`, which is not a Day+20
        number. A row without `day20_alpha` contributes nothing to Day+20.

    Bands (using the ENTRY_TIMING_DIVERGENCE_* constants as aligned_max /
    diverging_max):
      Aligned    — divergence <= aligned_max
      Diverging  — aligned_max < divergence <= diverging_max
      Extreme    — divergence > diverging_max

    Returns a list of band dicts, Aligned → Diverging → Extreme, each with:
      band_label, n (rows with any horizon data),
      day1_alpha, day1_pct_red, day1_n,
      day5_alpha, day5_pct_red, day5_n,
      day20_alpha, p_positive_alpha, day20_n,
      day1_is_thin, day5_is_thin, day20_is_thin  (bool, *_n < ``min_n``)
    Per-horizon stats are None until that horizon has >= 1 data point. Thin
    horizons are flagged, not dropped (same convention as
    calibration_by_score_band); the caller greys them via *_is_thin.
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
        if r.get("day20_alpha") is not None:
            b["_day20"].append(float(r["day20_alpha"]))

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
            "day1_is_thin":     d1["n"] < min_n,
            "day5_is_thin":     d5["n"] < min_n,
            "day20_is_thin":    d20["n"] < min_n,
        })
    return out


def find_illustrating_case(
    new_pick_rows: list[dict],
    diverging_max: float,
) -> dict | None:
    """
    Finds the ticker that fired as `new_pick` the most times while sitting in
    the Extreme divergence band (divergence > `diverging_max`), computed on
    the UN-deduped rows — the "repeated re-firing" pattern this tab was built
    to surface (e.g. AMD firing 5x in two weeks in 2026-07). Grounds the
    tab's illustrating-case callout in whatever is actually the current top
    repeat offender in the live data, so the callout never goes stale as a
    fixed historical citation would.

    Returns None when no ticker has >= 2 such firings — nothing to
    illustrate yet is a valid, expected result, never fabricated.
    """
    by_ticker: dict[str, list[dict]] = {}
    for r in new_pick_rows:
        div = divergence_at_entry(r)
        if div is not None and div > diverging_max:
            by_ticker.setdefault(r.get("ticker"), []).append(r)

    candidates = [(tk, rows) for tk, rows in by_ticker.items() if len(rows) >= 2]
    if not candidates:
        return None

    def _first_date(rows):
        dated = [r["rec_date"] for r in rows if r.get("rec_date") is not None]
        return min(dated) if dated else date.max

    # Most-repeated first; tie-break by earliest first firing for determinism.
    candidates.sort(key=lambda x: (-len(x[1]), _first_date(x[1])))
    ticker, rows = candidates[0]

    dated      = [r["rec_date"] for r in rows if r.get("rec_date") is not None]
    composites = [float(r["composite_score"]) for r in rows if r.get("composite_score") is not None]
    momenta    = [float(r["momentum_score"]) for r in rows if r.get("momentum_score") is not None]
    divs       = [d for d in (divergence_at_entry(r) for r in rows) if d is not None]
    alphas     = [
        float(r["alpha_pct"]) for r in rows
        if not r.get("outcome_maturing") and r.get("alpha_pct") is not None
    ]

    return {
        "ticker":         ticker,
        "n_firings":      len(rows),
        "first_date":     min(dated) if dated else None,
        "last_date":      max(dated) if dated else None,
        "composite_min":  min(composites) if composites else None,
        "composite_max":  max(composites) if composites else None,
        "momentum_min":   min(momenta) if momenta else None,
        "momentum_max":   max(momenta) if momenta else None,
        "divergence_min": min(divs) if divs else None,
        "divergence_max": max(divs) if divs else None,
        "alpha_min":      min(alphas) if alphas else None,
        "alpha_max":      max(alphas) if alphas else None,
    }


def band_narrative(band: dict, illustrating_ticker: str | None = None) -> str:
    """
    One-line, rule-based (never LLM) plain-English description of a
    divergence band's Day+1 -> Day+5 -> Day+20 alpha trajectory shape, for
    the stat-card sidebar. `illustrating_ticker` (from `find_illustrating_case`)
    is appended as a callback only when supplied by the caller for the band
    it actually applies to (e.g. only the Extreme band that ticker sits in).

    The "Day+20 is the worst point" case is checked first and separately from
    every other branch, specifically so a near-zero or positive Day+1 value
    can never mask a real, larger Day+20 loss — a live 2026-07-28 screenshot
    caught this exact miss (Day+1 ~0, Day+20 -14pp fell through to a generic
    "not enough Day+20 history" fallback that was simply wrong given data was
    present).
    """
    d1, d5, d20 = band.get("day1_alpha"), band.get("day5_alpha"), band.get("day20_alpha")
    d20_n = band.get("day20_n") or 0

    # A day is treated as "calm" at this small a magnitude so a rounding
    # artifact like -0.04 (displays as "-0.0pp") isn't read as a real signal.
    _CALM = -0.05

    if d1 is None:
        base = "Not enough Day+1 data yet to describe this band's shape."
    elif (d1 >= _CALM and (d5 is None or d5 >= _CALM)
            and d20 is not None and d20 < _CALM):
        # Day+1 AND Day+5 both looked calm/non-negative — nothing here would
        # have flagged a problem in the first week — yet Day+20 shows a real
        # loss. Checked BEFORE the "flat-to-positive" branch below so this
        # specific late-developing pattern (the exact thing this tab exists
        # to catch) is never masked by a near-zero Day+1 reading. Requires
        # BOTH early points to be calm — a Day+1 that's already clearly
        # negative is a different, already-covered case (monotonic
        # worsening), not a "looked fine, then wasn't" surprise.
        base = (
            f"Looks calm through the first few days (Day+1 {d1:+.1f}pp) but fades "
            f"hard to {d20:+.1f}pp by Day+20 ({d20_n} outcome{'s' if d20_n != 1 else ''}) "
            f"— the real cost of this pattern shows up late, not at entry."
        )
    elif d1 >= 0 and (d5 is None or d5 >= 0) and (d20 is None or d20 >= 0):
        base = "Stays flat-to-positive across every horizon measured so far."
    elif d1 < 0 and d5 is not None and d5 >= 0:
        base = (
            f"Recovers quickly — alpha is positive again by Day+5"
            + (f", {d20:+.1f}pp by Day+20." if d20 is not None else ".")
        )
    elif d1 < 0 and (d5 is None or d5 < 0) and d20 is not None and d20 >= 0:
        base = f"Mildly negative through Day+5, turns positive by Day+20 ({d20:+.1f}pp)."
    elif d1 < 0 and d20 is not None and d20 < 0 and d20 > d1:
        base = (
            f"Deepest drawdown of the three ({d1:+.1f}pp Day+1 alpha) — still recovers "
            f"some of it by Day+20 ({d20:+.1f}pp), but the first few days are the "
            f"roughest of any band."
        )
    elif d1 < 0 and d20 is not None and d20 <= d1:
        base = (
            f"Negative at Day+1 ({d1:+.1f}pp) and losses persist through Day+20 "
            f"({d20:+.1f}pp) — no recovery pattern yet."
        )
    else:
        base = f"Day+1 alpha {d1:+.1f}pp; not enough Day+20 history yet to say whether it recovers."

    if illustrating_ticker:
        base += f" This is the {illustrating_ticker}-shaped case."
    return base
