"""Regression guard: the premarket lane must not log "captured" for an
exit_signals / analyst_target_snapshots write that actually failed.

2026-08-30 data-integrity audit follow-up: `db.save_exit_signals_batch()` /
`db.save_analyst_target_snapshots_batch()` used to return None unconditionally,
and `_run_premarket` logged "captured" right after calling them regardless of
whether the upsert itself succeeded or silently failed via warnings.warn().
Both functions now return bool; this file locks that `_run_premarket`'s log
line actually branches on it, mirroring the model_predictions maturation fix
from the same audit.
"""
import datetime

import cron_runner as cr


def _base_payload(**overrides):
    payload = {
        "alerts": [],
        "built_at": "2026-08-30T08:00:00",
        "errors": [],
        "all_deterioration_signals": [
            {"ticker": "AAPL", "tier": "WATCH", "composite_score": 60,
             "price": 200.0, "dd_from_peak_pct": -8.0, "pnl_pct": 5.0,
             "below_ma_count": 2, "rel_strength": 0.9},
        ],
        "risk_off_signals": [],
        "analyst_target_snapshots": [
            {"ticker": "AAPL", "snapshot_date": "2026-08-30", "target_mean": 220.0},
        ],
    }
    payload.update(overrides)
    return payload


def _run(monkeypatch, *, exit_signals_ok, analyst_snapshots_ok):
    monkeypatch.setattr(cr, "compute_protective_alerts", lambda **_k: _base_payload())
    # Starve the velocity check of history so it takes its "insufficient
    # history — skip" branch rather than needing a real DataFrame.
    monkeypatch.setattr(cr.db, "load_exit_signals", lambda **_k: None)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: {})
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_exit_signals_batch", lambda *_a, **_k: exit_signals_ok)
    monkeypatch.setattr(cr.db, "save_analyst_target_snapshots_batch",
                         lambda *_a, **_k: analyst_snapshots_ok)
    return cr._run_premarket(datetime.datetime(2026, 8, 30, 8, 30), force=True)


def test_exit_signals_success_logs_captured(monkeypatch, capsys):
    _run(monkeypatch, exit_signals_ok=True, analyst_snapshots_ok=True)
    out = capsys.readouterr().out
    assert "exit_signals captured (1 rows" in out
    assert "exit_signals: WRITE FAILED" not in out


def test_exit_signals_failure_logs_write_failed_not_captured(monkeypatch, capsys):
    _run(monkeypatch, exit_signals_ok=False, analyst_snapshots_ok=True)
    out = capsys.readouterr().out
    assert "exit_signals: WRITE FAILED for 1 row(s)" in out
    assert "exit_signals captured" not in out


def test_analyst_target_snapshots_success_logs_captured(monkeypatch, capsys):
    _run(monkeypatch, exit_signals_ok=True, analyst_snapshots_ok=True)
    out = capsys.readouterr().out
    assert "analyst_target_snapshots captured (1 rows" in out
    assert "analyst_target_snapshots: WRITE FAILED" not in out


def test_analyst_target_snapshots_failure_logs_write_failed_not_captured(monkeypatch, capsys):
    _run(monkeypatch, exit_signals_ok=True, analyst_snapshots_ok=False)
    out = capsys.readouterr().out
    assert "analyst_target_snapshots: WRITE FAILED for 1 row(s)" in out
    assert "analyst_target_snapshots captured" not in out


def test_both_can_fail_independently_in_the_same_run(monkeypatch, capsys):
    _run(monkeypatch, exit_signals_ok=False, analyst_snapshots_ok=False)
    out = capsys.readouterr().out
    assert "exit_signals: WRITE FAILED for 1 row(s)" in out
    assert "analyst_target_snapshots: WRITE FAILED for 1 row(s)" in out
