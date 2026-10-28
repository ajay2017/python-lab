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

import math
from datetime import datetime, timezone

from stock_analyzer.constants import (
    FUNDAMENTALS_GATE_MIN_METRICS,
    FUNDAMENTALS_CACHE_MAX_AGE_DAYS,
    BUNDLE_CACHE_MAX_AGE_DAYS,
    ETF_LOOKTHROUGH_CACHE_MAX_AGE_DAYS,
    ATR_STOP_MULT,
    VALUATION_COVERAGE_FRESH_DAYS,
    VALUATION_PT_MIN_ANALYSTS,
    VALUATION_PT_MAX_TARGET_RATIO,
)
from stock_analyzer.data import (
    _parse_news_item,
    fetch_ticker_bundle, fetch_financials_from_info, fetch_etf_facts_from_info,
    fetch_etf_lookthrough,
)
from stock_analyzer import asset_type
from stock_analyzer import llm
from stock_analyzer import etf_scoring
from stock_analyzer.technicals import compute_indicators, technical_score, technical_coverage
from stock_analyzer.fundamentals import (
    business_quality_score, fundamental_score,
    upside_potential, count_core_metrics, resolve_fundamentals, CORE_BQ_KEYS,
)
from stock_analyzer.valuation import valuation_score
from stock_analyzer.valuation_target import choose_pt_target, pt_short_tag, pt_label, base_target_note
from stock_analyzer.market_time import ET, now_et
import logging
from stock_analyzer.sentiment import analyze_news, sentiment_score_0_100
from stock_analyzer.scoring import combined_score, recommendation
from stock_analyzer.risk import atr_stop_loss, compute_all_risk
from stock_analyzer.targets import support_resistance, entry_zone, compute_price_targets
from stock_analyzer import db
from stock_analyzer import news_feed as _news_feed
from stock_analyzer import news_relevance as _news_rel

# Unrelated headlines carried for display (plumbing, not policy).
_UNRELATED_SHOWN = 10


def _cache_age_in_days(fetched_at_iso: str | None) -> int | None:
    """Whole days between a stored ISO timestamp and now (UTC). None if unparseable.
    Used to bound the last-known-good fundamentals fallback."""
    if not fetched_at_iso:
        return None
    try:
        dt = datetime.fromisoformat(str(fetched_at_iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except Exception:
        return None


_log = logging.getLogger(__name__)


def _anthropic_section_secret(section, key):
    """Secrets getter for llm.resolve_anthropic_key: only the [anthropic] section.
    bundle_loader has never read a flat ANTHROPIC_API_KEY secret; reading one
    could switch on LLM news scoring (which moves the composite) on a deploy
    whose secrets carry only the flat key."""
    if section != "anthropic":
        return ""
    import streamlit as _st
    return (_st.secrets.get("anthropic") or {}).get(key, "")


def _et_date_str(ts) -> str | None:
    """A stored UTC (or tz-aware) ISO timestamp -> its America/New_York calendar
    date 'YYYY-MM-DD'. Naive stamps are UTC (that is how the caches write them).
    A bare 'YYYY-MM-DD' is already a calendar date and is returned unchanged —
    `db.load_bundle_cache` stores `fetched` as a UTC DATE, which can't be converted
    (reading it as UTC midnight would move every one a day early); for an
    evening-ET fetch it can read one day ahead. None if absent/unparseable."""
    if not ts:
        return None
    s = str(ts).strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET).date().isoformat()
    except Exception:
        return None


def _coerce_pt_str(v):
    """A saved target stored as a string ("685", "$700", "1,200.50") -> float.
    Anything else (incl. unparseable / non-finite strings) is returned unchanged;
    the chooser rejects junk itself."""
    if isinstance(v, str):
        try:
            f = float(v.strip().replace("$", "").replace(",", ""))
        except ValueError:
            return v
        return f if math.isfinite(f) else v
    return v


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
    _from_cache = False
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
        _from_cache = True
    # Per-ticker news feed status. A last-known-good cached bundle carries no news
    # (never cached there) -> "cached". A bundle from a provider without its own
    # status (not Yahoo) reads "ok" only when it really has headlines; an empty
    # one cannot be told from a failed fetch there, so it reads "error" (loud).
    if _from_cache:
        _news_status = _news_feed.NEWS_STATUS_CACHED
    else:
        _news_status = bundle.get("news_status") or (
            _news_feed.NEWS_STATUS_OK if bundle.get("news") else _news_feed.NEWS_STATUS_ERROR)
    df = compute_indicators(bundle["history"])
    t_score, t_signals = technical_score(df)
    _t_cov = technical_coverage(df, t_signals)
    financials = fetch_financials_from_info(bundle["info"])
    # ── Persistent last-known-good fundamentals fallback ──────────────────────
    # The fundamental leg is the fragile one: yfinance .info intermittently
    # returns empty AND the profile-only FMP refill can fail or be paused, leaving zero
    # core metrics. Serve the last good copy from Supabase (real data, aged,
    # bounded by FUNDAMENTALS_CACHE_MAX_AGE_DAYS) rather than blacking out on a
    # transient double-miss. Write-through when live is good. db.* degrade to
    # no-ops if the table doesn't exist → live-only.
    _fund_source = "live"
    _fund_cache_age_days = None
    _fund_cache_fetched_at = None   # the cache row's own fetch time (NB-1a)
    if count_core_metrics(financials) >= FUNDAMENTALS_GATE_MIN_METRICS:
        db.save_fundamentals_cache(ticker, financials)            # write-through
    else:
        _cached = db.load_fundamentals_cache(ticker)
        _fund_cache_fetched_at = (_cached or {}).get("fetched_at")
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
    # Item C (2026-10-21): the PT Upside target is chosen ONCE here, provider
    # first (valuation_target.choose_pt_target), the owner's saved research only
    # as a fallback. The saved-research RATING leg is unchanged (owner D-1).
    _consensus_label = None
    _consensus_tag = None
    _saved_targets = None   # None = coverage read FAILED (never "no coverage")
    try:
        import json as _json
        _cov_df = db.load_analyst_coverage_or_none(
            ticker=ticker, days=VALUATION_COVERAGE_FRESH_DAYS)
        if _cov_df is not None:
            _saved_targets = []
            _all_analysts: list = []
            _row_dates: list = []
            if not _cov_df.empty:
                for _, _row in _cov_df.iterrows():
                    _raw = _row.get("analysts") or []
                    if isinstance(_raw, str):
                        try:
                            _raw = _json.loads(_raw)
                        except Exception:
                            _raw = []
                    if isinstance(_raw, list):
                        _all_analysts.extend(_raw)
                        for _a in _raw:
                            if isinstance(_a, dict):
                                _saved_targets.append({
                                    "firm": _a.get("firm"),
                                    "price_target": _coerce_pt_str(_a.get("price_target")),
                                    "article_date": _a.get("article_date") or _row.get("article_date"),
                                })
                                _d = _a.get("article_date") or _row.get("article_date")
                                if _d:
                                    _row_dates.append(str(_d)[:10])
            if _all_analysts:
                from stock_analyzer.analyst_intel import derive_consensus
                _cons = derive_consensus(_all_analysts)
                _raw_label = _cons.get("consensus_rating") or ""
                _consensus_label = (_raw_label.split(" (")[0] if " (" in _raw_label
                                    else (_raw_label or None))
                if _consensus_label:
                    _firms = {str(a.get("firm") or "").strip().lower()
                              for a in _all_analysts if isinstance(a, dict)} - {""}
                    _nf = len(_firms) or len(_all_analysts)
                    _consensus_tag = (f"your saved research, {_nf} firm{'' if _nf == 1 else 's'}"
                                      + (f", newest {max(_row_dates)}" if _row_dates else ""))
    except Exception as _cov_exc:
        _saved_targets = None  # degrade — valuation scores on P/E + FCF alone
        _consensus_label = None
        _consensus_tag = None
        # The user still sees "couldn't be read". A DB outage returns None without
        # raising, so an exception here is usually a CODE bug: log it (server log
        # only). Deliberately NOT api_health — this source has no Data Health /
        # System Trust row, so recording it would turn the health icon red with
        # nothing explaining why (system_health.py ~277-283).
        _log.warning("saved-research read for %s raised %s: %s",
                     ticker, type(_cov_exc).__name__, _cov_exc)
    _fin_for_pt = financials if isinstance(financials, dict) else {}
    # Source key: `_source` is the provider that served the bundle; `_info_source`
    # is set only when ANOTHER provider back-filled sparse info (so it wins).
    # A bundle served from the last-known-good bundle cache carries neither
    # (the cache stores only history + info) -> generic "data provider" label.
    # as_of = when the provider's target was actually fetched, as an America/New_York
    # date: the fundamentals-cache row's own stamp when financials (and so the
    # target) came from that cache — checked FIRST, since a stale bundle can still
    # take its financials from the fundamentals cache — else the bundle cache's
    # date on a stale bundle, else now.
    if _fund_source != "live":
        _pt_as_of = _et_date_str(_fund_cache_fetched_at)   # None if unknown -> no date shown
    elif _stale_as_of is not None:
        _pt_as_of = _et_date_str(_stale_as_of)
    else:
        _pt_as_of = now_et().date().isoformat()
    _pt_choice = choose_pt_target(
        _fin_for_pt.get("analyst_target"), _fin_for_pt.get("num_analyst_opinions"),
        # Financials from the fundamentals cache may have been written by a
        # different provider than this bundle's, so name none (generic label).
        (bundle.get("_info_source") or bundle.get("_source")) if _fund_source == "live" else None,
        _pt_as_of,
        _stale_as_of is not None or _fund_source != "live",
        _saved_targets, price,
        min_analysts=VALUATION_PT_MIN_ANALYSTS, max_ratio=VALUATION_PT_MAX_TARGET_RATIO)
    _analyst_data: dict = {
        "avg_pt":          _pt_choice["target"],
        "pt_tag":          pt_short_tag(_pt_choice),
        "consensus_label": _consensus_label,
        "has_coverage":    bool(_consensus_label),
        "consensus_tag":   _consensus_tag,
    }
    if _pt_choice["source"] is None:
        # Why PT Upside is not scored (no `$` in this branch's text) -- shown in the
        # valuation signals instead of the leg silently vanishing.
        _analyst_data["pt_not_scored"] = pt_label(_pt_choice, fresh_days=VALUATION_COVERAGE_FRESH_DAYS)
    val_score, val_signals, val_available = valuation_score(financials, _analyst_data, price, _sector_for_scoring)
    # Headline relevance (docs/plans/headline-relevance.md): the feed tags every
    # ticker a story merely mentions, so keep only headlines whose TITLE names this
    # company/ticker -- once, here, so every news consumer reads the same set. Cap
    # AFTER the split (a relevant older headline must not lose its slot to newer
    # unrelated ones). No usable company name -> fail open (all tagged kept).
    _rel_info = bundle.get("info")
    _rel_extra = bundle.get("news_names")
    _rel_names = [
        _rel_info.get(k) if isinstance(_rel_info, dict) else None
        for k in ("shortName", "longName", "displayName")
    ] + (_rel_extra if isinstance(_rel_extra, list) else [])
    _rel_names = [n for n in _rel_names if isinstance(n, str) and n.strip()]
    _split = _news_rel.split_relevant(bundle["news"], ticker, _rel_names)
    _news_kept = _split["kept"][:_news_feed.NEWS_KEEP_PER_TICKER]
    _unrelated_rows = []
    for _u in _split["unrelated"][:_UNRELATED_SHOWN]:
        _ut, _up, _uu, _uts = _parse_news_item(_u)
        _unrelated_rows.append({"title": _ut, "publisher": _up, "ts": _uts, "url": _uu})
    _unrelated_n = (len(_split["unrelated"])
                    if _split["checked"] and not _from_cache else None)
    avg_sent, headlines = analyze_news(_news_kept)
    # Bidirectional LLM rescore — ticker-aware financial context, persisted to
    # sentiment_llm_cache so app + cron always use the same composite for a given
    # ticker on a given UTC day. Cache miss → call LLM → write cache. Any failure
    # at any step leaves avg_sent / headlines as the original VADER values (fail-open).
    try:
        import datetime as _dt
        from stock_analyzer.news_intelligence import rescore_headlines_llm as _rescore_hl
        _today = _dt.date.today().isoformat()
        _cached = db.load_sentiment_llm_cache(ticker, _today)
        if _cached is not None and _split["checked"]:
            # The shared day-cache can hold rows written before this filter existed
            # (or by a keyed deploy over unfiltered headlines): relevance-filter them
            # with the same terms and recompute the average from what survives.
            _crows = _news_rel.filter_scored(
                _cached.get("headlines"), ticker, _rel_names)
            _csc = [h["score"] for h in _crows if isinstance(h.get("score"), (int, float))]
            _cached = dict(_cached, headlines=_crows,
                           avg_sent=round(sum(_csc) / len(_csc), 3) if _csc else 0.0)
        if _news_feed.llm_cache_usable(_cached, headlines):
            headlines = _cached["headlines"]
            avg_sent  = _cached["avg_sent"]
        else:
            # Read key: env first (Railway cron lanes), then the [anthropic] secret
            _ant_key = llm.resolve_anthropic_key(_anthropic_section_secret)
            if _ant_key:
                _rescored = _rescore_hl(headlines, _ant_key, ticker)
                # None = LLM failed; an EMPTY rescore (no headlines) must never be
                # saved either -- it would serve "no news" for the rest of the day.
                if _news_feed.should_save_llm_rescore(_rescored):
                    _sc = [h["score"] for h in _rescored if isinstance(h.get("score"), (int, float))]
                    _new_avg = round(sum(_sc) / len(_sc), 3) if _sc else avg_sent
                    db.save_sentiment_llm_cache(ticker, _today, _rescored, _new_avg)
                    headlines = _rescored
                    avg_sent  = _new_avg
    except Exception:
        pass
    s_score = sentiment_score_0_100(avg_sent)
    total = combined_score(t_score, bq_score, val_score, s_score)
    rec = recommendation(total)
    stop, atr_val = atr_stop_loss(df, multiplier=ATR_STOP_MULT)
    entry_lo, entry_hi = entry_zone(price, atr_val) if price else (None, None)
    # Built from the CHOSEN target (item C, D-6), never the raw provider target,
    # because targets["base"] feeds the Watchlist ENTER_NOW R:R gate.
    targets = compute_price_targets(df, financials, price, _pt_choice["target"]) if price else None
    if isinstance(targets, dict):
        targets["base_note"] = base_target_note(targets, _pt_choice)
    sr = support_resistance(df, current_price=price)
    try:
        risk_metrics = compute_all_risk(df, spy_df, rfr)
    except Exception:
        risk_metrics = compute_all_risk(df, None, rfr)
    # Built from the CHOSEN target (item C), never the raw provider target: a
    # rejected target must not show as "upside to analyst target" elsewhere.
    upside = (upside_potential(price, {"analyst_target": _pt_choice["target"]},
                               source_tag=pt_short_tag(_pt_choice))
              if price and _pt_choice["target"] is not None else None)
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
    # Asset-type observability (Phase 1, F-279 §11). Phase 0 already makes
    # bq_available/val_available correctly withhold for a fund via the
    # fundamentals-metric-count gate, independent of this discriminator.
    # Phase 2 (below) is the first consumer that reads asset_type/etf_facts
    # for an actual decision (the ETF composite).
    _quote_type = _info.get("quoteType")
    _asset_type = asset_type.from_quote_type(_quote_type)
    _etf_facts = (
        fetch_etf_facts_from_info(_info)
        if _asset_type == asset_type.ASSET_TYPE_ETF else None
    )
    # ETF composite scoring (Phase 2, F-279 §11). Additive only, and scoped
    # entirely to asset_type == "etf" — a stock bundle's _etf_facts is
    # already None above, so every one of these five keys is None/False for
    # a stock: "not applicable", never a fabricated value. `t_score` is the
    # SAME technical score computed earlier in this function for every
    # bundle (stock or ETF) — reused, never recomputed.
    _etf_available = etf_scoring.etf_available(_etf_facts)
    _etf_cost_score = etf_scoring.expense_ratio_score(
        (_etf_facts or {}).get("net_expense_ratio")
    )
    _etf_total = (
        etf_scoring.etf_composite(t_score, _etf_cost_score)
        if _etf_available and _etf_cost_score is not None else None
    )
    _etf_rec = (
        etf_scoring.etf_recommendation(_etf_total)
        if _etf_total is not None else None
    )
    _etf_aum_thin = etf_scoring.etf_aum_thin((_etf_facts or {}).get("total_assets"))
    # ETF sector look-through (Phase 3a, ETF-support). Additive only, scoped
    # entirely to asset_type == "etf" -- a stock bundle's _etf_lookthrough is
    # None, same "not applicable" contract as _etf_facts/_etf_available above.
    # Cache-FIRST (the reverse of the fundamentals resolve/write-through
    # pattern above, which is live-first): a fund's sector composition moves
    # far more slowly than a stock's fundamentals, so check the persistent
    # cache before spending a live `funds_data` call, and only fetch live on a
    # miss or an entry older than ETF_LOOKTHROUGH_CACHE_MAX_AGE_DAYS. Never
    # raises -- fetch_etf_lookthrough and the db.* cache calls already
    # degrade to None/no-op on any failure.
    _etf_lookthrough = None
    if _asset_type == asset_type.ASSET_TYPE_ETF:
        _cached_lt = db.load_etf_lookthrough_cache(ticker)
        _cached_lt_age = _cache_age_in_days((_cached_lt or {}).get("fetched_at"))
        if (
            _cached_lt is not None
            and _cached_lt_age is not None
            and _cached_lt_age <= ETF_LOOKTHROUGH_CACHE_MAX_AGE_DAYS
        ):
            _etf_lookthrough = _cached_lt["payload"]
        else:
            _etf_lookthrough = fetch_etf_lookthrough(ticker)
            if _etf_lookthrough is not None:
                db.save_etf_lookthrough_cache(ticker, _etf_lookthrough)
    return {
        "df": df, "t_score": t_score, "t_signals": t_signals,
        "t_available": _t_cov["available"], "t_bars": _t_cov["bars"],
        # New 4-pillar keys:
        "bq_score":         bq_score,  "bq_signals":   bq_signals,
        "val_score":        val_score, "val_signals":  val_signals,
        "val_analyst_data": _analyst_data,
        "val_pt_choice":    _pt_choice,
        "upside_tag":       pt_short_tag(_pt_choice) if _pt_choice["target"] is not None else None,
        "bq_available":     bq_available,
        "val_available":    val_available,
        # Backward compat aliases (keep for one release):
        "f_score":          bq_score,  "f_signals":    bq_signals,
        "fundamentals_available": bq_available,
        "s_score": s_score, "avg_sent": avg_sent, "headlines": headlines,
        "total": total, "rec": rec, "financials": financials,
        "current_price": price, "upside": upside,
        "atr": atr_val, "stop": stop, "news_raw": _news_kept, "news_status": _news_status,
        "news_unrelated": _unrelated_rows, "news_unrelated_n": _unrelated_n,
        "news_relevance_checked": _split["checked"],
        "entry_lo": entry_lo, "entry_hi": entry_hi,
        "targets": targets, "sr": sr,
        "risk_metrics": risk_metrics, "earnings": bundle["earnings"],
        "revisions": bundle.get("revisions", {}),
        "name": name, "sector": sector,
        "industry": industry, "market_cap": market_cap,
        "business_summary": business_summary,
        "info_source": bundle.get("_info_source"),
        "data_source": bundle.get("_source"),   # provider that served the bundle (None from the bundle cache)
        "quote_type": _quote_type,
        "asset_type": _asset_type,
        "etf_facts": _etf_facts,
        "etf_available":  _etf_available,
        "etf_cost_score": _etf_cost_score,
        "etf_total":      _etf_total,
        "etf_rec":        _etf_rec,
        "etf_aum_thin":   _etf_aum_thin,
        "etf_lookthrough": _etf_lookthrough,
        "fund_metric_count": _fund_metric_count,
        "fund_source": _fund_source,
        "fund_cache_age_days": _fund_cache_age_days,
        "stale_as_of": _stale_as_of,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
