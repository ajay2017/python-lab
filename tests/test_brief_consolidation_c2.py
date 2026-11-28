"""Brief consolidation C2 (docs/plans/brief-consolidation-spec.md section 2 / row
RiskOffTrimShift): ONE risk-off trim pool for 🏠 Home and the 08:00 premarket email.

Owner decisions 2026-11-27: Q4 (the pool excludes only FULL exits: a stop breach, a
deterioration EXIT) and Q5 (reverses the 2026-07-29 audit's H6: a Watch on a risk-off name
reads "covered by today's Act Today trim of T" instead of the trim moving to a lower-beta
name).

Covers `risk_off_pool.full_exit_names` / `settle_brief`, the Brief's exclusion, the
app / email PARITY of the risk-off names + `trim_shares` + directives, and the additive
`trim_shares` on `exit_advisor.assess_risk_off_derisk`.
"""
import copy
import re
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from stock_analyzer import act_sell_cover
from stock_analyzer import daily_briefing as db
from stock_analyzer import exit_advisor
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer import risk_off_pool as rop

pytestmark = pytest.mark.fast

TODAY = date(2026, 10, 16)


# ── full_exit_names ──────────────────────────────────────────────────────────

def test_stop_breach_and_deterioration_exit_items_are_full_exits():
    items = [{"kind": "stop_breach", "ticker": "aaa"},
             {"kind": "deterioration_exit", "ticker": " Bbb "}]
    assert rop.full_exit_names(items, None) == frozenset({"AAA", "BBB"})


def test_an_exit_tier_payload_with_no_item_is_a_full_exit():
    # A composite-Sell name's deterioration EXIT card is skipped upstream; the payload
    # is the only trace of the EXIT.
    det = [{"ticker": "ccc", "tier": exit_advisor.EXIT}]
    assert rop.full_exit_names([], det) == frozenset({"CCC"})
    assert rop.full_exit_names([], tuple(det)) == frozenset({"CCC"})


@pytest.mark.parametrize("item", [
    {"kind": "deterioration_trim", "ticker": "AAA", "trim_shares": 3},
    {"kind": "sell_signal", "ticker": "AAA"},
    {"kind": "critical_news", "ticker": "AAA"},
    {"kind": "premortem_triggered", "ticker": "AAA"},
    {"kind": "risk", "ticker": "AAA"},
    {"kind": "risk_off_derisk", "ticker": "AAA"},
    {"kind": "stop_breach", "ticker": ""},
    {"kind": "stop_breach", "ticker": None},
    {"ticker": "AAA", "action": {"type": "TRIM_TO_TARGET"}},      # a Review item
    {"ticker": "AAA", "action": {"type": "WATCH"}},
    "not a dict", None,
])
def test_nothing_else_is_a_full_exit(item):
    assert rop.full_exit_names([item], None) == frozenset()


@pytest.mark.parametrize("tier", [exit_advisor.TRIM, exit_advisor.WATCH, None, "", "exit"])
def test_a_trim_or_watch_tier_payload_is_not_a_full_exit(tier):
    assert rop.full_exit_names([], [{"ticker": "AAA", "tier": tier}]) == frozenset()


@pytest.mark.parametrize("det", [None, {}, "x", 5, {"AAA": {"tier": "EXIT"}}])
def test_non_list_deterioration_contributes_no_exit_names(det):
    assert rop.full_exit_names([{"kind": "stop_breach", "ticker": "ZZZ"}], det) == {"ZZZ"}
    assert rop.full_exit_names(None, det) == frozenset()


def test_the_exit_tier_literal_is_the_exit_advisors():
    assert rop._EXIT_TIER == exit_advisor.EXIT


# ── assess_risk_off_derisk: additive trim_shares ─────────────────────────────

_SPY_DOWN = pd.DataFrame({"Close": [200.0 - i * 0.5 for i in range(250)]})


def _port(rows=None):
    rows = rows or [("AAA", 20.0, 2.0), ("BBB", 18.0, 1.9), ("CCC", 15.0, 1.8),
                    ("DDD", 12.0, 1.7), ("EEE", 10.0, 1.6), ("FFF", 8.0, 1.5)]
    return pd.DataFrame([
        {"Ticker": t, "Weight (%)": w, "Price": 100.0, "Shares": 10.0 + i * 7,
         "P&L (%)": 5.0, "Gap to Stop (%)": 10.0, "Stop": 90.0, "Stop Type": "Trailing",
         "Score": 60.0, "Avg Cost": 80.0}
        for i, (t, w, _b) in enumerate(rows)])


def _held(rows=None):
    rows = rows or [("AAA", 20.0, 2.0), ("BBB", 18.0, 1.9), ("CCC", 15.0, 1.8),
                    ("DDD", 12.0, 1.7), ("EEE", 10.0, 1.6), ("FFF", 8.0, 1.5)]
    return {t: {"risk_metrics": {"beta": b}} for t, _w, b in rows}


def _assess(**kw):
    return exit_advisor.assess_risk_off_derisk(
        _port(), _held(), fragility={"severity": "fragile"}, spy_trend_df=_SPY_DOWN,
        vix_level=14.0, **kw)


def test_assess_risk_off_cards_carry_the_directives_trim_shares():
    cards = _assess()
    assert [c["ticker"] for c in cards] == ["AAA", "BBB", "CCC"]
    for c in cards:
        assert isinstance(c["trim_shares"], int)
        m = re.search(r"\((\d+) share", c["directive"])
        assert m and int(m.group(1)) == c["trim_shares"]


def test_assess_trim_shares_zero_when_the_trim_rounds_to_nothing():
    port = _port()
    port["Shares"] = 1.0                       # 25% of 1 share -> 0 whole shares
    cards = exit_advisor.assess_risk_off_derisk(
        port, _held(), fragility={"severity": "fragile"}, spy_trend_df=_SPY_DOWN,
        vix_level=14.0)
    assert cards and all(c["trim_shares"] == 0 for c in cards)
    assert all("share" not in c["directive"].split("tighten")[0] for c in cards)


def test_assess_excludes_only_what_the_caller_passes():
    cards = _assess(exclude_tickers=rop.full_exit_names(
        [{"kind": "stop_breach", "ticker": "AAA"}], []))
    assert [c["ticker"] for c in cards] == ["BBB", "CCC", "DDD"]


# ── settle_brief ─────────────────────────────────────────────────────────────

def _card(t="AAA", trim_shares=4, why="Your book is fragile."):
    return {"kind": "risk_off_derisk", "ticker": t, "trim_shares": trim_shares,
            "why": why, "directive": f"Trim ≈25% ({trim_shares} shares) of {t}."}


def _deter_trim(t="AAA", shares=4, min_trim_shares=None):
    d = {"kind": "deterioration_trim", "ticker": t, "trim_shares": shares}
    if min_trim_shares is not None:
        d["min_trim"] = {"trim_shares": min_trim_shares}
    return d


def _sell(t="AAA", min_trim_shares=None):
    d = {"kind": "sell_signal", "ticker": t}
    if min_trim_shares is not None:
        d["min_trim"] = {"trim_shares": min_trim_shares}
    return d


def _watch(t="AAA", typ="WATCH", **kw):
    return {"ticker": t, "action": {"type": typ}, "headline": "h", **kw}


def _settle(cards, act=(), review=()):
    return rop.settle_brief(list(cards), list(act), list(review))


def test_a_same_name_sell_at_least_as_large_covers_and_the_line_says_so():
    # equality covers
    (c,), _ = _settle([_card(trim_shares=4)], act=[_deter_trim(shares=4)])
    assert c["why"].endswith(act_sell_cover.beside_note("covered", "AAA", "trim"))
    assert c["why"].startswith("Your book is fragile.")
    (c,), _ = _settle([_card(trim_shares=4)], act=[_deter_trim(shares=9)])
    assert act_sell_cover.beside_note("covered", "AAA", "trim") in c["why"]


def test_a_smaller_same_name_sell_reads_partial():
    (c,), _ = _settle([_card(trim_shares=4)], act=[_deter_trim(shares=3)])
    assert c["why"].endswith(act_sell_cover.beside_note("partial", "AAA", "trim"))


def test_a_sell_with_no_size_reads_unknown():
    (c,), _ = _settle([_card(trim_shares=4)], act=[_sell()])
    assert c["why"].endswith(act_sell_cover.beside_note("unknown", "AAA", "trim"))
    # ... and a Sell whose min_trim is at or above the card covers it
    (c,), _ = _settle([_card(trim_shares=4)], act=[_sell(min_trim_shares=4)])
    assert c["why"].endswith(act_sell_cover.beside_note("covered", "AAA", "trim"))
    (c,), _ = _settle([_card(trim_shares=4)], act=[_sell(min_trim_shares=2)])
    assert c["why"].endswith(act_sell_cover.beside_note("partial", "AAA", "trim"))


def test_a_card_that_sizes_no_trade_is_covered_by_any_other_sell():
    # trim_shares 0 = the card sizes nothing; any same-name sell (even unsized) covers it.
    for act in ([_sell()], [_deter_trim(shares=1)]):
        (c,), _ = _settle([_card(trim_shares=0)], act=act)
        assert act_sell_cover.beside_note("covered", "AAA", "trim") in c["why"]


def test_an_unreadable_trim_shares_reads_unknown_not_covered():
    for bad in (None, "4", 1.5, True):
        card = _card()
        card["trim_shares"] = bad
        (c,), _ = _settle([card], act=[_deter_trim(shares=9)])
        assert act_sell_cover.beside_note("unknown", "AAA", "trim") in c["why"]


def test_no_other_sell_means_no_line():
    for act, review in (([], []),
                        ([{"kind": "critical_news", "ticker": "AAA"}], []),
                        ([{"kind": "risk", "ticker": "AAA"}], []),
                        ([{"kind": "premortem_triggered", "ticker": "AAA"}], []),
                        ([_deter_trim("BBB", 9)], []),                    # another name
                        ([], [_watch()])):
        (c,), _ = _settle([_card()], act=act, review=review)
        assert c["why"] == "Your book is fragile."


def test_a_review_trim_on_the_name_counts_as_a_sell():
    trim = {"ticker": "AAA", "action": {"type": "TRIM_TO_TARGET", "trim_shares": 6}}
    (c,), _ = _settle([_card(trim_shares=4)], review=[trim])
    assert act_sell_cover.beside_note("covered", "AAA", "trim") in c["why"]


def test_a_non_news_watch_on_a_risk_off_name_reads_covered_and_keeps_its_type():
    review = [_watch("AAA"), _watch("AAA", "DETERIORATION_WATCH"), _watch("BBB")]
    _, out = _settle([_card("AAA")], review=review)
    assert out[0]["action"] == {"type": "WATCH", "covered_by": "AAA"}
    assert out[1]["action"] == {"type": "DETERIORATION_WATCH", "covered_by": "AAA"}
    assert out[2] is review[2]                      # BBB has no risk-off card: untouched


def test_a_watch_beside_a_risk_off_trim_keeps_the_other_action_fields():
    w = _watch("AAA")
    w["action"] = {"type": "WATCH", "threshold": 10.0}
    _, out = _settle([_card()], review=[w])
    assert out[0]["action"] == {"type": "WATCH", "threshold": 10.0, "covered_by": "AAA"}


def test_news_watch_tighten_only_and_other_review_cards_are_untouched():
    news = _watch("AAA", watch_kind="news")
    tighten = {"ticker": "AAA", "action": {"type": "TIGHTEN_ONLY"}}
    sector = {"ticker": "AAA", "action": {"type": "HOLD"}}
    already = _watch("AAA")
    already["action"]["covered_by"] = "ZZZ"
    review = [news, tighten, sector, already]
    _, out = _settle([_card()], review=review)
    assert out == review
    assert out[3]["action"]["covered_by"] == "ZZZ"          # an existing owner is kept


def test_a_watch_on_a_macro_card_resolves_through_trim_ticker():
    w = {"ticker": None, "action": {"type": "WATCH", "trim_ticker": "AAA"}}
    _, out = _settle([_card("AAA")], review=[w])
    assert out[0]["action"]["covered_by"] == "AAA"


def test_settle_never_mutates_its_inputs():
    cards = [_card("AAA"), _card("BBB")]
    act = [_deter_trim("AAA", 9), {"kind": "risk", "ticker": "BBB"}]
    review = [_watch("AAA"), _watch("BBB", "DETERIORATION_WATCH")]
    snap = copy.deepcopy((cards, act, review))
    out_cards, out_review = rop.settle_brief(cards, act, review)
    assert (cards, act, review) == snap
    assert out_cards[0] is not cards[0] and out_review[0] is not review[0]
    assert "covered_by" in out_review[0]["action"] and "covered_by" not in review[0]["action"]


def test_settle_keeps_every_card_and_every_review_item():
    cards = [_card("AAA"), _card("BBB"), {"kind": "risk_off_derisk", "ticker": None}, "x"]
    review = [_watch("AAA"), _watch("ZZZ"), "junk", None]
    out_cards, out_review = rop.settle_brief(cards, [_sell("AAA")], review)
    assert len(out_cards) == len(cards) and len(out_review) == len(review)
    assert [c.get("ticker") for c in out_cards if isinstance(c, dict)] == ["AAA", "BBB", None]


def test_settle_with_no_cards_is_an_identity_on_review():
    review = [_watch("AAA")]
    assert rop.settle_brief([], [_sell()], review) == ([], review)
    assert rop.settle_brief(None, None, None) == ([], [])


# ── the Brief (build_daily_briefing) ─────────────────────────────────────────

_DET_PATH = "stock_analyzer.daily_briefing.deterioration_signals"


def _brief(monkeypatch, act=(), review=(), det=None):
    monkeypatch.setattr(db, "_act_today", lambda *a, **k: [dict(x) for x in act])
    monkeypatch.setattr(db, "_review_list", lambda *a, **k: copy.deepcopy(list(review)))
    monkeypatch.setattr(db, "_grow_today", lambda *a, **k: {})
    monkeypatch.setattr(db, "deterioration_signals",
                        lambda *a, **k: copy.deepcopy(det if det is not None else {}))
    return db.build_daily_briefing(
        _port(), [], [], [], [], _held(), None, 10000.0, TODAY,
        fragility={"severity": "fragile"}, spy_trend_df=_SPY_DOWN, vix_level=14.0)


def _ro(brief):
    return [i["ticker"] for i in brief["act_today"] if i.get("kind") == "risk_off_derisk"]


@pytest.mark.parametrize("card", [
    {"kind": "critical_news", "ticker": "AAA", "action": "Watch - Critical News",
     "why": "w", "news_headline": "h", "news_compound": -0.6},
    {"kind": "premortem_triggered", "ticker": "AAA"},
    {"kind": "risk", "ticker": "AAA", "action": "RISK", "risk_flags": [{"title": "t"}]},
    {"kind": "sell_signal", "ticker": "AAA"},
    {"kind": "deterioration_trim", "ticker": "AAA", "trim_shares": 3},
], ids=["critical_news", "premortem", "sector_attribution", "sell_signal", "det_trim"])
def test_top_beta_name_stays_a_risk_off_name_beside_a_non_exit_act_card(monkeypatch, card):
    # H6 reversed by owner 2026-11-27 (brief-consolidation Q5) / Q4: only a full exit
    # leaves the pool.
    brief = _brief(monkeypatch, act=[card])
    assert _ro(brief) == ["AAA", "BBB", "CCC"]


@pytest.mark.parametrize("review_item", [
    _watch("AAA"),
    _watch("AAA", "DETERIORATION_WATCH"),
    _watch("AAA", watch_kind="news"),
    {"ticker": "AAA", "action": {"type": "TRIM_TO_TARGET", "trim_shares": 2}},
    {"ticker": "AAA", "action": {"type": "TIGHTEN_ONLY"}},
], ids=["watch", "det_watch", "news_watch", "review_trim", "tighten_only"])
def test_top_beta_name_stays_a_risk_off_name_beside_a_review_card(monkeypatch, review_item):
    # H6 reversed by owner 2026-11-27 (brief-consolidation Q5).
    brief = _brief(monkeypatch, review=[review_item])
    assert _ro(brief) == ["AAA", "BBB", "CCC"]


def test_a_watch_beside_the_trim_reads_covered_and_the_trim_does_not_move(monkeypatch):
    # H6 reversed by owner 2026-11-27 (brief-consolidation Q5).
    brief = _brief(monkeypatch, review=[_watch("AAA"), _watch("BBB", "DETERIORATION_WATCH")])
    assert _ro(brief) == ["AAA", "BBB", "CCC"]
    by = {r["ticker"]: r for r in brief["review_list"]}
    assert by["AAA"]["action"] == {"type": "WATCH", "covered_by": "AAA"}
    assert by["BBB"]["action"] == {"type": "DETERIORATION_WATCH", "covered_by": "BBB"}


def test_a_news_watch_is_folded_into_the_trim_not_marked_covered(monkeypatch):
    brief = _brief(monkeypatch, review=[_watch("AAA", watch_kind="news",
                                               news_headline="AAA recalls product")])
    assert not [r for r in brief["review_list"] if r.get("watch_kind") == "news"]
    card = next(i for i in brief["act_today"] if i.get("ticker") == "AAA")
    assert "AAA recalls product" in card["why"]


def test_a_same_name_sell_adds_the_beside_line_to_the_brief_card(monkeypatch):
    # AAA: 10 shares -> trim_shares int(10 * 25%) = 2; the deterioration TRIM sells 5.
    brief = _brief(monkeypatch, act=[_deter_trim("AAA", 5)])
    card = next(i for i in brief["act_today"] if i.get("kind") == "risk_off_derisk")
    assert card["ticker"] == "AAA"
    assert act_sell_cover.beside_note("covered", "AAA", "trim") in card["why"]


@pytest.mark.parametrize("item,det", [
    ({"kind": "stop_breach", "ticker": "AAA"}, None),
    ({"kind": "deterioration_exit", "ticker": "AAA"}, None),
    (None, [{"ticker": "AAA", "tier": "EXIT"}]),
], ids=["stop_breach", "det_exit_item", "exit_tier_payload_only"])
def test_a_full_exit_excludes_the_name_and_the_pick_moves(monkeypatch, item, det):
    brief = _brief(monkeypatch, act=[item] if item else [], det=det)
    assert _ro(brief) == ["BBB", "CCC", "DDD"]


def test_a_raising_settle_keeps_every_card_and_logs(monkeypatch, caplog):
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(rop, "settle_brief", _boom)
    with caplog.at_level("WARNING"):
        brief = _brief(monkeypatch, act=[_deter_trim("AAA", 5)],
                       review=[_watch("AAA")])
    assert _ro(brief) == ["AAA", "BBB", "CCC"]
    card = next(i for i in brief["act_today"] if i.get("kind") == "risk_off_derisk")
    assert "also has a sell" not in card["why"]                # unsettled, not dropped
    assert "covered_by" not in brief["review_list"][0]["action"]
    assert any("risk-off settle failed" in r.getMessage() for r in caplog.records)


# ── parity: 🏠 Home vs the 08:00 email ───────────────────────────────────────

def _parity_inputs():
    """AAA stop breach; BBB Sell + deterioration EXIT; CCC deterioration TRIM; DDD Watch
    only; EEE a Sell with no deterioration; FFF nothing. Pool after the full exits
    (top 3 by beta x weight) = CCC, DDD, EEE."""
    port = _port()
    port.loc[port["Ticker"] == "AAA", "Gap to Stop (%)"] = -1.0
    det = [
        {"ticker": "BBB", "tier": "EXIT", "dd_from_peak_pct": -30, "trend_ma": 200,
         "exit_floor": -20, "pnl_pct": -25, "weight_pct": 18},
        {"ticker": "CCC", "tier": "TRIM", "dd_from_peak_pct": -15, "trend_ma": 200,
         "exit_floor": -20, "pnl_pct": -5, "weight_pct": 15},
        {"ticker": "DDD", "tier": "WATCH", "dd_from_peak_pct": -8, "trend_ma": 50,
         "exit_floor": -20, "pnl_pct": 1, "weight_pct": 12},
    ]
    act = [{"kind": "stop_breach", "ticker": "AAA"},
           {"kind": "sell_signal", "ticker": "BBB"},
           {"kind": "deterioration_trim", "ticker": "CCC", "trim_shares": 9},
           {"kind": "sell_signal", "ticker": "EEE"}]
    review = [_watch("DDD", "DETERIORATION_WATCH")]
    return port, det, act, review


def _email(port, det):
    ctx = {"ok": True, "errors": [], "port_df": port.copy(), "held_data": _held(),
           "fragility": {"severity": "fragile"}, "spy_6mo": None, "spy_1y": _SPY_DOWN,
           "vix": 14.0}
    seen = {}
    real = exit_advisor.assess_risk_off_derisk

    def _spy(*a, **k):
        out = real(*a, **k)
        seen["cards"] = copy.deepcopy(out)
        seen["exclude"] = set(k["exclude_tickers"])
        return out
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.headless_alert_engine.deterioration_signals",
               return_value=copy.deepcopy(det)), \
         patch("stock_analyzer.exit_advisor.assess_risk_off_derisk", side_effect=_spy):
        res = hae.compute_protective_alerts(TODAY)
    alerts = [a for a in res["alerts"] if a["kind"] == "risk_off_derisk"]
    return alerts, seen


def test_home_and_the_email_name_the_same_risk_off_trims(monkeypatch):
    port, det, act, review = _parity_inputs()
    alerts, seen = _email(port, det)

    monkeypatch.setattr(db, "_act_today", lambda *a, **k: copy.deepcopy(act))
    monkeypatch.setattr(db, "_review_list", lambda *a, **k: copy.deepcopy(review))
    monkeypatch.setattr(db, "_grow_today", lambda *a, **k: {})
    monkeypatch.setattr(db, "deterioration_signals", lambda *a, **k: copy.deepcopy(det))
    brief_cards = []
    real = exit_advisor.assess_risk_off_derisk

    def _spy(*a, **k):
        out = real(*a, **k)
        brief_cards.extend(copy.deepcopy(out))
        brief_cards.append({"_exclude": set(k["exclude_tickers"])})
        return out
    monkeypatch.setattr(db.exit_advisor, "assess_risk_off_derisk", _spy)
    brief = db.build_daily_briefing(
        port.copy(), [], [], [], [], _held(), None, 10000.0, TODAY,
        fragility={"severity": "fragile"}, spy_trend_df=_SPY_DOWN, vix_level=14.0)
    brief_ro = [i for i in brief["act_today"] if i.get("kind") == "risk_off_derisk"]
    brief_exclude = next(c["_exclude"] for c in brief_cards if "_exclude" in c)
    brief_raw = [c for c in brief_cards if "_exclude" not in c]

    # the same exclusion, and it is the full exits only: the stop breach and the EXIT name
    assert seen["exclude"] == brief_exclude == {"AAA", "BBB"}
    # the same names, in the same order: the deterioration-TRIM, Watch-only and Sell-only
    # names are all still in the pool
    assert [a["ticker"] for a in alerts] == [i["ticker"] for i in brief_ro] == ["CCC", "DDD", "EEE"]
    # the same sizes and directives (the email dict carries no trim_shares: read it off
    # the engine's own cards, which feed both surfaces)
    assert [c["trim_shares"] for c in seen["cards"]] == [c["trim_shares"] for c in brief_raw]
    assert [i["trim_shares"] for i in brief_ro] == [c["trim_shares"] for c in seen["cards"]]
    assert [a["directive"] for a in alerts] == [i["directive"] for i in brief_ro]
    for i in brief_ro:
        assert str(i["trim_shares"]) in i["directive"] or i["trim_shares"] == 0
    # Home reconciles the names the email can't see: the TRIM / Sell names get the line,
    # the Watch name reads covered (the email carries neither: it has no Review list)
    why = {i["ticker"]: i["why"] for i in brief_ro}
    assert act_sell_cover.beside_note("covered", "CCC", "trim") in why["CCC"]
    assert "also has" not in why["DDD"]
    assert act_sell_cover.beside_note("unknown", "EEE", "trim") in why["EEE"]
    ddd = next(r for r in brief["review_list"] if r["ticker"] == "DDD")
    assert ddd["action"] == {"type": "DETERIORATION_WATCH", "covered_by": "DDD"}


def test_the_email_excludes_exactly_the_shared_functions_set():
    port, det, _act, _review = _parity_inputs()
    alerts_pre = [{"kind": "stop_breach", "ticker": "AAA"},
                  {"kind": "deterioration_exit", "ticker": "BBB"}]
    _alerts, seen = _email(port, det)
    assert seen["exclude"] == set(rop.full_exit_names(alerts_pre, det))
