"""Tests for stock_analyzer/data_maintenance.py — database maintenance /
data-quality framework, Phase 1 (docs/plans/data-maintenance-framework.md).

DETECTION ONLY. Focus of the contract:
  - offline-sentinel discipline: a check fed a None-returning loader must
    emit "unknown", never collapse to "ok" or silence;
  - the protected-table guard is real and structurally enforced;
  - the orphan-cache grace-period boundary is exact;
  - duplicate detection respects the null-safety rules the plan doc calls
    out explicitly (never treat two NULLs as a match, never let a
    broker-synced row match a manual one);
  - one check raising cannot suppress the others' results.
"""
from datetime import timedelta

import pandas as pd
import pytest

from stock_analyzer import data_maintenance as dm
from stock_analyzer import db
from stock_analyzer.constants import DATA_MAINT_ORPHAN_CACHE_GRACE_DAYS
from stock_analyzer.market_time import today_et

pytestmark = pytest.mark.fast


# ── protected-table guard ─────────────────────────────────────────────────────

def test_assert_not_protected_raises_for_protected_table():
    with pytest.raises(ValueError):
        dm.assert_not_protected("recommendations")
    with pytest.raises(ValueError):
        dm.assert_not_protected("analyst_coverage")


def test_assert_not_protected_does_not_raise_for_non_protected_table():
    dm.assert_not_protected("bundle_cache")  # must not raise
    dm.assert_not_protected("account_flows")  # must not raise


def test_would_delete_from_enforces_the_guard():
    with pytest.raises(ValueError):
        dm.would_delete_from("gate_suppressions")
    dm.would_delete_from("sector_cache")  # must not raise


def test_all_protected_tables_named_in_the_hard_constraint():
    """Pins the exact set named in docs/plans/data-maintenance-framework.md's
    'hard constraint' section -- a silent edit to this frozenset is exactly
    the kind of drift a mechanical test should catch."""
    assert dm._PROTECTED_TABLES == frozenset({
        "recommendations", "exit_signals", "gate_suppressions", "rec_events",
        "score_history", "model_predictions", "analyst_target_snapshots",
        "judgment_opinions", "judgment_grades", "analyst_coverage",
    })


# ── check 1 — orphan cache rows: offline sentinel ────────────────────────────

def test_orphan_cache_offline_table_emits_unknown_not_ok(monkeypatch):
    """Every one of the 8 registered tables returning None (offline) must
    produce exactly 8 'unknown' rows -- never a silent [] and never 'ok'."""
    monkeypatch.setattr(db, "load_ticker_last_touched", lambda table, col: None)
    rows = dm.check_orphan_cache_rows(set(), set(), set())
    assert len(rows) == len(dm._ORPHAN_CACHE_TABLES)
    assert all(r["severity"] == "unknown" for r in rows)
    assert all("DB unreachable" in r["detail"] for r in rows)


def test_orphan_cache_partial_offline_isolated_per_table(monkeypatch):
    """One table failing to read must not affect the others' verdicts."""
    def _loader(table, col):
        if table == "bundle_cache":
            return None
        return []  # every other table: read fine, zero rows
    monkeypatch.setattr(db, "load_ticker_last_touched", _loader)
    rows = dm.check_orphan_cache_rows(set(), set(), set())
    by_key = {r["key"]: r for r in rows}
    assert by_key["orphan_cache_bundle_cache"]["severity"] == "unknown"
    assert by_key["orphan_cache_fundamentals_cache"]["severity"] == "ok"


# ── check 1 — orphan detection boundary ──────────────────────────────────────

def _touch_row(ticker: str, days_ago: int, date_col: str) -> dict:
    d = (today_et() - timedelta(days=days_ago)).isoformat()
    return {"ticker": ticker, date_col: d}


def test_orphan_never_flags_a_ticker_in_any_roster(monkeypatch):
    """A ticker in held/watchlist/discovery is never flagged, regardless of
    how old its cache row is."""
    def _loader(table, col):
        if table != "bundle_cache":
            return []
        return [
            _touch_row("HELD", 9999, col),
            _touch_row("WATCH", 9999, col),
            _touch_row("DISC", 9999, col),
        ]
    monkeypatch.setattr(db, "load_ticker_last_touched", _loader)
    rows = dm.check_orphan_cache_rows({"HELD"}, {"WATCH"}, {"DISC"})
    bundle_row = next(r for r in rows if r["key"] == "orphan_cache_bundle_cache")
    assert bundle_row["severity"] == "ok"
    assert bundle_row["count"] == 0


def test_orphan_boundary_exact_grace_days_not_flagged(monkeypatch):
    def _loader(table, col):
        if table != "bundle_cache":
            return []
        return [_touch_row("ATBOUNDARY", DATA_MAINT_ORPHAN_CACHE_GRACE_DAYS, col)]
    monkeypatch.setattr(db, "load_ticker_last_touched", _loader)
    rows = dm.check_orphan_cache_rows(set(), set(), set())
    bundle_row = next(r for r in rows if r["key"] == "orphan_cache_bundle_cache")
    assert bundle_row["severity"] == "ok"
    assert bundle_row["count"] == 0


def test_orphan_boundary_one_day_past_grace_is_flagged(monkeypatch):
    def _loader(table, col):
        if table != "bundle_cache":
            return []
        return [_touch_row("PASTBOUNDARY", DATA_MAINT_ORPHAN_CACHE_GRACE_DAYS + 1, col)]
    monkeypatch.setattr(db, "load_ticker_last_touched", _loader)
    rows = dm.check_orphan_cache_rows(set(), set(), set())
    bundle_row = next(r for r in rows if r["key"] == "orphan_cache_bundle_cache")
    assert bundle_row["severity"] == "warn"
    assert bundle_row["count"] == 1
    assert "PASTBOUNDARY" in bundle_row["detail"]


def test_orphan_case_insensitive_roster_match(monkeypatch):
    def _loader(table, col):
        if table != "bundle_cache":
            return []
        return [_touch_row("aapl", 9999, col)]
    monkeypatch.setattr(db, "load_ticker_last_touched", _loader)
    rows = dm.check_orphan_cache_rows({"AAPL"}, set(), set())
    bundle_row = next(r for r in rows if r["key"] == "orphan_cache_bundle_cache")
    assert bundle_row["severity"] == "ok"


def test_orphan_malformed_date_is_skipped_not_crashed(monkeypatch):
    def _loader(table, col):
        if table != "bundle_cache":
            return []
        return [{"ticker": "BADDATE", col: "not-a-real-date"}]
    monkeypatch.setattr(db, "load_ticker_last_touched", _loader)
    rows = dm.check_orphan_cache_rows(set(), set(), set())  # must not raise
    bundle_row = next(r for r in rows if r["key"] == "orphan_cache_bundle_cache")
    assert bundle_row["severity"] == "ok"  # unparseable -> can't confirm recency -> not flagged


# ── None roster propagation (2026-10-02 review M1) ───────────────────────────
#
# A failed holdings/watchlist/discovery-universe read must never collapse to
# an empty set() here -- that would read identically to "genuinely not held
# anywhere", silently misreporting a held ticker with an aging cache row as
# an undisclosed orphan. This is the SAME "unknown" posture the function
# already uses when a cache TABLE itself can't be read -- extended here to
# cover a failed ROSTER read too.

def test_orphan_cache_none_held_roster_returns_unknown_never_false_positive(monkeypatch):
    """A held ticker with a genuinely stale cache row must read 'unknown',
    not 'warn', when the holdings read itself failed this run -- a warn
    here would be reporting a currently-held ticker as orphaned purely
    because one unrelated load failed."""
    def _loader(table, col):
        if table != "bundle_cache":
            return []
        return [_touch_row("REALLYHELD", DATA_MAINT_ORPHAN_CACHE_GRACE_DAYS + 1, col)]
    monkeypatch.setattr(db, "load_ticker_last_touched", _loader)
    rows = dm.check_orphan_cache_rows(None, set(), set())
    assert len(rows) == len(dm._ORPHAN_CACHE_TABLES)
    assert all(r["severity"] == "unknown" for r in rows)
    bundle_row = next(r for r in rows if r["key"] == "orphan_cache_bundle_cache")
    assert "REALLYHELD" not in bundle_row["detail"]


def test_orphan_cache_none_watchlist_or_discovery_roster_also_returns_unknown(monkeypatch):
    """Any ONE of the three rosters being None is enough to withhold the
    whole check -- not just the held-tickers one."""
    monkeypatch.setattr(db, "load_ticker_last_touched", lambda table, col: [])
    rows_wl = dm.check_orphan_cache_rows(set(), None, set())
    rows_disc = dm.check_orphan_cache_rows(set(), set(), None)
    assert all(r["severity"] == "unknown" for r in rows_wl)
    assert all(r["severity"] == "unknown" for r in rows_disc)


def test_orphan_cache_all_rosters_present_still_detects_normally(monkeypatch):
    """The None-propagation fix must not degrade the ordinary, all-present
    case -- a real orphan is still flagged when every roster loaded fine."""
    def _loader(table, col):
        if table != "bundle_cache":
            return []
        return [_touch_row("ORPHANED", DATA_MAINT_ORPHAN_CACHE_GRACE_DAYS + 1, col)]
    monkeypatch.setattr(db, "load_ticker_last_touched", _loader)
    rows = dm.check_orphan_cache_rows(set(), set(), set())
    bundle_row = next(r for r in rows if r["key"] == "orphan_cache_bundle_cache")
    assert bundle_row["severity"] == "warn"
    assert "ORPHANED" in bundle_row["detail"]


def test_run_all_checks_propagates_none_roster_to_orphan_check(monkeypatch):
    """run_all_checks must forward a None roster through rather than
    defaulting it to set() before check_orphan_cache_rows ever sees it."""
    monkeypatch.setattr(db, "load_ticker_last_touched", lambda table, col: [])
    monkeypatch.setattr(db, "load_account_flows_for_dedup_check", lambda: [])
    monkeypatch.setattr(
        db, "load_analyst_coverage_or_none",
        lambda **kw: pd.DataFrame(columns=["ticker", "article_date", "raw_text"]),
    )
    monkeypatch.setattr(
        db, "load_thesis_reviews",
        lambda: pd.DataFrame(columns=["ticker", "inputs_hash"]),
    )
    monkeypatch.setattr(db, "load_snaptrade_pending_imports", lambda status="pending": [])
    rows = dm.run_all_checks(None, set(), set())
    orphan_rows = [r for r in rows if r["key"].startswith("orphan_cache_")]
    assert len(orphan_rows) == len(dm._ORPHAN_CACHE_TABLES)
    assert all(r["severity"] == "unknown" for r in orphan_rows)


# ── check 2 — account_flows duplicates ───────────────────────────────────────

def test_account_flows_offline_emits_unknown(monkeypatch):
    monkeypatch.setattr(db, "load_account_flows_for_dedup_check", lambda: None)
    rows = dm.check_account_flows_duplicates()
    assert len(rows) == 1
    assert rows[0]["severity"] == "unknown"


def test_account_flows_flags_identical_manual_rows(monkeypatch):
    rows_data = [
        {"id": 1, "flow_date": "2026-09-01", "flow_type": "deposit",
         "amount": 500.0, "note": "test", "snaptrade_txn_id": None},
        {"id": 2, "flow_date": "2026-09-01", "flow_type": "deposit",
         "amount": 500.0, "note": "test", "snaptrade_txn_id": None},
    ]
    monkeypatch.setattr(db, "load_account_flows_for_dedup_check", lambda: rows_data)
    rows = dm.check_account_flows_duplicates()
    assert rows[0]["severity"] == "warn"
    assert rows[0]["count"] == 1


def test_account_flows_does_not_flag_distinct_broker_synced_rows(monkeypatch):
    """Two rows with identical (flow_date, flow_type, amount, note) but
    DISTINCT snaptrade_txn_ids are a legitimate broker sync, never a bug --
    and neither is ever compared at all, since only NULL-txn-id rows enter
    the grouping."""
    rows_data = [
        {"id": 1, "flow_date": "2026-09-01", "flow_type": "deposit",
         "amount": 500.0, "note": None, "snaptrade_txn_id": "txn-a"},
        {"id": 2, "flow_date": "2026-09-01", "flow_type": "deposit",
         "amount": 500.0, "note": None, "snaptrade_txn_id": "txn-b"},
    ]
    monkeypatch.setattr(db, "load_account_flows_for_dedup_check", lambda: rows_data)
    rows = dm.check_account_flows_duplicates()
    assert rows[0]["severity"] == "ok"


def test_account_flows_clean_returns_ok(monkeypatch):
    rows_data = [
        {"id": 1, "flow_date": "2026-09-01", "flow_type": "deposit",
         "amount": 500.0, "note": None, "snaptrade_txn_id": None},
        {"id": 2, "flow_date": "2026-09-15", "flow_type": "withdrawal",
         "amount": 200.0, "note": None, "snaptrade_txn_id": None},
    ]
    monkeypatch.setattr(db, "load_account_flows_for_dedup_check", lambda: rows_data)
    rows = dm.check_account_flows_duplicates()
    assert rows[0]["severity"] == "ok"


# ── check 3 — analyst_coverage duplicates (F2b), the COALESCE trap ───────────

def test_analyst_coverage_flags_identical_raw_text_same_article(monkeypatch):
    df = pd.DataFrame([
        {"ticker": "AAPL", "article_date": "2026-09-01", "raw_text": "same text here"},
        {"ticker": "AAPL", "article_date": "2026-09-01", "raw_text": "same text here"},
    ])
    monkeypatch.setattr(db, "load_analyst_coverage_or_none", lambda **kw: df)
    rows = dm.check_analyst_coverage_duplicates()
    assert rows[0]["severity"] == "warn"
    assert rows[0]["count"] == 1


def test_analyst_coverage_does_not_flag_distinct_firms_same_article(monkeypatch):
    """The legitimate multi-firm-per-article case (D9) -- same (ticker,
    article_date), genuinely different raw_text -- must never be flagged."""
    df = pd.DataFrame([
        {"ticker": "AAPL", "article_date": "2026-09-01", "raw_text": "Goldman's take"},
        {"ticker": "AAPL", "article_date": "2026-09-01", "raw_text": "Morgan Stanley's take"},
    ])
    monkeypatch.setattr(db, "load_analyst_coverage_or_none", lambda **kw: df)
    rows = dm.check_analyst_coverage_duplicates()
    assert rows[0]["severity"] == "ok"


def test_analyst_coverage_does_not_flag_two_null_raw_text_rows(monkeypatch):
    """The COALESCE trap this spec explicitly warns about: two rows that
    BOTH have NULL raw_text must never be treated as matching each other."""
    df = pd.DataFrame([
        {"ticker": "AAPL", "article_date": "2026-09-01", "raw_text": None},
        {"ticker": "AAPL", "article_date": "2026-09-01", "raw_text": None},
    ])
    monkeypatch.setattr(db, "load_analyst_coverage_or_none", lambda **kw: df)
    rows = dm.check_analyst_coverage_duplicates()
    assert rows[0]["severity"] == "ok"


def test_analyst_coverage_empty_df_is_ok_not_crash(monkeypatch):
    monkeypatch.setattr(
        db, "load_analyst_coverage_or_none",
        lambda **kw: pd.DataFrame(columns=["ticker", "article_date", "raw_text"]),
    )
    rows = dm.check_analyst_coverage_duplicates()
    assert rows[0]["severity"] == "ok"


def test_analyst_coverage_none_is_unknown_not_ok(monkeypatch):
    """Offline-sentinel discipline (2026-09-28 Opus review fix): a failed
    read (None) must surface as 'unknown', never silently collapse to 'ok'
    the way an empty-but-successful read correctly does above."""
    monkeypatch.setattr(db, "load_analyst_coverage_or_none", lambda **kw: None)
    rows = dm.check_analyst_coverage_duplicates()
    assert rows[0]["severity"] == "unknown"


def test_analyst_coverage_does_not_flag_same_text_different_firms(monkeypatch):
    """Real production finding (2026-09-28, first live run): the D9-confirmed
    legitimate multi-firm-per-article case can ALSO share IDENTICAL raw_text
    (one extraction emits one row per firm, same source text) -- grouping on
    raw_text alone re-flagged this exact already-closed population (e.g.
    CRCL 2026-08-03: TD Cowen vs Morgan Stanley). `analysts` must be part of
    the key so two rows differing only in firm/rating/target are never
    flagged, even with byte-identical raw_text."""
    df = pd.DataFrame([
        {"ticker": "CRCL", "article_date": "2026-08-03", "raw_text": "same source article",
         "analysts": [{"firm": "TD Cowen", "rating": "Buy", "price_target": 82}]},
        {"ticker": "CRCL", "article_date": "2026-08-03", "raw_text": "same source article",
         "analysts": [{"firm": "Morgan Stanley", "rating": "Underweight", "price_target": 38}]},
    ])
    monkeypatch.setattr(db, "load_analyst_coverage_or_none", lambda **kw: df)
    rows = dm.check_analyst_coverage_duplicates()
    assert rows[0]["severity"] == "ok"


def test_analyst_coverage_flags_same_text_and_same_firm(monkeypatch):
    """The genuine accidental-re-paste case: SAME text, SAME firm/rating --
    an exact match on every field, not just raw_text -- must still flag."""
    df = pd.DataFrame([
        {"ticker": "AAPL", "article_date": "2026-09-01", "raw_text": "same source article",
         "analysts": [{"firm": "Goldman", "rating": "Buy", "price_target": 250}]},
        {"ticker": "AAPL", "article_date": "2026-09-01", "raw_text": "same source article",
         "analysts": [{"firm": "Goldman", "rating": "Buy", "price_target": 250}]},
    ])
    monkeypatch.setattr(db, "load_analyst_coverage_or_none", lambda **kw: df)
    rows = dm.check_analyst_coverage_duplicates()
    assert rows[0]["severity"] == "warn"


# ── check 4 — thesis_reviews duplicates (F2c) ────────────────────────────────

def test_thesis_reviews_flags_identical_inputs_hash(monkeypatch):
    df = pd.DataFrame([
        {"ticker": "MSFT", "inputs_hash": "abc123"},
        {"ticker": "MSFT", "inputs_hash": "abc123"},
    ])
    monkeypatch.setattr(db, "load_thesis_reviews", lambda: df)
    rows = dm.check_thesis_reviews_duplicates()
    assert rows[0]["severity"] == "warn"


def test_thesis_reviews_does_not_flag_two_null_hash_rows(monkeypatch):
    df = pd.DataFrame([
        {"ticker": "MSFT", "inputs_hash": None},
        {"ticker": "MSFT", "inputs_hash": None},
    ])
    monkeypatch.setattr(db, "load_thesis_reviews", lambda: df)
    rows = dm.check_thesis_reviews_duplicates()
    assert rows[0]["severity"] == "ok"


def test_thesis_reviews_does_not_flag_earnings_checkpoint_marker(monkeypatch):
    """Real production finding (2026-09-28, first live run): app.py's
    earnings-checkpoint save_thesis_review call site writes a deliberately
    coarse inputs_hash literal, f"earnings_{report_date}" -- NOT a content
    hash -- so every genuine, distinct earnings-checkpoint review of the
    SAME report shares it by design. Grouping on it flagged 24 tickers'
    worth of entirely legitimate reviews. Must never flag rows whose
    inputs_hash carries this marker prefix, however many share it."""
    df = pd.DataFrame([
        {"ticker": "V", "inputs_hash": "earnings_2026-08-15"},
        {"ticker": "V", "inputs_hash": "earnings_2026-08-15"},
        {"ticker": "V", "inputs_hash": "earnings_2026-08-15"},
    ])
    monkeypatch.setattr(db, "load_thesis_reviews", lambda: df)
    rows = dm.check_thesis_reviews_duplicates()
    assert rows[0]["severity"] == "ok"


def test_thesis_reviews_still_flags_a_genuine_repeated_content_hash(monkeypatch):
    """Regression guard for the fix above: a genuine 16-hex-char content
    hash (the manual "Review Thesis" path's real format) repeated for the
    same ticker must still flag -- only the "earnings_" marker is excluded."""
    df = pd.DataFrame([
        {"ticker": "MSFT", "inputs_hash": "a1b2c3d4e5f6a7b8"},
        {"ticker": "MSFT", "inputs_hash": "a1b2c3d4e5f6a7b8"},
    ])
    monkeypatch.setattr(db, "load_thesis_reviews", lambda: df)
    rows = dm.check_thesis_reviews_duplicates()
    assert rows[0]["severity"] == "warn"


# ── check 5 — pending broker imports (F4) ────────────────────────────────────

def test_pending_imports_flags_duplicate_txn_id(monkeypatch):
    rows_data = [
        {"id": 1, "snaptrade_txn_id": "txn-1"},
        {"id": 2, "snaptrade_txn_id": "txn-1"},
    ]
    monkeypatch.setattr(db, "load_snaptrade_pending_imports", lambda status="pending": rows_data)
    rows = dm.check_pending_import_anomalies()
    assert rows[0]["severity"] == "warn"


def test_pending_imports_clean_is_ok(monkeypatch):
    rows_data = [{"id": 1, "snaptrade_txn_id": "txn-1"}, {"id": 2, "snaptrade_txn_id": "txn-2"}]
    monkeypatch.setattr(db, "load_snaptrade_pending_imports", lambda status="pending": rows_data)
    rows = dm.check_pending_import_anomalies()
    assert rows[0]["severity"] == "ok"


# ── isolation: run_all_checks ─────────────────────────────────────────────────

def test_run_all_checks_isolates_a_raising_check(monkeypatch):
    """One check raising must not prevent the other four from reporting."""
    def _boom(*_a, **_k):
        raise RuntimeError("simulated check failure")
    monkeypatch.setattr(dm, "check_orphan_cache_rows", _boom)
    monkeypatch.setattr(db, "load_account_flows_for_dedup_check", lambda: [])
    monkeypatch.setattr(
        db, "load_analyst_coverage_or_none",
        lambda **kw: pd.DataFrame(columns=["ticker", "article_date", "raw_text"]),
    )
    monkeypatch.setattr(
        db, "load_thesis_reviews",
        lambda: pd.DataFrame(columns=["ticker", "inputs_hash"]),
    )
    monkeypatch.setattr(db, "load_snaptrade_pending_imports", lambda status="pending": [])

    rows = dm.run_all_checks(set(), set(), set())
    keys = {r["key"] for r in rows}
    assert "orphan_cache_error" in keys
    assert any(r["severity"] == "unknown" for r in rows if r["key"] == "orphan_cache_error")
    # the other four checks still produced their own result rows
    assert "account_flows_duplicates" in keys
    assert "analyst_coverage_duplicates" in keys
    assert "thesis_reviews_duplicates" in keys
    assert "pending_import_duplicates" in keys


def test_run_all_checks_never_raises_end_to_end(monkeypatch):
    """A fully offline DB across every check must degrade to structured
    'unknown'/'ok' rows, never raise out of run_all_checks."""
    monkeypatch.setattr(db, "load_ticker_last_touched", lambda table, col: None)
    monkeypatch.setattr(db, "load_account_flows_for_dedup_check", lambda: None)
    monkeypatch.setattr(
        db, "load_analyst_coverage_or_none",
        lambda **kw: pd.DataFrame(columns=["ticker", "article_date", "raw_text"]),
    )
    monkeypatch.setattr(
        db, "load_thesis_reviews",
        lambda: pd.DataFrame(columns=["ticker", "inputs_hash"]),
    )
    monkeypatch.setattr(db, "load_snaptrade_pending_imports", lambda status="pending": [])
    rows = dm.run_all_checks(set(), set(), set())
    assert isinstance(rows, list) and len(rows) > 0
    assert all(r.get("severity") in ("ok", "warn", "down", "unknown") for r in rows)


# ── write-time dedup guard (F2a) — exercised via stock_analyzer.util ─────────

def test_add_account_flow_guard_rejects_recent_duplicate(monkeypatch):
    from stock_analyzer import market_time
    from stock_analyzer.util import is_duplicate_account_flow
    from stock_analyzer.constants import ACCOUNT_FLOW_DEDUP_WINDOW_SEC

    now = market_time.now_et()
    candidate = {"flow_date": "2026-09-28", "flow_type": "deposit",
                 "amount": 500.0, "note": "test"}
    recent = [{
        "flow_date": "2026-09-28", "flow_type": "deposit", "amount": 500.0,
        "note": "test", "snaptrade_txn_id": None,
        "created_at": now.isoformat(),
    }]
    assert is_duplicate_account_flow(candidate, recent, ACCOUNT_FLOW_DEDUP_WINDOW_SEC) is True


def test_add_account_flow_guard_accepts_a_genuinely_new_flow(monkeypatch):
    from stock_analyzer import market_time
    from stock_analyzer.util import is_duplicate_account_flow
    from stock_analyzer.constants import ACCOUNT_FLOW_DEDUP_WINDOW_SEC

    now = market_time.now_et()
    candidate = {"flow_date": "2026-09-28", "flow_type": "deposit",
                 "amount": 500.0, "note": "test"}
    recent = [{
        "flow_date": "2026-09-20", "flow_type": "deposit", "amount": 500.0,
        "note": "a totally different note", "snaptrade_txn_id": None,
        "created_at": (now - timedelta(days=8)).isoformat(),
    }]
    assert is_duplicate_account_flow(candidate, recent, ACCOUNT_FLOW_DEDUP_WINDOW_SEC) is False


def test_add_account_flow_guard_accepts_old_repeat_outside_window(monkeypatch):
    """A genuinely repeated flow (identical fields) entered outside the dedup
    window is NOT blocked -- e.g. two real $500 deposits made days apart."""
    from stock_analyzer import market_time
    from stock_analyzer.util import is_duplicate_account_flow
    from stock_analyzer.constants import ACCOUNT_FLOW_DEDUP_WINDOW_SEC

    now = market_time.now_et()
    candidate = {"flow_date": "2026-09-28", "flow_type": "deposit",
                 "amount": 500.0, "note": None}
    recent = [{
        "flow_date": "2026-09-28", "flow_type": "deposit", "amount": 500.0,
        "note": None, "snaptrade_txn_id": None,
        "created_at": (now - timedelta(days=3)).isoformat(),
    }]
    assert is_duplicate_account_flow(candidate, recent, ACCOUNT_FLOW_DEDUP_WINDOW_SEC) is False


def test_add_account_flow_guard_never_matches_a_broker_synced_row(monkeypatch):
    """A row matching in every field EXCEPT it carries a non-null
    snaptrade_txn_id is a legitimate broker sync -- never a match target."""
    from stock_analyzer import market_time
    from stock_analyzer.util import is_duplicate_account_flow
    from stock_analyzer.constants import ACCOUNT_FLOW_DEDUP_WINDOW_SEC

    now = market_time.now_et()
    candidate = {"flow_date": "2026-09-28", "flow_type": "deposit",
                 "amount": 500.0, "note": None}
    recent = [{
        "flow_date": "2026-09-28", "flow_type": "deposit", "amount": 500.0,
        "note": None, "snaptrade_txn_id": "snap-123",
        "created_at": now.isoformat(),
    }]
    assert is_duplicate_account_flow(candidate, recent, ACCOUNT_FLOW_DEDUP_WINDOW_SEC) is False


def test_add_account_flow_end_to_end_rejects_duplicate_insert(monkeypatch):
    """db.add_account_flow itself: a double-click submitting the same form
    within the dedup window must return False and never call insert()."""
    from stock_analyzer import market_time

    now = market_time.now_et()
    existing = {
        "id": 1, "flow_date": "2026-09-28", "flow_type": "deposit",
        "amount": 500.0, "note": "Paycheck", "snaptrade_txn_id": None,
        "created_at": now.isoformat(),
    }

    class _FakeTable:
        def __init__(self):
            self.insert_called = False

        def select(self, *_a, **_kw):
            return self

        def order(self, *_a, **_kw):
            return self

        def insert(self, record):
            self.insert_called = True
            return self

        def execute(self):
            from types import SimpleNamespace
            return SimpleNamespace(data=[existing])

    fake_table = _FakeTable()

    class _FakeClient:
        def table(self, _name):
            return fake_table

    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient())

    ok = db.add_account_flow("2026-09-28", "deposit", 500.0, "Paycheck")
    assert ok is False
    assert fake_table.insert_called is False


def test_add_account_flow_end_to_end_accepts_new_flow(monkeypatch):
    """A genuinely new flow (no matching recent row) is accepted and
    inserted normally."""
    class _FakeTable:
        def __init__(self):
            self.inserted = None

        def select(self, *_a, **_kw):
            return self

        def order(self, *_a, **_kw):
            return self

        def insert(self, record):
            self.inserted = record
            return self

        def execute(self):
            from types import SimpleNamespace
            return SimpleNamespace(data=[])

    fake_table = _FakeTable()

    class _FakeClient:
        def table(self, _name):
            return fake_table

    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient())

    ok = db.add_account_flow("2026-09-28", "deposit", 750.0, "New deposit")
    assert ok is True
    assert fake_table.inserted is not None
    assert fake_table.inserted["amount"] == 750.0
