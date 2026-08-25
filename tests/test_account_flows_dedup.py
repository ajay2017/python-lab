"""Regression tests for the 2026-08-24 review finding: account_flows had no
dedup key, so the broker-sync cron (2x/day, 90-day rolling lookback) silently
re-inserted every real CONTRIBUTION/WITHDRAWAL on every run, inflating
net_contributed_capital and understating reported account growth%.

Fix: stock_analyzer.broker_sync.classify_transactions now threads the
SnapTrade activity id through each `flows` entry (mirroring how
`income_events` already does it), and a new stock_analyzer.db.save_account_flows()
upserts on that id via a partial unique index — mirroring
save_snaptrade_income_events() exactly, including dropping id-less rows
(can't dedup those). The pre-existing single-row db.add_account_flow() is
untouched; it's still used by the manual/baseline deposit-logging UI, which
has no SnapTrade id to carry.
"""
import pandas as pd
import pytest

from stock_analyzer import broker_sync, db


# ── broker_sync.classify_transactions threads the id through `flows` ────────

def _contribution(txn_id="act-1", amount=500.0, trade_date="2026-08-20"):
    return {"id": txn_id, "type": "CONTRIBUTION", "amount": amount,
            "trade_date": trade_date}


def test_flows_entry_carries_the_snaptrade_txn_id():
    result = broker_sync.classify_transactions([_contribution()], pd.DataFrame())
    assert result["flows"] == [{
        "snaptrade_txn_id": "act-1",
        "flow_type": "deposit",
        "amount": 500.0,
        "flow_date": "2026-08-20",
    }]


def test_flows_entry_id_is_none_when_snaptrade_omits_the_activity_id():
    txn = _contribution()
    txn["id"] = None
    result = broker_sync.classify_transactions([txn], pd.DataFrame())
    assert result["flows"][0]["snaptrade_txn_id"] is None


def test_withdrawal_also_carries_the_id():
    txn = {"id": "act-2", "type": "WITHDRAWAL", "amount": 200.0,
           "trade_date": "2026-08-21"}
    result = broker_sync.classify_transactions([txn], pd.DataFrame())
    assert result["flows"] == [{
        "snaptrade_txn_id": "act-2",
        "flow_type": "withdrawal",
        "amount": 200.0,
        "flow_date": "2026-08-21",
    }]


# ── db.save_account_flows dedups on snaptrade_txn_id ─────────────────────────

class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeUpsertBuilder:
    """A real PostgREST upsert with ignore_duplicates=True: a record whose
    conflict-target column already exists in the store is a no-op (the
    EXISTING row wins), not a merge and not a duplicate insert."""

    def __init__(self, store, records, on_conflict, ignore_duplicates):
        self._store = store
        self._records = records
        self._on_conflict = on_conflict
        self._ignore_duplicates = ignore_duplicates

    def execute(self):
        for r in self._records:
            key = r[self._on_conflict]
            if self._ignore_duplicates and key in self._store:
                continue
            self._store[key] = dict(r)
        return _FakeExecResult([])


class _FakeInsertBuilder:
    def __init__(self, store, record):
        self._store = store
        self._record = record

    def execute(self):
        self._store.setdefault("__inserts__", []).append(dict(self._record))
        return _FakeExecResult([])


class _FakeTable:
    def __init__(self, store):
        self._store = store

    def upsert(self, records, on_conflict=None, ignore_duplicates=False):
        return _FakeUpsertBuilder(self._store, records, on_conflict, ignore_duplicates)

    def insert(self, record):
        return _FakeInsertBuilder(self._store, record)


class _FakeClient:
    def __init__(self):
        self.store: dict = {}

    def table(self, name):
        assert name == "account_flows"
        return _FakeTable(self.store)


def _install(fake):
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake


def _teardown():
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = None


@pytest.fixture(autouse=True)
def _patch_db_flags(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    yield


def _flow(txn_id="act-1", flow_type="deposit", amount=500.0, flow_date="2026-08-20"):
    return {"snaptrade_txn_id": txn_id, "flow_type": flow_type,
            "amount": amount, "flow_date": flow_date}


def test_re_syncing_the_same_activity_does_not_duplicate():
    """THE bug this fix closes: re-scanning the same 90-day window on the
    next cron run must not re-insert an already-recorded deposit."""
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_account_flows([_flow()])
        db.save_account_flows([_flow()])  # same activity, re-fetched
        db.save_account_flows([_flow()])  # and again (2x/day cron)
        assert len(fake.store) == 1
    finally:
        _teardown()


def test_two_distinct_activities_both_persist():
    fake = _FakeClient()
    _install(fake)
    try:
        n = db.save_account_flows([_flow(txn_id="act-1"), _flow(txn_id="act-2")])
        assert n == 2
        assert len(fake.store) == 2
    finally:
        _teardown()


def test_id_less_rows_are_dropped_not_inserted():
    """Without a snaptrade_txn_id we cannot dedup a future re-fetch, so
    inserting it would reintroduce the exact bug this fix closes."""
    fake = _FakeClient()
    _install(fake)
    try:
        n = db.save_account_flows([_flow(txn_id=None)])
        assert n == 0
        assert fake.store == {}
    finally:
        _teardown()


def test_readonly_is_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_account_flows([_flow()])
        assert fake.store == {}
    finally:
        _teardown()


def test_empty_batch_is_noop():
    fake = _FakeClient()
    _install(fake)
    try:
        assert db.save_account_flows([]) == 0
        assert fake.store == {}
    finally:
        _teardown()


def test_no_db_is_noop(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    fake = _FakeClient()
    _install(fake)
    try:
        db.save_account_flows([_flow()])
        assert fake.store == {}
    finally:
        _teardown()


def test_client_exception_fails_soft_to_zero():
    """Inert until the manual DDL is applied (the conflict target column
    won't exist yet) — must not raise, matching save_snaptrade_income_events'
    convention."""
    class _BoomTable:
        def upsert(self, *_a, **_k):
            raise RuntimeError("column snaptrade_txn_id does not exist")

    class _BoomClient:
        def table(self, name):
            return _BoomTable()

    _install(_BoomClient())
    try:
        assert db.save_account_flows([_flow()]) == 0
    finally:
        _teardown()


def test_manual_add_account_flow_is_untouched_and_has_no_id():
    """The manual/baseline deposit-logging UI path (app.py's two call sites)
    goes through add_account_flow, not save_account_flows -- confirm it
    still works standalone with no snaptrade_txn_id involved."""
    fake = _FakeClient()
    _install(fake)
    try:
        ok = db.add_account_flow("2026-08-20", "baseline", 1000.0, "Baseline")
        assert ok is True
        assert fake.store["__inserts__"] == [{
            "flow_date": "2026-08-20", "flow_type": "baseline",
            "amount": 1000.0, "note": "Baseline",
        }]
    finally:
        _teardown()
