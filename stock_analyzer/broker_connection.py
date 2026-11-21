"""Pure capture of SnapTrade's own connection / refresh health (#34c, chunk C1).

`docs/plans/broker-connection-alarm-34c.md`. SnapTrade documents that a disabled
connection "can no longer access the latest data from the brokerage, but will
continue to return the last cached state", so a dead Robinhood link can read as
"no mismatch" everywhere the app compares the journal with the broker. This
module only RECORDS what SnapTrade says, once per broker run, so the evidence
(and the alarm's thresholds) are measured before any decision is built on them.

C1 decides nothing: no constant, no flag, no email, no UI. Everything here is
parsing and summarising, with no Streamlit, no database and no network.

Rules (owner-accepted defaults U1-U10, probe P1 of 2026-11-21):
- `holdings_unavailable` counts ONLY when it is True / "true". Healthy accounts
  return None (P1), so None must read as available, never as "couldn't check".
- A holdings stamp is a tz-aware UTC instant. A naive value is read as UTC. A
  date-only value carries no time of day and parses to None (never an invented
  midnight).
- The transactions `last_successful_sync` is date-only: it is kept as the UTC
  date it was given, never shifted to ET midnight (the probe's bogus "39.1 h").
- `disabled` counts only when it is True / "true". `any_disabled` covers every
  authorization SnapTrade lists; `any_linked_disabled` counts only a disabled
  connection that one of our accounts is attached to (its `id` equals an
  account's `brokerage_authorization`), so an old disabled authorization left
  behind by a reconnect doesn't read as a dead link forever. Ids are compared
  in-process and never stored.
- None = "couldn't check" is preserved wherever a call failed. `accounts_ok` /
  `connections_ok` are derived here from whether each payload was a usable list.
- Reading `positions_read_ok` and `snapshot_saved` together: both None / `snapshot_saved`
  None = positions were never attempted (accounts call failed, or no accounts
  linked); `positions_read_ok=False` + `snapshot_saved=False` = a PARTIAL (or total)
  read, so no snapshot was attempted; `positions_read_ok=True` + `snapshot_saved=False`
  = every read worked but the snapshot WRITE failed. C2 must word those two
  differently.

Nothing here feeds `effective_capital`, `broker_frame`, `daily_budget` or `margin`.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import date, datetime, timezone

_UTC = timezone.utc

# A date with no time of day, e.g. "2026-11-19" or "20261119".
_DATE_ONLY = re.compile(r"^\d{4}-?\d{2}-?\d{2}$")
# Ids (UUIDs) and long digit runs (account numbers) never belong in a stored error.
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_LONG_DIGITS = re.compile(r"\b\d{6,}\b")

ERROR_MAX_CHARS = 160           # storage width of the `error` column (plan section 2)
POSITIONS_SIG_DECIMALS = 6      # quantity rounding so float noise can't change the signature
_JSON_DEPTH_LIMIT = 4


# ── defensive readers ────────────────────────────────────────────────────────
def _g(obj, key, default=None):
    """Field of a SDK object or dict, or `default`. Never raises."""
    if obj is None:
        return default
    try:
        return obj.get(key, default)
    except Exception:
        pass
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def _as_list(payload) -> list | None:
    """A payload as a list, or None when it is missing / not a sequence."""
    if payload is None:
        return None
    if isinstance(payload, (str, bytes, dict)):
        return None
    try:
        return list(payload)
    except Exception:
        return None


def _is_true(v) -> bool:
    """True only for the boolean True or the string "true" (any case)."""
    if v is True:
        return True
    return isinstance(v, str) and v.strip().lower() == "true"


def _is_nat_or_nan(v) -> bool:
    try:
        return v != v   # NaN and NaT are the only values unequal to themselves
    except Exception:
        return False


# ── parsing ──────────────────────────────────────────────────────────────────
def _to_utc(dt: datetime) -> datetime | None:
    if _is_nat_or_nan(dt):
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=_UTC)
    return dt.astimezone(_UTC)


def parse_holdings_stamp(v) -> datetime | None:
    """A holdings `last_successful_sync` as a tz-aware UTC datetime, else None.

    An offset (or "Z") is converted to UTC; a naive datetime / string is read as
    UTC; a date-only value ("2026-11-19" or a `date`) is None, because it has no
    time of day and a midnight would be invented; junk / NaN / None is None."""
    try:
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, datetime):
            return _to_utc(v)
        if isinstance(v, date):
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s or _DATE_ONLY.match(s):
                return None
            return _to_utc(datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00")))
        return None
    except Exception:
        return None


def parse_utc_date(v) -> date | None:
    """A `last_successful_sync` as the UTC calendar date it names, else None.

    "YYYY-MM-DD" is that date; a `date` is itself; a tz-aware datetime is
    converted to UTC and then truncated; a naive datetime is read as UTC. Never
    shifted to ET (the transactions field is date-only and lags 1-2 days)."""
    try:
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, datetime):
            dt = _to_utc(v)
            return None if dt is None else dt.date()
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            if _DATE_ONLY.match(s):
                d = s.replace("-", "")
                return date(int(d[:4]), int(d[4:6]), int(d[6:8]))
            dt = _to_utc(datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00")))
            return None if dt is None else dt.date()
        return None
    except Exception:
        return None


def positions_sig(positions_by_ticker) -> str | None:
    """A stable signature of the broker positions map the cron already builds.

    Sorted tickers, quantities rounded to `POSITIONS_SIG_DECIMALS` so float noise
    can't change it, sha256 hex (first 16 chars). None for a None input; an empty
    map has its own fixed value. Used to tell, across rows, whether SnapTrade's
    positions actually changed between two runs (does a call fetch live?)."""
    if positions_by_ticker is None:
        return None
    try:
        parts = []
        for tk, qty in dict(positions_by_ticker).items():
            try:
                q = float(qty)
            except Exception:
                q = float("nan")
            if math.isnan(q) or math.isinf(q):
                qs = str(q)
            else:
                qs = f"{round(q, POSITIONS_SIG_DECIMALS) + 0.0:.{POSITIONS_SIG_DECIMALS}f}"
            parts.append(f"{str(tk).strip().upper()}:{qs}")
        parts.sort()
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    except Exception:
        return None


# ── row building ─────────────────────────────────────────────────────────────
def _json_safe(v, depth: int = 0):
    """`v` reduced to JSON-serialisable types (dicts stay dicts). Never raises."""
    try:
        if v is None or isinstance(v, (bool, str)):
            return v
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return v if math.isfinite(v) else None
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        if depth >= _JSON_DEPTH_LIMIT:
            return str(v)
        if hasattr(v, "items"):
            return {str(k): _json_safe(x, depth + 1) for k, x in v.items()}
        if isinstance(v, (list, tuple, set)):
            return [_json_safe(x, depth + 1) for x in v]
        return str(v)
    except Exception:
        return None


def _iso(v) -> str | None:
    """A datetime as an ISO string WITH an offset (naive = UTC), else None."""
    try:
        if isinstance(v, datetime):
            dt = _to_utc(v) if (v.tzinfo is None or v.tzinfo.utcoffset(v) is None) else v
            return None if dt is None else dt.isoformat()
        if isinstance(v, str):
            dt = parse_holdings_stamp(v)
            return None if dt is None else dt.isoformat()
        return None
    except Exception:
        return None


def scrub_error(error) -> str | None:
    """`error` as short text with credentials, ids and account numbers removed."""
    if error is None:
        return None
    try:
        text = str(error)
        secrets = [os.environ.get("SNAPTRADE_CLIENT_ID", ""),
                   os.environ.get("SNAPTRADE_CONSUMER_KEY", "")]
        try:
            from stock_analyzer import snaptrade_client
            secrets.extend(snaptrade_client._snaptrade_app_creds())
        except Exception:
            pass
        for s in secrets:
            if s:
                text = text.replace(s, "[redacted]")
        text = _UUID.sub("[id]", text)
        text = _LONG_DIGITS.sub("[number]", text)
        text = " ".join(text.split())
        if not text:
            return None
        return text[:ERROR_MAX_CHARS]
    except Exception:
        return "error text unavailable"


def _auth_id(v) -> str | None:
    """A brokerage-authorization reference as a comparable string (a plain id, or an
    object / dict carrying `id`), else None. Compared in-process only."""
    if v is None:
        return None
    inner = _g(v, "id") if not isinstance(v, str) else None
    ref = v if inner is None else inner
    text = str(ref).strip()
    return text if text else None


def _holdings_of(account):
    return _g(_g(_g(account, "sync_status"), "holdings"), "last_successful_sync")


def _unavailable_of(account):
    return _g(_g(_g(account, "sync_status"), "holdings"), "holdings_unavailable")


def _txn_stamp_of(account):
    return _g(_g(_g(account, "sync_status"), "transactions"), "last_successful_sync")


def summarize_status(*, accounts, connections, run_started_at, checked_at,
                     positions_read_ok, snapshot_saved, positions_sig,
                     error) -> dict:
    """The `broker_connection_status` row for one broker run, from the raw
    SnapTrade payloads (SDK objects or dicts). Every value is JSON-serialisable;
    nothing in it is an id or an account number; never raises.

    `None` in a derived field means "couldn't check" (that call failed), except
    `holdings_stamp_missing` / `holdings_refreshed_*` / `txn_refreshed_utc_date`,
    where None means no usable stamp was given."""
    acct_list = _as_list(accounts)
    conn_list = _as_list(connections)

    # Our accounts' authorization ids, compared with the connections' ids below.
    linked_ids: set[str] | None = None
    if acct_list is not None:
        linked_ids = set()
        for a in acct_list:
            aid = _auth_id(_g(a, "brokerage_authorization"))
            if aid is not None:
                linked_ids.add(aid)

    any_disabled = None
    n_disabled = None
    any_linked_disabled = None
    disabled_date = None
    freshness_modes = None
    if conn_list is not None:
        any_disabled = False
        n_disabled = 0
        freshness_modes = []
        first_date = None
        first_linked_date = None
        linked_hit = False
        for c in conn_list:
            freshness_modes.append(_json_safe(_g(c, "data_freshness_mode")))
            if _is_true(_g(c, "disabled")):
                d = _g(c, "disabled_date")
                d_txt = None if d is None else str(_json_safe(d))
                if not any_disabled:
                    first_date = d_txt
                any_disabled = True
                n_disabled += 1
                if linked_ids is not None and _auth_id(_g(c, "id")) in linked_ids:
                    if not linked_hit:
                        first_linked_date = d_txt
                    linked_hit = True
        disabled_date = first_linked_date if linked_hit else first_date
        # None unless both lists were read. Accounts that carry no authorization
        # reference at all give no way to link a connection to them, so that is
        # "can't tell" too, never a clean False.
        if linked_ids is not None and (linked_ids or not acct_list):
            any_linked_disabled = linked_hit

    any_unavailable = None
    oldest = newest = None
    stamp_missing = None
    txn_oldest = None
    if acct_list is not None:
        any_unavailable = False
        stamp_missing = 0
        stamps = []
        txn_dates = []
        for a in acct_list:
            if _is_true(_unavailable_of(a)):
                any_unavailable = True
            st = parse_holdings_stamp(_holdings_of(a))
            if st is None:
                stamp_missing += 1
            else:
                stamps.append(st)
            td = parse_utc_date(_txn_stamp_of(a))
            if td is not None:
                txn_dates.append(td)
        if stamps:
            oldest, newest = min(stamps), max(stamps)
        if txn_dates:
            txn_oldest = min(txn_dates)

    now_utc = datetime.now(_UTC).isoformat()
    return {
        "run_started_at": _iso(run_started_at) or now_utc,
        "checked_at": _iso(checked_at) or now_utc,
        "accounts_ok": acct_list is not None,
        "connections_ok": conn_list is not None,
        "n_accounts": None if acct_list is None else len(acct_list),
        "n_connections": None if conn_list is None else len(conn_list),
        "any_disabled": any_disabled,
        "n_disabled": n_disabled,
        "any_linked_disabled": any_linked_disabled,
        "disabled_date": disabled_date,
        "any_holdings_unavailable": any_unavailable,
        "holdings_refreshed_oldest": None if oldest is None else oldest.isoformat(),
        "holdings_refreshed_newest": None if newest is None else newest.isoformat(),
        "holdings_stamp_missing": stamp_missing,
        "txn_refreshed_utc_date": None if txn_oldest is None else txn_oldest.isoformat(),
        "freshness_modes": freshness_modes,
        "positions_read_ok": None if positions_read_ok is None else bool(positions_read_ok),
        "snapshot_saved": None if snapshot_saved is None else bool(snapshot_saved),
        "positions_sig": positions_sig if isinstance(positions_sig, str) else None,
        "error": scrub_error(error),
    }


def log_summary(row: dict) -> str:
    """A short one-line description of a status row for the cron log: accounts,
    disabled flag, the oldest holdings stamp in ET, whether the snapshot saved.
    Never raises."""
    try:
        from stock_analyzer.market_time import ET
        oldest = parse_holdings_stamp(_g(row, "holdings_refreshed_oldest"))
        oldest_txt = "n/a" if oldest is None else oldest.astimezone(ET).strftime("%a %b %d %I:%M %p ET")
        return (f"accounts={_g(row, 'n_accounts')} "
                f"any_linked_disabled={_g(row, 'any_linked_disabled')} "
                f"oldest_refresh={oldest_txt} snapshot_saved={_g(row, 'snapshot_saved')}")
    except Exception:
        return "summary unavailable"
