#!/usr/bin/env python3
"""Backfill LEGACY account_daily_snapshots rows (bug register N1, chunk C3;
docs/plans/account-snapshot-repair.md).

Every row written before the provenance columns existed (since 2026-09-10) has
NULL provenance, so the staleness check never flags it. This script finds the
rows whose stored book disagrees with the journal (replayed to the row's own
cash_as_of cutoff) and daily_snapshots' share counts, and repairs gross_book,
net_equity, leverage, cushion and call_distance_pct (cash is never touched).

READ-ONLY BY DEFAULT: it prints every proposed repair and every refusal, the
weekly debriefs that may have read an affected date, and the October monthly
hint, and writes NOTHING. It also lists rows whose share counts match the
journal but whose stored gross does not ("needs review", never applied).
`--apply` first requires the four provenance columns to exist
(docs/sql/account-snapshot-provenance.sql), applies every `missing_ticker`
repair (its repaired gross reconciles to the journal book to the cent),
applies `partial_quantity` and `unreconciled` repairs ONLY for dates named in
`--apply-partial`, then re-plans and prints that applied rows no longer appear
(idempotence).

Closes: the stored daily_snapshots.close_price first, then Yahoo's
split-adjusted, dividend-UNadjusted daily close for the exact date. A close is
never carried forward from another day. A missing close for a touched ticker
refuses the row; one for an untouched ticker makes the row a review (no touched
ticker) or `unreconciled` (touched).

Side effects: reads account_daily_snapshots, trades, daily_snapshots and
weekly_debriefs; with --apply, UPDATEs account_daily_snapshots rows only.
Needs SUPABASE_URL and SUPABASE_KEY (the service-role key) in the environment
(run from a normal shell; never from the app).

Usage (dry run, then apply):
    python scripts/account_snapshot_backfill.py
    python scripts/account_snapshot_backfill.py --json
    python scripts/account_snapshot_backfill.py --apply
    python scripts/account_snapshot_backfill.py --apply --apply-partial 2026-10-28,2026-10-30
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import account_snapshot_repair as repair  # noqa: E402
from stock_analyzer import data as data_mod  # noqa: E402,F401  (tests patch data_mod.fetch_unadjusted_history)
from stock_analyzer import db  # noqa: E402
from stock_analyzer.account import _parse_date  # noqa: E402
from stock_analyzer.account_snapshot_autorepair import (  # noqa: E402,F401
    _CLOSE_FETCH_PERIOD as FETCH_PERIOD, fetch_missing_closes, snapshot_shares_and_closes,
)
from stock_analyzer.market_time import now_et  # noqa: E402
from stock_analyzer.performance_review import last_trading_day_on_or_before  # noqa: E402

DEFAULT_SINCE = "2026-09-10"


def _die(msg: str, code: int = 2):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _records(df) -> list:
    return [] if df is None else df.to_dict("records")


def plan(acct_df, trades_df, snap_df, now):
    """Load-independent planning step:
    (repairs, refusals, reviews, clean_count, closes)."""
    shares, closes = snapshot_shares_and_closes(snap_df)
    rows = _records(acct_df)
    needs = repair.legacy_close_needs(rows, trades_df, shares)
    closes = {**closes, **fetch_missing_closes(needs, closes)}
    repairs, refusals, reviews = repair.plan_legacy_repairs(rows, trades_df, shares, closes, now)
    legacy = [r for r in rows if repair._int_or_none(r.get("journal_max_trade_id")) is None]
    clean = (len(legacy) - len(repairs) - len(reviews)
             - len([x for x in refusals if x.get("snapshot_date")]))
    return repairs, refusals, reviews, max(clean, 0), closes


def affected_debriefs(repaired_dates, debriefs_df) -> list:
    """Weekly debriefs that used a repaired date as a week endpoint. A debrief
    measures from the last trading day on or before week_ending - 7 to the
    last trading day on or before week_ending (both computed with
    `performance_review.last_trading_day_on_or_before`). Approximate: a
    debrief whose endpoint row was invalid may have searched back to a
    neighbouring row. [(week_ending, [repaired dates])], oldest first."""
    out: dict = {}
    for r in _records(debriefs_df):
        we = _parse_date(r.get("week_ending"))
        if we is None:
            continue
        anchors = {last_trading_day_on_or_before(we),
                   last_trading_day_on_or_before(we - timedelta(days=7))}
        hit = sorted(d for d in repaired_dates if d in anchors)
        if hit:
            out[we] = hit
    return sorted(out.items())


def october_hint(repaired_dates) -> "str | None":
    octs = sorted(d for d in repaired_dates if d.month == 10)
    if not octs:
        return None
    return ("October monthly return (Reports): its end anchor is the last valid October "
            f"account row; repaired October date(s): {', '.join(d.isoformat() for d in octs)}. "
            "Nothing to regenerate: after applying, hard-refresh the app (Ctrl+F5; the "
            "account rows are cached per session for the day) and open Reports > "
            "Performance Review with an October period (Last month during November, else "
            "Custom), which recomputes it. Don't use Monthly Portfolio Intelligence's "
            "Generate button: that report doesn't read account rows, and "
            "the button saves a NEW trailing 4-week report that takes over its month's "
            "slot in the picker.")


def _fmt(v, spec="{:,.2f}"):
    return "n/a" if v is None else spec.format(v)


def print_report(repairs, refusals, reviews, clean, debriefs, debriefs_known=True,
                 out=print) -> None:
    out(f"Legacy rows: {len(repairs)} to repair, {len(refusals)} refused, "
        f"{len(reviews)} need review, {clean} clean.")
    for r in repairs:
        out("")
        note = {"missing_ticker": "auto-applied by --apply",
                "partial_quantity": "needs --apply-partial DATE",
                "unreconciled": "gross does not reconcile to the journal book: "
                                "needs --apply-partial DATE after you check it"}[r["category"]]
        out(f"{r['snapshot_date']}  [{r['category']}]  ({note})")
        for t in r["touched"]:
            out(f"    {t['ticker']:<6} {t['snapshot_shares']} -> {t['replay_shares']} sh "
                f"@ {t['close']:,.2f}  = {t['value_delta']:+,.2f}")
        out(f"    gross     {_fmt(r['old_gross_book'])} -> {_fmt(r['new_gross_book'])}")
        out(f"    net eq.   {_fmt(r['old_net_equity'])} -> {_fmt(r['new_net_equity'])}")
        out(f"    leverage  {_fmt(r['old_leverage'], '{:.2f}x')} -> {_fmt(r['new_leverage'], '{:.2f}x')}")
        out(f"    gap before (gross - journal book x closes): {_fmt(r['gap_before'])}; "
            f"residual after: {_fmt(r['residual'])}")
        if r["book_positions"] is None:
            out("    book_positions NOT written (the repaired gross is not reconciled)")
    if reviews:
        out("")
        out("Needs review (never applied): share counts match the journal, but the stored gross "
            "couldn't be reconciled with the journal book x that day's closes. A gap can be a "
            "trade logged after EOD, or the app re-saving daily_snapshots after EOD at a "
            "different price; each line gives its reason.")
        for x in reviews:
            out(f"    {x['snapshot_date']}: {x['reason']}")
    if refusals:
        out("")
        out("Refused (nothing written for these):")
        for x in refusals:
            tag = " [no daily_snapshots rows]" if x.get("no_snapshot_rows") else ""
            out(f"    {x.get('snapshot_date') or '(all rows)'}: {x['reason']}{tag}")
    dates = {_parse_date(r["snapshot_date"]) for r in repairs}
    out("")
    if debriefs:
        out("Weekly debriefs that used a repaired date as an endpoint (frozen unless regenerated):")
        for we, ds in debriefs:
            out(f"    week ending {we.isoformat()}  <- {', '.join(d.isoformat() for d in ds)}")
    elif not debriefs_known:
        out("(debriefs couldn't be read, or none exist)")
    else:
        out("No weekly debrief used a repaired date as an endpoint.")
    hint = october_hint(dates)
    if hint:
        out(hint)


def _selected(repairs, apply_partial: set) -> list:
    """missing_ticker repairs always; every other category only for a date the
    owner named in --apply-partial."""
    return [r for r in repairs
            if r["category"] == "missing_ticker" or r["snapshot_date"] in apply_partial]


def run(args, now=None) -> int:
    now = now or now_et()
    acct = db.load_account_daily_snapshots(start_date=args.since)
    if acct is None:
        _die("account_daily_snapshots could not be read.")
    trades = db.load_trades_or_none()
    if trades is None:
        _die("the journal's trades could not be read; refusing to plan anything.")
    snap = db.load_daily_snapshots_or_none(start_date=args.since)
    if snap is None:
        _die("daily_snapshots could not be read.")

    repairs, refusals, reviews, clean, _closes = plan(acct, trades, snap, now)
    deb_df = db.load_weekly_debriefs(limit=60)
    debriefs = affected_debriefs(
        {_parse_date(r["snapshot_date"]) for r in repairs}, deb_df)

    if args.json:
        print(json.dumps({"repairs": repairs, "refusals": refusals, "reviews": reviews, "clean": clean,
                          "debriefs": [[we.isoformat(), [d.isoformat() for d in ds]]
                                       for we, ds in debriefs]},
                         indent=2, default=str))
    else:
        print_report(repairs, refusals, reviews, clean, debriefs,
                     debriefs_known=deb_df is not None and len(deb_df) > 0)

    if not args.apply:
        print("\nDry run: nothing written. Re-run with --apply to write the "
              "missing_ticker repairs (and --apply-partial DATES for partial_quantity / unreconciled).")
        return 0

    avail = db.account_snapshot_provenance_available()
    if avail is not True:
        _die("provenance columns not confirmed "
             + ("(missing: apply docs/sql/account-snapshot-provenance.sql)"
                if avail is False else "(the probe failed; can't tell)")
             + "; nothing written.")
    chosen = _selected(repairs, args.apply_partial)
    skipped = [r["snapshot_date"] for r in repairs if r not in chosen]
    failed = []
    for r in chosen:
        ok = db.apply_account_snapshot_repair(r["snapshot_date"], r["fields"])
        print(f"{'applied' if ok else 'FAILED '} {r['snapshot_date']} [{r['category']}]")
        if not ok:
            failed.append(r["snapshot_date"])
    if skipped:
        print("Not applied (owner review, pass --apply-partial): "
              + ", ".join(skipped))

    acct2 = db.load_account_daily_snapshots(start_date=args.since)
    if acct2 is None:
        _die("re-read after apply failed; can't confirm idempotence.", code=3)
    repairs2, _r2, _rv2, _c2, _ = plan(acct2, trades, snap, now)
    again = {r["snapshot_date"] for r in repairs2} & {r["snapshot_date"] for r in chosen}
    again -= set(failed)
    if again:
        print(f"WARNING: re-plan still proposes applied dates: {sorted(again)}", file=sys.stderr)
        return 3
    print("Re-plan after apply: no applied row is proposed again (idempotent).")
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default=DEFAULT_SINCE, help="YYYY-MM-DD (default %(default)s)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--apply", action="store_true", help="write the repairs (default: dry run)")
    ap.add_argument("--apply-partial", default="",
                    help="comma-separated dates whose partial_quantity or unreconciled repair to apply (after you review them)")
    args = ap.parse_args(argv)
    if _parse_date(args.since) is None or len(args.since) != 10:
        _die("--since must be YYYY-MM-DD.")
    parts = [p.strip() for p in args.apply_partial.split(",") if p.strip()]
    for p in parts:
        if len(p) != 10 or _parse_date(p) is None:
            _die(f"--apply-partial: {p!r} is not YYYY-MM-DD.")
    args.apply_partial = set(parts)
    for var in ("SUPABASE_URL", "SUPABASE_KEY"):
        if not os.environ.get(var):
            _die(f"{var} is not set in the environment.")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
