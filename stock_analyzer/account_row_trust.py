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

C6b: a masked row reaches some readers as a null, which would read as "no data".
`leverage_gap_text`, `pre_coverage_left_out_note`, `left_out_reading` and
`records_left_out_note` decide the wording that says "left out" instead, so those
surfaces stay render-only.
"""
from __future__ import annotations

import pandas as pd

from .account import _parse_date
from .account_snapshot_repair import stale_details
from .performance_review import prior_trading_day

# The columns derived from gross_book + cash: masked together.
MONEY_FIELDS = ("gross_book", "net_equity", "leverage", "cushion", "call_distance_pct")

# Fail-closed reasons: an unreadable journal and a check that raised are different
# problems (the first clears when the DB read recovers, the second is a bug).
_JOURNAL_UNREADABLE = "the trade journal couldn't be read, so this day couldn't be checked"
_CHECK_ERRORED = "the check failed with an error, so this day couldn't be checked"

_NO_REASON = "the reason wasn't recorded"

# Presentation cap on the date list of the "left out" line (display only).
_MAX_DATES_SHOWN = 6


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

    uncheckable = _JOURNAL_UNREADABLE     # the reason whenever `details` is None
    try:
        details = stale_details(out, trades_df)   # None = journal unreadable / unusable
    except Exception:  # noqa: BLE001 -- a misused trades input is "couldn't check"
        details = None
        uncheckable = _CHECK_ERRORED

    if details is None:
        excluded = sorted(
            ({"snapshot_date": d.isoformat(), "reason": uncheckable}
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


def _dates_text(dates) -> str:
    """ISO dates, comma-joined; past `_MAX_DATES_SHOWN` the rest read "and N more"."""
    iso = [d.isoformat() for d in dates]
    if len(iso) <= _MAX_DATES_SHOWN:
        return ", ".join(iso)
    return ", ".join(iso[:_MAX_DATES_SHOWN]) + f" and {len(iso) - _MAX_DATES_SHOWN} more"


def excluded_dates_in(excluded, lo, hi) -> list:
    """Sorted unique `date`s of the excluded rows inside [lo, hi] (inclusive)."""
    out = set()
    for x in excluded or []:
        d = _parse_date(x.get("snapshot_date"))
        if d is not None and lo <= d <= hi:
            out.add(d)
    return sorted(out)


def excluded_line(excluded, period_start, period_end):
    """One sentence naming the excluded rows that matter to a period, or None.

    A row matters when its date is in [prior trading day of `period_start`
    (the d0 session), `period_end`]. Dates sharing one reason are collapsed
    ("d1, d2 and N more — reason": a fail-closed run would otherwise repeat the
    same long reason per day), groups in date order. Plain text (renders in
    st.caption, HTML email and plain text): no markdown bold, no dollar sign."""
    lo, hi = prior_trading_day(period_start), period_end
    groups: dict = {}
    for x in sorted(excluded or [], key=lambda r: str(r.get("snapshot_date"))):
        d = _parse_date(x.get("snapshot_date"))
        if d is not None and lo <= d <= hi:
            groups.setdefault(x.get("reason"), []).append(d)
    if not groups:
        return None
    parts = [f"{_dates_text(ds)} — {reason or _NO_REASON}" for reason, ds in groups.items()]
    return "Left out of this period's account figures: " + "; ".join(parts) + "."


# -- wording for the sites that would otherwise read a masked row as missing (C6b) -----

def _why_left_out(n: int) -> str:
    return ("its holdings didn't match the trade journal, or it couldn't be checked"
            if n == 1 else
            "their holdings didn't match the trade journal, or they couldn't be checked")


def records_left_out_note(dates, *, see_note: bool = True):
    """"The account record for D was left out (why; see the note above)." or None.
    `see_note=False` drops the pointer for surfaces that carry no note above
    (the debrief reason text, which the narrative reads)."""
    ds = sorted(set(dates or []))
    if not ds:
        return None
    one = len(ds) == 1
    ptr = "; see the note above" if see_note else ""
    return (f"The account {'record for' if one else 'records for'} {_dates_text(ds)} "
            f"{'was' if one else 'were'} left out ({_why_left_out(len(ds))}{ptr}).")


def leverage_gap_text(lev, excluded, period_start, period_end, *, see_note: bool = True):
    """Caption for Section 3's null-leverage rows, or None when there are none.

    `lev` is `period_attribution.leverage_section`'s dict. A null-leverage row
    that `exclude_stale_rows` masked is "left out", a different thing from a
    day that genuinely has no leverage recorded, so the two are counted apart.
    The window is `leverage_section`'s own ([period_start, period_end], no d0)."""
    if not isinstance(lev, dict):
        return None
    n_null = lev.get("n_null_rows") or 0
    n_call = lev.get("n_null_call_rows") or 0
    if not (n_null or n_call):
        return None
    left = min(n_null, len(excluded_dates_in(excluded, period_start, period_end)))
    no_lev = n_null - left
    sentences = []
    if left:
        ptr = "; see the note above" if see_note else ""
        sentences.append(f"{left} day(s) left out ({_why_left_out(left)}{ptr}).")
    segs = []
    if no_lev:
        segs.append(f"{no_lev} day(s) had no leverage recorded")
    if n_call:
        segs.append(f"{n_call} {'' if no_lev else 'day(s) '}had no margin-call distance")
    if segs:
        sentences.append(" and ".join(segs) + " — left out of the figures above.")
    return " ".join(sentences) or None


def pre_coverage_left_out_note(recon, excluded, *, see_note: bool = True):
    """Extra sentence for a `pre_coverage` reconciliation when masked rows sit
    where its missing endpoint is looked for, else None. It says only that
    those records were left out, never that masking is the sole cause (a row
    can also fail the after-close cash check). `no_end_record_yet` (no record
    existed) and `no_sessions` are never about masking."""
    if not isinstance(recon, dict) or recon.get("status") != "pre_coverage":
        return None
    d0, d1 = _parse_date(recon.get("d0")), _parse_date(recon.get("d1"))
    if d0 is None:
        return None
    reason = recon.get("pre_coverage_reason")
    if reason == "no_start_record":
        dates = excluded_dates_in(excluded, d0, d0)
    elif reason == "no_valid_end_record" and d1 is not None:
        dates = [d for d in excluded_dates_in(excluded, d0, d1) if d > d0]
    else:
        dates = []
    return records_left_out_note(dates, see_note=see_note)


def period_left_out_note(recon, excluded, *, see_note: bool = True):
    """`records_left_out_note` for every excluded record in the reconciliation's
    window [d0, period_end] (the span an account return needs both ends from), or
    None. For a surface that can't tell which endpoint failed."""
    if not isinstance(recon, dict):
        return None
    d0, end = _parse_date(recon.get("d0")), _parse_date(recon.get("period_end"))
    if d0 is None or end is None:
        return None
    return records_left_out_note(excluded_dates_in(excluded, d0, end), see_note=see_note)


def leverage_empty_text(lev) -> str:
    """Section 3's empty-state sentence, shared by the Reports screen and the
    markdown export. When null-leverage rows exist the section is empty because
    their readings were unusable (masked rows included), NOT because nothing was
    recorded; the old "no snapshots" text is only for a window with no rows."""
    n_null = (lev.get("n_null_rows") or 0) if isinstance(lev, dict) else 0
    if n_null > 0:
        return "No usable leverage readings in this period."
    return "No account snapshots recorded in this period."


def start_left_out_suffix(start_day, excluded) -> str:
    """" · change since D not shown (that day's record was left out; see the
    note above)" when the period-start drift row was masked (which drops the
    cushion / call-distance deltas), else "". Plain text, no dollar sign."""
    d = _parse_date(start_day)
    if d is None or not excluded_dates_in(excluded, d, d):
        return ""
    return (f" · change since {d.isoformat()} not shown (that day's record was left out; "
            "see the note above)")


def left_out_reading(day, excluded):
    """"left out (why; see the note above)" when the reading for `day` (the
    period-end drift row) was masked, else None, so a masked end cushion never
    prints as a bare dash."""
    d = _parse_date(day)
    if d is None or not excluded_dates_in(excluded, d, d):
        return None
    return f"left out ({_why_left_out(1)}; see the note above)"
