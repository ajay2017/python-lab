"""System proprioception — pipeline-trust health checks (Phase 1).

OWNER-ONLY DIAGNOSTIC. Every function here is READ-ONLY and INFORMING: it
reports the operational state of the data pipeline and changes NO gate, NO
recommendation, NO composite, NO threshold. It answers one question — *can I
trust what the app told me today?* — and nothing more.

Pull-based / render-time: nothing here depends on its own background job having
run (the "who watches the watcher" constraint from
docs/plans/system-proprioception.md). Every value is read live at call time from
a store that already exists.

Four checks:
  ① cron liveness      — reads `cron_heartbeat` (written by each cron_runner lane)
  ② data-store health  — existence (a missing table = the DDL-catcher) + freshness
  ③ provider health    — reads `api_health` (session-scoped provider call stats)
  ④ in-session caches   — which session_state producer caches populated this run

Severity vocabulary:
  "ok"      (🟢) — healthy / fresh.
  "warn"    (🟡) — degraded but the app still has the input (stale row, provider
                   on a backup, weekly lane a touch late). Amber, never suppresses.
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
)


# ── The cron lanes (check ①) ──────────────────────────────────────────────────
@dataclass(frozen=True)
class _Lane:
    key: str
    label: str
    kind: str   # "daily" | "weekly"


_LANES: tuple[_Lane, ...] = (
    _Lane("premarket",   "Pre-market protective scan", "daily"),
    _Lane("scan",        "Morning market scan",        "daily"),
    _Lane("intraday",    "Intraday pullback check",    "daily"),
    _Lane("eod",         "End-of-day snapshot",        "daily"),
    _Lane("thesis",      "Weekly thesis & debrief",    "weekly"),
    # Saturday housekeeping (cron_runner._run_maintenance): idempotent data
    # backfills. Registered here so a silent death is visible on 🩺 System
    # Trust — a lane that writes heartbeats nobody grades is a lane that can
    # stop firing unnoticed, which defeats the dead-man's-switch.
    _Lane("maintenance", "Weekly data backfills",      "weekly"),
)


# ── Providers (check ③) ───────────────────────────────────────────────────────
_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("finnhub",       "Live prices — Finnhub (primary)"),
    ("yahoo_finance", "Live prices — Yahoo Finance (secondary)"),
    ("fmp",           "Live prices — FMP (tertiary)"),
    ("supabase",      "Database — Supabase"),
    ("fred",          "Macro data — FRED"),
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


def _age(now: datetime, ts: datetime) -> timedelta:
    return now - ts


# ── ① cron liveness ───────────────────────────────────────────────────────────
def check_cron_liveness() -> list[dict]:
    """Read cron_heartbeat and grade each lane's recency. A lane with no
    heartbeat row yet is 'unknown' (not 'down') — silence right after this
    feature ships, or before a lane's first run, is not a failure. A lane whose
    latest run recorded status='failed' is 'down'."""
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
            h = api_health.get_health(source)
            _lvl = h.get("level")
            severity = level_map.get(_lvl, "unknown") if isinstance(_lvl, str) else "unknown"
            calls = h.get("calls", 0) or 0
            if calls == 0:
                severity = "unknown"
                detail = "no calls this session"
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


# ── rollup ────────────────────────────────────────────────────────────────────
_SEVERITY_RANK = {"ok": 0, "unknown": 0, "warn": 1, "down": 2}


def _worst(*severities: str) -> str:
    worst = "ok"
    for s in severities:
        if _SEVERITY_RANK.get(s, 0) > _SEVERITY_RANK.get(worst, 0):
            worst = s
    return worst


def compute_health(session_state: Any = None) -> dict:
    """Run all four checks and roll up a chip severity. Never raises.

    `chip_severity` is the worst of checks ①②③ ONLY (cron / data stores /
    providers) — check ④ (session caches) is reported on the page but excluded
    from the chip to avoid cold-load false positives. Returns "ok" | "warn" |
    "down"; the Home chip renders only for "warn"/"down"."""
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

    pipeline = [x["severity"] for x in lanes] + \
               [x["severity"] for x in stores] + \
               [x["severity"] for x in providers]
    chip = _worst(*pipeline) if pipeline else "ok"

    n_down = sum(1 for s in pipeline if s == "down")
    n_warn = sum(1 for s in pipeline if s == "warn")

    return {
        "lanes": lanes,
        "stores": stores,
        "providers": providers,
        "caches": caches,
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
