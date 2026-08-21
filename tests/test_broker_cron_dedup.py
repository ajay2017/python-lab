"""The `broker` cron lane's own failure email must dedup to 1/day.

Added 2026-08-21 alongside the fix: `_notify_failure("broker", ...)` had no
dedup, which was fine only because the lane was assumed to run once daily
(the lane's own comments flagged this as a prerequisite before increasing
cron frequency past daily — see cron_runner.py history). `_notify_broker_failure`
wraps it with the same dedup shape `_handle_db_unavailable` already uses for
DB outages, but keyed to its own alert_state row (`_BROKER_FAILURE_ROW`) since
this covers SnapTrade-side failures, not DB outages.

Same invariant as the DB-outage dedup: fail OPEN (send) whenever dedup state
is unavailable or errors — a duplicate email is far cheaper than a silently
swallowed failure.
"""
import cron_runner as cr


def test_sends_when_no_prior_alert_state(monkeypatch):
    sent = []
    monkeypatch.setattr(cr, "_notify_failure", lambda mode, detail: sent.append((mode, detail)))
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda **_k: True)
    now = cr.datetime.now(cr._ET)

    cr._notify_broker_failure(now, "SnapTrade unreachable")

    assert sent == [("broker", "SnapTrade unreachable")]


def test_reads_and_writes_its_own_dedicated_row_not_the_db_outage_row(monkeypatch):
    """This lane must own `_BROKER_FAILURE_ROW` (6), not collide with the
    shared `_DB_OUTAGE_ROW` (5) other lanes write on a DB outage — a
    regression that silently re-pointed this at row 5 would otherwise pass
    every other test in this file."""
    read_row_ids = []
    written = {}
    monkeypatch.setattr(cr, "_notify_failure", lambda *_a, **_k: None)

    def _load(row_id):
        read_row_ids.append(row_id)
        return None

    monkeypatch.setattr(cr.db, "load_alert_state", _load)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda **kw: written.update(kw) or True)
    now = cr.datetime.now(cr._ET)

    cr._notify_broker_failure(now, "x")

    assert read_row_ids == [cr._BROKER_FAILURE_ROW]
    assert written["row_id"] == cr._BROKER_FAILURE_ROW
    assert written["fingerprint"] == "broker"
    assert cr._BROKER_FAILURE_ROW != cr._DB_OUTAGE_ROW


def test_a_prior_send_from_yesterday_still_sends_today(monkeypatch):
    """Pins the date COMPARISON, not just the equality branch — a prior
    state that isn't today's date must never suppress."""
    sent = []
    monkeypatch.setattr(cr, "_notify_failure", lambda mode, detail: sent.append(mode))
    now = cr.datetime.now(cr._ET)
    yesterday = (now - cr.timedelta(days=1)).date().isoformat()
    monkeypatch.setattr(
        cr.db, "load_alert_state",
        lambda *_a, **_k: {"last_emailed_date": yesterday, "last_fingerprint": "broker"},
    )
    monkeypatch.setattr(cr.db, "save_alert_state", lambda **_k: True)

    cr._notify_broker_failure(now, "SnapTrade unreachable")

    assert sent == ["broker"]


def test_dedup_unavailable_still_sends(monkeypatch):
    """A load_alert_state read failure must read as 'no dedup available, send',
    never as 'already sent today'."""
    sent = []
    monkeypatch.setattr(cr, "_notify_failure", lambda mode, detail: sent.append(mode))

    def _boom(*_a, **_kw):
        raise RuntimeError("db offline")

    monkeypatch.setattr(cr.db, "load_alert_state", _boom)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda **_k: True)
    now = cr.datetime.now(cr._ET)

    cr._notify_broker_failure(now, "x")

    assert sent == ["broker"]


def test_dedup_suppresses_a_second_send_the_same_day(monkeypatch):
    sent = []
    monkeypatch.setattr(cr, "_notify_failure", lambda mode, detail: sent.append(mode))
    now = cr.datetime.now(cr._ET)
    monkeypatch.setattr(
        cr.db, "load_alert_state",
        lambda *_a, **_k: {"last_emailed_date": now.date().isoformat(), "last_fingerprint": "broker"},
    )
    monkeypatch.setattr(cr.db, "save_alert_state", lambda **_k: True)

    cr._notify_broker_failure(now, "SnapTrade unreachable")

    assert sent == [], "same lane, same day, already emailed — must not resend"


def test_dedup_write_failure_does_not_crash_or_suppress_the_send(monkeypatch):
    sent = []
    monkeypatch.setattr(cr, "_notify_failure", lambda mode, detail: sent.append(mode))
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: None)

    def _boom(**_kw):
        raise RuntimeError("db offline")

    monkeypatch.setattr(cr.db, "save_alert_state", _boom)
    now = cr.datetime.now(cr._ET)

    cr._notify_broker_failure(now, "x")  # must not raise

    assert sent == ["broker"]


def test_never_raises_even_when_everything_is_broken(monkeypatch):
    def _boom(*_a, **_kw):
        raise RuntimeError("everything is broken")

    monkeypatch.setattr(cr, "_notify_failure", _boom)
    monkeypatch.setattr(cr.db, "load_alert_state", _boom)
    monkeypatch.setattr(cr.db, "save_alert_state", _boom)
    now = cr.datetime.now(cr._ET)

    cr._notify_broker_failure(now, "x")  # must not raise
