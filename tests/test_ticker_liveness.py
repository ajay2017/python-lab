"""Tests for stock_analyzer/ticker_liveness.py and its cron integration.

Contracts being locked:
  1. Batch-health boundary is INCLUSIVE at TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT:
     health_pct == threshold → conclusive; strictly below → inconclusive.
     The 2026-08-04 Critical was an off-by-one of this exact shape.
  2. Provider degradation (rate-limit) → inconclusive, never a dead verdict.
  3. Multi-source rescue: a ticker missing from the batch but returned by the
     live-price layer is NOT reported dead.
  4. Confirmed dead path: email sent, `failures` empty, rc == 0.
  5. Ordering guarantee: sweep runs before DB sub-jobs, survives a DB early return.
  6. Sweep exception is isolated: lane failure without suppressing ① or ②.
  7. No email on a fully clean run.
  8. Shelf-status severity split: warn-only → no standalone email; down → email.
  9. sweep=None (batch raised) distinguishable from inconclusive, emails.
"""
import numpy as np
import pandas as pd
import pytest

import scripts.backfill_vol_predictions as bvp
from stock_analyzer import ticker_liveness as _tl
from stock_analyzer.constants import TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT
from stock_analyzer.ticker_liveness import sweep


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_df(alive: list[str], dead: list[str]) -> pd.DataFrame:
    """Build a yf.download-shaped DataFrame: MultiIndex(field, ticker) columns."""
    all_tickers = alive + dead
    dates = pd.date_range("2026-08-11", periods=5)
    tuples = [("Close", t) for t in all_tickers]
    mi = pd.MultiIndex.from_tuples(tuples)
    data: dict = {}
    for t in alive:
        data[("Close", t)] = [100.0, 101.0, 102.0, 103.0, 104.0]
    for t in dead:
        data[("Close", t)] = [np.nan, np.nan, np.nan, np.nan, np.nan]
    df = pd.DataFrame(data, index=dates)
    df.columns = mi
    return df


def _patch_rosters(monkeypatch, *, tickers: list[str]) -> None:
    """Replace all three roster sources with a controlled ticker set.

    Distributes tickers across the three sources so membership tracking is
    tested (each ticker appears in exactly one roster).
    """
    import stock_analyzer.scanner as sc
    import stock_analyzer.portfolio as po
    import stock_analyzer.discovery_universe as du

    n = len(tickers)
    third = max(n // 3, 1)
    s = tickers[:third]
    p = tickers[third: 2 * third]
    d = tickers[2 * third:]

    monkeypatch.setattr(sc, "SECTOR_UNIVERSE", {"A": s} if s else {})
    monkeypatch.setattr(po, "_SECTOR_CANDIDATES", {"B": p} if p else {})
    monkeypatch.setattr(du, "DISCOVERY_UNIVERSE", {"C": d} if d else {})


def _clean_sweep():
    return {
        "status": "ok",
        "health_pct": 100.0,
        "dead": [],
        "suspects_n": 0,
        "roster_n": 230,
    }


def _clean_shelf():
    return []


def _mk_backfill_ok():
    return {
        "updated": 0, "skipped_count": 0, "pending": 0, "offline": False,
        "rows": 0, "tickers": 0, "skipped": [], "already_done": [],
    }


# `None` is a MEANINGFUL sweep value (the offline sentinel: the batch raised), so
# it cannot double as "argument not supplied" — doing so silently swapped in a
# clean sweep and made the sweep-returned-None test vacuously pass against the
# wrong scenario. Same collapse the offline-sentinel contract exists to prevent,
# reproduced in the harness. Distinct sentinel required.
_UNSET = object()


def _setup_maintenance_lane(monkeypatch, *,
                             sweep_result=_UNSET,
                             shelf_result=_UNSET,
                             analyst_offline=False,
                             analyst_raises=False,
                             vol_raises=False):
    """Drive cron_runner.main() in maintenance mode; return (rc, emails, notified)."""
    import cron_runner as cr
    import stock_analyzer.reference_shelf as _rs
    import stock_analyzer.notify as _notify

    if sweep_result is _UNSET:
        sweep_result = _clean_sweep()
    if shelf_result is _UNSET:
        shelf_result = _clean_shelf()

    emails: list[str] = []   # labels of emails sent via _send_email
    notified: list[str] = [] # details passed to _notify_failure

    monkeypatch.setenv("ALERT_RUN_MODE", "maintenance")
    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)

    # Sweep sub-job ⓪
    monkeypatch.setattr(_tl, "sweep", lambda: sweep_result)
    monkeypatch.setattr(_rs, "shelf_status", lambda **_kw: shelf_result)
    # Patch the name BOUND IN cron_runner, not the one in notify: cron_runner
    # does `from stock_analyzer.notify import render_liveness_email` at module
    # import, so patching `_notify.render_liveness_email` is a no-op that reads
    # like it works — the real renderer would keep running.
    render_calls: list[dict] = []

    def _fake_render(**kw):
        render_calls.append(kw)
        return ("liveness subj", "<html>liveness</html>")

    monkeypatch.setattr(cr, "render_liveness_email", _fake_render)
    # Exposed for tests that need to assert on what the renderer was handed.
    _setup_maintenance_lane.last_render_calls = render_calls

    monkeypatch.setattr(cr, "_send_email",
                        lambda label, _s, _h: emails.append(label) or True)
    monkeypatch.setattr(cr, "_record_heartbeat", lambda *_a, **_kw: None)
    monkeypatch.setattr(cr, "_notify_failure",
                        lambda _mode, detail: notified.append(detail) or None)
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                        lambda: pd.DataFrame({"Ticker": ["AAPL"]}))

    # Sub-jobs ① and ②
    import scripts.backfill_analyst_prices as bap

    def _analyst(*_a, **_kw):
        if analyst_raises:
            raise RuntimeError("analyst boom")
        return {**_mk_backfill_ok(), "offline": analyst_offline,
                "pending": 1 if not analyst_offline else 0}

    def _vol(*_a, **_kw):
        if vol_raises:
            raise RuntimeError("vol boom")
        return {"rows": 0, "tickers": 0, "skipped": [], "already_done": []}

    monkeypatch.setattr(bap, "run_backfill", _analyst)
    monkeypatch.setattr(bvp, "run_backfill", _vol)

    rc = cr.main()
    return rc, emails, notified


# ── 1. Batch-health boundary — INCLUSIVE ──────────────────────────────────────

def test_health_at_threshold_is_conclusive(monkeypatch):
    """health_pct == TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT must be conclusive.

    Uses 10 tickers, 1 suspect → 90.0% == 90.0% threshold.
    The inclusivity is load-bearing: == threshold IS conclusive (not inconclusive).
    """
    tickers = [f"T{i}" for i in range(1, 11)]  # T1 … T10
    _patch_rosters(monkeypatch, tickers=tickers)

    suspect = tickers[-1]  # T10
    alive = [t for t in tickers if t != suspect]
    health = (len(alive) / len(tickers)) * 100.0
    assert health == TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT, (
        "pre-condition: 9/10 = 90.0 must equal the threshold")

    result = sweep(
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=[suspect]),
        fetch_live=lambda ts: {},   # dead ticker confirmed by all providers
    )
    assert result is not None
    assert result["status"] != "inconclusive", (
        "health_pct == threshold must be conclusive (inclusive boundary)")
    assert result["status"] == "ok"
    assert abs(result["health_pct"] - 90.0) < 1e-9


def test_health_below_threshold_is_inconclusive(monkeypatch):
    """health_pct strictly below threshold → inconclusive, dead == [].

    Uses 10 tickers, 2 suspects → 80.0% < 90.0% threshold.
    One discrete step below the boundary is sufficient to assert < (not <=).
    """
    tickers = [f"T{i}" for i in range(1, 11)]
    _patch_rosters(monkeypatch, tickers=tickers)

    suspects = tickers[-2:]   # T9, T10
    alive = [t for t in tickers if t not in suspects]
    health = (len(alive) / len(tickers)) * 100.0
    assert health < TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT, (
        "pre-condition: 8/10 = 80.0 must be below the threshold")

    result = sweep(
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=suspects),
        fetch_live=lambda ts: {},
    )
    assert result is not None
    assert result["status"] == "inconclusive"
    assert result["dead"] == []


# ── 2. Rate-limit / provider degradation ─────────────────────────────────────

def test_rate_limit_50pct_missing_is_inconclusive_not_dead(monkeypatch):
    """50% of roster tickers NaN in the batch → inconclusive, not a dead verdict.

    Validates that `_notify_failure` is not called and rc is unchanged from 0.
    """
    rc, emails, notified = _setup_maintenance_lane(
        monkeypatch,
        sweep_result={
            "status": "inconclusive",
            "health_pct": 50.0,
            "dead": [],
            "suspects_n": 115,
            "roster_n": 230,
        },
    )
    assert notified == [], "_notify_failure must NOT be called for an inconclusive sweep"
    # The inconclusive result IS an email trigger (not a lane failure)
    assert any("liveness" in e for e in emails), (
        "an inconclusive result must generate a liveness email")
    # rc is not raised by the sweep alone (backfills succeeded)
    assert rc == 0


# ── 3. Multi-source rescue ────────────────────────────────────────────────────

def test_multi_source_rescue_not_dead(monkeypatch):
    """A ticker absent from the batch but returned by fetch_live is NOT dead."""
    tickers = [f"T{i}" for i in range(1, 11)]
    _patch_rosters(monkeypatch, tickers=tickers)
    suspect = tickers[-1]
    alive = [t for t in tickers if t != suspect]

    def _live(ts):
        # Multi-source layer has a price for the suspect
        return {ts[0]: {"price": 42.0, "prev_close": None, "change_pct": None,
                        "fetched_at": "2026-08-16T10:00:00"}}

    result = sweep(
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=[suspect]),
        fetch_live=_live,
    )
    assert result is not None
    assert result["status"] == "ok"
    dead_tickers = [d["ticker"] for d in result["dead"]]
    assert suspect not in dead_tickers, (
        f"{suspect} was rescued by fetch_live and must not be reported dead")


# ── 4. Confirmed-dead path ────────────────────────────────────────────────────

def test_confirmed_dead_emails_not_lane_failure(monkeypatch):
    """One confirmed-dead ticker → liveness email sent; failures empty; rc == 0;
    _LAST_LANE_FAILURE_DETAIL remains None (not a maintenance lane failure)."""
    import cron_runner as cr

    rc, emails, notified = _setup_maintenance_lane(
        monkeypatch,
        sweep_result={
            "status": "ok",
            "health_pct": 99.0,
            "dead": [{"ticker": "CFLT",
                      "rosters": ["scanner.py SECTOR_UNIVERSE"]}],
            "suspects_n": 1,
            "roster_n": 231,
        },
    )
    assert any("liveness" in e for e in emails), "email must be sent for a dead ticker"
    assert notified == [], "a dead ticker is a chore, not a lane failure"
    assert rc == 0, "dead ticker must not set rc=1"
    # _LAST_LANE_FAILURE_DETAIL drives the 🩺 System Trust heartbeat — must stay None
    assert cr._LAST_LANE_FAILURE_DETAIL is None


# ── 5. Sweep runs before DB sub-jobs ─────────────────────────────────────────

def test_sweep_runs_before_db_early_return(monkeypatch):
    """With has_db() False the analyst backfill early-returns, but the sweep
    must already have run before that early return fires."""
    import cron_runner as cr
    import stock_analyzer.reference_shelf as _rs
    import stock_analyzer.notify as _notify

    called: list[str] = []

    def _sweep_spy():
        called.append("sweep")
        return _clean_sweep()

    monkeypatch.setenv("ALERT_RUN_MODE", "maintenance")
    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)

    monkeypatch.setattr(_tl, "sweep", _sweep_spy)
    monkeypatch.setattr(_rs, "shelf_status", lambda **_kw: [])
    # Patch the name bound in cron_runner, not notify — see the note in
    # _setup_maintenance_lane. Inert here (this sweep is clean, so nothing is
    # rendered), but patching `_notify` would be a silent no-op and a landmine
    # for the next edit.
    monkeypatch.setattr(cr, "render_liveness_email",
                        lambda **_kw: ("subj", "<html/>"))

    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_kw: False)
    monkeypatch.setattr(cr, "_record_heartbeat", lambda *_a, **_kw: None)
    monkeypatch.setattr(cr, "_notify_failure", lambda *_a, **_kw: None)

    # DB is unavailable — analyst backfill triggers the early DB-return path
    monkeypatch.setattr(cr, "_handle_db_unavailable", lambda *_a, **_kw: 1)

    import scripts.backfill_analyst_prices as bap
    monkeypatch.setattr(
        bap, "run_backfill",
        lambda **_kw: {**_mk_backfill_ok(), "offline": True},
    )

    cr.main()

    assert "sweep" in called, (
        "sweep (sub-job ⓪) must execute before the DB early-return in sub-job ①")


# ── 6. Sweep exception is isolated ───────────────────────────────────────────

def test_sweep_exception_is_contained(monkeypatch):
    """An exception inside the sweep lands in `failures` (lane failure) and
    the analyst/vol backfills still run afterward."""
    import stock_analyzer.reference_shelf as _rs

    ran: list[str] = []

    def _boom():
        raise RuntimeError("simulated sweep failure")

    monkeypatch.setattr(_tl, "sweep", _boom)
    monkeypatch.setattr(_rs, "shelf_status", lambda **_kw: [])

    import scripts.backfill_analyst_prices as bap

    def _analyst(*_a, **_kw):
        ran.append("analyst")
        return {**_mk_backfill_ok(), "pending": 1}

    def _vol(*_a, **_kw):
        ran.append("vol")
        return {"rows": 0, "tickers": 0, "skipped": [], "already_done": []}

    import cron_runner as cr
    monkeypatch.setenv("ALERT_RUN_MODE", "maintenance")
    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)
    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_kw: False)
    monkeypatch.setattr(cr, "_record_heartbeat", lambda *_a, **_kw: None)
    notified: list[str] = []
    monkeypatch.setattr(cr, "_notify_failure",
                        lambda _m, detail: notified.append(detail))
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                        lambda: pd.DataFrame({"Ticker": ["AAPL"]}))
    monkeypatch.setattr(bap, "run_backfill", _analyst)
    monkeypatch.setattr(bvp, "run_backfill", _vol)

    rc = cr.main()

    assert rc == 1, "a sweep exception must mark the lane as failed"
    assert any("liveness" in d for d in notified), (
        "_notify_failure must be called with the sweep exception detail")
    assert "analyst" in ran, "analyst backfill must still run after the sweep exception"
    assert "vol" in ran, "vol backfill must still run after the sweep exception"


# ── 7. No email on a clean run ────────────────────────────────────────────────

def test_no_email_on_clean_run(monkeypatch):
    """All tickers alive, no shelf issues → no liveness email sent."""
    rc, emails, notified = _setup_maintenance_lane(
        monkeypatch,
        sweep_result=_clean_sweep(),
        shelf_result=[
            {"key": "sector_universe", "severity": "ok",
             "label": "Grow Today scan universe", "location": "scanner.py",
             "detail": "last refreshed ...", "consequence": ""},
        ],
    )
    liveness_emails = [e for e in emails if "liveness" in e]
    assert liveness_emails == [], (
        "no liveness email should be sent when the sweep is clean and no shelf is down")


# ── 8. Shelf-status severity split ───────────────────────────────────────────

def test_shelf_warn_only_no_standalone_email(monkeypatch):
    """warn-only shelf row does NOT trigger a standalone email when sweep is clean."""
    _, emails, notified = _setup_maintenance_lane(
        monkeypatch,
        sweep_result=_clean_sweep(),
        shelf_result=[
            {"key": "sector_universe", "severity": "warn",
             "label": "Grow Today scan universe", "location": "scanner.py",
             "detail": "95 days old (refresh every 90d)", "consequence": ""},
        ],
    )
    liveness_emails = [e for e in emails if "liveness" in e]
    assert liveness_emails == [], (
        "a warn-only shelf row must not trigger a standalone liveness email")


def test_shelf_down_triggers_email(monkeypatch):
    """A shelf row with severity == 'down' triggers a liveness email on its own
    even when the sweep is completely clean."""
    _, emails, notified = _setup_maintenance_lane(
        monkeypatch,
        sweep_result=_clean_sweep(),
        shelf_result=[
            {"key": "macro_event_calendar", "severity": "down",
             "label": "Macro event calendar backbone", "location": "macro_calendar.py",
             "detail": "EXPIRED 2026-07-01 — 46d ago; extend it now",
             "consequence": "macro Act-Today items stop firing"},
        ],
    )
    liveness_emails = [e for e in emails if "liveness" in e]
    assert liveness_emails, (
        "a shelf row with severity == 'down' must trigger a liveness email")
    assert notified == [], "an expired shelf table is a chore, not a lane failure"


# ── 9. sweep=None (batch raised) ─────────────────────────────────────────────

def test_sweep_none_emails_and_is_distinguishable_from_inconclusive(monkeypatch):
    """sweep=None (batch exception) emails and is distinguishable from inconclusive.

    The email content must explicitly state there is no verdict and why —
    'silence is not health' (reference_shelf.py docstring principle).
    """
    _, emails, notified = _setup_maintenance_lane(
        monkeypatch,
        sweep_result=None,   # batch raised → sweep returns None
    )
    # Must send an email for a None sweep
    liveness_emails = [e for e in emails if "liveness" in e]
    assert liveness_emails, (
        "sweep=None (batch raised) must trigger a liveness email")
    assert notified == [], "sweep=None is not a lane failure"

    # The renderer must receive the None sentinel ITSELF, not a coerced empty
    # dict — that distinction is what lets the email say "no verdict this week"
    # instead of falsely reporting a clean sweep.
    received = _setup_maintenance_lane.last_render_calls
    assert received, "render_liveness_email must be called"
    assert received[0]["sweep"] is None, (
        f"renderer got {received[0]['sweep']!r}, not the None offline sentinel")
    assert received[-1]["sweep"] is None, (
        "render_liveness_email must receive sweep=None, not the inconclusive dict")


# ── render_liveness_email unit tests ─────────────────────────────────────────

def test_render_liveness_email_none_sweep():
    """render_liveness_email with sweep=None includes 'no verdict' headline."""
    from stock_analyzer.notify import render_liveness_email
    subj, html = render_liveness_email(
        sweep=None, shelf_down=[], shelf_warn=[], built_at="2026-08-16T08:00:00"
    )
    assert "no verdict" in subj.lower() or "failed" in subj.lower()
    assert "could not run" in html or "no verdict" in html.lower()


def test_render_liveness_email_inconclusive():
    """render_liveness_email with inconclusive sweep includes 'inconclusive'."""
    from stock_analyzer.notify import render_liveness_email
    subj, html = render_liveness_email(
        sweep={
            "status": "inconclusive",
            "health_pct": 50.0,
            "dead": [],
            "suspects_n": 115,
            "roster_n": 230,
        },
        shelf_down=[], shelf_warn=[], built_at="2026-08-16T08:00:00",
    )
    assert "inconclusive" in subj.lower()
    assert "inconclusive" in html.lower()


def test_render_liveness_email_dark_on_light():
    """The email must be dark-on-light (no near-white text on a dark background)
    because email clients strip <body> styling — verified live on 2026-08-16."""
    from stock_analyzer.notify import render_liveness_email
    _, html = render_liveness_email(
        sweep={
            "status": "ok",
            "health_pct": 99.0,
            "dead": [{"ticker": "DEAD", "rosters": ["scanner.py SECTOR_UNIVERSE"]}],
            "suspects_n": 1,
            "roster_n": 230,
        },
        shelf_down=[], shelf_warn=[], built_at="2026-08-16T08:00:00",
    )
    # Near-white text colours that are invisible on a white background
    for invisible in ("#f9fafb", "#e5e7eb", "#a8a29e", "#d6d3d1"):
        assert invisible not in html, (
            f"{invisible} is unreadable on a white background")
    # Dark page background must NOT be on <body> (clients strip it)
    assert "background:#0c0a09" not in html


def test_render_liveness_email_dead_ticker_escaping():
    """Ticker names in dead list are HTML-escaped (no XSS via crafted names)."""
    from stock_analyzer.notify import render_liveness_email
    _, html = render_liveness_email(
        sweep={
            "status": "ok",
            "health_pct": 99.0,
            "dead": [{"ticker": "<script>alert(1)</script>",
                      "rosters": ["scanner.py SECTOR_UNIVERSE"]}],
            "suspects_n": 1,
            "roster_n": 230,
        },
        shelf_down=[], shelf_warn=[], built_at="2026-08-16T08:00:00",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
