"""AST wiring pins for C7 (Weekly Debrief on the period_attribution engine).

app.py has no behavioural tests, so the wiring that makes Reports, the app's
"Generate Now" button and the Sunday cron share ONE engine path is pinned at
both ends here: the consumers call `period_inputs.compute_period_core` /
`load_engine_inputs` (never `reconcile_period` directly) and the Weekly Debrief
call sites pass the engine result plus the signal frames.

app.py is parsed through tests._ast_cache (a plain ast.parse of it costs
minutes under xdist)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._ast_cache import parse_file_cached

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
CRON = ROOT / "cron_runner.py"


def _calls(tree, lo=None, hi=None):
    """[(attr_or_name, ast.Call)] for calls on lines lo..hi (inclusive)."""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        if lo is not None and not (lo <= n.lineno <= hi):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
        if name:
            out.append((name, n))
    return out


def _names(calls):
    return {n for n, _ in calls}


def _line_of(text: str, needle: str) -> int:
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    raise AssertionError(f"marker not found in source: {needle!r}")


@pytest.fixture(scope="module")
def app_parts():
    text = APP.read_text(encoding="utf-8")
    tree = parse_file_cached(APP)
    reports = (_line_of(text, "# ── Period report (C4)"), _line_of(text, "# ── 1 · Period result"))
    debrief = (_line_of(text, 'key="_wd_generate_btn"'), _line_of(text, "# Display most recent debrief"))
    return text, tree, reports, debrief


def test_app_never_calls_reconcile_period_directly(app_parts):
    _text, tree, _r, _d = app_parts
    assert "reconcile_period" not in _names(_calls(tree))


def test_reports_block_goes_through_the_shared_helper(app_parts):
    _text, tree, (lo, hi), _d = app_parts
    names = _names(_calls(tree, lo, hi))
    assert {"load_engine_inputs", "compute_period_core"} <= names
    assert "leverage_section" not in names and "load_daily_snapshots_or_none" not in names


def test_app_debrief_block_uses_the_same_helper_and_passes_the_signal_frames(app_parts):
    _text, tree, _r, (lo, hi) = app_parts
    calls = _calls(tree, lo, hi)
    names = _names(calls)
    assert {"load_engine_inputs", "compute_period_core", "resolve_period",
            "spy_closes_by_date"} <= names
    build = next(c for n, c in calls if n == "build_debrief_package")
    kws = {k.arg for k in build.keywords}
    assert {"core", "exit_signals_df", "all_recs_df", "trades_df", "recs_df"} <= kws
    assert "snapshots_df" not in kws and "spy_week_pct" not in kws
    # the legacy outage-vs-insufficient classifier is gone from the page
    assert "classify_snapshot_read" not in names and "load_daily_snapshots_or_none" not in names
    # trades come from the engine inputs, not the session's cached frame
    trades_kw = next(k for k in build.keywords if k.arg == "trades_df")
    assert "inputs" in ast.dump(trades_kw.value)


def test_no_leftover_min_snapshot_days_constant_anywhere():
    for path in (APP, CRON, ROOT / "stock_analyzer" / "constants.py",
                 ROOT / "stock_analyzer" / "debrief_advisor.py"):
        assert "WEEKLY_DEBRIEF_MIN_SNAPSHOT_DAYS" not in path.read_text(encoding="utf-8"), path.name
    assert "WEEKLY_DEBRIEF_MIN_SNAPSHOT_DAYS" not in (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")


def _run_debrief_node():
    tree = parse_file_cached(CRON)
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run_debrief")


def test_cron_debrief_uses_the_shared_engine_path():
    fn = _run_debrief_node()
    calls = _calls(fn)
    names = _names(calls)
    assert {"load_engine_inputs", "compute_period_core", "resolve_period", "spy_closes_by_date"} <= names
    assert "load_daily_snapshots" not in names and "classify_snapshot_read" not in names
    assert "load_trades" not in names           # trades come from the engine inputs
    assert "load_exit_signals_or_none" in names and "load_exit_signals" not in names
    build = next(c for n, c in calls if n == "build_debrief_package")
    kws = {k.arg for k in build.keywords}
    assert {"core", "exit_signals_df", "all_recs_df", "trades_df", "recs_df"} <= kws
    email = next(c for n, c in calls if n == "render_debrief_email")
    ekw = {k.arg for k in email.keywords}
    assert "period" in ekw and "week_had_trades" not in ekw


def test_cron_debrief_failure_paths_are_wired():
    names = _names(_calls(_run_debrief_node()))
    assert "_db_unavailable_detail" in names and "_handle_db_unavailable" in names
    assert "_notify_failure" in names


def test_weekly_debrief_cols_carry_the_method_version():
    from stock_analyzer import db
    assert "metrics_version" in db._WEEKLY_DEBRIEF_COLS
