"""Brief consolidation C1 (row SectorCapCardMerged, build side; spec
docs/plans/brief-consolidation-spec.md section 1a).

F-22a reversed by owner 2026-11-27 (brief-consolidation Q1): the sector Hard Cap `risk`
card's ticker is an ATTRIBUTION (the sector's largest holding), so it is no longer
merged onto, or dropped by, a same-ticker stop breach / Sell / deterioration / news
card. A `risk` item groups only with other `risk` items on its ticker.
"""
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import breach_recovery
from stock_analyzer import daily_briefing as db
from stock_analyzer import decision_bucket as dbk
from stock_analyzer import sector_trigger_text
from stock_analyzer.act_today_view import act_today_view
from stock_analyzer.constants import STOP_RECOVERY_MARGIN_PCT
from tests.conftest import make_port_df

pytestmark = pytest.mark.fast

_TODAY = date(2026, 11, 27)

_FLAG = {
    "title": "Tech 40.0% — Hard Cap Breach", "recommendation": "Trim Tech toward 25%.",
    "problem": "Tech 40%", "rec_type": "sector_concentration", "sector": "Tech",
    "sector_members": [{"ticker": "AAA", "market_value": 20_000.0},
                       {"ticker": "BBB", "market_value": 10_000.0}],
    "trim_candidates": [{"ticker": "AAA", "score": 40.0, "weight": 20.0, "pnl_pct": 1.0,
                         "market_value": 20_000.0, "price": 100.0}],
    "redeploy_sectors": [{"sector": "Energy", "weight": 3.0}],
    "trim_target_pp": 15.0, "trim_target_dollar": 15_000, "trim_target_denom": 100_000.0,
}


def _item(kind, ticker="AAA", priority="high", **kw):
    d = {"priority": priority, "icon": "x", "ticker": ticker, "kind": kind,
         "action": f"ACT — {kind}", "weight": None, "pnl_pct": None}
    d.update(kw)
    return d


def _risk(ticker="AAA", flag=None, title="Sector"):
    f = dict(flag or _FLAG)
    f["title"] = title
    return _item("risk", ticker, action=f"RISK — {title}", risk_flags=[f])


def _port():
    return pd.DataFrame([
        {"Ticker": "AAA", "Weight (%)": 20.0, "P&L (%)": 5.0, "Price": 100.0, "Shares": 10.0},
        {"Ticker": "BBB", "Weight (%)": 10.0, "P&L (%)": 0.0, "Price": 10.0, "Shares": 10.0},
    ])


def _by_kind(out, ticker="AAA"):
    return {i["kind"]: i for i in out if str(i.get("ticker", "")).upper() == ticker}


# -- _consolidate_act_today -----------------------------------------------------------------

@pytest.mark.parametrize("kind,priority", [("stop_breach", "critical"), ("sell_signal", "high")])
def test_mechanical_exit_and_sector_card_both_stay(kind, priority):
    out = db._consolidate_act_today(
        [_item(kind, priority=priority), _risk()], _port())
    assert len(out) == 2
    cards = _by_kind(out)
    assert set(cards) == {kind, "risk"}
    risk = cards["risk"]
    flag = risk["risk_flags"][0]
    # the risk card keeps its whole rebalance-plan payload
    assert flag["rec_type"] == "sector_concentration"
    assert flag["trim_candidates"] == _FLAG["trim_candidates"]
    assert flag["trim_target_pp"] == 15.0 and flag["trim_target_dollar"] == 15_000
    assert flag["trim_target_denom"] == 100_000.0
    assert "risk_flags" not in cards[kind]


@pytest.mark.parametrize("kind", ["deterioration_trim", "deterioration_exit", "critical_news"])
def test_softer_cards_and_sector_card_both_stay_without_merged_flags(kind):
    out = db._consolidate_act_today([_item(kind), _risk()], _port())
    assert len(out) == 2
    cards = _by_kind(out)
    assert set(cards) == {kind, "risk"}
    assert "risk_flags" not in cards[kind]          # never carried onto a non-risk card
    assert len(cards["risk"]["risk_flags"]) == 1
    # weight / P&L still come from port_df on BOTH cards
    for c in cards.values():
        assert c["weight"] == 20.0 and c["pnl_pct"] == 5.0


def test_two_risk_items_on_one_ticker_still_merge():
    out = db._consolidate_act_today(
        [_risk(title="Sector"), _risk(title="Beta")], _port())
    assert len(out) == 1
    assert out[0]["kind"] == "risk"
    assert out[0]["action"] == "RISK — 2 flags on AAA"
    assert [f["title"] for f in out[0]["risk_flags"]] == ["Sector", "Beta"]
    assert out[0]["weight"] == 20.0 and out[0]["pnl_pct"] == 5.0


def test_risk_item_beside_two_non_risk_cards_keeps_the_old_non_risk_pick():
    # A non-risk group is consolidated exactly as before: the highest priority wins.
    out = db._consolidate_act_today(
        [_item("critical_news"), _item("deterioration_trim", priority="critical"), _risk()],
        _port())
    kinds = sorted(i["kind"] for i in out)
    assert kinds == ["deterioration_trim", "risk"]


def test_ticker_less_and_premortem_still_pass_through():
    macro = _item("macro", ticker=None)
    prem = _item("premortem_triggered")
    out = db._consolidate_act_today([macro, prem, _risk()], _port())
    assert {i["kind"] for i in out} == {"macro", "premortem_triggered", "risk"}


@pytest.mark.parametrize("combo", [
    ["stop_breach", "sell_signal", "critical_news", "deterioration_trim"],
    ["critical_news", "deterioration_exit"],
    ["sell_signal"],
    ["critical_news"],
])
def test_each_non_risk_non_premortem_ticker_appears_at_most_once(combo):
    items = [_item(k, priority="critical" if k == "stop_breach" else "high") for k in combo]
    items += [_item("premortem_triggered"), _risk(), _risk(title="Beta")]
    items += [_item(k, ticker="BBB") for k in combo]
    out = db._consolidate_act_today(items, _port())
    seen = [str(i["ticker"]).upper() for i in out
            if i.get("ticker") and i["kind"] not in ("risk", "premortem_triggered")]
    assert len(seen) == len(set(seen))
    assert sum(1 for i in out if i["kind"] == "risk") == 1   # the two risk items merged


# -- _reconcile_act -------------------------------------------------------------------------

def _act(it):
    return {**it, "_source": "act"}


def test_reconcile_keeps_news_and_standalone_sector_card_with_no_note_on_the_risk_card():
    news = _act(_item("critical_news", directive="Hold for now.", why="2 headlines"))
    risk = _act(_risk())
    out = dbk._reconcile_act([news, risk])
    assert [i["kind"] for i in out] == ["critical_news", "risk"]
    # the sector card sells nothing on AAA: it neither drops the news card nor gets a note
    assert "why" not in out[1] and "negative headlines" not in str(out[1])


def test_reconcile_review_trim_drops_news_and_notes_the_trim_not_the_risk_card():
    news = _act(_item("critical_news", directive="Hold for now.", why="2 headlines"))
    risk = _act(_risk())
    trim = {"_source": "review", "ticker": "AAA", "priority": "medium", "why": "w",
            "action": {"type": "TRIM_TO_TARGET", "trim_shares": 5}}
    out = dbk._reconcile_act([news, risk, trim])
    assert [i.get("kind") for i in out] == ["risk", None]
    assert "negative headlines" in out[1]["why"]
    assert "why" not in out[0]


def test_reconcile_risk_card_alone_is_not_a_reduce_call():
    # a news card beside ONLY the sector attribution card is not "contradicted"
    news = _act(_item("critical_news"))
    out = dbk._reconcile_act([news, _act(_risk())])
    assert len(out) == 2


def test_reconcile_still_drops_news_beside_a_real_reduce_kind():
    news = _act(_item("critical_news"))
    out = dbk._reconcile_act([news, _act(_item("deterioration_trim"))])
    assert [i["kind"] for i in out] == ["deterioration_trim"]
    assert "negative headlines" in out[0]["why"]


# -- breach_recovery ------------------------------------------------------------------------

def test_breach_recovery_never_restores_a_risk_card():
    assert "risk" in breach_recovery._EXCLUDED_ACT_KINDS
    out = breach_recovery.alternates_for(
        frozenset({"AAA"}), [_item("risk"), _item("sell_signal")], [])
    assert [i["kind"] for i in out["AAA"]["act"]] == ["sell_signal"]


# -- end to end: build_daily_briefing, Signals parity, act_today_view -----------------------

_REC = {
    "priority": "HIGH", "type": "sector_concentration", "sector": "Tech",
    "title": "Tech 40.0% — Hard Cap Breach",
    "problem": "Tech 40% of the book.", "recommendation": "Trim Tech toward 25%.",
    "root_tickers": [{"ticker": "AAA", "value": 20.0, "weight": 20.0,
                      "market_value": 20_000.0, "label": "20% weight"}],
    "trim_candidates": _FLAG["trim_candidates"], "redeploy_sectors": _FLAG["redeploy_sectors"],
    "trim_target_pp": 15.0, "trim_target_dollar": 15_000, "trim_target_denom": 100_000.0,
    "sector_members": _FLAG["sector_members"],
}


def _build(gap):
    port_df = make_port_df([
        {"ticker": "AAA", "weight": 20.0, "gap_to_stop": gap, "stop": 100.0,
         "price": 100.0, "shares": 200, "score": 70.0},
        {"ticker": "BBB", "weight": 10.0, "score": 70.0},
    ])
    return db.build_daily_briefing(
        port_df=port_df, alert_list=[], risk_recs=[dict(_REC)], news_items=[],
        macro_events=[], held_data={}, scanner_results=None,
        portfolio_value=100_000.0, today=_TODAY, market_context={"tone": "bull"},
    )


def _sector_cards(items):
    return [i for i in items if i.get("kind") == "risk"
            and any(f.get("rec_type") == "sector_concentration"
                    for f in (i.get("risk_flags") or []))]


def test_build_with_largest_holding_breached_holds_both_cards_and_matches_signals():
    brief = _build(gap=-3.0)
    aaa = [i for i in brief["act_today"] if i.get("ticker") == "AAA"]
    assert sorted(i["kind"] for i in aaa) == ["risk", "stop_breach"]
    flag = _sector_cards(aaa)[0]["risk_flags"][0]
    assert flag["sector"] == "Tech"
    assert flag["sector_members"] == _FLAG["sector_members"]
    assert flag["trim_candidates"] == _FLAG["trim_candidates"]
    # Signals parity: the REDUCE card there defers to a Brief Act Today item for this
    # sector; it must really exist now even when the sector's top name is breached.
    state = sector_trigger_text.card_state({"sector": "Tech"}, [dict(_REC)])
    assert state["band"] == "hard_cap"
    assert any(f.get("sector") == "Tech" for i in brief["act_today"]
               for f in (i.get("risk_flags") or []))


def test_build_without_a_breach_has_the_same_single_sector_card():
    brief = _build(gap=8.0)
    assert len(_sector_cards(brief["act_today"])) == 1


def _pe(gap):
    return pd.DataFrame({"Ticker": ["AAA"], "Stop": [100.0], "Gap to Stop (%)": [gap]})


def test_act_today_view_shows_exactly_one_sector_card_before_and_after_recovery():
    brief = _build(gap=-3.0)
    # active breach: one breach card + exactly one sector card
    view = act_today_view(brief, False, _pe(-3.0), None, STOP_RECOVERY_MARGIN_PCT)
    assert len(_sector_cards(view["active"])) == 1
    assert [i["kind"] for i in view["active"] if i.get("kind") == "stop_breach"] == ["stop_breach"]
    # recovered well above the stop: the breach is resolved, the sector card is still
    # exactly one (the breach restore must not bring back a duplicate)
    view2 = act_today_view(brief, False, _pe(STOP_RECOVERY_MARGIN_PCT + 5.0), None,
                           STOP_RECOVERY_MARGIN_PCT)
    assert [i["ticker"] for i in view2["resolved"]] == ["AAA"]
    assert len(_sector_cards(view2["active"])) == 1
    assert not [i for i in view2["active"] if i.get("kind") == "stop_breach"]
