"""Tests for stock_analyzer.home_notices (Home redesign P1 — the one-line
notices summary above the "⚠️ Alerts" expander) and its wiring into app.py.

Two halves:
1. `summarize_notices` / `notices_headline` pure-logic boundaries — empty
   input, tier merge, unknown-tier/source handling, deterministic ordering,
   a simulated 16-source "busy day" (the logic-level stand-in for a
   multi-banner day that can't be forced to happen live), and a quiet day
   with only caption-tier entries (D4 — this must still produce a non-`None`
   muted result, never a blank page).
2. AST-based wiring tests on app.py, same pattern as
   tests/test_act_today_view.py's split_defensive guards — every one of the
   16 `_alert_ph_*` placeholders has a matching `_home_notice()` call
   reachable from its fill site, the 5 blocks that run on every render never
   call it as a bare top-level statement (it must be nested inside an If/For,
   or it would manufacture a false daily notice), the summary placeholder is
   declared before the Alerts expander's own first placeholder, and the
   `_home_notice` closure is defined before its first call (module-def-order
   convention).
"""
import ast
from pathlib import Path

import pytest

from stock_analyzer.home_notices import (
    SOURCE_LABELS,
    SOURCE_ORDER,
    notices_headline,
    summarize_notices,
)

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "app.py"


# ── summarize_notices / notices_headline — pure logic ───────────────────────

def test_empty_list_returns_none():
    assert summarize_notices([]) is None


def test_none_input_returns_none():
    assert summarize_notices(None) is None


def test_none_summary_never_yields_all_clear_headline():
    # There is no summary to build a headline from -- confirm the only
    # possible caller path (summary -> headline) stays None end to end,
    # never synthesizing all-clear-sounding text from an absent summary.
    assert notices_headline(summarize_notices(None)) is None
    assert notices_headline(summarize_notices([])) is None
    assert notices_headline(None) is None


def test_duplicate_source_keeps_highest_tier_counted_once():
    # Same source fires at both "caption" and "error" across two entries in
    # one render -- merged to "error", named once, never double-counted as
    # a note too.
    summary = summarize_notices([("dayshock", "caption"), ("dayshock", "error")])
    assert summary["tier"] == "error"
    assert summary["named"] == [SOURCE_LABELS["dayshock"]]
    assert summary["n_notes"] == 0


def test_highest_tier_wins_across_all_four_tiers():
    entries = [
        ("leverage", "caption"),
        ("scorewithheld", "info"),
        ("structural", "warning"),
        ("outage", "error"),
    ]
    summary = summarize_notices(entries)
    assert summary["tier"] == "error"
    # errors first (only one here), then warnings, in SOURCE_ORDER position
    assert summary["named"] == [SOURCE_LABELS["outage"], SOURCE_LABELS["structural"]]
    assert summary["n_notes"] == 2  # leverage + scorewithheld, distinct info/caption sources


def test_unknown_tier_string_counts_as_warning_never_dropped():
    summary = summarize_notices([("stale", "totally-made-up-tier")])
    assert summary["tier"] == "warning"
    assert summary["named"] == [SOURCE_LABELS["stale"]]
    assert summary["n_notes"] == 0


def test_unknown_source_id_kept_with_raw_id_as_label_never_dropped():
    summary = summarize_notices([("some_new_source_nobody_registered", "warning")])
    assert summary["tier"] == "warning"
    assert summary["named"] == ["some_new_source_nobody_registered"]


def test_deterministic_ordering_is_source_order_position_not_call_order_or_alpha():
    # Called in reverse SOURCE_ORDER / non-alphabetical order on purpose.
    entries = [
        ("debrief", "error"),      # SOURCE_ORDER index 15
        ("dayshock", "error"),     # index 0
        ("systrust", "error"),     # index 6
        ("pnldq", "warning"),      # index 13
        ("xcheck", "warning"),     # index 1
    ]
    summary = summarize_notices(entries)
    assert summary["named"] == [
        SOURCE_LABELS["dayshock"], SOURCE_LABELS["systrust"], SOURCE_LABELS["debrief"],
        SOURCE_LABELS["xcheck"], SOURCE_LABELS["pnldq"],
    ]


def test_busy_day_all_16_sources_exact_named_and_n_notes_split():
    # One realistic mixed-tier entry per source, covering every id in
    # SOURCE_ORDER -- the logic-level stand-in for a day where every one of
    # the 16 Alerts-expander fill sites fires at once, which can't be forced
    # to happen live.
    tiers_by_source = {
        "dayshock":      "error",
        "xcheck":        "warning",
        "split":         "warning",
        "structural":    "warning",
        "drift":         "caption",
        "thesis":        "warning",
        "systrust":      "error",
        "heldload":      "caption",
        "stale":         "warning",
        "dropped":       "warning",
        "scorewithheld": "info",
        "outage":        "error",
        "leverage":      "caption",
        "pnldq":         "warning",
        "crossasset":    "info",
        "debrief":       "error",
    }
    assert set(tiers_by_source) == set(SOURCE_ORDER)  # keep this test honest
    entries = [(src, tier) for src, tier in tiers_by_source.items()]
    summary = summarize_notices(entries)

    expected_errors   = ["dayshock", "systrust", "outage", "debrief"]
    expected_warnings = ["xcheck", "split", "structural", "thesis", "stale", "dropped", "pnldq"]
    expected_notes    = ["drift", "heldload", "scorewithheld", "leverage", "crossasset"]

    assert summary["tier"] == "error"
    assert summary["named"] == [SOURCE_LABELS[s] for s in expected_errors] + \
                                [SOURCE_LABELS[s] for s in expected_warnings]
    assert summary["n_notes"] == len(expected_notes)

    headline = notices_headline(summary)
    assert headline is not None
    for s in expected_errors + expected_warnings:
        assert SOURCE_LABELS[s] in headline
    assert f"{len(expected_notes)} more note" in headline


def test_quiet_day_only_caption_tier_still_produces_non_none_muted_result():
    # The leverage caption fires almost every render for this book (per
    # CLAUDE.md's own coordination notes) -- confirm an otherwise-quiet day
    # with only that caption still yields a visible, non-None line (D4),
    # never silently nothing.
    summary = summarize_notices([("leverage", "caption")])
    assert summary is not None
    assert summary["tier"] == "info"
    assert summary["named"] == []
    assert summary["n_notes"] == 1

    headline = notices_headline(summary)
    assert headline is not None
    assert "1 note" in headline


# ── AST-based wiring tests on app.py ─────────────────────────────────────────
#
# Determined by reading app.py directly (not the plan doc's own estimate,
# which may have drifted): of the 16 `with _alert_ph_X.container():` blocks,
# these 5 execute on EVERY Home render regardless of whether their condition
# actually fires -- the real conditional lives one level deeper, inside an
# If/For. An unguarded `_home_notice()` call directly in one of these blocks'
# own top-level body would manufacture a false daily notice.
_UNCONDITIONAL_SOURCES = ("dayshock", "xcheck", "split", "structural", "thesis")


def _parse_app_py() -> ast.Module:
    src = APP_PY.read_text(encoding="utf-8-sig")
    return ast.parse(src, filename=str(APP_PY))


def _is_home_notice_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_home_notice"
    )


def _home_notice_call_source_id(node: ast.Call) -> str | None:
    if not node.args:
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) else None


def _placeholder_source_ids(tree: ast.Module) -> list[str]:
    """Every `_alert_ph_<id> = st.empty()`-shaped assignment's `<id>`."""
    ids = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("_alert_ph_"):
                    ids.append(target.id[len("_alert_ph_"):])
    return ids


def test_app_py_declares_exactly_the_16_expected_placeholders():
    # Sanity check that this test file's own expectations (SOURCE_ORDER)
    # haven't drifted from the real app.py placeholder set.
    tree = _parse_app_py()
    assert set(_placeholder_source_ids(tree)) == set(SOURCE_ORDER)


def test_every_alert_placeholder_has_a_matching_home_notice_call():
    tree = _parse_app_py()
    placeholder_ids = set(_placeholder_source_ids(tree))
    called_ids = {
        _home_notice_call_source_id(node)
        for node in ast.walk(tree)
        if _is_home_notice_call(node)
    }
    missing = placeholder_ids - called_ids
    assert not missing, (
        f"placeholder(s) with no matching _home_notice(\"<id>\", ...) call "
        f"reachable from their fill site: {sorted(missing)}"
    )


def _find_container_with_nodes(tree: ast.Module, source_id: str) -> list[ast.With]:
    name = f"_alert_ph_{source_id}"
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            ctx = item.context_expr
            if (
                isinstance(ctx, ast.Call)
                and isinstance(ctx.func, ast.Attribute)
                and ctx.func.attr == "container"
                and isinstance(ctx.func.value, ast.Name)
                and ctx.func.value.id == name
            ):
                hits.append(node)
                break
    return hits


def _bare_top_level_home_notice_calls(with_node: ast.With) -> list[ast.stmt]:
    """A bare `_home_notice(...)` as a direct statement of the with-block's
    OWN body -- the exact shape that would fire on every render for one of
    the 5 unconditional blocks."""
    return [
        stmt for stmt in with_node.body
        if isinstance(stmt, ast.Expr) and _is_home_notice_call(stmt.value)
    ]


def test_unconditional_alert_blocks_never_call_home_notice_at_top_level():
    tree = _parse_app_py()
    for source_id in _UNCONDITIONAL_SOURCES:
        with_nodes = _find_container_with_nodes(tree, source_id)
        assert with_nodes, f"no `with _alert_ph_{source_id}.container():` block found in app.py"
        for wn in with_nodes:
            bad = _bare_top_level_home_notice_calls(wn)
            assert not bad, (
                f"_alert_ph_{source_id}'s container block calls _home_notice() "
                "directly in its own top-level body -- this block runs on EVERY "
                "Home render even when nothing fires, so an unguarded call here "
                "would manufacture a false daily notice. Nest it inside the If/For "
                "that actually decided something fired."
            )
            has_call_anywhere = any(_is_home_notice_call(n) for n in ast.walk(wn))
            assert has_call_anywhere, (
                f"_alert_ph_{source_id}'s container block has no _home_notice() "
                "call anywhere inside it -- hook wiring appears to be missing."
            )


def test_notices_summary_placeholder_declared_before_alerts_expander():
    tree = _parse_app_py()
    notices_ph_line = None
    first_alert_ph_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "_notices_summary_ph" and notices_ph_line is None:
                    notices_ph_line = node.lineno
                if target.id.startswith("_alert_ph_"):
                    if first_alert_ph_line is None or node.lineno < first_alert_ph_line:
                        first_alert_ph_line = node.lineno
    assert notices_ph_line is not None, "_notices_summary_ph = st.empty() not found in app.py"
    assert first_alert_ph_line is not None, "no _alert_ph_* placeholder found in app.py"
    assert notices_ph_line < first_alert_ph_line, (
        "_notices_summary_ph must be declared BEFORE the Alerts expander's own "
        "first placeholder, so the summary always renders above it (D7)."
    )


def test_home_notice_closure_defined_before_its_first_call():
    tree = _parse_app_py()
    def_line = None
    first_call_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_home_notice":
            def_line = node.lineno
        if _is_home_notice_call(node):
            if first_call_line is None or node.lineno < first_call_line:
                first_call_line = node.lineno
    assert def_line is not None, "_home_notice closure not found in app.py"
    assert first_call_line is not None, "no _home_notice(...) call found in app.py"
    assert def_line < first_call_line, (
        "_home_notice must be defined before its first call site "
        "(feedback_module_def_order)."
    )
