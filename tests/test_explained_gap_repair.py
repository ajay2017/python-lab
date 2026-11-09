"""Pure tests for stock_analyzer/explained_gap_repair.py (N1-LogTime): a legacy
account row whose gross_book is short by a hand-logged trade is repaired only
when the owner-named trades explain the gap to the cent."""
from datetime import date, datetime

import pandas as pd
import pytest

from stock_analyzer import explained_gap_repair as X
from stock_analyzer.account import account_money_fields
from stock_analyzer.market_time import ET

pytestmark = pytest.mark.fast

NOW = ET.localize(datetime(2026, 11, 9, 12, 0, 0))
D0 = date(2026, 9, 22)
DS = "2026-09-22"
CASH_AS_OF = "2026-09-22T21:02:00+00:00"           # 17:02 EDT, post-close
EARLY = "2026-09-10T20:00:00+00:00"
ON_FILL = "2026-09-22T19:30:00+00:00"              # 15:30 EDT, hand-logged later
CASH, RATE = -300.0, 0.25
OLD_GROSS = 500.0                                  # AAA 10 @ 50; the ON buy is missing
CLOSES = {("AAA", D0): 50.0, ("ON", D0): 73.6, ("MU", D0): 100.0}
SNAPS = {(D0, "AAA"): 10, (D0, "ON"): 10}


def trades(*rows):
    """rows: (id, ticker, action, shares, traded_at)"""
    return pd.DataFrame(
        [{"id": i, "ticker": t, "action": a, "shares": s, "price": 10.0, "traded_at": ts}
         for i, t, a, s, ts in rows])


BASE = (1, "AAA", "BUY", 10, EARLY)
ON_BUY = (388, "ON", "BUY", 10, ON_FILL)


def acct(d=DS, gross=OLD_GROSS, cash=CASH, rate=RATE, cash_as_of=CASH_AS_OF,
         max_id=None, **extra):
    m = (account_money_fields(gross, cash, rate)
         if cash is not None and rate is not None else {})
    r = {"snapshot_date": d, "gross_book": gross, "cash_balance": cash,
         "net_equity": m.get("net_equity"), "leverage": m.get("leverage"),
         "maintenance_rate": rate, "cash_as_of": cash_as_of,
         "journal_max_trade_id": max_id, "book_positions": None,
         "original_gross_book": None, "repaired_at": None}
    r.update(extra)
    return r


def plan(rows, t, explained, snaps=SNAPS, closes=CLOSES, now=NOW):
    return X.plan_explained_gap_repairs(rows, t, snaps, closes, now, explained)


def only_refusal(reps, refs):
    assert reps == [] and len(refs) == 1
    return refs[0]["reason"]


# -- happy paths --------------------------------------------------------------

def test_buy_explains_the_gap_to_the_cent():
    t = trades(BASE, ON_BUY)
    reps, refs = plan([acct()], t, {DS: [388]})
    assert refs == [] and len(reps) == 1
    r = reps[0]
    assert r["category"] == "explained_gap" and r["touched"] == []
    assert r["snapshot_date"] == DS
    assert r["old_gross_book"] == 500.0 and r["new_gross_book"] == 1236.0
    assert r["gap_before"] == -736.0 and r["residual"] == 0.0
    assert r["explained_by"] == [{"id": 388, "ticker": "ON", "action": "BUY",
                                  "shares": 10.0, "close": 73.6, "value": 736.0}]
    m = account_money_fields(1236.0, CASH, RATE)
    f = r["fields"]
    assert f["gross_book"] == 1236.0
    for k in ("net_equity", "leverage", "cushion", "call_distance_pct"):
        assert f[k] == pytest.approx(m[k])
    assert r["new_net_equity"] == pytest.approx(m["net_equity"])
    assert r["new_leverage"] == pytest.approx(m["leverage"])
    assert r["old_net_equity"] == pytest.approx(200.0)
    assert f["book_positions"] == {"AAA": {"shares": 10.0, "close": 50.0},
                                   "ON": {"shares": 10.0, "close": 73.6}}
    assert r["book_positions"] == f["book_positions"]
    assert f["journal_max_trade_id"] == 388
    assert f["original_gross_book"] == 500.0
    assert f["repaired_at"] == NOW.isoformat()
    for k in ("cash_balance", "cash_as_of", "maintenance_rate"):
        assert k not in f


def test_journal_max_trade_id_is_the_highest_id_in_the_journal():
    t = trades(BASE, ON_BUY, (500, "AAA", "BUY", 1, "2026-09-25T20:00:00+00:00"))
    reps, refs = plan([acct()], t, {DS: [388]})
    assert refs == [] and reps[0]["fields"]["journal_max_trade_id"] == 500


def test_existing_original_gross_book_is_kept():
    t = trades(BASE, ON_BUY)
    reps, _ = plan([acct(original_gross_book=480.0)], t, {DS: [388]})
    assert reps[0]["fields"]["original_gross_book"] == 480.0


def test_sell_variant_gross_still_holds_the_sold_shares():
    # AAA 10 + ON 10 bought early; ON 4 sold at 15:30 and logged late. The stored
    # gross still holds all 10 ON, so the gap is positive and delta negative.
    t = trades(BASE, (5, "ON", "BUY", 10, EARLY), (6, "ON", "SELL", 4, ON_FILL))
    snaps = {(D0, "AAA"): 10, (D0, "ON"): 6}
    gross = 10 * 50.0 + 10 * 73.6
    reps, refs = plan([acct(gross=gross)], t, {DS: [6]}, snaps=snaps)
    assert refs == [] and len(reps) == 1
    r = reps[0]
    assert r["gap_before"] == pytest.approx(294.4)
    assert r["explained_by"][0]["action"] == "SELL"
    assert r["explained_by"][0]["value"] == pytest.approx(-294.4)
    assert r["new_gross_book"] == pytest.approx(941.6)
    assert r["residual"] == 0.0
    assert r["book_positions"]["ON"] == {"shares": 6.0, "close": 73.6}


def test_two_ids_on_one_date():
    t = trades(BASE, ON_BUY, (398, "MU", "BUY", 1, "2026-09-22T19:45:00+00:00"))
    snaps = {**SNAPS, (D0, "MU"): 1}
    reps, refs = plan([acct()], t, {DS: [388, 398]}, snaps=snaps)
    assert refs == [] and len(reps) == 1
    r = reps[0]
    assert r["new_gross_book"] == 1336.0 and r["gap_before"] == -836.0
    assert [e["id"] for e in r["explained_by"]] == [388, 398]


def test_cash_none_gives_none_money_fields():
    t = trades(BASE, ON_BUY)
    reps, refs = plan([acct(cash=None, rate=None)], t, {DS: [388]})
    assert refs == [] and len(reps) == 1
    f = reps[0]["fields"]
    assert f["gross_book"] == 1236.0
    for k in ("net_equity", "leverage", "cushion", "call_distance_pct"):
        assert f[k] is None


def test_several_dates_sorted_and_independent():
    t = trades(BASE, ON_BUY)
    reps, refs = plan([acct()], t, {"2026-09-23": [1], DS: [388]})
    assert [r["snapshot_date"] for r in reps] == [DS]
    assert [r["snapshot_date"] for r in refs] == ["2026-09-23"]      # no row: refused alone


# -- refusals a-e -------------------------------------------------------------

def test_refuse_row_missing():
    reason = only_refusal(*plan([acct()], trades(BASE, ON_BUY), {"2026-09-24": [388]}))
    assert "no account_daily_snapshots row" in reason


def test_refuse_bad_date_key():
    reps, refs = plan([acct()], trades(BASE, ON_BUY), {"22-09-2026": [388]})
    assert reps == [] and refs[0]["snapshot_date"] == "22-09-2026"
    assert "YYYY-MM-DD" in refs[0]["reason"]


def test_refuse_row_with_provenance():
    reason = only_refusal(*plan([acct(max_id=400)], trades(BASE, ON_BUY), {DS: [388]}))
    assert "already has provenance" in reason


def test_refuse_clean_row():
    reason = only_refusal(*plan([acct(gross=1236.0)], trades(BASE, ON_BUY), {DS: [388]}))
    assert "clean" in reason


def test_refuse_touched_row_is_a_legacy_repair_not_a_gap():
    snaps = {(D0, "AAA"): 10}                        # ON missing from daily_snapshots
    reason = only_refusal(*plan([acct()], trades(BASE, ON_BUY), {DS: [388]}, snaps=snaps))
    assert "missing_ticker" in reason and "repair" in reason


def test_refuse_legacy_planner_refusal():
    reason = only_refusal(*plan([acct()], trades(BASE, ON_BUY), {DS: [388]}, snaps={}))
    assert "legacy planner refuses" in reason and "no daily_snapshots rows" in reason


def test_refuse_review_with_no_close_is_unmeasurable():
    closes = {("AAA", D0): 50.0}                     # ON close missing
    reason = only_refusal(*plan([acct()], trades(BASE, ON_BUY), {DS: [388]}, closes=closes))
    assert "can't be measured" in reason


def test_refuse_cash_as_of_not_post_close():
    # Fill at 14:00 EDT, cash read at 15:00 EDT: same day but before the close.
    t = trades(BASE, (388, "ON", "BUY", 10, "2026-09-22T18:00:00+00:00"))
    reason = only_refusal(*plan([acct(cash_as_of="2026-09-22T19:00:00+00:00")], t, {DS: [388]}))
    assert "post-close" in reason


def test_refuse_cash_as_of_on_the_next_et_day():
    reason = only_refusal(*plan([acct(cash_as_of="2026-09-23T14:00:00+00:00")],
                                trades(BASE, ON_BUY), {DS: [388]}))
    assert "post-close" in reason


def test_refuse_cash_present_without_rate():
    reason = only_refusal(*plan([acct(rate=None)], trades(BASE, ON_BUY), {DS: [388]}))
    assert "maintenance rate" in reason


def test_refuse_duplicate_id():
    reason = only_refusal(*plan([acct()], trades(BASE, ON_BUY), {DS: [388, 388]}))
    assert "388" in reason and "more than once" in reason


def test_refuse_unknown_id():
    reason = only_refusal(*plan([acct()], trades(BASE, ON_BUY), {DS: [388, 999]}))
    assert "999" in reason and "not in the journal" in reason


def test_refuse_non_integer_and_empty_ids():
    t = trades(BASE, ON_BUY)
    assert "not an integer" in only_refusal(*plan([acct()], t, {DS: ["abc"]}))
    assert "not an integer" in only_refusal(*plan([acct()], t, {DS: [388.5]}))
    assert "no trade id" in only_refusal(*plan([acct()], t, {DS: []}))


def test_refuse_id_dated_another_day():
    t = trades(BASE, ON_BUY, (389, "ON", "BUY", 1, "2026-09-23T15:00:00+00:00"))
    reason = only_refusal(*plan([acct()], t, {DS: [388, 389]}))
    assert "389" in reason and "2026-09-23" in reason


def test_refuse_split_id():
    t = trades(BASE, ON_BUY, (390, "AAA", "SPLIT", 10, "2026-09-22T15:00:00+00:00"))
    reason = only_refusal(*plan([acct()], t, {DS: [390]}))
    assert "390" in reason and "SPLIT" in reason


def test_refuse_id_after_cash_cutoff():
    t = trades(BASE, ON_BUY, (390, "AAA", "BUY", 1, "2026-09-22T21:30:00+00:00"))  # 17:30 EDT
    reason = only_refusal(*plan([acct()], t, {DS: [388, 390]}))
    assert "390" in reason and "cash_as_of" in reason


def test_refuse_named_trade_without_close():
    # XYZ is bought and sold the same afternoon (flat, so the gap needs no XYZ
    # close), but naming its buy needs one.
    t = trades(BASE, ON_BUY, (391, "XYZ", "BUY", 5, "2026-09-22T18:00:00+00:00"),
               (392, "XYZ", "SELL", 5, "2026-09-22T19:00:00+00:00"))
    reason = only_refusal(*plan([acct()], t, {DS: [388, 391]}))
    assert "391" in reason and "XYZ" in reason and "no close" in reason


def test_refuse_partial_explanation_says_how_much_it_explains():
    t = trades(BASE, ON_BUY, (398, "MU", "BUY", 1, "2026-09-22T19:45:00+00:00"))
    snaps = {**SNAPS, (D0, "MU"): 1}
    reason = only_refusal(*plan([acct()], t, {DS: [388]}, snaps=snaps))
    assert "explain +736.00 of the -836.00 gap" in reason


def test_refuse_wrong_trade_explains_nothing():
    t = trades(BASE, ON_BUY, (398, "MU", "BUY", 1, "2026-09-22T19:45:00+00:00"))
    snaps = {**SNAPS, (D0, "MU"): 1}
    # Name only the MU buy: +100 of the -836 gap.
    reason = only_refusal(*plan([acct()], t, {DS: [398]}, snaps=snaps))
    assert "explain +100.00 of the -836.00 gap" in reason


def test_a_refused_date_does_not_block_another():
    t = trades(BASE, ON_BUY)
    rows = [acct(), acct(d="2026-09-29", max_id=9)]
    reps, refs = plan(rows, t, {DS: [388], "2026-09-29": [388]})
    assert [r["snapshot_date"] for r in reps] == [DS]
    assert [r["snapshot_date"] for r in refs] == ["2026-09-29"]


# -- input contracts ----------------------------------------------------------

def test_naive_now_raises():
    with pytest.raises(ValueError):
        plan([acct()], trades(BASE, ON_BUY), {DS: [388]}, now=datetime(2026, 11, 9))


def test_unreadable_trades_is_one_none_date_refusal():
    for bad in (None, pd.DataFrame([{"x": 1}])):
        reps, refs = plan([acct()], bad, {DS: [388]})
        assert reps == [] and len(refs) == 1 and refs[0]["snapshot_date"] is None


def test_non_dataframe_trades_is_caller_misuse():
    with pytest.raises(TypeError):
        plan([acct()], [{"id": 1}], {DS: [388]})


def test_empty_explained_plans_nothing():
    assert plan([acct()], trades(BASE, ON_BUY), {}) == ([], [])
    assert plan([acct()], trades(BASE, ON_BUY), None) == ([], [])


def test_acct_rows_may_be_a_dataframe():
    reps, refs = plan(pd.DataFrame([acct()]), trades(BASE, ON_BUY), {DS: [388]})
    assert refs == [] and len(reps) == 1


# -- time context -------------------------------------------------------------

def test_1559_et_fill_counts_and_1630_et_fill_does_not_against_a_1620_cash_read():
    cash_read = "2026-09-22T20:20:00+00:00"          # 16:20 EDT
    t = trades(BASE, (388, "ON", "BUY", 10, "2026-09-22T19:59:00+00:00"),   # 15:59 EDT
               (390, "AAA", "BUY", 1, "2026-09-22T20:30:00+00:00"))          # 16:30 EDT
    row = acct(cash_as_of=cash_read)
    reps, refs = plan([row], t, {DS: [388]})
    assert refs == [] and reps[0]["new_gross_book"] == 1236.0
    reason = only_refusal(*plan([row], t, {DS: [388, 390]}))
    assert "390" in reason and "cash_as_of" in reason


def test_est_day_after_the_dst_change_uses_the_same_cutoff_rule():
    d = date(2026, 11, 2)                              # Monday, EST (UTC-5)
    closes = {("AAA", d): 50.0, ("ON", d): 73.6}
    snaps = {(d, "AAA"): 10, (d, "ON"): 10}
    row = acct(d="2026-11-02", cash_as_of="2026-11-02T21:20:00+00:00")        # 16:20 EST
    t = trades(BASE, (388, "ON", "BUY", 10, "2026-11-02T20:59:00+00:00"),     # 15:59 EST
               (390, "AAA", "BUY", 1, "2026-11-02T21:30:00+00:00"))            # 16:30 EST
    reps, refs = plan([row], t, {"2026-11-02": [388]}, snaps=snaps, closes=closes)
    assert refs == [] and reps[0]["new_gross_book"] == 1236.0
    assert "after this row's cash_as_of" in only_refusal(
        *plan([row], t, {"2026-11-02": [388, 390]}, snaps=snaps, closes=closes))


def test_trade_logged_a_day_late_is_dated_by_its_fill_time_not_the_log_time():
    # traded_at was corrected to the fill time; an ET date of 09-22 matches the
    # row's date whenever the journal row was entered.
    t = trades(BASE, (388, "ON", "BUY", 10, "2026-09-22T19:30:00+00:00"))
    reps, refs = plan([acct()], t, {DS: [388]})
    assert refs == [] and len(reps) == 1


def test_trade_dated_late_evening_utc_is_the_next_et_day_only_after_midnight_et():
    # 2026-09-23T03:00Z is 23:00 EDT on 09-22 (still the 22nd in ET); the cash
    # read at 17:02 EDT predates it, so it is refused as after the cutoff, not
    # as another day.
    t = trades(BASE, ON_BUY, (390, "AAA", "BUY", 1, "2026-09-23T03:00:00+00:00"))
    reason = only_refusal(*plan([acct()], t, {DS: [388, 390]}))
    assert "cash_as_of" in reason


# -- cent tolerance, both sides (reviewer 2026-11-09, non-blocking 3) ----------

@pytest.mark.parametrize("gross, ok", [
    (499.99, True),    # residual -0.01: inside the cent
    (500.01, True),    # residual +0.01
    (499.98, False),   # residual -0.02: one cent too far
    (500.02, False),   # residual +0.02
])
def test_cent_tolerance_boundary(gross, ok):
    t = trades(BASE, ON_BUY)
    reps, refs = plan([acct(gross=gross)], t, {DS: [388]})
    if ok:
        assert refs == [] and len(reps) == 1
        assert abs(reps[0]["residual"]) <= 0.01 + 1e-9
    else:
        reason = only_refusal(reps, refs)
        assert "the named trades explain +736.00" in reason


def test_int_share_branch_reconciles_on_a_pre_1012_row():
    """A fractional untouched holding on an int-convention row (gross_book was
    int-share based before 2026-10-12): only `_gross_gap`'s int-share branch
    reconciles, which is the real September shape."""
    t = trades((1, "AAA", "BUY", 10.5, EARLY), ON_BUY)
    reps, refs = plan([acct(gross=500.0)], t, {DS: [388]})   # 10 x 50, int-based
    assert refs == [] and len(reps) == 1
    r = reps[0]
    assert r["gap_before"] == -736.0 and r["new_gross_book"] == 1236.0
    assert r["residual"] == 0.0
    assert r["book_positions"]["AAA"] == {"shares": 10.5, "close": 50.0}
