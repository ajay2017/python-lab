"""DeferSizeBlind C1-C3 (docs/plans/defer-size-blind.md, spec defer-size-blind-spec.md):
`act_sell_cover` reads each Act sell as the FEWEST shares it commits to selling, and the three
standing surfaces (🔧 Tune-up, 🔗 Risk Analysis, ⚖️ Rebalancer) defer only to a sell that
COVERS their own trim. A 1-share macro trim on a ~30% holding no longer hides that name's
ceiling trim.

Pins: the full `cover_state` table + its invariant; `item_covers` for every kind (incl. invalid
counts); combining; set consistency with the pre-change `brief_sell_tickers` / `view_sell_tickers`
(kept below as test oracles); `verified_sells` == key set of `verified_sell_cover`; the defect
end to end; fail directions (None map, unknown size, legacy shapes); no mutation of the
memoized Brief; text rules; history pins; fractional positions.
"""
import copy
import itertools
import math
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import act_sell_cover as asc
from stock_analyzer import act_sell_mention as asm
from stock_analyzer import coord_freshness as cf
from stock_analyzer import held_trim_owner as hto
from stock_analyzer import rebalancer as reb
from stock_analyzer import trim_sizing as ts
from stock_analyzer.constants import SINGLE_NAME_CEILING
from stock_analyzer.daily_briefing import _portfolio_tuneup
from stock_analyzer.decision_bucket import EXIT_KINDS, _ACT_REVIEW_TYPES
from tests.test_rebalancer import _drift_row

pytestmark = pytest.mark.fast

EXIT = {"exit": True, "shares": None}


def cov(n):
    return {"exit": False, "shares": n}


def _act(kind, ticker="AAA", **kw):
    return {"kind": kind, "ticker": ticker, "directive": "x", **kw}


def _rev(atype, ticker="AAA", **action):
    return {"icon": "📅", "ticker": ticker, "action": {"type": atype, **action}}


# -- the cover_state table -----------------------------------------------------------------

@pytest.mark.parametrize("entry, need, want", [
    (None, 5, "show"), (None, 0, "show"), (None, None, "show"),                # not sold
    (EXIT, 5, "covered"), (EXIT, 0, "covered"), (EXIT, None, "covered"),       # an exit
    (cov(None), 0, "covered"), (cov(3), 0, "covered"),                        # need 0 (Q8)
    (cov(5), 5, "covered"), (cov(6), 5, "covered"),                           # shares >= need
    (cov(4), 5, "partial"), (cov(1), 5, "partial"),                           # shares < need
    (cov(None), 5, "unknown"), (cov(5), None, "unknown"), (cov(None), None, "unknown"),
])
def test_cover_state_table(entry, need, want):
    assert asc.cover_state(entry, need) == want


def test_cover_state_invariant_covered_means_exit_or_need_zero_or_enough_shares():
    entries = [None, EXIT, cov(None)] + [cov(n) for n in (1, 2, 5, 9)]
    needs = [None, 0, 1, 2, 5, 9, 10]
    for entry, need in itertools.product(entries, needs):
        state = asc.cover_state(entry, need)
        assert state in ("show", "covered", "partial", "unknown")
        if state == "covered":
            assert entry["exit"] is True or need == 0 or entry["shares"] >= need, (entry, need)
        if entry is None:
            assert state == "show"
        if state == "partial":
            assert entry["shares"] < need
        if state == "unknown":
            assert entry["exit"] is False and (entry["shares"] is None or need is None)


@pytest.mark.parametrize("bad_need", [-1, 1.5, True, "5", float("nan"), float("inf")])
def test_an_invalid_need_reads_unknown_never_covered_by_a_non_exit_sell(bad_need):
    assert asc.cover_state(cov(99), bad_need) == "unknown"
    assert asc.cover_state(EXIT, bad_need) == "covered"


def test_need_accepts_a_whole_float_and_numpy_style_ints():
    assert asc.cover_state(cov(5), 5.0) == "covered"
    assert asc.cover_state(cov(5), 6.0) == "partial"
    import numpy as np
    assert asc.cover_state(cov(np.int64(5)), np.int64(5)) == "covered"


def test_a_malformed_entry_reads_sold_with_unknown_size():
    assert asc.cover_state(True, 5) == "unknown"
    assert asc.cover_state({}, 5) == "unknown"
    assert asc.cover_state({"exit": "yes"}, 5) == "unknown"          # only `is True` is an exit
    assert asc.cover_state({"exit": "yes", "shares": 9}, 5) == "covered"   # by its count alone


# -- valid counts ---------------------------------------------------------------------------

@pytest.mark.parametrize("v", [0, -1, 1.5, True, False, "2", "", None, float("nan"), float("inf"), [], {}])
def test_an_invalid_trim_shares_reads_unknown(v):
    assert asc.valid_shares(v) is None
    for item in (_act("deterioration_trim", trim_shares=v),
                 _rev("TRIM_TO_TARGET", trim_shares=v)):
        (cov_entry,) = asc.item_covers(item).values()
        assert cov_entry == {"exit": False, "shares": None}
    mt = _act("sell_signal", min_trim={"trim_shares": v})
    assert asc.item_covers(mt) == {"AAA": {"exit": False, "shares": None}}


@pytest.mark.parametrize("v, want", [(1, 1), (7, 7), (3.0, 3), (2.0, 2)])
def test_a_valid_trim_shares_is_a_whole_number(v, want):
    assert asc.valid_shares(v) == want and isinstance(asc.valid_shares(v), int)


# -- item_covers per kind -------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(k for k in EXIT_KINDS if k != "sell_signal")
                         + ["stop_breach", "deterioration_exit"])
def test_exit_kinds_are_exit_covers(kind):
    assert asc.item_covers(_act(kind, "aaa")) == {"AAA": {"exit": True, "shares": None}}


def test_sell_signal_covers_only_its_min_trim_never_an_exit():
    assert asc.item_covers(_act("sell_signal", min_trim={"trim_shares": 4, "trim_dollars": 9})) == {
        "AAA": cov(4)}
    assert asc.item_covers(_act("sell_signal")) == {"AAA": cov(None)}            # owner Q1
    assert asc.item_covers(_act("sell_signal", min_trim="junk")) == {"AAA": cov(None)}


def test_deterioration_trim_covers_its_structured_count():
    assert asc.item_covers(_act("deterioration_trim", trim_shares=2)) == {"AAA": cov(2)}
    assert asc.item_covers(_act("deterioration_trim")) == {"AAA": cov(None)}      # legacy shape
    assert asc.item_covers(_act("deterioration_trim", trim_shares=0)) == {"AAA": cov(None)}


def test_risk_off_derisk_is_never_a_full_cover_even_with_a_count():
    # owner Q2: its smallest sale is zero ("or tighten the stop")
    assert asc.item_covers(_act("risk_off_derisk", trim_shares=50)) == {"AAA": cov(None)}
    assert asc.cover_state(asc.item_covers(_act("risk_off_derisk"))["AAA"], 3) == "unknown"
    assert asc.cover_state(asc.item_covers(_act("risk_off_derisk"))["AAA"], 0) == "covered"


@pytest.mark.parametrize("atype", sorted(_ACT_REVIEW_TYPES))
def test_review_trims_cover_their_action_count(atype):
    assert asc.item_covers(_rev(atype, "aaa", trim_shares=3)) == {"AAA": cov(3)}
    assert asc.item_covers(_rev(atype)) == {"AAA": cov(None)}


def test_a_macro_trim_attributes_its_shares_to_trim_ticker():
    macro = {"icon": "🌐", "ticker": None, "action": {
        "type": "PROTECTIVE_TRIM", "trim_ticker": "ccc", "trim_shares": 1}}
    assert asc.item_covers(macro) == {"CCC": cov(1)}
    # a card ticker that differs from trim_ticker is also named, with an unknown size
    both = {"ticker": "III", "action": {"type": "PROTECTIVE_TRIM", "trim_ticker": "jjj",
                                          "trim_shares": 4}}
    assert asc.item_covers(both) == {"JJJ": cov(4), "III": cov(None)}
    same = {"ticker": "KKK", "action": {"type": "TRIM_TO_TARGET", "trim_ticker": "kkk",
                                          "trim_shares": 4}}
    assert asc.item_covers(same) == {"KKK": cov(4)}


@pytest.mark.parametrize("item", [
    _act("risk"), _act("critical_news"), _act("premortem_triggered"), _act("macro"),
    _act("macro_event"), _act("macro_trim"), _act("sector_concentration"), _act(""),
    _rev("WATCH"), _rev("HOLD"), _rev("TIGHTEN_ONLY"), _rev("DETERIORATION_WATCH"), _rev(""),
    None, "AAA", 5, [], {}, {"kind": "stop_breach"}, {"kind": "stop_breach", "ticker": ""},
    {"kind": "stop_breach", "ticker": None}, {"action": "TRIM", "ticker": "AAA"},
    {"ticker": None, "action": {"type": "PROTECTIVE_TRIM"}},
])
def test_non_sells_give_no_cover(item):
    assert asc.item_covers(item) == {}


# -- combining ------------------------------------------------------------------------------

def test_any_exit_wins_over_every_count():
    s = asc.sell_cover_summary([_act("deterioration_trim", trim_shares=9)],
                               [_rev("TRIM_TO_TARGET", trim_shares=5), _act_as_review_exit()])
    assert s == {"AAA": EXIT}


def _act_as_review_exit():
    return _act("stop_breach")      # an Act item passed in the review list still reads as an exit


def test_the_largest_count_wins_and_counts_do_not_add():
    s = asc.sell_cover_summary([_act("deterioration_trim", trim_shares=2)],
                               [_rev("TRIM_TO_TARGET", trim_shares=5), _rev("PROTECTIVE_TRIM", trim_shares=3)])
    assert s == {"AAA": cov(5)}                       # U6: "at least N" sells do not add (not 10)


def test_a_sized_plus_an_unsized_sell_is_the_sized_count():
    s = asc.sell_cover_summary([_act("risk_off_derisk")], [_rev("TRIM_TO_TARGET", trim_shares=2)])
    assert s == {"AAA": cov(2)}
    s = asc.sell_cover_summary([_act("sell_signal")], [_rev("TRIM_TO_TARGET", trim_shares=2)])
    assert s == {"AAA": cov(2)}


def test_unsized_only_is_unknown_and_tickers_are_uppercased_and_separate():
    s = asc.sell_cover_summary([_act("risk_off_derisk", "aaa"), _act("stop_breach", "bbb")], None)
    assert s == {"AAA": cov(None), "BBB": EXIT}


def test_summary_inputs_none_and_garbage():
    assert asc.sell_cover_summary(None, None) == {}
    assert asc.sell_cover_summary([None, "x", 3], [None, {}]) == {}


# -- set consistency with the pre-change implementation (kept here as the oracle) ------------

def _old_brief_sell_tickers(act_today, review):
    """The exact pre-DeferSizeBlind `held_trim_owner.brief_sell_tickers`."""
    out = set()
    for it in list(act_today or []) + list(review or []):
        if not isinstance(it, dict):
            continue
        kind = it.get("kind")
        if kind:
            if kind in EXIT_KINDS or kind in hto._EXTRA_SELL_KINDS:
                t = hto._norm(it.get("ticker"))
                if t and hto.brief_sell_names_ticker(it, t):
                    out.add(t)
            continue
        action = it.get("action")
        if isinstance(action, dict) and action.get("type") in _ACT_REVIEW_TYPES:
            for t in (hto._norm(it.get("ticker")), hto._norm(action.get("trim_ticker"))):
                if t and hto.brief_sell_names_ticker(it, t):
                    out.add(t)
    return out


def _old_view_sell_tickers(view):
    if not isinstance(view, dict) or view.get("state") == "offline":
        return None
    active = view.get("active")
    return _old_brief_sell_tickers(active if isinstance(active, list) else [], [])


def _fixture_items():
    kinds = ["stop_breach", "sell_signal", "deterioration_exit", "deterioration_trim",
             "risk_off_derisk", "risk", "critical_news", "premortem_triggered", "macro", ""]
    acts = [_act(k, t) for k in kinds for t in ("AAA", "bbb", "", None)]
    acts += [_act("sell_signal", "AAA", min_trim={"trim_shares": 2}),
             _act("deterioration_trim", "AAA", trim_shares=3)]
    revs = [_rev(a, t, **kw) for a in sorted(_ACT_REVIEW_TYPES) + ["WATCH", "HOLD", "TIGHTEN_ONLY", ""]
            for t in ("AAA", "ccc", None, "") for kw in ({}, {"trim_ticker": "ddd"}, {"trim_ticker": "AAA", "trim_shares": 1},
                                                              {"trim_ticker": ""})]
    revs += [None, "x", {}, {"ticker": "AAA", "action": "TRIM"}]
    return acts, revs


def test_brief_sell_tickers_equals_the_old_implementation_over_the_fixtures():
    acts, revs = _fixture_items()
    for a, r in ((acts, revs), (acts, []), ([], revs), (None, None), (acts[:7], revs[:9])):
        assert hto.brief_sell_tickers(a, r) == _old_brief_sell_tickers(a, r)
        assert set(asc.sell_cover_summary(a, r)) == _old_brief_sell_tickers(a, r)
    # every single item agrees with brief_sell_names_ticker, ticker by ticker
    for it in acts + revs:
        names = {t for t in ("AAA", "BBB", "CCC", "DDD") if hto.brief_sell_names_ticker(it, t)}
        assert set(asc.item_covers(it)) & {"AAA", "BBB", "CCC", "DDD"} == names


def test_view_sell_tickers_equals_the_old_implementation_over_the_fixtures():
    acts, revs = _fixture_items()
    views = [None, "x", 3, [], {"state": "offline", "active": acts}, {"state": "clear"},
             {"state": "act", "active": acts + revs}, {"state": "act", "active": "junk"},
             {"state": "act", "active": []}]
    for v in views:
        assert hto.view_sell_tickers(v) == _old_view_sell_tickers(v)
        cover = asc.view_sell_cover(v)
        old = _old_view_sell_tickers(v)
        assert (cover is None) == (old is None)
        if cover is not None:
            assert set(cover) == old


# -- verified_sells == key set of verified_sell_cover ---------------------------------------

_TODAY = date(2026, 11, 25)


def _vkw(_view, **over):
    kw = dict(reduce_state="ready", session_brief_offline=False, freshness=cf.FRESH,
              built_date=_TODAY.isoformat(), today=_TODAY)
    kw.update(over)
    kw.setdefault("view", _view)
    return kw


def _live_view(active):
    return {"state": "act", "active": list(active), "resolved": [], "aware": [],
            "n_active": len(active)}


def test_verified_sells_is_the_key_set_of_verified_sell_cover_on_every_leg():
    view = _live_view([_act("deterioration_trim", "SNOW", trim_shares=2),
                       _act("stop_breach", "TGT"), _act("risk", "ZZZ")])
    legs = [{}, dict(reduce_state="none"), dict(reduce_state="offline"),
            dict(session_brief_offline=True), dict(freshness=cf.STALE),
            dict(built_date=None), dict(built_date="2026-11-24"), dict(today=None),
            dict(view=None), dict(view={"state": "offline", "active": []}),
            dict(view=_live_view([]))]
    for over in legs:
        cover = asm.verified_sell_cover(**_vkw(view, **over))
        sells = asm.verified_sells(**_vkw(view, **over))
        assert (cover is None) == (sells is None), over
        if cover is not None:
            assert set(cover) == sells, over
    ok = asm.verified_sell_cover(**_vkw(view))
    assert ok == {"SNOW": cov(2), "TGT": EXIT}
    assert asm.verified_sells(**_vkw(view)) == {"SNOW", "TGT"}
    # checked and clean is an empty map / set, never None
    assert asm.verified_sell_cover(**_vkw(_live_view([]))) == {}
    assert asm.verified_sells(**_vkw(_live_view([]))) == set()


def test_verified_sells_membership_is_unchanged_whatever_the_size_of_the_sell():
    # U6b never hides a card or quotes a size: a 1-share sell names the ticker like an exit
    for act in (_act("deterioration_trim", "SNOW", trim_shares=1), _act("risk_off_derisk", "SNOW"),
                _act("stop_breach", "SNOW")):
        assert asm.verified_sells(**_vkw(_live_view([act]))) == {"SNOW"}


def test_verified_sell_cover_does_not_mutate_its_view():
    view = _live_view([_act("deterioration_trim", "SNOW", trim_shares=2)])
    before = copy.deepcopy(view)
    asm.verified_sell_cover(**_vkw(view))
    assert view == before


# -- the defect, end to end ------------------------------------------------------------------

def _thirty_percent_book():
    rows = [{"Ticker": "AAA", "Weight (%)": 30.0, "Shares": 600, "Price": 50.0, "Score": 70.0,
             "Market Value": 30_000.0}]
    rows += [{"Ticker": f"F{i}", "Weight (%)": 10.0, "Shares": 10, "Price": 100.0, "Score": 60.0,
              "Market Value": 1_000.0} for i in range(7)]
    return pd.DataFrame(rows)


def _macro_one_share_trim():
    return [{"icon": "🌐", "ticker": None, "action": {
        "type": "PROTECTIVE_TRIM", "trim_ticker": "AAA", "trim_shares": 1}}]


def _sn_card_rec(need=300):
    return {"type": "single_name_concentration", "priority": "MEDIUM", "title": "AAA 30.0%",
            "problem": "p", "recommendation": "Trim AAA toward the ceiling",
            "root_tickers": [{"ticker": "AAA"}], "trim_shares": need}


def test_defect_end_to_end_a_one_share_macro_trim_does_not_hide_the_ceiling_trim():
    review = _macro_one_share_trim()
    cover = asc.sell_cover_summary([], review)
    assert cover == {"AAA": cov(1)}

    # 🔧 Tune-up: the Brief build keeps the card (a 1-share sale does not cover 300 shares) ...
    deferred = {}
    brief_cards = _portfolio_tuneup([_sn_card_rec()], sell_cover=cover, port_df=_thirty_percent_book(),
                                    deferred_out=deferred)
    assert [c["type"] for c in brief_cards] == ["single_name_concentration"] and deferred == {}
    # ... and Home's reconcile gives it the partial note, on a COPY (the memoized Brief is intact)
    view = _live_view(review)
    memo_before = copy.deepcopy(brief_cards)
    rec = hto.reconcile_tuneup(brief_cards, deferred, asc.view_sell_cover(view))
    assert rec["deferred_texts"] == {}
    (card,) = rec["cards"]
    assert card["act_cover_note"] == asc.cover_note("partial", "AAA", "ceiling trim")
    assert brief_cards == memo_before and "act_cover_note" not in brief_cards[0]

    # 🔗 Risk Analysis reads partial (info, the card stays)
    inp = hto.book_inputs(_thirty_percent_book(), "AAA")
    line = hto.risk_analysis_single_name_line(
        "AAA", asc.view_sell_cover(view), {}, inp["weight_pct"], inp["n_positions"],
        inp["shares"], 300)
    assert (line["state"], line["tier"]) == ("partial", "info")

    # ⚖️ Rebalancer: the row stays in trims, counts in the totals, carries the note
    rows = [_drift_row("AAA", "TRIM", 20.0, 15_000.0, price=50.0, current=30.0, target=10.0)]
    plan = reb.build_rebalance_plan(pd.DataFrame(rows), 100_000.0, act_sell_cover=asc.view_sell_cover(view))
    assert plan["deferred_trims"] == [] and [t["ticker"] for t in plan["trims"]] == ["AAA"]
    assert plan["trims"][0]["shares_delta"] == 300 and plan["trims"][0]["act_partial"] is True
    assert plan["trims"][0]["act_cover_note"] == asc.cover_note("partial", "AAA", "row")
    assert plan["trim_pct"] == 15.0 and plan["trim_action_needed"] is True
    assert plan["act_partial_count"] == 1 and plan["total_trim_value"] == 15_000.0


def test_a_covering_sale_still_defers_all_three_surfaces():
    # EW worked example A in miniature: a sized sale at least the card's own size covers it
    review = [_rev("TRIM_TO_TARGET", "AAA", trim_shares=300)]
    cover = asc.sell_cover_summary([], review)
    deferred = {}
    assert _portfolio_tuneup([_sn_card_rec()], sell_cover=cover, deferred_out=deferred) == []
    assert list(deferred) == ["AAA"]
    rec = hto.reconcile_tuneup([], deferred, asc.view_sell_cover(_live_view(review)))
    assert rec["cards"] == [] and "AAA" in rec["deferred_texts"]
    assert hto.risk_analysis_single_name_line(
        "AAA", cover, {}, 30.0, 8, 600, 300)["state"] == "brief_owns"
    rows = [_drift_row("AAA", "TRIM", 20.0, 15_000.0, price=50.0, current=30.0, target=10.0)]
    plan = reb.build_rebalance_plan(pd.DataFrame(rows), 100_000.0, act_sell_cover=cover)
    assert [d["ticker"] for d in plan["deferred_trims"]] == ["AAA"] and plan["trims"] == []
    assert plan["act_partial_count"] == 0 and plan["trim_pct"] == 0.0


def test_a_recovered_breach_restores_the_card_with_no_note():
    # the Brief deferred to a stop breach; the live view demoted it: no cover, card back, no note
    deferred = {}
    _portfolio_tuneup([_sn_card_rec()], sell_cover=asc.sell_cover_summary([_act("stop_breach")], []),
                      deferred_out=deferred)
    assert list(deferred) == ["AAA"]
    rec = hto.reconcile_tuneup([], deferred, asc.view_sell_cover(_live_view([])))
    assert len(rec["cards"]) == 1 and "act_cover_note" not in rec["cards"][0]
    assert rec["deferred_texts"] == {}


def test_a_none_cover_never_defers_on_any_surface():
    deferred = {}
    out = _portfolio_tuneup([_sn_card_rec(0)], sell_cover=None, deferred_out=deferred)
    assert len(out) == 1 and deferred == {}
    assert hto.risk_analysis_single_name_line("AAA", None, {}, 30.0, 8, 600, 0)["state"] == "unverified"
    rows = [_drift_row("AAA", "TRIM", 20.0, 15_000.0, price=50.0, current=30.0, target=10.0)]
    plan = reb.build_rebalance_plan(pd.DataFrame(rows), 100_000.0, act_sell_cover=None)
    assert plan["deferred_trims"] == [] and len(plan["trims"]) == 1 and plan["act_partial_count"] == 0
    # a weekend visit with an earlier-day Brief: the verified gate returns None, nothing deferred
    assert asm.verified_sell_cover(**_vkw(_live_view(_macro_one_share_trim()),
                                          built_date="2026-11-20", today=date(2026, 11, 21))) is None


def test_a_card_that_sizes_no_trade_is_covered_by_any_sale_sized_or_not():
    # owner Q8; today's behaviour
    cover = asc.sell_cover_summary([_act("risk_off_derisk")], [])
    deferred = {}
    assert _portfolio_tuneup([_sn_card_rec(0)], sell_cover=cover, deferred_out=deferred) == []
    assert hto.risk_analysis_single_name_line("AAA", cover, {}, 15.0, 8, 100, 0)["state"] == "brief_owns"


def test_an_earnings_note_on_a_kept_card_survives_a_partial_cover():
    # in the window with no earnings-week decision: the card keeps its earnings_note AND
    # gains the cover note from Home's reconcile
    book = _thirty_percent_book()
    book.loc[0, "Weight (%)"] = 14.0
    cover = {"AAA": cov(1)}
    out = _portfolio_tuneup([_sn_card_rec()], sell_cover=cover, earnings_days={"AAA": 3},
                            port_df=book, deferred_out={})
    assert len(out) == 1 and "earnings_note" in out[0]
    rec = hto.reconcile_tuneup(out, {}, cover)
    assert "earnings_note" in rec["cards"][0] and "act_cover_note" in rec["cards"][0]


# -- text rules -----------------------------------------------------------------------------

def test_cover_note_texts_have_no_dollar_bold_html_or_share_count():
    for noun in ("ceiling trim", "row"):
        for state in ("partial", "unknown"):
            t = asc.cover_note(state, "aaa", noun)
            assert t.startswith("🏠 Today's Act Today call also sells AAA")
            assert noun in t and "re-sized from your new book" in t
            assert "not a second sale on top of it" in t
            for bad in ("$", "**", "<", ">"):
                assert bad not in t
            assert not any(ch.isdigit() for ch in t)
    assert asc.cover_note("partial", "AAA", "row").count("fewer shares than this row") == 1
    assert "couldn't be compared" in asc.cover_note("unknown", "AAA", "row")
    for state in ("show", "covered", None, "x"):
        assert asc.cover_note(state, "AAA", "row") is None


def test_the_brief_owns_text_still_names_the_ceiling_from_the_constant():
    t = hto.tuneup_single_name_disposition("AAA", {"AAA": EXIT}, {}, 19.0, 8, 100, 5)["text"]
    assert f"still over {SINGLE_NAME_CEILING:.0f}%" in t and "$" not in t and "**" not in t
    assert "15%" not in open(asc.__file__, encoding="utf-8").read()
    assert "15" not in asc.cover_note("partial", "AAA", "ceiling trim")


# -- fractional positions --------------------------------------------------------------------

def test_a_fractional_position_sizes_in_whole_shares_and_never_rounds_up():
    # 0.9 of a share to trim: shares_to_trim floors to 0, so the card sizes no trade (need 0)
    assert ts.shares_to_trim(2.4, 20.0, 15.0) == 0
    assert asc.cover_state(cov(None), ts.shares_to_trim(2.4, 20.0, 15.0)) == "covered"
    # a fractional Act count (3.0 cast) is a whole count; 1.5 is not a count at all
    assert asc.item_covers(_act("deterioration_trim", trim_shares=3.0))["AAA"]["shares"] == 3
    assert asc.item_covers(_act("deterioration_trim", trim_shares=1.5))["AAA"]["shares"] is None


# -- history pins ----------------------------------------------------------------------------

def test_rec_events_capture_is_independent_of_the_cover():
    # the rebal_trim history rows come from `rebalance_actions` (📡 Signals), not the Rebalancer
    # plan or the cover, so the new cover changes no history row (DoD #8 n/a)
    import inspect
    from stock_analyzer import rec_events_capture as rec
    src = inspect.getsource(rec)
    assert "act_sell_cover" not in src and "held_trim_owner" not in src
    assert "act_cover_note" not in src and "act_partial" not in src


def test_the_new_brief_keys_are_not_in_the_exit_signals_payload():
    from pathlib import Path
    app = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    seg = app[app.index("_exit_signals_to_save = []"):app.index("db.save_exit_signals_batch(_exit_signals_to_save)")]
    assert "trim_shares" not in seg and "act_cover" not in seg and "subject" not in seg


def test_the_synth_schema_version_is_bumped_for_the_new_brief_shape():
    import re
    from pathlib import Path
    app = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    m = re.search(r"_SYNTH_SCHEMA_VER = (\d+)  # bumped: (.*?); was 29:", app)
    assert m and int(m.group(1)) >= 30 and "DeferSizeBlind" in m.group(2)
    assert "subject / trim_shares" in m.group(2)


# -- app.py wiring pins (render only; app.py has no unit tests) ------------------------------

def _app():
    from pathlib import Path
    return Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")


def test_app_wiring_reads_the_cover_map_not_a_ticker_set():
    src = _app()
    assert "act_sell_cover.view_sell_cover(_act_view)" in src
    assert "held_trim_owner.view_sell_tickers" not in src
    assert "sell_tickers=" not in src.replace("brief_sell_tickers=", "")
    assert "act_sell_cover=_rb_sells" in src
    assert "_rec.get(\"trim_shares\")" in src


def test_app_py_does_not_decide_the_cover_state():
    # the decision (covered / partial / unknown) lives in act_sell_cover; app.py only renders
    src = _app()
    assert "cover_state(" not in src and "valid_shares(" not in src and "valid_need(" not in src


def test_the_new_tuneup_note_is_escaped_html_and_the_other_lines_are_plain_text():
    src = _app()
    assert "_safe_html(_tu['act_cover_note'])" in src
    assert 'st.info(_tr["act_cover_note"])' in src


def test_risk_advisor_card_is_the_only_producer_of_the_card_size():
    # trim_shares: 0 exactly when the card sizes no trade, None when no usable price
    from tests.test_risk_advisor import _sn_rec
    sized = _sn_rec(5.0, 100.0)
    assert sized["trim_sized"] is True and isinstance(sized["trim_shares"], int) and sized["trim_shares"] >= 1
    tiny = _sn_rec(0.05, 100.0)
    assert tiny["trim_sized"] is False and tiny["trim_shares"] == 0
    for px in (None, 0.0, -5.0):
        assert _sn_rec(0.05, px)["trim_shares"] is None
    assert math.isfinite(sized["trim_shares"])
