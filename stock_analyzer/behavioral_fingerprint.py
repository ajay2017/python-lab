"""
Behavioral Fingerprint — Concept A (F-193), My Edge 4th tab.

Observation-only, sample-gated statistics over the SAME `recommendations` /
`trades` substrate as `recommendations_history.py` and `decision_quality.py`
(per the plan's own coordination note: this must not become a parallel
logger). Buy-side only for v1 — exit-side TRIM/EXIT signals have no
historical capture (see docs/plans/next-evolution-strategy.md, Concept A,
Week-1 data-readiness audit).

Every pattern function returns None when either compared bucket has fewer
than `min_n` rows — never present a directional finding at small N. These
are correlations observed in the investor's own past decisions, not a
verdict on them, and the engine never reads any of these outputs: nothing
here re-ranks, re-scores, or gates a recommendation.

All functions are pure computation (no Streamlit, no DB calls, no fetches)
and defensively swallow malformed/missing input by returning None rather
than raising — a single bad row must not crash the tab.
"""

from typing import Optional


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
        return None if x != x else x  # NaN check
    except (TypeError, ValueError):
        return None


# ── Pattern 1 — momentum / recency-chasing proxy ─────────────────────────────

def momentum_recency_pattern(
    matched: list[dict], min_n: int, meaningful_delta_pp: float = 5.0
) -> Optional[dict]:
    """
    Median-split actionable recs by `momentum_score` (already known at signal
    time — zero new price fetches) and compare action_rate between the
    high-momentum half and the low-momentum half.

    `meaningful_delta_pp` is a display-copy threshold only (caller should pass
    constants.BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP) — it decides whether
    `direction` reads "chases"/"fades" vs "flat"; it never suppresses the card
    (that's `min_n`'s job).

    Returns None when either bucket (or the total scored pool) is below
    `min_n` in size. Otherwise:
        {"high": {n, n_acted, action_rate},
         "low":  {n, n_acted, action_rate},
         "delta_pp": float,
         "direction": "chases" | "fades" | "flat"}
    """
    try:
        if not matched:
            return None
        scored = [
            r for r in matched
            if isinstance(r, dict) and _safe_float(r.get("momentum_score")) is not None
        ]
        if len(scored) < min_n * 2:
            return None

        scored_sorted = sorted(scored, key=lambda r: _safe_float(r["momentum_score"]))
        n = len(scored_sorted)
        mid = n // 2
        median_val = (
            _safe_float(scored_sorted[mid]["momentum_score"])
            if n % 2 == 1
            else (
                _safe_float(scored_sorted[mid - 1]["momentum_score"])
                + _safe_float(scored_sorted[mid]["momentum_score"])
            ) / 2.0
        )

        high = [r for r in scored if _safe_float(r["momentum_score"]) >= median_val]
        low = [r for r in scored if _safe_float(r["momentum_score"]) < median_val]
        if len(high) < min_n or len(low) < min_n:
            return None

        def _bucket_stats(items: list[dict]) -> dict:
            n_i = len(items)
            n_acted = sum(1 for r in items if bool(r.get("acted_on")))
            return {
                "n": n_i,
                "n_acted": n_acted,
                "action_rate": round(n_acted / n_i * 100.0, 1) if n_i else 0.0,
            }

        high_stats = _bucket_stats(high)
        low_stats = _bucket_stats(low)
        delta_pp = round(high_stats["action_rate"] - low_stats["action_rate"], 1)
        if delta_pp > meaningful_delta_pp:
            direction = "chases"
        elif delta_pp < -meaningful_delta_pp:
            direction = "fades"
        else:
            direction = "flat"

        return {
            "high": high_stats,
            "low": low_stats,
            "delta_pp": delta_pp,
            "direction": direction,
        }
    except Exception:
        return None


# ── Pattern 2 — conviction-tier follow-through ───────────────────────────────

def conviction_tier_pattern(
    matched: list[dict], strong_buy_floor: float, min_n: int
) -> Optional[dict]:
    """
    Compare action_rate for Strong Buy (composite_score >= strong_buy_floor)
    vs plain Buy (below it) actionable recs.

    Returns None when either bucket is below `min_n`. Otherwise:
        {"strong_buy": {n, n_acted, action_rate},
         "buy":        {n, n_acted, action_rate},
         "delta_pp": float}
    """
    try:
        if not matched:
            return None
        scored = [
            r for r in matched
            if isinstance(r, dict) and _safe_float(r.get("composite_score")) is not None
        ]
        if not scored:
            return None

        strong = [r for r in scored if _safe_float(r["composite_score"]) >= strong_buy_floor]
        buy = [r for r in scored if _safe_float(r["composite_score"]) < strong_buy_floor]
        if len(strong) < min_n or len(buy) < min_n:
            return None

        def _bucket_stats(items: list[dict]) -> dict:
            n_i = len(items)
            n_acted = sum(1 for r in items if bool(r.get("acted_on")))
            return {
                "n": n_i,
                "n_acted": n_acted,
                "action_rate": round(n_acted / n_i * 100.0, 1) if n_i else 0.0,
            }

        strong_stats = _bucket_stats(strong)
        buy_stats = _bucket_stats(buy)
        delta_pp = round(strong_stats["action_rate"] - buy_stats["action_rate"], 1)

        return {
            "strong_buy": strong_stats,
            "buy": buy_stats,
            "delta_pp": delta_pp,
        }
    except Exception:
        return None


# ── Pattern 3 — opening-window entry timing ──────────────────────────────────

def opening_window_pattern(
    enriched: list[dict], opening_window_min: int, min_n: int
) -> Optional[dict]:
    """
    Compare average `alpha_pct` (SPY-adjusted outcome) between trades entered
    within `opening_window_min` minutes of the 9:30 ET open ("opening") vs
    all other rows with a resolvable `et_time` ("later").

    Caller is responsible for: pre-filtering `enriched` to acted + graded
    (`outcome_pct is not None and not outcome_maturing`) rows, and attaching
    an `et_time` field (a `datetime.time`, or an (hour, minute) tuple) to
    each row already converted to US/Eastern — this function does no
    timezone math itself.

    Returns None when either bucket is below `min_n`. Otherwise:
        {"opening": {n, avg_alpha_pct},
         "later":   {n, avg_alpha_pct},
         "delta_pp": float}
    """
    try:
        if not enriched:
            return None

        def _minutes_after_open(et_time) -> Optional[int]:
            try:
                if hasattr(et_time, "hour") and hasattr(et_time, "minute"):
                    h, m = et_time.hour, et_time.minute
                else:
                    h, m = et_time[0], et_time[1]
                return (int(h) * 60 + int(m)) - (9 * 60 + 30)
            except Exception:
                return None

        rows = []
        for r in enriched:
            if not isinstance(r, dict):
                continue
            et_time = r.get("et_time")
            if et_time is None:
                continue
            mins = _minutes_after_open(et_time)
            if mins is None:
                continue
            alpha = _safe_float(r.get("alpha_pct"))
            if alpha is None:
                continue
            rows.append({"mins": mins, "alpha_pct": alpha})

        if not rows:
            return None

        opening = [r for r in rows if 0 <= r["mins"] < opening_window_min]
        later = [r for r in rows if not (0 <= r["mins"] < opening_window_min)]
        if len(opening) < min_n or len(later) < min_n:
            return None

        def _bucket_stats(items: list[dict]) -> dict:
            n_i = len(items)
            avg = round(sum(r["alpha_pct"] for r in items) / n_i, 2) if n_i else None
            return {"n": n_i, "avg_alpha_pct": avg}

        opening_stats = _bucket_stats(opening)
        later_stats = _bucket_stats(later)
        delta_pp = (
            round(opening_stats["avg_alpha_pct"] - later_stats["avg_alpha_pct"], 2)
            if (opening_stats["avg_alpha_pct"] is not None and later_stats["avg_alpha_pct"] is not None)
            else None
        )

        return {
            "opening": opening_stats,
            "later": later_stats,
            "delta_pp": delta_pp,
        }
    except Exception:
        return None
