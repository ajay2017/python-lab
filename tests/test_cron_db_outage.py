"""A cron lane that cannot reach the database must SAY SO, not report success.

The defect this locks: `db.load_holdings()` collapsed a failed read into an
empty DataFrame, so `_build_context` could not tell "Supabase is unreachable"
from "the user owns nothing". Every lane logged one line, returned 0, and
`main()` recorded status="ok" — a Supabase outage was indistinguishable from a
quiet day. Most seriously, the pre-market PROTECTIVE lane (stop breaches,
deterioration EXIT, risk-off trims) would silently not run during a selloff
while 🩺 System Trust showed it healthy.

The two invariants worth more than the feature:
  1. DB unreachable  -> email + status="failed", never "ok".
  2. Genuinely empty holdings -> NO email, status="ok". A false positive here
     trains the owner to mute the alert, which kills the safety net entirely.
"""
import pandas as pd

from stock_analyzer import db


# ── the source fix: distinguishing unreadable from empty ──────────────────────

class _Boom:
    def table(self, *_a, **_kw):
        raise RuntimeError("supabase unreachable")


def test_or_none_returns_none_without_credentials(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_holdings_or_none() is None


def test_or_none_returns_none_when_the_query_raises(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _Boom())
    assert db.load_holdings_or_none() is None


def test_or_none_returns_empty_frame_when_table_is_genuinely_empty(monkeypatch):
    class _Empty:
        def table(self, *_a, **_kw): return self
        def select(self, *_a, **_kw): return self
        def order(self, *_a, **_kw): return self
        def execute(self): return type("R", (), {"data": []})()

    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _Empty())
    out = db.load_holdings_or_none()
    assert out is not None and isinstance(out, pd.DataFrame) and out.empty


def test_load_holdings_contract_is_unchanged_in_all_three_cases(monkeypatch):
    """The lenient wrapper must still return an empty frame on ANY failure —
    app.py assigns it straight into session_state and reads it everywhere."""
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_holdings().empty

    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _Boom())
    got = db.load_holdings()
    assert isinstance(got, pd.DataFrame) and got.empty


# ── the discriminator reaches the payload ─────────────────────────────────────

def test_build_context_reports_db_unavailable_without_credentials(monkeypatch):
    from stock_analyzer import headless_alert_engine as hae
    monkeypatch.setattr(hae.db, "has_db", lambda: False)
    ctx = hae._build_context(__import__("datetime").date(2026, 8, 16))
    assert ctx["ok"] is False and ctx["reason"] == "db_unavailable"


def test_build_context_reports_db_unavailable_when_the_read_fails(monkeypatch):
    """The direct regression: this path used to arrive as 'no holdings'."""
    from stock_analyzer import headless_alert_engine as hae
    monkeypatch.setattr(hae.db, "has_db", lambda: True)
    monkeypatch.setattr(hae.db, "load_holdings_or_none", lambda: None)
    ctx = hae._build_context(__import__("datetime").date(2026, 8, 16))
    assert ctx["reason"] == "db_unavailable"


def test_build_context_reports_no_holdings_for_a_genuinely_empty_book(monkeypatch):
    from stock_analyzer import headless_alert_engine as hae
    monkeypatch.setattr(hae.db, "has_db", lambda: True)
    monkeypatch.setattr(hae.db, "load_holdings_or_none",
                        lambda: pd.DataFrame(columns=["Ticker", "Shares", "Avg Cost ($)"]))
    ctx = hae._build_context(__import__("datetime").date(2026, 8, 16))
    assert ctx["reason"] == "no_holdings"


# ── lane-level invariants ─────────────────────────────────────────────────────

_FIXED_MORNING = "2026-08-17T08:30:00"   # a Monday, before the ET-noon cutoff


def _pin_clock_to_morning(monkeypatch, cr):
    """main() derives premarket vs eod from the ET hour, and deliberately does
    NOT honour ALERT_RUN_MODE=premarket (so a schedule change can't fire the
    wrong weekday lane). So the only way to exercise the premarket path is to
    pin the clock. Delegates everything except now() to the real datetime."""
    import datetime as _dt

    class _FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            naive = _dt.datetime.fromisoformat(_FIXED_MORNING)
            return tz.localize(naive) if tz is not None else naive

    monkeypatch.setattr(cr, "datetime", _FixedDatetime)


def _run_premarket(monkeypatch, *, reason, emails, heartbeats,
                   alert_state=None, save_state_ok=True):
    import cron_runner as cr

    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_RUN_MODE", raising=False)
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)
    _pin_clock_to_morning(monkeypatch, cr)

    payload = {"alerts": [], "built_at": "2026-08-16T08:00:00", "errors": ["x"]}
    if reason:
        payload["reason"] = reason
    monkeypatch.setattr(cr, "compute_protective_alerts", lambda **_k: payload)
    monkeypatch.setattr(cr, "_send_email",
                        lambda label, subj, html: emails.append((label, subj)) or True)
    monkeypatch.setattr(cr, "_record_heartbeat",
                        lambda lane, _n, status="ok", detail=None:
                        heartbeats.append((lane, status)))
    monkeypatch.setattr(cr, "_notify_failure", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: alert_state)
    monkeypatch.setattr(cr.db, "save_alert_state",
                        lambda *_a, **_k: save_state_ok)
    monkeypatch.setattr(cr.db, "save_exit_signals_batch", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "save_analyst_target_snapshots_batch", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "is_trading_day", lambda _d: True)
    return cr.main()


def test_db_outage_never_records_an_ok_heartbeat(monkeypatch):
    """THE invariant. A partial outage (holdings unreadable, rest of the DB
    fine) is the case where the heartbeat write actually succeeds — so without
    this it writes a fresh, genuinely green row over a scan that checked
    nothing."""
    emails, heartbeats = [], []
    rc = _run_premarket(monkeypatch, reason="db_unavailable",
                        emails=emails, heartbeats=heartbeats)
    assert rc == 1
    assert ("premarket", "ok") not in heartbeats
    assert ("premarket", "failed") in heartbeats


def test_db_outage_sends_exactly_one_email_naming_the_lane(monkeypatch):
    emails, heartbeats = [], []
    _run_premarket(monkeypatch, reason="db_unavailable",
                   emails=emails, heartbeats=heartbeats)
    assert len(emails) == 1
    label, subject = emails[0]
    assert label == "db-outage/premarket"
    assert "did NOT run" in subject and "database unreachable" in subject


def test_an_empty_book_sends_no_email_and_reports_ok(monkeypatch):
    """The false-positive guard — the single most important test here. An owner
    who genuinely holds nothing must never get an outage email, or they will
    mute the alert and the safety net dies."""
    emails, heartbeats = [], []
    rc = _run_premarket(monkeypatch, reason="no_holdings",
                       emails=emails, heartbeats=heartbeats)
    assert rc == 0
    assert emails == []
    assert ("premarket", "ok") in heartbeats


def test_a_provider_outage_is_not_reported_as_a_db_outage(monkeypatch):
    """no_bundles means the book was read fine and the PRICE feeds failed —
    a different fault with a different fix. Must not send the DB email."""
    emails, heartbeats = [], []
    _run_premarket(monkeypatch, reason="no_bundles",
                   emails=emails, heartbeats=heartbeats)
    assert emails == []


# ── dedup must fail OPEN ──────────────────────────────────────────────────────

def test_dedup_unavailable_still_sends(monkeypatch):
    """Total outage: load_alert_state returns None. That must read as 'no dedup
    available, send', never as 'already sent today'."""
    emails, heartbeats = [], []
    _run_premarket(monkeypatch, reason="db_unavailable", emails=emails,
                   heartbeats=heartbeats, alert_state=None)
    assert len(emails) == 1


def test_dedup_write_failure_does_not_crash_or_suppress(monkeypatch):
    emails, heartbeats = [], []
    rc = _run_premarket(monkeypatch, reason="db_unavailable", emails=emails,
                        heartbeats=heartbeats, save_state_ok=False)
    assert rc == 1 and len(emails) == 1


def test_dedup_suppresses_a_second_send_for_the_same_lane_same_day(monkeypatch):
    """Partial outage: the DB is well enough to dedup, so it should."""
    emails, heartbeats = [], []
    _run_premarket(monkeypatch, reason="db_unavailable", emails=emails,
                   heartbeats=heartbeats,
                   alert_state={"last_emailed_date": _FIXED_MORNING[:10],
                                "last_fingerprint": "premarket"})
    assert emails == [], "same lane, same day, already emailed — must not resend"


def test_a_different_lane_on_the_same_day_still_sends(monkeypatch):
    """Dedup is per lane, not global — each lane's silence is its own signal."""
    import cron_runner as cr
    sent = []
    now = cr.datetime.now(cr._ET)
    monkeypatch.setattr(cr, "_send_email", lambda l, s, h: sent.append(l) or True)
    monkeypatch.setattr(cr.db, "load_alert_state",
                        lambda *_a, **_k: {"last_emailed_date": now.date().isoformat(),
                                           "last_fingerprint": "premarket"})
    monkeypatch.setattr(cr.db, "save_alert_state", lambda **_k: True)
    rc = cr._handle_db_unavailable("eod", now, "unreachable")
    assert rc == 1 and sent == ["db-outage/eod"]


# ── the notification path must not depend on the thing that's broken ──────────

def test_the_outage_email_renders_without_touching_the_database(monkeypatch):
    from stock_analyzer import db as _db
    from stock_analyzer.notify import render_db_outage_email

    def _explode(*_a, **_kw):
        raise AssertionError("the outage email path must not touch Supabase")

    monkeypatch.setattr(_db, "_client", _explode)
    subject, html = render_db_outage_email(
        lane="premarket", lane_label="pre-market protective scan",
        what_did_not_run="Stop breaches were NOT evaluated today.",
        detail="holdings unreadable", built_at="2026-08-16T08:00:00",
    )
    assert "did NOT run" in subject
    assert "Stop breaches were NOT evaluated today." in html
    assert "infrastructure fault, not a market signal" in html


def test_handle_db_unavailable_never_raises(monkeypatch):
    """A failure to notify must never mask the original fault."""
    import cron_runner as cr

    def _boom(*_a, **_kw):
        raise RuntimeError("everything is broken")

    monkeypatch.setattr(cr, "_send_email", _boom)
    monkeypatch.setattr(cr.db, "load_alert_state", _boom)
    monkeypatch.setattr(cr.db, "save_alert_state", _boom)
    rc = cr._handle_db_unavailable("premarket", cr.datetime.now(cr._ET), "x")
    assert rc == 1


def test_dedup_fingerprint_union_persists_across_lanes(monkeypatch):
    """The union must be WRITTEN, not just read. If a second lane overwrote the
    fingerprint with only its own name, a third lane's send would re-arm the
    first — and premarket would email twice in one outage."""
    import cron_runner as cr
    written = {}
    now = cr.datetime.now(cr._ET)
    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "load_alert_state",
                        lambda *_a, **_k: {"last_emailed_date": now.date().isoformat(),
                                           "last_fingerprint": "premarket"})
    monkeypatch.setattr(cr.db, "save_alert_state",
                        lambda **kw: written.update(kw) or True)
    cr._handle_db_unavailable("eod", now, "unreachable")
    assert written["fingerprint"] == "eod|premarket"


def test_thesis_lane_outage_fails_the_run_and_the_heartbeat(monkeypatch):
    """The Sunday lane runs three sub-jobs in a loop. Their return codes must be
    CAPTURED — discarding them sent outage emails and then recorded status='ok',
    which is the silent success this whole change removes."""
    import cron_runner as cr
    heartbeats, emails = [], []

    monkeypatch.setenv("ALERT_RUN_MODE", "thesis")
    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)
    monkeypatch.setattr(cr, "_record_heartbeat",
                        lambda lane, _n, status="ok", detail=None:
                        heartbeats.append((lane, status)))
    monkeypatch.setattr(cr, "_notify_failure",
                        lambda *_a, **_k: emails.append("notify_failure"))
    monkeypatch.setattr(cr, "_run_thesis", lambda *_a, **_k: 1)     # outage
    monkeypatch.setattr(cr, "_run_debrief", lambda *_a, **_k: 0)
    monkeypatch.setattr(cr, "_run_monthly_report", lambda *_a, **_k: 0)

    rc = cr.main()
    assert rc == 1, "a sub-job outage must fail the Sunday run"
    assert ("thesis", "failed") in heartbeats
    assert ("thesis", "ok") not in heartbeats
    assert emails == [], "an outage must not also fire the crash-notification email"


def test_thesis_lane_still_reports_ok_when_all_subjobs_succeed(monkeypatch):
    import cron_runner as cr
    heartbeats = []
    monkeypatch.setenv("ALERT_RUN_MODE", "thesis")
    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)
    monkeypatch.setattr(cr, "_record_heartbeat",
                        lambda lane, _n, status="ok", detail=None:
                        heartbeats.append((lane, status)))
    monkeypatch.setattr(cr, "_notify_failure", lambda *_a, **_k: None)
    for fn in ("_run_thesis", "_run_debrief", "_run_monthly_report"):
        monkeypatch.setattr(cr, fn, lambda *_a, **_k: 0)
    assert cr.main() == 0
    assert ("thesis", "ok") in heartbeats


def test_intraday_no_scan_yet_with_a_healthy_db_sends_no_email(monkeypatch):
    """End-to-end false-positive guard on a PROBE lane: 'the scan hasn't run
    yet' and 'the DB is down' both surface as load_scanner_cache() -> None."""
    import cron_runner as cr
    emails = []
    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_k: emails.append(1) or True)
    monkeypatch.setattr(cr.db, "load_scanner_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                        lambda: pd.DataFrame({"Ticker": ["AAPL"]}))
    rc = cr._run_intraday(cr.datetime.now(cr._ET), force=True)
    assert rc == 0 and emails == [], "a healthy DB with no scan yet is not an outage"


def test_probe_reports_none_when_the_database_is_healthy(monkeypatch):
    import cron_runner as cr
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                        lambda: pd.DataFrame({"Ticker": ["AAPL"]}))
    assert cr._db_unavailable_detail() is None


def test_probe_detects_an_unreadable_holdings_table(monkeypatch):
    import cron_runner as cr
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none", lambda: None)
    assert "holdings" in (cr._db_unavailable_detail() or "")
