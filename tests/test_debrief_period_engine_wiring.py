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
    assert {"core", "exit_signals_df", "exit_signals_unavailable", "recs_unavailable", "all_recs_df",
            "trades_df", "recs_df"} <= kws
    assert "snapshots_df" not in kws and "spy_week_pct" not in kws
    # both ends pinned (DebRecs review 2026-11-09): the page reads signals strictly
    assert "load_recommendations_or_none" in names and "load_recommendations" not in names
    assert "load_exit_signals_or_none" in names and "load_exit_signals" not in names
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
    assert "load_recommendations_or_none" in names and "load_recommendations" not in names
    build = next(c for n, c in calls if n == "build_debrief_package")
    kws = {k.arg for k in build.keywords}
    assert {"core", "exit_signals_df", "exit_signals_unavailable", "recs_unavailable", "all_recs_df",
            "trades_df", "recs_df"} <= kws
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


def test_weekly_debrief_cols_carry_the_acted_rule_version():
    from stock_analyzer import db
    assert "acted_rule_version" in db._WEEKLY_DEBRIEF_COLS


# ── acted-rule C3b wiring pins: the new calls must FEED the loads / renders ───
# Each pin is a function over source text, so a test can run it on the real
# source AND on an in-memory copy with the wiring removed (it must then fail).

_WD_REGION_START = "from stock_analyzer import debrief_entry_calls as _wd_dec"
_WD_REGION_END = "# Numbers behind the debrief just generated"


def _weekly_debrief_region(text: str) -> str:
    lo, hi = text.index(_WD_REGION_START), text.index(_WD_REGION_END)
    assert lo < hi
    return text[lo:hi]


def _pin_generate_recs_start(region: str) -> bool:
    """The Generate path's recommendations load takes start_date from
    debrief_entry_calls.recs_load_start(week_start) and ends at the week's end."""
    import re
    return bool(re.search(
        r"load_recommendations_or_none\(\s*start_date\s*=\s*_wd_dec\.recs_load_start\(\s*_wd_start\s*\)"
        r"\s*,\s*end_date\s*=\s*_wd_today\s*,?\s*\)", region))


def _pin_strict_weekly_read(region: str) -> bool:
    """The page-load read is the strict one, never the lenient loader, and a
    None read shows the "couldn't read" message (not "No weekly debriefs yet")."""
    import re
    strict = re.search(r"_wd_df\s*=\s*_ai_db\.load_weekly_debriefs_or_none\(", region)
    lenient = re.search(r"\bload_weekly_debriefs\(", region)
    flag = re.search(r"_wd_read_failed\s*=\s*_wd_df\s+is\s+None", region)
    branch = re.search(
        r"if\s+_wd_read_failed:\s*\n\s*st\.warning\(\s*\n?\s*\"Couldn't read the saved debriefs", region)
    return bool(strict and flag and branch and not lenient)


def _pin_stored_row_caption(region: str) -> bool:
    """The stored row's version feeds acted_rule_caption and the result is shown."""
    import re
    return bool(re.search(
        r"(\w+)\s*=\s*_wd_dec\.acted_rule_caption\(\s*_wd\.get\(\"acted_rule_version\"\)\s*\)"
        r"\s*\n\s*if\s+\1:\s*\n\s*st\.caption\(\s*\1\s*\)", region))


def _without(region: str, old: str, new: str) -> str:
    assert old in region, f"mutation target not found: {old!r}"
    mutated = region.replace(old, new)
    assert mutated != region
    return mutated


def test_app_generate_loads_recs_from_recs_load_start(app_parts):
    region = _weekly_debrief_region(app_parts[0])
    assert _pin_generate_recs_start(region)
    # wiring removed in memory -> the pin fails (the bare week start, or the call
    # merely sitting elsewhere in the block, does not satisfy it)
    assert not _pin_generate_recs_start(
        _without(region, "_wd_dec.recs_load_start(_wd_start)", "_wd_start"))
    assert not _pin_generate_recs_start(
        _without(region, "start_date=_wd_dec.recs_load_start(_wd_start),",
                 "start_date=_wd_start,  # _wd_dec.recs_load_start(_wd_start)\n"))


def test_app_weekly_view_reads_saved_debriefs_strictly_with_a_none_message(app_parts):
    region = _weekly_debrief_region(app_parts[0])
    assert _pin_strict_weekly_read(region)
    assert not _pin_strict_weekly_read(_without(
        region, "_wd_df = _ai_db.load_weekly_debriefs_or_none(", "_wd_df = _ai_db.load_weekly_debriefs("))
    assert not _pin_strict_weekly_read(_without(
        region, "if _wd_read_failed:\n                st.warning(", "if _wd_read_failed:\n                st.info("))
    assert not _pin_strict_weekly_read(_without(
        region, "_wd_read_failed = _wd_df is None", "_wd_read_failed = False"))


def test_app_stored_row_render_captions_the_acted_rule_version(app_parts):
    region = _weekly_debrief_region(app_parts[0])
    assert _pin_stored_row_caption(region)
    assert not _pin_stored_row_caption(_without(region, "st.caption(_wd_rule_cap)", "pass"))
    assert not _pin_stored_row_caption(_without(
        region, '_wd.get("acted_rule_version")', '_wd.get("metrics_version")'))


def _pin_cron_recs_start(fn_source: str) -> bool:
    """In _run_debrief the week-scoped recs read's start_date is a name assigned
    from recs_load_start(...), and it ends at week_ending."""
    fn = ast.parse(fn_source).body[0]
    reads = [c for n, c in _calls(fn) if n == "load_recommendations_or_none"
             and any(k.arg == "start_date" for k in c.keywords)]
    if len(reads) != 1:
        return False
    start = next(k.value for k in reads[0].keywords if k.arg == "start_date")
    end = next((k.value for k in reads[0].keywords if k.arg == "end_date"), None)
    if not (isinstance(start, ast.Name) and isinstance(end, ast.Name) and end.id == "week_ending"):
        return False
    return any(
        isinstance(a, ast.Assign) and any(isinstance(t, ast.Name) and t.id == start.id for t in a.targets)
        and isinstance(a.value, ast.Call) and isinstance(a.value.func, ast.Attribute)
        and a.value.func.attr == "recs_load_start"
        for a in ast.walk(fn))


def test_cron_debrief_recs_start_comes_from_recs_load_start():
    # Also pinned behaviourally in test_cron_debrief_monthly_date_anchor.py
    # (Sunday 2026-11-08 loads from 2026-10-26); this is the source-level pin.
    src = ast.get_source_segment(CRON.read_text(encoding="utf-8"), _run_debrief_node())
    assert _pin_cron_recs_start(src)
    assert not _pin_cron_recs_start(_without(src, "_dec.recs_load_start(week_start)", "week_start"))
