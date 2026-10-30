"""2026-10-30: a failed manual_stops read must be distinguishable from "no manual
stops" (three-state). Until the automatic held stop is fixed (finding #26 C3) the
manual stops were the only ones that could fire (the held stop of C3 has since shipped), so a silent {} hid every working
stop. Pins: the strict db reader, the lenient wrapper, the premarket lane failure
(alerts still sent, dedup state saved), that the EOD lane does NOT fail on it, and
both ends of the app.py wiring (call, banner, None publish, stale-banner render)."""
import ast
import datetime
import re
from pathlib import Path

import pytest

import cron_runner as cr
from stock_analyzer import db
from tests._ast_cache import parse_file_cached

_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime.datetime(2026, 10, 30, 8, 30)


class _Exec:
    def __init__(self, data): self.data = data


class _Builder:
    def __init__(self, rows, exc): self._rows, self._exc = rows, exc
    def select(self, *_a, **_k): return self

    def execute(self):
        if self._exc is not None:
            raise self._exc
        return _Exec(self._rows)


class _Client:
    def __init__(self, rows=None, exc=None): self._rows, self._exc = rows, exc
    def table(self, _n): return _Builder(self._rows, self._exc)


def _patch(monkeypatch, *, has=True, rows=None, exc=None):
    monkeypatch.setattr(db, "has_db", lambda: has)
    monkeypatch.setattr(db, "_client", lambda: _Client(rows, exc))


# ── db ───────────────────────────────────────────────────────────────────────

def test_strict_returns_dict_on_rows(monkeypatch):
    _patch(monkeypatch, rows=[{"ticker": "aapl", "stop_price": 190.5, "set_at": "x",
                               "note": None, "source_action": None}])
    out = db.load_manual_stops_or_none()
    assert out == {"AAPL": {"stop_price": 190.5, "set_at": "x", "note": "",
                            "source_action": ""}}


def test_strict_returns_empty_dict_on_empty_table(monkeypatch):
    _patch(monkeypatch, rows=[])
    assert db.load_manual_stops_or_none() == {}


def test_strict_returns_none_on_exception(monkeypatch):
    _patch(monkeypatch, exc=RuntimeError("outage"))
    assert db.load_manual_stops_or_none() is None


def test_strict_returns_none_when_no_db(monkeypatch):
    _patch(monkeypatch, has=False)
    assert db.load_manual_stops_or_none() is None


def test_lenient_wrapper_still_returns_empty_dict_on_failure(monkeypatch):
    _patch(monkeypatch, exc=RuntimeError("outage"))
    assert db.load_manual_stops() == {}
    _patch(monkeypatch, has=False)
    assert db.load_manual_stops() == {}


# ── lanes ────────────────────────────────────────────────────────────────────

def _run_pm(monkeypatch, payload):
    monkeypatch.setattr(cr, "compute_protective_alerts", lambda **_k: payload)
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda **_k: None)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: {})
    saved = []
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *a, **_k: saved.append(a) or True)
    sent = []
    monkeypatch.setattr(cr, "render_alert_email", lambda *_a, **_k: ("subj", "<p/>"))
    monkeypatch.setattr(cr, "_send_email", lambda *a, **_k: sent.append(a) or True)
    notified = []
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, detail: notified.append((lane, detail)))
    rc = cr._run_premarket(NOW, force=True)
    return rc, sent, notified, saved


def test_premarket_unreadable_manual_stops_fails_lane_but_still_sends_alerts(monkeypatch):
    alert = {"kind": "stop_breach", "ticker": "AAPL"}
    rc, sent, notified, saved = _run_pm(monkeypatch, {
        "alerts": [alert], "built_at": "x", "errors": ["manual stops could not be read"],
        "manual_stops_unreadable": True,
    })
    assert rc == 1
    assert "manual_stops_unreadable" in cr._LAST_LANE_FAILURE_DETAIL
    assert len(notified) == 1
    assert len(sent) == 1, "a computed breach must still be emailed"
    assert len(saved) == 1
    assert saved[0][0] == NOW.date().isoformat()
    assert saved[0][1] == cr._fingerprint([alert]), "dedup state = the alert that was sent"


def test_premarket_readable_manual_stops_is_green(monkeypatch):
    rc, sent, notified, _saved = _run_pm(monkeypatch, {
        "alerts": [], "built_at": "x", "errors": [], "manual_stops_unreadable": False,
    })
    assert rc == 0 and notified == []


def test_eod_lane_does_not_fail_on_unreadable_manual_stops(monkeypatch):
    """No EOD write depends on stops (reviewer 2026-10-30): rc stays 0."""
    from tests import test_cron_eod_write_failure_propagation as eod
    rc = eod._run(monkeypatch, compute_eod=lambda **_k: eod._payload(
        manual_stops_unreadable=True, errors=["manual stops could not be read"]))
    assert rc == 0
    assert cr._LAST_LANE_FAILURE_DETAIL is None


# ── app.py wiring (pin both ends: the call and the banner) ───────────────────

_APP = (_ROOT / "app.py").read_text(encoding="utf-8")


def test_app_build_sites_use_strict_read():
    tree = parse_file_cached(_ROOT / "app.py")
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in ("load_manual_stops", "load_manual_stops_or_none")]
    assert [c.func.attr for c in calls].count("load_manual_stops_or_none") == 2
    assert not [c for c in calls if c.func.attr == "load_manual_stops"]


def test_app_banner_text_exists_and_is_rendered_at_all_sites():
    assert "Your manual stops couldn't be read from the database" in _APP
    assert "are the automatic ones only. A manual stop that is tighter than the automatic " in _APP
    # the pre-C3 claim is false now that the held stop can be breached
    assert "moves down with the" not in _APP
    assert "rarely triggers on its own" not in _APP
    assert "check your broker's stop orders." in _APP
    assert len(re.findall(r"MANUAL_STOPS_UNREADABLE_BANNER\)", _APP)) == 3
    assert "st.error(MANUAL_STOPS_UNREADABLE_BANNER)" in _APP
    assert "st.warning(MANUAL_STOPS_UNREADABLE_BANNER)" in _APP
    assert "$" not in re.search(r"MANUAL_STOPS_UNREADABLE_BANNER = \((.*?)\n\)", _APP, re.S).group(1)


def test_app_publishes_none_and_stale_banner_renders_it():
    # producer end: both build sites publish None (3-state), never {}, on failure
    assert 'st.session_state["_manual_stops"] = None if _manual_stops_unreadable' in _APP
    assert 'st.session_state["_manual_stops"]      = _published_manual_stops' in _APP
    # consumer end: the shared stale banner renders the error on a published None
    m = re.search(r"def _render_portfolio_stale_banner\(.*?\ndef ", _APP, re.S)
    assert m and 'st.session_state["_manual_stops"] is None' in m.group(0)
    assert "st.error(MANUAL_STOPS_UNREADABLE_BANNER)" in m.group(0)
