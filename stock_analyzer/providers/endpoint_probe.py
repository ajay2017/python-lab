"""Read-only provider endpoint probe (pure; no Streamlit).

Finds out which Finnhub / FMP endpoints the configured keys can actually use.
Each probe makes exactly ONE GET with the shared short timeout and NO retries.

It deliberately bypasses `api_health` (and the providers' `_get_json`, including
FMP_SKIP_ENDPOINTS) so the Data Health counters stay a record of the app's own
traffic, and so the endpoints the app has switched off can still be tested.
The one honesty hook: `on_fmp_call` is invoked once per FMP request actually
sent, so the caller can keep the daily FMP quota counter truthful.

`run_probe` returns rows {provider, endpoint, role, http_status, outcome, detail}.
Detail strings are redacted and never contain a key.
"""

import json
import re
from datetime import date, timedelta

import requests

from stock_analyzer import constants as C
from stock_analyzer import market_time
from stock_analyzer.providers import finnhub_provider as _fh
from stock_analyzer.providers import fmp_provider as _fmp
from stock_analyzer.providers._util import _TIMEOUT, _redact_url

OUT_WORKS = "works"
OUT_NO_DATA = "works, no data for symbol"
OUT_NOT_ON_PLAN = "not on plan"
OUT_AUTH = "auth error"
OUT_RATE = "rate limited"
OUT_NON_JSON = "non-JSON reply"
OUT_ERROR = "error"
OUT_NO_KEY = "no key"
OUT_SKIPPED = "skipped"

ROLE_IN_USE = "in use"          # the app calls this endpoint today
ROLE_CANDIDATE = "candidate"    # being evaluated as a replacement

_PLAN_WORDS = ("access", "plan", "premium", "subscription")
_FMP_PLAN_WORDS = ("plan", "subscription", "premium", "upgrade", "legacy")


_SYMBOL_RE = re.compile(r"[A-Z0-9.\-]{1,10}")


def normalize_symbol(raw) -> str | None:
    """Pure: strip + uppercase a typed ticker; 1-10 chars of A-Z, 0-9, '.', '-'
    (BRK-B, BRK.B). Anything else (empty, inner spaces, '$AAPL', too long) -> None."""
    if not isinstance(raw, str):
        return None
    s = raw.strip().upper()
    return s if _SYMBOL_RE.fullmatch(s) else None


def _scrub(text, key: str | None, n: int = 120) -> str:
    """Collapse whitespace, redact URL keys and any literal key, THEN trim (the
    trim must come last or a key straddling the cut would leak a prefix)."""
    s = _redact_url(" ".join(str(text).split()))
    if key:
        s = s.replace(key, "***")
    return s[:n]


def classify_response(provider: str, endpoint: str, status: int, body: str,
                      key: str | None = None) -> tuple[str, str]:
    """Pure: (outcome, detail) for one HTTP reply."""
    body = body or ""
    low = body.lower()
    # FMP's daily-limit text ("Limit Reach . Please upgrade your plan") also has
    # plan words, so it's checked first -- but only on an error reply or inside
    # an error payload below, never across a working 2xx body (a headline saying
    # "debt limit reached" must not read as rate-limited).
    if status == 429 or (not (200 <= status < 300) and "limit reach" in low):
        return OUT_RATE, _scrub(body, key, 60)
    if status == 402:
        return OUT_NOT_ON_PLAN, _scrub(body or "HTTP 402", key)
    if status in (401, 403):
        if any(w in low for w in _PLAN_WORDS):
            return OUT_NOT_ON_PLAN, _scrub(body, key)
        return OUT_AUTH, _scrub(body or f"HTTP {status}", key)
    if not (200 <= status < 300):
        return OUT_ERROR, _scrub(f"HTTP {status} {body}", key)
    if not body.strip():
        return OUT_NON_JSON, "empty body"
    try:
        data = json.loads(body)
    except ValueError:
        return OUT_NON_JSON, _scrub(body, key, 60)
    if isinstance(data, dict):
        msg = data.get("Error Message") or data.get("error")
        if msg and "limit reach" in str(msg).lower():
            return OUT_RATE, _scrub(msg, key)
        if msg and any(w in str(msg).lower() for w in _FMP_PLAN_WORDS + _PLAN_WORDS):
            return OUT_NOT_ON_PLAN, _scrub(msg, key)
        if msg:
            return OUT_ERROR, _scrub(msg, key)
    if isinstance(data, list):
        return (OUT_WORKS, f"{len(data)} items") if data else (
            OUT_NO_DATA, "empty list")
    if isinstance(data, dict):
        if not data:
            return OUT_NO_DATA, "empty object"
        if provider == "finnhub" and endpoint == "quote" and not data.get("c"):
            return OUT_NO_DATA, "price 0 (unknown symbol)"
        return OUT_WORKS, f"{len(data)} fields"
    return OUT_WORKS, _scrub(data, key, 60)


def _finnhub_probes(sym: str, today) -> list[tuple[str, dict, str]]:
    d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    return [
        ("quote", {"symbol": sym}, ROLE_IN_USE),
        ("stock/news-sentiment", {"symbol": sym}, ROLE_IN_USE),
        ("stock/earnings", {"symbol": sym, "limit": 1}, ROLE_IN_USE),
        ("company-news", {"symbol": sym, "from": d(-7), "to": d(0)}, ROLE_CANDIDATE),
        ("stock/metric", {"symbol": sym, "metric": "all"}, ROLE_CANDIDATE),
        ("stock/recommendation", {"symbol": sym}, ROLE_CANDIDATE),
        ("calendar/earnings", {"from": d(0), "to": d(14)}, ROLE_CANDIDATE),
    ]


def _fmp_probes(sym: str, today) -> list[tuple[str, dict, str]]:
    d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    return [(ep, params, ROLE_IN_USE) for ep, params in [
        ("quote", {"symbol": sym}),
        ("profile", {"symbol": sym}),
        ("historical-price-eod/full", {"symbol": sym, "from": d(-10), "to": d(0)}),
        ("ratios-ttm", {"symbol": sym}),
        ("key-metrics-ttm", {"symbol": sym}),
        ("financial-growth", {"symbol": sym, "limit": 1}),
        ("price-target-consensus", {"symbol": sym}),
        # same window the app sends (fmp_provider news path)
        ("news/stock", {"symbols": sym, "limit": 3,
                        "from": d(-C.NEWS_MAX_AGE_DAYS), "to": d(0)}),
        ("earnings", {"symbol": sym, "limit": 4}),
        ("grades-consensus", {"symbol": sym}),
        ("earnings-calendar", {"from": d(0), "to": d(14)}),
    ]]


def probe_counts() -> dict:
    """Requests one run sends per provider, so the UI never hardcodes them."""
    _d = date(2000, 1, 1)
    return {"finnhub": len(_finnhub_probes("X", _d)), "fmp": len(_fmp_probes("X", _d))}


def fmp_probe_allowed(used_today, n_requests: int) -> bool:
    """Pure: may a run of n FMP requests go ahead under the daily soft cap?
    None (count unknown) is allowed, matching the orchestrator's pause rule."""
    return used_today is None or used_today + n_requests <= C.FMP_DAILY_SOFT_CAP


def _one(provider, base, endpoint, params, key_param, key, get, on_call,
         role="") -> dict:
    row = {"provider": provider, "endpoint": endpoint, "role": role,
           "http_status": None, "outcome": OUT_ERROR, "detail": ""}
    p = dict(params)
    p[key_param] = key
    try:
        if on_call is not None:
            on_call()
        resp = get(f"{base}/{endpoint}", params=p, timeout=_TIMEOUT)
        row["http_status"] = int(resp.status_code)
        row["outcome"], row["detail"] = classify_response(
            provider, endpoint, row["http_status"], resp.text, key)
    except Exception as exc:
        row["outcome"] = OUT_ERROR
        row["detail"] = _scrub(f"{type(exc).__name__}: {exc}", key)
    return row


def run_probe(keys: dict, symbol: str = "AAPL", today=None, get=None,
              on_fmp_call=None, fmp_used_today=None) -> list[dict]:
    """One read-only request per endpoint. `keys` = {"finnhub": ..., "fmp": ...}
    (missing/empty -> that provider's rows say "no key", no request). `today`
    defaults to market_time.today_et(); `get` defaults to requests.get.
    `fmp_used_today` = today's FMP call count (None = unknown); if the run would
    pass the FMP daily soft cap every FMP row is "skipped" and nothing is sent."""
    today = today or market_time.today_et()
    get = get or requests.get
    rows: list[dict] = []
    for provider, base, key_param, probes, on_call in (
        ("finnhub", _fh._BASE, "token", _finnhub_probes(symbol, today), None),
        ("fmp", _fmp._BASE, "apikey", _fmp_probes(symbol, today), on_fmp_call),
    ):
        key = (keys or {}).get(provider)
        skip_cap = (provider == "fmp" and
                    not fmp_probe_allowed(fmp_used_today, len(probes)))
        for endpoint, params, role in probes:
            if not key:
                rows.append({"provider": provider, "endpoint": endpoint,
                             "role": role, "http_status": None,
                             "outcome": OUT_NO_KEY, "detail": "key not set"})
            elif skip_cap:
                rows.append({"provider": provider, "endpoint": endpoint,
                             "role": role, "http_status": None,
                             "outcome": OUT_SKIPPED,
                             "detail": "would pass FMP's daily soft cap "
                                       f"({fmp_used_today}/{C.FMP_DAILY_SOFT_CAP} used)"})
            else:
                rows.append(_one(provider, base, endpoint, params, key_param,
                                 key, get, on_call, role))
    return rows
