"""Tests for stock_analyzer/behavioral_fingerprint.py — Concept A (F-193)
observation-only behavioral pattern functions (My Edge 4th tab). Every
pattern function is sample-gated (returns None below min_n) and pure
computation — no Streamlit, no DB, no fetches. Previously zero test coverage.
"""
import datetime as dt

import pandas as pd
import pytest

from stock_analyzer import behavioral_fingerprint as bf


# ─── momentum_recency_pattern — builders ─────────────────────────────────────

def _bucket(score, n, n_acted):
    acted_flags = [True] * n_acted + [False] * (n - n_acted)
    return [{"momentum_score": score, "acted_on": a} for a in acted_flags]


# ─── momentum_recency_pattern — empty / gating ───────────────────────────────

def test_momentum_recency_pattern_empty_matched_returns_none():
    assert bf.momentum_recency_pattern([], min_n=3) is None


def test_momentum_recency_pattern_none_matched_returns_none():
    assert bf.momentum_recency_pattern(None, min_n=3) is None


def test_momentum_recency_pattern_below_min_n_times_2_returns_none():
    # min_n=10 requires >=20 scored rows total; only 15 supplied.
    matched = [{"momentum_score": i, "acted_on": True} for i in range(15)]
    assert bf.momentum_recency_pattern(matched, min_n=10) is None


def test_momentum_recency_pattern_lopsided_median_split_below_min_n_returns_none():
    # 6 identical momentum_score rows -> median == that value -> the high
    # bucket (score >= median) gets all 6, the low bucket (score < median)
    # gets 0 -- passes the total >= min_n*2 gate but fails the per-bucket gate.
    matched = [{"momentum_score": 5.0, "acted_on": True} for _ in range(6)]
    assert bf.momentum_recency_pattern(matched, min_n=3) is None


# ─── momentum_recency_pattern — median-split correctness ─────────────────────

def test_momentum_recency_pattern_even_length_median_averages_two_middle_values():
    # 200 rows at score=1 + 200 rows at score=2 (400 total, even) -> median
    # = avg(scored_sorted[199], scored_sorted[200]) = avg(1, 2) = 1.5, so the
    # split is a clean 200/200 by construction.
    low = _bucket(1, 200, n_acted=100)   # 50.0%
    high = _bucket(2, 200, n_acted=110)  # 55.0%
    result = bf.momentum_recency_pattern(low + high, min_n=50)
    assert result["low"]["n"] == 200
    assert result["high"]["n"] == 200
    assert result["low"]["action_rate"] == pytest.approx(50.0)
    assert result["high"]["action_rate"] == pytest.approx(55.0)


def test_momentum_recency_pattern_odd_length_median_is_middle_value():
    # 5 distinct scores [1,2,3,4,5] (odd) -> median = scored_sorted[2] = 3.
    # high = score>=3 -> {3,4,5} (3 items), low = score<3 -> {1,2} (2 items).
    matched = [
        {"momentum_score": 1, "acted_on": False},
        {"momentum_score": 2, "acted_on": False},
        {"momentum_score": 3, "acted_on": True},
        {"momentum_score": 4, "acted_on": True},
        {"momentum_score": 5, "acted_on": True},
    ]
    result = bf.momentum_recency_pattern(matched, min_n=2)
    assert result["high"]["n"] == 3
    assert result["low"]["n"] == 2


# ─── momentum_recency_pattern — direction boundary at meaningful_delta_pp ────

def test_momentum_recency_pattern_delta_exactly_at_boundary_is_flat():
    low = _bucket(1, 200, n_acted=100)   # 50.0%
    high = _bucket(2, 200, n_acted=110)  # 55.0% -> delta_pp == 5.0 exactly
    result = bf.momentum_recency_pattern(low + high, min_n=50, meaningful_delta_pp=5.0)
    assert result["delta_pp"] == pytest.approx(5.0)
    assert result["direction"] == "flat"


def test_momentum_recency_pattern_delta_above_boundary_is_chases():
    low = _bucket(1, 200, n_acted=100)   # 50.0%
    high = _bucket(2, 200, n_acted=111)  # 55.5% -> delta_pp == 5.5
    result = bf.momentum_recency_pattern(low + high, min_n=50, meaningful_delta_pp=5.0)
    assert result["direction"] == "chases"


def test_momentum_recency_pattern_delta_below_negative_boundary_is_fades():
    low = _bucket(1, 200, n_acted=111)   # 55.5%
    high = _bucket(2, 200, n_acted=100)  # 50.0% -> delta_pp == -5.5
    result = bf.momentum_recency_pattern(low + high, min_n=50, meaningful_delta_pp=5.0)
    assert result["direction"] == "fades"


# ─── conviction_tier_pattern ──────────────────────────────────────────────────

def _conviction_bucket(score, n, n_acted):
    acted_flags = [True] * n_acted + [False] * (n - n_acted)
    return [{"composite_score": score, "acted_on": a} for a in acted_flags]


def test_conviction_tier_pattern_empty_matched_returns_none():
    assert bf.conviction_tier_pattern([], strong_buy_floor=75, min_n=3) is None


def test_conviction_tier_pattern_either_bucket_below_min_n_returns_none():
    strong = _conviction_bucket(80, 2, 1)
    buy = _conviction_bucket(60, 2, 1)
    assert bf.conviction_tier_pattern(strong + buy, strong_buy_floor=75, min_n=3) is None


def test_conviction_tier_pattern_score_exactly_at_floor_is_strong_bucket():
    strong = _conviction_bucket(75, 5, 3)  # == floor -> strong (>=)
    buy = _conviction_bucket(60, 5, 1)
    result = bf.conviction_tier_pattern(strong + buy, strong_buy_floor=75, min_n=3)
    assert result["strong_buy"]["n"] == 5
    assert result["buy"]["n"] == 5


def test_conviction_tier_pattern_score_one_below_floor_is_buy_bucket():
    strong = _conviction_bucket(75, 5, 3)
    buy = _conviction_bucket(74, 5, 1)  # one below floor -> buy bucket
    result = bf.conviction_tier_pattern(strong + buy, strong_buy_floor=75, min_n=3)
    assert result["buy"]["n"] == 5
    assert result["delta_pp"] == pytest.approx(result["strong_buy"]["action_rate"] - result["buy"]["action_rate"])


# ─── opening_window_pattern ───────────────────────────────────────────────────

def test_opening_window_pattern_empty_enriched_returns_none():
    assert bf.opening_window_pattern([], opening_window_min=30, min_n=3) is None


def test_opening_window_pattern_time_object_and_tuple_both_work():
    rows_time_obj = [{"et_time": dt.time(9, 35), "alpha_pct": 1.0} for _ in range(5)]
    rows_tuple = [{"et_time": (9, 35), "alpha_pct": 1.0} for _ in range(5)]
    later_rows = [{"et_time": dt.time(11, 0), "alpha_pct": 0.5} for _ in range(5)]

    r1 = bf.opening_window_pattern(rows_time_obj + later_rows, opening_window_min=30, min_n=3)
    r2 = bf.opening_window_pattern(rows_tuple + later_rows, opening_window_min=30, min_n=3)
    assert r1["opening"]["n"] == 5
    assert r2["opening"]["n"] == 5


def test_opening_window_pattern_missing_et_time_dropped():
    rows = [{"et_time": dt.time(9, 35), "alpha_pct": 1.0} for _ in range(5)]
    rows += [{"alpha_pct": 1.0} for _ in range(3)]  # missing et_time -- dropped
    later = [{"et_time": dt.time(11, 0), "alpha_pct": 0.5} for _ in range(5)]
    result = bf.opening_window_pattern(rows + later, opening_window_min=30, min_n=3)
    assert result["opening"]["n"] == 5  # the 3 missing-et_time rows not counted


def test_opening_window_pattern_unresolvable_alpha_dropped():
    rows = [{"et_time": dt.time(9, 35), "alpha_pct": 1.0} for _ in range(5)]
    rows += [{"et_time": dt.time(9, 40), "alpha_pct": None} for _ in range(3)]  # dropped
    later = [{"et_time": dt.time(11, 0), "alpha_pct": 0.5} for _ in range(5)]
    result = bf.opening_window_pattern(rows + later, opening_window_min=30, min_n=3)
    assert result["opening"]["n"] == 5


def test_opening_window_pattern_boundary_exactly_at_window_is_later_not_opening():
    # mins == opening_window_min fails `0 <= mins < opening_window_min` -> "later".
    at_boundary = [{"et_time": dt.time(10, 0), "alpha_pct": 1.0} for _ in range(5)]  # mins=30
    opening = [{"et_time": dt.time(9, 35), "alpha_pct": 1.0} for _ in range(5)]      # mins=5
    result = bf.opening_window_pattern(at_boundary + opening, opening_window_min=30, min_n=3)
    assert result["opening"]["n"] == 5
    assert result["later"]["n"] == 5


def test_opening_window_pattern_below_min_n_in_either_bucket_returns_none():
    opening = [{"et_time": dt.time(9, 35), "alpha_pct": 1.0} for _ in range(2)]
    later = [{"et_time": dt.time(11, 0), "alpha_pct": 0.5} for _ in range(5)]
    assert bf.opening_window_pattern(opening + later, opening_window_min=30, min_n=3) is None


# ─── signal_response_rate_pattern / signal_lag_pattern — shared builders ────

def _exit_signals(rows):
    return pd.DataFrame(rows, columns=["ticker", "signal_date", "signal_type"])


def _trades(rows):
    return pd.DataFrame(rows, columns=["ticker", "action", "traded_at"])


# ─── signal_response_rate_pattern ────────────────────────────────────────────

def test_signal_response_rate_pattern_none_exit_signals_returns_none():
    assert bf.signal_response_rate_pattern(None, _trades([]), act_window_days=3, min_n=1) is None


def test_signal_response_rate_pattern_empty_trades_returns_none():
    signals = _exit_signals([{"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "WATCH"}])
    assert bf.signal_response_rate_pattern(signals, _trades([]), act_window_days=3, min_n=1) is None


def test_signal_response_rate_pattern_sell_exactly_at_window_boundary_counts():
    signals = _exit_signals([
        {"ticker": t, "signal_date": "2024-01-01", "signal_type": "WATCH"}
        for t in ("AAA", "BBB", "CCC")
    ])
    trades = _trades([{"ticker": "AAA", "action": "SELL", "traded_at": "2024-01-04"}])  # +3 days
    result = bf.signal_response_rate_pattern(signals, trades, act_window_days=3, min_n=3)
    assert result["WATCH"]["n_acted"] == 1


def test_signal_response_rate_pattern_sell_one_day_beyond_window_does_not_count():
    signals = _exit_signals([
        {"ticker": t, "signal_date": "2024-01-01", "signal_type": "WATCH"}
        for t in ("AAA", "BBB", "CCC")
    ])
    trades = _trades([{"ticker": "AAA", "action": "SELL", "traded_at": "2024-01-05"}])  # +4 days
    result = bf.signal_response_rate_pattern(signals, trades, act_window_days=3, min_n=3)
    assert result["WATCH"]["n_acted"] == 0


def test_signal_response_rate_pattern_grouped_independently_by_signal_type():
    signals = _exit_signals(
        [{"ticker": t, "signal_date": "2024-01-01", "signal_type": "WATCH"} for t in ("AAA", "BBB", "CCC")]
        + [{"ticker": t, "signal_date": "2024-01-01", "signal_type": "TRIM"} for t in ("DDD", "EEE", "FFF")]
    )
    trades = _trades([
        {"ticker": "AAA", "action": "SELL", "traded_at": "2024-01-02"},
        {"ticker": "DDD", "action": "SELL", "traded_at": "2024-01-10"},  # outside window
    ])
    result = bf.signal_response_rate_pattern(signals, trades, act_window_days=3, min_n=3)
    assert result["WATCH"]["n_acted"] == 1
    assert result["TRIM"]["n_acted"] == 0


def test_signal_response_rate_pattern_type_below_min_n_excluded_others_kept():
    signals = _exit_signals(
        [{"ticker": t, "signal_date": "2024-01-01", "signal_type": "WATCH"}
         for t in ("AAA", "BBB", "CCC", "DDD", "EEE")]
        + [{"ticker": "FFF", "signal_date": "2024-01-01", "signal_type": "EXIT"}]
    )
    trades = _trades([{"ticker": "ZZZ", "action": "SELL", "traded_at": "2024-06-01"}])  # unrelated filler
    result = bf.signal_response_rate_pattern(signals, trades, act_window_days=3, min_n=3)
    assert "WATCH" in result
    assert "EXIT" not in result


def test_signal_response_rate_pattern_all_types_below_min_n_returns_none():
    signals = _exit_signals([{"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "WATCH"}])
    trades = _trades([{"ticker": "ZZZ", "action": "SELL", "traded_at": "2024-06-01"}])  # unrelated filler
    result = bf.signal_response_rate_pattern(signals, trades, act_window_days=3, min_n=5)
    assert result is None


# ─── signal_lag_pattern ───────────────────────────────────────────────────────

def test_signal_lag_pattern_none_inputs_return_none():
    assert bf.signal_lag_pattern(None, _trades([]), act_window_days=5, min_n=1) is None
    signals = _exit_signals([{"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "WATCH"}])
    assert bf.signal_lag_pattern(signals, None, act_window_days=5, min_n=1) is None


def test_signal_lag_pattern_median_odd_and_even():
    # 3 WATCH signals on ticker AAA with lags 1,2,3 (odd count -> median=2)
    signals = _exit_signals([
        {"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "WATCH"},
        {"ticker": "BBB", "signal_date": "2024-01-01", "signal_type": "WATCH"},
        {"ticker": "CCC", "signal_date": "2024-01-01", "signal_type": "WATCH"},
    ])
    trades = _trades([
        {"ticker": "AAA", "action": "SELL", "traded_at": "2024-01-02"},  # lag 1
        {"ticker": "BBB", "action": "SELL", "traded_at": "2024-01-03"},  # lag 2
        {"ticker": "CCC", "action": "SELL", "traded_at": "2024-01-04"},  # lag 3
    ])
    result = bf.signal_lag_pattern(signals, trades, act_window_days=5, min_n=3)
    assert result["WATCH"]["median_lag_days"] == pytest.approx(2.0)


def test_signal_lag_pattern_pct_acted_day1_boundary_lag1_counts_lag2_does_not():
    signals = _exit_signals([
        {"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "WATCH"},
        {"ticker": "BBB", "signal_date": "2024-01-01", "signal_type": "WATCH"},
    ])
    trades = _trades([
        {"ticker": "AAA", "action": "SELL", "traded_at": "2024-01-02"},  # lag 1 -- counts
        {"ticker": "BBB", "action": "SELL", "traded_at": "2024-01-03"},  # lag 2 -- doesn't
    ])
    result = bf.signal_lag_pattern(signals, trades, act_window_days=5, min_n=2)
    assert result["WATCH"]["pct_acted_day1"] == pytest.approx(50.0)


def test_signal_lag_pattern_earliest_matching_sell_used_not_a_later_one():
    # Two sells on the same ticker inside the window -- the EARLIEST one's
    # lag must be used (loop iterates dates ascending, breaks on first match).
    signals = _exit_signals([{"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "WATCH"}])
    trades = _trades([
        {"ticker": "AAA", "action": "SELL", "traded_at": "2024-01-03"},  # lag 2 (earlier)
        {"ticker": "AAA", "action": "SELL", "traded_at": "2024-01-06"},  # lag 5 (later)
    ])
    result = bf.signal_lag_pattern(signals, trades, act_window_days=10, min_n=1)
    assert result["WATCH"]["median_lag_days"] == pytest.approx(2.0)


# ─── escalation_ignored_pattern ──────────────────────────────────────────────

def test_escalation_ignored_pattern_fewer_than_2_rows_returns_none():
    signals = _exit_signals([{"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "WATCH"}])
    assert bf.escalation_ignored_pattern(signals, _trades([]), act_window_days=5, min_n=1) is None


def test_escalation_ignored_pattern_watch_trim_exit_sequence_counts_3_pairs():
    # WATCH -> TRIM -> EXIT on one ticker = 3 pairs (WATCH-TRIM, WATCH-EXIT,
    # TRIM-EXIT), all valid escalations per the pair-weighted counting rule.
    signals = _exit_signals([
        {"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "WATCH"},
        {"ticker": "AAA", "signal_date": "2024-01-05", "signal_type": "TRIM"},
        {"ticker": "AAA", "signal_date": "2024-01-10", "signal_type": "EXIT"},
    ])
    result = bf.escalation_ignored_pattern(signals, _trades([]), act_window_days=5, min_n=1)
    assert result["n_escalations"] == 3


def test_escalation_ignored_pattern_downgrade_and_same_tier_are_not_escalations():
    # TRIM -> WATCH (downgrade) and a repeated WATCH -> WATCH (same tier) are
    # NOT counted as escalations.
    signals = _exit_signals([
        {"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "TRIM"},
        {"ticker": "AAA", "signal_date": "2024-01-05", "signal_type": "WATCH"},
        {"ticker": "AAA", "signal_date": "2024-01-10", "signal_type": "WATCH"},
    ])
    result = bf.escalation_ignored_pattern(signals, _trades([]), act_window_days=5, min_n=0)
    assert result is None or result["n_escalations"] == 0


def test_escalation_ignored_pattern_sell_between_dates_marks_not_ignored():
    signals = _exit_signals([
        {"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "WATCH"},
        {"ticker": "AAA", "signal_date": "2024-01-10", "signal_type": "TRIM"},
    ])
    trades = _trades([{"ticker": "AAA", "action": "SELL", "traded_at": "2024-01-05"}])
    result = bf.escalation_ignored_pattern(signals, trades, act_window_days=5, min_n=1)
    assert result["n_escalations"] == 1
    assert result["n_ignored"] == 0


def test_escalation_ignored_pattern_sell_on_exact_endpoint_dates_marks_not_ignored():
    # `earlier_date <= sd <= later_date` -- inclusive of both endpoints.
    signals = _exit_signals([
        {"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "WATCH"},
        {"ticker": "AAA", "signal_date": "2024-01-10", "signal_type": "TRIM"},
    ])
    trades = _trades([{"ticker": "AAA", "action": "SELL", "traded_at": "2024-01-10"}])  # == later_date
    result = bf.escalation_ignored_pattern(signals, trades, act_window_days=5, min_n=1)
    assert result["n_ignored"] == 0


def test_escalation_ignored_pattern_no_sell_between_dates_is_ignored():
    signals = _exit_signals([
        {"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "WATCH"},
        {"ticker": "AAA", "signal_date": "2024-01-10", "signal_type": "TRIM"},
    ])
    result = bf.escalation_ignored_pattern(signals, _trades([]), act_window_days=5, min_n=1)
    assert result["n_ignored"] == 1
    assert result["ignored_rate"] == pytest.approx(1.0)


def test_escalation_ignored_pattern_total_below_min_n_returns_none():
    signals = _exit_signals([
        {"ticker": "AAA", "signal_date": "2024-01-01", "signal_type": "WATCH"},
        {"ticker": "AAA", "signal_date": "2024-01-10", "signal_type": "TRIM"},
    ])
    assert bf.escalation_ignored_pattern(signals, _trades([]), act_window_days=5, min_n=5) is None
