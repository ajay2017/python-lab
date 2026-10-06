"""Tests for stock_analyzer.act_today_pointer — Home redesign P2's top-of-
page Act Today pointer (docs/plans/home-redesign.md's 2026-10-06 "P1 + P2
designed" section), and its wiring into app.py.

Three things this file guards:
1. The defense-in-depth boundary: a `view` claiming `state="clear"` but
   carrying a non-empty `active` list must still resolve to `"act"` — the
   presence of active items is ground truth, not the state label.
2. The fail-loud boundary: anything malformed or unrecognized reads as
   `"offline"` / `n=None`, never calm.
3. AST-based wiring on app.py: exactly one `act_today_view()` call inside
   Home, `_act_ptr_ph` declared before `_notices_summary_ph` (D7), and the
   full-book data-outage `st.stop()` preceded by an `_act_ptr_ph` fill.
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer.act_today_pointer import act_pointer
from stock_analyzer.act_today_view import act_today_view

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "app.py"
ACT_PTR_PY = ROOT / "stock_analyzer" / "act_today_pointer.py"

MARGIN = 2.0  # arbitrary test margin, same convention as test_act_today_view.py


def _pe(ticker="AAPL", gap=None, stop=100.0):
    return pd.DataFrame({"Ticker": [ticker], "Stop": [stop], "Gap to Stop (%)": [gap]})


def _breach(ticker="AAPL"):
    return {"ticker": ticker, "kind": "stop_breach", "why": "stop breached"}


def _act_item(ticker: str, kind: str) -> dict:
    """A hand-built act-bucket item, as act_today_view()'s own output would
    shape one (decision_bucket.split_defensive adds `_source` on its way in)."""
    return {"ticker": ticker, "kind": kind, "_source": "act", "why": "test"}


# ── Defense-in-depth boundary — write this first, per the task spec ─────────

def test_active_list_nonempty_overrides_clear_state_label():
    # The single most important test: a view claiming state="clear" but with
    # a non-empty active list must resolve to "act" regardless — presence of
    # active items is ground truth, not the state field itself.
    view = {
        "state": "clear",
        "active": [_act_item("AAPL", "stop_breach")],
        "resolved": [],
        "n_active": 1,
    }
    result = act_pointer(view)
    assert result["state"] == "act"
    assert result["n"] == 1
    assert result["chips"] == {"EXIT": 1}


def test_active_override_with_offline_n_still_resolves_to_act_with_none_n():
    # Reviewer finding, 2026-10-06: a view that claims state="offline" (whose
    # own shape normally carries n=None) but ALSO has a non-empty active list
    # takes the override to "act" — not reachable from today's real
    # act_today_view() output, but the module must not raise or silently
    # invent a number here; it reports what it actually has (n=None) and
    # trusts the RENDER layer (app.py) to never print "None Act Today" for
    # this case — pinned by this test so the render-layer guard can't drift
    # without this module's own contract changing first.
    view = {
        "state": "offline",
        "active": [_act_item("MSFT", "stop_breach")],
        "resolved": [],
        "n_active": None,
    }
    result = act_pointer(view)
    assert result["state"] == "act"
    assert result["n"] is None
    assert result["chips"] == {"EXIT": 1}


# ── Offline / malformed input — fail loud, never calm ────────────────────────

def test_none_view_is_offline():
    assert act_pointer(None) == {"state": "offline", "n": None, "chips": {}, "n_resolved": 0}


def test_empty_dict_view_is_offline():
    result = act_pointer({})
    assert result["state"] == "offline"
    assert result["n"] is None


def test_unrecognized_state_string_is_offline():
    result = act_pointer({"state": "totally-made-up"})
    assert result["state"] == "offline"
    assert result["n"] is None


def test_non_dict_view_is_offline():
    result = act_pointer("not a dict")
    assert result["state"] == "offline"
    assert result["n"] is None


def test_real_offline_view_from_act_today_view_is_offline():
    view = act_today_view(
        daily_brief=None, brief_offline=True, port_df_enriched=None,
        live_prices=None, margin_pct=MARGIN,
    )
    assert act_pointer(view) == {"state": "offline", "n": None, "chips": {}, "n_resolved": 0}


# ── Clear state ───────────────────────────────────────────────────────────

def test_clear_state_zero_n_and_no_chips():
    view = {"state": "clear", "active": [], "resolved": [], "n_active": 0}
    assert act_pointer(view) == {"state": "clear", "n": 0, "chips": {}, "n_resolved": 0}


def test_clear_state_counts_resolved():
    view = {
        "state": "clear", "active": [], "n_active": 0,
        "resolved": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
    }
    result = act_pointer(view)
    assert result["state"] == "clear"
    assert result["n_resolved"] == 2


def test_clear_state_resolved_none_defaults_to_zero():
    view = {"state": "clear", "active": [], "n_active": 0, "resolved": None}
    assert act_pointer(view)["n_resolved"] == 0


# ── Act state ─────────────────────────────────────────────────────────────

def test_act_state_trusts_n_active_not_recomputed_from_len_active():
    # n must come from view["n_active"] verbatim -- never recomputed from
    # len(active). A real disagreement between the two is itself a bug worth
    # a test catching, not something to silently reconcile here.
    view = {
        "state": "act",
        "active": [_act_item("AAPL", "stop_breach")],
        "resolved": [],
        "n_active": 5,  # deliberately disagrees with len(active) == 1
    }
    assert act_pointer(view)["n"] == 5


def test_act_state_chips_sum_and_ordered_exit_trim_watch():
    view = {
        "state": "act",
        "active": [
            _act_item("A", "stop_breach"),          # EXIT
            _act_item("B", "sell_signal"),          # EXIT
            _act_item("C", "risk"),                 # TRIM
            _act_item("D", "premortem_triggered"),  # WATCH
        ],
        "resolved": [],
        "n_active": 4,
    }
    result = act_pointer(view)
    assert list(result["chips"].items()) == [("EXIT", 2), ("TRIM", 1), ("WATCH", 1)]


def test_act_state_zero_count_bucket_omitted_from_chips():
    view = {
        "state": "act",
        "active": [_act_item("A", "stop_breach"), _act_item("B", "premortem_triggered")],
        "resolved": [],
        "n_active": 2,
    }
    result = act_pointer(view)
    assert "TRIM" not in result["chips"]
    assert list(result["chips"].keys()) == ["EXIT", "WATCH"]


def test_act_state_n_resolved_can_be_populated_alongside_active():
    # A stop breach can resolve on the SAME day there's still something else
    # active -- n_resolved must populate in the act state too, not just clear.
    view = {
        "state": "act",
        "active": [_act_item("A", "sell_signal")],
        "resolved": [{"ticker": "B"}],
        "n_active": 1,
    }
    result = act_pointer(view)
    assert result["state"] == "act"
    assert result["n_resolved"] == 1


# ── Real act_today_view() fixtures (catches drift between the two modules'
#    actual field names, not just a hand-built dict) ─────────────────────────

def test_real_act_today_view_clear_with_recovered_breach():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("AAPL", gap=MARGIN + 0.01),  # clears the recovery margin
        live_prices={},
        margin_pct=MARGIN,
    )
    result = act_pointer(view)
    assert result["state"] == "clear"
    assert result["n"] == 0
    assert result["n_resolved"] == 1


def test_real_act_today_view_act_state_exit_chip():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("AAPL", gap=MARGIN),  # exactly at margin -- stays active
        live_prices={},
        margin_pct=MARGIN,
    )
    result = act_pointer(view)
    assert result["state"] == "act"
    assert result["n"] == 1
    assert result["chips"] == {"EXIT": 1}
    assert result["n_resolved"] == 0


# ── Module hygiene ────────────────────────────────────────────────────────

def test_module_never_references_split_defensive():
    # AST-based (not a raw substring search) so this module's own docstring,
    # which NAMES split_defensive in prose while explaining why it must
    # never be referenced, doesn't trip its own guard.
    src = ACT_PTR_PY.read_text(encoding="utf-8-sig")
    tree = ast.parse(src, filename=str(ACT_PTR_PY))
    hits = [
        n for n in ast.walk(tree)
        if (isinstance(n, ast.Name) and n.id == "split_defensive")
        or (isinstance(n, ast.Attribute) and n.attr == "split_defensive")
        or (isinstance(n, ast.ImportFrom) and any(a.name == "split_defensive" for a in n.names))
    ]
    assert not hits, (
        "act_today_pointer.py must never call decision_bucket.split_defensive "
        "directly -- it would reopen the count-drift bug act_today_view.py "
        "exists to close. Read act_today_view()'s own output instead."
    )


def test_module_uses_bucket_act_by_type():
    src = ACT_PTR_PY.read_text(encoding="utf-8-sig")
    assert "bucket_act_by_type" in src


# ── AST-based wiring tests on app.py ─────────────────────────────────────────

def _parse_app_py() -> ast.Module:
    src = APP_PY.read_text(encoding="utf-8-sig")
    return ast.parse(src, filename=str(APP_PY))


def _home_if_node(tree: ast.Module) -> ast.If | None:
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "page"
            and node.test.comparators
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value == "🏠 Home"
        ):
            return node
    return None


def _walk_body(body: list[ast.stmt]):
    """Walk only `body`'s own statements, NOT a surrounding If node's
    `orelse` -- Home's `if page == "🏠 Home":` node's `orelse` chain holds
    every OTHER page's elif branch (Summary included, which has its own
    legitimate act_today_view() call), so ast.walk(home_if_node) directly
    would wrongly sweep those in too."""
    for stmt in body:
        yield from ast.walk(stmt)


def test_exactly_one_act_today_view_call_inside_home():
    tree = _parse_app_py()
    home = _home_if_node(tree)
    assert home is not None, "couldn't find the top-level `if page == \"🏠 Home\":` block"
    calls = [
        n for n in _walk_body(home.body)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "act_today_view"
    ]
    assert len(calls) == 1, (
        f"expected exactly one act_today_view() call inside Home's render "
        f"path, found {len(calls)} -- a second call site reopens the exact "
        f"count-drift class act_today_view.py exists to close."
    )


def test_act_ptr_ph_declared_before_notices_summary_ph():
    tree = _parse_app_py()
    act_ptr_line = None
    notices_ph_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "_act_ptr_ph" and act_ptr_line is None:
                    act_ptr_line = node.lineno
                if target.id == "_notices_summary_ph" and notices_ph_line is None:
                    notices_ph_line = node.lineno
    assert act_ptr_line is not None, "_act_ptr_ph = st.empty() not found in app.py"
    assert notices_ph_line is not None, "_notices_summary_ph = st.empty() not found in app.py"
    assert act_ptr_line < notices_ph_line, (
        "_act_ptr_ph must be declared BEFORE _notices_summary_ph (D7) -- the "
        "Act Today pointer must be the first content-bearing element Home "
        "renders, ahead of P1's notices summary."
    )


def _stmt_lists(tree: ast.AST):
    """Every statement list in the tree (If/For/While/Try/FunctionDef/Module
    body, plus orelse/finalbody/except-handler bodies) -- adjacency of two
    statements only means something within the SAME list."""
    lists = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if isinstance(body, list) and body and all(isinstance(s, ast.stmt) for s in body):
                lists.append(body)
        for h in getattr(node, "handlers", None) or []:
            if isinstance(h.body, list):
                lists.append(h.body)
    return lists


def _is_call_stmt_on(stmt: ast.stmt, name: str, attr: str | None = None) -> bool:
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    func = stmt.value.func
    if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == name):
        return False
    return attr is None or func.attr == attr


def _contains_act_ptr_ph_call(node: ast.AST) -> bool:
    """True if an `_act_ptr_ph.<method>(...)` call exists ANYWHERE in node's
    subtree (not just as a direct top-level statement) -- needed because the
    fill now lives inside each branch of an if/else, not as a flat sibling of
    st.stop() (reviewer finding, 2026-10-06: the original version of this
    test only checked direct statements in the SAME list as st.stop(), which
    would have missed a fill nested one level deeper in an if/else branch,
    and more importantly would NOT have caught a future st.stop() added
    without any fill on one of its branches -- the exact 'missed site' class
    P0's own review already found once in a different Home section)."""
    for n in ast.walk(node):
        if (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Attribute)
                and isinstance(n.value.func.value, ast.Name)
                and n.value.func.value.id == "_act_ptr_ph"):
            return True
    return False


def test_outage_branch_st_stop_preceded_by_act_ptr_ph_fill_on_every_path():
    """Every `st.stop()` inside Home's render body, reached before the single
    `act_today_view(` call (P0/P2's shared count source), must be guaranteed
    an `_act_ptr_ph` fill on EVERY path that reaches it -- not just "some
    _act_ptr_ph call exists somewhere in app.py", which the original weak
    version of this test effectively allowed (it would pass even if a NEW
    st.stop() were added elsewhere in Home with zero fill of its own, as long
    as some unrelated fill existed anywhere else in the file)."""
    tree = _parse_app_py()
    home = _home_if_node(tree)
    assert home is not None

    act_today_view_line = None
    for n in _walk_body(home.body):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "act_today_view"):
            act_today_view_line = n.lineno
            break
    assert act_today_view_line is not None

    checked_any_stop = False
    for stmts in _stmt_lists(home):
        for i, stmt in enumerate(stmts):
            if not _is_call_stmt_on(stmt, "st", "stop"):
                continue
            if stmt.lineno >= act_today_view_line:
                continue  # a stop() after _act_view exists is out of scope here
            checked_any_stop = True
            # A direct sibling fill earlier in the SAME list covers every
            # path trivially (the old, still-valid flat case).
            if any(_is_call_stmt_on(s, "_act_ptr_ph") for s in stmts[:i]):
                continue
            # Otherwise, the immediately preceding statement must be the
            # if/else whose EVERY branch independently fills the pointer --
            # a fill in only one branch would leave the other path blank.
            assert i > 0 and isinstance(stmts[i - 1], ast.If), (
                f"st.stop() at app.py:{stmt.lineno} has no _act_ptr_ph fill "
                "earlier in its own statement list, and isn't immediately "
                "preceded by an if/else whose branches could each supply one."
            )
            branch = stmts[i - 1]
            # Both branches checked unconditionally -- an empty `orelse`
            # (no `else:` clause at all) means that path reaches st.stop()
            # with nothing in it to fill the pointer, which must fail here
            # too, not be skipped as "nothing to check".
            for b in (branch.body, branch.orelse):
                assert any(_contains_act_ptr_ph_call(s) for s in b), (
                    f"st.stop() at app.py:{stmt.lineno}: at least one branch "
                    "of the preceding if/else has no _act_ptr_ph fill "
                    "anywhere in it -- that path would reach st.stop() with "
                    "the top pointer left blank, read as an unstated "
                    "all-clear under this design's explicit calm success "
                    "state."
                )
    assert checked_any_stop, (
        "no st.stop() found inside Home before the act_today_view() call -- "
        "if that call site moved, this test's own scope assumption is stale."
    )
