"""2026-10-11 audit D-M2: the premarket, scan and thesis lanes logged a
failed write but always returned 0, so the heartbeat stayed green while
exit_signals / score_history / analyst targets / scanner_cache / the
recommendations log / the gate ledger / a paid thesis review were lost.
Same contract the EOD lane got on 2026-09-21: a write that SHOULD have
succeeded and didn't flips rc to 1, sets _LAST_LANE_FAILURE_DETAIL and
sends the failure email. A legitimately empty write is never a failure.

Also pins the premarket half of the review follow-up (owner decision
2026-10-12): a total PRICE outage means no stop was checked, so the lane
fails and does NOT save dedup state as if it were a clean empty run.
"""
import datetime

import pandas as pd
import pytest

import cron_runner as cr

NOW = datetime.datetime(2026, 10, 13, 8, 30)


def _spy_notify(monkeypatch):
    calls = []
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, detail: calls.append((lane, detail)))
    return calls


# ── premarket ────────────────────────────────────────────────────────────────

def _pm_payload(**overrides):
    payload = {
        "alerts": [], "built_at": "2026-10-13T08:00:00", "errors": [],
        "all_deterioration_signals": [
            {"ticker": "AAPL", "tier": "WATCH", "composite_score": 60, "price": 200.0,
             "dd_from_peak_pct": -8.0, "pnl_pct": 5.0, "below_ma_count": 2,
             "rel_strength": 0.9},
        ],
        "risk_off_signals": [],
        "analyst_target_snapshots": [{"ticker": "AAPL", "snapshot_date": "2026-10-13"}],
        "score_history": [{"ticker": "AAPL", "score_date": "2026-10-13"}],
    }
    payload.update(overrides)
    return payload


def _run_pm(monkeypatch, payload, *, exit_ok=True, targets_ok=True, history_ok=True):
    monkeypatch.setattr(cr, "compute_protective_alerts", lambda **_k: payload)
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda **_k: None)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: {})
    saved_state = []
    monkeypatch.setattr(cr.db, "save_alert_state",
                        lambda *a, **_k: saved_state.append(a) or True)
    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_exit_signals_batch", lambda *_a, **_k: exit_ok)
    monkeypatch.setattr(cr.db, "save_analyst_target_snapshots_batch", lambda *_a, **_k: targets_ok)
    monkeypatch.setattr(cr.db, "save_score_history_batch", lambda *_a, **_k: history_ok)
    notified = _spy_notify(monkeypatch)
    rc = cr._run_premarket(NOW, force=True)
    return rc, notified, saved_state


def test_premarket_clean_run_returns_zero(monkeypatch):
    rc, notified, saved_state = _run_pm(monkeypatch, _pm_payload())
    assert rc == 0
    assert cr._LAST_LANE_FAILURE_DETAIL is None
    assert notified == []
    assert saved_state, "a clean empty run must still save dedup state"


@pytest.mark.parametrize("failing, key", [
    ("exit_ok", "exit_signals"),
    ("targets_ok", "analyst_target_snapshots"),
    ("history_ok", "score_history"),
])
def test_premarket_write_failure_fails_the_lane(monkeypatch, failing, key):
    rc, notified, _ = _run_pm(monkeypatch, _pm_payload(), **{failing: False})
    assert rc == 1
    assert key in cr._LAST_LANE_FAILURE_DETAIL
    assert notified == [("premarket", cr._LAST_LANE_FAILURE_DETAIL)]


def test_premarket_nothing_to_write_is_not_a_failure(monkeypatch):
    rc, notified, _ = _run_pm(
        monkeypatch,
        _pm_payload(all_deterioration_signals=[], analyst_target_snapshots=[], score_history=[]),
        exit_ok=False, targets_ok=False, history_ok=False,   # never called
    )
    assert rc == 0
    assert notified == []


@pytest.mark.parametrize("reason", ["no_bundles", "empty_port_df"])
def test_premarket_price_outage_fails_and_does_not_save_dedup_state(monkeypatch, capsys, reason):
    rc, notified, saved_state = _run_pm(monkeypatch, {
        "alerts": [], "built_at": "x", "errors": ["no holdings could be loaded"],
        "reason": reason,
    })
    out = capsys.readouterr().out
    assert rc == 1
    assert f"price_outage: {reason}" in cr._LAST_LANE_FAILURE_DETAIL
    assert "NOT checked" in cr._LAST_LANE_FAILURE_DETAIL
    assert len(notified) == 1
    assert saved_state == [], "an outage must not be recorded as a legitimately empty run"
    assert "nothing to act on" not in out


def test_premarket_empty_book_is_still_a_quiet_green_day(monkeypatch):
    """`no_holdings` (an all-cash account) is real, not an outage."""
    rc, notified, saved_state = _run_pm(monkeypatch, {
        "alerts": [], "built_at": "x", "errors": ["no holdings"], "reason": "no_holdings",
    })
    assert rc == 0
    assert notified == []
    assert saved_state


# ── scan ─────────────────────────────────────────────────────────────────────

def _run_scan(monkeypatch, *, cache_ok=True, rec_result=None, rec_raises=False,
              gl_result=None, gl_raises=False):
    from datetime import date as _date
    from stock_analyzer import reference_data as rd
    from stock_analyzer import scanner as real_scanner
    from stock_analyzer import gate_ledger

    monkeypatch.setattr(cr, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(rd, "resolve_universe_or_none",
                        lambda name: ({"Tech": ["AAPL"]}, _date(2026, 9, 1), None))
    monkeypatch.setattr(real_scanner, "scan_sectors",
                        lambda *a, **k: pd.DataFrame({"Ticker": ["AAPL"], "Score": [70.0]}))
    monkeypatch.setattr(cr, "_build_context", lambda _d: {"ok": True, "errors": []})
    monkeypatch.setattr(cr.db, "save_scanner_cache", lambda *_a, **_k: cache_ok)
    monkeypatch.setattr(cr, "compute_morning_picks",
                        lambda **_k: {"picks": [], "errors": [], "built_at": "x",
                                      "diag": {}, "book_drift": None,
                                      "macro_coverage": None, "grow": {}})
    monkeypatch.setattr(cr, "_build_new_pick_rows", lambda *_a, **_k: [{"ticker": "AAPL"}])

    def _save_recs(_rows):
        if rec_raises:
            raise RuntimeError("rec boom")
        return rec_result or {"attempted": 1, "saved": 1, "error": None}
    monkeypatch.setattr(cr.db, "save_recommendations", _save_recs)

    monkeypatch.setattr(gate_ledger, "build_suppression_rows", lambda *_a, **_k: [{"gate_id": "G-07"}])

    def _save_gl(_rows):
        if gl_raises:
            raise RuntimeError("gl boom")
        return gl_result or {"attempted": 1, "saved": 1, "error": None}
    monkeypatch.setattr(cr.db, "save_gate_suppressions", _save_gl)

    monkeypatch.setattr(cr.db, "load_exit_signals", lambda **_k: pd.DataFrame())
    monkeypatch.setattr(cr, "compute_watchlist_entries",
                        lambda **_k: {"entries": [], "built_at": "x", "errors": [],
                                      "reason": None, "gate_degraded": False})
    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
    notified = _spy_notify(monkeypatch)
    rc = cr._run_scan(cr.datetime.now(cr._ET), force=True)
    return rc, notified


def test_scan_clean_run_returns_zero(monkeypatch):
    rc, notified = _run_scan(monkeypatch)
    assert rc == 0
    assert notified == []


def test_scan_short_saved_count_without_error_is_not_a_failure(monkeypatch):
    """Both upserts ignore duplicates by design; saved < attempted is normal."""
    rc, _ = _run_scan(monkeypatch,
                      rec_result={"attempted": 3, "saved": 1, "error": None},
                      gl_result={"attempted": 3, "saved": 0, "error": None})
    assert rc == 0


@pytest.mark.parametrize("kwargs, key", [
    ({"cache_ok": False}, "scanner_cache"),
    ({"rec_result": {"attempted": 1, "saved": 0, "error": "offline"}}, "recommendations"),
    ({"rec_raises": True}, "recommendations"),
    ({"gl_result": {"attempted": 1, "saved": 0, "error": "offline"}}, "gate_suppressions"),
    ({"gl_raises": True}, "gate_suppressions"),
])
def test_scan_write_failure_fails_the_lane(monkeypatch, kwargs, key):
    rc, notified = _run_scan(monkeypatch, **kwargs)
    assert rc == 1
    assert key in cr._LAST_LANE_FAILURE_DETAIL
    assert notified == [("scan", cr._LAST_LANE_FAILURE_DETAIL)]


# ── thesis ───────────────────────────────────────────────────────────────────

def _run_thesis(monkeypatch, *, save_ok):
    import stock_analyzer.headless_alert_engine as hae
    from stock_analyzer import thesis_advisor as ta

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(hae, "_build_context", lambda today: {
        "ok": True, "errors": [],
        "held_data": {"AAPL": {"history": pd.DataFrame(), "info": {}, "headlines": []}},
    })
    monkeypatch.setattr(cr.db, "load_trades", lambda: pd.DataFrame([{
        "ticker": "AAPL", "action": "BUY", "user_thesis": "thesis",
        "traded_at": "2026-09-01T10:00:00+00:00",
    }]))
    monkeypatch.setattr(cr.db, "load_thesis_reviews",
                        lambda: pd.DataFrame(columns=["ticker", "reviewed_at"]))
    monkeypatch.setattr(cr.db, "load_analyst_coverage", lambda **_kw: pd.DataFrame())
    monkeypatch.setattr(ta, "run_batch_review",
                        lambda positions, **_kw: [{"ticker": "AAPL", "status": "intact"}])
    monkeypatch.setattr(cr.db, "save_thesis_review", lambda rec: save_ok)
    notified = _spy_notify(monkeypatch)
    now_et = cr.datetime.now(cr._ET).replace(hour=19, minute=0, second=0, microsecond=0)
    return cr._run_thesis(now_et, force=True), notified


def test_thesis_saved_review_returns_zero(monkeypatch):
    rc, notified = _run_thesis(monkeypatch, save_ok=True)
    assert rc == 0
    assert notified == []


def test_thesis_lost_paid_review_fails_and_emails_itself(monkeypatch):
    """main()'s thesis wrapper treats a sub-job's 1 as an outage that already
    emailed, so _run_thesis must send the failure email itself."""
    rc, notified = _run_thesis(monkeypatch, save_ok=False)
    assert rc == 1
    assert "1 of 1" in cr._LAST_LANE_FAILURE_DETAIL
    assert notified == [("thesis", cr._LAST_LANE_FAILURE_DETAIL)]


# ── Review follow-ups (D-M2 review, 2026-10-12) ──────────────────────────────

def test_each_lane_resets_a_stale_failure_detail(monkeypatch):
    """A previous run's detail must never be read as this run's (NB-7a)."""
    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", "stale from an earlier run")
    assert _run_pm(monkeypatch, _pm_payload())[0] == 0
    assert cr._LAST_LANE_FAILURE_DETAIL is None

    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", "stale from an earlier run")
    assert _run_scan(monkeypatch)[0] == 0
    assert cr._LAST_LANE_FAILURE_DETAIL is None

    monkeypatch.setattr(cr, "_LAST_LANE_FAILURE_DETAIL", "stale from an earlier run")
    assert _run_thesis(monkeypatch, save_ok=True)[0] == 0
    assert cr._LAST_LANE_FAILURE_DETAIL is None


def test_scan_gate_ledger_compat_note_is_not_a_failure(monkeypatch):
    """db.save_gate_suppressions' compat path writes every row and reports a
    `compat: ...` note in `error` -- a success (NB-3)."""
    rc, notified = _run_scan(monkeypatch, gl_result={
        "attempted": 1, "saved": 1,
        "error": "compat: ignore_duplicates unsupported (last-writer-wins)"})
    assert rc == 0
    assert notified == []


def test_scan_mid_lane_db_outage_keeps_an_earlier_write_failure_visible(monkeypatch):
    """A scanner_cache failure followed by a DB outage used to be reported as
    the outage alone (NB-2)."""
    monkeypatch.setattr(cr, "_handle_db_unavailable",
                        lambda lane, now_et, detail: (
                            setattr(cr, "_LAST_LANE_FAILURE_DETAIL", f"db_unavailable: {detail}")
                            or 1))
    from datetime import date as _date
    from stock_analyzer import reference_data as rd
    from stock_analyzer import scanner as real_scanner
    monkeypatch.setattr(cr, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(rd, "resolve_universe_or_none",
                        lambda name: ({"Tech": ["AAPL"]}, _date(2026, 9, 1), None))
    monkeypatch.setattr(real_scanner, "scan_sectors",
                        lambda *a, **k: pd.DataFrame({"Ticker": ["AAPL"], "Score": [70.0]}))
    monkeypatch.setattr(cr, "_build_context", lambda _d: {"ok": False, "errors": []})
    monkeypatch.setattr(cr.db, "save_scanner_cache", lambda *_a, **_k: False)
    monkeypatch.setattr(cr, "compute_morning_picks",
                        lambda **_k: {"picks": [], "errors": ["holdings unreadable"],
                                      "reason": "db_unavailable"})
    rc = cr._run_scan(cr.datetime.now(cr._ET), force=True)
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.startswith("db_unavailable")
    assert "scanner_cache" in cr._LAST_LANE_FAILURE_DETAIL


def test_main_thesis_mode_lost_review_reds_the_heartbeat_with_one_email(monkeypatch):
    """main()'s thesis wrapper treats a sub-job's 1 as an outage that already
    emailed. Pin the end-to-end result: heartbeat 'failed' carrying the
    thesis detail, and exactly ONE failure email -- the one _run_thesis sent
    (NB-7b)."""
    heartbeats = []
    monkeypatch.setattr(cr, "_record_heartbeat",
                        lambda lane, _n, status="ok", detail=None:
                        heartbeats.append((lane, status, detail)))
    monkeypatch.setattr(cr, "_run_debrief", lambda *_a, **_k: 0)
    monkeypatch.setattr(cr, "_run_monthly_report", lambda *_a, **_k: 0)
    monkeypatch.setenv("ALERT_RUN_MODE", "thesis")
    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)
    _, _ = _run_thesis(monkeypatch, save_ok=False)          # installs the stubs
    notified = _spy_notify(monkeypatch)                     # fresh spy for main()
    rc = cr.main()
    assert rc == 1
    assert len(heartbeats) == 1
    lane, status, detail = heartbeats[0]
    assert (lane, status) == ("thesis", "failed")
    assert "thesis_reviews" in detail
    assert len(notified) == 1 and notified[0][0] == "thesis"
