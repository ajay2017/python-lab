"""
Predictive Modeling Shadow Layer — Phase 1 scoring harness (F-234).

Pure logic, no Streamlit/DB imports; read-only over an already-loaded
`model_predictions`-shaped DataFrame. Answers exactly one question, honestly:
does the model beat a naive "next period ≈ last period" persistence baseline?
Nothing here feeds any gate, recommendation, or the composite score — see
docs/plans/predictive-modeling-shadow-layer.md §1.4 for the full design.
"""
from __future__ import annotations

import pandas as pd

from stock_analyzer.constants import (
    PREDICTION_MIN_MATURED_N,
    VOL_FORECAST_HORIZON_DAYS,
)

_EMPTY_RESULT = {
    "n_matured": 0,
    "n_matured_live": 0,
    "n_matured_backfill": 0,
    "mae_model": None,
    "mae_baseline": None,
    "skill_score": None,
    "skill_score_live_only": None,
    "regime_breakdown": {},
    "effective_n_note": "n=0 — nothing matured yet",
}


def _abs_error_series(d: pd.DataFrame, stored_col: str, pred_col: str) -> pd.Series:
    """Prefer a stored per-row error column (written at maturation, e.g. by
    the cron/backfill writer) when present; recompute from
    `|pred_col - realized_value|` wherever the stored value is missing (a
    hand-built test DataFrame, or an older row written before a column
    existed). Recomputing is safe here (unlike recomputing the BASELINE
    VALUE itself, which must never happen — design doc §1.6, item 2) because
    this is pure arithmetic on already-realized numbers, not a re-derivation
    of what the model "knew" at prediction time."""
    computed = (d[pred_col].astype(float) - d["realized_value"].astype(float)).abs()
    if stored_col in d.columns:
        stored = pd.to_numeric(d[stored_col], errors="coerce")
        return stored.where(stored.notna(), computed)
    return computed


def _safe_mean(s: pd.Series) -> float | None:
    s = s.dropna()
    if s.empty:
        return None
    return float(s.mean())


def _skill(mae_model: float | None, mae_baseline: float | None) -> float | None:
    """1 - MAE(model)/MAE(baseline). None (not 0, not inf) when either MAE is
    unavailable or the baseline MAE is zero (undefined ratio) — a withheld
    read, never a fabricated number."""
    if mae_model is None or mae_baseline is None or mae_baseline == 0:
        return None
    return 1.0 - (mae_model / mae_baseline)


def _effective_n_note(d: pd.DataFrame) -> str:
    """Rough overlap-adjustment caveat (NOT a rigorous statistical effective-N
    — design doc §1.6b explicitly asks only for "something defensible",
    not a precise estimator). Consecutive daily predictions against a
    20-trading-day horizon share ~19/20 days of window, so the raw
    `n_matured` count overstates how much INDEPENDENT information the
    sample actually carries.

    Formula: estimate the actual observed "stride" — the median gap (in
    calendar days) between consecutive `made_at` dates within the SAME
    ticker — across all tickers with ≥2 matured rows, then approximate
    `effective_n = n_matured / max(1, horizon_days / stride)`. A stride
    close to the horizon (little overlap, e.g. the backfill script's ~5-day
    stride against a 20-day horizon) barely discounts n; a stride of 1 day
    (every trading day, as the live cron does) discounts it by ~horizon_days.
    Always shown ALONGSIDE the raw n_matured, never in place of it (§1.6b).
    """
    n = len(d)
    if n == 0:
        return "n=0"
    if "ticker" not in d.columns or "made_at" not in d.columns:
        return f"n={n} (raw; insufficient columns to estimate stride)"

    horizon = VOL_FORECAST_HORIZON_DAYS
    if "horizon_days" in d.columns:
        _h = pd.to_numeric(d["horizon_days"], errors="coerce").dropna()
        if not _h.empty:
            horizon = float(_h.median())

    made_at = pd.to_datetime(d["made_at"], errors="coerce", utc=True)
    gaps: list[float] = []
    for _tk, _grp in d.assign(_made_at=made_at).groupby("ticker"):
        dates = _grp["_made_at"].dropna().sort_values().unique()
        if len(dates) < 2:
            continue
        diffs = pd.Series(dates).diff().dropna()
        gaps.extend((diffs / pd.Timedelta(days=1)).tolist())

    if not gaps:
        return f"n={n} (raw; too few repeat observations per ticker to estimate stride)"

    stride = float(pd.Series(gaps).median())
    if stride <= 0:
        return f"n={n} (raw; degenerate stride estimate)"
    overlap_factor = max(1.0, horizon / stride)
    effective_n = n / overlap_factor
    return (
        f"~{effective_n:.0f} effective (of {n} raw; "
        f"~{stride:.0f}-day observed stride vs {horizon:.0f}-day horizon)"
    )


def score_predictions(df: pd.DataFrame) -> dict:
    """Score a `model_predictions`-shaped DataFrame of ALREADY-MATURED rows
    (i.e. the caller has filtered to `realized_value` not null — rows with no
    realized outcome yet contribute nothing here and should not be passed
    in). Defensively re-filters on `realized_value.notna()` regardless, so
    an accidental unmatured row can't silently poison the aggregate.

    Returns a dict:
      - `n_matured` / `n_matured_live` / `n_matured_backfill` — raw counts
        (the `source` column classifies each row `"live"` or `"backfill"`;
        rows with a missing/unrecognized `source` count toward neither).
      - `mae_model` / `mae_baseline` — mean absolute error, blended
        (live + backfill).
      - `skill_score` — `1 - mae_model/mae_baseline`. **`None` (never 0, never
        a number) when `n_matured < PREDICTION_MIN_MATURED_N`** — this
        "withheld below the floor" behavior is a deliberate, tested contract
        (design doc §1.4), not an edge case to skip.
      - `skill_score_live_only` — same formula, restricted to `source ==
        "live"` rows only, so a 100%-backfilled headline skill number can
        never masquerade as live-validated (design doc §1.6b). Gated on the
        SAME `PREDICTION_MIN_MATURED_N` floor (reused deliberately, not a
        parallel constant) applied to `n_matured_live` — flagged in the
        implementation report as a design choice worth a second look, since
        the design doc's own wording ("a few live rows") reads slightly
        looser than the full floor.
      - `regime_breakdown` — `{regime_label: {n, mae_model, mae_baseline,
        skill_score}}` per distinct `regime_at_make` value (rows with a null
        regime tag are grouped under `"unknown"`). NOT gated on
        `PREDICTION_MIN_MATURED_N` — the point of this breakdown is to let a
        thin-but-real regime slice be visibly labeled as such (design doc
        §1.4/§1.6, item 5), not to hide it. A per-regime `skill_score` is
        still `None` if that regime's baseline MAE is 0/undefined.
      - `effective_n_note` — see `_effective_n_note` above.
    """
    if df is None or df.empty:
        return dict(_EMPTY_RESULT)

    d = df[df["realized_value"].notna()].copy() if "realized_value" in df.columns else df.copy()
    n = len(d)
    if n == 0:
        return dict(_EMPTY_RESULT)

    err_model = _abs_error_series(d, "abs_error", "predicted_value")
    err_baseline = _abs_error_series(d, "baseline_abs_error", "baseline_value")
    mae_model = _safe_mean(err_model)
    mae_baseline = _safe_mean(err_baseline)

    source = d["source"] if "source" in d.columns else pd.Series([None] * n, index=d.index)
    is_live = source == "live"
    is_backfill = source == "backfill"
    n_live = int(is_live.sum())
    n_backfill = int(is_backfill.sum())

    skill_score = _skill(mae_model, mae_baseline) if n >= PREDICTION_MIN_MATURED_N else None

    skill_score_live_only = None
    if n_live >= PREDICTION_MIN_MATURED_N:
        _mae_model_live = _safe_mean(err_model[is_live])
        _mae_baseline_live = _safe_mean(err_baseline[is_live])
        skill_score_live_only = _skill(_mae_model_live, _mae_baseline_live)

    regime_breakdown: dict = {}
    if "regime_at_make" in d.columns:
        regime_labels = d["regime_at_make"].where(d["regime_at_make"].notna(), "unknown").astype(str)
        for regime in sorted(regime_labels.unique()):
            grp_idx = regime_labels[regime_labels == regime].index
            grp_n = len(grp_idx)
            if grp_n == 0:
                continue
            g_mae_model = _safe_mean(err_model.loc[grp_idx])
            g_mae_baseline = _safe_mean(err_baseline.loc[grp_idx])
            regime_breakdown[regime] = {
                "n": grp_n,
                "mae_model": g_mae_model,
                "mae_baseline": g_mae_baseline,
                "skill_score": _skill(g_mae_model, g_mae_baseline),
            }

    return {
        "n_matured": n,
        "n_matured_live": n_live,
        "n_matured_backfill": n_backfill,
        "mae_model": mae_model,
        "mae_baseline": mae_baseline,
        "skill_score": skill_score,
        "skill_score_live_only": skill_score_live_only,
        "regime_breakdown": regime_breakdown,
        "effective_n_note": _effective_n_note(d),
    }
