"""Tests for stock_analyzer/system_health.py — System Proprioception Phase 1
(owner-only pipeline-trust diagnostic; INFORMS ONLY, changes no decision).

Focus of the contract:
  • the DDL-catcher — a provably-missing data store must read as "down"/"missing"
    (this is the exact 2026-08-07 model_predictions bug the feature exists for);
  • never-raises — every check swallows its own errors and returns structure;
  • the offline/unknown distinction — silence (no calls, no heartbeat, DB
    offline) is NEVER counted as degraded, only a proven failure is.
"""
from datetime import timedelta

from stock_analyzer import system_health as sh
from stock_analyzer import db, api_health, market_time


# ── fakes (mirror test_db_model_predictions.py style) ─────────────────────────
class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    def __init__(self, rows=None, exc=None):
        self._rows = rows if rows is not None else []
        self._exc = exc

    def select(self, *_a, **_kw):
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    def eq(self, *_a, **_kw):
        return self

    def execute(self):
        if self._exc is not None:
            raise self._exc
        return _FakeExecResult(self._rows)


class _FakeClient:
    def __init__(self, rows=None, exc=None):
        self._rows = rows
        self._exc = exc

    def table(self, _name):
        return _FakeQueryBuilder(self._rows, self._exc)


# ── _is_missing_table (the DDL-catcher's core) ────────────────────────────────
def test_is_missing_table_detects_postgrest_and_postgres_markers():
    assert sh._is_missing_table(Exception("Could not find the table 'public.foo' in the schema cache"))
    assert sh._is_missing_table(Exception('relation "public.foo" does not exist'))
    assert sh._is_missing_table(Exception("PGRST205"))
    assert sh._is_missing_table(Exception("42P01"))


def test_is_missing_table_ignores_unrelated_errors():
    assert not sh._is_missing_table(Exception("connection timed out"))
    assert not sh._is_missing_table(Exception("row-level security policy violation"))


# ── ② data stores: the DDL-catcher end to end ─────────────────────────────────
def test_missing_table_reads_as_down(monkeypatch):
    """A relation-does-not-exist error → severity 'down', state 'missing'."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(
        db, "_client",
        lambda: _FakeClient(exc=Exception("Could not find the table 'public.x' in the schema cache")),
    )
    rows = sh.check_data_stores()
    assert rows, "inventory must not be empty"
    assert all(r["severity"] == "down" and r["state"] == "missing" for r in rows)


def test_db_offline_reads_as_unknown_not_down(monkeypatch):
    """DB unreachable is NOT a pipeline failure — it must be 'unknown', never 'down'."""
    monkeypatch.setattr(db, "has_db", lambda: False)
    rows = sh.check_data_stores()
    assert rows
    assert all(r["severity"] == "unknown" and r["state"] == "offline" for r in rows)


def test_unconditional_daily_fresh_row_is_ok(monkeypatch):
    """An unconditional daily store whose latest row is today reads 'ok'."""
    today = market_time.today_et().isoformat()
    monkeypatch.setattr(db, "has_db", lambda: True)
    # every probed table returns a single row carrying every possible date col = today
    row = {"regime_date": today, "snapshot_date": today, "made_at": today, "scan_date": today}
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[row]))
    stores = {r["table"]: r for r in sh.check_data_stores()}
    for tbl in ("daily_regime", "daily_snapshots", "model_predictions", "scanner_cache"):
        assert stores[tbl]["severity"] == "ok", f"{tbl} should be fresh"


def test_unconditional_daily_missing_row_is_warn(monkeypatch):
    """Table exists but empty → the unconditional dailies are amber (warn), never red."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    stores = {r["table"]: r for r in sh.check_data_stores()}
    assert stores["daily_regime"]["severity"] == "warn"
    # a conditional store that's empty is entirely normal → unknown, not warn
    assert stores["exit_signals"]["severity"] == "unknown"


# ── ① cron liveness ───────────────────────────────────────────────────────────
def test_cron_liveness_none_heartbeats_is_unknown(monkeypatch):
    """Heartbeat store unavailable (offline or DDL unapplied) → all lanes unknown."""
    monkeypatch.setattr(db, "load_cron_heartbeats", lambda: None)
    lanes = sh.check_cron_liveness()
    assert lanes and all(x["severity"] == "unknown" for x in lanes)


def test_cron_liveness_fresh_ok_and_failed_down(monkeypatch):
    now = market_time.now_et()
    fresh = (now - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(db, "load_cron_heartbeats", lambda: [
        {"lane": "eod", "last_run_at": fresh, "status": "ok"},
        {"lane": "scan", "last_run_at": fresh, "status": "failed", "detail": "boom"},
    ])
    lanes = {x["key"]: x for x in sh.check_cron_liveness()}
    assert lanes["eod"]["severity"] == "ok"
    assert lanes["scan"]["severity"] == "down"          # ran and failed
    assert lanes["premarket"]["severity"] == "unknown"  # no row yet → not a fault


def test_cron_liveness_stale_daily_is_warn_then_down(monkeypatch):
    now = market_time.now_et()
    monkeypatch.setattr(db, "load_cron_heartbeats", lambda: [
        {"lane": "eod",  "last_run_at": (now - timedelta(days=2)).isoformat(),  "status": "ok"},
        {"lane": "scan", "last_run_at": (now - timedelta(days=9)).isoformat(),  "status": "ok"},
    ])
    lanes = {x["key"]: x for x in sh.check_cron_liveness()}
    assert lanes["eod"]["severity"] == "warn"   # 2d: late but not dead
    assert lanes["scan"]["severity"] == "down"  # 9d: dead


# ── ③ providers ───────────────────────────────────────────────────────────────
def test_providers_zero_calls_is_unknown():
    api_health.reset()
    provs = {p["source"]: p for p in sh.check_providers()}
    assert provs["finnhub"]["severity"] == "unknown"


def test_providers_success_is_ok():
    api_health.reset()
    api_health.record("finnhub", "success")
    provs = {p["source"]: p for p in sh.check_providers()}
    assert provs["finnhub"]["severity"] == "ok"


# ── ④ caches ──────────────────────────────────────────────────────────────────
def test_caches_none_is_unknown_value_is_ok():
    container = {"_port_df_enriched": object(), "_risk_advisor_recs_cache": None}
    caches = {c["key"]: c for c in sh.check_caches(container)}
    assert caches["_port_df_enriched"]["severity"] == "ok"
    assert caches["_risk_advisor_recs_cache"]["severity"] == "unknown"


# ── freshness "due" gating (DST-blindness regression) ─────────────────────────
def test_freshness_does_not_expect_todays_row_before_lane_due_hour(monkeypatch):
    """The blocking bug the Opus review caught: `expected_hour_et` must gate
    "today is due" AFTER the lane's actual fire+write time, or the check
    false-ambers daily. Lock the contract: before the due hour on a trading
    day, the most-recent-expected date is the PRIOR trading day (so today's
    not-yet-written row is NOT flagged stale); after it, today is expected."""
    import datetime as dt
    import pytz
    from stock_analyzer import data as sdata
    et = pytz.timezone("America/New_York")
    monkeypatch.setattr(sdata, "is_trading_day", lambda d: d.weekday() < 5)

    # Wed 2026-08-05 11:00 ET, eod store due at 19:00 → not due yet → expect Tue.
    before = et.localize(dt.datetime(2026, 8, 5, 11, 0))
    monkeypatch.setattr(market_time, "now_et", lambda: before)
    monkeypatch.setattr(market_time, "today_et", lambda: before.date())
    assert sh._last_expected_daily_date(19) < before.date()

    # Same day 20:00 ET → past the due hour → today is expected.
    after = et.localize(dt.datetime(2026, 8, 5, 20, 0))
    monkeypatch.setattr(market_time, "now_et", lambda: after)
    monkeypatch.setattr(market_time, "today_et", lambda: after.date())
    assert sh._last_expected_daily_date(19) == after.date()


# ── rollup + never-raises ─────────────────────────────────────────────────────
def test_worst_severity_ranking():
    assert sh._worst("ok", "unknown") == "ok"
    assert sh._worst("ok", "warn", "unknown") == "warn"
    assert sh._worst("warn", "down") == "down"


def test_caches_excluded_from_chip_rollup(monkeypatch):
    """An offline cache must NOT push the chip to degraded (cold-load safety)."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    today = market_time.today_et().isoformat()
    row = {"regime_date": today, "snapshot_date": today, "made_at": today, "scan_date": today}
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[row]))
    now = market_time.now_et()
    monkeypatch.setattr(db, "load_cron_heartbeats", lambda: [
        {"lane": ln, "last_run_at": (now - timedelta(hours=1)).isoformat(), "status": "ok"}
        for ln in ("premarket", "scan", "intraday", "eod", "thesis")
    ])
    api_health.reset()
    api_health.record("finnhub", "success")
    health = sh.compute_health(session_state={"_port_df_enriched": None})  # cache offline
    assert health["chip_severity"] == "ok"  # cache offline did NOT degrade the chip


def test_compute_health_never_raises_and_has_keys(monkeypatch):
    def _boom():
        raise RuntimeError("db exploded")
    monkeypatch.setattr(db, "has_db", _boom)
    monkeypatch.setattr(db, "load_cron_heartbeats", _boom)
    health = sh.compute_health(session_state={})
    for key in ("lanes", "stores", "providers", "caches", "chip_severity", "n_down", "n_warn"):
        assert key in health
