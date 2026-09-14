#!/usr/bin/env python3
"""Holding-period / entry-discipline analysis (roadmap item A4).

`docs/plans/investor-maturity-roadmap.md` §4 A4 -- alternative #2 of the six
strategy alternatives that analysis ranked by evidence support. Runs FIRST
of the roadmap's read-only analyses because it is upstream of a build
decision: `exit_advisor`'s deterioration ladder needs 2-of-3 sessions below
SMA50 plus a drawdown from peak to confirm TRIM/EXIT -- structurally a
multi-WEEK mechanism. `scripts/exit_ladder_replay.py` (W6) already found
52% of 54 closed losing round trips got no protective signal at all, and
most were held 0-10 days. THIS script asks the question that finding left
open: is that a genuine strategy/design mismatch (positions closed before
any weeks-scale mechanism has room to act), or an artefact of small /
deliberately-short trades that were never meant to be held that long?

The answer gates the roadmap's own B1 item (score-history decay readout,
also a weeks-scale signal): if the real holding period is mostly days, a
future decay readout may be answering a question that doesn't apply to
this book. This script does not decide that -- it reports the distribution
so that decision can be made with real numbers instead of the single
"most were held 0-10 days" data point W6 already surfaced.

WHAT THIS DOES. Builds every completed FIFO-matched round-trip lot fragment
(wins AND losses -- deliberately not scoped to losers only, unlike W6,
because a holding-period mismatch would show up across outcomes, not just
losing ones) via `investor_mirror.build_closed_lots`, the SAME pure
FIFO-lot replay the My Edge / Investor Mirror page uses -- not a
re-implementation. Reports:
  1. Holding-period distribution in buckets (days), overall and split by
     win/loss -- reuses `investor_mirror.disposition_effect` for the
     share-weighted winner/loser average (do not write a second average).
  2. Position-size cut: dollar exposure (shares x buy_price) split into
     terciles, holding period by tercile.
  3. OPTIONAL, separately dated: an app-aligned vs self-initiated cut via
     `self_track_record.classify_sells`, restricted to sells on/after
     `SELF_TRACK_SELL_RELIABLE_LOG_START` -- a materially shorter window
     than the full trade history, so this is reported as a distinct,
     labelled subset, never blended into the primary distribution.

REDLINE. Read-only historical measurement. Touches no gate, no constant, no
recommendation, no threshold in constants.py. If the results argue for a
policy change (position sizing, holding-period discipline, or re-scoping
the deterioration ladder's confirmation window), that is a separate policy
conversation with the owner -- this script produces evidence, not a
recommendation.

HONEST CAVEAT, printed on every run: this reads the FULL trade journal, not
just closed losers, so N is larger than W6's -- but still a single retail
account's history. A short average holding period is not by itself a
verdict on strategy quality; it could reflect deliberate short-term trades,
stop-outs, or profit-taking working as designed. Read the win/loss split
and the position-size cut before drawing a conclusion from the headline
median alone.

NOT A FALSIFIABLE-CRITERION SCRIPT (unlike offense_attribution.py's
pre-registered pass/fail). A4's own question -- "is this a real mismatch or
an artefact of small/short trades" -- is a judgment call for the owner to
make from the reported distribution, not a criterion this script can
mechanically resolve. It reports; it does not conclude.

Requires:
  - Supabase credentials (SUPABASE_URL / SUPABASE_KEY) to read `trades` and
    `exit_signals` -- this project's DB is hosted-only (see CLAUDE.md), so
    run this with the same env vars Railway uses. Per
    `exit_ladder_replay.py`'s own note: the Railway Console shell is NOT a
    usable environment for this -- run from a normal shell instead.
  - No price history / yfinance calls -- this script never fetches prices,
    it only replays the trade journal already in Supabase.
  - `requests` (already a project dependency) -- talks to Supabase's
    PostgREST endpoint directly, same reasoning as exit_ladder_replay.py:
    a single read-only GET has no reason to pull in the `supabase` SDK's
    heavier dependency chain (pyiceberg etc.) for this.

Usage:
    python scripts/holding_period_analysis.py
    python scripts/holding_period_analysis.py --ticker MU   # one ticker only
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import investor_mirror  # noqa: E402
from stock_analyzer import self_track_record as _stv  # noqa: E402
from stock_analyzer.constants import (  # noqa: E402
    BEHAVIORAL_MIN_SAMPLE_N,
    SELF_TRACK_SELL_RELIABLE_LOG_START,
    SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
)

# Holding-period buckets (days), upper-bound inclusive. Display buckets only
# -- not a threshold, not written to constants.py (no gate/policy reads these).
_BUCKETS = [
    (0, 3,   "0-3d"),
    (4, 10,  "4-10d"),
    (11, 20, "11-20d"),
    (21, 45, "21-45d"),
    (46, 90, "46-90d"),
    (91, None, "91d+"),
]


def _bucket_label(days: int) -> str:
    for lo, hi, label in _BUCKETS:
        if hi is None:
            if days >= lo:
                return label
        elif lo <= days <= hi:
            return label
    return "?"


def _rest_get(table: str, select: str, order: str | None = None) -> "pd.DataFrame | None":
    """Read-only PostgREST GET, mirroring exit_ladder_replay.load_trades().

    Returns `None` when credentials are absent or the request itself fails
    (network, auth, RLS) -- distinguished from a genuinely empty table,
    which returns an empty DataFrame.
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    params = {"select": select}
    if order:
        params["order"] = order
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/rest/v1/{table}",
            params=params,
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        print(f"Could not read the {table} table: {e}")
        return None
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def load_trades() -> "pd.DataFrame | None":
    return _rest_get("trades", "*", order="traded_at.desc")


def load_exit_signals() -> "pd.DataFrame | None":
    return _rest_get("exit_signals", "*", order="signal_date.desc")


def _tercile_labels(values: pd.Series) -> pd.Series:
    """Small/Medium/Large position-size terciles, by RELATIVE RANK rather
    than raw quantile bin edges. A retail trade journal routinely has tied
    dollar-exposure values (e.g. several trades sized to the same $1,000
    round number) -- a raw pd.qcut on the values themselves raises on
    duplicate bin edges even when there is genuine spread across the full
    sample, which would misreport "insufficient spread" when the real
    issue is just ties. Ranking first (method='first' breaks ties by
    original order, never dropping a row) guarantees three genuine terciles
    whenever there are >= 3 distinct rows, at the cost of an assignment
    that can put two identically-sized trades in different terciles --
    an acceptable trade for "always report a real tercile cut."
    Only falls back to a single bucket when there are truly fewer than 3
    rows to rank."""
    if len(values) < 3:
        return pd.Series(["All (< 3 rows)"] * len(values), index=values.index)
    ranks = values.rank(method="first")
    return pd.qcut(ranks, 3, labels=["Small", "Medium", "Large"])


def _print_distribution(lots: pd.DataFrame, ticker: str | None) -> None:
    n = len(lots)
    print(
        f"\n{'=' * 78}\n"
        f"HONEST CAVEAT: N = {n} completed round-trip lot fragment(s) "
        f"(wins AND losses). A short\naverage holding period is not by "
        f"itself a verdict on strategy quality -- read the\nwin/loss split "
        f"and position-size cut below before concluding from the median "
        f"alone.\nThis is a JUDGMENT-CALL report, not a pass/fail "
        f"criterion (unlike offense_attribution.py).\n{'=' * 78}\n"
    )
    if n == 0:
        print("No completed round trips found. Nothing to analyze.")
        return

    valid = lots.dropna(subset=["days_held", "pnl_abs", "shares", "buy_price"])
    if valid.empty:
        print("No round trips had complete days_held/pnl_abs/shares/buy_price data.")
        return

    valid = valid.copy()
    valid["bucket"] = valid["days_held"].astype(int).apply(_bucket_label)
    valid["outcome"] = valid["pnl_abs"].apply(lambda p: "WIN" if p >= 0 else "LOSS")
    valid["dollar_exposure"] = valid["shares"] * valid["buy_price"]

    # ── 1. Overall holding-period distribution ──────────────────────────────
    print(f"HOLDING-PERIOD DISTRIBUTION (N={len(valid)}{f', ticker={ticker}' if ticker else ''}):")
    order = [b[2] for b in _BUCKETS]
    counts = valid["bucket"].value_counts().reindex(order, fill_value=0)
    for label, cnt in counts.items():
        pct = cnt / len(valid) * 100.0
        bar = "#" * int(round(pct / 2))
        print(f"  {label:<10} {cnt:>4} ({pct:5.1f}%)  {bar}")
    median_days = valid["days_held"].median()
    print(f"\n  Median holding period: {median_days:.0f} days")

    # ── 2. Win/loss split via distribution + disposition_effect() reuse ─────
    print("\nBY OUTCOME:")
    for outcome in ("WIN", "LOSS"):
        sub = valid[valid["outcome"] == outcome]
        if sub.empty:
            print(f"  {outcome}: no rows")
            continue
        sub_counts = sub["bucket"].value_counts().reindex(order, fill_value=0)
        dist = ", ".join(f"{lbl}={cnt}" for lbl, cnt in sub_counts.items() if cnt > 0)
        print(f"  {outcome} (N={len(sub)}, median {sub['days_held'].median():.0f}d): {dist}")

    disp = investor_mirror.disposition_effect(lots, min_n=BEHAVIORAL_MIN_SAMPLE_N)
    if disp is None:
        print(
            f"\n  disposition_effect(): below BEHAVIORAL_MIN_SAMPLE_N="
            f"{BEHAVIORAL_MIN_SAMPLE_N} winners or losers -- no share-weighted "
            f"avg reported (this is the SAME house floor used elsewhere in "
            f"the app for exactly this reason: too few samples per bucket "
            f"renders a misleading pattern)."
        )
    else:
        print(
            f"\n  disposition_effect() [share-weighted, from investor_mirror.py -- "
            f"not re-derived here]:\n"
            f"    Winners held {disp['winner_avg_days']}d avg (N={disp['n_winners']})\n"
            f"    Losers  held {disp['loser_avg_days']}d avg (N={disp['n_losers']})\n"
            f"    Ratio (loser/winner): {disp['ratio']}"
        )

    # ── 3. Position-size (dollar exposure) cut ───────────────────────────────
    print("\nBY POSITION SIZE (dollar exposure = shares x buy_price, terciles):")
    valid["size_tercile"] = _tercile_labels(valid["dollar_exposure"])
    for tercile in valid["size_tercile"].cat.categories if hasattr(valid["size_tercile"], "cat") else []:
        sub = valid[valid["size_tercile"] == tercile]
        if sub.empty:
            continue
        lo, hi = sub["dollar_exposure"].min(), sub["dollar_exposure"].max()
        print(
            f"  {tercile:<8} (${lo:,.0f}-${hi:,.0f}, N={len(sub)}): "
            f"median {sub['days_held'].median():.0f}d held"
        )
    if not hasattr(valid["size_tercile"], "cat"):
        print(f"  Fewer than 3 completed round trips (N={len(valid)}) -- no tercile cut possible.")


def _print_self_track_cut(trades_df: pd.DataFrame, exit_signals_df) -> None:
    print(
        f"\n{'-' * 78}\n"
        f"OPTIONAL CUT -- app-aligned vs self-initiated SELLS, restricted to sells "
        f"on/after\n{SELF_TRACK_SELL_RELIABLE_LOG_START} "
        f"(SELF_TRACK_SELL_RELIABLE_LOG_START). This window is MATERIALLY "
        f"SHORTER\nthan the full trade history above -- report it separately, "
        f"never blend it into the\nprimary distribution.\n{'-' * 78}\n"
    )
    if exit_signals_df is None:
        print(
            "exit_signals could not be read (see error above, or credentials "
            "issue) -- skipping this cut. The primary holding-period "
            "distribution above is unaffected."
        )
        return
    classified = _stv.classify_sells(
        trades_df, exit_signals_df,
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    if classified is None:
        print("classify_sells() returned None unexpectedly -- skipping this cut.")
        return
    if not classified:
        print("No SELL rows to classify.")
        return
    buckets: dict[str, int] = {}
    for row in classified:
        buckets[row["bucket"]] = buckets.get(row["bucket"], 0) + 1
    for bucket, cnt in buckets.items():
        print(f"  {bucket}: {cnt}")
    n_reliable = buckets.get("engine_aligned", 0) + buckets.get("self_initiated", 0)
    print(
        f"\n  N={n_reliable} sells within the reliable-log window "
        f"(coverage_limited rows are pre-{SELF_TRACK_SELL_RELIABLE_LOG_START} "
        f"and excluded from this split by definition)."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--ticker", default=None, help="restrict to one ticker")
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
        print("Connected fine -- the trades table is genuinely empty. Nothing to analyze.")
        return 0

    if args.ticker:
        want = args.ticker.strip().upper()
        trades_df = trades_df[trades_df["ticker"].astype(str).str.upper() == want]
        if trades_df.empty:
            print(f"No trades found for ticker {want}.")
            return 0

    lots = investor_mirror.build_closed_lots(trades_df)
    _print_distribution(lots, args.ticker)

    exit_signals_df = load_exit_signals()
    _print_self_track_cut(trades_df, exit_signals_df)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
