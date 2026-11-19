"""Tests for stock_analyzer/monthly_entry_calls.py (C4a: the monthly Decision
Review judged on the shared entry-call rule). Spec: docs/plans/acted-rule-c4-spec.md.

Times are UTC in the fixtures: EDT = UTC-4 until 2026-11-01, EST = UTC-5 after.
November 2026: Thu 11-26 is Thanksgiving (closed), Fri 11-27 is a half day (open).
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import constants
from stock_analyzer import entry_call_action as eca
from stock_analyzer import monthly_entry_calls as mec
from stock_analyzer import recommendations_history as rh

D = date.fromisoformat
NOV = (D("2026-11-01"), D("2026-11-30"))
OCT = (D("2026-10-01"), D("2026-10-31"))
DEC = (D("2026-12-01"), D("2026-12-31"))

TRADE_COLS = ["id", "ticker", "action", "traded_at", "trigger_type", "notes",
              "broker_txn_id", "shares", "price"]


def rec(id_, ticker, rec_date, rec_type="new_pick", price=100.0, comp=70,
        verdict="Engine-Confirmed", already_held=None):
    return {"id": id_, "ticker": ticker, "rec_date": rec_date, "rec_type": rec_type,
            "surfaced_at": f"{rec_date}T14:00:00", "price_at_surface": price,
            "composite_score": comp, "verdict": verdict,
            "already_held": already_held}


def recs_of(*rows):
    return pd.DataFrame(list(rows))


def trade(id_, ticker, action, traded_at, trigger="MANUAL", notes="", broker=None,
          shares=10, price=100.0):
    return {"id": id_, "ticker": ticker, "action": action, "traded_at": traded_at,
            "trigger_type": trigger, "notes": notes, "broker_txn_id": broker,
            "shares": shares, "price": price}


def trades_of(*rows):
    return pd.DataFrame(list(rows), columns=TRADE_COLS)


def month(recs, trades, period=NOV, as_of=None, prices=None, spy=None,
          min_days=0, **kw):
    return mec.build_month(
        recs, trades, period_start=period[0], period_end=period[1],
        as_of=as_of or period[1], current_prices=prices or {},
        spy_close_by_date=spy, min_days=min_days, **kw)


def status_of(res, ticker):
    return next(n["status"] for n in res["names"] if n["ticker"] == ticker)


def _cell(res, ticker):
    return next(n for n in res["names"] if n["ticker"] == ticker)


# ── 5. time-context matrix ──────────────────────────────────────────────────

def test_thanksgiving_tue_call_friday_half_day_buy_acted():
    t = trades_of(trade(1, "NVDA", "BUY", "2026-11-27T15:00:00+00:00"))
    res = month(recs_of(rec(1, "NVDA", "2026-11-24")), t, as_of=D("2026-12-06"))
    assert status_of(res, "NVDA") == "acted"


def test_thanksgiving_wed_call_monday_buy_acted_and_no_buy_not_acted():
    r = recs_of(rec(1, "NVDA", "2026-11-25"))
    t = trades_of(trade(1, "NVDA", "BUY", "2026-11-30T15:00:00+00:00"))
    assert status_of(month(r, t, as_of=D("2026-12-06")), "NVDA") == "acted"
    res = month(r, trades_of(), as_of=D("2026-12-06"))
    assert status_of(res, "NVDA") == "not_acted"          # window ends 11-30 = cutoff
    assert _cell(res, "NVDA")["window_end"] == "2026-11-30"


def test_friday_half_day_call_is_still_open_even_with_a_dec_1_buy():
    t = trades_of(trade(1, "NVDA", "BUY", "2026-12-01T15:00:00+00:00"))
    res = month(recs_of(rec(1, "NVDA", "2026-11-27")), t, as_of=D("2026-12-06"))
    assert status_of(res, "NVDA") == "still_open"
    assert _cell(res, "NVDA")["window_end"] == "2026-12-01"
    assert res["counts"]["n_open"] == 1
    assert res["counts"]["n_acted"] == 0 and res["counts"]["n_not_acted"] == 0


def test_monday_nov_30_call_is_still_open():
    res = month(recs_of(rec(1, "AMD", "2026-11-30")), trades_of(), as_of=D("2026-12-06"))
    assert status_of(res, "AMD") == "still_open"
    assert _cell(res, "AMD")["window_end"] == "2026-12-02"


def test_year_end_thursday_call_is_open_in_december_generated_jan_3():
    res = month(recs_of(rec(1, "AMD", "2026-12-31")), trades_of(), period=DEC,
                as_of=D("2027-01-03"))
    assert status_of(res, "AMD") == "still_open"
    assert _cell(res, "AMD")["window_end"] == "2027-01-05"


def test_dst_friday_oct_30_call_open_in_october_and_never_in_november():
    r = recs_of(rec(1, "AMD", "2026-10-30"))
    oct_res = month(r, trades_of(), period=OCT, as_of=D("2026-11-08"))
    assert status_of(oct_res, "AMD") == "still_open"
    assert _cell(oct_res, "AMD")["window_end"] == "2026-11-03"
    nov_res = month(r, trades_of(), period=NOV, as_of=D("2026-12-06"))
    assert nov_res["names"] == []
    assert nov_res["counts"]["n_surfaced"] == 0


def test_after_hours_et_buy_dates_on_its_et_day():
    # Tue 11-03 call: window ends Thu 11-05. A 20:30 ET Thu 11-05 BUY is
    # 2026-11-06T01:30Z, whose UTC date is OUTSIDE the window.
    t = trades_of(trade(1, "AAPL", "BUY", "2026-11-06T01:30:00+00:00"))
    res = month(recs_of(rec(1, "AAPL", "2026-11-03")), t)
    assert status_of(res, "AAPL") == "acted"
    # ... and the same fill an ET evening later is outside
    t2 = trades_of(trade(1, "AAPL", "BUY", "2026-11-07T01:30:00+00:00"))
    assert status_of(month(recs_of(rec(1, "AAPL", "2026-11-03")), t2), "AAPL") == "not_acted"


def test_provenance_midnight_utc_import_is_reanchored_to_its_et_day():
    r = recs_of(rec(1, "AAPL", "2026-11-12"))
    imp = trades_of(trade(1, "AAPL", "BUY", "2026-11-12T00:00:00+00:00",
                          notes="Robinhood import", broker="abc"))
    assert status_of(month(r, imp), "AAPL") == "acted"
    manual = trades_of(trade(1, "AAPL", "BUY", "2026-11-12T00:00:00+00:00"))
    assert status_of(month(r, manual), "AAPL") == "not_acted"     # ET day before


def test_buy_logged_late_but_dated_in_window_is_acted():
    t = trades_of(trade(1, "AAPL", "BUY", "2026-11-04T15:00:00+00:00",
                        notes="logged 2026-11-20"))
    assert status_of(month(recs_of(rec(1, "AAPL", "2026-11-03")), t,
                           as_of=D("2026-12-06")), "AAPL") == "acted"


def test_sell_in_window_never_credits():
    t = trades_of(trade(1, "AAPL", "SELL", "2026-11-03T15:00:00+00:00", "RECOMMENDATION"))
    assert status_of(month(recs_of(rec(1, "AAPL", "2026-11-03")), t), "AAPL") == "not_acted"


def test_buy_after_period_end_never_credits():
    # Wed 11-25 call (window ends 11-30); a 12-01 BUY is past both
    t = trades_of(trade(1, "AAPL", "BUY", "2026-12-01T15:00:00+00:00"))
    res = month(recs_of(rec(1, "AAPL", "2026-11-25")), t, as_of=D("2026-12-06"))
    assert status_of(res, "AAPL") == "not_acted"


# ── 5. live cases ───────────────────────────────────────────────────────────

def test_live_mrvl_october_not_acted():
    r = recs_of(rec(1, "MRVL", "2026-10-06"), rec(2, "MRVL", "2026-10-07"))
    t = trades_of(
        trade(424, "MRVL", "BUY", "2026-10-13T15:00:00+00:00"),
        trade(419, "MRVL", "BUY", "2026-10-14T15:00:00+00:00", "DIVERSIFY_ADD"),
        trade(495, "MRVL", "BUY", "2026-10-15T15:00:00+00:00", "DIVERSIFY_ADD"),
        trade(435, "MRVL", "SELL", "2026-10-20T15:00:00+00:00", "RECOMMENDATION"))
    res = month(r, t, period=OCT, as_of=D("2026-11-08"))
    assert status_of(res, "MRVL") == "not_acted"
    assert _cell(res, "MRVL")["n_calls"] == 2
    assert res["counts"]["n_not_acted"] == 1 and res["counts"]["n_acted"] == 0


def test_live_nvda_enter_now_nov_6_acted_in_november():
    r = recs_of(rec(1, "NVDA", "2026-11-06", "enter_now", already_held=False))
    t = trades_of(trade(1, "NVDA", "BUY", "2026-11-09T15:00:00+00:00"))
    res = month(r, t, as_of=D("2026-12-06"))
    assert status_of(res, "NVDA") == "acted"
    assert res["counts"]["n_acted"] == 1


def test_live_mrvl_nov_6_credited_to_479_never_494():
    r = recs_of(rec(1, "MRVL", "2026-11-06"))
    t = trades_of(
        trade(494, "MRVL", "BUY", "2026-11-13T15:00:00+00:00"),
        trade(480, "MRVL", "BUY", "2026-11-09T15:05:00+00:00"),
        trade(479, "MRVL", "BUY", "2026-11-09T14:30:00+00:00", "RECOMMENDATION"))
    res = month(r, t, as_of=D("2026-12-06"))
    assert status_of(res, "MRVL") == "acted"
    assert [row["acted_trade"]["id"] for row in res["judged"]] == [479]


# ── 5. regenerate ───────────────────────────────────────────────────────────

def _regen_inputs():
    r = recs_of(rec(1, "NVDA", "2026-11-03"), rec(2, "AMD", "2026-11-03", comp=60),
                rec(3, "XYZ", "2026-11-30"))
    t = trades_of(trade(1, "NVDA", "BUY", "2026-11-04T15:00:00+00:00", price=100.0))
    return r, t


def test_regenerate_same_inputs_identical():
    r, t = _regen_inputs()
    kw = dict(prices={"NVDA": 110.0, "AMD": 90.0, "XYZ": 50.0},
              spy={D("2026-11-01"): 100.0, D("2026-12-06"): 101.0})
    a = month(r, t, as_of=D("2026-12-06"), **kw)
    b = month(r, t, as_of=D("2026-12-06"), **kw)
    assert a == b


def test_different_as_of_keeps_entry_block_and_flow_counts():
    r, t = _regen_inputs()
    a = month(r, t, as_of=D("2026-12-06"), prices={"NVDA": 110.0, "AMD": 90.0})
    b = month(r, t, as_of=D("2026-12-20"), prices={"NVDA": 120.0, "AMD": 80.0})
    assert a["viz"]["entry"] == b["viz"]["entry"]
    for k in ("n_total", "n_acted", "n_missed"):
        assert a["viz"]["flow"][k] == b["viz"]["flow"][k]
    assert a["counts"] == b["counts"]
    assert a["viz"]["as_of"] != b["viz"]["as_of"]


# ── 6. rule edge cases ──────────────────────────────────────────────────────

def test_c2_carry_forward_add_winner_plus_already_held_enter_now_same_day():
    r = recs_of(rec(1, "AAPL", "2026-11-04", "add_winner"),
                rec(2, "AAPL", "2026-11-04", "enter_now", already_held=True))
    t = trades_of(trade(1, "AAPL", "BUY", "2026-11-04T15:00:00+00:00", "RECOMMENDATION"))
    res = month(r, t)
    assert status_of(res, "AAPL") == "acted"
    assert _cell(res, "AAPL")["rec_types"] == ["add_winner"]
    assert [row["rec_type"] for row in res["judged"]] == ["add_winner"]
    assert [row["rec_type"] for row in res["engine"]] == ["add_winner"]


def test_leak_already_held_enter_now_old_credit_does_not_rescue_earlier_call():
    # enter_now (already held) on 11-12 has an OLD-rule same-day credit; the
    # new_pick 10 days earlier has no BUY inside its own window.
    r = recs_of(rec(1, "AAPL", "2026-11-02", "new_pick"),
                rec(2, "AAPL", "2026-11-12", "enter_now", already_held=True))
    t = trades_of(trade(1, "AAPL", "BUY", "2026-11-12T15:00:00+00:00", "RECOMMENDATION"))
    res = month(r, t, as_of=D("2026-12-06"))
    assert status_of(res, "AAPL") == "not_acted"
    assert [row["rec_type"] for row in res["judged"]] == ["new_pick"]
    assert res["viz"]["flow"]["n_acted"] == 0 and res["viz"]["flow"]["n_missed"] == 1


def test_credit_deduped_rows_never_judged_and_never_crash_the_roll_up():
    r = recs_of(rec(1, "MRVL", "2026-11-06", "enter_now", already_held=False),
                rec(2, "MRVL", "2026-11-06", "new_pick"))
    t = trades_of(trade(7, "MRVL", "BUY", "2026-11-09T15:00:00+00:00"))
    res = month(r, t, as_of=D("2026-12-06"))
    assert status_of(res, "MRVL") == "acted"
    assert [row["rec_type"] for row in res["judged"]] == ["new_pick"]   # A4 rank winner
    assert len(res["engine"]) == 2                                      # engine keeps both
    assert eca.arm(res["judged"][0]) == "acted"
    assert res["counts"]["n_acted"] == 1


def test_call_after_period_end_is_not_judged_and_does_not_turn_a_name_unknown():
    r = recs_of(rec(1, "AAPL", "2026-11-03"), rec(2, "AAPL", "2026-12-02"))
    res = month(r, trades_of(), as_of=D("2026-12-06"))
    assert status_of(res, "AAPL") == "not_acted"
    assert _cell(res, "AAPL")["n_calls"] == 1
    assert res["counts"]["n_unknown"] == 0 and res["counts"]["n_undated"] == 0
    assert [row["rec_date"] for row in res["engine"]] == [D("2026-11-03")]


def test_call_before_period_start_is_not_in_the_month():
    res = month(recs_of(rec(1, "AAPL", "2026-10-28")), trades_of())
    assert res["names"] == [] and res["counts"]["n_surfaced"] == 0


def test_ud5_not_acted_plus_still_open_reads_still_open_and_is_in_neither_count():
    r = recs_of(rec(1, "AAPL", "2026-11-04"), rec(2, "AAPL", "2026-11-30"))
    res = month(r, trades_of(), as_of=D("2026-12-06"))
    assert status_of(res, "AAPL") == "still_open"
    c = res["counts"]
    assert (c["n_open"], c["n_acted"], c["n_not_acted"]) == (1, 0, 0)
    assert res["judged"] == []                       # neither call is in your view
    assert len(res["engine"]) == 2                   # both stay in the engine view
    assert res["viz"]["flow"]["n_total"] == 0


def test_flow_n_acted_equals_counts_n_acted():
    r = recs_of(
        rec(1, "A1", "2026-11-03"), rec(2, "B1", "2026-11-03"),
        rec(3, "C1", "2026-11-03", "add_winner"), rec(4, "D1", "2026-11-30"),
        rec(5, "A1", "2026-11-09"), rec(6, "E1", "2026-11-05", "enter_now",
                                         already_held=False))
    t = trades_of(trade(1, "A1", "BUY", "2026-11-04T15:00:00+00:00"),
                  trade(2, "C1", "BUY", "2026-11-03T15:00:00+00:00"))
    res = month(r, t, as_of=D("2026-12-06"))
    c = res["counts"]
    assert c["n_acted"] == res["viz"]["flow"]["n_acted"] == 2
    assert c["n_not_acted"] == res["viz"]["flow"]["n_missed"] == 2
    assert res["viz"]["flow"]["n_total"] == c["n_acted"] + c["n_not_acted"]
    assert c["n_surfaced"] == 5 and c["n_open"] == 1


def test_unknown_trade_date_unreadable_is_listed_with_its_reason():
    r = recs_of(rec(1, "XYZ", "2026-11-09"))
    t = trades_of(trade(1, "XYZ", "BUY", "not a date"))
    res = month(r, t, as_of=D("2026-12-06"))
    assert status_of(res, "XYZ") == "unknown"
    assert _cell(res, "XYZ")["reason"] == "trade_date_unreadable"
    c = res["counts"]
    assert (c["n_unknown"], c["n_acted"], c["n_not_acted"]) == (1, 0, 0)
    ent = res["viz"]["entry"]
    assert ent["unknown_names"] == [{
        "ticker": "XYZ", "rec_types": ["new_pick"], "n_calls": 1,
        "last_call_date": "2026-11-09", "reason": "trade_date_unreadable"}]
    assert mec.signal_lines(res["viz"]) == [
        "Not judged (not counted as bought or skipped):",
        "• XYZ: New position call on Mon Nov 9. Not judged: a buy of this name "
        "has a date that couldn't be read."]


def test_undated_entry_row_is_counted_and_dropped():
    r = recs_of(rec(1, "AAPL", "2026-11-03"), rec(2, "AMD", None),
                rec(3, "OLD", "2026-11-03", "buy_candidate"))
    res = month(r, trades_of())
    assert res["counts"]["n_undated"] == 1
    assert res["counts"]["n_surfaced"] == 1
    assert mec.signal_lines(res["viz"]) == [
        "• 1 entry call row(s) had no readable date and are not counted."]


def test_empty_recs_frame_is_ok_with_zeros():
    res = month(pd.DataFrame(), trades_of())
    assert res["judge_state"] == "ok"
    assert res["counts"] == {"n_surfaced": 0, "n_acted": 0, "n_not_acted": 0,
                             "n_open": 0, "n_unknown": 0, "n_undated": 0}
    assert res["viz"] is not None
    assert mec.signal_lines(res["viz"]) == []


# ── judge_state refusals ────────────────────────────────────────────────────

def _assert_refused(res, state):
    assert res["judge_state"] == state
    assert res["counts"] is None
    assert res["names"] == [] and res["judged"] == [] and res["engine"] == []
    assert res["viz"] is None


def test_refusal_recs_unavailable():
    _assert_refused(month(None, trades_of()), "recs_unavailable")


def test_refusal_recs_unreadable():
    _assert_refused(month(pd.DataFrame({"x": [1]}), trades_of()), "recs_unreadable")
    _assert_refused(month("nope", trades_of()), "recs_unreadable")


def test_refusal_trades_unavailable_is_never_all_missed():
    _assert_refused(month(recs_of(rec(1, "AAPL", "2026-11-03")), None),
                    "trades_unavailable")


def test_refusal_trades_unreadable():
    _assert_refused(month(recs_of(rec(1, "AAPL", "2026-11-03")),
                          pd.DataFrame({"foo": [1]})), "trades_unreadable")


def test_ok_state():
    assert month(recs_of(rec(1, "AAPL", "2026-11-03")), trades_of())["judge_state"] == "ok"


# ── pd.NA ids ───────────────────────────────────────────────────────────────

def test_pd_na_id_does_not_crash_even_on_a_dedup_tie():
    df = recs_of(rec(1, "AAPL", "2026-11-03"), rec(2, "AAPL", "2026-11-03"),
                 rec(3, "AMD", "2026-11-03"))
    df["id"] = pd.array([pd.NA, 1, 2], dtype="Int64")
    t = trades_of(trade(1, "AAPL", "BUY", "2026-11-03T15:00:00+00:00"))
    res = month(df, t)
    assert status_of(res, "AAPL") == "acted"
    assert status_of(res, "AMD") == "not_acted"
    json.dumps(res["viz"])


# ── UD-1 B: two price bases ─────────────────────────────────────────────────

def test_engine_alpha_uses_call_day_price_judged_alpha_uses_fill():
    r = recs_of(rec(1, "NVDA", "2026-11-03", price=100.0))
    t = trades_of(trade(1, "NVDA", "BUY", "2026-11-04T15:00:00+00:00", price=110.0))
    spy = {D("2026-11-01"): 100.0, D("2026-12-06"): 100.0}           # flat SPY
    res = month(r, t, as_of=D("2026-12-06"), prices={"NVDA": 121.0}, spy=spy)
    j, e = res["judged"][0], res["engine"][0]
    assert j["acted_on"] is True and j["alpha_pct"] == pytest.approx(10.0, abs=0.01)   # fill 110
    assert e["acted_on"] is False and e["alpha_pct"] == pytest.approx(21.0, abs=0.01)  # call day 100
    # the band chart reads the engine figure
    assert res["viz"]["bands"] == [{"band": "Buy (65–74)", "avg_alpha": pytest.approx(21.0, abs=0.01)}]
    assert res["viz"]["entry"]["price_basis"] == "call_day"


def test_band_rows_action_rate_excludes_still_open_calls():
    r = recs_of(rec(1, "AAA", "2026-11-03", comp=70),          # acted
                rec(2, "BBB", "2026-11-03", comp=70),          # not acted
                rec(3, "CCC", "2026-11-30", comp=70),          # still open
                rec(4, "DDD", "2026-11-30", comp=80))          # still open, own band
    t = trades_of(trade(1, "AAA", "BUY", "2026-11-03T15:00:00+00:00"))
    res = month(r, t, as_of=D("2026-12-06"),
                prices={k: 101.0 for k in ("AAA", "BBB", "CCC", "DDD")},
                spy={D("2026-11-01"): 100.0, D("2026-12-06"): 100.0})
    rows = mec.band_rows(res["engine"], res["judged"])
    by = {x["band"]: x for x in rows}
    buy = by["Buy (65–74)"]
    assert buy["n_total"] == 3                    # engine view counts the open call
    assert buy["action_rate"] == 50.0             # judged: 1 acted of 2, open call out
    strong = by["Strong Buy (≥75)"]
    assert strong["n_total"] == 1 and strong["action_rate"] is None   # nothing judged
    assert buy["n_priced"] == 3 and buy["avg_alpha"] == pytest.approx(1.0, abs=0.01)
    vr = mec.verdict_rows(res["engine"], res["judged"])
    assert [x["verdict"] for x in vr] == ["Engine-Confirmed"]
    assert vr[0]["n_total"] == 4 and vr[0]["action_rate"] == 50.0      # 1 of the 2 judged
    assert set(vr[0]) == {"verdict", "n_total", "n_priced", "avg_alpha", "action_rate"}


def test_engine_row_without_price_at_surface_is_ungraded():
    r = recs_of(rec(1, "NVDA", "2026-11-03", price=None))
    t = trades_of(trade(1, "NVDA", "BUY", "2026-11-04T15:00:00+00:00", price=110.0))
    res = month(r, t, as_of=D("2026-12-06"), prices={"NVDA": 121.0},
                spy={D("2026-11-01"): 100.0, D("2026-12-06"): 100.0})
    assert res["engine"][0]["alpha_pct"] is None
    assert res["engine"][0]["alpha_unavailable_reason"] == "no_entry_reference"
    assert res["judged"][0]["alpha_pct"] is not None        # the fill still grades it


def test_compute_outcomes_called_for_judged_and_engine_views(monkeypatch):
    calls = []
    real = rh.compute_outcomes

    def wrap(matched, *a, **k):
        calls.append([(m["ticker"], m["acted_on"]) for m in matched])
        return real(matched, *a, **k)

    monkeypatch.setattr(rh, "compute_outcomes", wrap)
    r = recs_of(rec(1, "NVDA", "2026-11-03"), rec(2, "AMD", "2026-11-30"))
    t = trades_of(trade(1, "NVDA", "BUY", "2026-11-04T15:00:00+00:00"))
    month(r, t, as_of=D("2026-12-06"))
    assert calls == [[("NVDA", True)],                       # judged: only the acted name
                     [("NVDA", False), ("AMD", False)]]      # engine: all, none "acted"


# ── viz ─────────────────────────────────────────────────────────────────────

def _rich_month():
    r = recs_of(rec(1, "AAA", "2026-11-03", comp=70), rec(2, "BBB", "2026-11-03", comp=50),
                rec(3, "CCC", "2026-11-27", "enter_now", already_held=False),
                rec(4, "CCC", "2026-11-30", "add_winner"), rec(5, "XYZ", "2026-11-09"),
                rec(6, "ZZZ", None))
    t = trades_of(trade(1, "AAA", "BUY", "2026-11-04T15:00:00+00:00"),
                  trade(2, "XYZ", "BUY", "garbage"))
    return month(r, t, as_of=D("2026-12-06"),
                 prices={"AAA": 110.0, "BBB": 90.0, "CCC": 55.0, "XYZ": 70.0},
                 spy={D("2026-11-01"): 100.0, D("2026-12-06"): 102.0})


def test_viz_is_json_safe_and_round_trips():
    viz = _rich_month()["viz"]
    assert json.loads(json.dumps(viz)) == viz
    assert viz["acted_rule_version"] == 2
    assert viz["window_trading_days"] == constants.ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS
    assert viz["as_of"] == "2026-12-06"
    assert set(viz) >= {"flow", "bands", "missed", "missed_split",
                        "avg_skipped_names_alpha", "entry"}
    assert viz["entry"]["rec_types"] == ["new_pick", "add_winner", "enter_now"]
    assert viz["entry"]["open_names"] == [{
        "ticker": "CCC", "rec_types": ["add_winner", "enter_now"], "n_calls": 2,
        "last_call_date": "2026-11-30", "window_end": "2026-12-02"}]
    assert isinstance(viz["avg_skipped_names_alpha"], float)


def test_avg_skipped_names_alpha_is_present_even_when_none():
    res = month(recs_of(rec(1, "AAA", "2026-11-03")),
                trades_of(trade(1, "AAA", "BUY", "2026-11-03T15:00:00+00:00")))
    assert "avg_skipped_names_alpha" in res["viz"]
    assert res["viz"]["avg_skipped_names_alpha"] is None


# ── signal_lines ────────────────────────────────────────────────────────────

def test_signal_lines_exact_strings_and_plain_text():
    lines = mec.signal_lines(_rich_month()["viz"])
    assert lines == [
        "Still open at the month's end (not counted as bought or skipped):",
        "• CCC: Add to winner + Enter Now call surfaced 2× this month, last on "
        "Mon Nov 30. Decision window ends Wed Dec 2.",
        "Not judged (not counted as bought or skipped):",
        "• XYZ: New position call on Mon Nov 9. Not judged: a buy of this name "
        "has a date that couldn't be read.",
        "• 1 entry call row(s) had no readable date and are not counted.",
    ]
    for ln in lines:
        assert not any(ch in ln for ch in ("**", "*", "_", "$"))


def test_signal_lines_single_call_open_name():
    res = month(recs_of(rec(1, "NVDA", "2026-11-27", "enter_now", already_held=False)),
                trades_of(), as_of=D("2026-12-06"))
    assert mec.signal_lines(res["viz"]) == [
        "Still open at the month's end (not counted as bought or skipped):",
        "• NVDA: Enter Now call on Fri Nov 27. Decision window ends Tue Dec 1."]


def test_signal_lines_empty_when_nothing_to_list_and_for_non_v2():
    res = month(recs_of(rec(1, "AAA", "2026-11-03")), trades_of())
    assert mec.signal_lines(res["viz"]) == []
    for bad in (None, {}, {"acted_rule_version": 1}, {"acted_rule_version": float("nan")},
                "viz", {"entry": {"open_names": [{"ticker": "X"}]}}):
        assert mec.signal_lines(bad) == []


def test_signal_lines_tolerate_a_v2_viz_with_a_missing_entry_block():
    assert mec.signal_lines({"acted_rule_version": 2}) == []
    assert mec.signal_lines({"acted_rule_version": 2, "entry": None}) == []


# ── rule_note / acted_rule_caption / scope_words ────────────────────────────

_V2_ENTRY = {"acted_rule_version": 2, "entry": {"n_open": 3, "n_unknown": 1}}


def test_rule_note_exact_text_for_v2():
    assert mec.rule_note(_V2_ENTRY) == (
        "Acted on = a buy of that name on the call day or within 2 trading days "
        "after it (New Position, Add to Winner and Enter Now calls; an Enter Now "
        "on a name you already held is left out). 3 still open at the month's "
        "end and 1 not judged are in neither count. "
        "The band chart prices every call from its call-day price; the bought "
        "average uses your fill price, up to 2 trading days after the call. "
        "📜 Recommendations History and the other pages that count acted calls "
        "still use the older rule (a same-day buy tagged as a recommendation), "
        "so their counts can differ.")
    note = mec.rule_note(_V2_ENTRY)
    assert not any(ch in note for ch in ("**", "*", "_", "$"))


def test_rule_note_defaults_counts_to_zero_without_entry():
    assert "0 still open at the month's end and 0 not judged" in mec.rule_note(
        {"acted_rule_version": 2})


@pytest.mark.parametrize("viz", [None, {}, {"acted_rule_version": None},
                                 {"acted_rule_version": float("nan")},
                                 {"acted_rule_version": 1}, {"acted_rule_version": "x"}])
def test_older_viz_reads_as_older_rule(viz):
    assert mec.rule_note(viz) is None
    cap = mec.acted_rule_caption(viz, live=False)
    assert cap == (
        "This report used the older acted rule: only a buy tagged as a "
        "recommendation on the call day counted as acted, and only New "
        "Position calls were counted. Newer reports count a buy on the call "
        "day or within 2 trading days, and include Add to Winner and Enter Now calls.")
    assert mec.scope_words(viz)["names"] == "distinct New-Position tickers"


@pytest.mark.parametrize("v", [2, 2.0, "2", 3])
def test_current_viz_reads_as_current_rule(v):
    viz = {"acted_rule_version": v}
    assert mec.rule_note(viz) is not None
    assert mec.acted_rule_caption(viz, live=False) is None
    assert mec.scope_words(viz)["names"].startswith("distinct entry-call tickers")


def test_acted_rule_caption_live_text():
    expected = ("Counts and charts were recomputed today with the current acted "
                "rule (a buy on the call day or within 2 trading days). The text "
                "below and the bought average were saved when the report was "
                "generated, under the older rule, so they can differ.")
    assert mec.acted_rule_caption(None, live=True) == expected
    assert mec.acted_rule_caption(_V2_ENTRY, live=True) == expected


def test_scope_words_exact_strings():
    assert mec.scope_words(_V2_ENTRY) == {
        "names": "distinct entry-call tickers (New Position, Add to Winner, Enter Now)",
        "flow_title": "This month's signal flow — entry calls → bought / skipped → outcome",
        "missed_scope": "entry calls (New Position, Add to Winner, Enter Now)",
    }
    assert mec.scope_words(None) == {
        "names": "distinct New-Position tickers",
        "flow_title": "This month's signal flow — New Positions → acted / missed → outcome",
        "missed_scope": "New Positions to Initiate",
    }


def test_window_is_read_from_the_constant_at_call_time(monkeypatch):
    monkeypatch.setattr(constants, "ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS", 3)
    assert "within 3 trading days after it" in mec.rule_note(_V2_ENTRY)
    assert "up to 3 trading days after the call" in mec.rule_note(_V2_ENTRY)
    assert "within 3 trading days" in mec.acted_rule_caption(None, live=False)
    assert "within 3 trading days" in mec.acted_rule_caption(None, live=True)
    # the judgement follows too: Tue 11-24 call, BUY Mon 11-30 is 3 sessions later
    r = recs_of(rec(1, "AAPL", "2026-11-24"))
    t = trades_of(trade(1, "AAPL", "BUY", "2026-11-30T15:00:00+00:00"))
    res = month(r, t, as_of=D("2026-12-06"))
    assert status_of(res, "AAPL") == "acted"
    assert res["viz"]["window_trading_days"] == 3
    monkeypatch.setattr(constants, "ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS", 1)
    assert "within 1 trading day after it" in mec.rule_note(_V2_ENTRY)
    assert status_of(month(r, t, as_of=D("2026-12-06")), "AAPL") == "not_acted"


# ── lines through the email and the app renderers ───────────────────────────

def test_lines_render_as_one_li_per_bullet_in_the_email_and_a_list_in_markdown():
    from stock_analyzer import notify, report_display
    lines = mec.signal_lines(_rich_month()["viz"])
    text = "Narrative from the model.\n\n" + "\n".join(lines)
    html = notify._email_section("Signal discipline", text, {})
    bullets = [ln for ln in lines if ln.startswith("• ")]
    assert html.count("<li ") == len(bullets) == 3
    assert "Mon Nov 30" in html and "<strong>" not in html and "**" not in html
    md = report_display.report_markdown(text)
    assert md.count("\n- ") == len(bullets)
    assert "**" not in md
    assert "$" not in md
