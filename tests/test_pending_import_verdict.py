"""TJDate U9a: `stock_analyzer.pending_import_verdict`, the verdicts and button gates of
📥 Pending Imports (docs/plans/pending-imports-verdicts-u9.md).

Fixtures are the real incidents already pinned in `test_journal_broker_match`: MRVL
10-30, GOOGL 10-30, the D0 gap, CSCO 13903, OKTA 11-02, MRVL #419. Time-context
matrix: a Saturday and a Thanksgiving visit, the DST change, morning vs evening,
a stale sync, late and back-dated hand logs in both actions, a naive `now_et`.
"""
import ast
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from stock_analyzer import constants as C
from stock_analyzer import journal_broker_match as jbm
from stock_analyzer import pending_import_verdict as pv
from stock_analyzer import trade_entry_checks as tec
from tests.test_journal_broker_match import (
    NOW, SYNC, _444_rows, _ambiguous_row_and_fills, _csco, _fuzz_case, _mrvl_419, et, fl,
    frame, jrow)

pytestmark = pytest.mark.fast

D30 = date(2026, 10, 30)

# Every string a run produced, for the plain-text pin at the end.
_SEEN_ROWS: list = []
_SEEN_TEXTS: list = []


def decide(journal_rows, fills, pending=None, synced=SYNC, now=NOW):
    if pending is None:
        pending = [f for f in fills if f["status"] == "pending"]
    res = pv.decide_pending_imports(pending, frame(*journal_rows), fills, synced, now)
    _SEEN_ROWS.extend(res["rows"])
    _SEEN_TEXTS.extend([res["list_text"], res["compare_text"], res["coverage_text"],
                        res["summary_text"]])
    return res


def by_txn(res, txn):
    got = [r for r in res["rows"] if r["txn_id"] == txn]
    assert len(got) == 1, (txn, [r["txn_id"] for r in res["rows"]])
    return got[0]


def pend(fills):
    return [dict(f, status="pending") for f in fills]


# ── contract and mapping ─────────────────────────────────────────────────────

def test_every_matcher_kind_has_an_explicit_verdict():
    for kind in jbm._SEVERITY:
        assert kind in pv._VERDICT_OF_KIND, f"matcher kind {kind} has no verdict in pending_import_verdict"
    assert set(pv._VERDICT_OF_KIND) == set(jbm._SEVERITY)
    for v in set(pv._VERDICT_OF_KIND.values()):
        assert v in pv._TONE and v in pv._LABEL and v in pv._SUMMARY_WORDS
    assert pv.V_OUTSIDE_WINDOW in pv._TONE and pv.V_NOT_COMPARED in pv._TONE


def test_the_kind_to_verdict_table_matches_the_spec():
    m = pv._VERDICT_OF_KIND
    for k in (jbm.KIND_LINK, jbm.KIND_RECONCILED):
        assert m[k] == pv.V_IN_JOURNAL
    for k in (jbm.KIND_REDATE, jbm.KIND_SPLIT, jbm.KIND_FORWARD, jbm.KIND_PRICE_FIX,
              jbm.KIND_MERGED_FILLS, jbm.KIND_QTY_MISMATCH, jbm.KIND_LINKED_DIFFERS):
        assert m[k] == pv.V_DIFFERS
    assert m[jbm.KIND_UNLOGGED] == pv.V_NOT_IN_JOURNAL
    assert m[jbm.KIND_AMBIGUOUS] == m[jbm.KIND_TOO_MANY] == pv.V_UNCLEAR
    assert m[jbm.KIND_NOT_YET_COMPARED] == pv.V_NOT_YET
    assert m[jbm.KIND_UNCHECKED] == pv.V_UNCHECKED
    assert m[jbm.KIND_SKIPPED_NON_EQUITY] == pv.V_OPTION


def _fake_finding(kind, txn="T1", pid=1, **over):
    f = {"kind": kind, "text": "Matcher text.", "txn_ids": [txn], "pending_ids": [pid],
         "journal_ids": [], "nearby_journal_ids": [], "fill_statuses": ["pending"],
         "ticker": "X", "action": "BUY", "fill_dates": [date(2026, 11, 3)],
         "broker_shares": 5.0, "broker_price": 100.0}
    f.update(over)
    return f


def _with_matcher(monkeypatch, findings):
    monkeypatch.setattr(pv._jbm, "match_journal_to_broker", lambda *a, **k: findings)
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 3))]
    return decide([], f)


def test_an_unknown_matcher_kind_asks_for_both_ticks(monkeypatch):
    r = _with_matcher(monkeypatch, [_fake_finding("BRAND_NEW_KIND", journal_ids=[4])])["rows"][0]
    assert r["verdict"] == pv.V_UNCLEAR and r["log_needs_tick"] and r["dismiss_needs_tick"]
    assert "#4" in r["log_tick_label"] and r["dismiss_tick_label"]
    assert not pv.log_allowed(r, False) and pv.log_allowed(r, True)
    assert not pv.dismiss_allowed(r, False) and pv.dismiss_allowed(r, True)


def test_journal_only_can_never_reach_a_row_but_reads_as_the_strict_unclear(monkeypatch):
    r = _with_matcher(monkeypatch, [_fake_finding(jbm.KIND_JOURNAL_ONLY)])["rows"][0]
    assert r["verdict"] == pv.V_UNCLEAR and r["dismiss_needs_tick"]


def test_a_txn_in_two_findings_takes_the_most_restrictive_mapping(monkeypatch):
    link = _fake_finding(jbm.KIND_LINK, journal_ids=[3])
    unl = _fake_finding(jbm.KIND_UNLOGGED, nearby_journal_ids=[5])
    for order in ([link, unl], [unl, link]):
        r = _with_matcher(monkeypatch, order)["rows"][0]
        assert r["log_enabled"] is False and r["log_needs_tick"] is True
        assert r["dismiss_needs_tick"] is True
        assert r["log_tick_label"] and r["dismiss_tick_label"] and r["log_disabled_reason"]
        assert not pv.log_allowed(r, True) and not pv.dismiss_allowed(r, False)


def test_a_finding_is_found_by_pending_id_when_the_txn_does_not_match(monkeypatch):
    f = _fake_finding(jbm.KIND_LINK, txn="OTHER", pid=1, journal_ids=[3])
    r = _with_matcher(monkeypatch, [f])["rows"][0]                  # the pending row is T1, id 1
    assert r["verdict"] == pv.V_IN_JOURNAL and r["reason"] == "matcher:LINK"


def test_button_gates_truth_table():
    row = {"log_enabled": True, "log_needs_tick": True, "dismiss_needs_tick": True}
    assert pv.log_allowed(row, False) is False and pv.log_allowed(row, True) is True
    assert pv.dismiss_allowed(row, False) is False and pv.dismiss_allowed(row, True) is True
    row = {"log_enabled": False, "log_needs_tick": False, "dismiss_needs_tick": False}
    assert pv.log_allowed(row, True) is False and pv.log_allowed(row, False) is False
    assert pv.dismiss_allowed(row, False) is True
    row = {"log_enabled": True, "log_needs_tick": False, "dismiss_needs_tick": False}
    assert pv.log_allowed(row, False) is True


# ── fixtures from the real incidents ─────────────────────────────────────────

def test_mrvl_1030_both_pending_reads_already_in_journal_and_log_is_disabled():
    j = [jrow(1, "MRVL", "BUY", 8, 196.99, et(2026, 10, 30, 14))]
    f = [fl(1, "M1", "MRVL", "BUY", 5, 200.00, D30), fl(2, "M2", "MRVL", "BUY", 3, 191.96, D30)]
    res = decide(j, f)
    assert res["list_state"] == "rows" and res["compare_state"] == "ok"
    for txn in ("M1", "M2"):
        r = by_txn(res, txn)
        assert r["verdict"] == pv.V_IN_JOURNAL and r["kind"] == "RECONCILED" and r["tone"] == "ok"
        assert r["journal_ids"] == [1] and r["log_enabled"] is False
        assert r["log_disabled_reason"] == ("this fill is already in your journal (#1), so "
                                            "logging it would count it twice")
        assert r["dismiss_primary"] is True and r["dismiss_needs_tick"] is False
        assert not pv.log_allowed(r, True) and pv.dismiss_allowed(r, False)
    assert by_txn(res, "M1")["text"].startswith("Your journal's BUY 8 MRVL on Fri Oct 30 (#1)")
    assert res["summary_text"] == "2 pending imports: 2 already in your journal."
    assert res["coverage_text"] == jbm.match_coverage(SYNC)["text"] and res["compare_text"] == ""


def _googl():
    j = [jrow(450, "GOOGL", "BUY", 3, 323.0, et(2026, 10, 28, 11), txn="G1"),
         jrow(452, "GOOGL", "SELL", 5, 278.82, et(2026, 10, 30, 11))]
    f = [fl(1, "G1", "GOOGL", "BUY", 3, 323.0, date(2026, 10, 28), status="logged"),
         fl(2, "G2", "GOOGL", "SELL", 6, 278.82, D30),
         fl(3, "G3", "GOOGL", "BUY", 1, 280.0, D30)]
    return j, f


def test_googl_1030_buy_1_is_not_in_journal_and_dismissing_needs_the_tick():
    res = decide(*_googl())
    b = by_txn(res, "G3")
    assert b["verdict"] == pv.V_NOT_IN_JOURNAL and b["kind"] == "UNLOGGED" and b["tone"] == "warning"
    assert b["headline"] == "BUY 1 GOOGL at 280.00, placed Fri Oct 30"
    assert b["log_enabled"] and b["log_primary"] and not b["log_needs_tick"]
    assert b["dismiss_needs_tick"] is True and b["dismiss_primary"] is False
    assert pv.dismiss_allowed(b, False) is False and pv.dismiss_allowed(b, True) is True
    assert pv.log_allowed(b, False) is True
    assert b["dismiss_tick_label"] == ("I've checked: this fill is already in my journal under a "
                                       "row the comparison didn't match")
    assert b["text"].startswith("Robinhood bought 1 GOOGL on Fri Oct 30 at 280.00")


def test_googl_1030_sell_6_differs_on_quantity_and_log_is_disabled():
    res = decide(*_googl())
    s = by_txn(res, "G2")
    assert s["verdict"] == pv.V_DIFFERS and s["kind"] == "QTY_MISMATCH" and s["tone"] == "warning"
    assert s["log_enabled"] is False and "(#452)" in s["log_disabled_reason"]
    assert s["dismiss_primary"] and not s["dismiss_needs_tick"]
    assert s["dismiss_note"] == "clears this row only; it doesn't change your journal"
    assert "is 6 shares; your journal (#452) has 5 shares" in s["text"]
    assert not pv.log_allowed(s, True)
    assert res["summary_text"] == ("2 pending imports: 1 not in your journal, 1 in your journal "
                                   "with differences.")


def test_d0_gap_combined_row_plus_relog_the_3_needs_a_tick_naming_the_combined_row():
    d = D30
    j = [jrow(10, "MRVL", "BUY", 8, 196.99, et(2026, 10, 30, 14)),
         jrow(11, "MRVL", "BUY", 5, 200.0, et(2026, 10, 30, 15))]
    f = [fl(1, "M1", "MRVL", "BUY", 5, 200.0, d), fl(2, "M2", "MRVL", "BUY", 3, 191.96, d)]
    res = decide(j, f)
    five, three = by_txn(res, "M1"), by_txn(res, "M2")
    assert five["verdict"] == pv.V_IN_JOURNAL and five["kind"] == "LINK" and not five["log_enabled"]
    assert three["verdict"] == pv.V_NOT_IN_JOURNAL and three["nearby_journal_ids"] == [10]
    assert three["log_primary"] and three["log_needs_tick"]
    assert three["log_tick_label"] == "I've checked #10: this fill is a separate trade"
    assert not pv.log_allowed(three, False) and pv.log_allowed(three, True)
    assert "Journal row #10" in three["text"] and "check it before logging" in three["text"]


def test_csco_13903_pending_reads_already_in_journal():
    j, f = _csco("c")
    res = decide(j, f)                                           # 13902 is 'logged', 13903 is pending
    assert [r["txn_id"] for r in res["rows"]] == ["13903"]
    r = res["rows"][0]
    assert r["verdict"] == pv.V_IN_JOURNAL and r["kind"] == "LINK" and r["journal_ids"] == [603]
    assert r["headline"] == "BUY 5 CSCO at 103.00, placed Wed Nov 4"
    assert not r["log_enabled"] and "(#603)" in r["log_disabled_reason"]


def test_okta_1102_price_fix_is_a_difference_and_log_is_disabled():
    j = [jrow(500, "OKTA", "BUY", 3, 171.88, "2026-11-02T22:08:00Z")]
    f = [fl(9, "O3", "OKTA", "BUY", 3, 189.14, date(2026, 11, 2))]
    r = decide(j, f)["rows"][0]
    assert r["verdict"] == pv.V_DIFFERS and r["kind"] == "PRICE_FIX" and r["journal_ids"] == [500]
    assert not r["log_enabled"] and r["dismiss_primary"] and r["dismiss_note"]
    assert "is at 189.14, not 171.88 (#500)" in r["text"]


def test_mrvl_419_merged_fills_disables_log_and_names_the_row_to_correct():
    j, f = _mrvl_419()
    res = decide(j, pend(f))                                       # all four fills pending
    for txn in ("M1", "M2", "M3"):
        r = by_txn(res, txn)
        assert r["verdict"] == pv.V_DIFFERS and r["kind"] == "MERGED_FILLS"
        assert r["log_enabled"] is False
        assert r["log_disabled_reason"].endswith(
            "If these were separate trades, #419 needs correcting first.")
        assert "would count it twice" in r["log_disabled_reason"]


def test_already_linked_and_still_pending_reads_in_journal_with_the_row_number():
    j, f = _mrvl_419()
    res = decide(j, pend(f))
    r = by_txn(res, "L424")                                        # #424 carries this txn, no finding
    assert r["verdict"] == pv.V_IN_JOURNAL and r["kind"] is None and r["reason"] == "already_linked"
    assert r["text"] == "Journal row #424 is already linked to this Robinhood record."
    assert r["journal_ids"] == [424] and not r["log_enabled"] and r["dismiss_primary"]
    assert not r["dismiss_needs_tick"]


def test_ambiguous_asks_for_a_log_tick_naming_the_rows_and_dismiss_stays_plain():
    j, f = _ambiguous_row_and_fills()
    res = decide(j, f)
    assert len(res["rows"]) == 4
    for r in res["rows"]:
        assert r["verdict"] == pv.V_UNCLEAR and r["kind"] == "AMBIGUOUS" and r["tone"] == "neutral"
        assert r["log_enabled"] and not r["log_primary"] and r["log_needs_tick"]
        assert r["log_tick_label"] == "I've checked #701: this fill is a separate trade"
        assert not pv.log_allowed(r, False) and pv.log_allowed(r, True)
        assert not r["dismiss_needs_tick"] and not r["dismiss_primary"]
        assert pv.dismiss_allowed(r, False)


def test_too_many_is_unclear_with_the_same_gates():
    from tests.test_journal_broker_match import _seven
    j, f = _seven([100.0, 97.0, 103.0, 100.0, 98.0, 101.0, 101.0], "BUY")
    res = decide(j, f)
    assert res["rows"] and {r["kind"] for r in res["rows"]} == {"TOO_MANY"}
    for r in res["rows"]:
        assert r["verdict"] == pv.V_UNCLEAR and r["log_needs_tick"] and not r["dismiss_needs_tick"]


def _not_yet_case(synced):
    j = [jrow(1, "X", "SELL", 10, 100.0, et(2026, 11, 9, 10))]
    return decide(j, [fl(1, "N1", "X", "SELL", 3, 90.0, date(2026, 11, 9))], synced=synced)


def test_not_yet_row_that_absorbed_the_fill_asks_for_a_log_tick_naming_the_row():
    r = _not_yet_case(et(2026, 11, 10, 9))["rows"][0]
    assert r["verdict"] == pv.V_NOT_YET and r["kind"] == "NOT_YET_COMPARED" and r["tone"] == "neutral"
    assert r["log_enabled"] and not r["log_primary"] and r["log_needs_tick"]
    assert r["log_tick_label"] == "I've checked #1: this fill is a separate trade"
    assert r["text"].endswith(" It may be this fill: check it before logging this one.")
    assert "isn't compared with Robinhood yet" in r["text"]
    assert not r["dismiss_needs_tick"] and not pv.log_allowed(r, False) and pv.log_allowed(r, True)


def test_unchecked_row_is_plain_and_points_at_robinhood():
    j = [jrow(1, "BAD", "BUY", 5, 100.0, et(2026, 11, 3, 10))]
    bad = fl(1, "B1", "BAD", "BUY", 5, 100.0, date(2026, 11, 3))
    bad["raw_json"], bad["price"] = {}, None
    r = decide(j, [bad])["rows"][0]
    assert r["verdict"] == pv.V_UNCHECKED and r["kind"] == "UNCHECKED" and r["tone"] == "neutral"
    assert r["log_enabled"] and not r["log_primary"] and not r["log_needs_tick"]
    assert not r["dismiss_primary"] and not r["dismiss_needs_tick"]
    assert r["text"].endswith(" Check it in Robinhood and 📋 History before using either button.")
    assert r["headline"] == "BUY 5 BAD, placed Tue Nov 3"          # an unreadable price is left out


def test_option_row_disables_log_and_makes_already_logged_primary():
    f = [fl(1, "OPT", "X", "BUY", 1, 2.5, date(2026, 11, 3), raw={"option_symbol": "X 261120C00100000"})]
    r = decide([], f)["rows"][0]
    assert r["verdict"] == pv.V_OPTION and r["kind"] == "SKIPPED_NON_EQUITY" and r["tone"] == "neutral"
    assert not r["log_enabled"] and r["log_disabled_reason"] == "the journal tracks stocks and ETFs"
    assert r["dismiss_primary"] and not r["dismiss_needs_tick"]
    assert r["text"]


# ── twin rule ────────────────────────────────────────────────────────────────

def _twin_fills(statuses):
    return [fl(i, f"T{i}", "X", "BUY", 5, 100.0, date(2026, 11, 3), status=s)
            for i, s in enumerate(statuses, start=1)]


def test_a_dismissed_unlogged_twin_flips_the_pending_link_row():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10))]
    res = decide(j, _twin_fills(["pending", "dismissed"]), pending=_twin_fills(["pending"]))
    r = by_txn(res, "T1")
    assert r["verdict"] == pv.V_NOT_IN_JOURNAL and r["reason"] == "twin" and r["kind"] == "LINK"
    assert r["text"].startswith(pv.TWIN_TEXT + " Your journal's BUY 5 X on Tue Nov 3 (#1) matches")
    assert r["log_enabled"] and r["log_primary"] and not r["log_needs_tick"]
    assert r["dismiss_needs_tick"] is True and not pv.dismiss_allowed(r, False)
    assert r["journal_ids"] == [1]


def test_without_a_twin_the_same_pending_link_row_stays_in_journal():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10))]
    r = decide(j, _twin_fills(["pending"]))["rows"][0]
    assert r["verdict"] == pv.V_IN_JOURNAL and r["reason"] == "matcher:LINK"


def test_two_pending_candidates_and_one_dismissed_twin_flip_exactly_one_lowest_txn_first():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10)),
         jrow(2, "X", "BUY", 5, 100.0, et(2026, 11, 3, 11))]
    fills = _twin_fills(["pending", "pending", "dismissed"])
    res = decide(j, fills, pending=fills[:2])
    assert [by_txn(res, t)["verdict"] for t in ("T1", "T2")] == [pv.V_NOT_IN_JOURNAL, pv.V_IN_JOURNAL]
    assert by_txn(res, "T1")["reason"] == "twin"
    assert sum(1 for r in res["rows"] if r["reason"] == "twin") == 1


def test_a_pending_twin_never_flips_a_row():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10))]
    res = decide(j, _twin_fills(["pending", "pending"]))
    verdicts = sorted(r["verdict"] for r in res["rows"])
    assert verdicts == [pv.V_IN_JOURNAL, pv.V_NOT_IN_JOURNAL]
    assert all(r["reason"] != "twin" for r in res["rows"])


def test_a_logged_status_unlogged_twin_also_flips():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10))]
    res = decide(j, _twin_fills(["pending", "logged"]), pending=_twin_fills(["pending"]))
    assert res["rows"][0]["reason"] == "twin"


def test_a_twin_with_a_different_price_or_session_does_not_flip():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10))]
    fills = [_twin_fills(["pending"])[0],
             fl(2, "T2", "X", "BUY", 5, 101.0, date(2026, 11, 3), status="dismissed")]
    assert decide(j, fills, pending=fills[:1])["rows"][0]["verdict"] == pv.V_IN_JOURNAL
    fills = [_twin_fills(["pending"])[0],
             fl(2, "T2", "X", "BUY", 5, 100.0, date(2026, 11, 4), status="dismissed")]
    assert decide(j, fills, pending=fills[:1])["rows"][0]["verdict"] == pv.V_IN_JOURNAL


def test_the_twin_rule_flips_a_differs_row_too_and_takes_the_twins_nearby_ticks():
    # journal #1 is the same trade as T1 a day late (REDATE); T2 is an identical dismissed unlogged fill.
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 4, 10))]
    fills = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 3)),
             fl(2, "T2", "X", "BUY", 5, 100.0, date(2026, 11, 3), status="dismissed")]
    base = jbm.match_journal_to_broker(frame(*j), fills, SYNC, NOW)
    assert {f["kind"] for f in base} == {"REDATE", "UNLOGGED"}
    r = by_txn(decide(j, fills, pending=fills[:1]), "T1")
    assert r["reason"] == "twin" and r["verdict"] == pv.V_NOT_IN_JOURNAL
    unl = [f for f in base if f["kind"] == "UNLOGGED"][0]
    assert r["nearby_journal_ids"] == unl["nearby_journal_ids"]
    assert r["log_needs_tick"] is bool(unl["nearby_journal_ids"])
    redate = [f for f in base if f["kind"] == "REDATE"][0]
    assert r["text"] == pv.TWIN_TEXT + " " + redate["text"]          # the difference text is kept


def test_an_already_linked_row_never_flips_on_the_twin_rule():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10), txn="T1")]
    fills = _twin_fills(["pending", "dismissed"])
    r = by_txn(decide(j, fills, pending=fills[:1]), "T1")
    assert r["reason"] == "already_linked" and r["verdict"] == pv.V_IN_JOURNAL and not r["log_enabled"]


@pytest.mark.parametrize("journal_shares,journal_price", [(5, 104.0), (6, 100.0)])
def test_a_linked_differs_row_never_flips_on_the_twin_rule(journal_shares, journal_price):
    # (a) price-only difference, (b) share-count difference: the row's txn is on journal row #1.
    j = [jrow(1, "X", "BUY", journal_shares, journal_price, et(2026, 11, 3, 10), txn="T1")]
    fills = _twin_fills(["pending", "dismissed"])
    base = jbm.match_journal_to_broker(frame(*j), fills, SYNC, NOW)
    assert {f["kind"] for f in base} == {"LINKED_DIFFERS", "UNLOGGED"}
    r = by_txn(decide(j, fills, pending=fills[:1]), "T1")
    assert r["verdict"] == pv.V_DIFFERS and r["kind"] == "LINKED_DIFFERS" and r["reason"] != "twin"
    assert r["log_enabled"] is False and not pv.log_allowed(r, True)
    assert r["dismiss_primary"] and not r["dismiss_needs_tick"]


def test_a_twin_listed_pending_is_pending_even_when_the_fills_read_says_dismissed():
    # The pending list and the fills read disagree (a write between the two reads): T2 is
    # in the pending list, so it is a visible row, not a hidden twin that flips T1.
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10))]
    fills = _twin_fills(["pending", "dismissed"])
    res = decide(j, fills, pending=_twin_fills(["pending", "pending"]))
    assert all(r["reason"] != "twin" for r in res["rows"])
    assert by_txn(res, "T1")["verdict"] == pv.V_IN_JOURNAL
    # Same for a twin known only by its id (no txn on the finding's row).
    pend_rows = [dict(f, snaptrade_txn_id=None) if f["id"] == 2 else f
                 for f in _twin_fills(["pending", "pending"])]
    res = decide(j, fills, pending=pend_rows)
    assert by_txn(res, "T1")["reason"] != "twin"


def test_option_trades_in_the_summary_are_singular_or_plural():
    one = decide([], [fl(1, "O1", "X", "BUY", 1, 2.5, date(2026, 11, 3), raw={"option_symbol": "X 1"})])
    assert one["summary_text"] == "1 pending import: 1 option trade."
    two = decide([], [fl(i, f"O{i}", "X", "BUY", 1, 2.5, date(2026, 11, 3), raw={"option_symbol": "X 1"})
                      for i in (1, 2)])
    assert two["summary_text"] == "2 pending imports: 2 option trades."


# ── window boundary ──────────────────────────────────────────────────────────

def test_window_boundary_the_start_session_is_compared_one_session_earlier_is_outside():
    start = NOW.date() - timedelta(days=C.JOURNAL_MATCH_LOOKBACK_DAYS)
    assert tec._is_trading_day(start), "pick a different NOW: the window start must be a session"
    older = tec._previous_trading_day(start)
    f = [fl(1, "IN", "X", "BUY", 3, 50.0, start), fl(2, "OUT", "X", "BUY", 4, 51.0, older)]
    res = decide([], f)
    inside, outside = by_txn(res, "IN"), by_txn(res, "OUT")
    assert inside["verdict"] == pv.V_NOT_IN_JOURNAL and inside["kind"] == "UNLOGGED"
    assert outside["verdict"] == pv.V_OUTSIDE_WINDOW and outside["kind"] is None
    assert outside["text"] == (f"This fill is older than the {C.JOURNAL_MATCH_LOOKBACK_DAYS}-day "
                               "comparison window, so it wasn't compared with your journal.")
    assert outside["log_enabled"] and not outside["log_primary"] and not outside["log_needs_tick"]
    assert not outside["dismiss_needs_tick"] and not outside["dismiss_primary"]
    assert outside["tone"] == "neutral"


# ── three-state ──────────────────────────────────────────────────────────────

def test_pending_none_is_offline_never_no_pending_imports():
    res = pv.decide_pending_imports(None, frame(), [], SYNC, NOW)
    assert res["list_state"] == "offline" and res["rows"] == [] and res["compare_state"] == "skipped"
    assert res["list_text"] == "⚠️ Pending imports couldn't be read this render."
    assert "No pending" not in res["list_text"]
    for bad in ([1], "x", [{"id": 1}, None]):
        assert pv.decide_pending_imports(bad, frame(), [], SYNC, NOW)["list_state"] == "offline"


def test_empty_pending_does_not_run_the_matcher_and_names_the_sync(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the matcher must not run when nothing is pending")
    monkeypatch.setattr(pv._jbm, "match_journal_to_broker", boom)
    res = pv.decide_pending_imports([], None, None, SYNC, NOW)       # inputs may even be unreadable
    assert res["list_state"] == "empty" and res["rows"] == [] and res["compare_state"] == "skipped"
    assert res["list_text"] == ("✓ No pending imports as of the last completed transaction sync "
                                "(Thu Nov 12, 9:00 AM ET).")
    unknown = pv.decide_pending_imports([], None, None, None, NOW)
    assert unknown["list_state"] == "empty"
    assert unknown["list_text"] == "✓ No pending imports. " + jbm.match_coverage(None)["text"]
    assert "couldn't be read" in unknown["list_text"]


def _assert_offline_rows(res, banner_part):
    assert res["list_state"] == "rows" and res["compare_state"] == "offline"
    assert banner_part in res["compare_text"] and res["compare_text"].startswith("⚠️")
    assert res["coverage_text"] == ""
    assert res["rows"]
    for r in res["rows"]:
        assert r["verdict"] == pv.V_NOT_COMPARED and r["label"] == "❔ Not compared"
        assert r["log_enabled"] and r["dismiss_primary"] is False and r["log_primary"] is False
        assert not r["log_needs_tick"] and not r["dismiss_needs_tick"]
        assert r["kind"] is None and r["reason"] == "compare_offline" and r["tone"] == "neutral"
        assert pv.log_allowed(r, False) and pv.dismiss_allowed(r, False)
        for word in ("already in your journal", "not in your journal", "differ", "matches"):
            assert word not in r["text"].lower()
    assert "not compared" in res["summary_text"]


def test_unreadable_journal_fills_or_both_read_not_compared_and_name_the_part():
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 3))]
    _assert_offline_rows(pv.decide_pending_imports(f, None, f, SYNC, NOW), "your journal couldn't be read")
    _assert_offline_rows(pv.decide_pending_imports(f, frame(), None, SYNC, NOW),
                         "Robinhood's records couldn't be read")
    _assert_offline_rows(pv.decide_pending_imports(f, None, None, SYNC, NOW),
                         "your journal and Robinhood's records couldn't be read")


def test_a_matcher_that_returns_none_reads_not_compared(monkeypatch):
    monkeypatch.setattr(pv._jbm, "match_journal_to_broker", lambda *a, **k: None)
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 3))]
    _assert_offline_rows(pv.decide_pending_imports(f, frame(), f, SYNC, NOW), "the comparison")


def test_a_matcher_that_raises_is_never_a_verdict(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("matcher blew up")
    monkeypatch.setattr(pv._jbm, "match_journal_to_broker", boom)
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 3))]
    _assert_offline_rows(pv.decide_pending_imports(f, frame(), f, SYNC, NOW),
                         "the comparison couldn't run")


def test_a_matcher_result_that_is_not_a_list_reads_not_compared(monkeypatch):
    monkeypatch.setattr(pv._jbm, "match_journal_to_broker", lambda *a, **k: "garbage")
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 3))]
    _assert_offline_rows(pv.decide_pending_imports(f, frame(), f, SYNC, NOW), "the comparison")


def test_a_list_row_missing_from_the_fills_read_is_not_compared_never_guessed():
    pending = [fl(9, "T9", "X", "BUY", 5, 100.0, date(2026, 11, 3))]
    res = pv.decide_pending_imports(pending, frame(), [], SYNC, NOW)
    r = res["rows"][0]
    assert res["compare_state"] == "ok"
    assert r["verdict"] == pv.V_NOT_COMPARED and r["reason"] == "no_finding_in_window"
    assert r["label"] == "❔ Not compared" and r["log_enabled"] and not r["log_needs_tick"]
    assert not r["dismiss_needs_tick"] and r["kind"] is None


def test_a_row_with_an_unreadable_trade_date_is_not_compared():
    pending = [dict(fl(9, "T9", "X", "BUY", 5, 100.0, date(2026, 11, 3)), trade_date=None)]
    r = pv.decide_pending_imports(pending, frame(), [], SYNC, NOW)["rows"][0]
    assert r["verdict"] == pv.V_NOT_COMPARED and r["reason"] == "unreadable_date"
    assert r["headline"] == "BUY 5 X at 100.00"


def test_the_list_read_only_picks_rows_and_the_verdict_comes_from_the_fills_read():
    # The pending list says 5 @ 100 pending; the fills read (same txn) carries the real status.
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10))]
    pending = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 3))]
    fills = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 3), status="dismissed")]
    r = pv.decide_pending_imports(pending, frame(*j), fills, SYNC, NOW)["rows"][0]
    assert r["verdict"] == pv.V_IN_JOURNAL and r["kind"] == "LINK"


# ── time-context matrix ──────────────────────────────────────────────────────

def _lone_unlogged():
    return [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 5))]


@pytest.mark.parametrize("now", [et(2026, 11, 14, 12), et(2026, 11, 26, 12), et(2026, 11, 1, 12),
                                 et(2026, 11, 15, 9, 30)])
def test_weekend_holiday_and_dst_day_visits_still_read_not_in_journal(now):
    # Saturday 11-14, Thanksgiving 11-26, the DST change day 11-01 (fill 10-30), Sunday.
    fill = (fl(1, "T1", "X", "BUY", 5, 100.0, D30) if now.date() == date(2026, 11, 1)
            else fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 5)))
    res = decide([], [fill], now=now, synced=now - timedelta(hours=3))
    r = res["rows"][0]
    assert r["verdict"] == pv.V_NOT_IN_JOURNAL and r["log_primary"] and r["dismiss_needs_tick"]
    assert res["compare_state"] == "ok"


def test_morning_and_evening_visits_on_the_same_et_date_are_identical():
    j, f = _googl()
    a = pv.decide_pending_imports(f[1:], frame(*j), f, SYNC, et(2026, 11, 12, 7, 0))
    b = pv.decide_pending_imports(f[1:], frame(*j), f, SYNC, et(2026, 11, 12, 20, 30))
    assert a == b


def test_dst_fill_placed_in_the_evening_edt_reads_as_a_redate_difference():
    j = [jrow(1, "X", "BUY", 5, 100.0, "2026-11-02T21:00:00Z")]                # 16:00 EST Mon 11-02
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, D30,
            placed_at=datetime.fromisoformat("2026-10-31T00:30:00+00:00"))]     # 8:30 PM EDT Fri 10-30
    r = decide(j, f)["rows"][0]
    assert r["verdict"] == pv.V_DIFFERS and r["kind"] == "REDATE" and not r["log_enabled"]


def test_a_six_day_stale_sync_leaves_unlogged_unchanged():
    f = _lone_unlogged()
    fresh = decide([], f)["rows"][0]
    stale = decide([], f, synced=NOW - timedelta(days=6))["rows"][0]
    unknown = decide([], f, synced=None)["rows"][0]
    for r in (fresh, stale, unknown):
        assert r["verdict"] == pv.V_NOT_IN_JOURNAL and r["log_primary"] and not r["log_needs_tick"]
    assert fresh["text"] == stale["text"] == unknown["text"]
    assert "6" not in decide([], f, synced=NOW - timedelta(days=6))["compare_text"]


def test_a_recent_hand_log_near_the_fill_reads_not_yet_with_a_log_tick_even_when_sync_is_stale():
    for synced in (et(2026, 11, 10, 9), NOW - timedelta(days=6)):
        r = _not_yet_case(synced)["rows"][0]
        assert r["verdict"] == pv.V_NOT_YET and r["log_needs_tick"]


@pytest.mark.parametrize("act", ["BUY", "SELL"])
def test_late_hand_log_reads_redate_and_log_is_disabled(act):
    j = [jrow(1, "X", act, 5, 100.0, et(2026, 11, 3, 10))]
    r = decide(j, [fl(1, "T1", "X", act, 5, 100.0, date(2026, 11, 2))])["rows"][0]
    assert r["verdict"] == pv.V_DIFFERS and r["kind"] == "REDATE" and not r["log_enabled"]


@pytest.mark.parametrize("act", ["BUY", "SELL"])
def test_back_dated_hand_log_reads_forward_and_log_is_disabled(act):
    j = [jrow(1, "X", act, 5, 100.0, et(2026, 11, 2, 10))]
    r = decide(j, [fl(1, "T1", "X", act, 5, 100.0, date(2026, 11, 3))])["rows"][0]
    assert r["verdict"] == pv.V_DIFFERS and r["kind"] == "FORWARD" and not r["log_enabled"]


def test_weekend_placed_fill_is_read_as_the_next_session():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 9, 10))]
    r = decide(j, [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 7))])["rows"][0]   # placed Saturday
    assert r["verdict"] == pv.V_IN_JOURNAL and r["kind"] == "LINK"
    assert r["headline"] == "BUY 5 X at 100.00, placed Sat Nov 7"


def test_a_naive_now_is_a_value_error_in_every_state():
    naive = datetime(2026, 11, 12, 12, 0)
    f = _lone_unlogged()
    for pending in (None, [], f):
        with pytest.raises(ValueError):
            pv.decide_pending_imports(pending, frame(), f, SYNC, naive)


# ── property: the invariants hold on the matcher's own seeded generator ──────

_NOT_LOGGABLE_KINDS = {"LINK", "RECONCILED", "REDATE", "SPLIT", "FORWARD", "PRICE_FIX",
                       "MERGED_FILLS", "QTY_MISMATCH", "LINKED_DIFFERS"}


def test_property_log_never_enabled_for_in_journal_fills_and_dismiss_tick_iff_not_in_journal():
    rng = random.Random(7)
    seen_verdicts, seen_reasons, twin_rows = set(), set(), 0
    for case in range(80):
        now = et(2026, 11, rng.randint(10, 25), 12)
        sync = rng.choice([None, now, now - timedelta(days=1), now - timedelta(days=3),
                           now - timedelta(days=6)])
        rows, fills = _fuzz_case(rng, now)
        linked_logged = [f for f in fills if str(f["snaptrade_txn_id"]).startswith("L")]
        pending = ([f for f in fills if f["status"] == "pending"]
                   + [dict(f, status="pending") for f in linked_logged])      # linked yet still pending
        res = pv.decide_pending_imports(pending, frame(*rows), fills, sync, now)
        if not pending:
            assert res["list_state"] == "empty", case
            continue
        assert res["list_state"] == "rows" and res["compare_state"] == "ok", case
        findings = jbm.match_journal_to_broker(frame(*rows), fills, sync, now)
        nonpending_unlogged = sum(1 for f in findings if f["kind"] == "UNLOGGED"
                                  and f["fill_statuses"][0] != "pending")
        n_twin = 0
        linked_txns = {x["broker_txn_id"] for x in rows if x["broker_txn_id"]}
        for r in res["rows"]:
            seen_verdicts.add(r["verdict"])
            seen_reasons.add(r["reason"].split(":")[0] if r["reason"].startswith("unknown") else r["reason"])
            twin = r["reason"] == "twin"
            n_twin += twin
            if r["kind"] in _NOT_LOGGABLE_KINDS or r["reason"] == "already_linked":
                if r["txn_id"] in linked_txns:
                    assert not twin, (case, r["txn_id"])        # a txn on a journal row never flips
                if not twin:
                    assert r["log_enabled"] is False, (case, r["txn_id"], r["kind"])
                    assert not pv.log_allowed(r, True), (case, r["txn_id"])
                else:
                    assert r["verdict"] == pv.V_NOT_IN_JOURNAL
            if r["verdict"] in (pv.V_IN_JOURNAL, pv.V_DIFFERS):
                assert r["log_enabled"] is False and r["log_disabled_reason"], (case, r["txn_id"])
            strict_unclear = r["verdict"] == pv.V_UNCLEAR and r["kind"] not in ("AMBIGUOUS", "TOO_MANY")
            assert not strict_unclear, (case, r["kind"])
            assert r["dismiss_needs_tick"] == (r["verdict"] == pv.V_NOT_IN_JOURNAL), (case, r["txn_id"], r["verdict"])
            assert pv.dismiss_allowed(r, True)
            if r["dismiss_needs_tick"]:
                assert not pv.dismiss_allowed(r, False) and r["dismiss_tick_label"]
            if r["log_needs_tick"]:
                assert r["log_tick_label"] and not pv.log_allowed(r, False)
            assert r["label"] and r["text"] and r["headline"] and r["reason"]
            assert "$" not in r["text"] and "**" not in r["text"], case
        assert n_twin <= nonpending_unlogged, (case, n_twin, nonpending_unlogged)
        twin_rows += n_twin
        # order independence of the whole decision
        r1, r2, r3 = list(rows), list(fills), list(pending)
        rng.shuffle(r1)
        rng.shuffle(r2)
        rng.shuffle(r3)
        again = pv.decide_pending_imports(r3, frame(*r1), r2, sync, now)
        assert sorted(again["rows"], key=lambda r: str(r["txn_id"])) == \
            sorted(res["rows"], key=lambda r: str(r["txn_id"])), case
    assert {pv.V_IN_JOURNAL, pv.V_NOT_IN_JOURNAL} <= seen_verdicts, seen_verdicts
    assert "already_linked" in seen_reasons, seen_reasons


# ── strings, purity, reuse ───────────────────────────────────────────────────

def test_every_string_is_plain_and_nonempty():
    # Run the whole fixture set once more so _SEEN_* is populated even when run alone.
    j, f = _googl()
    decide(j, f)
    decide(*_csco("c"))
    decide(*_ambiguous_row_and_fills())
    decide(*_444_rows())
    decide([], [fl(1, "OPT", "X", "BUY", 1, 2.5, date(2026, 11, 3), raw={"option_symbol": "X 261120C00100000"})])
    _not_yet_case(et(2026, 11, 10, 9))
    mj, mf = _mrvl_419()
    decide(mj, pend(mf))
    decide([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10))], _twin_fills(["pending", "dismissed"]),
           pending=_twin_fills(["pending"]))
    pv.decide_pending_imports(None, frame(), [], SYNC, NOW)
    _SEEN_TEXTS.extend([pv.LIST_OFFLINE_TEXT, pv.TWIN_TEXT, pv.DISMISS_TICK_NOT_IN_JOURNAL,
                        pv.NOT_YET_TAIL, pv.UNCHECKED_TAIL, pv.OFFLINE_ROW_TEXT, pv.OPTION_TEXT,
                        pv.NOT_COMPARED_TEXT, pv.UNREADABLE_DATE_TEXT, pv.DISMISS_NOTE_DIFFERS,
                        pv.OPTION_DISABLED_REASON])
    _SEEN_TEXTS.extend(pv._LABEL.values())
    assert len(_SEEN_ROWS) >= 10
    for r in _SEEN_ROWS:
        strings = [r["headline"], r["label"], r["text"], r["reason"]]
        if r["log_needs_tick"]:
            strings.append(r["log_tick_label"])
        if r["dismiss_needs_tick"]:
            strings.append(r["dismiss_tick_label"])
        if not r["log_enabled"]:
            strings.append(r["log_disabled_reason"])
        for s in strings:
            assert isinstance(s, str) and s.strip(), r
        for s in strings + [r["log_tick_label"], r["dismiss_tick_label"], r["log_disabled_reason"],
                            r["dismiss_note"]]:
            assert "$" not in s and "**" not in s and not s.lstrip().startswith("#"), s
            assert all(not line.startswith("#") for line in s.splitlines()), s
    for s in _SEEN_TEXTS:
        assert "$" not in s and "**" not in s and not s.lstrip().startswith("#"), s


def test_module_is_pure_no_streamlit_db_or_data_import_and_no_clock_read():
    src = Path(pv.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module)
            mods |= {f"{n.module}.{a.name}" for a in n.names}
    assert not any(m and (m.startswith("streamlit") or m in ("stock_analyzer.db", "stock_analyzer.data",
                                                          "stock_analyzer.market_time"))
                   for m in mods), mods
    assert "stock_analyzer.trade_entry_checks" in mods and "stock_analyzer.journal_broker_match" in mods
    clock = {"now", "utcnow", "today", "time", "monotonic", "perf_counter"}
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not (called & clock), called & clock
    assert "datetime" not in {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                              and n.module == "datetime" for a in n.names}
    for banned in ("or []", "or {}", "unsafe_allow_html", "utcnow", "date.today"):
        assert banned not in src, banned


def test_helpers_are_reused_not_copied():
    assert pv._jbm is jbm and pv._tec is tec
    assert pv._jbm._session_on_or_after is jbm._session_on_or_after
    for name in ("_txn_text", "_as_date", "_num", "_raw_price", "_same_shares", "_same_price",
                 "_fmt_date", "_fmt_shares", "_fmt_price", "_aware_et"):
        assert getattr(pv._tec, name) is getattr(tec, name), name
    for name in ("match_journal_to_broker", "match_coverage", "_sync_et", "_sync_when"):
        assert getattr(pv._jbm, name) is getattr(jbm, name), name
    defined = {n.name for n in ast.walk(ast.parse(Path(pv.__file__).read_text(encoding="utf-8")))
               if isinstance(n, ast.FunctionDef)}
    assert not defined & {"_same_shares", "_same_price", "_session_on_or_after", "_txn_text",
                          "_as_date", "_num", "_raw_price", "_sync_et", "_sync_when",
                          "match_journal_to_broker", "match_coverage"}


def test_lookback_is_the_matchers_constant_not_a_copy():
    src = Path(pv.__file__).read_text(encoding="utf-8")
    assert "from stock_analyzer.constants import JOURNAL_MATCH_LOOKBACK_DAYS" in src
    assert pv.JOURNAL_MATCH_LOOKBACK_DAYS is C.JOURNAL_MATCH_LOOKBACK_DAYS
    assert not any(isinstance(n, ast.Assign) and any(getattr(t, "id", "").isupper()
                   and isinstance(n.value, ast.Constant) and isinstance(n.value.value, (int, float))
                   for t in n.targets) for n in ast.parse(src).body), "no numeric constant belongs here"
