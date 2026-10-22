"""Chunk 4 of docs/plans/shared-entry-check.md: the pure helpers behind the
Watchlist page wiring (entry_eligibility's watchlist_* / held / journal
helpers, budget_view.watchlist_asks), the chunk-3 review follow-ups, and
structural (AST / text) pins for the app.py wiring no test imports.

app.py is render-only wiring; every decision tested here lives in
stock_analyzer/.  Every "excluded / withheld" test has a firing control.
"""
import ast
import datetime
import pathlib

import pandas as pd
import pytest

from stock_analyzer import budget_view as bv
from stock_analyzer import coord_freshness
from stock_analyzer import entry_eligibility as ee
from stock_analyzer import exit_advisor, watchlist_advisor
from stock_analyzer.constants import MARKET_TONE_BEAR_PCT
from stock_analyzer.watchlist_advisor import build_watchlist_recommendation

pytestmark = pytest.mark.fast

_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = (_ROOT / "app.py").read_text(encoding="utf-8")
TODAY = datetime.date(2026, 10, 16)
NOW = datetime.datetime(2026, 10, 16, 11, 0)

FLAT = {"tone": "flat", "sp500_pct": -0.1, "available": True, "source": "live", "as_of": None}
BEAR = {"tone": "bear", "sp500_pct": -0.8, "available": True, "source": "home",
        "as_of": datetime.datetime(2026, 10, 16, 9, 42)}
UNAVAIL = {"tone": None, "sp500_pct": None, "available": False, "source": None, "as_of": None}
CLEAR_MAP = {"sectors": set(), "reasons": {}}
GOOD_NEWS = {"compound": 0.1, "n": 3, "headline": "fine"}


def _data(**ov):
    d = {
        "total": 70.0, "rec": {"label": "Buy"}, "current_price": 100.0,
        "entry_lo": 95.0, "entry_hi": 100.0, "stop": 90.0,
        "targets": {"base": 130.0}, "earnings": None,
    }
    d.update(ov)
    return d


def _ctx(**ov):
    c = {"tone_state": FLAT, "macro_map": CLEAR_MAP, "news": GOOD_NEWS, "sector": "Technology"}
    c.update(ov)
    return c


def _rec(ticker="XYZ", data=None, ctx="default", **kw):
    ctx = _ctx() if ctx == "default" else ctx
    return build_watchlist_recommendation(ticker, data or _data(), entry_ctx=ctx, **kw)


# ═════════════════════════ chunk-3 review follow-ups ═══════════════════════════
def test_hold_missing_reason_and_clears_when_is_a_visible_held_card(monkeypatch):
    """Item 2: a malformed hold must never drop the name."""
    def bad_check(**kw):
        return {"eligible": False, "holds": [{"check": "tone"}],
                "binding": {"check": "tone"}, "unchecked": []}
    monkeypatch.setattr(ee, "entry_check", bad_check)
    r = _rec()
    assert r["action"] == "ENTRY_HELD" and r["hold_kind"] == "tone"
    assert r["hold_reason"] and r["clears_when"]


def test_check_result_without_holds_key_fails_closed_not_dropped(monkeypatch):
    monkeypatch.setattr(ee, "entry_check", lambda **kw: {"eligible": False})
    r = _rec()
    assert r["action"] == "ENTRY_HELD"


def test_check_result_with_non_dict_hold_fails_closed(monkeypatch):
    monkeypatch.setattr(ee, "entry_check", lambda **kw: {
        "eligible": False, "holds": ["oops"], "binding": "oops", "unchecked": []})
    r = _rec()
    assert (r["action"], r["hold_kind"]) == ("ENTRY_HELD", "check_failed")


def test_eligible_check_with_missing_unchecked_key_stays_enter_now(monkeypatch):
    """Firing control: a well-formed eligible result is NOT turned into a hold."""
    monkeypatch.setattr(ee, "entry_check", lambda **kw: {"eligible": True})
    r = _rec()
    assert r["action"] == "ENTER_NOW" and r["entry_unchecked"] == []


def test_check_macro_falsy_sector_is_an_unchecked_note():
    for sector in (None, ""):
        r = ee.check_macro({"macro_map": CLEAR_MAP, "sector": sector})
        assert r["kind"] == "unchecked" and "sector unknown" in r["why"]
    # firing control: a real sector with a clear map passes silently
    assert ee.check_macro({"macro_map": CLEAR_MAP, "sector": "Technology"}) is None


def test_check_macro_falsy_sector_does_not_hide_an_all_sectors_block():
    mm = {"sectors": {"__ALL__"}, "reasons": {"__ALL__": "FOMC"}}
    r = ee.check_macro({"macro_map": mm, "sector": None})
    assert r["kind"] == "hold" and "FOMC" in r["reason"]


@pytest.mark.parametrize("bm", [{"sectors": set(), "reasons": None}, {"sectors": set()}])
def test_check_macro_empty_sets_are_a_pass_not_a_crash(bm):
    assert ee.check_macro({"macro_map": bm, "sector": "Technology"}) is None


@pytest.mark.parametrize("bm", [{}, {"reasons": {}}, {"sectors": None, "reasons": None}, "oops", []])
def test_check_macro_malformed_map_is_unchecked_not_a_silent_pass(bm):
    r = ee.check_macro({"macro_map": bm, "sector": "Technology"})
    assert r["kind"] == "unchecked" and r["check"] == "macro"


def test_check_macro_set_membership_decides_not_the_reason_string():
    mm = {"sectors": {"Technology"}, "reasons": {}}
    r = ee.check_macro({"macro_map": mm, "sector": "Technology"})
    assert r["kind"] == "hold" and "imminent" in r["reason"]


def test_both_card_kinds_use_the_one_entry_unchecked_key():
    held = _rec(ctx=_ctx(tone_state=BEAR, news=None))
    ready = _rec(ctx=_ctx(news=None))
    assert held["action"] == "ENTRY_HELD" and ready["action"] == "ENTER_NOW"
    assert [u["check"] for u in held["entry_unchecked"]] == ["news"]
    assert [u["check"] for u in ready["entry_unchecked"]] == ["news"]
    assert "unchecked" not in held and "unchecked" not in ready


def test_held_card_carries_soft_portfolio_caution():
    pctx = {"sector_weight_pct": 27.0, "sector_of_ticker": "Technology"}
    legacy = build_watchlist_recommendation("XYZ", _data(), portfolio_ctx=pctx)
    assert legacy["action"] == "ENTER_NOW" and legacy["portfolio_caution"]   # firing control
    held = _rec(ctx=_ctx(tone_state=BEAR), portfolio_ctx=pctx)
    assert held["action"] == "ENTRY_HELD"
    assert held["portfolio_caution"] == legacy["portfolio_caution"]


def test_held_card_carries_deterioration_warning(monkeypatch):
    monkeypatch.setattr(exit_advisor, "candidate_deterioration_caption",
                        lambda *a, **k: "WARN-TEXT")
    held = _rec(ctx=_ctx(tone_state=BEAR))
    ready = _rec()
    assert held["deterioration_warning"] == "WARN-TEXT"
    assert ready["deterioration_warning"] == "WARN-TEXT"


# ═════════════════════════ per-run / per-ticker context ════════════════════════
def test_tone_source_order_home_today_then_live_then_unavailable():
    home = {"tone": "bear", "sp500_pct": -0.9, "tone_available": True}
    st = ee.resolve_tone_state(home, True, None, -0.1, True)
    assert (st["source"], st["tone"]) == ("home", "bear")
    # Home bundle NOT from today -> the live read, even though Home says bear
    st = ee.resolve_tone_state(home, False, None, -0.1, True)
    assert (st["source"], st["tone"]) == ("live", "flat")
    # neither -> unavailable
    st = ee.resolve_tone_state(home, False, None, None, False)
    assert st["available"] is False and st["tone"] is None
    # Home's own flat fallback (tone_available False) is never trusted
    st = ee.resolve_tone_state({"tone": "flat", "tone_available": False}, True, None, None, False)
    assert st["available"] is False


def test_macro_events_source_order():
    ev = [{"event": "CPI"}]
    assert ee.pick_macro_events(ev, True, None) is ev
    assert ee.pick_macro_events(ev, False, [{"event": "live"}]) == [{"event": "live"}]
    assert ee.pick_macro_events(None, True, [{"event": "live"}]) == [{"event": "live"}]
    assert ee.pick_macro_events([], True, [{"event": "live"}]) == []     # checked, none
    assert ee.pick_macro_events(None, False, None) is None


def test_macro_none_becomes_an_unchecked_note_not_a_pass():
    ctx = ee.watchlist_run_ctx(FLAT, None, TODAY, NOW)
    assert ctx["macro_map"] is None
    tctx = ee.watchlist_ticker_ctx(ctx, sector="Technology", classified=True, news_items=None)
    r = build_watchlist_recommendation("XYZ", _data(), entry_ctx=tctx)
    assert r["action"] == "ENTER_NOW"
    assert {u["check"] for u in r["entry_unchecked"]} == {"macro", "news"}
    # firing control: [] is "checked, none" -> no macro note
    ctx = ee.watchlist_run_ctx(FLAT, [], TODAY, NOW)
    assert ctx["macro_map"] == {"sectors": set(), "reasons": {}}


def test_unclassified_sector_is_passed_as_none_and_noted():
    ctx = ee.watchlist_run_ctx(FLAT, [], TODAY, NOW)
    t = ee.watchlist_ticker_ctx(ctx, sector="Technology", classified=False, news_items=[])
    assert t["sector"] is None
    r = build_watchlist_recommendation("XYZ", _data(), entry_ctx=t)
    assert any(u["check"] == "macro" and "sector unknown" in u["why"]
               for u in r["entry_unchecked"])
    t = ee.watchlist_ticker_ctx(ctx, sector="Technology", classified=True, news_items=[])
    assert t["sector"] == "Technology"


def test_macro_event_holds_a_classified_sector_through_the_whole_chain():
    ev = [{"event": "CPI", "date": "2026-10-19", "impact": "HIGH", "category": "Inflation"}]
    ctx = ee.watchlist_run_ctx(FLAT, ev, TODAY, NOW)
    from stock_analyzer.macro_calendar import affected_sectors
    sector = next(iter(s for s in affected_sectors("Inflation") if s != "__ALL__"), None)
    if sector is None:   # category hits __ALL__
        sector = "Technology"
    t = ee.watchlist_ticker_ctx(ctx, sector=sector, classified=True, news_items=[])
    r = build_watchlist_recommendation("XYZ", _data(), entry_ctx=t)
    assert (r["action"], r["hold_kind"]) == ("ENTRY_HELD", "macro")


def test_news_ctx_from_items_uses_mean_and_names_the_worst_headline():
    items = [{"title": "a", "compound": -0.6}, {"title": "b", "compound": 0.2}]
    n = ee.news_ctx_from_items(items)
    assert n["n"] == 2 and n["headline"] == "a"
    assert n["compound"] == pytest.approx(-0.2)
    assert ee.news_ctx_from_items([]) is None
    assert ee.news_ctx_from_items(None) is None


def test_negative_news_from_items_holds_the_card():
    t = ee.watchlist_ticker_ctx(
        ee.watchlist_run_ctx(FLAT, [], TODAY, NOW), sector="Technology", classified=True,
        news_items=[{"title": "bad", "compound": -0.5}, {"title": "worse", "compound": -0.6}])
    r = build_watchlist_recommendation("XYZ", _data(), entry_ctx=t)
    assert (r["action"], r["hold_kind"]) == ("ENTRY_HELD", "news")


# ═════════════════════════ banner / labels / caches ════════════════════════════
def test_protect_banner_on_bear_names_threshold_pct_names_and_source():
    held = _rec("BKNG", ctx=_ctx(tone_state=BEAR))
    other = _rec("JPM", ctx=_ctx(tone_state=BEAR, macro_map={"sectors": {"Technology"}, "reasons": {}}))
    b = ee.watchlist_banner(BEAR, CLEAR_MAP, [held, other, _rec("OK", ctx=_ctx())],
                            home_state=ee.HOME_TODAY_OK)
    p = b["protect"]
    assert f"{MARKET_TONE_BEAR_PCT:g}%" in p["title"] and "-0.8%" in p["title"]
    # BKNG is held ONLY by the tone check; JPM is also macro-held, so it would not
    # be Ready even on a calm day and is not listed as "otherwise Ready".
    assert "BKNG" in p["body"] and "JPM" not in p["body"] and "1 name " in p["body"]
    assert p["source"] == "Market read from Home's Brief, 9:42 AM ET."
    assert b["notes"] == []


def test_no_protect_banner_on_flat_or_bull_firing_control_on_bear():
    assert ee.watchlist_banner(FLAT, CLEAR_MAP, [])["protect"] is None
    assert ee.watchlist_banner({**FLAT, "tone": "bull"}, CLEAR_MAP, [])["protect"] is None
    assert ee.watchlist_banner(BEAR, CLEAR_MAP, [])["protect"] is not None


def test_banner_says_when_the_tone_or_macro_check_could_not_run():
    b = ee.watchlist_banner(UNAVAIL, None, [])
    assert b["protect"] is None
    assert any("bear-day check couldn't run" in n for n in b["notes"])
    assert any("macro-event check couldn't run" in n for n in b["notes"])
    assert ee.watchlist_banner(FLAT, CLEAR_MAP, [])["notes"] == []


def test_live_tone_source_line_and_unavailable_none():
    assert "live" in ee.tone_source_line(FLAT)
    assert ee.tone_source_line(UNAVAIL) is None


def test_held_label_wording():
    assert ee.held_label({"hold_kind": "tone"}) == "Deferred — market risk-off"
    assert ee.held_label({"hold_kind": "macro"}) == "Held: macro event"
    assert ee.held_label({"hold_kind": "news"}) == "Held: negative news"
    assert ee.held_label({}) == "Held today"


def test_held_cache_none_on_failure_and_never_empty_dict_for_it():
    assert ee.watchlist_held_cache(None, "2026-10-16") is None
    c = ee.watchlist_held_cache([], "2026-10-16")
    assert c == {"day": "2026-10-16", "held": {}}      # checked, nothing held
    held = _rec("BKNG", ctx=_ctx(tone_state=BEAR))
    c = ee.watchlist_held_cache([held, _rec("OK")], "2026-10-16")
    assert set(c["held"]) == {"BKNG"} and "risk-off" in c["held"]["BKNG"]


def test_journal_note_cases():
    cache = {"day": "2026-10-16", "held": {"BKNG": "market risk-off (S&P -0.8%)"}}
    note = ee.journal_hold_note("bkng", cache, "2026-10-16")
    assert note.startswith("Note: BKNG is held today — market risk-off")
    assert ee.journal_hold_note("AAPL", cache, "2026-10-16") is None     # not held
    assert ee.journal_hold_note("BKNG", None, "2026-10-16") is None      # cache unreadable
    assert ee.journal_hold_note("BKNG", cache, "2026-10-17") is None     # yesterday's
    assert ee.journal_hold_note("BKNG", {"day": "2026-10-16", "held": None}, "2026-10-16") is None


# ═════════════════════════ budget: asks + the emailed allocation ═══════════════
def _sizer(px, stop):
    return {"shares": 10, "total_cost": px * 10}


def test_watchlist_asks_exclude_entry_held_and_every_non_enter_now():
    ready = _rec("RDY")
    held = _rec("HLD", ctx=_ctx(tone_state=BEAR))
    near = build_watchlist_recommendation("NER", _data(current_price=104.0))
    wait = build_watchlist_recommendation("WAT", _data(current_price=130.0))
    assert (ready["action"], held["action"], near["action"], wait["action"]) == (
        "ENTER_NOW", "ENTRY_HELD", "NEAR_ENTRY", "WAIT_ENTRY")
    asks = bv.watchlist_asks([ready, held, near, wait], set(), set(), 14000.0, _sizer)
    assert [a["ticker"] for a in asks] == ["RDY"]             # firing control: ENTER_NOW asks
    assert asks[0]["ps"] == {"shares": 10, "total_cost": 1000.0}
    assert asks[0]["composite"] == ready["score"]


def test_watchlist_asks_skip_held_and_sold_today_and_handle_sizer_failures():
    a, b, c = _rec("AAA"), _rec("BBB"), _rec("CCC")

    def boom(px, stop):
        raise RuntimeError("x")
    asks = bv.watchlist_asks([a, b, c], {"AAA"}, {"BBB"}, 14000.0, boom)
    assert [x["ticker"] for x in asks] == ["CCC"] and asks[0]["ps"] is None
    # no portfolio value -> ask kept, no size (unchanged from the inline logic)
    asks = bv.watchlist_asks([a], set(), set(), 0.0, _sizer)
    assert asks[0]["ps"] is None


_NO_TRADES = pd.DataFrame(columns=["traded_at", "action", "ticker", "shares", "price"])
_INPUTS = {"gross_book": 14000.0, "net_capital": 10000.0, "basis_state": "levered",
           "cash_balance": -4000.0, "cash_as_of": "2026-10-16T13:45:00+00:00"}
_PLAN_NOW = datetime.datetime(2026, 10, 16, 15, 0, tzinfo=datetime.timezone.utc)


def _gt_cache():
    brief = {"grow_today": {"tone": "bull", "new_picks": [], "add_positions": [],
                            "overflow_picks": []}}
    return bv.postpass(brief, _INPUTS, _NO_TRADES, {}, TODAY, _PLAN_NOW)["cache"]


def test_emailed_allocation_of_a_held_name_stays_counted_and_gets_no_new_size():
    allocs = {"HLD": {"shares": 6.0, "dollars": 600.0}}
    held = _rec("HLD", ctx=_ctx(tone_state=BEAR))
    ready = _rec("RDY")
    asks = bv.watchlist_asks([held, ready], set(), set(), 14000.0,
                             lambda px, stop: {"shares": 24, "total_cost": 2400.0,
                                               "risk_budget": 100.0, "actual_risk": 96.0,
                                               "risk_per_share": 4.0, "portfolio_pct": 17.1,
                                               "risk_pct_actual": 0.7, "ceiling_pct": 15.0,
                                               "ceiling_capped": False, "uncapped_shares": 24,
                                               "uncapped_pct": 17.1, "capital_capped": False,
                                               "capital_pct": 24.0})
    assert [a["ticker"] for a in asks] == ["RDY"]
    plan = bv.plan_downstream(_INPUTS, _NO_TRADES, allocs, _gt_cache(), TODAY, _PLAN_NOW, asks)
    assert plan["state"] == "ok"
    assert plan["summary"]["used_allocated"] == pytest.approx(600.0)   # still counted
    assert "HLD" not in plan["results"]                                # no new size for it
    # the READY name is sized from what is left after the locked 600
    assert plan["results"]["RDY"]["ps"] is not None
    # the allocation is the caller's object and is not consumed/released
    assert allocs == {"HLD": {"shares": 6.0, "dollars": 600.0}}


def test_emailed_line_text_time_shares_and_reason():
    held = _rec("HLD", ctx=_ctx(tone_state=BEAR))
    allocs = {"HLD": {"shares": 6.0, "dollars": 600.0}}
    times = {"HLD": "2026-10-16T13:45:00+00:00"}      # 09:45 ET in EDT
    line = ee.emailed_then_held_line(held, allocs, times)
    assert line == f"Emailed at 9:45 AM with 6 sh — now deferred: {held['hold_reason']}"
    # no timestamp -> "today", never an invented time
    assert ee.emailed_then_held_line(held, allocs, {}).startswith("Emailed today with 6 sh")
    assert ee.emailed_then_held_line(held, allocs, None).startswith("Emailed today with 6 sh")
    # no shares recorded
    assert ee.emailed_then_held_line(held, {"HLD": {"dollars": 600.0}}, times).startswith(
        "Emailed at 9:45 AM — now deferred")


def test_emailed_line_none_without_allocation_or_not_held_and_never_mutates():
    held = _rec("HLD", ctx=_ctx(tone_state=BEAR))
    ready = _rec("RDY")
    allocs = {"HLD": {"shares": 6.0, "dollars": 600.0}, "RDY": {"shares": 3.0, "dollars": 300.0}}
    snap = {k: dict(v) for k, v in allocs.items()}
    assert ee.emailed_then_held_line(held, {}, {}) is None
    assert ee.emailed_then_held_line(held, None, None) is None
    assert ee.emailed_then_held_line(held, {"OTHER": {"shares": 1}}, {}) is None
    assert ee.emailed_then_held_line(ready, allocs, {}) is None      # ENTER_NOW: no line
    assert ee.emailed_then_held_line(held, allocs, {"HLD": "2026-10-16T13:45:00+00:00"})
    assert allocs == snap                                            # read-only


def test_db_allocation_times_loader_contract(monkeypatch):
    from stock_analyzer import db

    class _Q:
        def __init__(self, rows, exc):
            self.rows, self.exc = rows, exc

        def select(self, *a): return self
        def eq(self, *a): return self
        def order(self, *a, **k): return self
        def limit(self, *a): return self

        def execute(self):
            if self.exc:
                raise self.exc

            class R:
                data = self.rows
            return R()

    class _C:
        def __init__(self, rows=None, exc=None):
            self.rows, self.exc = rows, exc

        def table(self, name):
            assert name == "new_position_budget_allocations"
            return _Q(self.rows, self.exc)

    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _C([{"ticker": "bkng", "created_at": "T"},
                                                    {"ticker": "X", "created_at": None}]))
    assert db.load_budget_allocation_times_or_none("2026-10-15") == {"BKNG": "T"}
    monkeypatch.setattr(db, "_client", lambda: _C([]))
    assert db.load_budget_allocation_times_or_none("2026-10-15") == {}       # checked, none
    monkeypatch.setattr(db, "_client", lambda: _C(exc=RuntimeError("x")))
    assert db.load_budget_allocation_times_or_none("2026-10-15") is None     # failure
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_budget_allocation_times_or_none("2026-10-15") is None


# ═════════════════════════ registry ════════════════════════════════════════════
def test_wl_held_cache_is_registered_decorative_watchlist_produced():
    reg = coord_freshness.PORTFOLIO_DEPENDENT_KEYS["_wl_held_cache"]
    assert reg["tier"] == coord_freshness.TIER_DECORATIVE
    assert reg["producer"] == "watchlist"
    assert "_wl_held_cache" in coord_freshness.keys_for_producer("watchlist")


# ═════════════════════════ app.py structural pins ══════════════════════════════
def _page_segment(start_marker, end_marker):
    i = APP.index(start_marker)
    j = APP.index(end_marker, i)
    return APP[i:j]


WL = _page_segment('elif page == "📋 Watchlist":', "# PAGE — THESIS (Chunk A")


def _calls(src, name):
    tree = ast.parse("if True:\n" + src.split("\n", 1)[1])
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if fname == name:
                out.append(n)
    return out


def test_advisor_call_passes_entry_ctx():
    calls = _calls(WL, "build_watchlist_recommendation")
    assert len(calls) == 1
    assert any(k.arg == "entry_ctx" for k in calls[0].keywords)
    # the ctx is built from the pure helpers, with the CURATED sector
    assert "_entry_elig.watchlist_ticker_ctx(" in WL
    assert 'sector=_wl_ctx["sector"]' in WL and 'classified=_wl_ctx["classified"]' in WL


def test_budget_asks_come_from_the_pure_helper_not_an_inline_loop():
    assert "_budget_view.watchlist_asks(" in WL
    assert '_bw["action"] != "ENTER_NOW"' not in WL


def test_sizing_is_skipped_for_entry_held_in_both_places():
    body = WL[WL.index("for _wr in _wl_view:"):]
    first_sizing = body.index("_wl_ps = position_sizing(")
    # S-D C4: the guard is sizing_path.may_show_size, which excludes ENTRY_HELD.
    from stock_analyzer.sizing_path import may_show_size
    assert may_show_size("watchlist", "ENTRY_HELD") is False
    guard = body.rindex("_wl_may_size and", 0, first_sizing)
    assert first_sizing - guard < 300          # the guard is the condition of THAT call
    assert '_wl_may_size = _sizing_path.may_show_size("watchlist", _action)' in body[:guard]
    assert 'elif _price and _action != "ENTRY_HELD":' in body   # no "can't size" caption either
    # and no trade button for it: the log-buy is keyed on ENTER_NOW only
    qa = body[body.index("_qa_col1:"):body.index("_qa_col2:")]
    assert 'if _action == "ENTER_NOW":' in qa and "ENTRY_HELD" not in qa


def test_held_and_sold_today_overrides_cover_entry_held():
    assert 'if _action in ("ENTER_NOW", "ENTRY_HELD") and _already_held:' in WL
    assert 'if _action in ("ENTER_NOW", "ENTRY_HELD") and _ticker in _wl_sold_today:' in WL


def test_actionable_view_and_kpi_include_entry_held_under_a_held_header():
    assert '("ENTER_NOW", "ENTRY_HELD", "NEAR_ENTRY")' in WL
    assert '"⏸ Held today"' in WL and "**⏸ Held today**" in WL
    assert '"ENTRY_HELD":        "⏸"' in WL
    assert WL.index("**⏸ Held today**") > WL.index("for _wr in _wl_view:")


def test_wl_held_cache_published_none_first_then_helper_never_boolop():
    assigns = []
    tree = ast.parse("if True:\n" + WL.split("\n", 1)[1])
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if (isinstance(t, ast.Subscript)
                        and isinstance(t.slice, ast.Constant)
                        and t.slice.value == "_wl_held_cache"):
                    assigns.append(n.value)
    assert len(assigns) == 2
    assert isinstance(assigns[0], ast.Constant) and assigns[0].value is None
    assert isinstance(assigns[1], ast.Call)
    assert not any(isinstance(a, ast.BoolOp) for a in assigns)
    first_stop = next(WL.index(ln) for ln in WL.splitlines() if ln.strip() == "st.stop()")
    assert WL.index('st.session_state["_wl_held_cache"] = None') < first_stop
    assert '_stamp_coord(["_wl_held_cache"])' in WL


def test_protect_banner_and_unavailable_notes_are_rendered_from_the_pure_banner():
    assert "_entry_elig.watchlist_banner(" in WL
    assert 'for _wl_note in _wl_banner["notes"]:' in WL
    # interpolated hold text goes through _safe_html (S&P, $)
    assert "_safe_html(_wl_pm['title'])" in WL and "_safe_html(_h['reason'])" in WL
    assert "_safe_html(_wr['clears_when'])" in WL


def test_held_card_text_never_uses_markdown_bold_inside_raw_html():
    seg = WL[WL.index("# ENTRY_HELD (shared new-entry check): why it is held"):]
    seg = seg[:seg.index("# An eligible ENTER_NOW")]
    assert "**" not in seg


def test_tone_context_reads_home_bundle_only_when_dated_today():
    assert '_get_or_offline(_wl_home_syn, "date") == _today_et().isoformat()' in WL
    assert "_entry_elig.resolve_tone_state(" in WL and "pick_macro_events(" in WL


def test_trade_journal_confirm_notes_a_held_name_after_the_budget_line():
    seg = _page_segment("_pending_buy = st.session_state.get(\"_tj_pending_buy\")",
                        "_pb_c1, _pb_c2 = st.columns")
    assert seg.index('_pb_prev["text"]') < seg.index("_entry_elig.journal_hold_note(")
    assert 'st.session_state.get("_wl_held_cache")' in seg
    # awareness only: the note sits before, and never touches, the confirm button
    assert "disabled" not in seg[seg.index("journal_hold_note"):]


def test_home_brief_crash_keeps_an_already_read_market_context():
    i = APP.index("_daily_brief    = None\n                # Keep the market context")
    seg = APP[i:i + 900]
    # the flat fallback is reachable only under an `if not isinstance(...)` guard
    assert "if not isinstance(_market_context, dict):" in seg
    first_assign = seg.index("_market_context = {")
    assert seg.index("if not isinstance(_market_context, dict):") < first_assign


# ═════════════════════ review round 1: banner truthfulness + follow-ups ═════════
HOME_BEAR_CTX = {"tone": "bear", "sp500_pct": -0.9, "tone_available": True,
                 "tone_read_at": datetime.datetime(2026, 10, 16, 9, 42)}
LIVE_BEAR = {"tone": "bear", "sp500_pct": -0.8, "available": True, "source": "live", "as_of": None}


def test_home_tone_state_three_paths():
    assert ee.home_tone_state(HOME_BEAR_CTX, True) == ee.HOME_TODAY_OK
    assert ee.home_tone_state({**HOME_BEAR_CTX, "tone_available": False}, True) == ee.HOME_TODAY_TONE_FAILED
    assert ee.home_tone_state(None, True) == ee.HOME_TODAY_TONE_FAILED
    assert ee.home_tone_state(HOME_BEAR_CTX, False) == ee.HOME_NOT_TODAY


def test_banner_path_source_home_says_matching_and_the_read_time():
    st_ = ee.resolve_tone_state(HOME_BEAR_CTX, True, HOME_BEAR_CTX["tone_read_at"], -0.1, True)
    assert st_["source"] == "home"
    p = ee.watchlist_banner(st_, CLEAR_MAP, [], home_state=ee.HOME_TODAY_OK)["protect"]
    assert "matching 🏠 Home" in p["body"]
    assert p["source"] == "Market read from Home's Brief, 9:42 AM ET."


def test_banner_path_home_has_no_time_when_the_context_carries_none():
    ctx = {k: v for k, v in HOME_BEAR_CTX.items() if k != "tone_read_at"}
    st_ = ee.resolve_tone_state(ctx, True, None, None, False)
    p = ee.watchlist_banner(st_, CLEAR_MAP, [], home_state=ee.HOME_TODAY_OK)["protect"]
    assert p["source"] == "Market read from Home's Brief."


def test_banner_path_live_while_home_tone_failed_never_says_matching():
    p = ee.watchlist_banner(LIVE_BEAR, CLEAR_MAP, [],
                            home_state=ee.HOME_TODAY_TONE_FAILED)["protect"]
    assert "matching" not in (p["title"] + p["body"] + p["source"])
    assert "couldn't read the market this run" in p["source"]
    assert "treating today as flat" in p["source"] and "were not deferred" in p["source"]
    assert p["body"].startswith("New entries deferred.")


def test_banner_path_live_while_home_not_built_today_never_says_matching():
    p = ee.watchlist_banner(LIVE_BEAR, CLEAR_MAP, [],
                            home_state=ee.HOME_NOT_TODAY)["protect"]
    assert "matching" not in (p["title"] + p["body"] + p["source"])
    assert p["source"] == "Market read live (🏠 Home's Brief isn't built today)."


def test_banner_path_live_with_no_home_state_is_plain_live():
    p = ee.watchlist_banner(LIVE_BEAR, CLEAR_MAP, [])["protect"]
    assert "matching" not in (p["title"] + p["body"]) and p["source"] == "Market read live."


def test_banner_path_unavailable_has_no_protect_banner_and_says_so():
    b = ee.watchlist_banner(UNAVAIL, CLEAR_MAP, [], home_state=ee.HOME_TODAY_TONE_FAILED)
    assert b["protect"] is None
    assert any("bear-day check couldn't run" in n for n in b["notes"])


def test_whole_chain_home_failed_then_live_bear_banner_is_true():
    ctx = {"tone": "flat", "sp500_pct": 0.0, "tone_available": False}
    hs = ee.home_tone_state(ctx, True)
    st_ = ee.resolve_tone_state(ctx, True, None, -0.8, True)
    assert st_["source"] == "live" and st_["tone"] == "bear"
    p = ee.watchlist_banner(st_, CLEAR_MAP, [], home_state=hs)["protect"]
    assert "matching" not in (p["body"] + p["source"]) and "treating today as flat" in p["source"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "x", None])
def test_non_finite_live_change_reads_as_unavailable(bad):
    r = ee.resolve_tone_state(None, False, None, bad, True)
    assert r["available"] is False and r["tone"] is None
    # firing control: a finite value is classified
    assert ee.resolve_tone_state(None, False, None, -0.8, True)["tone"] == "bear"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None])
def test_non_finite_home_change_falls_through_to_live(bad):
    ctx = {**HOME_BEAR_CTX, "sp500_pct": bad}
    r = ee.resolve_tone_state(ctx, True, None, -0.1, True)
    assert r["source"] == "live" and r["tone"] == "flat"
    r = ee.resolve_tone_state(ctx, True, None, None, False)
    assert r["available"] is False


def test_banner_count_is_tone_only_names_and_skips_overridden_cards():
    tone_only = _rec("AAA", ctx=_ctx(tone_state=BEAR))
    tone_only_held = _rec("BBB", ctx=_ctx(tone_state=BEAR))
    tone_only_sold = _rec("CCC", ctx=_ctx(tone_state=BEAR))
    tone_and_news = _rec("DDD", ctx=_ctx(tone_state=BEAR,
                                         news={"compound": -0.9, "n": 2, "headline": "x"}))
    recs = [tone_only, tone_only_held, tone_only_sold, tone_and_news]
    p = ee.watchlist_banner(BEAR, CLEAR_MAP, recs, home_state=ee.HOME_TODAY_OK,
                            exclude={"BBB", "ccc"})["protect"]
    assert "1 name " in p["body"] and "AAA" in p["body"]
    for t in ("BBB", "CCC", "DDD"):
        assert t not in p["body"]
    # firing control: with no exclusions BBB and CCC are counted
    p = ee.watchlist_banner(BEAR, CLEAR_MAP, recs, home_state=ee.HOME_TODAY_OK)["protect"]
    assert "3 names" in p["body"] and "DDD" not in p["body"]


def test_held_footer_budget_claim_only_when_the_allocation_record_was_read():
    assert "Not counted against today's new-position budget" in ee.held_budget_footer(None, {})
    assert "Not counted" not in ee.held_budget_footer(None, None)     # read failed: say nothing
    assert "Not counted" not in ee.held_budget_footer("Emailed today — now deferred: x", {"A": {}})
    assert ee.held_budget_footer(None, None) == "No size and no trade button while held."


def test_held_card_deterioration_caption_is_not_worded_as_enter_now(monkeypatch):
    flag = {"tier": "warn", "dd_from_peak_pct": 12.0, "trend_ma": 50}
    monkeypatch.setattr(exit_advisor, "candidate_deterioration_flag", lambda *a, **k: flag)
    held = _rec(ctx=_ctx(tone_state=BEAR))
    ready = _rec()
    assert held["action"] == "ENTRY_HELD" and ready["action"] == "ENTER_NOW"
    assert "rates it ENTER NOW" in (ready["deterioration_warning"] or "")   # firing control
    assert held["deterioration_warning"]
    assert "ENTER NOW" not in held["deterioration_warning"]
    assert "ready on its own numbers" in held["deterioration_warning"]


_CTX_SEG = WL[WL.index("# ── Shared new-entry check: per-run context"):WL.index("# ── Build recommendations")]


def test_app_tone_and_macro_are_read_in_separate_try_blocks():
    tone_at = _CTX_SEG.index("_entry_elig.resolve_tone_state(")
    macro_at = _CTX_SEG.index("_wl_macro_events_final = None")
    assert "    except Exception:" in _CTX_SEG[tone_at:macro_at]
    assert _CTX_SEG.count("    try:\n") >= 3
    # tone survives a macro failure
    assert '_wl_run_ctx = {"tone_state": _wl_tone_state, "macro_map": None}' in _CTX_SEG


def test_app_stamps_the_tone_read_time_on_the_market_context_and_uses_it():
    assert '"tone_read_at":    _now_et(),' in APP
    assert '_get_or_offline(_wl_home_mc, "tone_read_at")' in _CTX_SEG
    assert "_brief_built_at" not in _CTX_SEG


def test_app_banner_gets_home_state_exclusions_and_footer_helper():
    assert "home_state=_wl_home_state, exclude=_wl_held | _wl_sold_today" in WL
    assert "_entry_elig.held_budget_footer(_wl_em_line, _wl_alloc_map)" in WL
    assert "Not counted against today's new-position budget." not in WL


def test_app_closes_the_held_section_and_words_overrides_by_action():
    assert "_wl_held_hdr_closed = True" in WL and "**Other watchlist names**" in WL
    assert WL.index("_wl_held_hdr_closed = True") < WL.index("**⏸ Held today**")
    assert "ready on its own numbers but is held today" in WL
