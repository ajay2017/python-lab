import pytest

from stock_analyzer import ai_models_view as v
from stock_analyzer import llm, llm_settings


def _snap(state=llm_settings.STATE_NO_ROW, setting=None, warnings=(), as_of=None,
          updated_by=None):
    return llm_settings.Snapshot(state, setting or {}, tuple(warnings), as_of, updated_by)


def _row(rows, role):
    return next(r for r in rows if r["role"] == role)


# ---- key_status ----
def test_key_status_unknown_when_not_fetched():
    assert v.key_status("claude-x", None) == "unknown"


def test_key_status_available_and_not_listed():
    assert v.key_status("claude-x", ["claude-x"]) == "available"
    assert v.key_status("claude-x", ["claude-y"]) == "not_listed"
    assert v.key_status("claude-x", []) == "not_listed"


def test_key_status_dated_variant_of_an_alias():
    assert v.key_status("claude-sonnet-4-6", ["claude-sonnet-4-6-20260101"]) == "available"
    # a different id that merely shares a prefix is not a match
    assert v.key_status("claude-sonnet-4", ["claude-sonnet-45"]) == "not_listed"


def test_key_status_only_exact_or_eight_digit_dated_variant():
    assert v.key_status("claude-opus-5", ["claude-opus-5-5"]) == "not_listed"
    assert v.key_status("claude-sonnet-4", ["claude-sonnet-4-6-20260101"]) == "not_listed"
    assert v.key_status("claude-haiku-4-5", ["claude-haiku-4-5-20251001"]) == "available"


# ---- build_rows ----
def test_default_snapshot_is_all_code_default():
    rows = v.build_rows(_snap(), env={})
    assert [r["role"] for r in rows] == list(v.ROLE_ORDER)
    for r in rows:
        assert r["source"] == "code" and r["source_label"] == "code default"
        assert r["model"] == llm.CODE_DEFAULTS[r["role"]]
        assert r["notes"] == [] and r["warnings"] == [] and r["usage"] is None
        assert r["key"] == "unknown"


def test_setting_for_fast_shows_app_settings():
    setting = {"fast": llm.RoleSetting("claude-sonnet-5-5")}
    r = _row(v.build_rows(_snap(llm_settings.STATE_OK, setting), env={}), "fast")
    assert r["source"] == "setting" and r["source_label"] == "App Settings"
    assert r["model"] == "claude-sonnet-5-5" and r["notes"] == []


def test_env_for_fast_shows_label_and_n8_note():
    env = {"DRISHTA_LLM_MODEL_FAST": "claude-opus-5-5"}
    r = _row(v.build_rows(_snap(), env=env), "fast")
    assert r["source"] == "env"
    assert r["source_label"] == "env DRISHTA_LLM_MODEL_FAST"
    assert r["model"] == "claude-opus-5-5"
    assert r["notes"] == ["Set by an environment variable; an App Settings save "
                          "won't take effect while it is set"]


def test_env_and_setting_both_set_mentions_saved_model():
    setting = {"fast": llm.RoleSetting("claude-sonnet-5-5")}
    env = {"DRISHTA_LLM_MODEL_FAST": "claude-opus-5-5"}
    r = _row(v.build_rows(_snap(llm_settings.STATE_OK, setting), env=env), "fast")
    assert r["model"] == "claude-opus-5-5"
    assert r["notes"][0].endswith("(App Settings has claude-sonnet-5-5 saved)")


def test_bad_env_on_editable_role_is_noted_and_ignored():
    r = _row(v.build_rows(_snap(), env={"DRISHTA_LLM_MODEL_FAST": "gpt-4"}), "fast")
    assert r["source"] == "code" and r["model"] == llm.CODE_DEFAULTS["fast"]
    assert r["notes"] == ["Environment variable DRISHTA_LLM_MODEL_FAST is set but "
                          "ignored (not a claude- model id)"]


def test_env_on_non_editable_role_is_ignored_note():
    r = _row(v.build_rows(_snap(), env={"DRISHTA_LLM_MODEL_SCORE": "claude-opus-5-5"}),
             "score")
    assert r["source"] == "code" and r["model"] == llm.CODE_DEFAULTS["score"]
    assert r["notes"] == ["Environment variable DRISHTA_LLM_MODEL_SCORE is ignored: "
                          "this role isn't editable"]


def test_role_warnings_go_only_to_their_row():
    warns = ("fast: ['bad']; code default in use", "some general warning")
    rows = v.build_rows(_snap(llm_settings.STATE_OK, warnings=warns), env={})
    assert _row(rows, "fast")["warnings"] == [warns[0]]
    for r in rows:
        if r["role"] != "fast":
            assert r["warnings"] == []


def test_usage_passed_through():
    usage = {"fast": {"calls": 3, "errors": 1}}
    rows = v.build_rows(_snap(), env={}, usage=usage)
    assert _row(rows, "fast")["usage"] == {"calls": 3, "errors": 1}
    assert _row(rows, "deep")["usage"] is None


def test_key_status_per_row():
    ids = [llm.CODE_DEFAULTS["fast"]]
    rows = v.build_rows(_snap(), env={}, available_ids=ids)
    assert _row(rows, "fast")["key"] == "available"
    assert _row(rows, "extract_strong")["key"] == "not_listed"


@pytest.mark.parametrize("role", v.ROLE_ORDER)
def test_editable_and_lock_reason(role):
    r = _row(v.build_rows(_snap(), env={}), role)
    if role in ("fast", "deep", "extract"):
        assert r["editable"] is True and r["lock_reason"] is None
    else:
        assert r["editable"] is False and r["lock_reason"] == v.LOCK_REASONS[role]


def test_lock_reason_strings():
    assert v.LOCK_REASONS["score"].startswith("Pinned in reviewed code")
    assert "owner decision D2-1" in v.LOCK_REASONS["extract_strong"]


def test_every_role_has_description_and_locked_roles_have_reasons():
    assert set(v.ROLE_ORDER) == set(llm.ROLES)
    for role in llm.ROLES:
        assert v.ROLE_DESCRIPTIONS[role]
        if role not in llm.EDITABLE_ROLES:
            assert v.LOCK_REASONS[role]


# ---- settings_status ----
def test_settings_status_ok():
    level, text, others = v.settings_status(
        _snap(llm_settings.STATE_OK, as_of="2026-10-25", updated_by="owner"))
    assert level == "ok"
    assert text == "App Settings row saved 2026-10-25 by owner" and others == []


def test_settings_status_no_row():
    level, text, _ = v.settings_status(_snap())
    assert level == "ok"
    assert text == ("No App Settings row: every editable role is on its code "
                    "default unless an environment variable is set (see the table)")


def test_settings_status_ok_with_missing_meta():
    _, text, _ = v.settings_status(_snap(llm_settings.STATE_OK))
    assert text == "App Settings row saved an unknown date by unknown"


def test_settings_status_unreadable():
    level, text, others = v.settings_status(
        _snap(llm_settings.STATE_UNREADABLE,
              warnings=(llm_settings._WARN_UNREADABLE,)))
    assert (level, text) == ("warn", "AI model settings couldn't be read; "
                                     "code defaults in use unless an "
                                     "environment variable is set")
    assert others == []


def test_settings_status_invalid_and_other_warnings():
    warns = ("fast: ['x']; code default in use", "row doesn't match its hash")
    level, text, others = v.settings_status(
        _snap(llm_settings.STATE_INVALID, warnings=warns))
    assert (level, text) == ("error", "AI model settings row is invalid; "
                                      "code defaults in use unless an "
                                      "environment variable is set")
    assert others == ["row doesn't match its hash"]


# ---- equivalence with llm.resolved ----
def _assert_rows_match_resolved():
    rows = v.build_rows(llm_settings.get_snapshot(), env={})
    assert {r["role"] for r in rows} == set(llm.ROLES)
    for r in rows:
        assert r["model"] == llm.resolved(r["role"]), r["role"]


def test_rows_match_llm_resolved_under_no_row_stub():
    _assert_rows_match_resolved()


def test_rows_match_llm_resolved_with_valid_settings_row(monkeypatch):
    from stock_analyzer import reference_data
    payload = {"version": 1, "roles": {}}
    payload = llm_settings.apply_role(payload, "fast", {
        "model": "claude-sonnet-5-5", "effort": "low", "capabilities": None,
        "checked_at": None})
    payload = llm_settings.apply_role(payload, "extract", {
        "model": "claude-haiku-5-5", "effort": None, "capabilities": None,
        "checked_at": None})
    norm = llm_settings.canonicalize(payload)
    row = {"payload": norm,
           "payload_hash": reference_data.payload_hash_for(llm_settings.TABLE_NAME, norm),
           "as_of": "2026-10-25", "updated_by": "t"}
    monkeypatch.setattr(llm_settings, "_default_loader", lambda: ("ok", row))
    llm_settings.invalidate()
    try:
        snap = llm_settings.get_snapshot()
        assert snap.state == llm_settings.STATE_OK and set(snap.setting) == {"fast", "extract"}
        _assert_rows_match_resolved()
        rows = v.build_rows(snap, env={})
        assert next(r for r in rows if r["role"] == "fast")["source"] == "setting"
    finally:
        llm_settings.invalidate()
