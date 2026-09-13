"""Regression guard: the premarket lane must not log "captured" for a
score_history write that actually failed -- same log-collapses-failure-into-
success shape the 2026-08-30 audit fixed for exit_signals /
analyst_target_snapshots (see test_cron_premarket_write_failure_logging.py,
which this file mirrors for the new roadmap-B1 capture).
"""
import datetime

import cron_runner as cr


def _base_payload(**overrides):
    payload = {
        "alerts": [],
        "built_at": "2026-09-13T08:00:00",
        "errors": [],
        "all_deterioration_signals": [],
        "risk_off_signals": [],
        "analyst_target_snapshots": [],
        "score_history": [
            {"ticker": "AAPL", "score_date": "2026-09-13", "composite": 62.0,
             "t_score": 55.0, "bq_score": 70.0, "val_score": 65.0, "s_score": 60.0,
             "bq_available": True, "val_available": True, "price": 210.0,
             "source": "cron"},
        ],
    }
    payload.update(overrides)
    return payload


def _run(monkeypatch, *, score_history_ok):
    monkeypatch.setattr(cr, "compute_protective_alerts", lambda **_k: _base_payload())
    monkeypatch.setattr(cr.db, "load_exit_signals", lambda **_k: None)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: {})
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_exit_signals_batch", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_analyst_target_snapshots_batch", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_score_history_batch", lambda *_a, **_k: score_history_ok)
    return cr._run_premarket(datetime.datetime(2026, 9, 13, 8, 30), force=True)


def test_score_history_success_logs_captured(monkeypatch, capsys):
    _run(monkeypatch, score_history_ok=True)
    out = capsys.readouterr().out
    assert "score_history captured (1 rows" in out
    assert "score_history: WRITE FAILED" not in out


def test_score_history_failure_logs_write_failed_not_captured(monkeypatch, capsys):
    _run(monkeypatch, score_history_ok=False)
    out = capsys.readouterr().out
    assert "score_history: WRITE FAILED for 1 row(s)" in out
    assert "score_history captured" not in out


def test_no_rows_writes_nothing_and_logs_neither(monkeypatch, capsys):
    monkeypatch.setattr(cr, "compute_protective_alerts",
                         lambda **_k: _base_payload(score_history=[]))
    monkeypatch.setattr(cr.db, "load_exit_signals", lambda **_k: None)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: {})
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_exit_signals_batch", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_analyst_target_snapshots_batch", lambda *_a, **_k: True)
    calls = []
    monkeypatch.setattr(cr.db, "save_score_history_batch", lambda *_a, **_k: calls.append(1) or True)
    cr._run_premarket(datetime.datetime(2026, 9, 13, 8, 30), force=True)
    assert calls == []
    out = capsys.readouterr().out
    assert "score_history captured" not in out
    assert "score_history: WRITE FAILED" not in out
