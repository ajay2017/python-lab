"""Tests for the N1 C5 automatic account-snapshot repair: the pure
`close_needs`, the I/O orchestrator `run_auto_repair` (with a fake db), the
heartbeat helpers, and the EOD / Sunday-debrief wiring in cron_runner.
docs/plans/account-snapshot-repair.md."""
import ast
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import account_snapshot_autorepair as AR
from stock_analyzer import account_snapshot_repair as R
from stock_analyzer.account import account_money_fields
from stock_analyzer.market_time import ET
from tests.test_cron_debrief_monthly_date_anchor import _RESULT, _full_lane
from tests.test_cron_eod_write_failure_propagation import NOW as EOD_NOW
from tests.test_cron_eod_write_failure_propagation import _patch_defaults, _payload

D30 = date(2026, 10, 30)
ANCHOR_30 = ET.localize(datetime(2026, 10, 30, 16, 0)).isoformat()      # EDT import anchor
CASH_30 = ET.localize(datetime(2026, 10, 30, 16, 15)).isoformat()
RUN_NOW = ET.localize(datetime(2026, 11, 1, 20, 0))
LOOKBACK = 35                      # the production constant's value; passed explicitly


def _ts(y, m, d, hh, mm=0):
    return ET.localize(datetime(y, m, d, hh, mm)).isoformat()


def trades(*rows):
    """rows: (id, ticker, action, shares, traded_at)"""
    return pd.DataFrame(
        [{"id": i, "ticker": t, "action": a, "shares": s, "price": 10.0, "traded_at": ts}
         for i, t, a, s, ts in rows])


BASE = (1, "AAA", "BUY", 10, _ts(2026, 10, 1, 16))


def row(d="2026-10-30", gross=1000.0, cash=-500.0, book=None, max_id=4,
        cash_as_of=CASH_30, rate=0.25, **extra):
    book = {"AAA": {"shares": 10, "close": 100.0}} if book is None else book
    m = account_money_fields(gross, cash, rate)
    r = {"snapshot_date": d, "gross_book": gross, "cash_balance": cash,
         "maintenance_rate": rate, "cash_as_of": cash_as_of,
         "journal_max_trade_id": max_id, "book_positions": book,
         "original_gross_book": None, "repaired_at": None, **m}
    r.update(extra)
    return r


def snap(*rows):
    """rows: (date_iso, ticker, close)"""
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{"snapshot_date": d, "ticker": t, "shares": 1, "close_price": px}
                         for d, t, px in rows])


class FakeDB:
    def __init__(self, acct, trades_df, snap_df=None, apply_ok=True):
        self.acct, self.trades, self.snap, self.apply_ok = acct, trades_df, snap_df, apply_ok
        self.snap = pd.DataFrame() if snap_df is None else snap_df
        self.acct_start = self.snap_start = "unset"
        self.writes = []
        self.snap_writes = []                  # N1 C7: (date, upserts, deletes) per call
        self.snap_ok = True                    # True / False / None (the writer's contract)
        self.raise_in = None

    def load_account_daily_snapshots(self, start_date=None, end_date=None):
        self.acct_start = start_date
        if self.raise_in == "acct":
            raise RuntimeError("boom")
        return None if self.acct is None else pd.DataFrame(self.acct)

    def load_trades_or_none(self):
        if self.raise_in == "trades":
            raise RuntimeError("boom")
        return self.trades

    def load_daily_snapshots_or_none(self, start_date=None, end_date=None):
        self.snap_start = start_date
        return self.snap

    def repair_daily_snapshot_rows(self, snapshot_date, upserts, deletes):
        self.snap_writes.append((snapshot_date, upserts, deletes))
        if not self.snap_ok:
            return self.snap_ok
        touched = set(deletes) | {u["ticker"] for u in upserts}
        keep = self.snap[~((self.snap["snapshot_date"] == snapshot_date)
                           & self.snap["ticker"].isin(touched))]
        add = pd.DataFrame([{"snapshot_date": snapshot_date, "ticker": u["ticker"],
                             "shares": u["shares"], "close_price": u["close_price"]}
                            for u in upserts])
        self.snap = pd.concat([keep, add], ignore_index=True)
        return True

    def apply_account_snapshot_repair(self, snapshot_date, fields):
        self.writes.append(snapshot_date)
        if not self.apply_ok:
            return False
        for r in self.acct:
            if r["snapshot_date"] == snapshot_date:
                r.update(fields)
        return True


def go(db, today=date(2026, 11, 1), now=RUN_NOW, fetch=None):
    return AR.run_auto_repair(db, today, now, lookback_days=LOOKBACK, fetch_closes=fetch)


LATE_BBB = (5, "BBB", "BUY", 2, ANCHOR_30)


# -- close_needs ---------------------------------------------------------------

def test_close_needs_late_buy_of_a_new_ticker():
    assert R.close_needs([row()], trades(BASE, LATE_BBB)) == {("BBB", D30)}


def test_close_needs_empty_when_the_late_trade_is_already_in_the_book():
    r = row(book={"AAA": {"shares": 10, "close": 100.0}, "BBB": {"shares": 2, "close": 50.0}})
    assert R.close_needs([r], trades(BASE, LATE_BBB)) == set()


def test_close_needs_empty_for_a_legacy_row():
    assert R.close_needs([row(max_id=None)], trades(BASE, LATE_BBB)) == set()


def test_close_needs_empty_when_trades_are_unusable():
    assert R.close_needs([row()], None) == set()
    assert R.close_needs([row()], pd.DataFrame({"ticker": ["AAA"]})) == set()


def test_close_needs_includes_a_book_position_with_no_stored_close():
    r = row(book={"AAA": {"shares": 10, "close": None}})
    assert R.close_needs([r], trades(BASE, LATE_BBB)) == {("BBB", D30), ("AAA", D30)}


def test_close_needs_skips_a_late_full_exit():
    t = trades(BASE, (5, "AAA", "SELL", 10, ANCHOR_30))
    assert R.close_needs([row()], t) == set()


# -- run_auto_repair -----------------------------------------------------------

def test_repairs_a_stale_row_with_the_stored_daily_snapshots_close():
    db = FakeDB([row()], trades(BASE, LATE_BBB), snap(("2026-10-30", "BBB", 50.0)))
    called = []
    res = go(db, fetch=lambda n, h: called.append(n) or {})
    assert res["ok"] and res["applied"] == ["2026-10-30"] and res["failed"] == []
    assert res["refusals"] == [] and res["error"] is None and res["checked"] == 1
    assert db.acct[0]["gross_book"] == 1100.0
    assert db.acct[0]["book_positions"]["BBB"] == {"shares": 2.0, "close": 50.0}
    assert db.acct[0]["journal_max_trade_id"] == 5
    assert db.acct[0]["repaired_at"] == RUN_NOW.isoformat()
    assert called == [{("BBB", D30)}]


def test_close_comes_from_the_injected_fetch_and_only_for_missing_pairs():
    db = FakeDB([row()], trades(BASE, LATE_BBB), snap())
    calls = []

    def fetch(needs, have):
        calls.append((set(needs), dict(have)))
        return {("BBB", D30): 50.0}
    res = go(db, fetch=fetch)
    assert res["applied"] == ["2026-10-30"] and db.acct[0]["gross_book"] == 1100.0
    assert calls == [({("BBB", D30)}, {})]


def test_fetch_is_not_called_when_nothing_is_needed():
    db = FakeDB([row()], trades(BASE), snap())            # nothing stale
    res = go(db, fetch=lambda n, h: pytest.fail("fetch must not run"))
    assert res["ok"] and res["applied"] == [] and res["refusals"] == []


def test_excludes_todays_row_old_rows_and_passes_the_lookback_start():
    today = date(2026, 11, 1)
    rows = [row(d="2026-09-01", cash_as_of=_ts(2026, 9, 1, 16, 15)),
            row(d="2026-10-30"),
            row(d="2026-11-01", cash_as_of=_ts(2026, 11, 1, 16, 15))]
    t = trades(BASE, (5, "BBB", "BUY", 2, _ts(2026, 9, 1, 16)),
               (6, "BBB", "BUY", 2, ANCHOR_30), (7, "BBB", "BUY", 2, _ts(2026, 11, 1, 16)))
    db = FakeDB(rows, t, snap(("2026-10-30", "BBB", 50.0), ("2026-11-01", "BBB", 50.0),
                              ("2026-09-01", "BBB", 50.0)))
    res = go(db, today=today)
    assert db.acct_start == "2026-09-27" and db.snap_start == "2026-09-27"
    assert res["checked"] == 1 and res["since"] == "2026-09-27"
    assert db.writes == ["2026-10-30"]


def test_second_run_plans_nothing():
    db = FakeDB([row()], trades(BASE, LATE_BBB), snap(("2026-10-30", "BBB", 50.0)))
    assert go(db)["applied"] == ["2026-10-30"]
    again = go(db)
    assert again["ok"] and again["applied"] == [] and again["refusals"] == []
    assert db.writes == ["2026-10-30"]


def test_oversell_is_refused_nothing_written_and_reported():
    t = trades(BASE, (5, "AAA", "SELL", 20, ANCHOR_30))
    db = FakeDB([row()], t, snap())
    res = go(db)
    assert res["ok"] and db.writes == [] and res["applied"] == []
    assert [x["snapshot_date"] for x in res["refusals"]] == ["2026-10-30"]
    lines = AR.failure_lines(res)
    assert len(lines) == 1 and lines[0].startswith("account_snapshot_repair: refused 2026-10-30:")


def test_no_close_anywhere_is_refused():
    db = FakeDB([row()], trades(BASE, LATE_BBB), snap())
    res = go(db, fetch=lambda n, h: {})
    assert db.writes == [] and "no close" in res["refusals"][0]["reason"]
    assert AR.failure_lines(res)


def test_apply_returning_false_is_a_failed_write_and_a_failure_line():
    db = FakeDB([row()], trades(BASE, LATE_BBB), snap(("2026-10-30", "BBB", 50.0)), apply_ok=False)
    res = go(db)
    assert res["ok"] and res["failed"] == ["2026-10-30"] and res["applied"] == []
    assert AR.failure_lines(res) == ["account_snapshot_repair: write failed for 2026-10-30"]
    assert "write failed 2026-10-30" in AR.summary_line(res)


@pytest.mark.parametrize("which,err", [
    ("acct", "account_daily_snapshots unreadable"),
    ("trades", "trades unreadable"),
    ("snap", "daily_snapshots unreadable"),
])
def test_each_unreadable_input_is_an_error_result(which, err):
    db = FakeDB([row()], trades(BASE, LATE_BBB), snap())
    if which == "acct":
        db.acct = None
    elif which == "trades":
        db.trades = None
    else:
        db.snap = None
    res = go(db)
    assert res["ok"] is False and res["error"] == err and db.writes == []
    assert AR.failure_lines(res) == [f"account_snapshot_repair: {err}"]
    assert set(res) == {"ok", "since", "checked", "applied", "failed", "refusals", "error", "details",
                        "snapshot_rows_applied", "snapshot_rows_failed", "snapshot_rows_refused",
                        "snapshot_rows_summary"}


@pytest.mark.parametrize("which", ["acct", "trades"])
def test_a_loader_that_raises_never_propagates(which):
    db = FakeDB([row()], trades(BASE, LATE_BBB), snap())
    db.raise_in = which
    res = go(db)
    assert res["ok"] is False and "boom" in res["error"]


def test_clean_run_has_no_failure_lines_and_a_readable_summary():
    res = go(FakeDB([row()], trades(BASE), snap()))
    assert AR.failure_lines(res) == []
    assert AR.summary_line(res) == ("account_snapshot_repair: checked 1 row(s) since 2026-09-27"
                                    " · applied none · refused 0")


def test_refusal_lines_are_capped_at_five():
    res = {"ok": True, "since": "x", "checked": 7, "applied": [], "failed": [], "error": None,
           "refusals": [{"snapshot_date": f"2026-10-{i:02d}", "reason": "r"} for i in range(1, 8)]}
    lines = AR.failure_lines(res)
    assert len(lines) == 6 and lines[-1] == "account_snapshot_repair: … +2 more"


# -- time-context matrix --------------------------------------------------------

@pytest.mark.parametrize("friday,sunday", [
    (date(2026, 10, 30), date(2026, 11, 1)),      # EDT
    (date(2026, 11, 6), date(2026, 11, 8)),       # EST (after the DST change)
])
def test_sunday_pre_step_repairs_fridays_row_for_a_saturday_logged_fill(friday, sunday):
    fri = friday.isoformat()
    anchor = _ts(friday.year, friday.month, friday.day, 16)
    cash_as_of = _ts(friday.year, friday.month, friday.day, 16, 15)
    db = FakeDB([row(d=fri, cash_as_of=cash_as_of)],
                trades(BASE, (5, "BBB", "BUY", 2, anchor)), snap((fri, "BBB", 50.0)))
    res = go(db, today=sunday, now=ET.localize(datetime(sunday.year, sunday.month, sunday.day, 20, 0)))
    assert res["applied"] == [fri] and db.acct[0]["gross_book"] == 1100.0


def test_after_hours_fill_logged_the_same_evening_is_not_added():
    after = _ts(2026, 10, 30, 18)                              # 18:00 ET, after the cash read
    db = FakeDB([row()], trades(BASE, (5, "BBB", "BUY", 2, after)), snap(("2026-10-30", "BBB", 50.0)))
    res = go(db)
    assert res["ok"] and res["applied"] == [] and res["refusals"] == [] and db.writes == []


def test_holiday_dated_trade_goes_to_the_next_trading_day_row_only():
    thanks = _ts(2026, 11, 26, 16)                             # anchored 16:00 ET on Thanksgiving
    before = row(d="2026-11-25", max_id=8, cash_as_of=_ts(2026, 11, 25, 16, 15))
    after = row(d="2026-11-27", max_id=8, cash_as_of=_ts(2026, 11, 27, 16, 15))
    db = FakeDB([before, after], trades(BASE, (9, "BBB", "BUY", 2, thanks)),
                snap(("2026-11-25", "BBB", 50.0), ("2026-11-27", "BBB", 50.0)))
    res = go(db, today=date(2026, 11, 29), now=ET.localize(datetime(2026, 11, 29, 20, 0)))
    assert res["applied"] == ["2026-11-27"]
    assert db.acct[0]["gross_book"] == 1000.0 and db.acct[1]["gross_book"] == 1100.0


def test_a_late_sell_lowers_gross():
    db = FakeDB([row()], trades(BASE, (5, "AAA", "SELL", 4, ANCHOR_30)), snap())
    res = go(db)
    assert res["applied"] == ["2026-10-30"] and db.acct[0]["gross_book"] == 600.0


# -- cron wiring ----------------------------------------------------------------

CLEAN = {"ok": True, "since": "x", "checked": 0, "applied": [], "failed": [], "refusals": [], "error": None}


def _eod(monkeypatch, repair_result, *, order):
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _payload(
        snapshot_rows=[{"ticker": "AAA", "shares": 10.0, "close_price": 100.0}]))
    monkeypatch.setattr(cr._acct_autorepair, "run_auto_repair",
                        lambda *a, **k: order.append("repair") or repair_result)
    monkeypatch.setattr(cr.db, "save_daily_snapshot", lambda *_a, **_k: order.append("daily") or True)
    monkeypatch.setattr(cr.db, "save_account_daily_snapshot",
                        lambda *_a, **_k: order.append("account") or True)
    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", None)
    return cr._run_eod(EOD_NOW, force=True)


def test_eod_repairs_after_the_daily_snapshot_and_before_the_account_write(monkeypatch):
    order = []
    assert _eod(monkeypatch, CLEAN, order=order) == 0
    assert order.index("daily") < order.index("repair") < order.index("account")
    assert cr._LAST_LANE_FAILURE_DETAIL is None


def test_eod_repair_failure_flips_the_heartbeat_but_the_account_row_is_still_saved(monkeypatch):
    order = []
    bad = {**CLEAN, "refusals": [{"snapshot_date": "2026-10-30", "reason": "no close"}]}
    assert _eod(monkeypatch, bad, order=order) == 1
    assert "account" in order
    assert "account_snapshot_repair: refused 2026-10-30: no close" in cr._LAST_LANE_FAILURE_DETAIL


def test_eod_repair_that_raises_is_a_failure_not_a_crash(monkeypatch):
    order = []
    _patch_defaults(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(cr._acct_autorepair, "run_auto_repair", boom)
    monkeypatch.setattr(cr.db, "save_account_daily_snapshot",
                        lambda *_a, **_k: order.append("account") or True)
    assert cr._run_eod(EOD_NOW, force=True) == 1
    assert order == ["account"] and "account_snapshot_repair: kaboom" in cr._LAST_LANE_FAILURE_DETAIL


def test_debrief_runs_the_repair_before_loading_engine_inputs(monkeypatch):
    from stock_analyzer import period_inputs as pi
    seen = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cr, "_db_unavailable_detail", lambda: None)
    monkeypatch.setattr(cr, "_notify_failure", lambda *a, **k: None)
    def _engine(db, s, e, **kw):
        seen["engine_after_repair"] = "repair" in seen
        return {"trades_df": None, "account_snapshots_df": pd.DataFrame(),
                "daily_snapshots_df": pd.DataFrame()}
    monkeypatch.setattr(pi, "load_engine_inputs", _engine)
    monkeypatch.setattr(cr._acct_autorepair, "run_auto_repair",
                        lambda *a, **k: seen.update(repair=True) or CLEAN)
    cr._run_debrief(ET.localize(datetime(2026, 11, 1, 20, 0)), force=False)
    assert seen == {"repair": True, "engine_after_repair": True}


def test_debrief_with_a_repair_failure_still_generates_and_saves_but_fails_the_lane(monkeypatch):
    notified, calls = [], []
    bad = {**CLEAN, "ok": False, "error": "trades unreadable"}
    rc = _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified,
                    repair=lambda *a, **k: bad, calls=calls)
    assert rc == 1 and calls == ["generate", "save"]
    want = "debrief saved; account_snapshot_repair: trades unreadable"
    assert notified == [("debrief", want)]
    assert cr._LAST_LANE_FAILURE_DETAIL == want


def test_debrief_happy_path_returns_zero(monkeypatch):
    notified = []
    assert _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified,
                      repair=lambda *a, **k: CLEAN) == 0
    assert notified == []


def test_debrief_repair_that_raises_is_a_failure_line_not_a_crash(monkeypatch):
    notified = []

    def boom(*a, **k):
        raise RuntimeError("kaboom")
    assert _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified,
                      repair=boom) == 1
    assert notified == [("debrief", "debrief saved; account_snapshot_repair: kaboom")]


# -- AST pin ---------------------------------------------------------------------

@pytest.mark.parametrize("fn", ["_run_eod", "_run_debrief"])
def test_both_lanes_call_run_auto_repair(fn):
    tree = ast.parse(Path(cr.__file__).read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn)
    assert any(isinstance(n, ast.Attribute) and n.attr == "run_auto_repair"
               for n in ast.walk(node))
    # C6: the lanes go through the deduping helper, never `failure_lines` directly.
    assert any(isinstance(n, ast.Name) and n.id == "_repair_failure_lines" for n in ast.walk(node))
    assert not any(isinstance(n, ast.Attribute) and n.attr == "failure_lines" for n in ast.walk(node))


def test_ast_pin_lookback_is_passed_by_constant_name_not_a_literal():
    tree = ast.parse(Path(cr.__file__).read_text(encoding="utf-8"))
    for fn in ("_run_eod", "_run_debrief"):
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn)
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "run_auto_repair"]
        assert calls
        for c in calls:
            kw = {k.arg: k.value for k in c.keywords}
            assert isinstance(kw["lookback_days"], ast.Name)
            assert kw["lookback_days"].id == "ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS"


def test_lookback_stays_inside_the_close_fetch_window():
    from stock_analyzer.constants import ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS
    assert AR._CLOSE_FETCH_PERIOD == "6mo"          # about 180 days
    assert ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS <= 150


def test_lookback_boundary_today_minus_35_included_minus_36_excluded():
    today = date(2026, 11, 1)
    d35, d36 = date(2026, 9, 27), date(2026, 9, 26)
    rows = [row(d=d.isoformat(), cash_as_of=_ts(d.year, d.month, d.day, 16, 15)) for d in (d35, d36)]
    t = trades(BASE, (5, "BBB", "BUY", 2, _ts(2026, 9, 26, 16)), (6, "BBB", "BUY", 2, _ts(2026, 9, 27, 16)))
    db = FakeDB(rows, t, snap(("2026-09-27", "BBB", 50.0), ("2026-09-26", "BBB", 50.0)))
    res = go(db, today=today)
    assert res["checked"] == 1 and res["applied"] == ["2026-09-27"]


@pytest.mark.parametrize("reason", ["no_bundles", "empty_port_df"])
def test_repair_still_runs_on_a_total_price_outage_day(monkeypatch, reason):
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _payload(
        reason=reason, errors=["no holdings could be loaded"]))
    order = []
    monkeypatch.setattr(cr._acct_autorepair, "run_auto_repair",
                        lambda *a, **k: order.append("repair") or CLEAN)
    assert cr._run_eod(EOD_NOW, force=True) == 1       # the outage itself still fails the lane
    assert order == ["repair"]


def test_repair_still_runs_on_a_partial_outage_day(monkeypatch):
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _payload(
        snapshot_rows=[{"ticker": "AAPL", "shares": 10, "close_price": 200.0}],
        held_tickers=["AAPL", "MSFT"]))
    order = []
    monkeypatch.setattr(cr._acct_autorepair, "run_auto_repair",
                        lambda *a, **k: order.append("repair") or CLEAN)
    assert cr._run_eod(EOD_NOW, force=True) == 1
    assert order == ["repair"]


def test_summary_line_prints_the_applied_details():
    db = FakeDB([row(gross=15272.55)], trades(BASE, LATE_BBB), snap(("2026-10-30", "BBB", 50.0)))
    res = go(db)
    assert res["details"] == [{"date": "2026-10-30", "old_gross": 15272.55,
                               "new_gross": 1100.0, "max_late_id": 5}]
    assert "applied 2026-10-30 (gross 15,272.55 -> 1,100.00, trades to #5)" in AR.summary_line(res)


def test_debrief_final_repair_failure_says_the_debrief_was_saved(monkeypatch):
    notified = []
    bad = {**CLEAN, "failed": ["2026-10-30"]}
    assert _full_lane(monkeypatch, generate=dict(_RESULT), saved=True, notified=notified,
                      repair=lambda *a, **k: bad) == 1
    assert notified[0][1] == ("debrief saved; "
                              "account_snapshot_repair: write failed for 2026-10-30")


def test_debrief_earlier_failure_return_appends_the_repair_lines(monkeypatch):
    notified = []
    bad = {**CLEAN, "failed": ["2026-10-30"]}
    assert _full_lane(monkeypatch, generate=dict(_RESULT), saved=False, notified=notified,
                      repair=lambda *a, **k: bad) == 1
    assert notified[0][1].startswith("weekly_debriefs: save failed")
    assert notified[0][1].endswith("; account_snapshot_repair: write failed for 2026-10-30")


# -- N1 C7: per-ticker daily_snapshots rows of repaired account rows ------------------

def snap_sh(*rows):
    """rows: (date_iso, ticker, shares, close)"""
    return pd.DataFrame([{"snapshot_date": d, "ticker": t, "shares": n, "close_price": px}
                         for d, t, n, px in rows])


REPAIRED = {"repaired_at": ANCHOR_30, "original_gross_book": 900.0}
BOOK_A10 = {"AAA": {"shares": 10.0, "close": 100.0}}


def test_loaded_repaired_row_with_stale_snapshots_is_reconciled():
    book = {"AAA": {"shares": 10.0, "close": 100.0}, "BBB": {"shares": 2.0, "close": 50.0}}
    db = FakeDB([row(book=book, **REPAIRED)], trades(BASE, LATE_BBB),
                snap_sh(("2026-10-30", "AAA", 7, 100.0), ("2026-10-30", "GONE", 3, 5.0)))
    res = go(db)
    assert res["ok"] and res["applied"] == [] and db.writes == []       # the account row is not stale
    assert db.snap_writes == [("2026-10-30",
                               [{"ticker": "AAA", "shares": 10, "close_price": 100.0},
                                {"ticker": "BBB", "shares": 2, "close_price": 50.0}],
                               ["GONE"])]
    assert res["snapshot_rows_applied"] == ["2026-10-30"] and res["snapshot_rows_failed"] == []
    assert res["snapshot_rows_refused"] == []
    assert res["snapshot_rows_summary"] == "snapshot rows: 2026-10-30 AAA 7→10, +BBB 2, -GONE"
    assert "snapshot rows applied 2026-10-30" in AR.summary_line(res)


def test_row_repaired_in_this_run_uses_the_new_book_not_the_stale_loaded_one():
    db = FakeDB([row()], trades(BASE, LATE_BBB), snap(("2026-10-30", "BBB", 50.0)))
    res = go(db)
    assert res["applied"] == ["2026-10-30"] and res["snapshot_rows_applied"] == ["2026-10-30"]
    (d, ups, dels), = db.snap_writes
    assert d == "2026-10-30" and dels == []
    assert {u["ticker"]: u["shares"] for u in ups} == {"AAA": 10, "BBB": 2}   # BBB only in the NEW book


def test_a_failed_account_write_does_not_reconcile_with_the_unwritten_book():
    db = FakeDB([row()], trades(BASE, LATE_BBB), snap(("2026-10-30", "BBB", 50.0)), apply_ok=False)
    res = go(db)
    assert res["failed"] == ["2026-10-30"] and db.snap_writes == []
    assert res["snapshot_rows_applied"] == [] and res["snapshot_rows_failed"] == []


def test_row_without_repaired_at_is_left_alone_even_when_snapshots_differ():
    db = FakeDB([row()], trades(BASE), snap_sh(("2026-10-30", "AAA", 7, 100.0)))
    res = go(db)
    assert res["ok"] and db.snap_writes == [] and res["snapshot_rows_applied"] == []
    assert res["snapshot_rows_summary"] == "snapshot rows: no change"


def test_todays_row_is_never_reconciled_even_if_marked_repaired():
    db = FakeDB([row(d="2026-11-01", cash_as_of=_ts(2026, 11, 1, 16, 15), **REPAIRED)],
                trades(BASE), snap_sh(("2026-11-01", "AAA", 7, 100.0)))
    res = go(db, today=date(2026, 11, 1))
    assert res["checked"] == 0 and db.snap_writes == []


@pytest.mark.parametrize("bad", [None, False])
def test_snapshot_row_write_failure_is_reported_and_a_failure_line(bad):
    db = FakeDB([row(book=BOOK_A10, **REPAIRED)], trades(BASE), snap_sh(("2026-10-30", "AAA", 7, 100.0)))
    db.snap_ok = bad
    res = go(db)
    assert res["ok"] and res["snapshot_rows_failed"] == ["2026-10-30"]
    assert res["snapshot_rows_applied"] == []
    assert AR.failure_lines(res) == ["account_snapshot_repair: snapshot-row write failed for 2026-10-30"]
    assert "snapshot rows write failed 2026-10-30" in AR.summary_line(res)


def test_a_writer_that_raises_is_a_failed_date_not_a_crash():
    db = FakeDB([row(book=BOOK_A10, **REPAIRED)], trades(BASE), snap_sh(("2026-10-30", "AAA", 7, 100.0)))

    def boom(*a, **k):
        raise RuntimeError("boom")
    db.repair_daily_snapshot_rows = boom
    res = go(db)
    assert res["ok"] and res["snapshot_rows_failed"] == ["2026-10-30"]


def test_second_run_after_reconcile_makes_no_snapshot_writes():
    db = FakeDB([row(book=BOOK_A10, **REPAIRED)], trades(BASE), snap_sh(("2026-10-30", "AAA", 7, 100.0)))
    assert go(db)["snapshot_rows_applied"] == ["2026-10-30"]
    again = go(db)
    assert again["snapshot_rows_applied"] == [] and len(db.snap_writes) == 1
    assert AR.failure_lines(again) == []


def test_repaired_row_without_a_book_is_a_no_book_refusal_and_a_failure_line():
    db = FakeDB([row(**REPAIRED)], trades(BASE), snap_sh(("2026-10-30", "AAA", 7, 100.0)))
    db.acct[0]["book_positions"] = None
    res = go(db)
    assert db.snap_writes == []
    assert [x["kind"] for x in res["snapshot_rows_refused"]] == ["no_book"]
    (line,) = AR.failure_lines(res)
    assert line.startswith("account_snapshot_repair: snapshot rows refused 2026-10-30:")


def test_repaired_row_on_a_non_trading_day_is_refused_and_alarmed():
    sat = row(d="2026-10-31", book=BOOK_A10, cash_as_of=_ts(2026, 10, 31, 16, 15), **REPAIRED)
    db = FakeDB([sat], trades(BASE), snap_sh(("2026-10-31", "AAA", 7, 100.0)))
    res = go(db)
    assert db.snap_writes == []
    assert [x["kind"] for x in res["snapshot_rows_refused"]] == ["non_trading_day"]
    (line,) = AR.failure_lines(res)
    assert line.startswith("account_snapshot_repair: snapshot rows refused 2026-10-31:")


def test_repaired_book_implying_no_rows_is_refused_empty_book_and_alarmed():
    tiny = {"AAA": {"shares": 0.5, "close": 100.0}}
    db = FakeDB([row(book=tiny, **REPAIRED)], trades(BASE), snap_sh(("2026-10-30", "AAA", 1, 100.0)))
    res = go(db)
    assert db.snap_writes == []
    assert [x["kind"] for x in res["snapshot_rows_refused"]] == ["empty_book"]
    assert len(AR.failure_lines(res)) == 1


def test_snapshot_row_refusal_lines_are_capped_at_five():
    res = {"ok": True, "since": "x", "checked": 7, "applied": [], "failed": [], "error": None,
           "refusals": [], "snapshot_rows_refused": [
               {"snapshot_date": f"2026-10-{i:02d}", "kind": "no_book", "reason": "r"}
               for i in range(1, 8)]}
    lines = AR.failure_lines(res)
    assert len(lines) == 6 and lines[-1] == "account_snapshot_repair: … +2 more snapshot-row refusals"


def test_results_without_snapshot_row_keys_still_format():
    assert AR.failure_lines(CLEAN) == []
    assert "snapshot rows" not in AR.summary_line(CLEAN)
