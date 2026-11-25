"""db.load_held_stop_carry_or_none / save_held_stop_carry / delete_held_stop_carry
and the DDL file (SlidingStop C2, docs/plans/sliding-stop.md sections 6 and 7).

Contracts that matter: the loader's None-vs-{} offline sentinel (None = couldn't
check, {} = checked and nothing saved / nothing asked), the save's batched upsert on
`ticker`, the sweep's ticker-filtered delete, the missing-table hint the EOD lane
surfaces red, and that `held_stop_carry.rows_to_write`'s keys are exactly the
table's columns (and every row the writer can emit passes the table's CHECKs).
Fake client only: no network, no DB."""
import datetime as dt
import pathlib
import re

import pytest

from stock_analyzer import db
from stock_analyzer import held_stop_carry as hc

_SQL = pathlib.Path(__file__).resolve().parent.parent / "docs" / "sql" / "held-stop-carry.sql"
MISSING_TABLE = ("{'code': 'PGRST205', 'message': \"Could not find the table "
                 "'public.held_stop_carry' in the schema cache\"}")


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
        return self

    def delete(self):
        self._c.deletes.append(True)
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
        self.deletes: list = []
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


def _db_row(ticker="AAA", as_of="2026-09-30", stop=95.5, **kw):
    r = {"ticker": ticker, "as_of": as_of, "stop": stop, "stop_set_on": "2026-09-20",
         "source_basis": "partial", "binding": "trailing", "ratchet_label": None,
         "avg_cost": 100.0, "journal_buy_count": 2, "journal_buy_max_id": 41,
         "ref_session": "2026-09-29", "ref_close": 101.25,
         "bind_session": "2026-09-18", "bind_close": 104.0, "policy_sig": "sig",
         "carry_sig": "carry-v1", "source": "cron:eod",
         "written_at": "2026-09-30T20:05:00+00:00"}
    r.update(kw)
    return r


# ── loader: None paths and the no-call cases ─────────────────────────────────
@pytest.mark.parametrize("tickers", [[], (), set(), [None, "", "  "]])
def test_no_tickers_is_empty_dict_with_no_call(tickers):
    c = _install(_Client())
    assert db.load_held_stop_carry_or_none(tickers) == {}
    assert c.tables == [] and c.reads == 0


def test_tickers_none_is_none_with_no_call():
    c = _install(_Client())
    assert db.load_held_stop_carry_or_none(None) is None      # nothing asked: couldn't check
    assert c.tables == [] and c.reads == 0


def test_no_credentials_is_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    c = _install(_Client())
    assert db.load_held_stop_carry_or_none(["AAA"]) is None
    assert c.tables == []


def test_query_error_is_none_and_recorded():
    _install(_Client(exc=RuntimeError("boom 503")))
    assert db.load_held_stop_carry_or_none(["AAA"]) is None
    assert any("held_stop_carry_read" in m for m in _ERRORS)


def test_missing_table_is_none_and_not_recorded_as_an_api_error():
    _install(_Client(exc=RuntimeError(MISSING_TABLE)))
    assert db.load_held_stop_carry_or_none(["AAA"]) is None
    assert _ERRORS == []


def test_auth_error_is_none():
    _install(_Client(exc=RuntimeError("401 Unauthorized: invalid API key")))
    assert db.load_held_stop_carry_or_none(["AAA"]) is None


def test_client_construction_failure_is_none(monkeypatch):
    def boom():
        raise RuntimeError("no client")
    monkeypatch.setattr(db, "_client", boom)
    assert db.load_held_stop_carry_or_none(["AAA"]) is None


def test_none_is_not_collapsed_with_empty():
    _install(_Client(rows=[]))
    assert db.load_held_stop_carry_or_none(["AAA"]) == {}      # read ok, nothing saved
    _install(_Client(exc=RuntimeError("down")))
    assert db.load_held_stop_carry_or_none(["AAA"]) is None    # couldn't check


@pytest.mark.parametrize("bad", [
    {"ticker": "", "as_of": "2026-09-30"},
    {"ticker": None, "as_of": "2026-09-30"},
    {"ticker": "AAA", "as_of": None},
    {"ticker": "AAA", "as_of": "garbage"},
])
def test_malformed_row_makes_the_whole_read_unreadable(bad):
    _install(_Client(rows=[_db_row(ticker="BBB"), {**_db_row(), **bad}]))
    with pytest.warns(UserWarning, match="malformed row"):
        assert db.load_held_stop_carry_or_none(["AAA", "BBB"]) is None
    assert len(_ERRORS) == 1 and _ERRORS[0].startswith("held_stop_carry_read: malformed row")


# ── loader: query shape, dedupe, parsing ─────────────────────────────────────
def test_query_uses_in_filter_upper_cased_and_deduped():
    c = _install(_Client(rows=[]))
    db.load_held_stop_carry_or_none([" aaa", "AAA", "bbb", "Aaa"])
    assert c.tables[0] == "held_stop_carry"
    assert c.ins == [("ticker", ["AAA", "BBB"])] * c.reads


def test_pagination_reads_every_page():
    rows = [_db_row(ticker=f"T{i:04d}") for i in range(2503)]
    c = _install(_Client(rows=rows))
    out = db.load_held_stop_carry_or_none([r["ticker"] for r in rows])
    assert len(out) == 2503
    assert c.reads == 4          # 1000 + 1000 + 503 + the empty terminating page


def test_server_side_cap_below_page_size_does_not_truncate():
    rows = [_db_row(ticker=f"T{i:03d}") for i in range(250)]
    _install(_Client(rows=rows, server_cap=100))
    assert len(db.load_held_stop_carry_or_none(["T000"])) == 250


def test_shape_is_flat_ticker_to_row():
    _install(_Client(rows=[_db_row("AAA"), _db_row("BBB")]))
    out = db.load_held_stop_carry_or_none(["AAA", "BBB"])
    assert set(out) == {"AAA", "BBB"}
    assert out["AAA"]["ticker"] == "AAA" and out["BBB"]["stop"] == 95.5


def test_dates_parse_to_date_numerics_to_float_and_counts_to_int():
    _install(_Client(rows=[_db_row(stop="95.50", ref_close=101, journal_buy_count="3",
                                   journal_buy_max_id=41.0,
                                   written_at="2026-09-30T20:05:00.123456+00:00")]))
    row = db.load_held_stop_carry_or_none(["AAA"])["AAA"]
    for c in ("as_of", "stop_set_on", "ref_session", "bind_session"):
        assert type(row[c]) is dt.date, c
    assert row["as_of"] == dt.date(2026, 9, 30)
    for c in ("stop", "avg_cost", "ref_close", "bind_close"):
        assert type(row[c]) is float, c
    assert row["stop"] == 95.5 and row["ref_close"] == 101.0
    assert type(row["journal_buy_count"]) is int and row["journal_buy_count"] == 3
    assert type(row["journal_buy_max_id"]) is int and row["journal_buy_max_id"] == 41
    assert row["binding"] == "trailing" and row["policy_sig"] == "sig"
    assert row["carry_sig"] == "carry-v1" and row["source"] == "cron:eod"
    assert row["ratchet_label"] is None


def test_unparseable_values_become_none_so_the_reader_calls_the_row_incomplete():
    _install(_Client(rows=[_db_row(bind_close="n/a", stop="inf", bind_session="soon",
                                   journal_buy_count="x", journal_buy_max_id=None)]))
    row = db.load_held_stop_carry_or_none(["AAA"])["AAA"]
    assert row["bind_close"] is None and row["stop"] is None and row["bind_session"] is None
    assert row["journal_buy_count"] is None and row["journal_buy_max_id"] is None
    assert hc.check_row(row, as_of=dt.date(2026, 10, 1), avg_cost=100.0, journal_sig=(2, 41),
                        journal_readable=True, policy_sig_now="sig")[0] is False


def test_duplicate_key_keeps_the_latest_as_of():
    rows = [_db_row(as_of="2026-09-30", stop=90.0), _db_row(as_of="2026-10-02", stop=97.0),
            _db_row(as_of="2026-10-01", stop=92.0)]
    _install(_Client(rows=rows))
    row = db.load_held_stop_carry_or_none(["AAA"])["AAA"]
    assert row["as_of"] == dt.date(2026, 10, 2) and row["stop"] == 97.0


def test_ticker_key_is_normalised():
    _install(_Client(rows=[_db_row(ticker=" aaa ")]))
    assert set(db.load_held_stop_carry_or_none(["AAA"])) == {"AAA"}


# ── save ─────────────────────────────────────────────────────────────────────
def _w_row(**kw):
    r = {k: v for k, v in _db_row().items() if k != "written_at"}
    r.update(kw)
    return r


def test_readonly_save_is_a_noop_with_no_client_call(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    c = _install(_Client())
    ok, detail = db.save_held_stop_carry([_w_row()])
    assert ok is False and "read-only" in detail
    assert c.tables == [] and c.upserts == []


@pytest.mark.parametrize("rows", [[], None])
def test_empty_save_makes_no_call_and_is_ok(rows):
    c = _install(_Client())
    assert db.save_held_stop_carry(rows) == (True, "nothing to save")
    assert c.tables == [] and c.upserts == []


def test_no_credentials_save_is_not_ok(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    c = _install(_Client())
    ok, _ = db.save_held_stop_carry([_w_row()])
    assert ok is False and c.tables == []


def test_one_batched_upsert_with_the_exact_on_conflict():
    c = _install(_Client())
    ok, detail = db.save_held_stop_carry([_w_row(ticker="AAA"), _w_row(ticker="BBB")])
    assert ok is True and "2" in detail
    assert c.tables == ["held_stop_carry"]
    assert len(c.upserts) == 1
    recs, kw = c.upserts[0]
    assert kw == {"on_conflict": "ticker"}
    assert [r["ticker"] for r in recs] == ["AAA", "BBB"]
    assert "ignore_duplicates" not in kw       # a same-evening re-run must rewrite the row


def test_every_row_carries_a_fresh_tz_aware_written_at():
    c = _install(_Client())
    before = dt.datetime.now(dt.timezone.utc)
    db.save_held_stop_carry([_w_row(ticker="AAA"), _w_row(ticker="BBB"),
                             _w_row(ticker="CCC", written_at="2020-01-01T00:00:00+00:00")])
    after = dt.datetime.now(dt.timezone.utc)
    recs = c.upserts[0][0]
    assert len(recs) == 3
    assert len({r["written_at"] for r in recs}) == 1      # uniform keys and value across the batch
    for r in recs:
        w = dt.datetime.fromisoformat(r["written_at"])
        assert w.tzinfo is not None and w.utcoffset() == dt.timedelta(0)
        assert before <= w <= after               # refreshed, never the caller's old value


def test_written_at_is_added_by_the_save_not_the_pure_writer():
    rows = _writer_rows()
    assert "written_at" not in rows[0]
    c = _install(_Client())
    db.save_held_stop_carry(rows)
    assert "written_at" in c.upserts[0][0][0]


def test_dates_go_out_as_iso_strings():
    c = _install(_Client())
    db.save_held_stop_carry([_w_row(as_of=dt.datetime(2026, 9, 30, 20, 5),
                                    stop_set_on=dt.date(2026, 9, 20),
                                    ref_session=dt.date(2026, 9, 29),
                                    bind_session=dt.date(2026, 9, 18))])
    rec = c.upserts[0][0][0]
    assert rec["as_of"] == "2026-09-30"
    for k in ("stop_set_on", "ref_session", "bind_session"):
        assert isinstance(rec[k], str) and len(rec[k]) == 10, k


def test_sanitize_is_applied(monkeypatch):
    seen = []
    real = db._sanitize_for_write

    def spy(payload, writer):
        seen.append(writer)
        return real(payload, writer)
    monkeypatch.setattr(db, "_sanitize_for_write", spy)
    c = _install(_Client())
    with pytest.warns(UserWarning, match="save_held_stop_carry"):
        db.save_held_stop_carry([_w_row(bind_close=float("nan"))])
    assert seen == ["save_held_stop_carry"]
    assert c.upserts[0][0][0]["bind_close"] is None     # NaN -> NULL, not a failed request


def test_input_rows_are_not_mutated():
    row = _w_row(as_of=dt.date(2026, 9, 30))
    _install(_Client())
    db.save_held_stop_carry([row])
    assert row["as_of"] == dt.date(2026, 9, 30) and "written_at" not in row


def test_write_error_returns_false_never_raises_and_is_recorded():
    _install(_Client(exc=RuntimeError("503 upstream")))
    ok, detail = db.save_held_stop_carry([_w_row()])
    assert ok is False and "503" in detail
    assert "apply docs/sql/held-stop-carry.sql" not in detail
    assert any("held_stop_carry_upsert" in m for m in _ERRORS)


def test_missing_table_save_names_the_ddl_file():
    _install(_Client(exc=RuntimeError(MISSING_TABLE)))
    ok, detail = db.save_held_stop_carry([_w_row()])
    assert ok is False
    assert "apply docs/sql/held-stop-carry.sql" in detail
    assert _ERRORS == []        # a not-yet-applied DDL isn't a Supabase outage


def test_postgres_42p01_wording_is_also_a_missing_table():
    _install(_Client(exc=RuntimeError('relation "held_stop_carry" does not exist')))
    ok, detail = db.save_held_stop_carry([_w_row()])
    assert ok is False and "apply docs/sql/held-stop-carry.sql" in detail


def test_unencodable_rows_and_client_failure_never_raise(monkeypatch):
    _install(_Client())
    ok, _ = db.save_held_stop_carry([object()])           # dict(object()) raises
    assert ok is False

    def boom():
        raise RuntimeError("no client")
    monkeypatch.setattr(db, "_client", boom)
    ok, _ = db.save_held_stop_carry([_w_row()])
    assert ok is False


# ── delete ───────────────────────────────────────────────────────────────────
def test_delete_is_one_ticker_filtered_call_upper_cased_and_deduped():
    c = _install(_Client())
    ok, detail = db.delete_held_stop_carry([" aaa", "AAA", "bbb", None, ""])
    assert ok is True and "2" in detail
    assert c.tables == ["held_stop_carry"]
    assert c.deletes == [True]
    assert c.ins == [("ticker", ["AAA", "BBB"])]        # never an unfiltered delete


@pytest.mark.parametrize("tickers", [None, [], (), set(), [None, "", "  "]])
def test_delete_with_no_tickers_is_a_noop_and_ok(tickers):
    c = _install(_Client())
    assert db.delete_held_stop_carry(tickers) == (True, "nothing to delete")
    assert c.tables == [] and c.deletes == []


def test_readonly_delete_is_a_noop_with_no_client_call(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    c = _install(_Client())
    ok, detail = db.delete_held_stop_carry(["AAA"])
    assert ok is False and "read-only" in detail
    assert c.tables == [] and c.deletes == []


def test_no_credentials_delete_is_not_ok(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    c = _install(_Client())
    ok, _ = db.delete_held_stop_carry(["AAA"])
    assert ok is False and c.tables == []


def test_delete_failure_is_false_recorded_and_never_raises():
    _install(_Client(exc=RuntimeError("503 upstream")))
    ok, detail = db.delete_held_stop_carry(["AAA"])
    assert ok is False and "503" in detail
    assert "apply docs/sql/held-stop-carry.sql" not in detail
    assert any("held_stop_carry_delete" in m for m in _ERRORS)


def test_delete_on_a_missing_table_names_the_ddl_file():
    _install(_Client(exc=RuntimeError(MISSING_TABLE)))
    ok, detail = db.delete_held_stop_carry(["AAA"])
    assert ok is False and "apply docs/sql/held-stop-carry.sql" in detail
    assert _ERRORS == []


def test_delete_client_failure_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("no client")
    monkeypatch.setattr(db, "_client", boom)
    ok, _ = db.delete_held_stop_carry(["AAA"])
    assert ok is False


# ── round trip with the pure writer / reader ─────────────────────────────────
def _held_full_trailing():
    return {"basis": "full", "stop": 95.5, "avg_cost": 100.0, "binding": "trailing",
            "ratchet_label": None, "last_session": dt.date(2026, 10, 5), "last_close": 101.25,
            "bind_session": dt.date(2026, 10, 2), "bind_close": 104.0, "stop_set_on": None}


def _held_partial_ratchet():
    return {"basis": "partial", "stop": 96.0, "avg_cost": 100.0, "binding": "ratchet",
            "ratchet_label": "locks in a gain", "last_session": dt.date(2026, 10, 5),
            "last_close": 101.25, "bind_session": dt.date(2026, 10, 1), "bind_close": 110.0,
            "stop_set_on": dt.date(2026, 10, 2), "carry": {"state": hc.APPLIED}}


def _held_prior_session():
    return {"basis": "prior_session", "stop": 94.0, "avg_cost": 100.0,
            "binding": "prior_session", "ratchet_label": None,
            "last_session": dt.date(2026, 10, 5), "last_close": 101.25,
            "bind_session": None, "bind_close": None, "stop_set_on": None,
            "carry": {"state": hc.NOT_BINDING}}


def _writer_rows(held=None, as_of=dt.date(2026, 10, 6)):    # a Tuesday, a trading day
    held = held or {"AAA": _held_full_trailing(), "BBB": _held_partial_ratchet(),
                    "CCC": _held_prior_session()}
    rows = hc.rows_to_write(
        held, as_of, journal_sigs={t: (2, 41) for t in held},
        avg_costs={t: 100.0 for t in held}, prior_rows={},
        session_closes={t: 101.0 for t in held}, journal_readable=True, carry_readable=True)
    assert len(rows) == len(held), rows
    return rows


def test_round_trip_through_save_and_load_is_accepted_by_the_reader():
    rows = _writer_rows()
    c = _install(_Client())
    ok, _ = db.save_held_stop_carry(rows)
    assert ok is True
    saved = c.upserts[0][0]
    assert [{k: v for k, v in r.items() if k != "written_at"} for r in saved] == rows
    assert all(r["written_at"] for r in saved)

    # what Postgres hands back: the saved payload (+ written_at), numerics as numbers
    _install(_Client(rows=saved))
    out = db.load_held_stop_carry_or_none(["aaa", "BBB", "ccc"])
    assert set(out) == {"AAA", "BBB", "CCC"}
    nxt = dt.date(2026, 10, 7)
    for t in out:
        # the next session, same trades and rules: the row still describes the holding
        assert hc.check_row(out[t], as_of=nxt, avg_cost=100.0, journal_sig=(2, 41),
                            journal_readable=True, policy_sig_now=rows[0]["policy_sig"]) == (True, "")
    # a changed average cost (a trade) is rejected through the same loaded row
    ok, why = hc.check_row(out["AAA"], as_of=nxt, avg_cost=101.0, journal_sig=(2, 41),
                           journal_readable=True, policy_sig_now=rows[0]["policy_sig"])
    assert ok is False and why.startswith("trades")


def test_loader_row_keys_cover_every_rows_to_write_key():
    rows = _writer_rows()
    _install(_Client(rows=[{**r, "written_at": "2026-10-06T20:05:00+00:00"} for r in rows]))
    loaded = db.load_held_stop_carry_or_none(["AAA", "BBB", "CCC"])
    for r in rows:
        assert set(r) <= set(loaded[r["ticker"]])


# ── the DDL file cannot drift from the spec ──────────────────────────────────
def _sql() -> str:
    return re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", _SQL.read_text(encoding="utf-8")))


def test_ddl_declares_the_table_pk_and_rls():
    s = _sql()
    assert "CREATE TABLE IF NOT EXISTS held_stop_carry" in s
    assert "ticker TEXT PRIMARY KEY" in s
    assert "ALTER TABLE held_stop_carry ENABLE ROW LEVEL SECURITY" in s
    assert 'DROP POLICY IF EXISTS "Allow all (service role)" ON public.held_stop_carry' in s
    assert ('CREATE POLICY "Allow all (service role)" ON public.held_stop_carry '
            "FOR ALL TO service_role USING (true) WITH CHECK (true)") in s
    assert "DISABLE ROW LEVEL SECURITY" not in s.upper()


@pytest.mark.parametrize("fragment", [
    "ref_session < as_of",
    "stop_set_on <= as_of",
    "ratchet_label IS NULL OR binding = 'ratchet'",
    "stop > 0 AND stop < 'Infinity'",
    "ticker = upper(btrim(ticker)) AND ticker <> ''",
    "source_basis IN ('full', 'partial', 'prior_session')",
    "binding IN ('entry', 'trailing', 'ratchet', 'prior_session')",
    "source IN ('cron:eod')",
    "avg_cost NUMERIC NOT NULL CHECK (avg_cost > 0 AND avg_cost < 'Infinity')",
    "ref_close NUMERIC NOT NULL CHECK (ref_close > 0 AND ref_close < 'Infinity')",
    "bind_close NUMERIC NOT NULL CHECK (bind_close > 0 AND bind_close < 'Infinity')",
    "journal_buy_count BIGINT NOT NULL CHECK (journal_buy_count >= 0)",
    "journal_buy_max_id BIGINT NOT NULL CHECK (journal_buy_max_id >= 0)",
    "stop_set_on DATE NOT NULL",
    "bind_session DATE NOT NULL",
    "written_at TIMESTAMPTZ DEFAULT NOW()",
    "as_of DATE NOT NULL",
    "ref_session DATE NOT NULL",
    "policy_sig TEXT NOT NULL",
    "carry_sig TEXT NOT NULL",
])
def test_ddl_carries_each_spec_check(fragment):
    assert fragment in _sql()


def test_ddl_has_the_verify_block():
    s = _SQL.read_text(encoding="utf-8")
    for needle in ("relrowsecurity", "pg_policies", "has_table_privilege",
                   "'INSERT'", "AS service_role_can_insert", "'UPDATE'",
                   "AS service_role_can_update", "'DELETE'", "AS service_role_can_delete",
                   "COUNT(*)"):
        assert needle in s, needle


def test_ddl_columns_equal_every_key_the_writer_can_emit():
    cols = set(re.findall(r"^\s{4}(\w+)\s+(?:TEXT|DATE|NUMERIC|BIGINT|TIMESTAMPTZ)\b",
                          _SQL.read_text(encoding="utf-8"), flags=re.M))
    keys = set()
    for r in _writer_rows():                 # full / partial+ratchet / prior_session
        keys |= set(r)
    assert keys == cols - {"written_at"}
    assert "written_at" in cols


def test_every_writer_row_satisfies_the_ddl_checks():
    """The Python image of the table's CHECKs, applied to every row the writer emits
    (a violated CHECK would fail the whole batched upsert)."""
    for r in _writer_rows():
        d = lambda k: dt.date.fromisoformat(r[k])
        assert r["ticker"] == r["ticker"].strip().upper() and r["ticker"]
        assert r["stop"] > 0 and r["avg_cost"] > 0 and r["ref_close"] > 0 and r["bind_close"] > 0
        assert r["source_basis"] in ("full", "partial", "prior_session")
        assert r["binding"] in ("entry", "trailing", "ratchet", "prior_session")
        assert r["source"] == "cron:eod"
        assert d("ref_session") < d("as_of")
        assert d("stop_set_on") <= d("as_of")
        assert r["ratchet_label"] is None or r["binding"] == "ratchet"
        assert r["journal_buy_count"] >= 0 and r["journal_buy_max_id"] >= 0
        assert r["policy_sig"] and r["carry_sig"]


def test_ddl_nullable_columns_are_exactly_ratchet_label():
    s = _sql()
    cols = re.search(r"CREATE TABLE IF NOT EXISTS held_stop_carry \((.*?) CHECK \(ref_session", s).group(1)
    nullable_free = {m.group(1) for m in re.finditer(r"(?:^|, )(\w+) (?:TEXT|DATE|NUMERIC|BIGINT|TIMESTAMPTZ)\b", cols)}
    not_null = {m.group(1) for m in re.finditer(r"(?:^|, )(\w+) \w+(?: PRIMARY KEY)?(?: CHECK \((?:[^()]|\([^()]*\))*\))? ?(?:CHECK \((?:[^()]|\([^()]*\))*\) )?NOT NULL", cols)}
    # ticker is a PRIMARY KEY (implicitly NOT NULL); written_at has a DEFAULT
    assert nullable_free - not_null - {"ticker", "written_at"} == {"ratchet_label"}


def test_ddl_is_rerunnable_and_never_destructive():
    s = _sql().upper()
    assert "DROP TABLE" not in s and "TRUNCATE" not in s and "DELETE FROM" not in s
