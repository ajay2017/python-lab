"""Bottom dock (docs/plans/home-dock.md, C0c): the pure CSS helper and AST pins on
app.py's wiring (app.py is never imported by a test).

C0c made the dock app-wide: one module-level ``with st.container(key=DOCK_KEY)``
in app.py, before page dispatch, holding the only ``_index_strip()`` call.
C1 gave 🏠 Home four tabs (Act Today, Notices, Live Prices, Markets): the Home
placeholders are declared inside the dock's tabs and filled by Home's code.
"""
import ast
from pathlib import Path

from stock_analyzer import home_dock as hd
from tests._ast_cache import parse_file_cached

_APP = Path(__file__).parent.parent / "app.py"
_SRC = _APP.read_text(encoding="utf-8")


def test_dock_css_shape_and_key():
    assert hd.DOCK_KEY == "home_dock"
    css = hd.dock_css()
    assert isinstance(css, str)
    assert css.startswith("<style>") and css.endswith("</style>")
    assert f".st-key-{hd.DOCK_KEY}" in css
    assert "#131722" in css and "1px solid #31333f" in css
    assert "max-height: 45vh" in css and "overflow: auto" in css


def test_sticky_css():
    css = hd.dock_css()
    assert "position: sticky" in css
    assert "order: 9999" in css
    assert "position: fixed" not in css
    # no blank band under the dock at the end of the page (C0 probe finding)
    assert '[data-testid="stMainBlockContainer"] { padding-bottom: 0; }' in css
    # a page shorter than the screen still has the dock at the bottom edge
    assert "margin-top: auto" in css
    assert (
        '[data-testid="stVerticalBlock"]:has(> :is(.st-key-home_dock, '
        ':has(> .st-key-home_dock))) { min-height: calc(100dvh - 6rem); }'
    ) in css


def test_probe_switch_is_gone():
    for name in ("DOCK_MODES", "DEFAULT_DOCK_MODE", "dock_mode", "_FIXED_CSS"):
        assert not hasattr(hd, name), name


def _is_dock_container_call(node) -> bool:
    """`st.container(key=home_dock.DOCK_KEY)`."""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "container"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
    ):
        return False
    for kw in node.keywords:
        if (
            kw.arg == "key"
            and isinstance(kw.value, ast.Attribute)
            and kw.value.attr == "DOCK_KEY"
            and isinstance(kw.value.value, ast.Name)
            and kw.value.value.id == "home_dock"
        ):
            return True
    return False


def _is_index_strip_call(node) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_index_strip"
    )


def test_app_has_one_module_level_dock_container():
    tree = parse_file_cached(_APP)
    all_dock = [n for n in ast.walk(tree) if _is_dock_container_call(n)]
    assert len(all_dock) == 1, f"expected one dock container in app.py, found {len(all_dock)}"
    top_level = [
        stmt for stmt in tree.body
        if isinstance(stmt, ast.With)
        and any(_is_dock_container_call(item.context_expr) for item in stmt.items)
    ]
    assert len(top_level) == 1, (
        "the dock container must be a module-level `with`, not nested in a page branch"
    )


def test_app_has_one_index_strip_call_inside_the_dock():
    tree = parse_file_cached(_APP)
    calls = [n for n in ast.walk(tree) if _is_index_strip_call(n)]
    assert len(calls) == 1, f"expected exactly one _index_strip() call, found {len(calls)}"
    dock_with = next(
        stmt for stmt in tree.body
        if isinstance(stmt, ast.With)
        and any(_is_dock_container_call(item.context_expr) for item in stmt.items)
    )
    inside = [n for n in ast.walk(dock_with) if _is_index_strip_call(n)]
    assert inside == [calls[0]]


def test_app_has_no_dock_query_switch():
    assert 'st.query_params.get("dock")' not in _SRC
    assert "dock_mode" not in _SRC


# ── C1: pure helpers ─────────────────────────────────────────────────────────

def test_home_dock_has_four_tabs_act_today_first_markets_last():
    tabs = hd.dock_tabs(True)
    assert tabs == ("🔴 Act Today", "⚠️ Notices", "💹 Live Prices", "📈 Markets")
    assert tabs[0] == "🔴 Act Today" and tabs[-1] == "📈 Markets"


def test_other_pages_keep_the_markets_only_dock():
    assert hd.dock_tabs(False) == ("📈 Markets",)
    assert hd.dock_title(False) == "📈 Markets"


def test_home_title_names_every_tab_and_differs_from_other_pages():
    assert hd.dock_title(True) == "🏠 Act Today · Notices · Live Prices · Markets"
    assert hd.dock_title(True) != hd.dock_title(False)


def test_home_and_other_keys_differ():
    assert hd.dock_tabs_key(True) != hd.dock_tabs_key(False)
    assert hd.dock_expander_key(True) != hd.dock_expander_key(False)
    assert hd.dock_tabs_key(False) == hd.DOCK_TABS_KEY
    assert hd.dock_expander_key(False) == hd.DOCK_EXPANDER_KEY
    assert hd.dock_tabs_key(True) == hd.home_tabs_key(0)
    assert hd.dock_expander_key(True) == hd.HOME_DOCK_EXPANDER_KEY


def test_tab_labels_carry_no_numbers():
    # Labels are fixed when st.tabs is called, before Home computes any count.
    for label in hd.HOME_DOCK_TABS + hd.OTHER_DOCK_TABS:
        assert not any(ch.isdigit() for ch in label), label


# ── C1: AST pins on app.py's dock wiring ─────────────────────────────────────

def _dock_with(tree):
    return next(
        stmt for stmt in tree.body
        if isinstance(stmt, ast.With)
        and any(_is_dock_container_call(item.context_expr) for item in stmt.items)
    )


def _assigned_names(tree):
    """{name: [Assign nodes]} for every simple-name assignment in the tree."""
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out.setdefault(t.id, []).append(n)
    return out


def _with_named(dock_with, name):
    """The `with <name>:` node inside the dock."""
    hits = [
        n for n in ast.walk(dock_with)
        if isinstance(n, ast.With)
        and any(isinstance(i.context_expr, ast.Name) and i.context_expr.id == name
                for i in n.items)
    ]
    assert len(hits) == 1, f"expected one `with {name}:` in the dock, found {len(hits)}"
    return hits[0]


def _is_name_assigned_in(node, name):
    return any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
        for n in ast.walk(node)
    )


def test_home_placeholders_are_assigned_only_inside_the_dock():
    tree = parse_file_cached(_APP)
    dock = _dock_with(tree)
    dock_nodes = {id(n) for n in ast.walk(dock)}
    names = _assigned_names(tree)
    alert_names = [n for n in names if n.startswith("_alert_ph_")]
    assert len(alert_names) == 17
    wanted = alert_names + [
        "_act_ptr_ph", "_notices_summary_ph", "_notices_tab_summary_ph", "_dock_pane_prices",
    ]
    for name in wanted:
        assert name in names, f"{name} is never assigned in app.py"
        for node in names[name]:
            assert id(node) in dock_nodes, (
                f"{name} is assigned outside the module-level dock at app.py:{node.lineno}"
            )


def test_act_tab_holds_the_pointer_and_summary_and_notices_tab_the_alerts():
    tree = parse_file_cached(_APP)
    dock = _dock_with(tree)
    act = _with_named(dock, "_dock_tab_act")
    notices = _with_named(dock, "_dock_tab_notices")
    prices = _with_named(dock, "_dock_tab_prices")
    assert _is_name_assigned_in(act, "_act_ptr_ph")
    assert _is_name_assigned_in(act, "_notices_summary_ph")
    assert not _is_name_assigned_in(act, "_notices_tab_summary_ph")
    assert _is_name_assigned_in(notices, "_notices_tab_summary_ph")
    for name in sorted(n for n in _assigned_names(tree) if n.startswith("_alert_ph_")):
        assert _is_name_assigned_in(notices, name), f"{name} is not in the Notices tab"
        assert not _is_name_assigned_in(act, name)
    assert _is_name_assigned_in(prices, "_dock_pane_prices")


def test_pointer_is_declared_before_the_summary_in_the_act_tab():
    tree = parse_file_cached(_APP)
    act = _with_named(_dock_with(tree), "_dock_tab_act")
    names = _assigned_names(act)
    assert names["_act_ptr_ph"][0].lineno < names["_notices_summary_ph"][0].lineno


def test_notices_tab_ends_with_a_descriptive_caption_not_an_all_clear():
    tree = parse_file_cached(_APP)
    notices = _with_named(_dock_with(tree), "_dock_tab_notices")
    consts = [
        n.value for n in ast.walk(notices)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    assert "Notices from this Home run show here." in consts
    for c in consts:
        low = c.lower()
        assert "all clear" not in low and "no notices" not in low, c


def test_price_strip_is_called_inside_the_prices_pane():
    tree = parse_file_cached(_APP)
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_price_strip"
    ]
    assert len(calls) == 1
    panes = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.With)
        and any(isinstance(i.context_expr, ast.Name) and i.context_expr.id == "_dock_pane_prices"
                for i in n.items)
    ]
    assert len(panes) == 1
    assert calls[0] in list(ast.walk(panes[0]))


def test_old_expander_keys_are_gone():
    assert "_home_alerts_exp" not in _SRC
    assert "_home_price_strip_exp" not in _SRC


def test_home_notice_writes_both_summary_placeholders():
    tree = parse_file_cached(_APP)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_home_notice"
    )
    used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert {"_notices_summary_ph", "_notices_tab_summary_ph"} <= used


# ── C1 review fixes ──────────────────────────────────────────────────────────

def test_home_tabs_key_is_fresh_per_run_and_other_pages_constant():
    assert hd.home_tabs_key(1) != hd.home_tabs_key(2)
    assert hd.home_tabs_key(7) == hd.home_tabs_key(7)
    assert hd.HOME_DOCK_TABS_KEY in hd.home_tabs_key(3)
    assert hd.dock_tabs_key(True, run_n=1) != hd.dock_tabs_key(True, run_n=2)
    assert hd.dock_tabs_key(True, run_n=4) == hd.home_tabs_key(4)
    assert hd.dock_tabs_key(False, run_n=1) == hd.dock_tabs_key(False, run_n=99) == hd.DOCK_TABS_KEY
    # a Home key never equals the one-tab dock's key
    assert hd.home_tabs_key(0) != hd.DOCK_TABS_KEY


def _dock_tabs_call(dock):
    calls = [
        n for n in ast.walk(dock)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "tabs" and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "st"
    ]
    assert len(calls) == 1
    return calls[0]


def test_dock_tabs_key_carries_a_run_counter_not_a_constant():
    tree = parse_file_cached(_APP)
    call = _dock_tabs_call(_dock_with(tree))
    key = next(kw.value for kw in call.keywords if kw.arg == "key")
    assert isinstance(key, ast.Call), "the dock's tabs key must be computed per run"
    assert isinstance(key.func, ast.Attribute) and key.func.attr == "dock_tabs_key"
    assert any(kw.arg == "run_n" for kw in key.keywords), "dock_tabs_key must get run_n="
    # the expander key stays stable (folding persists): no run counter in it
    exp_keys = [
        kw.value for n in ast.walk(_dock_with(tree))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "expander"
        for kw in n.keywords if kw.arg == "key"
    ]
    assert len(exp_keys) == 1 and "run_n" not in ast.dump(exp_keys[0])


def test_run_counter_is_bumped_at_module_level_before_the_dock():
    tree = parse_file_cached(_APP)
    dock = _dock_with(tree)
    bumps = [
        stmt for stmt in tree.body
        if isinstance(stmt, ast.If) and '_home_dock_run_n' in ast.dump(stmt)
        and "_dock_on_home" in ast.dump(stmt.test)
    ]
    assert len(bumps) == 1, "expected one module-level `if _dock_on_home:` counter bump"
    assert bumps[0].lineno < dock.lineno
    assert any(isinstance(s, ast.Assign) and s.targets[0].id == "_dock_on_home"
               for s in tree.body if isinstance(s, ast.Assign) and isinstance(s.targets[0], ast.Name)
               and s.lineno < bumps[0].lineno)
    # The bump must really add 1 (a constant here would freeze the key and
    # bring back the sticky-tab bug while the presence checks above pass).
    on_home = [
        s for s in tree.body
        if isinstance(s, ast.Assign) and isinstance(s.targets[0], ast.Name)
        and s.targets[0].id == "_dock_on_home"
    ]
    assert ast.unparse(on_home[0].value) == "page == '🏠 Home'"
    bump_assigns = [s for s in bumps[0].body if isinstance(s, ast.Assign)]
    assert len(bump_assigns) == 1
    value = bump_assigns[0].value
    assert isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add)
    assert isinstance(value.right, ast.Constant) and value.right.value == 1
    assert "_home_dock_run_n" in ast.unparse(bump_assigns[0].targets[0])
    assert "_home_dock_run_n" in ast.unparse(value.left)


def test_tab_unpack_target_order_is_pinned():
    tree = parse_file_cached(_APP)
    dock = _dock_with(tree)
    unpacks = [
        n for n in ast.walk(dock)
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Tuple)
        and [e.id for e in n.targets[0].elts if isinstance(e, ast.Name)]
        == ["_dock_tab_act", "_dock_tab_notices", "_dock_tab_prices"]
    ]
    assert len(unpacks) == 1
    src = ast.unparse(unpacks[0].value)
    assert src == "_dock_tabs[:3]"


def test_notices_tab_summary_is_assigned_before_the_first_alert_placeholder():
    tree = parse_file_cached(_APP)
    notices = _with_named(_dock_with(tree), "_dock_tab_notices")
    names = _assigned_names(notices)
    first_alert = min(
        n.lineno for k, v in names.items() if k.startswith("_alert_ph_") for n in v
    )
    assert names["_notices_tab_summary_ph"][0].lineno < first_alert


def _home_branch(tree):
    return next(
        n for n in tree.body
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
        and isinstance(n.test.left, ast.Name) and n.test.left.id == "page"
        and isinstance(n.test.ops[0], ast.Eq)
        and isinstance(n.test.comparators[0], ast.Constant)
        and n.test.comparators[0].value == "🏠 Home"
    )


def test_dock_placeholder_uses_outside_the_dock_sit_inside_the_home_branch():
    tree = parse_file_cached(_APP)
    dock_ids = {id(n) for n in ast.walk(_dock_with(tree))}
    home_ids = {id(n) for stmt in _home_branch(tree).body for n in ast.walk(stmt)}
    names = {n for n in _assigned_names(tree) if n.startswith("_alert_ph_")}
    names |= {"_act_ptr_ph", "_notices_summary_ph", "_notices_tab_summary_ph", "_dock_pane_prices"}
    uses = 0
    for n in ast.walk(tree):
        if id(n) in dock_ids:
            continue
        if isinstance(n, ast.Name) and n.id in names and isinstance(n.ctx, ast.Load):
            uses += 1
            assert id(n) in home_ids, (
                f"{n.id} is used outside the dock and outside Home's branch at app.py:{n.lineno}"
            )
    assert uses > 20, "expected the Home fill sites to be found"
