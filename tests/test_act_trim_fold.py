"""BriefSkipSizeBlind Batch 2, B-C1 + B-C2 (docs/plans/brief-skip-size-blind-b1b2-spec.md):
a larger same-name Review trim folds into the Act Today card's `min_trim`.

Covers the pure module `act_trim_fold`, the `_review_list` tagging + fold wiring, the
`act_sell_cover` read of `min_trim` on a deterioration trim, and the app.py pins.
Every threshold is read from `constants.py`."""
import ast
import copy
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

import stock_analyzer.daily_briefing as db
from stock_analyzer import act_sell_cover as asc
from stock_analyzer import act_trim_fold as fold_mod
from stock_analyzer import decision_bucket as dbk
from stock_analyzer import trim_sizing as _ts
from stock_analyzer.constants import (
    DETERIORATION_TRIM_SUGGESTED_PCT,
    EARNINGS_OVERWEIGHT_TRIM_TO_PCT,
    LARGE_POSITION_WEIGHT_PCT,
    STOP_PROFIT_LOCK_PNL_PCT,
    STOP_PROFIT_LOCK_TRIM_PCT,
    STOP_RAISE_MIN_STEP_PCT,
    STOP_TIGHTEN_ATR_MULT,
    WEAK_CONVICTION_SCORE,
    WEAK_LARGE_TRIM_TO_PCT,
)
from tests.conftest import make_port_df

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
_TODAY = date(2026, 7, 27)
_SIZED_TYPES = ("TRIM_AND_TIGHTEN", "TRIM_TO_TARGET")


# ------------------------------------------------------------------ pure fixtures

_UR_WHY = ("Gap 2.0% above stop; P&L +30.0%. Today's Brief already says to reduce AAA, "
           "so this only tightens the stop on the shares you keep.")


def _stop(lock, shares=25, new_stop=95.0, gap=2.0, pnl=30.0, tag=True, tighten="ok"):
    action = ({"type": "TRIM_AND_TIGHTEN", "trim_shares": shares,
               "trim_dollars": shares * 100.0, "trim_pct": STOP_PROFIT_LOCK_TRIM_PCT,
               "new_stop": new_stop}
              if lock else {"type": "TIGHTEN_ONLY", "new_stop": new_stop})
    it = {"priority": "medium", "icon": "📍", "ticker": "AAA", "headline": "gap 2.0%",
          "action": action, "why": "original stop why", "trigger": "t", "weight": 14.0,
          "lifecycle": "winning"}
    if tighten == "ok":
        it["_u6_tighten"] = {"new_stop": new_stop, "why": _UR_WHY}
    elif tighten == "no_why":
        it["_u6_tighten"] = {"new_stop": new_stop}
    if tag:
        it.update({fold_mod.FOLD_KEY: "AAA", "_fold_gap": gap, "_fold_pnl": pnl})
    return it


def _earn(shares, w=14.0, target=10.0, label="in 3d", tag=True):
    it = {"priority": "medium", "icon": "📅", "ticker": "AAA",
          "headline": f"earnings {label}",
          "action": {"type": "TRIM_TO_TARGET", "trim_shares": shares,
                     "trim_dollars": shares * 100.0 if isinstance(shares, int) else None,
                     "from_weight": w, "target_weight": target, "reason_key": "earnings"},
          "why": "earnings why", "trigger": "t", "weight": w, "_u6_kind": "earnings"}
    if tag:
        it.update({fold_mod.FOLD_KEY: "AAA", "_fold_label": label})
    return it


def _weak(shares, w=14.0, score=40.0, tag=True, with_score=True):
    it = {"priority": "medium", "icon": "🔍", "ticker": "AAA", "headline": "largest weak",
          "action": {"type": "TRIM_TO_TARGET", "trim_shares": shares,
                     "trim_dollars": shares * 100.0 if isinstance(shares, int) else None,
                     "from_weight": w, "target_weight": WEAK_LARGE_TRIM_TO_PCT,
                     "reason_key": "weak_large"},
          "why": "weak why", "trigger": "t", "weight": w, "_u6_kind": "weak_large"}
    if with_score:
        it["_u6_score"] = score
    if tag:
        it[fold_mod.FOLD_KEY] = "AAA"
    return it


def _watch(kind, tag=True):
    it = {"priority": "low", "icon": "x", "ticker": "AAA", "headline": f"{kind} watch",
          "action": {"type": "WATCH"}, "why": "w", "trigger": "t", "weight": 14.0,
          "_u6_kind": kind}
    if tag:
        it[fold_mod.FOLD_KEY] = "AAA"
    return it


def _floor(n=25, dd=12.0):
    return {"reason_key": "deterioration_floor", "trim_shares": n,
            "trim_dollars": n * 100.0 if isinstance(n, (int, float)) else None,
            "from_weight": 14.0, "target_weight": None, "_fold_dd": dd}


def _act(kind, n=None, t="AAA"):
    d = {"ticker": t, "kind": kind, "priority": "high", "directive": "D.", "why": "W.",
         "reason": "D. W."}
    if n is not None:
        d["trim_shares"] = n
    return d


def _other():
    return {"priority": "low", "icon": "x", "ticker": "BBB", "headline": "other",
            "action": {"type": "WATCH"}, "why": "w", "trigger": "t", "weight": 3.0}


def _no_fold_keys(items):
    for it in items:
        assert not [k for k in it if isinstance(k, str) and k.startswith("_fold_")]


# ------------------------------------------------------------------ 1. fold matrix

_SIZES = {"lock": 25, "earnings": 30, "weak_large": 40, "deterioration_floor": 25}
_RANK = {"lock": 0, "weak_large": 1, "earnings": 2, "deterioration_floor": 3}
_SETS = [(), ("lock",), ("earnings",), ("weak_large",), ("earnings", "weak_large"),
         ("lock", "earnings", "weak_large"), ("deterioration_floor",),
         ("lock", "deterioration_floor"), ("earnings", "deterioration_floor")]


def _build_case(names):
    items = [_stop("lock" in names), _other()]
    if "earnings" in names:
        items.append(_earn(_SIZES["earnings"]))
    if "weak_large" in names:
        items.append(_weak(_SIZES["weak_large"]))
    extra = {"AAA": [_floor(_SIZES["deterioration_floor"])]} if "deterioration_floor" in names else None
    return items, extra


def _oracle(names, n_act):
    cands = [(n, _SIZES[n]) for n in names]
    if not cands:
        return None
    reason, m = min(cands, key=lambda c: (-c[1], _RANK[c[0]]))
    if n_act is not None and n_act >= m:
        return None
    return reason, m


@pytest.mark.parametrize("names", _SETS)
@pytest.mark.parametrize("act_kind,n_act", [("deterioration_trim", None), ("deterioration_trim", 0),
                                            ("deterioration_trim", 25), ("sell_signal", None)])
def test_fold_matrix(names, act_kind, n_act):
    if act_kind == "deterioration_trim" and "deterioration_floor" in names:
        pytest.skip("the floor is only a candidate under a sell_signal")
    items, extra = _build_case(names)
    before = copy.deepcopy(items)
    act = [_act(act_kind, n_act)]
    out, payloads = fold_mod.fold(items, act, extra_candidates=extra)
    assert items == before                                   # inputs never mutated
    want = _oracle(names, n_act if act_kind == "deterioration_trim" else None)
    if want is None:
        assert payloads == {}
    else:
        reason, m = want
        assert set(payloads) == {"AAA"}
        assert payloads["AAA"]["reason_key"] == reason
        assert payloads["AAA"]["trim_shares"] == m
        assert set(payloads["AAA"]) == {"trim_shares", "trim_dollars", "from_weight",
                                        "target_weight", "reason_key", "directive_tail",
                                        "why_tail"}
        if reason in ("lock", "deterioration_floor"):
            assert payloads["AAA"]["target_weight"] is None
    # T's tagged cards read as today's reduced-name output: one stop-only card, nothing else.
    t_items = [i for i in out if i.get("ticker") == "AAA"]
    assert len(t_items) == 1
    assert t_items[0]["action"] == {"type": "TIGHTEN_ONLY", "new_stop": 95.0}
    assert t_items[0]["why"] == _UR_WHY
    assert [i for i in out if i.get("ticker") == "BBB"] == [_other()]
    _no_fold_keys(out)


def test_tie_order_is_lock_then_weak_large_then_earnings_then_floor():
    items = [_stop(True, shares=30), _earn(30), _weak(30)]
    _, payloads = fold_mod.fold(items, [_act("sell_signal")],
                                extra_candidates={"AAA": [_floor(30)]})
    assert payloads["AAA"]["reason_key"] == "lock"
    _, payloads = fold_mod.fold([_stop(False), _earn(30), _weak(30)], [_act("sell_signal")],
                                extra_candidates={"AAA": [_floor(30)]})
    assert payloads["AAA"]["reason_key"] == "weak_large"
    _, payloads = fold_mod.fold([_stop(False), _earn(30)], [_act("sell_signal")],
                                extra_candidates={"AAA": [_floor(30)]})
    assert payloads["AAA"]["reason_key"] == "earnings"


def test_the_decision_does_not_depend_on_input_order():
    items = [_stop(True), _earn(30), _weak(40), _watch("earnings"), _other()]
    a = fold_mod.fold(items, [_act("sell_signal")])
    b = fold_mod.fold(list(reversed(items)), [_act("sell_signal")])
    assert a[1] == b[1]
    assert sorted(map(repr, a[0])) == sorted(map(repr, b[0]))


def test_a_tagged_watch_is_dropped_and_an_untagged_item_passes_through():
    items = [_stop(False), _watch("earnings"), _watch("weak_large"), _other()]
    out, payloads = fold_mod.fold(items, [_act("deterioration_trim", 25)])
    assert payloads == {}
    assert [i["icon"] for i in out] == ["📍", "x"]


def test_a_tagged_tighten_only_card_gets_the_under_reduce_why():
    out, _ = fold_mod.fold([_stop(False)], [_act("sell_signal")])
    assert out[0]["why"] == _UR_WHY
    assert out[0]["action"] == {"type": "TIGHTEN_ONLY", "new_stop": 95.0}


# ------------------------------------------------------------------ act_item_for / apply

def test_act_item_for_never_picks_a_card_that_does_not_sell():
    prem = _act("premortem_triggered")
    sell = _act("sell_signal")
    assert fold_mod.act_item_for([prem, sell], "aaa") is sell
    assert fold_mod.act_item_for([prem, _act("critical_news"), _act("risk")], "AAA") is None
    assert fold_mod.act_item_for([_act("stop_breach")], "AAA") is None
    assert fold_mod.act_item_for([sell], "") is None
    assert fold_mod.act_item_for(None, "AAA") is None


def test_apply_payload_grows_the_card_and_refreshes_reason():
    act = _act("sell_signal")
    _, payloads = fold_mod.fold([_stop(False), _weak(40)], [act])
    assert "min_trim" not in act                             # fold itself never writes
    assert fold_mod.apply_payload(act, payloads["AAA"]) is True
    assert act["min_trim"] == {k: payloads["AAA"][k] for k in (
        "trim_shares", "trim_dollars", "from_weight", "target_weight", "reason_key")}
    assert act["directive"].startswith("D. At minimum, trim")
    assert act["why"].startswith("W. Also your largest weak holding")
    assert act["reason"] == f"{act['directive']} {act['why']}"


def test_apply_payload_changes_nothing_on_a_bad_input():
    act = _act("sell_signal")
    before = copy.deepcopy(act)
    assert fold_mod.apply_payload(None, {"trim_shares": 1}) is False
    assert fold_mod.apply_payload(act, None) is False
    assert act == before


# ------------------------------------------------------------------ 4. fail-safes

@pytest.mark.parametrize("bad", [0, 1.5, True, float("nan"), "2", None, -3])
def test_an_invalid_candidate_count_leaves_the_subject_as_built(bad):
    items = [_stop(True), _earn(bad), _weak(40), _watch("earnings")]
    out, payloads = fold_mod.fold(items, [_act("sell_signal")])
    assert payloads == {}
    _no_fold_keys(out)
    assert [i["icon"] for i in out] == [i["icon"] for i in items]
    assert out[0]["action"]["type"] == "TRIM_AND_TIGHTEN"    # the lock card is not rewritten
    assert out[1]["action"]["type"] == "TRIM_TO_TARGET"


@pytest.mark.parametrize("bad", [0, 1.5, True, float("nan"), "2"])
def test_an_invalid_extra_candidate_count_leaves_the_subject_as_built(bad):
    out, payloads = fold_mod.fold([_stop(False), _weak(40)], [_act("sell_signal")],
                                  extra_candidates={"AAA": [_floor(bad)]})
    assert payloads == {}
    assert [i["action"]["type"] for i in out] == ["TIGHTEN_ONLY", "TRIM_TO_TARGET"]
    assert out[0]["why"] == "original stop why"


def test_no_partial_act_item_leaves_the_subject_as_built():
    items = [_stop(True), _earn(30)]
    for act in ([], [_act("premortem_triggered")], [_act("stop_breach")], [_act("risk")]):
        out, payloads = fold_mod.fold(items, act)
        assert payloads == {}
        assert [i["action"]["type"] for i in out] == ["TRIM_AND_TIGHTEN", "TRIM_TO_TARGET"]
        _no_fold_keys(out)


@pytest.mark.parametrize("mode", ["missing", "no_why"])
def test_a_stop_card_without_a_usable_carrier_leaves_the_subject_as_built(mode):
    for lock in (True, False):
        items = [_stop(lock, tighten=mode), _earn(30)]
        out, payloads = fold_mod.fold(items, [_act("sell_signal")])
        assert payloads == {}
        assert [i["action"]["type"] for i in out] == [items[0]["action"]["type"], "TRIM_TO_TARGET"]


def test_a_raising_fold_keeps_every_card_strips_carriers_and_logs(monkeypatch, caplog):
    df, pv = _book(20.0, score=40.0)
    act = [_act("sell_signal")]
    held = {"AAA": {"earnings": (_TODAY + timedelta(days=3)).isoformat()}}

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(fold_mod, "fold", boom)
    with caplog.at_level(logging.WARNING):
        items = db._review_list(df, [], [], held, _TODAY, portfolio_value=pv, act_today=act)
    assert "min_trim" not in act[0]
    assert "brief act-trim fold failed" in caplog.text
    t = [i for i in items if i.get("ticker") == "AAA"]
    assert [i for i in t if i["action"]["type"] == "TRIM_TO_TARGET"]      # a sell stays visible
    _no_fold_keys(items)


def test_strip_carriers_drops_only_fold_keys():
    it = _stop(False)
    it["_u6_tighten"] = {"x": 1}
    out = fold_mod.strip_carriers([it, "text"])
    assert out[1] == "text"
    assert "_u6_tighten" in out[0] and not [k for k in out[0] if k.startswith("_fold_")]
    assert fold_mod.FOLD_KEY in it                           # input untouched


# ------------------------------------------------------------------ 9. text rules

_REASONS = {
    "lock": lambda n: ([_stop(True, shares=n)], None),
    "earnings": lambda n: ([_stop(False), _earn(n)], None),
    "weak_large": lambda n: ([_stop(False), _weak(n)], None),
    "deterioration_floor": lambda n: ([_stop(False)], {"AAA": [_floor(n)]}),
}


@pytest.mark.parametrize("reason", list(_REASONS))
@pytest.mark.parametrize("n", [1, 7])
def test_tails_have_no_dollar_bold_nan_or_none_and_pluralise(reason, n):
    items, extra = _REASONS[reason](n)
    has_loser = True
    if reason != "deterioration_floor":
        extra = {"AAA": [_floor(n)]}             # a tie with the floor always loses (rank last)
    elif n > 1:
        items = items + [_earn(n - 1)]
    else:
        has_loser = False                        # nothing can sell fewer than one share
    _, payloads = fold_mod.fold(items, [_act("sell_signal")], extra_candidates=extra)
    p = payloads["AAA"]
    assert p["reason_key"] == reason
    word = "share" if n == 1 else "shares"
    for tail in (p["directive_tail"], p["why_tail"]):
        assert "$" not in tail and "**" not in tail
        assert "nan" not in tail.lower() and "None" not in tail
    assert f"({n} {word})" in p["directive_tail"]
    if has_loser:
        these = "this" if n == 1 else "these"
        assert f"none of them sells more than {these} {n} {word}." in p["why_tail"]
    else:
        assert "This also covers" not in p["why_tail"]


def test_exact_tail_texts():
    _, p = fold_mod.fold([_stop(False), _earn(30), _weak(25)], [_act("sell_signal")])
    t = p["AAA"]
    assert t["directive_tail"] == " At minimum, trim 14.0% → 10.0% before the earnings report (30 shares)."
    assert t["why_tail"] == (
        " Earnings in 3d: the earnings-week rule trims 14.0% → 10.0% before the report."
        " This also covers the low-score trim to "
        f"{WEAK_LARGE_TRIM_TO_PCT:.1f}% (25 shares); none of them sells more than these 30 shares.")
    _, p = fold_mod.fold([_stop(False), _weak(40)], [_act("sell_signal")])
    t = p["AAA"]
    assert t["directive_tail"] == f" At minimum, trim 14.0% → {WEAK_LARGE_TRIM_TO_PCT:.1f}% (40 shares)."
    assert t["why_tail"] == (
        f" Also your largest weak holding: weight 14.0% ≥ {LARGE_POSITION_WEIGHT_PCT:.0f}% with "
        f"score 40 < {WEAK_CONVICTION_SCORE}.")
    _, p = fold_mod.fold([_stop(True, 25)], [_act("sell_signal")])
    t = p["AAA"]
    assert t["directive_tail"] == (
        f" At minimum, sell {STOP_PROFIT_LOCK_TRIM_PCT:.0f}% of the position (25 shares) "
        "to lock part of the gain.")
    assert t["why_tail"] == " The stop is 2.0% away with P&L +30.0%, so part of the gain is locked first."
    _, p = fold_mod.fold([_stop(False)], [_act("sell_signal")],
                         extra_candidates={"AAA": [_floor(25)]})
    t = p["AAA"]
    assert t["directive_tail"] == (
        f" At minimum, trim about {DETERIORATION_TRIM_SUGGESTED_PCT:.0f}% of the position "
        "(25 shares) into the weakness.")
    assert t["why_tail"] == (
        " It is also deteriorating: down 12.0% from its peak and below its trend, so the "
        "deterioration rule's trim floor applies.")


def test_also_covers_lists_losers_in_rank_order_with_an_and_join():
    items = [_stop(True, 20), _earn(30), _weak(25)]
    _, p = fold_mod.fold(items, [_act("sell_signal")],
                         extra_candidates={"AAA": [_floor(10)]})
    tail = p["AAA"]["why_tail"]
    assert p["AAA"]["reason_key"] == "earnings"
    i_lock = tail.index("the profit-lock trim of")
    i_weak = tail.index("the low-score trim to")
    i_floor = tail.index("the deterioration trim floor")
    assert i_lock < i_weak < i_floor
    assert ", " in tail and " and the deterioration trim floor (10 shares);" in tail


@pytest.mark.parametrize("missing", ["score", "label", "gap", "dd"])
def test_a_missing_carrier_drops_only_that_reasons_first_sentence(missing):
    if missing == "score":
        items = [_stop(False), _weak(40, with_score=False), _earn(30)]
        extra, reason, first = None, "weak_large", "Also your largest weak holding"
    elif missing == "label":
        it = _earn(40)
        it["_fold_label"] = ""
        items, extra, reason, first = [_stop(False), it, _weak(30)], None, "earnings", "Earnings "
    elif missing == "gap":
        it = _stop(True, 40)
        it["_fold_gap"] = float("nan")
        items, extra, reason, first = [it, _weak(30)], None, "lock", "The stop is"
    else:
        items = [_stop(False), _weak(30)]
        extra = {"AAA": [{**_floor(40), "_fold_dd": None}]}
        reason, first = "deterioration_floor", "It is also deteriorating"
    _, p = fold_mod.fold(items, [_act("sell_signal")], extra_candidates=extra)
    t = p["AAA"]
    assert t["reason_key"] == reason
    assert first not in t["why_tail"]
    assert t["directive_tail"].startswith(" At minimum")
    assert "This also covers" in t["why_tail"]
    assert "nan" not in t["why_tail"].lower() and "None" not in t["why_tail"]


# ------------------------------------------------------------------ journal context gate

def test_shows_journal_context_only_for_weak_large_or_a_legacy_min_trim():
    f = fold_mod.shows_journal_context
    assert f({"min_trim": {"trim_shares": 3}}) is True                       # legacy: no reason_key
    assert f({"min_trim": {"trim_shares": 3, "reason_key": "weak_large"}}) is True
    for rk in ("earnings", "lock", "deterioration_floor"):
        assert f({"min_trim": {"trim_shares": 3, "reason_key": rk}}) is False
    assert f({}) is False and f({"min_trim": None}) is False and f({"min_trim": {}}) is False
    assert f(None) is False and f({"min_trim": "x"}) is False


# ------------------------------------------------------------------ integration fixtures

def _book(w, price=100.0, shares=100, score=70.0, pnl=0.0, gap=None, n_fill=14, stop=90.0):
    rows = [{"ticker": "AAA", "weight": w, "score": score, "price": price, "shares": shares,
             "pnl_pct": pnl, "gap_to_stop": gap, "stop": stop}]
    rows += [{"ticker": f"F{i}", "weight": 2.0} for i in range(n_fill)]
    return make_port_df(rows), shares * price / (w / 100.0)


_DET = {"tier": "TRIM", "ticker": "AAA", "shares": 100, "price": 100.0, "weight_pct": 14.0,
        "dd_from_peak_pct": 12.0}


def _lock_atr():
    step = 100.0 * STOP_RAISE_MIN_STEP_PCT / 100
    return (100 - 98 - step - 0.5) / STOP_TIGHTEN_ATR_MULT


def _run(kind, w, *, score=70.0, days=3, pnl=0.0, gap=None, n_trim=None, atr=None,
         n_fill=14, det=None, stop=90.0):
    df, pv = _book(w, score=score, pnl=pnl, gap=gap, n_fill=n_fill, stop=stop)
    act = [_act(kind, n_trim)]
    h = {}
    if days is not None:
        h["earnings"] = (_TODAY + timedelta(days=days)).isoformat()
    if atr:
        h["atr"] = atr
    items = db._review_list(df, [], [], {"AAA": h} if h else {}, _TODAY, portfolio_value=pv,
                            act_today=act, deterioration=det)
    return act, [i for i in items if i.get("ticker") == "AAA"], items


def _sized_on_aaa(items):
    return [i for i in items if i.get("ticker") == "AAA"
            and (i.get("action") or {}).get("type") in _SIZED_TYPES]


def _cover(act):
    return asc.item_covers(act[0])["AAA"]["shares"]


_N_ACT = _ts.fraction_trim_shares(100, DETERIORATION_TRIM_SUGGESTED_PCT)


# ------------------------------------------------------------------ 2. crossovers

@pytest.mark.parametrize("w", [12.5, 13.0, 40 / 3, 13.4, 14.0, 20.0])
def test_earnings_trim_vs_the_deterioration_floor_at_weights_around_the_crossover(w):
    act, rev, _ = _run("deterioration_trim", w, n_trim=_N_ACT)
    dec = _ts.earnings_week_decision(w, 15, 100)
    e_n = dec["trim_shares"] if dec else 0
    assert _sized_on_aaa(rev) == []
    assert _cover(act) == max(_N_ACT, e_n)
    if e_n > _N_ACT:
        assert act[0]["min_trim"]["reason_key"] == "earnings"
        assert act[0]["min_trim"]["trim_shares"] == e_n
        assert act[0]["min_trim"]["target_weight"] == EARNINGS_OVERWEIGHT_TRIM_TO_PCT
    else:
        assert "min_trim" not in act[0]


def test_at_twenty_percent_the_card_sells_what_the_earnings_rule_sells_not_the_floor():
    act, rev, _ = _run("deterioration_trim", 20.0, n_trim=_N_ACT)
    want = _ts.shares_to_trim(100, 20.0, EARNINGS_OVERWEIGHT_TRIM_TO_PCT)
    assert want > _N_ACT
    assert act[0]["min_trim"]["trim_shares"] == want
    assert f"({want} shares)" in act[0]["directive"]
    assert "This also covers" not in act[0]["why"]
    assert _sized_on_aaa(rev) == []


@pytest.mark.parametrize("w", [10.5, 10.67, 11.0, 13.4])
def test_weak_large_vs_the_deterioration_floor(w):
    act, rev, _ = _run("deterioration_trim", w, score=WEAK_CONVICTION_SCORE - 15.0, days=None,
                       n_trim=_N_ACT)
    wl_n = _ts.shares_to_trim(100, w, WEAK_LARGE_TRIM_TO_PCT)
    assert _sized_on_aaa(rev) == []
    assert _cover(act) == max(_N_ACT, wl_n)
    assert ("min_trim" in act[0]) == (wl_n > _N_ACT)
    if wl_n > _N_ACT:
        assert act[0]["min_trim"]["reason_key"] == "weak_large"


def test_the_standing_trim_to_the_ceiling_at_twenty_percent():
    dec = _ts.earnings_week_decision(20.0, 5, 100)
    assert dec["basis"] == "standing_in_window"              # the fixture exercises it
    act, rev, _ = _run("deterioration_trim", 20.0, n_trim=_N_ACT, n_fill=4)
    assert _cover(act) == max(_N_ACT, dec["trim_shares"])
    assert _sized_on_aaa(rev) == []


@pytest.mark.parametrize("kind,n", [("deterioration_trim", _N_ACT), ("deterioration_trim", 10),
                                    ("sell_signal", None)])
def test_stop_card_under_a_partial_reduce_is_tighten_only_and_the_lock_is_a_candidate(kind, n):
    act, rev, _ = _run(kind, 5.0, days=None, pnl=STOP_PROFIT_LOCK_PNL_PCT + 5, gap=2.0,
                       atr=_lock_atr(), stop=98.0, n_trim=n,
                       det=[_DET] if kind == "sell_signal" else None)
    cards = [i for i in rev if i.get("icon") == "📍"]
    assert len(cards) == 1
    assert cards[0]["action"]["type"] == "TIGHTEN_ONLY"
    assert "already says to reduce AAA" in cards[0]["why"]
    assert _sized_on_aaa(rev) == []
    lock_n = _ts.fraction_trim_shares(100, STOP_PROFIT_LOCK_TRIM_PCT)
    assert _cover(act) == max(lock_n, n or 0)
    if n is not None and n >= lock_n:
        assert "min_trim" not in act[0]                      # both 25%: a tie, no payload
    else:
        assert act[0]["min_trim"]["reason_key"] == "lock"
        assert f"({lock_n} shares)" in act[0]["directive"]


def test_the_lock_beats_the_floor_on_a_tie_and_names_it_as_covered():
    act, _, _ = _run("sell_signal", 5.0, days=None, pnl=STOP_PROFIT_LOCK_PNL_PCT + 5, gap=2.0,
                     atr=_lock_atr(), stop=98.0, det=[_DET])
    assert act[0]["min_trim"]["reason_key"] == "lock"
    assert "the deterioration trim floor (25 shares)" in act[0]["why"]


def test_a_sell_beside_a_deterioration_trim_is_sized_at_the_floor():
    act, rev, _ = _run("sell_signal", 11.0, days=None, det=[_DET])
    assert act[0]["min_trim"]["reason_key"] == "deterioration_floor"
    assert act[0]["min_trim"]["trim_shares"] == _N_ACT
    assert act[0]["min_trim"]["trim_dollars"] == _N_ACT * 100.0
    assert _cover(act) == _N_ACT
    assert rev == []


@pytest.mark.parametrize("shares", [0, 0.4])
def test_a_sub_share_floor_is_not_a_candidate(shares):
    act, _, _ = _run("sell_signal", 11.0, days=None, det=[{**_DET, "shares": shares}])
    assert "min_trim" not in act[0]


def test_a_deterioration_exit_under_a_sell_is_not_a_candidate():
    act, _, _ = _run("sell_signal", 11.0, days=None, det=[{**_DET, "tier": "EXIT"}])
    assert "min_trim" not in act[0]


# ------------------------------------------------------------------ 3. never sells less

_GRID = [(kind, n, w, score)
         for kind, n in (("deterioration_trim", _N_ACT), ("deterioration_trim", 0),
                         ("sell_signal", None))
         for w in (11.0, 14.0, 20.0)
         for score in (WEAK_CONVICTION_SCORE - 15.0, 70.0)]


@pytest.mark.parametrize("kind,n,w,score", _GRID)
def test_the_act_card_never_sells_less_than_any_candidate(kind, n, w, score):
    det = [_DET] if kind == "sell_signal" else None
    # What the Review sections would have sized for the name with no Act card at all.
    df, pv = _book(w, score=score)
    held = {"AAA": {"earnings": (_TODAY + timedelta(days=3)).isoformat()}}
    bare = db._review_list(df, [], [], held, _TODAY, portfolio_value=pv, act_today=[])
    cands = [i["action"]["trim_shares"] for i in _sized_on_aaa(bare)]
    if det:
        cands.append(_N_ACT)
    act, rev, _ = _run(kind, w, score=score, n_trim=n, det=det)
    cover = _cover(act)
    own = asc.valid_shares(n)
    if cands or own:
        assert cover is not None
        assert cover >= max(cands + ([own] if own else []))
    assert _sized_on_aaa(rev) == []


@pytest.mark.parametrize("kind,n,w", [("sell_signal", None, 20.0), ("deterioration_trim", _N_ACT, 20.0),
                                      ("sell_signal", None, 11.0)])
def test_reduce_and_flagged_ticker_sets_are_unchanged_by_the_fold(monkeypatch, kind, n, w):
    det = [_DET] if kind == "sell_signal" else None

    def sets(act, items):
        return (dbk.reduce_call_tickers(act, items), dbk.all_flagged_tickers(act, items))

    act1, _, items1 = _run(kind, w, score=WEAK_CONVICTION_SCORE - 15.0, n_trim=n, det=det)
    on = sets(act1, items1)

    def boom(*a, **k):
        raise RuntimeError("fold off")

    monkeypatch.setattr(fold_mod, "fold", boom)             # the keep-every-card fallback
    act2, _, items2 = _run(kind, w, score=WEAK_CONVICTION_SCORE - 15.0, n_trim=n, det=det)
    assert on == sets(act2, items2)
    assert "AAA" in on[0]


# ------------------------------------------------------------------ 5. premortem ordering

def test_the_size_lands_on_the_sell_not_on_a_premortem_card():
    df, pv = _book(20.0, score=WEAK_CONVICTION_SCORE - 15.0)
    prem, sell = _act("premortem_triggered"), _act("sell_signal")
    db._review_list(df, [], [], {}, _TODAY, portfolio_value=pv, act_today=[prem, sell])
    assert "min_trim" in sell and "min_trim" not in prem
    assert prem["directive"] == "D." and prem["why"] == "W."
    # And the other order.
    prem2, sell2 = _act("premortem_triggered"), _act("sell_signal")
    db._review_list(df, [], [], {}, _TODAY, portfolio_value=pv, act_today=[sell2, prem2])
    assert "min_trim" in sell2 and "min_trim" not in prem2


# ------------------------------------------------------------------ 6. regressions

def test_sell_plus_weak_large_keeps_its_size_and_gains_the_reason_key():
    w = LARGE_POSITION_WEIGHT_PCT + 2
    act, rev, _ = _run("sell_signal", w, score=WEAK_CONVICTION_SCORE - 10, days=None)
    mt = act[0]["min_trim"]
    n = _ts.shares_to_trim(100, w, WEAK_LARGE_TRIM_TO_PCT)
    assert mt == {"trim_shares": n, "trim_dollars": round(n * 100.0, 0), "from_weight": w,
                  "target_weight": WEAK_LARGE_TRIM_TO_PCT, "reason_key": "weak_large"}
    assert "At minimum, trim" in act[0]["directive"] and "≈" not in act[0]["directive"]
    assert "$" not in act[0]["directive"] and "$" not in act[0]["why"]
    assert "largest weak holding" in act[0]["why"]
    assert act[0]["reason"] == f"{act[0]['directive']} {act[0]['why']}"
    assert rev == []


@pytest.mark.parametrize("kind", ["stop_breach", "deterioration_exit"])
def test_a_full_exit_drops_every_review_trim_as_before(kind):
    act, rev, items = _run(kind, 20.0, score=WEAK_CONVICTION_SCORE - 15.0)
    assert rev == []                                         # earnings + weak-large skipped
    assert "min_trim" not in act[0]
    assert act[0]["directive"] == "D." and act[0]["why"] == "W."
    for it in items:
        _no_fold_keys([it])


@pytest.mark.parametrize("kind", ["stop_breach", "deterioration_exit"])
def test_a_full_exit_makes_the_stop_card_tighten_only(kind):
    act, rev, _ = _run(kind, 5.0, days=None, pnl=STOP_PROFIT_LOCK_PNL_PCT + 5, gap=2.0,
                       atr=_lock_atr(), stop=98.0)
    cards = [i for i in rev if i.get("icon") == "📍"]
    assert len(cards) == 1 and cards[0]["action"]["type"] == "TIGHTEN_ONLY"
    assert "already says to reduce AAA" in cards[0]["why"]
    assert "min_trim" not in act[0]


def test_a_no_reduce_day_is_unchanged_and_carries_no_fold_keys():
    df, pv = _book(20.0, score=WEAK_CONVICTION_SCORE - 15.0)
    held = {"AAA": {"earnings": (_TODAY + timedelta(days=3)).isoformat()}}
    a = db._review_list(df, [], [], held, _TODAY, portfolio_value=pv, act_today=[])
    b = db._review_list(df, [], [], held, _TODAY, portfolio_value=pv, act_today=None)
    c = db._review_list(df, [], [], held, _TODAY, portfolio_value=pv,
                        act_today=[_act("risk"), _act("critical_news"),
                                   _act("premortem_triggered")])
    assert a == b
    assert [i["action"] for i in a if i.get("ticker") == "AAA"] == [
        i["action"] for i in c if i.get("ticker") == "AAA"]
    assert _sized_on_aaa(a)
    for items in (a, b, c):
        _no_fold_keys(items)
        for it in items:
            assert not [k for k in it if k.startswith("_u6_")]


# ------------------------------------------------------------------ 11. pins

def _app_src():
    return (ROOT / "app.py").read_text(encoding="utf-8")


def test_synth_schema_version_keeps_the_fold_note_in_its_history():
    # B-C3 bumped 32 -> 33; the fold note sits in the "was 32:" clause.
    assert "; was 32: a larger same-name Review trim" in _app_src()
    assert "reason_key (BriefSkipSizeBlind)" in _app_src()
    assert "_SYNTH_SCHEMA_VER = 44  # bumped:" in _app_src()   # 40 -> 41 by brief-consolidation C3


def test_min_trim_is_not_written_by_the_exit_signals_capture():
    app = _app_src()
    seg = app[app.index("_exit_signals_to_save = []"):
              app.index("db.save_exit_signals_batch(_exit_signals_to_save)")]
    assert "min_trim" not in seg


def test_the_journal_context_gate_goes_through_the_pure_decision():
    app = _app_src()
    assert "act_trim_fold.shows_journal_context(_db_item)" in app
    assert 'if _db_item.get("min_trim") and _db_ticker:' not in app


def test_act_trim_fold_is_a_gate_file():
    hooks = str(ROOT / ".claude" / "hooks")
    sys.path.insert(0, hooks)
    try:
        import pre_tool_checks as p
    finally:
        sys.path.remove(hooks)
    assert "stock_analyzer/act_trim_fold.py" in p._GATE_FILES


def test_the_module_has_no_streamlit_import_and_no_money_or_bold_in_its_text():
    src = (ROOT / "stock_analyzer" / "act_trim_fold.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc = ast.get_docstring(tree, clean=False)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            assert not any(a.name.split(".")[0] == "streamlit" for a in n.names)
        if isinstance(n, ast.ImportFrom):
            assert (n.module or "").split(".")[0] != "streamlit"
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value != doc:
            assert "$" not in n.value and "**" not in n.value
