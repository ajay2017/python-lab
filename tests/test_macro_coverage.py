"""Tests for macro calendar coverage expiry (Task 5 — spec-required assertions).

Tests are deliberately boundary-asserting, not reasoned — the spec calls out
that "boundary asserted, not reasoned" is the requirement for test #1.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

import pandas as pd

from stock_analyzer import reference_shelf as rs
from stock_analyzer.constants import COMPOSITE_BUY, MACRO_IMMINENT_DAYS
from stock_analyzer.daily_briefing import _expired_macro_series, _grow_today
from stock_analyzer.notify import _macro_coverage_banner


# ── helpers ───────────────────────────────────────────────────────────────────

def _series(event: str, dates: list[str], category: str = "Growth") -> list[tuple]:
    """Build a recurring series with at least _MACRO_MIN_SERIES_ROWS rows."""
    return [(d, "08:30", event, category, "HIGH", "") for d in dates]


def _minimal_grow_today_kwargs(**overrides):
    """Minimal kwargs for _grow_today — overrides applied on top."""
    defaults = dict(
        port_df=pd.DataFrame(),
        scanner_results=pd.DataFrame(),
        news_items=[],
        held_data={},
        today=date(2026, 10, 30),
        portfolio_value=100_000.0,
        market_context={"tone": "bull", "sp500_pct": 0.5, "nasdaq_pct": 0.5,
                        "leading_sectors": []},
        act_today=None,
        review_list=None,
        composites=None,
        risk_recs=None,
        earnings_lookup=None,
        macro_events=[],
        movers=[],
        deterioration=None,
        winner_profile=None,
        net_capital=None,
        sold_today=None,
    )
    defaults.update(overrides)
    return defaults


# ── Test 1: boundary asserted on synthetic _STATIC ───────────────────────────
# Pinned to a synthetic table so these tests keep passing when the live macro
# calendar is extended (exactly the maintenance chore this feature prompts for).

_GDP_ONLY_STATIC = [
    # Four GDP rows — crosses _MACRO_MIN_SERIES_ROWS floor.  Last date 2026-10-29.
    ("2026-01-29", "08:30", "GDP Advance Estimate", "Growth", "HIGH", "Q4 2025"),
    ("2026-04-29", "08:30", "GDP Advance Estimate", "Growth", "HIGH", "Q1 2026"),
    ("2026-07-29", "08:30", "GDP Advance Estimate", "Growth", "HIGH", "Q2 2026"),
    ("2026-10-29", "08:30", "GDP Advance Estimate", "Growth", "HIGH", "Q3 2026"),
]


def test_on_last_date_nothing_is_expired(monkeypatch):
    """On 2026-10-29 (the last row date) the series has NOT expired yet.
    last == today means last >= today, so the function must not report it.
    """
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", _GDP_ONLY_STATIC)
    result = rs.expired_macro_series(date(2026, 10, 29))
    assert result is not None, "must not return None when the table is readable"
    names = [e.get("name") for e in (result or [])]
    assert "GDP Advance Estimate" not in names, (
        "on 2026-10-29 (the last date) the series is not expired — last == today"
    )


def test_one_day_after_last_date_gdp_is_expired(monkeypatch):
    """2026-10-30 is the first day after the last GDP row.  The series must
    appear in the result — this is the exact boundary the feature exists to catch.
    """
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", _GDP_ONLY_STATIC)
    result = rs.expired_macro_series(date(2026, 10, 30))
    assert result is not None, "must not return None when the table is readable"
    names = [e.get("name") for e in (result or [])]
    assert "GDP Advance Estimate" in names, (
        "on 2026-10-30 GDP Advance Estimate has no future dates and must be reported"
    )
    # Exactly one entry — only GDP is in the synthetic table.
    assert len(result) == 1, (
        f"expected exactly one expired series on 2026-10-30, got {len(result)}: {names}"
    )


# ── Test 2: offline sentinel ──────────────────────────────────────────────────

def test_expired_macro_series_returns_none_when_static_unreadable(monkeypatch):
    """When _STATIC cannot be imported, expired_macro_series must return None —
    not [], not [].  assert is None (not just falsiness) so the sentinel contract
    is proven."""
    def _boom(*a, **kw):
        raise ImportError("simulated import failure")

    # Patch _macro_series_index to raise
    monkeypatch.setattr(rs, "_macro_series_index", _boom)
    result = rs.expired_macro_series(date(2026, 10, 30))
    assert result is None, (
        "None is the contract for 'could not verify'; must not collapse to []"
    )


def test_daily_briefing_expired_macro_series_returns_none_on_exception(monkeypatch):
    """daily_briefing._expired_macro_series wraps reference_shelf in try/except.
    Force the underlying reference_shelf call to raise and confirm the wrapper
    returns None — not the exception, not []."""
    # Force reference_shelf.expired_macro_series to raise so the wrapper's
    # own try/except is exercised directly.
    monkeypatch.setattr(rs, "expired_macro_series", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("simulated failure")))
    result = _expired_macro_series(date(2026, 10, 30))
    assert result is None, (
        "daily_briefing._expired_macro_series must return None (not raise) "
        "when the underlying reference_shelf call fails"
    )


# ── Test 3: min-rows floor ────────────────────────────────────────────────────

def test_single_row_one_off_is_never_reported_as_expired(monkeypatch):
    """A one-off entry with a past date and only one row must never appear in
    expired_macro_series (< _MACRO_MIN_SERIES_ROWS)."""
    fake_static = [
        ("2026-01-01", "10:00", "Debt Ceiling X-Date", "Fiscal", "HIGH", "one-off"),
        # Also include one healthy recurring series so the index isn't empty.
        *_series("CPI Inflation", ["2027-03-10", "2027-04-13", "2027-05-12", "2027-06-10"]),
    ]
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", fake_static)
    result = rs.expired_macro_series(date(2026, 8, 1))
    assert result is not None
    names = [e.get("name") for e in result]
    assert "Debt Ceiling X-Date" not in names, (
        "a one-off entry (1 row) must not pass the _MACRO_MIN_SERIES_ROWS floor"
    )


# ── Test 4: gate isolation — awareness-only proof ────────────────────────────

def test_expired_static_does_not_change_new_picks_or_macro_blocked(monkeypatch):
    """Run _grow_today twice: once with live (clean) _STATIC, once with a
    fully-expired _STATIC.  new_picks and macro_blocked_picks must be IDENTICAL
    — the coverage key is awareness-only and must never gate picks.

    Three controls make the test un-vacuum-able:
    1. Independent-variable control: live run has CLEAN coverage ([] on
       2026-09-01), patched run has EXPIRED coverage (non-empty list).  Without
       this the two runs could have the same coverage state and the equality
       assertion proves nothing — it was the exact flaw in the previous round.
    2. new_picks non-empty control: "NEW" (Healthcare) is NOT macro-blocked by
       the Employment event (Healthcare severity=1 < threshold=2) and has valid
       composites, so it reaches new_picks in both runs.  The mutation
       `if _expired_macro_series(today): new_picks = []` is then detectable
       because it would empty new_picks only in the expired run, making
       new_picks_live != new_picks_expired and failing the equality assertion.
    3. macro_blocked_picks non-empty control: "BLOCKED" (Financials, severity=3
       >= threshold=2) IS macro-blocked in both runs, confirming the gate fired.

    Setup:
    - today = 2026-09-01 (live _STATIC is CLEAN — all series extend past here)
    - Employment HIGH event at 2026-09-01 (0 days away, inside MACRO_IMMINENT_DAYS)
      blocks Financials but not Healthcare
    - Scanner: "NEW" Healthcare (composite-valid → new_picks),
               "BLOCKED" Financials (macro-blocked → macro_blocked_picks)
    """
    from stock_analyzer.macro_calendar import _STATIC as _live_static

    # Expired fake: every series ended well before 2026-09-01.
    fake_expired = _series("GDP Advance Estimate",
                           ["2025-01-29", "2025-04-29", "2025-07-29", "2025-10-29"])

    # Two scanner picks: "NEW" in Healthcare (not macro-blocked), "BLOCKED" in
    # Financials (macro-blocked by Employment event, severity 3 >= threshold 2).
    scanner = pd.DataFrame([
        {"Ticker": "NEW",     "Score": COMPOSITE_BUY + 10, "Price": 50.0,
         "Signal": "Buy", "Sector": "Healthcare", "RSI": 60.0,
         "1M Momentum": 5.0, "Trend": "Up"},
        {"Ticker": "BLOCKED", "Score": COMPOSITE_BUY + 10, "Price": 80.0,
         "Signal": "Buy", "Sector": "Financials", "RSI": 60.0,
         "1M Momentum": 5.0, "Trend": "Up"},
    ])

    # Valid composites for "NEW" so it clears the composite gate and enters
    # new_picks.  "BLOCKED" is macro-gated before the composite check — it
    # needs no composites entry.
    composites = {"NEW": {
        "total": COMPOSITE_BUY + 10, "rec": {"label": "Strong Buy"},
        "stale_as_of": None, "fund_cache_age_days": None,
        "fundamentals_available": True, "val_available": True,
    }}

    # today = 2026-09-01: live _STATIC has CLEAN coverage ([] not non-empty).
    # Employment HIGH event at 2026-09-01 (0 days away).  No "released" key
    # → gate stays on.  No "time_et" → clock path skipped.
    today = date(2026, 9, 1)
    macro_events = [{
        "date": "2026-09-01", "event": "Non-Farm Payrolls",
        "category": "Employment", "impact": "HIGH",
    }]

    kwargs = _minimal_grow_today_kwargs(
        scanner_results=scanner,
        composites=composites,
        macro_events=macro_events,
        today=today,
    )

    # Run 1: live _STATIC — coverage must be CLEAN ([] not None) on this date.
    result_live = _grow_today(**kwargs)

    # Run 2: fully-expired _STATIC — coverage must be non-empty.
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", fake_expired)
    result_expired = _grow_today(**kwargs)

    # Reset
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", _live_static)

    # Control 1 — independent variable actually varies between the two runs.
    # If both runs have the same coverage state, the equality assertions below
    # cannot detect a gate that reads _expired_macro_series.
    assert result_live.get("macro_coverage_expired") == [], (
        "control: live run on 2026-09-01 must have CLEAN coverage ([] not None/non-empty); "
        f"got {result_live.get('macro_coverage_expired')!r}"
    )
    assert result_expired.get("macro_coverage_expired"), (
        "control: patched (all-past) run must have EXPIRED coverage (non-empty list); "
        f"got {result_expired.get('macro_coverage_expired')!r}"
    )

    # Control 2 — new_picks non-empty in both runs (proves the equality
    # assertion is not vacuous [] == [], and the mutation IS detectable).
    assert result_live.get("new_picks"), (
        f"negative control: new_picks must be non-empty in the live run "
        f"('NEW'/Healthcare is not macro-blocked by Employment); "
        f"got macro_blocked={result_live.get('macro_blocked_picks')}, "
        f"composite_unavail={result_live.get('composite_unavailable')}"
    )
    assert result_expired.get("new_picks"), (
        f"negative control: new_picks must be non-empty in the expired run too; "
        f"got macro_blocked={result_expired.get('macro_blocked_picks')}"
    )

    # Control 3 — macro_blocked_picks non-empty in both runs (proves the gate
    # fired and that equality assertion is not vacuous [] == []).
    assert result_live.get("macro_blocked_picks"), (
        f"negative control: macro_blocked_picks must be non-empty "
        f"('BLOCKED'/Financials must be macro-blocked by Employment); "
        f"got {result_live.get('macro_blocked_picks')!r}"
    )
    assert result_expired.get("macro_blocked_picks"), (
        f"negative control: macro_blocked_picks must be non-empty in the expired run too"
    )

    # Compare the ENTIRE return dict, not two hand-picked keys. reference_shelf
    # claims this value "cannot cause that function to suppress or un-suppress
    # any pick" — asserting on two keys is narrower than that claim and would
    # miss a mutation that read coverage and moved add_positions, deploy_note,
    # risk_banner, or a sizing field inside a pick dict. Verified: across these
    # two runs the ONLY key that differs is macro_coverage_expired itself.
    _ignore = {"macro_coverage_expired"}
    assert (
        {k: v for k, v in result_live.items() if k not in _ignore}
        == {k: v for k, v in result_expired.items() if k not in _ignore}
    ), (
        "NOTHING in the brief may differ between an expired and a healthy macro "
        "calendar except the awareness key itself — the value is "
        "disclosure-only and must never reach a suppression decision"
    )

    assert result_live.get("new_picks") == result_expired.get("new_picks"), (
        "new_picks must be identical regardless of coverage status — "
        "the coverage key is awareness-only and must never gate picks"
    )
    assert result_live.get("macro_blocked_picks") == result_expired.get("macro_blocked_picks"), (
        "macro_blocked_picks must be identical — coverage state must not affect gate"
    )


# ── Test 4b: discovery-sourced mover reaches the macro gate via TICKER_SECTORS ─

def test_discovery_mover_in_curated_sector_is_macro_blocked_not_new_pick():
    """ARM is Movers-sourced (discovery_universe.DISCOVERY_UNIVERSE
    ["Semiconductors"]) and was, until 2026-09-01, absent from
    portfolio.TICKER_SECTORS — resolve_sector() fell back to the raw provider
    GICS string ("Technology" here), unknown to _SECTOR_IMPACT, so an
    Inflation HIGH event blocking Semiconductors (severity 3) could never
    suppress it. This proves the fixed reachability: even though the movers
    row still carries the raw provider sector string, the curated
    TICKER_SECTORS entry wins and ARM lands in macro_blocked_picks, not
    new_picks.
    """
    today = date(2026, 9, 1)
    macro_events = [{
        "date": "2026-09-01", "event": "CPI Inflation",
        "category": "Inflation", "impact": "HIGH",
    }]
    movers = [{
        "ticker": "ARM", "price": 150.0, "sector": "Technology",
        "trend": "Up", "scanner_signal": "Buy", "score": COMPOSITE_BUY + 10,
        "rsi": 60.0, "mom_1m": 5.0, "mom_3m": 8.0,
        "composite_score": COMPOSITE_BUY + 10, "day_change": 3.0,
    }]
    composites = {"ARM": {
        "total": COMPOSITE_BUY + 10, "rec": {"label": "Strong Buy"},
        "stale_as_of": None, "fund_cache_age_days": None,
        "fundamentals_available": True, "val_available": True,
    }}

    result = _grow_today(**_minimal_grow_today_kwargs(
        movers=movers,
        composites=composites,
        macro_events=macro_events,
        today=today,
    ))

    blocked_tickers   = {p.get("ticker") for p in (result.get("macro_blocked_picks") or [])}
    new_pick_tickers  = {p.get("ticker") for p in (result.get("new_picks") or [])}
    assert "ARM" in blocked_tickers, (
        f"ARM must be macro-blocked (Semiconductors, Inflation severity 3 >= 2); "
        f"got macro_blocked={result.get('macro_blocked_picks')}, "
        f"new_picks={result.get('new_picks')}"
    )
    assert "ARM" not in new_pick_tickers, (
        "ARM must not reach new_picks while its sector is macro-blocked")


# ── Test 5: bear early-return carries the key ─────────────────────────────────

def test_bear_return_carries_macro_coverage_expired_key():
    """On a bear day _grow_today returns early.  The return dict must still
    carry 'macro_coverage_expired'.  The render condition
    `macro_expired and new_picks` is provably False because new_picks == [].
    """
    result = _grow_today(**_minimal_grow_today_kwargs(
        market_context={"tone": "bear", "sp500_pct": -1.5, "nasdaq_pct": -1.8,
                        "leading_sectors": []}
    ))
    assert result.get("tone") == "bear"
    assert "macro_coverage_expired" in result, (
        "bear early-return must include macro_coverage_expired key for the render condition"
    )
    assert result.get("new_picks") == [], "bear day must have empty new_picks"
    # Confirm the render condition is provably False
    expired = result.get("macro_coverage_expired")
    new_picks = result.get("new_picks")
    assert not (expired and new_picks), (
        "render condition `macro_expired and new_picks` must be False on bear day"
    )


# ── Test 6: cadence derivation on synthetic _STATIC ──────────────────────────

def _make_monthly_series(last_year: int, last_month: int, count: int = 6) -> list[tuple]:
    """Build a synthetic monthly series ending at (last_year, last_month)."""
    dates = []
    y, m = last_year, last_month
    for _ in range(count):
        dates.append(f"{y:04d}-{m:02d}-15")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    dates.reverse()
    return _series("Monthly Release", dates, category="Employment")


def _make_quarterly_series(base_year: int, count: int = 4) -> list[tuple]:
    """Build a synthetic quarterly series (Jan/Apr/Jul/Oct) for base_year."""
    months = [1, 4, 7, 10]
    dates = [f"{base_year:04d}-{m:02d}-15" for m in months[:count]]
    return _series("Quarterly Release", dates, category="Growth")


def test_monthly_series_not_overdue_at_20_days(monkeypatch):
    """A monthly series (max_gap ~30d) should NOT be overdue 20 days after its
    last date — expected_by = last + 30d ~ +30d, today is only +20d."""
    last = date(2026, 7, 15)
    today = last + timedelta(days=20)
    fake_static = _make_monthly_series(2026, 7)
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", fake_static)
    result = rs.expired_macro_series(today)
    assert result is not None
    entry = next((e for e in result if e["name"] == "Monthly Release"), None)
    assert entry is not None, "series must appear (last < today)"
    assert entry["is_overdue"] is False, (
        "at +20d a monthly series (30d cadence) should not yet be overdue"
    )


def test_monthly_series_overdue_at_40_days(monkeypatch):
    """A monthly series should be overdue 40 days after its last date
    (expected_by ~= last + 30d; 40d > 30d)."""
    last = date(2026, 7, 15)
    today = last + timedelta(days=40)
    fake_static = _make_monthly_series(2026, 7)
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", fake_static)
    result = rs.expired_macro_series(today)
    assert result is not None
    entry = next((e for e in result if e["name"] == "Monthly Release"), None)
    assert entry is not None
    assert entry["is_overdue"] is True, (
        "at +40d a monthly series (30d cadence) must be overdue"
    )


def test_quarterly_series_not_overdue_at_60_days(monkeypatch):
    """A quarterly series (max_gap ~92d) should NOT be overdue at +60d."""
    last = date(2026, 10, 15)
    today = last + timedelta(days=60)
    fake_static = _make_quarterly_series(2026)
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", fake_static)
    result = rs.expired_macro_series(today)
    assert result is not None
    entry = next((e for e in result if e["name"] == "Quarterly Release"), None)
    assert entry is not None, "series must appear (last < today)"
    assert entry["is_overdue"] is False, (
        "at +60d a quarterly series (~92d cadence) should not yet be overdue"
    )


def test_quarterly_series_overdue_at_100_days(monkeypatch):
    """A quarterly series should be overdue at +100d (expected_by ~= last + 92d)."""
    last = date(2026, 10, 15)
    today = last + timedelta(days=100)
    fake_static = _make_quarterly_series(2026)
    monkeypatch.setattr("stock_analyzer.macro_calendar._STATIC", fake_static)
    result = rs.expired_macro_series(today)
    assert result is not None
    entry = next((e for e in result if e["name"] == "Quarterly Release"), None)
    assert entry is not None
    assert entry["is_overdue"] is True, (
        "at +100d a quarterly series (~92d cadence) must be overdue"
    )


# ── Test 7: _macro_coverage_banner contract ──────────────────────────────────

def test_banner_returns_empty_for_none():
    assert _macro_coverage_banner(None) == "", \
        "None means could not verify — must be silent (same as _book_drift_banner)"


def test_banner_returns_empty_for_empty_list():
    assert _macro_coverage_banner([]) == "", \
        "[] means verified, nothing expired — must be silent"


def test_banner_returns_empty_when_nothing_is_overdue():
    expired_but_not_overdue = [
        {"name": "GDP Advance Estimate", "category": "Growth",
         "last_date": "2026-10-29",
         "expected_by": "2027-01-29", "is_overdue": False},
    ]
    assert _macro_coverage_banner(expired_but_not_overdue) == "", \
        "non-empty list but no is_overdue entries must still be silent"


def test_banner_returns_non_empty_when_overdue():
    overdue = [
        {"name": "GDP Advance Estimate", "category": "Growth",
         "last_date": "2026-10-29",
         "expected_by": "2027-01-29", "is_overdue": True},
    ]
    result = _macro_coverage_banner(overdue)
    assert result != "", "an overdue entry must produce a non-empty HTML banner"
    assert "GDP Advance Estimate" in result


def test_banner_mixed_list_fires_only_on_overdue_entries():
    mixed = [
        {"name": "CPI Inflation", "category": "Prices",
         "last_date": "2026-11-12",
         "expected_by": "2026-12-12", "is_overdue": False},
        {"name": "GDP Advance Estimate", "category": "Growth",
         "last_date": "2026-10-29",
         "expected_by": "2027-01-29", "is_overdue": True},
    ]
    result = _macro_coverage_banner(mixed)
    assert result != "", "at least one overdue entry means banner must render"
    assert "GDP Advance Estimate" in result
