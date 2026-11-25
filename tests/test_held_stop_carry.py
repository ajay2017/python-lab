"""Pure-layer tests for stock_analyzer.held_stop_carry (SlidingStop C1; synthetic
frames, no network / DB / clock). Spec: docs/plans/sliding-stop.md section 8. The
wiring, db and AST-parity tests belong to C2 / C3."""
from __future__ import annotations

import ast
import copy
import pathlib
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import constants as C
from stock_analyzer import held_stop as hs
from stock_analyzer import held_stop_carry as hc
from stock_analyzer import held_stop_floor as hf
from stock_analyzer import held_stop_wiring as hw
from stock_analyzer.act_today_precedence import is_stop_breached
from stock_analyzer.data import is_trading_day

T = "AAA"
AVG = 50.0
JSIG = (3, 17)
MULT = max(C.ATR_STOP_MULT, C.HELD_TRAIL_ATR_MULT)          # 3.0
EP_MISMATCH = {"status": "mismatch", "reason": "journal nets 5 shares but holdings show 7",
               "start": None, "first_buy_price": None, "split_ratio": 1.0, "last_buy": None}
SRC = pathlib.Path(hc.__file__)


# ── builders ─────────────────────────────────────────────────────────────────
def trading_days(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def frame_from(days, closes, half_range=1.0) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    return pd.DataFrame({"Open": c, "High": c + half_range, "Low": c - half_range,
                         "Close": c}, index=idx)


def steps_closes(steps, start=200.0):
    """closes from a list of +-1 daily steps: every true range is 2.0 (ATR = 2.0
    exactly), so the prior-close stop is Close - 6 and moves 1:1 with price."""
    return start + np.concatenate([[0.0], np.cumsum(steps)])


def pol():
    return {**hw._policy(), "trail_start": None}


def compute(frame, as_of, ep=EP_MISMATCH, avg=AVG):
    return hs.compute_held_stop(frame, ep, avg, as_of, **pol())


def wrapped(frame, as_of, ep=EP_MISMATCH, avg=AVG):
    return hw._wrap(T, compute(frame, as_of, ep, avg), "", avg, ep=ep)


def reader(w, row, frame, as_of, *, jsig=JSIG, readable=True, carry_readable=True,
           avg=AVG, sig=None):
    return hc.apply_carry(w, row, as_of=as_of, bars=frame, avg_cost=avg, journal_sig=jsig,
                          journal_readable=readable, carry_readable=carry_readable,
                          policy_sig_now=hf.policy_sig() if sig is None else sig)


_UNSET = object()


def writer(held, as_of, *, prior=None, jsig=JSIG, avg=AVG, jr=True, cr=True, close=_UNSET):
    """`close` = the as_of session's own Close (the seed guard's input). Left unset it
    stands in with the result's last_close, which is only fine for tests that are not
    about the seed guard."""
    if close is _UNSET:
        close = (held.get(T) or {}).get("last_close") if isinstance(held, dict) else None
    return hc.rows_to_write(
        held, as_of, journal_sigs={T: jsig}, avg_costs={T: avg},
        prior_rows={} if prior is None else {T: prior}, session_closes={T: close},
        journal_readable=jr, carry_readable=cr)


def make_row(frame, days, i, *, stop, **over):
    """A saved row as the EOD writer of days[i-1] would have left it: dated days[i-1],
    reference bar days[i-2]."""
    ref = days[i - 2]
    c = float(frame["Close"].loc[pd.Timestamp(ref)])
    row = {"ticker": T, "as_of": days[i - 1].isoformat(), "stop": stop,
           "stop_set_on": days[i - 1].isoformat(), "source_basis": "prior_session",
           "binding": "prior_session", "ratchet_label": None, "avg_cost": AVG,
           "journal_buy_count": JSIG[0], "journal_buy_max_id": JSIG[1],
           "ref_session": ref.isoformat(), "ref_close": c, "bind_session": ref.isoformat(),
           "bind_close": c, "policy_sig": hf.policy_sig(), "carry_sig": hc.carry_sig(),
           "source": "cron:eod"}
    row.update(over)
    return row


class Fall:
    """A falling name (-1 a day, ATR 2): prior_session stop = last close - 6."""
    def __init__(self, n=90, i=70):
        self.days = trading_days(date(2026, 9, 30), n)
        self.closes = steps_closes([-1.0] * (n - 1))
        self.frame = frame_from(self.days, self.closes)
        self.i = i
        self.d = self.days[i]
        self.w = wrapped(self.frame, self.d)
        assert self.w["basis"] == "prior_session" and self.w["binding"] == "prior_session"
        self.computed = self.w["stop"]

    def row(self, bump=5.0, **over):
        kw = {"stop": round(self.computed + bump, 2)}
        kw.update(over)
        return make_row(self.frame, self.days, self.i, **kw)


@pytest.fixture(scope="module")
def fall():
    return Fall()


# ── journal fingerprint ──────────────────────────────────────────────────────
def jrows(*specs):
    return pd.DataFrame([{"id": i, "ticker": t, "action": a, "shares": 1, "price": 1.0,
                          "traded_at": "2026-01-02T15:00:00+00:00"} for i, t, a in specs])


def test_journal_buy_sig_counts_buy_and_split_only():
    j = jrows((1, "AAA", "BUY"), (2, "AAA", "SELL"), (5, "AAA", "SPLIT"),
              (9, "BBB", "BUY"), (12, "AAA", "SELL"))
    assert hc.journal_buy_sig(j, "aaa ") == (2, 5)
    # a SELL-only change leaves the fingerprint alone
    j2 = pd.concat([j, jrows((20, "AAA", "SELL"))], ignore_index=True)
    assert hc.journal_buy_sig(j2, "AAA") == (2, 5)
    # a late-logged BUY, a deleted BUY, a new SPLIT all move it
    assert hc.journal_buy_sig(pd.concat([j, jrows((21, "AAA", "BUY"))]), "AAA") == (3, 21)
    assert hc.journal_buy_sig(j[j["id"] != 1], "AAA") == (1, 5)


def test_journal_buy_sig_unknown_and_empty_states():
    assert hc.journal_buy_sig(None, "AAA") is None
    assert hc.journal_buy_sig(jrows(), "AAA") == (0, 0)
    assert hc.journal_buy_sig(jrows((1, "BBB", "BUY")), "AAA") == (0, 0)
    assert hc.journal_buy_sig(pd.DataFrame({"x": [1]}), "AAA") is None
    assert hc.journal_buy_sig(pd.DataFrame([{"ticker": "AAA", "action": "BUY", "id": None}]),
                              "AAA") is None
    assert hc.journal_buy_sig([{"ticker": "AAA", "action": "BUY", "id": 4}], "AAA") == (1, 4)
    assert hc.journal_buy_sig([("AAA", "BUY", 4)], "AAA") is None


def test_carry_sig_is_a_stable_string():
    assert hc.carry_sig() == f"carry-{hc.CARRY_SIG_VERSION}"
    assert hc._mult() == MULT


# ── episode_from_journal: additive share fields ──────────────────────────────
def test_episode_carries_both_share_numbers():
    j = jrows((1, "X", "BUY"), (2, "X", "BUY"))
    j["shares"] = [3.0, 2.0]
    mism = hs.episode_from_journal("X", j, 7, shares_tol=1e-4)
    assert mism["status"] == "mismatch"
    assert (mism["journal_shares"], mism["holdings_shares"]) == (5.0, 7.0)
    ok = hs.episode_from_journal("X", j, 5, shares_tol=1e-4)
    assert ok["status"] == "ok" and (ok["journal_shares"], ok["holdings_shares"]) == (5.0, 5.0)
    unread = hs.episode_from_journal("X", None, 5, shares_tol=1e-4)
    assert unread["status"] == "unreadable"
    assert (unread["journal_shares"], unread["holdings_shares"]) == (None, 5.0)
    nohist = hs.episode_from_journal("X", jrows((1, "Y", "BUY")), 5, shares_tol=1e-4)
    assert nohist["status"] == "no_history"
    assert (nohist["journal_shares"], nohist["holdings_shares"]) == (0.0, 5.0)
    sold = hs.episode_from_journal("X", pd.concat([j, jrows((3, "X", "SELL"))]), 0.0,
                                   shares_tol=1e-4)
    assert sold["status"] == "no_history" or sold["journal_shares"] is not None
    # a holdings count that isn't a number reads as unknown, never raises early
    assert hs.episode_from_journal("X", None, "n/a", shares_tol=1e-4)["holdings_shares"] is None


def test_episode_existing_keys_unchanged():
    j = jrows((1, "X", "BUY"))
    e = hs.episode_from_journal("X", j, 1, shares_tol=1e-4)
    for k in ("status", "start", "first_buy_price", "split_ratio", "reason", "last_buy"):
        assert k in e
    assert e["status"] == "ok" and e["split_ratio"] == 1.0


# ── slow-bleed invariant (the bug and its fix) ───────────────────────────────
def simulate(frame, days, i0, *, skip_writes=(), jsig=JSIG, ep=EP_MISMATCH):
    """EOD of each day writes the row (as_of = that day, bars before it); the next
    morning reads it. Returns [(day, effective result, rows written)]."""
    row, out_all = None, []
    for i in range(i0, len(days)):
        D = days[i]
        w = wrapped(frame, D, ep)
        eff = reader(w, row, frame, D, jsig=jsig)
        rows = writer({T: eff}, D, prior=row, jsig=jsig,
                      close=float(frame["Close"].loc[pd.Timestamp(D)]))
        if rows and D not in skip_writes:
            row = rows[0]
        out_all.append((D, eff, rows))
    return out_all


def test_slow_bleed_breaches_the_morning_after_and_never_a_day_earlier():
    n, i0 = 80, 60
    days = trading_days(date(2026, 9, 30), n)
    closes = steps_closes([-1.0] * (n - 1))
    frame = frame_from(days, closes)
    sims = simulate(frame, days, i0)
    # oracle, independent of the module: the stop only ratchets up from the first
    # prior-close stop (C(i0-1) - 6), so on a falling name it stays put
    s0 = closes[i0 - 1] - 6.0
    first_close_at_or_below = next(i for i in range(i0, n) if closes[i] <= s0 + 1e-9)
    breach_mornings = []
    for k in range(len(sims) - 1):
        D_next, eff_next, _ = sims[k + 1]
        last_close = closes[i0 + k]                       # the close the next morning sees
        if is_stop_breached(float(last_close), eff_next["stop"]):
            breach_mornings.append(i0 + k + 1)
    assert breach_mornings, "the carry never breached"
    assert breach_mornings[0] == first_close_at_or_below + 1       # exactly the morning after
    assert all(b >= first_close_at_or_below + 1 for b in breach_mornings)
    # the stop never fell on any day, and equals the oracle
    stops = [e["stop"] for _, e, _ in sims]
    assert all(b >= a - 1e-9 for a, b in zip(stops, stops[1:]))
    assert stops[-1] == pytest.approx(s0)


def test_without_the_carry_a_slow_bleed_never_breaches_pinning_the_bug():
    n, i0 = 80, 60
    days = trading_days(date(2026, 9, 30), n)
    closes = steps_closes([-1.0] * (n - 1))
    frame = frame_from(days, closes)
    for i in range(i0, n):
        w = wrapped(frame, days[i])
        assert not is_stop_breached(float(closes[i - 1]), w["stop"])   # premarket: last close
    # the computed stop chases price: it falls with it
    assert wrapped(frame, days[n - 1])["stop"] < wrapped(frame, days[i0])["stop"]


def test_rows_keep_being_written_through_a_breach_for_a_valid_row():
    n, i0 = 80, 60
    days = trading_days(date(2026, 9, 30), n)
    closes = steps_closes([-1.0] * (n - 1))
    sims = simulate(frame_from(days, closes), days, i0)
    s0 = closes[i0 - 1] - 6.0
    deep = [(D, r) for D, e, r in sims if closes[days.index(D) - 1] < s0 - 2]
    assert deep, "scenario never went below the stop"
    for D, rows in deep:
        assert len(rows) == 1 and rows[0]["stop"] == pytest.approx(s0)
        assert rows[0]["stop"] >= rows[0]["ref_close"]                 # past the stop, still written


def test_eod_missed_on_d_minus_1_the_window_term_recovers_the_stop():
    n, i0 = 120, 60
    days = trading_days(date(2026, 9, 30), n)
    # fall, then rise (the stop climbs), then fall again: stop(D-1) can sit above the row
    steps = [-1.0] * (i0 + 4) + [1.0] * 25 + [-1.0] * (n - 1 - (i0 + 29))
    closes = steps_closes(steps)
    frame = frame_from(days, closes)
    base = simulate(frame, days, i0)
    # skip the EOD write of one day in the middle of the rise and one on the turn
    for skip_i in (i0 + 10, i0 + 20, i0 + 33, i0 + 34):
        with_miss = simulate(frame, days, i0, skip_writes={days[skip_i]})
        for (d1, a, _), (d2, b, _) in zip(base, with_miss):
            assert d1 == d2
            assert b["stop"] == pytest.approx(a["stop"]), (d1, skip_i)
    assert base[-1][1]["stop"] > base[0][1]["stop"]        # the rise did lift the stop


def test_stop_never_below_computed_over_a_zigzag_for_every_day():
    rng = np.random.default_rng(5)
    n, i0 = 120, 60
    days = trading_days(date(2026, 9, 30), n)
    steps = list(rng.choice([-1.0, 1.0], size=n - 1))
    frame = frame_from(days, steps_closes(steps))
    for D, eff, _ in simulate(frame, days, i0):
        assert eff["stop"] >= eff["carry"]["computed"] - 1e-9


# ── never lowers, every path ─────────────────────────────────────────────────
def test_never_lowers_on_every_lapse_and_n_a_path(fall):
    cases = [
        (fall.row(), {}),                                              # applied
        (fall.row(bump=-5.0), {}),                                     # not binding
        (fall.row(avg_cost=AVG + 0.01), {}),
        (fall.row(journal_buy_count=4), {}),
        (fall.row(policy_sig="old"), {}),
        (fall.row(carry_sig="carry-v0"), {}),
        (fall.row(ref_close=1000.0, bind_close=1000.0), {}),
        (fall.row(as_of=fall.d.isoformat()), {}),
        (fall.row(stop=None), {}),
        (None, {}),
        (fall.row(), {"carry_readable": False}),
    ]
    for row, kw in cases:
        out = reader(fall.w, row, fall.frame, fall.d, **kw)
        assert out["stop"] >= fall.computed, (row, kw)
        assert out["carry"]["computed"] == fall.computed


def test_carry_code_exception_keeps_the_computed_stop(fall, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("injected")
    monkeypatch.setattr(hs, "_atr_series", boom)
    out = reader(fall.w, fall.row(), fall.frame, fall.d)
    assert out["stop"] == fall.computed and out["basis"] == "prior_session"
    assert out["carry"]["state"] == hc.FAILED
    assert out["carry"]["reason"].startswith(hc.CARRY_CHECK_FAILED)
    assert hc.carry_check_failures({T: out, "BBB": {"carry": {"state": hc.NA}}}) == [T]


def test_exception_after_the_result_was_changed_returns_it_truly_unchanged(fall, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("injected after the stop was raised")
    monkeypatch.setattr(hw, "label_for", boom)
    before = copy.deepcopy(fall.w)
    out = reader(fall.w, fall.row(), fall.frame, fall.d)       # would APPLY, then label_for raises
    assert out["carry"]["state"] == hc.FAILED
    assert out["stop"] == fall.computed and out["binding"] == before["binding"]
    assert out["carry"]["computed"] == fall.computed and fall.w == before
    assert "lower than on earlier days" in out["carry"]["note"]
    assert out["stop_set_on"] == before["stop_set_on"]


def test_window_term_skips_sessions_before_the_min_bar_count():
    n = C.HELD_STOP_MIN_BARS + 6
    days = trading_days(date(2026, 9, 30), n)
    frame = frame_from(days, steps_closes([-1.0] * (n - 1)))
    bars = hs.completed_bars(frame, days[-1] + timedelta(days=1))
    win = hc._window_term(bars, days[0] - timedelta(days=1), MULT)
    assert win[1] == days[C.HELD_STOP_MIN_BARS]              # earliest eligible session
    assert hc._window_term(bars.iloc[:C.HELD_STOP_MIN_BARS], days[0], MULT) is None


def test_garbage_inputs_never_raise_and_never_lower(fall):
    for bad_row in ("junk", 5, [], {"stop": "x"}):
        out = reader(fall.w, bad_row, fall.frame, fall.d)
        assert out["stop"] == fall.computed
    out = hc.apply_carry(fall.w, fall.row(), as_of=fall.d, bars=None, avg_cost=AVG,
                         journal_sig=JSIG, journal_readable=True,
                         policy_sig_now=hf.policy_sig())
    assert out["stop"] == fall.computed
    out = hc.apply_carry("not a dict", None, as_of=fall.d, bars=None, avg_cost=1,
                         journal_sig=None, journal_readable=False, policy_sig_now="x")
    assert isinstance(out, dict) and out["carry"]["state"] == hc.NA


def test_inputs_are_not_mutated(fall):
    w, row, frame = copy.deepcopy(fall.w), fall.row(), fall.frame.copy()
    row0 = copy.deepcopy(row)
    out = reader(w, row, frame, fall.d)
    assert out is not w and "carry" not in w
    assert w == fall.w and row == row0 and frame.equals(fall.frame)
    held = {T: out}
    snap = copy.deepcopy(held)
    writer(held, fall.d, prior=row)
    assert held == snap


# ── the reader: applied / not binding / window ───────────────────────────────
def test_row_stop_applies_and_carries_its_provenance(fall):
    row = fall.row(binding="ratchet", ratchet_label="Breakeven guard")
    out = reader(fall.w, row, fall.frame, fall.d)
    c = out["carry"]
    assert c["state"] == hc.APPLIED and out["stop"] == pytest.approx(fall.computed + 5.0)
    assert out["binding"] == "ratchet" and out["ratchet_label"] == "Breakeven guard"
    assert out["bind_session"] == date.fromisoformat(row["bind_session"])
    assert out["stop_set_on"] == date.fromisoformat(row["stop_set_on"])
    assert out["label"] == "Prior-close ATR"           # basis stays prior_session
    assert out["basis"] == "prior_session"
    assert c["computed"] == fall.computed and c["value"] == out["stop"]
    assert "\\$" in c["note"] and str(round(fall.computed, 2).__format__(",.2f")) in c["note"]


def test_not_binding_when_computed_already_higher(fall):
    out = reader(fall.w, fall.row(bump=-3.0), fall.frame, fall.d)
    assert out["carry"]["state"] == hc.NOT_BINDING and out["stop"] == fall.computed
    assert out["binding"] == "prior_session" and out["stop_set_on"] == fall.w["stop_set_on"]


def test_window_term_binds_when_the_row_is_old_and_low():
    n = 90
    days = trading_days(date(2026, 9, 30), n)
    closes = steps_closes([1.0] * 75 + [-1.0] * (n - 76))
    frame = frame_from(days, closes)
    i = 80
    w = wrapped(frame, days[i])
    # a row dated 6 sessions ago with a low stop: the highest prior-close stop since
    # it was saved (the peak session) is above both
    j = i - 6
    row = make_row(frame, days, j + 1, stop=round(w["stop"] - 30, 2))
    out = reader(w, row, frame, days[i])
    peak = int(np.argmax(closes))
    assert days[j] < days[peak] < days[i]
    assert out["carry"]["state"] == hc.APPLIED
    assert out["stop"] == pytest.approx(round(closes[peak] - 6.0, 2))
    assert out["binding"] == "prior_session" and out["bind_session"] == days[peak]
    assert out["bind_close"] == pytest.approx(closes[peak])
    assert out["stop_set_on"] == days[peak + 1]


def test_partial_basis_applies_the_carry_and_keeps_it_across_the_switch(fall):
    # the 59 -> 60 bar switch: a prior_session row written yesterday, today's result is a
    # partial one (an ok episode): the carry is applied and the stop does not drop
    w = dict(fall.w, basis="partial", binding="trailing")
    row = fall.row()
    out = reader(w, row, fall.frame, fall.d)
    assert out["carry"]["state"] == hc.APPLIED and out["stop"] >= row["stop"]
    held = {T: out}
    rows = writer(held, fall.d, prior=row)
    assert rows and rows[0]["source_basis"] == "partial" and rows[0]["stop"] >= row["stop"]


@pytest.mark.parametrize("res", [
    {"basis": "full", "binding": "trailing"},
    {"basis": "buy_day", "binding": "entry"},
    {"basis": "unavailable", "binding": None, "stop": None},
    {"basis": "prior_session", "binding": "entry"},        # rejected-fill buy-day fallback
    {"basis": "prior_session", "binding": "range"},
    {"basis": "prior_session", "binding": "prior_session", "buy_day": True},
    {"basis": "partial", "binding": "trailing", "stop": None},
])
def test_never_applies_off_its_bases(fall, res):
    w = dict(fall.w, **res)
    out = reader(w, fall.row(bump=50.0), fall.frame, fall.d)
    assert out["carry"]["state"] == hc.NA and out["stop"] == w["stop"]
    assert out["binding"] == w["binding"] and out["basis"] == w["basis"]


def test_unreadable_and_absent_rows_are_distinct_states(fall):
    un = reader(fall.w, None, fall.frame, fall.d, carry_readable=False)
    none = reader(fall.w, None, fall.frame, fall.d)
    assert un["carry"]["state"] == hc.UNREADABLE and un["carry"]["note"]
    assert none["carry"]["state"] == hc.NONE and not none["carry"]["note"]
    assert un["stop"] == none["stop"] == fall.computed


# ── lapse causes ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("over, kw, code", [
    ({"avg_cost": AVG + 0.01}, {}, "trades"),
    ({}, {"avg": AVG + 0.01}, "trades"),
    ({"journal_buy_count": JSIG[0] + 1}, {}, "journal"),         # a new / late-logged BUY
    ({"journal_buy_count": JSIG[0] - 1}, {}, "journal"),         # a deleted BUY
    ({"journal_buy_max_id": JSIG[1] - 1}, {}, "journal"),
    ({}, {"jsig": (JSIG[0] + 1, JSIG[1] + 1)}, "journal"),       # a SPLIT row appeared
    ({"policy_sig": "v0;atr=9"}, {}, "rules"),
    ({"carry_sig": "carry-v0"}, {}, "rules"),
    ({}, {"sig": "different-policy"}, "rules"),
    ({"ref_close": 100.0, "bind_close": 100.0}, {}, "history"),  # ~50%+ mismatch
    ({"stop": None}, {}, "row"),                                 # incomplete rows
    ({"binding": "weird"}, {}, "row"),
    ({"ref_session": None}, {}, "row"),
    ({"journal_buy_count": None}, {}, "row"),
    ({"policy_sig": None}, {}, "row"),
])
def test_each_lapse_cause_returns_todays_stop(fall, over, kw, code):
    out = reader(fall.w, fall.row(**over), fall.frame, fall.d, **kw)
    assert out["carry"]["state"] == hc.LAPSED, out["carry"]
    assert out["carry"]["reason"].split(":")[0] == code
    assert out["stop"] == fall.computed and out["binding"] == "prior_session"
    assert out["carry"]["note"] and "wasn't used" in out["carry"]["note"]


def test_a_cent_of_average_cost_but_not_a_sell(fall):
    # avg_cost compared at the cent: sub-cent noise is the same average cost
    assert reader(fall.w, fall.row(), fall.frame, fall.d, avg=AVG + 0.004)["carry"]["state"] \
        == hc.APPLIED
    # a SELL-only journal change: the BUY / SPLIT fingerprint is unchanged -> still applies
    before = jrows((1, T, "BUY"), (2, T, "BUY"), (3, T, "SPLIT"))
    after = pd.concat([before, jrows((9, T, "SELL"))], ignore_index=True)
    assert hc.journal_buy_sig(before, T) == hc.journal_buy_sig(after, T)
    row = fall.row(journal_buy_count=3, journal_buy_max_id=3)
    out = reader(fall.w, row, fall.frame, fall.d, jsig=hc.journal_buy_sig(after, T))
    assert out["carry"]["state"] == hc.APPLIED


def test_same_day_row_applies_and_a_future_row_does_not(fall):
    same = reader(fall.w, fall.row(as_of=fall.d.isoformat()), fall.frame, fall.d)
    assert same["carry"]["state"] == hc.APPLIED and same["stop"] == fall.computed + 5.0
    future = reader(fall.w, fall.row(as_of=(fall.d + timedelta(days=3)).isoformat()),
                    fall.frame, fall.d)
    assert future["carry"]["state"] == hc.NONE
    assert future["carry"]["reason"].startswith(hc.R_FUTURE)
    assert future["stop"] == fall.computed
    # U2: with the journal unreadable a same-day row is accepted too
    un = reader(fall.w, fall.row(as_of=fall.d.isoformat()), fall.frame, fall.d,
                jsig=None, readable=False)
    assert un["carry"]["state"] == hc.APPLIED


def test_after_hours_on_d_after_the_eod_wrote_row_d_the_stop_holds():
    n, i0 = 80, 60
    days = trading_days(date(2026, 9, 30), n)
    closes = steps_closes([-1.0] * (n - 1))
    frame = frame_from(days, closes)
    s0 = closes[i0 - 1] - 6.0
    row = None
    checked = 0
    for k, D in enumerate(days[i0:]):
        w = wrapped(frame, D)
        daytime = reader(w, row, frame, D)                       # reads row D-1 all day
        rows = writer({T: daytime}, D, prior=row, close=float(closes[i0 + k]))
        if not rows:
            continue
        evening = reader(w, rows[0], frame, D)                   # a fresh visit after the EOD
        assert evening["carry"]["state"] in (hc.APPLIED, hc.NOT_BINDING)
        assert evening["stop"] == pytest.approx(daytime["stop"])
        # the live breach test (the day's own close vs the stop) can't change at night
        assert is_stop_breached(float(closes[i0 + k]), evening["stop"]) == \
            is_stop_breached(float(closes[i0 + k]), daytime["stop"])
        row = rows[0]
        checked += 1
    assert checked > 10 and row["stop"] == pytest.approx(s0)


def test_history_check_skips_a_session_outside_the_frame_but_needs_one(fall):
    # ref bar far outside the frame, bind bar inside: still verified on the bind bar
    far = (fall.days[0] - timedelta(days=30)).isoformat()
    row = fall.row(ref_session=far)
    assert reader(fall.w, row, fall.frame, fall.d)["carry"]["state"] == hc.APPLIED
    row = fall.row(ref_session=far, bind_session=far)
    out = reader(fall.w, row, fall.frame, fall.d)
    assert out["carry"]["state"] == hc.LAPSED and out["carry"]["reason"].startswith("history")


# ── U2: unreadable journal ───────────────────────────────────────────────────
def test_unreadable_journal_applies_only_a_previous_session_row(fall):
    prev = fall.days[fall.i - 1]
    old = fall.days[fall.i - 2]
    ok = reader(fall.w, fall.row(as_of=prev.isoformat()), fall.frame, fall.d,
                jsig=None, readable=False)
    assert ok["carry"]["state"] == hc.APPLIED
    stale = reader(fall.w, fall.row(as_of=old.isoformat()), fall.frame, fall.d,
                   jsig=None, readable=False)
    assert stale["carry"]["state"] == hc.LAPSED and stale["carry"]["reason"].startswith("unverified")
    assert stale["stop"] == fall.computed
    # readable flag True but no signature to compare = unreadable too
    odd = reader(fall.w, fall.row(as_of=old.isoformat()), fall.frame, fall.d,
                 jsig=None, readable=True)
    assert odd["carry"]["state"] == hc.LAPSED
    # with a readable journal the same older row is fine
    assert reader(fall.w, fall.row(as_of=old.isoformat()), fall.frame, fall.d)["carry"]["state"] \
        == hc.APPLIED
    # avg cost and the rules are still checked while the journal is unreadable
    bad = reader(fall.w, fall.row(as_of=prev.isoformat(), avg_cost=AVG + 1), fall.frame, fall.d,
                 jsig=None, readable=False)
    assert bad["carry"]["state"] == hc.LAPSED


# ── re-entry ─────────────────────────────────────────────────────────────────
def test_reentry_after_sweep_at_identical_avg_cost_gets_no_carry(fall):
    # the sold position's row is swept (saved - held), so the re-bought one starts clean
    assert hc.sweep_tickers({T, "BBB"}, {"BBB"}) == {T}
    out = reader(fall.w, None, fall.frame, fall.d, avg=AVG)
    assert out["carry"]["state"] == hc.NONE and out["stop"] == fall.computed


def test_same_session_round_trip_lapses_through_the_journal_sig(fall):
    # sold and re-bought inside one session at the same avg cost, no EOD between:
    # the row survives the sweep, the avg cost matches, only the new BUY id differs
    out = reader(fall.w, fall.row(), fall.frame, fall.d, jsig=(JSIG[0] + 1, JSIG[1] + 1))
    assert out["carry"]["state"] == hc.LAPSED and out["carry"]["reason"].startswith("journal")
    # with the journal unreadable, only a previous-session row applies (the residual)
    prev = fall.days[fall.i - 1].isoformat()
    unread = reader(fall.w, fall.row(as_of=prev), fall.frame, fall.d, jsig=None, readable=False)
    assert unread["carry"]["state"] == hc.APPLIED
    older = reader(fall.w, fall.row(as_of=fall.days[fall.i - 3].isoformat()), fall.frame, fall.d,
                   jsig=None, readable=False)
    assert older["carry"]["state"] == hc.LAPSED


# ── seed guard ───────────────────────────────────────────────────────────────
def held_result(**over):
    """A hand-built `_wrap`-shaped prior_session result for the writer."""
    d0 = date(2026, 10, 6)                       # a Tuesday: ref bar Mon 10-05
    r = {"ticker": T, "stop": 94.0, "basis": "prior_session", "binding": "prior_session",
         "last_session": date(2026, 10, 5), "last_close": 100.0, "bind_session": None,
         "bind_close": None, "avg_cost": AVG, "ratchet_label": None, "stop_set_on": None,
         "carry": {"state": hc.NONE}}
    r.update(over)
    return r, d0


def gap_down_case():
    """Mismatched name flat at 100 with ATR 2, then a 10-ATR gap down on D: the
    computed prior-close stop is C(D-1) - 6 = 94 and C(D) = 80 is already below it."""
    days = trading_days(date(2026, 9, 30), 80)
    closes = list(steps_closes([0.0] * 79, start=100.0))
    closes[-1] = closes[-2] - 20.0
    frame = frame_from(days, closes)
    D = days[-1]
    eff = reader(wrapped(frame, D), None, frame, D)
    assert eff["basis"] == "prior_session" and eff["carry"]["state"] == hc.NONE
    return eff, D, closes


def test_seed_guard_refuses_a_gap_down_seed_built_from_a_computed_stop():
    eff, D, closes = gap_down_case()
    assert eff["stop"] == pytest.approx(94.0) and eff["last_close"] == pytest.approx(100.0)
    assert is_stop_breached(closes[-1], eff["stop"])                    # already breached at C(D)
    assert writer({T: eff}, D, close=closes[-1]) == []                  # so never seeded
    # equality counts as breached (and the gap rounds as the Home column does)
    assert writer({T: eff}, D, close=94.0) == []
    assert len(writer({T: eff}, D, close=95.0)) == 1
    # an unknown close never seeds
    for bad in (None, 0.0, float("nan"), "x"):
        assert writer({T: eff}, D, close=bad) == []
    assert hc.rows_to_write({T: eff}, D, journal_sigs={T: JSIG}, avg_costs={T: AVG},
                            prior_rows={}, session_closes=None, journal_readable=True,
                            carry_readable=True) == []


def test_seed_guard_still_seeds_a_normal_day_from_a_computed_stop(fall):
    out = reader(fall.w, None, fall.frame, fall.d)
    assert len(writer({T: out}, fall.d, close=float(fall.closes[fall.i]))) == 1


def test_seed_guard_allows_advancing_a_valid_row_through_a_breach():
    r, d0 = held_result(stop=104.0, carry={"state": hc.APPLIED})
    rows = writer({T: r}, d0, close=90.0)                    # the close is below the stop
    assert len(rows) == 1 and rows[0]["stop"] == 104.0
    r, d0 = held_result(stop=104.0, carry={"state": hc.NOT_BINDING})
    assert len(writer({T: r}, d0, close=90.0)) == 1
    # a lapsed / absent row is not a valid prior row: the breached stop is not seeded
    for st in (hc.LAPSED, hc.NONE):
        r, d0 = held_result(stop=104.0, carry={"state": st})
        assert writer({T: r}, d0, close=90.0) == []
        assert len(writer({T: r}, d0, close=110.0)) == 1


def test_full_basis_is_exempt_from_the_seed_guard():
    d0 = date(2026, 10, 6)
    full = {"ticker": T, "stop": 105.0, "basis": "full", "binding": "trailing",
            "last_session": date(2026, 10, 5), "last_close": 100.0,
            "bind_session": date(2026, 10, 2), "bind_close": 108.0, "avg_cost": AVG,
            "ratchet_label": None, "stop_set_on": None}
    # no prior row and the close is already below the stop: a full stop is journal-dated,
    # so the row is still saved (it carries into the day before a mismatch starts)
    assert len(writer({T: full}, d0, close=95.0)) == 1
    assert len(writer({T: full}, d0, close=None)) == 1
    prior = {"ticker": T, "as_of": "2026-10-05", "stop": 104.0, "binding": "trailing",
             "ref_session": "2026-10-02", "ref_close": 108.0, "bind_session": "2026-10-02",
             "bind_close": 108.0, "avg_cost": AVG, "journal_buy_count": JSIG[0],
             "journal_buy_max_id": JSIG[1], "policy_sig": hf.policy_sig(),
             "carry_sig": hc.carry_sig()}
    assert len(writer({T: full}, d0, prior=prior, close=95.0)) == 1


def test_a_carried_full_row_on_a_prior_session_day_is_rewritten_with_its_binding(fall):
    # the core case: a saved full / partial row is applied on a prior_session day, and
    # that same EOD must advance it (basis prior_session, the saved binding kept)
    for binding, label in (("trailing", None), ("ratchet", "Profit Lock"), ("entry", None)):
        set_on = fall.days[fall.i - 9].isoformat()
        row = fall.row(binding=binding, ratchet_label=label, source_basis="full",
                       stop_set_on=set_on)
        out = reader(fall.w, row, fall.frame, fall.d)
        assert out["carry"]["state"] == hc.APPLIED
        assert out["basis"] == "prior_session" and out["binding"] == binding
        (new,) = writer({T: out}, fall.d, prior=row, close=float(fall.closes[fall.i]))
        assert new["as_of"] == fall.d.isoformat() and new["source_basis"] == "prior_session"
        assert new["binding"] == binding and new["ratchet_label"] == label
        assert new["stop"] == row["stop"] and new["stop_set_on"] == set_on
    # a raw prior_session result bound to entry / range (the rejected-fill buy-day
    # fallback) gets carry n/a from the reader and writes nothing
    for b in ("entry", "range"):
        out = reader(dict(fall.w, binding=b), fall.row(), fall.frame, fall.d)
        assert out["carry"]["state"] == hc.NA
        assert writer({T: out}, fall.d, close=float(fall.closes[fall.i])) == []


# ── the writer ───────────────────────────────────────────────────────────────
def test_writer_row_shape():
    r, d0 = held_result(stop=94.123, last_close=100.5)
    (row,) = writer({T: r}, d0)
    assert set(row) == {"ticker", "as_of", "stop", "stop_set_on", "source_basis", "binding",
                        "ratchet_label", "avg_cost", "journal_buy_count", "journal_buy_max_id",
                        "ref_session", "ref_close", "bind_session", "bind_close", "policy_sig",
                        "carry_sig", "source"}
    assert row["ticker"] == T and row["as_of"] == "2026-10-06" and row["stop"] == 94.12
    assert row["source_basis"] == "prior_session" and row["binding"] == "prior_session"
    assert row["ref_session"] == "2026-10-05" and row["ref_close"] == 100.5
    assert (row["bind_session"], row["bind_close"]) == ("2026-10-05", 100.5)   # its own bar
    assert (row["journal_buy_count"], row["journal_buy_max_id"]) == JSIG
    assert row["policy_sig"] == hf.policy_sig() and row["carry_sig"] == hc.carry_sig()
    assert row["source"] == "cron:eod" and row["stop_set_on"] == "2026-10-06"
    assert not any("manual" in k for k in row)             # the writer has no manual input


def test_writer_ratchet_row_keeps_its_label_and_a_carried_set_on():
    d0 = date(2026, 10, 6)
    r = {"ticker": T, "stop": 110.0, "basis": "partial", "binding": "ratchet",
         "ratchet_label": "Protect 10%", "last_session": date(2026, 10, 5), "last_close": 120.0,
         "bind_session": date(2026, 9, 28), "bind_close": 125.0, "avg_cost": AVG,
         "stop_set_on": date(2026, 9, 29), "carry": {"state": hc.APPLIED}}
    (row,) = writer({T: r}, d0)
    assert row["ratchet_label"] == "Protect 10%" and row["stop_set_on"] == "2026-09-29"
    assert row["source_basis"] == "partial" and row["bind_session"] == "2026-09-28"
    r2 = dict(r, binding="trailing")
    assert writer({T: r2}, d0)[0]["ratchet_label"] is None


@pytest.mark.parametrize("over", [
    {"basis": "buy_day"}, {"basis": "unavailable", "stop": None}, {"basis": "weird"},
    {"stop": 0.0}, {"stop": float("nan")}, {"stop": None},
    {"last_close": None}, {"last_session": date(2026, 10, 6)},    # ref bar not before as_of
    {"carry": {"state": hc.FAILED}}, {"carry": {"state": hc.UNREADABLE}},
    {"carry": {"state": hc.NA}}, {"carry": None}, {"carry": "x"},
])
def test_writer_skips_what_it_must_not_persist(over):
    r, d0 = held_result(**over)
    assert writer({T: r}, d0) == []


def test_writer_full_basis_needs_no_carry_dict():
    d0 = date(2026, 10, 6)
    full = {"ticker": T, "stop": 90.0, "basis": "full", "binding": "trailing",
            "last_session": date(2026, 10, 5), "last_close": 100.0,
            "bind_session": date(2026, 10, 2), "bind_close": 96.0, "avg_cost": AVG}
    (row,) = writer({T: full}, d0)
    assert row["source_basis"] == "full" and row["binding"] == "trailing"
    no_bind = dict(full, bind_session=None)
    assert writer({T: no_bind}, d0) == []


def test_writer_fails_closed_when_journal_or_carry_rows_are_unreadable():
    r, d0 = held_result()
    assert len(writer({T: r}, d0)) == 1
    for jr, cr in ((False, True), (None, True), (True, False), (True, None), (False, False)):
        assert writer({T: r}, d0, jr=jr, cr=cr) == []
    assert writer({T: r}, d0, jsig=None) == []                          # no fingerprint for it
    assert hc.rows_to_write({T: r}, d0, journal_sigs=None, avg_costs={T: AVG},
                            prior_rows={}, session_closes={T: 95.0}, journal_readable=True,
                            carry_readable=True) == []
    assert writer("junk", d0) == [] and writer(None, d0) == []


def test_writer_needs_a_positive_average_cost():
    r, d0 = held_result(avg_cost=0.0)
    assert writer({T: r}, d0, avg=0.0) == []
    assert writer({T: r}, d0, avg=None) == []
    r, d0 = held_result()
    assert len(writer({T: r}, d0, avg=AVG)) == 1


def test_writer_avg_cost_falls_back_to_the_result():
    r, d0 = held_result()
    rows = hc.rows_to_write({T: r}, d0, journal_sigs={T: JSIG}, avg_costs={}, prior_rows={},
                            session_closes={T: 95.0}, journal_readable=True, carry_readable=True)
    assert rows[0]["avg_cost"] == AVG


def test_writer_same_day_rerun_never_lowers_and_never_overwrites_a_newer_row():
    r, d0 = held_result(stop=94.0, carry={"state": hc.NONE})
    prior = {"ticker": T, "as_of": d0.isoformat(), "stop": 97.0, "binding": "prior_session",
             "ref_session": "2026-10-05", "ref_close": 100.0, "bind_session": "2026-10-05",
             "bind_close": 100.0, "avg_cost": AVG, "journal_buy_count": JSIG[0],
             "journal_buy_max_id": JSIG[1], "policy_sig": hf.policy_sig(),
             "carry_sig": hc.carry_sig()}
    assert writer({T: r}, d0, prior=prior) == []                  # re-run: row holds more
    higher, _ = held_result(stop=98.0, carry={"state": hc.NONE})
    assert len(writer({T: higher}, d0, prior=prior)) == 1          # a higher value is written
    changed = dict(prior, avg_cost=AVG + 5)                        # not the same holding now
    assert len(writer({T: r}, d0, prior=changed)) == 1
    newer = dict(prior, as_of="2026-10-07")
    assert writer({T: r}, d0, prior=newer) == []


# ── time context ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("as_of", [
    date(2026, 11, 28),          # Saturday
    date(2026, 11, 29),          # Sunday
    date(2026, 11, 26),          # Thanksgiving
    date(2027, 3, 14),           # the DST day (a Sunday)
    date(2026, 12, 25),          # Christmas
])
def test_writer_writes_nothing_on_a_non_trading_day(as_of):
    assert not is_trading_day(as_of)
    r, _ = held_result()
    assert writer({T: r}, as_of) == []


def test_writer_writes_on_the_trading_days_around_them():
    r, _ = held_result()
    for as_of in (date(2026, 11, 25), date(2026, 11, 27), date(2027, 3, 12), date(2027, 3, 15)):
        assert is_trading_day(as_of)
        rr = dict(r, last_session=as_of - timedelta(days=5))
        assert len(writer({T: rr}, as_of)) == 1


def frame_to(end: date, n=80):
    days = trading_days(end, n)
    return days, frame_from(days, steps_closes([-1.0] * (n - 1)))


@pytest.mark.parametrize("visit, row_day", [
    (date(2026, 11, 28), date(2026, 11, 27)),    # Saturday uses Friday's row
    (date(2026, 11, 29), date(2026, 11, 27)),    # Sunday too
    (date(2026, 11, 26), date(2026, 11, 25)),    # Thanksgiving uses Wednesday's
    (date(2026, 11, 27), date(2026, 11, 25)),    # the day after: the holiday has no row
    (date(2027, 3, 14), date(2027, 3, 12)),      # DST Sunday uses Friday's
])
def test_non_trading_visit_reads_the_previous_sessions_row(visit, row_day):
    days, frame = frame_to(row_day)
    assert days[-1] == row_day
    w = wrapped(frame, visit)
    ref = days[-2]
    row = make_row(frame, days + [visit], len(days), stop=round(w["stop"] + 4, 2),
                   as_of=row_day.isoformat(), ref_session=ref.isoformat(),
                   bind_session=ref.isoformat())
    # journal unreadable (U2): only the previous trading session's row applies
    out = reader(w, row, frame, visit, jsig=None, readable=False)
    assert out["carry"]["state"] == hc.APPLIED, out["carry"]
    older = dict(row, as_of=days[-2].isoformat())
    assert reader(w, older, frame, visit, jsig=None, readable=False)["carry"]["state"] == hc.LAPSED
    # and the window term spans the sessions after the row's reference bar, no more
    assert out["stop"] >= w["stop"]


def test_saturday_breach_test_is_fridays_close_against_the_carried_stop():
    days, frame = frame_to(date(2026, 11, 27))
    sat = date(2026, 11, 28)
    closes = frame["Close"].to_numpy()
    w = wrapped(frame, sat)
    s_fri_row = float(closes[-1]) + 1.0                 # a carried stop just above Friday's close
    row = make_row(frame, days + [sat], len(days), stop=s_fri_row, as_of=days[-1].isoformat(),
                   ref_session=days[-2].isoformat(), bind_session=days[-2].isoformat())
    out = reader(w, row, frame, sat)
    assert out["stop"] == pytest.approx(s_fri_row)
    assert is_stop_breached(float(closes[-1]), out["stop"])
    assert not is_stop_breached(float(closes[-1]), w["stop"])


# ── transitions ──────────────────────────────────────────────────────────────
def test_too_few_bars_then_the_seed():
    n = C.HELD_STOP_MIN_BARS
    days = trading_days(date(2026, 9, 30), n + 5)
    frame = frame_from(days, steps_closes([-1.0] * (n + 4)))
    short = wrapped(frame, days[n - 1])                 # n-1 completed bars
    assert short["basis"] == "unavailable"
    out = reader(short, None, frame, days[n - 1])
    assert out["carry"]["state"] == hc.NA and out["stop"] is None
    assert writer({T: out}, days[n - 1]) == []
    seeded = wrapped(frame, days[n])                    # n completed bars
    assert seeded["basis"] == "prior_session"
    out2 = reader(seeded, None, frame, days[n])
    rows = writer({T: out2}, days[n], close=float(frame["Close"].loc[pd.Timestamp(days[n])]))
    assert len(rows) == 1 and rows[0]["source_basis"] == "prior_session"


def test_full_basis_is_written_but_never_applied(fall):
    full = dict(fall.w, basis="full", binding="trailing", stop=fall.computed - 20.0,
                bind_session=fall.days[fall.i - 3], bind_close=100.0)
    out = reader(full, fall.row(bump=50.0), fall.frame, fall.d)
    assert out["carry"]["state"] == hc.NA and out["stop"] == full["stop"]
    (row,) = writer({T: out}, fall.d, prior=fall.row())
    assert row["source_basis"] == "full" and row["stop"] == round(full["stop"], 2)


def test_mismatch_to_fixed_a_lower_full_stop_governs():
    # U3: once the journal agrees again the result is full and nothing lifts it
    days = trading_days(date(2026, 9, 30), 90)
    frame = frame_from(days, steps_closes([-1.0] * 89))
    i = 70
    s = 62                                    # enough bars before entry to be a full basis
    ep_ok = {"status": "ok", "start": days[s], "first_buy_price": float(frame["Close"].iloc[s]),
             "split_ratio": 1.0, "reason": "", "last_buy": days[s]}
    i = 75
    res = compute(frame, days[i], ep_ok)
    assert res["basis"] == "full"
    w = hw._wrap(T, res, "", AVG, ep=ep_ok)
    row = make_row(frame, days, i, stop=w["stop"] + 25.0)
    out = reader(w, row, frame, days[i])
    assert out["stop"] == w["stop"] and out["carry"]["state"] == hc.NA


# ── sweep ────────────────────────────────────────────────────────────────────
def test_sweep_tickers_table():
    assert hc.sweep_tickers(["aaa ", "BBB", "CCC"], ["bbb", "ddd"]) == {"AAA", "CCC"}
    assert hc.sweep_tickers(["AAA"], ["AAA"]) == set()
    assert hc.sweep_tickers([], ["AAA"]) == set()
    assert hc.sweep_tickers(["AAA", "BBB"], []) == {"AAA", "BBB"}     # everything sold: real answer
    assert hc.sweep_tickers(["AAA"], None) is None                    # held set unknown: no delete
    assert hc.sweep_tickers(None, ["AAA"]) is None                    # saved set unknown
    assert hc.sweep_tickers(["AAA", ""], {"", " "}) == {"AAA"}


# ── lane failure / notice tier ───────────────────────────────────────────────
@pytest.mark.parametrize("held, readable, expect_red", [
    ({"A": {"basis": "prior_session"}}, False, True),
    ({"A": {"basis": "partial"}}, False, True),
    ({"A": {"basis": "full"}, "B": {"basis": "partial"}}, False, True),
    ({"A": {"basis": "full"}}, False, False),
    ({"A": {"basis": "unavailable"}, "B": {"basis": "buy_day"}}, False, False),
    ({"A": {"basis": "prior_session"}}, True, False),
    ({}, False, False),
    ({"A": None, "B": "x"}, False, False),
    (None, False, False),
])
def test_lane_failure_table(held, readable, expect_red):
    msg = hc.lane_failure(held, readable)
    assert (msg is not None) == expect_red
    if msg:
        assert "carried stops could not be read" in msg


def test_lane_failure_names_only_the_exposed_holdings():
    msg = hc.lane_failure({"B": {"basis": "partial"}, "A": {"basis": "prior_session"},
                           "C": {"basis": "full"}}, False)
    assert "2 holding(s)" in msg and "A, B" in msg and "C" not in msg.split("(")[-1]


@pytest.mark.parametrize("res, tier", [
    ({"basis": "prior_session", "episode_status": "mismatch", "carry": {"state": hc.NONE}}, "error"),
    ({"basis": "prior_session", "episode_status": "no_history", "carry": {"state": hc.LAPSED}}, "error"),
    ({"basis": "prior_session", "episode_status": "unreadable", "carry": {"state": hc.UNREADABLE}}, "error"),
    ({"basis": "prior_session", "episode_status": "mismatch", "carry": {"state": hc.FAILED}}, "error"),
    ({"basis": "prior_session", "episode_status": "mismatch"}, "error"),
    ({"basis": "prior_session", "episode_status": "mismatch", "carry": {"state": hc.APPLIED}}, "warning"),
    ({"basis": "prior_session", "episode_status": "no_history", "carry": {"state": hc.NOT_BINDING}}, "warning"),
    ({"basis": "prior_session", "episode_status": "ok", "carry": {"state": hc.NONE}}, None),
    ({"basis": "prior_session", "episode_status": "ok", "carry": {"state": hc.APPLIED}}, None),
    ({"basis": "prior_session", "carry": {"state": hc.NONE}}, None),
    ({"basis": "partial", "episode_status": "ok", "carry": {"state": hc.APPLIED}}, None),
    ({"basis": "partial", "episode_status": "mismatch"}, None),
    ({"basis": "full", "episode_status": "ok"}, None),
    ({"basis": "unavailable", "episode_status": "mismatch"}, None),
    ({"basis": "buy_day", "episode_status": "mismatch"}, None),
    (None, None), ("x", None), ({}, None),
])
def test_notice_tier_table(res, tier):
    assert hc.notice_tier(res) == tier


# ── purity: no clock, no UI, no DB ───────────────────────────────────────────
def test_module_is_pure_by_construction():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
            imported |= {f"{node.module}.{a.name}" for a in node.names}
    assert not imported & {"streamlit", "time", "requests", "supabase", "yfinance"}
    assert not any(n.endswith((".db", ".market_time")) or n in ("stock_analyzer.db",
                   "stock_analyzer.market_time") for n in imported)
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"now", "today", "utcnow", "utcfromtimestamp", "fromtimestamp"}
    assert not any("now_et" in ast.unparse(n) or "today_et" in ast.unparse(n)
                   for n in ast.walk(tree) if isinstance(n, ast.Call))


def test_no_clock_is_read_at_runtime(fall, monkeypatch):
    from stock_analyzer import market_time

    def boom(*a, **k):
        raise AssertionError("the carry read the clock")
    monkeypatch.setattr(market_time, "now_et", boom)
    monkeypatch.setattr(market_time, "today_et", boom)
    out = reader(fall.w, fall.row(), fall.frame, fall.d)
    assert out["carry"]["state"] == hc.APPLIED
    r, d0 = held_result()
    assert len(writer({T: r}, d0)) == 1
    assert hc.sweep_tickers({T}, set()) == {T}
    assert hc.notice_tier({"basis": "prior_session", "episode_status": "mismatch"}) == "error"


def test_policy_values_come_from_constants_not_literals():
    src = SRC.read_text(encoding="utf-8")
    assert "HELD_STOP_FLOOR_CLOSE_TOL_PCT" in src and "ATR_STOP_MULT" in src
    assert "HELD_TRAIL_ATR_MULT" in src
    tree = ast.parse(src)
    # no bare float literal other than 0 / 1 / 100-style plumbing in the decision code
    floats = {n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, float)}
    assert floats <= {0.0}, floats
