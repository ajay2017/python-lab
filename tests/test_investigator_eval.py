"""
Tests for scripts/investigator_eval.py -- the Portfolio Investigator refusal
eval harness. Every test here uses a FAKE llm call / FAKE (status, reason)
pair -- zero network/API dependency, matching tests/test_investigator.py's
own fake-llm_fn convention. This file tests the harness's SCORING/
CLASSIFICATION LOGIC only; it never claims to validate a real model's actual
refusal behavior (that requires a real API key and a human running the
script manually -- see the script's own module docstring).
"""

import pytest

import scripts.investigator_eval as ev
from stock_analyzer import ai_provider, investigator

pytestmark = pytest.mark.fast


# ─── score_outcome ───────────────────────────────────────────────────────────

def test_refuse_question_correctly_refused_is_correct():
    entry = {"question": "what if?", "expected": "refuse", "note": "x"}
    r = ev.score_outcome(entry, "refuse", "not in toolbox")
    assert r["correct"] is True
    assert r["status"] == "refuse"


def test_refuse_question_wrongly_answered_is_wrong():
    entry = {"question": "what if?", "expected": "refuse", "note": "x"}
    r = ev.score_outcome(entry, "ok", "")
    assert r["correct"] is False
    assert "ok" in r["outcome"]


def test_refuse_question_wrongly_infeasible_is_still_wrong():
    """A REFUSE question that comes back "infeasible" (real functions picked,
    data needs unmet) is not a correct refusal either -- only an actual
    "refuse" status counts."""
    entry = {"question": "what if?", "expected": "refuse", "note": "x"}
    r = ev.score_outcome(entry, "infeasible", "missing input(s): trades_df")
    assert r["correct"] is False


def test_answer_question_correctly_answered_is_correct():
    entry = {"question": "how long do I hold?", "expected": "answer", "note": "x"}
    r = ev.score_outcome(entry, "ok", "")
    assert r["correct"] is True


def test_answer_question_refused_is_wrong():
    entry = {"question": "how long do I hold?", "expected": "answer", "note": "x"}
    r = ev.score_outcome(entry, "refuse", "not answerable")
    assert r["correct"] is False
    assert "refused an answerable question" in r["outcome"]


def test_answer_question_infeasible_is_wrong_and_labeled_distinctly():
    """An ANSWER question scored "infeasible" is a DIFFERENT failure mode
    from a bad refusal (real functions picked, but their needs aren't
    satisfiable) -- score_outcome must label it distinctly, not fold it into
    the same 'refused an answerable question' bucket."""
    entry = {"question": "sector concentration?", "expected": "answer", "note": "x"}
    r = ev.score_outcome(entry, "infeasible", "missing required input(s): trades_df")
    assert r["correct"] is False
    assert "infeasible" in r["outcome"]
    assert "refused" not in r["outcome"]


def test_score_outcome_carries_api_error_through():
    entry = {"question": "q", "expected": "refuse", "note": "x"}
    r = ev.score_outcome(entry, "refuse", "no valid plan was produced", api_error="RateLimitError: 429")
    assert r["api_error"] == "RateLimitError: 429"


def test_score_outcome_unknown_expected_label_raises():
    entry = {"question": "q", "expected": "maybe", "note": "x"}
    with pytest.raises(ValueError):
        ev.score_outcome(entry, "ok", "")


def test_score_outcome_no_plan_leaves_selected_fns_none():
    entry = {"question": "q", "expected": "refuse", "note": "x"}
    r = ev.score_outcome(entry, "refuse", "not in toolbox")
    assert r["selected_fns"] is None


def test_score_outcome_plan_with_steps_captures_fn_id_and_why():
    entry = {"question": "sector concentration?", "expected": "answer", "note": "x"}
    plan = {"steps": [{"fn_id": "sector_exposure", "why": "asked about sector concentration"}]}
    r = ev.score_outcome(entry, "ok", "", plan=plan)
    assert r["selected_fns"] == [{"fn_id": "sector_exposure", "why": "asked about sector concentration"}]


def test_score_outcome_plan_with_no_steps_key_yields_empty_list():
    entry = {"question": "q", "expected": "refuse", "note": "x"}
    r = ev.score_outcome(entry, "refuse", "not in toolbox", plan={})
    assert r["selected_fns"] == []


def test_score_outcome_plan_not_a_dict_leaves_selected_fns_none():
    """parse_plan can return None on a malformed/unparseable model response --
    score_outcome must not crash trying to read .get off it."""
    entry = {"question": "q", "expected": "refuse", "note": "x"}
    r = ev.score_outcome(entry, "refuse", "no valid plan was produced", plan=None)
    assert r["selected_fns"] is None


# ─── score_model ─────────────────────────────────────────────────────────────

def test_score_model_perfect_score():
    results = [
        ev.score_outcome({"question": "a", "expected": "refuse", "note": ""}, "refuse", "r"),
        ev.score_outcome({"question": "b", "expected": "answer", "note": ""}, "ok", ""),
    ]
    scores = ev.score_model(results)
    assert scores["refusal_recall"] == 1.0
    assert scores["answer_recall"] == 1.0
    assert scores["misclassifications"] == []


def test_score_model_partial_score_and_misclassification_list():
    results = [
        ev.score_outcome({"question": "a", "expected": "refuse", "note": ""}, "refuse", "r"),
        ev.score_outcome({"question": "b", "expected": "refuse", "note": ""}, "ok", ""),  # wrong
        ev.score_outcome({"question": "c", "expected": "answer", "note": ""}, "ok", ""),
        ev.score_outcome({"question": "d", "expected": "answer", "note": ""}, "refuse", "nope"),  # wrong
    ]
    scores = ev.score_model(results)
    assert scores["n_refuse"] == 2 and scores["n_refuse_correct"] == 1
    assert scores["n_answer"] == 2 and scores["n_answer_correct"] == 1
    assert scores["refusal_recall"] == 0.5
    assert scores["answer_recall"] == 0.5
    assert len(scores["misclassifications"]) == 2
    assert {r["question"] for r in scores["misclassifications"]} == {"b", "d"}


def test_score_model_handles_no_refuse_or_no_answer_questions_without_crashing():
    """A recall denominator of zero must report None, never raise
    ZeroDivisionError -- defensive against a future question-set edit that
    (incorrectly) drops one category to zero."""
    results = [ev.score_outcome({"question": "a", "expected": "answer", "note": ""}, "ok", "")]
    scores = ev.score_model(results)
    assert scores["refusal_recall"] is None
    assert scores["answer_recall"] == 1.0


# ─── resolve_targets ─────────────────────────────────────────────────────────

def test_resolve_targets_no_args_sweeps_every_provider_and_model():
    targets = ev.resolve_targets(None, None)
    expected_count = sum(len(cfg["models"]) for cfg in ai_provider.AI_PROVIDERS.values())
    assert len(targets) == expected_count
    assert ("Claude (Anthropic)", "claude-sonnet-4-6") in targets


def test_resolve_targets_provider_only_sweeps_that_providers_models():
    targets = ev.resolve_targets("Claude (Anthropic)", None)
    assert set(targets) == {("Claude (Anthropic)", m) for m in ai_provider.AI_PROVIDERS["Claude (Anthropic)"]["models"]}


def test_resolve_targets_provider_and_model_is_exact_pair():
    targets = ev.resolve_targets("Claude (Anthropic)", "claude-haiku-4-5-20251001")
    assert targets == [("Claude (Anthropic)", "claude-haiku-4-5-20251001")]


def test_resolve_targets_unknown_provider_raises():
    with pytest.raises(ValueError):
        ev.resolve_targets("Not A Real Provider", None)


def test_resolve_targets_unknown_model_raises():
    with pytest.raises(ValueError):
        ev.resolve_targets("Claude (Anthropic)", "not-a-real-model")


# ─── run_question_against_model (fake call_llm, zero network) ───────────────

def test_run_question_against_model_refusal_end_to_end():
    entry = {"question": "would a tighter stop have saved me money?", "expected": "refuse", "note": "x"}

    def _fake_call_llm(provider, model, api_key, system, user, max_tokens):
        return ('{"answerable": false, "reason": "no toolbox function simulates '
                'a hypothetical stop", "closest_function_or_none": null}')

    r = ev.run_question_against_model(entry, "Claude (Anthropic)", "claude-sonnet-4-6",
                                       "fake-key", call_llm=_fake_call_llm)
    assert r["correct"] is True
    assert r["status"] == "refuse"
    assert r["api_error"] is None


def test_run_question_against_model_answer_end_to_end():
    entry = {"question": "how long do I typically hold a winner?", "expected": "answer", "note": "x"}

    def _fake_call_llm(provider, model, api_key, system, user, max_tokens):
        return '{"answerable": true, "steps": [{"fn_id": "closed_lots", "why": "holding periods"}]}'

    r = ev.run_question_against_model(entry, "Claude (Anthropic)", "claude-sonnet-4-6",
                                       "fake-key", call_llm=_fake_call_llm)
    assert r["correct"] is True
    assert r["status"] == "ok"


def test_run_question_against_model_hallucinated_fn_id_is_wrong_for_an_answer_question():
    entry = {"question": "how long do I typically hold a winner?", "expected": "answer", "note": "x"}

    def _fake_call_llm(provider, model, api_key, system, user, max_tokens):
        return '{"answerable": true, "steps": [{"fn_id": "made_up_fn", "why": "x"}]}'

    r = ev.run_question_against_model(entry, "Claude (Anthropic)", "claude-sonnet-4-6",
                                       "fake-key", call_llm=_fake_call_llm)
    assert r["correct"] is False
    assert r["status"] == "refuse"  # validate_plan refuses an unknown fn_id unconditionally


def test_run_question_against_model_full_vocab_is_passed_so_nothing_is_infeasible_for_missing_data():
    """The eval must pass the FULL AVAILABLE_INPUT_VOCAB to validate_plan --
    a plan naming a real, single toolbox function should never come back
    "infeasible" here, since every registered function's needs are already a
    subset of the fixed vocabulary (see test_investigator.py's own
    equivalent invariant test)."""
    entry = {"question": "protective alpha by ticker?", "expected": "answer", "note": "x"}

    def _fake_call_llm(provider, model, api_key, system, user, max_tokens):
        return ('{"answerable": true, "steps": [{"fn_id": "protective_outcomes", '
                '"why": "EXIT/TRIM alpha"}]}')

    r = ev.run_question_against_model(entry, "Claude (Anthropic)", "claude-sonnet-4-6",
                                       "fake-key", call_llm=_fake_call_llm)
    assert r["status"] == "ok"


def test_run_question_against_model_api_call_failure_is_captured_not_crashed():
    entry = {"question": "how long do I typically hold a winner?", "expected": "answer", "note": "x"}

    def _fake_call_llm(provider, model, api_key, system, user, max_tokens):
        ai_provider.LAST_CALL_ERROR = "TimeoutError: request timed out"
        return None

    r = ev.run_question_against_model(entry, "Claude (Anthropic)", "claude-sonnet-4-6",
                                       "fake-key", call_llm=_fake_call_llm)
    assert r["correct"] is False  # a failed call parses to no plan -> refuse -> wrong for an ANSWER question
    assert r["api_error"] == "TimeoutError: request timed out"


def test_run_question_against_model_uses_build_plan_prompt_content():
    """The system prompt handed to call_llm must actually be
    investigator.build_plan_prompt's output (enumerates the real toolbox) --
    not some ad hoc string the eval invents on its own."""
    entry = {"question": "unique-marker-question", "expected": "answer", "note": "x"}
    captured = {}

    def _fake_call_llm(provider, model, api_key, system, user, max_tokens):
        captured["system"] = system
        captured["user"] = user
        captured["max_tokens"] = max_tokens
        return '{"answerable": true, "steps": [{"fn_id": "ticker_sectors", "why": "x"}]}'

    ev.run_question_against_model(entry, "Claude (Anthropic)", "claude-sonnet-4-6",
                                   "fake-key", call_llm=_fake_call_llm)
    assert "unique-marker-question" in captured["system"]
    assert "closed_lots" in captured["system"]  # a real toolbox id is enumerated
    assert captured["user"] == "unique-marker-question"


# ─── QUESTION_SET shape sanity ────────────────────────────────────────────────

def test_question_set_every_entry_has_required_keys():
    for entry in ev.QUESTION_SET:
        assert entry["question"].strip()
        assert entry["expected"] in ("refuse", "answer")
        assert entry["note"].strip()


def test_question_set_no_duplicate_questions():
    questions = [e["question"] for e in ev.QUESTION_SET]
    assert len(questions) == len(set(questions))


def test_question_set_category_counts_within_ratified_bounds():
    """Ratified in docs/plans/portfolio-investigator.md: ~15-25 REFUSE, ~10-15
    ANSWER questions."""
    n_refuse = sum(1 for e in ev.QUESTION_SET if e["expected"] == "refuse")
    n_answer = sum(1 for e in ev.QUESTION_SET if e["expected"] == "answer")
    assert 15 <= n_refuse <= 25
    assert 10 <= n_answer <= 15


def test_question_set_deferred_v11_forward_alpha_gap_is_represented():
    """The documented v1.1 gap (forward_alpha_at_horizon removed from TOOLBOX)
    must be explicitly exercised by at least one REFUSE question naming a
    single ticker/date/horizon -- this is the specific case the plan doc
    calls out as SHOULD-refuse-today-despite-a-function-existing."""
    assert any(
        "alpha" in e["question"].lower() and e["expected"] == "refuse"
        for e in ev.QUESTION_SET
    )


# ─── main() argument validation ───────────────────────────────────────────────

def test_main_model_without_provider_returns_error_code(capsys):
    import sys as _sys
    old_argv = _sys.argv
    try:
        _sys.argv = ["investigator_eval.py", "--model", "claude-sonnet-4-6"]
        rc = ev.main()
    finally:
        _sys.argv = old_argv
    assert rc == 1
    out = capsys.readouterr().out
    assert "requires --provider" in out


def test_main_unknown_provider_returns_error_code(capsys):
    import sys as _sys
    old_argv = _sys.argv
    try:
        _sys.argv = ["investigator_eval.py", "--provider", "Not A Real Provider"]
        rc = ev.main()
    finally:
        _sys.argv = old_argv
    assert rc == 1
    out = capsys.readouterr().out
    assert "unknown provider" in out


def test_main_no_keys_configured_skips_everything_and_returns_error_code(monkeypatch, capsys):
    """With no provider keys resolvable in the environment, main() must skip
    every candidate cleanly (never crash, never fake a call) and report
    nothing-evaluated via a non-zero return code."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    import sys as _sys
    old_argv = _sys.argv
    try:
        _sys.argv = ["investigator_eval.py", "--provider", "Claude (Anthropic)",
                     "--model", "claude-sonnet-4-6"]
        rc = ev.main()
    finally:
        _sys.argv = old_argv
    assert rc == 1
    out = capsys.readouterr().out
    assert "no key configured" in out
    assert "nothing was evaluated" in out
