"""RiskListwiseYoung C2: the headless context carries the book risk coverage and a
young holding no longer blanks the portfolio beta the 09:45 Watchlist email needs.

Spec: docs/plans/risk-young-holding-c2-spec.md section 5 / 8.7. The risk metrics,
the scenario and the fragility read are the REAL code; only the I/O boundary
(db, providers, bundles) is patched, as in tests/test_headless_alert_engine.py.
"""
import importlib
import sys
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

for _mod in ("streamlit", "vaderSentiment", "vaderSentiment.vaderSentiment"):
    if _mod not in sys.modules:
        try:
            importlib.import_module(_mod)
        except ImportError:
            sys.modules[_mod] = MagicMock()

from stock_analyzer import headless_alert_engine as hae
from stock_analyzer.watchlist_advisor import _portfolio_risk_gate
from tests.test_risk_window import N_BOOK, _frame

TODAY = date(2026, 11, 26)
_H = "stock_analyzer.headless_alert_engine."


def _book(young_pct, young_closes=12):
    """A 3-name book (two long, one young) as (holdings_df, bundles, port_df)."""
    big = 100.0 - young_pct
    wts = {"AAA": big * 0.6, "BBB": big * 0.4, "YNG": young_pct}
    frames = {"AAA": _frame(N_BOOK, 1), "BBB": _frame(N_BOOK, 2),
              "YNG": _frame(young_closes, 5)}
    holdings = pd.DataFrame({"Ticker": list(wts)})
    bundles = {t: {"df": f, "beta": 1.1} for t, f in frames.items()}
    port = pd.DataFrame([
        {"Ticker": t, "Sector": "Healthcare", "Weight (%)": w,
         "Market Value": w * 100.0, "Price": 100.0}
        for t, w in wts.items()
    ])
    return holdings, bundles, port


def _build_ctx(young_pct):
    holdings, bundles, port = _book(young_pct)
    spy = _frame(N_BOOK, 99)
    with patch(_H + "db.has_db", return_value=True), \
         patch(_H + "db.load_holdings_or_none", return_value=holdings), \
         patch(_H + "db.load_trades_or_none", return_value=None), \
         patch(_H + "_held_stop_wiring.build_held_stops", return_value={}), \
         patch(_H + "_young_holding_stop.plain_notes", return_value=[]), \
         patch(_H + "db.load_held_stop_floors_or_none", return_value={}), \
         patch(_H + "db.load_held_stop_carry_or_none", return_value={}), \
         patch(_H + "db.load_manual_stops_or_none", return_value={}), \
         patch(_H + "fetch_risk_free_rate", return_value=0.045), \
         patch(_H + "fetch_spy", return_value=spy), \
         patch(_H + "_vix_level", return_value=16.0), \
         patch(_H + "load_bundle", side_effect=lambda t, *a, **k: dict(bundles[t])), \
         patch(_H + "_build_open_lots", return_value=[]), \
         patch("stock_analyzer.exit_advisor.material_add_window_days", return_value=None), \
         patch(_H + "build_portfolio_df", return_value=port):
        return hae._build_context(TODAY)


def test_young_holding_under_the_cap_keeps_beta_fragility_and_reports_partial():
    ctx = _build_ctx(3.6)
    assert ctx["ok"] is True
    assert ctx["port_risk"] is not None and ctx["port_risk"]["beta"] is not None
    assert ctx["fragility"] is not None              # the beta fed the scenario
    assert ctx["risk_coverage"]["state"] == "partial"
    assert [x["ticker"] for x in ctx["risk_coverage"]["left_out"]] == ["YNG"]


def test_young_holding_over_the_cap_is_unmeasured_with_coverage_still_set():
    ctx = _build_ctx(15.0)
    assert ctx["ok"] is True
    assert ctx["port_risk"] is None                  # offline sentinel, never {}
    assert ctx["fragility"] is None
    assert ctx["risk_coverage"]["state"] == "unmeasured"


def test_risk_computation_raising_leaves_port_risk_none_and_coverage_none():
    with patch(_H + "compute_portfolio_risk_metrics", side_effect=RuntimeError("x")):
        ctx = _build_ctx(3.6)
    assert ctx["port_risk"] is None
    assert ctx["risk_coverage"] is None              # nothing was built at all


def test_risk_computation_raising_after_filling_coverage_resets_it_to_none():
    # The builder filled coverage_out and THEN raised: the half-built coverage
    # must not read full / partial beside a None port_risk.
    def _fill_then_raise(port_df, held_data, spy, rfr, coverage_out=None, **kw):
        coverage_out.update({"state": "partial", "left_out": []})
        raise RuntimeError("late failure")

    with patch(_H + "compute_portfolio_risk_metrics", side_effect=_fill_then_raise):
        ctx = _build_ctx(3.6)
    assert ctx["port_risk"] is None
    assert ctx["risk_coverage"] is None


# ── through compute_watchlist_entries ────────────────────────────────────────
def _watchlist_run(ctx):
    """compute_watchlist_entries over ctx; the recommendation builder is the REAL
    portfolio-risk gate fed whatever portfolio_ctx the engine builds, for a
    watchlist name with beta 1.9."""
    seen = {}

    def _rec(ticker, data, portfolio_ctx=None, entry_ctx=None):
        seen["ctx"] = portfolio_ctx
        gate = _portfolio_risk_gate(1.9, portfolio_ctx)
        held = gate is not None and gate.get("severity") == "hard"
        return {"ticker": ticker, "action": "NEAR_ENTRY" if held else "ENTER_NOW",
                "suppression_kind": gate["kind"] if held else None,
                "score": 70.0, "price": 100.0, "entry_lo": 95.0, "entry_hi": 105.0,
                "stop": 90.0, "rr": 2.5, "summary": "s"}

    with patch(_H + "db.load_watchlist_or_none", return_value=["NEWW"]), \
         patch(_H + "db.load_holdings_or_none", return_value=pd.DataFrame({"Ticker": ["AAA", "BBB", "YNG"]})), \
         patch(_H + "fetch_risk_free_rate", return_value=0.045), \
         patch(_H + "fetch_spy", return_value=None), \
         patch(_H + "load_bundle", return_value={"sector": "Technology"}), \
         patch(_H + "build_watchlist_recommendation", side_effect=_rec), \
         patch(_H + "db.load_exit_signals_or_none", return_value=pd.DataFrame()), \
         patch(_H + "db.save_recommendations",
               side_effect=lambda rows: {"attempted": len(rows), "saved": len(rows), "error": None}), \
         patch(_H + "is_trading_day", return_value=True), \
         patch(_H + "_live_entry_context",
               return_value={"market_context": {"tone": "flat", "sp500_pct": -0.2,
                                                 "tone_available": True},
                             "macro_events": []}), \
         patch(_H + "db.load_gate_holds_on_day_or_none", return_value={}), \
         patch(_H + "db.load_trades_or_none", return_value=pd.DataFrame()), \
         patch(_H + "db.load_recommendations_or_none", return_value=pd.DataFrame()), \
         patch("stock_analyzer.data.fetch_market_indices", side_effect=RuntimeError("no net")), \
         patch("stock_analyzer.macro_calendar.build_macro_calendar", side_effect=RuntimeError("no net")):
        return hae.compute_watchlist_entries(TODAY, ctx=ctx), seen


def test_watchlist_beta_is_read_with_a_young_holding_under_the_cap():
    result, seen = _watchlist_run(_build_ctx(3.6))
    assert result["beta_read"] is True
    assert seen["ctx"]["portfolio_beta"] is not None
    assert result["checks_held"]["beta_unknown"] == []


def test_watchlist_beta_unknown_when_the_young_holding_is_over_the_cap():
    # port_risk None -> the beta check could not run -> a beta 1.9 name is HELD
    result, seen = _watchlist_run(_build_ctx(15.0))
    assert result["beta_read"] is False
    assert seen["ctx"]["portfolio_beta"] is None
    assert result["checks_held"]["beta_unknown"] == ["NEWW"]
