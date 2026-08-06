"""Tests for stock_analyzer/db.py::save_portfolio_thesis()/load_portfolio_thesis()
— the "State of the Portfolio" standing-thesis persistence (see
docs/plans/state-of-portfolio-standing-thesis.md). Mirrors the fake-client
harness pattern in tests/test_db_save_exit_signals.py.

Pre-DDL / offline-safe degrade is the critical invariant here: this table
needs a manually-applied DDL the user hasn't run yet in some environments —
load_portfolio_thesis() must return [] (never raise) on a
relation-does-not-exist-style failure, exactly like judgment_opinions/
analyst_target_snapshots already do.
"""
import pytest

from stock_analyzer import db


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeUpsertBuilder:
    def __init__(self, store, records):
        self._store = store
        self._records = records if isinstance(records, list) else [records]

    def execute(self):
        for r in self._records:
            key = (r.get("iso_year"), r.get("iso_week"))
            self._store[key] = dict(r)
        return _FakeExecResult([])


class _FakeSelectBuilder:
    """Mimics .select("*").gte(...).order(...).execute() for load_portfolio_thesis."""

    def __init__(self, store, raise_on_execute=False):
        self._store = store
        self._raise_on_execute = raise_on_execute
        self._cutoff = None

    def gte(self, col, value):
        self._cutoff = value
        return self

    def order(self, col, desc=False):
        return self

    def execute(self):
        if self._raise_on_execute:
            raise RuntimeError('relation "portfolio_thesis" does not exist')
        rows = list(self._store.values())
        if self._cutoff is not None:
            rows = [r for r in rows if str(r.get("thesis_date", "")) >= self._cutoff]
        return _FakeExecResult(rows)


class _FakeTable:
    def __init__(self, store, raise_on_select=False):
        self._store = store
        self._raise_on_select = raise_on_select

    def upsert(self, records, on_conflict=None):
        assert on_conflict == "iso_year,iso_week"
        return _FakeUpsertBuilder(self._store, records)

    def select(self, cols):
        return _FakeSelectBuilder(self._store, raise_on_execute=self._raise_on_select)


class _FakeClient:
    def __init__(self, raise_on_select=False):
        self.store: dict[tuple, dict] = {}
        self._raise_on_select = raise_on_select

    def table(self, name):
        assert name == "portfolio_thesis"
        return _FakeTable(self.store, raise_on_select=self._raise_on_select)


def _record(iso_year=2026, iso_week=32, **overrides):
    rec = {
        "v": 1,
        "thesis_date": "2026-08-06",
        "iso_year": iso_year,
        "iso_week": iso_week,
        "claims": {"risk_posture": "Monitor"},
        "prose": "As of Aug 06, your portfolio sits in Monitor posture.",
    }
    rec.update(overrides)
    return rec


@pytest.fixture(autouse=True)
def _patch_db_flags(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    yield


def _install(fake):
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake


def _teardown():
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = None


# ── save_portfolio_thesis ────────────────────────────────────────────────────

def test_save_writes_row_keyed_by_iso_year_week():
    fake = _FakeClient()
    _install(fake)
    try:
        ok = db.save_portfolio_thesis(_record())
        assert ok is True
        row = fake.store[(2026, 32)]
        assert row["schema_version"] == 1
        assert row["thesis_date"] == "2026-08-06"
        assert isinstance(row["claims"], str)  # json.dumps'd before write
    finally:
        _teardown()


def test_save_second_write_same_iso_week_overwrites_not_duplicates():
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_portfolio_thesis(_record(prose="first"))
        db.save_portfolio_thesis(_record(prose="second"))
        assert len(fake.store) == 1
        assert fake.store[(2026, 32)]["prose"] == "second"
    finally:
        _teardown()


def test_save_empty_record_is_noop():
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_portfolio_thesis({}) is False
        assert fake.store == {}
    finally:
        _teardown()


def test_save_readonly_is_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_portfolio_thesis(_record()) is False
        assert fake.store == {}
    finally:
        _teardown()


def test_save_no_db_is_noop(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_portfolio_thesis(_record()) is False
        assert fake.store == {}
    finally:
        _teardown()


def test_save_never_raises_on_db_failure():
    class _RaisingClient:
        def table(self, name):
            raise RuntimeError("DB offline")
    _install(_RaisingClient())
    try:
        assert db.save_portfolio_thesis(_record()) is False
    finally:
        _teardown()


# ── load_portfolio_thesis — offline / pre-DDL safe degrade ──────────────────

def test_load_returns_empty_list_when_table_does_not_exist():
    fake = _FakeClient(raise_on_select=True)
    _install(fake)
    try:
        assert db.load_portfolio_thesis(14) == []
    finally:
        _teardown()


def test_load_returns_empty_list_when_no_db():
    import stock_analyzer.db as _db_mod
    orig_has_db = _db_mod.has_db
    _db_mod.has_db = lambda: False
    try:
        assert db.load_portfolio_thesis(14) == []
    finally:
        _db_mod.has_db = orig_has_db


def test_load_returns_rows_most_recent_first_shape():
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_portfolio_thesis(_record(iso_year=2026, iso_week=31, thesis_date="2026-07-30"))
        db.save_portfolio_thesis(_record(iso_year=2026, iso_week=32, thesis_date="2026-08-06"))
        rows = db.load_portfolio_thesis(14)
        assert len(rows) == 2
        iso_weeks = {r["iso_week"] for r in rows}
        assert iso_weeks == {31, 32}
    finally:
        _teardown()


def test_load_never_raises_on_generic_exception():
    class _RaisingClient:
        def table(self, name):
            raise RuntimeError("boom")
    _install(_RaisingClient())
    try:
        assert db.load_portfolio_thesis(14) == []
    finally:
        _teardown()
