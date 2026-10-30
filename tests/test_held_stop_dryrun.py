"""Offline tests for scripts/held_stop_dryrun.py (synthetic frames, no network / DB)."""
import importlib.util
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import held_stop as hs
from stock_analyzer import portfolio, risk
from stock_analyzer.constants import ATR_STOP_MULT

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "held_stop_dryrun.py"


def _load():
    spec = importlib.util.spec_from_file_location("held_stop_dryrun_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dr = _load()


def frame(n=150, start="2026-01-05", gap_at=None, gap_to=85.0, steady=False):
    idx = pd.bdate_range(start, periods=n)
    i = np.arange(n)
    close = 100 * (1.002 ** i) if steady else 100 + 0.5 * np.sin(i / 5)
    close = np.array(close, dtype=float)
    if gap_at is not None:
        close[gap_at:] = gap_to
    h, lo = close * 1.005, close * 0.995
    if gap_at is not None:
        h[gap_at], lo[gap_at] = gap_to * 1.01, gap_to * 0.99
    return pd.DataFrame({"Open": close, "High": h, "Low": lo, "Close": close}, index=idx)


def ep(start, fb=100.0, last_buy=None):
    return {"status": "ok", "start": start, "first_buy_price": fb, "split_ratio": 1.0,
            "reason": "", "last_buy": last_buy or start}


# -- V0 ----------------------------------------------------------------------

def test_legacy_stop_equals_app_formula_with_partial_bar():
    f = frame(n=60)           # last row plays "today's partial bar"
    price = float(f["Close"].iloc[-1])
    atr_stop, _ = risk.atr_stop_loss(f, multiplier=ATR_STOP_MULT)
    want, label = portfolio.protective_stop(price, 99.0, atr_stop)
    got = dr.legacy_stop(f, price, 99.0, None)
    assert got["stop"] == want and got["type"] == label
    assert got["gap"] == round((price - want) / price * 100, 1)


def test_legacy_manual_wins_only_when_tighter():
    f = frame(n=60)
    price = float(f["Close"].iloc[-1])
    base = dr.legacy_stop(f, price, 99.0, None)["stop"]
    win = dr.legacy_stop(f, price, 99.0, {"stop_price": base + 1})
    lose = dr.legacy_stop(f, price, 99.0, {"stop_price": base - 1})
    assert win["type"] == "Manual" and win["stop"] == round(base + 1, 2)
    assert lose["type"] != "Manual" and lose["stop"] == base


# -- the #26 defect reproduced ----------------------------------------------

def test_gap_down_is_newly_breached_under_a_not_under_v0():
    n = 131
    f = frame(n=n, gap_at=n - 1)          # last bar = today's gapped partial bar
    as_of = f.index[-1].date()
    entry = f.index[70].date()
    price = float(f["Close"].iloc[-1])
    row = dr.today_row("XYZ", 10.0, 100.0, ep(entry), f, f, None, as_of, as_of, (2.0,),
                       live_price=price)
    assert row["v0"]["breached"] is False          # the stop chased the price down
    cell = row["cells"][("a", 2.0)]
    assert "NEWLY_BREACHED_CLOSE" in cell["flags"]
    assert cell["stop"] > price


def test_variant_b_drops_the_entry_anchor_and_does_not_mutate_episode():
    e = ep(date(2026, 3, 2))
    b = dr.episode_variant(e, "b")
    assert b["first_buy_price"] is None and e["first_buy_price"] == 100.0
    assert dr.episode_variant(e, "c")["first_buy_price"] == 100.0
    f = frame(n=140)
    as_of = f.index[-1].date()
    cells = dr.variant_cells(f, ep(f.index[70].date()), 100.0, as_of, f.index[100].date(),
                             (3.0,), None, 100.0)
    assert "entry anchor dropped" in cells[("b", 3.0)]["reason"]
    assert cells[("b", 3.0)]["auto_stop"] is not None
    assert ("a", 3.0) in cells and ("c", 3.0) in cells


def test_variants_b_c_skipped_without_trail_start():
    f = frame(n=140)
    cells = dr.variant_cells(f, ep(f.index[70].date()), 100.0, f.index[-1].date(), None,
                             (3.0,), None, 100.0)
    assert set(cells) == {("a", 3.0)}


# -- flags -------------------------------------------------------------------

def test_manual_now_loses_flips():
    v0 = {"type": "Manual", "gap": 5.0, "breached": False}
    assert "MANUAL_NOW_LOSES" in dr.flags({"stop": 90.0, "gap": 10.0, "manual_won": False},
                                          v0, 100.0)
    assert "MANUAL_NOW_LOSES" not in dr.flags({"stop": 96.0, "gap": 4.0, "manual_won": True},
                                              v0, 100.0)
    assert "MANUAL_NOW_LOSES" not in dr.flags({"stop": 90.0, "gap": 10.0, "manual_won": False},
                                              {**v0, "type": "ATR Stop"}, 100.0)


def test_low_vs_close_breach_and_v0_already_breached():
    v0 = {"type": "ATR Stop", "gap": 9.0, "breached": False}
    cell = {"stop": 96.0, "gap": 4.0, "manual_won": False, "low": 95.0, "low_stop": 96.0}
    fl = dr.flags(cell, v0, 100.0)
    assert "NEWLY_BREACHED_LOW" in fl and "NEWLY_BREACHED_CLOSE" not in fl
    fl2 = dr.flags(cell, {**v0, "breached": True}, 100.0)
    assert "NEWLY_BREACHED_LOW" not in fl2
    assert dr.flags({"stop": None, "gap": None}, v0, 100.0) == []


def test_band_crossings_use_constants():
    from stock_analyzer.constants import (ADD_WINNER_MIN_GAP_PCT, APPROACHING_STOP_GAP_PCT,
                                          POSITION_AT_RISK_GAP_PCT)
    far = {"type": "ATR Stop", "gap": ADD_WINNER_MIN_GAP_PCT + 5, "breached": False}
    fl = dr.flags({"stop": 1.0, "gap": POSITION_AT_RISK_GAP_PCT - 0.5}, far, 100.0)
    assert "CROSSES_AT_RISK" in fl and "LOSES_ADD_ROOM" in fl
    fl = dr.flags({"stop": 1.0, "gap": APPROACHING_STOP_GAP_PCT - 0.5}, far, 100.0)
    assert "CROSSES_APPROACHING" in fl and "CROSSES_AT_RISK" not in fl


# -- round trips -------------------------------------------------------------

def _trades(rows):
    return pd.DataFrame([{"id": i + 1, "ticker": t, "action": a, "shares": s, "price": p,
                          "traded_at": ts} for i, (t, a, s, p, ts) in enumerate(rows)])


def test_closed_round_trips_partial_sell_and_reentry_et_dates():
    df = _trades([
        ("AAA", "BUY", 10, 100, "2026-01-06T02:00:00+00:00"),   # ET: Jan 5
        ("AAA", "BUY", 5, 110, "2026-01-12T15:00:00+00:00"),
        ("AAA", "SELL", 6, 120, "2026-02-02T15:00:00+00:00"),   # partial
        ("AAA", "SELL", 9, 90, "2026-02-10T15:00:00+00:00"),    # closes
        ("AAA", "BUY", 4, 120, "2026-03-01T15:00:00+00:00"),    # re-entry
        ("AAA", "SELL", 4, 130, "2026-03-10T15:00:00+00:00"),
        ("BBB", "BUY", 3, 50, "2026-04-01T15:00:00+00:00"),     # still open
    ])
    trips = dr.closed_round_trips(df)
    assert [(t["ticker"], t["entry_date"], t["exit_date"]) for t in trips] == [
        ("AAA", date(2026, 1, 5), date(2026, 2, 10)),
        ("AAA", date(2026, 3, 1), date(2026, 3, 10))]
    t1 = trips[0]
    assert t1["first_buy_price"] == 100 and t1["shares"] == 15
    assert t1["avg_cost"] == pytest.approx((10 * 100 + 5 * 110) / 15)
    assert t1["exit_price"] == pytest.approx((6 * 120 + 9 * 90) / 15)
    assert t1["realized_ret"] == pytest.approx(t1["exit_price"] / t1["avg_cost"] - 1)
    assert dr.closed_round_trips(df, tickers=["BBB"]) == []
    assert len(dr.closed_round_trips(df, since=date(2026, 3, 1))) == 1
    assert dr.closed_round_trips(None) == []


def test_round_trip_with_split_is_flagged():
    df = _trades([
        ("AAA", "BUY", 10, 100, "2026-01-06T15:00:00+00:00"),
        ("AAA", "SPLIT", 20, 0, "2026-01-20T15:00:00+00:00"),
        ("AAA", "SELL", 20, 60, "2026-02-10T15:00:00+00:00"),
    ])
    trips = dr.closed_round_trips(df)
    assert len(trips) == 1 and trips[0]["has_split"] is True


# -- first_fire --------------------------------------------------------------

def _rt(f, entry_i, exit_i):
    entry, exit_ = f.index[entry_i].date(), f.index[exit_i].date()
    return {"ticker": "XYZ", "entry_date": entry, "exit_date": exit_, "buys": [(entry, 10.0, 100.0)],
            "sells": [(exit_, 10.0, 90.0)], "first_buy_price": 100.0, "avg_cost": 100.0,
            "exit_price": 90.0, "realized_ret": -0.1, "shares": 10.0, "has_split": False}


def test_first_fire_finds_the_gap_down_session():
    f = frame(n=150, gap_at=100)
    fr = dr.prep_frame(f, date(2100, 1, 1))
    res = dr.first_fire(fr, _rt(f, 70, 110), "a", 2.0)
    assert res["fired"] and res["fire_date"] == f.index[100].date()
    assert res["low_fire_date"] == f.index[100].date()
    assert res["sessions_before_exit"] == 10
    assert res["ret_at_fire"] == pytest.approx(85.0 / 100.0 - 1)
    assert res["whipsaw"] is False
    assert res["basis_counts"]


def test_first_fire_none_on_steady_uptrend():
    f = frame(n=150, steady=True)
    fr = dr.prep_frame(f, date(2100, 1, 1))
    res = dr.first_fire(fr, _rt(f, 70, 140), "a", 2.0)
    assert not res["fired"] and not res["low_fired"]
    assert res["sessions"] > 0


def test_attach_ladder_semantics():
    row = {"fired": True, "fire_date": date(2026, 5, 5)}
    out = dr.attach_ladder(dict(row), {"first_trim": date(2026, 5, 8), "first_exit": None}, True)
    assert out["before_trim"] is True and out["before_exit"] is True
    out = dr.attach_ladder(dict(row), {"first_trim": date(2026, 5, 1), "first_exit": None}, True)
    assert out["before_trim"] is False
    out = dr.attach_ladder(dict(row), None, False)
    assert out["before_trim"] is None and out["before_exit"] is None and out["ladder_vs_stop"] is None


def test_ladder_vs_stop_six_categories():
    d = date(2026, 5, 5)

    def cat(fire, trim, exit_):
        row = {"fired": fire is not None, "fire_date": fire}
        return dr.attach_ladder(row, {"first_trim": trim, "first_exit": exit_}, True)["ladder_vs_stop"]
    assert cat(d, date(2026, 5, 9), None) == "stop_first"
    assert cat(d, date(2026, 5, 1), date(2026, 5, 9)) == "ladder_first"
    assert cat(d, None, d) == "same_session"
    assert cat(d, None, None) == "stop_only"
    assert cat(None, d, None) == "ladder_only"
    assert cat(None, None, None) == "neither"


def test_basis_countable_excludes_pre_calendar_post_holiday():
    assert dr.basis_countable(date(2026, 1, 20), date(2026, 1, 16)) is True
    # Fri 2025-11-28 follows Thanksgiving Thu 11-27 (no bar): excluded
    assert dr.basis_countable(date(2025, 11, 28), date(2025, 11, 26)) is False
    assert dr.basis_countable(date(2025, 11, 26), date(2025, 11, 25)) is True


# -- summaries ---------------------------------------------------------------

def test_summarize_today_counts():
    rows = [{"v0": {"stop": None}, "cells": {("a", 2.0): {"flags": ["NEWLY_BREACHED_CLOSE", "NEWLY_BREACHED_LOW"],
                                    "basis": "full"}}},
            {"v0": {"stop": None}, "cells": {("a", 2.0): {"flags": ["MANUAL_NOW_LOSES", "CROSSES_AT_RISK"],
                                    "basis": "partial"}}}]
    s = dr.summarize_today(rows)[("a", 2.0)]
    assert s["holdings"] == 2 and s["newly_breached_close"] == 1 and s["newly_breached_low"] == 1
    assert s["manual_now_loses"] == 1 and s["crosses_at_risk"] == 1
    assert dict(s["basis"]) == {"full": 1, "partial": 1}


def test_summarize_replay_aggregates_split_winners_losers():
    base = dict(variant="a", mult=2.0, low_fired=False, basis_counts={"full": 2},
                fwd5=None, fwd10=None, whipsaw=None, ret_at_fire=None, ret_at_exit=None,
                ret_next_open=None, sessions_before_exit=None, loser=None, fired=False,
                ladder_vs_stop=None)
    rows = [
        {**base, "fired": True, "loser": True, "sessions_before_exit": 4, "whipsaw": True,
         "ret_at_fire": -0.05, "ret_at_exit": -0.10, "ret_next_open": -0.06, "fwd5": 0.02,
         "fwd10": 0.03, "ladder_vs_stop": "stop_first"},
        {**base, "fired": True, "loser": False, "sessions_before_exit": 8, "whipsaw": False,
         "ret_at_fire": 0.00, "ret_at_exit": 0.02, "fwd5": -0.02},
        {**base, "fired": False, "loser": True, "ladder_vs_stop": "neither"},
    ]
    s = dr.summarize_replay(rows)[("a", 2.0)]
    a = s["all"]
    assert a["trips"] == 3 and a["fires"] == 2
    assert a["median_sessions_early"] == 6 and a["n_early"] == 2
    assert a["whipsaw_pct"] == 50.0 and a["n_whipsaw"] == 2
    assert a["mean_saved_pp"] == pytest.approx((5.0 + -2.0) / 2)
    assert a["mean_saved_open_pp"] == pytest.approx(4.0) and a["n_saved_open"] == 1
    assert a["missing_fwd5"] == 0 and a["missing_fwd10"] == 1
    assert a["fwd10_pos_pct"] == 100.0 and a["n_fwd10"] == 1
    assert s["loser"]["trips"] == 2 and s["winner"]["trips"] == 1
    assert s["loser"]["mean_saved_pp"] == pytest.approx(5.0)
    assert s["winner"]["mean_saved_pp"] == pytest.approx(-2.0)
    assert s["losers_n"] == 2 and dict(s["ladder"]) == {"stop_first": 1, "neither": 1}
    assert s["basis"] == {"full": 6}


# -- script hygiene ----------------------------------------------------------

def test_import_does_not_pop_api_key_or_touch_network(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    _load()
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-test"
    src = _PATH.read_text(encoding="utf-8")
    i = src.index('os.environ.pop("ANTHROPIC_API_KEY"')
    assert src[:i].rstrip().endswith('if __name__ == "__main__":')


def test_long_fetch_period_comes_from_history_period_needed(monkeypatch):
    seen = []

    def fake(ticker, period):
        seen.append(period)
        return frame(n=10)
    monkeypatch.setattr(dr, "_fetch_long", fake)
    start = date(2026, 3, 2)
    want = hs.history_period_needed(None, start, None, dr.WARMUP_BARS, as_of=date(2026, 10, 30))
    dr._long_for("XYZ", start, None, date(2026, 10, 30))
    assert seen[0] == want
    seen.clear()
    dr._long_for("XYZ", None, None, date(2026, 10, 30))
    assert seen == ["1y"]


def test_main_refuses_without_db(monkeypatch, capsys):
    from stock_analyzer import db
    monkeypatch.setattr(db, "set_readonly", lambda flag: None)
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert dr.main([]) == 1
    assert "No Supabase credentials" in capsys.readouterr().out


# -- review round: tz-aware frames, price basis, LOW, loosening, replay --------

def _tz(f, tz):
    g = f.copy()
    if tz == "ET":
        g.index = g.index.tz_localize("America/New_York")
    else:  # UTC stamps at the 09:30 ET open (13:30 / 14:30 UTC), same ET date
        g.index = (g.index + pd.Timedelta(hours=14, minutes=30)).tz_localize("UTC")
    return g


@pytest.mark.parametrize("tz", ["ET", "UTC"])
def test_tz_aware_long_frame_through_long_for_today_and_replay(monkeypatch, tz):
    base = frame(n=131, gap_at=130)
    aware = _tz(base, tz)
    monkeypatch.setattr(dr, "_fetch_long", lambda t, period: aware)
    start = base.index[70].date()
    got = dr._long_for("XYZ", start, None, base.index[-1].date())    # used to raise TypeError
    assert got is aware
    as_of = base.index[-1].date()
    r_aware = dr.today_row("XYZ", 10.0, 100.0, ep(start), aware, base, None, as_of, as_of,
                           (2.0,), live_price=85.0)
    r_naive = dr.today_row("XYZ", 10.0, 100.0, ep(start), base, base, None, as_of, as_of,
                           (2.0,), live_price=85.0)
    assert r_aware["cells"][("a", 2.0)]["stop"] == r_naive["cells"][("a", 2.0)]["stop"]
    assert r_aware["latest_bar_date"] == as_of
    f2 = _tz(frame(n=150, gap_at=100), tz)
    fr = dr.prep_frame(f2, date(2100, 1, 1))
    rt = _rt(frame(n=150, gap_at=100), 70, 110)
    assert dr.first_fire(fr, rt, "a", 2.0)["fired"]


def test_long_for_rechecks_period_with_et_first_date(monkeypatch):
    seen = []
    base = frame(n=150)

    def fake(t, period):
        seen.append(period)
        return _tz(base, "ET")
    monkeypatch.setattr(dr, "_fetch_long", fake)
    dr._long_for("XYZ", date(2025, 6, 2), None, date(2026, 10, 30))   # tz-aware compare ran
    assert seen == ["2y"]      # frame starts later than needed (young listing): same label, no refetch


def test_one_price_latest_bar_incl_today_else_live():
    n = 131
    f = frame(n=n, gap_at=n - 1)
    as_of = f.index[-1].date()
    r = dr.today_row("XYZ", 10.0, 100.0, ep(f.index[70].date()), f, f, None, as_of, as_of, (2.0,))
    assert r["price"] == 85.0 and r["price_basis"] == "latest_bar"
    assert r["last_close"] != 85.0                          # last completed session (info)
    assert r["v0"]["gap"] == dr._gap(85.0, r["v0"]["stop"])  # V0 on the SAME price
    r2 = dr.today_row("XYZ", 10.0, 100.0, ep(f.index[70].date()), f, f, None, as_of, as_of,
                      (2.0,), live_price=84.0)
    assert r2["price"] == 84.0 and r2["price_basis"] == "live"


def _fake_stop(table, default):
    def fake(df, episode, avg_cost, as_of, **kw):
        return {"stop": table.get(as_of, default), "basis": "full", "binding": "trailing",
                "reason": "", "components": {"entry": None, "trailing": 1.0, "ratchet": None}}
    return fake


def test_low_judged_against_stop_as_of_that_session_not_next(monkeypatch):
    f = frame(n=30)
    d_prev = f.index[-1].date()                      # last completed session
    as_of = (f.index[-1] + pd.Timedelta(days=1)).date()
    low = float(f["Low"].iloc[-1])
    e = ep(f.index[5].date())
    # stop(D) above Low, stop(D-1) below Low: Low(D-1) vs stop(D) must NOT flag.
    monkeypatch.setattr(hs, "compute_held_stop", _fake_stop({d_prev: low - 1.0}, low + 50))
    cell = dr.variant_cells(f, e, 100.0, as_of, None, (2.0,), None, 100.0, ("a",))[("a", 2.0)]
    assert cell["low_session"] == d_prev and cell["low_stop"] == low - 1.0
    assert "NEWLY_BREACHED_LOW" not in dr.flags(cell, {"breached": False}, 100.0)
    monkeypatch.setattr(hs, "compute_held_stop", _fake_stop({d_prev: low + 1.0}, low + 50))
    cell = dr.variant_cells(f, e, 100.0, as_of, None, (2.0,), None, 100.0, ("a",))[("a", 2.0)]
    assert "NEWLY_BREACHED_LOW" in dr.flags(cell, {"breached": False}, 100.0)


def test_live_partial_bar_low_judged_against_stop_as_of_today(monkeypatch):
    f = frame(n=31)
    as_of = f.index[-1].date()                       # today partial bar is the last row
    low = float(f["Low"].iloc[-1])
    e = ep(f.index[5].date())
    monkeypatch.setattr(hs, "compute_held_stop", _fake_stop({as_of: low + 1.0}, low - 50))
    cell = dr.variant_cells(f, e, 100.0, as_of, None, (2.0,), None, 100.0, ("a",),
                            partial_low=True)[("a", 2.0)]
    assert cell["low_session"] == as_of and cell["low"] == low
    assert "NEWLY_BREACHED_LOW" in dr.flags(cell, {"breached": False}, 100.0)
    cell = dr.variant_cells(f, e, 100.0, as_of, None, (2.0,), None, 100.0, ("a",))[("a", 2.0)]
    assert cell["low_session"] == f.index[-2].date()   # no --live: last completed session


def test_breach_uses_app_rounded_gap_rule():
    v0 = {"breached": False, "gap": 9.0, "type": "ATR Stop"}
    fl = dr.flags({"stop": 100.0, "gap": 0.0, "manual_won": False}, v0, 100.04)
    assert "NEWLY_BREACHED_CLOSE" in fl                      # +0.04% rounds to gap 0.0
    fl = dr.flags({"stop": 100.0, "gap": 0.1, "manual_won": False}, v0, 100.1)
    assert "NEWLY_BREACHED_CLOSE" not in fl
    f = frame(n=60)
    stop0 = dr.legacy_stop(f, 100.0, 1000.0, None)["stop"]
    assert dr.legacy_stop(f, stop0 + 0.04, 1000.0, None)["breached"] is True
    assert dr.legacy_stop(f, stop0 + 0.5, 1000.0, None)["breached"] is False


def test_looser_and_ratchet_dropped_flags_and_delta():
    v0 = {"stop": 100.0, "gap": 5.0, "breached": False, "type": "Ratchet +10%",
          "auto_type": "Ratchet +10%"}
    fl = dr.flags({"stop": 95.0, "gap": 10.0, "manual_won": False, "ratchet": None}, v0, 105.0)
    assert "LOOSER_THAN_V0" in fl and "RATCHET_DROPPED" in fl
    fl = dr.flags({"stop": 101.0, "gap": 4.0, "manual_won": False, "ratchet": 99.0}, v0, 105.0)
    assert "LOOSER_THAN_V0" not in fl and "RATCHET_DROPPED" not in fl
    atr = {**v0, "auto_type": "ATR Stop", "type": "ATR Stop"}
    assert "RATCHET_DROPPED" not in dr.flags(
        {"stop": 95.0, "gap": 10.0, "manual_won": False, "ratchet": None}, atr, 105.0)
    n = 131
    f = frame(n=n, gap_at=n - 1)
    as_of = f.index[-1].date()
    r = dr.today_row("XYZ", 10.0, 100.0, ep(f.index[70].date()), f, f, None, as_of, as_of, (2.0,))
    c = r["cells"][("a", 2.0)]
    assert c["delta_vs_v0_pct"] == pytest.approx((c["stop"] - r["v0"]["stop"]) / r["price"] * 100)
    assert c["delta_vs_v0_pct"] > 0


def test_summarize_today_tighter_looser_equal():
    def row(v0s, cs):
        return {"v0": {"stop": v0s}, "cells": {("a", 2.0): {
            "flags": ["RATCHET_DROPPED"] if cs < v0s else [], "basis": "full", "stop": cs,
            "delta_vs_v0_pct": (cs - v0s)}}}
    s = dr.summarize_today([row(10.0, 12.0), row(10.0, 8.0), row(10.0, 10.0)])[("a", 2.0)]
    assert (s["tighter"], s["looser"], s["equal"]) == (1, 1, 1)
    assert s["median_delta_pct"] == 0.0 and s["ratchet_dropped"] == 1


def test_variant_b_reason_names_the_suppression():
    f = frame(n=140)
    cells = dr.variant_cells(f, ep(f.index[70].date()), 100.0, f.index[-1].date(),
                             f.index[100].date(), (3.0,), None, 100.0)
    assert "anchor suppressed (variant b)" in cells[("b", 3.0)]["reason"]
    assert "no recorded fill price" not in cells[("b", 3.0)]["reason"]


def test_whipsaw_is_back_above_the_fire_stop_and_next_open():
    f = frame(n=150, gap_at=100)
    f.loc[f.index[103], ["Close", "High", "Open"]] = 87.0   # above fire close 85, below stop
    fr = dr.prep_frame(f, date(2100, 1, 1))
    res = dr.first_fire(fr, _rt(f, 70, 110), "a", 2.0)
    assert res["fire_close"] == 85.0 and res["fire_stop"] > 90
    assert res["whipsaw"] is False
    assert res["ret_next_open"] == pytest.approx(85.0 / 100.0 - 1)
    assert res["next_open_gap"] == pytest.approx(0.0)
    f2 = frame(n=150, gap_at=100)
    f2.loc[f2.index[103], ["Close", "High", "Open"]] = 150.0   # back above the stop
    fr2 = dr.prep_frame(f2, date(2100, 1, 1))
    assert dr.first_fire(fr2, _rt(f2, 70, 110), "a", 2.0)["whipsaw"] is True
    assert res["loser"] is True


def test_oversold_trip_is_flagged():
    df = _trades([("AAA", "BUY", 10, 100, "2026-01-06T15:00:00+00:00"),
                  ("AAA", "SELL", 15, 90, "2026-02-10T15:00:00+00:00")])
    trips = dr.closed_round_trips(df)
    assert len(trips) == 1 and trips[0]["oversold"] is True
    ok = dr.closed_round_trips(_trades([("AAA", "BUY", 10, 100, "2026-01-06T15:00:00+00:00"),
                                        ("AAA", "SELL", 10, 90, "2026-02-10T15:00:00+00:00")]))
    assert ok[0]["oversold"] is False


def test_div_since_sums_dividends_in_et_dates():
    f = frame(n=40)
    f["Dividends"] = 0.0
    f.loc[f.index[10], "Dividends"] = 0.5
    f.loc[f.index[30], "Dividends"] = 0.5
    g = _tz(f, "UTC")
    assert dr.div_since(g, f.index[5].date(), None, 100.0) == pytest.approx(1.0)
    assert dr.div_since(g, f.index[20].date(), None, 100.0) == pytest.approx(0.5)
    assert dr.div_since(f.drop(columns="Dividends"), f.index[5].date(), None, 100.0) is None


def test_main_refuses_past_as_of_in_today_mode(capsys):
    assert dr.main(["--as-of", "2020-01-02"]) == 2
    assert "refused" in capsys.readouterr().out


def test_script_pops_fmp_key_only_under_main():
    lines = _PATH.read_text(encoding="utf-8").splitlines()
    i = next(n for n, ln in enumerate(lines) if 'os.environ.pop("FMP_API_KEY"' in ln)
    assert lines[i - 2].strip() == 'if __name__ == "__main__":'
    assert lines[i].startswith("    ")


def test_header_prints_unreadable_for_none_manual_stops(capsys):
    from types import SimpleNamespace
    args = SimpleNamespace(as_of=date(2026, 10, 30), trail_start=date(2026, 10, 30),
                           trail_start_explicit=False, mults=(2.0, 3.0), variant="c",
                           replay_variants=("a",), live=False)
    dr.print_header(args, 3, None, True)
    out = capsys.readouterr().out
    assert "manual stops: UNREADABLE" in out
    assert "manual stops loaded" not in out


def test_header_and_compact_table_content(capsys):
    from types import SimpleNamespace
    args = SimpleNamespace(as_of=date(2026, 10, 30), trail_start=date(2026, 10, 30),
                           trail_start_explicit=False, mults=(2.0, 3.0), variant="c",
                           replay_variants=("a",), live=False)
    dr.print_header(args, 3, 0, True)
    out = capsys.readouterr().out
    for want in ("constants.py HELD_STOP_", "survivorship", "day-1 picture",
                 "dividend-UNadjusted", "WARNING: 0 manual stops", "steady state of (c)",
                 "ADD_WINNER_MIN_GAP_PCT", "<="):
        assert want in out
    n = 131
    f = frame(n=n, gap_at=n - 1)
    as_of = f.index[-1].date()
    row = dr.today_row("XYZ", 10.0, 100.0, ep(f.index[70].date()), f, f, None, as_of, as_of,
                       (2.0, 3.0))
    dr.print_compact([row], "c", (2.0, 3.0))
    out = capsys.readouterr().out
    assert "XYZ" in out and "x2.0" in out and "x3.0" in out


def test_run_replay_variant_b_trails_from_entry_and_c_is_skipped(monkeypatch):
    import exit_ladder_replay as lad
    from types import SimpleNamespace
    from stock_analyzer import data
    f = frame(n=150, gap_at=100)
    e, x = f.index[70].date(), f.index[110].date()
    trades = _trades([("XYZ", "BUY", 10, 100, f"{e}T15:00:00+00:00"),
                      ("XYZ", "SELL", 10, 80, f"{x}T15:00:00+00:00")])
    monkeypatch.setattr(dr, "_long_for", lambda *a, **k: f)
    monkeypatch.setattr(data, "fetch_ticker_bundle", lambda t, p: {"history": f})
    calls = []

    def fake_ladder(rt, period="2y", spy_hist=None):
        calls.append(spy_hist is not None)
        return {"first_trim": f.index[105].date(), "first_exit": None}
    monkeypatch.setattr(lad, "replay_round_trip", fake_ladder)
    seen_ts = []
    real = dr.first_fire

    def spy(frame_, rt, variant, mult, *, trail_start=None):
        seen_ts.append((variant, trail_start))
        return real(frame_, rt, variant, mult, trail_start=trail_start)
    monkeypatch.setattr(dr, "first_fire", spy)
    args = SimpleNamespace(tickers=set(), since=None, as_of=date(2026, 10, 30),
                           trail_start=date(2026, 10, 30), trail_start_explicit=False,
                           mults=(2.0,), replay_variants=("b", "c"), ladder_period="2y")
    rows = dr.run_replay(args, trades)
    assert {r["variant"] for r in rows} == {"b"}
    assert seen_ts == [("b", e)]
    assert calls == [True]                      # SPY fetched once and passed in
    assert rows[0]["loser"] is True and rows[0]["ladder_vs_stop"] == "stop_first"
