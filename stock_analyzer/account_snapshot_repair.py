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
from .constants import ALERT_EOD_HOUR_ET, BROKER_DRIFT_SHARE_TOL
from .market_time import ET

# Replay-internal epsilon for "a running total is flat / dropped to nothing".
# Mirrors db.recalculate_from_trades / capital_vs_margin.holdings_by_date's own
# 1e-6 float-noise guard; it is not a policy threshold.
_FLAT_EPS = 1e-6

# A stored gross_book is rounded to 2 dp, so it reconciles with a re-summed book
# to within one cent. Rounding plumbing like _FLAT_EPS, not a policy threshold.
_GROSS_CENT_TOL = 0.01

# Refusal `kind`s that won't fix themselves (a split, an oversell, a book the
# late trades don't explain, ...). `no_close` and `journal_unreadable` are
# transient and retry nightly. Alarm-dedup plumbing (account_snapshot_autorepair),
# not a policy value.
STRUCTURAL_REFUSAL_KINDS = frozenset({"no_book", "split", "oversell", "unexplained", "no_rate"})


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


def stale_details(acct_df, trades_df):
    """`[{"snapshot_date": ISO str, "tickers": sorted list}]` for every row whose
    stored book no longer matches the journal as of the row's own cutoff,
    oldest first; `tickers` are the stale tickers.

    A row is stale when, for any ticker touched by a late trade, the replayed
    holdings (trades at or before the row's cutoff) differ from
    `book_positions[t]["shares"]` by more than `BROKER_DRIFT_SHARE_TOL`.
    Legacy rows are never flagged. Returns None when `trades_df` is None or
    unusable (the journal couldn't be read — "couldn't check", never "clean")."""
    if trades_df is None or not _trades_usable(trades_df):
        return None
    prep = _prep_trades(trades_df)
    out = []
    for row in _rows(acct_df):
        a = _analyse(row, prep)
        if a and a["stale_tickers"]:
            out.append({"snapshot_date": a["ctx"]["date"].isoformat(),
                        "tickers": sorted(a["stale_tickers"])})
    return sorted(out, key=lambda d: d["snapshot_date"])


def stale_rows(acct_df, trades_df):
    """ISO `snapshot_date`s of stale rows, oldest first (the dates of
    `stale_details`; one rule). None when the journal couldn't be read."""
    det = stale_details(acct_df, trades_df)
    return None if det is None else [d["snapshot_date"] for d in det]


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
      refusals  `[{"snapshot_date", "reason", "kind"}]` (kind: journal_unreadable,
                no_book, split, oversell, unexplained, no_close, no_rate) for every stale row left
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
        return [], [{"snapshot_date": None, "kind": "journal_unreadable",
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

        def refuse(reason, kind, _ds=ds):
            refusals.append({"snapshot_date": _ds, "reason": reason, "kind": kind})

        if a["no_book"]:
            refuse("no readable book_positions, so the row can't be repaired", "no_book")
            continue

        # Per touched ticker: signed late shares, and the refusal checks.
        reason = None
        kind = None
        delta: dict = {}
        for t in a["late"]:
            if t["kind"] == "SPLIT":
                reason = f"{t['ticker']}: a split is among the late trades"
                kind = "split"
                break
            delta[t["ticker"]] = delta.get(t["ticker"], 0.0) + (
                t["shares"] if t["kind"] == "BUY" else -t["shares"])
        if reason is None:
            for t in prep:
                if t["kind"] == "SPLIT" and t["ticker"] in a["touched"] and t["et_date"] > d:
                    reason = (f"{t['ticker']}: a split on {t['et_date'].isoformat()} falls "
                              "after this row, so its stored closes are not comparable")
                    kind = "split"
                    break
        if reason is None:
            bad = sorted(set(a["touched"]) & a["oversold"])
            if bad:
                reason = f"{', '.join(bad)}: the journal sells more than it holds (oversell)"
                kind = "oversell"
        if reason is None:
            for tk in a["stale_tickers"]:
                stored = ctx["book"].get(tk, {}).get("shares", 0.0)
                if abs(stored + delta.get(tk, 0.0) - a["replay"].get(tk, 0.0)) > BROKER_DRIFT_SHARE_TOL:
                    reason = (f"{tk}: stored {stored:g} shares plus the late trades "
                              f"({delta.get(tk, 0.0):+g}) do not equal the journal's "
                              f"{a['replay'].get(tk, 0.0):g}")
                    kind = "unexplained"
                    break
        if reason is not None:
            refuse(reason, kind)
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
            refuse(reason, "no_close")
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
            refuse(reason, "no_close")
            continue

        gross = round(sum(v["shares"] * v["close"] for v in new_book.values()), 2)
        cash = _f(row.get("cash_balance"))
        rate = _f(row.get("maintenance_rate"))
        if cash is not None and rate is None:
            refuse("no stored maintenance rate to re-derive the money fields with", "no_rate")
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


# ── C3: backfill of LEGACY rows (NULL provenance) ────────────────────────────

def _norm_snapshot_shares(snapshot_shares) -> dict:
    """{(date, TICKER): int shares} -> {date: {TICKER: int}}; bad keys / values
    are skipped."""
    out: dict = {}
    for k, v in (snapshot_shares or {}).items():
        try:
            d, tk = k
        except (TypeError, ValueError):
            continue
        d = _parse_date(d)
        sh = _int_or_none(v)
        tk = str(tk).upper().strip()
        if d is None or sh is None or not tk:
            continue
        out.setdefault(d, {})[tk] = sh
    return out


def _legacy_touched(prep: list, d: "_date", row: dict, snap: dict) -> tuple:
    """(R_c holdings {ticker: exact shares}, oversold tickers, sorted touched
    tickers) for one legacy row. R_c is the replay to the row's cutoff
    (`cash_as_of`, else the ET date). Touched = R_c's int shares differ from
    the snapshot's."""
    holdings, oversold = _replay(prep, d, _ts_utc(row.get("cash_as_of")))
    touched = sorted(tk for tk in set(snap) | set(holdings)
                     if int(holdings.get(tk, 0.0)) != snap.get(tk, 0))
    return holdings, oversold, touched


def _gross_gap(old_gross: float, holdings: dict, d: "_date", px_by: dict):
    """old_gross minus sum(shares x close over `holdings`), as the smaller-
    magnitude of the EXACT-share and the int-truncated-share sums (gross_book
    was int-based before 2026-10-12 and exact after). None when any held
    ticker has no close for `d` (the row can't be checked, never guessed)."""
    exact = trunc = 0.0
    for tk, sh in holdings.items():
        px = px_by.get((tk, d))
        if px is None:
            return None
        exact += sh * px
        trunc += int(sh) * px
    gaps = [round(old_gross - exact, 2), round(old_gross - trunc, 2)]
    return min(gaps, key=abs)


def _reconciled(gap) -> bool:
    return gap is not None and abs(gap) <= _GROSS_CENT_TOL + 1e-9


def _cash_read_post_close(row: dict, d: "_date") -> bool:
    """True when `cash_as_of` is absent (the ET-date cutoff applies) or is a
    same-ET-day read at or after `ALERT_EOD_HOUR_ET`: the timing half of
    `performance_review.valid_account_snapshot_row`, the only cash read that
    includes the day's afternoon fills."""
    ts = _ts_utc(row.get("cash_as_of"))
    if ts is None:
        return row.get("cash_as_of") is None
    et = ts.tz_convert(ET)
    return et.date() == d and et.hour >= ALERT_EOD_HOUR_ET


def legacy_close_needs(acct_rows, trades_df, snapshot_shares) -> set:
    """{(TICKER, date)} whose close `plan_legacy_repairs` may need: every
    ticker held in R_c or touched, for every legacy row that has snapshot
    rows. Lets the caller fetch only those closes. Empty when the trades
    can't be read."""
    if trades_df is None or not _trades_usable(trades_df):
        return set()
    prep = _prep_trades(trades_df)
    snap_by = _norm_snapshot_shares(snapshot_shares)
    need: set = set()
    for row in _rows(acct_rows):
        d = _parse_date(row.get("snapshot_date"))
        snap = snap_by.get(d) if d is not None else None
        if not snap or _int_or_none(row.get("journal_max_trade_id")) is not None:
            continue
        holdings, _o, touched = _legacy_touched(prep, d, row, snap)
        for tk in set(holdings) | set(touched):
            need.add((tk, d))
    return need


def close_needs(acct_rows, trades_df) -> set:
    """{(TICKER, date)} whose close `plan_repairs` may need that the row's own
    book can't supply: for every non-legacy STALE row with a readable book,
    each stale ticker the journal still holds (replayed shares > `_FLAT_EPS`)
    that is absent from the book or has a None close there, plus any book
    position with a None close. Lets the caller fetch only those closes. Empty
    when the trades can't be read."""
    if trades_df is None or not _trades_usable(trades_df):
        return set()
    prep = _prep_trades(trades_df)
    need: set = set()
    for row in _rows(acct_rows):
        a = _analyse(row, prep)
        if a is None or not a["stale_tickers"] or a["no_book"]:
            continue
        d, book = a["ctx"]["date"], a["ctx"]["book"]
        for tk in a["stale_tickers"]:
            if a["replay"].get(tk, 0.0) > _FLAT_EPS and book.get(tk, {}).get("close") is None:
                need.add((tk, d))
        for tk, v in book.items():
            if v["close"] is None:
                need.add((tk, d))
    return need


def plan_legacy_repairs(acct_rows, trades_df, snapshot_shares, closes, now):
    """Plan the repair of LEGACY rows (NULL `journal_max_trade_id`) from
    evidence. Pure: the caller does the writes. Returns
    `(repairs, refusals, reviews)`.

    `snapshot_shares` is `{(date, TICKER): int shares}` from `daily_snapshots`
    (the int-truncated convention); `closes` is `{(TICKER, date): close}`
    (unadjusted, caller contract (b)); `trades_df` must satisfy caller
    contract (a). `now` is a tz-aware ET datetime.

    R_c is the journal replayed to the row's cutoff (`cash_as_of`, else the
    end of the ET date) with the module's own `_replay`; R_day is every trade
    with ET date <= the row's date (no time cutoff). A ticker is TOUCHED when
    R_c's int shares differ from that date's `daily_snapshots` shares (absent
    or replayed to 0 included).

    Reconciling the stored gross: the cron writes the snapshot rows and
    `gross_book` from the same Price (`headless_alert_engine.compute_eod`),
    but `daily_snapshots` is last-writer-wins (the app rewrites today's rows
    after the close), so matching share counts do NOT prove the gross is
    right. `gap = old gross - sum(R_c shares x close)` is reconciled when
    within `_GROSS_CENT_TOL` of EITHER the exact-share or the int-share sum.
      - A row with no touched ticker and a reconciled gap is clean.
      - A row with no touched ticker and a gap (or a missing close, so it
        can't be checked) goes to `reviews` (never auto-applied).
      - A touched row is repaired by the DELTA method: old gross + sum over
        touched tickers of (R_c int shares - snapshot shares) x close. A sum
        over the whole book would silently rewrite any unrelated drift.
        Legacy rows have no `book_positions` to trust.
      - The repair's `residual` = new gross - sum(R_c x close). Only when it
        reconciles is the category `missing_ticker` (every touched ticker is
        a full add / exit) or `partial_quantity`, and `book_positions`
        (exact R_c shares to 6 dp x that date's close, every position) is
        written. Otherwise the category is `unreconciled`, with no
        `book_positions`: the script applies it only on explicit request.

    Refusals (reason, nothing written): no `daily_snapshots` rows for the date
    (`no_snapshot_rows`); a touched row whose `cash_as_of` isn't a same-ET-day
    post-close read; a touched ticker where R_day equals the snapshot but R_c
    doesn't (the EOD book already held trades logged after the cash read: not
    late trades, and a removal would be wrong); a split dated after the row; an
    oversell; a missing close for a touched ticker (never carried forward); a
    missing stored gross_book; cash present with no maintenance rate.
    Unreadable trades are ONE refusal with a None date.

    Each repair is {snapshot_date, category, touched[{ticker, snapshot_shares,
    replay_shares, close, value_delta}], old_/new_ gross_book, net_equity,
    leverage, gap_before, residual, drift, recomputed_untouched,
    book_positions, `fields`: the columns `db.apply_account_snapshot_repair`
    writes}. Provenance: `journal_max_trade_id` = the highest id in
    `trades_df` now (a second run skips the row), `original_gross_book` = the
    old gross, `repaired_at` = now. cash_balance / cash_as_of /
    maintenance_rate are never in `fields`.
    """
    if getattr(now, "tzinfo", None) is None:
        raise ValueError("plan_legacy_repairs: `now` must be tz-aware (ET)")
    if trades_df is None or not _trades_usable(trades_df):
        return [], [{"snapshot_date": None,
                     "reason": "the journal's trades could not be read, so no row can be checked"}], []
    prep = _prep_trades(trades_df)
    max_id = max((t["id"] for t in prep), default=0)
    px_by = _norm_closes(closes)
    snap_by = _norm_snapshot_shares(snapshot_shares)
    repairs: list = []
    refusals: list = []
    reviews: list = []

    for row in sorted(_rows(acct_rows), key=lambda r: str(r.get("snapshot_date"))):
        d = _parse_date(row.get("snapshot_date"))
        if d is None or _int_or_none(row.get("journal_max_trade_id")) is not None:
            continue                       # unusable date, or already has provenance
        ds = d.isoformat()

        def refuse(reason, _ds=ds, **extra):
            refusals.append({"snapshot_date": _ds, "reason": reason, **extra})

        snap = snap_by.get(d)
        if not snap:
            refuse("no daily_snapshots rows for this date, so there is nothing to compare "
                   "the journal with", no_snapshot_rows=True)
            continue

        holdings, oversold, touched = _legacy_touched(prep, d, row, snap)
        old_gross = _f(row.get("gross_book"))
        gap_before = (_gross_gap(old_gross, holdings, d, px_by)
                      if old_gross is not None else None)

        if not touched:
            if old_gross is None:
                reviews.append({"snapshot_date": ds, "gap": None, "old_gross_book": None,
                                "reason": "no stored gross_book to check"})
            elif gap_before is None:
                reviews.append({"snapshot_date": ds, "gap": None, "old_gross_book": old_gross,
                                "reason": "share counts agree, but a held ticker has no close "
                                          "for this date, so the gross can't be checked"})
            elif not _reconciled(gap_before):
                reviews.append({"snapshot_date": ds, "gap": gap_before,
                                "old_gross_book": old_gross,
                                "reason": f"share counts agree with the journal, but gross_book "
                                          f"is off the journal book by {gap_before:+,.2f}"})
            continue

        reason = None
        if not _cash_read_post_close(row, d):
            reason = (f"cash_as_of {row.get('cash_as_of')} is not a same-day post-close "
                      "read, so its cutoff can't be trusted")
        if reason is None:
            day_holdings, _od = _replay(prep, d, None)
            for tk in touched:
                c_sh = int(holdings.get(tk, 0.0))
                if int(day_holdings.get(tk, 0.0)) == snap.get(tk, 0) != c_sh:
                    reason = (f"{tk}: the difference is trades after this row's cash read "
                              "that the EOD book already held, not late trades")
                    break
        if reason is None:
            for t in prep:
                if t["kind"] == "SPLIT" and t["ticker"] in touched and t["et_date"] > d:
                    reason = (f"{t['ticker']}: a split on {t['et_date'].isoformat()} falls after "
                              "this row, so closes for this date are not comparable")
                    break
        if reason is None:
            bad = sorted(set(touched) & oversold)
            if bad:
                reason = f"{', '.join(bad)}: the journal sells more than it holds (oversell)"
        if reason is None:
            missing = [tk for tk in touched if (tk, d) not in px_by]
            if missing:
                reason = f"{', '.join(missing)}: no close for {ds}"
        if reason is None and old_gross is None:
            reason = "no stored gross_book to repair"
        cash = _f(row.get("cash_balance"))
        rate = _f(row.get("maintenance_rate"))
        if reason is None and cash is not None and rate is None:
            reason = "no stored maintenance rate to re-derive the money fields with"
        if reason is not None:
            refuse(reason)
            continue

        touched_out = []
        delta = 0.0
        for tk in touched:
            old_sh, new_sh, px = snap.get(tk, 0), int(holdings.get(tk, 0.0)), px_by[(tk, d)]
            delta += (new_sh - old_sh) * px
            touched_out.append({"ticker": tk, "snapshot_shares": old_sh,
                                "replay_shares": new_sh, "close": px,
                                "value_delta": round((new_sh - old_sh) * px, 2)})
        new_gross = round(old_gross + delta, 2)
        residual = _gross_gap(new_gross, holdings, d, px_by)
        reconciled = _reconciled(residual)
        if not reconciled:
            category = "unreconciled"
        elif all(x["snapshot_shares"] == 0 or x["replay_shares"] == 0 for x in touched_out):
            category = "missing_ticker"
        else:
            category = "partial_quantity"

        book = ({tk: {"shares": round(sh, 6), "close": px_by[(tk, d)]}
                 for tk, sh in holdings.items()} if reconciled else None)

        untouched_px = [(sh, px_by.get((tk, d))) for tk, sh in snap.items() if tk not in touched]
        recomputed_untouched = (None if any(p is None for _, p in untouched_px)
                                else round(sum(sh * p for sh, p in untouched_px), 2))
        old_book_px = [(sh, px_by.get((tk, d))) for tk, sh in snap.items()]
        drift = (None if any(p is None for _, p in old_book_px)
                 else round(old_gross - sum(sh * p for sh, p in old_book_px), 2))

        money = (account_money_fields(new_gross, cash, rate) if cash is not None else
                 {"cash_balance": None, "net_equity": None, "leverage": None,
                  "cushion": None, "call_distance_pct": None})
        orig = _f(row.get("original_gross_book"))
        fields = {
            "gross_book":           new_gross,
            "net_equity":           money["net_equity"],
            "leverage":             money["leverage"],
            "cushion":              money["cushion"],
            "call_distance_pct":    money["call_distance_pct"],
            "journal_max_trade_id": max_id,
            "original_gross_book":  orig if orig is not None else old_gross,
            "repaired_at":          now.isoformat(),
        }
        if book is not None:
            fields["book_positions"] = book
        repairs.append({
            "snapshot_date": ds,
            "category": category,
            "touched": touched_out,
            "old_gross_book": old_gross, "new_gross_book": new_gross,
            "old_net_equity": _f(row.get("net_equity")), "new_net_equity": money["net_equity"],
            "old_leverage": _f(row.get("leverage")), "new_leverage": money["leverage"],
            "gap_before": gap_before,
            "residual": residual,
            "drift": drift,
            "recomputed_untouched": recomputed_untouched,
            "book_positions": book,
            "fields": fields,
        })
    return repairs, refusals, reviews
