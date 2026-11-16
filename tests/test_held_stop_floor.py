"""Pure-layer tests for stock_analyzer.held_stop_floor (#26 C3c-1; synthetic frames,
no network / DB). The wiring, db, AST-parity and manual-precedence tests belong to
C3c-3 / C3c-4 (docs/plans/held-stop-floor-c3c-spec.md section 10)."""
from __future__ import annotations

import ast
import copy
import math
import pathlib
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import constants as C
from stock_analyzer import held_stop as hs
from stock_analyzer import held_stop_floor as hf
from stock_analyzer import held_stop_wiring as hw
from stock_analyzer.data import is_trading_day

T = "AAA"


# ── builders ─────────────────────────────────────────────────────────────────
def trading_days(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def frame_from_closes(days, closes, tz=None) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    df = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c}, index=idx)
    if tz == "ET":
        df.index = df.index.tz_localize("America/New_York")
    elif tz == "UTC":   # a bar stamped at 00:00 ET, expressed in UTC
        df.index = df.index.tz_localize("America/New_York").tz_convert("UTC")
    return df


def shaped_closes(n=140, e_idx=70, seed=3):
    """Flat ~100, entry at e_idx, a rise to ~108 over 22 sessions (< the 10% ratchet
    tier), then a slow decline: the trailing stop peaks early, so a fallback frame
    that seeds it later reads LOWER."""
    rng = np.random.default_rng(seed)
    c = np.empty(n)
    c[:e_idx] = 100 + rng.normal(0, 0.2, e_idx)
    c[e_idx] = 100.0
    for j in range(1, 23):
        c[e_idx + j] = 100 + 8.0 * j / 22 + rng.normal(0, 0.1)
    rest = n - (e_idx + 23)
    c[e_idx + 23:] = 108 - 9.0 * np.arange(1, rest + 1) / rest + rng.normal(0, 0.1, rest)
    return c


def pol():
    return {**hw._policy(), "trail_start": None}     # constants-driven, no go-live clip


def compute(frame, as_of, ep, avg):
    return hs.compute_held_stop(frame, ep, avg, as_of, **pol())


def make_ep(start, price, ratio=1.0, last_buy=None):
    return {"status": "ok", "start": start, "first_buy_price": price,
            "split_ratio": ratio, "reason": "", "last_buy": last_buy or start}


def wrapped(res, ep, avg):
    """What the driver's `_wrap` makes of an (applied) result, plus the floor
    fields C3c-4 will pass through."""
    w = hw._wrap(T, res, "", avg, ep=ep)
    w["floor"] = res.get("floor")
    w["stop_set_on"] = res.get("stop_set_on")
    return w


def rows_for(res, ep, avg, as_of):
    return hf.rows_to_write({T: wrapped(res, ep, avg)}, as_of)


class Scenario:
    def __init__(self, end=date(2026, 9, 30), n=140, e_idx=70, seed=3):
        self.days = trading_days(end, n)
        self.closes = shaped_closes(n, e_idx, seed)
        self.df = frame_from_closes(self.days, self.closes)
        self.e_idx = e_idx
        self.start = self.days[e_idx]
        self.avg = float(self.closes[e_idx])
        self.ep = make_ep(self.start, self.avg)

    def full(self, i, frame=None):
        """Result for as_of = days[i] on the long frame, floor-applied with no rows."""
        f = self.df.iloc[:i] if frame is None else frame
        res = compute(f, self.days[i], self.ep, self.avg)
        assert res["basis"] == "full", res
        return res

    def floored(self, i):
        """`full(i)` after `apply_floor` with no saved rows (what C3c-4 hands the writer)."""
        return hf.apply_floor(self.full(i), {}, ep=self.ep, avg_cost=self.avg,
                              frame=self.df.iloc[:i], as_of=self.days[i], readable=True)

    def row_at(self, i):
        res = self.full(i)
        out = hf.apply_floor(res, {}, ep=self.ep, avg_cost=self.avg, frame=self.df.iloc[:i],
                             as_of=self.days[i], readable=True)
        rows = rows_for(out, self.ep, self.avg, self.days[i])
        assert len(rows) == 1
        return rows[0]


@pytest.fixture(scope="module")
def sc():
    return Scenario()


@pytest.fixture
def row(sc):
    return sc.row_at(100)


# ── policy_sig / fingerprint ─────────────────────────────────────────────────
def test_policy_sig_is_stable_and_names_every_regime_constant():
    s = hf.policy_sig()
    assert s == hf.policy_sig() and s.startswith("v1;")
    assert repr(C.STOP_RATCHET_LEVELS) in s
    assert "HELD_STOP_FLOOR_CLOSE_TOL_PCT" not in s


@pytest.mark.parametrize("name,value", [
    ("ATR_STOP_MULT", 2.5), ("HELD_TRAIL_ATR_MULT", 3.5), ("HELD_TRAIL_START", "2026-11-30"),
    ("HELD_STOP_WARMUP_BARS", 61), ("HELD_STOP_MIN_BARS", 16),
    ("HELD_STOP_FILL_TOL_PCT", 2.5), ("HELD_STOP_SHARES_TOL", 1e-3),
    ("STOP_RATCHET_LEVELS", ((80, 0.4, "x"),)),
])
def test_each_policy_constant_changes_the_signature_and_invalidates_the_row(
        sc, row, monkeypatch, name, value):
    assert hf.match_row(row, sc.ep, sc.avg, sc.days[101])[0] is True
    monkeypatch.setattr(C, name, value)
    ok, why = hf.match_row(row, sc.ep, sc.avg, sc.days[101])
    assert ok is False and why.startswith("rules")


def test_atr_length_is_part_of_the_signature(sc, row, monkeypatch):
    assert f"atrlen={hs._ATR_LENGTH}" in hf.policy_sig()
    monkeypatch.setattr(hs, "_ATR_LENGTH", hs._ATR_LENGTH + 1)
    ok, why = hf.match_row(row, sc.ep, sc.avg, sc.days[101])
    assert ok is False and why.startswith("rules")


def test_close_tolerance_is_not_part_of_the_signature(sc, row, monkeypatch):
    before = hf.policy_sig()
    monkeypatch.setattr(C, "HELD_STOP_FLOOR_CLOSE_TOL_PCT", 9.0)
    assert hf.policy_sig() == before
    assert hf.match_row(row, sc.ep, sc.avg, sc.days[101])[0] is True


def test_fingerprint_rounding():
    fp = hf.episode_fingerprint(
        {"first_buy_price": 10.12345678, "split_ratio": 2.0000000004, "last_buy": date(2026, 9, 1)},
        100.004)
    assert fp["first_buy_price"] == 10.123457
    assert fp["split_ratio"] == 2.0
    assert fp["avg_cost"] == 100.0 and fp["last_buy"] == "2026-09-01"
    none = hf.episode_fingerprint({"first_buy_price": None}, 50)
    assert none["first_buy_price"] is None and none["split_ratio"] == 1.0


# ── match_row ────────────────────────────────────────────────────────────────
def _variant(sc, **over):
    ep = {**sc.ep, **{k: v for k, v in over.items() if k != "avg"}}
    return ep, over.get("avg", sc.avg)


@pytest.mark.parametrize("over,code,field", [
    ({"first_buy_price": 100.01}, "trades", "first_buy_price"),
    ({"split_ratio": 2.0}, "trades", "split_ratio"),
    ({"last_buy": date(2026, 8, 1)}, "trades", "last_buy"),
    ({"avg": 101.5}, "trades", "avg_cost"),
])
def test_each_fingerprint_field_invalidates_with_its_reason(sc, row, over, code, field):
    ep, avg = _variant(sc, **over)
    ok, why = hf.match_row(row, ep, avg, sc.days[101])
    assert ok is False and why.startswith(code) and field in why


def test_sub_cent_avg_cost_change_stays_valid(sc, row):
    assert hf.match_row(row, sc.ep, round(sc.avg, 2) + 0.004, sc.days[101])[0] is True


def test_late_add_is_invalid_and_late_partial_sell_is_valid(sc, row):
    # an add moves last_buy and the average cost
    ep, avg = _variant(sc, last_buy=sc.days[99], avg=sc.avg - 3.0)
    assert hf.match_row(row, ep, avg, sc.days[101])[0] is False
    # a partial sell changes neither the episode nor the (average-method) cost
    assert hf.match_row(row, sc.ep, sc.avg, sc.days[101]) == (True, "")


def test_moved_start_does_not_match_and_future_row_is_ignored(sc, row):
    moved = {**sc.ep, "start": sc.days[60]}
    assert hf.match_row(row, moved, sc.avg, sc.days[101])[0] is False
    ok, why = hf.match_row(row, sc.ep, sc.avg, sc.days[99])   # row as_of is days[100]
    assert ok is False and why.startswith("future_row")
    out = hf.apply_floor(sc.full(99), {sc.start: row}, ep=sc.ep, avg_cost=sc.avg,
                         frame=sc.df.iloc[:99], as_of=sc.days[99], readable=True)
    assert out["floor"]["state"] == "none" and out["stop"] == sc.full(99)["stop"]
    assert out["floor"]["row_as_of"] == sc.days[100]


# ── verify_closes ────────────────────────────────────────────────────────────
def _simple(n=30, close=100.0):
    days = trading_days(date(2026, 9, 30), n)
    return days, frame_from_closes(days, [close] * n)


def _vrow(days, ref_i=20, bind_i=10, ref_close=100.0, bind_close=100.0):
    return {"ref_session": days[ref_i], "ref_close": ref_close,
            "bind_session": days[bind_i], "bind_close": bind_close}


def test_close_check_boundaries():
    days, df = _simple()
    asof = days[-1] + timedelta(days=1)
    df.loc[pd.Timestamp(days[20]), "Close"] = 102.0          # exactly +2.0% of 100
    assert hf.verify_closes(_vrow(days), df, asof, tol_pct=2.0) == (True, "")
    df.loc[pd.Timestamp(days[20]), "Close"] = 102.01          # +0.01 over
    ok, why = hf.verify_closes(_vrow(days), df, asof, tol_pct=2.0)
    assert ok is False and why.startswith("history")
    df.loc[pd.Timestamp(days[20]), "Close"] = 98.0            # and the other side
    assert hf.verify_closes(_vrow(days), df, asof, tol_pct=2.0) == (True, "")
    df.loc[pd.Timestamp(days[20]), "Close"] = 99.2            # a 0.8% dividend drift
    assert hf.verify_closes(_vrow(days), df, asof, tol_pct=2.0) == (True, "")


def test_close_check_ref_missing_but_bind_present_verifies():
    days, df = _simple()
    asof = days[-1] + timedelta(days=1)
    r = _vrow(days)
    r["ref_session"] = days[-1] + timedelta(days=30)         # not in the frame
    assert hf.verify_closes(r, df, asof, tol_pct=2.0) == (True, "")
    df.loc[pd.Timestamp(days[10]), "Close"] = 50.0           # the bind bar was halved
    assert hf.verify_closes(r, df, asof, tol_pct=2.0)[0] is False


def test_close_check_neither_present_is_invalid():
    days, df = _simple()
    asof = days[-1] + timedelta(days=1)
    r = _vrow(days)
    r["ref_session"] = date(2020, 1, 2)
    r["bind_session"] = date(2020, 1, 3)
    ok, why = hf.verify_closes(r, df, asof, tol_pct=2.0)
    assert ok is False and why.startswith("history")


def test_close_check_bind_before_frame_start_is_skipped():
    days, df = _simple()
    asof = days[-1] + timedelta(days=1)
    r = _vrow(days)
    r["bind_session"] = days[0] - timedelta(days=40)          # older than the first bar
    r["bind_close"] = 1.0                                     # would fail if it were checked
    assert hf.verify_closes(r, df, asof, tol_pct=2.0) == (True, "")


def test_close_check_in_span_missing_session_is_invalid():
    days, df = _simple()
    asof = days[-1] + timedelta(days=1)
    gone = df.drop(pd.Timestamp(days[10]))                    # bind bar deleted, inside the span
    ok, why = hf.verify_closes(_vrow(days), gone, asof, tol_pct=2.0)
    assert ok is False and "bind session" in why and "missing from the price history" in why
    nan = df.copy()
    nan.loc[pd.Timestamp(days[10]), "Close"] = np.nan         # NaN Close: completed_bars drops it
    ok, why = hf.verify_closes(_vrow(days), nan, asof, tol_pct=2.0)
    assert ok is False and "bind session" in why
    ref_gone = df.drop(pd.Timestamp(days[20]))
    ok, why = hf.verify_closes(_vrow(days), ref_gone, asof, tol_pct=2.0)
    assert ok is False and "ref session" in why


def test_close_check_ignores_bars_on_or_after_as_of():
    days, df = _simple()
    r = _vrow(days)
    # the ref bar is "today": a completed-bars view never sees it
    ok, _ = hf.verify_closes(r, df, days[20], tol_pct=2.0)
    assert ok is True                                          # bind (days[10]) still verifies
    only_ref = {**r, "bind_session": None, "bind_close": None}
    assert hf.verify_closes(only_ref, df, days[20], tol_pct=2.0)[0] is False


# ── apply_floor ──────────────────────────────────────────────────────────────
def _bumped(row, by=3.0):
    return {**row, "stop": round(row["stop"] + by, 2)}


def _apply(sc, rows, i, *, frame=None, res=None, readable=True, basis=None, ep=None):
    f = sc.df.iloc[:i] if frame is None else frame
    r = compute(f, sc.days[i], sc.ep, sc.avg) if res is None else res
    if basis:
        r = {**r, "basis": basis}
    return hf.apply_floor(r, rows, ep=ep or sc.ep, avg_cost=sc.avg, frame=f,
                          as_of=sc.days[i], readable=readable)


def test_applied_floor_uses_saved_stop_keeps_basis_and_label_never_saved(sc, row):
    r = _bumped(row)
    out = _apply(sc, {sc.start: r}, 101, basis="partial")
    f = out["floor"]
    assert f["state"] == "applied" and out["stop"] == r["stop"]
    assert out["basis"] == "partial"                      # the floor never changes the basis
    assert out["binding"] == r["binding"]
    assert out["bind_session"] == date.fromisoformat(r["bind_session"])
    assert out["stop_set_on"] == date.fromisoformat(r["stop_set_on"])
    assert "Saved" not in out["label"]
    assert out["label"] == hw.label_for("partial", r["binding"], r["ratchet_label"])
    assert f["computed"] == compute(sc.df.iloc[:101], sc.days[101], sc.ep, sc.avg)["stop"]


def test_not_binding_when_todays_figure_reaches_the_saved_one(sc, row):
    lo = {**row, "stop": round(row["stop"] - 1.0, 2)}
    out = _apply(sc, {sc.start: lo}, 101)
    assert out["floor"]["state"] == "not_binding"
    assert out["stop"] == compute(sc.df.iloc[:101], sc.days[101], sc.ep, sc.avg)["stop"]
    assert out["stop_set_on"] == sc.days[101]             # a fresh higher value: set today


def test_equal_value_keeps_the_original_set_on(sc, row):
    out = _apply(sc, {sc.start: {**row, "stop": compute(
        sc.df.iloc[:101], sc.days[101], sc.ep, sc.avg)["stop"], "stop_set_on": "2026-08-03"}}, 101)
    assert out["floor"]["state"] == "not_binding"
    assert out["stop_set_on"] == date(2026, 8, 3)


@pytest.mark.parametrize("basis", ["prior_session", "unavailable", "buy_day"])
def test_floor_never_applies_off_full_or_partial(sc, row, basis):
    res = {**compute(sc.df.iloc[:101], sc.days[101], sc.ep, sc.avg), "basis": basis}
    out = hf.apply_floor(res, {sc.start: _bumped(row)}, ep=sc.ep, avg_cost=sc.avg,
                         frame=sc.df.iloc[:101], as_of=sc.days[101], readable=True)
    assert out["floor"]["state"] == "n/a" and out["stop"] == res["stop"]


def test_floor_never_applies_without_a_computed_stop_or_for_a_buy_day_episode(sc, row):
    res = {**compute(sc.df.iloc[:101], sc.days[101], sc.ep, sc.avg), "stop": None}
    assert _apply(sc, {sc.start: _bumped(row)}, 101, res=res)["floor"]["state"] == "n/a"
    out = hf.apply_floor(compute(sc.df.iloc[:101], sc.days[101], sc.ep, sc.avg),
                         {sc.start: _bumped(row)}, ep={**sc.ep, "start": sc.days[101]},
                         avg_cost=sc.avg, frame=sc.df.iloc[:101], as_of=sc.days[101],
                         readable=True)
    assert out["floor"]["state"] == "n/a"


def test_moved_start_finds_no_row(sc, row):
    ep2 = {**sc.ep, "start": sc.days[60]}
    out = _apply(sc, {sc.start: _bumped(row)}, 101, ep=ep2)
    assert out["floor"]["state"] == "none"


@pytest.mark.parametrize("over,code", [
    ({"first_buy_price": 99.0}, "trades"),
    ({"split_ratio": 2.0}, "trades"),
    ({"policy_sig": "v0;old"}, "rules"),
    ({"ref_close": 50.0, "bind_close": 50.0}, "history"),
])
def test_invalid_rows_fall_back_to_the_fresh_figure_with_a_reason(sc, row, over, code):
    r = {**_bumped(row), **over}
    out = _apply(sc, {sc.start: r}, 101, basis="partial")
    f = out["floor"]
    assert f["state"] == "invalid" and f["reason"].startswith(code)
    assert out["stop"] == f["computed"]
    assert f["value"] == r["stop"] and f["note"].startswith("A saved full-history stop")
    assert out["stop_set_on"] == sc.days[101]


def test_invalid_note_texts_per_cause(sc, row):
    texts = {}
    for code, over in {"trades": {"avg_cost": 120.0}, "history": {"ref_close": 10.0, "bind_close": 10.0},
                       "rules": {"policy_sig": "x"}}.items():
        out = _apply(sc, {sc.start: {**_bumped(row), **over}}, 101, basis="partial")
        texts[code] = out["floor"]["note"]
    assert "your trades in this position changed since then" in texts["trades"]
    assert "price history no longer matches" in texts["history"] and "stock split" in texts["history"]
    assert "the stop rules changed since then" in texts["rules"]
    assert all("\\$" in t for t in texts.values())


def test_notes_only_on_degraded_days_and_never_claim_only_moves_up(sc, row):
    r = _bumped(row)
    full = _apply(sc, {sc.start: r}, 101)                    # basis full
    assert full["floor"]["state"] == "applied" and full["floor"]["note"] == ""
    part = _apply(sc, {sc.start: r}, 101, basis="partial")
    assert "held at \\$" in part["floor"]["note"] and "saved on" in part["floor"]["note"]
    assert "Today's figure from the shorter history was \\$" in part["floor"]["note"]
    notes = [part["floor"]["note"],
             _apply(sc, {sc.start: {**r, "avg_cost": 1.0}}, 101, basis="partial")["floor"]["note"],
             _apply(sc, None, 101, readable=False, basis="partial")["floor"]["note"]]
    for n in notes:
        assert n and "only moves up" not in n.lower() and "never falls" not in n.lower()
        assert "\\" not in hw.plain_text_note(n)


def test_offline_three_state_none_vs_empty_never_collapse(sc, row):
    r = _bumped(row)
    unreadable = _apply(sc, None, 101, readable=False, basis="partial")
    assert unreadable["floor"]["state"] == "unreadable"
    assert "couldn't be read" in unreadable["floor"]["note"]
    assert unreadable["stop_set_on"] is None                       # a blank beats a guess
    assert _apply(sc, None, 101, readable=False)["floor"]["note"] == ""      # full day: silent
    assert _apply(sc, None, 101, readable=True)["floor"]["state"] == "unreadable"   # None rows
    assert _apply(sc, {sc.start: r}, 101, readable=False)["floor"]["state"] == "unreadable"
    empty = _apply(sc, {}, 101, readable=True, basis="partial")
    assert empty["floor"]["state"] == "none" and empty["floor"]["note"] == ""
    assert unreadable["stop"] == empty["stop"] == unreadable["floor"]["computed"]


def test_apply_floor_never_mutates_its_inputs(sc, row):
    f = sc.df.iloc[:101].copy()
    res = compute(f, sc.days[101], sc.ep, sc.avg)
    rows = {sc.start: _bumped(row)}
    ep = dict(sc.ep)
    snap = (copy.deepcopy(res), copy.deepcopy(rows), copy.deepcopy(ep), f.copy())
    out = hf.apply_floor(res, rows, ep=ep, avg_cost=sc.avg, frame=f, as_of=sc.days[101], readable=True)
    assert out is not res and out["floor"]["state"] == "applied"
    assert res == snap[0] and rows == snap[1] and ep == snap[2]
    pd.testing.assert_frame_equal(f, snap[3])
    assert "floor" not in res


def test_row_keys_may_be_iso_strings(sc, row):
    out = _apply(sc, {sc.start.isoformat(): _bumped(row)}, 101)
    assert out["floor"]["state"] == "applied"


# ── split guard ──────────────────────────────────────────────────────────────
def test_unrecorded_two_for_one_split_does_not_apply_the_floor(sc, row):
    i = 110
    split_df = sc.df.iloc[:i].copy()
    # Yahoo re-states every PRE-split bar by 1/2; the journal has no SPLIT row
    cut = 105
    split_df.iloc[:cut, :] = split_df.iloc[:cut, :] * 0.5
    r = _bumped(row)
    out = hf.apply_floor(compute(split_df, sc.days[i], sc.ep, sc.avg), {sc.start: r},
                         ep=sc.ep, avg_cost=sc.avg, frame=split_df, as_of=sc.days[i],
                         readable=True)
    assert out["floor"]["state"] == "invalid" and out["floor"]["reason"].startswith("history")
    assert out["stop"] == out["floor"]["computed"] != r["stop"]


def test_recorded_split_row_invalidates_the_fingerprint(sc, row):
    trades = pd.DataFrame([
        {"id": 1, "ticker": T, "action": "BUY", "shares": 10, "price": sc.avg,
         "traded_at": f"{sc.start.isoformat()}T15:00:00+00:00"},
        {"id": 2, "ticker": T, "action": "SPLIT", "shares": 20, "price": 0,
         "traded_at": f"{sc.days[104].isoformat()}T15:00:00+00:00"}])
    ep = hs.episode_from_journal(T, trades, 20, shares_tol=C.HELD_STOP_SHARES_TOL)
    assert ep["status"] == "ok" and ep["split_ratio"] == 2.0
    ok, why = hf.match_row(row, ep, sc.avg / 2, sc.days[110])
    assert ok is False and why.startswith("trades")


# ── writer ───────────────────────────────────────────────────────────────────
def test_only_full_basis_is_written(sc):
    res = sc.full(100)
    base = wrapped(sc.floored(100), sc.ep, sc.avg)
    held = {t: {**base, "ticker": t, "basis": b}
            for t, b in [("F", "full"), ("P", "partial"), ("S", "prior_session"),
                         ("U", "unavailable"), ("B", "buy_day")]}
    rows = hf.rows_to_write(held, sc.days[100])
    assert [r["ticker"] for r in rows] == ["F"]


def test_row_shape(sc):
    r = sc.row_at(100)
    assert set(r) == {"ticker", "episode_start", "as_of", "stop", "stop_set_on", "binding",
                      "ratchet_label", "first_buy_price", "split_ratio", "last_buy", "avg_cost",
                      "ref_session", "ref_close", "bind_session", "bind_close", "policy_sig",
                      "source"}
    assert r["ticker"] == T and r["source"] == "cron:eod" and r["policy_sig"] == hf.policy_sig()
    assert r["episode_start"] == sc.start.isoformat() and r["as_of"] == sc.days[100].isoformat()
    assert r["ref_session"] == sc.days[99].isoformat() < r["as_of"]
    assert r["stop_set_on"] == r["as_of"]                     # first value: set today
    assert r["binding"] in ("entry", "trailing", "ratchet")
    assert r["ref_close"] == pytest.approx(float(sc.closes[99]))
    bound = date.fromisoformat(r["bind_session"])
    assert sc.start - timedelta(days=10) <= bound < sc.days[100]
    assert r["first_buy_price"] == round(sc.avg, 6) and r["split_ratio"] == 1.0
    assert r["avg_cost"] == round(sc.avg, 2) and r["last_buy"] == sc.start.isoformat()


def test_binding_bar_carries_the_binding_components_close(sc):
    res = sc.full(100)
    comp = res["components"][res["binding"]]
    assert res["stop"] == round(comp, 2)
    s, c = res["bind_session"], res["bind_close"]
    assert c == pytest.approx(float(sc.df.loc[pd.Timestamp(s), "Close"]))
    assert res["last_close"] == pytest.approx(float(sc.closes[99]))


@pytest.mark.parametrize("as_of", [date(2026, 11, 14), date(2026, 11, 15), date(2026, 11, 26),
                                   date(2026, 7, 3)])
def test_non_trading_as_of_writes_nothing(sc, as_of):
    assert not is_trading_day(as_of)
    w = wrapped(sc.floored(100), sc.ep, sc.avg)
    assert hf.rows_to_write({T: w}, as_of) == []


def test_floored_full_result_keeps_saved_value_set_on_and_bind(sc, row):
    r = {**_bumped(row), "stop_set_on": "2026-08-03", "bind_session": row["bind_session"]}
    out = _apply(sc, {sc.start: r}, 101)
    assert out["floor"]["state"] == "applied" and out["basis"] == "full"
    new = rows_for(out, sc.ep, sc.avg, sc.days[101])[0]
    assert new["stop"] == r["stop"] and new["stop_set_on"] == "2026-08-03"
    assert new["binding"] == r["binding"] and new["bind_session"] == r["bind_session"]
    assert new["bind_close"] == r["bind_close"]
    assert new["as_of"] == sc.days[101].isoformat()
    assert new["ref_session"] == sc.days[100].isoformat()         # refreshed
    assert new["ref_close"] == pytest.approx(float(sc.closes[100]))


def test_fresh_higher_value_sets_set_on_to_as_of(sc, row):
    lo = {**row, "stop": round(row["stop"] - 1.0, 2)}
    out = _apply(sc, {sc.start: lo}, 101)
    new = rows_for(out, sc.ep, sc.avg, sc.days[101])[0]
    assert new["stop_set_on"] == sc.days[101].isoformat()
    assert new["stop"] == out["stop"]


def test_invalid_floor_is_overwritten_by_the_fresh_value(sc, row):
    r = {**_bumped(row), "policy_sig": "old"}
    out = _apply(sc, {sc.start: r}, 101)
    assert out["floor"]["state"] == "invalid"
    new = rows_for(out, sc.ep, sc.avg, sc.days[101])[0]
    assert new["stop"] == out["floor"]["computed"] and new["policy_sig"] == hf.policy_sig()


def test_as_of_earlier_than_the_stored_row_is_not_written(sc, row):
    out = _apply(sc, {sc.start: row}, 99)           # the row is dated days[100]: future
    assert out["floor"]["state"] == "none" and out["floor"]["row_as_of"] == sc.days[100]
    assert rows_for(out, sc.ep, sc.avg, sc.days[99]) == []
    # the same evening re-run (as_of == row as_of) is allowed
    same = _apply(sc, {sc.start: row}, 100)
    assert len(rows_for(same, sc.ep, sc.avg, sc.days[100])) == 1


def test_unreadable_floor_writes_nothing(sc):
    out = _apply(sc, None, 100, readable=False)
    assert out["floor"]["state"] == "unreadable"
    assert rows_for(out, sc.ep, sc.avg, sc.days[100]) == []


@pytest.mark.parametrize("patch", [
    {"stop": None}, {"stop": float("nan")}, {"stop": 0.0}, {"episode": None},
    {"binding": "prior_session"}, {"bind_session": None}, {"bind_close": None},
    {"last_close": None}, {"last_session": None}, {"avg_cost": 0.0},
])
def test_incomplete_result_writes_nothing(sc, patch):
    w = {**wrapped(sc.floored(100), sc.ep, sc.avg), **patch}
    assert hf.rows_to_write({T: w}, sc.days[100]) == []


def test_ref_session_must_precede_as_of_and_start_must_precede_as_of(sc):
    w = wrapped(sc.floored(100), sc.ep, sc.avg)
    assert hf.rows_to_write({T: {**w, "last_session": sc.days[100]}}, sc.days[100]) == []
    bought_today = {**w, "episode": {**w["episode"], "start": sc.days[100]}}
    assert hf.rows_to_write({T: bought_today}, sc.days[100]) == []


def test_rows_to_write_is_defensive_about_its_input(sc):
    assert hf.rows_to_write(None, sc.days[100]) == []
    assert hf.rows_to_write({}, sc.days[100]) == []
    assert hf.rows_to_write({T: None}, sc.days[100]) == []


def test_rows_to_write_does_not_mutate_input(sc):
    held = {T: wrapped(sc.floored(100), sc.ep, sc.avg)}
    snap = copy.deepcopy(held)
    hf.rows_to_write(held, sc.days[100])
    assert held == snap


def test_a_bar_dated_as_of_changes_neither_result_nor_row(sc):
    i = 100
    wild = sc.df.iloc[:i + 1].copy()
    wild.iloc[i, :] = [1.0, 1e6, 1e-6, 1e-3]          # today's partial bar, absurd
    a = hf.apply_floor(sc.full(i), {}, ep=sc.ep, avg_cost=sc.avg, frame=sc.df.iloc[:i],
                       as_of=sc.days[i], readable=True)
    b = hf.apply_floor(compute(wild, sc.days[i], sc.ep, sc.avg), {}, ep=sc.ep, avg_cost=sc.avg,
                       frame=wild, as_of=sc.days[i], readable=True)
    assert a == b
    assert rows_for(a, sc.ep, sc.avg, sc.days[i]) == rows_for(b, sc.ep, sc.avg, sc.days[i])


# ── the invariant (key test) ─────────────────────────────────────────────────
def _run_invariant(use_floors: bool, seed: int):
    n, e_idx, div_idx, window = 140, 70, 112, 80
    s = Scenario(n=n, e_idx=e_idx)
    rng = np.random.default_rng(seed)
    fallback = rng.random(n) < 0.5
    stored: dict = {}
    stops, states = [], []
    for i in range(100, n):
        as_of = s.days[i]
        is_fb = bool(fallback[i]) and i > 100        # the first day writes the first row
        long_res = compute(s.df.iloc[:i], as_of, s.ep, s.avg)
        assert long_res["basis"] == "full"
        if is_fb:
            # the app's bundle: a ROLLING window (its start advances every day, ending at
            # the last completed bar) that never reaches the ATR origin, dividend-ADJUSTED:
            # every bar before the ex-date (div_idx) is scaled down 0.8%
            frame = s.df.iloc[max(0, i - window):i].copy()
            if i > div_idx:
                before = frame.index < pd.Timestamp(s.days[div_idx])
                frame.loc[before, :] = frame.loc[before, :] * 0.992
            res = compute(frame, as_of, s.ep, s.avg)
            if res["basis"] == "full":
                res = {**res, "basis": "partial"}
        else:
            frame, res = s.df.iloc[:i], long_res
        if use_floors:
            res = hf.apply_floor(res, stored.get(T, {}), ep=s.ep, avg_cost=s.avg,
                                 frame=frame, as_of=as_of, readable=True)
            states.append(res["floor"]["state"])
            if res["floor"]["state"] == "applied":
                # U10: an applied floor is a verified LOWER bound of the true (full) stop
                assert res["stop"] <= long_res["stop"] + 1e-9, (i, res["stop"], long_res["stop"])
            for r in hf.rows_to_write({T: wrapped(res, s.ep, s.avg)}, as_of):
                stored.setdefault(r["ticker"], {})[date.fromisoformat(r["episode_start"])] = r
        stops.append(res["stop"])
    return stops, states, fallback[100:]


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_invariant_effective_stop_never_drops_with_floors_but_does_without(seed):
    with_floor, states, fb = _run_invariant(True, seed)
    without, _, _ = _run_invariant(False, seed)
    assert fb[1:].any()
    for a, b in zip(with_floor, with_floor[1:]):
        assert b >= a - 1e-9, (seed, with_floor)
    assert any(b < a - 1e-9 for a, b in zip(without, without[1:])), (seed, without)
    assert "applied" in states and "not_binding" in states   # the floor really bound
    assert "invalid" not in states      # a 0.8% dividend drift never trips the close check


# ── time-context matrix (spec section 6) ─────────────────────────────────────
def _time_case(end_bar: date, row_as_of: date, as_of: date, *, tz=None, n=170):
    days = trading_days(end_bar, n)
    closes = shaped_closes(n, 70, 5)
    df = frame_from_closes(days, closes, tz=tz)
    naive = frame_from_closes(days, closes)
    start = days[70]
    avg = float(closes[70])
    ep = make_ep(start, avg)
    res0 = compute(naive.loc[naive.index < pd.Timestamp(row_as_of)], row_as_of, ep, avg)
    assert res0["basis"] == "full"
    out0 = hf.apply_floor(res0, {}, ep=ep, avg_cost=avg, frame=naive, as_of=row_as_of, readable=True)
    row = rows_for(out0, ep, avg, row_as_of)[0]
    row = {**row, "stop": round(row["stop"] + 3.0, 2)}
    res1 = compute(df, as_of, ep, avg)
    out1 = hf.apply_floor(res1, {start: row}, ep=ep, avg_cost=avg, frame=df, as_of=as_of, readable=True)
    return row, out1, days


def test_saturday_visit_uses_fridays_row_with_thursday_as_ref():
    fri, sat = date(2026, 11, 13), date(2026, 11, 14)
    row, out, days = _time_case(fri, fri, sat)
    assert row["as_of"] == "2026-11-13" and row["ref_session"] == "2026-11-12"
    assert out["floor"]["state"] == "applied" and out["stop"] == row["stop"]


def test_holiday_visit_uses_the_prior_sessions_row():
    # Thanksgiving Thursday 2026-11-26: the market is shut, the row is Wednesday's
    row, out, _ = _time_case(date(2026, 11, 25), date(2026, 11, 25), date(2026, 11, 26))
    assert out["floor"]["state"] == "applied" and row["ref_session"] == "2026-11-24"


def test_day_after_thanksgiving_uses_the_wednesday_row():
    row, out, days = _time_case(date(2026, 11, 25), date(2026, 11, 25), date(2026, 11, 27))
    assert date(2026, 11, 26) not in days
    assert row["as_of"] == "2026-11-25"
    assert out["floor"]["state"] == "applied" and out["stop"] == row["stop"]


@pytest.mark.parametrize("tz", [None, "ET", "UTC"])
@pytest.mark.parametrize("as_of,prev_fri", [(date(2026, 11, 2), date(2026, 10, 30)),
                                            (date(2027, 3, 15), date(2027, 3, 12))])
def test_dst_change_weeks_floor_applies_for_every_index_flavour(as_of, prev_fri, tz):
    row, out, _ = _time_case(prev_fri, prev_fri, as_of, tz=tz)
    assert out["floor"]["state"] == "applied" and out["stop"] == row["stop"]
    assert row["ref_session"] == (trading_days(prev_fri, 2)[0]).isoformat()


@pytest.mark.parametrize("tz", ["ET", "UTC"])
def test_tz_aware_frames_give_the_naive_result(tz):
    d = date(2026, 11, 3)
    a = _time_case(date(2026, 10, 30), date(2026, 10, 30), d, tz=tz)[1]
    b = _time_case(date(2026, 10, 30), date(2026, 10, 30), d, tz=None)[1]
    assert a == b


def test_eod_at_2030_est_is_01_30_utc_next_day_and_writes_the_et_date(sc):
    utc = datetime(2026, 11, 17, 1, 30, tzinfo=timezone.utc)     # 20:30 EST on 11-16
    from stock_analyzer.market_time import ET
    et_day = utc.astimezone(ET).date()
    assert et_day == date(2026, 11, 16) and utc.date() == date(2026, 11, 17)
    days = trading_days(et_day, 170)
    closes = shaped_closes(170, 70, 5)
    df = frame_from_closes(days[:-1], closes[:-1])             # bars through Fri 11-13
    ep = make_ep(days[70], float(closes[70]))
    res = compute(df, et_day, ep, float(closes[70]))
    out = hf.apply_floor(res, {}, ep=ep, avg_cost=float(closes[70]), frame=df, as_of=et_day,
                         readable=True)
    rows = hf.rows_to_write({T: wrapped(out, ep, float(closes[70]))}, et_day)
    assert [r["as_of"] for r in rows] == ["2026-11-16"]
    assert hf.rows_to_write({T: wrapped(out, ep, float(closes[70]))}, utc.date()) != rows


def test_late_logged_events_at_the_pure_level(sc, row):
    r = _bumped(row)
    # late-logged add: last_buy / avg_cost change -> invalid, fresh row at EOD
    out = _apply(sc, {sc.start: r}, 101, ep={**sc.ep, "last_buy": sc.days[99]})
    assert out["floor"]["state"] == "invalid"
    # late first BUY that moves the start (the episode starts earlier): no row for it
    assert _apply(sc, {sc.start: r}, 101, ep={**sc.ep, "start": sc.days[65]})["floor"]["state"] == "none"
    # late SELL closing the episode: a new episode key later, no row for it
    assert _apply(sc, {sc.start: r}, 101, ep={**sc.ep, "start": sc.days[90]})["floor"]["state"] == "none"
    # late partial SELL: the fingerprint is unchanged
    assert _apply(sc, {sc.start: r}, 101)["floor"]["state"] == "applied"


# ── lane_failure (U4) ────────────────────────────────────────────────────────
def _h(basis):
    return {"basis": basis}


def test_lane_failure_cases_partial_only():
    """U4, owner 2026-11-16: red only when unreadable AND a name is `partial`."""
    ok = {"A": _h("full"), "B": _h("full")}
    assert hf.lane_failure(ok, True) is None
    assert hf.lane_failure(ok, False) is None                       # unreadable but nobody partial
    assert hf.lane_failure({"A": _h("full"), "B": _h("partial")}, True) is None
    msg = hf.lane_failure({"A": _h("full"), "B": _h("partial")}, False)
    assert msg and "could not be read" in msg and "B" in msg and "A" not in msg.split("(")[-1]
    assert hf.lane_failure({"A": _h("prior_session")}, False) is None    # no longer counts
    assert hf.lane_failure({"A": _h("unavailable")}, False) is None
    assert hf.lane_failure({"A": _h("buy_day")}, False) is None
    assert hf.lane_failure({"A": _h("prior_session"), "B": _h("partial")}, False)
    assert hf.lane_failure({}, False) is None
    assert hf.lane_failure(None, False) is None
    assert hf.lane_failure({"A": None}, False) is None   # a missing result: no floor could apply


def test_writer_fails_closed_without_a_floor_result_or_on_an_unknown_state(sc):
    good = wrapped(sc.floored(100), sc.ep, sc.avg)
    assert len(hf.rows_to_write({T: good}, sc.days[100])) == 1
    no_floor = {k: v for k, v in good.items() if k != "floor"}
    assert hf.rows_to_write({T: no_floor}, sc.days[100]) == []
    assert hf.rows_to_write({T: {**good, "floor": None}}, sc.days[100]) == []
    assert hf.rows_to_write({T: {**good, "floor": {"state": "mystery"}}}, sc.days[100]) == []
    for st in ("unreadable", "n/a"):
        assert hf.rows_to_write({T: {**good, "floor": {"state": st}}}, sc.days[100]) == []
    for st in ("applied", "not_binding", "none", "invalid"):
        assert len(hf.rows_to_write({T: {**good, "floor": {"state": st}}}, sc.days[100])) == 1


def test_after_hours_same_day_row_is_applied(sc, row):
    # 20:00 ET on the row's own session: as_of == row as_of (the EOD run already wrote it)
    out = _apply(sc, {sc.start: _bumped(row)}, 100)
    assert row["as_of"] == sc.days[100].isoformat()
    assert out["floor"]["state"] == "applied" and out["stop"] == _bumped(row)["stop"]


# ── a bad tick later corrected (the reviewer's failure case) ─────────────────
def _bad_tick_setup(sc):
    k = 95
    bad = sc.df.copy()
    bad.iloc[k, :] = bad.iloc[k, :] * 1.25                    # a +25% tick on bar 95
    res = compute(bad.iloc[:100], sc.days[100], sc.ep, sc.avg)
    assert res["basis"] == "full" and res["bind_session"] == sc.days[k]
    out = hf.apply_floor(res, {}, ep=sc.ep, avg_cost=sc.avg, frame=bad.iloc[:100],
                         as_of=sc.days[100], readable=True)
    saved = rows_for(out, sc.ep, sc.avg, sc.days[100])[0]
    assert saved["bind_session"] == sc.days[k].isoformat()
    return k, saved


@pytest.mark.parametrize("how", ["deleted", "nan"])
def test_corrected_bad_tick_bar_deleted_or_nan_does_not_reapply_the_floor(sc, how):
    k, saved = _bad_tick_setup(sc)
    fixed = sc.df.copy()
    i = 101
    if how == "deleted":
        fixed = fixed.drop(fixed.index[k])
        frame = fixed.iloc[:i - 1]
    else:
        fixed.iloc[k, fixed.columns.get_loc("Close")] = np.nan
        frame = fixed.iloc[:i]
    res = compute(frame, sc.days[i], sc.ep, sc.avg)
    assert res["basis"] == "full"
    out = hf.apply_floor(res, {sc.start: saved}, ep=sc.ep, avg_cost=sc.avg, frame=frame,
                         as_of=sc.days[i], readable=True)
    assert out["floor"]["state"] == "invalid"
    assert "missing from the price history" in out["floor"]["reason"]
    assert out["stop"] == res["stop"] < saved["stop"]
    new = rows_for(out, sc.ep, sc.avg, sc.days[i])[0]
    assert new["stop"] == res["stop"] and new["stop"] < saved["stop"]   # healed, not re-certified


# ── structure ────────────────────────────────────────────────────────────────
def test_no_module_level_import_of_held_stop_wiring():
    """held_stop_wiring will import this module (C3c-4), so the label import must stay lazy."""
    src = pathlib.Path(hf.__file__).read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.ImportFrom):
            assert node.module != "stock_analyzer.held_stop_wiring"
            assert not (node.module == "stock_analyzer"
                        and any(a.name == "held_stop_wiring" for a in node.names))
        if isinstance(node, ast.Import):
            assert all(a.name != "stock_analyzer.held_stop_wiring" for a in node.names)


def test_result_additions_are_additive_and_existing_callers_unchanged(sc):
    res = sc.full(100)
    for k in ("last_close", "bind_session", "bind_close"):
        assert k in res
    unavailable = hs.compute_held_stop(sc.df.iloc[:5], sc.ep, sc.avg, sc.days[10], **pol())
    assert unavailable["basis"] == "unavailable" and unavailable["last_close"] is None
    prior = hs.compute_held_stop(sc.df.iloc[:100], {"status": "unreadable", "reason": "x"},
                                 sc.avg, sc.days[100], **pol())
    assert prior["basis"] == "prior_session" and prior["bind_session"] is None
    assert prior["last_close"] == pytest.approx(float(sc.closes[99]))
    w = hw._wrap(T, res, "", sc.avg, ep=sc.ep)
    assert w["episode"] == {"start": sc.start, "first_buy_price": sc.avg, "split_ratio": 1.0,
                            "last_buy": sc.start}
    assert hw._wrap(T, res, "", sc.avg)["episode"] is None
    assert hw._wrap(T, res, "", sc.avg, ep={"status": "mismatch", "reason": "x"})["episode"] is None


def test_wrap_finite_values(sc):
    w = hw._wrap(T, sc.full(100), "", sc.avg, ep=sc.ep)
    assert math.isfinite(w["bind_close"]) and math.isfinite(w["last_close"])
