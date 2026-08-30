"""Regression guard: `_write_live_vol_predictions` / `_mature_vol_predictions`
must return a structured result, not a bare row count.

Both functions used to `return 0` for THREE structurally different reasons
that all logged identically as "0 row(s) written" at the eod-lane call site:
(1) genuinely nothing to do (no held tickers / nothing pending), (2) every
candidate was filtered out for insufficient data (bars too thin / maturation
not yet due), and (3) candidates WERE computed but the DB write itself
failed (`db.save_model_predictions_batch`/`db.mature_model_predictions_batch`
returned False). Cases (1)/(2) are legitimate no-ops; case (3) is a real
failure that was silently indistinguishable from them in the log.

Both functions now return {"candidates": int, "saved": int, "error": str |
None}: `error is None and candidates == 0` = nothing to do; `error is None
and candidates == saved > 0` = normal success; `error is not None` = the
write failed after candidates were computed (`saved == 0`, `candidates`
preserved so the failure's size is still visible in the log)."""
import datetime

import numpy as np
import pandas as pd

import cron_runner as cr
import stock_analyzer.data as sa_data


# ── _write_live_vol_predictions ───────────────────────────────────────────

def _closes(values):
    return pd.DataFrame({"Close": list(values)})


def test_write_live_vol_predictions_nothing_held_is_a_no_op_not_a_failure():
    payload = {"held_data": {}, "snapshot_rows": []}
    result = cr._write_live_vol_predictions(datetime.datetime(2026, 1, 1), payload, "bull")
    assert result == {"candidates": 0, "saved": 0, "error": None}


def test_write_live_vol_predictions_bars_too_thin_is_a_no_op_not_a_failure():
    # A single close price -> pct_change().dropna() is empty -> no candidate
    # row is ever built for this ticker, and there's no portfolio aggregate
    # (no snapshot_rows) to fall back to either.
    payload = {
        "held_data": {"AAPL": {"df": _closes([100.0])}},
        "snapshot_rows": [],
    }
    result = cr._write_live_vol_predictions(datetime.datetime(2026, 1, 1), payload, "bull")
    assert result == {"candidates": 0, "saved": 0, "error": None}


def test_write_live_vol_predictions_success_reports_saved_equal_to_candidates(monkeypatch):
    monkeypatch.setattr(cr.db, "save_model_predictions_batch", lambda rows: True)
    payload = {
        "held_data": {"AAPL": {"df": _closes([100, 101, 99, 102, 98, 103, 97])}},
        "snapshot_rows": [],  # no portfolio aggregate — keep this to 1 candidate row
    }
    result = cr._write_live_vol_predictions(datetime.datetime(2026, 1, 1), payload, "bull")
    assert result["error"] is None
    assert result["candidates"] == 1
    assert result["saved"] == result["candidates"]


def test_write_live_vol_predictions_db_failure_preserves_candidate_count(monkeypatch):
    monkeypatch.setattr(cr.db, "save_model_predictions_batch", lambda rows: False)
    payload = {
        "held_data": {"AAPL": {"df": _closes([100, 101, 99, 102, 98, 103, 97])}},
        "snapshot_rows": [],
    }
    result = cr._write_live_vol_predictions(datetime.datetime(2026, 1, 1), payload, "bull")
    assert result["candidates"] == 1, "the candidate row was computed, not skipped"
    assert result["saved"] == 0
    assert result["error"] is not None, "a real write failure must not look like a no-op"


# ── _mature_vol_predictions ────────────────────────────────────────────────

def _pending_frame(**overrides):
    row = {
        "id": 1,
        "ticker": "AAPL",
        "scope": "ticker",
        "made_at": "2026-01-01T00:00:00+00:00",
        "horizon_days": 20,
        "predicted_value": 0.20,
        "baseline_value": 0.18,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _history_spanning(made_date: datetime.date, end_date: datetime.date):
    """A daily-close history running from well before `made_date` through
    well after `end_date`, with enough variance that realized_vol() doesn't
    return None — long enough that the forward window (made_date, today]
    contains at least `horizon_days` trading-day returns."""
    idx = pd.date_range(made_date - datetime.timedelta(days=30),
                         end_date + datetime.timedelta(days=5), freq="B")
    rng = np.random.RandomState(7)
    prices = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, size=len(idx)))
    return pd.DataFrame({"Close": prices}, index=idx)


def test_mature_vol_predictions_nothing_pending_is_a_no_op_not_a_failure(monkeypatch):
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions", lambda model_name=None: None)
    result = cr._mature_vol_predictions(datetime.datetime(2026, 2, 15))
    assert result == {"candidates": 0, "saved": 0, "error": None}


def test_mature_vol_predictions_nothing_due_yet_is_a_no_op_not_a_failure(monkeypatch):
    # made_at is TODAY -> zero trading days elapsed -> horizon not due.
    monkeypatch.setattr(
        cr.db, "load_unmatured_model_predictions",
        lambda model_name=None: _pending_frame(made_at="2026-02-15T00:00:00+00:00"),
    )
    result = cr._mature_vol_predictions(datetime.datetime(2026, 2, 15))
    assert result == {"candidates": 0, "saved": 0, "error": None}


def test_mature_vol_predictions_success_reports_saved_equal_to_candidates(monkeypatch):
    made_date = datetime.date(2026, 1, 1)
    today = datetime.date(2026, 2, 15)
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions",
                         lambda model_name=None: _pending_frame())
    monkeypatch.setattr(sa_data, "fetch_price_history",
                         lambda ticker, period="3mo": _history_spanning(made_date, today))
    monkeypatch.setattr(cr.db, "mature_model_predictions_batch", lambda updates: True)

    result = cr._mature_vol_predictions(datetime.datetime(2026, 2, 15))
    assert result["error"] is None
    assert result["candidates"] == 1
    assert result["saved"] == result["candidates"]


def test_mature_vol_predictions_db_failure_preserves_candidate_count(monkeypatch):
    made_date = datetime.date(2026, 1, 1)
    today = datetime.date(2026, 2, 15)
    monkeypatch.setattr(cr.db, "load_unmatured_model_predictions",
                         lambda model_name=None: _pending_frame())
    monkeypatch.setattr(sa_data, "fetch_price_history",
                         lambda ticker, period="3mo": _history_spanning(made_date, today))
    monkeypatch.setattr(cr.db, "mature_model_predictions_batch", lambda updates: False)

    result = cr._mature_vol_predictions(datetime.datetime(2026, 2, 15))
    assert result["candidates"] == 1, "the maturation update was computed, not skipped"
    assert result["saved"] == 0
    assert result["error"] is not None, "a real write failure must not look like a no-op"
