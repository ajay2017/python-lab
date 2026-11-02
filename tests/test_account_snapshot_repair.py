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
