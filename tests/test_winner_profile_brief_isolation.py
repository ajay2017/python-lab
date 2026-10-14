"""The winner-profile badge must never take the Daily Brief offline (2026-10-14).

Both `build_daily_briefing` call sites in app.py evaluate `winner_profile=` as an
ARGUMENT inside their try. An exception escaping the profile build (Supabase
reads, build_closed_lots, match_recs_to_trades, build_winner_profile) used to fail
the whole Brief -- protective Act Today included -- for the awareness-only
"🧬 Matches your historical winning profile" badge. `_compute_winner_profile` is
now an isolation wrapper (same shape as the ETF lane's, tests/
test_etf_brief_isolation.py) that turns any escape into None -- the value the
Brief already gets when the profile is withheld below the sample floor.

app.py can't be imported, so the wrapper is lifted out of its AST and executed
against stubs, and both ends of the wiring are pinned.
"""
import ast
from pathlib import Path

import pytest

from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "app.py"

GUARDED = "_compute_winner_profile"
UNGUARDED = "_compute_winner_profile_unguarded"


def _tree():
    return parse_file_cached(APP_PY)


def _func_defs(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]


def _calls_to(node, name):
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and (
                (isinstance(n.func, ast.Name) and n.func.id == name)
                or (isinstance(n.func, ast.Attribute) and n.func.attr == name))]


class _StubSt:
    def __init__(self):
        self.captions = []

    def caption(self, text):
        self.captions.append(text)


def _load_wrapper(unguarded_impl):
    defs = _func_defs(_tree(), GUARDED)
    assert len(defs) == 1, f"expected exactly one def {GUARDED}, found {len(defs)}"
    mod = ast.Module(body=[defs[0]], type_ignores=[])
    ast.fix_missing_locations(mod)
    stub_st = _StubSt()
    ns = {"st": stub_st, UNGUARDED: unguarded_impl}
    exec(compile(mod, str(APP_PY), "exec"), ns)
    return ns[GUARDED], stub_st


@pytest.mark.parametrize("exc", [KeyError("rec_type"), TypeError("NoneType"),
                                 ValueError("bad date"), RuntimeError("boom")])
def test_escaping_exception_becomes_none_with_a_caption(exc):
    def _raises():
        raise exc
    wrapper, stub_st = _load_wrapper(_raises)
    assert wrapper() is None
    assert len(stub_st.captions) == 1
    assert type(exc).__name__ in stub_st.captions[0]
    assert "badge is off" in stub_st.captions[0]


@pytest.mark.parametrize("value", [None, {"n": 9, "composite_band": (70, 85)}])
def test_success_passes_the_value_through_unchanged_and_silently(value):
    wrapper, stub_st = _load_wrapper(lambda: value)
    assert wrapper() is value
    assert stub_st.captions == []


def test_every_brief_build_passes_the_guarded_wrapper():
    briefs = _calls_to(_tree(), "build_daily_briefing")
    assert len(briefs) >= 2, "expected the MISS-path and HIT-path Brief builds"
    for call in briefs:
        kw = {k.arg: k.value for k in call.keywords}
        assert "winner_profile" in kw, f"Brief build at line {call.lineno} lost winner_profile"
        v = kw["winner_profile"]
        assert (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                and v.func.id == GUARDED), (
            f"Brief build at line {call.lineno} must pass {GUARDED}(), not an unguarded value")


def test_unguarded_body_is_called_only_from_inside_the_wrapper():
    tree = _tree()
    all_calls = _calls_to(tree, UNGUARDED)
    wrapper = _func_defs(tree, GUARDED)[0]
    inside = _calls_to(wrapper, UNGUARDED)
    assert len(all_calls) == 1 and len(inside) == 1, (
        f"{UNGUARDED} must be called exactly once, inside {GUARDED}; "
        f"found {len(all_calls)} total, {len(inside)} inside")
    tries = [n for n in ast.walk(wrapper) if isinstance(n, ast.Try)]
    assert any(_calls_to(t, UNGUARDED) and any(
        isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in t.handlers)
        for t in tries), f"the {UNGUARDED} call must be guarded by `except Exception`"
