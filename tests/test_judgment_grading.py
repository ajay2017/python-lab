"""
Tests for stock_analyzer/judgment_grading.py::track_record_summary() —
previously ZERO test coverage for this readout function despite feeding The
Judge page's own witness track record.

Covers the per-ticker episode-collapse fix (docs/plans/data-foundation-
strategy.md §2 A3 / Phase 1c): a witness opining daily on the same still-open
per-ticker position is graded once per day it stays open, so an uncollapsed
rollup counts one real standing opinion multiple times. Per-ticker dimensions
(momentum, quality — `ticker` is a real ticker) are collapsed to one
representative per (source, dimension, ticker), anchored at the EARLIEST
`signal_date`; portfolio-wide dimensions (`ticker == "_PORTFOLIO"`) are
DELIBERATELY left uncollapsed — a separate, later problem (pure overlapping-
window pseudo-replication, no ticker to key a collapse on).
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_analyzer import judgment_grading as jg

pytestmark = pytest.mark.fast


# ─── builders ───────────────────────────────────────────────────────────────

def _grade_row(source="engine", dimension="momentum", ticker="AAA",
               signal_date="2026-01-01", correct=True, realized_pct=1.0):
    return {
        "source": source,
        "dimension": dimension,
        "ticker": ticker,
        "signal_date": signal_date,
        "horizon_days": 5,
        "opinion_signal": 1.0,
        "realized_pct": realized_pct,
        "correct": correct,
        "graded_at": "2026-01-10T00:00:00+00:00",
    }


def _grades_df(rows):
    return pd.DataFrame(rows)


# ─── empty / guard cases ────────────────────────────────────────────────────

def test_track_record_summary_none_input_returns_empty():
    assert jg.track_record_summary(None, min_sample_n=1) == []


def test_track_record_summary_empty_df_returns_empty():
    assert jg.track_record_summary(pd.DataFrame(), min_sample_n=1) == []


def test_track_record_summary_all_correct_none_returns_empty():
    rows = [_grade_row(correct=None), _grade_row(ticker="BBB", correct=None)]
    assert jg.track_record_summary(_grades_df(rows), min_sample_n=1) == []


# ─── per-ticker collapse ────────────────────────────────────────────────────

def test_track_record_summary_collapses_repeated_daily_grades_to_one_trial_per_ticker():
    """One witness opining on the same ticker/dimension across 6 days, mixed
    correct values. Must count as ONE trial, and the surviving `correct`
    must be the EARLIEST signal_date row's value — constructed so the
    earliest day's value (False) differs from the 5/6 majority (True),
    ruling out a "collapses to the most common value" false-positive."""
    rows = [
        _grade_row(ticker="AAA", signal_date="2026-01-01", correct=False),  # earliest, minority
        _grade_row(ticker="AAA", signal_date="2026-01-02", correct=True),
        _grade_row(ticker="AAA", signal_date="2026-01-03", correct=True),
        _grade_row(ticker="AAA", signal_date="2026-01-04", correct=True),
        _grade_row(ticker="AAA", signal_date="2026-01-05", correct=True),
        _grade_row(ticker="AAA", signal_date="2026-01-06", correct=True),
    ]
    out = jg.track_record_summary(_grades_df(rows), min_sample_n=1)
    assert len(out) == 1
    row = out[0]
    assert row["n"] == 1
    assert row["n_correct"] == 0
    assert row["accuracy"] == 0.0   # earliest day (False), not the 5/6 majority (True)


def test_track_record_summary_distinct_tickers_each_survive_as_own_trial():
    """Two DIFFERENT tickers, each with a repeated daily grade, must yield
    TWO trials after collapse — the ticker key is load-bearing, not just
    (source, dimension)."""
    rows = [
        _grade_row(ticker="AAA", signal_date="2026-01-01", correct=True),
        _grade_row(ticker="AAA", signal_date="2026-01-02", correct=True),
        _grade_row(ticker="BBB", signal_date="2026-01-01", correct=False),
        _grade_row(ticker="BBB", signal_date="2026-01-02", correct=False),
    ]
    out = jg.track_record_summary(_grades_df(rows), min_sample_n=1)
    assert len(out) == 1   # one (source, dimension) rollup row
    assert out[0]["n"] == 2   # AAA + BBB, each collapsed to 1
    assert out[0]["n_correct"] == 1   # AAA=True, BBB=False


# ─── portfolio-wide rows: deliberately NOT collapsed ────────────────────────

def test_track_record_summary_portfolio_wide_rows_never_collapsed():
    """Two _PORTFOLIO-keyed rows for the same (source, dimension) on
    different dates must BOTH survive into the rollup's n."""
    rows = [
        _grade_row(ticker="_PORTFOLIO", dimension="concentration",
                   signal_date="2026-01-01", correct=True),
        _grade_row(ticker="_PORTFOLIO", dimension="concentration",
                   signal_date="2026-01-15", correct=False),
    ]
    out = jg.track_record_summary(_grades_df(rows), min_sample_n=1)
    assert len(out) == 1
    assert out[0]["n"] == 2
    assert out[0]["n_correct"] == 1


def test_track_record_summary_mixed_frame_no_cross_contamination():
    """A frame mixing per-ticker momentum rows and _PORTFOLIO concentration
    rows must collapse/pass-through correctly into their OWN separate
    (source, dimension) rollup rows, with no leakage between them."""
    rows = [
        # per-ticker momentum: 3 daily rows for one ticker -> collapses to 1
        _grade_row(ticker="AAA", dimension="momentum", signal_date="2026-01-01", correct=True),
        _grade_row(ticker="AAA", dimension="momentum", signal_date="2026-01-02", correct=True),
        _grade_row(ticker="AAA", dimension="momentum", signal_date="2026-01-03", correct=True),
        # portfolio-wide concentration: 2 rows -> both survive
        _grade_row(ticker="_PORTFOLIO", dimension="concentration",
                   signal_date="2026-01-01", correct=True),
        _grade_row(ticker="_PORTFOLIO", dimension="concentration",
                   signal_date="2026-01-02", correct=False),
    ]
    out = jg.track_record_summary(_grades_df(rows), min_sample_n=1)
    by_dim = {r["dimension"]: r for r in out}
    assert set(by_dim) == {"momentum", "concentration"}
    assert by_dim["momentum"]["n"] == 1        # collapsed
    assert by_dim["concentration"]["n"] == 2   # uncollapsed


# ─── correct=None sentinel survives the change ──────────────────────────────

def test_track_record_summary_correct_none_still_excluded_before_collapse():
    """A data-gap row (correct=None, matured but the fetch failed) must be
    excluded from both n and accuracy, same as before this change — the
    sentinel must survive collapse."""
    rows = [
        _grade_row(ticker="AAA", signal_date="2026-01-01", correct=None),
        _grade_row(ticker="AAA", signal_date="2026-01-02", correct=True),
    ]
    out = jg.track_record_summary(_grades_df(rows), min_sample_n=1)
    assert len(out) == 1
    assert out[0]["n"] == 1
    assert out[0]["n_correct"] == 1
    assert out[0]["accuracy"] == 1.0


# ─── sufficient_sample / sort order unaffected ──────────────────────────────

def test_track_record_summary_sufficient_sample_reflects_collapsed_n():
    """sufficient_sample must gate on the COLLAPSED n, not the raw row count
    — 6 raw rows for one ticker collapse to n=1, below a min_sample_n=2."""
    rows = [
        _grade_row(ticker="AAA", signal_date=f"2026-01-{i:02d}", correct=True)
        for i in range(1, 7)
    ]
    out = jg.track_record_summary(_grades_df(rows), min_sample_n=2)
    assert out[0]["n"] == 1
    assert out[0]["sufficient_sample"] is False


def test_track_record_summary_sorted_by_dimension_then_source():
    rows = [
        _grade_row(source="engine", dimension="quality", ticker="AAA", correct=True),
        _grade_row(source="engine", dimension="momentum", ticker="BBB", correct=True),
    ]
    out = jg.track_record_summary(_grades_df(rows), min_sample_n=1)
    assert [r["dimension"] for r in out] == ["momentum", "quality"]
