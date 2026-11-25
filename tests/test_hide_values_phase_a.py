"""🙈 Hide values, phase A (owner 2026-11-25): app.py-formatted MONEY goes behind `_m` /
`_m_dol`; per-share PRICES stay visible. app.py is never imported by tests, so this file
pins the wiring by AST / source grep and EXECUTES the small helpers (`_m`, `_m_dol`,
`_pt_money`) cut out of app.py against a stubbed session.

* Every `_privacy` read has a True default and `_privacy` is written once, before any
  module-level reader (a fresh session must hide, not show).
* One representative money site per page wraps its figure; the price beside it does not.
* `_fmt_action` (phase B: its text feeds the AI Snapshot prompt and its cache hash) is
  never masked.
"""
import ast
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._ast_cache import parse_file_cached, source_segment

pytestmark = pytest.mark.fast

_APP = Path(__file__).resolve().parents[1] / "app.py"
_MASK = "••••••"


def _src() -> str:
    return _APP.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _func_src(name: str) -> str:
    tree = parse_file_cached(_APP)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return source_segment(_src(), node)


def _session_get_calls(tree):
    """Every `st.session_state.get("_privacy", ...)` call in app.py."""
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get"
                and isinstance(n.func.value, ast.Attribute)
                and n.func.value.attr == "session_state"
                and n.args and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == "_privacy"):
            out.append(n)
    return out


def _exec_helpers(private, *names):
    """Run the named app.py helper functions against a stub `st` whose session holds
    `_privacy` = `private` (None = key absent)."""
    state = {} if private is None else {"_privacy": private}
    ns = {"st": SimpleNamespace(session_state=state)}
    for name in names:
        exec(compile(_func_src(name), "app.py", "exec"), ns)
    return ns


# ── 1. the `_privacy` default ────────────────────────────────────────────────
def test_every_privacy_read_defaults_to_true():
    tree = parse_file_cached(_APP)
    calls = _session_get_calls(tree)
    assert len(calls) >= 20, "expected the known `_privacy` readers"
    for c in calls:
        assert len(c.args) == 2 and isinstance(c.args[1], ast.Constant) \
            and c.args[1].value is True, \
            f"app.py line {c.lineno}: `_privacy` read without a True default"


def test_no_bare_subscript_read_of_privacy():
    tree = parse_file_cached(_APP)
    for n in ast.walk(tree):
        if (isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load)
                and isinstance(n.value, ast.Attribute) and n.value.attr == "session_state"
                and isinstance(n.slice, ast.Constant) and n.slice.value == "_privacy"):
            pytest.fail(f"app.py line {n.lineno}: bare st.session_state['_privacy'] read")


def test_privacy_setdefault_precedes_every_module_level_reader():
    tree = parse_file_cached(_APP)
    sets = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "setdefault" and n.args
            and isinstance(n.args[0], ast.Constant) and n.args[0].value == "_privacy"
            and len(n.args) == 2 and isinstance(n.args[1], ast.Constant)
            and n.args[1].value is True]
    assert len(sets) == 1, "expected exactly one st.session_state.setdefault('_privacy', True)"
    first = sets[0].lineno
    # Readers inside a def run only when called (after the module-level setdefault);
    # the module-level ones must come after it.
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for c in _session_get_calls(stmt):
            assert c.lineno >= first, (
                f"app.py line {c.lineno}: `_privacy` read at module level before the "
                f"setdefault on line {first}")


# ── 2. the helpers ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("private,expected", [
    (True, _MASK), (None, _MASK), (False, "$1,235"),
])
def test_m_dol_masks_by_default_and_formats_when_shown(private, expected):
    ns = _exec_helpers(private, "_m", "_m_dol")
    assert ns["_m_dol"](1234.6) == expected


def test_m_dol_markdown_escape_and_format_spec():
    ns = _exec_helpers(False, "_m", "_m_dol")
    assert ns["_m_dol"](1234.6, md=True) == "\\$1,235"
    assert ns["_m_dol"](12.346, ",.2f") == "$12.35"
    assert _exec_helpers(True, "_m", "_m_dol")["_m_dol"](1234.6, md=True) == _MASK


def test_pt_money_masks_only_when_marked_money():
    ns = _exec_helpers(True, "_m", "_pt_money")
    pt = ns["_pt_money"]
    assert pt(1500.0, signed=False) == "\\$1,500"            # a per-share price: visible
    assert pt(1500.0, money=True) == _MASK                    # P&L money: hidden
    assert pt(None, money=True) == "—"                       # unknown is not a figure
    shown = _exec_helpers(False, "_m", "_pt_money")["_pt_money"]
    assert shown(12.5, money=True) == "+\\$12.50"


# ── 3. representative money sites, one per page ──────────────────────────────
_MONEY_SITES = [
    # (page, a source fragment that must be present)
    ("Home Grow Today", "≈{_m_dol(_sz.get('total_cost',0))}"),
    ("Home Rebalance plan", "_m_dol(_row['cut_dollar'], md=True)"),
    ("Evening Debrief", "_m_dol(_ed_sum['deployed'])"),
    ("Cold-load snapshot", '_sc[0].metric("Portfolio Value", _m_dol(_s_total))'),
    ("Signals", "_m_dol(half_val, md=True)"),
    ("Diversification", '"≈" + _m_dol(rec[\'add_dollars\'])'),
    ("Risk Analysis VaR", '"≈ " + _m_dol(_var_dollar) + " / day"'),
    ("Risk Analysis stress", '_st_df["Est. P&L ($)"] = "••••••"'),
    ("Risk Analysis compare", '_cmp_df["Post Value ($)"] = "••••••"'),
    ("Portfolio Overview Rebalancer", '_tr_m[3].metric("$ to Trim",       _m_dol(_tr[\'drift_val\']))'),
    ("Portfolio Overview Rebalancer hover", '"$ Drift: ••••<br>"'),
    ("Portfolio Overview Tax", "_m_dol(_tax['tax_today'])"),
    ("Portfolio Overview Tax table", '_tx_df[_tx_mc] = "••••••"'),
    ("Portfolio Overview Performance", "_m(_signed_usd(_net_alpha_dollar, 0))"),
    ("Analysis position sizing", "_m_dol(ps['actual_risk'])"),
    ("Analysis prior trades", "_pt_money(_pt_ep.get(\"realized_pnl\"), money=True)"),
    ("Watchlist sizing", "{_m_dol(_wl_ps['total_cost'])}"),
    ("Economic Calendar", "_m(_signed_usd(_pb['total_bear_impact'], 0))"),
    ("My Edge", "{_m_dol(abs(_me_dollar_gap))}"),
]


@pytest.mark.parametrize("page,fragment", _MONEY_SITES, ids=[p for p, _ in _MONEY_SITES])
def test_money_site_is_masked(page, fragment):
    assert fragment in _src(), f"{page}: expected the masked money site `{fragment}`"


def test_prices_beside_masked_money_stay_visible():
    src = _src()
    # Grow Today card: the entry zone and stop are prices.
    assert "Entry zone ${_sz.get('entry_lo', _gp['price']):.2f}" in src
    assert "Stop ≈${_sz.get('stop',0):.2f}" in src
    # Watchlist sizing box: "@ $price" and the stop stay.
    assert "@ ${_price:.2f} = " in src and "at stop ${_stop:.2f}" in src
    # Exit price / current price in the prior-trades pointer stay un-masked.
    assert "_pt_money(_xp, signed=False)" in src
    assert "_pt_money(price, signed=False)" in src


# ── 4. phase B stays untouched ───────────────────────────────────────────────
def test_fmt_action_is_never_masked():
    body = _func_src("_fmt_action")
    assert "_m(" not in body and "_m_dol(" not in body and "_privacy" not in body, (
        "_fmt_action feeds the AI Snapshot prompt and its cache hash: it must stay "
        "unmasked (phase B)")
