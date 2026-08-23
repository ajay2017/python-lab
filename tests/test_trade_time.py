"""Tests for stock_analyzer/trade_time.py — re-anchoring imported trades whose
`traded_at` landed at midnight UTC (the prior EVENING in ET), plus the
`market_time.et_anchor_iso` writer that stops new ones being created.

The two load-bearing tests here are the TAX INVARIANT (no UTC calendar date may
move, or every `.dt.date` reader silently re-dates) and the CORRUPTION BOUNDARY
(a genuine 20:00 ET fill is byte-indistinguishable from an import by value, so
only provenance may authorise a rewrite). Pure dict/frame math, no I/O.
"""
import pandas as pd
import pytest

from stock_analyzer import trade_time as tt
from stock_analyzer.constants import IMPORTED_TRADE_ANCHOR_ET_HOUR
from stock_analyzer.market_time import et_anchor_iso


def _row(traded_at, notes=None, broker_txn_id=None, ticker="AAA", action="BUY"):
    return {"ticker": ticker, "action": action, "shares": 10.0, "price": 100.0,
            "traded_at": traded_at, "notes": notes, "broker_txn_id": broker_txn_id}


# ─── the constant's safe band ───────────────────────────────────────────────

def test_anchor_hour_is_inside_the_band_that_cannot_move_a_utc_date():
    """>= 19:00 ET pushes into the NEXT UTC day, which would silently re-date
    every `.dt.date` reader — including tax lots and broker dedup keys."""
    assert 0 <= IMPORTED_TRADE_ANCHOR_ET_HOUR < 19


@pytest.mark.parametrize("day", ["2026-07-15", "2026-01-15"])   # EDT and EST
def test_anchoring_never_moves_the_utc_calendar_date(day):
    """THE TAX INVARIANT. Every Class B/C reader takes the UTC date; if the
    anchor moved it, holding periods and lot dates would shift by a day."""
    anchored = et_anchor_iso(day)
    assert pd.to_datetime(anchored, utc=True).date().isoformat() == day


@pytest.mark.parametrize("day", ["2026-07-15", "2026-01-15"])
def test_anchoring_makes_the_et_date_correct(day):
    """The whole point: the ET reading must now match the day the importer meant."""
    anchored = et_anchor_iso(day)
    et = pd.to_datetime(anchored, utc=True).tz_convert("America/New_York").date()
    assert et.isoformat() == day


def test_crossing_the_band_does_shift_the_utc_date_so_the_band_is_real():
    """Pins WHY the band exists, so a future edit past it fails loudly rather
    than quietly re-dating tax lots.

    The band is `< 19` because EST is the binding season: 19:00 EST == 00:00 UTC
    next day, while 19:00 EDT is still 23:00 UTC the same day. Testing only the
    July case would have made the constraint look one hour looser than it is.
    """
    # Inside the band: safe in BOTH seasons.
    assert pd.to_datetime(et_anchor_iso("2026-07-15", hour=16), utc=True).date().isoformat() == "2026-07-15"
    assert pd.to_datetime(et_anchor_iso("2026-01-15", hour=16), utc=True).date().isoformat() == "2026-01-15"
    # 19:00 is the boundary and it bites in WINTER only — which is exactly why
    # the constant is capped below it rather than at 20.
    assert pd.to_datetime(et_anchor_iso("2026-07-15", hour=19), utc=True).date().isoformat() == "2026-07-15"
    assert pd.to_datetime(et_anchor_iso("2026-01-15", hour=19), utc=True).date().isoformat() == "2026-01-16"
    # Past the band: shifts in both seasons.
    assert pd.to_datetime(et_anchor_iso("2026-07-15", hour=20), utc=True).date().isoformat() == "2026-07-16"


def test_et_anchor_iso_uses_localize_not_replace():
    """pytz zones carry a historical LMT offset (-04:56); `replace(tzinfo=...)`
    would apply it silently. A correct offset is -04:00 in July, -05:00 in Jan."""
    assert et_anchor_iso("2026-07-15").endswith("-04:00")
    assert et_anchor_iso("2026-01-15").endswith("-05:00")


# ─── the corruption boundary ────────────────────────────────────────────────

def test_a_genuine_evening_fill_with_no_provenance_is_left_byte_identical():
    """THE CORRUPTION BOUNDARY. A real 20:00 ET fill IS midnight UTC, so value
    alone cannot distinguish it from an import. Without a provenance marker it
    must never be rewritten."""
    original = "2026-07-16T00:00:00.123456+00:00"
    df = pd.DataFrame([_row(original)])
    out = tt.normalize_traded_at(df)
    assert out["traded_at"].iloc[0] == original
    assert bool(out["traded_at_time_known"].iloc[0]) is True


def test_a_timed_row_carrying_an_import_marker_is_left_alone():
    """Both facts are required. A broker row that already has a real time is
    not a bare-date import and must not be moved to 16:00."""
    original = "2026-07-15T13:45:00+00:00"
    df = pd.DataFrame([_row(original, broker_txn_id="abc123")])
    out = tt.normalize_traded_at(df)
    assert out["traded_at"].iloc[0] == original
    assert bool(out["traded_at_time_known"].iloc[0]) is True


def test_fails_closed_when_provenance_columns_are_absent():
    df = pd.DataFrame([{"ticker": "AAA", "action": "BUY", "shares": 1.0,
                        "price": 1.0, "traded_at": "2026-07-16T00:00:00+00:00"}])
    out = tt.normalize_traded_at(df)
    assert out["traded_at"].iloc[0] == "2026-07-16T00:00:00+00:00"
    assert bool(out["traded_at_time_known"].iloc[0]) is True


# ─── rows that SHOULD be re-anchored ────────────────────────────────────────

@pytest.mark.parametrize("prov", [
    {"broker_txn_id": "snap-1"},
    {"notes": "Robinhood import 2026-07-16"},
    {"notes": "RH text import 2026-07-16"},
])
def test_each_import_provenance_marker_triggers_the_reanchor(prov):
    df = pd.DataFrame([_row("2026-07-16T00:00:00+00:00", **prov)])
    out = tt.normalize_traded_at(df)
    ts = pd.to_datetime(out["traded_at"].iloc[0], utc=True)
    assert ts.tz_convert("America/New_York").hour == IMPORTED_TRADE_ANCHOR_ET_HOUR
    assert ts.tz_convert("America/New_York").date().isoformat() == "2026-07-16"
    assert ts.date().isoformat() == "2026-07-16"          # UTC date unmoved
    assert bool(out["traded_at_time_known"].iloc[0]) is False


def test_reanchor_anchors_off_the_utc_date_not_the_et_date():
    """Anchoring off the ET date would bake the off-by-one in permanently —
    midnight UTC on the 16th reads as the 15th in ET."""
    df = pd.DataFrame([_row("2026-07-16T00:00:00+00:00", broker_txn_id="s1")])
    out = tt.normalize_traded_at(df)
    et = pd.to_datetime(out["traded_at"].iloc[0], utc=True).tz_convert("America/New_York")
    assert et.date().isoformat() == "2026-07-16"


def test_only_qualifying_rows_move_in_a_mixed_frame():
    df = pd.DataFrame([
        _row("2026-07-16T00:00:00+00:00", broker_txn_id="s1", ticker="IMP"),
        _row("2026-07-16T00:00:00.123456+00:00", ticker="REAL"),   # evening fill
        _row("2026-07-16T13:45:00+00:00", ticker="MANUAL"),
    ])
    out = tt.normalize_traded_at(df)
    assert list(out["traded_at_time_known"]) == [False, True, True]
    assert out["traded_at"].iloc[1] == "2026-07-16T00:00:00.123456+00:00"
    assert out["traded_at"].iloc[2] == "2026-07-16T13:45:00+00:00"


# ─── offline-sentinel and shape contracts ───────────────────────────────────

def test_none_passes_straight_through_as_the_offline_sentinel():
    """This runs inside load_trades_or_none's path — it must never turn
    'could not read' into 'read and empty'."""
    assert tt.normalize_traded_at(None) is None


def test_empty_frame_returned_unchanged():
    df = pd.DataFrame(columns=["ticker", "traded_at", "notes"])
    assert tt.normalize_traded_at(df).empty


def test_missing_traded_at_column_returned_unchanged():
    df = pd.DataFrame([{"ticker": "AAA", "notes": None}])
    assert "traded_at_time_known" not in tt.normalize_traded_at(df).columns


def test_input_frame_is_not_mutated():
    original = "2026-07-16T00:00:00+00:00"
    df = pd.DataFrame([_row(original, broker_txn_id="s1")])
    tt.normalize_traded_at(df)
    assert df["traded_at"].iloc[0] == original


def test_unparseable_traded_at_does_not_raise_or_get_rewritten():
    df = pd.DataFrame([_row("not-a-timestamp", broker_txn_id="s1")])
    out = tt.normalize_traded_at(df)
    assert out["traded_at"].iloc[0] == "not-a-timestamp"


def test_column_stays_string_dtype_so_str_slicing_readers_keep_working():
    """Several readers do `str(v)[:10]`. Emitting Timestamps instead of ISO
    strings would change the dtype and break them."""
    df = pd.DataFrame([_row("2026-07-16T00:00:00+00:00", broker_txn_id="s1")])
    out = tt.normalize_traded_at(df)
    v = out["traded_at"].iloc[0]
    assert isinstance(v, str)
    assert str(v)[:10] == "2026-07-16"


# ─── the NaT regression this idiom exists to prevent ────────────────────────

def _mixed_frame():
    return pd.DataFrame([
        _row("2026-07-16T00:00:00+00:00", broker_txn_id="s1"),      # -> -04:00
        _row("2026-07-15T13:45:00.123456+00:00"),                   # microseconds
        _row("2026-01-15T00:00:00+00:00", notes="RH text import"),  # -> -05:00
        _row("2026-07-14T18:30:00+00:00"),                          # plain
    ])


def test_format_iso8601_is_required_for_zero_nat_and_the_shim_adds_no_nat():
    """`format="ISO8601"` is MANDATORY, and this is the regression contract.

    Discovered 2026-08-23 while building this: on pandas 3.0.3 bare `utc=True`
    NaTs rows whenever PRECISION varies across the column — which it already
    does today (raw-SQL rows have no microseconds, Python-SDK `now()` rows do;
    see db.recalculate_from_trades' comment). So the NaT is PRE-EXISTING, not
    introduced by widening the offsets. The contract this pins is therefore
    "the shim adds no NaT", not "the column is NaT-free under any idiom".
    """
    before = _mixed_frame()
    after  = tt.normalize_traded_at(before)

    # With the sanctioned idiom: clean, before and after.
    for frame in (before, after):
        parsed = pd.to_datetime(frame["traded_at"], errors="coerce",
                                utc=True, format="ISO8601")
        assert parsed.isna().sum() == 0

    # Without it: already lossy, and the shim must not make it worse.
    nat_before = pd.to_datetime(before["traded_at"], errors="coerce", utc=True).isna().sum()
    nat_after  = pd.to_datetime(after["traded_at"],  errors="coerce", utc=True).isna().sum()
    assert nat_after <= nat_before


def test_anchoring_alone_does_not_fix_the_money_bug_without_the_format_fix():
    """Pins WHY both halves shipped together. An anchored row carries no
    microseconds, so under bare `utc=True` it is still NaT'd out of the
    today-trades filter — the ET fix on its own would have looked correct in
    isolation and changed nothing on the real surface."""
    frame = pd.DataFrame([
        _row("2026-07-16T18:45:12.482913+00:00"),                 # manual, micros
        _row("2026-07-16T00:00:00+00:00", broker_txn_id="s1"),    # import
    ])
    out = tt.normalize_traded_at(frame)
    lossy = pd.to_datetime(out["traded_at"], errors="coerce", utc=True)
    good  = pd.to_datetime(out["traded_at"], errors="coerce", utc=True, format="ISO8601")
    assert lossy.isna().sum() == 1          # the anchored row vanishes
    assert good.isna().sum() == 0


def test_recalculate_from_trades_survives_the_widened_column():
    """The real consumer, not a synthetic parse: db.recalculate_from_trades
    sorts on `_sort_ts` and warns 'no prior BUY' when a SELL replays first."""
    from stock_analyzer import db as _db
    rows = [
        _row("2026-07-16T00:00:00+00:00", broker_txn_id="s1", action="BUY"),
        _row("2026-07-17T13:45:00.123456+00:00", action="SELL"),
    ]
    for i, r in enumerate(rows):
        r["id"] = i + 1
    out = tt.normalize_traded_at(pd.DataFrame(rows))
    result = _db.recalculate_from_trades(out)
    # The contract that matters: the BUY replayed BEFORE the SELL despite the
    # column now mixing offsets, so no "no prior BUY" drift warning is emitted
    # and the position closes out cleanly.
    assert result["warnings"] == []
    assert result["holdings_df"].empty


# ─── ordering: the latent FIFO bug the 16:00 anchor also fixes ──────────────

def test_imported_row_sorts_after_a_same_day_manual_fill():
    """Before the anchor, a midnight-UTC import sorted BEFORE every real fill on
    its own ET day — replaying an imported SELL ahead of the manual BUY that
    funded it. At 16:00 ET it sorts after the regular session."""
    df = pd.DataFrame([
        _row("2026-07-16T00:00:00+00:00", broker_txn_id="s1", action="SELL"),
        _row("2026-07-16T13:45:00+00:00", action="BUY"),   # 09:45 ET
    ])
    out = tt.normalize_traded_at(df)
    ts = pd.to_datetime(out["traded_at"], utc=True, format="ISO8601")
    assert ts.iloc[0] > ts.iloc[1]


# ─── the two live defects, at their exact call-site predicates ──────────────

def test_todays_pnl_filter_now_sees_a_trade_imported_today():
    """app.py's `_today_trades_dp` predicate verbatim. Before the fix this
    dropped the row, so its cash leg never entered the day-P&L delta."""
    df = pd.DataFrame([_row("2026-07-16T00:00:00+00:00", broker_txn_id="s1")])
    out = tt.normalize_traded_at(df)
    et_day = (pd.to_datetime(out["traded_at"], utc=True, errors="coerce")
              .dt.tz_convert("America/New_York").dt.date)
    assert et_day.iloc[0].isoformat() == "2026-07-16"


def test_risk_advisor_bought_today_predicate_now_matches():
    """risk_advisor.py's `_bought_today` predicate verbatim — the whiplash
    suppression that failed OPEN, letting the app trim what it just told you
    to buy."""
    ts = pd.to_datetime(
        tt.normalize_traded_at(
            pd.DataFrame([_row("2026-07-16T00:00:00+00:00", notes="Robinhood import x")])
        )["traded_at"].iloc[0],
        utc=True, errors="coerce",
    )
    assert ts.tz_convert("America/New_York").date().isoformat() == "2026-07-16"


# ─── the marker set must match the detector that already exists ─────────────

def test_rh_screenshot_marker_is_repaired_not_silently_skipped():
    """broker_screenshot.last_screenshot_sync_date ALREADY treats 'RH screenshot'
    as an import marker. A repair that recognised a narrower set left those rows
    wrong AND stamped them traded_at_time_known=True -- an affirmatively false
    claim. Read the detector that exists before writing a narrower one."""
    df = pd.DataFrame([_row("2026-07-16T00:00:00+00:00", notes="RH screenshot import 2026-07-16")])
    out = tt.normalize_traded_at(df)
    ts = pd.to_datetime(out["traded_at"].iloc[0], utc=True)
    assert ts.tz_convert("America/New_York").date().isoformat() == "2026-07-16"
    assert bool(out["traded_at_time_known"].iloc[0]) is False


def test_every_marker_the_screenshot_detector_matches_is_also_repaired():
    """Binds this module's marker set to the sibling detector's regex, so the
    two cannot drift apart again."""
    import re
    from stock_analyzer import broker_screenshot as bs
    import inspect
    src = inspect.getsource(bs.last_screenshot_sync_date)
    detector_markers = re.findall(r'r"([^"]+)"', src)
    assert detector_markers, "detector regex not found -- update this test"
    for alt in detector_markers[0].split("|"):
        note = f"{alt} 2026-07-16"
        out = tt.normalize_traded_at(
            pd.DataFrame([_row("2026-07-16T00:00:00+00:00", notes=note)])
        )
        assert bool(out["traded_at_time_known"].iloc[0]) is False, alt


def test_markers_are_matched_as_substrings_like_the_detector():
    """The sibling uses str.contains, not startswith. A prefixed note must
    still be recognised, or the two disagree about the same row."""
    df = pd.DataFrame([_row("2026-07-16T00:00:00+00:00", notes="Imported: RH text import batch 3")])
    out = tt.normalize_traded_at(df)
    assert bool(out["traded_at_time_known"].iloc[0]) is False


def test_app_writers_emit_notes_the_detector_recognises():
    """The writers build their notes from these exported constants, so a
    reworded writer cannot silently disarm the repair."""
    for marker in tt.IMPORT_NOTE_MARKERS:
        out = tt.normalize_traded_at(
            pd.DataFrame([_row("2026-07-16T00:00:00+00:00", notes=f"{marker} 2026-07-16")])
        )
        assert bool(out["traded_at_time_known"].iloc[0]) is False, marker


# ─── loader and writer must agree, including across a DST transition ────────

@pytest.mark.parametrize("day", [
    "2026-07-15", "2026-01-15",
    "2026-03-08",   # spring forward
    "2026-11-01",   # fall back
])
def test_loader_and_writer_anchor_identically(day):
    """They did not originally: pd.Timestamp(d, tz=ET) + Timedelta is an
    ABSOLUTE offset while ET.localize is WALL-CLOCK, and they disagreed by an
    hour on both DST transition days."""
    from stock_analyzer.market_time import et_anchor_iso
    loader = tt.anchored_iso(f"{day}T00:00:00+00:00")
    writer = et_anchor_iso(day)
    assert loader == writer
    assert pd.to_datetime(loader, utc=True).tz_convert(
        "America/New_York").hour == IMPORTED_TRADE_ANCHOR_ET_HOUR


# ─── the vectorized detection must behave exactly like the scalar one ───────

def test_vectorized_midnight_detection_matches_the_scalar_helper():
    values = [
        "2026-07-16T00:00:00+00:00",
        "2026-07-16T00:00:00.000000+00:00",
        "2026-07-16T00:00:00.123456+00:00",
        "2026-07-16T13:45:00+00:00",
        "not-a-timestamp",
        None,
    ]
    df = pd.DataFrame([_row(v, broker_txn_id="s1") for v in values])
    out = tt.normalize_traded_at(df)
    touched = [not k for k in out["traded_at_time_known"]]
    assert touched == [tt.is_midnight_utc(v) for v in values]


def test_a_nat_row_is_not_treated_as_midnight():
    """A NaT would compare False on every component; guard against a future
    refactor that drops the notna() term and starts rewriting garbage."""
    df = pd.DataFrame([_row("not-a-timestamp", broker_txn_id="s1")])
    out = tt.normalize_traded_at(df)
    assert bool(out["traded_at_time_known"].iloc[0]) is True


def test_normalize_is_not_quadratic_on_a_realistic_journal():
    """Runs inside load_trades_or_none (~44 call sites). A per-row scalar parse
    cost ~0.55ms/row -- 168ms on a 300-row journal, every load."""
    import time
    rows = []
    for i in range(300):
        rows.append(_row(f"2026-07-{(i % 28) + 1:02d}T13:45:00.123456+00:00"))
    df = pd.DataFrame(rows)
    t0 = time.perf_counter()
    tt.normalize_traded_at(df)
    assert (time.perf_counter() - t0) < 0.10


def test_pd_na_broker_id_is_not_read_as_provenance():
    """`str(pd.NA)` is '<NA>', which a string blocklist would pass as a real id."""
    df = pd.DataFrame([_row("2026-07-16T00:00:00.123456+00:00", broker_txn_id=pd.NA)])
    out = tt.normalize_traded_at(df)
    assert bool(out["traded_at_time_known"].iloc[0]) is True


# ─── the rewrite path, not just the detection path ──────────────────────────

def test_a_touched_row_never_gets_a_null_traded_at():
    """The rewrite used to re-parse each value and could return None on a miss,
    writing NULL into traded_at while stamping the row 'time known = False' --
    a trade silently losing its date. Driving the rewrite off the already-parsed
    timestamps makes that unreachable by construction."""
    rows = [
        _row("2026-07-16T00:00:00+00:00", broker_txn_id="s1"),
        _row("not-a-timestamp", broker_txn_id="s2"),
        _row("2026-01-15T00:00:00+00:00", notes="RH screenshot"),
    ]
    out = tt.normalize_traded_at(pd.DataFrame(rows))
    assert out["traded_at"].notna().all()
    assert all(isinstance(v, str) for v in out["traded_at"])


def test_normalize_is_fast_even_when_every_row_is_rewritten():
    """The original perf test had ZERO touched rows, so it only exercised the
    detection half. The rewrite half was still scalar-parsing per row."""
    import time
    rows = [
        _row(f"2026-07-{(i % 28) + 1:02d}T00:00:00+00:00", broker_txn_id=f"s{i}")
        for i in range(300)
    ]
    df = pd.DataFrame(rows)
    t0 = time.perf_counter()
    out = tt.normalize_traded_at(df)
    assert (time.perf_counter() - t0) < 0.20
    assert not any(out["traded_at_time_known"])


# ─── the shim must never be misreported as a database outage ────────────────

def test_a_shim_failure_falls_back_to_the_raw_frame_not_the_offline_sentinel(monkeypatch):
    """normalize_traded_at runs inside load_trades_or_none's try. If its
    exceptions fell through they would record a DB error and return None --
    the sentinel F-243's outage gate escalates to refusing to render the
    portfolio. A pure-logic bug must not look like a Supabase outage."""
    from stock_analyzer import db as _db
    import stock_analyzer.trade_time as _tt

    def _boom(*a, **k):
        raise RuntimeError("synthetic shim failure")

    monkeypatch.setattr(_tt, "normalize_traded_at", _boom)

    rows = [{"id": 1, "ticker": "AAA", "action": "BUY", "shares": 1.0,
             "price": 1.0, "traded_at": "2026-07-16T00:00:00+00:00",
             "notes": None, "broker_txn_id": None}]

    class _Resp:
        data = rows

    class _Q:
        def select(self, *a, **k): return self
        def order(self, *a, **k):  return self
        def execute(self):         return _Resp()

    class _Client:
        def table(self, *a, **k): return _Q()

    monkeypatch.setattr(_db, "has_db", lambda: True)
    monkeypatch.setattr(_db, "_client", lambda: _Client())

    out = _db.load_trades_or_none()
    assert out is not None, "a shim bug must not masquerade as an outage"
    assert len(out) == 1
    assert out["traded_at"].iloc[0] == "2026-07-16T00:00:00+00:00"
