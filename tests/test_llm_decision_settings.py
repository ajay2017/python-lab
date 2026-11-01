"""Plan 17 C4: the decision-role row (stock_analyzer/llm_decision_settings.py),
its isolation from ``llm_models`` and the resolver wiring in ``llm``."""
from datetime import datetime, timezone

import pytest

from stock_analyzer import db, llm, llm_decision_settings as lds, llm_settings, reference_data

H45 = llm.HAIKU_4_5
NEW = "claude-haiku-5-5"
CAPS = {"efforts": ["low", "medium", "high"], "thinking_types": ["adaptive"],
        "structured_outputs": True, "max_tokens": 64000, "max_input_tokens": 200000}


def _e(model=NEW, effort="low", caps=None, **kw):
    out = {"model": model, "effort": effort, "capabilities": caps, "checked_at": None}
    out.update(kw)
    return out


def _evald(eval_id=7, **kw):
    return _e(eval_id=eval_id, criteria_version=1, **kw)


def _score(caps=None, **kw):
    base = _evald(caps=caps, effective_from="2026-11-10", prior=None,
                  cumulative_shift_pts=0.0)
    base.update(kw)
    return base


def _payload(**roles):
    return {"version": 1, "roles": roles}


def _row(payload):
    norm = lds.canonicalize(payload)
    return {"payload": norm,
            "payload_hash": reference_data.payload_hash_for(lds.TABLE_NAME, norm),
            "as_of": "2026-11-01", "updated_by": "t"}


# ---- validation ----
def test_valid_payload_all_roles():
    p = _payload(score=_score(), filter=_evald(), extract_trigger=_evald())
    assert lds.validate_payload(p) == []


def test_empty_roles_valid():
    assert lds.validate_payload(_payload()) == []


@pytest.mark.parametrize("role", ["filter", "extract_trigger"])
def test_eval_required_unless_code_default(role):
    assert lds.validate_payload(_payload(**{role: _e()}))            # new model, no eval
    assert lds.validate_payload(_payload(**{role: _e(eval_id=1)}))   # no criteria_version
    assert lds.validate_payload(_payload(**{role: _e(criteria_version=1)}))
    assert lds.validate_payload(_payload(**{role: _e(model=H45, effort=None)})) == []


def test_score_eval_required_unless_code_default():
    p = _payload(score=_e(effective_from="2026-11-10"))
    assert lds.validate_payload(p)
    p = _payload(score=_e(model=H45, effort=None, effective_from="2026-11-10"))
    assert lds.validate_payload(p) == []


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "7"])
def test_eval_id_must_be_positive_int(bad):
    assert lds.validate_payload(_payload(filter=_e(eval_id=bad, criteria_version=1)))


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "1"])
def test_criteria_version_must_be_int_ge_1(bad):
    assert lds.validate_payload(_payload(filter=_e(eval_id=1, criteria_version=bad)))


def test_score_only_keys_rejected_on_other_roles():
    for role in ("filter", "extract_trigger"):
        for k, v in (("effective_from", "2026-11-10"), ("prior", None),
                     ("cumulative_shift_pts", 0.0)):
            assert lds.validate_payload(_payload(**{role: _evald(**{k: v})}))


@pytest.mark.parametrize("bad", [None, "", "2026-13-01", "11/10/2026", "2026-1-1", 5])
def test_score_effective_from_required_iso(bad):
    assert lds.validate_payload(_payload(score=_score(effective_from=bad)))


def test_score_prior_shape():
    ok = _payload(score=_score(prior={"model": H45, "effort": None, "capabilities": None}))
    assert lds.validate_payload(ok) == []
    for prior in ("x", {"model": H45, "effort": None, "capabilities": None, "extra": 1},
                  {"model": "gpt-4"}, {"effort": None},
                  {"model": H45, "effort": "low", "capabilities": None}):
        assert lds.validate_payload(_payload(score=_score(prior=prior))), prior


@pytest.mark.parametrize("bad", ["x", True, float("nan"), float("inf")])
def test_cumulative_shift_must_be_finite_number(bad):
    assert lds.validate_payload(_payload(score=_score(cumulative_shift_pts=bad)))


def test_cumulative_shift_default_is_allowed_missing():
    e = _score()
    del e["cumulative_shift_pts"]
    assert lds.validate_payload(_payload(score=e)) == []


def test_unknown_key_role_or_version_invalid():
    assert lds.validate_payload(_payload(filter=_evald(bogus=1)))
    assert lds.validate_payload(_payload(fast=_evald()))
    assert lds.validate_payload(_payload(extract_strong=_evald()))
    assert lds.validate_payload({"version": 2, "roles": {}})
    assert lds.validate_payload({"version": 1, "roles": {}, "x": 1})


def test_entry_validation_reuses_model_rules():
    assert lds.validate_payload(_payload(filter=_evald(model="gpt-4")))
    assert lds.validate_payload(_payload(filter=_evald(effort="max")))
    assert lds.validate_payload(_payload(filter=_evald(model="claude-future-9", effort=None)))


# ---- hash / canonical ----
def test_hash_ignores_checked_at():
    a = lds.canonicalize(_payload(filter=_evald(checked_at="2026-11-01T10:00:00")))
    b = lds.canonicalize(_payload(filter=_evald(checked_at="2026-11-02T10:00:00")))
    ha = reference_data.payload_hash_for(lds.TABLE_NAME, a)
    assert ha == reference_data.payload_hash_for(lds.TABLE_NAME, b)
    c = lds.canonicalize(_payload(filter=_evald(eval_id=8)))
    assert ha != reference_data.payload_hash_for(lds.TABLE_NAME, c)


def test_reference_data_dispatch():
    p = _payload(filter=_evald(checked_at=" 2026-11-01T10:00:00 "))
    assert reference_data.canonicalize_for(lds.TABLE_NAME, p) == lds.canonicalize(p)
    assert reference_data.validate_for_save(lds.TABLE_NAME, p) == []
    assert reference_data.validate_for_save(lds.TABLE_NAME, _payload(fast=_evald()))
    # llm_models unchanged: still its own validator, decision roles still refused
    assert reference_data.validate_for_save(llm_settings.TABLE_NAME, _payload(filter=_e()))
    assert reference_data.validate_for_save("sector_universe", {}) == []


# ---- score date key ----
def _utc(h, m, s, d=1):
    return datetime(2026, 11, d, h, m, s, tzinfo=timezone.utc)


def test_score_effective_from_boundary():
    assert lds.score_effective_from(_utc(10, 0, 0)) == "2026-11-02"
    assert lds.score_effective_from(_utc(23, 54, 59)) == "2026-11-02"
    assert lds.score_effective_from(_utc(23, 55, 0)) == "2026-11-03"
    assert lds.score_effective_from(_utc(23, 59, 59)) == "2026-11-03"
    assert lds.score_effective_from(_utc(0, 0, 0, d=2)) == "2026-11-03"
    assert lds.score_effective_from(datetime(2026, 11, 1, 12, 0)) == "2026-11-02"  # naive = UTC


def test_score_effective_from_month_end():
    assert lds.score_effective_from(
        datetime(2026, 11, 30, 23, 56, tzinfo=timezone.utc)) == "2026-12-02"


def test_score_entry_for_either_side_of_effective_from():
    prior = {"model": H45, "effort": None, "capabilities": None}
    e = lds.canonicalize(_score(effective_from="2026-11-10", prior=prior))
    assert lds.score_entry_for(e, "2026-11-10").model == NEW
    assert lds.score_entry_for(e, "2026-11-11").model == NEW
    assert lds.score_entry_for(e, "2026-11-09") == llm.RoleSetting(H45, None, None)
    e2 = lds.canonicalize(_score(effective_from="2026-11-10", prior=None))
    assert lds.score_entry_for(e2, "2026-11-09") is None


def test_score_entry_for_carries_effort_and_capabilities():
    e = lds.canonicalize(_score(effort="medium", caps=CAPS))
    rs = lds.score_entry_for(e, "2026-11-10")
    assert rs.effort == "medium" and dict(rs.capabilities) == CAPS


# ---- snapshot / current_setting ----
def _snap_for(monkeypatch, status, row):
    monkeypatch.setattr(lds, "_default_loader", lambda: (status, row))
    lds.invalidate()


def test_current_setting_empty_on_no_row_unreadable_invalid(monkeypatch):
    _snap_for(monkeypatch, "no_row", None)
    assert dict(lds.current_setting()) == {}
    assert lds.get_snapshot().status == "no_row"
    _snap_for(monkeypatch, "unreadable", None)
    assert dict(lds.current_setting()) == {}
    assert lds.get_snapshot().state == "unreadable"
    bad = _row(_payload(filter=_evald()))
    bad["payload_hash"] = "0" * 64
    _snap_for(monkeypatch, "ok", bad)
    assert dict(lds.current_setting()) == {} and lds.get_snapshot().state == "invalid"
    unknown_role = _row({"version": 1, "roles": {"fast": _evald()}})
    _snap_for(monkeypatch, "ok", unknown_role)
    assert dict(lds.current_setting()) == {} and lds.get_snapshot().state == "invalid"


def test_current_setting_never_raises(monkeypatch):
    monkeypatch.setattr(lds, "get_snapshot", lambda: 1 / 0)
    assert dict(lds.current_setting()) == {}


def test_current_setting_with_a_row(monkeypatch):
    p = _payload(filter=_evald(), score=_score(effective_from="2026-11-10"))
    _snap_for(monkeypatch, "ok", _row(p))
    assert lds.get_snapshot().state == "ok"
    assert lds.current_setting("2026-11-10")["score"].model == NEW
    assert lds.current_setting("2026-11-10")["filter"].model == NEW
    before = lds.current_setting("2026-11-09")
    assert "score" not in before and before["filter"].model == NEW


def test_invalid_role_entry_is_skipped_with_a_warning(monkeypatch):
    p = _payload(filter=_e(), extract_trigger=_evald())   # filter lacks eval
    _snap_for(monkeypatch, "ok", _row(p))
    snap = lds.get_snapshot()
    assert snap.state == "ok" and set(snap.entries) == {"extract_trigger"}
    assert any("filter" in w for w in snap.warnings)


# ---- isolation both ways ----
def test_broken_decision_row_does_not_touch_narrative_roles(monkeypatch):
    bad = _row(_payload(filter=_evald()))
    bad["payload_hash"] = "x"
    _snap_for(monkeypatch, "ok", bad)
    good = {"version": 1, "roles": {"fast": _e(model=NEW)}}
    norm = llm_settings.canonicalize(good)
    monkeypatch.setattr(llm_settings, "_default_loader", lambda: ("ok", {
        "payload": norm, "payload_hash": reference_data.payload_hash_for("llm_models", norm),
        "as_of": "2026-11-01", "updated_by": "t"}))
    llm_settings.invalidate()
    assert llm.resolved_with_source("fast") == (NEW, "setting")
    assert llm.resolved_with_source("filter") == (H45, "code")


def test_broken_llm_models_row_does_not_touch_decision_roles(monkeypatch):
    monkeypatch.setattr(llm_settings, "_default_loader", lambda: ("unreadable", None))
    llm_settings.invalidate()
    _snap_for(monkeypatch, "ok", _row(_payload(filter=_evald())))
    assert llm.resolved_with_source("filter") == (NEW, "setting")
    assert llm.resolved_with_source("fast") == (H45, "code")


def test_llm_models_row_naming_a_decision_role_is_still_invalid():
    assert llm_settings.validate_payload({"version": 1, "roles": {"filter": _e()}})


# ---- llm wiring ----
def test_resolved_decision_role_follows_setting(monkeypatch):
    _snap_for(monkeypatch, "ok", _row(_payload(filter=_evald(effort="medium"))))
    assert llm.resolved_with_source("filter") == (NEW, "setting")
    rs = llm.resolved_setting("filter")
    assert rs.model == NEW and rs.effort == "medium"
    assert llm.resolved_setting("extract_trigger") is None
    assert llm.resolved("extract_strong") == llm.OPUS_4_8
    # env never overrides
    monkeypatch.setenv("DRISHTA_LLM_MODEL_FILTER", "claude-x")
    assert llm.resolved("filter") == NEW


def test_complete_uses_the_decision_setting_request_shape(monkeypatch):
    _snap_for(monkeypatch, "ok", _row(_payload(filter=_evald(effort="medium"))))
    seen = []

    class _Msgs:
        def create(self, **kw):
            seen.append(kw)
            raise RuntimeError("stop")

    class _Client:
        messages = _Msgs()

    r = llm.complete("filter", api_key="k", messages=[{"role": "user", "content": "x"}],
                     max_tokens=50, client_factory=lambda **kw: _Client())
    assert r.model_source == "setting" and seen[0]["model"] == NEW
    assert seen[0]["output_config"] == {"effort": "medium"}


def test_provider_not_consulted_with_an_explicit_model_for_editable_roles(monkeypatch):
    from stock_analyzer import llm_settings
    calls = []
    monkeypatch.setattr(llm_settings, "current_setting", lambda *a, **k: calls.append(1) or {})
    assert llm._provider_setting("fast", "claude-x") is None
    assert llm._provider_setting("extract_strong", "claude-x") is None
    assert calls == []


def test_decision_role_explicit_model_is_checked_against_the_saved_setting(monkeypatch):
    S = llm.RoleSetting(NEW, "low", None)
    calls = []
    monkeypatch.setattr(lds, "current_setting",
                        lambda *a, **k: calls.append(1) or {"filter": S})
    assert llm._provider_setting("filter", NEW) == {"filter": S}
    assert calls == [1]
    assert llm.resolved_with_source("filter", NEW) == (NEW, "explicit")
    with pytest.raises(ValueError):
        llm.resolved_with_source("filter", llm.CODE_DEFAULTS["filter"])
    llm._provider_setting("filter", None)
    assert len(calls) == 4


def test_decision_role_explicit_model_with_a_failing_provider_means_no_setting(monkeypatch):
    monkeypatch.setattr(lds, "current_setting", lambda *a, **k: 1 / 0)
    code = llm.CODE_DEFAULTS["filter"]
    assert llm._provider_setting("filter", code) is None
    assert llm.resolved_with_source("filter", code) == (code, "explicit")


def test_provider_exception_means_code_default(monkeypatch):
    monkeypatch.setattr(lds, "current_setting", lambda *a, **k: 1 / 0)
    assert llm.resolved_with_source("score") == (H45, "code")


# ---- helpers for C5 ----
def test_apply_role_and_reset_filter():
    p = lds.apply_role(_payload(), "filter", _evald())
    assert set(p["roles"]) == {"filter"} and p["version"] == 1
    assert lds.reset_role(p, "filter", _utc(1, 0, 0))["roles"] == {}


def test_reset_role_extract_trigger_removes_only_it():
    p = _payload(filter=_evald(), extract_trigger=_evald())
    assert set(lds.reset_role(p, "extract_trigger", _utc(1, 0, 0))["roles"]) == {"filter"}


def test_reset_score_is_date_keyed_and_carries_the_total():
    cur = _score(effort="low", caps=CAPS, cumulative_shift_pts=0.4,
                 effective_from="2026-10-20")
    out = lds.reset_role(_payload(score=cur), "score", _utc(12, 0, 0))
    e = out["roles"]["score"]
    assert e["model"] == llm.CODE_DEFAULTS["score"]
    assert e["effective_from"] == "2026-11-02"
    assert e["prior"] == {"model": NEW, "effort": "low", "capabilities": CAPS}
    assert e["cumulative_shift_pts"] == 0.4
    assert lds.validate_payload(out) == []
    late = lds.reset_role(_payload(score=cur), "score", _utc(23, 56, 0))
    assert late["roles"]["score"]["effective_from"] == "2026-11-03"


def test_reset_score_with_a_pending_switch_keeps_what_resolves_today():
    x = {"model": "claude-haiku-4-5", "effort": None, "capabilities": None}
    pending = _score(effective_from="2026-11-02", prior=x)   # D+1
    out = lds.reset_role(_payload(score=pending), "score", _utc(12, 0, 0))
    e = out["roles"]["score"]
    assert e["prior"] == x
    assert lds.score_entry_for(e, "2026-11-01").model == x["model"]
    assert lds.score_entry_for(e, "2026-11-02") is not None
    assert lds.score_entry_for(e, "2026-11-02").model == llm.CODE_DEFAULTS["score"]
    # pending over the code default: nothing to keep
    none_prior = _score(effective_from="2026-11-02", prior=None)
    out2 = lds.reset_role(_payload(score=none_prior), "score", _utc(12, 0, 0))
    assert out2["roles"]["score"]["prior"] is None


def test_negative_zero_cumulative_shift_hashes_like_zero():
    a = _payload(score=_score(cumulative_shift_pts=-0.0))
    b = _payload(score=_score(cumulative_shift_pts=0.0))
    h = lambda p: reference_data.payload_hash_for(lds.TABLE_NAME, lds.canonicalize(p))
    assert h(a) == h(b)
    assert str(lds.canonicalize(a)["roles"]["score"]["cumulative_shift_pts"]) == "0.0"
    assert lds.validate_payload(a) == []


def test_reset_score_without_a_current_entry():
    out = lds.reset_role(_payload(), "score", _utc(12, 0, 0))
    assert out["roles"]["score"]["prior"] is None
    assert out["roles"]["score"]["cumulative_shift_pts"] == 0.0
    assert lds.validate_payload(out) == []


def test_save_invalidates_only_when_saved(monkeypatch):
    lds.get_snapshot()
    assert lds._cached is not None
    monkeypatch.setattr(db, "save_reference_table",
                        lambda *a, **k: {"status": "error", "detail": "x"})
    lds.save(_payload(), "t")
    assert lds._cached is not None
    monkeypatch.setattr(db, "save_reference_table",
                        lambda *a, **k: {"status": "saved", "as_of": "2026-11-01"})
    lds.save(_payload(), "t")
    assert lds._cached is None


def test_score_date_today_is_the_utc_date():
    from stock_analyzer import sentiment_cache
    assert sentiment_cache.score_date_today() == datetime.now(timezone.utc).date().isoformat()
