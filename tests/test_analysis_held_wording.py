"""S-D C4 (00)/(0), seen live 2026-10-20: two Analysis sentences that
contradicted the page for a held name.

(00) the "Already held" box advised "consider reduced add-on size" right above
"➕ No add call today" -- the advice must depend on the held-add verdict.
(0) a held name 🏠 Home couldn't price, on a Sell verdict, read "Not in your
portfolio — nothing to sell" -- an unpriced holding IS held.
"""
import ast
from pathlib import Path

from tests._ast_cache import parse_file_cached

_APP = Path(__file__).resolve().parent.parent / "app.py"


def _str_consts(node):
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def test_add_on_earnings_advice_only_on_an_add_call():
    tree = parse_file_cached(_APP)
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.IfExp)
            and any("consider reduced add-on size" in s for s in _str_consts(n.body))]
    assert len(hits) == 1, "the add-on advice must sit in exactly one conditional"
    assert isinstance(hits[0].test, ast.Name) and hits[0].test.id == "_earn_is_add"
    assert not any("add-on" in s for s in _str_consts(hits[0].orelse))
    # and the flag is the held-add verdict's state, not something else
    flag = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_earn_is_add" for t in n.targets)]
    assert len(flag) == 1
    cmp = [c for c in ast.walk(flag[0].value) if isinstance(c, ast.Compare)]
    assert len(cmp) == 1 and isinstance(cmp[0].ops[0], ast.Eq)
    assert "add" in _str_consts(cmp[0]) and "state" in _str_consts(cmp[0])
    # no other unconditional copy of the advice anywhere in app.py
    assert sum("consider reduced add-on size" in s for s in _str_consts(tree)) == 1


def test_unpriced_holding_never_reads_nothing_to_sell():
    tree = parse_file_cached(_APP)
    ifs = [n for n in ast.walk(tree) if isinstance(n, ast.If)
           and any("nothing to sell" in s for s in _str_consts(n))
           and isinstance(n.test, ast.Compare) and isinstance(n.test.ops[0], ast.In)
           and _names(n.test.comparators[0]) == {"_an_unpriced"}]
    assert len(ifs) == 1, "the 'nothing to sell' caption must be gated on _an_unpriced"
    node = ifs[0]
    assert not any("nothing to sell" in s for s in _str_consts(ast.Module(body=node.body, type_ignores=[])))
    assert any("Trade Journal" in s for s in _str_consts(ast.Module(body=node.body, type_ignores=[])))
    assert any("nothing to sell" in s for s in _str_consts(ast.Module(body=node.orelse, type_ignores=[])))
    assert sum("nothing to sell" in s for s in _str_consts(tree)) == 1
