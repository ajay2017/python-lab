"""stock_analyzer.daily_budget -- the daily new-position budget (pure layer)."""
import copy
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import daily_budget as db
from stock_analyzer import hold_reasons as hr
from stock_analyzer.constants import DAILY_NEW_POSITION_BUDGET_PCT

pytestmark = pytest.mark.fast

DAY = date(2026, 10, 15)
NOW = pd.Timestamp("2026-10-15T18:00:00Z")
CASH_TS = "2026-10-15T12:00:00+00:00"
OK = {"state": "ok", "basis": 10000.0, "reason": None, "lag_adjustment": 0.0}  # budget 4,000


def _sz(shares, price):
    return {"shares": shares, "total_cost": round(shares * price, 2),
            "port_pct": 10.0, "capital_pct": 20.0, "risk_budget": 100.0,
            "ceiling_capped": False, "uncapped_shares": shares,
            "stop": price * 0.9, "portfolio_value": 20000.0, "sizing_version": 3}


def _c(tk, comp, shares, price, etf=False, mom=50.0, sizing=None):
    return {"ticker": tk, "composite": comp, "momentum": mom, "is_etf": etf,
            "price": price, "sizing": sizing if sizing is not None else _sz(shares, price)}


def _trades(rows):
    return pd.DataFrame(rows, columns=["ticker", "action", "traded_at", "shares", "price"])


# ---------------------------------------------------------------- allocation
def test_non_binding_day_unchanged():
    # two 1,000 picks vs a 4,000 budget -> both fully sized
    r = db.allocate(OK, {}, {}, [_c("AAA", 80, 10, 100), _c("BBB", 70, 10, 100)])
    assert [r["per_ticker"][t]["status"] for t in ("AAA", "BBB")] == ["sized", "sized"]
    assert r["per_ticker"]["BBB"]["shares"] == 10
    assert r["left"] == pytest.approx(2000.0)


def test_binding_day_second_pick_sized_from_what_is_left():
    # budget 4,000; first 2,400 (24%), second wants 2,400 but only 1,600 left
    r = db.allocate(OK, {}, {}, [_c("AAA", 80, 24, 100), _c("BBB", 70, 24, 100)])
    assert r["per_ticker"]["AAA"]["status"] == "sized"
    b = r["per_ticker"]["BBB"]
    assert b["status"] == "budget_partial" and b["shares"] == 16 and b["uncapped_shares"] == 24
    assert r["left"] == pytest.approx(0.0)


def test_sum_never_exceeds_left_nor_f255_and_left_nonnegative():
    cands = [_c(f"T{i}", 90 - i, 7 + i, 37.5) for i in range(8)]
    r = db.allocate(OK, {"X": 500.0}, {"Y": {"shares": 5, "dollars": 700.0}}, cands)
    spent = 0.0
    for c in cands:
        e = r["per_ticker"][c["ticker"]]
        if e["shares"]:
            assert e["shares"] <= c["sizing"]["shares"]
            spent += e["shares"] * c["price"]
    assert spent <= 4000 - 500 - 700 + 1e-6
    assert r["left"] >= 0


def test_boundaries_used_equals_budget_and_one_share_edges():
    r = db.allocate(OK, {"X": 4000.0}, {}, [_c("AAA", 80, 5, 100)])
    assert r["per_ticker"]["AAA"]["status"] == "budget_exhausted"
    assert "shares" not in db.apply_to_sizing(_sz(5, 100), r["per_ticker"]["AAA"])
    # left = price - 0.01 -> exhausted; left = price -> exactly 1 share
    r = db.allocate(OK, {"X": 3900.01}, {}, [_c("AAA", 80, 5, 100)])
    assert r["per_ticker"]["AAA"]["status"] == "budget_exhausted"
    r = db.allocate(OK, {"X": 3900.0}, {}, [_c("AAA", 80, 5, 100)])
    assert (r["per_ticker"]["AAA"]["status"], r["per_ticker"]["AAA"]["shares"]) == ("budget_partial", 1)


def test_ordering_composite_first_and_reversed_input_identical():
    cands = [_c("LOW", 60, 24, 100), _c("HIGH", 90, 24, 100), _c("MID", 75, 24, 100)]
    a = db.allocate(OK, {}, {}, cands)
    b = db.allocate(OK, {}, {}, list(reversed(cands)))
    assert a == b
    assert a["per_ticker"]["HIGH"]["status"] == "sized"
    assert a["per_ticker"]["MID"]["status"] == "budget_partial"
    assert a["per_ticker"]["LOW"]["status"] == "budget_exhausted"


def test_etf_after_stock_even_with_higher_composite():
    cands = [_c("SPY", 99, 24, 100, etf=True), _c("STK", 60, 24, 100)]
    r = db.allocate(OK, {}, {}, cands)
    assert r["per_ticker"]["STK"]["status"] == "sized"
    assert r["per_ticker"]["SPY"]["status"] == "budget_partial"
    assert db.candidate_sort_key(cands[0])[0] is True


def test_missing_composite_last_and_ticker_tiebreak():
    keys = sorted([_c("ZZZ", None, 1, 1), _c("BBB", 50, 1, 1), _c("AAA", 50, 1, 1)],
                  key=db.candidate_sort_key)
    assert [k["ticker"] for k in keys] == ["AAA", "BBB", "ZZZ"]
    nan_key = db.candidate_sort_key({"ticker": "N", "composite": float("nan")})
    assert nan_key[1] == float("inf")


def test_logged_buy_deducted():
    r = db.allocate(OK, {"AAA": 1500.0}, {}, [_c("BBB", 80, 30, 100)])
    assert r["used_logged"] == 1500.0
    assert r["per_ticker"]["BBB"]["shares"] == 25  # 2,500 left


def test_logged_buy_replaces_its_allocation():
    r = db.allocate(OK, {"AAA": 1500.0}, {"AAA": {"shares": 20, "dollars": 2000.0}}, [])
    assert r["used_logged"] == 1500.0 and r["used_allocated"] == 0.0
    assert r["left"] == pytest.approx(2500.0)
    # control: no logged buy -> the allocation counts
    r2 = db.allocate(OK, {}, {"AAA": {"shares": 20, "dollars": 2000.0}}, [])
    assert r2["used_allocated"] == 2000.0 and r2["left"] == pytest.approx(2000.0)


def test_existing_allocation_reused_locked_and_consumes_nothing_new():
    alloc = {"AAA": {"shares": 15, "dollars": 1500.0}}
    r = db.allocate(OK, {}, alloc, [_c("AAA", 80, 24, 100), _c("BBB", 70, 10, 100)])
    a = r["per_ticker"]["AAA"]
    assert (a["status"], a["shares"]) == ("budget_reserved", 15)
    assert r["used_list"] == 1000.0  # only BBB
    # min(allocated, current F-255)
    r = db.allocate(OK, {}, alloc, [_c("AAA", 80, 9, 100)])
    assert r["per_ticker"]["AAA"]["shares"] == 9
    # idempotent
    cands = [_c("AAA", 80, 24, 100), _c("BBB", 70, 24, 100)]
    assert db.allocate(OK, {}, alloc, cands) == db.allocate(OK, {}, alloc, cands)


def test_passthrough_markers_consume_nothing():
    marker = {"ceiling_infeasible": True, "sizing_version": 3}
    r = db.allocate(OK, {}, {}, [_c("MKR", 99, 0, 100, sizing=marker),
                                 _c("EMP", 98, 0, 100, sizing={}),
                                 _c("AAA", 80, 10, 100)])
    assert r["per_ticker"]["MKR"]["status"] == "passthrough"
    assert r["per_ticker"]["EMP"]["status"] == "passthrough"
    assert r["per_ticker"]["AAA"]["status"] == "sized"
    assert r["used_list"] == 1000.0


# ----------------------------------------------------------------- fail-safe
@pytest.mark.parametrize("kind", ["basis", "logged", "allocations", "zero_basis"])
def test_unknown_input_withholds_every_consumer(kind):
    basis, logged, alloc = dict(OK), {}, {}
    if kind == "basis":
        basis = {"state": "unknown", "basis": None, "reason": "no cash record"}
    elif kind == "logged":
        logged = None
    elif kind == "allocations":
        alloc = None
    else:
        basis = {"state": "ok", "basis": 0.0}
    cands = [_c("AAA", 80, 10, 100), _c("BBB", 70, 10, 100),
             _c("MKR", 60, 0, 100, sizing={"stop_infeasible": True})]
    r = db.allocate(basis, logged, alloc, cands)
    assert r["state"] == "unknown"
    assert r["per_ticker"]["AAA"]["status"] == "budget_unknown"
    assert r["per_ticker"]["BBB"]["status"] == "budget_unknown"
    assert r["per_ticker"]["MKR"]["status"] == "passthrough"
    out = db.apply_to_sizing(cands[0]["sizing"], r["per_ticker"]["AAA"])
    assert "shares" not in out and out["budget_unknown"] is True
    # control: readable inputs size
    ok = db.allocate(OK, {}, {}, cands)
    assert ok["per_ticker"]["AAA"]["status"] == "sized"


def test_called_budget_zero_and_passthrough():
    r = db.allocate({"state": "called", "basis": -50.0}, {}, {},
                    [_c("MKR", 80, 0, 100, sizing={"capital_infeasible": True}),
                     _c("AAA", 70, 10, 100)])
    assert r["state"] == "called" and r["budget"] == 0.0
    assert r["per_ticker"]["MKR"]["status"] == "passthrough"
    assert r["per_ticker"]["AAA"]["status"] == "budget_exhausted"


# ------------------------------------------------------------- logged buys
def test_buys_on_et_day_midnight_boundary():
    df = _trades([
        ("AAA", "BUY", "2026-10-15T03:59:00+00:00", 10, 100.0),   # 23:59 ET on 10-14
        ("BBB", "BUY", "2026-10-15T04:00:00+00:00", 10, 100.0),   # 00:00 ET on 10-15
        ("CCC", "BUY", "2026-10-16T03:59:00+00:00", 5, 100.0),    # 23:59 ET on 10-15
        ("DDD", "BUY", "2026-10-16T04:00:00+00:00", 5, 100.0),    # 10-16 ET
        ("EEE", "SELL", "2026-10-15T15:00:00+00:00", 5, 100.0),   # sell: not a buy
        ("bbb", "BUY", "2026-10-15T15:00:00+00:00", 2, 50.0),     # summed, upper-cased
    ])
    assert db.buys_on_et_day(df, DAY) == {"BBB": 1100.0, "CCC": 500.0}


@pytest.mark.parametrize("bad", [
    None,
    pd.DataFrame([{"ticker": "A", "action": "BUY"}]),
    _trades([("A", "BUY", "2026-10-15T15:00:00+00:00", None, 10.0)]),
    _trades([("A", "BUY", "2026-10-15T15:00:00+00:00", 5, 0.0)]),
    _trades([("A", "BUY", "garbage", 5, 10.0)]),
])
def test_buys_unreadable_is_none_with_empty_control(bad):
    assert db.buys_on_et_day(bad, DAY) is None
    assert db.buys_on_et_day(_trades([]), DAY) == {}


def test_unvaluable_buy_on_another_day_does_not_poison():
    df = _trades([("A", "BUY", "2026-10-10T15:00:00+00:00", None, 10.0)])
    assert db.buys_on_et_day(df, DAY) == {}


# ----------------------------------------------------------------- basis
def _basis(state="levered", net=8000.0, gross=20000.0, cash=-12000.0,
           as_of=CASH_TS, trades=None, now=NOW):
    return db.budget_basis(gross, net, state, cash, as_of,
                           _trades([]) if trades is None else trades, DAY, now)


def test_basis_levered_ok_and_lag_correction():
    assert _basis()["basis"] == 8000.0
    df = _trades([
        ("A", "BUY", "2026-10-15T15:00:00+00:00", 10, 100.0),    # after cash ts: -1000
        ("B", "BUY", "2026-10-15T11:00:00+00:00", 10, 100.0),    # before: ignored
        ("C", "SELL", "2026-10-15T16:00:00+00:00", 5, 100.0),    # after: +500
        ("D", "SELL", "2026-10-15T10:00:00+00:00", 5, 100.0),    # before: ignored
    ])
    r = _basis(trades=df)
    assert r["lag_adjustment"] == pytest.approx(-500.0)
    assert r["basis"] == pytest.approx(7500.0) and r["state"] == "ok"


def test_trades_after_split():
    df = _trades([("A", "BUY", "2026-10-15T15:00:00+00:00", 10, 100.0),
                  ("C", "SELL", "2026-10-15T16:00:00+00:00", 5, 100.0)])
    assert db.trades_after(df, DAY, CASH_TS) == (1000.0, 500.0)
    assert db.trades_after(df, DAY, "2026-10-15T15:30:00+00:00") == (0.0, 500.0)
    assert db.trades_after(None, DAY, CASH_TS) is None
    assert db.trades_after(df, DAY, "garbage") is None


def test_basis_unlevered_with_cash_uses_gross_plus_cash():
    r = _basis(state="unlevered", net=None, gross=20000.0, cash=500.0)
    assert r["state"] == "ok" and r["basis"] == 20500.0


@pytest.mark.parametrize("kw", [
    dict(state="stale"), dict(state="weird"), dict(cash=None), dict(as_of=None),
    dict(as_of="2026-10-01T00:00:00Z"), dict(as_of="garbage"),
    dict(state="unlevered", net=None, gross=None, cash=0.0),
    dict(state="levered", net=None),
])
def test_basis_unknown_inputs(kw):
    r = _basis(**kw)
    assert r["state"] == "unknown" and r["basis"] is None and r["reason"]
    assert _basis()["state"] == "ok"  # control


def test_basis_unreadable_trades_unknown():
    r = db.budget_basis(20000.0, 8000.0, "levered", -12000.0, CASH_TS, None, DAY, NOW)
    assert r["state"] == "unknown"


def test_basis_called_and_lag_driven_called():
    assert _basis(state="called", net=-100.0)["state"] == "called"
    df = _trades([("A", "BUY", "2026-10-15T15:00:00+00:00", 100, 100.0)])  # -10,000
    r = _basis(trades=df)
    assert r["state"] == "called" and r["basis"] <= 0


# ----------------------------------------------------------- apply_to_sizing
def test_apply_partial_exhausted_and_no_mutation():
    sz = _sz(24, 100)
    frozen = copy.deepcopy(sz)
    cands = [_c("AAA", 80, 24, 100, sizing=sz), _c("BBB", 70, 24, 100)]
    frozen_c = copy.deepcopy(cands)
    logged, alloc = {"X": 100.0}, {"Y": {"shares": 1, "dollars": 100.0}}
    frozen_in = (copy.deepcopy(logged), copy.deepcopy(alloc), copy.deepcopy(OK))
    r = db.allocate(OK, logged, alloc, cands)
    part = db.apply_to_sizing(cands[1]["sizing"], r["per_ticker"]["BBB"])
    assert part["shares"] < 24 and part["budget_capped"] is True
    assert part["budget_uncapped_shares"] == 24 and part["sizing_version"] == 4
    assert part["total_cost"] == pytest.approx(part["shares"] * 100)
    assert part["port_pct"] < 10.0
    ex = db.apply_to_sizing(sz, {"status": "budget_exhausted"})
    assert "shares" not in ex and ex["budget_exhausted"] is True and ex["stop"] == sz["stop"]
    assert cands == frozen_c and sz == frozen
    assert (logged, alloc, OK) == frozen_in


def test_apply_sized_and_passthrough_only_bump_version():
    sz = _sz(10, 100)
    out = db.apply_to_sizing(sz, {"status": "sized", "shares": 10, "dollars": 1000.0})
    assert out["shares"] == 10 and out["sizing_version"] == 4 and "budget_capped" not in out
    mk = db.apply_to_sizing({"ceiling_infeasible": True, "sizing_version": 3}, {"status": "passthrough"})
    assert mk == {"ceiling_infeasible": True, "sizing_version": 4}
    assert db.apply_to_sizing({}, {"status": "passthrough"}) == {}


def test_apply_reserved_flagged():
    out = db.apply_to_sizing(_sz(24, 100), {"status": "budget_reserved", "shares": 15,
                                            "dollars": 1500.0, "uncapped_shares": 24})
    assert out["shares"] == 15 and out["budget_reserved"] is True


# ------------------------------------------------------------ wording / misc
def test_budget_texts_never_say_not_one_of_todays_picks():
    texts = [hr.budget_note(s, {"n": 3, "m": 5, "why": "no cash record"})
             for s in hr.BUDGET_CODES]
    texts += [hr.budget_status_line(55, 1000, 2000, 1000, 500), hr.budget_over_line(45)]
    for t in texts:
        assert t  # firing control
        assert "not one of today's picks" not in t
    assert hr.budget_note("pick_cap") == ""


def test_budget_wording_interpolates_constant():
    pct = f"{DAILY_NEW_POSITION_BUDGET_PCT:g}%"
    assert pct in hr.budget_note("budget_exhausted")
    assert pct in hr.budget_over_line(45)
    assert f"budget {pct}, $500 left" in hr.budget_status_line(55, 1000, 2000, 1000, 500)
    assert hr.budget_note("budget_partial", {"n": 3, "m": 5}) == \
        "Sized from what's left of today's budget — 3 of 5 shares."
    assert "(no cash record)" in hr.budget_note("budget_unknown", {"why": "no cash record"})


def test_sold_today_and_budget_agree_on_et_day():
    from stock_analyzer.sold_today import tickers_sold_on
    df = _trades([("AAA", "SELL", "2026-10-15T03:59:00+00:00", 1, 1.0),
                  ("BBB", "SELL", "2026-10-15T04:00:00+00:00", 1, 1.0)])
    assert tickers_sold_on(df, DAY) == {"BBB"}
    buys = _trades([("AAA", "BUY", "2026-10-15T03:59:00+00:00", 1, 1.0),
                    ("BBB", "BUY", "2026-10-15T04:00:00+00:00", 1, 1.0)])
    assert db.buys_on_et_day(buys, DAY) == {"BBB": 1.0}


# ------------------------------------------------------- review round 1 fixes
def test_logged_buy_stops_allocation_reuse_b1():
    alloc = {"AAA": {"shares": 20, "dollars": 2000.0}}
    # alloc 2,000 + logged 2,000 -> used 2,000, left 2,000; AAA sized as a normal candidate
    r = db.allocate(OK, {"AAA": 2000.0}, alloc, [_c("AAA", 80, 30, 100)])
    e = r["per_ticker"]["AAA"]
    assert e["status"] != "budget_reserved"
    assert (e["status"], e["shares"]) == ("budget_partial", 20)
    assert r["used_list"] == 2000.0
    # partial buy (alloc 20, bought 5) -> same
    r = db.allocate(OK, {"AAA": 500.0}, alloc, [_c("AAA", 80, 10, 100)])
    assert r["per_ticker"]["AAA"]["status"] == "sized"
    # left == 0 -> exhausted, not reserved
    r = db.allocate(OK, {"AAA": 4000.0}, alloc, [_c("AAA", 80, 10, 100)])
    assert r["per_ticker"]["AAA"]["status"] == "budget_exhausted"
    # control: no logged buy -> reserved
    r = db.allocate(OK, {}, alloc, [_c("AAA", 80, 30, 100)])
    assert r["per_ticker"]["AAA"]["status"] == "budget_reserved"


def test_trades_after_walks_every_date_b2():
    df = _trades([
        ("A", "BUY", "2026-10-14T18:00:00+00:00", 30, 100.0),   # prev day, after cash ts
        ("B", "BUY", "2026-10-14T09:00:00+00:00", 30, 100.0),   # prev day, before cash ts
        ("C", "SELL", "2026-10-14T19:00:00+00:00", 10, 100.0),  # sell after
    ])
    assert db.trades_after(df, DAY, "2026-10-14T10:00:00+00:00") == (3000.0, 1000.0)
    r = db.budget_basis(20000.0, 8000.0, "levered", -12000.0,
                        "2026-10-14T10:00:00+00:00", df, DAY, NOW)
    assert r["lag_adjustment"] == -2000.0 and r["basis"] == 6000.0


def test_trade_exactly_at_cash_timestamp_not_after():
    df = _trades([("A", "BUY", CASH_TS, 10, 100.0)])
    assert db.trades_after(df, DAY, CASH_TS) == (0.0, 0.0)
    df2 = _trades([("A", "BUY", "2026-10-15T12:00:01+00:00", 10, 100.0)])
    assert db.trades_after(df2, DAY, CASH_TS) == (1000.0, 0.0)


def test_unvaluable_row_before_cash_ts_does_not_poison_but_after_does():
    bad_before = _trades([("A", "BUY", "2026-10-01T10:00:00+00:00", None, 1.0)])
    assert db.trades_after(bad_before, DAY, CASH_TS) == (0.0, 0.0)
    bad_after = _trades([("A", "BUY", "2026-10-15T13:00:00+00:00", None, 1.0)])
    assert db.trades_after(bad_after, DAY, CASH_TS) is None


def test_stale_boundary_matches_margin_rule():
    from stock_analyzer.constants import ACCOUNT_CASH_STALE_DAYS as N
    now = pd.Timestamp("2026-10-15T12:00:00Z")
    ok_ts = now - pd.Timedelta(days=N)
    stale_ts = now - pd.Timedelta(days=N + 1)
    assert _basis(as_of=ok_ts.isoformat(), now=now)["state"] == "ok"
    assert _basis(as_of=stale_ts.isoformat(), now=now)["state"] == "unknown"


def test_price_mismatch_single_dollar_figure():
    # total_cost disagrees with shares*price; budget 4,000, price 100, F-255 40 sh
    sz = _sz(40, 100)
    sz["total_cost"] = 1.0
    r = db.allocate(OK, {"X": 1000.0}, {}, [_c("AAA", 80, 40, 100, sizing=sz)])
    e = r["per_ticker"]["AAA"]
    assert e["shares"] == 30 and e["status"] == "budget_partial"
    assert e["shares"] * 100 <= 3000


def test_duplicate_tickers_deterministic():
    a, b = _c("AAA", 80, 10, 100), _c("AAA", 80, 24, 100)
    r1 = db.allocate(OK, {}, {}, [a, b])
    r2 = db.allocate(OK, {}, {}, [b, a])
    assert r1 == r2 and r1["per_ticker"]["AAA"]["shares"] == 10
    assert r1["used_list"] == 1000.0  # charged once


@pytest.mark.parametrize("bad", [None, 0, -5, float("nan"), float("inf")])
def test_bad_allocation_dollars_is_unknown(bad):
    r = db.allocate(OK, {}, {"Y": {"shares": 5, "dollars": bad}}, [_c("AAA", 80, 10, 100)])
    assert r["state"] == "unknown"
    assert r["per_ticker"]["AAA"]["status"] == "budget_unknown"
    ok = db.allocate(OK, {}, {"Y": {"shares": 5, "dollars": 500.0}}, [_c("AAA", 80, 10, 100)])
    assert ok["per_ticker"]["AAA"]["status"] == "sized"


def test_logged_alone_above_budget():
    r = db.allocate(OK, {"X": 4500.0}, {}, [_c("AAA", 80, 10, 100), _c("BBB", 70, 5, 100)])
    assert r["left"] == 0.0 and r["pct_used"] > DAILY_NEW_POSITION_BUDGET_PCT
    assert r["over_budget"] is True
    assert all(r["per_ticker"][t]["status"] == "budget_exhausted" for t in ("AAA", "BBB"))
    assert "above the" in hr.budget_over_line(r["pct_used"])
    assert db.allocate(OK, {"X": 100.0}, {}, [])["over_budget"] is False


def test_momentum_tiebreak_sign():
    k = sorted([_c("AAA", 70, 1, 1, mom=40.0), _c("BBB", 70, 1, 1, mom=60.0),
                _c("CCC", 70, 1, 1, mom=None)], key=db.candidate_sort_key)
    assert [c["ticker"] for c in k] == ["BBB", "AAA", "CCC"]


def test_called_after_lag_reason_and_text():
    df = _trades([("A", "BUY", "2026-10-15T15:00:00+00:00", 100, 100.0)])
    b = _basis(trades=df)
    assert b["state"] == "called" and b["reason"] == db.CALLED_REASON
    r = db.allocate(b, {}, {}, [_c("AAA", 80, 10, 100)])
    e = r["per_ticker"]["AAA"]
    assert e["status"] == "budget_exhausted" and e["reason"] == db.CALLED_REASON
    t = hr.budget_note("budget_exhausted", {"called": True})
    assert "at or below zero" in t and "budget is already" not in t


def test_hold_reasons_codes_match_daily_budget():
    assert (hr.BUDGET_PARTIAL, hr.BUDGET_EXHAUSTED, hr.BUDGET_RESERVED, hr.BUDGET_UNKNOWN) == (
        db.BUDGET_PARTIAL, db.BUDGET_EXHAUSTED, db.BUDGET_RESERVED, db.BUDGET_UNKNOWN)


def test_new_wording_strings():
    assert hr.budget_note("budget_reserved") == "Allocated in today's email"
    pick = hr.budget_note("budget_exhausted")
    non = hr.budget_note("budget_exhausted", {"is_pick": False})
    assert pick.startswith("One of today's picks")
    assert non == (f"Not sized: today's new-position budget "
                   f"({DAILY_NEW_POSITION_BUDGET_PCT:g}% of net capital) is already used. "
                   f"Resets tomorrow.")
    assert "One of today's picks" not in non


def test_status_line_near_boundary_rounding():
    t = hr.budget_status_line(39.96, 1000, 0, 0, 3.4)
    assert "40.0% of net capital" in t and "$3 left" in t
    assert "39.9%" in hr.budget_status_line(39.94, 0, 0, 0, 0)


def test_left_never_negative_on_float_wiggle():
    r = db.allocate(OK, {"X": 3000.0000000005}, {}, [_c("AAA", 80, 1, 1000)])
    assert r["per_ticker"]["AAA"]["status"] == "sized"
    assert r["left"] == 0.0


def test_duplicate_marker_beats_sized_in_both_orders():
    m = _c("AAA", 80, 0, 100, sizing={"ceiling_infeasible": True})
    s_ = _c("AAA", 80, 10, 100)
    for order in ([m, s_], [s_, m]):
        r = db.allocate(OK, {}, {}, order)
        assert r["per_ticker"]["AAA"]["status"] == "passthrough"
        assert r["used_list"] == 0.0
