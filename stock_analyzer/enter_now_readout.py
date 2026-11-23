"""Watchlist ENTER_NOW base-rate readout, split by market state at call time.

READ-ONLY DIAGNOSTIC. Nothing here is wired into the app, changes a decision or
writes to the DB. It answers one question before any policy change: of the
matured 📋 Watchlist ENTER_NOW calls, how many beat SPY, and does that differ by
the market state the app could have known when it made the call?

Pre-registered (docs/queue.md, the ENTER_NOW base-rate line): the headline is
"of N matured calls, X% beat SPY" at `GATE_LEDGER_HORIZON_TRADING_DAYS`, with
`GATE_LEDGER_MIN_CALLS` as the floor below which a cell reads "building" (no
verdict). NEVER split by composite score band: the composite was shown not to
rank forward alpha (A2), and a band split would re-imply that it does. Splits by
market state are allowed and are the whole point (`SPLITS`).

Pure: no streamlit, no network, no DB, no clock reads (`today` is passed in).
Every cut-off comes from `constants.py` or from the functions it calls:
  * risk-off regime  -> `exit_advisor.risk_off_regime` (SPY below its
    `RISK_OFF_TREND_MA`-day mean, or VIX >= `RISK_OFF_VIX_LEVEL`);
  * trend labels     -> `market_trend.compute_trend`;
  * day-move tone    -> `entry_eligibility.classify_tone`.
The one number defined here, `EPISODE_GAP_SESSIONS`, is a readout GROUPING choice
(not policy, not a gate) and is only used to fold a name called several days
running into one episode as a robustness view.

Market state is read at `prior_session(rec_date)`: the last NYSE session strictly
before the call day, i.e. what the app could know before a 09:45 ET call.
"""
from __future__ import annotations

import math
from datetime import date
from statistics import median

import numpy as np
import pandas as pd

from stock_analyzer import exit_advisor, market_trend
from stock_analyzer.constants import RISK_OFF_TREND_MA, RISK_OFF_VIX_LEVEL
from stock_analyzer.data import previous_trading_day, trading_sessions_between
from stock_analyzer.entry_eligibility import classify_tone
from stock_analyzer.portfolio import resolve_sector
from stock_analyzer.predictive_analytics import advance_trading_days

# Readout grouping choice, NOT policy and NOT in constants.py: two ENTER_NOW calls
# on the same ticker at most this many NYSE sessions apart belong to one episode.
EPISODE_GAP_SESSIONS = 3

_SPX_SYMBOL = "^GSPC"          # pinned to market_trend.trend_symbols()[0] by a test
NOT_CHECKED = "not checked"
UNKNOWN = "unknown"

_TREND_ORDER = ("falling", "weakening", "steady", "rising", NOT_CHECKED)

# (title, per-call field, display order). Market-state splits only. There is
# deliberately no composite / score field here; a test pins that.
SPLITS = (
    (f"Risk-off regime armed at the prior close "
     f"(SPY < {RISK_OFF_TREND_MA}-day mean or VIX >= {RISK_OFF_VIX_LEVEL:g})",
     "regime_armed", ("yes", "no", UNKNOWN)),
    ("S&P 500 trend label at the prior close", "spx_label", _TREND_ORDER),
    ("Candidate's sector-fund trend label at the prior close", "sector_label", _TREND_ORDER),
    ("Day-move tone, call-day S&P close-to-close (APPROXIMATION of the 09:45 read)",
     "day_tone_approx", ("bear", "flat", "bull", UNKNOWN)),
    ("Already held at the call", "already_held", ("yes", "no", UNKNOWN)),
)


# ── small helpers ────────────────────────────────────────────────────────────

def _f(v) -> float | None:
    """Finite float or None (NaN / inf / non-numeric / bool-free of surprises)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _to_date(v) -> date | None:
    """Best-effort date coerce (ISO string, date, datetime, Timestamp). NaT/None -> None."""
    if v is None:
        return None
    if not isinstance(v, str):
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
    try:
        return v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _norm_ticker(v) -> str:
    return str(v or "").strip().upper()


def label_key(label) -> str:
    """A trend label for display / grouping: None -> 'not checked'."""
    return label if isinstance(label, str) and label else NOT_CHECKED


def _close_map(series) -> dict[date, float]:
    """{date: close} from a Series indexed by dates / Timestamps. NaN dropped,
    duplicate dates keep the last bar."""
    if series is None or len(series) == 0:
        return {}
    out: dict[date, float] = {}
    for idx, v in series.items():
        d = _to_date(idx)
        x = _f(v)
        if d is not None and x is not None:
            out[d] = x
    return out


# ── time context ─────────────────────────────────────────────────────────────

def prior_session(d: date) -> date:
    """Last NYSE session strictly before `d` (weekends and holidays skipped; `d`
    itself need not be a session). The market state the app could know before a
    09:45 ET call on `d`."""
    return previous_trading_day(d)


def is_matured(rec_date: date, horizon_trading_days: int, today: date) -> bool:
    """True once rec_date + horizon NYSE sessions is on or before `today`."""
    return advance_trading_days(rec_date, horizon_trading_days) <= today


# ── calls and episodes ───────────────────────────────────────────────────────

def _surfaced_key(row: dict) -> float:
    """Epoch seconds of `surfaced_at` (UTC), or +inf when absent / unparseable."""
    v = row.get("surfaced_at")
    if v is None:
        return math.inf
    try:
        ts = pd.Timestamp(v)
        if pd.isna(ts):
            return math.inf
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        return ts.timestamp()
    except Exception:
        return math.inf


def _id_key(row: dict) -> float:
    x = _f(row.get("id"))
    return x if x is not None else math.inf


def dedupe_calls(rows: list[dict]) -> list[dict]:
    """One call per (TICKER upper, rec_date); the EARLIEST row wins.

    Earliest = lowest `surfaced_at`, then lowest `id`, then first in input order
    (all three only when present). Rows with no ticker or no parseable rec_date
    are dropped (the caller can compare len(rows) to len(result)). Returns new
    dicts (input untouched) with `ticker` upper-cased and `rec_date` a date,
    sorted by (rec_date, ticker).
    """
    best: dict[tuple, tuple[tuple, dict]] = {}
    for idx, r in enumerate(rows):
        tk = _norm_ticker(r.get("ticker"))
        d = _to_date(r.get("rec_date"))
        if not tk or d is None:
            continue
        order = (_surfaced_key(r), _id_key(r), idx)
        key = (tk, d)
        if key not in best or order < best[key][0]:
            best[key] = (order, r)
    calls = []
    for (tk, d), (_o, r) in best.items():
        c = dict(r)
        c["ticker"] = tk
        c["rec_date"] = d
        calls.append(c)
    calls.sort(key=lambda c: (c["rec_date"], c["ticker"]))
    return calls


def group_episodes(calls: list[dict], gap_sessions: int = EPISODE_GAP_SESSIONS) -> list[dict]:
    """Fold consecutive calls on one ticker into episodes, graded from the FIRST call.

    Two neighbouring calls (by rec_date) on the same ticker are in the same
    episode when they are at most `gap_sessions` NYSE sessions apart
    (`data.trading_sessions_between`: Fri -> Mon is 1; a holiday is not a
    session). The gap is measured from the PREVIOUS call, so a name called every
    other day for a month is one episode. Robustness view against one name being
    counted many days running; it is a readout grouping, not a decision.

    `calls` need `ticker` (upper-case) and `rec_date` (date), e.g. from
    `dedupe_calls`. Returns episodes sorted by first date, each
    {episode_id, ticker, first (the SAME dict object as in `calls`), calls,
    n_calls, first_date, last_date}. Inputs are not mutated.
    """
    by_tk: dict[str, list[dict]] = {}
    for c in calls:
        by_tk.setdefault(c["ticker"], []).append(c)

    def _episode(tk: str, cs: list[dict]) -> dict:
        first = cs[0]
        return {"episode_id": f"{tk}@{first['rec_date'].isoformat()}", "ticker": tk,
                "first": first, "calls": list(cs), "n_calls": len(cs),
                "first_date": first["rec_date"], "last_date": cs[-1]["rec_date"]}

    eps: list[dict] = []
    for tk, cs in by_tk.items():
        cs = sorted(cs, key=lambda c: c["rec_date"])
        cur = [cs[0]]
        for c in cs[1:]:
            if trading_sessions_between(cur[-1]["rec_date"], c["rec_date"]) <= gap_sessions:
                cur.append(c)
            else:
                eps.append(_episode(tk, cur))
                cur = [c]
        eps.append(_episode(tk, cur))
    eps.sort(key=lambda e: (e["first_date"], e["ticker"]))
    return eps


# ── summaries ────────────────────────────────────────────────────────────────

def summarise(cells: dict[str, list[float]], min_calls: int) -> list[dict]:
    """Per cell: N, share of calls with alpha > 0 (beat SPY), mean and median alpha.

    Non-finite alphas are ignored. A cell with N < `min_calls` is verdict
    "building" and carries NO beat share / mean / median (None), so no figure
    can be read off a thin cell. N >= min_calls is "measured". Order of `cells`
    is kept. Alphas are percentage points vs SPY, as `forward_alpha_at_horizon`
    returns them.
    """
    out = []
    for name, vals in cells.items():
        v = [x for x in (_f(a) for a in vals) if x is not None]
        n = len(v)
        if n < min_calls:
            out.append({"cell": name, "n": n, "beat_share": None, "mean_alpha": None,
                        "median_alpha": None, "verdict": "building"})
            continue
        out.append({"cell": name, "n": n,
                    "beat_share": sum(1 for x in v if x > 0) / n,
                    "mean_alpha": sum(v) / n, "median_alpha": float(median(v)),
                    "verdict": "measured"})
    return out


def cells_by(rows: list[dict], field: str, alpha_field: str,
             order: tuple[str, ...] | None = None) -> dict[str, list[float]]:
    """Group per-call alphas by a market-state field. Rows whose alpha is missing
    (unmatured / unpriced) are left out. Keys in `order` come first, present even
    when empty (they then read N=0 "building"); any other key follows, sorted."""
    cells: dict[str, list[float]] = {k: [] for k in (order or ())}
    for r in rows:
        a = _f(r.get(alpha_field))
        if a is None:
            continue
        v = r.get(field)
        cells.setdefault(NOT_CHECKED if v is None else str(v), []).append(a)
    head = [k for k in (order or ()) if k in cells]
    tail = sorted(k for k in cells if k not in head)
    return {k: cells[k] for k in head + tail}


def grade_call(call: dict, horizon_trading_days: int, today: date, alpha_fn) -> tuple[str, float | None]:
    """('not_matured' | 'unpriced' | 'graded', alpha). `alpha_fn(ticker, rec_date,
    price_at_surface, horizon)` is injected (the script passes
    `predictive_analytics.forward_alpha_at_horizon`). An unmatured call is never
    priced, so it can't be confused with a fetch failure."""
    if not is_matured(call["rec_date"], horizon_trading_days, today):
        return "not_matured", None
    alpha = alpha_fn(call["ticker"], call["rec_date"], _f(call.get("price_at_surface")),
                     horizon_trading_days)
    alpha = _f(alpha)
    return ("graded", alpha) if alpha is not None else ("unpriced", None)


# ── market state at call time ────────────────────────────────────────────────

def regime_of(spy_close: pd.Series, vix_close: pd.Series | None, as_of: date) -> dict:
    """The risk-off regime as of `as_of`, using the app's own measure.

    SPY closes up to and including `as_of` and the last VIX close on or before it
    go through `exit_advisor.risk_off_regime` with `RISK_OFF_TREND_MA` and
    `RISK_OFF_VIX_LEVEL`. Returns {armed: bool | None, legs: [reason, ...],
    state}. armed is None (couldn't measure) unless `exit_advisor.risk_off_state`
    says "measured"; state is that function's verdict.
    """
    spy_map = {d: x for d, x in _close_map(spy_close).items() if d <= as_of}
    spy_df = None
    if spy_map:
        spy_df = pd.DataFrame({"Close": pd.Series(spy_map).sort_index()})
    vix_map = {d: x for d, x in _close_map(vix_close).items() if d <= as_of}
    vix = vix_map[max(vix_map)] if vix_map else None
    state = exit_advisor.risk_off_state(spy_df, vix, trend_ma=RISK_OFF_TREND_MA)
    if state != "measured":
        return {"armed": None, "legs": [], "state": state}
    armed, legs = exit_advisor.risk_off_regime(
        spy_df, vix, trend_ma=RISK_OFF_TREND_MA, vix_threshold=RISK_OFF_VIX_LEVEL)
    return {"armed": bool(armed), "legs": list(legs), "state": state}


def trend_labels(trend_result: dict | None, ticker: str, row_sector) -> dict:
    """S&P 500 trend label / drawdown word and the candidate's sector-fund label.

    `trend_result` is `market_trend.compute_trend(...)` (None = couldn't read, so
    every label is None). The call's sector is resolved with
    `portfolio.resolve_sector(ticker, row_sector)`, then matched to the fund row
    whose `app_sectors` contains it. No fund row (an 'Other' / unmapped / SPY-mapped
    sector) or a fund row that itself wasn't checked gives sector_label None.
    """
    out = {"spx_label": None, "spx_drawdown": None, "sector": None,
           "sector_fund": None, "sector_label": None}
    fb = row_sector if isinstance(row_sector, str) else ""
    sector = resolve_sector(ticker, fb)
    out["sector"] = sector
    if not trend_result:
        return out
    for r in trend_result["indices"]:
        if r.get("symbol") == _SPX_SYMBOL:
            out["spx_label"] = r.get("label")
            out["spx_drawdown"] = r.get("drawdown_word")
    for r in trend_result["sectors"]:
        if sector in r["app_sectors"]:
            out["sector_fund"] = r.get("symbol")
            out["sector_label"] = r.get("label")
            break
    return out


def day_move_pct(spx_close: pd.Series, d: date) -> float | None:
    """Close-to-close % change of the S&P 500 on session `d` vs the prior NYSE
    session. None when either close is missing (a weekend / holiday `d`, or a
    bar not downloaded yet)."""
    m = _close_map(spx_close)
    if d not in m:
        return None
    prev = previous_trading_day(d)
    if prev not in m or m[prev] <= 0:
        return None
    return (m[d] / m[prev] - 1.0) * 100.0


def day_tone_approx(spx_close: pd.Series, d: date) -> dict:
    """{pct, tone} where tone is `classify_tone(call-day close-to-close %)`
    (bull / flat / bear) or 'unknown'. An APPROXIMATION: the app's 09:45 read was
    the S&P move so far that morning, not the full-day close."""
    pct = day_move_pct(spx_close, d)
    return {"pct": pct, "tone": classify_tone(pct) if pct is not None else UNKNOWN}


def _held_label(v) -> str:
    if isinstance(v, (bool, np.bool_)):
        return "yes" if bool(v) else "no"
    return UNKNOWN


def classify_call(call: dict, *, spy_close, spx_close, vix_close, trend_fn) -> dict:
    """A copy of `call` plus its market state at call time.

    Added keys: prior_session, regime_armed ('yes' / 'no' / 'unknown'),
    regime_legs, regime_state, spx_label, spx_drawdown, sector, sector_fund,
    sector_label ('not checked' when None), day_move_pct, day_tone_approx,
    already_held ('yes' / 'no' / 'unknown'). `trend_fn(as_of)` returns
    `market_trend.compute_trend` for that session (the script caches it per date)
    or None.
    """
    c = dict(call)
    prior = prior_session(call["rec_date"])
    c["prior_session"] = prior
    reg = regime_of(spy_close, vix_close, prior)
    c["regime_armed"] = UNKNOWN if reg["armed"] is None else ("yes" if reg["armed"] else "no")
    c["regime_legs"] = "; ".join(reg["legs"])
    c["regime_state"] = reg["state"]
    tl = trend_labels(trend_fn(prior), call["ticker"], call.get("sector"))
    c["spx_label"] = label_key(tl["spx_label"])
    c["spx_drawdown"] = tl["spx_drawdown"] or ""
    c["sector"] = tl["sector"]
    c["sector_fund"] = tl["sector_fund"] or ""
    c["sector_label"] = label_key(tl["sector_label"])
    tone = day_tone_approx(spx_close, call["rec_date"])
    c["day_move_pct"] = tone["pct"]
    c["day_tone_approx"] = tone["tone"]
    c["already_held"] = _held_label(call.get("already_held"))
    return c
