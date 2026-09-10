"""Static (no-import) test dependency graph analyzer.

Work Item 1.2 of docs/plans/test-suite-optimization.md — builds the real
module dependency graph so Work Item 1.1 can mark "fast" tests from actual
data instead of a guess.

Method: pure `ast` parsing, never `import`/`exec` of project modules — some
modules have side effects (Streamlit, Supabase clients) or optional deps that
may not be installed in every environment, so importing them here would be
both slower and less reliable than static analysis.

For every file under tests/, this script:
  1. Parses its own MODULE-LEVEL import statements (imports nested inside a
     function/method body are deliberately excluded — those are deferred at
     runtime and don't cost anything during pytest's collection phase, which
     only executes each test file's top-level code).
  2. Resolves each import to a project module dotted name (stock_analyzer.*,
     or a top-level module: app, cron_runner, main, or scripts.*).
  3. Recursively walks each resolved project module's OWN module-level
     imports (again via ast, not execution) to build the transitive closure,
     and checks whether `stock_analyzer.bundle_loader` is reachable anywhere
     in that closure.

Output: docs/test-dependency-graph.md — heavy-module ranking (by number of
test files that transitively reach them) and the list of test files that
never transitively reach bundle_loader ("light"/fast-eligible).

Usage:
    python scripts/analyze_test_deps.py
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
STOCK_ANALYZER_DIR = REPO_ROOT / "stock_analyzer"
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Top-level (non-package) project modules that live at the repo root.
_ROOT_MODULES = ("app", "cron_runner", "main")

TARGET_MODULE = "stock_analyzer.bundle_loader"


def _dotted_name_for_file(path: Path) -> str:
    """Return the project dotted module name for a project source file."""
    rel = path.relative_to(REPO_ROOT)
    parts = list(rel.parts)
    assert parts[-1].endswith(".py")
    stem = parts[-1][:-3]
    if stem == "__init__":
        parts = parts[:-1]
    else:
        parts = parts[:-1] + [stem]
    return ".".join(parts)


def discover_project_modules() -> dict[str, Path]:
    """Map every resolvable project dotted module name -> its source file."""
    modules: dict[str, Path] = {}

    for py in STOCK_ANALYZER_DIR.glob("*.py"):
        modules[_dotted_name_for_file(py)] = py

    for py in SCRIPTS_DIR.glob("*.py"):
        modules[_dotted_name_for_file(py)] = py

    for name in _ROOT_MODULES:
        f = REPO_ROOT / f"{name}.py"
        if f.exists():
            modules[name] = f

    for py in TESTS_DIR.glob("test_*.py"):
        modules[_dotted_name_for_file(py)] = py

    return modules


class _TopLevelImportCollector(ast.NodeVisitor):
    """Collects Import/ImportFrom nodes that are NOT nested inside a
    function/async-function/lambda body — i.e. nodes that actually execute
    at module-import time, not deferred/lazy imports."""

    def __init__(self):
        self.imports: list[ast.Import] = []
        self.import_froms: list[ast.ImportFrom] = []
        self._func_depth = 0

    def visit_FunctionDef(self, node):
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    def visit_AsyncFunctionDef(self, node):
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    def visit_Lambda(self, node):
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    def visit_Import(self, node):
        if self._func_depth == 0:
            self.imports.append(node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if self._func_depth == 0:
            self.import_froms.append(node)
        self.generic_visit(node)


def _parse_top_level_imports(path: Path) -> _TopLevelImportCollector:
    # utf-8-sig transparently strips a leading BOM (app.py has one) while
    # behaving identically to utf-8 for BOM-less files.
    source = path.read_text(encoding="utf-8-sig", errors="replace")
    tree = ast.parse(source, filename=str(path))
    collector = _TopLevelImportCollector()
    collector.visit(tree)
    return collector


def _resolve_relative(current_dotted: str, level: int, module: str | None) -> str:
    """Resolve a relative `from . import x` / `from .x import y` to an
    absolute dotted name, given the dotted name of the module it appears in.
    """
    parts = current_dotted.split(".")
    # `current_dotted` names a MODULE (a .py file), not a package, so its own
    # enclosing package is everything but the last component.
    package_parts = parts[:-1]
    if level > 1:
        package_parts = package_parts[: len(package_parts) - (level - 1)]
    base = ".".join(package_parts)
    if module:
        return f"{base}.{module}" if base else module
    return base


def resolve_direct_imports(path: Path, current_dotted: str, project_modules: dict[str, Path]) -> set[str]:
    """Direct (one-hop) project-module dependencies of a single file."""
    deps: set[str] = set()
    collector = _parse_top_level_imports(path)

    for node in collector.imports:
        for alias in node.names:
            name = alias.name
            if name in project_modules:
                deps.add(name)

    for node in collector.import_froms:
        if node.level and node.level > 0:
            base = _resolve_relative(current_dotted, node.level, node.module)
        else:
            base = node.module or ""

        if base in project_modules:
            deps.add(base)

        # `from stock_analyzer import risk_advisor` — base is the package,
        # each imported name may itself be a submodule.
        for alias in node.names:
            candidate = f"{base}.{alias.name}" if base else alias.name
            if candidate in project_modules:
                deps.add(candidate)

    return deps


def build_direct_graph(project_modules: dict[str, Path]) -> dict[str, set[str]]:
    """direct_graph[module_dotted] = set of directly-imported project modules."""
    graph: dict[str, set[str]] = {}
    for dotted, path in project_modules.items():
        graph[dotted] = resolve_direct_imports(path, dotted, project_modules)
    return graph


def transitive_closure(start: str, direct_graph: dict[str, set[str]]) -> set[str]:
    """All project modules reachable from `start`, any number of hops (not
    including `start` itself unless it's reachable via a cycle)."""
    seen: set[str] = set()
    stack = list(direct_graph.get(start, ()))
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        seen.add(m)
        stack.extend(direct_graph.get(m, ()))
    return seen


def analyze() -> dict:
    project_modules = discover_project_modules()
    direct_graph = build_direct_graph(project_modules)

    test_files = sorted(p for p in project_modules if p.startswith("tests.test_"))

    test_closure: dict[str, set[str]] = {}
    test_reaches_bundle_loader: dict[str, bool] = {}
    for t in test_files:
        closure = transitive_closure(t, direct_graph)
        test_closure[t] = closure
        test_reaches_bundle_loader[t] = TARGET_MODULE in closure

    # Heavy-module ranking: for each non-test project module, count how many
    # TEST FILES transitively reach it (directly or transitively).
    heavy_counts: dict[str, int] = defaultdict(int)
    for t in test_files:
        for m in test_closure[t]:
            if not m.startswith("tests."):
                heavy_counts[m] += 1

    light_files = sorted(t for t in test_files if not test_reaches_bundle_loader[t])
    heavy_files = sorted(t for t in test_files if test_reaches_bundle_loader[t])

    return {
        "project_modules": project_modules,
        "direct_graph": direct_graph,
        "test_files": test_files,
        "test_closure": test_closure,
        "test_reaches_bundle_loader": test_reaches_bundle_loader,
        "heavy_counts": heavy_counts,
        "light_files": light_files,
        "heavy_files": heavy_files,
    }


def _short(dotted: str) -> str:
    """Display helper: tests.test_x -> tests/test_x.py"""
    if dotted.startswith("tests."):
        return "tests/" + dotted.split(".", 1)[1] + ".py"
    return dotted


def render_report(result: dict) -> str:
    test_files = result["test_files"]
    heavy_counts = result["heavy_counts"]
    light_files = result["light_files"]
    heavy_files = result["heavy_files"]

    lines: list[str] = []
    lines.append("# Test Dependency Graph")
    lines.append("")
    lines.append(
        "Generated by `scripts/analyze_test_deps.py` — static `ast`-based "
        "analysis of module-level imports (no code executed). Supports "
        "Work Item 1.2 of "
        "[docs/plans/test-suite-optimization.md](plans/test-suite-optimization.md)."
    )
    lines.append("")
    lines.append(
        f"Test files analyzed: **{len(test_files)}**. "
        f"Light (never transitively import `stock_analyzer.bundle_loader`): "
        f"**{len(light_files)}**. Heavy (reach `bundle_loader`): "
        f"**{len(heavy_files)}**."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Heavy Modules (ranked by number of test files that transitively reach them)")
    lines.append("")
    lines.append(
        "Counts a test file once per module it can reach through any chain "
        "of module-level imports, not just direct imports."
    )
    lines.append("")
    lines.append("| Rank | Module | Test files reaching it (transitively) |")
    lines.append("|---|---|---|")
    ranked = sorted(heavy_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    for i, (module, count) in enumerate(ranked[:40], start=1):
        lines.append(f"| {i} | `{module}` | {count} |")
    lines.append("")
    lines.append(
        f"(Showing top {min(40, len(ranked))} of {len(ranked)} project modules "
        "reached by at least one test file.)"
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"## Light / Fast-Eligible Test Files ({len(light_files)})")
    lines.append("")
    lines.append(
        "These test files never transitively import "
        f"`{TARGET_MODULE}` through any chain of module-level imports. "
        "Marked `@pytest.mark.fast` / `pytestmark = pytest.mark.fast` "
        "per Work Item 1.1."
    )
    lines.append("")
    for t in light_files:
        lines.append(f"- `{_short(t)}`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"## Heavy Test Files ({len(heavy_files)})")
    lines.append("")
    lines.append(
        f"These test files transitively reach `{TARGET_MODULE}` and are "
        "therefore left out of the `fast` marker set."
    )
    lines.append("")
    lines.append(
        "<details><summary>Full list</summary>"
    )
    lines.append("")
    for t in heavy_files:
        lines.append(f"- `{_short(t)}`")
    lines.append("")
    lines.append("</details>")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    result = analyze()
    report = render_report(result)
    out_path = REPO_ROOT / "docs" / "test-dependency-graph.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"Analyzed {len(result['test_files'])} test files.")
    print(f"Light (fast-eligible): {len(result['light_files'])}")
    print(f"Heavy: {len(result['heavy_files'])}")
    print(f"Report written to {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
