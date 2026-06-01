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
  • CROSS-CHECK — `crosscheck_price()` compares a primary price against the next
    price-capable provider and reports the % gap vs `DATA_XCHECK_TOLERANCE_PCT`.
    It is invoked deliberately by callers (not auto-run on the 60s strip), so it
    doesn't burn the keyed free-tier quota or add latency to every refresh.

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

_chain_cache: list | None = None


def chain() -> list:
    """Configured providers in DATA_PROVIDER_ORDER (key present), cached for the
    process. Construction reads keys from secrets/env, so the cache is rebuilt
    only on an explicit reset() (or a process restart, e.g. a Streamlit reboot
    after a secrets change)."""
    global _chain_cache
    if _chain_cache is None:
        out = []
        for name in C.DATA_PROVIDER_ORDER:
            cls = PROVIDER_REGISTRY.get(name)
            if cls is None:
                continue
            try:
                prov = cls()
            except Exception:
                continue
            if prov.is_configured():
                out.append(prov)
        _chain_cache = out
    return _chain_cache


def reset() -> None:
    """Drop the cached chain (call after a secrets/config change)."""
    global _chain_cache
    _chain_cache = None


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


# ── Gap-fill failover for live prices ────────────────────────────────────────
def get_live_prices(tickers: list[str]) -> dict[str, dict]:
    """Primary fills as many tickers as it can; subsequent providers fill only
    the ones still missing. A provider failing is skipped, not fatal."""
    if not tickers:
        return {}
    results: dict[str, dict] = {}
    remaining = list(tickers)
    for prov in _providers_for(CAP_LIVE_PRICE):
        if not remaining:
            break
        try:
            got = prov.live_prices(remaining)
        except Exception:
            continue
        for t, v in (got or {}).items():
            results.setdefault(t, v)
        remaining = [t for t in tickers if t not in results]
    return results


# ── Price cross-check (deliberate, not auto-run) ─────────────────────────────
def crosscheck_price(ticker: str, primary_price: float) -> dict | None:
    """Compare `primary_price` against the next price-capable provider after the
    primary. Returns {ok, source, other_price, gap_pct, tolerance_pct} or None
    when cross-check is disabled / no secondary price source / no comparable
    price. `ok` is False when the gap exceeds DATA_XCHECK_TOLERANCE_PCT — the
    caller surfaces that loudly ('price unverified')."""
    if "price" not in C.DATA_XCHECK_FIELDS or not primary_price:
        return None
    primary_name = C.DATA_PROVIDER_ORDER[0] if C.DATA_PROVIDER_ORDER else None
    tol = float(C.DATA_XCHECK_TOLERANCE_PCT)
    for prov in _providers_for(CAP_LIVE_PRICE):
        if prov.name == primary_name:
            continue  # need an INDEPENDENT source to validate
        try:
            got = prov.live_prices([ticker])
        except Exception:
            continue
        other = (got or {}).get(ticker, {}).get("price")
        if not other:
            continue
        gap = abs(float(primary_price) - float(other)) / float(primary_price) * 100
        return {
            "ok":            gap <= tol,
            "source":        prov.name,
            "other_price":   round(float(other), 2),
            "gap_pct":       round(gap, 3),
            "tolerance_pct": tol,
        }
    return None
