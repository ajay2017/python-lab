"""Grow Today's two sizing-note blocks (new-pick and add-to-winner) must
branch on EVERY disclosure marker `daily_briefing._position_size_for_render`
can emit -- otherwise a capped size shows no reason / an infeasible BUY shows
no size and no reason. Pins BOTH ends: the marker set is derived from the
producer's source, the consumer blocks from app.py's AST."""
import ast
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MARKER_RE = re.compile(r"(_capped|_infeasible|_unknown)$")


def _producer_markers() -> set:
    tree = ast.parse((_ROOT / "stock_analyzer" / "daily_briefing.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_position_size_for_render")
    keys = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Dict):
            for k in n.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
                and isinstance(n.slice.value, str) and isinstance(n.value, ast.Name) \
                and n.value.id == "out":
            keys.add(n.slice.value)
    return {k for k in keys if _MARKER_RE.search(k)}


def _is_sz_get(node, key=None):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_sz" and node.args
            and isinstance(node.args[0], ast.Constant)
            and (key is None or node.args[0].value == key))


def _consumer_blocks() -> list:
    """Per `_sz.get("ceiling_capped")` If: the keys read by it and its next sibling."""
    tree = ast.parse((_ROOT / "app.py").read_text(encoding="utf-8"))
    blocks = []
    for parent in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(parent, field, None)
            if not isinstance(body, list):
                continue
            for i, stmt in enumerate(body):
                if isinstance(stmt, ast.If) and any(
                        _is_sz_get(n, "ceiling_capped") for n in ast.walk(stmt.test)):
                    keys = set()
                    for node in body[i:i + 2]:
                        for n in ast.walk(node):
                            if _is_sz_get(n):
                                keys.add(n.args[0].value)
                    blocks.append(keys)
    return blocks


def test_producer_marker_set_is_what_we_expect():
    # Guards the derivation itself against going vacuous.
    assert _producer_markers() >= {
        "ceiling_capped", "capital_capped", "ceiling_infeasible",
        "capital_infeasible", "stop_infeasible", "portfolio_unknown"}


def test_two_grow_today_blocks_found():
    assert len(_consumer_blocks()) == 2


def test_each_grow_today_block_branches_on_every_marker():
    markers = _producer_markers()
    for keys in _consumer_blocks():
        assert markers <= keys, f"missing branches: {sorted(markers - keys)}"


# ── Daily new-position budget markers (chunk 4, docs/plans/daily-new-position-
# budget.md). The F-255 markers above are branched on inline; the budget ones
# are rendered through ONE pure function, `budget_view.card_note`, so the pin
# has two ends here too: (producer) every budget flag `daily_budget.
# apply_to_sizing` can emit must yield a note from card_note; (consumer) both
# Grow Today blocks must call card_note on their `_sz`, with the right kind.
_BUDGET_FLAG_RE = re.compile(r"budget_(capped|reserved|exhausted|unknown)$")


def _budget_producer_flags() -> set:
    src = (_ROOT / "stock_analyzer" / "daily_budget.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "apply_to_sizing")
    flags = {n.value for n in ast.walk(fn)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and _BUDGET_FLAG_RE.match(n.value)}
    for n in tree.body:   # exhausted/unknown go out via out[status] (module constants)
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) \
                and n.targets[0].id in ("BUDGET_EXHAUSTED", "BUDGET_UNKNOWN"):
            flags.add(n.value.value)
    return flags


def test_budget_producer_flag_set_is_what_we_expect():
    assert _budget_producer_flags() == {
        "budget_capped", "budget_reserved", "budget_exhausted", "budget_unknown"}


def test_card_note_covers_every_budget_flag_the_producer_emits():
    from stock_analyzer import budget_view
    for flag in _budget_producer_flags():
        sz = {"shares": 5, "budget_uncapped_shares": 9, flag: True}
        for kind in ("pick", "add"):
            assert budget_view.card_note(sz, kind), (flag, kind)


def _card_note_calls() -> list:
    """(kind, sibling-offset-from-ceiling-If) for each `_budget_view.card_note(_sz, kind, ...)`."""
    tree = ast.parse((_ROOT / "app.py").read_text(encoding="utf-8"))
    found = []
    for parent in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(parent, field, None)
            if not isinstance(body, list):
                continue
            ceil_idx = [i for i, st in enumerate(body) if isinstance(st, ast.If) and any(
                _is_sz_get(n, "ceiling_capped") for n in ast.walk(st.test))]
            for i, stmt in enumerate(body):
                node = stmt.value if isinstance(stmt, ast.Assign) else None
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "card_note"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "_budget_view"
                        and node.args and isinstance(node.args[0], ast.Name)
                        and node.args[0].id == "_sz"
                        and isinstance(node.args[1], ast.Constant)):
                    near = [i - c for c in ceil_idx if 0 < i - c <= 3]
                    found.append((node.args[1].value, bool(near)))
    return found


def test_both_grow_today_blocks_render_the_budget_note_with_the_right_kind():
    calls = _card_note_calls()
    assert sorted(k for k, _ in calls) == ["add", "pick"]
    assert all(near for _, near in calls), \
        "card_note must sit in the same block as the F-255 sizing notes"
