#!/usr/bin/env python3
"""Read-only: do Watchlist ENTER_NOW calls beat SPY, and does it differ by market state?

Base-rate readout (queue.md, the ENTER_NOW base-rate line). Nothing is wired, nothing
changes a decision, nothing is written (`db.set_readonly(True)`). Makes no LLM
calls (ANTHROPIC_API_KEY is dropped before any app import, only when run as a
script). Needs Supabase credentials in the environment (the same ones Railway
uses) and network for Yahoo Finance: run it from a normal shell, not locally
without them.

Pre-registered: the headline is "of N matured ENTER_NOW calls, X% beat SPY" at
GATE_LEDGER_HORIZON_TRADING_DAYS, and a cell with fewer than GATE_LEDGER_MIN_CALLS
calls reads "building" (no verdict). The 10-session horizon is printed as an
EARLY READ only. There is deliberately no split by composite score.

Usage:
    python scripts/enter_now_regime_readout.py
    python scripts/enter_now_regime_readout.py --since 2026-09-01 --csv enter_now.csv

Known approximations (marked, not guessed):
  * One call = one (TICKER, rec_date); the earliest-surfaced row wins. Rows with no
    ticker or no parseable rec_date are dropped (counted in the header).
  * Market state is read at the PRIOR NYSE session's close (what the app could know
    before a 09:45 ET call). The regime uses SPY closes + the ^VIX close; the trend
    labels use market_trend.compute_trend on the downloaded fund closes.
  * Day-move tone is the CALL-DAY S&P 500 (^GSPC) full-day close-to-close change run
    through entry_eligibility.classify_tone. The app's 09:45 read was the move so
    far that morning, so this is an APPROXIMATION and can disagree with it. A call
    on a day with no downloaded bar reads "unknown".
  * Prices are one batched Yahoo download with auto_adjust=True (dividend-adjusted),
    SPY leg included; price_at_surface is the app's own recorded price, and the
    forward stock close comes from providers.orchestrator.get_historical_close
    (first close on/after the target session within 7 days). Both are the same
    inputs the app's other forward-alpha readouts use, but the bases are not
    reconciled.
  * Alpha = stock % return minus SPY % return over rec_date -> rec_date + N NYSE
    sessions (predictive_analytics.forward_alpha_at_horizon, reused unchanged).
    "Beat SPY" = alpha strictly above 0. A call with no recorded price_at_surface
    or no forward close is counted as "unpriced", never graded.
  * Download end is today + 1 (exclusive), so a partial bar for today can be used when
    the market is open; run after the close.
  * Episodes: a ticker's calls at most --episode-gap NYSE sessions apart (gap from
    the previous call) fold into one episode graded from its FIRST call. The gap is
    a readout grouping choice, not policy.
  * A cell below the floor shows only N ("building"); its beat share / mean / median
    are withheld. The regime reads "unknown" when exit_advisor.risk_off_state could
    not measure either leg.
"""
from __future__ import annotations

import os

if __name__ == "__main__":
    os.environ.pop("ANTHROPIC_API_KEY", None)   # no LLM spend, before any app import

import argparse  # noqa: E402
import csv  # noqa: E402
import sys  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import enter_now_readout as eno  # noqa: E402
from stock_analyzer.constants import (  # noqa: E402
    GATE_LEDGER_HORIZON_TRADING_DAYS, GATE_LEDGER_MIN_CALLS,
)

# The 10-session horizon is an early read, not the pre-registered one.
EARLY_HORIZON = 10
HEADLINE_HORIZON = GATE_LEDGER_HORIZON_TRADING_DAYS

CSV_COLUMNS = (
    "ticker", "rec_date", "prior_session", "alpha_10", "alpha_30", "regime_armed",
    "regime_legs", "spx_label", "spx_drawdown", "sector_label", "day_tone_approx",
    "already_held", "episode_id",
    # extras: context for reading a row (composite_score is CSV-only, never summarised)
    "sector_fund", "day_move_pct", "regime_state", "n_calls_in_episode", "composite_score",
)


# -- prices -------------------------------------------------------------------

def _download_closes(call_dates: list[date], today: date):
    """One batched Yahoo download of every symbol the readout reads. Returns a
    DataFrame (index = dates, columns = symbols). Raises SystemExit when empty."""
    import pandas as pd
    import yfinance as yf

    from stock_analyzer import market_trend
    from stock_analyzer.data import _retry

    symbols: list[str] = []
    for s in ["SPY", "^GSPC", "^IXIC", "^VIX"] + market_trend.trend_symbols():
        if s not in symbols:
            symbols.append(s)
    start = min(call_dates) - timedelta(days=400)
    raw = _retry(
        yf.download, symbols, start=start.isoformat(),
        end=(today + timedelta(days=1)).isoformat(),   # end is exclusive
        auto_adjust=True, progress=False, threads=True,
    )
    if raw is None or raw.empty:
        raise SystemExit("Yahoo download came back empty. Nothing was computed.")
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    close = close.copy()
    close.index = [pd.Timestamp(x).date() for x in close.index]
    return close


def _memoised_close_fn():
    """providers.orchestrator.get_historical_close with a per-(ticker, start, end) memo."""
    from stock_analyzer.providers.orchestrator import get_historical_close
    memo: dict = {}

    def fn(ticker, start, end):
        key = (ticker, start, end)
        if key not in memo:
            try:
                memo[key] = get_historical_close(ticker, start, end)
            except Exception:
                memo[key] = None
        return memo[key]
    return fn


# -- formatting ---------------------------------------------------------------

def _pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _alpha(x) -> str:
    return "-" if x is None else f"{x:+.2f}"


def format_line(s: dict, min_calls: int) -> str:
    head = f"  {s['cell']:<22} N={s['n']:<4}"
    if s["verdict"] == "building":
        return f"{head} building (N < {min_calls})"
    return (f"{head} beat SPY {_pct(s['beat_share']):>6}   "
            f"mean {_alpha(s['mean_alpha'])} pp   median {_alpha(s['median_alpha'])} pp")


def print_block(title: str, cells: dict, min_calls: int) -> None:
    print(f"\n{title}")
    for s in eno.summarise(cells, min_calls):
        print(format_line(s, min_calls))


def print_report(rows: list[dict], episode_rows: list[dict], horizon: int, heading: str,
                 stats: dict) -> None:
    af = f"alpha_{horizon}"
    print(f"\n{'=' * 78}\n{heading}  [horizon {horizon} NYSE sessions, min N per cell "
          f"{GATE_LEDGER_MIN_CALLS}]\n{'=' * 78}")
    print(f"calls graded {stats['graded']}, not matured {stats['not_matured']}, "
          f"unpriced {stats['unpriced']} (matured but no price / forward close)")
    all_alphas = {"all matured calls": [r[af] for r in rows if r.get(af) is not None]}
    print_block("HEADLINE: of the matured ENTER_NOW calls, the share that beat SPY",
                all_alphas, GATE_LEDGER_MIN_CALLS)
    for title, field, order in eno.SPLITS:
        print_block(f"By {title}", eno.cells_by(rows, field, af, order), GATE_LEDGER_MIN_CALLS)
    ep_alphas = {"all matured episodes": [r[af] for r in episode_rows if r.get(af) is not None]}
    print_block("BY EPISODE (graded from each episode's first call): headline",
                ep_alphas, GATE_LEDGER_MIN_CALLS)
    # Every market-state split by episode too (owner 2026-11-22): the per-call
    # cells count one name's run of calls many times (1,016 calls were 142
    # episodes), so a policy reading must come from these. An episode carries
    # the market state of its FIRST call.
    for title, field, order in eno.SPLITS:
        print_block(f"BY EPISODE: {title}",
                    eno.cells_by(episode_rows, field, af, order), GATE_LEDGER_MIN_CALLS)


# -- main ---------------------------------------------------------------------

def main() -> int:
    # A Windows cp1252 console (or a redirect to a file) can't encode some
    # provider / regime text; force utf-8 with replacement, as
    # check_constants_documented.py does, so the readout never dies on output.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="", help="first rec_date, YYYY-MM-DD (default: earliest enter_now row)")
    ap.add_argument("--csv", default="", help="write one row per call to this path")
    ap.add_argument("--episode-gap", type=int, default=eno.EPISODE_GAP_SESSIONS,
                    help="NYSE sessions between calls that still count as one episode")
    args = ap.parse_args()

    from stock_analyzer import db
    db.set_readonly(True)
    if not db.has_db():
        print("No Supabase credentials (SUPABASE_URL/SUPABASE_KEY) -- cannot run.")
        return 1

    from stock_analyzer import market_trend
    from stock_analyzer.market_time import today_et
    from stock_analyzer.predictive_analytics import forward_alpha_at_horizon

    today = today_et()
    since = date.fromisoformat(args.since) if args.since else None
    recs = db.load_recommendations_or_none(since, today)
    if recs is None:
        raise SystemExit("Could not read recommendations from Supabase. Nothing was computed.")
    loaded = recs[recs["rec_type"] == "enter_now"] if len(recs) else recs
    rows = loaded.to_dict("records")
    calls = eno.dedupe_calls(rows)
    if not calls:
        print(f"No enter_now calls found ({len(rows)} rows loaded). Nothing to read.")
        return 0

    closes = _download_closes([c["rec_date"] for c in calls], today)
    for need in ("SPY", "^GSPC"):
        if need not in closes.columns or closes[need].dropna().empty:
            raise SystemExit(f"No {need} closes downloaded. Nothing was computed.")
    spy_close = closes["SPY"].dropna()
    spx_close = closes["^GSPC"].dropna()
    vix_close = closes["^VIX"].dropna() if "^VIX" in closes.columns else None
    spy_by_date = {d: float(v) for d, v in spy_close.items()}

    trend_memo: dict = {}

    def trend_fn(as_of):
        if as_of not in trend_memo:
            try:
                trend_memo[as_of] = market_trend.compute_trend(closes, as_of=as_of)
            except Exception:
                trend_memo[as_of] = None
        return trend_memo[as_of]

    close_fn = _memoised_close_fn()

    def alpha_fn(ticker, rec_date, price, horizon):
        return forward_alpha_at_horizon(ticker, rec_date, price, horizon, spy_by_date, close_fn)

    print(f"Pricing {len(calls)} calls at horizons {HEADLINE_HORIZON} and {EARLY_HORIZON} ...",
          file=sys.stderr)
    graded: list[dict] = []
    stats = {h: {"graded": 0, "not_matured": 0, "unpriced": 0} for h in (HEADLINE_HORIZON, EARLY_HORIZON)}
    for call in calls:
        c = eno.classify_call(call, spy_close=spy_close, spx_close=spx_close,
                              vix_close=vix_close, trend_fn=trend_fn)
        for h in (HEADLINE_HORIZON, EARLY_HORIZON):
            status, alpha = eno.grade_call(c, h, today, alpha_fn)
            c[f"alpha_{h}"] = alpha
            stats[h][status] += 1
        graded.append(c)

    episodes = eno.group_episodes(graded, args.episode_gap)
    for ep in episodes:
        for c in ep["calls"]:
            c["episode_id"] = ep["episode_id"]
            c["n_calls_in_episode"] = ep["n_calls"]
    episode_rows = [ep["first"] for ep in episodes]

    first, last = calls[0]["rec_date"], calls[-1]["rec_date"]
    unknown_regime = sum(1 for c in graded if c["regime_armed"] == eno.UNKNOWN)
    print(f"ENTER_NOW base-rate readout (read-only), run {today}")
    print(f"rec_date range {first} .. {last}")
    print(f"rows loaded {len(rows)}, calls after dedupe to one per (ticker, date) {len(calls)} "
          f"({len(rows) - len(calls)} duplicate or unusable rows dropped), "
          f"episodes (gap {args.episode_gap} sessions) {len(episodes)}")
    print(f"regime unknown for {unknown_regime} calls")
    print("APPROXIMATIONS: market state read at the prior session's close; day-move tone is the "
          "call-day full-day S&P close-to-close (the app's 09:45 read was intraday); adjusted "
          "Yahoo closes; beat SPY = alpha > 0; thin cells withhold their figures.")
    print("No split by recommendation score, by design.")

    print_report(graded, episode_rows, HEADLINE_HORIZON,
                 f"PRE-REGISTERED HEADLINE ({HEADLINE_HORIZON}-session horizon)", stats[HEADLINE_HORIZON])
    print_report(graded, episode_rows, EARLY_HORIZON,
                 f"EARLY READ, NOT THE PRE-REGISTERED HORIZON ({EARLY_HORIZON}-session horizon)",
                 stats[EARLY_HORIZON])

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(graded)
        print(f"\nwrote {len(graded)} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
