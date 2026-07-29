"""Tests for stock_analyzer/exit_velocity.py — WATCH-tier signal velocity
detection (compute_watch_velocity, find_accelerating_watches). Previously
zero test coverage despite feeding an alert that fires BEFORE a TRIM
threshold is crossed. Pure pandas, no I/O.
"""
import pandas as pd

from stock_analyzer import exit_velocity as ev


# ─── builders ───────────────────────────────────────────────────────────────

def _row(ticker="AAA", signal_date="2026-01-01", signal_type="WATCH", composite_score=60.0):
    return {
        "ticker": ticker, "signal_date": signal_date,
        "signal_type": signal_type, "composite_score": composite_score,
    }


def _df(rows):
    return pd.DataFrame(rows)


REQUIRED_COLS = {"ticker", "signal_date", "signal_type", "composite_score"}


# ─── compute_watch_velocity — None-return branches ──────────────────────────

def test_compute_watch_velocity_df_none_returns_none():
    assert ev.compute_watch_velocity(None, "AAA", 30) is None


def test_compute_watch_velocity_df_empty_returns_none():
    assert ev.compute_watch_velocity(pd.DataFrame(), "AAA", 30) is None


def test_compute_watch_velocity_missing_required_columns_returns_none():
    df = _df([{"ticker": "AAA", "composite_score": 60.0}])  # missing signal_date/signal_type
    assert ev.compute_watch_velocity(df, "AAA", 30) is None


def test_compute_watch_velocity_no_watch_rows_for_ticker_returns_none():
    df = _df([
        _row(ticker="AAA", signal_type="TRIM"),
        _row(ticker="BBB", signal_type="WATCH"),
    ])
    assert ev.compute_watch_velocity(df, "AAA", 30) is None


def test_compute_watch_velocity_no_rows_survive_date_coercion_returns_none():
    df = _df([
        _row(ticker="AAA", signal_date="not-a-date"),
        _row(ticker="AAA", signal_date="also-bad"),
    ])
    assert ev.compute_watch_velocity(df, "AAA", 30) is None


def test_compute_watch_velocity_fewer_than_2_rows_in_window_returns_none():
    df = _df([_row(ticker="AAA", signal_date="2026-01-15")])
    assert ev.compute_watch_velocity(df, "AAA", 30) is None


def test_compute_watch_velocity_rows_outside_lookback_window_excluded():
    # Two rows exist, but only one falls inside the lookback window relative
    # to the most recent date -> effectively < 2 rows in-window -> None.
    df = _df([
        _row(ticker="AAA", signal_date="2026-01-01", composite_score=70.0),
        _row(ticker="AAA", signal_date="2026-03-01", composite_score=60.0),
    ])
    assert ev.compute_watch_velocity(df, "AAA", lookback_days=10) is None


def test_compute_watch_velocity_nan_composite_score_on_oldest_returns_none():
    df = _df([
        _row(ticker="AAA", signal_date="2026-01-01", composite_score=float("nan")),
        _row(ticker="AAA", signal_date="2026-01-05", composite_score=60.0),
    ])
    assert ev.compute_watch_velocity(df, "AAA", 30) is None


def test_compute_watch_velocity_nan_composite_score_on_newest_returns_none():
    df = _df([
        _row(ticker="AAA", signal_date="2026-01-01", composite_score=60.0),
        _row(ticker="AAA", signal_date="2026-01-05", composite_score=float("nan")),
    ])
    assert ev.compute_watch_velocity(df, "AAA", 30) is None


# ─── compute_watch_velocity — happy path ────────────────────────────────────

def test_compute_watch_velocity_happy_path_deterioration():
    df = _df([
        _row(ticker="AAA", signal_date="2026-01-01", composite_score=65.234),
        _row(ticker="AAA", signal_date="2026-01-05", composite_score=61.5),
        _row(ticker="AAA", signal_date="2026-01-10", composite_score=58.987),
    ])
    result = ev.compute_watch_velocity(df, "AAA", lookback_days=30)
    assert result["ticker"] == "AAA"
    assert result["n_days"] == 3
    assert result["oldest_score"] == 65.2  # rounded to 1dp
    assert result["newest_score"] == 59.0  # rounded to 1dp
    assert result["delta"] == round(58.987 - 65.234, 2)
    assert result["oldest_date"] == "2026-01-01"
    assert result["newest_date"] == "2026-01-10"


def test_compute_watch_velocity_happy_path_improvement_positive_delta():
    df = _df([
        _row(ticker="AAA", signal_date="2026-01-01", composite_score=50.0),
        _row(ticker="AAA", signal_date="2026-01-10", composite_score=60.0),
    ])
    result = ev.compute_watch_velocity(df, "AAA", lookback_days=30)
    assert result["delta"] == 10.0


def test_compute_watch_velocity_ticker_case_insensitive():
    df = _df([
        _row(ticker="aaa", signal_date="2026-01-01", composite_score=50.0),
        _row(ticker="AaA", signal_date="2026-01-10", composite_score=40.0),
    ])
    result = ev.compute_watch_velocity(df, "AAA", lookback_days=30)
    assert result is not None
    assert result["ticker"] == "AAA"
    assert result["delta"] == -10.0


# ─── find_accelerating_watches ───────────────────────────────────────────────

def test_find_accelerating_watches_skips_ticker_with_none_velocity():
    df = _df([_row(ticker="AAA", signal_date="2026-01-01")])  # only 1 row -> velocity None
    result = ev.find_accelerating_watches(df, ["AAA", "BBB"], lookback_days=30, drop_threshold=5)
    assert result == []


def test_find_accelerating_watches_boundary_exactly_at_threshold_qualifies():
    df = _df([
        _row(ticker="AAA", signal_date="2026-01-01", composite_score=70.0),
        _row(ticker="AAA", signal_date="2026-01-10", composite_score=62.0),  # delta == -8.0
    ])
    result = ev.find_accelerating_watches(df, ["AAA"], lookback_days=30, drop_threshold=8)
    assert len(result) == 1
    assert result[0]["delta"] == -8.0


def test_find_accelerating_watches_just_inside_threshold_excluded():
    df = _df([
        _row(ticker="AAA", signal_date="2026-01-01", composite_score=70.0),
        _row(ticker="AAA", signal_date="2026-01-10", composite_score=62.1),  # delta == -7.9
    ])
    result = ev.find_accelerating_watches(df, ["AAA"], lookback_days=30, drop_threshold=8)
    assert result == []


def test_find_accelerating_watches_sort_order_most_deteriorated_first():
    df = _df([
        _row(ticker="AAA", signal_date="2026-01-01", composite_score=70.0),
        _row(ticker="AAA", signal_date="2026-01-10", composite_score=60.0),  # delta -10
        _row(ticker="BBB", signal_date="2026-01-01", composite_score=70.0),
        _row(ticker="BBB", signal_date="2026-01-10", composite_score=50.0),  # delta -20
    ])
    result = ev.find_accelerating_watches(df, ["AAA", "BBB"], lookback_days=30, drop_threshold=5)
    tickers = [r["ticker"] for r in result]
    assert tickers == ["BBB", "AAA"]  # -20 (more deteriorated) before -10
