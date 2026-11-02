"""db.py N1 provenance: the account_daily_snapshots save drops exactly the four
new keys on an unknown-column error and retries once; load_max_trade_id_or_none."""
import pytest

from stock_analyzer import db

pytestmark = pytest.mark.fast

PROV = ("journal_max_trade_id", "book_positions", "original_gross_book", "repaired_at")
ROW = {
    "snapshot_date": "2026-10-30", "gross_book": 1000.0, "cash_balance": -500.0,
    "net_equity": 500.0, "leverage": 2.0, "cushion": 1.0, "call_distance_pct": -1.0,
    "maintenance_rate": 0.25, "cash_as_of": "2026-10-30T21:00:00+00:00",
    "journal_max_trade_id": 7, "book_positions": {"AAA": {"shares": 10, "close": 100.0}},
    "original_gross_book": 900.0, "repaired_at": "2026-11-01T12:00:00-04:00",
}


class _Res:
    def __init__(self, data):
        self.data = data


class _Builder:
    def __init__(self, client):
        self.c = client
        self.is_upsert = False

    def upsert(self, payload, on_conflict=None):
        self.c.payloads.append(dict(payload))
        self.is_upsert = True
        return self

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if self.is_upsert and len(self.c.payloads) in self.c.fail_calls:
            raise RuntimeError(self.c.error)
        if not self.is_upsert:
            if self.c.read_raises:
                raise RuntimeError("boom")
            return _Res(self.c.read_data)
        return _Res([])


class _Client:
    def __init__(self, error="", fail_calls=(), read_data=None, read_raises=False):
        self.payloads = []
        self.error = error
        self.fail_calls = set(fail_calls)
        self.read_data = read_data
        self.read_raises = read_raises

    def table(self, _name):
        return _Builder(self)


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)


def test_unknown_column_drops_exactly_the_four_keys_and_retries_once(monkeypatch):
    c = _Client(error="Could not find the 'book_positions' column (PGRST204)", fail_calls=(1,))
    monkeypatch.setattr(db, "_client", lambda: c)
    with pytest.warns(UserWarning, match="provenance columns missing"):
        assert db.save_account_daily_snapshot(ROW) is True
    assert len(c.payloads) == 2
    assert set(c.payloads[0]) == set(ROW)
    assert set(c.payloads[1]) == set(ROW) - set(PROV)


def test_unrelated_error_does_not_retry(monkeypatch):
    c = _Client(error="connection reset", fail_calls=(1,))
    monkeypatch.setattr(db, "_client", lambda: c)
    with pytest.warns(UserWarning):
        assert db.save_account_daily_snapshot(ROW) is False
    assert len(c.payloads) == 1


def test_retry_failure_returns_false_without_raising(monkeypatch):
    c = _Client(error="column repaired_at does not exist", fail_calls=(1, 2))
    monkeypatch.setattr(db, "_client", lambda: c)
    with pytest.warns(UserWarning):
        assert db.save_account_daily_snapshot(ROW) is False
    assert len(c.payloads) == 2


def test_row_without_provenance_keys_saves_unchanged(monkeypatch):
    c = _Client()
    monkeypatch.setattr(db, "_client", lambda: c)
    legacy = {k: v for k, v in ROW.items() if k not in PROV}
    assert db.save_account_daily_snapshot(legacy) is True
    assert c.payloads == [legacy]


def test_info_reports_dropped_keys_only_on_fallback_retry_success(monkeypatch):
    c = _Client(error="Could not find the 'book_positions' column (PGRST204)", fail_calls=(1,))
    monkeypatch.setattr(db, "_client", lambda: c)
    info = {}
    with pytest.warns(UserWarning):
        assert db.save_account_daily_snapshot(ROW, info=info) is True
    assert info == {"provenance_dropped": sorted(PROV)}


def test_info_untouched_on_normal_success(monkeypatch):
    monkeypatch.setattr(db, "_client", lambda: _Client())
    info = {}
    assert db.save_account_daily_snapshot(ROW, info=info) is True
    assert info == {}


def test_info_untouched_on_failure(monkeypatch):
    for c in (_Client(error="connection reset", fail_calls=(1,)),
              _Client(error="column repaired_at does not exist", fail_calls=(1, 2))):
        monkeypatch.setattr(db, "_client", lambda c=c: c)
        info = {}
        with pytest.warns(UserWarning):
            assert db.save_account_daily_snapshot(ROW, info=info) is False
        assert info == {}


def test_max_trade_id_returns_int(monkeypatch):
    monkeypatch.setattr(db, "_client", lambda: _Client(read_data=[{"id": 412}]))
    assert db.load_max_trade_id_or_none() == 412


def test_max_trade_id_zero_for_a_readable_empty_journal(monkeypatch):
    monkeypatch.setattr(db, "_client", lambda: _Client(read_data=[]))
    assert db.load_max_trade_id_or_none() == 0


@pytest.mark.parametrize("data", [[{"id": None}], [{"id": "x"}]])
def test_max_trade_id_none_on_bad_id(monkeypatch, data):
    monkeypatch.setattr(db, "_client", lambda: _Client(read_data=data))
    assert db.load_max_trade_id_or_none() is None


def test_max_trade_id_none_on_failure_and_no_creds(monkeypatch):
    monkeypatch.setattr(db, "_client", lambda: _Client(read_raises=True))
    assert db.load_max_trade_id_or_none() is None
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_max_trade_id_or_none() is None
