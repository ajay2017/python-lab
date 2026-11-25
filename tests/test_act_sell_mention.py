"""U6b C1 (docs/plans/signals-catalyst-act-trim-mention-u6b.md, sections 2.1 and 7 items 1-6):
the pure gate that decides when 📡 Signals / 🔔 Catalyst Watch say a held name has a sell in
today's Act Today call. Pure-additive in C1 (nothing in app.py calls it yet), so these pin the
module and the end-to-end coordination through the real `build_daily_briefing`,
`act_today_view`, `portfolio.rebalance_actions` and `earnings_advisor.build_earnings_playbook`.

Items 7 (SURFACE_KEYS), 8 (app.py grep tests) and 9 (the C3 move) belong to C2 / C3."""
import copy
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import stock_analyzer.daily_briefing as db
from stock_analyzer import act_sell_mention as asm
from stock_analyzer import act_today_view as atv
from stock_analyzer import coord_freshness as cf
from stock_analyzer import earnings_advisor as ea
from stock_analyzer import held_trim_owner as hto
from stock_analyzer import signals_headline as sh
from stock_analyzer.constants import EARNINGS_OVERWEIGHT_TRIM_TO_PCT, STOP_RECOVERY_MARGIN_PCT
from stock_analyzer.portfolio import rebalance_actions

pytestmark = pytest.mark.fast

_TODAY = date(2026, 10, 26)            # a Monday
_ET = ZoneInfo("America/New_York")
_PRICE = 50.0
_BAD = ("$", "**", "<", ">")


@pytest.fixture(autouse=True)
def _tech_is_the_affected_sector(monkeypatch):
    monkeypatch.setattr("stock_analyzer.macro_calendar.affected_sectors", lambda _c: {"Tech"})


# -- helpers -------------------------------------------------------------------------

def _view(act=None, rev=None, gap=10.0, ticker="AAA"):
    """The real Act Today view over a hand-built Brief (stop recovery reads the live book)."""
    pe = pd.DataFrame([{"Ticker": ticker, "Gap to Stop (%)": gap, "Stop": 45.0}])
    brief = {"act_today": list(act or []), "review_list": list(rev or [])}
    return atv.act_today_view(brief, False, pe, {}, STOP_RECOVERY_MARGIN_PCT)


def _verified(view, **over):
    kw = dict(reduce_state="ready", session_brief_offline=False, freshness=cf.FRESH,
              built_date=_TODAY.isoformat(), today=_TODAY, view=view)
    kw.update(over)
    return asm.verified_sells(**kw)


def _sells_of(act=None, rev=None, gap=10.0):
    return _verified(_view(act, rev, gap))


# -- 1. verified_sells legs -----------------------------------------------------------

def test_all_legs_pass_returns_exactly_the_view_sell_set():
    v = _view(act=[{"kind": "deterioration_trim", "ticker": "AAA"}])
    assert _verified(v) == hto.view_sell_tickers(v) == {"AAA"}


def test_checked_and_empty_is_distinct_from_could_not_check():
    clear = _view()
    assert clear["state"] == "clear"
    got = _verified(clear)
    assert got == set() and got is not None


@pytest.mark.parametrize("over", [
    dict(reduce_state="missing"),
    dict(reduce_state="offline"),
    dict(reduce_state=None),
    dict(session_brief_offline=True),
    dict(freshness=cf.STALE),
    dict(freshness=cf.ABSENT),
    dict(freshness=None),
    dict(built_date=(_TODAY - timedelta(days=1)).isoformat()),
    dict(built_date=(_TODAY + timedelta(days=1)).isoformat()),
    dict(built_date=None),
    dict(built_date="garbage"),
    dict(built_date=""),
    dict(today=None),
])
def test_each_single_failing_leg_returns_none(over):
    v = _view(act=[{"kind": "deterioration_trim", "ticker": "AAA"}])
    assert _verified(v, **over) is None


@pytest.mark.parametrize("bad_view", [
    {"state": "offline", "active": [], "resolved": [], "aware": [], "n_active": None},
    None,
    "nope",
    [],
])
def test_an_offline_or_unusable_view_returns_none(bad_view):
    assert _verified(bad_view) is None


def test_a_real_offline_view_returns_none():
    v = atv.act_today_view(None, False, None, {}, STOP_RECOVERY_MARGIN_PCT)
    assert v["state"] == "offline"
    assert _verified(v) is None
    v2 = atv.act_today_view({"act_today": [], "review_list": []}, True, None, {},
                            STOP_RECOVERY_MARGIN_PCT)
    assert _verified(v2) is None


def test_built_date_may_be_a_date_object_and_today_a_datetime():
    v = _view(act=[{"kind": "sell_signal", "ticker": "AAA"}])
    assert _verified(v, built_date=_TODAY) == {"AAA"}
    assert _verified(v, today=datetime(2026, 10, 26, 9, 0, tzinfo=_ET)) == {"AAA"}


def test_verified_sells_does_not_mutate_its_view():
    v = _view(act=[{"kind": "deterioration_trim", "ticker": "AAA"}])
    before = copy.deepcopy(v)
    _verified(v)
    assert v == before


# -- 2. one notion of "Act Today sells T" (the real act_today_view) --------------------

@pytest.mark.parametrize("name,act,rev,gap", [
    ("risk attribution card only", [{"kind": "risk", "ticker": "AAA", "action": "RISK"}], [], 10.0),
    ("recovered stop breach", [{"kind": "stop_breach", "ticker": "AAA"}], [],
     STOP_RECOVERY_MARGIN_PCT + 5.0),
    ("U6 covered_by WATCH loser", [],
     [{"icon": "📅", "ticker": "AAA", "action": {"type": "WATCH", "covered_by": "AAA"}}], 10.0),
    ("critical-news hold", [{"kind": "critical_news", "ticker": "AAA", "action": "Watch"}], [], 10.0),
])
def test_these_are_not_a_sell(name, act, rev, gap):
    got = _sells_of(act, rev, gap)
    assert got == set(), name


@pytest.mark.parametrize("name,act,rev,gap", [
    ("weak-large TRIM_TO_TARGET", [],
     [{"icon": "🔍", "ticker": "AAA",
       "action": {"type": "TRIM_TO_TARGET", "trim_shares": 5, "target_weight": 8.0,
                  "reason_key": "weak_large"}}], 10.0),
    ("macro PROTECTIVE_TRIM keyed by trim_ticker", [],
     [{"icon": "🌐", "ticker": "", "action": {"type": "PROTECTIVE_TRIM", "trim_ticker": "AAA",
                                               "trim_shares": 3}}], 10.0),
    ("deterioration_trim", [{"kind": "deterioration_trim", "ticker": "AAA"}], [], 10.0),
    ("active stop breach", [{"kind": "stop_breach", "ticker": "AAA"}], [], -1.0),
    ("sell_signal", [{"kind": "sell_signal", "ticker": "AAA"}], [], 10.0),
    ("risk_off_derisk", [{"kind": "risk_off_derisk", "ticker": "AAA"}], [], 10.0),
    ("lower-case act ticker", [{"kind": "deterioration_trim", "ticker": "aaa"}], [], 10.0),
    ("lower-case trim_ticker", [],
     [{"icon": "🌐", "ticker": "", "action": {"type": "PROTECTIVE_TRIM", "trim_ticker": "aaa",
                                               "trim_shares": 3}}], 10.0),
])
def test_these_are_a_sell(name, act, rev, gap):
    assert _sells_of(act, rev, gap) == {"AAA"}, name


# -- 3. the Signals headline -----------------------------------------------------------

_CLEAR = {"kind": "clear", "text": sh.CLEAR_TEXT, "note": None}


def test_clear_with_a_sold_name_becomes_act_elsewhere():
    out = asm.signals_headline(_CLEAR, {"SNOW"}, [], set())
    assert out["line"] is None
    h = out["head"]
    assert h["kind"] == "act_elsewhere" and h["note"] is None
    assert h["text"] == ("ℹ️ No signal-driven trades on this page, but SNOW has a sell in "
                         "today's Act Today call (🏠 Home).")
    assert "✅" not in h["text"] and "well-balanced" not in h["text"]
    assert _CLEAR == {"kind": "clear", "text": sh.CLEAR_TEXT, "note": None}   # input not mutated


def test_clear_with_a_warning_count_text_also_becomes_act_elsewhere():
    head = {"kind": "clear", "note": None,
            "text": "✅ No signal-driven actions needed — 1 warning below to monitor."}
    out = asm.signals_headline(head, {"SNOW"}, [], set())
    assert out["head"]["kind"] == "act_elsewhere" and "✅" not in out["head"]["text"]


def test_clear_with_an_empty_set_is_unchanged():
    out = asm.signals_headline(_CLEAR, set(), [], set())
    assert out["head"] is _CLEAR and out["head"] == {"kind": "clear", "text": sh.CLEAR_TEXT,
                                                     "note": None}
    assert out["line"] is None


def test_clear_with_none_is_unchanged_and_has_no_line():
    out = asm.signals_headline(_CLEAR, None, [], set())
    assert out["head"] is _CLEAR and out["line"] is None


def test_clear_whose_only_sold_name_is_breach_named_stays_clear():
    out = asm.signals_headline(_CLEAR, {"SNOW"}, [], {"snow"})
    assert out["head"] is _CLEAR and out["line"] is None


@pytest.mark.parametrize("kind", ["act", "attention", "none", "resolved", "offline"])
def test_other_kinds_keep_their_head_and_name_sold_names_with_no_card(kind):
    head = {"kind": kind, "text": f"t-{kind}", "note": "n"}
    before = copy.deepcopy(head)
    out = asm.signals_headline(head, {"SNOW", "ABBV", "AAPL"}, {"aapl"}, {"abbv"})
    assert out["head"] is head and head == before           # same object, unchanged
    assert out["line"] == ("🏠 Also in today's Act Today call: SNOW has a sell (see 🏠 Home); "
                           "this page has no card of its own for it.")


@pytest.mark.parametrize("kind", ["act", "attention", "none", "resolved", "offline"])
def test_the_line_is_none_when_nothing_is_left_to_name(kind):
    head = {"kind": kind, "text": "t", "note": None}
    # every sold name has a card or is breach-named
    out = asm.signals_headline(head, {"AAA", "BBB"}, ["aaa"], ["BBB"])
    assert out["line"] is None and out["head"] is head
    # a None sells set names nothing, whatever the kind
    out = asm.signals_headline(head, None, [], [])
    assert out["line"] is None and out["head"] is head


def test_names_join_and_agree_in_number():
    one = asm.signals_headline(_CLEAR, {"SNOW"}, [], set())["head"]["text"]
    two = asm.signals_headline(_CLEAR, {"SNOW", "ABBV"}, [], set())["head"]["text"]
    three = asm.signals_headline(_CLEAR, {"SNOW", "ABBV", "AAPL"}, [], set())["head"]["text"]
    assert "but SNOW has a sell" in one
    assert "but ABBV and SNOW have a sell" in two
    assert "but AAPL, ABBV and SNOW have a sell" in three
    head = {"kind": "act", "text": "t", "note": None}
    l1 = asm.signals_headline(head, {"SNOW"}, [], [])["line"]
    l2 = asm.signals_headline(head, {"SNOW", "ABBV"}, [], [])["line"]
    l3 = asm.signals_headline(head, {"SNOW", "ABBV", "AAPL"}, [], [])["line"]
    assert "ABBV and SNOW have a sell" in l2 and "no card of its own for them." in l2
    assert "AAPL, ABBV and SNOW have a sell" in l3 and "for them." in l3
    assert "SNOW has a sell" in l1 and "for it." in l1


def test_headline_texts_have_no_dollar_bold_or_angle():
    head = {"kind": "act", "text": "t", "note": None}
    for out in (asm.signals_headline(_CLEAR, {"SNOW", "ABBV"}, [], []),
                asm.signals_headline(head, {"SNOW", "ABBV"}, [], [])):
        for text in (out["head"].get("text"), out["line"]):
            for bad in _BAD:
                assert bad not in (text or "")


# -- 4. card lines and tags --------------------------------------------------------------

_SELL_LINE = ("🏠 SNOW has a sell in today's Act Today call (see 🏠 Home for the size). Follow "
              "that call first; once it is logged this card is re-sized from your new book, so "
              "it is not a second sale on top of it.")


@pytest.mark.parametrize("card_type", ["trim", "review"])
def test_signals_trim_and_review_lines(card_type):
    assert asm.signals_card_line("SNOW", card_type, {"SNOW"}) == _SELL_LINE


def test_signals_add_line_and_unknown_type():
    assert asm.signals_card_line("SNOW", "add", {"SNOW"}) == (
        "🏠 SNOW has a sell in today's Act Today call (see 🏠 Home). Follow that call first; "
        "this card is not a reason to add to SNOW today.")
    other = asm.signals_card_line("SNOW", "mystery", {"SNOW"})
    assert other == ("🏠 SNOW has a sell in today's Act Today call (see 🏠 Home). Follow that "
                     "call first.")


@pytest.mark.parametrize("action", ["EXIT", "REDUCE"])
def test_catalyst_sell_actions_get_the_signals_sell_text(action):
    assert asm.catalyst_line("SNOW", action, {"SNOW"}) == _SELL_LINE


@pytest.mark.parametrize("action,label", [
    ("MONITOR", "Watch"), ("HOLD", "Hold"),
    ("SOMETHING_NEW", "earnings"), (None, "earnings"), (7, "earnings"),
])
def test_catalyst_other_actions_use_the_label(action, label):
    assert asm.catalyst_line("SNOW", action, {"SNOW"}) == (
        f"🏠 SNOW has a sell in today's Act Today call (see 🏠 Home for the size). Do that "
        f"first; this card's {label} plan applies to any shares you keep.")


def test_catalyst_hold_or_add_never_reads_as_a_reason_to_add():
    # Same clause 📡 Signals gives an add card on a sold name (Opus review 2026-11-25).
    assert asm.catalyst_line("SNOW", "HOLD_OR_ADD", {"SNOW"}) == (
        "🏠 SNOW has a sell in today's Act Today call (see 🏠 Home for the size). Do that "
        "first; this card's Hold or add plan applies to any shares you keep, and it is "
        "not a reason to add to SNOW today.")


def test_label_mapping_is_the_known_playbook_actions():
    assert asm.CATALYST_ACTION_LABELS == {"EXIT": "Exit", "REDUCE": "Reduce", "MONITOR": "Watch",
                                          "HOLD": "Hold", "HOLD_OR_ADD": "Hold or add"}


@pytest.mark.parametrize("sells", [None, set(), {"OTHER"}])
def test_no_line_and_no_tag_when_the_ticker_is_not_sold_or_unchecked(sells):
    assert asm.card_tag("SNOW", sells) == ""
    for ct in ("trim", "review", "add", "x"):
        assert asm.signals_card_line("SNOW", ct, sells) is None
    for act in ("EXIT", "REDUCE", "MONITOR", "HOLD", "HOLD_OR_ADD", "zzz"):
        assert asm.catalyst_line("SNOW", act, sells) is None


def test_tag_and_matching_are_case_insensitive_and_text_keeps_the_ticker_verbatim():
    assert asm.card_tag("snow", {"SNOW"}) == " · 🏠 Act Today sell"
    assert asm.card_tag(" SNOW ", frozenset({"SNOW"})) == " · 🏠 Act Today sell"
    assert asm.card_tag("", {"SNOW"}) == "" and asm.card_tag(None, {"SNOW"}) == ""
    assert asm.signals_card_line("snow", "trim", {"SNOW"}).startswith("🏠 snow has a sell")


def test_no_card_text_has_dollar_bold_or_angle():
    texts = [asm.card_tag("SNOW", {"SNOW"})]
    for ct in ("trim", "review", "add", "x"):
        texts.append(asm.signals_card_line("SNOW", ct, {"SNOW"}))
    for act in ("EXIT", "REDUCE", "MONITOR", "HOLD", "HOLD_OR_ADD", "zzz", None):
        texts.append(asm.catalyst_line("SNOW", act, {"SNOW"}))
    for surface in ("signals", "catalyst", "risk", "rebalancer", "nope"):
        texts.append(asm.unverified_caption(surface))
    texts.append(asm.unverified_caption("risk", "SNOW"))
    for t in texts:
        assert t
        for bad in _BAD:
            assert bad not in t, (bad, t)


def test_a_sells_collection_is_not_mutated_and_lists_are_accepted():
    sells = {"SNOW"}
    asm.card_tag("SNOW", sells)
    asm.signals_headline(_CLEAR, sells, ["SNOW"], ["SNOW"])
    assert sells == {"SNOW"}
    assert asm.card_tag("SNOW", ["snow"]) == " · 🏠 Act Today sell"
    assert asm.card_tag("SNOW", "SNOW") == ""            # a bare string is not a sell set


def test_unverified_captions():
    sc = asm.unverified_caption("signals")
    assert sc == asm.unverified_caption("catalyst") == (
        "⚠️ This page couldn't be checked against today's Act Today calls (🏠 Home's Act Today "
        "isn't available for the current book: not built yet, failed, out of date, or built on "
        "an earlier day), so a card here may sit beside an Act Today sell on the same name.")
    assert asm.unverified_caption("rebalancer") == (
        "⚠️ Trim rows below aren't checked against today's Act Today calls (🏠 Home's Act Today "
        "isn't available for the current book: not built yet, failed, out of date, or built on "
        "an earlier day), so a trim here may repeat a sell Act Today already sizes.")
    assert asm.unverified_caption("risk", "snow") == (
        "⚠️ Today's Act Today calls couldn't be checked against this card (🏠 Home's Act Today "
        "isn't available for the current book: not built yet, failed, out of date, or built on "
        "an earlier day), so it may repeat a sell Act Today already sizes for SNOW.")
    assert asm.unverified_caption("risk").endswith("already sizes for that name.")


def test_the_risk_caption_matches_the_c3_text_apart_from_the_date_clause():
    """held_trim_owner's unverified text is the C3 wording; ours adds only the date clause."""
    c3 = hto.risk_analysis_single_name_line("SNOW", None, {}, None, 0, None)["text"]
    ours = asm.unverified_caption("risk", "SNOW")
    assert ours.replace("out of date, or built on an earlier day", "or out of date") == c3


# -- 5. end to end: the real Brief, view, Signals actions and playbook --------------------

def _row(t, w, *, score=70.0, pnl=0.0, gap=10.0, shares=100, sector="Tech"):
    return {"Ticker": t, "Sector": sector, "Weight (%)": w, "Score": score,
            "Score Available": True, "P&L (%)": pnl, "Price": _PRICE, "Shares": shares,
            "Stop": _PRICE * 0.9, "Gap to Stop (%)": gap, "Stop Type": "ATR Stop",
            "Signal": "Hold", "Market Value": shares * _PRICE, "Avg Cost": _PRICE / 1.5}


def _book(w, *, score=50.0, pnl=0.0, gap=10.0, days=None, n=8, shares=100):
    """AAA (the subject) + BBB (Tech 20%) + Energy fillers = n positions."""
    rows = [_row("AAA", w, score=score, pnl=pnl, gap=gap, shares=shares), _row("BBB", 20.0)]
    rows += [_row(f"F{i}", 5.0, sector="Energy") for i in range(n - 2)]
    df = pd.DataFrame(rows)
    pv = shares * _PRICE / (w / 100.0)
    held = ({"AAA": {"earnings": (_TODAY + timedelta(days=days)).isoformat()}}
            if days is not None else {})
    return df, held, pv


def _act_trims(view, ticker="AAA"):
    """The Act Today view's Review-lane TRIM_TO_TARGET items for `ticker` (act items carry a
    string `action`, review items a dict)."""
    return [i for i in view["active"] if i.get("ticker") == ticker
            and isinstance(i.get("action"), dict) and i["action"].get("type") == "TRIM_TO_TARGET"]


def _e2e(w, *, days=None, det=False, **kw):
    """Brief -> Act Today view -> verified sells; Signals actions + headline; playbook."""
    df, held, pv = _book(w, days=days, **kw)
    if det:
        # a deterioration_trim is generated from price history inside the Brief; here the
        # Brief's own Review list is built the way the build does, with that Act item present
        # (so the earnings trim steps aside, U5) and the Act lane carrying the trim.
        act = [{"kind": "deterioration_trim", "ticker": "AAA", "priority": "high",
                "icon": "✂️", "action": "TRIM - Deterioration"}]
        rev = db._review_list(df, [], [], held, _TODAY, portfolio_value=pv, act_today=act)
        brief = {"act_today": act, "review_list": rev}
    else:
        brief = db.build_daily_briefing(
            port_df=df, alert_list=[], risk_recs=[], news_items=[], macro_events=[],
            held_data=held, scanner_results=None, portfolio_value=pv, today=_TODAY,
            market_context={"tone": "bull"})
    view = atv.act_today_view(brief, False, df, {}, STOP_RECOVERY_MARGIN_PCT)
    sells = _verified(view)
    ed = {"AAA": days} if days is not None else {}
    cards = rebalance_actions(df, earnings_days=ed)
    pb = {p["ticker"]: p for p in ea.build_earnings_playbook(df, held, today=_TODAY)}
    alerts = ([{"level": "warning", "ticker": "", "msg": "📅 AAA reports earnings soon"}]
              if days is not None else [])
    head = sh.decide(cards, alerts, False, brief_state="ok",
                     active_breach=frozenset(), resolved=frozenset())
    return dict(brief=brief, view=view, sells=sells, cards=cards, pb=pb, head=head)


def test_e2e_a_the_snow_case_headline_and_watch_line():
    """N=8, w 17.4, score 50, earnings in 7 days: the Brief issues the weak-large Act trim, 📡
    Signals has no card for the name, and its green headline is replaced."""
    r = _e2e(17.4, days=7, gap=5.0)                         # gap 5.0: the playbook reads MONITOR
    acts = _act_trims(r["view"])
    assert len(acts) == 1 and acts[0]["action"]["reason_key"] == "weak_large"
    assert r["sells"] == {"AAA"}
    assert r["cards"] == []                                  # no Signals card for the name
    assert r["head"]["kind"] == "clear" and "✅" in r["head"]["text"]
    out = asm.signals_headline(r["head"], r["sells"], [c["ticker"] for c in r["cards"]], set())
    assert out["head"]["kind"] == "act_elsewhere"
    assert out["head"]["text"] == ("ℹ️ No signal-driven trades on this page, but AAA has a sell "
                                   "in today's Act Today call (🏠 Home).")
    assert out["line"] is None
    pb = r["pb"]["AAA"]
    assert pb["action"] == "MONITOR"
    line = asm.catalyst_line("AAA", pb["action"], r["sells"])
    assert line.endswith("this card's Watch plan applies to any shares you keep.")
    assert asm.card_tag("AAA", r["sells"]) == " · 🏠 Act Today sell"


def test_e2e_b_earnings_and_weak_large_both_fire_and_the_act_trim_is_never_smaller():
    """w 19.0, score 50: Signals' earnings-week trim card (to 10%) and the Catalyst REDUCE both
    get the sell line; the one Act trim sells at least as many shares as Signals (the U6 proof;
    the two sizes differ and that is known and intended: the line names no size)."""
    r = _e2e(19.0, days=3)
    assert r["sells"] == {"AAA"}
    [card] = [c for c in r["cards"] if c["type"] == "trim" and c["ticker"] == "AAA"]
    assert card["target_weight"] == EARNINGS_OVERWEIGHT_TRIM_TO_PCT
    assert asm.signals_card_line("AAA", card["type"], r["sells"]).startswith(
        "🏠 AAA has a sell in today's Act Today call")
    pb = r["pb"]["AAA"]
    assert pb["action"] == "REDUCE"
    assert asm.catalyst_line("AAA", pb["action"], r["sells"]) is not None
    [act] = _act_trims(r["view"])
    assert act["action"]["trim_shares"] >= card["trim_shares"]
    # the card has its own headline, so the headline helper adds no "also" line for it
    head = {"kind": "none", "text": None, "note": None}
    assert asm.signals_headline(head, r["sells"], ["AAA"], set())["line"] is None
    for text in (asm.signals_card_line("AAA", "trim", r["sells"]),
                 asm.catalyst_line("AAA", "REDUCE", r["sells"])):
        assert "larger" not in text and "smaller" not in text and "shares" not in text


def test_e2e_c_no_weak_large_and_under_the_line_means_no_mention_anywhere():
    r = _e2e(17.4, days=3, score=55.0)
    assert r["sells"] == set() and r["sells"] is not None   # checked: Act Today sells nothing
    assert r["cards"] == []
    assert asm.card_tag("AAA", r["sells"]) == ""
    assert asm.signals_card_line("AAA", "trim", r["sells"]) is None
    assert asm.catalyst_line("AAA", r["pb"]["AAA"]["action"], r["sells"]) is None
    out = asm.signals_headline(r["head"], r["sells"], [], set())
    assert out["head"] is r["head"] and out["head"]["kind"] == "clear" and out["line"] is None


def test_e2e_d_earnings_on_day_8_with_the_weak_large_rule_still_mentions():
    """The weak-large sell is not earnings-gated, so a name 8 days out still has an Act sell."""
    r = _e2e(17.4, days=8)
    assert r["sells"] == {"AAA"}
    assert asm.card_tag("AAA", r["sells"]) == " · 🏠 Act Today sell"
    assert asm.catalyst_line("AAA", r["pb"]["AAA"]["action"], r["sells"]) is not None
    out = asm.signals_headline(r["head"], r["sells"], [c["ticker"] for c in r["cards"]], set())
    # no cards, only a warning: the headline would have stayed green over an Act sell
    assert r["head"]["kind"] == "clear" and out["head"]["kind"] == "act_elsewhere"


def test_e2e_e_a_deterioration_trim_name_in_the_window_gets_both_lines_with_no_size_claim():
    """The Act sale is the deterioration trim (the Brief's earnings trim steps aside, U5);
    Signals and Catalyst still size the earnings-week trim. Both lines appear and neither says
    which sale is larger."""
    r = _e2e(19.0, days=3, det=True, score=70.0)
    assert r["sells"] == {"AAA"}
    # no Brief earnings trim for the name (U5), but Signals still sizes one
    assert _act_trims(r["view"]) == []
    [card] = [c for c in r["cards"] if c["type"] == "trim" and c["ticker"] == "AAA"]
    sig = asm.signals_card_line("AAA", card["type"], r["sells"])
    cat = asm.catalyst_line("AAA", r["pb"]["AAA"]["action"], r["sells"])
    assert sig and cat
    for text in (sig, cat):
        for word in ("larger", "smaller", "bigger", "more than", "less than"):
            assert word not in text


# -- 6. time context ------------------------------------------------------------------------

def _sell_view():
    return _view(act=[{"kind": "deterioration_trim", "ticker": "AAA"}])


def test_et_evening_build_date_is_verified_when_the_utc_date_is_already_tomorrow():
    et_now = datetime(2026, 10, 26, 21, 0, tzinfo=_ET)         # 02:00 UTC on the 27th
    utc_date = et_now.astimezone(timezone.utc).date()
    et_date = et_now.date()
    assert utc_date == et_date + timedelta(days=1)
    v = _sell_view()
    # the memo was stamped with the ET date and today is the ET date: verified
    assert _verified(v, built_date=et_date.isoformat(), today=et_date) == {"AAA"}
    # the ET date governs, never the UTC one
    assert _verified(v, built_date=et_date.isoformat(), today=utc_date) is None
    assert _verified(v, built_date=utc_date.isoformat(), today=et_date) is None


def test_a_friday_build_seen_on_saturday_is_not_verified():
    fri, sat = date(2026, 10, 23), date(2026, 10, 24)
    assert fri.weekday() == 4 and sat.weekday() == 5
    assert _verified(_sell_view(), built_date=fri.isoformat(), today=sat) is None


def test_a_saturday_build_seen_on_saturday_is_verified():
    sat = date(2026, 10, 24)
    assert _verified(_sell_view(), built_date=sat.isoformat(), today=sat) == {"AAA"}


def test_a_sell_logged_after_the_build_makes_the_book_stale_so_nothing_is_mentioned():
    """Epoch +1 after the stamp (a trade logged or imported) classifies STALE; the helper then
    returns None (the caller shows the unverified caption), never a stale sell set."""
    stamps = {"_reduce_calls": 3}
    assert cf.classify(stamps, 3, "_reduce_calls") == cf.FRESH
    assert _verified(_sell_view(), freshness=cf.classify(stamps, 3, "_reduce_calls")) == {"AAA"}
    stale = cf.classify(stamps, 4, "_reduce_calls")
    assert stale == cf.STALE
    assert _verified(_sell_view(), freshness=stale) is None
    absent = cf.classify({}, 4, "_reduce_calls")
    assert absent == cf.ABSENT
    assert _verified(_sell_view(), freshness=absent) is None


def test_module_reads_no_clock_and_does_not_import_streamlit():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "stock_analyzer"
           / "act_sell_mention.py").read_text(encoding="utf-8")
    assert "import streamlit" not in src and "utcnow" not in src and "date.today" not in src
    assert "market_time" not in src
