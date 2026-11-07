"""
Automatic repair of stale account_daily_snapshots rows (bug register N1, C5).

The I/O orchestrator around the PURE planner in `account_snapshot_repair`:
the EOD lane and the Sunday debrief pre-step call `run_auto_repair`, which
loads the recent account rows, the journal's trades and the stored per-ticker
closes, plans with `plan_repairs` and writes each repair through
`db.apply_account_snapshot_repair`. Design: docs/plans/account-snapshot-repair.md.

C7: after the account repairs it also reconciles the per-ticker `daily_snapshots`
rows of every REPAIRED account row in the window (`repaired_at` set, or repaired
in this run) with that row's `book_positions`, via the pure
`snapshot_row_repair.plan_snapshot_rows` and `db.repair_daily_snapshot_rows`.
A row without `repaired_at` is left alone (a normal day can differ legitimately:
a holding that failed to price, int truncation).

Contracts (from the C1/C2 review):
  (a) trades come from `db_mod.load_trades_or_none` (normalised `traded_at`);
      an unreadable journal is an error, never "nothing stale".
  (b) closes are the stored `daily_snapshots.close_price` first, then Yahoo's
      UNadjusted close for the exact date; never carried forward.

`run_auto_repair` NEVER raises: a problem comes back in the result (`ok`
False / `failed` / `refusals`), and `failure_lines` turns it into heartbeat
lines. It never touches TODAY's row (the EOD writes that one in the same run)
and never blocks the day's account write or the debrief.

`db_mod` is injected so the flow is testable with a fake.
"""

from __future__ import annotations

from datetime import timedelta

from . import data as data_mod
from .account import _parse_date
from .account_snapshot_repair import STRUCTURAL_REFUSAL_KINDS, close_needs, plan_repairs
from .snapshot_row_repair import plan_snapshot_rows
from .snapshot_row_repair import summarize as summarize_snapshot_rows

# Yahoo history window for a close missing from daily_snapshots. Request-size
# plumbing, not a policy value. It must cover ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS
# (6mo is about 180 days): raise it if the lookback ever exceeds ~150 days
# (tests/test_account_snapshot_autorepair.py pins this).
_CLOSE_FETCH_PERIOD = "6mo"

# Refusal lines shown in a heartbeat detail before "... +N more".
_MAX_REFUSAL_LINES = 5

# Snapshot-row refusals on a REPAIRED account row that become a visible
# heartbeat line (the repaired row has no usable book, or is dated on a
# non-trading day, so its per-ticker rows aren't reconciled: either is a defect
# worth seeing). `today_or_future` can't occur here (rows are < today).
_SNAPSHOT_ROW_ALARM_KINDS = ("no_book", "empty_book", "non_trading_day")


def _records(df) -> list:
    return [] if df is None else df.to_dict("records")


def snapshot_shares_and_closes(snap_df) -> tuple:
    """({(date, TICKER): int shares}, {(TICKER, date): stored close}) from a
    daily_snapshots frame."""
    shares: dict = {}
    closes: dict = {}
    for r in _records(snap_df):
        d = _parse_date(r.get("snapshot_date"))
        tk = str(r.get("ticker") or "").upper().strip()
        if d is None or not tk:
            continue
        try:
            sh = int(float(r.get("shares")))
        except (TypeError, ValueError):
            continue
        shares[(d, tk)] = sh
        try:
            px = float(r.get("close_price"))
        except (TypeError, ValueError):
            px = None
        if px is not None and px == px and px > 0:
            closes[(tk, d)] = px
    return shares, closes


def fetch_missing_closes(needs, have: dict) -> dict:
    """Exact-date unadjusted closes from Yahoo for the (TICKER, date) pairs in
    `needs` not already in `have`. Never raises; a miss is simply absent."""
    out: dict = {}
    by_ticker: dict = {}
    for tk, d in needs:
        if (tk, d) not in have:
            by_ticker.setdefault(tk, set()).add(d)
    for tk, dates in sorted(by_ticker.items()):
        df = data_mod.fetch_unadjusted_history(tk, _CLOSE_FETCH_PERIOD)
        if df is None:
            continue
        try:
            by_day = {}
            for ix, v in df["Close"].items():
                d = ix.date() if hasattr(ix, "date") else _parse_date(ix)
                by_day[d] = float(v)
        except Exception:  # noqa: BLE001
            continue
        for d in dates:
            px = by_day.get(d)
            if px is not None and px == px and px > 0:
                out[(tk, d)] = px
    return out


def _result(since, *, ok, checked=0, applied=None, failed=None, refusals=None, error=None,
            details=None, snapshot_rows_applied=None, snapshot_rows_failed=None,
            snapshot_rows_refused=None, snapshot_rows_summary=None) -> dict:
    return {"ok": ok, "since": since, "checked": checked, "applied": applied or [],
            "failed": failed or [], "refusals": refusals or [], "error": error,
            "details": details or [],
            "snapshot_rows_applied": snapshot_rows_applied or [],
            "snapshot_rows_failed": snapshot_rows_failed or [],
            "snapshot_rows_refused": snapshot_rows_refused or [],
            "snapshot_rows_summary": snapshot_rows_summary}


def _is_set(v) -> bool:
    """True for a real value (not None / NaN / NaT / blank)."""
    if v is None:
        return False
    try:
        if v != v:
            return False
    except (TypeError, ValueError):
        pass
    return bool(str(v).strip())


def reconcile_snapshot_rows(db_mod, rows, applied_repairs, snap, today) -> tuple:
    """Per-ticker `daily_snapshots` reconciliation for the REPAIRED rows in
    `rows` (the window's account rows, all dated before `today`): those with
    `repaired_at` set plus every repair in `applied_repairs` (which supply the
    NEW `book_positions`, not the stale loaded one). `snap` is the already
    loaded daily_snapshots frame. Writes through
    `db_mod.repair_daily_snapshot_rows` only when a date has something to do.
    Returns (applied[ISO], failed[ISO], refused[{snapshot_date, kind, reason}],
    summary line). Never raises: a date whose reconcile throws is `failed`."""
    book_for: dict = {}
    for r in rows:
        d = _parse_date(r.get("snapshot_date"))
        if d is not None and _is_set(r.get("repaired_at")):
            book_for[d] = r.get("book_positions")
    for r in applied_repairs:
        d = _parse_date(r.get("snapshot_date"))
        if d is not None:
            book_for[d] = r.get("book_positions")
    by_date: dict = {}
    for r in _records(snap):
        d = _parse_date(r.get("snapshot_date"))
        if d is not None:
            by_date.setdefault(d, []).append(r)
    applied, failed, refused, plans = [], [], [], []
    for d in sorted(book_for):
        try:
            plan = plan_snapshot_rows(d, book_for[d], by_date.get(d, []), today)
            if plan["refusal"]:
                refused.append({"snapshot_date": plan["date"], **plan["refusal"]})
                continue
            if not (plan["upserts"] or plan["deletes"]):
                continue
            plans.append(plan)
            ok = db_mod.repair_daily_snapshot_rows(plan["date"], plan["upserts"], plan["deletes"])
        except Exception:  # noqa: BLE001 -- one date never blocks the rest
            ok = None
        (applied if ok else failed).append(d.isoformat())
    return applied, failed, refused, summarize_snapshot_rows(plans)


def run_auto_repair(db_mod, today, now, *, lookback_days, fetch_closes=None) -> dict:
    """Repair stale account rows dated in [today - lookback_days, today).

    `today` is the ET date, `now` a tz-aware ET datetime (stamped on
    `repaired_at`). `fetch_closes(needs, have) -> {(TICKER, date): close}`
    defaults to `fetch_missing_closes` and is called only when a close is
    needed. Returns {ok, since, checked, applied[ISO], failed[ISO],
    refusals[{snapshot_date, reason}], details[{date, old_gross, new_gross,
    max_late_id}] (applied repairs only), snapshot_rows_applied[ISO],
    snapshot_rows_failed[ISO], snapshot_rows_refused[{snapshot_date, kind,
    reason}], snapshot_rows_summary (one line), error}. Never raises."""
    since = None
    try:
        start = today - timedelta(days=lookback_days)
        since = start.isoformat()
        acct = db_mod.load_account_daily_snapshots(start_date=since)
        if acct is None:
            return _result(since, ok=False, error="account_daily_snapshots unreadable")
        rows = []
        for r in _records(acct):
            d = _parse_date(r.get("snapshot_date"))
            if d is not None and start <= d < today:    # never today's row
                rows.append(r)
        trades = db_mod.load_trades_or_none()
        if trades is None:
            return _result(since, ok=False, checked=len(rows), error="trades unreadable")
        snap = db_mod.load_daily_snapshots_or_none(start_date=since)
        if snap is None:
            return _result(since, ok=False, checked=len(rows), error="daily_snapshots unreadable")

        _shares, stored = snapshot_shares_and_closes(snap)
        needs = close_needs(rows, trades)
        closes = dict(stored)
        if needs:
            closes.update((fetch_closes or fetch_missing_closes)(needs, stored))
        repairs, refusals = plan_repairs(rows, trades, closes, now)

        old_gross = {}
        for r in rows:
            d = _parse_date(r.get("snapshot_date"))
            if d is not None:
                old_gross[d.isoformat()] = r.get("gross_book")
        applied, failed, details = [], [], []
        for r in repairs:
            ok = db_mod.apply_account_snapshot_repair(r["snapshot_date"], r)
            (applied if ok else failed).append(r["snapshot_date"])
            if ok:
                details.append({"date": r["snapshot_date"],
                                "old_gross": old_gross.get(r["snapshot_date"]),
                                "new_gross": r.get("gross_book"),
                                "max_late_id": r.get("journal_max_trade_id")})
        applied_set = set(applied)
        sr_applied, sr_failed, sr_refused, sr_summary = reconcile_snapshot_rows(
            db_mod, rows, [r for r in repairs if r["snapshot_date"] in applied_set],
            snap, today)
        return _result(since, ok=True, checked=len(rows), applied=applied,
                       failed=failed, refusals=refusals, details=details,
                       snapshot_rows_applied=sr_applied, snapshot_rows_failed=sr_failed,
                       snapshot_rows_refused=sr_refused, snapshot_rows_summary=sr_summary)
    except Exception as e:  # noqa: BLE001 -- never blocks the lane
        return _result(since, ok=False, error=f"{type(e).__name__}: {str(e)[:120]}")


def summary_line(result: dict) -> str:
    """One log line for the lane."""
    if not result.get("ok"):
        return f"account_snapshot_repair: FAILED — {result.get('error')}"
    applied = result.get("applied", [])
    by_date = {x.get("date"): x for x in result.get("details", [])}

    def _one(d):
        x = by_date.get(d)
        if not x:
            return d
        og, ng = x.get("old_gross"), x.get("new_gross")
        og = "n/a" if og is None else f"{float(og):,.2f}"
        ng = "n/a" if ng is None else f"{float(ng):,.2f}"
        return f"{d} (gross {og} -> {ng}, trades to #{x.get('max_late_id')})"
    return (f"account_snapshot_repair: checked {result.get('checked', 0)} row(s) since "
            f"{result.get('since')} · applied {', '.join(_one(d) for d in applied) if applied else 'none'}"
            f" · refused {len(result.get('refusals', []))}"
            + (f" · write failed {', '.join(result['failed'])}" if result.get("failed") else "")
            + (f" · snapshot rows applied {', '.join(result['snapshot_rows_applied'])}"
               f" ({result.get('snapshot_rows_summary')})"
               if result.get("snapshot_rows_applied") else "")
            + (f" · snapshot rows write failed {', '.join(result['snapshot_rows_failed'])}"
               if result.get("snapshot_rows_failed") else ""))


def _refusal_key(x: dict) -> str:
    return f"{x.get('snapshot_date')}|{x.get('kind')}"


def structural_refusal_keys(result: dict) -> list:
    """Sorted `"{snapshot_date}|{kind}"` for the run's STRUCTURAL refusals
    (kinds in `STRUCTURAL_REFUSAL_KINDS`: they won't fix themselves). [] when
    the run was not ok."""
    if not result.get("ok"):
        return []
    return sorted(_refusal_key(x) for x in result.get("refusals", [])
                  if x.get("kind") in STRUCTURAL_REFUSAL_KINDS)


def _is_suppressed(x: dict, suppress_keys) -> bool:
    return x.get("kind") in STRUCTURAL_REFUSAL_KINDS and _refusal_key(x) in suppress_keys


def failure_lines(result: dict, *, suppress_keys=frozenset()) -> list:
    """Heartbeat failure lines: [] when the run was ok with no failed write and
    no refusal. A refused stale row means a known-wrong history row stays.

    A STRUCTURAL refusal whose key (`structural_refusal_keys`) is in
    `suppress_keys` was already alarmed on an earlier run, so it is omitted
    (`standing_lines` logs it instead). Errors, failed writes and transient
    refusals (`no_close`, `journal_unreadable`) are never suppressed."""
    pre = "account_snapshot_repair:"
    if not result.get("ok"):
        return [f"{pre} {result.get('error')}"]
    out = []
    if result.get("failed"):
        out.append(f"{pre} write failed for {', '.join(result['failed'])}")
    refusals = [x for x in result.get("refusals", []) if not _is_suppressed(x, suppress_keys)]
    for x in refusals[:_MAX_REFUSAL_LINES]:
        out.append(f"{pre} refused {x.get('snapshot_date') or '(all rows)'}: {x.get('reason')}")
    if len(refusals) > _MAX_REFUSAL_LINES:
        out.append(f"{pre} … +{len(refusals) - _MAX_REFUSAL_LINES} more")
    # C7: per-ticker daily_snapshots rows of repaired account rows.
    if result.get("snapshot_rows_failed"):
        out.append(f"{pre} snapshot-row write failed for "
                   f"{', '.join(result['snapshot_rows_failed'])}")
    sr = [x for x in result.get("snapshot_rows_refused", [])
          if x.get("kind") in _SNAPSHOT_ROW_ALARM_KINDS]
    for x in sr[:_MAX_REFUSAL_LINES]:
        out.append(f"{pre} snapshot rows refused {x.get('snapshot_date')}: {x.get('reason')}")
    if len(sr) > _MAX_REFUSAL_LINES:
        out.append(f"{pre} … +{len(sr) - _MAX_REFUSAL_LINES} more snapshot-row refusals")
    return out


def standing_lines(result: dict, suppress_keys) -> list:
    """Log lines (not heartbeat failures) for the structural refusals that
    `failure_lines` left out because they were alarmed on an earlier run."""
    if not result.get("ok"):
        return []
    return [f"account_snapshot_repair: refused {x.get('snapshot_date') or '(all rows)'} still "
            f"standing ({x.get('reason')}) — alarmed when first seen; Reports and the Debrief "
            "leave that day out"
            for x in result.get("refusals", []) if _is_suppressed(x, suppress_keys)]
