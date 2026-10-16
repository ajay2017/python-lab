"""Shared new-entry check, chunk 5: cron / headless / email
(docs/plans/shared-entry-check.md).

Covers: one tone/macro read per scan run (compute_morning_picks'
`entry_context` -> compute_watchlist_entries), the per-ticker entry context the
cron builds from the SAME pure helpers the app Watchlist page calls (parity),
held names (ENTRY_HELD) never emailed / captured / allocated, D5 "(held
yesterday: ...)", D4 cron ledger rows (G-28/G-29 never for a held-in-portfolio
ticker; a failed write reds the heartbeat but never blocks the email), the
email footer, `tone_available` pinned on a real compute_morning_picks, and the
intraday lane as a second caller of compute_morning_picks.
"""
import datetime as _dt
import importlib
import sys
from contextlib import ExitStack
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

for _mod in ("streamlit", "vaderSentiment", "vaderSentiment.vaderSentiment"):
    if _mod not in sys.modules:
        try:
            importlib.import_module(_mod)
        except ImportError:
            sys.modules[_mod] = MagicMock()

import cron_runner as cr
from stock_analyzer import entry_eligibility as ee
from stock_analyzer import headless_alert_engine as hae
from stock_analyzer.gate_ledger import build_watchlist_suppression_rows
from stock_analyzer.watchlist_advisor import (
    build_watchlist_recommendation as REAL_BUILD,
)

pytestmark = pytest.mark.fast

TODAY = date(2026, 9, 10)          # a Thursday; PRIOR_DAY is a trading day
PRIOR_DAY = date(2026, 9, 9)
SEMIS = "NVDA"                     # curated -> "Semiconductors"
READ_AT = _dt.datetime(2026, 9, 10, 9, 45)

BEAR_CTX = {
    "market_context": {"tone": "bear", "sp500_pct": -0.9, "tone_available": True,
                       "tone_read_at": READ_AT},
    "macro_events": [],
}
FLAT_CTX = {
    "market_context": {"tone": "flat", "sp500_pct": -0.2, "tone_available": True,
                       "tone_read_at": READ_AT},
    "macro_events": [],
}


def _bundle(**overrides):
    base = {
        "total": 80.0, "rec": {"label": "Strong Buy"}, "current_price": 100.0,
        "entry_lo": 95.0, "entry_hi": 100.0, "stop": 90.0,
        "targets": {"base": 130.0}, "earnings": None, "sector": "Technology",
        "fundamentals_available": True, "val_available": True,
    }
    base.update(overrides)
    return base


def _ctx():
    port_df = pd.DataFrame([{
        "Ticker": "HELD1", "Sector": "Healthcare",
        "Weight (%)": 2.0, "Gate Weight (%)": 2.0, "Market Value": 2000.0,
    }])
    return {"ok": True, "errors": [], "port_df": port_df, "spy_6mo": None,
            "port_risk": {"beta": 1.0}}


def _holdings(tickers):
    return pd.DataFrame({"Ticker": list(tickers)})


_UNSET = object()


def _wle(*, watchlist=(SEMIS,), held=(), entry_context=_UNSET, bundle=None,
         fake_cards=None, gate_holds=None, prior_df=None, live_ctx=None):
    """Run compute_watchlist_entries at the I/O boundary. With `fake_cards`
    ({ticker: card}) the advisor is stubbed; otherwise the REAL advisor runs and
    every (ticker, portfolio_ctx, entry_ctx, card) is recorded in `spy`."""
    spy: list = []
    gate_calls: list = []
    live_calls: list = []
    bundle = bundle if bundle is not None else _bundle()

    def _build(ticker, data, portfolio_ctx=None, entry_ctx=None):
        if fake_cards is not None:
            spy.append((ticker, portfolio_ctx, entry_ctx, fake_cards[ticker]))
            return fake_cards[ticker]
        card = REAL_BUILD(ticker, data, portfolio_ctx=portfolio_ctx, entry_ctx=entry_ctx)
        spy.append((ticker, portfolio_ctx, entry_ctx, card))
        return card

    def _gate_holds(day, ids):
        gate_calls.append((day, list(ids)))
        if isinstance(gate_holds, Exception):
            raise gate_holds
        return gate_holds

    def _live(today, port_df):
        live_calls.append(today)
        return live_ctx if live_ctx is not None else FLAT_CTX

    with ExitStack() as st:
        P = lambda target, **kw: st.enter_context(  # noqa: E731
            patch(f"stock_analyzer.headless_alert_engine.{target}", **kw))
        P("db.load_watchlist_or_none", return_value=list(watchlist))
        P("db.load_holdings_or_none", return_value=_holdings(held))
        P("fetch_risk_free_rate", return_value=0.045)
        P("load_bundle", return_value=bundle)
        P("db.load_exit_signals_or_none", return_value=pd.DataFrame())
        P("db.load_trades_or_none", return_value=pd.DataFrame())
        P("db.load_recommendations_or_none",
          return_value=prior_df if prior_df is not None else pd.DataFrame())
        save_mock = P("db.save_recommendations", return_value={"error": None})
        P("is_trading_day", return_value=True)
        P("build_watchlist_recommendation", side_effect=_build)
        P("db.load_gate_holds_on_day_or_none", side_effect=_gate_holds)
        P("_live_entry_context", side_effect=_live)
        kw = {} if entry_context is _UNSET else {"entry_context": entry_context}
        result = hae.compute_watchlist_entries(
            TODAY, watchlist=list(watchlist), ctx=_ctx(), **kw)
    return result, save_mock, spy, gate_calls, live_calls


def _held_tone_card(ticker, price=100.0):
    return {"ticker": ticker, "action": "ENTRY_HELD", "score": 80.0, "price": price,
            "entry_lo": 95.0, "entry_hi": 105.0, "stop": 90.0, "rr": 2.5,
            "summary": "held", "hold_kind": "tone", "suppression_kind": "entry_tone",
            "gate_value": -0.9, "gate_threshold": -0.5}


def _enter_card(ticker):
    return {"ticker": ticker, "action": "ENTER_NOW", "score": 80.0, "price": 100.0,
            "entry_lo": 95.0, "entry_hi": 105.0, "stop": 90.0, "rr": 2.5,
            "summary": f"{ticker} summary"}


# ═════════════ 1. tone_available pinned on a real compute_morning_picks ══════
def _morning(indices=None, indices_exc=None, macro=None, macro_exc=None,
             brief_exc=None):
    ctx = {
        "ok": True, "errors": [],
        "port_df": pd.DataFrame({"Ticker": ["AAPL"], "Market Value": [1000.0]}),
        "held_data": {"AAPL": {}}, "fragility": None, "spy_6mo": None,
        "spy_1y": None, "vix": None, "holdings_df": pd.DataFrame({"Ticker": ["AAPL"]}),
    }
    brief = {"grow_today": {"tone": "flat", "new_picks": [], "sp500_pct": 0.0}}
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.data.fetch_market_indices",
               return_value=indices, side_effect=indices_exc), \
         patch("stock_analyzer.headless_alert_engine.load_bundle", return_value={}), \
         patch("stock_analyzer.data.curate_news_items", return_value=[]), \
         patch("stock_analyzer.macro_calendar.build_macro_calendar",
               return_value=macro, side_effect=macro_exc), \
         patch("stock_analyzer.headless_alert_engine.build_daily_briefing",
               return_value=brief, side_effect=brief_exc):
        return hae.compute_morning_picks(
            TODAY, scanner_results=pd.DataFrame({"Ticker": ["NVDA"]}))


def test_tone_available_false_when_the_index_fetch_raises():
    r = _morning(indices_exc=RuntimeError("down"), macro=[])
    assert r["entry_context"]["market_context"]["tone_available"] is False


def test_tone_available_false_when_the_sp500_row_is_missing():
    r = _morning(indices=[{"short": "NASDAQ", "change_pct": -1.0}], macro=[])
    assert r["entry_context"]["market_context"]["tone_available"] is False


def test_tone_available_true_with_an_sp500_row_and_the_read_is_stamped():
    r = _morning(indices=[{"short": "S&P 500", "change_pct": -0.8},
                          {"short": "NASDAQ", "change_pct": -1.0}], macro=[])
    mc = r["entry_context"]["market_context"]
    assert mc["tone_available"] is True
    assert mc["tone"] == "bear" and mc["sp500_pct"] == -0.8
    stamp = mc["tone_read_at"]
    assert isinstance(stamp, _dt.datetime) and stamp.tzinfo is not None
    assert stamp.utcoffset() is not None
    assert getattr(stamp.tzinfo, "zone", None) == "America/New_York"


def test_entry_context_macro_sentinel_none_vs_empty_vs_events():
    idx = [{"short": "S&P 500", "change_pct": 0.1}]
    assert _morning(indices=idx, macro_exc=RuntimeError("x"))["entry_context"][
        "macro_events"] is None                       # couldn't be built
    assert _morning(indices=idx, macro=[])["entry_context"]["macro_events"] == []
    ev = [{"event": "CPI", "impact": "HIGH", "category": "Inflation", "date": TODAY}]
    assert _morning(indices=idx, macro=ev)["entry_context"]["macro_events"] == ev


def test_a_failed_brief_build_still_returns_the_reads_it_already_made():
    r = _morning(indices=[{"short": "S&P 500", "change_pct": -0.8}], macro=[],
                 brief_exc=RuntimeError("boom"))
    assert r["picks"] == []
    assert r["entry_context"]["market_context"]["tone"] == "bear"
    assert r["entry_context"]["macro_events"] == []


# ═════════════ 2. intraday is a second caller of compute_morning_picks ═══════
def test_intraday_lane_is_unaffected_by_the_new_entry_context_key(monkeypatch):
    now_et = cr.datetime.now(cr._ET)
    today_str = now_et.date().isoformat()
    pick = {"ticker": "AAA", "composite_score": 80.0,
            "xref": {"verdict_reconciled": {"verdict": "go"}}}
    seen = {}

    def _picks(**kw):
        seen.update(kw)
        return {"picks": [pick], "errors": [], "built_at": today_str,
                "budget_inputs": {}, "book_drift": None, "macro_coverage": None,
                "entry_context": FLAT_CTX}
    monkeypatch.setattr(cr, "compute_morning_picks", _picks)
    monkeypatch.setattr(cr.db, "load_scanner_cache", lambda: {
        "df": pd.DataFrame({"Ticker": ["AAA"]}), "scan_date": today_str})
    from stock_analyzer import intraday_entry
    monkeypatch.setattr(intraday_entry, "fetch_intraday_prices",
                        lambda tickers: {"AAA": {"current": 98.0, "open": 100.0},
                                         "SPY": None})
    monkeypatch.setattr(intraday_entry, "compute_intraday_entries",
                        lambda *a, **k: [{"ticker": "AAA", "intraday_drop_pct": -2.0}])
    monkeypatch.setattr(cr, "run_daily_budget", lambda *a, **k: {
        "budget": {"state": "ok", "left": 1.0}, "written": 0})
    monkeypatch.setattr(cr, "render_intraday_entry_email", lambda **k: ("s", "h"))
    sent = []
    monkeypatch.setattr(cr, "_send_email", lambda label, s, h: sent.append(label) or True)
    monkeypatch.setattr(cr, "_dedup_state_or_log", lambda *a, **k: {})
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *a, **k: True)
    assert cr._run_intraday(now_et, force=True) == 0
    assert sent == ["intraday-entry"]
    assert "entry_context" not in seen          # the lane never asks for it


# ═════════════ 3. live fallback keeps the check on (never silently skipped) ══
def test_live_entry_context_sentinels(monkeypatch):
    with patch("stock_analyzer.data.fetch_market_indices",
               side_effect=RuntimeError("down")), \
         patch("stock_analyzer.macro_calendar.build_macro_calendar",
               side_effect=RuntimeError("x")):
        c = hae._live_entry_context(TODAY, None)
    assert c["market_context"]["tone_available"] is False
    assert c["macro_events"] is None
    with patch("stock_analyzer.data.fetch_market_indices",
               return_value=[{"short": "S&P 500", "change_pct": -0.7}]), \
         patch("stock_analyzer.macro_calendar.build_macro_calendar", return_value=[]):
        c = hae._live_entry_context(TODAY, pd.DataFrame())
    assert c["market_context"]["tone"] == "bear"
    assert c["macro_events"] == []               # checked, none -- not None


def test_no_entry_context_makes_a_live_read_and_the_check_still_defers():
    r, save, spy, _g, live_calls = _wle(live_ctx=BEAR_CTX)      # no entry_context
    assert live_calls == [TODAY]
    assert r["entries"] == []
    assert spy[0][3]["action"] == "ENTRY_HELD"
    save.assert_not_called()


def test_a_passed_entry_context_is_used_and_no_second_read_is_made():
    r, _s, spy, _g, live_calls = _wle(entry_context=BEAR_CTX)
    assert live_calls == []
    assert spy[0][3]["action"] == "ENTRY_HELD"


def test_tone_unreadable_fails_open_with_a_disclosure():
    ctx = {"market_context": {"tone_available": False}, "macro_events": None}
    r, _s, spy, _g, _l = _wle(entry_context=ctx)
    assert [e["ticker"] for e in r["entries"]] == [SEMIS]       # open
    assert r["entry_run"]["tone_state"]["available"] is False
    assert r["entry_run"]["macro_available"] is False
    lines = ee.email_footer_lines(r["entry_run"])
    assert "Market tone couldn't be read — the bear-day check didn't run." in lines
    assert "Macro calendar unavailable — the macro-event check didn't run." in lines


def test_nan_tone_is_unavailable_not_classified():
    ctx = {"market_context": {"tone": "flat", "sp500_pct": float("nan"),
                              "tone_available": True}, "macro_events": []}
    r, *_ = _wle(entry_context=ctx)
    assert r["entry_run"]["tone_state"]["available"] is False


def test_a_macro_crash_never_discards_a_correctly_read_bear_tone():
    # watchlist_run_ctx (macro build) raises: the bear tone is KEPT -> the name
    # is held for tone (like the app's separate try blocks), macro "couldn't run".
    with patch("stock_analyzer.headless_alert_engine.watchlist_run_ctx",
               side_effect=RuntimeError("boom")):
        r, save, spy, *_ = _wle(entry_context=BEAR_CTX)
    assert any("entry check macro failed" in e for e in r["errors"])
    assert spy[0][3]["action"] == "ENTRY_HELD" and spy[0][3]["hold_kind"] == "tone"
    assert spy[0][2]["tone_state"]["tone"] == "bear" and spy[0][2]["macro_map"] is None
    assert r["entries"] == []
    save.assert_not_called()
    lines = ee.email_footer_lines(r["entry_run"])
    assert lines[0].startswith("Market tone at 9:45 AM ET: bear")
    assert "Macro calendar unavailable — the macro-event check didn't run." in lines


def test_a_malformed_macro_event_on_a_bear_day_still_defers():
    ctx = {**BEAR_CTX, "macro_events": [None]}          # macro_block_map raises on None
    r, save, spy, *_ = _wle(entry_context=ctx)
    assert spy[0][3]["action"] == "ENTRY_HELD" and spy[0][3]["hold_kind"] == "tone"
    assert r["entries"] == [] and r["entry_run"]["tone_state"]["available"] is True
    assert r["entry_run"]["macro_available"] is False
    save.assert_not_called()


def test_an_unreadable_tone_step_fails_open_and_says_so():
    with patch("stock_analyzer.headless_alert_engine.resolve_tone_state",
               side_effect=RuntimeError("boom")):
        r, _s, spy, *_ = _wle(entry_context=BEAR_CTX)
    assert any("entry check tone failed" in e for e in r["errors"])
    assert [e["ticker"] for e in r["entries"]] == [SEMIS]          # open
    assert spy[0][2]["tone_state"]["available"] is False
    assert ee.email_footer_lines(r["entry_run"])[0] == (
        "Market tone couldn't be read — the bear-day check didn't run.")


# ═════════════ 4. held names: never emailed, captured or allocated ═══════════
def test_entry_held_is_never_emailed_captured_or_a_qualifier():
    cards = {"AAA": _enter_card("AAA"), "BBB": _held_tone_card("BBB")}
    r, save, *_ = _wle(watchlist=("AAA", "BBB"), fake_cards=cards,
                       entry_context=FLAT_CTX)
    assert [e["ticker"] for e in r["entries"]] == ["AAA"]
    rows = save.call_args[0][0]
    assert [x["ticker"] for x in rows] == ["AAA"]               # BBB not captured


def test_all_deferred_real_advisor_emails_nobody_captures_nothing_and_still_ledgers():
    r, save, spy, *_ = _wle(entry_context=BEAR_CTX)
    assert spy[0][3]["action"] == "ENTRY_HELD"
    assert r["entries"] == []
    save.assert_not_called()                                    # no enter_now row
    gids = [(x["ticker"], x["gate_id"], x["source"]) for x in r["gate_ledger_rows"]]
    assert gids == [(SEMIS, "G-29", "cron")]


def test_held_in_portfolio_ticker_gets_no_g28_g29_row_but_a_fit_row_survives():
    r, *_ = _wle(entry_context=BEAR_CTX, held=(SEMIS,))
    assert r["entries"] == []
    assert r["gate_ledger_rows"] == []
    # the filter touches only the two entry-check kinds
    near = {"ticker": "HLD", "action": "NEAR_ENTRY", "suppression_kind": "sector",
            "price": 10.0, "score": 70.0}
    held_tone = _held_tone_card("HLD")
    rows = build_watchlist_suppression_rows(
        [near, held_tone], rec_date=TODAY, source="cron", held_tickers={"hld"})
    assert [(x["ticker"], x["gate_id"]) for x in rows] == [("HLD", "G-05")]
    macro = {**_held_tone_card("MAC"), "suppression_kind": "entry_macro"}
    rows = build_watchlist_suppression_rows(
        [macro], rec_date=TODAY, source="cron", held_tickers=["MAC"])
    assert rows == []
    both = build_watchlist_suppression_rows(
        [macro, held_tone], rec_date=TODAY, source="cron", held_tickers=None)
    assert sorted(x["gate_id"] for x in both) == ["G-28", "G-29"]    # legacy: unfiltered


# ═════════════ 5. D5 "(deferred yesterday — ...)" ═════════════════════════════════
def test_held_yesterday_note_from_the_prior_trading_days_hold_row():
    r, _s, _spy, gate_calls, _l = _wle(entry_context=FLAT_CTX,
                                       gate_holds={SEMIS: "G-29"})
    assert [e["held_yesterday"] for e in r["entries"]] == ["market risk-off"]
    assert gate_calls[0][0] == PRIOR_DAY
    assert sorted(gate_calls[0][1]) == ["G-28", "G-29"]
    r, *_ = _wle(entry_context=FLAT_CTX, gate_holds={SEMIS: "G-28"})
    assert r["entries"][0]["held_yesterday"] == "macro event"


def test_held_yesterday_failed_read_or_no_row_means_no_note():
    for holds in (None, {}, {"OTHER": "G-29"}, {SEMIS: "G-05"}):
        r, *_ = _wle(entry_context=FLAT_CTX, gate_holds=holds)
        assert len(r["entries"]) == 1
        assert "held_yesterday" not in r["entries"][0], holds


def test_held_yesterday_lookup_exception_is_recorded_and_harmless():
    r, *_ = _wle(entry_context=FLAT_CTX, gate_holds=RuntimeError("db"))
    assert len(r["entries"]) == 1 and "held_yesterday" not in r["entries"][0]
    assert any("held-yesterday lookup failed" in e for e in r["errors"])


def test_db_day_scoped_hold_loader_contract(monkeypatch):
    from stock_analyzer import db

    class _Q:
        def __init__(self, rows, exc):
            self.rows, self.exc, self.eqs = rows, exc, []

        def select(self, *a): return self
        def eq(self, *a): self.eqs.append(a); return self
        def in_(self, *a): self.ins = a; return self
        def order(self, *a, **k): return self
        def limit(self, *a): return self

        def execute(self):
            if self.exc:
                raise self.exc

            class R:
                data = self.rows
            return R()

    class _C:
        def __init__(self, rows=None, exc=None):
            self.q = _Q(rows, exc)

        def table(self, name):
            assert name == "gate_suppressions"
            return self.q

    ids = ["G-28", "G-29"]
    monkeypatch.setattr(db, "has_db", lambda: True)
    rows = [{"ticker": "nvda", "gate_id": "G-28", "source": "cron"},
            {"ticker": "NVDA", "gate_id": "G-29", "source": "cron"},   # first wins
            {"ticker": "AMD", "gate_id": "G-29", "source": "app"},
            {"ticker": "X", "gate_id": "G-05", "source": "cron"},      # other gate
            {"ticker": "Y", "gate_id": "G-29", "source": "backfill"}]  # other source
    c = _C(rows)
    monkeypatch.setattr(db, "_client", lambda: c)
    assert db.load_gate_holds_on_day_or_none(PRIOR_DAY, ids) == {
        "NVDA": "G-28", "AMD": "G-29"}
    assert c.q.eqs == [("rec_date", "2026-09-09")]              # one day only
    assert c.q.ins == ("gate_id", ["G-28", "G-29"])             # filtered server-side too
    monkeypatch.setattr(db, "_client", lambda: _C([]))
    assert db.load_gate_holds_on_day_or_none("2026-09-09", ids) == {}   # checked, none
    monkeypatch.setattr(db, "_client", lambda: _C(exc=RuntimeError("x")))
    assert db.load_gate_holds_on_day_or_none(PRIOR_DAY, ids) is None
    monkeypatch.setattr(db, "_client", lambda: _C(None))
    assert db.load_gate_holds_on_day_or_none(PRIOR_DAY, ids) is None
    monkeypatch.setattr(db, "_client",
                        lambda: _C([{"ticker": "A", "gate_id": "G-29",
                                     "source": "cron"}] * db._GATE_SUPPRESSIONS_PAGE_SIZE))
    assert db.load_gate_holds_on_day_or_none(PRIOR_DAY, ids) is None   # at page cap
    assert db.load_gate_holds_on_day_or_none("not-a-date", ids) is None
    assert db.load_gate_holds_on_day_or_none(None, ids) is None
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_gate_holds_on_day_or_none(PRIOR_DAY, ids) is None


# ═════════════ 6. email footer + card note ═══════════════════════════════════
def test_footer_lines_per_path():
    ts = {"tone": "flat", "sp500_pct": -0.24, "available": True, "as_of": READ_AT}
    run = {"tone_state": ts, "macro_available": True}
    assert ee.email_footer_lines(run) == [
        "Market tone at 9:45 AM ET: flat (S&P -0.2%). The app re-checks when you "
        "open it and defers if the market turns risk-off."]
    no_time = {"tone_state": {**ts, "as_of": None}, "macro_available": True}
    assert ee.email_footer_lines(no_time)[0].startswith("Market tone: flat (S&P -0.2%)")
    pm = {"tone_state": {**ts, "as_of": _dt.datetime(2026, 9, 10, 13, 5)},
          "macro_available": True}
    assert "1:05 PM ET" in ee.email_footer_lines(pm)[0]
    assert ee.email_footer_lines({"tone_state": ts, "macro_available": False})[1:] == [
        "Macro calendar unavailable — the macro-event check didn't run."]
    down = {"tone_state": {"available": False}, "macro_available": True}
    assert ee.email_footer_lines(down) == [
        "Market tone couldn't be read — the bear-day check didn't run."]
    assert ee.email_footer_lines(None) == []
    assert ee.email_footer_lines("junk") == []


def test_email_renders_footer_escaped_and_the_held_yesterday_note():
    from stock_analyzer.notify import render_watchlist_entries_email
    entry = {**_enter_card("AAA"), "held_yesterday": "market <risk-off>"}
    run = {"tone_state": {"tone": "flat", "sp500_pct": -0.2, "available": True,
                          "as_of": READ_AT}, "macro_available": False}
    _s, html = render_watchlist_entries_email([entry], built_at="2026-09-10T09:45:00",
                                              entry_run=run)
    assert "Market tone at 9:45 AM ET: flat (S&amp;P -0.2%)" in html
    assert "Macro calendar unavailable — the macro-event check didn&#x27;t run." in html \
        or "Macro calendar unavailable — the macro-event check didn't run." in html
    assert "(deferred yesterday — market &lt;risk-off&gt;)" in html
    assert "market <risk-off>" not in html
    assert "**" not in html
    # an old caller (no entry_run) says nothing about tone and adds no note
    _s, plain = render_watchlist_entries_email([_enter_card("AAA")], built_at="x")
    assert "Market tone" not in plain and "deferred yesterday" not in plain


# ═════════════ 7. app <-> cron parity: the SAME card for identical inputs ════
_MACRO_EV = [{"event": "CPI", "impact": "HIGH", "category": "Inflation",
              "date": TODAY + timedelta(days=2)}]
_NEG_NEWS = [{"title": "Company faces fraud probe and plunging sales, investors flee",
              "publisher": "Reuters", "link": "u", "providerPublishTime": 1}]


@pytest.mark.parametrize("name, entry_context, bundle_kw", [
    ("bear", BEAR_CTX, {}),
    ("flat_eligible", FLAT_CTX, {}),
    ("macro", {**FLAT_CTX, "macro_events": _MACRO_EV}, {}),
    ("news", FLAT_CTX, {"news_raw": _NEG_NEWS}),
    ("tone_unreadable", {"market_context": {"tone_available": False},
                         "macro_events": None}, {}),
    ("stale", FLAT_CTX, {"stale_as_of": "2026-09-08"}),
    ("bear_macro_build_raises", {**BEAR_CTX, "macro_events": [None]}, {}),
])
def test_app_and_cron_build_the_same_card(name, entry_context, bundle_kw):
    from stock_analyzer.data import curate_news_items
    bundle = _bundle(**bundle_kw)
    _r, _s, spy, *_ = _wle(entry_context=entry_context, bundle=bundle)
    ticker, pctx, cron_entry_ctx, cron_card = spy[0]

    # The app page's recipe (app.py Watchlist wiring), verbatim in the pure
    # helpers it calls: resolve_tone_state(None, False, None, pct, ok) ->
    # watchlist_run_ctx -> watchlist_ticker_ctx(curate_news_items) -> advisor.
    mc = entry_context["market_context"]
    pct = mc.get("sp500_pct")
    tone = ee.resolve_tone_state(None, False, None, pct, mc.get("tone_available") is True)
    try:
        run = ee.watchlist_run_ctx(tone, entry_context["macro_events"], TODAY,
                                   _dt.datetime(2026, 9, 10, 9, 46))
    except Exception:       # the app's own fallback: tone kept, macro unchecked
        run = {"tone_state": tone, "macro_map": None}
    from stock_analyzer.sector_fit import sector_gate_context
    sctx = sector_gate_context(ticker, "Technology", _ctx()["port_df"])
    app_ctx = ee.watchlist_ticker_ctx(
        run, sector=sctx["sector"], classified=sctx["classified"],
        news_items=curate_news_items({ticker: bundle}))
    app_card = REAL_BUILD(ticker, bundle, portfolio_ctx=pctx, entry_ctx=app_ctx)

    assert {k: v for k, v in cron_entry_ctx.items() if k != "tone_state"} == \
        {k: v for k, v in app_ctx.items() if k != "tone_state"}
    assert cron_entry_ctx["tone_state"]["tone"] == app_ctx["tone_state"]["tone"]
    # The cron attaches email-only display fields to an eligible card AFTER the
    # advisor built it (sizing / economics / D5 note); those are not the card.
    _email_only = {"sizing", "net_capital", "capital_basis", "economics", "held_yesterday"}
    assert {k: v for k, v in cron_card.items() if k not in _email_only} == app_card, name


def test_the_parity_scenarios_really_differ():
    """Not vacuous: bear / macro / news / stale each HOLD, flat is eligible."""
    from stock_analyzer.data import curate_news_items  # noqa: F401
    actions = {}
    for name, ectx, kw in [
        ("bear", BEAR_CTX, {}), ("flat", FLAT_CTX, {}),
        ("macro", {**FLAT_CTX, "macro_events": _MACRO_EV}, {}),
        ("news", FLAT_CTX, {"news_raw": _NEG_NEWS}),
        ("stale", FLAT_CTX, {"stale_as_of": "2026-09-08"}),
    ]:
        _r, _s, spy, *_ = _wle(entry_context=ectx, bundle=_bundle(**kw))
        actions[name] = (spy[0][3]["action"], spy[0][3].get("hold_kind"))
    assert actions["flat"][0] == "ENTER_NOW"
    assert actions["bear"] == ("ENTRY_HELD", "tone")
    assert actions["macro"] == ("ENTRY_HELD", "macro")
    assert actions["news"] == ("ENTRY_HELD", "news")
    assert actions["stale"] == ("ENTRY_HELD", "stale")


# ═════════════ 8. cron _run_scan wiring ══════════════════════════════════════
def _scan(monkeypatch, *, morning, wle, gl_result=None, gl_raises=False,
          wle_raises=False):
    from stock_analyzer import reference_data as rd
    from stock_analyzer import scanner as real_scanner
    monkeypatch.setattr(cr, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(rd, "resolve_universe_or_none",
                        lambda name: ({"Tech": ["AAPL"]}, date(2026, 9, 1), None))
    monkeypatch.setattr(real_scanner, "scan_sectors",
                        lambda *a, **k: pd.DataFrame({"Ticker": ["AAPL"], "Score": [70.0]}))
    monkeypatch.setattr(cr, "_build_context", lambda _d: {"ok": True, "errors": []})
    monkeypatch.setattr(cr.db, "save_scanner_cache", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "compute_morning_picks", lambda **_k: morning)
    wle_calls: list = []

    def _wle_fn(**kw):
        wle_calls.append(kw)
        if wle_raises:
            raise RuntimeError("wle boom")
        return wle
    monkeypatch.setattr(cr, "compute_watchlist_entries", _wle_fn)
    monkeypatch.setattr(cr.db, "load_exit_signals", lambda **_k: pd.DataFrame())
    gl_calls: list = []

    def _save_gl(rows):
        gl_calls.append(list(rows))
        if gl_raises and rows and rows[0].get("gate_id", "").startswith("G-2"):
            raise RuntimeError("gl boom")
        if gl_result is not None and rows and rows[0].get("gate_id", "").startswith("G-2"):
            return gl_result
        return {"attempted": len(rows), "saved": len(rows), "error": None}
    monkeypatch.setattr(cr.db, "save_gate_suppressions", _save_gl)
    budget_calls: list = []

    def _budget(today, now, bi, emailed, others=()):
        budget_calls.append([(k, it.get("ticker")) for k, it in emailed])
        return {"budget": {"state": "ok", "left": 1.0, "pct_used": 0.0},
                "per_ticker": {}, "save_failed": False, "written": 0}
    monkeypatch.setattr(cr, "run_daily_budget", _budget)
    sent: list = []
    monkeypatch.setattr(cr, "_send_email", lambda label, s, h: sent.append((label, h)) or True)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
    notified: list = []
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, d: notified.append((lane, d)))
    rc = cr._run_scan(cr.datetime.now(cr._ET), force=True)
    return rc, sent, wle_calls, gl_calls, budget_calls, notified


def _morning_payload(**extra):
    return {"picks": [], "errors": [], "built_at": "2026-09-10T09:45:00",
            "diag": {}, "book_drift": None, "macro_coverage": None, "grow": {},
            "budget_inputs": {}, **extra}


def _wle_payload(entries=(), rows=(), entry_run=None):
    return {"entries": list(entries), "built_at": "2026-09-10T09:46:00", "errors": [],
            "reason": None, "gate_degraded": False,
            "entry_run": entry_run, "gate_ledger_rows": list(rows)}


def test_scan_passes_the_same_tone_read_to_the_watchlist_computation(monkeypatch):
    morning = _morning_payload(entry_context=FLAT_CTX)
    _rc, _sent, wle_calls, *_ = _scan(monkeypatch, morning=morning, wle=_wle_payload())
    assert wle_calls[0]["entry_context"] is FLAT_CTX            # the SAME object


def test_scan_without_an_entry_context_passes_none_so_the_watchlist_reads_live(monkeypatch):
    _rc, _sent, wle_calls, *_ = _scan(monkeypatch, morning=_morning_payload(),
                                      wle=_wle_payload())
    assert wle_calls[0]["entry_context"] is None


def test_scan_all_deferred_sends_no_watchlist_email_but_still_ledgers(monkeypatch):
    real, *_ = _wle(entry_context=BEAR_CTX)            # the REAL all-deferred payload
    assert real["entries"] == [] and real["gate_ledger_rows"]
    rc, sent, _w, gl_calls, budget_calls, notified = _scan(
        monkeypatch, morning=_morning_payload(entry_context=BEAR_CTX), wle=real)
    assert rc == 0 and notified == []
    assert [s for s in sent if s[0] == "watchlist-entries"] == []     # D6: no email
    assert budget_calls == [] or budget_calls == [[]]       # nothing allocated
    wl_rows = [r for call in gl_calls for r in call if r.get("gate_id") == "G-29"]
    assert [(r["ticker"], r["source"]) for r in wl_rows] == [(SEMIS, "cron")]


def test_scan_emails_only_ready_names_and_allocates_only_them(monkeypatch):
    cards = {"AAA": _enter_card("AAA"), "BBB": _held_tone_card("BBB")}
    real, *_ = _wle(watchlist=("AAA", "BBB"), fake_cards=cards, entry_context=FLAT_CTX)
    run = {"tone_state": {"tone": "flat", "sp500_pct": -0.2, "available": True,
                          "as_of": READ_AT}, "macro_available": True}
    real["entry_run"] = run
    rc, sent, _w, _g, budget_calls, _n = _scan(
        monkeypatch, morning=_morning_payload(entry_context=FLAT_CTX), wle=real)
    assert rc == 0
    assert budget_calls == [[("entry", "AAA")]]                 # BBB never allocated
    (label, html), = [s for s in sent if s[0] == "watchlist-entries"]
    assert "AAA" in html and "BBB" not in html
    assert "Market tone at 9:45 AM ET: flat" in html


def test_scan_ledger_write_failure_reds_the_lane_but_never_blocks_the_email(monkeypatch):
    row = {"ticker": "AAA", "gate_id": "G-29", "source": "cron", "rec_date": "2026-09-10"}
    wle = _wle_payload(entries=[_enter_card("AAA")], rows=[row])
    for kw in ({"gl_result": {"attempted": 1, "saved": 0, "error": "offline"}},
               {"gl_raises": True}):
        cr._LAST_LANE_FAILURE_DETAIL = None
        rc, sent, _w, gl_calls, _b, notified = _scan(
            monkeypatch, morning=_morning_payload(), wle=wle, **kw)
        assert rc == 1
        assert "watchlist_gate_suppressions" in cr._LAST_LANE_FAILURE_DETAIL
        assert notified == [("scan", cr._LAST_LANE_FAILURE_DETAIL)]
        assert [s[0] for s in sent] == ["watchlist-entries"]    # email went out
        assert [row] in gl_calls                                # the rows were saved


def test_scan_ledger_compat_note_is_not_a_failure_and_clean_run_is_green(monkeypatch):
    row = {"ticker": "AAA", "gate_id": "G-29", "source": "cron", "rec_date": "2026-09-10"}
    wle = _wle_payload(rows=[row])
    rc, *_ = _scan(monkeypatch, morning=_morning_payload(), wle=wle, gl_result={
        "attempted": 1, "saved": 1,
        "error": "compat: ignore_duplicates unsupported (last-writer-wins)"})
    assert rc == 0
    rc, sent, _w, gl_calls, *_ = _scan(monkeypatch, morning=_morning_payload(), wle=wle)
    assert rc == 0 and [row] in gl_calls
    # no rows -> no watchlist ledger call at all (an empty list is not a write)
    _rc, _s, _w, gl_calls, *_ = _scan(monkeypatch, morning=_morning_payload(),
                                      wle=_wle_payload())
    assert all(call == [] for call in gl_calls)


def test_scan_watchlist_computation_crash_leaves_the_lane_and_ledger_alone(monkeypatch):
    rc, sent, _w, gl_calls, *_ = _scan(monkeypatch, morning=_morning_payload(),
                                       wle=None, wle_raises=True)
    assert rc == 0
    assert all(call == [] for call in gl_calls)


def test_email_shows_a_per_name_not_checked_note_escaped():
    from stock_analyzer.notify import render_watchlist_entries_email
    e = {**_enter_card("AAA"), "entry_unchecked": [
        {"check": "news", "why": "no recent headlines to check"},
        {"check": "macro", "why": "sector unknown <b>"}]}
    _s, html = render_watchlist_entries_email([e], built_at="x")
    assert "Not checked: no recent headlines to check; sector unknown &lt;b&gt;" in html
    for none in (None, [], [{"check": "x"}]):
        _s, h = render_watchlist_entries_email(
            [{**_enter_card("AAA"), "entry_unchecked": none}], built_at="x")
        assert "Not checked" not in h
    _s, h = render_watchlist_entries_email([_enter_card("AAA")], built_at="x")
    assert "Not checked" not in h


def test_real_eligible_card_carries_its_unchecked_notes_into_the_email():
    from stock_analyzer.notify import render_watchlist_entries_email
    r, *_ = _wle(entry_context=FLAT_CTX)          # no news_raw -> "no recent headlines"
    assert r["entries"][0].get("entry_unchecked")
    _s, html = render_watchlist_entries_email(r["entries"], built_at="x")
    assert "Not checked: no recent headlines to check" in html
