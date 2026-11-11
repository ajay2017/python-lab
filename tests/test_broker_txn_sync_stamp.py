"""TJDate C2b: the `broker` cron's completed-transaction-sync stamp
(`snaptrade_config.last_txn_sync_at`) and the three db functions the Trade Journal's
Robinhood comparison reads.

The stamp is written ONLY when sub-job 2 of `cron_runner._run_broker` ran to the end
cleanly, so "compared with fills synced <date>" never overstates what was synced.
A failed pending-imports save is detected from `save_snaptrade_pending_imports`'s
return (the rows attempted: 0 while a txn-id'd record was to be saved = a failed
write). A failed stamp is bookkeeping only (a log line, never rc / failures), and
`last_full_sync_at` keeps its own behaviour.
"""
from pathlib import Path

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import db
from tests.test_broker_txn_sync_cron import _Res, _classified, _run

pytestmark = pytest.mark.fast

_NEW = [{"snaptrade_txn_id": "t1", "ticker": "X", "action": "BUY", "shares": 1,
         "price": 1.0, "trade_date": "2026-11-09", "raw_json": {}}]


# ── the cron stamp ───────────────────────────────────────────────────────────

def test_stamp_written_once_after_a_clean_pass_with_the_lane_start_time(monkeypatch):
    rc, rec = _run(monkeypatch, trades=pd.DataFrame(),
                   classified=_classified(new_pending=_NEW, linked_txn_ids=["t0"]), close_result=1)
    assert rc == 0 and rec["detail"] is None
    assert len(rec["stamp"]) == 1
    stamped = pd.Timestamp(rec["stamp"][0])
    assert stamped.tzinfo is not None                       # an ET-aware ISO string, like last_full_sync_at
    assert stamped.utcoffset().total_seconds() in (-4 * 3600, -5 * 3600)


def test_stamp_written_on_a_clean_pass_with_nothing_to_save(monkeypatch):
    _rc, rec = _run(monkeypatch, trades=pd.DataFrame())     # nothing pending, nothing linked
    assert len(rec["stamp"]) == 1


def test_stamp_withheld_when_the_journal_is_unreadable(monkeypatch):
    rc, rec = _run(monkeypatch, trades=None)
    assert rc == 1 and rec["stamp"] == []


def test_stamp_withheld_when_snaptrade_activities_are_unavailable(monkeypatch):
    rc, rec = _run(monkeypatch, trades=pd.DataFrame(), classify_none=True)
    assert rc == 1 and rec["stamp"] == []
    assert "activities unavailable" in rec["detail"]


def test_stamp_withheld_when_the_pending_save_failed(monkeypatch, capsys):
    rc, rec = _run(monkeypatch, trades=pd.DataFrame(),
                   classified=_classified(new_pending=_NEW), save_pending_result=0)
    assert rec["stamp"] == []
    assert rc == 0            # the save has never failed the lane; the withheld stamp is the signal
    assert "stamp withheld" in capsys.readouterr().out


def test_a_record_without_a_txn_id_is_not_counted_as_a_failed_save(monkeypatch):
    noid = [{"snaptrade_txn_id": None, "ticker": "X", "action": "BUY", "shares": 1,
             "price": 1.0, "trade_date": "2026-11-09"}]
    _rc, rec = _run(monkeypatch, trades=pd.DataFrame(),
                    classified=_classified(new_pending=noid), save_pending_result=0)
    assert len(rec["stamp"]) == 1                           # db skips id-less records: nothing to save


def test_stamp_withheld_when_closing_linked_pending_imports_failed(monkeypatch):
    rc, rec = _run(monkeypatch, trades=pd.DataFrame(),
                   classified=_classified(linked_txn_ids=["t1"]), close_result=None)
    assert rc == 1 and rec["stamp"] == []


def test_a_failed_stamp_is_bookkeeping_only(monkeypatch, capsys):
    rc, rec = _run(monkeypatch, trades=pd.DataFrame(), stamp_result=False)
    assert rc == 0 and rec["detail"] is None and len(rec["stamp"]) == 1
    assert "stamp not saved" in capsys.readouterr().out
    rc, rec = _run(monkeypatch, trades=pd.DataFrame(), stamp_result=RuntimeError("boom"))
    assert rc == 0 and rec["detail"] is None and len(rec["stamp"]) == 1


def test_the_full_sync_stamp_is_unchanged_and_independent(monkeypatch):
    calls: list = []
    _rc, rec = _run(monkeypatch, trades=None, config_calls=calls)       # ② skipped
    assert len(calls) == 1 and calls[0]["status"] == "connected" and "last_full_sync_at" in calls[0]
    assert "last_txn_sync_at" not in calls[0] and rec["stamp"] == []
    calls.clear()
    _rc, rec = _run(monkeypatch, trades=pd.DataFrame(), config_calls=calls)
    assert len(calls) == 1 and "last_txn_sync_at" not in calls[0] and len(rec["stamp"]) == 1


# ── db: the three C2b functions, against a fake client ───────────────────────

class _Q:
    """A fake PostgREST builder: records the chain, serves `rows` (paged by range)."""

    def __init__(self, rows, log, boom=False):
        self._rows, self._log, self._boom = rows, log, boom
        self._range = None

    def select(self, cols):
        self._log.append(("select", cols))
        return self

    def eq(self, col, val):
        self._log.append(("eq", col, val))
        return self

    def gte(self, col, val):
        self._log.append(("gte", col, val))
        return self

    def order(self, col, desc=False):
        self._log.append(("order", col, desc))
        return self

    def limit(self, n):
        self._log.append(("limit", n))
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def upsert(self, record, on_conflict=None):
        self._log.append(("upsert", record, on_conflict))
        return self

    def execute(self):
        if self._boom:
            raise RuntimeError("boom")
        if self._range is not None:
            a, b = self._range
            return _Res(self._rows[a:b + 1])
        return _Res(self._rows)


class _QClient:
    def __init__(self, rows=None, boom=False):
        self.rows, self.boom, self.log, self.tables = rows or [], boom, [], []

    def table(self, name):
        self.tables.append(name)
        return _Q(self.rows, self.log, self.boom)


def _use(monkeypatch, client, *, readonly=False, has_db=True):
    monkeypatch.setattr(db, "has_db", lambda: has_db)
    monkeypatch.setattr(db, "is_readonly", lambda: readonly)
    monkeypatch.setattr(db, "_client", lambda: client)
    return client


def test_load_txn_synced_at_returns_a_tz_aware_datetime_and_selects_only_its_column(monkeypatch):
    c = _use(monkeypatch, _QClient([{"last_txn_sync_at": "2026-11-10T17:00:00+00:00"}]))
    got = db.load_snaptrade_txn_synced_at_or_none()
    assert got.tzinfo is not None and got.isoformat() == "2026-11-10T17:00:00+00:00"
    assert ("select", "last_txn_sync_at") in c.log           # never added to load_snaptrade_config
    assert ("eq", "id", 1) in c.log and c.tables == ["snaptrade_config"]


def test_load_txn_synced_at_converts_an_offset_timestamp_to_the_same_instant(monkeypatch):
    _use(monkeypatch, _QClient([{"last_txn_sync_at": "2026-11-10T12:00:00.123456-05:00"}]))
    got = db.load_snaptrade_txn_synced_at_or_none()
    assert got is not None and got.tzinfo is not None
    assert (got.hour, got.minute) == (17, 0)                 # 12:00 EST is 17:00 UTC


def test_load_txn_synced_at_is_none_for_every_unreadable_state(monkeypatch):
    _use(monkeypatch, _QClient([]))
    assert db.load_snaptrade_txn_synced_at_or_none() is None            # no row
    _use(monkeypatch, _QClient([{"last_txn_sync_at": None}]))
    assert db.load_snaptrade_txn_synced_at_or_none() is None            # null
    _use(monkeypatch, _QClient([{"last_txn_sync_at": "garbage"}]))
    assert db.load_snaptrade_txn_synced_at_or_none() is None            # unparseable
    _use(monkeypatch, _QClient([{"last_txn_sync_at": "2026-11-10T17:00:00+00:00"}], boom=True))
    assert db.load_snaptrade_txn_synced_at_or_none() is None            # column missing / any error
    _use(monkeypatch, _QClient(), has_db=False)
    assert db.load_snaptrade_txn_synced_at_or_none() is None            # no DB


def test_the_config_loader_does_not_select_the_new_column():
    import inspect
    assert "last_txn_sync_at" not in inspect.getsource(db.load_snaptrade_config)


def test_save_txn_synced_at_upserts_only_that_column(monkeypatch):
    c = _use(monkeypatch, _QClient())
    assert db.save_snaptrade_txn_synced_at("2026-11-10T12:00:00-05:00") is True
    assert c.log == [("upsert", {"id": 1, "last_txn_sync_at": "2026-11-10T12:00:00-05:00"}, "id")]
    assert c.tables == ["snaptrade_config"]


def test_save_txn_synced_at_false_on_readonly_no_db_and_errors(monkeypatch):
    def _no_client():
        raise AssertionError("must not reach the DB")
    monkeypatch.setattr(db, "_client", _no_client)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    assert db.save_snaptrade_txn_synced_at("x") is False
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.save_snaptrade_txn_synced_at("x") is False
    _use(monkeypatch, _QClient(boom=True))
    assert db.save_snaptrade_txn_synced_at("x") is False


def _pend(i):
    return {"id": i, "snaptrade_txn_id": f"T{i}", "ticker": "OKTA", "action": "BUY", "shares": 3,
            "price": 189.14, "trade_date": "2026-11-02", "raw_json": {"price": 189.14}}


def test_load_pending_fills_filters_status_and_date_and_pages(monkeypatch):
    c = _use(monkeypatch, _QClient([_pend(i) for i in range(1, 8)]))
    monkeypatch.setattr(db, "_PENDING_FILLS_PAGE_SIZE", 3)
    got = db.load_snaptrade_pending_fills_or_none("2026-11-03")
    assert [r["id"] for r in got] == list(range(1, 8))                  # every page read
    assert ("eq", "status", "pending") in c.log and ("gte", "trade_date", "2026-11-03") in c.log
    sel = [x for x in c.log if x[0] == "select"][0][1]
    assert sel == "id,snaptrade_txn_id,ticker,action,shares,price,trade_date,raw_json"
    assert set(c.tables) == {"snaptrade_pending_imports"}


def test_load_pending_fills_empty_is_a_list_and_failure_is_none(monkeypatch):
    _use(monkeypatch, _QClient([]))
    assert db.load_snaptrade_pending_fills_or_none("2026-11-03") == []   # read, nothing pending
    _use(monkeypatch, _QClient([_pend(1)], boom=True))
    assert db.load_snaptrade_pending_fills_or_none("2026-11-03") is None
    _use(monkeypatch, _QClient(), has_db=False)
    assert db.load_snaptrade_pending_fills_or_none("2026-11-03") is None


def test_load_pending_fills_is_strict_unlike_the_lenient_loader(monkeypatch):
    _use(monkeypatch, _QClient([_pend(1)], boom=True))
    assert db.load_snaptrade_pending_imports("pending") == []            # lenient: [] on failure
    assert db.load_snaptrade_pending_fills_or_none("2026-11-03") is None  # strict: None


def test_pending_fills_loader_output_feeds_the_pure_checks(monkeypatch):
    from datetime import date
    from stock_analyzer import trade_entry_checks as tec
    _use(monkeypatch, _QClient([_pend(1)]))
    rows = db.load_snaptrade_pending_fills_or_none("2026-11-03")
    fills = tec.unlinked_fills(rows, pd.DataFrame({"ticker": [], "broker_txn_id": []}))
    f = tec.pending_fill_findings(
        {"ticker": "OKTA", "action": "BUY", "shares": 3, "price": 171.88,
         "trade_date": date(2026, 11, 2)}, fills)
    assert [x["kind"] for x in f] == ["PRICE_DIFFERS"]


def test_the_embedded_ddl_and_the_sql_file_carry_the_column():
    src = Path(db.__file__).read_text(encoding="utf-8")
    assert "last_txn_sync_at          timestamptz" in src
    sql = (Path(db.__file__).resolve().parents[1] / "docs" / "sql" /
           "snaptrade-config-txn-sync.sql").read_text(encoding="utf-8")
    assert "add column if not exists last_txn_sync_at timestamptz" in sql
    assert "row level security" not in sql.lower()


# ── db: the TJDate C5b all-status fills loader ───────────────────────────────

def _fill(i, status):
    return {"id": i, "snaptrade_txn_id": f"T{i}", "ticker": "OKTA", "action": "BUY", "shares": 3,
            "price": 189.14, "trade_date": "2026-11-02", "raw_json": {"price": 189.14},
            "status": status}


def test_load_fills_has_no_status_filter_selects_status_and_pages(monkeypatch):
    rows = [_fill(i, s) for i, s in enumerate(
        ["pending", "dismissed", "logged", "pending", "dismissed", "logged", "pending"], start=1)]
    c = _use(monkeypatch, _QClient(rows))
    monkeypatch.setattr(db, "_PENDING_FILLS_PAGE_SIZE", 3)
    got = db.load_snaptrade_fills_or_none("2026-11-03")
    assert [r["id"] for r in got] == list(range(1, 8))                  # every page, every status
    assert not [x for x in c.log if x[0] == "eq"]                       # no status (or any) filter
    assert ("gte", "trade_date", "2026-11-03") in c.log
    sel = [x for x in c.log if x[0] == "select"][0][1]
    assert sel == "id,snaptrade_txn_id,ticker,action,shares,price,trade_date,raw_json,status"
    assert ("order", "trade_date", True) in c.log and ("order", "id", True) in c.log
    assert set(c.tables) == {"snaptrade_pending_imports"}


def test_load_fills_empty_is_a_list_and_every_failure_is_none(monkeypatch):
    _use(monkeypatch, _QClient([]))
    assert db.load_snaptrade_fills_or_none("2026-11-03") == []           # read, no fills
    _use(monkeypatch, _QClient([_fill(1, "pending")], boom=True))
    assert db.load_snaptrade_fills_or_none("2026-11-03") is None         # a page failed
    _use(monkeypatch, _QClient(), has_db=False)
    assert db.load_snaptrade_fills_or_none("2026-11-03") is None         # no DB


def test_load_fills_leaves_the_pending_only_loader_unchanged(monkeypatch):
    c = _use(monkeypatch, _QClient([_pend(1)]))
    db.load_snaptrade_pending_fills_or_none("2026-11-03")
    assert ("eq", "status", "pending") in c.log                          # C2b still pending-only
    sel = [x for x in c.log if x[0] == "select"][0][1]
    assert "status" not in sel.split(",")


def test_fills_loader_output_feeds_the_matcher(monkeypatch):
    from datetime import datetime
    from stock_analyzer import journal_broker_match as jbm
    from stock_analyzer.market_time import ET
    _use(monkeypatch, _QClient([_fill(1, "dismissed")]))
    rows = db.load_snaptrade_fills_or_none("2026-10-28")
    journal = pd.DataFrame([{"id": 1, "ticker": "OKTA", "action": "BUY", "shares": 3, "price": 171.88,
                             "traded_at": "2026-11-02T22:08:00Z", "broker_txn_id": None, "notes": None}])
    now = ET.localize(datetime(2026, 11, 12, 12, 0))
    res = jbm.match_journal_to_broker(journal, rows, now, now)
    assert [f["kind"] for f in res] == ["PRICE_FIX"] and res[0]["fill_statuses"] == ["dismissed"]
