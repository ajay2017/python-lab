"""RecTime C2: every writer of a `trades` row sets `fill_time_typed` (source / AST
pins: no test imports app.py). Pins BOTH ends: the writer set (every `save_trade`
call site and every trades insert) AND the value each one stores.

True only when the owner typed a fill time in the Trade Journal; every import,
the broker prefill and the SPLIT row store False (docs/plans/fill-time-typed.md S4).
"""
import ast
from pathlib import Path

from tests._ast_cache import parse_file_cached

_ROOT = Path(__file__).resolve().parent.parent
_APP = _ROOT / "app.py"
_COL = "fill_time_typed"

# The staged journal record reaches db.save_trade through session state.
_STAGED = {"_pending_sell": "record", "_pending_buy": "record"}


def _app_tree():
    return parse_file_cached(_APP)


def _app_text():
    return _APP.read_text(encoding="utf-8")


def _save_trade_calls(tree):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "save_trade"]


def _dict_assigns(tree, name):
    """Every dict literal assigned to the plain name `name`."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict):
            if any(isinstance(t, ast.Name) and t.id == name for t in n.targets):
                out.append(n.value)
    return out


def _keys(d):
    return {k.value for k in d.keys if isinstance(k, ast.Constant)}


def _col_value(d):
    for k, v in zip(d.keys, d.values):
        if isinstance(k, ast.Constant) and k.value == _COL:
            return ast.unparse(v)
    return None


def _record_col_assigns(tree):
    """`record["fill_time_typed"] = <value>` statements (the journal staging)."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1:
            t = n.targets[0]
            if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                    and t.value.id == "record" and isinstance(t.slice, ast.Constant)
                    and t.slice.value == _COL):
                out.append(n)
    return out


def test_app_has_exactly_five_save_trade_call_sites():
    """A new writer must be classified here (and set the column) before it ships."""
    assert len(_save_trade_calls(_app_tree())) == 5


def test_every_save_trade_call_site_passes_a_record_carrying_the_column():
    tree = _app_tree()
    for call in _save_trade_calls(tree):
        assert len(call.args) == 1, ast.unparse(call)
        arg = call.args[0]
        if isinstance(arg, ast.Dict):                       # the SPLIT row, inline
            assert _COL in _keys(arg), ast.unparse(call)
            assert _col_value(arg) == "False"
        else:
            assert isinstance(arg, ast.Name), ast.unparse(call)
            name = _STAGED.get(arg.id, arg.id)
            if name == "record":                            # the journal's staged record
                continue
            lits = _dict_assigns(tree, name)                # CSV / RH text import rows
            assert lits, f"{name} is not a dict literal assignment"
            for lit in lits:
                assert _COL in _keys(lit), f"{name} lacks {_COL}"
                assert _col_value(lit) == "False", name


def test_staged_pending_records_are_the_same_record_object():
    """The save sites read `_pending_*`; that must be the staged `record`."""
    src = _app_text()
    assert 'st.session_state["_tj_pending_sell"] = record' in src
    assert 'st.session_state["_tj_pending_buy"] = record' in src
    assert "_pending_sell = st.session_state" in src
    assert "_pending_buy = st.session_state" in src


def test_journal_record_sets_the_column_in_both_branches():
    tree = _app_tree()
    assigns = _record_col_assigns(tree)
    assert len(assigns) == 2
    by_value = {ast.unparse(a.value): a for a in assigns}
    assert set(by_value) == {"False", "_tj_ft is not None"}

    broker_if = manual_if = None
    for n in ast.walk(tree):
        if (isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                and n.test.id == "_is_broker_trade"):
            if any(x is by_value["False"] for b in n.body for x in ast.walk(b)):
                broker_if = n
            if any(x is by_value["_tj_ft is not None"] for b in n.orelse for x in ast.walk(b)):
                manual_if = n
    assert broker_if is not None, "the broker-prefill False must sit in `if _is_broker_trade:`"
    assert manual_if is not None, "the manual value must sit in its else branch"


def test_manual_value_derives_from_the_fill_time_input_not_a_constant():
    src = _app_text()
    ft = src.index('_tj_ft = st.session_state.get("_tj_fill_time")')
    col = src.index('record["fill_time_typed"] = _tj_ft is not None')
    res = src.index("trade_entry_checks.resolve_traded_at(")
    # same variable, assigned before it is used, and the one resolve_traded_at reads
    assert ft < col
    assert "_tj_ft" in src[res:res + 200]
    # the widget the variable reads is the Fill time (ET) input
    assert 'key="_tj_fill_time"' in src
    # no bare constant sneaks in
    assert 'record["fill_time_typed"] = True' not in src


def test_the_prefill_form_has_no_fill_time_input_so_false_is_right():
    src = _app_text()
    at = src.index('key="_tj_fill_time"')
    guard = src.rfind("if not _is_broker_trade:", 0, at)
    assert guard != -1 and at - guard < 600


def _trades_write_calls(tree, attrs):
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in attrs):
            chain = ast.unparse(n.func.value)
            if 'table("trades")' in chain or "table('trades')" in chain:
                out.append(n)
    return out


def test_only_db_save_trade_inserts_trades_rows():
    """Writer set, db end: no insert / upsert into `trades` outside save_trade."""
    scanned = [_ROOT / "app.py", _ROOT / "cron_runner.py"]
    scanned += sorted((_ROOT / "stock_analyzer").glob("*.py"))
    scanned += sorted((_ROOT / "scripts").glob("*.py"))
    for path in scanned:
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        if "trades" not in src:
            continue
        tree = parse_file_cached(path) if path.name == "app.py" else ast.parse(src)
        calls = _trades_write_calls(tree, {"insert", "upsert"})
        if path.name == "db.py":
            funcs = {f.name: f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)}
            inside = {id(x) for x in ast.walk(funcs["save_trade"])}
            assert calls and all(id(c) in inside for c in calls), "an insert outside save_trade"
        else:
            assert not calls, f"{path.name} writes trades outside db.save_trade"


def test_no_save_trade_call_outside_app():
    """Writer set, caller end: app.py's five sites are the only db.save_trade
    callers; a call in cron_runner / stock_analyzer / scripts must be classified
    here first (db.py's own `def save_trade` is not a call)."""
    scanned = [_ROOT / "cron_runner.py"]
    scanned += sorted((_ROOT / "stock_analyzer").glob("*.py"))
    scanned += sorted((_ROOT / "scripts").glob("*.py"))
    for path in scanned:
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        if "save_trade" not in src:
            continue
        calls = [n for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Call)
                 and ((isinstance(n.func, ast.Attribute) and n.func.attr == "save_trade")
                      or (isinstance(n.func, ast.Name) and n.func.id == "save_trade"))]
        assert not calls, f"{path.name} calls save_trade outside app.py"


def _payload_keys(func, arg):
    """Constant keys of an update payload: a dict literal, or a name built in `func`."""
    if isinstance(arg, ast.Dict):
        return _keys(arg)
    assert isinstance(arg, ast.Name), ast.unparse(arg)
    keys = set()
    for n in ast.walk(func):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == arg.id and isinstance(n.value, ast.Dict):
                    keys |= _keys(n.value)
                if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                        and t.value.id == arg.id and isinstance(t.slice, ast.Constant)):
                    keys.add(t.slice.value)
    assert keys, f"could not resolve the payload {arg.id}"
    return keys


def test_no_trades_update_can_rewrite_traded_at():
    """Edits: `traded_at` is not editable, so no trades update payload carries it."""
    path = _ROOT / "stock_analyzer" / "db.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    updates = _trades_write_calls(tree, {"update"})
    assert updates
    funcs = [f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)]
    for call in updates:
        owner = [f for f in funcs if any(x is call for x in ast.walk(f))]
        assert owner
        keys = _payload_keys(owner[-1], call.args[0])
        assert "traded_at" not in keys, owner[-1].name
        assert _COL not in keys, owner[-1].name
