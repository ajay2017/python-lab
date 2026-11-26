"""SlidingStop C3: the carried held stop, WIRED (docs/plans/sliding-stop-c3-spec.md
sections 1-8; parent plan docs/plans/sliding-stop.md sections 3, 5, 8).

Covers `build_held_stops(carry=)` end to end on synthetic frames, the inert golden
(`carry={}` / `carry=None`), the mismatch / young-listing / full / buy-day paths, the
`episode_status` and `buy_day_path` keys, the status != ok frame order (U6), the
slow-bleed invariant driven day by day through the real builder + writer, the
time-context matrix, the headless context, the cron premarket and EOD steps, the app
read. The display half (commit 2: portfolio columns, explainer row, `driver_label`) is in
test_held_stop_carry_display.py; the pure layer is in test_held_stop_carry.py. No network,
no DB, no app import (app.py is read as text / AST)."""
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
from stock_analyzer import held_stop_carry as hc
from stock_analyzer import held_stop_wiring as hw
from stock_analyzer import stop_breach_text as sbt
from stock_analyzer import young_holding_stop as yhs
from stock_analyzer.act_today_precedence import is_stop_breached
from tests._ast_cache import parse_file_cached
from tests.test_held_stop_carry import AVG, frame_from, make_row, steps_closes, trading_days
from tests.test_held_stop_floor import Scenario, T
from tests.test_held_stop_floor_wiring import _entry, _holdings, _journal
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


# ── builders ─────────────────────────────────────────────────────────────────
# A MISMATCHED holding: the journal nets 5 shares, the broker holds 10.
MISMATCH_JOURNAL = pd.DataFrame([{"id": 1, "ticker": T, "action": "BUY", "shares": 5.0,
                                  "price": AVG, "traded_at": "2026-01-02T15:00:00+00:00"}])
MISMATCH_SIG = (1, 1)
HOLD = [{"Ticker": T, "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": AVG}]
CORE_SKIP = ("carry", "episode_status", "buy_day_path")


def falling(n=80, end=date(2026, 9, 30)):
    """A name falling 1 a day (ATR 2.0 exactly): the prior-close stop is Close - 6."""
    days = trading_days(end, n)
    closes = steps_closes([-1.0] * (n - 1))
    return days, closes, frame_from(days, closes)


def build(frame, as_of, carry, *, trades=MISMATCH_JOURNAL, holdings=HOLD, held=None, fetch=None):
    fetch = fetch or (lambda t, p: frame)
    return hw.build_held_stops(holdings, {} if held is None else held, trades, as_of,
                               fetch_history=fetch, floors={}, carry=carry)[T]


def mrow(frame, days, i, stop, **over):
    """A saved row for the MISMATCH_JOURNAL fingerprint, as the EOD of days[i-1] leaves it."""
    return make_row(frame, days, i, stop=stop, journal_buy_count=MISMATCH_SIG[0],
                    journal_buy_max_id=MISMATCH_SIG[1], **over)


def young():
    """A young listing: an OK episode (the journal agrees) but only 40 bars of history and
    the buy on bar 5, so no part of the full stop can be computed (< 60 warm-up bars)."""
    m = 40
    days = trading_days(date(2026, 9, 30), m)
    frame = frame_from(days, steps_closes([-0.2] * (m - 1), start=100.0))
    px = float(frame["Close"].iloc[5])
    trades = pd.DataFrame([{"id": 1, "ticker": T, "action": "BUY", "shares": 10.0, "price": px,
                            "traded_at": f"{days[5].isoformat()}T15:00:00+00:00"}])
    hold = [{"Ticker": T, "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": px}]
    return days, frame, trades, hold, px, date(2026, 10, 1)


@pytest.fixture(scope="module")
def s():
    return Scenario()


# ── 1. signature and constants ───────────────────────────────────────────────
def test_carry_is_a_required_keyword_with_no_default():
    p = inspect.signature(hw.build_held_stops).parameters["carry"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
    assert inspect.signature(hw._one).parameters["carry"].default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        hw.build_held_stops([], {}, None, date(2026, 10, 30), fetch_history=lambda t, p: None,
                            floors={})


def test_the_two_note_sentences_and_the_label_are_pinned():
    assert hw.LABEL_CARRIED == "Carried Stop"
    assert hw.NOTE_NO_TRAIL == "It does not trail, and it can move down as the price falls."
    assert hw.NOTE_NO_TRAIL_CARRIED == (
        "It does not trail on its own; a saved stop keeps it from moving down while your "
        "trades in this position don't change.")


# ── 2. inert golden ──────────────────────────────────────────────────────────
def _scenarios(s):
    days, closes, fall = falling()
    prev = float(s.df["Close"].iloc[117])               # a fill at the prior close: accepted
    buy_trades = _journal([("BUY", 10.0, prev, s.days[118])])

    def _down(t, p):
        raise RuntimeError("unadjusted history unavailable")
    return {
        "full": lambda cy: hw.build_held_stops(
            _holdings(s), {}, _entry(s), s.days[118], fetch_history=lambda t, p: s.df.iloc[:118],
            floors={}, carry=cy)[T],
        "partial": lambda cy: hw.build_held_stops(
            _holdings(s), {T: {"df": s.df.iloc[38:120]}}, _entry(s), s.days[120],
            fetch_history=_down, floors={}, carry=cy)[T],
        "prior_session": lambda cy: hw.build_held_stops(
            _holdings(s), {T: {"df": s.df.iloc[:118]}}, None, s.days[118],
            fetch_history=lambda t, p: s.df.iloc[:118], floors={}, carry=cy)[T],
        "mismatch": lambda cy: build(fall, days[60], cy),
        "buy_day": lambda cy: hw.build_held_stops(
            _holdings(s, avg=prev), {T: {"df": s.df.iloc[:118]}}, buy_trades, s.days[118],
            fetch_history=lambda t, p: s.df.iloc[:118], floors={}, carry=cy)[T],
        "unavailable": lambda cy: hw.build_held_stops(
            _holdings(s), {}, _entry(s), s.days[118], fetch_history=lambda t, p: 1 / 0,
            floors={}, carry=cy)[T],
    }


def _core(r):
    return {k: v for k, v in r.items() if k not in CORE_SKIP}


@pytest.mark.parametrize("name", ["full", "partial", "prior_session", "mismatch", "buy_day",
                                  "unavailable"])
def test_inert_golden_empty_carry_is_identical_and_none_changes_only_the_carry_state(
        s, monkeypatch, name):
    run = _scenarios(s)[name]
    with monkeypatch.context() as mp:                       # "today": no carry step at all
        mp.setattr(hw.hsc, "apply_carry", lambda out, *a, **k: out)
        ref = run({})
    got_empty, got_none = run({}), run(None)
    assert _core(got_empty) == _core(ref) and _core(got_none) == _core(ref)   # stop, label, note
    assert got_empty["stop"] == got_none["stop"] == ref["stop"]
    base = hw.full_note(ref)
    assert hw.full_note(got_empty) == base                  # state none / n/a: no extra sentence
    if ref["basis"] in ("partial", "prior_session"):
        assert got_empty["carry"]["state"] == hc.NONE
        assert got_none["carry"]["state"] == hc.UNREADABLE
        assert hw.full_note(got_none).startswith(base) and hw.full_note(got_none) != base
        assert "saved carried stop couldn't be read" in hw.full_note(got_none)
    elif "carry" in got_none:
        assert got_none["carry"]["state"] == hc.NA
        assert hw.full_note(got_none) == base


def test_a_junk_carry_value_reads_as_unreadable():
    days, _, fall = falling()
    assert build(fall, days[60], ["not", "a", "dict"])["carry"]["state"] == hc.UNREADABLE


# ── 3. mismatch path ─────────────────────────────────────────────────────────
def test_a_valid_saved_row_above_todays_figure_binds_with_the_carried_label_and_note():
    days, closes, fall = falling()
    i = 60
    raw = build(fall, days[i], {})
    row = mrow(fall, days, i, round(raw["stop"] + 5.0, 2))
    eff = build(fall, days[i], {T: row})
    assert eff["basis"] == "prior_session" and eff["episode_status"] == "mismatch"
    assert eff["stop"] == row["stop"] > raw["stop"]
    assert eff["label"] == hw.LABEL_CARRIED and raw["label"] == hw.LABEL_PRIOR
    assert eff["carry"]["state"] == hc.APPLIED and eff["carry"]["computed"] == pytest.approx(raw["stop"])
    assert hw.NOTE_NO_TRAIL_CARRIED in eff["note"] and hw.NOTE_NO_TRAIL not in eff["note"]
    assert hw.NOTE_NO_TRAIL in raw["note"]
    full = hw.full_note(eff)
    assert full.startswith(eff["note"]) and eff["carry"]["note"] in full
    assert hw.degraded_notes({T: eff})[0] == (T, hw.LABEL_CARRIED, full)
    # the plain-text consumers (email footer, Home) go through full_note too
    assert eff["carry"]["note"] in yhs.plain_notes({T: eff}, None)[0]   # (the footer unescapes later)
    assert "\\" not in hw.plain_text_note(full)


def test_a_saved_row_below_todays_figure_does_not_bind_but_the_note_still_says_so():
    days, _, fall = falling()
    i = 60
    raw = build(fall, days[i], {})
    row = mrow(fall, days, i, round(raw["stop"] - 5.0, 2))
    eff = build(fall, days[i], {T: row})
    assert eff["carry"]["state"] == hc.NOT_BINDING
    assert eff["stop"] == raw["stop"] and eff["label"] == hw.LABEL_PRIOR
    assert hw.NOTE_NO_TRAIL_CARRIED in eff["note"] and hw.NOTE_NO_TRAIL not in eff["note"]
    assert eff["carry"]["note"] == ""                      # nothing to explain: no extra sentence
    assert hw.full_note(eff) == eff["note"]


def test_a_lapsed_row_keeps_todays_stop_and_todays_wording():
    days, _, fall = falling()
    i = 60
    raw = build(fall, days[i], {})
    row = mrow(fall, days, i, round(raw["stop"] + 5.0, 2), avg_cost=AVG + 0.01)
    eff = build(fall, days[i], {T: row})
    assert eff["carry"]["state"] == hc.LAPSED and eff["stop"] == raw["stop"]
    assert hw.NOTE_NO_TRAIL in eff["note"] and hw.NOTE_NO_TRAIL_CARRIED not in eff["note"]
    assert "wasn't used because your average cost" in hw.full_note(eff)


def test_a_carry_bug_in_the_inputs_keeps_the_stop_and_is_the_failed_shape(monkeypatch):
    days, _, fall = falling()
    raw = build(fall, days[60], {})

    def _boom(*a, **k):
        raise ValueError("sig bug")
    monkeypatch.setattr(hw.hsc, "journal_buy_sig", _boom)
    eff = build(fall, days[60], {})
    assert eff["stop"] == raw["stop"] and eff["label"] == raw["label"]
    assert eff["carry"]["state"] == hc.FAILED
    assert eff["carry"]["reason"].startswith(hc.CARRY_CHECK_FAILED) and "ValueError" in eff["carry"]["reason"]
    assert eff["carry"]["computed"] == raw["stop"]
    assert hc.carry_check_failures({T: eff}) == [T]


# ── 4. the young-listing ok path ─────────────────────────────────────────────
def test_young_listing_gets_the_carry_and_the_new_wording():
    days, frame, trades, hold, px, as_of = young()
    raw = hw.build_held_stops(hold, {}, trades, as_of, fetch_history=lambda t, p: frame,
                              floors={}, carry={})[T]
    assert raw["basis"] == "prior_session" and raw["episode_status"] == "ok"
    assert raw["note"].startswith("No part of the full stop can be computed yet (")
    assert "start isn't usable" not in raw["note"]
    assert "so the stop is the last completed close minus 3 x ATR" in raw["note"]
    assert raw["note"].endswith(hw.NOTE_NO_TRAIL)
    row = make_row(frame, days + [as_of], len(days), stop=round(raw["stop"] + 3.0, 2),
                   avg_cost=px, journal_buy_count=1, journal_buy_max_id=1)
    eff = hw.build_held_stops(hold, {}, trades, as_of, fetch_history=lambda t, p: frame,
                              floors={}, carry={T: row})[T]
    assert eff["carry"]["state"] == hc.APPLIED and eff["stop"] == row["stop"]
    assert eff["label"] == hw.LABEL_CARRIED and eff["episode_status"] == "ok"
    assert eff["note"].startswith("No part of the full stop can be computed yet (")
    assert eff["note"].endswith(hw.NOTE_NO_TRAIL_CARRIED)


def test_the_non_ok_note_keeps_todays_wording_and_an_empty_reason_has_a_fallback():
    mult = max(float(C.ATR_STOP_MULT), float(C.HELD_TRAIL_ATR_MULT))
    res = {"basis": "prior_session", "reason": "", "stop": 1.0}
    bad = hw._note_for(res, {"status": "mismatch", "reason": "r"}, bundle=False, n_sessions=None,
                       fetch_problem=None)
    assert bad == (f"This position's start isn't usable (r), so the stop is the last completed "
                   f"close minus {mult:g} x ATR. {hw.NOTE_NO_TRAIL}")
    ok = hw._note_for(res, {"status": "ok"}, bundle=False, n_sessions=None, fetch_problem=None)
    assert "(too little price history before the entry)" in ok


# ── 5. full basis ────────────────────────────────────────────────────────────
def test_full_basis_is_never_carried(s):
    kw = dict(holdings=_holdings(s), trades=_entry(s))
    days = s.days

    def _b(carry):
        return hw.build_held_stops(kw["holdings"], {}, kw["trades"], days[118],
                                   fetch_history=lambda t, p: s.df.iloc[:118],
                                   floors={}, carry=carry)[T]
    plain = _b({})
    high = _b({T: {"stop": 9999.0, "as_of": days[117].isoformat()}})
    assert plain["basis"] == high["basis"] == "full"
    assert high["carry"]["state"] == hc.NA and high["stop"] == plain["stop"]
    assert high["label"] == plain["label"] and high["episode_status"] == "ok"


# ── 6. buy-day paths ─────────────────────────────────────────────────────────
def _buy_day_result(s, price):
    trades = _journal([("BUY", 10.0, price, s.days[118])])
    return hw.build_held_stops(
        _holdings(s, avg=price), {T: {"df": s.df.iloc[:118]}}, trades, s.days[118],
        fetch_history=lambda t, p: s.df.iloc[:118], floors={},
        carry={T: {"stop": 9999.0, "as_of": s.days[117].isoformat()}})[T]


def test_buy_day_normal_fill_and_rejected_fill_are_never_carried_or_written(s):
    prev = float(s.df["Close"].iloc[117])
    ok = _buy_day_result(s, prev)
    assert ok["basis"] == "buy_day" and ok["buy_day"] is True
    rejected = _buy_day_result(s, prev * 2.0)            # a fill far outside the prior close's band
    assert rejected["basis"] == "prior_session" and rejected["buy_day"] is False
    for r in (ok, rejected):
        assert r["buy_day_path"] is True and "carry" not in r and r["episode_status"] == "ok"
        assert r["stop"] < 9999.0
        assert hc.rows_to_write({T: r}, s.days[118], journal_sigs={T: (1, 1)}, avg_costs={},
                                prior_rows={}, session_closes={T: float(s.df["Close"].iloc[118])},
                                journal_readable=True, carry_readable=True) == []


def test_only_buy_day_returns_carry_the_marker(s):
    days, _, fall = falling()
    assert "buy_day_path" not in build(fall, days[60], {})
    assert "buy_day_path" not in _scenarios(s)["full"]({})


# ── 7. episode_status on every path ──────────────────────────────────────────
def test_episode_status_is_on_every_path(s, monkeypatch):
    days, _, fall = falling()
    sc = _scenarios(s)
    assert sc["full"]({})["episode_status"] == "ok"
    assert sc["partial"]({})["episode_status"] == "ok"
    assert sc["buy_day"]({})["episode_status"] == "ok"
    assert sc["unavailable"]({})["episode_status"] == "ok"
    assert sc["mismatch"]({})["episode_status"] == "mismatch"
    assert sc["prior_session"]({})["episode_status"] == "unreadable"      # journal None
    no_hist = pd.DataFrame([{"id": 1, "ticker": "ZZZ", "action": "BUY", "shares": 5.0,
                             "price": AVG, "traded_at": "2026-01-02T15:00:00+00:00"}])
    assert build(fall, days[60], {}, trades=no_hist)["episode_status"] == "no_history"
    # non-ok episode with no frame anywhere: unavailable, status kept
    gone = build(fall, days[60], {}, fetch=lambda t, p: 1 / 0)
    assert gone["basis"] == "unavailable" and gone["episode_status"] == "mismatch"
    # a build that raised: the app's / driver's unavailable result, status None
    monkeypatch.setattr(hw, "_one", lambda *a, **k: 1 / 0)
    boom = hw.build_held_stops(HOLD, {}, MISMATCH_JOURNAL, days[60], fetch_history=lambda t, p: fall,
                               floors={}, carry={})[T]
    assert boom["basis"] == "unavailable" and boom["episode_status"] is None
    assert hw.unavailable_result(T, "x")["episode_status"] is None


# ── 8. U6: the status != ok frame order ──────────────────────────────────────
def test_non_ok_episode_asks_the_1y_fetch_first_and_the_bundle_only_when_it_fails():
    days, _, fall = falling()
    other = frame_from(days, steps_closes([-1.0] * (len(days) - 1), start=300.0))   # clearly different
    calls = []

    def fetch_ok(t, p):
        calls.append((t, p))
        return fall

    def fetch_none(t, p):
        calls.append((t, p))
        return None

    def fetch_raises(t, p):
        calls.append((t, p))
        raise RuntimeError("down")
    stop_fetch = build(fall, days[60], {}, fetch=fetch_ok, held={T: {"df": other}})["stop"]
    assert calls == [(T, "1y")] and stop_fetch == build(fall, days[60], {})["stop"]
    for fetch in (fetch_none, fetch_raises):
        calls.clear()
        r = build(fall, days[60], {}, fetch=fetch, held={T: {"df": other}})
        assert calls == [(T, "1y")]
        assert r["stop"] == build(other, days[60], {}, fetch=lambda t, p: other)["stop"] != stop_fetch
    # neither frame: unavailable
    assert build(fall, days[60], {}, fetch=fetch_none)["basis"] == "unavailable"


# ── 9. the slow-bleed invariant, end to end ──────────────────────────────────
def _simulate(frame, days, i0, *, use_carry=True, skip_writes=()):
    """Each day: EOD of D builds the effective stop and writes its row; the next morning's
    build (as_of = next day, last close = D's) reads it. Returns [(day index, stop)] for the
    stop the MORNING of each day (the premarket build)."""
    row, out = None, []
    for i in range(i0, len(days)):
        D = days[i]
        carry = {} if (row is None or not use_carry) else {T: row}
        eff = build(frame, D, carry)
        out.append((i, eff["stop"], eff))
        rows = hc.rows_to_write(
            {T: eff}, D, journal_sigs={T: MISMATCH_SIG}, avg_costs={},
            prior_rows=carry, session_closes={T: float(frame["Close"].loc[pd.Timestamp(D)])},
            journal_readable=True, carry_readable=True)
        if rows and D not in skip_writes:
            row = rows[0]
    return out


def test_slow_bleed_first_breach_is_the_morning_after_the_first_close_at_or_below_the_stop():
    n, i0 = 80, 60
    days, closes, frame = falling(n)
    sims = _simulate(frame, days, i0)
    s0 = closes[i0 - 1] - 6.0                 # the first prior-close stop (ATR 2, 3 x ATR)
    first_at_or_below = next(i for i in range(i0, n) if closes[i] <= s0 + 1e-9)
    breach_mornings = [i for i, stop, _ in sims
                       if is_stop_breached(float(closes[i - 1]), stop)]
    assert breach_mornings and breach_mornings[0] == first_at_or_below + 1   # never a day earlier
    stops = [stop for _, stop, _ in sims]
    assert all(b >= a - 1e-9 for a, b in zip(stops, stops[1:])) and stops[-1] == pytest.approx(s0)
    # the carry is what held it: the first day's row, then applied every day after
    assert sims[0][2]["carry"]["state"] == hc.NONE
    assert all(e["carry"]["state"] in (hc.APPLIED, hc.NOT_BINDING) for _, _, e in sims[1:])
    # a name already past its stop keeps being written (a valid row advances through a breach)
    assert sims[-1][2]["carry"]["state"] == hc.APPLIED


def test_without_the_carry_the_same_bleed_never_breaches_pinning_the_bug():
    n, i0 = 80, 60
    days, closes, frame = falling(n)
    sims = _simulate(frame, days, i0, use_carry=False)     # carry={} every day, writes ignored
    assert all(not is_stop_breached(float(closes[i - 1]), stop) for i, stop, _ in sims)
    assert sims[-1][1] < sims[0][1]                        # the computed stop chased the price down


def test_a_missed_eod_loses_nothing_through_the_real_builder():
    n, i0 = 120, 60
    days = trading_days(date(2026, 9, 30), n)
    steps = [-1.0] * (i0 + 4) + [1.0] * 25 + [-1.0] * (n - 1 - (i0 + 29))
    frame = frame_from(days, steps_closes(steps))
    base = _simulate(frame, days, i0)
    for skip_i in (i0 + 10, i0 + 20, i0 + 33):
        miss = _simulate(frame, days, i0, skip_writes={days[skip_i]})
        for (i1, a, _), (i2, b, _) in zip(base, miss):
            assert i1 == i2 and b == pytest.approx(a), (i1, skip_i)
    assert base[-1][1] > base[0][1]                        # the rise did lift the stop


# ── 10. time-context matrix ──────────────────────────────────────────────────
@pytest.mark.parametrize("visit, row_day", [
    (date(2026, 11, 28), date(2026, 11, 27)),    # Saturday reads Friday's row
    (date(2026, 11, 26), date(2026, 11, 25)),    # Thanksgiving reads Wednesday's
    (date(2027, 3, 14), date(2027, 3, 12)),      # the DST Sunday reads Friday's
])
def test_non_trading_visit_reads_the_previous_sessions_row_and_the_writer_writes_nothing(
        visit, row_day):
    days = trading_days(row_day, 90)
    frame = frame_from(days, steps_closes([-1.0] * 89))
    assert days[-1] == row_day
    raw = build(frame, visit, {})
    row = mrow(frame, days + [visit], len(days), round(raw["stop"] + 4.0, 2),
               as_of=row_day.isoformat(), ref_session=days[-2].isoformat(),
               bind_session=days[-2].isoformat())
    eff = build(frame, visit, {T: row})
    assert eff["carry"]["state"] == hc.APPLIED and eff["stop"] == row["stop"]
    # the breach read is the LAST close against the carried stop (Monday's premarket rule)
    assert is_stop_breached(float(frame["Close"].iloc[-1]), eff["stop"]) is False
    assert hc.rows_to_write({T: eff}, visit, journal_sigs={T: MISMATCH_SIG}, avg_costs={},
                            prior_rows={T: row}, session_closes={T: float(frame["Close"].iloc[-1])},
                            journal_readable=True, carry_readable=True) == []


def test_the_dst_change_day_is_ordinary_date_math():
    # Fri 2027-03-12 EOD writes row 03-12; Mon 03-15 reads it (the DST weekend in between)
    days = trading_days(date(2027, 3, 12), 90)
    frame = frame_from(days, steps_closes([-1.0] * 89))
    mon = date(2027, 3, 15)
    raw = build(frame, mon, {})
    row = mrow(frame, days + [mon], len(days), round(raw["stop"] + 2.0, 2),
               as_of=days[-1].isoformat(), ref_session=days[-2].isoformat(),
               bind_session=days[-2].isoformat())
    eff = build(frame, mon, {T: row})
    assert eff["carry"]["state"] == hc.APPLIED and eff["stop"] == row["stop"]


# ── 11. label and note plumbing ──────────────────────────────────────────────
def test_stop_noun_reads_a_carried_stop_not_a_profit_lock_tier():
    assert sbt.stop_noun("Carried Stop") == "carried stop"
    assert "profit-lock" not in sbt.stop_noun(hw.LABEL_CARRIED)
    assert sbt.stop_noun("Protect 25% gain") == "profit-lock stop (Protect 25% gain)"


def test_full_note_appends_the_carry_note_after_the_floor_note():
    h = {"note": "N.", "floor": {"note": "F."}, "carry": {"note": "C."}}
    assert hw.full_note(h) == "N. F. C."
    assert hw.full_note({"note": "N.", "carry": {"note": "C."}}) == "N. C."
    assert hw.full_note({"note": "N.", "carry": {"note": ""}}) == "N."
    assert hw.full_note({"note": "N.", "floor": {"note": "F."}}) == "N. F."
    assert hw.full_note({"note": "", "carry": {"note": "C."}}) == "C."
    assert hw.full_note(None) == "" and hw.full_note({"note": "N.", "carry": None}) == "N."


# ── 12. the headless context ─────────────────────────────────────────────────
_TODAY = date(2026, 10, 30)


def _bundle_frame(last_day, close, *, tz=None):
    days = [date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)]
    days = [d for d in days if d <= last_day]
    closes = [10.0] * (len(days) - 1) + [close]
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes}, index=idx)
    if tz == "UTC":     # a bar stamped at 00:00 ET, expressed in UTC
        df.index = df.index.tz_localize("America/New_York").tz_convert("UTC")
    return df


def _ctx_carry(carry, trades, held_stops, bundles):
    captured = {}

    def _bhs(*a, **k):
        captured.update(k)
        return held_stops
    with patch.object(hae.db, "has_db", return_value=True), \
         patch.object(hae.db, "load_holdings_or_none",
                      return_value=pd.DataFrame({"Ticker": list(bundles)})), \
         patch.object(hae.db, "load_trades_or_none", return_value=trades), \
         patch.object(hae.db, "load_manual_stops_or_none", return_value={}), \
         patch.object(hae.db, "load_held_stop_floors_or_none", return_value={}), \
         patch.object(hae.db, "load_held_stop_carry_or_none",
                      **({"side_effect": carry} if isinstance(carry, Exception)
                         else {"return_value": carry})) as ld, \
         patch.object(hae, "fetch_risk_free_rate", return_value=0.045), \
         patch.object(hae, "fetch_spy", return_value=pd.DataFrame({"Close": [1.0, 2.0]})), \
         patch.object(hae, "_vix_level", return_value=16.0), \
         patch.object(hae, "load_bundle", side_effect=lambda t, *a, **k: dict(bundles[t])), \
         patch.object(hae, "_build_open_lots", return_value=[]), \
         patch.object(hae.exit_advisor, "material_add_window_days", return_value=None), \
         patch.object(hae._held_stop_wiring, "build_held_stops", side_effect=_bhs), \
         patch.object(hae, "build_portfolio_df",
                      return_value=pd.DataFrame({"Ticker": ["AAA"], "Weight (%)": [10.0]})), \
         patch.object(hae, "compute_portfolio_risk_metrics", return_value={"beta": 1.1}), \
         patch.object(hae, "run_scenario", return_value={"port_impact_pct": -5.0}), \
         patch.object(hae, "assess_fragility", return_value={"severity": "elevated"}):
        ctx = hae._build_context(_TODAY)
    return ctx, captured, ld


_J = pd.DataFrame([
    {"id": 3, "ticker": "AAA", "action": "BUY", "shares": 1, "price": 1.0},
    {"id": 4, "ticker": "BBB", "action": "BUY", "shares": 1, "price": 1.0},
    {"id": 6, "ticker": "BBB", "action": "SPLIT", "shares": 1, "price": 1.0},
    {"id": 9, "ticker": "AAA", "action": "SELL", "shares": 1, "price": 1.0}])
def _hsd(basis):
    return {"ticker": "X", "stop": 90.0, "basis": basis, "label": "Trailing Stop",
            "binding": "entry", "note": "", "fell_back": False, "buy_day": False,
            "degraded": basis in ("partial", "prior_session", "unavailable")}


_HSX = {"AAA": _hsd("partial"), "BBB": _hsd("full"), "CCC": _hsd("full"),
        "DDD": _hsd("full"), "EEE": _hsd("full")}


def _bundles():
    return {
        "AAA": {"financials": {}, "df": _bundle_frame(_TODAY, 12.5)},                  # a bar dated today
        "BBB": {"financials": {}, "df": _bundle_frame(date(2026, 10, 29), 11.0)},      # ends yesterday
        "CCC": {"financials": {}, "df": _bundle_frame(_TODAY, float("nan"))},          # NaN Close today
        "DDD": {"financials": {}, "df": _bundle_frame(_TODAY, 0.0)},                   # non-positive
        "EEE": {"financials": {}, "df": _bundle_frame(_TODAY, 9.25, tz="UTC")},        # tz-aware, ET date
    }


def test_build_context_reads_the_carry_once_passes_it_and_publishes_the_five_keys():
    rows = {"AAA": {"stop": 1.0}}
    ctx, kw, ld = _ctx_carry(rows, _J, _HSX, _bundles())
    ld.assert_called_once_with(["AAA", "BBB", "CCC", "DDD", "EEE"])   # a real list, never None
    assert kw["carry"] is rows and kw["floors"] == {}
    assert ctx["held_stop_carry"] is rows and ctx["held_stop_carry_readable"] is True
    assert ctx["held_stop_journal_readable"] is True
    assert ctx["held_stop_journal_sigs"] == {"AAA": (1, 3), "BBB": (2, 6), "CCC": (0, 0),
                                             "DDD": (0, 0), "EEE": (0, 0)}
    # only a bar whose ET date IS today, finite and > 0; never the last bar blindly
    assert ctx["held_stop_session_closes"] == {"AAA": 12.5, "EEE": 9.25}
    ctx, kw, _ = _ctx_carry({}, _J, _HSX, _bundles())                 # read, none saved: readable
    assert kw["carry"] == {} and ctx["held_stop_carry_readable"] is True
    ctx, kw, _ = _ctx_carry(None, _J, _HSX, _bundles())               # couldn't read: never {}
    assert kw["carry"] is None and ctx["held_stop_carry_readable"] is False
    assert ctx["held_stop_carry"] is None


def test_an_unreadable_journal_is_published_and_no_signature_is_invented():
    ctx, _, _ = _ctx_carry({}, None, _HSX, _bundles())
    assert ctx["held_stop_journal_readable"] is False
    assert all(v is None for v in ctx["held_stop_journal_sigs"].values())


def test_a_carry_read_that_raises_is_unreadable_not_empty():
    ctx, kw, _ = _ctx_carry(RuntimeError("db down"), _J, _HSX, _bundles())
    assert kw["carry"] is None and ctx["held_stop_carry_readable"] is False
    assert ctx["held_stop_carry"] is None


# ── AST parity (headless, cron, app) ─────────────────────────────────────────
def _calls(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id == name)
        or (isinstance(n.func, ast.Attribute) and n.func.attr == name))]


def _func(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _kw(call, name):
    return next((k.value for k in call.keywords if k.arg == name), None)


_FIVE = ("held_stop_carry", "held_stop_carry_readable", "held_stop_journal_readable",
         "held_stop_journal_sigs", "held_stop_session_closes")


def test_headless_passes_carry_and_publishes_the_five_keys_in_all_three_payloads():
    head = parse_file_cached(_HEADLESS_PATH)
    ctx_fn = _func(head, "_build_context")
    calls = _calls(ctx_fn, "build_held_stops")
    assert len(calls) == 1 and ast.unparse(_kw(calls[0], "carry")) == "held_stop_carry"
    load = _calls(ctx_fn, "load_held_stop_carry_or_none")
    assert len(load) == 1 and ast.unparse(load[0].args[0]) == "held_tickers"
    for name in ("_build_context", "compute_eod", "compute_protective_alerts"):
        src = ast.unparse(_func(head, name))
        for key in _FIVE:
            assert f"'{key}'" in src, (name, key)
    assert "_held_stop._et_dates" in ast.unparse(ctx_fn)        # the bar is picked by its ET date


def test_the_carry_read_never_collapses_the_offline_sentinel():
    src = ast.unparse(_func(parse_file_cached(_HEADLESS_PATH), "_build_context"))
    assert "held_stop_carry_readable = held_stop_carry is not None" in src
    cron_src = ast.unparse(_func(parse_file_cached(_CRON_PATH), "_eod_held_stop_carry"))
    assert " or {}" not in cron_src and " or []" not in cron_src


def test_eod_carry_writer_reads_held_stops_only_and_run_eod_calls_it_after_the_floors():
    fn = _func(parse_file_cached(_CRON_PATH), "_eod_held_stop_carry")
    fn.body = [n for n in fn.body if not (isinstance(n, ast.Expr)
                                          and isinstance(n.value, ast.Constant))]   # drop the docstring
    body = ast.unparse(fn)
    assert "payload.get('held_stops')" in body
    assert "port_df" not in body and "'Stop'" not in body and '"Stop"' not in body
    run = ast.unparse(_func(parse_file_cached(_CRON_PATH), "_run_eod"))
    assert "failures.extend(_eod_held_stop_carry(payload, now_et))" in run
    assert run.index("_eod_held_stop_floors(payload, now_et)") < run.index("_eod_held_stop_carry(payload, now_et)")


def test_app_passes_carry_reads_it_once_before_the_fan_out_and_raises_on_none():
    app = parse_file_cached(_APP_PATH)
    calls = _calls(app, "build_held_stops")
    assert len(calls) == 1 and ast.unparse(_kw(calls[0], "carry")) == "_carry"
    held = ast.unparse(_func(app, "_held_stops_for"))
    assert held.count("_held_stop_carry_for(") == 1
    assert held.index("_held_stop_carry_for(") < held.index("ThreadPoolExecutor")
    # the same ticker set as the floors
    assert "_held_stop_floors_for(_held_names)" in held and "_held_stop_carry_for(_held_names)" in held
    fn = _func(app, "_cached_held_stop_carry")
    dec = ast.unparse(fn.decorator_list[0])
    assert "cache_data" in dec and "86400" in dec
    assert [a.arg for a in fn.args.args] == ["tickers", "et_date"]        # no underscore: the key
    body = ast.unparse(fn)
    assert "load_held_stop_carry_or_none(" in body and "is None" in body and "raise " in body
    reader = ast.unparse(_func(app, "_held_stop_carry_for"))
    assert "_today_et().isoformat()" in reader and "except Exception" in reader and "return None" in reader
    assert "st.session_state" not in body + reader      # no new coordination key to register


# ── 13. cron premarket ───────────────────────────────────────────────────────
_DAY = datetime(2026, 12, 1, 8, 30)


def _pm_payload(held_stops, carry_readable):
    return {"alerts": [{"kind": "stop_breach", "ticker": "AAPL"}], "built_at": "x", "errors": [],
            "held_stops_failed": None, "held_stop_notes": [], "held_stops": held_stops,
            "held_stop_floors_readable": True, "held_stop_carry_readable": carry_readable}


def test_premarket_red_only_with_unreadable_carry_and_a_partial_or_prior_session_name(monkeypatch):
    part = {"AAA": {"basis": "partial"}, "BBB": {"basis": "full"}}
    rc, sent, saved_, _, _ = _run_pm(monkeypatch, _pm_payload(part, False), _DAY)
    assert rc == 1 and len(sent) == 1 and len(saved_) == 1          # alerts still sent
    detail = cr._LAST_LANE_FAILURE_DETAIL
    assert "held_stop_carry" in detail and "AAA" in detail and "BBB" not in detail
    assert "stops may be lower than the carried stops" in detail
    rc, *_ = _run_pm(monkeypatch, _pm_payload({"AAA": {"basis": "prior_session"}}, False), _DAY)
    assert rc == 1 and "held_stop_carry" in cr._LAST_LANE_FAILURE_DETAIL
    for held, readable in (({"AAA": {"basis": "full"}}, False),                  # nobody exposed
                           ({"AAA": {"basis": "buy_day"}, "BBB": {"basis": "unavailable"}}, False),
                           (part, True)):                                         # readable
        rc, *_ = _run_pm(monkeypatch, _pm_payload(held, readable), _DAY)
        assert rc == 0, (held, readable)


def test_premarket_logs_one_line_when_unreadable_but_nothing_is_exposed(monkeypatch, capsys):
    _run_pm(monkeypatch, _pm_payload({"AAA": {"basis": "full"}}, False), _DAY)
    assert capsys.readouterr().out.count("held_stop_carry: saved carried stops could not be read") == 1
    _run_pm(monkeypatch, _pm_payload({"AAA": {"basis": "full"}}, True), _DAY)
    assert "held_stop_carry" not in capsys.readouterr().out


# ── 14. cron EOD ─────────────────────────────────────────────────────────────
@pytest.fixture
def logs(monkeypatch):
    out = []
    monkeypatch.setattr(cr, "_log", lambda m, *a, **k: out.append(str(m)))
    return out


@pytest.fixture
def dbc(monkeypatch):
    """Records the carry writes / deletes; the sweep's saved set defaults to {T}."""
    rec = {"save": [], "delete": [], "saved": {T}, "save_result": (True, "ok"),
           "delete_result": (True, "deleted")}

    def _save(rows):
        rec["save"].append(rows)
        return rec["save_result"]

    def _delete(tickers):
        rec["delete"].append(list(tickers))
        return rec["delete_result"]
    monkeypatch.setattr(cr.db, "save_held_stop_carry", _save)
    monkeypatch.setattr(cr.db, "delete_held_stop_carry", _delete)
    monkeypatch.setattr(cr.db, "load_held_stop_carry_tickers_or_none", lambda: rec["saved"])
    return rec


def _scene(carry=None, *, i=60, readable=True, jreadable=True, hour=16, minute=30):
    days, closes, frame = falling()
    D = days[i]
    eff = build(frame, D, {} if carry is None else carry)
    payload = {"held_stops": {T: eff}, "held_stop_carry": {} if carry is None else carry,
               "held_stop_carry_readable": readable, "held_stop_journal_readable": jreadable,
               "held_stop_journal_sigs": {T: MISMATCH_SIG},
               "held_stop_session_closes": {T: float(closes[i])}, "held_tickers": [T]}
    now = _ET.localize(datetime.combine(D, dt.time(hour, minute)))
    return payload, now, days, frame


def test_eod_writes_the_effective_stops_and_logs_wrote_only_on_true(logs, dbc):
    payload, now, days, _ = _scene()
    assert cr._eod_held_stop_carry(payload, now) == []
    assert len(dbc["save"]) == 1 and [r["ticker"] for r in dbc["save"][0]] == [T]
    row = dbc["save"][0][0]
    assert row["as_of"] == days[60].isoformat() and row["journal_buy_count"] == 1
    assert row["source_basis"] == "prior_session" and row["stop"] == round(payload["held_stops"][T]["stop"], 2)
    assert any(m.startswith("held_stop_carry: wrote 1 row(s)") for m in logs)
    assert dbc["delete"] == []                      # saved {T} - held [T]: nothing swept


def test_eod_save_failure_is_a_lane_failure_and_never_logs_wrote(logs, dbc):
    payload, now, *_ = _scene()
    detail = "held_stop_carry table is missing -- apply docs/sql/held-stop-carry.sql (x)"
    dbc["save_result"] = (False, detail)
    assert cr._eod_held_stop_carry(payload, now) == ["held_stop_carry: " + detail]
    assert not any("wrote" in m for m in logs) and any("NOT written" in m for m in logs)


def test_eod_a_save_that_raises_never_raises_out(logs, monkeypatch):
    payload, now, *_ = _scene()

    def _boom(rows):
        raise RuntimeError("db down")
    monkeypatch.setattr(cr.db, "save_held_stop_carry", _boom)
    out = cr._eod_held_stop_carry(payload, now)
    assert len(out) == 1 and out[0].startswith("held_stop_carry:") and "db down" in out[0]


def test_eod_unreadable_carry_or_journal_writes_nothing_and_fails(logs, dbc):
    payload, now, *_ = _scene(readable=False)
    out = cr._eod_held_stop_carry(payload, now)
    assert out == ["held_stop_carry: carried stops could not be read; not written "
                   "(if the table is new: apply docs/sql/held-stop-carry.sql)"]
    assert dbc["save"] == [] and dbc["delete"] == []
    assert any("apply docs/sql/held-stop-carry.sql" in m for m in logs)
    assert cr._eod_held_stop_carry({k: v for k, v in payload.items()
                                    if k != "held_stop_carry_readable"}, now) == out
    payload, now, *_ = _scene(jreadable=False)
    assert cr._eod_held_stop_carry(payload, now) == [
        "held_stop_carry: the trade journal could not be read; not written"]
    assert dbc["save"] == [] and dbc["delete"] == []


def test_eod_non_trading_day_and_missing_payload_write_and_sweep_nothing(logs, dbc):
    payload, _, days, _ = _scene()
    saturday = _ET.localize(datetime(2026, 11, 7, 20, 30))
    assert cr._eod_held_stop_carry(payload, saturday) == []
    weekday = _ET.localize(datetime.combine(days[60], dt.time(16, 30)))
    assert cr._eod_held_stop_carry({"reason": "no_holdings"}, weekday) == []
    assert cr._eod_held_stop_carry({"held_stops": None}, weekday) == []
    assert dbc["save"] == [] and dbc["delete"] == []


def test_eod_uses_the_et_date_not_the_utc_date(logs, dbc):
    days, closes, frame = falling(end=date(2026, 11, 3))        # last bar Tue 11-03, as_of Wed 11-04
    wed = date(2026, 11, 4)
    eff = build(frame, wed, {})
    now = _ET.localize(datetime(2026, 11, 4, 20, 30))           # 01:30 UTC on 11-05
    assert now.astimezone(pytz.utc).date() == date(2026, 11, 5)
    payload = {"held_stops": {T: eff}, "held_stop_carry": {}, "held_stop_carry_readable": True,
               "held_stop_journal_readable": True, "held_stop_journal_sigs": {T: MISMATCH_SIG},
               "held_stop_session_closes": {T: float(closes[-1]) - 1.0}, "held_tickers": [T]}
    assert cr._eod_held_stop_carry(payload, now) == []
    assert dbc["save"][0][0]["as_of"] == "2026-11-04"


def test_eod_forced_before_the_close_seeds_nothing_but_a_valid_row_still_advances(logs, dbc):
    # no prior row: the would-be seed is not written (a partial bar must not be trusted)
    payload, now, days, frame = _scene(hour=12, minute=0)
    assert cr._eod_held_stop_carry(payload, now) == []
    assert dbc["save"] == [] and any("forced before the close" in m for m in logs)
    # the same forced run after the close seeds it
    payload, now, *_ = _scene(hour=16, minute=30)
    cr._eod_held_stop_carry(payload, now)
    assert len(dbc["save"]) == 1
    dbc["save"].clear()
    # a valid row (applied) advances even with the closes withheld
    raw = build(frame, days[60], {})
    row = mrow(frame, days, 60, round(raw["stop"] + 5.0, 2))
    payload, now, *_ = _scene({T: row}, hour=12, minute=0)
    assert payload["held_stops"][T]["carry"]["state"] == hc.APPLIED
    assert cr._eod_held_stop_carry(payload, now) == []
    assert len(dbc["save"]) == 1 and dbc["save"][0][0]["stop"] == row["stop"]


def test_eod_turns_red_for_a_carry_check_that_raised_and_writes_nothing_for_it(logs, dbc, monkeypatch):
    days, closes, frame = falling()

    def _boom(*a, **k):
        raise ValueError("sig bug")
    with monkeypatch.context() as mp:
        mp.setattr(hw.hsc, "journal_buy_sig", _boom)
        eff = build(frame, days[60], {})
    assert eff["carry"]["state"] == hc.FAILED
    payload = {"held_stops": {T: eff}, "held_stop_carry": {}, "held_stop_carry_readable": True,
               "held_stop_journal_readable": True, "held_stop_journal_sigs": {T: MISMATCH_SIG},
               "held_stop_session_closes": {T: float(closes[60])}, "held_tickers": [T]}
    now = _ET.localize(datetime.combine(days[60], dt.time(16, 30)))
    assert cr._eod_held_stop_carry(payload, now) == [f"held_stop_carry: carry check failed for {T}"]
    assert dbc["save"] == [] and any(f"{T} carry check failed" in m for m in logs)


def test_eod_logs_a_carried_stop_that_bound_with_the_amount_and_still_writes(logs, dbc):
    days, closes, frame = falling()
    raw = build(frame, days[60], {})
    row = mrow(frame, days, 60, round(raw["stop"] + 5.0, 2))
    payload, now, *_ = _scene({T: row})
    assert cr._eod_held_stop_carry(payload, now) == []
    line = next(m for m in logs if "carried stop bound" in m)
    assert line.startswith(f"held_stop_carry: {T} carried stop bound (prior_session): carried ")
    assert f"carried {row['stop']:.2f} vs computed {raw['stop']:.2f} (+5.00)" in line
    assert len(dbc["save"]) == 1
    logs.clear()
    plain_payload, plain_now, *_ = _scene()
    cr._eod_held_stop_carry(plain_payload, plain_now)
    assert not any("carried stop bound" in m for m in logs)


def test_eod_sweep_deletes_saved_minus_held_and_only_with_both_sets_known(logs, dbc):
    payload, now, *_ = _scene()
    dbc["saved"] = {T, "ZZZ", "OLD"}
    assert cr._eod_held_stop_carry(payload, now) == []
    assert dbc["delete"] == [["OLD", "ZZZ"]] and any("swept 2 sold position(s)" in m for m in logs)
    # even when nothing was written (the sweep is independent of the save)
    dbc["delete"].clear()
    dbc["save"].clear()
    payload, now, *_ = _scene(hour=12, minute=0)
    assert cr._eod_held_stop_carry(payload, now) == [] and dbc["save"] == []     # nothing seeded
    assert dbc["delete"] == [["OLD", "ZZZ"]]
    # saved set unknown: no delete call
    dbc["delete"].clear()
    dbc["saved"] = None
    assert cr._eod_held_stop_carry(payload, now) == [] and dbc["delete"] == []
    assert any("sweep skipped" in m for m in logs)
    # held set unknown (not a list): no delete call
    dbc["saved"] = {T, "ZZZ"}
    for bad in (None, "AAA", {T}):
        p2 = dict(payload, held_tickers=bad)
        assert cr._eod_held_stop_carry(p2, now) == [] and dbc["delete"] == []
    # an EMPTY held list is a real answer: everything saved goes
    assert cr._eod_held_stop_carry(dict(payload, held_tickers=[]), now) == []
    assert dbc["delete"] == [sorted({T, "ZZZ"})]
    # a failed delete is a lane failure
    dbc["delete_result"] = (False, "held_stop_carry delete failed: x")
    assert cr._eod_held_stop_carry(dict(payload, held_tickers=[]), now) == [
        "held_stop_carry: held_stop_carry delete failed: x"]
