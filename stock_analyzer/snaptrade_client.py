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
  SNAPTRADE_CLIENT_ID, SNAPTRADE_CONSUMER_KEY   — app-level (Railway secret)
  SNAPTRADE_USER_ID, SNAPTRADE_USER_SECRET      — user-level (Railway secret;
      USER_SECRET is issued once at registration and stored by the user, not
      auto-persisted by the app — see the plan doc's "Credential storage"
      section for why).
"""

import os
from datetime import timedelta

from stock_analyzer import api_health
from stock_analyzer.constants import SNAPTRADE_REQUEST_TIMEOUT_SEC
from stock_analyzer.market_time import today_et

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


def _snaptrade_user_creds() -> tuple[str, str]:
    """(user_id, user_secret) — env first, then st.secrets."""
    user_id = os.environ.get("SNAPTRADE_USER_ID", "")
    user_secret = os.environ.get("SNAPTRADE_USER_SECRET", "")
    if user_id and user_secret:
        return user_id, user_secret
    if st is not None:
        try:
            sec = st.secrets.get("snaptrade", {})
            return sec.get("user_id", "") or "", sec.get("user_secret", "") or ""
        except Exception:
            pass
    return "", ""


def has_snaptrade() -> bool:
    """True when both app-level and user-level SnapTrade credentials are present."""
    client_id, consumer_key = _snaptrade_app_creds()
    user_id, user_secret = _snaptrade_user_creds()
    return bool(client_id) and bool(consumer_key) and bool(user_id) and bool(user_secret)


# Process-level singleton — same rationale as db._client(): a plain module
# global caches the SDK client across Streamlit reruns (same process) and
# works headless in the cron, where st.cache_resource has no runtime.
_client_singleton = None
_client_creds_used: tuple[str, str] | None = None


def _client():
    """Lazily construct (and cache) the SnapTrade SDK client. Raises if
    app-level credentials are missing — callers must check `has_snaptrade()`
    first, or catch the exception (every public function below does)."""
    global _client_singleton, _client_creds_used
    client_id, consumer_key = _snaptrade_app_creds()
    if not client_id or not consumer_key:
        raise RuntimeError("SnapTrade app credentials not configured")
    if _client_singleton is not None and _client_creds_used == (client_id, consumer_key):
        return _client_singleton
    from snaptrade_client import SnapTrade, SnapTradeAuth
    _client_singleton = SnapTrade(
        auth=SnapTradeAuth.commercial_api_key(
            consumer_key=consumer_key,
            client_id=client_id,
        )
    )
    _client_creds_used = (client_id, consumer_key)
    return _client_singleton


def _record_success() -> None:
    api_health.record("snaptrade", "success")


def _record_error(msg: object) -> None:
    api_health.record("snaptrade", "error", msg=str(msg))


def register_user(user_id: str):
    """Register a new SnapTrade user. Returns the `user_secret` string (issued
    ONCE — the caller must display/persist it immediately) or None on failure.
    One-time setup call — not used in normal sync operation."""
    try:
        resp = _client().authentication.register_snap_trade_user(
            user_id=user_id, timeout=SNAPTRADE_REQUEST_TIMEOUT_SEC,
        )
        _record_success()
        return resp.body["userSecret"]
    except Exception as e:
        _record_error(e)
        return None


def get_connection_portal_url(user_id: str, user_secret: str):
    """Return the SnapTrade connection-portal redirect URL for the given user,
    or None on failure. One-time setup call."""
    try:
        resp = _client().authentication.login_snap_trade_user(
            user_id=user_id, user_secret=user_secret,
            timeout=SNAPTRADE_REQUEST_TIMEOUT_SEC,
        )
        _record_success()
        return resp.body.get("redirectURI")
    except Exception as e:
        _record_error(e)
        return None


def list_accounts():
    """Return the list of connected brokerage accounts (raw SDK dicts) for the
    configured user, or None on failure/missing credentials. Each element
    carries at least `id` (SnapTrade account UUID) and `institution_name`."""
    user_id, user_secret = _snaptrade_user_creds()
    if not user_id or not user_secret:
        return None
    try:
        resp = _client().account_information.list_user_accounts(
            user_id=user_id, user_secret=user_secret,
            timeout=SNAPTRADE_REQUEST_TIMEOUT_SEC,
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
    user_id, user_secret = _snaptrade_user_creds()
    if not user_id or not user_secret:
        return None
    try:
        resp = _client().account_information.get_user_account_balance(
            account_id=account_id, user_id=user_id, user_secret=user_secret,
            timeout=SNAPTRADE_REQUEST_TIMEOUT_SEC,
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
    user_id, user_secret = _snaptrade_user_creds()
    if not user_id or not user_secret:
        return None
    try:
        resp = _client().account_information.get_all_account_positions(
            account_id=account_id, user_id=user_id, user_secret=user_secret,
            timeout=SNAPTRADE_REQUEST_TIMEOUT_SEC,
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
    user_id, user_secret = _snaptrade_user_creds()
    if not user_id or not user_secret:
        return None
    end = today_et()
    start = end - timedelta(days=lookback_days)
    try:
        resp = _client().account_information.get_account_activities(
            account_id=account_id,
            user_id=user_id,
            user_secret=user_secret,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            offset=0,
            limit=1000,
            timeout=SNAPTRADE_REQUEST_TIMEOUT_SEC,
        )
        _record_success()
        return resp.body.get("data")
    except Exception as e:
        _record_error(e)
        return None
