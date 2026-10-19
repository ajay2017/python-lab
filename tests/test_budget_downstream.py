"""Chunk 5 of docs/plans/daily-new-position-budget.md -- Watchlist (rank 3),
Analysis (sized from what's left, consumes nothing) and the Trade Journal
BUY-confirm line, plus the Watchlist sector soft-caution same-ticker fix.

`budget_view.plan_downstream` / `buy_preview` are the pure decisions; app.py is
render-only wiring that no test imports, so its wiring is pinned structurally
(source order / AST) at the bottom. Every "withheld / unchanged" test has a
firing control; the mutations run against each are listed in the build report.
"""
import ast
from tests._ast_cache import parse_file_cached
import copy
import datetime
import pathlib

import pandas as pd
import pytest

from stock_analyzer import act_today_view
from stock_analyzer import budget_view as bv
from stock_analyzer import hold_reasons as hr
from stock_analyzer.constants import DAILY_NEW_POSITION_BUDGET_PCT
from stock_analyzer.watchlist_advisor import _portfolio_risk_gate

pytestmark = pytest.mark.fast

_ROOT = pathlib.Path(__file__).resolve().parent.parent
TODAY = datetime.date(2026, 10, 16)
YESTERDAY = datetime.date(2026, 10, 15)
NOW = datetime.datetime(2026, 10, 16, 15, 0, tzinfo=datetime.timezone.utc)  # 11:00 ET
_COLS = ["traded_at", "action", "ticker", "shares", "price"]
# net capital 10,000 -> budget 40% = 4,000; 24 sh x $100 = 24% of net capital.
INPUTS = {"gross_book": 14000.0, "net_capital": 10000.0, "basis_state": "levered",
          "cash_balance": -4000.0, "cash_as_of": "2026-10-16T13:45:00+00:00"}
NO_TRADES = pd.DataFrame(columns=_COLS)


def _ps(shares=24, price=100.0):
    """A `risk.position_sizing`-shaped dict."""
    return {"shares": shares, "risk_budget": 100.0, "actual_risk": shares * 4.0,
            "risk_per_share": 4.0, "total_cost": round(shares * price, 2),
            "portfolio_pct": round(shares * price / 14000.0 * 100, 1),
            "risk_pct_actual": round(shares * 4.0 / 14000.0 * 100, 2),
            "ceiling_pct": 15.0, "ceiling_capped": False,
            "uncapped_shares": shares, "uncapped_pct": 17.1,
            "capital_capped": False, "capital_pct": float(shares)}


def _ask(tk, comp, shares=24, price=100.0, ps="auto"):
    return {"ticker": tk, "composite": comp, "price": price,
            "ps": _ps(shares, price) if ps == "auto" else ps}


def _gt_cache(picks, trades=NO_TRADES, allocs=None, day=TODAY):
    """A REAL Home cache: budget_view.postpass over a brief with `picks`
    ([(ticker, composite, shares)])."""
    pk = [{"ticker": t, "composite_score": c, "score": 70.0, "price": 100.0,
           "sector": "Tech", "sizing": {**_ps(sh), "port_pct": 17.1}}
          for t, c, sh in picks]
    brief = {"grow_today": {"tone": "bull", "new_picks": pk,
                            "add_positions": [], "overflow_picks": []}}
    out = bv.postpass(brief, INPUTS, trades, {} if allocs is None else allocs,
                      day, NOW)
    return out["cache"]


def _plan(asks, cache, trades=NO_TRADES, allocs=None, inputs=INPUTS, **kw):
    return bv.plan_downstream(inputs, trades, {} if allocs is None else allocs,
                              cache, TODAY, NOW, asks, **kw)


def _sh(plan, tk):
    r = plan["results"][tk]
    return None if r["ps"] is None else r["ps"]["shares"]


def _buy(tk, shares, price, ts="2026-10-16T14:00:00+00:00"):
    return pd.DataFrame([{"traded_at": ts, "action": "BUY", "ticker": tk,
                          "shares": shares, "price": price}])


# ------------------------------------------------------- Watchlist rank 3
def test_rank3_asks_are_sized_from_what_grow_today_left_composite_first():
    cache = _gt_cache([("GT1", 90, 24)])             # Grow Today takes 2,400 first
    asks = [_ask("WLA", 70), _ask("WLB", 80)]        # input order is the WRONG one
    plan = _plan(asks, cache)
    assert plan["state"] == "ok"
    assert _sh(plan, "WLB") == 16                    # 4,000 - 2,400 = 1,600 left
    assert plan["results"]["WLB"]["status"] == "budget_partial"
    assert plan["results"]["WLB"]["note"] == hr.budget_note(
        hr.BUDGET_PARTIAL, {"n": 16, "m": 24})
    assert plan["results"]["WLA"]["ps"] is None      # the higher composite ate it
    assert plan["results"]["WLA"]["note"] == hr.budget_note(
        hr.BUDGET_EXHAUSTED, {"is_pick": False})
    # reversed input order gives the identical result (cross-surface proof)
    assert _plan(list(reversed(asks)), cache)["results"] == plan["results"]


def test_control_without_a_grow_today_pick_the_first_ask_is_full_size():
    cache = _gt_cache([])
    plan = _plan([_ask("WLB", 70), _ask("WLA", 80)], cache)
    assert (_sh(plan, "WLA"), _sh(plan, "WLB")) == (24, 16)
    assert plan["results"]["WLA"]["note"] is None
    assert plan["results"]["WLA"]["ps"] == _ps(24)    # untouched F-255 size


def test_partial_ps_is_rescaled_consistently_and_input_is_not_mutated():
    cache = _gt_cache([("GT1", 90, 24)])
    ask = _ask("WLA", 80)
    before = copy.deepcopy(ask)
    cache_before = copy.deepcopy(cache)
    out = _plan([ask], cache)["results"]["WLA"]["ps"]
    assert out["shares"] == 16 and out["total_cost"] == 1600.0
    assert out["actual_risk"] == 64.0                 # 16 x $4/share
    assert out["budget_capped"] is True and out["budget_uncapped_shares"] == 24
    assert out["uncapped_shares"] == 24               # F-255's own key untouched
    assert ask == before and cache == cache_before


def test_status_summary_counts_grow_today_as_listed_not_allocated():
    cache = _gt_cache([("GT1", 90, 24)])
    s = _plan([_ask("WLA", 80)], cache)["summary"]
    assert s["used_list"] == pytest.approx(4000.0)    # GT 2,400 + this page 1,600
    assert s["used_allocated"] == pytest.approx(0.0)
    assert s["left"] == pytest.approx(0.0)
    assert bv.status_line_text(s, list_label="Grow Today + this page").startswith(
        "New positions today:")
    assert "Grow Today + this page" in bv.status_line_text(
        s, list_label="Grow Today + this page")


def test_a_newer_email_allocation_counts_even_though_home_rendered_before_it():
    cache = _gt_cache([("GT1", 90, 24)])              # built with NO allocations
    base = _plan([_ask("WLA", 80, shares=24)], cache)
    assert _sh(base, "WLA") == 16
    later = _plan([_ask("WLA", 80, shares=24)], cache,
                  allocs={"EML": {"shares": 10, "dollars": 1000.0}})
    assert _sh(later, "WLA") == 6                     # 4,000 - 2,400 - 1,000


def test_a_trade_logged_since_home_rendered_counts():
    cache = _gt_cache([("GT1", 90, 24)])
    assert _sh(_plan([_ask("WLA", 80)], cache), "WLA") == 16
    # stamped BEFORE the cash record (13:45Z) so the basis is not lag-adjusted
    # and the arithmetic stays 4,000 - 2,400 - 1,000 logged.
    early = _buy("NEWB", 10, 100.0, ts="2026-10-16T13:00:00+00:00")
    assert _sh(_plan([_ask("WLA", 80)], cache, trades=early), "WLA") == 6
    # a buy AFTER the cash record also lowers the basis (lag correction): 3,600
    # budget - 2,400 - 1,000 = 200 -> 2 shares. Both directions shrink the size.
    assert _sh(_plan([_ask("WLA", 80)], cache, trades=_buy("NEWB", 10, 100.0)),
               "WLA") == 2


def test_a_logged_buy_of_a_grow_today_pick_replaces_its_grow_today_charge():
    cache = _gt_cache([("GT1", 90, 24)])
    # GT1 bought for 1,000 (not 2,400): the logged $ counts, not Home's.
    early = _buy("GT1", 10, 100.0, ts="2026-10-16T13:00:00+00:00")
    plan = _plan([_ask("WLA", 80)], cache, trades=early)
    assert _sh(plan, "WLA") == 24                     # 4,000 - 1,000 = 3,000 left
    s = plan["summary"]
    # the bought pick is charged ONCE, as a logged buy; only WLA is "listed"
    assert s["used_logged"] == pytest.approx(1000.0)
    assert s["used_list"] == pytest.approx(2400.0)
    assert s["used_allocated"] == pytest.approx(0.0)


# ------------------------------------------------------- one share count
def test_ticker_that_is_a_grow_today_pick_is_sized_in_grow_today_only():
    cache = _gt_cache([("BOTH", 90, 24)])
    plan = _plan([_ask("BOTH", 90), _ask("OTHR", 70)], cache)
    r = plan["results"]["BOTH"]
    assert r["status"] == "grow_today" and r["ps"] is None
    assert r["note"] == bv.GROW_TODAY_SIZED_NOTE == "Sized in Grow Today today — see 🏠 Home"
    # and it must not eat any room from the Watchlist-only ask
    assert _sh(plan, "OTHR") == 16                    # 4,000 - GT's 2,400


def test_control_a_ticker_that_is_not_a_pick_gets_its_own_size():
    cache = _gt_cache([("GT1", 90, 24)])
    r = _plan([_ask("OTHR", 90)], cache)["results"]["OTHR"]
    assert r["status"] != "grow_today" and r["ps"] is not None


def test_emailed_watchlist_ticker_shows_its_reserved_size():
    cache = _gt_cache([])
    plan = _plan([_ask("WLE", 80, shares=24)], cache,
                 allocs={"WLE": {"shares": 10, "dollars": 1000.0}})
    r = plan["results"]["WLE"]
    assert r["status"] == "budget_reserved"
    assert r["ps"]["shares"] == 10 and r["ps"]["budget_reserved"] is True
    assert r["note"] == "Reserved by today's email run"


def test_control_no_allocation_means_no_reservation():
    r = _plan([_ask("WLE", 80, shares=24)], _gt_cache([]))["results"]["WLE"]
    assert r["status"] == "sized" and r["ps"]["shares"] == 24


def test_reserved_size_never_exceeds_the_current_f255_size():
    r = _plan([_ask("WLE", 80, shares=8)], _gt_cache([]),
              allocs={"WLE": {"shares": 10, "dollars": 1000.0}})["results"]["WLE"]
    assert r["ps"]["shares"] == 8


# ------------------------------------------------------- staleness / unknown
@pytest.mark.parametrize("label,cache", [
    ("yesterday's cache", _gt_cache([("GT1", 90, 24)], day=YESTERDAY)),
    ("no cache (Home not built / couldn't check)", None),
])
def test_cache_not_describing_today_is_never_trusted(label, cache):
    plan = _plan([_ask("WLA", 80)], cache)
    r = plan["results"]["WLA"]
    assert r["ps"] is None and r["status"] == "budget_unknown", label
    assert r["note"] == hr.budget_note(
        hr.BUDGET_UNKNOWN, {"why": bv.HOME_NOT_LOADED_WHY})
    assert "open 🏠 Home first" in r["note"]
    assert plan["state"] == "unknown" and plan["summary"] is None


def test_control_todays_cache_sizes_the_same_ask():
    r = _plan([_ask("WLA", 80)], _gt_cache([("GT1", 90, 24)]))["results"]["WLA"]
    assert r["ps"] is not None and r["status"] == "budget_partial"


def test_a_cache_with_no_grow_ticker_list_is_unknown_not_no_picks():
    cache = _gt_cache([])
    assert cache["grow_tickers"] == []                # checked, nothing: known
    assert _plan([_ask("WLA", 80)], cache)["results"]["WLA"]["ps"] is not None
    blind = dict(cache)
    blind["grow_tickers"] = None                      # no Grow Today list at all
    assert _plan([_ask("WLA", 80)], blind)["results"]["WLA"]["ps"] is None


def test_yesterdays_cache_does_not_even_apply_its_grow_today_tickers():
    cache = _gt_cache([("BOTH", 90, 24)], day=YESTERDAY)
    r = _plan([_ask("BOTH", 90)], cache)["results"]["BOTH"]
    assert r["status"] == "budget_unknown"            # not "Sized in Grow Today"


def test_unreadable_trades_or_allocations_withhold_every_size():
    cache = _gt_cache([])
    plan = bv.plan_downstream(INPUTS, None, {}, cache, TODAY, NOW, [_ask("A", 80)])
    assert plan["results"]["A"]["ps"] is None
    plan = bv.plan_downstream(INPUTS, NO_TRADES, None, cache, TODAY, NOW, [_ask("A", 80)])
    assert plan["results"]["A"]["ps"] is None
    assert "emailed sizes couldn't be read" in plan["results"]["A"]["note"]
    # control: both readable -> sized
    ok = bv.plan_downstream(INPUTS, NO_TRADES, {}, cache, TODAY, NOW, [_ask("A", 80)])
    assert ok["results"]["A"]["ps"] is not None


def test_stale_cash_record_gives_the_basis_reason_not_the_home_reason():
    stale = dict(INPUTS, basis_state="stale")
    r = _plan([_ask("A", 80)], None, inputs=stale)["results"]["A"]
    assert "cash record is out of date" in r["note"]
    assert bv.HOME_NOT_LOADED_WHY not in r["note"]


def test_margin_called_book_has_no_budget_and_says_so():
    called = dict(INPUTS, basis_state="called", net_capital=-100.0)
    r = _plan([_ask("A", 80)], _gt_cache([]), inputs=called)["results"]["A"]
    assert r["ps"] is None
    assert r["note"] == hr.budget_note(hr.BUDGET_EXHAUSTED, {"called": True})


def test_no_size_marker_asks_pass_through_and_consume_nothing():
    cache = _gt_cache([])
    plan = _plan([_ask("NOSZ", 95, ps=None), _ask("WLA", 80)], cache)
    assert plan["results"]["NOSZ"] == {"status": "passthrough", "ps": None, "note": None}
    assert _sh(plan, "WLA") == 24


# ------------------------------------------------------- Analysis
def test_analysis_is_min_of_f255_and_what_is_left_and_consumes_nothing():
    cache = _gt_cache([("GT1", 90, 24)])             # 1,600 left
    asks = [_ask("AAA", 80, shares=24), _ask("BBB", 70, shares=24),
            _ask("CCC", 60, shares=8)]
    solo = _plan(asks, cache, mirror_gt=True, independent=True)
    assert (_sh(solo, "AAA"), _sh(solo, "BBB"), _sh(solo, "CCC")) == (16, 16, 8)
    # control: the same asks as ONE consuming list would starve the later ones
    joint = _plan(asks, cache)
    assert _sh(joint, "BBB") is None
    # looking at AAA changed nothing about BBB: alone == in company
    alone = _plan([_ask("BBB", 70, shares=24)], cache, mirror_gt=True,
                  independent=True)
    assert alone["results"]["BBB"] == solo["results"]["BBB"]


def test_analysis_never_exceeds_the_f255_size_even_with_plenty_left():
    r = _plan([_ask("AAA", 80, shares=5)], _gt_cache([]), mirror_gt=True,
              independent=True)["results"]["AAA"]
    assert r["ps"]["shares"] == 5 and r["note"] is None


def test_analysis_of_a_grow_today_pick_shows_that_same_size():
    cache = _gt_cache([("GT1", 90, 24), ("GT2", 80, 24)])   # GT2 got 16
    plan = _plan([_ask("GT2", 80, shares=24), _ask("GT1", 90, shares=24)], cache,
                 mirror_gt=True, independent=True)
    assert _sh(plan, "GT2") == 16 and _sh(plan, "GT1") == 24
    assert plan["results"]["GT2"]["note"] == bv.GROW_TODAY_MIRROR_NOTE
    # watchlist (no mirror) shows NO count for the same ticker
    assert _plan([_ask("GT2", 80)], cache)["results"]["GT2"]["ps"] is None


def test_analysis_of_a_grow_today_pick_that_was_not_sized_shows_why():
    cache = _gt_cache([("GT1", 90, 40), ("GT2", 80, 24)])   # GT2 exhausted
    r = _plan([_ask("GT2", 80)], cache, mirror_gt=True,
              independent=True)["results"]["GT2"]
    assert r["ps"] is None
    assert r["note"] == hr.budget_note(hr.BUDGET_EXHAUSTED, {"is_pick": False})


def test_analysis_of_an_emailed_ticker_shows_the_emailed_size():
    r = _plan([_ask("EML", 80, shares=24)], _gt_cache([]),
              allocs={"EML": {"shares": 10, "dollars": 1000.0}},
              mirror_gt=True, independent=True)["results"]["EML"]
    assert r["ps"]["shares"] == 10 and r["status"] == "budget_reserved"


def test_exported_size_equals_on_screen_size_one_source():
    """Every Analysis sizing site reads ONE `_an_sizing(ticker)` result, so the
    exported brief cannot differ from the screen. Pinned structurally."""
    src = _app_src()
    seg = _segment(src, 'elif page == "📈 Analysis":', 'elif page == "📋 Watchlist":')
    assert seg.count("position_sizing(") == 1         # the pre-pass only
    assert seg.count("_an_sizing(ticker)") == 3       # scorecard, detailed, export
    export = seg[seg.index("📋 Analysis Summary"):]
    assert "ps = _an_sizing(ticker)[0]" in export
    assert "position_sizing(" not in export


# ------------------------------------------------------- never raises
def test_a_crash_withholds_every_size_with_a_visible_reason():
    class Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("x")
    plan = bv.plan_downstream(Boom(), NO_TRADES, {}, _gt_cache([]), TODAY, NOW,
                              [_ask("A", 80), _ask("B", 70, ps=None)])
    assert plan["results"]["A"]["ps"] is None
    assert "the budget check failed" in plan["results"]["A"]["note"]
    assert plan["results"]["B"]["status"] == "passthrough"


# ------------------------------------------------------- pure helpers
def test_postpass_cache_publishes_the_grow_today_ticker_list():
    cache = _gt_cache([("ZZZ", 70, 5), ("AAA", 80, 5)])
    assert cache["grow_tickers"] == ["AAA", "ZZZ"]    # sorted, unique
    no_brief = bv.postpass(None, INPUTS, NO_TRADES, {}, TODAY, NOW)["cache"]
    assert no_brief["grow_tickers"] is None           # unknown, never []


def test_grow_list_known_requires_today_and_a_list():
    c = _gt_cache([("A", 80, 5)])
    assert bv.grow_list_known(c, TODAY) is True
    assert bv.grow_list_known(c, YESTERDAY) is False
    assert bv.grow_list_known(None, TODAY) is False
    assert bv.grow_list_known(dict(c, grow_tickers=None), TODAY) is False
    assert bv.grow_list_known(dict(c, per_ticker=None), TODAY) is False


def test_scale_ps_scales_together_and_leaves_input_alone():
    ps = _ps(24)
    before = copy.deepcopy(ps)
    out = bv.scale_ps(ps, 12)
    assert out["shares"] == 12 and out["total_cost"] == 1200.0
    assert out["actual_risk"] == 48.0 and out["risk_per_share"] == 4.0
    assert ps == before
    assert bv.scale_ps(ps, 24) == ps and bv.scale_ps(ps, 0) == ps


def test_grow_sector_tickers_maps_picks_and_adds_and_is_none_when_unknown():
    g = {"new_picks": [{"ticker": "aaa", "sector": "Tech"}],
         "add_positions": [{"ticker": "BBB", "sector": "Tech"},
                           {"ticker": "CCC", "sector": ""}]}
    assert bv.grow_sector_tickers(g) == {"Tech": {"AAA", "BBB"}}
    assert bv.grow_sector_tickers(None) is None
    assert bv.grow_sector_tickers({}) == {}


# ------------------------------------------------------- sector soft-caution
def _gt_ctx(by_sector):
    return {"sector_of_ticker": "Energy", "grow_today_sectors": {"Energy"},
            "grow_today_sector_tickers": by_sector}


def test_sector_caution_skips_when_the_grow_today_pick_is_the_same_ticker():
    gate = _portfolio_risk_gate(1.0, _gt_ctx({"Energy": {"XOM"}}), "xom")
    assert gate is None


def test_sector_caution_still_fires_for_a_different_grow_today_pick():
    gate = _portfolio_risk_gate(1.0, _gt_ctx({"Energy": {"CVX"}}), "XOM")
    assert gate is not None and gate["severity"] == "soft"
    assert "pick the higher-conviction setup" in gate["reason"]


def test_sector_caution_still_fires_when_the_same_ticker_shares_the_sector_with_another():
    gate = _portfolio_risk_gate(1.0, _gt_ctx({"Energy": {"XOM", "CVX"}}), "XOM")
    assert gate is not None and "higher-conviction" in gate["reason"]


def test_sector_caution_keeps_firing_when_the_ticker_map_is_unknown():
    # unknown (None / sector missing from the map / no ticker passed) = old behaviour
    for ctx, tk in ((_gt_ctx(None), "XOM"), (_gt_ctx({"Tech": {"XOM"}}), "XOM"),
                    (_gt_ctx({"Energy": {"XOM"}}), None)):
        gate = _portfolio_risk_gate(1.0, ctx, tk)
        assert gate is not None and gate["severity"] == "soft"


# ------------------------------------------------------- Trade Journal line
def test_buy_preview_includes_the_pending_buy():
    # stamped before the cash record (13:45Z): no lag adjustment of the basis
    early = _buy("AAA", 10, 100.0, ts="2026-10-16T13:00:00+00:00")
    prev = bv.buy_preview(INPUTS, early, 1500.0, TODAY, NOW)
    assert prev["state"] == "ok"
    assert prev["pct"] == pytest.approx(25.0)         # (1,000 logged + 1,500) / 10,000
    assert prev["text"] == ("Today's new buys including this one: 25.0% of net "
                            f"capital (daily budget {DAILY_NEW_POSITION_BUDGET_PCT:g}%).")
    assert prev["over"] is False
    # control: the pending $ is what moved it
    assert bv.buy_preview(INPUTS, early, 0.0, TODAY, NOW)["pct"] == pytest.approx(10.0)


def test_buy_preview_flags_a_buy_that_crosses_the_budget_and_never_blocks():
    prev = bv.buy_preview(INPUTS, NO_TRADES, 4500.0, TODAY, NOW)
    assert prev["over"] is True and "above the budget" in prev["text"]
    assert "block" not in prev["text"].lower()
    assert bv.buy_preview(INPUTS, NO_TRADES, 4000.0, TODAY, NOW)["over"] is False


def test_buy_preview_counts_today_only():
    old = _buy("AAA", 10, 100.0, ts="2026-10-15T14:00:00+00:00")
    assert bv.buy_preview(INPUTS, old, 0.0, TODAY, NOW)["pct"] == pytest.approx(0.0)


def test_buy_preview_says_it_couldnt_check_rather_than_going_silent():
    for prev in (bv.buy_preview(INPUTS, None, 100.0, TODAY, NOW),
                 bv.buy_preview(INPUTS, NO_TRADES, None, TODAY, NOW),
                 bv.buy_preview(dict(INPUTS, basis_state="stale"), NO_TRADES, 100.0,
                                TODAY, NOW),
                 bv.buy_preview(dict(INPUTS, basis_state="unknown"), NO_TRADES, 100.0,
                                TODAY, NOW)):
        assert prev["state"] == "unknown"
        assert "couldn't be checked" in prev["text"] and "not blocked" in prev["text"]


def test_buy_preview_is_silent_for_a_backdated_buy():
    p = bv.buy_preview(INPUTS, NO_TRADES, 100.0, TODAY, NOW,
                       pending_traded_at="2026-10-14T16:00:00+00:00")
    assert p == {"state": "not_today", "text": None}
    # control: a buy stamped today is previewed
    q = bv.buy_preview(INPUTS, NO_TRADES, 100.0, TODAY, NOW,
                       pending_traded_at="2026-10-16T14:00:00+00:00")
    assert q["state"] == "ok"


# ------------------------------------------------------- app.py wiring pins
def _app_src():
    return (_ROOT / "app.py").read_text(encoding="utf-8")


def _segment(src, start, end):
    a = src.index(start)
    return src[a:src.index(end, a)]


def test_app_parses():
    parse_file_cached(_ROOT / "app.py")


def test_downstream_helper_re_reads_strictly_and_never_trusts_session_trades():
    src = _app_src()
    seg = _segment(src, "def _budget_downstream_plan(", "def _refresh_portfolio_cache_after_trade")
    assert "_budget_strict_trades()" in seg and "_budget_strict_allocations()" in seg
    assert "_new_position_budget_cache" in seg
    assert "trades_df" not in seg.replace("_budget_strict_trades", "")
    assert "plan_downstream" in seg


def test_watchlist_sizes_every_enter_now_ask_before_any_card_renders():
    src = _app_src()
    seg = _segment(src, 'elif page == "📋 Watchlist":', "# PAGE — THESIS")
    pre = seg.index("_wl_bplan = _budget_downstream_plan(")
    loop = seg.index("for _wr in _wl_view:")
    assert pre < loop
    # asks are built by the pure budget_view.watchlist_asks (chunk 4 of the shared
    # entry check): ENTER_NOW only, not-held, not-sold-today, composite = the
    # card's score -- pinned at its boundaries in tests/test_watchlist_entry_page.py
    assert seg.index("_budget_view.watchlist_asks(") < loop
    asks = seg[seg.index("_wl_budget_asks = _budget_view.watchlist_asks("):pre]
    for needle in ("_wl_recs", "_wl_held", "_wl_sold_today", "_f255_wl_pv"):
        assert needle in asks, needle
    # the row consumes the plan's result and keeps the F-255 size for the cap lines
    assert '_wl_ps = _wl_bud["ps"]' in seg
    assert "_sizing_cap_lines(\n                            _wl_ps_f255" in seg
    assert 'elif _wl_bud is not None and _wl_bud["note"]:' in seg
    assert seg.count("_budget_downstream_plan(") == 1
    # B1: the Watchlist publishes its counts for Analysis, before any card
    pub = seg.index('st.session_state["_wl_budget_plan_cache"] = _budget_view.wl_plan_cache(')
    assert pre < pub < loop
    assert '_stamp_coord(["_wl_budget_plan_cache"])' in seg[pub:loop]
    # N5: the row looks its result up by the upper-cased ticker (the plan's key)
    assert '_wl_bplan["results"].get(str(_ticker).upper())' in seg
    # N3: a Home-not-loaded withhold offers ONE nav button via _pending_page
    assert '_budget_view.home_needed(_wl_bplan)' in seg[:loop]
    assert seg[:loop].count('key="_wl_budget_goto_home"') == 1
    assert 'st.session_state["_pending_page"] = "🏠 Home"' in seg[:loop]
    assert "nav_page" not in seg[pre:loop]


def test_watchlist_passes_the_same_ticker_map_to_the_advisor():
    seg = _segment(_app_src(), 'elif page == "📋 Watchlist":', "# PAGE — THESIS")
    assert '"grow_today_sector_tickers": _wl_gt_sector_tickers' in seg
    assert "_budget_view.grow_sector_tickers(" in seg


def test_advisor_receives_the_ticker_it_is_judging():
    src = (_ROOT / "stock_analyzer" / "watchlist_advisor.py").read_text(encoding="utf-8")
    assert "_portfolio_risk_gate(ticker_beta, portfolio_ctx, ticker)" in src


def test_analysis_plan_is_independent_and_mirrors_grow_today():
    seg = _segment(_app_src(), 'elif page == "📈 Analysis":', 'elif page == "📋 Watchlist":')
    call = seg[seg.index("_an_bplan = _budget_downstream_plan("):]
    call = call[:call.index(")\n")]
    assert "mirror_gt=True" in call and "independent=True" in call
    assert "need_wl=True" in call                     # B1: counts the Watchlist
    # N2: an unknown plan says WHY (not just a blank Shares column)
    pre = seg[:seg.index("# ── Summary scorecard")]
    assert "Share counts are withheld" in pre and "_an_bplan['reason']" in pre
    # N3: one button per page each, via _pending_page only
    for key, page in (("_an_budget_goto_home", "🏠 Home"),
                      ("_an_budget_goto_wl", "📋 Watchlist")):
        assert pre.count(f'key="{key}"') == 1
        assert f'st.session_state["_pending_page"] = "{page}"' in pre
    assert "nav_page" not in pre.replace("_pending_page", "")
    # the caveat travels with the note into every Analysis surface
    assert '_res.get("caveat")' in pre


def test_trade_journal_line_sits_in_the_buy_confirm_card_before_the_button():
    seg = _segment(_app_src(), 'elif page == "📒 Trade Journal":', "# PAGE — TRADE REVIEW")
    card = seg[seg.index('_pending_buy = st.session_state.get("_tj_pending_buy")'):]
    line = card.index("_budget_view.buy_preview(")
    assert line < card.index('"✅ Confirm BUY"')
    assert card.index("Confirm this BUY before it's recorded") < line
    call = card[line:card.index(")\n        except", line)]
    assert "_budget_strict_trades()" in call
    assert "float(_pb_shares) * float(_pb_price)" in call
    assert 'pending_traded_at=_pending_buy.get("traded_at")' in call
    # never blocks: the button is not conditioned on the preview
    btn = card[card.index('"✅ Confirm BUY"'):][:300]
    assert "_pb_prev" not in btn


# ======================================================= review round 1 fixes
def _wl_pub(asks, cache, **kw):
    """Run the Watchlist plan and return (plan, the cache it publishes: the ASKS)."""
    plan = _plan(asks, cache, **kw)
    return plan, bv.wl_plan_cache(asks, TODAY, 7)


def _an_plan(asks, cache, wl, epoch=7, **kw):
    return bv.plan_downstream(kw.pop("inputs", INPUTS), kw.pop("trades", NO_TRADES),
                              kw.pop("allocs", {}), cache, TODAY,
                              NOW, asks, mirror_gt=True, independent=True,
                              need_wl=True, wl_cache=wl, epoch=epoch)


# ---- B1: Analysis counts the Watchlist's asks
def _b1_world():
    cache = _gt_cache([("GT1", 90, 16)])              # GT takes 1,600 -> 2,400 left
    wl_plan, wl = _wl_pub([_ask("W1", 90, shares=20), _ask("W2", 80, shares=24)], cache)
    return cache, wl_plan, wl


def test_b1_watchlist_partial_ticker_gets_the_same_count_on_analysis():
    cache, wl_plan, wl = _b1_world()
    assert (_sh(wl_plan, "W1"), _sh(wl_plan, "W2")) == (20, 4)
    an = _an_plan([_ask("W1", 90, shares=24), _ask("W2", 80, shares=24)], cache, wl)
    assert _sh(an, "W1") == 20 and _sh(an, "W2") == 4   # same count, not 20 / 24
    assert an["results"]["W2"]["note"] == bv.WL_MIRROR_NOTE == "Same size as on 📋 Watchlist"
    assert an["results"]["W2"]["status"] == "watchlist_mirror"
    assert "caveat" not in an["results"]["W2"]


def test_b1_a_non_watchlist_ticker_is_sized_from_what_the_watchlist_left():
    cache, wl_plan, wl = _b1_world()
    an = _an_plan([_ask("X", 70, shares=20)], cache, wl)
    r = an["results"]["X"]
    assert r["ps"] is None and r["status"] == "budget_exhausted"   # 2,400 all spent
    # control: without the Watchlist's plan the same ask gets a full size
    # (and the caveat says the Watchlist isn't counted)
    blind = _an_plan([_ask("X", 70, shares=20)], cache, None)["results"]["X"]
    assert blind["ps"]["shares"] == 20 and blind["caveat"] == bv.WL_MISSING_NOTE


def test_b1_watchlist_plus_analysis_never_exceed_what_is_left():
    cache, wl_plan, wl = _b1_world()
    left = 2400.0
    an = _an_plan([_ask("W1", 90, shares=24), _ask("W2", 80, shares=24),
                   _ask("X", 70, shares=24)], cache, wl)
    spent = sum(r["ps"]["total_cost"] for r in an["results"].values() if r["ps"])
    # W1+W2 (the Watchlist's own) are the only spend; X has nothing left
    assert spent == pytest.approx(left) and spent <= left + 1e-9


def test_b1_a_stale_watchlist_plan_is_not_trusted():
    cache, wl_plan, wl = _b1_world()
    for bad in (dict(wl, day="2026-10-15"), dict(wl, epoch=6), None,
                {"day": wl["day"], "epoch": 7, "asks": None}):
        r = _an_plan([_ask("W2", 80, shares=24)], cache, bad)["results"]["W2"]
        assert r["caveat"] == bv.WL_MISSING_NOTE, bad     # own size + disclosure
        assert r["status"] != "watchlist_mirror"
    ok = _an_plan([_ask("W2", 80, shares=24)], cache, wl)["results"]["W2"]
    assert ok["status"] == "watchlist_mirror"            # control: fresh is used


def test_b1_one_switch_flips_default_show_to_withhold(monkeypatch):
    cache, wl_plan, wl = _b1_world()
    ask = [_ask("X", 70, shares=20)]
    monkeypatch.setattr(bv, "ANALYSIS_WITHHOLD_WITHOUT_WATCHLIST", False)
    shown = _an_plan(ask, cache, None)["results"]["X"]
    assert shown["ps"] is not None and shown["caveat"] == bv.WL_MISSING_NOTE
    monkeypatch.setattr(bv, "ANALYSIS_WITHHOLD_WITHOUT_WATCHLIST", True)
    held = _an_plan(ask, cache, None)["results"]["X"]
    assert held["ps"] is None and bv.WL_REQUIRED_WHY in held["note"]
    assert "open 📋 Watchlist first" in held["note"]
    # a FRESH Watchlist plan makes the flag irrelevant (control)
    fresh = _an_plan(ask, _gt_cache([]), bv.wl_plan_cache([], TODAY, 7))["results"]["X"]
    assert fresh["ps"] is not None and "caveat" not in fresh
    assert bv.ANALYSIS_WITHHOLD_WITHOUT_WATCHLIST is True  # patched, restored after


def test_b1_default_is_show_with_caveat():
    assert bv.ANALYSIS_WITHHOLD_WITHOUT_WATCHLIST is False


def test_b1_needs_watchlist_flags_both_settings(monkeypatch):
    cache = _gt_cache([])
    ask = [_ask("X", 70)]
    monkeypatch.setattr(bv, "ANALYSIS_WITHHOLD_WITHOUT_WATCHLIST", False)
    assert bv.needs_watchlist(_an_plan(ask, cache, None)) is True
    monkeypatch.setattr(bv, "ANALYSIS_WITHHOLD_WITHOUT_WATCHLIST", True)
    assert bv.needs_watchlist(_an_plan(ask, cache, None)) is True
    fresh = bv.wl_plan_cache([], TODAY, 7)
    assert bv.needs_watchlist(_an_plan(ask, cache, fresh)) is False


def test_b1_watchlist_publishes_its_asks_not_its_results():
    assert bv.wl_plan_cache(None, TODAY, 7) is None          # never built its asks
    assert bv.wl_plan_cache("x", TODAY, 7) is None
    empty = bv.wl_plan_cache([], TODAY, 7)                    # checked, nothing
    assert empty == {"day": "2026-10-16", "epoch": 7, "asks": []}
    ps = _ps(24)
    ok = bv.wl_plan_cache([{"ticker": "a", "composite": 80, "price": 100.0, "ps": ps},
                           {"ticker": "", "ps": None}, "junk"], TODAY, 7)
    assert ok["asks"] == [{"ticker": "A", "composite": 80, "price": 100.0, "ps": ps}]
    assert "results" not in ok and "home_sig" not in ok and "alloc_sig" not in ok
    assert ok["asks"][0]["ps"] is not ps                      # a copy, not an alias


def test_b1_registry_and_surface_wiring():
    from stock_analyzer import coord_freshness as cf
    k = "_wl_budget_plan_cache"
    assert cf.tier_of(k) == cf.TIER_GATE
    assert k in cf.keys_for_surface("an")
    assert k in cf.keys_for_producer("watchlist")
    assert "watchlist" in cf.PRODUCER_LABELS


def test_b1_helper_passes_the_published_watchlist_cache_and_epoch():
    seg = _segment(_app_src(), "def _budget_downstream_plan(",
                   "def _refresh_portfolio_cache_after_trade")
    assert 'wl_cache=st.session_state.get("_wl_budget_plan_cache")' in seg
    assert "epoch=_coord_epoch()" in seg


# ---- B2: the offline stub is UNKNOWN, not "checked, no picks"
def test_b2_offline_stub_brief_publishes_an_unknown_grow_list():
    stub = act_today_view.offline_brief_stub()
    assert stub["grow_today"] == {}                    # the trap: a dict
    cache = bv.postpass(stub, INPUTS, NO_TRADES, {}, TODAY, NOW)["cache"]
    assert cache["grow_tickers"] is None and cache["grow_state"] == "offline"
    # control: a real brief with no picks is "checked, nothing"
    real = bv.postpass({"grow_today": {"new_picks": [], "add_positions": []}},
                       INPUTS, NO_TRADES, {}, TODAY, NOW)["cache"]
    assert real["grow_tickers"] == [] and real["grow_state"] == "ok"
    assert bv.postpass(None, INPUTS, NO_TRADES, {}, TODAY, NOW)["cache"]["grow_state"] == "none"


def test_b2_offline_stub_cache_withholds_watchlist_sizes_with_a_true_reason():
    cache = bv.postpass(act_today_view.offline_brief_stub(), INPUTS, NO_TRADES, {},
                        TODAY, NOW)["cache"]
    plan = _plan([_ask("A", 80)], cache)
    r = plan["results"]["A"]
    assert r["ps"] is None and bv.GROW_OFFLINE_WHY in r["note"]
    assert r["note"] == hr.budget_note(hr.BUDGET_UNKNOWN,
                                       {"why": "today's Grow Today list couldn't be built"})
    assert plan["reason"] == bv.GROW_OFFLINE_WHY
    assert bv.HOME_NOT_LOADED_WHY not in r["note"]
    assert bv.home_needed(plan) is False               # opening Home isn't the fix
    # control: Home-not-loaded keeps its own reason + button
    none_plan = _plan([_ask("A", 80)], None)
    assert bv.HOME_NOT_LOADED_WHY in none_plan["results"]["A"]["note"]
    assert bv.home_needed(none_plan) is True


def test_b2_both_app_paths_go_through_postpass():
    """memo-HIT, MISS and the post-trade republisher all publish via
    `_publish_new_position_budget` -> postpass, so the stub guard covers them."""
    src = _app_src()
    assert src.count("_publish_new_position_budget(") >= 4   # def + 3 callers


# ---- B3: an emailed allocation made after Home rendered wins on Analysis
def test_b3_analysis_shows_the_email_size_for_a_grow_today_ticker():
    cache = _gt_cache([("GT1", 90, 24)])               # Home 09:30: GT1 = 24
    allocs = {"GT1": {"shares": 10, "dollars": 1000.0}}  # email landed at 09:45
    an = _an_plan([_ask("GT1", 90, shares=24)], cache, None, allocs=allocs)
    r = an["results"]["GT1"]
    assert r["ps"]["shares"] == 10 and r["status"] == "budget_reserved"
    assert r["note"] == "Reserved by today's email run"
    # control: no allocation -> Home's own size is mirrored
    plain = _an_plan([_ask("GT1", 90, shares=24)], cache, None)["results"]["GT1"]
    assert plain["ps"]["shares"] == 24 and plain["status"] == "grow_today_mirror"
    # a logged buy replaces the allocation -> back to Home's entry (not reserved)
    early = _buy("GT1", 5, 100.0, ts="2026-10-16T13:00:00+00:00")
    bought = bv.plan_downstream(INPUTS, early, allocs, cache, TODAY, NOW,
                                [_ask("GT1", 90, shares=24)], mirror_gt=True,
                                independent=True)["results"]["GT1"]
    assert bought["status"] == "grow_today_mirror"


def test_b3_watchlist_still_shows_no_second_count_for_that_ticker():
    cache = _gt_cache([("GT1", 90, 24)])
    r = _plan([_ask("GT1", 90)], cache,
              allocs={"GT1": {"shares": 10, "dollars": 1000.0}})["results"]["GT1"]
    assert r["status"] == "grow_today" and r["ps"] is None


# ---- N1 / N4
def test_n1_mirror_min_is_pinned_and_the_note_is_honest():
    cache = _gt_cache([("GT1", 90, 24)])
    small = _plan([_ask("GT1", 90, shares=8)], cache, mirror_gt=True,
                  independent=True)["results"]["GT1"]
    assert small["ps"]["shares"] == 8                   # never above Analysis's own
    assert small["note"] == ("Grow Today's card shows 24; this page's own sizing "
                             "allows 8")
    same = _plan([_ask("GT1", 90, shares=24)], cache, mirror_gt=True,
                 independent=True)["results"]["GT1"]
    assert same["note"] == bv.GROW_TODAY_MIRROR_NOTE


def test_n1_watchlist_mirror_min_is_pinned_too():
    cache, wl_plan, wl = _b1_world()
    r = _an_plan([_ask("W2", 80, shares=2)], cache, wl)["results"]["W2"]
    assert r["ps"]["shares"] == 2
    assert r["note"] == "Watchlist shows 4; this page's own sizing allows 2"


def test_n4_an_entry_without_a_status_is_unknown_never_the_full_size():
    for entry in (None, {}, {"status": None}):
        r = bv._from_entry(_ps(24), entry, False)
        assert r["ps"] is None and r["status"] == "budget_unknown"
        assert "no budget result" in r["note"]
    ok = bv._from_entry(_ps(24), {"status": "sized"}, False)   # control
    assert ok["ps"]["shares"] == 24


# ======================================================= review round 2 fixes
def _w1_world(allocs_then=None):
    """Watchlist rendered W1 (20 sh) with Grow Today at 16; returns
    (home cache, published wl cache)."""
    cache = _gt_cache([("GT1", 90, 16)])
    asks = [_ask("W1", 90, shares=20)]
    plan = _plan(asks, cache, allocs={} if allocs_then is None else allocs_then)
    assert _sh(plan, "W1") == 20
    return cache, bv.wl_plan_cache(asks, TODAY, 7)


def test_r2a_an_email_after_the_watchlist_render_beats_the_watchlist_count():
    cache, wl = _w1_world()
    allocs = {"W1": {"shares": 15, "dollars": 1500.0}}      # 09:45 email
    r = _an_plan([_ask("W1", 90, shares=24)], cache, wl, allocs=allocs)["results"]["W1"]
    assert r["ps"]["shares"] == 15 and r["status"] == "budget_reserved"
    # control: no new email -> the Watchlist's 20 is mirrored
    ok = _an_plan([_ask("W1", 90, shares=24)], cache, wl)["results"]["W1"]
    assert ok["status"] == "watchlist_mirror" and ok["ps"]["shares"] == 20
    # control: the SAME allocations at both times: Analysis sees the same room
    cache2, wl2 = _w1_world(allocs_then={"X": {"shares": 4, "dollars": 400.0}})
    same = _an_plan([_ask("W1", 90, shares=24)], cache2, wl2,
                    allocs={"X": {"shares": 4, "dollars": 400.0}})["results"]["W1"]
    assert same["status"] == "watchlist_mirror"
    # a changed allocation SIZE (same ticker): Analysis re-runs against it, so
    # the Watchlist's count shrinks with the room (4,000 - 1,600 - 900 = 1,500)
    bumped = _an_plan([_ask("W1", 90, shares=24)], cache2, wl2,
                      allocs={"X": {"shares": 9, "dollars": 900.0}})["results"]["W1"]
    assert bumped["ps"]["shares"] == 15 and "caveat" not in bumped


def test_r2b_a_home_rerender_that_changed_grow_today_invalidates_the_plan():
    cache, wl = _w1_world()
    # Home re-renders (no trade, same epoch) and Grow Today now also holds H:
    cache_h = _gt_cache([("GT1", 90, 16), ("H", 85, 24)])
    r = _an_plan([_ask("W1", 90, shares=24)], cache_h, wl)["results"]["W1"]
    assert r["status"] != "watchlist_mirror"
    # Home's grow list changed: the room is gone (16 + 24 sh), so the honest
    # answer is "budget used" -- what a Watchlist re-render would also say
    assert r["ps"] is None and r["status"] == "budget_exhausted"
    # control: unchanged Home content -> still the Watchlist's 20
    ok = _an_plan([_ask("W1", 90, shares=24)], cache, wl)["results"]["W1"]
    assert ok["status"] == "watchlist_mirror"


def test_r2b_an_unchanged_home_rerender_still_mirrors():
    cache, wl = _w1_world()
    later = datetime.datetime(2026, 10, 16, 15, 30, tzinfo=datetime.timezone.utc)
    rebuilt = bv.postpass({"grow_today": {"tone": "bull", "new_picks": [
        {"ticker": "GT1", "composite_score": 90, "score": 70.0, "price": 100.0,
         "sector": "Tech", "sizing": {**_ps(16), "port_pct": 17.1}}],
        "add_positions": [], "overflow_picks": []}}, INPUTS, NO_TRADES, {}, TODAY,
        later)["cache"]
    assert rebuilt["computed_at"] != cache["computed_at"]   # a fresh render...
    assert rebuilt["per_ticker"] == cache["per_ticker"]     # ...same content
    r = _an_plan([_ask("W1", 90, shares=24)], rebuilt, wl)["results"]["W1"]
    assert r["status"] == "watchlist_mirror"


def test_r2_malformed_published_asks_are_never_trusted():
    cache = _gt_cache([])
    good = bv.wl_plan_cache([_ask("A", 80)], TODAY, 7)
    assert bv.wl_plan_known(good, TODAY, 7) is True
    for bad in (None, dict(good, asks=None), dict(good, asks=[{"ps": None}]),
                dict(good, asks=["x"]), dict(good, day="2026-10-15"),
                dict(good, epoch=6), {"day": good["day"], "epoch": 7}):
        assert bv.wl_plan_known(bad, TODAY, 7) is False, bad
        r = _an_plan([_ask("A", 80)], cache, bad)["results"]["A"]
        assert r["status"] != "watchlist_mirror" and r["caveat"] == bv.WL_MISSING_NOTE


def test_r2_defence_in_depth_real_allocation_beats_a_watchlist_mirror():
    """A ticker with a real emailed allocation (no logged buy) is sized by the
    allocation, never copied from the Watchlist re-run."""
    cache = _gt_cache([])
    allocs = {"W1": {"shares": 15, "dollars": 1500.0}}
    wl = bv.wl_plan_cache([_ask("W1", 90, shares=24)], TODAY, 7)
    r = _an_plan([_ask("W1", 90, shares=24)], cache, wl, allocs=allocs)["results"]["W1"]
    assert r["ps"]["shares"] == 15 and r["status"] == "budget_reserved"
    # control: with no allocation the same ask IS mirrored
    ok = _an_plan([_ask("W1", 90, shares=24)], cache, wl)["results"]["W1"]
    assert ok["status"] == "watchlist_mirror" and ok["ps"]["shares"] == 24


def test_r2c_a_watchlist_passthrough_never_unlocks_analysis():
    cache = _gt_cache([("GT1", 90, 40)])                  # Grow Today used it all
    wl_asks = [_ask("PT", 80, ps=None)]                   # Watchlist: no F-255 size
    wl = bv.wl_plan_cache(wl_asks, TODAY, 7)
    assert _plan(wl_asks, cache)["results"]["PT"]["status"] == "passthrough"
    assert wl["asks"][0]["ps"] is None
    r = _an_plan([_ask("PT", 80, shares=20)], cache, wl)["results"]["PT"]
    assert r["ps"] is None and r["status"] == "budget_exhausted"
    # control: with budget room the same ask IS sized (the branch is not a blanket no)
    room = _gt_cache([])
    wl_room = bv.wl_plan_cache([_ask("PT", 80, ps=None)], TODAY, 7)
    ok = _an_plan([_ask("PT", 80, shares=20)], room, wl_room)["results"]["PT"]
    assert ok["ps"]["shares"] == 20 and ok["status"] != "watchlist_mirror"


def test_r2c_wl_result_never_returns_the_unbudgeted_size():
    for st in ("passthrough", "grow_today", None, "weird"):
        r = bv._wl_result(_ps(20), {"status": st, "shares": 20}, False)
        assert r["ps"] is None and r["status"] == "budget_unknown", st
    for st in ("sized", "budget_partial", "budget_reserved"):   # controls
        assert bv._wl_result(_ps(20), {"status": st, "shares": 20}, False)["ps"] is not None


def test_r2_watchlist_clears_its_published_plan_before_any_early_stop():
    seg = _segment(_app_src(), 'elif page == "📋 Watchlist":', "# PAGE — THESIS")
    clear = seg.index('st.session_state["_wl_budget_plan_cache"] = None')
    import re as _re
    stop = _re.search(r"^\s+st\.stop\(\)$", seg, _re.M).start()
    assert clear < stop
    assert '_stamp_coord(["_wl_budget_plan_cache"])' in seg[clear:stop]
    # ...and the real publish comes after, so it overwrites the clear
    assert seg.index("_budget_view.wl_plan_cache(") > stop


# ======================================================= review round 3 fixes
# The Watchlist publishes its ASKS; Analysis re-runs the same joint plan against
# its OWN current basis / trades / allocations / Home cache.
INPUTS_8K = dict(INPUTS, net_capital=8000.0)               # budget 3,200 (was 4,000)


def _r3_world():
    cache = _gt_cache([("GT1", 90, 16)])                   # GT takes 1,600
    asks = [_ask("W1", 90, shares=20), _ask("W2", 80, shares=24)]
    return cache, asks, bv.wl_plan_cache(asks, TODAY, 7)


def test_r3a_a_basis_drop_with_unchanged_home_and_epoch_is_seen_by_analysis():
    cache, asks, wl = _r3_world()
    base = _plan(asks, cache)
    assert (_sh(base, "W1"), _sh(base, "W2")) == (20, 4)
    ok = _an_plan([_ask("W2", 80)], cache, wl)["results"]["W2"]     # control
    assert ok["ps"]["shares"] == 4 and ok["status"] == "watchlist_mirror"
    assert ok["note"] == bv.WL_MIRROR_NOTE
    # net capital 10,000 -> 8,000 (same Home content, same epoch): 3,200 budget
    wl_now = _plan(asks, cache, inputs=INPUTS_8K)
    assert (_sh(wl_now, "W1"), _sh(wl_now, "W2")) == (16, None)
    an = _an_plan([_ask("W2", 80)], cache, wl, inputs=INPUTS_8K)["results"]["W2"]
    assert an["ps"] is None and an["status"] == "budget_exhausted"
    assert an["note"] == hr.budget_note(hr.BUDGET_EXHAUSTED, {"is_pick": False})


def test_r3b_a_trade_logged_without_an_epoch_bump_is_seen_by_analysis():
    cache, asks, wl = _r3_world()
    early = _buy("X", 10, 100.0, ts="2026-10-16T13:00:00+00:00")   # $1,000 bought
    wl_now = _plan(asks, cache, trades=early)
    assert (_sh(wl_now, "W1"), _sh(wl_now, "W2")) == (14, None)
    an = _an_plan([_ask("W2", 80)], cache, wl, trades=early)["results"]["W2"]
    assert an["ps"] is None and an["status"] == "budget_exhausted"
    # control: no trade -> the Watchlist's 4 is mirrored
    assert _an_plan([_ask("W2", 80)], cache, wl)["results"]["W2"]["ps"]["shares"] == 4


def test_r3_a_grow_today_pick_resized_with_the_same_status_frees_dollars():
    old_cache = _gt_cache([("GT1", 90, 24)])               # GT1 SIZED 24 (2,400)
    new_cache = _gt_cache([("GT1", 90, 20)])               # GT1 SIZED 20 (2,000)
    assert (old_cache["per_ticker"]["GT1"]["status"]
            == new_cache["per_ticker"]["GT1"]["status"] == "sized")
    asks = [_ask("W1", 90, shares=18), _ask("W2", 80, shares=24)]
    wl = bv.wl_plan_cache(asks, TODAY, 7)
    before = _plan(asks, old_cache)                        # 1,600 left
    assert (_sh(before, "W1"), _sh(before, "W2")) == (16, None)
    after = _plan(asks, new_cache)                         # 2,000 left
    assert (_sh(after, "W1"), _sh(after, "W2")) == (18, 2)
    an = _an_plan([_ask("W1", 90), _ask("W2", 80)], new_cache, wl)["results"]
    assert (an["W1"]["ps"]["shares"], an["W2"]["ps"]["shares"]) == (18, 2)


def test_r3_analysis_and_watchlist_agree_on_every_watchlist_ticker():
    """Same inputs -> identical status / shares / dollars for every Watchlist
    ticker, whichever page computes it (the Watchlist's displayed sizes and the
    Analysis mirror come from the same `_plan` call shape)."""
    asks = [_ask("W1", 90, shares=20), _ask("W2", 80, shares=24),
            _ask("W3", 70, shares=24), _ask("PT", 60, ps=None)]
    early = _buy("X", 10, 100.0, ts="2026-10-16T13:00:00+00:00")
    scenarios = {
        "basic": dict(cache=_gt_cache([("GT1", 90, 16)])),
        "no picks": dict(cache=_gt_cache([])),
        "basis 8k": dict(cache=_gt_cache([("GT1", 90, 16)]), inputs=INPUTS_8K),
        "trade": dict(cache=_gt_cache([("GT1", 90, 16)]), trades=early),
        "email": dict(cache=_gt_cache([("GT1", 90, 16)]),
                      allocs={"W2": {"shares": 6, "dollars": 600.0}}),
        "other email": dict(cache=_gt_cache([("GT1", 90, 16)]),
                            allocs={"EML": {"shares": 5, "dollars": 500.0}}),
        "gt exhausts": dict(cache=_gt_cache([("GT1", 90, 40)])),
        "called": dict(cache=_gt_cache([]), inputs=dict(INPUTS, basis_state="called",
                                                        net_capital=-100.0)),
        "home missing": dict(cache=None),
        "stale cash": dict(cache=_gt_cache([]), inputs=dict(INPUTS, basis_state="stale")),
    }
    wl = bv.wl_plan_cache(asks, TODAY, 7)
    for name, kw in scenarios.items():
        wl_plan = _plan(asks, **kw)
        an = _an_plan([dict(a) for a in asks], kw["cache"], wl,
                      **{k: v for k, v in kw.items() if k != "cache"})
        for a in asks:
            tk = a["ticker"]
            w, n = wl_plan["results"][tk], an["results"][tk]
            wps, nps = w["ps"], n["ps"]
            assert (wps is None) == (nps is None), (name, tk, w, n)
            if wps is not None:
                assert (wps["shares"], wps["total_cost"]) == \
                    (nps["shares"], nps["total_cost"]), (name, tk)
            elif w["status"] not in ("passthrough",):
                assert w["status"] == n["status"], (name, tk, w["status"], n["status"])
    # the harness is not vacuous: at least one scenario withholds, one sizes
    assert _plan(asks, cache=_gt_cache([]))["results"]["W1"]["ps"] is not None
    assert _plan(asks, cache=_gt_cache([("GT1", 90, 40)]))["results"]["W1"]["ps"] is None


def test_r3_analysis_reruns_with_its_own_inputs_not_stored_results():
    src = (_ROOT / "stock_analyzer" / "budget_view.py").read_text(encoding="utf-8")
    seg = src[src.index("def _plan("):src.index("def grow_sector_tickers")]
    rerun = seg[seg.index("wl_res = _plan("):seg.index("if wl_res[\"state\"]")]
    # the re-run is fed THIS call's inputs, in the Watchlist's own call shape
    for needle in ("inputs, trades_df, allocs, cache, today, now",
                   '_norm_asks(wl_cache["asks"])', "False)"):
        assert needle in rerun, needle
    assert 'wl_cache["results"]' not in src


def test_r3_stale_coord_stamp_of_a_none_cache_reads_absent_not_stale():
    """The Watchlist page clears + stamps its cache with None at page start:
    apply_stamps must drop (not record) a None, so it can never read STALE."""
    from stock_analyzer import coord_freshness as cf
    k = "_wl_budget_plan_cache"
    stamps = cf.apply_stamps({k: 3}, {k: None}, [k], 4)
    assert k not in stamps
    assert cf.classify(stamps, 5, k) == cf.ABSENT
    ok = cf.apply_stamps({}, {k: {"day": "x", "epoch": 4, "asks": []}}, [k], 4)
    assert ok[k] == 4 and cf.classify(ok, 5, k) == cf.STALE   # control: real value ages


def test_r3_watchlist_publishes_asks_and_helper_passes_cache_and_epoch():
    seg = _segment(_app_src(), 'elif page == "📋 Watchlist":', "# PAGE — THESIS")
    assert "_budget_view.wl_plan_cache(\n        _wl_budget_asks, _today_et(), _coord_epoch())" in seg
    assert "wl_plan_cache(\n        _wl_bplan" not in seg


# ---- review round 3 non-blocking: Analysis's status-line label
def test_wl_counted_true_only_when_the_watchlist_asks_were_charged():
    cache, asks, wl = _r3_world()
    counted = _an_plan([_ask("X", 70, shares=1)], cache, wl)
    assert counted["wl_counted"] is True
    # Watchlist dollars are inside the "list" figure the label names
    assert counted["summary"]["used_list"] >= 1600.0 + 2000.0 + 400.0
    # controls: no Watchlist cache, a stale-epoch cache, and an empty-asks cache
    assert _an_plan([_ask("X", 70, shares=1)], cache, None)["wl_counted"] is False
    assert _an_plan([_ask("X", 70, shares=1)], cache, wl, epoch=8)["wl_counted"] is False
    empty = bv.wl_plan_cache([], TODAY, 7)
    assert _an_plan([_ask("X", 70, shares=1)], cache, empty)["wl_counted"] is False


def test_analysis_status_line_label_reads_the_wl_counted_flag():
    seg = _app_src()
    i = seg.index('_an_bplan["summary"], mask=')
    window = seg[i:i + 300]
    assert '"Grow Today + 📋 Watchlist" if _an_bplan.get("wl_counted")' in window
    assert 'else "Grow Today"' in window
