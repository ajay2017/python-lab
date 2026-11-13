"""TJDate U8c: wiring pins for 🏠 Home's "Journal vs Robinhood" line (AST / text on
app.py and stock_analyzer/; docs/plans/journal-vs-robinhood-u8.md "Risks /
coordination" and "Tests the build must include").

No test imports app.py, so the one-writer / one-reader / outside-the-Brief rules are
pinned on the source: `_journal_match_cache` is awareness only, written at the Home fill
site and read there through `get_or_offline`, and never reaches Act Today, the Brief or
the Home synthesis.
"""
import ast
from pathlib import Path

import pytest
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "app.py"
PKG = ROOT / "stock_analyzer"
KEY = "_journal_match_cache"


def _tree() -> ast.Module:
    return parse_file_cached(APP_PY)


def _parents(tree: ast.Module) -> dict:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _ancestors(node, parents):
    while node in parents:
        node = parents[node]
        yield node


def _is_session_state(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "session_state"
            and isinstance(node.value, ast.Name) and node.value.id == "st")


def _key_constants(tree, key):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == key]


def _store_lines(tree, key, parents) -> list[int]:
    """Lines of `st.session_state["<key>"] = ...` assignments."""
    lines = []
    for c in _key_constants(tree, key):
        sub = parents.get(c)
        if (isinstance(sub, ast.Subscript) and _is_session_state(sub.value)
                and isinstance(sub.ctx, ast.Store)):
            lines.append(sub.lineno)
    return lines


def _enclosing_func(node, parents):
    for a in _ancestors(node, parents):
        if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return a
    return None


def test_cache_is_written_and_read_only_at_the_home_fill_site():
    tree = _tree()
    parents = _parents(tree)
    consts = _key_constants(tree, KEY)
    assert consts, f"{KEY} not found in app.py"
    writes, reads = [], []
    for c in consts:
        p = parents.get(c)
        if (isinstance(p, ast.Subscript) and _is_session_state(p.value)
                and isinstance(p.ctx, ast.Store)):
            writes.append(c)
        elif (isinstance(p, ast.Call) and isinstance(p.func, ast.Name)
                and p.func.id == "_get_or_offline" and len(p.args) == 2
                and _is_session_state(p.args[0]) and p.args[1] is c):
            reads.append(c)
        else:
            pytest.fail(
                f"line {c.lineno}: {KEY} is touched other than by a session_state store "
                "or a _get_or_offline read (a `.get(...)`, a bare subscript or a "
                "`in session_state` test would collapse the None sentinel)")
    assert writes and reads
    drift = _store_lines(tree, "_broker_drift_cache", parents)
    assert drift, "_broker_drift_cache store not found"
    fill_start = max(drift)
    for c in writes + reads:
        assert c.lineno > fill_start, (
            f"line {c.lineno}: {KEY} must be touched only after the _broker_drift_cache "
            "store (the Home fill site)")
    span = max(c.lineno for c in writes + reads) - min(c.lineno for c in writes + reads)
    assert span < 80, f"{KEY} is touched across {span} lines, not one fill site"
    funcs = {id(_enclosing_func(c, parents)) for c in writes + reads}
    assert len(funcs) == 1, f"{KEY} is touched from more than one function / scope"
    # Never inside a signature or list the Brief / Act Today / alert synthesis read.
    for c in writes + reads:
        for a in _ancestors(c, parents):
            if isinstance(a, ast.Dict):
                pytest.fail(f"line {c.lineno}: {KEY} sits inside a dict literal "
                            "(a synth signature or alert list?)")


def test_no_stock_analyzer_module_names_the_cache():
    hits = [p.name for p in PKG.glob("*.py") if KEY in p.read_text(encoding="utf-8")]
    assert not hits, f"{KEY} named in stock_analyzer/: {hits}"


def test_brief_act_today_and_synthesis_never_import_journal_match_view():
    for name in ("act_today_view", "act_today_pointer", "act_today_precedence",
                 "daily_briefing", "home_risk_synthesis", "judgment_synthesis",
                 "entry_posture", "budget_view"):
        src = (PKG / f"{name}.py").read_text(encoding="utf-8")
        assert "journal_match_view" not in src, f"{name}.py names journal_match_view"
    # Stronger: no stock_analyzer module other than the view itself imports it
    # (U7 reading the cache later would be a deliberate, reviewed change).
    importers = [p.name for p in PKG.glob("*.py")
                 if p.name != "journal_match_view.py"
                 and "journal_match_view" in p.read_text(encoding="utf-8")]
    assert not importers, f"modules naming journal_match_view: {importers}"


def _home_notice_calls(tree):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_home_notice" and n.args
            and isinstance(n.args[0], ast.Constant) and n.args[0].value == "journalmatch"]


def test_journalmatch_home_notice_sits_inside_a_conditional_container():
    tree = _tree()
    parents = _parents(tree)
    calls = _home_notice_calls(tree)
    assert len(calls) == 1, f"expected one journalmatch _home_notice call, got {len(calls)}"
    chain = list(_ancestors(calls[0], parents))
    ifs = [a for a in chain if isinstance(a, ast.If)]
    assert ifs, "the journalmatch _home_notice call is not inside an `if`"
    # The nearest If must be the 'something to say' branch, not a skip-on-no-broker If only.
    assert any("_jm_line" in ast.dump(i.test) for i in ifs), (
        "the journalmatch _home_notice call must sit in the `if _jm_line is not None` branch")
    withs = [a for a in chain if isinstance(a, ast.With) and any(
        isinstance(it.context_expr, ast.Call)
        and isinstance(it.context_expr.func, ast.Attribute)
        and it.context_expr.func.attr == "container"
        and isinstance(it.context_expr.func.value, ast.Name)
        and it.context_expr.func.value.id == "_alert_ph_journalmatch"
        for it in a.items)]
    assert withs, "the call must sit inside `with _alert_ph_journalmatch.container():`"
    # The `with` itself is the branch of the `_jm_line` conditional, never a bare
    # statement of the fill site's own body.
    assert isinstance(parents[withs[0]], ast.If) and "_jm_line" in ast.dump(parents[withs[0]].test)


def test_placeholder_declared_right_after_the_drift_placeholder():
    tree = _tree()
    parents = _parents(tree)

    def _is_assign_to(stmt, name):
        return (isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets))

    drift = [n for n in ast.walk(tree) if _is_assign_to(n, "_alert_ph_drift")]
    assert len(drift) == 1
    body = parents[drift[0]].body
    i = body.index(drift[0])
    assert i + 1 < len(body) and _is_assign_to(body[i + 1], "_alert_ph_journalmatch"), (
        "`_alert_ph_journalmatch = st.empty()` must be the statement right after "
        "`_alert_ph_drift = st.empty()`")


def _attr_calls(tree, attr):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr]


def _under_if_naming(node, parents, name):
    return any(isinstance(a, ast.If) and name in ast.dump(a.test) for a in _ancestors(node, parents))


def test_matcher_is_judged_with_the_stamp_that_keys_the_memo():
    tree = _tree()
    parents = _parents(tree)
    keys = _attr_calls(tree, "memo_key")
    assert len(keys) == 1, "expected exactly one memo_key call in app.py"
    key_txn = keys[0].args[2]
    assert isinstance(key_txn, ast.Name)
    fill = [c for c in _attr_calls(tree, "match_journal_to_broker")
            if _under_if_naming(c, parents, "_jm_linked")]
    assert len(fill) == 1, "expected one matcher call at the Home fill site"
    third = fill[0].args[2]
    assert isinstance(third, ast.Name) and third.id == key_txn.id, (
        "the matcher must be given the same txn stamp Name passed to memo_key "
        "(never a fresh `_jm_in[\"synced\"]` read)")
    # The memo store is only reached when the matcher result is a real list.
    memo_stores = _store_lines(tree, "_journal_match_memo", parents)
    assert memo_stores
    for c in _key_constants(tree, "_journal_match_memo"):
        sub = parents.get(c)
        if (isinstance(sub, ast.Subscript) and isinstance(sub.ctx, ast.Store)):
            guards = [a for a in _ancestors(sub, parents) if isinstance(a, ast.If)
                      and "isinstance" in ast.dump(a.test) and "_jm_found" in ast.dump(a.test)
                      and "id='list'" in ast.dump(a.test)]
            assert guards, "the memo store must sit under `if isinstance(<result>, list)`"
            # and in the body, not the orelse, of that guard
            assert any(sub in list(ast.walk(ast.Module(body=g.body, type_ignores=[])))
                       for g in guards)


def test_fill_site_skips_without_a_linked_broker_and_never_uses_unsafe_html():
    src = APP_PY.read_text(encoding="utf-8")
    start = src.index("# ── TJDate U8c: Journal vs Robinhood")
    end = src.index("# Publish Tier-B to session_state", start)
    block = src[start:end]
    assert "unsafe_allow_html" not in block
    tree = _tree()
    parents = _parents(tree)
    # The flag comes from snaptrade_client.has_snaptrade().
    flag_assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "_jm_linked" for t in n.targets)
                    and "has_snaptrade" in ast.dump(n.value)
                    and "snaptrade_client" in ast.dump(n.value)]
    assert flag_assigns, "_jm_linked must be set from snaptrade_client.has_snaptrade()"
    # Reads, the matcher, every cache store and the render all sit under `if _jm_linked:`.
    guarded = list(_attr_calls(tree, "match_journal_to_broker"))
    guarded = [c for c in guarded if c.lineno > flag_assigns[0].lineno
               and c.lineno < flag_assigns[0].lineno + 120]
    guarded += _attr_calls(tree, "home_line") + _home_notice_calls(tree)
    for c in _key_constants(tree, KEY):
        guarded.append(c)
    assert guarded
    for node in guarded:
        assert any(isinstance(a, ast.If) and isinstance(a.test, ast.Name)
                   and a.test.id == "_jm_linked" for a in _ancestors(node, parents)), (
            f"line {node.lineno}: not inside `if _jm_linked:`")
    # home_line is told the broker is linked only inside that branch.
    hl = _attr_calls(tree, "home_line")
    assert len(hl) == 1 and isinstance(hl[0].args[4], ast.Constant) and hl[0].args[4].value is True
