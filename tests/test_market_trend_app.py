"""AST pins for the 🌐 Macro market and sector trend section (chunk C2 of
docs/plans/market-sector-trend.md). app.py is never imported (no test does), so the
wiring is pinned by AST:

  * the fetch layers and the one renderer are module-level functions;
  * the failure path: the raising fetch is wrapped by the None-returning cache;
  * the renderer compares nothing against a trend threshold and uses no raw HTML;
  * the Macro branch renders the table BEFORE the `port_df.empty` gate (owner Q10);
  * (C2b) the page is two tabs: the trend (summary line, then tables) in the first,
    the macro signals (the `port_df.empty` gate and the load button) in the second;
  * `_fetch_sector_returns` still has its readers (Home's leading sectors and
    Performance Diagnostics), since the trend section must not replace it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

_APP = Path(__file__).parent.parent / "app.py"
_TREE = parse_file_cached(_APP)


def _module_func(name: str) -> ast.FunctionDef:
    for n in _TREE.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} is not a module-level function in app.py")


def _page_branch(page: str) -> ast.If:
    """The `if page == ...` / `elif page == ...` node for one page (module level)."""
    for top in _TREE.body:
        node = top
        while isinstance(node, ast.If):
            t = node.test
            if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                    and t.left.id == "page" and len(t.comparators) == 1
                    and isinstance(t.comparators[0], ast.Constant)
                    and t.comparators[0].value == page):
                return node
            node = node.orelse[0] if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) else None
    raise AssertionError(f"no page branch for {page!r}")


def _walk_body(node: ast.If):
    for stmt in node.body:
        yield from ast.walk(stmt)


def _call_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _qualified_calls(fn: ast.AST) -> set[str]:
    """'market_trend.compute_trend' for attribute calls, bare name otherwise."""
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                out.add(f"{f.value.id}.{f.attr}")
            elif isinstance(f, ast.Name):
                out.add(f.id)
    return out


# ── fetch layers and the renderer ────────────────────────────────────────────

def test_the_three_trend_functions_are_module_level():
    for name in ("_trend_result_ok", "_cached_market_trend", "_render_trend_table",
                 "_render_trend_summary"):
        _module_func(name)


def test_trend_result_ok_reads_the_module_and_never_the_heatmap_fetch():
    calls = _qualified_calls(_module_func("_trend_result_ok"))
    assert "market_trend.trend_symbols" in calls
    assert "market_trend.compute_trend" in calls
    assert "_fetch_sector_returns" not in calls


def test_trend_result_ok_raises_so_a_failure_is_never_cached():
    fn = _module_func("_trend_result_ok")
    assert any(isinstance(n, ast.Raise) for n in ast.walk(fn))
    assert not any(isinstance(n, ast.Try) for n in ast.walk(fn)), (
        "a try/except here would turn a failure into a cacheable value")


def test_cached_market_trend_calls_the_raising_fetch_inside_a_try():
    fn = _module_func("_cached_market_trend")
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert tries, "_cached_market_trend must catch the fetch's failure"
    assert any(
        isinstance(c, ast.Call) and _call_name(c) == "_trend_result_ok"
        for t in tries for stmt in t.body for c in ast.walk(stmt)
    )
    # the except path returns None (the offline sentinel), never an empty container
    handler_returns = [r.value for t in tries for h in t.handlers
                       for r in ast.walk(h) if isinstance(r, ast.Return)]
    assert handler_returns and all(
        isinstance(v, ast.Constant) and v.value is None for v in handler_returns)


@pytest.mark.parametrize("fn_name", ["_render_trend_table", "_render_trend_summary"])
def test_trend_renderers_have_no_raw_html_and_no_threshold_names(fn_name):
    fn = _module_func(fn_name)
    for n in ast.walk(fn):
        if isinstance(n, ast.keyword):
            assert n.arg != "unsafe_allow_html"
        if isinstance(n, ast.Name):
            assert not n.id.startswith(("TREND_", "RISK_OFF_")), n.id
        if isinstance(n, ast.Attribute):
            assert not n.attr.startswith(("TREND_", "RISK_OFF_")), n.attr


# ── the Macro branch ─────────────────────────────────────────────────────────

def test_macro_branch_renders_the_table_before_the_book_gate():
    branch = _page_branch("🌐 Macro")
    renders = [n for n in _walk_body(branch)
               if isinstance(n, ast.Call) and _call_name(n) == "_render_trend_table"]
    fetches = [n for n in _walk_body(branch)
               if isinstance(n, ast.Call) and _call_name(n) == "_cached_market_trend"]
    assert renders, "the Macro page must call _render_trend_table"
    assert fetches, "the Macro page must call _cached_market_trend"
    arg_src = ast.unparse(fetches[0].args[0])
    assert "as_of_session(_now_et())" in arg_src

    gates = [n for n in _walk_body(branch)
             if isinstance(n, ast.If) and isinstance(n.test, ast.Attribute)
             and n.test.attr == "empty" and isinstance(n.test.value, ast.Name)
             and n.test.value.id == "port_df"]
    assert gates, "the Macro page's port_df.empty gate went missing"
    gate_line = min(g.lineno for g in gates)
    assert max(r.lineno for r in renders) < gate_line
    assert fetches[0].lineno < gate_line


def test_macro_branch_render_is_inside_a_try_so_a_bug_cannot_take_the_page_down():
    branch = _page_branch("🌐 Macro")
    render_calls = [n for n in _walk_body(branch)
                    if isinstance(n, ast.Call) and _call_name(n) == "_render_trend_table"]
    in_try = [
        c for t in _walk_body(branch) if isinstance(t, ast.Try)
        for b in t.body for c in ast.walk(b)
        if isinstance(c, ast.Call) and _call_name(c) == "_render_trend_table"
    ]
    assert render_calls and len(in_try) == len(render_calls)


def _assigned_sources(branch: ast.If, name: str) -> list[str]:
    return [ast.unparse(a.value) for a in _walk_body(branch)
            if isinstance(a, ast.Assign) and a.value is not None
            and any(isinstance(t, ast.Name) and t.id == name for t in a.targets)]


def test_macro_branch_weights_come_from_get_or_offline_not_a_dot_get():
    branch = _page_branch("🌐 Macro")
    held = [n for n in _walk_body(branch)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "held_weights"
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "market_trend"]
    assert len(held) == 1
    arg = held[0].args[0]
    assert isinstance(arg, ast.Name), "held_weights should receive the one-time read"
    srcs = _assigned_sources(branch, arg.id)
    assert srcs == ['_get_or_offline(st.session_state, "_port_df_enriched")'] or srcs == [
        "_get_or_offline(st.session_state, '_port_df_enriched')"], srcs
    assert ".get(" not in srcs[0]


def test_macro_branch_passes_book_empty_to_the_renderer():
    branch = _page_branch("🌐 Macro")
    renders = [n for n in _walk_body(branch)
               if isinstance(n, ast.Call) and _call_name(n) == "_render_trend_table"]
    assert renders
    for c in renders:
        assert "book_empty" in {k.arg for k in c.keywords}
    assert "book_empty" in {a.arg for a in _module_func("_render_trend_table").args.args}


def test_macro_branch_fetch_sits_inside_a_spinner():
    branch = _page_branch("🌐 Macro")
    in_spinner = [
        c for w in _walk_body(branch) if isinstance(w, ast.With)
        and any(isinstance(i.context_expr, ast.Call) and _call_name(i.context_expr) == "spinner"
                for i in w.items)
        for b in w.body for c in ast.walk(b)
        if isinstance(c, ast.Call) and _call_name(c) == "_cached_market_trend"
    ]
    assert in_spinner


def test_renderer_builds_both_frames_before_any_streamlit_call_and_dates_above_tables():
    fn = _module_func("_render_trend_table")
    st_calls = sorted(
        (n.lineno, n.func.attr) for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "st")
    frame_lines = [n.lineno for n in ast.walk(fn)
                   if isinstance(n, ast.Call) and _call_name(n) == "_frame"]
    assert len(frame_lines) == 2 and st_calls
    assert max(frame_lines) < st_calls[0][0]
    # the as-of caption is a caption before the first dataframe
    dated = [ln for ln, attr in st_calls if attr == "caption" and any(
        isinstance(c, ast.Call) and c.lineno == ln and "As of the" in ast.unparse(c)
        for c in ast.walk(fn))]
    first_table = min(ln for ln, attr in st_calls if attr == "dataframe")
    assert dated and max(dated) < first_table


# ── C2b: two tabs, the summary line, the full-height tables ──────────────────

_TREND_TAB = "📈 Market and sector trend"
_SIG_TAB = "📡 Macro signals"


def _tab_withs(branch: ast.If) -> tuple[ast.With, ast.With]:
    """The `with <tab>:` blocks of the Macro branch's st.tabs unpacking, in order."""
    unpack = [
        n for n in branch.body if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Call) and _call_name(n.value) == "tabs"
    ]
    assert len(unpack) == 1, "the Macro branch must unpack one st.tabs call"
    names = [e.id for e in unpack[0].targets[0].elts]
    assert len(names) == 2
    withs = []
    for nm in names:
        found = [w for w in branch.body if isinstance(w, ast.With) and any(
            isinstance(i.context_expr, ast.Name) and i.context_expr.id == nm
            for i in w.items)]
        assert len(found) == 1, f"no top-level `with {nm}:` block"
        withs.append(found[0])
    return withs[0], withs[1]


def _walk_with(w: ast.With):
    for stmt in w.body:
        yield from ast.walk(stmt)


def test_macro_branch_has_the_two_tabs_in_order():
    branch = _page_branch("🌐 Macro")
    tabs = [n for n in _walk_body(branch)
            if isinstance(n, ast.Call) and _call_name(n) == "tabs"]
    assert len(tabs) == 1
    arg = tabs[0].args[0]
    assert isinstance(arg, ast.List)
    assert [e.value for e in arg.elts] == [_TREND_TAB, _SIG_TAB]


def test_trend_fetch_summary_and_table_sit_in_the_first_tab_summary_first():
    branch = _page_branch("🌐 Macro")
    first, second = _tab_withs(branch)
    in_first = list(_walk_with(first))
    in_second = list(_walk_with(second))
    for name in ("_cached_market_trend", "_render_trend_summary", "_render_trend_table"):
        assert len(_calls_to(in_first, name)) == 1, name
        assert not _calls_to(in_second, name), f"{name} leaked into the signals tab"
    summary = _calls_to(in_first, "_render_trend_summary")[0]
    table = _calls_to(in_first, "_render_trend_table")[0]
    assert summary.lineno < table.lineno
    assert "book_empty" in {k.arg for k in summary.keywords}
    # both renders share one try, so a bug in either cannot take the page down
    tries = [t for t in in_first if isinstance(t, ast.Try)]
    assert any(
        {id(summary), id(table)} <= {id(c) for b in t.body for c in ast.walk(b)}
        for t in tries)


def test_the_book_gate_and_the_load_button_sit_in_the_second_tab():
    branch = _page_branch("🌐 Macro")
    first, second = _tab_withs(branch)

    def gates(nodes):
        return [n for n in nodes if isinstance(n, ast.If)
                and isinstance(n.test, ast.Attribute) and n.test.attr == "empty"
                and isinstance(n.test.value, ast.Name) and n.test.value.id == "port_df"]

    def load_buttons(nodes):
        return [n for n in nodes if isinstance(n, ast.keyword) and n.arg == "key"
                and isinstance(n.value, ast.Constant) and n.value.value == "_macro_load_btn"]

    assert gates(list(_walk_with(second))) and not gates(list(_walk_with(first)))
    assert load_buttons(list(_walk_with(second))) and not load_buttons(list(_walk_with(first)))


def test_render_trend_summary_is_display_only_and_takes_book_empty():
    fn = _module_func("_render_trend_summary")
    assert "book_empty" in {a.arg for a in fn.args.args}
    calls = _qualified_calls(fn)
    assert "market_trend.chip_groups" in calls
    assert "market_trend.table_rows" in calls
    # exactly one markdown line is drawn
    st_md = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "markdown"]
    assert len(st_md) == 1


def test_render_trend_table_dataframes_both_pass_height():
    fn = _module_func("_render_trend_table")
    frames = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute) and n.func.attr == "dataframe"]
    assert len(frames) == 2
    for c in frames:
        assert "height" in {k.arg for k in c.keywords}


# ── C3: the dock's 📈 Markets tab ────────────────────────────────────────────

def _dock_markets_with() -> ast.With:
    found = [n for n in ast.walk(_TREE) if isinstance(n, ast.With) and any(
        isinstance(i.context_expr, ast.Name) and i.context_expr.id == "_dock_tab_markets"
        for i in n.items)]
    assert len(found) == 1, "expected one `with _dock_tab_markets:` block"
    return found[0]


def _decorator_src(fn: ast.FunctionDef) -> list[str]:
    return [ast.unparse(d) for d in fn.decorator_list]


def test_index_strip_is_still_a_60s_fragment_taking_the_trend_lines_by_argument():
    fn = _module_func("_index_strip")
    assert "st.fragment(run_every=60)" in _decorator_src(fn)
    names = [a.arg for a in fn.args.args]
    assert names[:2] == ["trend_lines", "trend_note"]
    # trend_lines defaults to None (the first default of the two)
    assert isinstance(fn.args.defaults[0], ast.Constant) and fn.args.defaults[0].value is None


def test_index_strip_never_fetches_the_trend_inside_the_60s_tick():
    fn = _module_func("_index_strip")
    called = {_call_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert "_cached_market_trend" not in called
    assert "_trend_result_ok" not in called
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert not names & {"_cached_market_trend", "_trend_result_ok"}


def test_dock_markets_tab_fetches_the_trend_and_hands_the_strip_two_arguments():
    block = _dock_markets_with()
    nodes = [n for stmt in block.body for n in ast.walk(stmt)]
    fetches = _calls_to(nodes, "_cached_market_trend")
    assert len(fetches) == 1
    assert "market_trend.as_of_session(_now_et()).isoformat()" in ast.unparse(fetches[0])
    strips = _calls_to(nodes, "_index_strip")
    assert len(strips) == 1 and len(strips[0].args) == 2 and not strips[0].keywords
    assert _calls_to(nodes, "_render_trend_chips")


def test_trend_dialog_is_a_module_level_st_dialog_defined_before_the_dock():
    fn = _module_func("_trend_dialog")
    decs = _decorator_src(fn)
    assert len(decs) == 1 and decs[0].startswith("st.dialog(")
    assert fn.lineno < _dock_markets_with().lineno


def test_trend_dialog_calls_the_same_renderers_the_macro_tab_calls():
    fn = _module_func("_trend_dialog")
    nodes = list(ast.walk(fn))
    for name in ("_render_trend_summary", "_render_trend_table"):
        calls = _calls_to(nodes, name)
        assert len(calls) == 1, name
        assert "book_empty" in {k.arg for k in calls[0].keywords}
    # the weights are read like the Macro tab does it, never with a bare .get
    srcs = [ast.unparse(a.value) for a in nodes if isinstance(a, ast.Assign)]
    assert any(s.replace("'", '"') == '_get_or_offline(st.session_state, "_port_df_enriched")'
               for s in srcs)
    assert "market_trend.held_weights" in _qualified_calls(fn)
    # the two renders share one try
    tries = [t for t in nodes if isinstance(t, ast.Try)]
    assert tries


def _pending_page_writes(fn: ast.AST) -> list[str]:
    out = []
    for a in ast.walk(fn):
        if isinstance(a, ast.Assign):
            for t in a.targets:
                if (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                        and t.slice.value == "_pending_page"):
                    out.append(ast.unparse(a.value).replace("'", '"'))
    return out


def test_trend_dialog_navigates_through_pending_page_never_nav_page():
    fn = _module_func("_trend_dialog")
    assert _pending_page_writes(fn) == ['"🌐 Macro"']
    trend_fns = ("_trend_dialog", "_trend_tile_lines", "_trend_note", "_render_trend_chips",
                 "_render_trend_summary", "_render_trend_table", "_index_strip")
    for name in trend_fns:
        for n in ast.walk(_module_func(name)):
            if isinstance(n, ast.Attribute):
                assert n.attr != "nav_page", name
            if isinstance(n, ast.Constant):
                assert n.value != "nav_page", name


def _key_constants(node: ast.AST) -> set:
    return {k.value.value for k in ast.walk(node) if isinstance(k, ast.keyword)
            and k.arg == "key" and isinstance(k.value, ast.Constant)}


def test_full_trend_table_button_sits_in_the_chips_helper_not_the_fragment():
    assert "_trend_dock_tbl_btn" not in _key_constants(_module_func("_index_strip"))
    chips = _module_func("_render_trend_chips")
    assert "_trend_dock_tbl_btn" in _key_constants(chips)
    assert _calls_to(ast.walk(chips), "_trend_dialog")
    assert "_trend_dlg_open_macro" in _key_constants(_module_func("_trend_dialog"))


@pytest.mark.parametrize("fn_name", ["_render_trend_chips", "_trend_tile_lines", "_trend_note"])
def test_dock_trend_helpers_have_no_raw_html_and_no_threshold_names(fn_name):
    fn = _module_func(fn_name)
    for n in ast.walk(fn):
        if isinstance(n, ast.keyword):
            assert n.arg != "unsafe_allow_html"
        if isinstance(n, ast.Name):
            assert not n.id.startswith(("TREND_", "RISK_OFF_")), n.id
        if isinstance(n, ast.Attribute):
            assert not n.attr.startswith(("TREND_", "RISK_OFF_")), n.attr


def test_chips_helper_reads_weights_through_get_or_offline_and_has_a_none_branch():
    fn = _module_func("_render_trend_chips")
    srcs = [ast.unparse(a.value).replace("'", '"') for a in ast.walk(fn)
            if isinstance(a, ast.Assign)]
    assert '_get_or_offline(st.session_state, "_port_df_enriched")' in srcs
    assert not any(".get(" in s for s in srcs)
    assert "market_trend.chip_groups" in _qualified_calls(fn)
    # the offline branch returns before anything else is drawn
    first = fn.body[1] if isinstance(fn.body[0], ast.Expr) else fn.body[0]
    assert isinstance(first, ast.If) and ast.unparse(first.test) == "result is None"
    assert any(isinstance(s, ast.Return) for s in first.body)


def test_chips_helper_always_dates_itself_and_discloses_a_missing_book():
    fn = _module_func("_render_trend_chips")
    # a caption that carries fmt_session (the as-of date), drawn unconditionally
    dated = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "caption"]
    assert any("fmt_session" in ast.unparse(c) or "_cap" in ast.unparse(c) for c in dated)
    assert any(isinstance(a, ast.Assign) and "fmt_session" in ast.unparse(a.value)
               for a in ast.walk(fn)), "the as-of caption text must use fmt_session"
    top_captions = [st_ for st_ in fn.body if isinstance(st_, ast.Expr)
                    and isinstance(st_.value, ast.Call)
                    and isinstance(st_.value.func, ast.Attribute)
                    and st_.value.func.attr == "caption"
                    and "_cap" in ast.unparse(st_.value)]
    assert top_captions, "the dated caption must not sit under an `if`"
    # the "you N%" disclosure when the book hasn't been published (weights None, not empty)
    gates = [n for n in ast.walk(fn) if isinstance(n, ast.If)
             and "_trend_w is None" in ast.unparse(n.test)]
    assert gates
    assert any("you N%" in ast.unparse(g) and "Home" in ast.unparse(g) for g in gates)
    assert "_trend_book_empty" in ast.unparse(gates[0].test)


@pytest.mark.parametrize("fn_name", ["_render_trend_chips", "_render_trend_summary"])
def test_trend_percent_rounding_is_one_shared_rule(fn_name):
    fn = _module_func(fn_name)
    local_defs = [n.name for n in ast.walk(fn) if isinstance(n, ast.FunctionDef)]
    assert "_pct" not in local_defs
    assert "market_trend.pct_text" in _qualified_calls(fn)


# ── the heatmap fetch keeps its readers ──────────────────────────────────────

def _calls_to(node_iter, name: str) -> list[ast.Call]:
    return [n for n in node_iter if isinstance(n, ast.Call) and _call_name(n) == name]


def test_fetch_sector_returns_is_still_a_module_level_producer():
    _module_func("_fetch_sector_returns")


def test_fetch_sector_returns_still_has_both_consumers():
    defn = _module_func("_fetch_sector_returns")
    outside = [
        n for n in ast.walk(_TREE)
        if isinstance(n, ast.Call) and _call_name(n) == "_fetch_sector_returns"
        and not (defn.lineno <= n.lineno <= defn.end_lineno)
    ]
    assert len(outside) >= 2

    home = _calls_to(_walk_body(_page_branch("🏠 Home")), "_fetch_sector_returns")
    assert home, "Home's leading-sectors read of _fetch_sector_returns is gone"

    overview = _page_branch("🥧 Portfolio Overview")
    diag = [
        a for a in _walk_body(overview)
        if isinstance(a, ast.Assign) and isinstance(a.value, ast.Call)
        and _call_name(a.value) == "_fetch_sector_returns"
        and any(isinstance(t, ast.Name) and t.id == "_sect_df" for t in a.targets)
    ]
    assert diag, "Performance Diagnostics' read of _fetch_sector_returns is gone"
