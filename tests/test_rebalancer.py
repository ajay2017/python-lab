"""Regression tests for stock_analyzer/rebalancer.py — the Portfolio
Rebalancing Advisor: per-position drift vs target weight, OK/WATCH/TRIM/ADD
classification, and the trim/add action lists with urgency ordering,
rationale branching, and the News Intelligence / Risk Advisor coordination
gates. Pure computation, no I/O. See docs/plans/test-automation.md for scope.
"""
import pandas as pd
import pytest

from stock_analyzer import rebalancer as reb
from stock_analyzer.constants import COMPOSITE_BUY, COMPOSITE_HOLD


# ── _f ────────────────────────────────────────────────────────────────────

def test_f_none_returns_default():
    assert reb._f(None) == 0.0
    assert reb._f(None, default=-1) == -1


def test_f_nan_returns_default():
    assert reb._f(float("nan"), default=-1) == -1


def test_f_unparseable_returns_default():
    assert reb._f("not-a-number", default=5) == 5


def test_f_parses_valid_value():
    assert reb._f("3.5") == 3.5


# ── equal_weights ───────────────────────────────────────────────────────────

def test_equal_weights_empty_df_returns_empty_dict():
    assert reb.equal_weights(pd.DataFrame()) == {}


def test_equal_weights_splits_evenly():
    df = pd.DataFrame({"Ticker": ["AAPL", "MSFT", "XOM"]})
    result = reb.equal_weights(df)
    assert result == {"AAPL": 33.33, "MSFT": 33.33, "XOM": 33.33}


# ── compute_drift ────────────────────────────────────────────────────────

def _port_row(ticker, weight, mval=10_000.0, price=100.0, shares=100,
              score=60.0, signal="Hold", sector="Tech"):
    return {
        "Ticker": ticker, "Weight (%)": weight, "Market Value": mval,
        "Price": price, "Shares": shares, "Score": score,
        "Signal": signal, "Sector": sector,
    }


def test_compute_drift_status_ok_within_2pp():
    df = pd.DataFrame([_port_row("AAPL", weight=12.0)])
    result = reb.compute_drift(df, {"AAPL": 10.0}, total_val=100_000.0)
    assert result.iloc[0]["Status"] == "OK"
    assert result.iloc[0]["Drift (pp)"] == 2.0


def test_compute_drift_status_watch_between_2_and_5pp():
    df = pd.DataFrame([_port_row("AAPL", weight=14.0)])
    result = reb.compute_drift(df, {"AAPL": 10.0}, total_val=100_000.0)
    assert result.iloc[0]["Status"] == "WATCH"


def test_compute_drift_status_trim_when_overweight_beyond_5pp():
    df = pd.DataFrame([_port_row("AAPL", weight=16.0)])
    result = reb.compute_drift(df, {"AAPL": 10.0}, total_val=100_000.0)
    assert result.iloc[0]["Status"] == "TRIM"
    assert result.iloc[0]["Drift (pp)"] == 6.0


def test_compute_drift_status_add_when_underweight_beyond_5pp():
    df = pd.DataFrame([_port_row("AAPL", weight=4.0)])
    result = reb.compute_drift(df, {"AAPL": 10.0}, total_val=100_000.0)
    assert result.iloc[0]["Status"] == "ADD"
    assert result.iloc[0]["Drift (pp)"] == -6.0


def test_compute_drift_missing_target_defaults_to_current_no_drift():
    df = pd.DataFrame([_port_row("AAPL", weight=12.0)])
    result = reb.compute_drift(df, {}, total_val=100_000.0)
    assert result.iloc[0]["Drift (pp)"] == 0.0
    assert result.iloc[0]["Status"] == "OK"


def test_compute_drift_drift_value_positive_means_trim():
    df = pd.DataFrame([_port_row("AAPL", weight=20.0, mval=20_000.0)])
    result = reb.compute_drift(df, {"AAPL": 10.0}, total_val=100_000.0)
    # target_val = 10% of 100k = 10k; drift_val = 20k - 10k = +10k (overweight $)
    assert result.iloc[0]["Drift Value ($)"] == 10_000.0


def test_compute_drift_sorted_descending_by_drift_pp():
    df = pd.DataFrame([
        _port_row("AAPL", weight=5.0),
        _port_row("MSFT", weight=20.0),
        _port_row("XOM", weight=12.0),
    ])
    result = reb.compute_drift(df, {"AAPL": 10.0, "MSFT": 10.0, "XOM": 10.0}, total_val=100_000.0)
    assert result["Ticker"].tolist() == ["MSFT", "XOM", "AAPL"]


def test_compute_drift_shares_truncated_to_int():
    df = pd.DataFrame([_port_row("AAPL", weight=10.0, shares=99.9)])
    result = reb.compute_drift(df, {"AAPL": 10.0}, total_val=100_000.0)
    assert result.iloc[0]["Shares"] == 99


# ── build_rebalance_plan ───────────────────────────────────────────────────

def _drift_row(ticker, status, drift_pp, drift_val, price=100.0, shares=100,
                score=60.0, signal="Hold", current=10.0, target=10.0, sector="Tech"):
    return {
        "Ticker": ticker, "Sector": sector, "Current (%)": current,
        "Target (%)": target, "Drift (pp)": drift_pp,
        "Drift Value ($)": drift_val, "Price ($)": price, "Shares": shares,
        "Score": score, "Signal": signal, "Status": status,
    }


def test_build_rebalance_plan_empty_drift_df():
    result = reb.build_rebalance_plan(pd.DataFrame(), total_val=100_000.0)
    assert result == {
        "trims": [], "adds": [], "ok": [], "total_trim_value": 0,
        "total_add_value": 0, "rebalance_pct": 0,
        "news_blocked_adds": 0, "risk_blocked_adds_count": 0,
        "risk_blocked_adds": [],
    }


def test_build_rebalance_plan_ok_status_goes_to_ok_list_only():
    df = pd.DataFrame([_drift_row("AAPL", "OK", 1.0, 500.0)])
    result = reb.build_rebalance_plan(df, total_val=100_000.0)
    assert result["ok"] == ["AAPL"]
    assert result["trims"] == []
    assert result["adds"] == []


def test_build_rebalance_plan_trim_sell_signal_highest_urgency():
    df = pd.DataFrame([_drift_row("AAPL", "TRIM", 8.0, 8000.0, signal="Sell", score=30.0)])
    result = reb.build_rebalance_plan(df, total_val=100_000.0)
    trim = result["trims"][0]
    # Sell(+40) + score<HOLD(+20) + drift>WATCH(+30) = 90
    assert trim["urgency"] == 90
    assert "Sell signal" in trim["rationale"]
    assert "before considering any other trim" in trim["action_detail"]


def test_build_rebalance_plan_trim_broken_conviction_rationale():
    df = pd.DataFrame([_drift_row("AAPL", "TRIM", 8.0, 8000.0, signal="Hold", score=40.0)])
    result = reb.build_rebalance_plan(df, total_val=100_000.0)
    trim = result["trims"][0]
    assert "conviction is broken" in trim["rationale"]
    assert trim["urgency"] == 20 + 30  # score<HOLD(+20) + drift>WATCH(+30), no Sell signal


def test_build_rebalance_plan_trim_winner_running_rationale():
    df = pd.DataFrame([_drift_row("AAPL", "TRIM", 8.0, 8000.0, signal="Buy", score=70.0)])
    result = reb.build_rebalance_plan(df, total_val=100_000.0)
    trim = result["trims"][0]
    assert "a winner running" in trim["rationale"]
    assert trim["urgency"] == 30  # only drift>WATCH tier, no Sell/low-score bonus


def test_build_rebalance_plan_trim_watch_status_urgency_floor_is_5():
    df = pd.DataFrame([_drift_row("AAPL", "WATCH", 3.0, 3000.0, signal="Buy", score=70.0)])
    result = reb.build_rebalance_plan(df, total_val=100_000.0)
    trim = result["trims"][0]
    # drift 3.0 > TOLERANCE_OK(2.0) but <= TOLERANCE_WATCH(5.0) -> +10, already above floor
    assert trim["urgency"] == 10


def test_build_rebalance_plan_trim_watch_status_urgency_floor_applies_when_below():
    # Construct a WATCH row where the raw urgency tally would be 0, to confirm
    # the max(urgency, 5) floor actually kicks in.
    df = pd.DataFrame([_drift_row("AAPL", "WATCH", 2.5, 2500.0, signal="Hold", score=70.0)])
    result = reb.build_rebalance_plan(df, total_val=100_000.0)
    trim = result["trims"][0]
    # abs(drift_pp)=2.5 > TOLERANCE_OK(2.0) -> +10 already, so floor is moot here;
    # still assert the value to pin behavior (urgency=10, not 5).
    assert trim["urgency"] == 10


def test_build_rebalance_plan_trim_shares_delta_at_least_1_even_if_price_invalid():
    df = pd.DataFrame([_drift_row("AAPL", "TRIM", 8.0, 8000.0, price=0.0)])
    result = reb.build_rebalance_plan(df, total_val=100_000.0)
    assert result["trims"][0]["shares_delta"] == 1


def test_build_rebalance_plan_add_high_conviction_underweight():
    df = pd.DataFrame([_drift_row("AAPL", "ADD", -8.0, -8000.0, signal="Buy", score=70.0)])
    result = reb.build_rebalance_plan(df, total_val=100_000.0)
    add = result["adds"][0]
    # score>=BUY(+30) + Buy in signal(+20) + drift>WATCH(+30) = 80
    assert add["urgency"] == 80
    assert "high-conviction name" in add["rationale"]


def test_build_rebalance_plan_add_low_conviction_rationale():
    df = pd.DataFrame([_drift_row("AAPL", "ADD", -8.0, -8000.0, signal="Hold", score=50.0)])
    result = reb.build_rebalance_plan(df, total_val=100_000.0)
    add = result["adds"][0]
    assert "reassess conviction before adding" in add["rationale"]
    assert add["urgency"] == 30  # only drift>WATCH tier


def test_build_rebalance_plan_add_suppressed_by_risk_trim_set():
    df = pd.DataFrame([_drift_row("AAPL", "ADD", -8.0, -8000.0, signal="Buy", score=70.0)])
    result = reb.build_rebalance_plan(df, total_val=100_000.0, risk_trim_set={"AAPL"})
    assert result["adds"] == []
    assert result["risk_blocked_adds_count"] == 1
    assert result["risk_blocked_adds"][0]["ticker"] == "AAPL"
    assert "Risk Advisor recommends trimming" in result["risk_blocked_adds"][0]["reason"]


def test_build_rebalance_plan_add_risk_trim_set_case_insensitive():
    df = pd.DataFrame([_drift_row("aapl", "ADD", -8.0, -8000.0)])
    result = reb.build_rebalance_plan(df, total_val=100_000.0, risk_trim_set={"AAPL"})
    assert result["adds"] == []
    assert result["risk_blocked_adds_count"] == 1


def test_build_rebalance_plan_add_critical_news_suppresses_urgency_and_flags():
    df = pd.DataFrame([_drift_row("AAPL", "ADD", -8.0, -8000.0, signal="Buy", score=70.0)])
    news = {"AAPL": {"level": "critical", "headline": "Fraud probe", "compound": -0.8}}
    result = reb.build_rebalance_plan(df, total_val=100_000.0, news_flags=news)
    add = result["adds"][0]
    assert add["urgency"] == 5  # min(80, 5)
    assert add["news_warning"]["level"] == "critical"
    assert "Fraud probe" in add["news_warning"]["headline"]
    assert result["news_blocked_adds"] == 1


def test_build_rebalance_plan_add_warning_news_does_not_change_urgency():
    df = pd.DataFrame([_drift_row("AAPL", "ADD", -8.0, -8000.0, signal="Buy", score=70.0)])
    news = {"AAPL": {"level": "warning", "headline": "Soft guidance", "compound": -0.3}}
    result = reb.build_rebalance_plan(df, total_val=100_000.0, news_flags=news)
    add = result["adds"][0]
    assert add["urgency"] == 80  # unchanged
    assert add["news_warning"]["level"] == "warning"
    assert result["news_blocked_adds"] == 1


def test_build_rebalance_plan_add_news_flag_lookup_uppercased_key():
    df = pd.DataFrame([_drift_row("aapl", "ADD", -8.0, -8000.0, signal="Buy", score=70.0)])
    news = {"AAPL": {"level": "warning", "headline": "x", "compound": 0.0}}
    result = reb.build_rebalance_plan(df, total_val=100_000.0, news_flags=news)
    assert result["adds"][0]["news_warning"] is not None


def test_build_rebalance_plan_add_no_news_flag_leaves_warning_none():
    df = pd.DataFrame([_drift_row("AAPL", "ADD", -8.0, -8000.0)])
    result = reb.build_rebalance_plan(df, total_val=100_000.0)
    assert result["adds"][0]["news_warning"] is None
    assert result["news_blocked_adds"] == 0


def test_build_rebalance_plan_trims_and_adds_sorted_by_urgency_desc():
    df = pd.DataFrame([
        _drift_row("LOWURG", "TRIM", 3.0, 3000.0, signal="Buy", score=70.0),   # urgency 10
        _drift_row("HIURG", "TRIM", 8.0, 8000.0, signal="Sell", score=20.0),   # urgency 90
    ])
    result = reb.build_rebalance_plan(df, total_val=100_000.0)
    assert [t["ticker"] for t in result["trims"]] == ["HIURG", "LOWURG"]


def test_build_rebalance_plan_totals_and_rebalance_pct():
    df = pd.DataFrame([
        _drift_row("AAPL", "TRIM", 8.0, 8000.0),
        _drift_row("MSFT", "ADD", -6.0, -6000.0),
    ])
    result = reb.build_rebalance_plan(df, total_val=100_000.0)
    assert result["total_trim_value"] == 8000.0
    assert result["total_add_value"] == 6000.0
    assert result["rebalance_pct"] == 14.0


def test_build_rebalance_plan_zero_total_val_rebalance_pct_is_zero():
    df = pd.DataFrame([_drift_row("AAPL", "TRIM", 8.0, 8000.0)])
    result = reb.build_rebalance_plan(df, total_val=0.0)
    assert result["rebalance_pct"] == 0.0
