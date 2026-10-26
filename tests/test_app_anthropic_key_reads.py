"""P1-f: app.py's 16 Anthropic-specific key reads go through the shared helpers.

Out of scope: the multi-provider pickers (AI Snapshot's _AI_PROVIDERS, the
Investigator's provider config) read secrets_path / env_var for whichever provider is
picked. The raw-read guard below only sees literal arguments, so it can't see those.

app.py cannot be imported, so these are structural (AST) checks. The three
news-filter / Pre-Mortem-trigger sites keep the section-only read they had before
P1-f (widening it could switch on a call feeding a pinned LLM role).
"""
import ast
from pathlib import Path

from tests._ast_cache import parse_file_cached

ROOT = Path(__file__).resolve().parents[1]
TREE = parse_file_cached(ROOT / "app.py")
SECRET_NAMES = {"anthropic", "ANTHROPIC_API_KEY"}


def _top_def(name):
    for n in TREE.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} not defined at module level")


def _calls_to(name):
    return [n for n in ast.walk(TREE)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name]


def _is_call_to(node, name):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


def _inside(node, container):
    return any(n is node for n in ast.walk(container))


def test_no_raw_anthropic_secret_or_env_reads_outside_helper():
    helper = _top_def("_anthropic_secret")
    bad = []
    for n in ast.walk(TREE):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("get", "getenv") and n.args
                and isinstance(n.args[0], ast.Constant)):
            continue
        arg, owner = n.args[0].value, ast.unparse(n.func.value)
        if owner == "st.secrets" and arg in SECRET_NAMES and not _inside(n, helper):
            bad.append((n.lineno, ast.unparse(n)))
        if arg == "ANTHROPIC_API_KEY" and (
                (owner == "os.environ" and n.func.attr == "get")
                or (owner == "os" and n.func.attr == "getenv")):
            bad.append((n.lineno, ast.unparse(n)))
    assert not bad, bad


def test_section_only_sites_are_exactly_the_three_filter_and_trigger_reads():
    calls = _calls_to("_anthropic_key_section_only")
    assert len(calls) == 3
    targets = []
    for n in ast.walk(TREE):
        if isinstance(n, ast.Assign) and _is_call_to(n.value, "_anthropic_key_section_only"):
            targets += [t.id for t in n.targets if isinstance(t, ast.Name)]
    assert sorted(targets) == ["_ant_key", "_ant_key", "_pmt_api_key"]
    for n in ast.walk(TREE):
        if (isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id in ("_ant_key", "_pmt_api_key")
                        for t in n.targets)):
            assert _is_call_to(n.value, "_anthropic_key_section_only"), n.lineno


def test_section_only_helper_ignores_env_and_flat_secret():
    fn = _top_def("_anthropic_key_section_only")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "_llm.resolve_anthropic_key"]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw.get("environ"), ast.Dict) and not kw["environ"].keys
    assert any(isinstance(c, ast.Compare)
               and any(isinstance(x, ast.Constant) and x.value == "anthropic"
                       for x in [c.left, *c.comparators])
               for c in ast.walk(fn))


def test_helpers_defined_before_first_use():
    first_call = min(n.lineno for name in ("_anthropic_key", "_anthropic_key_section_only")
                     for n in _calls_to(name))
    for name in ("_anthropic_secret", "_anthropic_key", "_anthropic_key_section_only"):
        assert _top_def(name).lineno < first_call, name


def test_full_order_site_count():
    # 13 + System Trust's AI-models key check (P2-d, models.list only) + App
    # Settings' AI models section (P2-c: models.list / models.retrieve and Test role)
    assert len(_calls_to("_anthropic_key")) == 15
