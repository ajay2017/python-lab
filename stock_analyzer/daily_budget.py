"""Daily new-position budget -- cap the SUM of a trading day's new buys.

F-255 caps ONE new position at NET_CAPITAL_POSITION_CAP_PCT of net capital;
nothing capped the day's total. This module is the pure decision: given what
was already bought today (logged), what was already pushed (emailed
allocations) and the day's candidate list, it sizes each candidate from what
is left of DAILY_NEW_POSITION_BUDGET_PCT of net capital, highest composite
first. See docs/plans/daily-new-position-budget.md.

Pure: no I/O, no Streamlit, no clock reads (now/day are parameters), never
mutates its inputs. MUST NOT import stock_analyzer.margin (awareness-only
invariant, tests/test_margin.py _GATE_MODULES) -- callers resolve
`margin.resolve_net_capital` and pass plain numbers/state names in.

Three-state conventions: None = "couldn't check" (never collapsed to {}).
A budget that cannot be checked withholds every budget-consuming SIZE
(status budget_unknown); the BUY call itself is never touched.

Candidate dict (input to `allocate`):
    {"ticker": str, "composite": float|None, "momentum": float|None,
     "is_etf": bool, "price": float,
     "sizing": dict as produced by daily_briefing._position_size_for_render}
A sizing with no "shares" key (ceiling/capital/stop infeasible,
portfolio_unknown, or `{}`) is a no-size marker: passed through, consumes
nothing.
Allocation (emailed size, locked): {ticker: {"shares": n, "dollars": $}}.
Logged buys: {ticker: dollars} from `buys_on_et_day`.
"""
from __future__ import annotations

import math

import pandas as pd

from stock_analyzer.constants import (
    ACCOUNT_CASH_STALE_DAYS,
    DAILY_NEW_POSITION_BUDGET_PCT,
)
from stock_analyzer.sold_today import _et_trade_dates

# Stamped on every budget-processed sizing (version 4 = adds the daily budget;
# daily_briefing.SIZING_FORMULA_VERSION is bumped to match by the wiring chunk).
BUDGET_SIZING_VERSION = 4

SIZED = "sized"
BUDGET_PARTIAL = "budget_partial"
BUDGET_RESERVED = "budget_reserved"
BUDGET_EXHAUSTED = "budget_exhausted"
BUDGET_UNKNOWN = "budget_unknown"
PASSTHROUGH = "passthrough"

CALLED_REASON = "net capital is at or below zero"
_TRADE_COLS = ("traded_at", "action", "ticker", "shares", "price")


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _trade_rows(trades_df, keep):
    """[(ticker, action, dollars, utc_ts)] for BUY/SELL rows where
    `keep(et_date, utc_ts)` is true. None when the log can't be read or a kept
    row can't be valued (rows that are not kept never poison the result)."""
    if trades_df is None:
        return None
    if getattr(trades_df, "empty", True):
        return []
    if any(c not in trades_df.columns for c in _TRADE_COLS):
        return None
    try:
        traded_at, dates = _et_trade_dates(trades_df)
        actions = trades_df["action"].astype(str).str.upper()
        out = []
        for i in range(len(trades_df)):
            act = actions.iloc[i]
            if act not in ("BUY", "SELL"):
                continue
            ts = traded_at.iloc[i]
            if pd.isna(ts):
                return None  # a BUY/SELL whose date is unknowable
            if not keep(dates.iloc[i], ts):
                continue
            tk = trades_df["ticker"].iloc[i]
            sh = _num(trades_df["shares"].iloc[i])
            px = _num(trades_df["price"].iloc[i])
            if sh is None or px is None or sh < 0 or px <= 0 or pd.isna(tk) \
                    or not str(tk).strip():
                return None  # kept but can't be valued
            out.append((str(tk).strip().upper(), act, sh * px, ts))
        return out
    except Exception:
        return None


def buys_on_et_day(trades_df, day) -> "dict[str, float] | None":
    """{ticker: dollars bought on ET `day`} (adds included); None = can't read."""
    rows = _trade_rows(trades_df, lambda d, ts: d == day)
    if rows is None:
        return None
    out: dict[str, float] = {}
    for tk, act, usd, _ in rows:
        if act == "BUY":
            out[tk] = out.get(tk, 0.0) + usd
    return out


def trades_after(trades_df, day, after_ts) -> "tuple[float, float] | None":
    """(buy $, sell $) of EVERY BUY/SELL logged strictly after `after_ts`,
    whatever its date (a cash record is trusted for days, so an earlier-dated
    post-cash trade must still be corrected); None = can't read. `day` is
    accepted for signature symmetry and does not filter. A trade exactly AT
    `after_ts` is not after it (the cash record already reflects it)."""
    try:
        cut = pd.to_datetime(after_ts, utc=True)
    except Exception:
        return None
    if pd.isna(cut):
        return None
    rows = _trade_rows(trades_df, lambda d, ts: ts > cut)
    if rows is None:
        return None
    buy = sell = 0.0
    for _, act, usd, ts in rows:
        if act == "BUY":
            buy += usd
        else:
            sell += usd
    return buy, sell


def budget_basis(gross_book, net_capital, basis_state, cash_balance,
                 cash_as_of, trades_df, day, now) -> dict:
    """Net-capital basis for the budget, lag-corrected for trades logged after
    the cash record (holdings update on trade log; cash only at broker sync).

    basis_state is margin.resolve_net_capital's second return value
    ("levered"|"unlevered"|"stale"|"called"), passed as a plain string.
    Returns {"state": "ok"|"unknown"|"called", "basis", "reason", "lag_adjustment"}.
    """
    def _res(state, basis=None, reason=None, lag=0.0):
        return {"state": state, "basis": basis, "reason": reason,
                "lag_adjustment": lag}

    if basis_state == "called":
        return _res("called", _num(net_capital), reason=CALLED_REASON)
    if basis_state == "stale":
        return _res("unknown", reason="the cash record is out of date")
    if basis_state not in ("levered", "unlevered"):
        return _res("unknown", reason="net capital couldn't be determined")
    cash = _num(cash_balance)
    if cash is None or cash_as_of is None:
        return _res("unknown", reason="no cash record")
    try:
        age = (pd.to_datetime(now, utc=True) - pd.to_datetime(cash_as_of, utc=True)).days
    except Exception:
        return _res("unknown", reason="the cash record's date is unreadable")
    if pd.isna(age) or age > ACCOUNT_CASH_STALE_DAYS:
        return _res("unknown", reason="the cash record is out of date")
    if basis_state == "levered":
        base = _num(net_capital)
    else:
        gb = _num(gross_book)
        base = None if gb is None or gb <= 0 else gb + cash
    if base is None:
        return _res("unknown", reason="net capital couldn't be determined")
    ta = trades_after(trades_df, day, cash_as_of)
    if ta is None:
        return _res("unknown", reason="the trade log couldn't be read")
    lag = ta[1] - ta[0]
    basis = base + lag
    if basis <= 0:
        return _res("called", basis, reason=CALLED_REASON, lag=lag)
    return _res("ok", basis, lag=lag)


def candidate_sort_key(c) -> tuple:
    """(is_etf, -composite, -momentum, ticker); a missing composite/momentum
    sorts last within its group. Deterministic, so cron and app agree."""
    c = c if isinstance(c, dict) else {}

    def _neg(v):
        n = _num(v)
        return float("inf") if n is None else -n

    return (bool(c.get("is_etf")), _neg(c.get("composite")),
            _neg(c.get("momentum")), str(c.get("ticker") or "").upper())


def _is_marker(sizing) -> bool:
    return not isinstance(sizing, dict) or "shares" not in sizing


def allocate(basis_result, logged, allocations, candidates) -> dict:
    """Size `candidates` against what is left of today's budget. See module
    docstring for input shapes. Returns
    {"state", "budget", "used_logged", "used_allocated", "used_list", "left",
     "pct_used", "per_ticker": {ticker: {"status", "shares", "dollars",
     "uncapped_shares", "reason"}}}.
    `state`: "ok" | "unknown" | "called".
    """
    # One candidate per ticker, deterministically: the smaller F-255 size wins
    # (a no-size marker beats any size), then the sort key -- input order never matters.
    _best: dict = {}
    for c in (candidates or []):
        if not isinstance(c, dict):
            continue
        sz = c.get("sizing")
        rank = (-1 if _is_marker(sz) else int(sz["shares"]), candidate_sort_key(c))
        t = str(c.get("ticker") or "").upper()
        if t not in _best or rank < _best[t][0]:
            _best[t] = (rank, c)
    cands = sorted((v[1] for v in _best.values()), key=candidate_sort_key)
    br = basis_result if isinstance(basis_result, dict) else {}
    state = br.get("state")
    reason = br.get("reason")
    if state not in ("ok", "called"):
        state = "unknown"
    elif state == "ok" and logged is None:
        state, reason = "unknown", "the trade log couldn't be read"
    elif state == "ok" and allocations is None:
        state, reason = "unknown", "today's emailed sizes couldn't be read"
    elif state == "ok" and not (_num(br.get("basis")) and _num(br.get("basis")) > 0):
        state, reason = "unknown", "net capital couldn't be determined"
    elif state == "ok" and any(
            not isinstance(v, dict) or not (_num(v.get("dollars")) or 0) > 0
            for v in allocations.values()):
        state, reason = "unknown", "an emailed size couldn't be read"
    if state == "unknown" and not reason:
        reason = "a required input was unavailable"

    per: dict = {}

    def _entry(status, shares=None, dollars=None, uncapped=None, why=None):
        return {"status": status, "shares": shares, "dollars": dollars,
                "uncapped_shares": uncapped, "reason": why}

    if state != "ok":
        for c in cands:
            tk = str(c.get("ticker") or "").upper()
            if tk in per:
                continue
            if _is_marker(c.get("sizing")):
                per[tk] = _entry(PASSTHROUGH)
            elif state == "called":
                per[tk] = _entry(BUDGET_EXHAUSTED, why=reason or CALLED_REASON)
            else:
                per[tk] = _entry(BUDGET_UNKNOWN, why=reason)
        return {"state": state, "budget": 0.0 if state == "called" else None,
                "used_logged": 0.0, "used_allocated": 0.0, "used_list": 0.0,
                "left": 0.0 if state == "called" else None, "pct_used": None,
                "reason": reason, "per_ticker": per}

    basis = float(br["basis"])
    budget = basis * DAILY_NEW_POSITION_BUDGET_PCT / 100.0
    logged_n = {str(k).upper(): v for k, v in logged.items() if _num(v)}
    used_logged = sum(float(v) for v in logged_n.values())
    alloc_n = {str(k).upper(): v for k, v in allocations.items()
               if isinstance(v, dict)}
    # A logged buy replaces its allocation -- never double-counted.
    used_alloc = sum(max(0.0, _num(v.get("dollars")) or 0.0)
                     for k, v in alloc_n.items() if k not in logged_n)
    left = max(0.0, budget - used_logged - used_alloc)
    used_list = 0.0

    for c in cands:
        tk = str(c.get("ticker") or "").upper()
        if tk in per:
            continue
        sz = c.get("sizing")
        if _is_marker(sz):
            per[tk] = _entry(PASSTHROUGH)
            continue
        f255_sh = int(sz["shares"])
        price = _num(c.get("price"))
        if price is None or price <= 0:
            per[tk] = _entry(BUDGET_UNKNOWN, why="no usable price")
            continue
        if tk in alloc_n and tk not in logged_n:
            a = alloc_n[tk]
            a_sh = _num(a.get("shares"))
            if a_sh is None:
                a_usd = _num(a.get("dollars")) or 0.0
                a_sh = math.floor(a_usd / price + 1e-9)
            sh = int(min(a_sh, f255_sh))
            if sh < 1:
                per[tk] = _entry(BUDGET_EXHAUSTED)
            else:
                per[tk] = _entry(BUDGET_RESERVED, sh, round(sh * price, 2), f255_sh)
            continue
        f255_usd = f255_sh * price  # ONE dollar figure for check and charge
        if f255_usd <= left + 1e-9:
            sh = f255_sh
        else:
            # min() is a free safeguard: floor(left/price) < f255_sh here.
            sh = min(f255_sh, int(math.floor(left / price + 1e-9)))
        if sh < 1:
            per[tk] = _entry(BUDGET_EXHAUSTED)
            continue
        spent = sh * price
        left = max(0.0, left - spent)
        used_list += spent
        per[tk] = _entry(SIZED if sh == f255_sh else BUDGET_PARTIAL,
                         sh, round(spent, 2), f255_sh)

    pct_used = (used_logged + used_alloc + used_list) / basis * 100.0
    return {"state": "ok", "budget": budget, "used_logged": used_logged,
            "used_allocated": used_alloc, "used_list": used_list, "left": left,
            "pct_used": pct_used, "per_ticker": per,
            "over_budget": used_logged + used_alloc > budget + 1e-9}


_SIZE_KEYS = ("shares", "total_cost", "port_pct", "risk_budget", "ceiling_capped",
              "uncapped_shares", "capital_capped", "capital_pct")


def apply_to_sizing(sizing, entry) -> dict:
    """New sizing dict reflecting an `allocate` per_ticker entry (input never
    mutated). Existing no-size marker convention: exhausted/unknown carry NO
    `shares` key; partial carries budget_capped + budget_uncapped_shares
    (mirroring ceiling_capped)."""
    base = dict(sizing) if isinstance(sizing, dict) else {}
    status = (entry or {}).get("status")
    if not base or status in (None, PASSTHROUGH):
        if base:
            base["sizing_version"] = BUDGET_SIZING_VERSION
        return base
    if status in (BUDGET_EXHAUSTED, BUDGET_UNKNOWN):
        out = {k: v for k, v in base.items() if k not in _SIZE_KEYS}
        out[status] = True
        if status == BUDGET_UNKNOWN:
            out["budget_reason"] = entry.get("reason")
        out["sizing_version"] = BUDGET_SIZING_VERSION
        return out
    old = base.get("shares")
    new = entry.get("shares")
    out = dict(base)
    out["sizing_version"] = BUDGET_SIZING_VERSION
    if new is None or not old:
        return out
    if new != old:
        ratio = new / old
        out["shares"] = new
        out["total_cost"] = entry.get("dollars", round(base.get("total_cost", 0) * ratio, 2))
        for k in ("port_pct", "capital_pct"):
            if _num(base.get(k)) is not None:
                out[k] = round(base[k] * ratio, 1)
    if status == BUDGET_PARTIAL:
        out["budget_capped"] = True
        out["budget_uncapped_shares"] = entry.get("uncapped_shares", old)
    elif status == BUDGET_RESERVED:
        out["budget_reserved"] = True
    return out
