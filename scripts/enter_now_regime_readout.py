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
    python scripts/enter_now_regime_readout.py --rec-types enter_now,new_pick,overflow_pick

Entry regime check (docs/plans/entry-regime-check.md, C2): the "would hold under rule
A / B / C" splits come from stock_analyzer.entry_regime (last completed session's close),
per call and per episode, beside the stock's own return ("mean stock return": the stock's
% return over the alpha window, not relative to SPY). Rule A is judged by the plan's
pre-registered go-live criteria (printed at the end of the 30-session section, enter_now
only). Rules B / C, the Nasdaq label and any other cut are exploratory. Hold rate and
hold/clear flips are printed per rule. With more than one --rec-types, a type other than
enter_now gets only the headline and the rule A split.

Known approximations (marked, not guessed):
  * One call = one (TICKER, rec_date, rec_type); the earliest-surfaced row wins. Rows with no
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
    ENTRY_REGIME_GO_LIVE_EARLY_HORIZON, GATE_LEDGER_FIRM_CALLS,
    GATE_LEDGER_HORIZON_TRADING_DAYS, GATE_LEDGER_MIN_CALLS, GATE_LEDGER_MIN_TICKERS,
)

# The early horizon is an early read, not the pre-registered headline one.
EARLY_HORIZON = ENTRY_REGIME_GO_LIVE_EARLY_HORIZON
HEADLINE_HORIZON = GATE_LEDGER_HORIZON_TRADING_DAYS

CSV_COLUMNS = (
    "ticker", "rec_date", "prior_session", "alpha_10", "alpha_30", "regime_armed",
    "regime_legs", "spx_label", "spx_drawdown", "sector_label", "day_tone_approx",
    "already_held", "episode_id",
    # extras: context for reading a row (composite_score is CSV-only, never summarised)
    "sector_fund", "day_move_pct", "regime_state", "n_calls_in_episode", "composite_score",
    # entry regime (C2): the rule verdicts come from stock_analyzer.entry_regime
    "rec_type", "rule_a", "rule_b", "rule_c", "ndx_label", "entry_regime_read_state",
    "vix_prior_close",
    # the stock's own return over the same window as the alpha
    "ret_10", "ret_30",
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


def _ret(x) -> str:
    return "-" if x is None else f"{x:+.2f}%"


def format_line(s: dict, min_calls: int) -> str:
    head = f"  {s['cell']:<22} N={s['n']:<4}"
    if s["verdict"] == "building":
        return f"{head} building (N < {min_calls})"
    line = (f"{head} beat SPY {_pct(s['beat_share']):>6}   "
            f"mean {_alpha(s['mean_alpha'])} pp   median {_alpha(s['median_alpha'])} pp")
    if "mean_return" in s:
        # the stock's own return (not |x|), over the same calls as the alpha figures
        line += f"   mean stock return {_ret(s['mean_return'])}"
    return line


def print_block(title: str, cells: dict, min_calls: int, ret_cells: dict | None = None) -> None:
    print(f"\n{title}")
    summaries = eno.summarise(cells, min_calls)
    if ret_cells is not None:
        summaries = eno.attach_mean_return(summaries, ret_cells)
    for s in summaries:
        print(format_line(s, min_calls))


def _headline_block(title: str, label: str, rows: list[dict], horizon: int) -> None:
    af, rf = f"alpha_{horizon}", f"ret_{horizon}"
    both = [r for r in rows if r.get(af) is not None]
    print_block(title, {label: [r[af] for r in both]}, GATE_LEDGER_MIN_CALLS,
                {label: [r[rf] for r in both if r.get(rf) is not None]})


def _split_block(prefix: str, rows: list[dict], split: tuple, horizon: int,
                 primary: bool = True, go_live_where: str | None = None) -> None:
    """primary False drops "PRIMARY, pre-registered" from the rule A title (only
    the enter_now population was pre-registered). go_live_where, on an episode
    rule A block, adds the pointer to the one place a go-live verdict is read."""
    title, field, order = split
    if not primary:
        title = title.replace(" (PRIMARY, pre-registered)", "")
    af, rf = f"alpha_{horizon}", f"ret_{horizon}"
    print_block(f"{prefix}{title}", eno.cells_by(rows, field, af, order), GATE_LEDGER_MIN_CALLS,
                eno.returns_by(rows, field, af, rf, order))
    if go_live_where and field == "rule_a":
        print(f"  go-live is decided only in the go-live block {go_live_where} "
              f"({GATE_LEDGER_FIRM_CALLS}-episode / {GATE_LEDGER_MIN_TICKERS}-ticker floors)")


def print_report(rows: list[dict], episode_rows: list[dict], horizon: int, heading: str,
                 stats: dict, rec_label: str = "ENTER_NOW", compact: bool = False,
                 go_live: dict | None = None, primary: bool = True,
                 go_live_where: str | None = None) -> None:
    """compact = headline + the rule A split only (per call and per episode), for a
    recommendation type the entry-regime plan did not pre-register. go_live, when
    given, prints the pre-registered go-live block after the episode splits.
    primary = the enter_now population the plan pre-registered."""
    print(f"\n{'=' * 78}\n{heading}  [horizon {horizon} NYSE sessions, min N per cell "
          f"{GATE_LEDGER_MIN_CALLS}]\n{'=' * 78}")
    print(f"calls graded {stats['graded']}, not matured {stats['not_matured']}, "
          f"unpriced {stats['unpriced']} (matured but no price / forward close)")
    print("'mean stock return' = the stock's own % return over the same window (absolute, "
          "not relative to SPY), over the same calls as the alpha figures.")
    _headline_block(f"HEADLINE: of the matured {rec_label} calls, the share that beat SPY",
                    "all matured calls", rows, horizon)
    splits = [s for s in eno.SPLITS if s[1] == "rule_a"] if compact else list(eno.SPLITS)
    for split in splits:
        _split_block("By ", rows, split, horizon, primary)
    _headline_block("BY EPISODE (graded from each episode's first call): headline",
                    "all matured episodes", episode_rows, horizon)
    # Every market-state split by episode too (owner 2026-11-22): the per-call
    # cells count one name's run of calls many times (1,016 calls were 142
    # episodes), so a policy reading must come from these. An episode carries
    # the market state of its FIRST call.
    for split in splits:
        _split_block("BY EPISODE: ", episode_rows, split, horizon, primary, go_live_where)
    if go_live is not None:
        print()
        for line in eno.format_go_live_lines(go_live):
            print(line)


def print_hold_rates(calls: list[dict], session_verdicts: dict) -> None:
    """session_verdicts covers EVERY NYSE session from the first to the last call
    of this type, so the held share and the flips are exact, not call-date only."""
    stats = {f"rule {r}": eno.hold_rate_and_flips(calls, f"rule_{r.lower()}", session_verdicts)
             for r in "ABC"}
    print("\nHOLD RATE AND FLIPS (every NYSE session from the first to the last call; "
          "unknown sessions are counted but skipped when counting flips):")
    for line in eno.format_hold_lines(stats):
        print(line)


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
    ap.add_argument("--rec-types", default="enter_now",
                    help="comma list of rec_type values, e.g. enter_now,new_pick,overflow_pick "
                         "(default: enter_now). Calls are deduped per (ticker, date, rec_type).")
    args = ap.parse_args()
    rec_types = [t.strip() for t in args.rec_types.split(",") if t.strip()]
    if not rec_types:
        raise SystemExit("--rec-types needs at least one rec_type.")

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
    loaded = recs[recs["rec_type"].isin(rec_types)] if len(recs) else recs
    rows = loaded.to_dict("records")
    calls = eno.dedupe_calls(rows, by_rec_type=True)
    if not calls:
        print(f"No {'/'.join(rec_types)} calls found ({len(rows)} rows loaded). Nothing to read.")
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

    def return_fn(ticker, rec_date, price, horizon):
        return eno.forward_return_pct(ticker, rec_date, price, horizon, close_fn)

    print(f"Pricing {len(calls)} calls at horizons {HEADLINE_HORIZON} and {EARLY_HORIZON} ...",
          file=sys.stderr)
    graded: list[dict] = []
    horizons = (HEADLINE_HORIZON, EARLY_HORIZON)
    stats = {(rt, h): {"graded": 0, "not_matured": 0, "unpriced": 0}
             for rt in rec_types for h in horizons}
    for call in calls:
        c = eno.classify_call(call, spy_close=spy_close, spx_close=spx_close,
                              vix_close=vix_close, trend_fn=trend_fn)
        for h in horizons:
            status, alpha = eno.grade_call(c, h, today, alpha_fn)
            c[f"alpha_{h}"] = alpha
            # the own return is only read for a call that graded, so the two
            # figures cover the same calls (the fetch is memoised: no extra call)
            c[f"ret_{h}"] = eno.grade_return(c, h, today, return_fn) if status == "graded" else None
            stats[(c.get("rec_type", rec_types[0]), h)][status] += 1
        graded.append(c)

    first, last = calls[0]["rec_date"], calls[-1]["rec_date"]
    print(f"Entry-regime readout over {', '.join(rec_types)} calls (read-only), run {today}")
    print(f"rec_date range {first} .. {last}")
    print(f"rows loaded {len(rows)}, calls after dedupe to one per (ticker, date, rec_type) "
          f"{len(calls)} ({len(rows) - len(calls)} duplicate or unusable rows dropped)")
    print("APPROXIMATIONS: market state read at the prior session's close; day-move tone is the "
          "call-day full-day S&P close-to-close (the app's 09:45 read was intraday); adjusted "
          "Yahoo closes; beat SPY = alpha > 0; thin cells withhold their figures.")
    print("The rule A / B / C verdicts come from stock_analyzer.entry_regime (the last "
          "completed session's close; the VIX close must be dated that session). Rule A is the "
          "pre-registered primary; B and C are named secondaries; the Nasdaq split and any "
          "other cut are EXPLORATORY (post-hoc).")
    print("No split by recommendation score, by design.")

    # Verdicts for every NYSE session in the range (not only call dates), read the
    # same way classify_call reads a call: the memoised trend per session + the VIX.
    session_verdicts = eno.session_rule_verdicts(first, last, trend_fn=trend_fn,
                                                 vix_close=vix_close)
    csv_rows: list[dict] = []
    for rt in rec_types:
        rt_calls = [c for c in graded if c.get("rec_type", rec_types[0]) == rt]
        if not rt_calls:
            print(f"\n(no {rt} calls)")
            continue
        episodes = eno.group_episodes(rt_calls, args.episode_gap)
        for ep in episodes:
            for c in ep["calls"]:
                c["episode_id"] = ep["episode_id"]
                c["n_calls_in_episode"] = ep["n_calls"]
        episode_rows = [ep["first"] for ep in episodes]
        csv_rows.extend(rt_calls)

        # Only enter_now is the pre-registered population (go-live block). With more
        # than one type selected, any other type gets the compact, exploratory view.
        is_primary = rt == "enter_now"
        compact = len(rec_types) > 1 and not is_primary
        label = rt.upper() if is_primary else f"{rt.upper()} (NOT pre-registered)"
        print(f"\n{'#' * 78}\n{label}: {len(rt_calls)} calls, {len(episodes)} episodes "
              f"(gap {args.episode_gap} sessions); regime unknown for "
              f"{sum(1 for c in rt_calls if c['regime_armed'] == eno.UNKNOWN)} calls\n{'#' * 78}")
        lo, hi = rt_calls[0]["rec_date"], rt_calls[-1]["rec_date"]
        print_hold_rates(rt_calls, {d: v for d, v in session_verdicts.items() if lo <= d <= hi})
        # The verdict is printed only for the pre-registered population (plan
        # section 4): every logged call and the default episode gap.
        go_live = None
        if is_primary:
            if eno.go_live_population_ok(args.episode_gap, args.since):
                go_live = eno.go_live_check(episode_rows, episode_rows)
            else:
                print(f"\n{eno.GO_LIVE_SKIPPED_LINE}")
        for h, heading in ((HEADLINE_HORIZON,
                            f"PRE-REGISTERED HEADLINE ({HEADLINE_HORIZON}-session horizon)"),
                           (EARLY_HORIZON,
                            f"EARLY READ, NOT THE PRE-REGISTERED HORIZON ({EARLY_HORIZON}-session horizon)")):
            print_report(rt_calls, episode_rows, h, heading, stats[(rt, h)],
                         rec_label=rt.upper(), compact=compact,
                         go_live=go_live if h == HEADLINE_HORIZON else None,
                         primary=is_primary,
                         go_live_where=(("below" if h == HEADLINE_HORIZON
                                         else f"in the {HEADLINE_HORIZON}-session section above")
                                        if is_primary and go_live is not None else None))
    graded = csv_rows

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(graded)
        print(f"\nwrote {len(graded)} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
