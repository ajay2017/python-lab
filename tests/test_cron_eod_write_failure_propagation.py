"""Regression guard for the 2026-09-21 app-review's Defect #2: `_run_eod`
used to return 0 unconditionally (unless the ENTIRE run was blocked by a
full DB outage), so a heartbeat of "ok" could coexist with any of its
several independent writes (account_daily_snapshot, portfolio_risk_
snapshot, rec_events, sentiment_snapshot, daily_regime, model_predictions)
having silently failed. `_run_eod` now accumulates real failures into a
`failures` list (mirroring `_run_maintenance`'s own established pattern)
and returns 1 -- with `_LAST_LANE_FAILURE_DETAIL` set -- when any genuine
failure occurred, while still returning 0 on a day where every step
legitimately had nothing to write (0 qualifying rec_events rows, no held
tickers for sentiment, etc.) — those must NEVER be misread as a failure.
"""
import datetime

import cron_runner as cr

NOW = datetime.datetime(2026, 9, 21, 17, 30)


def _payload(**overrides):
    payload = {
        "snapshot_rows": [],
        "port_df": None,
        "port_risk": None,
        "held_data": {},
        "pullback": None,
        "built_at": "2026-09-21T17:30:00",
        "errors": [],
    }
    payload.update(overrides)
    return payload


def _patch_defaults(monkeypatch, **overrides):
    """Every dependency defaults to a clean success/no-op so a test only
    needs to override the one thing it's exercising."""
    defaults = {
        "compute_eod": lambda **_k: _payload(),
        "compute_account_snapshot": lambda *_a, **_k: {"gross_book": 0.0, "leverage": None},
        "build_portfolio_risk_snapshot": lambda *_a, **_k: {"portfolio_beta": None, "top_sector": None},
        "build_rec_event_rows": lambda *_a, **_k: [],
        "_write_live_vol_predictions": lambda *_a, **_k: {"candidates": 0, "saved": 0, "error": None},
        "_mature_vol_predictions": lambda *_a, **_k: {"candidates": 0, "saved": 0, "error": None},
        "_write_live_earnings_predictions": lambda *_a, **_k: {
            "candidates": 0, "saved": 0, "error": None,
            "skip_unknown_timing": 0, "skip_survivorship": 0,
        },
        "_mature_earnings_predictions": lambda *_a, **_k: {
            "candidates": 0, "matured": 0, "withdrawn": 0, "error": None,
        },
        "_notify_failure": lambda *_a, **_k: None,
    }
    defaults.update(overrides)
    for name, fn in defaults.items():
        monkeypatch.setattr(cr, name, fn)
    monkeypatch.setattr(cr.db, "load_account_cash", lambda: None)
    monkeypatch.setattr(cr.db, "save_account_daily_snapshot", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_portfolio_risk_snapshot", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "load_trades", lambda: None)
    monkeypatch.setattr(cr.db, "save_rec_events", lambda *_a, **_k: {"saved": 0, "error": None})
    monkeypatch.setattr(cr.db, "save_daily_snapshot", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_sentiment_snapshot", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_daily_regime", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "stock_analyzer.reference_data.resolve_universe_or_none",
        lambda name: ({}, "ok", None),
    )
    monkeypatch.setattr(
        "stock_analyzer.macro_calendar.detect_macro_regime",
        lambda *_a, **_k: {"regime": "neutral"},
    )


def _run(monkeypatch, **overrides):
    _patch_defaults(monkeypatch, **overrides)
    return cr._run_eod(NOW, force=True)


# ── Clean run ────────────────────────────────────────────────────────────────

def test_all_writes_succeed_returns_zero(monkeypatch):
    assert _run(monkeypatch) == 0
    assert cr._LAST_LANE_FAILURE_DETAIL is None


def test_everything_legitimately_empty_still_returns_zero(monkeypatch):
    """A day with no holdings, no qualifying recs, no held tickers for
    sentiment -- every step has nothing to do, none of that is a failure."""
    rc = _run(monkeypatch, compute_eod=lambda **_k: _payload(
        snapshot_rows=[], held_data={}, port_df=None,
    ))
    assert rc == 0
    assert cr._LAST_LANE_FAILURE_DETAIL is None


# ── daily_snapshot ───────────────────────────────────────────────────────────

def test_daily_snapshot_write_failure_sets_rc(monkeypatch):
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _payload(
        snapshot_rows=[{"ticker": "AAPL", "shares": 10, "close_price": 200.0}],
    ))
    monkeypatch.setattr(cr.db, "save_daily_snapshot", lambda *_a, **_k: False)
    rc = cr._run_eod(NOW, force=True)
    assert rc == 1
    assert "daily_snapshot" in cr._LAST_LANE_FAILURE_DETAIL


def test_daily_snapshot_no_positions_is_not_a_failure(monkeypatch):
    """rows=[] (no holdings) must never be conflated with a write failure --
    save_daily_snapshot is never even called in that case."""
    called = []
    _patch_defaults(monkeypatch)
    monkeypatch.setattr(cr.db, "save_daily_snapshot", lambda *_a, **_k: called.append(1) or False)
    rc = cr._run_eod(NOW, force=True)
    assert rc == 0
    assert called == []


# ── account_daily_snapshot ───────────────────────────────────────────────────

def test_account_daily_snapshot_write_failure_sets_rc(monkeypatch):
    _patch_defaults(monkeypatch)
    monkeypatch.setattr(cr.db, "save_account_daily_snapshot", lambda *_a, **_k: False)
    rc = cr._run_eod(NOW, force=True)
    assert rc == 1
    assert "account_daily_snapshot" in cr._LAST_LANE_FAILURE_DETAIL


def test_account_daily_snapshot_exception_sets_rc(monkeypatch):
    _patch_defaults(monkeypatch, compute_account_snapshot=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    rc = cr._run_eod(NOW, force=True)
    assert rc == 1
    assert "account_daily_snapshot" in cr._LAST_LANE_FAILURE_DETAIL


# ── portfolio_risk_snapshot ──────────────────────────────────────────────────

def test_portfolio_risk_snapshot_write_failure_sets_rc(monkeypatch):
    _patch_defaults(monkeypatch)
    monkeypatch.setattr(cr.db, "save_portfolio_risk_snapshot", lambda *_a, **_k: False)
    rc = cr._run_eod(NOW, force=True)
    assert rc == 1
    assert "portfolio_risk_snapshot" in cr._LAST_LANE_FAILURE_DETAIL


# ── rec_events ───────────────────────────────────────────────────────────────

def test_rec_events_zero_qualifying_rows_is_not_a_failure(monkeypatch):
    rc = _run(monkeypatch, build_rec_event_rows=lambda *_a, **_k: [])
    assert rc == 0


def test_rec_events_write_failure_when_rows_present_sets_rc(monkeypatch):
    _patch_defaults(monkeypatch, build_rec_event_rows=lambda *_a, **_k: [{"ticker": "AAPL"}])
    monkeypatch.setattr(cr.db, "save_rec_events", lambda *_a, **_k: {"saved": 0, "error": "offline"})
    rc = cr._run_eod(NOW, force=True)
    assert rc == 1
    assert "rec_events" in cr._LAST_LANE_FAILURE_DETAIL


def test_rec_events_generator_exception_sets_rc(monkeypatch):
    rc = _run(monkeypatch, build_rec_event_rows=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert rc == 1
    assert "rec_events" in cr._LAST_LANE_FAILURE_DETAIL


# ── sentiment_snapshot ───────────────────────────────────────────────────────

def test_sentiment_snapshot_no_held_data_is_not_a_failure(monkeypatch):
    rc = _run(monkeypatch, compute_eod=lambda **_k: _payload(held_data={}))
    assert rc == 0


def test_sentiment_snapshot_only_malformed_bundles_is_not_a_failure(monkeypatch):
    """held_data is non-empty but every bundle is malformed (non-dict) --
    _snap_sentiment_rows ends up empty, which is legitimate, not a DB
    failure (save_sentiment_snapshot itself returns False for both cases,
    so this must be checked BEFORE calling it)."""
    called = []
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _payload(held_data={"AAPL": "not-a-dict"}))
    monkeypatch.setattr(cr.db, "save_sentiment_snapshot", lambda *_a, **_k: called.append(1) or False)
    rc = cr._run_eod(NOW, force=True)
    assert rc == 0
    assert called == []


def test_sentiment_snapshot_write_failure_when_rows_present_sets_rc(monkeypatch):
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _payload(
        held_data={"AAPL": {"avg_sent": 0.1, "s_score": 0.2, "headlines": []}},
    ))
    monkeypatch.setattr(cr.db, "save_sentiment_snapshot", lambda *_a, **_k: False)
    rc = cr._run_eod(NOW, force=True)
    assert rc == 1
    assert "sentiment_snapshot" in cr._LAST_LANE_FAILURE_DETAIL


def test_sentiment_snapshot_no_usable_reading_is_not_a_failure(monkeypatch):
    """2026-09-21 reviewer finding on this same fix: a bundle IS present and
    dict-shaped, but carries no VADER reading (avg_sent=None) AND Finnhub
    has nothing either (bullish_pct=None) -- db.save_sentiment_snapshot()
    would itself drop this row internally (db.py:1682-1684) and return
    False, indistinguishable from a real DB failure by return value alone.
    Must be filtered out BEFORE calling save, or a low-news day + a Finnhub
    outage would over-alert a healthy DB as "failed" -- same false-positive
    direction as the malformed-bundle case above, different trigger."""
    called = []
    monkeypatch.setattr(
        "stock_analyzer.news_sentiment.fetch_sentiment_for_tickers",
        lambda tickers: {t: {} for t in tickers},
    )
    _patch_defaults(monkeypatch, compute_eod=lambda **_k: _payload(
        held_data={"AAPL": {"avg_sent": None, "s_score": None, "headlines": []}},
    ))
    monkeypatch.setattr(cr.db, "save_sentiment_snapshot", lambda *_a, **_k: called.append(1) or False)
    rc = cr._run_eod(NOW, force=True)
    assert rc == 0
    assert called == []


# ── daily_regime ─────────────────────────────────────────────────────────────

def test_daily_regime_write_failure_sets_rc(monkeypatch):
    _patch_defaults(monkeypatch)
    monkeypatch.setattr(cr.db, "save_daily_regime", lambda *_a, **_k: False)
    rc = cr._run_eod(NOW, force=True)
    assert rc == 1
    assert "daily_regime" in cr._LAST_LANE_FAILURE_DETAIL


def test_daily_regime_detection_exception_sets_rc(monkeypatch):
    _patch_defaults(monkeypatch)
    monkeypatch.setattr(
        "stock_analyzer.macro_calendar.detect_macro_regime",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("FRED down")),
    )
    rc = cr._run_eod(NOW, force=True)
    assert rc == 1
    assert "daily_regime" in cr._LAST_LANE_FAILURE_DETAIL


# ── model_predictions (4 independent sub-steps) ─────────────────────────────

def test_model_predictions_zero_candidates_is_not_a_failure(monkeypatch):
    rc = _run(monkeypatch)
    assert rc == 0


def test_model_predictions_live_vol_error_sets_rc(monkeypatch):
    rc = _run(monkeypatch, _write_live_vol_predictions=lambda *_a, **_k: {
        "candidates": 3, "saved": 0, "error": "db offline",
    })
    assert rc == 1
    assert "model_predictions (live)" in cr._LAST_LANE_FAILURE_DETAIL


def test_model_predictions_maturation_vol_error_sets_rc(monkeypatch):
    rc = _run(monkeypatch, _mature_vol_predictions=lambda *_a, **_k: {
        "candidates": 2, "saved": 0, "error": "db offline",
    })
    assert rc == 1
    assert "model_predictions (maturation)" in cr._LAST_LANE_FAILURE_DETAIL


def test_model_predictions_earnings_live_error_sets_rc(monkeypatch):
    rc = _run(monkeypatch, _write_live_earnings_predictions=lambda *_a, **_k: {
        "candidates": 1, "saved": 0, "error": "db offline",
        "skip_unknown_timing": 0, "skip_survivorship": 0,
    })
    assert rc == 1
    assert "model_predictions (earnings, live)" in cr._LAST_LANE_FAILURE_DETAIL


def test_model_predictions_earnings_maturation_error_sets_rc(monkeypatch):
    rc = _run(monkeypatch, _mature_earnings_predictions=lambda *_a, **_k: {
        "candidates": 1, "matured": 0, "withdrawn": 0, "error": "db offline",
    })
    assert rc == 1
    assert "model_predictions (earnings, maturation)" in cr._LAST_LANE_FAILURE_DETAIL


def test_model_predictions_exception_sets_rc(monkeypatch):
    rc = _run(monkeypatch, _write_live_vol_predictions=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert rc == 1
    assert "model_predictions (live)" in cr._LAST_LANE_FAILURE_DETAIL


# ── Aggregation / notification ──────────────────────────────────────────────

def test_multiple_failures_all_recorded_in_detail(monkeypatch):
    _patch_defaults(monkeypatch)
    monkeypatch.setattr(cr.db, "save_account_daily_snapshot", lambda *_a, **_k: False)
    monkeypatch.setattr(cr.db, "save_daily_regime", lambda *_a, **_k: False)
    rc = cr._run_eod(NOW, force=True)
    assert rc == 1
    assert "account_daily_snapshot" in cr._LAST_LANE_FAILURE_DETAIL
    assert "daily_regime" in cr._LAST_LANE_FAILURE_DETAIL


def test_notify_failure_called_once_on_failure_never_on_success(monkeypatch):
    calls = []
    _patch_defaults(monkeypatch, _notify_failure=lambda *a, **k: calls.append(a))
    monkeypatch.setattr(cr.db, "save_daily_regime", lambda *_a, **_k: False)
    rc = cr._run_eod(NOW, force=True)
    assert rc == 1
    assert len(calls) == 1
    assert calls[0][0] == "eod"

    calls.clear()
    cr._LAST_LANE_FAILURE_DETAIL = None
    rc2 = _run(monkeypatch, _notify_failure=lambda *a, **k: calls.append(a))
    assert rc2 == 0
    assert calls == []
