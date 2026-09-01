"""
Shelf life for manually-maintained STATIC reference tables.

AWARENESS ONLY. Nothing here gates a recommendation, suppresses a pick, or
changes a score. It answers exactly one question — *which hand-maintained table
is overdue for a human refresh?* — and reports it on the owner-only 🩺 System
Trust page. It never raises: a proprioception layer that can crash the page it
reports on is worse than none.

One note about the G-07 macro-event gate: `daily_briefing._grow_today` imports
`macro_calendar` directly to suppress picks in sectors with imminent HIGH-impact
events.  The `expired_macro_series` function below only annotates that gate's
blind-spots for the UI; it is not read by `_grow_today` and cannot cause that
function to suppress or un-suppress any pick.  The gate always uses its own live
read of `macro_calendar._STATIC`; a failure in this module can never remove a
suppression there.

Why this exists (2026-08-15 audit): the app carries several curated tables that
drift SILENTLY. `SECTOR_UNIVERSE` — the ~70 names Grow Today scans every single
day — had not been refreshed since 2026-05-05 and carried no date at all, so
nothing could have told you. A stale net doesn't error; it just quietly stops
catching things.

Two staleness mechanics, because the tables fail in two different ways:

  KIND_AS_OF    A membership/value table with a last-refreshed date. Stale when
                its age exceeds its shelf life. Fails as SILENT ABSENCE (a stale
                universe stops surfacing current leaders) or as a mildly WRONG
                NUMBER (stale benchmark weights).

  KIND_HORIZON  A forward-dated table that RUNS OUT. Stale when the runway to
                its last covered date drops below a minimum. The horizon is
                always DERIVED from the table itself, never hand-written, so
                extending the table automatically clears the warning — there is
                no second thing to remember to update, and the registry can
                never disagree with the data it describes.

Registered here rather than as per-table constants on purpose: the failure being
fixed IS "a table exists with no date at all", and per-table opt-in reproduces
exactly that. A central registry makes an omission visible in one file. Each
table site carries a one-line pointer comment back here so an editor still sees
the obligation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

KIND_AS_OF = "as_of"
KIND_HORIZON = "horizon"


@dataclass(frozen=True)
class _RefTable:
    key: str            # joins to constants.REFERENCE_SHELF_LIFE_DAYS / _HORIZON_MIN_DAYS
    label: str          # plain business language for the UI
    location: str       # where the table actually lives
    kind: str
    consequence: str    # what goes WRONG when stale — never surface a bare number
    as_of: date | None = None                             # KIND_AS_OF only
    # KIND_HORIZON only. Returns either a bare date, or (date, limiting_series)
    # when the table is composed of independently-extended series and naming the
    # limiter makes the chore actionable.
    horizon_fn: Callable[[], "date | tuple[date, str] | None"] | None = None


# ── Derived horizons ─────────────────────────────────────────────────────────

# A recurring backbone series has many rows (currently 8-24 each). A one-off
# entry — a Jackson Hole date, a debt-ceiling X-date, a single special FOMC —
# would otherwise become its own one-row "series" whose max is in the past,
# dragging the min() below and pinning the macro row to a permanent false RED.
# Only recurring series define coverage, so require at least this many rows.
_MACRO_MIN_SERIES_ROWS = 4


def _macro_series_index() -> "dict[str, dict]":
    """Parse _STATIC once and return per-series metadata.

    Returns ``{event_name: {"last": date, "count": int, "category": str,
    "max_gap": int}}`` where ``max_gap`` is the largest gap in DAYS between
    consecutive rows of that same series.

    Callers that need different views of the same table (horizon, expiry list)
    should call this once and derive their view from the result — two
    independent parsers of the same table will drift.  Raises on import/parse
    failure so callers can handle it in one try/except.
    """
    from stock_analyzer.macro_calendar import _STATIC  # may raise

    rows_per_event: dict[str, list[date]] = {}
    category_per_event: dict[str, str] = {}
    for row in _STATIC or []:
        try:
            when     = date.fromisoformat(str(row[0])[:10])
            event    = str(row[2])
            category = str(row[3]) if len(row) > 3 else ""
        except Exception:
            continue
        rows_per_event.setdefault(event, []).append(when)
        if event not in category_per_event:
            category_per_event[event] = category

    index: dict[str, dict] = {}
    for event, dates in rows_per_event.items():
        count = len(dates)
        last  = max(dates)
        sorted_dates = sorted(dates)
        max_gap = 0
        for i in range(1, len(sorted_dates)):
            gap = (sorted_dates[i] - sorted_dates[i - 1]).days
            if gap > max_gap:
                max_gap = gap
        index[event] = {
            "last":     last,
            "count":    count,
            "category": category_per_event.get(event, ""),
            "max_gap":  max_gap,
        }
    return index


def _macro_static_horizon() -> "tuple[date, str] | None":
    """Earliest point at which the macro calendar starts losing coverage, plus
    the name of the series that runs out first.

    Deliberately the MINIMUM over per-series maxima, not the global max. The
    series are extended independently (FOMC dates come from the Fed, CPI/PPI/
    payrolls from BLS, GDP from BEA, retail sales from Census — different
    publishers on different schedules), so a global max would let one
    freshly-extended series mask five expiring ones. That is not hypothetical:
    on 2026-08-15 the FOMC series was extended through 2027-12-08 while CPI,
    payrolls, PPI, retail sales and GDP all still ended in Oct-Dec 2026. A
    global max would have read "green" with five of six series about to die on
    a decision path.

    Returns the series NAME alongside the date so the UI can say *which* chore
    is outstanding — "the whole macro calendar is dead" would be false and
    unactionable when only GDP has run out.
    """
    try:
        index = _macro_series_index()
    except Exception:
        return None
    recurring = {e: meta["last"] for e, meta in index.items()
                 if meta["count"] >= _MACRO_MIN_SERIES_ROWS}
    if not recurring:
        return None
    event = min(recurring, key=lambda e: recurring[e])
    return recurring[event], event


def expired_macro_series(today: "date | None" = None) -> "list[dict] | None":
    """Return entries for backbone macro series whose last date is in the past.

    Three possible return values:
      ``None``  — _STATIC could not be read (import/parse failure).  Do NOT
                  treat this as "nothing expired" — the distinction between
                  "checked, nothing expired" and "could not check" is the
                  entire point of this function.
      ``[]``    — _STATIC was read successfully and no recurring series has
                  expired (all last dates are >= today).
      list      — one dict per expired recurring series, sorted by last_date.

    Each entry: ``{"name", "category", "last_date" (ISO str),
    "expected_by" (ISO str), "is_overdue" (bool)}``.

    ``expected_by`` is derived purely from the series' own cadence
    (``last + max_gap``).  ``is_overdue = today > expected_by``.  No new
    constants and no constants.py changes — the module docstring principle
    ("always DERIVED from the table itself") applies here too.

    ``max_gap`` is the MAXIMUM observed gap between consecutive rows, so
    ``is_overdue`` is the most lenient possible call — it only fires once today
    is past the LARGEST interval ever seen for that series.  This errs toward
    silence over false alarms: a series that occasionally slips by an extra
    week won't trigger until it has exceeded even that widest gap.

    Only recurring series (>= _MACRO_MIN_SERIES_ROWS rows) are included, so a
    one-off Jackson Hole or debt-ceiling date is never reported.

    Uses ``_today()`` (ET-aware via ``market_time.today_et``) as the default —
    never ``date.today()``.
    """
    day = today if today is not None else _today()
    try:
        index = _macro_series_index()
    except Exception:
        return None

    out: list[dict] = []
    for event, meta in index.items():
        if meta["count"] < _MACRO_MIN_SERIES_ROWS:
            continue
        last: date = meta["last"]
        if last >= day:
            continue  # still current
        expected_by = last + timedelta(days=meta["max_gap"])
        out.append({
            "name":        event,
            "category":    meta["category"],
            "last_date":   last.isoformat(),
            "expected_by": expected_by.isoformat(),
            "is_overdue":  day > expected_by,
        })
    out.sort(key=lambda e: e["last_date"])
    return out


def _nyse_calendar_horizon() -> date | None:
    """Last day the hardcoded NYSE holiday calendar covers. Derived from
    MARKET_CALENDAR_LAST_YEAR so extending the calendar clears this by itself."""
    try:
        from stock_analyzer.constants import MARKET_CALENDAR_LAST_YEAR
        return date(int(MARKET_CALENDAR_LAST_YEAR), 12, 31)
    except Exception:
        return None


# ── The registry ─────────────────────────────────────────────────────────────
# `as_of` dates are the HONEST last-refresh dates from git history, NOT the date
# this registry was created. Stamping "today" on a table last touched in May
# would bake in a false claim and delay the first true warning by months.
#
# The rule applied, stated so nobody "corrects" these upward and silently resets
# the clock: as_of = the last DELIBERATE CURATION of the membership. Mechanical
# ticker renames don't count — `sector_universe` and `discovery_universe` were
# both touched on 2026-06-26 by a SQ→XYZ rename (a0ef644), and `sector_universe`
# on 2026-05-10 by SNOW→WDAY (5ac4211), but neither reconsidered which names
# belong. Dating from a rename would claim a refresh that never happened.
#
# Clarified 2026-08-16: a documented review that concludes "NO CHANGE NEEDED"
# DOES earn a new as_of. The date records that a human deliberately reconsidered
# the membership, not that the membership changed — and any other reading makes
# CHURNING A ROSTER the only way to clear the warning, which would actively
# degrade the very tables this exists to protect. The bar is evidence, not
# outcome: a commit bumping a date with no membership diff must say what was
# checked and what the conclusion was (e.g. "all 88 tickers resolved in the
# weekly liveness sweep; coverage re-reviewed against the absent large-caps; no
# change warranted"). Since the ticker_liveness sweep shipped, that evidence has
# a machine-checked component rather than resting on an assertion.
_REFERENCE_TABLES: tuple[_RefTable, ...] = (
    _RefTable(
        key="sector_universe",
        label="Grow Today scan universe",
        location="stock_analyzer/scanner.py — SECTOR_UNIVERSE",
        kind=KIND_AS_OF,
        # 2026-08-16: full curation — all 73 verified alive, 15 large-caps added
        # to close a 53%-tech skew, 2 new buckets (Industrials, Communications).
        as_of=date(2026, 8, 16),
        consequence="the daily buy-candidate net may be missing current market "
                    "leaders — it fails by silently not surfacing names, never by erroring",
    ),
    _RefTable(
        key="discovery_universe",
        label="Movers discovery universe",
        location="stock_analyzer/discovery_universe.py — DISCOVERY_UNIVERSE",
        kind=KIND_AS_OF,
        # 2026-09-01: full market-cap/liquidity sweep of the whole universe
        # via scripts/roster_coverage_report.py --roster discovery --caps —
        # no delistings found. 4 removals / 6 additions across 3 of 4 buckets
        # on market-cap/liquidity/sector-coverage grounds (AI, LCID out;
        # IBM, CSCO, EBAY, WBD, KR, ORLY in); Mega-cap Tech reviewed and
        # left unchanged.
        as_of=date(2026, 9, 1),
        consequence="the wider breakout net narrows — a genuine mover in an "
                    "untracked name stays invisible",
    ),
    _RefTable(
        key="sp500_sector_weights",
        label="S&P 500 benchmark sector weights",
        location="stock_analyzer/portfolio.py — SP500_SECTOR_WEIGHTS",
        kind=KIND_AS_OF,
        as_of=date(2026, 7, 1),
        consequence="the Portfolio-vs-S&P 500 sector tilt shows a wrong number "
                    "(this one misleads rather than merely omitting)",
    ),
    _RefTable(
        key="sector_candidates",
        label="Diversification candidate roster",
        location="stock_analyzer/portfolio.py — _SECTOR_CANDIDATES",
        kind=KIND_AS_OF,
        # 2026-08-17: full re-seed. All 56 names were alive, so this was a
        # FITNESS refresh, not rot — three sectors were seeding a de-risking
        # suggestion with sub-scale/speculative names.
        as_of=date(2026, 8, 17),
        consequence="diversification ADD suggestions may name delisted or "
                    "acquired tickers",
    ),
    _RefTable(
        key="macro_event_calendar",
        label="Macro event calendar backbone",
        location="stock_analyzer/macro_calendar.py — _STATIC",
        kind=KIND_HORIZON,
        horizon_fn=_macro_static_horizon,
        consequence="macro Act-Today items and macro-affected trims stop firing "
                    "once a series runs out — the FRED layer only enriches these "
                    "rows, it never creates them",
    ),
    _RefTable(
        key="nyse_calendar",
        label="NYSE holiday calendar",
        location="stock_analyzer/constants.py — NYSE_HOLIDAYS / MARKET_CALENDAR_LAST_YEAR",
        kind=KIND_HORIZON,
        horizon_fn=_nyse_calendar_horizon,
        consequence="past its last year the app treats unlisted holidays as open "
                    "trading days (the existing calendar_stale flag only warns "
                    "AFTER that point — this gives runway before it)",
    ),
)


# ── Public API ───────────────────────────────────────────────────────────────

# App Settings (docs/plans/app-settings.md) Commit 1 of 3 — the three tables
# now backed by stock_analyzer/db.py's reference_tables. NOT the other three
# (sp500_sector_weights, macro_event_calendar, nyse_calendar), which stay
# pure code-date registrations for now. Grading tries the DB row's as_of
# first and falls back to the hardcoded date below when the DB has no row
# for the name yet (pre-DDL, offline, or not-yet-seeded) — an un-migrated
# read keeps telling the truth exactly as it does today. Nothing in this
# commit reads or writes the underlying rosters themselves (SECTOR_UNIVERSE
# etc.) via the DB — that's Commit 2/3; this only changes which as_of date
# check ⑤ reports once a table has actually been edited through the app.
_DB_BACKED_KEYS = {"sector_universe", "discovery_universe", "sector_candidates"}


def _today() -> date:
    from stock_analyzer.market_time import today_et
    return today_et()


def _db_as_of(key: str) -> "date | None":
    """Current `as_of` from the DB-backed `reference_tables` row for `key`,
    or `None` if there is no row yet (pre-DDL, DB offline, or never seeded).
    Never raises — a broken/absent DB read here must degrade to the
    hardcoded fallback, never crash the shelf-life check that reports on it.
    """
    try:
        from stock_analyzer import db
        row = db.load_reference_table(key)
    except Exception:
        return None
    if not row:
        return None
    as_of_raw = row.get("as_of")
    if as_of_raw is None:
        return None
    if isinstance(as_of_raw, date):
        return as_of_raw
    try:
        return date.fromisoformat(str(as_of_raw)[:10])
    except Exception:
        return None


def _grade_as_of(entry: _RefTable, today: date) -> tuple[str, str]:
    from stock_analyzer.constants import REFERENCE_SHELF_LIFE_DAYS
    shelf = REFERENCE_SHELF_LIFE_DAYS.get(entry.key)
    as_of = entry.as_of
    if entry.key in _DB_BACKED_KEYS:
        as_of = _db_as_of(entry.key) or entry.as_of
    if as_of is None or shelf is None:
        return "unknown", "no recorded refresh date or shelf life"
    age = (today - as_of).days
    detail = f"last refreshed {as_of.isoformat()} — {age}d ago (refresh every {shelf}d)"
    # Boundary is INCLUSIVE of the shelf life: age == shelf is still ok, and the
    # warning starts the day after. Asserted exactly in tests rather than
    # reasoned about — the 2026-08-04 Critical was an off-by-one of this shape.
    return ("ok" if age <= shelf else "warn"), detail


def _grade_horizon(entry: _RefTable, today: date) -> tuple[str, str]:
    from stock_analyzer.constants import REFERENCE_HORIZON_MIN_DAYS
    min_days = REFERENCE_HORIZON_MIN_DAYS.get(entry.key)
    try:
        result = entry.horizon_fn() if entry.horizon_fn else None
    except Exception:
        result = None
    # horizon_fn may name the limiting series (see _macro_static_horizon).
    if isinstance(result, tuple):
        horizon, limiter = result
    else:
        horizon, limiter = result, None
    if horizon is None or min_days is None:
        return "unknown", "could not determine how far this table extends"

    runway = (horizon - today).days
    # Naming the limiter keeps the chore actionable: "the macro calendar
    # EXPIRED" reads as though the whole table is dead, when in practice five
    # of six series may still run for months and the actual job is to extend
    # one of them.
    via = f" — earliest series to run out: {limiter}" if limiter else ""
    if runway < 0:
        return "down", f"EXPIRED {horizon.isoformat()} — {abs(runway)}d ago; extend it now{via}"
    detail = (f"covered through {horizon.isoformat()} — {runway}d of runway "
              f"(want >={min_days}d){via}")
    return ("ok" if runway >= min_days else "warn"), detail


def shelf_status(today: date | None = None) -> list[dict]:
    """One row per registered reference table.

    Pure — no I/O, no network, no DB. Returns a list (never `None`): this is not
    a provider that can be "offline", so the offline-sentinel contract applies
    PER ROW instead — an entry whose source can't be read is graded `"unknown"`
    and still rendered, never dropped and never silently `"ok"`. Silence is not
    health.

    Never raises. A broken registry entry degrades that one row.
    """
    try:
        day = today or _today()
    except Exception:
        return []

    out: list[dict] = []
    for entry in _REFERENCE_TABLES:
        try:
            if entry.kind == KIND_HORIZON:
                severity, detail = _grade_horizon(entry, day)
            else:
                severity, detail = _grade_as_of(entry, day)
        except Exception as exc:
            severity, detail = "unknown", f"shelf-life check failed ({str(exc)[:80]})"
        out.append({
            "key":         entry.key,
            "label":       entry.label,
            "location":    entry.location,
            "kind":        entry.kind,
            "severity":    severity,
            "detail":      detail,
            "consequence": entry.consequence,
        })
    return out
