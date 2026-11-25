"""Gate module: the CARRIED held stop (SlidingStop C1; pure, no I/O, no clock).

WHY
---
A held position whose journal the app cannot date (the journal's share count
disagrees with the broker's, no history, an unreadable journal, or a young listing
with no computable component) gets `held_stop`'s "prior_session" stop: the last
completed close minus max(ATR_STOP_MULT, HELD_TRAIL_ATR_MULT) x ATR. That stop is
rebuilt from the latest close every day, so it chases a falling price down and is
never breached by a slow bleed; the full / partial ratchet it replaced is lost the
day the journal stops agreeing. This module saves the EFFECTIVE auto stop of every
held name at each EOD and, on a partial / prior_session day, lets the saved value
act as a lower bound:

    effective = max(computed, saved stop,
                    max over completed sessions s with ref_session < s < as_of of
                    Close_s - max(mults) x ATR_s)

so the stop only moves up while the carry is trusted. The window term covers the
sessions after the row was saved, so a missed EOD loses nothing. Never lowers: the
returned stop is >= the computed stop on every path (a lapse, an unreadable row and
a code failure all keep the computed stop).
Spec: docs/plans/sliding-stop.md (sections 3, 5, 6, 8).

THE CARRY LAPSES (today's behaviour) when any of these changed since the row was saved:
  trades    holdings avg_cost (to the cent), or the journal's (count, max id) of
            BUY + SPLIT rows for the ticker (a sell changes neither)
  rules     `held_stop_floor.policy_sig()` or `carry_sig()`
  history   a saved close no longer agrees with today's price history
            (`held_stop_floor.verify_closes`, `HELD_STOP_FLOOR_CLOSE_TOL_PCT`)
  row       incomplete row
An unreadable journal at read time (U2) applies the carry only when the row is
from the previous trading session.

WHAT IS HERE
------------
  carry_sig()                                    the module's rule version
  journal_buy_sig(trades, ticker)                (count, max id) of BUY + SPLIT rows
  check_row(row, ...)                            the fingerprint check (no prices)
  apply_carry(result, carry_row, ...)            the reader: one ticker
  rows_to_write(held_stops, as_of, ...)          the EOD writer's rows (seed guard)
  sweep_tickers(saved, held)                     rows the EOD lane may delete
  lane_failure(held_stops, carry_readable)       the premarket red heartbeat
  carry_check_failures(held_stops)               names whose carry code raised
  notice_tier(result)                            Home notice severity (C4)

Offline is three-state, as everywhere in the coordination layer: `carry_readable=False`
= the saved rows could not be read; `None` row = read, none saved for this ticker.
Nothing here mutates an input, reads the clock, or touches the manual stop (the
carry feeds the AUTO stop only; `compose_with_manual` runs after it).

NOT WIRED as of C1: nothing imports this module yet. `held_stop_wiring.label_for`
is imported lazily inside `apply_carry` (the wiring module will import this one).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd

from stock_analyzer import constants as _C
from stock_analyzer import held_stop as hs
from stock_analyzer import held_stop_floor as hsf
from stock_analyzer.act_today_precedence import is_stop_breached
from stock_analyzer.data import is_trading_day, previous_trading_day

CARRY_SIG_VERSION = "v1"
SOURCE_EOD = "cron:eod"

# Bindings a saved row may carry: the full / partial components plus the plain
# prior-close stop. A prior_session result bound to "entry" / "range" is the
# rejected-fill BUY-DAY fallback (`held_stop_wiring._buy_day`), never carried.
_BINDINGS = ("entry", "trailing", "ratchet", "prior_session")
_APPLY_BASES = ("partial", "prior_session")
_WRITE_BASES = ("full", "partial", "prior_session")

# Carry states (the `state` of the result's `carry` dict).
APPLIED = "applied"            # the carried stop is above today's figure and used
NOT_BINDING = "not_binding"    # row valid, today's figure already >= it
LAPSED = "lapsed"              # a row exists but is not trusted today
NONE = "none"                  # no row (or one dated today / later: not a prior session)
UNREADABLE = "unreadable"      # the saved rows couldn't be read
NA = "n/a"                     # the carry does not apply (basis / no stop)
FAILED = "failed"              # the carry code itself raised (a defect, never a data state)

# The `carry.reason` prefix when the carry code raised; the EOD lane turns it red.
CARRY_CHECK_FAILED = "carry check failed"

# Reason codes (the text before the colon in a `check_row` / `verify_closes` reason).
R_TRADES = "trades"
R_JOURNAL = "journal"
R_RULES = "rules"
R_HISTORY = hsf.R_HISTORY
R_FUTURE = hsf.R_FUTURE
R_ROW = hsf.R_ROW
R_STALE = "unverified"        # journal unreadable and the row is not from the previous session

# states whose result may be written (the carry was READ and went through apply_carry)
_WRITABLE_STATES = frozenset({APPLIED, NOT_BINDING, NONE, LAPSED})
# states that prove the prior row was valid today (fingerprint AND prices)
_VALID_STATES = frozenset({APPLIED, NOT_BINDING})
# episode statuses a user can fix in the journal (notice_tier); "ok" is not one
_FIXABLE_STATUSES = frozenset({"mismatch", "no_history", "unreadable"})


# ── small helpers ────────────────────────────────────────────────────────────
_f = hsf._f
_d = hsf._d
_iso = hsf._iso
_code = hsf._code


def _i(v) -> int | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or x != int(x):
        return None
    return int(x)


def _money(x: float) -> str:
    return f"\\${x:,.2f}"


def _mult() -> float:
    """The window term's multiple: the wider of the two stop multiples, exactly the
    prior_session rule's (`held_stop.compute_held_stop`, status != ok branch)."""
    return max(float(_C.ATR_STOP_MULT), float(_C.HELD_TRAIL_ATR_MULT))


def _next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    while not is_trading_day(n):
        n += timedelta(days=1)
    return n


def carry_sig() -> str:
    """The carry's own rule version (module plumbing, like
    `held_stop_floor.POLICY_SIG_VERSION`; bump it when the carry rule changes and
    every saved row lapses at once). Saved with each row beside `policy_sig`."""
    return f"carry-{CARRY_SIG_VERSION}"


# ── journal fingerprint ──────────────────────────────────────────────────────
def journal_buy_sig(trades_rows, ticker: str) -> tuple[int, int] | None:
    """(count, max id) over the journal's BUY and SPLIT rows for `ticker`; SELL rows
    are ignored (a sale never changes the position's start or its average cost, so
    it must not lapse a carry). `trades_rows` is the strict journal read: a
    DataFrame (columns ticker, action, id) or an iterable of dict rows. None when
    the journal is unreadable (`trades_rows is None`), the columns are missing, a
    row is not a dict, or a counted row has no integer id (the fingerprint can't be
    trusted, so the caller treats it as unreadable). No rows for the ticker =
    (0, 0): known, and distinct from None."""
    if trades_rows is None:
        return None
    try:
        if isinstance(trades_rows, pd.DataFrame):
            if len(trades_rows) == 0:
                return 0, 0
            if "ticker" not in trades_rows.columns or "action" not in trades_rows.columns:
                return None
            rows = trades_rows.to_dict("records")
        else:
            rows = list(trades_rows)
    except Exception:
        return None
    want = str(ticker).strip().upper()
    count, max_id = 0, 0
    for r in rows:
        if not isinstance(r, dict):
            return None
        if str(r.get("ticker", "") or "").strip().upper() != want:
            continue
        action = str(r.get("action", "") or "").upper()
        if "BUY" not in action and "SPLIT" not in action:
            continue
        rid = _i(r.get("id"))
        if rid is None:
            return None
        count += 1
        max_id = max(max_id, rid)
    return count, max_id


def _row_journal_sig(row: dict) -> tuple[int, int] | None:
    c, m = _i(row.get("journal_buy_count")), _i(row.get("journal_buy_max_id"))
    return None if c is None or m is None else (c, m)


def _sig_pair(sig) -> tuple[int, int] | None:
    if not isinstance(sig, (tuple, list)) or len(sig) != 2:
        return None
    c, m = _i(sig[0]), _i(sig[1])
    return None if c is None or m is None else (c, m)


def _complete(row) -> bool:
    """A usable saved row: finite stop > 0, a date, a binding, the reference and
    binding bars, the average cost, the journal fingerprint and both signatures."""
    if not isinstance(row, dict):
        return False
    stop, ref_c, bind_c, avg = (_f(row.get("stop")), _f(row.get("ref_close")),
                                _f(row.get("bind_close")), _f(row.get("avg_cost")))
    return not (stop is None or stop <= 0 or ref_c is None or ref_c <= 0
                or bind_c is None or bind_c <= 0 or avg is None or avg <= 0
                or _d(row.get("as_of")) is None or _d(row.get("ref_session")) is None
                or _d(row.get("bind_session")) is None
                or row.get("binding") not in _BINDINGS
                or _row_journal_sig(row) is None
                or not row.get("policy_sig") or not row.get("carry_sig"))


def check_row(row, *, as_of: date, avg_cost, journal_sig, journal_readable: bool,
              policy_sig_now: str, allow_same_day: bool = False) -> tuple[bool, str]:
    """Does the saved `row` still describe today's holding and rules? The
    fingerprint half of the carry check (no prices: `apply_carry` adds
    `verify_closes`). (True, "") or (False, "<code>: <detail>") with code `row`
    (not a usable row), `future_row` (dated after `as_of`; with
    `allow_same_day=False` also a row dated exactly `as_of`), `rules` (policy or
    carry signature), `trades` (average cost at the cent), `journal` (BUY / SPLIT
    fingerprint) or `unverified` (U2: the journal is unreadable, so only a row from
    the previous trading session before `as_of`, or with `allow_same_day` one dated
    `as_of`, is trusted)."""
    if not _complete(row):
        return False, f"{R_ROW}: saved row is incomplete"
    row_as_of = _d(row.get("as_of"))
    if row_as_of > as_of or (row_as_of == as_of and not allow_same_day):
        return False, f"{R_FUTURE}: saved row is dated {row_as_of}, not before {as_of}"
    if str(row.get("policy_sig")) != str(policy_sig_now):
        return False, f"{R_RULES}: policy signature"
    if str(row.get("carry_sig")) != carry_sig():
        return False, f"{R_RULES}: carry signature"
    want_avg, have_avg = _f(avg_cost), _f(row.get("avg_cost"))
    if want_avg is None or round(want_avg, 2) != round(have_avg, 2):
        return False, f"{R_TRADES}: average cost"
    sig = _sig_pair(journal_sig)
    if journal_readable and sig is not None:
        if sig != _row_journal_sig(row):
            return False, f"{R_JOURNAL}: BUY / SPLIT rows changed"
    elif row_as_of != previous_trading_day(as_of) and not (allow_same_day and row_as_of == as_of):
        return False, (f"{R_STALE}: the trade journal can't be read and the saved stop "
                       f"is from {row_as_of}, not the previous session")
    return True, ""


# ── reader ───────────────────────────────────────────────────────────────────
def _why(code: str) -> str:
    return {
        R_TRADES: "your average cost for this position changed since then",
        R_JOURNAL: "your trades in this position changed since then",
        R_HISTORY: ("the price history no longer matches the prices it was saved from "
                    "(for example a stock split)"),
        R_RULES: "the stop rules changed since then",
        R_STALE: "the trade journal couldn't be read to confirm it",
    }.get(code, "the saved record couldn't be used")


def _carry_dict(state: str, *, value=None, set_on=None, reason="", note="",
                row_as_of=None, computed=None, binding=None) -> dict:
    return {"state": state, "value": value, "set_on": set_on, "reason": reason,
            "note": note, "row_as_of": row_as_of, "computed": computed,
            "binding": binding}


def _window_term(bars: pd.DataFrame, after: date, mult: float):
    """(value, session, close) of the highest Close_s - mult x ATR_s over the
    frame's completed sessions s > `after`; None when none is finite. Sessions
    with fewer than `HELD_STOP_MIN_BARS` bars before them are skipped: an un-warmed
    EWM ATR is too small and would inflate Close - mult x ATR on the first bars."""
    if len(bars) == 0:
        return None
    min_pos = int(_C.HELD_STOP_MIN_BARS)
    atr = hs._atr_series(bars["High"], bars["Low"], bars["Close"], hs._ATR_LENGTH)
    close = bars["Close"].astype(float)
    best = None
    ts_after = pd.Timestamp(after)
    for pos, ts in enumerate(bars.index):
        if ts <= ts_after or pos < min_pos:
            continue
        c, a = _f(close.loc[ts]), _f(atr.loc[ts])
        if c is None or a is None:
            continue
        v = round(c - mult * a, 2)
        if best is None or v > best[0]:
            best = (v, ts.date(), c)
    return best


def apply_carry(result: dict, carry_row, *, as_of: date, bars, avg_cost, journal_sig,
                journal_readable: bool, policy_sig_now: str,
                carry_readable: bool = True) -> dict:
    """The effective held-stop result for one ticker: a NEW dict (no input is
    mutated) that is `result` plus

      carry        {state, value, set_on, reason, note, row_as_of, computed, binding}
      stop_set_on  (only when the carry binds) the first as_of the value was reached
      label        (only when the carry binds) `held_stop_wiring.label_for(...)`

    `result` is `held_stop_wiring._wrap`'s dict (or `compute_held_stop`'s), AFTER
    `apply_floor`; `carry_row` is this ticker's saved row (`None` = read, none
    saved); `bars` is the price frame the result was built from (`completed_bars`
    is applied, so a raw frame is fine); `avg_cost` the holdings average cost;
    `journal_sig` = `journal_buy_sig(trades, ticker)` (None when unknown) and
    `journal_readable` False when the journal could not be read (U2);
    `policy_sig_now` = `held_stop_floor.policy_sig()`; `carry_readable` False when
    the saved rows could not be read.

    Applies ONLY on basis partial / prior_session with a computed stop; never on
    full, buy_day, unavailable or the rejected-fill buy-day result (a
    prior_session result whose binding is not "prior_session", or one flagged
    `buy_day`): state `n/a`, result unchanged. A prior_session result whose carried
    row had binding trailing / ratchet / entry keeps that binding afterwards (so
    the writer can advance it). A row dated `as_of` (the EOD wrote it for today)
    applies; one dated after `as_of` is state `none`. When it binds, the stop, binding,
    ratchet label, bind_*, `stop_set_on` and label are the saved row's (or the
    window session's); the basis is never touched.

    NEVER lowers: the returned stop is >= `result["stop"]` on every path; any
    exception returns the result unchanged with state `failed` and a reason that
    starts with `CARRY_CHECK_FAILED`."""
    base = dict(result) if isinstance(result, dict) else {}
    try:
        return _apply(dict(base), carry_row, as_of=as_of, bars=bars, avg_cost=avg_cost,
                      journal_sig=journal_sig, journal_readable=journal_readable,
                      policy_sig_now=policy_sig_now, carry_readable=carry_readable)
    except Exception as e:      # a carry bug must never remove or lower today's stop
        kept = dict(base)
        kept["carry"] = _carry_dict(
            FAILED, reason=f"{CARRY_CHECK_FAILED}: {type(e).__name__}",
            computed=_f(base.get("stop")),
            note=("The carried stop couldn't be checked, so this stop may be lower than "
                  "on earlier days."))
        return kept


def _apply(out: dict, carry_row, *, as_of, bars, avg_cost, journal_sig,
           journal_readable, policy_sig_now, carry_readable) -> dict:
    from stock_analyzer.held_stop_wiring import label_for  # lazy: see module docstring

    basis, binding = out.get("basis"), out.get("binding")
    computed = _f(out.get("stop"))

    def done(carry: dict) -> dict:
        out["carry"] = carry
        return out

    if (basis not in _APPLY_BASES or computed is None or computed <= 0
            or basis == "buy_day" or out.get("buy_day")
            or (basis == "prior_session" and binding != "prior_session")):
        return done(_carry_dict(NA, computed=computed))
    if not carry_readable:
        return done(_carry_dict(
            UNREADABLE, reason="saved carried stops could not be read", computed=computed,
            note=("The saved carried stop couldn't be read, so this stop may be lower "
                  "than on earlier days.")))
    if carry_row is None:
        return done(_carry_dict(NONE, reason="no saved carried stop", computed=computed))

    row = carry_row if isinstance(carry_row, dict) else {}
    row_as_of = _d(row.get("as_of"))
    row_stop = _f(row.get("stop"))
    if row_as_of is not None and row_as_of > as_of:
        # a row dated TODAY is accepted: the EOD writes row D = stop(D), built from
        # sessions before D, so it is exactly the stop in force all of D and the
        # window over (ref, D) is empty (an evening visit can't create a false breach)
        return done(_carry_dict(
            NONE, reason=f"{R_FUTURE}: saved row is dated {row_as_of}, after {as_of}",
            row_as_of=row_as_of, computed=computed))

    row_set_on = _d(row.get("stop_set_on")) or row_as_of

    def lapsed(reason: str) -> dict:
        shown = (f" ({_money(row_stop)} from {row_set_on})"
                 if row_stop is not None and row_set_on else "")
        return done(_carry_dict(
            LAPSED, value=row_stop, set_on=row_set_on, reason=reason, row_as_of=row_as_of,
            computed=computed,
            note=(f"A carried stop{shown} wasn't used because {_why(_code(reason))}.")))

    ok, why = check_row(row, as_of=as_of, avg_cost=avg_cost, journal_sig=journal_sig,
                        journal_readable=journal_readable, policy_sig_now=policy_sig_now,
                        allow_same_day=True)
    if not ok:
        return lapsed(why)
    ok, why = hsf.verify_closes(row, bars, as_of, tol_pct=float(_C.HELD_STOP_FLOOR_CLOSE_TOL_PCT))
    if not ok:
        return lapsed(why)

    saved = round(row_stop, 2)
    win = _window_term(hs.completed_bars(bars, as_of), _d(row.get("ref_session")), _mult())
    comp = round(computed, 2)
    best = comp
    pick = None                      # None = computed; "row" / "window"
    if saved > best:
        best, pick = saved, "row"
    if win is not None and win[0] > best:
        best, pick = win[0], "window"
    if pick is None:
        return done(_carry_dict(NOT_BINDING, value=saved, set_on=row_set_on,
                                row_as_of=row_as_of, computed=computed,
                                binding=row.get("binding")))
    out["stop"] = best
    if pick == "row":
        out["binding"] = row["binding"]
        out["ratchet_label"] = row.get("ratchet_label") if row["binding"] == "ratchet" else None
        out["bind_session"] = _d(row.get("bind_session"))
        out["bind_close"] = _f(row.get("bind_close"))
        set_on = row_set_on
        note = (f"The stop is held at {_money(best)}, the stop carried from {row_set_on}, "
                f"because your trades in this position haven't changed since then and "
                f"the saved prices still match. Today's figure was {_money(computed)}.")
    else:
        out["binding"] = "prior_session"
        out["ratchet_label"] = None
        out["bind_session"], out["bind_close"] = win[1], win[2]
        set_on = min(_next_trading_day(win[1]), as_of)
        note = (f"The stop is held at {_money(best)}, the highest prior-close stop since "
                f"the carried one was saved, because your trades in this position "
                f"haven't changed since then. Today's figure was {_money(computed)}.")
    out["stop_set_on"] = set_on
    out["label"] = label_for(out.get("basis"), out.get("binding"), out.get("ratchet_label"))
    return done(_carry_dict(APPLIED, value=best, set_on=set_on, note=note,
                            row_as_of=row_as_of, computed=computed, binding=out["binding"]))


# ── writer ───────────────────────────────────────────────────────────────────
def rows_to_write(held_stops, as_of: date, *, journal_sigs, avg_costs, prior_rows,
                  session_closes, journal_readable: bool,
                  carry_readable: bool) -> list[dict]:
    """`held_stop_carry` rows for the EOD lane to upsert (key: ticker). `held_stops`
    is `payload["held_stops"]` (the EFFECTIVE, already floored and carried AUTO
    result per ticker; never `port_df["Stop"]`, which a manual stop can override),
    `as_of` the ET session date the stops were in force for, `journal_sigs`
    {TICKER: journal_buy_sig(...)}, `avg_costs` {TICKER: average cost} (falls back
    to the result's own `avg_cost`), `prior_rows` {TICKER: saved row} as read for
    this run, `session_closes` {TICKER: the Close of the `as_of` session itself} (the
    EOD frame's bar dated `as_of`; REQUIRED, so a caller can't forget it; used only
    by the seed guard).

    FAIL CLOSED: nothing at all unless `journal_readable is True` and
    `carry_readable is True`, and nothing on a non-trading `as_of` (a forced weekend
    run). Per ticker a row is written only when the basis is full / partial /
    prior_session (never buy_day / unavailable; the rejected-fill buy-day fallback
    has carry state n/a and so never passes the carry-dict rule below), the stop is finite
    and > 0, the journal fingerprint and average cost are known, the reference bar
    (before `as_of`) and the binding bar are known, and the carry code did not
    fail. A partial / prior_session result must also carry a `carry` dict (the
    reader ran); a result without one writes nothing, so it can't overwrite rows it
    never read.

    SEED GUARD (partial / prior_session only): with no VALID prior row (absent, or it
    would lapse: the result's own carry state is not applied / not_binding), a row
    is never SEEDED when the stop is already breached by the `as_of` session's own
    close (`is_stop_breached(session_close, stop)`, equality counts), and an unknown
    close never seeds. The reference close (C(as_of - 1)) is the wrong comparison: a
    computed prior-close stop is always below it, so the guard would never fire,
    while a gap-down on `as_of` would seed a stop that is already breached and keep
    a false "Sell all" alive from the next session. Advancing a valid row is always
    allowed: that advance is what catches a slow bleed. FULL basis is exempt: a full
    stop is dated by the journal, so a genuine full-basis row (possibly already past
    its stop) must be saved to carry into the day before a mismatch starts.

    SAME-DAY RE-RUN: a prior row dated `as_of` (an earlier run of this EOD) that
    still matches and holds a stop >= this run's is left alone (belt and braces: a re-run
    must not replace a row that already holds more); a row
    dated after `as_of` is never overwritten. Dates are ISO strings."""
    as_of = _d(as_of)
    if (not isinstance(held_stops, dict) or as_of is None or journal_readable is not True
            or carry_readable is not True or not is_trading_day(as_of)):
        return []
    sig_now, c_sig = hsf.policy_sig(), carry_sig()
    journal_sigs = journal_sigs if isinstance(journal_sigs, dict) else {}
    avg_costs = avg_costs if isinstance(avg_costs, dict) else {}
    prior_rows = prior_rows if isinstance(prior_rows, dict) else {}
    session_closes = session_closes if isinstance(session_closes, dict) else {}
    rows: list[dict] = []
    for ticker, r in sorted(held_stops.items(), key=lambda kv: str(kv[0])):
        t = str(ticker).strip().upper()
        if not t or not isinstance(r, dict) or r.get("basis") not in _WRITE_BASES:
            continue
        basis, binding = r["basis"], r.get("binding")
        stop = _f(r.get("stop"))
        avg = _f(avg_costs.get(t))
        if avg is None:
            avg = _f(r.get("avg_cost"))
        jsig = _sig_pair(journal_sigs.get(t))
        ref_s, ref_c = _d(r.get("last_session")), _f(r.get("last_close"))
        if (stop is None or stop <= 0 or avg is None or avg <= 0 or jsig is None
                or ref_s is None or ref_c is None or ref_c <= 0 or ref_s >= as_of
                or binding not in _BINDINGS):
            continue
        bind_s, bind_c = _d(r.get("bind_session")), _f(r.get("bind_close"))
        if (bind_s is None or bind_c is None) and binding == "prior_session":
            bind_s, bind_c = ref_s, ref_c       # the plain prior-close stop binds on its own bar
        if bind_s is None or bind_c is None or bind_c <= 0:
            continue
        carry = r.get("carry")
        if isinstance(carry, dict) and carry.get("state") == FAILED:
            continue                  # a code defect: write nothing for this name
        if basis != "full" and (not isinstance(carry, dict)
                                or carry.get("state") not in _WRITABLE_STATES):
            continue                  # the reader never ran / unreadable / n/a: fail closed
        prior = prior_rows.get(t)
        prior_as_of = _d(prior.get("as_of")) if isinstance(prior, dict) else None
        if prior_as_of is not None and as_of < prior_as_of:
            continue                  # an out-of-order run never overwrites a newer row
        if prior_as_of == as_of:
            ok, _ = check_row(prior, as_of=as_of, avg_cost=avg, journal_sig=jsig,
                              journal_readable=True, policy_sig_now=sig_now,
                              allow_same_day=True)
            prior_stop = _f(prior.get("stop"))
            if ok and prior_stop is not None and prior_stop >= round(stop, 2):
                continue              # an earlier run today already holds >= this value
        if basis != "full" and not (isinstance(carry, dict)
                                    and carry.get("state") in _VALID_STATES):
            sess_c = _f(session_closes.get(t))
            if sess_c is None or sess_c <= 0 or is_stop_breached(sess_c, round(stop, 2)):
                continue              # seed guard: never seed an unknown / already-breached stop
        set_on = _d(r.get("stop_set_on")) or as_of
        if set_on > as_of:
            set_on = as_of
        label = r.get("ratchet_label")
        rows.append({
            "ticker": t,
            "as_of": as_of.isoformat(),
            "stop": round(stop, 2),
            "stop_set_on": set_on.isoformat(),
            "source_basis": basis,
            "binding": binding,
            "ratchet_label": str(label) if binding == "ratchet" and label else None,
            "avg_cost": round(avg, 2),
            "journal_buy_count": jsig[0],
            "journal_buy_max_id": jsig[1],
            "ref_session": ref_s.isoformat(),
            "ref_close": ref_c,
            "bind_session": bind_s.isoformat(),
            "bind_close": bind_c,
            "policy_sig": sig_now,
            "carry_sig": c_sig,
            "source": SOURCE_EOD,
        })
    return rows


# ── sweep ────────────────────────────────────────────────────────────────────
def sweep_tickers(saved_tickers, held_tickers) -> "set[str] | None":
    """Tickers whose saved rows the EOD lane should delete: saved - held (a sold
    position, so a later re-entry starts clean). `held_tickers is None` (or
    `saved_tickers is None`) = what is held / saved could not be established: None,
    and the caller skips the delete (the `write_guards.snapshot_sweep_keep`
    semantics; a lingering row is recoverable, a deleted held name's carry is
    not). An EMPTY held set is a real answer (everything sold): all saved rows go."""
    if held_tickers is None or saved_tickers is None:
        return None
    held = {str(t).strip().upper() for t in held_tickers if str(t).strip()}
    saved = {str(t).strip().upper() for t in saved_tickers if str(t).strip()}
    return saved - held


# ── premarket heartbeat / EOD failure ────────────────────────────────────────
def lane_failure(held_stops, carry_readable: bool) -> str | None:
    """The premarket lane goes red only when the saved carried stops are unreadable
    AND at least one holding is on basis partial / prior_session (the only bases a
    carry can lift on). Other bases, and a missing or non-dict result, don't count:
    no carry could have applied to them. Otherwise None (log-only). Returns the
    failure clause, without a prefix (mirrors `held_stop_floor.lane_failure`)."""
    if carry_readable or not isinstance(held_stops, dict):
        return None
    names = sorted(str(t) for t, h in held_stops.items()
                   if isinstance(h, dict) and h.get("basis") in _APPLY_BASES)
    if not names:
        return None
    return ("the saved carried stops could not be read while "
            f"{len(names)} holding(s) are on a partial / prior-close stop basis ({', '.join(names)})")


def carry_check_failures(held_stops) -> list[str]:
    """Sorted tickers whose carry code raised (`carry.state == "failed"`): a code
    defect the EOD lane turns red (like `FLOOR_CHECK_FAILED`)."""
    if not isinstance(held_stops, dict):
        return []
    return sorted(str(t) for t, h in held_stops.items()
                  if isinstance(h, dict) and isinstance(h.get("carry"), dict)
                  and h["carry"].get("state") == FAILED)


# ── Home notice severity ─────────────────────────────────────────────────────
def notice_tier(result) -> str | None:
    """Severity of the "Stop basis" notice for one held-stop result (U4):

      "error"    a fixable journal cause (mismatch / no_history / unreadable, read
                 from `result["episode_status"]`, which C3 copies from
                 `episode_from_journal`) leaves the name on a prior_session stop and
                 the carry is NOT holding it (state other than applied / not_binding)
      "warning"  the same, but the carry is holding it
      None       everything else: an "ok" episode (the young-listing case, whose
                 notice is F-320's), a full / partial basis, a missing status, or a
                 non-dict result

    Never about the manual stop (not an input here)."""
    if not isinstance(result, dict) or result.get("basis") != "prior_session":
        return None
    if result.get("episode_status") not in _FIXABLE_STATUSES:
        return None
    carry = result.get("carry")
    state = carry.get("state") if isinstance(carry, dict) else None
    return "warning" if state in _VALID_STATES else "error"
