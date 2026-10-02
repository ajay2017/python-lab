#!/usr/bin/env python3
"""
R-Multiple legacy report — print each closed round-trip episode's realized
P&L measured against the risk that WOULD have been planned for it, using the
app's own ATR-based entry-stop reconstruction (the "engine" lens — see
`stock_analyzer/r_multiple.py`).

WHAT THIS DOES. For every ticker ever traded, reconstructs round-trip
episodes via `ticker_history.build_ticker_history` (the SAME episode builder
the 🧾 Prior Trades tab uses — not re-implemented here), then for every
CLOSED episode computes an R-multiple via `r_multiple.episode_r_multiple`
("engine" lens only — the "declared" lens needs a capture step that has not
shipped yet and will correctly report "no plan" for every episode today).
Also prints `r_multiple.add_flags` ("added while losing" / averaging down).

REDLINE. Read-only, print-only historical measurement. Writes nothing to the
database, touches no gate/constant/recommendation. Modeled directly on
`scripts/exit_ladder_replay.py` (same owner-runs-with-own-credentials
pattern, same read-only posture).

HONEST CAVEAT, printed on every run: R-multiple coverage depends on having
≥15 trading-day bars of price history before each buy leg's own date — an
episode entered very early in the available price-history window, or on a
ticker/period combination with thin lookback, will correctly show "no R"
rather than a fabricated number. Coverage is reported explicitly at the end.

Requires:
  - Supabase credentials (SUPABASE_URL / SUPABASE_KEY) to read `trades` —
    same as `exit_ladder_replay.py`; this project's DB is hosted-only.
  - Public daily price history via yfinance — no credentials needed.
  - `requests` for the trades read (talks to Supabase's PostgREST endpoint
    directly, bypassing the `supabase` SDK — same reason as
    `exit_ladder_replay.py`'s own docstring explains).

Usage:
    python scripts/r_multiple_legacy_report.py                # every ticker
    python scripts/r_multiple_legacy_report.py --ticker MU     # one ticker
    python scripts/r_multiple_legacy_report.py --period 5y     # longer fetch
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import data as _data  # noqa: E402
from stock_analyzer import r_multiple  # noqa: E402
from stock_analyzer import ticker_history  # noqa: E402


def load_trades() -> "pd.DataFrame | None":
    """Trades via Supabase's PostgREST endpoint directly — same approach and
    same reasoning as `exit_ladder_replay.py::load_trades`. Returns None when
    credentials are absent or the request itself fails; an empty-but-
    successful response is a genuinely empty `trades` table (empty DataFrame,
    not None)."""
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


def _fmt_r(r: float | None) -> str:
    return f"{r:+.2f}R" if r is not None else "n/a"


def _fmt_money(v: float | None) -> str:
    return f"${v:,.2f}" if v is not None else "n/a"


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
        print("Connected fine -- the trades table is genuinely empty. Nothing to report.")
        return 0

    tickers = sorted(trades_df["ticker"].astype(str).str.upper().str.strip().unique())
    if args.ticker:
        want = args.ticker.strip().upper()
        tickers = [t for t in tickers if t == want]

    print(f"Found {len(tickers)} ticker(s) with trade history.\n")

    rows_printed = 0
    r_values: list[float] = []
    n_closed_total = 0
    n_resolvable = 0
    losers_worse_than_1r = 0

    header = f"{'TICKER':<8}{'CLOSED':<12}{'REALIZED P&L':<16}{'R-MULTIPLE':<14}{'ADDED WHILE LOSING':<20}"
    print(header)
    print("-" * len(header))

    for ticker in tickers:
        try:
            bundle = _data.fetch_ticker_bundle(ticker, args.period)
        except Exception as e:
            print(f"{ticker:<8}-- price history fetch failed: {e}")
            continue
        ohlc_df = bundle.get("history") if isinstance(bundle, dict) else None
        if ohlc_df is None or ohlc_df.empty:
            print(f"{ticker:<8}-- no price history returned")
            continue

        hist = ticker_history.build_ticker_history(trades_df, ticker, spy_history_df=None)
        if hist is None:
            continue

        for ep in hist["episodes"]:
            if ep["status"] != "closed":
                continue
            n_closed_total += 1
            res = r_multiple.episode_r_multiple(ep, "engine", ohlc_df)
            flags = r_multiple.add_flags(ep)
            r_val = res.get("r_multiple")
            exit_str = ep["exit_date"].isoformat() if ep["exit_date"] else "unknown"
            pnl = ep.get("realized_pnl")
            awl = "yes" if flags["added_while_losing"] else "no"

            if r_val is not None:
                n_resolvable += 1
                r_values.append(r_val)
                if r_val < -1.0:
                    losers_worse_than_1r += 1
                r_str = _fmt_r(r_val)
            else:
                r_str = f"no R ({res.get('reason', 'unknown')})"

            print(f"{ticker:<8}{exit_str:<12}{_fmt_money(pnl):<16}{r_str:<14}{awl:<20}")
            rows_printed += 1

    print("-" * len(header))
    if rows_printed == 0:
        print("\nNo closed episodes found across any ticker. Nothing to summarize.")
        return 0

    print(f"\nCoverage: R available for {n_resolvable} of {n_closed_total} closed episode(s).")
    if n_resolvable > 0:
        mean_r = sum(r_values) / len(r_values)
        sorted_r = sorted(r_values)
        mid = len(sorted_r) // 2
        median_r = (sorted_r[mid] if len(sorted_r) % 2 == 1
                    else (sorted_r[mid - 1] + sorted_r[mid]) / 2)
        print(f"Mean R: {mean_r:+.2f}   Median R: {median_r:+.2f}")
        losing = [r for r in r_values if r < 0]
        print(
            f"Losing episodes worse than -1.0R: {losers_worse_than_1r} of "
            f"{n_resolvable} resolvable ({(losers_worse_than_1r / n_resolvable * 100.0):.0f}%). "
            f"(Descriptive stat only -- not a classification of whether the risk plan "
            f"was 'honored'; that call is explicitly deferred to a later phase.)"
        )
        print(f"({len(losing)} of {n_resolvable} resolvable episodes were losers overall.)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
