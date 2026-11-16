"""AcctMask: the 💰 Account page must not print a real dollar amount while the sidebar
"🙈 Hide values" toggle is on.  app.py is never imported (no test does) -- this pins
the wiring by AST, in the style of tests/test_period_report_render.py.

Two things are pinned:
  * the CLASS: every f-string in the Account page block with a `$` directly before a
    formatted value (a dollar amount) is either masked (inside a call to `_m`,
    `_m_usd`, `_cvm_d` or `_cvm_dm`) or sits in the branch that only runs with
    privacy off (its enclosing if / conditional expression tests a privacy flag);
  * the inputs that cannot mask their own number (cash form, baseline form, the
    paydown slider) are rendered only on the privacy-off branch of `_acct_priv`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._ast_cache import parse_file_cached, source_segment

pytestmark = pytest.mark.fast

_APP = Path(__file__).parent.parent / "app.py"
_SRC = _APP.read_text(encoding="utf-8")
_TREE = parse_file_cached(_APP)

_LINES = _SRC.splitlines()
_START = next(i for i, ln in enumerate(_LINES, 1) if ln.startswith('elif page == "💰 Account":'))
_END = next(i for i, ln in enumerate(_LINES, 1)
            if i > _START and ln.startswith("elif page == "))

_MASKERS = {"_m", "_m_usd", "_cvm_d", "_cvm_dm"}
_FLAGS = {"_acct_priv", "_chart_priv", "_cvm_priv", "_sii_chart_priv"}

# Sites that are genuinely safe but not expressible by the two rules above. Keyed by a
# stable substring of the f-string's source; every entry needs a reason.  Empty today.
_ALLOWLIST: dict[str, str] = {}

for _n in ast.walk(_TREE):
    for _c in ast.iter_child_nodes(_n):
        _c._parent = _n  # type: ignore[attr-defined]


def _in_block(node) -> bool:
    ln = getattr(node, "lineno", None)
    return ln is not None and _START < ln < _END


def _ancestors(node):
    p = getattr(node, "_parent", None)
    while p is not None:
        yield p
        p = getattr(p, "_parent", None)


def _src(node) -> str:
    return source_segment(_SRC, node) or ""


def _dollar_amount_fstrings():
    """f-strings in the Account block with a `$` literal directly before a FormattedValue.
    A plotly placeholder like "$%{y:,.0f}" is a plain Constant, so it never counts."""
    out = []
    for n in ast.walk(_TREE):
        if not (isinstance(n, ast.JoinedStr) and _in_block(n)):
            continue
        vals = n.values
        for i in range(1, len(vals)):
            prev = vals[i - 1]
            if (isinstance(vals[i], ast.FormattedValue) and isinstance(prev, ast.Constant)
                    and isinstance(prev.value, str) and prev.value.endswith("$")):
                out.append(n)
                break
    return out


def _is_masked(node) -> bool:
    for a in _ancestors(node):
        if (isinstance(a, ast.Call) and isinstance(a.func, ast.Name)
                and a.func.id in _MASKERS):
            return True
    return False


def _names(expr):
    return {x.id for x in ast.walk(expr) if isinstance(x, ast.Name)}


def _is_privacy_test(test, flags) -> bool:
    if _names(test) & flags:
        return True
    # the chart code also reads the session key directly: st.session_state.get("_privacy")
    for x in ast.walk(test):
        if (isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr == "get"
                and x.args and isinstance(x.args[0], ast.Constant) and x.args[0].value == "_privacy"):
            return True
    return False


def _runs_with_privacy_off(node, flags=_FLAGS) -> bool:
    """True when some enclosing if / IfExp tests a privacy flag and `node` sits in the
    branch that runs with privacy OFF: the else-branch of a plain flag test, or the
    body of a `not flag` test."""
    child = node
    for a in _ancestors(node):
        if isinstance(a, (ast.If, ast.IfExp)) and _is_privacy_test(a.test, flags):
            negated = isinstance(a.test, ast.UnaryOp) and isinstance(a.test.op, ast.Not)
            body = a.body if isinstance(a, ast.If) else [a.body]
            orelse = a.orelse if isinstance(a, ast.If) else [a.orelse]
            in_body = any(child is b for b in body)
            in_orelse = any(child is o for o in orelse)
            if (negated and in_body) or (not negated and in_orelse):
                return True
        child = a
    return False


def test_block_is_found_and_has_amounts():
    assert _END - _START > 500
    assert len(_dollar_amount_fstrings()) >= 3


def test_every_account_dollar_amount_is_masked_or_privacy_off_only():
    bad = []
    for n in _dollar_amount_fstrings():
        if _is_masked(n) or _runs_with_privacy_off(n):
            continue
        text = _src(n)
        if any(key in text for key in _ALLOWLIST):
            continue
        bad.append(f"app.py:{n.lineno}: {text[:110]}")
    assert not bad, "Account page prints a real dollar amount with Hide values on:\n" + "\n".join(bad)


def test_guard_rejects_an_unmasked_amount():
    """The rule itself: a bare f-string amount is neither masked nor privacy-off-only."""
    tree = ast.parse('def f():\n    st.caption(f"debit ${abs(c):,.0f}")\n')
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            c._parent = n  # type: ignore[attr-defined]
    js = next(n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr))
    assert not _is_masked(js) and not _runs_with_privacy_off(js)
    tree = ast.parse('def f():\n    st.caption(_m(f"debit ${abs(c):,.0f}"))\n')
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            c._parent = n  # type: ignore[attr-defined]
    js = next(n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr))
    assert _is_masked(js)


# --- the inputs that cannot mask their own number ------------------------------

def _calls(attr: str):
    return [n for n in ast.walk(_TREE)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr and _in_block(n)]


def _first_arg_is(call, text: str) -> bool:
    return bool(call.args) and isinstance(call.args[0], ast.Constant) and call.args[0].value == text


def _kw_is(call, name: str, text: str) -> bool:
    return any(k.arg == name and isinstance(k.value, ast.Constant) and k.value.value == text
               for k in call.keywords)


def _hidden_when_private(node) -> bool:
    return _runs_with_privacy_off(node, flags={"_acct_priv"})


def test_cash_form_and_handler_only_render_with_privacy_off():
    forms = [c for c in _calls("form") if _first_arg_is(c, "_account_cash_form")]
    assert len(forms) == 1 and _hidden_when_private(forms[0])
    handlers = [n for n in ast.walk(_TREE) if isinstance(n, ast.If) and _in_block(n)
                and isinstance(n.test, ast.Name) and n.test.id == "_cash_submit"]
    assert len(handlers) == 1 and _hidden_when_private(handlers[0])


def test_baseline_form_and_handler_only_render_with_privacy_off():
    forms = [c for c in _calls("form") if _first_arg_is(c, "_account_baseline_form")]
    assert len(forms) == 1 and _hidden_when_private(forms[0])
    handlers = [n for n in ast.walk(_TREE) if isinstance(n, ast.If) and _in_block(n)
                and isinstance(n.test, ast.Name) and n.test.id == "_bsub"]
    assert len(handlers) == 1 and _hidden_when_private(handlers[0])


def test_paydown_slider_only_renders_with_privacy_off():
    sliders = [c for c in _calls("slider") if _kw_is(c, "key", "_cvm_paydown_slider")]
    assert len(sliders) == 1 and _hidden_when_private(sliders[0])


def test_readonly_guard_still_wraps_the_forms():
    """The privacy branch sits INSIDE db.is_readonly()'s else: a viewer sees no form."""
    for name in ("_account_cash_form", "_account_baseline_form"):
        form = next(c for c in _calls("form") if _first_arg_is(c, name))
        guarded = False
        child = form
        for a in _ancestors(form):
            if (isinstance(a, ast.If) and isinstance(a.test, ast.UnaryOp)
                    and isinstance(a.test.op, ast.Not) and "is_readonly" in _src(a.test)
                    and any(child is b for b in a.body)):
                guarded = True
            child = a
        assert guarded, f"{name} lost its `if not db.is_readonly()` guard"


def test_user_guide_names_the_hidden_inputs():
    assert "the cash box is hidden (an input can't mask its number)" in _SRC
