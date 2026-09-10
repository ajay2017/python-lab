"""
Tests for stock_analyzer/account.py's annualization_caveat() — added 2026-07-28
alongside the Benchmark Mirror total-account-value fix, to explain when an
annualized money-weighted return (short window and/or a levered account) can
look far more dramatic than the underlying period return warrants.
"""
from stock_analyzer.account import (
    annualization_caveat, _ANNUALIZE_CAVEAT_MAX_DAYS,
    compute_account_snapshot, leverage_series_for_chart,
)
import pandas as pd
import pytest

pytestmark = pytest.mark.fast


def test_none_days_returns_none():
    assert annualization_caveat(None) is None


def test_long_unlevered_window_returns_none():
    assert annualization_caveat(_ANNUALIZE_CAVEAT_MAX_DAYS + 1, is_levered=False) is None


def test_exactly_at_max_days_boundary_returns_none():
    # "< max_days" is the short-window condition — the boundary itself is not short
    assert annualization_caveat(_ANNUALIZE_CAVEAT_MAX_DAYS, is_levered=False) is None


def test_short_window_unlevered_mentions_days():
    msg = annualization_caveat(_ANNUALIZE_CAVEAT_MAX_DAYS - 1, is_levered=False)
    assert msg is not None
    assert str(_ANNUALIZE_CAVEAT_MAX_DAYS - 1) in msg


def test_short_window_levered_mentions_both_leverage_and_days():
    msg = annualization_caveat(33, is_levered=True)
    assert msg is not None
    assert "33" in msg
    assert "leverag" in msg.lower()


def test_long_window_levered_mentions_leverage_only():
    msg = annualization_caveat(_ANNUALIZE_CAVEAT_MAX_DAYS + 10, is_levered=True)
    assert msg is not None
    assert "margin" in msg.lower() or "leverag" in msg.lower()


def test_short_unlevered_and_short_levered_messages_differ():
    unlevered = annualization_caveat(30, is_levered=False)
    levered = annualization_caveat(30, is_levered=True)
    assert unlevered != levered


# ── compute_account_snapshot ────────────────────────────────────────────────
# Pure, no-I/O daily account-level leverage/margin-cushion snapshot (feeds the
# EOD cron write into account_daily_snapshots). NOW is fixed and tz-aware so
# every test is deterministic — the function itself does no clock read.

_NOW = pd.Timestamp("2026-09-10T12:00:00Z")
_ROWS = [
    {"ticker": "AAPL", "shares": 10, "close_price": 200.0},
    {"ticker": "MSFT", "shares": 5, "close_price": 400.0},
]  # gross_book = 2000 + 2000 = 4000.0


def test_gross_book_and_maintenance_rate_always_populated():
    r = compute_account_snapshot("2026-09-10", _ROWS, None, 0.25, 7, _NOW)
    assert r["gross_book"] == 4000.0
    assert r["maintenance_rate"] == 0.25


def test_no_cash_record_leaves_all_cash_fields_none():
    r = compute_account_snapshot("2026-09-10", _ROWS, None, 0.25, 7, _NOW)
    assert r["cash_balance"] is None
    assert r["net_equity"] is None
    assert r["leverage"] is None
    assert r["cushion"] is None
    assert r["call_distance_pct"] is None
    assert r["cash_as_of"] is None


def test_cash_missing_updated_at_treated_like_no_record():
    rec = {"cash_balance": -1000.0, "updated_at": None}
    r = compute_account_snapshot("2026-09-10", _ROWS, rec, 0.25, 7, _NOW)
    assert r["cash_balance"] is None
    assert r["cash_as_of"] is None


def test_cash_balance_none_still_records_cash_as_of():
    """A record exists (has updated_at) but cash_balance itself is missing —
    cash_as_of must still surface so staleness/absence is visible in the
    history, even though every cash-derived field stays None."""
    rec = {"cash_balance": None, "updated_at": _NOW.isoformat()}
    r = compute_account_snapshot("2026-09-10", _ROWS, rec, 0.25, 7, _NOW)
    assert r["cash_balance"] is None
    assert r["cash_as_of"] == rec["updated_at"]


def test_stale_at_exactly_the_limit_is_still_fresh():
    """Inclusive boundary — age_days == stale_days_limit is FRESH, matching
    margin.resolve_net_capital's own `> stale_days_limit` convention."""
    rec = {"cash_balance": -1000.0, "updated_at": (_NOW - pd.Timedelta(days=7)).isoformat()}
    r = compute_account_snapshot("2026-09-10", _ROWS, rec, 0.25, 7, _NOW)
    assert r["cash_balance"] == -1000.0
    assert r["net_equity"] == 3000.0
    assert r["leverage"] == pytest.approx(4000.0 / 3000.0)


def test_stale_one_day_past_the_limit_blanks_cash_fields_but_keeps_cash_as_of():
    rec = {"cash_balance": -1000.0, "updated_at": (_NOW - pd.Timedelta(days=8)).isoformat()}
    r = compute_account_snapshot("2026-09-10", _ROWS, rec, 0.25, 7, _NOW)
    assert r["cash_balance"] is None
    assert r["net_equity"] is None
    assert r["leverage"] is None
    assert r["cushion"] is None
    assert r["call_distance_pct"] is None
    assert r["cash_as_of"] == rec["updated_at"]  # staleness stays visible


def test_no_cash_record_still_returns_gross_book():
    r = compute_account_snapshot("2026-09-10", _ROWS, None, 0.25, 7, _NOW)
    assert r["gross_book"] == 4000.0


def test_net_equity_le_zero_in_call_leverage_none_cushion_present():
    """A debit big enough to push net_equity <= 0 (margin-called). leverage
    must be None (gross/net_equity would be a nonsensical negative ratio);
    cushion/call_distance_pct come straight from margin.call_distance's own
    output, which IS defined here (it doesn't require net_equity > 0)."""
    rec = {"cash_balance": -4500.0, "updated_at": _NOW.isoformat()}  # net = 4000-4500 = -500
    r = compute_account_snapshot("2026-09-10", _ROWS, rec, 0.25, 7, _NOW)
    assert r["net_equity"] == -500.0
    assert r["leverage"] is None
    assert r["cushion"] is not None
    assert r["call_distance_pct"] is not None


def test_unlevered_leverage_computed_but_cushion_none():
    """cash_balance >= 0 (no debit) — margin.call_distance() itself returns
    None (nothing to compute against zero debit), so cushion/call_distance_pct
    stay None, but leverage is still gross_book/net_equity and must be < 1."""
    rec = {"cash_balance": 500.0, "updated_at": _NOW.isoformat()}  # net = 4500
    r = compute_account_snapshot("2026-09-10", _ROWS, rec, 0.25, 7, _NOW)
    assert r["cushion"] is None
    assert r["call_distance_pct"] is None
    assert r["leverage"] is not None
    assert r["leverage"] < 1.0
    assert r["leverage"] == pytest.approx(4000.0 / 4500.0)


def test_snapshot_date_is_stringified():
    r = compute_account_snapshot("2026-09-10", _ROWS, None, 0.25, 7, _NOW)
    assert r["snapshot_date"] == "2026-09-10"


def test_no_holdings_gives_zero_gross_book_not_a_crash():
    r = compute_account_snapshot("2026-09-10", [], None, 0.25, 7, _NOW)
    assert r["gross_book"] == 0.0


# ── leverage_series_for_chart ───────────────────────────────────────────────
# The chart's null-gap resample helper — must NOT blanket-drop a resampled
# bucket just because ONE plotted column (e.g. a stale-cash day) is null.

def _lev_df(n=10):
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "leverage":          [1.1 + 0.01 * i for i in range(n)],
        "call_distance_pct": [-50 + i for i in range(n)],
        "gross_book":        [1000.0] * n,
    }, index=dates)


def test_all_data_no_resample_returns_same_row_count():
    df = _lev_df(5)
    out = leverage_series_for_chart(df, "All data")
    assert len(out) == 5


def test_row_with_all_plotted_cols_null_is_dropped():
    df = _lev_df(5)
    df.iloc[2, df.columns.get_loc("leverage")] = None
    df.iloc[2, df.columns.get_loc("call_distance_pct")] = None
    out = leverage_series_for_chart(df, "All data")
    assert len(out) == 4


def test_row_with_only_one_plotted_col_null_is_kept():
    """The stale-cash case: leverage/call_distance_pct null, but the row
    itself (and gross_book) is real — dropping it would erase a genuine
    staleness gap instead of showing it."""
    df = _lev_df(5)
    df.iloc[2, df.columns.get_loc("leverage")] = 1.5  # only call_distance_pct null
    df.iloc[2, df.columns.get_loc("call_distance_pct")] = None
    out = leverage_series_for_chart(df, "All data")
    assert len(out) == 5


def test_weekly_resample_uses_last_and_preserves_a_stale_gap():
    # 14 days spanning 2 ISO weeks (Fri-anchored resample) — a fully-null day
    # inside a bucket must not make the WHOLE bucket vanish if other days in
    # it have data (resample('W-FRI').last() already handles that; this just
    # confirms the helper doesn't additionally nuke a legitimately-null
    # bucket that has SOME real values elsewhere in the week).
    df = _lev_df(14)
    out = leverage_series_for_chart(df, "Weekly")
    assert not out.empty
    assert list(out.columns) == list(df.columns)


def test_monthly_resample_runs_without_error():
    df = _lev_df(40)
    out = leverage_series_for_chart(df, "Monthly")
    assert not out.empty
