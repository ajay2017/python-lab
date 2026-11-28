"""Buy-check C3a (docs/plans/entry-fail-closed-c3-spec.md, owner amendments 2026-11-27):
the earnings date is resolved ONCE in the producer, so every reader agrees.

Covers the pure resolver (`earnings_date`), the FMP cross-check index
(`earnings_xcheck`), the providers' additive fields, `bundle_loader.load_bundle`'s new
keys (producer pin + AST pin), `buy_check_state.earnings_state` reading the resolved
stamp, and the cron log lines. No network: the FMP calendar seam is stubbed (conftest
makes it raise unless a test opts in), yfinance is faked, db is stubbed.

Time-context matrix: every resolver case is fed an injected `today` (never the clock):
a Friday, the Thanksgiving Thursday, a weekend, the report day itself, the day after.
"""
import importlib.util
import math
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import bundle_loader
from stock_analyzer import buy_check_state as bcs
from stock_analyzer import constants as C
from stock_analyzer import earnings_date as ed
from stock_analyzer import earnings_xcheck as ex
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer.providers import fmp_provider as fp
from stock_analyzer.providers import orchestrator as orch
from stock_analyzer.providers import yfinance_provider as yp
from stock_analyzer.providers.base import CAP_BUNDLE, DataProvider

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 27)          # a Friday (the day after Thanksgiving)


def _d(offset: int, today: date = TODAY) -> str:
    return (today + timedelta(days=offset)).isoformat()


def _xc(upcoming=(), reported=None, state="ok") -> dict:
    return {"state": state, "upcoming": list(upcoming), "reported": reported}


def _res(yahoo=(), status="date", xc=None, fund=False, stale=False, today=TODAY) -> dict:
    return ed.resolve(is_fund=fund, stale_copy=stale, yahoo_dates=list(yahoo),
                      yahoo_status=status, xcheck=_xc() if xc is None else xc, today=today)


@pytest.fixture(autouse=True)
def _fresh_chains():
    orch.reset()
    yield
    orch.reset()


# ═══ constants (owner-approved 2026-11-27) ═══════════════════════════════════

def test_constants_carry_the_owner_approved_values():
    assert C.EARNINGS_PAST_DATE_HOLD_DAYS == 14
    assert C.EARNINGS_REPORTED_CLEAR_DAYS == 30
    assert C.EARNINGS_XCHECK_HORIZON_DAYS == 90
    assert C.EARNINGS_XCHECK_CACHE_TTL_SEC == 21600
    assert C.EARNINGS_XCHECK_FAIL_RETRY_SEC == 300


# ═══ resolve: the rule table ═════════════════════════════════════════════════

def test_a_fund_is_not_applicable_even_with_a_stray_date():
    r = _res(yahoo=[_d(3)], fund=True)
    assert r["state"] == "not_applicable" and r["date"] is None


def test_a_stale_saved_copy_is_failed_even_with_a_future_date():
    r = _res(yahoo=[_d(3)], stale=True, xc=_xc(upcoming=[_d(2)]))
    assert (r["state"], r["reason"], r["date"]) == ("failed", "stale_copy", None)


def test_yahoo_future_only():
    r = _res(yahoo=[_d(10)])
    assert (r["state"], r["date"], r["source"]) == ("date", _d(10), "yahoo")
    assert r["disagree"] is None


def test_fmp_future_only_when_yahoo_has_none():
    for status in ("no_date", "failed", None):
        r = _res(yahoo=[], status=status, xc=_xc(upcoming=[_d(10)]))
        assert (r["state"], r["date"], r["source"]) == ("date", _d(10), "fmp"), status


def test_equal_dates_read_both():
    r = _res(yahoo=[_d(5)], xc=_xc(upcoming=[_d(5)]))
    assert (r["date"], r["source"], r["disagree"]) == (_d(5), "both", None)


def test_when_they_differ_the_earlier_wins_and_the_disagreement_is_kept():
    r = _res(yahoo=[_d(6)], xc=_xc(upcoming=[_d(4)]))
    assert (r["date"], r["source"]) == (_d(4), "fmp")
    assert r["disagree"] == {"yahoo": _d(6), "fmp": _d(4)}
    r = _res(yahoo=[_d(4)], xc=_xc(upcoming=[_d(6)]))
    assert (r["date"], r["source"]) == (_d(4), "yahoo")
    assert r["disagree"] == {"yahoo": _d(4), "fmp": _d(6)}


def test_yahoo_past_and_future_pair_reads_the_future():
    r = _res(yahoo=[_d(-3), _d(5)])
    assert (r["state"], r["date"]) == ("date", _d(5))


def test_crwd_yahoo_past_date_is_overridden_by_the_fmp_future_date():
    r = _res(yahoo=["2026-11-23"], xc=_xc(upcoming=["2026-12-01"]))
    assert (r["state"], r["date"], r["source"]) == ("date", "2026-12-01", "fmp")
    assert r["yahoo_date"] == "2026-11-23"


def test_crwd_with_the_fmp_calendar_unreadable_is_past_unchecked():
    r = _res(yahoo=["2026-11-23"], xc=_xc(state="failed"))
    assert (r["state"], r["reason"], r["date"]) == ("failed", "past_unchecked", None)


def test_crwd_with_fmp_ok_but_no_row_is_past_unconfirmed():
    # CRWD is NOT in FMP's free-plan sample: "no row" proves nothing, so it stays held.
    r = _res(yahoo=["2026-11-23"], xc=_xc())
    assert (r["state"], r["reason"], r["date"]) == ("failed", "past_unconfirmed", None)
    assert r["yahoo_date"] == "2026-11-23"


def test_an_fmp_upcoming_list_is_ignored_when_the_index_failed():
    r = _res(yahoo=[], status="no_date", xc=_xc(upcoming=[_d(5)], state="failed"))
    assert r["state"] == "no_date"


def test_nvda_reported_with_an_actual_passes():
    r = _res(yahoo=["2026-11-17"], xc=_xc(reported="2026-11-17"))
    assert (r["state"], r["date"], r["last_reported"]) == ("reported", None, "2026-11-17")


def test_nvda_reported_without_an_actual_stays_held():
    # `earnings_xcheck` sets `reported` only from a row with an actual EPS, so a report
    # without one reaches the resolver as reported=None.
    r = _res(yahoo=["2026-11-17"], xc=_xc(reported=None))
    assert (r["state"], r["reason"]) == ("failed", "past_unconfirmed")


def test_the_past_hold_boundary_14_days_held_15_days_passes():
    held = _res(yahoo=[_d(-C.EARNINGS_PAST_DATE_HOLD_DAYS)])
    assert (held["state"], held["reason"]) == ("failed", "past_unconfirmed")
    passed = _res(yahoo=[_d(-C.EARNINGS_PAST_DATE_HOLD_DAYS - 1)])
    assert (passed["state"], passed["date"]) == ("reported_unconfirmed", None)
    assert passed["last_reported"] == _d(-C.EARNINGS_PAST_DATE_HOLD_DAYS - 1)


def test_the_past_hold_boundary_with_the_calendar_unreadable():
    held = _res(yahoo=[_d(-C.EARNINGS_PAST_DATE_HOLD_DAYS)], xc=_xc(state="failed"))
    assert (held["state"], held["reason"]) == ("failed", "past_unchecked")
    # older than the hold window it passes whatever the calendar says (Q4 now only
    # matters inside the window)
    passed = _res(yahoo=[_d(-C.EARNINGS_PAST_DATE_HOLD_DAYS - 1)], xc=_xc(state="failed"))
    assert passed["state"] == "reported_unconfirmed"


def test_yesterday_is_inside_the_hold_window():
    r = _res(yahoo=[_d(-1)])
    assert (r["state"], r["reason"]) == ("failed", "past_unconfirmed")


def test_the_reported_boundary_30_days_passes_31_does_not():
    n = C.EARNINGS_REPORTED_CLEAR_DAYS
    assert _res(yahoo=[], xc=_xc(reported=_d(-n)))["state"] == "reported"
    assert _res(yahoo=[], xc=_xc(reported=_d(-1)))["state"] == "reported"
    out = _res(yahoo=[], status="failed", xc=_xc(reported=_d(-n - 1)))
    assert (out["state"], out["reason"]) == ("failed", "read_failed")
    # a "report" dated today is not a past report
    out = _res(yahoo=[], status="failed", xc=_xc(reported=_d(0)))
    assert out["state"] == "failed"


def test_the_horizon_boundary_90_days_counts_91_does_not():
    h = C.EARNINGS_XCHECK_HORIZON_DAYS
    near = _res(yahoo=[], status="no_date", xc=_xc(upcoming=[_d(h)]))
    assert (near["state"], near["date"]) == ("date", _d(h))
    far = _res(yahoo=[], status="no_date", xc=_xc(upcoming=[_d(h + 1)]))
    assert far["state"] == "no_date"
    # Yahoo's own future date has no horizon
    assert _res(yahoo=[_d(h + 1)])["date"] == _d(h + 1)


def test_no_date_with_nothing_else_passes_as_no_date():
    r = _res(yahoo=[], status="no_date")
    assert (r["state"], r["date"]) == ("no_date", None)


def test_read_failed_with_nothing_else_is_failed():
    r = _res(yahoo=[], status="failed")
    assert (r["state"], r["reason"]) == ("failed", "read_failed")
    r = _res(yahoo=[], status=None)
    assert (r["state"], r["reason"]) == ("failed", "read_failed")


def test_a_date_equal_to_today_is_a_date_not_a_past_one():
    r = _res(yahoo=[_d(0)])
    assert (r["state"], r["date"]) == ("date", _d(0))


def test_garbage_inputs_never_raise_and_read_failed():
    for kw in (dict(yahoo_dates=None, xcheck=None, today=TODAY),
               dict(yahoo_dates=["x", None, 5], xcheck="nope", today=TODAY),
               dict(yahoo_dates=[], xcheck=_xc(), today=None),
               dict(yahoo_dates=[], xcheck=_xc(), today="2026-11-27")):
        r = ed.resolve(is_fund=False, stale_copy=False, yahoo_status=None, **kw)
        assert r["state"] == "failed" and r["reason"] == "read_failed", kw


def test_resolve_does_not_mutate_its_inputs():
    ydates = [_d(-3), _d(5)]
    xc = _xc(upcoming=[_d(9), _d(4)], reported=_d(-2))
    snap = (list(ydates), dict(xc), list(xc["upcoming"]))
    _res(yahoo=ydates, xc=xc)
    assert (ydates, xc, xc["upcoming"]) == (snap[0], snap[1], snap[2])


@pytest.mark.parametrize("today, yahoo, held", [
    (date(2026, 11, 27), "2026-11-23", True),    # Friday after Thanksgiving: CRWD, 4 days
    (date(2026, 11, 28), "2026-11-23", True),    # Saturday
    (date(2026, 11, 29), "2026-11-23", True),    # Sunday
    (date(2026, 11, 26), "2026-11-23", True),    # Thanksgiving itself
])
def test_time_context_a_past_date_is_held_on_weekends_and_holidays(today, yahoo, held):
    r = _res(yahoo=[yahoo], today=today)
    assert (r["state"] == "failed") is held


def test_time_context_the_report_day_and_the_day_after():
    # report day 12-01: the date is still `date` (day 0), all day
    assert _res(yahoo=["2026-12-01"], today=date(2026, 12, 1))["state"] == "date"
    # 12-02: FMP has the actual -> reported; without it -> still held
    day2 = date(2026, 12, 2)
    assert _res(yahoo=["2026-12-01"], xc=_xc(reported="2026-12-01"), today=day2)["state"] == "reported"
    assert _res(yahoo=["2026-12-01"], xc=_xc(), today=day2)["state"] == "failed"


def test_time_context_dst_change_days_use_the_injected_calendar_date():
    for today in (date(2026, 11, 1), date(2027, 3, 14)):
        r = _res(yahoo=[(today + timedelta(days=3)).isoformat()], today=today)
        assert (r["state"], r["date"]) == ("date", (today + timedelta(days=3)).isoformat())


# ═══ yahoo_dates / xcheck_for ════════════════════════════════════════════════

def test_yahoo_dates_parses_sorts_dedupes_and_never_raises():
    assert ed.yahoo_dates(["2026-12-03", "2026-12-01 16:00:00", "2026-12-03"]) == [
        "2026-12-01", "2026-12-03"]
    assert ed.yahoo_dates("2026-12-01") == ["2026-12-01"]
    assert ed.yahoo_dates(date(2026, 12, 1)) == ["2026-12-01"]
    assert ed.yahoo_dates((date(2026, 12, 2), pd.Timestamp("2026-12-01"))) == [
        "2026-12-01", "2026-12-02"]
    assert ed.yahoo_dates(["soon", None, 7, ""]) == []
    for junk in (None, {}, 5, object(), {"a": 1}, []):
        assert ed.yahoo_dates(junk) == []


def test_xcheck_for_reads_the_ticker_and_its_dot_dash_variant():
    idx = {"state": "ok", "by_ticker": {
        "CRWD": {"upcoming": ["2026-12-01"], "reported": None},
        "BRK-B": {"upcoming": ["2026-12-05"], "reported": "2026-11-10"}}}
    assert ed.xcheck_for(idx, "crwd") == {"state": "ok", "upcoming": ["2026-12-01"],
                                          "reported": None}
    assert ed.xcheck_for(idx, "BRK.B")["upcoming"] == ["2026-12-05"]
    assert ed.xcheck_for(idx, "BRK.B")["reported"] == "2026-11-10"
    assert ed.xcheck_for(idx, "ZZZZ") == {"state": "ok", "upcoming": [], "reported": None}


@pytest.mark.parametrize("bad", [None, {}, {"state": "failed", "by_ticker": {}},
                                 {"state": "ok"}, "x", {"state": "ok", "by_ticker": []}])
def test_xcheck_for_a_missing_or_failed_index_reads_failed(bad):
    out = ed.xcheck_for(bad, "CRWD")
    assert out["state"] == "failed" and out["upcoming"] == [] and out["reported"] is None


def test_xcheck_for_failed_result_is_a_fresh_dict_each_time():
    a = ed.xcheck_for(None, "A")
    a["upcoming"].append("2026-01-01")
    assert ed.xcheck_for(None, "A")["upcoming"] == []


# ═══ display ═════════════════════════════════════════════════════════════════

def _date_res(offset):
    return {"state": "date", "date": _d(offset), "source": "yahoo", "reason": None,
            "yahoo_date": _d(offset), "last_reported": None, "disagree": None}


@pytest.mark.parametrize("days, urgency", [
    (0, "imminent"),
    (C.EARNINGS_IMMINENT_DAYS, "imminent"),
    (C.EARNINGS_IMMINENT_DAYS + 1, "soon"),
    (C.EARNINGS_URGENCY_SOON_DAYS, "soon"),
    (C.EARNINGS_URGENCY_SOON_DAYS + 1, "ahead"),
])
def test_display_urgency_boundaries(days, urgency):
    out = ed.display(_date_res(days), TODAY)
    assert out["urgency"] == urgency and out["days"] == days
    assert out["date_text"] == _d(days)


def test_display_a_date_that_passed_since_the_data_loaded_is_unconfirmed():
    out = ed.display(_date_res(-1), TODAY)
    assert out["urgency"] == "unconfirmed" and out["days"] == -1
    assert "has passed since the data loaded" in out["note"]


def test_display_reported_and_reported_unconfirmed():
    rep = ed.display(_res(yahoo=[], xc=_xc(reported="2026-11-19")), TODAY)
    assert rep["urgency"] == "reported" and rep["days"] is None
    assert rep["note"] == "reported on Thu Nov 19; the next date isn't on the calendar yet"
    old = ed.display(_res(yahoo=["2026-10-20"]), TODAY)
    assert old["urgency"] == "reported"
    assert old["note"] == "reported on Tue Oct 20; the next date isn't announced yet"


def test_display_failed_reasons_unconfirmed():
    pu = ed.display(_res(yahoo=["2026-11-23"]), TODAY)
    assert pu["urgency"] == "unconfirmed"
    assert pu["note"] == ("the earnings date on file (Mon Nov 23) has passed and the next "
                          "one couldn't be confirmed")
    pc = ed.display(_res(yahoo=["2026-11-23"], xc=_xc(state="failed")), TODAY)
    assert pc["note"] == ("the earnings date on file (Mon Nov 23) has passed and the "
                          "earnings calendar couldn't be read to confirm the next one")
    sc = ed.display(_res(stale=True), TODAY)
    assert sc["note"] == "a saved copy of the data can't show the earnings date"
    rf = ed.display(_res(yahoo=[], status="failed"), TODAY)
    assert rf["note"] == "the next earnings date couldn't be read"


def test_display_disagreement_note_and_the_quiet_states():
    d = ed.display(_res(yahoo=[_d(8)], xc=_xc(upcoming=[_d(4)])), TODAY)
    assert d["urgency"] == "imminent" and d["date_text"] == _d(4)
    assert d["note"].startswith("Yahoo shows ") and "the earlier date is used." in d["note"]
    nd = ed.display(_res(yahoo=[], status="no_date"), TODAY)
    assert (nd["urgency"], nd["note"]) == ("none", "no earnings date announced yet")
    na = ed.display(_res(fund=True), TODAY)
    assert na["urgency"] == "n/a" and na["date_text"] == "n/a"
    for junk in (None, "x", {}, {"state": "weird"}):
        assert ed.display(junk, TODAY)["urgency"] == "none"


def test_display_texts_never_carry_dollar_or_bold_markers():
    cases = [_res(yahoo=["2026-11-23"]), _res(yahoo=["2026-11-23"], xc=_xc(state="failed")),
             _res(yahoo=[], xc=_xc(reported="2026-11-19")), _res(yahoo=["2026-10-20"]),
             _res(yahoo=[_d(8)], xc=_xc(upcoming=[_d(4)])), _res(stale=True),
             _res(yahoo=[], status="no_date")]
    for r in cases:
        note = ed.display(r, TODAY)["note"] or ""
        assert "$" not in note and "**" not in note
        line = ed.held_unconfirmed_line("CRWD", r, 9.5, 8.0) or ""
        assert "$" not in line and "**" not in line


def test_held_unconfirmed_line_wording_and_none_cases():
    r = _res(yahoo=["2026-11-23"])
    assert ed.held_unconfirmed_line("crwd", r, 9.54, 8.0) == (
        "Earnings date unconfirmed for CRWD: the date on file (Mon Nov 23) has passed and no "
        "next date could be confirmed. If it reports this week, its 9.5% is above your 8.0% "
        "earnings-week line; confirm the date on the company's investor-relations page.")
    assert ed.held_unconfirmed_line("CRWD", _res(yahoo=[_d(5)]), 9.5, 8.0) is None
    assert ed.held_unconfirmed_line("CRWD", None, 9.5, 8.0) is None
    assert ed.held_unconfirmed_line("CRWD", r, float("nan"), 8.0) is None
    assert ed.held_unconfirmed_line("CRWD", r, 9.5, None) is None
    assert ed.held_unconfirmed_line("", r, 9.5, 8.0) is None


# ═══ earnings_xcheck ═════════════════════════════════════════════════════════

def _rows(*triples):
    """(ticker, iso date, eps_actual) -> provider-shaped rows."""
    return [{"ticker": t, "date": d, "when": "", "eps_actual": e} for t, d, e in triples]


def _install(monkeypatch, past=(), future=(), calls=None, fail_on=None, clock=None):
    calls = calls if calls is not None else []

    def _fetch(frm, to):
        calls.append((frm, to))
        if fail_on is not None and len(calls) == fail_on:
            raise RuntimeError("fmp down")
        return list(future) if frm == TODAY.isoformat() else list(past)

    monkeypatch.setattr(ex, "_fetch_rows", _fetch)
    if clock is not None:
        monkeypatch.setattr(ex, "_clock", lambda: clock[0])
    return calls


def test_conftest_default_seam_raises_and_the_index_fails_without_raising():
    with pytest.raises(RuntimeError):
        ex._fetch_rows("2026-11-01", "2026-11-02")
    idx = ex.calendar_index(TODAY)
    assert idx["state"] == "failed" and idx["by_ticker"] == {}
    assert "real FMP earnings-calendar call in a test" in idx["error"]


def test_the_two_windows_are_the_30_day_past_and_90_day_future(monkeypatch):
    calls = _install(monkeypatch, past=[], future=[])
    ex.calendar_index(TODAY)
    assert calls == [
        (_d(-C.EARNINGS_REPORTED_CLEAR_DAYS), _d(-1)),
        (_d(0), _d(C.EARNINGS_XCHECK_HORIZON_DAYS)),
    ]


def test_index_builds_upcoming_and_reported_per_ticker(monkeypatch):
    _install(monkeypatch,
             past=_rows(("NVDA", "2026-11-17", 1.30), ("TGT", "2026-11-18", 2.10),
                        ("NVDA", "2026-11-05", 0.9), ("XYZ", "2026-11-10", None)),
             future=_rows(("CRWD", "2026-12-01", None), ("crwd", "2026-12-01", None),
                          ("SNOW", "2026-12-02", None), ("FAR", _d(C.EARNINGS_XCHECK_HORIZON_DAYS + 5), None)))
    idx = ex.calendar_index(TODAY)
    assert idx["state"] == "ok" and idx["error"] is None and idx["as_of_day"] == TODAY.isoformat()
    by = idx["by_ticker"]
    assert by["CRWD"] == {"upcoming": ["2026-12-01"], "reported": None}
    assert by["SNOW"]["upcoming"] == ["2026-12-02"]
    assert by["NVDA"]["reported"] == "2026-11-17"            # the latest report with an actual
    assert by["TGT"]["reported"] == "2026-11-18"
    assert "XYZ" not in by or by["XYZ"]["reported"] is None  # no actual -> never "reported"
    assert "FAR" not in by                                     # beyond the horizon
    assert idx["counts"]["reported"] == 3 and idx["counts"]["upcoming"] == 3


@pytest.mark.parametrize("eps", [None, float("nan"), float("inf"), True, "n/a"])
def test_a_row_without_a_finite_actual_never_sets_reported(monkeypatch, eps):
    _install(monkeypatch, past=_rows(("NVDA", "2026-11-17", eps)), future=[])
    idx = ex.calendar_index(TODAY)
    assert idx["state"] == "ok"
    assert idx["by_ticker"].get("NVDA", {}).get("reported") is None


def test_a_zero_actual_is_a_real_actual(monkeypatch):
    _install(monkeypatch, past=_rows(("NVDA", "2026-11-17", 0.0)), future=[])
    assert ex.calendar_index(TODAY)["by_ticker"]["NVDA"]["reported"] == "2026-11-17"


def test_past_rows_outside_the_window_are_ignored(monkeypatch):
    n = C.EARNINGS_REPORTED_CLEAR_DAYS
    _install(monkeypatch, past=_rows(("OLD", _d(-n - 1), 1.0), ("EDGE", _d(-n), 1.0),
                                     ("TODAYROW", _d(0), 1.0)), future=[])
    by = ex.calendar_index(TODAY)["by_ticker"]
    assert "OLD" not in by and "TODAYROW" not in by
    assert by["EDGE"]["reported"] == _d(-n)


@pytest.mark.parametrize("fail_on", [1, 2])
def test_one_failed_call_fails_the_whole_index(monkeypatch, fail_on):
    _install(monkeypatch, past=_rows(("NVDA", "2026-11-17", 1.0)),
             future=_rows(("CRWD", "2026-12-01", None)), fail_on=fail_on)
    idx = ex.calendar_index(TODAY)
    assert idx["state"] == "failed" and idx["by_ticker"] == {}      # never a partial index
    assert "fmp down" in idx["error"]


def test_a_none_or_non_list_answer_fails_the_index_never_an_empty_one(monkeypatch):
    for bad in (None, {"a": 1}, "x"):
        ex.reset_cache()
        monkeypatch.setattr(ex, "_fetch_rows", lambda f, t, _b=bad: _b)
        idx = ex.calendar_index(TODAY)
        assert idx["state"] == "failed" and idx["by_ticker"] == {}


def test_an_exception_never_escapes(monkeypatch):
    def _boom(f, t):
        raise ValueError("weird")
    monkeypatch.setattr(ex, "_fetch_rows", _boom)
    assert ex.calendar_index(TODAY)["state"] == "failed"


def test_the_real_seam_raises_when_no_provider_answers(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "earnings_xcheck_fresh", ROOT / "stock_analyzer" / "earnings_xcheck.py")
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    monkeypatch.setattr(orch, "get_earnings_calendar_rows_or_none", lambda a, b: None)
    with pytest.raises(RuntimeError, match="FMP_API_KEY"):
        fresh._fetch_rows("2026-11-01", "2026-11-02")
    monkeypatch.setattr(orch, "get_earnings_calendar_rows_or_none", lambda a, b: [{"x": 1}])
    assert fresh._fetch_rows("a", "b") == [{"x": 1}]


# ── cache ────────────────────────────────────────────────────────────────────

def test_a_good_index_is_cached_for_the_ttl_then_refetched(monkeypatch):
    clk = [1000.0]
    calls = _install(monkeypatch, past=[], future=[], clock=clk)
    first = ex.calendar_index(TODAY)
    n = len(calls)
    assert n == 2
    clk[0] += C.EARNINGS_XCHECK_CACHE_TTL_SEC - 1
    assert ex.calendar_index(TODAY) is first and len(calls) == n
    clk[0] += 2
    ex.calendar_index(TODAY)
    assert len(calls) == 2 * n


def test_a_failed_index_is_retried_after_the_fail_retry_not_before(monkeypatch):
    clk = [1000.0]
    calls = _install(monkeypatch, past=[], future=[], clock=clk, fail_on=1)
    assert ex.calendar_index(TODAY)["state"] == "failed"
    n = len(calls)
    clk[0] += C.EARNINGS_XCHECK_FAIL_RETRY_SEC - 1
    assert ex.calendar_index(TODAY)["state"] == "failed" and len(calls) == n   # still cached
    clk[0] += 2
    idx = ex.calendar_index(TODAY)
    assert len(calls) > n and idx["state"] == "ok"      # the retry reaches the (now good) seam


def test_the_cache_is_keyed_by_the_et_day(monkeypatch):
    clk = [1000.0]
    calls = _install(monkeypatch, past=[], future=[], clock=clk)
    ex.calendar_index(TODAY)
    n = len(calls)
    ex.calendar_index(TODAY)
    assert len(calls) == n
    ex.calendar_index(TODAY + timedelta(days=1))        # ET midnight passed: refetch
    assert len(calls) == 2 * n


def test_the_default_day_is_the_et_date_even_when_utc_is_a_day_ahead(monkeypatch):
    """23:30 ET on 2026-11-26 is already 11-27 in UTC (UTC-03:00-like clock)."""
    clk = [1000.0]
    seen = []

    def _fetch(frm, to):
        seen.append((frm, to))
        return []
    monkeypatch.setattr(ex, "_fetch_rows", _fetch)
    monkeypatch.setattr(ex, "_clock", lambda: clk[0])
    monkeypatch.setattr(ex, "today_et", lambda: date(2026, 11, 26))
    idx = ex.calendar_index()
    assert idx["as_of_day"] == "2026-11-26"
    assert seen[1][0] == "2026-11-26"
    monkeypatch.setattr(ex, "today_et", lambda: date(2026, 11, 27))   # ET midnight
    assert ex.calendar_index()["as_of_day"] == "2026-11-27" and len(seen) == 4


@pytest.mark.parametrize("day", [date(2026, 11, 1), date(2027, 3, 14)])
def test_dst_change_days_are_ordinary_cache_days(monkeypatch, day):
    clk = [1000.0]
    calls = []

    def _fetch(frm, to):
        calls.append((frm, to))
        return []
    monkeypatch.setattr(ex, "_fetch_rows", _fetch)
    monkeypatch.setattr(ex, "_clock", lambda: clk[0])
    ex.calendar_index(day)
    ex.calendar_index(day)
    assert len(calls) == 2                                  # one build, two windows
    assert calls[1][0] == day.isoformat()
    ex.calendar_index(day + timedelta(days=1))
    assert len(calls) == 4


def test_the_lock_makes_concurrent_callers_share_one_fetch(monkeypatch):
    calls = []

    def _slow(frm, to):
        calls.append((frm, to))
        time.sleep(0.05)
        return []
    monkeypatch.setattr(ex, "_fetch_rows", _slow)
    out = []

    def _go():
        out.append(ex.calendar_index(TODAY))
    threads = [threading.Thread(target=_go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 2                                  # past + future, once in total
    assert len(out) == 8 and all(o is out[0] for o in out) and out[0]["state"] == "ok"


def test_last_index_and_log_line(monkeypatch):
    assert ex.last_index() is None
    assert ex.log_line() == "earnings cross-check: not run in this process"
    _install(monkeypatch, past=_rows(("NVDA", "2026-11-17", 1.0)),
             future=_rows(("CRWD", "2026-12-01", None), ("SNOW", "2026-12-02", None)))
    idx = ex.calendar_index(TODAY)
    assert ex.last_index() is idx
    line = ex.log_line()
    assert line.startswith("earnings cross-check: ok, 2 upcoming / 1 reported rows (fetched ")
    assert line.endswith(" ET)")
    failed = {"state": "failed", "error": "RuntimeError: fmp down", "fetched_at": "x"}
    assert ex.log_line(failed) == (
        "earnings cross-check: couldn't be read (RuntimeError: fmp down); names whose date "
        f"on file passed in the last {C.EARNINGS_PAST_DATE_HOLD_DAYS} days read unconfirmed")
    assert ex.log_line("garbage") == "earnings cross-check: not run in this process"


# ═══ providers ═══════════════════════════════════════════════════════════════

def test_fmp_earnings_calendar_adds_eps_actual_additively():
    p = fp.FMPProvider.__new__(fp.FMPProvider)
    p._key = "K"
    p._info_cache = {}
    payload = [
        {"symbol": "nvda", "date": "2026-11-17", "epsActual": 1.3, "epsEstimated": 1.2,
         "time": "AMC", "lastUpdated": "x"},
        {"symbol": "CRWD", "date": "2026-12-01 00:00:00", "epsActual": None},
        {"symbol": "BAD", "date": "2026-11-10", "epsActual": "n/a"},
        {"symbol": "NAN", "date": "2026-11-10", "epsActual": float("nan")},
        {"symbol": "", "date": "2026-11-10"},
        {"symbol": "NODATE"},
    ]
    p._get_json = lambda path, params=None: payload
    out = p.earnings_calendar("2026-11-01", "2026-12-31")
    assert [r["ticker"] for r in out] == ["NVDA", "CRWD", "BAD", "NAN"]
    assert out[0] == {"ticker": "NVDA", "date": "2026-11-17", "when": "amc", "eps_actual": 1.3}
    assert out[1]["eps_actual"] is None and out[1]["date"] == "2026-12-01"
    assert out[2]["eps_actual"] is None and out[3]["eps_actual"] is None


def _cal_provider(behaviour):
    class _Cal(DataProvider):
        name = "cal"

        def is_configured(self):
            return True

        def earnings_calendar(self, a, b):
            if isinstance(behaviour, Exception):
                raise behaviour
            return behaviour
    return _Cal


def test_orchestrator_rows_or_none(monkeypatch):
    monkeypatch.setattr(C, "DATA_EARNINGS_CALENDAR_ORDER", ["cal"])
    monkeypatch.setattr(orch, "PROVIDER_REGISTRY", {"cal": _cal_provider([{"ticker": "X"}])})
    assert orch.get_earnings_calendar_rows_or_none("a", "b") == [{"ticker": "X"}]
    orch.reset()
    monkeypatch.setattr(orch, "PROVIDER_REGISTRY", {"cal": _cal_provider([])})
    assert orch.get_earnings_calendar_rows_or_none("a", "b") == []     # read, nothing listed
    orch.reset()
    monkeypatch.setattr(orch, "PROVIDER_REGISTRY", {"cal": _cal_provider(RuntimeError("x"))})
    assert orch.get_earnings_calendar_rows_or_none("a", "b") is None   # failure is None, never []
    orch.reset()
    monkeypatch.setattr(orch, "PROVIDER_REGISTRY", {"cal": _cal_provider({"not": "a list"})})
    assert orch.get_earnings_calendar_rows_or_none("a", "b") is None
    orch.reset()
    monkeypatch.setattr(C, "DATA_EARNINGS_CALENDAR_ORDER", ["missing"])
    assert orch.get_earnings_calendar_rows_or_none("a", "b") is None


def _fake_yf_ticker(cal):
    class _T:
        def __init__(self, t):
            pass

        def history(self, period="6mo"):
            idx = pd.date_range("2024-01-01", periods=30, freq="D")
            c = [100.0 + i for i in range(30)]
            return pd.DataFrame({"Close": c, "High": [x + 1 for x in c],
                                 "Low": [x - 1 for x in c], "Open": c,
                                 "Volume": [1_000_000.0] * 30}, index=idx)

        info = {"sector": "Technology", "longBusinessSummary": "x"}
        upgrades_downgrades = None
        calendar = cal
    return _T


def test_yahoo_bundle_carries_every_parseable_date_and_keeps_the_first(monkeypatch):
    monkeypatch.setattr(yp.yf, "Ticker", _fake_yf_ticker(
        {"Earnings Date": ["2026-11-23", "2026-12-01 16:00:00", "soon"]}))
    monkeypatch.setattr(yp.YFinanceProvider, "search_news_with_names",
                        lambda self, t, keep=None: ([], "ok", []))
    b = yp.YFinanceProvider().bundle("ACME")
    assert b["earnings"] == "2026-11-23" and b["earnings_status"] == "date"
    assert b["earnings_dates"] == ["2026-11-23", "2026-12-01"]


@pytest.mark.parametrize("cal", [{"Earnings Date": []}, {}, None, {"Earnings Date": None}])
def test_yahoo_bundle_earnings_dates_is_empty_when_no_date(monkeypatch, cal):
    monkeypatch.setattr(yp.yf, "Ticker", _fake_yf_ticker(cal))
    monkeypatch.setattr(yp.YFinanceProvider, "search_news_with_names",
                        lambda self, t, keep=None: ([], "ok", []))
    assert yp.YFinanceProvider().bundle("ACME")["earnings_dates"] == []


def test_next_earnings_is_the_earliest_future_date_never_a_past_one(monkeypatch):
    # dates far from "today" keep this independent of the clock
    monkeypatch.setattr(yp.yf, "Ticker", _fake_yf_ticker(
        {"Earnings Date": ["2000-01-05", "2099-03-02", "2099-02-01"]}))
    assert yp.YFinanceProvider().next_earnings("ACME") == "2099-02-01"
    monkeypatch.setattr(yp.yf, "Ticker", _fake_yf_ticker({"Earnings Date": ["2000-01-05"]}))
    assert yp.YFinanceProvider().next_earnings("ACME") is None
    monkeypatch.setattr(yp.yf, "Ticker", _fake_yf_ticker({"Earnings Date": []}))
    assert yp.YFinanceProvider().next_earnings("ACME") is None


def _fallback_registry(monkeypatch, primary_bundle, fallback_earnings):
    hist = pd.DataFrame({"Close": [1.0, 2.0]})

    class _Yahoo(DataProvider):
        name = "yahoo_finance"
        capabilities = frozenset({CAP_BUNDLE})

        def is_configured(self):
            return True

        def bundle(self, ticker, period="6mo"):
            return dict({"history": hist, "info": {"marketCap": 1e9}, "news": [],
                         "revisions": {"x": 1}}, **primary_bundle)

    class _Other(DataProvider):
        name = "finnhub"
        capabilities = frozenset({CAP_BUNDLE})

        def is_configured(self):
            return True

        def earnings(self, ticker):
            return fallback_earnings

    monkeypatch.setattr(orch, "PROVIDER_REGISTRY",
                        {"yahoo_finance": _Yahoo, "finnhub": _Other})
    monkeypatch.setattr(orch._ah, "in_cooldown", lambda *a, **k: False)
    monkeypatch.setattr(orch._ah, "get_fmp_daily_quota", lambda: 0)
    monkeypatch.setattr(orch._ah, "record", lambda *a, **k: None)
    orch.reset()


def test_fallback_stamps_date_only_for_a_value_that_parses(monkeypatch):
    _fallback_registry(monkeypatch, {"earnings": None, "earnings_status": "failed"},
                       "2026-12-01 16:00:00")
    b = orch.get_bundle("ABC")
    assert b["earnings_status"] == "date"
    _fallback_registry(monkeypatch, {"earnings": None, "earnings_status": "failed"}, "soon")
    b = orch.get_bundle("ABC")
    assert b["earnings_status"] == "failed"


# ═══ bundle_loader: the producer pin ═════════════════════════════════════════

def _price_df(n=60):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    c = [100 + i * 0.3 for i in range(n)]
    return pd.DataFrame({"Close": c, "High": [x + 1 for x in c],
                         "Low": [x - 1 for x in c], "Open": c,
                         "Volume": [1_000_000.0] * n}, index=idx)


def _db(bundle_cache=None):
    class _Db:
        save_bundle_cache = staticmethod(lambda *a, **k: None)
        load_bundle_cache = staticmethod(lambda *a, **k: bundle_cache)
        save_fundamentals_cache = staticmethod(lambda *a, **k: None)
        load_fundamentals_cache = staticmethod(lambda *a, **k: None)
        load_analyst_coverage = staticmethod(lambda *a, **k: None)
        load_analyst_coverage_or_none = staticmethod(lambda *a, **k: None)
        save_sector_cache = staticmethod(lambda *a, **k: None)
        load_sector_cache = staticmethod(lambda *a, **k: "")
        save_etf_lookthrough_cache = staticmethod(lambda *a, **k: None)
        load_etf_lookthrough_cache = staticmethod(lambda *a, **k: None)
        load_sentiment_llm_cache = staticmethod(lambda *a, **k: None)
        save_sentiment_llm_cache = staticmethod(lambda *a, **k: None)
    return _Db


def _setup_producer(monkeypatch, *, info=None, bundle_extra=None, today=TODAY, cache=None,
                    past=(), future=(), fetch_raises=False):
    monkeypatch.setattr(bundle_loader, "db", _db(cache))
    monkeypatch.setattr(bundle_loader, "today_et", lambda: today)

    def _fetch(ticker, period):
        if fetch_raises:
            raise RuntimeError("providers down")
        return dict({"history": _price_df(), "info": info if info is not None
                     else {"sector": "Technology"}, "news": [], "earnings": None,
                     "revisions": {}, "_info_source": "t"}, **(bundle_extra or {}))
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle", _fetch)
    cal_calls = []

    def _cal(frm, to):
        cal_calls.append((frm, to))
        return list(future) if frm == today.isoformat() else list(past)
    monkeypatch.setattr(ex, "_fetch_rows", _cal)
    # a fund bundle would otherwise reach for the live look-through
    monkeypatch.setattr(bundle_loader, "fetch_etf_lookthrough", lambda t: None)
    return cal_calls


def test_producer_crwd_reads_the_fmp_date_everywhere(monkeypatch):
    _setup_producer(monkeypatch,
                    bundle_extra={"earnings": "2026-11-23", "earnings_dates": ["2026-11-23"],
                                  "earnings_status": "date"},
                    future=_rows(("CRWD", "2026-12-01", None)))
    out = bundle_loader.load_bundle("CRWD")
    assert out["earnings"] == "2026-12-01"
    assert out["earnings_yahoo"] == "2026-11-23"
    assert out["earnings_status"] == "date"                  # the raw provider stamp, unchanged
    assert out["earnings_resolved"]["state"] == "date"
    assert out["earnings_resolved"]["source"] == "fmp"
    assert bcs.earnings_state(out) == {"state": "date", "date": "2026-12-01", "reason": None}


def test_producer_a_past_yahoo_date_never_reaches_bundle_earnings(monkeypatch):
    _setup_producer(monkeypatch,
                    bundle_extra={"earnings": "2026-11-17", "earnings_dates": ["2026-11-17"],
                                  "earnings_status": "date"})
    out = bundle_loader.load_bundle("NVDA")
    assert out["earnings"] is None and out["earnings_yahoo"] == "2026-11-17"
    assert out["earnings_resolved"]["state"] == "failed"
    assert out["earnings_resolved"]["reason"] == "past_unconfirmed"
    assert bcs.earnings_state(out)["reason"] == "past_unconfirmed"


def test_producer_nvda_reported_with_an_actual_passes(monkeypatch):
    _setup_producer(monkeypatch,
                    bundle_extra={"earnings": "2026-11-17", "earnings_dates": ["2026-11-17"],
                                  "earnings_status": "date"},
                    past=_rows(("NVDA", "2026-11-17", 1.3)))
    out = bundle_loader.load_bundle("NVDA")
    assert out["earnings"] is None
    assert out["earnings_resolved"]["state"] == "reported"
    assert bcs.earnings_state(out)["state"] == "reported"


def test_producer_with_the_calendar_unreadable_holds_a_recent_past_date(monkeypatch):
    _setup_producer(monkeypatch,
                    bundle_extra={"earnings": "2026-11-23", "earnings_dates": ["2026-11-23"],
                                  "earnings_status": "date"})
    monkeypatch.setattr(ex, "_fetch_rows", lambda f, t: (_ for _ in ()).throw(RuntimeError("x")))
    out = bundle_loader.load_bundle("CRWD")
    assert out["earnings_resolved"]["reason"] == "past_unchecked"


def test_producer_stamps_a_future_yahoo_date_without_the_calendar(monkeypatch):
    _setup_producer(monkeypatch, bundle_extra={"earnings": "2026-12-02", "earnings_status": "date"})
    monkeypatch.setattr(ex, "_fetch_rows", lambda f, t: (_ for _ in ()).throw(RuntimeError("x")))
    out = bundle_loader.load_bundle("ACME")
    assert out["earnings"] == "2026-12-02" and out["earnings_resolved"]["source"] == "yahoo"


def test_producer_stamps_on_the_stale_copy_path(monkeypatch):
    cached = {"bundle": {"history": _price_df(), "info": {"sector": "Technology"}, "news": [],
                         "earnings": "2026-12-02", "revisions": {}, "earnings_status": "date"},
              "fetched_at": "2026-11-20"}
    calls = _setup_producer(monkeypatch, cache=cached, fetch_raises=True)
    out = bundle_loader.load_bundle("ACME")
    assert out["stale_as_of"] == "2026-11-20"
    assert out["earnings"] is None                      # a saved copy never proves a date
    assert out["earnings_resolved"]["state"] == "failed"
    assert out["earnings_resolved"]["reason"] == "stale_copy"
    assert calls == []                                  # and costs no calendar read


def test_producer_stamps_read_failed_when_the_resolver_raises(monkeypatch):
    _setup_producer(monkeypatch, bundle_extra={"earnings": "2026-12-02", "earnings_status": "date"})

    def _boom(**kw):
        raise RuntimeError("resolver broke")
    monkeypatch.setattr(bundle_loader.earnings_date, "resolve", _boom)
    out = bundle_loader.load_bundle("ACME")
    assert out["earnings"] is None
    assert out["earnings_resolved"]["state"] == "failed"
    assert out["earnings_resolved"]["reason"] == "read_failed"
    assert out["earnings_yahoo"] == "2026-12-02"


def test_producer_a_fund_is_not_applicable_and_costs_no_calendar_read(monkeypatch):
    calls = _setup_producer(monkeypatch, info={"sector": "", "quoteType": "ETF"},
                            bundle_extra={"earnings": "2026-12-02", "earnings_status": "date"})
    out = bundle_loader.load_bundle("VOO")
    assert out["earnings_resolved"]["state"] == "not_applicable" and out["earnings"] is None
    assert calls == []


def test_producer_a_known_fund_with_a_missing_quote_type_is_not_applicable(monkeypatch):
    calls = _setup_producer(monkeypatch, info={"sector": ""},
                            bundle_extra={"earnings": None, "earnings_status": "failed"})
    out = bundle_loader.load_bundle("XLE")
    assert out["earnings_resolved"]["state"] == "not_applicable"
    assert calls == []
    # a stock with the same missing quoteType is not waved through
    out2 = bundle_loader.load_bundle("ACME")
    assert out2["earnings_resolved"]["state"] == "failed"


def test_known_funds_cover_the_sector_and_factor_rosters():
    assert {"XLE", "SOXX", "IGV", "MTUM", "USMV"} <= set(bundle_loader._KNOWN_FUNDS)


def test_load_bundle_returns_the_new_keys_in_its_one_dict_literal():
    import ast
    src = (ROOT / "stock_analyzer" / "bundle_loader.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "load_bundle")
    returns = [n for n in ast.walk(fn)
               if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]
    assert len(returns) == 1
    keys = [k.value for k in returns[0].value.keys if isinstance(k, ast.Constant)]
    for k in ("earnings", "earnings_yahoo", "earnings_status", "earnings_resolved"):
        assert k in keys, k


def test_only_bundle_loader_uses_fetch_ticker_bundle():
    """AST / source pin: the resolved date is stamped in `load_bundle`, so no other
    production module may build a bundle straight from `fetch_ticker_bundle` (scripts
    are excepted). A second user would hand readers a raw, unresolved Yahoo date. The one
    known exception is app.py's Evidence Pack fetch (`_parallel_fetch_bundles`, which
    reads the raw bundle's `earnings` for display; sibling named in the C3a report, to be
    moved to the resolved date with the C3c display work): it is pinned to exactly that
    one site so a second one can't appear unnoticed."""
    import ast
    users = []
    files = [p for p in (ROOT / "stock_analyzer").rglob("*.py")] + [ROOT / "cron_runner.py"]
    for p in files:
        if p.name == "data.py" and p.parent.name == "stock_analyzer":
            continue            # the definition itself
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if ((isinstance(node, ast.Name) and node.id == "fetch_ticker_bundle"
                 and isinstance(node.ctx, ast.Load))
                    or (isinstance(node, ast.Attribute) and node.attr == "fetch_ticker_bundle")):
                users.append(p.name)
                break
    assert users == ["bundle_loader.py"], users
    app_src = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
    submits = [ln.strip() for ln in app_src.splitlines()
               if "ex.submit(fetch_ticker_bundle" in ln.split("#", 1)[0]]
    assert len(submits) == 1, submits


# ═══ buy_check_state.earnings_state reads the resolved stamp ═════════════════

def _b(**kw):
    return dict({"asset_type": "stock"}, **kw)


@pytest.mark.parametrize("resolved, expect", [
    ({"state": "date", "date": "2026-12-01"}, {"state": "date", "date": "2026-12-01", "reason": None}),
    ({"state": "reported", "date": None}, {"state": "reported", "date": None, "reason": None}),
    ({"state": "reported_unconfirmed", "date": None},
     {"state": "reported_unconfirmed", "date": None, "reason": None}),
    ({"state": "no_date", "date": None}, {"state": "no_date", "date": None, "reason": None}),
    ({"state": "not_applicable", "date": None},
     {"state": "not_applicable", "date": None, "reason": None}),
    ({"state": "failed", "date": None, "reason": "past_unconfirmed"},
     {"state": "failed", "date": None, "reason": "past_unconfirmed"}),
    ({"state": "failed", "date": None}, {"state": "failed", "date": None, "reason": "read_failed"}),
    ({"state": "date", "date": "garbage"}, {"state": "failed", "date": None, "reason": "read_failed"}),
    ({"state": "weird"}, {"state": "failed", "date": None, "reason": "read_failed"}),
])
def test_earnings_state_maps_the_resolved_stamp(resolved, expect):
    # an `earnings` value that disagrees proves the resolved stamp is what is read
    assert bcs.earnings_state(_b(earnings="2020-01-15", earnings_resolved=resolved)) == expect


def test_earnings_state_reported_states_are_passes_not_failures():
    for st in ("reported", "reported_unconfirmed"):
        assert bcs.earnings_state(_b(earnings_resolved={"state": st}))["state"] == st
    assert bcs.STATE_REPORTED == "reported"
    assert bcs.STATE_REPORTED_UNCONFIRMED == "reported_unconfirmed"


def test_earnings_state_without_a_stamp_keeps_the_c1_rules():
    assert bcs.earnings_state(_b(earnings="2026-12-02", earnings_status="date")) == {
        "state": "date", "date": "2026-12-02"}
    assert bcs.earnings_state(_b(earnings=None, earnings_status="no_date"))["state"] == "no_date"
    assert bcs.earnings_state(_b(earnings="2026-12-02", stale_as_of="x"))["state"] == "failed"
    # a non-dict stamp is ignored
    assert bcs.earnings_state(_b(earnings="2026-12-02", earnings_resolved="x"))["state"] == "date"


def test_earnings_state_an_etf_stays_not_applicable():
    out = bcs.earnings_state({"asset_type": "etf",
                              "earnings_resolved": {"state": "failed", "reason": "read_failed"}})
    assert out == {"state": "not_applicable", "date": None}


# ═══ cron log lines ══════════════════════════════════════════════════════════

def test_summary_splits_failed_by_reason_and_lists_no_date_names():
    out = hae.earnings_states_summary(
        {"A": "date", "B": "reported", "C": "reported_unconfirmed", "D": "no_date",
         "E": "failed", "F": "failed", "G": "failed", "H": "not_applicable", "I": "weird"},
        {"E": "past_unconfirmed", "F": "past_unchecked", "G": None})
    assert out["counts"] == {"not_applicable": 1, "date": 1, "reported": 1,
                             "reported_unconfirmed": 1, "no_date": 1, "failed": 4}
    assert out["failed_tickers"] == ["E", "F", "G", "I"]
    assert out["failed_by_reason"] == {"past_unchecked": ["F"], "past_unconfirmed": ["E"],
                                       "read_failed": ["G", "I"]}
    assert out["no_date_tickers"] == ["D"]


def test_summary_without_a_reason_map_files_failures_under_read_failed():
    out = hae.earnings_states_summary({"A": "failed"})
    assert out["failed_by_reason"] == {"read_failed": ["A"]}


def _log_lines(monkeypatch, payload):
    import cron_runner
    lines = []
    monkeypatch.setattr(cron_runner, "_log", lines.append)
    cron_runner._log_watchlist_earnings_read(payload)
    return lines


def test_cron_log_line_new_shape_with_reasons_and_the_cross_check_line(monkeypatch):
    es = hae.earnings_states_summary(
        {"AAA": "date", "BBB": "reported", "CCC": "reported_unconfirmed", "DDD": "no_date",
         "CRWD": "failed", "EEE": "failed", "FFF": "not_applicable"},
        {"CRWD": "past_unconfirmed", "EEE": "read_failed"})
    _install(monkeypatch, past=[], future=_rows(("CRWD", "2026-12-01", None)))
    ex.calendar_index(TODAY)
    lines = _log_lines(monkeypatch, {"earnings_states": es})
    assert lines[0] == ("watchlist earnings read: 1 date, 1 reported, 1 reported_unconfirmed, "
                        "1 no_date (DDD), 2 failed (past_unconfirmed: CRWD; read_failed: EEE), "
                        "1 n/a")
    assert lines[1].startswith("earnings cross-check: ok, 1 upcoming / 0 reported rows")


def test_cron_log_line_new_shape_without_failures_or_no_date_names(monkeypatch):
    es = hae.earnings_states_summary({"AAA": "date"})
    lines = _log_lines(monkeypatch, {"earnings_states": es})
    assert lines[0] == ("watchlist earnings read: 1 date, 0 reported, 0 reported_unconfirmed, "
                        "0 no_date, 0 failed, 0 n/a")


def test_cron_log_line_failed_cross_check(monkeypatch):
    es = hae.earnings_states_summary({"CRWD": "failed"}, {"CRWD": "past_unchecked"})
    _install(monkeypatch, past=[], future=[], fail_on=1)
    ex.calendar_index(TODAY)
    lines = _log_lines(monkeypatch, {"earnings_states": es})
    assert "past_unchecked: CRWD" in lines[0]
    assert lines[1].startswith("earnings cross-check: couldn't be read (")
    assert lines[1].endswith(f"passed in the last {C.EARNINGS_PAST_DATE_HOLD_DAYS} days read unconfirmed")


def test_cron_log_line_legacy_shape_is_unchanged_and_still_logs_the_cross_check(monkeypatch):
    payload = {"earnings_states": {
        "counts": {"not_applicable": 2, "date": 5, "no_date": 1, "failed": 2},
        "failed_tickers": ["XYZ", "ABC"]}}
    lines = _log_lines(monkeypatch, payload)
    assert lines[0] == "watchlist earnings read: 5 date, 1 no_date, 2 failed (XYZ, ABC), 2 n/a"
    assert lines[1].startswith("earnings cross-check:")


def test_cron_log_line_a_malformed_count_never_raises(monkeypatch):
    for bad in (None, "oops", {"earnings_states": None},
                {"earnings_states": {"counts": None, "failed_tickers": []}}):
        lines = _log_lines(monkeypatch, bad)
        assert lines and lines[0].startswith("watchlist earnings read:")


def test_cron_log_line_truncates_a_long_name_list(monkeypatch):
    names = {f"T{i:02d}": "failed" for i in range(13)}
    es = hae.earnings_states_summary(names, {k: "read_failed" for k in names})
    lines = _log_lines(monkeypatch, {"earnings_states": es})
    assert "read_failed: T00, T01, T02, T03, T04, T05, T06, T07, T08, T09, +3 more" in lines[0]


def test_math_isfinite_is_not_shadowed():
    # the eps helper in earnings_xcheck relies on math.isfinite
    assert ex._finite(0.0) and not ex._finite(math.nan) and not ex._finite(True)
