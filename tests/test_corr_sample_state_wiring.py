"""C1 -- every thin-sample decision routes through
portfolio.correlation_sample_state; nothing compares n_obs to
CORR_MIN_OBS_TRUSTED itself outside portfolio.py."""
from __future__ import annotations

import ast
import pathlib

import pytest
from tests._ast_cache import parse_file_cached

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# One parse per file, shared: app.py is ~44K lines and a single ast.parse
# measured 37.7s on 2026-10-12. Re-parsing per test pushed the pre-commit
# suite past its 450s timeout. Same fix as test_check_antipatterns.py:
# a module-level cache, plus an xdist_group so every test here lands on one
# worker and the cache is actually shared. Read-only use of the trees.
pytestmark = pytest.mark.xdist_group(name="corr_sample_state_wiring_ast")
_TREES: dict = {}


def _tree(rel):
    if rel not in _TREES:
        _TREES[rel] = parse_file_cached(_ROOT / rel)
    return _TREES[rel]


def _names(node):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def _floor_compares(tree):
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Compare) and "CORR_MIN_OBS_TRUSTED" in _names(n)]


@pytest.mark.parametrize("rel", [
    "stock_analyzer/pair_add_gate.py", "stock_analyzer/cluster_add_gate.py",
    "stock_analyzer/util.py", "app.py", "cron_runner.py",
])
def test_no_compare_against_the_floor_outside_portfolio(rel):
    assert _floor_compares(_tree(rel)) == [], rel


def test_the_detector_sees_a_planted_compare():
    assert _floor_compares(ast.parse("x = n < CORR_MIN_OBS_TRUSTED")) == [1]
    assert _floor_compares(ast.parse("x = n < portfolio.CORR_MIN_OBS_TRUSTED")) == [1]
    assert _floor_compares(ast.parse("x = f(CORR_MIN_OBS_TRUSTED)")) == []


def _assigned_from_state(tree, local):
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == local for t in n.targets):
            if (isinstance(n.value, ast.Call)
                    and "correlation_sample_state" in _names(n.value.func)
                    # C1 review X1/X2: the argument must be EXACTLY the
                    # coverage local -- `correlation_sample_state(None)` or
                    # `({"n_obs": 999})` used to pass this check.
                    and len(n.value.args) == 1 and not n.value.keywords
                    and isinstance(n.value.args[0], ast.Name)
                    and n.value.args[0].id == "_corr_cov"):
                return True
    return False


def _is_eq_test(test, local, const):
    """`test` is exactly `<local> == <const>` -- one Eq, both sides bare Names.
    Pinning the STATE, not just the variable: an If that merely mentions
    `local` let thin/unknown be swapped, or `== UNKNOWN` widened to
    `!= TRUSTED`, with the wiring test still green (C1 review, blocking)."""
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name) and test.left.id == local
        and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Name) and test.comparators[0].id == const
    )


def _guarded_by(tree, local, needle, const):
    """True if a string constant containing `needle` sits in the BODY of an
    If/elif whose test is exactly `<local> == <const>`."""
    for n in ast.walk(tree):
        if isinstance(n, ast.If) and _is_eq_test(n.test, local, const):
            for sub in n.body:
                for c in ast.walk(sub):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) and needle in c.value:
                        return True
    return False


def test_the_guard_detector_pins_the_state_not_just_the_name():
    src = 'if s == THIN:\n    x = "warn thin"\nelif s != TRUSTED:\n    x = "warn unknown"\n'
    t = ast.parse(src)
    assert _guarded_by(t, "s", "warn thin", "THIN")
    assert not _guarded_by(t, "s", "warn thin", "UNKNOWN"), "wrong state must not match"
    assert not _guarded_by(t, "s", "warn unknown", "UNKNOWN"), "a widened != test must not match"
    assert not _guarded_by(t, "other", "warn thin", "THIN"), "wrong local must not match"


@pytest.mark.parametrize("needle,const", [
    ("observations this app requires", "CORR_SAMPLE_THIN"),
    ("is **unknown**", "CORR_SAMPLE_UNKNOWN"),
])
def test_app_risk_analysis_block_pinned_both_ends(needle, const):
    t = _tree("app.py")
    assert _assigned_from_state(t, "_corr_sample")
    assert _guarded_by(t, "_corr_sample", needle, const)


@pytest.mark.parametrize("needle,const", [
    ("too thin to trust", "CORR_SAMPLE_THIN"),
    ("sample size behind it is unknown", "CORR_SAMPLE_UNKNOWN"),
])
def test_app_f245_line_pinned_both_ends(needle, const):
    t = _tree("app.py")
    assert _assigned_from_state(t, "_fs_sample")
    assert _guarded_by(t, "_fs_sample", needle, const)


def test_floor_has_one_source_in_constants():
    """C0 (2026-10-12, owner decision D1): CORR_MIN_OBS_TRUSTED lives in
    constants.py, value unchanged. portfolio re-exports the SAME object, and
    no other module assigns its own copy (an assignment anywhere else would
    silently fork the floor that every correlation gate reads)."""
    from stock_analyzer import constants, portfolio
    assert constants.CORR_MIN_OBS_TRUSTED == 20
    assert portfolio.CORR_MIN_OBS_TRUSTED is constants.CORR_MIN_OBS_TRUSTED
    assigners = []
    for path in sorted((_ROOT / "stock_analyzer").glob("*.py")) + [_ROOT / "app.py", _ROOT / "cron_runner.py"]:
        rel = str(path.relative_to(_ROOT)).replace("\\", "/")
        for n in ast.walk(_tree(rel)):
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                if any(isinstance(t, ast.Name) and t.id == "CORR_MIN_OBS_TRUSTED" for t in targets):
                    assigners.append(rel)
    assert assigners == ["stock_analyzer/constants.py"], assigners


def test_monkeypatching_the_portfolio_name_still_moves_the_floor(monkeypatch):
    """The re-export must stay a module global that correlation_sample_state
    reads at call time. Contrast: unpatched, 5 is thin."""
    from stock_analyzer import portfolio
    assert portfolio.correlation_sample_state({"n_obs": 5}) == portfolio.CORR_SAMPLE_THIN
    monkeypatch.setattr(portfolio, "CORR_MIN_OBS_TRUSTED", 1)
    assert portfolio.correlation_sample_state({"n_obs": 5}) == portfolio.CORR_SAMPLE_TRUSTED


def test_assigned_from_state_requires_the_coverage_local_as_sole_argument():
    ok = ast.parse("s = correlation_sample_state(_corr_cov)")
    assert _assigned_from_state(ok, "s")
    for bad in ("s = correlation_sample_state(None)",
                's = correlation_sample_state({"n_obs": 999})',
                "s = correlation_sample_state(other)",
                "s = correlation_sample_state(_corr_cov, extra)",
                "s = correlation_sample_state(corr_coverage=_corr_cov)",
                "s = correlation_sample_state()"):
        assert not _assigned_from_state(ast.parse(bad), "s"), bad


def _chain_tests(top):
    """Flatten an if/elif chain into its ordered list of test expressions."""
    out, node = [], top
    while isinstance(node, ast.If):
        out.append(node.test)
        node = node.orelse[0] if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) else None
    return out


def _sample_branches_precede_dupes(tree, local="_fs_sample", dupes="_fs_dupes"):
    """Longest if/elif chain testing `local == THIN` AND `local == UNKNOWN`:
    both must appear BEFORE the first test naming `dupes`. Returns the list
    of (thin_idx, unknown_idx, dupes_idx) for every such chain."""
    found = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.If):
            continue
        tests = _chain_tests(n)
        idx = {}
        for i, t in enumerate(tests):
            if _is_eq_test(t, local, "CORR_SAMPLE_THIN"):
                idx.setdefault("thin", i)
            elif _is_eq_test(t, local, "CORR_SAMPLE_UNKNOWN"):
                idx.setdefault("unknown", i)
            elif dupes in _names(t):
                idx.setdefault("dupes", i)
        if "thin" in idx and "unknown" in idx and "dupes" in idx:
            found.append((idx["thin"], idx["unknown"], idx["dupes"]))
    return found


def test_f245_thin_and_unknown_branches_precede_the_duplicated_pair_claim():
    """C1 review X3: a reorder letting a thin sample reach the "one bet
    stopping out twice" claim must fail."""
    chains = _sample_branches_precede_dupes(_tree("app.py"))
    assert chains, "F-245 if/elif chain not found"
    for thin, unknown, dupes in chains:
        assert thin < dupes and unknown < dupes, (thin, unknown, dupes)


def _src(*lines):
    return chr(10).join(lines) + chr(10)


def test_chain_order_detector_rejects_a_reordered_chain():
    good = ast.parse(_src(
        "if a is None:", "  pass",
        "elif s == CORR_SAMPLE_THIN:", "  pass",
        "elif s == CORR_SAMPLE_UNKNOWN:", "  pass",
        "elif d:", "  pass",
    ))
    bad = ast.parse(_src(
        "if a is None:", "  pass",
        "elif d:", "  pass",
        "elif s == CORR_SAMPLE_THIN:", "  pass",
        "elif s == CORR_SAMPLE_UNKNOWN:", "  pass",
    ))
    gres = _sample_branches_precede_dupes(good, local="s", dupes="d")
    bres = _sample_branches_precede_dupes(bad, local="s", dupes="d")
    assert gres and all(t < d and u < d for t, u, d in gres)
    assert bres and not all(t < d and u < d for t, u, d in bres), "reordered chain must be rejected"
