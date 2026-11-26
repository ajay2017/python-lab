"""U6 C2 (docs/plans/brief-one-trim-per-name-u6.md): the Brief Review list issues at most ONE
Act trim per name, through the real `daily_briefing._review_list` / `build_daily_briefing`.
The pre-merge list comes from the same build with the merge monkeypatched to an identity.
Also pins the app.py render-only wiring (`_fmt_action` -> `covered_action_text`)."""
import copy
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import stock_analyzer.daily_briefing as db
from stock_analyzer import act_today_view as atv
from stock_analyzer import brief_trim_merge as btm
from stock_analyzer import decision_bucket as dbk
from stock_analyzer import act_sell_cover as asc
from stock_analyzer import held_trim_owner as hto
from stock_analyzer import trim_sizing as ts
from stock_analyzer.constants import (
    EARNINGS_IMMINENT_DAYS,
    LARGE_POSITION_WEIGHT_PCT,
    STOP_RECOVERY_MARGIN_PCT,
    WEAK_CONVICTION_SCORE,
)
from stock_analyzer.macro_calendar import HIGH as MC_HIGH

pytestmark = pytest.mark.fast

_TODAY = date(2026, 10, 26)            # a Monday
_PRICE = 50.0
_FORBIDDEN = ("sizes nothing", "already conservative", "no pre-event action", "no pre-event trim",
              "within tolerance", "position still has room", "only the stop is tightened")


def _row(t, w, *, score=70.0, pnl=0.0, gap=10.0, shares=100, sector="Tech", avail=True):
    return {"Ticker": t, "Sector": sector, "Weight (%)": w,
            "Score": score if avail else math.nan, "Score Available": avail,
            "P&L (%)": pnl, "Price": _PRICE, "Shares": shares, "Stop": _PRICE * 0.9,
            "Gap to Stop (%)": gap, "Stop Type": "ATR Stop", "Signal": "Hold",
            "Market Value": shares * _PRICE}


def _scn(w=17.4, *, score=50.0, pnl=0.0, gap=10.0, days=None, macro_days=None, shares=100,
         n=8, avail=True):
    """A book whose AAA row is the subject: AAA + BBB (Tech 20%) + fillers in Energy."""
    rows = [_row("AAA", w, score=score, pnl=pnl, gap=gap, shares=shares, avail=avail),
            _row("BBB", 20.0)]
    rows += [_row(f"F{i}", 5.0, sector="Energy") for i in range(n - 2)]
    df = pd.DataFrame(rows)
    pv = shares * _PRICE / (w / 100.0)
    held = ({"AAA": {"earnings": (_TODAY + timedelta(days=days)).isoformat()}}
            if days is not None else {})
    events = ([{"date": (_TODAY + timedelta(days=macro_days)).isoformat(), "impact": MC_HIGH,
                "category": "Inflation", "event": "CPI", "affected_tickers": ["AAA", "BBB"]}]
              if macro_days is not None else [])
    return df, held, events, pv


@pytest.fixture(autouse=True)
def _tech_is_the_affected_sector(monkeypatch):
    monkeypatch.setattr("stock_analyzer.macro_calendar.affected_sectors", lambda _c: {"Tech"})


def _list(scn, monkeypatch, *, merge=True, act=None, today=_TODAY):
    df, held, events, pv = scn
    if merge:
        return db._review_list(df, [], events, held, today, portfolio_value=pv, act_today=act)
    with monkeypatch.context() as m:
        m.setattr(db.brief_trim_merge, "one_trim_per_name", lambda items: items)
        return db._review_list(df, [], events, held, today, portfolio_value=pv, act_today=act)


def _acts(items, t="AAA"):
    return [i for i in items if (i["action"]["type"] in dbk._ACT_REVIEW_TYPES
                                 and dbk._ticker(i) == t)]


def _check_invariants(pre, post, where=""):
    pre_acts, post_acts = _acts(pre), _acts(post)
    assert len(post_acts) <= 1, where
    assert len(pre) == len(post), where                               # nothing removed
    assert all(not k.startswith("_u6_") for i in post for k in i), where
    if pre_acts:
        assert len(post_acts) == 1, where
        most = max(a["action"]["trim_shares"] for a in pre_acts)
        assert post_acts[0]["action"]["trim_shares"] == most, where   # never sells less
        assert post_acts[0]["action"] in [a["action"] for a in pre_acts], where   # unchanged dict
    else:
        assert post_acts == [], where
    assert hto.brief_sell_tickers([], pre) == hto.brief_sell_tickers([], post), where
    # DeferSizeBlind: the U6 merge never lowers what the Brief commits to selling on a name
    # (the winner is the largest sale), so the cover map is the same before and after
    assert asc.sell_cover_summary([], pre) == asc.sell_cover_summary([], post), where
    assert dbk.reduce_call_tickers([], pre) == dbk.reduce_call_tickers([], post), where
    assert (dbk.all_flagged_tickers([], pre, ignore_news=True)
            == dbk.all_flagged_tickers([], post, ignore_news=True)), where
    assert set(dbk.add_blocking_cards([], pre, set())) == set(dbk.add_blocking_cards([], post, set())), where
    if pre_acts:
        for i in post:
            if dbk._ticker(i) == "AAA" and "covered_by" in i["action"]:
                assert not any(b in i["why"] or b in i["trigger"] for b in _FORBIDDEN), (where, i)
                assert "$" not in i["why"] and "**" not in i["why"], where
            if i["icon"] == "🌐" and "covered_by" in i["action"]:
                assert "trim_ticker" not in i["action"], where


# -- 1. the pair / triple matrix --------------------------------------------------------

_MATRIX = {
    "E sized + W sized": dict(w=17.4, days=3),                                # 17.4 > floor 17.5? n=8
    "E standing + W sized": dict(w=17.4, days=3, n=6),
    "E unsized WATCH + W sized (SNOW)": dict(w=17.4, days=3, n=8),
    "E + W + lock": dict(w=17.4, days=3, gap=2.0, pnl=30.0, n=6),
    "W + lock": dict(w=12.0, gap=2.0, pnl=30.0),
    "W WATCH (pv unusable shares) + E sized": dict(w=17.6, days=3, score=50.0, shares=100),
    "W + macro": dict(w=12.9, macro_days=2),
    "W + macro + lock": dict(w=12.9, macro_days=2, gap=2.0, pnl=30.0),
    "E + macro": dict(w=20.0, days=3, macro_days=1, score=70.0),
    "E + W + lock + macro": dict(w=17.6, days=3, macro_days=3, gap=2.0, pnl=30.0),
    "tighten only + W": dict(w=12.0, gap=5.0, pnl=9.0),
    "tighten only + E": dict(w=20.0, days=3, gap=5.0, pnl=9.0, score=70.0, n=6),
    "no collision": dict(w=9.0, score=70.0),
}


@pytest.mark.parametrize("name", list(_MATRIX))
def test_matrix_one_act_trim_per_name(name, monkeypatch):
    scn = _scn(**_MATRIX[name])
    _check_invariants(_list(scn, monkeypatch, merge=False), _list(scn, monkeypatch), name)


def test_two_macro_events_on_one_name_leave_one_trim(monkeypatch):
    df, held, events, pv = _scn(w=17.4, score=70.0, macro_days=1)
    events = events + [{**events[0], "event": "NFP",
                        "date": (_TODAY + timedelta(days=3)).isoformat()}]
    scn = (df, held, events, pv)
    pre, post = _list(scn, monkeypatch, merge=False), _list(scn, monkeypatch)
    assert len(_acts(pre)) >= 2                       # the fixture really collides
    _check_invariants(pre, post, "macro x 2")
    [win] = _acts(post)
    assert win["event"] == "CPI"                      # tie: the earlier event date wins
    other = next(i for i in post if i["icon"] == "🌐" and i.get("event") == "NFP")
    assert other["action"]["covered_by"] == "AAA"


def test_the_snow_case_keeps_the_act_card_and_rewords_the_earnings_watch(monkeypatch):
    # N=8 -> earnings-week line 17.5; w=17.4 is under it (E unsized), score 50 -> weak-large trim
    scn = _scn(w=17.4, days=8 - 5, n=8)               # earnings in 3 days
    pre, post = _list(scn, monkeypatch, merge=False), _list(scn, monkeypatch)
    e_pre = next(i for i in pre if i["icon"] == "📅")
    assert e_pre["action"]["type"] == "WATCH" and "sizes nothing" in e_pre["why"]       # the live defect
    [win] = _acts(post)
    assert win["icon"] == "🔍"
    w_pre = next(i for i in pre if i["icon"] == "🔍")
    assert win == {k: v for k, v in w_pre.items() if not k.startswith("_u6_")}          # byte-identical
    e = next(i for i in post if i["icon"] == "📅")
    assert e["action"] == {"type": "WATCH", "covered_by": "AAA"}
    for bad in _FORBIDDEN + ("standing work", "re-enter"):
        assert bad not in e["why"] and bad not in e["trigger"]
    assert btm.covered_action_text(e["action"]) == (
        "covered by today's Act Today trim of AAA; no separate trade.")


# -- 2. seeded property test ---------------------------------------------------------------

def test_seeded_property_test(monkeypatch):
    rng = random.Random(20261124)
    seen_collision = 0
    for k in range(160):
        scn = _scn(w=round(rng.uniform(5, 30), 1),
                   score=rng.choice([40.0, 54.9, 55.0, 70.0]),
                   avail=rng.choice([True, True, True, False]),
                   gap=rng.choice([None, 2.9, 3.0, 3.1, 8.0]),
                   pnl=rng.choice([7.9, 8.0, 24.9, 25.0, 40.0]),
                   days=rng.choice([None, -1, 0, 3, 7, 8]),
                   macro_days=rng.choice([None, 1, 3]),
                   shares=rng.choice([100, 37.5]),
                   n=rng.choice([5, 8, 12]))
        pre, post = _list(scn, monkeypatch, merge=False), _list(scn, monkeypatch)
        _check_invariants(pre, post, f"k={k} {scn[0].iloc[0].to_dict()}")
        seen_collision += len(_acts(pre)) >= 2
    assert seen_collision >= 10                         # the generator really exercises collisions


def test_the_act_lane_is_never_touched(monkeypatch):
    act = [{"kind": "critical_news", "ticker": "AAA", "action": "Watch", "directive": "d",
            "why": "w", "weight": 17.4}, {"kind": "risk", "ticker": "AAA", "action": "RISK", "why": "r"}]
    before = copy.deepcopy(act)
    scn = _scn(w=17.4, days=3, macro_days=2, gap=2.0, pnl=30.0, n=6)
    _list(scn, monkeypatch, act=act)
    assert act == before


# -- 3. crossovers and tie-breaks (100 shares) ----------------------------------------------

def _w_shares(w):
    return ts.shares_to_trim(100, w, 8.0)


@pytest.mark.parametrize("w,winner", [(10.6, "📍"), (10.7, "📍"), (10.8, "📍"), (11.0, "🔍")])
def test_weak_large_vs_profit_lock_crossover(w, winner, monkeypatch):
    scn = _scn(w=w, gap=2.0, pnl=30.0)
    pre, post = _list(scn, monkeypatch, merge=False), _list(scn, monkeypatch)
    sizes = {i["icon"]: i["action"]["trim_shares"] for i in _acts(pre)}
    assert sizes["📍"] == 25 and sizes["🔍"] == _w_shares(w)
    [win] = _acts(post)
    assert win["icon"] == winner
    if winner == "📍":
        w_item = next(i for i in post if i["icon"] == "🔍")
        assert w_item["action"] == {"type": "WATCH", "covered_by": "AAA"}


@pytest.mark.parametrize("w,winner", [(12.9, "🌐"), (13.5, "🔍")])
def test_weak_large_vs_macro_crossover(w, winner, monkeypatch):
    scn = _scn(w=w, macro_days=2)
    pre, post = _list(scn, monkeypatch, merge=False), _list(scn, monkeypatch)
    assert {i["icon"] for i in _acts(pre)} == {"🔍", "🌐"}
    [win] = _acts(post)
    assert win["icon"] == winner
    _check_invariants(pre, post)


def test_earnings_trim_vs_profit_lock_and_ties_between_earnings_and_weak_large(monkeypatch):
    # E (to 10%) needs w above the earnings line (12.0 at N=15); it sells at least the
    # 25-share lock only from ~13.5 (floor(100 * (w - 10) / w) >= 26); a tie goes to the lock.
    for w, winner in ((13.0, "📍"), (13.5, "📍"), (13.6, "📅")):
        scn = _scn(w=w, days=3, n=15, score=70.0, gap=2.0, pnl=30.0)
        pre, post = _list(scn, monkeypatch, merge=False), _list(scn, monkeypatch)
        assert {i["icon"] for i in _acts(pre)} == {"📅", "📍"}, w
        assert _acts(post)[0]["icon"] == winner, w
        _check_invariants(pre, post, w)
    # tiny share counts: E and W floor to the same number -> the weak-large card wins the tie
    scn = _scn(w=17.6, days=3, n=6, shares=1, score=50.0)
    pre, post = _list(scn, monkeypatch, merge=False), _list(scn, monkeypatch)
    sizes = {i["icon"]: i["action"]["trim_shares"] for i in _acts(pre)}
    if len(sizes) == 2 and sizes["📅"] == sizes["🔍"]:
        assert _acts(post)[0]["icon"] == "🔍"
    _check_invariants(pre, post)


@pytest.mark.parametrize("w,score,expect_w", [
    (LARGE_POSITION_WEIGHT_PCT, WEAK_CONVICTION_SCORE - 0.1, True),
    (LARGE_POSITION_WEIGHT_PCT - 0.1, WEAK_CONVICTION_SCORE - 0.1, False),
    (12.0, WEAK_CONVICTION_SCORE, False),
    (12.0, WEAK_CONVICTION_SCORE - 0.1, True),
])
def test_weak_large_boundaries_still_decide_whether_the_card_exists(w, score, expect_w, monkeypatch):
    post = _list(_scn(w=w, score=score), monkeypatch)
    assert bool(_acts(post)) is expect_w


@pytest.mark.parametrize("gap,pnl,kind", [
    (3.0, 25.0, "TRIM_AND_TIGHTEN"), (3.1, 25.0, "TIGHTEN_ONLY"),
    (3.0, 24.9, "TIGHTEN_ONLY"),
])
def test_stop_boundaries_unchanged(gap, pnl, kind, monkeypatch):
    post = _list(_scn(w=9.0, score=70.0, gap=gap, pnl=pnl), monkeypatch)
    [stop] = [i for i in post if i["icon"] == "📍"]
    assert stop["action"]["type"] == kind


@pytest.mark.parametrize("w,expect", [(17.5, "WATCH"), (17.6, "TRIM_TO_TARGET")])
def test_earnings_line_boundary_n8(w, expect, monkeypatch):
    post = _list(_scn(w=w, days=3, n=8, score=70.0), monkeypatch)
    assert next(i for i in post if i["icon"] == "📅")["action"]["type"] == expect


# -- 4. no collision -> no change -------------------------------------------------------------

@pytest.mark.parametrize("scn_kw", [
    dict(w=9.0, score=70.0), dict(w=20.0, days=3, score=70.0, n=6),
    dict(w=12.0, score=50.0), dict(w=12.0, score=70.0, gap=2.0, pnl=30.0),
    dict(w=35.0, score=70.0, macro_days=2), dict(w=17.4, days=3, score=70.0, n=8),
])
def test_without_a_same_name_pair_the_list_equals_the_unmerged_one(scn_kw, monkeypatch):
    scn = _scn(**scn_kw)
    pre, post = _list(scn, monkeypatch, merge=False), _list(scn, monkeypatch)
    if len(_acts(pre)) >= 2 or any(i["action"]["type"] == "TIGHTEN_ONLY" and _acts(pre) for i in pre):
        pytest.skip("this fixture collides; covered by the matrix")
    assert post == [{k: v for k, v in i.items() if not k.startswith("_u6_")} for i in pre]


# -- 5. fail-safe -------------------------------------------------------------------------------

def test_a_raising_merge_keeps_the_unmerged_list_logs_and_strips_carriers(monkeypatch, caplog):
    scn = _scn(w=17.4, days=3, n=6, gap=2.0, pnl=30.0)
    pre = _list(scn, monkeypatch, merge=False)
    assert len(_acts(pre)) >= 2

    def boom(_items):
        raise RuntimeError("boom")
    monkeypatch.setattr(db.brief_trim_merge, "one_trim_per_name", boom)
    with caplog.at_level("WARNING"):
        post = db._review_list(scn[0], [], scn[2], scn[1], _TODAY, portfolio_value=scn[3])
    assert len(_acts(post)) == len(_acts(pre))                         # every sell stays visible
    assert all(not k.startswith("_u6_") for i in post for k in i)
    assert any("one-trim-per-name" in r.getMessage() for r in caplog.records)


# -- 7. wording, through the real list ----------------------------------------------------------------

def _rich(monkeypatch):
    """w=20 at N=8: earnings (50 sh), weak-large (60), profit-lock (25) and macro (25) all fire."""
    scn = _scn(w=20.0, days=3, macro_days=2, gap=2.0, pnl=30.0, n=8, score=50.0)
    pre, post = _list(scn, monkeypatch, merge=False), _list(scn, monkeypatch)
    assert {i["icon"] for i in _acts(pre)} == {"📅", "🔍", "📍", "🌐"}
    return pre, post


def test_wording_numbers_equal_the_action_fields(monkeypatch):
    pre, post = _rich(monkeypatch)
    sizes = {i["icon"]: i["action"]["trim_shares"] for i in _acts(pre)}
    [win] = _acts(post)
    m = win["action"]["trim_shares"]
    assert (m, sizes["📅"], sizes["📍"], sizes["🌐"]) == (60, 50, 25, 25)
    assert win["why"].endswith(
        "This sale also covers the profit-lock trim of 25% of the position (25 shares), the "
        "earnings-week trim to 10.0% (50 shares) and the CPI pre-event trim (25 shares); none of "
        "them sells more than these 60 shares.")
    e = next(i for i in post if i["icon"] == "📅")
    assert e["why"] == ("Today's Act Today trim of AAA sells 60 shares, no fewer than the 50 shares a "
                        "trim to 10.0% would sell, so earnings week adds no separate trade.")
    m_ = next(i for i in post if i["icon"] == "🌐")
    assert "(25 shares); today's Act Today trim of AAA sells 60 shares, no fewer" in m_["why"]
    st = next(i for i in post if i["icon"] == "📍")
    assert st["action"]["type"] == "TIGHTEN_ONLY" and "Today's Brief already says to reduce AAA" in st["why"]
    # a weak-large item is on the name: the covered earnings Trigger never advises a re-entry
    assert "re-enter" not in e["trigger"] and "Beat" not in e["trigger"]


def test_a_name_with_no_weak_large_item_keeps_the_reentry_clause_on_a_covered_earnings_card(monkeypatch):
    # N=15, w=13.0: score 70 -> no weak-large; the 25-share lock beats the 23-share earnings trim
    scn = _scn(w=13.0, days=3, n=15, score=70.0, gap=2.0, pnl=30.0)
    post = _list(scn, monkeypatch)
    [win] = _acts(post)
    assert win["icon"] == "📍"
    e = next(i for i in post if i["icon"] == "📅")
    assert e["action"] == {"type": "WATCH", "covered_by": "AAA"}
    assert e["trigger"].startswith("Beat → re-enter post-print if composite holds; ")


def test_the_covered_weak_large_card_prints_the_floored_score_never_55_lt_55(monkeypatch):
    # profit-lock (25 sh) beats the weak-large trim at w=10.6 (24 sh); score 54.95 reads 54.9
    scn = _scn(w=10.6, score=54.95, gap=2.0, pnl=30.0)
    post = _list(scn, monkeypatch)
    w = next(i for i in post if i["icon"] == "🔍")
    assert w["action"] == {"type": "WATCH", "covered_by": "AAA"}
    assert "score 54.9 < 55." in w["why"] and "55 < 55" not in w["why"]


# -- 8. time context ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("days,has_e", [(0, True), (EARNINGS_IMMINENT_DAYS, True),
                                        (EARNINGS_IMMINENT_DAYS + 1, False), (-1, False)])
def test_earnings_window_edges(days, has_e, monkeypatch):
    post = _list(_scn(w=17.4, days=days, n=8), monkeypatch)
    assert any(i["icon"] == "📅" for i in post) is has_e
    if not has_e:                         # day 8: today's single weak-large card, unchanged
        [win] = _acts(post)
        assert win["icon"] == "🔍" and "also covers" not in win["why"]


def test_saturday_visit_with_a_monday_report_is_two_days(monkeypatch):
    sat = date(2026, 11, 28)
    df, _h, events, pv = _scn(w=17.4)
    held = {"AAA": {"earnings": "2026-11-30"}}
    post = db._review_list(df, [], events, held, sat, portfolio_value=pv)
    e = next(i for i in post if i["icon"] == "📅")
    assert "in 2d" in e["headline"] and e["action"] == {"type": "WATCH", "covered_by": "AAA"}


@pytest.mark.parametrize("mdays", [1, 3])
def test_macro_on_days_one_and_three_is_merged(mdays, monkeypatch):
    scn = _scn(w=12.9, macro_days=mdays)
    _check_invariants(_list(scn, monkeypatch, merge=False), _list(scn, monkeypatch), mdays)
    assert [i for i in _list(scn, monkeypatch) if i["icon"] == "🌐"]
    for none_day in (0, 4):
        assert not [i for i in _list(_scn(w=12.9, macro_days=none_day), monkeypatch)
                    if i["icon"] == "🌐"]


def test_an_et_evening_today_gives_the_same_list_as_the_plain_date(monkeypatch):
    evening = datetime(2026, 10, 26, 21, 0, tzinfo=ZoneInfo("America/New_York")).date()
    scn = _scn(w=17.4, days=3, macro_days=2, gap=2.0, pnl=30.0, n=6)
    assert _list(scn, monkeypatch, today=evening) == _list(scn, monkeypatch)


# -- 9. coordination through build_daily_briefing ------------------------------------------------------------

def _brief(monkeypatch, *, merge=True, **kw):
    df, held, events, pv = _scn(**kw)
    risk = [{"type": "single_name_concentration", "priority": "HIGH", "title": "AAA", "problem": "p",
             "recommendation": "Trim AAA toward the 15% ceiling", "root_tickers": [{"ticker": "AAA"}]}]
    args = dict(port_df=df, alert_list=[], risk_recs=risk, news_items=[], macro_events=events,
                held_data=held, scanner_results=None, portfolio_value=pv, today=_TODAY,
                market_context={"tone": "bull"})
    if merge:
        return db.build_daily_briefing(**args)
    with monkeypatch.context() as m:
        m.setattr(db.brief_trim_merge, "one_trim_per_name", lambda items: items)
        return db.build_daily_briefing(**args)


def test_build_end_to_end_coordination(monkeypatch):
    kw = dict(w=17.6, days=3, n=6, gap=2.0, pnl=30.0, macro_days=2)
    pre, post = _brief(monkeypatch, merge=False, **kw), _brief(monkeypatch, **kw)
    n_pre = len(_acts(pre["review_list"]))
    assert n_pre >= 3 and len(_acts(post["review_list"])) == 1
    # one Act item per subject in the split Act bucket
    split = dbk.split_defensive(post["act_today"], post["review_list"])
    assert len([i for i in split["act"] if i.get("_source") == "review"
                and dbk._ticker(i) == "AAA" and dbk._is_reduce(i)]) == 1
    # the Act Today count drops by exactly the extra trims on the name
    v_pre = atv.act_today_view(pre, False, None, {}, STOP_RECOVERY_MARGIN_PCT)
    v_post = atv.act_today_view(post, False, None, {}, STOP_RECOVERY_MARGIN_PCT)
    assert v_pre["n_active"] - v_post["n_active"] == n_pre - 1
    # the first reduce item per ticker is now the winner
    [win] = _acts(post["review_list"])
    assert dbk.reduce_call_items(post["act_today"], post["review_list"])["AAA"]["action"] == win["action"]
    # sets unchanged
    assert hto.brief_sell_tickers(pre["act_today"], pre["review_list"]) == \
        hto.brief_sell_tickers(post["act_today"], post["review_list"])
    assert asc.sell_cover_summary(pre["act_today"], pre["review_list"]) == \
        asc.sell_cover_summary(post["act_today"], post["review_list"])
    assert dbk.reduce_call_tickers(pre["act_today"], pre["review_list"]) == \
        dbk.reduce_call_tickers(post["act_today"], post["review_list"])
    assert set(pre["portfolio_tuneup_deferred"]) == set(post["portfolio_tuneup_deferred"])
    assert [c["type"] for c in pre["portfolio_tuneup"]] == [c["type"] for c in post["portfolio_tuneup"]]
    # the macro card stood down; AAA is listed as "its own Act Today call not listed"
    macro_item = next(i for i in post["review_list"] if i["icon"] == "🌐")
    assert macro_item["action"].get("covered_by") == "AAA"
    assert "(1 affected name with its own Act Today call not listed)" in macro_item["headline"]


def test_a_critical_news_card_folds_onto_the_single_winner(monkeypatch):
    scn = _scn(w=17.6, days=3, n=6, gap=2.0, pnl=30.0)
    post = _list(scn, monkeypatch)
    news = {"kind": "critical_news", "ticker": "AAA", "icon": "🚨", "action": "Watch",
            "directive": "Hold for now", "why": "2 headlines", "trigger": "t", "weight": 17.6,
            "news_headline": "AAA recalls product"}
    split = dbk.split_defensive([news], post)
    cards = [i for i in split["act"] if dbk._ticker(i) == "AAA"]
    assert not [i for i in cards if i.get("kind") == "critical_news"]
    noted = [i for i in cards if "negative headlines" in i.get("why", "")]
    assert len(noted) == 1 and noted[0]["action"]["type"] in dbk._ACT_REVIEW_TYPES
    watchers = [i for i in post if "covered_by" in i["action"]]
    assert watchers and all("negative headlines" not in i["why"] for i in watchers)


# -- 10 / 11. app.py wiring (render-only) ----------------------------------------------------------------------

_APP = Path(__file__).resolve().parent.parent / "app.py"


def test_fmt_action_routes_covered_watch_through_the_pure_text():
    src = _APP.read_text(encoding="utf-8")
    start = src.index("def _fmt_action(")
    body = src[start:src.index("def _render_holdings_table", start)]
    assert "brief_trim_merge.covered_action_text(action)" in body
    assert "from stock_analyzer import brief_trim_merge" in src
    # render-only: no comparison against a constant, no sentence of its own for the covered case
    assert "covered by today's Act Today trim" not in body
    watch = body[body.index('if t == "WATCH":'):body.index('if t == "TIGHTEN_ONLY":')]
    assert "no action today" in watch                        # the default text is unchanged
    assert watch.index("covered_action_text") < watch.rindex("no action today")


def test_fmt_action_routes_covered_deterioration_watch_through_the_pure_text():
    src = _APP.read_text(encoding="utf-8")
    start = src.index("def _fmt_action(")
    body = src[start:src.index("def _render_holdings_table", start)]
    det = body[body.index('if t == "DETERIORATION_WATCH":'):body.index('return (_HOME_CALM, "—"')]
    assert "brief_trim_merge.covered_action_text(action)" in det
    assert "no action today" in det                          # the default text is unchanged
    assert det.index("covered_action_text") < det.rindex("no action today")
    assert "covered by today's Act Today trim" not in det    # render-only
