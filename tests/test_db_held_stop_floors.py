"""db.load_held_stop_floors_or_none / db.save_held_stop_floors and the DDL file
(#26 C3c-3, docs/plans/held-stop-floor-c3c-spec.md sections 1, 2 and 10).

Contracts that matter: the loader's None-vs-{} offline sentinel (None = couldn't
check, {} = checked and nothing saved / nothing asked), the save's batched upsert on
(ticker, episode_start), the missing-table hint the EOD lane surfaces red, and that
`held_stop_floor.rows_to_write`'s keys are exactly the table's columns. Fake client
only: no network, no DB."""
import datetime as dt
import pathlib
import re

import pandas as pd
import pytest

from stock_analyzer import db
from stock_analyzer import held_stop_floor as hf

_SQL = pathlib.Path(__file__).resolve().parent.parent / "docs" / "sql" / "held-stop-floors.sql"
_CHECK_SQL = (pathlib.Path(__file__).resolve().parent.parent / "docs" / "sql"
              / "readonly-held-stop-floors-check.sql")
MISSING_TABLE = ("{'code': 'PGRST205', 'message': \"Could not find the table "
                 "'public.held_stop_floors' in the schema cache\"}")


class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client):
        self._c = client
        self._range = None

    def select(self, cols):
        self._c.selects.append(cols)
        return self

    def in_(self, col, values):
        self._c.ins.append((col, list(values)))
        return self

    def order(self, *a, **k):
        self._c.orders.append(a)
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def upsert(self, records, **kw):
        self._c.upserts.append((records, kw))
        self._upsert = True
        return self

    def execute(self):
        if self._c.exc is not None:
            raise self._c.exc
        if self._range is not None:
            self._c.reads += 1
            a, b = self._range
            page = self._c.rows[a:b + 1]
            if self._c.server_cap is not None:
                page = page[:self._c.server_cap]
            return _Res(page)
        return _Res([])


class _Client:
    def __init__(self, rows=None, exc=None, server_cap=None):
        self.rows = rows if rows is not None else []
        self.exc = exc
        self.server_cap = server_cap
        self.tables: list = []
        self.selects: list = []
        self.ins: list = []
        self.orders: list = []
        self.upserts: list = []
        self.reads = 0

    def table(self, name):
        self.tables.append(name)
        return _Query(self)


_ERRORS: list = []


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    _ERRORS.clear()
    monkeypatch.setattr(db, "_record_db_error", _ERRORS.append)
    yield
    db._CLIENT = None


def _install(c):
    db._CLIENT = c
    return c


def _db_row(ticker="AAA", start="2026-09-01", as_of="2026-09-30", stop=95.5, **kw):
    r = {"ticker": ticker, "episode_start": start, "as_of": as_of, "stop": stop,
         "stop_set_on": "2026-09-20", "binding": "trailing", "ratchet_label": None,
         "first_buy_price": 100.0, "split_ratio": 1.0, "last_buy": start,
         "avg_cost": 100.0, "ref_session": "2026-09-29", "ref_close": 101.25,
         "bind_session": "2026-09-18", "bind_close": 104.0, "policy_sig": "sig",
         "source": "cron:eod", "written_at": "2026-09-30T20:05:00+00:00"}
    r.update(kw)
    return r


# ── loader: None paths and the no-call cases ─────────────────────────────────
@pytest.mark.parametrize("tickers", [[], (), set(), [None, "", "  "]])
def test_no_tickers_is_empty_dict_with_no_call(tickers):
    c = _install(_Client())
    assert db.load_held_stop_floors_or_none(tickers) == {}
    assert c.tables == [] and c.reads == 0


def test_tickers_none_is_none_with_no_call():
    c = _install(_Client())
    assert db.load_held_stop_floors_or_none(None) is None      # nothing asked: couldn't check
    assert c.tables == [] and c.reads == 0


def test_no_credentials_is_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    c = _install(_Client())
    assert db.load_held_stop_floors_or_none(["AAA"]) is None
    assert c.tables == []


def test_query_error_is_none_and_recorded():
    _install(_Client(exc=RuntimeError("boom 503")))
    assert db.load_held_stop_floors_or_none(["AAA"]) is None
    assert any("held_stop_floors_read" in m for m in _ERRORS)


def test_missing_table_is_none_and_not_recorded_as_an_api_error():
    _install(_Client(exc=RuntimeError(MISSING_TABLE)))
    assert db.load_held_stop_floors_or_none(["AAA"]) is None
    assert _ERRORS == []


def test_auth_error_is_none():
    _install(_Client(exc=RuntimeError("401 Unauthorized: invalid API key")))
    assert db.load_held_stop_floors_or_none(["AAA"]) is None


def test_client_construction_failure_is_none(monkeypatch):
    def boom():
        raise RuntimeError("no client")
    monkeypatch.setattr(db, "_client", boom)
    assert db.load_held_stop_floors_or_none(["AAA"]) is None


def test_none_is_not_collapsed_with_empty():
    _install(_Client(rows=[]))
    assert db.load_held_stop_floors_or_none(["AAA"]) == {}      # read ok, nothing saved
    _install(_Client(exc=RuntimeError("down")))
    assert db.load_held_stop_floors_or_none(["AAA"]) is None    # couldn't check


@pytest.mark.parametrize("bad", [
    {"ticker": "", "episode_start": "2026-09-01", "as_of": "2026-09-30"},
    {"ticker": None, "episode_start": "2026-09-01", "as_of": "2026-09-30"},
    {"ticker": "AAA", "episode_start": None, "as_of": "2026-09-30"},
    {"ticker": "AAA", "episode_start": "garbage", "as_of": "2026-09-30"},
    {"ticker": "AAA", "episode_start": "2026-09-01", "as_of": None},
])
def test_malformed_row_makes_the_whole_read_unreadable(bad):
    _install(_Client(rows=[_db_row(ticker="BBB"), {**_db_row(), **bad}]))
    with pytest.warns(UserWarning, match="malformed row"):
        assert db.load_held_stop_floors_or_none(["AAA", "BBB"]) is None
    assert len(_ERRORS) == 1 and _ERRORS[0].startswith("held_stop_floors_read: malformed row")


# ── loader: query shape, dedupe, parsing ─────────────────────────────────────
def test_query_uses_in_filter_upper_cased_and_deduped():
    c = _install(_Client(rows=[]))
    db.load_held_stop_floors_or_none([" aaa", "AAA", "bbb", "Aaa"])
    assert c.tables[0] == "held_stop_floors"
    assert c.ins == [("ticker", ["AAA", "BBB"])] * c.reads


def test_pagination_reads_every_page():
    rows = [_db_row(ticker=f"T{i:04d}", start="2026-09-01") for i in range(2503)]
    c = _install(_Client(rows=rows))
    out = db.load_held_stop_floors_or_none([r["ticker"] for r in rows])
    assert len(out) == 2503
    assert c.reads == 4          # 1000 + 1000 + 503 + the empty terminating page


def test_server_side_cap_below_page_size_does_not_truncate():
    rows = [_db_row(ticker=f"T{i:03d}") for i in range(250)]
    _install(_Client(rows=rows, server_cap=100))
    assert len(db.load_held_stop_floors_or_none(["T000"])) == 250


def test_shape_is_ticker_then_episode_start_date():
    _install(_Client(rows=[_db_row("AAA", "2026-09-01"), _db_row("AAA", "2026-10-05"),
                           _db_row("BBB", "2026-09-17")]))
    out = db.load_held_stop_floors_or_none(["AAA", "BBB"])
    assert set(out) == {"AAA", "BBB"}
    assert set(out["AAA"]) == {dt.date(2026, 9, 1), dt.date(2026, 10, 5)}
    assert set(out["BBB"]) == {dt.date(2026, 9, 17)}
    assert all(type(k) is dt.date for k in out["AAA"])


def test_dates_parse_to_date_and_numerics_to_float():
    _install(_Client(rows=[_db_row(stop="95.50", ref_close=101, split_ratio="1.000000000",
                                   written_at="2026-09-30T20:05:00.123456+00:00")]))
    row = db.load_held_stop_floors_or_none(["AAA"])["AAA"][dt.date(2026, 9, 1)]
    for c in ("episode_start", "as_of", "stop_set_on", "last_buy", "ref_session",
              "bind_session"):
        assert type(row[c]) is dt.date, c
    assert row["as_of"] == dt.date(2026, 9, 30)
    for c in ("stop", "first_buy_price", "split_ratio", "avg_cost", "ref_close", "bind_close"):
        assert type(row[c]) is float, c
    assert row["stop"] == 95.5 and row["ref_close"] == 101.0
    assert row["binding"] == "trailing" and row["policy_sig"] == "sig"
    assert row["source"] == "cron:eod" and row["ratchet_label"] is None


def test_none_values_stay_none_not_zero_or_nat():
    _install(_Client(rows=[_db_row(first_buy_price=None, last_buy=None, bind_session=None,
                                   bind_close=None, stop_set_on=None, ratchet_label=None)]))
    row = db.load_held_stop_floors_or_none(["AAA"])["AAA"][dt.date(2026, 9, 1)]
    for c in ("first_buy_price", "last_buy", "bind_session", "bind_close", "stop_set_on",
              "ratchet_label"):
        assert row[c] is None, c


def test_unparseable_optional_values_become_none():
    _install(_Client(rows=[_db_row(last_buy="soon", bind_close="n/a", first_buy_price="inf")]))
    row = db.load_held_stop_floors_or_none(["AAA"])["AAA"][dt.date(2026, 9, 1)]
    assert row["last_buy"] is None and row["bind_close"] is None
    assert row["first_buy_price"] is None


def test_duplicate_key_keeps_the_latest_as_of():
    rows = [_db_row(as_of="2026-09-30", stop=90.0), _db_row(as_of="2026-10-02", stop=97.0),
            _db_row(as_of="2026-10-01", stop=92.0)]
    _install(_Client(rows=rows))
    row = db.load_held_stop_floors_or_none(["AAA"])["AAA"][dt.date(2026, 9, 1)]
    assert row["as_of"] == dt.date(2026, 10, 2) and row["stop"] == 97.0


def test_ticker_key_is_normalised():
    _install(_Client(rows=[_db_row(ticker=" aaa ")]))
    assert set(db.load_held_stop_floors_or_none(["AAA"])) == {"AAA"}


# ── save ─────────────────────────────────────────────────────────────────────
def _w_row(**kw):
    r = {k: v for k, v in _db_row().items() if k != "written_at"}
    r.update(kw)
    return r


def test_readonly_save_is_a_noop_with_no_client_call(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    c = _install(_Client())
    ok, detail = db.save_held_stop_floors([_w_row()])
    assert ok is False and "read-only" in detail
    assert c.tables == [] and c.upserts == []


@pytest.mark.parametrize("rows", [[], None])
def test_empty_save_makes_no_call_and_is_ok(rows):
    c = _install(_Client())
    assert db.save_held_stop_floors(rows) == (True, "no rows to write")
    assert c.tables == [] and c.upserts == []


def test_no_credentials_save_is_not_ok(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    c = _install(_Client())
    ok, _ = db.save_held_stop_floors([_w_row()])
    assert ok is False and c.tables == []


def test_one_batched_upsert_with_the_exact_on_conflict():
    c = _install(_Client())
    ok, detail = db.save_held_stop_floors([_w_row(ticker="AAA"), _w_row(ticker="BBB")])
    assert ok is True and "2" in detail
    assert c.tables == ["held_stop_floors"]
    assert len(c.upserts) == 1
    recs, kw = c.upserts[0]
    assert kw == {"on_conflict": "ticker,episode_start"}
    assert [r["ticker"] for r in recs] == ["AAA", "BBB"]
    assert "ignore_duplicates" not in kw       # a same-evening re-run must rewrite the row


def test_every_row_carries_a_fresh_tz_aware_written_at():
    c = _install(_Client())
    before = dt.datetime.now(dt.timezone.utc)
    db.save_held_stop_floors([_w_row(ticker="AAA"), _w_row(ticker="BBB"),
                              _w_row(ticker="CCC", written_at="2020-01-01T00:00:00+00:00")])
    after = dt.datetime.now(dt.timezone.utc)
    recs = c.upserts[0][0]
    assert len(recs) == 3
    stamps = {r["written_at"] for r in recs}
    assert len(stamps) == 1                       # uniform keys and value across the batch
    for r in recs:
        w = dt.datetime.fromisoformat(r["written_at"])
        assert w.tzinfo is not None and w.utcoffset() == dt.timedelta(0)
        assert before <= w <= after               # refreshed, never the caller's old value


def test_written_at_is_added_by_the_save_not_the_pure_writer():
    as_of = dt.date(2026, 9, 30)
    assert "written_at" not in hf.rows_to_write(_held(as_of), as_of)[0]
    c = _install(_Client())
    db.save_held_stop_floors(hf.rows_to_write(_held(as_of), as_of))
    assert "written_at" in c.upserts[0][0][0]


def test_dates_go_out_as_iso_strings():
    c = _install(_Client())
    db.save_held_stop_floors([_w_row(episode_start=dt.date(2026, 9, 1),
                                     as_of=dt.datetime(2026, 9, 30, 20, 5),
                                     stop_set_on=dt.date(2026, 9, 20),
                                     last_buy=dt.date(2026, 9, 1),
                                     ref_session=dt.date(2026, 9, 29),
                                     bind_session=dt.date(2026, 9, 18))])
    rec = c.upserts[0][0][0]
    assert rec["episode_start"] == "2026-09-01" and rec["as_of"] == "2026-09-30"
    for k in ("stop_set_on", "last_buy", "ref_session", "bind_session"):
        assert isinstance(rec[k], str) and len(rec[k]) == 10, k


def test_sanitize_is_applied(monkeypatch):
    seen = []
    real = db._sanitize_for_write

    def spy(payload, writer):
        seen.append(writer)
        return real(payload, writer)
    monkeypatch.setattr(db, "_sanitize_for_write", spy)
    c = _install(_Client())
    with pytest.warns(UserWarning, match="save_held_stop_floors"):
        db.save_held_stop_floors([_w_row(bind_close=float("nan"))])
    assert seen == ["save_held_stop_floors"]
    assert c.upserts[0][0][0]["bind_close"] is None     # NaN -> NULL, not a failed request


def test_input_rows_are_not_mutated():
    row = _w_row(as_of=dt.date(2026, 9, 30))
    _install(_Client())
    db.save_held_stop_floors([row])
    assert row["as_of"] == dt.date(2026, 9, 30)


def test_write_error_returns_false_never_raises_and_is_recorded():
    _install(_Client(exc=RuntimeError("503 upstream")))
    ok, detail = db.save_held_stop_floors([_w_row()])
    assert ok is False and "503" in detail
    assert "apply docs/sql/held-stop-floors.sql" not in detail
    assert any("held_stop_floors_upsert" in m for m in _ERRORS)


def test_missing_table_save_names_the_ddl_file():
    _install(_Client(exc=RuntimeError(MISSING_TABLE)))
    ok, detail = db.save_held_stop_floors([_w_row()])
    assert ok is False
    assert "apply docs/sql/held-stop-floors.sql" in detail
    assert _ERRORS == []        # a not-yet-applied DDL isn't a Supabase outage


def test_postgres_42p01_wording_is_also_a_missing_table():
    _install(_Client(exc=RuntimeError('relation "held_stop_floors" does not exist')))
    ok, detail = db.save_held_stop_floors([_w_row()])
    assert ok is False and "apply docs/sql/held-stop-floors.sql" in detail


def test_unencodable_rows_and_client_failure_never_raise(monkeypatch):
    _install(_Client())
    ok, _ = db.save_held_stop_floors([object()])           # dict(object()) raises
    assert ok is False

    def boom():
        raise RuntimeError("no client")
    monkeypatch.setattr(db, "_client", boom)
    ok, _ = db.save_held_stop_floors([_w_row()])
    assert ok is False


# ── round trip: rows_to_write -> save -> loader -> the pure reader ───────────
def _held(as_of):
    start = dt.date(2026, 9, 1)
    return {"AAA": {
        "basis": "full", "stop": 95.5, "avg_cost": 100.0, "binding": "trailing",
        "ratchet_label": None, "last_session": dt.date(2026, 9, 29), "last_close": 101.25,
        "bind_session": dt.date(2026, 9, 18), "bind_close": 104.0,
        "episode": {"start": start, "first_buy_price": 100.0, "split_ratio": 1.0,
                    "last_buy": start},
        "floor": {"state": "none", "row_as_of": None}, "stop_set_on": None}}


def test_round_trip_through_save_and_load_is_accepted_by_the_reader():
    as_of = dt.date(2026, 9, 30)                       # a Wednesday, a trading day
    rows = hf.rows_to_write(_held(as_of), as_of)
    assert len(rows) == 1
    c = _install(_Client())
    ok, _ = db.save_held_stop_floors(rows)
    assert ok is True
    saved = c.upserts[0][0]
    assert [{k: v for k, v in r.items() if k != "written_at"} for r in saved] == rows
    assert all(r["written_at"] for r in saved)

    # what Postgres hands back: the saved payload (+ written_at), numerics as numbers
    _install(_Client(rows=[{**r, "written_at": "2026-09-30T20:05:00+00:00"} for r in saved]))
    out = db.load_held_stop_floors_or_none(["aaa"])
    ep = {"start": dt.date(2026, 9, 1), "first_buy_price": 100.0, "split_ratio": 1.0,
          "last_buy": dt.date(2026, 9, 1)}
    ticker_rows = out["AAA"]
    row = ticker_rows[ep["start"]]
    # the next session, same trades and rules: the row still describes the holding
    assert hf.match_row(row, ep, 100.0, dt.date(2026, 10, 1)) == (True, "")
    # a changed average cost (a trade) is rejected through the same loaded row
    ok, why = hf.match_row(row, ep, 101.0, dt.date(2026, 10, 1))
    assert ok is False and why.startswith("trades")

    # end to end: a lower fallback figure is lifted to the saved stop
    days = [d for d in pd.bdate_range("2026-08-20", "2026-09-30") if d.date() != dt.date(2026, 9, 7)]
    closes = [101.25 if d.date() == dt.date(2026, 9, 29) else 104.0 if d.date() == dt.date(2026, 9, 18)
              else 100.0 for d in days]
    frame = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes},
                         index=pd.DatetimeIndex(days))
    result = {"basis": "partial", "stop": 90.0, "binding": "trailing", "ratchet_label": None,
              "episode": {**ep, "status": "ok"}}
    eff = hf.apply_floor(result, ticker_rows, ep={**ep, "status": "ok"}, avg_cost=100.0,
                         frame=frame, as_of=dt.date(2026, 10, 1), readable=True)
    assert eff["floor"]["state"] == "applied", eff["floor"]
    assert eff["stop"] == 95.5


def test_loader_row_keys_cover_every_rows_to_write_key():
    as_of = dt.date(2026, 9, 30)
    row = hf.rows_to_write(_held(as_of), as_of)[0]
    _install(_Client(rows=[{**row, "written_at": "2026-09-30T20:05:00+00:00"}]))
    loaded = db.load_held_stop_floors_or_none(["AAA"])["AAA"][dt.date(2026, 9, 1)]
    assert set(row) <= set(loaded)


# ── the DDL file cannot drift from the spec ──────────────────────────────────
def _sql() -> str:
    return re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", _SQL.read_text(encoding="utf-8")))


def test_ddl_declares_the_table_pk_and_rls():
    s = _sql()
    assert "CREATE TABLE IF NOT EXISTS held_stop_floors" in s
    assert "PRIMARY KEY (ticker, episode_start)" in s
    assert "ALTER TABLE held_stop_floors ENABLE ROW LEVEL SECURITY" in s
    assert 'DROP POLICY IF EXISTS "Allow all (service role)" ON public.held_stop_floors' in s
    assert ('CREATE POLICY "Allow all (service role)" ON public.held_stop_floors '
            "FOR ALL TO service_role USING (true) WITH CHECK (true)") in s
    assert "DISABLE ROW LEVEL SECURITY" not in s.upper()


@pytest.mark.parametrize("fragment", [
    "ref_session < as_of",
    "episode_start < as_of",
    "stop_set_on <= as_of",
    "stop > 0 AND stop < 'Infinity'",
    "ticker = upper(btrim(ticker)) AND ticker <> ''",
    "binding IN ('entry', 'trailing', 'ratchet')",
    "source IN ('cron:eod')",
    "split_ratio NUMERIC NOT NULL CHECK (split_ratio > 0 AND split_ratio < 'Infinity')",
    "avg_cost NUMERIC NOT NULL CHECK (avg_cost > 0 AND avg_cost < 'Infinity')",
    "ref_close NUMERIC NOT NULL CHECK (ref_close > 0 AND ref_close < 'Infinity')",
    "bind_close NUMERIC NOT NULL CHECK (bind_close > 0 AND bind_close < 'Infinity')",
    "first_buy_price NUMERIC CHECK (first_buy_price > 0 AND first_buy_price < 'Infinity')",
    "stop_set_on DATE NOT NULL",
    "bind_session DATE NOT NULL",
    "written_at TIMESTAMPTZ DEFAULT NOW()",
    "episode_start DATE NOT NULL",
    "as_of DATE NOT NULL",
    "ref_session DATE NOT NULL",
    "policy_sig TEXT NOT NULL",
])
def test_ddl_carries_each_spec_check(fragment):
    assert fragment in _sql()


def test_ddl_has_the_verify_block():
    s = _SQL.read_text(encoding="utf-8")
    for needle in ("relrowsecurity", "pg_policies", "has_table_privilege",
                   "'INSERT'", "AS service_role_can_insert", "'UPDATE'",
                   "AS service_role_can_update", "COUNT(*)"):
        assert needle in s, needle


def test_ddl_columns_equal_the_writers_row_keys():
    as_of = dt.date(2026, 9, 30)
    row_keys = set(hf.rows_to_write(_held(as_of), as_of)[0])
    cols = set(re.findall(r"^\s{4}(\w+)\s+(?:TEXT|DATE|NUMERIC|TIMESTAMPTZ)\b",
                          _SQL.read_text(encoding="utf-8"), flags=re.M))
    assert row_keys == cols - {"written_at"}
    assert "written_at" in cols


def test_ddl_nullable_columns_are_exactly_the_spec_ones():
    s = _sql()
    cols = re.search(r"CREATE TABLE IF NOT EXISTS held_stop_floors \((.*?)PRIMARY KEY", s).group(1)
    nullable = {m.group(1) for m in re.finditer(
        r"(?:^|, )(\w+) (?:TEXT|DATE|NUMERIC|TIMESTAMPTZ)(?: CHECK \((?:[^()]|\([^()]*\))*\))?(?=,)", cols)}
    assert nullable == {"ratchet_label", "first_buy_price", "last_buy"}


def test_ddl_is_rerunnable_and_never_destructive():
    s = _sql().upper()
    assert "DROP TABLE" not in s and "TRUNCATE" not in s and "DELETE FROM" not in s


def test_readonly_check_script_only_reads():
    s = _CHECK_SQL.read_text(encoding="utf-8")
    body = re.sub(r"--[^\n]*", "", s).upper()
    for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"):
        assert not re.search(rf"\b{verb}\b", body), verb
    assert "held_stop_floors" in s and "America/New_York" in s
    assert "max(as_of)" in s
    assert "(written_at at time zone 'America/New_York')::date = as_of AS written_that_night"         in re.sub(r"\s+", " ", s).replace(" as written_that_night", " AS written_that_night")
