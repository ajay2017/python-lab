"""C4 (audit A-M1) + C6 (audit A-M3): persisted correlation narratives withhold,
and "clean" correlation screens disclose, on an unverified matrix.

Records withhold, screens disclose. All AST pins read one shared parse of app.py.
"""
from __future__ import annotations

import ast
import itertools
import pathlib

import pytest
from tests._ast_cache import parse_file_cached

from stock_analyzer import portfolio
from stock_analyzer.util import correlation_record_withheld_note

_ROOT = pathlib.Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.xdist_group(name="corr_record_screens_ast")
_APP: dict = {}

_UNCHECKED_KEY = "_corr_unchecked_cache"
_COV_KEY = "_corr_coverage_cache"


def _app():
    if "tree" not in _APP:
        _APP["tree"] = parse_file_cached(_ROOT / "app.py")
    return _APP["tree"]


def _parents(tree):
    out = {}
    for n in ast.walk(tree):
        for ch in ast.iter_child_nodes(n):
            out[ch] = n
    return out


def _is_get_of(value, key):
    return (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
            and value.func.attr == "get" and value.args
            and isinstance(value.args[0], ast.Constant) and value.args[0].value == key)


def _assigns(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]


def _one(seq):
    seq = list(seq)
    assert len(seq) == 1, len(seq)
    return seq[0]


def _is_not(node, name):
    return (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)
            and isinstance(node.operand, ast.Name) and node.operand.id == name)


def _and_with_bare(test, name):
    return (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And)
            and any(isinstance(v, ast.Name) and v.id == name for v in test.values))


def _calls_note(node):
    return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
               and c.func.id == "correlation_record_withheld_note" for c in ast.walk(node))


def _is_verified_call(value):
    return (isinstance(value, ast.Call)
            and ((isinstance(value.func, ast.Name) and value.func.id == "correlation_claim_verified")
                 or (isinstance(value.func, ast.Attribute)
                     and value.func.attr == "correlation_claim_verified")))


def _innermost_if(parents, node):
    n = parents.get(node)
    while n is not None and not isinstance(n, ast.If):
        n = parents.get(n)
    return n


# -- C4 -------------------------------------------------------------------------
_C4 = [
    ("_rs", "_rs_gen_btn", "save_regime_scenario_cache"),
    ("_cs", "_cs_gen_btn", "save_catalyst_stress_cache"),
]


def _button_if(tree, key):
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.If):
            for c in ast.walk(n.test):
                if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                        and c.func.attr == "button"
                        and any(k.arg == "key" and isinstance(k.value, ast.Constant)
                                and k.value.value == key for k in c.keywords)):
                    out.append((n, c))
    return _one(out)


@pytest.mark.parametrize("pfx,btn,save", _C4)
def test_c4_record_ok_fed_by_plain_gets(pfx, btn, save):
    tree = _app()
    call = _one(_assigns(tree, f"{pfx}_record_ok")).value
    assert _is_verified_call(call)
    assert [a.id for a in call.args] == [f"{pfx}_unchecked", f"{pfx}_cov"]
    for var, key in ((f"{pfx}_unchecked", _UNCHECKED_KEY), (f"{pfx}_cov", _COV_KEY)):
        a = _one(_assigns(tree, var))
        assert not isinstance(a.value, ast.BoolOp), "`or []` collapses None into verified-empty"
        assert _is_get_of(a.value, key)


@pytest.mark.parametrize("pfx,btn,save", _C4)
def test_c4_button_disabled_and_click_guard_polarity(pfx, btn, save):
    name = f"{pfx}_record_ok"
    if_node, b = _button_if(_app(), btn)
    disabled = _one(k for k in b.keywords if k.arg == "disabled").value
    assert isinstance(disabled, ast.BoolOp) and isinstance(disabled.op, ast.Or)
    assert any(_is_not(v, name) for v in disabled.values)
    assert not any(isinstance(v, ast.Name) and v.id == name for v in disabled.values)
    assert _and_with_bare(if_node.test, name)


@pytest.mark.parametrize("pfx,btn,save", _C4)
def test_c4_single_save_under_innermost_and_guard(pfx, btn, save):
    tree = _app()
    saves = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == save]
    assert len(saves) == 1
    inner = _innermost_if(_parents(tree), saves[0])
    assert inner is not None and _and_with_bare(inner.test, f"{pfx}_record_ok")


@pytest.mark.parametrize("pfx,btn,save", _C4)
def test_c4_withheld_caption_explains_the_disabled_button(pfx, btn, save):
    name = f"{pfx}_record_ok"
    hits = [n for n in ast.walk(_app())
            if isinstance(n, ast.If) and _is_not(n.test, name)
            and any(_calls_note(s) for s in n.body)]
    assert len(hits) == 1


def test_c4_detector_helpers_reject_inverted_forms():
    ev = lambda s: ast.parse(s, mode="eval").body  # noqa: E731
    assert _is_not(ev("not _rs_record_ok"), "_rs_record_ok")
    assert not _is_not(ev("_rs_record_ok"), "_rs_record_ok")
    assert not _is_not(ev("not _cs_record_ok"), "_rs_record_ok")
    assert _and_with_bare(ev("a and _rs_record_ok"), "_rs_record_ok")
    assert not _and_with_bare(ev("a and not _rs_record_ok"), "_rs_record_ok")
    assert not _and_with_bare(ev("a or _rs_record_ok"), "_rs_record_ok")
    assert _is_verified_call(ev("correlation_claim_verified(a, b)"))
    assert not _is_verified_call(ev("True"))


# -- C6 -------------------------------------------------------------------------
# (text prefix of the clean/negative claim, verified-flag name, claim-rendering fn,
#  fn in the else, note built in the else?, unchecked var, coverage var)
_C6 = [
    ("✅ No major diversification gaps", "_sa_corr_ok", "success", "info", True,
     "_sa_unchecked", "_sa_cov"),
    ("✅ No highly correlated pairs", "_ra_corr_ok", "success", "info", True,
     "_corr_unchecked", "_corr_cov"),
    ("No correlated clusters detected", "_pi_corr_ok", "success", "info", True,
     "_pi_unchecked", "_pi_cov"),
    ("No strongly-correlated names to cascade through.", "_ss_corr_ok", "caption", "caption", False,
     "_ss_unchecked", "_ss_cov"),
    ("No upcoming HIGH-impact macro event", "_cs_record_ok", "caption", "info", True,
     "_cs_unchecked", "_cs_cov"),
]


def _lead_text(arg):
    """Leading literal text of a string arg (a Constant, or an f-string's first piece)."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if (isinstance(arg, ast.JoinedStr) and arg.values
            and isinstance(arg.values[0], ast.Constant)):
        return str(arg.values[0].value)
    return ""


def _claim_call(tree, prefix, fn):
    return _one(
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == fn
        and n.args and _lead_text(n.args[0]).startswith(prefix))


def _has_call(nodes, attr):
    return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
               and c.func.attr == attr for s in nodes for c in ast.walk(s))


def _c6_site_ok(tree, prefix, flag, fn, else_fn="info", note_in_else=True):
    """The clean claim sits in the BODY of an If testing exactly the bare
    verified flag; the VERIFIED branch renders no info/note (so an extra
    unverified-looking message there is caught); the orelse renders the
    neutral message."""
    claim = _claim_call(tree, prefix, fn)
    inner = _innermost_if(_parents(tree), claim)
    if inner is None:
        return False
    t = inner.test
    if not (isinstance(t, ast.Name) and t.id == flag):
        return False
    if not any(claim is c for s in inner.body for c in ast.walk(s)):
        return False
    if _has_call(inner.body, "info") or any(_calls_note(s) for s in inner.body):
        return False
    if not _has_call(inner.orelse, else_fn):
        return False
    if note_in_else:
        return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                   and c.func.attr == "info" and _calls_note(c)
                   for s in inner.orelse for c in ast.walk(s))
    return True


def _flag_inputs_ok(tree, flag, unc, cov):
    """Argument ORDER, and that each input is a plain .get() of the RIGHT
    session key (never an `or`, never the other key)."""
    call = _one(_assigns(tree, flag)).value
    if not (_is_verified_call(call) and [a.id for a in call.args] == [unc, cov]):
        return False
    for var, key, other in ((unc, _UNCHECKED_KEY, _COV_KEY), (cov, _COV_KEY, _UNCHECKED_KEY)):
        mine = [a for a in _assigns(tree, var)
                if isinstance(a.value, ast.Call) and _is_get_of(a.value, key)
                and "session_state" in ast.unparse(a.value.func)]
        if not mine:
            return False
        for a in _assigns(tree, var):
            if isinstance(a.value, ast.BoolOp):
                return False
            if _is_get_of(a.value, other):
                return False
    return True


@pytest.mark.parametrize("prefix,flag,fn,else_fn,note_in_else,unc,cov", _C6)
def test_c6_clean_claim_only_under_positive_verified_flag(prefix, flag, fn, else_fn, note_in_else, unc, cov):
    assert _c6_site_ok(_app(), prefix, flag, fn, else_fn, note_in_else)


@pytest.mark.parametrize("prefix,flag,fn,else_fn,note_in_else,unc,cov", _C6)
def test_c6_flag_fed_by_the_right_inputs_in_order(prefix, flag, fn, else_fn, note_in_else, unc, cov):
    assert _flag_inputs_ok(_app(), flag, unc, cov)


def test_c6_blast_radius_disclosure_and_unconditional_empty_caption():
    tree = _app()
    # (a) one st.info of the note sits ABOVE the columns, under `not _ss_corr_ok`
    hits = [n for n in ast.walk(tree)
            if isinstance(n, ast.If) and _is_not(n.test, "_ss_corr_ok")
            and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "info" and _calls_note(c)
                    for s in n.body for c in ast.walk(s))]
    assert len(hits) == 1
    # ...and renders ONCE: no loop between it and the enclosing function.
    _par = _parents(tree)
    _n = _par.get(hits[0])
    while _n is not None and not isinstance(_n, (ast.FunctionDef, ast.Module)):
        assert not isinstance(_n, (ast.For, ast.While)), "blast info inside a loop"
        _n = _par.get(_n)
    # (b) the empty-cascade caption stays UNCONDITIONAL (innermost If is `not _ss_blast`)
    cap = _claim_call(tree, "Not enough correlated positions to estimate a cascade", "caption")
    inner = _innermost_if(_parents(tree), cap)
    assert inner is not None and _is_not(inner.test, "_ss_blast")


def test_c6_detector_self_check_on_planted_source():
    good = ('if ok:\n    st.success("X claim")\nelse:\n'
            '    st.info("n " + correlation_record_withheld_note(a, b, subject="s"))\n')
    unconditional = 'st.success("X claim")\n'
    inverted = ('if not ok:\n    st.success("X claim")\nelse:\n'
                '    st.info(correlation_record_withheld_note(a, b, subject="s"))\n')
    swapped = ('if ok:\n    st.info(correlation_record_withheld_note(a, b, subject="s"))\n'
               'else:\n    st.success("X claim")\n')
    no_info = 'if ok:\n    st.success("X claim")\nelse:\n    pass\n'
    info_in_verified = ('if ok:\n    st.success("X claim")\n    st.info("unverified")\nelse:\n'
                        '    st.info(correlation_record_withheld_note(a, b, subject="s"))\n')
    assert _c6_site_ok(ast.parse(good), "X claim", "ok", "success")
    for bad in (unconditional, inverted, swapped, no_info, info_in_verified):
        assert not _c6_site_ok(ast.parse(bad), "X claim", "ok", "success"), bad

    ok_src = ('_k = st.session_state.get("_corr_unchecked_cache")\n'
              '_c = st.session_state.get("_corr_coverage_cache")\n'
              'f = correlation_claim_verified(_k, _c)\n')
    assert _flag_inputs_ok(ast.parse(ok_src), "f", "_k", "_c")
    swapped_args = ok_src.replace("(_k, _c)", "(_c, _k)")
    wrong_key = ok_src.replace('"_corr_unchecked_cache"', '"_corr_coverage_cache"')
    or_collapse = ok_src.replace('"_corr_unchecked_cache")', '"_corr_unchecked_cache") or []')
    for bad in (swapped_args, wrong_key, or_collapse):
        assert not _flag_inputs_ok(ast.parse(bad), "f", "_k", "_c"), bad


# -- subjects / notes -------------------------------------------------------------
_EXPECTED_SUBJECTS = {
    "This scenario can't be run or saved right now",
    "This stress narrative can't be run or saved right now",
    "The correlation check behind this result isn't verified",
    "This “no correlated pairs” reading isn't verified",
    "This “no correlated clusters” reading isn't verified",
    "These cascade estimates rest on an unverified correlation matrix",
}


def test_every_subject_is_present_and_dollar_free():
    found = set()
    for n in ast.walk(_app()):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id == "correlation_record_withheld_note":
            for k in n.keywords:
                if k.arg == "subject" and isinstance(k.value, ast.Constant):
                    found.add(k.value.value)
    assert _EXPECTED_SUBJECTS <= found
    for subject in _EXPECTED_SUBJECTS:
        assert "$" not in subject
        for u, c in itertools.product([None, [], ["X"]], [None, {"n_obs": 5}, {"n_obs": 125}]):
            note = correlation_record_withheld_note(u, c, subject=subject)
            if portfolio.correlation_claim_verified(u, c):
                assert note is None  # contrast: verified -> no note, claim renders
            else:
                assert note.startswith(subject) and "$" not in note
