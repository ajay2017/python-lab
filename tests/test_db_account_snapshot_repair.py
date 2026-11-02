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
