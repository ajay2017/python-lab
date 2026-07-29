"""Tests for stock_analyzer/trades.py — realized-P&L performance summary
(`performance_stats`) and the single-trade SELL P&L formula
(`compute_realized_pnl`). Pure pandas aggregation, no I/O. Previously zero
test coverage.
"""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer import trades as tr


# ─── builders ───────────────────────────────────────────────────────────────

def _trades_df(rows):
    """rows: list of dicts with action/ticker/realized_pnl (any missing key
    defaults so a test only needs to vary the field(s) it's checking)."""
    defaults = {"action": "BUY", "ticker": "AAA", "realized_pnl": np.nan}
    return pd.DataFrame([{**defaults, **r} for r in rows])


# ─── performance_stats — empty / None input ──────────────────────────────────

_EMPTY = {
    "total_trades": 0, "sell_trades": 0, "buy_trades": 0,
    "total_realized_pnl": 0.0, "wins": 0, "losses": 0,
    "win_rate": 0.0, "avg_winner": 0.0, "avg_loser": 0.0,
    "best_trade": None, "worst_trade": None,
    "realized_by_ticker": {},
}


def test_performance_stats_none_df_returns_empty_dict():
    assert tr.performance_stats(None) == _EMPTY


def test_performance_stats_empty_df_returns_empty_dict():
    assert tr.performance_stats(pd.DataFrame()) == _EMPTY


# ─── performance_stats — SPLIT rows excluded from all aggregates ───────────

def test_performance_stats_split_rows_excluded_from_total_trades():
    df = _trades_df([
        {"action": "BUY"},
        {"action": "SELL", "realized_pnl": 5.0},
        {"action": "SPLIT"},
        {"action": "SPLIT"},
    ])
    result = tr.performance_stats(df)
    assert result["total_trades"] == 2  # SPLIT rows don't count
    assert result["buy_trades"] == 1
    assert result["sell_trades"] == 1


# ─── performance_stats — no SELL rows: partial-empty dict ──────────────────

def test_performance_stats_no_sell_rows_returns_partial_empty():
    df = _trades_df([{"action": "BUY"}, {"action": "BUY"}])
    result = tr.performance_stats(df)
    assert result["total_trades"] == 2
    assert result["buy_trades"] == 2
    assert result["sell_trades"] == 0
    assert result["total_realized_pnl"] == 0.0
    assert result["wins"] == 0
    assert result["losses"] == 0
    assert result["win_rate"] == 0.0
    assert result["best_trade"] is None
    assert result["worst_trade"] is None
    assert result["realized_by_ticker"] == {}


# ─── performance_stats — NaN realized_pnl dropped from win/loss but counted
#     toward sell_trades (sells is pre-dropna, with_pnl is post-dropna) ────

def test_performance_stats_sell_trades_counts_nan_pnl_rows_but_stats_exclude_them():
    df = _trades_df([
        {"action": "BUY"},
        {"action": "SELL", "realized_pnl": 10.0},
        {"action": "SELL", "realized_pnl": np.nan},  # dropped from win/loss stats
    ])
    result = tr.performance_stats(df)
    assert result["sell_trades"] == 2       # pre-dropna count
    assert result["wins"] == 1              # post-dropna (with_pnl) count
    assert result["losses"] == 0
    assert result["total_realized_pnl"] == 10.0


# ─── performance_stats — win_rate calc + rounding ───────────────────────────

def test_performance_stats_win_rate_calculation_and_rounding():
    df = _trades_df([
        {"action": "SELL", "realized_pnl": 10.0},
        {"action": "SELL", "realized_pnl": -5.0},
        {"action": "SELL", "realized_pnl": 3.0},
    ])
    result = tr.performance_stats(df)
    assert result["wins"] == 2
    assert result["losses"] == 1
    assert result["win_rate"] == pytest.approx(round(2 / 3 * 100, 1))
    assert result["avg_winner"] == pytest.approx(round((10.0 + 3.0) / 2, 2))
    assert result["avg_loser"] == pytest.approx(-5.0)


# ─── performance_stats — best_trade / worst_trade shape ────────────────────

def test_performance_stats_best_and_worst_trade_are_full_row_dicts():
    df = _trades_df([
        {"action": "SELL", "ticker": "AAA", "realized_pnl": 10.0},
        {"action": "SELL", "ticker": "BBB", "realized_pnl": -7.0},
    ])
    result = tr.performance_stats(df)
    assert result["best_trade"]["ticker"] == "AAA"
    assert result["best_trade"]["realized_pnl"] == 10.0
    assert result["worst_trade"]["ticker"] == "BBB"
    assert result["worst_trade"]["realized_pnl"] == -7.0
    assert set(result["best_trade"].keys()) == {"action", "ticker", "realized_pnl"}


# ─── performance_stats — realized_by_ticker grouped/summed/sorted desc ────

def test_performance_stats_realized_by_ticker_grouped_summed_sorted_desc():
    df = _trades_df([
        {"action": "SELL", "ticker": "AAA", "realized_pnl": 5.0},
        {"action": "SELL", "ticker": "AAA", "realized_pnl": 3.0},
        {"action": "SELL", "ticker": "BBB", "realized_pnl": 20.0},
        {"action": "SELL", "ticker": "CCC", "realized_pnl": -1.0},
    ])
    result = tr.performance_stats(df)
    assert result["realized_by_ticker"] == {"BBB": 20.0, "AAA": 8.0, "CCC": -1.0}
    assert list(result["realized_by_ticker"].keys()) == ["BBB", "AAA", "CCC"]


# ─── compute_realized_pnl ─────────────────────────────────────────────────────

def test_compute_realized_pnl_none_cost_basis_returns_none():
    assert tr.compute_realized_pnl(10, 100.0, None) is None


def test_compute_realized_pnl_zero_cost_basis_returns_none():
    assert tr.compute_realized_pnl(10, 100.0, 0) is None


def test_compute_realized_pnl_negative_cost_basis_returns_none():
    assert tr.compute_realized_pnl(10, 100.0, -5.0) is None


def test_compute_realized_pnl_normal_case_formula_and_rounding():
    result = tr.compute_realized_pnl(shares=10, price=105.256, cost_basis=100.0)
    assert result == round((105.256 - 100.0) * 10, 2)
