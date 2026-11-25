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

_NETWORK_CALLS: list = []


@pytest.fixture(autouse=True)
def _count_network_reads(monkeypatch):
    """Every run in this file must be network-free: a live index / macro read
    here would be a real fetch. They are recorded (and fail) so the helper can
    assert none happened -- `_read_market_context` swallows the raise, so a
    bare raise alone would hide a leak."""
    _NETWORK_CALLS.clear()

    def _boom(*a, **k):
        _NETWORK_CALLS.append("net")
        raise RuntimeError("network read in a unit test")
    monkeypatch.setattr("stock_analyzer.data.fetch_market_indices", _boom)
    monkeypatch.setattr("stock_analyzer.macro_calendar.build_macro_calendar", _boom)


def _wl_card(ticker, action="ENTER_NOW", score=70.0):
    return {
        "ticker": ticker, "action": action, "score": score, "price": 100.0,
        "entry_lo": 95.0, "entry_hi": 105.0, "stop": 90.0, "rr": 2.5,
        "summary": f"{ticker} summary",
    }


def _holdings_df(rows):
    cols = ["Ticker", "Shares", "Avg Cost ($)"]
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


def _priced_frame():
    """A non-empty priced book frame: a ctx that is `ok` must carry one (an ok ctx
    with an empty frame reads as fit UNKNOWN since entry-fail-closed C2a)."""
    return pd.DataFrame([{"Ticker": "MSFT", "Sector": "Technology",
                          "Weight (%)": 10.0, "Market Value": 1000.0, "Price": 300.0}])


_UNSET = object()  # distinguishes "caller didn't pass prior_df" from "caller explicitly wants None"


def _run_watchlist_entries(
    watchlist=("AAPL",), held_rows=None, cards=None, ctx=None,
    prior_df=_UNSET, exit_signals_df=_UNSET, scanner_go_tickers=None,
    watchlist_side_effect=None, holdings_side_effect=None, bundle=None,
    exit_signals_side_effect=None, trades_df=_UNSET, trades_side_effect=None,
    entry_context=None, ctx_log=None, load_gate_holds=_UNSET,
    load_bundle_fn=None,
):
    cards = cards if cards is not None else {t: _wl_card(t) for t in watchlist}
    ctx = ctx if ctx is not None else {
        "ok": True, "errors": [], "port_df": _priced_frame(), "spy_6mo": None,
        "port_risk": {"beta": 1.1},
    }
    prior_df = pd.DataFrame() if prior_df is _UNSET else prior_df
    # _UNSET (the default) means "genuinely no signals today" (empty frame) --
    # load_exit_signals_or_none()'s check-succeeded-clean case. Pass
    # exit_signals_df=None explicitly, or exit_signals_side_effect=<exception>,
    # to simulate the check-FAILED case (2026-09-24 app review, J1) --
    # load_exit_signals_or_none()'s own documented `None`-on-failure contract.
    exit_signals_df = pd.DataFrame() if exit_signals_df is _UNSET else exit_signals_df
    # Readable, empty trade log by default. Pass trades_df=None (or a
    # trades_side_effect) for the check-FAILED case, which suppresses all
    # emailed entries (sold-today exclusion, 2026-10-11 audit follow-up).
    trades_df = pd.DataFrame() if trades_df is _UNSET else trades_df

    def _fake_build_rec(ticker, data, portfolio_ctx=None, entry_ctx=None):
        # entry_ctx is the chunk-5 kwarg; recorded so tests can assert on it.
        if ctx_log is not None:
            ctx_log.append((ticker, entry_ctx))
        return cards[ticker]

    # No live reads in tests: a caller passing no entry_context gets this
    # deterministic flat, available context from the fallback.
    def _fake_live_ctx(today, port_df):
        return {"market_context": {"tone": "flat", "sp500_pct": -0.2,
                                   "tone_available": True},
                "macro_events": []}

    _gate_holds_default = {} if load_gate_holds is _UNSET else load_gate_holds

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
               side_effect=load_bundle_fn or (lambda t, *a, **k: (
                   bundle if bundle is not None else {"sector": "Technology"}
               ))), \
         patch("stock_analyzer.headless_alert_engine.build_watchlist_recommendation",
               side_effect=_fake_build_rec), \
         patch("stock_analyzer.headless_alert_engine.db.load_exit_signals_or_none",
               return_value=exit_signals_df, side_effect=exit_signals_side_effect), \
         patch("stock_analyzer.headless_alert_engine.db.save_recommendations",
               side_effect=lambda rows: {"attempted": len(rows), "saved": len(rows), "error": None}) as save_mock, \
         patch("stock_analyzer.headless_alert_engine.is_trading_day", return_value=True), \
         patch("stock_analyzer.headless_alert_engine._live_entry_context",
               side_effect=_fake_live_ctx), \
         patch("stock_analyzer.headless_alert_engine.db.load_gate_holds_on_day_or_none",
               return_value=_gate_holds_default), \
         patch("stock_analyzer.headless_alert_engine.db.load_trades_or_none",
               return_value=trades_df, side_effect=trades_side_effect), \
         patch("stock_analyzer.headless_alert_engine.db.load_recommendations_or_none",
               return_value=prior_df):
        result = hae.compute_watchlist_entries(
            TODAY, scanner_go_tickers=scanner_go_tickers,
            **({"entry_context": entry_context} if entry_context is not None else {}),
        )
        result["_save_mock"] = save_mock
        assert _NETWORK_CALLS == [], "compute_watchlist_entries made a live read"
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


def _cron_capture_t_score(**flags):
    # build_watchlist_recommendation is stubbed (see _run_watchlist_entries), so
    # an ENTER_NOW card reaches the capture even for a t-unavailable bundle --
    # this pins the narrowed `_entry` carrying t_available into the writer
    # (the D24 trap), independent of the upstream withhold.
    result = _run_watchlist_entries(
        watchlist=("AAPL",),
        bundle={"sector": "Technology", "t_score": 50.0, "bq_score": 61.0,
                "val_score": 44.0, "bq_available": True, "val_available": True,
                "headlines": [{"headline": "x", "score": 0.2}], **flags},
    )
    rows = result["_save_mock"].call_args[0][0]
    assert len(rows) == 1
    return rows[0]["t_score"]


def test_cron_capture_nulls_t_score_when_t_available_false():
    assert _cron_capture_t_score(t_available=False) is None


def test_cron_capture_keeps_t_score_when_t_available_true_or_absent():
    assert _cron_capture_t_score(t_available=True) == 50.0
    assert _cron_capture_t_score() == 50.0


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
    ctx = {"ok": True, "errors": [], "port_df": _priced_frame(), "spy_6mo": None,
           "port_risk": {"beta": 1.2}}
    result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
    assert result["gate_degraded"] is False


def test_gate_degraded_when_ctx_ok_but_beta_missing():
    ctx = {"ok": True, "errors": [], "port_df": pd.DataFrame(), "spy_6mo": None,
           "port_risk": {"beta": None}}
    result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
    assert result["gate_degraded"] is True
    assert [c["ticker"] for c in result["entries"]] == ["NVDA"]


# ── CronSectorClaim: which fit leg actually ran (docs/plans/entry-fail-closed.md C0) ──

@pytest.mark.parametrize("reason", ["db_unavailable", "no_bundles", "empty_port_df", None])
def test_ctx_not_ok_reports_neither_fit_leg_read_and_email_does_not_claim_sector_ran(reason):
    """Only `no_holdings` reads as a checked (empty) book; every other not-ok ctx
    is "holdings couldn't be loaded or priced" (C0 reviewer note 2)."""
    from stock_analyzer.notify import render_watchlist_entries_email
    ctx = {"ok": False, "errors": ["boom"]}
    if reason is not None:
        ctx["reason"] = reason
    result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
    assert result["gate_degraded"] is True
    assert result["fit_read"] is False
    assert result["beta_read"] is False
    assert result["fit_state"] == {"state": "unknown", "unpriced_sectors": None}
    _, html = render_watchlist_entries_email(
        [_wl_card("NVDA")], built_at=result["built_at"],
        gate_degraded=result["gate_degraded"],
        fit_read=result["fit_read"], beta_read=result["beta_read"],
    )
    assert "still applied" not in html
    assert "Portfolio fit checks unavailable" in html
    assert "couldn't be loaded or priced for this run" in html
    assert "Portfolio-beta fit check unavailable" not in html


def test_ctx_ok_with_an_empty_frame_is_fit_unknown_not_read():
    """C0 reviewer note 1: `ok: True` with no usable frame is NOT a read book."""
    for frame in (pd.DataFrame(), None):
        ctx = {"ok": True, "errors": [], "port_df": frame, "spy_6mo": None,
               "port_risk": {"beta": 1.2}}
        result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
        assert result["fit_read"] is False
        assert result["fit_state"]["state"] == "unknown"
        assert result["gate_degraded"] is True


def test_empty_book_is_read_not_degraded_and_email_has_no_fit_box():
    """reason "no_holdings" = the book was read and is empty: a sector weight
    of 0.0 is true, so the email must not say the holdings couldn't be loaded."""
    from stock_analyzer.notify import render_watchlist_entries_email
    ctx = {"ok": False, "reason": "no_holdings", "errors": ["no holdings"]}
    result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
    assert result["gate_degraded"] is False
    assert result["fit_read"] is True
    assert result["beta_read"] is True      # nothing to compute for an empty book
    assert result["fit_state"] == {"state": "flat", "unpriced_sectors": None}
    assert [c["ticker"] for c in result["entries"]] == ["NVDA"]
    _, html = render_watchlist_entries_email(
        result["entries"], built_at=result["built_at"],
        gate_degraded=result["gate_degraded"],
        fit_read=result["fit_read"], beta_read=result["beta_read"],
    )
    assert "Portfolio fit checks unavailable" not in html
    assert "Portfolio-beta fit check unavailable" not in html


def test_ctx_ok_beta_none_reports_fit_read_but_not_beta_and_keeps_beta_box():
    from stock_analyzer.notify import render_watchlist_entries_email
    ctx = {"ok": True, "errors": [], "port_df": _priced_frame(), "spy_6mo": None,
           "port_risk": {"beta": None}}
    result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
    assert result["gate_degraded"] is True
    assert result["fit_read"] is True
    assert result["beta_read"] is False
    _, html = render_watchlist_entries_email(
        result["entries"], built_at=result["built_at"],
        gate_degraded=result["gate_degraded"],
        fit_read=result["fit_read"], beta_read=result["beta_read"],
    )
    assert "Portfolio-beta fit check unavailable" in html
    assert "still applied" in html
    assert "Portfolio fit checks unavailable" not in html


def test_both_legs_read_reports_both_true_and_no_box():
    from stock_analyzer.notify import render_watchlist_entries_email
    ctx = {"ok": True, "errors": [], "port_df": _priced_frame(), "spy_6mo": None,
           "port_risk": {"beta": 1.2}}
    result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx)
    assert result["gate_degraded"] is False
    assert result["fit_read"] is True
    assert result["beta_read"] is True
    _, html = render_watchlist_entries_email(
        result["entries"], built_at=result["built_at"],
        gate_degraded=result["gate_degraded"],
        fit_read=result["fit_read"], beta_read=result["beta_read"],
    )
    assert "fit check unavailable" not in html
    assert "fit checks unavailable" not in html


def test_render_legacy_gate_degraded_only_call_keeps_todays_beta_box():
    """A caller that passes only gate_degraded=True (no fit_read / beta_read)
    behaves exactly as before the CronSectorClaim change."""
    from stock_analyzer.notify import render_watchlist_entries_email
    _, html = render_watchlist_entries_email(
        [_wl_card("NVDA")], built_at="2026-09-10T09:45:00", gate_degraded=True,
    )
    assert "Portfolio-beta fit check unavailable" in html
    assert "The concentration/sector check below still applied" in html
    assert "Portfolio fit checks unavailable" not in html


def test_render_fit_not_read_shows_fit_box_even_without_gate_degraded_flag():
    from stock_analyzer.notify import render_watchlist_entries_email
    _, html = render_watchlist_entries_email(
        [_wl_card("NVDA")], built_at="2026-09-10T09:45:00", fit_read=False,
    )
    assert "Portfolio fit checks unavailable" in html
    assert "Treat them as not checked against your book." in html
    assert "still applied" not in html


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
    """A ctx whose frame sums to portfolio value 0.0 -> _position_size_for_render's
    own 'portfolio' no-size marker (F-261 cold-path posture) rather than a
    fabricated size. (The frame is non-empty: an `ok` ctx with an empty frame is
    fit UNKNOWN since entry-fail-closed C2a.)"""
    result = _run_watchlist_entries(watchlist=["NVDA"], ctx=_ctx_with_portfolio_value(0.0))
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


# ── Sold-today exclusion (2026-10-11 audit follow-up to B-H1) ────────────────
#
# A watchlist name SOLD this morning is no longer held, so D-C's held check
# lets it through. It must be captured for grading like any ENTER_NOW but
# never emailed, and a failed trade-log read suppresses the whole email set
# (owner decision 2026-10-12), same posture as the protective-call check.

def _sold_today_df(tickers):
    return pd.DataFrame([
        {"ticker": t, "action": "SELL", "traded_at": f"{TODAY.isoformat()}T15:00:00+00:00"}
        for t in tickers
    ])


def test_sold_today_name_is_captured_but_not_emailed():
    result = _run_watchlist_entries(
        watchlist=["AAPL", "NVDA"],
        cards={"AAPL": _wl_card("AAPL"), "NVDA": _wl_card("NVDA")},
        trades_df=_sold_today_df(["AAPL"]),
    )
    assert [c["ticker"] for c in result["entries"]] == ["NVDA"]
    saved = {r["ticker"] for r in result["_save_mock"].call_args[0][0]}
    assert saved == {"AAPL", "NVDA"}


def test_a_sale_on_an_earlier_day_does_not_block_today():
    df = pd.DataFrame([{"ticker": "AAPL", "action": "SELL",
                        "traded_at": f"{PRIOR_DAY.isoformat()}T15:00:00+00:00"}])
    result = _run_watchlist_entries(watchlist=["AAPL"], trades_df=df)
    assert [c["ticker"] for c in result["entries"]] == ["AAPL"]


def test_trade_log_unavailable_suppresses_every_entry_but_still_captures():
    result = _run_watchlist_entries(watchlist=["NVDA"], trades_df=None)
    assert result["entries"] == []
    assert any("sold-today" in e for e in result["errors"])
    assert result["_save_mock"].called


def test_trade_log_read_raising_suppresses_every_entry():
    result = _run_watchlist_entries(
        watchlist=["NVDA"], trades_side_effect=RuntimeError("db down"),
    )
    assert result["entries"] == []
    assert any("trades lookup failed" in e for e in result["errors"])


def test_the_cron_reads_the_trade_log_strictly_not_via_ctx():
    """`ctx["trades_df"]` comes from the lenient load_trades(), which turns a
    failed read into an empty frame -- reading it would silently disarm the
    fail-closed rule. The strict reader must be the one consulted."""
    ctx = {"ok": True, "errors": [], "port_df": pd.DataFrame(), "spy_6mo": None,
           "port_risk": {"beta": 1.1}, "trades_df": pd.DataFrame()}
    result = _run_watchlist_entries(watchlist=["NVDA"], ctx=ctx, trades_df=None)
    assert result["entries"] == []


# ── entry-fail-closed C1: scan-log count of the earnings read (read-only) ────

def _bundle_with(asset="stock", earnings=None, status=None, **extra):
    b = {"sector": "Technology", "asset_type": asset, "earnings": earnings}
    if status is not None:
        b["earnings_status"] = status
    b.update(extra)
    return b


def test_payload_counts_the_earnings_read_per_state_and_lists_failed_tickers():
    bundles = {
        "AAA": _bundle_with(earnings="2026-12-02", status="date"),
        "BBB": _bundle_with(earnings=None, status="no_date"),
        "CCC": _bundle_with(earnings=None, status="failed"),
        "DDD": _bundle_with(earnings=None),                      # no status key: couldn't tell
        "EEE": _bundle_with(asset="etf", earnings=None, status="failed"),
        "FFF": _bundle_with(earnings=None, status="date", stale_as_of="2026-11-20"),
    }
    result = _run_watchlist_entries(
        watchlist=list(bundles), load_bundle_fn=lambda t, *a, **k: bundles[t])
    assert result["earnings_states"] == {
        "counts": {"not_applicable": 1, "date": 1, "no_date": 1, "failed": 3},
        "failed_tickers": ["CCC", "DDD", "FFF"],
    }


def test_a_bundle_that_failed_to_load_is_not_counted():
    def _load(t, *a, **k):
        if t == "BAD":
            raise RuntimeError("down")
        return _bundle_with(earnings="2026-12-02", status="date")
    result = _run_watchlist_entries(watchlist=["OK", "BAD"], load_bundle_fn=_load)
    assert result["earnings_states"]["counts"]["date"] == 1
    assert result["earnings_states"]["failed_tickers"] == []
    assert sum(result["earnings_states"]["counts"].values()) == 1


def test_empty_watchlist_payload_carries_a_zero_count():
    result = _run_watchlist_entries(watchlist=())
    assert result["earnings_states"] == {
        "counts": {"not_applicable": 0, "date": 0, "no_date": 0, "failed": 0},
        "failed_tickers": [],
    }


def test_counting_never_changes_the_decision_fields():
    """Nothing reads the new state: the entries / holds are identical for a
    failed read and a dated one (C3 changes that, not C1)."""
    a = _run_watchlist_entries(watchlist=["AAPL"], bundle=_bundle_with(earnings=None, status="failed"))
    b = _run_watchlist_entries(watchlist=["AAPL"], bundle=_bundle_with(earnings="2026-12-02", status="date"))
    assert [c["ticker"] for c in a["entries"]] == [c["ticker"] for c in b["entries"]] == ["AAPL"]
    assert a["gate_degraded"] == b["gate_degraded"]


def test_a_counting_exception_never_breaks_the_lane(monkeypatch):
    def _boom(_m):
        raise RuntimeError("count broke")
    monkeypatch.setattr(hae, "earnings_states_summary", _boom)
    result = _run_watchlist_entries(watchlist=["AAPL"])
    assert result["earnings_states"] is None
    assert any("earnings state count failed" in e for e in result["errors"])
    assert [c["ticker"] for c in result["entries"]] == ["AAPL"]


def test_a_per_ticker_state_exception_never_breaks_the_lane(monkeypatch):
    def _boom(_b):
        raise RuntimeError("state broke")
    monkeypatch.setattr(hae._buy_check_state, "earnings_state", _boom)
    result = _run_watchlist_entries(watchlist=["AAPL"])
    assert any("earnings state failed" in e for e in result["errors"])
    assert [c["ticker"] for c in result["entries"]] == ["AAPL"]


def test_summary_counts_an_unrecognised_state_as_failed():
    out = hae.earnings_states_summary({"A": "date", "B": "weird", "C": None})
    assert out["counts"]["date"] == 1 and out["counts"]["failed"] == 2
    assert out["failed_tickers"] == ["B", "C"]


def test_scan_logs_one_earnings_read_line(monkeypatch, capsys):
    wle_payload = {
        "entries": [], "reason": None, "errors": [], "built_at": "x", "gate_degraded": False,
        "earnings_states": {
            "counts": {"not_applicable": 2, "date": 5, "no_date": 1, "failed": 2},
            "failed_tickers": ["XYZ", "ABC"]},
    }
    rc, sent, saved, handled = _run_scan_to_watchlist_entries(monkeypatch, wle_payload=wle_payload)
    assert rc == 0
    out = capsys.readouterr().out
    assert "watchlist earnings read: 5 date, 1 no_date, 2 failed (XYZ, ABC), 2 n/a" in out


def test_scan_log_line_omits_the_ticker_list_when_none_failed(monkeypatch, capsys):
    wle_payload = {
        "entries": [], "reason": None, "errors": [], "built_at": "x", "gate_degraded": False,
        "earnings_states": {
            "counts": {"not_applicable": 0, "date": 3, "no_date": 0, "failed": 0},
            "failed_tickers": []},
    }
    _run_scan_to_watchlist_entries(monkeypatch, wle_payload=wle_payload)
    assert "watchlist earnings read: 3 date, 0 no_date, 0 failed, 0 n/a" in capsys.readouterr().out


@pytest.mark.parametrize("bad", [None, "oops", {"counts": None, "failed_tickers": []},
                                 {"counts": {"date": 1}, "failed_tickers": []}])
def test_a_malformed_count_never_breaks_the_scan_lane(monkeypatch, capsys, bad):
    wle_payload = {"entries": [], "reason": None, "errors": [], "built_at": "x",
                   "gate_degraded": False, "earnings_states": bad}
    rc, sent, saved, handled = _run_scan_to_watchlist_entries(monkeypatch, wle_payload=wle_payload)
    assert rc == 0 and sent == []
    assert "watchlist earnings read:" in capsys.readouterr().out


def test_a_payload_without_the_key_logs_not_counted_and_the_lane_runs(monkeypatch, capsys):
    wle_payload = {"entries": [], "reason": None, "errors": [], "built_at": "x",
                   "gate_degraded": False}
    rc, *_ = _run_scan_to_watchlist_entries(monkeypatch, wle_payload=wle_payload)
    assert rc == 0
    assert "watchlist earnings read: not counted" in capsys.readouterr().out
