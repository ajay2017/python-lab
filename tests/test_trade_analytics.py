"""Tests for stock_analyzer/trade_analytics.py — Trade Journal behavioral
analytics (trigger-type breakdown, monthly P&L trend, hold-time stats, and
the coaching-card behavioral insights engine). Previously zero test coverage
despite being real decision logic feeding a user-facing page. Pure logic,
no I/O — except `_build_overtrading_stats` and `build_full_analytics`, which
call `datetime.now(_ET)` internally (NY-local, 2026-08-04 audit fix — was a
naive `datetime.utcnow()`; tested by constructing trade dates relative to
`datetime.now(_ET)` at run time, not hardcoded dates).
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
import pytz

from stock_analyzer import trade_analytics as ta

_ET = pytz.timezone("America/New_York")


# ─── builders ───────────────────────────────────────────────────────────────

def _trade(action="SELL", ticker="AAA", traded_at="2026-01-15", realized_pnl=None,
           cost_basis=None, shares=None, price=None, trigger_type="MANUAL"):
    return {
        "action": action, "ticker": ticker, "traded_at": traded_at,
        "realized_pnl": realized_pnl, "cost_basis": cost_basis,
        "shares": shares, "price": price, "trigger_type": trigger_type,
    }


def _trades_df(rows):
    return pd.DataFrame(rows)


def _sell(ticker="AAA", traded_at="2026-01-15", realized_pnl=100.0, cost_basis=50.0,
          shares=10, price=60.0, trigger_type="MANUAL"):
    return _trade("SELL", ticker, traded_at, realized_pnl, cost_basis, shares, price, trigger_type)


def _buy(ticker="AAA", traded_at="2026-01-01", cost_basis=50.0, shares=10, price=50.0):
    return _trade("BUY", ticker, traded_at, None, cost_basis, shares, price, None)


# ─── _safe_float ────────────────────────────────────────────────────────────

def test_safe_float_none_returns_default():
    assert ta._safe_float(None) == 0.0
    assert ta._safe_float(None, default=5.0) == 5.0


def test_safe_float_nan_returns_default():
    assert ta._safe_float(float("nan"), default=-1.0) == -1.0


def test_safe_float_unparseable_returns_default():
    assert ta._safe_float("not-a-number", default=2.0) == 2.0


def test_safe_float_parses_valid_value():
    assert ta._safe_float("3.5") == 3.5


# ─── _parse_dt ──────────────────────────────────────────────────────────────

def test_parse_dt_na_returns_none():
    assert ta._parse_dt(None) is None
    assert ta._parse_dt(float("nan")) is None


def test_parse_dt_parses_valid_string():
    result = ta._parse_dt("2026-01-15")
    assert result == pd.Timestamp("2026-01-15")


def test_parse_dt_unparseable_returns_none():
    assert ta._parse_dt("not-a-date") is None


# ─── _pnl_pct ───────────────────────────────────────────────────────────────

def test_pnl_pct_zero_invested_returns_none():
    assert ta._pnl_pct(100.0, 0, 10) is None
    assert ta._pnl_pct(100.0, 50.0, 0) is None


def test_pnl_pct_zero_pnl_with_valid_invested_returns_zero_not_none():
    # 2026-08-04 audit fix: a genuine breakeven trade (pnl == 0, invested > 0)
    # must be distinguishable from "no data" (invested <= 0 -> None).
    assert ta._pnl_pct(0.0, 50.0, 10) == 0.0
    assert ta._pnl_pct(0.0, 50.0, 10) is not None


def test_pnl_pct_positive_calculation():
    # invested = 500, pnl = 100 -> 20.0%
    assert ta._pnl_pct(100.0, 50.0, 10) == 20.0


def test_pnl_pct_negative_calculation():
    assert ta._pnl_pct(-50.0, 50.0, 10) == -10.0


# ─── compute_extended_stats ─────────────────────────────────────────────────

def test_compute_extended_stats_none_df_returns_empty():
    assert ta.compute_extended_stats(None).empty


def test_compute_extended_stats_empty_df_returns_empty():
    assert ta.compute_extended_stats(pd.DataFrame()).empty


def test_compute_extended_stats_no_sell_rows_returns_empty():
    df = _trades_df([_buy()])
    assert ta.compute_extended_stats(df).empty


def test_compute_extended_stats_single_row_happy_path():
    df = _trades_df([_buy(traded_at="2026-01-01"), _sell(traded_at="2026-01-15")])
    ext = ta.compute_extended_stats(df)
    assert len(ext) == 1
    row = ext.iloc[0]
    assert row["pnl_pct"] == 20.0  # 100/500*100
    assert row["month_str"] == "2026-01"
    assert row["hold_days"] == 14


def test_compute_extended_stats_hold_days_matches_nearest_preceding_buy():
    # Two BUYs for the same ticker; hold_days should use the nearer, not the
    # earliest.
    df = _trades_df([
        _buy(traded_at="2026-01-01"),
        _buy(traded_at="2026-01-10"),
        _sell(traded_at="2026-01-15"),
    ])
    ext = ta.compute_extended_stats(df)
    assert ext.iloc[0]["hold_days"] == 5  # nearest buy (01-10), not the earliest (01-01)


def test_compute_extended_stats_no_matching_buy_gives_none_hold_days():
    df = _trades_df([_buy(ticker="BBB"), _sell(ticker="AAA")])
    ext = ta.compute_extended_stats(df)
    assert ext.iloc[0]["hold_days"] is None


# ─── build_trigger_breakdown ────────────────────────────────────────────────

def test_build_trigger_breakdown_empty_input_returns_empty():
    assert ta.build_trigger_breakdown(pd.DataFrame()).empty


def test_build_trigger_breakdown_missing_trigger_type_column_returns_empty():
    ext = pd.DataFrame([{"realized_pnl": 10.0}])
    assert ta.build_trigger_breakdown(ext).empty


def test_build_trigger_breakdown_all_winner_group_avg_loss_is_none():
    df = _trades_df([
        _buy(traded_at="2026-01-01"),
        _sell(traded_at="2026-01-10", realized_pnl=50.0),
    ])
    ext = ta.compute_extended_stats(df)
    trig = ta.build_trigger_breakdown(ext)
    assert trig.iloc[0]["Avg Loss ($)"] is None
    assert trig.iloc[0]["Avg Win ($)"] == 50.0


def test_build_trigger_breakdown_all_loser_group_avg_win_is_none():
    df = _trades_df([
        _buy(traded_at="2026-01-01"),
        _sell(traded_at="2026-01-10", realized_pnl=-30.0),
    ])
    ext = ta.compute_extended_stats(df)
    trig = ta.build_trigger_breakdown(ext)
    assert trig.iloc[0]["Avg Win ($)"] is None
    assert trig.iloc[0]["Avg Loss ($)"] == -30.0


def test_build_trigger_breakdown_profit_factor_none_when_no_losers():
    df = _trades_df([
        _buy(traded_at="2026-01-01"),
        _sell(traded_at="2026-01-10", realized_pnl=50.0),
    ])
    ext = ta.compute_extended_stats(df)
    trig = ta.build_trigger_breakdown(ext)
    assert trig.iloc[0]["Profit Factor"] is None


def test_build_trigger_breakdown_profit_factor_calculated_when_both_present():
    df = _trades_df([
        _buy(ticker="AAA", traded_at="2026-01-01"),
        _sell(ticker="AAA", traded_at="2026-01-05", realized_pnl=100.0),
        _buy(ticker="BBB", traded_at="2026-01-01"),
        _sell(ticker="BBB", traded_at="2026-01-05", realized_pnl=-50.0),
    ])
    ext = ta.compute_extended_stats(df)
    trig = ta.build_trigger_breakdown(ext)
    assert trig.iloc[0]["Profit Factor"] == 2.0  # 100 / 50


# ─── build_monthly_trend ────────────────────────────────────────────────────

def test_build_monthly_trend_empty_input_returns_empty():
    assert ta.build_monthly_trend(pd.DataFrame()).empty


def test_build_monthly_trend_groupby_correctness():
    df = _trades_df([
        _buy(ticker="AAA", traded_at="2026-01-01"),
        _sell(ticker="AAA", traded_at="2026-01-10", realized_pnl=100.0),
        _buy(ticker="BBB", traded_at="2026-02-01"),
        _sell(ticker="BBB", traded_at="2026-02-10", realized_pnl=-40.0),
        _buy(ticker="CCC", traded_at="2026-02-01"),
        _sell(ticker="CCC", traded_at="2026-02-15", realized_pnl=60.0),
    ])
    ext = ta.compute_extended_stats(df)
    monthly = ta.build_monthly_trend(ext)
    jan = monthly[monthly["month_str"] == "2026-01"].iloc[0]
    feb = monthly[monthly["month_str"] == "2026-02"].iloc[0]
    assert jan["pnl"] == 100.0
    assert jan["trade_count"] == 1
    assert jan["win_rate"] == 100.0
    assert feb["pnl"] == 20.0
    assert feb["trade_count"] == 2
    assert feb["win_rate"] == 50.0


# ─── build_hold_time_stats ───────────────────────────────────────────────────

def test_build_hold_time_stats_empty_input_returns_empty_dict():
    assert ta.build_hold_time_stats(pd.DataFrame()) == {}


def test_build_hold_time_stats_all_null_hold_days_returns_empty_dict():
    ext = pd.DataFrame([{"hold_days": None, "realized_pnl": 10.0}])
    assert ta.build_hold_time_stats(ext) == {}


def test_build_hold_time_stats_happy_path():
    df = _trades_df([
        _buy(ticker="AAA", traded_at="2026-01-01"),
        _sell(ticker="AAA", traded_at="2026-01-11", realized_pnl=50.0),   # 10-day winner
        _buy(ticker="BBB", traded_at="2026-01-01"),
        _sell(ticker="BBB", traded_at="2026-01-06", realized_pnl=-20.0),  # 5-day loser
    ])
    ext = ta.compute_extended_stats(df)
    stats = ta.build_hold_time_stats(ext)
    assert stats["avg_hold_days"] == 7.5
    assert stats["winners_avg_days"] == 10.0
    assert stats["losers_avg_days"] == 5.0
    assert stats["sample_size"] == 2


# ─── _build_overtrading_stats ────────────────────────────────────────────────

def _months_ago(n_months, day=15):
    """Return an ISO date string exactly n_months before today's (NY-local,
    matching production's `_dt.now(_ET)`) calendar month (day fixed at the
    15th to dodge month-length edge cases), using real month arithmetic via
    pd.DateOffset — NOT a 30-day approximation, which drifts a full calendar
    month off near month boundaries (e.g. 90 days before day 29 of a month
    lands one month short), corrupting the "1 distinct trade per prior month"
    fixture this helper exists to build."""
    dt = pd.Timestamp(datetime.now(_ET)).replace(day=min(day, 28), tzinfo=None) - pd.DateOffset(months=n_months)
    return dt.strftime("%Y-%m-%d")


def test_build_overtrading_stats_insufficient_history_returns_empty():
    # Only the current month present -> < 2 distinct months -> {}.
    current = datetime.now(_ET).strftime("%Y-%m-15")
    df = _trades_df([_buy(traded_at=current), _sell(traded_at=current)])
    assert ta._build_overtrading_stats(df) == {}


def test_build_overtrading_stats_happy_path_elevated():
    # 1 trade/month for 3 prior months (avg=1), then a burst of 4 this month
    # -> multiplier = 4.0 >= 2.0 -> is_elevated True.
    rows = []
    for n in range(3, 0, -1):
        rows.append(_buy(ticker="AAA", traded_at=_months_ago(n)))
    current = datetime.now(_ET).strftime("%Y-%m-%d")
    for _ in range(4):
        rows.append(_sell(ticker="AAA", traded_at=current, realized_pnl=10.0))
    df = _trades_df(rows)
    stats = ta._build_overtrading_stats(df)
    assert stats["current_month_count"] == 4
    assert stats["rolling_avg"] == 1.0
    assert stats["multiplier"] == 4.0
    assert stats["is_elevated"] is True


def test_build_overtrading_stats_not_elevated_below_threshold():
    rows = []
    for n in range(3, 0, -1):
        rows.append(_buy(ticker="AAA", traded_at=_months_ago(n)))
    current = datetime.now(_ET).strftime("%Y-%m-%d")
    rows.append(_sell(ticker="AAA", traded_at=current, realized_pnl=10.0))
    df = _trades_df(rows)
    stats = ta._build_overtrading_stats(df)
    assert stats["multiplier"] == 1.0
    assert stats["is_elevated"] is False


def test_build_overtrading_stats_none_or_empty_returns_empty():
    assert ta._build_overtrading_stats(None) == {}
    assert ta._build_overtrading_stats(pd.DataFrame()) == {}


# ─── build_behavioral_insights ───────────────────────────────────────────────

def test_build_behavioral_insights_no_section_fires_all_neutral():
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=pd.DataFrame(),
        hold_stats={}, win_rate=50.0, profit_factor=None,
        avg_win_pct=None, avg_loss_pct=None, overtrading_stats=None,
    )
    assert insights == []


# -- Loss discipline section --

def test_build_behavioral_insights_loss_discipline_triggers_high():
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=pd.DataFrame(),
        hold_stats={}, win_rate=45.0, profit_factor=None,
        avg_win_pct=10.0, avg_loss_pct=-9.0,  # ratio 1.11 < 1.5, win_rate < 55
    )
    titles = [i["title"] for i in insights]
    assert any("Loss/Win Ratio Too Tight" in t for t in titles)
    assert insights[0]["priority"] == "HIGH"


def test_build_behavioral_insights_loss_discipline_ok_case():
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=pd.DataFrame(),
        hold_stats={}, win_rate=60.0, profit_factor=None,
        avg_win_pct=20.0, avg_loss_pct=-5.0,  # ratio 4.0 >= 2.5, win_rate >= 55
    )
    titles = [i["title"] for i in insights]
    assert any("Strong Win/Loss Asymmetry" in t for t in titles)


# -- Hold time section --

def test_build_behavioral_insights_hold_time_disposition_effect_triggers():
    hold_stats = {"winners_avg_days": 5.0, "losers_avg_days": 10.0}  # 10 > 5*1.3
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=pd.DataFrame(),
        hold_stats=hold_stats, win_rate=50.0, profit_factor=None,
        avg_win_pct=None, avg_loss_pct=None,
    )
    titles = [i["title"] for i in insights]
    assert any("Disposition Effect" in t for t in titles)


def test_build_behavioral_insights_hold_time_healthy_discipline_ok_case():
    hold_stats = {"winners_avg_days": 10.0, "losers_avg_days": 5.0}  # 10 > 5*1.3
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=pd.DataFrame(),
        hold_stats=hold_stats, win_rate=50.0, profit_factor=None,
        avg_win_pct=None, avg_loss_pct=None,
    )
    titles = [i["title"] for i in insights]
    assert any("Healthy Discipline" in t for t in titles)


def test_build_behavioral_insights_hold_time_falsy_zero_skips_section():
    # Documented (if surprising) behavior: the hold-time section requires
    # BOTH winners_avg_days and losers_avg_days truthy -- exactly 0.0 is
    # falsy in Python, so a value of 0.0 skips the section even though it's
    # a legitimate ("same-day") data point, not a missing one.
    hold_stats = {"winners_avg_days": 0.0, "losers_avg_days": 10.0}
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=pd.DataFrame(),
        hold_stats=hold_stats, win_rate=50.0, profit_factor=None,
        avg_win_pct=None, avg_loss_pct=None,
    )
    titles = [i["title"] for i in insights]
    assert not any("Disposition Effect" in t or "Healthy Discipline" in t for t in titles)


# -- Trigger type section --

def _trigger_row(trigger, wr, expectancy):
    return {"Trigger": trigger, "Win Rate (%)": wr, "Expectancy ($)": expectancy}


def test_build_behavioral_insights_trigger_type_best_vs_worst_triggers():
    trigger_df = pd.DataFrame([
        _trigger_row("RECOMMENDATION", 70.0, 100.0),
        _trigger_row("MANUAL", 30.0, -50.0),
    ])
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=trigger_df, monthly_df=pd.DataFrame(),
        hold_stats={}, win_rate=50.0, profit_factor=None,
        avg_win_pct=None, avg_loss_pct=None,
    )
    titles = [i["title"] for i in insights]
    assert any("Outperform" in t for t in titles)


def test_build_behavioral_insights_all_triggers_positive_ok_case():
    trigger_df = pd.DataFrame([
        _trigger_row("RECOMMENDATION", 70.0, 100.0),
        _trigger_row("MANUAL", 60.0, 50.0),
    ])
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=trigger_df, monthly_df=pd.DataFrame(),
        hold_stats={}, win_rate=50.0, profit_factor=None,
        avg_win_pct=None, avg_loss_pct=None,
    )
    titles = [i["title"] for i in insights]
    assert any("All Trigger Types Showing Positive Expectancy" in t for t in titles)


# -- Monthly momentum section --

def test_build_behavioral_insights_monthly_deterioration_triggers():
    monthly_df = pd.DataFrame({
        "month_str": ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"],
        "pnl": [500.0, 500.0, -100.0, -150.0, -200.0],
    })
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=monthly_df,
        hold_stats={}, win_rate=50.0, profit_factor=None,
        avg_win_pct=None, avg_loss_pct=None,
    )
    titles = [i["title"] for i in insights]
    assert any("Deterioration" in t for t in titles)


def test_build_behavioral_insights_monthly_momentum_ok_case():
    monthly_df = pd.DataFrame({
        "month_str": ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"],
        "pnl": [50.0, 50.0, 500.0, 550.0, 600.0],
    })
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=monthly_df,
        hold_stats={}, win_rate=50.0, profit_factor=None,
        avg_win_pct=None, avg_loss_pct=None,
    )
    titles = [i["title"] for i in insights]
    assert any("Momentum" in t for t in titles)


# -- Profit factor section --

def test_build_behavioral_insights_profit_factor_below_one_high():
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=pd.DataFrame(),
        hold_stats={}, win_rate=50.0, profit_factor=0.8,
        avg_win_pct=None, avg_loss_pct=None,
    )
    titles = [i["title"] for i in insights]
    assert any("Below 1.0" in t for t in titles)


def test_build_behavioral_insights_profit_factor_healthy_ok_case():
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=pd.DataFrame(),
        hold_stats={}, win_rate=50.0, profit_factor=2.5,
        avg_win_pct=None, avg_loss_pct=None,
    )
    titles = [i["title"] for i in insights]
    assert any("Healthy Profit Factor" in t for t in titles)


# -- Overtrading section --

def test_build_behavioral_insights_overtrading_elevated_high():
    ot = {"multiplier": 3.0, "rolling_avg": 2.0, "current_month_count": 6, "is_elevated": True}
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=pd.DataFrame(),
        hold_stats={}, win_rate=50.0, profit_factor=None,
        avg_win_pct=None, avg_loss_pct=None, overtrading_stats=ot,
    )
    titles = [i["title"] for i in insights]
    assert any("Overtrading Alert" in t for t in titles)


def test_build_behavioral_insights_overtrading_moderate_pace_not_elevated():
    ot = {"multiplier": 1.6, "rolling_avg": 2.0, "current_month_count": 3, "is_elevated": False}
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=pd.DataFrame(),
        hold_stats={}, win_rate=50.0, profit_factor=None,
        avg_win_pct=None, avg_loss_pct=None, overtrading_stats=ot,
    )
    titles = [i["title"] for i in insights]
    assert any("Trade Pace Elevated" in t for t in titles)


# -- Sort order --

def test_build_behavioral_insights_sort_order_high_before_ok():
    ot = {"multiplier": 3.0, "rolling_avg": 2.0, "current_month_count": 6, "is_elevated": True}  # HIGH
    insights = ta.build_behavioral_insights(
        ext_df=pd.DataFrame(), trigger_df=pd.DataFrame(), monthly_df=pd.DataFrame(),
        hold_stats={}, win_rate=60.0, profit_factor=2.5,  # OK
        avg_win_pct=None, avg_loss_pct=None, overtrading_stats=ot,
    )
    assert len(insights) == 2
    priorities = [i["priority"] for i in insights]
    assert priorities == ["HIGH", "OK"]


# ─── build_full_analytics ────────────────────────────────────────────────────

def test_build_full_analytics_none_or_empty_returns_empty_shape():
    result = ta.build_full_analytics(None)
    assert result["ext_df"].empty
    assert result["insights"] == []
    result2 = ta.build_full_analytics(pd.DataFrame())
    assert result2["insights"] == []


def test_build_full_analytics_no_sell_rows_returns_empty_shape():
    df = _trades_df([_buy()])
    result = ta.build_full_analytics(df)
    assert result["ext_df"].empty
    assert result["profit_factor"] is None
    assert result["win_rate"] is None


def test_build_full_analytics_happy_path_multi_month_history():
    rows = []
    for n in range(3, -1, -1):
        d = _months_ago(n) if n > 0 else datetime.now(_ET).strftime("%Y-%m-%d")
        rows.append(_buy(ticker="AAA", traded_at=d))
        rows.append(_sell(ticker="AAA", traded_at=d, realized_pnl=25.0, cost_basis=50.0, shares=10))
    df = _trades_df(rows)
    result = ta.build_full_analytics(df)
    assert not result["ext_df"].empty
    assert result["profit_factor"] is None  # no losers -> pf_denom 0 -> None
    assert result["win_rate"] == 100.0
    assert isinstance(result["insights"], list)
