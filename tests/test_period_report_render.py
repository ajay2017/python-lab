"""Wiring + render-helper tests for the period report's app.py render (C4):
S1 period result, S3 leverage & margin, the trust strip and the export.
app.py is never imported (no test does) -- the wiring is pinned by AST, in the
style of tests/test_grow_today_app_wiring.py.  Plan: docs/plans/period-report.md.
"""
from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from stock_analyzer import period_attribution as pa
from tests._ast_cache import parse_file_cached, source_segment

pytestmark = pytest.mark.fast

_APP = Path(__file__).parent.parent / "app.py"
_SRC = _APP.read_text(encoding="utf-8")
_TREE = parse_file_cached(_APP)
for _n in ast.walk(_TREE):
    for _c in ast.iter_child_nodes(_n):
        _c._parent = _n  # type: ignore[attr-defined]


def _attr_calls(owner: str, attr: str, root=_TREE):
    return [n for n in ast.walk(root)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr and isinstance(n.func.value, ast.Name) and n.func.value.id == owner]


def _kw(call, name):
    return next((k.value for k in call.keywords if k.arg == name), None)


def _enclosing_try(node):
    p = getattr(node, "_parent", None)
    while p is not None:
        if isinstance(p, ast.Try):
            return p
        p = getattr(p, "_parent", None)
    return None


# --- wiring ------------------------------------------------------------------

def test_page_calls_the_engine_once_each():
    for fn in ("reconcile_period", "leverage_section", "trust_strip", "waterfall_rows",
               "leverage_tiles", "format_period_csv", "format_period_markdown", "resolve_period"):
        assert len(_attr_calls("_pa_mod", fn)) == 1, fn


def test_engine_calls_are_inside_a_try_that_catches_exception():
    for fn in ("reconcile_period", "leverage_section", "trust_strip"):
        call = _attr_calls("_pa_mod", fn)[0]
        t = _enclosing_try(call)
        assert t is not None, fn
        assert any(isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in t.handlers), fn


def _assigned_from(name: str):
    """Every Call assigned to `name` (simple Name target)."""
    out = []
    for n in ast.walk(_TREE):
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets):
            out.append(n.value)
    return out


def test_each_loader_is_the_none_on_failure_variant():
    rc = _attr_calls("_pa_mod", "reconcile_period")[0]
    inc = _kw(rc, "income_events")
    daily = _kw(rc, "daily_snapshots_df")
    assert isinstance(inc, ast.Name) and isinstance(daily, ast.Name)
    srcs = {inc.id: _assigned_from(inc.id), daily.id: _assigned_from(daily.id)}
    assert [v.func.attr for v in srcs[inc.id]] == ["load_snaptrade_income_events_or_none"]
    assert [v.func.attr for v in srcs[daily.id]] == ["load_daily_snapshots_or_none"]
    trades = _kw(rc, "trades_df")
    assert [v.func.attr for v in _assigned_from(trades.id)] == ["load_trades_or_none"]
    # flows come from the dedup-check loader through the day cache, never db.load_account_flows()
    flows = _kw(rc, "flows")
    assert isinstance(flows, ast.Name) and flows.id == "_perf_flows_rows"
    assert "db.load_account_flows_for_dedup_check()" in _SRC
    # and the period report never reads the lenient flows loader
    t = _enclosing_try(rc)
    assert not [c for c in ast.walk(t) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr in ("load_account_flows", "load_snaptrade_income_events", "load_trades")]
    # the account snapshots are the day-cached full (unscoped) frame
    snaps = _kw(rc, "account_snapshots_df")
    assert isinstance(snaps, ast.Name) and snaps.id == "_perf_acct_snap_full_df"


def test_reference_leverage_is_the_constant():
    ls = _attr_calls("_pa_mod", "leverage_section")[0]
    assert isinstance(ls.args[3], ast.Name) and ls.args[3].id == "LEVERAGE_REFERENCE_TARGET"
    # the "Days above N.Nx" tile label reads the same constant
    assert "Days above {LEVERAGE_REFERENCE_TARGET:.1f}x" in _SRC


def test_no_or_collapse_around_period_inputs():
    t = _enclosing_try(_attr_calls("_pa_mod", "reconcile_period")[0])
    for n in ast.walk(t):
        if isinstance(n, ast.BoolOp) and isinstance(n.op, ast.Or):
            for v in n.values:
                assert not (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                            and v.func.attr == "get"), ast.unparse(n)
                assert not isinstance(v, (ast.List, ast.Dict)), ast.unparse(n)


def test_old_leverage_drift_section_is_not_rendered_twice():
    # the old standalone header is gone ...
    heads = [n for n in ast.walk(_TREE) if isinstance(n, ast.Constant) and n.value == "#### 🛡️ Leverage & Margin Cushion Drift"]
    assert heads == []
    # ... and its data (leverage_drift) is read exactly once, inside S3
    subs = [n for n in ast.walk(_TREE) if isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Name) and n.value.id == "_perf_review"
            and isinstance(n.slice, ast.Constant) and n.slice.value == "leverage_drift"]
    assert len(subs) == 1


def _more_expander():
    for n in ast.walk(_TREE):
        if isinstance(n, ast.With):
            for it in n.items:
                c = it.context_expr
                if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "expander"
                        and c.args and isinstance(c.args[0], ast.Constant)
                        and c.args[0].value == "More from this period"):
                    return n
    return None


def test_existing_sections_live_inside_more_from_this_period():
    w = _more_expander()
    assert w is not None
    keys = {n.slice.value for n in ast.walk(w) if isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Name) and n.value.id == "_perf_review"
            and isinstance(n.slice, ast.Constant)}
    assert {"account_return", "account_return_monthly", "return_vs_spy", "trade_behavior", "recs",
            "gates", "risk_drift", "risk_discipline"} <= keys
    # the new S1/S3 engine calls are NOT inside it
    for fn in ("reconcile_period", "leverage_section", "waterfall_rows", "leverage_tiles", "split_moves"):
        assert _attr_calls("_pa_mod", fn, root=w) == [], fn


def _fn_def(name):
    return next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_every_period_dollar_goes_through_the_masking_helper():
    # the helper masks real values via _m and leaves None readable ("unknown")
    helper = ast.unparse(_fn_def("_m_usd"))
    assert "_m(" in helper and "is None" in helper
    t = _enclosing_try(_attr_calls("_pa_mod", "reconcile_period")[0])
    region = t._parent  # the else-branch body holding the S1/S3 render
    # no raw usd_plain / _m(usd_plain) left in the render: dollars use _m_usd only
    assert [c for c in ast.walk(region) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            and c.func.attr == "usd_plain"] == []
    assert len([c for c in ast.walk(region) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                and c.func.id == "_m_usd"]) >= 10


def test_trust_strip_is_masked_by_mask_usd_behind_the_privacy_flag():
    seg = None
    for n in ast.walk(_TREE):
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_pr_strip" for t in n.targets):
            seg = n
    assert seg is not None
    src = ast.unparse(seg)
    assert "mask_usd" in src and "_privacy" in src
    # conditional: mask_usd only on the privacy branch, the raw label otherwise
    ifexp = [n for n in ast.walk(seg) if isinstance(n, ast.IfExp)]
    assert ifexp and "_privacy" in ast.unparse(ifexp[0].test)
    assert "mask_usd" in ast.unparse(ifexp[0].body) and "label" in ast.unparse(ifexp[0].orelse)


def test_system_trust_chip_is_wired_and_filtered_when_unknown():
    t = _enclosing_try(_attr_calls("_pa_mod", "trust_strip")[0])
    src = ast.unparse(t)
    assert "get_health" in src and "chip_severity" in src and "is_readonly" in src
    assert "'warn': 'degraded'" in src and "'down': 'failing'" in src
    assert "!= 'health_now'" in src  # an unreadable chip is dropped, never a grey one


def test_trust_chips_are_not_put_in_raw_html():
    # the chip strip is a markdown line; no unsafe_allow_html carries a chip label
    src_lines = [ln for ln in _SRC.splitlines() if "_pr_strip" in ln]
    assert src_lines and not any("unsafe_allow_html" in ln for ln in src_lines)


# --- helpers -----------------------------------------------------------------

def test_usd_plain_sign_before_dollar_and_unknown():
    assert pa.usd_plain(-1314.4) == "-$1,314"
    assert pa.usd_plain(4.0, plus=True) == "+$4"
    assert pa.usd_plain(4.0) == "$4"
    assert pa.usd_plain(0.0, plus=True) == "$0"
    assert pa.usd_plain(None) == "unknown"
    assert "\\" not in pa.usd_plain(-5)


def test_mask_usd_removes_escaped_dollars_only():
    assert pa.mask_usd("Unexplained -\\$57 (0.7%)") == "Unexplained •••••• (0.7%)"
    assert "$" not in pa.mask_usd("cushion \\$2,410 and \\$1,000.50")
    assert pa.mask_usd("Account records 5/5") == "Account records 5/5"


def _ok_recon(**over):
    rec = pa._empty_recon("ok", date(2026, 10, 12), date(2026, 10, 16))
    rec.update({
        "d0": date(2026, 10, 9), "d1": date(2026, 10, 16),
        "equity_start": 8518.0, "equity_end": 7204.0, "equity_change": -1314.0, "equity_pct": -15.4,
        "pos_pnl": {"held_through": -683.0, "sold_leg": -318.0, "bought_leg": -196.0,
                    "round_trip": -64.0, "total": -1261.0},
        "leverage_split": {"borrowed": -500.5, "own": -500.5, "unsplit": 0.0, "days_split": 5,
                           "days_unsplit": 0, "avg_leverage": 3.0},
        "flows": {"net": 0.0, "n": 0},
        "income": {"dividends": 4.0, "interest_charged": 0.0, "interest_earned": 0.0, "fees": 0.0,
                   "n": 1, "last_event_date": None, "n_reclassified_from_fee": 0,
                   "last_interest_charged_month": None, "interest_coverage_gap_months": []},
        "residual": {"total": -57.0, "cash": -74.0, "book": 17.0, "pct_of_start": -0.7},
    })
    rec.update(over)
    return rec


def _top(rows):
    return [r for r in rows if not r["indent"] and not r["breakdown"]]


def test_waterfall_rows_add_up_to_the_net_change():
    rows = pa.waterfall_rows(_ok_recon())
    labels = [r["label"] for r in rows]
    assert labels[0] == "Price moves on positions held through"
    assert labels[1] == "Last-day moves on positions you sold"
    assert labels[2].startswith("Moves on positions held at the prior close")
    assert "on borrowed money (avg 3.0x leverage)" in labels
    assert labels[-1] == "Net equity change" and rows[-1]["total"]
    assert sum(r["value"] for r in _top(rows)[:-1]) == pytest.approx(rows[-1]["value"])
    # the subtotal is a breakdown of the two lines above, and its children sum to it
    sub = rows[2]
    assert sub["breakdown"] and sub["value"] == pytest.approx(rows[0]["value"] + rows[1]["value"])
    kids = [r["value"] for r in rows[3:] if r["indent"]]
    assert sum(kids) == pytest.approx(sub["value"])


def test_waterfall_unknown_lines_are_none_not_zero():
    rows = pa.waterfall_rows(_ok_recon(flows=None, income=None, residual=None, status="partial"))
    by = {r["label"]: r["value"] for r in rows}
    for k in ("Deposits / withdrawals", "Dividends", "Margin interest posted", "Fees", "Unexplained"):
        assert by[k] is None, k
    assert pa.usd_plain(by["Fees"]) == "unknown"


def test_waterfall_pre_coverage_and_offline_show_only_price_lines():
    pre = _ok_recon(status="pre_coverage", equity_change=None, residual=None)
    labels = [r["label"] for r in pa.waterfall_rows(pre)]
    assert "Net equity change" not in labels and "Unexplained" not in labels
    assert labels[0] == "Price moves on positions held through"
    assert pa.waterfall_rows(pa._empty_recon("offline", None, None)) == []
    # no valid account record at any day -> borrowed/own unknown, never $0
    by = {r["label"]: r for r in pa.waterfall_rows(_ok_recon(
        status="pre_coverage", equity_change=None, residual=None,
        leverage_split={"borrowed": 0.0, "own": 0.0, "unsplit": -1.0, "days_split": 0,
                        "days_unsplit": 5, "avg_leverage": None}))}
    assert by["on borrowed money"]["value"] is None and by["on your own money"]["value"] is None
    assert by["not split (no valid account record that day)"]["value"] == pytest.approx(-1001.0)


def test_interest_posted_sign_and_none():
    inc = _ok_recon()["income"]
    assert pa.interest_posted(_ok_recon(income=dict(inc, interest_charged=120.0, interest_earned=5.0))) == -115.0
    assert pa.interest_posted(_ok_recon(income=None)) is None


def test_pre_coverage_text_names_the_reason():
    assert "session before" in pa.pre_coverage_reason_text({"pre_coverage_reason": "no_start_record"})
    assert "2026-10-09" in pa.pre_coverage_reason_text({"pre_coverage_reason": "no_end_record_yet",
                                                         "d0": date(2026, 10, 9)})
    assert pa.pre_coverage_reason_text({"pre_coverage_reason": "weird"})


def _lev(call_pct, cushion=2410.0):
    return {"status": "ok",
            "start": {"date": date(2026, 10, 9), "leverage": 3.0},
            "end": {"date": date(2026, 10, 16), "leverage": 2.61},
            "min": {"date": date(2026, 10, 16), "leverage": 2.61},
            "max": {"date": date(2026, 10, 12), "leverage": 3.21},
            "n_days_recorded": 5, "days_above_reference": 5,
            "closest_call": ({"date": date(2026, 10, 13), "call_distance_pct": call_pct,
                              "cushion": cushion, "leverage": 3.0} if call_pct is not None else None)}


def test_leverage_tiles_drop_away_vs_in_call():
    t = pa.leverage_tiles(_lev(-12.9))
    assert t["range_value"] == "2.61x - 3.21x"
    assert t["call_value"] == "-12.9%" and t["call_when"] == "a 12.9% drop away on 2026-10-13"
    assert t["cushion"] == 2410.0 and t["days_value"] == "5 of 5"
    c = pa.leverage_tiles(_lev(3.0))
    assert c["call_value"] == "In a margin call" and "drop away" not in c["call_when"]
    zero = pa.leverage_tiles(_lev(0.0))
    assert zero["call_value"] == "In a margin call"  # >= 0 is already in call


def test_leverage_tiles_unknown_states():
    assert pa.leverage_tiles(_lev(None))["call_value"] == "unknown"
    off = pa.leverage_tiles({"status": "offline"})
    assert off["range_value"] == "unknown" and off["days_value"] == "unknown"
    assert pa.leverage_tiles(None)["range_value"] == "unknown"


# --- engine-driven (week_world) ---------------------------------------------

def _week_recon(drop_d0=False):
    from tests.test_period_attribution import FRI, INT_EV, MON, recon, week_world
    t, a, dl = week_world()
    if drop_d0:
        a = a[a["snapshot_date"] != FRI.isoformat()]
    return recon(t, a, dl, ps=MON, pe=MON, income=INT_EV, flows=[])


def test_engine_waterfall_children_sum_to_parents_and_top_level_to_net():
    r = _week_recon()
    assert r["status"] == "ok"
    rows = pa.waterfall_rows(r)
    sub = next(x for x in rows if x["breakdown"])
    held = next(x for x in rows if x["label"] == "Price moves on positions held through")["value"]
    sold = next(x for x in rows if x["label"] == "Last-day moves on positions you sold")["value"]
    assert sub["value"] == pytest.approx(held + sold)
    kids = [x["value"] for x in rows if x["indent"]]
    assert all(k is not None for k in kids)
    assert sum(kids) == pytest.approx(sub["value"])       # B1: borrowed + own + unsplit == held + sold
    top = _top(rows)
    assert sum(x["value"] for x in top[:-1]) == pytest.approx(top[-1]["value"])  # lines add to the net
    assert top[-1]["value"] == pytest.approx(r["equity_change"])
    # margin interest note + deposits note come from the data
    by = {x["label"]: x for x in rows}
    assert by["Margin interest posted"]["note"] == "posts monthly"
    assert by["Deposits / withdrawals"]["note"] == "none recorded"


def test_engine_split_unknown_without_a_start_account_row():
    r = _week_recon(drop_d0=True)
    assert r["status"] == "pre_coverage"
    sm = pa.split_moves(r)
    assert sm["borrowed"] is None and sm["own"] is None          # B2: unknown, not $0
    assert sm["unsplit"] == pytest.approx(sm["held_sold"])
    rows = pa.waterfall_rows(r)
    by = {x["label"]: x for x in rows}
    assert by["on borrowed money"]["value"] is None and by["on your own money"]["value"] is None
    assert "Net equity change" not in by
    assert pa.usd_plain(by["on borrowed money"]["value"]) == "unknown"


def test_interest_gap_note_replaces_posts_monthly():
    inc = dict(_ok_recon()["income"], interest_coverage_gap_months=["2026-10"])
    by = {x["label"]: x for x in pa.waterfall_rows(_ok_recon(income=inc))}
    assert by["Margin interest posted"]["note"] == "not recorded for 2026-10"


def test_trades_chip_wording_and_health_chip_state():
    r = _ok_recon()
    chips = {c["key"]: c for c in pa.trust_strip(r, None, trades_truncated=False, health_now="degraded")}
    assert chips["trades"]["label"] == "Trade journal read in full"
    assert chips["health_now"]["label"] == "System Trust now: degraded" and chips["health_now"]["state"] == "warn"
