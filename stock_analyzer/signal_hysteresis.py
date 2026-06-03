"""
Signal hysteresis — the calm-advisor layer's final damper (Tier 2, Phase 2C).

A medium-term advisor (§2B persona) should not make the user feel like every
day is a fresh re-evaluation. When a pick was already on the board yesterday and
its composite has barely moved AND its verdict is unchanged, that's not a new
call — it's the same conviction holding. We mark it "steady vs yesterday" so the
user reads it as continuity, not a daily decision to re-litigate.

This is ANNOTATE-ONLY. It NEVER adds, removes, re-orders, or suppresses a pick —
it cannot fight the buy gates or the AM lock. It only attaches a cosmetic
`_hysteresis` marker the renderer may show as a grey chip. If anything is
unknown (no prior snapshot, missing composite), the pick is simply left
untouched and renders as normal (i.e. treated as fresh).

Pure logic — no Streamlit / no I/O. The caller builds `prior_snapshot` from the
recommendations log and passes the live pick dicts.
"""

from stock_analyzer.constants import HYSTERESIS_COMPOSITE_DELTA


def _pick_composite(pick: dict):
    """Best-available composite score for a Grow-Today pick.

    new_picks carry it as `composite_score`; add-to-winner picks carry their
    score as `score`. Returns a positive float, or None when unscored."""
    for key in ("composite_score", "score", "total"):
        val = pick.get(key)
        if val is None:
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return None


def _pick_verdict(pick: dict) -> str:
    """Normalised verdict for a pick. new_picks store it under xref.verdict;
    add-to-winner picks have no verdict on the dict (constant 'confirmed' by
    construction). Returns a lowercased string, or '' when unknown."""
    xref = pick.get("xref") or {}
    v = xref.get("verdict") or pick.get("verdict") or ""
    return str(v).strip().lower()


def apply_hysteresis(
    today_picks: list[dict],
    prior_snapshot: dict,
    delta: float = HYSTERESIS_COMPOSITE_DELTA,
) -> list[dict]:
    """Mark picks that are steady vs yesterday. Mutates picks in place and
    returns the same list (for chaining).

    Parameters
    ----------
    today_picks    : list of pick dicts (new_picks and/or add_positions).
    prior_snapshot : dict ticker(UPPER) -> {"composite": float|None,
                     "verdict": str}, built from yesterday's recommendations.
    delta          : composite points within which a pick counts as steady.

    A pick is steady iff ALL hold:
      - its ticker was in yesterday's snapshot,
      - both composites are known (positive), and within `delta`,
      - the verdict is not a known change (if both verdicts are non-empty they
        must match; an unknown verdict on either side does not block — verdict
        is a guard against flips, not a hard requirement).

    Steady picks get `pick["_hysteresis"] = {"stable": True,
    "note": "Steady vs yesterday"}`. Everything else is left untouched so it
    renders as a fresh/changed call. Never suppresses or re-orders.
    """
    if not today_picks or not prior_snapshot:
        return today_picks

    try:
        band = abs(float(delta))
    except (TypeError, ValueError):
        return today_picks

    for pick in today_picks:
        ticker = str(pick.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        prior = prior_snapshot.get(ticker)
        if not prior:
            continue

        today_comp = _pick_composite(pick)
        prior_comp = prior.get("composite")
        if today_comp is None or prior_comp is None:
            continue
        try:
            if abs(today_comp - float(prior_comp)) > band:
                continue
        except (TypeError, ValueError):
            continue

        # Verdict guard: only block when BOTH sides are known and they differ.
        today_verdict = _pick_verdict(pick)
        prior_verdict = str(prior.get("verdict") or "").strip().lower()
        if today_verdict and prior_verdict and today_verdict != prior_verdict:
            continue

        pick["_hysteresis"] = {"stable": True, "note": "Steady vs yesterday"}

    return today_picks
