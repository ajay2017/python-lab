"""Chunk 6 of docs/plans/shared-entry-check.md: 📈 Analysis offers a share count
only when the shared new-entry check lets it.

The decision (`entry_eligibility.analysis_entry_hold` / `analysis_entry_decision`)
is pure and tested at every branch; app.py is render-only wiring that no test
imports, so its wiring is pinned structurally (text/AST slices of the Analysis
page).  Every "no size" test has a firing control (an eligible twin).
"""
import datetime
import pathlib
import re

import pytest

from stock_analyzer import budget_view as bv
from stock_analyzer import entry_eligibility as ee
from stock_analyzer.constants import EARNINGS_IMMINENT_DAYS
from stock_analyzer.watchlist_advisor import build_watchlist_recommendation

pytestmark = pytest.mark.fast

_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = (_ROOT / "app.py").read_text(encoding="utf-8")
TODAY = datetime.date(2026, 10, 16)

FLAT = {"tone": "flat", "sp500_pct": -0.1, "available": True, "source": "live", "as_of": None}
BEAR_HOME = {"tone": "bear", "sp500_pct": -0.8, "available": True, "source": "home",
             "as_of": datetime.datetime(2026, 10, 16, 9, 42)}
BEAR_LIVE = {"tone": "bear", "sp500_pct": -0.8, "available": True, "source": "live", "as_of": None}
FLAT_HOME = {"tone": "flat", "sp500_pct": -0.1, "available": True, "source": "home",
             "as_of": None}
UNAVAIL = {"tone": None, "sp500_pct": None, "available": False, "source": None, "as_of": None}
CLEAR_MAP = {"sectors": set(), "reasons": {}}
NEG_NEWS = [{"title": "Probe widens", "compound": -0.5}]
GOOD_NEWS = [{"title": "Fine quarter", "compound": 0.3}]


def _run(tone=FLAT, macro=CLEAR_MAP):
    return {"tone_state": tone, "macro_map": macro}


def _decide(*, basis="equity", label="Strong Buy", held=False, run_ctx="default",
            bundle="default", sector="Technology", classified=True,
            news="default", earnings=None):
    return ee.analysis_entry_decision(
        bundle={} if bundle == "default" else bundle, basis=basis, label=label, held=held,
        run_ctx=_run() if run_ctx == "default" else run_ctx, sector=sector,
        classified=classified, news_items=GOOD_NEWS if news == "default" else news,
        earnings=earnings, today=TODAY)


def _iso(days):
    return (TODAY + datetime.timedelta(days=days)).isoformat()


# ═════════════════════════ the pure decision ═══════════════════════════════════
def test_not_held_buy_eligible_gets_a_size():
    d = _decide()
    assert d["offer_size"] is True and d["hold"] is None and d["holds"] == []
    assert d["entry_checked"] is True and d["reason_if_no_size"] is None


def test_not_held_etf_buy_also_runs_the_check():
    assert _decide(basis="etf", label="Buy")["offer_size"] is True
    d = _decide(basis="etf", label="Buy", run_ctx=_run(BEAR_LIVE))
    assert d["offer_size"] is False and d["hold"]["check"] == "tone"


def test_bear_day_withholds_the_size():
    d = _decide(run_ctx=_run(BEAR_HOME))
    assert d["offer_size"] is False
    assert d["hold"]["check"] == "tone"
    assert "risk-off" in d["reason_if_no_size"]
    assert d["entry_checked"] is True


def test_earnings_day_boundary():
    # D7: the full check includes earnings. <= EARNINGS_IMMINENT_DAYS holds, +1 sizes.
    on = _decide(earnings=_iso(EARNINGS_IMMINENT_DAYS))
    assert on["offer_size"] is False and on["hold"]["check"] == "earnings"
    today = _decide(earnings=_iso(0))
    assert today["offer_size"] is False
    off = _decide(earnings=_iso(EARNINGS_IMMINENT_DAYS + 1))
    assert off["offer_size"] is True and off["holds"] == []


def test_past_earnings_date_does_not_hold():
    assert _decide(earnings=_iso(-3))["offer_size"] is True


def test_macro_hold():
    m = {"sectors": {"Technology"}, "reasons": {"Technology": "CPI (2026-10-18) — 2d away"}}
    d = _decide(run_ctx=_run(macro=m))
    assert d["offer_size"] is False and d["hold"]["check"] == "macro"
    # control: a different sector is not held
    assert _decide(run_ctx=_run(macro=m), sector="Energy")["offer_size"] is True


def test_negative_news_hold():
    d = _decide(news=NEG_NEWS)
    assert d["offer_size"] is False and d["hold"]["check"] == "news"
    assert _decide(news=GOOD_NEWS)["offer_size"] is True


def test_stale_data_hold():
    d = _decide(bundle={"stale_as_of": "2026-10-10"})
    assert d["offer_size"] is False and d["hold"]["check"] == "stale"
    assert _decide(bundle={})["offer_size"] is True


def test_binding_hold_is_the_first_in_check_order():
    d = _decide(run_ctx=_run(BEAR_LIVE), news=NEG_NEWS, bundle={"stale_as_of": "2026-10-10"})
    assert [h["check"] for h in d["holds"]] == ["stale", "tone", "news"]
    assert d["hold"]["check"] == "stale"


def test_crash_fails_closed():
    # A run context that raises inside the check -> no size, never a size.
    d = _decide(run_ctx=None)
    assert d["offer_size"] is False and d["entry_checked"] is True
    assert d["reason_if_no_size"] == "entry check couldn't run"
    assert d["hold"] is None
    # control: the same inputs with a real context are eligible
    assert _decide()["offer_size"] is True


def test_missing_entry_result_fails_closed():
    d = ee.analysis_entry_hold(basis="equity", label="Buy", held=False, entry=None)
    assert d["offer_size"] is False and d["reason_if_no_size"] == "entry check couldn't run"
    d2 = ee.analysis_entry_hold(basis="equity", label="Buy", held=False,
                                entry={"holds": [], "unchecked": []}, check_crashed=True)
    assert d2["offer_size"] is False


@pytest.mark.parametrize("label", ["Hold", "Sell", "Strong Sell"])
@pytest.mark.parametrize("held", [False, True])
def test_non_buy_verdict_never_gets_a_size(label, held):
    d = _decide(label=label, held=held)
    assert d["offer_size"] is False
    assert d["reason_if_no_size"] == f"no size on a {label} verdict"
    assert d["entry_checked"] is False   # no entry check needed (S-A)


def test_held_buy_size_is_unchanged_even_on_a_bear_day():
    # The known D5 / S-D gap: held-name adds are not entry-checked in this chunk.
    d = _decide(held=True, run_ctx=_run(BEAR_HOME), news=NEG_NEWS)
    assert d["offer_size"] is True and d["entry_checked"] is False and d["holds"] == []


def test_withheld_verdict_never_gets_a_size():
    d = _decide(basis="withheld", label=None)
    assert d["offer_size"] is False and d["reason_if_no_size"] == "verdict withheld"
    assert _decide(basis="weird")["offer_size"] is False


def test_unavailable_tone_fails_open_with_a_note():
    d = _decide(run_ctx=_run(UNAVAIL))
    assert d["offer_size"] is True
    assert any(u["check"] == "tone" for u in d["unchecked"])


def test_unavailable_macro_fails_open_with_a_note():
    d = _decide(run_ctx=_run(macro=None))
    assert d["offer_size"] is True
    assert any(u["check"] == "macro" for u in d["unchecked"])


def test_needs_check_only_for_not_held_real_buys():
    assert ee.analysis_needs_check("equity", "Buy", False) is True
    assert ee.analysis_needs_check("equity", "Buy", True) is False
    assert ee.analysis_needs_check("equity", "Hold", False) is False
    assert ee.analysis_needs_check("withheld", None, False) is False


# ═════════════════════════ tiles + banner ══════════════════════════════════════
def test_gate_tiles_states():
    tiles = {t["label"]: t for t in ee.analysis_gate_tiles(
        _decide(run_ctx=_run(BEAR_LIVE, macro=None), news=[]))}
    assert list(tiles) == ["Market tone", "Macro", "News", "Data fresh"]
    assert tiles["Market tone"]["state"] is False
    assert tiles["Macro"]["state"] is None and tiles["Macro"]["detail"] == "not checked"
    assert tiles["News"]["state"] is None      # no headlines to check
    assert tiles["Data fresh"]["state"] is True


def test_gate_tiles_empty_when_the_check_did_not_run():
    assert ee.analysis_gate_tiles(_decide(held=True)) == []
    assert ee.analysis_gate_tiles(_decide(label="Sell")) == []
    assert ee.analysis_gate_tiles(None) == []


def test_gate_tiles_after_a_crash_are_all_unchecked():
    tiles = ee.analysis_gate_tiles(_decide(run_ctx=None))
    assert len(tiles) == 4 and all(t["state"] is None for t in tiles)


def test_banner_says_home_only_when_the_tone_came_from_home():
    home = ee.analysis_hold_banner(_decide(run_ctx=_run(BEAR_HOME)), BEAR_HOME,
                                   ee.HOME_TODAY_OK)
    assert home["title"].startswith("Not an entry today — deferred: market risk-off")
    assert "📋 Watchlist and 🏠 Home apply the same check." in home["body"]
    assert "Same call" not in home["body"]
    assert "no size is offered today" in home["body"]
    live = ee.analysis_hold_banner(_decide(run_ctx=_run(BEAR_LIVE)), BEAR_LIVE,
                                   ee.HOME_TODAY_TONE_FAILED)
    assert "🏠 Home apply" not in live["body"]
    assert "📋 Watchlist applies the same check." in live["body"]
    # Home-sourced tone but the binding hold is NOT the tone -> no Home claim
    nt = ee.analysis_hold_banner(_decide(run_ctx=_run(FLAT_HOME), news=NEG_NEWS),
                                 FLAT_HOME, ee.HOME_TODAY_OK)
    assert "🏠 Home" not in nt["body"] and "Watchlist applies the same check" in nt["body"]
    st_ = ee.analysis_hold_banner(
        _decide(run_ctx=_run(BEAR_HOME), bundle={"stale_as_of": "2026-10-10"}),
        BEAR_HOME, ee.HOME_TODAY_OK)   # binding = stale, tone is Home's
    assert st_["title"].split("— ")[1].startswith("held: score uses cached")
    assert "🏠 Home" not in st_["body"]
    assert "treating today as flat" in live["source"]


def test_banner_for_a_non_tone_hold_and_for_a_crash_and_when_nothing_held():
    b = ee.analysis_hold_banner(_decide(news=NEG_NEWS), FLAT, ee.HOME_NOT_TODAY)
    assert b["title"].startswith("Not an entry today — held: negative news")
    assert b["source"] is None
    c = ee.analysis_hold_banner(_decide(run_ctx=None))
    assert "couldn't run" in c["title"]
    assert ee.analysis_hold_banner(_decide()) is None
    assert ee.analysis_hold_banner(_decide(label="Sell")) is None
    assert ee.analysis_hold_banner(_decide(held=True, run_ctx=_run(BEAR_HOME))) is None


# ═════════════════ Analysis <-> Watchlist agreement ════════════════════════════
def _wl_data(**ov):
    d = {
        "total": 70.0, "rec": {"label": "Buy"}, "current_price": 100.0,
        "entry_lo": 95.0, "entry_hi": 100.0, "stop": 90.0,
        "targets": {"base": 130.0}, "earnings": None,
    }
    d.update(ov)
    return d


@pytest.mark.parametrize("name,run_ctx,news,bundle_ov", [
    ("bear", _run(BEAR_HOME), GOOD_NEWS, {}),
    ("bear-live", _run(BEAR_LIVE), GOOD_NEWS, {}),
    ("macro", _run(macro={"sectors": {"Technology"},
                          "reasons": {"Technology": "CPI — 2d away"}}), GOOD_NEWS, {}),
    ("news", _run(), NEG_NEWS, {}),
    ("stale", _run(), GOOD_NEWS, {"stale_as_of": "2026-10-10"}),
    ("fund-age", _run(), GOOD_NEWS, {"fund_cache_age_days": 9}),
])
def test_a_ticker_the_watchlist_holds_is_held_on_analysis_with_the_same_reason(
        name, run_ctx, news, bundle_ov):
    data = _wl_data(**bundle_ov)
    tctx = ee.watchlist_ticker_ctx(run_ctx, sector="Technology", classified=True,
                                   news_items=news)
    wl = build_watchlist_recommendation("XYZ", data, entry_ctx=tctx)
    assert wl["action"] == "ENTRY_HELD", name
    an = _decide(bundle=data, run_ctx=run_ctx, news=news)
    assert an["offer_size"] is False
    assert an["hold"]["check"] == wl["holds"][0]["check"]
    assert an["hold"]["reason"] == wl["holds"][0]["reason"]


def test_a_ticker_the_watchlist_enters_is_sized_on_analysis():
    tctx = ee.watchlist_ticker_ctx(_run(), sector="Technology", classified=True,
                                   news_items=GOOD_NEWS)
    wl = build_watchlist_recommendation("XYZ", _wl_data(), entry_ctx=tctx)
    assert wl["action"] == "ENTER_NOW"
    assert _decide(bundle=_wl_data())["offer_size"] is True


def test_watchlist_held_name_is_never_a_budget_ask_so_analysis_cannot_mirror_it():
    tctx = ee.watchlist_ticker_ctx(_run(BEAR_HOME), sector="Technology", classified=True,
                                   news_items=GOOD_NEWS)
    held = build_watchlist_recommendation("XYZ", _wl_data(), entry_ctx=tctx)
    assert held["action"] == "ENTRY_HELD"
    asks = bv.watchlist_asks([held], (), (), 10000.0, lambda p, s: {"shares": 5})
    assert asks == []   # no ask -> not in the cache Analysis mirrors from


# ═════════════════ app.py wiring pins (render-only; no test imports app) ═══════
def _analysis_src():
    a = APP.index('elif page == "📈 Analysis":')
    b = APP.index('elif page == "📋 Watchlist":')
    return APP[a:b]


def test_asks_skip_names_the_decision_does_not_size():
    src = _analysis_src()
    loop = src[src.index("for _at, _ar in results.items():"):]
    loop = loop[:loop.index("_an_bplan = _budget_downstream_plan(")]
    assert 'if not _an_entry[_at]["offer_size"]:' in loop
    assert loop.index('_an_entry[_at]["offer_size"]') < loop.index("_an_asks.append(")
    # the skip leaves the loop body before the ask is built
    skip = loop[loop.index('if not _an_entry[_at]["offer_size"]:'):loop.index("_ap = _ar[")]
    assert "continue" in skip


def test_sizing_and_budget_gate_read_the_same_decision():
    src = _analysis_src()
    sizing = src[src.index("def _an_sizing(_t):"):src.index("def _an_offers_size")]
    assert '_d = _an_entry.get(_t)' in sizing and 'not _d["offer_size"]' in sizing
    assert "return None, None, None" in sizing
    offers = src[src.index("def _an_offers_size"):]
    offers = offers[:offers.index("if any(_an_offers_size")]
    assert '_an_entry[_t]["offer_size"]' in offers


def test_scorecard_shares_column_reads_the_decision_through_the_sizing():
    src = _analysis_src()
    sc = src[src.index('ps = _an_sizing(ticker)[0]   # budget-adjusted'):]
    sc = sc[:sc.index("summary_df = pd.DataFrame(rows)")]
    assert "_an_entry[ticker]" in sc
    assert "analysis_shares_label(_an_entry[ticker])" in sc
    # the only way to a "suggested" count is the (decision-gated) ps
    assert sc.count("{ps['shares']} suggested") == 1


def test_export_trade_line_is_gated_and_says_why():
    src = _analysis_src()
    exp = src[src.index("# Analysis summary"):]
    assert "ps = _an_sizing(ticker)[0]" in exp
    trade = exp[exp.index('(f"**Trade**: Buy {ps'):]
    assert "if ps and rr_v and rr_v > 0" in trade[:400]
    assert "no size today" in exp
    assert '_ap_entry["entry_checked"] and not _ap_entry["offer_size"]' in exp


def test_hold_verdict_add_prefill_reads_the_gated_size():
    src = _analysis_src()
    hold = src[src.index("elif _sa_is_hold:\n                    # Hold — surface both sides"):]
    hold = hold[:hold.index("# Buy signal")]
    assert 'shares=(ps["shares"] if ps else None)' in hold
    # ps comes only from _an_sizing, never from a local position_sizing() call
    assert "position_sizing(" not in hold
    detailed = src[src.index('st.subheader("Detailed Analysis")'):src.index("# Analysis summary")]
    assert "position_sizing(" not in detailed


def test_per_run_ctx_uses_the_watchlists_helpers_in_separate_try_blocks():
    src = _analysis_src()
    blk = src[src.index("# ── Shared new-entry check: may this page offer"):
              src.index("# Daily new-position budget")]
    for fn in ("home_tone_state", "resolve_tone_state", "pick_macro_events",
               "watchlist_run_ctx", "analysis_entry_decision", "analysis_notes"):
        assert f"_entry_elig.{fn}(" in blk, fn
    assert blk.count("        try:\n            _an_home_syn") == 1
    assert "_an_macro_events = None\n        try:" in blk   # macro in its own try
    assert "curate_news_items({_t: _r})" in blk
    assert "sector_fit.sector_gate_context(" in blk
    assert 'f"_wl_macro_cal_{_today_et()}"' in blk        # one calendar build shared


def test_banner_tiles_and_button_wiring():
    src = _analysis_src()
    assert "_entry_elig.analysis_hold_banner(" in src
    assert "_entry_elig.analysis_gate_tiles(" in src
    assert '_safe_html(_da_banner[\'title\'])' in src
    assert "_safe_html(_gx['detail'])" in src
    # the 📒 Buy button must not render when held back (OP-07), and a normal
    # Buy still logs exactly as before
    buy = src[src.index("# Buy signal — primary action is BUY"):]
    buy = buy[:buy.index("with _ap_c3:")]
    assert "if _da_held_back:" in buy
    assert buy.index("if _da_held_back:") < buy.index("_render_trade_button(")
    assert buy.index("else:", buy.index("if _da_held_back:")) < buy.index('suggested_action="BUY"')
    assert 'trigger="RECOMMENDATION"' in buy and "followed_intent=_ap_followed_buy" in buy
    assert '"MANUAL" if _da_held_back' not in src
    # no markdown bold inside the raw-HTML banner
    b = src[src.index("_da_banner = _entry_elig.analysis_hold_banner("):]
    b = b[:b.index("c1, c2, c3, c4 = st.columns(4)")]
    assert "**" not in b


def test_decision_carries_an_explicit_crashed_key_and_tiles_use_it():
    assert _decide(run_ctx=None)["crashed"] is True
    assert _decide()["crashed"] is False and _decide(label="Sell")["crashed"] is False
    # a decision whose reason text merely matches is NOT a crash
    d = _decide()
    d.update(entry_checked=True, offer_size=False, reason_if_no_size="entry check couldn't run")
    assert all(t["state"] is True for t in ee.analysis_gate_tiles(d))
    # and a crashed flag with different text is
    d2 = _decide(run_ctx=None)
    d2["reason_if_no_size"] = "x"
    assert all(t["state"] is None for t in ee.analysis_gate_tiles(d2))


def test_shares_label_and_fail_open_notes():
    assert ee.analysis_shares_label(_decide(run_ctx=_run(BEAR_LIVE))) == "— (deferred)"
    assert ee.analysis_shares_label(_decide(news=NEG_NEWS)) == "— (held)"
    assert ee.analysis_shares_label(_decide(run_ctx=None)) == "— (held)"
    n = ee.analysis_notes(UNAVAIL, None)
    assert len(n) == 2 and all("sizes are shown" in x for x in n)
    assert all("entries are shown" not in x for x in n)
    assert ee.analysis_notes(FLAT, CLEAR_MAP) == []


def test_one_normalized_held_test_in_app():
    src = _analysis_src()
    assert 'ticker in _sa_port["Ticker"].values' not in src
    assert 'ticker in _sc_port["Ticker"].values' not in src
    assert src.count("str(ticker).strip().upper() in _an_held_set") == 2


def test_strip_level_banner_exists_before_the_scorecard():
    src = _analysis_src()
    i = src.index("if len(results) == 1:" + chr(10) + "        _an_strip_t")
    assert "_entry_elig.analysis_hold_banner(" in src[i:i + 400]
    assert "_safe_html(_an_strip_b['title'])" in src[i:i + 1200]
    # the render condition itself (review: `if False and …` used to pass this pin)
    assert ("if _an_strip_b is not None:" + chr(10) + "            st.markdown(") in src[i:i + 1200]
    assert i < src.index('st.subheader("Summary Scorecard")')
    # and after the single-ticker verdict strip
    assert i > src.index("_sv_ticker, _sv_r = next(iter(results.items()))")
