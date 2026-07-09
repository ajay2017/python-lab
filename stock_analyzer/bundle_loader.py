"""
Per-ticker bundle loader — the full single-ticker analysis pipeline (history →
indicators → all three scores → composite + recommendation + price/stop/targets +
risk metrics), with the last-known-good cache fallbacks.

Extracted verbatim from app.py's `load_all` so the Streamlit app AND the headless
email-alerts cron (exit-discipline Phase 3) share ONE code path — no drift. The
app keeps a thin `@st.cache_data` wrapper around this that supplies the cached SPY
series + risk-free rate; the cron calls `load_bundle` directly with its own.

Pure of Streamlit (no `st.*`): the only side effects are the best-effort Supabase
cache write-throughs via `db.*`, which already degrade to no-ops when the DB is
offline. `spy_df` and `rfr` are injected by the caller (so this never reaches for
a Streamlit-cached helper).
"""

from __future__ import annotations

from stock_analyzer.constants import (
    FUNDAMENTALS_GATE_MIN_METRICS,
    FUNDAMENTALS_CACHE_MAX_AGE_DAYS,
    BUNDLE_CACHE_MAX_AGE_DAYS,
    ATR_STOP_MULT,
    VALUATION_COVERAGE_FRESH_DAYS,
)
from stock_analyzer.data import fetch_ticker_bundle, fetch_financials_from_info
from stock_analyzer.technicals import compute_indicators, technical_score
from stock_analyzer.fundamentals import (
    business_quality_score, fundamental_score,
    upside_potential, count_core_metrics, resolve_fundamentals, CORE_BQ_KEYS,
)
from stock_analyzer.valuation import valuation_score
from stock_analyzer.sentiment import analyze_news, sentiment_score_0_100
from stock_analyzer.scoring import combined_score, recommendation
from stock_analyzer.risk import atr_stop_loss, compute_all_risk
from stock_analyzer.targets import support_resistance, entry_zone, compute_price_targets
from stock_analyzer import db


def _cache_age_in_days(fetched_at_iso: str | None) -> int | None:
    """Whole days between a stored ISO timestamp and now (UTC). None if unparseable.
    Used to bound the last-known-good fundamentals fallback."""
    if not fetched_at_iso:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(fetched_at_iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except Exception:
        return None


def load_bundle(ticker: str, period: str = "6mo", spy_df=None, rfr: float = 0.045) -> dict:
    """Load + analyse one ticker. `spy_df` (benchmark history) and `rfr` are
    injected by the caller; everything else is fetched/computed here.

    Resilience: write-through the raw bundle to a last-known-good cache on every
    successful fetch; if the history/bundle providers ALL fail, serve the aged
    cached copy (tagged stale) so the portfolio still renders instead of cascading
    to "Could not load". All cache I/O is wrapped so it can NEVER break the normal
    success path. Mirrors the original app.load_all exactly (less the Streamlit
    cache + the internal SPY/rfr fetch, now passed in)."""
    _stale_as_of = None
    try:
        bundle = fetch_ticker_bundle(ticker, period)
        try:
            db.save_bundle_cache(ticker, bundle)
        except Exception:
            pass
    except Exception:
        _cached_bundle = None
        try:
            _cached_bundle = db.load_bundle_cache(ticker, BUNDLE_CACHE_MAX_AGE_DAYS)
        except Exception:
            _cached_bundle = None
        if not _cached_bundle:
            raise  # nothing cached (or too stale) → honest "Could not load"
        bundle = _cached_bundle["bundle"]
        _stale_as_of = _cached_bundle["fetched_at"]
    df = compute_indicators(bundle["history"])
    t_score, t_signals = technical_score(df)
    financials = fetch_financials_from_info(bundle["info"])
    # ── Persistent last-known-good fundamentals fallback ──────────────────────
    # The fundamental leg is the fragile one: yfinance .info intermittently
    # returns empty AND the FMP backfill can be quota-exhausted, leaving zero
    # core metrics. Serve the last good copy from Supabase (real data, aged,
    # bounded by FUNDAMENTALS_CACHE_MAX_AGE_DAYS) rather than blacking out on a
    # transient double-miss. Write-through when live is good. db.* degrade to
    # no-ops if the table doesn't exist → live-only.
    _fund_source = "live"
    _fund_cache_age_days = None
    if count_core_metrics(financials) >= FUNDAMENTALS_GATE_MIN_METRICS:
        db.save_fundamentals_cache(ticker, financials)            # write-through
    else:
        _cached = db.load_fundamentals_cache(ticker)
        _cached_age = _cache_age_in_days((_cached or {}).get("fetched_at"))
        financials, _, _fund_source, _fund_cache_age_days = resolve_fundamentals(
            financials,
            (_cached or {}).get("financials"),
            _cached_age,
            FUNDAMENTALS_CACHE_MAX_AGE_DAYS,
            FUNDAMENTALS_GATE_MIN_METRICS,
        )
    # Scoring sector stays on the RAW live .info (empty → _default norms) and does
    # NOT use the cached-sector fallback below — deliberately. Feeding a cached
    # sector here would silently move fundamental/composite scores on a sparse day
    # (a scoring change dressed as a data-resilience fix). Classification/gating
    # gets the resilient sector; scoring does not. Don't "unify" these.
    _sector_for_scoring = bundle.get("info", {}).get("sector", "")
    bq_score, bq_signals = business_quality_score(financials, _sector_for_scoring)
    _fund_metric_count = count_core_metrics(financials)
    bq_available = _fund_metric_count >= FUNDAMENTALS_GATE_MIN_METRICS
    # Last *valid* close. compute_indicators already strips NaN-Close bars, but
    # guard here too — defense in depth: float(NaN) is truthy, so a stray NaN
    # would pass every `if price:` check and render "$nan". None = honest "no price".
    # Price is computed here (before valuation_score) because the PT-upside metric
    # needs current_price to compute % upside to consensus target.
    _closes = df["Close"].dropna() if not df.empty else None
    price = float(_closes.iloc[-1]) if _closes is not None and not _closes.empty else None
    # ── Analyst coverage for Valuation pillar ──────────────────────────────────
    _analyst_data: dict = {"avg_pt": None, "consensus_label": None, "has_coverage": False}
    try:
        import json as _json
        _cov_df = db.load_analyst_coverage(ticker=ticker, days=VALUATION_COVERAGE_FRESH_DAYS)
        if _cov_df is not None and not _cov_df.empty:
            _all_analysts: list = []
            for _, _row in _cov_df.iterrows():
                _raw = _row.get("analysts") or []
                if isinstance(_raw, str):
                    try:
                        _raw = _json.loads(_raw)
                    except Exception:
                        _raw = []
                if isinstance(_raw, list):
                    _all_analysts.extend(_raw)
            if _all_analysts:
                from stock_analyzer.analyst_intel import derive_consensus
                _cons = derive_consensus(_all_analysts)
                _raw_label = _cons.get("consensus_rating") or ""
                _label = _raw_label.split(" (")[0] if " (" in _raw_label else (_raw_label or None)
                _analyst_data = {
                    "avg_pt":          _cons.get("avg_pt"),
                    "consensus_label": _label,
                    "has_coverage":    bool(_label),
                }
    except Exception:
        pass  # silent degrade — valuation scores on P/E + FCF alone
    val_score, val_signals = valuation_score(financials, _analyst_data, price, _sector_for_scoring)
    avg_sent, headlines = analyze_news(bundle["news"])
    s_score = sentiment_score_0_100(avg_sent)
    total = combined_score(t_score, bq_score, val_score, s_score)
    rec = recommendation(total)
    stop, atr_val = atr_stop_loss(df, multiplier=ATR_STOP_MULT)
    entry_lo, entry_hi = entry_zone(price, atr_val) if price else (None, None)
    targets = compute_price_targets(df, financials, price) if price else None
    sr = support_resistance(df)
    try:
        risk_metrics = compute_all_risk(df, spy_df, rfr)
    except Exception:
        risk_metrics = compute_all_risk(df, None, rfr)
    upside = upside_potential(price, financials) if price else None
    _info  = bundle.get("info", {})
    name   = _info.get("shortName") or _info.get("longName") or ticker
    sector = _info.get("sector", "")
    # Sector resilience: `.info` is the only LIVE source for sector and it comes
    # back sparse on Yahoo's flaky days → an unmapped holding would collapse to
    # the "Other" catch-all AND drop out of the sector-concentration gate (which
    # excludes "Other"). Sector is near-static, so write-through a good value and
    # fall back to the last-known one when the live fetch is empty (mirrors the
    # fundamentals-cache resilience). Best-effort; no-ops until the table exists.
    if sector:
        db.save_sector_cache(ticker, sector)
    else:
        sector = db.load_sector_cache(ticker) or ""
    industry          = _info.get("industry", "")
    market_cap        = _info.get("marketCap")
    business_summary  = _info.get("longBusinessSummary", "")
    return {
        "df": df, "t_score": t_score, "t_signals": t_signals,
        # New 4-pillar keys:
        "bq_score":         bq_score,  "bq_signals":   bq_signals,
        "val_score":        val_score, "val_signals":  val_signals,
        "val_analyst_data": _analyst_data,
        "bq_available":     bq_available,
        # Backward compat aliases (keep for one release):
        "f_score":          bq_score,  "f_signals":    bq_signals,
        "fundamentals_available": bq_available,
        "s_score": s_score, "avg_sent": avg_sent, "headlines": headlines,
        "total": total, "rec": rec, "financials": financials,
        "current_price": price, "upside": upside,
        "atr": atr_val, "stop": stop, "news_raw": bundle["news"],
        "entry_lo": entry_lo, "entry_hi": entry_hi,
        "targets": targets, "sr": sr,
        "risk_metrics": risk_metrics, "earnings": bundle["earnings"],
        "revisions": bundle.get("revisions", {}),
        "name": name, "sector": sector,
        "industry": industry, "market_cap": market_cap,
        "business_summary": business_summary,
        "info_source": bundle.get("_info_source"),
        "fund_metric_count": _fund_metric_count,
        "fund_source": _fund_source,
        "fund_cache_age_days": _fund_cache_age_days,
        "stale_as_of": _stale_as_of,
    }
