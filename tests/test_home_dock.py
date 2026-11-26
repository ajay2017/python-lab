"""Bottom dock (docs/plans/home-dock.md, C0c): the pure CSS helper and AST pins on
app.py's wiring (app.py is never imported by a test).

C0c made the dock app-wide: one module-level ``with st.container(key=DOCK_KEY)``
in app.py, before page dispatch, holding the only ``_index_strip()`` call.
C1 gave 🏠 Home tabs (C1c retired Act Today, leaving Notices, Live Prices, Markets): the Home
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
    assert "padding-bottom: 0; }" in css
    # a page shorter than the screen still has the dock at the bottom edge
    assert "margin-top: auto" in css
    assert (
        '[data-testid="stVerticalBlock"]:has(> :is(.st-key-home_dock, '
        ':has(> .st-key-home_dock))) { min-height: calc(100dvh - 4.25rem); }'
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

def test_home_dock_has_three_tabs_notices_first_markets_last():
    tabs = hd.dock_tabs(True)
    assert tabs == ("⚠️ Notices", "💹 Live Prices", "📈 Markets")
    assert tabs[0] == "⚠️ Notices" and tabs[-1] == "📈 Markets"
    assert not any("Act Today" in t for t in tabs)


def test_other_pages_have_the_same_three_tabs():
    # C1b: Notices, Live Prices and Markets on every page.
    assert hd.dock_tabs(False) == hd.dock_tabs(True) == (
        "⚠️ Notices", "💹 Live Prices", "📈 Markets")


def test_title_bar_is_one_word_that_repeats_no_tab_label():
    # Owner 2026-11-19: the title bar only folds the dock; naming the tabs in it
    # duplicated the tab row right under it.
    for on_home in (True, False):
        title = hd.dock_title(on_home)
        assert title == hd.DOCK_TITLE == "Dock"
        for label in hd.dock_tabs(on_home):
            assert label.split(" ", 1)[-1] not in title


def test_title_bar_css_is_slim_and_scoped_to_the_dock():
    css = hd.dock_css()
    assert '.st-key-home_dock [data-testid="stExpander"] summary ' in css
    assert "padding: 2px 10px" in css
    # never an unscoped expander rule (it would restyle every expander in the app)
    assert '\n[data-testid="stExpander"]' not in css and '} [data-testid="stExpander"]' not in css
    assert "<" not in css.removeprefix("<style>").removesuffix("</style>")


def test_home_and_other_keys_differ():
    assert hd.dock_tabs_key(True) != hd.dock_tabs_key(False)
    assert hd.dock_expander_key(True) != hd.dock_expander_key(False)
    assert hd.dock_tabs_key(False) == hd.other_tabs_key(0)
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


def _home_branch_of_dock(dock_with):
    """The dock's `if _dock_on_home:` branch (Home's own tab panes)."""
    hits = [
        n for n in ast.walk(dock_with)
        if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "_dock_on_home"
    ]
    assert len(hits) == 1
    return hits[0]


def _with_named(dock_with, name):
    """The `with <name>:` node inside Home's branch of the dock."""
    home_if = ast.Module(body=_home_branch_of_dock(dock_with).body, type_ignores=[])
    hits = [
        n for n in ast.walk(home_if)
        if isinstance(n, ast.With)
        and any(isinstance(i.context_expr, ast.Name) and i.context_expr.id == name
                for i in n.items)
    ]
    assert len(hits) == 1, f"expected one `with {name}:` in Home's dock branch, found {len(hits)}"
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
    assert len(alert_names) == 18
    wanted = alert_names + ["_notices_summary_ph", "_dock_pane_prices"]
    for name in wanted:
        assert name in names, f"{name} is never assigned in app.py"
        for node in names[name]:
            assert id(node) in dock_nodes, (
                f"{name} is assigned outside the module-level dock at app.py:{node.lineno}"
            )


def test_notices_tab_holds_the_summary_and_every_alert_placeholder():
    tree = parse_file_cached(_APP)
    dock = _dock_with(tree)
    notices = _with_named(dock, "_dock_tab_notices")
    prices = _with_named(dock, "_dock_tab_prices")
    assert _is_name_assigned_in(notices, "_notices_summary_ph")
    for name in sorted(n for n in _assigned_names(tree) if n.startswith("_alert_ph_")):
        assert _is_name_assigned_in(notices, name), f"{name} is not in the Notices tab"
    assert _is_name_assigned_in(prices, "_dock_pane_prices")
    # one summary placeholder only (the Act Today tab's copy went with the tab)
    assert len(_assigned_names(tree)["_notices_summary_ph"]) == 1
    assert "_dock_tab_act" not in _SRC


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


def _price_strip_calls(tree):
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_price_strip"
    ]


def test_price_strip_has_exactly_two_exclusive_call_paths():
    """One in Home's `_dock_pane_prices` pane and one in the dock's other-page pane
    function; Home never calls the dock function (so one call per run)."""
    tree = parse_file_cached(_APP)
    calls = _price_strip_calls(tree)
    assert len(calls) == 2
    panes = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.With)
        and any(isinstance(i.context_expr, ast.Name) and i.context_expr.id == "_dock_pane_prices"
                for i in n.items)
    ]
    assert len(panes) == 1
    in_pane = [c for c in calls if c in list(ast.walk(panes[0]))]
    assert len(in_pane) == 1
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_render_dock_live_prices")
    in_fn = [c for c in calls if c in list(ast.walk(fn))]
    assert len(in_fn) == 1
    # the dock function is called only from the dock's non-Home branch
    dock = _dock_with(tree)
    home_if = _home_branch_of_dock(dock)
    callers = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "_render_dock_live_prices"
    ]
    assert len(callers) == 1
    assert callers[0] in list(ast.walk(ast.Module(body=home_if.orelse, type_ignores=[])))


def test_price_strip_is_a_module_level_fragment_defined_before_the_dock():
    tree = parse_file_cached(_APP)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_price_strip"]
    assert len(fns) == 1, "_price_strip must be defined once, at module level"
    assert fns[0].lineno < _dock_with(tree).lineno
    deco = ast.unparse(fns[0].decorator_list[0])
    assert deco == "st.fragment(run_every=60)"
    # and not also defined inside Home's branch
    home = _home_branch(tree)
    assert not any(isinstance(n, ast.FunctionDef) and n.name == "_price_strip"
                   for stmt in home.body for n in ast.walk(stmt))


def test_old_expander_keys_are_gone():
    assert "_home_alerts_exp" not in _SRC
    assert "_home_price_strip_exp" not in _SRC


def test_home_notice_writes_the_one_summary_placeholder():
    tree = parse_file_cached(_APP)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_home_notice"
    )
    used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "_notices_summary_ph" in used
    assert "_notices_tab_summary_ph" not in _SRC
    # no pointer hint is appended any more: the line is already in the Notices tab
    consts = [n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any("→ ⚠️ Notices tab" in c for c in consts)


# ── C1 review fixes ──────────────────────────────────────────────────────────

def test_tabs_key_is_fresh_per_run_on_every_page():
    assert hd.home_tabs_key(1) != hd.home_tabs_key(2)
    assert hd.home_tabs_key(7) == hd.home_tabs_key(7)
    assert hd.HOME_DOCK_TABS_KEY in hd.home_tabs_key(3)
    assert hd.dock_tabs_key(True, run_n=1) != hd.dock_tabs_key(True, run_n=2)
    assert hd.dock_tabs_key(True, run_n=4) == hd.home_tabs_key(4)
    # C1b: the other pages' key is per full run too, with its own prefix
    assert hd.dock_tabs_key(False, run_n=1) != hd.dock_tabs_key(False, run_n=2)
    assert hd.DOCK_TABS_KEY in hd.dock_tabs_key(False, run_n=5)
    # a Home key never equals another page's key, at any run number
    for n in range(4):
        for m in range(4):
            assert hd.home_tabs_key(n) != hd.other_tabs_key(m)


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


def test_run_counter_is_bumped_at_module_level_on_every_page_before_the_dock():
    tree = parse_file_cached(_APP)
    dock = _dock_with(tree)
    bumps = [
        stmt for stmt in tree.body
        if isinstance(stmt, ast.Assign) and stmt.lineno < dock.lineno
        and "_home_dock_run_n" in ast.unparse(stmt.targets[0])
    ]
    assert len(bumps) == 1, "expected one unconditional module-level counter bump"
    # The bump must really add 1 (a constant here would freeze the key and
    # bring back the sticky-tab bug while the presence checks pass).
    value = bumps[0].value
    assert isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add)
    assert isinstance(value.right, ast.Constant) and value.right.value == 1
    assert "_home_dock_run_n" in ast.unparse(value.left)
    # not inside an `if page ==` guard: it runs on every page
    assert not any(isinstance(st_, ast.If) and bumps[0] in list(ast.walk(st_)) for st_ in tree.body)
    on_home = [
        s_ for s_ in tree.body
        if isinstance(s_, ast.Assign) and isinstance(s_.targets[0], ast.Name)
        and s_.targets[0].id == "_dock_on_home"
    ]
    assert ast.unparse(on_home[0].value) == "page == '🏠 Home'"


def test_tab_unpack_target_order_is_pinned():
    tree = parse_file_cached(_APP)
    dock = _dock_with(tree)
    unpacks = [
        n for n in ast.walk(dock)
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Tuple)
        and [e.id for e in n.targets[0].elts if isinstance(e, ast.Name)]
        == ["_dock_tab_notices", "_dock_tab_prices", "_dock_tab_markets"]
    ]
    assert len(unpacks) == 1
    assert ast.unparse(unpacks[0].value) == "_dock_tabs"


def test_notices_tab_summary_is_assigned_before_the_first_alert_placeholder():
    tree = parse_file_cached(_APP)
    notices = _with_named(_dock_with(tree), "_dock_tab_notices")
    names = _assigned_names(notices)
    first_alert = min(
        n.lineno for k, v in names.items() if k.startswith("_alert_ph_") for n in v
    )
    assert names["_notices_summary_ph"][0].lineno < first_alert


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
    names |= {"_notices_summary_ph", "_dock_pane_prices"}
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


# ── C1c: top padding and the style-only elements above the first card ────────

def test_top_pad_constant_is_header_plus_a_small_gap():
    assert hd._HEADER_HEIGHT_REM == 3.75
    assert hd._TOP_PAD_REM > hd._HEADER_HEIGHT_REM, "content must never sit under the header"
    assert hd._TOP_PAD_REM - hd._HEADER_HEIGHT_REM <= 1.0


def test_top_pad_is_used_in_both_rules_and_is_not_zero():
    css = hd.dock_css()
    pad = f"{hd._TOP_PAD_REM:g}rem"
    assert f'[data-testid="stMainBlockContainer"] {{ padding-top: {pad}; ' in css
    assert f"min-height: calc(100dvh - {pad})" in css
    assert "padding-top: 0;" not in css and "padding-top: 0rem" not in css
    assert "6rem" not in css


def test_dock_css_has_no_tag_like_text_inside_the_style():
    # Streamlit's st.html sanitizer (DOMPurify 3.4.0, SAFE_FOR_XML) drops a style
    # element whose text contains "<" + a letter, "/" or "!".
    import re
    css = hd.dock_css()
    inner = css[len("<style>"):-len("</style>")]
    assert not re.search(r"<[/\w!]", inner)


def _st_html_string_args(tree):
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "html" and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "st" and n.args
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
            out.append(n.args[0].value)
    return out


def test_global_theme_is_style_only_st_html_without_tag_text_inside():
    import re
    tree = parse_file_cached(_APP)
    htmls = _st_html_string_args(tree)
    themes = [h for h in htmls if "Inter:wght" in h]
    assert len(themes) == 1, "the global theme must be one st.html call"
    body = themes[0].strip()
    assert body.startswith("<style>") and body.endswith("</style>")
    inner = body[len("<style>"):-len("</style>")]
    assert not re.search(r"<[/\w!]", inner), "a tag-like string inside the CSS makes st.html drop it"
    assert "</style>" not in inner


def test_dock_css_is_emitted_with_st_html():
    tree = parse_file_cached(_APP)
    hits = [
        n for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "html"
        and n.value.args and isinstance(n.value.args[0], ast.Call)
        and isinstance(n.value.args[0].func, ast.Attribute)
        and n.value.args[0].func.attr == "dock_css"
    ]
    assert len(hits) == 1, "expected one module-level st.html(home_dock.dock_css())"
    assert "st.markdown(home_dock.dock_css()" not in _SRC


# ── C1b: the digest on every other page ──────────────────────────────────────

def _fn(tree, name):
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_other_pages_dock_branch_calls_both_panes():
    tree = parse_file_cached(_APP)
    other = ast.Module(body=_home_branch_of_dock(_dock_with(tree)).orelse, type_ignores=[])
    called = {n.func.id for n in ast.walk(other)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert {"_render_dock_digest", "_render_dock_live_prices"} <= called
    withs = {i.context_expr.id for n in ast.walk(other) if isinstance(n, ast.With)
             for i in n.items if isinstance(i.context_expr, ast.Name)}
    assert {"_dock_tab_notices", "_dock_tab_prices"} <= withs


def test_digest_pane_reads_with_get_or_offline_and_navigates_via_pending_page():
    tree = parse_file_cached(_APP)
    fn = _fn(tree, "_render_dock_digest")
    src = ast.unparse(fn)
    assert "_get_or_offline(st.session_state, '_home_dock_digest')" in src
    assert "coord_freshness.classify(" in src
    assert "home_dock_digest.digest_line(" in src
    assert "st.session_state['_pending_page'] = '🏠 Home'" in src
    assert "nav_page" not in src
    assert "key='_dock_open_home'" in src
    # the digest key is never read with a bare .get(...) / `in session_state`
    assert ".get('_home_dock_digest'" not in src and "in st.session_state" not in src
    # a failure never breaks the page
    assert any(isinstance(n, ast.Try) for n in ast.walk(fn))
    consts = [n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert "Home's last check couldn't be read here — open 🏠 Home." in consts
    assert "Details are in 🏠 Home's ⚠️ Notices tab." in consts


def test_live_prices_pane_reads_the_published_holdings_with_get_or_offline():
    tree = parse_file_cached(_APP)
    fn = _fn(tree, "_render_dock_live_prices")
    src = ast.unparse(fn)
    assert "_get_or_offline(st.session_state, '_last_held_tickers')" in src
    consts = [n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert "Open 🏠 Home once this session to load your holdings' prices." in consts
    assert "No holdings." in consts


# ── C1b: the Home producer of `_home_dock_digest` ───────────────────────────

def _is_publish_call(n) -> bool:
    return (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name)
            and n.value.func.id == "_publish_home_dock_digest")


def test_home_resets_the_digest_to_none_near_its_top():
    home = _home_branch(parse_file_cached(_APP))
    first = home.body[:6]
    resets = [
        st_ for st_ in first
        if isinstance(st_, ast.Assign) and "_home_dock_digest" in ast.unparse(st_.targets[0])
        and isinstance(st_.value, ast.Constant) and st_.value.value is None
    ]
    assert len(resets) == 1, "Home must set _home_dock_digest to None at its top"
    # and clears the stamp the same way Home stamps its other keys
    assert any("_stamp_coord(['_home_dock_digest'])" == ast.unparse(st_.value)
               for st_ in home.body[:8] if isinstance(st_, ast.Expr))


def test_home_publishes_before_every_st_stop_and_at_the_end():
    tree = parse_file_cached(_APP)
    home = _home_branch(tree)
    stops = []
    for node in ast.walk(ast.Module(body=home.body, type_ignores=[])):
        for field in ("body", "orelse"):
            lst = getattr(node, field, None)
            if isinstance(lst, list):
                for i, st_ in enumerate(lst):
                    if (isinstance(st_, ast.Expr) and isinstance(st_.value, ast.Call)
                            and isinstance(st_.value.func, ast.Attribute)
                            and st_.value.func.attr == "stop"
                            and isinstance(st_.value.func.value, ast.Name)
                            and st_.value.func.value.id == "st"):
                        stops.append((lst, i))
    assert len(stops) == 1, "Home has exactly one st.stop(); a new one needs a digest publish"
    lst, i = stops[0]
    branch = lst[i - 1]
    assert isinstance(branch, ast.If), "the st.stop() must follow the outage / no-holdings if/else"
    for part, expect_no_holdings in ((branch.body, False), (branch.orelse, True)):
        pubs = [n for st_ in part for n in ast.walk(st_)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_publish_home_dock_digest"]
        assert len(pubs) == 1
        assert ("no_holdings" in [kw.arg for kw in pubs[0].keywords]) == expect_no_holdings
        assert isinstance(pubs[0].args[0], ast.Constant) and pubs[0].args[0].value is None
    last = home.body[-1]
    assert _is_publish_call(last), "the last statement of Home's branch must publish the digest"
    assert ast.unparse(last.value.args[0]) == "_act_view"


def test_publish_helper_is_defined_before_its_first_call_and_stamps():
    tree = parse_file_cached(_APP)
    home = _home_branch(tree)
    fn = next(n for n in home.body if isinstance(n, ast.FunctionDef)
              and n.name == "_publish_home_dock_digest")
    first_call = min(n.lineno for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                     and n.func.id == "_publish_home_dock_digest")
    assert fn.lineno < first_call
    src = ast.unparse(fn)
    assert "home_dock_digest.build_digest(" in src
    assert "_stamp_coord(['_home_dock_digest'])" in src
    # a failure publishes None, never a partial answer
    assert "st.session_state['_home_dock_digest'] = None" in src


def test_digest_key_is_registered_for_freshness():
    from stock_analyzer import coord_freshness as cf
    meta = cf.PORTFOLIO_DEPENDENT_KEYS["_home_dock_digest"]
    assert meta["producer"] == "home" and meta["tier"] == cf.TIER_DECISION
    assert "_home_dock_digest" in cf.keys_for_producer("home")
    assert "_home_dock_digest" in cf.SURFACE_KEYS["dock"]
