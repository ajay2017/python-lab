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
from datetime import date, datetime, timedelta

import pytz

from stock_analyzer import db
from stock_analyzer import broker_sync
from stock_analyzer import exit_advisor
from stock_analyzer import margin as _margin_mod
from stock_analyzer.bundle_loader import load_bundle
from stock_analyzer.data import fetch_spy, fetch_vix, fetch_risk_free_rate, is_trading_day
from stock_analyzer.portfolio import build_portfolio_df
from stock_analyzer.risk import compute_portfolio_risk_metrics
from stock_analyzer.stress_test import SCENARIOS, run_scenario, assess_fragility
from stock_analyzer.tax_advisor import _build_open_lots
from stock_analyzer.daily_briefing import deterioration_signals, build_daily_briefing
from stock_analyzer.watchlist_advisor import build_watchlist_recommendation
from stock_analyzer.recommendations_history import build_enter_now_rows
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
    SNAPTRADE_BALANCE_STALE_HOURS,
    ACCOUNT_CASH_STALE_DAYS,
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
    spy_6mo, spy_1y, vix, holdings_df, trades_df}. `ok=False` (reason in `errors`) when there's no DB / no
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
            "fragility": fragility, "spy_6mo": spy_6mo, "spy_1y": spy_1y, "vix": vix,
            # Raw (not int()-truncated) frames, already loaded above at zero extra
            # cost — added 2026-08-24 so compute_morning_picks can compute a
            # broker-drift verdict via broker_sync.decide_drift_banner, which
            # must diff the RAW holdings_df (fractional shares intact), not
            # port_df (build_portfolio_df truncates to int(shares), and diffing
            # a truncated frame fabricates a permanent phantom drift on any
            # fractional broker lot).
            "holdings_df": holdings_df, "trades_df": trades_df,
            # Additive (2026-09-10) — the already-computed risk-metrics dict
            # (beta, etc.), previously discarded after `fragility` was derived
            # from it. compute_watchlist_entries needs `beta` for its portfolio-
            # fit gate and would otherwise have to recompute compute_all_risk a
            # second time. `None` when the risk computation itself failed above
            # (caller must treat a missing/None beta as "couldn't be built",
            # never as "beta is zero").
            "port_risk": port_risk}


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

    # F-255: resolve the SEPARATE net-capital sizing cap the same way the app
    # does, so the emailed buy-list respects it too, not just the interactive
    # surfaces. `db.load_account_cash()` is a pure DB read that works headlessly
    # (no Streamlit dependency). None/inert when unlevered, no cash record, or
    # the record is stale — identical no-op posture to every other caller.
    try:
        _f255_acct = db.load_account_cash()
        _f255_net_cap, _f255_basis = _margin_mod.resolve_net_capital(
            portfolio_value, _f255_acct, ACCOUNT_CASH_STALE_DAYS, datetime.now(_ET)
        )
    except Exception:
        _f255_net_cap = None

    try:
        brief = build_daily_briefing(
            port_df=port_df, alert_list=[], risk_recs=[], news_items=news_items,
            macro_events=macro_events, held_data=held_data, scanner_results=scanner_results,
            portfolio_value=portfolio_value, today=today, market_context=market_context,
            grow_composites=grow_composites, movers=[], spy_df=spy_6mo,
            fragility=fragility, spy_trend_df=spy_1y, vix_level=vix,
            net_capital=_f255_net_cap,
        )
    except Exception as e:
        return {"picks": [], "built_at": built_at,
                "errors": errors + [f"build_daily_briefing failed: {e}"]}

    # build_daily_briefing's tone/new_picks/etc. live nested under "grow_today"
    # (app.py always reads it that way) -- NOT at the top level of `brief`.
    grow = brief.get("grow_today") or {}

    # Book-vs-broker drift verdict — F-252 follow-up (2026-08-24). The suggested
    # share sizes above are computed from `portfolio_value` (this book's own
    # sum), which can silently disagree with what the broker actually shows —
    # and unlike the interactive app, the emailed picks below carry no drift
    # banner at all today. Isolated in its own try/except AFTER
    # build_daily_briefing has already succeeded, so a SnapTrade/DB fault here
    # can never abort pick computation or block the email. `None` flows
    # through as "unknown" — the fail-safe direction for a PUSH surface (see
    # notify._book_drift_banner: only state=="drift" ever renders anything).
    book_drift = None
    try:
        _bsnap = db.load_broker_position_snapshot()
        if _bsnap is not None:
            _price_map = (
                dict(zip(port_df["Ticker"], port_df["Price"]))
                if not port_df.empty and "Ticker" in port_df.columns and "Price" in port_df.columns
                else {}
            )
            book_drift = broker_sync.decide_drift_banner(
                _bsnap,
                ctx.get("holdings_df"),   # RAW frame — see _build_context's comment on why
                datetime.now(_ET),
                SNAPTRADE_BALANCE_STALE_HOURS,
                price_map=_price_map,
                recent_trade_tickers=broker_sync.tickers_traded_since(
                    ctx.get("trades_df"), _bsnap.get("captured_at")
                ),
            )
    except Exception:
        book_drift = None

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
    # Macro calendar coverage — passes through from _grow_today's LATE-built
    # "macro_coverage_expired" key so the email renderers can show the
    # blind-spot banner when a backbone series has expired.  None means
    # "could not verify"; [] means "verified, nothing expired".  Isolated so
    # a reference_shelf failure here can never abort pick computation.
    macro_coverage = grow.get("macro_coverage_expired")   # may be None or []

    return {"picks": grow.get("new_picks", []) or [], "built_at": built_at,
            "errors": errors, "diag": diag, "book_drift": book_drift,
            "macro_coverage": macro_coverage,
            # grow_today dict — gate_ledger.build_suppression_rows reads the
            # suppression buckets from here (W5 capture half). Additive; existing
            # callers that ignore this key are unaffected.
            "grow": grow}


def _prior_trading_day(d: date) -> date:
    """Most recent NYSE trading day strictly before `d` — NYSE-calendar aware
    (via data.is_trading_day), not naive weekday-1 math. Used only for the
    enter_now day-over-day transition diff in compute_watchlist_entries (D-B).
    Bounded to 10 calendar days back; NYSE has never closed that long."""
    cur = d - timedelta(days=1)
    for _ in range(10):
        if is_trading_day(cur):
            return cur
        cur -= timedelta(days=1)
    return cur


def compute_watchlist_entries(
    today: date | None = None,
    watchlist: "list[str] | None" = None,
    scanner_go_tickers=None,
) -> dict:
    """Return {"entries": [...] | None, "built_at": <ET iso>, "errors": [...],
    "reason": str | None, "gate_degraded": bool}.

    The PROACTIVE counterpart to opening 📋 Watchlist: recomputes each
    watchlist ticker's verdict via the SAME `build_watchlist_recommendation`
    the interactive page calls (unchanged, no logic drift). CAPTURE and
    ANNOUNCE are deliberately different scopes (coordinator decision,
    2026-09-10) — same distinction the interactive page already draws (it
    captures a held ENTER_NOW for grading with already_held=True, but only
    turns it into a render-time caution, never a capture-time exclusion):

      capture — EVERY ENTER_NOW ticker, held or not, is written as today's
             `enter_now` baseline via the same `build_enter_now_rows` +
             `db.save_recommendations` the interactive page uses. Matches the
             interactive page's own D1 capture scope exactly, so grading
             coverage doesn't silently undercount held tickers on a cron-only
             day versus a day someone opens Watchlist.
      D-C  — held tickers are excluded from the EMAIL-ELIGIBLE set only (a
             held ENTER_NOW is already suppressed into a caution on the
             interactive page; this mirrors that outcome for what gets
             emailed, never for what gets captured above).
      dedup — `scanner_go_tickers` (today's high-conviction scan picks) and
             today's EXIT/TRIM/RISK_OFF `exit_signals` tickers are excluded
             from the email-eligible set so the same name never gets two
             independent buy announcements, or a buy announcement while under
             an active protective call.
      D-B  — of the email-eligible set, only tickers NEWLY qualifying versus
             the prior trading day's recorded `enter_now` set are emailed. A
             ticker persisting from yesterday is silent (but was still
             captured above, so tomorrow's diff has today's baseline
             regardless of what's emailed today).

    Offline contract: `entries` is `None` ONLY when the watchlist or holdings
    read itself fails (`reason="db_unavailable"`) — a genuine producer
    failure. `entries == []` means checked and genuinely nothing new
    transitioned (including the case where a same-day prior-day lookup
    failure makes the transition unverifiable — see below).

    D-D: the portfolio-fit gate (sector weight from port_df + portfolio beta)
    is best-effort. Sector/concentration reliably runs off port_df; beta is
    the leg genuinely at risk of not being buildable headlessly (a fragility/
    risk-metrics computation failure, or no portfolio context at all) —
    tracked via `gate_degraded`, never silently dropped. An otherwise-
    qualifying name is still included when degraded, never withheld.

    Never raises; faults are collected in `errors`.
    """
    today = today or datetime.now(_ET).date()
    built_at = datetime.now(_ET).isoformat()
    errors: list[str] = []
    scanner_go = {str(t).strip().upper() for t in (scanner_go_tickers or []) if str(t).strip()}

    if watchlist is None:
        try:
            watchlist = db.load_watchlist_or_none()
        except Exception as e:
            watchlist = None
            errors.append(f"load_watchlist failed: {e}")
    if watchlist is None:
        return {"entries": None, "built_at": built_at,
                "errors": errors or ["watchlist could not be read from Supabase"],
                "reason": "db_unavailable", "gate_degraded": False}
    watchlist = [str(t).strip().upper() for t in watchlist if str(t).strip()]
    if not watchlist:
        return {"entries": [], "built_at": built_at, "errors": [], "reason": None,
                "gate_degraded": False}

    # Held-ticker set — read directly, decoupled from the heavier bundle/
    # port_df build below. D-C's held-exclusion is a hard invariant (a wrong
    # inclusion here would announce "buy" on a name already owned), not a
    # degradable gate like beta — so THIS read fails CLOSED (entries=None,
    # same "db_unavailable" reason as the watchlist read above) rather than
    # proceeding on a guess.
    try:
        holdings_df = db.load_holdings_or_none()
    except Exception as e:
        holdings_df = None
        errors.append(f"load_holdings failed: {e}")
    if holdings_df is None:
        return {"entries": None, "built_at": built_at, "errors": errors,
                "reason": "db_unavailable", "gate_degraded": False}
    held_set = (
        {str(t).strip().upper() for t in holdings_df["Ticker"].tolist() if str(t).strip()}
        if not holdings_df.empty and "Ticker" in holdings_df.columns else set()
    )

    # Portfolio-fit context (sector weight + beta) for the gate — best-effort.
    # Reuses the SAME _build_context prep every other headless computation
    # uses, so sector weights/beta tie out with the protective/offense lanes.
    ctx = _build_context(today)
    gate_degraded = False
    port_df = None
    portfolio_beta = None
    spy_for_bundles = None
    if ctx.get("ok"):
        port_df = ctx["port_df"]
        spy_for_bundles = ctx.get("spy_6mo")
        port_risk = ctx.get("port_risk")
        portfolio_beta = _f(port_risk.get("beta")) if port_risk else None
        if portfolio_beta is None:
            gate_degraded = True
    else:
        # _build_context always sets "errors" to a list on both its ok and
        # not-ok branches (never omits or nulls it) — direct index, not
        # `.get(...) or []`, matching compute_protective_alerts/
        # compute_morning_picks's own convention for this same ctx dict.
        _ctx_errors = ctx["errors"] if "errors" in ctx else []
        errors.extend(_ctx_errors)
        gate_degraded = True
        try:
            spy_for_bundles = fetch_spy("6mo")
        except Exception:
            spy_for_bundles = None

    try:
        rfr = fetch_risk_free_rate()
    except Exception:
        rfr = 0.045

    # ── Per-ticker: bundle load → portfolio_ctx → build_watchlist_recommendation ──
    qualifying: list[dict] = []
    sector_map: dict = {}
    for t in watchlist:
        try:
            data = load_bundle(t, "6mo", spy_df=spy_for_bundles, rfr=rfr)
        except Exception as e:
            errors.append(f"{t}: bundle load failed ({e})")
            continue
        sector = str(data.get("sector") or "") if isinstance(data, dict) else ""
        sector_map[t] = sector
        sec_wt = 0.0
        if sector and port_df is not None and not port_df.empty and "Sector" in port_df.columns:
            gcol = "Gate Weight (%)" if "Gate Weight (%)" in port_df.columns else "Weight (%)"
            try:
                sec_wt = float(port_df[port_df["Sector"] == sector][gcol].sum())
            except Exception:
                sec_wt = 0.0
        pctx = {
            "sector_of_ticker":  sector,
            "sector_weight_pct": sec_wt,
            "portfolio_beta":    portfolio_beta,
            # NOT replicated headlessly — these are session-only Risk Advisor /
            # Grow Today state, not part of any headless computation. Omitting
            # them only weakens the SOFT-caution legs of _portfolio_risk_gate;
            # the HARD sector/beta breach checks (the ones that can downgrade
            # ENTER_NOW to NEAR_ENTRY) are unaffected.
            "active_high_risk_alerts": [],
            "grow_today_sectors":      set(),
        }
        try:
            card = build_watchlist_recommendation(t, data, portfolio_ctx=pctx)
        except Exception as e:
            errors.append(f"{t}: recommendation build failed ({e})")
            continue
        if card.get("action") == "ENTER_NOW":
            qualifying.append(card)

    # ── Capture scope vs. announce scope — deliberately DIFFERENT (coordinator
    # decision, 2026-09-10) ──────────────────────────────────────────────────
    # `qualifying` above is the RAW ENTER_NOW set — every ticker, held or not,
    # before any exclusion — and stays that way through the rec-log capture
    # below. This matches the interactive page's own D1 capture scope exactly
    # (build_enter_now_rows/app.py ~23886: held tickers are INCLUDED, marked
    # already_held=True, not capture-time excluded — the interactive page only
    # turns a held ENTER_NOW into a caution at RENDER time, app.py ~24042).
    # Capture (grading coverage) and announce (what gets emailed) are
    # different concerns: if cron-only days captured zero enter_now rows for
    # held tickers while interactive-visit days captured them, held tickers
    # would be systematically undercounted in the enter_now dataset specifically
    # on days nobody opens Watchlist — undermining the exact "grading coverage
    # doesn't depend on a Watchlist visit" goal this function exists for.
    # A SEPARATE, narrower `eligible` set (below) is derived only for the D-B
    # transition diff and the final `entries` — never for the capture above.

    # Today's EXIT/TRIM/RISK_OFF protective-call tickers — never announce a BUY
    # on a name simultaneously under an active protective call. Same
    # non-distinguishing offline behaviour as the scan lane's own existing
    # exit_alerts read (load_exit_signals collapses "outage" and "empty" to
    # the same empty frame) — mirrored, not a new risk.
    protective_tickers: set = set()
    try:
        sig_df = db.load_exit_signals(days_back=1)
        if sig_df is not None and not sig_df.empty and "signal_date" in sig_df.columns:
            today_str = today.isoformat()
            _rows = sig_df[
                (sig_df["signal_date"].astype(str) == today_str)
                & (sig_df["signal_type"].isin(["EXIT", "TRIM", "RISK_OFF"]))
            ]
            protective_tickers = {
                str(t).strip().upper() for t in _rows["ticker"].tolist() if str(t).strip()
            }
    except Exception as e:
        errors.append(f"exit_signals lookup failed: {e}")

    # D-C (held) + scanner-Go dedup + protective-call exclusion — applied ONLY
    # to derive the email-eligible set, never to the capture above.
    eligible = [
        c for c in qualifying
        if str(c.get("ticker", "")).upper() not in held_set
        and str(c.get("ticker", "")).upper() not in scanner_go
        and str(c.get("ticker", "")).upper() not in protective_tickers
    ]

    # enter_now recommendation-log capture — the FULL RAW qualifying set
    # (every ENTER_NOW ticker, held or not — see the capture-vs-announce note
    # above), so grading coverage doesn't depend on a Watchlist visit on
    # cron-only days, and tomorrow's transition diff always has today's
    # baseline regardless of what's emailed today.
    try:
        _rows = build_enter_now_rows(qualifying, held_set, today, sector_map)
        if _rows:
            _save_result = db.save_recommendations(_rows)
            if _save_result.get("error"):
                errors.append(f"enter_now rec-log save error: {_save_result['error']}")
    except Exception as e:
        errors.append(f"enter_now rec-log capture failed: {e}")

    # D-B: transition-only — diff the EMAIL-ELIGIBLE set (not the raw capture
    # set) against the prior trading day's recorded enter_now set. A failed
    # prior-day lookup means the transition can't be verified this run:
    # suppress rather than risk re-announcing an already-seen name (CLAUDE.md
    # operating posture — recommend nothing rather than recommend wrongly).
    # `None` (not `set()`) distinguishes "couldn't check" from "checked,
    # nothing recorded yesterday".
    prior_tickers: "set | None" = set()
    try:
        prior_date = _prior_trading_day(today)
        prior_df = db.load_recommendations_or_none(start_date=prior_date, end_date=prior_date)
        if prior_df is None:
            prior_tickers = None
            errors.append("prior-day enter_now lookup unavailable — cannot verify transitions "
                          "this run, suppressing all entries")
        elif not prior_df.empty and "rec_type" in prior_df.columns:
            prior_tickers = {
                str(t).strip().upper()
                for t in prior_df[prior_df["rec_type"] == "enter_now"]["ticker"].tolist()
                if str(t).strip()
            }
    except Exception as e:
        prior_tickers = None
        errors.append(f"prior enter_now lookup failed: {e}")

    new_entries = (
        [] if prior_tickers is None else
        [c for c in eligible if str(c.get("ticker", "")).upper() not in prior_tickers]
    )

    return {
        "entries": new_entries, "built_at": built_at, "errors": errors,
        "reason": None, "gate_degraded": gate_degraded,
    }


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
