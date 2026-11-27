"""Beta-gate C3 (ScanCacheAnyAge): the one rule for whether the saved scan may
size today's new picks. Covers `scan_freshness` (decide / should_replace /
hold_banner / priced_at_label / tooltip + lead texts), `_grow_today` /
`build_daily_briefing` with `scan_state`, the cron intraday lane's use of it and
the app.py wiring pins. Boundaries come from `session_label.SESSION_OPEN`.
Spec: docs/plans/beta-gate-c3-spec.md.
"""
import ast
import inspect
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from stock_analyzer import scan_freshness as sf
from stock_analyzer import session_label
from stock_analyzer.daily_briefing import _grow_today
from stock_analyzer.session_label import SESSION_OPEN
from tests._ast_cache import parse_file_cached
from tests.conftest import make_port_df
from tests.test_beta_gate_grow_today import _calls, _etf_scn, _with_beta
from tests.test_daily_briefing import _scanner_df
from tests.test_grow_today_golden import _PCT, _TODAY, _bundle, _scn

pytestmark = pytest.mark.fast

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent

# 2026-11-25 is a Wednesday (trading day); 2026-11-26 Thanksgiving; 2026-11-27 early close.
WED = date(2026, 11, 25)
THU_HOLIDAY = date(2026, 11, 26)
FRI_EARLY = date(2026, 11, 27)
SAT = date(2026, 11, 28)
MON = date(2026, 11, 30)


def _et(d: date, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=ET)


def _meta(at: datetime | str | None) -> dict:
    return {"scan_date": "x", "source": "cron", "scanned_at": at}


def _utc_iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


# ── decide: the time-context matrix ──────────────────────────────────────────

def test_trading_day_scan_today_after_the_open_is_fresh():
    now = _et(WED, 10, 0)
    d = sf.decide(_meta(_et(WED, 9, 47)), now)
    assert (d["state"], d["why"]) == (sf.STATE_FRESH, "ok")
    assert d["scan_session"] == WED and d["next_session"] is None
    assert d["scanned_at_et"] == _et(WED, 9, 47)


def test_yesterdays_scan_during_the_session_is_stale_older_session():
    d = sf.decide(_meta(_et(WED - timedelta(days=1), 9, 47)), _et(WED, 9, 31))
    assert (d["state"], d["why"]) == (sf.STATE_STALE, "older_session")
    assert d["next_session"] == WED


def test_yesterdays_scan_before_the_open_is_stale_pre_open():
    d = sf.decide(_meta(_et(WED - timedelta(days=1), 9, 47)), _et(WED, 8, 0))
    assert (d["state"], d["why"]) == (sf.STATE_STALE, "pre_open")
    assert d["next_session"] == WED


def test_a_scan_saved_today_before_the_open_is_stale_before_open_scan():
    d = sf.decide(_meta(_et(WED, 7, 55)), _et(WED, 8, 0))
    assert (d["state"], d["why"]) == (sf.STATE_STALE, "before_open_scan")


def test_same_day_pre_open_scan_on_a_non_trading_day_reads_closed_day():
    # Sat 2026-11-28 12:00 with a scan saved Sat 07:55: the market is closed, so the
    # reason is closed_day (not before_open_scan); next scan is Monday.
    d = sf.decide(_meta(_et(SAT, 7, 55)), _et(SAT, 12, 0))
    assert (d["state"], d["why"]) == (sf.STATE_STALE, "closed_day")
    assert d["next_session"] == MON


def test_open_boundary_to_the_second():
    now = _et(WED, 12, 0)
    at_open = _et(WED, SESSION_OPEN.hour, SESSION_OPEN.minute, 0)
    assert sf.decide(_meta(at_open), now)["state"] == sf.STATE_FRESH
    just_before = at_open - timedelta(seconds=1)
    d = sf.decide(_meta(just_before), now)
    assert (d["state"], d["why"]) == (sf.STATE_STALE, "before_open_scan")


def test_before_open_scan_is_stale_even_after_the_open():
    # a pre-open Refresh Signals scan on yesterday's close must not size picks at 10:00
    d = sf.decide(_meta(_et(WED, 9, 29, 59)), _et(WED, 10, 0))
    assert (d["state"], d["why"]) == (sf.STATE_STALE, "before_open_scan")


@pytest.mark.parametrize("hh,mm", [(16, 1), (18, 0), (23, 59)])
def test_todays_scan_stays_fresh_after_hours_until_midnight(hh, mm):
    d = sf.decide(_meta(_et(WED, 9, 47)), _et(WED, hh, mm))
    assert (d["state"], d["why"]) == (sf.STATE_FRESH, "ok")


def test_saturday_with_fridays_scan_is_stale_closed_day_next_session_monday():
    d = sf.decide(_meta(_et(FRI_EARLY, 9, 47)), _et(SAT, 11, 0))
    assert (d["state"], d["why"]) == (sf.STATE_STALE, "closed_day")
    assert d["next_session"] == MON


def test_thanksgiving_with_wednesdays_scan_is_stale_closed_day_next_friday():
    d = sf.decide(_meta(_et(WED, 9, 47)), _et(THU_HOLIDAY, 12, 0))
    assert (d["state"], d["why"]) == (sf.STATE_STALE, "closed_day")
    assert d["next_session"] == FRI_EARLY


def test_early_close_day_with_a_post_open_scan_is_fresh():
    d = sf.decide(_meta(_et(FRI_EARLY, 9, 47)), _et(FRI_EARLY, 14, 0))
    assert d["state"] == sf.STATE_FRESH


def test_dst_edt_utc_string_converts_with_the_real_offset():
    # Mon 2026-11-02 is EST after the 11-01 change: 14:47Z = 09:47 EST (fresh);
    # 13:47Z = 08:47 EST (before the open).
    now = _et(date(2026, 11, 2), 15, 0)
    assert sf.decide(_meta("2026-11-02T14:47:00+00:00"), now)["state"] == sf.STATE_FRESH
    d = sf.decide(_meta("2026-11-02T13:47:00+00:00"), now)
    assert (d["state"], d["why"]) == (sf.STATE_STALE, "before_open_scan")


def test_dst_after_the_march_change_uses_edt_not_a_fixed_offset():
    # DST starts Sun 2026-03-08, so Mon 2026-03-09 is the first EDT Monday: 13:47Z =
    # 09:47 EDT (fresh at 10:00 ET), which would read 08:47 under a fixed EST offset.
    d = sf.decide(_meta("2026-03-09T13:47:00+00:00"), _et(date(2026, 3, 9), 10, 0))
    assert d["state"] == sf.STATE_FRESH, d
    # 2027's change is Sun 03-14, so Mon 2027-03-08 is still EST: the same 13:47Z is
    # 08:47 EST, before the open (the spec's "2027-03-08 = EDT" line is wrong).
    d2 = sf.decide(_meta("2027-03-08T13:47:00+00:00"), _et(date(2027, 3, 8), 10, 0))
    assert (d2["state"], d2["why"]) == (sf.STATE_STALE, "before_open_scan"), d2
    d3 = sf.decide(_meta("2027-03-15T13:47:00+00:00"), _et(date(2027, 3, 15), 10, 0))
    assert d3["state"] == sf.STATE_FRESH, d3


def test_trailing_z_and_pandas_timestamp_are_accepted():
    now = _et(WED, 10, 0)
    z = _utc_iso(_et(WED, 9, 47)).replace("+00:00", "Z")
    assert sf.decide(_meta(z), now)["state"] == sf.STATE_FRESH
    ts = pd.Timestamp(_et(WED, 9, 47))
    assert sf.decide(_meta(ts), now)["state"] == sf.STATE_FRESH


@pytest.mark.parametrize("meta", [
    None, {}, {"scanned_at": None}, {"scanned_at": ""}, "not a dict", [1],
    {"scanned_at": "garbage"},
    {"scanned_at": "2026-11-25"},                       # date-only (the old app meta)
    {"scanned_at": "2026-11-25T09:47:00"},              # naive string
    {"scanned_at": datetime(2026, 11, 25, 9, 47)},      # naive datetime
    {"scanned_at": date(2026, 11, 25)},                 # a date object
    {"scanned_at": 12345},
])
def test_missing_naive_or_unreadable_scanned_at_is_unknown(meta):
    d = sf.decide(meta, _et(WED, 10, 0))
    assert d["state"] == sf.STATE_UNKNOWN
    assert d["why"] in ("no_meta", "unreadable")
    assert d["scanned_at_et"] is None


def test_a_scan_from_the_future_is_unknown():
    now = _et(WED, 10, 0)
    assert sf.decide(_meta(now + timedelta(minutes=10)), now)["state"] == sf.STATE_UNKNOWN
    # within the 5-minute skew allowance it still reads
    assert sf.decide(_meta(now + timedelta(minutes=4)), now)["state"] == sf.STATE_FRESH


def test_naive_now_is_unknown():
    d = sf.decide(_meta(_et(WED, 9, 47)), datetime(2026, 11, 25, 10, 0))
    assert (d["state"], d["why"]) == (sf.STATE_UNKNOWN, "unreadable")
    assert sf.decide(_meta(_et(WED, 9, 47)), None)["state"] == sf.STATE_UNKNOWN


def test_decide_reads_no_clock():
    src = inspect.getsource(sf)
    assert ".now(" not in src and "today(" not in src and "utcnow" not in src
    # the answer is a function of its two arguments only: same inputs, same output
    a = sf.decide(_meta(_et(WED, 9, 47)), _et(WED, 10, 0))
    b = sf.decide(_meta(_et(WED, 9, 47)), _et(WED, 10, 0))
    assert a == b


def test_session_open_is_public_and_session_label_uses_it():
    assert session_label.SESSION_OPEN == time(9, 30)
    assert not hasattr(session_label, "_OPEN")
    assert session_label.price_move_session(_et(WED, SESSION_OPEN.hour, SESSION_OPEN.minute)) == (True, WED)


# ── should_replace ───────────────────────────────────────────────────────────

def test_should_replace_strictly_newer_only():
    old = _meta(_et(WED, 9, 47))
    new = _meta(_et(WED, 9, 48))
    assert sf.should_replace(old, new) is True
    assert sf.should_replace(new, old) is False
    assert sf.should_replace(old, _meta(_et(WED, 9, 47))) is False   # equal


def test_should_replace_unreadable_current_takes_any_readable_cached():
    cached = _meta(_et(WED, 9, 47))
    assert sf.should_replace({"scanned_at": "2026-11-25"}, cached) is True
    assert sf.should_replace(None, cached) is True
    assert sf.should_replace({}, cached) is True


def test_a_pre_open_user_refresh_is_replaced_by_the_later_cron_scan():
    # Refresh Signals at 09:28 (from_cache False) is held all day unless the 09:45 cron
    # scan in scanner_cache replaces it; a user scan newer than the cache is kept.
    user = {"scanned_at": _utc_iso(_et(WED, 9, 28)), "from_cache": False}
    cron = {"scanned_at": _utc_iso(_et(WED, 9, 46)), "source": "cron"}
    assert sf.decide(user, _et(WED, 10, 0))["why"] == "before_open_scan"
    assert sf.should_replace(user, cron) is True
    assert sf.should_replace({"scanned_at": _utc_iso(_et(WED, 10, 5))}, cron) is False


def test_should_replace_unreadable_cached_never_replaces():
    cur = _meta(_et(WED, 9, 47))
    assert sf.should_replace(cur, {"scanned_at": "garbage"}) is False
    assert sf.should_replace(cur, None) is False
    assert sf.should_replace(None, None) is False


# ── wording ──────────────────────────────────────────────────────────────────

def _stale(why, scanned=None, nxt=None):
    scanned = scanned or _et(WED, 9, 47)
    return {"state": sf.STATE_STALE, "why": why, "scanned_at_et": scanned,
            "scan_session": scanned.date(), "next_session": nxt}


def test_banner_wording_for_each_why():
    open_txt = "9:30 AM"
    assert f"{SESSION_OPEN.hour}:{SESSION_OPEN.minute:02d} AM" == open_txt
    older = sf.hold_banner(_stale("older_session"))
    assert older.startswith("⏸ No new-position sizes yet today. The latest saved scan is from "
                            "Wed Nov 25, 9:47 AM ET, not today's session")
    assert "(about 9:45 AM ET)" in older and "🔄 Refresh Signals" in older
    before = sf.hold_banner(_stale("before_open_scan", _et(WED, 7, 55)))
    assert "ran at Wed Nov 25, 7:55 AM ET, before the 9:30 AM ET open" in before
    pre = sf.hold_banner(_stale("pre_open"))
    assert pre.startswith("⏸ No new-position sizes before the open. The market opens at 9:30 AM ET;")
    assert "Wed Nov 25, 9:47 AM ET" in pre
    closed = sf.hold_banner(_stale("closed_day", nxt=MON))
    assert closed == ("⏸ Market closed today, so no new-position sizes. The latest saved scan is "
                      "from Wed Nov 25, 9:47 AM ET; next scan Mon Nov 30 about 9:45 AM ET.")
    closed_none = sf.hold_banner(_stale("closed_day", nxt=None))
    assert "next scan at the next session's open" in closed_none


def test_banner_unknown_wording_and_fail_closed_shapes():
    unk = sf.hold_banner(sf.decide({"scanned_at": "garbage"}, _et(WED, 10, 0)))
    assert unk.startswith("⏸ No new-position sizes: couldn't tell when the saved scan ran.")
    no_meta = sf.hold_banner(sf.decide(None, _et(WED, 10, 0)))
    assert no_meta == ("⏸ No new-position sizes: no saved scan to read yet. New picks return "
                       "after today's scan (about 9:45 AM ET) or a 🔄 Refresh Signals once the "
                       "market is open.")
    # a stale decision that cannot name its scan reads the unknown wording, never None
    assert sf.hold_banner({"state": sf.STATE_STALE, "why": "older_session"}) == unk
    assert sf.hold_banner({"state": "garbage"}) == unk


def test_banner_none_when_fresh_or_not_a_dict():
    fresh = sf.decide(_meta(_et(WED, 9, 47)), _et(WED, 10, 0))
    assert sf.hold_banner(fresh) is None
    assert sf.hold_banner(None) is None and sf.hold_banner("x") is None


def test_no_dollar_signs_and_no_zero_padded_hour_in_any_text():
    texts = [sf.hold_banner(_stale(w, _et(WED, 9, 5), MON))
             for w in ("older_session", "before_open_scan", "pre_open", "closed_day")]
    texts.append(sf.hold_banner(None) or "")
    texts.append(sf.hold_banner({"state": "unknown", "why": "no_meta"}))
    texts.append(sf.priced_at_label(sf.decide(_meta(_et(WED, 13, 5)), _et(WED, 14, 0))))
    texts.append(sf.signals_tooltip(_stale("older_session", _et(WED, 9, 5))))
    texts.append(sf.leads_heading({"scan_session": "2026-11-25"}, 3))
    texts.append(sf.leads_note({"scan_session": "2026-11-25"}))
    for t in texts:
        assert "$" not in t
        assert " 09:" not in t and ", 09:" not in t
    assert "9:05 AM ET" in texts[0]


def test_priced_at_label_fresh_only():
    fresh = sf.decide(_meta(_et(WED, 9, 47)), _et(WED, 10, 0))
    assert sf.priced_at_label(fresh) == "Sizes priced at the 9:47 AM ET scan."
    assert sf.priced_at_label(_stale("older_session")) is None
    assert sf.priced_at_label(None) is None
    assert sf.priced_at_label({"state": sf.STATE_FRESH}) is None   # no readable time


def test_priced_at_label_afternoon_scan_uses_12h_clock():
    fresh = sf.decide(_meta(_et(WED, 13, 5)), _et(WED, 14, 0))
    assert sf.priced_at_label(fresh) == "Sizes priced at the 1:05 PM ET scan."
    noon = sf.decide(_meta(_et(WED, 12, 0)), _et(WED, 14, 0))
    assert sf.priced_at_label(noon) == "Sizes priced at the 12:00 PM ET scan."


def test_signals_tooltip_fresh_stale_and_unreadable():
    fresh = sf.decide(_meta(_et(WED, 9, 47)), _et(WED, 10, 0))
    assert sf.signals_tooltip(fresh, "cron") == (
        "Signals from today's auto-scan at 9:47 AM ET. Click to re-score on live prices intraday.")
    assert sf.signals_tooltip(fresh, "app").startswith("Signals from today's scan at 9:47 AM ET.")
    stale = sf.decide(_meta(_et(WED, 9, 47)), _et(THU_HOLIDAY, 12, 0))
    assert sf.signals_tooltip(stale, "cron") == (
        "Signals from the Wed Nov 25, 9:47 AM ET scan — click to score today's price action.")
    assert sf.signals_tooltip(sf.decide(None, _et(WED, 10, 0))) is None
    assert sf.signals_tooltip(None) is None


def test_leads_texts_name_the_scan_session():
    hold = {"scan_session": "2026-11-25"}
    assert sf.leads_heading(hold, 4) == "Momentum leads — from the Wed Nov 25 scan, not checked (4)"
    assert "from the Wed Nov 25 scan" in sf.leads_note(hold)
    assert sf.leads_heading({}, 2) == "Momentum leads — from the saved scan, not checked (2)"
    assert sf.leads_heading(None, 2) == "Momentum leads — from the saved scan, not checked (2)"


# ── _grow_today with scan_state ──────────────────────────────────────────────

FRESH = sf.decide(_meta(_et(WED, 9, 47)), _et(WED, 10, 0))
STALE = sf.decide(_meta(_et(WED - timedelta(days=1), 9, 47)), _et(WED, 10, 0))
UNKNOWN = sf.decide(None, _et(WED, 10, 0))


def _run(s, **kw):
    port_df = make_port_df(s["port_rows"]) if s["port_rows"] else None
    scanner = _scanner_df(s["rows"]) if s["rows"] else None
    ctx = {"tone": s["tone"], "sp500_pct": s["pct"], "nasdaq_pct": s["pct"] * 1.2,
           "leading_sectors": []}
    return _grow_today(
        port_df, scanner, s["news"], s["held_data"], _TODAY, 100_000.0, ctx,
        act_today=s["act_today"], review_list=s["review_list"],
        composites=s["composites"], earnings_lookup=s["earnings"],
        macro_events=s["macro"], movers=s["movers"], etf_candidates=s["etf"], **kw)


def _tk(items):
    return [i["ticker"] for i in items]


def _mover_scn(**o):
    comps = {"ZNEWX": _bundle(80.0), "ZCTRL": _bundle(80.0), "ZMOVE": _bundle(80.0)}
    movers = [{"ticker": "ZMOVE", "price": 100.0, "sector": "Utilities", "score": 50.0,
               "composite_score": 85.0, "day_change": 6.0}]
    return _scn(_PCT["bull"], composites=comps, movers=movers, **o)


def test_not_wired_leaves_every_key_and_adds_scan_hold_none():
    base = _run(_scn(_PCT["bull"]))
    assert base["scan_hold"] is None
    assert _tk(base["new_picks"])         # the fixture does produce picks


def test_fresh_scan_is_identical_to_not_wired_except_scan_hold_stays_none():
    a = _run(_mover_scn())
    b = _run(_mover_scn(), scan_state=FRESH)
    assert a == b and b["scan_hold"] is None


@pytest.mark.parametrize("state", [STALE, UNKNOWN])
def test_held_scan_sizes_no_pick_and_lists_every_scan_name_as_a_lead(state):
    grow = _run(_mover_scn(), scan_state=state)
    assert grow["new_picks"] == [] and grow["overflow_picks"] == []
    assert grow["flat_day_held"] == []
    assert grow["scan_hold"]["state"] == state["state"]
    assert grow["scan_hold"]["why"] == state["why"]
    assert grow["scan_hold"]["banner"] and grow["scan_hold"]["banner"].startswith("⏸")
    # the scan's qualifying names (and the mover, U8) are not evaluated at all
    for b in ("sector_blocked_picks", "macro_blocked_picks", "composite_skipped",
              "news_skipped", "earnings_waiting", "beta_blocked_picks"):
        assert grow[b] == [], b
    leads = {l["ticker"] for l in grow["unevaluated_leads"]}
    assert {"ZNEWX", "ZCTRL"} <= leads
    assert all(set(l) == {"ticker", "momentum_score"} for l in grow["unevaluated_leads"])
    assert "ZMOVE" not in _tk(grow["new_picks"])


def test_scan_hold_is_json_safe_strings():
    import json
    grow = _run(_mover_scn(), scan_state=STALE)
    json.dumps(grow["scan_hold"])
    h = grow["scan_hold"]
    assert h["scanned_at_et"] == STALE["scanned_at_et"].isoformat()
    assert h["scan_session"] == (WED - timedelta(days=1)).isoformat()
    assert h["next_session"] == WED.isoformat()


def test_a_non_dict_scan_state_reads_as_held():
    grow = _run(_mover_scn(), scan_state="garbage")
    assert grow["new_picks"] == []
    assert grow["scan_hold"]["why"] is None and grow["scan_hold"]["banner"]


def test_held_scan_leaves_the_etf_lane_alone():
    fresh = _run(_etf_scn(0.9), scan_state=FRESH)
    held = _run(_etf_scn(0.9), scan_state=STALE)
    assert "SPY" in _tk(fresh["new_picks"])
    assert "SPY" in _tk(held["new_picks"])


def test_held_scan_leaves_the_add_lanes_alone():
    from tests.test_grow_today_golden import _adds_scn
    kw = dict(news=[], earnings={}, act_today=None, review_list=None, held_data={})
    a = _run(_adds_scn(_PCT["bull"], **kw))
    b = _run(_adds_scn(_PCT["bull"], **kw), scan_state=STALE)
    a.pop("scan_hold"), b.pop("scan_hold")
    for k in ("add_positions", "risk_blocked_adds", "concentration_blocked_adds",
              "sector_blocked_adds", "entry_held_adds", "card_blocked_adds",
              "room_blocked_adds", "adds_paused"):
        assert a[k] == b[k], k


def test_held_scan_makes_no_g32_pick_source_or_budget_use():
    # no pick -> nothing for the G-32 shadow or the new-pick budget to size
    grow = _run(_mover_scn(), scan_state=STALE)
    assert grow["new_picks"] == []
    assert not any(i.get("sizing") for i in grow["unevaluated_leads"])


def test_bear_return_carries_scan_hold():
    s = _scn(_PCT["bear"])
    held = _run(s, scan_state=STALE)
    assert held["tone"] == "bear" and held["scan_hold"]["why"] == "older_session"
    assert _run(s, scan_state=FRESH)["scan_hold"] is None
    assert _run(s)["scan_hold"] is None


# ── cron intraday lane ───────────────────────────────────────────────────────

def _intraday(monkeypatch, scanned_at, *, now=None, force=False):
    import cron_runner as cr
    import stock_analyzer.intraday_entry as ie
    now = now or cr._ET.localize(datetime(2026, 10, 16, 11, 35))
    logs, emails, picks_calls = [], [], []
    monkeypatch.setattr(cr, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(cr.db, "load_scanner_cache",
                        lambda: {"df": pd.DataFrame({"Ticker": ["AAA"]}),
                                 "scan_date": now.date().isoformat(),
                                 "scanned_at": scanned_at})
    monkeypatch.setattr(cr, "_log", lambda m, *a, **k: logs.append(m))

    def _picks(**kw):
        picks_calls.append(kw)
        return {"picks": [], "errors": [], "built_at": "x", "budget_inputs": {},
                "book_drift": None, "macro_coverage": None}
    monkeypatch.setattr(cr, "compute_morning_picks", _picks)
    monkeypatch.setattr(cr, "_send_email", lambda *a, **k: emails.append(a) or True)
    monkeypatch.setattr(ie, "fetch_intraday_prices", lambda t: {})
    rc = cr._run_intraday(now, force=force)
    return rc, logs, emails, picks_calls


def test_intraday_skips_a_yesterday_scan_with_the_reason_logged_and_sends_nothing(monkeypatch):
    rc, logs, emails, picks = _intraday(monkeypatch, "2026-10-15T13:47:00+00:00")
    assert rc == 0 and emails == [] and picks == []
    line = next(l for l in logs if "scanner_cache not usable" in l)
    assert "older_session" in line and "scanned" in line and line.endswith("— skip.")


def test_intraday_skip_log_formats_the_scan_time_not_a_datetime_repr(monkeypatch):
    rc, logs, emails, picks = _intraday(monkeypatch, "2026-10-15T13:47:00+00:00")
    line = next(l for l in logs if "scanner_cache not usable" in l)
    assert "scanned Thu Oct 15, 9:47 AM ET)" in line and "datetime" not in line


def test_intraday_skips_a_pre_open_scan_saved_today(monkeypatch):
    # 08:47 EDT on the run day: today's date, but before the open
    rc, logs, emails, picks = _intraday(monkeypatch, "2026-10-16T12:47:00+00:00")
    assert picks == [] and any("before_open_scan" in l for l in logs)


def test_intraday_skips_a_cache_with_no_scanned_at(monkeypatch):
    rc, logs, emails, picks = _intraday(monkeypatch, None)
    assert picks == [] and any("no_meta" in l for l in logs)


def test_intraday_proceeds_on_todays_post_open_scan(monkeypatch):
    rc, logs, emails, picks = _intraday(monkeypatch, "2026-10-16T13:47:00+00:00")
    assert len(picks) == 1                       # reached compute_morning_picks
    assert not any("not usable" in l for l in logs)


def test_intraday_force_bypasses_the_freshness_rule(monkeypatch):
    rc, logs, emails, picks = _intraday(monkeypatch, "2026-10-15T13:47:00+00:00", force=True)
    assert len(picks) == 1


# ── app.py / daily_briefing wiring pins ──────────────────────────────────────

def test_every_build_daily_briefing_call_passes_scan_state():
    tree = parse_file_cached(ROOT / "app.py")
    calls = _calls(tree, "build_daily_briefing")
    assert len(calls) == 2
    for c in calls:
        kw = {k.arg: k.value for k in c.keywords}
        assert isinstance(kw.get("scan_state"), ast.Name), c.lineno
        assert kw["scan_state"].id == "_scan_decision", c.lineno


def test_build_daily_briefing_forwards_scan_state_to_grow_today():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "daily_briefing.py")
    bdb = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "build_daily_briefing")
    assert "scan_state" in {a.arg for a in bdb.args.kwonlyargs + bdb.args.args}
    gt = _calls(bdb, "_grow_today")
    assert len(gt) == 1
    kw = {k.arg: k.value for k in gt[0].keywords}
    assert isinstance(kw.get("scan_state"), ast.Name) and kw["scan_state"].id == "scan_state"


def test_headless_lanes_pass_no_scan_state():
    # the 09:45 scan lane is post-open by its lane guard; the intraday lane refuses a
    # non-fresh cache before calling (spec 4c)
    tree = parse_file_cached(ROOT / "stock_analyzer" / "headless_alert_engine.py")
    for c in _calls(tree, "build_daily_briefing"):
        assert "scan_state" not in {k.arg for k in c.keywords}


_APP = (ROOT / "app.py").read_text(encoding="utf-8")


def test_scanner_results_is_assigned_only_through_the_helper():
    tree = parse_file_cached(ROOT / "app.py")
    bad = []
    for n in ast.walk(tree):
        targets = []
        if isinstance(n, ast.Assign):
            targets = n.targets
        elif isinstance(n, ast.AugAssign):
            targets = [n.target]
        for t in targets:
            if isinstance(t, ast.Attribute) and t.attr == "scanner_results":
                v = n.value if isinstance(n, ast.Assign) else None
                if isinstance(v, ast.Constant) and v.value is None:
                    continue                                  # the initialiser
                bad.append(t.lineno)
            if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) \
                    and t.slice.value in ("scanner_results", "_scanner_results_meta"):
                bad.append(t.lineno)
    # exactly one real assignment of each: inside _set_scanner_results
    helper = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_set_scanner_results")
    lo, hi = helper.lineno, helper.end_lineno
    assert bad and all(lo <= ln <= hi for ln in bad), bad


def test_helper_is_used_at_all_three_sites_with_a_real_timestamp_and_from_cache():
    assert _APP.count("_set_scanner_results(") == 5   # def + hydrate + rehydrate + refresh + subset
    assert re.search(r'"from_cache": True', _APP) and re.search(r'"from_cache": False', _APP)
    assert '"source": "app_subset"' in _APP
    assert _APP.count('pd.Timestamp.now(tz="UTC").isoformat()') >= 2
    # the old bare-date scanned_at is gone
    assert '"scanned_at": _today_d.isoformat()' not in _APP


def test_rehydrate_rereads_any_non_fresh_scan_and_replaces_only_with_a_strictly_newer_one():
    assert "_SCAN_REHYDRATE_TTL_S = 120" in _APP
    i = _APP.index("_rh_meta = st.session_state.get(")
    block = _APP[i:i + 1400]
    # any source, and no meta at all, re-reads whenever the scan isn't fresh ...
    assert 'from_cache' not in block.split("try:")[0]
    assert "scan_freshness.decide(_rh_meta, _now_et())" in block
    assert "!= scan_freshness.STATE_FRESH" in block
    # ... but only a strictly newer saved scan replaces it
    assert "scan_freshness.should_replace(_rh_meta, _rh_new_meta)" in block
    assert "except Exception" in block


def test_synth_sig_carries_the_scan_decision():
    i = _APP.index("_synth_sig = (")
    block = _APP[i:i + 1800]
    assert '(_scan_decision["state"], _scan_decision["why"])' in block
    assert "_scan_decision = scan_freshness.decide(" in _APP


def test_empty_state_caption_is_skipped_under_a_scan_hold():
    assert ("if not new_picks and not add_pos and not isinstance(_scan_hold_ui, dict):"
            in _APP)
    assert 'st.info(_scan_hold_txt.replace("$", "\\\\$"))' in _APP


def test_priced_at_caption_and_leads_heading_are_wired():
    assert "scan_freshness.priced_at_label(_scan_decision)" in _APP
    assert "scan_freshness.leads_heading(" in _APP
    assert "scan_freshness.signals_tooltip(" in _APP


def test_no_new_policy_comparison_in_app_or_cron_for_the_scan_rule():
    cron = (ROOT / "cron_runner.py").read_text(encoding="utf-8")
    assert "scan_freshness.decide(" not in cron or "_scan_freshness.decide(" in cron
    assert "SESSION_OPEN" not in _APP and "SESSION_OPEN" not in cron
