"""#26 C3c-4 / C3c-5: the saved held-stop floor, WIRED (docs/plans/held-stop-floor-c3c-spec.md
sections 5, 6, 10 and "Tests the build must include").

Covers `build_held_stops(floors=)` end to end on synthetic frames, the inert golden
(`floors={}` / `floors=None`), the manual-stop precedence, the cron premarket and EOD
steps, the display helpers and the AST parity pins. The pure layer is in
test_held_stop_floor.py. No network, no DB, no app import (app.py is read as text / AST)."""
from __future__ import annotations

import ast
import datetime as dt
import inspect
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
import pytz

import cron_runner as cr
from stock_analyzer import constants as C
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer import held_stop as hs
from stock_analyzer import held_stop_floor as hf
from stock_analyzer import held_stop_view as hv
from stock_analyzer import held_stop_wiring as hw
from stock_analyzer import portfolio as pf
from stock_analyzer import stop_breach_text as sbt
from tests._ast_cache import parse_file_cached
from tests.test_held_stop_floor import Scenario, T
from tests.test_held_stop_lanes import _run_pm

_ROOT = Path(__file__).resolve().parent.parent
_APP_PATH = _ROOT / "app.py"
_HEADLESS_PATH = _ROOT / "stock_analyzer" / "headless_alert_engine.py"
_CRON_PATH = _ROOT / "cron_runner.py"
_ET = pytz.timezone("America/New_York")


@pytest.fixture(autouse=True)
def early_trail(monkeypatch):
    # trailing / ratchet from the start of the synthetic history (as test_held_stop_wiring)
    monkeypatch.setattr(C, "HELD_TRAIL_START", "2025-01-01")


@pytest.fixture(scope="module")
def s():
    return Scenario()


# ── builders ─────────────────────────────────────────────────────────────────
def _journal(rows):
    """rows: [(action, shares, price, date)]"""
    return pd.DataFrame([{"id": i + 1, "ticker": T, "action": a, "shares": sh, "price": px,
                          "traded_at": f"{d.isoformat()}T14:00:00+00:00"}
                         for i, (a, sh, px, d) in enumerate(rows)])


def _entry(s):
    return _journal([("BUY", 10.0, s.avg, s.start)])


def _holdings(s, shares=10.0, avg=None):
    return [{"Ticker": T, "Shares": int(shares), "Shares Exact": shares,
             "Avg Cost ($)": s.avg if avg is None else avg}]


def _full(s, i, floors, trades=None, holdings=None, as_of=None):
    """The long-frame (full path) result for as_of = days[i] (bars before it)."""
    return hw.build_held_stops(
        holdings or _holdings(s), {}, _entry(s) if trades is None else trades,
        as_of or s.days[i], fetch_history=lambda t, p: s.df.iloc[:i], floors=floors)[T]


def _fallback(s, i, floors, trades=None, holdings=None, as_of=None, window=80):
    """The bundle-only (fallback) result: the long fetch fails, the app's rolling
    80-session window (ending at the last bar before `i`) is the only frame."""
    def _down(t, p):
        raise RuntimeError("unadjusted history unavailable")
    return hw.build_held_stops(
        holdings or _holdings(s), {T: {"df": s.df.iloc[max(0, i - window):i]}},
        _entry(s) if trades is None else trades, as_of or s.days[i],
        fetch_history=_down, floors=floors)[T]


def _db_shape(row):
    """A written row as `db.load_held_stop_floors_or_none` hands it back."""
    r = dict(row)
    for c in ("episode_start", "as_of", "stop_set_on", "last_buy", "ref_session", "bind_session"):
        if r.get(c) is not None:
            r[c] = date.fromisoformat(r[c])
    return {T: {r["episode_start"]: r}}


@pytest.fixture
def saved(s):
    """(floors, row, full result): the EOD row of day 110 (stop 101.62, trailing)."""
    full = _full(s, 110, {})
    rows = hf.rows_to_write({T: full}, s.days[110])
    assert len(rows) == 1
    return _db_shape(rows[0]), rows[0], full


# ── end to end: the _wrap pass-through, the reader, the writer ───────────────
def test_full_result_carries_floor_label_and_set_on_and_reaches_the_writer(s, saved):
    _, row, full = saved
    assert full["basis"] == "full" and full["floor"]["state"] == hf.NONE
    assert full["label"] == "Trailing Stop" and full["stop_set_on"] == s.days[110]
    # the _wrap pass-through is what lets rows_to_write see a floor dict at all
    assert row["ticker"] == T and row["as_of"] == s.days[110].isoformat()
    assert row["stop"] == round(full["stop"], 2) and row["bind_session"] is not None


def test_wrap_passes_floor_stop_set_on_and_label_through():
    base = hs._result(100.0, "full", "entry", "")
    w = hw._wrap(T, {**base, "floor": {"state": "none"}, "stop_set_on": date(2026, 1, 2),
                     "label": "Trailing Stop"}, "", 50.0)
    assert w["floor"] == {"state": "none"} and w["stop_set_on"] == date(2026, 1, 2)
    assert w["label"] == "Trailing Stop"
    plain = hw._wrap(T, base, "", 50.0)       # no floor on the input: None, label from label_for
    assert plain["floor"] is None and plain["stop_set_on"] is None and plain["label"] == "Entry Stop"


def test_floor_holds_the_stop_on_a_fallback_day_and_the_notes_say_so(s, saved):
    floors, row, _ = saved
    raw = _fallback(s, 120, {})
    eff = _fallback(s, 120, floors)
    assert raw["basis"] == eff["basis"] == "partial"          # the basis is never changed
    assert eff["fell_back"] and eff["degraded"]
    assert eff["stop"] == row["stop"] > raw["stop"]
    assert eff["floor"]["state"] == hf.APPLIED and eff["floor"]["computed"] == pytest.approx(raw["stop"])
    assert eff["label"] == hw.label_for("partial", row["binding"], row["ratchet_label"])
    assert eff["stop_set_on"] == date.fromisoformat(row["stop_set_on"])
    note = hw.degraded_notes({T: eff})[0][2]
    assert note.startswith(eff["note"])
    assert f"The stop is held at \\${row['stop']:,.2f}" in note
    assert f"saved on {row['stop_set_on']}" in note and "Today's figure from the shorter history was" in note
    assert "only moves up" not in note.lower() and "only moves up" not in eff["note"].lower()
    assert "\\" not in hw.plain_text_note(note) and "$" in hw.plain_text_note(note)
    # the same effective result is what the writer would see next: partial is never written
    assert hf.rows_to_write({T: eff}, s.days[120]) == []


def test_floors_empty_leaves_the_fallback_figure_alone(s):
    raw = _fallback(s, 120, {})
    assert raw["floor"]["state"] == hf.NONE and raw["floor"]["note"] == ""
    assert hw.degraded_notes({T: raw})[0][2] == raw["note"]


def test_a_full_basis_floor_binds_by_the_saved_amount_without_a_note(s, saved):
    floors, row, _ = saved
    bumped = {T: {k: {**v, "stop": v["stop"] + 0.50} for k, v in floors[T].items()}}
    eff = _full(s, 115, bumped)
    assert eff["basis"] == "full" and eff["floor"]["state"] == hf.APPLIED
    assert eff["stop"] == pytest.approx(row["stop"] + 0.50)
    assert eff["floor"]["note"] == "" and hw.degraded_notes({T: eff}) == []   # no banner on a full day
    assert hf.rows_to_write({T: eff}, s.days[115])[0]["stop"] == pytest.approx(row["stop"] + 0.50)


# ── the inert golden ─────────────────────────────────────────────────────────
def _scenarios(s):
    buy_trades = _journal([("BUY", 10.0, s.avg, s.days[118])])
    return {
        "full": lambda fl: _full(s, 118, fl),
        "fallback": lambda fl: _fallback(s, 120, fl),
        "prior_session": lambda fl: hw.build_held_stops(
            _holdings(s), {T: {"df": s.df.iloc[:118]}}, None, s.days[118],
            fetch_history=lambda t, p: s.df.iloc[:118], floors=fl)[T],
        "unavailable": lambda fl: hw.build_held_stops(
            _holdings(s), {}, _entry(s), s.days[118],
            fetch_history=lambda t, p: 1 / 0, floors=fl)[T],
        "buy_day": lambda fl: hw.build_held_stops(
            _holdings(s, avg=s.avg), {T: {"df": s.df.iloc[:118]}}, buy_trades, s.days[118],
            fetch_history=lambda t, p: s.df.iloc[:118], floors=fl)[T],
    }


def _core(r):
    return {k: v for k, v in r.items() if k not in ("floor", "stop_set_on")}


@pytest.mark.parametrize("name", ["full", "fallback", "prior_session", "unavailable", "buy_day"])
def test_inert_golden_empty_floors_is_identical_and_none_changes_only_the_degraded_notes(
        s, saved, monkeypatch, name):
    run = _scenarios(s)[name]
    with monkeypatch.context() as mp:                       # "today": no floor step at all
        mp.setattr(hw.hsf, "apply_floor", lambda out, *a, **k: out)
        ref = run({})
    assert _core(run({})) == _core(ref)
    none_run = run(None)
    assert _core(none_run) == _core(ref)                    # note field, stop, label: unchanged
    base = hw.full_note(ref)
    got = hw.full_note(none_run)
    if ref["basis"] == "partial":
        assert got.startswith(base) and got != base
        assert "saved full-history stop couldn't be read" in got
    else:
        assert got == base
    assert hw.full_note(run({})) == base
    # a result with floors unreadable is never written
    assert hf.rows_to_write({T: none_run}, s.days[118]) == []


def test_unreadable_floors_on_a_full_day_say_nothing_and_write_nothing(s):
    r = _full(s, 118, None)
    assert r["basis"] == "full" and r["floor"]["state"] == hf.UNREADABLE and r["floor"]["note"] == ""
    assert hw.degraded_notes({T: r}) == [] and hf.rows_to_write({T: r}, s.days[118]) == []


def test_floors_is_a_required_keyword_with_no_default():
    p = inspect.signature(hw.build_held_stops).parameters["floors"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
    assert inspect.signature(hw._one).parameters["floors"].default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        hw.build_held_stops([], {}, None, date(2026, 10, 30), fetch_history=lambda t, p: None)


def test_a_junk_floors_value_reads_as_unreadable(s):
    assert _full(s, 118, ["not", "a", "dict"])["floor"]["state"] == hf.UNREADABLE


# ── manual stop precedence and the portfolio columns ─────────────────────────
def _port(s, auto, manual=None, price=110.0):
    loaded = {T: {"current_price": price, "stop": 1.0, "sector": "Technology",
                  "rec": {"icon": "G", "label": "Buy"}, "total": 70}}
    ms = {T: {"stop_price": manual}} if manual is not None else None
    return pf.build_portfolio_df(_holdings(s), loaded, manual_stops=ms, held_stops={T: auto})


def test_manual_stop_between_computed_and_floor_loses_above_floor_wins(s, saved):
    floors, row, _ = saved
    raw, eff = _fallback(s, 120, {}), _fallback(s, 120, floors)
    between = round((raw["stop"] + eff["stop"]) / 2, 2)
    above = round(eff["stop"] + 1.0, 2)
    for manual, wins in ((between, False), (above, True), (None, False)):
        r = _port(s, eff, manual).iloc[0]
        assert r["Auto Stop"] == eff["stop"]
        if wins:
            assert r["Stop"] == manual and r["Stop Type"] == "Manual"
        else:
            assert r["Stop"] == eff["stop"] and r["Stop Type"] == eff["label"]
        # Analysis (analysis_manual_override on the frame's "Auto Stop") agrees with the build
        stop, is_manual = hw.analysis_manual_override(
            r["Auto Stop"], {"stop_price": manual} if manual is not None else None)
        assert (stop, is_manual) == (r["Stop"], r["Stop Type"] == "Manual")
    # the same manual stop, no floor: it would have won
    assert _port(s, raw, between).iloc[0]["Stop"] == between


def test_stop_saved_columns_only_when_the_floor_bound(s, saved):
    floors, row, full = saved
    eff = _fallback(s, 120, floors)
    r = _port(s, eff).iloc[0]
    assert r["Stop Saved"] == eff["stop"] and r["Stop Saved On"] == row["stop_set_on"]
    assert "The stop is held at" in r["Stop Note"] and r["Stop Note"].startswith(eff["note"])
    for h in (_fallback(s, 120, {}), _fallback(s, 120, None), full, None):
        r2 = _port(s, h).iloc[0]
        assert pd.isna(r2["Stop Saved"]) and pd.isna(r2["Stop Saved On"])
    # a floor that exists but did not bind (today's figure reached it) shows nothing
    nb = _full(s, 111, floors)
    assert nb["floor"]["state"] in (hf.NOT_BINDING, hf.APPLIED)
    if nb["floor"]["state"] == hf.NOT_BINDING:
        assert pd.isna(_port(s, nb).iloc[0]["Stop Saved"])


def test_brief_breach_and_sig_read_the_floored_stop(s, saved):
    floors, _, _ = saved
    raw, eff = _fallback(s, 120, {}), _fallback(s, 120, floors)
    price = (raw["stop"] + eff["stop"]) / 2          # between the two: a breach only with the floor
    assert hw.brief_breached_set(_port(s, raw, price=price)) == set()
    assert hw.brief_breached_set(_port(s, eff, price=price)) == {T}
    assert hw.held_sig(_port(s, raw)) != hw.held_sig(_port(s, eff))   # the Home memo rebuilds


# ── display (C3c-5) ──────────────────────────────────────────────────────────
def test_saved_row_only_when_it_binds_and_never_a_new_label(s, saved):
    floors, row, full = saved
    eff = _fallback(s, 120, floors)
    r = _port(s, eff).iloc[0]
    h = hv.from_row(r)
    assert h["saved"] == eff["stop"] and h["saved_on"] == row["stop_set_on"]
    rows = hv.component_rows(h)
    saved_rows = [x for x in rows if x["kind"] == "saved"]
    assert len(saved_rows) == 1 and saved_rows[0]["value"] == eff["stop"]
    assert saved_rows[0]["label"] == f"{eff['label']} (saved)" and "Saved Stop" not in saved_rows[0]["label"]
    assert row["stop_set_on"] in saved_rows[0]["text"] and "$" not in saved_rows[0]["text"]
    assert [x["value"] for x in rows] == sorted((x["value"] for x in rows), reverse=True)
    # the build_held_stops result itself reads the same (floor dict, no columns)
    assert [x["kind"] for x in hv.component_rows(eff)].count("saved") == 1
    for not_bound in (_port(s, _fallback(s, 120, {})).iloc[0], _port(s, full).iloc[0]):
        assert all(x["kind"] != "saved" for x in hv.component_rows(hv.from_row(not_bound)))
    assert all(x["kind"] != "saved" for x in hv.component_rows({"components": {"entry": 90.0}}))


def test_what_if_and_driver_label_know_a_binding_floor(s, saved):
    floors, row, _ = saved
    eff = _fallback(s, 120, floors)
    h = hv.from_row(_port(s, eff).iloc[0])
    low = hv.what_if(h, 100.0, s.avg)
    assert low["stop"] == eff["stop"] and low["saved"] == eff["stop"] and not low["raised"]
    assert hv.driver_label(low) == eff["label"] and "saved" not in hv.driver_label(low).lower()
    high = hv.what_if(h, 160.0, s.avg)               # a rally lifts the rebuilt parts above it
    assert high["stop"] > eff["stop"] and high["raised"]
    assert hv.driver_label(high) != "Stop" and hv.driver_label(high) in {
        "Entry Stop", "Trailing Stop"} | {lab for _, _, lab in C.STOP_RATCHET_LEVELS}
    # no floor: the old behaviour, the old keys' values unchanged
    plain = hv.what_if(hv.from_row(_port(s, _fallback(s, 120, {})).iloc[0]), 100.0, s.avg)
    assert plain["saved"] is None and plain["saved_label"] is None


def test_no_label_a_floor_can_produce_makes_stop_noun_print_saved():
    labels = {hw.label_for(b, k, rl) for b in ("full", "partial") for k, rl in
              (("entry", None), ("trailing", None), ("ratchet", None),
               *(("ratchet", lab) for _, _, lab in C.STOP_RATCHET_LEVELS))}
    assert labels and all("saved" not in x.lower() for x in labels)
    assert all("saved" not in sbt.stop_noun(x).lower() for x in labels)


def test_partial_notes_never_claim_only_moves_up(s, saved):
    floors, _, _ = saved
    for fl in ({}, floors, None, {T: {s.start: {**next(iter(floors[T].values())), "avg_cost": 1.0}}}):
        r = _fallback(s, 120, fl)
        assert "only moves up" not in hw.full_note(r).lower()


# ── wiring-level time matrix ─────────────────────────────────────────────────
def test_saturday_visit_uses_fridays_row(s):
    i = next(k for k in range(112, 135) if s.days[k - 1].weekday() == 4)   # days[i-1] is a Friday
    friday = s.days[i - 1]
    full = _full(s, i - 1, {})                                              # the Friday EOD build
    row = hf.rows_to_write({T: full}, friday)[0]
    saturday = friday + dt.timedelta(days=2)
    eff = _fallback(s, i, _db_shape(row), as_of=saturday)
    assert eff["floor"]["state"] in (hf.APPLIED, hf.NOT_BINDING)
    assert eff["floor"]["row_as_of"] == friday
    assert eff["stop"] >= row["stop"]


def test_late_logged_add_invalidates_and_late_partial_sell_keeps_the_floor(s, saved):
    floors, row, _ = saved
    d_late = s.days[112]
    px = float(s.df.loc[pd.Timestamp(d_late), "Close"])
    avg_add = (10 * s.avg + 5 * px) / 15
    add = _journal([("BUY", 10.0, s.avg, s.start), ("BUY", 5.0, px, d_late)])
    r = _fallback(s, 120, floors, trades=add, holdings=_holdings(s, 15.0, avg_add))
    assert r["floor"]["state"] == hf.INVALID and r["floor"]["reason"].startswith("trades")
    assert "your trades in this position changed" in hw.full_note(r)
    sell = _journal([("BUY", 10.0, s.avg, s.start), ("SELL", 4.0, px, d_late)])
    r2 = _fallback(s, 120, floors, trades=sell, holdings=_holdings(s, 6.0, s.avg))
    assert r2["floor"]["state"] == hf.APPLIED and r2["stop"] == row["stop"]


def test_late_first_buy_that_moves_the_start_finds_no_row(s, saved):
    floors, _, _ = saved
    moved = _journal([("BUY", 10.0, s.avg, s.days[s.e_idx + 2])])
    r = _fallback(s, 120, floors, trades=moved)
    assert r["floor"]["state"] in (hf.NONE, hf.NA)


# ── cron: premarket heartbeat (U4 refined) ───────────────────────────────────
def _pm_payload(held_stops, readable):
    return {"alerts": [{"kind": "stop_breach", "ticker": "AAPL"}], "built_at": "x", "errors": [],
            "held_stops_failed": None, "held_stop_notes": [],
            "held_stops": held_stops, "held_stop_floors_readable": readable}


_DAY = datetime(2026, 12, 1, 8, 30)


def test_premarket_red_only_with_unreadable_floors_and_a_partial_name(monkeypatch):
    part = {"AAA": {"basis": "partial"}, "BBB": {"basis": "full"}}
    rc, sent, saved_, _, _ = _run_pm(monkeypatch, _pm_payload(part, False), _DAY)
    assert rc == 1 and len(sent) == 1 and len(saved_) == 1          # alerts still sent
    assert "held_stop_floor" in cr._LAST_LANE_FAILURE_DETAIL and "AAA" in cr._LAST_LANE_FAILURE_DETAIL
    for held, readable in (({"AAA": {"basis": "full"}}, False),               # nobody partial
                           ({"AAA": {"basis": "prior_session"}, "BBB": {"basis": "unavailable"}}, False),
                           (part, True),                                       # readable
                           ({"AAA": {"basis": "buy_day"}}, False)):
        rc, *_ = _run_pm(monkeypatch, _pm_payload(held, readable), _DAY)
        assert rc == 0, (held, readable)


def test_premarket_logs_one_line_when_unreadable_but_nothing_partial(monkeypatch, capsys):
    _run_pm(monkeypatch, _pm_payload({"AAA": {"basis": "full"}}, False), _DAY)
    assert capsys.readouterr().out.count("held_stop_floor: saved floors could not be read") == 1
    _run_pm(monkeypatch, _pm_payload({"AAA": {"basis": "full"}}, True), _DAY)
    assert "held_stop_floor" not in capsys.readouterr().out


# ── cron: the EOD writer step ────────────────────────────────────────────────
_WED_2030 = _ET.localize(datetime(2026, 11, 4, 20, 30))    # 20:30 EST = 01:30 UTC on 11-05


@pytest.fixture
def logs(monkeypatch):
    out = []
    monkeypatch.setattr(cr, "_log", lambda m, *a, **k: out.append(str(m)))
    return out


@pytest.fixture
def writes(monkeypatch):
    calls = []

    def _save(rows):
        calls.append(rows)
        return True, f"wrote {len(rows)} row(s)"
    monkeypatch.setattr(cr.db, "save_held_stop_floors", _save)
    return calls


def _eod_payload(s, readable=True, i=118):
    return {"held_stops": {T: _full(s, i, {})}, "held_stop_floors_readable": readable}


def test_eod_writes_the_effective_full_stops_and_logs_wrote_n_only_on_true(s, logs, writes):
    now = _ET.localize(datetime.combine(s.days[118], dt.time(16, 30)))
    assert cr._eod_held_stop_floors(_eod_payload(s), now) == []
    assert len(writes) == 1 and [r["ticker"] for r in writes[0]] == [T]
    assert writes[0][0]["as_of"] == s.days[118].isoformat()
    assert any(m.startswith("held_stop_floor: wrote 1 row(s)") for m in logs)


def test_eod_save_failure_is_a_lane_failure_and_never_logs_wrote(s, logs, monkeypatch):
    now = _ET.localize(datetime.combine(s.days[118], dt.time(16, 30)))
    detail = "held_stop_floors table is missing -- apply docs/sql/held-stop-floors.sql (x)"
    monkeypatch.setattr(cr.db, "save_held_stop_floors", lambda rows: (False, detail))
    assert cr._eod_held_stop_floors(_eod_payload(s), now) == ["held_stop_floor: " + detail]
    assert not any("wrote" in m for m in logs) and any("NOT written" in m for m in logs)


def test_eod_save_that_raises_never_raises_out(s, logs, monkeypatch):
    now = _ET.localize(datetime.combine(s.days[118], dt.time(16, 30)))

    def _boom(rows):
        raise RuntimeError("db down")
    monkeypatch.setattr(cr.db, "save_held_stop_floors", _boom)
    out = cr._eod_held_stop_floors(_eod_payload(s), now)
    assert len(out) == 1 and out[0].startswith("held_stop_floor:") and "db down" in out[0]


def test_eod_unreadable_floors_skip_the_write_and_add_a_failure(s, logs, writes):
    now = _ET.localize(datetime.combine(s.days[118], dt.time(16, 30)))
    out = cr._eod_held_stop_floors(_eod_payload(s, readable=False), now)
    # the red line names the DDL file (spec §1): a missing table reads as unreadable here
    assert out == ["held_stop_floor: floors could not be read; not written "
                   "(if the table is new: apply docs/sql/held-stop-floors.sql)"] and writes == []
    assert cr._eod_held_stop_floors({"held_stops": _eod_payload(s)["held_stops"]}, now) == out


def test_eod_non_trading_day_and_missing_payload_write_nothing(s, logs, writes):
    saturday = _ET.localize(datetime(2026, 11, 7, 20, 30))
    assert cr._eod_held_stop_floors(_eod_payload(s), saturday) == [] and writes == []
    weekday = _ET.localize(datetime.combine(s.days[118], dt.time(16, 30)))
    assert cr._eod_held_stop_floors({"reason": "no_holdings"}, weekday) == [] and writes == []
    assert cr._eod_held_stop_floors({"held_stops": {}, "held_stop_floors_readable": True},
                                    weekday) == [] and writes == []


def test_eod_uses_the_et_date_not_the_utc_date(logs, writes):
    es = Scenario(end=date(2026, 11, 3))                 # last bar Tue 11-03, as_of Wed 11-04
    payload = {"held_stops": {T: hw.build_held_stops(
        _holdings(es), {}, _entry(es), date(2026, 11, 4),
        fetch_history=lambda t, p: es.df, floors={})[T]}, "held_stop_floors_readable": True}
    assert _WED_2030.astimezone(pytz.utc).date() == date(2026, 11, 5)
    assert cr._eod_held_stop_floors(payload, _WED_2030) == []
    assert writes[0][0]["as_of"] == "2026-11-04"


def test_eod_logs_a_full_day_bind_with_the_amount_and_still_writes(s, saved, logs, writes):
    floors, row, _ = saved
    bumped = {T: {k: {**v, "stop": v["stop"] + 0.50} for k, v in floors[T].items()}}
    eff = _full(s, 115, bumped)
    now = _ET.localize(datetime.combine(s.days[115], dt.time(16, 30)))
    assert cr._eod_held_stop_floors({"held_stops": {T: eff}, "held_stop_floors_readable": True}, now) == []
    line = next(m for m in logs if "bound on a full-history day" in m)
    assert line.startswith(f"held_stop_floor: {T} saved floor bound") and "(+" in line
    assert f"saved {row['stop'] + 0.50:.2f}" in line
    assert len(writes) == 1 and writes[0][0]["stop"] == pytest.approx(row["stop"] + 0.50)
    # no bind, no line
    logs.clear()
    cr._eod_held_stop_floors(_eod_payload(s), _ET.localize(datetime.combine(s.days[118], dt.time(16, 30))))
    assert not any("bound on a full-history day" in m for m in logs)


def test_eod_writer_never_writes_a_manual_stop_it_reads_held_stops_only():
    src = _CRON_PATH.read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(parse_file_cached(_CRON_PATH)) if isinstance(n, ast.FunctionDef)
              and n.name == "_eod_held_stop_floors")
    fn.body = [n for n in fn.body if not (isinstance(n, ast.Expr)
                                          and isinstance(n.value, ast.Constant))]   # drop the docstring
    body = ast.unparse(fn)
    assert "payload.get('held_stops')" in body or "payload['held_stops']" in body
    assert "port_df" not in body and "'Stop'" not in body and '"Stop"' not in body
    run = next(n for n in ast.walk(parse_file_cached(_CRON_PATH)) if isinstance(n, ast.FunctionDef)
               and n.name == "_run_eod")
    assert "_eod_held_stop_floors(payload, now_et)" in ast.unparse(run)
    assert "failures.extend(_eod_held_stop_floors(" in ast.unparse(run) and src


# ── the headless context ─────────────────────────────────────────────────────
def _ctx_with_floors(floors, held_stops):
    from tests.test_held_stop_lanes import GOOD, TODAY
    captured = {}

    def _bhs(*a, **k):
        captured.update(k)
        return held_stops
    with patch.object(hae.db, "has_db", return_value=True), \
         patch.object(hae.db, "load_holdings_or_none",
                      return_value=pd.DataFrame({"Ticker": ["AAA", "bbb"]})), \
         patch.object(hae.db, "load_trades_or_none", return_value=GOOD), \
         patch.object(hae.db, "load_manual_stops_or_none", return_value={}), \
         patch.object(hae.db, "load_held_stop_floors_or_none", return_value=floors) as ld, \
         patch.object(hae, "fetch_risk_free_rate", return_value=0.045), \
         patch.object(hae, "fetch_spy", return_value=pd.DataFrame({"Close": [1.0, 2.0]})), \
         patch.object(hae, "_vix_level", return_value=16.0), \
         patch.object(hae, "load_bundle", return_value={"financials": {}}), \
         patch.object(hae, "_build_open_lots", return_value=[]), \
         patch.object(hae.exit_advisor, "material_add_window_days", return_value=None), \
         patch.object(hae._held_stop_wiring, "build_held_stops", side_effect=_bhs), \
         patch.object(hae, "build_portfolio_df",
                      return_value=pd.DataFrame({"Ticker": ["AAA"], "Weight (%)": [10.0]})), \
         patch.object(hae, "compute_portfolio_risk_metrics", return_value={"beta": 1.1}), \
         patch.object(hae, "run_scenario", return_value={"port_impact_pct": -5.0}), \
         patch.object(hae, "assess_fragility", return_value={"severity": "elevated"}):
        ctx = hae._build_context(TODAY)
    return ctx, captured, ld


def test_build_context_reads_floors_once_for_a_real_ticker_list_and_publishes_readability():
    hsx = {"AAA": {"basis": "full", "stop": 90.0}}
    ctx, kw, ld = _ctx_with_floors({"AAA": {}}, hsx)
    ld.assert_called_once_with(["AAA", "BBB"])             # a real list, upper-cased, never None
    assert kw["floors"] == {"AAA": {}}
    assert ctx["held_stops"] is hsx and ctx["held_stop_floors_readable"] is True
    ctx, kw, _ = _ctx_with_floors({}, hsx)                  # read, none saved: readable
    assert kw["floors"] == {} and ctx["held_stop_floors_readable"] is True
    ctx, kw, _ = _ctx_with_floors(None, hsx)                # couldn't read: never collapsed to {}
    assert kw["floors"] is None and ctx["held_stop_floors_readable"] is False


# ── AST parity ───────────────────────────────────────────────────────────────
def _calls(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id == name)
        or (isinstance(n.func, ast.Attribute) and n.func.attr == name))]


def _func(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _kw(call, name):
    return next((k.value for k in call.keywords if k.arg == name), None)


def test_both_production_calls_pass_floors_and_the_app_reads_them_once_before_the_fan_out():
    app = parse_file_cached(_APP_PATH)
    calls = _calls(app, "build_held_stops")
    assert len(calls) == 1 and _kw(calls[0], "floors") is not None
    assert ast.unparse(_kw(calls[0], "floors")) == "_floors"
    held = ast.unparse(_func(app, "_held_stops_for"))
    assert held.count("_held_stop_floors_for(") == 1
    assert held.index("_held_stop_floors_for(") < held.index("ThreadPoolExecutor")
    head = parse_file_cached(_HEADLESS_PATH)
    hcalls = _calls(_func(head, "_build_context"), "build_held_stops")
    assert len(hcalls) == 1 and ast.unparse(_kw(hcalls[0], "floors")) == "held_stop_floors"
    load = _calls(_func(head, "_build_context"), "load_held_stop_floors_or_none")
    assert len(load) == 1 and ast.unparse(load[0].args[0]) == "held_tickers"


def test_app_floor_reader_is_cached_per_et_day_and_raises_on_none():
    app = parse_file_cached(_APP_PATH)
    fn = _func(app, "_cached_held_stop_floors")
    dec = ast.unparse(fn.decorator_list[0])
    assert "cache_data" in dec and "86400" in dec
    assert [a.arg for a in fn.args.args] == ["tickers", "et_date"]   # no underscore: the key
    body = ast.unparse(fn)
    assert "load_held_stop_floors_or_none(" in body and "is None" in body and "raise " in body
    reader = ast.unparse(_func(app, "_held_stop_floors_for"))
    assert "_today_et().isoformat()" in reader and "except Exception" in reader and "return None" in reader
    assert "st.session_state" not in body + reader      # no new coordination key to register


def test_headless_payloads_carry_the_floor_inputs():
    head = parse_file_cached(_HEADLESS_PATH)
    for name in ("_build_context", "compute_eod", "compute_protective_alerts"):
        src = ast.unparse(_func(head, name))
        assert "'held_stops'" in src and "'held_stop_floors_readable'" in src, name


# ── confirming-round additions (Opus review of C3c-4 + C3c-5, 2026-11-16) ──────
def test_unrecorded_split_on_a_fallback_day_never_adds_a_breach(s, saved):
    """Spec split guard, end to end: Yahoo re-states the whole 6-month window by 1/2
    (a 2:1 split the journal doesn't know about). The saved row's closes no longer
    match, so the floor is invalid and the name's breach status is exactly what it
    would be with no floor at all: the floor can't create a false sell-all."""
    floors, _, _ = saved
    i = 120
    halved = s.df.iloc[max(0, i - 80):i].copy()
    halved.loc[:, ["Open", "High", "Low", "Close"]] = halved[["Open", "High", "Low", "Close"]] * 0.5

    def _down(t, p):
        raise RuntimeError("unadjusted history unavailable")

    def _build(fl):
        return hw.build_held_stops(_holdings(s), {T: {"df": halved}}, _entry(s), s.days[i],
                                   fetch_history=_down, floors=fl)[T]
    raw, eff = _build({}), _build(floors)
    assert eff["floor"]["state"] == hf.INVALID
    assert eff["floor"]["reason"].startswith(hf.R_HISTORY)
    assert eff["stop"] == raw["stop"]
    price = float(halved["Close"].iloc[-1])
    assert hw.brief_breached_set(_port(s, eff, price=price)) == \
        hw.brief_breached_set(_port(s, raw, price=price))


def test_saved_row_text_blames_the_shorter_history_only_on_a_degraded_day():
    def _h(basis):
        return {"basis": basis, "binding": "trailing", "components": {"entry": 90.0},
                "floor": {"state": "applied", "value": 95.0, "set_on": date(2026, 11, 13)}}
    full = [r for r in hv.component_rows(_h("full")) if r["kind"] == "saved"][0]["text"]
    part = [r for r in hv.component_rows(_h("partial")) if r["kind"] == "saved"][0]["text"]
    assert "shorter price history" not in full and "today's figure came out lower" in full
    assert "today's shorter price history gave a lower figure" in part


def test_a_floor_bug_keeps_todays_stop_and_writes_nothing(s, monkeypatch):
    """Opus non-blocking #3: an exception inside apply_floor must leave the
    unfloored stop in place (never "Stop Unavailable") and mark the floor
    unreadable, so the fail-closed writer skips the name."""
    good = _full(s, 110, {})

    def _boom(*a, **k):
        raise ValueError("floor bug")
    monkeypatch.setattr(hf, "apply_floor", _boom)
    out = _full(s, 110, {})
    assert out["basis"] == good["basis"] == "full"
    assert out["stop"] == good["stop"] and out["label"] == good["label"]
    assert out["floor"]["state"] == hf.UNREADABLE and "ValueError" in out["floor"]["reason"]
    assert out["stop_set_on"] is None
    assert hf.rows_to_write({T: out}, s.days[110]) == []
