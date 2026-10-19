"""stock_analyzer.write_guards + the db writers that apply it (2026-10-14
impossible-states sweep).

Two production defects, both found in real Supabase data:
  * save_daily_snapshot's sweep deleted a HELD ticker that merely failed to
    price (MU vanished from 2026-06-25 with no trade either side).
  * the app's market-day writers recorded weekend/holiday-dated rows (250+).
"""
from datetime import date, datetime

import pytest
from tests._ast_cache import parse_file_cached

from stock_analyzer import db
from stock_analyzer.write_guards import snapshot_sweep_keep, split_non_trading_rows

pytestmark = pytest.mark.fast

# 2026-10-09 Fri (trading), 2026-10-10 Sat, 2026-09-07 Mon = Labor Day.
FRI, SAT, LABOR_DAY = "2026-10-09", "2026-10-10", "2026-09-07"


# ── snapshot_sweep_keep ────────────────────────────────────────────────────

def test_unpriced_held_ticker_is_kept_not_swept():
    # MU is held but absent from the batch (failed to price) -> must survive.
    assert snapshot_sweep_keep({"AAPL"}, ["AAPL", "MU"]) == ["AAPL", "MU"]


def test_exited_ticker_is_swept():
    # TSLA was written earlier today but is no longer held -> not in keep.
    keep = snapshot_sweep_keep({"AAPL"}, ["AAPL"])
    assert "TSLA" not in keep and keep == ["AAPL"]


def test_unknown_held_set_means_no_sweep():
    assert snapshot_sweep_keep({"AAPL"}, None) is None


def test_keep_normalizes_case_and_blanks():
    assert snapshot_sweep_keep({"aapl", " "}, [" mu ", ""]) == ["AAPL", "MU"]


def test_written_ticker_kept_even_if_missing_from_held():
    # A row just written is never deleted by its own sweep.
    assert snapshot_sweep_keep({"NEW"}, []) == ["NEW"]


# ── split_non_trading_rows ─────────────────────────────────────────────────

def test_weekend_and_holiday_rows_dropped():
    rows = [{"d": FRI}, {"d": SAT}, {"d": LABOR_DAY}]
    kept, dropped = split_non_trading_rows(rows, "d")
    assert kept == [{"d": FRI}] and dropped == 2


def test_date_and_datetime_values_accepted():
    rows = [{"d": date(2026, 10, 10)}, {"d": datetime(2026, 10, 9, 15, 0)}]
    kept, dropped = split_non_trading_rows(rows, "d")
    assert kept == [{"d": datetime(2026, 10, 9, 15, 0)}] and dropped == 1


def test_missing_or_garbage_date_is_kept_for_the_writer_to_judge():
    rows = [{"d": None}, {"d": "not-a-date"}, {}]
    kept, dropped = split_non_trading_rows(rows, "d")
    assert len(kept) == 3 and dropped == 0


# ── fake Supabase client ───────────────────────────────────────────────────

class _Q:
    def __init__(self, log, table):
        self._log, self._table, self._op = log, table, None
        self.not_ = self

    def upsert(self, rows, **kw):
        self._op = ("upsert", self._table, list(rows))
        return self

    def delete(self):
        self._op = ["delete", self._table, {}]
        return self

    def select(self, *_a, **_k):
        self._op = ("select", self._table, None)
        return self

    def eq(self, col, val):
        if isinstance(self._op, list):
            self._op[2][col] = val
        return self

    def in_(self, col, vals):
        if isinstance(self._op, list):
            self._op[2]["not_in:" + col] = list(vals)
        return self

    def execute(self):
        self._log.append(tuple(self._op) if isinstance(self._op, list) else self._op)

        class _R:
            data = []
        return _R()


class _Client:
    def __init__(self):
        self.log = []

    def table(self, name):
        return _Q(self.log, name)


@pytest.fixture
def fake(monkeypatch):
    c = _Client()
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "_client", lambda: c)
    return c


# ── save_daily_snapshot ────────────────────────────────────────────────────

def test_snapshot_sweep_keeps_unpriced_holding(fake):
    ok = db.save_daily_snapshot(FRI, [{"ticker": "AAPL", "shares": 4, "close_price": 300}],
                                held_tickers=["AAPL", "MU"])
    assert ok is True
    deletes = [op for op in fake.log if op[0] == "delete"]
    assert len(deletes) == 1
    assert deletes[0][2]["not_in:ticker"] == ["AAPL", "MU"]
    assert deletes[0][2]["snapshot_date"] == FRI


def test_snapshot_unknown_held_set_upserts_but_deletes_nothing(fake):
    with pytest.warns(UserWarning, match="sweep skipped"):
        ok = db.save_daily_snapshot(FRI, [{"ticker": "AAPL", "shares": 4, "close_price": 300}],
                                    held_tickers=None)
    assert ok is True
    assert [op[0] for op in fake.log] == ["upsert"]


def test_snapshot_held_tickers_is_required_keyword():
    with pytest.raises(TypeError):
        db.save_daily_snapshot(FRI, [])  # type: ignore[call-arg]


# ── market-day writers drop weekend/holiday rows ───────────────────────────

def _rec(d, t="AAPL"):
    return {"ticker": t, "rec_date": d, "rec_type": "new_pick",
            "price_at_surface": 100.0, "composite_score": 70}


def test_save_recommendations_all_weekend_is_clean_nothing_to_record(fake):
    res = db.save_recommendations([_rec(SAT), _rec(LABOR_DAY, "MU")])
    assert res == {"attempted": 0, "saved": 0, "error": None, "skipped_non_trading": 2}
    assert fake.log == []


def test_save_recommendations_mixed_batch_writes_only_trading_day(fake):
    res = db.save_recommendations([_rec(FRI), _rec(SAT, "MU")])
    assert res["attempted"] == 1 and res["error"] is None
    upserted = [r for op in fake.log if op[0] == "upsert" for r in op[2]]
    assert [r["ticker"] for r in upserted] == ["AAPL"]


def _gate(d, t="AAPL"):
    return {"ticker": t, "rec_date": d, "gate_id": "G-07", "source": "app"}


def test_save_gate_suppressions_drops_weekend_rows(fake):
    assert db.save_gate_suppressions([_gate(SAT)]) == {
        "attempted": 0, "saved": 0, "error": None, "skipped_non_trading": 1}
    assert fake.log == []
    res = db.save_gate_suppressions([_gate(FRI), _gate(SAT, "MU")])
    assert res["attempted"] == 1
    upserted = [r for op in fake.log if op[0] == "upsert" for r in op[2]]
    assert [r["ticker"] for r in upserted] == ["AAPL"]


def _sig(d, t="AAPL"):
    return {"ticker": t, "signal_date": d, "signal_type": "WATCH"}


def test_save_exit_signals_all_weekend_executes_nothing(fake):
    assert db.save_exit_signals_batch([_sig(SAT)]) is False
    assert fake.log == []


def test_save_exit_signals_mixed_batch_writes_only_trading_day(fake):
    assert db.save_exit_signals_batch([_sig(FRI), _sig(SAT, "MU")]) is True
    upserted = [r for op in fake.log if op[0] == "upsert" for r in op[2]]
    assert [r["ticker"] for r in upserted] == ["AAPL"]


# ── app.py wiring (AST pin) ────────────────────────────────────────────────

def test_app_snapshot_held_set_comes_from_raw_holdings():
    """app.py has no test harness, so pin BOTH ends of the wiring: the
    save_daily_snapshot call passes held_tickers=_snap_held, and _snap_held
    is built from st.session_state "holdings_df" -- never port_df/_snap_rows,
    which have already lost unpriced holdings."""
    import ast
    import pathlib
    tree = parse_file_cached(pathlib.Path(__file__).resolve().parents[1] / "app.py")
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "save_daily_snapshot"]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw.get("held_tickers"), ast.Name) and kw["held_tickers"].id == "_snap_held"
    assigns = {t.id: n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
               for t in n.targets if isinstance(t, ast.Name)}
    held_src = ast.unparse(assigns["_snap_held"])
    hdf_src = ast.unparse(assigns["_snap_hdf"])
    assert "_snap_hdf" in held_src and "port_df" not in held_src and "_snap_rows" not in held_src
    assert "holdings_df" in hdf_src
