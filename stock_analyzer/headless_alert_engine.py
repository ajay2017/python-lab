"""
Headless protective-alert engine — exit-discipline Phase 3.

Recomputes ONLY the protective ("reduce today") signals — stop breaches,
deterioration EXIT, and risk-off de-risk — with no Streamlit runtime, so a
GitHub Actions cron can email the user without the app being open. Reuses the
same pure logic the Daily Brief uses (one code path, no drift): the bundle
loader, build_portfolio_df, the fragility engine, exit_advisor, and the same
single-surface dedup rule.

Deliberately NARROW: it does not compute grow/buy/review/news/macro — those are
not "reach me now" decisions. Inputs come from Supabase (holdings/trades/stops)
+ the live providers; credentials are read from os.environ (see db._supabase_creds
/ providers._util.get_secret).
"""

from __future__ import annotations

from datetime import date, datetime

import pytz

from stock_analyzer import db
from stock_analyzer import exit_advisor
from stock_analyzer.bundle_loader import load_bundle
from stock_analyzer.data import fetch_spy, fetch_vix, fetch_risk_free_rate
from stock_analyzer.portfolio import build_portfolio_df
from stock_analyzer.risk import compute_portfolio_risk_metrics
from stock_analyzer.stress_test import SCENARIOS, run_scenario, assess_fragility
from stock_analyzer.tax_advisor import _build_open_lots
from stock_analyzer.daily_briefing import deterioration_signals
from stock_analyzer.constants import (
    PORTFOLIO_BETA_ELEVATED,
    PORTFOLIO_BETA_CEILING,
    FRAGILITY_PULLBACK_PCT,
)

_ET = pytz.timezone("America/New_York")


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _vix_level() -> float | None:
    try:
        v = fetch_vix("1mo")
        if v is None or getattr(v, "empty", True) or "Close" not in v.columns:
            return None
        c = v["Close"].dropna()
        return float(c.iloc[-1]) if not c.empty else None
    except Exception:
        return None


def compute_protective_alerts(today: date | None = None) -> dict:
    """Return {"alerts": [...], "built_at": <ET iso>, "errors": [...]}.

    Each alert is a normalised dict: {kind, ticker, action, directive, why,
    trigger, weight, pnl_pct}. `kind` ∈ {stop_breach, deterioration_exit,
    risk_off_derisk}. An empty `alerts` list means "nothing to act on" — the
    caller sends no email. Never raises; faults are collected in `errors`.
    """
    errors: list[str] = []
    today = today or datetime.now(_ET).date()

    if not db.has_db():
        return {"alerts": [], "built_at": datetime.now(_ET).isoformat(),
                "errors": ["no Supabase credentials (SUPABASE_URL/SUPABASE_KEY)"]}

    # ── Persistent inputs ────────────────────────────────────────────────────
    try:
        holdings_df = db.load_holdings()
    except Exception as e:
        return {"alerts": [], "built_at": datetime.now(_ET).isoformat(),
                "errors": [f"load_holdings failed: {e}"]}
    if holdings_df is None or holdings_df.empty:
        return {"alerts": [], "built_at": datetime.now(_ET).isoformat(), "errors": []}

    try:
        trades_df = db.load_trades()
    except Exception:
        trades_df = None
    try:
        manual_stops = db.load_manual_stops()
    except Exception:
        manual_stops = {}

    # ── Market data ──────────────────────────────────────────────────────────
    try:
        rfr = fetch_risk_free_rate()
    except Exception:
        rfr = 0.045
    try:
        spy_6mo = fetch_spy("6mo")
    except Exception:
        spy_6mo = None
        errors.append("SPY 6mo fetch failed")
    try:
        spy_1y = fetch_spy("1y")
    except Exception:
        spy_1y = None
    vix = _vix_level()

    # ── Per-ticker bundles (shared loader) + position-age enrichment ──────────
    held_tickers = [
        str(t).strip().upper() for t in holdings_df["Ticker"].tolist() if str(t).strip()
    ]
    held_data: dict = {}
    for t in held_tickers:
        try:
            bundle = load_bundle(t, "6mo", spy_df=spy_6mo, rfr=rfr)
        except Exception as e:
            errors.append(f"{t}: bundle load failed ({e})")
            continue
        try:
            lots = _build_open_lots(t, trades_df, today) if trades_df is not None else []
            ages = [l["days_held"] for l in lots]
            bundle["position_age_days"] = max(ages) if ages else None
            bundle["material_add_age_days"] = exit_advisor.material_add_window_days(lots)
        except Exception:
            bundle["position_age_days"] = None
            bundle["material_add_age_days"] = None
        held_data[t] = bundle

    if not held_data:
        return {"alerts": [], "built_at": datetime.now(_ET).isoformat(),
                "errors": errors + ["no holdings could be loaded"]}

    # ── Portfolio frame + fragility ───────────────────────────────────────────
    # NB: unlike the live app (app.py merges intraday live prices into held_data
    # before building port_df), this pre-market cron uses load_bundle's last close.
    # That's intentional and correct here: at ~08:00 ET there is no intraday tape,
    # and the stop rule is "CLOSED below stop" — same semantics as the Daily Brief.
    holdings = holdings_df.to_dict("records")
    port_df = build_portfolio_df(holdings, held_data, manual_stops=manual_stops)
    if port_df is None or port_df.empty:
        return {"alerts": [], "built_at": datetime.now(_ET).isoformat(),
                "errors": errors + ["portfolio frame empty after load"]}

    try:
        port_risk = compute_portfolio_risk_metrics(port_df, held_data, spy_6mo, rfr)
    except Exception:
        port_risk = {}
    fragility = None
    try:
        beta = port_risk.get("beta") if port_risk else None
        if beta is not None:
            mild = next((s for s in SCENARIOS if s["id"] == "mild_correction"), None)
            mild_res = (run_scenario(mild, port_df, held_data, beta,
                                     custom_spy_move=FRAGILITY_PULLBACK_PCT)
                        if mild else {})
            fragility = assess_fragility(mild_res, beta, PORTFOLIO_BETA_ELEVATED,
                                         PORTFOLIO_BETA_CEILING, FRAGILITY_PULLBACK_PCT)
    except Exception:
        fragility = None

    # ── Protective signals (same rules as the Brief, single-surface) ──────────
    alerts: list[dict] = []
    reduced: set[str] = set()

    # 1. Stop breaches (highest priority).
    for _, row in port_df.iterrows():
        gap = _f(row.get("Gap to Stop (%)"))
        if gap is None or gap > 0:
            continue
        t = str(row.get("Ticker", "")).upper()
        shares = int(_f(row.get("Shares"), 0) or 0)
        alerts.append({
            "kind": "stop_breach", "ticker": t, "action": "SELL — Stop Breached",
            "directive": f"Sell all {shares} shares at next open — mechanical stop rule.",
            "why": (f"Price ${_f(row.get('Price'), 0):.2f} closed below the "
                    f"{row.get('Stop Type', row.get('Stop Source',''))} stop "
                    f"${_f(row.get('Stop'), 0):.2f} (gap {gap:+.1f}%)."),
            "trigger": "Already breached — this is the exit signal.",
            "weight": _f(row.get("Weight (%)")), "pnl_pct": _f(row.get("P&L (%)")),
        })
        reduced.add(t)

    # 2. Deterioration EXIT only (TRIM/WATCH excluded by the protective scope).
    try:
        det = deterioration_signals(port_df, held_data, spy_6mo)
    except Exception as e:
        det = []
        errors.append(f"deterioration_signals failed: {e}")
    for d in det:
        if d.get("tier") != "EXIT":
            continue
        t = str(d.get("ticker", "")).upper()
        if t in reduced:
            continue
        alerts.append({
            "kind": "deterioration_exit", "ticker": t, "action": "REDUCE — Deterioration EXIT",
            "directive": (f"Reduce {t} aggressively — down {d.get('dd_from_peak_pct')}% from its "
                          f"peak and below its {d.get('trend_ma')}-day trend."),
            "why": (f"Drawdown-from-peak {d.get('dd_from_peak_pct')}% past the EXIT floor "
                    f"({d.get('exit_floor')}%); P&L {d.get('pnl_pct')}%, "
                    f"weight {d.get('weight_pct')}%."),
            "trigger": "Deterioration exit — confirm and reduce before close.",
            "weight": d.get("weight_pct"), "pnl_pct": d.get("pnl_pct"),
        })
        reduced.add(t)

    # 3. Risk-off de-risk (lowest priority; excludes already-reduced tickers).
    try:
        risk_off = exit_advisor.assess_risk_off_derisk(
            port_df, held_data, fragility=fragility, spy_trend_df=spy_1y,
            vix_level=vix, exclude_tickers=reduced,
        )
    except Exception as e:
        risk_off = []
        errors.append(f"assess_risk_off_derisk failed: {e}")
    for c in risk_off:
        alerts.append({
            "kind": "risk_off_derisk", "ticker": c.get("ticker"),
            "action": c.get("action", "TRIM — Risk-Off"),
            "directive": c.get("directive", ""), "why": c.get("why", ""),
            "trigger": c.get("trigger", ""), "weight": c.get("weight"),
            "pnl_pct": c.get("pnl_pct"),
        })

    return {"alerts": alerts, "built_at": datetime.now(_ET).isoformat(), "errors": errors}
