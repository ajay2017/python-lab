"""U6 C1 (docs/plans/brief-one-trim-per-name-u6.md): brief_trim_merge on hand-built Review
items. The pure decision: one Act trim per name (the most shares wins, its action dict
unchanged), the rest demoted to reworded non-trade cards; fail-safe, order independence,
exact wording."""
import copy
import itertools
import random

import pytest

from stock_analyzer import brief_trim_merge as btm
from stock_analyzer.constants import (
    LARGE_POSITION_WEIGHT_PCT,
    MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
    STOP_PROFIT_LOCK_TRIM_PCT,
    WEAK_CONVICTION_SCORE,
    WEAK_LARGE_TRIM_TO_PCT,
)
from stock_analyzer.decision_bucket import _ACT_REVIEW_TYPES

pytestmark = pytest.mark.fast

_TRIG = "Beat → re-enter post-print if composite holds; Miss → the stop is fine."
_TRIG_NRE = "Miss → the stop is fine."
_UR_WHY = ("Gap 2.0% above stop; P&L +30.0%. Today's Brief already says to reduce AAA, so "
           "this only tightens the stop on the shares you keep.")


def lock(t="AAA", n=25, stop=48.5):
    return {"priority": "medium", "icon": "📍", "ticker": t, "headline": "gap 2.0% above stop",
            "action": {"type": "TRIM_AND_TIGHTEN", "trim_shares": n, "trim_dollars": n * 50.0,
                       "trim_pct": STOP_PROFIT_LOCK_TRIM_PCT, "new_stop": stop},
            "why": "Gap critical (2.0% < 3%) and P&L +30.0% above 25% — lock part of the gain.",
            "trigger": "Stop break = full exit next open.", "weight": 12.0, "lifecycle": "established",
            "_u6_tighten": {"new_stop": stop, "why": _UR_WHY}}


def tighten(t="AAA", stop=48.5):
    return {"priority": "low", "icon": "📍", "ticker": t, "headline": "gap 5.0% above stop",
            "action": {"type": "TIGHTEN_ONLY", "new_stop": stop},
            "why": "Gap 5.0% above stop; P&L +9.0%. Tighten stop to protect downside; position still has room.",
            "trigger": "Stop break = full exit next open.", "weight": 12.0, "lifecycle": "established",
            "_u6_tighten": {"new_stop": stop, "why": _UR_WHY}}


def earn(t="AAA", n=20, target=10.0, w=17.4, trig_extra=""):
    a = ({"type": "TRIM_TO_TARGET", "trim_shares": n, "trim_dollars": n * 50.0,
          "from_weight": w, "target_weight": target, "reason_key": "earnings"}
         if n else {"type": "WATCH"})
    return {"priority": "medium", "icon": "📅", "ticker": t, "headline": f"earnings in 3d, weight {w:.1f}%",
            "action": a, "why": "Weight is over the line. Trim to size-down the binary event exposure."
            if n else "Weight is at or under the line, so the earnings-week rule sizes nothing "
            "before the report (standing work).",
            "trigger": "no pre-event action needed" if not n else _TRIG, "weight": w,
            "revision_flag_line": "rating cut" if trig_extra else None,
            "_u6_kind": "earnings", "_u6_trigger_trimmed": _TRIG,
            "_u6_trigger_trimmed_no_reentry": _TRIG_NRE}


def weak(t="AAA", n=30, target=WEAK_LARGE_TRIM_TO_PCT, w=17.4, score=50.0):
    a = ({"type": "TRIM_TO_TARGET", "trim_shares": n, "trim_dollars": n * 50.0,
          "from_weight": w, "target_weight": target, "reason_key": "weak_large"}
         if n else {"type": "WATCH"})
    return {"priority": "medium", "icon": "🔍", "ticker": t,
            "headline": f"largest weak holding — {w:.1f}% weight, score {score:.0f}/100",
            "action": a, "why": "Weight ... largest position with weakest conviction.",
            "trigger": "Already flagged — act before close, don't wait.", "weight": w,
            "_u6_kind": "weak_large", "_u6_score": score}


def macro(t="AAA", n=10, event="NFP", date="2026-12-04", exp=40.0):
    return {"priority": "low", "icon": "🌐", "ticker": None, "event": event,
            "headline": f"{event} in 3d ({date}) · affected: AAA", "weight": None,
            "action": {"type": "PROTECTIVE_TRIM", "trim_ticker": t, "trim_shares": n,
                       "trim_dollars": n * 50.0, "from_exposure": exp, "to_exposure": exp - 5.0,
                       "reduction_pp": 5.0, "weakest_score": 50.0, "conviction_unmeasured": False},
            "why": "Affected-sector exposure 40.0% > 30% trim threshold.",
            "trigger": "Hawkish surprise → stops.", "_affected_all": [t],
            "_headline_head": f"{event} in 3d ({date})", "_u6_event_date": date}


def _acts(out, t="AAA"):
    return [i for i in out
            if (i.get("action") or {}).get("type") in _ACT_REVIEW_TYPES
            and (i.get("ticker") or i["action"].get("trim_ticker")) == t]


def _no_carriers(out):
    return all(not k.startswith("_u6_") for i in out for k in i)


# -- winner selection ---------------------------------------------------------------

def test_most_shares_wins_and_its_action_is_unchanged():
    e, w, lk, mc = earn(n=20), weak(n=30), lock(n=25), macro(n=10)
    out = btm.one_trim_per_name([e, w, lk, mc])
    [win] = _acts(out)
    assert win["action"] == w["action"] and win["action"]["trim_shares"] == 30
    assert _no_carriers(out) and len(out) == 4


@pytest.mark.parametrize("shares,winner_icon", [
    ({"lock": 25, "weak": 25, "earn": 25, "macro": 25}, "📍"),
    ({"lock": 5, "weak": 25, "earn": 25, "macro": 25}, "🔍"),
    ({"lock": 5, "weak": 5, "earn": 25, "macro": 25}, "📅"),
    ({"lock": 5, "weak": 5, "earn": 5, "macro": 25}, "🌐"),
])
def test_ties_go_lock_then_weak_then_earnings_then_macro(shares, winner_icon):
    items = [lock(n=shares["lock"]), weak(n=shares["weak"]), earn(n=shares["earn"]),
             macro(n=shares["macro"])]
    [win] = _acts(btm.one_trim_per_name(items))
    assert win["icon"] == winner_icon


def test_macro_tie_goes_to_the_earliest_event_date_then_name():
    a, b = macro(event="CPI", date="2026-12-10", n=7), macro(event="NFP", date="2026-12-04", n=7)
    [win] = _acts(btm.one_trim_per_name([a, b]))
    assert win["event"] == "NFP"
    c, d = macro(event="PPI", date="2026-12-04", n=7), macro(event="NFP", date="2026-12-04", n=7)
    [win] = _acts(btm.one_trim_per_name([c, d]))
    assert win["event"] == "NFP"


def test_a_single_trim_is_never_altered_and_other_names_are_untouched():
    items = [weak("AAA", n=30), earn("BBB", n=10), macro("CCC", n=4), lock("DDD", n=9)]
    out = btm.one_trim_per_name(items)
    exp = [{k: v for k, v in i.items() if not k.startswith("_u6_")} for i in items]
    assert out == exp


def test_a_name_with_no_act_trim_keeps_its_watch_cards_as_they_are():
    items = [earn("AAA", n=0), weak("AAA", n=0), tighten("AAA")]
    out = btm.one_trim_per_name(items)
    assert out == [{k: v for k, v in i.items() if not k.startswith("_u6_")} for i in items]


# -- wording ------------------------------------------------------------------------

def test_winner_gets_one_also_covers_sentence_with_each_part_in_rank_order():
    out = btm.one_trim_per_name([earn(n=20), weak(n=30), lock(n=25), macro(n=10)])
    [win] = _acts(out)
    assert win["why"].endswith(
        " This sale also covers the profit-lock trim of 25% of the position (25 shares), "
        "the earnings-week trim to 10.0% (20 shares) and the NFP pre-event trim (10 shares); "
        "none of them sells more than these 30 shares.")
    assert win["why"].startswith(weak()["why"])


def test_single_loser_part_and_singular_share_wording():
    out = btm.one_trim_per_name([weak(n=1), earn(n=1)])
    [win] = _acts(out)
    assert win["why"].endswith(" This sale also covers the earnings-week trim to 10.0% "
                               "(1 share); none of them sells more than this 1 share.")
    cov = next(i for i in out if i["icon"] == "📅")
    assert "sells 1 share, no fewer than the 1 share a trim" in cov["why"]


def test_earnings_loser_sized_text_action_and_trigger():
    out = btm.one_trim_per_name([weak(n=30), earn(n=20)])
    e = next(i for i in out if i["icon"] == "📅")
    assert e["action"] == {"type": "WATCH", "covered_by": "AAA"}
    assert e["why"] == ("Today's Act Today trim of AAA sells 30 shares, no fewer than the 20 "
                        "shares a trim to 10.0% would sell, so earnings week adds no separate trade.")
    assert e["trigger"] == _TRIG_NRE                    # a weak-large item on the name: no re-entry
    assert e["priority"] == "medium" and e["headline"].startswith("earnings in 3d")


def test_earnings_loser_keeps_the_reentry_clause_when_no_weak_large_item_is_on_the_name():
    out = btm.one_trim_per_name([lock(n=30), earn(n=20)])
    e = next(i for i in out if i["icon"] == "📅")
    assert e["trigger"] == _TRIG
    assert "Beat → re-enter" in e["trigger"]


def test_unsized_earnings_watch_beside_a_trim_is_reworded_and_the_act_card_has_no_suffix():
    w, e = weak(n=30), earn(n=0)
    out = btm.one_trim_per_name([w, e])
    win = next(i for i in out if i["icon"] == "🔍")
    assert win["why"] == w["why"] and win["action"] == w["action"]       # SNOW: byte-identical
    cov = next(i for i in out if i["icon"] == "📅")
    assert cov["action"] == {"type": "WATCH", "covered_by": "AAA"}
    assert cov["why"] == ("Today's Act Today trim of AAA sells 30 shares; the earnings-week "
                          "rule sizes no trade of its own.")
    for bad in ("sizes nothing", "standing work", "no pre-event action", "no pre-event trim",
                "already conservative", "within tolerance"):
        assert bad not in cov["why"] and bad not in cov["trigger"]


def test_weak_large_loser_sized_and_unsized_text():
    out = btm.one_trim_per_name([lock(n=40), weak(n=30)])
    w = next(i for i in out if i["icon"] == "🔍")
    assert w["action"] == {"type": "WATCH", "covered_by": "AAA"}
    assert w["why"] == (f"Weight 17.4% ≥ {LARGE_POSITION_WEIGHT_PCT:.0f}% AND score 50 < "
                        f"{WEAK_CONVICTION_SCORE}. Today's Act Today trim of AAA sells 40 shares, "
                        "no fewer than the 30 shares a trim to 8.0% would sell, so no separate trim.")
    assert w["trigger"] == "Covered by today's Act Today trim of AAA."
    out = btm.one_trim_per_name([lock(n=40), weak(n=0)])
    w = next(i for i in out if i["icon"] == "🔍")
    assert w["why"] == (f"Weight 17.4% ≥ {LARGE_POSITION_WEIGHT_PCT:.0f}% AND score 50 < "
                        f"{WEAK_CONVICTION_SCORE}. Today's Act Today trim of AAA sells 40 shares; "
                        "this check sizes no trade of its own.")


def test_score_text_never_prints_55_lt_55():
    out = btm.one_trim_per_name([lock(n=40), weak(n=30, score=54.95)])
    w = next(i for i in out if i["icon"] == "🔍")
    assert "score 54.9 < 55" in w["why"] and "55 < 55" not in w["why"]


def test_lock_loser_becomes_the_shared_stop_only_card():
    out = btm.one_trim_per_name([weak(n=40), lock(n=25, stop=48.5)])
    lk = next(i for i in out if i["icon"] == "📍")
    assert lk["action"] == {"type": "TIGHTEN_ONLY", "new_stop": 48.5}
    assert lk["why"] == _UR_WHY
    assert lk["trigger"] == "Stop break = full exit next open."


def test_a_tighten_only_beside_a_winner_gets_the_under_reduce_wording():
    out = btm.one_trim_per_name([earn(n=20), tighten()])
    t = next(i for i in out if i["icon"] == "📍")
    assert t["action"] == {"type": "TIGHTEN_ONLY", "new_stop": 48.5} and t["why"] == _UR_WHY
    for bad in ("position still has room", "only the stop is tightened"):
        assert bad not in t["why"]


def test_macro_loser_stands_down_and_loses_its_trim_ticker():
    out = btm.one_trim_per_name([weak(n=30), macro(n=10)])
    m = next(i for i in out if i["icon"] == "🌐")
    assert m["action"] == {"type": "WATCH", "from_exposure": 40.0,
                           "threshold": MACRO_AFFECTED_TRIM_THRESHOLD_PCT, "covered_by": "AAA"}
    assert "trim_ticker" not in m["action"]
    assert m["why"] == (f"Affected-sector exposure 40.0% > {MACRO_AFFECTED_TRIM_THRESHOLD_PCT:.0f}% "
                        "trim threshold. The pre-event trim would fall on AAA (10 shares); today's "
                        "Act Today trim of AAA sells 30 shares, no fewer, so no separate pre-event "
                        "trim is sized.")
    assert m["trigger"] == macro()["trigger"] and m["_affected_all"] == ["AAA"]


def test_no_text_on_a_covered_name_carries_a_dollar_sign_or_bold_marker():
    out = btm.one_trim_per_name([earn(n=20), weak(n=30), lock(n=25), macro(n=10), tighten()])
    for i in out:
        for k in ("why", "trigger"):
            assert "$" not in i[k] and "**" not in i[k]


def test_covered_action_text():
    assert btm.covered_action_text({"type": "WATCH", "covered_by": "SNOW"}) == (
        "covered by today's Act Today trim of SNOW; no separate trade.")
    for a in ({"type": "WATCH"}, {"covered_by": ""}, {"covered_by": None}, None, "x", []):
        assert btm.covered_action_text(a) is None


# -- fail-safe ------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [None, float("nan"), 0, "3", 2.5, True, -4])
def test_a_bad_trim_shares_leaves_the_subject_untouched(bad):
    w = weak(n=30)
    e = earn(n=20)
    e["action"]["trim_shares"] = bad
    out = btm.one_trim_per_name([w, e, tighten()])
    assert len(_acts(out)) == 2
    assert out[0]["why"] == w["why"] and out[1]["action"] == e["action"]
    assert out[2]["why"] == tighten()["why"]


def test_a_bad_name_does_not_block_another_name():
    bad = earn("ZZZ", n=20)
    bad["action"]["trim_shares"] = None
    out = btm.one_trim_per_name([bad, weak("AAA", n=30), earn("AAA", n=20)])
    assert len(_acts(out, "AAA")) == 1 and len(_acts(out, "ZZZ")) == 1


def test_a_missing_carrier_leaves_the_subject_untouched():
    e = earn(n=20)
    del e["_u6_trigger_trimmed_no_reentry"]          # a weak-large item is on the name
    assert len(_acts(btm.one_trim_per_name([weak(n=30), e]))) == 2
    e = earn(n=20)
    del e["_u6_trigger_trimmed"]                     # no weak-large item on the name
    assert len(_acts(btm.one_trim_per_name([lock(n=30), e]))) == 2
    lk = lock(n=10)
    del lk["_u6_tighten"]
    assert len(_acts(btm.one_trim_per_name([weak(n=30), lk]))) == 2


def test_an_act_trim_of_unknown_kind_leaves_the_subject_untouched():
    odd = earn(n=20)
    odd["action"]["reason_key"] = "mystery"
    assert len(_acts(btm.one_trim_per_name([weak(n=30), odd]))) == 2


def test_numpy_ints_are_real_share_counts():
    np = pytest.importorskip("numpy")
    out = btm.one_trim_per_name([weak(n=np.int64(30)), earn(n=np.int64(20))])
    assert len(_acts(out)) == 1


# -- order independence, no mutation, carriers ----------------------------------------

def _canon(out):
    return sorted(repr(sorted(i.items(), key=lambda kv: kv[0])) for i in out)


def test_output_is_independent_of_input_order_and_inputs_are_not_mutated():
    base = [earn(n=20), weak(n=30), lock(n=25), macro(n=10), macro(event="CPI", date="2026-12-10", n=10),
            tighten(), earn("BBB", n=5), weak("BBB", n=5)]
    snapshot = copy.deepcopy(base)
    want = _canon(btm.one_trim_per_name(base))
    assert base == snapshot
    rng = random.Random(7)
    for _ in range(40):
        shuffled = list(base)
        rng.shuffle(shuffled)
        assert _canon(btm.one_trim_per_name(shuffled)) == want


def test_every_permutation_of_a_small_set_gives_the_same_winner_and_text():
    base = [earn(n=20), weak(n=20), macro(n=20)]
    want = _canon(btm.one_trim_per_name(base))
    for perm in itertools.permutations(base):
        assert _canon(btm.one_trim_per_name(list(perm))) == want


def test_strip_carriers_drops_only_u6_keys_and_copies():
    it = earn(n=20)
    [out] = btm.strip_carriers([it])
    assert "_u6_kind" in it and _no_carriers([out])
    assert out["headline"] == it["headline"]
    out["action"]["x"] = 1
    assert "x" not in it["action"]


def test_empty_and_none_inputs():
    assert btm.one_trim_per_name([]) == [] and btm.one_trim_per_name(None) == []


# -- U6 sibling: a same-name DETERIORATION_WATCH beside an Act trim ---------------------------

def det(t="SNOW"):
    """The 👁️ Monitoring card `_review_list` builds in section 0 (early deterioration)."""
    return {"priority": "low", "icon": "👁️", "ticker": t,
            "headline": "down 12.0% from $60.00 peak, below SMA50",
            "action": {"type": "DETERIORATION_WATCH"},
            "why": "Early deterioration: drawdown 12.0%, below the 50-day trend. Not an action "
                   "yet — watching for follow-through.",
            "trigger": "A 2-of-3 close below the trend with market underperformance → TRIM.",
            "weight": 17.4, "lifecycle": "", "pnl_pct": -3.0, "price": 52.8,
            "dd_from_peak_pct": 12.0, "below_ma_count": 2, "rel_strength": -1.5}


def _det_of(out):
    [d] = [i for i in out if (i.get("action") or {}).get("type") == "DETERIORATION_WATCH"]
    return d


def test_deterioration_watch_beside_a_trim_keeps_its_type_and_gains_only_covered_by():
    w, d = weak("SNOW", n=2), det("SNOW")
    out = btm.one_trim_per_name([w, d])
    got = _det_of(out)
    assert got["action"] == {**d["action"], "covered_by": "SNOW"}
    assert got["action"]["type"] == "DETERIORATION_WATCH"
    assert {k: v for k, v in got.items() if k != "action"} == {
        k: v for k, v in d.items() if k != "action"}
    assert btm.covered_action_text(got["action"]) == (
        "covered by today's Act Today trim of SNOW; no separate trade.")
    win = next(i for i in out if i["icon"] == "🔍")
    assert win["action"] == w["action"] and win["why"] == w["why"]       # the Act trim is untouched
    assert _no_carriers(out) and len(out) == 2


def test_deterioration_watch_on_a_name_with_no_act_trim_is_unchanged():
    d = det("SNOW")
    out = btm.one_trim_per_name([d, weak("BBB", n=30)])
    assert _det_of(out) == d
    assert "covered_by" not in _det_of(out)["action"]


@pytest.mark.parametrize("bad", [0, 1.5])
def test_deterioration_watch_is_unchanged_when_the_trim_cannot_be_proved_safe(bad):
    w = weak("SNOW", n=2)
    w["action"]["trim_shares"] = bad
    d = det("SNOW")
    out = btm.one_trim_per_name([w, d])
    assert _det_of(out) == d and "covered_by" not in _det_of(out)["action"]


def test_deterioration_watch_beside_a_macro_trim_is_covered_by_its_trim_ticker():
    d = det("SNOW")
    out = btm.one_trim_per_name([macro("SNOW", n=10), d])
    assert _det_of(out)["action"] == {"type": "DETERIORATION_WATCH", "covered_by": "SNOW"}
    [win] = _acts(out, "SNOW")
    assert win["action"]["type"] == "PROTECTIVE_TRIM" and win["ticker"] is None


def test_deterioration_watch_result_is_independent_of_input_order():
    base = [weak("SNOW", n=2), det("SNOW")]
    want = _canon(btm.one_trim_per_name(base))
    assert _canon(btm.one_trim_per_name(list(reversed(base)))) == want
    for perm in itertools.permutations(base + [earn("SNOW", n=1)]):
        assert _det_of(btm.one_trim_per_name(list(perm)))["action"]["covered_by"] == "SNOW"


def test_deterioration_watch_merge_does_not_mutate_the_input():
    base = [weak("SNOW", n=2), det("SNOW")]
    snapshot = copy.deepcopy(base)
    btm.one_trim_per_name(base)
    assert base == snapshot and "covered_by" not in base[1]["action"]
