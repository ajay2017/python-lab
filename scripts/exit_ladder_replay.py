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
fully closed by one SELL, at a loss) it replays
`exit_advisor.classify_deterioration_tier` day-by-day across the ACTUAL held
window via `exit_advisor.assess_holding` -- the SAME pure scalar core the
live Daily Brief uses, not a re-implementation -- and reports, per tier
(WATCH/TRIM/EXIT), the date it would have first fired, against the date the
owner actually sold.

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

Usage:
    python scripts/exit_ladder_replay.py              # every closed losing round trip
    python scripts/exit_ladder_replay.py --ticker MU  # restrict to one ticker
    python scripts/exit_ladder_replay.py --period 2y  # shorter price-history fetch
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import data as _data  # noqa: E402
from stock_analyzer import db, exit_advisor  # noqa: E402
from stock_analyzer.indicators import atr as _atr_series  # noqa: E402
from stock_analyzer.technicals import compute_indicators  # noqa: E402
from stock_analyzer.trade_review import _pair_sells_to_buys  # noqa: E402

_TIERS = ("WATCH", "TRIM", "EXIT")


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


def replay_round_trip(rt: dict, period: str = "5y") -> dict:
    """Add first-fired-per-tier dates to `rt`, or an `error` string.

    Every scalar is re-derived day-by-day from real price history via
    `exit_advisor.assess_holding` -- the live engine's own pure scalar core,
    not a re-implementation -- called on a df TRUNCATED to each day so no
    call can see a future bar (`.tail(...)`-based windows inside
    `assess_holding` would otherwise read past the replay date).
    """
    ticker = rt["ticker"]
    try:
        bundle = _data.fetch_ticker_bundle(ticker, period)
        spy_bundle = _data.fetch_ticker_bundle("SPY", period)
    except Exception as e:
        return {**rt, "error": f"price history fetch failed: {e}"}

    hist = bundle.get("history") if isinstance(bundle, dict) else None
    spy_hist = spy_bundle.get("history") if isinstance(spy_bundle, dict) else None
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
        if result is None:
            continue
        tier = result["tier"]
        if first_fired.get(tier) is None:
            first_fired[tier] = d

    earliest_date = min((v for v in first_fired.values() if v is not None), default=None)
    earliest_tier = next((t for t in _TIERS if first_fired[t] == earliest_date), None) if earliest_date else None

    return {
        **rt,
        "error": None,
        **{f"first_{t.lower()}": first_fired[t] for t in _TIERS},
        "earliest_tier": earliest_tier,
        "earliest_date": earliest_date,
        "fired_before_sale": earliest_date is not None and earliest_date < exit_date,
        "lead_days": (exit_date - earliest_date).days if earliest_date else None,
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

    header = f"{'TICKER':<8}{'ENTRY':<12}{'ACTUAL SELL':<14}{'WATCH':<12}{'TRIM':<12}{'EXIT':<12}{'LEAD (days)':<12}"
    print(header)
    print("-" * len(header))
    fired_before, errored = 0, 0
    for rt in replayed:
        if rt.get("error"):
            errored += 1
            print(f"{rt['ticker']:<8}{_fmt(rt['entry_date']):<12}{_fmt(rt['exit_date']):<14}"
                  f"-- {rt['error']}")
            continue
        lead = rt["lead_days"]
        lead_str = f"{lead:+d}" if rt["fired_before_sale"] else ("0" if lead == 0 else "never")
        if rt["fired_before_sale"]:
            fired_before += 1
        print(f"{rt['ticker']:<8}{_fmt(rt['entry_date']):<12}{_fmt(rt['exit_date']):<14}"
              f"{_fmt(rt['first_watch']):<12}{_fmt(rt['first_trim']):<12}"
              f"{_fmt(rt['first_exit']):<12}{lead_str:<12}")

    evaluable = n - errored
    print("-" * len(header))
    print(
        f"\nFALSIFIABLE SIGNAL: of {evaluable} evaluable round trip(s), the ladder fired "
        f"BEFORE the actual\nsale in {fired_before} ({(fired_before / evaluable * 100.0) if evaluable else 0:.0f}%)."
        f" LEAD (days) is how many days\nearlier the earliest tier fired than the "
        f"actual sale; a negative or 'never' value means\nthe ladder did NOT flag "
        f"this loss before the owner acted on it.\n"
    )
    if errored:
        print(f"({errored} round trip(s) could not be replayed -- see the error column above.)\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--ticker", default=None, help="restrict to one ticker")
    parser.add_argument("--period", default="5y", help="yfinance history period to fetch (default: 5y)")
    args = parser.parse_args()

    trades_df = db.load_trades()
    if trades_df is None or trades_df.empty:
        print(
            "No trades could be read. Either there are none, or Supabase "
            "credentials\n(SUPABASE_URL / SUPABASE_KEY) are not set in this "
            "shell's environment."
        )
        return 1

    candidates = closed_losing_round_trips(trades_df, ticker=args.ticker)
    replayed = [replay_round_trip(rt, period=args.period) for rt in candidates]
    _print_report(replayed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
