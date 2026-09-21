"""Structural guard (2026-09-21 audit, "Systemic observations" §8): a
cron-written table with no row in `system_health.py`'s `_INVENTORY` is a
dead-diagnostic gap -- check (c) can't tell a silent write failure or an
un-applied DDL from a healthy day. Two audits in a row found a real store
missing from the inventory only after it had shipped without one
(`account_daily_snapshots`, `score_history`) -- this test exists so the next
one is caught at commit time instead of at the next audit.

Statically parses `cron_runner.py` for every `db.save_*(...)` call site,
resolves each called function's target table(s) by parsing `db.py`'s own
function body for a `.table("literal")` call, and asserts each resolved
table is either registered in `_INVENTORY` or on the small, reasoned
allowlist below. This is a completeness check on the registry, not a
correctness check on any individual `_Store`'s fields (unconditional/lane/
date_col) -- those stay eyeballed at review time, same as today.
"""
import ast
from pathlib import Path

import pytest

from stock_analyzer.system_health import _INVENTORY

pytestmark = pytest.mark.fast

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CRON_RUNNER = _REPO_ROOT / "cron_runner.py"
_DB_PY = _REPO_ROOT / "stock_analyzer" / "db.py"

# Deliberately excluded from _INVENTORY -- covered by System Trust check (a)
# (cron liveness), not check (c) (data-store health), so absence here is
# correct, not a gap. See system_health.py's own module docstring for the
# six-check breakdown.
_ALLOWLIST = {
    "cron_heartbeat",  # check (a) reads this table directly (check_cron_liveness)
    "alert_state",     # check (a)'s per-lane row-keyed dedup state (_LANES / _Lane)
}


def _called_save_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith("save_")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "db"
        ):
            names.add(node.func.attr)
    return names


def _function_table_targets(path: Path, func_names: set[str]) -> dict[str, set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in func_names:
            tables: set[str] = set()
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "table"
                    and sub.args
                    and isinstance(sub.args[0], ast.Constant)
                    and isinstance(sub.args[0].value, str)
                ):
                    tables.add(sub.args[0].value)
            result[node.name] = tables
    return result


def test_every_cron_written_table_is_registered_in_system_health_inventory():
    called = _called_save_functions(_CRON_RUNNER)
    assert called, "expected at least one db.save_* call in cron_runner.py -- parser regressed?"

    targets = _function_table_targets(_DB_PY, called)
    registered = {s.table for s in _INVENTORY}

    missing = sorted(
        (fn, table)
        for fn, tables in targets.items()
        for table in tables
        if table not in registered and table not in _ALLOWLIST
    )
    assert not missing, (
        "cron_runner.py writes to table(s) with no system_health._INVENTORY row "
        "(a dead-diagnostic gap -- a silently-failed write or an un-applied DDL "
        "would be invisible on 🩺 System Trust). Add a _Store(...) row, or add the "
        f"table to _ALLOWLIST above with a reason if a different check covers it: {missing}"
    )


def test_allowlist_entries_are_still_actually_excluded_from_the_inventory():
    """Guards the allowlist itself from silently rotting: if either name is
    ever ALSO registered in _INVENTORY, the allowlist entry is stale and
    should be removed (redundant, not wrong, but worth catching)."""
    registered = {s.table for s in _INVENTORY}
    stale = _ALLOWLIST & registered
    assert not stale, f"allowlist entries already in _INVENTORY, safe to remove: {stale}"
