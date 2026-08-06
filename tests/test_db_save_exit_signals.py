"""Regression tests for stock_analyzer/db.py::save_exit_signals_batch() —
2026-08-05 bug fix (Bug 3 of the exit_signals price-capture audit).

Prior to this fix, save_exit_signals_batch() did a plain PostgREST upsert on
(ticker, signal_date, signal_type) with no coalesce: if a same-day Brief
rebuild produced a row with a NULL in a nullable column (e.g. price_at_signal
dropped because an upstream field wasn't carried forward — see Bug 1/2 of the
same audit, daily_briefing.py / exit_advisor.py), that NULL would silently
overwrite a previously-captured non-null value on the next save. The fix adds
a coalesce-on-write merge inside save_exit_signals_batch() itself (the single
choke point both app.py and cron_runner.py go through): before the upsert, it
reads any existing rows for the batch's keys and fills an incoming NULL from
the existing non-null value — a genuine new non-null value still overwrites
(last-non-null-wins).
"""
import pytest

from stock_analyzer import db


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeSelectBuilder:
    """Mimics the .select(cols).in_(...).in_(...).in_(...).execute() chain
    save_exit_signals_batch() uses for its pre-read."""

    def __init__(self, store):
        self._store = store
        self._filters: dict[str, set] = {}

    def in_(self, col, values):
        self._filters[col] = {str(v) for v in values}
        return self

    def execute(self):
        rows = []
        for row in self._store.values():
            if all(str(row.get(col)) in vals for col, vals in self._filters.items()):
                rows.append(dict(row))
        return _FakeExecResult(rows)


class _FakeUpsertBuilder:
    """Mimics a real PostgREST upsert: the incoming record REPLACES the
    matched row wholesale (this is exactly why a NULL in the incoming record
    clobbers a prior non-null value absent the caller-side merge)."""

    def __init__(self, store, records):
        self._store = store
        self._records = records

    def execute(self):
        for r in self._records:
            key = (r.get("ticker"), str(r.get("signal_date")), r.get("signal_type"))
            self._store[key] = dict(r)
        return _FakeExecResult([])


class _FakeTable:
    def __init__(self, store, raise_on_select=False):
        self._store = store
        self._raise_on_select = raise_on_select

    def select(self, cols):
        if self._raise_on_select:
            raise RuntimeError("DB offline")
        return _FakeSelectBuilder(self._store)

    def upsert(self, records, on_conflict=None):
        return _FakeUpsertBuilder(self._store, records)


class _FakeClient:
    def __init__(self, raise_on_select=False):
        self.store: dict[tuple, dict] = {}
        self._raise_on_select = raise_on_select

    def table(self, name):
        assert name == "exit_signals"
        return _FakeTable(self.store, raise_on_select=self._raise_on_select)


def _sig(ticker="AAPL", signal_date="2026-08-05", signal_type="TRIM", **overrides):
    row = {
        "ticker": ticker, "signal_date": signal_date, "signal_type": signal_type,
        "composite_score": None, "price_at_signal": None, "dd_from_peak_pct": None,
        "pnl_pct": None, "below_ma_count": None, "rel_strength": None,
    }
    row.update(overrides)
    return row


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


# ── The critical invariant: a NULL write must never clobber a persisted
# non-null value; a genuine non-null write still overwrites ────────────────

def test_price_at_signal_not_clobbered_by_later_null_write():
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_exit_signals_batch([_sig(price_at_signal=100)])
        db.save_exit_signals_batch([_sig(price_at_signal=None)])
        key = ("AAPL", "2026-08-05", "TRIM")
        assert fake.store[key]["price_at_signal"] == 100
    finally:
        _teardown()


def test_price_at_signal_null_first_then_real_value_persists():
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_exit_signals_batch([_sig(price_at_signal=None)])
        db.save_exit_signals_batch([_sig(price_at_signal=100)])
        key = ("AAPL", "2026-08-05", "TRIM")
        assert fake.store[key]["price_at_signal"] == 100
    finally:
        _teardown()


def test_price_at_signal_last_non_null_wins_not_first_value_sticks_forever():
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_exit_signals_batch([_sig(price_at_signal=100)])
        db.save_exit_signals_batch([_sig(price_at_signal=150)])
        key = ("AAPL", "2026-08-05", "TRIM")
        assert fake.store[key]["price_at_signal"] == 150
    finally:
        _teardown()


def test_null_protection_is_generic_across_nullable_columns_not_price_specific():
    """Same invariant, a different nullable column -- proves the merge is
    per-column/generic (iterates _EXIT_SIGNAL_NULLABLE_COLS), not a
    price_at_signal-only special case."""
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_exit_signals_batch([_sig(dd_from_peak_pct=-12.5)])
        db.save_exit_signals_batch([_sig(dd_from_peak_pct=None)])
        key = ("AAPL", "2026-08-05", "TRIM")
        assert fake.store[key]["dd_from_peak_pct"] == -12.5
    finally:
        _teardown()


def test_null_protection_does_not_cross_contaminate_different_columns():
    """A merge into one column must not smuggle values into an unrelated
    column on the same row."""
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_exit_signals_batch([_sig(price_at_signal=100, dd_from_peak_pct=-5.0)])
        db.save_exit_signals_batch([_sig(price_at_signal=None, dd_from_peak_pct=None,
                                          rel_strength=2.5)])
        key = ("AAPL", "2026-08-05", "TRIM")
        row = fake.store[key]
        assert row["price_at_signal"] == 100
        assert row["dd_from_peak_pct"] == -5.0
        assert row["rel_strength"] == 2.5  # the genuinely-new value, unaffected
    finally:
        _teardown()


def test_null_protection_scoped_to_the_exact_ticker_date_type_key():
    """A different (ticker, signal_date, signal_type) key must not donate its
    values into an unrelated row's merge."""
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_exit_signals_batch([_sig(ticker="AAPL", price_at_signal=100)])
        db.save_exit_signals_batch([_sig(ticker="MSFT", price_at_signal=None)])
        assert fake.store[("MSFT", "2026-08-05", "TRIM")]["price_at_signal"] is None
    finally:
        _teardown()


# ── Offline safety: pre-read failure must fall through to an as-is upsert,
# never lose the write ───────────────────────────────────────────────────────

def test_preread_failure_falls_through_to_upsert_as_is():
    fake = _FakeClient(raise_on_select=True)
    _install(fake)
    try:
        db.save_exit_signals_batch([_sig(price_at_signal=100)])
        key = ("AAPL", "2026-08-05", "TRIM")
        assert fake.store[key]["price_at_signal"] == 100  # write still landed
    finally:
        _teardown()


# ── Guard rails already covered by save_trade's pattern -- pin the
# equivalents here since this function has its own early-return guards ──────

def test_readonly_is_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_exit_signals_batch([_sig(price_at_signal=100)])
        assert fake.store == {}
    finally:
        _teardown()


def test_empty_batch_is_noop():
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_exit_signals_batch([])
        assert fake.store == {}
    finally:
        _teardown()


def test_no_db_is_noop(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_exit_signals_batch([_sig(price_at_signal=100)])
        assert fake.store == {}
    finally:
        _teardown()
