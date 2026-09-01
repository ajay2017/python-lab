"""
One-time migration script — seeds `reference_tables` from the CURRENT
hardcoded code lists. App Settings, Commit 1 of 3 (docs/plans/app-settings.md).

RUN ONCE, BY HAND, AFTER the DDL in docs/sql/app_settings_ddl.sql has been
applied in the Supabase dashboard. This script does NOT run automatically —
it is not wired into any cron lane (see `stock_analyzer/system_health.py`'s
`_LANES`), and nothing in the app calls it. Re-running it later is safe and
idempotent: `db.save_reference_table`'s content-hash mechanism means seeding
an already-seeded, unchanged table reports "no_change" rather than
re-stamping `as_of` or writing a duplicate history row.

Usage (from a shell with the app's Supabase env vars / secrets set):

    python scripts/seed_reference_tables.py

Seeds exactly the three tables migrated in this commit:
  - sector_universe    <- stock_analyzer.scanner.SECTOR_UNIVERSE
  - discovery_universe <- stock_analyzer.discovery_universe.DISCOVERY_UNIVERSE
  - sector_candidates  <- stock_analyzer.portfolio._SECTOR_CANDIDATES

IMPORTANT — this script does NOT change what the running app reads. Per the
design doc's staged cutover, the hardcoded code lists above are NOT deleted
by this script or by this commit; every existing importer (scanner.py,
portfolio.py, ticker_liveness.py, cron_runner.py, the ~6 app.py sites) keeps
reading them exactly as it does today. This only writes a DB-side mirror so
Commit 2's `⚙️ App Settings` page has something to show and edit, and so a
future Commit 3 can verify the DB row is right before ever flipping the
cutover and deleting the code lists.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import db  # noqa: E402
from stock_analyzer.discovery_universe import DISCOVERY_UNIVERSE  # noqa: E402
from stock_analyzer.portfolio import _SECTOR_CANDIDATES  # noqa: E402
from stock_analyzer.scanner import SECTOR_UNIVERSE  # noqa: E402

_TABLES = (
    ("sector_universe", SECTOR_UNIVERSE),
    ("discovery_universe", DISCOVERY_UNIVERSE),
    ("sector_candidates", _SECTOR_CANDIDATES),
)


def _seed_one(name: str, payload: dict) -> str:
    """Seed one table; returns the status string for the run summary."""
    result = db.save_reference_table(name, payload, updated_by="seed_script")
    status = result.get("status", "error")
    if status == "saved":
        print(f"  [{name}] SAVED — as_of={result.get('as_of')}")
    elif status == "no_change":
        print(f"  [{name}] no_change — already seeded with this exact payload")
    else:
        print(f"  [{name}] ERROR — {result.get('detail')}")
    return status


def main() -> int:
    print("Seeding reference_tables from the current hardcoded code lists.")
    print("(one-time migration — see this script's own docstring before re-running)\n")

    summary: dict[str, int] = {"saved": 0, "no_change": 0, "error": 0}
    for name, payload in _TABLES:
        status = _seed_one(name, payload)
        summary[status] = summary.get(status, 0) + 1

    print(
        f"\nDone: {summary.get('saved', 0)} saved, "
        f"{summary.get('no_change', 0)} unchanged, "
        f"{summary.get('error', 0)} error(s)."
    )
    print(
        "The running app still reads the hardcoded code lists directly — "
        "this seed does not change app behaviour (Commit 2 wires up the "
        "resolver + editor; Commit 3 flips the cutover)."
    )
    return 1 if summary.get("error", 0) else 0


if __name__ == "__main__":
    sys.exit(main())
