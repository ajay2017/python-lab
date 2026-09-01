"""System proprioception — pipeline-trust health checks (Phase 1).

OWNER-ONLY DIAGNOSTIC. Every function here is READ-ONLY and INFORMING: it
reports the operational state of the data pipeline and changes NO gate, NO
recommendation, NO composite, NO threshold. It answers one question — *can I
trust what the app told me today?* — and nothing more.

Pull-based / render-time: nothing here depends on its own background job having
run (the "who watches the watcher" constraint from
docs/plans/system-proprioception.md). Every value is read live at call time from
a store that already exists.

Six checks:
  ① cron liveness      — reads `cron_heartbeat` (written by each cron_runner lane)
  ② data-store health  — existence (a missing table = the DDL-catcher) + freshness
  ③ provider health    — reads `api_health` (session-scoped provider call stats)
  ④ in-session caches   — which session_state producer caches populated this run
  ⑤ reference shelf life — is any hand-maintained reference table overdue for refresh
  ⑥ write outcomes      — did today's interactive ledger writes actually save

Severity vocabulary:
  "ok"      (🟢) — healthy / fresh.
  "warn"    (🟡) — degraded but the app still has the input (stale row, provider
                   on a backup, weekly lane a touch late, or a lane's last-good
                   heartbeat predates its expected fire — possibly a write that
                   silently failed during a total DB outage). Amber, never
                   suppresses.
  "down"    (🔴) — the input is provably GONE: a missing data store (the DDL bug),
                   a lane that ran and FAILED, or a provider still actively
                   erroring (its most recent call did NOT succeed — a resolved
                   burst re-grades to "warn", see check_providers()). The classes
                   that mean the app may be deciding blind.
  "unknown" (⚪) — not observed yet this session (no provider call made, a lane
                   with no heartbeat row yet, a page not visited). NEVER counted
                   as degraded — silence is not failure.

Never raises. A proprioception layer that can crash the page it reports on is
worse than none, so every check swallows its own errors and returns a structured
result. The recency windows below are OBSERVABILITY thresholds (how stale is
"stale"), not investment-policy boundaries — deliberately local to this module
rather than in constants.py, which is reserved for values that move a decision.

DELIBERATE EXCEPTION (F-238, 2026-08-15): check ⑤'s reference-data shelf lives
live in `constants.py` (`REFERENCE_SHELF_LIFE_DAYS` / `REFERENCE_HORIZON_MIN_DAYS`)
rather than here, even though they are observability values by the rule above.
Two reasons the user accepted when shown the conflict: Hard Rule #1 is stated
without an observability carve-out, and — the deciding one — those values are
keyed to a registry in `stock_analyzer/reference_shelf.py`, so keeping keys and
values in one visible place is what lets a test assert that no table can be
registered without a shelf life. Don't "fix" one convention to match the other.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

# ── Recency windows (observability, not investment policy) ────────────────────
_DAILY_LANE_OK_HOURS   = 30      # a daily lane seen within 30h is healthy
_DAILY_LANE_WARN_DAYS  = 4       # 30h..4d = amber; older = red
_WEEKLY_LANE_OK_DAYS   = 8       # a weekly lane seen within 8d is healthy
_WEEKLY_LANE_WARN_DAYS = 16      # 8..16d = amber; older = red

# Substrings that mark a PostgREST/Postgres "table does not exist" — the
# DDL-catcher. Matched case-insensitively against the caught exception text.
_MISSING_TABLE_MARKERS = (
    "could not find the table",   # PostgREST PGRST205 schema-cache miss
    "does not exist",             # Postgres 42P01 "relation ... does not exist"
    "pgrst205",
    "42p01",
)


# ── The inventory: which cron lane writes which data store, and how ───────────
# Single source of truth for check ②. `unconditional=True` means the lane writes
# this store on EVERY trading period (its absence today, once due, is a real
# problem); `unconditional=False` means the write is conditional (e.g.
# exit_signals only when deterioration exists) so absence is legitimate and we
# check EXISTENCE only, never freshness. `date_col`/`expected_hour_et` are set
# ONLY for the unconditional dailies whose column names are confirmed in code —
# every other store is existence-only, so a column-name assumption can never
# misreport it.
@dataclass(frozen=True)
class _Store:
    table: str
    label: str            # plain business language (headline)
    lane: str
    cadence: str          # "daily" | "weekly" | "monthly"
    unconditional: bool
    date_col: str | None = None
    expected_hour_et: int | None = None


_INVENTORY: tuple[_Store, ...] = (
    # Unconditional dailies — existence AND freshness (confirmed date columns).
    # `expected_hour_et` is the wall-clock ET hour AFTER which today's row is
    # expected to exist. It MUST clear the lane's actual fire+write time WITH
    # margin, or the check false-ambers every trading day during the owner's
    # active window. The Railway crons fire at FIXED UTC times chosen for EST
    # (see memory project_cron_railway_migration), so in EDT they land ~1h
    # later: cron-scan 14:45 UTC = 09:45 EST / 10:45 EDT; cron-eod 21:30 UTC =
    # 16:30 EST / 17:30 EDT. These hours carry the same "lower-bound only, ~1h
    # later in EDT is harmless" slack the migration's own gates use — expecting
    # a row a couple hours after the latest possible write, never before.
    # NOTE: model_predictions.made_at is a UTC timestamptz (parsed [:10]),
    # whereas regime_date/snapshot_date are ET logical dates — near-midnight ET
    # the UTC date can be a day AHEAD, but only ever in the safe (>=) direction,
    # so it can never false-stale. model_predictions is unconditional=True on
    # the assumption the vol-forecast lane writes ≥1 row every eod; a rare
    # no-holdings day would amber (informing-only, acceptable).
    _Store("daily_regime",      "Market regime read",             "eod",  "daily", True,  "regime_date",   19),
    _Store("daily_snapshots",   "Daily P&L snapshot",             "eod",  "daily", True,  "snapshot_date", 19),
    _Store("model_predictions", "Volatility forecast (Model Lab)","eod",  "daily", True,  "made_at",       19),
    _Store("scanner_cache",     "Market scan cache",              "scan", "daily", True,  "scan_date",     12),
    # Conditional writes — EXISTENCE only (the DDL-catcher); absence is legitimate.
    _Store("exit_signals",              "Protective exit scan",          "premarket", "daily",   False),
    _Store("analyst_target_snapshots",  "Analyst price-target history",  "premarket", "daily",   False),
    _Store("recommendations",           "Buy recommendations log",       "scan",      "daily",   False),
    _Store("sentiment_history",         "Sentiment snapshot",            "eod",       "daily",   False),
    _Store("weekly_debriefs",           "Weekly debrief",                "thesis",    "weekly",  False),
    _Store("monthly_reports",           "Monthly intelligence report",   "thesis",    "monthly", False),
    _Store("thesis_reviews",            "AI thesis reviews",             "thesis",    "weekly",  False),
    # Existence-only on purpose. Its DDL is applied BY HAND, so until that
    # happens the broker lane logs a skip and 🏠 Home's drift banner silently
    # says "not checked" forever — a feature that looks fine while doing
    # nothing. Registering it here is what makes that state visible.
    _Store("broker_position_snapshot",  "Broker position snapshot",      "broker",    "daily",   False),
    # 2026-08-30 data-integrity audit: 7 tables that cron/the app actively
    # write to had ZERO visibility here — any of them could silently stop
    # being written with no signal anywhere in the app, not even "unknown".
    # All existence-only, same rationale as the block above.
    _Store("gate_suppressions",          "Gate suppression ledger",                   "scan",   "daily", False),
    _Store("account_cash",               "Account cash balance",                      "broker", "daily", False),
    _Store("account_flows",              "Account deposits/withdrawals",              "broker", "daily", False),
    _Store("snaptrade_pending_imports",  "Broker transactions pending confirmation",  "broker", "daily", False),
    _Store("snaptrade_income_events",    "Broker dividend/interest/fee events",       "broker", "daily", False),
    _Store("snaptrade_config",           "Broker connection config",                  "broker", "daily", False),
    # judgment_grades ("The Judge", F-227's grading harness) has NO cron lane —
    # it is written ONLY via a manual "▶ Run grading" button in the app, same
    # category as analyst_coverage's primary write path or thesis_erosion_cache.
    # `lane` is a pure display/grouping label here (see `_probe_store` and
    # `check_data_stores` below): it is never looked up against `_LANES` or
    # `by_lane` (those only exist inside check_cron_liveness, keyed off
    # cron_heartbeat rows, a completely separate structure), so an
    # unrecognized value cannot break or silently no-op anything — "interactive"
    # is simply the honest label, not a real lane. Confirmed MISSING from
    # production entirely as of 2026-08-30 (`relation "judgment_grades" does
    # not exist`) — registering it here is what finally makes that visible.
    _Store("judgment_grades",            "Judge grading harness",                     "interactive", "daily", False),
)


# ── The cron lanes (check ①) ──────────────────────────────────────────────────
@dataclass(frozen=True)
class _Lane:
    key: str
    label: str
    kind: str   # "daily" | "weekly"
    # Expected-fire tightening (2026-08-21, closes the "row survives a total DB
    # outage" gap — see the block comment below the table). Empty tuple = don't
    # grade this lane's freshness beyond the existing age-window logic above;
    # any non-empty tuple opts the lane into the "row exists but predates the
    # expected fire" check inside check_cron_liveness().
    fire_hours_et: tuple[int, ...] = ()
    fire_weekday: int | None = None   # date.weekday(): Mon=0..Sun=6; weekly lanes only


# `fire_hours_et` / `fire_weekday` values below are ET-native and ALREADY carry
# margin past each lane's actual Railway-dashboard cron fire+write time (same
# "expect a row a couple hours after the latest possible write, never before"
# posture as the `expected_hour_et` column in `_INVENTORY` above). Railway Cron
# Job schedules are dashboard-managed, NOT repo-managed (see the ⚠️ note in
# docs/architecture.md's cron table and memory `project_cron_railway_migration`)
# — these hours are a DUPLICATE of that real source of truth, confirmed against
# the live Railway dashboard by the user on 2026-08-21, and must be updated by
# hand if a schedule is ever changed there. They deliberately do NOT need a
# per-lane DST adjustment: because they are ET-native (not fixed-UTC, unlike the
# Railway cron expressions themselves) and already margined, the ~1h seasonal
# drift a fixed-UTC cron shows across EST/EDT is absorbed by the same margin
# automatically — nothing here needs touching twice a year.
#
#   lane key      | fire_hours_et | fire_weekday
#   premarket     | (10,)         | —
#   scan          | (12,)         | —
#   intraday      | (13,)         | —
#   eod           | (19,)         | —
#   thesis        | (20,)         | 6 (Sunday)
#   maintenance   | (10,)         | 5 (Saturday)
#   broker        | (12, 18)      | —
_LANES: tuple[_Lane, ...] = (
    _Lane("premarket",   "Pre-market protective scan", "daily",  fire_hours_et=(10,)),
    _Lane("scan",        "Morning market scan",        "daily",  fire_hours_et=(12,)),
    _Lane("intraday",    "Intraday pullback check",    "daily",  fire_hours_et=(13,)),
    _Lane("eod",         "End-of-day snapshot",        "daily",  fire_hours_et=(19,)),
    _Lane("thesis",      "Weekly thesis & debrief",    "weekly", fire_hours_et=(20,), fire_weekday=6),
    # Saturday housekeeping (cron_runner._run_maintenance): idempotent data
    # backfills. Registered here so a silent death is visible on 🩺 System
    # Trust — a lane that writes heartbeats nobody grades is a lane that can
    # stop firing unnoticed, which defeats the dead-man's-switch.
    _Lane("maintenance", "Weekly data backfills",      "weekly", fire_hours_et=(10,), fire_weekday=5),
    # SnapTrade broker sync (cron_runner._run_broker, F-244). "daily" matches
    # SNAPTRADE_BALANCE_STALE_HOURS=25's implicit cadence assumption. Same
    # registration rationale as `maintenance` above. Two distinct pre-setup
    # states, both safe: before the `broker` Railway cron service is even
    # scheduled, no row ever exists and this reads "unknown" (⚪); once it IS
    # scheduled but SnapTrade credentials aren't configured yet, _run_broker
    # returns 0 and the dispatcher still writes a fresh status="ok" heartbeat
    # every fire (the dormant no-op is a genuine successful run), so the lane
    # reads green — never "down" either way (2026-08-17 review finding: the
    # original comment here conflated these two states).
    #
    # fire_hours_et corrected 2026-08-24 (measured, not guessed): the
    # cron-broker Railway service's real Cron Schedule is "0 16,21 * * *"
    # (16:00 and 21:00 UTC) -- confirmed from the dashboard, not inferred.
    # In EDT that's 12:00/17:00 ET; in EST it's 11:00/16:00 ET. The previous
    # (14, 19) was never verified against the dashboard and didn't match
    # either -- two real captures (2026-08-23 17:02 ET, 2026-08-24 12:02 ET)
    # each landed ~2min after the true 17:00/12:00 ET slots. This did NOT
    # cause a wrong health signal: the deadline check below only uses
    # max(fire_hours_et), and 19 already safely exceeded the true max in
    # both DST states (17 EDT / 16 EST), so it was inaccurate documentation,
    # not a live bug. Using (12, 18) rather than the literal (12, 17): this
    # file's own header design principle (see the lane-table comment above)
    # is that the deadline should sit PAST each lane's fire+write time, not
    # exactly at it -- 17 would land exactly at the true EDT fire instant
    # (write lands ~17:02), leaving zero margin the way eod=(19,) has margin
    # past its own fire time. 18 restores that margin while still safely
    # exceeding the EST-equivalent max of 16 (Opus review finding, 2026-08-24).
    _Lane("broker",      "SnapTrade broker sync",       "daily", fire_hours_et=(12, 18)),
)


# ── Providers (check ③) ───────────────────────────────────────────────────────
_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("finnhub",       "Live prices — Finnhub (primary)"),
    ("yahoo_finance", "Live prices — Yahoo Finance (secondary)"),
    ("fmp",           "Live prices — FMP (tertiary)"),
    ("supabase",      "Database — Supabase"),
    ("fred",          "Macro data — FRED"),
    # F-244: api_health.py has recorded a "snaptrade" source since the
    # broker-sync feature shipped, but this display list is the ONLY thing
    # that puts a source in front of the user — adding to api_health's
    # internal _stats dict does nothing on its own (found live, 2026-08-17:
    # a user hit "Couldn't reach SnapTrade" with no error detail visible
    # anywhere because this row was missing).
    ("snaptrade",     "Broker sync — SnapTrade"),
)


# ── In-session producer caches (check ④) ──────────────────────────────────────
# Keys that follow the None-on-failure offline contract. Read via
# util.get_or_offline so an offline None is preserved (never collapsed).
_CACHES: tuple[tuple[str, str], ...] = (
    ("_port_df_enriched",          "Portfolio holdings"),
    ("_risk_advisor_recs_cache",   "Risk alerts"),
    ("_corr_df_cache",             "Correlation analysis"),
    ("_actions_cache",             "Act-Today actions"),
    ("_div_recs_cache",            "Diversification advice"),
)


# ── Interactive write-outcome diagnostics (check ⑥) ────────────────────────────
# Both dicts are shaped {"attempted": int, "saved": int, "error": str | None},
# written only on the APP-interactive path (Grow Today build in app.py) inside
# their own try/except. The cron lane's equivalent writes (cron_runner.py) have
# no Streamlit session to publish into — their outcome is console-logged only,
# not surfaced here.
_WRITE_OUTCOMES: tuple[tuple[str, str], ...] = (
    ("_rec_log_save_result",     "Buy recommendations log"),
    ("_gate_ledger_save_result", "Gate suppression ledger"),
)


# ── helpers ───────────────────────────────────────────────────────────────────
def _is_missing_table(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(m in s for m in _MISSING_TABLE_MARKERS)


def _parse_date(v: Any) -> date | None:
    """Lenient date parse — handles a bare 'YYYY-MM-DD' or a full timestamptz
    ('YYYY-MM-DDTHH:MM:SS+00:00', e.g. model_predictions.made_at)."""
    if v is None:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _last_expected_daily_date(expected_hour_et: int) -> date | None:
    """The most recent trading day on which an unconditional daily lane firing
    at ~`expected_hour_et` should have written by now. If today is a trading day
    and that hour has passed, it's today; otherwise the most recent prior
    trading day. Returns None if the trading-calendar helper is unavailable."""
    try:
        from stock_analyzer import market_time
        from stock_analyzer.data import is_trading_day
    except Exception:
        return None
    try:
        now = market_time.now_et()
        d = market_time.today_et()
        if is_trading_day(d) and now.hour >= expected_hour_et:
            return d
        d = d - timedelta(days=1)
        # Bound the walk-back so a broken calendar can never loop forever.
        for _ in range(10):
            if is_trading_day(d):
                return d
            d = d - timedelta(days=1)
        return d
    except Exception:
        return None


def _last_expected_weekly_date(fire_weekday: int, expected_hour_et: int) -> date | None:
    """The most recent calendar date matching `fire_weekday` (Mon=0..Sun=6) that
    is on/before today — i.e. the weekly lane's expected fire date. Self-derives
    ET "now" via `market_time.now_et()` (mirrors `_last_expected_daily_date`
    exactly — NOT handed a `now` from the caller) so this can never be fed a
    non-ET fallback clock; returns None if `market_time` won't import, same as
    the daily helper. Calendar-weekday based, NOT trading-day based
    (thesis/maintenance run on Sun/Sat, non-trading days by design). If today
    IS that weekday but the expected hour hasn't passed yet, the fire hasn't
    happened today, so "expected" steps back to last week's occurrence. Never
    raises — returns None on any error."""
    try:
        from stock_analyzer import market_time
    except Exception:
        return None
    try:
        now = market_time.now_et()
        d = now.date()
        delta_days = (d.weekday() - fire_weekday) % 7
        candidate = d - timedelta(days=delta_days)
        if candidate == d and now.hour < expected_hour_et:
            candidate = candidate - timedelta(days=7)
        return candidate
    except Exception:
        return None


def _age(now: datetime, ts: datetime) -> timedelta:
    return now - ts


# ── ① cron liveness ───────────────────────────────────────────────────────────
def check_cron_liveness() -> list[dict]:
    """Read cron_heartbeat and grade each lane's recency. A lane with no
    heartbeat row yet is 'unknown' (not 'down') — silence right after this
    feature ships, or before a lane's first run, is not a failure. A lane whose
    latest run recorded status='failed' is 'down'.

    Lanes with `fire_hours_et` configured get an additional tightening: an
    otherwise-'ok' row that predates the lane's expected fire (this daily
    hour, or this week's `fire_weekday`+hour) downgrades to 'warn' — this
    catches a heartbeat write that silently failed during a total DB outage
    (the last-good row survives and would otherwise read fresh for up to
    _DAILY_LANE_OK_HOURS/_WEEKLY_LANE_OK_DAYS after the real failure). Only
    ever 'ok' → 'warn'; never overrides 'down'/'unknown'/'failed'."""
    from stock_analyzer import db

    try:
        heartbeats = db.load_cron_heartbeats()
    except Exception:
        heartbeats = None  # load_cron_heartbeats catches internally, but never trust a dep to
    if heartbeats is None:
        # Store unavailable — DB offline OR the cron_heartbeat DDL isn't applied.
        return [{
            "key": lane.key, "label": lane.label, "severity": "unknown",
            "last_run": None, "status": None,
            "detail": "heartbeat unavailable — DB offline, or the cron_heartbeat "
                      "table has not been created (apply its one-time DDL)",
        } for lane in _LANES]

    by_lane = {}
    for row in heartbeats:
        if isinstance(row, dict) and row.get("lane"):
            by_lane[str(row["lane"])] = row

    try:
        from stock_analyzer import market_time
        now = market_time.now_et()
    except Exception:
        now = datetime.now().astimezone()

    out: list[dict] = []
    for lane in _LANES:
        row = by_lane.get(lane.key)
        if row is None:
            out.append({
                "key": lane.key, "label": lane.label, "severity": "unknown",
                "last_run": None, "status": None,
                "detail": "no heartbeat recorded yet",
            })
            continue
        status = str(row.get("status") or "ok")
        raw = row.get("last_run_at")
        ran = None
        try:
            ran = datetime.fromisoformat(str(raw))
        except Exception:
            ran = None

        if ran is None:
            severity, detail = "unknown", "heartbeat present but timestamp unparseable"
        elif status == "failed":
            severity = "down"
            detail = f"last run FAILED — {str(row.get('detail') or '')[:120]}".rstrip(" —")
        else:
            try:
                age = _age(now, ran)
            except Exception:
                age = None
            if age is None:
                severity, detail = "unknown", "could not compute age"
            elif lane.kind == "weekly":
                if age <= timedelta(days=_WEEKLY_LANE_OK_DAYS):
                    severity, detail = "ok", f"fired {_ago(age)}"
                elif age <= timedelta(days=_WEEKLY_LANE_WARN_DAYS):
                    severity, detail = "warn", f"last fired {_ago(age)} — later than weekly cadence"
                else:
                    severity, detail = "down", f"last fired {_ago(age)} — weekly lane appears dead"
            else:  # daily
                if age <= timedelta(hours=_DAILY_LANE_OK_HOURS):
                    severity, detail = "ok", f"fired {_ago(age)}"
                elif age <= timedelta(days=_DAILY_LANE_WARN_DAYS):
                    severity, detail = "warn", f"last fired {_ago(age)} — later than daily cadence"
                else:
                    severity, detail = "down", f"last fired {_ago(age)} — daily lane appears dead"

            # Tightening (2026-08-21): the age-window grading above trusts the
            # last-known-good row's AGE, but during a total DB outage the
            # heartbeat write itself fails to save — the last-good row survives
            # and this lane would read "ok" for up to _DAILY_LANE_OK_HOURS /
            # _WEEKLY_LANE_OK_DAYS after a real failure, even though the
            # failure already fired an outage email through a DB-independent
            # channel (found live 2026-08-16: the `maintenance` lane read green
            # for days after a real outage). Only ever downgrades "ok" → "warn"
            # — never touches "down"/"unknown"/"failed", and never runs for a
            # lane with no `fire_hours_et` configured. Wrapped defensively so
            # this new logic can only ever leave the base result unchanged, not
            # make it worse.
            if severity == "ok" and lane.fire_hours_et:
                try:
                    if lane.fire_weekday is not None:
                        expected = _last_expected_weekly_date(
                            lane.fire_weekday, max(lane.fire_hours_et))
                    else:
                        expected = _last_expected_daily_date(max(lane.fire_hours_et))
                    # `ran` may round-trip from Supabase as UTC even though the
                    # write was ET-native (timestamptz) — normalize to ET so this
                    # compares against `expected` (always ET-native) on the same
                    # calendar date; otherwise an evening ET fire (eod/broker/
                    # thesis) can read a day later in UTC and mask a genuine
                    # single-period miss, worst in EST (winter).
                    from stock_analyzer import market_time as _mt
                    ran_et_date = ran.astimezone(_mt.ET).date()
                    if expected is not None and ran_et_date < expected:
                        severity = "warn"
                        detail = (
                            f"last good heartbeat is from {ran.date()}, but a run was "
                            f"expected by now — a scheduled fire may have failed without "
                            f"recording (e.g. a DB outage prevented the failure write too); "
                            f"check for an outage email."
                        )
                except Exception:
                    pass

        out.append({
            "key": lane.key, "label": lane.label, "severity": severity,
            "last_run": str(raw) if raw is not None else None,
            "status": status, "detail": detail,
        })
    return out


def _ago(age: timedelta) -> str:
    secs = max(0, int(age.total_seconds()))
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


# ── ② data-store existence + freshness (the DDL-catcher) ──────────────────────
def _probe_store(store: _Store) -> dict:
    from stock_analyzer import db

    base = {"table": store.table, "label": store.label, "lane": store.lane,
            "cadence": store.cadence, "severity": "unknown", "state": "unknown",
            "latest": None, "detail": ""}
    if not db.has_db():
        # DELIBERATE ASYMMETRY with check ③, which grades the same condition
        # "down" (see check_providers). Not an inconsistency to harmonise:
        # ② reports whether a STORE exists and is fresh, and with no
        # credentials it genuinely CANNOT know — "unknown" is the honest answer.
        # ③ reports whether the DATABASE is reachable, and "no credentials" is a
        # definite answer to that. ③ is stating the reason ② has to abstain.
        # Both are pinned by tests; "fixing" either to match the other reopens
        # the green-over-blind hole closed on 2026-08-17.
        base["state"], base["detail"] = "offline", "database unreachable"
        return base
    try:
        client = db._client()
        select_cols = store.date_col or "*"
        q = client.table(store.table).select(select_cols)
        if store.date_col:
            q = q.order(store.date_col, desc=True)
        rows = q.limit(1).execute().data
    except Exception as exc:
        if _is_missing_table(exc):
            base["state"], base["severity"] = "missing", "down"
            base["detail"] = "data store does not exist — setup step (DDL) not applied?"
        else:
            base["state"], base["severity"] = "error", "unknown"
            base["detail"] = str(exc)[:120]
        return base

    if not rows:
        base["state"] = "empty"
        # An unconditional daily with zero rows once due is mildly concerning
        # (amber); a conditional store with no rows yet is entirely normal.
        base["severity"] = "warn" if store.unconditional else "unknown"
        base["detail"] = "table exists, no rows yet"
        return base

    base["state"] = "present"
    latest = rows[0].get(store.date_col) if (store.date_col and isinstance(rows[0], dict)) else None
    base["latest"] = latest

    if store.unconditional and store.date_col and store.expected_hour_et is not None:
        latest_d = _parse_date(latest)
        expected = _last_expected_daily_date(store.expected_hour_et)
        if latest_d is None:
            base["severity"], base["detail"] = "unknown", "present (latest date unreadable)"
        elif expected is None:
            base["severity"], base["detail"] = "ok", f"latest {latest_d.isoformat()}"
        elif latest_d >= expected:
            base["severity"], base["detail"] = "ok", f"fresh — latest {latest_d.isoformat()}"
        else:
            base["severity"] = "warn"
            base["detail"] = f"stale — latest {latest_d.isoformat()}, expected {expected.isoformat()}"
    else:
        base["severity"] = "ok"
        _ld = _parse_date(latest)
        base["detail"] = f"exists — latest {_ld.isoformat()}" if _ld else "exists"
    return base


def check_data_stores() -> list[dict]:
    """Probe every store in the inventory. Never raises."""
    out: list[dict] = []
    for store in _INVENTORY:
        try:
            out.append(_probe_store(store))
        except Exception as exc:  # defense in depth — one bad probe can't sink the check
            out.append({"table": store.table, "label": store.label, "lane": store.lane,
                        "cadence": store.cadence, "severity": "unknown", "state": "error",
                        "latest": None, "detail": str(exc)[:120]})
    return out


# ── ③ provider health ─────────────────────────────────────────────────────────
def check_providers() -> list[dict]:
    """Session-scoped provider health from api_health. A provider with zero
    calls this session is 'unknown' (⚪), not degraded."""
    try:
        from stock_analyzer import api_health
    except Exception:
        return []
    level_map = {"green": "ok", "yellow": "warn", "red": "down", "gray": "unknown"}
    out: list[dict] = []
    for source, label in _PROVIDERS:
        try:
            # Supabase special-case (2026-08-17). Without it this check showed a
            # fully GREEN 🩺 System Trust over an app that could read nothing:
            # with no credentials nothing ever CALLS Supabase, so calls == 0 →
            # "unknown" → which ranks 0 → chip_severity rolls up to "ok".
            # `has_db()` is a CREDENTIALS check, not reachability (db.py) — the
            # special-case is defensible only because credentials-missing
            # provably means the app can read nothing. Do NOT let this drift
            # into "has_db() means the DB is up"; that conflation already cost
            # us once in F-239.
            #
            # The reachable-but-broken case is handled SEPARATELY, in db.py's
            # `_record_db_error`. An earlier version of this comment claimed it
            # "needs no help here … real errors already grade this row red" —
            # that was WRONG, and a live outage test on 2026-08-17 disproved it:
            # db.py recorded every failure as a bare "error", and api_health
            # reaches red at auth_errors >= 1, rate_limits >= 3, or FIVE
            # consecutive plain errors,
            # so a wrong service-role key rendered this row AMBER over a
            # database that could not be read at all. db.py now classifies
            # 401/403/RLS as an "auth" event, which is red on the first
            # occurrence. Both halves are needed: this one for credentials
            # ABSENT, that one for credentials WRONG.
            # Function-level import, matching check_cron_liveness/_probe_store.
            if source == "supabase":
                from stock_analyzer import db as _db
                if not _db.has_db():
                    out.append({
                        "source": source, "label": label, "severity": "down",
                        "detail": "no Supabase credentials (SUPABASE_URL / "
                                  "SUPABASE_KEY not set) — the app cannot read "
                                  "holdings, trades or watchlist",
                    })
                    continue
            h = api_health.get_health(source)
            _lvl = h.get("level")
            severity = level_map.get(_lvl, "unknown") if isinstance(_lvl, str) else "unknown"
            calls = h.get("calls", 0) or 0
            if calls == 0:
                severity = "unknown"
                # Say WHY there were no calls. The bare "no calls this session"
                # is exactly the ambiguity that hid the green-over-blind defect
                # above: it read identically whether the provider was merely
                # unused or structurally unreachable.
                detail = ("credentials present, no calls recorded this session"
                          if source == "supabase" else "no calls this session")
            else:
                # api_health's "red" is a cumulative session-lifetime read (e.g.
                # rate_limits >= 3 never decays) — it can stay red long after the
                # provider actually recovered. consec_err == 0 means the MOST
                # RECENT call succeeded, so re-grade that as "warn" (recovered),
                # matching this module's own vocabulary ("warn = degraded but the
                # app still has the input... provider on a backup") instead of
                # showing a stale alarm for a live page.
                recovered = severity == "down" and (h.get("consec_err", 0) or 0) == 0
                if recovered:
                    severity = "warn"
                detail = f"{h.get('successes', 0)}/{calls} ok · last success {h.get('freshness', '—')}"
                if severity in ("warn", "down") and h.get("last_error"):
                    detail += f" · {str(h.get('last_error'))[:80]}"
                if recovered:
                    detail += " · recovered — most recent call succeeded"
        except Exception as exc:
            severity, detail = "unknown", str(exc)[:120]
        out.append({"source": source, "label": label, "severity": severity, "detail": detail})
    return out


# ── ④ in-session producer caches ──────────────────────────────────────────────
def check_caches(session_state: Any = None) -> list[dict]:
    """Report which producer caches populated this run. Read via
    util.get_or_offline so an offline None is preserved. None → 'unknown'
    (a page may simply not have been visited yet this session, or Home is still
    populating them further down the same cold run), a real value → 'ok'. This
    check emits ONLY 'ok'/'unknown' — never 'warn'/'down' — so it can't elevate
    the chip anyway (unknown ranks 0). compute_health() still excludes it from
    the chip rollup explicitly: forward-defense so that if this check ever grew
    a degraded severity, a legitimately-unset-on-a-cold-run cache still couldn't
    false-positive the top-of-Home chip."""
    from stock_analyzer import util

    container: Any = session_state
    if container is None:
        try:
            import streamlit as st
            container = st.session_state
        except Exception:
            container = {}

    out: list[dict] = []
    for key, label in _CACHES:
        try:
            val = util.get_or_offline(container, key)
            loaded = val is not None
        except Exception:
            loaded = False
            val = None
        out.append({
            "key": key, "label": label,
            "severity": "ok" if loaded else "unknown",
            "loaded": loaded,
            "detail": "loaded this run" if loaded else "not loaded this session",
        })
    return out


# ── ⑥ interactive write-outcome diagnostics ────────────────────────────────────
def check_write_outcomes(session_state: Any = None) -> list[dict]:
    """Grade the outcome of writes the app ATTEMPTS on the current interactive
    render (Grow Today build) — distinct from check ② (does a table exist and
    look fresh at all) because this answers "did TODAY's attempt actually
    succeed", closing the gap where a silently swallowed write failure was
    indistinguishable from a healthy no-op.

    Severity:
      key ABSENT from session_state -> 'unknown' (not attempted this session —
        Grow Today hasn't built yet, a Locked Setup guard skipped the write, or
        a read-only session where the write is never attempted). Must NEVER
        read as "no gate fired" / "nothing to record" — that is a materially
        different, ATTEMPTED-and-confirmed-clean state (see below).
      key present, error truthy -> 'down' (attempted and failed — exactly the
        class this check exists to surface).
      key present, error falsy, saved < attempted -> 'warn' (partial upsert;
        some rows silently dropped).
      key present, error falsy, attempted > 0, saved >= attempted -> 'ok'.
      key present, error falsy, attempted == 0 -> 'ok', "nothing to record" —
        an attempted, CONFIRMED-clean run, not the same as unknown/absent.
    Never raises."""
    container: Any = session_state
    if container is None:
        try:
            import streamlit as st
            container = st.session_state
        except Exception:
            container = {}

    out: list[dict] = []
    for key, label in _WRITE_OUTCOMES:
        try:
            result = container.get(key) if hasattr(container, "get") else None
        except Exception:
            result = None
        try:
            if result is None:
                out.append({"key": key, "label": label, "severity": "unknown",
                            "detail": "not attempted this session"})
                continue
            attempted = int(result.get("attempted") or 0)
            saved = int(result.get("saved") or 0)
            error = result.get("error")
            if error:
                severity, detail = "down", f"write failed — {str(error)[:120]}"
            elif attempted and saved < attempted:
                severity, detail = "warn", f"partial write — saved {saved}/{attempted}"
            elif attempted:
                severity, detail = "ok", f"saved {saved}/{attempted}"
            else:
                severity, detail = "ok", "nothing to record this run"
        except Exception as exc:
            severity, detail = "unknown", f"could not grade — {str(exc)[:120]}"
        out.append({"key": key, "label": label, "severity": severity, "detail": detail})
    return out


# ── rollup ────────────────────────────────────────────────────────────────────
_SEVERITY_RANK = {"ok": 0, "unknown": 0, "warn": 1, "down": 2}


def _worst(*severities: str) -> str:
    worst = "ok"
    for s in severities:
        if _SEVERITY_RANK.get(s, 0) > _SEVERITY_RANK.get(worst, 0):
            worst = s
    return worst


# ── ⑤ reference-data shelf life ───────────────────────────────────────────────
def check_reference_data() -> list[dict]:
    """Grade hand-maintained static reference tables for staleness.

    Thin adapter over `reference_shelf.shelf_status()` into this module's row
    shape. AWARENESS ONLY, and reported OFF the Home chip (see compute_health)
    — a stale table is a chore to schedule, not an incident to react to.

    Each row states the CONSEQUENCE, not just an age: per
    `feedback_recommendation_transparency`, never surface a bare number the
    user has to interpret."""
    try:
        from stock_analyzer.reference_shelf import shelf_status
        rows = shelf_status()
    except Exception:
        return []

    out: list[dict] = []
    for row in rows or []:
        detail = row.get("detail") or ""
        consequence = row.get("consequence") or ""
        if row.get("severity") in ("warn", "down") and consequence:
            detail = f"{detail} — {consequence}"
        out.append({
            "key":      row.get("key"),
            "label":    row.get("label"),
            "severity": row.get("severity", "unknown"),
            "detail":   detail,
            "location": row.get("location"),
        })
    return out


def compute_health(session_state: Any = None) -> dict:
    """Run all six checks and roll up a chip severity. Never raises.

    `chip_severity` is the worst of checks ①②③⑥ (cron / data stores /
    providers / write outcomes). Two checks are reported on the page but
    deliberately EXCLUDED from the chip:

      ④ session caches — excluded to avoid cold-load false positives.
      ⑤ reference-data shelf life — excluded because it is a STANDING
        CONDITION, not a transient fault. Every other check reports something
        that is either broken now or fine now; a stale ticker universe stays
        stale for weeks until a human does manual work. Rolling it into the
        chip would park a permanent amber on Home that the user cannot clear
        today — and they would learn to ignore the chip that ALSO says "a cron
        lane has died." Desensitizing the safety instrument costs far more than
        the drift being reported.

    Returns "ok" | "warn" | "down"; the Home chip renders only for
    "warn"/"down"."""
    try:
        from stock_analyzer import market_time
        computed_at = market_time.now_et().isoformat()
    except Exception:
        computed_at = None

    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception:
            return []

    lanes = _safe(check_cron_liveness)
    stores = _safe(check_data_stores)
    providers = _safe(check_providers)
    caches = _safe(check_caches, session_state)
    reference = _safe(check_reference_data)
    writes = _safe(check_write_outcomes, session_state)

    # NB: `reference` and `caches` are deliberately absent from `pipeline` —
    # see the docstring. The guarantee is structural, not merely test-asserted:
    # neither list is ever appended below, so no severity either produces can
    # reach chip_severity/n_warn/n_down regardless of how degraded it reads.
    # `writes` is, by contrast, DELIBERATELY
    # INCLUDED: unlike ④/⑤ it is a same-session pass/fail signal, not a
    # cold-load cache (④) or a standing chore (⑤) — it can legitimately emit
    # warn/down for a real swallowed write failure, which is precisely the
    # class of problem the Home chip exists to surface.
    pipeline = [x["severity"] for x in lanes] + \
               [x["severity"] for x in stores] + \
               [x["severity"] for x in providers] + \
               [x["severity"] for x in writes]
    chip = _worst(*pipeline) if pipeline else "ok"

    n_down = sum(1 for s in pipeline if s == "down")
    n_warn = sum(1 for s in pipeline if s == "warn")

    return {
        "lanes": lanes,
        "stores": stores,
        "providers": providers,
        "caches": caches,
        "reference": reference,
        "writes": writes,
        "chip_severity": chip,
        "n_down": n_down,
        "n_warn": n_warn,
        "computed_at": computed_at,
    }


def get_health(force: bool = False, ttl_sec: int = 300) -> dict:
    """Session-memoized compute_health — recompute at most once per `ttl_sec`
    so the Home chip doesn't re-probe the DB on every rerun. Falls back to a
    direct compute when there's no Streamlit session (tests / headless)."""
    try:
        import streamlit as st
        from stock_analyzer import market_time
        now = market_time.now_et().timestamp()
        cache = st.session_state.get("_system_health_cache")
        if (not force and isinstance(cache, dict)
                and (now - cache.get("_ts", 0)) < ttl_sec):
            return cache["data"]
        data = compute_health(st.session_state)
        st.session_state["_system_health_cache"] = {"_ts": now, "data": data}
        return data
    except Exception:
        # No Streamlit session (tests / headless) — compute directly, unmemoized.
        return compute_health()
