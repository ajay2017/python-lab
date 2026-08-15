"""Tests for the `maintenance` cron lane's skip logic —
`scripts/backfill_vol_predictions.run_backfill(skip_existing=...)`.

The lane exists because the 2026-08-15 Railway cutover removed the only
practical shell for one-off maintenance scripts (see
docs/plans/railway-migration.md). Because it now runs on a schedule rather
than by hand, the expensive part (a 5y history fetch per held ticker) must be
skipped once done — but ONLY when the skip is actually proven safe.

The tri-state from `db.has_backfilled_predictions` is the whole point:
  True  -> already backfilled, skip (the saving)
  False -> confirmed absent, do the work
  None  -> UNKNOWN (DB offline / pre-DDL). Must do the work anyway: the
           backfill is idempotent, so a redundant run costs provider calls,
           whereas wrongly skipping leaves a permanent hole in the ledger.
"""
import scripts.backfill_vol_predictions as bvp


def _stub(monkeypatch, *, held, backfilled_state, rows_written=3):
    """Wire run_backfill's three collaborators and record which tickers
    actually reached the expensive _backfill_ticker path."""
    touched: list[str] = []

    monkeypatch.setattr(bvp, "_held_tickers", lambda: list(held))
    monkeypatch.setattr(
        bvp.db, "has_backfilled_predictions",
        lambda _m, _v, ticker: backfilled_state.get(ticker),
    )

    def _fake_backfill(ticker):
        touched.append(ticker)
        return rows_written, None

    monkeypatch.setattr(bvp, "_backfill_ticker", _fake_backfill)
    return touched


def test_skip_existing_false_backfills_everything(monkeypatch):
    """Manual CLI default: redo all, ignoring existing rows."""
    touched = _stub(monkeypatch, held=["AAPL", "MSFT"],
                    backfilled_state={"AAPL": True, "MSFT": True})
    out = bvp.run_backfill(skip_existing=False, log=lambda _m: None)
    assert touched == ["AAPL", "MSFT"]
    assert out["already_done"] == []
    assert out["rows"] == 6


def test_skip_existing_true_skips_already_done(monkeypatch):
    """The saving: a ticker with backfill rows is not re-fetched."""
    touched = _stub(monkeypatch, held=["AAPL", "MSFT"],
                    backfilled_state={"AAPL": True, "MSFT": False})
    out = bvp.run_backfill(skip_existing=True, log=lambda _m: None)
    assert touched == ["MSFT"]
    assert out["already_done"] == ["AAPL"]
    assert out["rows"] == 3


def test_unknown_backfill_state_does_the_work_anyway(monkeypatch):
    """None (offline sentinel) must NOT be collapsed into 'already done'."""
    touched = _stub(monkeypatch, held=["AAPL"], backfilled_state={"AAPL": None})
    out = bvp.run_backfill(skip_existing=True, log=lambda _m: None)
    assert touched == ["AAPL"], "an unknown backfill state must not skip the ticker"
    assert out["already_done"] == []


def test_newly_added_holding_gets_backfilled_on_next_tick(monkeypatch):
    """The lane's actual purpose: buy a new ticker, it backfills itself."""
    touched = _stub(monkeypatch, held=["AAPL", "MSFT", "NVDA"],
                    backfilled_state={"AAPL": True, "MSFT": True, "NVDA": False})
    out = bvp.run_backfill(skip_existing=True, log=lambda _m: None)
    assert touched == ["NVDA"]
    assert sorted(out["already_done"]) == ["AAPL", "MSFT"]


def test_no_holdings_is_a_clean_noop(monkeypatch):
    touched = _stub(monkeypatch, held=[], backfilled_state={})
    out = bvp.run_backfill(skip_existing=True, log=lambda _m: None)
    assert touched == []
    assert out == {"tickers": 0, "rows": 0, "skipped": [], "already_done": []}


def test_skipped_tickers_are_reported(monkeypatch):
    """A ticker whose history fetch fails is reported, not silently dropped."""
    monkeypatch.setattr(bvp, "_held_tickers", lambda: ["AAPL"])
    monkeypatch.setattr(bvp.db, "has_backfilled_predictions",
                        lambda _m, _v, _t: False)
    monkeypatch.setattr(bvp, "_backfill_ticker",
                        lambda _t: (0, "no price history returned"))
    out = bvp.run_backfill(skip_existing=True, log=lambda _m: None)
    assert out["skipped"] == ["AAPL"]
    assert out["rows"] == 0


# ── the maintenance lane's heartbeat status ───────────────────────────────────
# Regression guard for a real bug caught in the 2026-08-15 Opus review: the lane
# isolates its sub-job failures and signals them by RETURNING non-zero rather
# than raising, so main()'s except-branch never sees it. The success path used to
# record status="ok" unconditionally — meaning a Saturday where BOTH backfills
# blew up still showed a green "maintenance · ok" on 🩺 System Trust, defeating
# the very dead-man's-switch the lane was registered there to provide.

def _run_lane(monkeypatch, *, analyst_raises=False, vol_raises=False):
    """Drive cron_runner.main() in maintenance mode, capturing the heartbeat."""
    import cron_runner as cr

    recorded = {}
    monkeypatch.setenv("ALERT_RUN_MODE", "maintenance")
    monkeypatch.setenv("ALERT_FORCE", "1")          # bypass the Saturday guard
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)
    monkeypatch.setattr(cr, "_notify_failure", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        cr, "_record_heartbeat",
        lambda lane, _now, status="ok", detail=None: recorded.update(
            lane=lane, status=status, detail=detail),
    )

    def _mk(raises):
        def _fn(*_a, **_kw):
            if raises:
                raise RuntimeError("simulated backfill failure")
            return {"updated": 0, "skipped_count": 0, "pending": 0, "offline": False,
                    "rows": 0, "tickers": 0, "skipped": [], "already_done": []}
        return _fn

    import scripts.backfill_analyst_prices as bap
    monkeypatch.setattr(bap, "run_backfill", _mk(analyst_raises))
    monkeypatch.setattr(bvp, "run_backfill", _mk(vol_raises))

    rc = cr.main()
    return rc, recorded


def test_maintenance_success_records_ok_heartbeat(monkeypatch):
    rc, hb = _run_lane(monkeypatch)
    assert rc == 0
    assert hb["lane"] == "maintenance"
    assert hb["status"] == "ok"


def test_maintenance_failure_records_failed_heartbeat(monkeypatch):
    """The bug: this used to record status='ok' over a failed run."""
    rc, hb = _run_lane(monkeypatch, analyst_raises=True, vol_raises=True)
    assert rc == 1
    assert hb["lane"] == "maintenance"
    assert hb["status"] == "failed", "a failed lane must not report a green heartbeat"
    assert hb["detail"] and "analyst_prices" in hb["detail"]
    # Both sub-job names present proves the FORWARD isolation direction: job ②
    # still ran after job ① raised. (The test below only proves the backward
    # direction, since analyst is job ① and runs first regardless.)
    assert "vol_predictions" in hb["detail"]


def test_maintenance_partial_failure_still_marks_failed(monkeypatch):
    """One sub-job failing must not be masked by the other succeeding."""
    rc, hb = _run_lane(monkeypatch, vol_raises=True)
    assert rc == 1
    assert hb["status"] == "failed"
    assert "vol_predictions" in hb["detail"]


def test_one_subjob_failure_does_not_suppress_the_other(monkeypatch):
    """Isolation: the analyst job still runs when the vol job blows up."""
    import cron_runner as cr
    ran = []
    monkeypatch.setenv("ALERT_RUN_MODE", "maintenance")
    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)
    monkeypatch.setattr(cr, "_notify_failure", lambda *_a, **_kw: None)
    monkeypatch.setattr(cr, "_record_heartbeat", lambda *_a, **_kw: None)

    import scripts.backfill_analyst_prices as bap

    def _vol(*_a, **_kw):
        raise RuntimeError("boom")

    def _analyst(*_a, **_kw):
        ran.append("analyst")
        return {"updated": 1, "skipped_count": 0, "pending": 1, "offline": False}

    monkeypatch.setattr(bvp, "run_backfill", _vol)
    monkeypatch.setattr(bap, "run_backfill", _analyst)

    cr.main()
    assert ran == ["analyst"]
