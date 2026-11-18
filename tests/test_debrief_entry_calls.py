"""Tests for stock_analyzer/debrief_entry_calls.py (C3a of the acted-rule work).

Spec: docs/plans/acted-rule-c3-spec.md. Times are UTC in the fixtures: EST =
UTC-5 after 2026-11-01. Calendar used: Mon 2026-11-02 .. Sun 11-08 is one
debrief week, Mon 11-09 .. Sun 11-15 the next; 11-26 is Thanksgiving and 11-27
a half-day.
"""
from __future__ import annotations

import inspect
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import constants
from stock_analyzer import debrief_entry_calls as dec
from stock_analyzer import entry_call_action as eca

D = date.fromisoformat

COLS = ["id", "ticker", "action", "traded_at", "trigger_type", "notes",
        "broker_txn_id"]


def trade(id_, ticker, action, traded_at, trigger="MANUAL", notes="", broker=None):
    return {"id": id_, "ticker": ticker, "action": action, "traded_at": traded_at,
            "trigger_type": trigger, "notes": notes, "broker_txn_id": broker}


def trades(*rows):
    return pd.DataFrame(list(rows), columns=COLS)


def rec(id_, ticker, rec_date, rec_type="new_pick", conviction="Buy",
        verdict="Confirmed", already_held=None, **extra):
    r = {"id": id_, "ticker": ticker, "rec_date": rec_date, "rec_type": rec_type,
         "conviction": conviction, "verdict": verdict, "already_held": already_held,
         "surfaced_at": f"{rec_date}T13:00:00"}
    r.update(extra)
    return r


def recs(*rows):
    return pd.DataFrame(list(rows))


def week(recs_df, trades_df, ws, we, **kw):
    return dec.weekly_entry_calls(recs_df, trades_df, week_start=D(ws),
                                  week_ending=D(we), **kw)


def by_ticker(rows):
    return {r["ticker"]: r for r in rows}


# ── 1. recs_load_start ──────────────────────────────────────────────────────

@pytest.mark.parametrize("monday,expected", [
    ("2026-11-09", "2026-11-02"),     # week_start - 7 (window rule alone: 11-05)
    ("2026-11-02", "2026-10-26"),     # (window rule alone: 10-29)
    ("2026-11-30", "2026-11-23"),     # Thanksgiving week (window rule alone: 11-25)
])
def test_recs_load_start_known_mondays(monday, expected):
    assert constants.ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS == 2
    assert dec.recs_load_start(D(monday)) == D(expected)
    assert dec.recs_load_start(monday) == D(expected)          # ISO text accepted


def test_recs_load_start_is_sufficient_and_minimal_for_every_monday():
    m = D("2026-01-05")
    while m <= D("2027-12-31"):
        for n in (0, 1, 2, 3, 5):
            r = dec.recs_load_start(m, n)
            assert r <= m - timedelta(days=1)
            assert r <= m - timedelta(days=7), (m, n)      # last week's calls load
            # sufficiency: no earlier call's window reaches the week
            for k in range(1, 15):
                assert eca.window_end(r - timedelta(days=k), n) < m, (m, n, k)
            # minimality: past last week, the load start itself still reaches the
            # week (N = 0 never does, so it stays at week_start - 7)
            assert r == m - timedelta(days=7) or eca.window_end(r, n) >= m, (m, n)
        m += timedelta(days=7)


def test_recs_load_start_follows_the_constant(monkeypatch):
    monkeypatch.setattr(constants, "ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS", 3)
    assert dec.recs_load_start(D("2026-11-09")) == dec.recs_load_start(D("2026-11-09"), 3)
    assert dec.recs_load_start(D("2026-11-09")) == D("2026-11-02")   # week_start - 7 binds
    out = week(recs(), trades(), "2026-11-09", "2026-11-15")
    assert out["window_trading_days"] == 3


def test_recs_load_start_rejects_a_non_date():
    with pytest.raises(ValueError):
        dec.recs_load_start("not a date")


# ── 2. the NVDA two-week case (UD-1) ────────────────────────────────────────

NVDA_RECS = recs(rec(1, "NVDA", "2026-11-06", "enter_now", conviction="Strong Buy",
                     verdict="Confirmed", already_held=False))
NVDA_BUY = trades(trade(7, "NVDA", "BUY", "2026-11-09T15:00:00+00:00"))


def test_nvda_week_ending_nov_8_still_open_and_not_in_share():
    out = week(NVDA_RECS, NVDA_BUY, "2026-11-02", "2026-11-08")
    assert out["recs_status"] == "ok" and out["judge_state"] == "ok"
    (row,) = out["this_week"]
    assert row["status"] == "still_open"
    assert row["window_end"] == "2026-11-10"
    assert out["carried_over"] == []
    assert out["follow"] == {"n_acted": 0, "n_not_acted": 0, "n_still_open": 1,
                             "n_unknown": 0, "n_carried": 0, "share_pct": None}
    assert out["matched_trade_ids"] == []
    assert dec.entry_signal_lines(out) == [
        "Entry signals this week:",
        "• NVDA: Enter Now call (Strong Buy, Confirmed) on Fri Nov 6. "
        "Still open (decision window ends Tue Nov 10); next week's debrief "
        "says whether you acted.",
    ]


def test_nvda_week_ending_nov_15_carried_over_and_counted():
    out = week(NVDA_RECS, NVDA_BUY, "2026-11-09", "2026-11-15")
    assert out["this_week"] == []
    (row,) = out["carried_over"]
    assert row["status"] == "acted"
    assert row["matched_trade_date"] == "2026-11-09"
    assert row["matched_trade_id"] == 7
    assert row["carried_from"] == "still_open"
    assert out["follow"]["n_acted"] == 1
    assert out["follow"]["n_carried"] == 1
    assert out["follow"]["share_pct"] == 100
    assert out["matched_trade_ids"] == [7]
    assert dec.entry_signal_lines(out) == [
        "Entry signals this week:",
        "• No entry signals (new positions, adds to winners, Enter Now) "
        "were surfaced this week.",
        "Carried over from last week:",
        "• NVDA: Enter Now call (Strong Buy, Confirmed) on Fri Nov 6 "
        "(still open at last week's end). Acted on: you bought on Mon Nov 9.",
    ]


def test_loading_from_recs_load_start_is_enough():
    """The frame a caller loads from `recs_load_start` carries the call."""
    start = dec.recs_load_start(D("2026-11-09"))
    assert start <= D("2026-11-06")
    # and all of last week's calls, which the by-name carry-over reads
    assert start <= D("2026-11-02")
    assert dec.recs_load_start(D("2026-11-02")) <= D("2026-10-26")


# ── 3. MRVL #479 not #464 ───────────────────────────────────────────────────

def test_mrvl_week_of_nov_8():
    mrvl = recs(rec(1, "MRVL", "2026-11-06"))
    base = [trade(454, "MRVL", "BUY", "2026-10-30T15:00:00+00:00"),
            trade(464, "MRVL", "SELL", "2026-11-02T15:00:00+00:00")]
    full = trades(*base,
                  trade(479, "MRVL", "BUY", "2026-11-06T14:30:00+00:00", "RECOMMENDATION"),
                  trade(480, "MRVL", "BUY", "2026-11-06T14:35:00+00:00"))
    out = week(mrvl, full, "2026-11-02", "2026-11-08")
    (row,) = out["this_week"]
    assert row["status"] == "acted" and row["matched_trade_id"] == 479
    assert out["matched_trade_ids"] == [479]
    out = week(mrvl, trades(*base), "2026-11-02", "2026-11-08")
    assert out["this_week"][0]["status"] == "still_open"
    assert out["matched_trade_ids"] == []


# ── 4. what never credits a call ────────────────────────────────────────────

def test_sell_buy_before_call_and_buy_after_week_ending_never_credit():
    r = recs(rec(1, "SELLY", "2026-11-04"), rec(2, "EARLY", "2026-11-04"),
             rec(3, "LATE", "2026-11-06"))
    t = trades(
        trade(1, "SELLY", "SELL", "2026-11-05T15:00:00+00:00"),
        trade(2, "EARLY", "BUY", "2026-11-03T15:00:00+00:00"),
        trade(3, "LATE", "BUY", "2026-11-09T15:00:00+00:00"),     # after week_ending
    )
    out = week(r, t, "2026-11-02", "2026-11-08")
    rows = by_ticker(out["this_week"])
    assert rows["SELLY"]["status"] == "not_acted"
    assert rows["EARLY"]["status"] == "not_acted"
    assert rows["LATE"]["status"] == "still_open"
    assert out["matched_trade_ids"] == []


def test_calls_after_week_ending_are_not_listed_and_do_not_turn_a_name_unknown():
    r = recs(rec(1, "NVDA", "2026-11-02"),
             rec(2, "NVDA", "2026-11-10"),          # after week_ending
             rec(3, "AMD", "2026-11-10"))           # only after week_ending
    out = week(r, trades(), "2026-11-02", "2026-11-08")
    assert [x["ticker"] for x in out["this_week"]] == ["NVDA"]
    row = out["this_week"][0]
    assert row["status"] == "not_acted"
    assert row["times_surfaced"] == 1
    assert row["call_dates"] == ["2026-11-02"]


# ── 5. which calls are judged ───────────────────────────────────────────────

def test_already_held_enter_now_excluded_blank_judged_other_types_never_listed():
    r = recs(
        rec(1, "HELD", "2026-11-02", "enter_now", already_held=True),
        rec(2, "BLANK", "2026-11-02", "enter_now", already_held=None),
        rec(3, "NAN", "2026-11-02", "enter_now", already_held=float("nan")),
        rec(4, "CAND", "2026-11-02", "buy_candidate"),
        rec(5, "OVER", "2026-11-02", "overflow_pick"),
        rec(6, "MIX", "2026-11-02", "new_pick"),
        rec(7, "MIX", "2026-11-03", "enter_now", already_held=True),
    )
    out = week(r, trades(), "2026-11-02", "2026-11-08")
    rows = by_ticker(out["this_week"])
    assert sorted(rows) == ["BLANK", "MIX", "NAN"]
    assert rows["MIX"]["times_surfaced"] == 1               # the held enter_now isn't counted
    assert rows["MIX"]["rec_types"] == ["new_pick"]
    assert rows["MIX"]["call_dates"] == ["2026-11-02"]
    assert out["n_undated"] == 0


def test_roll_up_fields_conviction_verdict_times_surfaced_and_reason():
    r = recs(
        rec(1, "AAA", "2026-11-02", "new_pick", conviction="Buy", verdict="Confirmed"),
        rec(2, "AAA", "2026-11-03", "enter_now", conviction="Strong Buy",
            verdict="Confirmed", already_held=False),
        rec(3, "AAA", "2026-11-04", "new_pick", conviction="Strong Buy", verdict="Watch"),
    )
    out = week(r, trades(), "2026-11-02", "2026-11-08")
    (row,) = out["this_week"]
    assert row["times_surfaced"] == 3
    assert row["conviction"] == "Strong Buy"               # the latest call's
    assert row["verdict"] == "Confirmed"                   # the most common
    assert row["rec_types"] == ["enter_now", "new_pick"]
    assert row["call_dates"] == ["2026-11-02", "2026-11-03", "2026-11-04"]
    assert row["status"] == "not_acted"
    assert row["reason"] is None
    assert row["window_end"] == "2026-11-06"               # the latest call's window


def test_unreadable_call_dates_are_counted_not_listed():
    r = recs(rec(1, "AAA", "2026-11-02"), rec(2, "BBB", None),
             rec(3, "CCC", "garbage"), rec(4, "DDD", None, "buy_candidate"))
    out = week(r, trades(), "2026-11-02", "2026-11-08")
    assert [x["ticker"] for x in out["this_week"]] == ["AAA"]
    assert out["n_undated"] == 2                           # the buy_candidate is not an entry call
    assert "• 2 entry signal row(s) had no readable date and are not shown." \
        in dec.entry_signal_lines(out)


# ── 6. carried over ─────────────────────────────────────────────────────────

def test_carried_over_exclusions():
    r = recs(
        rec(1, "OPEN", "2026-10-30"),           # no buy; window ends Tue 11-03
        rec(2, "ACTD", "2026-10-30"),           # bought inside its own week
        rec(3, "SHUT", "2026-10-28"),           # window closed Fri 10-30
        rec(4, "OLD", "2026-10-27"),            # long closed
    )
    t = trades(trade(1, "ACTD", "BUY", "2026-10-30T15:00:00+00:00"))
    out = week(r, t, "2026-11-02", "2026-11-08")
    assert out["this_week"] == []
    (row,) = out["carried_over"]
    assert row["ticker"] == "OPEN"
    assert row["status"] == "not_acted"                    # re-judged at the week's end
    assert row["window_end"] == "2026-11-03"
    assert out["follow"]["n_not_acted"] == 1
    assert out["follow"]["n_carried"] == 1
    assert out["follow"]["share_pct"] == 0


def test_carried_acted_inside_the_new_week():
    r = recs(rec(1, "OPEN", "2026-10-30"))
    t = trades(trade(1, "OPEN", "BUY", "2026-11-02T15:00:00+00:00"))
    out = week(r, t, "2026-11-02", "2026-11-08")
    assert out["carried_over"][0]["status"] == "acted"
    assert out["carried_over"][0]["matched_trade_date"] == "2026-11-02"


def test_same_ticker_carried_and_called_again_counts_once_this_week_wins():
    # carried call 10-30 (window to 11-03) is not acted; this week's 11-03 call
    # is acted by the 11-04 buy. One name, one count, acted.
    r = recs(rec(1, "NVDA", "2026-10-30"), rec(2, "NVDA", "2026-11-03"))
    t = trades(trade(1, "NVDA", "BUY", "2026-11-04T15:00:00+00:00"))
    out = week(r, t, "2026-11-02", "2026-11-08")
    assert out["this_week"][0]["status"] == "acted"
    assert out["carried_over"][0]["status"] == "not_acted"
    assert out["follow"] == {"n_acted": 1, "n_not_acted": 0, "n_still_open": 0,
                             "n_unknown": 0, "n_carried": 0, "share_pct": 100}


def test_same_ticker_carried_acted_beats_this_week_still_open():
    r = recs(rec(1, "NVDA", "2026-10-30"), rec(2, "NVDA", "2026-11-06"))
    t = trades(trade(1, "NVDA", "BUY", "2026-11-02T15:00:00+00:00"))
    out = week(r, t, "2026-11-02", "2026-11-08")
    assert out["this_week"][0]["status"] == "still_open"
    assert out["carried_over"][0]["status"] == "acted"
    f = out["follow"]
    assert (f["n_acted"], f["n_still_open"], f["n_carried"]) == (1, 0, 1)
    assert out["matched_trade_ids"] == [1]


def test_ud1_share_includes_carried_names_and_counts_them():
    r = recs(
        rec(1, "AAA", "2026-11-02"),                                  # acted this week
        rec(2, "BBB", "2026-11-02"),                                  # not acted this week
        rec(3, "CCC", "2026-10-30", "enter_now", already_held=False), # carried, acted
        rec(4, "DDD", "2026-10-30"),                                  # carried, not acted
        rec(5, "EEE", "2026-11-06"),                                  # still open
    )
    t = trades(trade(1, "AAA", "BUY", "2026-11-03T15:00:00+00:00"),
               trade(2, "CCC", "BUY", "2026-11-02T15:00:00+00:00"))
    out = week(r, t, "2026-11-02", "2026-11-08")
    assert out["follow"] == {"n_acted": 2, "n_not_acted": 2, "n_still_open": 1,
                             "n_unknown": 0, "n_carried": 2, "share_pct": 50}
    assert out["matched_trade_ids"] == [1, 2]


@pytest.mark.parametrize("n_acted,n_total,expected", [(1, 3, 33), (2, 3, 67), (1, 8, 13), (0, 2, 0)])
def test_share_rounds_to_whole_percent(n_acted, n_total, expected):
    rows = [rec(i, f"T{i}", "2026-11-02") for i in range(n_total)]
    buys = [trade(i, f"T{i}", "BUY", "2026-11-02T15:00:00+00:00") for i in range(n_acted)]
    out = week(recs(*rows), trades(*buys), "2026-11-02", "2026-11-08")
    assert out["follow"]["share_pct"] == expected


def test_carry_over_is_by_name_xyz_two_week_case():
    # XYZ: new_pick Mon 11-02, BUY Tue 11-03 (acted), then add_winner Fri 11-06.
    xyz = recs(rec(1, "XYZ", "2026-11-02", "new_pick"),
               rec(2, "XYZ", "2026-11-06", "add_winner"))
    buy = trades(trade(1, "XYZ", "BUY", "2026-11-03T15:00:00+00:00"))
    wk1 = week(xyz, buy, "2026-11-02", "2026-11-08")
    (row,) = wk1["this_week"]
    assert row["status"] == "acted" and row["times_surfaced"] == 2
    assert wk1["follow"]["share_pct"] == 100
    # the Fri 11-06 call is still open at 11-08, but the NAME printed as acted
    # last week, so it is not carried into the week ending 11-15
    wk2 = week(xyz, buy, "2026-11-09", "2026-11-15")
    assert wk2["this_week"] == []
    assert wk2["carried_over"] == []
    assert wk2["follow"] == {"n_acted": 0, "n_not_acted": 0, "n_still_open": 0,
                             "n_unknown": 0, "n_carried": 0, "share_pct": None}
    assert wk2["matched_trade_ids"] == []
    assert dec.entry_signal_lines(wk2) == [
        "Entry signals this week:",
        "• No entry signals (new positions, adds to winners, Enter Now) "
        "were surfaced this week.",
    ]


def test_carry_over_name_still_open_last_week_is_carried():
    # same name, but nothing bought before the 11-06 call: still open last week
    xyz = recs(rec(1, "XYZ", "2026-11-02", "new_pick"),
               rec(2, "XYZ", "2026-11-06", "add_winner"))
    buy = trades(trade(1, "XYZ", "BUY", "2026-11-09T15:00:00+00:00"))
    wk1 = week(xyz, buy, "2026-11-02", "2026-11-08")
    assert wk1["this_week"][0]["status"] == "still_open"
    wk2 = week(xyz, buy, "2026-11-09", "2026-11-15")
    (row,) = wk2["carried_over"]
    assert row["ticker"] == "XYZ" and row["status"] == "acted"


def test_unknown_carry_is_carried_and_rejudged_at_week_ending():
    # a BUY with an unreadable date makes the 10-30 call unknown at 11-01, and
    # its window (to 11-03) reaches the new week; a valid 11-02 buy then acts it
    r = recs(rec(1, "AAA", "2026-10-30"))
    t = trades(trade(1, "AAA", "BUY", "not a date"),
               trade(2, "AAA", "BUY", "2026-11-02T15:00:00+00:00"))
    out = week(r, t, "2026-11-02", "2026-11-08")
    (row,) = out["carried_over"]
    assert row["status"] == "acted" and row["matched_trade_id"] == 2
    # still unreadable and nothing else: carried as "Not judged"
    t = trades(trade(1, "AAA", "BUY", "not a date"))
    out = week(r, t, "2026-11-02", "2026-11-08")
    (row,) = out["carried_over"]
    assert row["status"] == "unknown" and row["reason"] == "trade_date_unreadable"
    assert out["follow"]["n_unknown"] == 1 and out["follow"]["n_carried"] == 0
    assert dec.entry_signal_lines(out)[-1] == (
        "• AAA: New position call (Buy, Confirmed) on Fri Oct 30 "
        "(its decision window ran into this week). "
        "Not judged: a buy of this name has a date that couldn't be read.")
    assert row["carried_from"] == "unknown"
    assert "still open" not in dec.entry_signal_lines(out)[-1]
    # an unknown call whose window closed before the new week is not carried
    r = recs(rec(1, "AAA", "2026-10-27"))                  # window ends Thu 10-29
    assert week(r, t, "2026-11-02", "2026-11-08")["carried_over"] == []


def test_tie_in_both_blocks_both_acted_is_not_counted_as_carried():
    # carried 10-30 call acted by the 11-02 buy; this week's 11-03 call acted by
    # the 11-04 buy. One name, acted in both blocks: this week's row wins the
    # tie, so n_carried stays 0 (pins the `<`, not `<=`, in `_follow`).
    r = recs(rec(1, "NVDA", "2026-10-30"), rec(2, "NVDA", "2026-11-03"))
    t = trades(trade(1, "NVDA", "BUY", "2026-11-02T15:00:00+00:00"),
               trade(2, "NVDA", "BUY", "2026-11-04T15:00:00+00:00"))
    out = week(r, t, "2026-11-02", "2026-11-08")
    assert out["this_week"][0]["status"] == "acted"
    assert out["carried_over"][0]["status"] == "acted"
    assert out["follow"] == {"n_acted": 1, "n_not_acted": 0, "n_still_open": 0,
                             "n_unknown": 0, "n_carried": 0, "share_pct": 100}


# ── 7. time context ─────────────────────────────────────────────────────────

def test_dst_change_week_ending_nov_8():
    # DST ended Sun 2026-11-01; the Fri 10-30 call's window ends Tue 11-03.
    r = recs(rec(1, "LATE", "2026-10-30"), rec(2, "INTIME", "2026-10-30"))
    t = trades(
        # Tue 11-03 23:30 EST = Wed 11-04 04:30 UTC: still Tuesday in ET
        trade(1, "INTIME", "BUY", "2026-11-04T04:30:00+00:00"),
        # Wed 11-04 00:30 EST = 05:30 UTC: one session too late
        trade(2, "LATE", "BUY", "2026-11-04T05:30:00+00:00"),
    )
    out = week(r, t, "2026-11-02", "2026-11-08")
    assert out["this_week"] == []
    rows = by_ticker(out["carried_over"])
    assert rows["INTIME"]["status"] == "acted"
    assert rows["INTIME"]["matched_trade_date"] == "2026-11-03"
    assert rows["INTIME"]["window_end"] == "2026-11-03"
    assert rows["LATE"]["status"] == "not_acted"
    # a Fri 10-30 20:30 EDT fill (Sat 10-31 00:30 UTC) is the call day itself
    t = trades(trade(3, "LATE", "BUY", "2026-10-31T00:30:00+00:00"))
    carried = by_ticker(week(r, t, "2026-11-02", "2026-11-08")["carried_over"])
    assert "LATE" not in carried                           # acted inside its own week

def test_thanksgiving_and_half_day():
    r = recs(
        rec(1, "TUE", "2026-11-24"),
        rec(2, "WED", "2026-11-25"),
        rec(3, "FRI", "2026-11-27"),
    )
    t = trades(
        trade(1, "TUE", "BUY", "2026-11-27T15:00:00+00:00"),    # half-day Friday
        trade(2, "WED", "BUY", "2026-11-30T15:00:00+00:00"),
        trade(3, "FRI", "BUY", "2026-12-01T15:00:00+00:00"),
    )
    wk1 = week(r, t, "2026-11-23", "2026-11-29")
    rows = by_ticker(wk1["this_week"])
    assert rows["TUE"]["status"] == "acted"
    assert rows["WED"]["status"] == "still_open" and rows["WED"]["window_end"] == "2026-11-30"
    assert rows["FRI"]["status"] == "still_open" and rows["FRI"]["window_end"] == "2026-12-01"

    wk2 = week(r, t, "2026-11-30", "2026-12-06")
    assert wk2["this_week"] == []
    carried = by_ticker(wk2["carried_over"])
    assert sorted(carried) == ["FRI", "WED"]                   # TUE was acted in its own week
    assert carried["WED"]["status"] == "acted"
    assert carried["FRI"]["status"] == "acted"
    # a buy one session too late is not acted
    late = trades(trade(2, "WED", "BUY", "2026-12-01T15:00:00+00:00"))
    wk2 = week(r, late, "2026-11-30", "2026-12-06")
    assert by_ticker(wk2["carried_over"])["WED"]["status"] == "not_acted"


def test_after_hours_fills_date_in_et():
    # Fri 20:30 ET = Sat 01:30 UTC: still Friday, the last day of a Wed call's window
    r = recs(rec(1, "FRI", "2026-11-04"))
    t = trades(trade(1, "FRI", "BUY", "2026-11-07T01:30:00+00:00"))
    row = week(r, t, "2026-11-02", "2026-11-08")["this_week"][0]
    assert row["status"] == "acted" and row["matched_trade_date"] == "2026-11-06"
    # Sun 23:30 ET = Mon 04:30 UTC: inside the week ending Sunday 11-08
    r = recs(rec(1, "SUN", "2026-11-06"))
    t = trades(trade(1, "SUN", "BUY", "2026-11-09T04:30:00+00:00"))
    row = week(r, t, "2026-11-02", "2026-11-08")["this_week"][0]
    assert row["status"] == "acted" and row["matched_trade_date"] == "2026-11-08"


def test_late_logged_imports_and_sells():
    r = recs(rec(1, "IMP", "2026-11-06"), rec(2, "RAW", "2026-11-06"))
    t = trades(
        # provenance import at midnight UTC on 11-11: re-anchored to 11-11 -> too late
        trade(1, "IMP", "BUY", "2026-11-11T00:00:00+00:00", notes="Robinhood import"),
        # no provenance: reads as the ET day before (11-10) -> inside the window
        trade(2, "RAW", "BUY", "2026-11-11T00:00:00+00:00"),
    )
    rows = by_ticker(week(r, t, "2026-11-09", "2026-11-15")["carried_over"])
    assert rows["IMP"]["status"] == "not_acted"
    assert rows["RAW"]["status"] == "acted"
    # a late-logged SELL never affects acted
    t2 = trades(trade(3, "IMP", "SELL", "2026-11-09T15:00:00+00:00", notes="Robinhood import"))
    assert by_ticker(week(r, t2, "2026-11-09", "2026-11-15")["carried_over"])["IMP"]["status"] == "not_acted"


def test_deterministic_and_reads_no_clock():
    a = week(NVDA_RECS, NVDA_BUY, "2026-11-09", "2026-11-15")
    b = week(NVDA_RECS, NVDA_BUY, "2026-11-09", "2026-11-15")
    assert a == b
    src = inspect.getsource(dec)
    for token in ("now(", "today(", "utcnow", "streamlit", "import db", "from stock_analyzer import db"):
        assert token not in src, token


# ── 8. offline ──────────────────────────────────────────────────────────────

def test_trades_none_judges_nothing_and_never_says_not_acted():
    r = recs(rec(1, "AAA", "2026-11-02"), rec(2, "BBB", "2026-10-30"))
    out = week(r, None, "2026-11-02", "2026-11-08")
    assert out["recs_status"] == "ok" and out["judge_state"] == "trades_unavailable"
    (row,) = out["this_week"]
    assert row["status"] == "unknown" and row["reason"] == "trades_unavailable"
    # the 10-30 call read unknown last week too, so it is carried (Not judged)
    (crow,) = out["carried_over"]
    assert crow["ticker"] == "BBB" and crow["status"] == "unknown"
    assert crow["carried_from"] == "unknown"
    bbb_line = [ln for ln in dec.entry_signal_lines(out) if "BBB" in ln][0]
    assert "(its decision window ran into this week)" in bbb_line
    assert "still open" not in bbb_line
    assert out["follow"]["share_pct"] is None and out["follow"]["n_unknown"] == 2
    lines = dec.entry_signal_lines(out)
    assert lines[1] == ("• AAA: New position call (Buy, Confirmed) on Mon Nov 2. "
                        "Not judged: the trade journal couldn't be read.")
    assert not any("Not acted on" in ln for ln in lines)


def test_unreadable_trades_frame():
    r = recs(rec(1, "AAA", "2026-11-02"))
    out = week(r, pd.DataFrame({"foo": [1]}), "2026-11-02", "2026-11-08")
    assert out["judge_state"] == "trades_unreadable"
    assert out["this_week"][0]["reason"] == "trades_unreadable"


def test_trade_date_unreadable_reason():
    r = recs(rec(1, "AAA", "2026-11-02"))
    t = trades(trade(1, "AAA", "BUY", "not a date"))
    row = week(r, t, "2026-11-02", "2026-11-08")["this_week"][0]
    assert row["status"] == "unknown" and row["reason"] == "trade_date_unreadable"


def test_recs_unavailable_is_never_a_quiet_week():
    for bad in (None, "nope", pd.DataFrame({"ticker": ["A"], "rec_date": ["2026-11-02"]})):
        out = week(bad, trades(), "2026-11-02", "2026-11-08")
        assert out["recs_status"] == "unavailable"
        assert out["this_week"] == [] and out["carried_over"] == []
        assert dec.entry_signal_lines(out) == []
    # a missing column on a non-empty frame; an empty frame is fine
    assert week(recs(), trades(), "2026-11-02", "2026-11-08")["recs_status"] == "ok"
    assert week(pd.DataFrame(), trades(), "2026-11-02", "2026-11-08")["recs_status"] == "ok"


def test_quiet_week_line():
    out = week(recs(), trades(), "2026-11-02", "2026-11-08")
    assert dec.entry_signal_lines(out) == [
        "Entry signals this week:",
        "• No entry signals (new positions, adds to winners, Enter Now) "
        "were surfaced this week.",
    ]


# ── 9. pd.NA ids ────────────────────────────────────────────────────────────

def test_pd_na_ids_through_classify_calls_weekly_and_apply_to_matched():
    frame = pd.DataFrame({
        "id": pd.array([1, pd.NA], dtype="Int64"),
        "ticker": ["AAA", "BBB"], "rec_date": ["2026-11-02", "2026-11-02"],
        "rec_type": ["enter_now", "new_pick"], "already_held": [True, False],
        "conviction": ["Buy", "Buy"], "verdict": ["x", "y"],
    })
    t = trades(trade(5, "BBB", "BUY", "2026-11-03T15:00:00+00:00"))
    res = eca.classify_calls(frame, t, cutoff=D("2026-11-08"))
    assert [r["status"] for r in res] == ["excluded", "acted"]
    assert eca.per_name(res)
    out = week(frame, t, "2026-11-02", "2026-11-08")
    assert [r["ticker"] for r in out["this_week"]] == ["BBB"]           # AAA is already held
    assert out["this_week"][0]["status"] == "acted"
    assert out["matched_trade_ids"] == [5]
    matched = [{"id": pd.NA, "ticker": "BBB", "rec_date": "2026-11-02",
                "rec_type": "new_pick"}]
    got = eca.apply_to_matched(matched, frame, t, cutoff=D("2026-11-08"))
    assert got[0]["entry_call_status"] == "acted"


# ── 10. entry_signal_lines: exact strings ───────────────────────────────────

def _row(**kw):
    base = {"ticker": "NVDA", "status": "acted", "reason": None,
            "rec_types": ["enter_now"], "conviction": "", "verdict": "",
            "times_surfaced": 1, "call_dates": ["2026-11-06"],
            "window_end": "2026-11-10", "matched_trade_date": "2026-11-09",
            "matched_trade_id": 1}
    base.update(kw)
    return base


def _entry(this_week=(), carried=(), **kw):
    e = {"recs_status": "ok", "judge_state": "ok", "this_week": list(this_week),
         "carried_over": list(carried), "n_undated": 0, "window_trading_days": 2}
    e.update(kw)
    return e


def test_line_acted_with_and_without_end_week_pct():
    row = _row(conviction="Strong Buy", verdict="Confirmed", end_week_pct=2.34)
    assert dec.entry_signal_lines(_entry([row]))[1] == (
        "• NVDA: Enter Now call (Strong Buy, Confirmed) on Fri Nov 6. "
        "Acted on: you bought on Mon Nov 9. Ended the week +2.3%.")
    row = _row(end_week_pct=-1.26)
    assert dec.entry_signal_lines(_entry([row]))[1] == (
        "• NVDA: Enter Now call on Fri Nov 6. "
        "Acted on: you bought on Mon Nov 9. Ended the week -1.3%.")
    for missing in (None, float("nan")):
        row = _row(end_week_pct=missing)
        assert "Ended the week" not in dec.entry_signal_lines(_entry([row]))[1]


def test_line_not_acted_one_call_and_many_calls():
    one = _row(status="not_acted", window_end="2026-11-04", call_dates=["2026-11-02"],
               matched_trade_date=None, matched_trade_id=None)
    assert dec.entry_signal_lines(_entry([one]))[1] == (
        "• NVDA: Enter Now call on Mon Nov 2. Not acted on: no buy on the call "
        "day or the 2 trading days after it (window ended Wed Nov 4).")
    many = _row(status="not_acted", window_end="2026-11-13", times_surfaced=3,
                call_dates=["2026-11-09", "2026-11-10", "2026-11-11"],
                rec_types=["add_winner", "new_pick"], conviction="Buy")
    assert dec.entry_signal_lines(_entry([many]))[1] == (
        "• NVDA: New position + Add to winner call (Buy) surfaced 3× this week, "
        "last on Wed Nov 11. Not acted on: no buy within the decision window of any "
        "of its 3 calls (last window ended Fri Nov 13).")


def test_line_one_trading_day_wording():
    one = _row(status="not_acted", window_end="2026-11-09", call_dates=["2026-11-06"])
    assert "the call day or the 1 trading day after it" in \
        dec.entry_signal_lines(_entry([one], window_trading_days=1))[1]


def test_line_still_open_and_unknown_reasons():
    row = _row(status="still_open", window_end="2026-11-10")
    assert dec.entry_signal_lines(_entry([row]))[1] == (
        "• NVDA: Enter Now call on Fri Nov 6. Still open (decision window ends "
        "Tue Nov 10); next week's debrief says whether you acted.")
    texts = {
        "trades_unavailable": "the trade journal couldn't be read",
        "trades_unreadable": "the trade journal couldn't be read",
        "trade_date_unreadable": "a buy of this name has a date that couldn't be read",
        "no_call_date": "the call has no readable date",
    }
    for reason, text in texts.items():
        row = _row(status="unknown", reason=reason)
        assert dec.entry_signal_lines(_entry([row]))[1] == (
            f"• NVDA: Enter Now call on Fri Nov 6. Not judged: {text}.")


def test_line_carried_not_acted_and_not_judged():
    nac = _row(status="not_acted", window_end="2026-11-10", carried_from="still_open")
    assert dec.entry_signal_lines(_entry(carried=[nac])) [-1] == (
        "• NVDA: Enter Now call on Fri Nov 6 (still open at last week's end). "
        "Not acted on: no buy on the call day or the 2 trading days after it "
        "(window ended Tue Nov 10).")
    unk = _row(status="unknown", reason="trade_date_unreadable", carried_from="unknown")
    assert dec.entry_signal_lines(_entry(carried=[unk]))[-1] == (
        "• NVDA: Enter Now call on Fri Nov 6 (its decision window ran into this week). "
        "Not judged: a buy of this name has a date that couldn't be read.")
    # a row with no carried_from stays neutral rather than claiming "still open"
    bare = _row(status="unknown", reason="trade_date_unreadable")
    assert "still open" not in dec.entry_signal_lines(_entry(carried=[bare]))[-1]
    # carried rows never show a week-end move
    acted = _row(end_week_pct=5.0)
    assert "Ended the week" not in dec.entry_signal_lines(_entry(carried=[acted]))[-1]


def test_lines_with_no_plain_text_markdown_characters():
    rows = [
        _row(conviction="Strong Buy", verdict="Confirmed", end_week_pct=3.0),
        _row(ticker="AMD", status="not_acted", times_surfaced=2,
             call_dates=["2026-11-02", "2026-11-03"], window_end="2026-11-05"),
        _row(ticker="MSFT", status="still_open"),
        _row(ticker="ORCL", status="unknown", reason="trades_unavailable"),
    ]
    lines = dec.entry_signal_lines(_entry(rows, carried=rows[:2], n_undated=2))
    assert lines[0] == "Entry signals this week:"
    assert "Carried over from last week:" in lines
    for ln in lines:
        for bad in ("**", "*", "_", "$"):
            assert bad not in ln, (bad, ln)
        assert ln.startswith("• ") or ln.endswith(":")


def test_undated_with_no_calls_does_not_claim_a_quiet_week():
    lines = dec.entry_signal_lines(_entry(n_undated=1))
    assert lines == ["Entry signals this week:",
                     "• 1 entry signal row(s) had no readable date and are not shown."]


def test_lines_empty_for_unavailable_or_junk():
    assert dec.entry_signal_lines(_entry(recs_status="unavailable")) == []
    assert dec.entry_signal_lines(None) == []


def test_lines_through_the_email_section_and_report_markdown():
    from stock_analyzer.notify import _email_section
    from stock_analyzer.report_display import report_markdown
    out = week(NVDA_RECS, NVDA_BUY, "2026-11-09", "2026-11-15")
    lines = dec.entry_signal_lines(out)
    n_bullets = sum(1 for ln in lines if ln.startswith("• "))
    html = _email_section("Decisions you made", "\n".join(lines), {})
    assert html.count("<li") == n_bullets == 2
    assert "**" not in html
    assert "Carried over from last week:" in html
    md = report_markdown("\n".join(lines))
    assert md.count("\n- ") == n_bullets
    assert "**" not in md
    # the header lines stay prose: not turned into list items
    assert "- Entry signals" not in md and "- Carried over" not in md


# ── 11. acted_rule_caption ──────────────────────────────────────────────────

def test_acted_rule_caption():
    for current in (2, 3, "2", 2.0):
        assert dec.acted_rule_caption(current) is None
    older = dec.acted_rule_caption(None)
    assert older is not None
    for old in (float("nan"), 1, "1", 0, "garbage", pd.NA):
        assert dec.acted_rule_caption(old) == older
    assert "older acted rule" in older
    assert "within 2 trading days" in older
    assert "Enter Now calls weren't listed" in older


# ── 12. behavioural_rows ────────────────────────────────────────────────────

def _hist(*rows):
    return pd.DataFrame(list(rows))


def _h(id_, ticker, rec_date, rec_type="new_pick", mom=50.0, comp=70.0, **kw):
    r = {"id": id_, "ticker": ticker, "rec_date": rec_date, "rec_type": rec_type,
         "momentum_score": mom, "composite_score": comp, "surfaced_at": f"{rec_date}T13:00:00"}
    r.update(kw)
    return r


def test_behavioural_rows_arms_and_drops():
    hist = _hist(
        _h(1, "ACT", "2026-11-02", mom=60.0, comp=80.0),
        _h(2, "MISS", "2026-11-02", mom=40.0, comp=55.0),
        _h(3, "SELLO", "2026-11-02"),                           # only a SELL: not acted
        _h(4, "OPEN", "2026-11-06"),                            # window still open at cutoff
        _h(5, "UNK", "2026-11-02"),                             # unreadable trade date
        _h(6, "DUP", "2026-11-02", "enter_now", already_held=False),
        _h(7, "DUP", "2026-11-02", "new_pick"),                 # same name + day, one buy
        _h(8, "FUT", "2026-11-09"),                             # after the cutoff
        _h(9, "CAND", "2026-11-02", "buy_candidate"),
        _h(10, "HELD", "2026-11-02", "enter_now", already_held=True),
    )
    t = trades(
        trade(1, "ACT", "BUY", "2026-11-03T15:00:00+00:00"),
        trade(2, "SELLO", "SELL", "2026-11-03T15:00:00+00:00"),
        trade(3, "UNK", "BUY", "not a date"),
        trade(4, "DUP", "BUY", "2026-11-03T15:00:00+00:00"),
        trade(5, "CAND", "BUY", "2026-11-02T15:00:00+00:00"),
        trade(6, "HELD", "BUY", "2026-11-02T15:00:00+00:00"),
    )
    rows = dec.behavioural_rows(hist, t, cutoff=D("2026-11-08"))
    assert rows is not None
    assert {"momentum_score", "composite_score", "acted_on"} == set(rows[0])
    # ACT (True), MISS (False), SELLO (False), DUP: one acted, loser deduped away
    assert sorted((r["acted_on"], r["momentum_score"], r["composite_score"]) for r in rows) \
        == sorted([(True, 60.0, 80.0), (False, 40.0, 55.0), (False, 50.0, 70.0),
                   (True, 50.0, 70.0)])
    assert sum(1 for r in rows if r["acted_on"]) == 2


def test_behavioural_rows_none_when_unreadable():
    hist = _hist(_h(1, "ACT", "2026-11-02"))
    t = trades(trade(1, "ACT", "BUY", "2026-11-03T15:00:00+00:00"))
    assert dec.behavioural_rows(hist, None, cutoff=D("2026-11-08")) is None
    assert dec.behavioural_rows(None, t, cutoff=D("2026-11-08")) is None
    assert dec.behavioural_rows(pd.DataFrame({"x": [1]}), t, cutoff=D("2026-11-08")) is None
    assert dec.behavioural_rows(pd.DataFrame(), t, cutoff=D("2026-11-08")) == []


def test_behavioural_rows_pd_na_id():
    hist = pd.DataFrame({
        "id": pd.array([pd.NA, 2], dtype="Int64"), "ticker": ["AAA", "BBB"],
        "rec_date": ["2026-11-02", "2026-11-02"], "rec_type": ["new_pick", "new_pick"],
        "momentum_score": [1.0, 2.0], "composite_score": [3.0, 4.0],
    })
    t = trades(trade(1, "AAA", "BUY", "2026-11-03T15:00:00+00:00"))
    rows = dec.behavioural_rows(hist, t, cutoff=D("2026-11-08"))
    assert [r["acted_on"] for r in rows] == [True, False]


# ── 13. drop_ai_entry_bullets ───────────────────────────────────────────────

AI_TEXT = "\n".join([
    "You made two trades this week.",
    "• NVDA: you bought after the call.",
    "• **AMD**: surfaced and passed.",
    "- NVDA -- a dash bullet.",
    "* **NVDA** — star bullet.",
    "• TSLA: a TRIM signal fired and you held.",
    "• AAPL: closed this week.",
    "• NVDAX: a different name.",
    "• XYZ: another name.",
    "NVDA was the headline of the week.",
    "• Both lists: NVDA mid-line only.",
])


def test_drop_ai_entry_bullets():
    out = dec.drop_ai_entry_bullets(AI_TEXT, ["NVDA", "AMD", "TSLA"], ["TSLA", "AAPL"])
    lines = out.split("\n")
    assert "You made two trades this week." in lines
    assert not any("dash bullet" in ln or "star bullet" in ln or "surfaced and passed" in ln
                   for ln in lines)
    # kept: protective / closed names, other names, prose, non-leading mentions
    assert "• TSLA: a TRIM signal fired and you held." in lines
    assert "• AAPL: closed this week." in lines
    assert "• NVDAX: a different name." in lines
    assert "• XYZ: another name." in lines
    assert "NVDA was the headline of the week." in lines
    assert "• Both lists: NVDA mid-line only." in lines


def test_drop_ai_entry_bullets_edges():
    assert dec.drop_ai_entry_bullets(None, ["NVDA"], []) == ""
    assert dec.drop_ai_entry_bullets("• NVDA: x", [], []) == "• NVDA: x"
    assert dec.drop_ai_entry_bullets("• NVDA: x", ["NVDA"], ["NVDA"]) == "• NVDA: x"
    assert dec.drop_ai_entry_bullets("• NVDA: x\n• AMD: y", ["nvda"], []) == "• AMD: y"
    assert dec.drop_ai_entry_bullets("• BRK-B: x\n• BRK: y", ["BRK-B"], []) == "• BRK: y"


def test_drop_ai_entry_bullets_needs_a_delimiter_after_the_ticker():
    # a one-letter entry ticker must not eat prose that merely starts with it
    text = "\n".join([
        "• A: New position call (Buy) on Mon Nov 2.",
        "• A TRIM signal on TSLA fired; you held.",
        "• A (Buy): another form.",
        "• A - dash form.",
        "• A - dash form.\n• An unrelated bullet.",
    ])
    out = dec.drop_ai_entry_bullets(text, ["A"], ["TSLA"]).split("\n")
    assert out == ["• A TRIM signal on TSLA fired; you held.", "• An unrelated bullet."]
