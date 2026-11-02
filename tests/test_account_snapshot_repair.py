"""Pure tests for stock_analyzer/account_snapshot_repair.py (bug register N1,
docs/plans/account-snapshot-repair.md TEST MATRIX, pure cases)."""
from datetime import date, datetime

import pandas as pd
import pytest

from stock_analyzer import account_snapshot_repair as R
from stock_analyzer.account import account_money_fields
from stock_analyzer.market_time import ET

pytestmark = pytest.mark.fast

NOW = ET.localize(datetime(2026, 11, 1, 12, 0, 0))
D30 = date(2026, 10, 30)
CASH_AS_OF_30 = "2026-10-30T21:00:00+00:00"        # 17:00 EDT
ANCHOR_30 = "2026-10-30T20:00:00+00:00"            # 16:00 EDT, the import anchor


def trades(*rows):
    """rows: (id, ticker, action, shares, traded_at)"""
    return pd.DataFrame(
        [{"id": i, "ticker": t, "action": a, "shares": s, "price": 10.0, "traded_at": ts}
         for i, t, a, s, ts in rows]
    )


def row(d="2026-10-30", gross=1000.0, cash=-500.0, book=None, max_id=4,
        cash_as_of=CASH_AS_OF_30, rate=0.25, **extra):
    book = {"AAA": {"shares": 10, "close": 100.0}} if book is None else book
    m = (account_money_fields(gross, cash, rate) if cash is not None else
         {"net_equity": None, "leverage": None, "cushion": None, "call_distance_pct": None})
    r = {"snapshot_date": d, "gross_book": gross, "cash_balance": cash,
         "maintenance_rate": rate, "cash_as_of": cash_as_of,
         "journal_max_trade_id": max_id, "book_positions": book,
         "original_gross_book": None, "repaired_at": None, **m}
    r.update(extra)
    return r


BASE = (1, "AAA", "BUY", 10, "2026-10-01T20:00:00+00:00")


def late_bbb(id_=5, sh=2, ts=ANCHOR_30):
    return (id_, "BBB", "BUY", sh, ts)


CLOSES = {("BBB", D30): 50.0}


# -- late_trades / stale_rows -------------------------------------------------

def test_saturday_logged_friday_buy_flagged_and_repaired():
    t = trades(BASE, late_bbb())
    assert R.stale_rows([row()], t) == ["2026-10-30"]
    reps, refs = R.plan_repairs([row()], t, CLOSES, NOW)
    assert refs == []
    r = reps[0]
    assert r["gross_book"] == 1100.0
    assert r["book_positions"]["BBB"] == {"shares": 2.0, "close": 50.0}
    assert r["net_equity"] == 1100.0 - 500.0
    assert r["journal_max_trade_id"] == 5
    assert r["original_gross_book"] == 1000.0
    assert r["repaired_at"] == NOW.isoformat()
    assert r["cash_balance"] == -500.0 and r["cash_as_of"] == CASH_AS_OF_30


def test_late_trades_returns_the_late_rows_only():
    t = trades(BASE, late_bbb(), (6, "CCC", "BUY", 1, "2026-10-31T20:00:00+00:00"))
    late = R.late_trades(row(), t)
    assert list(late["id"]) == [5]          # 6 is after cash_as_of


def test_id_equal_to_journal_max_trade_id_is_not_late():
    t = trades(BASE, late_bbb(id_=4))
    assert R.late_trades(row(), t).empty
    assert R.stale_rows([row()], t) == []


def test_holiday_dated_trade_applies_to_next_trading_day_row_only():
    thanks = "2026-11-26T21:00:00+00:00"    # 16:00 EST anchor on Thanksgiving
    t = trades(BASE, (9, "BBB", "BUY", 2, thanks))
    before = row(d="2026-11-25", max_id=8, cash_as_of="2026-11-25T21:00:00+00:00")
    after = row(d="2026-11-27", max_id=8, cash_as_of="2026-11-27T21:00:00+00:00")
    assert R.stale_rows([before, after], t) == ["2026-11-27"]


def test_dst_trade_et_date_with_null_cash_as_of():
    r = row(d="2026-11-02", cash=None, cash_as_of=None, max_id=9)
    on_day = trades(BASE, (10, "BBB", "BUY", 2, "2026-11-02T20:30:00-05:00"))
    same_instant = trades(BASE, (10, "BBB", "BUY", 2, "2026-11-03T01:30:00+00:00"))
    next_et_day = trades(BASE, (10, "BBB", "BUY", 2, "2026-11-03T00:30:00-05:00"))
    assert R.stale_rows([r], on_day) == ["2026-11-02"]
    assert R.stale_rows([r], same_instant) == ["2026-11-02"]
    assert R.stale_rows([r], next_et_day) == []


def test_anchor_at_1600_est_counts_as_at_or_before_cash_as_of_1600_30():
    r = row(d="2026-11-02", max_id=9, cash_as_of="2026-11-02T21:00:30+00:00")
    t = trades(BASE, (10, "BBB", "BUY", 2, "2026-11-02T16:00:00-05:00"))
    assert list(R.late_trades(r, t)["id"]) == [10]


def test_after_hours_same_evening_fill_is_not_added():
    after = "2026-10-30T22:00:00+00:00"      # 18:00 EDT, after the 17:00 cash read
    t = trades(BASE, late_bbb(ts=after))
    assert R.late_trades(row(), t).empty
    assert R.stale_rows([row()], t) == []
    assert R.plan_repairs([row()], t, CLOSES, NOW) == ([], [])


def test_book_that_already_has_the_trade_is_not_stale_race():
    book = {"AAA": {"shares": 10, "close": 100.0}, "BBB": {"shares": 2, "close": 50.0}}
    t = trades(BASE, late_bbb())
    r = row(book=book, gross=1100.0)
    assert R.stale_rows([r], t) == []


def test_race_one_ticker_reflected_other_not_repairs_only_the_missing_one():
    book = {"AAA": {"shares": 10, "close": 100.0}, "BBB": {"shares": 2, "close": 50.0}}
    t = trades(BASE, late_bbb(), (6, "CCC", "BUY", 3, ANCHOR_30))
    reps, refs = R.plan_repairs([row(book=book, gross=1100.0)], t,
                                {("CCC", D30): 20.0}, NOW)
    assert refs == []
    assert reps[0]["gross_book"] == 1100.0 + 60.0
    assert reps[0]["book_positions"]["BBB"] == {"shares": 2, "close": 50.0}
    assert reps[0]["journal_max_trade_id"] == 6


def test_d_minus_1_untouched_and_d_plus_1_already_reflected():
    t = trades(BASE, late_bbb())
    d29 = row(d="2026-10-29", cash_as_of="2026-10-29T21:00:00+00:00", max_id=4)
    d30 = row()
    d02 = row(d="2026-11-02", cash_as_of="2026-11-02T21:00:00+00:00", max_id=5,
              book={"AAA": {"shares": 10, "close": 100.0}, "BBB": {"shares": 2, "close": 50.0}})
    assert R.stale_rows([d29, d30, d02], t) == ["2026-10-30"]
    reps, _ = R.plan_repairs([d29, d30, d02], t, CLOSES, NOW)
    assert [r["snapshot_date"] for r in reps] == ["2026-10-30"]


def test_late_sell_lowers_gross():
    t = trades(BASE, (5, "AAA", "SELL", 4, ANCHOR_30))
    reps, refs = R.plan_repairs([row()], t, {}, NOW)
    assert refs == []
    assert reps[0]["gross_book"] == 600.0
    assert reps[0]["book_positions"]["AAA"]["shares"] == 6.0


def test_late_full_exit_sell_drops_the_ticker():
    t = trades(BASE, (5, "AAA", "SELL", 10, ANCHOR_30))
    reps, _ = R.plan_repairs([row()], t, {}, NOW)
    assert reps[0]["gross_book"] == 0.0
    assert reps[0]["book_positions"] == {}


def test_oversell_refused():
    t = trades(BASE, (5, "AAA", "SELL", 20, ANCHOR_30))
    reps, refs = R.plan_repairs([row()], t, {}, NOW)
    assert reps == []
    assert "oversell" in refs[0]["reason"] and refs[0]["snapshot_date"] == "2026-10-30"


def test_null_cash_repairs_gross_and_leaves_money_fields_none():
    t = trades(BASE, late_bbb())
    r = row(cash=None)
    reps, _ = R.plan_repairs([r], t, CLOSES, NOW)
    x = reps[0]
    assert x["gross_book"] == 1100.0
    assert x["cash_balance"] is None
    assert x["net_equity"] is None and x["leverage"] is None
    assert x["cushion"] is None and x["call_distance_pct"] is None


def test_missing_close_refused_and_never_carried_forward():
    t = trades(BASE, late_bbb())
    other_day = {("BBB", date(2026, 10, 29)): 49.0}
    reps, refs = R.plan_repairs([row()], t, other_day, NOW)
    assert reps == []
    assert "no close" in refs[0]["reason"]


def test_fractional_half_share_buy_exact():
    t = trades(BASE, late_bbb(sh=0.5))
    reps, _ = R.plan_repairs([row()], t, {("BBB", D30): 101.0}, NOW)
    assert reps[0]["gross_book"] == 1050.5
    assert reps[0]["book_positions"]["BBB"]["shares"] == 0.5


def test_legacy_null_provenance_row_is_never_flagged():
    t = trades(BASE, late_bbb())
    legacy = row(max_id=None)
    assert R.late_trades(legacy, t).empty
    assert R.stale_rows([legacy], t) == []
    assert R.plan_repairs([legacy], t, CLOSES, NOW) == ([], [])


def test_max_id_but_no_book_positions_with_late_trade_is_flagged_and_refused():
    t = trades(BASE, late_bbb())
    r = row()
    r["book_positions"] = None
    assert R.stale_rows([r], t) == ["2026-10-30"]
    reps, refs = R.plan_repairs([r], t, CLOSES, NOW)
    assert reps == []
    assert len(refs) == 1 and "no readable book_positions" in refs[0]["reason"]


def test_max_id_but_no_book_positions_without_late_trade_not_flagged():
    t = trades(BASE)
    r = row()
    r["book_positions"] = None
    assert R.stale_rows([r], t) == []
    assert R.plan_repairs([r], t, CLOSES, NOW) == ([], [])


def test_non_dataframe_trades_raises_type_error():
    with pytest.raises(TypeError):
        R.stale_rows([row()], [{"id": 1}])
    with pytest.raises(TypeError):
        R.plan_repairs([row()], {"id": 1}, CLOSES, NOW)


def test_frame_missing_id_or_traded_at_is_could_not_check():
    t = trades(BASE, late_bbb())
    for col in ("id", "traded_at"):
        bad = t.drop(columns=[col])
        assert R.stale_rows([row()], bad) is None
        reps, refs = R.plan_repairs([row()], bad, CLOSES, NOW)
        assert reps == [] and refs[0]["snapshot_date"] is None


def test_trade_logged_between_broker_run_and_eod_both_halves():
    """An 18:00 ET fill (after the 17:00 cash read) logged before EOD, so the
    EOD book already holds it. Untouched ticker: row not flagged. A ticker that
    ALSO has an in-cutoff late trade: the replay (cutoff-limited) excludes the
    after-hours fill while the book includes it, so the repair refuses."""
    after = "2026-10-30T22:00:00+00:00"
    book = {"AAA": {"shares": 10, "close": 100.0}, "BBB": {"shares": 2, "close": 50.0}}
    only_after = trades(BASE, late_bbb(ts=after))
    assert R.stale_rows([row(book=book, gross=1100.0)], only_after) == []
    both = trades(BASE, (5, "BBB", "BUY", 1, ANCHOR_30), (6, "BBB", "BUY", 1, after))
    r = row(book=book, gross=1100.0)      # book holds 2 BBB (incl. the after-hours one)
    assert R.stale_rows([r], both) == ["2026-10-30"]
    reps, refs = R.plan_repairs([r], both, CLOSES, NOW)
    # replay at the cutoff = 1 BBB; stored 2 + late 1 != 1 -> refused, never repaired
    assert reps == [] and len(refs) == 1


def test_split_among_late_trades_refused():
    t = trades(BASE, (5, "AAA", "SPLIT", 20, ANCHOR_30))
    reps, refs = R.plan_repairs([row()], t, {}, NOW)
    assert reps == [] and "split" in refs[0]["reason"]


def test_split_after_the_row_date_refused():
    t = trades(BASE, late_bbb(), (6, "BBB", "SPLIT", 4, "2026-11-05T21:00:00+00:00"))
    reps, refs = R.plan_repairs([row()], t, CLOSES, NOW)
    assert reps == [] and "split" in refs[0]["reason"]


def test_book_disagreeing_with_journal_beyond_late_trades_refused():
    t = trades(BASE, (2, "AAA", "BUY", 7, "2026-10-02T20:00:00+00:00"),
               (5, "AAA", "BUY", 3, ANCHOR_30))
    # journal says 20; book says 10 and the late trade explains only +3
    reps, refs = R.plan_repairs([row()], t, {}, NOW)
    assert reps == [] and "do not equal" in refs[0]["reason"]


def test_idempotent_second_run_plans_nothing():
    t = trades(BASE, late_bbb())
    reps, _ = R.plan_repairs([row()], t, CLOSES, NOW)
    assert R.stale_rows(reps, t) == []
    assert R.plan_repairs(reps, t, CLOSES, NOW) == ([], [])


def test_original_gross_book_set_once_across_two_repairs():
    t1 = trades(BASE, late_bbb())
    first, _ = R.plan_repairs([row()], t1, CLOSES, NOW)
    t2 = trades(BASE, late_bbb(), (6, "CCC", "BUY", 1, ANCHOR_30))
    second, _ = R.plan_repairs(first, t2, {("CCC", D30): 10.0}, NOW)
    assert first[0]["original_gross_book"] == 1000.0
    assert second[0]["original_gross_book"] == 1000.0     # not the 1100 intermediate
    assert second[0]["gross_book"] == 1110.0
    pre = row(original_gross_book=999.0)
    third, _ = R.plan_repairs([pre], t1, CLOSES, NOW)
    assert third[0]["original_gross_book"] == 999.0


def test_money_fields_recomputed_from_own_gross_reproduce_stored_values():
    r = row(gross=15272.55, cash=-12762.22)
    m = account_money_fields(r["gross_book"], r["cash_balance"], r["maintenance_rate"])
    for k in ("net_equity", "leverage", "cushion", "call_distance_pct"):
        assert m[k] == r[k]


def test_trades_none_is_could_not_check():
    assert R.stale_rows([row()], None) is None
    reps, refs = R.plan_repairs([row()], None, CLOSES, NOW)
    assert reps == [] and refs[0]["snapshot_date"] is None


def test_naive_now_rejected():
    with pytest.raises(ValueError):
        R.plan_repairs([row()], trades(BASE), {}, datetime(2026, 11, 1))


def test_accepts_dataframe_rows():
    t = trades(BASE, late_bbb())
    df = pd.DataFrame([row()])
    assert R.stale_rows(df, t) == ["2026-10-30"]
    reps, _ = R.plan_repairs(df, t, CLOSES, NOW)
    assert reps[0]["gross_book"] == 1100.0


# -- the 2026-11-01 regression fixture ---------------------------------------

def test_regression_fixture_1030_row_flagged_and_repaired():
    """Owner SQL 2026-11-01 (plan table). CSCO / MRVL / MU were bought 10-30
    and reached the journal after the 10-30 row was written."""
    spec = [("2026-10-23", 20951.66, -13286.14, 90),
            ("2026-10-27", 15317.51, -7761.29, 94),
            ("2026-10-28", 15242.86, -11637.31, 96),
            ("2026-10-29", 19805.70, -14247.57, 98),
            ("2026-10-30", 15272.55, -12762.22, 100)]
    rows = []
    for d, gross, cash, mid in spec:
        rows.append(row(d=d, gross=gross, cash=cash, max_id=mid,
                        cash_as_of=f"{d}T21:00:00+00:00",
                        book={"BASE": {"shares": 1000, "close": gross / 1000}}))
    t = trades((1, "BASE", "BUY", 1000, "2026-09-10T20:00:00+00:00"),
               (101, "CSCO", "BUY", 5, ANCHOR_30),
               (102, "MRVL", "BUY", 8, ANCHOR_30),
               (103, "MU", "BUY", 4, ANCHOR_30))
    assert R.stale_rows(rows, t) == ["2026-10-30"]
    closes = {("CSCO", D30): 104.6, ("MRVL", D30): 196.375, ("MU", D30): 442.25}
    reps, refs = R.plan_repairs(rows, t, closes, NOW)
    assert refs == [] and len(reps) == 1
    r = reps[0]
    added = round(5 * 104.6 + 8 * 196.375 + 4 * 442.25, 2)
    assert added == 3863.0
    assert r["gross_book"] == round(15272.55 + added, 2)
    assert r["net_equity"] == pytest.approx(r["gross_book"] - 12762.22)
    assert r["cash_balance"] == -12762.22
    assert r["original_gross_book"] == 15272.55
    assert r["journal_max_trade_id"] == 103
    assert R.plan_repairs(reps, t, closes, NOW) == ([], [])


# ── C3: plan_legacy_repairs (backfill of NULL-provenance rows) ───────────────

D = lambda s: date.fromisoformat(s)  # noqa: E731

# Old book AAA 10 @ 50 + DASH 5 @ 200 + OKTA 7 @ 100 = 2200 (cron: gross and the
# snapshot rows come from the same Price).
OLD_GROSS = 2200.0


def legacy(d="2026-10-30", gross=OLD_GROSS, cash=-1000.0, cash_as_of=None, **extra):
    r = row(d=d, gross=gross, cash=cash, max_id=None, book=None,
            cash_as_of=cash_as_of or f"{d}T21:02:00+00:00", **extra)
    r["book_positions"] = None
    return r


def snaps(d, **sh):
    return {(D(d), tk): n for tk, n in sh.items()}


BASE_TRADES = [(1, "AAA", "BUY", 10, "2026-09-10T20:00:00+00:00"),
               (2, "DASH", "BUY", 5, "2026-09-10T20:00:00+00:00"),
               (3, "OKTA", "BUY", 7, "2026-09-10T20:00:00+00:00")]
LATE_1030 = [(101, "CSCO", "BUY", 5, ANCHOR_30), (102, "MRVL", "BUY", 8, ANCHOR_30),
             (103, "MU", "BUY", 4, ANCHOR_30), (104, "DASH", "BUY", 2, ANCHOR_30),
             (105, "OKTA", "BUY", 1, ANCHOR_30)]
CLOSES_1030 = {("CSCO", D30): 104.6, ("MRVL", D30): 196.375, ("MU", D30): 442.25,
               ("DASH", D30): 200.0, ("OKTA", D30): 100.0, ("AAA", D30): 50.0}
SNAP_OLD = dict(AAA=10, DASH=5, OKTA=7)


def plan1(rows, t, sh, closes=None):
    return R.plan_legacy_repairs(rows, t, sh, CLOSES_1030 if closes is None else closes, NOW)


def test_legacy_1101_fixture_partial_quantity_delta_method():
    t = trades(*BASE_TRADES, *LATE_1030)
    reps, refs, revs = plan1([legacy()], t, snaps("2026-10-30", **SNAP_OLD))
    assert refs == [] and revs == [] and len(reps) == 1
    r = reps[0]
    assert r["category"] == "partial_quantity"
    assert {x["ticker"] for x in r["touched"]} == {"CSCO", "MRVL", "MU", "DASH", "OKTA"}
    delta = round(5 * 104.6 + 8 * 196.375 + 4 * 442.25 + 2 * 200 + 1 * 100, 2)
    assert delta == 4363.0
    assert r["new_gross_book"] == round(OLD_GROSS + delta, 2) == 6563.0
    assert r["gap_before"] == -4363.0 and r["residual"] == 0.0   # the gap IS the late trades
    f = r["fields"]
    assert f["gross_book"] == 6563.0
    m = account_money_fields(6563.0, -1000.0, 0.25)
    assert f["net_equity"] == pytest.approx(m["net_equity"])
    assert f["leverage"] == pytest.approx(m["leverage"])
    for k in ("cash_balance", "cash_as_of", "maintenance_rate"):
        assert k not in f
    assert f["original_gross_book"] == OLD_GROSS
    assert f["journal_max_trade_id"] == 105
    assert f["repaired_at"] == NOW.isoformat()
    # book_positions: exact R_c shares x that date's close, EVERY position, no None close
    bk = f["book_positions"]
    assert bk == r["book_positions"]
    assert set(bk) == {"AAA", "DASH", "OKTA", "CSCO", "MRVL", "MU"}
    assert bk["DASH"] == {"shares": 7.0, "close": 200.0}
    assert all(v["close"] is not None for v in bk.values())
    assert sum(v["shares"] * v["close"] for v in bk.values()) == pytest.approx(6563.0)


def test_legacy_missing_ticker_only_row():
    t = trades(*BASE_TRADES, LATE_1030[0], LATE_1030[1])
    reps, refs, revs = plan1([legacy()], t, snaps("2026-10-30", **SNAP_OLD))
    assert refs == [] and revs == [] and reps[0]["category"] == "missing_ticker"
    assert reps[0]["new_gross_book"] == round(OLD_GROSS + 5 * 104.6 + 8 * 196.375, 2)


def test_legacy_full_exit_is_missing_ticker_and_leaves_the_book():
    t = trades(*BASE_TRADES, (50, "OKTA", "SELL", 7, ANCHOR_30))
    reps, refs, _ = plan1([legacy()], t, snaps("2026-10-30", **SNAP_OLD))
    assert refs == [] and reps[0]["category"] == "missing_ticker"
    assert "OKTA" not in reps[0]["fields"]["book_positions"]
    assert reps[0]["new_gross_book"] == round(OLD_GROSS - 700.0, 2)


def test_legacy_trade_after_cash_as_of_is_not_counted():
    t = trades(*BASE_TRADES, (101, "MU", "BUY", 4, "2026-10-30T21:30:00+00:00"))
    assert plan1([legacy()], t, snaps("2026-10-30", **SNAP_OLD)) == ([], [], [])


def test_legacy_missing_close_refuses_and_never_carries_forward():
    t = trades(*BASE_TRADES, LATE_1030[0])
    carried = {("CSCO", D("2026-10-29")): 99.0, ("AAA", D30): 50.0}   # another day: ignored
    reps, refs, _ = plan1([legacy()], t, snaps("2026-10-30", **SNAP_OLD), carried)
    assert reps == [] and "CSCO" in refs[0]["reason"] and "no close" in refs[0]["reason"]


def test_legacy_split_after_the_row_refuses():
    t = trades(*BASE_TRADES, LATE_1030[0], (200, "CSCO", "SPLIT", 10, "2026-11-05T20:00:00+00:00"))
    reps, refs, _ = plan1([legacy()], t, snaps("2026-10-30", **SNAP_OLD))
    assert reps == [] and "split" in refs[0]["reason"]


def test_legacy_oversell_refuses():
    t = trades(*BASE_TRADES, (40, "CSCO", "BUY", 2, "2026-09-11T20:00:00+00:00"),
               (50, "CSCO", "SELL", 5, ANCHOR_30))
    reps, refs, _ = plan1([legacy()], t, snaps("2026-10-30", CSCO=2, **SNAP_OLD))
    assert reps == [] and "oversell" in refs[0]["reason"]


def test_legacy_unreadable_trades_is_one_none_date_refusal():
    reps, refs, revs = R.plan_legacy_repairs([legacy()], None, snaps("2026-10-30", AAA=10), {}, NOW)
    assert reps == [] and revs == []
    assert refs == [{"snapshot_date": None,
                     "reason": "the journal's trades could not be read, so no row can be checked"}]


def test_legacy_row_without_daily_snapshots_is_refused_and_flagged():
    t = trades(*BASE_TRADES, LATE_1030[0])
    reps, refs, _ = plan1([legacy()], t, snaps("2026-10-29", AAA=10))
    assert reps == [] and refs[0]["no_snapshot_rows"] is True


def test_legacy_idempotent_and_provenance_rows_are_skipped():
    t = trades(*BASE_TRADES, *LATE_1030)
    sh = snaps("2026-10-30", **SNAP_OLD)
    reps, _, _ = plan1([legacy()], t, sh)
    applied = {**legacy(), **reps[0]["fields"]}
    assert plan1([applied], t, sh) == ([], [], [])
    assert R.stale_rows([applied], t) == []


def test_legacy_clean_row_and_naive_now():
    t = trades(*BASE_TRADES)
    sh = snaps("2026-10-30", **SNAP_OLD)
    assert plan1([legacy()], t, sh) == ([], [], [])
    with pytest.raises(ValueError):
        R.plan_legacy_repairs([legacy()], t, sh, CLOSES_1030, datetime(2026, 11, 1))


def test_legacy_close_needs_lists_held_and_touched_pairs():
    t = trades(*BASE_TRADES, LATE_1030[0])
    need = R.legacy_close_needs([legacy()], t, snaps("2026-10-30", **SNAP_OLD))
    assert need == {(tk, D30) for tk in ("AAA", "DASH", "OKTA", "CSCO")}
    assert R.legacy_close_needs([legacy()], None, snaps("2026-10-30", **SNAP_OLD)) == set()


# -- review-fix: stale gross behind matching daily_snapshots -----------------------

def test_legacy_matching_snapshot_but_gross_gap_is_needs_review():
    """daily_snapshots was rewritten after the close (last-writer-wins), so the
    share counts match the journal while gross_book still lacks the trades."""
    t = trades(*BASE_TRADES, LATE_1030[0], LATE_1030[1])
    sh = snaps("2026-10-30", CSCO=5, MRVL=8, **SNAP_OLD)
    reps, refs, revs = plan1([legacy()], t, sh)
    assert reps == [] and refs == [] and len(revs) == 1
    exp_gap = round(OLD_GROSS - (2200 + 5 * 104.6 + 8 * 196.375), 2)
    assert revs[0]["gap"] == exp_gap and revs[0]["snapshot_date"] == "2026-10-30"
    assert "gross_book" in revs[0]["reason"]


def test_legacy_clean_row_reconciles_under_either_gross_regime():
    t = trades(*BASE_TRADES[:1], (9, "AAA", "BUY", 0.5, "2026-09-10T20:00:00+00:00"))           # 10.5 exact AAA
    sh = snaps("2026-10-30", AAA=10)
    exact = legacy(gross=10.5 * 50.0)
    trunc = legacy(gross=10 * 50.0)
    assert plan1([exact], t, sh) == ([], [], [])
    assert plan1([trunc], t, sh) == ([], [], [])


def test_legacy_matching_row_with_missing_close_is_reviewed_not_clean():
    t = trades(*BASE_TRADES)
    reps, refs, revs = plan1([legacy()], t, snaps("2026-10-30", **SNAP_OLD),
                             {("AAA", D30): 50.0})
    assert reps == [] and refs == [] and "can't be checked" in revs[0]["reason"]


def test_legacy_repair_whose_residual_does_not_reconcile_is_downgraded():
    t = trades(*BASE_TRADES, LATE_1030[0], LATE_1030[1])
    sh = snaps("2026-10-30", **SNAP_OLD)
    reps, refs, revs = plan1([legacy(gross=OLD_GROSS + 37.0)], t, sh)   # unrelated drift
    assert refs == [] and revs == [] and len(reps) == 1
    r = reps[0]
    assert r["category"] == "unreconciled" and r["residual"] == 37.0
    assert "book_positions" not in r["fields"] and r["book_positions"] is None


# -- review-fix: a removal the EOD book already held -------------------------------

def test_legacy_noon_cash_read_with_import_already_in_snapshot_is_refused():
    """cash_as_of is the noon read: the 16:00 import is after the cutoff but the
    EOD snapshot already held it. A removal would be wrong."""
    t = trades(*BASE_TRADES, LATE_1030[0])
    sh = snaps("2026-10-30", CSCO=5, **SNAP_OLD)
    r = legacy(cash_as_of="2026-10-30T16:00:00+00:00")                  # 12:00 EDT
    reps, refs, revs = plan1([r], t, sh)
    assert reps == [] and len(refs) == 1
    assert "post-close" in refs[0]["reason"]


def test_legacy_stale_cash_day_before_is_refused():
    t = trades(*BASE_TRADES, (101, "CSCO", "BUY", 5, "2026-10-29T20:00:00+00:00"))
    r = legacy(cash_as_of="2026-10-29T21:02:00+00:00")
    reps, refs, _ = plan1([r], t, snaps("2026-10-30", **SNAP_OLD))
    assert reps == [] and "post-close" in refs[0]["reason"]


def test_legacy_now_stamped_1710_log_already_in_snapshot_is_refused():
    """A hand-logged buy stamped 17:10 EDT (21:10Z) is after cash_as_of 21:02Z
    (so R_c lacks it) but the EOD book (21:30Z) held it: R_day == snapshot."""
    t = trades(*BASE_TRADES, (101, "CSCO", "BUY", 5, "2026-10-30T21:10:00+00:00"))
    sh = snaps("2026-10-30", CSCO=5, **SNAP_OLD)
    reps, refs, _ = plan1([legacy()], t, sh)
    assert reps == [] and "after this row's cash read" in refs[0]["reason"]


def test_legacy_genuine_late_buy_still_repairs():
    t = trades(*BASE_TRADES, LATE_1030[0])                              # R_c == R_day != snap
    reps, refs, _ = plan1([legacy()], t, snaps("2026-10-30", **SNAP_OLD))
    assert refs == [] and reps[0]["category"] == "missing_ticker"


def test_legacy_null_cash_uses_the_et_date_cutoff():
    t = trades(*BASE_TRADES, LATE_1030[0])
    r = legacy(cash=None)
    r["cash_as_of"] = None
    reps, refs, _ = plan1([r], t, snaps("2026-10-30", **SNAP_OLD))
    assert refs == [] and reps[0]["fields"]["net_equity"] is None
