"""
Tests for scripts/deterioration_flicker_scan.py's pure helpers -- the
timeline/run/gap composition that measures ACTIVE-CLEAR-ACTIVE round trips in
exit_signals history. No DB/network dependency, matching
tests/test_exit_early_cost_analysis.py's own convention for a `scripts/`
module with genuinely new pure logic worth unit coverage.
"""

from datetime import date

import pytest

import scripts.deterioration_flicker_scan as dfs

pytestmark = pytest.mark.fast


def _d(s: str) -> date:
    return date.fromisoformat(s)


# ─── build_tier_timelines ────────────────────────────────────────────────────

def test_build_tier_timelines_restricts_to_score_history_coverage():
    """An exit_signals row on a date with NO score_history row for that
    ticker must not appear on the timeline at all -- that date is
    unconfirmed, not "clear"."""
    signals = [{"ticker": "MU", "signal_date": "2026-01-05", "signal_type": "WATCH"}]
    score_history = [{"ticker": "MU", "score_date": "2026-01-06"}]  # different day
    timelines = dfs.build_tier_timelines(signals, score_history)
    assert timelines["MU"] == [(_d("2026-01-06"), None)]


def test_build_tier_timelines_marks_confirmed_clear_days_none():
    signals = []
    score_history = [
        {"ticker": "MU", "score_date": "2026-01-05"},
        {"ticker": "MU", "score_date": "2026-01-06"},
    ]
    timelines = dfs.build_tier_timelines(signals, score_history)
    assert timelines["MU"] == [(_d("2026-01-05"), None), (_d("2026-01-06"), None)]


def test_build_tier_timelines_takes_highest_severity_same_day():
    signals = [
        {"ticker": "MU", "signal_date": "2026-01-05", "signal_type": "WATCH"},
        {"ticker": "MU", "signal_date": "2026-01-05", "signal_type": "EXIT"},
    ]
    score_history = [{"ticker": "MU", "score_date": "2026-01-05"}]
    timelines = dfs.build_tier_timelines(signals, score_history)
    assert timelines["MU"] == [(_d("2026-01-05"), "EXIT")]


def test_build_tier_timelines_ignores_risk_off():
    signals = [{"ticker": "MU", "signal_date": "2026-01-05", "signal_type": "RISK_OFF"}]
    score_history = [{"ticker": "MU", "score_date": "2026-01-05"}]
    timelines = dfs.build_tier_timelines(signals, score_history)
    assert timelines["MU"] == [(_d("2026-01-05"), None)]


def test_build_tier_timelines_multiple_tickers_independent():
    signals = [{"ticker": "MU", "signal_date": "2026-01-05", "signal_type": "TRIM"}]
    score_history = [
        {"ticker": "MU", "score_date": "2026-01-05"},
        {"ticker": "AAPL", "score_date": "2026-01-05"},
    ]
    timelines = dfs.build_tier_timelines(signals, score_history)
    assert timelines["MU"] == [(_d("2026-01-05"), "TRIM")]
    assert timelines["AAPL"] == [(_d("2026-01-05"), None)]


# ─── runs_from_timeline ──────────────────────────────────────────────────────

def test_runs_from_timeline_collapses_consecutive_same_state():
    timeline = [
        (_d("2026-01-05"), "WATCH"), (_d("2026-01-06"), "WATCH"),
        (_d("2026-01-07"), None),
        (_d("2026-01-08"), "WATCH"),
    ]
    runs = dfs.runs_from_timeline(timeline)
    assert runs == [
        {"state": "WATCH", "start": _d("2026-01-05"), "end": _d("2026-01-06"), "n_days": 2},
        {"state": None, "start": _d("2026-01-07"), "end": _d("2026-01-07"), "n_days": 1},
        {"state": "WATCH", "start": _d("2026-01-08"), "end": _d("2026-01-08"), "n_days": 1},
    ]


def test_runs_from_timeline_empty_input():
    assert dfs.runs_from_timeline([]) == []


# ─── gaps_from_runs ──────────────────────────────────────────────────────────

def test_gaps_from_runs_detects_same_tier_round_trip():
    runs = [
        {"state": "WATCH", "start": _d("2026-01-05"), "end": _d("2026-01-05"), "n_days": 1},
        {"state": None, "start": _d("2026-01-06"), "end": _d("2026-01-06"), "n_days": 1},
        {"state": "WATCH", "start": _d("2026-01-07"), "end": _d("2026-01-07"), "n_days": 1},
    ]
    gaps = dfs.gaps_from_runs("MU", runs)
    assert len(gaps) == 1
    g = gaps[0]
    assert g["same_tier"] is True
    assert g["before_tier"] == "WATCH" and g["after_tier"] == "WATCH"
    assert g["gap_confirmed_days"] == 1


def test_gaps_from_runs_detects_cross_tier_round_trip():
    runs = [
        {"state": "WATCH", "start": _d("2026-01-05"), "end": _d("2026-01-05"), "n_days": 1},
        {"state": None, "start": _d("2026-01-06"), "end": _d("2026-01-06"), "n_days": 1},
        {"state": "TRIM", "start": _d("2026-01-07"), "end": _d("2026-01-07"), "n_days": 1},
    ]
    gaps = dfs.gaps_from_runs("MU", runs)
    assert len(gaps) == 1
    assert gaps[0]["same_tier"] is False


def test_gaps_from_runs_ignores_leading_and_trailing_clear_runs():
    """A CLEAR run at either end of the timeline has no bounding ACTIVE run
    on one side -- must not be counted as a gap."""
    runs = [
        {"state": None, "start": _d("2026-01-01"), "end": _d("2026-01-01"), "n_days": 1},
        {"state": "WATCH", "start": _d("2026-01-02"), "end": _d("2026-01-02"), "n_days": 1},
        {"state": None, "start": _d("2026-01-03"), "end": _d("2026-01-03"), "n_days": 1},
    ]
    assert dfs.gaps_from_runs("MU", runs) == []


def test_gaps_from_runs_no_gaps_when_never_clears():
    runs = [{"state": "WATCH", "start": _d("2026-01-05"), "end": _d("2026-01-10"), "n_days": 4}]
    assert dfs.gaps_from_runs("MU", runs) == []


def test_gaps_from_runs_long_gap_from_sold_and_rebought_not_miscounted():
    """A months-long CLEAR span (e.g. position sold, later re-bought) is
    still technically a "gap" by this function's definition, but its
    n_days will be large -- summarize_gaps buckets it into the overflow
    bucket rather than the short-gap table, so it never LOOKS like flicker."""
    runs = [
        {"state": "EXIT", "start": _d("2026-01-05"), "end": _d("2026-01-05"), "n_days": 1},
        {"state": None, "start": _d("2026-01-06"), "end": _d("2026-06-01"), "n_days": 90},
        {"state": "WATCH", "start": _d("2026-06-02"), "end": _d("2026-06-02"), "n_days": 1},
    ]
    gaps = dfs.gaps_from_runs("MU", runs)
    assert len(gaps) == 1
    assert gaps[0]["gap_confirmed_days"] == 90


# ─── summarize_gaps ──────────────────────────────────────────────────────────

def test_summarize_gaps_buckets_by_length_and_splits_same_vs_cross_tier():
    gaps = [
        {"same_tier": True,  "gap_confirmed_days": 1},
        {"same_tier": True,  "gap_confirmed_days": 1},
        {"same_tier": False, "gap_confirmed_days": 2},
        {"same_tier": True,  "gap_confirmed_days": 10},
    ]
    summary = dfs.summarize_gaps(gaps, max_gap=3)
    assert summary["buckets"][1] == {"same_tier": 2, "cross_tier": 0}
    assert summary["buckets"][2] == {"same_tier": 0, "cross_tier": 1}
    assert summary["buckets"][3] == {"same_tier": 0, "cross_tier": 0}
    assert summary["overflow"] == {"same_tier": 1, "cross_tier": 0}


def test_summarize_gaps_empty_input():
    summary = dfs.summarize_gaps([], max_gap=3)
    assert summary["overflow"] == {"same_tier": 0, "cross_tier": 0}
    assert all(v == {"same_tier": 0, "cross_tier": 0} for v in summary["buckets"].values())
