"""Tests for the reference-data shelf-life registry (2026-08-15).

The feature exists because static reference tables drift SILENTLY — a stale
ticker universe never errors, it just quietly stops surfacing names. These
tests lock the two invariants that make the check trustworthy (a new table
can't be registered without a shelf life; the check never nags or lies) and
assert the staleness boundaries EXACTLY rather than a day either side, because
the 2026-08-04 Critical was an off-by-one of precisely this shape that a design
review had called harmless.
"""
from datetime import date

from stock_analyzer import reference_shelf as rs
from stock_analyzer.constants import (
    REFERENCE_HORIZON_MIN_DAYS,
    REFERENCE_SHELF_LIFE_DAYS,
)


# ── ① registry/constants key-set sync, BOTH directions ────────────────────────
# The whole reason for a central registry is that "a table exists with no shelf
# life" is the failure being fixed. These two assertions are what actually
# enforce it — without them the registry is just a list.

def test_every_registered_table_has_a_shelf_life():
    registered = {e.key for e in rs._REFERENCE_TABLES}
    configured = set(REFERENCE_SHELF_LIFE_DAYS) | set(REFERENCE_HORIZON_MIN_DAYS)
    assert registered == configured, (
        "registry and constants disagree — a table was added to one and not the "
        f"other. Only in registry: {registered - configured}. "
        f"Only in constants: {configured - registered}"
    )


def test_each_key_is_in_exactly_the_dict_matching_its_kind():
    for entry in rs._REFERENCE_TABLES:
        in_shelf = entry.key in REFERENCE_SHELF_LIFE_DAYS
        in_horizon = entry.key in REFERENCE_HORIZON_MIN_DAYS
        assert in_shelf != in_horizon, f"{entry.key} must be in exactly one dict"
        if entry.kind == rs.KIND_AS_OF:
            assert in_shelf, f"{entry.key} is KIND_AS_OF but has no shelf life"
        else:
            assert in_horizon, f"{entry.key} is KIND_HORIZON but has no horizon minimum"


def test_as_of_tables_have_a_date_and_horizon_tables_have_a_function():
    for entry in rs._REFERENCE_TABLES:
        if entry.kind == rs.KIND_AS_OF:
            assert entry.as_of is not None, f"{entry.key} has no as_of date"
        else:
            assert entry.horizon_fn is not None, f"{entry.key} has no horizon_fn"


def test_every_entry_states_a_consequence_not_just_a_number():
    """Per feedback_recommendation_transparency — never surface a bare number."""
    for entry in rs._REFERENCE_TABLES:
        assert entry.consequence and len(entry.consequence) > 20, entry.key


# ── ② KIND_AS_OF boundary, exact ──────────────────────────────────────────────

def _as_of_entry(key="sector_universe"):
    return next(e for e in rs._REFERENCE_TABLES if e.key == key)


def test_as_of_ok_exactly_at_the_shelf_life_boundary():
    entry = _as_of_entry()
    shelf = REFERENCE_SHELF_LIFE_DAYS[entry.key]
    today = date.fromordinal(entry.as_of.toordinal() + shelf)
    severity, _ = rs._grade_as_of(entry, today)
    assert severity == "ok", "age == shelf life must still be ok (inclusive boundary)"


def test_as_of_warns_one_day_past_the_boundary():
    entry = _as_of_entry()
    shelf = REFERENCE_SHELF_LIFE_DAYS[entry.key]
    today = date.fromordinal(entry.as_of.toordinal() + shelf + 1)
    severity, _ = rs._grade_as_of(entry, today)
    assert severity == "warn"


def test_as_of_never_reports_down():
    """A stale membership list is a chore, never an outage — it must not be
    able to reach the same severity a missing DB table uses."""
    entry = _as_of_entry()
    today = date.fromordinal(entry.as_of.toordinal() + 10_000)
    severity, _ = rs._grade_as_of(entry, today)
    assert severity == "warn"


# ── ③ KIND_HORIZON boundary, exact ────────────────────────────────────────────

def _horizon_entry(horizon: date | None, key="nyse_calendar"):
    entry = next(e for e in rs._REFERENCE_TABLES if e.key == key)
    return rs._RefTable(
        key=entry.key, label=entry.label, location=entry.location,
        kind=rs.KIND_HORIZON, consequence=entry.consequence,
        horizon_fn=lambda: horizon,
    )


def test_horizon_ok_exactly_at_the_minimum_runway(monkeypatch):
    min_days = REFERENCE_HORIZON_MIN_DAYS["nyse_calendar"]
    today = date(2026, 8, 15)
    entry = _horizon_entry(date.fromordinal(today.toordinal() + min_days))
    severity, _ = rs._grade_horizon(entry, today)
    assert severity == "ok", "runway == minimum must still be ok"


def test_horizon_warns_one_day_below_the_minimum(monkeypatch):
    min_days = REFERENCE_HORIZON_MIN_DAYS["nyse_calendar"]
    today = date(2026, 8, 15)
    entry = _horizon_entry(date.fromordinal(today.toordinal() + min_days - 1))
    severity, _ = rs._grade_horizon(entry, today)
    assert severity == "warn"


def test_horizon_in_the_past_is_down(monkeypatch):
    """An expired forward-dated table IS an outage — the data is simply gone."""
    today = date(2026, 8, 15)
    entry = _horizon_entry(date(2026, 8, 14))
    severity, detail = rs._grade_horizon(entry, today)
    assert severity == "down"
    assert "EXPIRED" in detail


# ── ④ the macro horizon is PER SERIES, not a global max ───────────────────────
# The bug this prevents: the FOMC series was extended through 2027-12-08 while
# CPI/payrolls/PPI/retail/GDP all still ended in late 2026. A global max would
# have read "green" with five of six series about to expire on a decision path.

def _series(event: str, dates: list[str]) -> list[tuple]:
    """Build a recurring series — at least _MACRO_MIN_SERIES_ROWS rows, so it
    counts as backbone coverage rather than a one-off."""
    return [(d, "08:30", event, "Cat", "HIGH", "") for d in dates]


def test_macro_horizon_takes_the_earliest_series_expiry(monkeypatch):
    fake_static = (
        _series("FOMC Rate Decision",
                ["2027-03-17", "2027-06-09", "2027-09-15", "2027-12-08"])
        + _series("GDP Advance Estimate",
                  ["2026-01-29", "2026-04-29", "2026-07-30", "2026-10-29"])
        + _series("CPI Inflation",
                  ["2027-03-10", "2027-04-13", "2027-05-12", "2027-06-10"])
    )
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", fake_static)
    horizon, limiter = rs._macro_static_horizon()
    assert horizon == date(2026, 10, 29), (
        "must report the FIRST series to run dry, not the furthest-out date"
    )
    assert limiter == "GDP Advance Estimate", "must name the limiting series"


def test_a_one_off_event_cannot_pin_the_macro_row_to_false_red(monkeypatch):
    """A single special entry (Jackson Hole, a debt-ceiling X-date) would
    otherwise be its own one-row 'series' whose max is in the past, dragging
    min() below zero and reporting the whole backbone as EXPIRED forever."""
    fake_static = (
        _series("CPI Inflation",
                ["2027-03-10", "2027-04-13", "2027-05-12", "2027-06-10"])
        + [("2026-08-21", "10:00", "Jackson Hole Symposium", "Fed Policy", "HIGH", "")]
    )
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", fake_static)
    horizon, limiter = rs._macro_static_horizon()
    assert horizon == date(2027, 6, 10)
    assert limiter == "CPI Inflation", "a one-off must not become the limiter"


def test_extending_the_weakest_series_clears_the_warning(monkeypatch):
    """Proves the horizon is DERIVED — extending the table is the only action
    needed, with no second place to update."""
    today = date(2026, 8, 15)
    entry = next(e for e in rs._REFERENCE_TABLES if e.key == "macro_event_calendar")

    thin = _series("GDP Advance Estimate",
                   ["2026-01-29", "2026-04-29", "2026-07-30", "2026-10-29"])
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", thin)
    severity, detail = rs._grade_horizon(entry, today)
    assert severity == "warn"
    assert "GDP Advance Estimate" in detail

    extended = _series("GDP Advance Estimate",
                       ["2027-01-28", "2027-04-29", "2027-07-29", "2027-10-28"])
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", extended)
    assert rs._grade_horizon(entry, today)[0] == "ok"


def test_nyse_horizon_derives_from_the_calendar_constant(monkeypatch):
    monkeypatch.setattr("stock_analyzer.constants.MARKET_CALENDAR_LAST_YEAR", 2031)
    assert rs._nyse_calendar_horizon() == date(2031, 12, 31)


# ── ⑤ unknown, never silently ok; never raises ────────────────────────────────

def test_unreadable_horizon_is_unknown_not_ok():
    def _boom():
        raise RuntimeError("source unreadable")

    entry = rs._RefTable(
        key="nyse_calendar", label="x", location="y", kind=rs.KIND_HORIZON,
        consequence="z" * 30, horizon_fn=_boom,
    )
    severity, _ = rs._grade_horizon(entry, date(2026, 8, 15))
    assert severity == "unknown", "silence is not health — must never grade ok"


def test_missing_as_of_is_unknown_not_ok():
    entry = rs._RefTable(
        key="sector_universe", label="x", location="y", kind=rs.KIND_AS_OF,
        consequence="z" * 30, as_of=None,
    )
    assert rs._grade_as_of(entry, date(2026, 8, 15))[0] == "unknown"


def test_shelf_status_never_raises_on_a_broken_entry(monkeypatch):
    broken = rs._RefTable(
        key="nope", label="broken", location="nowhere", kind=rs.KIND_HORIZON,
        consequence="z" * 30, horizon_fn=lambda: (_ for _ in ()).throw(ValueError("x")),
    )
    monkeypatch.setattr(rs, "_REFERENCE_TABLES", (broken,))
    rows = rs.shelf_status()
    assert isinstance(rows, list) and len(rows) == 1
    assert rows[0]["severity"] == "unknown"


def test_shelf_status_returns_a_row_per_registered_table():
    rows = rs.shelf_status(today=date(2026, 8, 15))
    assert {r["key"] for r in rows} == {e.key for e in rs._REFERENCE_TABLES}
    assert all(r["severity"] in ("ok", "warn", "down", "unknown") for r in rows)


# ── ⑥ date source ─────────────────────────────────────────────────────────────

def test_today_resolves_via_market_time_not_naive_date_today(monkeypatch):
    """Patch to a date where the two implementations DISAGREE.

    An earlier version of this test patched to 2031 and asserted "warn" — but
    naive date.today() (2026-08-15, age 102d > 90d shelf) also yields "warn",
    so it could not distinguish the implementations and passed for the wrong
    reason. 2026-05-06 is one day after sector_universe's as_of, so the correct
    implementation says "ok" while a naive clock would still say "warn".
    """
    entry = _as_of_entry()
    monkeypatch.setattr("stock_analyzer.market_time.today_et",
                        lambda: date(2026, 5, 6))
    row = next(r for r in rs.shelf_status() if r["key"] == entry.key)
    assert row["severity"] == "ok", (
        "shelf_status must read the patched market_time clock; a naive "
        "date.today() would report this table as overdue"
    )
    assert rs._today() == date(2026, 5, 6)


# ── ⑦ coupling-map regression (the drift a universe refresh will introduce) ───

def test_diversify_map_keys_and_values_resolve():
    """`_DIVERSIFY_TO_DISCOVERY` is keyed on BOTH universes' bucket labels, so a
    rename during the refresh this feature exists to prompt would silently
    degrade diversification suggestions to roster-only. Cheap guard."""
    from stock_analyzer.discovery_universe import DISCOVERY_UNIVERSE
    from stock_analyzer.portfolio import _DIVERSIFY_TO_DISCOVERY, _SECTOR_CANDIDATES

    for sector, buckets in _DIVERSIFY_TO_DISCOVERY.items():
        assert sector in _SECTOR_CANDIDATES, f"{sector!r} is not a _SECTOR_CANDIDATES key"
        for bucket in (buckets if isinstance(buckets, (list, tuple, set)) else [buckets]):
            assert bucket in DISCOVERY_UNIVERSE, (
                f"{sector!r} maps to DISCOVERY_UNIVERSE bucket {bucket!r}, which no longer exists"
            )
