"""Entry regime check: the pre-registered market-trend rules (MEASURE ONLY).

docs/plans/entry-regime-check.md (EnterNowInProtect step 2). Three rules say
whether the market's TREND, not its one-day move, would hold new entries:

  A (primary)  the S&P 500 F-319 trend label is in ENTRY_REGIME_HOLD_SPX_LABELS
  B            the label is in ENTRY_REGIME_ALT_HOLD_SPX_LABELS (A plus Steady)
  C            risk-off at the prior close: the S&P 500 below its
               RISK_OFF_TREND_MA-day average, OR the VIX close dated that
               session >= RISK_OFF_VIX_LEVEL

Every verdict is three-valued, "hold" / "clear" / "unknown", and an unknown read
is NEVER "clear". The inputs are the LAST COMPLETED session's closes
(`market_trend.as_of_session`), the same at 09:45, mid-day and evening; this
module never reads an intraday or live value, and a bar dated after the as-of
session changes nothing.

Nothing here is wired into a decision. No pick, size, stop, gate or card reads a
verdict; a test pins that no decision module imports this one. The readout
(`scripts/enter_now_regime_readout.py`, C2) and the daily shadow row (C3) are
the only intended consumers, until the plan's go-live criteria (section 4) are
met and a fresh design wires it.

Pure: no streamlit, no network, no DB, no `exit_advisor`. Rule values live in
`constants.py`; bump `ENTRY_REGIME_RULE_VERSION` when a rule definition, or a
TREND_* / RISK_OFF_* value a rule depends on, changes.
"""
from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np
import pandas as pd

from stock_analyzer.constants import (
    ENTRY_REGIME_ALT_HOLD_SPX_LABELS,
    ENTRY_REGIME_HOLD_SPX_LABELS,
    ENTRY_REGIME_RULE_VERSION,
    RISK_OFF_VIX_LEVEL,
)
from stock_analyzer.data import is_trading_day, previous_trading_day
from stock_analyzer.market_time import ET

_SPX = "^GSPC"
_NASDAQ = "^IXIC"

RULES = ("A", "B", "C")

READ_OK = "ok"
READ_TREND_UNREAD = "trend_unread"
READ_ASOF_MISMATCH = "asof_mismatch"  # shadow_row only

HOLD = "hold"
CLEAR = "clear"
UNKNOWN = "unknown"

_MA200_SIDES = ("below", "above", "at")


# ── small helpers ────────────────────────────────────────────────────────────

def _as_date(d) -> date | None:
    """A calendar date, or None. A tz-aware datetime / Timestamp is converted
    to America/New_York FIRST (a 21:00 ET evening is already the next UTC
    day: the TJUtc / C3b class; C1 review 2026-11-22). A naive value keeps its
    own date. Strings and np.datetime64 are not accepted (None)."""
    if d is None or d is pd.NaT:   # NaT is not a Timestamp; it would slip into the datetime branch
        return None
    if isinstance(d, pd.Timestamp):
        return (d.tz_convert(ET) if d.tzinfo is not None else d).date()
    if isinstance(d, datetime):
        return (d.astimezone(ET) if d.tzinfo is not None else d).date()
    if isinstance(d, date):
        return d
    return None


def _clean_vix(v) -> float | None:
    """A usable VIX close: a real, finite, positive number. bool is never one."""
    if v is None or isinstance(v, (bool, np.bool_)):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f <= 0:
        return None
    return f


def _row(trend_result, symbol: str) -> dict | None:
    if not trend_result:
        return None
    indices = trend_result.get("indices")
    if indices is None:
        return None
    for r in indices:
        if r.get("symbol") == symbol:
            return r
    return None


# ── public: the VIX input ────────────────────────────────────────────────────

def vix_close_asof(series, as_of) -> float | None:
    """The VIX close dated EXACTLY `as_of` (compared by calendar date, so a
    tz-naive or tz-aware index both work). None when that session has no bar or
    its value isn't a finite number above zero. Never a prior day's close, never
    a later bar. Takes a pandas Series only: a DataFrame (the shape
    ``yf.download`` returns) raises TypeError rather than silently reading as
    "no VIX"."""
    if isinstance(series, pd.DataFrame):
        raise TypeError("vix_close_asof takes a Series of VIX closes, not a DataFrame")
    target = _as_date(as_of)
    if target is None or series is None or getattr(series, "empty", True):
        return None
    try:
        s = pd.Series(series)
    except (TypeError, ValueError):
        return None
    hit = None
    for ix, v in zip(s.index, s.to_list()):
        try:
            d = pd.Timestamp(ix).date()
        except (TypeError, ValueError):
            continue  # an unparseable / NaT index entry can't be the as-of bar
        if d == target:
            hit = v  # a duplicate date keeps the last bar, as market_trend does
    return _clean_vix(hit)


# ── public: the read ─────────────────────────────────────────────────────────

def _unread(as_of, vix) -> dict:
    return {
        "as_of": as_of, "read_state": READ_TREND_UNREAD,
        "spx_label": None, "spx_reason": None, "spx_confirm_k": None,
        "spx_ma50_side": None, "spx_ma200_side": None, "spx_from_high_pct": None,
        "ndx_label": None, "ndx_ma50_side": None, "ndx_ma200_side": None,
        "vix_close": vix,
    }


def read(trend_result, vix_asof, as_of) -> dict:
    """Snapshot of the inputs the rules use, for the as-of session `as_of`.

    `trend_result` is `market_trend.compute_trend`'s result (or None);
    `vix_asof` is `vix_close_asof`'s answer. read_state is "trend_unread" (every
    spx_ / ndx_ field None) when the trend result is None, was computed for a
    different session than `as_of`, or has no usable S&P 500 label."""
    as_of = _as_date(as_of)
    vix = _clean_vix(vix_asof)
    if trend_result is None or _as_date(trend_result.get("as_of")) != as_of:
        return _unread(as_of, vix)
    spx = _row(trend_result, _SPX)
    if spx is None or spx.get("label") is None:
        return _unread(as_of, vix)
    ndx = _row(trend_result, _NASDAQ)
    if ndx is None:
        ndx = {}  # the Nasdaq is an input only: a missing row leaves its fields None
    return {
        "as_of": as_of, "read_state": READ_OK,
        "spx_label": spx.get("label"), "spx_reason": spx.get("reason"),
        "spx_confirm_k": spx.get("confirm_k"),
        "spx_ma50_side": spx.get("ma50_side"),
        "spx_ma200_side": spx.get("ma200_side"),
        "spx_from_high_pct": spx.get("from_high_pct"),
        "ndx_label": ndx.get("label"), "ndx_ma50_side": ndx.get("ma50_side"),
        "ndx_ma200_side": ndx.get("ma200_side"),
        "vix_close": vix,
    }


# ── public: the rules ────────────────────────────────────────────────────────

def _label_rule(read_: dict, labels: tuple) -> dict:
    label = read_.get("spx_label") if read_.get("read_state") == READ_OK else None
    if label is None:
        leg = {"leg": "spx_label", "state": UNKNOWN, "value": None}
        return {"state": UNKNOWN, "legs": [leg],
                "reason": "the S&P 500 trend label could not be read"}
    trips = label in labels
    leg = {"leg": "spx_label", "state": HOLD if trips else CLEAR, "value": label}
    return {"state": HOLD if trips else CLEAR, "legs": [leg],
            "reason": (f"the S&P 500 reads {label}, which is "
                       f"{'in' if trips else 'not in'} {list(labels)}")}


def _rule_c(read_: dict) -> dict:
    ok = read_.get("read_state") == READ_OK
    side = read_.get("spx_ma200_side") if ok else None
    if side not in _MA200_SIDES:
        trend_leg = {"leg": "spx_below_200d", "state": UNKNOWN, "value": None}
    else:
        trend_leg = {"leg": "spx_below_200d",
                     "state": HOLD if side == "below" else CLEAR, "value": side}
    vix = _clean_vix(read_.get("vix_close"))
    if vix is None:
        vix_leg = {"leg": "vix_at_or_above_risk_off", "state": UNKNOWN, "value": None}
    else:
        vix_leg = {"leg": "vix_at_or_above_risk_off",
                   "state": HOLD if vix >= RISK_OFF_VIX_LEVEL else CLEAR,
                   "value": vix}
    legs = [trend_leg, vix_leg]
    if any(g["state"] == HOLD for g in legs):
        state = HOLD
        tripped = [g["leg"] for g in legs if g["state"] == HOLD]
        reason = "risk-off at the prior close: " + " and ".join(tripped)
    elif all(g["state"] == CLEAR for g in legs):
        state, reason = CLEAR, "neither the 200-day leg nor the VIX leg trips"
    else:
        state = UNKNOWN
        missing = [g["leg"] for g in legs if g["state"] == UNKNOWN]
        reason = "a leg could not be measured: " + " and ".join(missing)
    return {"state": state, "legs": legs, "reason": reason}


def decide(read: dict, rule: str) -> dict:
    """{state, rule, rule_version, legs, reason} for rule "A", "B" or "C".
    state is "hold", "clear" or "unknown"; an unmeasured input is never "clear"."""
    if rule == "A":
        out = _label_rule(read, ENTRY_REGIME_HOLD_SPX_LABELS)
    elif rule == "B":
        out = _label_rule(read, ENTRY_REGIME_ALT_HOLD_SPX_LABELS)
    elif rule == "C":
        out = _rule_c(read)
    else:
        raise ValueError(f"unknown entry regime rule: {rule!r}")
    out["rule"] = rule
    out["rule_version"] = ENTRY_REGIME_RULE_VERSION
    return out


def candidates(read: dict) -> dict:
    """The three pre-registered verdicts for one read: {"A": ..., "B": ..., "C": ...}."""
    return {r: decide(read, r) for r in RULES}


# ── public: the daily shadow row (used by C3) ────────────────────────────────

def shadow_row(read: dict, call_date, source: str) -> dict | None:
    """The flat market-level row for one ET `call_date`, or None when that date
    is not an NYSE trading day (no entry call exists to compare it with).

    The read must be for the session before `call_date`
    (`previous_trading_day(call_date)`); any other as-of is recorded as read_state
    "asof_mismatch" with every rule "unknown", never as a verdict. A read that
    could not be made ("trend_unread") still yields a row. as_of_session is the
    session the read was actually for."""
    cd = _as_date(call_date)
    if cd is None or not is_trading_day(cd):
        return None
    as_of = _as_date(read.get("as_of"))
    state = read.get("read_state")
    if as_of != previous_trading_day(cd):
        state = READ_ASOF_MISMATCH
        verdicts = {r: UNKNOWN for r in RULES}
    else:
        verdicts = {r: decide(read, r)["state"] for r in RULES}
    return {
        "call_date": cd, "as_of_session": as_of, "source": source,
        "rule_version": ENTRY_REGIME_RULE_VERSION, "read_state": state,
        "spx_label": read.get("spx_label"), "spx_reason": read.get("spx_reason"),
        "spx_confirm_k": read.get("spx_confirm_k"),
        "spx_ma50_side": read.get("spx_ma50_side"),
        "spx_ma200_side": read.get("spx_ma200_side"),
        "spx_from_high_pct": read.get("spx_from_high_pct"),
        "ndx_label": read.get("ndx_label"),
        "ndx_ma50_side": read.get("ndx_ma50_side"),
        "ndx_ma200_side": read.get("ndx_ma200_side"),
        "vix_close": read.get("vix_close"),
        "rule_a": verdicts["A"], "rule_b": verdicts["B"], "rule_c": verdicts["C"],
    }
