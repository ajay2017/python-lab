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


# ── held_tickers pass-through / correlation-claim-verification follow-on
# (2026-10-09, "screens disclose, records withhold") ────────────────────────

def test_h8_cron_forwards_held_tickers_not_held_data_keys(monkeypatch):
    """H8 — the cron must pass held_tickers=payload["held_tickers"] straight
    through to build_portfolio_risk_snapshot, never held_data's own keys.
    held_data deliberately lacks "BAD" (its bundle failed upstream) while
    held_tickers names both — the two genuinely differ here, otherwise this
    test would pass even if the cron were wired to the wrong source."""
    captured = {}

    def _capture(snapshot_date, port_df, port_risk, held_data,
                 held_tickers=None, diagnostics=None):
        captured["held_tickers"] = held_tickers
        captured["held_data_keys"] = list((held_data or {}).keys())
        if isinstance(diagnostics, dict):
            diagnostics.update(corr_unchecked=None, corr_n_obs=None, corr_withheld=False)
        return {"portfolio_beta": None, "top_sector": None}

    _patch_defaults(
        monkeypatch,
        compute_eod=lambda **_k: _payload(
            held_data={"AAPL": {}}, held_tickers=["AAPL", "BAD"],
        ),
        build_portfolio_risk_snapshot=_capture,
    )
    rc = cr._run_eod(NOW, force=True)
    assert rc == 0
    assert captured["held_tickers"] == ["AAPL", "BAD"]
    assert captured["held_data_keys"] == ["AAPL"]
    assert captured["held_tickers"] != captured["held_data_keys"]


def test_h9_withheld_correlation_is_not_a_failure_but_is_logged(monkeypatch, capsys):
    """H9 — a day whose correlation reading is withheld as unverified must
    still return rc=0, must NOT add "portfolio_risk_snapshot" to the
    failures list (a legitimately conditional outcome is not a failure, per
    that lane's own established convention), and must log exactly why."""
    def _withheld_build(snapshot_date, port_df, port_risk, held_data,
                         held_tickers=None, diagnostics=None):
        if isinstance(diagnostics, dict):
            diagnostics.update(
                corr_unchecked=["BAD"], corr_n_obs=0, corr_withheld=True,
            )
        return {"portfolio_beta": 1.1, "top_sector": "Tech"}

    _patch_defaults(
        monkeypatch,
        compute_eod=lambda **_k: _payload(held_tickers=["AAPL", "BAD"]),
        build_portfolio_risk_snapshot=_withheld_build,
    )
    rc = cr._run_eod(NOW, force=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert cr._LAST_LANE_FAILURE_DETAIL is None
    assert "withheld" in out
    assert "BAD" in out


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


def test_h8b_none_held_tickers_is_forwarded_not_substituted(monkeypatch):
    """A None held_tickers must reach the builder AS None, never swapped for
    held_data's keys.

    Opus review 2026-10-09: an `or list(held_data)` fallback in the cron
    SURVIVED every other test, because in all currently-reachable states the
    two behave identically -- on the ok path held_tickers is always a
    non-empty list, and on not-ok paths held_data is absent too. So the
    comment in cron_runner claiming the missing `or` protects something was
    asserting an untested property.

    This constructs the state that `or` would actually corrupt: None
    held_tickers alongside a NON-empty held_data. Under an `or` fallback the
    builder would receive ["AAPL"] and verify a reading whose expected set was
    never known -- i.e. silently re-introducing the circularity this whole
    commit exists to remove. Forwarding None instead makes
    correlation_unchecked return None and the reading is withheld, which is
    the honest answer.
    """
    captured = {}

    def _capture(snapshot_date, port_df, port_risk, held_data,
                 held_tickers=None, diagnostics=None):
        captured["held_tickers"] = held_tickers
        captured["held_data_keys"] = list((held_data or {}).keys())
        if isinstance(diagnostics, dict):
            diagnostics.update(corr_unchecked=None, corr_n_obs=None, corr_withheld=True)
        return {"portfolio_beta": 1.1, "top_sector": "Tech"}

    _patch_defaults(
        monkeypatch,
        compute_eod=lambda **_k: _payload(
            held_data={"AAPL": {}}, held_tickers=None,
        ),
        build_portfolio_risk_snapshot=_capture,
    )
    rc = cr._run_eod(NOW, force=True)
    assert rc == 0
    # The control: held_data really is non-empty, so an `or` fallback would
    # have had something to substitute. Without this, the assertion below
    # could pass simply because there was nothing to fall back to.
    assert captured["held_data_keys"] == ["AAPL"]
    assert captured["held_tickers"] is None, (
        "cron substituted a fallback for a None held_tickers -- that silently "
        "restores the held_data circularity this commit removes."
    )
