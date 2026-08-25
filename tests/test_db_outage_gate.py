"""Tests for the DB-outage honesty fixes (2026-08-17), plus the classify/decide
extraction (2026-08-25) that closed the gap this file used to have to disclaim.

Two defects, one theme: the app must never look healthy — or worse, assert
something — while it cannot read the database.

SCOPE HONESTY (UPDATED 2026-08-25): the banner TEXT, the `st.stop()` call, the
retry button, and the sidebar severity are still REVIEW-ONLY (`tests/` cannot
import or render `app.py`). But the two decisions that USED to live inline in
app.py — "what scope does this load failure have" and "what verdict does this
page get given that scope" — are now `db.classify_load_result()` and
`stock_analyzer.outage_gate.decide()`, both pure and tested below. app.py is
now pure wiring around their outputs.
"""
import time

import pandas as pd
import pytest

from stock_analyzer import db, outage_gate, system_health
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


# ─── classify_load_result — the load-scope decision, extracted from app.py ──

def test_classify_holdings_unreadable_is_hard_scope_regardless_of_w_t():
    now = 1_000_000.0
    rec = db.classify_load_result(None, None, None, now)
    assert rec == {"at": now, "detail": db.unavailable_detail() or "Supabase could not be read",
                    "scope": "holdings"}


def test_classify_trades_unreadable_alone_is_partial_not_holdings():
    """The exact regression this extraction exists to guard: F-243 shipped a
    version where a trades-only failure was misclassified with the book's own
    scope, forcing a full-page block for a fault that leaves holdings correct.
    A pre-fix version of this logic fails this assertion."""
    now = 1_000_000.0
    rec = db.classify_load_result(pd.DataFrame({"Ticker": ["AAPL"]}), [], None, now)
    assert rec is not None
    assert rec["scope"] == "partial"
    assert "trade history" in rec["detail"]
    assert "watchlist" not in rec["detail"]


def test_classify_watchlist_unreadable_alone_is_also_partial():
    now = 1_000_000.0
    rec = db.classify_load_result(pd.DataFrame({"Ticker": ["AAPL"]}), None, pd.DataFrame(), now)
    assert rec is not None
    assert rec["scope"] == "partial"
    assert "watchlist" in rec["detail"]
    assert "trade history" not in rec["detail"]


def test_classify_both_watchlist_and_trades_unreadable_names_both():
    now = 1_000_000.0
    rec = db.classify_load_result(pd.DataFrame({"Ticker": ["AAPL"]}), None, None, now)
    assert rec is not None
    assert rec["scope"] == "partial"
    assert "watchlist" in rec["detail"] and "trade history" in rec["detail"]


def test_classify_all_three_present_is_success_returns_none():
    now = 1_000_000.0
    rec = db.classify_load_result(pd.DataFrame({"Ticker": ["AAPL"]}), [], pd.DataFrame(), now)
    assert rec is None


def test_classify_stamps_the_caller_supplied_now_not_a_fresh_clock_read():
    """Pure — the timestamp in the record must be exactly what was passed in,
    proving this function reads no clock of its own."""
    rec = db.classify_load_result(None, None, None, 42.0)
    assert rec["at"] == 42.0


# ─── outage_gate.decide — the render-verdict decision, extracted from app.py ─

def test_decide_no_failure_renders_normally():
    assert outage_gate.decide(None, "🏠 Home", DB_OUTAGE_SAFE_PAGES) == ("none", None)
    assert outage_gate.decide({}, "🏠 Home", DB_OUTAGE_SAFE_PAGES) == ("none", None)


def test_decide_holdings_scope_stops_an_unsafe_page():
    verdict, msg = outage_gate.decide(
        {"scope": "holdings", "detail": "kaboom"}, "🏠 Home", DB_OUTAGE_SAFE_PAGES
    )
    assert verdict == "stop"
    assert "Cannot reach the database" in msg
    assert "kaboom" in msg


def test_decide_holdings_scope_still_renders_the_safe_pages():
    for safe_page in DB_OUTAGE_SAFE_PAGES:
        verdict, msg = outage_gate.decide(
            {"scope": "holdings", "detail": "kaboom"}, safe_page, DB_OUTAGE_SAFE_PAGES
        )
        assert (verdict, msg) == ("none", None), f"{safe_page} must stay reachable during an outage"


def test_decide_partial_scope_warns_but_never_stops_any_page():
    for pg in ("🏠 Home", *DB_OUTAGE_SAFE_PAGES):
        verdict, msg = outage_gate.decide(
            {"scope": "partial", "detail": "watchlist"}, pg, DB_OUTAGE_SAFE_PAGES
        )
        assert verdict == "warn"
        assert "Partial database outage" in msg


def test_decide_unrecognized_scope_falls_through_to_none_not_either_extreme():
    """Defensive: matches the original inline if/elif with no trailing else —
    an unrecognized scope must not accidentally strand OR silently hide a
    real outage; it should behave as if there were no record at all."""
    verdict, msg = outage_gate.decide({"scope": "??"}, "🏠 Home", DB_OUTAGE_SAFE_PAGES)
    assert (verdict, msg) == ("none", None)


def test_decide_composes_end_to_end_with_classify_load_result():
    """The two extracted functions are meant to compose exactly as app.py
    wires them — prove the seam, not just each half in isolation."""
    now = 1_000_000.0
    rec = db.classify_load_result(pd.DataFrame({"Ticker": ["AAPL"]}), None, [], now)
    verdict, msg = outage_gate.decide(rec, "🎯 My Edge", DB_OUTAGE_SAFE_PAGES)
    assert verdict == "warn"
    assert "watchlist" in msg


# ─── Don't strand the user ──────────────────────────────────────────────────

def test_outage_safe_pages_keep_the_diagnostic_reachable():
    """The only mechanical guard on 'the fix must not hide its own diagnostic'.
    Everything else about the gate is review-only."""
    assert "🩺 System Trust" in DB_OUTAGE_SAFE_PAGES
    assert "📖 User Guide" in DB_OUTAGE_SAFE_PAGES


@pytest.fixture(autouse=True)
def _isolate_supabase_health():
    """These tests write to api_health's MODULE-GLOBAL _stats. Without teardown
    they leak auth_errors=1 into every later test in the process — which passes
    today and fails the moment test order changes (test_api_health.py asserts
    overall_level() == gray)."""
    from stock_analyzer import api_health
    api_health._stats.pop("supabase", None)
    yield
    api_health._stats.pop("supabase", None)


# ─── Credentials WRONG (not merely absent) — found by a live outage test ────
# The has_db()-False special-case above covers credentials ABSENT. A wrong
# service-role key is the likelier real fault (it broke the Railway cutover),
# and it took a live test on the dormant Streamlit deploy to show the app
# rendered AMBER — "decisions still have their inputs" — over a dead database.

def test_auth_failure_grades_red_on_the_first_occurrence():
    """api_health reaches red at auth_errors >= 1 or FIVE consecutive plain
    errors. db.py used to record every failure as a bare "error", so a 401 sat
    on yellow until the 5th. Classifying auth faults is what makes one wrong
    key immediately red."""
    from stock_analyzer import api_health
    api_health._stats.pop("supabase", None)
    db._record_db_error("{'message': 'JSON could not be generated', 'code': 401}")
    assert api_health.get_health("supabase")["level"] == "red"


def test_rls_block_is_classified_as_auth_not_a_transient_error():
    from stock_analyzer import api_health
    api_health._stats.pop("supabase", None)
    db._record_db_error('permission denied for table holdings (42501) row-level security')
    assert api_health.get_health("supabase")["level"] == "red"


def test_a_plain_transient_error_still_grades_below_red():
    """The classifier must not turn every failure into an auth fault — a
    genuine transient (timeout, connection reset) should stay recoverable."""
    from stock_analyzer import api_health
    api_health._stats.pop("supabase", None)
    db._record_db_error("connection reset by peer")
    assert api_health.get_health("supabase")["level"] != "red"


def test_wrong_key_turns_the_supabase_provider_row_down(monkeypatch):
    """End to end: credentials present but rejected ⇒ the provider row is down,
    so the chip is red rather than the amber the live test showed."""
    from stock_analyzer import api_health
    monkeypatch.setattr(db, "has_db", lambda: True)
    api_health._stats.pop("supabase", None)
    db._record_db_error("{'code': 401, 'message': 'Invalid API key'}")
    rows = {r["source"]: r for r in system_health.check_providers()}
    assert rows["supabase"]["severity"] == "down"
    assert system_health.compute_health()["chip_severity"] == "down"
