#!/usr/bin/env python3
"""Reconcile per-ticker daily_snapshots rows with repaired account rows
(bug register N1, chunk C7; docs/plans/account-snapshot-repair.md step 7).

The N1 repairs (C3 backfill, C5 nightly auto-repair) fixed
account_daily_snapshots.gross_book and book_positions, but the per-ticker
daily_snapshots rows for those dates kept the pre-repair book. The nightly
auto-repair now reconciles repaired rows inside its 35-day lookback; this
script covers the OLDER repaired dates once.

For every account_daily_snapshots row with `repaired_at` set and a date before
today (ET) it plans the rows `daily_snapshots` should hold (late buy: upsert
with int shares; short row: top-up; late full exit: delete; the pure planner is
stock_analyzer/snapshot_row_repair.py) and prints a per-date table, every
refusal, and the projected effect (the book's gross vs the per-ticker sum, now
and after).

READ-ONLY BY DEFAULT: nothing is written without `--apply`. `--apply` writes
each date through db.repair_daily_snapshot_rows (never today's date), re-reads
and re-plans, and fails if anything is still left to do.

Side effects: reads account_daily_snapshots and daily_snapshots; with --apply,
upserts / deletes daily_snapshots rows for the planned tickers and dates only.
Needs SUPABASE_URL and SUPABASE_KEY (the service-role key) in the environment
(run from a normal shell; never from the app).

Exit codes: 0 ok; 1 a write failed; 2 bad input / unreadable table; 3 the
post-apply re-plan still proposes work (or the re-read failed); 4 some date was
REFUSED (no readable book, empty book, non-trading day: see the "Refused" list;
in a dry run, or after --apply, which still writes the plannable dates). A
failed write (1) wins over 4, and 3 wins over both.

Usage (dry run, then apply):
    python scripts/daily_snapshot_row_repair.py
    python scripts/daily_snapshot_row_repair.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import db  # noqa: E402
from stock_analyzer.account import _parse_date  # noqa: E402
from stock_analyzer.account_snapshot_autorepair import _is_set, _records  # noqa: E402
from stock_analyzer.market_time import today_et  # noqa: E402
from stock_analyzer.snapshot_row_repair import (  # noqa: E402
    apply_plan_to_rows, plan_snapshot_rows,
)


def _die(msg: str, code: int = 2):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def repaired_books(acct_df, today) -> dict:
    """{date: book_positions} for every account row with `repaired_at` set and a
    date before `today`."""
    out: dict = {}
    for r in _records(acct_df):
        d = _parse_date(r.get("snapshot_date"))
        if d is not None and d < today and _is_set(r.get("repaired_at")):
            out[d] = r.get("book_positions")
    return out


def rows_by_date(snap_df) -> dict:
    out: dict = {}
    for r in _records(snap_df):
        d = _parse_date(r.get("snapshot_date"))
        if d is not None:
            out.setdefault(d, []).append(r)
    return out


def plan_all(acct_df, snap_df, today) -> list:
    """[(date, stored rows, plan)] for every repaired date, oldest first."""
    books, by_date = repaired_books(acct_df, today), rows_by_date(snap_df)
    return [(d, by_date.get(d, []), plan_snapshot_rows(d, books[d], by_date.get(d, []), today))
            for d in sorted(books)]


def _value(rows) -> float:
    total = 0.0
    for r in rows:
        try:
            total += float(r.get("shares")) * float(r.get("close_price"))
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def _book_value(book):
    """Sum of shares x close over a book dict (the account row's gross), or None."""
    if not isinstance(book, dict):
        return None
    try:
        return round(sum(float(v["shares"]) * float(v["close"]) for v in book.values()), 2)
    except (TypeError, ValueError, KeyError):
        return None


def print_report(planned, books, out=print) -> None:
    todo = [p for _d, _r, p in planned if p["upserts"] or p["deletes"]]
    refused = [p for _d, _r, p in planned if p["refusal"]]
    out(f"Repaired account rows: {len(planned)}; {len(todo)} need per-ticker row changes, "
        f"{len(refused)} refused.")
    for d, stored, p in planned:
        if p["refusal"]:
            continue
        if not (p["upserts"] or p["deletes"]):
            out(f"{p['date']}  already consistent")
            continue
        out("")
        out(f"{p['date']}")
        for u in p["upserts"]:
            old = p["previous"].get(u["ticker"])
            was = "(no row)" if old is None else f"{old:g} sh"
            out(f"    {u['ticker']:<6} {was} -> {u['shares']} sh @ {u['close_price']:,.2f}  "
                f"[{'insert' if old is None else 'update'}]")
        for tk in p["deletes"]:
            old = p["previous"].get(tk)
            out(f"    {tk:<6} {'?' if old is None else f'{old:g}'} sh -> (no row)  [delete]")
        after = apply_plan_to_rows(stored, p)
        bv = _book_value(books.get(d))
        out(f"    book gross {'n/a' if bv is None else f'{bv:,.2f}'}; "
            f"per-ticker sum {_value(stored):,.2f} -> {_value(after):,.2f} "
            "(int shares truncate, so a small gap to the book is expected)")
    if refused:
        out("")
        out("Refused (nothing written for these):")
        for p in refused:
            out(f"    {p['date']}: [{p['refusal']['kind']}] {p['refusal']['reason']}")


def run(args, today=None) -> int:
    today = today or today_et()
    acct = db.load_account_daily_snapshots()
    if acct is None:
        _die("account_daily_snapshots could not be read.")
    books = repaired_books(acct, today)
    if not books:
        print("No repaired account rows before today: nothing to do.")
        return 0
    start = min(books)
    snap = db.load_daily_snapshots_or_none(start_date=start.isoformat())
    if snap is None:
        _die("daily_snapshots could not be read.")
    planned = plan_all(acct, snap, today)
    print_report(planned, books)
    refused = [p["date"] for _d, _r, p in planned if p["refusal"]]
    refused_code = 4 if refused else 0

    if not args.apply:
        print("\nDry run: nothing written. Re-run with --apply to write these rows.")
        if refused:
            print(f"Refused dates remain ({len(refused)}); see above.", file=sys.stderr)
        return refused_code

    failed = []
    for _d, _stored, p in planned:
        if p["refusal"] or not (p["upserts"] or p["deletes"]):
            continue
        ok = db.repair_daily_snapshot_rows(p["date"], p["upserts"], p["deletes"])
        print(f"{'applied' if ok else 'FAILED '} {p['date']}")
        if not ok:
            failed.append(p["date"])

    snap2 = db.load_daily_snapshots_or_none(start_date=start.isoformat())
    if snap2 is None:
        _die("re-read after apply failed; can't confirm idempotence.", code=3)
    left = [p["date"] for _d, _r, p in plan_all(acct, snap2, today)
            if (p["upserts"] or p["deletes"]) and p["date"] not in failed]
    if left:
        print(f"WARNING: re-plan still proposes changes for: {left}", file=sys.stderr)
        return 3
    print("Re-plan after apply: nothing left to do for the applied dates (idempotent).")
    if refused:
        print(f"Refused dates remain ({len(refused)}); see above.", file=sys.stderr)
    return 1 if failed else refused_code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write the rows (default: dry run)")
    args = ap.parse_args(argv)
    for var in ("SUPABASE_URL", "SUPABASE_KEY"):
        if not os.environ.get(var):
            _die(f"{var} is not set in the environment.")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
