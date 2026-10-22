"""
Golden-request snapshot for every Anthropic `messages.create` call in
stock_analyzer/ (docs/plans/llm-model-routing.md, section 2 + section 10 item 3).

Why: the model-routing refactor moves all 25 call sites onto one helper. This
file records EXACTLY what each site sends today -- constructor kwargs of
`anthropic.Anthropic(...)` AND every kwarg of `messages.create(...)` -- so a
later chunk can prove it changed nothing (model, max_tokens, temperature
present/absent, timeout and where it is set, retries, system, messages, and any
extra kwarg such as thinking/effort).

How it works: `anthropic.Anthropic` is replaced by a recorder (every module
does `import anthropic` inside the function, so one patch intercepts them
all). The recorder derives each call's site key from the CALLING frame
(`<module>.<enclosing function>#<n>`), so the coverage guard compares what was
really invoked against an AST scan of the source, not against a hand-kept list.

Regenerate deliberately (after an intended change) with:
    LLM_GOLDEN_REGEN=1 python -m pytest tests/test_llm_request_golden.py -q
and review the fixture diff.

NOT covered: app.py's AI Snapshot `_call_ai_brief` (app.py has no test harness).
"""
from __future__ import annotations

import ast
import datetime
import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "stock_analyzer"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "llm_requests_golden.json"

FAKE_KEY = "sk-test-golden-0000"
KEY_PLACEHOLDER = "<KEY>"
ABSENT = "<ABSENT>"

_CALL_KEYS = ("model", "max_tokens", "temperature", "timeout", "system", "messages")
_CTOR_KEYS = ("api_key", "max_retries", "timeout", "base_url")


# ── normalisation ────────────────────────────────────────────────────────────

def _norm(v):
    """JSON-serialisable, readable form of a kwarg value."""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in v.items()}
    cls = type(v)
    # anthropic.Timeout is httpx.Timeout
    if all(hasattr(v, a) for a in ("connect", "read", "write", "pool")):
        return {"__type__": f"{cls.__module__}.{cls.__qualname__}",
                "connect": v.connect, "read": v.read,
                "write": v.write, "pool": v.pool}
    return {"__type__": f"{cls.__module__}.{cls.__qualname__}", "repr": repr(v)}


# ── recorder fake ────────────────────────────────────────────────────────────

class Recorder:
    """Stands in for `anthropic.Anthropic`. `canned` is the reply text: a str
    (every call), a list (indexed by call order within the case) or a callable
    taking the create() kwargs."""

    def __init__(self):
        self.records: dict[str, dict] = {}
        self._counts: dict[str, int] = {}
        self._n_calls = 0
        self._lock = threading.Lock()
        self.canned = "ok"

    def client_factory(self, *args, **kwargs):
        assert not args, "Anthropic() positional args are not expected"
        return _FakeClient(self, kwargs)

    def _reply_text(self, kwargs, idx):
        c = self.canned
        if callable(c):
            return c(kwargs)
        if isinstance(c, (list, tuple)):
            return c[idx]
        return c


class _FakeMessages:
    def __init__(self, rec: Recorder, ctor_kwargs: dict):
        self._rec = rec
        self._ctor = ctor_kwargs

    def create(self, **kwargs):
        rec = self._rec
        # The call site is the first frame outside the shared helper: once a
        # module routes through stock_analyzer.llm.complete, the frame that
        # calls messages.create is llm.complete itself, and keying on it would
        # file every migrated site under one name.
        caller = sys._getframe(1)
        while caller.f_back is not None and caller.f_globals.get("__name__") == "stock_analyzer.llm":
            caller = caller.f_back
        mod = caller.f_globals.get("__name__", "?")
        mod = mod.removeprefix("stock_analyzer.")
        func = caller.f_code.co_name
        base = f"{mod}.{func}"

        # the real key we passed through must reach the constructor
        assert self._ctor.get("api_key") == FAKE_KEY, (
            f"{base}: api_key not passed through to anthropic.Anthropic()")

        ctor_rec = {k: (KEY_PLACEHOLDER if k == "api_key" else _norm(self._ctor[k]))
                    if k in self._ctor else ABSENT for k in _CTOR_KEYS}
        ctor_extra = {k: _norm(v) for k, v in self._ctor.items() if k not in _CTOR_KEYS}
        call_rec = {k: (_norm(kwargs[k]) if k in kwargs else ABSENT) for k in _CALL_KEYS}
        call_extra = {k: _norm(v) for k, v in kwargs.items() if k not in _CALL_KEYS}

        with rec._lock:
            n = rec._counts.get(base, 0)
            rec._counts[base] = n + 1
            idx = rec._n_calls
            rec._n_calls += 1
            key = f"{base}#{n}"
            assert key not in rec.records, f"duplicate site key {key}"
            rec.records[key] = {
                "client": {**ctor_rec, "other": ctor_extra},
                "create": {**call_rec, "other": call_extra},
            }
        text = rec._reply_text(kwargs, idx)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )


class _FakeClient:
    def __init__(self, rec, ctor_kwargs):
        self.kwargs = ctor_kwargs
        self.messages = _FakeMessages(rec, ctor_kwargs)


# ── cases (one per public entry point) ───────────────────────────────────────
# Each takes (rec, mp), sets rec.canned, calls the function with small fixed
# inputs, and asserts the result shows the canned reply was ACCEPTED.

def case_ai_provider(rec, mp):
    from stock_analyzer import ai_provider
    rec.canned = "A plan."
    out = ai_provider.call_llm("Claude (Anthropic)", "claude-sonnet-4-6", FAKE_KEY,
                               "SYSTEM TEXT", "USER TEXT", 500)
    assert out == "A plan."
    # a second model: the opus-4x request shape (no sampling), same fields
    out = ai_provider.call_llm("Claude (Anthropic)", "claude-opus-5", FAKE_KEY,
                               "SYSTEM TEXT", "USER TEXT", 500)
    assert out == "A plan."


def case_analyst_intel(rec, mp):
    from stock_analyzer import analyst_intel
    rec.canned = json.dumps({
        "article_date": "2026-10-01",
        "reports": [{"ticker": "SPOT", "company": "Spotify", "report_type": "other",
                     "analysts": [{"firm": "Bank of America", "analyst": None,
                                   "rating": "Buy", "price_target": 685, "upside_pct": 41}],
                     "thesis": ["t"], "catalysts": [], "risks": []}],
    })
    out = analyst_intel.extract_report("Article text about Spotify.", FAKE_KEY)
    assert out and out[0]["ticker"] == "SPOT"


def case_broker_screenshot(rec, mp):
    from stock_analyzer import broker_screenshot
    rec.canned = json.dumps({"Zzyzx Corp": "ZZYX"})
    text = "Zzyzx Corp market buy\nIndividual · Oct 1\n$100.00\n1 share at $100.00\n"
    out = broker_screenshot.parse_robinhood_text(
        text, api_key=FAKE_KEY, model="claude-opus-4-8",
        reference_date=datetime.date(2026, 10, 22))
    trades = out["trades"]
    assert len(trades) == 1 and trades.iloc[0]["ticker"] == "ZZYX"


def case_catalyst_stress(rec, mp):
    from stock_analyzer import catalyst_stress
    rec.canned = json.dumps({"narrative": "A macro print lands on a weak point."})
    evidence = {
        "top_macro": {"event": "CPI", "date": "2026-10-25", "days_out": 3,
                      "category": "Inflation", "overlap_tickers": ["AAA", "BBB"], "score": 12.5},
        "top_earnings": None, "blast_radius": [], "clusters": [],
    }
    out = catalyst_stress.generate_catalyst_narrative(evidence, FAKE_KEY)
    assert out == {"narrative": "A macro print lands on a weak point."}


def case_debate_agent(rec, mp):
    from stock_analyzer import debate_agent
    rec.canned = [
        "Bull opens.", "Bear answers.", "Bull rebuts.", "Bear closes.",
        json.dumps({"verdict": "bull_wins", "key_dispute": "valuation",
                    "bull_case_score": 70, "bear_case_score": 40, "grounded": True}),
    ]
    corpus = {"ticker": "AAA", "current_price": 100.0, "composite_score": 80.0,
              "composite_label": "Strong Buy", "sector": "Tech", "conviction": "High"}
    out = debate_agent.run_debate(corpus, "entry", FAKE_KEY)
    assert out["verdict"] == "bull_wins" and not out["partial"]
    assert len(out["transcript"]) == 4


def case_debrief_advisor(rec, mp):
    from stock_analyzer import debrief_advisor
    rec.canned = ("**What happened**\nFacts here.\n**Decisions you made**\nDecisions.\n"
                  "**Patterns this week**\nPatterns.\n**One thing to watch**\nWatch.")
    package = {"narratable": True, "week_start": "2026-10-12", "week_ending": "2026-10-16",
               "metrics_version": 2, "performance_pct": None,
               "result_reason": "fixed test reason."}
    out = debrief_advisor.generate_debrief(package, FAKE_KEY)
    assert out is not None and out["section_facts"] == "Facts here."


def case_earnings_intel(rec, mp):
    from stock_analyzer import earnings_intel
    rec.canned = json.dumps({"article_date": "2026-10-20", "records": [
        {"ticker": "JPM", "company": "JPMorgan Chase", "earnings_date": "2026-10-14"}]})
    out = earnings_intel.extract_playbook("Preview article.", datetime.date(2026, 10, 12), FAKE_KEY)
    assert out and out[0]["ticker"] == "JPM"
    rec.canned = json.dumps({"article_date": "2026-10-20", "records": [
        {"ticker": "JPM", "company": "JPMorgan Chase", "report_date": "2026-10-14",
         "actual_eps": 4.96, "estimated_eps": 4.61}]})
    out = earnings_intel.extract_results("Results article.", datetime.date(2026, 10, 15), FAKE_KEY)
    assert out and out[0]["actual_eps"] == 4.96


def case_intelligence_report(rec, mp):
    from stock_analyzer import intelligence_report
    rec.canned = ("**Entry quality**\nQ.\n**Signal discipline**\nS.\n**Pattern & focus**\nP.")
    package = {"period_start": "2026-09-22", "period_end": "2026-10-21", "has_data": True,
               "q0_ready": False, "min_graded": 5, "n_total": 4, "n_acted": 2, "n_missed": 2,
               "action_rate": 50.0, "avg_acted_pct": 3.0, "avg_missed_pct": 1.0,
               "avg_acted_alpha": 1.5, "avg_missed_alpha": -0.5, "missed_alpha": -2.0,
               "engine_alpha_pct": 0.5, "viz": None}
    out = intelligence_report.generate_report(package, FAKE_KEY)
    assert out is not None and out["section_entry_quality"] == "Q."


def case_missed_opportunity(rec, mp):
    from stock_analyzer import missed_opportunity
    corpus = [
        {"ticker": t, "sector": "Tech", "price_band": "20-50", "composite_band": "75-79",
         "verdict": "Confirmed", "outcome_label": "ran", "outcome_pct": 12.0, "alpha_pct": 8.0}
        for t in ("AAA", "BBB", "CCC")
    ]
    rec.canned = json.dumps({"patterns": [
        {"tickers": ["AAA", "BBB"], "shared_dimension": "sector", "shared_value": "Tech",
         "pattern_label": "Tech names ran"}]})
    out = missed_opportunity.generate_missed_opportunity_patterns(corpus, FAKE_KEY)
    assert out and len(out["patterns"]) == 1


def case_news_intelligence(rec, mp):
    from stock_analyzer import news_intelligence as ni
    rec.canned = json.dumps([{"idx": 0, "score": 0.2}])

    items = [{"ticker": "AAA", "title": "AAA stumbles on report", "compound": -0.5,
              "label": "Negative"}]
    out = ni.rescore_news_items_llm(items, FAKE_KEY)
    assert out[0]["compound"] == 0.2

    memo: dict = {}
    res, status = ni.rescore_brief_news(items, FAKE_KEY, window_start_ts=0.0,
                                        memo=memo, now_ts=1_000_000.0)
    assert res[0]["compound"] == 0.2 and status["filtered"] == 1

    heads = [{"headline": "AAA beats estimates", "score": -0.1, "label": "Neutral", "url": ""}]
    out = ni.rescore_headlines_llm(heads, FAKE_KEY, ticker="AAA")
    assert out is not None and out[0]["score"] == 0.2


def case_portfolio_qa(rec, mp):
    from stock_analyzer import portfolio_qa
    rec.canned = json.dumps({"intent": "trades_in_range", "ticker": None,
                             "start_date": "2026-10-12", "end_date": "2026-10-18",
                             "horizon_days": None, "reason": None})
    out = portfolio_qa.parse_question("what did I buy last week", FAKE_KEY,
                                      datetime.date(2026, 10, 22))
    assert out and out["intent"] == "trades_in_range"
    rec.canned = "You hold 2 positions worth $100."
    facts = {"found": True, "position_count": 2, "total_value": 100.0,
             "total_pnl_dollar": 5.0, "total_pnl_pct": 5.0, "best_ticker": "AAA",
             "best_pnl_pct": 9.0, "worst_ticker": "BBB", "worst_pnl_pct": -1.0}
    out = portfolio_qa.narrate_answer("portfolio_summary", facts, FAKE_KEY)
    assert out == "You hold 2 positions worth \\$100."


def case_premarket_stance(rec, mp):
    from stock_analyzer import premarket_stance
    rec.canned = "Futures flat, quiet tape.\nStance: Neutral"
    inputs = {"futures": [{"name": "ES", "chg_pct": 0.1}], "regime_label": "Neutral",
              "top_holdings": [{"ticker": "AAA", "weight": 10.0, "sector": "Tech", "signal": None}]}
    out = premarket_stance.generate_stance(inputs, FAKE_KEY)
    assert out and out["stance"] == "neutral"


def case_premortem_advisor(rec, mp):
    from stock_analyzer import premortem_advisor
    rec.canned = json.dumps([
        {"angle": "pillar", "argument": "Valuation is stretched."},
        {"angle": "portfolio", "argument": "Adds to tech weight."},
        {"angle": "macro", "argument": "Rate risk."},
    ])
    inputs = {"engine": {"composite": 80.0, "band": "Strong Buy", "driving_pillar": "Technical"},
              "portfolio": {"n_positions": 5, "top_sector": "Tech",
                            "top_sector_weight_pct": 40.0, "this_sector": "Tech"}}
    out = premortem_advisor.generate_case_against("AAA", inputs, FAKE_KEY)
    assert out and len(out["case_against"]) == 3


def case_premortem_monitor(rec, mp):
    from stock_analyzer import premortem_monitor
    rec.canned = json.dumps({"checkable": True, "direction": "below", "price_level": 150.0})
    out = premortem_monitor.extract_trigger("I will exit if it breaks $150.", FAKE_KEY)
    assert out == {"checkable": True, "direction": "below", "price_level": 150.0}


def case_regime_stress(rec, mp):
    from stock_analyzer import regime_stress
    rec.canned = json.dumps({"scenario_narrative": "Stress builds slowly.",
                             "indicator_watchlist": ["VIX"]})
    evidence = {"regime": {"label": "Risk-off", "fed_trend": "hiking", "cpi_yoy": 3.1,
                           "confidence": 60, "signals": [("VIX", "elevated")]},
                "blast_radius": [], "clusters": []}
    out = regime_stress.generate_regime_scenario(evidence, FAKE_KEY)
    assert out and out["scenario_narrative"] == "Stress builds slowly."


def case_structural_scanner(rec, mp):
    from stock_analyzer import structural_scanner
    rec.canned = "The book leans on one cluster."
    evidence = {"clusters": [{"size": 3, "tickers": ["AAA", "BBB", "CCC"],
                              "avg_internal_corr": 0.7, "combined_weight_pct": 30.0,
                              "tier": "warning"}],
                "weakest_links": [], "blast_radius": []}
    out = structural_scanner.generate_structural_narrative(evidence, FAKE_KEY)
    assert out == "The book leans on one cluster."


def case_thesis_advisor(rec, mp):
    from stock_analyzer import thesis_advisor as ta
    rec.canned = "Still on track.\nVerdict: INTACT"
    inputs = ta.build_review_inputs(technical={"above_sma50": True, "rsi": 55.0,
                                               "momentum_1m_pct": 3.0},
                                    news_headlines=["AAA wins contract"])
    out = ta.review_thesis("AAA", "Growth continues. Breaks if sales fall.", inputs, FAKE_KEY)
    assert out and out["status"] == "INTACT"

    rec.canned = "AAA should keep growing. Breaks if sales fall."
    ainputs = ta.build_authoring_inputs(company_name="AAA Inc", sector="Tech",
                                        engine={"composite": 80.0, "band": "Strong Buy"})
    out = ta.draft_thesis("AAA", ainputs, FAKE_KEY)
    assert out and out["draft"].startswith("AAA should")

    rec.canned = "Beat on both lines.\nVerdict: INTACT"
    res = {"actual_eps": 1.1, "estimated_eps": 1.0, "eps_beat": True, "eps_surprise_pct": 10.0,
           "actual_revenue": 2.0, "estimated_revenue": 1.9, "rev_beat": True,
           "guidance_direction": "raised", "key_narrative": "Strong quarter."}
    out = ta.generate_earnings_thesis_update("AAA", "Growth continues.", res, FAKE_KEY)
    assert out and out["suggested_status"] == "INTACT"


def case_thesis_cluster(rec, mp):
    from stock_analyzer import thesis_cluster
    corpus = [{"ticker": "AAA", "sector": "Tech", "thesis_text": "Cloud demand keeps growing."},
              {"ticker": "BBB", "sector": "Tech", "thesis_text": "Cloud spending stays strong."}]
    rec.canned = json.dumps({"clusters": [
        {"tickers": ["AAA", "BBB"], "shared_assumption": "Cloud demand persists",
         "quotes": {"AAA": "Cloud demand", "BBB": "Cloud spending"}}]})
    out = thesis_cluster.generate_thesis_clusters(corpus, FAKE_KEY)
    assert out and out["truncated"] is False and len(out["clusters"]) == 1


def case_thesis_red_team(rec, mp):
    from stock_analyzer import thesis_red_team
    rec.canned = json.dumps([{"claim": "Relative strength is fading.", "severity": "medium",
                              "signal_basis": "RS vs SPY -3.0pp"}])
    inputs = thesis_red_team.build_counter_evidence_inputs(
        "AAA", 100.0, 90.0, 30, "Growth continues.", "Exit below $85.", "WATCH", -3.0, -2.0)
    out = thesis_red_team.generate_counter_evidence("AAA", inputs, FAKE_KEY)
    assert out and out[0]["severity"] == "medium"


CASES = {
    "ai_provider": case_ai_provider,
    "analyst_intel": case_analyst_intel,
    "broker_screenshot": case_broker_screenshot,
    "catalyst_stress": case_catalyst_stress,
    "debate_agent": case_debate_agent,
    "debrief_advisor": case_debrief_advisor,
    "earnings_intel": case_earnings_intel,
    "intelligence_report": case_intelligence_report,
    "missed_opportunity": case_missed_opportunity,
    "news_intelligence": case_news_intelligence,
    "portfolio_qa": case_portfolio_qa,
    "premarket_stance": case_premarket_stance,
    "premortem_advisor": case_premortem_advisor,
    "premortem_monitor": case_premortem_monitor,
    "regime_stress": case_regime_stress,
    "structural_scanner": case_structural_scanner,
    "thesis_advisor": case_thesis_advisor,
    "thesis_cluster": case_thesis_cluster,
    "thesis_red_team": case_thesis_red_team,
}


def _capture(case_fn) -> dict:
    import anthropic
    rec = Recorder()
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(anthropic, "Anthropic", rec.client_factory)
        case_fn(rec, mp)
    finally:
        mp.undo()
    assert rec.records, "case made no messages.create call"
    return rec.records


def _capture_all() -> dict:
    out: dict = {}
    for name, fn in CASES.items():
        got = _capture(fn)
        clash = set(got) & set(out)
        assert not clash, f"case {name} reuses site keys {sorted(clash)}"
        out.update(got)
    return dict(sorted(out.items()))


def _dump(d: dict) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


@pytest.fixture(scope="module")
def golden() -> dict:
    if os.environ.get("LLM_GOLDEN_REGEN") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(_dump(_capture_all()), encoding="utf-8")
    assert FIXTURE.exists(), (
        "missing tests/fixtures/llm_requests_golden.json -- generate with "
        "LLM_GOLDEN_REGEN=1")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _diff(site: str, want, got) -> list[str]:
    lines = []
    if isinstance(want, dict) and isinstance(got, dict):
        for k in sorted(set(want) | set(got)):
            if k not in want:
                lines.append(f"  {site}.{k}: NEW (not in golden): {got[k]!r}")
            elif k not in got:
                lines.append(f"  {site}.{k}: MISSING (golden had {want[k]!r})")
            else:
                lines += _diff(f"{site}.{k}", want[k], got[k])
    elif want != got:
        lines.append(f"  {site}: golden={want!r}\n  {' ' * len(site)}  live  ={got!r}")
    return lines


@pytest.mark.parametrize("case_name", sorted(CASES))
def test_requests_match_golden(case_name, golden):
    got = _capture(CASES[case_name])
    problems: list[str] = []
    for site, live in got.items():
        if site not in golden:
            problems.append(f"{site}: not in golden fixture")
            continue
        problems += _diff(site, golden[site], live)
    if problems:
        pytest.fail("LLM request changed vs golden:\n" + "\n".join(problems), pytrace=False)


def test_golden_has_no_stale_sites(golden):
    live = _capture_all()
    assert set(live) == set(golden), (
        f"only in live: {sorted(set(live) - set(golden))}; "
        f"only in golden: {sorted(set(golden) - set(live))}")


def test_golden_secret_is_placeholder(golden):
    for site, rec in golden.items():
        assert rec["client"]["api_key"] == KEY_PLACEHOLDER, site
    assert FAKE_KEY not in FIXTURE.read_text(encoding="utf-8")


def _is_llm_complete_call(node) -> bool:
    """`llm.complete(...)` / `_llm.complete(...)`: the shared helper's entry
    point (stock_analyzer/llm.py). Migrated call sites must call it through a
    module alias ending in "llm" so this guard can see them."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "complete"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id.endswith("llm"))


def _ast_call_sites() -> set[tuple[str, str]]:
    """(module, enclosing function) for every Anthropic call site in
    stock_analyzer/*.py: a `<x>.messages.create(...)`, or a call to the shared
    helper `llm.complete(...)`. llm.py itself is the helper, not a site: its
    own messages.create is what every migrated site goes through."""
    found: set[tuple[str, str]] = set()
    for path in sorted(PKG_DIR.glob("*.py")):
        if path.stem == "llm":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        def visit(node, func):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func = node.name
            if ((isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "messages")
                    or _is_llm_complete_call(node)):
                found.add((path.stem, func or "<module>"))
            for child in ast.iter_child_nodes(node):
                visit(child, func)

        visit(tree, None)
    return found


def test_every_messages_create_site_is_covered(golden):
    ast_sites = _ast_call_sites()
    covered = {tuple(k.rsplit("#", 1)[0].rsplit(".", 1)) for k in golden}
    assert ast_sites == covered, (
        f"call sites without a golden snapshot: {sorted(ast_sites - covered)}; "
        f"golden entries with no call site: {sorted(covered - ast_sites)}")
