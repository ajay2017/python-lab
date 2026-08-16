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

import math
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
from stock_analyzer.daily_briefing import deterioration_signals, build_daily_briefing
from stock_analyzer.constants import (
    PORTFOLIO_BETA_ELEVATED,
    PORTFOLIO_BETA_CEILING,
    FRAGILITY_PULLBACK_PCT,
    PULLBACK_ALERT_INDEX_PCT,
    GROW_CANDIDATE_POOL,
    COMPOSITE_BUY,
    COMPOSITE_BUY_FLAT_DAY,
    MARKET_TONE_BULL_PCT,
    MARKET_TONE_BEAR_PCT,
)

_ET = pytz.timezone("America/New_York")


def _f(v, default=None):
    """Parse to float, or `default` on anything unparseable — including NaN.

    A source field that's legitimately None (e.g. "Stop Unavailable" ->
    gap_to_stop=None in portfolio.py) gets silently pandas-coerced to NaN once
    it shares a DataFrame column with any row that has a real float value.
    float(nan) doesn't raise, so without this check a caller doing
    `if _f(x) is None: skip` would miss it -- NaN passed straight through as
    a "real" float. Confirmed reachable: found while testing the stop-breach
    loop below, which would otherwise fire a bogus SELL alert on a ticker
    whose stop is actually unknown.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def _vix_level() -> float | None:
    try:
        v = fetch_vix("1mo")
        if v is None or getattr(v, "empty", True) or "Close" not in v.columns:
            return None
        c = v["Close"].dropna()
        return float(c.iloc[-1]) if not c.empty else None
    except Exception:
        return None


def _build_context(today: date) -> dict:
    """Shared headless data-prep: db inputs → market data → per-ticker bundles →
    port_df → fragility. Returns {ok, errors, port_df, held_data, fragility,
    spy_6mo, spy_1y, vix}. `ok=False` (reason in `errors`) when there's no DB / no
    holdings / the frame is empty — callers short-circuit to an empty result.
    Never raises. One prep path feeds BOTH the pre-market protective run and the
    EOD snapshot/pullback run, so they can never disagree about the book."""
    errors: list[str] = []
    # `reason` is what lets a caller tell "the DB is unreachable" apart from
    # "the user owns nothing" — the two used to be indistinguishable here,
    # because db.load_holdings() collapsed a failed read into an empty frame.
    # A protective lane MUST distinguish them: one means email the owner that
    # the scan did not run, the other means quietly do nothing.
    if not db.has_db():
        return {"ok": False, "reason": "db_unavailable",
                "errors": ["no Supabase credentials (SUPABASE_URL/SUPABASE_KEY)"]}
    try:
        holdings_df = db.load_holdings_or_none()
    except Exception as e:   # defensive — the strict reader already swallows
        return {"ok": False, "reason": "db_unavailable",
                "errors": [f"load_holdings failed: {e}"]}
    if holdings_df is None:
        return {"ok": False, "reason": "db_unavailable",
                "errors": ["holdings could not be read from Supabase"]}
    if holdings_df.empty:
        return {"ok": False, "reason": "no_holdings", "errors": ["no holdings"]}

    try:
        trades_df = db.load_trades()
    except Exception:
        trades_df = None
    try:
        manual_stops = db.load_manual_stops()
    except Exception:
        manual_stops = {}

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
        # Provider fault, NOT a DB fault — the book was read fine, the price
        # bundles failed. Must not trigger a "database unreachable" email.
        return {"ok": False, "reason": "no_bundles",
                "errors": errors + ["no holdings could be loaded"]}

    # NB: no intraday live-price merge (unlike the live app). The pre-market run
    # uses last close (stop rule = "CLOSED below stop"); the EOD run is post-close
    # so last close IS today's final close — correct for the snapshot too.
    holdings = holdings_df.to_dict("records")
    port_df = build_portfolio_df(holdings, held_data, manual_stops=manual_stops)
    if port_df is None or port_df.empty:
        return {"ok": False, "reason": "empty_port_df",
                "errors": errors + ["portfolio frame empty after load"]}

    try:
        port_risk = compute_portfolio_risk_metrics(port_df, held_data, spy_6mo, rfr)
    except Exception:
        port_risk = None
    fragility = None
    try:
        beta = _f(port_risk.get("beta")) if port_risk else None
        if beta is not None:
            mild = next((s for s in SCENARIOS if s["id"] == "mild_correction"), None)
            mild_res = (run_scenario(mild, port_df, held_data, beta,
                                     custom_spy_move=FRAGILITY_PULLBACK_PCT)
                        if mild else {})
            fragility = assess_fragility(mild_res, beta, PORTFOLIO_BETA_ELEVATED,
                                         PORTFOLIO_BETA_CEILING, FRAGILITY_PULLBACK_PCT)
    except Exception:
        fragility = None

    return {"ok": True, "errors": errors, "port_df": port_df, "held_data": held_data,
            "fragility": fragility, "spy_6mo": spy_6mo, "spy_1y": spy_1y, "vix": vix}


def compute_protective_alerts(today: date | None = None) -> dict:
    """Return {"alerts": [...], "built_at": <ET iso>, "errors": [...]}.

    Each alert is a normalised dict: {kind, ticker, action, directive, why,
    trigger, weight, pnl_pct}. `kind` ∈ {stop_breach, deterioration_exit,
    risk_off_derisk}. An empty `alerts` list means "nothing to act on" — the
    caller sends no email. Never raises; faults are collected in `errors`.
    """
    today = today or datetime.now(_ET).date()
    ctx = _build_context(today)
    built_at = datetime.now(_ET).isoformat()
    if not ctx.get("ok"):
        # `reason` rides along so the cron can tell a DB outage (email the
        # owner: the scan did NOT run) from an empty book (stay quiet).
        return {"alerts": [], "built_at": built_at, "errors": ctx.get("errors", []),
                "reason": ctx.get("reason")}

    errors = list(ctx["errors"])
    port_df, held_data = ctx["port_df"], ctx["held_data"]
    spy_6mo, spy_1y, vix, fragility = ctx["spy_6mo"], ctx["spy_1y"], ctx["vix"], ctx["fragility"]

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

    # composite_score enrichment for exit_signals capture (all tiers, not just
    # the EXIT-only protective-email scope below) — mirrors app.py:4143-4149.
    composite_map = (
        port_df.set_index("Ticker")["Score"].to_dict()
        if "Ticker" in port_df.columns and "Score" in port_df.columns
        else {}
    )
    for d in det:
        d["composite_score"] = composite_map.get(str(d.get("ticker", "")).upper())

    # Daily analyst-target consensus snapshot (log-only, Phase 1 — no alert
    # reads this yet). Reuses the already-loaded bundles; zero extra API cost.
    # Skips stale-cache-served bundles so persisted history never mixes in a
    # bundle_cache fallback value (would contaminate a future day-over-day
    # comparison — see the INTC staleness incident precedent).
    analyst_target_snapshots: list[dict] = []
    for t, bundle in held_data.items():
        if bundle.get("stale_as_of") is not None:
            continue
        fin = bundle.get("financials") or {}
        target_mean = _f(fin.get("analyst_target"))
        if target_mean is None:
            continue
        analyst_target_snapshots.append({
            "ticker": t,
            "snapshot_date": today.isoformat(),
            "target_mean": target_mean,
            "num_analysts": fin.get("num_analyst_opinions"),
            "info_source": bundle.get("info_source"),
        })

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
        c["composite_score"] = composite_map.get(str(c.get("ticker", "")).upper())
        alerts.append({
            "kind": "risk_off_derisk", "ticker": c.get("ticker"),
            "action": c.get("action", "TRIM — Risk-Off"),
            "directive": c.get("directive", ""), "why": c.get("why", ""),
            "trigger": c.get("trigger", ""), "weight": c.get("weight"),
            "pnl_pct": c.get("pnl_pct"),
        })

    return {
        "alerts": alerts, "built_at": built_at, "errors": errors,
        # Additive — full WATCH/TRIM/EXIT + RISK_OFF signal lists (composite_score
        # attached above) for exit_signals capture. Never used to build `alerts`
        # above; the EXIT-only/risk_off email scope is unchanged.
        "all_deterioration_signals": det,
        "risk_off_signals": risk_off,
        # Additive — daily analyst-target consensus snapshot per held ticker,
        # log-only (Phase 1). Never used to build `alerts` above.
        "analyst_target_snapshots": analyst_target_snapshots,
    }


def compute_morning_picks(today: date | None = None, scanner_results=None) -> dict:
    """Return {"picks": [...new_picks...], "built_at": <ET iso>, "errors": [...]}.

    The OFFENSE counterpart to compute_protective_alerts: the headless equivalent
    of the Home brief's Grow Today "New Positions to Initiate". Reuses _build_context
    and assembles the SAME inputs build_daily_briefing uses in the app — market
    tone, per-pick composites, and news (derived from the already-loaded bundles) —
    then calls the SAME build_daily_briefing so the gating (tone / composite /
    sector-cap / conflict exclusion) is identical, no logic drift. The caller
    filters to the high-conviction "Go" set and emails it. Empty `picks` → caller
    sends no email. Never raises; faults collected in `errors`.

    Mirrors the app's full input assembly (tone + composites + news + macro
    calendar) so the gating — including the imminent-macro sector suppression —
    matches Grow Today and the email never surfaces a pick the app would suppress.
    """
    today = today or datetime.now(_ET).date()
    built_at = datetime.now(_ET).isoformat()
    if scanner_results is None or getattr(scanner_results, "empty", True):
        return {"picks": [], "built_at": built_at, "errors": ["no scanner results"]}

    ctx = _build_context(today)
    if not ctx.get("ok"):
        return {"picks": [], "built_at": built_at, "errors": ctx.get("errors", []),
                "reason": ctx.get("reason")}
    errors = list(ctx["errors"])
    port_df, held_data = ctx["port_df"], ctx["held_data"]
    spy_6mo, spy_1y, vix, fragility = ctx["spy_6mo"], ctx["spy_1y"], ctx["vix"], ctx["fragility"]
    try:
        portfolio_value = float(port_df["Market Value"].sum()) if not port_df.empty else 0.0
    except Exception:
        portfolio_value = 0.0

    # Market tone (drives the bull-only new-pick gate) — mirrors the app's
    # _market_context assembly (S&P daily move → bull/bear/flat). leading_sectors
    # is left empty headlessly: it only flavours the thesis text, not the gating.
    market_context = {"tone": "flat", "sp500_pct": 0.0, "nasdaq_pct": 0.0, "leading_sectors": []}
    try:
        from stock_analyzer.data import fetch_market_indices
        _idx = fetch_market_indices()
        _sp = next((i for i in _idx if i.get("short") == "S&P 500"), None)
        _nq = next((i for i in _idx if i.get("short") == "NASDAQ"), None)
        _sp_pct = float(_sp["change_pct"]) if _sp else 0.0
        market_context = {
            "tone": (
                "bull" if _sp_pct >= MARKET_TONE_BULL_PCT
                else "bear" if _sp_pct <= MARKET_TONE_BEAR_PCT
                else "flat"
            ),
            "sp500_pct": _sp_pct,
            "nasdaq_pct": float(_nq["change_pct"]) if _nq else 0.0,
            "leading_sectors": [],
        }
    except Exception as e:
        errors.append(f"market tone fetch failed: {e}")

    # Per-pick composites for the top scanner names — mirrors the app's
    # grow-composites loop (load_bundle is what app.load_all wraps). Without these
    # picks fall to "unverified" and never reach the Go set.
    held_set = {str(t).upper() for t in held_data.keys()}
    try:
        rfr = fetch_risk_free_rate()
    except Exception:
        rfr = 0.045
    try:
        _top = (scanner_results[~scanner_results["Ticker"].str.upper().isin(held_set)]
                .head(GROW_CANDIDATE_POOL)["Ticker"].tolist())
    except Exception:
        _top = []
    grow_composites: dict = {}
    for _tc in _top:
        _t = str(_tc).strip().upper()
        if not _t:
            continue
        try:
            grow_composites[_t] = load_bundle(_t, "6mo", spy_df=spy_6mo, rfr=rfr)
        except Exception:
            continue

    # News from the already-loaded bundles (held + composites) so _cross_reference
    # still suppresses negative-news conflicts — no extra network calls.
    try:
        from stock_analyzer.data import curate_news_items
        _news_src = dict(held_data)
        _news_src.update(grow_composites)
        news_items = curate_news_items(_news_src)
    except Exception:
        news_items = []

    # Macro calendar — MUST pass it (not []), else the imminent-HIGH-impact-event
    # sector gate is disabled headlessly and the buy email could surface a pick
    # the app would suppress on a binary-catalyst day (FOMC/CPI/jobs). The static
    # backbone gives the event dates even without a FRED key. (Streamlit-free.)
    macro_events: list = []
    try:
        import os as _os
        from stock_analyzer.macro_calendar import build_macro_calendar
        macro_events = build_macro_calendar(
            port_df, fred_key=(_os.environ.get("FRED_API_KEY") or None), today=today,
        ) or []
    except Exception as e:
        errors.append(f"macro calendar failed: {e}")

    try:
        brief = build_daily_briefing(
            port_df=port_df, alert_list=[], risk_recs=[], news_items=news_items,
            macro_events=macro_events, held_data=held_data, scanner_results=scanner_results,
            portfolio_value=portfolio_value, today=today, market_context=market_context,
            grow_composites=grow_composites, movers=[], spy_df=spy_6mo,
            fragility=fragility, spy_trend_df=spy_1y, vix_level=vix,
        )
    except Exception as e:
        return {"picks": [], "built_at": built_at,
                "errors": errors + [f"build_daily_briefing failed: {e}"]}

    # build_daily_briefing's tone/new_picks/etc. live nested under "grow_today"
    # (app.py always reads it that way) -- NOT at the top level of `brief`.
    grow = brief.get("grow_today") or {}

    # Diagnostic so a 0-pick run is self-explaining in the cron log (a flat tape
    # raises the new-pick bar to 78 and caps at 1, a bull tape lets 65+ through —
    # so "0 picks" next to a Home page showing morning picks is usually the tone
    # gate, not a fault). bar=None on bear (new entries suppressed outright).
    _tone = grow.get("tone", "flat")
    _bar = None if _tone == "bear" else (COMPOSITE_BUY if _tone == "bull" else COMPOSITE_BUY_FLAT_DAY)
    diag = {
        "tone":             _tone,
        # _grow_today's bear-day early return omits "sp500_pct" entirely (it
        # only builds the message string) -- fall back to market_context's own
        # fetched value so a real risk-off move never logs as "S&P n/a".
        "sp500_pct":        grow.get("sp500_pct", market_context.get("sp500_pct")),
        "bar":              _bar,
        "sector_blocked":   len(grow.get("sector_blocked_picks", []) or []),
        "macro_blocked":    len(grow.get("macro_blocked_picks", []) or []),
        "composite_short":  len(grow.get("composite_skipped", []) or []),
        "composite_unavail": len(grow.get("composite_unavailable", []) or []),
    }
    return {"picks": grow.get("new_picks", []) or [], "built_at": built_at,
            "errors": errors, "diag": diag}


def _assess_pullback(spy_6mo, fragility, threshold: float) -> dict | None:
    """Reactive drawdown read: did the broad market ACTUALLY fall ≥ `threshold`
    (a negative %) on the latest session? Returns the exposure framing or None.

    This observes reality (the index IS down), the most reliable leg of the
    pullback-awareness frame — distinct from the pre-market REGIME risk-off. The
    book-implied move reuses the fragility ×-market multiplier so the displayed
    numbers tie out with the Home fragility gauge."""
    try:
        if spy_6mo is None or getattr(spy_6mo, "empty", True) or "Close" not in spy_6mo.columns:
            return None
        c = spy_6mo["Close"].dropna()
        if len(c) < 2:
            return None
        prev = float(c.iloc[-2])
        if prev <= 0:
            return None
        idx_pct = (float(c.iloc[-1]) / prev - 1.0) * 100.0
    except Exception:
        return None
    if idx_pct > threshold:        # threshold is negative; fire only on a deep-enough drop
        return None
    frag = fragility or {}
    mult = frag.get("mult")
    book_implied = round(mult * idx_pct, 1) if mult else None   # mult>0, idx_pct<0 → negative
    return {
        "index_pct": round(idx_pct, 1),
        "book_implied_pct": book_implied,
        "severity": frag.get("severity"),
        "mult": mult,
        "exposed": frag.get("exposed") or [],
    }


def compute_eod(today: date | None = None, pullback_threshold: float = PULLBACK_ALERT_INDEX_PCT) -> dict:
    """End-of-day job inputs: today's snapshot rows (for the Today's-P&L baseline)
    + a reactive pullback read. Returns {"snapshot_rows": [...], "pullback": {...}|None,
    "built_at": <ET iso>, "errors": [...]}. Never raises.

    snapshot_rows shape matches db.save_daily_snapshot: {ticker, shares, close_price}.
    Reuses the SAME _build_context as the protective run (post-close → last close
    is final)."""
    today = today or datetime.now(_ET).date()
    ctx = _build_context(today)
    built_at = datetime.now(_ET).isoformat()
    if not ctx.get("ok"):
        return {"snapshot_rows": [], "pullback": None, "built_at": built_at,
                "errors": ctx.get("errors", []), "reason": ctx.get("reason")}

    port_df = ctx["port_df"]
    snapshot_rows = []
    for _, row in port_df.iterrows():
        px = _f(row.get("Price"))
        sh = _f(row.get("Shares"))
        t = str(row.get("Ticker", "")).upper()
        if t and px and px > 0 and sh and sh > 0:
            snapshot_rows.append({"ticker": t, "shares": sh, "close_price": px})

    pullback = _assess_pullback(ctx["spy_6mo"], ctx["fragility"], pullback_threshold)
    return {"snapshot_rows": snapshot_rows, "pullback": pullback,
            "built_at": built_at, "errors": list(ctx["errors"]),
            "held_data": ctx.get("held_data", {})}
