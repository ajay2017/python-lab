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
    return [p for p in chain() if p.supports(capability)]


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


def get_bundle(ticker: str, period: str = "6mo") -> dict:
    return _failover_single(CAP_BUNDLE, "bundle", ticker, period)


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
