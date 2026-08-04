"""Regression tests for stock_analyzer/headless_alert_engine.py — exit-discipline
Phase 3's cron-facing protective-alert / morning-picks / EOD engine.

Unlike most modules in this suite this one is a thin orchestration layer over
Supabase (db.*) and live providers (fetch_spy/fetch_vix/fetch_risk_free_rate/
load_bundle/fetch_market_indices/curate_news_items/build_macro_calendar) plus
already-tested pure logic elsewhere (exit_advisor, risk.py, stress_test,
daily_briefing). Tests here mock that I/O boundary and exercise what's actually
this module's own logic: the ok/errors short-circuiting in _build_context, the
stop-breach > deterioration-EXIT > risk-off priority + single-surface dedup in
compute_protective_alerts, the market-tone/diagnostic assembly in
compute_morning_picks, and the snapshot-row filtering + pullback framing in
compute_eod/_assess_pullback. See docs/plans/test-automation.md for scope.
"""
import importlib
import sys
from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

# stock_analyzer.db and .sentiment (imported transitively via
# headless_alert_engine -> db / bundle_loader -> sentiment) hard-import
# streamlit / vaderSentiment at module load time. The dev venv was originally
# bare of these (app only ever runs on Streamlit Cloud/Railway — see CLAUDE.md
# "never run locally"), so fall back to a stub for collection when they're
# genuinely not installed; every call into them this module makes is mocked
# directly in the tests below, so their real behaviour is never exercised
# here regardless. Try a real import first (rather than checking sys.modules
# membership) so that other test files needing the REAL vaderSentiment
# library (test_sentiment.py, test_sentiment_velocity.py) aren't broken by
# collection-order — a bare sys.modules check would permanently clobber the
# real, now-installed module with a MagicMock the first time this file
# happens to collect before those do.
for _mod in ("streamlit", "vaderSentiment", "vaderSentiment.vaderSentiment"):
    if _mod not in sys.modules:
        try:
            importlib.import_module(_mod)
        except ImportError:
            sys.modules[_mod] = MagicMock()

from stock_analyzer import headless_alert_engine as hae


TODAY = date(2026, 7, 27)


# ── _f ────────────────────────────────────────────────────────────────────

def test_f_parses_numeric_string():
    assert hae._f("3.5") == 3.5


def test_f_none_on_none_with_no_default():
    assert hae._f(None) is None


def test_f_returns_default_on_bad_value():
    assert hae._f("not-a-number", default=-1) == -1


def test_f_returns_default_on_none_value():
    assert hae._f(None, default=0) == 0


def test_f_returns_default_on_nan():
    # Regression test: a NaN (e.g. a None coerced by pandas column-dtype
    # promotion) used to parse "successfully" via float(nan) and pass straight
    # through instead of falling back to `default`. Found+fixed 2026-07-27.
    assert hae._f(float("nan"), default=-1) == -1


def test_f_none_on_nan_with_no_default():
    assert hae._f(float("nan")) is None


# ── _vix_level ──────────────────────────────────────────────────────────

def test_vix_level_none_on_fetch_exception():
    with patch("stock_analyzer.headless_alert_engine.fetch_vix", side_effect=RuntimeError("boom")):
        assert hae._vix_level() is None


def test_vix_level_none_on_none_result():
    with patch("stock_analyzer.headless_alert_engine.fetch_vix", return_value=None):
        assert hae._vix_level() is None


def test_vix_level_none_on_empty_frame():
    with patch("stock_analyzer.headless_alert_engine.fetch_vix", return_value=pd.DataFrame()):
        assert hae._vix_level() is None


def test_vix_level_none_on_missing_close_column():
    df = pd.DataFrame({"Open": [1.0, 2.0]})
    with patch("stock_analyzer.headless_alert_engine.fetch_vix", return_value=df):
        assert hae._vix_level() is None


def test_vix_level_returns_last_close():
    df = pd.DataFrame({"Close": [14.0, 15.0, 16.5]})
    with patch("stock_analyzer.headless_alert_engine.fetch_vix", return_value=df):
        assert hae._vix_level() == 16.5


# ── _assess_pullback ────────────────────────────────────────────────────

def _spy(closes):
    return pd.DataFrame({"Close": closes})


def test_assess_pullback_none_when_spy_is_none():
    assert hae._assess_pullback(None, {}, -3.0) is None


def test_assess_pullback_none_when_spy_empty():
    assert hae._assess_pullback(pd.DataFrame(), {}, -3.0) is None


def test_assess_pullback_none_when_missing_close_column():
    df = pd.DataFrame({"Open": [1.0, 2.0]})
    assert hae._assess_pullback(df, {}, -3.0) is None


def test_assess_pullback_none_when_fewer_than_two_rows():
    assert hae._assess_pullback(_spy([100.0]), {}, -3.0) is None


def test_assess_pullback_none_when_prev_close_non_positive():
    assert hae._assess_pullback(_spy([0.0, 95.0]), {}, -3.0) is None


def test_assess_pullback_none_when_drop_short_of_threshold():
    # -1% move, threshold -3% -> not deep enough, no fire.
    assert hae._assess_pullback(_spy([100.0, 99.0]), {}, -3.0) is None


def test_assess_pullback_fires_at_exact_threshold():
    df = _spy([100.0, 97.0])  # exactly -3.0%
    result = hae._assess_pullback(df, {}, -3.0)
    assert result is not None
    assert result["index_pct"] == -3.0


def test_assess_pullback_fires_and_frames_with_fragility_mult():
    df = _spy([100.0, 95.0])  # -5.0%
    fragility = {"mult": 1.4, "severity": "elevated", "exposed": ["AAPL", "MSFT"]}
    result = hae._assess_pullback(df, fragility, -3.0)
    assert result["index_pct"] == -5.0
    assert result["book_implied_pct"] == -7.0
    assert result["severity"] == "elevated"
    assert result["mult"] == 1.4
    assert result["exposed"] == ["AAPL", "MSFT"]


def test_assess_pullback_book_implied_none_without_mult():
    df = _spy([100.0, 95.0])
    result = hae._assess_pullback(df, {}, -3.0)
    assert result["book_implied_pct"] is None
    assert result["exposed"] == []


def test_assess_pullback_none_on_internal_exception():
    class Boom(pd.DataFrame):
        @property
        def empty(self):
            raise RuntimeError("boom")
    assert hae._assess_pullback(Boom({"Close": [1.0, 2.0]}), {}, -3.0) is None


# ── _build_context ───────────────────────────────────────────────────────

def _patch_context_deps(**overrides):
    """Baseline set of patches for a "happy path" _build_context call; pass
    keyword overrides (target short-name -> value/side_effect) to redirect
    specific ones for a given test."""
    defaults = dict(
        has_db=True,
        load_holdings=pd.DataFrame({"Ticker": ["AAPL"]}),
        load_trades=None,
        load_manual_stops={},
        fetch_risk_free_rate=0.045,
        fetch_spy_6mo=pd.DataFrame({"Close": [100.0, 101.0]}),
        fetch_spy_1y=pd.DataFrame({"Close": [90.0, 101.0]}),
        vix_level=16.0,
        load_bundle={"financials": {}},
        build_open_lots=[],
        material_add_window_days=None,
        build_portfolio_df=pd.DataFrame({"Ticker": ["AAPL"], "Weight (%)": [10.0]}),
        compute_portfolio_risk_metrics={"beta": 1.1},
        run_scenario={"port_impact_pct": -5.0},
        assess_fragility={"severity": "elevated", "mult": 1.2, "exposed": ["AAPL"]},
    )
    defaults.update(overrides)
    return defaults


def _run_build_context(cfg):
    with patch("stock_analyzer.headless_alert_engine.db.has_db", return_value=cfg["has_db"]), \
         patch("stock_analyzer.headless_alert_engine.db.load_holdings",
               return_value=cfg["load_holdings"], side_effect=cfg.get("load_holdings_side_effect")), \
         patch("stock_analyzer.headless_alert_engine.db.load_trades",
               return_value=cfg["load_trades"]), \
         patch("stock_analyzer.headless_alert_engine.db.load_manual_stops",
               return_value=cfg["load_manual_stops"]), \
         patch("stock_analyzer.headless_alert_engine.fetch_risk_free_rate",
               return_value=cfg["fetch_risk_free_rate"], side_effect=cfg.get("rfr_side_effect")), \
         patch("stock_analyzer.headless_alert_engine.fetch_spy",
               side_effect=cfg.get("fetch_spy_side_effect") or
               [cfg["fetch_spy_6mo"], cfg["fetch_spy_1y"]]), \
         patch("stock_analyzer.headless_alert_engine._vix_level",
               return_value=cfg["vix_level"]), \
         patch("stock_analyzer.headless_alert_engine.load_bundle",
               return_value=cfg["load_bundle"], side_effect=cfg.get("load_bundle_side_effect")), \
         patch("stock_analyzer.headless_alert_engine._build_open_lots",
               return_value=cfg["build_open_lots"]), \
         patch("stock_analyzer.exit_advisor.material_add_window_days",
               return_value=cfg["material_add_window_days"]), \
         patch("stock_analyzer.headless_alert_engine.build_portfolio_df",
               return_value=cfg["build_portfolio_df"]), \
         patch("stock_analyzer.headless_alert_engine.compute_portfolio_risk_metrics",
               return_value=cfg["compute_portfolio_risk_metrics"]), \
         patch("stock_analyzer.headless_alert_engine.run_scenario",
               return_value=cfg["run_scenario"]), \
         patch("stock_analyzer.headless_alert_engine.assess_fragility",
               return_value=cfg["assess_fragility"]):
        return hae._build_context(TODAY)


def test_build_context_no_db_short_circuits():
    result = _run_build_context(_patch_context_deps(has_db=False))
    assert result["ok"] is False
    assert "no Supabase credentials" in result["errors"][0]


def test_build_context_load_holdings_exception():
    cfg = _patch_context_deps()
    cfg["load_holdings_side_effect"] = RuntimeError("db down")
    result = _run_build_context(cfg)
    assert result["ok"] is False
    assert "load_holdings failed" in result["errors"][0]


def test_build_context_no_holdings():
    result = _run_build_context(_patch_context_deps(load_holdings=pd.DataFrame({"Ticker": []})))
    assert result["ok"] is False
    assert "no holdings" in result["errors"]


def test_build_context_all_bundles_fail_to_load():
    cfg = _patch_context_deps()
    cfg["load_bundle_side_effect"] = RuntimeError("provider down")
    result = _run_build_context(cfg)
    assert result["ok"] is False
    assert "no holdings could be loaded" in result["errors"]
    assert any("bundle load failed" in e for e in result["errors"])


def test_build_context_port_df_empty_after_load():
    result = _run_build_context(_patch_context_deps(build_portfolio_df=pd.DataFrame()))
    assert result["ok"] is False
    assert "portfolio frame empty after load" in result["errors"]


def test_build_context_happy_path():
    result = _run_build_context(_patch_context_deps())
    assert result["ok"] is True
    assert result["errors"] == []
    assert not result["port_df"].empty
    assert "AAPL" in result["held_data"]
    assert result["fragility"]["severity"] == "elevated"
    assert result["vix"] == 16.0


def test_build_context_fragility_none_when_beta_is_nan():
    # Regression test for the 2026-07-27 Opus-review follow-up (fixed
    # 2026-07-28): `beta = port_risk.get("beta")` didn't route through the
    # NaN-aware `_f()` helper, so a NaN beta (e.g. pandas column-dtype
    # promotion) would pass the `is not None` guard and get admitted into
    # run_scenario/assess_fragility instead of correctly skipping fragility.
    result = _run_build_context(_patch_context_deps(
        compute_portfolio_risk_metrics={"beta": float("nan")}
    ))
    assert result["fragility"] is None


def test_build_context_fragility_none_when_beta_missing():
    result = _run_build_context(_patch_context_deps(compute_portfolio_risk_metrics={"beta": None}))
    assert result["ok"] is True
    assert result["fragility"] is None


def test_build_context_rfr_fetch_failure_falls_back():
    cfg = _patch_context_deps()
    cfg["rfr_side_effect"] = RuntimeError("rate feed down")
    result = _run_build_context(cfg)
    assert result["ok"] is True  # non-fatal — held_data still builds


def test_build_context_spy_6mo_fetch_failure_appends_error_but_continues():
    cfg = _patch_context_deps()
    cfg["fetch_spy_side_effect"] = [RuntimeError("spy down"), cfg["fetch_spy_1y"]]
    result = _run_build_context(cfg)
    assert result["ok"] is True
    assert "SPY 6mo fetch failed" in result["errors"]
    assert result["spy_6mo"] is None


def test_build_context_position_age_days_set_from_open_lots():
    cfg = _patch_context_deps(load_trades=pd.DataFrame({"x": [1]}))
    cfg["build_open_lots"] = [{"days_held": 30}, {"days_held": 10}]
    result = _run_build_context(cfg)
    assert result["held_data"]["AAPL"]["position_age_days"] == 30


def test_build_context_position_age_days_none_when_no_trades():
    result = _run_build_context(_patch_context_deps(load_trades=None))
    assert result["held_data"]["AAPL"]["position_age_days"] is None


# ── compute_protective_alerts ─────────────────────────────────────────────

def _port_row(ticker, gap_to_stop, price=100.0, shares=10, stop=90.0,
              weight=10.0, pnl_pct=5.0, stop_type="Trailing"):
    return {
        "Ticker": ticker, "Gap to Stop (%)": gap_to_stop, "Price": price,
        "Shares": shares, "Stop": stop, "Weight (%)": weight, "P&L (%)": pnl_pct,
        "Stop Type": stop_type, "Score": 60.0,
    }


def _run_protective_alerts(ctx, **patches):
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.headless_alert_engine.deterioration_signals",
               return_value=patches.get("det", [])), \
         patch("stock_analyzer.exit_advisor.assess_risk_off_derisk",
               return_value=patches.get("risk_off", [])):
        return hae.compute_protective_alerts(TODAY)


def _ok_ctx(port_rows, held_data=None):
    return {
        "ok": True, "errors": [], "port_df": pd.DataFrame(port_rows),
        "held_data": held_data or {}, "fragility": None,
        "spy_6mo": None, "spy_1y": None, "vix": None,
    }


def test_protective_alerts_ctx_not_ok_returns_empty():
    ctx = {"ok": False, "errors": ["no Supabase credentials"]}
    result = _run_protective_alerts(ctx)
    assert result["alerts"] == []
    assert result["errors"] == ["no Supabase credentials"]


def test_protective_alerts_stop_breach_detected():
    ctx = _ok_ctx([_port_row("AAPL", gap_to_stop=-2.0)])
    result = _run_protective_alerts(ctx)
    assert len(result["alerts"]) == 1
    assert result["alerts"][0]["kind"] == "stop_breach"
    assert result["alerts"][0]["ticker"] == "AAPL"


def test_protective_alerts_no_breach_when_gap_positive_or_none():
    ctx = _ok_ctx([_port_row("AAPL", gap_to_stop=3.0), _port_row("MSFT", gap_to_stop=None)])
    result = _run_protective_alerts(ctx)
    assert result["alerts"] == []


def test_protective_alerts_no_breach_when_none_gap_pandas_coerced_to_nan():
    # Regression test for a real bug found+fixed 2026-07-27: a "Stop
    # Unavailable" ticker (gap_to_stop=None, set explicitly by portfolio.py
    # when no stop could be computed) sitting in the same port_df as any
    # ticker with a real numeric stop gets its None silently promoted to NaN
    # by pandas (mixed dtype column). float(nan) doesn't raise, so the old
    # `_f()` returned NaN instead of falling back to the default -- the
    # `gap is None` guard in the stop-breach loop never caught it, and NaN > 0
    # is also False, so the row fell through and fabricated a bogus
    # "SELL -- Stop Breached" alert for a ticker whose stop was actually
    # unknown.
    ctx = _ok_ctx([_port_row("AAPL", gap_to_stop=3.0), _port_row("MSFT", gap_to_stop=None)])
    assert ctx["port_df"]["Gap to Stop (%)"].dtype.kind == "f"  # confirms real coercion, not object/None
    result = _run_protective_alerts(ctx)
    assert result["alerts"] == []


def test_protective_alerts_deterioration_exit_only_exit_tier():
    ctx = _ok_ctx([_port_row("AAPL", gap_to_stop=5.0)])
    det = [
        {"ticker": "AAPL", "tier": "WATCH", "dd_from_peak_pct": -8, "trend_ma": 50,
         "exit_floor": -20, "pnl_pct": -5, "weight_pct": 10},
        {"ticker": "MSFT", "tier": "EXIT", "dd_from_peak_pct": -25, "trend_ma": 200,
         "exit_floor": -20, "pnl_pct": -22, "weight_pct": 8},
    ]
    result = _run_protective_alerts(ctx, det=det)
    assert len(result["alerts"]) == 1
    assert result["alerts"][0]["kind"] == "deterioration_exit"
    assert result["alerts"][0]["ticker"] == "MSFT"


def test_protective_alerts_dedup_stop_breach_wins_over_deterioration_exit():
    ctx = _ok_ctx([_port_row("AAPL", gap_to_stop=-1.0)])
    det = [{"ticker": "AAPL", "tier": "EXIT", "dd_from_peak_pct": -30, "trend_ma": 200,
            "exit_floor": -20, "pnl_pct": -28, "weight_pct": 12}]
    result = _run_protective_alerts(ctx, det=det)
    assert len(result["alerts"]) == 1
    assert result["alerts"][0]["kind"] == "stop_breach"


def test_protective_alerts_risk_off_excludes_already_reduced_tickers():
    ctx = _ok_ctx([_port_row("AAPL", gap_to_stop=-1.0)])
    risk_off = [{"ticker": "MSFT", "action": "TRIM — Risk-Off", "directive": "trim",
                 "why": "beta ceiling", "trigger": "risk-off", "weight": 15, "pnl_pct": 2}]

    captured = {}

    def _fake_risk_off(port_df, held_data, fragility=None, spy_trend_df=None,
                        vix_level=None, exclude_tickers=None):
        captured["exclude_tickers"] = set(exclude_tickers)
        return risk_off

    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.headless_alert_engine.deterioration_signals", return_value=[]), \
         patch("stock_analyzer.exit_advisor.assess_risk_off_derisk", side_effect=_fake_risk_off):
        result = hae.compute_protective_alerts(TODAY)

    assert captured["exclude_tickers"] == {"AAPL"}
    kinds = [a["kind"] for a in result["alerts"]]
    assert kinds == ["stop_breach", "risk_off_derisk"]
    assert result["alerts"][1]["ticker"] == "MSFT"


def test_protective_alerts_deterioration_signals_exception_appends_error():
    ctx = _ok_ctx([_port_row("AAPL", gap_to_stop=5.0)])
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.headless_alert_engine.deterioration_signals",
               side_effect=RuntimeError("boom")), \
         patch("stock_analyzer.exit_advisor.assess_risk_off_derisk", return_value=[]):
        result = hae.compute_protective_alerts(TODAY)
    assert result["alerts"] == []
    assert any("deterioration_signals failed" in e for e in result["errors"])


def test_protective_alerts_risk_off_exception_appends_error():
    ctx = _ok_ctx([_port_row("AAPL", gap_to_stop=5.0)])
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.headless_alert_engine.deterioration_signals", return_value=[]), \
         patch("stock_analyzer.exit_advisor.assess_risk_off_derisk",
               side_effect=RuntimeError("boom")):
        result = hae.compute_protective_alerts(TODAY)
    assert result["alerts"] == []
    assert any("assess_risk_off_derisk failed" in e for e in result["errors"])


def test_protective_alerts_composite_score_enrichment():
    ctx = _ok_ctx([_port_row("AAPL", gap_to_stop=5.0)])
    ctx["port_df"].loc[0, "Score"] = 72.0
    det = [{"ticker": "AAPL", "tier": "WATCH", "dd_from_peak_pct": -8, "trend_ma": 50,
            "exit_floor": -20, "pnl_pct": -5, "weight_pct": 10}]
    result = _run_protective_alerts(ctx, det=det)
    assert result["all_deterioration_signals"][0]["composite_score"] == 72.0


def test_protective_alerts_analyst_target_snapshot_built_and_skips_stale():
    ctx = _ok_ctx(
        [_port_row("AAPL", gap_to_stop=5.0), _port_row("MSFT", gap_to_stop=5.0)],
        held_data={
            "AAPL": {"financials": {"analyst_target": 210.0, "num_analyst_opinions": 12},
                     "info_source": "yfinance", "stale_as_of": None},
            "MSFT": {"financials": {"analyst_target": 480.0}, "stale_as_of": "2026-07-25"},
        },
    )
    result = _run_protective_alerts(ctx)
    snaps = result["analyst_target_snapshots"]
    assert len(snaps) == 1
    assert snaps[0]["ticker"] == "AAPL"
    assert snaps[0]["target_mean"] == 210.0
    assert snaps[0]["snapshot_date"] == TODAY.isoformat()


def test_protective_alerts_analyst_target_snapshot_skips_missing_target():
    ctx = _ok_ctx(
        [_port_row("AAPL", gap_to_stop=5.0)],
        held_data={"AAPL": {"financials": {}, "stale_as_of": None}},
    )
    result = _run_protective_alerts(ctx)
    assert result["analyst_target_snapshots"] == []


def test_protective_alerts_analyst_target_snapshot_skips_nan_target():
    # Regression test for the 2026-07-27 Opus-review follow-up (fixed
    # 2026-07-28): `target_mean = fin.get("analyst_target")` bypassed the
    # NaN-aware `_f()` helper, so a NaN target from yfinance would pass the
    # `is None` guard and get persisted -- poisoning a future day-over-day
    # analyst-target comparison. Log-only Phase 1, but the fix closes the
    # gap before anything downstream reads this table.
    ctx = _ok_ctx(
        [_port_row("AAPL", gap_to_stop=5.0)],
        held_data={"AAPL": {"financials": {"analyst_target": float("nan")}, "stale_as_of": None}},
    )
    result = _run_protective_alerts(ctx)
    assert result["analyst_target_snapshots"] == []


# ── compute_morning_picks ─────────────────────────────────────────────────

def _scanner_df(tickers):
    return pd.DataFrame({"Ticker": tickers})


def test_morning_picks_no_scanner_results_short_circuits():
    result = hae.compute_morning_picks(TODAY, scanner_results=None)
    assert result["picks"] == []
    assert "no scanner results" in result["errors"]


def test_morning_picks_empty_scanner_results_short_circuits():
    result = hae.compute_morning_picks(TODAY, scanner_results=pd.DataFrame())
    assert result["picks"] == []


def test_morning_picks_ctx_not_ok_short_circuits():
    with patch("stock_analyzer.headless_alert_engine._build_context",
               return_value={"ok": False, "errors": ["no holdings"]}):
        result = hae.compute_morning_picks(TODAY, scanner_results=_scanner_df(["NVDA"]))
    assert result["picks"] == []
    assert result["errors"] == ["no holdings"]


def _run_morning_picks(sp_pct, brief_overrides=None):
    ctx = {
        "ok": True, "errors": [], "port_df": pd.DataFrame({"Ticker": ["AAPL"], "Market Value": [1000.0]}),
        "held_data": {"AAPL": {}}, "fragility": None, "spy_6mo": None, "spy_1y": None, "vix": None,
    }
    grow = {"tone": "bull", "new_picks": [{"ticker": "NVDA"}], "sp500_pct": sp_pct,
            "sector_blocked_picks": [], "macro_blocked_picks": [],
            "composite_skipped": [], "composite_unavailable": []}
    grow.update(brief_overrides or {})
    brief = {"grow_today": grow}
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.data.fetch_market_indices",
               return_value=[{"short": "S&P 500", "change_pct": sp_pct},
                             {"short": "NASDAQ", "change_pct": sp_pct}]), \
         patch("stock_analyzer.headless_alert_engine.load_bundle", return_value={}), \
         patch("stock_analyzer.data.curate_news_items", return_value=[]), \
         patch("stock_analyzer.macro_calendar.build_macro_calendar", return_value=[]), \
         patch("stock_analyzer.headless_alert_engine.build_daily_briefing", return_value=brief):
        return hae.compute_morning_picks(TODAY, scanner_results=_scanner_df(["NVDA"]))


def test_morning_picks_bull_tone_uses_composite_buy_bar():
    result = _run_morning_picks(sp_pct=1.0, brief_overrides={"tone": "bull"})
    assert result["diag"]["tone"] == "bull"
    assert result["diag"]["bar"] == hae.COMPOSITE_BUY
    assert result["picks"] == [{"ticker": "NVDA"}]


def test_morning_picks_flat_tone_uses_flat_day_bar():
    result = _run_morning_picks(sp_pct=0.1, brief_overrides={"tone": "flat"})
    assert result["diag"]["bar"] == hae.COMPOSITE_BUY_FLAT_DAY


def test_morning_picks_bear_tone_has_no_bar():
    result = _run_morning_picks(sp_pct=-1.0, brief_overrides={"tone": "bear"})
    assert result["diag"]["bar"] is None


def test_morning_picks_bear_tone_sp500_pct_falls_back_to_market_context():
    """_grow_today's real bear-day early return omits "sp500_pct" entirely (it
    only builds a message string) -- diag must still surface the real S&P
    move via market_context rather than logging it as "n/a" (2026-08-04
    reviewer non-blocking note on the grow_today-unwrap fix)."""
    ctx = {
        "ok": True, "errors": [], "port_df": pd.DataFrame({"Ticker": ["AAPL"], "Market Value": [1000.0]}),
        "held_data": {"AAPL": {}}, "fragility": None, "spy_6mo": None, "spy_1y": None, "vix": None,
    }
    # Real bear-branch shape: no "sp500_pct" key at all.
    brief = {"grow_today": {"tone": "bear", "new_picks": []}}
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.data.fetch_market_indices",
               return_value=[{"short": "S&P 500", "change_pct": -1.75},
                             {"short": "NASDAQ", "change_pct": -2.1}]), \
         patch("stock_analyzer.headless_alert_engine.load_bundle", return_value={}), \
         patch("stock_analyzer.data.curate_news_items", return_value=[]), \
         patch("stock_analyzer.macro_calendar.build_macro_calendar", return_value=[]), \
         patch("stock_analyzer.headless_alert_engine.build_daily_briefing", return_value=brief):
        result = hae.compute_morning_picks(TODAY, scanner_results=_scanner_df(["NVDA"]))
    assert result["diag"]["sp500_pct"] == -1.75


def test_morning_picks_diag_counts_blocked_lists():
    result = _run_morning_picks(sp_pct=1.0, brief_overrides={
        "sector_blocked_picks": [{"ticker": "X"}],
        "macro_blocked_picks": [{"ticker": "Y"}, {"ticker": "Z"}],
    })
    assert result["diag"]["sector_blocked"] == 1
    assert result["diag"]["macro_blocked"] == 2


def test_morning_picks_market_tone_fetch_failure_falls_back_to_flat():
    ctx = {
        "ok": True, "errors": [], "port_df": pd.DataFrame({"Ticker": ["AAPL"], "Market Value": [1000.0]}),
        "held_data": {"AAPL": {}}, "fragility": None, "spy_6mo": None, "spy_1y": None, "vix": None,
    }
    brief = {"grow_today": {"tone": "flat", "new_picks": [], "sp500_pct": 0.0}}
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.data.fetch_market_indices", side_effect=RuntimeError("down")), \
         patch("stock_analyzer.headless_alert_engine.load_bundle", return_value={}), \
         patch("stock_analyzer.data.curate_news_items", return_value=[]), \
         patch("stock_analyzer.macro_calendar.build_macro_calendar", return_value=[]), \
         patch("stock_analyzer.headless_alert_engine.build_daily_briefing", return_value=brief):
        result = hae.compute_morning_picks(TODAY, scanner_results=_scanner_df(["NVDA"]))
    assert any("market tone fetch failed" in e for e in result["errors"])


def test_morning_picks_build_daily_briefing_exception_returns_empty():
    ctx = {
        "ok": True, "errors": [], "port_df": pd.DataFrame({"Ticker": ["AAPL"], "Market Value": [1000.0]}),
        "held_data": {"AAPL": {}}, "fragility": None, "spy_6mo": None, "spy_1y": None, "vix": None,
    }
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.data.fetch_market_indices", return_value=[]), \
         patch("stock_analyzer.headless_alert_engine.load_bundle", return_value={}), \
         patch("stock_analyzer.data.curate_news_items", return_value=[]), \
         patch("stock_analyzer.macro_calendar.build_macro_calendar", return_value=[]), \
         patch("stock_analyzer.headless_alert_engine.build_daily_briefing",
               side_effect=RuntimeError("boom")):
        result = hae.compute_morning_picks(TODAY, scanner_results=_scanner_df(["NVDA"]))
    assert result["picks"] == []
    assert any("build_daily_briefing failed" in e for e in result["errors"])


# ── compute_eod ────────────────────────────────────────────────────────────

def test_compute_eod_ctx_not_ok_short_circuits():
    with patch("stock_analyzer.headless_alert_engine._build_context",
               return_value={"ok": False, "errors": ["no holdings"]}):
        result = hae.compute_eod(TODAY)
    assert result["snapshot_rows"] == []
    assert result["pullback"] is None
    assert result["errors"] == ["no holdings"]


def test_compute_eod_snapshot_rows_filters_invalid_price_or_shares():
    ctx = {
        "ok": True, "errors": [],
        "port_df": pd.DataFrame([
            {"Ticker": "AAPL", "Price": 200.0, "Shares": 10},
            {"Ticker": "ZERO", "Price": 0.0, "Shares": 5},
            {"Ticker": "NOSHARES", "Price": 50.0, "Shares": 0},
            {"Ticker": "NONE", "Price": None, "Shares": 5},
        ]),
        "held_data": {}, "fragility": None, "spy_6mo": None,
    }
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx):
        result = hae.compute_eod(TODAY)
    assert result["snapshot_rows"] == [{"ticker": "AAPL", "shares": 10.0, "close_price": 200.0}]


def test_compute_eod_attaches_pullback_reading():
    ctx = {
        "ok": True, "errors": [],
        "port_df": pd.DataFrame([{"Ticker": "AAPL", "Price": 200.0, "Shares": 10}]),
        "held_data": {}, "fragility": {"mult": 1.2, "severity": "elevated", "exposed": []},
        "spy_6mo": _spy([100.0, 95.0]),
    }
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx):
        result = hae.compute_eod(TODAY, pullback_threshold=-3.0)
    assert result["pullback"]["index_pct"] == -5.0
    assert result["pullback"]["severity"] == "elevated"


def test_compute_eod_pullback_none_on_calm_market():
    ctx = {
        "ok": True, "errors": [],
        "port_df": pd.DataFrame([{"Ticker": "AAPL", "Price": 200.0, "Shares": 10}]),
        "held_data": {}, "fragility": None,
        "spy_6mo": _spy([100.0, 100.5]),
    }
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx):
        result = hae.compute_eod(TODAY, pullback_threshold=-3.0)
    assert result["pullback"] is None
