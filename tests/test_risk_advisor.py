"""Regression tests for stock_analyzer/risk_advisor.py's
build_risk_advisor_recommendations() — the 7-metric portfolio risk advisor.

Each portfolio-level metric (beta/sharpe/volatility/drawdown/tail-risk) has
its own HIGH/MEDIUM/(OK) priority ladder, several of which have a "dead zone"
between the action bands and the OK band where NO recommendation fires at
all (e.g. Sharpe 0.8-1.0, drawdown -20% to -10%) -- these are easy to get
wrong in a refactor since there's no rec object to assert *against*, only an
absence. Tests build a small synthetic portfolio via
tests.conftest.make_risk_advisor_inputs so each case varies only the field(s)
relevant to the branch under test.
"""
import pandas as pd

from stock_analyzer.constants import (
    DRAWDOWN_CONTRIB_MAX,
    PORTFOLIO_DRAWDOWN_ACTION_MAX,
    PORTFOLIO_DRAWDOWN_HIGH_MAX,
    PORTFOLIO_DRAWDOWN_OK_MIN,
    PORTFOLIO_VOL_HIGH_PCT,
    PORTFOLIO_VOL_MEDIUM_PCT,
    SECTOR_CEILING,
    SECTOR_ELEVATED,
    SHARPE_DRAG_MIN_WEIGHT_PCT,
    SHARPE_DRAG_RELATIVE_MAX,
    SHARPE_HIGH_RISK_MAX,
    SHARPE_MEDIUM_RISK_MAX,
    SHARPE_STRONG_MIN,
    SINGLE_NAME_CEILING,
    TAIL_RATIO_ACTION_MIN,
    TAIL_RATIO_HIGH_MIN,
    WEAK_CONVICTION_SCORE,
)
from stock_analyzer.risk_advisor import build_risk_advisor_recommendations
from tests.conftest import find_rec, make_risk_advisor_inputs


_ONE_ROW = [{"ticker": "AAA", "weight": 20.0, "market_value": 20_000.0, "beta": 1.0}]


def _recs(rows, **overrides):
    return build_risk_advisor_recommendations(*make_risk_advisor_inputs(rows, **overrides))


# ── guard clauses ─────────────────────────────────────────────────────────────

def test_empty_port_df_returns_empty():
    _unused_df, held_data, port_risk, h_rets, pv, gd = make_risk_advisor_inputs(_ONE_ROW)
    assert build_risk_advisor_recommendations(pd.DataFrame(), held_data, port_risk, h_rets, pv, gd) == []


def test_empty_port_risk_returns_empty():
    port_df, held_data, _unused_risk, h_rets, pv, gd = make_risk_advisor_inputs(_ONE_ROW)
    assert build_risk_advisor_recommendations(port_df, held_data, {}, h_rets, pv, gd) == []


def test_missing_or_nonpositive_portfolio_value_returns_empty():
    port_df, held_data, port_risk, h_rets, _unused_pv, gd = make_risk_advisor_inputs(_ONE_ROW)
    assert build_risk_advisor_recommendations(port_df, held_data, port_risk, h_rets, None, gd) == []  # type: ignore[arg-type]
    assert build_risk_advisor_recommendations(port_df, held_data, port_risk, h_rets, 0.0, gd) == []


# ── beta ──────────────────────────────────────────────────────────────────────

def test_beta_high_priority_above_ceiling():
    recs = _recs(_ONE_ROW, beta=1.6)  # PORTFOLIO_BETA_CEILING = 1.4
    rec = find_rec(recs, "beta")
    assert rec is not None
    assert rec["priority"] == "HIGH"


def test_beta_medium_priority_above_elevated_below_ceiling():
    recs = _recs(_ONE_ROW, beta=1.35)  # between ELEVATED(1.3) and CEILING(1.4)
    rec = find_rec(recs, "beta")
    assert rec is not None
    assert rec["priority"] == "MEDIUM"


def test_beta_ok_below_elevated():
    recs = _recs(_ONE_ROW, beta=1.0)
    assert find_rec(recs, "beta") is None
    assert find_rec(recs, "ok_beta") is not None


def test_beta_root_tickers_sorted_by_contribution():
    rows = [
        {"ticker": "LOW", "weight": 10.0, "market_value": 10_000.0, "beta": 1.2},
        {"ticker": "HIGH", "weight": 30.0, "market_value": 30_000.0, "beta": 2.5},
    ]
    recs = _recs(rows, beta=1.6)
    rec = find_rec(recs, "beta")
    assert rec is not None
    assert rec["root_tickers"][0]["ticker"] == "HIGH"  # bigger beta*weight contribution first


# ── sharpe ────────────────────────────────────────────────────────────────────

def test_sharpe_high_below_0_4():
    recs = _recs(_ONE_ROW, sharpe=0.3)
    rec = find_rec(recs, "sharpe")
    assert rec is not None
    assert rec["priority"] == "HIGH"


def test_sharpe_medium_between_0_4_and_0_8():
    recs = _recs(_ONE_ROW, sharpe=0.6)
    rec = find_rec(recs, "sharpe")
    assert rec is not None
    assert rec["priority"] == "MEDIUM"


def test_sharpe_ok_at_or_above_1_0():
    recs = _recs(_ONE_ROW, sharpe=1.2)
    assert find_rec(recs, "sharpe") is None
    assert find_rec(recs, "ok_sharpe") is not None


def test_sharpe_dead_zone_produces_no_rec():
    # 0.8 <= sharpe < 1.0 is neither the action ladder nor the OK ladder.
    recs = _recs(_ONE_ROW, sharpe=0.9)
    assert find_rec(recs, "sharpe") is None
    assert find_rec(recs, "ok_sharpe") is None


def test_sharpe_high_priority_boundary_pinned_to_constant():
    # Just below SHARPE_HIGH_RISK_MAX -> HIGH; regression pin for the
    # constants.py extraction (was an inline 0.4 literal).
    recs = _recs(_ONE_ROW, sharpe=SHARPE_HIGH_RISK_MAX - 0.01)
    assert find_rec(recs, "sharpe")["priority"] == "HIGH"


def test_sharpe_medium_priority_boundary_pinned_to_constant():
    # At exactly SHARPE_HIGH_RISK_MAX -> MEDIUM (the HIGH branch is a strict <).
    recs = _recs(_ONE_ROW, sharpe=SHARPE_HIGH_RISK_MAX)
    assert find_rec(recs, "sharpe")["priority"] == "MEDIUM"


def test_sharpe_action_ladder_boundary_pinned_to_constant():
    # At exactly SHARPE_MEDIUM_RISK_MAX, the action ladder no longer fires
    # (strict < gate) -- regression pin for the constants.py extraction.
    recs = _recs(_ONE_ROW, sharpe=SHARPE_MEDIUM_RISK_MAX)
    assert find_rec(recs, "sharpe") is None


def test_sharpe_ok_boundary_pinned_to_constant():
    recs = _recs(_ONE_ROW, sharpe=SHARPE_STRONG_MIN)
    assert find_rec(recs, "ok_sharpe") is not None


def test_sharpe_drag_selection_requires_both_relative_and_weight_floor():
    # A ticker only gets named as a "drag" when its own Sharpe trails the
    # portfolio Sharpe by SHARPE_DRAG_RELATIVE_MAX AND its weight clears
    # SHARPE_DRAG_MIN_WEIGHT_PCT -- regression pin for the constants.py
    # extraction (was inline 0.7 / 3.0 literals).
    port_sharpe = 0.5
    rows = [
        {  # trails enough, but weight is below the floor -> NOT named
            "ticker": "TOO_SMALL", "weight": SHARPE_DRAG_MIN_WEIGHT_PCT - 1,
            "market_value": 1_000.0, "sharpe": port_sharpe * SHARPE_DRAG_RELATIVE_MAX - 0.01,
        },
        {  # big enough weight, but doesn't trail enough -> NOT named
            "ticker": "NOT_LAGGING", "weight": SHARPE_DRAG_MIN_WEIGHT_PCT + 5,
            "market_value": 10_000.0, "sharpe": port_sharpe * SHARPE_DRAG_RELATIVE_MAX + 0.01,
        },
        {  # both conditions clear -> named as a drag
            "ticker": "REAL_DRAG", "weight": SHARPE_DRAG_MIN_WEIGHT_PCT + 5,
            "market_value": 10_000.0, "sharpe": port_sharpe * SHARPE_DRAG_RELATIVE_MAX - 0.01,
        },
    ]
    recs = _recs(rows, sharpe=port_sharpe)
    rec = find_rec(recs, "sharpe")
    assert rec is not None
    drag_tickers = [t["ticker"] for t in rec["root_tickers"]]
    assert drag_tickers == ["REAL_DRAG"]


# ── volatility ────────────────────────────────────────────────────────────────

def test_volatility_high_above_30():
    recs = _recs(_ONE_ROW, ann_volatility=35.0)
    rec = find_rec(recs, "volatility")
    assert rec is not None
    assert rec["priority"] == "HIGH"


def test_volatility_medium_between_25_and_30():
    recs = _recs(_ONE_ROW, ann_volatility=27.0)
    rec = find_rec(recs, "volatility")
    assert rec is not None
    assert rec["priority"] == "MEDIUM"


def test_volatility_no_rec_at_or_below_25():
    # Volatility has no "OK" sub-type, unlike beta/sharpe/drawdown -- just silence.
    recs = _recs(_ONE_ROW, ann_volatility=20.0)
    assert find_rec(recs, "volatility") is None


def test_volatility_high_boundary_pinned_to_constant():
    recs = _recs(_ONE_ROW, ann_volatility=PORTFOLIO_VOL_HIGH_PCT + 0.01)
    assert find_rec(recs, "volatility")["priority"] == "HIGH"


def test_volatility_medium_boundary_pinned_to_constant():
    # At exactly PORTFOLIO_VOL_HIGH_PCT, HIGH's strict > gate doesn't fire.
    recs = _recs(_ONE_ROW, ann_volatility=PORTFOLIO_VOL_HIGH_PCT)
    assert find_rec(recs, "volatility")["priority"] == "MEDIUM"


def test_volatility_no_rec_boundary_pinned_to_constant():
    # At exactly PORTFOLIO_VOL_MEDIUM_PCT, no rec fires (strict > gate).
    recs = _recs(_ONE_ROW, ann_volatility=PORTFOLIO_VOL_MEDIUM_PCT)
    assert find_rec(recs, "volatility") is None


# ── max drawdown ──────────────────────────────────────────────────────────────

def test_drawdown_high_below_neg30():
    recs = _recs(_ONE_ROW, max_drawdown=-35.0)
    rec = find_rec(recs, "drawdown")
    assert rec is not None
    assert rec["priority"] == "HIGH"


def test_drawdown_medium_between_neg30_and_neg20():
    recs = _recs(_ONE_ROW, max_drawdown=-25.0)
    rec = find_rec(recs, "drawdown")
    assert rec is not None
    assert rec["priority"] == "MEDIUM"


def test_drawdown_ok_above_neg10():
    recs = _recs(_ONE_ROW, max_drawdown=-5.0)
    assert find_rec(recs, "drawdown") is None
    assert find_rec(recs, "ok_drawdown") is not None


def test_drawdown_dead_zone_produces_no_rec():
    # -20% < max_dd <= -10% fires neither the action ladder nor the OK ladder.
    recs = _recs(_ONE_ROW, max_drawdown=-15.0)
    assert find_rec(recs, "drawdown") is None
    assert find_rec(recs, "ok_drawdown") is None


def test_drawdown_high_boundary_pinned_to_constant():
    recs = _recs(_ONE_ROW, max_drawdown=PORTFOLIO_DRAWDOWN_HIGH_MAX - 0.01)
    assert find_rec(recs, "drawdown")["priority"] == "HIGH"


def test_drawdown_medium_boundary_pinned_to_constant():
    # At exactly PORTFOLIO_DRAWDOWN_HIGH_MAX, HIGH's strict < gate doesn't fire.
    recs = _recs(_ONE_ROW, max_drawdown=PORTFOLIO_DRAWDOWN_HIGH_MAX)
    assert find_rec(recs, "drawdown")["priority"] == "MEDIUM"


def test_drawdown_action_ladder_boundary_pinned_to_constant():
    # At exactly PORTFOLIO_DRAWDOWN_ACTION_MAX, the action ladder no longer
    # fires (strict < gate).
    recs = _recs(_ONE_ROW, max_drawdown=PORTFOLIO_DRAWDOWN_ACTION_MAX)
    assert find_rec(recs, "drawdown") is None


def test_drawdown_ok_boundary_pinned_to_constant():
    recs = _recs(_ONE_ROW, max_drawdown=PORTFOLIO_DRAWDOWN_OK_MIN + 0.01)
    assert find_rec(recs, "ok_drawdown") is not None


def test_drawdown_contributor_selection_boundary_pinned_to_constant():
    # A per-ticker drawdown just at DRAWDOWN_CONTRIB_MAX is NOT named as a
    # contributor (strict < gate) -- regression pin for the constants.py
    # extraction (was an inline -15 literal).
    rows = [{
        "ticker": "AAA", "weight": 20.0, "market_value": 20_000.0,
        "max_drawdown": DRAWDOWN_CONTRIB_MAX,
    }]
    recs = _recs(rows, max_drawdown=-35.0)
    rec = find_rec(recs, "drawdown")
    assert rec is not None
    assert rec["root_tickers"] == []


# ── tail risk ─────────────────────────────────────────────────────────────────

def test_tail_risk_high_above_2_2_ratio():
    recs = _recs(_ONE_ROW, var_95_pct=-2.0, cvar_95_pct=-5.0)  # ratio 2.5
    rec = find_rec(recs, "tail_risk")
    assert rec is not None
    assert rec["priority"] == "HIGH"


def test_tail_risk_medium_between_1_7_and_2_2():
    recs = _recs(_ONE_ROW, var_95_pct=-2.0, cvar_95_pct=-4.0)  # ratio 2.0
    rec = find_rec(recs, "tail_risk")
    assert rec is not None
    assert rec["priority"] == "MEDIUM"


def test_tail_risk_no_rec_at_or_below_1_7():
    recs = _recs(_ONE_ROW, var_95_pct=-2.0, cvar_95_pct=-3.0)  # ratio 1.5
    assert find_rec(recs, "tail_risk") is None


def test_tail_risk_requires_both_metrics_present():
    recs = _recs(_ONE_ROW, var_95_pct=-2.0, cvar_95_pct=None)
    assert find_rec(recs, "tail_risk") is None


def test_tail_risk_high_boundary_pinned_to_constant():
    var = -2.0
    recs = _recs(_ONE_ROW, var_95_pct=var, cvar_95_pct=var * (TAIL_RATIO_HIGH_MIN + 0.01))
    assert find_rec(recs, "tail_risk")["priority"] == "HIGH"


def test_tail_risk_medium_boundary_pinned_to_constant():
    # Ratio exactly at TAIL_RATIO_HIGH_MIN -> HIGH's strict > gate doesn't fire.
    var = -2.0
    recs = _recs(_ONE_ROW, var_95_pct=var, cvar_95_pct=var * TAIL_RATIO_HIGH_MIN)
    assert find_rec(recs, "tail_risk")["priority"] == "MEDIUM"


def test_tail_risk_no_rec_boundary_pinned_to_constant():
    # Ratio exactly at TAIL_RATIO_ACTION_MIN -> no rec (strict > gate).
    var = -2.0
    recs = _recs(_ONE_ROW, var_95_pct=var, cvar_95_pct=var * TAIL_RATIO_ACTION_MIN)
    assert find_rec(recs, "tail_risk") is None


# ── sector concentration ──────────────────────────────────────────────────────

def test_sector_hard_breach_above_ceiling():
    rows = [{"ticker": "AAA", "weight": SECTOR_CEILING + 5, "market_value": 40_000.0, "sector": "Tech"}]
    recs = _recs(rows)
    rec = find_rec(recs, "sector_concentration")
    assert rec is not None
    assert rec["priority"] == "HIGH"


def test_sector_elevated_between_elevated_and_ceiling():
    rows = [{"ticker": "AAA", "weight": SECTOR_ELEVATED + 2, "market_value": 27_000.0, "sector": "Tech"}]
    recs = _recs(rows)
    rec = find_rec(recs, "sector_concentration")
    assert rec is not None
    assert rec["priority"] == "MEDIUM"


def test_sector_below_elevated_produces_no_rec():
    rows = [{"ticker": "AAA", "weight": SECTOR_ELEVATED - 5, "market_value": 20_000.0, "sector": "Tech"}]
    recs = _recs(rows)
    assert find_rec(recs, "sector_concentration") is None


def test_unclassified_sector_excluded_from_top_pick_and_flagged_separately():
    # A huge "Other" bucket must NOT be picked as the top sector (incoherent
    # "trim Other" advice) -- it gets its own LOW data-hygiene note instead.
    rows = [
        {"ticker": "UNCLASSIFIED", "weight": 50.0, "market_value": 50_000.0, "sector": "Other"},
        {"ticker": "TECHNAME", "weight": 10.0, "market_value": 10_000.0, "sector": "Tech"},
    ]
    recs = _recs(rows)
    assert find_rec(recs, "sector_concentration") is None  # Tech alone is nowhere near the caps
    other_rec = find_rec(recs, "unclassified_holdings")
    assert other_rec is not None
    assert other_rec["priority"] == "LOW"


# ── single-name concentration ─────────────────────────────────────────────────

def test_single_name_overweight_strong_conviction_flagged():
    rows = [{
        "ticker": "AAA", "weight": SINGLE_NAME_CEILING + 5, "market_value": 20_000.0,
        "score": WEAK_CONVICTION_SCORE + 10,
    }]
    recs = _recs(rows)
    rec = find_rec(recs, "single_name_concentration")
    assert rec is not None
    assert rec["priority"] == "MEDIUM"


def test_single_name_overweight_weak_conviction_not_flagged_here():
    # Overweight + WEAK is the daily_briefing "weak-large" flag's job, not this
    # one's -- this rec is deliberately conviction-INDEPENDENT-above-the-gate,
    # gated to score >= WEAK_CONVICTION_SCORE so the two surfaces never double-fire.
    rows = [{
        "ticker": "AAA", "weight": SINGLE_NAME_CEILING + 5, "market_value": 20_000.0,
        "score": WEAK_CONVICTION_SCORE - 10,
    }]
    recs = _recs(rows)
    assert find_rec(recs, "single_name_concentration") is None
