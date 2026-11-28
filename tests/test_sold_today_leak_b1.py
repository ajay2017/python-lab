"""Buy-check C5 chunk B1 (docs/plans/entry-fail-closed-c5-spec.md section 4, items 1-7;
owner QB1-QB4 2026-11-27): an unreadable trade journal HOLDS new entries and adds
instead of reading as "nothing sold today" (SoldTodayLeak).

Covers `journal_read` (pure), `check_journal` + the binding order, the Watchlist banner /
email footer / Analysis wording, Grow Today's whole-day `trades_unread` hold through
`_grow_today` and `build_daily_briefing`, the time-context matrix of the shared sold-today
rule, the cron producers (`_build_context`, `compute_morning_picks`,
`compute_watchlist_entries`), the lane failure lines and the AST / source-order pins.
app.py wiring is B2 and is not pinned here.  No network, no DB."""
import ast
import json
import pathlib
from contextlib import ExitStack
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import daily_briefing, entry_eligibility as ee
from stock_analyzer import entry_posture as ep
from stock_analyzer import gate_ledger, hold_reasons, journal_read, macro_read, sizing_path
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer import tone_freshness as tf
from stock_analyzer.daily_briefing import _grow_today, build_daily_briefing
from stock_analyzer.sold_today import tickers_sold_on
from tests import test_grow_today_golden as golden
from tests._ast_cache import parse_file_cached
from tests.conftest import find_item, make_port_df
from tests.test_daily_briefing import COMPOSITE_BUY, _scanner_df
from tests.test_entry_hold_c4b import (
    _OPEN_READ, _PICK_LISTS, _PREOPEN_READ, _read, _run_intraday, _run_scan, _scn,
)

pytestmark = pytest.mark.fast

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
ROOT = pathlib.Path(__file__).resolve().parent.parent
TODAY = golden._TODAY                      # Thu 2026-10-15, a trading day


def _et(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET)


def _no_money_markup(*texts):
    for t in texts:
        assert "$" not in t and "**" not in t, t


# ═════════════════════ journal_read (pure) ════════════════════════════════════
def test_unread_reason_and_entry_hold_shape():
    assert journal_read.CHECK_UNREAD == "trades_unread"
    assert journal_read.unread_reason() == (
        "your trade journal couldn't be read, so the app can't tell whether you sold "
        "this name today")
    h = journal_read.entry_hold()
    assert h["kind"] == "hold" and h["check"] == "trades_unread" and h["gate_id"] is None
    assert h["reason"] == journal_read.unread_reason()
    assert h["gate_value"] is None and h["gate_threshold"] is None
    assert h["clears_when"] == ("the trade journal can be read — re-checked every time "
                                "you open this page")
    _no_money_markup(h["reason"], h["clears_when"])


def test_grow_hold_alone_is_json_safe_strings_with_the_banner():
    h = journal_read.grow_hold()
    assert json.loads(json.dumps(h)) == h
    assert set(h) == {"check", "reason", "clears_when", "banner", "session"}
    assert h["check"] == "trades_unread" and h["session"] is None
    assert h["clears_when"] == "when the trade journal can be read again"
    assert h["banner"] == (
        "⏸ New picks, ETF picks and adds are held: your trade journal couldn't be read, "
        "so Grow Today can't tell which names you sold today (it never suggests buying "
        "one back the same day). They return when the trade journal can be read again.")
    _no_money_markup(h["banner"], h["reason"], h["clears_when"])
    assert journal_read.grow_hold(tone_hold=None) == h


def test_grow_hold_merges_a_weekday_preopen_tone_not_today_hold():
    tone = tf.grow_entry_hold(
        {"tone": "bull", "sp500_pct": 0.5, "tone_available": True,
         "tone_read_at": _et(2026, 11, 25, 8, 0)}, date(2026, 11, 25))
    h = journal_read.grow_hold(tone_hold=tone)
    assert h["check"] == "trades_unread" and h["session"] == "2026-11-24"
    assert h["clears_when"] == "when the trade journal can be read again, and after the 9:30 AM ET open"
    assert h["banner"].endswith(f"They return {h['clears_when']}.")
    assert json.loads(json.dumps(h)) == h


def test_grow_hold_merges_a_saturday_and_a_thanksgiving_hold():
    sat = tf.grow_entry_hold(
        {"tone": "bull", "sp500_pct": 0.5, "tone_available": True,
         "tone_read_at": _et(2026, 11, 28, 10, 0)}, date(2026, 11, 28))
    assert journal_read.grow_hold(sat)["clears_when"].endswith(
        ", and on Mon Nov 30 after the open")
    tg = tf.grow_entry_hold(
        {"tone": "bull", "sp500_pct": 0.5, "tone_available": True,
         "tone_read_at": _et(2026, 11, 26, 11, 0)}, date(2026, 11, 26))
    h = journal_read.grow_hold(tg)
    assert h["clears_when"].endswith(", and on Fri Nov 27 after the open")
    assert h["session"] == "2026-11-25"
    _no_money_markup(h["banner"])


def test_grow_hold_does_not_merge_a_tone_unread_hold():
    unread = tf.grow_entry_hold({"tone_available": False}, date(2026, 11, 25))
    h = journal_read.grow_hold(tone_hold=unread)
    assert h["clears_when"] == "when the trade journal can be read again" and h["session"] is None


def test_retry_caption_only_for_a_journal_hold():
    cap = journal_read.retry_caption(journal_read.grow_hold())
    assert cap == ("🏠 Home re-reads the journal each time it reloads; the picks return "
                   "on the first reload that can read it.")
    _no_money_markup(cap)
    assert journal_read.retry_caption(macro_read.grow_hold()) is None
    for bad in (None, {}, "x", {"check": "tone_not_today"}):
        assert journal_read.retry_caption(bad) is None


def test_failure_line_absent_unread_diag_and_trades_unavailable_false():
    assert journal_read.failure_line() is None
    assert journal_read.failure_line({"diag": {"entry_hold": None}}) is None
    assert journal_read.failure_line({"diag": {"entry_hold": "macro_unread"}}) is None
    assert journal_read.failure_line(None, {"trades_available": True}) is None
    assert journal_read.failure_line(None, {"tone_state": {}}) is None      # key absent
    line = journal_read.failure_line({"diag": {"entry_hold": "trades_unread"}})
    assert line == ("trades_unread: the trade journal couldn't be read — no buy-list, "
                    "intraday pick or Watchlist Enter Now this run")
    assert journal_read.failure_line(None, {"trades_available": False}) == line
    assert journal_read.failure_line({"diag": {}}, {"trades_available": False}) == line
    _no_money_markup(line)
    for bad in (None, "x", 5, []):
        assert journal_read.failure_line(bad, bad) is None


def test_failure_line_fires_on_a_bear_day_unread_journal():
    # A bear day returns before Grow Today's journal hold, so entry_hold is None;
    # the run's own strict read (diag trades_read) still turns the lane red.
    line = journal_read.failure_line({"diag": {"entry_hold": None, "trades_read": False}})
    assert line == journal_read.failure_line({"diag": {"entry_hold": "trades_unread"}})
    assert line is not None
    assert journal_read.failure_line({"diag": {"entry_hold": None, "trades_read": True}}) is None
    assert journal_read.failure_line({"diag": {"entry_hold": None, "trades_read": None}}) is None


def test_morning_picks_diag_carries_the_runs_journal_read():
    import inspect
    from stock_analyzer import headless_alert_engine as hae
    src = inspect.getsource(hae.compute_morning_picks)
    assert '"trades_read":      ctx.get("trades_read"),' in src


def test_failure_line_never_raises():
    class _Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("x")
    assert journal_read.failure_line(_Boom(), _Boom()) is None


def test_journal_read_does_not_import_entry_eligibility_or_read_a_clock():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "journal_read.py")
    mods = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mods.append(n.module or "")
            mods += [(n.module or "") + "." + a.name for a in n.names]
        elif isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
    joined = " ".join(mods)
    assert "entry_eligibility" not in joined and "streamlit" not in joined
    assert "stock_analyzer.db" not in joined
    called = {getattr(n.func, "attr", getattr(n.func, "id", None))
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not (called & {"now", "today", "utcnow", "time", "now_et", "today_et"}), called


# ── analysis_sold_today ─────────────────────────────────────────────────────
def _decision(**kw):
    d = {"offer_size": True, "hold": None, "holds": [], "unchecked": [], "passed": [],
         "reason_if_no_size": None, "entry_checked": True, "crashed": False}
    d.update(kw)
    return d


def test_analysis_sold_today_withholds_the_size_and_prepends_the_hold():
    base = _decision()
    out = journal_read.analysis_sold_today(base, "msft")
    assert out is not base and base["offer_size"] is True and base["hold"] is None  # a copy
    assert out["offer_size"] is False
    assert out["hold"]["check"] == "sold_today" and out["hold"]["gate_id"] is None
    assert out["hold"]["reason"] == ("you sold MSFT today — the app doesn't suggest "
                                     "buying a name back the same day")
    assert out["hold"]["clears_when"] == "the next ET trading day"
    assert out["reason_if_no_size"] == out["hold"]["reason"]
    assert out["holds"][0] is out["hold"]
    _no_money_markup(out["hold"]["reason"])


def test_analysis_sold_today_keeps_the_other_holds_after_it():
    other = {"check": "news", "reason": "negative news"}
    out = journal_read.analysis_sold_today(_decision(offer_size=False, hold=other,
                                                     holds=[other]), "AAA")
    assert [h["check"] for h in out["holds"]] == ["sold_today", "news"]
    assert out["hold"]["check"] == "sold_today"


@pytest.mark.parametrize("d", [
    _decision(entry_checked=False),                    # non-Buy / held: no check ran
    {"entry_checked": None}, {}, None, "x", 5,
])
def test_analysis_sold_today_leaves_anything_unchecked_as_given(d):
    assert journal_read.analysis_sold_today(d, "AAA") is d


def test_analysis_sold_today_flows_through_the_banner_and_label():
    out = journal_read.analysis_sold_today(_decision(), "AAA")
    banner = ee.analysis_hold_banner(out)
    assert banner["title"] == ("Not an entry today — held: you sold AAA today — the app "
                               "doesn't suggest buying a name back the same day")
    assert "Clears when: the next ET trading day." in banner["body"]
    assert ee.analysis_shares_label(out) == "— (held)"


# ── same_day_sell_note ──────────────────────────────────────────────────────
def _sell(ticker, ts, action="SELL"):
    return {"ticker": ticker, "action": action, "traded_at": ts, "shares": 1, "price": 10.0}


def test_note_buy_and_sell_on_the_same_et_day():
    trades = pd.DataFrame([_sell("MSFT", "2026-11-26T15:00:00+00:00")])   # Thu 10:00 ET
    note = journal_read.same_day_sell_note("msft", trades, "2026-11-26T19:00:00+00:00")
    assert note == ("Note: you also sold MSFT on Thu Nov 26. The app doesn't suggest buying "
                    "a name back the same day you sold it; this note doesn't stop you "
                    "logging a trade you made.")
    _no_money_markup(note)


def test_note_buy_friday_sell_thursday_is_none():
    trades = pd.DataFrame([_sell("MSFT", "2026-11-26T15:00:00+00:00")])
    assert journal_read.same_day_sell_note("MSFT", trades, "2026-11-27T15:00:00+00:00") is None


def test_note_a_late_buy_for_wednesday_is_dated_wednesday():
    trades = pd.DataFrame([_sell("MSFT", "2026-11-25T16:00:00+00:00")])   # Wed 11:00 ET
    note = journal_read.same_day_sell_note(
        "MSFT", trades, "2026-11-25T21:00:00-05:00")                       # logged for Wed
    assert note is not None and "on Wed Nov 25." in note


def test_note_uses_the_buys_et_date_for_a_late_evening_utc_stamp():
    # Thu 23:30 ET = Fri 04:30 UTC: the buy is a THURSDAY buy; a Thursday sell matches.
    trades = pd.DataFrame([_sell("MSFT", "2026-11-26T15:00:00+00:00")])
    note = journal_read.same_day_sell_note("MSFT", trades, "2026-11-27T04:30:00+00:00")
    assert note is not None and "on Thu Nov 26." in note


def test_note_unread_journal_says_it_couldnt_be_checked():
    note = journal_read.same_day_sell_note("MSFT", None, "2026-11-26T15:00:00+00:00")
    assert note == ("Note: whether you sold MSFT on Thu Nov 26 couldn't be checked (the "
                    "trade journal couldn't be read).")
    _no_money_markup(note)
    # a frame the rule can't parse is unread too, never "no sell"
    assert "couldn't be checked" in journal_read.same_day_sell_note(
        "MSFT", pd.DataFrame([{"ticker": "MSFT"}]), "2026-11-26T15:00:00+00:00")


def test_note_none_for_a_readable_journal_with_no_such_sell():
    assert journal_read.same_day_sell_note(
        "MSFT", pd.DataFrame(columns=["ticker", "action", "traded_at"]),
        "2026-11-26T15:00:00+00:00") is None
    only_buy = pd.DataFrame([_sell("MSFT", "2026-11-26T15:00:00+00:00", action="BUY")])
    assert journal_read.same_day_sell_note("MSFT", only_buy, "2026-11-26T15:00:00+00:00") is None
    other = pd.DataFrame([_sell("AAPL", "2026-11-26T15:00:00+00:00")])
    assert journal_read.same_day_sell_note("MSFT", other, "2026-11-26T15:00:00+00:00") is None


def test_note_falls_back_to_the_callers_et_today_and_never_guesses_a_date():
    trades = pd.DataFrame([_sell("MSFT", "2026-11-26T15:00:00+00:00")])
    assert journal_read.same_day_sell_note("MSFT", trades, None, today=date(2026, 11, 26))
    assert journal_read.same_day_sell_note("MSFT", trades, None) is None
    assert journal_read.same_day_sell_note("MSFT", trades, "not a date") is None
    # unread with no date at all still says so (on "that day"), never silence
    assert "on that day couldn't be checked" in journal_read.same_day_sell_note(
        "MSFT", None, None)


def test_note_naive_buy_time_is_read_as_et_wall_clock():
    trades = pd.DataFrame([_sell("MSFT", "2026-11-26T15:00:00+00:00")])
    assert journal_read.same_day_sell_note("MSFT", trades, datetime(2026, 11, 26, 23, 30))
    assert journal_read.same_day_sell_note("MSFT", trades, date(2026, 11, 26))
    assert journal_read.same_day_sell_note("MSFT", trades, pd.Timestamp("2026-11-27 00:30")) is None


@pytest.mark.parametrize("ticker", [None, "", "  "])
def test_note_blank_ticker_and_never_raises(ticker):
    assert journal_read.same_day_sell_note(ticker, None, "2026-11-26T15:00:00+00:00") is None
    assert journal_read.same_day_sell_note("AAA", object(), object()) is None


# ═════════════════════ time-context matrix: the shared sold-today rule ═════════
def _sold(ts, day, ticker="XYZ"):
    return tickers_sold_on(pd.DataFrame([_sell(ticker, ts)]), day)


def test_matrix_late_evening_et_sell_counts_that_et_day_edt_and_est():
    # Thu 23:30 ET = Fri 03:30Z (EDT) / Fri 04:30Z (EST): Thursday's sale in both.
    assert _sold("2026-10-16T03:30:00+00:00", date(2026, 10, 15)) == {"XYZ"}
    assert _sold("2026-10-16T03:30:00+00:00", date(2026, 10, 16)) == set()
    assert _sold("2026-11-13T04:30:00+00:00", date(2026, 11, 12)) == {"XYZ"}
    assert _sold("2026-11-13T04:30:00+00:00", date(2026, 11, 13)) == set()


def test_matrix_after_midnight_et_sell_counts_the_new_day():
    # Fri 00:30 ET = Fri 04:30Z (EDT): Friday's sale, not Thursday's.
    assert _sold("2026-10-16T04:30:00+00:00", date(2026, 10, 16)) == {"XYZ"}
    assert _sold("2026-10-16T04:30:00+00:00", date(2026, 10, 15)) == set()


def test_matrix_sell_logged_friday_with_thursdays_anchor_counts_thursday():
    df = pd.DataFrame([{**_sell("XYZ", "2026-10-15T20:00:00+00:00"),
                        "created_at": "2026-10-16T14:00:00+00:00"}])   # logged Fri, traded Thu
    assert tickers_sold_on(df, date(2026, 10, 15)) == {"XYZ"}
    assert tickers_sold_on(df, date(2026, 10, 16)) == set()


def test_matrix_saturday_and_holiday_visits_do_not_see_fridays_sells_as_today():
    friday = "2026-11-27T15:00:00+00:00"
    assert _sold(friday, date(2026, 11, 28)) == set()        # Saturday visit
    assert _sold("2026-11-25T15:00:00+00:00", date(2026, 11, 26)) == set()   # Thanksgiving
    # but a sale logged ON Thanksgiving Day itself is that day's
    assert _sold("2026-11-26T15:00:00+00:00", date(2026, 11, 26)) == {"XYZ"}


def test_matrix_dst_fall_back_day_with_tz_aware_stamps():
    day = date(2026, 11, 1)             # clocks fall back at 02:00 local
    assert _sold("2026-11-01T04:30:00+00:00", day) == {"XYZ"}      # 00:30 EDT Sun
    assert _sold("2026-11-01T04:30:00+00:00", date(2026, 10, 31)) == set()
    assert _sold("2026-11-02T04:30:00+00:00", day) == {"XYZ"}      # 23:30 EST Sun
    assert _sold("2026-11-02T04:30:00+00:00", date(2026, 11, 2)) == set()
    # the offset form (as the journal writes it) lands on the same day
    assert _sold("2026-11-01T23:30:00-05:00", day) == {"XYZ"}


def test_matrix_dst_spring_forward_day():
    day = date(2026, 3, 8)              # clocks spring forward at 02:00 local
    # 04:30Z on 03-08 is 23:30 EST on 03-07 (before the change): Saturday's sale
    assert _sold("2026-03-08T04:30:00+00:00", date(2026, 3, 7)) == {"XYZ"}
    assert _sold("2026-03-08T04:30:00+00:00", day) == set()
    # 03:30Z on 03-09 is 23:30 EDT on 03-08 (after the change): Sunday's sale
    assert _sold("2026-03-09T03:30:00+00:00", day) == {"XYZ"}
    assert _sold("2026-03-09T03:30:00+00:00", date(2026, 3, 9)) == set()


def test_matrix_after_hours_sell_counts_that_weekday():
    # Fri 21:00 ET = Sat 01:00Z (EDT)
    assert _sold("2026-10-17T01:00:00+00:00", date(2026, 10, 16)) == {"XYZ"}
    assert _sold("2026-10-17T01:00:00+00:00", date(2026, 10, 17)) == set()


def test_none_unreadable_and_empty_stay_distinguishable():
    assert tickers_sold_on(None, TODAY) is None
    assert tickers_sold_on(pd.DataFrame([{"ticker": "X"}]), TODAY) is None   # no traded_at
    assert tickers_sold_on(pd.DataFrame(columns=["ticker", "action", "traded_at"]), TODAY) == set()


# ═════════════════════ check_journal + the binding order ═══════════════════════
def _ctx(**kw):
    base = {"bundle": None, "sector": "Technology", "macro_map": None}
    base.update(kw)
    return base


@pytest.mark.parametrize("val, held", [(False, True), (None, False), (True, False)])
def test_check_journal_holds_only_on_an_explicit_false(val, held):
    r = ee.check_journal({"trades_read": val})
    assert (r is not None) is held
    if held:
        assert r["kind"] == "hold" and r["check"] == "trades_unread" and r["gate_id"] is None
        assert r["reason"] == journal_read.unread_reason()
    assert ee.check_journal({}) is None


def test_check_journal_is_in_the_default_checks_last():
    assert ee.DEFAULT_CHECKS[-1] is ee.check_journal
    assert "trades_unread" in ee._ABSTENTION_CHECKS
    assert "tone_unread" in ee._ABSTENTION_CHECKS and "macro_unread" in ee._ABSTENTION_CHECKS


_BULL = {"tone": "bull", "sp500_pct": 0.5, "available": True, "source": "home",
         "as_of": None, "why": None, "home_read_at": None}
_BEAR = {**_BULL, "tone": "bear", "sp500_pct": -2.0}
_DOWN = {"tone": None, "sp500_pct": None, "available": False, "source": None,
         "as_of": None, "why": "live_failed", "home_read_at": None}
_MAP_OK = {"sectors": set(), "reasons": {}}


def _check(ts, *, macro_map=_MAP_OK, **kw):
    return ee.entry_check(bundle={"asset_type": None}, tone_state=ts, sector="Technology",
                          macro_map=macro_map, **kw)


def test_binding_unread_journal_alone_binds_with_no_ledger_kind():
    r = _check(_BULL, trades_read=False)
    assert r["eligible"] is False and r["binding"]["check"] == "trades_unread"
    from stock_analyzer.watchlist_advisor import _HOLD_SUPPRESSION_KIND
    assert _HOLD_SUPPRESSION_KIND.get("trades_unread") is None
    assert gate_ledger._WATCHLIST_KIND.get("trades_unread") is None


def test_binding_a_readable_or_unwired_journal_holds_nothing():
    assert _check(_BULL, trades_read=True)["eligible"] is True
    assert _check(_BULL)["eligible"] is True                      # not wired (None)
    assert _check(_BULL, trades_read=None)["eligible"] is True


def test_binding_bear_beats_unread_journal_and_keeps_its_g29_row():
    r = _check(_BEAR, gate_ids={"tone": "G-29"}, trades_read=False)
    assert [h["check"] for h in r["holds"]] == ["tone", "trades_unread"]
    assert r["binding"]["check"] == "tone" and r["binding"]["gate_id"] == "G-29"


def test_binding_a_g28_macro_block_beats_unread_journal():
    blocked = {"sectors": {"Technology"}, "reasons": {"Technology": "CPI today"}}
    r = _check(_BULL, macro_map=blocked, gate_ids={"macro": "G-28"}, trades_read=False)
    assert r["binding"]["check"] == "macro" and r["binding"]["gate_id"] == "G-28"
    assert [h["check"] for h in r["holds"]] == ["macro", "trades_unread"]


def test_binding_other_policy_holds_beat_unread_journal():
    news = {"compound": -0.9, "n": 3, "headline": "bad"}
    assert _check(_BULL, news=news, trades_read=False)["binding"]["check"] == "news"
    assert _check(_BULL, earnings_days=2, trades_read=False)["binding"]["check"] == "earnings"


def test_binding_tone_unread_binds_before_unread_journal():
    r = _check(_DOWN, trades_read=False)
    assert [h["check"] for h in r["holds"]] == ["tone_unread", "trades_unread"]
    assert r["binding"]["check"] == "tone_unread"


def test_binding_macro_unread_binds_before_unread_journal():
    r = _check(_BULL, macro_map=None, trades_read=False)
    assert [h["check"] for h in r["holds"]] == ["macro_unread", "trades_unread"]
    assert r["binding"]["check"] == "macro_unread"


def test_held_kind_label_and_badge_for_trades_unread():
    assert ee.HELD_KIND_LABELS["trades_unread"] == "trade journal couldn't be read"
    assert ee.held_label({"hold_kind": "trades_unread"}) == "Held: trade journal couldn't be read"


def test_run_ctx_and_ticker_ctx_carry_trades_read():
    now = _et(2026, 11, 25, 10, 0)
    run = ee.watchlist_run_ctx(_BULL, [], date(2026, 11, 25), now)
    assert run["trades_read"] is None                          # not wired = no hold
    run_f = ee.watchlist_run_ctx(_BULL, [], date(2026, 11, 25), now, trades_read=False)
    assert run_f["trades_read"] is False
    t = ee.watchlist_ticker_ctx(run_f, sector="Technology", classified=True, news_items=[])
    assert t["trades_read"] is False
    assert ee.watchlist_ticker_ctx(run, sector="Technology", classified=True,
                                   news_items=[])["trades_read"] is None


# ═════════════════════ banner / footer / Analysis ═════════════════════════════
def _held(ticker, *checks):
    return {"ticker": ticker, "action": "ENTRY_HELD", "holds": [{"check": c} for c in checks]}


def test_banner_journal_block_lists_only_all_journal_cards_and_honours_exclude():
    recs = [_held("AAA", "trades_unread"), _held("BBB", "trades_unread", "news"),
            _held("CCC", "tone"), _held("DDD", "trades_unread"),
            {"ticker": "EEE", "action": "NEAR_ENTRY"}]
    b = ee.watchlist_banner(_BULL, _MAP_OK, recs, exclude={"ddd"}, trades_read=False)
    j = b["trades_unread"]
    assert j["title"] == "Trade journal couldn't be read — new entries held"
    assert "1 name would otherwise be Ready: AAA." in j["body"]
    assert "BBB" not in j["body"] and "DDD" not in j["body"] and "CCC" not in j["body"]
    assert ("The app never suggests buying back a name the same day you sold it, and "
            "without the journal it can't tell which names you sold today. Clears when "
            "the trade journal can be read — re-checked every time you open this page."
            ) in j["body"]
    assert b["unread"] is None and b["macro_unread"] is None and b["protect"] is None
    assert b["notes"] == []
    _no_money_markup(j["title"], j["body"])


def test_banner_journal_block_absent_unless_explicitly_false():
    for tr in (None, True):
        assert ee.watchlist_banner(_BULL, _MAP_OK, [], trades_read=tr)["trades_unread"] is None
    assert ee.watchlist_banner(_BULL, _MAP_OK, [])["trades_unread"] is None
    b = ee.watchlist_banner(_BULL, _MAP_OK, [], trades_read=False)
    assert "would otherwise be Ready" not in b["trades_unread"]["body"]
    assert ee.watchlist_banner(_BULL, _MAP_OK, None, trades_read=False)["trades_unread"] is not None


def test_banner_all_three_unread_is_one_list_never_three():
    recs = [_held("AAA", "tone_unread", "macro_unread", "trades_unread"),
            _held("BBB", "tone_unread"), _held("CCC", "macro_unread"),
            _held("DDD", "trades_unread"), _held("EEE", "tone")]
    b = ee.watchlist_banner(_DOWN, None, recs, trades_read=False)
    assert "4 names would otherwise be Ready: AAA, BBB, CCC, DDD." in b["unread"]["body"]
    assert "would otherwise be Ready" not in b["macro_unread"]["body"]
    assert "would otherwise be Ready" not in b["trades_unread"]["body"]


def test_banner_macro_and_journal_unread_list_once_under_the_macro_block():
    recs = [_held("AAA", "macro_unread", "trades_unread"), _held("BBB", "macro_unread"),
            _held("CCC", "trades_unread")]
    b = ee.watchlist_banner(_BULL, None, recs, trades_read=False)
    assert b["unread"] is None
    assert "3 names would otherwise be Ready: AAA, BBB, CCC." in b["macro_unread"]["body"]
    assert "would otherwise be Ready" not in b["trades_unread"]["body"]


def test_banner_tone_and_journal_unread_list_once_under_the_tone_block():
    recs = [_held("AAA", "tone_unread", "trades_unread"), _held("BBB", "trades_unread")]
    b = ee.watchlist_banner(_DOWN, _MAP_OK, recs, trades_read=False)
    assert "2 names would otherwise be Ready: AAA, BBB." in b["unread"]["body"]
    assert "would otherwise be Ready" not in b["trades_unread"]["body"]


def test_banner_bear_day_keeps_protect_and_adds_the_journal_block():
    b = ee.watchlist_banner(_BEAR, _MAP_OK, [_held("AAA", "tone", "trades_unread")],
                            trades_read=False)
    assert b["protect"] is not None and b["trades_unread"] is not None
    # held by two checks, so it is listed under neither (only all-one-check names are)
    assert "AAA" not in b["protect"]["body"] and "AAA" not in b["trades_unread"]["body"]
    b2 = ee.watchlist_banner(_BEAR, _MAP_OK, [_held("BBB", "tone"), _held("CCC", "trades_unread")],
                             trades_read=False)
    assert "BBB" in b2["protect"]["body"] and "CCC" in b2["trades_unread"]["body"]


def test_banner_without_the_new_argument_is_unchanged_for_old_callers():
    b = ee.watchlist_banner(_BULL, _MAP_OK, [])
    assert set(b) == {"protect", "unread", "macro_unread", "trades_unread", "tone_line",
                      "notes"}


def test_email_footer_only_on_an_explicit_false():
    ts = {"tone": "flat", "sp500_pct": -0.24, "available": True,
          "as_of": datetime(2026, 9, 10, 9, 45)}
    lines = ee.email_footer_lines({"tone_state": ts, "macro_available": True,
                                   "trades_available": False})
    assert lines[1:] == ["Trade journal couldn't be read — no name is listed as Ready to "
                         "Enter this run."]
    assert len(ee.email_footer_lines({"tone_state": ts, "macro_available": True,
                                      "trades_available": True})) == 1
    assert len(ee.email_footer_lines({"tone_state": ts, "macro_available": True})) == 1
    both = ee.email_footer_lines({"tone_state": ts, "macro_available": False,
                                  "trades_available": False})
    assert len(both) == 3
    _no_money_markup(*lines)


def _analysis(trades_read, **kw):
    return ee.analysis_entry_decision(
        bundle={"asset_type": None}, basis="equity", label=kw.get("label", "Buy"),
        held=kw.get("held", False),
        run_ctx=ee.watchlist_run_ctx(_BULL, [], date(2026, 11, 25), _et(2026, 11, 25, 10, 0),
                                     trades_read=trades_read),
        sector="Technology", classified=True, news_items=[], earnings=None,
        today=date(2026, 11, 25), ticker="AAA")


def test_analysis_unread_journal_offers_no_size_and_names_it():
    d = _analysis(False)
    assert d["offer_size"] is False and d["hold"]["check"] == "trades_unread"
    assert d["reason_if_no_size"] == journal_read.unread_reason()
    banner = ee.analysis_hold_banner(d, tone_state=_BULL)
    assert banner["title"].startswith("Not an entry today — held: your trade journal "
                                      "couldn't be read")
    assert ee.analysis_shares_label(d) == "— (held)"


def test_analysis_readable_or_unwired_journal_and_non_buy_or_held_are_unchanged():
    assert _analysis(True)["offer_size"] is True
    assert _analysis(None)["offer_size"] is True
    held = _analysis(False, held=True)
    assert held["offer_size"] is True and held["entry_checked"] is False
    sell = _analysis(False, label="Sell")
    assert sell["entry_checked"] is False and sell["offer_size"] is False
    assert journal_read.analysis_sold_today(held, "AAA") is held
    assert journal_read.analysis_sold_today(sell, "AAA") is sell


# ═════════════════════ Grow Today ═════════════════════════════════════════════
def _gt(s, ctx, **kw):
    port_df = make_port_df(s["port_rows"]) if s["port_rows"] else None
    scanner = golden._scanner_df(s["rows"]) if s["rows"] else None

    class _Clk(golden._FixedDT):
        _h, _m = s["clock"]

    with mock.patch.object(daily_briefing, "_dt", _Clk):
        return _grow_today(
            port_df, scanner, s["news"], s["held_data"], TODAY, 100_000.0, ctx,
            act_today=s["act_today"], review_list=s["review_list"],
            composites=s["composites"], earnings_lookup=s["earnings"],
            macro_events=kw.pop("macro", None), movers=s["movers"],
            etf_candidates=s["etf"], **kw)


def _build(s, ctx, macro_events=(), **kw):
    port_df = make_port_df(s["port_rows"])
    scanner = golden._scanner_df(s["rows"]) if s["rows"] else None

    class _Clk(golden._FixedDT):
        _h, _m = s["clock"]

    with mock.patch.object(daily_briefing, "_dt", _Clk):
        return build_daily_briefing(
            port_df=port_df, alert_list=[], risk_recs=[], news_items=s["news"],
            macro_events=list(macro_events) if macro_events is not None else None,
            held_data=s["held_data"], scanner_results=scanner,
            portfolio_value=100_000.0, today=TODAY, market_context=ctx,
            grow_composites=s["composites"], movers=s["movers"],
            etf_candidates=s["etf"], **kw)


def test_unread_journal_on_a_bull_day_holds_every_list_and_keeps_tone_and_pct():
    g = _gt(_scn(), _read(_OPEN_READ), trades_unread=True)
    assert g["entry_hold"]["check"] == "trades_unread"
    assert g["tone"] == "bull" and g["sp500_pct"] == 0.8 and g["tone_available"] is True
    for k in _PICK_LISTS:
        assert g[k] == [], k
    assert g["fit_unpriced_picks"] == []
    assert g["message"] is None
    assert g["adds_paused"] and g["unevaluated_leads"]           # disclosure only, no size
    assert all("shares" not in a for a in g["adds_paused"])
    assert json.loads(json.dumps(g["entry_hold"])) == g["entry_hold"]
    assert gate_ledger.build_suppression_rows(
        g, rec_date=TODAY, source="app", tone="bull", sp500_pct=0.8) == []


def test_control_a_readable_journal_calls_picks_adds_and_etf():
    g = _gt(_scn(), _read(_OPEN_READ), trades_unread=False)
    assert g["entry_hold"] is None
    assert g["new_picks"] and g["add_positions"]
    assert any(p.get("ticker") == "SPY" for p in g["new_picks"])
    # and the default (every caller before C5) is the same as False
    assert _gt(_scn(), _read(_OPEN_READ))["entry_hold"] is None


def test_bear_day_with_unread_journal_stays_on_the_bear_branch_and_writes_its_g23_row():
    g = _gt(_scn(), _read(_OPEN_READ, tone="bear", sp500_pct=-2.0), trades_unread=True)
    assert g["tone"] == "bear" and g["entry_hold"] is None and g["message"]
    rows = gate_ledger.build_suppression_rows(
        g, rec_date=TODAY, source="app", tone="bear", sp500_pct=-2.0)
    assert [r["gate_id"] for r in rows] == ["G-23"]


def test_unread_macro_calendar_binds_before_unread_journal():
    g = _gt(_scn(), _read(_OPEN_READ), macro_unread=True, trades_unread=True)
    assert g["entry_hold"]["check"] == "macro_unread"


def test_unread_tone_binds_before_unread_journal():
    g = _gt(_scn(), _read(_OPEN_READ, tone_available=False), trades_unread=True)
    assert g["entry_hold"]["check"] == "tone_unread" and g["sp500_pct"] is None


def test_preopen_with_unread_journal_merges_the_open_into_one_banner():
    g = _gt(_scn(), _read(_PREOPEN_READ), trades_unread=True)
    h = g["entry_hold"]
    assert h["check"] == "trades_unread" and h["session"] == "2026-10-14"
    assert h["clears_when"] == ("when the trade journal can be read again, and after the "
                                "9:30 AM ET open")
    assert g["tone"] == "bull" and g["sp500_pct"] == 0.8
    for k in _PICK_LISTS:
        assert g[k] == [], k


def test_the_journal_hold_return_has_the_bear_returns_keys():
    held = _gt(_scn(), _read(_OPEN_READ), trades_unread=True)
    bear = _gt(_scn(), _read(_OPEN_READ, tone="bear", sp500_pct=-2.0))
    assert set(held) == set(bear)


def test_build_with_trades_unread_true_holds_and_leaves_act_today_and_review_alone():
    s = _scn()
    held = _build(s, _read(_OPEN_READ), trades_unread=True)
    clean = _build(s, _read(_OPEN_READ), trades_unread=False)
    assert held["grow_today"]["entry_hold"]["check"] == "trades_unread"
    assert clean["grow_today"]["entry_hold"] is None
    assert held["act_today"] == clean["act_today"]
    assert held["review_list"] == clean["review_list"]


def test_build_with_a_frame_that_cannot_be_parsed_is_held_even_unflagged():
    s = _scn()
    no_traded_at = pd.DataFrame([{"ticker": "SAP", "action": "SELL", "shares": 1}])
    g = _build(s, _read(_OPEN_READ), trades_df=no_traded_at)["grow_today"]
    assert g["entry_hold"]["check"] == "trades_unread"
    no_action = pd.DataFrame([{"ticker": "SAP", "traded_at": "2026-10-15T15:00:00+00:00"}])
    assert _build(s, _read(_OPEN_READ), trades_df=no_action)["grow_today"][
        "entry_hold"]["check"] == "trades_unread"


def test_build_with_no_journal_at_all_and_not_flagged_keeps_todays_behaviour():
    g = _build(_scn(), _read(_OPEN_READ), trades_df=None)["grow_today"]
    assert g["entry_hold"] is None and g["new_picks"]
    g2 = _build(_scn(), _read(_OPEN_READ), trades_df=None, trades_unread=False)["grow_today"]
    assert g2["entry_hold"] is None


def test_build_with_an_empty_readable_journal_is_not_held():
    empty = pd.DataFrame(columns=["ticker", "action", "traded_at"])
    g = _build(_scn(), _read(_OPEN_READ), trades_df=empty)["grow_today"]
    assert g["entry_hold"] is None and g["new_picks"]


def test_build_unread_flag_wins_even_when_a_frame_is_supplied():
    ok = pd.DataFrame(columns=["ticker", "action", "traded_at"])
    g = _build(_scn(), _read(_OPEN_READ), trades_df=ok, trades_unread=True)["grow_today"]
    assert g["entry_hold"]["check"] == "trades_unread"


def test_build_a_known_sell_keeps_the_existing_per_name_path_not_the_day_hold():
    # 2026-07-27 (Mon): a SELL at Mon 23:30 ET (= Tue 03:30Z) still counts as Monday's.
    day = date(2026, 7, 27)
    port_df = make_port_df([])
    scanner = _scanner_df([{"ticker": "SAP", "score": COMPOSITE_BUY + 10, "sector": "Tech"}])
    composites = {"SAP": {"total": COMPOSITE_BUY + 10, "rec": {"label": "Buy"},
                          "fundamentals_available": True}}

    def _run(ts):
        trades = pd.DataFrame([{"ticker": "SAP", "action": "SELL", "shares": 7,
                                "price": 210.0, "traded_at": ts}])
        return build_daily_briefing(
            port_df, [], [], [], [], {}, scanner, 100_000.0, day,
            market_context={"tone": "bull"}, grow_composites=composites, trades_df=trades,
        )["grow_today"]

    late = _run("2026-07-28T03:30:00+00:00")             # Mon 23:30 ET
    assert late["entry_hold"] is None
    assert find_item(late["new_picks"], "SAP") is None   # blocked by name, not by the day hold
    next_day = _run("2026-07-28T04:30:00+00:00")         # Tue 00:30 ET: Tuesday's sale
    assert find_item(next_day["new_picks"], "SAP") is not None


# ── readers of entry_hold ───────────────────────────────────────────────────
def _journal_grow():
    return _gt(_scn(), _read(_OPEN_READ), trades_unread=True)


def test_held_add_verdict_names_the_journal_reason():
    v = sizing_path.held_add_verdict(
        "ZADDOK", _journal_grow(), brief_date="2026-10-15", today="2026-10-15",
        brief_epoch=1, epoch=1)
    assert v["state"] == "not_called"
    assert "holds new picks and adds" in v["reason"]
    assert "your trade journal couldn't be read" in v["reason"]
    assert "up day" not in v["reason"]


def test_hold_reasons_journal_wording_and_the_merged_hold_keeps_the_journal_label():
    assert hold_reasons.adds_paused_label("flat", 2, journal_read.grow_hold()) == \
        "Adds held — trade journal couldn't be read (2)"
    merged = journal_read.grow_hold(tf.grow_entry_hold(_read(_PREOPEN_READ), date(2026, 10, 15)))
    assert hold_reasons.adds_paused_label("flat", 3, merged) == \
        "Adds held — trade journal couldn't be read (3)"
    note = hold_reasons.adds_paused_note(["MSFT"], journal_read.grow_hold())
    assert "your trade journal couldn't be read" in note and "no size" in note
    assert "Resumes when the trade journal can be read again." in note
    assert "next up day" not in note
    lead = hold_reasons.leads_note("flat", journal_read.grow_hold())
    assert "no verdict" in lead and "flat day" not in lead
    _no_money_markup(note, lead)


def test_other_adds_paused_labels_are_unchanged():
    unread = tf.grow_entry_hold({"tone_available": False}, date(2026, 10, 15))
    assert hold_reasons.adds_paused_label("flat", 2, unread) == \
        "Adds held — market move couldn't be read (2)"
    assert hold_reasons.adds_paused_label("flat", 2, macro_read.grow_hold()) == \
        "Adds held — macro calendar couldn't be read (2)"


def test_redeploy_headline_defers_to_a_journal_hold():
    hold = journal_read.grow_hold()
    ts = {"tone": "bull", "sp500_pct": 0.8, "available": True, "source": "home",
          "as_of": _et(2026, 11, 25, 10, 0), "why": None, "home_read_at": None}
    assert tf.redeploy_grow_hold(hold, ts, date(2026, 11, 25)) is hold
    out = ep.redeploy_call({"state": "entry", "ticker": "AAA", "sector": "Tech",
                            "score": 70.0, "rr": 2.5}, ep.new_entry_posture(ts),
                           grow_hold=hold)
    assert out["entry_called"] is False and out["icon"] == "⏸"
    assert "Grow Today holds new entries: your trade journal couldn't be read" in out["text"]
    assert "They return when the trade journal can be read again." in out["text"]
    assert "$" not in out["text"]       # (the leading **bold** is the redeploy renderer's own)


# ═════════════════════ cron: _build_context + compute_morning_picks ════════════
def test_build_context_always_sets_trades_read():
    from tests.test_headless_alert_engine import _patch_context_deps, _run_build_context
    unread = _run_build_context(_patch_context_deps(load_trades=None))
    assert unread["ok"] is True and unread["trades_read"] is False
    # the lenient stand-in is only an empty frame; the flag is what tells them apart
    assert unread["trades_df"] is not None and len(unread["trades_df"]) == 0
    readable = _run_build_context(_patch_context_deps(
        load_trades=pd.DataFrame(columns=["ticker", "action", "traded_at"])))
    assert readable["ok"] is True and readable["trades_read"] is True


_IDX = [{"short": "S&P 500", "change_pct": 0.8}]


def _morning(extra_ctx=None, brief=None, macro=()):
    ctx = {
        "ok": True, "errors": [],
        "port_df": pd.DataFrame({"Ticker": ["AAPL"], "Market Value": [1000.0]}),
        "held_data": {"AAPL": {}}, "fragility": None, "spy_6mo": None,
        "spy_1y": None, "vix": None, "holdings_df": pd.DataFrame({"Ticker": ["AAPL"]}),
    }
    ctx.update(extra_ctx or {})
    scanner = pd.DataFrame([{"Ticker": "NVDA", "Score": 90.0, "Price": 100.0, "Signal": "Buy",
                             "Sector": "Technology", "RSI": 60.0, "1M Momentum": 5.0,
                             "Trend": "Up"}])
    P = mock.patch
    with ExitStack() as st:
        st.enter_context(P("stock_analyzer.headless_alert_engine._build_context", return_value=ctx))
        st.enter_context(P("stock_analyzer.data.fetch_market_indices", return_value=_IDX))
        st.enter_context(P("stock_analyzer.headless_alert_engine.load_bundle", return_value={}))
        st.enter_context(P("stock_analyzer.data.curate_news_items", return_value=[]))
        st.enter_context(P("stock_analyzer.macro_calendar.build_macro_calendar",
                           return_value=list(macro)))
        if brief is not None:
            st.enter_context(P("stock_analyzer.headless_alert_engine.build_daily_briefing", brief))
        return hae.compute_morning_picks(date(2026, 10, 16), scanner_results=scanner)


def test_cron_unread_journal_holds_the_day_end_to_end():
    r = _morning({"trades_read": False, "trades_df": pd.DataFrame(
        columns=["ticker", "action", "traded_at"])})
    assert r["picks"] == []
    assert r["diag"]["entry_hold"] == "trades_unread" and r["diag"]["bar"] is None
    assert r["grow"]["entry_hold"]["check"] == "trades_unread"


def test_cron_readable_or_unflagged_journal_does_not_hold():
    for extra in ({"trades_read": True}, {}):
        r = _morning(extra)
        assert r["diag"]["entry_hold"] != "trades_unread", extra
        assert r["grow"]["entry_hold"] is None or r["grow"]["entry_hold"]["check"] != "trades_unread"


def _brief_mock():
    brief = {"grow_today": {"tone": "bull", "new_picks": [], "sp500_pct": 0.5,
                            "sector_blocked_picks": [], "macro_blocked_picks": [],
                            "composite_skipped": [], "composite_unavailable": []}}
    return mock.MagicMock(return_value=brief)


@pytest.mark.parametrize("extra, flag", [
    ({"trades_read": False}, True), ({"trades_read": True}, False), ({}, False),
])
def test_cron_passes_trades_unread_to_the_brief_and_still_trades_df(extra, flag):
    frame = pd.DataFrame({"ticker": ["NVDA"], "action": ["SELL"],
                          "traded_at": ["2026-10-16T13:35:00+00:00"]})
    m = _brief_mock()
    _morning({**extra, "trades_df": frame}, brief=m)
    kw = m.call_args.kwargs
    assert kw["trades_unread"] is flag
    assert kw["trades_df"] is frame            # the B-H1 wiring is untouched


# ═════════════════════ cron: the Watchlist lane ═══════════════════════════════
from tests.test_watchlist_entry_cron import (   # noqa: E402  (after the fixtures above)
    BEAR_CTX, FLAT_CTX, REAL_BUILD, SEMIS, _bundle, _ctx as _wl_ctx, _holdings,
)
from tests.test_watchlist_entry_cron import TODAY as WL_TODAY   # noqa: E402

_UNSET = object()


def _wle(*, trades=_UNSET, trades_exc=None, entry_context=FLAT_CTX, held=(), ctx=None):
    """compute_watchlist_entries at the I/O boundary with a chosen strict trade read."""
    trades = pd.DataFrame() if trades is _UNSET else trades
    spy: list = []

    def _build_rec(ticker, data, portfolio_ctx=None, entry_ctx=None):
        card = REAL_BUILD(ticker, data, portfolio_ctx=portfolio_ctx, entry_ctx=entry_ctx)
        spy.append((ticker, portfolio_ctx, entry_ctx, card))
        return card

    with ExitStack() as st:
        P = lambda target, **kw: st.enter_context(  # noqa: E731
            mock.patch(f"stock_analyzer.headless_alert_engine.{target}", **kw))
        P("db.load_watchlist_or_none", return_value=[SEMIS])
        P("db.load_holdings_or_none", return_value=_holdings(held))
        P("fetch_risk_free_rate", return_value=0.045)
        P("fetch_spy", return_value=None)
        P("load_bundle", return_value=_bundle())
        P("db.load_exit_signals_or_none", return_value=pd.DataFrame())
        P("db.load_trades_or_none", return_value=trades, side_effect=trades_exc)
        P("db.load_recommendations_or_none", return_value=pd.DataFrame())
        save = P("db.save_recommendations", return_value={"error": None})
        P("is_trading_day", return_value=True)
        P("build_watchlist_recommendation", side_effect=_build_rec)
        P("db.load_gate_holds_on_day_or_none", return_value={})
        P("_live_entry_context", side_effect=lambda today, port_df: FLAT_CTX)
        result = hae.compute_watchlist_entries(
            WL_TODAY, watchlist=[SEMIS], ctx=ctx if ctx is not None else _wl_ctx(),
            entry_context=entry_context)
    return result, save, spy


@pytest.mark.parametrize("kw", [
    {"trades": None}, {"trades_exc": RuntimeError("db down")},
    {"trades": pd.DataFrame([{"ticker": "NVDA"}])},          # no traded_at: unparseable
])
def test_watchlist_unread_journal_holds_every_name_and_reports_it(kw):
    r, save, spy = _wle(**kw)
    assert r["entries"] == []
    card = spy[0][3]
    assert card["action"] == "ENTRY_HELD" and card["hold_kind"] == "trades_unread"
    assert card["suppression_kind"] is None
    assert spy[0][2]["trades_read"] is False
    save.assert_not_called()                           # a held card is never captured (QB1)
    assert r["gate_ledger_rows"] == []
    assert r["checks_held"]["trades_unread"] == [SEMIS]
    assert r["entry_run"]["trades_available"] is False
    assert any("sold-today" in e for e in r["errors"])
    lines = ee.email_footer_lines(r["entry_run"])
    assert ("Trade journal couldn't be read — no name is listed as Ready to Enter this "
            "run.") in lines


def test_watchlist_readable_journal_is_available_and_the_name_is_called():
    r, save, spy = _wle(trades=pd.DataFrame())
    assert spy[0][3]["action"] == "ENTER_NOW" and spy[0][2]["trades_read"] is True
    assert r["entry_run"]["trades_available"] is True
    assert r["checks_held"]["trades_unread"] == []
    assert [c["ticker"] for c in r["entries"]] == [SEMIS]
    assert save.called
    assert len(ee.email_footer_lines(r["entry_run"])) == 1


def test_watchlist_a_known_sell_keeps_the_per_name_path_not_the_hold():
    sold = pd.DataFrame([{"ticker": SEMIS, "action": "SELL",
                          "traded_at": f"{WL_TODAY.isoformat()}T15:00:00+00:00"}])
    r, save, spy = _wle(trades=sold)
    assert spy[0][3]["action"] == "ENTER_NOW"          # captured for grading ...
    assert r["entries"] == []                          # ... but never emailed
    assert r["entry_run"]["trades_available"] is True
    assert r["checks_held"]["trades_unread"] == []


def test_watchlist_bear_day_with_unread_journal_binds_tone_and_keeps_its_g29_row():
    r, _save, spy = _wle(trades=None, entry_context=BEAR_CTX)
    assert spy[0][3]["hold_kind"] == "tone" and spy[0][3]["suppression_kind"] == "entry_tone"
    assert r["checks_held"]["trades_unread"] == []    # bound by the policy hold, not counted
    assert r["gate_ledger_rows"]


def test_watchlist_a_held_in_portfolio_name_is_not_counted_in_checks_held():
    r, *_ = _wle(trades=None, held=(SEMIS,))
    assert r["checks_held"]["trades_unread"] == []


def test_watchlist_a_macro_crash_keeps_the_journal_read_in_the_fallback_ctx():
    with mock.patch("stock_analyzer.headless_alert_engine.watchlist_run_ctx",
                    side_effect=RuntimeError("boom")):
        r, _save, spy = _wle(trades=None)
    assert any("entry check macro failed" in e for e in r["errors"])
    assert spy[0][2]["trades_read"] is False
    assert spy[0][3]["hold_kind"] in ("macro_unread", "trades_unread")
    assert r["entry_run"]["trades_available"] is False and r["entries"] == []


def test_watchlist_checks_held_line_names_trades_unread():
    line = cr._watchlist_checks_held_line(
        {"checks_held": {"fit_unknown": [], "beta_unknown": [], "tone_unread": [],
                         "macro_unread": [], "trades_unread": ["AAA", "BBB"]}})
    assert line == "watchlist: 2 held, a check couldn't run (trades_unread: AAA, BBB)"
    assert cr._watchlist_checks_held_line({"checks_held": {"trades_unread": []}}) is None


# ═════════════════════ cron_runner: texts + lane failures ═════════════════════
@pytest.mark.parametrize("hold, text", [
    (tf.CHECK_UNREAD, "market move couldn't be read — new picks held (G-34)"),
    (tf.CHECK_NOT_TODAY, "the market read isn't today's session — new picks held (G-34)"),
    (macro_read.CHECK_UNREAD, "macro calendar couldn't be read — new picks held (G-34)"),
    (journal_read.CHECK_UNREAD, "trade journal couldn't be read — new picks held (G-34)"),
])
def test_entry_hold_text_covers_every_check_grow_today_can_emit(hold, text):
    assert cr._entry_hold_text(hold) == text


def test_every_grow_today_hold_check_constant_has_its_own_log_text():
    checks = []
    for mod in (tf, macro_read, journal_read):
        for name, val in vars(mod).items():
            if name.startswith("CHECK_") and isinstance(val, str) and val != "sold_today":
                checks.append(val)
    assert {"tone_unread", "tone_not_today", "macro_unread", "trades_unread"} <= set(checks)
    texts = [cr._entry_hold_text(c) for c in checks]
    assert len(set(texts)) == len(texts)           # none falls through to another's wording


class _ProxyMP:
    """Replace the scan helper's stubbed Watchlist compute with one that carries an
    `entry_run` (the Watchlist run's own strict journal read)."""

    def __init__(self, mp, wle_payload):
        self._mp, self._payload = mp, wle_payload

    def setattr(self, target, name, value=None, **kw):
        if name == "compute_watchlist_entries":
            value = lambda **_k: self._payload      # noqa: E731
        return self._mp.setattr(target, name, value, **kw)

    def __getattr__(self, name):
        return getattr(self._mp, name)


def _scan_journal(monkeypatch, diag, entry_run=None):
    payload = {"entries": [], "built_at": "x", "errors": [], "reason": None,
               "gate_degraded": False, "news_feed": {"state": "ok"}}
    if entry_run is not None:
        payload["entry_run"] = entry_run
    return _run_scan(_ProxyMP(monkeypatch, payload), diag)


def test_scan_unread_journal_logs_names_it_reddens_the_lane_and_sends_a_failure(
        monkeypatch, capsys):
    diag = {"tone": "bull", "sp500_pct": 0.8, "bar": None, "entry_hold": "trades_unread"}
    rc, emails, notified = _scan_journal(monkeypatch, diag)
    out = capsys.readouterr().out
    assert "trade journal couldn't be read — new picks held (G-34)" in out
    assert "JOURNAL UNREAD — trades_unread: the trade journal couldn't be read" in out
    assert rc == 1 and notified and notified[0][0] == "scan"
    assert any(f.startswith("trades_unread:") for f in notified[0][1])
    assert "morning-action" not in emails


def test_scan_the_watchlist_runs_own_unread_journal_reddens_the_lane_too(monkeypatch, capsys):
    diag = {"tone": "bull", "sp500_pct": 0.8, "bar": 65, "entry_hold": None}
    rc, _e, notified = _scan_journal(monkeypatch, diag, entry_run={"trades_available": False})
    assert "JOURNAL UNREAD —" in capsys.readouterr().out
    assert rc == 1 and any(f.startswith("trades_unread:") for f in notified[0][1])


def test_scan_one_line_when_both_the_grow_hold_and_the_watchlist_run_are_unread(
        monkeypatch):
    diag = {"tone": "bull", "sp500_pct": 0.8, "bar": None, "entry_hold": "trades_unread"}
    _rc, _e, notified = _scan_journal(monkeypatch, diag, entry_run={"trades_available": False})
    assert [f for f in notified[0][1] if f.startswith("trades_unread:")] == [
        journal_read.failure_line({"diag": diag})]


def test_scan_readable_journal_adds_no_failure(monkeypatch, capsys):
    diag = {"tone": "bull", "sp500_pct": 0.8, "bar": 65, "entry_hold": None}
    rc, _e, notified = _scan_journal(monkeypatch, diag, entry_run={"trades_available": True})
    assert "JOURNAL UNREAD" not in capsys.readouterr().out
    assert rc == 0 and notified == []


def test_intraday_unread_journal_logs_and_reddens_the_lane(monkeypatch, capsys):
    rc, emails, notified = _run_intraday(monkeypatch, {"entry_hold": "trades_unread"})
    out = capsys.readouterr().out
    assert "intraday: trade journal couldn't be read — new picks held (G-34)" in out
    assert "JOURNAL UNREAD —" in out
    assert rc == 1 and emails == {}
    assert notified[0][0] == "intraday"
    assert any(f.startswith("trades_unread:") for f in notified[0][1])


def test_intraday_readable_journal_adds_no_failure(monkeypatch, capsys):
    rc, _e, notified = _run_intraday(monkeypatch, {"entry_hold": None})
    assert "JOURNAL UNREAD" not in capsys.readouterr().out
    assert rc == 0 and notified == []


# ═════════════════════ AST / source-order pins ═════════════════════════════════
def _fn(tree, name):
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _call_kwargs(tree, callee):
    return [{k.arg: k.value for k in n.keywords} for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == callee]


def test_pin_cron_brief_call_passes_both_trades_df_and_trades_unread():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "headless_alert_engine.py")
    calls = _call_kwargs(_fn(tree, "compute_morning_picks"), "build_daily_briefing")
    assert len(calls) == 1
    assert "trades_df" in calls[0] and "trades_unread" in calls[0]
    seg = ast.dump(calls[0]["trades_unread"])
    assert "trades_read" in seg and "False" in seg           # `ctx.get("trades_read") is False`


def test_pin_build_context_ok_dict_always_carries_trades_read():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "headless_alert_engine.py")
    fn = _fn(tree, "_build_context")
    ok_dicts = [d for d in ast.walk(fn) if isinstance(d, ast.Dict)
                and any(isinstance(k, ast.Constant) and k.value == "ok" for k in d.keys)
                and any(isinstance(v, ast.Constant) and v.value is True for v in d.values)]
    assert len(ok_dicts) == 1
    keys = [k.value for k in ok_dicts[0].keys if isinstance(k, ast.Constant)]
    assert "trades_read" in keys


def test_pin_watchlist_run_ctx_call_and_fallback_carry_the_strict_journal_read():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "headless_alert_engine.py")
    fn = _fn(tree, "compute_watchlist_entries")
    calls = _call_kwargs(fn, "watchlist_run_ctx")
    assert len(calls) == 1 and "trades_read" in calls[0]
    fallbacks = [d for d in ast.walk(fn) if isinstance(d, ast.Dict)
                 and any(isinstance(k, ast.Constant) and k.value == "macro_map" for k in d.keys)]
    assert fallbacks, "the macro-failure fallback dict disappeared"
    for d in fallbacks:
        assert "trades_read" in [k.value for k in d.keys if isinstance(k, ast.Constant)]
    runs = [d for d in ast.walk(fn) if isinstance(d, ast.Dict)
            and any(isinstance(k, ast.Constant) and k.value == "macro_available" for k in d.keys)]
    assert runs and all("trades_available" in [k.value for k in d.keys
                                               if isinstance(k, ast.Constant)] for d in runs)


def test_pin_the_strict_journal_read_precedes_the_run_context_and_is_read_once():
    src = (ROOT / "stock_analyzer" / "headless_alert_engine.py").read_text(encoding="utf-8")
    body = src[src.index("def compute_watchlist_entries"):]
    body = body[:body.index("\ndef _budget_candidate")] if "\ndef _budget_candidate" in body else body
    i_read = body.index("tickers_sold_on(db.load_trades_or_none()")
    i_ctx = body.index("watchlist_run_ctx(")
    assert i_read < i_ctx
    assert body.count("db.load_trades_or_none()") == 1       # read once, strictly


def test_pin_grow_today_journal_hold_sits_after_macro_and_before_not_today_and_picks():
    src = (ROOT / "stock_analyzer" / "daily_briefing.py").read_text(encoding="utf-8")
    i_unread = src.index("tone_freshness.CHECK_UNREAD:\n        return _entry_hold_return()")
    i_bear = src.index('    if tone == "bear":\n        # macro_coverage_expired')
    i_macro = src.index("    if macro_unread:\n        _entry_hold = macro_read.grow_hold(")
    i_jr = src.index("    if trades_unread:\n        _entry_hold = journal_read.grow_hold(")
    i_nt = src.index("    if _entry_hold is not None:\n        return _entry_hold_return()\n\n    # Score")
    assert i_unread < i_bear < i_macro < i_jr < i_nt


def test_pin_one_production_caller_of_grow_today_passes_the_unread_flag():
    calls = []
    for path in ("stock_analyzer", "cron_runner.py", "app.py"):
        p = ROOT / path
        for f in (sorted(p.rglob("*.py")) if p.is_dir() else [p]):
            calls += [(f.name, k) for k in _call_kwargs(parse_file_cached(f), "_grow_today")]
    assert [c[0] for c in calls] == ["daily_briefing.py"]
    assert "trades_unread" in calls[0][1]
    assert "_trades_unread" in ast.dump(calls[0][1]["trades_unread"])


def test_pin_the_brief_unread_rule_is_flag_or_unparseable_frame():
    src = (ROOT / "stock_analyzer" / "daily_briefing.py").read_text(encoding="utf-8")
    assert ("_trades_unread = bool(trades_unread) or (\n"
            "        trades_df is not None and _sold_today_or_none is None)") in src


def test_pin_cron_registers_the_journal_in_kinds_text_and_both_failure_sites():
    src = (ROOT / "cron_runner.py").read_text(encoding="utf-8")
    assert "journal_read.CHECK_UNREAD" in src
    assert src.count("journal_read.failure_line(") == 2     # scan + intraday
    assert "JOURNAL UNREAD" in src
    tree = parse_file_cached(ROOT / "cron_runner.py")
    scan = _fn(tree, "_run_scan")
    assert any(isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "failure_line"
               and getattr(n.func.value, "id", None) == "journal_read"
               and len(n.args) == 2 for n in ast.walk(scan))      # payload + the Watchlist run
