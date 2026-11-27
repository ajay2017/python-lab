"""Buy-check C4m (docs/plans/entry-fail-closed-c4m-spec.md; owner Q1-Q4 2026-11-27):
an unreadable macro-event calendar HOLDS new buys instead of reading as "no event".

Covers `macro_read` (pure), `check_macro` + the binding order, the Watchlist banner /
email footer / Analysis wording, Grow Today's whole-day `macro_unread` hold through
`build_daily_briefing(macro_events=None)`, the readers of `entry_hold`, the cron
producers and failure lines, the FRED guard in `build_macro_calendar`, and the AST pins
for both ends of each wiring (app.py is never imported). No network, no DB."""
import ast
import datetime as _dt
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
from stock_analyzer import gate_ledger, hold_reasons, macro_read, sizing_path
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer import tone_freshness as tf
from stock_analyzer.daily_briefing import _grow_today, build_daily_briefing
from tests import test_grow_today_golden as golden
from tests.conftest import make_port_df
from tests.test_daily_briefing import _etf_candidate
from tests.test_entry_hold_c4b import (
    _OPEN_READ, _PICK_LISTS, _PREOPEN_READ, _read, _run_intraday, _run_scan, _scn,
)
from tests._ast_cache import parse_file_cached, source_segment

pytestmark = pytest.mark.fast

ET = ZoneInfo("America/New_York")
ROOT = pathlib.Path(__file__).resolve().parent.parent
TODAY = golden._TODAY                      # Thu 2026-10-15, a trading day
_APP_SRC = (ROOT / "app.py").read_text(encoding="utf-8")


def _et(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET)


# ═════════════════════ macro_read (pure) ══════════════════════════════════════
@pytest.mark.parametrize("raw", [None, "x", {}, 5, ("a",)])
def test_events_or_none_non_list_is_none(raw):
    assert macro_read.events_or_none(raw) is None
    assert macro_read.events_unread(raw) is True


def test_events_or_none_keeps_a_list_including_the_empty_one():
    ev = [{"event": "CPI"}]
    assert macro_read.events_or_none(ev) is ev
    empty: list = []
    assert macro_read.events_or_none(empty) is empty
    assert macro_read.events_unread([]) is False        # checked, none: NOT a hold
    assert macro_read.events_unread(ev) is False


@pytest.mark.parametrize("m", [None, {"reasons": {}}, "x", 5, [], {"sectors": None}])
def test_map_unread_true_for_a_missing_or_malformed_map(m):
    assert macro_read.map_unread(m) is True


@pytest.mark.parametrize("m", [{"sectors": set()}, {"sectors": {"Energy"}, "reasons": {}},
                               {"sectors": {"__ALL__"}, "reasons": {"__ALL__": "FOMC"}}])
def test_map_unread_false_for_a_usable_map(m):
    assert macro_read.map_unread(m) is False


def test_unread_reason_and_entry_hold_shape():
    assert macro_read.unread_reason() == "the macro-event calendar couldn't be read"
    h = macro_read.entry_hold()
    assert h["kind"] == "hold" and h["check"] == "macro_unread" and h["gate_id"] is None
    assert h["reason"] == macro_read.unread_reason()
    assert h["gate_value"] is None and h["gate_threshold"] is None
    assert "re-checked every time you open this page" in h["clears_when"]


def test_grow_hold_alone_is_json_safe_strings_with_the_banner():
    h = macro_read.grow_hold()
    assert json.loads(json.dumps(h)) == h
    assert set(h) == {"check", "reason", "clears_when", "banner", "session"}
    assert h["check"] == "macro_unread" and h["session"] is None
    assert h["clears_when"] == "when the macro calendar is read again"
    assert h["banner"].startswith("⏸ New picks, ETF picks and adds are held: "
                                  "the macro-event calendar couldn't be read, so Grow Today")
    assert "next 3 days" in h["banner"]
    assert h["banner"].endswith("They return when the macro calendar is read again.")
    assert "$" not in h["banner"] and "**" not in h["banner"]
    assert macro_read.grow_hold(tone_hold=None) == h


def test_grow_hold_merges_a_preopen_tone_not_today_hold():
    tone = tf.grow_entry_hold(
        {"tone": "bull", "sp500_pct": 0.5, "tone_available": True,
         "tone_read_at": _et(2026, 11, 25, 8, 0)}, date(2026, 11, 25))
    h = macro_read.grow_hold(tone_hold=tone)
    assert h["check"] == "macro_unread" and h["session"] == "2026-11-24"
    assert h["clears_when"] == "when the macro calendar is read again, and after the 9:30 AM ET open"
    assert h["banner"].endswith(f"They return {h['clears_when']}.")
    assert json.loads(json.dumps(h)) == h


def test_grow_hold_merges_a_saturday_and_a_thanksgiving_hold():
    sat = tf.grow_entry_hold(
        {"tone": "bull", "sp500_pct": 0.5, "tone_available": True,
         "tone_read_at": _et(2026, 11, 28, 10, 0)}, date(2026, 11, 28))
    assert macro_read.grow_hold(sat)["clears_when"].endswith(", and on Mon Nov 30 after the open")
    tg = tf.grow_entry_hold(
        {"tone": "bull", "sp500_pct": 0.5, "tone_available": True,
         "tone_read_at": _et(2026, 11, 26, 11, 0)}, date(2026, 11, 26))
    h = macro_read.grow_hold(tg)
    assert h["clears_when"].endswith(", and on Fri Nov 27 after the open")
    assert h["session"] == "2026-11-25"


def test_grow_hold_does_not_merge_a_tone_unread_hold():
    unread = tf.grow_entry_hold({"tone_available": False}, date(2026, 11, 25))
    h = macro_read.grow_hold(tone_hold=unread)
    assert h["clears_when"] == "when the macro calendar is read again" and h["session"] is None


def test_retry_caption_only_for_a_macro_hold():
    cap = macro_read.retry_caption(macro_read.grow_hold())
    assert cap.startswith("🔄 Refresh macro re-reads the calendar")
    assert "every 30 minutes" in cap and "$" not in cap and "**" not in cap
    assert macro_read.retry_caption(tf.grow_entry_hold({"tone_available": False},
                                                       date(2026, 11, 25))) is None
    for bad in (None, {}, "x", {"check": "tone_not_today"}):
        assert macro_read.retry_caption(bad) is None


def test_failure_line_key_absent_none_and_entry_run_false():
    assert macro_read.failure_line({"market_context": {}}) is None        # key absent
    assert macro_read.failure_line({"macro_events": []}) is None           # checked, none
    assert macro_read.failure_line({"macro_events": [{"event": "x"}]}) is None
    line = macro_read.failure_line({"macro_events": None})
    assert line.startswith("macro_unread: the macro calendar couldn't be read")
    assert "no buy-list, intraday pick or Watchlist Enter Now this run" in line
    assert "$" not in line and "**" not in line
    assert macro_read.failure_line(None, {"macro_available": False}) == line
    assert macro_read.failure_line({"macro_events": []}, {"macro_available": True}) is None
    assert macro_read.failure_line({"macro_events": []}, {"tone_state": {}}) is None
    for bad in (None, "x", 5, []):
        assert macro_read.failure_line(bad) is None


def test_failure_line_never_raises():
    class _Boom(dict):
        def __contains__(self, k):
            raise RuntimeError("x")
    assert macro_read.failure_line(_Boom()) is None


def test_macro_read_does_not_import_entry_eligibility_or_read_a_clock():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "macro_read.py")
    mods = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mods.append(n.module or "")
            mods += [(n.module or "") + "." + a.name for a in n.names]
        elif isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
    joined = " ".join(mods)
    assert "entry_eligibility" not in joined and "streamlit" not in joined
    assert "datetime" not in joined and "db" not in joined.split()
    src = (ROOT / "stock_analyzer" / "macro_read.py").read_text(encoding="utf-8")
    assert "now(" not in src and "today(" not in src and "time.time" not in src


# ═════════════════════ check_macro + the binding order ═════════════════════════
def _ctx(**kw):
    base = {"bundle": None, "sector": "Technology", "macro_map": None}
    base.update(kw)
    return base


@pytest.mark.parametrize("m", [None, {"reasons": {}}, {"sectors": None}, "x", 5])
@pytest.mark.parametrize("sector", ["Technology", None, "Wacky Label"])
@pytest.mark.parametrize("etf", [False, True])
def test_check_macro_unusable_map_is_a_macro_unread_hold(m, sector, etf):
    bundle = {"asset_type": "ETF"} if etf else {}
    r = ee.check_macro(_ctx(macro_map=m, sector=sector, bundle=bundle))
    assert r["kind"] == "hold" and r["check"] == "macro_unread" and r["gate_id"] is None
    assert r["reason"] == "the macro-event calendar couldn't be read"


def test_check_macro_the_empty_map_passes_and_g28_and_unknown_sector_are_unchanged():
    assert ee.check_macro(_ctx(macro_map={"sectors": set(), "reasons": {}})) is None
    blocked = ee.check_macro(_ctx(
        macro_map={"sectors": {"Technology"}, "reasons": {"Technology": "CPI today"}},
        gate_ids={"macro": "G-28"}))
    assert blocked["check"] == "macro" and blocked["gate_id"] == "G-28"
    assert blocked["reason"] == "macro event · CPI today"
    unknown = ee.check_macro(_ctx(
        macro_map={"sectors": {"Energy"}, "reasons": {"Energy": "CPI"}}, sector="Wacky Label"))
    assert unknown["check"] == "macro_unknown_sector"


def _bundle_ok():
    return {"asset_type": None}


def _check(ts, **kw):
    return ee.entry_check(bundle=_bundle_ok(), tone_state=ts, sector="Technology",
                          macro_map=None, **kw)


_BULL = {"tone": "bull", "sp500_pct": 0.5, "available": True, "source": "home",
         "as_of": None, "why": None, "home_read_at": None}
_BEAR = {**_BULL, "tone": "bear", "sp500_pct": -2.0}
_DOWN = {"tone": None, "sp500_pct": None, "available": False, "source": None,
         "as_of": None, "why": "live_failed", "home_read_at": None}


def test_binding_macro_unread_alone_binds_with_no_ledger_suppression_kind():
    r = _check(_BULL)
    assert r["eligible"] is False and r["binding"]["check"] == "macro_unread"
    from stock_analyzer.watchlist_advisor import _HOLD_SUPPRESSION_KIND
    assert _HOLD_SUPPRESSION_KIND.get("macro_unread") is None


def test_binding_bear_beats_macro_unread_and_keeps_its_g29_row():
    r = _check(_BEAR, gate_ids={"tone": "G-29"})
    assert [h["check"] for h in r["holds"]] == ["tone", "macro_unread"]
    assert r["binding"]["check"] == "tone" and r["binding"]["gate_id"] == "G-29"
    from stock_analyzer.watchlist_advisor import _HOLD_SUPPRESSION_KIND
    assert _HOLD_SUPPRESSION_KIND["tone"] == "entry_tone"


def test_binding_a_policy_hold_beats_macro_unread():
    news = {"compound": -0.9, "n": 3, "headline": "bad"}
    assert _check(_BULL, news=news)["binding"]["check"] == "news"
    assert _check(_BULL, earnings_days=2)["binding"]["check"] == "earnings"
    r = ee.entry_check(bundle={"stale_as_of": "2026-10-01"}, tone_state=_BULL,
                       sector="Technology", macro_map=None)
    assert r["binding"]["check"] == "stale"


def test_binding_tone_unread_binds_first_and_macro_unread_alone_is_an_abstention():
    r = _check(_DOWN)
    assert [h["check"] for h in r["holds"]] == ["tone_unread", "macro_unread"]
    assert r["binding"]["check"] == "tone_unread"
    assert "macro_unread" in ee._ABSTENTION_CHECKS and "tone_unread" in ee._ABSTENTION_CHECKS
    assert "macro_unknown_sector" not in ee._ABSTENTION_CHECKS


def test_held_kind_label_and_badge_for_macro_unread():
    assert ee.HELD_KIND_LABELS["macro_unread"] == "macro calendar couldn't be read"
    assert ee.held_label({"hold_kind": "macro_unread"}) == "Held: macro calendar couldn't be read"


# ═════════════════════ banner / footer / Analysis ═════════════════════════════
def _held(ticker, *checks):
    return {"ticker": ticker, "action": "ENTRY_HELD",
            "holds": [{"check": c} for c in checks]}


def test_banner_macro_block_lists_only_all_macro_unread_cards_and_honours_exclude():
    recs = [_held("AAA", "macro_unread"), _held("BBB", "macro_unread", "news"),
            _held("CCC", "tone"), _held("DDD", "macro_unread"),
            {"ticker": "EEE", "action": "NEAR_ENTRY"}]
    b = ee.watchlist_banner(_BULL, None, recs, exclude={"ddd"})
    mu = b["macro_unread"]
    assert mu["title"] == "Macro calendar couldn't be read — new entries held"
    assert "1 name would otherwise be Ready: AAA." in mu["body"]
    assert "BBB" not in mu["body"] and "DDD" not in mu["body"] and "CCC" not in mu["body"]
    assert "re-checked every time you open this page" in mu["body"]
    assert b["unread"] is None and b["protect"] is None
    assert b["notes"] == []
    assert "entries are shown, not held" not in json.dumps(b)


def test_banner_macro_block_without_names_has_no_list_and_none_for_a_usable_map():
    b = ee.watchlist_banner(_BULL, None, [])
    assert "would otherwise be Ready" not in b["macro_unread"]["body"]
    assert ee.watchlist_banner(_BULL, {"sectors": set(), "reasons": {}}, [])["macro_unread"] is None
    assert ee.watchlist_banner(_BULL, None, None)["macro_unread"] is not None


def test_banner_both_unread_is_one_list_never_two():
    recs = [_held("AAA", "tone_unread", "macro_unread"), _held("BBB", "tone_unread"),
            _held("CCC", "macro_unread")]
    b = ee.watchlist_banner(_DOWN, None, recs)
    assert "3 names would otherwise be Ready: AAA, BBB, CCC." in b["unread"]["body"]
    assert "would otherwise be Ready" not in b["macro_unread"]["body"]
    assert b["macro_unread"]["title"].startswith("Macro calendar couldn't be read")


def test_banner_bear_day_macro_block_still_present_and_protect_kept():
    b = ee.watchlist_banner(_BEAR, None, [_held("AAA", "tone", "macro_unread")])
    assert b["protect"] is not None and b["macro_unread"] is not None


def test_banner_bodies_have_no_dollar_or_bold_markup():
    b = ee.watchlist_banner(_DOWN, None, [_held("AAA", "tone_unread", "macro_unread")])
    for blk in (b["unread"], b["macro_unread"]):
        assert "$" not in blk["title"] + blk["body"] and "**" not in blk["title"] + blk["body"]


def test_email_footer_unread_macro_line():
    ts = {"tone": "flat", "sp500_pct": -0.24, "available": True,
          "as_of": _dt.datetime(2026, 9, 10, 9, 45)}
    lines = ee.email_footer_lines({"tone_state": ts, "macro_available": False})
    assert lines[1:] == ["Macro calendar couldn't be read — no name is listed as Ready "
                         "to Enter this run."]
    assert len(ee.email_footer_lines({"tone_state": ts, "macro_available": True})) == 1
    assert not any("didn't run" in l for l in lines)


def test_analysis_unread_calendar_offers_no_size_names_it_and_has_no_sizes_shown_note():
    d = ee.analysis_entry_hold(
        basis="equity", label="Buy", held=False,
        entry=ee.entry_check(bundle=_bundle_ok(), tone_state=_BULL, sector="Technology",
                             macro_map=None))
    assert d["offer_size"] is False and d["hold"]["check"] == "macro_unread"
    assert d["reason_if_no_size"] == "the macro-event calendar couldn't be read"
    tiles = {t["label"]: t for t in ee.analysis_gate_tiles(d)}
    assert tiles["Macro"]["state"] is False
    assert tiles["Macro"]["detail"] == "the macro-event calendar couldn't be read"
    banner = ee.analysis_hold_banner(d, tone_state=_BULL)
    assert banner["title"] == ("Not an entry today — held: the macro-event calendar "
                               "couldn't be read")
    assert ee.analysis_shares_label(d) == "— (held)"
    notes = ee.analysis_notes(_BULL, None)
    assert not any("sizes are shown" in n for n in notes)
    assert not any("macro" in n.lower() for n in notes)


# ═════════════════════ Grow Today ═════════════════════════════════════════════
def _gt(s, ctx, *, macro_unread=False, macro=None):
    port_df = make_port_df(s["port_rows"]) if s["port_rows"] else None
    scanner = golden._scanner_df(s["rows"]) if s["rows"] else None

    class _Clk(golden._FixedDT):
        _h, _m = s["clock"]

    with mock.patch.object(daily_briefing, "_dt", _Clk):
        return _grow_today(
            port_df, scanner, s["news"], s["held_data"], TODAY, 100_000.0, ctx,
            act_today=s["act_today"], review_list=s["review_list"],
            composites=s["composites"], earnings_lookup=s["earnings"],
            macro_events=macro, movers=s["movers"], etf_candidates=s["etf"],
            macro_unread=macro_unread)


def _build(s, ctx, macro_events):
    port_df = make_port_df(s["port_rows"])
    scanner = golden._scanner_df(s["rows"]) if s["rows"] else None

    class _Clk(golden._FixedDT):
        _h, _m = s["clock"]

    with mock.patch.object(daily_briefing, "_dt", _Clk):
        return build_daily_briefing(
            port_df=port_df, alert_list=[], risk_recs=[], news_items=s["news"],
            macro_events=macro_events, held_data=s["held_data"], scanner_results=scanner,
            portfolio_value=100_000.0, today=TODAY, market_context=ctx,
            grow_composites=s["composites"], movers=s["movers"],
            etf_candidates=s["etf"])


def test_build_with_macro_events_none_holds_every_list_and_keeps_tone_and_pct():
    g = _build(_scn(), _read(_OPEN_READ), None)["grow_today"]
    assert g["entry_hold"]["check"] == "macro_unread"
    assert g["tone"] == "bull" and g["sp500_pct"] == 0.8      # a real reading, kept
    for k in _PICK_LISTS:
        assert g[k] == [], k
    assert g["message"] is None and g["tone_available"] is True
    assert g["adds_paused"] and g["unevaluated_leads"]           # disclosure only, no size
    assert all("shares" not in a for a in g["adds_paused"])
    assert json.loads(json.dumps(g["entry_hold"])) == g["entry_hold"]


def test_build_control_empty_calendar_is_not_held_and_calls_picks_adds_and_etf():
    g = _build(_scn(), _read(_OPEN_READ), [])["grow_today"]
    assert g["entry_hold"] is None
    assert g["new_picks"] and g["add_positions"]
    assert any(p.get("ticker") == "SPY" for p in g["new_picks"])


def test_ledger_is_empty_for_a_macro_unread_day():
    g = _build(_scn(), _read(_OPEN_READ), None)["grow_today"]
    assert gate_ledger.build_suppression_rows(
        g, rec_date=TODAY, source="app", tone="bull", sp500_pct=0.8) == []


def test_bear_day_with_none_stays_on_the_bear_branch_and_writes_its_g23_row():
    g = _build(_scn(), _read(_OPEN_READ, tone="bear", sp500_pct=-2.0), None)["grow_today"]
    assert g["tone"] == "bear" and g["entry_hold"] is None and g["message"]
    rows = gate_ledger.build_suppression_rows(
        g, rec_date=TODAY, source="app", tone="bear", sp500_pct=-2.0)
    assert [r["gate_id"] for r in rows] == ["G-23"]


def test_tone_unread_beats_macro_unread():
    g = _build(_scn(), _read(_OPEN_READ, tone_available=False), None)["grow_today"]
    assert g["entry_hold"]["check"] == "tone_unread"
    assert g["sp500_pct"] is None


def test_preopen_with_none_is_macro_unread_with_merged_clears():
    g = _build(_scn(), _read(_PREOPEN_READ), None)["grow_today"]
    h = g["entry_hold"]
    assert h["check"] == "macro_unread" and h["session"] == "2026-10-14"
    assert h["clears_when"] == ("when the macro calendar is read again, and after the "
                                "9:30 AM ET open")
    assert g["tone"] == "bull" and g["sp500_pct"] == 0.8
    for k in _PICK_LISTS:
        assert g[k] == [], k


def test_act_today_and_review_read_none_as_no_events_and_are_unchanged():
    s = _scn()
    held = _build(s, _read(_OPEN_READ), None)
    clean = _build(s, _read(_OPEN_READ), [])
    assert held["grow_today"]["entry_hold"]["check"] == "macro_unread"
    assert held["act_today"] == clean["act_today"]
    assert held["review_list"] == clean["review_list"]


def test_a_direct_grow_today_call_keeps_todays_behaviour_for_macro_events_none():
    g = _gt(_scn(), _read(_OPEN_READ), macro_unread=False, macro=None)
    assert g["entry_hold"] is None and g["new_picks"]          # the kwarg is the only switch
    h = _gt(_scn(), _read(_OPEN_READ), macro_unread=True, macro=None)
    assert h["entry_hold"]["check"] == "macro_unread"


def test_grow_today_held_return_has_the_bear_returns_keys_with_macro_unread():
    held = _gt(_scn(), _read(_OPEN_READ), macro_unread=True)
    bear = _gt(_scn(), _read(_OPEN_READ, tone="bear", sp500_pct=-2.0))
    assert set(held) == set(bear)


# ═════════════════════ readers of entry_hold ═══════════════════════════════════
def _macro_grow():
    return _gt(_scn(), _read(_OPEN_READ), macro_unread=True)


def test_held_add_verdict_names_the_macro_reason():
    v = sizing_path.held_add_verdict(
        "ZADDOK", _macro_grow(), brief_date="2026-10-15", today="2026-10-15",
        brief_epoch=1, epoch=1)
    assert v["state"] == "not_called"
    assert "holds new picks and adds" in v["reason"]
    assert "macro-event calendar couldn't be read" in v["reason"]
    assert "up day" not in v["reason"]


def test_hold_reasons_macro_wording_and_the_merged_hold_keeps_the_macro_label():
    assert hold_reasons.adds_paused_label("flat", 2, macro_read.grow_hold()) == \
        "Adds held — macro calendar couldn't be read (2)"
    merged = macro_read.grow_hold(tf.grow_entry_hold(_read(_PREOPEN_READ), date(2026, 10, 15)))
    assert hold_reasons.adds_paused_label("flat", 3, merged) == \
        "Adds held — macro calendar couldn't be read (3)"
    note = hold_reasons.adds_paused_note(["MSFT"], macro_read.grow_hold())
    assert "macro-event calendar couldn't be read" in note and "no size" in note
    assert "Resumes when the macro calendar is read again." in note
    assert "next up day" not in note
    lead = hold_reasons.leads_note("flat", macro_read.grow_hold())
    assert "no verdict" in lead and "flat day" not in lead


def test_tone_unread_label_and_not_today_labels_are_unchanged():
    unread = tf.grow_entry_hold({"tone_available": False}, date(2026, 10, 15))
    assert hold_reasons.adds_paused_label("flat", 2, unread) == \
        "Adds held — market move couldn't be read (2)"


def test_redeploy_hold_passes_a_macro_hold_through_and_the_call_defers():
    hold = macro_read.grow_hold()
    ts = {"tone": "bull", "sp500_pct": 0.8, "available": True, "source": "home",
          "as_of": _et(2026, 11, 25, 10, 0), "why": None, "home_read_at": None}
    assert tf.redeploy_grow_hold(hold, ts, date(2026, 11, 25)) is hold
    posture = ep.new_entry_posture(ts)
    out = ep.redeploy_call({"state": "entry", "ticker": "AAA", "sector": "Tech",
                            "score": 70.0, "rr": 2.5}, posture, grow_hold=hold)
    assert out["entry_called"] is False and out["icon"] == "⏸"
    assert "Grow Today holds new entries: the macro-event calendar couldn't be read" in out["text"]
    assert "They return when the macro calendar is read again." in out["text"]
    assert "$" not in out["text"]


def test_home_grow_hold_returns_a_macro_hold_from_the_synth_cache():
    hold = macro_read.grow_hold()
    syn = {"date": "2026-11-25", "bundle": {"_daily_brief": {"grow_today": {"entry_hold": hold}}}}
    assert ep.home_grow_hold(syn, "2026-11-25") is hold


# ═════════════════════ cron: producers ════════════════════════════════════════
_IDX = [{"short": "S&P 500", "change_pct": 0.8}]


def _morning(macro=None, macro_exc=None, brief=None):
    ctx = {
        "ok": True, "errors": [],
        "port_df": pd.DataFrame({"Ticker": ["AAPL"], "Market Value": [1000.0]}),
        "held_data": {"AAPL": {}}, "fragility": None, "spy_6mo": None,
        "spy_1y": None, "vix": None, "holdings_df": pd.DataFrame({"Ticker": ["AAPL"]}),
    }
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
                           return_value=macro, side_effect=macro_exc))
        if brief is not None:
            st.enter_context(P("stock_analyzer.headless_alert_engine.build_daily_briefing",
                               side_effect=brief))
        return hae.compute_morning_picks(date(2026, 10, 16), scanner_results=scanner)


def test_cron_macro_build_raising_holds_end_to_end_with_none_in_the_context():
    r = _morning(macro_exc=RuntimeError("calendar down"))
    assert r["entry_context"]["macro_events"] is None
    assert r["picks"] == []
    assert r["diag"]["entry_hold"] == "macro_unread" and r["diag"]["bar"] is None
    assert r["grow"]["entry_hold"]["check"] == "macro_unread"
    assert any("macro calendar failed" in e for e in r["errors"])


def test_cron_macro_build_returning_none_or_a_non_list_is_unread_too():
    for bad in (None, {"a": 1}, "x"):
        r = _morning(macro=bad)
        assert r["entry_context"]["macro_events"] is None, bad
        assert r["diag"]["entry_hold"] == "macro_unread", bad


def test_cron_an_empty_calendar_is_a_real_check_and_not_held():
    r = _morning(macro=[])
    assert r["entry_context"]["macro_events"] == []
    # (a tone hold may apply at the wall-clock time of the run; a macro one never does)
    assert r["diag"]["entry_hold"] != "macro_unread"
    assert r["grow"]["entry_hold"] is None or r["grow"]["entry_hold"]["check"] != "macro_unread"


def test_cron_build_failure_path_still_carries_the_calendar_it_read():
    r = _morning(macro=[], brief=RuntimeError("boom"))
    assert r["picks"] == [] and r["entry_context"]["macro_events"] == []
    r2 = _morning(macro_exc=RuntimeError("x"), brief=RuntimeError("boom"))
    assert r2["entry_context"]["macro_events"] is None


# ═════════════════════ cron: the Watchlist lane ═══════════════════════════════
def _wle(entry_context):
    from tests.test_watchlist_entry_cron import _wle as _w
    return _w(entry_context=entry_context)


_FLAT_MC = {"tone": "flat", "sp500_pct": -0.2, "tone_available": True,
            "tone_read_at": _dt.datetime(2026, 9, 10, 9, 45)}


def test_watchlist_none_calendar_holds_every_name_and_reports_it():
    r, save, spy, _g, _l = _wle({"market_context": _FLAT_MC, "macro_events": None})
    assert r["entries"] == []
    card = spy[0][3]
    assert card["action"] == "ENTRY_HELD" and card["hold_kind"] == "macro_unread"
    assert card["suppression_kind"] is None
    save.assert_not_called()                           # a held card is never captured
    assert r["gate_ledger_rows"] == []
    assert r["checks_held"]["macro_unread"] == ["NVDA"]
    assert r["entry_run"]["macro_available"] is False
    lines = ee.email_footer_lines(r["entry_run"])
    assert "Macro calendar couldn't be read — no name is listed as Ready to Enter this run." in lines


def test_watchlist_malformed_events_list_is_held_too():
    r, _s, spy, *_ = _wle({"market_context": _FLAT_MC, "macro_events": [None]})
    assert spy[0][3]["hold_kind"] == "macro_unread"
    assert r["entry_run"]["macro_available"] is False and r["entries"] == []


def test_watchlist_empty_and_real_calendars_are_available():
    r, _s, spy, *_ = _wle({"market_context": _FLAT_MC, "macro_events": []})
    assert r["entry_run"]["macro_available"] is True
    assert spy[0][3]["action"] == "ENTER_NOW"
    assert r["checks_held"]["macro_unread"] == []


def test_watchlist_a_held_in_portfolio_name_is_not_counted_in_checks_held():
    r, *_ = _wle_held({"market_context": _FLAT_MC, "macro_events": None})
    assert r["checks_held"]["macro_unread"] == []


def _wle_held(entry_context):
    from tests.test_watchlist_entry_cron import _wle as _w, SEMIS
    return _w(entry_context=entry_context, held=(SEMIS,))


def test_watchlist_bear_day_with_none_binds_tone_and_keeps_its_g29_row():
    bear = {**_FLAT_MC, "tone": "bear", "sp500_pct": -0.9}
    r, _s, spy, *_ = _wle({"market_context": bear, "macro_events": None})
    assert spy[0][3]["hold_kind"] == "tone" and spy[0][3]["suppression_kind"] == "entry_tone"
    assert r["checks_held"]["macro_unread"] == []     # bound by the policy hold, not counted
    assert r["gate_ledger_rows"]                       # the G-29 row survives


def test_watchlist_checks_held_line_names_macro_unread():
    line = cr._watchlist_checks_held_line(
        {"checks_held": {"fit_unknown": [], "beta_unknown": [], "tone_unread": [],
                         "macro_unread": ["AAA", "BBB"]}})
    assert line == "watchlist: 2 held, a check couldn't run (macro_unread: AAA, BBB)"
    assert cr._watchlist_checks_held_line({"checks_held": {"macro_unread": []}}) is None


# ═════════════════════ cron_runner: texts + lane failures ═════════════════════
@pytest.mark.parametrize("hold, text", [
    (tf.CHECK_UNREAD, "market move couldn't be read — new picks held (G-34)"),
    (tf.CHECK_NOT_TODAY, "the market read isn't today's session — new picks held (G-34)"),
    (macro_read.CHECK_UNREAD, "macro calendar couldn't be read — new picks held (G-34)"),
])
def test_entry_hold_text_covers_every_check_grow_today_can_emit(hold, text):
    assert cr._entry_hold_text(hold) == text


def _scan_with_entry_context(monkeypatch, macro_events, hold="macro_unread"):
    diag = {"tone": "bull", "sp500_pct": 0.8, "bar": None, "entry_hold": hold}
    return _run_scan(monkeypatch, diag,
                     entry_context={"market_context": {}, "macro_events": macro_events})


def test_scan_unread_calendar_logs_names_it_reddens_the_lane_and_sends_a_failure(
        monkeypatch, capsys):
    rc, emails, notified = _scan_with_entry_context(monkeypatch, None)
    out = capsys.readouterr().out
    assert "macro calendar couldn't be read — new picks held (G-34)" in out
    assert "MACRO UNREAD — macro_unread: the macro calendar couldn't be read" in out
    assert rc == 1 and notified and notified[0][0] == "scan"
    assert any(f.startswith("macro_unread:") for f in notified[0][1])
    assert "morning-action" not in emails


def test_scan_clean_calendar_adds_no_macro_failure(monkeypatch, capsys):
    rc, _e, notified = _scan_with_entry_context(monkeypatch, [], hold=None)
    out = capsys.readouterr().out
    assert "MACRO UNREAD" not in out
    assert rc == 0 and notified == []


def _intraday_with_context(monkeypatch, macro_events, hold):
    return _run_intraday(monkeypatch, {"entry_hold": hold},
                         entry_context={"market_context": {}, "macro_events": macro_events})


def test_intraday_unread_calendar_logs_and_reddens_the_lane(monkeypatch, capsys):
    rc, emails, notified = _intraday_with_context(monkeypatch, None, "macro_unread")
    out = capsys.readouterr().out
    assert "intraday: macro calendar couldn't be read — new picks held (G-34)" in out
    assert "MACRO UNREAD —" in out
    assert rc == 1 and emails == {}
    assert notified[0][0] == "intraday"
    assert any(f.startswith("macro_unread:") for f in notified[0][1])


def test_intraday_clean_calendar_no_macro_failure(monkeypatch, capsys):
    rc, _e, notified = _intraday_with_context(monkeypatch, [], None)
    assert "MACRO UNREAD" not in capsys.readouterr().out
    assert rc == 0 and notified == []


# ═════════════════════ FRED guard (owner Q3) ══════════════════════════════════
def test_a_fred_failure_keeps_the_static_rows_and_does_not_raise():
    from stock_analyzer import macro_calendar as mc
    today = date(2026, 11, 20)
    with mock.patch.object(mc, "_fetch_fred", side_effect=ValueError("odd payload")), \
         mock.patch("stock_analyzer.api_health.record") as rec:
        rows = mc.build_macro_calendar(pd.DataFrame(), fred_key="k", today=today)
    assert rows and all(r["released"] is False for r in rows)
    assert all(r["source"] == "static" for r in rows)
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
    assert rec.call_args[0][:2] == ("fred", "parse")


def test_a_fred_failure_is_a_read_calendar_not_a_hold():
    from stock_analyzer import macro_calendar as mc
    with mock.patch.object(mc, "_fetch_fred", side_effect=RuntimeError("x")):
        rows = mc.build_macro_calendar(pd.DataFrame(), fred_key=None, today=date(2026, 11, 20))
    assert macro_read.events_unread(rows) is False


# ═════════════════════ AST pins (both ends) ═══════════════════════════════════
def _fn(tree, name):
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _has_or_around_call(tree, callee):
    for n in ast.walk(tree):
        if isinstance(n, ast.BoolOp) and isinstance(n.op, ast.Or):
            for sub in ast.walk(n):
                if (isinstance(sub, ast.Call)
                        and getattr(sub.func, "id", getattr(sub.func, "attr", None)) == callee):
                    return True
    return False


def test_pin_no_or_fallback_around_build_macro_calendar_in_producers():
    for path in ("stock_analyzer/headless_alert_engine.py", "app.py"):
        tree = parse_file_cached(ROOT / path)
        assert not _has_or_around_call(tree, "build_macro_calendar"), path


def test_pin_one_name_feeds_the_brief_and_the_entry_context():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "headless_alert_engine.py")
    fn = _fn(tree, "compute_morning_picks")
    brief_calls = [n for n in ast.walk(fn)
                   if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "build_daily_briefing"]
    assert brief_calls
    kw = {k.arg: k.value for k in brief_calls[0].keywords}
    assert isinstance(kw["macro_events"], ast.Name)
    name = kw["macro_events"].id
    ctx_dicts = [d for d in ast.walk(fn) if isinstance(d, ast.Dict)
                 and any(isinstance(k, ast.Constant) and k.value == "macro_events" for k in d.keys)]
    assert len(ctx_dicts) >= 2
    for d in ctx_dicts:
        i = [k.value if isinstance(k, ast.Constant) else None for k in d.keys].index("macro_events")
        assert isinstance(d.values[i], ast.Name) and d.values[i].id == name
    assigns = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    assert any(isinstance(a.value, ast.Constant) and a.value.value is None for a in assigns)
    assert any("events_or_none" in ast.dump(a.value) for a in assigns)


def test_pin_grow_today_has_one_production_caller_passing_events_unread():
    calls = []
    for path in ("stock_analyzer", "cron_runner.py", "app.py"):
        p = ROOT / path
        files = sorted(p.rglob("*.py")) if p.is_dir() else [p]
        for f in files:
            tree = parse_file_cached(f)
            calls += [(f.name, n) for n in ast.walk(tree)
                      if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_grow_today"]
    assert [c[0] for c in calls] == ["daily_briefing.py"]
    node = calls[0][1]
    kw = {k.arg: k.value for k in node.keywords}
    assert "macro_unread" in kw and "macro_events" in kw
    seg = ast.dump(kw["macro_unread"])
    assert "events_unread" in seg
    assert isinstance(kw["macro_events"], ast.Name)
    assert kw["macro_events"].id in seg


def test_pin_grow_today_macro_hold_sits_after_bear_and_before_not_today_and_picks():
    src = (ROOT / "stock_analyzer" / "daily_briefing.py").read_text(encoding="utf-8")
    i_unread = src.index("tone_freshness.CHECK_UNREAD:\n        return _entry_hold_return()")
    i_bear = src.index('    if tone == "bear":\n        # macro_coverage_expired')
    i_macro = src.index("    if macro_unread:\n        _entry_hold = macro_read.grow_hold(")
    i_nt = src.index("    if _entry_hold is not None:\n        return _entry_hold_return()\n\n    # Score")
    i_picks = src.index("new_picks: list[dict] = []")
    assert i_unread < i_bear < i_macro < i_nt < i_picks


def test_pin_home_stores_only_a_list_under_the_day_key():
    fn_src = _APP_SRC
    i = fn_src.index('_macro_events = st.session_state.get(_mc_day_key)')
    seg = fn_src[i:i + 1500]
    assert "if _macro_events is None:" in seg
    assert "macro_read.events_or_none(build_macro_calendar(" in seg
    assert "except Exception:\n                _macro_events = None" in seg
    assert ("if _macro_events is not None:\n"
            "                st.session_state[_mc_day_key] = _macro_events") in seg
    # the only assignment into the day key is the guarded one
    stores = [ln for ln in _APP_SRC.splitlines() if "st.session_state[_mc_day_key] =" in ln]
    assert len(stores) == 1


def test_pin_home_readers_are_none_safe():
    assert ("_cc_catalysts = ([] if _macro_events is None else [") in _APP_SRC
    assert ('_pm["events"] = [] if _macro_events is None else [') in _APP_SRC
    assert "for e in (_macro_events or [])" in _APP_SRC          # the source label


def test_pin_abstention_label_and_cron_kinds_carry_macro_unread():
    assert "macro_unread" in ee._ABSTENTION_CHECKS
    assert "macro_unread" in ee.HELD_KIND_LABELS
    tree = parse_file_cached(ROOT / "cron_runner.py")
    fn = _fn(tree, "_watchlist_checks_held_line")
    src = source_segment((ROOT / "cron_runner.py").read_text(encoding="utf-8"), fn)
    assert "macro_read.CHECK_UNREAD" in src
    assert macro_read.CHECK_UNREAD == "macro_unread"
    hsrc = (ROOT / "stock_analyzer" / "headless_alert_engine.py").read_text(encoding="utf-8")
    assert '"macro_unread": []' in hsrc and '_checks_held["macro_unread"]' in hsrc
    assert "macro_read.map_unread(_entry_run_ctx.get(\"macro_map\"))" in hsrc


def test_pin_the_app_watchlist_renders_the_macro_banner_escaped():
    i = _APP_SRC.index('if _wl_banner["macro_unread"] is not None:')
    seg = _APP_SRC[i:i + 1200]
    assert "_safe_html(_wl_mu['title'])" in seg and "_safe_html(_wl_mu['body'])" in seg
    assert "unsafe_allow_html=True" in seg


def test_pin_grow_today_render_shows_the_macro_retry_caption_after_the_banner():
    fn = _fn(parse_file_cached(ROOT / "app.py"), "_render_grow_today")
    src = source_segment(_APP_SRC, fn)
    i_hold = src.index('grow.get("entry_hold")')
    i_bear = src.index("if bear_msg:")
    block = src[i_hold:i_bear]
    assert "macro_read.retry_caption(_entry_hold_ui)" in block
    assert block.index("st.info(") < block.index("macro_read.retry_caption(")
    assert block.index("macro_read.retry_caption(") < block.index("_render_grow_pacing_disclosures")


def test_pin_synth_schema_is_bumped_for_c4m():
    i = _APP_SRC.index("_SYNTH_SCHEMA_VER = ")
    line = _APP_SRC[i:_APP_SRC.index("\n", i)]
    n = int(line.split("=")[1].split("#")[0])
    assert n >= 38
    assert "macro_unread" in line


def test_pin_macro_read_is_a_gate_file():
    import sys
    sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
    import pre_tool_checks as p
    assert "stock_analyzer/macro_read.py" in p._GATE_FILES
