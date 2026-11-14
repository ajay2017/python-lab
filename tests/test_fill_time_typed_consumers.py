"""RecTime C3 (`docs/plans/fill-time-typed.md`): the three consumers of
`sync_timing` -- 💳 Capital vs Margin's late-trade anchor, 💰 Account's "traded
since this sync" caption, and the F-306 budget basis -- plus the vectorized
`classify_vs_sync` pre-pass that makes them cheap enough to call on every render.

Motivating case (live 2026-11-13): #494 BUY 5 MRVL filled 10:21 ET, saved 14:30:50
ET with the fill time left blank, already in the 11:19:14 ET broker sync. The raw
`traded_at > sync` comparison took its $750 off a second time.

Owner decisions: Q1 (Capital vs Margin adjusts ONLY for a certain-after row; an
unknown row is named, never adjusted); Q2 (budget: unknown BUYs subtracted,
unknown SELLs NOT added, a "may be up to $X low" caption). Pure frame math; no
Streamlit, no DB.
"""
import pathlib
import re
from datetime import date

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import budget_view as bv
from stock_analyzer import capital_vs_margin as cvm
from stock_analyzer import daily_budget as dbud
from stock_analyzer import sync_timing as stm
from stock_analyzer.market_time import et_anchor_iso
from stock_analyzer.trade_time import normalize_traded_at

pytestmark = pytest.mark.fast

_ROOT = pathlib.Path(__file__).resolve().parent.parent

SYNC = "2026-11-13T11:19:14-05:00"      # the 2026-11-13 broker sync, ET
SYNC_UTC = "2026-11-13T16:19:14+00:00"  # the same instant


def _frame(rows):
    """rows: (ticker, action, shares, price, traded_at, fill_time_typed) ->
    a frame as `db.load_trades_or_none` would normalise it."""
    return normalize_traded_at(pd.DataFrame([
        {"id": i + 1, "ticker": tk, "action": act, "shares": sh, "price": px,
         "traded_at": at, "notes": None, "broker_txn_id": None,
         "fill_time_typed": typed}
        for i, (tk, act, sh, px, at, typed) in enumerate(rows)]))


SELL_TYPED_0956 = ("MRVL", "SELL", 3.0, 160.0, "2026-11-13T09:56:00-05:00", True)
BUY_BLANK_1430 = ("MRVL", "BUY", 5.0, 150.0, "2026-11-13T14:30:50.554958-05:00", False)
BUY_TYPED_1430 = ("MRVL", "BUY", 5.0, 150.0, "2026-11-13T14:30:00-05:00", True)
BUY_PREFILL_1600 = ("CSCO", "BUY", 10.0, 60.0, et_anchor_iso("2026-11-13"), False)


# ─── 0. classify_vs_sync: vectorized pre-pass == the all-rows path ──────────

def _mixed_frame():
    """Every kind, both sides of the sync, several dates, unreadable rows."""
    rows = []
    stamps = [
        "2026-11-10T16:00:00-05:00", "2026-11-12T10:00:00-05:00",
        "2026-11-13T09:30:00-05:00", "2026-11-13T11:19:14-05:00",   # a tie
        "2026-11-13T11:19:14.000001-05:00", "2026-11-13T14:30:50.5-05:00",
        "2026-11-13T16:00:00-05:00", "2026-11-13T23:30:00-05:00",
        "2026-11-14T10:00:00-05:00", "2026-11-16T16:00:00-05:00",
    ]
    kinds = ["fill", "recorded", "placeholder", None, float("nan"), "garbage", "FILL "]
    i = 0
    for st in stamps:
        for k in kinds:
            i += 1
            rows.append({"id": i, "ticker": f"T{i % 7}",
                         "action": "BUY" if i % 2 else "SELL",
                         "shares": float(1 + i % 5), "price": 10.0 + i,
                         "traded_at": st, "traded_at_time_kind": k})
    rows.append({"id": 900, "ticker": "BAD", "action": "BUY", "shares": 1.0,
                 "price": 1.0, "traded_at": "garbage", "traded_at_time_kind": "fill"})
    rows.append({"id": 901, "ticker": "BAD", "action": "BUY", "shares": 1.0,
                 "price": 1.0, "traded_at": None, "traded_at_time_kind": "placeholder"})
    return pd.DataFrame(rows, index=range(100, 100 + len(rows)))


def test_fast_path_equals_the_all_rows_path_on_a_mixed_frame():
    df = _mixed_frame()
    fast = stm._classify(df, SYNC, fast=True)
    slow = stm._classify(df, SYNC, fast=False)
    assert fast == slow
    assert stm.classify_vs_sync(df, SYNC) == slow
    # every bucket is exercised, so equality is not vacuous
    for key in ("after", "before", "unknown", "unreadable"):
        assert fast[key], key


def test_fast_path_equals_the_all_rows_path_without_a_kind_column():
    df = _mixed_frame().drop(columns=["traded_at_time_kind"])
    assert stm._classify(df, SYNC, fast=True) == stm._classify(df, SYNC, fast=False)


def test_row_codes_match_after_sync_row_by_row():
    df = _mixed_frame()
    codes = stm.row_codes(df, SYNC)
    assert codes is not None and len(codes) == len(df)
    for pos, (_, r) in enumerate(df.iterrows()):
        v = stm.after_sync(r["traded_at"], r["traded_at_time_kind"], SYNC)
        if stm._instant(r["traded_at"]) is None:
            want = stm.UNREADABLE
        else:
            want = stm.AFTER if v is True else stm.BEFORE if v is False else stm.UNKNOWN
        assert codes[pos] == want, (pos, r["traded_at"], r["traded_at_time_kind"])


def test_row_codes_unreadable_inputs_are_none_not_all_before():
    good = _mixed_frame()
    assert stm.row_codes(None, SYNC) is None
    assert stm.row_codes("x", SYNC) is None
    assert stm.row_codes(good, "garbage") is None
    assert stm.row_codes(pd.DataFrame([{"ticker": "A"}]), SYNC) is None
    empty = stm.row_codes(pd.DataFrame(columns=["traded_at"]), SYNC)
    assert isinstance(empty, np.ndarray) and len(empty) == 0


def test_placeholder_before_the_sync_day_is_sent_straight_to_before():
    """A placeholder on an EARLIER ET date is before whatever its 16:00 clock
    says; the same date stays unknown (the fast path must not decide it)."""
    df = pd.DataFrame([
        {"ticker": "A", "action": "BUY", "shares": 1.0, "price": 1.0,
         "traded_at": "2026-11-12T16:00:00-05:00", "traded_at_time_kind": "placeholder"},
        {"ticker": "B", "action": "BUY", "shares": 1.0, "price": 1.0,
         "traded_at": "2026-11-13T16:00:00-05:00", "traded_at_time_kind": "placeholder"},
    ])
    out = stm.classify_vs_sync(df, SYNC)
    assert out["before"] == [0] and out["unknown"] == [1] and out["after"] == []


# ─── 1. Capital vs Margin late-trade anchor ─────────────────────────────────

ANCHOR = {"cash": -1000.0, "date": date(2026, 11, 13), "src": "live"}
CASH_REC = {"cash_balance": -1000.0, "updated_at": SYNC}


def _adjust(rows, anchor=ANCHOR, rec=CASH_REC):
    return cvm.adjust_anchor_for_late_trades(anchor, rec, _frame(rows))


def test_494_replay_blank_time_buy_is_not_adjusted_and_is_reported_unknown():
    r = _adjust([SELL_TYPED_0956, BUY_BLANK_1430])
    assert r["cash"] == -1000.0                       # NOT taken off a second time
    assert "late_trade_adjustment" not in r and "late_trade_count" not in r
    assert r["late_trade_unknown_count"] == 1
    assert r["late_trade_unknown_tickers"] == ["MRVL"]
    assert r["late_trade_unknown_delta"] == pytest.approx(-750.0)
    assert (r["date"], r["src"]) == (ANCHOR["date"], "live")


def test_494_replay_the_same_buy_typed_is_adjusted():
    r = _adjust([SELL_TYPED_0956, BUY_TYPED_1430])
    assert r["cash"] == pytest.approx(-1750.0)
    assert r["late_trade_adjustment"] == pytest.approx(-750.0)
    assert r["late_trade_count"] == 1
    assert "late_trade_unknown_count" not in r


def test_same_day_16_00_prefill_row_is_not_adjusted_and_is_unknown():
    r = _adjust([BUY_PREFILL_1600])
    assert r["cash"] == -1000.0 and "late_trade_adjustment" not in r
    assert r["late_trade_unknown_count"] == 1
    assert r["late_trade_unknown_tickers"] == ["CSCO"]
    assert r["late_trade_unknown_delta"] == pytest.approx(-600.0)


def test_certain_and_unknown_in_one_frame_adjust_only_the_certain():
    r = _adjust([BUY_TYPED_1430,
                 ("AAA", "SELL", 2.0, 50.0, "2026-11-13T15:10:11.123456-05:00", False)])
    assert r["cash"] == pytest.approx(-1000.0 - 750.0)
    assert r["late_trade_adjustment"] == pytest.approx(-750.0)
    assert r["late_trade_count"] == 1
    assert r["late_trade_unknown_count"] == 1
    assert r["late_trade_unknown_tickers"] == ["AAA"]
    assert r["late_trade_unknown_delta"] == pytest.approx(100.0)     # SELL +


def test_unknown_tickers_are_sorted_and_counted_per_row():
    r = _adjust([("ZZZ", "BUY", 1.0, 10.0, "2026-11-13T14:00:00.5-05:00", False),
                 ("AAA", "BUY", 1.0, 10.0, "2026-11-13T14:01:00.5-05:00", False),
                 ("AAA", "SELL", 1.0, 30.0, "2026-11-13T14:02:00.5-05:00", False)])
    assert r["late_trade_unknown_count"] == 3
    assert r["late_trade_unknown_tickers"] == ["AAA", "ZZZ"]
    assert r["late_trade_unknown_delta"] == pytest.approx(10.0)


def test_row_certainly_before_the_sync_changes_nothing():
    rows = [("A", "BUY", 1.0, 10.0, "2026-11-13T10:30:00.5-05:00", False),   # recorded <= sync
            ("B", "BUY", 1.0, 10.0, "2026-11-13T09:00:00-05:00", True)]      # typed before
    assert _adjust(rows) == ANCHOR


def test_tie_with_the_sync_is_already_reflected():
    assert _adjust([("A", "BUY", 1.0, 10.0, SYNC, True)]) == ANCHOR


def test_prior_day_rows_are_outside_the_anchor_day():
    """A trade logged a day late: dated 11-12, a blank time (16:00 placeholder),
    saved after the sync. Another ET date than the anchor's: untouched."""
    rows = [("A", "BUY", 1.0, 10.0, et_anchor_iso("2026-11-12"), False)]
    assert _adjust(rows) == ANCHOR


def test_dated_a_day_late_in_the_future_is_outside_the_anchor_day_too():
    rows = [("A", "SELL", 1.0, 10.0, et_anchor_iso("2026-11-14"), False)]
    assert _adjust(rows) == ANCHOR


def test_weekend_visit_a_friday_blank_time_row_after_a_friday_sync_is_unknown():
    """Visited Saturday, but the last sync was Friday 11:19: the anchor is still
    Friday's, and the Friday 14:30 blank-time row is still unknown."""
    r = _adjust([BUY_BLANK_1430])
    assert r["late_trade_unknown_count"] == 1


def test_a_later_sync_clears_the_unknown_row():
    """After the next sync (Saturday 02:00 ET) the live anchor's date is the
    14th; the Friday row is another ET date, so nothing is unknown any more."""
    anchor = {"cash": -1000.0, "date": date(2026, 11, 14), "src": "live"}
    rec = {"cash_balance": -1000.0, "updated_at": "2026-11-14T02:00:00-05:00"}
    assert _adjust([BUY_BLANK_1430], anchor, rec) == anchor


def test_same_day_later_sync_after_the_record_time_makes_it_before():
    anchor = {"cash": -1000.0, "date": date(2026, 11, 13), "src": "live"}
    rec = {"cash_balance": -1000.0, "updated_at": "2026-11-13T15:30:00-05:00"}
    assert _adjust([BUY_BLANK_1430], anchor, rec) == anchor


def test_dst_fall_back_day_2026_11_01_late_evening_row_stays_on_the_anchor_day():
    """23:30 EST on 11-01 is 04:30Z on 11-02. The ET date is still 11-01, so a
    blank-time row recorded then is that day's unknown row."""
    anchor = {"cash": -1000.0, "date": date(2026, 11, 1), "src": "live"}
    rec = {"cash_balance": -1000.0, "updated_at": "2026-11-01T11:19:14-05:00"}
    r = _adjust([("A", "BUY", 1.0, 10.0, "2026-11-01T23:30:00.25-05:00", False)],
                anchor, rec)
    assert r["late_trade_unknown_count"] == 1 and r["cash"] == -1000.0


def test_dst_spring_forward_day_2026_03_08():
    anchor = {"cash": -1000.0, "date": date(2026, 3, 8), "src": "live"}
    rec = {"cash_balance": -1000.0, "updated_at": "2026-03-08T11:19:14-04:00"}
    r = _adjust([("A", "BUY", 1.0, 10.0, "2026-03-08T14:30:00-04:00", True),
                 ("B", "BUY", 2.0, 10.0, "2026-03-08T14:31:00.5-04:00", False)],
                anchor, rec)
    assert r["late_trade_adjustment"] == pytest.approx(-10.0)
    assert r["late_trade_unknown_tickers"] == ["B"]


def test_after_hours_typed_fill_is_adjusted_blank_time_is_not():
    r = _adjust([("A", "BUY", 1.0, 10.0, "2026-11-13T17:45:00-05:00", True),
                 ("B", "BUY", 1.0, 20.0, "2026-11-13T17:46:00.7-05:00", False)])
    assert r["late_trade_adjustment"] == pytest.approx(-10.0)
    assert r["late_trade_unknown_delta"] == pytest.approx(-20.0)


@pytest.mark.parametrize("action,sign", [("BUY", -1), ("SELL", 1)])
def test_buy_and_sell_unknown_delta_signs(action, sign):
    r = _adjust([("A", action, 4.0, 10.0, "2026-11-13T14:00:00.5-05:00", False)])
    assert r["late_trade_unknown_delta"] == pytest.approx(sign * 40.0)
    assert r["cash"] == -1000.0


def test_a_split_row_after_the_sync_moves_no_cash_and_is_counted_certain_when_typed():
    r = _adjust([("A", "SPLIT", 40.0, 1.0, "2026-11-13T15:00:00-05:00", True)])
    assert r["late_trade_adjustment"] == 0.0 and r["late_trade_count"] == 1
    assert r["cash"] == -1000.0


def test_unreadable_row_time_is_in_neither_set():
    df = _frame([BUY_TYPED_1430])
    df.loc[len(df)] = df.iloc[0]
    df.loc[df.index[-1], "traded_at"] = "garbage"
    r = cvm.adjust_anchor_for_late_trades(ANCHOR, CASH_REC, df)
    # the unparseable row has no ET date, so the day filter drops it
    assert r["late_trade_count"] == 1 and "late_trade_unknown_count" not in r


def test_frame_without_a_kind_column_keeps_the_old_reading():
    """No `traded_at_time_kind` (an older-shaped frame): `time_known is False` is
    an invented stamp (unknown on the anchor day), everything else a real time."""
    df = pd.DataFrame([
        {"traded_at": "2026-11-13T16:00:00-05:00", "action": "BUY", "shares": 15,
         "price": 74.0, "ticker": "X", "traded_at_time_known": False},
        {"traded_at": "2026-11-13T17:07:00-05:00", "action": "BUY", "shares": 1,
         "price": 50.0, "ticker": "Y", "traded_at_time_known": True},
    ])
    r = cvm.adjust_anchor_for_late_trades(ANCHOR, CASH_REC, df)
    assert r["late_trade_adjustment"] == pytest.approx(-50.0)
    assert r["late_trade_unknown_tickers"] == ["X"]


def test_adjusted_anchor_still_round_trips_to_zero_drift_for_a_certain_row():
    """The reconstruction identity the adjustment exists for is unchanged for a
    certain-after row: the prior day's cash lands on the original figure."""
    trades = _frame([("ON", "BUY", 15.0, 74.0, "2026-11-13T17:07:00-05:00", True)])
    anchor = {"cash": -12424.0, "date": date(2026, 11, 13), "src": "live"}
    adj = cvm.adjust_anchor_for_late_trades(anchor, CASH_REC, trades)
    series = cvm.reconstruct_daily_cash(adj, date(2026, 11, 12), trades, [], [])
    by_date = {r["date"]: r["cash_balance"] for r in series}
    assert by_date[date(2026, 11, 12)] == pytest.approx(-12424.0, abs=1e-9)


# ─── 1b. the ℹ️ note ────────────────────────────────────────────────────────

def _money(v):
    return f"\\${v:,.0f}"


def test_note_for_the_494_replay_reads_the_reviewed_wording():
    anchor = _adjust([SELL_TYPED_0956, BUY_BLANK_1430])
    note = cvm.late_trade_unknown_note(anchor, CASH_REC, date(2026, 11, 13), _money)
    assert note == (
        "ℹ️ 1 trade today (MRVL, about \\$750) has no fill time the app can place "
        "against the 11:19 AM ET broker sync (left blank, or an import), so the app "
        "can't tell whether that sync already counted it. The anchor isn't adjusted "
        "for it; if the cash check below fails by about \\$750, this is why. It "
        "clears at the next broker sync after the trade (for an import, the next "
        "day's sync). Type Robinhood's fill time when you log later than the fill.")
    # the old overclaims are gone: the logging time of an unknown row isn't known
    assert "was logged" not in note and "were logged" not in note
    assert "clears at the next sync." not in note


def test_note_is_none_when_nothing_is_unknown_or_the_anchor_is_unreadable():
    adj = _adjust([SELL_TYPED_0956, BUY_TYPED_1430])           # certain only
    assert cvm.late_trade_unknown_note(adj, CASH_REC, date(2026, 11, 13), _money) is None
    assert cvm.late_trade_unknown_note(ANCHOR, CASH_REC, date(2026, 11, 13), _money) is None
    assert cvm.late_trade_unknown_note(None, CASH_REC, date(2026, 11, 13), _money) is None


def test_note_does_not_say_today_when_the_anchor_is_an_older_day():
    anchor = _adjust([BUY_BLANK_1430])
    note = cvm.late_trade_unknown_note(anchor, CASH_REC, date(2026, 11, 16), _money)
    assert "trade on Fri Nov 13 (" in note and " today " not in note


def test_note_names_several_tickers_and_uses_the_formatter_for_the_amount():
    anchor = _adjust([("ZZZ", "BUY", 1.0, 10.0, "2026-11-13T14:00:00.5-05:00", False),
                      ("AAA", "BUY", 2.0, 10.0, "2026-11-13T14:01:00.5-05:00", False)])
    note = cvm.late_trade_unknown_note(anchor, CASH_REC, date(2026, 11, 13),
                                       lambda v: "••••••")
    assert "2 trades today (AAA, ZZZ, about ••••••) have no fill time" in note
    assert "counted them. The anchor isn't adjusted for them;" in note
    assert "trade(s)" not in note and "$" not in note


def test_note_is_silent_for_a_set_that_moves_no_cash():
    anchor = _adjust([("A", "SPLIT", 40.0, 1.0, "2026-11-13T14:00:00.5-05:00", False)])
    assert anchor["late_trade_unknown_count"] == 1
    assert cvm.late_trade_unknown_note(anchor, CASH_REC, date(2026, 11, 13), _money) is None


def test_note_is_silent_for_a_set_that_nets_to_under_a_dollar():
    anchor = _adjust([("A", "BUY", 1.0, 10.0, "2026-11-13T14:00:00.5-05:00", False),
                      ("A", "SELL", 1.0, 10.0, "2026-11-13T14:01:00.5-05:00", False)])
    assert anchor["late_trade_unknown_count"] == 2
    assert anchor["late_trade_unknown_delta"] == 0.0
    assert cvm.late_trade_unknown_note(anchor, CASH_REC, date(2026, 11, 13), _money) is None


def test_note_survives_an_unreadable_sync_time_in_the_cash_record():
    anchor = _adjust([BUY_BLANK_1430])
    note = cvm.late_trade_unknown_note(anchor, {"updated_at": "garbage"},
                                       date(2026, 11, 13), _money)
    assert "against the broker sync (left blank, or an import)" in note


# ─── 2. 💰 Account cash caption ─────────────────────────────────────────────

def test_account_sets_494_replay_names_the_unknown_row_apart():
    s = stm.account_cash_sets(_frame([SELL_TYPED_0956, BUY_BLANK_1430]), SYNC)
    assert s == {"certain_tickers": [], "certain_delta": 0.0,
                 "unknown_tickers": ["MRVL"], "unknown_delta": pytest.approx(-750.0)}


def test_account_sets_the_same_buy_typed_is_certain():
    s = stm.account_cash_sets(_frame([SELL_TYPED_0956, BUY_TYPED_1430]), SYNC)
    assert s["certain_tickers"] == ["MRVL"]
    assert s["certain_delta"] == pytest.approx(-750.0)
    assert s["unknown_tickers"] == [] and s["unknown_delta"] == 0.0


def test_account_sets_never_sum_the_unknown_set_into_the_certain_figure():
    s = stm.account_cash_sets(_frame([
        ("AAA", "SELL", 2.0, 50.0, "2026-11-13T15:00:00-05:00", True),      # certain +100
        ("BBB", "BUY", 1.0, 400.0, "2026-11-13T15:05:00.5-05:00", False),   # unknown -400
        BUY_PREFILL_1600,                                                    # unknown -600
    ]), SYNC)
    assert s["certain_tickers"] == ["AAA"] and s["certain_delta"] == pytest.approx(100.0)
    assert s["unknown_tickers"] == ["BBB", "CSCO"]
    assert s["unknown_delta"] == pytest.approx(-1000.0)


def test_account_sets_unreadable_rows_are_in_neither_and_unreadable_log_is_none():
    df = _frame([BUY_TYPED_1430, BUY_BLANK_1430])
    df.loc[len(df)] = df.iloc[0]
    df.loc[df.index[-1], ["traded_at", "ticker"]] = ["garbage", "BAD"]
    s = stm.account_cash_sets(df, SYNC)
    assert "BAD" not in s["certain_tickers"] + s["unknown_tickers"]
    assert stm.account_cash_sets(None, SYNC) is None
    assert stm.account_cash_sets(_frame([BUY_TYPED_1430]), "garbage") is None
    assert stm.account_cash_sets(pd.DataFrame([{"ticker": "A"}]), SYNC) is None


def test_account_sets_empty_log_is_a_real_empty_answer():
    s = stm.account_cash_sets(pd.DataFrame(columns=["traded_at", "ticker"]), SYNC)
    assert s == {"certain_tickers": [], "certain_delta": 0.0,
                 "unknown_tickers": [], "unknown_delta": 0.0}


def test_account_sets_weekend_visit_friday_blank_row_after_a_saturday_sync_is_before():
    df = _frame([BUY_BLANK_1430])
    assert stm.account_cash_sets(df, "2026-11-14T02:00:00-05:00")["unknown_tickers"] == []
    assert stm.account_cash_sets(df, SYNC)["unknown_tickers"] == ["MRVL"]


def test_account_unknown_sentence_singular_and_plural_wording():
    assert stm.account_cash_unknown_sentence(["MRVL"], -750.0) == (
        "MRVL (about $750) has no fill time the app can place against this sync "
        "(left blank, or an import), so the figures above may already include it.")
    assert stm.account_cash_unknown_sentence(["AAA", "BBB"], 1000.4) == (
        "AAA, BBB (about $1,000) have no fill time the app can place against this "
        "sync (left blank, or an import), so the figures above may already include "
        "them.")


def test_account_unknown_sentence_amount_goes_through_the_formatter():
    """The page passes its privacy mask: the amount is the formatter's output and
    no raw dollar sign is left in the sentence."""
    s = stm.account_cash_unknown_sentence(["MRVL"], -750.0, lambda v: "••••••")
    assert s == ("MRVL (about ••••••) has no fill time the app can place against "
                 "this sync (left blank, or an import), so the figures above may "
                 "already include it.")
    seen = []
    stm.account_cash_unknown_sentence(["A"], -1234.6, lambda v: seen.append(v) or "x")
    assert seen == [pytest.approx(1234.6)]            # absolute value, unrounded
    # a set that nets to under a dollar never calls it
    stm.account_cash_unknown_sentence(["A"], 0.2, lambda v: seen.append("called"))
    assert "called" not in seen


def test_account_unknown_sentence_edges():
    assert stm.account_cash_unknown_sentence([], -750.0) is None
    assert stm.account_cash_unknown_sentence(None, -750.0) is None
    s = stm.account_cash_unknown_sentence(["A"], 0.0)       # a SPLIT: no cash figure
    assert s.startswith("A has no fill time") and "$" not in s
    assert "$" not in stm.account_cash_unknown_sentence(["A"], 0.4)
    assert "(about $1)" in stm.account_cash_unknown_sentence(["A"], 0.5 + 0.5)


# ─── 3. F-306 budget basis ──────────────────────────────────────────────────

DAY = date(2026, 11, 13)
NOW = pd.Timestamp("2026-11-13T19:30:00Z")        # 14:30 ET


_FROM_ROWS = object()


def _basis(rows, net=8000.0, gross=20000.0, cash=-12000.0, as_of=SYNC_UTC, now=NOW,
           trades=_FROM_ROWS):
    df = _frame(rows) if trades is _FROM_ROWS else trades
    return dbud.budget_basis(gross, net, "levered", cash, as_of, df, DAY, now)


def test_budget_no_unknown_rows_leaves_lag_uncertain_zero():
    r = _basis([SELL_TYPED_0956, BUY_TYPED_1430])
    assert r["state"] == "ok"
    assert r["lag_adjustment"] == pytest.approx(-750.0)       # certain BUY, as today
    assert r["basis"] == pytest.approx(7250.0)
    assert r["lag_uncertain"] == 0.0 and r["lag_uncertain_tickers"] == []


def test_budget_494_replay_unknown_buy_is_subtracted_and_flagged():
    r = _basis([SELL_TYPED_0956, BUY_BLANK_1430])
    assert r["state"] == "ok"
    assert r["lag_adjustment"] == pytest.approx(-750.0)       # still subtracted
    assert r["basis"] == pytest.approx(7250.0)
    assert r["lag_uncertain"] == pytest.approx(750.0)
    assert r["lag_uncertain_tickers"] == ["MRVL"]


def test_budget_unknown_sell_is_not_added_and_is_flagged():
    r = _basis([("MRVL", "SELL", 5.0, 150.0, "2026-11-13T14:30:50.5-05:00", False)])
    assert r["lag_adjustment"] == 0.0 and r["basis"] == pytest.approx(8000.0)
    assert r["lag_uncertain"] == pytest.approx(750.0)
    assert r["lag_uncertain_tickers"] == ["MRVL"]


def test_budget_certain_sell_is_still_added():
    r = _basis([("MRVL", "SELL", 5.0, 150.0, "2026-11-13T14:30:00-05:00", True)])
    assert r["lag_adjustment"] == pytest.approx(750.0)
    assert r["basis"] == pytest.approx(8750.0) and r["lag_uncertain"] == 0.0


def test_budget_lag_uncertain_is_gross_of_both_unknown_sides():
    r = _basis([
        ("AAA", "BUY", 2.0, 100.0, "2026-11-13T14:00:00.5-05:00", False),    # -200 kept
        ("BBB", "SELL", 1.0, 300.0, "2026-11-13T14:05:00.5-05:00", False),   # +300 NOT added
        ("CCC", "SELL", 1.0, 50.0, "2026-11-13T15:00:00-05:00", True),       # certain +50
    ])
    assert r["lag_adjustment"] == pytest.approx(50.0 - 200.0)
    assert r["basis"] == pytest.approx(8000.0 - 150.0)
    assert r["lag_uncertain"] == pytest.approx(500.0)
    assert r["lag_uncertain_tickers"] == ["AAA", "BBB"]


def test_budget_same_day_16_00_prefill_buy_is_unknown_not_certain():
    r = _basis([BUY_PREFILL_1600])
    assert r["lag_adjustment"] == pytest.approx(-600.0)
    assert r["lag_uncertain"] == pytest.approx(600.0)
    assert r["lag_uncertain_tickers"] == ["CSCO"]


def test_budget_row_certainly_before_the_cash_record_is_ignored():
    r = _basis([("A", "BUY", 5.0, 150.0, "2026-11-13T10:30:00.5-05:00", False),
                SELL_TYPED_0956, ("B", "BUY", 1.0, 10.0, SYNC, True)])
    assert r["lag_adjustment"] == 0.0 and r["lag_uncertain"] == 0.0


def test_budget_later_dated_blank_time_row_is_certain_after():
    """A blank-time row stamped on a LATER ET date than the cash record (S3)."""
    r = _basis([("A", "BUY", 1.0, 100.0, "2026-11-16T10:05:00.5-05:00", False)],
               now=pd.Timestamp("2026-11-16T16:00:00Z"))
    assert r["lag_adjustment"] == pytest.approx(-100.0) and r["lag_uncertain"] == 0.0


def test_budget_frame_without_kind_column_is_the_old_raw_comparison():
    df = pd.DataFrame([
        {"traded_at": "2026-11-13T14:30:00-05:00", "action": "BUY", "ticker": "A",
         "shares": 1.0, "price": 100.0},
        {"traded_at": "2026-11-13T10:00:00-05:00", "action": "BUY", "ticker": "B",
         "shares": 1.0, "price": 100.0}])
    r = _basis(None, trades=df)
    assert r["lag_adjustment"] == pytest.approx(-100.0) and r["lag_uncertain"] == 0.0


def test_budget_unreadable_log_is_unknown_not_zero_lag():
    r = _basis(None, trades=None)
    assert r["state"] == "unknown" and r["basis"] is None
    assert r["lag_uncertain"] == 0.0 and r["lag_uncertain_tickers"] == []


def test_budget_a_buy_with_no_readable_time_still_makes_the_basis_unknown():
    """Today's treatment of a NaT BUY/SELL, unchanged: the whole answer is
    unknown, nothing is guessed."""
    df = _frame([BUY_TYPED_1430, BUY_BLANK_1430])
    df.loc[len(df)] = df.iloc[0]
    df.loc[df.index[-1], "traded_at"] = "garbage"
    r = _basis(None, trades=df)
    assert r["state"] == "unknown" and r["reason"] == "the trade log couldn't be read"


def test_budget_an_unknown_row_that_cannot_be_valued_is_unknown():
    df = _frame([("A", "BUY", None, 150.0, "2026-11-13T14:30:50.5-05:00", False)])
    assert _basis(None, trades=df)["state"] == "unknown"


def test_budget_called_by_an_unknown_buy_still_reports_what_is_uncertain():
    r = _basis([("A", "BUY", 100.0, 100.0, "2026-11-13T14:30:50.5-05:00", False)])
    assert r["state"] == "called" and r["basis"] <= 0
    assert r["lag_uncertain"] == pytest.approx(10000.0)


def test_budget_other_states_carry_no_uncertainty():
    for state in ("stale", "weird"):
        r = dbud.budget_basis(20000.0, 8000.0, state, -12000.0, SYNC_UTC,
                              _frame([BUY_BLANK_1430]), DAY, NOW)
        assert r["lag_uncertain"] == 0.0 and r["lag_uncertain_tickers"] == []


def test_trades_after_keeps_its_two_tuple_contract_and_excludes_unknown_rows():
    df = _frame([BUY_TYPED_1430, BUY_BLANK_1430,
                 ("S", "SELL", 1.0, 20.0, "2026-11-13T15:00:00-05:00", True)])
    assert dbud.trades_after(df, DAY, SYNC_UTC) == (750.0, 20.0)
    assert dbud.trades_after(None, DAY, SYNC_UTC) is None
    assert dbud.trades_after(df, DAY, "garbage") is None
    split = dbud.trades_after_split(df, DAY, SYNC_UTC)
    assert split == {"buy": 750.0, "sell": 20.0, "unknown_buy": 750.0,
                     "unknown_sell": 0.0, "unknown_tickers": ["MRVL"]}


def test_budget_weekend_visit_friday_blank_row_after_a_saturday_cash_record_is_before():
    r = _basis([BUY_BLANK_1430], as_of="2026-11-14T07:00:00+00:00",
               now=pd.Timestamp("2026-11-14T15:00:00Z"))
    assert r["lag_adjustment"] == 0.0 and r["lag_uncertain"] == 0.0


def test_budget_dst_spring_forward_day():
    cash_ts = "2026-03-08T15:19:14+00:00"       # 11:19:14 EDT
    r = dbud.budget_basis(
        20000.0, 8000.0, "levered", -12000.0, cash_ts,
        _frame([("A", "BUY", 1.0, 100.0, "2026-03-08T14:30:00.5-04:00", False),
                ("B", "BUY", 1.0, 10.0, "2026-03-08T14:30:00-04:00", True)]),
        date(2026, 3, 8), pd.Timestamp("2026-03-08T19:00:00Z"))
    assert r["lag_adjustment"] == pytest.approx(-110.0)
    assert r["lag_uncertain"] == pytest.approx(100.0)
    assert r["lag_uncertain_tickers"] == ["A"]


def test_buys_on_et_day_is_unchanged_by_the_new_row_shape():
    df = _frame([BUY_TYPED_1430, BUY_BLANK_1430, SELL_TYPED_0956])
    assert dbud.buys_on_et_day(df, DAY) == {"MRVL": 1500.0}


# ─── 3b. budget_view: caption text + cache plumbing ─────────────────────────

def test_lag_text_wording_and_plural():
    c = {"state": "ok", "lag_uncertain": 750.0, "lag_uncertain_tickers": ["MRVL"]}
    assert bv.lag_uncertain_text(c) == (
        "Budget basis may be up to $750 low until the next broker sync after the "
        "trade (for an import, the next day's): MRVL has no fill time the app can "
        "place against the cash snapshot (left blank, or an import).")
    c2 = {"state": "called", "lag_uncertain": 1234.4,
          "lag_uncertain_tickers": ["AAA", "BBB"]}
    assert bv.lag_uncertain_text(c2) == (
        "Budget basis may be up to $1,234 low until the next broker sync after the "
        "trades (for an import, the next day's): AAA, BBB have no fill time the app "
        "can place against the cash snapshot (left blank, or an import).")


def test_lag_text_masks_the_amount_in_privacy_mode():
    c = {"state": "ok", "lag_uncertain": 750.0, "lag_uncertain_tickers": ["MRVL"]}
    t = bv.lag_uncertain_text(c, mask=True)
    assert "$" not in t and "••••••" in t and "MRVL" in t


@pytest.mark.parametrize("cache", [
    None, "x", {}, {"state": "unknown", "lag_uncertain": 5.0, "lag_uncertain_tickers": ["A"]},
    {"state": "ok"}, {"state": "ok", "lag_uncertain": 0.0, "lag_uncertain_tickers": ["A"]},
    {"state": "ok", "lag_uncertain": None, "lag_uncertain_tickers": ["A"]},
    {"state": "ok", "lag_uncertain": "bad", "lag_uncertain_tickers": ["A"]},
    {"state": "ok", "lag_uncertain": 5.0, "lag_uncertain_tickers": []},
    {"state": "ok", "lag_uncertain": 5.0, "lag_uncertain_tickers": None},
    {"state": "ok", "lag_uncertain": 5.0, "lag_uncertain_tickers": [" "]},
])
def test_lag_text_says_nothing_without_a_real_uncertainty(cache):
    assert bv.lag_uncertain_text(cache) is None


def _inputs():
    return {"gross_book": 20000.0, "net_capital": 8000.0, "basis_state": "levered",
            "cash_balance": -12000.0, "cash_as_of": SYNC_UTC}


_BRIEF = {"act_today": [], "review_list": [],
          "grow_today": {"tone": "bull", "new_picks": [], "add_positions": [],
                         "overflow_picks": []}}


def test_postpass_cache_carries_the_uncertainty():
    out = bv.postpass(_BRIEF, _inputs(), _frame([SELL_TYPED_0956, BUY_BLANK_1430]),
                      {}, DAY, NOW)
    cache = out["cache"]
    assert cache is not None and cache["state"] == "ok"
    assert cache["lag_adjustment"] == pytest.approx(-750.0)
    assert cache["lag_uncertain"] == pytest.approx(750.0)
    assert cache["lag_uncertain_tickers"] == ["MRVL"]
    assert bv.lag_uncertain_text(cache).startswith("Budget basis may be up to $750 low")


def test_postpass_cache_is_clean_when_nothing_is_unknown():
    out = bv.postpass(_BRIEF, _inputs(), _frame([SELL_TYPED_0956, BUY_TYPED_1430]),
                      {}, DAY, NOW)
    assert out["cache"]["lag_uncertain"] == 0.0
    assert bv.lag_uncertain_text(out["cache"]) is None


def test_downstream_summary_carries_the_uncertainty():
    trades = _frame([SELL_TYPED_0956, BUY_BLANK_1430])
    cache = bv.postpass(_BRIEF, _inputs(), trades, {}, DAY, NOW)["cache"]
    res = bv.plan_downstream(_inputs(), trades, {}, cache, DAY, NOW, [])
    assert res["summary"] is not None, res
    assert res["summary"]["lag_uncertain"] == pytest.approx(750.0)
    assert res["summary"]["lag_uncertain_tickers"] == ["MRVL"]
    assert bv.lag_uncertain_text(res["summary"]) is not None


# ─── 4. wiring (app.py has no tests: pin each consumer end) ─────────────────

_APP = (_ROOT / "app.py").read_text(encoding="utf-8")


def test_app_wires_the_account_caption_through_sync_timing_only():
    assert "sync_timing.account_cash_sets(" in _APP
    assert "sync_timing.account_cash_unknown_sentence(" in _APP
    # the old inline raw comparison is gone from the caption block
    assert "_acct_traded_since = broker_sync.tickers_traded_since(" not in _APP
    assert "_ts_acct > _cap_acct" not in _APP


def test_app_renders_the_capital_vs_margin_note_and_the_three_budget_captions():
    assert "capital_vs_margin.late_trade_unknown_note(" in _APP
    assert len(re.findall(r"_budget_view\.lag_uncertain_text\(", _APP)) == 3


def test_app_escapes_dollars_in_every_new_caption():
    for name in ("_bu", "_an_bu", "_wl_bu", "_acct_unk"):
        assert f'{name}.replace("$", "\\\\$")' in _APP, name


def test_app_masks_the_account_unknown_amount_and_drops_the_dead_import():
    assert 'lambda v: _m(f"${v:,.0f}"))' in _APP
    assert "today_trade_cash_delta" not in _APP
