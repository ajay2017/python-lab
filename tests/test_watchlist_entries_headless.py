"""Regression tests for the Watchlist "Ready to Enter" proactive email —
headless_alert_engine.compute_watchlist_entries + notify.render_watchlist_entries_email
+ their cron_runner._run_scan wiring.

Pure additive capability (D-A/D-B/D-C/D-D locked in the planner spec): no
change to watchlist_advisor.py's decision logic, no new gate/threshold. Tests
here mock the I/O boundary (db.*, _build_context, load_bundle,
build_watchlist_recommendation) and exercise this feature's own logic: the
held/scanner-go/protective-call exclusions, the day-over-day transition diff
(D-B), the offline contract (entries=None vs []), and the beta-gate
degradation disclosure (D-D). See tests/test_headless_alert_engine.py for the
same mocking conventions this file follows.
"""
import importlib
import sys
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

# Same collection-safety shim as test_headless_alert_engine.py — see that
# file's own comment for why this is a real-import-first fallback, not a
# blind sys.modules stub.
for _mod in ("streamlit", "vaderSentiment", "vaderSentiment.vaderSentiment"):
    if _mod not in sys.modules:
        try:
            importlib.import_module(_mod)
        except ImportError:
            sys.modules[_mod] = MagicMock()

from stock_analyzer import headless_alert_engine as hae

pytestmark = pytest.mark.fast

TODAY = date(2026, 9, 10)
PRIOR_DAY = TODAY - timedelta(days=1)


# ── test helpers ────────────────────────────────────────────────────────────

def _wl_card(ticker, action="ENTER_NOW", score=70.0):
    return {
        "ticker": ticker, "action": action, "score": score, "price": 100.0,
        "entry_lo": 95.0, "entry_hi": 105.0, "stop": 90.0, "rr": 2.5,
        "summary": f"{ticker} summary",
    }


def _holdings_df(rows):
    cols = ["Ticker", "Shares", "Avg Cost ($)"]
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


_UNSET = object()  # distinguishes "caller didn't pass prior_df" from "caller explicitly wants None"


def _run_watchlist_entries(
    watchlist=("AAPL",), held_rows=None, cards=None, ctx=None,
    prior_df=_UNSET, exit_signals_df=None, scanner_go_tickers=None,
    watchlist_side_effect=None, holdings_side_effect=None,
):
    cards = cards if cards is not None else {t: _wl_card(t) for t in watchlist}
    ctx = ctx if ctx is not None else {
        "ok": True, "errors": [], "port_df": pd.DataFrame(), "spy_6mo": None,
        "port_risk": {"beta": 1.1},
    }
    prior_df = pd.DataFrame() if prior_df is _UNSET else prior_df
    exit_signals_df = exit_signals_df if exit_signals_df is not None else pd.DataFrame()

    def _fake_build_rec(ticker, data, portfolio_ctx=None):
        return cards[ticker]

    with patch("stock_analyzer.headless_alert_engine.db.load_watchlist_or_none",
               return_value=None if watchlist_side_effect else list(watchlist),
               side_effect=watchlist_side_effect), \
         patch("stock_analyzer.headless_alert_engine.db.load_holdings_or_none",
               return_value=None if holdings_side_effect else _holdings_df(held_rows),
               side_effect=holdings_side_effect), \
         patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.headless_alert_engine.fetch_risk_free_rate", return_value=0.045), \
         patch("stock_analyzer.headless_alert_engine.fetch_spy", return_value=None), \
         patch("stock_analyzer.headless_alert_engine.load_bundle",
               side_effect=lambda t, *a, **k: {"sector": "Technology"}), \
         patch("stock_analyzer.headless_alert_engine.build_watchlist_recommendation",
               side_effect=_fake_build_rec), \
         patch("stock_analyzer.headless_alert_engine.db.load_exit_signals",
               return_value=exit_signals_df), \
         patch("stock_analyzer.headless_alert_engine.db.save_recommendations",
               side_effect=lambda rows: {"attempted": len(rows), "saved": len(rows), "error": None}) as save_mock, \
         patch("stock_analyzer.headless_alert_engine.is_trading_day", return_value=True), \
         patch("stock_analyzer.headless_alert_engine.db.load_recommendations_or_none",
               return_value=prior_df):
        result = hae.compute_watchlist_entries(
            TODAY, scanner_go_tickers=scanner_go_tickers,
        )
        result["_save_mock"] = save_mock
        return result


def _prior_enter_now_df(tickers):
    return pd.DataFrame([
        {"ticker": t, "rec_type": "enter_now", "rec_date": PRIOR_DAY.isoformat()}
        for t in tickers
    ])


def _today_signals_df(tickers, signal_type="EXIT"):
    return pd.DataFrame([
        {"ticker": t, "signal_date": TODAY.isoformat(), "signal_type": signal_type}
        for t in tickers
    ])


# ── offline contract ─────────────────────────────────────────────────────────

def test_watchlist_read_failure_returns_none_entries_and_db_unavailable_reason():
    result = _run_watchlist_entries(watchlist_side_effect=RuntimeError("boom"))
    assert result["entries"] is None
    assert result["reason"] == "db_unavailable"


def test_holdings_read_failure_returns_none_entries_and_db_unavailable_reason():
    result = _run_watchlist_entries(holdings_side_effect=RuntimeError("boom"))
    assert result["entries"] is None
    assert result["reason"] == "db_unavailable"


def test_empty_watchlist_returns_empty_entries_not_none():
    result = _run_watchlist_entries(watchlist=())
    assert result["entries"] == []
    assert result["reason"] is None


# ── D-C: held-ticker exclusion (announce scope) vs. capture scope ──────────
# Coordinator decision 2026-09-10: capture (grading coverage) and announce
# (what gets emailed) are DIFFERENT scopes, matching the interactive page's
# own behavior (it captures a held ENTER_NOW with already_held=True for
# grading, and only turns it into a render-time caution, never a capture-time
# exclusion). A held ticker must therefore be captured — but never emailed.

def test_held_ticker_excluded_from_entries_but_still_captured():
    result = _run_watchlist_entries(
        watchlist=["AAPL"],
        held_rows=[{"Ticker": "AAPL", "Shares": 10, "Avg Cost ($)": 50.0}],
        cards={"AAPL": _wl_card("AAPL")},
    )
    assert result["entries"] == []
    save_mock = result["_save_mock"]
    assert save_mock.called, (
        "a held ticker's ENTER_NOW must still be captured for grading "
        "coverage (matching the interactive page's own D1 capture scope) "
        "even though it is never emailed"
    )
    saved_rows = save_mock.call_args[0][0]
    assert saved_rows[0]["ticker"] == "AAPL"
    assert saved_rows[0]["already_held"] is True


def test_capture_scope_is_raw_and_wider_than_the_email_eligible_scope():
    """Pins the exact capture-vs-announce distinction: with one held ticker
    and one not-held ticker both ENTER_NOW, BOTH get captured, but only the
    not-held one is ever emailed."""
    result = _run_watchlist_entries(
        watchlist=["AAPL", "NVDA"],
        held_rows=[{"Ticker": "AAPL", "Shares": 10, "Avg Cost ($)": 50.0}],
        cards={"AAPL": _wl_card("AAPL"), "NVDA": _wl_card("NVDA")},
    )
    assert [c["ticker"] for c in result["entries"]] == ["NVDA"]
    save_mock = result["_save_mock"]
    assert save_mock.called
    saved_tickers = {r["ticker"]: r["already_held"] for r in save_mock.call_args[0][0]}
    assert saved_tickers == {"AAPL": True, "NVDA": False}


# ── D-B: transition-only cadence ─────────────────────────────────────────────

def test_second_consecutive_run_same_set_is_silent_but_still_captured():
    # Run 1: nothing recorded yesterday -> fires.
    r1 = _run_watchlist_entries(watchlist=["NVDA"], prior_df=pd.DataFrame())
    assert [c["ticker"] for c in r1["entries"]] == ["NVDA"]
    assert r1["_save_mock"].called

    # Run 2 (same day-over-day set): NVDA now shows up in "yesterday's"
    # recorded enter_now rows -> silent, but the row is still written again
    # (idempotent upsert) so grading coverage doesn't gap.
    r2 = _run_watchlist_entries(watchlist=["NVDA"], prior_df=_prior_enter_now_df(["NVDA"]))
    assert r2["entries"] == []
    assert r2["_save_mock"].called


def test_ticker_drops_out_then_returns_fires_on_run_1_and_3_silent_on_2():
    # Run 1: ENTER_NOW, nothing recorded yesterday -> fires.
    r1 = _run_watchlist_entries(
        watchlist=["MSFT"], cards={"MSFT": _wl_card("MSFT", action="ENTER_NOW")},
        prior_df=pd.DataFrame(),
    )
    assert [c["ticker"] for c in r1["entries"]] == ["MSFT"]

    # Run 2: drops out of ENTER_NOW entirely (e.g. NEAR_ENTRY) -> no card
    # qualifies at all, so no entry and no row written for it that day.
    r2 = _run_watchlist_entries(
        watchlist=["MSFT"], cards={"MSFT": _wl_card("MSFT", action="NEAR_ENTRY")},
        prior_df=_prior_enter_now_df(["MSFT"]),
    )
    assert r2["entries"] == []

    # Run 3: back to ENTER_NOW. "Yesterday" (day 2) recorded nothing for MSFT
    # (it didn't qualify that day), so the transition fires again.
    r3 = _run_watchlist_entries(
        watchlist=["MSFT"], cards={"MSFT": _wl_card("MSFT", action="ENTER_NOW")},
        prior_df=pd.DataFrame(),
    )
    assert [c["ticker"] for c in r3["entries"]] == ["MSFT"]


def test_prior_day_lookup_failure_suppresses_all_entries():
    """Can't verify the transition -> recommend nothing rather than risk
    re-announcing an already-seen name (CLAUDE.md operating posture)."""
    result = _run_watchlist_entries(watchlist=["NVDA"], prior_df=None)
    assert result["entries"] == []
    assert any("prior-day enter_now lookup unavailable" in e for e in result["errors"])
    # The full qualifying set is still captured today regardless.
    assert result["_save_mock"].called


# ── dedup with scanner "Go" set and protective-call tickers ─────────────────

def test_ticker_in_scanner_go_set_is_excluded():
    result = _run_watchlist_entries(watchlist=["NVDA"], scanner_go_tickers={"NVDA"})
    assert result["entries"] == []


def test_ticker_under_active_protective_call_is_excluded():
    result = _run_watchlist_entries(
        watchlist=["NVDA"], exit_signals_df=_today_signals_df(["NVDA"], "EXIT"),
    )
    assert result["entries"] == []


def test_ticker_under_risk_off_call_is_excluded():
    result = _run_watchlist_entries(
        watchlist=["NVDA"], exit_signals_df=_today_signals_df(["NVDA"], "RISK_OFF"),
    )
    assert result["entries"] == []


def test_unrelated_protective_call_does_not_exclude_other_tickers():
    result = _run_watchlist_entries(
        watchlist=["NVDA", "AMD"],
        cards={"NVDA": _wl_card("NVDA"), "AMD": _wl_card("AMD")},
        exit_signals_df=_today_signals_df(["NVDA"], "EXIT"),
    )
    assert [c["ticker"] for c in result["entries"]] == ["AMD"]


# ── D-D: gate degradation disclosure ─────────────────────────────────────────

def test_gate_degraded_when_beta_context_unbuildable_but_entry_still_included():
    ctx = {"ok": False, "errors": ["no holdings"]}
    result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
    assert result["gate_degraded"] is True
    assert [c["ticker"] for c in result["entries"]] == ["NVDA"]


def test_gate_not_degraded_when_beta_available():
    ctx = {"ok": True, "errors": [], "port_df": pd.DataFrame(), "spy_6mo": None,
           "port_risk": {"beta": 1.2}}
    result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
    assert result["gate_degraded"] is False


def test_gate_degraded_when_ctx_ok_but_beta_missing():
    ctx = {"ok": True, "errors": [], "port_df": pd.DataFrame(), "spy_6mo": None,
           "port_risk": {"beta": None}}
    result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
    assert result["gate_degraded"] is True
    assert [c["ticker"] for c in result["entries"]] == ["NVDA"]


# ── render_watchlist_entries_email ───────────────────────────────────────────

def test_render_watchlist_entries_email_subject_lists_tickers():
    from stock_analyzer.notify import render_watchlist_entries_email
    entries = [_wl_card("NVDA"), _wl_card("AMD")]
    subject, html = render_watchlist_entries_email(entries, built_at="2026-09-10T09:45:00")
    assert "NVDA" in subject and "AMD" in subject
    assert "NVDA" in html and "AMD" in html


def test_render_watchlist_entries_email_bolds_markdown_in_summary_not_literal():
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["summary"] = "Score 70/100 · **all conditions align** for opening a position."
    subject, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "**" not in html, "literal markdown bold leaked into raw HTML"
    assert "<strong>all conditions align</strong>" in html


def test_render_watchlist_entries_email_escapes_html_metacharacters():
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["summary"] = "<script>alert(1)</script>"
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_watchlist_entries_email_gate_degraded_disclosure_present():
    from stock_analyzer.notify import render_watchlist_entries_email
    _, html = render_watchlist_entries_email(
        [_wl_card("NVDA")], built_at="2026-09-10T09:45:00", gate_degraded=True,
    )
    assert "Portfolio-beta fit check unavailable" in html


def test_render_watchlist_entries_email_no_disclosure_when_not_degraded():
    from stock_analyzer.notify import render_watchlist_entries_email
    _, html = render_watchlist_entries_email(
        [_wl_card("NVDA")], built_at="2026-09-10T09:45:00", gate_degraded=False,
    )
    assert "Portfolio-beta fit check unavailable" not in html


# ── cron_runner._run_scan wiring ─────────────────────────────────────────────

def _run_scan_to_watchlist_entries(monkeypatch, *, wle_payload=None, wle_side_effect=None,
                                    send_result=True, save_state_ok=True, alert_state=None,
                                    now_et=None, force=True):
    import cron_runner as cr
    from datetime import date as _date

    monkeypatch.setattr(cr, "is_trading_day", lambda _d: True)

    from stock_analyzer import reference_data as rd
    monkeypatch.setattr(
        rd, "resolve_universe_or_none",
        lambda name: ({"Tech": ["AAPL"]}, _date(2026, 9, 1), None),
    )
    from stock_analyzer import scanner as real_scanner
    monkeypatch.setattr(
        real_scanner, "scan_sectors",
        lambda *a, **k: pd.DataFrame({"Ticker": ["AAPL"], "Score": [70.0]}),
    )
    monkeypatch.setattr(cr.db, "save_scanner_cache", lambda *_a, **_k: True)
    monkeypatch.setattr(
        cr, "compute_morning_picks",
        lambda **_k: {"picks": [], "errors": [], "built_at": "2026-09-10T09:45:00",
                      "diag": {}, "book_drift": None, "macro_coverage": None, "grow": {}},
    )
    monkeypatch.setattr(cr.db, "load_exit_signals", lambda **_k: pd.DataFrame())
    if wle_side_effect is not None:
        monkeypatch.setattr(cr, "compute_watchlist_entries", wle_side_effect)
    else:
        monkeypatch.setattr(cr, "compute_watchlist_entries", lambda **_k: wle_payload)

    sent_emails = []
    monkeypatch.setattr(
        cr, "_send_email",
        lambda label, subj, html: sent_emails.append(label) or send_result,
    )
    monkeypatch.setattr(
        cr.db, "load_alert_state",
        lambda row: alert_state if row == cr._WATCHLIST_ENTRIES_ROW else None,
    )
    saved_state = []
    monkeypatch.setattr(
        cr.db, "save_alert_state",
        lambda *a, **k: saved_state.append((a, k)) or save_state_ok,
    )
    handled = []
    monkeypatch.setattr(
        cr, "_handle_db_unavailable",
        lambda lane, now_et, detail: handled.append((lane, detail)) or 1,
    )
    rc = cr._run_scan(now_et if now_et is not None else cr.datetime.now(cr._ET), force=force)
    return rc, sent_emails, saved_state, handled


def test_watchlist_entries_db_unavailable_routes_through_handle_db_unavailable(monkeypatch):
    wle_payload = {"entries": None, "reason": "db_unavailable", "errors": ["boom"],
                   "built_at": "x", "gate_degraded": False}
    rc, sent, saved, handled = _run_scan_to_watchlist_entries(monkeypatch, wle_payload=wle_payload)
    assert rc == 1
    assert handled and handled[0][0] == "watchlist-entries"
    assert "watchlist-entries" not in sent


def test_watchlist_entries_no_new_entries_sends_no_email(monkeypatch):
    wle_payload = {"entries": [], "reason": None, "errors": [], "built_at": "x",
                   "gate_degraded": False}
    rc, sent, saved, handled = _run_scan_to_watchlist_entries(monkeypatch, wle_payload=wle_payload)
    assert rc == 0
    assert sent == []
    assert saved == []


def test_watchlist_entries_real_send_saves_dedup_state(monkeypatch):
    entry = {"ticker": "NVDA", "score": 80.0, "entry_lo": 90.0, "entry_hi": 100.0,
             "stop": 85.0, "rr": 3.0, "summary": "NVDA ready"}
    wle_payload = {"entries": [entry], "reason": None, "errors": [],
                   "built_at": "2026-09-10T09:45:00", "gate_degraded": False}
    rc, sent, saved, handled = _run_scan_to_watchlist_entries(
        monkeypatch, wle_payload=wle_payload, send_result=True, save_state_ok=True,
    )
    assert rc == 0
    assert sent == ["watchlist-entries"]
    assert saved, "a real send must save dedup state (row=_WATCHLIST_ENTRIES_ROW)"


def test_watchlist_entries_send_failure_does_not_save_dedup_state(monkeypatch):
    """Retry-safety: a transient send failure must not be recorded as sent, so
    the later DST slot / next cron firing can retry — same pattern as
    _BUY_ROW/_INTRADAY_ROW above it in cron_runner.py."""
    entry = {"ticker": "NVDA", "score": 80.0, "entry_lo": 90.0, "entry_hi": 100.0,
             "stop": 85.0, "rr": 3.0, "summary": "NVDA ready"}
    wle_payload = {"entries": [entry], "reason": None, "errors": [],
                   "built_at": "2026-09-10T09:45:00", "gate_degraded": False}
    rc, sent, saved, handled = _run_scan_to_watchlist_entries(
        monkeypatch, wle_payload=wle_payload, send_result=False,
    )
    assert rc == 0
    assert sent == ["watchlist-entries"]
    assert saved == [], "a failed send must NOT save dedup state"


def test_watchlist_entries_dedup_suppresses_same_set_same_day(monkeypatch):
    """force=True (used by the other cron-lane tests here) deliberately BYPASSES
    dedup, same as the pre-existing _BUY_ROW/_INTRADAY_ROW pattern — so this is
    the one test in this section that must run with force=False and a pinned,
    post-open clock instead, matching test_cron_db_outage.py's own convention."""
    import cron_runner as cr

    entry = {"ticker": "NVDA", "score": 80.0, "entry_lo": 90.0, "entry_hi": 100.0,
             "stop": 85.0, "rr": 3.0, "summary": "NVDA ready"}
    wle_payload = {"entries": [entry], "reason": None, "errors": [],
                   "built_at": "2026-09-10T09:45:00", "gate_degraded": False}
    pinned_now = cr._ET.localize(__import__("datetime").datetime(2026, 9, 10, 9, 45))
    today_str = pinned_now.date().isoformat()
    import hashlib
    fp = hashlib.sha1(f"{today_str}|NVDA".encode("utf-8")).hexdigest()[:16]
    rc, sent, saved, handled = _run_scan_to_watchlist_entries(
        monkeypatch, wle_payload=wle_payload, now_et=pinned_now, force=False,
        alert_state={"last_emailed_date": today_str, "last_fingerprint": fp},
    )
    assert sent == [], "same set, same day, already emailed — must not resend"


def test_watchlist_entries_failure_never_aborts_the_scan_lane(monkeypatch):
    """Isolation: a fault computing watchlist entries must not affect the
    already-completed buy-list section (rc still 0, no exception)."""
    def _boom(**_k):
        raise RuntimeError("watchlist entries blew up")

    rc, sent, saved, handled = _run_scan_to_watchlist_entries(
        monkeypatch, wle_side_effect=_boom,
    )
    assert rc == 0
