"""Tests for the App Settings reference-data layer — Commit 1 of 3
(docs/plans/app-settings.md): the pure `stock_analyzer.reference_data`
module, the new `stock_analyzer.db` `reference_tables`/
`reference_table_history` functions, and `reference_shelf`'s DB-first
`as_of` fallback for the three migrated tables.

Nothing exercised here is wired into any decision path yet -- `resolve_universe`
has zero callers in this commit; that's Commit 2.
"""
from __future__ import annotations

from datetime import date

import pytest

from stock_analyzer import db, reference_shelf
from stock_analyzer.constants import REFERENCE_SHELF_LIFE_DAYS
from stock_analyzer.reference_data import (
    ReferenceDataUnavailable,
    canonicalize,
    resolve_universe,
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
