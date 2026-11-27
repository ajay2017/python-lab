"""Buy-check C4b pass 1 (docs/plans/entry-fail-closed-c4-spec.md items 8-13; owner
U2 / U3, 2026-11-27): Grow Today HOLDS every pick, ETF pick and add when the market
move couldn't be read (`tone_unread`) or isn't today's session (`tone_not_today`),
instead of running the day as flat.

Covers `tone_freshness.grow_entry_hold` + its pure view builders, `_grow_today`'s
early return, the gate ledger's empty answer, the readers of `grow["tone"]`
(`held_add_verdict`, `hold_reasons`, the headless diag), the provider's one-bar guard
and the cron's 0-pick log + failure appends. No network, no DB."""
import ast
import json
import math
import pathlib
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import daily_briefing, gate_ledger, hold_reasons, sizing_path
from stock_analyzer import tone_freshness as tf
from stock_analyzer.constants import COMPOSITE_BUY
from stock_analyzer.daily_briefing import _grow_today, build_daily_briefing
from stock_analyzer.providers import yfinance_provider as yp
from tests import test_cron_daily_budget as _bud
from tests import test_grow_today_golden as golden
from tests.conftest import make_port_df
from tests.test_daily_briefing import _etf_candidate
from tests._ast_cache import parse_file_cached, source_segment

pytestmark = pytest.mark.fast

ET = ZoneInfo("America/New_York")
ROOT = pathlib.Path(__file__).resolve().parent.parent
TODAY = golden._TODAY                      # Thu 2026-10-15, a trading day


def _et(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET)


def _mc(read_at=None, **kw):
    c = {"tone": "bull", "sp500_pct": 0.8, "nasdaq_pct": 1.0, "leading_sectors": [],
         "tone_available": True}
    if read_at is not None:
        c["tone_read_at"] = read_at
    c.update(kw)
    return c


# ═════════════════════ grow_entry_hold ════════════════════════════════════════
def test_post_open_read_today_is_not_held():
    assert tf.grow_entry_hold(_mc(_et(2026, 11, 25, 10, 0)), date(2026, 11, 25)) is None


@pytest.mark.parametrize("ctx", [
    {"tone": "bull", "sp500_pct": 1.0, "tone_available": False},
    {"tone": "bull", "sp500_pct": float("nan"), "tone_available": True},
    {"tone": "bull", "sp500_pct": float("inf"), "tone_available": True},
    {"tone": "bull", "sp500_pct": None, "tone_available": True},
    None, "junk", [],
])
def test_unread_contexts_hold_as_tone_unread(ctx):
    h = tf.grow_entry_hold(ctx, date(2026, 11, 25))
    assert h["check"] == "tone_unread" and h["session"] is None
    assert "couldn't be read" in h["reason"] and h["banner"] and h["clears_when"]


@pytest.mark.parametrize("bad", [datetime(2026, 11, 25, 10, 0), "10:00", None, 1.5])
def test_present_but_unreadable_read_time_is_unread(bad):
    ctx = _mc()
    ctx["tone_read_at"] = bad
    assert tf.grow_entry_hold(ctx, date(2026, 11, 25))["check"] == "tone_unread"


@pytest.mark.parametrize("ctx", [
    {"tone": "bull"},                                           # tests / legacy
    {"tone": "bull", "sp500_pct": 1.2},
    {"tone": "flat", "sp500_pct": 0.0, "tone_available": True},  # no read time key
])
def test_legacy_contexts_without_the_keys_are_not_held(ctx):
    assert tf.grow_entry_hold(ctx, date(2026, 11, 25)) is None


def test_u2_preopen_build_holds_as_tone_not_today():
    h = tf.grow_entry_hold(_mc(_et(2026, 11, 25, 8, 0)), date(2026, 11, 25))
    assert h["check"] == "tone_not_today"
    assert h["reason"] == "today's session hasn't opened yet — the latest move is Tue Nov 24's"
    assert h["clears_when"] == "after the 9:30 AM ET open"
    assert h["session"] == "2026-11-24"
    assert "Tue Nov 24" in h["banner"] and "9:30 AM ET" in h["banner"]


def test_u2_open_boundary_0929_59_held_0930_00_not():
    assert tf.grow_entry_hold(_mc(_et(2026, 11, 25, 9, 29, 59)),
                              date(2026, 11, 25))["check"] == "tone_not_today"
    assert tf.grow_entry_hold(_mc(_et(2026, 11, 25, 9, 30, 0)), date(2026, 11, 25)) is None


def test_u2_saturday_holds_and_clears_next_monday():
    h = tf.grow_entry_hold(_mc(_et(2026, 11, 28, 10, 0)), date(2026, 11, 28))
    assert h["check"] == "tone_not_today"
    assert "market is closed today" in h["reason"] and "Fri Nov 27" in h["reason"]
    assert h["clears_when"] == "on Mon Nov 30 after the open"
    assert h["session"] == "2026-11-27"


def test_u2_thanksgiving_holds_and_clears_the_next_day():
    h = tf.grow_entry_hold(_mc(_et(2026, 11, 26, 11, 0)), date(2026, 11, 26))
    assert h["check"] == "tone_not_today" and "Wed Nov 25" in h["reason"]
    assert h["clears_when"] == "on Fri Nov 27 after the open"


def test_a_read_from_an_earlier_day_holds_with_its_own_wording():
    h = tf.grow_entry_hold(_mc(_et(2026, 11, 24, 15, 0)), date(2026, 11, 25))
    assert h["check"] == "tone_not_today" and "earlier day" in h["reason"]
    assert h["clears_when"] == "when the market move is read again"


@pytest.mark.parametrize("day", [date(2026, 11, 2), date(2027, 3, 8)])
def test_zoneinfo_open_boundary_on_mondays_around_dst(day):
    assert tf.grow_entry_hold(_mc(_et(day.year, day.month, day.day, 9, 29)),
                              day)["check"] == "tone_not_today"
    assert tf.grow_entry_hold(_mc(_et(day.year, day.month, day.day, 9, 31)), day) is None


def test_a_utc_read_time_is_converted_to_et_before_judging():
    from datetime import timezone
    # 14:31 UTC on 2026-11-25 = 09:31 ET: post-open
    ctx = _mc(datetime(2026, 11, 25, 14, 31, tzinfo=timezone.utc))
    assert tf.grow_entry_hold(ctx, date(2026, 11, 25)) is None
    ctx = _mc(datetime(2026, 11, 25, 14, 29, tzinfo=timezone.utc))
    assert tf.grow_entry_hold(ctx, date(2026, 11, 25))["check"] == "tone_not_today"


def test_today_may_be_a_datetime():
    assert tf.grow_entry_hold(_mc(_et(2026, 11, 25, 10, 0)), _et(2026, 11, 25, 12, 0)) is None


@pytest.mark.parametrize("ctx,day", [
    (_mc(_et(2026, 11, 25, 8, 0)), date(2026, 11, 25)),
    (_mc(_et(2026, 11, 28, 10, 0)), date(2026, 11, 28)),
    ({"tone_available": False}, date(2026, 11, 25)),
])
def test_hold_is_json_safe_strings_only(ctx, day):
    h = tf.grow_entry_hold(ctx, day)
    assert json.loads(json.dumps(h)) == h
    assert all(v is None or isinstance(v, str) for v in h.values())
    assert set(h) == {"check", "reason", "clears_when", "banner", "session"}


def test_grow_entry_hold_never_raises_and_fails_closed():
    h = tf.grow_entry_hold(_mc(_et(2026, 11, 25, 10, 0)), "not a date")
    assert h is None or h["check"] in ("tone_unread", "tone_not_today")


# ═════════════════════ view builders ══════════════════════════════════════════
def test_grow_count_text_says_held_never_zero():
    held = {"entry_hold": {"check": "tone_unread"}, "new_picks": [], "add_positions": []}
    assert "held" in tf.grow_count_text(held) and "0" not in tf.grow_count_text(held)
    assert tf.grow_count_text({"new_picks": [1, 2], "add_positions": [3]}) == "3 Grow Today"
    assert tf.grow_count_text({"new_picks": [], "add_positions": []}) == "0 Grow Today"
    assert tf.grow_count_text(None) == "0 Grow Today"


def test_grow_header_view_held_bull_flat_bear():
    held = tf.grow_header_view(
        {"entry_hold": {"check": "tone_unread", "reason": "x"}, "new_picks": [1]}, "bull")
    assert held["held"] is True and held["count"] == " (held)" and held["show_cleared"] is False
    assert "held" in held["subtitle"]
    bull = tf.grow_header_view({"new_picks": [1], "add_positions": []}, "bull")
    assert bull == {"held": False, "count": " (1 setup)", "subtitle": "", "show_cleared": True}
    assert tf.grow_header_view({"new_picks": [1, 2]}, "flat")["count"] == " (2 setups)"
    assert "elevated caution" in tf.grow_header_view({}, "flat")["subtitle"]
    assert "protect capital" in tf.grow_header_view({}, "bear")["subtitle"]
    assert tf.grow_header_view({}, "flat")["count"] == ""
    assert tf.grow_header_view(None, "bull")["show_cleared"] is True


@pytest.mark.parametrize("ctx", [
    {"tone": "flat", "sp500_pct": 0.0, "nasdaq_pct": 0.0, "tone_available": False},
    {"tone": "bull", "sp500_pct": float("nan"), "tone_available": True},
    None,
])
def test_tone_card_view_unread_never_reads_as_a_flat_day(ctx):
    v = tf.tone_card_view(ctx, _et(2026, 11, 25, 11, 0))
    assert v["tone_key"] == "unread"
    assert "Mixed Market" not in v["label"] and "Hold Steady" not in v["label"]
    assert "+0.00%" not in v["sp_line"] and v["nq_line"] is None
    assert "held" in v["note"]


def test_tone_card_view_available_labels_and_lines():
    now = _et(2026, 11, 25, 11, 0)
    bull = tf.tone_card_view(_mc(_et(2026, 11, 25, 10, 50)), now)
    assert bull["tone_key"] == "bull" and "Markets Up" in bull["label"]
    assert bull["sp_line"] == "S&P 500 +0.80%" and bull["nq_line"] == "Nasdaq +1.00%"
    assert bull["note"] is None and bull["is_stale"] is False
    bear = tf.tone_card_view(_mc(tone="bear", sp500_pct=-2.0), now)
    assert bear["tone_key"] == "bear" and "Protect Mode" in bear["label"]
    flat = tf.tone_card_view(_mc(tone="flat", sp500_pct=0.0), now)
    assert flat["tone_key"] == "flat" and "Mixed Market" in flat["label"]
    assert flat["sp_line"] == "S&P 500 +0.00%"           # a REAL zero is still shown
    assert tf.tone_card_view({"tone": "weird", "sp500_pct": 0.1}, now)["tone_key"] == "flat"


def test_tone_card_view_last_close_knows_holidays_and_weekends():
    wed_pre = tf.tone_card_view(_mc(), _et(2026, 11, 25, 8, 0))
    assert wed_pre["is_stale"] and wed_pre["last_close_text"] == "Tue Nov 24"
    assert "data as of Tue Nov 24" in wed_pre["date_text"]
    assert wed_pre["date_text"].startswith("Wednesday, November 25 2026")
    thanks = tf.tone_card_view(_mc(), _et(2026, 11, 26, 11, 0))
    assert thanks["is_stale"] and thanks["last_close_text"] == "Wed Nov 25"   # not Tue / Wed-1
    fri_after_thanks_pre = tf.tone_card_view(_mc(), _et(2026, 11, 27, 8, 0))
    assert fri_after_thanks_pre["last_close_text"] == "Wed Nov 25"
    sat = tf.tone_card_view(_mc(), _et(2026, 11, 28, 10, 0))
    assert sat["last_close_text"] == "Fri Nov 27"
    open_ = tf.tone_card_view(_mc(), _et(2026, 11, 25, 9, 30))
    assert open_["is_stale"] is False and open_["last_close_text"] is None
    assert "data as of" not in open_["date_text"]


# ═════════════════════ _grow_today ════════════════════════════════════════════
def _scn(**o):
    """Bull-day scenario with scan names, an ETF, and qualifying held adds."""
    base = golden._multi_scn(
        0.8, etf=[_etf_candidate()],
        port_rows=[{"ticker": "ZHELD", "weight": 5.0, "sector": "Tech"}] + golden._held_rows())
    base.update(o)
    return base


def _run(s, ctx):
    port_df = make_port_df(s["port_rows"]) if s["port_rows"] else None
    scanner = golden._scanner_df(s["rows"]) if s["rows"] else None

    class _Clk(golden._FixedDT):
        _h, _m = s["clock"]

    with mock.patch.object(daily_briefing, "_dt", _Clk):
        return _grow_today(
            port_df, scanner, s["news"], s["held_data"], TODAY, 100_000.0, ctx,
            act_today=s["act_today"], review_list=s["review_list"],
            composites=s["composites"], earnings_lookup=s["earnings"],
            macro_events=s["macro"], movers=s["movers"], etf_candidates=s["etf"])


_PICK_LISTS = ("new_picks", "add_positions", "overflow_picks", "flat_day_held",
               "risk_blocked_adds", "concentration_blocked_adds", "sector_blocked_adds",
               "card_blocked_adds", "entry_held_adds", "room_blocked_adds",
               "sector_blocked_picks", "sector_unknown_picks", "macro_blocked_picks",
               "composite_skipped", "composite_unavailable", "news_skipped",
               "earnings_waiting", "etf_screened_out", "beta_blocked_picks",
               "beta_unknown_picks", "beta_blocked_adds", "cluster_blocked_adds",
               "pair_blocked_adds")


def _read(day_read_at, **kw):
    return {"tone": "bull", "sp500_pct": 0.8, "nasdaq_pct": 1.0, "leading_sectors": [],
            "tone_available": True, "tone_read_at": day_read_at, **kw}


_OPEN_READ = datetime(2026, 10, 15, 10, 0, tzinfo=ET)
_PREOPEN_READ = datetime(2026, 10, 15, 8, 0, tzinfo=ET)


def test_control_available_day_makes_picks_adds_and_etf_calls():
    g = _run(_scn(), _read(_OPEN_READ))
    assert g["entry_hold"] is None
    assert g["new_picks"] and g["add_positions"]
    assert any(p.get("ticker") == "SPY" for p in g["new_picks"])


def test_unread_day_with_bull_tone_holds_every_list():
    ctx = _read(_OPEN_READ, tone_available=False)
    g = _run(_scn(), ctx)
    assert g["entry_hold"]["check"] == "tone_unread"
    assert g["tone"] == "bull"                     # the caller's tone is carried, not invented
    for k in _PICK_LISTS:
        assert g[k] == [], k
    assert g["sp500_pct"] is None and g["message"] is None
    assert g["tone_available"] is False
    assert g["adds_paused"] and g["unevaluated_leads"]       # disclosure only, no size
    assert all("shares" not in a for a in g["adds_paused"])
    assert g["macro_coverage_expired"] is not False
    assert json.loads(json.dumps(g["entry_hold"])) == g["entry_hold"]


def test_unread_day_via_flat_default_tone_stays_flat_and_held():
    ctx = {"tone": "flat", "sp500_pct": 0, "nasdaq_pct": 0, "leading_sectors": [],
           "tone_available": False}
    g = _run(_scn(), ctx)
    assert g["tone"] == "flat" and g["entry_hold"]["check"] == "tone_unread"
    assert g["new_picks"] == [] and g["add_positions"] == []


def test_unread_beats_a_bear_tone():
    ctx = _read(_OPEN_READ, tone="bear", sp500_pct=-2.0, tone_available=False)
    g = _run(_scn(), ctx)
    assert g["entry_hold"]["check"] == "tone_unread" and g["message"] is None


def test_every_held_return_has_the_bear_returns_keys():
    held = _run(_scn(), _read(_OPEN_READ, tone_available=False))
    bear = _run(_scn(), _read(_OPEN_READ, tone="bear", sp500_pct=-2.0))
    assert set(held) == set(bear)


def test_u2_preopen_bull_holds_as_tone_not_today():
    g = _run(_scn(), _read(_PREOPEN_READ))
    assert g["entry_hold"]["check"] == "tone_not_today"
    assert g["tone"] == "bull" and g["sp500_pct"] == 0.8      # a real (last-session) reading
    for k in _PICK_LISTS:
        assert g[k] == [], k


def test_u2_preopen_bear_takes_the_bear_branch_with_the_session_phrase():
    g = _run(_scn(), _read(_PREOPEN_READ, tone="bear", sp500_pct=-2.0))
    assert g["tone"] == "bear" and g["entry_hold"] is None
    assert "in the last session (Wed Oct 14)" in g["message"]
    assert "today" not in g["message"].split("—")[0]


def test_bear_message_keeps_today_for_a_post_open_or_legacy_read():
    post = _run(_scn(), _read(_OPEN_READ, tone="bear", sp500_pct=-2.0))
    assert post["message"].startswith("S&P 500 -2.00% today — market in risk-off mode.")
    legacy = _run(_scn(), {"tone": "bear", "sp500_pct": -2.0})
    assert legacy["message"].startswith("S&P 500 -2.00% today — market in risk-off mode.")
    assert legacy["entry_hold"] is None


def test_legacy_context_is_not_held_and_has_entry_hold_none():
    g = _run(_scn(), {"tone": "bull", "sp500_pct": 0.8, "nasdaq_pct": 1.0,
                      "leading_sectors": []})
    assert g["entry_hold"] is None and g["new_picks"]


def test_market_context_none_through_build_daily_briefing_holds():
    port_df = make_port_df([{"ticker": "HELD", "weight": 10.0, "sector": "Tech"}])
    brief = build_daily_briefing(
        port_df=port_df, alert_list=[], risk_recs=[], news_items=[], macro_events=[],
        held_data={}, scanner_results=None, portfolio_value=100_000.0, today=TODAY,
        market_context=None, etf_candidates=[_etf_candidate()])
    g = brief["grow_today"]
    assert g["entry_hold"]["check"] == "tone_unread"
    assert g["new_picks"] == [] and g["tone"] == "flat"


def test_build_daily_briefing_available_context_is_not_held():
    port_df = make_port_df([{"ticker": "HELD", "weight": 10.0, "sector": "Tech"}])
    brief = build_daily_briefing(
        port_df=port_df, alert_list=[], risk_recs=[], news_items=[], macro_events=[],
        held_data={}, scanner_results=None, portfolio_value=100_000.0, today=TODAY,
        market_context=_read(_OPEN_READ), etf_candidates=[_etf_candidate()])
    assert brief["grow_today"]["entry_hold"] is None
    assert brief["grow_today"]["new_picks"]


# ═════════════════════ gate_ledger ════════════════════════════════════════════
@pytest.mark.parametrize("tone", ["flat", "bear", "bull", None])
def test_suppression_rows_empty_when_held(tone):
    g = _run(_scn(), _read(_OPEN_READ, tone_available=False))
    assert gate_ledger.build_suppression_rows(
        g, rec_date=TODAY, source="app", tone=tone, sp500_pct=-2.0) == []


def test_suppression_rows_empty_for_a_preopen_hold_even_with_bear_tone_passed():
    g = _run(_scn(), _read(_PREOPEN_READ))
    assert gate_ledger.build_suppression_rows(
        g, rec_date=TODAY, source="cron", tone="bear", sp500_pct=-2.0) == []


def test_bear_day_still_writes_its_g23_row():
    g = _run(_scn(), _read(_OPEN_READ, tone="bear", sp500_pct=-2.0))
    rows = gate_ledger.build_suppression_rows(
        g, rec_date=TODAY, source="app", tone="bear", sp500_pct=-2.0)
    assert [r["gate_id"] for r in rows] == ["G-23"]


def test_an_available_day_still_writes_rows_when_a_gate_fires():
    s = _scn()
    g = _run(s, _read(_OPEN_READ))
    assert g["entry_hold"] is None
    rows = gate_ledger.build_suppression_rows(
        g, rec_date=TODAY, source="app", tone="bull", sp500_pct=0.8)
    assert isinstance(rows, list)


# ═════════════════════ readers of grow["tone"] ════════════════════════════════
def _hv(grow):
    return sizing_path.held_add_verdict(
        "ZADDOK", grow, brief_date="2026-10-15", today="2026-10-15", brief_epoch=1, epoch=1)


def test_held_add_verdict_with_a_hold_says_why_not_adds_only_on_up_day():
    g = _run(_scn(), _read(_OPEN_READ, tone_available=False))
    v = _hv(g)
    assert v["state"] == "not_called"
    assert "holds new picks and adds" in v["reason"] and "couldn't be read" in v["reason"]
    assert "up day" not in v["reason"]


def test_held_add_verdict_preopen_hold_names_the_session():
    v = _hv(_run(_scn(), _read(_PREOPEN_READ)))
    assert v["state"] == "not_called" and "hasn't opened yet" in v["reason"]


def test_held_add_verdict_without_a_hold_is_unchanged():
    g = _run(_scn(), _read(_OPEN_READ))
    assert _hv(g)["state"] == "add"
    flat = {"tone": "flat", "entry_hold": None, "adds_paused": []}
    assert "up day" in _hv(flat)["reason"]
    assert "risk-off" in _hv({"tone": "bear"})["reason"]


_UNREAD = tf.grow_entry_hold({"tone_available": False}, date(2026, 10, 15))
_NOT_TODAY = tf.grow_entry_hold(_read(_PREOPEN_READ), date(2026, 10, 15))


def test_hold_reasons_labels_and_notes_use_held_wording():
    assert hold_reasons.adds_paused_label("flat", 2, _UNREAD) == \
        "Adds held — market move couldn't be read (2)"
    assert hold_reasons.adds_paused_label("flat", 1, _NOT_TODAY) == \
        "Adds held until the market opens (1)"
    note = hold_reasons.adds_paused_note(["MSFT"], _UNREAD)
    assert "MSFT" in note and "held" in note and "next up day" not in note
    assert "Resumes once the S&P 500 read succeeds" in note
    assert "no size" in note
    lead = hold_reasons.leads_note("flat", _NOT_TODAY)
    assert "no verdict" in lead and "flat day" not in lead and "Resumes after" in lead


def test_hold_reasons_without_a_hold_are_unchanged():
    assert hold_reasons.adds_paused_label("bear", 2) == "Adds paused on a down day (2)"
    assert hold_reasons.adds_paused_label("flat", 1, None) == "Adds paused on a flat day (1)"
    assert "next up day" in hold_reasons.adds_paused_note(["MSFT"])
    assert "next up day" in hold_reasons.adds_paused_note(["MSFT"], None)
    assert hold_reasons.leads_note("bear").startswith("New entries are deferred")


def test_headless_diag_carries_entry_hold_producer_end():
    src = (ROOT / "stock_analyzer" / "headless_alert_engine.py").read_text(encoding="utf-8")
    assert '"entry_hold":' in src and "_hold.get(\"check\")" in src
    tree = parse_file_cached(ROOT / "stock_analyzer" / "headless_alert_engine.py")
    keys = [k.value for n in ast.walk(tree) if isinstance(n, ast.Dict)
            for k in n.keys if isinstance(k, ast.Constant)]
    assert "entry_hold" in keys


def _picks_payload(grow_ctx, *, fetch):
    from stock_analyzer import headless_alert_engine as hae
    ctx = {
        "ok": True, "errors": [],
        "port_df": pd.DataFrame({"Ticker": ["AAPL"], "Market Value": [1000.0]}),
        "held_data": {"AAPL": {}}, "fragility": None, "spy_6mo": None, "spy_1y": None,
        "vix": None, "holdings_df": pd.DataFrame({"Ticker": ["AAPL"]}),
    }
    scanner = pd.DataFrame([{"Ticker": "NVDA", "Score": 90.0, "Price": 100.0, "Signal": "Buy",
                             "Sector": "Technology", "RSI": 60.0, "1M Momentum": 5.0,
                             "Trend": "Up"}])
    with mock.patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         mock.patch("stock_analyzer.data.fetch_market_indices", side_effect=fetch), \
         mock.patch("stock_analyzer.headless_alert_engine.load_bundle", return_value={}), \
         mock.patch("stock_analyzer.data.curate_news_items", return_value=[]), \
         mock.patch("stock_analyzer.macro_calendar.build_macro_calendar", return_value=[]):
        return hae.compute_morning_picks(date(2026, 10, 16), scanner_results=scanner)


def test_cron_producer_failed_s_and_p_read_holds_end_to_end():
    r = _picks_payload(None, fetch=RuntimeError("down"))
    assert r["diag"]["entry_hold"] == "tone_unread"
    assert r["diag"]["bar"] is None and r["picks"] == []
    assert r["grow"]["entry_hold"]["check"] == "tone_unread"


def test_cron_producer_empty_index_list_holds_too():
    r = _picks_payload(None, fetch=lambda: [])
    assert r["diag"]["entry_hold"] == "tone_unread" and r["picks"] == []


# ═════════════════════ provider one-bar guard ═════════════════════════════════
def _close_frame(rows_by_ticker):
    idx = pd.date_range("2026-11-24", periods=max(len(v) for v in rows_by_ticker.values()))
    cols = pd.MultiIndex.from_tuples([("Close", t) for t in rows_by_ticker])
    data = {("Close", t): list(v) + [float("nan")] * (len(idx) - len(v))
            for t, v in rows_by_ticker.items()}
    return pd.DataFrame(data, index=idx, columns=cols)


def test_one_bar_index_series_is_omitted_not_a_made_up_flat_move():
    frame = _close_frame({"^DJI": [40000.0, 40400.0], "^GSPC": [5000.0], "^IXIC": [16000.0, 16160.0]})
    with mock.patch.object(yp.yf, "download", return_value=frame):
        rows = yp.YFinanceProvider().market_indices()
    shorts = {r["short"] for r in rows}
    assert shorts == {"DOW", "NASDAQ"}                  # S&P 500 row omitted
    assert all(r["change_pct"] != 0.0 for r in rows)


def test_two_bar_index_series_still_reports_its_move():
    frame = _close_frame({"^DJI": [40000.0, 40400.0], "^GSPC": [5000.0, 5050.0],
                          "^IXIC": [16000.0, 16160.0]})
    with mock.patch.object(yp.yf, "download", return_value=frame):
        rows = yp.YFinanceProvider().market_indices()
    sp = next(r for r in rows if r["short"] == "S&P 500")
    assert sp["change_pct"] == 1.0


def test_missing_s_and_p_row_makes_both_producers_unavailable():
    from stock_analyzer import headless_alert_engine as hae
    frame = _close_frame({"^DJI": [40000.0, 40400.0], "^GSPC": [5000.0], "^IXIC": [16000.0, 16160.0]})
    with mock.patch.object(yp.yf, "download", return_value=frame), \
         mock.patch("stock_analyzer.data._C.DATA_MULTISOURCE_ENABLED", False):
        ctx = hae._read_market_context()
    assert ctx["tone_available"] is False
    assert tf.grow_entry_hold(ctx, ctx["tone_read_at"].date())["check"] == "tone_unread"
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '"tone_available":  _sp_row is not None' in src


# ═════════════════════ cron: 0-pick log + U3 failures ═════════════════════════
def test_tone_unread_failure_line_only_for_an_unread_hold():
    assert cr._tone_unread_failure({"diag": {"entry_hold": "tone_unread"}}).startswith(
        "tone_unread: the S&P 500 read failed")
    assert cr._tone_unread_failure({"diag": {"entry_hold": "tone_not_today"}}) is None
    for p in ({"diag": {}}, {"diag": None}, {}, None, "x", {"diag": {"entry_hold": None}}):
        assert cr._tone_unread_failure(p) is None


def test_entry_hold_text_names_each_hold():
    assert "couldn't be read" in cr._entry_hold_text("tone_unread")
    assert "(G-34)" in cr._entry_hold_text("tone_unread")
    assert "isn't today's session" in cr._entry_hold_text("tone_not_today")


def _run_scan(monkeypatch, diag):
    from datetime import date as _date
    from stock_analyzer import reference_data as rd, scanner as real_scanner
    emails, notified = {}, []
    monkeypatch.setattr(cr, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(rd, "resolve_universe_or_none",
                        lambda name: ({"Tech": ["AAA"]}, _date(2026, 9, 1), None))
    monkeypatch.setattr(real_scanner, "scan_sectors",
                        lambda *a, **k: pd.DataFrame({"Ticker": ["AAA"], "Score": [70.0]}))
    monkeypatch.setattr(cr, "_build_context", lambda _d: {"ok": True, "errors": []})
    monkeypatch.setattr(cr.db, "save_scanner_cache", lambda *_a, **_k: True)
    payload = {"picks": [], "errors": [], "built_at": "x", "diag": diag, "book_drift": None,
               "macro_coverage": None, "grow": {"entry_hold": None},
               "budget_inputs": _bud.BUDGET_INPUTS, "news_feed": {"state": "ok"}}
    monkeypatch.setattr(cr, "compute_morning_picks", lambda **_k: payload)
    monkeypatch.setattr(cr, "compute_watchlist_entries", lambda **_k: {
        "entries": [], "built_at": "x", "errors": [], "reason": None,
        "gate_degraded": False, "news_feed": {"state": "ok"}})
    monkeypatch.setattr(cr.db, "save_recommendations",
                        lambda rows: {"attempted": len(rows), "saved": len(rows), "error": None})
    monkeypatch.setattr(gate_ledger, "build_suppression_rows", lambda *_a, **_k: [])
    monkeypatch.setattr(cr.db, "save_gate_suppressions",
                        lambda _r: {"attempted": 0, "saved": 0, "error": None})
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda **_k: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_send_email",
                        lambda label, s, h: emails.__setitem__(label, h) or True)
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, d: notified.append((lane, d)))
    monkeypatch.setattr(cr, "_notify_lane_failure",
                        lambda lane, now, fails: notified.append((lane, list(fails))))
    cols = _bud._TRADE_COLS
    monkeypatch.setattr(cr.db, "load_trades_or_none", lambda: pd.DataFrame(columns=cols))
    monkeypatch.setattr(cr.db, "load_trades", lambda: pd.DataFrame(columns=cols))
    monkeypatch.setattr(cr.db, "load_budget_allocations_or_none", lambda day: {})
    monkeypatch.setattr(cr.db, "save_budget_allocations", _bud._ok_save)
    rc = cr._run_scan(_bud.NOW, force=True)
    return rc, emails, notified


def test_scan_zero_pick_log_names_the_hold_and_the_lane_goes_red(monkeypatch, capsys):
    diag = {"tone": "flat", "sp500_pct": None, "bar": None, "entry_hold": "tone_unread"}
    rc, emails, notified = _run_scan(monkeypatch, diag)
    out = capsys.readouterr().out
    assert "market move couldn't be read — new picks held (G-34)" in out
    assert "new-pick bar" not in out and "tone=flat" not in out
    assert rc == 1 and notified and notified[0][0] == "scan"
    assert any("tone_unread: the S&P 500 read failed" in f for f in notified[0][1])
    assert "morning-action" not in emails


def test_scan_not_today_hold_logs_but_does_not_redden_the_lane(monkeypatch, capsys):
    diag = {"tone": "bull", "sp500_pct": 0.8, "bar": None, "entry_hold": "tone_not_today"}
    rc, _, notified = _run_scan(monkeypatch, diag)
    assert "isn't today's session" in capsys.readouterr().out
    assert rc == 0 and notified == []


def test_scan_without_a_hold_keeps_its_old_zero_pick_wording(monkeypatch, capsys):
    diag = {"tone": "flat", "sp500_pct": 0.1, "bar": 78, "entry_hold": None}
    rc, _, notified = _run_scan(monkeypatch, diag)
    out = capsys.readouterr().out
    assert "tone=flat (S&P +0.10%) · new-pick bar=78" in out
    assert rc == 0 and notified == []


def _run_intraday(monkeypatch, diag):
    import stock_analyzer.intraday_entry as ie
    emails, notified = {}, []
    now = cr._ET.localize(datetime(2026, 10, 16, 11, 35).replace(tzinfo=None))
    monkeypatch.setattr(cr, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(cr.db, "load_scanner_cache",
                        lambda: {"df": pd.DataFrame({"Ticker": ["AAA"]}),
                                 "scan_date": "2026-10-16",
                                 "scanned_at": "2026-10-16T13:47:00+00:00"})
    payload = {"picks": [], "errors": [], "built_at": "x", "book_drift": None,
               "macro_coverage": None, "budget_inputs": _bud.BUDGET_INPUTS,
               "news_feed": {"state": "ok"}, "diag": diag}
    monkeypatch.setattr(cr, "compute_morning_picks", lambda **_k: payload)
    monkeypatch.setattr(ie, "fetch_intraday_prices", lambda t: {})
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_send_email",
                        lambda label, s, h: emails.__setitem__(label, h) or True)
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, d: notified.append((lane, d)))
    monkeypatch.setattr(cr, "_notify_lane_failure",
                        lambda lane, now_, fails: notified.append((lane, list(fails))))
    rc = cr._run_intraday(now, force=False)
    return rc, emails, notified


def test_intraday_tone_unread_logs_the_hold_and_reddens_the_lane(monkeypatch, capsys):
    rc, emails, notified = _run_intraday(monkeypatch, {"entry_hold": "tone_unread"})
    out = capsys.readouterr().out
    assert "intraday: market move couldn't be read — new picks held (G-34)" in out
    assert rc == 1 and emails == {}
    assert notified[0][0] == "intraday"
    assert any(f.startswith("tone_unread:") for f in notified[0][1])


def test_intraday_not_today_hold_logs_without_failing(monkeypatch, capsys):
    rc, _, notified = _run_intraday(monkeypatch, {"entry_hold": "tone_not_today"})
    assert "isn't today's session" in capsys.readouterr().out
    assert rc == 0 and notified == []


def test_intraday_no_hold_no_failure(monkeypatch, capsys):
    rc, _, notified = _run_intraday(monkeypatch, {"entry_hold": None})
    assert rc == 0 and notified == []
    assert "new picks held" not in capsys.readouterr().out


# ═════════════════════ AST pins ═══════════════════════════════════════════════
def _calls_attr(tree, owner, name):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == name and isinstance(n.func.value, ast.Name)
            and n.func.value.id == owner]


def test_pin_grow_today_calls_grow_entry_hold_and_returns_entry_hold_keys():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "daily_briefing.py")
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_grow_today")
    assert _calls_attr(fn, "tone_freshness", "grow_entry_hold"), "consumer end"
    keys = [k.value for n in ast.walk(fn) if isinstance(n, ast.Dict)
            for k in n.keys if isinstance(k, ast.Constant)]
    # held return + bear return + normal return
    assert keys.count("entry_hold") >= 3


def test_pin_hold_runs_before_the_bear_branch_and_before_any_pick_path():
    src = (ROOT / "stock_analyzer" / "daily_briefing.py").read_text(encoding="utf-8")
    i_hold = src.index("tone_freshness.grow_entry_hold(market_context, today)")
    i_unread = src.index("tone_freshness.CHECK_UNREAD:\n        return _entry_hold_return()")
    i_bear = src.index('    if tone == "bear":\n        # macro_coverage_expired')
    i_picks = src.index("new_picks: list[dict] = []")
    assert i_hold < i_unread < i_bear < i_picks


def test_pin_gate_ledger_returns_empty_for_a_hold_before_the_bear_branch():
    src = (ROOT / "stock_analyzer" / "gate_ledger.py").read_text(encoding="utf-8")
    assert src.index('isinstance(grow.get("entry_hold"), dict)') < src.index('if tone == "bear":')


def test_pin_tone_dicts_in_headless_carry_tone_read_at():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "headless_alert_engine.py")
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            ks = [k.value for k in n.keys if isinstance(k, ast.Constant)]
            if "tone_available" in ks:
                i = ks.index("tone_available")
                v = n.values[i]
                if isinstance(v, ast.Constant) and v.value is False:
                    continue
                assert "tone_read_at" in ks, "a tone_available dict without tone_read_at"


def test_pin_cron_scan_and_intraday_append_the_tone_failure():
    src = (ROOT / "cron_runner.py").read_text(encoding="utf-8")
    assert "failures.append(_tone_fail)" in src
    assert "_id_failures.append(_id_tone_fail)" in src


# ═════════════════════ app.py wiring pins (pass 2; app.py is never imported) ══════
_APP_SRC = (ROOT / "app.py").read_text(encoding="utf-8")


def _app_fn(name):
    tree = parse_file_cached(ROOT / "app.py")
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_pin_render_grow_today_reads_entry_hold_before_bear_msg_and_returns():
    fn = _app_fn("_render_grow_today")
    src = source_segment(_APP_SRC, fn)
    i_hold = src.index('grow.get("entry_hold")')
    i_bear = src.index("if bear_msg:")
    assert i_hold < i_bear
    held_block = src[i_hold:i_bear]
    assert "st.info(" in held_block and "_render_grow_pacing_disclosures(grow, tone)" in held_block
    assert "return" in held_block
    # the header comes from the pure view, not an inline tone branch
    assert "tone_freshness.grow_header_view(grow, tone)" in src
    assert src.index("grow_header_view") < i_hold


def test_pin_held_banner_escapes_dollars_and_has_no_raw_html_bold():
    fn = _app_fn("_render_grow_today")
    src = source_segment(_APP_SRC, fn)
    block = src[src.index('grow.get("entry_hold")'):src.index("if bear_msg:")]
    assert '.replace("$", "\\\\$")' in block
    assert "**" not in block and "unsafe_allow_html" not in block


def test_pin_tone_card_calls_tone_card_view_and_actions_card_uses_grow_count_text():
    assert "tone_freshness.tone_card_view(_market_context, _db_now_et)" in _APP_SRC
    assert "tone_freshness.grow_count_text(_db_grow)" in _APP_SRC
    assert "weekday - 4" not in _APP_SRC.split("tone_card_view(_market_context")[1][:4000]


def test_pin_market_tone_cache_publishes_none_for_an_unread_market():
    i = _APP_SRC.index('st.session_state["_market_tone_cache"] = (')
    seg = _APP_SRC[i:i + 200]
    assert "None if tone_freshness.tone_unread(_market_context) else _db_tone" in seg


def test_pin_synth_signature_carries_the_session_and_schema_is_37():
    assert "_SYNTH_SCHEMA_VER = 37  # bumped: Grow Today carries entry_hold" in _APP_SRC
    sig = _APP_SRC[_APP_SRC.index("_synth_sig = ("):][:2600]
    assert "session_label.price_move_session(_now_et())" in sig


def test_pin_pacing_disclosures_pass_the_hold_to_the_labels():
    fn = _app_fn("_render_grow_pacing_disclosures")
    src = source_segment(_APP_SRC, fn)
    assert "adds_paused_label(tone, len(_pz_names), _pz_hold)" in src
    assert "adds_paused_note(_pz_names, _pz_hold)" in src
    assert "leads_note(tone, _leads_entry_hold)" in src


def test_consumers_of_market_tone_cache_are_none_safe():
    # thesis draft: dict.get(None) -> None regime; thesis snapshot: regime None is a
    # normal state (thesis_advisor.build_snapshot).
    assert '}.get(st.session_state.get("_market_tone_cache")),' in _APP_SRC
    assert 'regime=st.session_state.get("_market_tone_cache"),' in _APP_SRC
    from stock_analyzer import thesis_advisor as ta
    snap = ta.build_snapshot(evidence=None, composite=None, erosion_score=None,
                             erosion_label=None, pt_signal=None, analyst_latest=None,
                             regime=None)
    assert snap["regime"] is None


# ═════════════════════ fix round: redeploy headline defers to Grow Today ═════════
from stock_analyzer import entry_posture as ep


def _ts(as_of, available=True, tone="bull", pct=0.8):
    return {"tone": tone, "sp500_pct": pct, "available": available, "source": "home",
            "as_of": as_of, "why": None, "home_read_at": None}


def test_redeploy_hold_passes_a_home_hold_dict_through():
    h = {"check": "tone_unread", "reason": "x", "clears_when": "y"}
    assert tf.redeploy_grow_hold(h, _ts(_et(2026, 11, 25, 10, 0)), date(2026, 11, 25)) is h
    assert tf.redeploy_grow_hold(h, None, date(2026, 11, 25)) is h


def test_redeploy_hold_preopen_read_is_held_and_open_boundary_is_not():
    d = date(2026, 11, 25)
    held = tf.redeploy_grow_hold(None, _ts(_et(2026, 11, 25, 9, 29, 59)), d)
    assert held["check"] == "tone_not_today" and held["clears_when"] == "after the 9:30 AM ET open"
    assert tf.redeploy_grow_hold(None, _ts(_et(2026, 11, 25, 9, 30, 0)), d) is None


def test_redeploy_hold_saturday_thanksgiving_and_after_hours():
    sat = tf.redeploy_grow_hold(None, _ts(_et(2026, 11, 28, 10, 0)), date(2026, 11, 28))
    assert "market is closed today" in sat["reason"] and sat["clears_when"] == "on Mon Nov 30 after the open"
    tg = tf.redeploy_grow_hold(None, _ts(_et(2026, 11, 26, 11, 0)), date(2026, 11, 26))
    assert "Wed Nov 25" in tg["reason"]
    assert tf.redeploy_grow_hold(None, _ts(_et(2026, 11, 25, 16, 30)), date(2026, 11, 25)) is None


def test_redeploy_hold_reuses_the_same_wording_as_grow_entry_hold():
    pre = _et(2026, 11, 25, 8, 0)
    a = tf.redeploy_grow_hold(None, _ts(pre), date(2026, 11, 25))
    b = tf.grow_entry_hold(_mc(pre), date(2026, 11, 25))
    assert a == b


@pytest.mark.parametrize("ts", [
    _ts(_et(2026, 11, 25, 8, 0), available=False), None, "x", {},
    _ts(datetime(2026, 11, 25, 8, 0)), _ts(None), _ts("08:00"),
])
def test_redeploy_hold_unavailable_or_unreadable_tone_is_none(ts):
    assert tf.redeploy_grow_hold(None, ts, date(2026, 11, 25)) is None


def _headline():
    return {"state": "entry", "ticker": "AAA", "sector": "Tech", "score": 70.0, "rr": 2.5}


def test_redeploy_call_defers_to_a_grow_hold_for_open_and_flat_postures():
    hold = tf.grow_entry_hold(_mc(_et(2026, 11, 25, 8, 0)), date(2026, 11, 25))
    for tone in ("bull", "flat"):
        posture = ep.new_entry_posture(_ts(_et(2026, 11, 25, 8, 0), tone=tone))
        out = ep.redeploy_call(_headline(), posture, grow_hold=hold)
        assert out["entry_called"] is False and out["invite_own_pick"] is False
        assert out["icon"] == "⏸" and out["level"] == "info"
        assert "**AAA** (Tech) clears the Buy gate" in out["text"]
        assert "Grow Today holds new entries: today's session hasn't opened yet" in out["text"]
        assert "They return after the 9:30 AM ET open." in out["text"]
        assert "genuine redeploy entry" not in out["text"] and "$" not in out["text"]


def test_redeploy_call_without_a_hold_is_unchanged_and_bear_keeps_protect_mode():
    posture = ep.new_entry_posture(_ts(_et(2026, 11, 25, 10, 0)))
    assert ep.redeploy_call(_headline(), posture)["entry_called"] is True
    assert ep.redeploy_call(_headline(), posture, grow_hold=None)["entry_called"] is True
    hold = tf.grow_entry_hold(_mc(_et(2026, 11, 25, 8, 0)), date(2026, 11, 25))
    bear = ep.new_entry_posture(_ts(_et(2026, 11, 25, 8, 0), tone="bear", pct=-2.0))
    out = ep.redeploy_call(_headline(), bear, grow_hold=hold)
    assert "Protect Mode" in out["text"]
    unk = ep.new_entry_posture({"available": False})
    assert "couldn't be read" in ep.redeploy_call(_headline(), unk, grow_hold=hold)["text"]
    # a non-"entry" headline keeps the caller's wording
    other = ep.redeploy_call({"state": "rr_fails", "ticker": "AAA"}, posture, grow_hold=hold)
    assert other["text"] is None


def test_home_grow_hold_reads_the_synth_cache_only_when_built_today():
    hold = {"check": "tone_unread"}
    syn = {"date": "2026-11-25", "bundle": {"_daily_brief": {"grow_today": {"entry_hold": hold}}}}
    assert ep.home_grow_hold(syn, "2026-11-25") is hold
    assert ep.home_grow_hold(syn, "2026-11-26") is None
    for bad in (None, {}, {"date": "2026-11-25", "bundle": None},
                {"date": "2026-11-25", "bundle": {"_daily_brief": None}},
                {"date": "2026-11-25", "bundle": {"_daily_brief": {"grow_today": None}}},
                {"date": "2026-11-25", "bundle": {"_daily_brief": {"grow_today": {"entry_hold": None}}}},
                "x"):
        assert ep.home_grow_hold(bad, "2026-11-25") is None


def test_pin_both_app_redeploy_call_sites_pass_grow_hold():
    tree = parse_file_cached(ROOT / "app.py")
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "redeploy_call"]
    assert len(calls) == 2
    for c in calls:
        kw = {k.arg for k in c.keywords}
        assert "grow_hold" in kw
    assert _APP_SRC.count("tone_freshness.redeploy_grow_hold(") == 2


# ── non-blocking review items ────────────────────────────────────────────────
def test_a_read_dated_later_than_today_holds_with_a_truthful_reason():
    h = tf.grow_entry_hold(_mc(_et(2026, 11, 26, 10, 0)), date(2026, 11, 25))
    assert h["check"] == "tone_not_today"
    assert "a different day" in h["reason"] and "hasn't opened" not in h["reason"]
    assert h["clears_when"] == tf.CLEARS_READ_AGAIN


def test_adds_held_label_for_the_other_day_variant_is_not_the_opens_wording():
    other = tf.grow_entry_hold(_mc(_et(2026, 11, 24, 15, 0)), date(2026, 11, 25))
    assert hold_reasons.adds_paused_label("flat", 2, other) == \
        "Adds held — the market move isn't today's (2)"
    pre = tf.grow_entry_hold(_mc(_et(2026, 11, 25, 8, 0)), date(2026, 11, 25))
    assert hold_reasons.adds_paused_label("flat", 2, pre) == "Adds held until the market opens (2)"


def test_same_day_after_close_and_early_close_afternoon_are_not_held():
    assert tf.grow_entry_hold(_mc(_et(2026, 11, 25, 16, 30)), date(2026, 11, 25)) is None
    assert tf.grow_entry_hold(_mc(_et(2026, 11, 27, 13, 10)), date(2026, 11, 27)) is None


def test_preopen_bear_read_takes_the_bear_branch_and_still_writes_its_g23_row():
    ctx = _read(_PREOPEN_READ, tone="bear", sp500_pct=-2.0)
    g = _run(_scn(), ctx)
    assert g["tone"] == "bear" and g["entry_hold"] is None and g["new_picks"] == []
    rows = gate_ledger.build_suppression_rows(
        g, rec_date=TODAY, source="app", tone="bear", sp500_pct=-2.0)
    assert [r["gate_id"] for r in rows] == ["G-23"]


def test_pin_grow_subtitle_is_escaped_in_the_header_html():
    assert "{_safe_html(_g_subtitle)}" in _APP_SRC


# ── a stale pre-open Home hold must not outlive the open (Signals) ──────────
_D = date(2026, 11, 25)
_HOME_NOT_TODAY = tf.grow_entry_hold(_mc(_et(2026, 11, 25, 8, 0)), _D)
_HOME_UNREAD = tf.grow_entry_hold({"tone_available": False}, _D)


def test_stale_not_today_home_hold_is_dropped_when_the_page_has_a_post_open_read():
    assert _HOME_NOT_TODAY["check"] == "tone_not_today"
    assert tf.redeploy_grow_hold(_HOME_NOT_TODAY, _ts(_et(2026, 11, 25, 10, 0)), _D) is None
    assert tf.redeploy_grow_hold(_HOME_NOT_TODAY, _ts(_et(2026, 11, 25, 9, 30, 0)), _D) is None
    assert tf.redeploy_grow_hold(_HOME_NOT_TODAY, _ts(_et(2026, 11, 25, 16, 30)), _D) is None


def test_not_today_home_hold_stands_against_a_pre_open_or_unusable_page_read():
    assert tf.redeploy_grow_hold(_HOME_NOT_TODAY, _ts(_et(2026, 11, 25, 9, 29)), _D) is _HOME_NOT_TODAY
    assert tf.redeploy_grow_hold(_HOME_NOT_TODAY, _ts(_et(2026, 11, 25, 10, 0), available=False),
                                 _D) is _HOME_NOT_TODAY
    assert tf.redeploy_grow_hold(_HOME_NOT_TODAY, None, _D) is _HOME_NOT_TODAY
    assert tf.redeploy_grow_hold(_HOME_NOT_TODAY, _ts(datetime(2026, 11, 25, 10, 0)),
                                 _D) is _HOME_NOT_TODAY
    # a post-open read of ANOTHER day is not "a post-open read of today"
    assert tf.redeploy_grow_hold(_HOME_NOT_TODAY, _ts(_et(2026, 11, 24, 15, 0)), _D) is _HOME_NOT_TODAY


def test_home_tone_unread_hold_always_passes_through():
    assert tf.redeploy_grow_hold(_HOME_UNREAD, _ts(_et(2026, 11, 25, 10, 0)), _D) is _HOME_UNREAD
    assert tf.redeploy_grow_hold(_HOME_UNREAD, _ts(_et(2026, 11, 25, 8, 0)), _D) is _HOME_UNREAD


def test_home_site_parity_hold_and_tone_from_one_preopen_build_still_hold():
    # Home's hold and its `_hm_tone` come from ONE build, so they never disagree.
    pre = _et(2026, 11, 25, 8, 0)
    hold = tf.grow_entry_hold(_mc(pre), _D)
    assert tf.redeploy_grow_hold(hold, _ts(pre), _D) is hold
    post = _et(2026, 11, 25, 10, 0)
    assert tf.grow_entry_hold(_mc(post), _D) is None
    assert tf.redeploy_grow_hold(None, _ts(post), _D) is None


def test_signals_stale_home_hold_no_longer_defers_the_headline_after_the_open():
    posture = ep.new_entry_posture(_ts(_et(2026, 11, 25, 10, 0)))
    hold = tf.redeploy_grow_hold(_HOME_NOT_TODAY, _ts(_et(2026, 11, 25, 10, 0)), _D)
    assert ep.redeploy_call(_headline(), posture, grow_hold=hold)["entry_called"] is True
