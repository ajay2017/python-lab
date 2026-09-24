#!/usr/bin/env python3
"""Put a real dollar figure on the app's protective EXIT/TRIM calls that ran
early -- and on how much the owner already self-corrected for free.

Grew out of an Opus `planner` design pass reconciling two already-computed,
seemingly-opposite findings about this app's protective signals:

  1. `protective_track_record.py`'s per-ticker collapsed measure showed
     `protect_alpha = -15.7%` at `n_mature = 17` (`band = "firm"`) at the
     time this design pass ran -- EXIT/TRIM-flagged tickers, on average,
     went on to BEAT SPY after being flagged. **Sign convention changed
     2026-09-24** (2026-09-24 app review, B1) to match the app-wide alpha
     formula everywhere else in the app -- the `-15.7%` figure above uses
     the OLD (pre-2026-09-24) convention and is quoted here only as the
     historical reason this script was built, not as a value comparable to
     a live reading today. Current convention (reused verbatim, never
     re-derived here): positive = the flagged name beat SPY (the call ran
     early); negative = the flagged name lagged SPY (the call was right).
  2. `scripts/exit_ladder_replay.py`'s W6 replay found the opposite-sounding
     result: of 54 real closed LOSING round trips, 52% got NO protective
     signal at all before the loss (too late/absent), not too early.

The planner's read: these describe two different failure modes of one
miscalibration (insensitive on real fast movers, trigger-happy on names
having a transient dip) -- but nobody had separated, IN DOLLARS, how much
the "ran early" pattern actually cost when the owner ACTED on it (sold,
cutting a recovering name short) versus how often the owner already
self-corrected by NOT acting on an early call. That missing number is what
this script produces.

WHAT THIS DOES. For every ticker in `protective_track_record`'s collapsed,
MATURE, priced population (reused via `compute_protective_outcomes` +
`collapse_by_ticker` exactly -- the outcome classification and its sign
convention are never re-derived here), splits by direction:

  "ran early"  -- protect_alpha_pct > 0 (the flagged name beat SPY)
  "validated"  -- protect_alpha_pct <= 0 (the flagged name lagged/tied SPY)
    (a <= 0.0 tie counts as "validated" -- a disclosed judgment call;
    protective_track_record.py itself only defines strict positive/negative)

then, per ticker, determines whether the owner actually SOLD near a
qualifying signal by composing two more existing pure functions rather than
re-deriving the match: `self_track_record.classify_sells` tags each real
SELL row `engine_aligned` using the SAME window/reliable-log-start semantics
the live Self Track Record surface uses, and `trade_review._pair_sells_to_
buys` walks that ticker's FULL trade history to attribute exactly which
matched shares (and at what price) came from an engine_aligned sell versus
an unrelated one -- a sell that only partially closed a position, or drew
from more than one BUY lot, is credited by real matched shares, not the raw
SELL row's share count. The same FIFO helper, run on trades truncated to
on/before the signal date, also gives the shares that were actually open
(the "at risk") the moment the signal fired.

Four buckets fall out, exactly as designed with the owner:

  CUT SHORT       (ran early + sold)      -- dollar COST of having sold:
                    (current price - actual sell price) x shares sold.
                    A MARK-TO-TODAY hypothetical, not a realized number --
                    the owner may have redeployed that capital elsewhere,
                    which this script cannot see or credit.
  SELF-CORRECTED  (ran early + not sold)  -- no forced cost; reports the
                    same mark-to-today unrealized gain since the signal date,
                    as context for "what would have been lost had the app
                    been followed here."
  AVOIDED A LOSS  (validated + sold)      -- dollar BENEFIT of having sold:
                    (actual sell price - current price) x shares sold.
  IGNORED A CALL  (validated + not sold)  -- current unrealized drag vs the
                    signal-date price, ONLY if still held today; if the
                    ticker was later sold on unrelated terms, this is noted
                    without a computed figure rather than guessing why.

REDLINE. Read-only historical measurement, same as `exit_ladder_replay.py`.
Touches no gate, no constant, no live recommendation. This produces evidence
for an owner + `planner` conversation, never a conclusion -- it does NOT
say "therefore widen the stop," and any resulting change to `ATR_STOP_MULT`
or the deterioration ladder is a separate policy conversation requiring the
owner, a fresh `planner` design pass, and the mandatory Opus `reviewer`
(`exit_advisor.py` / `constants.py` are `_GATE_FILES` members).

HONEST CAVEAT, printed on every run:
  - Sample size: bounded by PROTECT_TRACK_MIN_CALLS-gated maturity, same as
    the live Defense facet card -- a thin population here is not evidence
    the pattern generalizes.
  - Mark-to-today vs realized: every "CUT SHORT" / "SELF-CORRECTED" /
    "IGNORED A CALL" dollar figure uses TODAY's live price as one input, so
    it is a snapshot, not a fixed verdict -- it will read differently
    tomorrow purely from price movement, with zero change in the underlying
    facts. Only "AVOIDED A LOSS" and the sold portion of "CUT SHORT" involve
    a REALIZED sell price; the other side of both those comparisons is
    still a live mark.
  - Sign nuance: "ran early"/"validated" is a RELATIVE (vs-SPY) call. A
    negative "CUT SHORT" dollar figure is possible and means the stock's
    ABSOLUTE price kept falling even though it beat SPY (SPY fell more) --
    relative and absolute framings can disagree; don't conflate them.
  - Survivorship: a ticker with no current live price (delisted, acquired,
    ticker changed) cannot be marked and is reported with no figure, not a
    fabricated zero.
  - Shares "at signal": the open-position size at the *signal date* (FIFO,
    via `_pair_sells_to_buys` truncated to trades on/before that date) is
    used as the exposure multiplier for both SELF-CORRECTED and IGNORED-A-
    CALL, even if the position has since grown/shrunk from unrelated trades
    -- a deliberate, disclosed simplification, not the actual capital at
    risk today.
  - This is evidence, not a threshold recommendation. See REDLINE above.

Requires:
  - Supabase credentials (SUPABASE_URL / SUPABASE_KEY) to read `trades` and
    `exit_signals` -- this project's DB is hosted-only (see CLAUDE.md), so
    run this with the same env vars Railway uses. The Railway Console shell
    is NOT a usable environment for this (minimal PATH, no app deps) -- run
    from a normal shell with the same Supabase env vars set instead.
  - Public daily price history (for SPY) and live current prices via the
    app's own multi-source fetcher -- no separate credentials needed beyond
    what `stock_analyzer.data` already uses.
  - `requests` for the Supabase reads (already a project dependency) -- this
    script talks to Supabase's PostgREST endpoint directly rather than via
    the `supabase` SDK, deliberately, for the same reason
    `exit_ladder_replay.py` does: the SDK pulls in `pyiceberg`, which has no
    prebuilt wheel for newer Python/Windows combos and needs a C toolchain
    to build from source, for reads this script has no other reason to need
    the SDK's full weight for.

This cannot be run from this coding session (no live DB credentials here) --
the owner runs it themselves, or pastes the output back for interpretation.

Usage:
    python scripts/exit_early_cost_analysis.py              # every mature protective call
    python scripts/exit_early_cost_analysis.py --ticker MU  # restrict to one ticker
    python scripts/exit_early_cost_analysis.py --period 5y  # longer SPY history fetch
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
from stock_analyzer.constants import (  # noqa: E402
    PROTECT_TRACK_FIRM_CALLS,
    PROTECT_TRACK_MIN_CALLS,
    REC_SCORE_MIN_DAYS,
    SELF_TRACK_SELL_RELIABLE_LOG_START,
    SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
)
from stock_analyzer.market_time import today_et  # noqa: E402
from stock_analyzer.protective_track_record import (  # noqa: E402
    collapse_by_ticker,
    compute_protective_outcomes,
    protective_headline,
)
from stock_analyzer.self_track_record import classify_sells  # noqa: E402
from stock_analyzer.trade_review import _pair_sells_to_buys  # noqa: E402

_BUCKET_ORDER = ("cut_short", "self_corrected", "avoided_loss", "ignored_call")
_BUCKET_LABEL = {
    "cut_short":      "CUT SHORT (ran early + sold)",
    "self_corrected": "SELF-CORRECTED (ran early + not sold)",
    "avoided_loss":   "AVOIDED A LOSS (validated + sold)",
    "ignored_call":   "IGNORED A CALL (validated + not sold)",
}


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


def _fmt(d) -> str:
    return d.strftime("%Y-%m-%d") if d else "--"


# ── Pure helpers (unit-testable with synthetic data -- no DB/network) ───────

def classify_direction(protect_alpha_pct: float) -> str:
    """"ran_early" (protect_alpha_pct > 0 -- the flagged name beat SPY after
    the warning, i.e. the call ran early) or "validated" (<= 0 -- the name
    lagged or tied SPY, i.e. the call was right). Sign convention reused
    verbatim from `protective_track_record.py` (flipped 2026-09-24, 2026-09-24
    app review B1, to match the app-wide alpha formula); the <= 0 tie-goes-to-
    "validated" rule is this script's own disclosed judgment call -- that
    module only defines strict positive/negative.
    """
    return "ran_early" if protect_alpha_pct > 0 else "validated"


def trade_rows_for_ticker(trades_df, ticker: str) -> list[dict]:
    """BUY/SELL rows for one ticker, shaped for `_pair_sells_to_buys`
    (id, ticker, action, shares, price, _trade_date). Same row shape
    `exit_ladder_replay.closed_losing_round_trips` builds, minus that
    function's losing/single-lot post-filter -- this script needs the FULL
    trade history for the ticker (wins and losses, open and closed).
    """
    want = ticker.strip().upper()
    rows: list[dict] = []
    if trades_df is None:
        return rows
    for _, r in trades_df.iterrows():
        act = str(r.get("action", "") or "").upper()
        if "BUY" not in act and "SELL" not in act:
            continue
        tk = str(r.get("ticker", "") or "").strip().upper()
        if tk != want:
            continue
        td = _to_date(r.get("traded_at"))
        if td is None:
            continue
        rows.append({
            "id": r.get("id"), "ticker": tk, "action": act,
            "shares": _f(r.get("shares")), "price": _f(r.get("price")),
            "_trade_date": td,
        })
    return rows


def shares_open_asof(ticker_rows: list[dict], asof: date | None) -> float:
    """Net long shares still open as of `asof` (inclusive), FIFO-correct via
    `_pair_sells_to_buys` -- sum of `shares_remaining` across every BUY whose
    trade date is <= asof. `asof=None` means no date filter (the ticker's
    full trade history, i.e. shares held as of today).
    """
    rows = ticker_rows if asof is None else [r for r in ticker_rows if r["_trade_date"] <= asof]
    if not rows:
        return 0.0
    pairing = _pair_sells_to_buys(rows)
    return sum(m["shares_remaining"] for m in pairing["matches"].values())


def aggregate_engine_aligned_sale(ticker_rows: list[dict], engine_aligned_sell_ids: set) -> dict | None:
    """Share-weighted aggregate of the portions of this ticker's SELLs that
    `self_track_record.classify_sells` classified `engine_aligned` (a SELL
    within SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS of a real EXIT/TRIM signal).
    Walks the SAME FIFO pairing `_pair_sells_to_buys` produces for the
    ticker's full trade history, so a sell that only partially closed a BUY
    (or drew from multiple BUYs) is attributed by actual matched shares, not
    the raw SELL row's share count.

    Returns None if no matched-sell portion for this ticker is
    engine_aligned. Otherwise: {"shares", "avg_price", "earliest_sell_date"}.
    """
    if not ticker_rows:
        return None
    pairing = _pair_sells_to_buys(ticker_rows)
    portions = [
        m2 for m in pairing["matches"].values()
        for m2 in m["matched"]
        if m2["sell_id"] in engine_aligned_sell_ids
    ]
    if not portions:
        return None
    shares = sum(p["shares"] for p in portions)
    if shares <= 0:
        return None
    weighted_price = sum(p["shares"] * p["sell_price"] for p in portions) / shares
    earliest = min((p["sell_date"] for p in portions if p.get("sell_date")), default=None)
    return {
        "shares":             round(shares, 4),
        "avg_price":          round(weighted_price, 4),
        "earliest_sell_date": earliest,
    }


def bucket_and_dollar_figure(
    *, ticker: str, direction: str, signal_date: date | None,
    price_at_signal: float, current_price: float | None,
    sale: dict | None, shares_at_signal: float, shares_held_today: float,
) -> dict:
    """
    Compose the bucket label + dollar figure for one ticker. Pure arithmetic
    on already-computed inputs -- invents no new financial logic.

    Sign conventions (see module docstring for the full rationale):
      cut_short      : (current_price - sale.avg_price)  * sale.shares   -- cost
      self_corrected : (current_price - price_at_signal) * shares_at_signal
      avoided_loss   : (sale.avg_price - current_price)  * sale.shares   -- benefit
      ignored_call   : (current_price - price_at_signal) * shares_at_signal -- drag
    """
    still_held = shares_held_today > 1e-9
    note: str | None = None
    dollar: float | None = None
    leftover_note = None
    if sale is not None and shares_at_signal - sale["shares"] > 1e-6:
        leftover_note = (
            f"{round(shares_at_signal - sale['shares'], 2)} share(s) of this position "
            "were NOT part of the qualifying sale and aren't counted in this figure"
        )

    if direction == "ran_early" and sale is not None:
        bucket = "cut_short"
        if current_price is not None:
            dollar = round((current_price - sale["avg_price"]) * sale["shares"], 2)
        else:
            note = "no current price available -- see survivorship caveat"
        note = "; ".join(n for n in (note, leftover_note) if n) or None

    elif direction == "ran_early" and sale is None:
        bucket = "self_corrected"
        if shares_at_signal <= 1e-9:
            note = "could not determine an open position at the signal date (data gap) -- no figure"
        elif current_price is None:
            note = "no current price available -- see survivorship caveat"
        else:
            dollar = round((current_price - price_at_signal) * shares_at_signal, 2)
            if not still_held:
                note = "no longer held -- exited later on unrelated terms; figure is hypothetical"

    elif direction == "validated" and sale is not None:
        bucket = "avoided_loss"
        if current_price is not None:
            dollar = round((sale["avg_price"] - current_price) * sale["shares"], 2)
        else:
            note = "no current price available -- see survivorship caveat"
        note = "; ".join(n for n in (note, leftover_note) if n) or None

    else:  # validated, no sale
        bucket = "ignored_call"
        if not still_held:
            note = "no longer held -- sold later on other terms; not guessing why, no figure computed"
        elif shares_at_signal <= 1e-9:
            note = "could not determine an open position at the signal date (data gap) -- no figure"
        elif current_price is None:
            note = "no current price available -- see survivorship caveat"
        else:
            dollar = round((current_price - price_at_signal) * shares_at_signal, 2)

    return {
        "ticker":          ticker,
        "bucket":          bucket,
        "direction":       direction,
        "signal_date":     signal_date,
        "dollar":          dollar,
        "note":            note,
        "shares_used":     round(sale["shares"], 4) if sale else round(shares_at_signal, 4),
        "reference_price": sale["avg_price"] if sale else price_at_signal,
        "sale_date":       sale["earliest_sell_date"] if sale else None,
        "current_price":   current_price,
    }


# ── Supabase reads (direct PostgREST -- see module docstring's Requires) ────

def load_trades() -> "pd.DataFrame | None":
    """Trades via Supabase's PostgREST endpoint directly. Returns `None` when
    credentials are absent or the request fails, distinguished from a
    genuinely empty `trades` table (returned as an empty DataFrame)."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/rest/v1/trades",
            params={"select": "*", "order": "traded_at.asc"},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        print(f"Could not read the trades table: {e}")
        return None
    return pd.DataFrame(rows)


def load_exit_signals() -> "pd.DataFrame | None":
    """exit_signals via Supabase's PostgREST endpoint directly -- same
    None-vs-empty-DataFrame contract as load_trades(), and the same
    SDK-avoidance rationale (see module docstring)."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/rest/v1/exit_signals",
            params={"select": "*", "order": "signal_date.asc"},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        print(f"Could not read the exit_signals table: {e}")
        return None
    return pd.DataFrame(rows)


def _spy_close_by_date(spy_hist) -> dict:
    """{date: close} from a price-history DataFrame -- same reshaping
    `app.py`'s own Engine Track Record card does inline (ZONE around
    L12152-12166) before calling `compute_protective_outcomes`."""
    out: dict = {}
    if spy_hist is None or spy_hist.empty or "Close" not in spy_hist.columns:
        return out
    for ts, row in spy_hist.iterrows():
        d = ts.date() if hasattr(ts, "date") else _to_date(ts)
        try:
            c = float(row["Close"])
        except (TypeError, ValueError):
            c = None
        if d is not None and c and c > 0:
            out[d] = c
    return out


def _live_prices(tickers: list[str]) -> dict:
    """{ticker: price} via the app's own multi-source live-price fetcher --
    same reshaping `app.py`'s Engine Track Record card does inline."""
    if not tickers:
        return {}
    try:
        px = _data.fetch_live_prices(tickers)
    except Exception:
        return {}
    return {
        t: float(d.get("price", 0))
        for t, d in (px or {}).items()
        if d and d.get("price")
    }


def _print_report(results: list[dict], headline: dict) -> None:
    n = len(results)
    print(
        f"\n{'=' * 78}\n"
        f"HONEST CAVEAT: N = {n} mature, priced protective (EXIT/TRIM) call(s) -- "
        f"the same\npopulation the 🛡️ Defense facet's protect_alpha is measured "
        f"over (n_mature={headline.get('n_mature')}, band=\"{headline.get('band')}\", "
        f"protect_alpha="
        f"{headline.get('protect_alpha')}). A thin N here is not evidence this\n"
        f"pattern generalizes. Every dollar figure below that uses TODAY's live "
        f"price is a SNAPSHOT,\nnot a fixed verdict -- it will read differently "
        f"tomorrow from price movement alone, with zero\nchange in the underlying "
        f"facts. See the module docstring's full HONEST CAVEAT section.\n{'=' * 78}\n"
    )
    if n == 0:
        print("No qualifying mature protective calls found. Nothing to analyze.")
        return

    by_bucket: dict[str, list[dict]] = {b: [] for b in _BUCKET_ORDER}
    for r in results:
        by_bucket[r["bucket"]].append(r)

    for b in _BUCKET_ORDER:
        rows = by_bucket[b]
        print(f"\n── {_BUCKET_LABEL[b]} ── ({len(rows)} ticker(s))")
        if not rows:
            print("  (none)")
            continue
        rows.sort(key=lambda r: (r["dollar"] is None, -abs(r["dollar"] or 0.0)))
        header = (f"  {'TICKER':<8}{'SIGNAL DATE':<14}{'SALE DATE':<12}{'SHARES':<10}"
                  f"{'REF PRICE':<12}{'CURRENT':<10}{'$ FIGURE':<14}NOTE")
        print(header)
        total = 0.0
        counted = 0
        for r in rows:
            dollar_str = f"{r['dollar']:+,.2f}" if r["dollar"] is not None else "n/a"
            cur_str = f"{r['current_price']:.2f}" if r["current_price"] is not None else "n/a"
            ref_str = f"{r['reference_price']:.2f}" if r["reference_price"] is not None else "n/a"
            print(
                f"  {r['ticker']:<8}{_fmt(r['signal_date']):<14}{_fmt(r['sale_date']):<12}"
                f"{r['shares_used']:<10.2f}{ref_str:<12}{cur_str:<10}{dollar_str:<14}"
                f"{r['note'] or ''}"
            )
            if r["dollar"] is not None:
                total += r["dollar"]
                counted += 1
        print(f"  {'-' * (len(header) - 2)}")
        print(f"  Bucket total ({counted}/{len(rows)} priced): ${total:+,.2f}")

    cut_short_total = sum(r["dollar"] for r in by_bucket["cut_short"] if r["dollar"] is not None)
    avoided_total = sum(r["dollar"] for r in by_bucket["avoided_loss"] if r["dollar"] is not None)
    print(
        f"\n{'=' * 78}\n"
        f"HEADLINE (mark-to-today snapshot, not a fixed verdict):\n"
        f"  Cost of cutting winners short so far: ${cut_short_total:+,.2f} "
        f"({len(by_bucket['cut_short'])} ticker(s) sold near an early call)\n"
        f"  Benefit of correctly-followed calls:  ${avoided_total:+,.2f} "
        f"({len(by_bucket['avoided_loss'])} ticker(s) sold near a validated call)\n"
        f"  Self-corrected (ignored an early call, still/was holding): "
        f"{len(by_bucket['self_corrected'])} ticker(s)\n"
        f"  Ignored a good call (still exposed to a validated warning): "
        f"{len(by_bucket['ignored_call'])} ticker(s)\n"
        f"{'=' * 78}\n"
        "This is evidence for an owner + planner conversation, not a "
        "recommendation -- see REDLINE\nin the module docstring. Nothing here "
        "implies a constants.py change on its own.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--ticker", default=None, help="restrict to one ticker")
    parser.add_argument("--period", default="2y", help="SPY history period to fetch (default: 2y)")
    args = parser.parse_args()
    want = args.ticker.strip().upper() if args.ticker else None

    trades_df = load_trades()
    if trades_df is None:
        print(
            "No trades could be read. Either Supabase credentials "
            "(SUPABASE_URL / SUPABASE_KEY) are not set in this shell's "
            "environment, or the request itself failed -- see any error above."
        )
        return 1

    signals_df = load_exit_signals()
    if signals_df is None:
        print(
            "No exit_signals could be read. Either Supabase credentials are "
            "not set, or the request itself failed -- see any error above."
        )
        return 1

    scoped = (
        signals_df[signals_df["signal_type"].isin(["EXIT", "TRIM"])]
        if not signals_df.empty else signals_df
    )
    if scoped.empty:
        print("No EXIT/TRIM signals recorded yet. Nothing to analyze.")
        return 0

    tickers = sorted({
        str(t).strip().upper() for t in scoped["ticker"].dropna().tolist() if str(t).strip()
    })
    if want:
        tickers = [t for t in tickers if t == want]
        if not tickers:
            print(f"No EXIT/TRIM signals recorded for {want}.")
            return 0

    print(f"Fetching SPY benchmark history ({args.period})...")
    try:
        spy_bundle = _data.fetch_ticker_bundle("SPY", args.period)
        spy_hist = spy_bundle.get("history") if isinstance(spy_bundle, dict) else None
    except Exception as e:
        print(f"SPY fetch failed ({e}) -- cannot compute vs-SPY outcomes.")
        return 1
    spy_close_by_date = _spy_close_by_date(spy_hist)
    if not spy_close_by_date:
        print("No usable SPY history returned -- cannot compute vs-SPY outcomes.")
        return 1

    print(f"Fetching live prices for {len(tickers)} ticker(s)...")
    current_prices = _live_prices(tickers)

    today = today_et()
    enriched = compute_protective_outcomes(
        scoped, current_prices, today=today,
        spy_close_by_date=spy_close_by_date, min_days=REC_SCORE_MIN_DAYS,
    )
    collapsed = collapse_by_ticker(enriched)
    headline = protective_headline(collapsed, PROTECT_TRACK_MIN_CALLS, PROTECT_TRACK_FIRM_CALLS)

    # Same mature+priced predicate protective_headline() itself applies --
    # duplicated (not imported) because it's an inline filter there, not an
    # exported helper; keeping the exact expression is what guarantees this
    # script's population matches the live Defense facet's population.
    mature_priced = [
        r for r in collapsed
        if not r.get("maturing")
        and r.get("protect_alpha_pct") is not None
        and r["protect_alpha_pct"] == r["protect_alpha_pct"]  # NaN check
    ]
    if want:
        mature_priced = [r for r in mature_priced if r["ticker"] == want]

    if not mature_priced:
        _print_report([], headline)
        return 0

    sell_classified = classify_sells(
        trades_df, signals_df,
        reliable_log_start=SELF_TRACK_SELL_RELIABLE_LOG_START,
        signal_window_days=SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    if sell_classified is None:
        print("Could not classify sells against exit_signals -- aborting rather "
              "than silently treating every sell as unmatched.")
        return 1
    engine_aligned_ids = {
        r["id"] for r in sell_classified if r.get("bucket") == "engine_aligned"
    }

    results: list[dict] = []
    for row in mature_priced:
        tk = row["ticker"]
        rows = trade_rows_for_ticker(trades_df, tk)
        signal_date = row["signal_date"]
        shares_at_signal = shares_open_asof(rows, signal_date)
        shares_held_today = shares_open_asof(rows, None)
        sale = aggregate_engine_aligned_sale(rows, engine_aligned_ids)
        direction = classify_direction(row["protect_alpha_pct"])
        result = bucket_and_dollar_figure(
            ticker=tk, direction=direction, signal_date=signal_date,
            price_at_signal=row["price_at_signal"], current_price=current_prices.get(tk),
            sale=sale, shares_at_signal=shares_at_signal, shares_held_today=shares_held_today,
        )
        results.append(result)

    _print_report(results, headline)
    return 0


if __name__ == "__main__":
    sys.exit(main())
