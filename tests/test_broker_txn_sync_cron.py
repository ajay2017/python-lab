"""TJDate C4 — the `broker` cron lane's transaction sub-job (②) and the
`db.close_linked_pending_imports` write it uses.

Two defects closed here (HoldDup, docs/bug-register.md):
  * the sub-job read the journal with the LENIENT `db.load_trades()`, which
    returns an EMPTY frame on a read failure — every broker fill in the 90-day
    window then looked unlogged and was re-queued as a pending import. It now
    uses `db.load_trades_or_none()` and skips the whole sub-job on None.
  * a pending row whose fill was later linked to a journal row (Tier 1 id on a
    trades row, or a Tier-2 backfill) stayed "pending" forever; logging it would
    double-count holdings. The sub-job now closes those rows.
"""
import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import db

pytestmark = pytest.mark.fast

_ACCOUNTS = [{"id": "individual", "name": "Individual"}]


def _classified(**over):
    base = {
        "new_pending": [],
        "backfill_broker_txn_id": [],
        "income_events": [],
        "flows": [],
        "ignored": {},
        "linked_txn_ids": [],
    }
    base.update(over)
    return base


def _run(monkeypatch, *, trades, classified=None, backfill_ok=None, close_result=0):
    """Drive `_run_broker` through sub-job ②. `trades` is what
    `db.load_trades_or_none` returns. Returns (rc, recorders)."""
    rec = {"classify": [], "save_pending": [], "backfill": [], "close": []}
    backfill_ok = backfill_ok or {}

    monkeypatch.setattr(cr.snaptrade_client, "has_snaptrade", lambda: True)
    monkeypatch.setattr(cr.snaptrade_client, "list_accounts", lambda: _ACCOUNTS)
    monkeypatch.setattr(cr.snaptrade_client, "get_account_positions",
                        lambda aid: [{"instrument": {"kind": "stock", "symbol": "AAA"}, "units": 5.0}])
    monkeypatch.setattr(cr.snaptrade_client, "get_account_balance", lambda _a: {"cash": 1.0})
    monkeypatch.setattr(cr.snaptrade_client, "get_account_activities", lambda *_a, **_k: [])
    monkeypatch.setattr(cr.broker_sync, "map_balances_to_cash",
                        lambda raw: {"cash_balance": 1.0, "note": "test"})
    monkeypatch.setattr(cr.db, "save_broker_position_snapshot", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_account_cash", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_snaptrade_config", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "load_snaptrade_income_events", lambda *_a, **_k: [])
    monkeypatch.setattr(cr.db, "load_trades_or_none", lambda: trades)
    monkeypatch.setattr(cr.db, "save_account_flows", lambda *_a, **_k: 0)
    monkeypatch.setattr(cr.db, "save_snaptrade_income_events", lambda *_a, **_k: 0)

    def _classify(raw, existing, existing_income_events=None):
        rec["classify"].append(existing)
        return classified if classified is not None else _classified()

    def _save_pending(rows):
        rec["save_pending"].append(rows)
        return len(rows)

    def _backfill(trade_id, txn_id):
        rec["backfill"].append((trade_id, txn_id))
        return backfill_ok.get(txn_id, True)

    def _close(ids):
        rec["close"].append(list(ids))
        return close_result

    monkeypatch.setattr(cr.broker_sync, "classify_transactions", _classify)
    monkeypatch.setattr(cr.db, "save_snaptrade_pending_imports", _save_pending)
    monkeypatch.setattr(cr.db, "backfill_trade_broker_txn_id", _backfill)
    monkeypatch.setattr(cr.db, "close_linked_pending_imports", _close)
    monkeypatch.setattr(cr, "_db_unavailable_detail", lambda: None)
    monkeypatch.setattr(cr, "_notify_broker_failure", lambda *_a, **_k: None)
    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", None)

    rc = cr._run_broker(cr.datetime.now(cr._ET), force=True)
    rec["detail"] = cr._LAST_LANE_FAILURE_DETAIL
    return rc, rec


# ── the strict journal read ──────────────────────────────────────────────────

def test_unreadable_trades_skips_classify_and_every_write(monkeypatch):
    """THE LOAD-BEARING CASE: None = could not read. Classifying against a
    phantom-empty journal would re-queue every logged fill as pending."""
    rc, rec = _run(monkeypatch, trades=None,
                   classified=_classified(new_pending=[{"snaptrade_txn_id": "x"}]))
    assert rec["classify"] == []
    assert rec["save_pending"] == []
    assert rec["backfill"] == []
    assert rec["close"] == []
    assert rc == 1
    assert "trades table unreadable" in rec["detail"]
    assert "re-queued as pending" in rec["detail"]


def test_empty_journal_frame_is_a_real_result_and_proceeds(monkeypatch):
    rc, rec = _run(monkeypatch, trades=pd.DataFrame())
    assert len(rec["classify"]) == 1
    assert rec["save_pending"] == [[]]
    assert rc == 0
    assert rec["detail"] is None


def test_the_lane_uses_the_strict_loader_not_the_lenient_one(monkeypatch):
    called = []
    monkeypatch.setattr(cr.db, "load_trades", lambda: called.append("lenient") or pd.DataFrame())
    _run(monkeypatch, trades=pd.DataFrame())
    assert called == []


# ── closing stale pending rows ───────────────────────────────────────────────

def test_close_gets_tier1_ids_plus_successful_backfill_ids_only(monkeypatch):
    classified = _classified(
        linked_txn_ids=["t1-linked"],
        backfill_broker_txn_id=[
            {"trade_id": 11, "broker_txn_id": "t2-ok"},
            {"trade_id": 12, "broker_txn_id": "t3-failed"},
        ],
    )
    rc, rec = _run(monkeypatch, trades=pd.DataFrame(), classified=classified,
                   backfill_ok={"t3-failed": False}, close_result=2)
    assert rec["backfill"] == [(11, "t2-ok"), (12, "t3-failed")]
    assert rec["close"] == [["t1-linked", "t2-ok"]]      # the failed backfill is excluded
    assert rc == 0


def test_close_ids_are_deduplicated_preserving_order(monkeypatch):
    classified = _classified(
        linked_txn_ids=["a", "b"],
        backfill_broker_txn_id=[{"trade_id": 1, "broker_txn_id": "b"},
                                {"trade_id": 2, "broker_txn_id": "c"}],
    )
    _rc, rec = _run(monkeypatch, trades=pd.DataFrame(), classified=classified)
    assert rec["close"] == [["a", "b", "c"]]


def test_close_not_called_when_nothing_is_linked(monkeypatch):
    _rc, rec = _run(monkeypatch, trades=pd.DataFrame(), classified=_classified())
    assert rec["close"] == []


def test_close_not_called_when_every_backfill_failed(monkeypatch):
    classified = _classified(backfill_broker_txn_id=[{"trade_id": 1, "broker_txn_id": "x"}])
    _rc, rec = _run(monkeypatch, trades=pd.DataFrame(), classified=classified,
                    backfill_ok={"x": False})
    assert rec["close"] == []


def test_close_returning_none_fails_the_lane(monkeypatch):
    rc, rec = _run(monkeypatch, trades=pd.DataFrame(),
                   classified=_classified(linked_txn_ids=["t1"]), close_result=None)
    assert rc == 1
    assert "couldn't close pending imports already in the journal" in rec["detail"]


def test_closed_count_appears_in_the_sync_log_line(monkeypatch, capsys):
    _run(monkeypatch, trades=pd.DataFrame(),
         classified=_classified(linked_txn_ids=["t1", "t2"]), close_result=2)
    assert "closed 2 linked pending" in capsys.readouterr().out


# ── db.close_linked_pending_imports ──────────────────────────────────────────

class _Res:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, log, raise_on_call=None):
        self._log = log
        self._raise_on = raise_on_call
        self._call = {}

    def update(self, payload):
        self._call = {"update": payload, "eq": [], "in": None}
        return self

    def eq(self, col, val):
        self._call["eq"].append((col, val))
        return self

    def in_(self, col, vals):
        self._call["in"] = (col, list(vals))
        return self

    def execute(self):
        self._log.append(self._call)
        if self._raise_on is not None and len(self._log) == self._raise_on:
            raise RuntimeError("boom")
        return _Res([{"id": i} for i in range(len(self._call["in"][1]))])


class _FakeClient:
    def __init__(self, raise_on_call=None):
        self.log = []
        self.tables = []
        self._raise_on = raise_on_call

    def table(self, name):
        self.tables.append(name)
        return _FakeTable(self.log, self._raise_on)


@pytest.fixture
def fake_db(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "_client", lambda: client)
    return client


def test_db_close_filters_on_status_pending_and_sets_logged(fake_db):
    n = db.close_linked_pending_imports(["a", "b"])
    assert n == 2
    assert fake_db.tables == ["snaptrade_pending_imports"]
    call = fake_db.log[0]
    assert call["update"] == {"status": "logged"}
    assert ("status", "pending") in call["eq"]          # never touches dismissed/logged
    assert call["in"] == ("snaptrade_txn_id", ["a", "b"])


def test_db_close_chunks_of_100(fake_db):
    ids = [f"t{i}" for i in range(250)]
    n = db.close_linked_pending_imports(ids)
    assert [len(c["in"][1]) for c in fake_db.log] == [100, 100, 50]
    assert n == 250
    assert all(("status", "pending") in c["eq"] for c in fake_db.log)


def test_db_close_exactly_100_ids_is_one_chunk(fake_db):
    db.close_linked_pending_imports([f"t{i}" for i in range(100)])
    assert len(fake_db.log) == 1


def test_db_close_empty_input_is_zero_and_makes_no_call(fake_db):
    assert db.close_linked_pending_imports([]) == 0
    assert db.close_linked_pending_imports(None) == 0
    assert fake_db.log == []


def test_db_close_readonly_viewer_is_a_noop_returning_zero(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    monkeypatch.setattr(db, "has_db", lambda: True)

    def _no_client():
        raise AssertionError("read-only viewer must not reach the DB")

    monkeypatch.setattr(db, "_client", _no_client)
    assert db.close_linked_pending_imports(["a"]) == 0


def test_db_close_without_a_db_is_none_not_zero(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.close_linked_pending_imports(["a"]) is None


def test_db_close_exception_in_any_chunk_returns_none(monkeypatch):
    client = _FakeClient(raise_on_call=2)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "_client", lambda: client)
    assert db.close_linked_pending_imports([f"t{i}" for i in range(150)]) is None
