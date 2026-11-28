"""Brief consolidation C3 (row MacroTrimDefer; spec docs/plans/brief-consolidation-spec.md
section 1 row 3, section 4 Row 3).

The macro pre-event trim used to skip every affected name that had ANY Act Today card, so
the trim slid to a stronger name with no line saying why. Now a name is passed over only
when today's Act Today call sells at least as much of it as the trim would (read AFTER the
BriefSkipSizeBlind fold), and the card names each name it passed over. Owner Q6: when the
weakest name's own sale covers the trim, the trim MOVES to the next-weakest name. Owner
Q7: a Sell with no size keeps the trim on that name with the "treat this as the minimum"
sentence.
"""
import datetime
import math

import pandas as pd
import pytest

from stock_analyzer import act_sell_cover
from stock_analyzer import daily_briefing as db
from stock_analyzer import decision_bucket
from stock_analyzer import macro_trim_target as mtt
from stock_analyzer import trim_sizing
from stock_analyzer.constants import (
    MACRO_AFFECTED_TRIM_REDUCTION_PP,
    MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
    MACRO_BROAD_EXPOSURE_PCT,
)
from stock_analyzer.macro_calendar import HIGH as MC_HIGH
from tests.conftest import make_port_df

pytestmark = pytest.mark.fast

_TODAY = datetime.date(2026, 11, 27)
_PV = 10_000.0
# 5 pp of 10,000 = $500 = 5 shares at $100.
_MACRO_SH = trim_sizing.shares_for_dollars(
    round(_PV * MACRO_AFFECTED_TRIM_REDUCTION_PP / 100, 0), 100.0)


def test_fixture_sits_in_the_sized_trim_band():
    assert MACRO_AFFECTED_TRIM_THRESHOLD_PCT < 40.0 < MACRO_BROAD_EXPOSURE_PCT
    assert _MACRO_SH == 5


def _book(tech=((("AAA", 15.0, 60.0), ("BBB", 15.0, 65.0), ("CCC", 10.0, 70.0))), avail=True):
    rows = []
    for t, w, sc in tech:
        r = {"Ticker": t, "Sector": "Tech", "Weight (%)": w, "Score": sc,
             "Shares": 10.0, "Price": 100.0}
        if avail is not None:
            r["Score Available"] = bool(avail) and not (sc != sc)   # NaN = withheld
        rows.append(r)
    rows.append({"Ticker": "ZZZ", "Sector": "Energy", "Weight (%)": 60.0, "Score": 70.0,
                 "Shares": 10.0, "Price": 100.0, **({"Score Available": True} if avail is not None else {})})
    return pd.DataFrame(rows)


def _ev(days=1):
    return {"date": (_TODAY + datetime.timedelta(days=days)).isoformat(),
            "impact": MC_HIGH, "category": "Inflation", "event": "CPI",
            "affected_tickers": ["AAA", "BBB", "CCC"]}


def _macro(monkeypatch, act=(), df=None, exclude=None, pv=_PV):
    monkeypatch.setattr("stock_analyzer.macro_calendar.affected_sectors", lambda _c: ["Tech"])
    items = db._review_list(df if df is not None else _book(), [], [_ev()], {}, _TODAY,
                            portfolio_value=pv, act_today=list(act), macro_exclude=exclude)
    (item,) = [i for i in items if i.get("icon") == "🌐"]
    return item


def _act(kind, ticker="AAA", **kw):
    d = {"priority": "high", "icon": "x", "ticker": ticker, "kind": kind,
         "action": f"ACT — {kind}", "weight": None, "pnl_pct": None}
    d.update(kw)
    return d


_NOT_SELLING = ["critical_news", "premortem_triggered", "risk"]


@pytest.mark.parametrize("kind", _NOT_SELLING)
def test_weakest_name_with_a_card_that_sells_nothing_still_gets_the_trim(monkeypatch, kind):
    it = _macro(monkeypatch, act=[_act(kind)])
    a = it["action"]
    assert a["type"] == "PROTECTIVE_TRIM" and a["trim_ticker"] == "AAA"
    assert a["trim_shares"] == _MACRO_SH
    assert "Not AAA" not in it["why"] and "also has" not in it["why"]


def test_critical_news_hold_is_dropped_and_the_trim_carries_the_headline(monkeypatch):
    news = _act("critical_news", news_headline="AAA probe widens")
    it = _macro(monkeypatch, act=[news])
    assert it["action"]["trim_ticker"] == "AAA"
    out = decision_bucket.split_defensive([news], [dict(it, ticker=None)])
    kinds = [x.get("kind") for x in out["act"]]
    assert "critical_news" not in kinds                      # the hold is dropped ...
    trim = [x for x in out["act"] if (x.get("action") or {}).get("type") == "PROTECTIVE_TRIM"]
    assert len(trim) == 1 and "Also flagged on recent negative headlines" in trim[0]["why"]   # ... note folded on


@pytest.mark.parametrize("kind", ["stop_breach", "deterioration_exit"])
def test_weakest_name_being_exited_is_skipped_and_the_card_says_so(monkeypatch, kind):
    it = _macro(monkeypatch, act=[_act(kind)])
    a = it["action"]
    assert a["type"] == "PROTECTIVE_TRIM" and a["trim_ticker"] == "BBB"
    assert it["why"].endswith("Not AAA: today's Act Today call already sells all of it.")


@pytest.mark.parametrize("n,moves", [(_MACRO_SH, True), (_MACRO_SH + 3, True),
                                     (_MACRO_SH - 1, False)])
def test_deterioration_trim_covers_at_equality_and_above_but_not_below(monkeypatch, n, moves):
    it = _macro(monkeypatch, act=[_act("deterioration_trim", trim_shares=n)])
    a = it["action"]
    if moves:
        assert a["trim_ticker"] == "BBB"
        assert it["why"].endswith(
            "Not AAA: today's Act Today call already sells at least this much of it.")
    else:
        assert a["trim_ticker"] == "AAA" and a["trim_shares"] == _MACRO_SH
        assert it["why"].endswith(
            "AAA also has a smaller sell in today's Act Today call; sell this "
            "pre-event trim's size in total, not the two added together.")


def test_sell_with_no_min_trim_keeps_the_trim_with_the_minimum_sentence(monkeypatch):
    it = _macro(monkeypatch, act=[_act("sell_signal")])          # Q7
    assert it["action"]["trim_ticker"] == "AAA"
    assert it["why"].endswith(
        "AAA also has its own sell in today's Act Today call, and its size couldn't be "
        "compared with this pre-event trim; treat this pre-event trim as the minimum, "
        "not an extra sale on top.")


@pytest.mark.parametrize("n,moves", [(_MACRO_SH, True), (_MACRO_SH - 1, False)])
def test_sell_with_a_folded_min_trim_covers_only_when_large_enough(monkeypatch, n, moves):
    it = _macro(monkeypatch, act=[_act("sell_signal", min_trim={"trim_shares": n})])
    assert it["action"]["trim_ticker"] == ("BBB" if moves else "AAA")


def test_post_fold_proof_a_folded_min_trim_is_read_by_the_macro_section(monkeypatch):
    """Deterioration trim of 1 share, but an earnings-week Review trim of 5 folds onto the
    Act card as `min_trim` BEFORE the macro section runs: 5 >= the macro 5 -> AAA skipped.
    Read before the fold (1 < 5) AAA would have kept the trim with a 'smaller sell' line."""
    det = _act("deterioration_trim", trim_shares=1)
    payload = {"trim_shares": _MACRO_SH, "trim_dollars": 500.0, "from_weight": 15.0,
               "target_weight": 10.0, "reason_key": "earnings"}
    # The same Act card, unfolded: the trim stays on AAA with the partial sentence.
    plain = _macro(monkeypatch, act=[_act("deterioration_trim", trim_shares=1)])
    assert plain["action"]["trim_ticker"] == "AAA"
    monkeypatch.setattr(db.act_trim_fold, "fold", lambda items, act, extra_candidates=None:
                        (list(items), {"AAA": payload}))
    items = db._review_list(_book(), [], [_ev()], {}, _TODAY, portfolio_value=_PV,
                            act_today=[det])
    (it,) = [i for i in items if i.get("icon") == "🌐"]
    assert det["min_trim"]["trim_shares"] == _MACRO_SH          # the fold really landed first
    assert it["action"]["trim_ticker"] == "BBB"
    assert "Not AAA: today's Act Today call already sells at least this much of it." in it["why"]


def test_only_names_ranking_before_the_target_are_named(monkeypatch):
    it = _macro(monkeypatch, act=[_act("stop_breach", "AAA"), _act("stop_breach", "CCC")])
    assert it["action"]["trim_ticker"] == "BBB"
    assert "Not AAA" in it["why"] and "Not CCC" not in it["why"]


def test_every_affected_name_covered_gives_the_new_all_covered_watch(monkeypatch):
    act = [_act("stop_breach", t) for t in ("AAA", "BBB", "CCC")]
    it = _macro(monkeypatch, act=act)
    assert it["action"]["type"] == "WATCH" and "trim_ticker" not in it["action"]
    assert it["why"] == mtt.all_covered_why(40.0, "AAA, BBB, CCC")
    assert it["why"] == (
        "Affected-sector exposure 40.0% > 30% trim threshold, but every affected holding "
        "already has a sale at least this large in today's Act Today call (AAA, BBB, CCC), "
        "so no separate pre-event trim is sized.")


def test_exposure_within_tolerance_and_broad_events_are_unchanged(monkeypatch):
    low = _macro(monkeypatch, act=[_act("stop_breach")],
                 df=_book(tech=(("AAA", 10.0, 60.0), ("BBB", 10.0, 65.0))))
    assert low["action"]["type"] == "WATCH"
    assert "≤ 30% trim threshold — within tolerance" in low["why"]
    broad = _macro(monkeypatch, act=[_act("stop_breach")],
                   df=_book(tech=(("AAA", 40.0, 60.0), ("BBB", 30.0, 65.0))))
    assert broad["action"].get("broad") is True and "a broad macro print" in broad["why"]


def test_macro_exclude_is_honoured_silently(monkeypatch):
    it = _macro(monkeypatch, exclude={"AAA"})
    assert it["action"]["trim_ticker"] == "BBB"
    assert "Not AAA" not in it["why"]
    allx = _macro(monkeypatch, exclude={"AAA", "BBB", "CCC"})
    assert allx["action"]["type"] == "WATCH" and "every affected holding" in allx["why"]


def test_conviction_unmeasured_why_names_the_act_call(monkeypatch):
    it = _macro(monkeypatch, df=_book(tech=(("AAA", 15.0, math.nan), ("BBB", 25.0, math.nan)),
                                      avail=False))
    a = it["action"]
    assert a["trim_ticker"] == "BBB" and a["conviction_unmeasured"] is True
    assert a["weakest_score"] is None
    assert it["why"].endswith(
        "Conviction is unmeasured for every affected holding not already sold in today's "
        "Act Today call (fundamentals unavailable), so the weakest name can't be ranked — "
        "trimming the largest remaining affected position instead.")


# -- regression: with no Act sells the output equals the old inline code -------------------

def _old_target(df):
    """HEAD's selection + sizing, copied verbatim, over an unfiltered frame."""
    m = df["Score"].notna()
    if "Score Available" in df.columns:
        m &= df["Score Available"].fillna(False).astype(bool)
    measured = df[m]
    unmeasured = measured.empty
    if unmeasured:
        w = df.sort_values(["Weight (%)", "Ticker"], ascending=[False, True], kind="stable").iloc[0]
    else:
        w = measured.sort_values(["Score", "Ticker"], ascending=[True, True], kind="stable").iloc[0]
    return w, unmeasured


_REG = {
    "measured": (("AAA", 15.0, 60.0), ("BBB", 15.0, 65.0), ("CCC", 10.0, 70.0)),
    "measured_tie": (("BBB", 15.0, 62.0), ("AAA", 15.0, 62.0), ("CCC", 10.0, 70.0)),
    "partly_withheld": (("AAA", 25.0, math.nan), ("BBB", 10.0, 72.0), ("CCC", 5.0, 55.0)),
    "unmeasured": (("AAA", 15.0, math.nan), ("BBB", 25.0, math.nan)),
    "unmeasured_tie": (("YYY", 20.0, math.nan), ("AAA", 20.0, math.nan)),
}


@pytest.mark.parametrize("name", sorted(_REG))
@pytest.mark.parametrize("act", [[], [_act("critical_news", "ZZZ")]])
def test_no_act_sell_output_matches_the_old_inline_code(monkeypatch, name, act):
    df = _book(tech=_REG[name])
    it = _macro(monkeypatch, act=act, df=df)
    tech = df[df["Sector"] == "Tech"]
    w, unmeasured = _old_target(tech)
    pp = min(MACRO_AFFECTED_TRIM_REDUCTION_PP, float(w["Weight (%)"]))
    dollars = round(_PV * (pp / 100), 0)
    shares = trim_sizing.shares_for_dollars(dollars, float(w["Price"]))
    if shares > 0:
        dollars = round(shares * float(w["Price"]), 0)
        pp = round(dollars / _PV * 100, 1)
    exposure = float(tech["Weight (%)"].sum())
    a = it["action"]
    assert a["type"] == "PROTECTIVE_TRIM"
    assert (a["trim_ticker"], a["trim_shares"], a["trim_dollars"], a["reduction_pp"]) == (
        str(w["Ticker"]), shares, dollars, pp)
    assert a["from_exposure"] == exposure and a["to_exposure"] == max(0.0, exposure - pp)
    assert a["conviction_unmeasured"] is unmeasured
    assert a["weakest_score"] == (None if unmeasured else float(w["Score"]))
    head = f"Affected-sector exposure {exposure:.1f}% > 30% trim threshold. "
    tail = ("Conviction is unmeasured for every affected holding not already sold in today's "
            "Act Today call (fundamentals unavailable), so the weakest name can't be ranked "
            "— trimming the largest remaining affected position instead."
            if unmeasured else
            "Trimming weakest holding first preserves higher-conviction names.")
    assert it["why"] == head + tail


def test_size_trim_matches_the_old_formula_on_a_grid_incl_sub_share():
    for w in (0.5, 3.0, 5.0, 12.0, 40.0):
        for px in (0.0, 0.5, 3.0, 37.2, 100.0, 480.0, 5000.0, 20000.0):
            for pv in (1000.0, 7582.0, 10_000.0, 250_000.0):
                pp = min(MACRO_AFFECTED_TRIM_REDUCTION_PP, w)
                dollars = round(pv * (pp / 100), 0)
                sh = trim_sizing.shares_for_dollars(dollars, px)
                if px > 0 and sh > 0:
                    dollars = round(sh * px, 0)
                    pp = round(dollars / pv * 100, 1)
                assert mtt.size_trim(w, px, pv) == {
                    "reduction_pp": pp, "trim_dollars": dollars, "trim_shares": sh}, (w, px, pv)


# -- pure pick / wording -------------------------------------------------------------------

def _rows(*spec):
    return [{"ticker": t, "score": s, "score_available": True, "weight": w, "price": 100.0}
            for t, s, w in spec]


def test_pick_offline_cover_skips_nothing_and_empty_inputs_are_safe():
    rows = _rows(("AAA", 40.0, 15.0), ("BBB", 55.0, 15.0))
    for cover in (None, "x", []):
        assert mtt.pick(rows, cover, _PV)["target"]["ticker"] == "AAA"
    for r in ([], None):
        out = mtt.pick(r, {}, _PV)
        assert out["target"] is None and out["all_covered"] is False
    assert mtt.pick(rows, {}, 0.0)["target"] is None


def test_pick_reports_reason_beside_and_does_not_mutate():
    rows = _rows(("AAA", 40.0, 15.0), ("BBB", 50.0, 15.0), ("CCC", 60.0, 15.0))
    cover = {"AAA": {"exit": True, "shares": None},
             "BBB": {"exit": False, "shares": _MACRO_SH},
             "CCC": {"exit": False, "shares": 1}}
    import copy
    snap = (copy.deepcopy(rows), copy.deepcopy(cover))
    out = mtt.pick(rows, cover, _PV)
    assert out["target"]["ticker"] == "CCC" and out["beside"] == "partial"
    assert out["skipped_ahead"] == [{"ticker": "AAA", "reason": "exit"},
                                    {"ticker": "BBB", "reason": "covers"}]
    assert (rows, cover) == snap
    unk = mtt.pick(rows[2:], {"CCC": {"exit": False, "shares": None}}, _PV)
    assert unk["beside"] == "unknown"
    assert mtt.pick(rows[2:], {}, _PV)["beside"] is None


def test_wording_has_no_dollar_bold_or_share_counts():
    texts = [mtt.skipped_sentence("AAA", "exit"), mtt.skipped_sentence("AAA", "covers"),
             mtt.all_covered_why(40.0, ["AAA", "BBB"])]
    texts += [act_sell_cover.beside_note(s, "AAA", "pre-event trim")
              for s in ("covered", "partial", "unknown")]
    assert act_sell_cover.beside_note("show", "AAA", "trim") is None
    for t in texts:
        assert "$" not in t and "**" not in t and not any(c.isdigit() for c in t.replace("40.0", "").replace("30", ""))


def test_beside_note_sentences_are_the_spec_words():
    assert act_sell_cover.beside_note("covered", "aaa", "trim") == (
        "AAA also has a sell at least this large in today's Act Today call; that sale "
        "counts as this trim, so don't add this one on top of it.")
    assert act_sell_cover.beside_note("partial", "AAA", "trim") == (
        "AAA also has a smaller sell in today's Act Today call; sell this trim's size in "
        "total, not the two added together.")
    assert act_sell_cover.beside_note("unknown", "AAA", "trim") == (
        "AAA also has its own sell in today's Act Today call, and its size couldn't be "
        "compared with this trim; treat this trim as the minimum, not an extra sale on top.")
    assert act_sell_cover.beside_note("bogus", "AAA", "trim") is None


# -- the macro Affected list reads the sells canon -----------------------------------------

def test_affected_list_keeps_a_name_whose_only_act_card_is_the_sector_attribution():
    port_df = make_port_df([
        {"ticker": "AAA", "weight": 20.0, "sector": "Tech", "score": 70.0},
        {"ticker": "BBB", "weight": 10.0, "sector": "Tech", "score": 70.0},
    ])
    ev = {"date": "2026-11-29", "impact": MC_HIGH, "category": "Inflation", "event": "CPI",
          "affected_tickers": ["AAA", "BBB"]}
    rec = {
        "priority": "HIGH", "type": "sector_concentration", "sector": "Tech",
        "title": "Tech 30.0% — Hard Cap Breach", "problem": "Tech 30%.",
        "recommendation": "Trim Tech.",
        "root_tickers": [{"ticker": "AAA", "value": 20.0, "weight": 20.0,
                          "market_value": 20_000.0, "label": "20% weight"}],
        "trim_candidates": [], "redeploy_sectors": [], "trim_target_pp": 5.0,
        "trim_target_dollar": 5_000, "trim_target_denom": 100_000.0,
        "sector_members": [{"ticker": "AAA", "market_value": 20_000.0}],
    }
    brief = db.build_daily_briefing(
        port_df=port_df, alert_list=[], risk_recs=[rec], news_items=[], macro_events=[ev],
        held_data={}, scanner_results=None, portfolio_value=100_000.0, today=_TODAY,
        market_context={"tone": "bull"})
    assert any(i.get("kind") == "risk" and i.get("ticker") == "AAA"
               for i in brief["act_today"]), "precondition: AAA carries only the sector card"
    macro = [i for i in brief["review_list"] if i.get("icon") == "🌐"]
    assert macro, "precondition: the macro card is in the Review list"
    head = macro[0]["headline"]
    assert "AAA" in head and "BBB" in head and "not listed" not in head
