"""Tests for stock_analyzer/daily_pnl.py — Tier-B positions-scope day P&L
(`compute_positions_day_pnl`), the today's-trades cash delta it consumes
(`today_trade_cash_delta`), and the NaN-safe float coercion helper (`_num`).
Pure dict/list math, no I/O. Previously zero test coverage.
"""
import math

from stock_analyzer import daily_pnl as dp


# ─── _num — NaN/None/junk coercion ────────────────────────────────────────────

def test_num_none_returns_default():
    assert dp._num(None) == 0.0
    assert dp._num(None, default=5.0) == 5.0


def test_num_nan_returns_default():
    assert dp._num(float("nan")) == 0.0
    assert dp._num(float("nan"), default=-1.0) == -1.0


def test_num_junk_string_returns_default():
    assert dp._num("not-a-number") == 0.0


def test_num_valid_value_coerces_to_float():
    assert dp._num("3.5") == 3.5
    assert dp._num(3) == 3.0


# ─── today_trade_cash_delta ───────────────────────────────────────────────────

def test_today_trade_cash_delta_empty_list_is_zero():
    assert dp.today_trade_cash_delta([]) == 0.0


def test_today_trade_cash_delta_sell_adds_cash():
    trades = [{"action": "SELL", "price": 10.0, "shares": 5}]
    assert dp.today_trade_cash_delta(trades) == 50.0


def test_today_trade_cash_delta_buy_subtracts_cash():
    trades = [{"action": "BUY", "price": 10.0, "shares": 5}]
    assert dp.today_trade_cash_delta(trades) == -50.0


def test_today_trade_cash_delta_split_and_other_actions_ignored():
    trades = [
        {"action": "SPLIT", "price": 100.0, "shares": 5},
        {"action": "DIVIDEND", "price": 100.0, "shares": 5},
    ]
    assert dp.today_trade_cash_delta(trades) == 0.0


def test_today_trade_cash_delta_mixed_buy_and_sell_nets_correctly():
    trades = [
        {"action": "SELL", "price": 20.0, "shares": 3},   # +60
        {"action": "BUY", "price": 10.0, "shares": 4},    # -40
        {"action": "SPLIT", "price": 5.0, "shares": 100},  # ignored
    ]
    assert dp.today_trade_cash_delta(trades) == 20.0


# ─── compute_positions_day_pnl — baseline guard ──────────────────────────────

def test_compute_positions_day_pnl_empty_baseline_returns_none():
    assert dp.compute_positions_day_pnl([], {}, [], 1000.0) is None


def test_compute_positions_day_pnl_none_baseline_returns_none():
    assert dp.compute_positions_day_pnl([], None, [], 1000.0) is None


# ─── compute_positions_day_pnl — normal case formula ────────────────────────

def test_compute_positions_day_pnl_normal_case_matches_formula():
    held = [{"ticker": "AAA", "shares": 10, "price": 110.0}]
    baseline = {"AAA": {"shares": 10, "close": 100.0}}
    today_trades = [{"ticker": "AAA", "action": "SELL", "price": 50.0, "shares": 1}]
    total_value = 1100.0

    result = dp.compute_positions_day_pnl(held, baseline, today_trades, total_value)

    current_val = 10 * 110.0
    baseline_val = 10 * 100.0
    cash_delta = 50.0 * 1  # SELL adds
    expected_pnl = current_val - baseline_val + cash_delta
    expected_pct = expected_pnl / total_value * 100.0

    assert result["day_pnl"] == round(expected_pnl, 2)
    assert result["day_pnl_pct"] == round(expected_pct, 2)
    assert result["trade_cash_delta"] == round(cash_delta, 2)
    assert result["current_value"] == round(current_val, 2)
    assert result["baseline_value"] == round(baseline_val, 2)
    assert result["n_baseline"] == 1


def test_compute_positions_day_pnl_total_value_zero_guard_returns_zero_pct():
    held = [{"ticker": "AAA", "shares": 10, "price": 110.0}]
    baseline = {"AAA": {"shares": 10, "close": 100.0}}
    result = dp.compute_positions_day_pnl(held, baseline, [], 0.0)
    assert result["day_pnl_pct"] == 0.0
    assert not math.isnan(result["day_pnl_pct"])


# ─── compute_positions_day_pnl — orphan detection ───────────────────────────

def test_compute_positions_day_pnl_orphan_ticker_neither_held_nor_traded_flagged():
    held = [{"ticker": "AAA", "shares": 10, "price": 110.0}]
    baseline = {
        "AAA": {"shares": 10, "close": 100.0},
        "BBB": {"shares": 5, "close": 50.0},   # vanished with no exit recorded
    }
    result = dp.compute_positions_day_pnl(held, baseline, [], 1000.0)
    assert result["orphans"] == ["BBB"]


def test_compute_positions_day_pnl_still_held_ticker_is_not_an_orphan():
    held = [{"ticker": "AAA", "shares": 10, "price": 110.0}]
    baseline = {"AAA": {"shares": 10, "close": 100.0}}
    result = dp.compute_positions_day_pnl(held, baseline, [], 1000.0)
    assert result["orphans"] == []


def test_compute_positions_day_pnl_ticker_sold_today_is_not_an_orphan():
    held = []  # fully exited
    baseline = {"AAA": {"shares": 10, "close": 100.0}}
    today_trades = [{"ticker": "AAA", "action": "SELL", "price": 105.0, "shares": 10}]
    result = dp.compute_positions_day_pnl(held, baseline, today_trades, 1000.0)
    assert result["orphans"] == []


def test_compute_positions_day_pnl_orphans_sorted_alphabetically():
    held = []
    baseline = {
        "ZZZ": {"shares": 1, "close": 10.0},
        "AAA": {"shares": 1, "close": 10.0},
        "MMM": {"shares": 1, "close": 10.0},
    }
    result = dp.compute_positions_day_pnl(held, baseline, [], 1000.0)
    assert result["orphans"] == ["AAA", "MMM", "ZZZ"]


# ─── compute_positions_day_pnl — rounding ───────────────────────────────────

def test_compute_positions_day_pnl_rounds_all_numeric_outputs():
    held = [{"ticker": "AAA", "shares": 3, "price": 100.123456}]
    baseline = {"AAA": {"shares": 3, "close": 99.987654}}
    result = dp.compute_positions_day_pnl(held, baseline, [], 300.0)
    for key in ("day_pnl", "day_pnl_pct", "trade_cash_delta", "current_value", "baseline_value"):
        value = result[key]
        assert round(value, 2) == value
