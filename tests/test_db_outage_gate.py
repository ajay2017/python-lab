"""Tests for the DB-outage honesty fixes (2026-08-17).

Two defects, one theme: the app must never look healthy — or worse, assert
something — while it cannot read the database.

SCOPE HONESTY: `tests/` cannot import or render `app.py`, so the banner, the
`st.stop()`, the retry button and the sidebar severity are REVIEW-ONLY and
verified live. Everything testable was deliberately pushed into `db.py` and
`constants.py` so the untestable residual is pure wiring.
"""
import time

import pandas as pd
import pytest

from stock_analyzer import db, system_health
from stock_analyzer.constants import DB_OUTAGE_SAFE_PAGES, DB_RELOAD_RETRY_SEC


# ─── The fabricated watchlist — the sharpest defect of the two ───────────────

def test_watchlist_or_none_never_fabricates_a_default(monkeypatch):
    """`load_watchlist()` returns `list(_DEFAULT_WATCHLIST)` with no
    credentials — a watchlist the user never created, presented as theirs.
    A wrong ASSERTION, worse than a wrong absence. Assert the exact value, not
    just falsiness: `[]` would also be falsy and would still be wrong."""
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_watchlist_or_none() is None
    # The lenient wrapper still fabricates — deliberately, as a FIRST-RUN
    # SEED (decided in the 2026-06-26 review's C3 fix, which changed only
    # the error path to []). It now has ZERO production callers, so this
    # pins the trap rather than a live contract: the next person who
    # autocompletes to load_watchlist() reintroduces C3.
    assert db.load_watchlist() == list(db._DEFAULT_WATCHLIST)


def test_watchlist_or_none_distinguishes_empty_from_unreadable(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)

    class _Ok:
        def table(self, *_a): return self
        def select(self, *_a): return self
        def execute(self): return type("R", (), {"data": []})()

    monkeypatch.setattr(db, "_client", lambda: _Ok())
    assert db.load_watchlist_or_none() == []      # read fine, genuinely empty

    class _Boom:
        def table(self, *_a): raise RuntimeError("PGRST205")

    monkeypatch.setattr(db, "_client", lambda: _Boom())
    assert db.load_watchlist_or_none() is None    # could not read


def test_trades_or_none_distinguishes_empty_from_unreadable(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_trades_or_none() is None

    monkeypatch.setattr(db, "has_db", lambda: True)

    class _Empty:
        def table(self, *_a): return self
        def select(self, *_a): return self
        def order(self, *_a, **_k): return self
        def execute(self): return type("R", (), {"data": []})()

    monkeypatch.setattr(db, "_client", lambda: _Empty())
    out = db.load_trades_or_none()
    # A brand-new journal is a REAL answer, and it must still carry the
    # schema consumers index into.
    assert out is not None and out.empty
    assert list(out.columns) == list(db._TRADE_COLS)

    # Patch the CLIENT, not load_trades: load_trades now delegates here, so
    # patching it proved only that a wrapper survives a raising callee —
    # the real function never raises. This exercises an actual read failure.
    class _Boom:
        def table(self, *_a): raise RuntimeError("PGRST205 trades")
    monkeypatch.setattr(db, "_client", lambda: _Boom())
    monkeypatch.setattr(db.st, "error", lambda *_a, **_k: None)
    assert db.load_trades_or_none() is None
    # And the lenient wrapper degrades to a COLUMNED empty, not a bare frame.
    lenient = db.load_trades()
    assert lenient.empty and list(lenient.columns) == list(db._TRADE_COLS)


# ─── The shared outage explanation ──────────────────────────────────────────

def test_unavailable_detail_states_the_reason_and_is_none_when_healthy(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert "credentials" in (db.unavailable_detail() or "")

    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "load_holdings_or_none", lambda: None)
    assert "holdings" in (db.unavailable_detail() or "")

    monkeypatch.setattr(db, "load_holdings_or_none", lambda: pd.DataFrame())
    assert db.unavailable_detail() is None       # healthy AND empty → no fault

    def _raise():
        raise RuntimeError("kaboom")
    monkeypatch.setattr(db, "load_holdings_or_none", _raise)
    assert "raised" in (db.unavailable_detail() or "")   # never propagates


def test_cron_detail_delegates_so_email_and_banner_cannot_drift(monkeypatch):
    """The whole point of moving the probe into db.py."""
    import cron_runner as cr
    monkeypatch.setattr(cr.db, "has_db", lambda: False)
    assert cr._db_unavailable_detail() == db.unavailable_detail()


# ─── Retry cooldown — boundary asserted exactly ─────────────────────────────

def test_should_attempt_db_reload_boundary_is_inclusive():
    """At EXACTLY the cooldown we retry. Stated as a test rather than reasoned
    about: the 2026-08-04 Critical was an off-by-one of this shape that a
    design review had waved through as harmless."""
    now = 1_000_000.0
    assert db.should_attempt_db_reload(None, now) is True          # never failed
    assert db.should_attempt_db_reload(now - DB_RELOAD_RETRY_SEC, now) is True
    assert db.should_attempt_db_reload(now - DB_RELOAD_RETRY_SEC + 0.01, now) is False
    assert db.should_attempt_db_reload(now, now) is False          # just failed


def test_should_attempt_db_reload_is_pure_and_uses_epoch_not_wall_clock():
    # Pure: same inputs, same answer, no clock read. Guards against a refactor
    # to datetime.now() (which check_antipatterns.py flags anyway).
    a = db.should_attempt_db_reload(0.0, 10.0)
    time.sleep(0.01)
    assert db.should_attempt_db_reload(0.0, 10.0) is a


# ─── Green-over-blind ───────────────────────────────────────────────────────

def test_supabase_row_is_down_when_credentials_missing(monkeypatch):
    """Before this fix: no credentials → nothing calls Supabase → calls == 0 →
    'unknown' → ranks 0 → a fully GREEN System Trust over a blind app."""
    monkeypatch.setattr(db, "has_db", lambda: False)
    rows = {r["source"]: r for r in system_health.check_providers()}
    assert rows["supabase"]["severity"] == "down"
    assert "credentials" in rows["supabase"]["detail"]


def test_blind_app_turns_the_home_chip_red(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    health = system_health.compute_health()
    assert health["chip_severity"] == "down"
    assert health["n_down"] >= 1


def test_check_providers_never_raises_if_has_db_raises(monkeypatch):
    def _raise():
        raise RuntimeError("secrets backend down")
    monkeypatch.setattr(db, "has_db", _raise)
    rows = {r["source"]: r for r in system_health.check_providers()}
    assert rows["supabase"]["severity"] == "unknown"   # degraded, not crashed


def test_zero_calls_detail_distinguishes_supabase_from_other_providers(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    rows = {r["source"]: r for r in system_health.check_providers()}
    if rows["supabase"]["severity"] == "unknown":
        assert "credentials present" in rows["supabase"]["detail"]


def test_store_checks_stay_unknown_while_provider_row_goes_down(monkeypatch):
    """DELIBERATE ASYMMETRY, pinned so nobody 'harmonises' it away.
    ② cannot know whether a store exists without credentials — 'unknown' is
    honest. ③ knows the database is unreachable — 'down' is honest. ③ is
    stating the reason ② has to abstain."""
    monkeypatch.setattr(db, "has_db", lambda: False)
    stores = system_health.check_data_stores()
    assert stores and all(r["severity"] == "unknown" for r in stores)
    providers = {r["source"]: r for r in system_health.check_providers()}
    assert providers["supabase"]["severity"] == "down"


# ─── Don't strand the user ──────────────────────────────────────────────────

def test_outage_safe_pages_keep_the_diagnostic_reachable():
    """The only mechanical guard on 'the fix must not hide its own diagnostic'.
    Everything else about the gate is review-only."""
    assert "🩺 System Trust" in DB_OUTAGE_SAFE_PAGES
    assert "📖 User Guide" in DB_OUTAGE_SAFE_PAGES
