"""Tests for stock_analyzer.act_today_view — the single source of truth for
"how many Act Today items are there right now," shared by Home and Summary.

Two things this file guards:
1. The fail-open boundary: a crashed/not-yet-built Daily Brief must read as
   `state="offline"` / `n_active=None` (NEVER `0`) — the exact distinction
   that stops a crashed build from rendering as a false "nothing to act on".
2. The stop-breach demotion boundary (ported verbatim from 🏠 Home's
   pre-existing loop, now the single implementation every surface shares).
"""
import ast
from pathlib import Path
from tests._ast_cache import parse_file_cached

import pandas as pd
import pytest

from stock_analyzer.act_today_view import act_today_view

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "app.py"
ACT_VIEW_PY = ROOT / "stock_analyzer" / "act_today_view.py"

MARGIN = 2.0  # arbitrary test margin -- act_today_view takes it as a plain
              # parameter and never hardcodes a threshold of its own.


def _pe(ticker="AAPL", gap=None, stop=100.0, has_gap_col=True):
    """Build a minimal port_df_enriched-shaped DataFrame for one ticker."""
    data = {"Ticker": [ticker], "Stop": [stop]}
    if has_gap_col:
        data["Gap to Stop (%)"] = [gap]
    return pd.DataFrame(data)


def _breach(ticker="AAPL"):
    return {"ticker": ticker, "kind": "stop_breach", "why": "stop breached"}


def _sell_signal(ticker="MSFT"):
    return {"ticker": ticker, "kind": "sell_signal", "why": "sell signal fired"}


# ── Fail-open boundary ───────────────────────────────────────────────────────

def test_offline_when_brief_offline_flag_true():
    view = act_today_view(
        daily_brief={"act_today": [], "review_list": []},
        brief_offline=True,
        port_df_enriched=None,
        live_prices=None,
        margin_pct=MARGIN,
    )
    assert view["state"] == "offline"
    assert view["n_active"] is None  # never 0 -- 0 means "checked, clean"
    assert view["active"] == []
    assert view["resolved"] == []
    assert view["aware"] == []


def test_offline_when_daily_brief_is_none():
    view = act_today_view(
        daily_brief=None,
        brief_offline=False,
        port_df_enriched=None,
        live_prices=None,
        margin_pct=MARGIN,
    )
    assert view["state"] == "offline"
    assert view["n_active"] is None


def test_clear_when_act_bucket_empty_and_not_offline():
    view = act_today_view(
        daily_brief={"act_today": [], "review_list": []},
        brief_offline=False,
        port_df_enriched=None,
        live_prices=None,
        margin_pct=MARGIN,
    )
    assert view["state"] == "clear"
    assert view["n_active"] == 0
    assert view["active"] == []


# ── Same-day macro release wording (2026-10-30 #13) ──────────────────────────

def _macro_card():
    from datetime import date
    return {"ticker": None, "kind": "macro", "priority": "high",
            "action": "MACRO — GDP", "directive": "Hold through the print",
            "why": "w", "trigger": "t",
            "_event_date": date(2026, 10, 29), "_event_time_et": "08:30"}


def _macro_view(now):
    card = _macro_card()
    view = act_today_view(
        daily_brief={"act_today": [card], "review_list": []},
        brief_offline=False, port_df_enriched=None, live_prices=None,
        margin_pct=MARGIN, now=now,
    )
    items = [i for i in view["active"] + view["aware"] if i.get("kind") == "macro"]
    assert len(items) == 1
    return card, items[0]


def test_view_swaps_macro_directive_after_release_without_mutating_brief():
    from datetime import datetime
    card, out = _macro_view(datetime(2026, 10, 29, 12, 0))
    assert out["directive"].startswith("Released at 08:30 ET")
    assert card["directive"] == "Hold through the print"


def test_view_macro_unchanged_before_release():
    from datetime import datetime
    _, out = _macro_view(datetime(2026, 10, 29, 8, 0))
    assert out["directive"] == "Hold through the print"


def test_view_macro_unchanged_when_now_is_none():
    _, out = _macro_view(None)
    assert out["directive"] == "Hold through the print"


# ── Stop-breach demotion boundary ───────────────────────────────────────────

def test_recovered_breach_demotes_to_resolved():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("AAPL", gap=MARGIN + 0.01),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 0
    assert view["active"] == []
    assert len(view["resolved"]) == 1
    assert view["resolved"][0]["ticker"] == "AAPL"
    assert view["resolved"][0]["_breach_state"] == "recovered"


def test_gap_exactly_at_margin_stays_active_boundary():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("AAPL", gap=MARGIN),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []
    assert view["active"][0]["ticker"] == "AAPL"
    assert view["active"][0]["_breach_state"] == "active"


def test_gap_none_stays_active_never_demoted():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("AAPL", gap=None),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []
    assert view["active"][0]["_breach_state"] == "unavailable"


def test_gap_nan_stays_active_never_demoted():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("AAPL", gap=float("nan")),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []
    assert view["active"][0]["_breach_state"] == "unavailable"


def test_port_df_enriched_none_leaves_breach_active():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=None,
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []


def test_port_df_enriched_empty_leaves_breach_active():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=pd.DataFrame(),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []


def test_port_df_enriched_missing_gap_column_leaves_breach_active():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("AAPL", has_gap_col=False),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []


def test_non_breach_item_never_demoted_regardless_of_gap():
    # Even a port_df_enriched row that WOULD classify as "recovered" for a
    # stop_breach must have no effect on a non-stop_breach kind.
    view = act_today_view(
        daily_brief={"act_today": [_sell_signal("MSFT")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("MSFT", gap=99.0),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []
    assert view["active"][0]["ticker"] == "MSFT"
    assert "_breach_state" not in view["active"][0]


# ── Source-scan wiring test (AST-based, same pattern as
#    tests/test_repo_hygiene.py) ────────────────────────────────────────────
#
# A Call-only scan (the first draft of this test) misses an aliased import
# (`from stock_analyzer.decision_bucket import split_defensive as _sd`,
# never called under its real name) or a bound reference carried around and
# invoked later (`_f = decision_bucket.split_defensive; ...; _f(x, y)`).
# `_split_defensive_any_reference` catches ALL of those; `_split_defensive_
# call_sites` stays narrower (Call nodes only) so act_today_view.py's own
# genuine invocation can still be positively asserted, not just "referenced."

def _split_defensive_call_sites(path: Path) -> list[str]:
    """Call nodes only -- used to prove act_today_view.py genuinely INVOKES
    split_defensive (an any-reference hit alone wouldn't prove that)."""
    tree = parse_file_cached(path)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None
        )
        if name == "split_defensive":
            hits.append(ast.dump(node))
    return hits


def _split_defensive_any_reference(path: Path) -> list[str]:
    """Every way `split_defensive` could be referenced in `path`'s source:
    a direct Call, a bare Name/Attribute node (covers both a real call's own
    `.func` child AND a rebound reference that is never directly called in
    this file), or an `ImportFrom` alias. Used to prove ABSENCE in app.py --
    a Call-only scan would pass on an aliased import that's never called
    under its original name, which is exactly the kind of fork this module
    exists to close."""
    tree = parse_file_cached(path)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "split_defensive":
            hits.append(ast.dump(node))
        elif isinstance(node, ast.Attribute) and node.attr == "split_defensive":
            hits.append(ast.dump(node))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "split_defensive":
                    hits.append(f"ImportFrom({alias.name} as {alias.asname or alias.name})")
    return hits


def test_app_py_has_no_split_defensive_reference_of_any_kind():
    """Every Act Today count surface must route through act_today_view() --
    a direct decision_bucket.split_defensive() call (or an aliased import, or
    a rebound reference carried around and called later) in app.py is exactly
    the fork that let Home and Summary count differently (the bug this
    module exists to close). Paired with
    test_act_today_view_module_is_the_sole_caller_of_split_defensive below:
    together the two prove "sole caller," not just each half in isolation --
    app.py has ZERO references of any kind, and act_today_view.py has a real
    Call, so nothing else in app.py's own source could be the actual wiring
    point."""
    hits = _split_defensive_any_reference(APP_PY)
    assert not hits, (
        f"app.py references decision_bucket.split_defensive "
        f"({len(hits)} site(s), including aliased imports/rebound names, not "
        f"just direct calls) -- route through "
        "stock_analyzer.act_today_view.act_today_view() instead."
    )


def test_act_today_view_module_is_the_sole_caller_of_split_defensive():
    """The other half of the pairing above -- act_today_view.py must
    genuinely INVOKE split_defensive (a Call, not merely an unused import),
    or the whole module would be dead wiring."""
    hits = _split_defensive_call_sites(ACT_VIEW_PY)
    assert hits, (
        "act_today_view.py no longer calls decision_bucket.split_defensive -- "
        "has the implementation moved elsewhere unreviewed?"
    )


# ── B-H2 (2026-10-11 audit): an offline stub must never read as a real Brief ─
#
# Brief Lock was disabled only when the Brief was None, but the crash path had
# already swapped None for a stub dict -- so Lock could freeze an OFFLINE Brief.
# The next render served the stub, flipped the offline flag to False, and Home
# read "Nothing to act on today" with every protective veto gone all day.

from stock_analyzer.act_today_view import (  # noqa: E402
    OFFLINE_BRIEF_MARKER,
    brief_lock_allowed,
    is_offline_brief,
    locked_snapshot_servable,
    offline_brief_stub,
)

_REAL_BRIEF = {"act_today": [], "review_list": [],
               "grow_today": {"tone": "bull"}}


def test_offline_brief_stub_is_marked_and_detected():
    stub = offline_brief_stub()
    assert stub[OFFLINE_BRIEF_MARKER] is True
    assert is_offline_brief(stub)
    # Still carries every key downstream code reads, so nothing crashes on it.
    for key in ("act_today", "review_list", "grow_today"):
        assert key in stub


def test_offline_brief_stub_returns_a_fresh_dict_each_call():
    a = offline_brief_stub()
    a["act_today"].append({"ticker": "X"})
    assert offline_brief_stub()["act_today"] == [], (
        "offline_brief_stub must not share mutable state across calls"
    )


@pytest.mark.parametrize("value", [
    _REAL_BRIEF, None, [], "stub", {OFFLINE_BRIEF_MARKER: 1}, {OFFLINE_BRIEF_MARKER: "yes"},
])
def test_is_offline_brief_false_for_anything_but_the_marked_stub(value):
    # `is True`, not truthiness: a stray truthy value under the key is not a stub.
    assert is_offline_brief(value) is False
    # Contrast: the genuine stub IS detected, so this test can't pass vacuously.
    assert is_offline_brief(offline_brief_stub()) is True


def test_marked_stub_reads_offline_even_when_the_flag_says_false():
    # THE B-H2 headline: the flag is exactly what the Lock path flipped to
    # False. A marked stub must still read offline, n_active None -- never 0.
    v = act_today_view(offline_brief_stub(), brief_offline=False,
                       port_df_enriched=None, live_prices=None, margin_pct=MARGIN)
    assert v["state"] == "offline"
    assert v["n_active"] is None
    # Contrast: the SAME empty buckets, unmarked, with the flag False, is a
    # genuine "checked, nothing active" -- proving the marker alone flips it.
    unmarked = {k: v2 for k, v2 in offline_brief_stub().items() if k != OFFLINE_BRIEF_MARKER}
    v_clear = act_today_view(unmarked, brief_offline=False,
                             port_df_enriched=None, live_prices=None, margin_pct=MARGIN)
    assert v_clear["state"] == "clear"
    assert v_clear["n_active"] == 0


@pytest.mark.parametrize("brief,offline,expected", [
    (_REAL_BRIEF, False, True),            # a genuinely built Brief may be locked
    (_REAL_BRIEF, True, False),            # the flag alone refuses
    (offline_brief_stub(), False, False),  # the marker alone refuses (laundering case)
    (offline_brief_stub(), True, False),
    (_REAL_BRIEF, None, False),            # offline state never established -> refuse
    (None, False, False),
])
def test_brief_lock_allowed_truth_table(brief, offline, expected):
    assert brief_lock_allowed(brief, offline) is expected


@pytest.mark.parametrize("snapshot,expected", [
    (_REAL_BRIEF, True),
    (offline_brief_stub(), False),  # the case that leaked
    (None, False),
    ({}, False),        # the stricter of the two callers this replaced
    ([], False),
])
def test_locked_snapshot_servable_truth_table(snapshot, expected):
    assert locked_snapshot_servable(snapshot) is expected


# --- app.py wiring, pinned at BOTH ends (memory feedback_ast_test_pins_one_end).
# app.py has no import-level test coverage, so these AST checks are the only
# thing standing between the decision above and the render code using it.

def _app_tree():
    return parse_file_cached(APP_PY)


def _calls_named(tree, name):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            n = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if n == name:
                out.append(node)
    return out


def _is_name(node, name):
    return isinstance(node, ast.Name) and node.id == name


def _is_call(node, name):
    """True iff `node` is itself a call to `name` (bare or attribute form)."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (isinstance(f, ast.Name) and f.id == name) or (
        isinstance(f, ast.Attribute) and f.attr == name
    )


def _assigns_to(tree, target):
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and _is_name(n.targets[0], target)
    ]


def test_app_builds_the_offline_stub_via_the_helper_never_inline():
    # PRODUCER end. An inline stub literal would carry no marker, silently
    # reopening B-H2 while every consumer test below still passed.
    tree = _app_tree()
    stub_keys = {"act_today", "review_list", "grow_today"}
    inline = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        and {k.value for k in node.keys if isinstance(k, ast.Constant)} == stub_keys
    ]
    assert inline == [], f"inline offline-Brief stub literal(s) at app.py lines {inline}"
    # Pinned as the DIRECT right-hand side of a `_daily_brief =` assignment,
    # not merely "called somewhere" -- a call whose result is discarded, or
    # wrapped and transformed, would pass a mere call-count check.
    direct = [a for a in _assigns_to(tree, "_daily_brief") if _is_call(a.value, "offline_brief_stub")]
    assert len(direct) == 1, (
        f"expected exactly one `_daily_brief = offline_brief_stub()`, found {len(direct)}"
    )


def test_app_never_touches_the_offline_marker_directly():
    # The marker is owned by act_today_view. If app.py could read or pop it,
    # a stub could be built correctly and then stripped (`.pop(MARKER)`),
    # reopening B-H2 while the producer test above still passed.
    src = APP_PY.read_text(encoding="utf-8-sig")
    assert "OFFLINE_BRIEF_MARKER" not in src
    assert OFFLINE_BRIEF_MARKER not in src, (
        f"app.py references the offline marker {OFFLINE_BRIEF_MARKER!r} directly"
    )


def test_app_brief_lock_decision_computed_once_and_used_for_both_state_and_click():
    tree = _app_tree()
    # (1) The decision is computed from the REAL offline flag -- not a literal.
    decided = [a for a in _assigns_to(tree, "_brief_lock_ok") if _is_call(a.value, "brief_lock_allowed")]
    assert len(decided) == 1, f"expected one `_brief_lock_ok = brief_lock_allowed(...)`, found {len(decided)}"
    args = decided[0].value.args
    assert len(args) == 2 and _is_name(args[0], "_daily_brief"), "first arg must be _daily_brief"
    flag = args[1]
    assert (isinstance(flag, ast.Call) and isinstance(flag.func, ast.Attribute)
            and flag.func.attr == "get" and flag.args
            and isinstance(flag.args[0], ast.Constant)
            and flag.args[0].value == "_daily_brief_offline"), (
        "the offline argument must be session_state.get('_daily_brief_offline') -- "
        "a hard-coded False would let the flag-based refusal silently disappear"
    )
    # (2) The button renders disabled exactly when the decision refuses.
    lock_btns = [
        c for c in _calls_named(tree, "button")
        if any(kw.arg == "key" and isinstance(kw.value, ast.Constant)
               and kw.value.value == "_brief_lock_btn" for kw in c.keywords)
    ]
    assert len(lock_btns) == 1, f"expected one _brief_lock_btn, found {len(lock_btns)}"
    disabled = [kw.value for kw in lock_btns[0].keywords if kw.arg == "disabled"]
    assert (disabled and isinstance(disabled[0], ast.UnaryOp)
            and isinstance(disabled[0].op, ast.Not) and _is_name(disabled[0].operand, "_brief_lock_ok"))
    # (3) The CLICK is re-guarded: `if st.button(...) and _brief_lock_ok:`.
    # A Streamlit button returns True on the rerun it triggered even if that
    # rerun renders it disabled -- the disabled state alone does not stop it.
    guarded = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.If) and isinstance(n.test, ast.BoolOp) and isinstance(n.test.op, ast.And)
        and any(v is lock_btns[0] for v in n.test.values)
        and any(_is_name(v, "_brief_lock_ok") for v in n.test.values)
    ]
    assert len(guarded) == 1, "the Lock click handler must be guarded by `and _brief_lock_ok`"
    # The BUTTON must be the FIRST operand. `_brief_lock_ok and st.button(...)`
    # short-circuits before the widget is created, so on a refused Brief the
    # Lock button would vanish from the page instead of rendering disabled.
    assert guarded[0].test.values[0] is lock_btns[0], (
        "st.button(...) must be evaluated first so the widget always renders"
    )


def test_app_lock_serve_path_guards_with_locked_snapshot_servable():
    # `_brief_use_lock = (... and locked_snapshot_servable(_brief_snapshot))`,
    # as a DIRECT operand of the And -- wrapping it as `servable(x) or x is not
    # None` would re-admit the stub and still pass a mere call count.
    tree = _app_tree()
    uses = _assigns_to(tree, "_brief_use_lock")
    assert len(uses) == 1, f"expected one `_brief_use_lock =` assignment, found {len(uses)}"
    expr = uses[0].value
    assert isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.And)
    hits = [v for v in expr.values
            if _is_call(v, "locked_snapshot_servable") and v.args and _is_name(v.args[0], "_brief_snapshot")]
    assert len(hits) == 1, "_brief_use_lock must AND in locked_snapshot_servable(_brief_snapshot) directly"
    # ...and the CONSUMER must test that guarded flag alone. Widening it to
    # `if _brief_use_lock or _brief_snapshot is not None:` would serve a stub
    # while the guarded expression above still looked correct.
    serving = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.If) and _is_name(n.test, "_brief_use_lock")
        and any(isinstance(s, ast.Assign) and len(s.targets) == 1
                and _is_name(s.targets[0], "_daily_brief") and _is_name(s.value, "_brief_snapshot")
                for s in n.body)
    ]
    assert len(serving) == 1, (
        "the snapshot must be served only under a bare `if _brief_use_lock:`"
    )


def test_app_evening_debrief_baseline_guards_with_locked_snapshot_servable():
    # The `if` that picks the locked snapshot as the debrief's AM baseline must
    # carry locked_snapshot_servable(...) as a direct And operand.
    tree = _app_tree()

    def _guards_the_stored_snapshot(v):
        # The argument must be the STORED snapshot. Checking some other object
        # (e.g. the live `_daily_brief`) would pass while the branch below still
        # adopts an offline stub from session_state as the AM baseline.
        if not (_is_call(v, "locked_snapshot_servable") and v.args):
            return False
        a = v.args[0]
        return (isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute)
                and a.func.attr == "get" and a.args
                and isinstance(a.args[0], ast.Constant)
                and a.args[0].value == "_brief_locked_snapshot")

    hits = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.If) and isinstance(n.test, ast.BoolOp) and isinstance(n.test.op, ast.And)
        and any(_guards_the_stored_snapshot(v) for v in n.test.values)
        and any(isinstance(s, ast.Assign) and len(s.targets) == 1
                and _is_name(s.targets[0], "_ed_baseline_brief") for s in n.body)
    ]
    assert len(hits) == 1, (
        "Evening Debrief's locked-baseline branch must guard on "
        "locked_snapshot_servable(session_state.get('_brief_locked_snapshot'))"
    )
