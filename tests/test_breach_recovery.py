"""BriefBreachRestore Batch 1 (docs/plans/brief-breach-restore-batch1-spec.md): a recovered stop
breach restores the per-name Act / Review cards the breach hid at build time.

Covers the pure module `breach_recovery`, the builder's counterfactual pass
(`daily_briefing._breach_alternate_pass`, `_act_today(skip_breach=)`, `_review_list(macro_exclude=)`),
and `act_today_view`'s re-split. Every threshold is read from `constants.py`."""
import ast
import copy
import logging
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

import stock_analyzer.daily_briefing as db
from stock_analyzer import act_sell_cover as asc
from stock_analyzer import breach_recovery as br
from stock_analyzer import decision_bucket as dbk
from stock_analyzer import held_news_check
from stock_analyzer.act_today_view import act_today_view
from stock_analyzer.constants import (
    APPROACHING_STOP_GAP_PCT,
    BUCKET_CRITICAL_NEWS_IS_ACT,
    EARNINGS_IMMINENT_DAYS,
    NEWS_CRITICAL_MAX_TIER,
    NEWS_CRITICAL_MIN_HEADLINES,
    NEWS_SENTIMENT_CRITICAL,
    STOP_RECOVERY_MARGIN_PCT,
)
from stock_analyzer.macro_calendar import HIGH as MC_HIGH
from stock_analyzer.util import stop_recovery_state
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
_TODAY = date(2026, 10, 26)            # a Monday
_PRICE = 50.0
M = STOP_RECOVERY_MARGIN_PCT
_ABOVE_APPROACHING = APPROACHING_STOP_GAP_PCT + 12.0   # no breach, no approaching-stop card


@pytest.fixture(autouse=True)
def _tech_is_the_affected_sector(monkeypatch):
    monkeypatch.setattr("stock_analyzer.macro_calendar.affected_sectors", lambda _c: {"Tech"})


# ---------------------------------------------------------------- fixtures

def _row(t, w, *, score=70.0, pnl=0.0, gap=10.0, shares=100, sector="Tech", signal="Hold"):
    return {"Ticker": t, "Sector": sector, "Weight (%)": w, "Score": score,
            "Score Available": True, "P&L (%)": pnl, "Price": _PRICE, "Shares": shares,
            "Stop": _PRICE * 0.9, "Gap to Stop (%)": gap, "Stop Type": "ATR Stop",
            "Signal": signal, "Market Value": shares * _PRICE}


def _det(tier, ticker="AAA", shares=100):
    return {"tier": tier, "ticker": ticker, "shares": shares, "dd_from_peak_pct": -12.0,
            "peak": 60.0, "trend_ma": 50, "below_ma_count": 3, "rel_strength": -2.0,
            "trim_floor": 10.0, "exit_floor": 20.0, "pnl_pct": -5.0, "weight_pct": 17.4,
            "price": _PRICE, "dollar_risk": 500.0}


def _news(ticker="AAA", n=None):
    start = held_news_check.news_window_start(_TODAY).timestamp()
    return [{"ticker": ticker, "compound": NEWS_SENTIMENT_CRITICAL - 0.1,
             "tier": NEWS_CRITICAL_MAX_TIER, "ts": start + 3600 + i,
             "title": f"{ticker} headline {i}"}
            for i in range(n or NEWS_CRITICAL_MIN_HEADLINES)]


def _book(gap, *, w=17.4, score=50.0, signal="Hold", pnl=0.0, extra=()):
    rows = [_row("AAA", w, score=score, gap=gap, signal=signal, pnl=pnl),
            _row("BBB", 20.0)]
    rows += [_row(f"F{i}", 5.0, sector="Energy") for i in range(6)]
    rows += list(extra)
    pv = 100 * _PRICE / (w / 100.0)
    return pd.DataFrame(rows), pv


# name -> kwargs for _scene
_SCENES = {
    "exit": dict(det="EXIT", days=3),
    "trim": dict(det="TRIM", days=3),
    "sell_signal_weak_large": dict(signal="Sell"),
    "critical_news": dict(news=True, w=10.0, score=70.0),
    "review_trims": dict(days=3),
    "nothing_else": dict(w=10.0, score=70.0),
}


def _scene(name, gap):
    kw = dict(_SCENES[name])
    det = kw.pop("det", None)
    days = kw.pop("days", None)
    news = kw.pop("news", False)
    df, pv = _book(gap, **kw)
    held = ({"AAA": {"earnings": (_TODAY + timedelta(days=days)).isoformat()}}
            if days is not None else {})
    return dict(port_df=df, alert_list=[], risk_recs=[],
                news_items=_news() if news else [], macro_events=[], held_data=held,
                scanner_results=None, portfolio_value=pv, today=_TODAY,
                market_context={"tone": "bull"}), ([_det(det)] if det else [])


def _build(monkeypatch, name, gap, *, alt_pass=None, act_patch=None):
    kwargs, det = _scene(name, gap)
    with monkeypatch.context() as m:
        m.setattr(db, "deterioration_signals", lambda *a, **k: det)
        if alt_pass is not None:
            m.setattr(db, "_breach_alternate_pass", alt_pass)
        if act_patch is not None:
            m.setattr(db, "_act_today", act_patch(db._act_today))
        return db.build_daily_briefing(**kwargs)


def _reference_alternates(monkeypatch, name):
    """T's non-excluded items from a build where AAA is NOT breached."""
    kwargs, det = _scene(name, _ABOVE_APPROACHING)
    ts_ = held_news_check.news_window_start(_TODAY)
    lbl = held_news_check.window_label(ts_)
    act = db._act_today(kwargs["port_df"], [], [], kwargs["news_items"], [], _TODAY,
                        deterioration=det, premortem_triggers=None,
                        news_window_start_ts=ts_.timestamp(), news_window_label=lbl)
    rev = db._review_list(kwargs["port_df"], kwargs["news_items"], [], kwargs["held_data"], _TODAY,
                          portfolio_value=kwargs["portfolio_value"], act_today=act,
                          deterioration=det, news_window_start_ts=ts_.timestamp(),
                          news_window_label=lbl)
    return br.alternates_for({"AAA"}, act, rev)["AAA"]


def _item(kind, t="AAA", **kw):
    return {"ticker": t, "kind": kind, "priority": "high", "icon": "x", "action": "A",
            "directive": "d", "why": "w", "trigger": "t", "weight": 10.0, "pnl_pct": 0.0, **kw}


def _breach(t="AAA"):
    return _item("stop_breach", t, priority="critical", shares=10)


def _review(t="AAA", typ="TRIM_TO_TARGET", **kw):
    return {"ticker": t, "headline": "h", "why": "w", "trigger": "t", "priority": "high",
            "icon": "x", "action": {"type": typ, "trim_shares": 2, **kw}}


def _pe(gap, t="AAA"):
    return pd.DataFrame({"Ticker": [t], "Stop": [90.0], "Gap to Stop (%)": [gap]})


def _brief(act=None, review=None, alts="__absent__"):
    b = {"act_today": [_breach()] if act is None else act,
         "review_list": [] if review is None else review}
    if alts != "__absent__":
        b[br.ALTERNATES_KEY] = alts
    return b


def _view(brief, gap, ticker="AAA"):
    return act_today_view(brief, False, _pe(gap, ticker), {}, M)


# ------------------------------------------------- 1-6 builder (real pipeline)

@pytest.mark.parametrize("name", ["exit", "trim", "sell_signal_weak_large", "critical_news",
                                  "review_trims", "nothing_else"])
def test_alternates_equal_a_no_breach_build(monkeypatch, name):
    brief = _build(monkeypatch, name, -1.0)
    assert [i["kind"] for i in brief["act_today"]
            if i.get("ticker") == "AAA"] == ["stop_breach"]
    got = brief["breach_alternates"]["AAA"]
    want = _reference_alternates(monkeypatch, name)
    # `trigger` is excluded: the earnings-week card's trigger line quotes the stop gap
    # (-1.0% here, 20% in the reference book), a Brief-time figure that differs by construction.
    drop = lambda items: [{k: v for k, v in i.items() if k != "trigger"} for i in items]
    assert drop(got["act"]) == drop(want["act"])
    assert drop(got["review"]) == drop(want["review"])
    if name != "nothing_else":
        assert got["act"] or got["review"]
    else:
        assert got == {"act": [], "review": []}
    # A restored earnings card never prints the build-time negative gap as "% below".
    for r in got["review"]:
        trig = str(r.get("trigger") or "")
        assert "-1.0%" not in trig
        if "expected move" in trig:
            assert "at or below the stop when today's Brief was built" in trig


def test_the_scenes_actually_exercise_each_lane(monkeypatch):
    # guards the equivalence test above against passing on two empty lists
    kinds = lambda n: [i["kind"] for i in _build(monkeypatch, n, -1.0)["breach_alternates"]["AAA"]["act"]]
    assert kinds("exit") == ["deterioration_exit"]
    assert kinds("trim") == ["deterioration_trim"]
    assert kinds("sell_signal_weak_large") == ["sell_signal"]
    assert kinds("critical_news") == ["critical_news"]
    assert _build(monkeypatch, "review_trims", -1.0)["breach_alternates"]["AAA"]["review"]
    sell = _build(monkeypatch, "sell_signal_weak_large", -1.0)["breach_alternates"]["AAA"]["act"][0]
    assert "min_trim" in sell                                   # the weak-large sizing rode on it


def test_a_recovered_sell_with_an_earnings_trim_restores_one_card_carrying_the_earnings_min_trim(monkeypatch):
    # BriefSkipSizeBlind: the alternate pass runs the fold too, so a recovered name whose
    # no-breach build has a sell_signal AND an earnings-week trim restores ONE sell card
    # (the Sell, carrying the earnings size), never a second trim card beside it.
    df, pv = _book(-1.0, w=22.0, score=70.0, signal="Sell")
    held = {"AAA": {"earnings": (_TODAY + timedelta(days=3)).isoformat()}}
    monkeypatch.setattr(db, "deterioration_signals", lambda *a, **k: [])
    brief = db.build_daily_briefing(
        port_df=df, alert_list=[], risk_recs=[], news_items=[], macro_events=[],
        held_data=held, scanner_results=None, portfolio_value=pv, today=_TODAY,
        market_context={"tone": "bull"})
    alt = brief["breach_alternates"]["AAA"]
    assert [i["kind"] for i in alt["act"]] == ["sell_signal"]
    assert alt["act"][0]["min_trim"]["reason_key"] == "earnings"
    assert not [i for i in alt["review"]
                if (i.get("action") or {}).get("type") in ("TRIM_TO_TARGET", "TRIM_AND_TIGHTEN")]
    v = _view(brief, _ABOVE_APPROACHING)
    mine = [i for i in v["active"] if str(i.get("ticker", "")).upper() == "AAA"]
    assert [i.get("kind") for i in mine] == ["sell_signal"]
    assert mine[0]["min_trim"]["reason_key"] == "earnings"


@pytest.mark.parametrize("name", ["exit", "sell_signal_weak_large", "review_trims"])
def test_main_lists_are_identical_with_the_pass_on_or_off(monkeypatch, name):
    on = _build(monkeypatch, name, -1.0)
    off = _build(monkeypatch, name, -1.0, alt_pass=lambda *a, **k: {})
    assert on["act_today"] == off["act_today"]
    assert on["review_list"] == off["review_list"]
    assert off["breach_alternates"] == {}


@pytest.mark.parametrize("name", list(_SCENES))
def test_main_review_list_never_carries_a_breached_subject(monkeypatch, name):
    brief = _build(monkeypatch, name, -1.0)
    breached = br.breach_tickers(brief["act_today"])
    assert breached == {"AAA"}
    assert not [i for i in brief["review_list"] if dbk._ticker(i) in breached]


def test_alternates_only_hold_breached_subjects(monkeypatch):
    kwargs, det = _scene("exit", -1.0)
    kwargs["port_df"] = pd.concat(
        [kwargs["port_df"], pd.DataFrame([_row("CCC", 3.0, score=40.0, signal="Sell")])],
        ignore_index=True)
    monkeypatch.setattr(db, "deterioration_signals", lambda *a, **k: det)
    brief = db.build_daily_briefing(**kwargs)
    assert set(brief["breach_alternates"]) == {"AAA"}           # CCC (a plain Sell) is main-list only
    assert any(i["ticker"] == "CCC" and i["kind"] == "sell_signal" for i in brief["act_today"])


def test_a_no_breach_day_makes_one_call_each_and_an_empty_map(monkeypatch):
    calls = {"act": 0, "review": 0}
    real_act, real_rev = db._act_today, db._review_list

    def act(*a, **k):
        calls["act"] += 1
        return real_act(*a, **k)

    def rev(*a, **k):
        calls["review"] += 1
        return real_rev(*a, **k)

    monkeypatch.setattr(db, "_act_today", act)
    monkeypatch.setattr(db, "_review_list", rev)
    brief = _build(monkeypatch, "nothing_else", _ABOVE_APPROACHING)
    assert calls == {"act": 1, "review": 1}
    assert brief["breach_alternates"] == {}


def test_a_breach_day_makes_exactly_one_extra_call_each(monkeypatch):
    calls = {"act": 0, "review": 0}
    real_act, real_rev = db._act_today, db._review_list
    monkeypatch.setattr(db, "_act_today",
                        lambda *a, **k: (calls.__setitem__("act", calls["act"] + 1), real_act(*a, **k))[1])
    monkeypatch.setattr(db, "_review_list",
                        lambda *a, **k: (calls.__setitem__("review", calls["review"] + 1), real_rev(*a, **k))[1])
    _build(monkeypatch, "exit", -1.0)
    assert calls == {"act": 2, "review": 2}


def test_a_failing_pass_leaves_the_main_lists_and_logs(monkeypatch, caplog):
    ok = _build(monkeypatch, "exit", -1.0, alt_pass=lambda *a, **k: {})

    def raising(real):
        def act(*a, **k):
            if k.get("skip_breach"):
                raise RuntimeError("boom")
            return real(*a, **k)
        return act

    with caplog.at_level(logging.WARNING, logger=db._log.name):
        brief = _build(monkeypatch, "exit", -1.0, act_patch=raising)
    assert brief["breach_alternates"] is None
    assert brief["act_today"] == ok["act_today"]
    assert brief["review_list"] == ok["review_list"]
    assert any("breach-alternate pass failed" in r.getMessage() for r in caplog.records)


def test_skip_breach_and_macro_exclude_default_to_byte_identical_output(monkeypatch):
    kwargs, det = _scene("exit", -1.0)
    ts_ = held_news_check.news_window_start(_TODAY)
    common = dict(deterioration=det, news_window_start_ts=ts_.timestamp(),
                  news_window_label=held_news_check.window_label(ts_))
    args = (kwargs["port_df"], [], [], kwargs["news_items"], [], _TODAY)
    assert db._act_today(*args, **common) == db._act_today(*args, skip_breach=None, **common)
    assert db._act_today(*args, **common) == db._act_today(*args, skip_breach=set(), **common)
    act = db._act_today(*args, **common)
    rargs = (kwargs["port_df"], [], [], kwargs["held_data"], _TODAY)
    rkw = dict(portfolio_value=kwargs["portfolio_value"], deterioration=det,
               news_window_start_ts=common["news_window_start_ts"],
               news_window_label=common["news_window_label"])
    assert (db._review_list(*rargs, act_today=copy.deepcopy(act), **rkw)
            == db._review_list(*rargs, act_today=copy.deepcopy(act), macro_exclude=None, **rkw))


def test_macro_exclude_stops_the_pre_event_trim_picking_the_subject():
    rows = [_row("AAA", 12.9, score=50.0), _row("BBB", 20.0)]
    rows += [_row(f"F{i}", 5.0, sector="Energy") for i in range(6)]
    df = pd.DataFrame(rows)
    pv = 100 * _PRICE / 0.129
    events = [{"date": (_TODAY + timedelta(days=2)).isoformat(), "impact": MC_HIGH,
               "category": "Inflation", "event": "CPI", "affected_tickers": ["AAA", "BBB"]}]

    def trims(**kw):
        out = db._review_list(df, [], events, {}, _TODAY, portfolio_value=pv, act_today=[], **kw)
        return [(i["action"].get("trim_ticker"), i["action"]["type"]) for i in out
                if i["action"].get("trim_ticker")]

    assert [t for t, _ in trims()] == ["AAA"]                   # the natural pick
    assert "AAA" not in [t for t, _ in trims(macro_exclude={"AAA"})]
    assert "AAA" not in [t for t, _ in trims(macro_exclude={"aaa"})]    # case-insensitive


# ----------------------------------------------------- pure module: alternates

def test_breach_tickers_filters_and_uppercases():
    act = [_breach("aaa"), _item("sell_signal", "BBB"), "junk", None, _breach("CCC"),
           {"kind": "stop_breach", "ticker": None}]
    assert br.breach_tickers(act) == frozenset({"AAA", "CCC"})
    assert br.breach_tickers(None) == frozenset()
    assert br.breach_tickers("x") == frozenset()


def test_alternates_for_selects_subject_items_and_every_breach_is_present():
    act_alt = [_item("deterioration_exit"), _item("premortem_triggered"), _item("macro", None),
               _item("risk_off_derisk"), _item("critical_news", "BBB"), _item("risk")]
    rev_alt = [_review(), _review("BBB"), _review(None, trim_ticker="AAA"),
               {**_review(None), "ticker": "AAA", "action": {"type": "WATCH", "trim_ticker": "ZZZ"}}]
    out = br.alternates_for({"AAA", "CCC"}, act_alt, rev_alt)
    assert set(out) == {"AAA", "CCC"}
    assert [i["kind"] for i in out["AAA"]["act"]] == ["deterioration_exit", "risk"]
    assert len(out["AAA"]["review"]) == 1 and out["AAA"]["review"][0]["action"]["type"] == "TRIM_TO_TARGET"
    assert out["CCC"] == {"act": [], "review": []}


def test_alternates_for_copies_its_items_and_survives_none_inputs():
    src = [_item("deterioration_trim")]
    out = br.alternates_for({"AAA"}, src, None)
    out["AAA"]["act"][0]["why"] = "changed"
    assert src[0]["why"] == "w"
    assert br.alternates_for(set(), None, None) == {}


# ----------------------------------------------------------- pure module: augment

def test_augment_is_none_when_there_is_nothing_to_do():
    full = _brief(alts={"AAA": {"act": [], "review": []}})
    assert br.augment(full, set()) is None
    assert br.augment(None, {"AAA"}) is None
    assert br.augment("brief", {"AAA"}) is None
    assert br.augment(_brief(), {"AAA"}) is None                 # legacy Brief: no key


def test_augment_restores_in_lane_order_and_drops_the_resolved_breach():
    alts = {"aaa": {"act": [_item("deterioration_exit")], "review": [_review()]}}
    other = _item("sell_signal", "BBB")
    brief = _brief(act=[_breach(), other, _breach("CCC")], review=[_review("BBB")], alts=alts)
    out = br.augment(brief, {"AAA"})
    assert [(i["kind"], i["ticker"]) for i in out["act"]] == [
        ("sell_signal", "BBB"), ("stop_breach", "CCC"), ("deterioration_exit", "AAA")]
    assert out["act"][-1][br.RESTORED_KEY] is True and br.RESTORED_KEY not in out["act"][0]
    assert [i["ticker"] for i in out["review"]] == ["BBB", "AAA"]
    assert out["review"][-1][br.RESTORED_KEY] is True
    assert out["unchecked"] == [] and out["restored"] == ["AAA"]


def test_augment_empty_entry_is_checked_and_not_restored():
    out = br.augment(_brief(alts={"AAA": {"act": [], "review": []}}), {"AAA"})
    assert out["act"] == [] and out["review"] == []
    assert out["unchecked"] == [] and out["restored"] == []


@pytest.mark.parametrize("alts", [None, "x", [], {}, {"AAA": None}, {"AAA": {"act": []}},
                                  {"AAA": {"act": "no", "review": []}},
                                  {"AAA": {"act": [], "review": [1]}}, {"BBB": {"act": [], "review": []}}])
def test_augment_marks_a_bad_entry_unchecked(alts):
    out = br.augment(_brief(alts=alts), {"AAA"})
    assert out == {"act": None, "review": None, "unchecked": ["AAA"], "restored": []}


def test_augment_malformed_entry_leaves_other_tickers_restored():
    alts = {"AAA": {"act": "no", "review": []},
            "CCC": {"act": [_item("deterioration_trim", "CCC")], "review": []}}
    brief = _brief(act=[_breach(), _breach("CCC")], alts=alts)
    out = br.augment(brief, {"AAA", "CCC"})
    assert out["unchecked"] == ["AAA"] and out["restored"] == ["CCC"]
    assert [i["kind"] for i in out["act"]] == ["stop_breach", "deterioration_trim"]
    assert out["act"][0]["ticker"] == "AAA"                      # AAA's breach stays (not demoted away)


def test_augment_never_mutates_the_brief():
    brief = _brief(alts={"AAA": {"act": [_item("deterioration_exit")], "review": [_review()]}})
    before = copy.deepcopy(brief)
    out = br.augment(brief, {"AAA"})
    out["act"][0]["why"] = "x"
    out["review"][0]["why"] = "x"
    assert brief == before


# ------------------------------------------------------------------- 7 view

@pytest.mark.parametrize("gap", [-1.0, 0.0, M - 0.01, M, M + 0.01, None])
def test_view_restores_only_where_the_breach_is_recovered(gap):
    alts = {"AAA": {"act": [_item("deterioration_exit")], "review": []}}
    full, legacy = _brief(alts=alts), _brief()
    v, v_old = _view(full, gap), _view(legacy, gap)
    recovered = stop_recovery_state(gap, M) == "recovered"
    if recovered:
        assert v["restored"] == ["AAA"]
        assert [i["kind"] for i in v["active"]] == ["deterioration_exit"]
        assert v["active"][0][br.RESTORED_KEY] is True
        assert [i["kind"] for i in v["resolved"]] == ["stop_breach"]
    else:
        assert v["restored"] == [] and v["recovery_unchecked"] == []
        assert v == v_old                                        # identical to the pre-change view
        assert [i["kind"] for i in v["active"]] == ["stop_breach"]


def test_gap_exactly_at_the_margin_is_not_a_recovery():
    assert stop_recovery_state(M, M) != "recovered"
    v = _view(_brief(alts={"AAA": {"act": [_item("deterioration_exit")], "review": []}}), M)
    assert v["restored"] == [] and v["resolved"] == []


# ---------------------------------------------------------------- 8 re-breach

def test_recover_rebreach_recover_on_one_memoized_brief():
    brief = _brief(alts={"AAA": {"act": [_item("deterioration_exit")],
                                 "review": [_review(typ="PROTECTIVE_TRIM")]}})
    pristine = copy.deepcopy(brief)
    kinds = []
    for gap in (M + 1.0, -1.0, M + 1.0):
        v = _view(brief, gap)
        kinds.append((v["restored"], [i["kind"] if "kind" in i else "review" for i in v["active"]]))
        assert brief == pristine
    assert kinds[0] == kinds[2] == (["AAA"], ["deterioration_exit", "review"])
    assert kinds[1] == ([], ["stop_breach"])


# -------------------------------------------------------- 9 critical news

def test_restored_critical_news_lands_where_the_constant_says_and_is_not_folded():
    news = _item("critical_news", news_headline="AAA recalls", why="2 headlines ...")
    v = _view(_brief(alts={"AAA": {"act": [news], "review": []}}), M + 1.0)
    in_act = [i for i in v["active"] if i["kind"] == "critical_news"]
    in_aware = [i for i in v["aware"] if i.get("kind") == "critical_news"]
    assert (len(in_act), len(in_aware)) == ((1, 0) if BUCKET_CRITICAL_NEWS_IS_ACT else (0, 1))
    card = (in_act or in_aware)[0]
    assert "Also flagged on recent negative headlines" not in card["why"]
    assert card[br.RESTORED_KEY] is True
    assert not [i for i in v["resolved"] if i.get(br.RESTORED_KEY)]
    assert not [i for i in v["resolved"] if i.get("kind") != "stop_breach"]


# ------------------------------------------------------------ 10 legacy / bad

def test_legacy_brief_view_equals_the_old_behaviour():
    v = _view(_brief(), M + 1.0)
    assert v["active"] == [] and v["recovery_unchecked"] == [] and v["restored"] == []
    assert [i["kind"] for i in v["resolved"]] == ["stop_breach"]


def test_none_alternates_restore_nothing_and_report_the_name():
    v = _view(_brief(alts=None), M + 1.0)
    assert v["active"] == [] and v["restored"] == [] and v["recovery_unchecked"] == ["AAA"]
    assert [i["kind"] for i in v["resolved"]] == ["stop_breach"]


def test_malformed_entry_is_unchecked_while_another_recovered_name_restores():
    alts = {"AAA": {"act": "no", "review": []},
            "CCC": {"act": [_item("deterioration_trim", "CCC", trim_shares=3)], "review": []}}
    brief = _brief(act=[_breach(), _breach("CCC")], alts=alts)
    pe = pd.DataFrame({"Ticker": ["AAA", "CCC"], "Stop": [90.0, 90.0],
                       "Gap to Stop (%)": [M + 1.0, M + 1.0]})
    v = act_today_view(brief, False, pe, {}, M)
    assert v["recovery_unchecked"] == ["AAA"] and v["restored"] == ["CCC"]
    assert [i["ticker"] for i in v["active"]] == ["CCC"]
    assert sorted(i["ticker"] for i in v["resolved"]) == ["AAA", "CCC"]


def test_offline_view_carries_the_new_keys():
    v = act_today_view(None, False, None, {}, M)
    assert v["recovery_unchecked"] == [] and v["restored"] == []


# ------------------------------------------------ 11 DeferSizeBlind end to end

def test_recovered_trim_covers_by_its_shares_and_a_live_breach_is_an_exit():
    alts = {"AAA": {"act": [_item("deterioration_trim", trim_shares=3)], "review": []}}
    brief = _brief(alts=alts)
    rec = asc.view_sell_cover(_view(brief, M + 1.0))
    assert rec["AAA"]["shares"] == 3 and rec["AAA"]["exit"] is False
    live = asc.view_sell_cover(_view(brief, -1.0))
    assert live["AAA"] == {"exit": True, "shares": None}


def test_recovered_with_nothing_restored_is_not_sold():
    cover = asc.view_sell_cover(_view(_brief(alts={"AAA": {"act": [], "review": []}}), M + 1.0))
    assert cover == {}


# ------------------------------------------------------------- 12 text helpers

def test_restored_tag_cases():
    assert br.restored_tag(_item("deterioration_exit")) is None
    assert br.restored_tag({**_item("deterioration_exit"), br.RESTORED_KEY: False}) is None
    assert br.restored_tag({**_item("deterioration_exit"), br.RESTORED_KEY: 1}) is None
    assert br.restored_tag({**_item("x", None), br.RESTORED_KEY: True}) is None
    assert br.restored_tag(None) is None
    tag = br.restored_tag({**_item("deterioration_exit", "aaa"), br.RESTORED_KEY: True})
    assert tag == ("↩️ Shown because AAA's stop breach recovered (live price back above the stop); "
                   "this call was in today's Brief behind that sell.")
    assert br.restored_tag({**_review("bbb"), br.RESTORED_KEY: True}).startswith("↩️ Shown because BBB's")


_PLAIN = "Monitor the position; re-evaluate if price returns to or below the stop."


def test_resolved_card_line_three_branches_and_a_bad_view():
    sold = {"state": "act", "active": [_item("deterioration_exit")], "resolved": [_breach()],
            "recovery_unchecked": ["AAA"]}
    assert br.resolved_card_line(sold, "AAA") == (
        "AAA still has its own Act Today call above; follow that, not this resolved stop.")
    unch = {"state": "clear", "active": [], "resolved": [_breach()], "recovery_unchecked": ["aaa"]}
    assert br.resolved_card_line(unch, "AAA") == (
        "AAA's other checks (earnings week, low score, sell signals) couldn't be rebuilt after the "
        "stop recovered; open 📈 Analysis before trading AAA.")
    plain = {"state": "clear", "active": [], "resolved": [_breach()], "recovery_unchecked": []}
    assert br.resolved_card_line(plain, "AAA") == _PLAIN
    assert br.resolved_card_line({"state": "clear", "active": [], "resolved": []}, "AAA") == _PLAIN
    for bad in (None, "x", []):
        assert br.resolved_card_line(bad, "AAA") == _PLAIN
    assert br.resolved_card_line({"state": "offline", "active": [_item("deterioration_exit")]}, "AAA") == _PLAIN
    assert br.resolved_card_line(sold, "") == _PLAIN


def _resolved(*tickers):
    return [{**_breach(t), "_breach_state": "recovered"} for t in tickers]


def test_summary_caption_cases():
    base = "ℹ️ {n} stop breach(es) resolved — live price recovered above stop: {tks} → see 🏠 Home for detail."
    assert br.summary_resolved_caption(None) is None
    assert br.summary_resolved_caption({"resolved": []}) is None
    assert br.summary_resolved_caption({}) is None
    plain = {"state": "clear", "active": [], "resolved": _resolved("bbb", "AAA", "aaa"),
             "recovery_unchecked": []}
    assert br.summary_resolved_caption(plain) == base.format(n=3, tks="AAA, BBB")
    one = {"state": "act", "active": [_item("deterioration_exit")], "resolved": _resolved("AAA"),
           "recovery_unchecked": []}
    assert br.summary_resolved_caption(one) == (
        base.format(n=1, tks="AAA") + " AAA still has its own Act Today call.")
    two = {"state": "act", "active": [_item("deterioration_exit"), _item("sell_signal", "BBB")],
           "resolved": _resolved("AAA", "BBB"), "recovery_unchecked": []}
    assert br.summary_resolved_caption(two) == (
        base.format(n=2, tks="AAA, BBB") + " AAA, BBB still have their own Act Today call.")
    unch = {"state": "clear", "active": [], "resolved": _resolved("AAA", "BBB"),
            "recovery_unchecked": ["AAA", "BBB"]}
    assert br.summary_resolved_caption(unch) == (
        base.format(n=2, tks="AAA, BBB") + " AAA, BBB: other checks couldn't be rebuilt after the "
        "recovery; open 📈 Analysis before trading.")
    both = {"state": "act", "active": [_item("deterioration_exit", "AAA")],
            "resolved": _resolved("AAA", "BBB"), "recovery_unchecked": ["AAA", "BBB"]}
    assert br.summary_resolved_caption(both) == (
        base.format(n=2, tks="AAA, BBB") + " AAA still has its own Act Today call."
        " BBB: other checks couldn't be rebuilt after the recovery; open 📈 Analysis before trading.")


# ------------------------------------------------------------- 13 text rules

@pytest.mark.parametrize("t", ["AAA", "brk-b", "X"])
def test_no_dollar_bold_or_share_count_in_any_produced_text(t):
    view = {"state": "act", "active": [_item("deterioration_trim", t.upper(), trim_shares=7)],
            "resolved": _resolved(t.upper(), "ZZZ"), "recovery_unchecked": ["ZZZ"]}
    texts = [br.restored_tag({**_item("x", t), br.RESTORED_KEY: True}),
             br.resolved_card_line(view, t), br.resolved_card_line(view, "ZZZ"),
             br.resolved_card_line({"resolved": [], "active": []}, t),
             br.summary_resolved_caption(view)]
    for s in texts:
        assert s and "$" not in s and "**" not in s and "share" not in s.lower()
        assert "7" not in s


# ------------------------------------------------------------------ 14 pins

def test_synth_schema_version_is_32_and_keeps_the_breach_note_in_its_history():
    # BriefSkipSizeBlind bumped 31 -> 32; the BriefBreachRestore note sits in "was 31:".
    assert "_SYNTH_SCHEMA_VER = 32  # bumped: a larger same-name Review trim" in (
        (ROOT / "app.py").read_text(encoding="utf-8"))
    assert "was 31: the Brief result carries breach_alternates" in (
        (ROOT / "app.py").read_text(encoding="utf-8"))


def _string_constants(path):
    tree = parse_file_cached(path)
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_only_the_view_reads_the_alternates_key():
    assert br.ALTERNATES_KEY == "breach_alternates"
    for name in ("app.py", "cron_runner.py"):
        src = (ROOT / name).read_text(encoding="utf-8")
        assert "ALTERNATES_KEY" not in src, name
        assert "breach_alternates" not in _string_constants(ROOT / name), name
    allowed = {"daily_briefing.py", "breach_recovery.py"}
    for p in (ROOT / "stock_analyzer").glob("*.py"):
        if p.name in allowed:
            continue
        assert "breach_alternates" not in p.read_text(encoding="utf-8"), p.name
        assert "ALTERNATES_KEY" not in p.read_text(encoding="utf-8").replace(
            "breach_recovery.augment", ""), p.name


def test_breach_recovery_is_a_gate_file():
    sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
    try:
        import pre_tool_checks as p
    finally:
        sys.path.pop(0)
    assert "stock_analyzer/breach_recovery.py" in p._GATE_FILES


def test_app_wires_the_three_render_sites_through_the_module():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "breach_recovery.restored_tag(_item)" in src
    assert "breach_recovery.resolved_card_line(_act_view, _rb_tk)" in src
    assert "breach_recovery.summary_resolved_caption(_sm_act_view)" in src
    assert "Monitor the position; re-evaluate" not in src          # now only in the module


# ------------------------------------------------------------ 15 time context

def test_a_weekend_visit_with_the_build_price_never_restores():
    # live price == build price: the gap is still <= 0, so the breach stays active
    brief = _brief(alts={"AAA": {"act": [_item("deterioration_exit")], "review": []}})
    v = _view(brief, -1.0)
    assert v["restored"] == [] and v["resolved"] == []
    assert [i["kind"] for i in v["active"]] == ["stop_breach"]
    assert not any(br.RESTORED_KEY in i for i in v["active"] + v["aware"])
