"""TJDate C5: `stock_analyzer.journal_broker_match`, the journal vs Robinhood matcher.

Spec: docs/plans/journal-broker-match-c5.md. Fixtures from the real incidents:
MRVL 10-30 (5 @ 200.00 + 3 @ 191.96 = 8 @ 196.99), #444-#446 (before the 11-02
fix), GOOGL 10-30 (SELL 5 vs 6), OKTA 11-02 (171.88 vs 189.14), CSCO 11-04.

Time-context matrix (C5 judges ET dates, never the time of day): an evening log,
the DST change, a weekend placement, Thanksgiving, the 11-27 early close, and the
D1 lag boundaries (Monday row, Friday row, Thanksgiving week).
"""
import ast
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import constants as C
from stock_analyzer import journal_broker_match as jbm
from stock_analyzer import trade_entry_checks as tec
from stock_analyzer.market_time import ET

pytestmark = pytest.mark.fast

_COLS = ["id", "ticker", "action", "shares", "price", "traded_at", "broker_txn_id", "notes"]


def et(y, mo, d, h=0, mi=0, s=0):
    return ET.localize(datetime(y, mo, d, h, mi, s))


NOW = et(2026, 11, 12, 12, 0)
SYNC = et(2026, 11, 12, 9, 0)


def jrow(id_, tk, act, sh, px, at, txn=None, notes=None):
    return {"id": id_, "ticker": tk, "action": act, "shares": sh, "price": px,
            "traded_at": at if isinstance(at, str) else at.isoformat(),
            "broker_txn_id": txn, "notes": notes}


def frame(*rows):
    return pd.DataFrame(list(rows), columns=_COLS)


def fl(pid, txn, tk, act, sh, px, placed, status="pending", placed_at=None, raw=None):
    r = {"price": px}
    if placed_at is not None:
        r["trade_date"] = placed_at.isoformat()
    if raw is not None:
        r.update(raw)
    return {"id": pid, "snaptrade_txn_id": txn, "ticker": tk, "action": act,
            "shares": sh, "price": round(px, 2), "trade_date": placed.isoformat(),
            "raw_json": r, "status": status}


def run(journal_rows, fills, synced=SYNC, now=NOW):
    return jbm.match_journal_to_broker(frame(*journal_rows), fills, synced, now)


def kinds(findings):
    return [f["kind"] for f in findings]


def one(findings, kind):
    got = [f for f in findings if f["kind"] == kind]
    assert len(got) == 1, (kind, kinds(findings))
    return got[0]


# ── contract ────────────────────────────────────────────────────────────────

def test_none_for_every_unusable_input():
    good = frame()
    assert jbm.match_journal_to_broker(None, [], SYNC, NOW) is None
    assert jbm.match_journal_to_broker([1], [], SYNC, NOW) is None
    assert jbm.match_journal_to_broker(good, None, SYNC, NOW) is None
    assert jbm.match_journal_to_broker(good, "x", SYNC, NOW) is None
    assert jbm.match_journal_to_broker(good, [1], SYNC, NOW) is None                 # non-dict fill
    bad_tk = fl(1, "T1", " ", "BUY", 1, 1.0, date(2026, 11, 2))
    assert jbm.match_journal_to_broker(good, [bad_tk], SYNC, NOW) is None             # blank ticker
    bad_act = fl(1, "T1", "X", "SPLIT", 1, 1.0, date(2026, 11, 2))
    assert jbm.match_journal_to_broker(good, [bad_act], SYNC, NOW) is None            # action
    nocol = pd.DataFrame([{"id": 1, "ticker": "X", "action": "BUY", "shares": 1,
                           "price": 1.0, "traded_at": "2026-11-02T15:00:00Z"}])
    assert jbm.match_journal_to_broker(nocol, [], SYNC, NOW) is None                  # no broker_txn_id


def test_empty_results_and_a_naive_now():
    assert jbm.match_journal_to_broker(frame(), [], SYNC, NOW) == []
    assert jbm.match_journal_to_broker(pd.DataFrame(), [], SYNC, NOW) == []           # no columns, empty: valid
    with pytest.raises(ValueError):
        jbm.match_journal_to_broker(frame(), [], SYNC, datetime(2026, 11, 12, 12, 0))


def test_every_pair_linked_is_clean():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10), txn="T1")]
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 3), status="logged")]
    assert run(j, f) == []


def test_module_is_pure():
    src = Path(jbm.__file__).read_text(encoding="utf-8")
    mods = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module)
            mods |= {f"{n.module}.{a.name}" for a in n.names}
    assert not any(m and (m.startswith("streamlit") or m in ("stock_analyzer.db", "stock_analyzer.data"))
                   for m in mods), mods


def test_helpers_are_reused_not_copied():
    assert jbm._tec is tec
    for name in ("_sessions_between", "_same_shares", "_same_price", "_raw_price", "fills_since"):
        assert getattr(jbm._tec, name) is getattr(tec, name), name


def test_session_on_or_after_agrees_with_the_trading_calendar():
    from stock_analyzer import data
    d = date(2026, 10, 1)
    while d <= date(2027, 1, 31):
        s = jbm._session_on_or_after(d)
        assert data.is_trading_day(s) and s >= d
        if data.is_trading_day(d):
            assert s == d
        else:
            assert all(not data.is_trading_day(d + timedelta(days=k)) for k in range((s - d).days))
        d += timedelta(days=1)


def test_constants_are_sane_and_pinned():
    assert isinstance(C.JOURNAL_MATCH_LOOKBACK_DAYS, int)
    assert C.JOURNAL_MATCH_LOOKBACK_DAYS <= C.ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS
    assert all(isinstance(x, int) for x in (C.JOURNAL_MATCH_MAX_FILLS, C.JOURNAL_MATCH_MAX_CANDIDATES,
                                            C.JOURNAL_MATCH_BROKER_LAG_SESSIONS))
    assert C.JOURNAL_MATCH_MAX_CANDIDATES >= C.JOURNAL_MATCH_MAX_FILLS >= 2
    assert C.JOURNAL_MATCH_BROKER_LAG_SESSIONS >= 0


# ── fixtures from the real incidents ────────────────────────────────────────

def test_mrvl_same_day_split_is_reconciled_not_a_difference():
    j = [jrow(1, "MRVL", "BUY", 8, 196.99, et(2026, 10, 30, 14))]
    f = [fl(1, "M1", "MRVL", "BUY", 5, 200.00, date(2026, 10, 30)),
         fl(2, "M2", "MRVL", "BUY", 3, 191.96, date(2026, 10, 30))]
    res = run(j, f)
    assert kinds(res) == ["RECONCILED"]
    r = res[0]
    assert r["rank"] == 170 and r["severity"] == "info"
    assert r["journal_ids"] == [1] and r["txn_ids"] == ["M1", "M2"]
    assert not [x for x in res if x["severity"] == "differs"]
    assert r["one_click_eligible"] is False and r["one_click_block"] == "needs_multi_fill_link"
    assert abs(r["broker_price"] - 196.985) < 1e-9


def _444_rows():
    j = [jrow(444, "GOOGL", "BUY", 5, 310.60, "2026-10-29T14:56:05Z"),
         jrow(445, "MSFT", "BUY", 3, 526.00, "2026-10-29T15:09:25Z"),
         jrow(446, "OKTA", "BUY", 7, 221.72, "2026-10-29T15:14:44Z")]
    d28, d29, d30 = date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)
    f = [fl(1, "MS1", "MSFT", "BUY", 2, 527.0, d28), fl(2, "MS2", "MSFT", "BUY", 1, 524.0, d29),
         fl(3, "OK1", "OKTA", "BUY", 5, 224.406, d28), fl(4, "OK2", "OKTA", "BUY", 2, 215.0, d28),
         fl(5, "GO1", "GOOGL", "BUY", 3, 323.0, d28), fl(6, "GO2", "GOOGL", "BUY", 1, 300.99, d28),
         fl(7, "GO3", "GOOGL", "BUY", 1, 283.0, d29), fl(8, "GO4", "GOOGL", "BUY", 1, 280.0, d30)]
    return j, f


def test_444_446_are_three_splits_and_one_unlogged():
    j, f = _444_rows()
    res = run(j, f)
    assert kinds(res) == ["UNLOGGED", "SPLIT", "SPLIT", "SPLIT"]
    assert [r["rank"] for r in res] == [30, 90, 90, 90]
    splits = {r["ticker"]: r for r in res if r["kind"] == "SPLIT"}
    assert splits["GOOGL"]["journal_ids"] == [444] and splits["GOOGL"]["txn_ids"] == ["GO1", "GO2", "GO3"]
    assert splits["MSFT"]["journal_ids"] == [445] and splits["MSFT"]["txn_ids"] == ["MS1", "MS2"]
    assert splits["OKTA"]["journal_ids"] == [446] and splits["OKTA"]["txn_ids"] == ["OK1", "OK2"]
    for tk, want in (("MSFT", 526.00), ("OKTA", 221.7186), ("GOOGL", 310.598)):
        r = splits[tk]
        assert abs(r["broker_price"] - want) < 1e-4, tk
        assert r["one_click_eligible"] is True and r["requires_fill_date_check"] is True
        assert r["correction_shape"] == "split" and r["one_click_block"] is None
        assert r["severity"] == "differs"
    un = res[0]
    assert (un["ticker"], un["action"], un["txn_ids"], un["broker_shares"]) == ("GOOGL", "BUY", ["GO4"], 1.0)
    assert un["holdings_effect"] == -1.0 and un["nearby_journal_ids"] == []


def test_googl_1030_qty_mismatch_ranks_ahead_of_the_dismissed_unlogged():
    j = [jrow(450, "GOOGL", "BUY", 3, 323.0, et(2026, 10, 28, 11), txn="G1"),
         jrow(452, "GOOGL", "SELL", 5, 278.82, et(2026, 10, 30, 11))]
    f = [fl(1, "G1", "GOOGL", "BUY", 3, 323.0, date(2026, 10, 28), status="logged"),
         fl(2, "G2", "GOOGL", "SELL", 6, 278.82, date(2026, 10, 30), status="dismissed"),
         fl(3, "G3", "GOOGL", "BUY", 1, 280.0, date(2026, 10, 30), status="dismissed")]
    res = run(j, f)
    assert kinds(res) == ["QTY_MISMATCH", "UNLOGGED"]
    q, u = res
    assert q["rank"] == 15 and q["holdings_effect"] == 1.0 and q["journal_ids"] == [452]
    assert "is 6 shares; your journal (#452) has 5 shares, so it counts 1 more GOOGL share." in q["text"]
    assert u["rank"] == 30 and u["txn_ids"] == ["G3"]
    assert "You marked it 'Already logged' in Pending Imports." in u["text"]


def test_okta_1102_is_a_price_fix_and_does_not_contest_446():
    j, f = _444_rows()
    j = [x for x in j if x["ticker"] == "OKTA"]
    f = [x for x in f if x["ticker"] == "OKTA"]
    j.append(jrow(500, "OKTA", "BUY", 3, 171.88, "2026-11-02T22:08:00Z"))     # 17:08 EST
    f.append(fl(9, "O3", "OKTA", "BUY", 3, 189.14, date(2026, 11, 2)))
    res = run(j, f)
    assert sorted(kinds(res)) == ["PRICE_FIX", "SPLIT"]
    p = one(res, "PRICE_FIX")
    assert p["journal_ids"] == [500] and p["txn_ids"] == ["O3"] and p["rank"] == 70
    assert p["correction_shape"] == "price" and p["one_click_eligible"] is True
    assert p["requires_fill_date_check"] is False and p["holdings_effect"] == 0.0
    assert one(res, "SPLIT")["journal_ids"] == [446]


def _csco(dup_state):
    older = jrow(601, "CSCO", "BUY", 3, 102.0, et(2026, 11, 4, 10), notes="RECOMMENDATION")
    newer = jrow(602, "CSCO", "BUY", 3, 102.0, et(2026, 11, 4, 11), notes="MANUAL")
    manual = jrow(603, "CSCO", "BUY", 5, 103.0, et(2026, 11, 4, 12), notes="MANUAL")
    f = [fl(1, "13902", "CSCO", "BUY", 3, 102.0, date(2026, 11, 4), status="logged"),
         fl(2, "13903", "CSCO", "BUY", 5, 103.0, date(2026, 11, 4))]
    if dup_state == "a":
        return [older, newer, manual], f
    if dup_state == "b":
        newer = dict(newer, broker_txn_id="13902")
        return [older, newer, manual], f
    return [older, manual], f


def test_csco_duplicate_row_reads_journal_only_with_a_duplicate_pointer():
    res = run(*_csco("a"))
    links = [r for r in res if r["kind"] == "LINK"]
    assert sorted(r["journal_ids"][0] for r in links) == [602, 603]               # the NEWER 3 @ 102 row
    jo = one(res, "JOURNAL_ONLY")
    assert jo["journal_ids"] == [601] and jo["possible_duplicate_of"] == 602 and jo["rank"] == 20
    assert "#602 has the same trade that day" in jo["text"]
    res = run(*_csco("b"))                                                        # the duplicate is linked
    assert kinds(res) == ["JOURNAL_ONLY", "LINK"]
    jo = res[0]
    assert jo["journal_ids"] == [601] and jo["possible_duplicate_of"] == 602
    assert res[1]["journal_ids"] == [603] and res[1]["txn_ids"] == ["13903"]


def test_csco_today_state_is_two_links():
    res = run(*_csco("c"))
    assert kinds(res) == ["LINK", "LINK"] and all(r["rank"] == 160 for r in res)
    assert {r["journal_ids"][0] for r in res} == {601, 603}
    assert all(r["one_click_eligible"] and r["correction_shape"] == "link"
               and not r["requires_fill_date_check"] for r in res)


def test_d0_gap_combined_row_plus_relog():
    d = date(2026, 10, 30)
    j = [jrow(10, "MRVL", "BUY", 8, 196.99, et(2026, 10, 30, 14)),
         jrow(11, "MRVL", "BUY", 5, 200.0, et(2026, 10, 30, 15))]
    f = [fl(1, "M1", "MRVL", "BUY", 5, 200.0, d), fl(2, "M2", "MRVL", "BUY", 3, 191.96, d)]
    res = run(j, f)
    assert kinds(res) == ["JOURNAL_ONLY", "UNLOGGED", "LINK"]
    jo, un, link = res
    assert jo["journal_ids"] == [10] and jo["possible_duplicate_of"] is None
    assert un["txn_ids"] == ["M2"] and un["nearby_journal_ids"] == [10]
    assert "Journal row #10" in un["text"] and "check it before logging" in un["text"]
    assert link["journal_ids"] == [11] and link["txn_ids"] == ["M1"]


# ── ambiguity and the explanation loop ──────────────────────────────────────

def test_ambiguous_tier_one_two_ways_to_make_the_same_row():
    d = date(2026, 10, 28)
    j = [jrow(701, "X", "BUY", 2, 100.0, et(2026, 10, 28, 11))]
    f = [fl(i, f"X{i}", "X", "BUY", 1, px, d)
         for i, px in enumerate((99.0, 101.0, 99.5, 100.5), start=1)]
    res = run(j, f)
    assert kinds(res) == ["AMBIGUOUS"]
    a = res[0]
    assert a["journal_ids"] == [701] and a["txn_ids"] == ["X1", "X2", "X3", "X4"]
    assert a["holdings_effect"] is None and a["rank"] == 120 and a["severity"] == "unresolved"


def test_ambiguous_tier_two_one_fill_two_rows():
    j = [jrow(801, "X", "BUY", 5, 50.0, et(2026, 11, 2, 11)),
         jrow(802, "X", "BUY", 5, 50.0, et(2026, 11, 4, 11))]
    f = [fl(1, "T1", "X", "BUY", 5, 50.0, date(2026, 11, 3))]
    res = run(j, f)
    assert kinds(res) == ["AMBIGUOUS"]
    assert res[0]["journal_ids"] == [801, 802] and res[0]["txn_ids"] == ["T1"]


def test_an_ambiguous_tier_one_row_does_not_block_an_unrelated_tier_two_row():
    d3 = date(2026, 11, 3)
    j = [jrow(901, "X", "BUY", 2, 100.0, et(2026, 11, 3, 11)),
         jrow(902, "X", "BUY", 5, 50.0, et(2026, 11, 5, 11))]
    f = [fl(i, f"X{i}", "X", "BUY", 1, px, d3)
         for i, px in enumerate((99.0, 101.0, 99.5, 100.5), start=1)]
    f.append(fl(9, "T9", "X", "BUY", 5, 50.0, date(2026, 11, 4)))
    res = run(j, f)
    assert sorted(kinds(res)) == ["AMBIGUOUS", "REDATE"]
    assert one(res, "REDATE")["journal_ids"] == [902]
    assert one(res, "AMBIGUOUS")["journal_ids"] == [901]


def _noise(n, start_id):
    return [fl(start_id + i, f"N{i}", "X", "BUY", 0.5, 1000.0, date(2026, 11, 5)) for i in range(n)]


def test_too_many_candidates_boundary_12_searched_13_gives_up():
    j = [jrow(1, "X", "BUY", 6, 100.0, et(2026, 11, 4, 11))]
    exact = [fl(i, f"E{i}", "X", "BUY", 1, 100.0, date(2026, 11, 3)) for i in range(1, 7)]
    res = run(j, exact + _noise(C.JOURNAL_MATCH_MAX_CANDIDATES - 6, 20))
    assert sorted(kinds(res)) == ["SPLIT"] + ["UNLOGGED"] * 6                       # 12 candidates: searched
    assert len(one(res, "SPLIT")["txn_ids"]) == 6
    res = run(j, exact + _noise(C.JOURNAL_MATCH_MAX_CANDIDATES - 5, 20))
    assert kinds(res) == ["TOO_MANY"]                                              # 13: no subset search
    assert len(res[0]["txn_ids"]) == 13 and res[0]["rank"] == 130
    assert "Too many Robinhood X BUY fills near" in res[0]["text"] and "(13)" in res[0]["text"]


def test_seven_fill_cross_day_exact_split_is_too_many_never_journal_only_plus_unlogged():
    days = [date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6),
            date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)]
    prices = [100.0, 97.0, 103.0, 102.0, 98.0, 101.0, 99.0]       # average 100.0, none at 100 on the row's day
    assert abs(sum(prices) / 7 - 100.0) < 1e-12
    j = [jrow(1, "X", "BUY", 7, 100.0, et(2026, 11, 6, 11))]
    f = [fl(i + 1, f"S{i}", "X", "BUY", 1, p, d) for i, (d, p) in enumerate(zip(days, prices))]
    res = run(j, f)
    assert kinds(res) == ["TOO_MANY"] and len(res[0]["txn_ids"]) == 7


def test_seven_fill_same_day_combined_row_is_reconciled_by_the_whole_day_rule():
    d = date(2026, 11, 4)
    prices = [99.0, 101.0, 98.0, 102.0, 97.0, 103.0, 100.0]
    j = [jrow(1, "X", "BUY", 7, 100.0, et(2026, 11, 4, 11))]
    f = [fl(i + 1, f"S{i}", "X", "BUY", 1, p, d) for i, p in enumerate(prices)]
    res = run(j, f)
    assert kinds(res) == ["RECONCILED"] and len(res[0]["txn_ids"]) == 7


# ── date kinds ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("act", ["BUY", "SELL"])
def test_forward_the_fill_is_placed_after_the_journal_date(act):
    j = [jrow(1, "X", act, 5, 100.0, et(2026, 11, 2, 10))]
    f = [fl(1, "T1", "X", act, 5, 100.0, date(2026, 11, 3))]
    res = run(j, f)
    assert kinds(res) == ["FORWARD"]
    r = res[0]
    assert r["one_click_eligible"] is False and r["one_click_block"] == "forward"
    assert r["rank"] == 100 and "so it can't have filled on" in r["text"]


@pytest.mark.parametrize("act", ["BUY", "SELL"])
def test_redate_logged_one_session_late_in_both_actions(act):
    j = [jrow(1, "X", act, 5, 100.0, et(2026, 11, 3, 10))]
    f = [fl(1, "T1", "X", act, 5, 100.0, date(2026, 11, 2))]
    r = run(j, f)[0]
    assert r["kind"] == "REDATE" and r["one_click_eligible"] is True
    assert r["correction_shape"] == "redate" and r["requires_fill_date_check"] is True
    assert r["fill_dates"] == [date(2026, 11, 2)] and r["journal_dates"] == [date(2026, 11, 3)]


def test_redate_pre_open_log_names_both_possibilities():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 5, 7, 30))]
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 4), placed_at=et(2026, 11, 4, 20, 30))]
    r = run(j, f)[0]
    assert r["kind"] == "REDATE" and r["requires_fill_date_check"] is True
    assert "placed 8:30 PM ET" in r["text"]
    assert "if it filled on Wed Nov 4, the journal date is wrong" in r["text"]
    assert "if the order waited and filled on Thu Nov 5, the journal is right" in r["text"]


def test_redate_before_the_repair_cutoff_is_not_one_click():
    now = et(2026, 12, 8, 12)                       # cutoff = 2026-11-03
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10))]
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 2))]
    r = run(j, f, synced=now, now=now)[0]
    assert r["kind"] == "REDATE"
    assert r["one_click_eligible"] is False and r["one_click_block"] == "outside_repair_lookback"
    now = et(2026, 12, 7, 12)                       # cutoff = 2026-11-02: inside
    assert run(j, f, synced=now, now=now)[0]["one_click_eligible"] is True


def test_linked_differs_shares_and_nothing_when_identical_or_absent():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10), txn="T1")]
    f = [fl(1, "T1", "X", "BUY", 6, 100.0, date(2026, 11, 3), status="logged")]
    r = run(j, f)[0]
    assert r["kind"] == "LINKED_DIFFERS" and r["rank"] == 110 and r["severity"] == "differs"
    assert "share count 5 in the journal, 6 in Robinhood" in r["text"]
    assert r["journal_ids"] == [1] and r["txn_ids"] == ["T1"]
    assert run(j, [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 3), status="logged")]) == []
    assert run(j, []) == []                                       # the fill is not in the input


def test_option_fill_is_skipped_never_unlogged():
    f = [fl(1, "OPT", "X", "BUY", 1, 2.5, date(2026, 11, 3),
            raw={"option_symbol": "X 261120C00100000"})]
    res = run([], f)
    assert kinds(res) == ["SKIPPED_NON_EQUITY"]
    assert res[0]["severity"] == "info" and res[0]["txn_ids"] == ["OPT"] and res[0]["rank"] == 180
    assert "look like option trades" in res[0]["text"]


def test_an_unreadable_fill_makes_its_whole_group_unchecked_others_still_match():
    j = [jrow(1, "BAD", "BUY", 5, 100.0, et(2026, 11, 3, 10)),
         jrow(2, "OK", "BUY", 5, 100.0, et(2026, 11, 3, 10))]
    bad = fl(1, "B1", "BAD", "BUY", 5, 100.0, date(2026, 11, 3))
    bad["raw_json"], bad["price"] = {}, None
    good = fl(2, "G1", "OK", "BUY", 5, 100.0, date(2026, 11, 3))
    res = run(j, [bad, good])
    assert sorted(kinds(res)) == ["LINK", "UNCHECKED"]
    u = one(res, "UNCHECKED")
    assert u["ticker"] == "BAD" and u["journal_ids"] == [1] and u["txn_ids"] == ["B1"]
    assert u["rank"] == 140 and "Robinhood record B1" in u["text"] and u["holdings_effect"] is None
    assert one(res, "LINK")["ticker"] == "OK"


def test_an_unreadable_journal_row_makes_its_group_unchecked():
    j = [jrow(1, "X", "BUY", None, 100.0, et(2026, 11, 3, 10))]
    res = run(j, [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 3))])
    assert kinds(res) == ["UNCHECKED"] and "journal row #1" in res[0]["text"]
    assert res[0]["journal_ids"] == [1] and res[0]["txn_ids"] == ["T1"]


# ── lag / stale sync (owner D1) ─────────────────────────────────────────────

def _lag(row_date, sync):
    j = [jrow(1, "X", "BUY", 5, 100.0, et(row_date.year, row_date.month, row_date.day, 10))]
    now = et(2026, 12, 1, 12)
    return run(j, [], synced=sync, now=now)


@pytest.mark.parametrize("row,sync,want", [
    (date(2026, 11, 2), et(2026, 11, 3, 17), "NOT_YET_COMPARED"),
    (date(2026, 11, 2), et(2026, 11, 4, 17), "JOURNAL_ONLY"),
    (date(2026, 11, 2), et(2026, 11, 2, 17), "NOT_YET_COMPARED"),         # a sync on the day itself
    (date(2026, 11, 20), et(2026, 11, 23, 17), "NOT_YET_COMPARED"),       # Friday row, Monday sync
    (date(2026, 11, 20), et(2026, 11, 24, 17), "JOURNAL_ONLY"),
    (date(2026, 11, 25), et(2026, 11, 27, 17), "NOT_YET_COMPARED"),       # Thanksgiving week
    (date(2026, 11, 25), et(2026, 11, 30, 17), "JOURNAL_ONLY"),
    (date(2026, 11, 2), None, "NOT_YET_COMPARED"),
    (date(2026, 11, 2), "garbage", "NOT_YET_COMPARED"),
])
def test_lag_boundaries(row, sync, want):
    res = _lag(row, sync)
    assert kinds(res) == [want]


def test_unknown_sync_says_so_and_known_sync_names_the_time():
    unk = _lag(date(2026, 11, 2), None)[0]
    assert "the time of the last completed broker sync couldn't be read" in unk["text"]
    known = _lag(date(2026, 11, 2), et(2026, 11, 3, 17))[0]
    assert "the last completed sync was Tue Nov 3, 5:00 PM ET" in known["text"]
    assert known["rank"] == 150 and known["severity"] == "unchecked"


@pytest.mark.parametrize("sync,through", [
    (et(2026, 11, 3, 17), date(2026, 11, 1)),      # Tue sync: Sun 11-01 is the latest date 2 sessions behind it
    (et(2026, 11, 4, 17), date(2026, 11, 2)),
    (et(2026, 11, 23, 17), date(2026, 11, 19)),
    (et(2026, 11, 24, 17), date(2026, 11, 22)),
    (et(2026, 11, 30, 17), date(2026, 11, 26)),    # Thanksgiving: the holiday itself
])
def test_match_coverage_judged_through(sync, through):
    cov = jbm.match_coverage(sync)
    assert cov["state"] == "known"
    assert cov["judged_through"] == through
    assert jbm._judged(through, sync.date()) and not jbm._judged(through + timedelta(days=1), sync.date())
    assert f"Journal rows dated after {tec._fmt_date(through)} aren't compared" in cov["text"]
    assert "last completed sync" in cov["text"] and cov["synced_at_et"] == sync


def test_match_coverage_unknown():
    for bad in (None, "garbage"):
        cov = jbm.match_coverage(bad)
        assert cov["state"] == "unknown" and cov["judged_through"] is None
        assert cov["synced_at_et"] is None
        assert cov["text"].startswith("The time of the last completed Robinhood sync couldn't be read")
    naive = jbm.match_coverage(datetime(2026, 11, 4, 22, 0))                    # read as UTC = 17:00 ET
    assert naive["state"] == "known" and naive["judged_through"] == date(2026, 11, 2)


def test_lag_zero_equals_the_c2b_coverage_rule(monkeypatch):
    monkeypatch.setattr(jbm, "JOURNAL_MATCH_BROKER_LAG_SESSIONS", 0)
    d = date(2026, 10, 25)
    while d <= date(2026, 12, 5):
        for s in (d - timedelta(days=1), d, d + timedelta(days=1), d + timedelta(days=4)):
            want = tec.broker_check_coverage(d, et(s.year, s.month, s.day, 17))["state"] == "covered"
            assert jbm._judged(d, s) is want, (d, s)
        d += timedelta(days=1)


def test_not_yet_absorbs_a_nearby_unmatched_fill_but_a_far_one_is_unlogged():
    j = [jrow(1, "X", "SELL", 10, 100.0, et(2026, 11, 9, 10))]
    near = fl(1, "N1", "X", "SELL", 3, 90.0, date(2026, 11, 9))
    far = fl(2, "F1", "X", "SELL", 3, 90.0, date(2026, 11, 2))
    res = run(j, [near], synced=et(2026, 11, 10, 9))
    assert kinds(res) == ["NOT_YET_COMPARED"] and res[0]["txn_ids"] == ["N1"]
    res = run(j, [near, far], synced=et(2026, 11, 10, 9))
    assert sorted(kinds(res)) == ["NOT_YET_COMPARED", "UNLOGGED"]
    assert one(res, "UNLOGGED")["txn_ids"] == ["F1"] and one(res, "UNLOGGED")["rank"] == 10
    assert one(res, "NOT_YET_COMPARED")["txn_ids"] == ["N1"]


# ── time-context matrix ─────────────────────────────────────────────────────

def test_evening_log_reads_as_its_et_date_link_not_redate():
    j = [jrow(1, "X", "BUY", 5, 100.0, "2026-11-05T00:12:00Z")]                # 11-04 19:12 EST
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 4))]
    res = run(j, f)
    assert kinds(res) == ["LINK"] and res[0]["journal_dates"] == [date(2026, 11, 4)]


def test_dst_fill_placed_20_30_edt_on_10_30_vs_a_16_00_est_anchor_on_11_02_is_redate():
    j = [jrow(1, "X", "BUY", 5, 100.0, "2026-11-02T21:00:00Z")]                # 16:00 EST, Mon 11-02
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 10, 30),
            placed_at=datetime.fromisoformat("2026-10-31T00:30:00+00:00"))]
    r = run(j, f)[0]
    assert r["kind"] == "REDATE" and r["fill_dates"] == [date(2026, 10, 30)]
    assert "placed 8:30 PM ET" in r["text"]


def test_weekend_placement_reads_as_the_next_session():
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 7))]                  # placed Saturday
    res = run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 9, 10))], f)
    assert kinds(res) == ["LINK"] and res[0]["placed_dates"] == [date(2026, 11, 7)]
    assert res[0]["fill_dates"] == [date(2026, 11, 9)]
    res = run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 7, 10))],
              [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 6))])           # journal Saturday, fill Friday
    assert kinds(res) == ["REDATE"]
    res = run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 8, 10))], f)          # journal Sunday, fill placed Saturday
    assert kinds(res) == ["FORWARD"]


def test_thanksgiving_window():
    now = et(2026, 12, 8, 12)
    res = run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 27, 10))],
              [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 26))], synced=now, now=now)
    assert kinds(res) == ["LINK"]                                               # placed on the holiday
    res = run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 26, 10))],
              [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 25))], synced=now, now=now)
    assert kinds(res) == ["REDATE"]                                             # journal on the holiday


def test_early_close_day_log_links():
    now = et(2026, 12, 8, 12)
    res = run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 27, 13, 30))],
              [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 27))], synced=now, now=now)
    assert kinds(res) == ["LINK"]


# ── window, invariants, text ────────────────────────────────────────────────

def test_a_finding_dated_entirely_before_the_window_is_dropped():
    old = date(2026, 9, 1)
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 9, 1, 10))]
    assert run(j, [fl(1, "T1", "X", "BUY", 3, 100.0, old)]) == []


def test_rows_and_fills_before_start_but_inside_the_pool_still_match():
    start = NOW.date() - timedelta(days=C.JOURNAL_MATCH_LOOKBACK_DAYS)           # 2026-10-08, a Thursday
    before = start - timedelta(days=1)
    assert tec._is_trading_day(before) and tec._is_trading_day(start) and before >= tec.fills_since(start)
    at = lambda d: et(d.year, d.month, d.day, 10)
    # a fill before `start` still explains a row on `start`
    res = run([jrow(1, "X", "BUY", 5, 100.0, at(start))], [fl(1, "T1", "X", "BUY", 5, 100.0, before)])
    assert kinds(res) == ["REDATE"]
    # a row before `start` still claims a fill on `start`
    res = run([jrow(1, "X", "BUY", 5, 100.0, at(before))], [fl(1, "T1", "X", "BUY", 5, 100.0, start)])
    assert kinds(res) == ["FORWARD"]
    # both before `start`: the finding is dropped
    assert run([jrow(1, "X", "BUY", 5, 100.0, at(before))],
               [fl(1, "T1", "X", "BUY", 5, 100.0, before)]) == []
    assert run([jrow(1, "X", "BUY", 5, 100.0, at(before))],
               [fl(1, "T1", "X", "BUY", 3, 100.0, before)]) == []


def test_fills_read_since_is_the_day_after_the_trading_day_before_the_pool_start():
    now = et(2026, 11, 12, 12)
    pool_start = tec.fills_since(now.date() - timedelta(days=C.JOURNAL_MATCH_LOOKBACK_DAYS))
    assert pool_start == date(2026, 10, 5)                                       # a Monday
    got = jbm.fills_read_since(now)
    assert got == tec._previous_trading_day(pool_start) + timedelta(days=1) == date(2026, 10, 3)
    assert got <= pool_start and not tec._is_trading_day(got)                     # the Saturday that rolls to Monday
    with pytest.raises(ValueError):
        jbm.fills_read_since(datetime(2026, 11, 12))


def test_a_weekend_placement_rolling_to_the_pool_start_is_still_a_candidate():
    # NOW 11-12: pool starts Mon 10-05. The fill is placed Sat 10-03 (session 10-05).
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 10, 3))]
    res = run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 10, 8, 10))], f)
    assert kinds(res) == ["REDATE"] and res[0]["fill_dates"] == [date(2026, 10, 5)]
    assert res[0]["placed_dates"] == [date(2026, 10, 3)]
    # placed Fri 10-02 (session 10-02, before the pool): not a candidate
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 10, 2))]
    assert kinds(run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 10, 8, 10))], f)) == ["JOURNAL_ONLY"]


def test_every_text_is_plain():
    j, f = _444_rows()
    res = run(j, f)
    res += run(*_csco("a")) + run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 2, 10), txn="T1")],
                                  [fl(1, "T1", "X", "BUY", 6, 100.0, date(2026, 11, 2))])
    for r in res:
        assert isinstance(r["text"], str) and r["text"]
        assert "$" not in r["text"] and "**" not in r["text"]


def _fuzz_case(rng, now):
    """1-3 groups; linked rows (some differing from their fill); dates on both sides
    of the pool start; fills of every status."""
    ps = jbm.fills_read_since(now)
    days = [ps + timedelta(days=k) for k in range(-6, (now.date() - ps).days + 1)]
    days = [d for d in days if rng.random() < 0.9]
    rows, fills, nid, ntx = [], [], 1, 1
    for tk in rng.sample(["A", "B", "C"], rng.randint(1, 3)):
        act = rng.choice(["BUY", "SELL"])
        center = rng.choice(days)
        near = [d for d in days if abs((d - center).days) <= 6]
        for _ in range(rng.randint(0, 6)):
            d = rng.choice(near)
            txn = None
            sh, px = rng.choice([1, 2, 3, 4, 5, 6]), rng.choice([50.0, 51.0, 52.0])
            if rng.random() < 0.15:
                txn = f"L{ntx}"
                ntx += 1
                fills.append(fl(1000 + ntx, txn, tk, act, sh if rng.random() < 0.7 else sh + 1, px, d,
                                status="logged"))
            rows.append(jrow(nid, tk, act, sh, px, et(d.year, d.month, d.day, rng.randint(9, 15)), txn=txn))
            nid += 1
        for _ in range(rng.randint(0, 9)):
            d = rng.choice(near)
            fills.append(fl(ntx, f"T{ntx}", tk, act, rng.choice([1, 2, 3, 4, 5]),
                            rng.choice([50.0, 51.0, 52.0]), d,
                            status=rng.choice(["pending", "dismissed", "logged"])))
            ntx += 1
    if rng.random() < 0.4:      # a row that merged fills of two days at an off price (MERGED_FILLS)
        d = rng.choice(days)
        rows.append(jrow(nid, "M", "BUY", 4, 60.0, et(d.year, d.month, d.day, 11)))
        for off, px in zip(rng.sample([1, 2, 3, 4], 2), (60.0, 50.0)):     # one fill anchors the row's price
            fills.append(fl(ntx, f"T{ntx}", "M", "BUY", 2, px, d - timedelta(days=off),
                            status=rng.choice(["pending", "dismissed", "logged"])))
            ntx += 1
    return rows, fills


def test_property_every_row_and_fill_in_exactly_one_finding_and_order_independent():
    rng = random.Random(7)
    seen_kinds = set()
    for case in range(120):
        now = et(2026, 11, rng.randint(10, 25), 12)
        sync = rng.choice([None, now, now - timedelta(days=1), now - timedelta(days=3),
                           now - timedelta(days=6)])
        start = now.date() - timedelta(days=C.JOURNAL_MATCH_LOOKBACK_DAYS)
        rows, fills = _fuzz_case(rng, now)
        res = jbm.match_journal_to_broker(frame(*rows), fills, sync, now)
        assert res is not None, case
        seen_kinds |= set(kinds(res))
        linked_txns = {r["broker_txn_id"] for r in rows if r["broker_txn_id"]}
        seen_j, seen_t = [], []
        for r in res:
            assert r["text"] and "$" not in r["text"] and "**" not in r["text"], case
            if r["kind"] == "LINKED_DIFFERS":
                continue
            seen_j += r["journal_ids"]
            seen_t += r["txn_ids"]
        assert len(set(seen_j)) == len(seen_j) and len(set(seen_t)) == len(seen_t), (case, kinds(res))
        for r in rows:                                         # every unlinked in-window row appears
            d = datetime.fromisoformat(r["traded_at"]).date()
            if r["broker_txn_id"] is None and d >= start:
                assert r["id"] in seen_j, (case, r["id"], d, kinds(res))
        for f in fills:                                        # every unlinked in-window fill appears
            if f["snaptrade_txn_id"] in linked_txns:
                continue
            sess = jbm._session_on_or_after(date.fromisoformat(f["trade_date"]))
            if sess >= start:
                assert f["snaptrade_txn_id"] in seen_t, (case, f["snaptrade_txn_id"], sess, kinds(res))
        r1, r2 = list(rows), list(fills)
        rng.shuffle(r1)
        rng.shuffle(r2)
        assert jbm.match_journal_to_broker(frame(*r1), r2, sync, now) == res, case
    assert {"NOT_YET_COMPARED", "LINKED_DIFFERS", "JOURNAL_ONLY", "UNLOGGED", "LINK",
            "MERGED_FILLS"} <= seen_kinds


def test_fills_with_no_txn_and_no_pending_id_are_ordered_by_content_not_input_order():
    j = [jrow(1, "X", "BUY", 3, 100.0, et(2026, 11, 4, 10))]
    a = {"id": None, "snaptrade_txn_id": None, "ticker": "X", "action": "BUY", "shares": 3,
         "price": 100.0, "trade_date": "2026-11-04", "raw_json": {"price": 100.0}, "status": "pending"}
    b = dict(a, shares=2, price=90.0, raw_json={"price": 90.0})
    c = dict(a, shares=1, price=95.0, raw_json={"price": 95.0})
    first = run(j, [a, b, c])
    assert first == run(j, [c, b, a]) == run(j, [b, a, c])


def _seven(prices, act):
    days = [date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6),
            date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)]
    j = [jrow(1, "X", act, 7, 100.0, et(2026, 11, 6, 11))]
    f = [fl(i + 1, f"S{i}", "X", act, 1, p, d) for i, (d, p) in enumerate(zip(days, prices))]
    return j, f


@pytest.mark.parametrize("act", ["BUY", "SELL"])
def test_too_many_also_fires_when_the_only_explanation_is_tier_three(act):
    # a same-day single fill at the row's price makes a tier-3 QTY_MISMATCH, yet a
    # 7-fill combination could still be the real story: never QTY_MISMATCH + UNLOGGED
    j, f = _seven([100.0, 97.0, 103.0, 100.0, 98.0, 101.0, 101.0], act)
    res = run(j, f)
    assert kinds(res) == ["TOO_MANY"] and len(res[0]["txn_ids"]) == 7


def test_a_lone_tier_three_row_with_few_candidates_is_still_a_qty_mismatch():
    j = [jrow(1, "X", "BUY", 7, 100.0, et(2026, 11, 6, 11))]
    f = [fl(1, "S1", "X", "BUY", 1, 100.0, date(2026, 11, 6)),
         fl(2, "S2", "X", "BUY", 1, 97.0, date(2026, 11, 4))]
    assert kinds(run(j, f)) == ["QTY_MISMATCH", "UNLOGGED"]


def test_weekend_placement_text_uses_the_placed_date_and_names_the_session():
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 7))]                   # placed Sat, session Mon 11-09
    r = run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 10, 10))], f)[0]
    assert r["kind"] == "REDATE"
    assert "is dated Sat Nov 7" in r["text"] and "is dated Mon Nov 9" not in r["text"]
    assert "(placed Sat Nov 7, so it filled Mon Nov 9 or later)" in r["text"]
    assert "if it filled on Mon Nov 9, the journal date is wrong" in r["text"]
    r = run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 8, 10))], f)[0]          # journal Sunday
    assert r["kind"] == "FORWARD"
    assert "is dated Sat Nov 7, the day it was placed" in r["text"]
    assert "can't have filled on Sun Nov 8" in r["text"]
    assert "(placed Sat Nov 7, so it filled Mon Nov 9 or later)" in r["text"]
    plain = run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10))],
                [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 4))])[0]        # a weekday placement
    assert plain["kind"] == "FORWARD" and "(placed" not in plain["text"]


def test_extreme_lag_still_gives_a_judged_through_date(monkeypatch):
    monkeypatch.setattr(jbm, "JOURNAL_MATCH_BROKER_LAG_SESSIONS", 40)
    cov = jbm.match_coverage(et(2026, 11, 30, 17))
    assert cov["judged_through"] is not None and jbm._judged(cov["judged_through"], date(2026, 11, 30))
    assert "Journal rows dated after" in cov["text"] and "No journal rows" not in cov["text"]


def test_share_words_follow_the_count():
    one_fill = run([jrow(1, "X", "BUY", 7, 100.0, et(2026, 11, 4, 10))],
                   [fl(1, "T1", "X", "BUY", 1, 100.0, date(2026, 11, 4))])
    q = one(one_fill, "QTY_MISMATCH")
    assert "is 1 share;" in q["text"] and "has 7 shares" in q["text"] and "6 more X shares." in q["text"]
    two = run([jrow(1, "X", "BUY", 2, 100.0, et(2026, 11, 4, 10))],
              [fl(1, "T1", "X", "BUY", 1, 100.0, date(2026, 11, 4))])
    assert "1 more X share." in one(two, "QTY_MISMATCH")["text"]


# ── MERGED_FILLS (owner's 2026-11-11 dry run, MRVL #419) and the wider UNLOGGED safety net ──

def _mrvl_419():
    j = [jrow(419, "MRVL", "BUY", 5, 260.0, "2026-10-14T13:51:32Z"),             # 09:51 EDT
         jrow(424, "MRVL", "BUY", 2, 265.0, et(2026, 10, 13, 16), txn="L424")]
    f = [fl(1, "L424", "MRVL", "BUY", 2, 265.0, date(2026, 10, 13), status="logged"),
         fl(2, "M1", "MRVL", "BUY", 2, 260.0, date(2026, 10, 14), status="dismissed"),
         fl(3, "M2", "MRVL", "BUY", 1, 260.0, date(2026, 10, 14), status="dismissed"),
         fl(4, "M3", "MRVL", "BUY", 2, 235.0, date(2026, 10, 15), status="dismissed")]
    return j, f


def test_mrvl_419_merged_fills_from_two_days_at_an_off_price():
    res = run(*_mrvl_419())
    assert kinds(res) == ["MERGED_FILLS"]                    # no UNLOGGED, no AMBIGUOUS, nothing for #424
    r = res[0]
    assert r["journal_ids"] == [419] and r["txn_ids"] == ["M1", "M2", "M3"]
    assert r["severity"] == "differs" and r["rank"] == 95 and r["holdings_effect"] == 0.0
    assert abs(r["broker_price"] - 250.0) < 1e-9 and r["journal_price"] == 260.0 and r["broker_shares"] == 5.0
    assert r["one_click_eligible"] is False and r["one_click_block"] == "forward"
    assert r["correction_shape"] == "split" and r["requires_fill_date_check"] is True
    assert r["text"] == ("Your journal's BUY 5 MRVL at 260.00 on Wed Oct 14 (#419) matches 3 Robinhood "
                         "fills dated Wed Oct 14, Thu Oct 15 totalling 5 shares at an average of 250.00, "
                         "not 260.00. Robinhood dates each fill by when its order was placed; check the "
                         "'Filled' dates. If these were separate trades, a Robinhood fill is missing from "
                         "your journal.")


def test_merged_fills_on_earlier_days_is_display_only():
    j = [jrow(1, "X", "BUY", 5, 260.0, et(2026, 10, 16, 10))]
    f = [fl(1, "T1", "X", "BUY", 2, 260.0, date(2026, 10, 14)),                  # the anchor: the row's own price
         fl(2, "T2", "X", "BUY", 3, 250.0, date(2026, 10, 15))]
    r = run(j, f)[0]
    assert r["kind"] == "MERGED_FILLS" and r["one_click_block"] == "display_only"
    assert r["one_click_eligible"] is False and r["requires_fill_date_check"] is True


def test_merged_fills_text_names_a_weekend_placement():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 10, 10))]
    f = [fl(1, "T1", "X", "BUY", 3, 100.0, date(2026, 11, 7)), fl(2, "T2", "X", "BUY", 2, 90.0, date(2026, 11, 9))]
    r = run(j, f)[0]
    assert r["kind"] == "MERGED_FILLS"
    assert "dated Sat Nov 7, Mon Nov 9" in r["text"]
    assert "(placed Sat Nov 7, so it filled Mon Nov 9 or later)" in r["text"] and "$" not in r["text"]


def test_an_exact_price_cross_day_split_wins_over_merged_fills():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 4, 10))]
    f = [fl(1, "A", "X", "BUY", 3, 100.0, date(2026, 11, 3)), fl(2, "B", "X", "BUY", 2, 100.0, date(2026, 11, 2)),
         fl(3, "C", "X", "BUY", 2, 110.0, date(2026, 11, 3))]                  # {A, C} would be a MERGED_FILLS
    res = run(j, f)
    assert sorted(kinds(res)) == ["SPLIT", "UNLOGGED"]
    assert one(res, "SPLIT")["txn_ids"] == ["B", "A"] and one(res, "UNLOGGED")["txn_ids"] == ["C"]


def test_a_row_with_only_qty_mismatch_and_merged_fills_resolves_as_merged_fills():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 4, 10))]
    f = [fl(1, "A", "X", "BUY", 4, 100.0, date(2026, 11, 4)),                    # a same-price QTY_MISMATCH on its own
         fl(2, "B", "X", "BUY", 1, 110.0, date(2026, 11, 3))]
    res = run(j, f)
    assert kinds(res) == ["MERGED_FILLS"] and res[0]["txn_ids"] == ["B", "A"]


def test_too_many_still_fires_when_the_only_explanations_are_merged_fills():
    prices = [100.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0]                   # one fill anchors; no 5 average 100
    days = [date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6),
            date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)]
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 6, 11))]
    f = [fl(i + 1, f"S{i}", "X", "BUY", 1, p, d) for i, (p, d) in enumerate(zip(prices, days))]
    _, jrecs = jbm._parse_journal(frame(*j))
    inf = jbm._explain(jrecs[0], jbm._parse_fills(f))
    assert inf["E"] and {e[0] for e in inf["E"]} == {3} and {e[1] for e in inf["E"]} == {"MERGED_FILLS"}
    assert inf["too_many"] is True
    res = run(j, f)
    assert kinds(res) == ["TOO_MANY"] and len(res[0]["txn_ids"]) == 7
    # the earlier 7 @ 100 repro: its 7 fills can't be a subset (max 6), so only tier 4 remains
    j7, f7 = _seven([100.0, 97.0, 103.0, 100.0, 98.0, 101.0, 101.0], "BUY")
    _, r7 = jbm._parse_journal(frame(*j7))
    inf7 = jbm._explain(r7[0], jbm._parse_fills(f7))
    assert {e[0] for e in inf7["E"]} == {4} and inf7["too_many"] is True
    assert kinds(run(j7, f7)) == ["TOO_MANY"]


@pytest.mark.parametrize("act,rank", [("BUY", 20), ("SELL", 40)])
def test_an_unanchored_share_count_coincidence_is_not_merged_fills_h1_h3(act, rank):
    # H1 / H3: a real unlogged fill that happens to have the row's share count
    j = [jrow(1, "X", act, 10, 95.0, et(2026, 11, 2, 10))]
    res = run(j, [fl(1, "U1", "X", act, 10, 120.0, date(2026, 11, 4))])
    assert sorted(kinds(res)) == ["JOURNAL_ONLY", "UNLOGGED"]
    assert one(res, "JOURNAL_ONLY")["rank"] == rank
    u = one(res, "UNLOGGED")
    assert u["nearby_journal_ids"] == [1] and "is nearby and matches no fill" in u["text"]


def test_an_unanchored_two_fill_set_is_not_merged_fills_h2():
    j = [jrow(1, "X", "BUY", 10, 95.0, et(2026, 11, 2, 10))]
    f = [fl(1, "U1", "X", "BUY", 4, 120.0, date(2026, 11, 3)), fl(2, "U2", "X", "BUY", 6, 121.0, date(2026, 11, 4))]
    res = run(j, f)
    assert sorted(kinds(res)) == ["JOURNAL_ONLY", "UNLOGGED", "UNLOGGED"]
    assert all(u["nearby_journal_ids"] == [1] for u in res if u["kind"] == "UNLOGGED")


def test_a_single_anchored_fill_with_the_rows_shares_is_a_redate_not_merged_fills():
    res = run([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 4, 10))],
              [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 3))])
    assert kinds(res) == ["REDATE"]


def _ambiguous_row_and_fills():
    j = [jrow(701, "X", "BUY", 2, 100.0, et(2026, 11, 4, 11))]
    f = [fl(i, f"X{i}", "X", "BUY", 1, px, date(2026, 11, 4))
         for i, px in enumerate((99.0, 101.0, 99.5, 100.5), start=1)]
    return j, f


def test_unlogged_names_a_nearby_ambiguous_row_with_the_not_fully_matched_sentence():
    j, f = _ambiguous_row_and_fills()
    f.append(fl(9, "U1", "X", "BUY", 3, 80.0, date(2026, 11, 5)))                 # claimed by nothing
    res = run(j, f)
    assert sorted(kinds(res)) == ["AMBIGUOUS", "UNLOGGED"]
    u = one(res, "UNLOGGED")
    assert u["txn_ids"] == ["U1"] and u["nearby_journal_ids"] == [701] and u["rank"] == 30
    assert u["text"].endswith(" Journal row #701 (BUY 2 on Wed Nov 4) is nearby and isn't fully "
                              "matched to Robinhood: check it before logging this one.")
    assert one(res, "AMBIGUOUS")["journal_ids"] == [701]                          # still one verdict per row
    f[-1] = fl(9, "U1", "X", "BUY", 3, 80.0, date(2026, 11, 10))                  # 4 sessions away: beyond the window
    far = one(run(j, f), "UNLOGGED")
    assert far["nearby_journal_ids"] == [] and "nearby" not in far["text"]


def test_unlogged_names_a_nearby_too_many_row_and_a_linked_row_that_differs_on_shares():
    j7, f7 = _seven([100.0, 97.0, 103.0, 100.0, 98.0, 101.0, 101.0], "BUY")
    res = run(j7, f7)
    assert kinds(res) == ["TOO_MANY"]                                              # every fill is claimed: nothing to name it on
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 4, 10), txn="L1")]
    f = [fl(1, "L1", "X", "BUY", 6, 100.0, date(2026, 11, 4), status="logged"),
         fl(2, "U1", "X", "BUY", 2, 90.0, date(2026, 11, 5))]
    res = run(j, f)
    assert sorted(kinds(res)) == ["LINKED_DIFFERS", "UNLOGGED"]
    u = one(res, "UNLOGGED")
    assert u["nearby_journal_ids"] == [1] and "isn't fully matched to Robinhood" in u["text"]
    assert one(res, "LINKED_DIFFERS")["journal_ids"] == [1]
    f[0] = fl(1, "L1", "X", "BUY", 5, 100.0, date(2026, 11, 4), status="logged")  # identical: not a reference
    assert one(run(j, f), "UNLOGGED")["nearby_journal_ids"] == []


def test_unlogged_lists_journal_only_and_not_fully_matched_rows_together():
    # a JOURNAL_ONLY row keeps its own sentence; the ambiguous row gets the new one
    j = [jrow(701, "X", "BUY", 2, 100.0, et(2026, 11, 2, 11)),
         jrow(702, "X", "BUY", 9, 70.0, et(2026, 11, 5, 11))]
    f = [fl(i, f"X{i}", "X", "BUY", 1, px, date(2026, 11, 2))
         for i, px in enumerate((99.0, 101.0, 99.5, 100.5), start=1)]
    f.append(fl(9, "U1", "X", "BUY", 3, 80.0, date(2026, 11, 4)))
    u = one(run(j, f), "UNLOGGED")
    assert u["nearby_journal_ids"] == [701, 702]
    assert "Journal row #702 (BUY 9 on Thu Nov 5) is nearby and matches no fill" in u["text"]
    assert "Journal row #701 (BUY 2 on Mon Nov 2) is nearby and isn't fully matched to Robinhood" in u["text"]


# ── cross-consistency with the C2b single-entry check ───────────────────────

def _unlinked_for_c2b(fills, journal_rows):
    return tec.unlinked_fills(fills, frame(*journal_rows))


def test_c2b_match_same_day_is_a_c5_link():
    entry_row = jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 4, 10))
    f = [fl(1, "T1", "X", "BUY", 5, 100.0, date(2026, 11, 4))]
    c2b = tec.pending_fill_findings(
        {"ticker": "X", "action": "BUY", "shares": 5, "price": 100.0, "trade_date": date(2026, 11, 4)},
        _unlinked_for_c2b(f, []))
    assert [x["kind"] for x in c2b] == ["MATCH_SAME_DAY"] and c2b[0]["txn_ids"] == ["T1"]
    res = run([entry_row], f)
    assert kinds(res) == ["LINK"] and res[0]["txn_ids"] == c2b[0]["txn_ids"] and res[0]["journal_ids"] == [1]


def test_c2b_and_c5_agree_on_mrvl_and_okta():
    d = date(2026, 10, 30)
    f = [fl(1, "M1", "MRVL", "BUY", 5, 200.00, d), fl(2, "M2", "MRVL", "BUY", 3, 191.96, d)]
    entry = {"ticker": "MRVL", "action": "BUY", "shares": 8, "price": 196.99, "trade_date": d}
    assert tec.pending_fill_findings(entry, _unlinked_for_c2b(f, [])) == []
    assert kinds(run([jrow(1, "MRVL", "BUY", 8, 196.99, et(2026, 10, 30, 14))], f)) == ["RECONCILED"]
    of = [fl(1, "O3", "OKTA", "BUY", 3, 189.14, date(2026, 11, 2))]
    oentry = {"ticker": "OKTA", "action": "BUY", "shares": 3, "price": 171.88, "trade_date": date(2026, 11, 2)}
    c2b = tec.pending_fill_findings(oentry, _unlinked_for_c2b(of, []))
    assert [x["kind"] for x in c2b] == ["PRICE_DIFFERS"]
    assert kinds(run([jrow(1, "OKTA", "BUY", 3, 171.88, "2026-11-02T22:08:00Z")], of)) == ["PRICE_FIX"]
