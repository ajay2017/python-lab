"""Tests for stock_analyzer/earnings_move_forecast.py — Predictive Modeling
Shadow Layer Phase 2 (F-234), MEASUREMENT-ONLY, earnings-move magnitude.

Mirrors test_vol_forecast.py's style. Per the build spec, these are the
leakage-boundary / invariant tests the design calls out explicitly: never
guess on unknown BMO/AMC timing, never treat absence of a fresh
next-earnings date as a reschedule signal, correct BMO-vs-AMC session
selection at the boundary, and the write-eligibility window's exact
boundaries."""
import pandas as pd
import pytest

import stock_analyzer.data as sa_data
from stock_analyzer.earnings_move_forecast import (
    baseline_from_history,
    is_reschedule,
    is_write_eligible,
    realized_move,
    resolve_upcoming_earnings,
)


# ── resolve_upcoming_earnings ────────────────────────────────────────────────

def test_resolve_upcoming_earnings_fmp_hit_with_timing(monkeypatch):
    monkeypatch.setattr(
        sa_data, "fetch_earnings_calendar",
        lambda from_date, to_date: [{"ticker": "AAPL", "date": "2026-02-01", "when": "amc"}],
    )
    monkeypatch.setattr(sa_data, "fetch_next_earnings", lambda t: None)
    import datetime
    out = resolve_upcoming_earnings("AAPL", datetime.date(2026, 1, 20), 30)
    assert out == ("2026-02-01", "amc")


def test_resolve_upcoming_earnings_fmp_miss_falls_back_to_yfinance_when_empty(monkeypatch):
    monkeypatch.setattr(sa_data, "fetch_earnings_calendar", lambda from_date, to_date: [])
    monkeypatch.setattr(sa_data, "fetch_next_earnings", lambda t: "2026-02-05")
    import datetime
    out = resolve_upcoming_earnings("MU", datetime.date(2026, 1, 20), 30)
    assert out == ("2026-02-05", "")


def test_resolve_upcoming_earnings_neither_source_has_anything(monkeypatch):
    monkeypatch.setattr(sa_data, "fetch_earnings_calendar", lambda from_date, to_date: [])
    monkeypatch.setattr(sa_data, "fetch_next_earnings", lambda t: None)
    import datetime
    out = resolve_upcoming_earnings("ZZZZ", datetime.date(2026, 1, 20), 30)
    assert out == (None, "")


def test_resolve_upcoming_earnings_ignores_other_tickers_in_fmp_calendar(monkeypatch):
    monkeypatch.setattr(
        sa_data, "fetch_earnings_calendar",
        lambda from_date, to_date: [{"ticker": "MSFT", "date": "2026-02-01", "when": "bmo"}],
    )
    monkeypatch.setattr(sa_data, "fetch_next_earnings", lambda t: "2026-02-10")
    import datetime
    out = resolve_upcoming_earnings("AAPL", datetime.date(2026, 1, 20), 30)
    assert out == ("2026-02-10", "")  # falls back, not the MSFT row


def test_resolve_upcoming_earnings_unrecognized_when_value_normalizes_to_blank(monkeypatch):
    monkeypatch.setattr(
        sa_data, "fetch_earnings_calendar",
        lambda from_date, to_date: [{"ticker": "AAPL", "date": "2026-02-01", "when": "mid-day"}],
    )
    monkeypatch.setattr(sa_data, "fetch_next_earnings", lambda t: None)
    import datetime
    out = resolve_upcoming_earnings("AAPL", datetime.date(2026, 1, 20), 30)
    assert out == ("2026-02-01", "")


# ── baseline_from_history ────────────────────────────────────────────────────

def test_baseline_from_history_exact_k_available_returns_median():
    out = baseline_from_history([4.0, 6.0, 8.0], k=3)
    assert out == pytest.approx(6.0)


def test_baseline_from_history_fewer_than_k_returns_none():
    out = baseline_from_history([4.0, 6.0], k=3)
    assert out is None


def test_baseline_from_history_more_than_k_uses_only_trailing_k():
    # First value (100.0, an outlier) must be dropped -- only the trailing 3
    # (4, 6, 8) should feed the median.
    out = baseline_from_history([100.0, 4.0, 6.0, 8.0], k=3)
    assert out == pytest.approx(6.0)


def test_baseline_from_history_empty_list_returns_none():
    assert baseline_from_history([], k=3) is None


# ── realized_move ─────────────────────────────────────────────────────────────

def _df_from_closes(date_close_pairs):
    idx = pd.to_datetime([d for d, _ in date_close_pairs])
    closes = [c for _, c in date_close_pairs]
    return pd.DataFrame({"Close": closes}, index=idx)


def test_realized_move_bmo_session_selection():
    # BMO: close_before = last close STRICTLY before event_date; close_after
    # = close ON event_date.
    df = _df_from_closes([
        ("2026-01-13", 100.0),   # close_before
        ("2026-01-14", 106.0),   # close_after (event day)
        ("2026-01-15", 999.0),   # must NOT be used
    ])
    out = realized_move(df, "2026-01-14", "bmo")
    assert out == pytest.approx(6.0)


def test_realized_move_amc_session_selection():
    # AMC: close_before = close ON event_date; close_after = first close
    # STRICTLY after event_date.
    df = _df_from_closes([
        ("2026-01-13", 999.0),   # must NOT be used
        ("2026-01-14", 100.0),   # close_before (event day)
        ("2026-01-15", 91.0),    # close_after
    ])
    out = realized_move(df, "2026-01-14", "amc")
    assert out == pytest.approx(9.0)


def test_realized_move_missing_before_bar_returns_none():
    # BMO needs a bar strictly BEFORE event_date -- none exists here.
    df = _df_from_closes([("2026-01-15", 91.0)])
    out = realized_move(df, "2026-01-14", "bmo")
    assert out is None


def test_realized_move_missing_after_bar_returns_none():
    df = _df_from_closes([("2026-01-13", 100.0)])  # no bar ON event_date
    out = realized_move(df, "2026-01-14", "bmo")
    assert out is None


def test_realized_move_unknown_timing_returns_none_never_guesses():
    df = _df_from_closes([
        ("2026-01-13", 100.0), ("2026-01-14", 106.0), ("2026-01-15", 91.0),
    ])
    assert realized_move(df, "2026-01-14", "") is None
    assert realized_move(df, "2026-01-14", "midday") is None
    assert realized_move(df, "2026-01-14", None) is None


def test_realized_move_none_or_empty_df_returns_none():
    assert realized_move(None, "2026-01-14", "bmo") is None
    assert realized_move(pd.DataFrame(), "2026-01-14", "bmo") is None


def test_realized_move_split_adjusted_closes_dont_register_as_spurious_move():
    # A 2:1 split took effect BETWEEN the two bracketing sessions in the real
    # world -- but the data layer's job (not this function's) is to hand in
    # ALREADY split-adjusted closes, so the two sessions here are on a
    # consistent basis and the move must read as the genuine small earnings
    # move (2%), never anything resembling the ~50% a naive raw-price
    # comparison across an unadjusted split would produce.
    df = _df_from_closes([
        ("2026-01-13", 100.0),   # already split-adjusted close_before
        ("2026-01-14", 98.0),    # already split-adjusted close_after
    ])
    out = realized_move(df, "2026-01-14", "bmo")
    assert out == pytest.approx(2.0)
    assert out < 10.0  # nowhere near a spurious ~50% split artifact


# ── is_write_eligible ─────────────────────────────────────────────────────────

def test_is_write_eligible_boundaries():
    assert is_write_eligible(0, 3) is False
    assert is_write_eligible(1, 3) is True
    assert is_write_eligible(3, 3) is True
    assert is_write_eligible(4, 3) is False


def test_is_write_eligible_non_numeric_returns_false():
    assert is_write_eligible("x", 3) is False


# ── is_reschedule ─────────────────────────────────────────────────────────────

def test_is_reschedule_true_when_gap_smaller_than_min_gap():
    # Fresh next-earnings date only 10 days after the frozen one -- a
    # postponement, not a genuine subsequent quarterly print.
    assert is_reschedule("2026-01-15", "2026-01-25", min_gap_days=45) is True


def test_is_reschedule_false_when_gap_at_or_above_min_gap():
    assert is_reschedule("2026-01-15", "2026-03-01", min_gap_days=45) is False  # ~45d
    assert is_reschedule("2026-01-15", "2026-04-15", min_gap_days=45) is False  # ~90d


def test_is_reschedule_false_when_fresh_date_is_none():
    # Absence must NEVER be read as a reschedule signal.
    assert is_reschedule("2026-01-15", None, min_gap_days=45) is False


def test_is_reschedule_false_when_fresh_date_is_before_frozen_date():
    # A "fresh" date earlier than the frozen one isn't a forward reschedule
    # signal this function is meant to detect -- negative gap is excluded.
    assert is_reschedule("2026-01-15", "2026-01-01", min_gap_days=45) is False


def test_is_reschedule_never_raises_on_malformed_dates():
    assert is_reschedule("not-a-date", "2026-01-25", min_gap_days=45) is False


def test_is_reschedule_false_when_gap_is_exactly_zero():
    # Regression (Opus review finding, 2026-09-07): a fresh lookup returning
    # the SAME date as the frozen one (gap==0) means the event is still
    # scheduled as-is, not rescheduled. The old `0 <=` lower bound treated
    # this as a reschedule, which -- combined with a maturation-lane
    # ordering bug -- withdrew every still-upcoming pending row the same
    # run it was written, keeping the ledger permanently empty. The
    # ordering bug is fixed separately in cron_runner.py (the reschedule
    # check now only runs after a row is already past its maturity gate,
    # so a fresh lookup can never legitimately return the frozen date);
    # this `0 <` bound is the defense-in-depth layer pinned here.
    assert is_reschedule("2026-01-15", "2026-01-15", min_gap_days=45) is False
