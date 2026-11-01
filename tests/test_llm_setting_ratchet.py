"""Plan 17 C4: no unreviewed caller may inject a model setting into a decision-role
LLM call. ``setting=`` on these functions is allowed only in the allowlisted files."""
import ast
from pathlib import Path

from tests._ast_cache import parse_file_cached

ROOT = Path(__file__).resolve().parents[1]
GUARDED = {"complete", "llm_score_batch", "rescore_news_items_llm",
           "rescore_brief_news", "rescore_headlines_llm", "extract_trigger",
           "_call_haiku"}
# Files that may pass ``setting=``: today's existing sites plus the reviewed
# decision-path owners.
# Matched by repo-relative POSIX path, not basename.
ALLOWED = {
    "stock_analyzer/llm.py", "stock_analyzer/news_intelligence.py",
    "stock_analyzer/premortem_monitor.py", "stock_analyzer/bundle_loader.py",
    "stock_analyzer/llm_role_eval.py", "stock_analyzer/ai_models_settings.py",
    # existing editable-role passthroughs (debate_agent calls _call_haiku)
    "stock_analyzer/debate_agent.py", "stock_analyzer/premarket_stance.py",
    "scripts/llm_role_eval.py",
}


def _files():
    yield ROOT / "app.py"
    yield ROOT / "cron_runner.py"
    yield from (ROOT / "stock_analyzer").rglob("*.py")
    yield from (ROOT / "scripts").rglob("*.py")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _callee(node: ast.Call):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _guarded_setting_calls(tree):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and _callee(n) in GUARDED
            and any(k.arg == "setting" for k in n.keywords)]


def _offenders():
    out = []
    for path in _files():
        if not path.exists() or _rel(path) in ALLOWED:
            continue
        tree = parse_file_cached(path)
        out.extend(f"{_rel(path)}:{n.lineno} {_callee(n)}"
                   for n in _guarded_setting_calls(tree))
    return out


def test_setting_kw_only_in_allowlisted_files():
    assert _offenders() == []


def test_the_scan_sees_the_expected_files():
    names = {p.name for p in _files() if p.exists()}
    assert {"app.py", "cron_runner.py", "llm.py", "news_intelligence.py",
            "llm_role_eval.py"} <= names
    assert any(_rel(p).startswith("scripts/") for p in _files() if p.exists())
    assert all(a.count("/") >= 1 for a in ALLOWED)


def test_the_detector_catches_a_violation():
    tree = ast.parse("llm.complete('score', setting={'score': 1})\n"
                     "rescore_headlines_llm(h, k, setting=s)\nother(setting=1)")
    assert [_callee(n) for n in _guarded_setting_calls(tree)] == [
        "complete", "rescore_headlines_llm"]


def test_the_detector_catches_call_haiku():
    tree = ast.parse("_call_haiku(k, m, s, u, setting=x)")
    assert [_callee(n) for n in _guarded_setting_calls(tree)] == ["_call_haiku"]
