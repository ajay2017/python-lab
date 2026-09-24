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
    prior_df=_UNSET, exit_signals_df=_UNSET, scanner_go_tickers=None,
    watchlist_side_effect=None, holdings_side_effect=None, bundle=None,
    exit_signals_side_effect=None,
):
    cards = cards if cards is not None else {t: _wl_card(t) for t in watchlist}
    ctx = ctx if ctx is not None else {
        "ok": True, "errors": [], "port_df": pd.DataFrame(), "spy_6mo": None,
        "port_risk": {"beta": 1.1},
    }
    prior_df = pd.DataFrame() if prior_df is _UNSET else prior_df
    # _UNSET (the default) means "genuinely no signals today" (empty frame) --
    # load_exit_signals_or_none()'s check-succeeded-clean case. Pass
    # exit_signals_df=None explicitly, or exit_signals_side_effect=<exception>,
    # to simulate the check-FAILED case (2026-09-24 app review, J1) --
    # load_exit_signals_or_none()'s own documented `None`-on-failure contract.
    exit_signals_df = pd.DataFrame() if exit_signals_df is _UNSET else exit_signals_df

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
               side_effect=lambda t, *a, **k: (
                   bundle if bundle is not None else {"sector": "Technology"}
               )), \
         patch("stock_analyzer.headless_alert_engine.build_watchlist_recommendation",
               side_effect=_fake_build_rec), \
         patch("stock_analyzer.headless_alert_engine.db.load_exit_signals_or_none",
               return_value=exit_signals_df, side_effect=exit_signals_side_effect), \
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


def test_cron_capture_persists_pillar_columns_from_the_bundle():
    """D8 regression, and specifically a SECOND-CALLER regression.

    `build_enter_now_rows` gained an optional bundles map so enter_now rows
    stop persisting NULL pillars. The app-side caller was updated first and the
    cron caller was missed — which made the fix nearly a no-op in production,
    because `save_recommendations` upserts ON CONFLICT DO NOTHING with no
    UPDATE path, so the first writer of a (ticker, rec_date, 'enter_now') key
    wins permanently, and THIS lane fires unattended every day while the app
    path needs a 📋 Watchlist visit.

    Asserting on what actually reaches save_recommendations (not on the helper's
    signature) is what makes this catch a forgotten caller rather than a
    forgotten parameter.

    Includes `headlines` (D24): the cron path computes an explicit
    `sentiment_available` boolean from it, so a bundle with sentiment scores
    but no headlines that produced them is a self-contradictory fixture, not
    a real one — it would (correctly) get its sentiment nulled as fabricated.
    """
    result = _run_watchlist_entries(
        watchlist=("AAPL",),
        bundle={"sector": "Technology", "t_score": 80.0, "bq_score": 61.0,
                "val_score": 44.0, "s_score": 55.0, "avg_sent": 0.1,
                "bq_available": True, "val_available": True,
                "headlines": [{"headline": "AAPL rallies", "score": 0.2}]},
    )
    rows = result["_save_mock"].call_args[0][0]
    assert len(rows) == 1
    r = rows[0]
    assert r["t_score"] == 80.0
    assert r["bq_score"] == 61.0
    assert r["val_score"] == 44.0
    assert r["s_score"] == 55.0
    assert r["avg_sent"] == 0.1


def test_cron_capture_preserves_the_legacy_f_score_fallback():
    """The cron path narrows the bundle to just the pillar keys before handing
    it on. That narrowing must preserve ABSENCE, not materialise missing keys
    as None — `dict.get("bq_score", <fallback>)` returns None for a
    present-but-None key instead of falling through, which would silently kill
    the legacy f_score fallback on this path only.
    """
    result = _run_watchlist_entries(
        watchlist=("AAPL",),
        bundle={"sector": "Technology", "f_score": 58.0},   # no bq_score at all
    )
    r = result["_save_mock"].call_args[0][0][0]
    assert r["bq_score"] == 58.0


def test_cron_capture_pillars_are_null_not_zero_when_bundle_lacks_them():
    # "Not captured" must stay distinguishable from a real 0 pillar score,
    # which is the most bearish reading there is.
    result = _run_watchlist_entries(watchlist=("AAPL",))   # sector-only bundle
    r = result["_save_mock"].call_args[0][0][0]
    for col in ("t_score", "bq_score", "val_score", "s_score", "avg_sent"):
        assert r[col] is None, col


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


# ── protective-check failure -- 2026-09-24 app review, J1 ────────────────────
# Previously used the unsafe db.load_exit_signals(), whose except branch
# returns the same empty DataFrame on a failed read as on a genuine zero-row
# day -- an outage silently emptied protective_tickers and let a flagged name
# through unexcluded. Now uses load_exit_signals_or_none(); a None return
# (or a raised exception) must suppress ALL entries, matching the
# already-established prior_tickers/D-B "can't verify, so suppress" pattern
# a few lines below in the same function -- never silently proceed as if
# nothing were under a protective call.

def test_protective_check_returning_none_suppresses_all_entries():
    """load_exit_signals_or_none() returning None (its documented failure
    sentinel) must suppress the whole email-eligible set for today -- the
    same posture as a failed prior_tickers lookup, not a soft degradation
    like the beta gate."""
    result = _run_watchlist_entries(watchlist=["NVDA"], exit_signals_df=None)
    assert result["entries"] == []
    assert any(
        "cannot verify protective-call status" in e for e in result["errors"]
    )


def test_protective_check_raising_suppresses_all_entries():
    result = _run_watchlist_entries(
        watchlist=["NVDA"], exit_signals_side_effect=RuntimeError("supabase unreachable"),
    )
    assert result["entries"] == []
    assert any(
        "cannot verify protective-call status" in e for e in result["errors"]
    )


def test_protective_check_failure_does_not_abort_the_capture_step():
    """The capture (rec-log grading baseline) reads `qualifying`, not
    `eligible` -- it must still write today's ENTER_NOW baseline even when
    the protective check fails and the email-eligible set is suppressed."""
    result = _run_watchlist_entries(watchlist=["NVDA"], exit_signals_df=None)
    assert result["entries"] == []
    assert result["_save_mock"].called


def test_protective_check_failure_does_not_falsely_suppress_when_signals_are_genuinely_clean():
    """Regression guard on the sentinel logic itself: a genuinely clean day
    (empty DataFrame, not None) must NOT be misread as a failure."""
    result = _run_watchlist_entries(watchlist=["NVDA"], exit_signals_df=pd.DataFrame())
    assert [c["ticker"] for c in result["entries"]] == ["NVDA"]
    assert not any(
        "cannot verify protective-call status" in e for e in result["errors"]
    )


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


# ── sizing/economics parity fields (2026-09-22) ─────────────────────────────
# The "Ready to Enter" email showed ticker/score/entry-zone/R:R/stop/prose
# only, with none of the sizing or dollar-risk detail the interactive page
# has carried since F-249/F-272. Attached ONLY to the final email-eligible
# list, best-effort — a failure must leave the fields absent, never abort
# the run or fabricate a number.

def _ctx_with_portfolio_value(pv, beta=1.1):
    port_df = pd.DataFrame({"Ticker": ["MSFT"], "Market Value": [pv], "Price": [300.0]})
    return {"ok": True, "errors": [], "port_df": port_df, "spy_6mo": None,
            "port_risk": {"beta": beta}}


def test_emitted_entries_carry_sizing_economics_and_capital_fields():
    ctx = _ctx_with_portfolio_value(100_000.0)
    with patch("stock_analyzer.headless_alert_engine.db.load_account_cash", return_value=None):
        result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
    assert [c["ticker"] for c in result["entries"]] == ["NVDA"]
    entry = result["entries"][0]
    assert "sizing" in entry
    assert "economics" in entry
    assert "net_capital" in entry
    assert "capital_basis" in entry
    assert entry["economics"]["state"] == "ok"
    assert entry["sizing"].get("shares")
    assert entry["capital_basis"] == "unlevered"
    assert entry["net_capital"] is None


def test_sizing_fields_show_portfolio_unknown_marker_when_portfolio_value_zero():
    """The default test fixture's ctx carries an empty port_df -> portfolio
    value 0.0 -> _position_size_for_render's own 'portfolio' no-size marker
    (F-261 cold-path posture) rather than a fabricated size."""
    result = _run_watchlist_entries(watchlist=["NVDA"])
    entry = result["entries"][0]
    assert entry["sizing"].get("portfolio_unknown") is True
    assert "shares" not in entry["sizing"]


def test_sizing_economics_failure_leaves_fields_absent_but_entries_still_returned():
    ctx = _ctx_with_portfolio_value(100_000.0)
    with patch("stock_analyzer.headless_alert_engine._position_size_for_render",
               side_effect=RuntimeError("boom")):
        result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
    assert [c["ticker"] for c in result["entries"]] == ["NVDA"]
    entry = result["entries"][0]
    assert "sizing" not in entry
    assert "economics" not in entry
    assert any("sizing/economics failed" in e for e in result["errors"])


def test_account_cash_lookup_failure_falls_back_to_unknown_basis_not_unlevered():
    """A FAILED net-capital check must not be indistinguishable from a
    verified 'no leverage' finding — capital_basis == 'unknown' (not
    'unlevered') so a downstream capital_equivalent_risk call correctly
    reads it as unknown rather than asserting a state never checked."""
    ctx = _ctx_with_portfolio_value(100_000.0)
    with patch("stock_analyzer.headless_alert_engine.db.load_account_cash",
               side_effect=RuntimeError("boom")):
        result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
    entry = result["entries"][0]
    assert entry["capital_basis"] == "unknown"
    assert entry["net_capital"] is None


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


def test_render_watchlist_entries_email_shows_deterioration_warning_when_present():
    """2026-09-16 ENVA follow-on — the email carries the same warn-only
    disclosure the app page shows, escaped for the raw-HTML email context."""
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["deterioration_warning"] = (
        "📉 NVDA's own recent price action shows a broken trend / deep "
        "drawdown — down 22.0% from its ~3-month high and recently below "
        "its 50-day trend line. This describes the stock's chart, not a "
        "position (you don't own it yet); the composite still rates it "
        "ENTER NOW. Shown so you enter with eyes open. Awareness only — "
        "doesn't change this recommendation."
    )
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "broken trend / deep" in html
    assert "down 22.0%" in html


def test_render_watchlist_entries_email_no_deterioration_warning_when_absent():
    """No `deterioration_warning` key at all (every pre-existing card shape)
    must not render an empty div or raise."""
    from stock_analyzer.notify import render_watchlist_entries_email
    _, html = render_watchlist_entries_email([_wl_card("NVDA")], built_at="2026-09-10T09:45:00")
    assert "own recent price action" not in html


def test_render_watchlist_entries_email_escapes_deterioration_warning_metacharacters():
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["deterioration_warning"] = "<script>alert(1)</script>"
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ── two-tier header (2026-09-22) ─────────────────────────────────────────────

def test_render_watchlist_entries_email_caution_tier_gets_amber_header_and_text():
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["deterioration_warning"] = "chart looks broken"
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "#f59e0b" in html
    assert "WITH CAUTION" in html


def test_render_watchlist_entries_email_clean_tier_gets_green_header_no_caution_text():
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "#22c55e" in html
    assert "WITH CAUTION" not in html


# ── economics line (2026-09-22) ──────────────────────────────────────────────

def test_render_watchlist_entries_email_shows_economics_line_hand_checked():
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["economics"] = {
        "state": "ok", "risk_per_share": 8.88, "risk_pct": 4.39,
        "gain_per_share": 19.53, "target": 221.74, "breakeven_pct": 31.25,
    }
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "risk $8.88/sh" in html
    assert "target $221.74" in html
    # ceil, not round: the true 31.25% bar must never render as ">31%".
    assert "needs >32% win rate to break even" in html


def test_render_watchlist_entries_email_breakeven_never_understates_the_bar():
    """A breakeven that rounds DOWN under `.0f` must still render a bar the
    reader can trust: ">N%" has to be >= the real requirement, never below it."""
    from stock_analyzer.notify import render_watchlist_entries_email
    import re as _re
    for be, expected in ((32.258, 33), (31.25, 32), (30.0, 30), (33.7, 34)):
        card = _wl_card("NVDA")
        card["economics"] = {
            "state": "ok", "risk_per_share": 2.59, "risk_pct": 7.5,
            "gain_per_share": 5.44, "target": 39.94, "breakeven_pct": be,
        }
        _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
        m = _re.search(r"needs >(\d+)% win rate", html)
        assert m is not None, "breakeven line missing"
        shown = int(m.group(1))
        assert shown == expected
        assert shown >= be - 1e-9, f"understated the breakeven bar: {shown} < {be}"


def test_render_watchlist_entries_email_no_economics_line_when_unpriced():
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["economics"] = {"state": "unpriced", "risk_per_share": None, "risk_pct": None,
                          "gain_per_share": None, "target": None, "breakeven_pct": None}
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "break even" not in html


def test_render_watchlist_entries_email_never_uses_forecast_language():
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["economics"] = {
        "state": "ok", "risk_per_share": 8.88, "risk_pct": 4.39,
        "gain_per_share": 19.53, "target": 221.74, "breakeven_pct": 31.25,
    }
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    lowered = html.lower()
    assert "confidence" not in lowered
    assert "probability" not in lowered
    assert "odds" not in lowered


# ── sizing block (2026-09-22, F-272 email parity) ────────────────────────────

def test_render_watchlist_entries_email_sizing_block_shows_shares_and_capital_basis():
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["sizing"] = {"shares": 10, "total_cost": 1000.0, "portfolio_value": 40000.0,
                       "sizing_version": 3}
    card["economics"] = {
        "state": "ok", "risk_per_share": 8.88, "risk_pct": 4.39,
        "gain_per_share": 19.53, "target": 221.74, "breakeven_pct": 31.25,
    }
    card["net_capital"] = 20000.0
    card["capital_basis"] = "levered"
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "10 shares" in html
    assert "Suggested size" in html
    assert "net capital" in html


def test_render_watchlist_entries_email_portfolio_unknown_shows_reason_not_shares():
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["sizing"] = {"portfolio_unknown": True, "sizing_version": 3}
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "Position sizing unavailable" in html
    assert "Suggested size" not in html


def test_render_watchlist_entries_email_stop_infeasible_renders_reason_via_cap_note():
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["sizing"] = {"stop_infeasible": True, "stop_at": 95.0,
                       "portfolio_value": 40000.0, "sizing_version": 3}
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "No size suggested" in html
    assert "95.00" in html
    assert "Suggested size" not in html


def test_render_watchlist_entries_email_capital_infeasible_margin_called_still_gives_a_reason():
    """net_capital <= 0 leaves `one_share_capital_pct` None, so the guarded
    cap-note arm cannot fire. Without a fallback the card renders a header and
    an economics line above a SILENTLY EMPTY sizing slot — on the one day the
    account is margin-called. The reason must always be stated."""
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["sizing"] = {"capital_infeasible": True, "one_share_capital_pct": None,
                       "net_capital": -500.0, "portfolio_value": 40000.0,
                       "sizing_version": 3}
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "No size suggested" in html
    assert "at or below zero" in html
    assert "Suggested size" not in html


def test_render_watchlist_entries_email_stale_capital_basis_discloses_instead_of_silence():
    """A stale/unresolvable account-cash figure must SAY so. Silence here
    would read as "unlevered" on the surface where that assumption is most
    expensive to get wrong — the owner trades on ~3x margin."""
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["sizing"] = {"shares": 10, "total_cost": 1000.0, "portfolio_value": 40000.0,
                       "sizing_version": 3}
    card["economics"] = {
        "state": "ok", "risk_per_share": 8.88, "risk_pct": 4.39,
        "gain_per_share": 19.53, "target": 221.74, "breakeven_pct": 31.25,
    }
    card["net_capital"] = None
    card["capital_basis"] = "stale"
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "10 shares" in html
    assert "missing or too old" in html
    # Must never assert the un-checked state as "no leverage".
    assert "of your net capital (vs" not in html


def test_render_watchlist_entries_email_no_sizing_block_when_sizing_absent():
    """Every pre-existing card shape (no `sizing` key at all) must not
    render an empty block or raise."""
    from stock_analyzer.notify import render_watchlist_entries_email
    _, html = render_watchlist_entries_email([_wl_card("NVDA")], built_at="2026-09-10T09:45:00")
    assert "Suggested size" not in html
    assert "Position sizing unavailable" not in html


def test_render_watchlist_entries_email_full_fields_no_markdown_leak():
    """Combines summary markdown + sizing + economics + capital fields — the
    literal-** leak class must not regress once these new fields land beside
    the existing markdown-bearing summary field."""
    from stock_analyzer.notify import render_watchlist_entries_email
    card = _wl_card("NVDA")
    card["summary"] = "Score 70/100 · **all conditions align** for opening a position."
    card["sizing"] = {"shares": 10, "total_cost": 1000.0, "portfolio_value": 40000.0,
                       "sizing_version": 3}
    card["economics"] = {
        "state": "ok", "risk_per_share": 8.88, "risk_pct": 4.39,
        "gain_per_share": 19.53, "target": 221.74, "breakeven_pct": 31.25,
    }
    card["net_capital"] = 20000.0
    card["capital_basis"] = "levered"
    _, html = render_watchlist_entries_email([card], built_at="2026-09-10T09:45:00")
    assert "**" not in html
    assert "<strong>all conditions align</strong>" in html


# ── footer calibration disclosure (2026-09-22) ───────────────────────────────

def test_render_watchlist_entries_email_footer_has_calibration_disclosure():
    from stock_analyzer.notify import render_watchlist_entries_email
    _, html = render_watchlist_entries_email([_wl_card("NVDA")], built_at="2026-09-10T09:45:00")
    assert "Composite clears a bar" in html
    assert "does not rank which names outperform" in html
    assert "239" not in html   # never hardcode the sample count — it rots


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
