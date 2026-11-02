"""
Fail-closed reader mask for account_daily_snapshots rows (bug register N1, C6).

PURE: no streamlit, no db import, no network, no clock read.

A row whose stored book no longer matches the journal (a trade logged after the
row was written; `account_snapshot_repair.stale_details`) describes two
different books at once, and the nightly auto-repair (C5) may REFUSE it. Every
reader (account return, reconciliation, leverage, debrief) would still consume
it. `exclude_stale_rows` returns a COPY of the frame with the row's money
fields set to None. Nulling `net_equity` makes
`performance_review.valid_account_snapshot_row` reject the row, which is the
invalid-row path every reader already handles (d0 withheld, d1 searches back,
reconcile skips it, leverage / drift sections skip a None). `excluded_line`
names what was left out, so the mask is never silent.

Legacy rows (NULL `journal_max_trade_id`) are never flagged, by design. When
the journal can't be read, every row WITH provenance is masked (it can't be
verified); legacy rows are untouched.
"""
from __future__ import annotations

import pandas as pd

from .account import _parse_date
from .account_snapshot_repair import stale_details
from .performance_review import prior_trading_day

# The columns derived from gross_book + cash: masked together.
MONEY_FIELDS = ("gross_book", "net_equity", "leverage", "cushion", "call_distance_pct")

_UNCHECKABLE = "it couldn't be checked against the trade journal (the journal couldn't be read)"


def _mask(df: pd.DataFrame, row_mask) -> pd.DataFrame:
    """`df` (already a copy) with MONEY_FIELDS set to None (object dtype, so a
    later `pd.DataFrame` / `_opt` reads missing, never "+nan") where `row_mask`."""
    if not bool(row_mask.any()):
        return df
    for c in MONEY_FIELDS:
        if c in df.columns:
            df[c] = df[c].astype(object)
            df.loc[row_mask, c] = None
    return df


def exclude_stale_rows(acct_df, trades_df):
    """`(masked_copy, excluded)`.

    `excluded` is `[{"snapshot_date": ISO str, "reason": str}]`, oldest first.
    `acct_df` None -> `(None, [])`. The input is never mutated (callers pass a
    frame cached in session_state) and the function is idempotent."""
    if acct_df is None:
        return None, []
    out = acct_df.copy()
    if out.empty or "journal_max_trade_id" not in out.columns \
            or "snapshot_date" not in out.columns:
        return out, []
    dates = out["snapshot_date"].apply(_parse_date)
    has_prov = pd.to_numeric(out["journal_max_trade_id"], errors="coerce").notna() & dates.notna()

    try:
        details = stale_details(out, trades_df)
    except Exception:  # noqa: BLE001 -- a misused trades input is "couldn't check"
        details = None

    if details is None:
        excluded = sorted(
            ({"snapshot_date": d.isoformat(), "reason": _UNCHECKABLE}
             for d in dates[has_prov]),
            key=lambda x: x["snapshot_date"])
        return _mask(out, has_prov), excluded

    by_date = {d["snapshot_date"]: d["tickers"] for d in details}
    row_mask = dates.apply(lambda d: d is not None and d.isoformat() in by_date)
    excluded = [{
        "snapshot_date": ds,
        "reason": (f"its holdings don't match the trade journal ({', '.join(tk)} changed "
                   "by a trade logged after that day's record)"),
    } for ds, tk in sorted(by_date.items())]
    return _mask(out, row_mask), excluded


def excluded_line(excluded, period_start, period_end):
    """One sentence naming the excluded rows that matter to a period, or None.

    A row matters when its date is in [prior trading day of `period_start`
    (the d0 session), `period_end`]. Plain text (renders in st.caption, HTML
    email and plain text): no markdown bold, no dollar sign."""
    lo, hi = prior_trading_day(period_start), period_end
    parts = []
    for x in excluded or []:
        d = _parse_date(x.get("snapshot_date"))
        if d is not None and lo <= d <= hi:
            parts.append(f"{d.isoformat()} — {x.get('reason')}")
    if not parts:
        return None
    return "Left out of this period's account figures: " + "; ".join(parts) + "."
