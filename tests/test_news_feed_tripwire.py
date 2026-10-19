"""News-feed restore, chunk 2 (docs/plans/news-feed-restore.md): make a dead feed loud.

(g) news_feed.feed_state boundaries; (h) the cron lanes turn an outage into a red
heartbeat while STILL sending their emails; (i) the yahoo_news provider row in
the System Trust provider check; plus pins for Home's `_news_feed_cache` publish and
its registry rows. No network, no DB: everything is faked.
"""
import ast
from tests._ast_cache import parse_file_cached
import datetime
import re
from pathlib import Path

import pandas as pd
import pytest

import cron_runner as cr
from stock_analyzer import api_health, coord_freshness, news_feed as nf, system_health
from stock_analyzer.constants import NEWS_FEED_OUTAGE_MIN_TICKERS as MIN
from stock_analyzer.providers import yfinance_provider as yp
from tests import test_cron_daily_budget as _bud

ROOT = Path(__file__).resolve().parent.parent
NOW_TS = 1_800_000_000.0


def _b(status):
    return {"news_status": status}


def _many(status, n):
    return {f"T{i}": _b(status) for i in range(n)}


# -- (g) feed_state -----------------------------------------------------------
def test_constant_is_five():
    assert MIN == 5


@pytest.mark.parametrize("status", ["none", "error"])
def test_min_live_bundles_none_ok_is_outage(status):
    r = nf.feed_state(_many(status, MIN))
    assert r["state"] == "outage" and r["checked"] == MIN and r["with_news"] == 0


@pytest.mark.parametrize("status", ["none", "error"])
def test_one_below_min_is_thin(status):
    r = nf.feed_state(_many(status, MIN - 1))
    assert r["state"] == "thin" and r["checked"] == MIN - 1


def test_one_ok_among_many_is_ok():
    d = _many("error", 20)
    d["GOOD"] = _b("ok")
    r = nf.feed_state(d)
    assert r["state"] == "ok" and r["with_news"] == 1 and r["checked"] == 21


def test_empty_and_none_input_is_none():
    assert nf.feed_state({}) is None
    assert nf.feed_state([]) is None
    assert nf.feed_state(None) is None


def test_cached_and_missing_are_unavailable_and_not_live():
    d = {"A": _b("cached"), "B": {}, "C": None, "D": _b("error"), "E": _b("none")}
    r = nf.feed_state(d)
    assert r["checked"] == 2
    assert {"A", "B", "C", "D"} <= set(r["unavailable"])
    assert r["none"] == ["E"]


def test_cached_do_not_count_toward_the_outage_floor():
    d = _many("none", MIN - 1)
    d.update({f"C{i}": _b("cached") for i in range(10)})
    assert nf.feed_state(d)["state"] == "thin"


def test_iterable_of_bundles_works():
    r = nf.feed_state([{"ticker": "X", "news_status": "none"}] * MIN)
    assert r["state"] == "outage"


def test_outage_failure_one_line_and_none_cases():
    out = {"state": "outage", "checked": 7, "with_news": 0}
    line = nf.outage_failure(out, out, {"state": "ok"}, None)
    assert line.startswith("news feed: 0 headlines across 7 tickers")
    assert nf.outage_failure(None, {"state": "thin"}, {"state": "ok"}) is None
    assert nf.outage_failure() is None


# -- (h) cron lanes -----------------------------------------------------------
OUT = {"state": "outage", "checked": 9, "with_news": 0, "unavailable": [], "none": []}
OK = {"state": "ok", "checked": 9, "with_news": 4, "unavailable": [], "none": []}
NOW_PM = datetime.datetime(2026, 10, 13, 8, 30)


def _run_pm(monkeypatch, feed):
    payload = {"alerts": [], "built_at": "2026-10-13T08:00:00", "errors": [],
               "all_deterioration_signals": [], "risk_off_signals": [],
               "analyst_target_snapshots": [], "score_history": [],
               "split_withheld": ["AAPL"],     # reportable on its own -> an email goes
               "news_feed": feed}
    sent, notified = [], []
    monkeypatch.setattr(cr, "compute_protective_alerts", lambda **_k: payload)
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda **_k: None)
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: {})
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_send_email", lambda *a, **_k: sent.append(a[0]) or True)
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, d: notified.append((lane, d)))
    rc = cr._run_premarket(NOW_PM, force=True)
    return rc, sent, notified


def test_premarket_outage_reddens_heartbeat_but_still_emails(monkeypatch):
    rc, sent, notified = _run_pm(monkeypatch, OUT)
    assert rc == 1
    assert "news" in cr._LAST_LANE_FAILURE_DETAIL
    assert notified and notified[0][0] == "premarket"
    assert sent == ["protective"], "the protective email must still go out"


@pytest.mark.parametrize("feed", [OK, None, {"state": "thin", "checked": 2}])
def test_premarket_healthy_has_no_news_failure_and_emails(monkeypatch, feed):
    rc, sent, notified = _run_pm(monkeypatch, feed)
    assert rc == 0 and cr._LAST_LANE_FAILURE_DETAIL is None and notified == []
    assert sent == ["protective"]


def _run_scan(monkeypatch, mp_feed, wl_feed):
    from datetime import date as _date
    from stock_analyzer import reference_data as rd, scanner as real_scanner, gate_ledger
    emails, notified = {}, []
    monkeypatch.setattr(cr, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(rd, "resolve_universe_or_none",
                        lambda name: ({"Tech": ["AAA"]}, _date(2026, 9, 1), None))
    monkeypatch.setattr(real_scanner, "scan_sectors",
                        lambda *a, **k: pd.DataFrame({"Ticker": ["AAA"], "Score": [70.0]}))
    monkeypatch.setattr(cr, "_build_context", lambda _d: {"ok": True, "errors": []})
    monkeypatch.setattr(cr.db, "save_scanner_cache", lambda *_a, **_k: True)
    payload = {"picks": [_bud._pick("AAA", 80)], "errors": [], "built_at": "x",
               "diag": {"tone": "bull"}, "book_drift": None, "macro_coverage": None,
               "grow": {}, "budget_inputs": _bud.BUDGET_INPUTS, "news_feed": mp_feed}
    monkeypatch.setattr(cr, "compute_morning_picks", lambda **_k: payload)
    monkeypatch.setattr(cr, "compute_watchlist_entries", lambda **_k: {
        "entries": [_bud._entry("WLB", 70)], "built_at": "x", "errors": [],
        "reason": None, "gate_degraded": False, "news_feed": wl_feed})
    monkeypatch.setattr(cr.db, "save_recommendations",
                        lambda rows: {"attempted": len(rows), "saved": len(rows), "error": None})
    monkeypatch.setattr(gate_ledger, "build_suppression_rows", lambda *_a, **_k: [])
    monkeypatch.setattr(cr.db, "save_gate_suppressions",
                        lambda _r: {"attempted": 0, "saved": 0, "error": None})
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda **_k: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_send_email",
                        lambda label, s, h: emails.__setitem__(label, h) or True)
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, d: notified.append((lane, d)))
    cols = _bud._TRADE_COLS
    monkeypatch.setattr(cr.db, "load_trades_or_none", lambda: pd.DataFrame(columns=cols))
    monkeypatch.setattr(cr.db, "load_trades", lambda: pd.DataFrame(columns=cols))
    monkeypatch.setattr(cr.db, "load_budget_allocations_or_none", lambda day: {})
    monkeypatch.setattr(cr.db, "save_budget_allocations", _bud._ok_save)
    rc = cr._run_scan(_bud.NOW, force=True)
    return rc, emails, notified


def test_scan_outage_reddens_heartbeat_but_still_sends_both_emails(monkeypatch):
    rc, emails, notified = _run_scan(monkeypatch, OUT, OK)
    assert rc == 1 and "news" in cr._LAST_LANE_FAILURE_DETAIL
    assert notified and notified[0][0] == "scan"
    assert "morning-action" in emails and "watchlist-entries" in emails


def test_scan_healthy_sends_both_emails_with_no_failure(monkeypatch):
    rc, emails, notified = _run_scan(monkeypatch, OK, OK)
    assert rc == 0 and notified == []
    assert "morning-action" in emails and "watchlist-entries" in emails


def test_scan_two_outage_payloads_make_one_failure_line(monkeypatch):
    rc, _, notified = _run_scan(monkeypatch, OUT, OUT)
    assert rc == 1
    assert cr._LAST_LANE_FAILURE_DETAIL.count("news feed:") == 1
    assert len(notified) == 1


def test_scan_watchlist_only_outage_still_fails(monkeypatch):
    rc, _, _ = _run_scan(monkeypatch, OK, OUT)
    assert rc == 1 and "news feed:" in cr._LAST_LANE_FAILURE_DETAIL


def _run_intraday(monkeypatch, feed, picks):
    import stock_analyzer.intraday_entry as ie
    emails, notified = {}, []
    now = cr._ET.localize(datetime.datetime(2026, 10, 16, 11, 35))
    monkeypatch.setattr(cr, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(cr.db, "load_scanner_cache",
                        lambda: {"df": pd.DataFrame({"Ticker": ["AAA"]}),
                                 "scan_date": "2026-10-16"})
    payload = {"picks": picks, "errors": [], "built_at": "x", "book_drift": None,
               "macro_coverage": None, "budget_inputs": _bud.BUDGET_INPUTS, "news_feed": feed}
    monkeypatch.setattr(cr, "compute_morning_picks", lambda **_k: payload)
    monkeypatch.setattr(ie, "fetch_intraday_prices",
                        lambda t: {x: {"current": 98.0, "open": 100.0} for x in t})
    monkeypatch.setattr(ie, "compute_intraday_entries",
                        lambda ps, *_a: [{**p, "intraday_drop_pct": -2.0, "current_price": 98.0,
                                          "open_price": 100.0} for p in ps])
    monkeypatch.setattr(cr.db, "load_alert_state", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_alert_state", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_send_email",
                        lambda label, s, h: emails.__setitem__(label, h) or True)
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, d: notified.append((lane, d)))
    cols = _bud._TRADE_COLS
    monkeypatch.setattr(cr.db, "load_trades_or_none", lambda: pd.DataFrame(columns=cols))
    monkeypatch.setattr(cr.db, "load_budget_allocations_or_none", lambda day: {})
    monkeypatch.setattr(cr.db, "save_budget_allocations", _bud._ok_save)
    rc = cr._run_intraday(now, force=False)
    return rc, emails, notified


def test_intraday_outage_reddens_heartbeat_but_still_emails(monkeypatch):
    rc, emails, notified = _run_intraday(monkeypatch, OUT, [_bud._pick("AAA", 80)])
    assert rc == 1 and "intraday-entry" in emails
    assert "news" in cr._LAST_LANE_FAILURE_DETAIL and notified[0][0] == "intraday"


def test_intraday_healthy_emails_and_is_green(monkeypatch):
    rc, emails, notified = _run_intraday(monkeypatch, OK, [_bud._pick("AAA", 80)])
    assert rc == 0 and "intraday-entry" in emails and notified == []


def test_intraday_outage_with_no_go_picks_still_fails(monkeypatch):
    rc, emails, notified = _run_intraday(monkeypatch, OUT, [])
    assert rc == 1 and emails == {} and notified[0][0] == "intraday"


# -- (i) yahoo_news provider health (System Trust check 3) --------------------
class _Resp:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status

    def json(self):
        return self._b


class _Yf:
    body, status, boom = {"news": []}, 200, None

    def get(self, url, params=None, timeout=30):
        if type(self).boom:
            raise type(self).boom
        return _Resp(type(self).body, type(self).status)


@pytest.fixture
def yn(monkeypatch):
    monkeypatch.setitem(api_health._stats, "yahoo_news", api_health._blank())
    _Yf.body, _Yf.status, _Yf.boom = {"news": []}, 200, None
    monkeypatch.setattr(yp, "_YfData", _Yf)
    return _Yf


def _row():
    return next(r for r in system_health.check_providers() if r["source"] == "yahoo_news")


def test_check_providers_has_a_yahoo_news_row_unknown_when_unused(yn):
    assert _row()["severity"] == "unknown"


def test_five_missing_key_responses_read_red(yn):
    yn.body = {"message": "Not Found"}
    for _ in range(4):
        yp.YFinanceProvider().search_news("ACME", NOW_TS)
    assert _row()["severity"] != "down"
    yp.YFinanceProvider().search_news("ACME", NOW_TS)
    assert _row()["severity"] == "down"


def test_missing_key_non200_and_exception_record_errors(yn):
    yn.body = {"message": "Not Found"}
    yp.YFinanceProvider().search_news("ACME", NOW_TS)
    yn.body, yn.status = {"news": []}, 500
    yp.YFinanceProvider().search_news("ACME", NOW_TS)
    yn.status, yn.boom = 200, RuntimeError("x")
    yp.YFinanceProvider().search_news("ACME", NOW_TS)
    h = api_health.get_health("yahoo_news")
    assert h["errors"] == 3 and h["successes"] == 0


def test_news_key_ok_or_none_records_success_and_resets_streak(yn):
    yn.body = {"message": "Not Found"}
    for _ in range(3):
        yp.YFinanceProvider().search_news("ACME", NOW_TS)
    yn.body = {"news": []}                      # status "none" -> still a success
    yp.YFinanceProvider().search_news("ACME", NOW_TS)
    h = api_health.get_health("yahoo_news")
    assert h["successes"] == 1 and h["consec_err"] == 0


# -- Home publish + registry pins ---------------------------------------------
def _app_src():
    return (ROOT / "app.py").read_text(encoding="utf-8")


def test_home_publishes_news_feed_cache_with_none_sentinel():
    tree = parse_file_cached(ROOT / "app.py")
    assigns = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Subscript)
                and isinstance(n.targets[0].slice, ast.Constant)
                and n.targets[0].slice.value == "_news_feed_cache"):
            assigns.append(n)
    assert len(assigns) == 1
    assert isinstance(assigns[0].value, ast.Name), "must publish a plain local, never `x or {}`"
    src = _app_src()
    seg = src[src.index("_news_feed_state = None"):src.index('st.session_state["_news_feed_cache"]')]
    assert "except Exception:\n        _news_feed_state = None" in seg
    assert not re.search(r"_news_feed_state\s*=\s*\{\}", seg)
    assert "_news_feed.feed_state(" in seg




def test_news_feed_cache_is_deliberately_not_portfolio_dependent():
    """Feed health is not book-derived (a trade does not make it stale) and Summary
    does not read it, so it is NOT in the freshness registry or any surface map --
    the same handling as _broker_drift_cache. Classified in the registry test's
    _SELF_CONSUMED_BY_PRODUCER bucket; still a documented sentinel key."""
    assert "_news_feed_cache" not in coord_freshness.PORTFOLIO_DEPENDENT_KEYS
    assert not any("_news_feed_cache" in keys for keys in coord_freshness.SURFACE_KEYS.values())
    import importlib.util
    spec = importlib.util.spec_from_file_location("ap", ROOT / "scripts" / "check_antipatterns.py")
    ap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ap)
    assert "_news_feed_cache" in ap._SENTINEL_KEYS


# -- producer end: the real headless compute_* functions -----------------------
def test_protective_payload_reports_an_outage_from_its_held_bundles():
    from tests import test_headless_alert_engine as th
    held = {f"H{i}": {"news_status": "none"} for i in range(MIN)}
    ctx = th._ok_ctx([th._port_row("H0", 50.0)], held_data=held)
    result = th._run_protective_alerts(ctx)
    assert result["news_feed"]["state"] == "outage"
    assert result["news_feed"]["checked"] == MIN


def test_protective_payload_control_one_ok_bundle_is_ok():
    from tests import test_headless_alert_engine as th
    held = {f"H{i}": {"news_status": "none"} for i in range(MIN)}
    held["H0"] = {"news_status": "ok"}
    ctx = th._ok_ctx([th._port_row("H0", 50.0)], held_data=held)
    assert th._run_protective_alerts(ctx)["news_feed"]["state"] == "ok"


def test_morning_picks_payload_counts_held_and_grow_candidate_bundles():
    from unittest.mock import patch
    from stock_analyzer import headless_alert_engine as hae
    ctx = {
        "ok": True, "errors": [],
        "port_df": pd.DataFrame({"Ticker": ["H1", "H2"], "Market Value": [1000.0, 1000.0]}),
        "held_data": {"H1": {"news_status": "none"}, "H2": {"news_status": "none"}},
        "fragility": None, "spy_6mo": None, "spy_1y": None, "vix": None,
        "holdings_df": pd.DataFrame({"Ticker": ["H1", "H2"]}),
    }
    brief = {"grow_today": {"tone": "bull", "new_picks": [], "sp500_pct": 1.0}}
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.data.fetch_market_indices",
               return_value=[{"short": "S&P 500", "change_pct": 1.0}]), \
         patch("stock_analyzer.headless_alert_engine.load_bundle",
               side_effect=lambda t, *a, **k: {"news_status": "ok"}), \
         patch("stock_analyzer.data.curate_news_items", return_value=[]), \
         patch("stock_analyzer.macro_calendar.build_macro_calendar", return_value=[]), \
         patch("stock_analyzer.headless_alert_engine.build_daily_briefing", return_value=brief):
        result = hae.compute_morning_picks(
            pd.Timestamp("2026-10-16").date(),
            scanner_results=pd.DataFrame({"Ticker": ["G1", "G2", "G3"]}))
    nfs = result["news_feed"]
    assert nfs["checked"] == 5, "2 held + 3 grow-candidate bundles"
    assert nfs["with_news"] == 3 and nfs["none"] == ["H1", "H2"]


def test_watchlist_payload_lists_a_failed_load_as_unavailable_and_not_checked():
    from tests import test_watchlist_entries_headless as tw

    def _lb(t, *a, **k):
        if t == "BBB":
            raise RuntimeError("bundle boom")
        return {"sector": "Technology", "news_status": "ok" if t == "AAA" else "none"}

    result = tw._run_watchlist_entries(watchlist=("AAA", "BBB", "CCC"), load_bundle_fn=_lb)
    nfs = result["news_feed"]
    assert nfs["checked"] == 2
    assert nfs["unavailable"] == ["BBB"]
    assert nfs["with_news"] == 1 and nfs["none"] == ["CCC"]


# -- partial outages -------------------------------------------------------------
def test_partial_flag_boundaries():
    d = _many("error", MIN)
    d["GOOD"] = _b("ok")
    assert nf.feed_state(d)["partial"] is True and nf.feed_state(d)["state"] == "ok"
    d = _many("error", MIN - 1)
    d["GOOD"] = _b("ok")
    assert nf.feed_state(d)["partial"] is False
    assert nf.feed_state(_many("error", MIN))["partial"] is False       # outage, not partial
    assert nf.feed_state(_many("error", MIN - 1))["partial"] is False   # thin


def test_cached_bundles_count_toward_partial():
    d = {f"C{i}": _b("cached") for i in range(MIN)}
    d["GOOD"] = _b("ok")
    assert nf.feed_state(d)["partial"] is True


def test_premarket_logs_a_partial_feed_without_failing(monkeypatch, capsys):
    part = {"state": "ok", "checked": 18, "with_news": 1, "partial": True,
            "unavailable": [f"T{i}" for i in range(17)], "none": []}
    rc, sent, notified = _run_pm(monkeypatch, part)
    out = capsys.readouterr().out
    assert rc == 0 and notified == [] and cr._LAST_LANE_FAILURE_DETAIL is None
    assert "news feed PARTIAL" in out
    assert sent == ["protective"]


def test_premarket_no_partial_line_for_a_healthy_feed(monkeypatch, capsys):
    _run_pm(monkeypatch, OK)
    assert "PARTIAL" not in capsys.readouterr().out


# -- failure-email dedup for news-only failures ----------------------------------
def _run_pm_dedup(monkeypatch, feed, *, row8_date=None, exit_ok=True):
    payload = {"alerts": [], "built_at": "2026-10-13T08:00:00", "errors": [],
               "all_deterioration_signals": [
                   {"ticker": "AAPL", "tier": "WATCH", "composite_score": 60, "price": 200.0,
                    "dd_from_peak_pct": -8.0, "pnl_pct": 5.0, "below_ma_count": 2,
                    "rel_strength": 0.9}],
               "risk_off_signals": [], "analyst_target_snapshots": [], "score_history": [],
               "split_withheld": ["AAPL"], "news_feed": feed}
    notified, saves = [], []

    def _load(row_id=1, *a, **k):
        if row_id == cr._NEWS_FAILURE_ROW and row8_date:
            return {"last_emailed_date": row8_date, "last_fingerprint": "news_feed"}
        return {}

    monkeypatch.setattr(cr, "compute_protective_alerts", lambda **_k: payload)
    monkeypatch.setattr(cr.db, "load_exit_signals_or_none", lambda **_k: None)
    monkeypatch.setattr(cr.db, "load_alert_state", _load)
    monkeypatch.setattr(cr.db, "save_alert_state",
                        lambda *a, **k: saves.append(k.get("row_id")) or True)
    monkeypatch.setattr(cr.db, "save_exit_signals_batch", lambda *_a, **_k: exit_ok)
    monkeypatch.setattr(cr, "_send_email", lambda *a, **_k: True)
    monkeypatch.setattr(cr, "_notify_failure", lambda lane, d: notified.append((lane, d)))
    rc = cr._run_premarket(NOW_PM, force=True)
    return rc, notified, saves


def test_first_news_only_failure_of_the_day_emails_and_records_dedup(monkeypatch):
    rc, notified, saves = _run_pm_dedup(monkeypatch, OUT)
    assert rc == 1 and len(notified) == 1
    assert cr._NEWS_FAILURE_ROW in saves


def test_second_news_only_failure_the_same_day_is_not_emailed_but_heartbeat_stays_red(monkeypatch):
    rc, notified, saves = _run_pm_dedup(monkeypatch, OUT, row8_date="2026-10-13")
    assert rc == 1 and "news feed:" in cr._LAST_LANE_FAILURE_DETAIL
    assert notified == []
    assert cr._NEWS_FAILURE_ROW not in saves


def test_news_failure_from_yesterday_emails_again(monkeypatch):
    rc, notified, _ = _run_pm_dedup(monkeypatch, OUT, row8_date="2026-10-12")
    assert rc == 1 and len(notified) == 1


def test_a_non_news_failure_still_emails_even_after_a_news_email_today(monkeypatch):
    rc, notified, saves = _run_pm_dedup(monkeypatch, OUT, row8_date="2026-10-13", exit_ok=False)
    assert rc == 1 and len(notified) == 1
    assert "exit_signals" in notified[0][1] and "news feed:" in notified[0][1]
    assert cr._NEWS_FAILURE_ROW not in saves


def test_a_non_news_failure_alone_emails_and_does_not_touch_the_news_row(monkeypatch):
    rc, notified, saves = _run_pm_dedup(monkeypatch, OK, exit_ok=False)
    assert rc == 1 and len(notified) == 1 and cr._NEWS_FAILURE_ROW not in saves


# -- sidebar / App Settings use of overall_level ---------------------------------
def test_overall_level_exclude_judges_price_sources_only(monkeypatch):
    for src in list(api_health._stats):
        monkeypatch.setitem(api_health._stats, src, api_health._blank())
    api_health.record("finnhub", "success")
    for _ in range(5):
        api_health.record("yahoo_news", "error", "x")
    assert api_health.overall_level()[0] == "red"
    assert api_health.overall_level(exclude=("yahoo_news",))[0] == "green"
    assert api_health.overall_level(exclude=())[0] == "red"


def test_app_sidebar_lists_the_news_source_and_app_settings_excludes_it():
    src = _app_src()
    assert '("yahoo_news",    "Yahoo News (yf.Search)")' in src
    assert 'overall_level(exclude=("yahoo_news",))[0] == "red"' in src
    assert '_ah.overall_level()[0] == "red"' not in src
