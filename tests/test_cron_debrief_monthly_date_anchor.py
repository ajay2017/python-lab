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
import pytest

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

def _capture_engine_inputs(monkeypatch, captured, offline=("trades_df",)):
    """Capture the (start, end) the debrief lane hands to the shared input
    loader, and return an input dict whose `offline` keys are None so the lane
    stops right after that call (no LLM / email mocking needed)."""
    from stock_analyzer import period_inputs as pi

    def _fake(db, period_start, period_end, **kw):
        captured["start_date"] = period_start
        captured["end_date"] = period_end
        out = {"trades_df": pd.DataFrame(), "account_snapshots_df": pd.DataFrame(),
               "daily_snapshots_df": pd.DataFrame(), "flows": [], "income_events": []}
        for k in offline:
            out[k] = None
        return out
    monkeypatch.setattr(pi, "load_engine_inputs", _fake)
    monkeypatch.setattr(cr, "_db_unavailable_detail", lambda: None)
    monkeypatch.setattr(cr, "_notify_failure", lambda *a, **k: None)


def test_run_debrief_anchors_a_genuine_sunday_to_itself(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}
    _capture_engine_inputs(monkeypatch, captured)

    now_et = cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0))  # Sunday
    cr._run_debrief(now_et, force=False)

    assert captured["end_date"] == datetime.date(2026, 8, 9)
    assert captured["start_date"] == datetime.date(2026, 8, 3)


def test_run_debrief_forced_retry_the_next_day_anchors_to_the_same_sunday(monkeypatch):
    """The actual bug: a force=True retry on Monday must compute the SAME
    week_ending as the Sunday run it's retrying, not now_et.date()."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}
    _capture_engine_inputs(monkeypatch, captured)

    now_et = cr._ET.localize(datetime.datetime(2026, 8, 10, 9, 0, 0))  # Monday retry
    cr._run_debrief(now_et, force=True)

    assert captured["end_date"] == datetime.date(2026, 8, 9), \
        "a Monday retry must land on the same week_ending as the Sunday run"
    assert captured["end_date"] != now_et.date(), \
        "must not regress to raw now_et.date()"
    assert captured["start_date"] == datetime.date(2026, 8, 3)


def test_run_debrief_forced_retry_six_days_later_still_anchors_to_the_same_sunday(monkeypatch):
    """A Saturday retry (offset 6) pins the exact formula, not just Monday."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}
    _capture_engine_inputs(monkeypatch, captured)

    now_et = cr._ET.localize(datetime.datetime(2026, 8, 15, 9, 0, 0))  # Saturday
    cr._run_debrief(now_et, force=True)

    assert captured["end_date"] == datetime.date(2026, 8, 9)
    assert captured["end_date"] != now_et.date()
    assert captured["start_date"] == datetime.date(2026, 8, 3)


def test_run_debrief_inputs_offline_with_db_reachable_fails_the_lane_and_notifies(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured, notified = {}, []
    _capture_engine_inputs(monkeypatch, captured, offline=("trades_df", "daily_snapshots_df"))
    monkeypatch.setattr(cr, "_notify_failure", lambda mode, detail: notified.append((mode, detail)))
    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", None)

    now_et = cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0))
    rc = cr._run_debrief(now_et, force=False)

    assert rc == 1
    assert notified == [("debrief", "debrief_inputs_offline: trades_df, daily_snapshots_df")]
    assert cr._LAST_LANE_FAILURE_DETAIL == "debrief_inputs_offline: trades_df, daily_snapshots_df"


def test_run_debrief_inputs_offline_with_db_unreachable_uses_the_outage_handler(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured, handled = {}, []
    _capture_engine_inputs(monkeypatch, captured)
    monkeypatch.setattr(cr, "_db_unavailable_detail", lambda: "supabase unreachable")
    monkeypatch.setattr(cr, "_handle_db_unavailable",
                        lambda lane, now, detail: handled.append((lane, detail)) or 1)

    now_et = cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0))
    rc = cr._run_debrief(now_et, force=False)

    assert rc == 1
    assert handled == [("debrief", "supabase unreachable")]


def test_run_debrief_no_minimum_snapshot_floor_anymore(monkeypatch):
    """The old 5-snapshot-day floor skipped every holiday week; the lane now
    proceeds whenever the engine inputs are readable (it reaches the recs read)."""
    from stock_analyzer import period_inputs as pi
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reached = []
    monkeypatch.setattr(pi, "load_engine_inputs", lambda db, s, e, **kw: {
        "trades_df": pd.DataFrame(), "account_snapshots_df": pd.DataFrame(),
        "daily_snapshots_df": pd.DataFrame(), "flows": [], "income_events": []})

    def _stop(**kw):
        reached.append(kw)
        raise RuntimeError("stop here")
    monkeypatch.setattr(cr.db, "load_recommendations", _stop)
    with pytest.raises(RuntimeError, match="stop here"):
        cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0)), force=False)
    assert reached and reached[0]["end_date"] == datetime.date(2026, 8, 9)


# ── _run_debrief: a paid result that is not saved / not generated fails the lane ──

def _full_lane(monkeypatch, *, generate, saved, notified):
    """Run _run_debrief far enough to reach generation/saving with everything mocked.
    `generate` -> what generate_debrief returns; `saved` -> save_weekly_debrief's result."""
    from stock_analyzer import debrief_advisor as da
    from stock_analyzer import period_inputs as pi
    import stock_analyzer.data as sdata

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    for k in ("RESEND_API_KEY", "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(pi, "load_engine_inputs", lambda db, s, e, **kw: {
        "trades_df": pd.DataFrame(), "account_snapshots_df": pd.DataFrame(),
        "daily_snapshots_df": pd.DataFrame(), "flows": [], "income_events": []})
    monkeypatch.setattr(pi, "compute_period_core", lambda *a, **k: {})
    monkeypatch.setattr(sdata, "fetch_spy", lambda period="6mo": None)
    monkeypatch.setattr(cr.db, "load_recommendations", lambda **k: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda days_back=10: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_thesis_reviews", lambda: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_weekly_debriefs", lambda limit=1: pd.DataFrame())
    monkeypatch.setattr(cr.db, "save_weekly_debrief", lambda rec: saved)
    monkeypatch.setattr(da, "build_debrief_package",
                        lambda **k: {"narratable": True, "result_status": "ok", "week_ending": "2026-08-09"})
    monkeypatch.setattr(da, "generate_debrief", lambda pkg, key: generate)
    monkeypatch.setattr(cr, "_notify_failure", lambda mode, detail: notified.append((mode, detail)))
    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", None)
    return cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0)), force=False)


_RESULT = {"week_ending": "2026-08-09", "performance_pct": 1.0, "alpha_pct": 0.5, "metrics_version": 2}


def test_run_debrief_success_is_green_with_no_notification(monkeypatch):
    notified = []
    assert _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified) == 0
    assert notified == [] and cr._LAST_LANE_FAILURE_DETAIL is None


def test_run_debrief_save_failure_fails_the_lane_with_one_notification(monkeypatch):
    notified = []
    assert _full_lane(monkeypatch, generate=dict(_RESULT), saved=False, notified=notified) == 1
    assert len(notified) == 1 and notified[0][0] == "debrief"
    assert "weekly_debriefs: save failed" in notified[0][1] and "apply the DDL" in notified[0][1]
    assert cr._LAST_LANE_FAILURE_DETAIL == notified[0][1]


def test_run_debrief_save_failure_still_sends_the_email_first(monkeypatch):
    # A failed save (e.g. the metrics_version DDL missing) must not cost the
    # owner the week's debrief: the email goes out, THEN the lane fails.
    from stock_analyzer import notify as _nt   # _run_debrief imports it locally
    sent, notified = [], []
    monkeypatch.setattr(_nt, "render_debrief_email", lambda *a, **k: "<html/>")
    monkeypatch.setattr(_nt, "send_email_resend",
                        lambda **k: (sent.append(k["subject"]) or (True, "")))
    monkeypatch.setattr(cr.db, "_client", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    real_delenv = monkeypatch.delenv
    monkeypatch.delenv = lambda *a, **k: None          # keep the Resend vars _full_lane would drop
    for k, v in (("RESEND_API_KEY", "k"), ("ALERT_EMAIL_TO", "to@x"), ("ALERT_EMAIL_FROM", "from@x")):
        monkeypatch.setenv(k, v)
    try:
        rc = _full_lane(monkeypatch, generate=dict(_RESULT), saved=False, notified=notified)
    finally:
        monkeypatch.delenv = real_delenv
    assert rc == 1 and len(notified) == 1
    assert len(sent) == 1 and "Weekly Debrief" in sent[0]


def test_run_debrief_llm_failure_fails_the_lane_with_one_notification(monkeypatch):
    notified = []
    assert _full_lane(monkeypatch, generate=None, saved=True, notified=notified) == 1
    assert len(notified) == 1 and notified[0][0] == "debrief"
    assert notified[0][1].startswith("debrief_blind:") and "LLM call failed" in notified[0][1]


def test_run_debrief_reads_exit_signals_back_to_the_weeks_first_day(monkeypatch):
    seen = []
    from stock_analyzer import debrief_advisor as da
    notified = []
    # drive the same mocked lane but capture days_back (Sunday 2026-08-09, week starts 08-03 -> 7)
    import stock_analyzer.data as sdata  # noqa: F401  (ensures module import before patching)
    orig = cr.db
    _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified)
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none",
                        lambda days_back=10: seen.append(days_back) or pd.DataFrame())
    cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 14, 9, 0, 0)), force=True)  # Friday retry
    assert seen == [da.signal_days_back(datetime.date(2026, 8, 14), datetime.date(2026, 8, 3))] == [12]
    assert orig is cr.db


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


# ── _run_monthly_report: B4b, an empty report with data is a failed lane ──────

def _monthly_with_package(monkeypatch, has_data, notified):
    from stock_analyzer import intelligence_report as ir
    import yfinance
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cr.db, "load_recommendations",
                        lambda **kw: pd.DataFrame({"ticker": ["AAPL"]}))
    monkeypatch.setattr(cr.db, "load_trades", lambda: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_weekly_debriefs", lambda limit=5: pd.DataFrame())
    monkeypatch.setattr("stock_analyzer.data.fetch_live_prices",
                        lambda *a, **k: {})
    monkeypatch.setattr(yfinance, "download",
                        lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ir, "build_report_package", lambda **kw: {
        "n_total": 1, "n_acted": 0, "n_graded": 0, "q0_ready": False,
        "has_data": has_data})
    monkeypatch.setattr(ir, "generate_report", lambda package, api_key: None)
    monkeypatch.setattr(cr, "_notify_failure", lambda m, d: notified.append((m, d)))
    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", None)


def test_run_monthly_report_none_with_data_fails_the_lane(monkeypatch):
    notified = []
    _monthly_with_package(monkeypatch, True, notified)
    now_et = cr._ET.localize(datetime.datetime(2026, 8, 16, 18, 0, 0))
    assert cr._run_monthly_report(now_et, force=True) == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.startswith("monthly_blind")
    assert "deep=" in cr._LAST_LANE_FAILURE_DETAIL
    assert notified == [("monthly", cr._LAST_LANE_FAILURE_DETAIL)]


def test_run_monthly_report_none_without_data_is_a_quiet_skip(monkeypatch):
    notified = []
    _monthly_with_package(monkeypatch, False, notified)
    now_et = cr._ET.localize(datetime.datetime(2026, 8, 16, 18, 0, 0))
    assert cr._run_monthly_report(now_et, force=True) == 0
    assert cr._LAST_LANE_FAILURE_DETAIL is None and notified == []
