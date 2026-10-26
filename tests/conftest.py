# Regression tests for stock_analyzer/ pure-logic modules — no Streamlit, no
# Supabase, no live network. See docs/plans/test-automation.md for scope and
# batch order.
import ast
import sys
from pathlib import Path

import pandas as pd
import pytest

# ── Guard: no test parses app.py the slow way ───────────────────────────────
# app.py is ~3 MB; a plain ast.parse costs 15-90 s per xdist worker, and the
# commit hook gives the whole suite 450 s (it timed out 3x on 2026-10-20).
# Tests must use tests._ast_cache.parse_file_cached / source_segment instead.
# Fires only when a TEST FILE calls ast.parse / ast.get_source_segment
# DIRECTLY on a huge source: production code (e.g. check_antipatterns parsing
# app.py), pytest internals and _ast_cache itself are untouched.  pytest.fail
# raises a BaseException, so a test's own `except Exception` can't swallow it.
_HUGE_SOURCE_CHARS = 1_000_000   # app.py ~3.0M; next-largest (db.py) ~0.33M
_TESTS_DIR = Path(__file__).resolve().parent
_AST_CACHE_PY = _TESTS_DIR / "_ast_cache.py"
_real_parse = ast.parse
_real_get_source_segment = ast.get_source_segment


def _forbid_slow_app_parse(source, fn_name):
    if not isinstance(source, (str, bytes)) or len(source) <= _HUGE_SOURCE_CHARS:
        return
    try:
        caller = Path(sys._getframe(2).f_code.co_filename).resolve()
    except Exception:
        return
    if caller.parent == _TESTS_DIR and caller != _AST_CACHE_PY:
        pytest.fail(
            f"{caller.name} calls ast.{fn_name} on a {len(source):,}-char source "
            "(app.py) directly -- use tests._ast_cache.parse_file_cached(path) "
            "/ source_segment(src, node) so the commit hook stays under 450 s.",
            pytrace=False,
        )


def _guarded_parse(source, *args, **kwargs):
    _forbid_slow_app_parse(source, "parse")
    return _real_parse(source, *args, **kwargs)


def _guarded_get_source_segment(source, node, *args, **kwargs):
    _forbid_slow_app_parse(source, "get_source_segment")
    return _real_get_source_segment(source, node, *args, **kwargs)


# A test that MUST compare against the stdlib on app.py (the cache's own parity
# test) opts out visibly via `ast.get_source_segment.__wrapped__`.
_guarded_parse.__wrapped__ = _real_parse
_guarded_get_source_segment.__wrapped__ = _real_get_source_segment
ast.parse = _guarded_parse
ast.get_source_segment = _guarded_get_source_segment


@pytest.fixture(autouse=True)
def _clear_llm_model_env(monkeypatch):
    """N13: a DRISHTA_LLM_MODEL_* variable in the developer's shell must never
    change which model a test resolves."""
    import os
    for k in [k for k in os.environ if k.startswith("DRISHTA_LLM_MODEL_")]:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _stub_llm_settings_loader(monkeypatch):
    """N13: no test may ever read a real App Settings model row."""
    from stock_analyzer import llm_settings, llm_usage
    monkeypatch.setattr(llm_settings, "_default_loader", lambda: ("no_row", None))
    llm_settings.invalidate()
    llm_usage.reset()
    yield
    llm_settings.invalidate()
    llm_usage.reset()


def make_risk_advisor_inputs(
    rows,
    *,
    portfolio_value: float = 100_000.0,
    gate_denom: float | None = None,
    beta: float | None = 1.0,
    ann_volatility: float | None = 15.0,
    sharpe: float | None = 1.2,
    sortino: float | None = 1.0,
    var_95_pct: float | None = None,
    cvar_95_pct: float | None = None,
    max_drawdown: float | None = -5.0,
):
    """Build (port_df, held_data, port_risk, h_rets, portfolio_value, gate_denom)
    for stock_analyzer.risk_advisor.build_risk_advisor_recommendations().

    `rows` is a list of dicts, each describing one held ticker. Any field not
    given falls back to a "safe" default so a test can vary only the field(s)
    relevant to the recommendation it's checking, without triggering unrelated
    branches. Defaults for the top-level port_risk metrics (beta/sharpe/vol/
    drawdown) are likewise all "safe" (no rec expected) unless overridden.

    Per-row fields: ticker, weight (%), market_value ($), price, pnl_pct,
    score, sector, signal, beta, sharpe, sortino, max_drawdown, var_95, ret_6mo.
    """
    defaults = dict(
        weight=10.0, market_value=10_000.0, price=100.0, pnl_pct=0.0, score=60.0,
        sector="Tech", signal="Hold", beta=1.0, sharpe=1.0, sortino=1.0,
        max_drawdown=-5.0, var_95=-2.0, ret_6mo=0.0,
    )
    filled = []
    for r in rows:
        row = {**defaults, **r}
        filled.append(row)

    port_df = pd.DataFrame([
        {
            "Ticker": r["ticker"],
            "Weight (%)": r["weight"],
            "Market Value": r["market_value"],
            "Price": r["price"],
            "P&L (%)": r["pnl_pct"],
            "Score": r["score"],
            "Signal": r["signal"],
            "Sector": r["sector"],
        }
        for r in filled
    ])
    held_data = {
        r["ticker"]: {
            "risk_metrics": {
                "beta": r["beta"],
                "sharpe": r["sharpe"],
                "sortino": r["sortino"],
                "max_drawdown": r["max_drawdown"],
                "var_95": r["var_95"],
            }
        }
        for r in filled
    }
    h_rets = {r["ticker"]: r["ret_6mo"] for r in filled}
    port_risk = {
        "beta": beta,
        "ann_volatility": ann_volatility,
        "sharpe": sharpe,
        "sortino": sortino,
        "var_95_pct": var_95_pct,
        "cvar_95_pct": cvar_95_pct,
        "max_drawdown": max_drawdown,
    }
    return port_df, held_data, port_risk, h_rets, portfolio_value, gate_denom


def find_rec(recs, rec_type):
    """First rec dict of the given `type`, or None."""
    return next((r for r in recs if r["type"] == rec_type), None)


def make_port_df(rows):
    """Build a port_df for stock_analyzer.daily_briefing's _grow_today()/
    _review_list() — a "safe" default row (no gate/suppression triggered)
    unless a field is overridden. `_gate_wt`/`_gate_wt_col` fall back to
    "Weight (%)" when "Gate Weight (%)" is absent, so it's omitted here.

    Per-row fields: ticker, weight (%), score, signal, price, sector, pnl_pct,
    gap_to_stop (%), shares, mom_1m, stop, market_value (S-D: defaults to
    shares * price, the holding's value the add loop sizes against).
    """
    defaults = dict(
        weight=10.0, score=50.0, signal="Hold", price=100.0, sector="Tech",
        pnl_pct=0.0, gap_to_stop=None, shares=10, mom_1m=0.0, stop=90.0,
        market_value=None,
    )
    filled = [{**defaults, **r} for r in rows]
    data = [
        {
            "Ticker": r["ticker"],
            "Weight (%)": r["weight"],
            "Score": r["score"],
            "Signal": r["signal"],
            "Price": r["price"],
            "Sector": r["sector"],
            "P&L (%)": r["pnl_pct"],
            "Gap to Stop (%)": r["gap_to_stop"],
            "Shares": r["shares"],
            "1M Momentum": r["mom_1m"],
            "Stop": r["stop"],
            "Market Value": (r["market_value"] if r["market_value"] is not None
                             else r["shares"] * r["price"]),
        }
        for r in filled
    ]
    return pd.DataFrame(data)


def find_item(items, ticker):
    """First item dict for `ticker` (case-insensitive), or None."""
    ticker = str(ticker).upper()
    return next((i for i in items if str(i.get("ticker", "")).upper() == ticker), None)
