"""Regression guard: a split-withheld-only day must still send an email
(data-integrity finding D1).

`render_alert_email` requires at least one of alerts/velocity_alerts/
split_withheld to be non-empty to be called meaningfully, and
`_run_premarket`'s own "nothing to act on" gate used to check only
`alerts`/`velocity_alerts`. Before this fix, a run where the ONLY thing that
happened was a split-withheld deterioration signal would fall through that
gate, log "nothing to act on — no email", and the disclosure this finding
exists to guarantee would itself go unreported — the exact silent-filter
class this whole effort is about.
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
        "split_withheld": [],
    }
    payload.update(overrides)
    return payload


def _run(monkeypatch, payload, render_calls):
    monkeypatch.setattr(cr, "compute_protective_alerts", lambda **_k: payload)
    monkeypatch.setattr(cr.db, "load_exit_signals", lambda **_k: None)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: {})
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_exit_signals_batch", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_analyst_target_snapshots_batch", lambda *_a, **_k: True)

    _real_render = cr.render_alert_email

    def _spy_render(*a, **k):
        render_calls.append((a, k))
        return _real_render(*a, **k)

    monkeypatch.setattr(cr, "render_alert_email", _spy_render)
    return cr._run_premarket(datetime.datetime(2026, 9, 13, 8, 30), force=True)


def test_split_withheld_only_still_sends_an_email(monkeypatch, capsys):
    payload = _base_payload(split_withheld=["AAA"])
    calls = []
    _run(monkeypatch, payload, calls)
    out = capsys.readouterr().out
    assert "nothing to act on" not in out
    assert len(calls) == 1
    assert calls[0][1]["split_withheld"] == ["AAA"]


def test_split_withheld_only_logged_before_send(monkeypatch, capsys):
    payload = _base_payload(split_withheld=["AAA", "BBB"])
    _run(monkeypatch, payload, [])
    out = capsys.readouterr().out
    assert "split signal(s) withheld: AAA, BBB" in out


def test_genuinely_empty_run_still_says_nothing_to_act_on(monkeypatch, capsys):
    payload = _base_payload()  # alerts=[], split_withheld=[] -- truly quiet day
    calls = []
    _run(monkeypatch, payload, calls)
    out = capsys.readouterr().out
    assert "nothing to act on — no email" in out
    assert calls == []


def test_split_withheld_alone_does_not_prevent_dedup_state_save(monkeypatch, capsys):
    # A split-withheld-only send must still save dedup state on success --
    # otherwise the SAME withheld ticker re-sends every single lane run.
    payload = _base_payload(split_withheld=["AAA"])
    _run(monkeypatch, payload, [])
    out = capsys.readouterr().out
    assert "state saved" in out


def test_hard_alert_and_split_withheld_both_reach_render(monkeypatch):
    payload = _base_payload(
        alerts=[{"ticker": "MSFT", "kind": "stop_breach", "directive": "Sell now"}],
        split_withheld=["AAA"],
    )
    calls = []
    _run(monkeypatch, payload, calls)
    assert len(calls) == 1
    assert calls[0][1]["split_withheld"] == ["AAA"]
    assert calls[0][0][0][0]["ticker"] == "MSFT"
