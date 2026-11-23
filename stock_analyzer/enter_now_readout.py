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

Entry regime rules (docs/plans/entry-regime-check.md, C2): the A / B / C "would
hold" verdicts are computed THROUGH `entry_regime` (never through `regime_of`,
which is the older Home-style read kept for CSV back-compat). The go-live block
(`go_live_check`) applies the criteria the plan pre-registered in section 4,
before this readout was first run over the past calls. Still read-only.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import median

import numpy as np
import pandas as pd

from stock_analyzer import entry_regime, exit_advisor, market_trend
from stock_analyzer.constants import (
    ENTRY_REGIME_GO_LIVE_EARLY_HORIZON,
    ENTRY_REGIME_GO_LIVE_MARGIN_PP,
    GATE_LEDGER_FIRM_CALLS,
    GATE_LEDGER_HORIZON_TRADING_DAYS,
    GATE_LEDGER_MIN_TICKERS,
    RISK_OFF_TREND_MA,
    RISK_OFF_VIX_LEVEL,
)
from stock_analyzer.data import is_trading_day, previous_trading_day, trading_sessions_between
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
    # The entry regime rules, computed through entry_regime (plan section 3).
    ("Would hold under rule A (PRIMARY, pre-registered): S&P 500 Weakening or Falling",
     "rule_a", ("hold", "clear", UNKNOWN)),
    ("Would hold under rule B (secondary): S&P 500 anything but Rising",
     "rule_b", ("hold", "clear", UNKNOWN)),
    (f"Would hold under rule C (secondary): S&P 500 below its {RISK_OFF_TREND_MA}-day "
     f"average or VIX close >= {RISK_OFF_VIX_LEVEL:g}",
     "rule_c", ("hold", "clear", UNKNOWN)),
    ("Nasdaq trend label (EXPLORATORY, not pre-registered)", "ndx_label", _TREND_ORDER),
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


def dedupe_calls(rows: list[dict], by_rec_type: bool = False) -> list[dict]:
    """One call per (TICKER upper, rec_date); the EARLIEST row wins. With
    `by_rec_type` the key is (TICKER, rec_date, rec_type), so a name surfaced as
    two recommendation types the same day stays two calls (one per type).

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
        key = (tk, d, str(r.get("rec_type") or "")) if by_rec_type else (tk, d)
        if key not in best or order < best[key][0]:
            best[key] = (order, r)
    calls = []
    for key, (_o, r) in best.items():
        c = dict(r)
        c["ticker"] = key[0]
        c["rec_date"] = key[1]
        calls.append(c)
    calls.sort(key=lambda c: (c["rec_date"], c["ticker"], str(c.get("rec_type") or "")))
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


def forward_return_pct(ticker: str, rec_date: date, price_at_entry, horizon_trading_days: int,
                       historical_close_fn) -> float | None:
    """The stock's OWN % return over the same window `forward_alpha_at_horizon`
    uses: from the recorded entry price to the first close on/after
    `advance_trading_days(rec_date, horizon)` (looked up with the same
    `historical_close_fn(ticker, target, target + 7 days)` call). Unrounded, so
    `ret - spy_return` rounds to the alpha figure. None when the entry price,
    the forward close or the ticker is missing / non-positive; never raises.
    No clock read: the caller grades only matured calls (`grade_return`)."""
    if not ticker or rec_date is None:
        return None
    p0 = _f(price_at_entry)
    if p0 is None or p0 <= 0:
        return None
    target = advance_trading_days(rec_date, horizon_trading_days)
    try:
        fwd = historical_close_fn(ticker, target, target + timedelta(days=7))
    except Exception:
        return None
    p1 = _f(fwd)
    if p1 is None or p1 <= 0:
        return None
    return (p1 - p0) / p0 * 100.0


def grade_return(call: dict, horizon_trading_days: int, today: date, return_fn) -> float | None:
    """The call's own % return at the horizon, or None when it hasn't matured or
    can't be priced. `return_fn(ticker, rec_date, price_at_surface, horizon)` is
    injected, like `grade_call`'s `alpha_fn`."""
    if not is_matured(call["rec_date"], horizon_trading_days, today):
        return None
    return _f(return_fn(call["ticker"], call["rec_date"], _f(call.get("price_at_surface")),
                        horizon_trading_days))


def returns_by(rows: list[dict], field: str, alpha_field: str, ret_field: str,
               order: tuple[str, ...] | None = None) -> dict[str, list[float]]:
    """Per-call own returns grouped like `cells_by`, over the SAME population: a
    row counts only when its alpha AND its return are finite, so a cell's alpha N
    and return N line up."""
    cells: dict[str, list[float]] = {k: [] for k in (order or ())}
    for r in rows:
        if _f(r.get(alpha_field)) is None:
            continue
        x = _f(r.get(ret_field))
        if x is None:
            continue
        v = r.get(field)
        cells.setdefault(NOT_CHECKED if v is None else str(v), []).append(x)
    return cells


def attach_mean_return(summaries: list[dict], ret_cells: dict[str, list[float]]) -> list[dict]:
    """Add `mean_return` (mean of the stock's own % return, NOT its absolute
    value) and `n_returns` to each `summarise` row. A "building" cell stays
    withheld (None), exactly like its alpha figures."""
    out = []
    for s in summaries:
        t = dict(s)
        vals = [x for x in (_f(a) for a in ret_cells.get(s["cell"], [])) if x is not None]
        t["n_returns"] = len(vals)
        t["mean_return"] = (sum(vals) / len(vals)
                            if s["verdict"] == "measured" and vals else None)
        out.append(t)
    return out


# ── hold rate and flips ──────────────────────────────────────────────────────

def session_rule_verdicts(first: date, last: date, *, trend_fn, vix_close) -> dict:
    """{call_date: {"rule_a": ..., "rule_b": ..., "rule_c": ...}} for EVERY NYSE
    session from `first` to `last` inclusive, each read exactly as `classify_call`
    reads a call made that day: market state at `prior_session(d)`, through
    `entry_regime` (`trend_fn(prior)`, the VIX close dated that session). Gives
    hold rate and flips over every session, not only the sessions that had a call."""
    out: dict[date, dict[str, str]] = {}
    d = first
    while d <= last:
        if is_trading_day(d):
            prior = prior_session(d)
            rd = entry_regime.read(trend_fn(prior), entry_regime.vix_close_asof(vix_close, prior), prior)
            cand = entry_regime.candidates(rd)
            out[d] = {"rule_a": cand["A"]["state"], "rule_b": cand["B"]["state"],
                      "rule_c": cand["C"]["state"]}
        d += timedelta(days=1)
    return out


def hold_rate_and_flips(calls: list[dict], rule_field: str, session_verdicts: dict | None = None) -> dict:
    """How often one rule would hold, and how often its verdict flips.

    `calls` carry `rec_date` and `rule_field` ('hold' / 'clear' / 'unknown', from
    `classify_call`); the per-call hold share is always reported. With
    `session_verdicts` (from `session_rule_verdicts`) the date-level figures cover
    EVERY NYSE session in that range (basis "sessions"); without it they cover the
    distinct call dates only (basis "call_dates"; a date whose calls disagree
    counts as unknown). Unknown dates are counted in the denominator and listed
    separately; hold<->clear flips are counted across consecutive dates with
    unknown dates skipped; flips_per_20_sessions scales by the NYSE-session span
    first date -> last date (None when the span is 0)."""
    if session_verdicts is not None:
        basis = "sessions"
        verdicts = {d: v.get(rule_field, UNKNOWN) for d, v in session_verdicts.items()}
    else:
        basis = "call_dates"
        by_date: dict[date, set] = {}
        for c in calls:
            by_date.setdefault(c["rec_date"], set()).add(c.get(rule_field, UNKNOWN))
        verdicts = {d: (next(iter(v)) if len(v) == 1 else UNKNOWN) for d, v in by_date.items()}
    ordered = sorted(verdicts)
    n_calls = len(calls)
    hold_calls = sum(1 for c in calls if c.get(rule_field) == "hold")
    hold_dates = sum(1 for d in ordered if verdicts[d] == "hold")
    known = [verdicts[d] for d in ordered if verdicts[d] in ("hold", "clear")]
    flips = sum(1 for a, b in zip(known, known[1:]) if a != b)
    span = trading_sessions_between(ordered[0], ordered[-1]) if ordered else 0
    return {
        "rule_field": rule_field, "basis": basis, "n_calls": n_calls, "hold_calls": hold_calls,
        "n_dates": len(ordered), "hold_dates": hold_dates,
        "clear_dates": sum(1 for d in ordered if verdicts[d] == "clear"),
        "unknown_dates": sum(1 for d in ordered if verdicts[d] not in ("hold", "clear")),
        "hold_share_calls": hold_calls / n_calls if n_calls else None,
        "hold_share_dates": hold_dates / len(ordered) if ordered else None,
        "flips": flips, "session_span": span,
        "flips_per_20_sessions": flips / span * 20 if span > 0 else None,
    }


def format_hold_lines(stats_by_rule: dict[str, dict]) -> list[str]:
    """Printable lines for `hold_rate_and_flips` results keyed by a rule title:
    one line for the dates / sessions (with the flips), one for the calls."""
    def pct(x):
        return "-" if x is None else f"{x * 100:.1f}%"
    lines = []
    for title, st in stats_by_rule.items():
        f20 = st["flips_per_20_sessions"]
        unit = "NYSE sessions" if st.get("basis") == "sessions" else "call dates"
        lines.append(
            f"  {title}: would hold on {st['hold_dates']}/{st['n_dates']} {unit} "
            f"({pct(st['hold_share_dates'])}); unknown on {st['unknown_dates']}; "
            f"{st['flips']} hold/clear flips over {st['session_span']} sessions"
            + ("" if f20 is None else f" ({f20:.1f} per 20 sessions)"))
        lines.append(
            f"    of the calls: would hold on {st['hold_calls']}/{st['n_calls']} "
            f"({pct(st['hold_share_calls'])})")
    return lines


# ── go-live check (plan section 4, pre-registered) ───────────────────────────

MET = "MET"
NOT_MET = "NOT MET"
NOT_ENOUGH = "NOT ENOUGH DATA"


def _go_live_cell(rows: list[dict], alpha_field: str, rule_field: str, verdict: str) -> dict:
    vals, tickers = [], set()
    for r in rows:
        if r.get(rule_field) != verdict:
            continue
        a = _f(r.get(alpha_field))
        if a is None:
            continue
        vals.append(a)
        tickers.add(_norm_ticker(r.get("ticker")))
    n = len(vals)
    return {"n": n, "tickers": len(tickers),
            "beat_share": sum(1 for x in vals if x > 0) / n if n else None,
            "median_alpha": float(median(vals)) if n else None,
            "mean_alpha": sum(vals) / n if n else None}


def go_live_check(episode_rows_30: list[dict], episode_rows_10: list[dict], *,
                  field_30: str = f"alpha_{GATE_LEDGER_HORIZON_TRADING_DAYS}",
                  field_10: str = f"alpha_{ENTRY_REGIME_GO_LIVE_EARLY_HORIZON}",
                  rule_field: str = "rule_a",
                  min_episodes: int = GATE_LEDGER_FIRM_CALLS,
                  min_tickers: int = GATE_LEDGER_MIN_TICKERS,
                  margin_pp: float = ENTRY_REGIME_GO_LIVE_MARGIN_PP) -> dict:
    """The plan's section 4 go-live criteria, judged on one rule per EPISODE.

    `episode_rows_*` are each episode's FIRST call (carrying `rule_field`, the
    ticker and the alpha fields). Cells: "hold" vs "pass" (verdict "clear");
    unknown-verdict and unmatured / unpriced episodes are in neither. Criteria:
      1. each 30-session cell has >= `min_episodes` episodes and >= `min_tickers`
         distinct tickers;
      2. hold beat-SPY share <= pass share - `margin_pp` percentage points;
      3. hold median alpha < 0;
      4. at the early horizon (`ENTRY_REGIME_GO_LIVE_EARLY_HORIZON` sessions) the
         hold cell's beat-SPY share is below the pass cell's, no margin (plan
         section 4, clarification 2026-11-22); each 10-session cell needs the same
         episode / ticker floors as criterion 1, else NOT ENOUGH DATA. The
         10-session cells hold every episode matured at 10 sessions, including
         those not yet matured at 30.
    Each is MET / NOT MET / NOT ENOUGH DATA; criteria 2 and 3 are NOT ENOUGH DATA
    until criterion 1 is MET. overall is "ALL MET" only when all four are MET.
    A cell below its floor reports `figures_shown` False so a printer withholds it.
    """
    hold = _go_live_cell(episode_rows_30, field_30, rule_field, "hold")
    pas = _go_live_cell(episode_rows_30, field_30, rule_field, "clear")
    hold10 = _go_live_cell(episode_rows_10, field_10, rule_field, "hold")
    pas10 = _go_live_cell(episode_rows_10, field_10, rule_field, "clear")
    unknown_30 = sum(1 for r in episode_rows_30
                     if r.get(rule_field) not in ("hold", "clear")
                     and _f(r.get(field_30)) is not None)
    for cell in (hold, pas, hold10, pas10):
        cell["figures_shown"] = cell["n"] >= min_episodes and cell["tickers"] >= min_tickers

    c1 = MET if (hold["figures_shown"] and pas["figures_shown"]) else NOT_ENOUGH
    gap_pp = None
    if c1 == MET:
        gap_pp = (pas["beat_share"] - hold["beat_share"]) * 100.0
        c2 = MET if gap_pp >= margin_pp - 1e-9 else NOT_MET
        c3 = MET if hold["median_alpha"] < 0 else NOT_MET
    else:
        c2 = c3 = NOT_ENOUGH
    diff10_pp = None
    if hold10["figures_shown"] and pas10["figures_shown"]:
        diff10_pp = (hold10["beat_share"] - pas10["beat_share"]) * 100.0
        c4 = MET if diff10_pp < 0 else NOT_MET
    else:
        c4 = NOT_ENOUGH

    criteria = [
        {"id": 1, "status": c1, "text":
            f"each cell has >= {min_episodes} episodes and >= {min_tickers} distinct tickers"},
        {"id": 2, "status": c2, "gap_pp": gap_pp, "text":
            f"hold beat-SPY share is at least {margin_pp:g} pp below the pass share"},
        {"id": 3, "status": c3, "text": "hold cell median alpha is below 0"},
        {"id": 4, "status": c4, "diff10_pp": diff10_pp, "text":
            "10-session read: hold beat-SPY share is below the pass share (same sign)"},
    ]
    all_met = all(c["status"] == MET for c in criteria)
    return {"rule_field": rule_field, "hold": hold, "pass": pas, "hold_10": hold10,
            "pass_10": pas10, "unknown_30": unknown_30, "criteria": criteria,
            "margin_pp": margin_pp, "min_episodes": min_episodes, "min_tickers": min_tickers,
            "overall": "ALL MET" if all_met else NOT_MET,
            "data_sufficient": all(c["status"] != NOT_ENOUGH for c in criteria)}


def go_live_population_ok(episode_gap: int, since) -> bool:
    """True only for the pre-registered population: every logged call (no
    `--since`) and the default episode gap. Anything else gets no go-live verdict
    (plan section 4)."""
    return episode_gap == EPISODE_GAP_SESSIONS and not since


GO_LIVE_SKIPPED_LINE = ("go-live block skipped: non-default --episode-gap / --since "
                        "(not the pre-registered population)")


def format_go_live_lines(res: dict) -> list[str]:
    """Printable go-live block. Cells below the floors show only N and tickers."""
    def cell_line(name: str, c: dict, indent: str = "  ") -> str:
        head = f"{indent}{name:<18} episodes {c['n']:<4} tickers {c['tickers']:<3}"
        if not c["figures_shown"]:
            return f"{head} below the floor, figures withheld"
        return (f"{head} beat SPY {c['beat_share'] * 100:5.1f}%   "
                f"median alpha {c['median_alpha']:+.2f} pp")
    lines = [
        "GO-LIVE CHECK, rule A, per episode, 30-session horizon "
        "(criteria pre-registered in docs/plans/entry-regime-check.md section 4)",
        cell_line("A would hold", res["hold"]),
        cell_line("A would pass", res["pass"]),
        f"  (episodes with an unknown verdict, left out of both cells: {res['unknown_30']})",
    ]
    for c in res["criteria"]:
        extra = ""
        if c["id"] == 2 and c.get("gap_pp") is not None:
            extra = f" (gap {c['gap_pp']:+.1f} pp, needs >= {res['margin_pp']:g})"
        if c["id"] == 4 and c.get("diff10_pp") is not None:
            extra = f" (hold {'below' if c['diff10_pp'] < 0 else 'not below'} pass)"
        lines.append(f"  ({c['id']}) {c['text']}: {c['status']}{extra}")
        if c["id"] == 4:
            lines.append("      (4) reads: 10-session hold beat-SPY share below the pass share; "
                         "same floors; see plan §4 clarification 2026-11-22")
            lines.append(cell_line("A would hold (10)", res["hold_10"], "      "))
            lines.append(cell_line("A would pass (10)", res["pass_10"], "      "))
    lines.append(f"  OVERALL: {res['overall']}"
                 + ("" if res["data_sufficient"] else " (not enough data to judge yet)"))
    return lines


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
    already_held ('yes' / 'no' / 'unknown'), and the entry regime fields: rule_a /
    rule_b / rule_c ('hold' / 'clear' / 'unknown', computed through
    `entry_regime` from the trend result and the VIX close dated EXACTLY the prior
    session), entry_regime_read_state, vix_prior_close, ndx_label (EXPLORATORY).
    `trend_fn(as_of)` returns `market_trend.compute_trend` for that session (the
    script caches it per date) or None.
    """
    c = dict(call)
    prior = prior_session(call["rec_date"])
    c["prior_session"] = prior
    reg = regime_of(spy_close, vix_close, prior)
    c["regime_armed"] = UNKNOWN if reg["armed"] is None else ("yes" if reg["armed"] else "no")
    c["regime_legs"] = "; ".join(reg["legs"])
    c["regime_state"] = reg["state"]
    trend = trend_fn(prior)
    tl = trend_labels(trend, call["ticker"], call.get("sector"))
    rd = entry_regime.read(trend, entry_regime.vix_close_asof(vix_close, prior), prior)
    cand = entry_regime.candidates(rd)
    c["rule_a"], c["rule_b"], c["rule_c"] = (cand[k]["state"] for k in ("A", "B", "C"))
    c["entry_regime_read_state"] = rd["read_state"]
    c["vix_prior_close"] = rd["vix_close"]
    c["ndx_label"] = label_key(rd["ndx_label"])
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
