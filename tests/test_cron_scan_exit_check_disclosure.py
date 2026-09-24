"""Tests for cron_runner._resolve_exit_alerts_for_email / the exit-check-state
fold into _daily_action_fingerprint — 2026-09-24 app review, Top-5 #1 / A1.

The defect this locks: `_run_scan`'s morning-action email used
`db.load_exit_signals()`, whose own docstring says its except branch
"returns the same empty DataFrame either way" on a failed read or on a
genuine zero-row day. A DB hiccup therefore rendered the email's "handle
exits before entering" section as if the check had succeeded and found
nothing — on the one email that also tells the user to deploy new capital.

`_resolve_exit_alerts_for_email` is the pure(ish) fix: it uses
`db.load_exit_signals_or_none()` so the caller can tell "checked, zero
exits" apart from "the read itself failed," and returns that as an explicit
second value rather than collapsing both into an empty list.
"""
import pandas as pd
import pytest

import cron_runner as cr

pytestmark = pytest.mark.fast


# ─── _resolve_exit_alerts_for_email ─────────────────────────────────────────

def test_returns_empty_not_failed_when_db_has_no_credentials(monkeypatch):
    """load_exit_signals_or_none() itself returns None when has_db() is
    False -- this must surface as check_failed=True, not a silent []."""
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda days_back=365: None)
    alerts, failed = cr._resolve_exit_alerts_for_email("2026-09-24")
    assert alerts == []
    assert failed is True


def test_returns_failed_true_when_the_query_raises(monkeypatch):
    """Defense-in-depth: even if load_exit_signals_or_none itself somehow
    raised instead of catching internally, the helper must not propagate or
    silently swallow into a bare []."""
    def _boom(days_back=365):
        raise RuntimeError("supabase unreachable")
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", _boom)
    alerts, failed = cr._resolve_exit_alerts_for_email("2026-09-24")
    assert alerts == []
    assert failed is True


def test_genuine_zero_row_day_is_not_a_failure(monkeypatch):
    """An empty DataFrame (has_db() True, query ran, nothing to return) is a
    real, checked, clean day -- must NOT be reported as check_failed."""
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda days_back=365: pd.DataFrame())
    alerts, failed = cr._resolve_exit_alerts_for_email("2026-09-24")
    assert alerts == []
    assert failed is False


def test_missing_signal_date_column_is_not_a_failure(monkeypatch):
    """A malformed/legacy-shape frame without signal_date is defensively
    treated as no-signals-today, not as a check failure -- the read itself
    succeeded, the column just isn't there."""
    monkeypatch.setattr(
        cr.db, "load_exit_signals_or_none",
        lambda days_back=365: pd.DataFrame([{"ticker": "AAA"}]),
    )
    alerts, failed = cr._resolve_exit_alerts_for_email("2026-09-24")
    assert alerts == []
    assert failed is False


def test_missing_signal_type_column_with_signal_date_present_is_not_a_failure(monkeypatch):
    """Regression for the reviewer's non-blocking finding on this commit
    (cron_runner.py:1394-1401 pre-fix): the guard originally checked only
    'signal_date not in columns', so a frame with signal_date present but
    signal_type absent reached the row-filter line and raised an uncaught
    KeyError instead of returning a clean/defensive result -- a robustness
    regression vs. the original code, which wrapped load+filter in one try.
    The guard is now symmetric across both columns."""
    monkeypatch.setattr(
        cr.db, "load_exit_signals_or_none",
        lambda days_back=365: pd.DataFrame([{"ticker": "AAA", "signal_date": "2026-09-24"}]),
    )
    alerts, failed = cr._resolve_exit_alerts_for_email("2026-09-24")
    assert alerts == []
    assert failed is False


def test_filters_to_todays_exit_and_trim_rows_only(monkeypatch):
    df = pd.DataFrame([
        {"ticker": "AAA", "signal_type": "EXIT", "signal_date": "2026-09-24"},
        {"ticker": "BBB", "signal_type": "TRIM", "signal_date": "2026-09-24"},
        {"ticker": "CCC", "signal_type": "WATCH", "signal_date": "2026-09-24"},  # wrong tier
        {"ticker": "DDD", "signal_type": "EXIT", "signal_date": "2026-09-23"},  # wrong day
    ])
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda days_back=365: df)
    alerts, failed = cr._resolve_exit_alerts_for_email("2026-09-24")
    assert failed is False
    tickers = sorted(a["ticker"] for a in alerts)
    assert tickers == ["AAA", "BBB"]


# ─── _daily_action_fingerprint — exit-check state folded in ─────────────────

def test_fingerprint_differs_between_clean_and_failed_check_with_same_alerts():
    """A day where the check STARTS FAILING is itself a legitimate re-fire
    condition -- dedup must not mask it against yesterday's clean-day
    fingerprint just because both happen to carry an empty exit_alerts list."""
    top_pick = {"ticker": "AAA", "composite_score": 75.0}
    fp_ok     = cr._daily_action_fingerprint(top_pick, [], exit_check_unavailable=False)
    fp_failed = cr._daily_action_fingerprint(top_pick, [], exit_check_unavailable=True)
    assert fp_ok != fp_failed


def test_fingerprint_default_matches_explicit_false():
    """Backward-compat: the new parameter must default to the old (clean)
    behaviour for any existing caller that doesn't pass it."""
    top_pick = {"ticker": "AAA", "composite_score": 75.0}
    exit_alerts = [{"ticker": "ZZZ", "signal_type": "EXIT"}]
    assert (cr._daily_action_fingerprint(top_pick, exit_alerts)
            == cr._daily_action_fingerprint(top_pick, exit_alerts, exit_check_unavailable=False))
