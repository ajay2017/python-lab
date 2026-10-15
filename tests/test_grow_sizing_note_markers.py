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
