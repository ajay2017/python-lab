"""Ratchet (S-D C8, docs/plans/one-sizing-path.md): share counts come from ONE
sizing path, so `risk.position_sizing` is called only from the reviewed spots.

Every buy size on a surface has to pass the caps, the daily budget and the
entry checks. A new direct `position_sizing(` call is how a surface would hand
out a size that skips them (the bug class S-D removed from the Rebalancer,
Diversification, Watchlist and Economic Calendar). AST checks over app.py,
cron_runner.py, scripts/*.py and stock_analyzer/*.py:
  1. direct calls per file equal the reviewed count in _ALLOWED_CALLS
     (equality, so a removed call also updates the list);
  2. the function is never aliased, passed around or imported elsewhere;
  3. it is defined only in risk.py;
  4. the Watchlist panel's call sits behind `_wl_may_size` (ENTER_NOW only),
     and the Watchlist budget lambda feeds `watchlist_asks` (budget-capped).
Self-tests on synthetic source keep the detector itself honest.
"""
import ast
import functools
from pathlib import Path

from tests._ast_cache import parse_file_cached

ROOT = Path(__file__).resolve().parents[1]
_NAME = "position_sizing"

# file -> reviewed number of direct position_sizing(...) calls.
_ALLOWED_CALLS = {
    # size_position(): a held add counts the whole position (S-D C1).
    "stock_analyzer/sizing_path.py": 1,
    # Grow Today's adapter (_position_size_for_render), budgeted downstream.
    "stock_analyzer/daily_briefing.py": 1,
    # 📈 Analysis not-held Buy (then the daily budget); 📋 Watchlist budget
    # asks (lambda into budget_view.watchlist_asks); 📋 Watchlist ENTER_NOW
    # panel (behind sizing_path.may_show_size via _wl_may_size).
    "app.py": 3,
}
# Files allowed `from stock_analyzer.risk import position_sizing` (no alias).
_IMPORT_ALLOWED = {"app.py", "stock_analyzer/daily_briefing.py"}


def _files():
    return [ROOT / "app.py", ROOT / "cron_runner.py",
            *sorted((ROOT / "scripts").glob("*.py")),
            *sorted((ROOT / "stock_analyzer").glob("*.py"))]


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _is_ref(node) -> bool:
    return ((isinstance(node, ast.Name) and node.id == _NAME)
            or (isinstance(node, ast.Attribute) and node.attr == _NAME))


def _scan(tree: ast.AST) -> dict:
    """{"calls": [Call], "stray_refs": [lineno], "imports": [(lineno, asname)],
    "defs": [lineno]} for one module."""
    call_funcs = set()
    out = {"calls": [], "stray_refs": [], "imports": [], "defs": []}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_ref(node.func):
            out["calls"].append(node)
            call_funcs.add(id(node.func))
    for node in ast.walk(tree):
        if _is_ref(node) and id(node) not in call_funcs:
            out["stray_refs"].append(getattr(node, "lineno", 0))
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == _NAME:
                    out["imports"].append((node.lineno, a.asname))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == _NAME:
            out["defs"].append(node.lineno)
    return out


def _parents(tree: ast.AST) -> dict:
    par = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            par[id(child)] = node
    return par


def _ancestors(node, par):
    while id(node) in par:
        node = par[id(node)]
        yield node


def _names_in(node) -> set:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


# ── The ratchet over the real repo ────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _repo_scans():
    # One walk per file per session; the tests only read the result.
    return {_rel(p): (_scan(t := parse_file_cached(p)), t) for p in _files()}


def test_position_sizing_calls_only_in_reviewed_spots():
    got = {rel: len(s["calls"]) for rel, (s, _) in _repo_scans().items() if s["calls"]}
    assert got == _ALLOWED_CALLS, (
        f"direct position_sizing(...) calls per file are {got}, reviewed list is "
        f"{_ALLOWED_CALLS}. A new call hands out a share count that can skip the caps, "
        "the daily budget and the entry checks (S-D, docs/plans/one-sizing-path.md): "
        "route it through sizing_path / the Grow Today adapter instead, or get it "
        "reviewed and update _ALLOWED_CALLS.")


def test_position_sizing_never_aliased_or_passed_around():
    bad = []
    for rel, (s, _) in _repo_scans().items():
        bad += [f"{rel}:{ln} reference outside a call" for ln in s["stray_refs"]]
        for ln, asname in s["imports"]:
            if rel not in _IMPORT_ALLOWED or asname is not None:
                bad.append(f"{rel}:{ln} imports position_sizing"
                           + (f" as {asname}" if asname else ""))
    assert not bad, "position_sizing escapes the call-site ratchet: " + "; ".join(bad)


def test_position_sizing_defined_only_in_risk():
    defs = {rel: s["defs"] for rel, (s, _) in _repo_scans().items() if s["defs"]}
    assert set(defs) == {"stock_analyzer/risk.py"} and len(defs["stock_analyzer/risk.py"]) == 1, defs


def test_watchlist_calls_are_gated():
    tree = parse_file_cached(ROOT / "app.py")
    par = _parents(tree)
    panel, lam = [], []
    for call in _scan(tree)["calls"]:
        anc = list(_ancestors(call, par))
        assign = next((a for a in anc if isinstance(a, ast.Assign)), None)
        if assign is not None and any(isinstance(t, ast.Name) and t.id == "_wl_ps"
                                      for t in assign.targets):
            panel.append(any(isinstance(a, ast.If) and "_wl_may_size" in _names_in(a.test)
                             for a in anc))
        if anc and isinstance(anc[0], ast.Lambda):
            outer = next((a for a in anc[1:] if isinstance(a, ast.Call)), None)
            lam.append(outer is not None and isinstance(outer.func, ast.Attribute)
                       and outer.func.attr == "watchlist_asks")
    assert panel == [True], (
        "the 📋 Watchlist panel's position_sizing call must sit inside an `if` on "
        f"_wl_may_size (ENTER_NOW only, S-D C4); found {panel}")
    assert lam == [True], (
        "the 📋 Watchlist budget lambda must feed budget_view.watchlist_asks; "
        f"found {lam}")


# ── Self-tests: the detector catches each escape route ────────────────────────

def _scan_src(src: str) -> dict:
    return _scan(ast.parse(src))


def test_detector_counts_name_and_attribute_calls():
    s = _scan_src("position_sizing(1)\nrisk.position_sizing(2)\nother(3)\n")
    assert len(s["calls"]) == 2 and not s["stray_refs"]


def test_detector_flags_alias_pass_and_reassign():
    s = _scan_src("from stock_analyzer.risk import position_sizing as ps\n"
                  "f(position_sizing)\n"
                  "g = risk.position_sizing\n")
    assert s["imports"] == [(1, "ps")]
    assert len(s["stray_refs"]) == 2 and not s["calls"]


def test_detector_finds_definitions():
    assert _scan_src("def position_sizing():\n    pass\n")["defs"] == [1]


def test_gate_check_sees_ungated_panel():
    tree = ast.parse("if _price:\n    _wl_ps = position_sizing(1)\n")
    par = _parents(tree)
    call = _scan(tree)["calls"][0]
    assert not any(isinstance(a, ast.If) and "_wl_may_size" in _names_in(a.test)
                   for a in _ancestors(call, par))
