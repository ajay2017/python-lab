"""#26 C3 review fixes: the held-stop lane flags in headless `_build_context` and the
premarket lane's handling of them (red heartbeat + failure email, computed alerts
still sent, dedup saved, footer lines in the email)."""
import datetime
from unittest.mock import patch

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import headless_alert_engine as hae

TODAY = datetime.date(2026, 10, 30)
GO_LIVE_DAY = datetime.datetime(2026, 11, 2, 8, 30)   # first Monday after the 10-31 go-live


def _hs(basis="full", fell_back=False, buy_day=False, label="Trailing Stop", note=""):
    return {"ticker": "X", "stop": 90.0, "basis": basis, "label": label, "binding": "entry",
            "note": note, "fell_back": fell_back, "buy_day": buy_day,
            "degraded": basis in ("partial", "prior_session", "unavailable")}


def _ctx(held_stops, trades):
    with patch.object(hae.db, "has_db", return_value=True), \
         patch.object(hae.db, "load_holdings_or_none",
                      return_value=pd.DataFrame({"Ticker": ["AAA", "BBB"][:len(held_stops)]})), \
         patch.object(hae.db, "load_trades_or_none", return_value=trades), \
         patch.object(hae.db, "load_manual_stops_or_none", return_value={}), \
         patch.object(hae, "fetch_risk_free_rate", return_value=0.045), \
         patch.object(hae, "fetch_spy", return_value=pd.DataFrame({"Close": [1.0, 2.0]})), \
         patch.object(hae, "_vix_level", return_value=16.0), \
         patch.object(hae, "load_bundle", return_value={"financials": {}}), \
         patch.object(hae, "_build_open_lots", return_value=[]), \
         patch.object(hae.exit_advisor, "material_add_window_days", return_value=None), \
         patch.object(hae._held_stop_wiring, "build_held_stops", return_value=held_stops), \
         patch.object(hae, "build_portfolio_df",
                      return_value=pd.DataFrame({"Ticker": ["AAA"], "Weight (%)": [10.0]})), \
         patch.object(hae, "compute_portfolio_risk_metrics", return_value={"beta": 1.1}), \
         patch.object(hae, "run_scenario", return_value={"port_impact_pct": -5.0}), \
         patch.object(hae, "assess_fragility", return_value={"severity": "elevated"}):
        return hae._build_context(TODAY)


GOOD = pd.DataFrame({"ticker": ["AAA"]})


def test_journal_unreadable_flags_and_gives_old_consumers_an_empty_frame_with_columns():
    r = _ctx({"AAA": _hs("prior_session")}, None)
    assert r["held_stops_failed"] == "trade journal could not be read"
    assert list(r["trades_df"].columns) == list(hae.db._TRADE_COLS) and r["trades_df"].empty


def test_every_name_prior_or_unavailable_on_a_readable_journal():
    r = _ctx({"AAA": _hs("prior_session"), "BBB": _hs("unavailable")}, GOOD)
    assert r["held_stops_failed"] == "no holding has a usable held stop"
    assert len(r["held_stop_notes"]) == 2


def test_one_full_basis_name_is_not_a_failure():
    r = _ctx({"AAA": _hs("prior_session"), "BBB": _hs("full")}, GOOD)
    assert r["held_stops_failed"] is None


def test_every_name_fell_back_to_the_bundle_fails():
    r = _ctx({"AAA": _hs("partial", fell_back=True), "BBB": _hs("partial", fell_back=True)}, GOOD)
    assert r["held_stops_failed"] and "6-month" in r["held_stops_failed"]


def test_buy_day_names_are_not_judged():
    assert _ctx({"AAA": _hs("buy_day", buy_day=True)}, GOOD)["held_stops_failed"] is None
    r = _ctx({"AAA": _hs("buy_day", buy_day=True), "BBB": _hs("prior_session")}, GOOD)
    assert r["held_stops_failed"] == "no holding has a usable held stop"
    assert len(r["held_stop_notes"]) == 1


# ── premarket lane ───────────────────────────────────────────────────────────
def _run_pm(monkeypatch, payload, now):
    monkeypatch.setattr(cr, "compute_protective_alerts", lambda **_k: payload)
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda **_k: None)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: {})
    saved, sent, renders, notified = [], [], [], []
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *a, **_k: saved.append(a) or True)

    def _render(*a, **k):
        renders.append(k)
        return ("subj", "<p/>")
    monkeypatch.setattr(cr, "render_alert_email", _render)
    monkeypatch.setattr(cr, "_send_email", lambda *a, **_k: sent.append(a) or True)
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, detail: notified.append((lane, detail)))
    rc = cr._run_premarket(now, force=True)
    return rc, sent, saved, renders, notified


def test_premarket_held_stops_failed_goes_red_but_still_sends_and_saves(monkeypatch):
    alert = {"kind": "stop_breach", "ticker": "AAPL"}
    payload = {"alerts": [alert], "built_at": "x", "errors": [],
               "held_stops_failed": "trade journal could not be read",
               "held_stop_notes": ["AAPL (Prior-close ATR): journal note"]}
    rc, sent, saved, renders, notified = _run_pm(monkeypatch, payload, GO_LIVE_DAY)
    assert rc == 1
    assert "held_stops_failed" in cr._LAST_LANE_FAILURE_DETAIL
    assert len(sent) == 1 and len(saved) == 1
    assert saved[0][1] == cr._fingerprint([alert])
    lines = renders[0]["footer_lines"]
    assert lines[0].startswith("Stops were recalculated")        # go-live line first
    assert lines[1] == "AAPL (Prior-close ATR): journal note"    # then the per-name notes


def test_premarket_no_failure_flag_is_green_and_footer_has_only_notes(monkeypatch):
    payload = {"alerts": [{"kind": "stop_breach", "ticker": "AAPL"}], "built_at": "x", "errors": [],
               "held_stops_failed": None, "held_stop_notes": ["AAPL (Stop Unavailable): n"]}
    rc, sent, saved, renders, notified = _run_pm(
        monkeypatch, payload, datetime.datetime(2026, 12, 1, 8, 30))
    assert rc == 0 and len(sent) == 1
    assert renders[0]["footer_lines"] == ["AAPL (Stop Unavailable): n"]   # window over: no go-live line
