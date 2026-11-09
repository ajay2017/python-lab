"""
Owner-named, evidence-gated repair of legacy account rows whose gross_book is
short by a hand-logged trade (bug register N1-LogTime) — PURE.

`account_snapshot_repair.plan_legacy_repairs` deliberately never applies a
"needs review" row (share counts agree with the journal, but `gross_book` is off
the journal book x that day's closes): such a gap can be a trade logged after
the EOD run, or the app re-saving `daily_snapshots` at a different price, and
the data alone can't say which. This module is the evidence-gated step the OWNER
triggers: the owner names the trade id(s) that explain a date's gap, and the
repair is planned only when those trades explain the gap TO THE CENT.

Evidence rule: `delta = sum(+shares if BUY else -shares) x close(ticker, date)`
over the named trades; `new_gross = round(old_gross + delta, 2)`; the repair is
planned only when `new_gross` reconciles with the journal book replayed to the
row's cash cutoff x that day's closes (`_reconciled`, within one cent; the same
tolerance the sibling uses). A partial explanation is refused with how much of
the gap the named trades explain; nothing is ever guessed or rounded into place.

Why owner-named: the module can't tell a late-logged trade from a re-saved
price, so it never picks trades itself. The owner's naming, backed by a VERIFIED
fill time (for the three 2026-09 rows: Robinhood's Filled times, applied by
docs/sql/journal-fix-2026-11-07-fill-times.sql), is the claim. The cent match
proves the arithmetic, not the date: a trade whose journal `traded_at` is itself
wrong (it really filled a later day) passes identically and would overstate net
equity, because that day's cash read never paid for it. Only name trades whose
fill time has been checked against the broker.

Known fail-closed cases (refused by the cent check, not by a named reason): a
FRACTIONAL named trade on a pre-2026-10-12 int-share row (the delta here uses
exact shares), and a split after the row or an oversell on a named ticker.

Same input contracts as `plan_legacy_repairs` (trades_df from
`db.load_trades_or_none`; `snapshot_shares` `{(date, TICKER): int}`; `closes`
`{(TICKER, date): close}`, unadjusted; `now` tz-aware ET). Never raises on bad
rows: an unusable date key, a missing row, a bad id or any failed check is a
refusal that names what failed, and a refused date plans nothing. The only
raises are caller misuse (a naive `now`, a non-DataFrame `trades_df`), as in the
sibling. No DB, no Streamlit, no network, no clock read.
"""

from __future__ import annotations

from numbers import Integral

from .account import _parse_date, account_money_fields
from .account_snapshot_repair import (
    _cash_read_post_close, _cutoff_ok, _f, _gross_gap, _int_or_none, _norm_closes,
    _prep_trades, _reconciled, _replay, _rows, _trades_usable, _ts_utc,
    plan_legacy_repairs,
)


def _row_for(rows: list, d) -> "dict | None":
    for r in rows:
        if _parse_date(r.get("snapshot_date")) == d:
            return r
    return None


def _precheck_reason(row: dict, trades_df, snapshot_shares, closes, now, d) -> tuple:
    """(reason | None, gap). Check b: the row, judged alone by the sibling
    planner, must be a review with a measured unreconciled gap."""
    reps, refs, revs = plan_legacy_repairs([row], trades_df, snapshot_shares, closes, now)
    if reps:
        return (f"the legacy planner already classes this row as a "
                f"{reps[0]['category']} repair (a touched ticker), not a measured gap "
                "that named trades could explain", None)
    if refs:
        return (f"the legacy planner refuses this row: {refs[0].get('reason')}", None)
    if not revs:
        return ("the row is clean: its gross_book already reconciles with the journal "
                "book x that day's closes", None)
    gap = revs[0].get("gap")
    if gap is None:
        return (f"the row's gap can't be measured: {revs[0].get('reason')}", None)
    return None, gap


def _plan_one(row, d, ids, prep, px_by, trades_df, snapshot_shares, closes, now) -> tuple:
    """(repair | None, reason | None) for one named date."""
    ds = d.isoformat()
    # a. provenance
    max_id_row = _int_or_none(row.get("journal_max_trade_id"))
    if max_id_row is not None:
        return None, f"this row already has provenance (journal_max_trade_id {max_id_row})"
    # b. the legacy planner sees exactly one measured, unreconciled gap
    reason, gap = _precheck_reason(row, trades_df, snapshot_shares, closes, now, d)
    if reason is not None:
        return None, reason
    # c. cash read and the stored figures the repair needs
    if not _cash_read_post_close(row, d):
        return None, (f"cash_as_of {row.get('cash_as_of')} is not a same-day post-close "
                      "read, so its cutoff can't be trusted")
    old_gross = _f(row.get("gross_book"))
    if old_gross is None:
        return None, "no stored gross_book to repair"
    cash = _f(row.get("cash_balance"))
    rate = _f(row.get("maintenance_rate"))
    if cash is not None and rate is None:
        return None, "no stored maintenance rate to re-derive the money fields with"
    # d. every named trade
    if not ids:
        return None, "no trade id was named for this date"
    cash_as_of_ts = _ts_utc(row.get("cash_as_of"))
    by_id = {t["id"]: t for t in prep}
    seen: set = set()
    named: list = []
    for raw in ids:
        tid = int(raw) if isinstance(raw, Integral) and not isinstance(raw, bool) else None
        if tid is None:
            return None, f"trade id {raw!r} is not an integer"
        if tid in seen:
            return None, f"trade id {tid} is named more than once"
        seen.add(tid)
        t = by_id.get(tid)
        if t is None:
            return None, f"trade id {tid} is not in the journal"
        if t["kind"] not in ("BUY", "SELL"):
            return None, f"trade id {tid} is a {t['kind']}, not a BUY or SELL"
        if t["et_date"] != d:
            return None, (f"trade id {tid} is dated {t['et_date'].isoformat()} (ET), "
                          f"not {ds}")
        if not _cutoff_ok(t, d, cash_as_of_ts):
            return None, (f"trade id {tid} is after this row's cash_as_of, so the cash "
                          "read did not include it")
        px = px_by.get((t["ticker"], d))
        if px is None:
            return None, f"trade id {tid}: no close for {t['ticker']} on {ds}"
        signed = t["shares"] if t["kind"] == "BUY" else -t["shares"]
        named.append({"id": tid, "ticker": t["ticker"], "action": t["kind"],
                      "shares": t["shares"], "close": px,
                      "value": round(signed * px, 2), "_signed_value": signed * px})
    # e. the evidence: the named trades must close the gap to the cent
    delta = sum(n["_signed_value"] for n in named)
    new_gross = round(old_gross + delta, 2)
    holdings = _replay(prep, d, cash_as_of_ts)[0]
    residual = _gross_gap(new_gross, holdings, d, px_by)
    if not _reconciled(residual):
        return None, f"the named trades explain {delta:+,.2f} of the {gap:+,.2f} gap"
    for n in named:
        n.pop("_signed_value")

    money = (account_money_fields(new_gross, cash, rate) if cash is not None else
             {"cash_balance": None, "net_equity": None, "leverage": None,
              "cushion": None, "call_distance_pct": None})
    book = {tk: {"shares": round(sh, 6), "close": px_by[(tk, d)]}
            for tk, sh in holdings.items()}
    orig = _f(row.get("original_gross_book"))
    fields = {
        "gross_book":           new_gross,
        "net_equity":           money["net_equity"],
        "leverage":             money["leverage"],
        "cushion":              money["cushion"],
        "call_distance_pct":    money["call_distance_pct"],
        "journal_max_trade_id": max(t["id"] for t in prep),
        "original_gross_book":  orig if orig is not None else old_gross,
        "repaired_at":          now.isoformat(),
        "book_positions":       book,
    }
    return {
        "snapshot_date": ds,
        "category": "explained_gap",
        "touched": [],
        "explained_by": named,
        "old_gross_book": old_gross, "new_gross_book": new_gross,
        "old_net_equity": _f(row.get("net_equity")), "new_net_equity": money["net_equity"],
        "old_leverage": _f(row.get("leverage")), "new_leverage": money["leverage"],
        "gap_before": gap,
        "residual": residual,
        "book_positions": book,
        "fields": fields,
    }, None


def plan_explained_gap_repairs(acct_rows, trades_df, snapshot_shares, closes, now, explained):
    """Plan the repair of legacy account rows from OWNER-NAMED trades. Pure: the
    caller does the writes. Returns `(repairs, refusals)`.

    `explained` is `{ISO date str: [int trade ids]}`: for each date the owner
    names the trades that explain the row's `gross_book` gap. A date is repaired
    only when ALL of these hold (checked in this order; the first failure is the
    refusal, and a refused date plans nothing):
      a. the row exists and has a NULL `journal_max_trade_id`;
      b. the sibling `plan_legacy_repairs`, run on that row alone, returns it as
         exactly one review with a measured gap (no touched ticker): not clean,
         not a repair, not a refusal, not a gap it couldn't measure;
      c. `cash_as_of` is absent or a same-ET-day post-close read, `gross_book`
         is stored, and a stored maintenance rate exists when cash does;
      d. every named id is unique, in the journal, a BUY or SELL dated this ET
         date, at or before the row's cash cutoff, with a close for its ticker;
      e. the named trades explain the gap to the cent: `delta = sum(signed
         shares x close)`, `new_gross = round(old_gross + delta, 2)`, and the
         residual against the journal book replayed to the cutoff reconciles.

    Each repair mirrors `plan_legacy_repairs`' shape: {snapshot_date, category
    "explained_gap", touched [], old_/new_ gross_book, net_equity, leverage,
    gap_before, residual, book_positions, `fields` (the columns
    `db.apply_account_snapshot_repair` writes), plus `explained_by`: [{id,
    ticker, action, shares, close, value}] with `value` signed (+ BUY, - SELL)}.
    Provenance: `journal_max_trade_id` = the highest id in `trades_df` now (a
    second run refuses "already has provenance"), `original_gross_book` = the
    old gross, `repaired_at` = now. cash_balance / cash_as_of / maintenance_rate
    are never in `fields`. Refusals are `{"snapshot_date", "reason"}`; unreadable
    trades are ONE refusal with a None date.
    """
    if getattr(now, "tzinfo", None) is None:
        raise ValueError("plan_explained_gap_repairs: `now` must be tz-aware (ET)")
    if trades_df is None or not _trades_usable(trades_df):
        return [], [{"snapshot_date": None,
                     "reason": "the journal's trades could not be read, so no row can be checked"}]
    prep = _prep_trades(trades_df)
    px_by = _norm_closes(closes)
    rows = _rows(acct_rows)
    repairs: list = []
    refusals: list = []

    for key in sorted(explained or {}, key=str):
        d = _parse_date(key)
        if d is None:
            refusals.append({"snapshot_date": str(key),
                             "reason": "not a valid YYYY-MM-DD date"})
            continue
        row = _row_for(rows, d)
        if row is None:
            refusals.append({"snapshot_date": d.isoformat(),
                             "reason": "no account_daily_snapshots row for this date"})
            continue
        ids = explained[key]
        ids = list(ids) if isinstance(ids, (list, tuple, set)) else []
        repair, reason = _plan_one(row, d, ids, prep, px_by,
                                   trades_df, snapshot_shares, closes, now)
        if reason is not None:
            refusals.append({"snapshot_date": d.isoformat(), "reason": reason})
        else:
            repairs.append(repair)
    return repairs, refusals
