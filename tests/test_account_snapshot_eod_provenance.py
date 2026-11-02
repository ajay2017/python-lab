"""N1 C4 (docs/plans/account-snapshot-repair.md): every EOD account_daily_snapshots
row records the journal book it was built from -- `journal_max_trade_id` (read
BEFORE holdings) and `book_positions` -- with `gross_book` equal to exactly what
account_snapshot_repair.plan_repairs re-sums from that book."""
import ast
import datetime
import math
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import account_snapshot_repair as R
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer.account import compute_account_snapshot
from stock_analyzer.market_time import ET

pytestmark = pytest.mark.fast

TODAY = date(2026, 11, 2)


def _ctx(port_df):
    return {"ok": True, "errors": [], "port_df": port_df, "held_data": {},
            "fragility": None, "spy_6mo": None}


def _port_df():
    return pd.DataFrame([
        {"Ticker": "aaa", "Price": 100.5, "Shares": 10, "Shares Exact": 10.0},
        {"Ticker": "BKNG", "Price": 4800.0, "Shares": 0, "Shares Exact": 0.5},   # fractional
        {"Ticker": "NOPX", "Price": float("nan"), "Shares": 5, "Shares Exact": 5.0},
        {"Ticker": "ZERO", "Price": 0.0, "Shares": 5, "Shares Exact": 5.0},
    ])


# -- compute_eod: order ------------------------------------------------------

def test_max_trade_id_is_read_before_holdings():
    calls = []
    with patch.object(hae.db, "load_max_trade_id_or_none",
                      side_effect=lambda: calls.append("max_id") or 7), \
         patch.object(hae, "_build_context",
                      side_effect=lambda t: calls.append("context") or _ctx(_port_df())):
        result = hae.compute_eod(TODAY)
    assert calls == ["max_id", "context"]
    assert result["journal_max_trade_id"] == 7


def test_max_trade_id_read_raising_is_none_not_a_crash():
    with patch.object(hae.db, "load_max_trade_id_or_none", side_effect=RuntimeError("x")), \
         patch.object(hae, "_build_context", return_value=_ctx(_port_df())):
        result = hae.compute_eod(TODAY)
    assert result["journal_max_trade_id"] is None
    assert result["book_positions"] is not None


# -- compute_eod: book shape and gross ---------------------------------------

def test_book_positions_shape_and_gross_equals_the_repair_resum():
    with patch.object(hae.db, "load_max_trade_id_or_none", return_value=2), \
         patch.object(hae, "_build_context", return_value=_ctx(_port_df())):
        result = hae.compute_eod(TODAY)
    book = result["book_positions"]
    assert book == {"AAA": {"shares": 10.0, "close": 100.5},
                    "BKNG": {"shares": 0.5, "close": 4800.0}}
    expected = round(sum(v["shares"] * v["close"] for v in book.values()), 2)
    assert result["gross_book"] == expected == 3405.0
    # the P&L baseline keeps its int-truncated shares (BKNG's 0.5 -> 0 dropped)
    assert result["snapshot_rows"] == [{"ticker": "AAA", "shares": 10.0, "close_price": 100.5}]


def test_duplicate_ticker_rows_sum_shares():
    df = pd.DataFrame([
        {"Ticker": "AAA", "Price": 10.0, "Shares": 1, "Shares Exact": 1.25},
        {"Ticker": "aaa", "Price": 10.0, "Shares": 2, "Shares Exact": 2.5},
    ])
    with patch.object(hae.db, "load_max_trade_id_or_none", return_value=1), \
         patch.object(hae, "_build_context", return_value=_ctx(df)):
        result = hae.compute_eod(TODAY)
    assert result["book_positions"] == {"AAA": {"shares": 3.75, "close": 10.0}}
    assert result["gross_book"] == 37.5


def test_book_none_when_columns_missing_falls_back_to_market_value():
    df = pd.DataFrame([{"Shares": 10, "Market Value": 1234.5}])
    with patch.object(hae.db, "load_max_trade_id_or_none", return_value=1), \
         patch.object(hae, "_build_context", return_value=_ctx(df)):
        result = hae.compute_eod(TODAY)
    assert result["book_positions"] is None
    assert result["gross_book"] == 1234.5


def test_all_rows_unpriced_is_a_failed_book_not_an_empty_one():
    df = pd.DataFrame([{"Ticker": "AAA", "Price": float("nan"), "Shares": 1, "Shares Exact": 1.0}])
    with patch.object(hae.db, "load_max_trade_id_or_none", return_value=1), \
         patch.object(hae, "_build_context", return_value=_ctx(df)):
        result = hae.compute_eod(TODAY)
    assert result["book_positions"] is None
    assert result["gross_book"] is None


# -- compute_eod: early returns ----------------------------------------------

def test_no_holdings_early_return_is_a_real_empty_book_with_the_max_id():
    with patch.object(hae.db, "load_max_trade_id_or_none", return_value=9), \
         patch.object(hae, "_build_context",
                      return_value={"ok": False, "reason": "no_holdings", "errors": []}):
        result = hae.compute_eod(TODAY)
    assert result["book_positions"] == {}
    assert result["journal_max_trade_id"] == 9


def test_db_unavailable_early_return_has_no_book():
    with patch.object(hae.db, "load_max_trade_id_or_none", return_value=None), \
         patch.object(hae, "_build_context",
                      return_value={"ok": False, "reason": "db_unavailable", "errors": ["x"]}):
        result = hae.compute_eod(TODAY)
    assert result["book_positions"] is None
    assert result["journal_max_trade_id"] is None


# -- the row the EOD writes is repairable exactly -----------------------------

CASH_AS_OF = "2026-11-02T21:15:00+00:00"        # 16:15 EST (DST ended 2026-11-01)
LATE_AT = ET.localize(datetime.datetime(2026, 11, 2, 16, 0)).astimezone(
    datetime.timezone.utc).isoformat()           # the 16:00 ET import anchor


def _eod_row(max_id=2):
    with patch.object(hae.db, "load_max_trade_id_or_none", return_value=max_id), \
         patch.object(hae, "_build_context", return_value=_ctx(_port_df())):
        payload = hae.compute_eod(TODAY)
    row = compute_account_snapshot(
        TODAY, payload["snapshot_rows"],
        {"cash_balance": -500.0, "updated_at": CASH_AS_OF},
        0.25, 7, ET.localize(datetime.datetime(2026, 11, 2, 16, 30)),
        gross_book=payload["gross_book"])
    row["journal_max_trade_id"] = payload["journal_max_trade_id"]
    row["book_positions"] = payload["book_positions"]
    return row


def _trades(*extra):
    rows = [(1, "AAA", "BUY", 10, "2026-10-01T20:00:00+00:00"),
            (2, "BKNG", "BUY", 0.5, "2026-10-02T20:00:00+00:00"), *extra]
    return pd.DataFrame([{"id": i, "ticker": t, "action": a, "shares": s, "price": 10.0,
                          "traded_at": ts} for i, t, a, s, ts in rows])


NOW = ET.localize(datetime.datetime(2026, 11, 3, 9, 0))


def test_written_row_with_no_later_trades_is_not_stale_and_plans_nothing():
    row = _eod_row()
    assert R.stale_rows([row], _trades()) == []
    assert R.plan_repairs([row], _trades(), {}, NOW) == ([], [])


def test_late_buy_is_repaired_to_old_gross_plus_shares_times_close():
    row = _eod_row()
    t = _trades((3, "NEWC", "BUY", 4, LATE_AT))
    assert R.stale_rows([row], t) == ["2026-11-02"]
    reps, refs = R.plan_repairs([row], t, {("NEWC", TODAY): 50.25}, NOW)
    assert refs == []
    assert math.isclose(reps[0]["gross_book"], row["gross_book"] + 4 * 50.25, abs_tol=0.005)
    assert reps[0]["journal_max_trade_id"] == 3
    assert reps[0]["original_gross_book"] == row["gross_book"]


@pytest.mark.parametrize("as_string", [False, True])
def test_book_survives_the_jsonb_round_trip(as_string):
    """Postgres jsonb hands keys back sorted and a client may return the column
    as a JSON string: the read-back row must still reconcile and repair."""
    import json
    row = _eod_row()
    text = json.dumps(row["book_positions"], sort_keys=True)
    # reversed insertion order: the re-sum must not depend on key order
    row["book_positions"] = text if as_string else dict(reversed(list(json.loads(text).items())))
    assert R.stale_rows([row], _trades()) == []
    assert R.plan_repairs([row], _trades(), {}, NOW) == ([], [])
    t = _trades((3, "NEWC", "BUY", 4, LATE_AT))
    reps, refs = R.plan_repairs([row], t, {("NEWC", TODAY): 50.25}, NOW)
    assert refs == []
    assert math.isclose(reps[0]["gross_book"], row["gross_book"] + 4 * 50.25, abs_tol=0.005)


# -- cron _run_eod ------------------------------------------------------------

CRON_NOW = datetime.datetime(2026, 9, 21, 17, 30)


def _cron_payload(**over):
    p = {"snapshot_rows": [{"ticker": "AAA", "shares": 10.0, "close_price": 100.0}],
         "port_df": None, "port_risk": None, "held_data": {}, "pullback": None,
         "built_at": "2026-09-21T17:30:00", "errors": [], "held_tickers": ["AAA"],
         "gross_book": 1000.0, "journal_max_trade_id": 5,
         "book_positions": {"AAA": {"shares": 10.0, "close": 100.0}}}
    p.update(over)
    return p


def _cron_run(monkeypatch, payload, save=None):
    saved = []

    def _save(row, info=None):
        saved.append(dict(row))
        if save is not None:
            return save(row, info)
        return True

    monkeypatch.setattr(cr, "compute_eod", lambda **_k: payload)
    monkeypatch.setattr(cr, "build_portfolio_risk_snapshot",
                        lambda *_a, **_k: {"portfolio_beta": None, "top_sector": None})
    monkeypatch.setattr(cr, "build_rec_event_rows", lambda *_a, **_k: [])
    monkeypatch.setattr(cr, "_write_live_vol_predictions",
                        lambda *_a, **_k: {"candidates": 0, "saved": 0, "error": None})
    monkeypatch.setattr(cr, "_mature_vol_predictions",
                        lambda *_a, **_k: {"candidates": 0, "saved": 0, "error": None})
    monkeypatch.setattr(cr, "_write_live_earnings_predictions", lambda *_a, **_k: {
        "candidates": 0, "saved": 0, "error": None,
        "skip_unknown_timing": 0, "skip_survivorship": 0})
    monkeypatch.setattr(cr, "_mature_earnings_predictions", lambda *_a, **_k: {
        "candidates": 0, "matured": 0, "withdrawn": 0, "error": None})
    monkeypatch.setattr(cr, "_notify_failure", lambda *_a, **_k: None)
    monkeypatch.setattr(cr._acct_autorepair, "run_auto_repair",
                        lambda *_a, **_k: {"ok": True, "since": "x", "checked": 0, "applied": [],
                                          "failed": [], "refusals": [], "error": None})    # N1 C5: tested in test_account_snapshot_autorepair.py
    monkeypatch.setattr(cr.db, "load_account_cash", lambda: None)
    monkeypatch.setattr(cr.db, "save_account_daily_snapshot", _save)
    monkeypatch.setattr(cr.db, "save_portfolio_risk_snapshot", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "load_trades", lambda: None)
    monkeypatch.setattr(cr.db, "save_rec_events", lambda *_a, **_k: {"saved": 0, "error": None})
    monkeypatch.setattr(cr.db, "save_daily_snapshot", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_sentiment_snapshot", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_daily_regime", lambda *_a, **_k: True)
    monkeypatch.setattr("stock_analyzer.reference_data.resolve_universe_or_none",
                        lambda name: ({}, "ok", None))
    monkeypatch.setattr("stock_analyzer.macro_calendar.detect_macro_regime",
                        lambda *_a, **_k: {"regime": "neutral"})
    rc = cr._run_eod(CRON_NOW, force=True)
    return rc, saved


def test_happy_path_saves_provenance_and_no_set_once_columns(monkeypatch):
    rc, saved = _cron_run(monkeypatch, _cron_payload())
    assert rc == 0
    assert cr._LAST_LANE_FAILURE_DETAIL is None
    assert len(saved) == 1
    assert saved[0]["journal_max_trade_id"] == 5
    assert saved[0]["book_positions"] == {"AAA": {"shares": 10.0, "close": 100.0}}
    assert "original_gross_book" not in saved[0] and "repaired_at" not in saved[0]


def test_unreadable_max_id_still_saves_the_row_and_flips_the_heartbeat(monkeypatch):
    rc, saved = _cron_run(monkeypatch, _cron_payload(journal_max_trade_id=None))
    assert rc == 1
    assert len(saved) == 1 and saved[0]["journal_max_trade_id"] is None
    assert "journal max trade id unreadable" in cr._LAST_LANE_FAILURE_DETAIL


def test_missing_book_still_saves_the_row_and_flips_the_heartbeat(monkeypatch):
    rc, saved = _cron_run(monkeypatch, _cron_payload(book_positions=None))
    assert rc == 1
    assert len(saved) == 1 and saved[0]["book_positions"] is None
    assert "book_positions unavailable" in cr._LAST_LANE_FAILURE_DETAIL


def test_dropped_provenance_columns_flip_the_heartbeat(monkeypatch):
    def _save(row, info):
        info["provenance_dropped"] = ["book_positions"]
        return True
    rc, saved = _cron_run(monkeypatch, _cron_payload(), save=_save)
    assert rc == 1
    assert "columns missing, provenance dropped" in cr._LAST_LANE_FAILURE_DETAIL


def test_empty_all_cash_book_is_not_a_provenance_failure(monkeypatch):
    rc, saved = _cron_run(monkeypatch, _cron_payload(
        snapshot_rows=[], held_tickers=[], gross_book=None, book_positions={}))
    assert rc == 0
    assert saved[0]["book_positions"] == {}


# -- AST pin (EOD end) --------------------------------------------------------

def test_run_eod_assigns_both_provenance_keys_and_never_the_set_once_ones():
    tree = ast.parse((Path(cr.__file__)).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_run_eod")
    assigned = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if (isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == "_acct_row"
                        and isinstance(tgt.slice, ast.Constant)):
                    assigned.add(tgt.slice.value)
    assert {"journal_max_trade_id", "book_positions"} <= assigned
    strings = {n.value for n in ast.walk(fn)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not any("original_gross_book" in s or "repaired_at" in s for s in strings)
