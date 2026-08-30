"""Regression tests for stock_analyzer/portfolio_health.py — the Portfolio
Construction Health Score (5 sub-scores -> weighted average -> A-F grade) and
Portfolio Dynamics (per-position tenure/cohort/engine-alignment). Pure
computation, no I/O, no Streamlit -- straightforward to test directly.
See docs/plans/test-automation.md for scope.
"""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from stock_analyzer import portfolio_health as ph
from tests.conftest import make_port_df


# ── grade_colors / _grade / score_color ──────────────────────────────────

def test_grade_colors_known_letter_returns_tuple():
    assert ph.grade_colors("A") == ("#15803d", "#4ade80")


def test_grade_colors_unknown_letter_falls_back_to_question_mark():
    assert ph.grade_colors("Z") == ph.grade_colors("?")


def test_grade_colors_single_string_value_duplicated_into_tuple():
    # "C" is stored as a single string (bg == border) in _GRADE_COLORS.
    assert ph.grade_colors("C") == ("#b45309", "#b45309")


@pytest.mark.parametrize("score,letter", [
    (100.0, "A"), (80.0, "A"), (79.9, "B"),
    (65.0, "B"), (64.9, "C"),
    (50.0, "C"), (49.9, "D"),
    (35.0, "D"), (34.9, "F"),
    (0.0, "F"),
])
def test_grade_boundaries(score, letter):
    assert ph._grade(score)[0] == letter


@pytest.mark.parametrize("score,color", [
    (None, "#6b7280"),
    (85.0, "#16a34a"), (80.0, "#16a34a"), (79.9, "#2563eb"),
    (65.0, "#2563eb"), (64.9, "#d97706"),
    (50.0, "#d97706"), (49.9, "#ea580c"),
    (35.0, "#ea580c"), (34.9, "#dc2626"),
    (0.0, "#dc2626"),
])
def test_score_color_boundaries(score, color):
    assert ph.score_color(score) == color


# ── _concentration_score ──────────────────────────────────────────────────

def test_concentration_score_empty_df_returns_none():
    result = ph._concentration_score(pd.DataFrame())
    assert result["score"] is None
    assert result["detail"] == {}


def test_concentration_score_comfortable_range_is_100():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 8.0, "sector": "Tech"},
        {"ticker": "MSFT", "weight": 8.0, "sector": "Tech"},
    ])
    result = ph._concentration_score(df)
    # max_name_wt=8 <= 10 (elevated) -> 100; sector Tech=16 <= 25 -> 100
    assert result["score"] == 100.0


def test_concentration_score_zero_at_or_above_ceiling():
    df = make_port_df([{"ticker": "AAPL", "weight": 15.0, "sector": "Tech"}])
    result = ph._concentration_score(df)
    assert result["score"] == 0.0
    assert result["detail"]["worst_name"] == "AAPL"
    assert result["detail"]["max_name_wt"] == 15.0


def test_concentration_score_interpolates_between_elevated_and_ceiling():
    # name at 12.5%, halfway between 10 (elevated) and 15 (ceiling) -> 50.0
    df = make_port_df([{"ticker": "AAPL", "weight": 12.5, "sector": "Tech"}])
    result = ph._concentration_score(df)
    assert result["score"] == 50.0


def test_concentration_score_sector_drives_score_when_worse_than_name():
    # Two names each under the single-name ceiling but same sector sums to 35
    # (at the sector ceiling) -> sector_score 0, which is the binding min().
    df = make_port_df([
        {"ticker": "AAPL", "weight": 17.5, "sector": "Tech"},
        {"ticker": "MSFT", "weight": 17.5, "sector": "Tech"},
    ])
    result = ph._concentration_score(df)
    assert result["score"] == 0.0
    assert result["detail"]["worst_sector"] == "Tech"
    assert result["detail"]["max_sector_wt"] == 35.0


def test_concentration_score_uses_gate_weight_column_when_present():
    df = make_port_df([{"ticker": "AAPL", "weight": 8.0, "sector": "Tech"}])
    df["Gate Weight (%)"] = [20.0]
    result = ph._concentration_score(df)
    # Gate Weight (20%) is above the single-name ceiling -> 0, overriding the
    # comfortable 8% Weight (%) value.
    assert result["score"] == 0.0
    assert result["detail"]["max_name_wt"] == 20.0


def test_concentration_score_no_sector_column_defaults_sector_score_to_full():
    df = pd.DataFrame({"Ticker": ["AAPL"], "Weight (%)": [8.0]})
    result = ph._concentration_score(df)
    assert result["detail"]["max_sector_wt"] == 0.0
    assert result["detail"]["worst_sector"] is None
    assert result["score"] == 100.0


# ── _sector_balance_score ─────────────────────────────────────────────────

def test_sector_balance_score_empty_df_returns_none():
    assert ph._sector_balance_score(pd.DataFrame())["score"] is None


def test_sector_balance_score_no_sector_column_returns_none():
    df = pd.DataFrame({"Ticker": ["AAPL"], "Weight (%)": [10.0]})
    assert ph._sector_balance_score(df)["score"] is None


def test_sector_balance_score_single_sector_scores_10():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 50.0, "sector": "Tech"},
        {"ticker": "MSFT", "weight": 50.0, "sector": "Tech"},
    ])
    result = ph._sector_balance_score(df)
    assert result["score"] == 10.0
    assert result["detail"]["n_sectors"] == 1
    assert result["detail"]["normalized_entropy"] == 0.0


def test_sector_balance_score_two_sectors_perfectly_balanced_capped_at_55():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 50.0, "sector": "Tech"},
        {"ticker": "XOM", "weight": 50.0, "sector": "Energy"},
    ])
    result = ph._sector_balance_score(df)
    # normalized entropy = 1.0 (perfectly balanced) -> raw 100, capped at 55
    assert result["score"] == 55.0
    assert result["detail"]["n_sectors"] == 2
    assert result["detail"]["normalized_entropy"] == 1.0


def test_sector_balance_score_three_sectors_perfectly_balanced_not_capped():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 33.3, "sector": "Tech"},
        {"ticker": "XOM", "weight": 33.3, "sector": "Energy"},
        {"ticker": "JPM", "weight": 33.4, "sector": "Financials"},
    ])
    result = ph._sector_balance_score(df)
    assert result["score"] == 100.0
    assert result["detail"]["n_sectors"] == 3


def test_sector_balance_score_uneven_three_sectors_below_100():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 80.0, "sector": "Tech"},
        {"ticker": "XOM", "weight": 10.0, "sector": "Energy"},
        {"ticker": "JPM", "weight": 10.0, "sector": "Financials"},
    ])
    result = ph._sector_balance_score(df)
    assert 0.0 < result["score"] < 100.0


def test_sector_balance_score_falls_back_to_gate_weight_column():
    df = pd.DataFrame({
        "Ticker": ["AAPL", "XOM"], "Sector": ["Tech", "Energy"],
        "Gate Weight (%)": [50.0, 50.0],
    })
    result = ph._sector_balance_score(df)
    assert result["score"] == 55.0  # same 2-sector balanced-and-capped case


# ── _diversification_score_sub ────────────────────────────────────────────

def test_diversification_score_none_when_both_inputs_none():
    assert ph._diversification_score_sub(None, None)["score"] is None


def test_diversification_score_uses_avg_corr_when_present():
    result = ph._diversification_score_sub(div_score_val=42.0, avg_corr=0.0)
    assert result["score"] == 100.0  # uncorrelated -> max score
    assert result["detail"]["avg_corr"] == 0.0


def test_diversification_score_avg_corr_midpoint():
    result = ph._diversification_score_sub(None, avg_corr=0.5)
    assert result["score"] == 50.0


def test_diversification_score_avg_corr_clamped_to_zero_above_1():
    result = ph._diversification_score_sub(None, avg_corr=1.5)
    assert result["score"] == 0.0


def test_diversification_score_falls_back_to_div_score_val_when_no_corr():
    result = ph._diversification_score_sub(div_score_val=37.5, avg_corr=None)
    assert result["score"] == 37.5
    assert result["detail"]["avg_corr"] is None


# ── _factor_exposure_score ─────────────────────────────────────────────────

def test_factor_exposure_score_none_when_both_inputs_none():
    assert ph._factor_exposure_score(None, None, None)["score"] is None


def test_factor_exposure_score_calm_no_hb_penalty():
    result = ph._factor_exposure_score({"severity": "calm"}, hb_share=10.0, port_beta=0.9)
    assert result["score"] == 85.0


def test_factor_exposure_score_caution_severity():
    result = ph._factor_exposure_score({"severity": "caution"}, hb_share=None, port_beta=1.1)
    assert result["score"] == 55.0


def test_factor_exposure_score_fragile_severity():
    result = ph._factor_exposure_score({"severity": "fragile"}, hb_share=None, port_beta=1.5)
    assert result["score"] == 20.0


def test_factor_exposure_score_unknown_severity_defaults_65():
    result = ph._factor_exposure_score({"severity": "made-up"}, hb_share=None, port_beta=1.0)
    assert result["score"] == 65.0


def test_factor_exposure_score_high_beta_share_penalty_tiers():
    low_result = ph._factor_exposure_score({"severity": "calm"}, hb_share=39.9, port_beta=1.0)
    mid_result = ph._factor_exposure_score({"severity": "calm"}, hb_share=40.0, port_beta=1.0)
    high_result = ph._factor_exposure_score({"severity": "calm"}, hb_share=60.0, port_beta=1.0)
    assert low_result["score"] == 85.0    # no penalty below 40
    assert mid_result["score"] == 73.0    # 85 - 12
    assert high_result["score"] == 60.0   # 85 - 25


def test_factor_exposure_score_clamped_to_zero_floor():
    result = ph._factor_exposure_score({"severity": "fragile"}, hb_share=60.0, port_beta=2.0)
    assert result["score"] == 0.0  # 20 - 25 clamped at 0, not negative


def test_factor_exposure_score_fragility_none_abstains_even_with_beta_present():
    # Surface-proprioception F-260 finding #7: port_beta is display-only in
    # `detail` and never feeds `base`/`hb_penalty` -- fragility=None means
    # severity can't be known, so the score must abstain (None) rather than
    # fabricate the neutral-65 default. A real number here would make
    # compute_health_score's n_available count this dimension "available",
    # suppressing the "some dimensions unavailable" banner precisely when
    # this dimension is the one that couldn't be measured.
    result = ph._factor_exposure_score(None, hb_share=None, port_beta=1.2)
    assert result["score"] is None
    assert result["detail"] == {}


def test_factor_exposure_score_non_dict_fragility_abstains():
    for bad in ["not a dict", 42, []]:
        assert ph._factor_exposure_score(bad, hb_share=None, port_beta=1.0)["score"] is None


# ── _signal_integrity_score ────────────────────────────────────────────────

def test_signal_integrity_score_empty_df_returns_none():
    assert ph._signal_integrity_score(pd.DataFrame())["score"] is None


def test_signal_integrity_score_no_score_column_returns_none():
    df = pd.DataFrame({"Ticker": ["AAPL"], "Weight (%)": [10.0]})
    assert ph._signal_integrity_score(df)["score"] is None


def test_signal_integrity_score_zero_total_weight_returns_none():
    df = make_port_df([{"ticker": "AAPL", "weight": 0.0, "score": 70.0}])
    assert ph._signal_integrity_score(df)["score"] is None


def test_signal_integrity_score_all_buy_weight_is_100():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 50.0, "score": 70.0},
        {"ticker": "MSFT", "weight": 50.0, "score": 80.0},
    ])
    result = ph._signal_integrity_score(df)
    assert result["score"] == 100.0
    assert result["detail"]["n_below_hold"] == 0


def test_signal_integrity_score_mixed_weights_and_below_hold_count():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 60.0, "score": 70.0},   # Buy
        {"ticker": "MSFT", "weight": 40.0, "score": 40.0},   # below Hold floor (44)
    ])
    result = ph._signal_integrity_score(df)
    assert result["score"] == 60.0
    assert result["detail"]["n_below_hold"] == 1
    assert result["detail"]["weighted_avg_composite"] == 58.0  # (70*60+40*40)/100


def test_signal_integrity_score_nan_scores_excluded_from_weighted_avg():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 50.0, "score": 70.0},
        {"ticker": "MSFT", "weight": 50.0, "score": 80.0},
    ])
    df.loc[1, "Score"] = float("nan")
    result = ph._signal_integrity_score(df)
    assert result["detail"]["weighted_avg_composite"] == 70.0


# ── _build_specific ────────────────────────────────────────────────────────

def test_build_specific_returns_none_for_empty_detail():
    assert ph._build_specific("concentration", {}) is None


def test_build_specific_concentration_name_driven():
    detail = {"max_name_wt": 14.0, "worst_name": "AAPL", "max_sector_wt": 10.0,
              "worst_sector": "Tech", "name_ceiling": 15.0, "sector_ceiling": 35.0}
    text = ph._build_specific("concentration", detail)
    assert "AAPL" in text
    assert "Tech" not in text  # sector ratio (10/35) well below name ratio (14/15)


def test_build_specific_concentration_both_shown_when_both_elevated():
    detail = {"max_name_wt": 14.0, "worst_name": "AAPL", "max_sector_wt": 30.0,
              "worst_sector": "Tech", "name_ceiling": 15.0, "sector_ceiling": 35.0}
    text = ph._build_specific("concentration", detail)
    assert "AAPL" in text and "Tech" in text


def test_build_specific_sector_balance_with_weights():
    detail = {"n_sectors": 3, "sector_weights": {"Tech": 50.0, "Energy": 25.0, "Financials": 25.0}}
    text = ph._build_specific("sector_balance", detail)
    assert "3 sectors" in text and "Tech" in text


def test_build_specific_sector_balance_without_weights():
    detail = {"n_sectors": 1, "sector_weights": {}}
    assert ph._build_specific("sector_balance", detail) == "1 sector(s) represented"


def test_build_specific_diversification():
    assert "0.42" in ph._build_specific("diversification", {"avg_corr": 0.42})


def test_build_specific_diversification_none_avg_corr():
    assert ph._build_specific("diversification", {"avg_corr": None}) is None


def test_build_specific_factor_exposure_full():
    detail = {"severity": "fragile", "port_beta": 1.45, "hb_share": 62.0}
    text = ph._build_specific("factor_exposure", detail)
    assert "Fragile" in text and "1.45" in text and "62%" in text


def test_build_specific_signal_integrity_both_parts():
    detail = {"pct_buy_weight": 30.0, "n_below_hold": 2}
    text = ph._build_specific("signal_integrity", detail)
    assert "30%" in text and "2" in text


def test_build_specific_unknown_key_returns_none():
    assert ph._build_specific("nonexistent", {"x": 1}) is None


# ── _build_improvements ────────────────────────────────────────────────────

def test_build_improvements_picks_top_2_lowest_scores():
    scored_asc = [("concentration", 20.0), ("sector_balance", 30.0), ("diversification", 90.0)]
    sub_scores = {
        "concentration": {"detail": {}}, "sector_balance": {"detail": {}},
        "diversification": {"detail": {}},
    }
    result = ph._build_improvements(scored_asc, sub_scores)
    assert len(result) == 2
    assert [r["dimension"] for r in result] == ["concentration", "sector_balance"]


def test_build_improvements_low_bucket_below_40_mid_at_or_above():
    scored_asc = [("concentration", 39.9), ("sector_balance", 40.0)]
    sub_scores = {"concentration": {"detail": {}}, "sector_balance": {"detail": {}}}
    result = ph._build_improvements(scored_asc, sub_scores)
    assert result[0]["action"] == ph._IMPROVEMENT["concentration"]["low"]
    assert result[1]["action"] == ph._IMPROVEMENT["sector_balance"]["mid"]


# ── compute_health_score ────────────────────────────────────────────────────

def test_compute_health_score_no_data_at_all_returns_question_mark():
    result = ph.compute_health_score(pd.DataFrame(), None, None, None, None, None)
    assert result["overall"] is None
    assert result["grade"] == "?"
    assert result["grade_label"] == "Insufficient data"
    assert result["n_available"] == 0


def test_compute_health_score_averages_available_sub_scores():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 50.0, "sector": "Tech", "score": 70.0},
        {"ticker": "XOM", "weight": 50.0, "sector": "Energy", "score": 70.0},
    ])
    result = ph.compute_health_score(
        df, div_score_val=None, avg_corr=0.2, hb_share=10.0,
        fragility={"severity": "calm"}, port_risk={"beta": 1.0},
    )
    assert result["n_available"] == 5
    assert result["overall"] is not None
    assert result["grade"] in {"A", "B", "C", "D", "F"}
    assert len(result["improvements"]) <= 2


def test_compute_health_score_fragility_offline_drops_n_available_to_4():
    # Pins the F-260 finding #7 fix at the compute_health_score level: with
    # fragility unavailable but port_beta present, factor_exposure must NOT
    # count toward n_available -- otherwise app.py's `n_available < 5` banner
    # ("Some dimensions are unavailable") never fires for exactly the session
    # where a dimension genuinely couldn't be measured.
    df = make_port_df([
        {"ticker": "AAPL", "weight": 50.0, "sector": "Tech", "score": 70.0},
        {"ticker": "XOM", "weight": 50.0, "sector": "Energy", "score": 70.0},
    ])
    result = ph.compute_health_score(
        df, div_score_val=None, avg_corr=0.2, hb_share=10.0,
        fragility=None, port_risk={"beta": 1.0},
    )
    assert result["n_available"] == 4
    assert result["sub_scores"]["factor_exposure"]["score"] is None


def test_compute_health_score_improvements_sorted_by_worst_first():
    df = make_port_df([{"ticker": "AAPL", "weight": 15.0, "sector": "Tech", "score": 20.0}])
    result = ph.compute_health_score(
        df, div_score_val=None, avg_corr=0.9, hb_share=70.0,
        fragility={"severity": "fragile"}, port_risk={"beta": 1.6},
    )
    scores = [s["score"] for s in result["improvements"]]
    assert scores == sorted(scores)


# ── compute_portfolio_dynamics ──────────────────────────────────────────────

def _trades_df(rows):
    """rows: list of (ticker, days_ago, action, shares)."""
    today = datetime.now()
    return pd.DataFrame([
        {"id": i, "ticker": t, "traded_at": (today - timedelta(days=d)).isoformat(),
         "action": a, "shares": sh}
        for i, (t, d, a, sh) in enumerate(rows)
    ])


def test_portfolio_dynamics_no_trades_df_all_unknown_cohort():
    df = make_port_df([{"ticker": "AAPL", "weight": 100.0, "score": 70.0, "pnl_pct": 5.0}])
    result = ph.compute_portfolio_dynamics(df, None)
    assert result["has_tenure_data"] is False
    assert result["positions"][0]["cohort"] == "Unknown"
    assert result["positions"][0]["months_held"] is None
    assert result["positions"][0]["annualized_return"] is None


def test_portfolio_dynamics_fresh_cohort_under_30_days():
    df = make_port_df([{"ticker": "AAPL", "weight": 100.0, "score": 70.0, "pnl_pct": 5.0}])
    trades = _trades_df([("AAPL", 10, "BUY", 10)])
    result = ph.compute_portfolio_dynamics(df, trades)
    pos = result["positions"][0]
    assert pos["cohort"] == "Fresh"
    assert result["has_tenure_data"] is True


def test_portfolio_dynamics_growing_cohort_boundary_at_6_months():
    df = make_port_df([{"ticker": "AAPL", "weight": 100.0, "score": 70.0, "pnl_pct": 5.0}])
    # 6 months * 30.44 = 182.64 days -> use 182 days (just under 6.0 months) to
    # land inside the Growing band, not Established.
    trades = _trades_df([("AAPL", 182, "BUY", 10)])
    result = ph.compute_portfolio_dynamics(df, trades)
    assert result["positions"][0]["cohort"] == "Growing"


def test_portfolio_dynamics_established_cohort_over_6_months():
    df = make_port_df([{"ticker": "AAPL", "weight": 100.0, "score": 70.0, "pnl_pct": 5.0}])
    trades = _trades_df([("AAPL", 400, "BUY", 10)])
    result = ph.compute_portfolio_dynamics(df, trades)
    assert result["positions"][0]["cohort"] == "Established"


def test_portfolio_dynamics_annualized_return_requires_half_month_held():
    df = make_port_df([{"ticker": "AAPL", "weight": 100.0, "score": 70.0, "pnl_pct": 10.0}])
    trades = _trades_df([("AAPL", 5, "BUY", 10)])  # ~0.16 months, below the 0.5 gate
    result = ph.compute_portfolio_dynamics(df, trades)
    assert result["positions"][0]["annualized_return"] is None


def test_portfolio_dynamics_annualized_return_computed_past_gate():
    df = make_port_df([{"ticker": "AAPL", "weight": 100.0, "score": 70.0, "pnl_pct": 10.0}])
    trades = _trades_df([("AAPL", 60, "BUY", 10)])  # ~1.97 months
    result = ph.compute_portfolio_dynamics(df, trades)
    pos = result["positions"][0]
    assert pos["annualized_return"] == round(10.0 * (12.0 / pos["months_held"]), 1)


@pytest.mark.parametrize("score,verdict", [
    (65.0, "BUY"), (64.9, "HOLD"), (44.0, "HOLD"), (43.9, "WATCH"),
    (30.0, "WATCH"), (29.9, "EXIT"), (0.0, "EXIT"),
])
def test_portfolio_dynamics_verdict_ladder_boundaries(score, verdict):
    df = make_port_df([{"ticker": "AAPL", "weight": 100.0, "score": score}])
    result = ph.compute_portfolio_dynamics(df, None)
    assert result["positions"][0]["verdict"] == verdict


def test_portfolio_dynamics_cohort_data_ordering_and_skips_empty():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 50.0, "score": 70.0, "pnl_pct": 5.0},
        {"ticker": "XOM", "weight": 50.0, "score": 70.0, "pnl_pct": -2.0},
    ])
    trades = _trades_df([("AAPL", 10, "BUY", 10), ("XOM", 400, "BUY", 10)])
    result = ph.compute_portfolio_dynamics(df, trades)
    cohorts = [c["cohort"] for c in result["cohort_data"]]
    assert cohorts == ["Fresh", "Established"]  # Growing/Unknown skipped, order preserved


def test_portfolio_dynamics_cohort_avg_pnl_and_tickers():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 50.0, "score": 70.0, "pnl_pct": 10.0},
        {"ticker": "MSFT", "weight": 50.0, "score": 70.0, "pnl_pct": 20.0},
    ])
    trades = _trades_df([("AAPL", 10, "BUY", 10), ("MSFT", 15, "BUY", 10)])
    result = ph.compute_portfolio_dynamics(df, trades)
    fresh = next(c for c in result["cohort_data"] if c["cohort"] == "Fresh")
    assert fresh["count"] == 2
    assert fresh["avg_pnl"] == 15.0
    assert set(fresh["tickers"]) == {"AAPL", "MSFT"}


def test_portfolio_dynamics_alignment_counts_and_weights():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 60.0, "score": 70.0},   # BUY
        {"ticker": "MSFT", "weight": 40.0, "score": 20.0},   # EXIT
    ])
    result = ph.compute_portfolio_dynamics(df, None)
    assert result["alignment"] == {"BUY": 1, "HOLD": 0, "WATCH": 0, "EXIT": 1}
    assert result["align_weight"] == {"BUY": 60.0, "HOLD": 0.0, "WATCH": 0.0, "EXIT": 40.0}


def test_portfolio_dynamics_vitality_pct_is_buy_plus_hold_share():
    df = make_port_df([
        {"ticker": "AAPL", "weight": 50.0, "score": 70.0},   # BUY
        {"ticker": "MSFT", "weight": 25.0, "score": 50.0},   # HOLD
        {"ticker": "XOM", "weight": 25.0, "score": 10.0},    # EXIT
    ])
    result = ph.compute_portfolio_dynamics(df, None)
    assert result["vitality_pct"] == 67  # round(2/3 * 100)


def test_portfolio_dynamics_empty_portfolio_zero_vitality_no_crash():
    result = ph.compute_portfolio_dynamics(pd.DataFrame({"Ticker": [], "Score": []}), None)
    assert result["n_positions"] == 0
    assert result["vitality_pct"] == 0
    assert result["positions"] == []


def test_portfolio_dynamics_resets_on_reentry_uses_open_lot_only():
    # A closed-then-reopened position should NOT inherit the original buy
    # date (the whole point of _build_open_lots FIFO replay).
    df = make_port_df([{"ticker": "AAPL", "weight": 100.0, "score": 70.0}])
    trades = _trades_df([
        ("AAPL", 400, "BUY", 10),
        ("AAPL", 200, "SELL", 10),
        ("AAPL", 5, "BUY", 10),
    ])
    result = ph.compute_portfolio_dynamics(df, trades)
    assert result["positions"][0]["cohort"] == "Fresh"


def test_portfolio_dynamics_open_lots_exception_is_swallowed():
    df = make_port_df([{"ticker": "AAPL", "weight": 100.0, "score": 70.0}])
    bad_trades = pd.DataFrame({"not_ticker_column": ["x"]})
    result = ph.compute_portfolio_dynamics(df, bad_trades)
    assert result["positions"][0]["cohort"] == "Unknown"
    assert result["has_tenure_data"] is False
