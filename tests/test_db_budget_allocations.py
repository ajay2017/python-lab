"""db.save_budget_allocations / load_budget_allocations_or_none (daily
new-position budget, chunk 2). The contracts that matter: FIRST WRITER WINS
(never overwrite a pushed size), and the loader's None-vs-{} offline sentinel
(None = couldn't check, {} = checked and nothing pushed)."""
import datetime as dt

import pytest

from stock_analyzer import daily_budget, db

pytestmark = pytest.mark.fast

TRADING_DAY = "2026-10-15"   # Thursday
SATURDAY = "2026-10-17"


class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client):
        self._c = client

    def upsert(self, records, **kw):
        self._c.upserts.append((records, kw))
        if self._c.upsert_exc is not None:
            raise self._c.upsert_exc
        self._c.pending = "upsert"
        return self

    def select(self, cols):
        self._c.pending = "select"
        return self

    def eq(self, *a):
        self._c.eqs.append(a)
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a):
        self._c.limits.append(a)
        return self

    def execute(self):
        if self._c.raise_exc is not None:
            raise self._c.raise_exc
        if self._c.pending == "select":
            return _Res(self._c.rows)
        return _Res(self._c.upsert_result)


class _Client:
    def __init__(self, rows=None, raise_exc=None):
        self.rows = rows
        self.raise_exc = raise_exc
        self.upserts: list = []
        self.pending = None
        self.tables: list = []
        self.eqs: list = []
        self.limits: list = []
        self.upsert_exc = None
        self.upsert_result: list = []

    def table(self, name):
        self.tables.append(name)
        return _Query(self)


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    yield
    db._CLIENT = None


def _install(c):
    db._CLIENT = c
    return c


def _row(ticker="AAPL", dollars=1000.0, day=TRADING_DAY, **kw):
    r = {"budget_date": day, "ticker": ticker, "dollars": dollars,
         "shares": 10, "price": 100.0, "composite_score": 80.0,
         "source": "scan:morning", "basis_dollars": 8000.0, "budget_pct": 10.0}
    r.update(kw)
    return r


# ── writer ───────────────────────────────────────────────────────────────

def test_first_writer_wins_upsert_flags():
    c = _install(_Client())
    out = db.save_budget_allocations([_row()])
    assert out["error"] is None and out["saved"] == 1
    assert c.tables == ["new_position_budget_allocations"]
    recs, kw = c.upserts[0]
    assert kw["ignore_duplicates"] is True
    assert kw["on_conflict"] == "budget_date,ticker"
    assert recs[0]["budget_date"] == TRADING_DAY


def test_ticker_uppercased():
    c = _install(_Client())
    db.save_budget_allocations([_row(ticker=" msft ")])
    assert c.upserts[0][0][0]["ticker"] == "MSFT"


def test_weekend_row_dropped_no_db_call():
    c = _install(_Client())
    out = db.save_budget_allocations([_row(day=SATURDAY)])
    assert c.upserts == [] and out["saved"] == 0 and out["error"] is None
    assert out["skipped_non_trading"] == 1


def test_readonly_is_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    c = _install(_Client())
    out = db.save_budget_allocations([_row()])
    assert c.upserts == [] and out["saved"] == 0 and out["error"] == "read-only"


@pytest.mark.parametrize("bad", [0, -5, float("nan"), float("inf"), None, "x"])
def test_bad_dollars_dropped_and_reported(bad):
    c = _install(_Client())
    out = db.save_budget_allocations([_row(ticker="BAD", dollars=bad), _row(ticker="OK")])
    sent = [r["ticker"] for r in c.upserts[0][0]]
    assert sent == ["OK"] and out["dropped_invalid"] == 1 and out["saved"] == 1


def test_all_invalid_makes_no_db_call():
    c = _install(_Client())
    out = db.save_budget_allocations([_row(dollars=0)])
    assert c.upserts == [] and out["dropped_invalid"] == 1


def test_unknown_source_dropped():
    c = _install(_Client())
    out = db.save_budget_allocations([_row(source="mystery")])
    assert c.upserts == [] and out["dropped_invalid"] == 1


def test_missing_table_surfaces_error():
    _install(_Client(raise_exc=RuntimeError('relation "new_position_budget_allocations" does not exist')))
    out = db.save_budget_allocations([_row()])
    assert out["saved"] == 0 and "does not exist" in out["error"]


# ── loader ───────────────────────────────────────────────────────────────

def test_loader_shape_and_uppercase():
    _install(_Client(rows=[{"ticker": "aapl", "shares": 10, "dollars": 1000},
                           {"ticker": "MSFT", "shares": None, "dollars": "250.5"}]))
    assert db.load_budget_allocations_or_none(TRADING_DAY) == {
        "AAPL": {"shares": 10.0, "dollars": 1000.0},
        "MSFT": {"shares": None, "dollars": 250.5},
    }


def test_loader_empty_is_empty_dict_not_none():
    _install(_Client(rows=[]))
    assert db.load_budget_allocations_or_none(TRADING_DAY) == {}


def test_loader_error_is_none():
    _install(_Client(raise_exc=RuntimeError("DB offline")))
    assert db.load_budget_allocations_or_none(TRADING_DAY) is None


def test_loader_no_db_is_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_budget_allocations_or_none(TRADING_DAY) is None


@pytest.mark.parametrize("bad_row", [
    {"ticker": "AAPL", "shares": 1, "dollars": None},
    {"ticker": "AAPL", "shares": 1, "dollars": 0},
    {"ticker": "AAPL", "shares": 1, "dollars": float("nan")},
    {"ticker": "", "shares": 1, "dollars": 100},
    {"ticker": "AAPL", "shares": 1, "dollars": "abc"},
])
def test_loader_malformed_row_is_none(bad_row):
    _install(_Client(rows=[{"ticker": "OK", "shares": 1, "dollars": 50}, bad_row]))
    assert db.load_budget_allocations_or_none(TRADING_DAY) is None


def test_loader_output_feeds_allocate_directly():
    _install(_Client(rows=[{"ticker": "AAA", "shares": 5, "dollars": 500.0}]))
    allocs = db.load_budget_allocations_or_none(TRADING_DAY)
    cand = {"ticker": "AAA", "composite": 80, "momentum": 50, "is_etf": False,
            "price": 100.0, "sizing": {"shares": 10, "total_cost": 1000.0}}
    r = daily_budget.allocate({"state": "ok", "basis": 8000.0}, {}, allocs, [cand])
    assert r["state"] == "ok"
    assert r["per_ticker"]["AAA"]["shares"] == 5   # locked at the emailed size


# ── review round 1 (B1-B3 + non-blocking) ──────────────────────────────────

_FULL_KEYS = {"attempted", "saved", "error", "dropped_invalid",
              "skipped_non_trading", "inserted"}


@pytest.mark.parametrize("tk", [None, "", "   "])
def test_missing_ticker_dropped_not_NONE(tk):
    c = _install(_Client())
    out = db.save_budget_allocations([_row(ticker=tk)])
    assert c.upserts == [] and out["dropped_invalid"] == 1


UTC = dt.timezone.utc


def test_utc_datetime_converted_to_et_day_on_write():
    # 2026-10-16 01:00 UTC == 2026-10-15 21:00 ET -> ET day 10-15
    c = _install(_Client())
    db.save_budget_allocations([_row(day=dt.datetime(2026, 10, 16, 1, 0, tzinfo=UTC))])
    assert c.upserts[0][0][0]["budget_date"] == "2026-10-15"


def test_utc_datetime_converted_to_et_day_on_read():
    c = _install(_Client(rows=[]))
    assert db.load_budget_allocations_or_none(dt.datetime(2026, 10, 16, 1, 0, tzinfo=UTC)) == {}
    assert ("budget_date", "2026-10-15") in c.eqs


def test_naive_datetime_rejected_both_ways():
    c = _install(_Client(rows=[]))
    naive = dt.datetime(2026, 10, 15, 21, 0)
    out = db.save_budget_allocations([_row(day=naive)])
    assert c.upserts == [] and out["dropped_invalid"] == 1
    assert db.load_budget_allocations_or_none(naive) is None
    assert c.eqs == []


def test_plain_date_and_iso_string_pass():
    c = _install(_Client(rows=[]))
    db.save_budget_allocations([_row(day=dt.date(2026, 10, 15))])
    assert c.upserts[0][0][0]["budget_date"] == "2026-10-15"
    assert db.load_budget_allocations_or_none("2026-10-15T09:45:00") is None   # naive timestamp rejected
    assert db.load_budget_allocations_or_none(dt.date(2026, 10, 15)) == {}


@pytest.mark.parametrize("bad", ["garbage", "", "2026-13-45", 12345, None])
def test_unparseable_day_dropped_and_loader_none(bad):
    c = _install(_Client(rows=[]))
    out = db.save_budget_allocations([_row(day=bad), _row(ticker="OK")])
    assert [r["ticker"] for r in c.upserts[0][0]] == ["OK"] and out["dropped_invalid"] == 1
    assert db.load_budget_allocations_or_none(bad) is None


def test_loader_queries_the_requested_day_and_limits():
    c = _install(_Client(rows=[]))
    db.load_budget_allocations_or_none("2026-10-15")
    assert ("budget_date", "2026-10-15") in c.eqs
    assert c.limits and c.limits[0] == (db._BUDGET_ALLOC_PAGE_SIZE,)


def test_loader_data_none_is_none_not_empty():
    _install(_Client(rows=None))
    assert db.load_budget_allocations_or_none(TRADING_DAY) is None


def test_loader_day_none_is_none_without_query():
    c = _install(_Client(rows=[]))
    assert db.load_budget_allocations_or_none(None) is None
    assert c.tables == []


def test_loader_nonfinite_shares_is_none():
    _install(_Client(rows=[{"ticker": "A", "shares": float("nan"), "dollars": 10}]))
    assert db.load_budget_allocations_or_none(TRADING_DAY) is None


def test_loader_page_cap_is_none_truncation_guard():
    rows = [{"ticker": f"T{i}", "shares": 1, "dollars": 10}
            for i in range(db._BUDGET_ALLOC_PAGE_SIZE)]
    _install(_Client(rows=rows))
    assert db.load_budget_allocations_or_none(TRADING_DAY) is None


def test_loader_malformed_row_warns():
    _install(_Client(rows=[{"ticker": "AAPL", "shares": 1, "dollars": 0}]))
    with pytest.warns(UserWarning, match="malformed row"):
        assert db.load_budget_allocations_or_none(TRADING_DAY) is None


def test_typeerror_does_not_fall_back_to_plain_upsert():
    c = _install(_Client())
    c.upsert_exc = TypeError("unexpected keyword ignore_duplicates")
    out = db.save_budget_allocations([_row()])
    assert len(c.upserts) == 1
    assert out["saved"] == 0 and out["error"]


def test_every_return_path_has_full_key_set(monkeypatch):
    _install(_Client())
    assert set(db.save_budget_allocations([])) == _FULL_KEYS
    assert set(db.save_budget_allocations([_row(dollars=0)])) == _FULL_KEYS
    assert set(db.save_budget_allocations([_row(day=SATURDAY)])) == _FULL_KEYS
    assert set(db.save_budget_allocations([_row()])) == _FULL_KEYS
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert set(db.save_budget_allocations([_row()])) == _FULL_KEYS
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    assert set(db.save_budget_allocations([_row()])) == _FULL_KEYS


def test_inserted_is_best_effort():
    c = _install(_Client())
    c.upsert_result = [{"ticker": "AAPL"}]
    assert db.save_budget_allocations([_row(), _row(ticker="MSFT")])["inserted"] == 1
    c.upsert_result = None
    assert db.save_budget_allocations([_row()])["inserted"] is None


def test_sanitize_applied_to_payload(monkeypatch):
    seen = []
    real = db._sanitize_for_write
    monkeypatch.setattr(db, "_sanitize_for_write",
                        lambda p, w: (seen.append(w), real(p, w))[1])
    _install(_Client())
    db.save_budget_allocations([_row()])
    assert seen == ["save_budget_allocations"]


@pytest.mark.parametrize("stamp,et_day", [
    ("2026-10-16T01:00:00+00:00", "2026-10-15"),
    ("2026-10-16T01:00:00Z", "2026-10-15"),
    ("2026-10-16T01:00:00.123456+00:00", "2026-10-15"),
    ("2026-12-16T04:59:00Z", "2026-12-15"),       # EST season (UTC-5)
    ("2026-10-15T09:45:00-04:00", "2026-10-15"),
    ("  2026-10-16T01:00:00Z\n", "2026-10-15"),   # padded input is stripped, not rejected
])
def test_offset_timestamp_strings_convert_to_et_day_both_ways(stamp, et_day):
    c = _install(_Client(rows=[]))
    db.save_budget_allocations([_row(day=stamp)])
    assert c.upserts[0][0][0]["budget_date"] == et_day
    assert db.load_budget_allocations_or_none(stamp) == {}
    assert ("budget_date", et_day) in c.eqs
