"""Regression tests for stock_analyzer/portfolio.py — stop-ladder, trim
allocation, and diversification scoring.

stop_ladder()/protective_stop()/manual_stop_wins() back the "How your stop is
set" explainer AND the live engine's actual stop math, so a drift here is a
drift in what the Brief acts on. project_stop_ladder_and_display (memory)
already flags one real trap: the raw engine `stop` vs. the ratcheted
`_sa_holding['Stop']` — these tests pin the ratchet-vs-ATR "which number wins"
logic and the manual-override boundary directly so that class of bug can't
silently return.
"""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer.constants import CORR_DANGER_PAIRS_THRESHOLD, CORR_HIGH_PAIRS_THRESHOLD
from stock_analyzer.portfolio import (
    diversification_score,
    manual_stop_wins,
    protective_stop,
    real_sector_exposure,
    sector_benchmark_tilt,
    stop_ladder,
    trim_allocation,
)


# ── protective_stop ───────────────────────────────────────────────────────────

def test_protective_stop_no_avg_cost_falls_back_to_atr():
    stop, label = protective_stop(current_price=100.0, avg_cost=0.0, atr_stop=92.0)
    assert (stop, label) == (92.0, "ATR Stop")


def test_protective_stop_below_first_ratchet_rung_uses_atr():
    # +5% gain is below the +10% breakeven-guard rung.
    stop, label = protective_stop(current_price=105.0, avg_cost=100.0, atr_stop=95.0)
    assert (stop, label) == (95.0, "ATR Stop")


def test_protective_stop_ratchets_at_breakeven_guard():
    # +10% gain -> floor = avg_cost * 1.02 = 102.0, which beats a lower ATR stop.
    stop, label = protective_stop(current_price=110.0, avg_cost=100.0, atr_stop=95.0)
    assert (stop, label) == (102.0, "Breakeven guard")


def test_protective_stop_ratchets_at_protect_40pct_gain():
    # +75% gain -> floor = avg_cost * 1.40 = 140.0, which beats a lower ATR stop.
    stop, label = protective_stop(current_price=175.0, avg_cost=100.0, atr_stop=130.0)
    assert (stop, label) == (140.0, "Protect 40% gain")


def test_protective_stop_never_returns_below_atr_floor():
    # ATR stop can still beat a modest ratchet floor (the "number that binds"
    # nuance) -- protective_stop takes the max of the two.
    stop, label = protective_stop(current_price=112.0, avg_cost=100.0, atr_stop=105.0)
    assert stop == 105.0
    assert label == "Breakeven guard"  # tier label still reflects the gain tier reached


# ── manual_stop_wins ──────────────────────────────────────────────────────────

def test_manual_stop_wins_when_at_least_as_tight():
    assert manual_stop_wins(manual_price=100.0, protective_stop_price=100.0) is True
    assert manual_stop_wins(manual_price=101.0, protective_stop_price=100.0) is True


def test_manual_stop_loses_when_looser_than_protective():
    assert manual_stop_wins(manual_price=99.99, protective_stop_price=100.0) is False


def test_manual_stop_loses_when_non_positive_or_missing():
    assert manual_stop_wins(manual_price=0.0, protective_stop_price=100.0) is False
    assert manual_stop_wins(manual_price=None, protective_stop_price=100.0) is False  # type: ignore[arg-type]  -- exercises the function's own falsy guard


# ── stop_ladder ───────────────────────────────────────────────────────────────

def test_stop_ladder_returns_none_on_missing_inputs():
    assert stop_ladder(price=0.0, avg_cost=100.0, atr_val=5.0) is None
    assert stop_ladder(price=100.0, avg_cost=100.0, atr_val=0.0) is None
    assert stop_ladder(price=100.0, avg_cost=0.0, atr_val=5.0) is None


def test_stop_ladder_auto_source_atr_when_ratchet_floor_lower():
    # +5% gain: no ratchet rung reached yet -> auto_source must be "atr".
    result = stop_ladder(price=105.0, avg_cost=100.0, atr_val=10.0, atr_multiplier=2.0)
    assert result is not None
    assert result["ratchet_floor"] is None
    assert result["auto_source"] == "atr"
    assert result["auto_stop"] == result["atr_stop"]


def test_stop_ladder_auto_source_ratchet_when_it_exceeds_atr():
    # +30% gain -> ratchet floor (avg_cost*1.10=110) exceeds a wide ATR stop.
    result = stop_ladder(price=130.0, avg_cost=100.0, atr_val=40.0, atr_multiplier=2.0)
    assert result is not None
    assert result["ratchet_floor"] == 110.0
    assert result["auto_source"] == "ratchet"
    assert result["auto_stop"] == 110.0


def test_stop_ladder_manual_override_applies_only_when_at_least_as_tight():
    base = dict(price=130.0, avg_cost=100.0, atr_val=40.0, atr_multiplier=2.0)
    tight = stop_ladder(**base, manual_stop=115.0)  # auto_stop is 110.0 here
    assert tight is not None
    assert tight["active_source"] == "manual"
    assert tight["manual_applied"] is True
    assert tight["active_stop"] == 115.0

    loose = stop_ladder(**base, manual_stop=105.0)  # looser than the 110.0 auto stop
    assert loose is not None
    assert loose["active_source"] == "ratchet"
    assert loose["manual_applied"] is False
    assert loose["active_stop"] == 110.0


def test_stop_ladder_stopped_out_flag():
    # Normal case: ATR stop is always below the current price by construction
    # (price - mult*atr), so a flat/no-gain position is never "stopped out".
    normal = stop_ladder(price=100.0, avg_cost=100.0, atr_val=5.0, atr_multiplier=2.0)
    assert normal is not None
    assert normal["stopped_out"] is False

    # A price gap-down through a previously-set manual stop above the auto
    # stop (e.g. price fell hard between sessions) must flag stopped_out.
    gapped = stop_ladder(price=100.0, avg_cost=100.0, atr_val=5.0, atr_multiplier=2.0, manual_stop=105.0)
    assert gapped is not None
    assert gapped["active_stop"] == 105.0
    assert gapped["stopped_out"] is True


def test_stop_ladder_gap_pct_matches_price_and_active_stop():
    result = stop_ladder(price=200.0, avg_cost=100.0, atr_val=10.0, atr_multiplier=2.0)
    assert result is not None
    expected_gap = round((200.0 - result["active_stop"]) / 200.0 * 100.0, 1)
    assert result["gap_pct"] == expected_gap


def test_stop_ladder_atr_stop_override_is_used_verbatim():
    result = stop_ladder(price=105.0, avg_cost=100.0, atr_val=10.0, atr_stop_override=93.37)
    assert result is not None
    assert result["atr_stop"] == 93.37


def test_stop_ladder_ratchet_rungs_mark_exactly_one_current_when_reached():
    result = stop_ladder(price=130.0, avg_cost=100.0, atr_val=10.0)  # +30% gain
    assert result is not None
    current_rungs = [r for r in result["ratchet_rungs"] if r["is_current"]]
    assert len(current_rungs) == 1
    assert current_rungs[0]["gain_pct"] == 25  # the +25% rung is the highest reached at +30%


def test_stop_ladder_next_tier_is_none_past_top_rung():
    result = stop_ladder(price=200.0, avg_cost=100.0, atr_val=10.0)  # +100% gain, past all rungs
    assert result is not None
    assert result["next_tier"] is None


# ── trim_allocation ───────────────────────────────────────────────────────────

def test_trim_allocation_non_positive_target_returns_empty():
    result = trim_allocation([{"ticker": "A", "market_value": 1000}], target_dollar=0.0, denom=1000.0)
    assert result == {"rows": [], "total_allocated": 0.0, "target": 0.0, "shortfall": 0.0}


def test_trim_allocation_fully_exits_weakest_names_first():
    ordered = [
        {"ticker": "WEAK", "market_value": 1000.0, "price": 50.0, "pnl_pct": -10.0},
        {"ticker": "MID", "market_value": 2000.0, "price": 100.0, "pnl_pct": 5.0},
    ]
    result = trim_allocation(ordered, target_dollar=1000.0, denom=3000.0)
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["ticker"] == "WEAK"
    assert row["full"] is True
    assert row["cut_dollar"] == 1000
    assert row["tax_dir"] == "loss"
    assert result["shortfall"] == 0.0


def test_trim_allocation_partial_trims_the_last_name_to_hit_target():
    ordered = [
        {"ticker": "WEAK", "market_value": 1000.0, "price": 50.0, "pnl_pct": -10.0},
        {"ticker": "MID", "market_value": 2000.0, "price": 100.0, "pnl_pct": 5.0},
    ]
    result = trim_allocation(ordered, target_dollar=1500.0, denom=3000.0)
    assert len(result["rows"]) == 2
    assert result["rows"][0]["full"] is True
    assert result["rows"][1]["full"] is False
    assert result["rows"][1]["cut_dollar"] == 500
    assert result["total_allocated"] == 1500
    assert result["shortfall"] == 0.0


def test_trim_allocation_reports_shortfall_when_names_run_out():
    ordered = [{"ticker": "WEAK", "market_value": 1000.0, "price": 50.0, "pnl_pct": -10.0}]
    result = trim_allocation(ordered, target_dollar=1500.0, denom=1000.0)
    assert result["total_allocated"] == 1000
    assert result["shortfall"] == 500


def test_trim_allocation_shares_none_when_price_missing():
    ordered = [{"ticker": "WEAK", "market_value": 1000.0, "price": 0.0, "pnl_pct": 0.0}]
    result = trim_allocation(ordered, target_dollar=500.0, denom=1000.0)
    assert result["rows"][0]["shares"] is None
    assert result["rows"][0]["tax_dir"] == "flat"


# ── diversification_score ─────────────────────────────────────────────────────

def _corr_df(matrix, tickers):
    return pd.DataFrame(matrix, index=tickers, columns=tickers)


def test_diversification_score_empty_or_single_name_withholds():
    assert diversification_score(pd.DataFrame())["score"] is None
    assert diversification_score(_corr_df([[1.0]], ["A"]))["score"] is None


def test_diversification_score_uncorrelated_book_scores_midpoint():
    # score = (1 - avg_corr) / 2 * 100 -> the scale runs anti-correlated(-1)=100
    # .. uncorrelated(0)=50 .. lockstep(+1)=0, so zero correlation is the
    # MIDPOINT, not the ceiling -- pinned here since that's easy to misread.
    tickers = ["A", "B"]
    matrix = [[1.0, 0.0], [0.0, 1.0]]
    result = diversification_score(_corr_df(matrix, tickers))
    assert result["score"] == 50.0
    assert result["avg_correlation"] == 0.0
    assert result["risk_pairs"] == []


def test_diversification_score_anti_correlated_book_scores_100():
    tickers = ["A", "B"]
    matrix = [[1.0, -1.0], [-1.0, 1.0]]
    result = diversification_score(_corr_df(matrix, tickers))
    assert result["score"] == 100.0
    assert result["avg_correlation"] == -1.0


def test_diversification_score_lockstep_book_scores_0():
    tickers = ["A", "B"]
    matrix = [[1.0, 1.0], [1.0, 1.0]]
    result = diversification_score(_corr_df(matrix, tickers))
    assert result["score"] == 0.0
    assert result["avg_correlation"] == 1.0


def test_diversification_score_flags_danger_and_warning_pairs_at_thresholds():
    tickers = ["A", "B", "C"]
    danger_corr = CORR_DANGER_PAIRS_THRESHOLD
    warn_corr = CORR_HIGH_PAIRS_THRESHOLD
    matrix = [
        [1.0, danger_corr, warn_corr],
        [danger_corr, 1.0, 0.0],
        [warn_corr, 0.0, 1.0],
    ]
    result = diversification_score(_corr_df(matrix, tickers))
    levels = {(p["t1"], p["t2"]): p["level"] for p in result["risk_pairs"]}
    assert levels[("A", "B")] == "danger"
    assert levels[("A", "C")] == "warning"


def test_diversification_score_ignores_nan_pairs():
    tickers = ["A", "B"]
    matrix = [[1.0, np.nan], [np.nan, 1.0]]
    result = diversification_score(_corr_df(matrix, tickers))
    # No valid pair to average -> weight_sum is 0 -> avg_corr falls back to 0.0.
    assert result["avg_correlation"] == 0.0
    assert result["risk_pairs"] == []


# ── real_sector_exposure / sector_benchmark_tilt ──────────────────────────────

def test_real_sector_exposure_normalizes_provider_aliases():
    port_df = pd.DataFrame([
        {"Ticker": "V", "Market Value": 1000.0},
        {"Ticker": "LLY", "Market Value": 500.0},
    ])
    held_data = {
        "V":   {"sector": "Financial Services"},  # provider alias -> "Financials"
        "LLY": {"sector": "Healthcare"},           # provider alias -> "Health Care"
    }
    result = real_sector_exposure(port_df, held_data)
    sectors = dict(zip(result["Sector"], result["Pct"]))
    assert sectors == {"Financials": pytest.approx(66.7), "Health Care": pytest.approx(33.3)}


def test_real_sector_exposure_unmapped_sector_falls_back_to_other():
    port_df = pd.DataFrame([{"Ticker": "WEIRD", "Market Value": 100.0}])
    held_data = {"WEIRD": {"sector": "Some Nonsense Category"}}
    result = real_sector_exposure(port_df, held_data)
    assert result["Sector"].tolist() == ["Other"]


def test_real_sector_exposure_missing_sector_field_falls_back_to_other():
    port_df = pd.DataFrame([{"Ticker": "NOPE", "Market Value": 100.0}])
    result = real_sector_exposure(port_df, {})
    assert result["Sector"].tolist() == ["Other"]


def test_real_sector_exposure_empty_portfolio_returns_empty_df():
    assert real_sector_exposure(pd.DataFrame(), {}).empty


def test_sector_benchmark_tilt_unheld_benchmark_sector_shows_negative_tilt():
    real_sector_df = pd.DataFrame([{"Sector": "Financials", "Pct": 100.0}])
    tilt_df = sector_benchmark_tilt(real_sector_df)
    row = tilt_df[tilt_df["Sector"] == "Information Technology"].iloc[0]
    assert row["Portfolio Pct"] == 0.0
    assert row["Tilt"] < 0


def test_sector_benchmark_tilt_matches_portfolio_minus_benchmark():
    real_sector_df = pd.DataFrame([{"Sector": "Financials", "Pct": 20.0}])
    tilt_df = sector_benchmark_tilt(real_sector_df)
    row = tilt_df[tilt_df["Sector"] == "Financials"].iloc[0]
    assert row["Tilt"] == pytest.approx(row["Portfolio Pct"] - row["Benchmark Pct"], abs=0.05)
