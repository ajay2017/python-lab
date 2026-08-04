"""
Shared helpers for the keyed REST providers (Finnhub, FMP).

Key-reading is deliberately dual-source: Streamlit secrets when deployed, and
an environment variable fallback so the adapters can be smoke-tested offline
(see providers/selftest.py) without the full app or putting keys in code.
"""

import os
import re
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


def _redact_url(url: str) -> str:
    """Strip apikey/token query-param values from a URL before logging."""
    return re.sub(r'(?i)([?&](apikey|api_key|token|key)=)[^&]+', r'\1***', url)


def http_get_json(url: str, params: dict | None = None, timeout: int = _TIMEOUT):
    """GET `url` and return parsed JSON. Raises on HTTP error (incl. 429) so the
    caller can classify rate-limit vs other errors and record api_health.
    No per-call retry by design — the orchestrator fails over to the next provider.
    API key query params are redacted from any raised exception message —
    including a bare requests.get() failure (Timeout/ConnectionError/etc.),
    not just the raise_for_status() branch. requests embeds the full request
    URL in those exceptions' own message, so a redaction that only wrapped
    raise_for_status() (as this used to) let a plaintext API key leak into
    api_health on a routine timeout/DNS hiccup (2026-08-04 audit finding —
    Finnhub had this gap; FMP's provider-level _safe() masked it there)."""
    try:
        resp = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise type(exc)(_redact_url(str(exc))) from None
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        safe_msg = _redact_url(str(exc))
        raise requests.HTTPError(safe_msg, response=resp) from None
    if not resp.text.strip():
        # Surface the HTTP status when the body is empty so callers can distinguish
        # a silent rate-limit 200+empty from a genuine parse error.
        raise ValueError(f"HTTP {resp.status_code} — empty response body")
    return resp.json()


def is_rate_limit(exc: Exception) -> bool:
    """True when an exception looks like an HTTP 429 / quota error."""
    msg = str(exc).lower()
    return "429" in msg or "too many" in msg or "rate limit" in msg or "limit reach" in msg


def classify_error(exc: Exception) -> str:
    """Classify an exception into a fine-grained api_health event type.
    Use instead of bare is_rate_limit() at provider call sites."""
    import json as _json
    # Parse check FIRST — JSONDecodeError messages often contain numeric column
    # offsets ("line 1 column 403 (char 402)") that would otherwise match the
    # auth/quota bare-string checks below and trigger the circuit-breaker.
    if isinstance(exc, (ValueError, _json.JSONDecodeError)):
        return "parse"
    msg = str(exc).lower()
    if "401" in msg or "403" in msg or "unauthorized" in msg or "forbidden" in msg:
        return "auth"
    if "402" in msg or "payment required" in msg or "plan limit" in msg or "upgrade your" in msg:
        return "quota"
    if "429" in msg or "too many" in msg or "rate limit" in msg or "limit reach" in msg:
        return "rate_limit"
    return "error"
