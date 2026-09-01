"""
One-time migration script — REST-API fallback for `seed_reference_tables.py`.

WHY THIS SECOND SCRIPT EXISTS: `scripts/seed_reference_tables.py` (the real,
canonical seed script) goes through `stock_analyzer.db`, which needs the
`supabase` package installed. On a Python 3.14 dev machine, `pip install
supabase` fails: `storage3` (a hard dependency of `supabase-py`, used only for
Supabase's file-Storage feature — `db.py` never touches it) unconditionally
requires `pyiceberg`, and `pyiceberg` has no pre-built wheel for cp314 at all
(confirmed against PyPI 2026-09-01; latest release 0.11.1 only ships wheels up
to cp313), so pip falls back to a source build that needs a C++ compiler this
machine doesn't have. Production (`runtime.txt` pins python-3.12) is
unaffected — this is a LOCAL, Python-3.14-ONLY dev-machine problem.

This script does the IDENTICAL 3-table seed as the real script, but talks to
Supabase's plain REST API (PostgREST) directly via the stdlib `urllib` module
— zero third-party dependencies, so it needs no `pip install` at all and
sidesteps the pyiceberg/cp314 wheel gap entirely. It reuses
`stock_analyzer.reference_data.canonicalize` (a pure function with no DB
dependency, so importing it does NOT need the `supabase` package) to keep the
canonicalization logic identical to what `db.py::save_reference_table` uses
in production — this script must produce the exact same payload_hash a real
`save_reference_table` call would, or a future real save could be treated as
a spurious "change" purely due to hashing differently.

RUN ONCE, BY HAND, AFTER the DDL in docs/sql/app_settings_ddl.sql has been
applied. Re-running is safe and idempotent (same content-hash no-op-save
mechanism as the real script).

Usage (from a shell with the app's Supabase env vars set):

    python scripts/seed_reference_tables_rest_fallback.py

Prefer `scripts/seed_reference_tables.py` whenever `supabase` is installable
(e.g. on a Python 3.10-3.13 venv, or after installing a C++ toolchain) — this
fallback exists only to unblock the Python-3.14 case without requiring either.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer.discovery_universe import DISCOVERY_UNIVERSE  # noqa: E402
from stock_analyzer.portfolio import _SECTOR_CANDIDATES  # noqa: E402
from stock_analyzer.reference_data import canonicalize  # noqa: E402
from stock_analyzer.scanner import SECTOR_UNIVERSE  # noqa: E402

_TABLES = (
    ("sector_universe", SECTOR_UNIVERSE),
    ("discovery_universe", DISCOVERY_UNIVERSE),
    ("sector_candidates", _SECTOR_CANDIDATES),
)


def _env_or_die(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"ERROR: ${name} is not set. Export it before running this script.")
        sys.exit(1)
    return val


def _rest_request(
    base_url: str, api_key: str, method: str, path: str,
    params: "dict | None" = None, body: "dict | list | None" = None,
    prefer: "str | None" = None,
) -> "list | None":
    """Minimal PostgREST caller via stdlib urllib. Returns the parsed JSON
    response (a list of rows, PostgREST's normal shape) or None on any
    failure — mirrors the offline-sentinel contract the real db.py uses,
    even though this script is a one-off, not a production code path."""
    url = f"{base_url}/rest/v1/{path}"
    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"  HTTP {e.code} on {method} {path}: {detail[:300]}")
        return None
    except Exception as e:  # noqa: BLE001 - a one-off script, report and continue
        print(f"  request failed on {method} {path}: {e}")
        return None


def _seed_one(base_url: str, api_key: str, name: str, payload: dict) -> str:
    normalized = canonicalize(payload)
    new_hash = hashlib.sha256(
        json.dumps(normalized, sort_keys=True).encode("utf-8")
    ).hexdigest()

    existing = _rest_request(
        base_url, api_key, "GET", "reference_tables",
        params={"name": f"eq.{name}", "select": "payload_hash"},
    )
    if existing is None:
        print(f"  [{name}] ERROR — could not read existing row (see request error above)")
        return "error"
    if existing and existing[0].get("payload_hash") == new_hash:
        print(f"  [{name}] no_change — already seeded with this exact payload")
        return "no_change"

    today = date.today().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()
    row = {
        "name": name,
        "payload": normalized,
        "payload_hash": new_hash,
        "as_of": today,
        "updated_by": "seed_script_rest_fallback",
        "updated_at": now_iso,
    }
    upserted = _rest_request(
        base_url, api_key, "POST", "reference_tables",
        params={"on_conflict": "name"},
        body=row,
        prefer="resolution=merge-duplicates,return=representation",
    )
    if upserted is None:
        print(f"  [{name}] ERROR — upsert failed (see request error above)")
        return "error"

    history_row = {
        "name": name,
        "payload": normalized,
        "payload_hash": new_hash,
        "as_of": today,
        "updated_by": "seed_script_rest_fallback",
    }
    hist = _rest_request(
        base_url, api_key, "POST", "reference_table_history",
        body=history_row, prefer="return=representation",
    )
    if hist is None:
        print(f"  [{name}] SAVED to reference_tables but the history-row insert FAILED "
              "— check reference_table_history's RLS/DDL")
        return "error"

    print(f"  [{name}] SAVED — as_of={today}")
    return "saved"


def main() -> int:
    base_url = _env_or_die("SUPABASE_URL").rstrip("/")
    api_key = _env_or_die("SUPABASE_KEY")

    print("Seeding reference_tables from the current hardcoded code lists.")
    print("(REST-API fallback — see this script's own docstring for why it exists)\n")

    summary: dict[str, int] = {"saved": 0, "no_change": 0, "error": 0}
    for name, payload in _TABLES:
        status = _seed_one(base_url, api_key, name, payload)
        summary[status] = summary.get(status, 0) + 1

    print(
        f"\nDone: {summary.get('saved', 0)} saved, "
        f"{summary.get('no_change', 0)} unchanged, "
        f"{summary.get('error', 0)} error(s)."
    )
    print(
        "The running app still reads the hardcoded code lists directly for "
        "commit-1-only deployments; if commit 2 (the resolver rewiring) is "
        "already live, it now reads through the DB rows this script just wrote."
    )
    return 1 if summary.get("error", 0) else 0


if __name__ == "__main__":
    sys.exit(main())
