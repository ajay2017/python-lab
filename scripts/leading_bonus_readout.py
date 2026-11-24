#!/usr/bin/env python3
"""Read-only: do picks that got the Brief's leading-sector rank bonus beat SPY differently?

Readout for the bug-register row TrendVsLeading. Nothing is wired, nothing changes a
decision, nothing is written (`db.set_readonly(True)`). Makes no LLM calls
(ANTHROPIC_API_KEY is dropped before any app import, only when run as a script).
Needs Supabase credentials in the environment (SUPABASE_URL / SUPABASE_KEY, the
ones Railway uses) and network for Yahoo Finance: run it from a normal shell.

The definition (population, outcome, splits, floors, reconstruction) is pre-registered
in stock_analyzer/leading_bonus_readout.PRE_REGISTERED_DEFINITION and printed first.
The bonus and the leading funds were NOT stored with each pick, so they are rebuilt
from prices; see that module's docstring for what is reconstructed and the proxies
used for what a row cannot say. No verdict is printed.

Usage (PowerShell):
    $env:SUPABASE_URL = "https://<project-id>.supabase.co"
    $env:SUPABASE_KEY = "<service-role key>"
    python scripts/leading_bonus_readout.py
    python scripts/leading_bonus_readout.py --since 2026-09-02 --csv leading_bonus.csv
    python scripts/leading_bonus_readout.py --rec-types new_pick,overflow_pick

Known limits (printed, not guessed):
  * The live 1-week return used the newest Yahoo bar at build time (a partial bar for
    today once the market is open). Primary read = the prior completed session; the
    count of picks whose bonus state changes under the rec_date close is printed.
  * The bonus matched the scanner row's raw "Sector" label; the stored sector is the
    gate-resolved one (the same for curated names, different for e.g. Watchlist extras).
  * A pick in a leading sector whose writer or mover status the row cannot settle is
    "unknown" and kept out of every comparison (counted). overflow_pick rows carry
    no thesis and no pillar scores, so they are "unknown" whenever their sector led.
  * Calls dated before the last SECTOR_ETF addition (2026-09-02) are rebuilt with
    funds that could not lead live; they are counted in the header.
  * Prices are one batched Yahoo download (auto_adjust=True); the forward stock close
    comes from providers.orchestrator.get_historical_close, like the Enter Now readout.
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
from stock_analyzer import leading_bonus_readout as lbr  # noqa: E402

CSV_COLUMNS = (
    "ticker", "rec_date", "rec_type", "prior_session", "sector", "bonus", "bonus_reason",
    "bonus_alt", "leading_funds", "bonus_fund", "fund_label", "fund_ret_1w", "fund_dir",
    "alpha_10", "alpha_30", "episode_id", "n_calls_in_episode", "composite_score",
)


def _download_closes(call_dates: list[date], today: date):
    """One batched Yahoo download: SPY + the indices + every sector fund. Returns a
    DataFrame (index = dates, columns = symbols). Raises SystemExit when empty."""
    import pandas as pd
    import yfinance as yf

    from stock_analyzer import market_trend
    from stock_analyzer.data import _retry

    symbols: list[str] = []
    for s in ["SPY"] + market_trend.trend_symbols():
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


def print_cells(title: str, items: list[dict], names: dict | None) -> None:
    print(f"\n{title}")
    for it in items:
        print(lbr.format_cell_line(it, names))


def print_horizon(rt_calls: list[dict], episode_rows: list[dict], horizon: int,
                  heading: str, stats: dict) -> None:
    print(f"\n{'=' * 78}\n{heading}  [horizon {horizon} NYSE sessions]\n{'=' * 78}")
    print(f"calls graded {stats['graded']}, not matured {stats['not_matured']}, "
          f"unpriced {stats['unpriced']} (matured but no price / forward close)")
    print("\nBY EPISODE (graded from each episode's first call), per call beside it:")
    print(lbr.format_headline_line("all matured picks", rt_calls, episode_rows, horizon))
    for title, field, order, names, where in lbr.split_definitions():
        items = lbr.split_cells(rt_calls, episode_rows, field, order, horizon, where)
        print_cells(f"BY EPISODE: {title}", items, names)


def main() -> int:
    # A Windows cp1252 console (or a redirect to a file) can't encode some text;
    # force utf-8 with replacement so the readout never dies on output.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="", help="first rec_date, YYYY-MM-DD (default: earliest row)")
    ap.add_argument("--csv", default="", help="write one row per call to this path")
    ap.add_argument("--episode-gap", type=int, default=eno.EPISODE_GAP_SESSIONS,
                    help="NYSE sessions between calls that still count as one episode")
    ap.add_argument("--rec-types", default="new_pick",
                    help="comma list of rec_type values (default: new_pick; add overflow_pick "
                         "to include the 'also cleared' names)")
    args = ap.parse_args()
    rec_types = [t.strip() for t in args.rec_types.split(",") if t.strip()]
    if not rec_types:
        raise SystemExit("--rec-types needs at least one rec_type.")

    print(lbr.PRE_REGISTERED_DEFINITION)

    from stock_analyzer import db
    db.set_readonly(True)
    if not db.has_db():
        print("\nNo Supabase credentials (SUPABASE_URL/SUPABASE_KEY) -- cannot run.")
        return 1

    from stock_analyzer import market_trend
    from stock_analyzer.market_time import today_et
    from stock_analyzer.predictive_analytics import forward_alpha_at_horizon

    today = today_et()
    since = date.fromisoformat(args.since) if args.since else None
    recs = db.load_recommendations_or_none(since, today)
    if recs is None:
        raise SystemExit("Could not read recommendations from Supabase. Nothing was computed.")
    loaded = recs[recs["rec_type"].isin(rec_types)] if len(recs) else recs
    rows = loaded.to_dict("records")
    calls = eno.dedupe_calls(rows, by_rec_type=True)
    if not calls:
        print(f"\nNo {'/'.join(rec_types)} rows found ({len(rows)} rows loaded). Nothing to read.")
        return 0

    closes = _download_closes([c["rec_date"] for c in calls], today)
    if "SPY" not in closes.columns or closes["SPY"].dropna().empty:
        raise SystemExit("No SPY closes downloaded. Nothing was computed.")
    spy_by_date = {d: float(v) for d, v in closes["SPY"].dropna().items()}

    state_memo: dict = {}

    def state_fn(as_of):
        if as_of not in state_memo:
            try:
                trend = market_trend.compute_trend(closes, as_of=as_of)
            except Exception:
                trend = None
            try:
                state_memo[as_of] = lbr.session_state(closes, as_of, trend)
            except Exception:
                state_memo[as_of] = None
        return state_memo[as_of]

    close_fn = _memoised_close_fn()

    def alpha_fn(ticker, rec_date, price, horizon):
        return forward_alpha_at_horizon(ticker, rec_date, price, horizon, spy_by_date, close_fn)

    horizons = (lbr.HEADLINE_HORIZON, lbr.EARLY_HORIZON)
    print(f"Pricing {len(calls)} picks at horizons {horizons[0]} and {horizons[1]} ...", file=sys.stderr)
    classified: list[dict] = []
    stats = {(rt, h): {"graded": 0, "not_matured": 0, "unpriced": 0}
             for rt in rec_types for h in horizons}
    for call in calls:
        c = lbr.classify_pick(call, state_fn)
        for h in horizons:
            status, alpha = eno.grade_call(c, h, today, alpha_fn)
            c[f"alpha_{h}"] = alpha
            stats[(c.get("rec_type", rec_types[0]), h)][status] += 1
        classified.append(c)

    print(f"\nLeading-bonus readout over {', '.join(rec_types)} (read-only), run {today}")
    print(f"rec_date range {calls[0]['rec_date']} .. {calls[-1]['rec_date']}")
    print(f"rows loaded {len(rows)}, picks after dedupe to one per (ticker, date, rec_type) "
          f"{len(calls)} ({len(rows) - len(calls)} duplicate or unusable rows dropped)")
    cnt = lbr.bonus_counts(classified)
    print("bonus state of the picks (reconstructed): "
          + ", ".join(f"{lbr.BONUS_NAMES[k]} {cnt['by_state'].get(k, 0)}" for k in lbr.BONUS_ORDER))
    for reason, n in sorted(cnt["by_reason"].items(), key=lambda kv: -kv[1]):
        print(f"    {n:>4}  {reason}")
    print(f"sensitivity: {cnt['differ_alt']} of {cnt['n']} picks read a different bonus state if the "
          f"leading set is taken at the rec_date close instead of the prior session's")
    early = sum(1 for c in classified if c["rec_date"] < lbr.FUND_SET_LAST_CHANGED)
    print(f"{early} picks are dated before {lbr.FUND_SET_LAST_CHANGED} (the last change to which funds "
          f"can lead): they are rebuilt with funds that could not lead then (use --since to drop them)")

    csv_rows: list[dict] = []
    for rt in rec_types:
        rt_calls = [c for c in classified if c.get("rec_type", rec_types[0]) == rt]
        if not rt_calls:
            print(f"\n(no {rt} rows)")
            continue
        episodes = eno.group_episodes(rt_calls, args.episode_gap)
        for ep in episodes:
            for c in ep["calls"]:
                c["episode_id"] = ep["episode_id"]
                c["n_calls_in_episode"] = ep["n_calls"]
        episode_rows = [ep["first"] for ep in episodes]
        csv_rows.extend(rt_calls)
        print(f"\n{'#' * 78}\n{rt.upper()}: {len(rt_calls)} picks, {len(episodes)} episodes "
              f"(gap {args.episode_gap} sessions)\n{'#' * 78}")
        for h, heading in ((lbr.HEADLINE_HORIZON, f"PRIMARY ({lbr.HEADLINE_HORIZON}-session horizon)"),
                           (lbr.EARLY_HORIZON, f"EARLY READ ({lbr.EARLY_HORIZON}-session horizon)")):
            print_horizon(rt_calls, episode_rows, h, heading, stats[(rt, h)])

    print(f"\n{lbr.CAVEAT_LINE}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(csv_rows)
        print(f"wrote {len(csv_rows)} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
