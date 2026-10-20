r"""Widget / metric / expander labels are markdown, so two bare "$" in one label
pair up as LaTeX: both signs vanish and the text between them renders in a
math font (2026-10-20: the Trade Journal's "Cost Basis / share ( ) <- auto-filled
183.10"; also the tax-harvest expander). Escape as \$. Only literal "$" in the
label's own string parts are checked -- a "$" produced by an interpolated value
is not visible here."""
import ast
from pathlib import Path

from tests._ast_cache import parse_file_cached

_APP = Path(__file__).resolve().parent.parent / "app.py"
_LABEL_CALLS = {
    "number_input", "text_input", "text_area", "selectbox", "multiselect", "radio",
    "checkbox", "toggle", "slider", "select_slider", "date_input", "button",
    "download_button", "form_submit_button", "metric", "expander", "tabs",
    "file_uploader", "popover",
}


def _literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "\x00" for v in node.values)
    return None


def _bare_dollars(s):
    return sum(1 for i, ch in enumerate(s) if ch == "$" and (i == 0 or s[i - 1] != "\\"))


def _offenders(tree):
    assigns = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            assigns.setdefault(n.targets[0].id, []).append(n.value)
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in _LABEL_CALLS):
            continue
        cands = list(n.args[:1]) + [k.value for k in n.keywords if k.arg == "label"]
        for c in cands:
            if isinstance(c, (ast.List, ast.Tuple)):
                nodes = list(c.elts)
            elif isinstance(c, ast.Name):
                nodes = assigns.get(c.id, [])   # a label held in a variable
            else:
                nodes = [c]
            for x in nodes:
                s = _literal(x)
                if s is not None and _bare_dollars(s) >= 2:
                    out.append((n.lineno, s[:80]))
    return out


def test_no_label_pairs_two_bare_dollar_signs():
    assert _offenders(parse_file_cached(_APP)) == []


def test_the_check_fires_on_a_pair_and_spares_an_escaped_one():
    bad = ast.parse('st.number_input(f"Cost ($) auto-filled ${x:.2f}")\n'
                    'lbl = "a $1 b $2"\nst.metric(lbl, 1)\n')
    assert len(_offenders(bad)) == 2
    # mirrors app.py's fix exactly: source text f"... (\\$) ... \\${x}"
    ok = ast.parse(r'st.number_input(f"Cost (\\$) auto-filled \\${x:.2f}")' "\n"
                   'st.metric("Price ($)", 1)\n')
    assert _offenders(ok) == []
