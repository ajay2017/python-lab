"""
Thin wrapper over the SnapTrade Python SDK (`snaptrade-python-sdk` on PyPI,
package `snaptrade_client`). See docs/plans/snaptrade-broker-integration.md
for the full design.

Every public function here returns `None` on any failure (missing
credentials, timeout, SDK/API error) — never raises into caller code. This
mirrors the multi-source provider convention elsewhere in the app (Finnhub/
yfinance/FMP failover): callers treat `None` as "unavailable this call" and
apply the offline-sentinel contract themselves (never render `None` as zero
or as "no data").

Credentials (env-first, then st.secrets — same dual-source pattern as
`db._supabase_creds`, so this works both in the headless Railway cron and
in the Streamlit app):
  SNAPTRADE_CLIENT_ID, SNAPTRADE_CONSUMER_KEY   — the only two credentials.

**Personal API key, not Commercial** (confirmed against the actual SnapTrade
Dashboard, 2026-08-18 — the original build wrongly assumed the Commercial
multi-tenant model). SnapTrade's own Personal-key page states: "Personal
accounts do not register a SnapTrade user — skip registerUser, and do not
send userId or userSecret." So unlike a Commercial integration (which
registers a distinct `userId`/`userSecret` per end-user), every call here
uses ONLY the Client ID + Consumer Key — there is no per-user registration
step and no second credential pair. Verified directly against the SDK: none
of `login_snap_trade_user`/`list_user_accounts`/`get_user_account_balance`/
`get_all_account_positions`/`get_account_activities` require `user_id`/
`user_secret` (omitting them produces the same clientId-level auth error as
supplying them, not a "missing required field" error).
"""

import os
from datetime import timedelta

from stock_analyzer import api_health
from stock_analyzer.constants import SNAPTRADE_REQUEST_TIMEOUT_SEC
from stock_analyzer.market_time import today_et
from stock_analyzer.providers.yfinance_provider import _call_with_timeout

try:
    import streamlit as st
except Exception:
    st = None


def _snaptrade_app_creds() -> tuple[str, str]:
    """(client_id, consumer_key) — env first, then st.secrets."""
    client_id = os.environ.get("SNAPTRADE_CLIENT_ID", "")
    consumer_key = os.environ.get("SNAPTRADE_CONSUMER_KEY", "")
    if client_id and consumer_key:
        return client_id, consumer_key
    if st is not None:
        try:
            sec = st.secrets.get("snaptrade", {})
            return sec.get("client_id", "") or "", sec.get("consumer_key", "") or ""
        except Exception:
            pass
    return "", ""


def has_snaptrade() -> bool:
    """True when the Personal SnapTrade API key (Client ID + Consumer Key)
    is present. No separate user-level credential exists for a Personal key."""
    client_id, consumer_key = _snaptrade_app_creds()
    return bool(client_id) and bool(consumer_key)


# Process-level singleton — same rationale as db._client(): a plain module
# global caches the SDK client across Streamlit reruns (same process) and
# works headless in the cron, where st.cache_resource has no runtime.
_client_singleton = None
_client_creds_used: tuple[str, str] | None = None


def _client():
    """Lazily construct (and cache) the SnapTrade SDK client. Raises if
    credentials are missing — callers must check `has_snaptrade()` first,
    or catch the exception (every public function below does)."""
    global _client_singleton, _client_creds_used
    client_id, consumer_key = _snaptrade_app_creds()
    if not client_id or not consumer_key:
        raise RuntimeError("SnapTrade credentials not configured")
    if _client_singleton is not None and _client_creds_used == (client_id, consumer_key):
        return _client_singleton
    from snaptrade_client import SnapTrade, SnapTradeAuth
    _client_singleton = SnapTrade(
        auth=SnapTradeAuth.personal_api_key(
            consumer_key=consumer_key,
            client_id=client_id,
        )
    )
    _client_creds_used = (client_id, consumer_key)
    return _client_singleton


def _record_success() -> None:
    api_health.record("snaptrade", "success")


def _record_error(exc: object) -> None:
    """Record a failure for api_health's 120-char-truncated last_error_msg.

    SnapTrade's ApiException.__str__ leads with the raw HTTP response
    (status line + full header dict) before ever reaching the actual JSON
    error body, so the generic str(e) representation gets cut off by the
    120-char truncation exactly before the useful part — confirmed live
    2026-08-17 (`(400) Reason: Bad Request HTTP response headers:
    HTTPHeaderDict({'Date': ...` and nothing past it). `.body` is the
    parsed JSON SnapTrade actually sent (e.g. `{'detail': 'Invalid
    clientId provided - fake', ...}`) — lead with that instead."""
    try:
        from snaptrade_client import ApiException
        if isinstance(exc, ApiException) and exc.body:
            api_health.record("snaptrade", "error", msg=f"{exc.status} {exc.body}")
            return
    except Exception:
        pass
    api_health.record("snaptrade", "error", msg=str(exc))


def get_connection_portal_url():
    """Return the SnapTrade connection-portal redirect URL, or None on
    failure. One-time setup call — no user_id/user_secret for a Personal key."""
    try:
        resp = _call_with_timeout(
            _client().authentication.login_snap_trade_user,
            (), {},
            SNAPTRADE_REQUEST_TIMEOUT_SEC,
        )
        _record_success()
        return resp.body.get("redirectURI")
    except Exception as e:
        _record_error(e)
        return None


def list_accounts():
    """Return the list of connected brokerage accounts (raw SDK dicts), or
    None on failure/missing credentials. Each element carries at least `id`
    (SnapTrade account UUID) and `institution_name`."""
    if not has_snaptrade():
        return None
    try:
        resp = _call_with_timeout(
            _client().account_information.list_user_accounts,
            (), {},
            SNAPTRADE_REQUEST_TIMEOUT_SEC,
        )
        _record_success()
        return resp.body
    except Exception as e:
        _record_error(e)
        return None


def get_account_balance(account_id: str):
    """Return the raw balance entry (dict-like, at least `cash`/`buying_power`/
    `currency`) for one SnapTrade account, or None on failure. Multi-currency
    accounts return multiple entries; single-currency (e.g. a US Robinhood
    account) returns one — this function returns the FULL response body
    unfiltered and leaves currency selection to the caller (broker_sync.py),
    since collapsing to one number is a business decision, not plumbing."""
    if not has_snaptrade():
        return None
    try:
        resp = _call_with_timeout(
            _client().account_information.get_user_account_balance,
            (), {"account_id": account_id},
            SNAPTRADE_REQUEST_TIMEOUT_SEC,
        )
        _record_success()
        return resp.body
    except Exception as e:
        _record_error(e)
        return None


def get_account_positions(account_id: str):
    """Return the raw list of position entries for one SnapTrade account
    (each with an `instrument` discriminated by `instrument.kind`, plus
    `units`/`price`/`cost_basis`), or None on failure."""
    if not has_snaptrade():
        return None
    try:
        resp = _call_with_timeout(
            _client().account_information.get_all_account_positions,
            (), {"account_id": account_id},
            SNAPTRADE_REQUEST_TIMEOUT_SEC,
        )
        _record_success()
        return resp.body.get("results")
    except Exception as e:
        _record_error(e)
        return None


def get_account_activities(account_id: str, lookback_days: int):
    """Return the raw list of activity/transaction entries for one SnapTrade
    account over the last `lookback_days` days, or None on failure. Each
    entry carries `id` (SnapTrade transaction id — the Tier-1 dedup key),
    `type` (BUY/SELL/DIVIDEND/INTEREST/FEE/CONTRIBUTION/WITHDRAWAL/...),
    `symbol.symbol` (ticker, when applicable), `units`, `price`, `amount`,
    `trade_date`. Paginated server-side (max 1000/page) — callers expecting
    more than that in one lookback window would need to page via `offset`;
    not built here since SNAPTRADE_SYNC_MAX_TXN_LOOKBACK_DAYS keeps the
    window small enough that a single retail account won't hit it."""
    if not has_snaptrade():
        return None
    end = today_et()
    start = end - timedelta(days=lookback_days)
    try:
        resp = _call_with_timeout(
            _client().account_information.get_account_activities,
            (),
            {
                "account_id": account_id,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "offset": 0,
                "limit": 1000,
            },
            SNAPTRADE_REQUEST_TIMEOUT_SEC,
        )
        _record_success()
        return resp.body.get("data")
    except Exception as e:
        _record_error(e)
        return None
