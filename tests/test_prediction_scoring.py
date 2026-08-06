"""Tests for stock_analyzer/prediction_scoring.py — Predictive Modeling
Shadow Layer Phase 1 (F-234), MEASUREMENT-ONLY.

Per the build spec: floor behavior (skill_score MUST be None, not 0, below
PREDICTION_MIN_MATURED_N) is a deliberate, TESTED contract -- not an edge
case to wave through."""
import pandas as pd
import pytest

from stock_analyzer.constants import PREDICTION_MIN_MATURED_N
from stock_analyzer.prediction_scoring import score_predictions


def _rows(n, predicted_offset, baseline_offset, source="live", regime=None,
          ticker="AAA", start_day=1, horizon=20, realized_base=50.0):
    """Build n matured rows with a CONSTANT abs error for both model and
    baseline (|predicted_offset| and |baseline_offset| respectively), so the
    expected MAE/skill are exactly known ahead of time. made_at dates are
    real, monotonically-increasing calendar dates (5 days apart) so the
    effective_n_note's stride estimate never degenerates on a larger n."""
    base = pd.Timestamp("2026-01-01") + pd.Timedelta(days=start_day - 1)
    out = []
    for i in range(n):
        realized = realized_base + i * 0.01  # tiny variation, doesn't affect abs errors
        out.append({
            "ticker": ticker,
            "made_at": (base + pd.Timedelta(days=5 * i)).isoformat(),
            "horizon_days": horizon,
            "predicted_value": realized + predicted_offset,
            "baseline_value": realized + baseline_offset,
            "realized_value": realized,
            "source": source,
            "regime_at_make": regime,
        })
    return out


def _df(rows):
    return pd.DataFrame(rows)


# ── Empty / degenerate input ─────────────────────────────────────────────────

def test_none_dataframe_returns_zeroed_result():
    out = score_predictions(None)
    assert out["n_matured"] == 0
    assert out["skill_score"] is None
    assert out["skill_score_live_only"] is None
    assert out["regime_breakdown"] == {}


def test_empty_dataframe_returns_zeroed_result():
    out = score_predictions(pd.DataFrame())
    assert out["n_matured"] == 0
    assert out["skill_score"] is None


def test_unmatured_rows_are_excluded_from_n_matured():
    rows = _rows(5, 4, 8)
    rows[0]["realized_value"] = None  # not yet matured
    out = score_predictions(_df(rows))
    assert out["n_matured"] == 4


# ── Floor behavior (THE deliberate contract) ─────────────────────────────────

def test_skill_score_withheld_below_floor():
    n = PREDICTION_MIN_MATURED_N - 1
    rows = _rows(n, predicted_offset=4, baseline_offset=8)
    out = score_predictions(_df(rows))
    assert out["n_matured"] == n
    assert out["skill_score"] is None  # withheld, not 0, not a fabricated number
    # MAE itself is not gated by the floor -- it's always computed when data exists.
    assert out["mae_model"] == pytest.approx(4.0)
    assert out["mae_baseline"] == pytest.approx(8.0)


def test_skill_score_computed_at_exactly_the_floor():
    n = PREDICTION_MIN_MATURED_N
    rows = _rows(n, predicted_offset=4, baseline_offset=8)
    out = score_predictions(_df(rows))
    assert out["n_matured"] == n
    assert out["skill_score"] == pytest.approx(0.5)  # 1 - 4/8


def test_skill_score_computed_above_the_floor():
    n = PREDICTION_MIN_MATURED_N + 5
    rows = _rows(n, predicted_offset=2, baseline_offset=8)
    out = score_predictions(_df(rows))
    assert out["skill_score"] == pytest.approx(0.75)  # 1 - 2/8


# ── Skill-score formula correctness ──────────────────────────────────────────

def test_skill_score_formula_matches_hand_computed_value():
    n = PREDICTION_MIN_MATURED_N + 5
    rows = _rows(n, predicted_offset=3, baseline_offset=6)
    out = score_predictions(_df(rows))
    assert out["mae_model"] == pytest.approx(3.0)
    assert out["mae_baseline"] == pytest.approx(6.0)
    assert out["skill_score"] == pytest.approx(1 - 3.0 / 6.0)


def test_skill_score_negative_when_model_worse_than_baseline():
    n = PREDICTION_MIN_MATURED_N
    rows = _rows(n, predicted_offset=10, baseline_offset=2)
    out = score_predictions(_df(rows))
    assert out["skill_score"] < 0


def test_skill_score_none_when_baseline_mae_is_zero():
    n = PREDICTION_MIN_MATURED_N
    rows = _rows(n, predicted_offset=4, baseline_offset=0)  # baseline == realized exactly
    out = score_predictions(_df(rows))
    assert out["mae_baseline"] == pytest.approx(0.0)
    assert out["skill_score"] is None  # undefined ratio, never fabricated


def test_stored_abs_error_column_preferred_over_recompute():
    n = PREDICTION_MIN_MATURED_N
    rows = _rows(n, predicted_offset=4, baseline_offset=8)
    for r in rows:
        r["abs_error"] = 1.0            # deliberately different from |4|
        r["baseline_abs_error"] = 2.0   # deliberately different from |8|
    out = score_predictions(_df(rows))
    assert out["mae_model"] == pytest.approx(1.0)
    assert out["mae_baseline"] == pytest.approx(2.0)
    assert out["skill_score"] == pytest.approx(0.5)  # 1 - 1/2


# ── Live-only subsetting ─────────────────────────────────────────────────────

def test_live_only_skill_withheld_below_floor_even_when_blended_clears_it():
    n_live = PREDICTION_MIN_MATURED_N - 5
    n_backfill = 10
    rows = (
        _rows(n_live, 2, 8, source="live")
        + _rows(n_backfill, 2, 8, source="backfill", ticker="BBB")
    )
    out = score_predictions(_df(rows))
    assert out["n_matured"] == n_live + n_backfill
    assert out["n_matured"] >= PREDICTION_MIN_MATURED_N
    assert out["skill_score"] is not None            # blended clears the floor
    assert out["n_matured_live"] == n_live
    assert out["skill_score_live_only"] is None       # live-only does NOT clear it


def test_live_only_skill_computed_when_live_rows_clear_the_floor():
    n_live = PREDICTION_MIN_MATURED_N
    rows = _rows(n_live, 2, 8, source="live")
    out = score_predictions(_df(rows))
    assert out["n_matured_live"] == n_live
    assert out["skill_score_live_only"] == pytest.approx(out["skill_score"])


def test_n_matured_backfill_counts_correctly():
    rows = (
        _rows(PREDICTION_MIN_MATURED_N, 2, 8, source="live")
        + _rows(3, 2, 8, source="backfill", ticker="CCC")
    )
    out = score_predictions(_df(rows))
    assert out["n_matured_backfill"] == 3
    assert out["n_matured_live"] == PREDICTION_MIN_MATURED_N


# ── Regime-stratified breakdown ──────────────────────────────────────────────

def test_regime_breakdown_splits_by_regime_and_is_not_floor_gated():
    calm = _rows(5, predicted_offset=1, baseline_offset=8, regime="calm", ticker="CALM")
    stress = _rows(5, predicted_offset=6, baseline_offset=8, regime="stress", ticker="STRESS")
    out = score_predictions(_df(calm + stress))
    assert out["n_matured"] == 10
    assert out["skill_score"] is None  # 10 < floor -- blended still withheld

    breakdown = out["regime_breakdown"]
    assert set(breakdown.keys()) == {"calm", "stress"}
    assert breakdown["calm"]["n"] == 5
    assert breakdown["stress"]["n"] == 5
    # Regime breakdown is NOT gated by PREDICTION_MIN_MATURED_N -- a thin
    # regime slice is shown, visibly labeled by its own small n, not hidden.
    assert breakdown["calm"]["skill_score"] == pytest.approx(1 - 1.0 / 8.0)
    assert breakdown["stress"]["skill_score"] == pytest.approx(1 - 6.0 / 8.0)


def test_regime_breakdown_groups_missing_regime_as_unknown():
    rows = _rows(5, 2, 8, regime=None, ticker="NOREGIME")
    out = score_predictions(_df(rows))
    assert "unknown" in out["regime_breakdown"]
    assert out["regime_breakdown"]["unknown"]["n"] == 5


# ── effective_n_note ─────────────────────────────────────────────────────────

def test_effective_n_note_is_a_nonempty_string_and_cites_raw_n():
    n = PREDICTION_MIN_MATURED_N
    rows = _rows(n, 2, 8)
    out = score_predictions(_df(rows))
    note = out["effective_n_note"]
    assert isinstance(note, str)
    assert str(n) in note


def test_effective_n_note_handles_single_observation_per_ticker():
    # Every row a distinct ticker -> no repeat observations to estimate a
    # stride from -- must degrade to an honest caveat, never raise.
    rows = []
    for i in range(PREDICTION_MIN_MATURED_N):
        r = _rows(1, 2, 8, ticker=f"T{i}")[0]
        rows.append(r)
    out = score_predictions(_df(rows))
    assert isinstance(out["effective_n_note"], str)
    assert out["skill_score"] == pytest.approx(0.75)  # unaffected by the note
