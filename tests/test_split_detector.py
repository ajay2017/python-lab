"""Tests for stock_analyzer/split_detector.py — reconciles a stored
avg_cost/shares position against yfinance's split history when the two look
out of sync (e.g. a forward/reverse split that never got applied to the
locally-tracked cost basis). Pure decision logic once `fetch_splits` (the
one real I/O boundary, yfinance's `Ticker(...).splits`) is mocked — done
below by replacing the module's own `fetch_splits` reference directly, per
the module docstring's own recommendation over fighting the yfinance
Ticker/splits API shape. Real constants (from the module itself, not
constants.py — this module keeps its own thresholds):
_LOOKBACK_DAYS=730, _MIN_DISTORTION=0.35, _MAX_ADJ_DISTANCE=0.60.
"""
import datetime as _dt

import pandas as pd
import pytest

from stock_analyzer import split_detector as sd


# ─── cumulative_ratio ────────────────────────────────────────────────────────

def test_cumulative_ratio_empty_series_is_one():
    assert sd.cumulative_ratio(pd.Series([], dtype=float)) == 1.0


def test_cumulative_ratio_two_forward_splits_multiplies():
    idx = pd.date_range("2023-01-01", periods=2, freq="365D")
    splits = pd.Series([2.0, 2.0], index=idx)
    assert sd.cumulative_ratio(splits) == pytest.approx(4.0)


def test_cumulative_ratio_reverse_split():
    idx = pd.date_range("2023-01-01", periods=1, freq="D")
    splits = pd.Series([0.1], index=idx)
    assert sd.cumulative_ratio(splits) == pytest.approx(0.1)


# ─── detect_split_adjustment — non-positive input guards ────────────────────

def test_detect_split_adjustment_non_positive_current_price_returns_none(monkeypatch):
    monkeypatch.setattr(sd, "fetch_splits", lambda *a, **k: pd.Series(dtype=float))
    assert sd.detect_split_adjustment("AAA", 10, 100.0, 0.0) is None
    assert sd.detect_split_adjustment("AAA", 10, 100.0, -5.0) is None


def test_detect_split_adjustment_non_positive_avg_cost_returns_none(monkeypatch):
    monkeypatch.setattr(sd, "fetch_splits", lambda *a, **k: pd.Series(dtype=float))
    assert sd.detect_split_adjustment("AAA", 10, 0.0, 100.0) is None
    assert sd.detect_split_adjustment("AAA", 10, -1.0, 100.0) is None


def test_detect_split_adjustment_non_positive_shares_returns_none(monkeypatch):
    monkeypatch.setattr(sd, "fetch_splits", lambda *a, **k: pd.Series(dtype=float))
    assert sd.detect_split_adjustment("AAA", 0, 100.0, 100.0) is None
    assert sd.detect_split_adjustment("AAA", -5, 100.0, 100.0) is None


# ─── detect_split_adjustment — distortion guard at the 35% boundary ─────────
# abs(orig_pnl_pct)/100 < 0.35 skips. avg_cost=100 -> exactly 35% distortion
# needs current_price=135.0; just below needs 134.99.

def test_detect_split_adjustment_distortion_just_below_35pct_skips(monkeypatch):
    calls = []
    monkeypatch.setattr(sd, "fetch_splits", lambda *a, **k: calls.append(1) or pd.Series(dtype=float))
    result = sd.detect_split_adjustment("AAA", 10, 100.0, 134.99)
    assert result is None
    assert calls == []  # never even reached fetch_splits


def test_detect_split_adjustment_distortion_at_exactly_35pct_does_not_skip(monkeypatch):
    # Not skipped by the guard -- proceeds to fetch_splits and, with a ratio
    # that plausibly explains the gap, produces a real adjustment.
    idx = pd.DatetimeIndex(["2024-06-01"], tz="UTC")
    monkeypatch.setattr(sd, "fetch_splits", lambda *a, **k: pd.Series([1.35], index=idx))
    result = sd.detect_split_adjustment("AAA", 10, 100.0, 135.0)
    assert result is not None


# ─── detect_split_adjustment — empty splits / no meaningful ratio ───────────

def test_detect_split_adjustment_empty_splits_returns_none(monkeypatch):
    monkeypatch.setattr(sd, "fetch_splits", lambda *a, **k: pd.Series(dtype=float))
    result = sd.detect_split_adjustment("AAA", 10, 100.0, 200.0)
    assert result is None


def test_detect_split_adjustment_ratio_within_1pct_of_one_returns_none(monkeypatch):
    idx = pd.DatetimeIndex(["2024-06-01"], tz="UTC")
    monkeypatch.setattr(sd, "fetch_splits", lambda *a, **k: pd.Series([1.005], index=idx))
    result = sd.detect_split_adjustment("AAA", 10, 100.0, 200.0)
    assert result is None


# ─── detect_split_adjustment — validation rejection (adj_dist > 60%) ───────

def test_detect_split_adjustment_adj_distance_too_far_rejected(monkeypatch):
    # avg_cost=100, current=1000 (huge distortion); a 2:1 ratio doesn't
    # remotely explain a 10x price move -> adj_cost=50, adj_dist ~0.95 > 0.60.
    idx = pd.DatetimeIndex(["2024-06-01"], tz="UTC")
    monkeypatch.setattr(sd, "fetch_splits", lambda *a, **k: pd.Series([2.0], index=idx))
    result = sd.detect_split_adjustment("AAA", 10, 100.0, 1000.0)
    assert result is None


# ─── detect_split_adjustment — valid forward split ──────────────────────────

def test_detect_split_adjustment_valid_forward_split(monkeypatch):
    idx = pd.DatetimeIndex(["2024-03-15"], tz="UTC")
    monkeypatch.setattr(sd, "fetch_splits", lambda *a, **k: pd.Series([4.0], index=idx))

    result = sd.detect_split_adjustment("AAA", 10, 400.0, 100.0)

    assert result is not None
    assert result["split_type"] == "Forward"
    assert result["ratio_str"] == "4:1"
    assert result["adj_shares"] == pytest.approx(40.0)
    assert result["adj_avg_cost"] == pytest.approx(100.0)
    assert result["split_date"] == _dt.date(2024, 3, 15)


# ─── detect_split_adjustment — valid reverse split ──────────────────────────

def test_detect_split_adjustment_valid_reverse_split(monkeypatch):
    idx = pd.DatetimeIndex(["2024-05-01"], tz="UTC")
    monkeypatch.setattr(sd, "fetch_splits", lambda *a, **k: pd.Series([0.1], index=idx))
    result = sd.detect_split_adjustment("AAA", 100, 1.0, 10.0)

    assert result is not None
    assert result["split_type"] == "Reverse"
    assert result["ratio_str"] == "1:10"
    assert result["adj_shares"] == pytest.approx(10.0)
    assert result["adj_avg_cost"] == pytest.approx(10.0)


# ─── detect_portfolio_splits — empty/None holdings ──────────────────────────

def test_detect_portfolio_splits_none_holdings_returns_empty_list():
    assert sd.detect_portfolio_splits(None, {}) == []


def test_detect_portfolio_splits_empty_holdings_returns_empty_list():
    assert sd.detect_portfolio_splits(pd.DataFrame(), {}) == []


# ─── detect_portfolio_splits — skips non-positive rows ──────────────────────

def test_detect_portfolio_splits_skips_non_positive_rows(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not be called for a skipped row")
    monkeypatch.setattr(sd, "detect_split_adjustment", boom)

    holdings = pd.DataFrame({
        "Ticker":       ["AAA", "BBB", "CCC"],
        "Shares":       [0, 10, 10],
        "Avg Cost ($)": [100.0, 0.0, 100.0],
    })
    live_prices = {"AAA": {"price": 50.0}, "BBB": {"price": 50.0}, "CCC": {"price": 0.0}}
    result = sd.detect_portfolio_splits(holdings, live_prices)
    assert result == []


# ─── detect_portfolio_splits — dismissed filtering ──────────────────────────

def test_detect_portfolio_splits_dismissed_key_filters_out_match(monkeypatch):
    fixed_adj = {
        "ticker": "AAA", "split_ratio": 4.0, "ratio_str": "4:1",
        "split_date": _dt.date(2024, 3, 15), "split_type": "Forward",
        "orig_shares": 10, "orig_avg_cost": 400.0, "orig_pnl_pct": -75.0,
        "adj_shares": 40.0, "adj_avg_cost": 100.0, "adj_pnl_pct": 0.0,
        "current_price": 100.0,
    }
    monkeypatch.setattr(sd, "detect_split_adjustment", lambda *a, **k: dict(fixed_adj))

    holdings = pd.DataFrame({
        "Ticker":       ["AAA"],
        "Shares":       [10],
        "Avg Cost ($)": [400.0],
    })
    live_prices = {"AAA": {"price": 100.0}}

    dismissed = {"AAA_2024-03-15"}
    result = sd.detect_portfolio_splits(holdings, live_prices, dismissed=dismissed)
    assert result == []

    result_no_dismiss = sd.detect_portfolio_splits(holdings, live_prices, dismissed=set())
    assert len(result_no_dismiss) == 1
    assert result_no_dismiss[0]["ticker"] == "AAA"
