"""Chunk C5: the eval-gated decision roles in App Settings' "AI models" section
(stock_analyzer/ai_models_settings.py, docs/plans/llm-model-routing.md 17)."""
import inspect
import math
from datetime import datetime, timedelta, timezone

import pytest

from stock_analyzer import ai_models_settings as ams
from stock_analyzer import (llm, llm_decision_settings as lds, llm_role_eval,
                            news_intelligence, premortem_monitor, reference_data)
from stock_analyzer.constants import (
    LLM_EVAL_CRITERIA_VERSION,
    LLM_EVAL_MAX_AGE_DAYS,
    LLM_EVAL_SCORE_MAX_CUMULATIVE_SHIFT_PTS as CUM_LIMIT,
)

CAPS = {"efforts": ["low", "medium", "high"], "thinking_types": ["adaptive"],
        "structured_outputs": True, "max_tokens": 64000, "max_input_tokens": 200000}
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
ROLES = sorted(llm.DECISION_ROLES)

_SCORE_REPLY = ('[{"idx":0,"score":0.4},{"idx":1,"score":-0.3},{"idx":2,"score":0.2},'
                '{"idx":3,"score":0.0},{"idx":4,"score":-0.1}]')
_FILTER_REPLY = "[" + ",".join(f'{{"idx":{i},"score":0.1}}' for i in range(25)) + "]"
_TRIGGER_REPLY = '{"checkable": true, "direction": "below", "price_level": 42.5}'
_REPLY = {"score": _SCORE_REPLY, "filter": _FILTER_REPLY, "extract_trigger": _TRIGGER_REPLY}


class _Result:
    def __init__(self, text="ok", **kw):
        self.text = text
        self.error = kw.get("error")
        self.stop_reason = kw.get("stop_reason", "end_turn")
        self.truncated = False
        self.model = kw.get("model", "claude-haiku-5-5")
        self.requested_model = "claude-haiku-5-5"
        self.fell_back = kw.get("fell_back", False)
        self.model_source = kw.get("model_source", "setting")
        self.usage = {"input_tokens": 1000, "output_tokens": 200}

    @property
    def ok(self):
        return self.text is not None


def _fake(role, text="<default-reply>", raises=None, **kw):
    calls = []

    def complete(r, **k):
        calls.append({"role": r, **k})
        if raises:
            raise raises
        return _Result(_REPLY[role] if text == "<default-reply>" else text, **kw)
    complete.calls = calls
    return complete


def _entry(model="claude-haiku-5-5", effort="low", caps=CAPS):
    return {"model": model, "effort": effort, "capabilities": caps,
            "checked_at": "2026-10-26T10:00:00"}


def _test_ok(entry):
    return {"key": ams.candidate_key(entry), "ok": True, "fell_back": False,
            "model_source": "setting", "format_ok": True, "site": "x",
            "served_model": entry["model"], "error": None}


def _base_for(role, cur, today="2026-11-20"):
    """(model, effort) the role resolves to on ``today`` given its stored entry."""
    if cur is None:
        return llm.CODE_DEFAULTS[role], None
    if role == "score" and today < cur["effective_from"]:
        prior = cur.get("prior")
        return ((prior["model"], prior["effort"]) if prior
                else (llm.CODE_DEFAULTS[role], None))
    return cur["model"], cur["effort"]


def _eval(entry, role="score", *, age=timedelta(days=1), shift=0.2, passed=True,
          base=None, **over):
    base = base or (llm.CODE_DEFAULTS[role], None)
    base_keys = {"current_model": base[0], "current_effort": base[1]}
    row = {"id": 7, "role": role, "model": entry["model"], "effort": entry["effort"],
           "capabilities_hash": llm_role_eval.capabilities_hash(entry["capabilities"]),
           "criteria_version": LLM_EVAL_CRITERIA_VERSION, "passed": passed,
           "ran_at": (NOW - age).isoformat(), "sample_n": 300,
           "metrics": {"mean_shift_net": shift, **base_keys},
           "failures": [], "baseline": "b"}
    if "metrics" in over:
        over["metrics"] = {**base_keys, **over["metrics"]}
    row.update(over)
    return row


def _cur(role="score", model="claude-sonnet-5-5", effort=None, cum=0.0,
         effective_from="2026-11-10", prior=None):
    e = {"model": model, "effort": effort, "capabilities": CAPS,
         "checked_at": "2026-10-26T10:00:00", "eval_id": 3,
         "criteria_version": LLM_EVAL_CRITERIA_VERSION}
    if role == "score":
        e.update({"effective_from": effective_from, "prior": prior,
                  "cumulative_shift_pts": cum})
    return e


def _decide(role, entry, **kw):
    test = kw.pop("test", _test_ok(entry))
    cur = kw.pop("current", None)
    ev = kw.pop("eval_row", None) or _eval(entry, role, base=_base_for(role, cur))
    now = kw.pop("now", NOW)
    return ams.can_save_decision(role, entry, test, ev, cur, now, **kw)


def _snap(payload):
    norm = lds.canonicalize(payload)
    row = {"payload": norm,
           "payload_hash": reference_data.payload_hash_for(lds.TABLE_NAME, norm),
           "as_of": "2026-11-01", "updated_by": "t"}
    return lds.build_snapshot(lds.STATE_OK, row)


# ---- the three production-shaped test cases ----
def test_decision_cases_mirror_the_production_sites():
    sc, fl, tr = (ams.TEST_CASES[r] for r in ("score", "filter", "extract_trigger"))
    assert inspect.signature(news_intelligence.rescore_headlines_llm).parameters[
        "timeout"].default == ams._NEWS_SITE_TIMEOUT_SEC == sc["timeout"]
    assert inspect.signature(news_intelligence.llm_score_batch).parameters[
        "timeout"].default == fl["timeout"]
    assert inspect.signature(premortem_monitor.extract_trigger).parameters[
        "max_tokens"].default == tr["max_tokens"]
    assert [c["site_timeout"] for c in (sc, fl, tr)] == [True, True, True]


@pytest.mark.parametrize("role", ROLES)
def test_decision_run_test_exercises_production_through_the_setting(role):
    fake = _fake(role)
    e = _entry()
    out = ams.run_test(role, e, "key", complete=fake)
    (call,) = fake.calls
    assert call["role"] == role and call["api_key"] == "key"
    assert call.get("model") is None
    rs = call["setting"][role]
    assert isinstance(rs, llm.RoleSetting) and (rs.model, rs.effort) == ("claude-haiku-5-5", "low")
    assert dict(rs.capabilities) == CAPS
    assert set(call["setting"]) == {role}
    assert out["ok"] is True and out["format_ok"] is True
    assert out["model_source"] == "setting" and out["key"] == ams.candidate_key(e)
    # production's own modules are restored
    assert news_intelligence.llm is llm and premortem_monitor.llm is llm


def test_score_case_sends_the_production_request():
    fake = _fake("score")
    ams.run_test("score", _entry(), "key", complete=fake)
    (call,) = fake.calls
    assert call["max_tokens"] == ams.TEST_CASES["score"]["max_tokens"] == 512
    assert call["temperature"] == 0
    assert call["client_kwargs"]["max_retries"] == 0
    assert call["client_kwargs"]["timeout"].read == ams.TEST_CASES["score"]["timeout"]
    prompt = call["messages"][0]["content"]
    assert "Score each headline for SAMPLE investors" in prompt and "Headlines:\n0. " in prompt
    assert prompt.count('\n4. "') == 1 and '\n5. "' not in prompt


def test_filter_case_sends_a_25_item_batch_with_no_retry():
    fake = _fake("filter")
    ams.run_test("filter", _entry(), "key", complete=fake)
    (call,) = fake.calls
    assert call["max_tokens"] == ams.TEST_CASES["filter"]["max_tokens"] == 64 + 32 * 25
    assert call["client_kwargs"]["max_retries"] == 0
    assert call["client_kwargs"]["timeout"].read == ams.TEST_CASES["filter"]["timeout"]
    prompt = call["messages"][0]["content"]
    assert '\n24. ' in prompt and '\n25. ' not in prompt


def test_trigger_case_sends_the_production_system_prompt():
    fake = _fake("extract_trigger")
    ams.run_test("extract_trigger", _entry(), "key", complete=fake)
    (call,) = fake.calls
    assert call["system"] == premortem_monitor._EXTRACT_SYSTEM_PROMPT
    assert call["messages"] == [{"role": "user", "content": ams._SAMPLE_COMMITMENT}]
    assert call["max_tokens"] == ams.TEST_CASES["extract_trigger"]["max_tokens"]


@pytest.mark.parametrize("role,bad", [
    ("score", "I cannot score these"),
    ("score", '[{"idx":0,"score":0.4}]'),                 # incomplete array
    ("score", '[{"idx":0,"score":5}]'),                    # out of range
    ("filter", '[{"idx":0,"score":0.1}]'),
    ("filter", "not json"),
    ("extract_trigger", "no trigger here"),
    ("extract_trigger", '{"checkable": true, "direction": "sideways", "price_level": 1}'),
])
def test_decision_format_checks_refuse_bad_replies(role, bad):
    assert ams.TEST_CASES[role]["format_check"](bad) is False
    out = ams.run_test(role, _entry(), "key", complete=_fake(role, bad))
    assert out["ok"] is True and out["format_ok"] is False


@pytest.mark.parametrize("role", ROLES)
def test_decision_format_checks_accept_good_and_fenced_replies(role):
    f = ams.TEST_CASES[role]["format_check"]
    assert f(_REPLY[role]) is True
    assert f("```json\n" + _REPLY[role] + "\n```") is True
    assert f("") is False and f(None) is False


def test_trigger_not_checkable_is_a_good_reply():
    f = ams.TEST_CASES["extract_trigger"]["format_check"]
    assert f('{"checkable": false, "direction": null, "price_level": null}') is True


def test_decision_run_test_fell_back_and_failures_are_reported():
    out = ams.run_test("filter", _entry(), "key",
                       complete=_fake("filter", fell_back=True, model="claude-opus-5-5"))
    assert out["fell_back"] is True and out["served_model"] == "claude-opus-5-5"
    out = ams.run_test("score", _entry(), "key", complete=_fake("score", text=None))
    # a None reply: production returns None, so nothing is accepted
    assert out["ok"] is False and out["format_ok"] is None


def test_decision_run_test_reports_a_raising_complete_and_no_key():
    out = ams.run_test("score", _entry(), "key",
                       complete=_fake("score", raises=RuntimeError("boom")))
    assert out["ok"] is False and "RuntimeError: boom" in out["error"]
    assert news_intelligence.llm is llm
    out = ams.run_test("score", _entry(), "", complete=_fake("score"))
    assert out["ok"] is False and "no request" in out["error"]


# ---- can_save_decision ----
@pytest.mark.parametrize("role", ROLES)
def test_can_save_decision_allows_a_fully_gated_candidate(role):
    e = _entry()
    assert _decide(role, e) == (True, "")


def test_can_save_decision_rejects_a_non_decision_role():
    ok, why = ams.can_save_decision("fast", _entry(), _test_ok(_entry()), None, None, NOW)
    assert ok is False and "decision" in why


def test_requires_the_test_role_first():
    e = _entry()
    other = _entry(effort="high")
    for test in (None, _test_ok(other), {**_test_ok(e), "ok": False, "error": "x"},
                 {**_test_ok(e), "fell_back": True},
                 {**_test_ok(e), "model_source": "code"},
                 {**_test_ok(e), "format_ok": False}):
        ok, why = _decide("filter", e, test=test)
        assert ok is False and why


def test_no_eval_and_a_failed_eval_are_refused_with_the_command():
    e = _entry()
    ok, why = ams.can_save_decision("score", e, _test_ok(e), None, None, NOW)
    assert ok is False
    assert "python scripts/llm_role_eval.py --role score --model claude-haiku-5-5 --effort low" in why
    ok, why = _decide("filter", e, eval_row=_eval(e, "filter", passed=False))
    assert ok is False and "did not pass" in why


@pytest.mark.parametrize("field,value,fragment", [
    ("model", "claude-sonnet-5-5", "stored eval is for"),
    ("effort", "high", "stored eval used"),
    ("effort", None, "stored eval used"),
    ("capabilities_hash", "0" * 64, "different model capabilities"),
    ("criteria_version", LLM_EVAL_CRITERIA_VERSION + 1, "criteria version"),
])
def test_eval_must_match_model_effort_hash_and_criteria(field, value, fragment):
    e = _entry()
    ok, why = _decide("filter", e, eval_row=_eval(e, "filter", **{field: value}))
    assert ok is False and fragment in why


def test_eval_effort_none_equals_none():
    e = _entry(effort=None)
    assert _decide("filter", e, eval_row=_eval(e, "filter")) == (True, "")


def test_eval_age_limit_is_inclusive():
    e = _entry()
    at_limit = _eval(e, "filter", age=timedelta(days=LLM_EVAL_MAX_AGE_DAYS))
    assert _decide("filter", e, eval_row=at_limit) == (True, "")
    over = _eval(e, "filter", age=timedelta(days=LLM_EVAL_MAX_AGE_DAYS, seconds=1))
    ok, why = _decide("filter", e, eval_row=over)
    assert ok is False and f"{LLM_EVAL_MAX_AGE_DAYS}-day limit" in why


def test_eval_ran_at_z_suffix_naive_and_unreadable():
    e = _entry()
    z = (NOW - timedelta(days=1)).replace(tzinfo=None).isoformat() + "Z"
    assert _decide("filter", e, eval_row=_eval(e, "filter", ran_at=z)) == (True, "")
    ok, why = _decide("filter", e, eval_row=_eval(e, "filter", ran_at="junk"))
    assert ok is False and "run time" in why
    ok, _ = _decide("filter", e, eval_row=_eval(e, "filter", ran_at=None))
    assert ok is False


def test_cumulative_shift_limit_is_inclusive_for_score():
    e = _entry()
    cur = _cur(cum=0.4)
    shift = CUM_LIMIT - 0.4
    b = ("claude-sonnet-5-5", None)
    assert _decide("score", e, current=cur, eval_row=_eval(e, shift=shift, base=b)) == (True, "")
    ok, why = _decide("score", e, current=cur, eval_row=_eval(e, shift=shift + 0.001, base=b))
    assert ok is False and "cumulative" in why
    neg = _cur(cum=-0.4)
    assert _decide("score", e, current=neg, eval_row=_eval(e, shift=-shift, base=b)) == (True, "")
    assert _decide("score", e, current=neg,
                   eval_row=_eval(e, shift=-shift - 0.001, base=b))[0] is False


def test_score_needs_a_measured_shift_but_other_roles_do_not():
    e = _entry()
    for metrics in ({}, {"mean_shift_net": None}, {"mean_shift_net": float("nan")}):
        assert _decide("score", e, eval_row=_eval(e, metrics=metrics))[0] is False
    assert _decide("filter", e, eval_row=_eval(e, "filter", metrics={})) == (True, "")


@pytest.mark.parametrize("role", ROLES)
def test_candidate_already_in_effect_is_refused(role):
    saved = _entry(model="claude-sonnet-5-5", effort=None)
    ok, why = _decide(role, saved, current=_cur(role, "claude-sonnet-5-5", None))
    assert ok is False and "already" in why
    default = _entry(model=llm.CODE_DEFAULTS[role], effort=None)
    ok, why = _decide(role, default, current=None)
    assert ok is False and "code default" in why


def test_same_model_different_effort_is_not_in_effect():
    e = _entry(effort="high")
    assert _decide("filter", e, current=_cur("filter", "claude-haiku-5-5", "low")) == (True, "")


# ---- build_decision_entry ----
@pytest.mark.parametrize("role", ROLES)
def test_built_entry_validates_for_every_role(role):
    e = _entry()
    entry = ams.build_decision_entry(role, e, _eval(e, role), None, NOW)
    assert lds.validate_role_entry(role, entry) == []
    assert lds.validate_payload(lds.apply_role({"version": 1, "roles": {}}, role, entry)) == []
    assert entry["eval_id"] == 7 and entry["criteria_version"] == LLM_EVAL_CRITERIA_VERSION
    assert (entry["model"], entry["effort"]) == ("claude-haiku-5-5", "low")
    assert entry["capabilities"] == CAPS and entry["checked_at"] == e["checked_at"]
    assert ("effective_from" in entry) == (role == "score")


def test_non_score_entry_has_exactly_the_common_keys():
    e = _entry()
    assert set(ams.build_decision_entry("filter", e, _eval(e, "filter"), None, NOW)) == {
        "model", "effort", "capabilities", "checked_at", "eval_id", "criteria_version"}


def test_score_entry_effective_from_and_cumulative():
    e = _entry()
    entry = ams.build_decision_entry("score", e, _eval(e, shift=0.2),
                                     _cur(cum=0.1234567), NOW)
    assert entry["effective_from"] == "2026-11-21"
    assert entry["cumulative_shift_pts"] == round(0.1234567 + 0.2, 4)
    near = datetime(2026, 11, 20, 23, 57, tzinfo=timezone.utc)   # within 300 s of 00:00 UTC
    assert ams.build_decision_entry("score", e, _eval(e), None, near)["effective_from"] == "2026-11-22"


def test_score_entry_cumulative_never_negative_zero():
    e = _entry()
    entry = ams.build_decision_entry("score", e, _eval(e, shift=-0.0), None, NOW)
    assert entry["cumulative_shift_pts"] == 0.0
    assert math.copysign(1.0, entry["cumulative_shift_pts"]) == 1.0


def test_score_prior_is_what_resolves_today_not_a_pending_entry():
    e = _entry()
    assert ams.build_decision_entry("score", e, _eval(e), None, NOW)["prior"] is None
    effective = _cur(effective_from="2026-11-10")            # in effect on 11-20
    prior = ams.build_decision_entry("score", e, _eval(e), effective, NOW)["prior"]
    assert prior == {"model": "claude-sonnet-5-5", "effort": None, "capabilities": CAPS}
    old = {"model": "claude-haiku-5-5", "effort": "low", "capabilities": CAPS}
    pending = _cur(effective_from="2026-11-25", prior=old)   # not yet effective
    assert ams.build_decision_entry("score", e, _eval(e), pending, NOW)["prior"] == old
    pending_noprior = _cur(effective_from="2026-11-25", prior=None)
    assert ams.build_decision_entry("score", e, _eval(e), pending_noprior, NOW)["prior"] is None


def test_score_entry_without_a_measured_shift_raises():
    e = _entry()
    with pytest.raises(ValueError):
        ams.build_decision_entry("score", e, _eval(e, metrics={}), None, NOW)


# ---- decide_decision_candidate ----
def test_decide_decision_candidate_needs_a_successful_check():
    entry, errs = ams.decide_decision_candidate(
        "filter", "claude-haiku-5-5", "low", CAPS, None, "2026-11-01T10:00:00")
    assert errs == [] and entry["model"] == "claude-haiku-5-5"
    entry, errs = ams.decide_decision_candidate(
        "filter", "claude-haiku-5-5", "low", None, "net down", "2026-11-01T10:00:00",
        error_kind=llm.ERR_UNREACHABLE)
    assert entry is None and errs
    entry, errs = ams.decide_decision_candidate(
        "fast", "claude-haiku-5-5", "low", CAPS, None, "2026-11-01T10:00:00")
    assert entry is None and errs
    entry, errs = ams.decide_decision_candidate(
        "score", "claude-haiku-5-5", "xhigh", CAPS, None, "2026-11-01T10:00:00")
    assert entry is None and errs


# ---- labels, command, summary ----
def test_eval_command():
    assert ams.eval_command("score", "claude-haiku-5-5", "low") == (
        "python scripts/llm_role_eval.py --role score --model claude-haiku-5-5 --effort low")
    assert ams.eval_command("filter", "claude-haiku-5-5", None) == (
        "python scripts/llm_role_eval.py --role filter --model claude-haiku-5-5")


def test_score_effective_label_in_est_and_edt():
    assert ams.score_effective_label("2026-11-03") == (
        "from 00:00 UTC on Tue 2026-11-03 (7:00 PM ET on Mon Nov 2)")   # EST
    assert ams.score_effective_label("2026-10-20") == (
        "from 00:00 UTC on Tue 2026-10-20 (8:00 PM ET on Mon Oct 19)")  # EDT
    assert ams.score_effective_label("2026-11-01").endswith("(8:00 PM ET on Sat Oct 31)")
    assert ams.score_effective_label("junk") == "from junk (UTC date)"


def test_save_effective_line_only_for_score():
    assert ams.save_effective_line("filter", NOW) is None
    line = ams.save_effective_line("score", NOW)
    assert "from 00:00 UTC on Sat 2026-11-21 (7:00 PM ET on Fri Nov 20)" in line


def test_eval_summary_for_each_role():
    assert ams.eval_summary(None) is None
    e = _entry()
    row = _eval(e, "score", metrics={"mean_shift_net": 0.2, "mean_shift_net_abs": 0.2,
                                     "mean_abs_net": 2.5, "failure_pct": 0.0},
                failures=["net mean |shift| is 2.5, above X 1.0."],
                passed=False, ran_at="2026-11-02T20:05:00+00:00")
    s = ams.eval_summary(row)
    assert s["verdict"] == "FAIL" and s["passed"] is False
    assert s["ran_at"] == "Mon Nov 2, 3:05 PM ET"
    assert s["sample_n"] == 300 and s["failures"] == row["failures"]
    by = {m["constant"]: m for m in s["metrics"]}
    assert by["LLM_EVAL_SCORE_MAX_MEAN_SHIFT_PTS"]["within"] is True
    assert by["LLM_EVAL_SCORE_MAX_MEAN_ABS_SHIFT_PTS"]["within"] is False
    assert by["LLM_EVAL_SCORE_MAX_BAND_CROSS_PCT"]["value"] == "not measured"
    assert by["LLM_EVAL_SCORE_MAX_BAND_CROSS_PCT"]["within"] is False
    assert ams.eval_summary(_eval(e, "filter"))["verdict"] == "PASS"
    assert len(ams.eval_summary(_eval(e, "extract_trigger"))["metrics"]) == len(
        llm_role_eval.limit_table("extract_trigger"))


# ---- base payload, status, view, reset ----
def test_base_payload_for_decision():
    assert ams.base_payload_for_decision(lds.STATE_NO_ROW, None) == (
        {"version": 1, "roles": {}}, [])
    base, notes = ams.base_payload_for_decision(lds.STATE_UNREADABLE, None)
    assert base is None and notes
    good = {"model": "claude-haiku-5-5", "effort": "low", "capabilities": CAPS,
            "checked_at": None, "eval_id": 7, "criteria_version": 1}
    row = {"payload": {"version": 1, "roles": {"filter": good}},
           "payload_hash": reference_data.payload_hash_for(
               lds.TABLE_NAME, lds.canonicalize({"version": 1, "roles": {"filter": good}})),
           "as_of": "2026-11-01", "updated_by": "t"}
    base, notes = ams.base_payload_for_decision(lds.STATE_OK, row)
    assert base["roles"] == {"filter": good} and notes == []
    row["payload_hash"] = "bad"
    base, notes = ams.base_payload_for_decision(lds.STATE_OK, row)
    assert base == {"version": 1, "roles": {}} and "invalid" in notes[0]


def test_base_payload_drops_a_role_entry_that_no_longer_validates():
    bad = {"model": "claude-haiku-5-5", "effort": "low", "capabilities": CAPS,
           "checked_at": None}       # not the code default and no eval_id
    p = {"version": 1, "roles": {"filter": bad}}
    row = {"payload": p, "payload_hash": reference_data.payload_hash_for(
        lds.TABLE_NAME, lds.canonicalize(p)), "as_of": "x", "updated_by": "t"}
    base, notes = ams.base_payload_for_decision(lds.STATE_OK, row)
    assert base["roles"] == {} and "filter" in notes[0]


def test_decision_status_levels_and_stale():
    ok = _snap({"version": 1, "roles": {}})
    assert ams.decision_status(ok)[0] == "ok"
    level, text, others = ams.decision_status(lds.build_snapshot(lds.STATE_NO_ROW, None))
    assert level == "ok" and "code defaults" in text and others == []
    # Unreadable is red on both pages (owner U4), like ai_models_view.decision_status.
    assert ams.decision_status(lds.build_snapshot(lds.STATE_UNREADABLE, None))[0] == "error"
    bad = lds.build_snapshot(lds.STATE_OK, {"payload": {"version": 1, "roles": {}},
                                            "payload_hash": "bad"})
    assert ams.decision_status(bad)[0] == "error"
    stale = lds.replace(ok, stale=True, warnings=(lds.WARN_STALE,))
    level, text, others = ams.decision_status(stale)
    assert level == "warn" and "last good copy" in text and others == []


def test_decision_role_view_defaults_and_score_dates():
    empty = lds.build_snapshot(lds.STATE_NO_ROW, None)
    v = ams.decision_role_view("filter", empty, NOW)
    assert v["model"] == llm.CODE_DEFAULTS["filter"] and v["source"] == llm.SOURCE_CODE
    assert v["effective_line"] is None
    score = _cur(effective_from="2026-11-10", cum=0.3)
    snap = _snap({"version": 1, "roles": {"score": score}})
    v = ams.decision_role_view("score", snap, NOW)
    assert v["model"] == "claude-sonnet-5-5" and v["source"] == llm.SOURCE_SETTING
    assert v["effective_line"].startswith("This entry applies from 00:00 UTC on Tue 2026-11-10")
    assert "code default" in v["prior_line"]
    assert "+0.30 pts" in v["cumulative_line"]
    before = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    v = ams.decision_role_view("score", snap, before)
    assert v["model"] == llm.CODE_DEFAULTS["score"] and v["source"] == llm.SOURCE_CODE
    assert v["effective_line"].startswith("Pending: claude-sonnet-5-5 applies")


def test_can_reset_decision():
    snap = _snap({"version": 1, "roles": {"filter": _cur("filter")}})
    assert ams.can_reset_decision("filter", snap) == (True, "")
    ok, why = ams.can_reset_decision("score", snap)
    assert ok is False and why
    bad = lds.build_snapshot(lds.STATE_OK, {"payload": {"version": 1, "roles": {}},
                                            "payload_hash": "bad"})
    assert ams.can_reset_decision("score", bad)[0] is True


def test_decision_save_message_names_the_score_date():
    saved = {"status": "saved"}
    lvl, text = ams.decision_save_message(saved, "ok", "score", "2026-11-03")
    assert lvl == "success" and "from 00:00 UTC on Tue 2026-11-03" in text
    lvl, text = ams.decision_save_message(saved, "ok", "filter")
    assert lvl == "success" and "next AI call" in text
    assert ams.decision_save_message({"status": "error", "detail": "x"}, "ok", "score")[0] == "error"


# ---- history ----
def test_decision_history_lines():
    f = _cur("filter", "claude-haiku-5-5", "low")
    s1 = _cur(effective_from="2026-11-03")
    assert ams.decision_history_lines({"version": 1, "roles": {}}, None) == [
        "Initial capture: no role set (every decision role on its code default)"]
    first = ams.decision_history_lines({"version": 1, "roles": {"filter": f, "score": s1}}, None)
    assert first == ["Initial capture: filter: claude-haiku-5-5 (effort low), "
                     "score: claude-sonnet-5-5 from 2026-11-03"]
    older = {"version": 1, "roles": {"filter": f, "score": s1}}
    s2 = _cur(model="claude-haiku-5-5", effort="low", effective_from="2026-11-20")
    new = {"version": 1, "roles": {"filter": f, "score": s2}}
    assert ams.decision_history_lines(new, older) == [
        "score: claude-sonnet-5-5 from 2026-11-03 → claude-haiku-5-5 (effort low) from 2026-11-20"]
    gone = {"version": 1, "roles": {"score": s1}}
    assert ams.decision_history_lines(gone, older) == [
        "filter: claude-haiku-5-5 (effort low) → code default (reset)"]
    assert ams.decision_history_lines(older, older) == ["No role changes"]
    recheck = {"version": 1, "roles": {"filter": {**f, "checked_at": "2030-01-01T00:00:00"},
                                       "score": s1}}
    assert ams.decision_history_lines(recheck, older) == ["No role changes"]
    caps2 = {"version": 1, "roles": {"filter": {**f, "capabilities": None}, "score": s1}}
    assert "stored capabilities updated" in ams.decision_history_lines(caps2, older)[0]


# ---- Opus review fixes (F1-F9) ----
@pytest.mark.parametrize("role", ["score", "filter"])
def test_f1_eval_baseline_must_equal_what_resolves_today(role):
    e = _entry()
    ok, why = _decide(role, e, eval_row=_eval(e, role, base=("claude-sonnet-5-5", None)))
    assert ok is False
    assert "this eval measured" in why and "claude-sonnet-5-5" in why
    assert "is in effect now; re-run the eval" in why
    ok, why = _decide(role, e, eval_row=_eval(e, role, base=(llm.CODE_DEFAULTS[role], "low")))
    assert ok is False and "re-run the eval" in why           # effort mismatch
    row = _eval(e, role)
    del row["metrics"]["current_model"]
    del row["metrics"]["current_effort"]
    ok, why = _decide(role, e, eval_row=row)
    assert ok is False and "re-run the eval" in why           # fields missing
    assert _decide(role, e, eval_row=_eval(e, role, base=(llm.CODE_DEFAULTS[role], None))
                   ) == (True, "")


def test_f1_baseline_effort_compares_none_equal():
    e = _entry()
    row = _eval(e, "filter")
    row["metrics"]["current_effort"] = ""         # falsy == None
    assert _decide("filter", e, eval_row=row) == (True, "")


def test_f1_stored_choice_is_the_baseline_and_extract_trigger_is_exempt():
    e = _entry()
    stored = _cur("filter", "claude-sonnet-5-5", None)
    assert _decide("filter", e, current=stored) == (True, "")
    stale = _eval(e, "filter", base=(llm.CODE_DEFAULTS["filter"], None))
    assert _decide("filter", e, current=stored, eval_row=stale)[0] is False
    row = _eval(e, "extract_trigger", base=("claude-sonnet-5-5", "high"))
    assert _decide("extract_trigger", e, eval_row=row) == (True, "")
    row = _eval(e, "extract_trigger")
    row["metrics"] = {}
    assert _decide("extract_trigger", e, eval_row=row) == (True, "")


def test_f1_score_baseline_is_the_model_resolving_today():
    e = _entry()
    old = {"model": "claude-sonnet-5-5", "effort": None, "capabilities": CAPS}
    cur = _cur(effective_from="2026-11-01", prior=old)        # in effect on 11-20
    ok, why = _decide("score", e, current=cur,
                      eval_row=_eval(e, base=("claude-sonnet-5-5", None)))
    assert (ok, why) == (True, "")
    ok, why = _decide("score", e, current=cur, eval_row=_eval(e))   # code-default baseline
    assert ok is False and "re-run the eval" in why


def test_f2a_score_save_refused_while_a_switch_is_pending():
    e = _entry()
    pending = _cur(effective_from="2026-11-21")               # NOW is 11-20
    ok, why = _decide("score", e, current=pending)
    assert ok is False
    assert why == ("a score switch to claude-sonnet-5-5 is already pending from "
                   "2026-11-21 (00:00 UTC); wait until it applies, then re-run the eval")
    due = _cur(effective_from="2026-11-20")                   # applies today: not pending
    assert _decide("score", e, current=due,
                   eval_row=_eval(e, base=("claude-sonnet-5-5", None))) == (True, "")


def test_f2c_score_cumulative_for_both_sides_of_effective_from():
    assert lds.score_cumulative_for(None, "2026-11-20") == 0.0
    e = _cur(effective_from="2026-11-21", cum=0.7)
    assert lds.score_cumulative_for(e, "2026-11-21") == 0.7
    assert lds.score_cumulative_for(e, "2026-11-30") == 0.7
    assert lds.score_cumulative_for(e, "2026-11-20") == 0.7   # no cumulative_before: falls back
    e["cumulative_before"] = 0.3
    assert lds.score_cumulative_for(e, "2026-11-20") == 0.3
    assert lds.score_cumulative_for(e, "2026-11-21") == 0.7


def test_f2b_cumulative_before_is_valid_on_score_only_and_normalised():
    e = _cur(cum=0.7)
    e["cumulative_before"] = 0.3
    assert lds.validate_role_entry("score", e) == []
    e["cumulative_before"] = float("nan")
    assert lds.validate_role_entry("score", e)
    f = _cur("filter")
    f["cumulative_before"] = 0.1
    assert any("unexpected key" in x for x in lds.validate_role_entry("filter", f))
    a = lds.canonicalize({"version": 1, "roles": {"score": {**_cur(), "cumulative_before": -0.0}}})
    assert math.copysign(1.0, a["roles"]["score"]["cumulative_before"]) == 1.0
    assert str(a["roles"]["score"]["cumulative_before"]) == "0.0"


def test_f2d_build_entry_records_cumulative_before_and_uses_today_total():
    e = _entry()
    cur = _cur(cum=0.5)
    entry = ams.build_decision_entry("score", e, _eval(e, shift=0.2), cur, NOW)
    assert entry["cumulative_before"] == 0.5
    assert entry["cumulative_shift_pts"] == 0.7
    assert lds.validate_role_entry("score", entry) == []
    pending = _cur(effective_from="2026-11-25", cum=0.9)
    pending["cumulative_before"] = 0.4
    entry = ams.build_decision_entry("score", e, _eval(e, shift=0.2), pending, NOW)
    assert entry["cumulative_before"] == 0.4 and entry["cumulative_shift_pts"] == 0.6
    fresh = ams.build_decision_entry("score", e, _eval(e, shift=-0.0), None, NOW)
    assert math.copysign(1.0, fresh["cumulative_before"]) == 1.0


def test_f2d_reset_during_a_pending_switch_keeps_the_pre_switch_total_until_it_applies():
    pending = _cur(effective_from="2026-11-25", cum=0.9)
    pending["cumulative_before"] = 0.4
    out = lds.reset_role({"version": 1, "roles": {"score": pending}}, "score", NOW)
    e = out["roles"]["score"]
    # pending window: the pre-switch total (0.4); once applied: back at the origin (0).
    assert e["cumulative_before"] == 0.4 and e["cumulative_shift_pts"] == 0.0
    assert lds.validate_role_entry("score", e) == []


def test_f2d_script_cumulative_reads_the_total_for_today(monkeypatch):
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "llm_role_eval.py"
    spec = importlib.util.spec_from_file_location("_llm_role_eval_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pending = _cur(effective_from="2999-01-01", cum=0.9)
    pending["cumulative_before"] = 0.4
    snap = _snap({"version": 1, "roles": {"score": pending}})
    monkeypatch.setattr(lds, "get_snapshot", lambda: snap)
    assert mod._score_cumulative() == 0.4


def test_f3_eval_for_another_role_or_not_strictly_true_is_refused():
    e = _entry()
    ok, why = _decide("filter", e, eval_row=_eval(e, "score"))
    assert ok is False and "role" in why
    row = _eval(e, "filter")
    row["role"] = None
    assert _decide("filter", e, eval_row=row)[0] is False
    for passed in (1, "true", None):
        ok, why = _decide("filter", e, eval_row=_eval(e, "filter", passed=passed))
        assert ok is False and "did not pass" in why


def test_f4_future_ran_at_boundary():
    e = _entry()
    skew = ams._CLOCK_SKEW_SEC
    assert skew == 300
    at = _eval(e, "filter", age=timedelta(seconds=-skew))
    assert _decide("filter", e, eval_row=at) == (True, "")
    over = _eval(e, "filter", age=timedelta(seconds=-skew - 1))
    ok, why = _decide("filter", e, eval_row=over)
    assert ok is False and "future" in why


def test_f5_unusable_now_refuses_instead_of_raising():
    e = _entry()
    ok, why = _decide("filter", e, now=None)
    assert ok is False and why
    ok, why = _decide("filter", e, now="junk")
    assert ok is False and why
    ok, why = _decide("filter", e, eval_row=_eval(e, "filter", ran_at=12345))
    assert ok is False and why


def test_f6_other_threads_reach_the_real_complete_during_a_test(monkeypatch):
    import threading
    real_calls = []
    monkeypatch.setattr(llm, "complete",
                        lambda role, **kw: real_calls.append(role) or _Result("real"))
    entered, release = threading.Event(), threading.Event()

    def held(role, **kw):
        entered.set()
        assert release.wait(10)
        return _Result(_SCORE_REPLY)

    out = {}
    t = threading.Thread(target=lambda: out.update(
        ams.run_test("score", _entry(), "key", complete=held)))
    t.start()
    try:
        assert entered.wait(10)
        assert news_intelligence.llm is not llm        # the proxy is in place
        got = news_intelligence.llm.complete("fast", system="s", user="u")
        assert got.text == "real" and real_calls == ["fast"]
    finally:
        release.set()
        t.join(10)
    assert out["ok"] is True and out["format_ok"] is True    # the held reply was recorded
    assert real_calls == ["fast"]                             # and the other call was not
    assert news_intelligence.llm is llm and premortem_monitor.llm is llm


def test_f6_a_failure_part_way_still_restores_every_module(monkeypatch):
    made = []
    real_proxy = ams._LLMProxy

    def flaky(real):
        if made:
            raise RuntimeError("second proxy fails")
        made.append(1)
        return real_proxy(real)

    monkeypatch.setattr(ams, "_LLMProxy", flaky)
    with pytest.raises(RuntimeError):
        ams._production_call(ams.TEST_CASES["score"], "key", {}, _fake("score"))
    assert news_intelligence.llm is llm and premortem_monitor.llm is llm
    assert getattr(ams._tl, "rec", None) is None


def test_f7_any_decision_save_refused_while_the_decision_row_is_invalid():
    e = _entry()
    ok, why = _decide("score", e, snapshot_state=lds.STATE_INVALID)
    assert ok is False
    assert why == ("the saved decision-role row is invalid; Reset score first "
                   "(its running drift total then restarts at 0), then save again")
    assert _decide("score", e, snapshot_state=lds.STATE_OK) == (True, "")
    # A filter / extract_trigger Save would rewrite the row from an empty base and
    # drop score's entry and its drift total, so it is refused too.
    assert _decide("filter", e, snapshot_state=lds.STATE_INVALID)[0] is False
    assert _decide("filter", e, snapshot_state=lds.STATE_OK) == (True, "")


def test_f9_reset_of_a_default_score_entry_is_a_no_op():
    default = _cur(model=llm.CODE_DEFAULTS["score"], effort=None, effective_from="2026-11-10")
    snap = _snap({"version": 1, "roles": {"score": default}})
    ok, why = ams.can_reset_decision("score", snap, NOW)
    assert ok is False and why
    pending = _cur(model=llm.CODE_DEFAULTS["score"], effort=None, effective_from="2026-11-25")
    snap = _snap({"version": 1, "roles": {"score": pending}})
    assert ams.can_reset_decision("score", snap, NOW) == (True, "")
    other = _cur(effective_from="2026-11-10")
    snap = _snap({"version": 1, "roles": {"score": other}})
    assert ams.can_reset_decision("score", snap, NOW) == (True, "")


def test_f8_save_effective_line_wording():
    line = ams.save_effective_line("score", NOW)
    assert "isn't split between two models in normal operation" in line
    assert "trading day never mixes" not in line
