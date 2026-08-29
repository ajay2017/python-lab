#!/usr/bin/env python3
"""Replay the exit-deterioration ladder against the owner's own realized losses.

App-review Innovation #2 (docs/reviews/2026-08-26-app-review.md, "Replay the
exit ladder against the owner's own realized losses"). The premise under
test: "the ladder would have saved me, I sold too late." `ATR_STOP_MULT` and
the `DETERIORATION_*` ladder have never been checked against a loss this
owner actually took -- Forward Simulator Phase 2 (see CLAUDE.md's queue)
waits on `PROTECT_TRACK_MIN_CALLS` matured protective calls before it can
even start answering this, and that metric "can only accumulate in the
regime where the question matters least." The data to answer a sharper
version already exists: closed losing round trips in `trades`, plus price
history.

WHAT THIS DOES. For every closed, single-lot, losing round trip (one BUY
fully closed by one SELL, at a loss) it replays BOTH named mechanisms
day-by-day across the ACTUAL held window: the deterioration ladder via
`exit_advisor.assess_holding` (the SAME pure scalar core the live Daily
Brief uses, not a re-implementation), and the ATR_STOP_MULT stop via
`portfolio.protective_stop` (the SAME formula the live "Stop" column uses --
also not a re-implementation, and also memoryless, so replaying it fresh
per day is exactly what the live app itself does on every render). Reports,
per tier (WATCH/TRIM/EXIT) and for the stop, the date it would have first
fired, against the date the owner actually sold -- both as a ladder-only
tally (the review's original framing) and a combined "either mechanism"
tally, since they are structurally different (the ladder needs multi-day
confirmation; the stop is a single-day check).

REDLINE. Read-only historical measurement. Touches no gate, no constant, no
recommendation. If the results argue for a threshold change, that is a
separate policy conversation with the owner -- this script produces
evidence, not a recommendation.

HONEST CAVEAT, printed on every run: the sample is small and
survivorship-shaped -- it can only see positions that were CLOSED, so it
says nothing about positions still open or never entered. Read N before the
table, not just the table.

FALSIFIABLE SIGNAL. If across the closed losers the ladder's earliest-fired
date is NOT systematically earlier than the actual sell date, the premise
("the ladder would have saved me, I sold too late") is false -- the exit
ladder is not the lever, and Forward-Simulator Phase 2 should be closed
rather than waited on. This script reports the tally; it does not decide
that call.

Requires:
  - Supabase credentials (SUPABASE_URL / SUPABASE_KEY) to read `trades` --
    this project's DB is hosted-only (see CLAUDE.md), so run this with the
    same env vars Railway uses. Per `backfill_vol_predictions.py`'s own
    note: Railway's Console shell is NOT a usable environment for this
    (minimal PATH, no app deps) -- run from a normal shell with the same
    Supabase env vars set instead.
  - Public daily price history via yfinance -- no credentials needed.
  - `requests` for the trades read (already a project dependency) -- this
    script talks to Supabase's PostgREST endpoint directly rather than via
    the `supabase` SDK, deliberately: the SDK pulls in `pyiceberg`, which
    has no prebuilt wheel for newer Python/Windows combos and needs a C
    toolchain to build from source, for a single read-only GET this
    script has no other reason to need the SDK's full weight for.

Usage:
    python scripts/exit_ladder_replay.py              # every closed losing round trip
    python scripts/exit_ladder_replay.py --ticker MU  # restrict to one ticker
    python scripts/exit_ladder_replay.py --period 5y  # longer price-history fetch
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import data as _data  # noqa: E402
from stock_analyzer import exit_advisor  # noqa: E402
from stock_analyzer import portfolio as _portfolio  # noqa: E402
from stock_analyzer.constants import ATR_STOP_MULT  # noqa: E402
from stock_analyzer.indicators import atr as _atr_series  # noqa: E402
from stock_analyzer.technicals import compute_indicators  # noqa: E402
from stock_analyzer.trade_review import _pair_sells_to_buys  # noqa: E402

_TIERS = ("WATCH", "TRIM", "EXIT")
_SIGNALS = _TIERS + ("STOP",)


def _f(v, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        x = float(v)
        return default if x != x else x  # NaN check
    except (TypeError, ValueError):
        return default


def _to_date(v) -> date | None:
    if v is None:
        return None
    try:
        return v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def closed_losing_round_trips(trades_df, ticker: str | None = None) -> list[dict]:
    """Every closed, SINGLE-LOT, losing round trip in `trades_df`.

    "Single-lot" = one BUY fully closed by exactly one SELL -- the smallest
    honest version, deliberately not attempting partial-fill / multi-add
    round trips (those need a P&L attribution call this script isn't making).
    FIFO matching is reused from `trade_review._pair_sells_to_buys` rather
    than re-implemented, so this can never silently disagree with what the
    Trade Review page itself would call a round trip.
    """
    want = ticker.strip().upper() if ticker else None
    rows: list[dict] = []
    if trades_df is not None:
        for _, r in trades_df.iterrows():
            act = str(r.get("action", "") or "").upper()
            if "BUY" not in act and "SELL" not in act:
                continue
            tk = str(r.get("ticker", "") or "").strip().upper()
            if not tk or (want and tk != want):
                continue
            td = _to_date(r.get("traded_at"))
            if td is None:
                continue
            rows.append({
                "id": r.get("id"), "ticker": tk, "action": act,
                "shares": _f(r.get("shares")), "price": _f(r.get("price")),
                "_trade_date": td,
            })

    pairing = _pair_sells_to_buys(rows)
    by_id = {r["id"]: r for r in rows}

    out: list[dict] = []
    for buy_id, m in pairing["matches"].items():
        if m["shares_remaining"] > 1e-9:
            continue  # not fully closed
        if len(m["matched"]) != 1:
            continue  # partial-fill / multi-sell round trip -- out of scope here
        buy = by_id.get(buy_id)
        if buy is None or buy["price"] <= 0:
            continue
        sell = m["matched"][0]
        shares = sell["shares"]
        pnl = (sell["sell_price"] - buy["price"]) * shares
        if pnl >= 0:
            continue  # winners are out of scope for this replay
        out.append({
            "ticker": buy["ticker"],
            "entry_date": buy["_trade_date"],
            "entry_price": buy["price"],
            "shares": shares,
            "exit_date": sell["sell_date"],
            "exit_price": sell["sell_price"],
            "realized_pnl": pnl,
        })
    out.sort(key=lambda rt: rt["exit_date"], reverse=True)
    return out


def replay_round_trip(rt: dict, period: str = "2y", spy_hist=None) -> dict:
    """Add first-fired-per-tier dates to `rt`, or an `error` string.

    Every scalar is re-derived day-by-day from real price history via
    `exit_advisor.assess_holding` -- the live engine's own pure scalar core,
    not a re-implementation -- called on a df TRUNCATED to each day so no
    call can see a future bar (`.tail(...)`-based windows inside
    `assess_holding` would otherwise read past the replay date).

    `spy_hist` lets `main()` fetch the benchmark ONCE and reuse it across
    every round trip -- SPY doesn't change per ticker, and re-fetching it
    per round trip was needlessly slow (a real symptom on a multi-round-trip
    account: the SPY fundamentals 404 -- harmless, but repeated -- printed
    once per round trip, with no other progress signal). Fetches its own
    copy when called standalone (e.g. from a test) with `spy_hist=None`.
    """
    ticker = rt["ticker"]
    try:
        bundle = _data.fetch_ticker_bundle(ticker, period)
        if spy_hist is None:
            spy_bundle = _data.fetch_ticker_bundle("SPY", period)
            spy_hist = spy_bundle.get("history") if isinstance(spy_bundle, dict) else None
    except Exception as e:
        return {**rt, "error": f"price history fetch failed: {e}"}

    hist = bundle.get("history") if isinstance(bundle, dict) else None
    if hist is None or hist.empty or spy_hist is None or spy_hist.empty:
        return {**rt, "error": "no price history returned"}

    df = compute_indicators(hist)
    atr_full = _atr_series(df["High"], df["Low"], df["Close"], length=14)

    # Re-key to plain dates so `.loc[:d]` slices on the SAME calendar the
    # trades table uses -- positional rolling/ewm math above is unaffected
    # by the re-key (it already ran on the original DatetimeIndex).
    df = df.copy()
    df.index = [ts.date() if hasattr(ts, "date") else ts for ts in df.index]
    atr_full.index = df.index
    spy_hist = spy_hist.copy()
    spy_hist.index = [ts.date() if hasattr(ts, "date") else ts for ts in spy_hist.index]

    entry_date, exit_date = rt["entry_date"], rt["exit_date"]
    trading_days = [d for d in df.index if entry_date <= d <= exit_date]
    if not trading_days:
        return {**rt, "error": "no trading days found in the held window"}

    first_fired: dict[str, date | None] = {t: None for t in _TIERS}
    first_stop_breach: date | None = None
    for d in trading_days:
        df_slice = df.loc[:d]
        if df_slice.empty:
            continue
        spy_slice = spy_hist.loc[:d]
        price_d = float(df_slice["Close"].iloc[-1])
        atr_slice = atr_full.loc[:d]
        atr_val = float(atr_slice.iloc[-1]) if len(atr_slice) else None
        age_days = (d - entry_date).days

        result = exit_advisor.assess_holding(
            ticker, df_slice, spy_slice,
            price=price_d, atr=atr_val,
            avg_cost=rt["entry_price"], shares=rt["shares"],
            age_days=age_days,
        )
        if result is not None:
            tier = result["tier"]
            if first_fired.get(tier) is None:
                first_fired[tier] = d

        # ATR_STOP_MULT stop, replayed via the SAME live formula the app's own
        # "Stop" column uses (portfolio.protective_stop: max(raw ATR stop, the
        # profit-ratchet floor)) -- but set from YESTERDAY's close/ATR and
        # checked against TODAY's price action, not same-day-vs-same-day.
        # That ordering matters: the Brief sets/displays a position's stop
        # each morning from the prior close, then a breach is today's price
        # falling through a level that was already fixed before today's move
        # -- so a stop derived from TODAY's own post-move ATR would let a
        # single sharp drop inflate the ATR at the exact moment it needs to
        # trigger, "chasing" the move and never registering a breach. Caught
        # by a synthetic single-day-crash test during development: a same-day
        # version of this check never fired on a 30% single-day gap-down.
        #
        # Checked against the day's LOW, not just its Close: a real stop
        # order triggers the instant price crosses it intraday, not only at
        # end of day. A close-only check would miss a breach-then-recovery
        # day entirely and understate how early the stop would really have
        # fired -- the deterioration ladder above is legitimately a
        # once-daily (Brief-render-time) read, but a stop-loss is not.
        if first_stop_breach is None and len(df_slice) >= 2 and len(atr_slice) >= 2:
            prev_close = float(df_slice["Close"].iloc[-2])
            _raw_atr_prev = float(atr_slice.iloc[-2])
            atr_prev = _raw_atr_prev if _raw_atr_prev == _raw_atr_prev else None  # NaN check
            if atr_prev and prev_close:
                raw_atr_stop = round(prev_close - ATR_STOP_MULT * atr_prev, 2)
                stop_price, _ = _portfolio.protective_stop(prev_close, rt["entry_price"], raw_atr_stop)
                _low_d = df_slice["Low"].iloc[-1] if "Low" in df_slice.columns else None
                low_d = float(_low_d) if _low_d is not None and _low_d == _low_d else price_d
                if stop_price and low_d <= stop_price:
                    first_stop_breach = d

    all_signals = dict(first_fired)
    all_signals["STOP"] = first_stop_breach

    earliest_date = min((v for v in first_fired.values() if v is not None), default=None)
    earliest_tier = next((t for t in _TIERS if first_fired[t] == earliest_date), None) if earliest_date else None
    earliest_date_any = min((v for v in all_signals.values() if v is not None), default=None)
    earliest_signal_any = (
        next((t for t in _SIGNALS if all_signals[t] == earliest_date_any), None)
        if earliest_date_any else None
    )

    return {
        **rt,
        "error": None,
        **{f"first_{t.lower()}": first_fired[t] for t in _TIERS},
        "first_stop": first_stop_breach,
        "earliest_tier": earliest_tier,
        "earliest_date": earliest_date,
        "fired_before_sale": earliest_date is not None and earliest_date < exit_date,
        "lead_days": (exit_date - earliest_date).days if earliest_date else None,
        "earliest_signal_any": earliest_signal_any,
        "earliest_date_any": earliest_date_any,
        "fired_before_sale_any": earliest_date_any is not None and earliest_date_any < exit_date,
        "lead_days_any": (exit_date - earliest_date_any).days if earliest_date_any else None,
    }


def _fmt(d) -> str:
    return d.strftime("%Y-%m-%d") if d else "never"


def _print_report(replayed: list[dict]) -> None:
    n = len(replayed)
    print(
        f"\n{'=' * 78}\n"
        f"HONEST CAVEAT: N = {n} closed, single-lot, LOSING round trip(s). "
        f"This can only see\npositions that were CLOSED -- it says nothing "
        f"about positions still open or never\nentered, and a small N here "
        f"is not evidence the ladder generalizes.\n{'=' * 78}\n"
    )
    if n == 0:
        print("No qualifying closed losing round trips found. Nothing to replay.")
        return

    header = (f"{'TICKER':<8}{'ENTRY':<12}{'ACTUAL SELL':<14}{'WATCH':<12}{'TRIM':<12}"
               f"{'EXIT':<12}{'STOP':<12}{'LEAD (any)':<12}")
    print(header)
    print("-" * len(header))
    fired_before_ladder, fired_before_any, never_any, same_day_any, errored = 0, 0, 0, 0, 0
    for rt in replayed:
        if rt.get("error"):
            errored += 1
            print(f"{rt['ticker']:<8}{_fmt(rt['entry_date']):<12}{_fmt(rt['exit_date']):<14}"
                  f"-- {rt['error']}")
            continue
        lead_any = rt["lead_days_any"]
        if rt["fired_before_sale_any"]:
            lead_any_str = f"{lead_any:+d}"
            fired_before_any += 1
        elif lead_any == 0:
            lead_any_str = "0"
            same_day_any += 1
        else:
            lead_any_str = "never"
            never_any += 1
        if rt["fired_before_sale"]:
            fired_before_ladder += 1
        print(f"{rt['ticker']:<8}{_fmt(rt['entry_date']):<12}{_fmt(rt['exit_date']):<14}"
              f"{_fmt(rt['first_watch']):<12}{_fmt(rt['first_trim']):<12}"
              f"{_fmt(rt['first_exit']):<12}{_fmt(rt['first_stop']):<12}{lead_any_str:<12}")

    evaluable = n - errored
    print("-" * len(header))
    print(
        f"\nLADDER ONLY (WATCH/TRIM/EXIT): of {evaluable} evaluable round trip(s), fired "
        f"BEFORE the actual sale in\n{fired_before_ladder} "
        f"({(fired_before_ladder / evaluable * 100.0) if evaluable else 0:.0f}%). "
        f"This is the app-review's original falsifiable signal -- \"the\nladder would "
        f"have saved me, I sold too late.\""
    )
    print(
        f"\nLADDER + ATR_STOP_MULT STOP combined: fired before the sale in "
        f"{fired_before_any} ({(fired_before_any / evaluable * 100.0) if evaluable else 0:.0f}%), "
        f"same-day-only in\n{same_day_any} "
        f"({(same_day_any / evaluable * 100.0) if evaluable else 0:.0f}%), never in "
        f"{never_any} ({(never_any / evaluable * 100.0) if evaluable else 0:.0f}%). "
        f"STOP replays the LIVE\n\"Stop\" column's own formula "
        f"(portfolio.protective_stop: max(raw ATR stop, the current profit-ratchet\n"
        f"floor), set from the PRIOR close/ATR and checked against today's INTRADAY LOW "
        f"(not just the close --\na real stop order triggers the instant price crosses "
        f"it, so a close-only check would miss a\nbreach-then-recovery day) -- not "
        f"a same-day, self-\nreferential check (an earlier version of this script was; "
        f"a synthetic single-day-crash test caught it\nnever firing). A short holding "
        f"period (a few days) structurally limits how early EITHER\nmechanism can fire; "
        f"the ladder needs multiple sessions to confirm a trend break, and a position\n"
        f"held only a few days gives neither mechanism much room to have fired before "
        f"the actual sale.\n"
    )
    if errored:
        print(f"({errored} round trip(s) could not be replayed -- see the error column above.)\n")


def load_trades() -> "pd.DataFrame | None":
    """Trades via Supabase's PostgREST endpoint directly, bypassing the
    `supabase` SDK -- see the module docstring's `Requires` section for why.

    Returns `None` when credentials are absent, or when the request itself
    fails (network, auth, RLS) -- distinguished by `main()`'s own message,
    not collapsed here. An empty-but-successful response is a genuinely
    empty `trades` table, returned as an empty `DataFrame`, not `None`.
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/rest/v1/trades",
            params={"select": "*", "order": "traded_at.desc"},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        print(f"Could not read the trades table: {e}")
        return None
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--ticker", default=None, help="restrict to one ticker")
    parser.add_argument("--period", default="2y", help="yfinance history period to fetch (default: 2y)")
    args = parser.parse_args()

    trades_df = load_trades()
    if trades_df is None:
        print(
            "No trades could be read. Either Supabase credentials "
            "(SUPABASE_URL / SUPABASE_KEY) are not set in this shell's "
            "environment, or the request itself failed -- see any error above."
        )
        return 1
    if trades_df.empty:
        print("Connected fine -- the trades table is genuinely empty. Nothing to replay.")
        return 0

    candidates = closed_losing_round_trips(trades_df, ticker=args.ticker)
    print(f"Found {len(candidates)} closed losing round trip(s) to replay.\n")

    print(f"Fetching SPY benchmark history ({args.period})...")
    try:
        _spy_bundle = _data.fetch_ticker_bundle("SPY", args.period)
        spy_hist = _spy_bundle.get("history") if isinstance(_spy_bundle, dict) else None
    except Exception as e:
        print(f"SPY fetch failed ({e}) -- each round trip will retry it individually.")
        spy_hist = None

    replayed = []
    for i, rt in enumerate(candidates, 1):
        print(f"[{i}/{len(candidates)}] Replaying {rt['ticker']} "
              f"({rt['entry_date']} -> {rt['exit_date']})...")
        replayed.append(replay_round_trip(rt, period=args.period, spy_hist=spy_hist))
    print()
    _print_report(replayed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
