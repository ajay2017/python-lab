"""Tests for the App Settings reference-data layer.

Commit 1 of 3 (docs/plans/app-settings.md): the pure
`stock_analyzer.reference_data` module, the new `stock_analyzer.db`
`reference_tables`/`reference_table_history` functions, and
`reference_shelf`'s DB-first `as_of` fallback for the three migrated tables.

Commit 2 of 3: `resolve_universe` is now wired into every real importer
(scanner.py/portfolio.py/ticker_liveness.py/cron_runner.py/app.py — see
their own test files for the wiring-level proof); this file adds coverage
for the additional pure decision helpers Commit 2 needed:
`resolve_universe_or_none`, the TIGHTENED `sector_candidates` bucket-key-
EQUALITY rule in `validate_payload` (a Commit-1 Opus review finding),
`decide_large_drop_confirmation`, `decide_save_action`, `changed_tickers`,
`classify_ticker_resolution`, and `history_delta` — the ⚙️ App Settings
page's save/validate/confirm DECISION logic, extracted out of app.py per
this project's "extract the DECISION, not just the helper" convention.
"""
from __future__ import annotations

from datetime import date

import pytest

from stock_analyzer import db, reference_shelf
from stock_analyzer.constants import REFERENCE_SHELF_LIFE_DAYS
from stock_analyzer.reference_data import (
    ReferenceDataUnavailable,
    canonicalize,
    changed_tickers,
    classify_ticker_resolution,
    decide_large_drop_confirmation,
    decide_save_action,
    history_delta,
    resolve_universe,
    resolve_universe_or_none,
    validate_payload,
)


# ── Fakes, mirroring tests/test_db_model_predictions.py's style, extended ───
# with a shared mutable per-table store so a test can save then load and see
# its own write -- needed to exercise the content-hash no-op-save invariant.

class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    def __init__(self, rows_ref, raise_on_execute=False):
        self._rows_ref = rows_ref
        self._filters = []
        self._order = None
        self._limit_n = None
        self._raise = raise_on_execute
        self._write = None  # ("upsert" | "insert", rows, on_conflict)

    def select(self, *_a, **_kw):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit_n = n
        return self

    def upsert(self, data, on_conflict=None):
        self._write = ("upsert", data if isinstance(data, list) else [data], on_conflict)
        return self

    def insert(self, data):
        self._write = ("insert", data if isinstance(data, list) else [data], None)
        return self

    def execute(self):
        if self._raise:
            raise RuntimeError("simulated relation does not exist / transient failure")
        if self._write:
            kind, rows, on_conflict = self._write
            if kind == "upsert":
                key_cols = (on_conflict or "name").split(",")
                for r in rows:
                    match = next(
                        (x for x in self._rows_ref
                         if all(x.get(c) == r.get(c) for c in key_cols)),
                        None,
                    )
                    if match is not None:
                        match.update(r)
                    else:
                        self._rows_ref.append(dict(r))
            else:  # insert
                for r in rows:
                    self._rows_ref.append(dict(r))
            return _FakeExecResult(list(rows))
        # a read
        data = list(self._rows_ref)
        for col, val in self._filters:
            data = [r for r in data if r.get(col) == val]
        if self._order:
            col, desc = self._order
            data = sorted(data, key=lambda r: r.get(col) or "", reverse=desc)
        if self._limit_n is not None:
            data = data[: self._limit_n]
        return _FakeExecResult(data)


class _FakeClient:
    """Shared mutable per-table row store across repeated `.table()` calls --
    closer to real Supabase behaviour than a single canned response, needed
    so a save can be immediately followed by a load that sees it."""

    def __init__(self, raise_on_execute=False):
        self._store: dict = {}
        self._raise = raise_on_execute

    def table(self, name):
        rows_ref = self._store.setdefault(name, [])
        return _FakeQueryBuilder(rows_ref, raise_on_execute=self._raise)


def _wire_fake_db(monkeypatch, readonly=False, has_db_val=True, raise_on_execute=False):
    fake = _FakeClient(raise_on_execute=raise_on_execute)
    monkeypatch.setattr(db, "is_readonly", lambda: readonly)
    monkeypatch.setattr(db, "has_db", lambda: has_db_val)
    monkeypatch.setattr(db, "_client", lambda: fake)
    return fake


# ── canonicalize ──────────────────────────────────────────────────────────────

def test_canonicalize_sorts_buckets_and_tickers_and_uppercases():
    payload = {"b_sector": ["msft", "aapl"], "a_sector": ["ibm"]}
    out = canonicalize(payload)
    assert list(out.keys()) == ["a_sector", "b_sector"]
    assert out["b_sector"] == ["AAPL", "MSFT"]


def test_canonicalize_is_idempotent_and_order_case_insensitive():
    p1 = {"Tech": ["msft", "AAPL"], "Health": ["lly"]}
    p2 = {"Health": ["LLY"], "Tech": ["aapl", "MSFT"]}
    c1 = canonicalize(p1)
    c2 = canonicalize(p2)
    assert c1 == c2
    assert canonicalize(c1) == c1  # idempotent


def test_canonicalize_empty_payload_is_empty_dict():
    assert canonicalize({}) == {}
    assert canonicalize(None) == {}


# ── resolve_universe ──────────────────────────────────────────────────────────

def test_resolve_universe_raises_when_db_returns_none(monkeypatch):
    monkeypatch.setattr(db, "load_reference_table", lambda name: None)
    with pytest.raises(ReferenceDataUnavailable):
        resolve_universe("sector_universe")


def test_resolve_universe_raises_on_empty_dict_payload(monkeypatch):
    monkeypatch.setattr(
        db, "load_reference_table",
        lambda name: {"payload": {}, "as_of": "2026-08-01", "payload_hash": "x"},
    )
    with pytest.raises(ReferenceDataUnavailable):
        resolve_universe("sector_universe")


def test_resolve_universe_raises_when_every_bucket_is_empty(monkeypatch):
    monkeypatch.setattr(
        db, "load_reference_table",
        lambda name: {"payload": {"Tech": [], "Health": []}, "as_of": "2026-08-01"},
    )
    with pytest.raises(ReferenceDataUnavailable):
        resolve_universe("sector_universe")


def test_resolve_universe_returns_payload_and_as_of_from_the_same_read(monkeypatch):
    calls = []

    def _fake_load(name):
        calls.append(name)
        return {
            "payload": {"Tech": ["AAPL", "MSFT"]},
            "as_of": "2026-08-15",
            "payload_hash": "abc",
            "updated_by": "seed_script",
        }

    monkeypatch.setattr(db, "load_reference_table", _fake_load)
    payload, as_of = resolve_universe("sector_universe")
    assert payload == {"Tech": ["AAPL", "MSFT"]}
    assert as_of == date(2026, 8, 15)
    assert calls == ["sector_universe"]  # exactly one read -- can't diverge


def test_resolve_universe_raises_when_payload_present_but_as_of_missing(monkeypatch):
    """A non-empty payload with no resolvable as_of is a malformed row, not a
    legitimate state -- payload and as_of must always travel together, so
    this must raise rather than silently return (payload, None)."""
    monkeypatch.setattr(
        db, "load_reference_table",
        lambda name: {"payload": {"Tech": ["AAPL"]}, "as_of": None, "payload_hash": "x"},
    )
    with pytest.raises(ReferenceDataUnavailable):
        resolve_universe("sector_universe")


# ── validate_payload ──────────────────────────────────────────────────────────

def test_validate_payload_rejects_bucket_set_change():
    errors = validate_payload(
        "discovery_universe",
        {"Tech": ["AAPL"], "NewBucket": ["MSFT"]},
        existing_bucket_keys={"Tech", "Health"},
    )
    assert errors
    assert "locked" in errors[0]


def test_validate_payload_accepts_membership_only_change():
    errors = validate_payload(
        "discovery_universe",
        {"Tech": ["AAPL", "MSFT"], "Health": ["LLY"]},
        existing_bucket_keys={"Tech", "Health"},
    )
    assert errors == []


def test_validate_payload_rejects_unknown_ticker_for_sector_candidates():
    errors = validate_payload(
        "sector_candidates",
        {"Semiconductors": ["NVDA", "ZZZFAKE"]},
        existing_bucket_keys=None,
    )
    assert errors
    assert "ZZZFAKE" in errors[0]


def test_validate_payload_accepts_known_tickers_for_sector_candidates():
    # NVDA/AMD both carry a real portfolio.TICKER_SECTORS entry.
    errors = validate_payload(
        "sector_candidates",
        {"Semiconductors": ["NVDA", "AMD"]},
        existing_bucket_keys=None,
    )
    assert errors == []


def test_validate_payload_sector_candidates_rule_is_scoped_to_that_table():
    """An unclassified ticker is fine for any OTHER table -- the
    TICKER_SECTORS coverage rule applies only to sector_candidates."""
    errors = validate_payload(
        "discovery_universe",
        {"Tech": ["ZZZFAKE"]},
        existing_bucket_keys=None,
    )
    assert errors == []


# ── sector_candidates: TIGHTENED bucket-key EQUALITY rule (2026-09-01) ───────
# A Commit-1 Opus review finding: the original check only confirmed *some*
# TICKER_SECTORS entry existed, not that its VALUE equals the bucket key the
# ticker is being placed under -- letting e.g. AAPL (a Consumer Tech name)
# be added into a Healthcare bucket, the exact roster incoherence
# tests/test_portfolio.py::test_roster_ticker_sector_matches_its_roster_key
# forbids for the real _SECTOR_CANDIDATES dict.

def test_validate_payload_rejects_ticker_placed_under_wrong_sector():
    # AAPL's real TICKER_SECTORS value is "Consumer Tech", not "Healthcare".
    errors = validate_payload(
        "sector_candidates",
        {"Healthcare": ["AAPL"]},
        existing_bucket_keys=None,
    )
    assert errors
    assert "AAPL" in errors[-1]
    assert "Healthcare" in errors[-1] and "Consumer Tech" in errors[-1]


def test_validate_payload_accepts_ticker_placed_under_its_curated_sector():
    # NVDA's real TICKER_SECTORS value IS "Semiconductors" -- correct placement.
    errors = validate_payload(
        "sector_candidates",
        {"Semiconductors": ["NVDA"]},
        existing_bucket_keys=None,
    )
    assert errors == []


def test_validate_payload_wrong_sector_and_unknown_ticker_both_reported():
    # A mismatch and an unknown ticker are independent failure modes -- both
    # must be reported, not just whichever the validator checks first.
    errors = validate_payload(
        "sector_candidates",
        {"Healthcare": ["AAPL", "ZZZFAKE"]},
        existing_bucket_keys=None,
    )
    assert len(errors) == 2
    assert any("ZZZFAKE" in e for e in errors)
    assert any("AAPL" in e for e in errors)


# ── db.load_reference_table ───────────────────────────────────────────────────

def test_load_reference_table_no_creds_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_reference_table("sector_universe") is None


def test_load_reference_table_query_failure_returns_none(monkeypatch):
    _wire_fake_db(monkeypatch, raise_on_execute=True)
    assert db.load_reference_table("sector_universe") is None


def test_load_reference_table_no_row_yet_returns_none(monkeypatch):
    _wire_fake_db(monkeypatch)
    assert db.load_reference_table("sector_universe") is None


# ── db.load_reference_table_history ───────────────────────────────────────────

def test_load_reference_table_history_no_creds_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_reference_table_history("sector_universe") is None


def test_load_reference_table_history_query_failure_returns_none(monkeypatch):
    _wire_fake_db(monkeypatch, raise_on_execute=True)
    assert db.load_reference_table_history("sector_universe") is None


def test_load_reference_table_history_no_rows_yet_returns_empty_list(monkeypatch):
    _wire_fake_db(monkeypatch)
    assert db.load_reference_table_history("sector_universe") == []


# ── db.save_reference_table ───────────────────────────────────────────────────

def test_save_reference_table_readonly_is_error_not_raise(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    result = db.save_reference_table("sector_universe", {"Tech": ["AAPL"]}, "tester")
    assert result["status"] == "error"


def test_save_reference_table_no_creds_is_error(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: False)
    result = db.save_reference_table("sector_universe", {"Tech": ["AAPL"]}, "tester")
    assert result["status"] == "error"


def test_save_reference_table_never_raises_on_simulated_db_failure(monkeypatch):
    _wire_fake_db(monkeypatch, raise_on_execute=True)
    result = db.save_reference_table("sector_universe", {"Tech": ["AAPL"]}, "tester")  # no raise
    assert result["status"] == "error"
    assert result.get("detail")


def test_save_reference_table_first_save_writes_row_and_history(monkeypatch):
    fake = _wire_fake_db(monkeypatch)
    result = db.save_reference_table("sector_universe", {"Tech": ["msft", "aapl"]}, "tester")
    assert result["status"] == "saved"
    assert result["as_of"]

    stored = fake._store["reference_tables"]
    assert len(stored) == 1
    assert stored[0]["payload"] == {"Tech": ["AAPL", "MSFT"]}

    history = fake._store["reference_table_history"]
    assert len(history) == 1
    assert history[0]["payload"] == {"Tech": ["AAPL", "MSFT"]}


def test_save_reference_table_reordered_recased_identical_payload_is_no_change(monkeypatch):
    """The load-bearing boundary test: a save that canonicalizes identically
    to what's already stored must be a no-op -- as_of unmoved, no new history
    row. This is the entire snooze-button-proofing mechanism."""
    fake = _wire_fake_db(monkeypatch)

    first = db.save_reference_table("sector_universe", {"Tech": ["msft", "aapl"]}, "tester")
    assert first["status"] == "saved"
    as_of_after_first = fake._store["reference_tables"][0]["as_of"]
    history_len_after_first = len(fake._store["reference_table_history"])

    second = db.save_reference_table("sector_universe", {"Tech": ["AAPL", "MSFT"]}, "tester2")
    assert second == {"status": "no_change"}
    assert fake._store["reference_tables"][0]["as_of"] == as_of_after_first
    assert len(fake._store["reference_table_history"]) == history_len_after_first


def test_save_reference_table_genuine_delta_saves_and_appends_history(monkeypatch):
    from stock_analyzer.market_time import today_et

    fake = _wire_fake_db(monkeypatch)
    first = db.save_reference_table("sector_universe", {"Tech": ["AAPL"]}, "tester")
    assert first["status"] == "saved"

    second = db.save_reference_table("sector_universe", {"Tech": ["AAPL", "MSFT"]}, "tester")
    assert second["status"] == "saved"
    assert second["as_of"] == today_et().isoformat()
    assert len(fake._store["reference_table_history"]) == 2
    assert fake._store["reference_tables"][0]["payload"] == {"Tech": ["AAPL", "MSFT"]}


# ── reference_shelf's DB-first as_of resolution ──────────────────────────────

def test_reference_shelf_falls_back_to_code_as_of_when_db_has_no_row(monkeypatch):
    monkeypatch.setattr(db, "load_reference_table", lambda name: None)
    entry = next(e for e in reference_shelf._REFERENCE_TABLES if e.key == "sector_universe")
    shelf_days = REFERENCE_SHELF_LIFE_DAYS[entry.key]
    today = date.fromordinal(entry.as_of.toordinal() + shelf_days)

    severity, detail = reference_shelf._grade_as_of(entry, today)
    assert severity == "ok"
    assert entry.as_of.isoformat() in detail


def test_reference_shelf_prefers_db_as_of_when_a_row_is_present(monkeypatch):
    entry = next(e for e in reference_shelf._REFERENCE_TABLES if e.key == "discovery_universe")
    db_as_of = date(2026, 8, 30)
    monkeypatch.setattr(
        db, "load_reference_table",
        lambda name: {"as_of": db_as_of.isoformat()} if name == "discovery_universe" else None,
    )
    severity, detail = reference_shelf._grade_as_of(entry, date(2026, 8, 31))
    assert db_as_of.isoformat() in detail
    assert entry.as_of.isoformat() not in detail


def test_reference_shelf_non_migrated_table_never_touches_db(monkeypatch):
    """sp500_sector_weights is NOT one of the three migrated tables -- its
    as_of grading must never call db.load_reference_table at all."""
    calls = []
    monkeypatch.setattr(
        db, "load_reference_table",
        lambda name: calls.append(name) or None,
    )
    entry = next(e for e in reference_shelf._REFERENCE_TABLES if e.key == "sp500_sector_weights")
    reference_shelf._grade_as_of(entry, date(2026, 8, 31))
    assert calls == []


# ═════════════════════════════════════════════════════════════════════════════
# Commit 2 — App Settings save-flow decision helpers
# ═════════════════════════════════════════════════════════════════════════════

# ── resolve_universe_or_none ─────────────────────────────────────────────────

def test_resolve_universe_or_none_success_returns_payload_and_as_of(monkeypatch):
    monkeypatch.setattr(
        db, "load_reference_table",
        lambda name: {"payload": {"Tech": ["AAPL"]}, "as_of": "2026-08-15", "payload_hash": "x"},
    )
    payload, as_of, err = resolve_universe_or_none("sector_universe")
    assert payload == {"Tech": ["AAPL"]}
    assert as_of == date(2026, 8, 15)
    assert err is None


def test_resolve_universe_or_none_failure_returns_none_payload_and_error(monkeypatch):
    monkeypatch.setattr(db, "load_reference_table", lambda name: None)
    payload, as_of, err = resolve_universe_or_none("sector_universe")
    assert payload is None
    assert as_of is None
    assert err  # non-empty error string, never a raised exception
    assert "sector_universe" in err


def test_resolve_universe_or_none_never_raises_on_empty_payload(monkeypatch):
    monkeypatch.setattr(
        db, "load_reference_table",
        lambda name: {"payload": {}, "as_of": "2026-08-01", "payload_hash": "x"},
    )
    payload, as_of, err = resolve_universe_or_none("sector_universe")
    assert payload is None and as_of is None and err


# ── decide_large_drop_confirmation ───────────────────────────────────────────
# Boundary is the "== is still normal" shape shared with
# TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT / reference_shelf's shelf-life grade:
# a drop of EXACTLY the threshold does NOT trigger; only strictly MORE does.

def test_large_drop_exactly_at_threshold_does_not_need_confirmation():
    old = {"Tech": [f"T{i}" for i in range(10)]}   # 10 tickers
    new = {"Tech": [f"T{i}" for i in range(7)]}    # 7 tickers -> 30% drop exactly
    result = decide_large_drop_confirmation(old, new, threshold_pct=30.0)
    assert result["needs_confirmation"] is False
    assert result["reasons"] == []


def test_large_drop_one_ticker_past_threshold_needs_confirmation():
    old = {"Tech": [f"T{i}" for i in range(10)]}   # 10 tickers
    new = {"Tech": [f"T{i}" for i in range(6)]}    # 6 tickers -> 40% drop, past 30%
    result = decide_large_drop_confirmation(old, new, threshold_pct=30.0)
    assert result["needs_confirmation"] is True
    assert result["reasons"]
    assert "40%" in result["reasons"][0]


def test_large_drop_growth_never_needs_confirmation():
    old = {"Tech": ["AAPL"]}
    new = {"Tech": ["AAPL", "MSFT", "GOOGL"]}
    result = decide_large_drop_confirmation(old, new, threshold_pct=30.0)
    assert result["needs_confirmation"] is False


def test_large_drop_old_total_zero_never_triggers_count_drop():
    old = {"Tech": []}
    new = {"Tech": []}
    result = decide_large_drop_confirmation(old, new, threshold_pct=30.0)
    assert result["needs_confirmation"] is False


def test_large_drop_emptied_bucket_triggers_unconditionally_regardless_of_pct():
    # A bucket going from 1 ticker to 0 is a 100% drop for THAT bucket, but
    # a tiny drop overall -- must still trigger, independent of the overall
    # percentage threshold.
    old = {"Tech": [f"T{i}" for i in range(99)], "Health": ["LLY"]}
    new = {"Tech": [f"T{i}" for i in range(99)], "Health": []}
    result = decide_large_drop_confirmation(old, new, threshold_pct=30.0)
    assert result["needs_confirmation"] is True
    assert any("Health" in r for r in result["reasons"])


def test_large_drop_reorder_and_recase_alone_never_triggers():
    old = {"Tech": ["msft", "aapl"]}
    new = {"Tech": ["AAPL", "MSFT"]}
    result = decide_large_drop_confirmation(old, new, threshold_pct=30.0)
    assert result["needs_confirmation"] is False


# ── decide_save_action — precedence order ────────────────────────────────────

def test_decide_save_action_structure_errors_block_regardless_of_everything_else():
    decision = decide_save_action(
        structure_errors=["bucket locked"],
        validator_offline=True,
        unresolved_tickers=["ZZZ"],
        large_drop={"needs_confirmation": True, "reasons": ["big drop"]},
        confirmed=True,
    )
    assert decision["action"] == "blocked"
    assert decision["reasons"] == ["bucket locked"]


def test_decide_save_action_validator_offline_blocks_before_unresolved():
    decision = decide_save_action(
        structure_errors=[], validator_offline=True,
        unresolved_tickers=["ZZZ"], large_drop=None, confirmed=False,
    )
    assert decision["action"] == "blocked"
    assert "provider" in decision["reasons"][0] or "validate" in decision["reasons"][0]


def test_decide_save_action_unresolved_tickers_block():
    decision = decide_save_action(
        structure_errors=[], validator_offline=False,
        unresolved_tickers=["ZZZFAKE"], large_drop=None, confirmed=False,
    )
    assert decision["action"] == "blocked"
    assert "ZZZFAKE" in decision["reasons"][0]


def test_decide_save_action_large_drop_without_confirmation_needs_confirmation():
    decision = decide_save_action(
        structure_errors=[], validator_offline=False, unresolved_tickers=[],
        large_drop={"needs_confirmation": True, "reasons": ["big drop"]},
        confirmed=False,
    )
    assert decision["action"] == "needs_confirmation"
    assert decision["reasons"] == ["big drop"]


def test_decide_save_action_large_drop_with_confirmation_proceeds():
    decision = decide_save_action(
        structure_errors=[], validator_offline=False, unresolved_tickers=[],
        large_drop={"needs_confirmation": True, "reasons": ["big drop"]},
        confirmed=True,
    )
    assert decision["action"] == "proceed"


def test_decide_save_action_clean_path_proceeds():
    decision = decide_save_action()
    assert decision["action"] == "proceed"
    assert decision["reasons"] == []


# ── changed_tickers ───────────────────────────────────────────────────────────

def test_changed_tickers_detects_addition():
    old = {"Tech": ["AAPL"]}
    new = {"Tech": ["AAPL", "MSFT"]}
    assert changed_tickers(old, new) == {"MSFT"}


def test_changed_tickers_ignores_removal():
    # A removed ticker is not "changed" for validation purposes -- it needs
    # no provider-existence check, only an addition/move does.
    old = {"Tech": ["AAPL", "MSFT"]}
    new = {"Tech": ["AAPL"]}
    assert changed_tickers(old, new) == set()


def test_changed_tickers_moved_bucket_counts_as_changed():
    old = {"Tech": ["AAPL"], "Health": []}
    new = {"Tech": [], "Health": ["AAPL"]}
    assert changed_tickers(old, new) == {"AAPL"}


def test_changed_tickers_reorder_and_recase_alone_is_not_a_change():
    old = {"Tech": ["msft", "aapl"]}
    new = {"Tech": ["AAPL", "MSFT"]}
    assert changed_tickers(old, new) == set()


def test_changed_tickers_no_changes_at_all():
    same = {"Tech": ["AAPL", "MSFT"]}
    assert changed_tickers(same, dict(same)) == set()


# ── classify_ticker_resolution ───────────────────────────────────────────────

def test_classify_ticker_resolution_empty_tickers_short_circuits():
    result = classify_ticker_resolution(set(), prices={}, provider_health_red=False)
    assert result == {"validator_offline": False, "unresolved": []}


def test_classify_ticker_resolution_none_prices_means_validator_offline():
    result = classify_ticker_resolution({"AAPL"}, prices=None, provider_health_red=False)
    assert result["validator_offline"] is True
    assert result["unresolved"] == []


def test_classify_ticker_resolution_all_unresolved_and_health_red_means_offline():
    result = classify_ticker_resolution(
        {"ZZZ1", "ZZZ2"}, prices={}, provider_health_red=True,
    )
    assert result["validator_offline"] is True
    assert result["unresolved"] == []


def test_classify_ticker_resolution_all_unresolved_but_health_green_means_bad_tickers():
    # Same empty {} result, but provider health is GREEN -- this must read
    # as "these tickers genuinely don't exist", not "provider is down".
    result = classify_ticker_resolution(
        {"ZZZ1", "ZZZ2"}, prices={}, provider_health_red=False,
    )
    assert result["validator_offline"] is False
    assert result["unresolved"] == ["ZZZ1", "ZZZ2"]


def test_classify_ticker_resolution_partial_resolution_reports_only_unresolved():
    result = classify_ticker_resolution(
        {"AAPL", "ZZZFAKE"},
        prices={"AAPL": {"price": 150.0}},
        provider_health_red=True,  # even if red, a PARTIAL resolution is not "offline"
    )
    assert result["validator_offline"] is False
    assert result["unresolved"] == ["ZZZFAKE"]


def test_classify_ticker_resolution_zero_or_none_price_is_unresolved():
    result = classify_ticker_resolution(
        {"AAPL"}, prices={"AAPL": {"price": None}}, provider_health_red=False,
    )
    assert result["unresolved"] == ["AAPL"]


# ── history_delta ─────────────────────────────────────────────────────────────

def test_history_delta_oldest_row_is_initial_capture():
    result = history_delta({"Tech": ["AAPL"]}, older_payload=None)
    assert result == {"added": [], "removed": [], "buckets_touched": [], "initial": True}


def test_history_delta_reports_added_and_removed():
    older = {"Cybersecurity": ["PANW", "CRWD"], "Consumer Tech": ["AAPL", "SQ"]}
    newer = {"Cybersecurity": ["PANW", "CRWD", "CYBR"], "Consumer Tech": ["AAPL"]}
    result = history_delta(newer, older)
    assert result["added"] == ["CYBR"]
    assert result["removed"] == ["SQ"]
    assert result["buckets_touched"] == ["Consumer Tech", "Cybersecurity"]
    assert result["initial"] is False


def test_history_delta_no_change_between_rows():
    same = {"Tech": ["AAPL", "MSFT"]}
    result = history_delta(same, dict(same))
    assert result["added"] == [] and result["removed"] == [] and result["buckets_touched"] == []


def test_history_delta_reorder_and_recase_alone_reports_no_delta():
    older = {"Tech": ["msft", "aapl"]}
    newer = {"Tech": ["AAPL", "MSFT"]}
    result = history_delta(newer, older)
    assert result["added"] == [] and result["removed"] == [] and result["buckets_touched"] == []
