"""Ratchet: Anthropic model ids and SDK calls live in stock_analyzer/llm.py only
(docs/plans/llm-model-routing.md, Phase 1 / P1-e).

Three AST checks over app.py, cron_runner.py and stock_analyzer/*.py:
  1. no `claude-...` model-id string literal outside the allowed spots,
  2. no `Anthropic(...)` client construction or `.messages.create(...)` call
     outside llm.py,
  3. llm is imported only as `from stock_analyzer import llm [as <name ending
     in "llm">]`, because the golden-request and role-pin guards only look at
     such aliases.
"""
import ast
import re
from pathlib import Path

from tests._ast_cache import parse_file_cached

ROOT = Path(__file__).resolve().parents[1]
_MODEL_ID = re.compile(r"^claude-[A-Za-z0-9.\-]+$")

# (file, assigned name): the one assignment whose value may hold model ids
_ID_ALLOWED_ASSIGNS = {
    ("stock_analyzer/ai_provider.py", "AI_PROVIDERS"),
    ("stock_analyzer/investigator.py", "EVAL_PASSED_MODELS"),
    ("app.py", "_AI_PROVIDERS"),
}


def _files():
    return [ROOT / "app.py", ROOT / "cron_runner.py",
            *sorted((ROOT / "stock_analyzer").rglob("*.py"))]


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _assign_names(node):
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {t.id for t in targets if isinstance(t, ast.Name)}


def _model_id_literals(tree, rel: str) -> list:
    """Model-id string constants, skipping the allowed Assign nodes."""
    hits = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if (isinstance(child, (ast.Assign, ast.AnnAssign))
                    and child.value is not None
                    and any((rel, n) in _ID_ALLOWED_ASSIGNS
                            for n in _assign_names(child))):
                continue
            if (isinstance(child, ast.Constant) and isinstance(child.value, str)
                    and _MODEL_ID.match(child.value)):
                hits.append((child.lineno, child.value))
            walk(child)

    walk(tree)
    return hits


def test_no_model_id_literals_outside_allowed_places():
    bad = []
    for p in _files():
        rel = _rel(p)
        if rel == "stock_analyzer/llm.py":
            continue
        tree = parse_file_cached(p)
        bad += [f"{rel}:{ln} {v!r}" for ln, v in _model_id_literals(tree, rel)]
    assert not bad, "model id literal outside llm.py:\n" + "\n".join(bad)


_CLIENT_NAMES = {"Anthropic", "AsyncAnthropic"}


def test_no_anthropic_client_or_messages_create_outside_llm():
    bad = []
    for p in _files():
        rel = _rel(p)
        if rel == "stock_analyzer/llm.py":
            continue
        tree = parse_file_cached(p)
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "anthropic":
                for a in n.names:
                    if a.name in _CLIENT_NAMES:
                        bad.append(f"{rel}:{n.lineno} from anthropic import {a.name}")
                continue
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if isinstance(f, ast.Name) and f.id in _CLIENT_NAMES:
                bad.append(f"{rel}:{n.lineno} {f.id}(...)")
            elif not isinstance(f, ast.Attribute):
                continue
            elif f.attr in _CLIENT_NAMES:
                bad.append(f"{rel}:{n.lineno} {f.attr}(...)")
            elif (f.attr == "create" and isinstance(f.value, ast.Attribute)
                  and f.value.attr == "messages"):
                bad.append(f"{rel}:{n.lineno} .messages.create(...)")
    assert not bad, "direct Anthropic SDK use outside llm.py:\n" + "\n".join(bad)


def test_llm_import_form():
    bad = []
    for p in _files():
        rel = _rel(p)
        tree = parse_file_cached(p)
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                if n.module == "stock_analyzer.llm":
                    bad.append(f"{rel}:{n.lineno} from stock_analyzer.llm import ...")
                elif n.module == "stock_analyzer":
                    for a in n.names:
                        if a.name == "llm" and not (a.asname or "llm").endswith("llm"):
                            bad.append(f"{rel}:{n.lineno} alias {a.asname!r}")
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name == "stock_analyzer.llm" and not (
                            a.asname and a.asname.endswith("llm")):
                        bad.append(f"{rel}:{n.lineno} import stock_analyzer.llm "
                                   f"as {a.asname!r}")
    assert not bad, "bad llm import form:\n" + "\n".join(bad)


def test_resolve_model_only_called_inside_llm():
    """B1: resolve_model / resolve_model_with_source are called only inside
    llm.py (and llm_settings.py, plus ai_models_view.py for display); every other site uses ``llm.resolved`` so the
    App Settings choice can't be bypassed by an early resolve."""
    names = {"resolve_model", "resolve_model_with_source"}
    # ai_models_view only DISPLAYS a resolution (System Trust) against the
    # snapshot and env it is handed; it never builds a request.
    allowed = {"stock_analyzer/llm.py", "stock_analyzer/llm_settings.py",
               "stock_analyzer/ai_models_view.py"}
    bad = []
    for path in _files():
        rel = _rel(path)
        if rel in allowed:
            continue
        for n in ast.walk(parse_file_cached(path)):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else None)
            if name in names:
                bad.append(f"{rel}:{n.lineno} {name}")
    assert not bad, "resolve_model called outside llm.py:\n" + "\n".join(bad)
