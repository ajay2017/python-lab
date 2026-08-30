"""Regression guard for the weekly-debrief / monthly-report date-anchor bug.

`weekly_debriefs.week_ending` and `monthly_reports.period_end` are both
UNIQUE-constrained, upserted `on_conflict` that column — the intent is one row
per week/period, safely overwritable on retry. `_run_debrief`/
`_run_monthly_report` previously anchored that column to raw `now_et.date()`,
so a `force=True` retry (ALERT_FORCE=1, or a standalone ALERT_RUN_MODE=debrief
invocation) landing on a DIFFERENT calendar day than the original Sunday run
computed a DIFFERENT date — the upsert then created a near-duplicate row
instead of overwriting. Confirmed live in production: `weekly_debriefs` has
real pairs `2026-06-27`/`2026-06-28` and `2026-08-09`/`2026-08-10`, one day
apart.

The fix anchors both to `market_time.most_recent_sunday(now_et.date())`, so a
genuine Sunday run, a same-week cron retry, AND an interactive "Generate Now"
/ "Generate Monthly Report" click on any weekday in `app.py` all compute the
IDENTICAL date. The formula itself now lives in `stock_analyzer.market_time`
(originally a private `_most_recent_sunday` in this module) so `cron_runner`
and `app.py` share one definition instead of risking two that drift.
"""
import datetime

import pandas as pd

import cron_runner as cr
from stock_analyzer import market_time


# ── the pure helper — pins the exact formula ──────────────────────────────────

def test_most_recent_sunday_on_a_sunday_is_unchanged():
    d = datetime.date(2026, 8, 9)          # a Sunday
    assert market_time.most_recent_sunday(d) == d


def test_most_recent_sunday_on_a_monday_goes_back_one_day():
    d = datetime.date(2026, 8, 10)         # the Monday after 2026-08-09
    assert market_time.most_recent_sunday(d) == datetime.date(2026, 8, 9)
    assert market_time.most_recent_sunday(d) != d, "must not fall back to raw now_et.date()"


def test_most_recent_sunday_on_a_saturday_goes_back_six_days():
    d = datetime.date(2026, 8, 22)         # a Saturday
    assert market_time.most_recent_sunday(d) == datetime.date(2026, 8, 16)
    assert market_time.most_recent_sunday(d) != d


def test_most_recent_sunday_on_a_wednesday_goes_back_three_days():
    d = datetime.date(2026, 8, 12)         # a Wednesday
    assert market_time.most_recent_sunday(d) == datetime.date(2026, 8, 9)


# ── _run_debrief: the real production pair (2026-08-09 / 2026-08-10) ─────────

def _capture_load_daily_snapshots(monkeypatch, captured):
    """Return an early-exit-friendly frame (1 of the required 5 snapshot days)
    so _run_debrief logs and returns right after the call whose kwargs we care
    about, without needing to mock the LLM/email path at all."""
    def _fake(*, start_date, end_date):
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        return pd.DataFrame({"snapshot_date": [str(end_date)]})
    monkeypatch.setattr(cr.db, "load_daily_snapshots", _fake)


def test_run_debrief_anchors_a_genuine_sunday_to_itself(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}
    _capture_load_daily_snapshots(monkeypatch, captured)

    now_et = cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0))  # Sunday
    rc = cr._run_debrief(now_et, force=False)

    assert rc == 0
    assert captured["end_date"] == datetime.date(2026, 8, 9)
    assert captured["start_date"] == datetime.date(2026, 8, 3)


def test_run_debrief_forced_retry_the_next_day_anchors_to_the_same_sunday(monkeypatch):
    """The actual bug: a force=True retry on Monday must compute the SAME
    week_ending as the Sunday run it's retrying, not now_et.date()."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}
    _capture_load_daily_snapshots(monkeypatch, captured)

    now_et = cr._ET.localize(datetime.datetime(2026, 8, 10, 9, 0, 0))  # Monday retry
    rc = cr._run_debrief(now_et, force=True)

    assert rc == 0
    assert captured["end_date"] == datetime.date(2026, 8, 9), \
        "a Monday retry must land on the same week_ending as the Sunday run"
    assert captured["end_date"] != now_et.date(), \
        "must not regress to raw now_et.date()"
    assert captured["start_date"] == datetime.date(2026, 8, 3)


def test_run_debrief_forced_retry_six_days_later_still_anchors_to_the_same_sunday(monkeypatch):
    """A Saturday retry (offset 6) pins the exact formula, not just Monday."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}
    _capture_load_daily_snapshots(monkeypatch, captured)

    now_et = cr._ET.localize(datetime.datetime(2026, 8, 15, 9, 0, 0))  # Saturday
    rc = cr._run_debrief(now_et, force=True)

    assert rc == 0
    assert captured["end_date"] == datetime.date(2026, 8, 9)
    assert captured["end_date"] != now_et.date()


# ── _run_monthly_report: same anchor, same guarantee ──────────────────────────

def _capture_load_recommendations(monkeypatch, captured):
    """Empty (not None) recs frame + a healthy DB probe -> _run_monthly_report
    logs 'nothing to report yet' and returns right after the call whose kwargs
    we care about, without needing to mock the LLM/email path."""
    def _fake(*, start_date, end_date):
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        return pd.DataFrame()
    monkeypatch.setattr(cr.db, "load_recommendations", _fake)
    monkeypatch.setattr(cr.db, "load_trades", lambda: pd.DataFrame())
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                        lambda: pd.DataFrame({"Ticker": ["AAPL"]}))


def test_run_monthly_report_anchors_a_genuine_sunday_to_itself(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}
    _capture_load_recommendations(monkeypatch, captured)

    now_et = cr._ET.localize(datetime.datetime(2026, 8, 16, 18, 0, 0))  # Sunday
    rc = cr._run_monthly_report(now_et, force=True)

    assert rc == 0
    assert captured["end_date"] == datetime.date(2026, 8, 16)
    assert captured["start_date"] == datetime.date(2026, 8, 16) - datetime.timedelta(days=28)


def test_run_monthly_report_forced_retry_the_next_day_anchors_to_the_same_sunday(monkeypatch):
    """The actual bug, applied to period_end: a force=True retry on Monday must
    compute the SAME period_end as the Sunday run it's retrying."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}
    _capture_load_recommendations(monkeypatch, captured)

    now_et = cr._ET.localize(datetime.datetime(2026, 8, 17, 9, 0, 0))  # Monday retry
    rc = cr._run_monthly_report(now_et, force=True)

    assert rc == 0
    assert captured["end_date"] == datetime.date(2026, 8, 16), \
        "a Monday retry must land on the same period_end as the Sunday run"
    assert captured["end_date"] != now_et.date(), \
        "must not regress to raw now_et.date()"


def test_run_monthly_report_forced_retry_six_days_later_still_anchors_to_the_same_sunday(monkeypatch):
    """A Saturday retry (offset 6) pins the exact formula, not just Monday."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}
    _capture_load_recommendations(monkeypatch, captured)

    now_et = cr._ET.localize(datetime.datetime(2026, 8, 22, 9, 0, 0))  # Saturday
    rc = cr._run_monthly_report(now_et, force=True)

    assert rc == 0
    assert captured["end_date"] == datetime.date(2026, 8, 16)
    assert captured["end_date"] != now_et.date()
