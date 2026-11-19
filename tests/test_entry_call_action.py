"""Tests for stock_analyzer/entry_call_action.py (the shared entry-call acted rule).

Times are UTC in the fixtures: EDT = UTC-4 until 2026-11-01, EST = UTC-5 after.
"""
from __future__ import annotations

import copy
import random
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import constants
from stock_analyzer import predictive_analytics as pa
from stock_analyzer import entry_call_action as eca
from stock_analyzer.recommendations_history import match_recs_to_trades

COLS = ["id", "ticker", "action", "traded_at", "trigger_type", "notes",
        "broker_txn_id"]


def trade(id_, ticker, action, traded_at, trigger="MANUAL", notes="", broker=None):
    return {"id": id_, "ticker": ticker, "action": action, "traded_at": traded_at,
            "trigger_type": trigger, "notes": notes, "broker_txn_id": broker}


def frame(*rows):
    return pd.DataFrame(list(rows), columns=COLS)


def call(ticker, rec_date, rec_type="new_pick", id_=1, already_held=None):
    return {"id": id_, "ticker": ticker, "rec_date": rec_date,
            "rec_type": rec_type, "already_held": already_held}


def judge(c, trades, cutoff, **kw):
    return eca.classify_call(c, trades, cutoff=cutoff, **kw)


D = date.fromisoformat


# ── 1. pins ─────────────────────────────────────────────────────────────────

def test_pins():
    assert constants.ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS == 2
    assert eca.ACTED_RULE_VERSION == 2
    assert eca.ENTRY_CALL_REC_TYPES == ("new_pick", "add_winner", "enter_now")
    assert eca.CONFIRMING_TRIGGER == "RECOMMENDATION"
    assert pa._advance_trading_days is pa.advance_trading_days


# ── 2-5. window edges ───────────────────────────────────────────────────────

def test_friday_call_monday_buy_acted():
    t = frame(trade(1, "NVDA", "BUY", "2026-11-16T15:00:00+00:00"))
    r = judge(call("NVDA", "2026-11-13"), t, D("2026-11-22"))
    assert r["status"] == "acted"
    assert r["window_end"] == D("2026-11-17")
    assert r["matched_trade_date"] == D("2026-11-16")


def test_window_edge_in_and_out():
    c = call("NVDA", "2026-11-13")
    inside = frame(trade(1, "NVDA", "BUY", "2026-11-17T15:00:00+00:00"))
    outside = frame(trade(2, "NVDA", "BUY", "2026-11-18T15:00:00+00:00"))
    assert judge(c, inside, D("2026-11-22"))["status"] == "acted"
    assert judge(c, outside, D("2026-11-22"))["status"] == "not_acted"


def test_holiday_window():
    c = call("AAPL", "2026-11-25")
    mon = frame(trade(1, "AAPL", "BUY", "2026-11-30T15:00:00+00:00"))
    r = judge(c, mon, D("2026-11-30"))
    assert r["window_end"] == D("2026-11-30")
    assert r["status"] == "acted"
    tue = frame(trade(2, "AAPL", "BUY", "2026-12-01T15:00:00+00:00"))
    assert judge(c, tue, D("2026-12-06"))["status"] == "not_acted"


def test_half_day_is_a_session():
    r = judge(call("AAPL", "2026-11-27"), frame(), D("2026-11-30"))
    assert r["status"] == "still_open"
    assert r["window_end"] == D("2026-12-01")


# ── 6-7. DST and naive ──────────────────────────────────────────────────────

def test_dst_boundaries():
    c = call("AAPL", "2026-10-30")
    cut = D("2026-11-10")
    r = judge(c, frame(trade(1, "AAPL", "BUY", "2026-10-31T00:30:00+00:00")), cut)
    assert r["window_end"] == D("2026-11-03")
    assert r["status"] == "acted"
    assert r["matched_trade_date"] == D("2026-10-30")
    r = judge(c, frame(trade(1, "AAPL", "BUY", "2026-11-04T00:30:00+00:00")), cut)
    assert r["status"] == "acted"
    assert r["matched_trade_date"] == D("2026-11-03")
    r = judge(c, frame(trade(1, "AAPL", "BUY", "2026-11-04T14:45:00+00:00")), cut)
    assert r["status"] == "not_acted"


def test_naive_timestamp_is_utc():
    c = call("AAPL", "2026-10-30")
    r = judge(c, frame(trade(1, "AAPL", "BUY", "2026-11-04 00:30:00")), D("2026-11-10"))
    assert r["status"] == "acted"
    assert r["matched_trade_date"] == D("2026-11-03")


# ── 8-12. ET dating and import provenance ───────────────────────────────────

def test_2030_et_fill_is_next_utc_day_but_in_window():
    c = call("AAPL", "2026-11-12")
    t = frame(trade(1, "AAPL", "BUY", "2026-11-17T01:30:00+00:00"))
    assert judge(c, t, D("2026-11-22"))["status"] == "acted"


def test_2030_et_fill_day_before_not_credited():
    c = call("AAPL", "2026-11-12")
    t = frame(trade(1, "AAPL", "BUY", "2026-11-12T01:30:00+00:00"))
    assert judge(c, t, D("2026-11-22"))["status"] == "not_acted"


def test_midnight_utc_import_acted_raw_and_normalised():
    from stock_analyzer.trade_time import normalize_traded_at
    c = call("AAPL", "2026-11-12")
    raw = frame(trade(1, "AAPL", "BUY", "2026-11-12T00:00:00+00:00",
                      notes="Robinhood import"))
    assert judge(c, raw, D("2026-11-22"))["status"] == "acted"
    pre = normalize_traded_at(raw)
    assert judge(c, pre, D("2026-11-22"))["status"] == "acted"


def test_midnight_utc_without_provenance_is_et_day_before():
    c = call("AAPL", "2026-11-12")
    t = frame(trade(1, "AAPL", "BUY", "2026-11-12T00:00:00+00:00"))
    assert judge(c, t, D("2026-11-22"))["status"] == "not_acted"


def test_midnight_utc_with_broker_id_acted():
    c = call("AAPL", "2026-11-12")
    t = frame(trade(1, "AAPL", "BUY", "2026-11-12T00:00:00+00:00", broker="abc"))
    assert judge(c, t, D("2026-11-22"))["status"] == "acted"


# ── 13-14. direction ────────────────────────────────────────────────────────

def test_sell_never_credits():
    c = call("MRVL", "2026-11-06")
    t = frame(trade(1, "MRVL", "SELL", "2026-11-06T15:00:00+00:00", "RECOMMENDATION"))
    assert judge(c, t, D("2026-11-08"))["status"] == "still_open"
    assert judge(c, t, D("2026-11-15"))["status"] == "not_acted"


def test_earlier_buy_never_credits():
    c = call("MRVL", "2026-11-06")
    t = frame(trade(1, "MRVL", "BUY", "2026-11-05T15:00:00+00:00"))
    assert judge(c, t, D("2026-11-15"))["status"] == "not_acted"


# ── 15-17. cut-off ──────────────────────────────────────────────────────────

def test_cutoff_with_buy_after():
    c = call("MRVL", "2026-11-06")
    t = frame(trade(1, "MRVL", "BUY", "2026-11-09T15:00:00+00:00"))
    r = judge(c, t, D("2026-11-08"))
    assert r["status"] == "still_open"
    assert r["window_end"] == D("2026-11-10")
    assert judge(c, t, D("2026-11-09"))["status"] == "acted"
    assert judge(c, t, D("2026-11-15"))["status"] == "acted"


def test_cutoff_no_buy():
    c = call("MRVL", "2026-11-06")
    assert judge(c, frame(), D("2026-11-09"))["status"] == "still_open"
    assert judge(c, frame(), D("2026-11-10"))["status"] == "not_acted"


def test_regenerate_is_stable():
    calls = [call("AAPL", "2026-11-27", id_=1), call("MSFT", "2026-11-23", id_=2),
             call("NVDA", "2026-11-24", "enter_now", id_=3, already_held=False)]
    rows = [trade(1, "AAPL", "BUY", "2026-12-01T15:00:00+00:00"),
            trade(2, "MSFT", "BUY", "2026-11-24T15:00:00+00:00"),
            trade(3, "NVDA", "BUY", "2026-11-30T15:00:00+00:00"),
            trade(4, "MSFT", "SELL", "2026-11-25T15:00:00+00:00")]
    cut = D("2026-11-30")
    full = eca.classify_calls(calls, frame(*rows), cutoff=cut)
    assert full[0]["status"] == "still_open"
    # Trades after the cut-off are ignored: filtering them changes nothing.
    kept = [r for r in rows if r["traded_at"][:10] <= "2026-11-30"]
    assert eca.classify_calls(calls, frame(*kept), cutoff=cut) == full
    # Row order of both inputs is irrelevant.
    sc, sr = calls[:], rows[:]
    random.Random(7).shuffle(sc)
    random.Random(8).shuffle(sr)
    shuffled = eca.classify_calls(sc, frame(*sr), cutoff=cut)
    assert sorted(shuffled, key=lambda r: r["id"]) == sorted(full, key=lambda r: r["id"])


# ── 18-19. unknown and excluded ─────────────────────────────────────────────

def test_call_after_cutoff_is_unknown():
    r = judge(call("AAPL", "2026-12-02"), frame(), D("2026-11-30"))
    assert (r["status"], r["reason"]) == ("unknown", "call_after_cutoff")


def test_no_call_date_unknown():
    for bad in (None, float("nan"), pd.NaT, "", "garbage"):
        r = judge(call("AAPL", bad), frame(), D("2026-11-30"))
        assert (r["status"], r["reason"]) == ("unknown", "no_call_date"), bad
        assert r["window_end"] is None


def test_rec_date_accepts_date_datetime_timestamp():
    for v in (D("2026-11-06"), pd.Timestamp("2026-11-06"),
              pd.Timestamp("2026-11-06 09:30", tz="America/New_York"),
              "2026-11-06T09:30:00"):
        assert judge(call("AAPL", v), frame(), D("2026-11-30"))["rec_date"] == D("2026-11-06")


@pytest.mark.parametrize("held,expected", [
    (True, "excluded"), ("true", "excluded"), (1, "excluded"),
    (None, "not_acted"), (float("nan"), "not_acted"), (False, "not_acted"),
])
def test_already_held_enter_now(held, expected):
    r = judge(call("NVDA", "2026-11-02", "enter_now", already_held=held),
              frame(), D("2026-11-30"))
    assert r["status"] == expected
    if expected == "excluded":
        assert r["reason"] == "already_held"


def test_already_held_only_matters_for_enter_now():
    r = judge(call("NVDA", "2026-11-02", "new_pick", already_held=True),
              frame(), D("2026-11-30"))
    assert r["status"] == "not_acted"


@pytest.mark.parametrize("rt", ["buy_candidate", "overflow_pick", "", "weird"])
def test_other_types_excluded(rt):
    r = judge(call("NVDA", "2026-11-02", rt), frame(), D("2026-11-30"))
    assert (r["status"], r["reason"]) == ("excluded", "not_entry_call_type")


def test_add_winner_is_judged():
    t = frame(trade(1, "NVDA", "BUY", "2026-11-03T15:00:00+00:00"))
    assert judge(call("NVDA", "2026-11-02", "add_winner"), t,
                 D("2026-11-30"))["status"] == "acted"


# ── 20-22. offline / empty / unreadable ─────────────────────────────────────

def test_offline_everything_unknown():
    calls = [call("AAPL", "2026-11-02", id_=1),
             call("MSFT", "2026-11-03", "add_winner", id_=2)]
    res = eca.classify_calls(calls, None, cutoff=D("2026-11-30"))
    assert [(r["status"], r["reason"]) for r in res] == [
        ("unknown", "trades_unavailable")] * 2
    assert [p["status"] for p in eca.per_name(res)] == ["unknown", "unknown"]
    recs = pd.DataFrame([{"id": 1, "ticker": "AAPL", "rec_date": "2026-11-02",
                          "rec_type": "new_pick"}])
    matched = match_recs_to_trades(recs, frame())
    assert eca.apply_to_matched(matched, recs, None, cutoff=D("2026-11-30")) is None
    assert eca.apply_to_matched(matched, None, frame(), cutoff=D("2026-11-30")) is None


@pytest.mark.parametrize("empty", [pd.DataFrame(columns=COLS), pd.DataFrame()])
def test_empty_journal_is_judged(empty):
    c = call("AAPL", "2026-11-02")
    assert judge(c, empty, D("2026-11-30"))["status"] == "not_acted"
    assert judge(call("AAPL", "2026-11-27"), empty, D("2026-11-30"))["status"] == "still_open"


def test_unreadable_frames():
    no_traded_at = pd.DataFrame([{"id": 1, "ticker": "AAPL", "action": "BUY"}])
    r = judge(call("AAPL", "2026-11-02"), no_traded_at, D("2026-11-30"))
    assert (r["status"], r["reason"]) == ("unknown", "trades_unreadable")
    garbage = frame(trade(1, "AAPL", "BUY", "garbage"))
    r = judge(call("AAPL", "2026-11-02"), garbage, D("2026-11-30"))
    assert (r["status"], r["reason"]) == ("unknown", "trade_date_unreadable")


def test_unreadable_buy_does_not_block_a_real_match_or_other_tickers():
    t = frame(trade(1, "AAPL", "BUY", "garbage"),
              trade(2, "AAPL", "BUY", "2026-11-03T15:00:00+00:00"))
    assert judge(call("AAPL", "2026-11-02"), t, D("2026-11-30"))["status"] == "acted"
    t = frame(trade(1, "MSFT", "BUY", "garbage"))
    assert judge(call("AAPL", "2026-11-02"), t, D("2026-11-30"))["status"] == "not_acted"


# ── 23-24. legacy weekend date, ticker, earliest ────────────────────────────

def test_weekend_legacy_rec_date():
    c = call("AAPL", "2026-10-03")
    mon = frame(trade(1, "AAPL", "BUY", "2026-10-05T15:00:00+00:00"))
    r = judge(c, mon, D("2026-10-31"))
    assert r["window_end"] == D("2026-10-06")
    assert r["status"] == "acted"
    wed = frame(trade(1, "AAPL", "BUY", "2026-10-07T15:00:00+00:00"))
    assert judge(c, wed, D("2026-10-31"))["status"] == "not_acted"
    fri = frame(trade(1, "AAPL", "BUY", "2026-10-02T15:00:00+00:00"))
    assert judge(c, fri, D("2026-10-31"))["status"] == "not_acted"


def test_ticker_match_and_other_ticker():
    t = frame(trade(1, " mrvl ", "BUY", "2026-11-03T15:00:00+00:00"),
              trade(2, "AAPL", "BUY", "2026-11-03T15:00:00+00:00"))
    assert judge(call("MRVL", "2026-11-02"), t, D("2026-11-30"))["status"] == "acted"
    t = frame(trade(2, "AAPL", "BUY", "2026-11-03T15:00:00+00:00"))
    assert judge(call("MRVL", "2026-11-02"), t, D("2026-11-30"))["status"] == "not_acted"


def test_earliest_timestamp_then_lowest_id():
    c = call("MRVL", "2026-11-02")
    t = frame(trade(9, "MRVL", "BUY", "2026-11-03T15:00:00+00:00"),
              trade(5, "MRVL", "BUY", "2026-11-03T14:00:00+00:00"))
    assert judge(c, t, D("2026-11-30"))["matched_trade"]["id"] == 5
    t = frame(trade(9, "MRVL", "BUY", "2026-11-03T15:00:00+00:00"),
              trade(5, "MRVL", "BUY", "2026-11-03T15:00:00+00:00"))
    assert judge(c, t, D("2026-11-30"))["matched_trade"]["id"] == 5


def test_confirmed_flag():
    c = call("MRVL", "2026-11-02")
    t = frame(trade(1, "MRVL", "BUY", "2026-11-03T15:00:00+00:00", " recommendation "))
    assert judge(c, t, D("2026-11-30"))["confirmed"] is True
    t = frame(trade(1, "MRVL", "BUY", "2026-11-03T15:00:00+00:00", "MANUAL"))
    r = judge(c, t, D("2026-11-30"))
    assert r["confirmed"] is False
    assert r["status"] == "acted"


# ── 25. per_name ────────────────────────────────────────────────────────────

def test_per_name_precedence_and_shape():
    cut = D("2026-11-08")
    calls = [
        call("BBB", "2026-11-02", id_=1), call("BBB", "2026-11-06", id_=2),
        call("AAA", "2026-11-02", id_=3), call("AAA", "2026-11-06", id_=4),
        call("CCC", "2026-11-02", id_=5), call("CCC", "2026-11-20", id_=6),
        call("DDD", "2026-11-02", id_=7),
        call("EEE", "2026-11-02", "buy_candidate", id_=8),
    ]
    t = frame(trade(1, "AAA", "BUY", "2026-11-06T15:00:00+00:00", "RECOMMENDATION"))
    res = eca.classify_calls(calls, t, cutoff=cut)
    names = eca.per_name(res)
    assert [n["ticker"] for n in names] == ["AAA", "BBB", "CCC", "DDD"]
    by = {n["ticker"]: n for n in names}
    assert by["BBB"]["status"] == "still_open"
    assert by["BBB"]["window_end"] == D("2026-11-10")
    assert by["AAA"]["status"] == "acted"
    assert by["AAA"]["credited_call_date"] == D("2026-11-06")
    assert by["AAA"]["matched_trade_id"] == 1
    assert by["AAA"]["matched_trade_date"] == D("2026-11-06")
    assert by["AAA"]["confirmed"] is True
    assert by["AAA"]["window_end"] == D("2026-11-10")
    assert by["AAA"]["n_calls"] == 2
    assert by["AAA"]["first_call_date"] == D("2026-11-02")
    assert by["AAA"]["last_call_date"] == D("2026-11-06")
    assert by["CCC"]["status"] == "unknown"
    assert by["DDD"]["status"] == "not_acted"
    assert by["DDD"]["credited_call_date"] is None
    assert by["DDD"]["confirmed"] is False
    assert by["DDD"]["rec_types"] == ["new_pick"]
    assert eca.per_name([]) == []


# ── 26-27. apply_to_matched ─────────────────────────────────────────────────

def _recs(*rows):
    return pd.DataFrame(list(rows))


def test_apply_to_matched_shape_and_passthrough():
    recs = _recs(
        {"id": 1, "ticker": "MRVL", "rec_date": "2026-11-06", "rec_type": "new_pick",
         "surfaced_at": "2026-11-06T13:00:00"},
        {"id": 2, "ticker": "AAPL", "rec_date": "2026-11-06", "rec_type": "buy_candidate",
         "surfaced_at": "2026-11-06T13:00:00"},
        {"id": 3, "ticker": "NVDA", "rec_date": "2026-11-06", "rec_type": "enter_now",
         "already_held": True, "surfaced_at": "2026-11-06T13:00:00"},
        {"id": 4, "ticker": "MSFT", "rec_date": "2026-11-02", "rec_type": "add_winner",
         "surfaced_at": "2026-11-02T13:00:00"},
    )
    trades = frame(
        trade(7, "MRVL", "BUY", "2026-11-06T14:30:00+00:00", "RECOMMENDATION"),
        trade(8, "AAPL", "BUY", "2026-11-06T14:30:00+00:00", "RECOMMENDATION"),
    )
    trades["shares"], trades["price"] = [5, 2], [100.0, 200.0]
    matched = match_recs_to_trades(recs, trades)
    before = copy.deepcopy(matched)
    out = eca.apply_to_matched(matched, recs, trades, cutoff=D("2026-11-30"))

    assert matched == before                      # input not mutated
    assert len(out) == len(matched) == 4
    assert [r["id"] for r in out] == [1, 2, 3, 4]
    ref = matched[0]["acted_trade"]
    assert ref is not None
    assert set(out[0]["acted_trade"]) == set(ref)
    assert out[0]["acted_trade"] == ref
    assert out[0]["acted_on"] is True
    assert out[0]["entry_call_status"] == "acted"
    assert out[0]["acted_rule_version"] == 2
    assert out[0]["credit_deduped"] is False
    assert out[0]["entry_call_window_end"] == D("2026-11-10")
    # buy_candidate keeps its old same-day credit, only tagged excluded.
    assert out[1]["entry_call_status"] == "excluded"
    assert out[1]["entry_call_reason"] == "not_entry_call_type"
    assert {k: v for k, v in out[1].items()
            if k not in ("entry_call_status", "entry_call_reason")} == matched[1]
    # already-held enter_now (looked up by id from recs_df) is excluded too.
    assert out[2]["entry_call_status"] == "excluded"
    assert out[2]["entry_call_reason"] == "already_held"
    # add_winner with no BUY is judged, not acted.
    assert out[3]["entry_call_status"] == "not_acted"
    assert out[3]["acted_on"] is False and out[3]["acted_trade"] is None


def test_apply_to_matched_credit_dedup_same_ticker_and_date():
    recs = _recs(
        {"id": 1, "ticker": "MRVL", "rec_date": "2026-11-06", "rec_type": "enter_now",
         "already_held": False, "surfaced_at": "2026-11-06T13:00:00"},
        {"id": 2, "ticker": "MRVL", "rec_date": "2026-11-06", "rec_type": "new_pick",
         "surfaced_at": "2026-11-06T13:00:00"},
    )
    trades = frame(trade(7, "MRVL", "BUY", "2026-11-09T15:00:00+00:00"))
    out = eca.apply_to_matched(match_recs_to_trades(recs, trades), recs, trades,
                               cutoff=D("2026-11-30"))
    by_type = {r["rec_type"]: r for r in out}
    win, lose = by_type["new_pick"], by_type["enter_now"]      # A4 rank: new_pick wins
    assert win["acted_on"] is True and win["acted_trade"]["id"] == 7
    assert win["credit_deduped"] is False
    assert lose["entry_call_status"] == "acted"
    assert lose["acted_on"] is False and lose["acted_trade"] is None
    assert lose["credit_deduped"] is True


def test_apply_to_matched_different_dates_both_credited():
    recs = _recs(
        {"id": 1, "ticker": "MRVL", "rec_date": "2026-10-06", "rec_type": "new_pick"},
        {"id": 2, "ticker": "MRVL", "rec_date": "2026-10-07", "rec_type": "new_pick"},
    )
    trades = frame(trade(7, "MRVL", "BUY", "2026-10-08T15:00:00+00:00"))
    out = eca.apply_to_matched(match_recs_to_trades(recs, trades), recs, trades,
                               cutoff=D("2026-10-31"))
    assert [r["acted_on"] for r in out] == [True, True]
    assert [r["credit_deduped"] for r in out] == [False, False]


def test_apply_to_matched_missing_already_held_column_is_judged():
    recs = _recs({"id": 1, "ticker": "NVDA", "rec_date": "2026-11-02",
                  "rec_type": "enter_now"})
    out = eca.apply_to_matched(match_recs_to_trades(recs, frame()), recs, frame(),
                               cutoff=D("2026-11-30"))
    assert out[0]["entry_call_status"] == "not_acted"


def test_apply_to_matched_na_id_reads_not_found_not_a_crash():
    """An Int64 recs id of pd.NA used to raise inside the already_held lookup
    (`rid == rid` is NA, and bool(NA) raises). It now reads "not found" = judged."""
    recs = pd.DataFrame({
        "id": pd.array([1, pd.NA], dtype="Int64"),
        "already_held": [True, False],
    })
    matched = [
        {"id": 1, "ticker": "NVDA", "rec_date": "2026-11-06", "rec_type": "enter_now"},
        {"id": pd.NA, "ticker": "AMD", "rec_date": "2026-11-06", "rec_type": "enter_now"},
    ]
    out = eca.apply_to_matched(matched, recs, frame(), cutoff=D("2026-11-30"))
    assert out[0]["entry_call_status"] == "excluded"      # id 1 found: already held
    assert out[0]["entry_call_reason"] == "already_held"
    assert out[1]["entry_call_status"] == "not_acted"     # NA id: not found, judged


# ── L1-L3. live MRVL / NVDA cases ───────────────────────────────────────────

def _mrvl_october():
    return frame(
        trade(424, "MRVL", "BUY", "2026-10-13T15:00:00+00:00", "MANUAL"),
        trade(419, "MRVL", "BUY", "2026-10-14T15:00:00+00:00", "DIVERSIFY_ADD"),
        trade(495, "MRVL", "BUY", "2026-10-15T15:00:00+00:00", "DIVERSIFY_ADD"),
        trade(435, "MRVL", "SELL", "2026-10-20T15:00:00+00:00", "RECOMMENDATION"),
    )


def test_live_mrvl_october():
    calls = [call("MRVL", "2026-10-06", id_=1), call("MRVL", "2026-10-07", id_=2)]
    cut = D("2026-10-31")
    res = eca.classify_calls(calls, _mrvl_october(), cutoff=cut)
    assert [r["status"] for r in res] == ["not_acted", "not_acted"]
    assert eca.per_name(res)[0]["status"] == "not_acted"
    res3 = eca.classify_calls(calls, _mrvl_october(), cutoff=cut, window_trading_days=3)
    assert [r["status"] for r in res3] == ["not_acted", "not_acted"]
    res4 = eca.classify_calls(calls, _mrvl_october(), cutoff=cut, window_trading_days=4)
    assert [r["status"] for r in res4] == ["not_acted", "acted"]
    assert res4[1]["matched_trade"]["id"] == 424
    assert res4[1]["confirmed"] is False
    assert eca.per_name(res4)[0]["status"] == "acted"


def test_live_mrvl_week_of_nov_8():
    c = call("MRVL", "2026-11-06")
    base = [trade(454, "MRVL", "BUY", "2026-10-30T15:00:00+00:00", "MANUAL"),
            trade(464, "MRVL", "SELL", "2026-11-02T15:00:00+00:00", "MANUAL")]
    full = frame(*base,
                 trade(479, "MRVL", "BUY", "2026-11-06T14:30:00+00:00", "RECOMMENDATION"),
                 trade(480, "MRVL", "BUY", "2026-11-06T14:35:00+00:00", "MANUAL"))
    r = judge(c, full, D("2026-11-08"))
    assert r["status"] == "acted"
    assert r["matched_trade"]["id"] == 479
    assert r["confirmed"] is True
    r = judge(c, frame(*base), D("2026-11-08"))
    assert r["status"] == "still_open"


def test_live_nvda_friday_call_monday_buy():
    c = call("NVDA", "2026-11-06", "enter_now", already_held=False)
    t = frame(trade(1, "NVDA", "BUY", "2026-11-09T15:00:00+00:00"))
    r = judge(c, t, D("2026-11-08"))
    assert r["status"] == "still_open"
    assert r["window_end"] == D("2026-11-10")
    assert judge(c, t, D("2026-11-15"))["status"] == "acted"
    assert judge(c, t, D("2026-11-15"), window_trading_days=0)["status"] == "not_acted"
    assert judge(c, t, D("2026-11-15"), window_trading_days=1)["status"] == "acted"


# ── arm(): the C4 caller contract as one function ───────────────────────────

@pytest.mark.parametrize("row,expected", [
    ({"entry_call_status": "acted", "credit_deduped": False}, "acted"),
    ({"entry_call_status": "acted"}, "acted"),                 # flag absent = not deduped
    ({"entry_call_status": "acted", "credit_deduped": True}, None),
    ({"entry_call_status": "not_acted", "credit_deduped": False}, "passed"),
    ({"entry_call_status": "not_acted"}, "passed"),
    ({"entry_call_status": "still_open"}, None),
    ({"entry_call_status": "unknown"}, None),
    ({"entry_call_status": "excluded"}, None),
    ({}, None),
    ({"entry_call_status": None}, None),
])
def test_arm_by_status(row, expected):
    assert eca.arm(row) == expected


def test_arm_never_splits_on_acted_on_or_rec_type():
    # acted_on False on a still-open row, and an excluded row that kept the OLD
    # same-day credit (acted_on True): neither is an arm.
    assert eca.arm({"entry_call_status": "still_open", "acted_on": False,
                    "rec_type": "new_pick"}) is None
    assert eca.arm({"entry_call_status": "excluded", "acted_on": True,
                    "rec_type": "enter_now"}) is None
    # a not_acted row with acted_on True would still be an arm by status
    assert eca.arm({"entry_call_status": "not_acted", "acted_on": True}) == "passed"


def test_arm_on_real_apply_to_matched_output():
    recs = _recs(
        {"id": 1, "ticker": "MRVL", "rec_date": "2026-11-06", "rec_type": "enter_now",
         "already_held": False, "surfaced_at": "2026-11-06T13:00:00"},
        {"id": 2, "ticker": "MRVL", "rec_date": "2026-11-06", "rec_type": "new_pick",
         "surfaced_at": "2026-11-06T13:00:00"},
        {"id": 3, "ticker": "AAPL", "rec_date": "2026-11-06", "rec_type": "buy_candidate"},
        {"id": 4, "ticker": "AMD", "rec_date": "2026-11-06", "rec_type": "new_pick"},
        {"id": 5, "ticker": "TSLA", "rec_date": "2026-11-30", "rec_type": "new_pick"},
    )
    trades = frame(trade(7, "MRVL", "BUY", "2026-11-09T15:00:00+00:00"))
    out = eca.apply_to_matched(match_recs_to_trades(recs, trades), recs, trades,
                               cutoff=D("2026-11-30"))
    assert [eca.arm(r) for r in out] == [None, "acted", None, "passed", None]
