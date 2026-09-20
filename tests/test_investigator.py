"""
Tests for stock_analyzer/investigator.py — 🔎 Portfolio Investigator's
fixed-toolbox orchestration and, above all, its refusal gate.

`validate_plan` is the safety-critical function in this module (a
hallucinated toolbox function must ALWAYS be refused, unconditionally) so it
gets the most exhaustive coverage. `investigate()`'s tests use a fake
`llm_fn` — no real network/API calls anywhere in this file.
"""

from datetime import date

import pandas as pd
import pytest

from stock_analyzer import investigator as inv
from stock_analyzer.constants import INVESTIGATOR_MAX_LLM_CALLS

pytestmark = pytest.mark.fast


# ─── validate_plan — the refusal gate ───────────────────────────────────────

def test_hallucinated_fn_id_always_refused():
    """(a) An unknown fn_id must be refused, no exceptions — even alongside
    an otherwise-valid real step."""
    plan = {"answerable": True, "steps": [
        {"fn_id": "closed_lots", "why": "real"},
        {"fn_id": "totally_made_up_function", "why": "hallucinated"},
    ]}
    status, reason = inv.validate_plan(plan, inv.AVAILABLE_INPUT_VOCAB)
    assert status == "refuse"
    assert "totally_made_up_function" in reason


def test_hallucinated_fn_id_refused_even_with_full_available_inputs():
    """A hallucinated id is refused unconditionally — not just when inputs
    happen to be missing too."""
    plan = {"answerable": True, "steps": [{"fn_id": "nope", "why": "x"}]}
    status, _ = inv.validate_plan(plan, inv.AVAILABLE_INPUT_VOCAB)
    assert status == "refuse"


def test_empty_steps_list_refused():
    """(b) A compute-less plan (steps == []) is refused, not silently treated
    as a valid empty investigation."""
    plan = {"answerable": True, "steps": []}
    status, reason = inv.validate_plan(plan, inv.AVAILABLE_INPUT_VOCAB)
    assert status == "refuse"
    assert "no toolbox functions" in reason


def test_missing_steps_key_refused():
    plan = {"answerable": True}
    status, _ = inv.validate_plan(plan, inv.AVAILABLE_INPUT_VOCAB)
    assert status == "refuse"


def test_unsatisfiable_needs_is_infeasible_and_names_the_missing_input():
    """(c) A plan whose combined needs exceed available_inputs -> infeasible,
    with the specific missing input named in the reason string."""
    plan = {"answerable": True, "steps": [{"fn_id": "rec_outcomes", "why": "x"}]}
    # rec_outcomes needs recs_df/trades_df/current_prices/spy_close_by_date —
    # only give it trades_df.
    status, reason = inv.validate_plan(plan, frozenset({"trades_df"}))
    assert status == "infeasible"
    assert "recs_df" in reason


def test_valid_satisfiable_plan_is_ok():
    """(d) A valid, satisfiable, non-empty plan -> ok."""
    plan = {"answerable": True, "steps": [{"fn_id": "closed_lots", "why": "x"}]}
    status, reason = inv.validate_plan(plan, frozenset({"trades_df"}))
    assert status == "ok"
    assert reason == ""


def test_answerable_false_refused_with_own_reason():
    """(e) answerable: False -> refuse, using the plan's own stated reason."""
    plan = {"answerable": False, "reason": "no toolbox function measures that",
            "closest_function_or_none": None}
    status, reason = inv.validate_plan(plan, inv.AVAILABLE_INPUT_VOCAB)
    assert status == "refuse"
    assert reason == "no toolbox function measures that"


def test_answerable_false_falls_back_to_generic_reason_when_none_given():
    plan = {"answerable": False}
    status, reason = inv.validate_plan(plan, inv.AVAILABLE_INPUT_VOCAB)
    assert status == "refuse"
    assert reason


def test_zero_needs_function_always_feasible():
    """ticker_sectors declares no needs at all -- feasible even with zero
    available inputs."""
    plan = {"answerable": True, "steps": [{"fn_id": "ticker_sectors", "why": "x"}]}
    status, _ = inv.validate_plan(plan, frozenset())
    assert status == "ok"


def test_non_dict_plan_refused():
    status, _ = inv.validate_plan(None, inv.AVAILABLE_INPUT_VOCAB)
    assert status == "refuse"


# ─── parse_plan ──────────────────────────────────────────────────────────────

def test_parse_plan_extracts_json_embedded_in_prose():
    raw = (
        "Sure, here's my plan:\n"
        '{"answerable": true, "steps": [{"fn_id": "closed_lots", "why": "holding periods"}]}\n'
        "Let me know if you need anything else!"
    )
    plan = inv.parse_plan(raw)
    assert plan is not None
    assert plan["answerable"] is True
    assert plan["steps"] == [{"fn_id": "closed_lots", "why": "holding periods"}]


def test_parse_plan_malformed_json_returns_none():
    assert inv.parse_plan("not json at all, sorry") is None


def test_parse_plan_empty_string_returns_none():
    assert inv.parse_plan("") is None
    assert inv.parse_plan(None) is None


def test_parse_plan_answerable_false_shape():
    raw = '{"answerable": false, "reason": "no fn for that", "closest_function_or_none": "closed_lots"}'
    plan = inv.parse_plan(raw)
    assert plan == {"answerable": False, "reason": "no fn for that", "closest_function_or_none": "closed_lots"}


def test_parse_plan_steps_not_a_list_returns_none():
    raw = '{"answerable": true, "steps": "closed_lots"}'
    assert inv.parse_plan(raw) is None


def test_parse_plan_step_missing_fn_id_returns_none():
    raw = '{"answerable": true, "steps": [{"why": "no id given"}]}'
    assert inv.parse_plan(raw) is None


# ─── execute_plan ────────────────────────────────────────────────────────────

def test_execute_plan_one_failing_step_does_not_block_others(monkeypatch):
    def _boom(_bundle):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(inv.TOOLBOX, "closed_lots", {**inv.TOOLBOX["closed_lots"], "adapter": _boom})
    plan = {"answerable": True, "steps": [
        {"fn_id": "closed_lots", "why": "x"},
        {"fn_id": "ticker_sectors", "why": "y"},
    ]}
    result = inv.execute_plan(plan, {})
    assert "closed_lots" not in result or "error" not in result.get("closed_lots", {})
    assert "ticker_sectors" in result
    assert "ticker_sectors" in result["ticker_sectors"]
    assert "closed_lots" in result["errors"]
    assert "kaboom" in result["errors"]["closed_lots"]


def test_execute_plan_never_calls_a_function_not_in_the_plan(monkeypatch):
    calls = []

    def _spy_adapter(name):
        def _inner(_bundle):
            calls.append(name)
            return {}
        return _inner

    for fn_id in inv.TOOLBOX:
        monkeypatch.setitem(inv.TOOLBOX, fn_id, {**inv.TOOLBOX[fn_id], "adapter": _spy_adapter(fn_id)})

    plan = {"answerable": True, "steps": [{"fn_id": "ticker_sectors", "why": "x"}]}
    inv.execute_plan(plan, {})
    assert calls == ["ticker_sectors"]


# ─── verify_fetch ────────────────────────────────────────────────────────────

def test_verify_fetch_passes_on_fully_populated_subset():
    ok, why = inv.verify_fetch({"trades_df": pd.DataFrame({"a": [1]}), "current_prices": {"AAPL": 1.0}})
    assert ok is True
    assert why == ""


def test_verify_fetch_flags_none_value():
    ok, why = inv.verify_fetch({"trades_df": None})
    assert ok is False
    assert "trades_df" in why


def test_verify_fetch_flags_empty_current_prices_but_not_empty_trades_df():
    ok, why = inv.verify_fetch({
        "current_prices": {},
        "trades_df": pd.DataFrame(),  # genuinely zero trades -- legitimate, not a failure
    })
    assert ok is False
    assert "current_prices" in why
    assert "trades_df" not in why


def test_verify_fetch_empty_dict_is_fine_when_nothing_requested():
    ok, why = inv.verify_fetch({})
    assert ok is True


# ─── build_sync_draft — zero filesystem I/O ─────────────────────────────────

def test_build_sync_draft_never_touches_the_filesystem(monkeypatch):
    """Hard redline: this function must never open/write a file (the running
    container's filesystem is ephemeral and has no path to the owner's repo
    or memory files -- see docs/plans/portfolio-investigator.md, THE DEFECT).
    Patches builtins.open to raise if ever called, proving no I/O path exists
    even indirectly."""
    def _open_should_never_be_called(*a, **k):
        raise AssertionError("build_sync_draft must never call open()")

    monkeypatch.setattr("builtins.open", _open_should_never_be_called)
    drafts = inv.build_sync_draft(
        "did my trims outperform?", "Yes, on average.",
        [{"target": "docs/requirements.md", "hint": "append to F-xxx"}],
    )
    assert len(drafts) == 1
    assert drafts[0]["target"] == "docs/requirements.md"
    assert "did my trims outperform?" in drafts[0]["draft_markdown"]


def test_build_sync_draft_handles_no_targets():
    assert inv.build_sync_draft("q", "report", []) == []
    assert inv.build_sync_draft("q", "report", None) == []


# ─── investigate() orchestrator ─────────────────────────────────────────────

def _bundle_full():
    return {
        "trades_df": pd.DataFrame({
            "id": [1, 2],
            "ticker": ["AAA", "AAA"],
            "action": ["BUY", "SELL"],
            "shares": [10.0, 10.0],
            "price": [10.0, 12.0],
            "traded_at": ["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"],
        }),
        "exit_signals_df": pd.DataFrame(),
        "recs_df": pd.DataFrame(),
        "current_prices": {"AAA": 10.0},
        "spy_close_by_date": {date(2026, 1, 1): 100.0},
        "port_df": pd.DataFrame({"Ticker": ["AAA"], "Shares": [1]}),
    }


def test_investigate_always_garbage_llm_never_loops_past_the_call_cap():
    """A fake llm_fn that ALWAYS returns unparseable garbage must stop within
    INVESTIGATOR_MAX_LLM_CALLS total calls and return a visible non-answer --
    never an infinite loop. The plan stage caps itself at exactly one
    re-plan attempt (2 calls) regardless of how high max_calls is, per the
    orchestrator's own design -- so this asserts an upper bound, not
    equality to the cap."""
    call_count = {"n": 0}

    def _garbage_llm(system, user, max_tokens):
        call_count["n"] += 1
        return "not json, sorry, can't help"

    result = inv.investigate("how did I do?", _bundle_full(), _garbage_llm)

    assert call_count["n"] <= INVESTIGATOR_MAX_LLM_CALLS
    assert result["answered"] is False
    assert result["status"] == "no_plan"
    assert result["report_text"] is None


def test_investigate_stops_forever_even_with_a_tiny_call_budget():
    call_count = {"n": 0}

    def _garbage_llm(system, user, max_tokens):
        call_count["n"] += 1
        return None  # simulates an LLM-call failure, not just bad JSON

    result = inv.investigate("how did I do?", _bundle_full(), _garbage_llm, max_calls=1)
    assert call_count["n"] == 1
    assert result["answered"] is False


def test_investigate_missing_required_input_never_reaches_execute_plan(monkeypatch):
    """A data_bundle missing/empty for a key the plan's needs require must
    return a 'couldn't complete' result and must NOT call execute_plan at
    all."""
    def _plan_llm(system, user, max_tokens):
        return '{"answerable": true, "steps": [{"fn_id": "protective_outcomes", "why": "check EXIT/TRIM alpha"}]}'

    bundle = _bundle_full()
    bundle["current_prices"] = {}  # present but empty -> verify_fetch failure

    executed = {"called": False}
    real_execute_plan = inv.execute_plan

    def _spy_execute_plan(plan, data_bundle):
        executed["called"] = True
        return real_execute_plan(plan, data_bundle)

    monkeypatch.setattr(inv, "execute_plan", _spy_execute_plan)

    result = inv.investigate("were my TRIMs justified?", bundle, _plan_llm)

    assert executed["called"] is False
    assert result["answered"] is False
    assert result["status"] == "incomplete_data"
    assert "current_prices" in result["reason"]


def test_investigate_refusal_returns_immediately_no_retry():
    calls = {"n": 0}

    def _refusing_llm(system, user, max_tokens):
        calls["n"] += 1
        return '{"answerable": false, "reason": "no toolbox function simulates that", "closest_function_or_none": null}'

    result = inv.investigate("what if I had used a tighter stop?", _bundle_full(), _refusing_llm)
    assert calls["n"] == 1  # no retry on an honest refusal
    assert result["status"] == "refused"
    assert result["answered"] is False


def test_investigate_happy_path_ok():
    def _llm(system, user, max_tokens):
        if "Facts (JSON)" in system:
            return "Your closed lots show a mixed record. Caveats: small sample."
        return '{"answerable": true, "steps": [{"fn_id": "closed_lots", "why": "holding periods"}]}'

    result = inv.investigate("how long do I typically hold a winner?", _bundle_full(), _llm)
    assert result["answered"] is True
    assert result["status"] == "ok"
    assert result["report_text"]
    assert result["trace"] == ["closed_lots"]
    assert "closed_lots" in result["facts"]


def test_investigate_report_call_failure_is_visible_not_silent():
    def _llm(system, user, max_tokens):
        if "Facts (JSON)" in system:
            return None  # report call fails
        return '{"answerable": true, "steps": [{"fn_id": "ticker_sectors", "why": "x"}]}'

    result = inv.investigate("what sectors am I in?", _bundle_full(), _llm)
    assert result["answered"] is False
    assert result["status"] == "report_failed"
    assert inv.LAST_REPORT_ERROR is not None


def test_investigate_infeasible_plan_retries_once_then_gives_up():
    """An infeasible plan (unsatisfiable needs) gets exactly one re-plan
    attempt; if the retry is ALSO infeasible, it stops rather than looping."""
    calls = {"n": 0}

    def _always_infeasible_llm(system, user, max_tokens):
        calls["n"] += 1
        # rec_outcomes needs recs_df/trades_df/current_prices/spy_close_by_date
        return '{"answerable": true, "steps": [{"fn_id": "rec_outcomes", "why": "x"}]}'

    result = inv.investigate("how did my acted recs do?", {"trades_df": pd.DataFrame()}, _always_infeasible_llm)
    assert calls["n"] == 2
    assert result["answered"] is False
    assert result["status"] == "no_plan"


# ─── TOOLBOX shape sanity ────────────────────────────────────────────────────

def test_every_toolbox_needs_entry_is_a_subset_of_the_fixed_vocabulary():
    for fn_id, entry in inv.TOOLBOX.items():
        assert entry["needs"] <= inv.AVAILABLE_INPUT_VOCAB, fn_id


def test_every_toolbox_entry_has_a_summary_and_cannot_list():
    for fn_id, entry in inv.TOOLBOX.items():
        assert entry["summary"], fn_id
        assert isinstance(entry["cannot"], list) and entry["cannot"], fn_id


def test_build_plan_prompt_enumerates_every_toolbox_id():
    prompt = inv.build_plan_prompt("how did I do?")
    for fn_id in inv.TOOLBOX:
        assert fn_id in prompt


# ─── EVAL_PASSED_MODELS cross-module consistency ─────────────────────────────

def test_every_eval_passed_model_exists_in_ai_provider_registry():
    from stock_analyzer import ai_provider
    for provider, model in inv.EVAL_PASSED_MODELS:
        assert provider in ai_provider.AI_PROVIDERS, provider
        assert model in ai_provider.AI_PROVIDERS[provider]["models"], (provider, model)


def test_build_plan_prompt_history_carries_question_text_only_not_answers():
    prompt = inv.build_plan_prompt("and last week?", history_questions=["how did I do this month?"])
    assert "how did I do this month?" in prompt


# ─── classify_buys adapter — degrade path ────────────────────────────────────
# classify_buys/self_vs_engine_summary's own logic is already covered in
# tests/test_self_track_record.py — not duplicated here. This only checks the
# adapter's own defensive contract: a failed (None) recs_df must degrade to a
# visible {"error": ...} fact, never a crash, when called directly (bypassing
# verify_fetch, which would normally have already caught this upstream).

def test_adapt_classify_buys_degrades_to_error_when_recs_df_is_none():
    result = inv.TOOLBOX["classify_buys"]["adapter"]({
        "trades_df": pd.DataFrame([{"action": "BUY", "ticker": "AAPL",
                                     "traded_at": "2026-08-10", "shares": 1, "price": 100}]),
        "recs_df": None,
        "universe_set": {"AAPL"},
        "watchlist_set": set(),
        "current_prices": {"AAPL": 110},
        "spy_close_by_date": {},
    })
    assert "error" in result
    assert "self_vs_engine_buys" not in result
