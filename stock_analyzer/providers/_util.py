"""
Shared helpers for the keyed REST providers (Finnhub, FMP).

Key-reading is deliberately dual-source: Streamlit secrets when deployed, and
an environment variable fallback so the adapters can be smoke-tested offline
(see providers/selftest.py) without the full app or putting keys in code.
"""

import os
import requests

_TIMEOUT = 10  # seconds — REST calls must not hang the page


def get_secret(name: str) -> str | None:
    """Return a secret by name from Streamlit secrets (deployed) or an env var
    (offline test), stripped of whitespace. None when absent in both.

    Tolerates a common TOML mistake: a flat key placed AFTER a [section] header
    is parsed by TOML as nested INSIDE that section (e.g. FINNHUB_API_KEY written
    below [fred] becomes fred.FINNHUB_API_KEY). So if the top-level lookup misses,
    we also scan one level of sections — a misplaced key silently disabling a
    data source is exactly the silent degradation this app refuses to allow."""
    # 1) Streamlit secrets — available when running inside the app.
    try:
        import streamlit as st
        # 1a) top-level (the correct placement)
        val = st.secrets.get(name)
        if val:
            return str(val).strip()
        # 1b) fallback: scan one level of sections for a mis-nested key
        try:
            for _k in st.secrets.keys():
                _sec = st.secrets[_k]
                if hasattr(_sec, "get"):           # a [section] table, not a scalar
                    _v = _sec.get(name)
                    if _v:
                        return str(_v).strip()
        except Exception:
            pass
    except Exception:
        # No secrets file / not in a Streamlit context — fall through to env.
        pass
    # 2) Environment variable — for offline selftest / CI.
    val = os.environ.get(name)
    return val.strip() if val else None


def http_get_json(url: str, params: dict | None = None, timeout: int = _TIMEOUT):
    """GET `url` and return parsed JSON. Raises on HTTP error (incl. 429) so the
    caller can classify rate-limit vs other errors and record api_health.
    No per-call retry by design — the orchestrator fails over to the next provider."""
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def is_rate_limit(exc: Exception) -> bool:
    """True when an exception looks like an HTTP 429 / quota error."""
    msg = str(exc).lower()
    return "429" in msg or "too many" in msg or "rate limit" in msg or "limit reach" in msg
