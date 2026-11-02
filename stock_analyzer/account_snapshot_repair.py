"""
Account-snapshot staleness detection and repair (bug register N1) — PURE.

`account_daily_snapshots.net_equity = gross_book + cash_balance`. The cash
comes from the broker lane (`cash_as_of`, ~21:15 UTC since 2026-11-01) and already includes
every fill, while `gross_book` is the JOURNAL book at EOD. A trade that reaches
the journal after the row was written (a Friday buy confirmed on Saturday)
leaves the row describing two different books. Design:
docs/plans/account-snapshot-repair.md.

This module decides, from data the caller injects:
  - which journal trades are LATE for a row (`late_trades`),
  - which rows are STALE (`stale_rows`; None when the journal couldn't be read),
  - what a repaired row becomes, or why it must be REFUSED (`plan_repairs`).

No DB, no Streamlit, no network, no clock read (`now` is injected, tz-aware
ET). Closes are an injected mapping `{(TICKER, date): close}` — the caller
fetches them; a close is never carried forward from another day.

Provenance columns read from a row (all optional, NULL = legacy):
  journal_max_trade_id  highest `trades.id` the EOD run had seen.
  book_positions        {TICKER: {"shares": n, "close": px}} behind gross_book.
  original_gross_book   gross_book before the FIRST repair (set once).
A row with a NULL `journal_max_trade_id` (or no usable date) is a legacy row:
it is never flagged, because it records no point to be "late" against. A row
WITH a max id but a NULL / unreadable `book_positions` is flagged as soon as one
late trade exists, and `plan_repairs` refuses it (nothing to repair from).

Caller contracts:
  (a) `trades_df` must come from `db.load_trades_or_none` (it runs
      `trade_time.normalize_traded_at`, which gives imported fills their 16:00
      ET anchor). A non-DataFrame raises TypeError; a frame without the `id`
      or `traded_at` column is "couldn't check" (`stale_rows` -> None).
  (b) Injected `closes` must be UNADJUSTED, or the stored
      `daily_snapshots.close_price`: yfinance `auto_adjust` dividend-adjusts
      past closes, which would misprice the repaired book.
  (c) The C4 EOD writer must build `gross_book` as the same sum of
      shares x close over the row's `book_positions`, and must NOT send
      `original_gross_book` / `repaired_at` on the EOD upsert.

"Late" is judged against the row's own `cash_as_of` timestamp, never the
calendar day: a fill at 18:00 ET logged the same evening was NOT in the cash
read at 17:00, so it must not be added to that day. When `cash_as_of` is NULL
the fallback is the ET calendar date of `traded_at` <= `snapshot_date`.

The share replay lives here rather than reusing
`capital_vs_margin.holdings_by_date`: that function buckets by calendar date
(this one needs a TIMESTAMP cutoff per row) and clamps an oversell silently
(this one must report it so the repair can refuse).
"""

from __future__ import annotations

import json
import math
from datetime import date as _date

import pandas as pd

from .account import _parse_date, account_money_fields
from .constants import BROKER_DRIFT_SHARE_TOL
from .market_time import ET

# Replay-internal epsilon for "a running total is flat / dropped to nothing".
# Mirrors db.recalculate_from_trades / capital_vs_margin.holdings_by_date's own
# 1e-6 float-noise guard; it is not a policy threshold.
_FLAT_EPS = 1e-6


# ── small coercion helpers ───────────────────────────────────────────────────

def _f(v):
    """float or None (None / NaN / non-numeric / non-finite -> None)."""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _int_or_none(v):
    x = _f(v)
    return int(x) if x is not None else None


def _ts_utc(v):
    """A tz-aware UTC Timestamp, or None when missing / unparseable."""
    if v is None:
        return None
    try:
        t = pd.to_datetime(v, utc=True, errors="coerce", format="ISO8601")
    except Exception:
        return None
    if t is None or pd.isna(t):
        return None
    return t


def _rows(acct) -> list:
    """Account rows as a list of plain dicts (accepts a DataFrame, a list of
    dicts, or None)."""
    if acct is None:
        return []
    if isinstance(acct, pd.DataFrame):
        return acct.to_dict("records")
    return [dict(r) for r in acct]


def _iso(v):
    """JSON-safe pass-through for a timestamp-ish value (None for missing)."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v.isoformat() if hasattr(v, "isoformat") else v


def _book(row) -> "dict | None":
    """{TICKER: {"shares": float, "close": float|None}} from a row's
    `book_positions`, or None when absent / unreadable (no book to compare:
    flagged once a late trade exists, never repaired)."""
    raw = row.get("book_positions")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    for k, v in raw.items():
        t = str(k).upper().strip()
        if not t or not isinstance(v, dict):
            continue
        sh = _f(v.get("shares"))
        if sh is None:
            continue
        out[t] = {"shares": sh, "close": _f(v.get("close"))}
    return out


# ── trade preparation and replay ─────────────────────────────────────────────

def _prep_trades(trades_df) -> list:
    """Journal rows as dicts sorted by (traded_at, id). Rows that can't be
    placed in time (unparseable traded_at), have no integer id, or fail the
    same input guard `recalculate_from_trades` applies (empty ticker,
    non-numeric / non-positive shares or price) are skipped."""
    if trades_df is None:
        return []
    if not isinstance(trades_df, pd.DataFrame):
        raise TypeError("trades_df must be a DataFrame (db.load_trades_or_none)")
    if trades_df.empty:
        return []
    out: list = []
    for ix, r in trades_df.iterrows():
        ticker = str(r.get("ticker", "") or "").upper().strip()
        action = str(r.get("action", "") or "").upper()
        sh, px = _f(r.get("shares")), _f(r.get("price"))
        ts = _ts_utc(r.get("traded_at"))
        tid = _int_or_none(r.get("id"))
        if not ticker or ts is None or tid is None:
            continue
        if sh is None or px is None or sh <= 0 or px <= 0:
            continue
        if "SPLIT" in action:
            kind = "SPLIT"
        elif "BUY" in action:
            kind = "BUY"
        elif "SELL" in action:
            kind = "SELL"
        else:
            continue
        out.append({
            "_ix": ix, "id": tid, "ticker": ticker, "kind": kind, "shares": sh,
            "ts": ts, "et_date": ts.tz_convert(ET).date(),
        })
    out.sort(key=lambda t: (t["ts"], t["id"]))
    return out


def _cutoff_ok(t: dict, snap_date: "_date", cash_as_of_ts) -> bool:
    """True when trade `t` is at or before the row's cutoff: `traded_at <=
    cash_as_of`, else (cash_as_of NULL) ET date of `traded_at` <= snapshot_date."""
    if cash_as_of_ts is not None:
        return t["ts"] <= cash_as_of_ts
    return t["et_date"] <= snap_date


def _replay(prep: list, snap_date: "_date", cash_as_of_ts) -> tuple:
    """Forward-replay the trades inside the row's cutoff.

    Returns (holdings {ticker: shares}, oversold {ticker}). Mirrors
    `db.recalculate_from_trades`: BUY adds, SELL subtracts, SPLIT overwrites
    with the post-split total, a flat ticker is dropped. An oversell clamps to
    0 (as the journal's own replay does) but is REPORTED so a repair can refuse.
    """
    holdings: dict = {}
    oversold: set = set()
    for t in prep:
        if not _cutoff_ok(t, snap_date, cash_as_of_ts):
            continue
        k, sh, tk = t["kind"], t["shares"], t["ticker"]
        if k == "SPLIT":
            holdings[tk] = sh
        elif k == "BUY":
            holdings[tk] = holdings.get(tk, 0.0) + sh
        else:
            new = holdings.get(tk, 0.0) - sh
            if new < -_FLAT_EPS:
                oversold.add(tk)
                new = 0.0
            if new <= _FLAT_EPS:
                holdings.pop(tk, None)
            else:
                holdings[tk] = new
    return holdings, oversold


def _row_ctx(row: dict) -> "dict | None":
    """The provenance a row is judged on, or None for a legacy row (NULL
    journal_max_trade_id or no usable date). `book` is None when
    book_positions is NULL / unreadable: late trades can still be found, but
    nothing can be compared or repaired."""
    max_id = _int_or_none(row.get("journal_max_trade_id"))
    d = _parse_date(row.get("snapshot_date"))
    if max_id is None or d is None:
        return None
    return {
        "date": d, "max_id": max_id, "book": _book(row),
        "cash_as_of_ts": _ts_utc(row.get("cash_as_of")),
    }


def _late_prepped(prep: list, ctx: dict) -> list:
    return [t for t in prep
            if t["id"] > ctx["max_id"] and _cutoff_ok(t, ctx["date"], ctx["cash_as_of_ts"])]


# ── public API ───────────────────────────────────────────────────────────────

def _trades_usable(trades_df) -> bool:
    """A DataFrame carrying the columns lateness depends on. Anything else that
    is a DataFrame is "couldn't check"; a non-DataFrame is misuse (TypeError)."""
    if not isinstance(trades_df, pd.DataFrame):
        raise TypeError("trades_df must be a DataFrame (db.load_trades_or_none)")
    return "id" in trades_df.columns and "traded_at" in trades_df.columns


def late_trades(row, trades_df):
    """Trades that reached the journal AFTER `row` was written but belong to
    its book: `id > row.journal_max_trade_id` AND `traded_at <= cash_as_of`
    (cash_as_of NULL: ET date of traded_at <= snapshot_date).

    A legacy row (NULL `journal_max_trade_id`) returns an empty frame. A row
    with a max id but no readable `book_positions` still returns its late
    trades. Returns a subset of `trades_df` (original columns and index),
    oldest first. trades_df None returns an empty frame; a non-DataFrame
    raises TypeError; a frame without `id` / `traded_at` raises ValueError."""
    if trades_df is None:
        return pd.DataFrame()
    if not _trades_usable(trades_df):
        raise ValueError("trades_df lacks the `id` / `traded_at` columns")
    ctx = _row_ctx(dict(row))
    if ctx is None:
        return trades_df.iloc[0:0]
    late = _late_prepped(_prep_trades(trades_df), ctx)
    if not late:
        return trades_df.iloc[0:0]
    return trades_df.loc[[t["_ix"] for t in late]]


def _analyse(row: dict, prep: list) -> "dict | None":
    """Per-row analysis shared by `stale_rows` and `plan_repairs`; None for a
    legacy row."""
    ctx = _row_ctx(row)
    if ctx is None:
        return None
    late = _late_prepped(prep, ctx)
    touched = sorted({t["ticker"] for t in late})
    holdings, oversold = _replay(prep, ctx["date"], ctx["cash_as_of_ts"])
    no_book = ctx["book"] is None
    if no_book:
        stale_tickers = list(touched)        # can't compare: any late trade flags
    else:
        stale_tickers = [
            tk for tk in touched
            if abs(holdings.get(tk, 0.0) - ctx["book"].get(tk, {}).get("shares", 0.0))
            > BROKER_DRIFT_SHARE_TOL
        ]
    return {"ctx": ctx, "late": late, "touched": touched, "replay": holdings,
            "oversold": oversold, "stale_tickers": stale_tickers, "no_book": no_book}


def stale_rows(acct_df, trades_df):
    """ISO `snapshot_date`s of rows whose stored book no longer matches the
    journal as of the row's own cutoff, oldest first.

    A row is stale when, for any ticker touched by a late trade, the replayed
    holdings (trades at or before the row's cutoff) differ from
    `book_positions[t]["shares"]` by more than `BROKER_DRIFT_SHARE_TOL`.
    Legacy rows are never flagged. Returns None when `trades_df` is None
    (the journal couldn't be read — "couldn't check", never "clean")."""
    if trades_df is None or not _trades_usable(trades_df):
        return None
    prep = _prep_trades(trades_df)
    out = []
    for row in _rows(acct_df):
        a = _analyse(row, prep)
        if a and a["stale_tickers"]:
            out.append(a["ctx"]["date"].isoformat())
    return sorted(out)


def _norm_closes(closes) -> dict:
    out: dict = {}
    for k, v in (closes or {}).items():
        try:
            tk, d = k
        except (TypeError, ValueError):
            continue
        d = _parse_date(d)
        px = _f(v)
        if d is None or px is None or px <= 0:
            continue
        out[(str(tk).upper().strip(), d)] = px
    return out


def plan_repairs(acct_rows, trades_df, closes, now):
    """Plan the repair of every stale row. Pure: the caller does the writes.

    `closes` is `{(TICKER, date): close}`, injected. `now` is a tz-aware ET
    datetime stamped on `repaired_at`. Returns `(repairs, refusals)`:
      repairs   one full replacement row per repaired date (every column
                `save_account_daily_snapshot` upserts), oldest first;
      refusals  `[{"snapshot_date", "reason"}]` for every stale row left
                alone. An unreadable journal is ONE refusal with a None date.

    A stale row is repaired only when, for every touched ticker, the stored
    shares plus the signed late trades equal the replayed shares (within
    `BROKER_DRIFT_SHARE_TOL`), or the ticker already matches the replay (the
    trade was in the book already: a race with the EOD read). It REFUSES on a
    SPLIT among the touched ticker's late trades or dated after the row, an
    oversell, a missing close (the row's own stored close first, then
    `closes`; never carried forward), a book that disagrees with the journal
    in a way the late trades don't explain, or a missing stored maintenance
    rate when cash is known.

    The repaired row keeps `cash_balance` and `cash_as_of` as they were; its
    money fields are re-derived from the new gross book with the row's own
    cash and STORED maintenance rate (cash None -> money fields None).
    `journal_max_trade_id` becomes the highest late id used, so a second run
    plans nothing; `original_gross_book` is set once.
    """
    if getattr(now, "tzinfo", None) is None:
        raise ValueError("plan_repairs: `now` must be tz-aware (ET)")
    if trades_df is None or not _trades_usable(trades_df):
        return [], [{"snapshot_date": None,
                     "reason": "the journal's trades could not be read, so no row can be checked"}]
    prep = _prep_trades(trades_df)
    px_by = _norm_closes(closes)
    repairs: list = []
    refusals: list = []

    for row in sorted(_rows(acct_rows), key=lambda r: str(r.get("snapshot_date"))):
        a = _analyse(row, prep)
        if a is None or not a["stale_tickers"]:
            continue
        ctx = a["ctx"]
        d = ctx["date"]
        ds = d.isoformat()

        def refuse(reason, _ds=ds):
            refusals.append({"snapshot_date": _ds, "reason": reason})

        if a["no_book"]:
            refuse("no readable book_positions, so the row can't be repaired")
            continue

        # Per touched ticker: signed late shares, and the refusal checks.
        reason = None
        delta: dict = {}
        for t in a["late"]:
            if t["kind"] == "SPLIT":
                reason = f"{t['ticker']}: a split is among the late trades"
                break
            delta[t["ticker"]] = delta.get(t["ticker"], 0.0) + (
                t["shares"] if t["kind"] == "BUY" else -t["shares"])
        if reason is None:
            for t in prep:
                if t["kind"] == "SPLIT" and t["ticker"] in a["touched"] and t["et_date"] > d:
                    reason = (f"{t['ticker']}: a split on {t['et_date'].isoformat()} falls "
                              "after this row, so its stored closes are not comparable")
                    break
        if reason is None:
            bad = sorted(set(a["touched"]) & a["oversold"])
            if bad:
                reason = f"{', '.join(bad)}: the journal sells more than it holds (oversell)"
        if reason is None:
            for tk in a["stale_tickers"]:
                stored = ctx["book"].get(tk, {}).get("shares", 0.0)
                if abs(stored + delta.get(tk, 0.0) - a["replay"].get(tk, 0.0)) > BROKER_DRIFT_SHARE_TOL:
                    reason = (f"{tk}: stored {stored:g} shares plus the late trades "
                              f"({delta.get(tk, 0.0):+g}) do not equal the journal's "
                              f"{a['replay'].get(tk, 0.0):g}")
                    break
        if reason is not None:
            refuse(reason)
            continue

        # New book: stale tickers take the journal's shares; everything else
        # (incl. already-reflected touched tickers) stays as stored.
        new_book: dict = {}
        for tk, v in ctx["book"].items():
            if tk not in a["stale_tickers"]:
                new_book[tk] = {"shares": v["shares"], "close": v["close"]}
        for tk in a["stale_tickers"]:
            sh = round(a["replay"].get(tk, 0.0), 6)
            if sh <= _FLAT_EPS:
                continue                      # late full exit: leaves the book
            close = ctx["book"].get(tk, {}).get("close")
            if close is None:
                close = px_by.get((tk, d))
            if close is None:
                reason = f"{tk}: no close for {ds}"
                break
            new_book[tk] = {"shares": sh, "close": close}
        if reason is not None:
            refuse(reason)
            continue
        # Untouched positions priced from a stored close; a stored position
        # without one can't be valued (never carried forward).
        for tk, v in new_book.items():
            if v["close"] is None:
                close = px_by.get((tk, d))
                if close is None:
                    reason = f"{tk}: no close for {ds}"
                    break
                v["close"] = close
        if reason is not None:
            refuse(reason)
            continue

        gross = round(sum(v["shares"] * v["close"] for v in new_book.values()), 2)
        cash = _f(row.get("cash_balance"))
        rate = _f(row.get("maintenance_rate"))
        if cash is not None and rate is None:
            refuse("no stored maintenance rate to re-derive the money fields with")
            continue
        money = (account_money_fields(gross, cash, rate) if cash is not None else
                 {"cash_balance": None, "net_equity": None, "leverage": None,
                  "cushion": None, "call_distance_pct": None})
        old_gross = _f(row.get("gross_book"))
        orig = _f(row.get("original_gross_book"))
        repairs.append({
            "snapshot_date":       ds,
            "gross_book":          gross,
            "cash_balance":        money["cash_balance"],
            "net_equity":          money["net_equity"],
            "leverage":            money["leverage"],
            "cushion":             money["cushion"],
            "call_distance_pct":   money["call_distance_pct"],
            "maintenance_rate":    rate,
            "cash_as_of":          _iso(row.get("cash_as_of")),
            "book_positions":      new_book,
            "journal_max_trade_id": max(t["id"] for t in a["late"]),
            "original_gross_book": orig if orig is not None else old_gross,
            "repaired_at":         now.isoformat(),
        })
    return repairs, refusals
