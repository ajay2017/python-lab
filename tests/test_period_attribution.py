"""Tests for stock_analyzer/period_attribution.py (period report C2: S1 + S3)."""
from __future__ import annotations

import copy
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import period_attribution as pa
from stock_analyzer.constants import NYSE_HOLIDAYS
from stock_analyzer.daily_pnl import compute_positions_day_pnl

D = date.fromisoformat
FRI, MON, TUE, WED = D("2026-10-09"), D("2026-10-12"), D("2026-10-13"), D("2026-10-14")
OK_ASOF = "21:30:00+00:00"  # 17:30 ET: after the EOD hour


def _ts(d, hhmm="19:00:00"):
    return f"{d.isoformat()}T{hhmm}+00:00"


def _acct(d, g, c, *, lev=None, cd=None, cushion=None, mr=0.25, asof=None):
    e = None if (g is None or c is None) else g + c
    return {
        "snapshot_date": d.isoformat(), "gross_book": g, "cash_balance": c, "net_equity": e,
        "leverage": lev if lev is not None else (g / e if e else None),
        "cushion": cushion, "call_distance_pct": cd, "maintenance_rate": mr,
        "cash_as_of": asof if asof is not None else f"{d.isoformat()}T{OK_ASOF}",
    }


def world(init, steps, closes, *, init_date=D("2026-09-01")):
    """Simulate a book so recorded G/C are consistent with the journal.

    init: [(ticker, shares, price)] bought on init_date.  steps: {date: {"trades":
    [(ticker, 'BUY'|'SELL', shares, price)], "cash": float}} (cash = flows/income).
    closes: {date: {ticker: close}} for every session d0..d1 and every held name.
    Returns (trades_df, acct_df, daily_df)."""
    rows, nid, cash, pos = [], 0, 0.0, {}
    for t, sh, px in init:
        nid += 1
        rows.append({"id": nid, "ticker": t, "action": "BUY", "shares": sh, "price": px,
                     "traded_at": _ts(init_date)})
        cash -= sh * px
        pos[t] = pos.get(t, 0) + sh
    acct, daily = [], []
    for d in sorted(closes):
        st = steps.get(d, {})
        for t, a, sh, px in st.get("trades", []):
            nid += 1
            rows.append({"id": nid, "ticker": t, "action": a, "shares": sh, "price": px,
                         "traded_at": _ts(d)})
            sgn = 1 if a == "BUY" else -1
            cash -= sgn * sh * px
            pos[t] = pos.get(t, 0) + sgn * sh
            if pos[t] == 0:
                del pos[t]
        cash += st.get("cash", 0.0)
        g = sum(sh * closes[d][t] for t, sh in pos.items())
        acct.append(_acct(d, g, cash))
        for t, sh in pos.items():
            daily.append({"snapshot_date": d.isoformat(), "ticker": t, "shares": int(sh),
                          "close_price": closes[d][t]})
    return pd.DataFrame(rows), pd.DataFrame(acct), pd.DataFrame(daily)


def recon(trades, acct, daily, ps=MON, pe=WED, flows=(), income=(), **kw):
    return pa.reconcile_period(
        period_start=ps, period_end=pe, trades_df=trades, account_snapshots_df=acct,
        daily_snapshots_df=daily, flows=list(flows) if flows is not None else None,
        income_events=list(income) if income is not None else None, **kw)


# --- the plan's synthetic week --------------------------------------------

def week_world():
    init = [("A", 10, 100), ("B", 5, 200)]
    closes = {FRI: {"A": 100, "B": 200}, MON: {"A": 98, "C": 45}}
    steps = {MON: {"trades": [("B", "SELL", 5, 190), ("C", "BUY", 4, 50),
                              ("D", "BUY", 2, 100), ("D", "SELL", 2, 90)],
                   "cash": -5.0}}
    # initial book is financed so that C(d0) = -1000: seed with a deposit of 0 -> cash = -2000
    # (A 1000 + B 1000); override below by shifting cash via a first-day "cash" is not allowed,
    # so build rows then re-base cash by +1000.
    t, a, dl = world(init, steps, closes)
    a = a.copy()
    a["cash_balance"] += 1000
    a["net_equity"] = a["gross_book"] + a["cash_balance"]
    a["leverage"] = a["gross_book"] / a["net_equity"]
    return t, a, dl


INT_EV = [{"event_type": "interest", "amount": -5.0, "event_date": "2026-10-12", "raw_code": "MINT",
           "ticker": None, "snaptrade_txn_id": "x1"}]


def test_synthetic_week_numbers():
    t, a, dl = week_world()
    r = recon(t, a, dl, ps=MON, pe=MON, income=INT_EV, flows=[])
    assert r["status"] == "ok"
    assert (r["equity_start"], r["equity_end"], r["equity_change"]) == (1000, 885, -115)
    pp = r["pos_pnl"]
    assert pp["held_through"] == pytest.approx(-20)
    assert pp["sold_leg"] == pytest.approx(-50)
    assert pp["bought_leg"] == pytest.approx(-20)
    assert pp["round_trip"] == pytest.approx(-20)
    assert r["income"]["interest_charged"] == 5 and r["income"]["interest_earned"] == 0
    assert r["residual"]["total"] == pytest.approx(0, abs=1e-9)
    assert r["residual"]["cash"] == pytest.approx(0, abs=1e-9)
    assert r["residual"]["book"] == pytest.approx(0, abs=1e-9)
    assert r["legacy_close_to_close"] == pytest.approx(-20)
    gap = r["equity_change"] - r["legacy_close_to_close"]
    assert gap == pytest.approx(pp["sold_leg"] + pp["bought_leg"] + pp["round_trip"] - 5)
    # leverage split: C(p) = -1000, G(p) = 2000 -> half of held+sold is borrowed
    ls = r["leverage_split"]
    assert ls["borrowed"] == pytest.approx(-35) and ls["own"] == pytest.approx(-35)
    assert ls["unsplit"] == pytest.approx(-40)
    assert ls["borrowed"] + ls["own"] + ls["unsplit"] == pytest.approx(pp["total"], abs=1e-9)


# --- multi-day exact identity ----------------------------------------------

def big_world():
    init = [("A", 10, 100), ("B", 10, 50)]
    closes = {
        FRI: {"A": 100, "B": 50},
        MON: {"A": 103, "B": 54, "E": 21},
        TUE: {"A": 101, "B": 52, "E": 23},
        WED: {"A": 99, "E": 22},
    }
    steps = {
        FRI: {"cash": 500.0},  # E0 = 500, C = -1000: ~3x levered, C < 0 at p in every interval
        MON: {"trades": [("B", "SELL", 4, 55), ("E", "BUY", 3, 20)]},
        TUE: {"trades": [("B", "SELL", 6, 52.5), ("E", "BUY", 2, 21.5),
                         ("D", "BUY", 2, 10), ("D", "SELL", 2, 12)],
              "cash": 100 + 3 - 2},  # deposit 100, dividend 3, interest -2
        WED: {"trades": [("E", "SELL", 5, 22.25), ("A", "BUY", 1, 99.5)]},
    }
    t, a, dl = world(init, steps, closes)
    flows = [{"flow_date": "2026-10-13", "flow_type": "deposit", "amount": 100.0}]
    income = [
        {"event_type": "dividend", "amount": 3.0, "event_date": "2026-10-13", "ticker": "A", "snaptrade_txn_id": "d1"},
        {"event_type": "interest", "amount": -2.0, "event_date": "2026-10-13", "ticker": None, "snaptrade_txn_id": "i1"},
    ]
    return t, a, dl, flows, income


def test_exact_identity_multiday():
    t, a, dl, flows, income = big_world()
    r = recon(t, a, dl, flows=flows, income=income)
    assert r["status"] == "ok"
    pp = r["pos_pnl"]
    F, I = r["flows"]["net"], 3 - 2
    assert F == 100
    assert pp["total"] == pytest.approx(r["equity_change"] - F - I, abs=1e-9)
    assert r["residual"]["total"] == pytest.approx(0, abs=0.005)
    assert r["residual"]["cash"] == pytest.approx(0, abs=0.005)
    assert abs(r["book_gap_start"]) < 1e-9 and abs(r["book_gap_end"]) < 1e-9
    assert sum(v["total"] for v in r["per_ticker"].values()) == pytest.approx(pp["total"], abs=1e-9)
    assert r["per_ticker"]["B"]["sold_usd"] == pytest.approx(4 * 55 + 6 * 52.5)
    assert r["unpriced"] == []
    assert r["coverage"] == {"trading_days": 3, "account_days_valid": 3, "snapshot_days": 3}


def test_agreement_oracle_vs_daily_pnl():
    t, a, dl, flows, income = big_world()
    hold = {FRI: {"A": 10, "B": 10},
            MON: {"A": 10, "B": 6, "E": 3},
            TUE: {"A": 10, "E": 5},
            WED: {"A": 11}}
    cl = {FRI: {"A": 100, "B": 50}, MON: {"A": 103, "B": 54, "E": 21},
          TUE: {"A": 101, "B": 52, "E": 23}, WED: {"A": 99, "E": 22}}
    days = [FRI, MON, TUE, WED]
    tdf = t.copy()
    for p, d in zip(days, days[1:]):
        today = [{"action": r.action, "shares": r.shares, "price": r.price}
                 for r in tdf.itertuples() if r.traded_at.startswith(d.isoformat())]
        held = [{"ticker": k, "shares": v, "price": cl[d][k]} for k, v in hold[d].items()]
        base = {k: {"shares": v, "close": cl[p][k]} for k, v in hold[p].items()}
        oracle = compute_positions_day_pnl(held, base, today, 1.0)["day_pnl"]
        r = recon(t, a, dl, ps=d, pe=d)
        assert r["d0"] == p
        assert r["pos_pnl"]["total"] == pytest.approx(oracle, abs=0.006)


# --- sentinels --------------------------------------------------------------

def test_pre_coverage_when_d0_not_valid():
    t, a, dl = week_world()
    a2 = a[a["snapshot_date"] != FRI.isoformat()]
    r = recon(t, a2, dl, ps=MON, pe=MON, income=INT_EV, flows=[])
    assert r["status"] == "pre_coverage"
    assert r["residual"] is None and r["equity_change"] is None
    assert r["pos_pnl"]["held_through"] == pytest.approx(-20)  # holdings-only still computed
    stale = a.copy()
    stale.loc[stale["snapshot_date"] == FRI.isoformat(), "cash_as_of"] = f"{FRI.isoformat()}T10:00:00+00:00"
    assert recon(t, stale, dl, ps=MON, pe=MON, income=INT_EV, flows=[])["status"] == "pre_coverage"


def test_offline_and_none_lines():
    t, a, dl = week_world()
    assert recon(None, a, dl)["status"] == "offline"
    assert recon(t, None, dl)["status"] == "offline"
    r = recon(t, a, dl, ps=MON, pe=MON, income=INT_EV, flows=None)
    assert r["flows"] is None and r["residual"] is None and r["status"] == "partial"
    r = recon(t, a, dl, ps=MON, pe=MON, income=None, flows=[])
    assert r["income"] is None and r["residual"] is None
    assert recon(t, a, dl, ps=MON, pe=MON, income=INT_EV, flows=[])["residual"] is not None


def test_missing_close_is_unpriced_never_zero():
    t, a, dl = week_world()
    dl2 = dl[~((dl["ticker"] == "C") & (dl["snapshot_date"] == MON.isoformat()))]
    r = recon(t, a, dl2, ps=MON, pe=MON, income=INT_EV, flows=[])
    assert ("C", MON) in r["unpriced"]
    assert r["status"] == "partial"
    assert r["pos_pnl"]["bought_leg"] == 0 and "C" not in {k for k, v in r["per_ticker"].items() if v["total"]}
    assert r["pos_pnl"]["held_through"] == pytest.approx(-20)  # others unaffected
    # the excluded P&L is not hidden: it lands in the unexplained line
    assert r["residual"]["total"] == pytest.approx(-20)


# --- boundaries / dating ----------------------------------------------------

def test_window_boundaries_and_start_book():
    t, a, dl = week_world()
    flows = [{"flow_date": FRI.isoformat(), "flow_type": "deposit", "amount": 500.0},
             {"flow_date": MON.isoformat(), "flow_type": "deposit", "amount": 7.0},
             {"flow_date": "2026-10-13", "flow_type": "deposit", "amount": 900.0},
             {"flow_date": MON.isoformat(), "flow_type": "baseline", "amount": 9999.0}]
    inc = INT_EV + [{"event_type": "interest", "amount": -100.0, "event_date": FRI.isoformat(),
                     "snaptrade_txn_id": "x0"},
                    {"event_type": "dividend", "amount": 9.0, "event_date": "2026-10-13",
                     "snaptrade_txn_id": "x2"}]
    r = recon(t, a, dl, ps=MON, pe=MON, income=inc, flows=flows)
    assert r["flows"] == {"net": 7.0, "n": 1}               # d0 excluded, d1 included, baseline never
    assert r["income"]["interest_charged"] == 5             # d0 event excluded
    assert r["income"]["dividends"] == 0                    # after d1 excluded
    # trades dated d0 are in the starting book, not the window
    t2 = pd.concat([t, pd.DataFrame([{"id": 99, "ticker": "Z", "action": "BUY", "shares": 1,
                                      "price": 10, "traded_at": _ts(FRI)}])])
    r2 = recon(t2, a, dl, ps=MON, pe=MON, income=INT_EV, flows=[])
    assert "Z" not in r2["per_ticker"] or r2["per_ticker"]["Z"]["bought_usd"] == 0


def test_et_dating_and_weekend_import_folds_into_monday():
    t, a, dl = week_world()
    # a 21:30 ET Monday trade is 01:30Z Tuesday -> still Monday
    tl = t.copy()
    tl.loc[(tl["ticker"] == "B") & (tl["action"] == "SELL"), "traded_at"] = "2026-10-13T01:30:00+00:00"
    r = recon(tl, a, dl, ps=MON, pe=MON, income=INT_EV, flows=[])
    assert r["pos_pnl"]["sold_leg"] == pytest.approx(-50)
    # a Saturday trade belongs to the Fri->Mon interval
    ts = t.copy()
    ts.loc[(ts["ticker"] == "B") & (ts["action"] == "SELL"), "traded_at"] = "2026-10-10T16:00:00+00:00"
    r = recon(ts, a, dl, ps=MON, pe=MON, income=INT_EV, flows=[])
    assert r["pos_pnl"]["sold_leg"] == pytest.approx(-50)
    assert r["per_ticker"]["B"]["sold_usd"] == pytest.approx(950)


# --- splits -----------------------------------------------------------------

def _split_world():
    trades = pd.DataFrame([
        {"id": 1, "ticker": "S", "action": "BUY", "shares": 10, "price": 100, "traded_at": _ts(D("2026-09-01"))},
        {"id": 2, "ticker": "S", "action": "SPLIT", "shares": 20, "price": 50, "traded_at": _ts(MON)},
    ])
    acct = pd.DataFrame([_acct(FRI, 1000, 0), _acct(MON, 1040, 0)])
    return trades, acct


def test_split_in_window_is_exact():
    trades, acct = _split_world()
    daily = pd.DataFrame([{"snapshot_date": FRI.isoformat(), "ticker": "S", "shares": 10, "close_price": 100},
                          {"snapshot_date": MON.isoformat(), "ticker": "S", "shares": 20, "close_price": 52}])
    r = recon(trades, acct, daily, ps=MON, pe=MON)
    assert r["pos_pnl"]["total"] == pytest.approx(40)       # 20*52 - 10*100, not a fabricated -ish halving
    assert r["pos_pnl"]["total"] == pytest.approx(r["equity_change"])
    assert r["residual"]["total"] == pytest.approx(0, abs=1e-9)


def test_historical_close_fallback_refused_for_split_ticker():
    trades, acct = _split_world()
    daily = pd.DataFrame([{"snapshot_date": FRI.isoformat(), "ticker": "S", "shares": 10, "close_price": 100}])
    hist = {"S": {MON: 52.0}}
    r = recon(trades, acct, daily, ps=MON, pe=MON, historical_closes=hist)
    assert ("S", MON) in r["unpriced"]
    # a non-split ticker may use the fallback
    t, a, dl = week_world()
    dl2 = dl[~((dl["ticker"] == "C") & (dl["snapshot_date"] == MON.isoformat()))]
    r2 = recon(t, a, dl2, ps=MON, pe=MON, income=INT_EV, flows=[], historical_closes={"C": {"2026-10-12": 45.0}})
    assert r2["unpriced"] == [] and r2["pos_pnl"]["bought_leg"] == pytest.approx(-20)


# --- leverage split ---------------------------------------------------------

def test_leverage_parts_sum_and_cash_positive_day():
    t, a, dl, flows, income = big_world()
    r = recon(t, a, dl, flows=flows, income=income)
    ls = r["leverage_split"]
    assert ls["borrowed"] + ls["own"] + ls["unsplit"] == pytest.approx(r["pos_pnl"]["total"], abs=1e-9)
    assert ls["days_split"] == 3 and ls["days_unsplit"] == 0
    # hand-computed borrowed = sum over intervals of X_i * max(0, -C(p)) / G(p),
    # X = held_through + sold_leg: MON 74 @ 1000/1500, TUE -23 @ 840/1417, WED -23.75 @ 463/1125
    expect = 74 * 1000 / 1500 + (-23) * 840 / 1417 + (-23.75) * 463 / 1125
    assert ls["borrowed"] != 0
    assert ls["borrowed"] == pytest.approx(expect, abs=1e-9)
    # cash-positive at p -> nothing borrowed (every interval genuinely cash-positive)
    a2 = a.copy()
    a2["cash_balance"] = 500.0
    a2["net_equity"] = a2["gross_book"] + 500.0
    r2 = recon(t, a2, dl, flows=flows, income=income)
    assert r2["leverage_split"]["days_split"] == 3
    assert r2["leverage_split"]["borrowed"] == 0
    assert r2["leverage_split"]["own"] + r2["leverage_split"]["unsplit"] == pytest.approx(r2["pos_pnl"]["total"])


def test_invalid_row_at_p_goes_unsplit_total_unchanged():
    t, a, dl, flows, income = big_world()
    base = recon(t, a, dl, flows=flows, income=income)
    a2 = a.copy()
    a2.loc[a2["snapshot_date"] == MON.isoformat(), "cash_as_of"] = f"{MON.isoformat()}T10:00:00+00:00"
    r = recon(t, a2, dl, flows=flows, income=income)
    assert r["pos_pnl"]["total"] == pytest.approx(base["pos_pnl"]["total"])
    assert r["leverage_split"]["days_unsplit"] == 1 and r["leverage_split"]["days_split"] == 2
    ls = r["leverage_split"]
    assert ls["borrowed"] + ls["own"] + ls["unsplit"] == pytest.approx(r["pos_pnl"]["total"], abs=1e-9)
    assert ls["unsplit"] != pytest.approx(base["leverage_split"]["unsplit"])


# --- interest capture gap (C0 finding) --------------------------------------

THU29, FRI30, SAT31 = D("2026-10-29"), D("2026-10-30"), D("2026-10-31")


def _levered_world():
    """Levered book spanning the end of October (d0 = Thu 10-29)."""
    closes = {THU29: {"A": 100}, FRI30: {"A": 101}}
    t, a, dl = world([("A", 10, 100)], {}, closes)
    return t, a, dl


AUG = [{"event_type": "interest", "amount": -30.0, "event_date": "2026-08-31", "snaptrade_txn_id": "a"}]


def _gap(t, a, dl, pe, income=AUG):
    return recon(t, a, dl, ps=FRI30, pe=pe, flows=[], income=income)


def test_interest_gap_month_just_ended():
    t, a, dl = _levered_world()
    r = _gap(t, a, dl, SAT31)                  # October has ended by period_end
    assert r["income"]["interest_coverage_gap_months"] == ["2026-10"]
    assert r["income"]["last_interest_charged_month"] == "2026-08"
    chips = {c["key"]: c for c in pa.trust_strip(r, None)}
    assert chips["interest"]["state"] == "warn" and "2026-08" in chips["interest"]["label"]
    md = pa.format_period_markdown({"period_start": FRI30, "period_end": SAT31, "recon": r, "leverage": {}})
    assert "2026-10" in md and "not recorded" in md


def test_interest_gap_never_flags_unfinished_month():
    t, a, dl = _levered_world()
    # period ends Fri 10-30: October is not over yet -> no gap
    assert _gap(t, a, dl, FRI30)["income"]["interest_coverage_gap_months"] == []
    # a mid-October week -> no gap either
    t2, a2, dl2, flows, income = big_world()
    assert recon(t2, a2, dl2, flows=flows, income=AUG)["income"]["interest_coverage_gap_months"] == []


def test_interest_gap_none_when_unlevered_or_covered():
    t, a, dl = _levered_world()
    a2 = a.copy()
    a2["cash_balance"] = 5000.0
    a2["net_equity"] = a2["gross_book"] + 5000.0
    assert _gap(t, a2, dl, SAT31)["income"]["interest_coverage_gap_months"] == []
    # a charge already recorded in October (or later) -> no gap
    oct_ev = AUG + [{"event_type": "interest", "amount": -9.0, "event_date": "2026-10-26", "snaptrade_txn_id": "b"}]
    r = _gap(t, a, dl, SAT31, oct_ev)
    assert r["income"]["interest_coverage_gap_months"] == []
    assert r["income"]["last_interest_charged_month"] == "2026-10"
    # positive (earned) interest does not count as a margin charge
    earned = [{"event_type": "interest", "amount": 4.0, "event_date": "2026-10-01", "snaptrade_txn_id": "c"}]
    assert _gap(t, a, dl, SAT31, earned)["income"]["interest_coverage_gap_months"] == ["2026-10"]


# --- flows chip -------------------------------------------------------------

def test_flows_chip_never_ok_when_empty():
    t, a, dl = week_world()
    r = recon(t, a, dl, ps=MON, pe=MON, income=INT_EV, flows=[])
    c = {x["key"]: x for x in pa.trust_strip(r, None, flows_last_date=None)}
    assert c["flows"]["state"] == "warn" and "ever" in c["flows"]["label"]
    c = {x["key"]: x for x in pa.trust_strip(r, None, flows_last_date="2026-06-25")}
    assert "2026-06-25" in c["flows"]["label"] and c["flows"]["state"] == "warn"
    r_none = recon(t, a, dl, ps=MON, pe=MON, income=INT_EV, flows=None)
    assert {x["key"]: x for x in pa.trust_strip(r_none, None)}["flows"]["state"] == "unknown"
    assert {x["key"]: x for x in pa.trust_strip(None, None)}["unexplained"]["state"] == "unknown"
    health = {x["key"]: x for x in pa.trust_strip(r, None, health_now="ok")}["health_now"]
    assert health["state"] == "ok" and "now" in health["label"]


# --- S3 ---------------------------------------------------------------------

def _lev_df():
    rows = [
        _acct(D("2026-10-12"), 3000, -1500, lev=2.0, cd=-20.0, cushion=900),
        _acct(D("2026-10-13"), 3000, -1500, lev=2.5, cd=-12.9, cushion=500),
        _acct(D("2026-10-14"), 3000, -1500, lev=3.0, cd=-30.0, cushion=1500),
        _acct(D("2026-10-15"), 3000, None, lev=None, cd=None),
    ]
    rows[3]["leverage"] = None
    return pd.DataFrame(rows)


def test_leverage_section_basics():
    s = pa.leverage_section(_lev_df(), MON, D("2026-10-16"), 2.0)
    assert s["status"] == "ok"
    assert s["closest_call"]["date"] == TUE and s["closest_call"]["call_distance_pct"] == -12.9
    assert s["days_above_reference"] == 2            # 2.0 exactly is NOT above (strict >)
    assert s["n_days_recorded"] == 3 and s["n_null_rows"] == 1 and s["n_trading_days"] == 5
    assert s["min"]["leverage"] == 2.0 and s["max"]["date"] == WED
    assert s["start"]["date"] == MON and s["end"]["date"] == WED
    assert s["maintenance_rate"] == 0.25 and s["days_in_call"] == 0


def test_leverage_section_in_call_and_states():
    df = _lev_df()
    df.loc[1, "call_distance_pct"] = 0.0
    df.loc[2, "call_distance_pct"] = 4.0
    s = pa.leverage_section(df, MON, D("2026-10-16"), 2.0)
    assert s["days_in_call"] == 2 and s["closest_call"]["call_distance_pct"] == 4.0
    assert pa.leverage_section(None, MON, WED, 2.0)["status"] == "offline"
    assert pa.leverage_section(df, D("2026-01-05"), D("2026-01-09"), 2.0)["status"] == "empty"


# --- presets ----------------------------------------------------------------

@pytest.mark.parametrize("today,exp", [
    ("2026-10-17", ("2026-10-05", "2026-10-11")),   # Sat
    ("2026-10-18", ("2026-10-12", "2026-10-18")),   # Sun
    ("2026-10-19", ("2026-10-12", "2026-10-18")),   # Mon
    ("2026-10-21", ("2026-10-12", "2026-10-18")),   # Wed
])
def test_last_week(today, exp):
    assert pa.resolve_period("last_week", D(today)) == (D(exp[0]), D(exp[1]))


def test_other_presets():
    assert pa.resolve_period("last_month", D("2027-01-15")) == (D("2026-12-01"), D("2026-12-31"))
    assert pa.resolve_period("last_quarter", D("2027-02-10")) == (D("2026-10-01"), D("2026-12-31"))
    assert pa.resolve_period("this_quarter", D("2026-10-20")) == (D("2026-10-01"), D("2026-10-20"))
    assert pa.resolve_period("this_tax_year", D("2026-10-20")) == (D("2026-01-01"), D("2026-10-20"))
    assert pa.resolve_period("since_tracking", D("2026-10-20"), tracking_start=D("2026-09-11")) == (D("2026-09-11"), D("2026-10-20"))
    assert pa.resolve_period("since_tracking", D("2026-10-20")) is None
    assert pa.resolve_period("custom", D("2026-10-20"), D("2026-10-05"), D("2026-10-01")) is None
    assert pa.resolve_period("custom", D("2026-10-20"), D("2026-10-05"), D("2026-10-30")) is None
    assert pa.resolve_period("custom", D("2026-10-20"), D("2026-10-05"), D("2026-10-07")) == (D("2026-10-05"), D("2026-10-07"))
    assert pa.resolve_period("bogus", D("2026-10-20")) is None


def test_holiday_monday_week():
    assert "2026-09-07" in NYSE_HOLIDAYS
    ps, pe = pa.resolve_period("last_week", D("2026-09-16"))
    assert (ps, pe) == (D("2026-09-07"), D("2026-09-13"))
    trades = pd.DataFrame([{"id": 1, "ticker": "A", "action": "BUY", "shares": 1, "price": 10, "traded_at": _ts(D("2026-08-01"))}])
    r = pa.reconcile_period(period_start=ps, period_end=pe, trades_df=trades,
                            account_snapshots_df=pd.DataFrame([_acct(D("2026-09-04"), 10, 0), _acct(D("2026-09-11"), 11, 0)]),
                            daily_snapshots_df=pd.DataFrame([
                                {"snapshot_date": "2026-09-04", "ticker": "A", "shares": 1, "close_price": 10},
                                {"snapshot_date": "2026-09-08", "ticker": "A", "shares": 1, "close_price": 10},
                                {"snapshot_date": "2026-09-09", "ticker": "A", "shares": 1, "close_price": 10},
                                {"snapshot_date": "2026-09-10", "ticker": "A", "shares": 1, "close_price": 10},
                                {"snapshot_date": "2026-09-11", "ticker": "A", "shares": 1, "close_price": 11}]),
                            flows=[], income_events=[])
    assert r["d0"] == D("2026-09-04") and r["d1"] == D("2026-09-11")
    assert r["pos_pnl"]["total"] == pytest.approx(1.0)


# --- purity -----------------------------------------------------------------

def test_inputs_not_mutated():
    t, a, dl, flows, income = big_world()
    snap = [x.copy() for x in (t, a, dl)]
    fl, inc = copy.deepcopy(flows), copy.deepcopy(income)
    recon(t, a, dl, flows=flows, income=income)
    pa.leverage_section(a, MON, WED, 2.0)
    for x, y in zip((t, a, dl), snap):
        pd.testing.assert_frame_equal(x, y)
    assert flows == fl and income == inc


def test_import_isolation():
    src = (Path(__file__).parent.parent / "stock_analyzer" / "period_attribution.py").read_text(encoding="utf-8")
    for mod in ("risk", "exit_advisor", "risk_advisor", "scoring", "ranking", "streamlit", "db"):
        assert not re.search(rf"^\s*(from|import)\s+(stock_analyzer\.)?{mod}\b", src, re.M), mod
        assert not re.search(rf"from stock_analyzer import[^\n]*\b{mod}\b", src), mod
    code = ("import sys; import stock_analyzer.period_attribution; "
            "bad=[m for m in ('stock_analyzer.risk','stock_analyzer.exit_advisor','stock_analyzer.risk_advisor',"
            "'stock_analyzer.scoring','stock_analyzer.ranking','stock_analyzer.db','streamlit') if m in sys.modules]; "
            "print('BAD', bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(Path(__file__).parent.parent))
    assert "BAD []" in out.stdout, out.stdout + out.stderr


# --- exports ----------------------------------------------------------------

def test_markdown_has_no_unescaped_dollar_and_csv_shape():
    t, a, dl, flows, income = big_world()
    r = recon(t, a, dl, flows=flows, income=income)
    lev = pa.leverage_section(_lev_df(), MON, D("2026-10-16"), 2.0)
    report = {"period_start": MON, "period_end": WED, "recon": r, "leverage": lev,
              "trust": pa.trust_strip(r, lev, health_now="ok")}
    md = pa.format_period_markdown(report)
    assert "\\$" in md
    assert not re.search(r"(?<!\\)\$", md)
    for line in md.splitlines():
        stars = line.count("**")
        assert stars in (0, 2), line
    df = pa.format_period_csv(report)
    assert list(df.columns) == ["section", "item", "value", "note"]
    assert {"s1", "s3"} <= set(df["section"])
    assert "offline" in pa.format_period_markdown({"recon": {"status": "offline"}}).lower()


# --- canonical income (live FEE = margin interest) --------------------------

def test_fee_mint_pair_counted_once_as_interest_and_lone_fee_reclassified():
    t, a, dl = week_world()
    pair = [
        {"id": 111, "event_type": "fee", "raw_code": "FEE", "ticker": None, "amount": -61.01,
         "event_date": "2026-10-12", "snaptrade_txn_id": "live111"},
        {"id": 113, "event_type": "interest", "raw_code": "MINT", "ticker": None, "amount": -61.01,
         "event_date": "2026-10-12", "snaptrade_txn_id": "csv:2026-10-12:MINT::-6101"},
    ]
    r = recon(t, a, dl, ps=MON, pe=MON, income=pair, flows=[])
    assert r["income"]["interest_charged"] == pytest.approx(61.01)   # once, not twice
    assert r["income"]["fees"] == 0
    lone = [pair[0]]
    r = recon(t, a, dl, ps=MON, pe=MON, income=lone, flows=[])
    assert r["income"]["interest_charged"] == pytest.approx(61.01)
    assert r["income"]["fees"] == 0 and r["income"]["n_reclassified_from_fee"] == 1
    md = pa.format_period_markdown({"period_start": MON, "period_end": MON, "recon": r, "leverage": {}})
    assert "counted as margin interest" in md
    # a live FEE this month is interest: no coverage gap for the month it sits in
    assert r["income"]["interest_coverage_gap_months"] == []
    assert r["income"]["last_interest_charged_month"] == "2026-10"


# --- review round: B1 / B2 / B3 and follow-ups -------------------------------

def test_sell_from_carried_shares_first():
    """B1: A held 10; same interval sell 4 @95 and buy 3 @97, close 100 -> 98.
    The sell consumes carried shares first (q1 = min(q_sell, s_p))."""
    closes = {FRI: {"A": 100}, MON: {"A": 98}}
    steps = {MON: {"trades": [("A", "SELL", 4, 95), ("A", "BUY", 3, 97)]}}
    t, a, dl = world([("A", 10, 100)], steps, closes)
    a = a.copy()
    a["cash_balance"] += 5000
    a["net_equity"] = a["gross_book"] + a["cash_balance"]
    r = recon(t, a, dl, ps=MON, pe=MON, flows=[], income=[])
    pa_ = r["per_ticker"]["A"]
    assert pa_["held_through"] == pytest.approx(-12)   # 6 * (98 - 100)
    assert pa_["sold_leg"] == pytest.approx(-20)       # 4 * (95 - 100)
    assert pa_["bought_leg"] == pytest.approx(3)       # 3 * (98 - 97)
    assert pa_["round_trip"] == 0
    assert r["residual"]["total"] == pytest.approx(0, abs=1e-9)


def test_trade_dated_d0_is_start_book_not_cash_window():
    """B2: a trade on d0 (Fri) is in the starting book; if it leaked into the
    window's S-B, R_cash would be off by its cost."""
    closes = {FRI: {"A": 100, "Z": 10}, MON: {"A": 98, "Z": 11}}
    steps = {FRI: {"trades": [("Z", "BUY", 1, 10)]}}
    t, a, dl = world([("A", 10, 100)], steps, closes)
    r = recon(t, a, dl, ps=MON, pe=MON, flows=[], income=[])
    assert r["pos_pnl"]["held_through"] == pytest.approx(-20 + 1)
    assert r["residual"]["cash"] == pytest.approx(0, abs=1e-9)
    assert r["residual"]["total"] == pytest.approx(0, abs=1e-9)


def test_pre_coverage_reasons_and_chips():
    t, a, dl = week_world()
    # no valid start record
    r = recon(t, a[a["snapshot_date"] != FRI.isoformat()], dl, ps=MON, pe=MON, income=INT_EV, flows=[])
    assert r["pre_coverage_reason"] == "no_start_record"
    c = {x["key"]: x for x in pa.trust_strip(r, None)}["coverage"]
    assert "before the period" in c["label"] and c["state"] == "warn"
    # d0 valid, end-of-day record not written yet
    r = recon(t, a[a["snapshot_date"] <= FRI.isoformat()], dl, ps=MON, pe=MON, income=INT_EV, flows=[])
    assert r["status"] == "pre_coverage" and r["pre_coverage_reason"] == "no_end_record_yet"
    c = {x["key"]: x for x in pa.trust_strip(r, None)}["coverage"]
    assert "may not have landed yet" in c["label"] and "17:30" not in c["label"]
    assert "before the period" not in c["label"] and str(FRI) in c["label"]
    assert "17:30" not in pa.format_period_markdown({"recon": r})
    # a range with no trading sessions
    r = recon(t, a, dl, ps=D("2026-10-10"), pe=D("2026-10-11"), income=INT_EV, flows=[])
    assert r["pre_coverage_reason"] == "no_sessions"
    c = {x["key"]: x for x in pa.trust_strip(r, None)}["coverage"]
    assert "No trading sessions" in c["label"]
    assert recon(t, a, dl, ps=MON, pe=MON, income=INT_EV, flows=[])["pre_coverage_reason"] is None


def test_interest_gap_is_per_month_presence():
    sess = [D("2026-09-29") + timedelta(days=i) for i in range(32)]
    sess = [d for d in sess if d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS and d <= D("2026-10-30")]
    t, a, dl = world([("A", 10, 100)], {}, {d: {"A": 100} for d in sess})
    a = a.copy()
    a["cash_balance"] = -500.0
    a["net_equity"] = a["gross_book"] - 500.0
    ev = [{"event_type": "interest", "amount": -30.0, "event_date": "2026-08-31", "snaptrade_txn_id": "a"},
          {"event_type": "interest", "amount": -9.0, "event_date": "2026-10-26", "snaptrade_txn_id": "b"}]
    r = recon(t, a, dl, ps=D("2026-09-30"), pe=SAT31, flows=[], income=ev)
    assert r["income"]["interest_coverage_gap_months"] == ["2026-09"]   # Aug + Oct recorded, Sep missing


def test_unpriced_labels_unexplained_and_partial_status():
    t, a, dl = week_world()
    dl2 = dl[~((dl["ticker"] == "C") & (dl["snapshot_date"] == MON.isoformat()))]
    r = recon(t, a, dl2, ps=MON, pe=MON, income=INT_EV, flows=[])
    assert "unpriced" in {x["key"]: x for x in pa.trust_strip(r, None)}["unexplained"]["label"]
    assert "includes the P&L of unpriced" in pa.format_period_markdown({"recon": r})
    r_none = recon(t, a, dl, ps=MON, pe=MON, income=INT_EV, flows=None)
    assert r_none["status"] == "partial"


def test_journal_order_inconsistency_flagged_not_unpriced():
    t, a, dl = week_world()
    q = pd.DataFrame([
        {"id": 50, "ticker": "Q", "action": "SELL", "shares": 2, "price": 90, "traded_at": "2026-10-12T20:00:00+00:00"},
        {"id": 51, "ticker": "Q", "action": "BUY", "shares": 2, "price": 100, "traded_at": "2026-10-12T20:00:00+00:00"}])
    t4 = pd.concat([t, q])
    r = recon(t4, a, dl, ps=MON, pe=MON, income=INT_EV, flows=[])
    assert r["journal_inconsistent"] == [("Q", MON)]
    assert all(u[0] != "Q" for u in r["unpriced"])
    assert r["status"] == "partial"
    c = {x["key"]: x for x in pa.trust_strip(r, None)}["journal"]
    assert c["state"] == "warn"
    assert "inconsistent" in pa.format_period_markdown({"recon": r})


def test_non_positive_equity_at_p_is_not_split():
    closes = {FRI: {"A": 100}, MON: {"A": 98}}
    t, a, dl = world([("A", 10, 100)], {}, closes)      # fully financed: E(d0) = 0
    r = recon(t, a, dl, ps=MON, pe=MON, flows=[], income=[])
    ls = r["leverage_split"]
    assert ls["days_unsplit"] == 1 and ls["days_split"] == 0 and ls["borrowed"] == 0
    assert ls["unsplit"] == pytest.approx(r["pos_pnl"]["total"])


def test_trust_strip_leverage_chip_always_present_and_null_call_rows_counted():
    t, a, dl = week_world()
    r = recon(t, a, dl, ps=MON, pe=MON, income=INT_EV, flows=[])
    for lev in (None, {}, {"status": "ok"}):
        c = {x["key"]: x for x in pa.trust_strip(r, lev)}
        assert c["leverage_records"]["state"] == "unknown"
    df = _lev_df()
    s = pa.leverage_section(df, MON, D("2026-10-16"), 2.0)
    assert s["n_null_call_rows"] == 0
    df.loc[0, "call_distance_pct"] = None
    assert pa.leverage_section(df, MON, D("2026-10-16"), 2.0)["n_null_call_rows"] == 1
    assert pa._CENT_TOL == 0.005


def test_records_present_but_failing_cash_check_reason():
    t, a, dl, flows, income = big_world()
    a2 = a.copy()
    a2.loc[a2["snapshot_date"] > FRI.isoformat(), "cash_as_of"] = "2026-10-09T21:30:00+00:00"
    r = recon(t, a2, dl, flows=flows, income=income)
    assert r["status"] == "pre_coverage" and r["pre_coverage_reason"] == "no_valid_end_record"
    c = {x["key"]: x for x in pa.trust_strip(r, None)}["coverage"]
    assert "none passed the after-close cash check" in c["label"] and "landed" not in c["label"]


def test_phantom_shares_later_intervals_are_journal_flagged_not_unpriced():
    closes = {FRI: {"A": 100}, MON: {"A": 98}, TUE: {"A": 101}}
    t, a, dl = world([("A", 10, 100)], {}, closes)
    q = pd.DataFrame([
        {"id": 50, "ticker": "Q", "action": "SELL", "shares": 2, "price": 90, "traded_at": "2026-10-12T20:00:00+00:00"},
        {"id": 51, "ticker": "Q", "action": "BUY", "shares": 2, "price": 100, "traded_at": "2026-10-12T20:00:00+00:00"}])
    r = recon(pd.concat([t, q]), a, dl, ps=MON, pe=TUE, flows=[], income=[])
    assert r["journal_inconsistent"] == [("Q", MON), ("Q", TUE)]
    assert all(u[0] != "Q" for u in r["unpriced"])
    assert "journal-inconsistent" in pa.format_period_markdown({"recon": r})
    assert r["residual"] is not None
    assert "journal-excluded" in {x["key"]: x for x in pa.trust_strip(r, None)}["unexplained"]["label"]
