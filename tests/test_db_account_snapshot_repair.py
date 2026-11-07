"""db.py N1 C3: account_snapshot_provenance_available and
apply_account_snapshot_repair (mocked client)."""
import pytest

from stock_analyzer import db

pytestmark = pytest.mark.fast

FIELDS = {
    "gross_book": 19635.55, "net_equity": 6873.33, "leverage": 2.86, "cushion": 1.0,
    "call_distance_pct": -10.0, "book_positions": {"AAA": {"shares": 10, "close": 50.0}},
    "journal_max_trade_id": 105, "original_gross_book": 15272.55,
    "repaired_at": "2026-11-01T12:00:00-04:00",
    # must never be written:
    "cash_balance": -1.0, "cash_as_of": "x", "maintenance_rate": 0.9,
}


class _Res:
    def __init__(self, data):
        self.data = data


class _Builder:
    def __init__(self, c):
        self.c, self.kind, self.eqs, self.cols = c, "select", [], None

    def select(self, cols="*"):
        self.kind, self.cols = "select", cols
        return self

    def update(self, payload):
        self.kind = "update"
        self.c.updates.append(dict(payload))
        return self

    def eq(self, k, v):
        self.eqs.append((k, v))
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self.kind == "select":
            self.c.selects.append(self.cols)
            if self.c.select_error:
                raise RuntimeError(self.c.select_error)
            return _Res(self.c.row_data)
        self.c.update_eqs.append(list(self.eqs))
        if self.c.update_error:
            raise RuntimeError(self.c.update_error)
        return _Res(self.c.update_data)


class _Client:
    def __init__(self, row_data=None, select_error="", update_error=""):
        self.updates, self.update_eqs, self.selects = [], [], []
        self.row_data = [{"original_gross_book": None}] if row_data is None else row_data
        self.select_error, self.update_error = select_error, update_error
        self.update_data = [{"snapshot_date": "2026-10-30"}]

    def table(self, name):
        assert name == "account_daily_snapshots"
        return _Builder(self)


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)


def test_probe_true_selects_the_four_columns(monkeypatch):
    c = _Client()
    monkeypatch.setattr(db, "_client", lambda: c)
    assert db.account_snapshot_provenance_available() is True
    assert c.selects == ["journal_max_trade_id,book_positions,original_gross_book,repaired_at"]


def test_probe_false_on_unknown_column_none_on_other_failure(monkeypatch):
    monkeypatch.setattr(db, "_client", lambda: _Client(
        select_error="column account_daily_snapshots.repaired_at does not exist"))
    assert db.account_snapshot_provenance_available() is False
    monkeypatch.setattr(db, "_client", lambda: _Client(select_error="connection reset"))
    assert db.account_snapshot_provenance_available() is None
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.account_snapshot_provenance_available() is None


def test_apply_writes_only_the_repair_columns_keyed_on_date(monkeypatch):
    c = _Client()
    monkeypatch.setattr(db, "_client", lambda: c)
    assert db.apply_account_snapshot_repair("2026-10-30", FIELDS) is True
    assert set(c.updates[0]) == set(db._ACCOUNT_SNAPSHOT_REPAIR_COLS)
    assert not {"cash_balance", "cash_as_of", "maintenance_rate"} & set(c.updates[0])
    assert c.update_eqs == [[("snapshot_date", "2026-10-30")]]
    assert c.updates[0]["original_gross_book"] == 15272.55


def test_apply_never_overwrites_an_existing_original_gross_book(monkeypatch):
    c = _Client(row_data=[{"original_gross_book": 111.0}])
    monkeypatch.setattr(db, "_client", lambda: c)
    assert db.apply_account_snapshot_repair("2026-10-30", FIELDS) is True
    assert "original_gross_book" not in c.updates[0]
    assert c.updates[0]["gross_book"] == 19635.55      # the rest still repairs


def test_apply_missing_row_failure_readonly_and_bad_input(monkeypatch):
    monkeypatch.setattr(db, "_client", lambda: _Client(row_data=[]))
    assert db.apply_account_snapshot_repair("2026-10-30", FIELDS) is False
    c = _Client(update_error="boom")
    monkeypatch.setattr(db, "_client", lambda: c)
    with pytest.warns(UserWarning):
        assert db.apply_account_snapshot_repair("2026-10-30", FIELDS) is False
    c2 = _Client()
    monkeypatch.setattr(db, "_client", lambda: c2)
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    assert db.apply_account_snapshot_repair("2026-10-30", FIELDS) is False
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    assert db.apply_account_snapshot_repair("", FIELDS) is False
    assert db.apply_account_snapshot_repair("2026-10-30", {"cash_balance": 1}) is False
    assert c2.updates == []


def test_apply_update_matching_no_row_is_a_failure(monkeypatch):
    c = _Client()
    c.update_data = []
    monkeypatch.setattr(db, "_client", lambda: c)
    assert db.apply_account_snapshot_repair("2026-10-30", FIELDS) is False


# -- N1 C7: repair_daily_snapshot_rows ---------------------------------------------

class _SnapBuilder:
    def __init__(self, c, table):
        self.c, self.table_name, self.op, self.payload, self.filters = c, table, None, None, []

    def upsert(self, records, on_conflict=None):
        self.op, self.payload, self.on_conflict = "upsert", records, on_conflict
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v))
        return self

    def in_(self, k, v):
        self.filters.append(("in", k, list(v)))
        return self

    def execute(self):
        self.c.calls.append((self.table_name, self.op, self.payload,
                             getattr(self, "on_conflict", None), list(self.filters)))
        if self.c.fail_on == self.op:
            raise RuntimeError("boom")
        return _Res([])


class _SnapClient:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def table(self, name):
        return _SnapBuilder(self, name)


UPS = [{"ticker": "crwd", "shares": 3, "close_price": 480.5},
       {"ticker": "CSCO", "shares": 15, "close_price": 75.5}]


@pytest.fixture
def snap_client(monkeypatch):
    from datetime import date
    c = _SnapClient()
    monkeypatch.setattr(db, "_client", lambda: c)
    import stock_analyzer.market_time as mt
    monkeypatch.setattr(mt, "today_et", lambda: date(2026, 11, 7))
    return c


def test_repair_rows_upserts_on_the_pk_then_deletes_exactly_the_listed_tickers(snap_client):
    assert db.repair_daily_snapshot_rows("2026-11-05", UPS, ["gone", "ALSO"]) is True
    up, de = snap_client.calls
    assert up[:4] == ("daily_snapshots", "upsert", [
        {"snapshot_date": "2026-11-05", "ticker": "CRWD", "shares": 3.0, "close_price": 480.5},
        {"snapshot_date": "2026-11-05", "ticker": "CSCO", "shares": 15.0, "close_price": 75.5}],
        "snapshot_date,ticker")
    assert de[:2] == ("daily_snapshots", "delete")
    # exactly the listed tickers for that date: an `.eq` + a plain `.in_`, never `not_`
    assert de[4] == [("eq", "snapshot_date", "2026-11-05"), ("in", "ticker", ["ALSO", "GONE"])]


@pytest.mark.parametrize("d", ["2026-11-07", "2026-11-08", "2027-01-01"])
def test_repair_rows_refuses_today_and_future(snap_client, d):
    assert db.repair_daily_snapshot_rows(d, UPS, ["X"]) is False
    assert snap_client.calls == []


def test_repair_rows_day_before_today_is_allowed_and_weekend_dates_are_not_special(snap_client):
    assert db.repair_daily_snapshot_rows("2026-11-06", UPS, []) is True
    assert db.repair_daily_snapshot_rows("2026-11-01", UPS, []) is True      # a Sunday
    assert [c[1] for c in snap_client.calls] == ["upsert", "upsert"]


def test_repair_rows_readonly_no_db_and_bad_input(snap_client, monkeypatch):
    assert db.repair_daily_snapshot_rows("", UPS, []) is False
    assert db.repair_daily_snapshot_rows("not-a-date", UPS, []) is False
    for bad in ([{"ticker": "A", "shares": 0, "close_price": 5}],
                [{"ticker": "A", "shares": 3, "close_price": float("nan")}],
                [{"ticker": "", "shares": 3, "close_price": 5}],
                [{"ticker": "A", "shares": None, "close_price": 5}]):
        assert db.repair_daily_snapshot_rows("2026-11-05", bad, []) is False
    assert snap_client.calls == []
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    assert db.repair_daily_snapshot_rows("2026-11-05", UPS, []) is False
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.repair_daily_snapshot_rows("2026-11-05", UPS, []) is None
    assert snap_client.calls == []


def test_repair_rows_nothing_to_write_is_true_without_a_call(snap_client):
    assert db.repair_daily_snapshot_rows("2026-11-05", [], []) is True
    assert snap_client.calls == []


def test_repair_rows_exception_is_none_and_a_failed_upsert_never_deletes(snap_client):
    snap_client.fail_on = "upsert"
    with pytest.warns(UserWarning):
        assert db.repair_daily_snapshot_rows("2026-11-05", UPS, ["GONE"]) is None
    assert [c[1] for c in snap_client.calls] == ["upsert"]
    snap_client.calls.clear()
    snap_client.fail_on = "delete"
    with pytest.warns(UserWarning):
        assert db.repair_daily_snapshot_rows("2026-11-05", [], ["GONE"]) is None
