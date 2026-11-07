"""
Per-ticker `daily_snapshots` reconciliation for repaired account rows
(bug register N1, chunk C7; docs/plans/account-snapshot-repair.md step 7).

The N1 repairs (C3 backfill, C5 nightly auto-repair) correct an
`account_daily_snapshots` row's `gross_book` and `book_positions`
({TICKER: {"shares": float, "close": float}}), but the per-ticker
`daily_snapshots` rows for that date (PK (snapshot_date, ticker)) kept the
pre-repair book. This module is the PURE planner that says what those rows
should read: a late buy is upserted, a short row is topped up, a late full exit
is deleted. It does no I/O (callers write through
`db.repair_daily_snapshot_rows`).

Expected rows follow the EOD writer (`headless_alert_engine.compute_eod`
snapshot_rows): shares are TRUNCATED to an int on purpose (it is the
Today's-P&L baseline) and a position with 0 whole shares or a non-positive close
has no row (`db.save_daily_snapshot` drops sh <= 0 / px <= 0).

Safety: TODAY's row (and any later date) is never planned, the EOD lane writes
it. A weekend / NYSE-holiday date is refused too (`non_trading_day`): an account
row on such a day is a known bad-row class, so no per-ticker writes follow it.
A book that can't be read is a refusal, never "delete everything". So is a book
that implies NO rows while rows exist (`empty_book`: `{}`, or every entry under
one whole share / with a non-positive close): a late exit of the whole book is
not a case worth an irreversible delete-all. A `{}` book with no stored rows is
a clean no-op, and a book with SOME sub-share entries still deletes just those
tickers' rows.
"""

from __future__ import annotations

import json
import math

from .account import _parse_date
from .constants import NYSE_HOLIDAYS

# A stored close within this of the book's close is the same price (cents).
_CLOSE_TOL = 0.005
_SHARE_EPS = 1e-9
# Dates named in `summarize` before "+N more".
_MAX_SUMMARY_DATES = 8


def _plan(date_iso, upserts=None, deletes=None, previous=None, refusal=None) -> dict:
    return {"date": date_iso, "upserts": upserts or [], "deletes": deletes or [],
            "previous": previous or {}, "refusal": refusal}


def _refuse(date_iso, kind, reason) -> dict:
    return _plan(date_iso, refusal={"kind": kind, "reason": reason})


def _num(v):
    """float(v) when finite, else None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _expected_rows(book_positions):
    """(expected, n_entries, None) from a book, or (None, 0, reason), where
    `expected` is {TICKER: (int shares, close)}. A position with 0 whole shares
    or a non-positive close has no row."""
    raw = book_positions
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:  # noqa: BLE001
            return None, 0, "book_positions is not valid JSON"
    if not isinstance(raw, dict):
        return None, 0, "no readable book_positions"
    out: dict = {}
    for k, v in raw.items():
        tk = str(k).upper().strip()
        if not tk or not isinstance(v, dict):
            return None, 0, f"book_positions entry {k!r} is unreadable"
        sh, px = _num(v.get("shares")), _num(v.get("close"))
        if sh is None or px is None:
            return None, 0, f"book_positions entry {tk} has no usable shares / close"
        if tk in out:
            return None, 0, f"book_positions names {tk} twice"
        n = int(sh)                      # TRUNCATED, like the EOD writer
        out[tk] = (n, px) if (n > 0 and px > 0) else None
    return {t: v for t, v in out.items() if v is not None}, len(out), None


def plan_snapshot_rows(snapshot_date, book_positions, snapshot_rows, today) -> dict:
    """What `daily_snapshots` should hold for `snapshot_date` given the repaired
    account row's `book_positions`.

    A weekend / NYSE-holiday date is refused as `non_trading_day` (the same
    weekday + `constants.NYSE_HOLIDAYS` test as `data.is_trading_day`, without
    importing the provider stack). `snapshot_rows`: that date's stored rows (dicts with ticker / shares /
    close_price). `today`: the ET date. Returns {"date": ISO, "upserts":
    [{ticker, shares, close_price}], "deletes": [ticker], "previous": {ticker:
    stored shares or None} (for the log line only), "refusal": None | {kind,
    reason}}, sorted by ticker. A refusal carries no actions. Never raises.

    Upsert when a ticker has no row, its stored shares differ from the book's
    whole shares, or its stored close is more than a cent-ish (0.005) off the
    book's close. Delete a stored ticker with no expected row. Idempotent."""
    d = _parse_date(snapshot_date)
    if d is None:
        return _refuse(None, "bad_date", f"unreadable snapshot date {snapshot_date!r}")
    ds = d.isoformat()
    t = _parse_date(today)
    if t is None or d >= t:
        return _refuse(ds, "today_or_future",
                       "today's (or a later) row is written by the EOD lane, never repaired here")
    if d.weekday() >= 5 or ds in NYSE_HOLIDAYS:
        return _refuse(ds, "non_trading_day",
                       "an account row dated on a weekend / NYSE holiday is a bad row; "
                       "no per-ticker rows are planned for it")

    stored: dict = {}
    for r in (snapshot_rows or []):
        if not isinstance(r, dict):
            continue
        tk = str(r.get("ticker") or "").upper().strip()
        if tk:
            stored[tk] = (_num(r.get("shares")), _num(r.get("close_price")))

    expected, _n_entries, why = _expected_rows(book_positions)
    if expected is None:
        return _refuse(ds, "no_book", why)
    if not expected and stored:
        return _refuse(ds, "empty_book",
                       "the repaired book implies no per-ticker rows (empty, or every position "
                       "under one whole share) but daily_snapshots has rows: not deleting them all")

    upserts, previous = [], {}
    for tk in sorted(expected):
        sh, px = expected[tk]
        cur = stored.get(tk)
        if (cur is None or cur[0] is None or abs(cur[0] - sh) > _SHARE_EPS
                or cur[1] is None or abs(cur[1] - px) > _CLOSE_TOL):
            upserts.append({"ticker": tk, "shares": sh, "close_price": px})
            previous[tk] = None if cur is None else cur[0]
    deletes = sorted(tk for tk in stored if tk not in expected)
    for tk in deletes:
        previous[tk] = stored[tk][0]
    return _plan(ds, upserts, deletes, previous)


def apply_plan_to_rows(snapshot_rows, plan) -> list:
    """The `daily_snapshots` rows (dicts with ticker / shares / close_price) as
    they would read after `plan` is written, sorted by ticker. Pure; used to
    project a plan's effect and to prove idempotency. A refused plan changes
    nothing."""
    out: dict = {}
    for r in (snapshot_rows or []):
        tk = str(r.get("ticker") or "").upper().strip()
        if tk:
            out[tk] = {"ticker": tk, "shares": r.get("shares"),
                       "close_price": r.get("close_price")}
    if not plan.get("refusal"):
        for tk in plan.get("deletes", []):
            out.pop(tk, None)
        for u in plan.get("upserts", []):
            out[u["ticker"]] = dict(u)
    return [out[tk] for tk in sorted(out)]


def _shares_txt(v) -> str:
    return "?" if v is None else f"{v:g}"


def summarize(plans) -> str:
    """One log line for the plans that have something to do, e.g.
    `snapshot rows: 2026-11-05 +CRWD 3, CSCO 13→15, DASH 7→10; 2026-10-28 -XYZ`.
    A new ticker reads `+TICKER n`, a changed one `TICKER old→new`, a delete
    `-TICKER`. Capped at 8 dates, then `+N more`."""
    live = [p for p in (plans or []) if p.get("upserts") or p.get("deletes")]
    if not live:
        return "snapshot rows: no change"
    parts = []
    for p in live[:_MAX_SUMMARY_DATES]:
        prev = p.get("previous", {})
        bits = []
        for u in p.get("upserts", []):
            old = prev.get(u["ticker"])
            bits.append(f"+{u['ticker']} {_shares_txt(u['shares'])}" if old is None
                        else f"{u['ticker']} {_shares_txt(old)}→{_shares_txt(u['shares'])}")
        bits += [f"-{tk}" for tk in p.get("deletes", [])]
        parts.append(f"{p.get('date')} " + ", ".join(bits))
    extra = len(live) - _MAX_SUMMARY_DATES
    return "snapshot rows: " + "; ".join(parts) + (f" +{extra} more" if extra > 0 else "")
