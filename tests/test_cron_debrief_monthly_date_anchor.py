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

The WEEKLY fix anchors `week_ending` to `market_time.most_recent_sunday(now_et.date())`
(via `resolve_period("last_week", ...)`), so a genuine Sunday run, a same-week
cron retry, AND an interactive "Generate Now" click on any weekday in `app.py`
all compute the IDENTICAL date. The formula lives in `stock_analyzer.market_time`
so `cron_runner` and `app.py` share one definition instead of risking two that
drift.

The MONTHLY lane no longer uses the Sunday anchor (owner U12, 2026-11-17,
docs/plans/acted-rule-and-report-periods.md C1): its window is the previous
CALENDAR month (`report_periods.monthly_period`), so `period_end` is always a
month's last day and a `force` retry on any later day of the month still lands
on the same upsert key. The old "most recent Sunday - 28 days" window left a gap
whenever first Sundays were five weeks apart. That change is deliberate; the
monthly tests below pin the calendar-month window on purpose.
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
    monkeypatch.setattr(cr._acct_autorepair, "run_auto_repair",
                        lambda *_a, **_k: {"ok": True, "since": "x", "checked": 0, "applied": [],
                                          "failed": [], "refusals": [], "error": None})    # N1 C5: tested in test_account_snapshot_autorepair.py
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
    monkeypatch.setattr(cr._acct_autorepair, "run_auto_repair",
                        lambda *_a, **_k: {"ok": True, "since": "x", "checked": 0, "applied": [],
                                          "failed": [], "refusals": [], "error": None})    # N1 C5: tested in test_account_snapshot_autorepair.py
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reached = []
    monkeypatch.setattr(pi, "load_engine_inputs", lambda db, s, e, **kw: {
        "trades_df": pd.DataFrame(), "account_snapshots_df": pd.DataFrame(),
        "daily_snapshots_df": pd.DataFrame(), "flows": [], "income_events": []})

    def _stop(**kw):
        reached.append(kw)
        raise RuntimeError("stop here")
    monkeypatch.setattr(cr.db, "load_recommendations_or_none", _stop)
    monkeypatch.setattr(cr.db, "load_thesis_reviews_or_none", lambda: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_holdings_or_none", lambda: pd.DataFrame({"Ticker": []}))
    with pytest.raises(RuntimeError, match="stop here"):
        cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0)), force=False)
    assert reached and reached[0]["end_date"] == datetime.date(2026, 8, 9)


def _recs_kwargs_for_sunday(monkeypatch, when):
    """Run the debrief lane up to its week-scoped recommendations read and return
    that read's kwargs (the read raises, so nothing after it runs)."""
    from stock_analyzer import period_inputs as pi
    monkeypatch.setattr(cr._acct_autorepair, "run_auto_repair",
                        lambda *_a, **_k: {"ok": True, "since": "x", "checked": 0, "applied": [],
                                          "failed": [], "refusals": [], "error": None})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(pi, "load_engine_inputs", lambda db, s, e, **kw: {
        "trades_df": pd.DataFrame(), "account_snapshots_df": pd.DataFrame(),
        "daily_snapshots_df": pd.DataFrame(), "flows": [], "income_events": []})
    seen, logs = [], []

    def _stop(**kw):
        seen.append(kw)
        raise RuntimeError("stop here")
    monkeypatch.setattr(cr.db, "load_recommendations_or_none", _stop)
    monkeypatch.setattr(cr.db, "load_thesis_reviews_or_none", lambda: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_holdings_or_none", lambda: pd.DataFrame({"Ticker": []}))
    monkeypatch.setattr(cr, "_log", lambda msg: logs.append(msg))
    with pytest.raises(RuntimeError, match="stop here"):
        cr._run_debrief(cr._ET.localize(when), force=False)
    return seen[0], logs


def test_run_debrief_loads_recs_back_to_last_weeks_start_so_open_calls_carry_over(monkeypatch):
    """Acted-rule C3: the Sunday 2026-11-08 debrief covers Mon 11-02 .. Sun 11-08,
    but a call from the week before can still be open and must be carried over,
    so the read reaches back to 2026-10-26 (week_start - 7), and stops at the
    week's end (never past it)."""
    kw, logs = _recs_kwargs_for_sunday(monkeypatch, datetime.datetime(2026, 11, 8, 18, 0, 0))
    assert kw["start_date"] == datetime.date(2026, 10, 26)
    assert kw["end_date"] == datetime.date(2026, 11, 8)
    assert any("lookback starts 2026-10-26" in m for m in logs)


def test_run_debrief_recs_start_is_the_shared_helpers_value(monkeypatch):
    from stock_analyzer import debrief_entry_calls as dec
    kw, _logs = _recs_kwargs_for_sunday(monkeypatch, datetime.datetime(2026, 11, 15, 18, 0, 0))
    assert kw["start_date"] == dec.recs_load_start(datetime.date(2026, 11, 9))
    assert kw["end_date"] == datetime.date(2026, 11, 15)


# ── _run_debrief: a paid result that is not saved / not generated fails the lane ──

def _full_lane(monkeypatch, *, generate, saved, notified, repair=None, calls=None):
    """Run _run_debrief far enough to reach generation/saving with everything mocked.
    `generate` -> what generate_debrief returns; `saved` -> save_weekly_debrief's result."""
    from stock_analyzer import debrief_advisor as da
    from stock_analyzer import period_inputs as pi
    import stock_analyzer.data as sdata

    monkeypatch.setattr(cr._acct_autorepair, "run_auto_repair",
                        repair or (lambda *_a, **_k: {"ok": True, "since": "x", "checked": 0, "applied": [],
                                                      "failed": [], "refusals": [], "error": None}))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    for k in ("RESEND_API_KEY", "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(pi, "load_engine_inputs", lambda db, s, e, **kw: {
        "trades_df": pd.DataFrame(), "account_snapshots_df": pd.DataFrame(),
        "daily_snapshots_df": pd.DataFrame(), "flows": [], "income_events": []})
    monkeypatch.setattr(pi, "compute_period_core", lambda *a, **k: {})
    monkeypatch.setattr(sdata, "fetch_spy", lambda period="6mo": None)
    monkeypatch.setattr(cr.db, "load_recommendations_or_none", lambda **k: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda days_back=10: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_thesis_reviews_or_none", lambda: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_holdings_or_none", lambda: pd.DataFrame({"Ticker": []}))
    monkeypatch.setattr(cr.db, "load_weekly_debriefs", lambda limit=1: pd.DataFrame())
    calls = [] if calls is None else calls
    monkeypatch.setattr(cr.db, "save_weekly_debrief", lambda rec: calls.append("save") or saved)
    monkeypatch.setattr(da, "build_debrief_package",
                        lambda **k: {"narratable": True, "result_status": "ok", "week_ending": "2026-08-09"})
    monkeypatch.setattr(da, "generate_debrief", lambda pkg, key: calls.append("generate") or generate)
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


def test_run_debrief_thesis_read_failure_fails_the_lane_before_any_llm_call(monkeypatch):
    from stock_analyzer import debrief_advisor as da
    notified, generated = [], []
    _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified)
    monkeypatch.setattr(da, "generate_debrief", lambda pkg, key: generated.append(1) or dict(_RESULT))
    monkeypatch.setattr(cr.db, "load_thesis_reviews_or_none", lambda: None)
    monkeypatch.setattr(cr, "_db_unavailable_detail", lambda: None)
    notified.clear()
    rc = cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0)), force=False)
    assert rc == 1 and generated == []
    assert notified == [("debrief", "debrief_inputs_offline: thesis_reviews")]
    assert cr._LAST_LANE_FAILURE_DETAIL == "debrief_inputs_offline: thesis_reviews"


def test_run_debrief_broken_list_is_latest_per_ticker_and_held_only(monkeypatch):
    from stock_analyzer import debrief_advisor as da
    notified, pkgs = [], []
    reviews = pd.DataFrame([
        {"ticker": "AAA", "status": "BROKEN", "reviewed_at": "2026-07-01"},
        {"ticker": "AAA", "status": "INTACT", "reviewed_at": "2026-08-01"},
        {"ticker": "bbb", "status": "BROKEN", "reviewed_at": "2026-08-01"},
        {"ticker": "CCC", "status": "BROKEN", "reviewed_at": "2026-08-01"},
    ])
    _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified)
    monkeypatch.setattr(cr.db, "load_thesis_reviews_or_none", lambda: reviews)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                        lambda: pd.DataFrame({"Ticker": ["AAA", "BBB"]}))
    monkeypatch.setattr(da, "build_debrief_package",
                        lambda **k: pkgs.append(k["broken_theses"]) or
                        {"narratable": True, "result_status": "ok", "week_ending": "2026-08-09"})
    cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0)), force=False)
    assert pkgs == [["BBB"]]


def test_run_debrief_thesis_read_failure_on_db_outage_routes_to_outage_handler(monkeypatch):
    notified, routed = [], []
    _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified)
    monkeypatch.setattr(cr.db, "load_thesis_reviews_or_none", lambda: None)
    monkeypatch.setattr(cr, "_db_unavailable_detail", lambda: "db down")
    monkeypatch.setattr(cr, "_handle_db_unavailable",
                        lambda mode, now, detail: routed.append((mode, detail)) or 7)
    notified.clear()
    rc = cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0)), force=False)
    assert rc == 7 and routed == [("debrief", "db down")] and notified == []


def test_run_debrief_unusable_reviews_frame_fails_the_lane(monkeypatch):
    notified = []
    _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified)
    monkeypatch.setattr(cr.db, "load_thesis_reviews_or_none",
                        lambda: pd.DataFrame([{"ticker": "AAA", "reviewed_at": "2026-08-01"}]))
    notified.clear()
    rc = cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0)), force=False)
    assert rc == 1
    assert notified == [("debrief", "debrief_inputs_offline: thesis_reviews")]


def test_run_debrief_holdings_read_failure_keeps_list_unfiltered(monkeypatch):
    from stock_analyzer import debrief_advisor as da
    notified, pkgs, logs = [], [], []
    reviews = pd.DataFrame([
        {"ticker": "AAA", "status": "BROKEN", "reviewed_at": "2026-08-01"},
        {"ticker": "CCC", "status": "BROKEN", "reviewed_at": "2026-08-01"},
    ])
    _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified)
    monkeypatch.setattr(cr.db, "load_thesis_reviews_or_none", lambda: reviews)
    monkeypatch.setattr(cr.db, "load_holdings_or_none", lambda: None)
    monkeypatch.setattr(cr, "_log", lambda msg: logs.append(msg))
    monkeypatch.setattr(da, "build_debrief_package",
                        lambda **k: pkgs.append(k["broken_theses"]) or
                        {"narratable": True, "result_status": "ok", "week_ending": "2026-08-09"})
    cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0)), force=False)
    assert pkgs == [["AAA", "CCC"]]
    assert any("holdings lookup failed" in m for m in logs)


# ── Deb: an unreadable exit_signals read is disclosed and fails the heartbeat ──

_EXIT_LINE = "debrief: exit_signals unreadable — protective section disclosed as unavailable"


def _lane_with_exit_read(monkeypatch, exit_read, *, generate=None, saved=True):
    """Run the fully-mocked lane once, then re-run it with `exit_read` as the
    load_exit_signals_or_none result, capturing the build kwargs and the calls."""
    from stock_analyzer import debrief_advisor as da
    notified, calls, kws = [], [], []
    _full_lane(monkeypatch, generate=dict(_RESULT) if generate is None else generate,
               saved=saved, notified=notified, calls=calls)
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda days_back=10: exit_read)
    monkeypatch.setattr(da, "build_debrief_package",
                        lambda **k: kws.append(k) or
                        {"narratable": True, "result_status": "ok", "week_ending": "2026-08-09"})
    notified.clear()
    calls.clear()
    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", None)
    rc = cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0)), force=False)
    return rc, notified, calls, kws


def test_run_debrief_unreadable_exit_signals_is_disclosed_and_fails_the_heartbeat(monkeypatch):
    rc, notified, calls, kws = _lane_with_exit_read(monkeypatch, None)
    assert len(kws) == 1 and kws[0]["exit_signals_unavailable"] is True
    assert kws[0]["exit_signals_df"] is None
    assert calls == ["generate", "save"]                  # still generated AND saved, never blocked
    assert rc == 1
    assert notified == [("debrief", "debrief saved; " + _EXIT_LINE)]
    assert cr._LAST_LANE_FAILURE_DETAIL == "debrief saved; " + _EXIT_LINE


def test_run_debrief_readable_exit_signals_passes_not_unavailable_and_stays_green(monkeypatch):
    for frame in (pd.DataFrame(), pd.DataFrame([{"ticker": "AAA"}])):
        rc, notified, calls, kws = _lane_with_exit_read(monkeypatch, frame)
        assert kws[0]["exit_signals_unavailable"] is False
        assert rc == 0 and notified == [] and cr._LAST_LANE_FAILURE_DETAIL is None
        assert calls == ["generate", "save"]


def test_run_debrief_unreadable_exit_signals_rides_every_later_failure_path(monkeypatch):
    from stock_analyzer import debrief_advisor as da
    notified = []
    _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified)
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda days_back=10: None)
    now = cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0))
    # LLM failure: the one notification names both problems
    monkeypatch.setattr(da, "generate_debrief", lambda pkg, key: None)
    notified.clear()
    assert cr._run_debrief(now, force=False) == 1 and len(notified) == 1
    assert notified[0][1].startswith("debrief_blind:") and notified[0][1].endswith(_EXIT_LINE)
    # save failure
    monkeypatch.setattr(da, "generate_debrief", lambda pkg, key: dict(_RESULT))
    monkeypatch.setattr(cr.db, "save_weekly_debrief", lambda rec: False)
    notified.clear()
    assert cr._run_debrief(now, force=False) == 1 and len(notified) == 1
    assert "weekly_debriefs: save failed" in notified[0][1] and notified[0][1].endswith(_EXIT_LINE)


def test_run_debrief_unreadable_exit_signals_keeps_the_repair_fingerprint_commit(monkeypatch):
    # the extra line must not break commit-after-delivery of the repair fingerprint
    committed = []
    monkeypatch.setattr(cr, "_repair_failure_lines",
                        lambda res, now: (["account_snapshot_repair: refused"], "fp-1"))
    monkeypatch.setattr(cr, "_commit_repair_fingerprint", lambda fp, now: committed.append(fp))
    notified = []
    _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified)
    monkeypatch.setattr(cr, "_notify_failure", lambda mode, detail: notified.append((mode, detail)) or True)
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda days_back=10: None)
    notified.clear()
    committed.clear()
    rc = cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0)), force=False)
    assert rc == 1 and len(notified) == 1
    assert "account_snapshot_repair: refused" in notified[0][1] and _EXIT_LINE in notified[0][1]
    assert committed == ["fp-1"]


# ── DebRecs: an unreadable recommendations read is disclosed and fails the heartbeat ──

_RECS_LINE = "debrief: recommendations unreadable — entry signals disclosed as unavailable"


def _lane_with_recs_read(monkeypatch, week_read, *, all_read=pd.DataFrame(), exit_read=None):
    """Run the fully-mocked lane once, then re-run it with `week_read` as the
    week-scoped load_recommendations_or_none result (`all_read` for the no-arg
    full-history call), capturing the build kwargs and the calls."""
    from stock_analyzer import debrief_advisor as da
    notified, calls, kws, logs = [], [], [], []
    _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified, calls=calls)
    monkeypatch.setattr(cr.db, "load_recommendations_or_none",
                        lambda **k: week_read if k else all_read)
    if exit_read is not None:
        monkeypatch.setattr(cr.db, "load_exit_signals_or_none",
                            lambda days_back=10: exit_read[0])
    monkeypatch.setattr(da, "build_debrief_package",
                        lambda **k: kws.append(k) or
                        {"narratable": True, "result_status": "ok", "week_ending": "2026-08-09"})
    monkeypatch.setattr(cr, "_log", lambda msg: logs.append(msg))
    notified.clear()
    calls.clear()
    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", None)
    rc = cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0)), force=False)
    return rc, notified, calls, kws, logs


def test_run_debrief_unreadable_recs_is_disclosed_and_fails_the_heartbeat(monkeypatch):
    rc, notified, calls, kws, logs = _lane_with_recs_read(monkeypatch, None)
    assert len(kws) == 1 and kws[0]["recs_unavailable"] is True
    assert kws[0]["recs_df"] is None
    assert calls == ["generate", "save"]                  # still generated AND saved, never blocked
    assert rc == 1
    assert notified == [("debrief", "debrief saved; " + _RECS_LINE)]
    assert cr._LAST_LANE_FAILURE_DETAIL == "debrief saved; " + _RECS_LINE
    assert any("recommendations lookup failed" in m for m in logs)
    assert any("recs loaded (from lookback)=unreadable" in m for m in logs)      # the result line no longer crashes on None


def test_run_debrief_readable_recs_passes_not_unavailable_and_stays_green(monkeypatch):
    for frame in (pd.DataFrame(), pd.DataFrame([{"ticker": "AAA"}])):
        rc, notified, calls, kws, _logs = _lane_with_recs_read(monkeypatch, frame)
        assert kws[0]["recs_unavailable"] is False
        assert rc == 0 and notified == [] and cr._LAST_LANE_FAILURE_DETAIL is None
        assert calls == ["generate", "save"]


def test_run_debrief_unreadable_full_history_is_logged_but_not_a_lane_failure(monkeypatch):
    # the behavioural fingerprint is optional enrichment: log only, stay green
    rc, notified, calls, kws, logs = _lane_with_recs_read(
        monkeypatch, pd.DataFrame(), all_read=None)
    assert rc == 0 and notified == [] and cr._LAST_LANE_FAILURE_DETAIL is None
    assert kws[0]["all_recs_df"] is None and kws[0]["recs_unavailable"] is False
    assert any("full recommendation history lookup failed" in m for m in logs)


_ENTRY_LINE = "debrief: entry-call check failed — entry signals disclosed as unavailable"


def _lane_with_real_package(monkeypatch, *, entry_boom):
    """The fully-mocked lane, but with the REAL build_debrief_package, so the
    entry-call module runs (or, with `entry_boom`, raises) for real."""
    from stock_analyzer import debrief_advisor as da
    real_build = da.build_debrief_package
    notified, calls, logs, pkgs = [], [], [], []
    _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified, calls=calls)
    monkeypatch.setattr(da, "build_debrief_package", real_build)
    monkeypatch.setattr(da, "generate_debrief",
                        lambda pkg, key: pkgs.append(pkg) or dict(_RESULT))
    if entry_boom:
        def _boom(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr("stock_analyzer.debrief_entry_calls.weekly_entry_calls", _boom)
    monkeypatch.setattr(cr, "_log", lambda msg: logs.append(msg))
    notified.clear()
    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", None)
    rc = cr._run_debrief(cr._ET.localize(datetime.datetime(2026, 8, 9, 18, 0, 0)), force=False)
    return rc, notified, logs, pkgs


def test_run_debrief_entry_module_crash_turns_the_lane_red_and_stays_disclosed(monkeypatch):
    from stock_analyzer import debrief_advisor as da
    rc, notified, logs, pkgs = _lane_with_real_package(monkeypatch, entry_boom=True)
    assert rc == 1 and len(notified) == 1
    assert notified[0] == ("debrief", "debrief saved; " + _ENTRY_LINE)
    assert cr._LAST_LANE_FAILURE_DETAIL == "debrief saved; " + _ENTRY_LINE
    assert any("debrief: entry-call module failed (RuntimeError); entry signals not judged" in m
               for m in logs)
    # the debrief itself is still generated, and its package carries the disclosure
    assert len(pkgs) == 1 and pkgs[0]["recs_status"] == "unavailable"
    assert pkgs[0]["entry_error"] == "RuntimeError"
    note = da.recs_unavailable_note(pkgs[0])
    assert "couldn't be loaded" in note and "not the database" in note


def test_run_debrief_healthy_entry_check_stays_green(monkeypatch):
    rc, notified, logs, pkgs = _lane_with_real_package(monkeypatch, entry_boom=False)
    assert rc == 0 and notified == []
    assert pkgs[0]["recs_status"] == "ok" and pkgs[0]["entry_error"] is None
    assert not any("entry-call module failed" in m for m in logs)


def test_run_debrief_unreadable_recs_does_not_also_report_a_module_failure(monkeypatch):
    # the read failure has its own line; the module line is only for a good read
    rc, notified, _calls, kws, logs = _lane_with_recs_read(monkeypatch, None)
    assert not any("entry-call module failed" in m for m in logs)
    assert notified == [("debrief", "debrief saved; " + _RECS_LINE)]


def test_run_debrief_both_signal_reads_failing_names_both_failures(monkeypatch):
    rc, notified, _calls, kws, _logs = _lane_with_recs_read(monkeypatch, None, exit_read=(None,))
    assert kws[0]["recs_unavailable"] is True and kws[0]["exit_signals_unavailable"] is True
    assert rc == 1 and len(notified) == 1
    assert _EXIT_LINE in notified[0][1] and _RECS_LINE in notified[0][1]


# ── _run_monthly_report: the previous CALENDAR month (U12) ────────────────────

def _capture_load_recommendations(monkeypatch, captured):
    """Empty (not None) recs frame + a healthy DB probe -> _run_monthly_report
    logs 'nothing to report yet' and returns right after the call whose kwargs
    we care about, without needing to mock the LLM/email path."""
    def _fake(*, start_date, end_date):
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        return pd.DataFrame()
    monkeypatch.setattr(cr.db, "load_recommendations_or_none", _fake)
    monkeypatch.setattr(cr.db, "load_trades_or_none", lambda: pd.DataFrame())
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                        lambda: pd.DataFrame({"Ticker": ["AAPL"]}))


def _monthly_window(monkeypatch, when, force=True):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}
    _capture_load_recommendations(monkeypatch, captured)
    rc = cr._run_monthly_report(cr._ET.localize(when), force=force)
    return rc, captured


def test_run_monthly_report_covers_the_previous_calendar_month(monkeypatch):
    rc, cap = _monthly_window(monkeypatch, datetime.datetime(2026, 8, 16, 18, 0, 0))  # a Sunday
    assert rc == 0
    assert cap["start_date"] == datetime.date(2026, 7, 1)
    assert cap["end_date"] == datetime.date(2026, 7, 31)


def test_run_monthly_report_first_sunday_of_december_covers_november(monkeypatch):
    """The owner's 12-06 email: November 1-30, no 28-day rolling window."""
    rc, cap = _monthly_window(monkeypatch, datetime.datetime(2026, 12, 6, 18, 0, 0), force=False)
    assert rc == 0
    assert (cap["start_date"], cap["end_date"]) == (datetime.date(2026, 11, 1),
                                                    datetime.date(2026, 11, 30))


def test_run_monthly_report_first_sunday_on_a_dst_change_day(monkeypatch):
    """2026-11-01 is both a first Sunday and the fall-back day."""
    rc, cap = _monthly_window(monkeypatch, datetime.datetime(2026, 11, 1, 18, 0, 0), force=False)
    assert rc == 0
    assert (cap["start_date"], cap["end_date"]) == (datetime.date(2026, 10, 1),
                                                    datetime.date(2026, 10, 31))


@pytest.mark.parametrize("when", [
    datetime.datetime(2026, 12, 13, 18, 0, 0),    # the second Sunday
    datetime.datetime(2026, 12, 7, 9, 0, 0),      # the Monday after the first Sunday
    datetime.datetime(2026, 12, 8, 9, 0, 0),      # a weekday inside the first week
])
def test_run_monthly_report_not_forced_only_runs_on_the_first_sunday(monkeypatch, when):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}
    _capture_load_recommendations(monkeypatch, captured)
    assert cr._run_monthly_report(cr._ET.localize(when), force=False) == 0
    assert captured == {}, "the lane must skip before reading any data"


@pytest.mark.parametrize("when", [
    datetime.datetime(2026, 8, 17, 9, 0, 0),      # the Monday retry
    datetime.datetime(2026, 8, 22, 9, 0, 0),      # the Saturday retry
    datetime.datetime(2026, 8, 31, 23, 30, 0),    # the last evening of the month
])
def test_run_monthly_report_forced_retry_later_in_the_month_lands_on_the_same_period_end(
        monkeypatch, when):
    """The upsert key: a force=True retry on ANY day of August must compute the
    SAME period_end (July 31) as the first-Sunday run it is retrying."""
    _rc, original = _monthly_window(monkeypatch, datetime.datetime(2026, 8, 2, 18, 0, 0),
                                    force=False)               # first Sunday of August
    rc, retry = _monthly_window(monkeypatch, when)
    assert rc == 0
    assert retry["end_date"] == original["end_date"] == datetime.date(2026, 7, 31)
    assert retry["start_date"] == original["start_date"] == datetime.date(2026, 7, 1)
    assert retry["end_date"] != when.date(), "must not regress to raw now_et.date()"


def test_run_monthly_report_in_january_covers_the_previous_decembers_calendar_month(monkeypatch):
    rc, cap = _monthly_window(monkeypatch, datetime.datetime(2027, 1, 3, 18, 0, 0), force=False)
    assert rc == 0
    assert (cap["start_date"], cap["end_date"]) == (datetime.date(2026, 12, 1),
                                                    datetime.date(2026, 12, 31))


# ── _run_monthly_report: offline contract (an unreadable read is a FAILURE) ───

def _monthly_offline(monkeypatch, *, recs, trades, probe=None):
    from stock_analyzer import intelligence_report as ir
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = {"package": 0, "generate": 0, "saved": [], "notified": [], "outage": [],
             "emailed": []}
    monkeypatch.setattr(cr.db, "load_recommendations_or_none", lambda **kw: recs)
    monkeypatch.setattr(cr.db, "load_trades_or_none", lambda: trades)
    monkeypatch.setattr(cr, "_db_unavailable_detail", lambda: probe)
    monkeypatch.setattr(cr, "_handle_db_unavailable",
                        lambda lane, now, detail: calls["outage"].append((lane, detail)) or 1)
    monkeypatch.setattr(cr, "_notify_failure", lambda m, d: calls["notified"].append((m, d)))
    monkeypatch.setattr(cr, "_send_email", lambda *a, **k: calls["emailed"].append(a))
    monkeypatch.setattr(cr.db, "save_monthly_report", lambda r: calls["saved"].append(r) or True)
    monkeypatch.setattr(ir, "build_report_package",
                        lambda **kw: calls.__setitem__("package", calls["package"] + 1) or {})
    monkeypatch.setattr(ir, "generate_report",
                        lambda p, k: calls.__setitem__("generate", calls["generate"] + 1))
    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", None)
    rc = cr._run_monthly_report(cr._ET.localize(datetime.datetime(2026, 12, 6, 18, 0, 0)),
                                force=False)
    return rc, calls


def test_run_monthly_report_unreadable_trades_fails_the_lane_with_no_report_or_email(monkeypatch):
    rc, calls = _monthly_offline(monkeypatch, recs=pd.DataFrame({"ticker": ["AAPL"]}),
                                 trades=None)
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL == "monthly_inputs_offline: trades"
    assert calls["notified"] == [("monthly", "monthly_inputs_offline: trades")]
    assert calls["package"] == 0 and calls["generate"] == 0
    assert calls["saved"] == [] and calls["emailed"] == []


def test_run_monthly_report_unreadable_recommendations_fails_the_lane(monkeypatch):
    rc, calls = _monthly_offline(monkeypatch, recs=None, trades=pd.DataFrame())
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL == "monthly_inputs_offline: recommendations"
    assert calls["package"] == 0 and calls["saved"] == [] and calls["emailed"] == []


def test_run_monthly_report_both_reads_failing_names_both(monkeypatch):
    rc, _calls = _monthly_offline(monkeypatch, recs=None, trades=None)
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL == "monthly_inputs_offline: recommendations, trades"


def test_run_monthly_report_unreadable_read_with_the_db_down_sends_the_outage_email(monkeypatch):
    rc, calls = _monthly_offline(monkeypatch, recs=pd.DataFrame({"ticker": ["AAPL"]}),
                                 trades=None, probe="supabase unreachable")
    assert rc == 1
    assert calls["outage"] == [("monthly", "supabase unreachable")]
    assert calls["package"] == 0 and calls["saved"] == [] and calls["emailed"] == []


def test_run_monthly_report_empty_recs_with_readable_trades_is_still_a_quiet_skip(monkeypatch):
    rc, calls = _monthly_offline(monkeypatch, recs=pd.DataFrame(), trades=pd.DataFrame())
    assert rc == 0
    assert calls["notified"] == [] and calls["outage"] == [] and calls["package"] == 0


# ── _run_monthly_report: B4b, an empty report with data is a failed lane ──────

def _monthly_with_package(monkeypatch, has_data, notified):
    from stock_analyzer import intelligence_report as ir
    import yfinance
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cr.db, "load_recommendations_or_none",
                        lambda **kw: pd.DataFrame({"ticker": ["AAPL"]}))
    monkeypatch.setattr(cr.db, "load_trades_or_none", lambda: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_weekly_debriefs", lambda limit=5: pd.DataFrame())
    monkeypatch.setattr("stock_analyzer.data.fetch_live_prices",
                        lambda *a, **k: {})
    monkeypatch.setattr(yfinance, "download",
                        lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ir, "build_report_package", lambda **kw: {
        "judge_state": "ok", "n_surfaced": 1, "n_total": 1, "n_acted": 0,
        "n_missed": 1, "n_open": 0, "n_unknown": 0, "n_graded": 0,
        "q0_ready": False, "has_data": has_data})
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


# ── _run_monthly_report: as_of + the period's own weekly trajectory ───────────

def _monthly_capture_package_inputs(monkeypatch, now_et, weeks):
    """Run the lane up to build_report_package and capture what it was given: the
    kwargs, and the `end=` the SPY download was asked for."""
    from stock_analyzer import intelligence_report as ir
    import yfinance
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen = {}
    monkeypatch.setattr(cr.db, "load_recommendations_or_none",
                        lambda **kw: pd.DataFrame({"ticker": ["AAPL"]}))
    monkeypatch.setattr(cr.db, "load_trades_or_none", lambda: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_weekly_debriefs",
                        lambda limit=5: seen.setdefault("limit", limit) and pd.DataFrame(
                            [{"week_ending": w, "metrics_version": 2} for w in weeks]))
    monkeypatch.setattr("stock_analyzer.data.fetch_live_prices", lambda *a, **k: {})
    monkeypatch.setattr(yfinance, "download",
                        lambda *a, **k: seen.update(spy_end=k.get("end")) or pd.DataFrame())
    monkeypatch.setattr(ir, "build_report_package",
                        lambda **kw: seen.update(pkg=kw) or {
                            "judge_state": "ok", "n_surfaced": 1, "n_total": 1,
                            "n_acted": 0, "n_missed": 1, "n_open": 0, "n_unknown": 0,
                            "n_graded": 0, "q0_ready": False, "has_data": False})
    monkeypatch.setattr(ir, "generate_report", lambda package, api_key: None)
    assert cr._run_monthly_report(now_et, force=False) == 0
    return seen


def test_run_monthly_report_measures_outcomes_to_the_run_date(monkeypatch):
    now_et = cr._ET.localize(datetime.datetime(2026, 12, 6, 18, 0, 0))
    seen = _monthly_capture_package_inputs(monkeypatch, now_et, [])
    assert seen["pkg"]["period_end"] == datetime.date(2026, 11, 30)
    assert seen["pkg"]["as_of"] == datetime.date(2026, 12, 6)
    # the SPY series must reach the run date (yfinance's `end` is exclusive)
    assert seen["spy_end"] == "2026-12-07"


def test_run_monthly_report_trajectory_is_the_periods_own_weeks(monkeypatch):
    """The 12-06 run for November: week_ending 12-06 (a December week) and 11-01
    (an October week) are out; 11-08 .. 11-29 are in, newest first."""
    now_et = cr._ET.localize(datetime.datetime(2026, 12, 6, 18, 0, 0))
    weeks = ["2026-12-06", "2026-11-29", "2026-11-22", "2026-11-15", "2026-11-08",
             "2026-11-01", "2026-10-25"]
    seen = _monthly_capture_package_inputs(monkeypatch, now_et, weeks)
    assert [w["week_ending"] for w in seen["pkg"]["weekly_rows"]] == [
        "2026-11-29", "2026-11-22", "2026-11-15", "2026-11-08"]
    assert seen["limit"] >= 52, "must reach back far enough for a forced run on an older month"


# ── _run_monthly_report: C4b, the judgement must be readable ──────────────────

def _monthly_judge_state_lane(monkeypatch, package):
    """Run the lane with `build_report_package` returning `package`; capture what
    the lane did afterwards."""
    from stock_analyzer import intelligence_report as ir
    import yfinance
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = {"generate": 0, "saved": [], "notified": [], "emailed": []}
    monkeypatch.setattr(cr.db, "load_recommendations_or_none",
                        lambda **kw: pd.DataFrame({"ticker": ["AAPL"]}))
    monkeypatch.setattr(cr.db, "load_trades_or_none", lambda: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_weekly_debriefs", lambda limit=5: pd.DataFrame())
    monkeypatch.setattr("stock_analyzer.data.fetch_live_prices", lambda *a, **k: {})
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ir, "build_report_package", lambda **kw: package)
    monkeypatch.setattr(ir, "generate_report",
                        lambda p, k: calls.__setitem__("generate", calls["generate"] + 1)
                        or {"period_start": "2026-11-01", "period_end": "2026-11-30"})
    monkeypatch.setattr(cr, "_notify_failure", lambda m, d: calls["notified"].append((m, d)))
    monkeypatch.setattr(cr, "_send_email", lambda *a, **k: calls["emailed"].append(a))
    monkeypatch.setattr(cr.db, "save_monthly_report", lambda r: calls["saved"].append(r) or True)
    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", None)
    rc = cr._run_monthly_report(cr._ET.localize(datetime.datetime(2026, 12, 6, 18, 0, 0)),
                                force=False)
    return rc, calls


@pytest.mark.parametrize("state", ["recs_unavailable", "recs_unreadable",
                                   "trades_unavailable", "trades_unreadable"])
def test_run_monthly_report_non_ok_judge_state_fails_the_lane(monkeypatch, state):
    rc, calls = _monthly_judge_state_lane(
        monkeypatch, {"judge_state": state, "has_data": False})
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL == f"monthly_unjudgeable: {state}"
    assert calls["notified"] == [("monthly", f"monthly_unjudgeable: {state}")]
    assert calls["generate"] == 0 and calls["saved"] == [] and calls["emailed"] == []


def test_run_monthly_report_a_package_without_judge_state_reads_as_not_ok(monkeypatch):
    rc, calls = _monthly_judge_state_lane(monkeypatch, {"has_data": True})
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL == "monthly_unjudgeable: None"
    assert calls["generate"] == 0 and calls["saved"] == [] and calls["emailed"] == []


def test_run_monthly_report_logs_the_counts_and_the_rule_line(monkeypatch, capsys):
    pkg = {"judge_state": "ok", "has_data": False, "n_surfaced": 7, "n_acted": 3,
           "n_missed": 2, "n_open": 1, "n_unknown": 1, "n_graded": 5,
           "q0_ready": True, "engine_alpha_pct": 1.5}
    rc, _calls = _monthly_judge_state_lane(monkeypatch, pkg)
    assert rc == 0
    out = capsys.readouterr().out
    assert ("monthly: 7 name(s) · acted=3 · skipped=2 · open=1 · unknown=1 · "
            "graded=5 · q0_ready=True · engine_alpha=1.5 · rule=v2 "
            "cutoff=2026-11-30") in out


def test_run_monthly_report_judges_at_the_period_end_not_the_run_date(monkeypatch):
    """The real pipeline: the cutoff passed to the judgement is `period_end`
    (U6). A Fri 11-27 call (window ends Tue 12-01) bought on 12-01 reads still
    open on the 12-06 run, and the outcomes are dated to the run date."""
    from stock_analyzer import monthly_entry_calls as mec
    import yfinance
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    recs = pd.DataFrame([{
        "id": 1, "ticker": "NVDA", "rec_date": "2026-11-27", "rec_type": "enter_now",
        "surfaced_at": "2026-11-27T15:00:00", "price_at_surface": 100.0,
        "composite_score": 70, "verdict": "Engine-Confirmed", "already_held": False}])
    trades = pd.DataFrame([{
        "id": 1, "ticker": "NVDA", "action": "BUY", "traded_at": "2026-12-01T15:00:00+00:00",
        "trigger_type": "MANUAL", "notes": "", "broker_txn_id": None,
        "shares": 1, "price": 100.0}])
    monkeypatch.setattr(cr.db, "load_recommendations_or_none", lambda **kw: recs)
    monkeypatch.setattr(cr.db, "load_trades_or_none", lambda: trades)
    monkeypatch.setattr(cr.db, "load_weekly_debriefs", lambda limit=5: pd.DataFrame())
    monkeypatch.setattr("stock_analyzer.data.fetch_live_prices", lambda *a, **k: {})
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: pd.DataFrame())
    seen = {}
    real = mec.build_month

    def _wrap(*a, **k):
        seen["kw"] = k
        out = real(*a, **k)
        seen["out"] = out
        return out

    monkeypatch.setattr(mec, "build_month", _wrap)
    from stock_analyzer import intelligence_report as ir
    monkeypatch.setattr(ir, "generate_report", lambda p, k: None)
    monkeypatch.setattr(cr, "_notify_failure", lambda m, d: None)
    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", None)
    cr._run_monthly_report(cr._ET.localize(datetime.datetime(2026, 12, 6, 18, 0, 0)),
                           force=False)
    assert seen["kw"]["period_end"] == datetime.date(2026, 11, 30)
    assert seen["kw"]["as_of"] == datetime.date(2026, 12, 6)
    assert [n["status"] for n in seen["out"]["names"]] == ["still_open"]
    assert seen["out"]["counts"]["n_open"] == 1
