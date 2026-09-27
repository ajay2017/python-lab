"""stock_analyzer/db.py::load_broker_position_snapshot / save_broker_position_
snapshot — the `kinds` jsonb column (Phase 1 ETF support, F-279 §11).

Load-bearing invariants:
  - `kinds` is additive to `positions` — a missing/empty kinds map means
    "kind unknown for these tickers", NOT "the whole snapshot is unknown"
    (that's still governed by `positions is None`).
  - Pre-DDL (the `kinds` column doesn't exist in Supabase yet), BOTH the
    read and the write must degrade gracefully to exactly the same
    behaviour this feature had before Phase 1 — never break the existing
    drift-warning feature just because an unrelated column was added.
"""
from stock_analyzer import db
import pytest

pytestmark = pytest.mark.fast


# ── load_broker_position_snapshot ────────────────────────────────────────────

class _FakeSnapExec:
    def __init__(self, data): self.data = data


class _FakeSnapBuilder:
    def __init__(self, rows, select_raises=None):
        self._rows = rows
        self._select_raises = select_raises
        self._selected_cols = None

    def select(self, cols):
        self._selected_cols = cols
        if self._select_raises is not None and "kinds" in cols:
            raise self._select_raises
        return self

    def eq(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self

    def execute(self):
        return _FakeSnapExec(self._rows)


class _FakeSnapClient:
    def __init__(self, rows, select_raises=None):
        self._rows, self._select_raises = rows, select_raises

    def table(self, _name):
        return _FakeSnapBuilder(self._rows, self._select_raises)


def test_load_includes_kinds_when_present(monkeypatch):
    rows = [{"positions": {"SPY": 10.0}, "account_ids": ["a"],
             "all_accounts_ok": True, "captured_at": "2026-09-27T00:00:00Z",
             "kinds": {"SPY": "etf"}}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeSnapClient(rows))
    out = db.load_broker_position_snapshot()
    assert out is not None
    assert out["kinds"] == {"SPY": "etf"}


def test_load_kinds_defaults_to_empty_dict_when_null(monkeypatch):
    rows = [{"positions": {"SPY": 10.0}, "account_ids": None,
             "all_accounts_ok": True, "captured_at": "2026-09-27T00:00:00Z",
             "kinds": None}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeSnapClient(rows))
    out = db.load_broker_position_snapshot()
    assert out is not None
    assert out["kinds"] == {}


def test_load_predll_missing_kinds_column_falls_back_to_original_select(monkeypatch):
    """The column doesn't exist yet in Supabase — the wide select raises, the
    function must degrade to the ORIGINAL column list rather than returning
    None (which would look identical to the whole snapshot being unknown)."""
    rows = [{"positions": {"SPY": 10.0}, "account_ids": ["a"],
             "all_accounts_ok": True, "captured_at": "2026-09-27T00:00:00Z"}]
    exc = Exception("Could not find the 'kinds' column of 'broker_position_snapshot' in the schema cache")
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeSnapClient(rows, select_raises=exc))
    out = db.load_broker_position_snapshot()
    assert out is not None
    assert out["positions"] == {"SPY": 10.0}
    assert out["kinds"] == {}


def test_load_positions_none_still_returns_none_unaffected_by_kinds(monkeypatch):
    rows = [{"positions": None, "kinds": {"SPY": "etf"}}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeSnapClient(rows))
    assert db.load_broker_position_snapshot() is None


def test_load_unrelated_select_error_still_propagates_to_none(monkeypatch):
    exc = Exception("connection refused")
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeSnapClient([], select_raises=exc))
    assert db.load_broker_position_snapshot() is None


# ── save_broker_position_snapshot ────────────────────────────────────────────

class _FakeSaveSnapBuilder:
    def __init__(self, calls, raise_exc=None, raise_on_call=1):
        self._calls, self._raise_exc, self._raise_on_call = calls, raise_exc, raise_on_call

    def upsert(self, row, on_conflict=None):
        self._calls.append(dict(row))
        return self

    def execute(self):
        if self._raise_exc is not None and len(self._calls) == self._raise_on_call:
            raise self._raise_exc
        return None


class _FakeSaveSnapClient:
    def __init__(self, raise_exc=None, raise_on_call=1):
        self.calls: list = []
        self._raise_exc, self._raise_on_call = raise_exc, raise_on_call

    def table(self, _name):
        return _FakeSaveSnapBuilder(self.calls, self._raise_exc, self._raise_on_call)


@pytest.fixture(autouse=True)
def _patch_save(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)


def test_save_includes_kinds_when_given(monkeypatch):
    fake = _FakeSaveSnapClient()
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        ok = db.save_broker_position_snapshot({"SPY": 10.0}, kinds={"SPY": "etf"})
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    assert fake.calls[0]["kinds"] == {"SPY": "etf"}


def test_save_omits_kinds_key_entirely_when_none():
    fake = _FakeSaveSnapClient()
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        ok = db.save_broker_position_snapshot({"SPY": 10.0})
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    assert "kinds" not in fake.calls[0]


def test_save_missing_kinds_column_degrades_and_retries():
    exc = Exception(
        "Could not find the 'kinds' column of 'broker_position_snapshot' in the schema cache"
    )
    fake = _FakeSaveSnapClient(raise_exc=exc, raise_on_call=1)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        ok = db.save_broker_position_snapshot({"SPY": 10.0}, kinds={"SPY": "etf"})
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    assert len(fake.calls) == 2
    assert "kinds" in fake.calls[0]
    assert "kinds" not in fake.calls[1]


def test_save_none_positions_never_writes():
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = _FakeSaveSnapClient()
    try:
        ok = db.save_broker_position_snapshot(None, kinds={"SPY": "etf"})
    finally:
        _db_mod._CLIENT = None
    assert ok is False
