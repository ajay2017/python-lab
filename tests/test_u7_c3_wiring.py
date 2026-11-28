"""U7 C3 commit 2: AST pins for the app.py wiring of the Robinhood net capital check
(docs/plans/net-capital-broker-frame-c3-spec.md section 5, "AST pins").

No test imports app.py, so every sizing surface's use of `_effective_net_capital`
is pinned here by its syntax tree: the helper's own body, the single place the
budget inputs are built, the six decision sites, the Home move above the drift
banner, the memo signature and the cash-note form. The budget_view / headless /
cron pins live in tests/test_effective_capital.py (not duplicated here).
No network, no DB."""
import ast
import re
from pathlib import Path

import pytest

from stock_analyzer import daily_budget, daily_briefing
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

_ROOT = Path(__file__).resolve().parent.parent
_APP_PATH = _ROOT / "app.py"
_APP = _APP_PATH.read_text(encoding="utf-8")
_TREE = parse_file_cached(_APP_PATH)


def _name(call):
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _calls(node, name):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and _name(n) == name]


def _top_def(name):
    hits = [n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(hits) == 1, f"{name} must be one module-level def"
    return hits[0]


def _all_defs(name):
    return [n for n in ast.walk(_TREE)
            if isinstance(n, ast.FunctionDef) and n.name == name]


def _assign_target_of(call):
    """Name of the variable a `_effective_net_capital(...)` call is assigned to
    (None when it isn't the value of a simple assignment)."""
    for n in ast.walk(_TREE):
        if isinstance(n, ast.Assign) and n.value is call and isinstance(n.targets[0], ast.Name):
            return n.targets[0].id
        if isinstance(n, ast.Subscript) and n.value is call:
            return "<subscript>"
    return None


# ---------------------------------------------------------------- the helper
def test_resolve_net_capital_is_called_exactly_three_times():
    calls = _calls(_TREE, "resolve_net_capital")
    assert len(calls) == 3
    helper = _top_def("_effective_net_capital")
    inside = _calls(helper, "resolve_net_capital")
    assert len(inside) == 1
    outside = set()
    for n in ast.walk(_TREE):
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                and _name(n.value) == "resolve_net_capital"
                and n.value not in inside):
            tgt = n.targets[0]
            assert isinstance(tgt, ast.Tuple)
            outside.add(tgt.elts[0].id)
    # the two awareness siblings stay on the JOURNAL figure (C6)
    assert outside == {"_bl_net_cap", "_mg_net_cap"}


def test_app_never_builds_budget_inputs_itself():
    assert _calls(_TREE, "budget_inputs") == []
    assert "budget_inputs(" not in "\n".join(
        ln for ln in _APP.splitlines() if not ln.lstrip().startswith("#"))


def test_helper_and_loader_are_module_level_and_never_nested():
    _top_def("_effective_net_capital")
    loader = _top_def("_cached_broker_snapshot")
    assert any("cache_data" in ast.unparse(d) for d in loader.decorator_list)
    assert len(_all_defs("_cached_broker_snapshot")) == 1     # Home's nested copy is gone
    assert len(_all_defs("_effective_net_capital")) == 1
    assert any(isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "_NC_UNSET" for t in n.targets)
        for n in _TREE.body)


def test_helper_signature():
    h = _top_def("_effective_net_capital")
    assert [a.arg for a in h.args.args] == ["gross_book"]
    assert [a.arg for a in h.args.kwonlyargs] == [
        "port_df", "holdings_df", "acct_rec", "book_known"]
    unset = [ast.unparse(d) for d in h.args.kw_defaults]
    assert unset == ["None", "_NC_UNSET", "_NC_UNSET", "True"]


def test_helper_body_wiring():
    h = _top_def("_effective_net_capital")
    res = _calls(h, "resolve")
    assert len(res) == 1 and ast.unparse(res[0].func) == "_effective_capital.resolve"
    seg = ast.unparse(h)
    assert "_cached_broker_snapshot()" in seg
    assert "trades_df=st.session_state.get('trades_df')" in seg
    assert "_budget_price_map(" in seg
    assert "_now_et()" in seg
    assert "db.load_account_cash()" in seg
    assert "ACCOUNT_CASH_STALE_DAYS" in seg
    # the budget code is never handed a number here: nothing of the budget is built
    assert _calls(h, "budget_inputs") == []
    assert _calls(h, "postpass") == [] and _calls(h, "plan_downstream") == []
    assert not [n for n in ast.walk(h) if isinstance(n, ast.Constant)
                and n.value == "budget_inputs"]


def test_helper_sits_before_publish_and_outside_the_refresh_segment():
    i_helper = _APP.index("def _effective_net_capital(")
    i_loader = _APP.index("def _cached_broker_snapshot(")
    i_pub = _APP.index("def _publish_new_position_budget(")
    i_plan = _APP.index("def _budget_downstream_plan(")
    i_refresh_end = _APP.index("def _fill_news_slot(")
    assert i_loader < i_helper < i_pub < i_plan < i_refresh_end
    # The refresh segment must not read "trades_df" by name. The helper above
    # does read the session copy for the split guard (over-tight only if it
    # lags), and every after-trade caller reloads trades_df before the refresh.
    assert "trades_df" not in _APP[i_plan:i_refresh_end]


# ---------------------------------------------------------------- budget callers
def test_publish_and_plan_take_the_whole_helper_dict():
    pub = _top_def("_publish_new_position_budget")
    assert [a.arg for a in pub.args.args] == ["raw_brief", "ncap", "price_map"]
    plan = _top_def("_budget_downstream_plan")
    assert [a.arg for a in plan.args.args][:2] == ["ncap", "asks"]
    for fn in (pub, plan):
        subs = [n for n in ast.walk(fn)
                if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                and n.value.id == "ncap"
                and isinstance(n.slice, ast.Constant) and n.slice.value == "budget_inputs"]
        assert len(subs) == 1, fn.name
        # nothing else of the helper dict (certainly not "net") reaches the budget
        others = [n for n in ast.walk(fn) if isinstance(n, ast.Subscript)
                  and isinstance(n.value, ast.Name) and n.value.id == "ncap"]
        assert len(others) == 1, fn.name


def test_every_publish_site_passes_the_helper_dict_never_a_net_figure():
    sites = _calls(_TREE, "_publish_new_position_budget")
    assert len(sites) == 3          # refresh-after-trade, Home HIT path, Home MISS path
    firsts = sorted(ast.unparse(c.args[1]) for c in sites)
    assert firsts == ["_f255_nc", "_f255_nc", "_rb_nc"]
    for c in sites:
        assert "net_cap" not in ast.unparse(c)
    for c in _calls(_TREE, "_budget_downstream_plan"):
        assert ast.unparse(c.args[0]) in ("_f255_nc", "_f255_wl_nc")
        assert "net_cap" not in ast.unparse(c)


def test_buy_preview_gets_the_helper_budget_inputs():
    previews = _calls(_TREE, "buy_preview")
    assert len(previews) == 1
    assert ast.unparse(previews[0].args[0]) == "_pb_nc['budget_inputs']"


# ---------------------------------------------------------------- the six sites
def test_all_six_decision_sites_call_the_helper():
    calls = _calls(_TREE, "_effective_net_capital")
    # six decision sites + the Account page's readout
    assert len(calls) == 7
    targets = sorted(str(_assign_target_of(c)) for c in calls)
    assert targets == sorted([
        "_rb_nc",           # _refresh_portfolio_cache_after_trade budget republish
        "_f255_nc",         # Home F-255
        "_f255_nc",         # Analysis F-255
        "_f255_wl_nc",      # Watchlist F-255
        "_pb_nc",           # Trade Journal buy preview
        "_f255_nc",         # Trade Journal add-concentration net-capital leg
        "<subscript>",      # Account readout: ...["frame"]
    ])
    # the after-trade refresh lives inside its function and passes the fresh holdings
    refresh = _top_def("_refresh_portfolio_cache_after_trade")
    (rc,) = _calls(refresh, "_effective_net_capital")
    kw = {k.arg: ast.unparse(k.value) for k in rc.keywords}
    assert kw == {"port_df": "port_df", "holdings_df": "h_df"}


def test_home_f255_comes_from_the_helper_and_precedes_the_drift_banner():
    i_help = _APP.index("_f255_nc = _effective_net_capital(total_val, port_df=port_df)")
    i_render = _APP.index("_drift_notice_tier = _render_broker_drift(")
    i_brief = _APP.index("net_capital     = _f255_net_cap,")
    assert i_help < i_render < i_brief
    # both Brief builds read the reconciled figure
    assert _APP.count("net_capital     = _f255_net_cap,") == 2
    assert '_f255_net_cap, _f255_basis = _f255_nc["net"], _f255_nc["state"]' in _APP


def test_trade_journal_buy_preview_and_add_concentration_arguments():
    by_target = {}
    for c in _calls(_TREE, "_effective_net_capital"):
        by_target.setdefault(_assign_target_of(c), []).append(
            {k.arg: ast.unparse(k.value) for k in c.keywords} | {
                "_pos": [ast.unparse(a) for a in c.args]})
    (preview,) = by_target["_pb_nc"]
    assert preview["_pos"] == ["_pb_pv"] and preview["book_known"] == "_pb_pv > 0"
    # the add-concentration leg is sized on the PRE-trade book: pre-trade holdings
    # and prices, never the session holdings the confirm just overwrote
    leg = [v for v in by_target["_f255_nc"] if v.get("holdings_df") == "_pb_pre_h_df"]
    assert len(leg) == 1 and leg[0]["port_df"] == "_pb_cc_pdf"
    assert "_pb_pre_h_df = _pb_h_df.copy()" in _APP
    assert _APP.index("_pb_pre_h_df = _pb_h_df.copy()") < _APP.index(
        "_pb_h_df.at[_pb_h_idx, \"Shares\"] = (")


def test_watchlist_and_analysis_sites_read_net_and_state():
    assert ('_f255_wl_net_cap, _f255_wl_basis = _f255_wl_nc["net"], _f255_wl_nc["state"]'
            in _APP)
    assert "_f255_nc = _effective_net_capital(portfolio_value)" in _APP


# ---------------------------------------------------------------- drift banner
def test_render_broker_drift_takes_and_gets_a_sizing_clause():
    d = _top_def("_render_broker_drift")
    assert [a.arg for a in d.args.args] == ["placeholder", "verdict", "sizing_clause"]
    assert ast.unparse(d.args.defaults[-1]) == "None"
    calls = _calls(_TREE, "_render_broker_drift")
    assert len(calls) == 1
    kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
    assert kw == {"sizing_clause":
                  "_broker_frame_view.drift_sizing_clause(_f255_nc['frame'])"}
    seg = ast.unparse(d)
    # the clause is appended only after the "(full reconciliation)." text, in the
    # mismatch warning; never in the captions
    names = [n for n in ast.walk(d) if isinstance(n, ast.Name) and n.id == "sizing_clause"]
    assert len(names) == 2                     # the test and the append
    assert "(full reconciliation)." in _APP
    i_full = _APP.index("(full reconciliation).")
    assert 'sizing_clause if sizing_clause else ""' in _APP[i_full:i_full + 600]


def test_home_drift_block_reads_the_module_level_snapshot_loader():
    tries = [n for n in ast.walk(_TREE) if isinstance(n, ast.Try)
             and _calls(n, "decide_drift_banner")]
    assert len(tries) == 1
    assert len(_calls(tries[0], "_cached_broker_snapshot")) == 1
    assert _calls(_top_def("_effective_net_capital"), "_cached_broker_snapshot")


def test_the_sizing_snapshot_is_loaded_in_one_place_only():
    loads = _calls(_TREE, "load_broker_position_snapshot")
    # the cached loader, plus the Trade Journal's asset-type bridge (`kinds` only;
    # not a sizing read)
    assert len(loads) == 2
    assert sum(c in list(ast.walk(_top_def("_cached_broker_snapshot"))) for c in loads) == 1
    assert not _calls(_top_def("_effective_net_capital"), "load_broker_position_snapshot")
    assert not _calls(_top_def("_refresh_portfolio_cache_after_trade"),
                      "load_broker_position_snapshot")


# ---------------------------------------------------------------- memo
def test_synth_sig_carries_both_stamps_and_the_schema_is_22():
    m = re.search(r"_synth_sig = \((.*?)\n    \)\n", _APP, re.S)
    assert m
    body = m.group(1)
    assert 'str(_f255_nc["cash_as_of"])' in body
    assert 'str(_f255_nc["captured_at"])' in body
    assert re.search(r"_SYNTH_SCHEMA_VER = 43\b", _APP)  # 40 -> 41 by brief-consolidation C3


def test_sizing_versions_are_6():
    assert daily_budget.BUDGET_SIZING_VERSION == 6
    assert daily_briefing.SIZING_FORMULA_VERSION == 6


# ---------------------------------------------------------------- account page
def test_account_readout_uses_the_helper_frame_not_its_own_read():
    assert not re.search(r"^from stock_analyzer import broker_frame as _broker_frame$",
                         _APP, re.M)
    assert "BROKER_FRAME_PAIR_MAX_MINUTES" not in _APP
    assert "_broker_frame.broker_frame(" not in _APP
    (call,) = [c for c in _calls(_TREE, "_effective_net_capital")
               if _assign_target_of(c) == "<subscript>"]
    kw = {k.arg: ast.unparse(k.value) for k in call.keywords}
    assert [ast.unparse(a) for a in call.args] == ["_equity"]
    assert kw == {"port_df": "_acc_pdf", "acct_rec": "_acct"}
    assert '_effective_net_capital(\n                    _equity, port_df=_acc_pdf, acct_rec=_acct)["frame"]' in _APP


def test_cash_note_form_uses_the_view_helpers_at_both_saves():
    assert len(_calls(_TREE, "cash_note_default")) == 1
    saves = _calls(_TREE, "cash_note_to_save")
    # the direct save and the pending ("Save anyway") tuple
    assert len(saves) == 2
    assert all(ast.unparse(c.args[0]) == "_new_note" for c in saves)
    assert "_new_note or None" not in _APP
    # every save_account_cash call from this form gets the helper's result
    for c in _calls(_TREE, "save_account_cash"):
        txt = ast.unparse(c)
        assert "cash_note_to_save" in txt or ", _pn" in txt or "_pn)" in txt, txt


def test_user_guide_no_longer_calls_the_check_a_shadow():
    assert "Net capital check (shadow)" not in _APP
    assert "shown only, never used for sizing" not in _APP
    assert "lower of your journal's figure and Robinhood's own positions" in _APP


# ---------------------------------------------------------------- called-by-broker reason
def test_buy_preview_names_the_robinhood_cause_when_the_frame_calls_the_budget(monkeypatch):
    import datetime

    import pandas as pd

    from stock_analyzer import broker_frame as bf
    from stock_analyzer import budget_view as bv

    def _called(reason):
        return lambda *a, **k: {"state": "called", "basis": -1.0, "reason": reason}

    today = datetime.date(2026, 11, 16)
    now = pd.Timestamp("2026-11-16T15:00:00Z")
    empty = pd.DataFrame(columns=["traded_at", "action", "ticker", "shares", "price",
                                  "traded_at_time_kind"])
    monkeypatch.setattr(bv.effective_capital, "reconciled_basis",
                        _called(bf.CALLED_BY_BROKER_REASON))
    out = bv.buy_preview({}, empty, 100.0, today, now)
    assert out["state"] == "unknown"
    assert bf.CALLED_BY_BROKER_REASON in out["text"]
    assert daily_budget.CALLED_REASON != bf.CALLED_BY_BROKER_REASON
    # a journal-only "called" (or a basis with no reason) keeps the old wording
    monkeypatch.setattr(bv.effective_capital, "reconciled_basis", _called(None))
    out = bv.buy_preview({}, empty, 100.0, today, now)
    assert daily_budget.CALLED_REASON in out["text"]
