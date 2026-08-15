"""Invariants protecting EXISTING machinery from the reference-shelf check.

Two things could go wrong when adding a staleness check to a codebase that
already has a health chip and a cron dead-man's-switch, and both would damage
instruments that matter more than the feature itself:

  1. If reference staleness joins the Home chip rollup, Home carries a
     permanent amber the user cannot clear today — and they learn to ignore the
     chip that ALSO reports "a cron lane has died."
  2. If it ever reaches the maintenance lane's `failures` list or return code,
     `main()` writes status="failed", and 🩺 System Trust grades the lane
     "down" — i.e. the dead-man's-switch reports a dead cron lane because a
     ticker list is old. That makes the one instrument that proves the pipeline
     runs tell a lie.

Test 2 is included even though v1 adds NO cron code, precisely so a future
version that wires up a push notification can't wire it up wrongly.
"""
from datetime import date

from stock_analyzer import reference_shelf as rs
from stock_analyzer import system_health


def _all_reference_maximally_overdue(monkeypatch):
    """Force every reference row to the worst severity it can reach."""
    monkeypatch.setattr(
        system_health, "check_reference_data",
        lambda: [
            {"key": "a", "label": "A", "severity": "down", "detail": "", "location": ""},
            {"key": "b", "label": "B", "severity": "warn", "detail": "", "location": ""},
        ],
    )


def _all_reference_fresh(monkeypatch):
    monkeypatch.setattr(
        system_health, "check_reference_data",
        lambda: [
            {"key": "a", "label": "A", "severity": "ok", "detail": "", "location": ""},
            {"key": "b", "label": "B", "severity": "ok", "detail": "", "location": ""},
        ],
    )


def _quiet_other_checks(monkeypatch):
    monkeypatch.setattr(system_health, "check_cron_liveness", lambda: [])
    monkeypatch.setattr(system_health, "check_data_stores", lambda: [])
    monkeypatch.setattr(system_health, "check_providers", lambda: [])
    monkeypatch.setattr(system_health, "check_caches", lambda _s=None: [])


# ── ① the Home chip must be blind to reference staleness ──────────────────────

def test_chip_identical_whether_reference_is_fresh_or_maximally_stale(monkeypatch):
    _quiet_other_checks(monkeypatch)

    _all_reference_fresh(monkeypatch)
    fresh = system_health.compute_health()

    _all_reference_maximally_overdue(monkeypatch)
    stale = system_health.compute_health()

    assert fresh["chip_severity"] == stale["chip_severity"] == "ok"
    assert fresh["n_warn"] == stale["n_warn"] == 0
    assert fresh["n_down"] == stale["n_down"] == 0, (
        "reference staleness leaked into the Home chip rollup — it would park a "
        "permanent amber the user can't clear and desensitize them to real outages"
    )


def test_reference_rows_are_still_reported_on_the_page(monkeypatch):
    """Excluded from the chip, but NOT hidden — the page must still show them."""
    _quiet_other_checks(monkeypatch)
    _all_reference_maximally_overdue(monkeypatch)
    health = system_health.compute_health()
    assert len(health["reference"]) == 2
    assert {r["severity"] for r in health["reference"]} == {"down", "warn"}


def test_a_real_outage_still_drives_the_chip(monkeypatch):
    """Guard against over-correcting: the chip must still fire for real faults."""
    _quiet_other_checks(monkeypatch)
    _all_reference_fresh(monkeypatch)
    monkeypatch.setattr(
        system_health, "check_cron_liveness",
        lambda: [{"key": "eod", "label": "EOD", "severity": "down", "detail": ""}],
    )
    health = system_health.compute_health()
    assert health["chip_severity"] == "down"
    assert health["n_down"] == 1


def test_compute_health_survives_a_broken_reference_check(monkeypatch):
    _quiet_other_checks(monkeypatch)

    def _boom():
        raise RuntimeError("reference check exploded")

    monkeypatch.setattr(system_health, "check_reference_data", _boom)
    health = system_health.compute_health()
    assert health["reference"] == []
    assert health["chip_severity"] == "ok"


# ── ② the cron dead-man's-switch must stay honest ─────────────────────────────

def test_stale_reference_data_never_fails_the_maintenance_lane(monkeypatch):
    """With both backfills succeeding and every reference table overdue, the
    maintenance lane must return 0, leave _LAST_LANE_FAILURE_DETAIL unset, and
    not send a failure email. A stale ticker list is not a dead cron lane."""
    import cron_runner as cr

    monkeypatch.setenv("ALERT_RUN_MODE", "maintenance")
    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)

    notified = []
    monkeypatch.setattr(cr, "_notify_failure", lambda *a, **k: notified.append(a))
    monkeypatch.setattr(cr, "_record_heartbeat", lambda *a, **k: None)

    ok_summary = {"updated": 0, "skipped_count": 0, "pending": 0, "offline": False,
                  "rows": 0, "tickers": 0, "skipped": [], "already_done": []}
    import scripts.backfill_analyst_prices as bap
    import scripts.backfill_vol_predictions as bvp
    monkeypatch.setattr(bap, "run_backfill", lambda *a, **k: ok_summary)
    monkeypatch.setattr(bvp, "run_backfill", lambda *a, **k: ok_summary)

    # Every reference table maximally stale.
    monkeypatch.setattr(rs, "shelf_status", lambda today=None: [
        {"key": "sector_universe", "label": "x", "location": "y", "kind": "as_of",
         "severity": "down", "detail": "d", "consequence": "c"},
    ])

    rc = cr.main()
    assert rc == 0, "stale reference data must not fail the maintenance lane"
    assert cr._LAST_LANE_FAILURE_DETAIL is None
    assert notified == [], "must not fire the cron dead-man's-switch email"


def test_maintenance_lane_does_not_import_reference_shelf(monkeypatch):
    """v1 is pull-only by design. If a future version adds a push digest it must
    go through _send_email directly, never _notify_failure — this test failing
    is the prompt to re-read that reasoning, not to delete the test."""
    import inspect

    import cron_runner as cr
    src = inspect.getsource(cr._run_maintenance)
    assert "reference_shelf" not in src, (
        "reference_shelf reached the maintenance lane — if this is deliberate, "
        "verify it cannot touch `failures` or the return code before updating "
        "this test (see test_stale_reference_data_never_fails_the_maintenance_lane)"
    )
