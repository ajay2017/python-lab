"""Tests for cron_runner._write_live_earnings_predictions /
_mature_earnings_predictions — Predictive Modeling Shadow Layer Phase 2
(F-234, earnings-move magnitude), MEASUREMENT-ONLY.

Sibling to tests/test_cron_model_predictions_result_shape.py (the Phase 1
vol-forecast equivalent) — same {"candidates", "saved"/"matured", "error"}
result-shape discipline, extended with Phase-2-specific dedup/reschedule/
survivorship invariants the design spec calls out explicitly."""
import datetime

import pandas as pd
import pytest

import cron_runner as cr
import stock_analyzer.data as sa_data
import stock_analyzer.earnings_advisor as ea_mod
import stock_analyzer.earnings_move_forecast as emf_mod
from stock_analyzer.constants import EARNINGS_MOVE_BASELINE_K


def _matured_frame(ticker="AAPL", n=None, realized=5.0):
    n = EARNINGS_MOVE_BASELINE_K if n is None else n
    rows = [
        {
            "ticker": ticker,
            "realized_value": realized + i,
            "made_at": f"2025-{(i % 9) + 1:02d}-01T00:00:00+00:00",
            "source": "live",
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["ticker", "realized_value", "made_at"])


class _FakeStore:
    """In-memory fake for the `model_predictions` table across a
    write/dedup/reschedule test sequence — plays the role `_FakeClient` plays
    in tests/test_db_model_predictions.py, but at the db.py FUNCTION layer
    (load_unmatured_model_predictions / save_model_predictions_batch /
    withdraw_unmatured_model_prediction) rather than the query-builder layer,
    since these cron tests monkeypatch db.py's public functions directly."""

    def __init__(self):
        self.rows: list[dict] = []
        self._next_id = 1

    def unmatured(self, model_name=None):
        out = [r for r in self.rows if r.get("realized_value") is None]
        if model_name:
            out = [r for r in out if r.get("model_name") == model_name]
        return pd.DataFrame(out) if out else pd.DataFrame()

    def save_batch(self, new_rows):
        for r in new_rows:
            row = dict(r)
            row["id"] = self._next_id
            self._next_id += 1
            row.setdefault("realized_value", None)
            self.rows.append(row)
        return True

    def withdraw(self, row_id):
        """Mirrors db.withdraw_unmatured_model_prediction's real filter:
        deletes ONLY while unmatured -- a no-op (row survives) if the row
        was already matured (realized_value is set)."""
        self.rows = [
            r for r in self.rows
            if not (r.get("id") == row_id and r.get("realized_value") is None)
        ]
        return True


# ── _write_live_earnings_predictions ─────────────────────────────────────────

def _payload_for(ticker="AAPL"):
    return {"held_data": {ticker: {"risk_metrics": {"var_95": 4.0}, "sector": "Technology"}},
            "snapshot_rows": []}


def test_write_live_earnings_predictions_nothing_held_is_a_no_op(monkeypatch):
    result = cr._write_live_earnings_predictions(
        datetime.datetime(2026, 1, 12), {"held_data": {}, "snapshot_rows": []}, "bull")
    assert result == {"candidates": 0, "saved": 0, "error": None,
                       "skip_unknown_timing": 0, "skip_survivorship": 0}


def test_write_live_earnings_predictions_dedups_across_the_lead_window(monkeypatch):
    """One event, checked on 3 consecutive in-window days -- exactly one row
    must survive (dedup via the existence check), not three."""
    store = _FakeStore()
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions", store.unmatured)
    monkeypatch.setattr(cr.db, "load_model_predictions",
                         lambda model_name=None, days_back=400: _matured_frame())
    monkeypatch.setattr(cr.db, "save_model_predictions_batch", store.save_batch)
    monkeypatch.setattr(cr.db, "withdraw_unmatured_model_prediction", store.withdraw)
    monkeypatch.setattr(emf_mod, "resolve_upcoming_earnings",
                         lambda ticker, today, lookout: ("2026-01-15", "amc"))
    monkeypatch.setattr(ea_mod, "_estimate_move", lambda rm, sector: 8.0)

    payload = _payload_for()
    for day in (12, 13, 14):  # 2026-01-15 is a Thursday -- 12/13/14 are all within lead=3
        result = cr._write_live_earnings_predictions(datetime.datetime(2026, 1, day), payload, "bull")
        assert result["error"] is None

    assert len(store.rows) == 1
    assert store.rows[0]["ticker"] == "AAPL"
    assert store.rows[0]["features_snapshot"]["event_date"] == "2026-01-15"


def test_write_live_earnings_predictions_missed_middle_day_still_yields_one_row(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions", store.unmatured)
    monkeypatch.setattr(cr.db, "load_model_predictions",
                         lambda model_name=None, days_back=400: _matured_frame())
    monkeypatch.setattr(cr.db, "save_model_predictions_batch", store.save_batch)
    monkeypatch.setattr(cr.db, "withdraw_unmatured_model_prediction", store.withdraw)
    monkeypatch.setattr(emf_mod, "resolve_upcoming_earnings",
                         lambda ticker, today, lookout: ("2026-01-15", "amc"))
    monkeypatch.setattr(ea_mod, "_estimate_move", lambda rm, sector: 8.0)

    payload = _payload_for()
    for day in (12, 14):  # day 13 skipped entirely (cron didn't fire / was down)
        result = cr._write_live_earnings_predictions(datetime.datetime(2026, 1, day), payload, "bull")
        assert result["error"] is None

    assert len(store.rows) == 1


def test_write_live_earnings_predictions_never_writes_at_days_until_zero_or_less(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions", store.unmatured)
    monkeypatch.setattr(cr.db, "load_model_predictions",
                         lambda model_name=None, days_back=400: _matured_frame())
    monkeypatch.setattr(cr.db, "save_model_predictions_batch", store.save_batch)
    monkeypatch.setattr(emf_mod, "resolve_upcoming_earnings",
                         lambda ticker, today, lookout: ("2026-01-15", "amc"))
    monkeypatch.setattr(ea_mod, "_estimate_move", lambda rm, sector: 8.0)

    payload = _payload_for()
    # 2026-01-16 is the day AFTER the print -- days_until <= 0.
    result = cr._write_live_earnings_predictions(datetime.datetime(2026, 1, 16), payload, "bull")
    assert result["candidates"] == 0
    assert result["saved"] == 0
    assert len(store.rows) == 0


def test_write_live_earnings_predictions_unknown_timing_writes_nothing(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions", store.unmatured)
    monkeypatch.setattr(cr.db, "load_model_predictions",
                         lambda model_name=None, days_back=400: _matured_frame())
    monkeypatch.setattr(cr.db, "save_model_predictions_batch", store.save_batch)
    monkeypatch.setattr(emf_mod, "resolve_upcoming_earnings",
                         lambda ticker, today, lookout: ("2026-01-15", ""))  # unknown timing
    monkeypatch.setattr(ea_mod, "_estimate_move", lambda rm, sector: 8.0)

    payload = _payload_for()
    result = cr._write_live_earnings_predictions(datetime.datetime(2026, 1, 13), payload, "bull")
    assert result["candidates"] == 0
    assert result["skip_unknown_timing"] == 1
    assert len(store.rows) == 0


def test_write_live_earnings_predictions_insufficient_baseline_history_writes_nothing(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions", store.unmatured)
    # Fewer than EARNINGS_MOVE_BASELINE_K matured rows -- survivorship excludes.
    monkeypatch.setattr(cr.db, "load_model_predictions",
                         lambda model_name=None, days_back=400: _matured_frame(n=EARNINGS_MOVE_BASELINE_K - 1))
    monkeypatch.setattr(cr.db, "save_model_predictions_batch", store.save_batch)
    monkeypatch.setattr(emf_mod, "resolve_upcoming_earnings",
                         lambda ticker, today, lookout: ("2026-01-15", "amc"))
    monkeypatch.setattr(ea_mod, "_estimate_move", lambda rm, sector: 8.0)

    payload = _payload_for()
    result = cr._write_live_earnings_predictions(datetime.datetime(2026, 1, 13), payload, "bull")
    assert result["candidates"] == 0
    assert result["skip_survivorship"] == 1
    assert len(store.rows) == 0


def test_write_live_earnings_predictions_read_failure_on_pending_is_not_a_no_op(monkeypatch):
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions", lambda model_name=None: None)
    payload = _payload_for()
    result = cr._write_live_earnings_predictions(datetime.datetime(2026, 1, 13), payload, "bull")
    assert result["candidates"] == 0
    assert result["saved"] == 0
    assert result["error"] is not None


def test_write_live_earnings_predictions_read_failure_on_matured_is_not_a_no_op(monkeypatch):
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions", lambda model_name=None: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_model_predictions", lambda model_name=None, days_back=400: None)
    payload = _payload_for()
    result = cr._write_live_earnings_predictions(datetime.datetime(2026, 1, 13), payload, "bull")
    assert result["candidates"] == 0
    assert result["error"] is not None


# ── _mature_earnings_predictions ─────────────────────────────────────────────

def _pending_earnings_row(**overrides):
    row = {
        "id": 1,
        "ticker": "AAPL",
        "model_name": "earnings_move_v1",
        "predicted_value": 8.0,
        "baseline_value": 6.0,
        "realized_value": None,
        "features_snapshot": {"event_date": "2026-01-15", "bmo_amc": "amc"},
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_mature_earnings_predictions_genuinely_nothing_pending_is_a_no_op(monkeypatch):
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions", lambda model_name=None: pd.DataFrame())
    result = cr._mature_earnings_predictions(datetime.datetime(2026, 2, 15))
    assert result == {"candidates": 0, "matured": 0, "withdrawn": 0, "error": None}


def test_mature_earnings_predictions_read_failure_is_not_a_no_op(monkeypatch):
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions", lambda model_name=None: None)
    result = cr._mature_earnings_predictions(datetime.datetime(2026, 2, 15))
    assert result["candidates"] == 0
    assert result["error"] is not None


def test_mature_earnings_predictions_still_upcoming_row_survives_untouched(monkeypatch):
    # Regression (Opus review finding, 2026-09-07): the reschedule backstop
    # used to run BEFORE the maturity gate, so a pending row whose print is
    # still days away got a fresh resolve_upcoming_earnings() lookup that
    # legitimately returned the SAME frozen date (gap==0) -- misread as a
    # reschedule and withdrawn, every single cron run, before the row ever
    # had a chance to mature. Confirmed live: a row for a print 2 trading
    # days out was withdrawn the same run it was written. The fix moves the
    # maturity gate first, so this scenario must now leave the row
    # completely alone: no withdraw, no maturation attempt at all.
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions",
                         lambda model_name=None: _pending_earnings_row(features_snapshot={
                             "event_date": "2026-02-17", "bmo_amc": "amc",
                         }))
    monkeypatch.setattr(cr.db, "withdraw_unmatured_model_prediction",
                         lambda row_id: pytest.fail(
                             "a still-upcoming row (event not yet due) must never be withdrawn"))
    # If the reschedule check ran (it must not), a fresh lookup returning the
    # SAME date would previously satisfy the old 0<=gap<45 bound.
    monkeypatch.setattr(emf_mod, "resolve_upcoming_earnings",
                         lambda ticker, today, lookout: pytest.fail(
                             "must never even call resolve_upcoming_earnings for a "
                             "not-yet-due row -- the maturity gate must short-circuit first"))
    monkeypatch.setattr(cr.db, "mature_model_predictions_batch",
                         lambda updates: pytest.fail("must never attempt to mature a not-yet-due row"))

    # now_et = 2026-02-15, frozen event_date = 2026-02-17 -- print is still
    # ~2 calendar days (and at least 1 trading day) in the future.
    result = cr._mature_earnings_predictions(datetime.datetime(2026, 2, 15))
    assert result == {"candidates": 0, "matured": 0, "withdrawn": 0, "error": None}


def test_mature_earnings_predictions_reschedule_withdraws_and_never_matures(monkeypatch):
    calls = {"mature_batch": 0, "withdrawn": []}
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions",
                         lambda model_name=None: _pending_earnings_row())

    def _withdraw(row_id):
        calls["withdrawn"].append(row_id)
        return True
    monkeypatch.setattr(cr.db, "withdraw_unmatured_model_prediction", _withdraw)

    def _mature_batch(updates):
        calls["mature_batch"] += 1
        return True
    monkeypatch.setattr(cr.db, "mature_model_predictions_batch", _mature_batch)

    # Fresh lookup finds a next print only 10 days after the frozen date --
    # a postponement, not a genuine next quarterly print.
    monkeypatch.setattr(emf_mod, "resolve_upcoming_earnings",
                         lambda ticker, today, lookout: ("2026-01-25", ""))

    result = cr._mature_earnings_predictions(datetime.datetime(2026, 2, 15))
    assert result["withdrawn"] == 1
    assert result["matured"] == 0
    assert calls["withdrawn"] == [1]
    assert calls["mature_batch"] == 0, "a rescheduled row must never reach maturation"


def test_mature_earnings_predictions_absence_of_fresh_date_matures_normally(monkeypatch):
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions",
                         lambda model_name=None: _pending_earnings_row())
    monkeypatch.setattr(cr.db, "withdraw_unmatured_model_prediction",
                         lambda row_id: pytest.fail("must never withdraw on absence of a fresh date"))
    # No fresh next-earnings date found at all -- absence, not a reschedule signal.
    monkeypatch.setattr(emf_mod, "resolve_upcoming_earnings",
                         lambda ticker, today, lookout: (None, ""))
    monkeypatch.setattr(emf_mod, "realized_move", lambda hist, event_date, when: 7.5)
    monkeypatch.setattr(sa_data, "fetch_price_history", lambda ticker, period="3mo": pd.DataFrame({"Close": [1.0]}))

    saved = {}
    def _mature_batch(updates):
        saved["updates"] = updates
        return True
    monkeypatch.setattr(cr.db, "mature_model_predictions_batch", _mature_batch)

    result = cr._mature_earnings_predictions(datetime.datetime(2026, 2, 15))
    assert result["withdrawn"] == 0
    assert result["matured"] == 1
    assert saved["updates"][0]["realized_value"] == pytest.approx(7.5)
    assert saved["updates"][0]["abs_error"] == pytest.approx(abs(8.0 - 7.5))
    assert saved["updates"][0]["baseline_abs_error"] == pytest.approx(abs(6.0 - 7.5))


def test_mature_earnings_predictions_genuine_next_print_90d_out_matures_normally(monkeypatch):
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions",
                         lambda model_name=None: _pending_earnings_row())
    monkeypatch.setattr(cr.db, "withdraw_unmatured_model_prediction",
                         lambda row_id: pytest.fail("a genuine next print must never be mistaken for a reschedule"))
    # Fresh next print ~90 days after the frozen 2026-01-15 date.
    monkeypatch.setattr(emf_mod, "resolve_upcoming_earnings",
                         lambda ticker, today, lookout: ("2026-04-15", ""))
    monkeypatch.setattr(emf_mod, "realized_move", lambda hist, event_date, when: 5.0)
    monkeypatch.setattr(sa_data, "fetch_price_history", lambda ticker, period="3mo": pd.DataFrame({"Close": [1.0]}))
    monkeypatch.setattr(cr.db, "mature_model_predictions_batch", lambda updates: True)

    result = cr._mature_earnings_predictions(datetime.datetime(2026, 2, 15))
    assert result["withdrawn"] == 0
    assert result["matured"] == 1


def test_mature_earnings_predictions_bar_not_yet_available_leaves_unmatured(monkeypatch):
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions",
                         lambda model_name=None: _pending_earnings_row())
    monkeypatch.setattr(emf_mod, "resolve_upcoming_earnings",
                         lambda ticker, today, lookout: (None, ""))
    monkeypatch.setattr(emf_mod, "realized_move", lambda hist, event_date, when: None)  # not yet available
    monkeypatch.setattr(sa_data, "fetch_price_history", lambda ticker, period="3mo": pd.DataFrame({"Close": [1.0]}))
    result = cr._mature_earnings_predictions(datetime.datetime(2026, 2, 15))
    assert result == {"candidates": 0, "matured": 0, "withdrawn": 0, "error": None}


def test_mature_earnings_predictions_db_failure_preserves_candidate_count(monkeypatch):
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions",
                         lambda model_name=None: _pending_earnings_row())
    monkeypatch.setattr(emf_mod, "resolve_upcoming_earnings",
                         lambda ticker, today, lookout: (None, ""))
    monkeypatch.setattr(emf_mod, "realized_move", lambda hist, event_date, when: 4.0)
    monkeypatch.setattr(sa_data, "fetch_price_history", lambda ticker, period="3mo": pd.DataFrame({"Close": [1.0]}))
    monkeypatch.setattr(cr.db, "mature_model_predictions_batch", lambda updates: False)

    result = cr._mature_earnings_predictions(datetime.datetime(2026, 2, 15))
    assert result["candidates"] == 1
    assert result["matured"] == 0
    assert result["error"] is not None


# ── withdraw no-op against an already-matured row (fake-DB-harness check) ────

def test_fake_store_withdraw_is_a_noop_against_an_already_matured_row():
    """Mirrors db.withdraw_unmatured_model_prediction's real
    `.is_("realized_value", "null")` filter: a row that has ALREADY matured
    (realized_value set) must survive a withdraw call untouched -- proves
    the race-safety property against the maturation cron landing first."""
    store = _FakeStore()
    store.rows.append({
        "id": 1, "ticker": "AAPL", "model_name": "earnings_move_v1",
        "realized_value": 9.0, "predicted_value": 8.0, "baseline_value": 6.0,
        "features_snapshot": {"event_date": "2026-01-15", "bmo_amc": "amc"},
    })
    result = store.withdraw(1)
    assert result is True          # the call itself "succeeds"
    assert len(store.rows) == 1    # but the matured row is untouched (0 rows affected)
