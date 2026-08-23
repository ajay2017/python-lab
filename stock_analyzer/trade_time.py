"""
Re-anchor imported trades to a real ET time, so "what day did this trade
happen" has one answer.

THE DEFECT. `trades.traded_at` is `timestamptz`. The three import writers
(broker sync, CSV, RH-text) send a bare date string; Postgres casts it with the
session timezone (UTC on Supabase) and stores midnight UTC. Midnight UTC is
**the previous evening in ET**, so every reader that does
`tz_convert("America/New_York").date()` dates an imported trade one day early.

Two consequences, both live before this module existed:
  • `app.py`'s Today's-P&L today-trades filter dropped a trade imported TODAY,
    so its cash leg never entered the day-P&L delta — a wrong dollar figure.
  • `risk_advisor`'s `_bought_today` whiplash suppression FAILED OPEN, letting
    the app name a position for trim hours after recommending the buy.

WHY THIS IS NOT A ONE-LINE PARSE FIX. Readers split into two camps that fail in
OPPOSITE directions, so there is no single parse that fixes both:
  • `tz_convert(...).date()`  — midnight-UTC rows shift BACK one ET day.
  • `.date()` on the UTC value — correct for midnight-UTC rows, but a genuine
    20:00 ET fill (next-day UTC) shifts FORWARD one day.
Re-anchoring the VALUE, once, at the point it is loaded, makes both camps agree
without touching either. That is why this runs in `db.load_trades_or_none()`
rather than at 20-odd call sites.

THE CORRUPTION BOUNDARY, and why detection is provenance-gated. Because the
column is `timestamptz`, an imported row is **byte-indistinguishable** from a
real fill that happened at 20:00 ET. A value-only rule ("re-anchor anything at
midnight UTC") would therefore silently re-date genuine after-hours trades. So
a row is re-anchored only on the CONJUNCTION of two independent facts: the
value is exactly midnight UTC, AND the row carries an import-provenance marker.
Anything else is left byte-identical. Fails closed: if the provenance columns
are missing entirely, nothing is touched.

Emits an ISO string with an ET offset rather than a Timestamp, deliberately —
the column stays `object`/string dtype, so the `str(v)[:10]` readers and the
`pd.to_datetime(..., utc=True)` readers both keep working unchanged. Changing
the dtype would be a far larger blast radius for no gain.

MIXED OFFSETS ARE THE POINT, AND THE TRAP. This widens the column from all
`+00:00` to a mix of `+00:00` and `-04:00`/`-05:00`. That is safe *only* with
`utc=True` (and `format="ISO8601"` where precision also varies) — see memory
`feedback_pandas_mixed_tz_parsing` and the comment in `db.recalculate_from_trades`.
Without `utc=True`, pandas infers a format from the first row and coerces the
rest to NaT. Every reader already uses that idiom; `tests/test_trade_time.py`
asserts zero NaT through the real consumers rather than trusting the argument.

Pure / no I/O / no Streamlit.
"""

from __future__ import annotations

import pandas as pd

from stock_analyzer.constants import IMPORTED_TRADE_ANCHOR_ET_HOUR

_ET = "America/New_York"

# Markers the import writers put in `trades.notes`. EXPORTED and consumed by
# the writers themselves (`app.py`) so the text that gets written and the text
# this repair looks for cannot drift apart — editing one writer's wording would
# otherwise disarm the repair silently, and the only symptom would be an ET
# date quietly going back to being wrong.
#
# `broker_txn_id` covers the SnapTrade path structurally and needs no marker.
#
# "RH screenshot" is here because `broker_screenshot.last_screenshot_sync_date`
# ALREADY treats it as a real import marker (`r"RH screenshot|RH text import"`).
# Omitting it would have left that class of row unrepaired while stamping it
# `traded_at_time_known = True` — an affirmatively false claim. Read the
# detector that already exists before writing a narrower one
# (`feedback_validation_reads_detector_source`).
NOTE_CSV_IMPORT        = "Robinhood import"
NOTE_TEXT_IMPORT       = "RH text import"
NOTE_SCREENSHOT_IMPORT = "RH screenshot"
IMPORT_NOTE_MARKERS = (NOTE_CSV_IMPORT, NOTE_TEXT_IMPORT, NOTE_SCREENSHOT_IMPORT)

_IMPORT_NOTE_MARKERS_LOWER = tuple(m.lower() for m in IMPORT_NOTE_MARKERS)


def _has_import_provenance(row_notes, row_broker_id) -> bool:
    """True when a row demonstrably came from an import writer.

    Structural (`broker_txn_id`) OR textual (`notes` marker). Deliberately does
    NOT look at the timestamp — this is the independent second fact that makes
    the conjunction safe, so folding a time check in here would defeat it.

    Substring, not `startswith`, to match `last_screenshot_sync_date`'s existing
    `str.contains` semantics — a detector and its repair disagreeing about what
    counts as the same row is the bug this comment exists to prevent.
    """
    if not _isna(row_broker_id) and str(row_broker_id).strip() not in ("", "None", "nan"):
        return True
    note = str(row_notes or "").strip().lower()
    return any(m in note for m in _IMPORT_NOTE_MARKERS_LOWER)


def _isna(v) -> bool:
    """`pd.isna` that survives being handed a list/array (which returns an array)."""
    try:
        r = pd.isna(v)
        return bool(r) if not hasattr(r, "__len__") else False
    except (TypeError, ValueError):
        return v is None


def is_midnight_utc(value) -> bool:
    """True when `value` parses to exactly 00:00:00 UTC (the import fingerprint).

    NOT sufficient on its own to re-anchor — a real 20:00 ET fill is also
    midnight UTC. Pair with `_has_import_provenance`. Scalar convenience only;
    `normalize_traded_at` uses the vectorized path.
    """
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if ts is None or pd.isna(ts):
        return False
    return (ts.hour, ts.minute, ts.second, ts.microsecond) == (0, 0, 0, 0)


def anchored_iso(value, anchor_hour: int = IMPORTED_TRADE_ANCHOR_ET_HOUR) -> str | None:
    """Midnight-UTC `value` → the SAME UTC calendar day at `anchor_hour` ET, as ISO.

    Anchors off the **UTC** date, not the ET date: the UTC date is the calendar
    day the importer actually meant (it is what the bare date string became),
    whereas the ET date is already the off-by-one this function exists to undo.

    Delegates to `market_time.et_anchor_iso` so the loader and the WRITER share
    one implementation. They did not originally: `pd.Timestamp(d, tz=ET) +
    Timedelta(hours=16)` is an ABSOLUTE offset while `ET.localize(...)` is
    WALL-CLOCK, and the two disagree by an hour across a DST transition
    (2026-03-08 → 17:00 vs 16:00).
    """
    from stock_analyzer.market_time import et_anchor_iso
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if ts is None or pd.isna(ts):
        return None
    return et_anchor_iso(ts.date(), int(anchor_hour))


def normalize_traded_at(
    trades_df: "pd.DataFrame | None",
    anchor_hour: int = IMPORTED_TRADE_ANCHOR_ET_HOUR,
) -> "pd.DataFrame | None":
    """Re-anchor imported midnight-UTC `traded_at` values to `anchor_hour` ET.

    Returns a COPY with `traded_at` rewritten for qualifying rows only, plus a
    boolean `traded_at_time_known` column (False exactly for rows this touched)
    so a consumer that genuinely needs a fill TIME — as opposed to a fill DAY —
    can exclude them rather than assert a time the app invented.

    ⚠️ `traded_at_time_known` IS ASYMMETRIC. `False` is a firm claim: this
    function invented that time. `True` is only "not re-anchored here" — it is
    also True on the fail-closed no-provenance path, and True for any bare-date
    import whose marker was lost or never written. So it is sound to EXCLUDE on
    `False` and unsound to treat `True` as evidence a real fill time is known.
    Note too that a genuinely-empty journal short-circuits earlier in
    `load_trades_or_none`, so the column is ABSENT on an empty frame — read it
    with `.get`, not `df["traded_at_time_known"]`.

    Passes `None` straight through: this sits inside the offline-sentinel path
    and must never convert "could not read" into "read and empty".
    """
    if trades_df is None:
        return None
    if not isinstance(trades_df, pd.DataFrame) or trades_df.empty:
        return trades_df
    if "traded_at" not in trades_df.columns:
        return trades_df

    df = trades_df.copy()

    # Fail closed: with no provenance column there is no second fact, and a
    # value-only rule would re-date genuine after-hours fills.
    has_notes  = "notes" in df.columns
    has_broker = "broker_txn_id" in df.columns
    if not (has_notes or has_broker):
        df["traded_at_time_known"] = True
        return df

    _notes  = df["notes"]         if has_notes  else pd.Series([None] * len(df), index=df.index)
    _broker = df["broker_txn_id"] if has_broker else pd.Series([None] * len(df), index=df.index)

    _is_import = [
        _has_import_provenance(n, b) for n, b in zip(_notes, _broker)
    ]

    # VECTORIZED, deliberately. This runs inside load_trades_or_none(), which
    # has ~44 call sites, so a per-row scalar pd.to_datetime here cost ~0.55ms
    # PER ROW (168ms on a 300-row journal, ~50x the vectorized parse) on every
    # single load. `format="ISO8601"` is required, not optional — see the module
    # docstring; without it varying microsecond precision silently NaTs rows,
    # and a NaT would read as "not midnight" and skip a row that needs repair.
    _ts = pd.to_datetime(df["traded_at"], utc=True, errors="coerce",
                         format="ISO8601")
    _is_midnight = (
        (_ts.dt.hour == 0) & (_ts.dt.minute == 0)
        & (_ts.dt.second == 0) & (_ts.dt.microsecond == 0)
        & _ts.notna()
    )
    _touch = [bool(imp and mid) for imp, mid in zip(_is_import, _is_midnight)]

    if any(_touch):
        # Reuse the dates already parsed above rather than re-parsing per row.
        # Two reasons, both real: the scalar re-parse was ~0.55ms/row on the
        # app's hottest read path, AND `anchored_iso` returns None on a parse
        # miss — which would have been written straight into `traded_at`,
        # silently destroying a trade's date while stamping it "time known =
        # False". Driving off `_ts` makes that branch unreachable by
        # construction: `_touch` already requires `_ts.notna()`.
        from stock_analyzer.market_time import et_anchor_iso
        df.loc[_touch, "traded_at"] = [
            et_anchor_iso(d, int(anchor_hour)) for d in _ts[_touch].dt.date
        ]
    df["traded_at_time_known"] = [not t for t in _touch]
    return df
