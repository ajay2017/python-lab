"""BriefSkipSizeBlind B-C3 (docs/plans/brief-skip-size-blind-b3-spec.md): the 🔧 Tune-up
beta / Sharpe cards are settled on Home against the live Act Today view's sell sizes.

Covers the pure module `tuneup_cover.reconcile_trim_drivers`, the no-build-time-drop
end to end through `build_daily_briefing` + `act_today_view`, and the app.py pins.
No threshold literal: the one boundary used (the stop-recovery margin) is imported."""
import ast
import copy
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tests._ast_cache import parse_file_cached

from stock_analyzer import act_sell_cover as asc
from stock_analyzer import daily_briefing as db
from stock_analyzer import tuneup_cover as tc
from stock_analyzer.act_today_view import act_today_view
from stock_analyzer.constants import STOP_RECOVERY_MARGIN_PCT

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ fixtures

def _beta(subject="AAA", trim_shares=10, tickers=("AAA", "BBB", "CCC")):
    return {"type": "beta", "priority": "HIGH", "title": "Portfolio Beta 1.8",
            "problem": "p", "recommendation": "Sell 50% of AAA", "tickers": list(tickers),
            "subject": subject, "trim_shares": trim_shares}


def _sharpe(tickers=("TSLA", "COIN")):
    return {"type": "sharpe", "priority": "HIGH", "title": "Sharpe 0.4", "problem": "p",
            "recommendation": "trim 40-50%", "tickers": list(tickers)}


def _vol():
    return {"type": "volatility", "priority": "HIGH", "title": "Vol", "problem": "p",
            "recommendation": "add a diversifier", "tickers": ["AAA"]}


def _sn():
    return {"type": "single_name_concentration", "priority": "HIGH", "title": "AAA is big",
            "problem": "p", "recommendation": "trim", "tickers": ["AAA"],
            "subject": "AAA", "trim_shares": 3}


def _exit_cov(*names):
    return {n: {"exit": True, "shares": None} for n in names}


def _trim_cov(name, shares):
    return {name: {"exit": False, "shares": shares}}


def _types(res):
    return [c["type"] for c in res["cards"]]


_NO_DIGIT_NO_MARKUP = ("$", "**")


def _clean(text):
    assert isinstance(text, str) and text
    assert not any(m in text for m in _NO_DIGIT_NO_MARKUP)
    assert not any(ch.isdigit() for ch in text), text   # no share count, no number at all


# ------------------------------------------------------------------ view offline

def test_view_offline_returns_every_card_unchanged_with_no_notes():
    cards = [_beta(), _sharpe(), _vol()]
    for off in (None, "x", [], ()):
        res = tc.reconcile_trim_drivers(cards, off)
        assert res["cards"] == cards and res["notes"] == []


def test_non_list_cards_read_as_empty():
    assert tc.reconcile_trim_drivers(None, {}) == {"cards": [], "notes": []}
    assert tc.reconcile_trim_drivers(None, None) == {"cards": [], "notes": []}


# ------------------------------------------------------------------ beta card

def test_beta_not_sold_is_kept_with_no_note():
    res = tc.reconcile_trim_drivers([_beta()], {})
    assert res["notes"] == []
    assert res["cards"] == [_beta()]


def test_beta_covered_by_an_exit_is_dropped_with_a_note():
    res = tc.reconcile_trim_drivers([_beta()], _exit_cov("AAA"))
    assert res["cards"] == []
    assert len(res["notes"]) == 1
    n = res["notes"][0]
    assert n.startswith("🔧 AAA already has a sell in today's Act Today call at least as large")
    _clean(n)


def test_beta_covered_by_a_large_enough_trim_equality_covers():
    for sold in (10, 11, 500):
        res = tc.reconcile_trim_drivers([_beta(trim_shares=10)], _trim_cov("AAA", sold))
        assert res["cards"] == [] and len(res["notes"]) == 1, sold


def test_beta_partial_keeps_the_card_with_the_partial_note():
    res = tc.reconcile_trim_drivers([_beta(trim_shares=10)], _trim_cov("AAA", 9))
    assert res["notes"] == []
    (c,) = res["cards"]
    assert c["act_cover_note"] == asc.cover_note(asc.STATE_PARTIAL, "AAA", "beta trim", tail=tc._BETA_AFTER)
    _clean(c["act_cover_note"])
    assert "fewer shares than this beta trim" in c["act_cover_note"]


def test_beta_unknown_size_keeps_the_card_with_the_unknown_note():
    # risk_off_derisk: a sell of unknown size never fully covers
    cov = {"AAA": {"exit": False, "shares": None}}
    res = tc.reconcile_trim_drivers([_beta(trim_shares=10)], cov)
    (c,) = res["cards"]
    assert c["act_cover_note"] == asc.cover_note(asc.STATE_UNKNOWN, "AAA", "beta trim", tail=tc._BETA_AFTER)
    _clean(c["act_cover_note"])
    assert "couldn't be compared" in c["act_cover_note"]


def test_beta_trim_shares_zero_is_covered_by_any_sell():
    for cov in (_trim_cov("AAA", 1), {"AAA": {"exit": False, "shares": None}}):
        res = tc.reconcile_trim_drivers([_beta(trim_shares=0)], cov)
        assert res["cards"] == [] and len(res["notes"]) == 1


def test_beta_trim_shares_none_is_unknown_under_a_trim_and_covered_under_an_exit():
    res = tc.reconcile_trim_drivers([_beta(trim_shares=None)], _trim_cov("AAA", 50))
    assert res["notes"] == [] and res["cards"][0]["act_cover_note"]
    res = tc.reconcile_trim_drivers([_beta(trim_shares=None)], _exit_cov("AAA"))
    assert res["cards"] == [] and len(res["notes"]) == 1


@pytest.mark.parametrize("bad", [True, False, float("nan"), "2", -1, 1.5, [], {}])
def test_beta_invalid_trim_shares_reads_unknown(bad):
    res = tc.reconcile_trim_drivers([_beta(trim_shares=bad)], _trim_cov("AAA", 500))
    (c,) = res["cards"]
    assert c["act_cover_note"] == asc.cover_note(asc.STATE_UNKNOWN, "AAA", "beta trim", tail=tc._BETA_AFTER)


def test_beta_subject_is_matched_case_insensitively_and_chips_keep_their_spelling():
    res = tc.reconcile_trim_drivers([_beta(subject="aaa", tickers=("aaa", "Bbb"))],
                                    {"AAA": {"exit": False, "shares": 3}})
    (c,) = res["cards"]
    assert c["tickers"] == ["aaa", "Bbb"] and c["act_cover_note"]


def test_beta_only_the_subject_decides_the_card_not_other_chips():
    # BBB is sold in full but is only a chip; the card sells AAA, which is not sold.
    res = tc.reconcile_trim_drivers([_beta(subject="AAA")], _exit_cov("BBB"))
    (c,) = res["cards"]
    assert c["tickers"] == ["AAA", "CCC"]            # BBB (exit-covered) leaves the chips
    assert "act_cover_note" not in c and res["notes"] == []


def test_beta_chips_drop_exit_covered_names_but_keep_trim_sold_names():
    cov = {**_exit_cov("BBB"), **_trim_cov("CCC", 40)}
    res = tc.reconcile_trim_drivers([_beta(subject="AAA")], cov)
    assert res["cards"][0]["tickers"] == ["AAA", "CCC"]


def test_beta_chips_may_all_go_and_the_card_still_stands_when_its_subject_is_unsold():
    res = tc.reconcile_trim_drivers([_beta(subject="ZZZ", tickers=("BBB",))], _exit_cov("BBB"))
    (c,) = res["cards"]
    assert c["tickers"] == [] and res["notes"] == []


def test_beta_without_a_subject_is_kept_and_only_chip_filtered():
    for subj in (None, "", "  ", 7):
        res = tc.reconcile_trim_drivers([_beta(subject=subj)], _exit_cov("AAA", "BBB"))
        (c,) = res["cards"]
        assert c["tickers"] == ["CCC"] and res["notes"] == [], subj
    legacy = _beta()
    del legacy["subject"], legacy["trim_shares"]       # a memoized / locked pre-B-C3 card
    res = tc.reconcile_trim_drivers([legacy], _exit_cov("AAA"))
    assert res["cards"][0]["tickers"] == ["BBB", "CCC"]


# ------------------------------------------------------------------ sharpe card

def test_sharpe_all_chips_exit_covered_is_dropped_with_a_note():
    res = tc.reconcile_trim_drivers([_sharpe()], _exit_cov("TSLA", "COIN"))
    assert res["cards"] == []
    (n,) = res["notes"]
    assert n.startswith("🔧 The Sharpe card's drag names (COIN, TSLA) all have an exit")
    _clean(n)


def test_sharpe_some_chips_exit_covered_keeps_fewer_chips():
    res = tc.reconcile_trim_drivers([_sharpe()], _exit_cov("TSLA"))
    assert res["cards"][0]["tickers"] == ["COIN"] and res["notes"] == []


def test_sharpe_a_trim_only_sell_never_removes_a_chip():
    cov = {**_trim_cov("TSLA", 99), **_trim_cov("COIN", 99)}
    res = tc.reconcile_trim_drivers([_sharpe()], cov)
    assert res["cards"] == [_sharpe()] and res["notes"] == []


def test_sharpe_with_no_chips_to_begin_with_is_kept():
    res = tc.reconcile_trim_drivers([_sharpe(tickers=())], _exit_cov("TSLA"))
    assert len(res["cards"]) == 1 and res["notes"] == []


# ------------------------------------------------------------------ pass-through / safety

def test_other_cards_pass_through_untouched_and_in_place():
    cards = [_vol(), _beta(subject="AAA"), _sn(), _sharpe(), _vol()]
    res = tc.reconcile_trim_drivers(cards, _exit_cov("AAA", "TSLA", "COIN"))
    assert _types(res) == ["volatility", "single_name_concentration", "volatility"]
    assert res["cards"][0] is cards[0] and res["cards"][1] is cards[2]
    assert len(res["notes"]) == 2                    # beta + sharpe each said why


def test_kept_driver_cards_keep_their_position_in_the_list():
    cards = [_vol(), _beta(subject="AAA", trim_shares=10), _sharpe()]
    res = tc.reconcile_trim_drivers(cards, _trim_cov("AAA", 1))
    assert _types(res) == ["volatility", "beta", "sharpe"]


def test_inputs_are_never_mutated():
    cards = [_beta(trim_shares=10), _sharpe(), _vol()]
    cov = {**_trim_cov("AAA", 1), **_exit_cov("TSLA")}
    c0, v0 = copy.deepcopy(cards), copy.deepcopy(cov)
    tc.reconcile_trim_drivers(cards, cov)
    assert cards == c0 and cov == v0


def test_malformed_cards_and_covers_never_raise():
    junk = [None, 7, "x", [], {"type": "beta"}, {"type": "sharpe", "tickers": "TSLA"},
            {"type": "beta", "tickers": None, "subject": "AAA"}]
    cov = {"AAA": "weird", "TSLA": None, 3: {"exit": True}, "BBB": {"exit": "yes"}}
    res = tc.reconcile_trim_drivers(junk, cov)
    assert res["cards"][:4] == junk[:4]
    assert isinstance(res["notes"], list)
    # a present-but-malformed cover entry reads as sold with an unknown size
    (c,) = [x for x in res["cards"] if isinstance(x, dict) and x.get("subject") == "AAA"]
    assert c["act_cover_note"]


def test_notes_and_cover_notes_never_carry_money_markup_or_numbers():
    res = tc.reconcile_trim_drivers(
        [_beta(subject="AAA"), _sharpe()], _exit_cov("AAA", "TSLA", "COIN"))
    for n in res["notes"]:
        _clean(n)
    res = tc.reconcile_trim_drivers([_beta(trim_shares=12)], _trim_cov("AAA", 5))
    _clean(res["cards"][0]["act_cover_note"])


# ------------------------------------------------------------------ end to end

_HELD = {t: {"risk_metrics": {"beta": b}} for t, b in
         [("AAA", 2.0), ("BBB", 1.5), ("CCC", 1.4), ("DDD", 1.3)]}


def _port_df():
    rows = [("AAA", 20.0), ("BBB", 15.0), ("CCC", 10.0), ("DDD", 10.0)]
    return pd.DataFrame([
        {"Ticker": t, "Weight (%)": w, "Price": 100.0, "Shares": 10.0, "P&L (%)": 5.0}
        for t, w in rows])


def _beta_rec(subject="EEE", trim_shares=8, roots=("EEE",)):
    return {"type": "beta", "priority": "HIGH", "title": "Beta", "problem": "p",
            "recommendation": f"Sell 50% of {subject}", "trim_shares": trim_shares,
            "root_tickers": [{"ticker": t} for t in roots],
            "beta_levers": {"target": 1.3, "trim": {"ticker": subject}}}


def _brief(monkeypatch, act, review, risk_recs):
    monkeypatch.setattr(db, "_act_today", lambda *a, **k: [dict(x) for x in act])
    monkeypatch.setattr(db, "_review_list", lambda *a, **k: [dict(x) for x in review])
    monkeypatch.setattr(db, "_grow_today", lambda *a, **k: {})
    monkeypatch.setattr(db, "deterioration_signals", lambda *a, **k: {})
    return db.build_daily_briefing(
        _port_df(), [], risk_recs, [], [], _HELD, None, 10000.0, date(2026, 10, 16),
        fragility={"severity": "fragile"},
        spy_trend_df=pd.DataFrame({"Close": [200.0 - i * 0.5 for i in range(250)]}),
        vix_level=14.0)


def _det_trim(ticker="EEE", shares=5):
    return {"ticker": ticker, "kind": "deterioration_trim", "trim_shares": shares,
            "action": "Trim", "why": "w", "headline": "h"}


def _settle(brief, port_df_enriched=None):
    view = act_today_view(brief, False, port_df_enriched, None, STOP_RECOVERY_MARGIN_PCT)
    return tc.reconcile_trim_drivers(brief["portfolio_tuneup"], asc.view_sell_cover(view))


def test_e2e_no_build_time_drop_whatever_the_act_and_review_lists_hold(monkeypatch):
    watch = {"ticker": "EEE", "action": {"type": "WATCH"}, "headline": "h"}
    brief = _brief(monkeypatch, [_det_trim("EEE", 99)], [watch], [_beta_rec()])
    assert [c["type"] for c in brief["portfolio_tuneup"]] == ["beta"]
    card = brief["portfolio_tuneup"][0]
    assert card["subject"] == "EEE" and card["trim_shares"] == 8 and card["tickers"] == ["EEE"]


def test_e2e_a_smaller_deterioration_trim_leaves_the_beta_card_with_a_partial_note(monkeypatch):
    brief = _brief(monkeypatch, [_det_trim("EEE", 5)], [], [_beta_rec(trim_shares=8)])
    res = _settle(brief)
    (c,) = res["cards"]
    assert c["type"] == "beta" and res["notes"] == []
    assert c["act_cover_note"] == asc.cover_note(asc.STATE_PARTIAL, "EEE", "beta trim", tail=tc._BETA_AFTER)


def test_e2e_a_covering_deterioration_trim_drops_the_beta_card_with_a_note(monkeypatch):
    for sold in (8, 20):
        brief = _brief(monkeypatch, [_det_trim("EEE", sold)], [], [_beta_rec(trim_shares=8)])
        res = _settle(brief)
        assert res["cards"] == [] and len(res["notes"]) == 1 and "EEE" in res["notes"][0], sold


def test_e2e_a_recovered_stop_breach_no_longer_hides_the_beta_card(monkeypatch):
    breach = {"ticker": "EEE", "kind": "stop_breach", "action": "Sell", "why": "w",
              "headline": "h"}
    brief = _brief(monkeypatch, [breach], [], [_beta_rec(trim_shares=8)])
    assert [c["type"] for c in brief["portfolio_tuneup"]] == ["beta"]      # never dropped at build
    # still breached: the exit covers the beta card
    pe_active = pd.DataFrame([{"Ticker": "EEE", "Gap to Stop (%)": -3.0, "Stop": 100.0}])
    res = _settle(brief, pe_active)
    assert res["cards"] == [] and len(res["notes"]) == 1
    # recovered (live price back above the stop by more than the margin): the card is back
    pe_rec = pd.DataFrame([{"Ticker": "EEE", "Gap to Stop (%)": STOP_RECOVERY_MARGIN_PCT + 5.0,
                            "Stop": 100.0}])
    res = _settle(brief, pe_rec)
    assert [c["type"] for c in res["cards"]] == ["beta"] and res["notes"] == []
    assert "act_cover_note" not in res["cards"][0]


def test_e2e_view_offline_shows_the_card_with_no_note(monkeypatch):
    brief = _brief(monkeypatch, [_det_trim("EEE", 99)], [], [_beta_rec()])
    view = act_today_view(brief, True, None, None, STOP_RECOVERY_MARGIN_PCT)
    res = tc.reconcile_trim_drivers(brief["portfolio_tuneup"], asc.view_sell_cover(view))
    assert [c["type"] for c in res["cards"]] == ["beta"] and res["notes"] == []


# ------------------------------------------------------------------ pins

def _app_src():
    return (ROOT / "app.py").read_text(encoding="utf-8")


def test_synth_schema_version_keeps_the_tuneup_and_fold_notes_in_its_history():
    src = _app_src()
    # beta-gate C2 bumped 33 -> 34; the Tune-up note now sits in the "was 33:" clause.
    assert "_SYNTH_SCHEMA_VER = 37  # bumped:" in src
    assert "; was 33: the Tune-up beta card carries subject / trim_shares" in src
    assert "(BriefSkipSizeBlind B-C3); was 32: a larger same-name Review trim" in src


def test_tuneup_cover_is_a_gate_file():
    sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
    try:
        import pre_tool_checks as p
    finally:
        sys.path.pop(0)
    assert "stock_analyzer/tuneup_cover.py" in p._GATE_FILES


def test_app_calls_reconcile_trim_drivers_exactly_once_and_renders_the_notes():
    tree = parse_file_cached(ROOT / "app.py")
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "reconcile_trim_drivers"
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "tuneup_cover"]
    assert len(calls) == 1
    src = _app_src()
    assert "from stock_analyzer import tuneup_cover" in src
    assert "for _tud_n in _db_tuneup_driver_notes:" in src
    assert "st.caption(_tud_n)" in src


def test_build_daily_briefing_no_longer_computes_an_acted_set_for_the_tuneup():
    src = (ROOT / "stock_analyzer" / "daily_briefing.py").read_text(encoding="utf-8")
    assert "_acted_final" not in src and "acted_tickers" not in src
    assert "_TUNEUP_TRIM_DRIVER_TYPES" not in src and "_TUNEUP_PRIMARY_ANCHORED_TYPES" not in src

def test_beta_note_says_re_checked_not_re_sized():
    # The beta card is a fixed "Sell 50%": after the Act sale it is re-checked against
    # the new book's beta, never "re-sized" like a ceiling trim (Opus review 2026-11-26).
    for state in (asc.STATE_PARTIAL, asc.STATE_UNKNOWN):
        note = asc.cover_note(state, "AAA", "beta trim", tail=tc._BETA_AFTER)
        assert "re-checked against your new book's beta" in note
        assert "re-sized" not in note and "$" not in note and "**" not in note
    # The default ending is unchanged for the ceiling trim / Rebalancer callers.
    assert "re-sized from your new book" in asc.cover_note(asc.STATE_PARTIAL, "AAA", "ceiling trim")
