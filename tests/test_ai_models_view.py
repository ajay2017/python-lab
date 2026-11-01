import pytest

from stock_analyzer import ai_models_view as v
from stock_analyzer import llm, llm_settings
from stock_analyzer import llm_decision_settings as lds


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


def test_env_on_decision_role_is_ignored_note():
    for role in llm.DECISION_ROLES:
        var = "DRISHTA_LLM_MODEL_" + role.upper()
        r = _row(v.build_rows(_snap(), env={var: "claude-opus-5-5"}), role)
        assert r["source"] == "code" and r["model"] == llm.CODE_DEFAULTS[role]
        assert r["notes"] == [f"{var} is set but ignored for this role"]


def test_env_on_pinned_role_is_ignored_note():
    r = _row(v.build_rows(_snap(), env={"DRISHTA_LLM_MODEL_EXTRACT_STRONG": "claude-x-1"}),
             "extract_strong")
    assert r["source"] == "code"
    assert r["notes"] == ["Environment variable DRISHTA_LLM_MODEL_EXTRACT_STRONG is "
                          "ignored: this role isn't editable"]


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
        assert r["editable_label"] == "yes"
    else:
        assert r["editable"] is False and r["lock_reason"] == v.LOCK_REASONS[role]
        if role in llm.DECISION_ROLES:
            assert r["editable_label"].startswith("yes, after a passing eval")
        else:
            assert r["editable_label"] == v.LOCK_REASONS[role]


def test_lock_reason_strings():
    assert v.LOCK_REASONS["score"].startswith("Switched in ⚙️ App Settings → Decision roles")
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
                    "default unless an environment variable is set (see each role below)")


def test_settings_status_ok_with_missing_meta():
    _, text, _ = v.settings_status(_snap(llm_settings.STATE_OK))
    assert text == "App Settings row saved an unknown date by unknown"


def test_settings_status_unreadable():
    level, text, others = v.settings_status(
        _snap(llm_settings.STATE_UNREADABLE,
              warnings=(llm_settings.WARN_UNREADABLE,)))
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


# ---- decision roles (plan 17, C6) ----
H55 = "claude-haiku-5-5"


def _dsnap(entries=None, state=lds.STATE_OK, warnings=(), stale=False, stale_since=None,
           as_of="2026-11-01", by="owner"):
    return lds.Snapshot(state, entries or {}, tuple(warnings), as_of, by, stale,
                        stale_since)


def _entry(model=H55, **kw):
    e = {"model": model, "effort": "low", "capabilities": None, "checked_at": None,
         "eval_id": 7, "criteria_version": 1}
    e.update(kw)
    return e


def _score_entry(**kw):
    base = _entry(effective_from="2026-11-10", prior=None, cumulative_shift_pts=0.35)
    base.update(kw)
    return base


def test_decision_rows_without_snapshot_show_the_code_default():
    for role in llm.DECISION_ROLES:
        r = _row(v.build_rows(_snap(), env={}), role)
        assert r["model"] == llm.CODE_DEFAULTS[role] and r["source_label"] == "code default"
        assert r["detail"] is None and r["grey_notes"] == []


def test_filter_row_shows_the_saved_model_eval_and_source():
    d = _dsnap({"filter": _entry(eval_id=12, criteria_version=3)})
    r = _row(v.build_rows(_snap(), env={}, decision_snapshot=d), "filter")
    assert r["model"] == H55 and r["source"] == "setting"
    assert r["source_label"] == "App Settings"
    assert r["eval_id"] == 12 and r["criteria_version"] == 3
    assert r["detail"] == "Eval #12 (criteria v3)"
    # an unchanged role keeps the code default
    assert _row(v.build_rows(_snap(), env={}, decision_snapshot=d),
                "extract_trigger")["source_label"] == "code default"


def test_score_pending_switch_shows_today_prior_and_the_effective_date():
    prior = {"model": llm.HAIKU_4_5, "effort": None, "capabilities": None}
    d = _dsnap({"score": _score_entry(prior=prior)})
    r = _row(v.build_rows(_snap(), env={}, decision_snapshot=d, today="2026-11-09"),
             "score")
    assert r["model"] == llm.HAIKU_4_5 and r["source_label"] == "App Settings"
    assert r["pending"] == f"switches to {H55} from 00:00 UTC on 2026-11-10"
    assert "Switches to claude-haiku-5-5 from 00:00 UTC on 2026-11-10" in r["detail"]
    assert r["cumulative_shift_pts"] == 0.35
    # pending: today's total, then the total after the switch
    assert "Cumulative score shift across switches today:" in r["detail"]
    assert "pts after the pending switch" in r["detail"]


def test_score_without_prior_before_effective_date_is_the_code_default():
    d = _dsnap({"score": _score_entry()})
    r = _row(v.build_rows(_snap(), env={}, decision_snapshot=d, today="2026-11-09"),
             "score")
    assert r["model"] == llm.CODE_DEFAULTS["score"] and r["source_label"] == "code default"
    assert r["pending"] is not None


def test_score_effective_from_boundary_day_is_the_new_model():
    d = _dsnap({"score": _score_entry()})
    on = _row(v.build_rows(_snap(), env={}, decision_snapshot=d, today="2026-11-10"),
              "score")
    assert on["model"] == H55 and on["pending"] is None
    assert "Switches to" not in on["detail"]


def test_grey_note_when_saved_model_differs_from_the_code_default():
    d = _dsnap({"filter": _entry(), "extract_trigger": _entry(
        model=llm.CODE_DEFAULTS["extract_trigger"], eval_id=None, criteria_version=None)})
    rows = v.build_rows(_snap(), env={}, decision_snapshot=d)
    assert any("code default not yet moved to match" in n
               for n in _row(rows, "filter")["grey_notes"])
    assert _row(rows, "extract_trigger")["grey_notes"] == []
    assert _row(rows, "extract_trigger")["detail"].startswith("No eval behind")


def test_env_note_on_decision_role_with_a_saved_entry():
    d = _dsnap({"filter": _entry()})
    r = _row(v.build_rows(_snap(), env={"DRISHTA_LLM_MODEL_FILTER": "claude-opus-5-5"},
                          decision_snapshot=d), "filter")
    assert r["model"] == H55 and r["source"] == "setting"
    assert r["notes"] == ["DRISHTA_LLM_MODEL_FILTER is set but ignored for this role"]


def test_decision_warnings_go_to_their_row_not_the_editable_ones():
    d = _dsnap({}, warnings=("filter: ['bad']; code default in use", "general"))
    rows = v.build_rows(_snap(llm_settings.STATE_OK, warnings=("fast: ['x']",)),
                        env={}, decision_snapshot=d)
    assert _row(rows, "filter")["warnings"] == ["filter: ['bad']; code default in use"]
    assert _row(rows, "fast")["warnings"] == ["fast: ['x']"]


@pytest.mark.parametrize("state", [lds.STATE_NO_ROW, lds.STATE_UNREADABLE,
                                   lds.STATE_INVALID])
def test_non_ok_decision_snapshot_shows_code_defaults(state):
    r = _row(v.build_rows(_snap(), env={}, decision_snapshot=_dsnap(state=state)),
             "score")
    assert r["model"] == llm.CODE_DEFAULTS["score"] and r["detail"] is None


def test_decision_status_ok_no_row_unreadable_invalid():
    assert v.decision_status(_dsnap()) == (
        "ok", "AI decision-model settings saved 2026-11-01 by owner", [])
    level, text, _ = v.decision_status(_dsnap(state=lds.STATE_NO_ROW))
    assert level == "ok" and "all decision roles on code defaults" in text
    level, text, others = v.decision_status(_dsnap(
        state=lds.STATE_UNREADABLE, warnings=(lds.WARN_UNREADABLE,)))
    assert level == "error" and "couldn't be read" in text and others == []
    level, text, others = v.decision_status(_dsnap(
        state=lds.STATE_INVALID, warnings=("row doesn't match its hash",)))
    assert level == "error" and "invalid" in text and others == ["row doesn't match its hash"]


def test_decision_status_stale_names_the_et_time():
    # 15:30 UTC on 2026-11-01 is 10:30 ET (EST, after the 11-01 fall-back)
    d = _dsnap(stale=True, stale_since="2026-11-01T15:30:00+00:00",
               warnings=(lds.WARN_STALE,))
    level, text, others = v.decision_status(d)
    assert level == "warn" and others == []
    assert text == ("AI decision-model settings couldn't be re-read: serving the "
                    "last good copy since 2026-11-01 10:30 ET")
    _, no_when, _ = v.decision_status(_dsnap(stale=True))
    assert no_when.endswith("serving the last good copy")


def test_model_counts_text():
    t = v.model_counts_text({"legacy (Haiku 4.5)": 30, H55: 1200})
    assert t == ("Today's news-score rows by model: "
                 "claude-haiku-5-5: 1,200 · legacy (Haiku 4.5): 30")
    assert v.model_counts_text({}) == "Today's news-score rows by model: none yet today"
    assert v.model_counts_text(None) == "Today's news-score rows by model: couldn't be read"


def test_todays_counts_line_reads_todays_score_date_and_never_raises(monkeypatch):
    from stock_analyzer import db, sentiment_cache
    seen = []
    monkeypatch.setattr(db, "count_sentiment_cache_models",
                        lambda d: seen.append(d) or {H55: 2})
    assert v.todays_model_counts_line().endswith("claude-haiku-5-5: 2")
    assert seen == [sentiment_cache.score_date_today()]

    def boom(d):
        raise RuntimeError("x")
    monkeypatch.setattr(db, "count_sentiment_cache_models", boom)
    assert v.todays_model_counts_line().endswith("couldn't be read")


def test_decision_resolution_summary_names_roles_and_state(monkeypatch):
    prior = {"model": llm.HAIKU_4_5, "effort": None, "capabilities": None}
    snap = _dsnap({"score": _score_entry(prior=prior), "filter": _entry()})
    monkeypatch.setattr(lds, "get_snapshot", lambda **k: snap)
    out = lds.resolution_summary()
    assert "score=" in out and "from 2026-11-10" in out and f"prior {llm.HAIKU_4_5}" in out
    assert f"filter={H55} (setting)" in out
    assert f"extract_trigger={llm.CODE_DEFAULTS['extract_trigger']} (code)" in out
    assert out.endswith("decision_settings=ok")


def test_decision_resolution_summary_never_raises(monkeypatch):
    def boom(**k):
        raise RuntimeError("x")
    monkeypatch.setattr(lds, "get_snapshot", boom)
    assert lds.resolution_summary() == "decision model resolution unavailable"
