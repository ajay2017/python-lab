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

import numpy as np
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


def _et_ts(value) -> "pd.Timestamp | None":
    """`value` as a tz-aware ET Timestamp, or None for NaT/None/unparseable.

    Naive values are read as UTC, exactly as `normalize_traded_at` (and the
    `timestamptz` column the DB returns) treat them. `format="ISO8601"` so
    mixed precision / offsets never NaT a valid value.
    """
    if value is None or _isna(value):
        return None
    try:
        ts = pd.to_datetime(value, utc=True, errors="coerce", format="ISO8601")
    except (TypeError, ValueError):
        return None
    if ts is None or pd.isna(ts):
        return None
    return ts.tz_convert(_ET)


def et_trade_date(value):
    """The ET calendar date (`datetime.date`) of a stored `traded_at`, or None.

    For display sites that used the UTC `str(traded_at)[:10]` slice, which
    dates a 19:14 ET fill (next-day UTC) one day late. None for
    NaT / None / unparseable, so a caller can tell "no date" from a date.
    """
    ts = _et_ts(value)
    return None if ts is None else ts.date()


def is_placeholder_time(value, time_known=None) -> bool:
    """True when `value`'s time-of-day is an app-invented placeholder.

    Placeholder = `time_known` is explicitly False (`normalize_traded_at`
    invented the time), OR the ET wall clock is exactly 16:00:00.000000 (the
    date-only anchor the importers write). Tested on the ET WALL CLOCK, never a
    fixed UTC hour, because 16:00 ET is 20:00 UTC in EDT and 21:00 UTC in EST.

    `time_known=None` / NaN is NOT a placeholder by that rule (unknown is not
    False). Known limitation, accepted: a real fill at exactly 16:00:00.000000
    ET (a closing-auction print) is also reported as a placeholder.
    """
    if isinstance(time_known, (bool, np.bool_)) and not time_known:
        return True
    ts = _et_ts(value)
    if ts is None:
        return False
    return (ts.hour, ts.minute, ts.second, ts.microsecond) == (
        int(IMPORTED_TRADE_ANCHOR_ET_HOUR), 0, 0, 0)


def et_display(value, time_known=None) -> str:
    """"YYYY-MM-DD HH:MM" in ET; "YYYY-MM-DD —" when the time is a placeholder
    (see `is_placeholder_time`, including its 16:00:00 caveat); "" for
    NaT / None / unparseable.
    """
    ts = _et_ts(value)
    if ts is None:
        return ""
    if is_placeholder_time(value, time_known):
        return ts.strftime("%Y-%m-%d") + " —"
    return ts.strftime("%Y-%m-%d %H:%M")


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

    Also adds `traded_at_time_kind` (RecTime, `docs/plans/fill-time-typed.md`
    S2): "fill" | "recorded" | "placeholder", saying what the stored time IS —
    a real fill time, the moment Record was pressed (an UPPER BOUND on the
    fill), or an app-invented time. First match wins:
      1. "placeholder": re-anchored here, OR the ET wall clock is exactly the
         import anchor (`is_placeholder_time`'s 16:00:00.000000) and
         `fill_time_typed` is not True (a typed 16:00 is a fill), OR the time
         is unparseable.
      2. `fill_time_typed is True`  -> "fill".
      3. `fill_time_typed is False` -> "recorded".
      4. NULL / column absent (legacy row) -> value-only: microsecond != 0 ->
         "recorded", microsecond == 0 -> "fill" (the status quo). Rewrites
         nothing. Fingerprint: `st.time_input` and SQL repairs give microsecond
         0; `now_et().isoformat()` and the DB's `now()` carry microseconds.
    Emitted on the no-provenance fail-closed path too. Failure is isolated: if
    the derivation raises, NO kind column is emitted (consumers read a missing
    column as "fill", the status quo) and every other behaviour is unchanged.
    `traded_at_time_known` is untouched by all of this.

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
        _attach_time_kind(df, None, None)
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
    # Classified from the PRE-rewrite parse `_ts` (a re-anchored row is
    # "placeholder" by `_touch`; every other row's value was not rewritten).
    _attach_time_kind(df, _ts, _touch)
    return df


# `traded_at_time_kind` values (RecTime S2). Read by `sync_timing`.
KIND_FILL        = "fill"
KIND_RECORDED    = "recorded"
KIND_PLACEHOLDER = "placeholder"
TIME_KINDS = (KIND_FILL, KIND_RECORDED, KIND_PLACEHOLDER)


_NS_PER_MICROSECOND = 1_000
_NS_PER_SECOND = 1_000_000_000
_NS_PER_HOUR = 3_600 * _NS_PER_SECOND
_NS_PER_DAY = 24 * _NS_PER_HOUR


def _tri_state(v):
    """`fill_time_typed` cell -> True / False / None (NULL, NaN, pd.NA, anything
    that is not a real boolean). Strict on purpose: only a boolean counts."""
    if v is True or v is np.True_:
        return True
    if v is False or v is np.False_:
        return False
    return None


def _time_kind_values(df: "pd.DataFrame", ts, touch) -> "np.ndarray":
    """The S2 classification for every row of `df`, vectorized (no per-row
    `pd.to_datetime`; see the performance note in `normalize_traded_at`).

    `ts` is the parsed UTC `traded_at` Series (parsed here when None), `touch`
    the per-row "re-anchored by this call" list (all False when None).
    """
    n = len(df)
    if ts is None:
        ts = pd.to_datetime(df["traded_at"], utc=True, errors="coerce",
                            format="ISO8601")
    ok = ts.notna().to_numpy()
    # ET wall-clock time of day as integer nanoseconds. One tz_convert plus one
    # numpy mod; the four `.dt.hour/.minute/.second/.microsecond` accessors cost
    # ~3x the vectorised parse on a 3000-row journal and tripped the speed test.
    # The wall clock (not a fixed UTC hour) because 16:00 ET is 20:00Z in EDT
    # and 21:00Z in EST. NaT rows hold an arbitrary int; `ok` masks them.
    wall = ts.dt.tz_convert(_ET).dt.tz_localize(None)
    tod_ns = np.mod(
        wall.to_numpy().astype("datetime64[ns]").view("int64"),
        _NS_PER_DAY,
    )
    # Reads IMPORTED_TRADE_ANCHOR_ET_HOUR, deliberately NOT this function's
    # `anchor_hour` argument: the kind matches `is_placeholder_time`, which also
    # tests the constant.
    anchor = (tod_ns == int(IMPORTED_TRADE_ANCHOR_ET_HOUR) * _NS_PER_HOUR) & ok
    # `.dt.microsecond != 0`  <=>  the sub-second part is at least 1 microsecond.
    micro_nonzero = (tod_ns % _NS_PER_SECOND) >= _NS_PER_MICROSECOND

    if "fill_time_typed" in df.columns:
        tri = [_tri_state(v) for v in df["fill_time_typed"]]
    else:
        tri = [None] * n
    is_true  = np.array([t is True for t in tri], dtype=bool)
    is_false = np.array([t is False for t in tri], dtype=bool)
    touched  = (np.array(touch, dtype=bool) if touch is not None
                else np.zeros(n, dtype=bool))

    placeholder = touched | ~ok | (anchor & ~is_true)
    # np.select takes the FIRST matching condition, which is the spec's order.
    kinds = np.select(
        [placeholder, is_true, is_false, micro_nonzero],
        [KIND_PLACEHOLDER, KIND_FILL, KIND_RECORDED, KIND_RECORDED],
        default=KIND_FILL,
    )
    return kinds.astype(object)


def _attach_time_kind(df: "pd.DataFrame", ts, touch) -> None:
    """Add `traded_at_time_kind` to `df` in place, or emit NOTHING on failure.

    Isolated from `normalize_traded_at`'s other duties on purpose: a bug here
    must not take the frame (or the journal's re-anchor) down with it. A missing
    column reads as "fill" downstream, which is today's behaviour. Any stale
    kind column inherited from the input is dropped on failure so a consumer
    can't read a kind that no longer matches the rows.
    """
    try:
        df["traded_at_time_kind"] = _time_kind_values(df, ts, touch)
    except Exception as _err:                    # noqa: BLE001 — see docstring
        df.drop(columns=["traded_at_time_kind"], errors="ignore", inplace=True)
        print(f"trade_time: traded_at_time_kind not derived, status quo: {_err}")
