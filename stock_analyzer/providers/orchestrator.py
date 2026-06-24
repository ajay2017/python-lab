"""
Failover orchestrator + price cross-check across the configured provider chain.

`data.py` delegates here ONLY when `constants.DATA_MULTISOURCE_ENABLED` is True;
when it's False, data.py calls the single yfinance provider directly so
behaviour is byte-for-byte the pre-provider code. See memory
`project_second_data_source` for the converged design.

Two mechanisms:
  • FAILOVER — try providers in `DATA_PROVIDER_ORDER` (the first being primary),
    skipping any that aren't configured or don't advertise the capability, until
    one returns a usable result. live_prices is GAP-FILL failover (primary fills
    most tickers; later providers fill only the ones still missing).
  • CROSS-CHECK — `crosscheck_price()` validates the live-price primary against
    the next INDEPENDENT source: prev_close strict (DATA_XCHECK_PREVCLOSE_TOL_PCT)
    + live price loose (DATA_XCHECK_LIVE_TOL_PCT). Invoked deliberately by callers
    (not auto-run on the 60s strip), so it doesn't burn keyed free-tier quota or
    add latency to every refresh.

NOTE: this module must NOT be imported by providers/__init__.py — it imports the
registry FROM __init__, so importing it there would create a cycle.
"""

import pandas as pd

from stock_analyzer import constants as C
from stock_analyzer import api_health as _ah
from stock_analyzer.providers import PROVIDER_REGISTRY
from stock_analyzer.providers.base import (
    ProviderUnavailable,
    CAP_LIVE_PRICE, CAP_HISTORY, CAP_BUNDLE, CAP_INDICES, CAP_RISK_FREE,
)

_chain_cache: dict[tuple, list] = {}


def _chain_for(order: list) -> list:
    """Configured providers for a given order list (key present), cached per
    order. Construction reads keys from secrets/env, so the cache is rebuilt only
    on reset() (or a process restart, e.g. a Streamlit reboot after a secrets
    change)."""
    key = tuple(order)
    if key not in _chain_cache:
        out = []
        for name in order:
            cls = PROVIDER_REGISTRY.get(name)
            if cls is None:
                continue
            try:
                prov = cls()
            except Exception:
                continue
            if prov.is_configured():
                out.append(prov)
        _chain_cache[key] = out
    return _chain_cache[key]


def chain() -> list:
    """General failover chain (history / bundle / indices / risk-free)."""
    return _chain_for(C.DATA_PROVIDER_ORDER)


def reset() -> None:
    """Drop all cached chains (call after a secrets/config change)."""
    _chain_cache.clear()


def _providers_for(capability: str) -> list:
    capable = [p for p in chain() if p.supports(capability)]
    # Circuit-breaker (rate-limit-resilience Phase 2): skip providers actively
    # tripped on 429s so we don't re-hammer them ticker-after-ticker (the FMP
    # quota-exhaustion amplifier). If that would leave NO provider (all cooled),
    # fall through to the full capable list — degrade to a live attempt + the
    # caller's cache fallback rather than ever hard-blocking. Auto-recovers when
    # PROVIDER_RL_COOLDOWN_SEC elapses.
    # NB: p.name must equal the api_health source key the provider records under
    # (yahoo_finance / finnhub / fmp) for the cooldown lookup to match.
    live = [p for p in capable
            if not _ah.in_cooldown(p.name, C.PROVIDER_RL_COOLDOWN_SEC)]
    return live if live else capable


# ── Single-result failover (history / bundle / indices / risk-free) ──────────
def _failover_single(capability: str, method: str, *args, **kwargs):
    """Try each capable provider in order; return the first non-empty result.
    Re-raises the last error if every provider fails (preserves the old
    single-source contract where the caller handles the exception)."""
    last_exc: Exception | None = None
    for prov in _providers_for(capability):
        try:
            result = getattr(prov, method)(*args, **kwargs)
        except ProviderUnavailable as exc:
            last_exc = exc
            continue
        except Exception as exc:
            last_exc = exc
            continue
        # Treat None / empty frame / empty container as "no data → try next".
        if result is None:
            continue
        if isinstance(result, pd.DataFrame) and result.empty:
            continue
        if isinstance(result, (list, dict)) and len(result) == 0:
            continue
        return result
    if last_exc is not None:
        raise last_exc
    raise ProviderUnavailable(f"no configured provider served {method}")


def get_history(ticker: str, period: str = "6mo") -> pd.DataFrame:
    return _failover_single(CAP_HISTORY, "price_history", ticker, period)


def _info_sparse(info: dict | None) -> bool:
    """True when an `.info` dict can't support fundamental scoring — yfinance's
    `.info` intermittently returns empty/sparse, which would collapse the
    fundamental score to a neutral 50 and flip a Buy to a Hold on a data hiccup
    rather than a real change."""
    if not info:
        return True
    anchors = ("marketCap", "trailingPE", "profitMargins", "revenueGrowth", "returnOnEquity")
    return not any(info.get(k) is not None for k in anchors)


def get_bundle(ticker: str, period: str = "6mo") -> dict:
    """Bundle with failover AND fundamental backfill. The primary (yfinance)
    bundle is used for history + news (sentiment); if its `.info` is sparse (a
    yfinance hiccup), fundamentals are backfilled from the next bundle-capable
    provider (FMP) so the composite stays stable across surfaces instead of
    flipping Buy↔Hold on missing `.info`. Keeps the richest of both sources."""
    providers = _providers_for(CAP_BUNDLE)
    primary = None
    last_exc: Exception | None = None
    for prov in providers:
        try:
            b = prov.bundle(ticker, period)
        except Exception as exc:
            last_exc = exc
            continue
        if not b:
            continue
        hist = b.get("history")
        if isinstance(hist, pd.DataFrame) and hist.empty:
            continue
        primary = b
        primary.setdefault("_source", prov.name)
        break
    if primary is None:
        if last_exc is not None:
            raise last_exc
        raise ProviderUnavailable(f"no configured provider served bundle for {ticker}")

    # Backfill fundamentals if the primary's .info is sparse.
    if _info_sparse(primary.get("info")):
        for prov in providers:
            if prov.name == primary.get("_source") or not hasattr(prov, "info"):
                continue
            try:
                other_info = prov.info(ticker)
            except Exception:
                continue
            if not _info_sparse(other_info):
                primary["info"] = other_info
                primary["_info_source"] = prov.name
                break

    # Backfill the earnings DATE and revisions INDEPENDENTLY of .info. yfinance
    # routinely returns usable `.info` (so the composite scores fine) yet NO
    # earnings date — they come from different yfinance endpoints. Coupling the
    # date backfill to info-sparse (as before) therefore left held names with a
    # blank earnings date whenever `.info` happened to be present — surfacing as
    # Catalyst Watch "No date found" AND silently disarming the earnings-proximity
    # gates (which read this same field). Fill from the first OTHER bundle-capable
    # provider's LIGHT per-field accessor (1 call each, not a full second bundle)
    # only when the field is missing — so it can never regress; an FMP miss leaves
    # the field exactly as it was. The circuit-breaker (_providers_for) still skips
    # any provider currently cooled-down, so this won't hammer an exhausted source.
    if not primary.get("earnings") or not primary.get("revisions"):
        for prov in providers:
            if prov.name == primary.get("_source"):
                continue
            if not primary.get("earnings") and hasattr(prov, "earnings"):
                try:
                    primary["earnings"] = prov.earnings(ticker)
                except Exception:
                    pass
            if not primary.get("revisions") and hasattr(prov, "revisions"):
                try:
                    primary["revisions"] = prov.revisions(ticker)
                except Exception:
                    pass
            if primary.get("earnings") and primary.get("revisions"):
                break
    return primary


def get_earnings_calendar(from_date: str, to_date: str) -> list[dict]:
    """Market-wide upcoming earnings for a date range. Only FMP serves this, so
    it's a best-effort single-source lookup: return [] (not an error) when no
    provider in the chain offers it, so Catalyst Watch degrades to held-only
    earnings rather than failing the page."""
    for prov in chain():
        if hasattr(prov, "earnings_calendar"):
            try:
                return prov.earnings_calendar(from_date, to_date) or []
            except Exception:
                return []
    return []


def get_market_indices() -> list:
    return _failover_single(CAP_INDICES, "market_indices")


def get_risk_free_rate() -> float:
    return _failover_single(CAP_RISK_FREE, "risk_free_rate")


# ── Gap-fill failover for live prices (own real-time-first order) ────────────
def _live_price_providers() -> list:
    return [p for p in _chain_for(C.DATA_LIVE_PRICE_ORDER) if p.supports(CAP_LIVE_PRICE)]


def get_live_prices(tickers: list[str]) -> dict[str, dict]:
    """Live-price primary (Finnhub, real-time) fills as many tickers as it can;
    subsequent providers in DATA_LIVE_PRICE_ORDER gap-fill only the ones still
    missing. A provider failing is skipped, not fatal — so a Finnhub outage
    silently degrades to yfinance (delayed), never worse than today."""
    if not tickers:
        return {}
    results: dict[str, dict] = {}
    for prov in _live_price_providers():
        remaining = [t for t in tickers if t not in results]
        if not remaining:
            break
        try:
            got = prov.live_prices(remaining)
        except Exception:
            continue
        for t, v in (got or {}).items():
            results.setdefault(t, v)
    return results


# ── Price cross-check (deliberate, not auto-run) ─────────────────────────────
def crosscheck_price(ticker: str, primary_price: float,
                     primary_prev_close: float | None = None) -> dict | None:
    """Validate the live-price primary's reading for `ticker` against the next
    INDEPENDENT price source. Returns None when disabled / no validator / no
    comparable data, else a dict:

        {ok, source, prev_gap_pct, prev_ok, live_gap_pct, live_ok, other_price}

    prev_close is checked strictly (DATA_XCHECK_PREVCLOSE_TOL_PCT) — a settled
    value that must match across sources, so a breach is a real integrity fault.
    live price is checked loosely (DATA_XCHECK_LIVE_TOL_PCT) because a delayed
    validator legitimately differs from a real-time primary intraday. `ok` is
    the AND of whichever checks could run; the caller surfaces `not ok` loudly."""
    if "price" not in C.DATA_XCHECK_FIELDS or not primary_price:
        return None
    order = C.DATA_LIVE_PRICE_ORDER or C.DATA_PROVIDER_ORDER
    primary_name = order[0] if order else None
    for prov in _live_price_providers():
        if prov.name == primary_name:
            continue  # need an INDEPENDENT source to validate
        try:
            rec = (prov.live_prices([ticker]) or {}).get(ticker)
        except Exception:
            continue
        if not rec:
            continue
        other_price = rec.get("price")
        other_pc    = rec.get("prev_close")
        result: dict = {"source": prov.name,
                        "other_price": round(float(other_price), 2) if other_price else None}
        ok = True
        ran_any = False
        if primary_prev_close and other_pc:
            pg = abs(float(primary_prev_close) - float(other_pc)) / float(primary_prev_close) * 100
            result["prev_gap_pct"] = round(pg, 3)
            result["prev_ok"] = pg <= float(C.DATA_XCHECK_PREVCLOSE_TOL_PCT)
            ok = ok and result["prev_ok"]
            ran_any = True
        if other_price:
            lg = abs(float(primary_price) - float(other_price)) / float(primary_price) * 100
            result["live_gap_pct"] = round(lg, 3)
            result["live_ok"] = lg <= float(C.DATA_XCHECK_LIVE_TOL_PCT)
            ok = ok and result["live_ok"]
            ran_any = True
        if not ran_any:
            continue
        result["ok"] = ok
        return result
    return None


def _compare(primary_price, primary_pc, other_price, other_pc) -> dict | None:
    """Build a cross-check result comparing one ticker's primary vs validator
    readings. prev_close strict, live loose. None if nothing comparable."""
    result: dict = {"other_price": round(float(other_price), 2) if other_price else None}
    ok, ran = True, False
    if primary_pc and other_pc:
        pg = abs(float(primary_pc) - float(other_pc)) / float(primary_pc) * 100
        result["prev_gap_pct"] = round(pg, 3)
        result["prev_ok"] = pg <= float(C.DATA_XCHECK_PREVCLOSE_TOL_PCT)
        ok &= result["prev_ok"]; ran = True
    if primary_price and other_price:
        lg = abs(float(primary_price) - float(other_price)) / float(primary_price) * 100
        result["live_gap_pct"] = round(lg, 3)
        result["live_ok"] = lg <= float(C.DATA_XCHECK_LIVE_TOL_PCT)
        ok &= result["live_ok"]; ran = True
    if not ran:
        return None
    result["ok"] = ok
    return result


def crosscheck_batch(tickers: list[str]) -> dict[str, dict]:
    """Batch cross-check for many tickers in just TWO calls: the live-price
    primary (Finnhub) and the first INDEPENDENT validator (yfinance, one batched
    download). Returns {ticker: result} only for tickers checkable against an
    independent source. Used by the portfolio-page guardrail; cached upstream so
    it runs periodically, not on every rerun."""
    if "price" not in C.DATA_XCHECK_FIELDS or not tickers:
        return {}
    provs = _live_price_providers()
    if len(provs) < 2:
        return {}
    primary = provs[0]
    validator = provs[1]
    # Validator-health gate: when the validator source is RED in api_health
    # (rate-limited / hard-erroring — e.g. Yahoo 401 Invalid Crumb or 429 from a
    # datacenter IP), its returned prices can't be trusted, so any "disagreement"
    # is the validator's own degradation, not a real integrity fault. Surfacing a
    # red "sources disagree" banner then is a false alarm. Skip the cross-check
    # this cycle; it auto-resumes when the validator recovers (api_health clears).
    if _is_red(validator.name):
        return {}
    try:
        prim = primary.live_prices(list(tickers))
    except Exception:
        prim = {}
    if not prim:
        return {}
    try:
        val = validator.live_prices(list(tickers))
    except Exception:
        return {}

    out: dict[str, dict] = {}
    for t, pr in prim.items():
        # Skip tickers the primary itself gap-filled from the validator source —
        # comparing a source against itself proves nothing.
        if pr.get("source") == validator.name:
            continue
        vr = val.get(t)
        if not vr:
            continue
        res = _compare(pr.get("price"), pr.get("prev_close"),
                       vr.get("price"), vr.get("prev_close"))
        if res is None:
            continue
        res["primary_source"] = primary.name
        res["validator"] = validator.name
        out[t] = res
    return out


def _is_red(source: str) -> bool:
    """True when an api_health source is at its 'red' level (rate-limited /
    erroring hard). Single predicate so the validator-health gate and the UI
    'paused' note share one threshold definition (api_health.get_health)."""
    return _ah.get_health(source).get("level") == "red"


def live_price_validator_degraded() -> str | None:
    """Name of the live-price cross-check validator if it is currently RED in
    api_health, else None. The held-position cross-check (crosscheck_batch) skips
    when this is set — a 'disagreement' against a degraded validator is a false
    alarm, not an integrity fault. Lets the UI show a transparent 'cross-check
    paused' note instead of either a red banner or silent nothing."""
    if "price" not in C.DATA_XCHECK_FIELDS:
        return None
    provs = _live_price_providers()
    if len(provs) < 2:
        return None
    validator = provs[1]
    return validator.name if _is_red(validator.name) else None
