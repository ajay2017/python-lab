"""Regression tests for stock_analyzer/tax_advisor.py — the Tax Efficiency
Advisor: FIFO tax-lot reconstruction, STCG/LTCG classification, harvest/wait
action ladder, and the awareness-only holding-period + wash-sale helpers
used elsewhere (EXIT cards). Pure computation (date math + pandas), no I/O
beyond the caller-supplied DataFrames. See docs/plans/test-automation.md.
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import tax_advisor as ta
from stock_analyzer.constants import (
    TAX_HARVEST_MIN_LOSS,
    TAX_LTCG_WAIT_WINDOW_DAYS,
    TAX_LONGTERM_WINDOW_DAYS,
    TAX_RATE_LONG_TERM,
    TAX_RATE_SHORT_TERM,
    TAX_STCG_THRESHOLD_DAYS,
    TAX_WASH_SALE_DAYS,
)

TODAY = date(2026, 7, 28)


def _trades(rows):
    """rows: list of (id, ticker, days_ago, action, shares)."""
    return pd.DataFrame([
        {"id": i, "ticker": t, "traded_at": (TODAY - timedelta(days=d)).isoformat(),
         "action": a, "shares": sh}
        for i, t, d, a, sh in rows
    ])


def _port_row(ticker="AAPL", shares=100.0, avg_cost=100.0, price=110.0,
              pnl=1000.0, signal="Hold"):
    return {
        "Ticker": ticker, "Shares": shares, "Avg Cost": avg_cost,
        "Price": price, "P&L ($)": pnl, "Signal": signal,
    }


# ── _f / _opt ────────────────────────────────────────────────────────────

def test_f_none_returns_default():
    assert ta._f(None) == 0.0
    assert ta._f(None, default=5) == 5


def test_f_nan_returns_default():
    assert ta._f(float("nan"), default=-1) == -1


def test_f_unparseable_returns_default():
    assert ta._f("bad", default=2) == 2


def test_opt_none_for_none():
    assert ta._opt(None) is None


def test_opt_none_for_nan():
    assert ta._opt(float("nan")) is None


def test_opt_none_for_unparseable():
    assert ta._opt("bad") is None


def test_opt_preserves_valid_value():
    assert ta._opt("42.5") == 42.5


# ── _earliest_buy ────────────────────────────────────────────────────────

def test_earliest_buy_none_trades_df():
    assert ta._earliest_buy("AAPL", None) is None


def test_earliest_buy_empty_trades_df():
    assert ta._earliest_buy("AAPL", pd.DataFrame()) is None


def test_earliest_buy_no_buy_rows_for_ticker():
    trades = _trades([(0, "AAPL", 100, "SELL", 10)])
    assert ta._earliest_buy("AAPL", trades) is None


def test_earliest_buy_returns_earliest_of_multiple():
    trades = _trades([(0, "AAPL", 100, "BUY", 10), (1, "AAPL", 300, "BUY", 5)])
    assert ta._earliest_buy("AAPL", trades) == TODAY - timedelta(days=300)


def test_earliest_buy_case_insensitive_ticker():
    trades = _trades([(0, "aapl", 100, "BUY", 10)])
    assert ta._earliest_buy("AAPL", trades) == TODAY - timedelta(days=100)


# ── _build_open_lots ─────────────────────────────────────────────────────

def test_build_open_lots_none_trades_df():
    assert ta._build_open_lots("AAPL", None, TODAY) == []


def test_build_open_lots_no_rows_for_ticker():
    trades = _trades([(0, "MSFT", 100, "BUY", 10)])
    assert ta._build_open_lots("AAPL", trades, TODAY) == []


def test_build_open_lots_single_buy():
    trades = _trades([(0, "AAPL", 100, "BUY", 10)])
    lots = ta._build_open_lots("AAPL", trades, TODAY)
    assert len(lots) == 1
    assert lots[0]["shares"] == 10
    assert lots[0]["days_held"] == 100


def test_build_open_lots_zero_or_negative_shares_skipped():
    trades = _trades([(0, "AAPL", 100, "BUY", 0), (1, "AAPL", 90, "BUY", -5),
                       (2, "AAPL", 80, "BUY", 10)])
    lots = ta._build_open_lots("AAPL", trades, TODAY)
    assert len(lots) == 1
    assert lots[0]["shares"] == 10


def test_build_open_lots_unparseable_shares_skipped():
    trades = pd.DataFrame([
        {"id": 0, "ticker": "AAPL", "traded_at": (TODAY - timedelta(days=100)).isoformat(),
         "action": "BUY", "shares": "not-a-number"},
    ])
    assert ta._build_open_lots("AAPL", trades, TODAY) == []


def test_build_open_lots_sell_fully_consumes_oldest_lot_fifo():
    trades = _trades([
        (0, "AAPL", 200, "BUY", 10),
        (1, "AAPL", 100, "BUY", 10),
        (2, "AAPL", 50, "SELL", 10),
    ])
    lots = ta._build_open_lots("AAPL", trades, TODAY)
    assert len(lots) == 1
    assert lots[0]["days_held"] == 100  # the newer lot remains


def test_build_open_lots_sell_partially_consumes_oldest_lot():
    trades = _trades([
        (0, "AAPL", 200, "BUY", 10),
        (1, "AAPL", 50, "SELL", 4),
    ])
    lots = ta._build_open_lots("AAPL", trades, TODAY)
    assert len(lots) == 1
    assert lots[0]["shares"] == 6
    assert lots[0]["days_held"] == 200


def test_build_open_lots_sell_across_multiple_lots():
    trades = _trades([
        (0, "AAPL", 300, "BUY", 5),
        (1, "AAPL", 200, "BUY", 5),
        (2, "AAPL", 50, "SELL", 8),
    ])
    lots = ta._build_open_lots("AAPL", trades, TODAY)
    assert len(lots) == 1
    assert lots[0]["shares"] == 2
    assert lots[0]["days_held"] == 200  # remainder from the second (newer) lot


def test_build_open_lots_split_pro_rata_adjusts_existing_lots():
    trades = _trades([
        (0, "AAPL", 200, "BUY", 10),
        (1, "AAPL", 100, "SPLIT", 20),  # 2-for-1: 10 -> 20
    ])
    lots = ta._build_open_lots("AAPL", trades, TODAY)
    assert len(lots) == 1
    assert lots[0]["shares"] == 20
    assert lots[0]["days_held"] == 200  # original acquisition date preserved


def test_build_open_lots_split_with_no_prior_lots_synthesizes_seed():
    trades = _trades([(0, "AAPL", 100, "SPLIT", 15)])
    lots = ta._build_open_lots("AAPL", trades, TODAY)
    assert len(lots) == 1
    assert lots[0]["shares"] == 15
    assert lots[0]["days_held"] == 100


# ── split_ratio (premortem_monitor.py's split-safety fix) ──────────────────

def test_build_open_lots_no_split_ratio_stays_one():
    trades = _trades([(0, "AAPL", 200, "BUY", 10)])
    lots = ta._build_open_lots("AAPL", trades, TODAY)
    assert lots[0]["split_ratio"] == 1.0


def test_build_open_lots_split_ratio_tracks_2for1():
    trades = _trades([
        (0, "AAPL", 200, "BUY", 10),
        (1, "AAPL", 100, "SPLIT", 20),  # 2-for-1: 10 -> 20
    ])
    lots = ta._build_open_lots("AAPL", trades, TODAY)
    assert lots[0]["split_ratio"] == 2.0


def test_build_open_lots_split_ratio_compounds_across_two_splits():
    trades = _trades([
        (0, "AAPL", 300, "BUY", 10),
        (1, "AAPL", 200, "SPLIT", 20),   # 2-for-1
        (2, "AAPL", 100, "SPLIT", 80),   # 4-for-1 -> cumulative 8x
    ])
    lots = ta._build_open_lots("AAPL", trades, TODAY)
    assert len(lots) == 1
    assert lots[0]["shares"] == 80
    assert lots[0]["split_ratio"] == 8.0


def test_build_open_lots_split_ratio_synthesized_seed_is_one():
    """No prior lots to ratio-adjust against — treated as already in
    current terms (module docstring's documented assumption)."""
    trades = _trades([(0, "AAPL", 100, "SPLIT", 15)])
    lots = ta._build_open_lots("AAPL", trades, TODAY)
    assert lots[0]["split_ratio"] == 1.0


def test_build_open_lots_sell_then_rebuy_new_lot_ratio_is_one():
    """A split affecting an OLD (now-closed) lot must not leak into a
    brand-new lot opened afterward."""
    trades = _trades([
        (0, "AAPL", 300, "BUY", 10),
        (1, "AAPL", 250, "SPLIT", 20),   # 2-for-1 on the old lot
        (2, "AAPL", 200, "SELL", 20),    # old lot fully closed
        (3, "AAPL", 100, "BUY", 5),      # brand-new lot, never split
    ])
    lots = ta._build_open_lots("AAPL", trades, TODAY)
    assert len(lots) == 1
    assert lots[0]["shares"] == 5
    assert lots[0]["split_ratio"] == 1.0


def test_build_open_lots_invalid_timestamps_dropped():
    trades = pd.DataFrame([
        {"id": 0, "ticker": "AAPL", "traded_at": "not-a-date", "action": "BUY", "shares": 10},
    ])
    assert ta._build_open_lots("AAPL", trades, TODAY) == []


# ── build_tax_analysis ───────────────────────────────────────────────────

def test_build_tax_analysis_skips_row_missing_shares():
    df = pd.DataFrame([{**_port_row(), "Shares": None}])
    result = ta.build_tax_analysis(df, None, today=TODAY)
    assert result["rows"] == []


def test_build_tax_analysis_skips_row_missing_avg_cost():
    df = pd.DataFrame([{**_port_row(), "Avg Cost": None}])
    result = ta.build_tax_analysis(df, None, today=TODAY)
    assert result["rows"] == []


def test_build_tax_analysis_skips_row_missing_price():
    df = pd.DataFrame([{**_port_row(), "Price": None}])
    result = ta.build_tax_analysis(df, None, today=TODAY)
    assert result["rows"] == []


def test_build_tax_analysis_skips_row_missing_pnl():
    df = pd.DataFrame([{**_port_row(), "P&L ($)": None}])
    result = ta.build_tax_analysis(df, None, today=TODAY)
    assert result["rows"] == []


def test_build_tax_analysis_unknown_gain_type_no_trades():
    df = pd.DataFrame([_port_row(pnl=1000.0)])
    result = ta.build_tax_analysis(df, None, today=TODAY)
    row = result["rows"][0]
    assert row["gain_type"] == "Unknown"
    assert row["days_held"] is None
    assert row["acq_date"] is None


def test_build_tax_analysis_unknown_gain_type_worst_case_tax():
    df = pd.DataFrame([_port_row(pnl=1000.0)])
    result = ta.build_tax_analysis(df, None, today=TODAY)
    row = result["rows"][0]
    assert row["tax_if_sold_today"] == round(1000.0 * TAX_RATE_SHORT_TERM, 0)
    assert row["tax_if_ltcg"] == round(1000.0 * TAX_RATE_LONG_TERM, 0)


def test_build_tax_analysis_unknown_gain_type_with_earliest_buy_fallback():
    trades = _trades([(0, "AAPL", 500, "SELL", 100)])  # no BUY rows -> no open lots
    df = pd.DataFrame([_port_row(pnl=1000.0)])
    result = ta.build_tax_analysis(df, trades, today=TODAY)
    assert result["rows"][0]["gain_type"] == "Unknown"


def test_build_tax_analysis_all_ltcg():
    trades = _trades([(0, "AAPL", 400, "BUY", 100)])
    df = pd.DataFrame([_port_row(shares=100.0, pnl=1000.0)])
    result = ta.build_tax_analysis(df, trades, today=TODAY)
    row = result["rows"][0]
    assert row["gain_type"] == "LTCG"
    assert row["days_to_ltcg"] == 0
    assert row["ltcg_frac"] == 1.0
    assert row["action"] == "LTCG_ELIGIBLE"
    assert row["tax_if_sold_today"] == round(1000.0 * TAX_RATE_LONG_TERM, 0)


def test_build_tax_analysis_all_stcg():
    trades = _trades([(0, "AAPL", 100, "BUY", 100)])
    df = pd.DataFrame([_port_row(shares=100.0, pnl=1000.0)])
    result = ta.build_tax_analysis(df, trades, today=TODAY)
    row = result["rows"][0]
    assert row["gain_type"] == "STCG"
    assert row["days_to_ltcg"] == TAX_STCG_THRESHOLD_DAYS - 100
    assert row["stcg_frac"] == 1.0
    assert row["tax_if_sold_today"] == round(1000.0 * TAX_RATE_SHORT_TERM, 0)


def test_build_tax_analysis_mixed_lots_apportioned():
    trades = _trades([
        (0, "AAPL", 400, "BUY", 60),   # LTCG lot
        (1, "AAPL", 100, "BUY", 40),   # STCG lot
    ])
    df = pd.DataFrame([_port_row(shares=100.0, pnl=1000.0)])
    result = ta.build_tax_analysis(df, trades, today=TODAY)
    row = result["rows"][0]
    assert row["gain_type"] == "MIXED"
    assert row["ltcg_frac"] == 0.6
    assert row["stcg_frac"] == 0.4
    expected_tax = round(1000.0 * 0.4 * TAX_RATE_SHORT_TERM + 1000.0 * 0.6 * TAX_RATE_LONG_TERM, 0)
    assert row["tax_if_sold_today"] == expected_tax


def test_build_tax_analysis_negative_pnl_zero_tax_and_harvestable():
    df = pd.DataFrame([_port_row(pnl=-1000.0)])
    result = ta.build_tax_analysis(df, None, today=TODAY)
    row = result["rows"][0]
    assert row["tax_if_sold_today"] == 0.0
    assert row["tax_if_ltcg"] == 0.0
    assert row["harvestable"] == 1000.0


def test_build_tax_analysis_harvest_action_on_loss_beyond_min():
    df = pd.DataFrame([_port_row(pnl=-(TAX_HARVEST_MIN_LOSS + 1), signal="Hold")])
    result = ta.build_tax_analysis(df, None, today=TODAY)
    row = result["rows"][0]
    assert row["action"] == "HARVEST"
    assert row["harvest_blocked"] is False


def test_build_tax_analysis_harvest_blocked_by_buy_conviction():
    df = pd.DataFrame([_port_row(pnl=-(TAX_HARVEST_MIN_LOSS + 1), signal="Strong Buy")])
    result = ta.build_tax_analysis(df, None, today=TODAY)
    row = result["rows"][0]
    assert row["action"] == "HOLD_FOR_SIGNAL"
    assert row["harvest_blocked"] is True


def test_build_tax_analysis_small_loss_not_harvestable_falls_to_monitor():
    df = pd.DataFrame([_port_row(pnl=-(TAX_HARVEST_MIN_LOSS - 1), signal="Hold")])
    result = ta.build_tax_analysis(df, None, today=TODAY)
    row = result["rows"][0]
    assert row["action"] == "MONITOR"


def test_build_tax_analysis_wait_action_near_ltcg():
    days_ago = TAX_STCG_THRESHOLD_DAYS - TAX_LTCG_WAIT_WINDOW_DAYS  # exactly at the WAIT boundary
    trades = _trades([(0, "AAPL", days_ago, "BUY", 100)])
    df = pd.DataFrame([_port_row(shares=100.0, pnl=1000.0)])
    result = ta.build_tax_analysis(df, trades, today=TODAY)
    row = result["rows"][0]
    assert row["gain_type"] == "STCG"
    assert row["days_to_ltcg"] == TAX_LTCG_WAIT_WINDOW_DAYS
    assert row["action"] == "WAIT"


def test_build_tax_analysis_hold_for_ltcg_when_far_from_threshold():
    trades = _trades([(0, "AAPL", 10, "BUY", 100)])  # far from LTCG
    df = pd.DataFrame([_port_row(shares=100.0, pnl=1000.0)])
    result = ta.build_tax_analysis(df, trades, today=TODAY)
    row = result["rows"][0]
    assert row["gain_type"] == "STCG"
    assert row["days_to_ltcg"] > TAX_LTCG_WAIT_WINDOW_DAYS
    assert row["action"] == "HOLD_FOR_LTCG"


def test_build_tax_analysis_sort_order():
    trades = _trades([
        (0, "HARV", 100, "BUY", 100),
        (1, "WAITX", TAX_STCG_THRESHOLD_DAYS - 30, "BUY", 100),
        (2, "HOLDL", 10, "BUY", 100),
        (3, "LTELG", 400, "BUY", 100),
    ])
    df = pd.DataFrame([
        _port_row(ticker="HARV", pnl=-(TAX_HARVEST_MIN_LOSS + 1), signal="Hold"),
        _port_row(ticker="WAITX", shares=100.0, pnl=1000.0),
        _port_row(ticker="HOLDL", shares=100.0, pnl=1000.0),
        _port_row(ticker="LTELG", shares=100.0, pnl=1000.0),
        _port_row(ticker="MONI", pnl=0.0),
    ])
    result = ta.build_tax_analysis(df, trades, today=TODAY)
    actions = [r["action"] for r in result["rows"]]
    assert actions == ["HARVEST", "WAIT", "HOLD_FOR_LTCG", "LTCG_ELIGIBLE", "MONITOR"]


def test_build_tax_analysis_totals_aggregate_across_rows():
    trades = _trades([
        (0, "AAPL", 400, "BUY", 100),   # LTCG
        (1, "MSFT", 100, "BUY", 100),   # STCG
    ])
    df = pd.DataFrame([
        _port_row(ticker="AAPL", shares=100.0, pnl=1000.0),
        _port_row(ticker="MSFT", shares=100.0, pnl=500.0),
        _port_row(ticker="LOSS", pnl=-800.0),
    ])
    result = ta.build_tax_analysis(df, trades, today=TODAY)
    assert result["total_ltcg_gain"] == 1000.0
    assert result["total_stcg_gain"] == 500.0
    assert result["total_harvestable"] == 800.0
    assert result["tax_today"] == round(1000.0 * TAX_RATE_LONG_TERM + 500.0 * TAX_RATE_SHORT_TERM, 0)


def test_build_tax_analysis_custom_rates_override_defaults():
    df = pd.DataFrame([_port_row(pnl=1000.0)])
    result = ta.build_tax_analysis(df, None, stcg_rate=0.5, ltcg_rate=0.1, today=TODAY)
    row = result["rows"][0]
    assert row["tax_if_sold_today"] == 500.0
    assert row["tax_if_ltcg"] == 100.0
    assert result["stcg_rate"] == 0.5
    assert result["ltcg_rate"] == 0.1


def test_build_tax_analysis_defaults_today_when_not_supplied():
    df = pd.DataFrame([_port_row(pnl=1000.0)])
    result = ta.build_tax_analysis(df, None)  # no `today` -> _today_et()
    assert result["rows"][0]["gain_type"] == "Unknown"


# ── holding_period_status ────────────────────────────────────────────────

def test_holding_period_status_none_when_no_lots():
    assert ta.holding_period_status("AAPL", None, today=TODAY) is None


def test_holding_period_status_ltcg_not_near():
    trades = _trades([(0, "AAPL", 400, "BUY", 10)])
    result = ta.holding_period_status("AAPL", trades, today=TODAY)
    assert result["gain_type"] == "LTCG"
    assert result["days_to_ltcg"] == 0
    assert result["near_ltcg"] is False


def test_holding_period_status_stcg_near_ltcg():
    days_ago = TAX_STCG_THRESHOLD_DAYS - TAX_LONGTERM_WINDOW_DAYS  # exactly at the near-LTCG boundary
    trades = _trades([(0, "AAPL", days_ago, "BUY", 10)])
    result = ta.holding_period_status("AAPL", trades, today=TODAY)
    assert result["gain_type"] == "STCG"
    assert result["days_to_ltcg"] == TAX_LONGTERM_WINDOW_DAYS
    assert result["near_ltcg"] is True


def test_holding_period_status_stcg_far_from_ltcg_not_near():
    trades = _trades([(0, "AAPL", 10, "BUY", 10)])
    result = ta.holding_period_status("AAPL", trades, today=TODAY)
    assert result["gain_type"] == "STCG"
    assert result["near_ltcg"] is False


def test_holding_period_status_mixed():
    trades = _trades([(0, "AAPL", 400, "BUY", 60), (1, "AAPL", 100, "BUY", 40)])
    result = ta.holding_period_status("AAPL", trades, today=TODAY)
    assert result["gain_type"] == "MIXED"


def test_holding_period_status_custom_window():
    trades = _trades([(0, "AAPL", TAX_STCG_THRESHOLD_DAYS - 50, "BUY", 10)])
    result = ta.holding_period_status("AAPL", trades, today=TODAY, lt_window_days=100)
    assert result["near_ltcg"] is True  # 50 <= 100 (custom window), would be False under default 30


# ── wash_sale_risk ───────────────────────────────────────────────────────

def test_wash_sale_risk_none_trades_df():
    assert ta.wash_sale_risk("AAPL", None, today=TODAY) is None


def test_wash_sale_risk_empty_trades_df():
    assert ta.wash_sale_risk("AAPL", pd.DataFrame(), today=TODAY) is None


def test_wash_sale_risk_no_buy_rows():
    trades = _trades([(0, "AAPL", 10, "SELL", 10)])
    assert ta.wash_sale_risk("AAPL", trades, today=TODAY) is None


def test_wash_sale_risk_recent_buy_within_window():
    trades = _trades([(0, "AAPL", 10, "BUY", 10)])
    result = ta.wash_sale_risk("AAPL", trades, today=TODAY)
    assert result["days_ago"] == 10
    assert result["window_days"] == TAX_WASH_SALE_DAYS


def test_wash_sale_risk_boundary_exactly_at_window_included():
    trades = _trades([(0, "AAPL", TAX_WASH_SALE_DAYS, "BUY", 10)])
    result = ta.wash_sale_risk("AAPL", trades, today=TODAY)
    assert result is not None
    assert result["days_ago"] == TAX_WASH_SALE_DAYS


def test_wash_sale_risk_just_past_window_excluded():
    trades = _trades([(0, "AAPL", TAX_WASH_SALE_DAYS + 1, "BUY", 10)])
    assert ta.wash_sale_risk("AAPL", trades, today=TODAY) is None


def test_wash_sale_risk_same_day_buy_included():
    trades = _trades([(0, "AAPL", 0, "BUY", 10)])
    result = ta.wash_sale_risk("AAPL", trades, today=TODAY)
    assert result["days_ago"] == 0


def test_wash_sale_risk_uses_most_recent_buy():
    trades = _trades([(0, "AAPL", 25, "BUY", 10), (1, "AAPL", 5, "BUY", 10)])
    result = ta.wash_sale_risk("AAPL", trades, today=TODAY)
    assert result["days_ago"] == 5


def test_wash_sale_risk_case_insensitive_ticker():
    trades = _trades([(0, "aapl", 10, "BUY", 10)])
    result = ta.wash_sale_risk("AAPL", trades, today=TODAY)
    assert result is not None


def test_wash_sale_risk_custom_window():
    trades = _trades([(0, "AAPL", 40, "BUY", 10)])
    assert ta.wash_sale_risk("AAPL", trades, today=TODAY) is None  # past default 30-day window
    result = ta.wash_sale_risk("AAPL", trades, today=TODAY, window_days=45)
    assert result is not None
