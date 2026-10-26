"""P2-b: the App Settings model choice (stock_analyzer/llm_settings.py), the
per-table dispatch in reference_data / db, and the load status helper."""
import hashlib
import json
import threading

import anthropic
import httpx
import pytest

from stock_analyzer import db, llm, llm_settings as ls, reference_data
from tests.test_reference_data import _wire_fake_db

CAPS = {"efforts": ["low", "medium", "high"], "thinking_types": ["adaptive"],
        "structured_outputs": True, "max_tokens": 64000, "max_input_tokens": 200000}


def _entry(model="claude-haiku-5-5", effort="low", caps=None, checked_at=None):
    return {"model": model, "effort": effort, "capabilities": caps,
            "checked_at": checked_at}


def _payload(**roles):
    return {"version": 1, "roles": roles}


def _row(payload):
    norm = ls.canonicalize(payload)
    return {"payload": norm,
            "payload_hash": reference_data.payload_hash_for(ls.TABLE_NAME, norm),
            "as_of": "2026-10-25", "updated_by": "t"}


# ---- validation ----
def test_valid_payload():
    p = _payload(fast=_entry(), deep=_entry("claude-sonnet-5-5", "high", CAPS,
                                           "2026-10-25T10:00:00"))
    assert ls.validate_payload(p) == []


def test_empty_roles_is_valid():
    assert ls.validate_payload(_payload()) == []


@pytest.mark.parametrize("p", [None, [], "x"])
def test_payload_must_be_dict(p):
    assert ls.validate_payload(p)


def test_bad_top_level():
    assert ls.validate_payload({"version": 2, "roles": {}})
    assert ls.validate_payload({"version": 1})
    assert ls.validate_payload({"version": 1, "roles": [], })
    assert ls.validate_payload({"version": 1, "roles": {}, "extra": 1})


@pytest.mark.parametrize("role", ["score", "filter", "extract_trigger"])
def test_pinned_role_rejected_by_name(role):
    errs = ls.validate_payload(_payload(**{role: _entry()}))
    assert any(role in e and "pinned" in e for e in errs)


def test_extract_strong_rejected_by_name():
    errs = ls.validate_payload(_payload(extract_strong=_entry()))
    assert any("extract_strong" in e and "code-only" in e for e in errs)


def test_unknown_role_rejected():
    assert ls.validate_payload(_payload(nope=_entry()))


@pytest.mark.parametrize("entry", [
    "claude-haiku-5-5",
    {"model": "claude-haiku-5-5", "extra": 1},
    {"model": 5},
    {"model": "gpt-4"},
    {"model": "claude-"},
    {"model": "claude-Haiku"},
    {"model": "claude-haiku-5-5", "effort": "max"},
    {"model": "claude-haiku-5-5", "effort": "xhigh"},
    {"model": "claude-haiku-5-5", "capabilities": []},
    {"model": "claude-haiku-5-5", "capabilities": {"efforts": "low"}},
    {"model": "claude-haiku-5-5", "capabilities": {"efforts": [1]}},
    {"model": "claude-haiku-5-5", "capabilities": {"max_tokens": "9"}},
    {"model": "claude-haiku-5-5", "capabilities": {"max_tokens": True}},
    {"model": "claude-haiku-5-5", "checked_at": "yesterday"},
    {"model": "claude-haiku-5-5", "checked_at": 5},
])
def test_role_entry_violations(entry):
    assert ls.validate_role_entry("fast", entry)


def _caps(**over):
    return {**CAPS, **over}


@pytest.mark.parametrize("caps", [
    {k: v for k, v in CAPS.items() if k != "max_tokens"},          # missing key
    _caps(extra=1),                                                 # unexpected key
    _caps(efforts=["low", "low"]),                                  # not distinct
    _caps(efforts=["low", "ultra"]),                                # unknown level
    _caps(thinking_types=["adaptive", "adaptive"]),
    _caps(thinking_types=["manual"]),
    _caps(thinking_types="adaptive"),
    _caps(structured_outputs=1),
    _caps(structured_outputs=None),
    _caps(max_tokens=1e20),                                         # float
    _caps(max_tokens=0),
    _caps(max_tokens=-5),
    _caps(max_tokens=True),
    _caps(max_input_tokens=1000.0),
    _caps(max_input_tokens="9"),
    {},
])
def test_capabilities_shape_violations(caps):
    assert ls.validate_role_entry("fast", _entry("claude-some-future-9", "low", caps))


def test_capabilities_none_ints_and_full_shape_pass():
    caps = _caps(max_tokens=None, max_input_tokens=None, efforts=[], thinking_types=[])
    assert ls.validate_role_entry("fast", _entry("claude-haiku-5-5", None, caps)) == []


def test_normalize_capabilities_output_always_validates():
    from types import SimpleNamespace as NS
    from tests.test_llm import _full_info
    for info in (_full_info(), NS(), None):
        caps = llm.normalize_capabilities(info)
        assert set(caps) == llm.CAPABILITY_KEYS
        assert ls.validate_role_entry(
            "fast", _entry("claude-some-future-9", None, caps)) == []


def test_validate_payload_validates_the_canonical_form():
    # unstripped model / effort: the saved (canonical) form is what must pass
    p = _payload(fast={"model": "  claude-haiku-5-5 ", "effort": " low ",
                       "capabilities": None, "checked_at": None})
    assert ls.validate_payload(p) == []


def test_validate_payload_canonicalize_failure_is_an_error(monkeypatch):
    monkeypatch.setattr(ls, "canonicalize", lambda p: 1 / 0)
    assert ls.validate_payload(_payload())


def test_role_entry_non_editable_role():
    assert ls.validate_role_entry("score", _entry())


def test_effort_must_be_listed_by_capabilities():
    e = _entry("claude-haiku-5-5", "high", _caps(efforts=["low", "medium"]))
    assert ls.validate_role_entry("fast", e)
    e = _entry("claude-haiku-5-5", "medium", _caps(efforts=["low", "medium"]))
    assert ls.validate_role_entry("fast", e) == []


@pytest.mark.parametrize("model", ["claude-opus-5", "claude-opus-4-8", llm.HAIKU_4_5,
                                   llm.SONNET_4_6])
def test_effort_rejected_on_profiles_that_drop_it(model):
    assert ls.validate_role_entry("deep", _entry(model, "low"))
    assert ls.validate_role_entry("deep", _entry(model, None)) == []


def test_generic_id_needs_capabilities():
    assert ls.validate_role_entry("fast", _entry("claude-some-future-9", None))
    assert ls.validate_role_entry(
        "fast", _entry("claude-some-future-9", "low", CAPS)) == []


def test_model_is_stripped_before_matching():
    assert ls.validate_role_entry("fast", _entry("  claude-haiku-5-5  ")) == []


# ---- canonicalize / hash_view ----
def test_canonicalize_idempotent_strips_keeps_case_and_list_order():
    p = {"b": [" X ", "a"], "a": {"z": " Claude-Y ", "y": 1}, "n": None}
    c = ls.canonicalize(p)
    assert list(c) == ["a", "b", "n"]
    assert list(c["a"]) == ["y", "z"]
    assert c["a"]["z"] == "Claude-Y"
    assert c["b"] == ["X", "a"]
    assert ls.canonicalize(c) == c


def test_checked_at_alone_never_changes_the_hash():
    a = ls.canonicalize(_payload(fast=_entry(checked_at="2026-10-25T10:00:00")))
    b = ls.canonicalize(_payload(fast=_entry(checked_at="2026-11-01T10:00:00")))
    assert a != b
    assert (reference_data.payload_hash_for(ls.TABLE_NAME, a)
            == reference_data.payload_hash_for(ls.TABLE_NAME, b))
    c = ls.canonicalize(_payload(fast=_entry(effort="medium")))
    assert (reference_data.payload_hash_for(ls.TABLE_NAME, a)
            != reference_data.payload_hash_for(ls.TABLE_NAME, c))


def test_hash_view_does_not_mutate_its_input():
    n = ls.canonicalize(_payload(fast=_entry(checked_at="2026-10-25T10:00:00")))
    ls.hash_view(n)
    assert n["roles"]["fast"]["checked_at"] == "2026-10-25T10:00:00"


# ---- build_snapshot ----
def test_snapshot_unreadable_and_no_row():
    s = ls.build_snapshot("unreadable", None)
    assert s.state == "unreadable" and not s.setting
    assert s.warnings == ("AI model settings could not be read; code defaults in use",)
    s = ls.build_snapshot("no_row", None)
    assert s.state == "no_row" and not s.setting and s.warnings == ()


def test_snapshot_ok_two_roles():
    row = _row(_payload(fast=_entry("claude-haiku-5-5", "medium", CAPS),
                        deep=_entry(llm.SONNET_4_6, None)))
    s = ls.build_snapshot("ok", row)
    assert s.state == "ok" and s.warnings == ()
    assert s.as_of == "2026-10-25" and s.updated_by == "t"
    assert s.setting["fast"].model == "claude-haiku-5-5"
    assert s.setting["fast"].effort == "medium"
    assert s.setting["fast"].capabilities["max_tokens"] == 64000
    assert s.setting["deep"] == llm.RoleSetting(llm.SONNET_4_6, None, None)
    with pytest.raises(TypeError):
        s.setting["x"] = 1
    with pytest.raises(TypeError):
        s.setting["fast"].capabilities["max_tokens"] = 1


def test_snapshot_hash_mismatch_is_invalid():
    row = _row(_payload(fast=_entry()))
    row["payload"]["roles"]["fast"]["model"] = "claude-opus-5-5"  # edited in SQL
    s = ls.build_snapshot("ok", row)
    assert s.state == "invalid" and not s.setting
    assert any("hash" in w for w in s.warnings)


def test_snapshot_missing_hash_is_invalid():
    row = _row(_payload(fast=_entry()))
    del row["payload_hash"]
    assert ls.build_snapshot("ok", row).state == "invalid"


def test_snapshot_bad_version_is_invalid():
    row = _row({"version": 2, "roles": {"fast": _entry()}})
    s = ls.build_snapshot("ok", row)
    assert s.state == "invalid" and not s.setting and s.warnings


def test_snapshot_one_bad_role_dropped_others_kept():
    row = _row(_payload(fast=_entry(), deep=_entry("gpt-4")))
    s = ls.build_snapshot("ok", row)
    assert s.state == "ok" and set(s.setting) == {"fast"}
    assert len(s.warnings) == 1 and s.warnings[0].startswith("deep: ")
    assert s.warnings[0].endswith("; code default in use")


def test_snapshot_pinned_role_in_row_is_invalid():
    row = _row(_payload(score=_entry()))
    assert ls.build_snapshot("ok", row).state == "invalid"


# ---- get_snapshot TTL ----
class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _Loader:
    def __init__(self, result=("no_row", None), exc=None):
        self.n = 0
        self.result = result
        self.exc = exc

    def __call__(self):
        self.n += 1
        if self.exc:
            raise self.exc
        return self.result


def test_snapshot_cached_within_ttl_then_reloaded():
    clock, loader = _Clock(), _Loader()
    ls.get_snapshot(loader=loader, clock=clock)
    clock.t += ls._TTL_SEC - 1
    ls.get_snapshot(loader=loader, clock=clock)
    assert loader.n == 1
    clock.t += 2
    ls.get_snapshot(loader=loader, clock=clock)
    assert loader.n == 2


def test_unreadable_reloaded_after_short_ttl():
    clock, loader = _Clock(), _Loader(("unreadable", None))
    assert ls.get_snapshot(loader=loader, clock=clock).state == "unreadable"
    clock.t += ls._UNREADABLE_TTL_SEC + 1
    assert ls._UNREADABLE_TTL_SEC < ls._TTL_SEC
    ls.get_snapshot(loader=loader, clock=clock)
    assert loader.n == 2


def test_invalidate_forces_reload():
    clock, loader = _Clock(), _Loader()
    ls.get_snapshot(loader=loader, clock=clock)
    ls.invalidate()
    ls.get_snapshot(loader=loader, clock=clock)
    assert loader.n == 2


def test_stale_snapshot_served_while_another_reload_is_in_progress():
    clock, started, release = _Clock(), threading.Event(), threading.Event()
    ls.get_snapshot(loader=_Loader(), clock=clock)           # cache a no_row snapshot
    clock.t += ls._TTL_SEC + 1                                # expire it

    def slow():
        started.set()
        release.wait(5)
        return ("unreadable", None)

    out = []
    t = threading.Thread(target=lambda: out.append(ls.get_snapshot(loader=slow, clock=clock)))
    t.start()
    assert started.wait(5)
    other = _Loader()
    s = ls.get_snapshot(loader=other, clock=clock)            # must not block or load
    assert s.state == "no_row" and other.n == 0
    release.set()
    t.join(5)
    assert out[0].state == "unreadable"


def test_invalidate_during_a_reload_prevents_the_old_read_being_cached():
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return ("unreadable", None)

    out = []
    t = threading.Thread(target=lambda: out.append(ls.get_snapshot(loader=slow, clock=_Clock())))
    t.start()
    assert started.wait(5)
    ls.invalidate()
    release.set()
    t.join(5)
    assert out[0].state == "unreadable"                       # returned to its caller
    assert ls._cached is None                                 # but never stored


def test_first_ever_load_blocks_and_returns_the_loaded_snapshot():
    loader = _Loader()
    assert ls._cached is None
    assert ls.get_snapshot(loader=loader, clock=_Clock()).state == "no_row"
    assert loader.n == 1 and ls._cached is not None


def test_raising_loader_is_unreadable():
    s = ls.get_snapshot(loader=_Loader(exc=RuntimeError("boom")), clock=_Clock())
    assert s.state == "unreadable"


def test_current_setting_none_without_row_and_never_raises(monkeypatch):
    assert ls.current_setting() is None
    monkeypatch.setattr(ls, "get_snapshot", lambda: 1 / 0)
    assert ls.current_setting() is None


def test_current_setting_returns_mapping(monkeypatch):
    row = _row(_payload(fast=_entry()))
    monkeypatch.setattr(ls, "_default_loader", lambda: ("ok", row))
    ls.invalidate()
    assert ls.current_setting()["fast"].model == "claude-haiku-5-5"


def test_resolution_summary_and_never_raises(monkeypatch):
    out = ls.resolution_summary()
    assert out == (f"fast={llm.HAIKU_4_5} (code) · deep={llm.SONNET_4_6} (code) · "
                   f"extract={llm.SONNET_4_6} (code) · settings=no_row")
    monkeypatch.setattr(llm, "resolved_with_source", lambda *a: 1 / 0)
    assert isinstance(ls.resolution_summary(), str)


# ---- decide_save ----
def test_decide_save_ok_with_capabilities():
    entry, errs = ls.decide_save("fast", " claude-haiku-5-5 ", "medium", CAPS, None,
                                 "2026-10-25T10:00:00")
    assert errs == [] and entry["model"] == "claude-haiku-5-5"
    assert entry["capabilities"] == CAPS and entry["effort"] == "medium"


def test_decide_save_not_found_refused():
    entry, errs = ls.decide_save("fast", "claude-haiku-5-5", None, None,
                                 "NotFoundError: model", None,
                                 error_kind=llm.ERR_REJECTED)
    assert entry is None
    assert errs == ["the Models API rejected claude-haiku-5-5 with this API key "
                    "(NotFoundError: model)"]


@pytest.mark.parametrize("err", ["AuthenticationError: 401", "PermissionDeniedError: 403",
                                 "NotFoundError: 404", "RateLimitError: 429",
                                 "no API key"])
def test_decide_save_every_rejection_refused_even_for_a_known_id(err):
    entry, errs = ls.decide_save("fast", "claude-haiku-5-5", None, None, err, None,
                                 error_kind=llm.ERR_REJECTED)
    assert entry is None and "rejected" in errs[0]


@pytest.mark.parametrize("err", ["InternalServerError: 500", "OverloadedError: 529",
                                 "APIConnectionError: down", "APITimeoutError: slow"])
def test_decide_save_unreachable_known_id_allowed_without_capabilities(err):
    entry, errs = ls.decide_save("fast", "claude-haiku-5-5", "low", None, err, None,
                                 error_kind=llm.ERR_UNREACHABLE)
    assert errs == [] and entry["capabilities"] is None


def test_decide_save_error_without_a_kind_fails_closed():
    entry, errs = ls.decide_save("fast", "claude-haiku-5-5", None, None,
                                 "APIConnectionError: down", None)
    assert entry is None and errs


def test_decide_save_strips_effort_and_validates_the_canonical_entry():
    entry, errs = ls.decide_save("fast", "claude-haiku-5-5", " low ", CAPS, None, None)
    assert errs == [] and entry["effort"] == "low"


_REQ = httpx.Request("GET", "https://x")


class _Status(Exception):
    def __init__(self, code):
        super().__init__(str(code))
        self.status_code = code


@pytest.mark.parametrize("exc,kind", [
    (_Status(401), llm.ERR_REJECTED), (_Status(403), llm.ERR_REJECTED),
    (_Status(404), llm.ERR_REJECTED), (_Status(429), llm.ERR_REJECTED),
    (_Status(499), llm.ERR_REJECTED), (_Status(500), llm.ERR_UNREACHABLE),
    (_Status(529), llm.ERR_UNREACHABLE),
    (anthropic.APIConnectionError(request=_REQ), llm.ERR_UNREACHABLE),
    (anthropic.APITimeoutError(request=_REQ), llm.ERR_UNREACHABLE),
    (RuntimeError("what"), llm.ERR_REJECTED),
])
def test_classify_api_error(exc, kind):
    assert llm.classify_api_error(exc) == kind


def test_classify_api_error_bool_status_is_not_a_status():
    class E(Exception):
        status_code = True
    assert llm.classify_api_error(E()) == llm.ERR_REJECTED


def test_decide_save_unreachable_generic_id_refused():
    entry, errs = ls.decide_save("fast", "claude-some-future-9", None, None,
                                 "APIConnectionError: down", None)
    assert entry is None and errs


def test_decide_save_invalid_effort_refused():
    entry, errs = ls.decide_save("fast", "claude-haiku-5-5", "max", CAPS, None, None)
    assert entry is None and errs


# ---- base_payload_for_save ----
_EMPTY = {"version": 1, "roles": {}}


def test_base_payload_no_row_is_empty():
    assert ls.base_payload_for_save("no_row", None) == (_EMPTY, [])


@pytest.mark.parametrize("status,row", [("unreadable", None), ("weird", {}),
                                        ("ok", None), ("ok", [])])
def test_base_payload_unreadable_gives_none_and_a_note(status, row):
    p, notes = ls.base_payload_for_save(status, row)
    assert p is None and len(notes) == 1 and "overwrite" in notes[0]


def test_base_payload_invalid_row_is_replaced_with_a_note():
    row = _row(_payload(fast=_entry()))
    row["payload"]["roles"]["fast"]["model"] = "claude-opus-5-5"   # hash mismatch
    p, notes = ls.base_payload_for_save("ok", row)
    assert p == _EMPTY and len(notes) == 1 and "invalid" in notes[0]
    row = _row({"version": 2, "roles": {}})
    p, notes = ls.base_payload_for_save("ok", row)
    assert p == _EMPTY and len(notes) == 1


def test_base_payload_valid_row_keeps_good_roles_and_drops_ignored_ones():
    good = _entry("claude-haiku-5-5", "low", CAPS)
    row = _row(_payload(fast=good, deep=_entry("gpt-4")))
    p, notes = ls.base_payload_for_save("ok", row)
    assert p == {"version": 1, "roles": {"fast": good}}
    assert len(notes) == 1 and "deep" in notes[0]
    row = _row(_payload(fast=good))
    assert ls.base_payload_for_save("ok", row) == ({"version": 1, "roles": {"fast": good}}, [])


# ---- apply_role / reset_role ----
def test_apply_and_reset_role():
    e = _entry()
    p = ls.apply_role(None, "fast", e)
    assert p == {"version": 1, "roles": {"fast": e}}
    e2 = _entry("claude-sonnet-5-5")
    p2 = ls.apply_role(p, "deep", e2)
    assert set(p2["roles"]) == {"fast", "deep"} and set(p["roles"]) == {"fast"}
    p3 = ls.reset_role(p2, "fast")
    assert set(p3["roles"]) == {"deep"} and set(p2["roles"]) == {"fast", "deep"}
    assert ls.reset_role(None, "fast") == {"version": 1, "roles": {}}


def test_save_invalidates_only_on_saved(monkeypatch):
    ls.get_snapshot(loader=_Loader(), clock=_Clock())
    monkeypatch.setattr(db, "save_reference_table", lambda *a: {"status": "no_change"})
    ls.save(_payload(), "t")
    assert ls._cached is not None
    monkeypatch.setattr(db, "save_reference_table",
                        lambda *a: {"status": "saved", "as_of": "x"})
    assert ls.save(_payload(), "t")["status"] == "saved"
    assert ls._cached is None


# ---- existing tables stay byte-identical ----
_FIXTURE = {"Tech": ["msft", "aapl"], "Energy": ["xom"]}
_FIXTURE_HASH = "efcd2b8aaf86de3dd29ff4075bea8853153b5c2906db048733c4b742d2e41061"


def test_existing_table_canonical_form_and_hash_unchanged():
    c = reference_data.canonicalize(_FIXTURE)
    assert reference_data.canonicalize_for("sector_candidates", _FIXTURE) == c
    old_inline = hashlib.sha256(
        json.dumps(c, sort_keys=True).encode("utf-8")).hexdigest()
    h = reference_data.payload_hash_for("sector_candidates", c)
    assert h == old_inline == _FIXTURE_HASH
    assert reference_data.validate_for_save("sector_candidates", _FIXTURE) == []


def test_llm_models_dispatch_uses_its_own_canonicalizer():
    p = {"version": 1, "roles": {"fast": {"model": " claude-Haiku ", "effort": None}}}
    assert reference_data.canonicalize_for("llm_models", p)["roles"]["fast"]["model"] \
        == "claude-Haiku"
    assert reference_data.validate_for_save("llm_models", {"version": 9})


# ---- db ----
def test_save_llm_models_invalid_payload_writes_nothing(monkeypatch):
    fake = _wire_fake_db(monkeypatch)
    calls = []
    monkeypatch.setattr(db, "_client", lambda: calls.append(1) or fake)
    r = db.save_reference_table("llm_models", _payload(score=_entry()), "t")
    assert r["status"] == "error" and r["detail"].startswith("invalid payload: ")
    assert calls == [] and fake._store == {}


def test_save_llm_models_valid_payload_saves_with_its_hash(monkeypatch):
    fake = _wire_fake_db(monkeypatch)
    p = _payload(fast=_entry(checked_at="2026-10-25T10:00:00"))
    assert db.save_reference_table("llm_models", p, "t")["status"] == "saved"
    row = fake._store["reference_tables"][0]
    assert row["payload_hash"] == reference_data.payload_hash_for(
        "llm_models", ls.canonicalize(p))
    # a re-check (only checked_at differs) is a no-op save
    p2 = _payload(fast=_entry(checked_at="2026-11-01T10:00:00"))
    assert db.save_reference_table("llm_models", p2, "t") == {"status": "no_change"}
    # and the saved row builds an ok snapshot
    assert ls.build_snapshot("ok", row).state == "ok"


def test_load_reference_table_status(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_reference_table_status("x") == ("unreadable", None)
    fake = _wire_fake_db(monkeypatch)
    assert db.load_reference_table_status("") == ("unreadable", None)
    assert db.load_reference_table_status("llm_models") == ("no_row", None)
    fake._store["reference_tables"] = [{"name": "llm_models", "payload": {}}]
    st, row = db.load_reference_table_status("llm_models")
    assert st == "ok" and row["name"] == "llm_models"
    _wire_fake_db(monkeypatch, raise_on_execute=True)
    assert db.load_reference_table_status("llm_models") == ("unreadable", None)
