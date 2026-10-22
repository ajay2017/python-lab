"""Role-pin test for every `llm.complete(...)` call (docs/plans/llm-model-routing.md).

Phase 1 changes no model: each role's code default equals the model the site
hardcoded before. From Phase 3 the ROLE passed here decides the model, so
moving a call to a different role (for example the pinned `score` / `filter` /
`extract_trigger` roles that feed the composite and Act Today) is a reviewed
change. This test fails on any such move, and on any new or removed call, until
the expected table below is edited by hand.

A call is `<name ending in "llm">.complete(...)` in stock_analyzer/*.py,
app.py or cron_runner.py. Each is recorded as (file stem, enclosing function) -> the first
positional argument rendered with ast.unparse.
"""
import ast
from pathlib import Path

from tests._ast_cache import parse_file_cached

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    ("ai_provider", "call_llm"): "llm.ROLE_DEEP",
    ("analyst_intel", "extract_report"): "llm.ROLE_EXTRACT",
    ("broker_screenshot", "_resolve_unknown_tickers"): "llm.ROLE_EXTRACT_STRONG",
    ("catalyst_stress", "generate_catalyst_narrative"): "llm.ROLE_FAST",
    # the role is a parameter: rounds pass the default FAST, the judge EXTRACT
    ("debate_agent", "_call_haiku"): "role",
    ("debrief_advisor", "generate_debrief"): "llm.ROLE_DEEP",
    ("earnings_intel", "extract_playbook"): "llm.ROLE_EXTRACT",
    ("earnings_intel", "extract_results"): "llm.ROLE_EXTRACT",
    ("intelligence_report", "generate_report"): "llm.ROLE_DEEP",
    ("missed_opportunity", "generate_missed_opportunity_patterns"): "llm.ROLE_FAST",
    ("news_intelligence", "rescore_news_items_llm"): "llm.ROLE_FILTER",
    ("news_intelligence", "llm_score_batch"): "llm.ROLE_FILTER",
    ("news_intelligence", "rescore_headlines_llm"): "llm.ROLE_SCORE",
    ("portfolio_qa", "parse_question"): "llm.ROLE_FAST",
    ("portfolio_qa", "narrate_answer"): "llm.ROLE_FAST",
    ("premarket_stance", "generate_stance"): "llm.ROLE_FAST",
    ("premortem_advisor", "generate_case_against"): "llm.ROLE_FAST",
    ("premortem_monitor", "extract_trigger"): "llm.ROLE_EXTRACT_TRIGGER",
    ("regime_stress", "generate_regime_scenario"): "llm.ROLE_FAST",
    ("structural_scanner", "generate_structural_narrative"): "llm.ROLE_FAST",
    ("thesis_advisor", "review_thesis"): "llm.ROLE_DEEP",
    ("thesis_advisor", "draft_thesis"): "llm.ROLE_DEEP",
    ("thesis_advisor", "generate_earnings_thesis_update"): "llm.ROLE_DEEP",
    ("thesis_cluster", "generate_thesis_clusters"): "llm.ROLE_FAST",
    ("thesis_red_team", "generate_counter_evidence"): "llm.ROLE_FAST",
    # app.py imports the module as `_llm`; this is AI Snapshot's Claude branch
    ("app", "_call_ai_brief"): "_llm.ROLE_FAST",
}


def _collect() -> dict:
    found = {}
    files = sorted((ROOT / "stock_analyzer").glob("*.py")) + [ROOT / "app.py", ROOT / "cron_runner.py"]
    for path in files:
        tree = parse_file_cached(path)

        def walk(node, fn):
            for child in ast.iter_child_nodes(node):
                nf = child.name if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
                if (isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr == "complete"
                        and isinstance(child.func.value, ast.Name)
                        and child.func.value.id.endswith("llm")):
                    arg = ast.unparse(child.args[0]) if child.args else None
                    key = (path.stem, nf)
                    assert key not in found, f"two llm.complete calls in {key}"
                    found[key] = arg
                walk(child, nf)

        walk(tree, None)
    return found


def test_every_llm_complete_call_uses_its_expected_role():
    assert _collect() == EXPECTED


def test_debate_call_haiku_call_sites_pass_the_expected_role():
    """_call_haiku takes the role as a parameter, so the table above records
    only "role" for it. Pin each call site: the four rounds use the default
    (FAST, narrative: cut-off text kept), the judge passes EXTRACT (JSON: a
    cut-off verdict is never parsed)."""
    tree = parse_file_cached(ROOT / "stock_analyzer" / "debate_agent.py")
    roles = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_call_haiku"):
            kw = {k.arg: ast.unparse(k.value) for k in n.keywords}
            roles.append((n.lineno, kw.get("role", "<default>")))
    roles = [r for _, r in sorted(roles)]
    assert roles == ["<default>"] * 4 + ["llm.ROLE_EXTRACT"], roles
