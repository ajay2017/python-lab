"""Gate module: the persisted held-stop FLOOR (finding #26 C3c; pure, no I/O).

WHY
---
`held_stop.compute_held_stop` is rebuilt every load from the price history it is
given. On a day the long fetch fails, the driver falls back to the app's 6-month
bundle (always basis "partial"); once an episode outgrows that frame the entry
anchor drops and the trailing ATR is seeded from a different bar, so the stop can
fall below yesterday's. This module saves the last FULL-basis stop per
(ticker, episode start) and lets a later run use it as a lower bound:

    effective = max(computed, saved stop)

but only while it is still what C1's own invariant promises ("with no trade in
between, stop(D+1) >= stop(D)", held_stop.py). So the floor lapses when
  (a) the trade-and-policy fingerprint changed (an add, a split row, a fill edit,
      a rule change: C1 permits those to lower the stop), or
  (b) today's price history no longer agrees with the closes the row was saved
      from (an unrecorded split, or a bad tick later corrected).
Spec: docs/plans/held-stop-floor-c3c-spec.md (sections 2-4, 8).

WHAT IS HERE
------------
  policy_sig() / episode_fingerprint(ep, avg_cost)   what a row must match
  match_row(row, ep, avg_cost, as_of)                the fingerprint check
  verify_closes(row, frame, as_of, *, tol_pct)       the price-history check
  apply_floor(result, ticker_rows, ...)              the reader: one ticker
  rows_to_write(held_stops, as_of)                   the EOD writer's rows
  lane_failure(held_stops, readable)                 the premarket red heartbeat

Policy values are read from `constants` at call time. The basis is NEVER changed
by the floor (a floored fallback stays "partial"). Nothing here mutates an input.

Offline is three-state, as everywhere in the coordination layer: `readable=False`
(or `ticker_rows is None`) = the saved floors could not be read; `{}` = read, none
saved. They are never collapsed. Callers pass `floors.get(TICKER, {})` when the
read succeeded.

NOT WIRED as of C3c-1: nothing imports this module yet. `held_stop_wiring.label_for`
is imported lazily inside `apply_floor` because C3c-4 makes `held_stop_wiring`
import this module (a module-level import would be a cycle).
"""
from __future__ import annotations

import math
from datetime import date, datetime

import pandas as pd

from stock_analyzer import constants as _C
from stock_analyzer import held_stop as hs
from stock_analyzer.write_guards import split_non_trading_rows

POLICY_SIG_VERSION = "v1"
SOURCE_EOD = "cron:eod"
_BINDINGS = ("entry", "trailing", "ratchet")

# Floor states (the `state` of the result's `floor` dict).
APPLIED = "applied"            # saved stop above today's figure and used
NOT_BINDING = "not_binding"    # row valid, today's figure already >= it
NONE = "none"                  # no saved row for this episode (or a future-dated one)
INVALID = "invalid"            # a row exists but is not trusted today
UNREADABLE = "unreadable"      # the saved floors couldn't be read
NA = "n/a"                     # floor does not apply (basis / no episode)

# Reason codes (the text before the colon in a `match_row` / `verify_closes` reason).
R_TRADES = "trades"
R_RULES = "rules"
R_HISTORY = "history"
R_FUTURE = "future_row"
R_ROW = "row"

# floor states whose result may be written: the floors were READ and the result went
# through `apply_floor`. A result without a floor dict (a missing pass-through) or in
# state unreadable / n/a is never written, so it can't overwrite rows blind.
_WRITABLE_STATES = frozenset({APPLIED, NOT_BINDING, NONE, INVALID})


# ── small helpers ────────────────────────────────────────────────────────────
def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _d(v) -> date | None:
    """date / datetime / Timestamp / ISO string -> date; None when missing or bad."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _iso(v) -> str | None:
    d = _d(v)
    return d.isoformat() if d is not None else None


def _round_or_none(v, nd: int) -> float | None:
    x = _f(v)
    return None if x is None else round(x, nd)


def _code(reason: str) -> str:
    return str(reason or "").split(":", 1)[0].strip()


# ── fingerprint ──────────────────────────────────────────────────────────────
def policy_sig() -> str:
    """The stop-rule regime a row was built under. A changed constant changes the
    string, so every saved row becomes invalid at once (it is the regime version
    for the `held_stop_floors` history table). `HELD_STOP_FLOOR_CLOSE_TOL_PCT` is
    deliberately NOT in it: it tunes the price-history check, not the stop."""
    return ";".join([
        POLICY_SIG_VERSION,
        f"atr={float(_C.ATR_STOP_MULT)!r}",
        f"trail={float(_C.HELD_TRAIL_ATR_MULT)!r}",
        f"start={str(_C.HELD_TRAIL_START)}",
        f"warmup={int(_C.HELD_STOP_WARMUP_BARS)}",
        f"minbars={int(_C.HELD_STOP_MIN_BARS)}",
        f"filltol={float(_C.HELD_STOP_FILL_TOL_PCT)!r}",
        f"sharestol={float(_C.HELD_STOP_SHARES_TOL)!r}",
        f"ratchet={_C.STOP_RATCHET_LEVELS!r}",
        f"atrlen={int(hs._ATR_LENGTH)}",
    ])


def episode_fingerprint(ep: dict, avg_cost) -> dict:
    """What a saved row must equal today: the first fill (round 6), the split
    ratio (round 9), the last BUY date, the average cost at cents, and the policy
    signature. Average cost is the journal's average-method figure, unchanged by
    a sell, so a partial sell keeps the floor (matches C1's invariant)."""
    ep = ep or {}
    ratio = _f(ep.get("split_ratio"))
    return {
        "first_buy_price": _round_or_none(ep.get("first_buy_price"), 6),
        "split_ratio": round(ratio if ratio is not None else 1.0, 9),
        "last_buy": _iso(ep.get("last_buy")),
        "avg_cost": _round_or_none(avg_cost, 2),
        "policy_sig": policy_sig(),
    }


def _row_fingerprint(row: dict) -> dict:
    ratio = _f(row.get("split_ratio"))
    return {
        "first_buy_price": _round_or_none(row.get("first_buy_price"), 6),
        "split_ratio": round(ratio if ratio is not None else 1.0, 9),
        "last_buy": _iso(row.get("last_buy")),
        "avg_cost": _round_or_none(row.get("avg_cost"), 2),
        "policy_sig": None if row.get("policy_sig") is None else str(row.get("policy_sig")),
    }


def match_row(row: dict, ep: dict, avg_cost, as_of: date) -> tuple[bool, str]:
    """Does the saved `row` still describe today's holding episode and rules?
    (True, "") or (False, "<code>: <detail>") with code `future_row` (the row is
    dated after `as_of`), `trades` (first fill, split ratio, last buy, average
    cost or episode start changed), `rules` (policy signature changed) or `row`
    (not a usable row)."""
    if not isinstance(row, dict):
        return False, f"{R_ROW}: no saved row"
    row_as_of = _d(row.get("as_of"))
    if row_as_of is None:
        return False, f"{R_ROW}: saved row has no date"
    if row_as_of > as_of:
        return False, f"{R_FUTURE}: saved row is dated {row_as_of}, after {as_of}"
    if row.get("episode_start") is not None and ep and ep.get("start") is not None \
            and _d(row.get("episode_start")) != _d(ep.get("start")):
        return False, f"{R_TRADES}: episode start"
    want, have = episode_fingerprint(ep, avg_cost), _row_fingerprint(row)
    for field in ("first_buy_price", "split_ratio", "last_buy", "avg_cost"):
        if want[field] != have[field]:
            return False, f"{R_TRADES}: {field}"
    if want["policy_sig"] != have["policy_sig"]:
        return False, f"{R_RULES}: policy signature"
    return True, ""


# ── price-history check ──────────────────────────────────────────────────────
def verify_closes(row: dict, frame, as_of: date, *, tol_pct: float) -> tuple[bool, str]:
    """Do today's completed bars still carry the closes the row was saved from?
    Checks `ref_session` and `bind_session` against the frame's completed bars
    (`held_stop.completed_bars`). A saved session OUTSIDE the frame's span (before
    its first bar or after its last) is skipped, so a short fallback frame still
    verifies on the other one; at least one session must be checked. A session
    INSIDE the span that is absent (a deleted bar, or a NaN Close that
    `completed_bars` drops) is a failure: the price the row was built from is
    gone, so it can't be re-certified. Every checked session must be within
    `tol_pct` % of its saved close. A split (3:2 = 33%, 2:1 = 50%) or a corrected
    tick fails; the adjusted bundle's dividend drift passes.
    (True, "") or (False, "history: <detail>")."""
    try:
        bars = hs.completed_bars(frame, as_of)
    except Exception:
        return False, f"{R_HISTORY}: price history unreadable"
    if len(bars) == 0:
        return False, f"{R_HISTORY}: no completed bars"
    first, last = bars.index[0], bars.index[-1]
    checked = 0
    for s_key, c_key in (("ref_session", "ref_close"), ("bind_session", "bind_close")):
        sess, saved = _d(row.get(s_key)), _f(row.get(c_key))
        if sess is None or saved is None or saved <= 0:
            continue
        ts = pd.Timestamp(sess)
        if ts < first or ts > last:
            continue                      # outside the frame's span: nothing to compare
        name = s_key[:-8]                 # "ref" / "bind"
        got = _f(bars["Close"].loc[ts]) if ts in bars.index else None
        if got is None:
            return False, (f"{R_HISTORY}: {name} session {sess} is missing from the "
                           "price history")
        checked += 1
        # |got/saved - 1| * 100 <= tol_pct, without the float round-trip of the ratio
        if abs(got - saved) * 100.0 > float(tol_pct) * saved:
            return False, (f"{R_HISTORY}: {name} close {got:.2f} vs saved "
                           f"{saved:.2f} on {sess}")
    if checked == 0:
        return False, f"{R_HISTORY}: none of the saved sessions is in the price history"
    return True, ""


# ── reader ───────────────────────────────────────────────────────────────────
def _money(x: float) -> str:
    return f"\\${x:,.2f}"


def _why(code: str) -> str:
    return {
        R_TRADES: "your trades in this position changed since then",
        R_HISTORY: ("the price history no longer matches the prices it was saved from "
                    "(for example a stock split)"),
        R_RULES: "the stop rules changed since then",
    }.get(code, "the saved record couldn't be used")


def _floor_dict(state: str, *, value=None, set_on=None, reason="", note="",
                row_as_of=None, computed=None) -> dict:
    return {"state": state, "value": value, "set_on": set_on, "reason": reason,
            "note": note, "row_as_of": row_as_of, "computed": computed}


def _lookup(ticker_rows, start: date):
    if not isinstance(ticker_rows, dict):
        return None
    for key in (start, start.isoformat(), pd.Timestamp(start)):
        if key in ticker_rows:
            return ticker_rows[key]
    for k, v in ticker_rows.items():          # tolerate other date-like keys
        if _d(k) == start:
            return v
    return None


def apply_floor(result: dict, ticker_rows, *, ep: dict, avg_cost, frame, as_of: date,
                readable: bool) -> dict:
    """The effective held-stop result for one ticker: a NEW dict (no input is
    mutated) that is `result` plus

      floor        {state, value, set_on, reason, note, row_as_of, computed}
      stop_set_on  the first as_of at which the effective value was reached
      label        `held_stop_wiring.label_for(basis, binding, ratchet_label)`

    `result` is `held_stop.compute_held_stop`'s dict (or the driver's fallback
    variant of it); `frame` is the frame that produced it; `ticker_rows` is
    {episode_start: row} for this ticker (`{}` = none saved, `None` = couldn't be
    read); `readable` is False when the saved floors couldn't be read.

    The floor applies only on basis full / partial with a computed stop, to the
    current episode's row, never a row dated after `as_of`, and only when
    `match_row` and `verify_closes` both pass. When it binds, the stop, binding,
    ratchet label, bind_* and `stop_set_on` are the saved row's; the basis is
    never touched. `floor.note` is a plain-English sentence for a degraded
    (non-full) basis only (empty on a full day: no banner, no note).
    """
    from stock_analyzer.held_stop_wiring import label_for  # lazy: see module docstring

    out = dict(result)
    basis = out.get("basis")
    computed = _f(out.get("stop"))

    def done(floor: dict, *, set_on) -> dict:
        out["floor"] = floor
        out["stop_set_on"] = set_on
        out["label"] = label_for(out.get("basis"), out.get("binding"), out.get("ratchet_label"))
        return out

    start = _d((ep or {}).get("start")) if isinstance(ep, dict) and ep.get("status", "ok") == "ok" else None
    if basis not in ("full", "partial") or computed is None or start is None or start >= as_of:
        return done(_floor_dict(NA, computed=computed), set_on=None)

    degraded = basis != "full"
    if not readable or ticker_rows is None:
        note = ("The saved full-history stop couldn't be read, so this stop may be "
                "lower than on earlier days." if degraded else "")
        return done(_floor_dict(UNREADABLE, reason="saved floors could not be read",
                                note=note, computed=computed), set_on=None)

    row = _lookup(ticker_rows, start)
    if row is None:
        return done(_floor_dict(NONE, reason="no saved floor for this episode",
                                computed=computed), set_on=as_of)

    if not isinstance(row, dict):
        row = {}                      # falls out below as an incomplete row
    row_as_of = _d(row.get("as_of"))
    row_stop = _f(row.get("stop"))
    row_set_on = _d(row.get("stop_set_on")) or row_as_of
    if row_as_of is not None and row_as_of > as_of:
        return done(_floor_dict(NONE, reason=f"{R_FUTURE}: saved row is dated {row_as_of}",
                                row_as_of=row_as_of, computed=computed), set_on=as_of)

    def invalid(reason: str) -> dict:
        shown = f" ({_money(row_stop)} from {row_set_on})" if row_stop is not None and row_set_on else ""
        note = (f"A saved full-history stop{shown} wasn't used because "
                f"{_why(_code(reason))}." if degraded else "")
        return done(_floor_dict(INVALID, value=row_stop, set_on=row_set_on, reason=reason,
                                note=note, row_as_of=row_as_of, computed=computed),
                    set_on=as_of)

    if row_stop is None or row_stop <= 0 or row_as_of is None \
            or row.get("binding") not in _BINDINGS:
        return invalid(f"{R_ROW}: saved row is incomplete")
    ok, why = match_row(row, ep, avg_cost, as_of)
    if not ok:
        return invalid(why)
    ok, why = verify_closes(row, frame, as_of, tol_pct=float(_C.HELD_STOP_FLOOR_CLOSE_TOL_PCT))
    if not ok:
        return invalid(why)

    saved = round(row_stop, 2)
    if saved > computed:
        out["stop"] = saved
        out["binding"] = row["binding"]
        out["ratchet_label"] = row.get("ratchet_label") if row["binding"] == "ratchet" else None
        out["bind_session"] = _d(row.get("bind_session"))
        out["bind_close"] = _f(row.get("bind_close"))
        note = (f"The stop is held at {_money(saved)}, the full-history stop saved on "
                f"{row_set_on}, because your trades in this position haven't changed since "
                f"then and the saved prices still match. Today's figure from the shorter "
                f"history was {_money(computed)}." if degraded else "")
        return done(_floor_dict(APPLIED, value=saved, set_on=row_set_on, note=note,
                                row_as_of=row_as_of, computed=computed), set_on=row_set_on)
    # today's figure already reaches the saved one: the value was first reached on
    # the saved row's date only when it is exactly equal
    return done(_floor_dict(NOT_BINDING, value=saved, set_on=row_set_on,
                            row_as_of=row_as_of, computed=computed),
                set_on=row_set_on if round(computed, 2) == saved else as_of)


# ── writer ───────────────────────────────────────────────────────────────────
def rows_to_write(held_stops, as_of: date) -> list[dict]:
    """`held_stop_floors` rows for the EOD lane to upsert (key: ticker,
    episode_start). `held_stops` is `payload["held_stops"]` (the EFFECTIVE, already
    floored AUTO result per ticker; never `port_df["Stop"]`, which a manual stop
    can override), `as_of` the ET session date the stops were in force for.

    A row is written only when ALL hold: `as_of` is a trading day (a forced
    weekend run writes nothing); basis == "full" (never partial, prior_session,
    buy_day or unavailable); the stop is finite and > 0; the episode start (before
    `as_of`), average cost, split ratio, binding, the reference bar and the
    binding bar are all present; the result carries a `floor` dict whose state is
    applied / not_binding / none / invalid (fail closed: a missing `floor`, or state
    unreadable / n/a, writes nothing, so it can't overwrite rows it never read; a
    full-basis result with a computed stop and an episode never reaches n/a); and
    `as_of` isn't earlier than the saved row it read.

    A floored result keeps the saved stop, binding, bind_* and stop_set_on and
    refreshes the reference bar; a fresh value overwrites an invalid floor (this
    is how a corrected bad tick or a split heals). Dates are ISO strings."""
    if not isinstance(held_stops, dict):
        return []
    sig = policy_sig()
    rows: list[dict] = []
    for ticker, r in sorted(held_stops.items(), key=lambda kv: str(kv[0])):
        if not isinstance(r, dict) or r.get("basis") != "full":
            continue
        stop, avg_cost = _f(r.get("stop")), _f(r.get("avg_cost"))
        ep = r.get("episode") if isinstance(r.get("episode"), dict) else None
        start = _d((ep or {}).get("start"))
        ref_s, ref_c = _d(r.get("last_session")), _f(r.get("last_close"))
        bind_s, bind_c = _d(r.get("bind_session")), _f(r.get("bind_close"))
        binding = r.get("binding")
        floor = r.get("floor")
        if not isinstance(floor, dict):     # absent / None: not a floor result (fresh value)
            floor = {}
        ratio = _f((ep or {}).get("split_ratio"))
        if (stop is None or stop <= 0 or avg_cost is None or avg_cost <= 0 or ep is None
                or start is None or start >= as_of or ratio is None or ratio <= 0
                or binding not in _BINDINGS
                or ref_s is None or ref_c is None or ref_c <= 0 or ref_s >= as_of
                or bind_s is None or bind_c is None or bind_c <= 0):
            continue
        if floor.get("state") not in _WRITABLE_STATES:
            continue                  # no floor result / unreadable / n/a: fail closed
        row_as_of = _d(floor.get("row_as_of"))
        if row_as_of is not None and as_of < row_as_of:
            continue
        set_on = _d(r.get("stop_set_on")) or as_of
        if set_on > as_of:
            set_on = as_of
        label = r.get("ratchet_label")
        rows.append({
            "ticker": str(ticker).strip().upper(),
            "episode_start": start.isoformat(),
            "as_of": as_of.isoformat(),
            "stop": round(stop, 2),
            "stop_set_on": set_on.isoformat(),
            "binding": binding,
            "ratchet_label": str(label) if binding == "ratchet" and label else None,
            "first_buy_price": _round_or_none(ep.get("first_buy_price"), 6),
            "split_ratio": round(ratio, 9),
            "last_buy": _iso(ep.get("last_buy")),
            "avg_cost": round(avg_cost, 2),
            "ref_session": ref_s.isoformat(),
            "ref_close": ref_c,
            "bind_session": bind_s.isoformat(),
            "bind_close": bind_c,
            "policy_sig": sig,
            "source": SOURCE_EOD,
        })
    kept, _ = split_non_trading_rows(rows, "as_of")
    return kept


# ── premarket heartbeat ──────────────────────────────────────────────────────
def lane_failure(held_stops, readable: bool) -> str | None:
    """U4 (owner 2026-11-16: partial only): the premarket lane goes red when the
    saved floors are unreadable AND at least one holding has basis == "partial"
    (the only basis a floor can lift on a fallback day). prior_session /
    unavailable names no longer count, and neither does a missing or non-dict
    result: no floor could have applied to it, so there is nothing to disclose.
    Otherwise None (log-only). Returns the failure clause, without a prefix."""
    if readable or not isinstance(held_stops, dict):
        return None
    names = sorted(str(t) for t, h in held_stops.items()
                   if isinstance(h, dict) and h.get("basis") == "partial")
    if not names:
        return None
    return ("the saved full-history stops could not be read while "
            f"{len(names)} holding(s) are on a limited (partial) stop basis ({', '.join(names)})")
