"""P2-c: the pure decisions behind App Settings' "🤖 AI models" section
(stock_analyzer/ai_models_settings.py)."""
import inspect
import types

import pytest

from stock_analyzer import ai_models_settings as ams
from stock_analyzer import ai_models_view, analyst_intel, llm, llm_settings
from stock_analyzer import premarket_stance, thesis_advisor
from stock_analyzer.constants import (
    ANALYST_EXTRACT_MAX_TOKENS,
    ANALYST_EXTRACT_TIMEOUT_SEC,
    LLM_REQUEST_TIMEOUT_SEC,
)

CAPS = {"efforts": ["low", "medium", "high"], "thinking_types": ["adaptive"],
        "structured_outputs": True, "max_tokens": 64000, "max_input_tokens": 200000}
CAPS_LOW_HIGH = {"efforts": ["low", "high"], "thinking_types": [],
                 "structured_outputs": False, "max_tokens": 8000,
                 "max_input_tokens": 100000}

_GOOD_REPLY = {
    "fast": "Futures are lower into CPI.\nStance: Defensive at open",
    "deep": "The thesis is under pressure.\nVerdict: WEAKENING",
    "extract": '{"article_date":"2026-10-12","reports":[{"ticker":"NWND"}]}',
}


def _entry(model="claude-haiku-5-5", effort="low", caps=CAPS):
    return {"model": model, "effort": effort, "capabilities": caps,
            "checked_at": "2026-10-26T10:00:00"}


class _Result:
    def __init__(self, text="ok", **kw):
        self.text = text
        self.error = kw.get("error")
        self.stop_reason = kw.get("stop_reason", "end_turn")
        self.truncated = kw.get("truncated", False)
        self.model = kw.get("model", "claude-haiku-5-5")
        self.requested_model = kw.get("requested_model", "claude-haiku-5-5")
        self.fell_back = kw.get("fell_back", False)
        self.model_source = kw.get("model_source", "setting")
        self.usage = kw.get("usage", {"input_tokens": 1000, "output_tokens": 200})

    @property
    def ok(self):
        return self.text is not None


def _fake(result=None, *, raises=None):
    calls = []

    def complete(role, **kw):
        calls.append({"role": role, **kw})
        if raises:
            raise raises
        return result if result is not None else _Result(_GOOD_REPLY[role])
    complete.calls = calls
    return complete


class _Clock:
    def __init__(self, *ticks):
        self.ticks = list(ticks)

    def __call__(self):
        return self.ticks.pop(0)


# ---- TEST_CASES mirror production ----
def test_one_case_per_editable_role():
    assert set(ams.TEST_CASES) == set(llm.EDITABLE_ROLES)


def test_case_max_tokens_and_timeouts_match_production():
    fast = inspect.signature(premarket_stance.generate_stance).parameters
    deep = inspect.signature(thesis_advisor.review_thesis).parameters
    ext = inspect.signature(analyst_intel.extract_report).parameters
    assert ams.TEST_CASES["fast"]["max_tokens"] == fast["max_tokens"].default
    assert ams.TEST_CASES["deep"]["max_tokens"] == deep["max_tokens"].default
    assert ams.TEST_CASES["extract"]["max_tokens"] == ext["max_tokens"].default
    assert ams.TEST_CASES["extract"]["max_tokens"] == ANALYST_EXTRACT_MAX_TOKENS
    # the production stance passes no timeout: the test uses the common budget
    assert "timeout" not in fast
    assert ams.TEST_CASES["fast"]["timeout"] == LLM_REQUEST_TIMEOUT_SEC
    assert ams.TEST_CASES["deep"]["timeout"] == LLM_REQUEST_TIMEOUT_SEC
    assert ams.TEST_CASES["extract"]["timeout"] == ANALYST_EXTRACT_TIMEOUT_SEC


def test_thesis_review_defaults_use_the_constant_and_draft_stays_300():
    from stock_analyzer.constants import THESIS_REVIEW_MAX_TOKENS
    for fn in (thesis_advisor.review_thesis, thesis_advisor.generate_earnings_thesis_update):
        assert inspect.signature(fn).parameters["max_tokens"].default == THESIS_REVIEW_MAX_TOKENS
    assert THESIS_REVIEW_MAX_TOKENS == 1000
    assert inspect.signature(thesis_advisor.draft_thesis).parameters["max_tokens"].default == 300


def test_case_expects_json_matches_the_production_call_sites():
    assert ams.TEST_CASES["fast"]["expects_json"] is True
    assert ams.TEST_CASES["deep"]["expects_json"] is True
    assert ams.TEST_CASES["extract"]["expects_json"] is None  # the role is JSON itself


def test_case_prompts_are_the_site_prompts():
    sys_f, user_f = ams.TEST_CASES["fast"]["build"]()
    assert sys_f == premarket_stance._SYSTEM_PROMPT
    assert user_f == premarket_stance.format_user_prompt(ams._SAMPLE_STANCE_INPUTS)
    assert "US futures:" in user_f and "Top portfolio holdings" in user_f
    sys_d, user_d = ams.TEST_CASES["deep"]["build"]()
    assert sys_d == thesis_advisor._SYSTEM_PROMPT
    assert user_d.startswith("Ticker: SAMPLE") and "Current evidence:" in user_d
    sys_e, user_e = ams.TEST_CASES["extract"]["build"]()
    assert sys_e == analyst_intel._system_prompt()
    assert 4000 <= len(user_e) <= 8000 and "SAMPLE ARTICLE" in user_e


def test_sites_are_labelled():
    assert ams.TEST_CASES["fast"]["site"] == "premarket_stance.generate_stance"
    assert ams.TEST_CASES["deep"]["site"] == "thesis_advisor.review_thesis"
    assert ams.TEST_CASES["extract"]["site"] == "analyst_intel.extract_report"


# ---- format checks ----
def test_stance_format_check():
    f = ams.TEST_CASES["fast"]["format_check"]
    assert f("Prose.\nStance: Neutral at open") is True
    assert f("Prose only, cut off mid sen") is False
    assert f("") is False and f(None) is False


def test_thesis_format_check_reads_the_verdict_line():
    f = ams.TEST_CASES["deep"]["format_check"]
    assert f("Text.\nVerdict: INTACT") is True
    assert f("Text.\nverdict: broken") is True
    assert f("Text with no verdict line") is False
    assert f("Verdict: maybe") is False
    # the LAST verdict line is the one the parser reads
    assert f("Verdict: INTACT\nmore\nVerdict: unclear") is False


def test_extract_format_check_uses_analyst_intels_parser():
    f = ams.TEST_CASES["extract"]["format_check"]
    assert f(_GOOD_REPLY["extract"]) is True
    assert f('```json\n{"reports": []}\n```') is True      # fenced, empty list
    assert f("[{\"ticker\": \"X\"}]") is True
    assert f("I could not read the article") is False
    assert f('{"reports": [{"ticker": "X"') is False        # cut off


def test_parse_extract_text_matches_extract_report(monkeypatch):
    text = '{"article_date": "2026-10-12", "reports": [{"ticker": "X"}, 3]}'
    monkeypatch.setattr(llm, "complete", lambda *a, **k: _Result(text))
    via_report = analyst_intel.extract_report("raw", "key")
    assert via_report == analyst_intel.parse_extract_text(text)
    assert via_report == [{"ticker": "X", "article_date": "2026-10-12"}]


# ---- candidate_key ----
def test_candidate_key_is_hashable_and_capability_sensitive():
    k = ams.candidate_key(_entry())
    assert hash(k) == hash(ams.candidate_key(_entry()))
    assert k != ams.candidate_key(_entry(effort="high"))
    assert k != ams.candidate_key(_entry(model="claude-sonnet-5-5"))
    assert k != ams.candidate_key(_entry(caps=CAPS_LOW_HIGH))
    assert k != ams.candidate_key(_entry(caps=None))


def test_candidate_key_ignores_capability_key_order_and_checked_at():
    a = _entry(caps={"efforts": ["low"], "max_tokens": 1})
    b = {**_entry(caps={"max_tokens": 1, "efforts": ["low"]}),
         "checked_at": "2030-01-01T00:00:00"}
    assert ams.candidate_key(a) == ams.candidate_key(b)


# ---- run_test ----
@pytest.mark.parametrize("role", ["fast", "deep", "extract"])
def test_run_test_sends_the_production_shape(role):
    case = ams.TEST_CASES[role]
    fake = _fake()
    entry = _entry()
    out = ams.run_test(role, entry, "key", complete=fake, clock=_Clock(10.0, 11.5))
    (call,) = fake.calls
    assert call["role"] == role and call["api_key"] == "key"
    assert call.get("model") is None                      # never an explicit model
    assert set(call["setting"]) == {role}
    rs = call["setting"][role]
    assert isinstance(rs, llm.RoleSetting)
    assert (rs.model, rs.effort) == ("claude-haiku-5-5", "low")
    assert dict(rs.capabilities) == CAPS
    assert call["max_tokens"] == case["max_tokens"]
    assert call["timeout"] == case["timeout"]
    assert call["expects_json"] == case["expects_json"]
    system, user = case["build"]()
    assert call["system"] == system
    assert call["messages"] == [{"role": "user", "content": user}]
    assert set(call) == {"role", "api_key", "messages", "system", "max_tokens",
                         "expects_json", "timeout", "setting"}
    assert out["ok"] is True and out["error"] is None
    assert out["format_ok"] is True
    assert out["latency_s"] == 1.5 and out["timeout_s"] == case["timeout"]
    assert out["key"] == ams.candidate_key(entry) and out["site"] == case["site"]


def test_run_test_without_capabilities_passes_none():
    fake = _fake()
    ams.run_test("fast", _entry(caps=None), "key", complete=fake)
    assert fake.calls[0]["setting"]["fast"].capabilities is None


def test_run_test_reports_the_result_fields():
    res = _Result(_GOOD_REPLY["fast"], model="claude-haiku-5-5-20260101",
                  requested_model="claude-haiku-5-5", fell_back=True,
                  stop_reason="end_turn", truncated=False)
    out = ams.run_test("fast", _entry(), "key", complete=_fake(res))
    assert out["served_model"] == "claude-haiku-5-5-20260101"
    assert out["requested_model"] == "claude-haiku-5-5"
    assert out["fell_back"] is True and out["model_source"] == "setting"
    assert out["stop_reason"] == "end_turn" and out["truncated"] is False
    assert (out["input_tokens"], out["output_tokens"]) == (1000, 200)
    assert out["cost_usd"] == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1e6)
    assert out["preview"] == _GOOD_REPLY["fast"]


def test_run_test_cost_is_none_for_an_unpriced_model():
    res = _Result("Stance: Neutral at open", model="claude-mystery-9")
    out = ams.run_test("fast", _entry(), "key", complete=_fake(res))
    assert out["cost_usd"] is None


def test_run_test_preview_is_cut_to_400_chars():
    res = _Result("x" * 1000 + "\nStance: Neutral at open")
    out = ams.run_test("fast", _entry(), "key", complete=_fake(res))
    assert len(out["preview"]) == 400


def test_run_test_failed_reply_has_no_format_verdict():
    res = _Result(None, error="reply cut off at max_tokens", stop_reason="max_tokens",
                  truncated=True, usage=None)
    out = ams.run_test("extract", _entry(), "key", complete=_fake(res))
    assert out["ok"] is False and out["error"] == "reply cut off at max_tokens"
    assert out["format_ok"] is None and out["preview"] == ""
    assert out["truncated"] is True and out["cost_usd"] is None


def test_run_test_flags_a_reply_without_the_site_format():
    out = ams.run_test("deep", _entry(), "key",
                       complete=_fake(_Result("No verdict here at all")))
    assert out["ok"] is True and out["format_ok"] is False


def test_run_test_never_raises():
    out = ams.run_test("fast", _entry(), "key",
                       complete=_fake(raises=RuntimeError("boom")),
                       clock=_Clock(1.0, 2.0))
    assert out["ok"] is False and "RuntimeError: boom" in out["error"]
    assert out["format_ok"] is None and out["latency_s"] == 1.0


def test_run_test_unknown_role_and_bad_entry_do_not_raise():
    assert ams.run_test("score", _entry(), "key", complete=_fake())["ok"] is False
    out = ams.run_test("fast", {"effort": "low"}, "key", complete=_fake())
    assert out["ok"] is False and out["error"]


def test_run_test_against_the_real_complete_with_no_key():
    out = ams.run_test("fast", _entry(), "")
    assert out["ok"] is False and out["error"] == "no API key"
    assert out["model_source"] == llm.SOURCE_SETTING


# ---- can_save ----
def _passed(entry, **over):
    t = {"key": ams.candidate_key(entry), "ok": True, "error": None,
         "model_source": "setting", "format_ok": True,
         "site": "premarket_stance.generate_stance", "served_model": entry["model"]}
    t.update(over)
    return t


def test_can_save_allowed_after_a_passing_test():
    e = _entry()
    assert ams.can_save(e, _passed(e)) == (True, "")


def test_can_save_allowed_when_the_format_check_cannot_tell():
    e = _entry()
    assert ams.can_save(e, _passed(e, format_ok=None))[0] is True


def test_can_save_needs_a_test():
    ok, why = ams.can_save(_entry(), None)
    assert ok is False and why == "run Test role first"


def test_can_save_needs_a_candidate():
    assert ams.can_save(None, None)[0] is False


def test_can_save_rejects_a_test_of_another_candidate():
    e = _entry()
    for other in (_entry(effort="high"), _entry(model="claude-sonnet-5-5"),
                  _entry(caps=CAPS_LOW_HIGH)):
        ok, why = ams.can_save(e, _passed(other))
        assert ok is False
        assert why == "the last test was for a different model or effort"


def test_can_save_rejects_a_failed_test_with_its_error():
    e = _entry()
    ok, why = ams.can_save(e, _passed(e, ok=False, error="reply cut off at max_tokens"))
    assert ok is False and why == "the last test failed: reply cut off at max_tokens"


def test_can_save_rejects_a_test_that_did_not_run_on_the_setting():
    e = _entry()
    ok, why = ams.can_save(e, _passed(e, model_source="env",
                                      served_model="claude-opus-4-8"))
    assert ok is False and "claude-opus-4-8" in why and "env" in why


def test_can_save_rejects_a_reply_without_the_site_format():
    e = _entry()
    ok, why = ams.can_save(e, _passed(e, format_ok=False, site="thesis_advisor.review_thesis"))
    assert ok is False
    assert why == "the reply didn't have the format thesis_advisor.review_thesis parses"


# ---- effort_options / effort_label ----
def test_effort_options_known_modern_with_full_caps():
    assert ams.effort_options("claude-haiku-5-5", CAPS) == [None, "low", "medium", "high"]


def test_effort_options_known_modern_without_caps_offers_every_allowed_level():
    assert ams.effort_options("claude-haiku-5-5", None) == [None, "low", "medium", "high"]


def test_effort_options_generic_limited_to_listed_levels():
    assert ams.effort_options("claude-future-9", CAPS_LOW_HIGH) == [None, "low", "high"]


def test_effort_options_never_offers_a_level_above_the_cap():
    caps = {**CAPS, "efforts": ["low", "medium", "high", "xhigh", "max"]}
    assert ams.effort_options("claude-sonnet-5-5", caps) == [None, "low", "medium", "high"]


@pytest.mark.parametrize("model", ["claude-haiku-4-5", "claude-opus-4-8",
                                   "claude-sonnet-4-6", "claude-opus-5"])
def test_effort_options_legacy_and_opus_4x_take_none(model):
    assert ams.effort_options(model, CAPS) == [None]


def test_effort_options_every_option_passes_validation():
    for model, caps in (("claude-haiku-5-5", CAPS), ("claude-future-9", CAPS_LOW_HIGH),
                        ("claude-haiku-4-5", CAPS), ("claude-opus-4-8", CAPS)):
        for eff in ams.effort_options(model, caps):
            e = {"model": model, "effort": eff, "capabilities": caps, "checked_at": None}
            assert llm_settings.validate_role_entry("fast", e) == []


def test_effort_label_per_profile():
    assert ams.effort_label("claude-haiku-5-5", None) == "default (low)"
    assert ams.effort_label("claude-future-9", None) == "model default (not sent)"
    assert ams.effort_label("claude-haiku-4-5", None) == (
        "none (this model takes no effort setting)")
    assert ams.effort_label("claude-opus-4-8", None) == (
        "none (this model takes no effort setting)")
    assert ams.effort_label("claude-haiku-5-5", "medium") == "medium"


def test_effort_label_generic_says_low_only_when_the_capabilities_list_it():
    assert ams.effort_label("claude-future-9", None, CAPS_LOW_HIGH) == "default (low)"
    assert ams.effort_label("claude-future-9", None,
                            {**CAPS_LOW_HIGH, "efforts": ["high"]}) == (
        "model default (not sent)")
    assert ams.effort_label("claude-future-9", None, None) == "model default (not sent)"


def _sent_effort(model, caps):
    req = llm.build_request("fast", model, max_tokens=100,
                            messages=[{"role": "user", "content": "x"}],
                            effort=None, capabilities=caps)
    return (req.get("output_config") or {}).get("effort")


def test_default_labels_match_what_build_request_sends():
    assert _sent_effort("claude-haiku-5-5", CAPS) == "low"
    assert ams.effort_label("claude-haiku-5-5", None, CAPS) == "default (low)"
    assert _sent_effort("claude-future-9", CAPS_LOW_HIGH) == "low"
    assert ams.effort_label("claude-future-9", None, CAPS_LOW_HIGH) == "default (low)"
    no_low = {**CAPS_LOW_HIGH, "efforts": ["high"]}
    assert _sent_effort("claude-future-9", no_low) is None
    assert ams.effort_label("claude-future-9", None, no_low) == "model default (not sent)"
    for m in ("claude-haiku-4-5", "claude-opus-4-8"):
        assert _sent_effort(m, CAPS) is None


# ---- model_choices ----
def test_model_choices_filters_sorts_and_puts_current_first_when_absent():
    ids = ["claude-sonnet-5-5", "gpt-x", "claude-haiku-5-5", None, "claude-haiku-5-5"]
    assert ams.model_choices(ids, "claude-haiku-5-5") == [
        "claude-haiku-5-5", "claude-sonnet-5-5"]
    assert ams.model_choices(ids, "claude-opus-4-8") == [
        "claude-opus-4-8", "claude-haiku-5-5", "claude-sonnet-5-5"]


def test_model_choices_with_no_list_is_just_the_current_model():
    assert ams.model_choices(None, "claude-haiku-4-5") == ["claude-haiku-4-5"]
    assert ams.model_choices([], "claude-haiku-4-5") == ["claude-haiku-4-5"]


# ---- edit_block_reason ----
def _snap(setting=None):
    return llm_settings.Snapshot(llm_settings.STATE_OK, setting or {}, (), None, None)


def test_edit_block_reason_env_source_uses_the_row_note():
    env = {"DRISHTA_LLM_MODEL_FAST": "claude-opus-4-8"}
    rows = ai_models_view.build_rows(_snap(), env=env)
    row = next(r for r in rows if r["role"] == "fast")
    why = ams.edit_block_reason(row)
    assert row["notes"][0] in why
    assert "won't take effect while it is set" in why
    assert "Test role are disabled" in why


def test_edit_block_reason_env_notes_mention_the_saved_setting():
    env = {"DRISHTA_LLM_MODEL_FAST": "claude-opus-4-8"}
    setting = {"fast": llm.RoleSetting("claude-haiku-5-5", "low", None)}
    row = next(r for r in ai_models_view.build_rows(_snap(setting), env=env)
               if r["role"] == "fast")
    assert "claude-haiku-5-5 saved" in ams.edit_block_reason(row)


def test_edit_block_reason_locked_role_gives_the_lock_reason():
    rows = ai_models_view.build_rows(_snap(), env={})
    for role in ("score", "filter", "extract_trigger", "extract_strong"):
        row = next(r for r in rows if r["role"] == role)
        assert ams.edit_block_reason(row) == row["lock_reason"]
        assert row["lock_reason"]


def test_edit_block_reason_none_for_an_editable_role():
    rows = ai_models_view.build_rows(_snap(), env={})
    for role in ("fast", "deep", "extract"):
        assert ams.edit_block_reason(next(r for r in rows if r["role"] == role)) is None


def test_edit_block_reason_ignored_env_value_does_not_block():
    rows = ai_models_view.build_rows(_snap(), env={"DRISHTA_LLM_MODEL_FAST": "gpt-x"})
    row = next(r for r in rows if r["role"] == "fast")
    assert ams.edit_block_reason(row) is None


# ---- history_lines ----
def _p(**roles):
    return {"version": 1, "roles": roles}


def test_history_first_row_is_an_initial_capture():
    lines = ams.history_lines(_p(fast=_entry()), None)
    assert lines == ["Initial capture: fast: claude-haiku-5-5 (effort low)"]
    assert ams.history_lines(_p(), None)[0].startswith("Initial capture:")


def test_history_set_change_and_reset():
    old = _p(deep=_entry("claude-sonnet-5-5", None))
    new = _p(fast=_entry())
    assert ams.history_lines(new, old) == [
        "deep: claude-sonnet-5-5 → code default (reset)",
        "fast: code default → claude-haiku-5-5 (effort low)",
    ]


def test_history_model_to_model_with_effort():
    old = _p(fast=_entry("claude-haiku-5-5", "low"))
    new = _p(fast=_entry("claude-sonnet-5-5", "high"))
    assert ams.history_lines(new, old) == [
        "fast: claude-haiku-5-5 (effort low) → claude-sonnet-5-5 (effort high)"]


def test_history_ignores_checked_at():
    old = _p(fast=_entry())
    new = _p(fast={**_entry(), "checked_at": "2030-01-01T00:00:00"})
    assert ams.history_lines(new, old) == ["No role changes"]


def test_history_capability_only_change_is_reported_as_such():
    old = _p(fast=_entry(caps=CAPS))
    new = _p(fast=_entry(caps=CAPS_LOW_HIGH))
    assert ams.history_lines(new, old) == [
        "fast: claude-haiku-5-5 (effort low), stored capabilities updated"]


def test_history_tolerates_odd_payloads():
    assert ams.history_lines({}, {"roles": "x"}) == ["No role changes"]
    assert ams.history_lines(None, {}) == ["No role changes"]


# ---- save_outcome_message ----
def test_save_outcome_saved():
    level, text = ams.save_outcome_message({"status": "saved", "as_of": "2026-10-26"},
                                           llm_settings.STATE_OK)
    assert level == "success"
    assert "next AI call" in text and "cron lane" in text and "next run" in text


def test_save_outcome_no_change_on_a_valid_row():
    level, text = ams.save_outcome_message({"status": "no_change"},
                                           llm_settings.STATE_OK)
    assert level == "info"
    assert text == "Nothing changed: this role already has that model and effort."


@pytest.mark.parametrize("state", [llm_settings.STATE_NO_ROW,
                                   llm_settings.STATE_UNREADABLE])
def test_save_outcome_no_change_other_states_is_info(state):
    assert ams.save_outcome_message({"status": "no_change"}, state)[0] == "info"


def test_save_outcome_no_change_on_an_invalid_row_warns():
    level, text = ams.save_outcome_message({"status": "no_change"},
                                           llm_settings.STATE_INVALID)
    assert level == "warning"
    assert "still invalid" in text and "ignored" in text
    assert "hash" in text and "Reset" in text


def test_save_outcome_error_shows_the_detail():
    level, text = ams.save_outcome_message(
        {"status": "error", "detail": "invalid payload: x"}, llm_settings.STATE_OK)
    assert level == "error" and "invalid payload: x" in text


def test_save_outcome_error_without_detail_or_dict():
    assert ams.save_outcome_message({"status": "error"}, "ok")[0] == "error"
    assert ams.save_outcome_message(None, "ok")[0] == "error"


# ---- module hygiene ----
def test_module_has_no_streamlit_import():
    src = inspect.getsource(ams)
    assert "import streamlit" not in src


def test_test_cases_are_immutable():
    assert isinstance(ams.TEST_CASES, types.MappingProxyType)


# ---- review fixes (2026-10-26) ----
def test_can_save_rejects_a_fallback_answer():
    res = _Result(_GOOD_REPLY["fast"], model="claude-opus-5-5",
                  requested_model="claude-sonnet-5-5", fell_back=True)
    e = _entry("claude-sonnet-5-5")
    out = ams.run_test("fast", e, "key", complete=_fake(res))
    assert out["ok"] is True and out["fell_back"] is True
    ok, why = ams.can_save(e, out)
    assert ok is False
    assert why == ("the candidate declined the sample and claude-opus-5-5 answered "
                   "instead; the test didn't exercise claude-sonnet-5-5")


def test_stance_check_requires_a_recognised_word_on_the_last_stance_line():
    f = ams.TEST_CASES["fast"]["format_check"]
    assert f("x\nStance: Constructive at open") is True
    assert f("x\nStance: Defensive at open") is True
    assert f("x\nStance: cautious") is False
    assert f("Stance: Defensive\nmore\nStance: cautious") is False
    assert f("Stance:") is False


def test_stance_check_agrees_with_parse_stance_on_recognised_words():
    f = ams.TEST_CASES["fast"]["format_check"]
    for word, expect in (("Defensive", "defensive"), ("Constructive", "constructive"),
                         ("Neutral", "neutral")):
        text = f"Prose.\nStance: {word} at open"
        assert f(text) is True
        assert premarket_stance.parse_stance(text)["stance"] == expect
    # parse_stance matches "Stance:" / "stance:" only, so an upper-case line is
    # never read and production shows Neutral: the check must fail it.
    text = "Prose.\nSTANCE: Defensive at open"
    assert f(text) is False
    assert premarket_stance.parse_stance(text)["stance"] == "neutral"


def test_site_timeout_flag_only_false_for_the_stance():
    assert [r for r, c in ams.TEST_CASES.items() if not c["site_timeout"]] == ["fast"]


def test_save_outcome_reset_no_change():
    level, text = ams.save_outcome_message({"status": "no_change"},
                                           llm_settings.STATE_OK, action="reset")
    assert level == "info"
    assert text == "Nothing to reset: this role has no saved App Settings choice."


def test_format_checked_at():
    assert ams.format_checked_at("2026-10-26T14:05:33-04:00") == "14:05 ET"
    assert ams.format_checked_at(None) == "an unknown time"
    assert ams.format_checked_at("junk") == "an unknown time"
