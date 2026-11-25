#!/usr/bin/env python3
"""Dry-run: what would the held-position trailing stop (finding #26) have said?

Chunk C2 of docs/plans/held-trailing-stop.md. Diagnostic only: nothing is wired,
nothing is enforced. It exists so the owner can rule on U1 (trail multiple 2.0 /
2.5 / 3.0 / 3.5) and U2 (start variant) with evidence, and see day 1.

Side effects (verified by reading the code paths, not by running them):
  * `db.set_readonly(True)`: no user-data writes. Reads only: holdings, trades,
    manual_stops.
  * FMP_API_KEY and ANTHROPIC_API_KEY are dropped from the environment before any
    app import, ONLY when run as a script. Without the FMP key,
    `data.fetch_ticker_bundle` can't refill sparse `.info` from FMP, so nothing
    reaches `db.increment_daily_quota("fmp")` (an rpc upsert that set_readonly
    does not block; fmp_provider raises "FMP_API_KEY not set" before counting).
    No LLM calls. (If the key lives in a Streamlit secrets file rather than the
    environment, the pop doesn't remove it; the owner runs from a shell.)
  * Remaining side effects are outbound only: Yahoo history calls, Finnhub /
    Yahoo calls made by `fetch_ticker_bundle` and `fetch_live_prices` (they spend
    the shell's own Finnhub rate budget), yfinance's local tz-cache files, and
    in-process api_health counters. Nothing is cached to the DB.
Requires SUPABASE_URL / SUPABASE_KEY in the environment (run from a normal shell).

Variants (the C1 engine, `stock_analyzer.held_stop.compute_held_stop`):
  V0   today's engine: risk.atr_stop_loss on the 6-month bundle frame INCLUDING
       today's partial bar, then portfolio.protective_stop(price, avg_cost,
       atr_stop), then the manual override via portfolio.manual_stop_wins.
  a    full retroactive: entry anchor + trailing + sticky ratchet from each buy.
  b    from `--trail-start` only: the entry anchor is suppressed.
  c    anchor retroactive, trailing / ratchet from `--trail-start`.
Each is swept over the trail multiples in `--mults` (default 2.0 2.5 3.0 3.5).

Price basis (one price for V0 and every variant): the live price with `--live`,
else the latest Close of the RAW long frame INCLUDING today's bar. "Last close"
(last COMPLETED session) is shown as an info column. A breach is the app's own
rule, `act_today_precedence.is_stop_breached` (gap rounded to
GAP_TO_STOP_ROUND_DECIMALS <= 0), so +0.04% above the stop counts as breached.

Modes:
  --today (default)  compact table, one row per holding for `--variant` (default
                     c): V0 stop / gap / type, then per multiple the stop, its
                     delta vs V0 (percent of price; negative = LOOSER than today)
                     and flags. `--detail` prints the full per-cell blocks; the
                     CSV carries everything. Flags: NEWLY_BREACHED_CLOSE,
                     NEWLY_BREACHED_LOW (the last COMPLETED session's Low vs the
                     stop as of that session; with --live, today's partial-bar Low
                     vs the stop as of today), CROSSES_AT_RISK /
                     CROSSES_APPROACHING / LOSES_ADD_ROOM (vs V0's band),
                     MANUAL_NOW_LOSES, LOOSER_THAN_V0, RATCHET_DROPPED (V0 had a
                     ratchet tier, the cell has no ratchet component). Summary per
                     variant x multiple: tighter / looser / equal counts, median
                     delta, ratchet-dropped, breach / band counts, basis counts.
                     `--as-of` other than today is refused (holdings, average
                     cost and manual stops are as of now).
  --replay           over ALL closed round trips: for each as-of session from
                     entry+1 to the exit, the held stop as of that session; first
                     fire = first session whose Close (separately Low) breaches it
                     (same rule). Reported: sessions before the exit, return at
                     fire vs at actual exit, 5- / 10-session forward return,
                     return if sold at the NEXT session's Open (the app says "sell
                     at next open"), whipsaw (a Close back above the FIRE STOP
                     within 10 sessions), and, for losing trips, how the stop's
                     first fire compares with the deterioration ladder's first
                     TRIM / EXIT (stop first / ladder first / same session / stop
                     only / ladder only / neither). Summaries are split winners vs
                     losers, with n beside every mean / median. Replay runs
                     variant (a) unless `--replay-variants` says otherwise; for b
                     with no explicit `--trail-start` each trip's trail_start is
                     its entry date (isolates the anchor's contribution) and c is
                     not computed (c == a when trail_start <= entry).
  --floor            (C3c-2, docs/plans/held-stop-floor-c3c-spec.md section 10 item 2)
                     the persisted-floor dry run, per holding, using the PRODUCTION
                     policy (held_stop_wiring._policy: trail_start = HELD_TRAIL_START;
                     an explicit --trail-start overrides). Replays the EOD writer over
                     the last `--floor-sessions` sessions (default 20): each session the
                     full-basis result on the long frame goes through
                     held_stop_floor.apply_floor with the rows saved so far, then
                     rows_to_write; the rows live in memory only (the table stands in).
                     Then today, on the long frame vs the bundle-only fallback frame
                     (6-month bundle, basis forced to partial like the driver), each
                     unfloored and floored with the replayed rows. Reports binds on the
                     full frame (full-day delta = saved - computed, replay and today)
                     and on the fallback; NEWLY_BREACHED_ON_FALLBACK (floored fallback
                     stop breached by the price, unfloored not; the app's
                     is_stop_breached); the close-check drift of the REF session and
                     the BIND session reported separately against
                     HELD_STOP_FLOOR_CLOSE_TOL_PCT (read at call time) with the bind
                     bar's age; the floor state on each frame; U8 = names whose long
                     frame is permanently partial (never get a floor). `--bundle-start
                     YYYY-MM-DD` truncates the fallback frame to start on that date
                     (simulates a later rollover). Excludes --today / --replay. CSV =
                     one "today" row per holding plus one "replay" row per replayed
                     session. Read-only: no DB write, no LLM, no new outbound call.
  --carry            (SlidingStop C2b, docs/plans/sliding-stop.md section 7 item 4) the
                     carried-stop dry run, per holding, PRODUCTION policy. Today no
                     holding is on a prior_session stop, so replaying the real basis would
                     show the carry idle: instead, over the last `--carry-sessions`
                     sessions (default 20) each holding is replayed under a FORCED
                     scenario "the journal stops dating this position from session k", for
                     k = the first replayed session, the middle one and 5 sessions before
                     the end. Sessions < k: the production result on its real basis
                     (floored, then held_stop_carry.apply_carry) and the EOD writer
                     (rows_to_write) saves rows in memory. Sessions >= k: what
                     held_stop_wiring._one's non-ok branch gives on the same frame (the
                     UNCARRIED prior_session stop), then apply_carry with the saved row,
                     then rows_to_write advances it, under three U2 variants
                     (journal_readable, journal_down, journal_down_at_read: whether the
                     reader / the EOD writer can read the journal). Per session it reports
                     the TRUE full-history stop (real episode, floored, not carried), the
                     uncarried stop, the carried stop, the carry state / lapse reason and
                     the breach of each against that session's own Close (the app's
                     is_stop_breached). Flags: FALSE_BREACH (carried breached, true not;
                     the hard gate, 0 required, counted when the true basis is full),
                     CARRY_ABOVE_FULL (carried > true + 1 cent), SAVED_A_SLIDE (true
                     breached, uncarried not, carried breached), MISSED (true breached,
                     carried not), BREACH_NO_FULL_TRUTH (informational). The summary ends
                     with "GATE: PASS (0 false breaches)" or "GATE: FAIL (N false
                     breaches: ...)". `--detail` prints every replayed session, otherwise
                     only flagged / lapsed ones; the CSV carries every session. Excludes
                     --today / --replay / --floor. Read-only: no DB write, no LLM; the
                     only outbound call is the long Yahoo history per holding.
  --csv PATH         row-level data.   --tickers A,B  --since YYYY-MM-DD

Usage:
    python scripts/held_stop_dryrun.py --today --live
    python scripts/held_stop_dryrun.py --floor --bundle-start 2026-06-24 --csv floor.csv
    python scripts/held_stop_dryrun.py --carry --carry-sessions 20 --csv carry.csv
    python scripts/held_stop_dryrun.py --replay --csv replay.csv
    python scripts/held_stop_dryrun.py --today --trail-start 2026-10-15 --mults 3.0,3.5 --detail

Loaders (read-only): db.load_holdings_or_none (exact fractional shares),
db.load_trades_or_none (None = unreadable), db.load_manual_stops_or_none (None =
unreadable: the header prints "manual stops: UNREADABLE" and the run continues with {}), data.fetch_ticker_bundle(t, "6mo")["history"] for V0 (the
app's bundle path: yfinance default auto_adjust=True, dividend-ADJUSTED, today's
partial bar included), data.fetch_live_prices (--live), and
yf.Ticker(t).history(period=..., auto_adjust=False) for the long frame.

Known approximations (marked, not guessed):
  * yfinance 1.3.0 `Ticker.history` defaults to auto_adjust=True, which replaces
    OHLC with Adj-scaled values (utils.auto_adjust, read in the installed source).
    With auto_adjust=False the OHLC stay split-adjusted but dividend-UNadjusted
    (the plan's C3 must-have). The long frame uses False; V0's frame is the app's
    adjusted bundle. The live Yahoo response can't be verified offline: spot-check
    one ticker's Close against the broker's quote. `div_since_E_pct` (dividends
    since the episode start / since max(E, trail_start), as % of price) sizes the
    adjustment gap per holding.
  * Period per ticker = `held_stop.history_period_needed` (re-checked once
    against the fetched frame, in ET dates); no episode start -> "1y".
  * Replay avg cost as of a session = share-weighted mean of the episode's buys
    dated before it. Return at exit = weighted sell price vs final average cost.
    Trips with a SPLIT row, or whose sells exceed their buys (journal starts
    mid-position), are skipped and counted.
  * The ladder comparison (`exit_ladder_replay.replay_round_trip`) is its own
    single-lot path on dividend-adjusted frames: multi-lot trips are fed their
    final average cost and total bought shares as one lot. Replay = CLOSED trips
    only (survivorship); the owner's open winners are invisible to it.
  * `NYSE_HOLIDAYS` starts 2026-01-01, so for earlier as-of sessions `held_stop`
    may call a session after a holiday "partial". Those sessions are excluded
    from the replay basis counts; the stop VALUE is unaffected.
  * (a) replay is the steady state of (c) for positions opened after the deploy
    date (trail_start <= entry makes c == a).
"""
from __future__ import annotations

import os

if __name__ == "__main__":
    os.environ.pop("ANTHROPIC_API_KEY", None)   # no LLM spend, before any app import
    os.environ.pop("FMP_API_KEY", None)         # no FMP quota spend / rpc write via the bundle

import argparse  # noqa: E402
import csv  # noqa: E402
import statistics  # noqa: E402
import sys  # noqa: E402
from collections import Counter  # noqa: E402
from datetime import date  # noqa: E402
from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling scripts (exit_ladder_replay)

from stock_analyzer import constants as _C  # noqa: E402
from stock_analyzer import held_stop as hs  # noqa: E402
from stock_analyzer import held_stop_carry as hsc  # noqa: E402
from stock_analyzer import held_stop_floor as hsf  # noqa: E402
from stock_analyzer import held_stop_wiring as hsw  # noqa: E402
from stock_analyzer import portfolio, risk  # noqa: E402
from stock_analyzer.act_today_precedence import is_stop_breached  # noqa: E402
from stock_analyzer.constants import (  # noqa: E402
    ADD_WINNER_MIN_GAP_PCT, APPROACHING_STOP_GAP_PCT, ATR_STOP_MULT,
    GAP_TO_STOP_ROUND_DECIMALS, HELD_STOP_FILL_TOL_PCT, HELD_STOP_MIN_BARS,
    HELD_STOP_SHARES_TOL, HELD_STOP_WARMUP_BARS,
    NYSE_HOLIDAYS, POSITION_AT_RISK_GAP_PCT, STOP_RATCHET_LEVELS,
)

WARMUP_BARS = HELD_STOP_WARMUP_BARS
MIN_BARS = HELD_STOP_MIN_BARS
FILL_TOL_PCT = HELD_STOP_FILL_TOL_PCT
SHARES_TOL = HELD_STOP_SHARES_TOL
INITIAL_MULT = ATR_STOP_MULT
RATCHET_LEVELS = STOP_RATCHET_LEVELS

DEFAULT_MULTS = (2.0, 2.5, 3.0, 3.5)   # the U1 sweep from the plan, not a gate
VARIANTS = ("a", "b", "c")
FORWARD_SESSIONS = (5, 10)             # replay readout horizons, not a gate
WHIPSAW_SESSIONS = 10
# First year NYSE_HOLIDAYS covers, derived (not a literal).
_HOLIDAY_CAL_START = date.fromisoformat(min(NYSE_HOLIDAYS))
# protective_stop's label when no ratchet tier applies (portfolio.py).
_NO_RATCHET_LABELS = ("ATR Stop", "Stop Unavailable")


def _f(v):
    try:
        x = float(v)
        return x if x == x and x not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _gap(price, stop):
    if price is None or stop is None or not price:
        return None
    return round((price - stop) / price * 100, GAP_TO_STOP_ROUND_DECIMALS)


def _held_kwargs(mult: float, trail_start) -> dict:
    return dict(initial_mult=INITIAL_MULT, trail_mult=float(mult), trail_start=trail_start,
                ratchet_levels=RATCHET_LEVELS, warmup_bars=WARMUP_BARS,
                min_bars=MIN_BARS, fill_tol_pct=FILL_TOL_PCT)


def episode_variant(episode: dict, variant: str) -> dict:
    """Variant (b) drops the entry anchor: a copy with first_buy_price=None."""
    ep = dict(episode or {})
    if variant == "b":
        ep["first_buy_price"] = None
        ep["reason"] = "anchor suppressed (variant b)"
    return ep


def _variant_trail_start(variant: str, trail_start):
    return None if variant == "a" else trail_start


def _raw_bars(df, as_of: date):
    """Bars dated <= as_of INCLUDING today's partial bar, naive ET-date index,
    NaN Close dropped, tz-aware or naive input alike."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close"])
    out = df.copy()
    out.index = hs._et_dates(out.index)
    out = out[out.index <= pd.Timestamp(as_of)]
    out = out[out["Close"].notna()].sort_index(kind="mergesort")
    return out[~out.index.duplicated(keep="last")]


def div_since(df, since, until=None, denom=None):
    """Sum of the frame's Dividends dated in [since, until] as % of denom."""
    if df is None or len(df) == 0 or "Dividends" not in df.columns or not denom or since is None:
        return None
    idx = hs._et_dates(df.index)
    m = idx >= pd.Timestamp(since)
    if until is not None:
        m = m & (idx <= pd.Timestamp(until))
    return float(df["Dividends"][m].fillna(0).sum()) / denom * 100


# -- V0: today's engine -------------------------------------------------------

def legacy_stop(frame6m, price, avg_cost, manual) -> dict:
    """Today's app formula: atr_stop_loss on the frame (incl. a partial bar),
    protective_stop(price, avg_cost, atr_stop), manual override via
    manual_stop_wins. `manual` is the manual_stops row dict (or a price, or None).
    Breach = the app's `is_stop_breached`."""
    out = {"stop": None, "type": "Stop Unavailable", "auto_type": "Stop Unavailable",
           "gap": None, "breached": False, "atr_stop": None}
    if frame6m is None or len(frame6m) < 2 or not price:
        return out
    atr_stop, _ = risk.atr_stop_loss(frame6m, multiplier=ATR_STOP_MULT)
    if atr_stop is None or atr_stop <= 0:
        return out
    out["atr_stop"] = atr_stop
    stop, label = portfolio.protective_stop(price, avg_cost, atr_stop)
    out["auto_type"] = label
    mp = manual.get("stop_price") if isinstance(manual, dict) else manual
    mp = _f(mp)
    if mp and portfolio.manual_stop_wins(mp, stop):
        stop, label = round(mp, 2), "Manual"
    out.update(stop=stop, type=label, gap=_gap(price, stop),
               breached=is_stop_breached(price, stop))
    return out


# -- the proposal -------------------------------------------------------------

def _manual_price(manual):
    mp = manual.get("stop_price") if isinstance(manual, dict) else manual
    return _f(mp)


def variant_cells(long_frame, episode, avg_cost, as_of, trail_start, mults, manual,
                  price, variants=VARIANTS, partial_low=False) -> dict:
    """{(variant, mult): cell}. Variants b / c need trail_start (skipped if None).
    cell: stop (after the manual override), auto_stop, binding ("manual" when the
    manual won), basis, reason, gap, manual_won, ratchet (component or None),
    low / low_stop / low_session (the Low and the stop that was in force for it).

    LOW: the last COMPLETED session's Low is judged against the stop computed with
    as_of = that session (fixed before it began). With `partial_low` and a bar
    dated as_of in the frame, that bar's (partial) Low is judged against the stop
    as of today. Never Low(D-1) against stop(D)."""
    done = hs.completed_bars(long_frame, as_of)
    raw = _raw_bars(long_frame, as_of)
    last_session = done.index[-1].date() if len(done) else None
    last_close = _f(done["Close"].iloc[-1]) if len(done) else None
    if partial_low and len(raw) and raw.index[-1].date() == as_of:
        low, low_asof = _f(raw["Low"].iloc[-1]), as_of
    else:
        low = _f(done["Low"].iloc[-1]) if len(done) and "Low" in done.columns else None
        low_asof = last_session
    mp = _manual_price(manual)
    out = {}
    for v in variants:
        if v != "a" and trail_start is None:
            continue
        ep = episode_variant(episode, v)
        ts = _variant_trail_start(v, trail_start)
        for m in mults:
            kw = _held_kwargs(m, ts)
            res = hs.compute_held_stop(long_frame, ep, avg_cost, as_of, **kw)
            stop, manual_won = hs.compose_with_manual(res["stop"], mp)
            low_stop = None
            if low is not None and low_asof is not None:
                if low_asof == as_of:
                    low_stop = stop
                else:
                    r2 = hs.compute_held_stop(long_frame, ep, avg_cost, low_asof, **kw)
                    low_stop, _ = hs.compose_with_manual(r2["stop"], mp)
            out[(v, m)] = {
                "stop": stop, "auto_stop": res["stop"], "basis": res["basis"],
                "binding": "manual" if manual_won else res["binding"],
                "reason": res["reason"], "gap": _gap(price, stop),
                "manual_won": manual_won, "ratchet": (res.get("components") or {}).get("ratchet"),
                "last_session": last_session, "last_close": last_close,
                "low": low, "low_stop": low_stop, "low_session": low_asof,
            }
    return out


def _band(gap):
    """Rank of a gap-to-stop: 0 breached, 1 at risk, 2 approaching, 3 comfortable.
    Edges use <= like position_lifecycle."""
    if gap is None:
        return None
    if gap <= 0:
        return 0
    if gap <= POSITION_AT_RISK_GAP_PCT:
        return 1
    if gap <= APPROACHING_STOP_GAP_PCT:
        return 2
    return 3


def flags(cell: dict, v0: dict, price) -> list[str]:
    """Flags for one proposal cell relative to V0 (see the module docstring)."""
    out: list[str] = []
    stop = cell.get("stop")
    if stop is None:
        return out
    v0_breached = bool(v0.get("breached"))
    if price and is_stop_breached(price, stop) and not v0_breached:
        out.append("NEWLY_BREACHED_CLOSE")
    low, low_stop = cell.get("low"), cell.get("low_stop")
    if low and low_stop and is_stop_breached(low, low_stop) and not v0_breached:
        out.append("NEWLY_BREACHED_LOW")
    g, g0 = cell.get("gap"), v0.get("gap")
    b, b0 = _band(g), _band(g0)
    if b is not None and b > 0:
        if b == 1 and (b0 is None or b0 > 1):
            out.append("CROSSES_AT_RISK")
        elif b == 2 and (b0 is None or b0 > 2):
            out.append("CROSSES_APPROACHING")
    if g is not None and g0 is not None and g < ADD_WINNER_MIN_GAP_PCT <= g0:
        out.append("LOSES_ADD_ROOM")
    if v0.get("type") == "Manual" and not cell.get("manual_won"):
        out.append("MANUAL_NOW_LOSES")
    v0_stop = v0.get("stop")
    if v0_stop is not None and stop < v0_stop:
        out.append("LOOSER_THAN_V0")
    if (v0.get("auto_type") not in _NO_RATCHET_LABELS and v0_stop is not None
            and cell.get("ratchet") is None):
        out.append("RATCHET_DROPPED")
    return out


def today_row(ticker, shares, avg_cost, episode, long_frame, frame6m, manual, as_of,
              trail_start, mults, live_price=None, variants=VARIANTS) -> dict:
    """Everything for one holding, offline-testable (frames in, dict out). One
    price for V0 and the variants: live, else the latest raw Close incl. today's bar."""
    raw = _raw_bars(long_frame, as_of)
    done = hs.completed_bars(long_frame, as_of)
    latest_close = _f(raw["Close"].iloc[-1]) if len(raw) else None
    latest_date = raw.index[-1].date() if len(raw) else None
    last_close = _f(done["Close"].iloc[-1]) if len(done) else None
    price = live_price if live_price else latest_close
    price_basis = "live" if live_price else "latest_bar"
    v0 = legacy_stop(frame6m, price, avg_cost, manual)
    partial_low = bool(live_price) and latest_date == as_of
    cells = variant_cells(long_frame, episode, avg_cost, as_of, trail_start, mults,
                          manual, price, variants=variants, partial_low=partial_low)
    ep_start = (episode or {}).get("start")
    since_trail = (max(pd.Timestamp(ep_start), pd.Timestamp(trail_start))
                   if ep_start and trail_start else None)
    for c in cells.values():
        c["flags"] = flags(c, v0, price)
        c["delta_vs_v0_pct"] = ((c["stop"] - v0["stop"]) / price * 100
                                if c["stop"] is not None and v0["stop"] is not None
                                and price else None)
    return {
        "ticker": ticker, "shares": shares, "avg_cost": avg_cost,
        "episode": {k: (episode or {}).get(k) for k in
                    ("status", "start", "last_buy", "first_buy_price", "reason")},
        "last_close": last_close, "price": price, "price_basis": price_basis,
        "latest_bar_date": latest_date, "live_price": live_price, "v0": v0,
        "manual": _manual_price(manual), "cells": cells,
        "div_since_E_pct": div_since(long_frame, ep_start, as_of, price),
        "div_since_trail_pct": div_since(long_frame, since_trail, as_of, price),
    }


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def summarize_today(rows: list[dict]) -> dict:
    """{(variant, mult): counts} over the today rows' cells."""
    out: dict = {}
    for r in rows:
        for key, c in r["cells"].items():
            s = out.setdefault(key, {
                "holdings": 0, "newly_breached_close": 0, "newly_breached_low": 0,
                "crosses_at_risk": 0, "crosses_approaching": 0, "loses_add_room": 0,
                "manual_now_loses": 0, "ratchet_dropped": 0,
                "tighter": 0, "looser": 0, "equal": 0, "deltas": [],
                "basis": Counter()})
            s["holdings"] += 1
            fl = set(c.get("flags") or [])
            s["newly_breached_close"] += "NEWLY_BREACHED_CLOSE" in fl
            s["newly_breached_low"] += "NEWLY_BREACHED_LOW" in fl
            s["crosses_at_risk"] += "CROSSES_AT_RISK" in fl
            s["crosses_approaching"] += "CROSSES_APPROACHING" in fl
            s["loses_add_room"] += "LOSES_ADD_ROOM" in fl
            s["manual_now_loses"] += "MANUAL_NOW_LOSES" in fl
            s["ratchet_dropped"] += "RATCHET_DROPPED" in fl
            v0_stop = r["v0"].get("stop")
            if c.get("stop") is not None and v0_stop is not None:
                if c["stop"] > v0_stop:
                    s["tighter"] += 1
                elif c["stop"] < v0_stop:
                    s["looser"] += 1
                else:
                    s["equal"] += 1
                if c.get("delta_vs_v0_pct") is not None:
                    s["deltas"].append(c["delta_vs_v0_pct"])
            s["basis"][c.get("basis")] += 1
    for s in out.values():
        s["median_delta_pct"] = _median(s["deltas"])
    return out


# -- replay -------------------------------------------------------------------

def _et_date(ts) -> date:
    return ts.tz_convert("America/New_York").date()


def closed_round_trips(trades_df, *, tickers=None, since=None,
                       shares_tol: float = SHARES_TOL) -> list[dict]:
    """ALL closed round trips (winners and losers): shares from ~0 to ~0 in ET
    dates, partial sells and adds inside one trip, a re-entry a new trip. Same
    running-total logic as held_stop.episode_from_journal (SPLIT row = adjusted
    TOTAL). Flags: has_split; oversold (sold more than bought: the journal starts
    mid-position)."""
    if trades_df is None or len(trades_df) == 0 or "ticker" not in trades_df.columns:
        return []
    df = trades_df.copy()
    df["_tk"] = df["ticker"].astype(str).str.upper().str.strip()
    df["_ts"] = pd.to_datetime(df["traded_at"], errors="coerce", utc=True, format="ISO8601")
    df = df.dropna(subset=["_ts"])
    want = {t.upper() for t in tickers} if tickers else None
    cols = ["_ts", "id"] if "id" in df.columns else ["_ts"]
    out: list[dict] = []
    for tk, g in df.groupby("_tk", sort=True):
        if want and tk not in want:
            continue
        total, cur = 0.0, None
        for _, r in g.sort_values(cols).iterrows():
            action = str(r.get("action", "")).upper()
            sh, px = _f(r.get("shares")), _f(r.get("price"))
            if not sh or sh <= 0:
                continue
            d = _et_date(r["_ts"])
            if "SPLIT" in action:
                if cur is not None and total > shares_tol:
                    cur["has_split"] = True
                    total = sh
            elif "BUY" in action:
                if total <= shares_tol:
                    cur = {"ticker": tk, "entry_date": d, "buys": [], "sells": [],
                           "has_split": False, "oversold": False}
                    total = 0.0
                cur["buys"].append((d, sh, px or 0.0))
                total += sh
            elif "SELL" in action and cur is not None:
                cur["sells"].append((d, sh, px or 0.0))
                total -= sh
                if total < -shares_tol:
                    cur["oversold"] = True
                if total <= shares_tol:
                    cur["exit_date"] = d
                    out.append(_finalize_trip(cur))
                    cur, total = None, 0.0
    if since is not None:
        out = [t for t in out if t["exit_date"] >= since]
    out.sort(key=lambda t: (t["exit_date"], t["ticker"]))
    return out


def _wavg(rows) -> float | None:
    sh = sum(s for _, s, _ in rows)
    return sum(s * p for _, s, p in rows) / sh if sh > 0 else None


def _finalize_trip(cur: dict) -> dict:
    cur["first_buy_price"] = cur["buys"][0][2] or None
    cur["avg_cost"] = _wavg(cur["buys"])
    cur["exit_price"] = _wavg(cur["sells"])
    cur["shares"] = sum(s for _, s, _ in cur["buys"])
    cur["realized_ret"] = ((cur["exit_price"] / cur["avg_cost"] - 1)
                           if cur["avg_cost"] and cur["exit_price"] else None)
    return cur


def _avg_cost_before(rt: dict, d: date) -> float | None:
    return _wavg([b for b in rt["buys"] if b[0] < d])


def _last_buy_before(rt: dict, d: date):
    ds = [b[0] for b in rt["buys"] if b[0] < d]
    return max(ds) if ds else None


def prep_frame(df, as_of: date):
    """Completed bars, naive ET session-date index (hs.completed_bars)."""
    return hs.completed_bars(df, as_of)


def basis_countable(as_of: date, prev_session) -> bool:
    """Exclude pre-calendar-start sessions whose previous weekday has no bar
    (a holiday `held_stop` can't know about) from basis counts."""
    if as_of >= _HOLIDAY_CAL_START:
        return True
    prev_bd = (pd.Timestamp(as_of) - pd.offsets.BDay(1)).date()
    return prev_session is not None and prev_session == prev_bd


def first_fire(frame, rt: dict, variant: str, mult: float, *, trail_start=None) -> dict:
    """Walk as-of sessions entry+1 .. exit; the held stop as of each uses bars
    before it only. First fire = first session whose Close (and separately Low)
    breaches that session's stop by the app's `is_stop_breached`. `frame` is a
    prepared (prep_frame) frame that may run past the exit (forward returns and
    the next-open read it)."""
    entry, exit_ = rt["entry_date"], rt["exit_date"]
    ts = _variant_trail_start(variant, trail_start)
    fb = None if variant == "b" else rt.get("first_buy_price")
    dates = [i.date() for i in frame.index]
    ret_exit = rt.get("realized_ret")
    res = {"ticker": rt["ticker"], "variant": variant, "mult": mult,
           "entry_date": entry, "exit_date": exit_, "fired": False, "fire_date": None,
           "fire_close": None, "fire_stop": None, "fire_basis": None,
           "fire_binding": None, "sessions_before_exit": None, "ret_at_fire": None,
           "ret_at_exit": ret_exit,
           "loser": (ret_exit < 0) if ret_exit is not None else None,
           "fwd5": None, "fwd10": None, "whipsaw": None, "next_open_gap": None,
           "ret_next_open": None, "low_fired": False, "low_fire_date": None,
           "low_sessions_before_exit": None, "basis_counts": Counter(), "sessions": 0,
           "div_since_E_pct": div_since(frame, entry, exit_, rt.get("avg_cost"))}
    for i, s in enumerate(dates):
        if not (entry < s <= exit_):
            continue
        avg_cost = _avg_cost_before(rt, s)
        if not avg_cost:
            continue
        ep = {"status": "ok", "start": entry, "first_buy_price": fb, "split_ratio": 1.0,
              "reason": "", "last_buy": _last_buy_before(rt, s)}
        r = hs.compute_held_stop(frame.iloc[:i + 1], ep, avg_cost, s,
                                 **_held_kwargs(mult, ts))
        res["sessions"] += 1
        if basis_countable(s, dates[i - 1] if i else None):
            res["basis_counts"][r["basis"]] += 1
        stop = r["stop"]
        if stop is None:
            continue
        close, low = float(frame["Close"].iloc[i]), float(frame["Low"].iloc[i])
        if not res["fired"] and is_stop_breached(close, stop):
            after = [d for d in dates if s < d <= exit_]
            fwd = {n: (float(frame["Close"].iloc[i + n]) / close - 1
                       if i + n < len(frame) else None) for n in FORWARD_SESSIONS}
            ahead = frame["Close"].iloc[i + 1:i + 1 + WHIPSAW_SESSIONS]
            nxt = (float(frame["Open"].iloc[i + 1])
                   if "Open" in frame.columns and i + 1 < len(frame) else None)
            res.update(fired=True, fire_date=s, fire_close=close, fire_stop=stop,
                       fire_basis=r["basis"], fire_binding=r["binding"],
                       sessions_before_exit=len(after),
                       ret_at_fire=close / avg_cost - 1,
                       fwd5=fwd[5], fwd10=fwd[10],
                       whipsaw=(bool((ahead > stop).any()) if len(ahead) else None),
                       next_open_gap=(nxt / close - 1 if nxt else None),
                       ret_next_open=(nxt / avg_cost - 1 if nxt else None))
        if not res["low_fired"] and is_stop_breached(low, stop):
            res.update(low_fired=True, low_fire_date=s,
                       low_sessions_before_exit=len([d for d in dates if s < d <= exit_]))
    return res


def attach_ladder(row: dict, ladder: dict | None, loser: bool) -> dict:
    """Ladder comparison for LOSING trips only (None = N/A: a winner, or the
    ladder couldn't be replayed). ladder_vs_stop: stop_first / ladder_first /
    same_session / stop_only / ladder_only / neither, where the ladder's first
    fire is the earlier of its TRIM and EXIT. before_trim / before_exit: True /
    False; a tier that never fired counts as "this fire was earlier"."""
    row["ladder_first_trim"] = row["ladder_first_exit"] = None
    row["before_trim"] = row["before_exit"] = None
    row["ladder_vs_stop"] = None
    if not loser or not ladder or ladder.get("error"):
        return row
    trim, exit_ = ladder.get("first_trim"), ladder.get("first_exit")
    row["ladder_first_trim"], row["ladder_first_exit"] = trim, exit_
    if row["fired"]:
        for key, t in (("before_trim", trim), ("before_exit", exit_)):
            row[key] = True if t is None else row["fire_date"] < t
    first = min((t for t in (trim, exit_) if t is not None), default=None)
    s = row["fire_date"] if row["fired"] else None
    if s is not None and first is not None:
        row["ladder_vs_stop"] = ("stop_first" if s < first else
                                 "ladder_first" if s > first else "same_session")
    elif s is not None:
        row["ladder_vs_stop"] = "stop_only"
    elif first is not None:
        row["ladder_vs_stop"] = "ladder_only"
    else:
        row["ladder_vs_stop"] = "neither"
    return row


def _agg(rs: list[dict]) -> dict:
    fires = [r for r in rs if r["fired"]]

    def pp(r, key):
        a, b = r.get(key), r.get("ret_at_exit")
        return None if a is None or b is None else (a - b) * 100

    def pct(r, key):
        return None if r.get(key) is None else r[key] * 100
    w = [r["whipsaw"] for r in fires if r["whipsaw"] is not None]
    f10 = [r["fwd10"] for r in fires if r["fwd10"] is not None]
    saved = [pp(r, "ret_at_fire") for r in fires]
    saved_open = [pp(r, "ret_next_open") for r in fires]
    f5 = [pct(r, "fwd5") for r in fires]
    f10p = [pct(r, "fwd10") for r in fires]
    early = [r["sessions_before_exit"] for r in fires]
    n = lambda xs: len([x for x in xs if x is not None])  # noqa: E731
    return {
        "trips": len(rs), "fires": len(fires),
        "low_fires": sum(1 for r in rs if r["low_fired"]),
        "median_sessions_early": _median(early), "n_early": n(early),
        "whipsaw_pct": (100.0 * sum(w) / len(w)) if w else None, "n_whipsaw": len(w),
        "fwd10_pos_pct": (100.0 * sum(1 for x in f10 if x > 0) / len(f10)) if f10 else None,
        "n_fwd10": len(f10),
        "mean_saved_pp": _mean(saved), "median_saved_pp": _median(saved), "n_saved": n(saved),
        "mean_saved_open_pp": _mean(saved_open), "median_saved_open_pp": _median(saved_open),
        "n_saved_open": n(saved_open),
        "mean_fwd5_pct": _mean(f5), "median_fwd5_pct": _median(f5), "n_fwd5": n(f5),
        "mean_fwd10_pct": _mean(f10p), "median_fwd10_pct": _median(f10p),
        "missing_fwd5": len(fires) - n(f5), "missing_fwd10": len(fires) - n(f10p),
    }


def summarize_replay(rows: list[dict]) -> dict:
    """{(variant, mult): {"all"/"loser"/"winner": _agg, "ladder": Counter over
    ladder_vs_stop for losing trips, "losers_n", "basis"}}. saved_pp > 0: the fire
    beat the actual exit."""
    out: dict = {}
    for k in sorted({(r["variant"], r["mult"]) for r in rows}):
        rs = [r for r in rows if (r["variant"], r["mult"]) == k]
        basis: Counter = Counter()
        for r in rs:
            basis.update(r["basis_counts"])
        losers = [r for r in rs if r.get("loser") is True]
        out[k] = {
            "all": _agg(rs), "loser": _agg(losers),
            "winner": _agg([r for r in rs if r.get("loser") is False]),
            "ladder": Counter(r["ladder_vs_stop"] for r in losers if r.get("ladder_vs_stop")),
            "losers_n": len(losers),
            "ladder_unavailable": sum(1 for r in losers if not r.get("ladder_vs_stop")),
            "basis": dict(basis),
        }
    return out


# -- I/O edges ----------------------------------------------------------------

def _fetch_long(ticker: str, period: str):
    """Split-adjusted, dividend-UNadjusted OHLC (yfinance auto_adjust=False)."""
    import yfinance as yf
    df = yf.Ticker(ticker).history(period=period, auto_adjust=False)
    if df is not None and len(df) and "Close" in df.columns:
        df = df[df["Close"].notna()]
    return df


def _long_for(ticker: str, episode_start, trail_start, as_of: date):
    period = hs.history_period_needed(None, episode_start, trail_start, WARMUP_BARS,
                                      as_of=as_of) or "1y"
    df = _fetch_long(ticker, period)
    if df is not None and len(df) and period != "max":
        # yfinance indexes are tz-aware: compare ET session dates, not raw stamps.
        first = hs._et_dates(df.index)[0].date()
        again = hs.history_period_needed(first, episode_start, trail_start,
                                         WARMUP_BARS, as_of=as_of)
        if again is not None and again != period:
            df = _fetch_long(ticker, again)
    return df


def _fmt(v, nd=2):
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) and v == v else "-"


_FLAG_SHORT = {
    "NEWLY_BREACHED_CLOSE": "BRCH", "NEWLY_BREACHED_LOW": "BRLO", "CROSSES_AT_RISK": "RISK",
    "CROSSES_APPROACHING": "APPR", "LOSES_ADD_ROOM": "ADDX", "MANUAL_NOW_LOSES": "MANL",
    "LOOSER_THAN_V0": "LOOS", "RATCHET_DROPPED": "RATX",
}


def print_header(args, n_holdings: int, n_manual: "int | None", do_today: bool) -> None:
    print("=== HELD-STOP DRY RUN (finding #26, C2) ===")
    print(f"as_of={args.as_of}  trail_start={args.trail_start}"
          f"{'' if args.trail_start_explicit else ' (default = as_of)'}  mults={list(args.mults)}")
    print(f"today variant shown: {args.variant}; replay variants: {list(args.replay_variants)}")
    print(f"price basis: {'live price' if args.live else 'latest bar Close incl. today (raw frame)'} "
          "for V0 and every variant; 'last_close' = last completed session (info)")
    if n_manual is None:   # strict read failed: NOT the same as "none set"
        print(f"holdings: {n_holdings}; manual stops: UNREADABLE "
              "(load_manual_stops_or_none returned None; continuing with {} -- "
              "MANUAL_NOW_LOSES / manual columns below are NOT reliable)")
    else:
        print(f"holdings: {n_holdings}; manual stops loaded: {n_manual}"
              f"{'  WARNING: 0 manual stops with holdings present (table read OK but empty)' if n_manual == 0 and n_holdings else ''}")
    print(f"WARMUP_BARS={WARMUP_BARS} MIN_BARS={MIN_BARS} FILL_TOL_PCT={FILL_TOL_PCT} "
          f"SHARES_TOL={SHARES_TOL} (constants.py HELD_STOP_*)")
    print("variants use split-adjusted, dividend-UNadjusted closes; V0 uses the app's adjusted "
          "6-month bundle (div_since_E_pct in the CSV sizes the gap)")
    print("b / c at trail_start = as_of are the day-1 picture: b = prior_session fallback, "
          "c = entry anchor only, no ratchet")
    print("(a) replay = the steady state of (c) for positions opened after HELD_TRAIL_START "
          "(trail_start <= entry makes c == a)")
    print("replay = CLOSED trips only (survivorship); ladder is single-lot on final avg cost on "
          "adjusted frames")
    print("pre-2026 post-holiday sessions are excluded from replay basis counts "
          f"(NYSE_HOLIDAYS starts {_HOLIDAY_CAL_START})")
    print(f"band edges use <= (like position_lifecycle); breach = is_stop_breached (rounded gap <= 0); "
          f"ADD_WINNER_MIN_GAP_PCT ({ADD_WINNER_MIN_GAP_PCT}) == APPROACHING_STOP_GAP_PCT "
          f"({APPROACHING_STOP_GAP_PCT}) is {'true' if ADD_WINNER_MIN_GAP_PCT == APPROACHING_STOP_GAP_PCT else 'NOT true'}"
          ", so those two flags coincide when equal")
    print("replay breach = the same rule; b with no explicit --trail-start trails from each trip's "
          "entry date; c is not computed there (== a)")
    print()


def print_compact(rows: list[dict], variant: str, mults) -> None:
    print(f"\n=== TODAY, variant {variant} (delta = stop vs V0 as % of price; negative = LOOSER) ===")
    for r in rows:
        v0 = r["v0"]
        head = (f"{r['ticker']:<6} px={_fmt(r['price'])}({r['price_basis'][:4]}) "
                f"V0 {_fmt(v0['stop'])} gap {_fmt(v0['gap'], 1)}% {v0['type']}"
                f"{' BREACHED' if v0['breached'] else ''}")
        parts = []
        for m in mults:
            c = r["cells"].get((variant, m))
            if c is None:
                continue
            fl = ",".join(_FLAG_SHORT.get(f, f) for f in c["flags"])
            parts.append(f"x{m}: {_fmt(c['stop'])} ({_fmt(c['delta_vs_v0_pct'], 1)}%) "
                         f"{c['basis'][:4]}{' ' + fl if fl else ''}")
        print(head + "  |  " + "  |  ".join(parts))


def print_today(rows: list[dict]) -> None:
    for r in rows:
        ep = r["episode"]
        print(f"\n{r['ticker']}  {r['shares']:g} sh @ {_fmt(r['avg_cost'])}  episode "
              f"{ep['status']} start={ep['start']} last_buy={ep['last_buy']}  "
              f"last_close={_fmt(r['last_close'])}  price({r['price_basis']})={_fmt(r['price'])}")
        v0 = r["v0"]
        print(f"  V0    stop={_fmt(v0['stop'])} gap={_fmt(v0['gap'], 1)}% type={v0['type']}"
              f"{' BREACHED' if v0['breached'] else ''}"
              f"{'  (manual ' + _fmt(r['manual']) + ')' if r['manual'] else ''}")
        for (v, m), c in r["cells"].items():
            print(f"  {v} x{m:<4} stop={_fmt(c['stop'])} gap={_fmt(c['gap'], 1)}% "
                  f"d={_fmt(c['delta_vs_v0_pct'], 1)}% bind={c['binding']} basis={c['basis']} "
                  f"{' '.join(c['flags'])}"
                  f"{'  [' + c['reason'] + ']' if c['reason'] else ''}")


def print_today_summary(s: dict, n: int) -> None:
    print(f"\n=== TODAY SUMMARY ({n} holdings) ===")
    for (v, m), x in sorted(s.items()):
        print(f"  {v} x{m}: tighter {x['tighter']} / looser {x['looser']} / equal {x['equal']} "
              f"(median delta {_fmt(x['median_delta_pct'], 2)}% of price); ratchet dropped "
              f"{x['ratchet_dropped']}; newly breached close {x['newly_breached_close']}, "
              f"low {x['newly_breached_low']}; crosses at-risk {x['crosses_at_risk']}, "
              f"approaching {x['crosses_approaching']}; loses add room {x['loses_add_room']}; "
              f"manual now loses {x['manual_now_loses']}; basis {dict(x['basis'])}")


def _line(a: dict) -> str:
    return (f"trips {a['trips']}, fires {a['fires']} (low {a['low_fires']}); sessions early "
            f"median {_fmt(a['median_sessions_early'], 1)} (n={a['n_early']}); saved@close "
            f"mean {_fmt(a['mean_saved_pp'], 1)}pp / median {_fmt(a['median_saved_pp'], 1)}pp "
            f"(n={a['n_saved']}); saved@next-open mean {_fmt(a['mean_saved_open_pp'], 1)}pp / "
            f"median {_fmt(a['median_saved_open_pp'], 1)}pp (n={a['n_saved_open']}); whipsaw "
            f"{_fmt(a['whipsaw_pct'], 0)}% (n={a['n_whipsaw']}); fwd10>0 "
            f"{_fmt(a['fwd10_pos_pct'], 0)}% (n={a['n_fwd10']}); fwd5 mean "
            f"{_fmt(a['mean_fwd5_pct'], 1)}% / median {_fmt(a['median_fwd5_pct'], 1)}% "
            f"(n={a['n_fwd5']}, missing {a['missing_fwd5']}); fwd10 mean "
            f"{_fmt(a['mean_fwd10_pct'], 1)}% / median {_fmt(a['median_fwd10_pct'], 1)}% "
            f"(n={a['n_fwd10']}, missing {a['missing_fwd10']})")


def print_replay_summary(s: dict) -> None:
    print("\n=== REPLAY SUMMARY (saved_pp > 0: the fire beat the actual exit) ===")
    for (v, m), x in sorted(s.items()):
        print(f"\n  {v} x{m}")
        for g in ("all", "loser", "winner"):
            print(f"    {g:<6} {_line(x[g])}")
        print(f"    ladder vs stop over {x['losers_n']} losers "
              f"({x['ladder_unavailable']} without a ladder read): {dict(x['ladder'])}")
        print(f"    basis counts {x['basis']}")


def flatten_today(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        base = {"ticker": r["ticker"], "shares": r["shares"], "avg_cost": r["avg_cost"],
                "episode_status": r["episode"]["status"], "episode_start": r["episode"]["start"],
                "last_buy": r["episode"]["last_buy"], "last_close": r["last_close"],
                "price": r["price"], "price_basis": r["price_basis"],
                "div_since_E_pct": r["div_since_E_pct"],
                "div_since_trail_pct": r["div_since_trail_pct"]}
        out.append({**base, "variant": "V0", "mult": "", "stop": r["v0"]["stop"],
                    "gap": r["v0"]["gap"], "binding": r["v0"]["type"], "basis": "",
                    "reason": "", "flags": "", "delta_vs_v0_pct": "", "low": "",
                    "low_stop": "", "ratchet": ""})
        for (v, m), c in r["cells"].items():
            out.append({**base, "variant": v, "mult": m, "stop": c["stop"], "gap": c["gap"],
                        "binding": c["binding"], "basis": c["basis"], "reason": c["reason"],
                        "flags": "|".join(c["flags"]), "delta_vs_v0_pct": c["delta_vs_v0_pct"],
                        "low": c["low"], "low_stop": c["low_stop"], "ratchet": c["ratchet"]})
    return out


def _write_csv(path: str, rows: list[dict]) -> None:
    keys: list = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows([{k: (dict(v) if isinstance(v, Counter) else v) for k, v in r.items()}
                     for r in rows])
    print(f"wrote {len(rows)} rows to {path}")


def run_today(args, holdings, trades, manual_stops):
    """(rows, skipped[(ticker, reason)])."""
    from stock_analyzer import data
    rows, skipped = [], []
    live = {}
    if args.live and len(holdings):
        try:
            live = data.fetch_live_prices([str(t).upper() for t in holdings["Ticker"]]) or {}
        except Exception as e:
            print(f"live prices failed ({e}); falling back to the latest bar")
    for _, h in holdings.iterrows():
        t = str(h["Ticker"]).upper()
        if args.tickers and t not in args.tickers:
            continue
        shares, avg = float(h["Shares"]), float(h["Avg Cost ($)"])
        ep = hs.episode_from_journal(t, trades, shares, shares_tol=SHARES_TOL)
        try:
            long_df = _long_for(t, ep.get("start"), args.trail_start, args.as_of)
            f6 = data.fetch_ticker_bundle(t, "6mo").get("history")
            if long_df is None or not len(long_df):
                skipped.append((t, "no long history returned"))
                continue
            lp = _f((live.get(t) or {}).get("price"))
            rows.append(today_row(t, shares, avg, ep, long_df, f6, manual_stops.get(t),
                                  args.as_of, args.trail_start, args.mults, live_price=lp))
        except Exception as e:
            skipped.append((t, f"{type(e).__name__}: {e}"))
    return rows, skipped


def run_replay(args, trades) -> list[dict]:
    import exit_ladder_replay as ladder_mod  # sibling script (its dir is on sys.path); lazy: network deps
    from stock_analyzer import data
    trips = closed_round_trips(trades, tickers=args.tickers, since=args.since)
    skip_split = sum(t["has_split"] for t in trips)
    skip_over = sum(1 for t in trips if t["oversold"] and not t["has_split"])
    print(f"{len(trips)} closed round trips; skipped: {skip_split} with a SPLIT, "
          f"{skip_over} oversold (journal starts mid-position)")
    try:
        sb = data.fetch_ticker_bundle("SPY", args.ladder_period)
        spy_hist = sb.get("history") if isinstance(sb, dict) else None
    except Exception as e:
        print(f"SPY fetch failed ({e}); ladder comparison unavailable")
        spy_hist = None
    frames: dict = {}
    out: list[dict] = []
    for i, rt in enumerate(trips, 1):
        if rt["has_split"] or rt["oversold"]:
            continue
        t = rt["ticker"]
        if t not in frames:
            first = min(x["entry_date"] for x in trips if x["ticker"] == t)
            try:
                frames[t] = prep_frame(_long_for(t, first, None, args.as_of), args.as_of)
            except Exception as e:
                print(f"{t}: history fetch failed ({e})")
                frames[t] = None
        fr = frames[t]
        if fr is None or not len(fr):
            continue
        loser = rt["realized_ret"] is not None and rt["realized_ret"] < 0
        lad = None
        if loser and spy_hist is not None:
            try:
                lad = ladder_mod.replay_round_trip(
                    {"ticker": t, "entry_date": rt["entry_date"], "exit_date": rt["exit_date"],
                     "entry_price": rt["avg_cost"], "shares": rt["shares"]},
                    period=args.ladder_period, spy_hist=spy_hist)
            except Exception as e:
                lad = {"error": str(e)}
        print(f"[{i}/{len(trips)}] {t} {rt['entry_date']} -> {rt['exit_date']}")
        for v in args.replay_variants:
            if v == "c" and not args.trail_start_explicit:
                continue   # c == a when trail_start <= entry; not computed
            ts = args.trail_start
            if v == "b" and not args.trail_start_explicit:
                ts = rt["entry_date"]
            for m in args.mults:
                res = first_fire(fr, rt, v, m, trail_start=ts)
                out.append(attach_ladder(res, lad, loser))
    return out


# -- floor mode (finding #26 C3c-2) -------------------------------------------
# The persisted-floor dry run. Pure parts (frames in, dicts out) first, then the
# I/O edge `run_floor`. Everything here is read-only: the "saved rows" are a dict
# in memory standing in for the held_stop_floors table.

FLOOR_SESSIONS_DEFAULT = 20   # replay depth: a report window, not a gate


def truncate_frame(df, start):
    """Bars whose ET session date >= `start` (the rolled 6-month bundle of a later
    day). `start` None, or an empty frame: returned unchanged."""
    if start is None or df is None or len(df) == 0:
        return df
    return df[hs._et_dates(df.index) >= pd.Timestamp(start)]


def _iso_date(v):
    """date / datetime / Timestamp / ISO string -> date; None when missing or bad."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "date") and callable(v.date):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _usable(df) -> bool:
    return (isinstance(df, pd.DataFrame) and len(df) > 0
            and all(c in df.columns for c in ("High", "Low", "Close")))


def replay_dates(frame, ep, as_of: date, n: int) -> list:
    """The last `n` completed-session dates D of `frame` with D > max(episode start,
    last BUY) and D < as_of: the sessions whose EOD run would have written a row
    under TODAY's fingerprint (an earlier session, before the last add, would
    carry a different average cost and be invalid today anyway)."""
    marks = [d for d in (_iso_date((ep or {}).get("start")),
                         _iso_date((ep or {}).get("last_buy"))) if d is not None]
    if not marks or n < 1:
        return []
    after = max(marks)
    bars = hs.completed_bars(frame, as_of)
    ds = [i.date() for i in bars.index if i.date() > after]
    return ds[-n:]


def replay_writer(ticker, long_frame, ep, avg_cost, dates, pol):
    """Replay the EOD writer over `dates`. Per session D: full-basis result on the
    long frame as of D, wrapped like the driver (`held_stop_wiring._wrap`), through
    `apply_floor` with the rows saved so far, then `rows_to_write` (fail closed:
    only a result that went through apply_floor is written). Returns
    ({episode_start date: row}, log). One log entry per session: as_of, basis,
    floor_state, floor_reason, computed (the figure before the floor), stop (after),
    delta (saved - computed when the floor bound, else 0.0), delta_pct (of the last
    close), binding, wrote. Nothing is read from or written to a database."""
    rows: dict = {}
    log: list = []
    for d in dates:
        res = hs.compute_held_stop(long_frame, ep, avg_cost, d, **pol)
        eff = hsf.apply_floor(hsw._wrap(ticker, res, "", avg_cost, ep=ep), rows, ep=ep,
                              avg_cost=avg_cost, frame=long_frame, as_of=d, readable=True)
        written = hsf.rows_to_write({ticker: eff}, d)
        fl = eff["floor"]
        computed = fl.get("computed")
        bound = fl["state"] == hsf.APPLIED and eff.get("stop") is not None and computed is not None
        delta = round(eff["stop"] - computed, 2) if bound else 0.0
        lc = _f(eff.get("last_close"))
        log.append({"as_of": d, "basis": eff.get("basis"), "floor_state": fl["state"],
                    "floor_reason": fl.get("reason", ""), "computed": computed,
                    "stop": eff.get("stop"), "delta": delta,
                    "delta_pct": (delta / lc * 100) if lc else None,
                    "binding": eff.get("binding"), "wrote": bool(written)})
        for r in written:
            rows[_iso_date(r["episode_start"])] = r
    return rows, log


def close_drift(row, frame, as_of: date, *, tol_pct: float, long_frame=None) -> dict:
    """The close check of `held_stop_floor.verify_closes`, reported per session and
    SEPARATELY for the REF session and the BIND session of the saved `row`, against
    `frame` (the frame today's result came from). Per session (prefix ref_ / bind_):
    session, saved_close, frame_close, drift_pct (signed, frame vs saved), status,
    age_sessions. status: within / over (|drift| above `tol_pct`, the verify_closes
    arithmetic) / outside_frame (before the frame's first bar or after its last:
    verify_closes skips it) / missing (inside the span but absent: verify_closes
    FAILS) / no_row / no_data / no_frame. age_sessions = completed sessions after
    that bar in `long_frame` (0 = the last completed session); None when unknown."""
    try:
        bars = hs.completed_bars(frame, as_of)
    except Exception:
        bars = pd.DataFrame(columns=["Open", "High", "Low", "Close"])
    lbars = hs.completed_bars(long_frame, as_of) if long_frame is not None else None
    out: dict = {}
    for name in ("ref", "bind"):
        sess = _iso_date(row.get(f"{name}_session")) if row else None
        saved = _f(row.get(f"{name}_close")) if row else None
        d = {f"{name}_session": sess, f"{name}_saved_close": saved,
             f"{name}_frame_close": None, f"{name}_drift_pct": None,
             f"{name}_status": "no_row", f"{name}_age_sessions": None}
        if row:
            ts = pd.Timestamp(sess) if sess is not None else None
            if lbars is not None and ts is not None and ts in lbars.index:
                d[f"{name}_age_sessions"] = int((lbars.index > ts).sum())
            if sess is None or saved is None or saved <= 0:
                d[f"{name}_status"] = "no_data"
            elif len(bars) == 0:
                d[f"{name}_status"] = "no_frame"
            elif ts < bars.index[0] or ts > bars.index[-1]:
                d[f"{name}_status"] = "outside_frame"
            elif ts not in bars.index or _f(bars["Close"].loc[ts]) is None:
                d[f"{name}_status"] = "missing"
            else:
                got = _f(bars["Close"].loc[ts])
                d[f"{name}_frame_close"] = got
                d[f"{name}_drift_pct"] = (got / saved - 1.0) * 100.0
                d[f"{name}_status"] = ("over" if abs(got - saved) * 100.0 > float(tol_pct) * saved
                                       else "within")
        out.update(d)
    return out


def _anchor_cover_start(long_frame, ep, as_of: date):
    """The first bar the frame must contain to keep the entry anchor: the bar
    `HELD_STOP_WARMUP_BARS` completed sessions before the episode start (C1's ATR
    origin). A fallback frame whose first bar is LATER loses the anchor. None when
    the long frame itself doesn't reach back that far (permanently partial)."""
    start = _iso_date((ep or {}).get("start"))
    if start is None:
        return None
    bars = hs.completed_bars(long_frame, as_of)
    origin = int(bars.index.searchsorted(pd.Timestamp(start))) - int(HELD_STOP_WARMUP_BARS)
    return bars.index[origin].date() if 0 <= origin < len(bars) else None


def _stop_delta(un, fl):
    a, b = (un or {}).get("stop"), (fl or {}).get("stop")
    return round(b - a, 2) if a is not None and b is not None else None


def floor_row(ticker, shares, avg_cost, ep, long_frame, bundle_frame, as_of: date, pol, *,
              sessions=FLOOR_SESSIONS_DEFAULT, bundle_start=None, live_price=None) -> dict:
    """Everything the floor dry run says about ONE holding (frames in, dict out; no
    network). Keys are flat (CSV-ready) except `replay_log`. See the module
    docstring's --floor entry for the definitions."""
    tol = float(_C.HELD_STOP_FLOOR_CLOSE_TOL_PCT)         # read at call time
    ep = ep or {}
    raw = _raw_bars(long_frame, as_of)
    latest_close = _f(raw["Close"].iloc[-1]) if len(raw) else None
    price = live_price if live_price else latest_close
    start = _iso_date(ep.get("start"))
    out = {"ticker": ticker, "shares": shares, "avg_cost": avg_cost, "as_of": as_of,
           "episode_status": ep.get("status"), "episode_start": start,
           "last_buy": _iso_date(ep.get("last_buy")),
           "price": price, "price_basis": "live" if live_price else "latest_bar",
           "tol_pct": tol, "bundle_start": bundle_start, "flags": [], "no_floor": "",
           "u8_permanently_partial": False, "replay_log": []}
    if ep.get("status") != "ok" or start is None:
        out["no_floor"] = f"no usable episode ({ep.get('status')}: {ep.get('reason')})"
        out["flags"].append("NO_FLOOR_NO_EPISODE")
        return out
    if start >= as_of:
        out["no_floor"] = "bought today (buy_day basis, no floor)"
        return out

    # replay the writer over the long frame
    dates = replay_dates(long_frame, ep, as_of, int(sessions))
    rows, log = replay_writer(ticker, long_frame, ep, avg_cost, dates, pol)
    binds = [e for e in log if e["floor_state"] == hsf.APPLIED and e["basis"] == "full"
             and e["delta"] > 0]
    out.update({
        "replay_sessions": len(log),
        "replay_full_days": sum(1 for e in log if e["basis"] == "full"),
        "replay_rows_written": sum(1 for e in log if e["wrote"]),
        "full_day_binds": len(binds),
        "full_day_max_delta": max((e["delta"] for e in binds), default=0.0),
        "full_day_max_delta_pct": max((e["delta_pct"] or 0.0 for e in binds), default=0.0),
        "full_day_binds_detail": "; ".join(f"{e['as_of']} +{e['delta']:.2f}" for e in binds),
        "replay_log": log,
    })
    saved = rows.get(start)
    out["saved_row_as_of"] = _iso_date(saved.get("as_of")) if saved else None
    out["saved_row_stop"] = saved.get("stop") if saved else None
    out["saved_row_binding"] = saved.get("binding") if saved else None
    out["anchor_cover_start"] = _anchor_cover_start(long_frame, ep, as_of)

    # today on the long frame: unfloored vs floored by the replayed rows
    full_raw = hs.compute_held_stop(long_frame, ep, avg_cost, as_of, **pol)
    full_un = hsw._wrap(ticker, full_raw, "", avg_cost, ep=ep)
    full_fl = hsf.apply_floor(full_un, rows, ep=ep, avg_cost=avg_cost, frame=long_frame,
                              as_of=as_of, readable=True)
    out.update({
        "full_basis": full_un["basis"], "full_reason": full_raw.get("reason", ""),
        "full_stop": full_un["stop"], "full_binding": full_un["binding"],
        "full_floor_state": full_fl["floor"]["state"],
        "full_floor_reason": full_fl["floor"].get("reason", ""),
        "full_floored_stop": full_fl["stop"], "full_delta": _stop_delta(full_un, full_fl),
    })
    out["u8_permanently_partial"] = bool(log) and not out["replay_full_days"] \
        and full_un["basis"] == "partial"

    # today on the bundle-only fallback frame (basis forced to partial like _one)
    fb_frame = truncate_frame(bundle_frame, bundle_start)
    bars_fb = hs.completed_bars(fb_frame, as_of) if _usable(fb_frame) else None
    out["fallback_first_bar"] = (bars_fb.index[0].date()
                                 if bars_fb is not None and len(bars_fb) else None)
    if not _usable(fb_frame):
        out["fb_basis"] = "no_fallback_frame"
    else:
        res = hs.compute_held_stop(fb_frame, ep, avg_cost, as_of, **pol)
        if res["basis"] == "full":
            res = {**res, "basis": "partial"}
        fb_un = hsw._wrap(ticker, res, "", avg_cost, fell_back=True, ep=ep)
        fb_fl = hsf.apply_floor(fb_un, rows, ep=ep, avg_cost=avg_cost, frame=fb_frame,
                                as_of=as_of, readable=True)
        out.update({
            "fb_basis": fb_un["basis"], "fb_reason": res.get("reason", ""),
            "fb_stop": fb_un["stop"], "fb_binding": fb_un["binding"],
            "fb_floor_state": fb_fl["floor"]["state"],
            "fb_floor_reason": fb_fl["floor"].get("reason", ""),
            "fb_floored_stop": fb_fl["stop"], "fb_floored_binding": fb_fl["binding"],
            "fb_delta": _stop_delta(fb_un, fb_fl),
            "fb_drop_vs_full": (round(full_un["stop"] - fb_un["stop"], 2)
                                if full_un["stop"] is not None and fb_un["stop"] is not None
                                else None),
            "fb_anchor_lost": bool((full_un.get("components") or {}).get("entry") is not None
                                   and (fb_un.get("components") or {}).get("entry") is None),
        })
        if fb_fl["floor"]["state"] == hsf.INVALID:
            out["flags"].append("FLOOR_INVALID_ON_FALLBACK")
        if out["fb_anchor_lost"]:
            out["flags"].append("ANCHOR_LOST_ON_FALLBACK")
        if (out["fb_delta"] or 0) > 0:
            out["flags"].append("FLOOR_BINDS_FALLBACK")
        if price and fb_un["stop"] is not None:
            out["fb_breached_unfloored"] = bool(is_stop_breached(price, fb_un["stop"]))
        if price and fb_fl["stop"] is not None:
            out["fb_breached_floored"] = bool(is_stop_breached(price, fb_fl["stop"]))
        if out.get("fb_breached_floored") and out.get("fb_breached_unfloored") is False:
            out["flags"].append("NEWLY_BREACHED_ON_FALLBACK")
    if full_fl["floor"]["state"] == hsf.INVALID:
        out["flags"].append("FLOOR_INVALID_ON_FULL")
    if (out["full_delta"] or 0) > 0:
        out["flags"].append("FLOOR_BINDS_FULL_TODAY")
    if out["full_day_binds"]:
        out["flags"].append("FLOOR_BOUND_ON_FULL_DAYS")
    if out["u8_permanently_partial"]:
        out["flags"].append("U8_PERMANENTLY_PARTIAL")

    # close-check drift of the saved row against the FALLBACK frame (ref / bind apart)
    drift = close_drift(saved, fb_frame if _usable(fb_frame) else None, as_of, tol_pct=tol,
                        long_frame=long_frame)
    out.update(drift)
    for name in ("ref", "bind"):
        if drift[f"{name}_status"] == "over":
            out["flags"].append(f"DRIFT_OVER_TOL_{name.upper()}")
    return out


def summarize_floor(rows: list[dict]) -> dict:
    """Counts over the floor rows: binds on full days (replay) and today on each
    frame, NEWLY_BREACHED_ON_FALLBACK names, floor states per frame, the close-check
    drift of the ref and the bind session (status counts, max |drift|, the bind
    ages), U8 names and the names that can never get a floor."""
    def drift_block(name):
        sts = Counter(r.get(f"{name}_status") for r in rows
                      if r.get(f"{name}_status") not in (None, "no_row"))
        ds = [abs(r[f"{name}_drift_pct"]) for r in rows if r.get(f"{name}_drift_pct") is not None]
        ages = [r[f"{name}_age_sessions"] for r in rows
                if r.get(f"{name}_age_sessions") is not None]
        return {"status": dict(sts), "over": sts.get("over", 0), "within": sts.get("within", 0),
                "max_abs_drift_pct": max(ds) if ds else None, "ages": sorted(ages)}
    floored = [r for r in rows if not r.get("no_floor")]
    return {
        "holdings": len(rows), "floor_possible": len(floored),
        "full_day_binds_total": sum(r.get("full_day_binds", 0) for r in floored),
        "full_day_binds_names": sorted(r["ticker"] for r in floored if r.get("full_day_binds")),
        "full_day_max_delta": max((r.get("full_day_max_delta") or 0.0 for r in floored),
                                  default=0.0),
        "full_day_max_delta_pct": max((r.get("full_day_max_delta_pct") or 0.0 for r in floored),
                                      default=0.0),
        "full_binds_today": sorted(r["ticker"] for r in floored if (r.get("full_delta") or 0) > 0),
        "fb_binds": sorted(r["ticker"] for r in floored if (r.get("fb_delta") or 0) > 0),
        "fb_max_delta": max((r.get("fb_delta") or 0.0 for r in floored), default=0.0),
        "newly_breached": sorted(r["ticker"] for r in floored
                                 if "NEWLY_BREACHED_ON_FALLBACK" in r["flags"]),
        "anchor_lost": sorted(r["ticker"] for r in floored if r.get("fb_anchor_lost")),
        "full_states": dict(Counter(r.get("full_floor_state") for r in floored)),
        "fb_states": dict(Counter(r.get("fb_floor_state") for r in floored
                                  if r.get("fb_floor_state"))),
        "ref": drift_block("ref"), "bind": drift_block("bind"),
        "u8": sorted(r["ticker"] for r in rows if r.get("u8_permanently_partial")),
        "no_floor": sorted((r["ticker"], r["no_floor"]) for r in rows if r.get("no_floor")),
    }


def flatten_floor(rows: list[dict]) -> list[dict]:
    """CSV rows: one `today` row per holding (every scalar column, flags joined with
    |) then one `replay` row per replayed session."""
    out = []
    for r in rows:
        base = {k: v for k, v in r.items() if k != "replay_log"}
        out.append({"record": "today", **base, "flags": "|".join(r["flags"])})
    for r in rows:
        for e in r.get("replay_log") or []:
            out.append({"record": "replay", "ticker": r["ticker"], **e})
    return out


def print_floor_header(args, n_holdings: int, pol: dict) -> None:
    print("=== HELD-STOP FLOOR DRY RUN (finding #26, C3c-2; read-only) ===")
    print(f"as_of={args.as_of}  holdings={n_holdings}  replay sessions={args.floor_sessions}  "
          f"bundle-start={args.bundle_start or 'none (the live 6-month bundle as fetched)'}")
    print(f"policy = production (held_stop_wiring._policy): trail_start={pol['trail_start']}"
          f"{' (explicit --trail-start)' if args.trail_start_explicit else ' (HELD_TRAIL_START)'}; "
          f"close-check tolerance HELD_STOP_FLOOR_CLOSE_TOL_PCT="
          f"{float(_C.HELD_STOP_FLOOR_CLOSE_TOL_PCT)}%")
    print("price basis: "
          + ("live price" if args.live else "latest bar Close incl. today (raw long frame)"))
    print("replay: per session D (D > max(episode start, last BUY), D < as_of): the full-basis "
          "result on the long frame as of D -> apply_floor(rows saved so far) -> rows_to_write; "
          "rows live in memory only. Today's average cost / episode are used for every D "
          "(the fingerprint a real row would carry), and the long frame is today's fetch "
          "(a tick Yahoo later revises can't show up here).")
    print("fallback = the 6-month bundle (dividend-ADJUSTED, so past closes drift) with basis "
          "forced to partial, as the driver does; floor delta = effective stop - the stop "
          "before the floor; breach = is_stop_breached(price, stop).")
    print("U8 = names whose long frame is partial on every replayed session: they never get "
          "a floor.")
    print()


def _yn(v) -> str:
    return "-" if v is None else ("YES" if v else "no")


def _drift_text(r: dict, name: str) -> str:
    st = r.get(f"{name}_status")
    if st in (None, "no_row"):
        return f"{name}: no saved row"
    base = (f"{name} {r.get(f'{name}_session')} saved {_fmt(r.get(f'{name}_saved_close'))} "
            f"frame {_fmt(r.get(f'{name}_frame_close'))}")
    if r.get(f"{name}_drift_pct") is not None:
        base += f" drift {r[f'{name}_drift_pct']:+.3f}% vs tol {r['tol_pct']:g}% -> {st.upper()}"
    else:
        base += f" -> {st.upper()}"
    age = r.get(f"{name}_age_sessions")
    return base + (f", {age} session(s) old" if age is not None else "")


def print_floor(rows: list[dict]) -> None:
    for r in rows:
        print(f"\n{r['ticker']}  {r['shares']:g} sh @ {_fmt(r['avg_cost'])}  episode "
              f"{r['episode_status']} start={r['episode_start']} last_buy={r['last_buy']}  "
              f"price({r['price_basis']})={_fmt(r['price'])}")
        if r["no_floor"]:
            print(f"  no floor possible: {r['no_floor']}")
            continue
        print(f"  replay: {r['replay_sessions']} sessions, {r['replay_full_days']} full, "
              f"{r['replay_rows_written']} rows written; floor bound on full days: "
              f"{r['full_day_binds']}x"
              + (f" (max delta {r['full_day_max_delta']:.2f} = {r['full_day_max_delta_pct']:.3f}% "
                 f"of price; {r['full_day_binds_detail']})" if r["full_day_binds"] else ""))
        print(f"  saved row: as_of={r['saved_row_as_of']} stop={_fmt(r['saved_row_stop'])} "
              f"binding={r['saved_row_binding']}")
        print(f"  FULL     basis={r['full_basis']} stop={_fmt(r['full_stop'])} -> floored "
              f"{_fmt(r['full_floored_stop'])} (delta {_fmt(r['full_delta'])}) floor="
              f"{r['full_floor_state']}"
              f"{' [' + r['full_floor_reason'] + ']' if r['full_floor_reason'] else ''}"
              f"{'  (' + r['full_reason'] + ')' if r['full_basis'] != 'full' and r['full_reason'] else ''}")
        if r.get("fb_basis") == "no_fallback_frame":
            print("  FALLBACK no usable bundle frame (empty after --bundle-start?)")
        else:
            print(f"  FALLBACK first bar {r['fallback_first_bar']} (anchor needs a bar on/before "
                  f"{r['anchor_cover_start']}{', LOST' if r['fb_anchor_lost'] else ''}) "
                  f"basis={r['fb_basis']} stop={_fmt(r['fb_stop'])} -> floored "
                  f"{_fmt(r['fb_floored_stop'])} (delta {_fmt(r['fb_delta'])}; fallback is "
                  f"{_fmt(r['fb_drop_vs_full'])} below the full stop) floor={r['fb_floor_state']}"
                  f"{' [' + r['fb_floor_reason'] + ']' if r['fb_floor_reason'] else ''}")
            print(f"  breach at price: unfloored fallback {_yn(r.get('fb_breached_unfloored'))}, "
                  f"floored fallback {_yn(r.get('fb_breached_floored'))}")
            print(f"  close-check: {_drift_text(r, 'ref')}")
            print(f"               {_drift_text(r, 'bind')}")
        if r["u8_permanently_partial"]:
            print(f"  U8: long frame is PERMANENTLY partial -> never gets a floor "
                  f"({r['full_reason']})")
        if r["flags"]:
            print(f"  FLAGS: {' '.join(r['flags'])}")


def print_floor_summary(s: dict, tol: float) -> None:
    print(f"\n=== FLOOR SUMMARY ({s['holdings']} holdings, {s['floor_possible']} can have a floor) ===")
    print(f"  full-day binds (replay): {s['full_day_binds_total']} bind(s) on "
          f"{s['full_day_binds_names']}; max delta {s['full_day_max_delta']:.2f} "
          f"({s['full_day_max_delta_pct']:.3f}% of price)  <- expected: cents only")
    print(f"  binds TODAY: full frame {s['full_binds_today']}; fallback frame {s['fb_binds']} "
          f"(max delta {s['fb_max_delta']:.2f})")
    print(f"  NEWLY_BREACHED_ON_FALLBACK: {len(s['newly_breached'])} {s['newly_breached']}")
    print(f"  entry anchor lost on the fallback frame: {s['anchor_lost']}")
    print(f"  floor state, full frame: {s['full_states']}; fallback frame: {s['fb_states']}")
    print(f"  CLOSE-CHECK DRIFT vs HELD_STOP_FLOOR_CLOSE_TOL_PCT = {tol:g}%  "
          "(fallback frame, today):")
    for name, label in (("ref", "REF session "), ("bind", "BIND session")):
        d = s[name]
        print(f"    {label}: {d['over']} OVER tolerance, {d['within']} within; "
              f"statuses {d['status']}; max |drift| {_fmt(d['max_abs_drift_pct'], 3)}%"
              + (f"; bar ages (sessions) {d['ages']}" if name == "bind" else ""))
    print(f"  U8, permanently partial (never get a floor): {s['u8'] or 'none'}")
    if s["no_floor"]:
        print("  no floor possible: " + "; ".join(f"{t} ({why})" for t, why in s["no_floor"]))


def run_floor(args, holdings, trades):
    """(rows, skipped[(ticker, reason)]). The only network use is what today mode
    already makes: the long Yahoo history, the 6-month bundle, and (--live) live
    prices."""
    from stock_analyzer import data
    pol = hsw._policy()
    if args.trail_start_explicit:
        pol = {**pol, "trail_start": args.trail_start}
    rows, skipped = [], []
    live = {}
    if args.live and len(holdings):
        try:
            live = data.fetch_live_prices([str(t).upper() for t in holdings["Ticker"]]) or {}
        except Exception as e:
            print(f"live prices failed ({e}); falling back to the latest bar")
    for _, h in holdings.iterrows():
        t = str(h["Ticker"]).upper()
        if args.tickers and t not in args.tickers:
            continue
        shares, avg = float(h["Shares"]), float(h["Avg Cost ($)"])
        ep = hs.episode_from_journal(t, trades, shares, shares_tol=SHARES_TOL)
        try:
            start = _iso_date(ep.get("start"))
            long_df = f6 = None
            if ep.get("status") == "ok" and start is not None and start < args.as_of:
                long_df = _long_for(t, ep.get("start"), pol["trail_start"], args.as_of)
                if long_df is None or not len(long_df):
                    skipped.append((t, "no long history returned"))
                    continue
                f6 = data.fetch_ticker_bundle(t, "6mo").get("history")
            lp = _f((live.get(t) or {}).get("price"))
            rows.append(floor_row(t, shares, avg, ep, long_df, f6, args.as_of, pol,
                                  sessions=args.floor_sessions,
                                  bundle_start=args.bundle_start, live_price=lp))
        except Exception as e:
            skipped.append((t, f"{type(e).__name__}: {e}"))
    return rows, skipped


# -- carry mode (SlidingStop C2b) ----------------------------------------------
# The carried-stop dry run (docs/plans/sliding-stop.md section 7 item 4). Pure parts
# (frames in, dicts out) first, then the I/O edge `run_carry`. Read-only: the saved
# carry rows are a dict in memory standing in for the held_stop_carry table.

CARRY_SESSIONS_DEFAULT = 20   # replay depth: a report window, not a gate
CARRY_LATE_OFFSET = 5         # the "late" forced-mismatch start: sessions before the end
CARRY_ABOVE_FULL_TOL = 0.01   # CARRY_ABOVE_FULL report tolerance (one cent), not a gate
CARRY_FLAGS = ("FALSE_BREACH", "CARRY_ABOVE_FULL", "SAVED_A_SLIDE", "MISSED",
               "BREACH_NO_FULL_TRUTH")
# U2 variants once the forced mismatch has started: (the reader can read the journal,
# the EOD writer can). Before the mismatch both read it (production).
CARRY_VARIANTS = {
    "journal_readable": (True, True),         # the real mismatch case: it reads, it disagrees
    "journal_down": (False, False),           # an outage at the reader AND at the EOD run:
                                              # the writer fails closed, the row stops advancing
    "journal_down_at_read": (False, True),    # a read-time outage only: the EOD still advances it
}


def carry_ks(dates: list) -> list:
    """The forced-mismatch start sessions k replayed per holding: the FIRST replayed
    session (no saved row before it: the seed path), the MIDDLE one, and the session
    `CARRY_LATE_OFFSET` before the end (clamped to the first). Sorted, no repeats."""
    n = len(dates)
    if n == 0:
        return []
    return [dates[i] for i in sorted({0, n // 2, max(0, n - CARRY_LATE_OFFSET)})]


def carry_flags(close, truth_stop, truth_basis, unc_stop, carried_stop) -> dict:
    """Breach booleans (the app's own rule, `is_stop_breached`) and the flags for ONE
    session: the stop in force for D against D's own Close.
      FALSE_BREACH          carried breached, the TRUE full-history stop not (gated
                            only when the true basis is full)
      CARRY_ABOVE_FULL      carried > true + one cent (true basis full)
      SAVED_A_SLIDE         true breached, uncarried not, carried breached
      MISSED                true breached, carried not
      BREACH_NO_FULL_TRUTH  carried breached, true not, and the true basis is not
                            full (informational: there is no full-history truth)"""
    tb = bool(is_stop_breached(close, truth_stop))
    ub = bool(is_stop_breached(close, unc_stop))
    cb = bool(is_stop_breached(close, carried_stop))
    full = truth_basis == "full"
    flags = []
    if cb and not tb:
        flags.append("FALSE_BREACH" if full else "BREACH_NO_FULL_TRUTH")
    if (full and truth_stop is not None and carried_stop is not None
            and carried_stop > truth_stop + CARRY_ABOVE_FULL_TOL):
        flags.append("CARRY_ABOVE_FULL")
    if tb and not ub and cb:
        flags.append("SAVED_A_SLIDE")
    if tb and not cb:
        flags.append("MISSED")
    return {"truth_breached": tb, "uncarried_breached": ub, "carried_breached": cb,
            "flags": flags}


def carry_truth(ticker, long_frame, ep, avg_cost, dates, pol) -> dict:
    """{D: effective result} on the REAL episode: the full-history production result
    per session, wrapped like the driver and floored with the floor rows saved so far
    (the C3c writer replayed in memory), NOT carried. This is the TRUE stop series and
    the pre-mismatch production result."""
    rows: dict = {}
    out: dict = {}
    for d in dates:
        res = hs.compute_held_stop(long_frame, ep, avg_cost, d, **pol)
        eff = hsf.apply_floor(hsw._wrap(ticker, res, "", avg_cost, ep=ep), rows, ep=ep,
                              avg_cost=avg_cost, frame=long_frame, as_of=d, readable=True)
        out[d] = eff
        for r in hsf.rows_to_write({ticker: eff}, d):
            rows[_iso_date(r["episode_start"])] = r
    return out


def carry_unreadable(ticker, long_frame, bad_ep, avg_cost, dates, pol) -> dict:
    """{D: effective result} when the journal cannot date the position: the result
    `held_stop_wiring._one`'s non-ok branch gives (`compute_held_stop` with an episode
    whose status is not "ok", wrapped, then `apply_floor`, which drops out for it) on
    the same frame. This is the UNCARRIED prior_session stop."""
    out: dict = {}
    for d in dates:
        res = hs.compute_held_stop(long_frame, bad_ep, avg_cost, d, **pol)
        out[d] = hsf.apply_floor(hsw._wrap(ticker, res, "", avg_cost, ep=bad_ep), {},
                                 ep=bad_ep, avg_cost=avg_cost, frame=long_frame, as_of=d,
                                 readable=True)
    return out


def carry_replay(ticker, frame, truth, bad, avg_cost, jsig, dates, k, variant, closes, *,
                 force_lapse_from=None) -> list:
    """One forced scenario, "the journal stops dating this position from session k",
    replayed over `dates` with the saved carry rows in memory. Sessions < k use
    `truth[D]` (production on the real basis); sessions >= k use `bad[D]` (the
    unreadable-episode result) with the reader / writer journal visibility of
    `CARRY_VARIANTS[variant]`. Per session: `held_stop_carry.apply_carry` with the row
    saved so far, then `rows_to_write` (the seed guard, the fail-closed rules) advances
    the row. `closes` = {D: that session's own Close}. `force_lapse_from` (a test
    hook) hands the carry an average cost $1 higher from that session on, as if an add
    changed the fingerprint. Returns one log dict per session."""
    saved: dict = {}
    sig_now = hsf.policy_sig()
    log: list = []
    for d in dates:
        mis = d >= k
        reader_ok, writer_ok = CARRY_VARIANTS[variant] if mis else (True, True)
        base = bad[d] if mis else truth[d]
        avg_c = avg_cost + 1.0 if (force_lapse_from is not None and d >= force_lapse_from) \
            else avg_cost
        carried = hsc.apply_carry(base, saved.get(ticker), as_of=d, bars=frame,
                                  avg_cost=avg_c, journal_sig=jsig if reader_ok else None,
                                  journal_readable=reader_ok, policy_sig_now=sig_now,
                                  carry_readable=True)
        written = hsc.rows_to_write({ticker: carried}, d, journal_sigs={ticker: jsig},
                                    avg_costs={ticker: avg_c}, prior_rows=dict(saved),
                                    session_closes={ticker: closes.get(d)},
                                    journal_readable=writer_ok, carry_readable=True)
        for r in written:
            saved[r["ticker"]] = r
        close = closes.get(d)
        tr, unc = truth[d], base
        cy = carried.get("carry") or {}
        fl = carry_flags(close, tr.get("stop"), tr.get("basis"), unc.get("stop"),
                         carried.get("stop"))
        diff = (round(carried["stop"] - tr["stop"], 2)
                if carried.get("stop") is not None and tr.get("stop") is not None else None)
        log.append({"ticker": ticker, "k": k, "variant": variant, "as_of": d, "mismatch": mis,
                    "close": close, "truth_stop": tr.get("stop"), "truth_basis": tr.get("basis"),
                    "uncarried_stop": unc.get("stop"), "uncarried_basis": unc.get("basis"),
                    "carried_stop": carried.get("stop"), "carried_minus_truth": diff,
                    "carry_state": cy.get("state"), "carry_reason": cy.get("reason", ""),
                    "carry_code": hsf._code(cy.get("reason", "")) if cy.get("state") ==
                    hsc.LAPSED else "", "carry_binding": cy.get("binding"),
                    "truth_breached": fl["truth_breached"],
                    "uncarried_breached": fl["uncarried_breached"],
                    "carried_breached": fl["carried_breached"],
                    "wrote": bool(written), "flags": fl["flags"]})
    return log


def carry_holding(ticker, shares, avg_cost, ep, long_frame, as_of: date, pol, *,
                  journal_sig, sessions=CARRY_SESSIONS_DEFAULT, ks=None,
                  force_lapse_from=None) -> dict:
    """Everything the carry dry run says about ONE holding (frames in, dict out; no
    network). `skipped` is a reason text when the holding can't be exercised, else
    `scenarios` = one dict per (k, variant): k, variant, log, counts (flags), states,
    lapses (reason codes)."""
    ep = ep or {}
    start = _iso_date(ep.get("start"))
    out = {"ticker": ticker, "shares": shares, "avg_cost": avg_cost, "as_of": as_of,
           "episode_status": ep.get("status"), "episode_start": start,
           "last_buy": _iso_date(ep.get("last_buy")), "skipped": "", "dates": [], "ks": [],
           "scenarios": []}
    if ep.get("status") != "ok" or start is None:
        out["skipped"] = f"no usable episode ({ep.get('status')}: {ep.get('reason')})"
        return out
    if start >= as_of:
        out["skipped"] = "bought today (buy_day basis, never carried)"
        return out
    if journal_sig is None:
        out["skipped"] = "journal fingerprint unavailable (no row could be written)"
        return out
    if not _usable(long_frame):
        out["skipped"] = "no usable long price history"
        return out
    dates = replay_dates(long_frame, ep, as_of, int(sessions))
    if not dates:
        out["skipped"] = "no completed session after the episode start / last buy"
        return out
    truth = carry_truth(ticker, long_frame, ep, avg_cost, dates, pol)
    if all(truth[d].get("stop") is None for d in dates):
        out["skipped"] = (f"no stop could be built on any replayed session (fewer than "
                          f"{MIN_BARS} completed bars: HELD_STOP_MIN_BARS)")
        return out
    bad_ep = hs._episode("mismatch", "forced scenario: the journal stops dating this position",
                         holdings_shares=shares)
    bad = carry_unreadable(ticker, long_frame, bad_ep, avg_cost, dates, pol)
    bars = hs.completed_bars(long_frame, as_of)
    closes = {i.date(): _f(c) for i, c in zip(bars.index, bars["Close"])}
    ks = list(ks) if ks else carry_ks(dates)
    out["dates"], out["ks"] = dates, ks
    for k in ks:
        for variant in CARRY_VARIANTS:
            log = carry_replay(ticker, long_frame, truth, bad, avg_cost, journal_sig, dates,
                               k, variant, closes, force_lapse_from=force_lapse_from)
            out["scenarios"].append({
                "k": k, "variant": variant, "log": log,
                "counts": Counter(f for e in log for f in e["flags"]),
                "states": Counter(e["carry_state"] for e in log),
                "lapses": Counter(e["carry_code"] for e in log if e["carry_code"])})
    return out


def summarize_carry(rows: list[dict]) -> dict:
    """Counts over the carry rows: per-flag totals, the detail of every FALSE_BREACH /
    CARRY_ABOVE_FULL / SAVED_A_SLIDE / MISSED session, holdings exercised and skipped
    (with the reason), lapse reason codes per holding, and the gate."""
    ex = [r for r in rows if not r.get("skipped")]
    flag_counts: Counter = Counter()
    detail = {f: [] for f in CARRY_FLAGS}
    lapses: dict = {}
    states: Counter = Counter()
    sessions = 0
    for r in ex:
        for sc in r["scenarios"]:
            for e in sc["log"]:
                sessions += 1
                states[e["carry_state"]] += 1
                if e["carry_code"]:
                    lapses.setdefault(r["ticker"], Counter())[e["carry_code"]] += 1
                for f in e["flags"]:
                    flag_counts[f] += 1
                    detail[f].append(e)
    return {
        "holdings": len(rows), "exercised": sorted(r["ticker"] for r in ex),
        "skipped": sorted((r["ticker"], r["skipped"]) for r in rows if r.get("skipped")),
        "scenario_sessions": sessions, "flag_counts": dict(flag_counts), "detail": detail,
        "lapses": {t: dict(c) for t, c in sorted(lapses.items())}, "states": dict(states),
        "false_breaches": len(detail["FALSE_BREACH"]),
    }


def carry_gate_line(s: dict) -> str:
    """The one-line verdict. A run that exercised nothing proves nothing, so it is
    NOT PASS."""
    if not s["exercised"]:
        return "GATE: NOT EXERCISED (no holding could be replayed)"
    n = s["false_breaches"]
    if n == 0:
        return "GATE: PASS (0 false breaches)"
    shown = [f"{e['ticker']} k={e['k']} {e['variant']} {e['as_of']} "
             f"carried {_fmt(e['carried_stop'])} vs close {_fmt(e['close'])}, "
             f"true {_fmt(e['truth_stop'])}" for e in s["detail"]["FALSE_BREACH"][:10]]
    more = f" (+{n - 10} more)" if n > 10 else ""
    return f"GATE: FAIL ({n} false breaches: " + "; ".join(shown) + more + ")"


def flatten_carry(rows: list[dict]) -> list[dict]:
    """CSV rows: one per replayed session per scenario (flags joined with |), plus one
    `skipped` row per holding that could not be replayed."""
    out = []
    for r in rows:
        if r.get("skipped"):
            out.append({"record": "skipped", "ticker": r["ticker"], "reason": r["skipped"]})
            continue
        for sc in r["scenarios"]:
            for e in sc["log"]:
                out.append({"record": "session", **e, "flags": "|".join(e["flags"])})
    return out


def print_carry_header(args, n_holdings: int, pol: dict) -> None:
    print("=== HELD-STOP CARRY DRY RUN (SlidingStop C2b; read-only) ===")
    print(f"as_of={args.as_of}  holdings={n_holdings}  replay sessions={args.carry_sessions}  "
          f"policy = production (held_stop_wiring._policy): trail_start={pol['trail_start']}"
          f"{' (explicit --trail-start)' if args.trail_start_explicit else ' (HELD_TRAIL_START)'}")
    print("scenario per holding: 'the journal stops dating this position from session k' "
          "for k = the first replayed session, the middle one and "
          f"{CARRY_LATE_OFFSET} sessions before the end. Sessions < k: the production "
          "result on the real basis (floored, then the carry), the EOD writer saves rows in "
          "memory. Sessions >= k: the result _one's non-ok branch gives on the same frame "
          "(the UNCARRIED prior_session stop), then held_stop_carry.apply_carry with the "
          "saved row, then rows_to_write advances it.")
    print("U2 variants after k: " + "; ".join(
        f"{v} (reader {'reads' if rd else 'cannot read'} the journal, EOD writer "
        f"{'reads' if wr else 'cannot read'} it)" for v, (rd, wr) in CARRY_VARIANTS.items()))
    print("per session: the stop in force for D against D's own Close, the app's rule "
          "is_stop_breached. TRUE = the real-episode full-history production stop (floored, "
          "not carried).")
    print("flags: FALSE_BREACH (carried breached, true not; the gate, true basis full) | "
          f"CARRY_ABOVE_FULL (carried > true + {CARRY_ABOVE_FULL_TOL:g}) | SAVED_A_SLIDE (true "
          "breached, uncarried not, carried breached) | MISSED (true breached, carried not) | "
          "BREACH_NO_FULL_TRUTH (as FALSE_BREACH but the true basis is not full; not gated)")
    print("approximations: the long unadjusted frame stands in for the app's 6-month bundle "
          "on the unreadable-episode result; today's average cost / episode / journal "
          "fingerprint are used for every session; the floor rows are replayed in memory.")
    print()


def _carry_line(e: dict) -> str:
    state = e["carry_state"] + (f": {e['carry_code']}" if e["carry_code"] else "")
    return (f"    {e['as_of']} {'MIS' if e['mismatch'] else 'ok '} close {_fmt(e['close']):>8} "
            f"true {_fmt(e['truth_stop']):>8} ({e['truth_basis']}) "
            f"uncarried {_fmt(e['uncarried_stop']):>8} carried {_fmt(e['carried_stop']):>8} "
            f"[{state}]{' wrote' if e['wrote'] else ''}"
            f"{'  ' + ' '.join(e['flags']) if e['flags'] else ''}")


def print_carry(rows: list[dict], detail: bool = False) -> None:
    for r in rows:
        print(f"\n{r['ticker']}  {r['shares']:g} sh @ {_fmt(r['avg_cost'])}  episode "
              f"{r['episode_status']} start={r['episode_start']} last_buy={r['last_buy']}")
        if r["skipped"]:
            print(f"  skipped: {r['skipped']}")
            continue
        print(f"  replay: {len(r['dates'])} sessions {r['dates'][0]} -> {r['dates'][-1]}; "
              f"forced-mismatch starts k = {[str(k) for k in r['ks']]}")
        for sc in r["scenarios"]:
            flags = " ".join(f"{f}={n}" for f, n in sorted(sc["counts"].items())) or "no flags"
            print(f"  k={sc['k']} {sc['variant']}: states {dict(sc['states'])}; lapses "
                  f"{dict(sc['lapses']) or 'none'}; {flags}")
            for e in sc["log"]:
                if detail or e["flags"] or e["carry_code"]:
                    print(_carry_line(e))


def print_carry_summary(s: dict) -> None:
    print(f"\n=== CARRY SUMMARY ({s['holdings']} holdings, {len(s['exercised'])} replayed, "
          f"{s['scenario_sessions']} scenario-sessions) ===")
    print(f"  holdings exercised: {s['exercised'] or 'none'}")
    for t, why in s["skipped"]:
        print(f"  skipped {t}: {why}")
    print("  flag counts: " + (", ".join(f"{f}={s['flag_counts'].get(f, 0)}" for f in CARRY_FLAGS)))
    print(f"  carry states over all sessions: {s['states']}")
    print(f"  lapses observed (reason codes) per holding: {s['lapses'] or 'none'}")
    for f in ("CARRY_ABOVE_FULL", "FALSE_BREACH"):
        for e in s["detail"][f]:
            print(f"  {f}: {e['ticker']} k={e['k']} {e['variant']} {e['as_of']} carried "
                  f"{_fmt(e['carried_stop'])} true {_fmt(e['truth_stop'])} "
                  f"(diff {_fmt(e['carried_minus_truth'])}) close {_fmt(e['close'])}")
    print(f"  {carry_gate_line(s)}")


def run_carry(args, holdings, trades):
    """(rows, skipped[(ticker, reason)]). The only network use is the long Yahoo
    history per holding (no live prices, no 6-month bundle)."""
    pol = hsw._policy()
    if args.trail_start_explicit:
        pol = {**pol, "trail_start": args.trail_start}
    rows, skipped = [], []
    for _, h in holdings.iterrows():
        t = str(h["Ticker"]).upper()
        if args.tickers and t not in args.tickers:
            continue
        shares, avg = float(h["Shares"]), float(h["Avg Cost ($)"])
        ep = hs.episode_from_journal(t, trades, shares, shares_tol=SHARES_TOL)
        try:
            start = _iso_date(ep.get("start"))
            long_df = None
            if ep.get("status") == "ok" and start is not None and start < args.as_of:
                long_df = _long_for(t, ep.get("start"), pol["trail_start"], args.as_of)
                if long_df is None or not len(long_df):
                    skipped.append((t, "no long history returned"))
                    continue
            rows.append(carry_holding(t, shares, avg, ep, long_df, args.as_of, pol,
                                      journal_sig=hsc.journal_buy_sig(trades, t),
                                      sessions=args.carry_sessions))
        except Exception as e:
            skipped.append((t, f"{type(e).__name__}: {e}"))
    return rows, skipped


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--today", action="store_true")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--live", action="store_true", help="price today's gaps with live prices")
    ap.add_argument("--csv", default="")
    ap.add_argument("--tickers", default="")
    ap.add_argument("--since", default="", help="replay: round trips exiting on/after")
    ap.add_argument("--as-of", default="", dest="as_of", help="replay only; today mode needs today")
    ap.add_argument("--trail-start", default="", dest="trail_start")
    ap.add_argument("--mults", default="")
    ap.add_argument("--replay-variants", default="a", dest="replay_variants")
    ap.add_argument("--variant", default="c", help="variant in the compact today table")
    ap.add_argument("--detail", action="store_true", help="print per-cell blocks too")
    ap.add_argument("--ladder-period", default="2y", dest="ladder_period")
    ap.add_argument("--floor", action="store_true",
                    help="persisted-floor dry run (C3c-2); excludes --today / --replay")
    ap.add_argument("--floor-sessions", type=int, default=FLOOR_SESSIONS_DEFAULT,
                    dest="floor_sessions", help="floor mode: sessions of EOD writes to replay")
    ap.add_argument("--bundle-start", default="", dest="bundle_start",
                    help="floor mode: truncate the fallback (6-month bundle) frame to start "
                         "on this ET date, to simulate a later rollover")
    ap.add_argument("--carry", action="store_true",
                    help="carried-stop dry run (SlidingStop C2b); excludes --today / "
                         "--replay / --floor")
    ap.add_argument("--carry-sessions", type=int, default=CARRY_SESSIONS_DEFAULT,
                    dest="carry_sessions", help="carry mode: sessions of EOD writes to replay")
    args = ap.parse_args(argv)

    from stock_analyzer.market_time import today_et
    args.tickers = {t.strip().upper() for t in args.tickers.split(",") if t.strip()}
    args.as_of = _parse_date(args.as_of) if args.as_of else today_et()
    args.trail_start_explicit = bool(args.trail_start)
    args.trail_start = _parse_date(args.trail_start) if args.trail_start else args.as_of
    args.since = _parse_date(args.since) if args.since else None
    args.mults = (tuple(float(x) for x in args.mults.split(",") if x.strip())
                  if args.mults else DEFAULT_MULTS)
    args.replay_variants = tuple(v for v in args.replay_variants.split(",") if v in VARIANTS) or ("a",)
    if args.variant not in VARIANTS:
        args.variant = "c"
    args.bundle_start = _parse_date(args.bundle_start) if args.bundle_start else None
    if args.floor and (args.today or args.replay):
        print("--floor is its own mode: don't combine it with --today / --replay.")
        return 2
    if args.floor and args.floor_sessions < 1:
        print("--floor-sessions must be at least 1.")
        return 2
    if args.floor and args.bundle_start is not None and args.bundle_start >= args.as_of:
        print(f"--bundle-start {args.bundle_start} must be before the as-of date {args.as_of}: "
              "the fallback frame would hold no completed bar.")
        return 2
    if args.carry and (args.today or args.replay or args.floor):
        print("--carry is its own mode: don't combine it with --today / --replay / --floor.")
        return 2
    if args.carry and args.carry_sessions < 1:
        print("--carry-sessions must be at least 1.")
        return 2
    do_today = (args.today or not args.replay) and not args.floor and not args.carry
    if (do_today or args.floor or args.carry) and args.as_of != today_et():
        print("--as-of other than today is refused in today mode: holdings, average cost and "
              "manual stops are as of now, so a past as-of would mix two dates.")
        return 2

    from stock_analyzer import db
    db.set_readonly(True)
    if not db.has_db():
        print("No Supabase credentials (SUPABASE_URL/SUPABASE_KEY) -- cannot run.")
        return 1
    trades = db.load_trades_or_none()
    if trades is None:
        print("Trade journal could not be read; nothing computed.")
        return 1
    holdings = db.load_holdings_or_none() if (do_today or args.floor or args.carry) else None
    if (do_today or args.floor or args.carry) and holdings is None:
        print("Holdings could not be read; nothing computed.")
        return 1
    csv_rows: list[dict] = []
    if args.carry:
        # the carry sits on the AUTO stop: manual stops are not part of it, not loaded
        pol = hsw._policy()
        if args.trail_start_explicit:
            pol = {**pol, "trail_start": args.trail_start}
        print_carry_header(args, len(holdings), pol)
        rows, skipped = run_carry(args, holdings, trades)
        print(f"holdings computed: {len(rows)}; skipped: {len(skipped)}"
              + "".join(f"\n  skipped {t}: {why}" for t, why in skipped))
        print_carry(rows, args.detail)
        s = summarize_carry(rows)
        print_carry_summary(s)
        csv_rows += flatten_carry(rows)
        if args.csv:
            _write_csv(args.csv, csv_rows)
        return 0
    if args.floor:
        # manual stops are not part of the floor (it floors the AUTO stop): not loaded
        pol = hsw._policy()
        if args.trail_start_explicit:
            pol = {**pol, "trail_start": args.trail_start}
        print_floor_header(args, len(holdings), pol)
        rows, skipped = run_floor(args, holdings, trades)
        print(f"holdings computed: {len(rows)}; skipped: {len(skipped)}"
              + "".join(f"\n  skipped {t}: {why}" for t, why in skipped))
        print_floor(rows)
        print_floor_summary(summarize_floor(rows), float(_C.HELD_STOP_FLOOR_CLOSE_TOL_PCT))
        csv_rows += flatten_floor(rows)
        if args.csv:
            _write_csv(args.csv, csv_rows)
        return 0
    manual = db.load_manual_stops_or_none() if do_today else {}
    print_header(args, len(holdings) if holdings is not None else 0,
                 None if manual is None else len(manual), do_today)
    if manual is None:
        manual = {}
    if do_today:
        rows, skipped = run_today(args, holdings, trades, manual)
        print(f"holdings computed: {len(rows)}; skipped: {len(skipped)}"
              + "".join(f"\n  skipped {t}: {why}" for t, why in skipped))
        print_compact(rows, args.variant, args.mults)
        if args.detail:
            print_today(rows)
        print_today_summary(summarize_today(rows), len(rows))
        csv_rows += flatten_today(rows)
    if args.replay:
        rep = run_replay(args, trades)
        print_replay_summary(summarize_replay(rep))
        csv_rows += [{**r, "basis_counts": dict(r["basis_counts"])} for r in rep]
    if args.csv:
        _write_csv(args.csv, csv_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
