#!/usr/bin/env python3
"""Read-only probe of SnapTrade's own connection / sync freshness fields.

SnapTrade documents that a disabled connection "can no longer access the latest
data from the brokerage, but will continue to return the last cached state".
The app never reads these fields, so a dropped brokerage link could look like
"no mismatch". This prints the real values so an alarm can be designed on them.

Read-only: no database, no writes, no orders. It makes two SnapTrade GET calls
(list brokerage authorizations, list user accounts). No Supabase needed.
Ids and account numbers are masked to their last 4 characters; credentials are
never printed.

Run (bash):
    SNAPTRADE_CLIENT_ID=... SNAPTRADE_CONSUMER_KEY=... python scripts/snaptrade_status_probe.py
PowerShell: set $env:SNAPTRADE_CLIENT_ID and $env:SNAPTRADE_CONSUMER_KEY first.

Exit code: 0 success, 1 both calls failed, 2 credentials missing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, date, timezone  # noqa: E402

from stock_analyzer import snaptrade_client  # noqa: E402
from stock_analyzer.constants import SNAPTRADE_REQUEST_TIMEOUT_SEC  # noqa: E402
from stock_analyzer.market_time import now_et  # noqa: E402


def _g(obj, key, default=None):
    try:
        return obj.get(key, default)
    except Exception:
        return default


def _mask(value) -> str:
    s = "" if value is None else str(value)
    if not s:
        return "(none)"
    return "****" + s[-4:]


def _parse_ts(value):
    """Best-effort tz-aware datetime from datetime / date / ISO string, else None."""
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, date):
            dt = datetime(value.year, value.month, value.day)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _age_text(value) -> str:
    """'<raw> | age N.N h | ET <time>' (raw value printed as given)."""
    if value is None:
        return "None"
    dt = _parse_ts(value)
    if dt is None:
        return f"{value} (unparseable)"
    now = now_et()
    hours = (now - dt).total_seconds() / 3600.0
    return f"{value} | age {hours:.1f} h | ET {dt.astimezone(now.tzinfo):%Y-%m-%d %H:%M}"


def _age_hours(value):
    dt = _parse_ts(value)
    if dt is None:
        return None
    return (now_et() - dt).total_seconds() / 3600.0


def _scrub(text: str) -> str:
    """Remove the credentials from text: SnapTrade's error bodies can echo the
    client id (e.g. "Invalid clientId provided - <id>")."""
    for secret in snaptrade_client._snaptrade_app_creds():
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _fmt_exc(e) -> str:
    out = type(e).__name__
    status = getattr(e, "status", None)
    body = getattr(e, "body", None)
    if status is not None:
        out += f" status={status}"
    if body:
        out += f" body={body}"
    return _scrub(out)


def _call(fn):
    resp = snaptrade_client._call_with_timeout(fn, (), {}, SNAPTRADE_REQUEST_TIMEOUT_SEC)
    return resp.body


def main() -> int:
    if not snaptrade_client.has_snaptrade():
        print("SnapTrade credentials missing: set SNAPTRADE_CLIENT_ID and "
              "SNAPTRADE_CONSUMER_KEY in the environment.")
        return 2

    failures = 0
    auth_by_id: dict[str, object] = {}

    print("=== 1. Connections ===")
    try:
        client = snaptrade_client._client()
        auths = _call(client.connections.list_brokerage_authorizations)
        if not auths:
            print("(no connections returned)")
        for a in auths or []:
            brokerage = _g(a, "brokerage", {}) or {}
            bname = _g(brokerage, "name") or _g(a, "name")
            aid = _g(a, "id")
            print(f"- {bname} | id {_mask(aid)} | type={_g(a, 'type')} | "
                  f"disabled={_g(a, 'disabled')} | disabled_date={_g(a, 'disabled_date')} | "
                  f"data_freshness_mode={_g(a, 'data_freshness_mode')} | "
                  f"created_date={_g(a, 'created_date')}")
            if aid is not None:
                auth_by_id[str(aid)] = a
    except Exception as e:
        failures += 1
        print(f"connections call failed: {_fmt_exc(e)}")

    print("\n=== 2. Accounts ===")
    accounts = None
    try:
        client = snaptrade_client._client()
        accounts = _call(client.account_information.list_user_accounts)
        if not accounts:
            print("(no accounts returned)")
        for acct in accounts or []:
            ss = _g(acct, "sync_status", {}) or {}
            h = _g(ss, "holdings", {}) or {}
            t = _g(ss, "transactions", {}) or {}
            print(f"- {_g(acct, 'institution_name')} | {_g(acct, 'name')} | "
                  f"number {_mask(_g(acct, 'number'))} | "
                  f"authorization {_mask(_g(acct, 'brokerage_authorization'))}")
            print(f"    holdings: initial_sync_completed={_g(h, 'initial_sync_completed')} "
                  f"holdings_unavailable={_g(h, 'holdings_unavailable')}")
            print(f"    holdings last_successful_sync: {_age_text(_g(h, 'last_successful_sync'))}")
            print(f"    transactions: initial_sync_completed={_g(t, 'initial_sync_completed')}")
            print(f"    transactions last_successful_sync: {_age_text(_g(t, 'last_successful_sync'))}")
    except Exception as e:
        failures += 1
        print(f"accounts call failed: {_fmt_exc(e)}")

    print("\n=== 3. Verdict ===")
    for acct in accounts or []:
        label = f"{_g(acct, 'institution_name')} {_mask(_g(acct, 'number'))}"
        auth = auth_by_id.get(str(_g(acct, "brokerage_authorization")))
        mode = _g(auth, "data_freshness_mode") if auth is not None else None
        if auth is not None and _g(auth, "disabled"):
            core = f"DISABLED since {_g(auth, 'disabled_date')}"
        else:
            hold = _g(_g(acct, "sync_status", {}) or {}, "holdings", {}) or {}
            hours = _age_hours(_g(hold, "last_successful_sync"))
            if hours is None:
                core = "holdings last synced unknown"
            else:
                core = f"holdings last synced {hours:.1f} h ago"
                if hours > 24:
                    core += " (>24h)"
        print(f"{label}: {core}; data_freshness_mode={mode}")

    return 1 if failures == 2 else 0


if __name__ == "__main__":
    sys.exit(main())
