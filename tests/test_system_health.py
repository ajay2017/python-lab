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


def test_providers_recovered_rate_limit_burst_regrades_to_warn():
    # rate_limits >= 3 makes api_health.get_health() report "red" for the rest
    # of the session (that counter never decays) — but if the most recent call
    # actually succeeded, the diagnostic must not keep flagging it as currently
    # down. This is the exact 2026-08-10 live case: Finnhub hit 429s, Yahoo
    # failover covered it, and a later Finnhub call itself succeeded.
    api_health.reset()
    api_health.record("finnhub", "rate_limit")
    api_health.record("finnhub", "rate_limit")
    api_health.record("finnhub", "rate_limit")
    api_health.record("finnhub", "success")
    assert api_health.get_health("finnhub")["level"] == "red"  # underlying counter still red
    provs = {p["source"]: p for p in sh.check_providers()}
    assert provs["finnhub"]["severity"] == "warn"
    assert "recovered" in provs["finnhub"]["detail"]


def test_providers_still_erroring_stays_down():
    api_health.reset()
    for _ in range(5):
        api_health.record("finnhub", "error", "boom")  # consecutive_errors >= 5 → red, last call is NOT a success
    provs = {p["source"]: p for p in sh.check_providers()}
    assert provs["finnhub"]["severity"] == "down"


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


# ── expected-fire tightening (2026-08-21: closes the "row survives a total DB
# outage" gap — see check_cron_liveness's docstring and the block comment above
# _LANES) ───────────────────────────────────────────────────────────────────────
def test_last_expected_weekly_date_due_hour_boundary(monkeypatch):
    """Unit-level lock on `_last_expected_weekly_date`: before the lane's
    fire_weekday+hour this week, expected = last week's occurrence; at/after,
    expected = this week's. 2026-08-22 is a Saturday (weekday 5). Self-derives
    ET "now" via market_time.now_et() (mirrors _last_expected_daily_date) —
    monkeypatch that, don't pass `now` as an argument."""
    import datetime as dt
    import pytz
    et = pytz.timezone("America/New_York")

    before = et.localize(dt.datetime(2026, 8, 22, 9, 0))   # before the 10am fire hour
    monkeypatch.setattr(market_time, "now_et", lambda: before)
    assert sh._last_expected_weekly_date(5, 10) == dt.date(2026, 8, 15)

    after = et.localize(dt.datetime(2026, 8, 22, 11, 0))   # after the 10am fire hour
    monkeypatch.setattr(market_time, "now_et", lambda: after)
    assert sh._last_expected_weekly_date(5, 10) == dt.date(2026, 8, 22)


def test_last_expected_weekly_date_returns_none_on_internal_error(monkeypatch):
    """Never raises — a `market_time.now_et()` that blows up must yield None,
    not a crash, mirroring `_last_expected_daily_date`'s fail-safe style."""
    def _boom():
        raise RuntimeError("clock unavailable")
    monkeypatch.setattr(market_time, "now_et", _boom)
    assert sh._last_expected_weekly_date(5, 10) is None


def test_cron_liveness_weekly_outage_regression(monkeypatch):
    """The motivating bug: a DB outage during Saturday's `maintenance` fire
    means the heartbeat write itself never happened, so last week's row
    survives. Age alone (7d4h) is still inside the weekly OK band (8 days),
    but the row predates THIS week's expected fire — must downgrade to warn,
    not read green."""
    import datetime as dt
    import pytz
    et = pytz.timezone("America/New_York")

    now = et.localize(dt.datetime(2026, 8, 22, 15, 0))   # this Saturday, 5h past the 10am fire
    ran = et.localize(dt.datetime(2026, 8, 15, 11, 0))   # LAST week's Saturday fire (only good row)
    monkeypatch.setattr(market_time, "now_et", lambda: now)
    monkeypatch.setattr(db, "load_cron_heartbeats", lambda: [
        {"lane": "maintenance", "last_run_at": ran.isoformat(), "status": "ok"},
    ])
    lanes = {x["key"]: x for x in sh.check_cron_liveness()}
    assert lanes["maintenance"]["severity"] == "warn"
    assert "expected" in lanes["maintenance"]["detail"]


def test_cron_liveness_daily_missed_fire_warns_past_due_hour(monkeypatch):
    """Daily equivalent of the regression above: an `eod` row dated yesterday
    reads 'warn' once today's 19:00 ET due hour has passed, but stays 'ok'
    before it — not-yet-due must never amber."""
    import datetime as dt
    import pytz
    from stock_analyzer import data as sdata
    et = pytz.timezone("America/New_York")
    monkeypatch.setattr(sdata, "is_trading_day", lambda d: d.weekday() < 5)

    # 2026-08-20 (Thu) 20:00 ET is the last good row; 2026-08-21 (Fri) is "today".
    ran = et.localize(dt.datetime(2026, 8, 20, 20, 0))
    monkeypatch.setattr(db, "load_cron_heartbeats", lambda: [
        {"lane": "eod", "last_run_at": ran.isoformat(), "status": "ok"},
    ])

    past_due = et.localize(dt.datetime(2026, 8, 21, 20, 0))   # after 19:00 ET
    monkeypatch.setattr(market_time, "now_et", lambda: past_due)
    monkeypatch.setattr(market_time, "today_et", lambda: past_due.date())
    lanes = {x["key"]: x for x in sh.check_cron_liveness()}
    assert lanes["eod"]["severity"] == "warn"

    not_due = et.localize(dt.datetime(2026, 8, 21, 18, 0))    # before 19:00 ET
    monkeypatch.setattr(market_time, "now_et", lambda: not_due)
    monkeypatch.setattr(market_time, "today_et", lambda: not_due.date())
    lanes = {x["key"]: x for x in sh.check_cron_liveness()}
    assert lanes["eod"]["severity"] == "ok"   # not yet due — must never false-amber


def test_cron_liveness_broker_multi_fire_uses_max_hour(monkeypatch):
    """broker fires twice a day (14, 19) — grading must use max()=19, NOT
    min()=14. A missed weekday fire (last-good row is the prior trading day,
    now is past 19:00 ET today) reads warn."""
    import datetime as dt
    import pytz
    from stock_analyzer import data as sdata
    et = pytz.timezone("America/New_York")
    monkeypatch.setattr(sdata, "is_trading_day", lambda d: d.weekday() < 5)

    ran = et.localize(dt.datetime(2026, 8, 20, 20, 0))   # prior trading day (Thu)
    monkeypatch.setattr(db, "load_cron_heartbeats", lambda: [
        {"lane": "broker", "last_run_at": ran.isoformat(), "status": "ok"},
    ])
    now = et.localize(dt.datetime(2026, 8, 21, 20, 0))   # Fri, past 19:00 ET
    monkeypatch.setattr(market_time, "now_et", lambda: now)
    monkeypatch.setattr(market_time, "today_et", lambda: now.date())
    lanes = {x["key"]: x for x in sh.check_cron_liveness()}
    assert lanes["broker"]["severity"] == "warn"


def test_cron_liveness_broker_between_fires_stays_ok(monkeypatch):
    """The discriminating case: at Fri 15:00 ET — past the 14:00 fire but
    BEFORE the 19:00 one — a last-good row from Thu must still read 'ok'.
    grading against min()=14 would false-amber here; only max()=19 is correct
    (a healthy day where only the later fire has written yet is not a miss)."""
    import datetime as dt
    import pytz
    from stock_analyzer import data as sdata
    et = pytz.timezone("America/New_York")
    monkeypatch.setattr(sdata, "is_trading_day", lambda d: d.weekday() < 5)

    ran = et.localize(dt.datetime(2026, 8, 20, 20, 0))   # prior trading day (Thu)
    monkeypatch.setattr(db, "load_cron_heartbeats", lambda: [
        {"lane": "broker", "last_run_at": ran.isoformat(), "status": "ok"},
    ])
    now = et.localize(dt.datetime(2026, 8, 21, 15, 0))   # Fri, between the 14:00 and 19:00 fires
    monkeypatch.setattr(market_time, "now_et", lambda: now)
    monkeypatch.setattr(market_time, "today_et", lambda: now.date())
    lanes = {x["key"]: x for x in sh.check_cron_liveness()}
    assert lanes["broker"]["severity"] == "ok"


def test_cron_liveness_tightening_error_leaves_severity_unchanged(monkeypatch):
    """If `_last_expected_weekly_date` itself blows up, the tightening block's
    own try/except must swallow it and leave the base age-window severity
    untouched — never a worse default than before this feature existed."""
    def _boom(*_a, **_kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(sh, "_last_expected_weekly_date", _boom)
    now = market_time.now_et()
    fresh = (now - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(db, "load_cron_heartbeats", lambda: [
        {"lane": "maintenance", "last_run_at": fresh, "status": "ok"},
    ])
    lanes = {x["key"]: x for x in sh.check_cron_liveness()}
    assert lanes["maintenance"]["severity"] == "ok"   # base age-window result, untouched


def test_cron_liveness_tightening_never_overrides_down_or_unknown(monkeypatch):
    """ok→warn is the ONLY transition this tightening can ever make. A fresh
    'failed' row stays down, an already-stale (past the daily OK window) row
    stays whatever the base age-window logic already gave it (no double
    grading), and a lane with no heartbeat row at all stays unknown."""
    now = market_time.now_et()
    monkeypatch.setattr(db, "load_cron_heartbeats", lambda: [
        {"lane": "eod",  "last_run_at": (now - timedelta(hours=1)).isoformat(), "status": "failed", "detail": "boom"},
        {"lane": "scan", "last_run_at": (now - timedelta(days=2)).isoformat(),  "status": "ok"},
        # broker: no row at all.
    ])
    lanes = {x["key"]: x for x in sh.check_cron_liveness()}
    assert lanes["eod"]["severity"] == "down"      # fresh but FAILED — tightening never runs
    assert lanes["scan"]["severity"] == "warn"     # 2d stale — base logic's own answer, not re-graded
    assert lanes["broker"]["severity"] == "unknown"  # no row at all — never touched


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
