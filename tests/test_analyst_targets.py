"""Tests for stock_analyzer/analyst_targets.py — analyst consensus
price-target (PT) cut detection (F-169 Phase 2). Pure pandas, no I/O.
Mirrors tests/test_exit_velocity.py's pure-DataFrame-fixture shape.
"""
import pandas as pd

from stock_analyzer import analyst_targets as at
from stock_analyzer.constants import PT_TARGET_LOOKBACK_DAYS


# ─── builders ───────────────────────────────────────────────────────────────

def _rows(ticker="AAA", targets=None, info_sources=None, captured_ats=None):
    """Build one row per element of `targets`, dated sequentially oldest-first
    (index 0 = oldest/compare candidate, last index = newest)."""
    targets = targets or []
    n = len(targets)
    dates = [f"2026-07-{i + 1:02d}" for i in range(n)]
    if info_sources is None:
        info_sources = [None] * n
    if captured_ats is None:
        captured_ats = [f"2026-07-{i + 1:02d}T09:00:00Z" for i in range(n)]
    return [
        {
            "ticker": ticker,
            "snapshot_date": dates[i],
            "target_mean": targets[i],
            "num_analysts": 10,
            "info_source": info_sources[i],
            "captured_at": captured_ats[i],
        }
        for i in range(n)
    ]


def _df(rows):
    return pd.DataFrame(rows)


SIX_FLAT     = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]   # 0% -> flat
SIX_UP       = [100.0, 100.0, 100.0, 100.0, 100.0, 110.0]   # +10% -> up
SIX_MILD_CUT = [100.0, 100.0, 100.0, 100.0, 100.0, 97.0]    # -3% -> cut, sub-warning


# ─── withheld / guard branches ──────────────────────────────────────────────

def test_detect_pt_cut_none_df_withheld_no_fabricated_flat():
    result = at.detect_pt_cut(None, "AAA")
    assert result["insufficient_history"] is True
    assert result["direction"] is None
    assert result["level"] is None
    assert result["pct_change"] is None


def test_detect_pt_cut_empty_df_withheld():
    result = at.detect_pt_cut(pd.DataFrame(), "AAA")
    assert result["insufficient_history"] is True
    assert result["direction"] is None


def test_detect_pt_cut_missing_required_columns_withheld():
    df = pd.DataFrame([{"ticker": "AAA", "target_mean": 100.0}])  # missing snapshot_date
    result = at.detect_pt_cut(df, "AAA")
    assert result["insufficient_history"] is True
    assert result["direction"] is None


def test_detect_pt_cut_ticker_absent_entirely_withheld_zero_days():
    df = _df(_rows(ticker="BBB", targets=SIX_FLAT))
    result = at.detect_pt_cut(df, "AAA")
    assert result["insufficient_history"] is True
    assert result["n_snapshot_days"] == 0
    assert result["direction"] is None


def test_detect_pt_cut_five_distinct_dates_insufficient_history_not_flat():
    # One short of the 6-row minimum (PT_TARGET_LOOKBACK_DAYS + 1 = 6).
    df = _df(_rows(targets=[100.0, 100.0, 100.0, 100.0, 100.0]))
    result = at.detect_pt_cut(df, "AAA")
    assert result["insufficient_history"] is True
    assert result["direction"] is None  # never fabricate "flat" from partial data
    assert result["n_snapshot_days"] == 5


# ─── exactly 6 dates — happy path ────────────────────────────────────────────

def test_detect_pt_cut_six_dates_flat():
    df = _df(_rows(targets=SIX_FLAT))
    result = at.detect_pt_cut(df, "AAA")
    assert result["insufficient_history"] is False
    assert result["direction"] == "flat"
    assert result["level"] is None
    assert result["pct_change"] == 0.0
    assert result["n_snapshot_days"] == 6


def test_detect_pt_cut_six_dates_up():
    df = _df(_rows(targets=SIX_UP))
    result = at.detect_pt_cut(df, "AAA")
    assert result["direction"] == "up"
    assert result["level"] is None
    assert result["pct_change"] > 0


def test_detect_pt_cut_six_dates_mild_cut_no_alert_level():
    df = _df(_rows(targets=SIX_MILD_CUT))
    result = at.detect_pt_cut(df, "AAA")
    assert result["direction"] == "cut"
    assert result["level"] is None  # real cut, but below the warning threshold


# ─── boundary classification ─────────────────────────────────────────────────

def test_detect_pt_cut_boundary_exactly_warn_pct_is_warning():
    df = _df(_rows(targets=[100.0, 100.0, 100.0, 100.0, 100.0, 93.0]))  # -7.0%
    result = at.detect_pt_cut(df, "AAA")
    assert result["level"] == "warning"


def test_detect_pt_cut_boundary_exactly_danger_pct_is_danger():
    df = _df(_rows(targets=[100.0, 100.0, 100.0, 100.0, 100.0, 85.0]))  # -15.0%
    result = at.detect_pt_cut(df, "AAA")
    assert result["level"] == "danger"


def test_detect_pt_cut_boundary_just_inside_warn_no_alert():
    df = _df(_rows(targets=[100.0, 100.0, 100.0, 100.0, 100.0, 93.01]))  # -6.99%
    result = at.detect_pt_cut(df, "AAA")
    assert result["level"] is None
    assert result["direction"] == "cut"


def test_detect_pt_cut_beyond_warn_pct_is_warning():
    df = _df(_rows(targets=[100.0, 100.0, 100.0, 100.0, 100.0, 92.0]))  # -8%
    result = at.detect_pt_cut(df, "AAA")
    assert result["level"] == "warning"


def test_detect_pt_cut_beyond_danger_pct_is_danger_not_unbounded():
    df = _df(_rows(targets=[100.0, 100.0, 100.0, 100.0, 100.0, 80.0]))  # -20%
    result = at.detect_pt_cut(df, "AAA")
    assert result["level"] == "danger"


# ─── info_source source-switch suppression ───────────────────────────────────

def test_detect_pt_cut_info_source_both_none_trusted():
    df = _df(_rows(
        targets=[100.0, 100.0, 100.0, 100.0, 100.0, 90.0],
        info_sources=[None] * 6,
    ))
    result = at.detect_pt_cut(df, "AAA")
    assert result["source_switch_suppressed"] is False
    assert result["direction"] is not None


def test_detect_pt_cut_info_source_both_equal_non_none_trusted():
    info_sources = [None] * 6
    info_sources[0] = "fmp"   # compare (oldest)
    info_sources[5] = "fmp"   # newest
    df = _df(_rows(targets=[100.0, 100.0, 100.0, 100.0, 100.0, 90.0], info_sources=info_sources))
    result = at.detect_pt_cut(df, "AAA")
    assert result["source_switch_suppressed"] is False
    assert result["direction"] is not None


def test_detect_pt_cut_info_source_differing_non_none_suppressed():
    info_sources = [None] * 6
    info_sources[0] = "fmp"
    info_sources[5] = "other_source"
    df = _df(_rows(targets=[100.0, 100.0, 100.0, 100.0, 100.0, 90.0], info_sources=info_sources))
    result = at.detect_pt_cut(df, "AAA")
    assert result["source_switch_suppressed"] is True
    assert result["direction"] is None
    assert result["level"] is None


def test_detect_pt_cut_info_source_one_null_one_non_null_is_a_deliberate_trusted_call():
    # Judgment call pinned explicitly: info_source is only ever None or "fmp"
    # today, so a lone FMP-backfilled day must not disqualify the comparison.
    info_sources = [None] * 6
    info_sources[0] = None       # compare (oldest)
    info_sources[5] = "fmp"      # newest
    df = _df(_rows(targets=[100.0, 100.0, 100.0, 100.0, 100.0, 90.0], info_sources=info_sources))
    result = at.detect_pt_cut(df, "AAA")
    assert result["source_switch_suppressed"] is False
    assert result["direction"] is not None


# ─── divide-by-zero guard ─────────────────────────────────────────────────────

def test_detect_pt_cut_compare_target_zero_withheld_not_a_crash():
    df = _df(_rows(targets=[0.0, 100.0, 100.0, 100.0, 100.0, 100.0]))
    result = at.detect_pt_cut(df, "AAA")
    assert result["insufficient_history"] is False
    assert result["direction"] is None
    assert result["pct_change"] is None
    assert result["compare_target"] == 0.0


# ─── de-dup via captured_at ───────────────────────────────────────────────────

def test_detect_pt_cut_duplicate_snapshot_date_resolved_via_captured_at():
    rows = _rows(targets=[100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    # Duplicate the oldest date (2026-07-01) with a stale target_mean and an
    # EARLIER captured_at -- the dedup must keep the row with the LATEST
    # captured_at, not the first-inserted one.
    stale_dup = dict(rows[0])
    stale_dup["target_mean"] = 999.0
    stale_dup["captured_at"] = "2026-07-01T01:00:00Z"  # earlier than rows[0]'s 09:00:00Z
    df = _df([stale_dup] + rows)
    result = at.detect_pt_cut(df, "AAA")
    assert result["n_snapshot_days"] == 6  # duplicate doesn't inflate the count
    assert result["insufficient_history"] is False
    assert result["compare_target"] == 100.0  # NOT the stale 999.0
    assert result["direction"] == "flat"


# ─── case-insensitivity ──────────────────────────────────────────────────────

def test_detect_pt_cut_ticker_case_insensitive():
    df = _df(_rows(ticker="aaa", targets=SIX_UP))
    result = at.detect_pt_cut(df, "AAA")
    assert result["ticker"] == "AAA"
    assert result["direction"] == "up"
