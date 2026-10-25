import math
import threading
from types import SimpleNamespace as NS

import pytest

from stock_analyzer import llm


def _resp(blocks, stop="end_turn", usage=True, details=None):
    r = NS(content=blocks, stop_reason=stop)
    if usage:
        r.usage = NS(input_tokens=11, output_tokens=22)
    if details is not None:
        r.stop_details = details
    return r


def _t(s):
    return NS(type="text", text=s)


MSG = [{"role": "user", "content": "hi"}]


# ---- resolve_model / profile_for ----
def test_resolve_model_explicit_wins():
    assert llm.resolve_model("fast", "claude-x") == "claude-x"


def test_resolve_model_empty_explicit_falls_back():
    assert llm.resolve_model("fast", "") == llm.HAIKU_4_5


@pytest.mark.parametrize("role,model", [
    ("fast", llm.HAIKU_4_5), ("filter", llm.HAIKU_4_5), ("score", llm.HAIKU_4_5),
    ("extract_trigger", llm.HAIKU_4_5), ("deep", llm.SONNET_4_6),
    ("extract", llm.SONNET_4_6), ("extract_strong", llm.OPUS_4_8)])
def test_role_defaults(role, model):
    assert llm.resolve_model(role) == model


def test_unknown_role_raises():
    with pytest.raises(ValueError):
        llm.resolve_model("nope")


def test_role_sets():
    assert llm.PINNED_ROLES == {"score", "filter", "extract_trigger"}
    assert llm.JSON_ROLES == llm.ROLES - {"fast", "deep"}
    assert set(llm.CODE_DEFAULTS) == set(llm.ROLES)


@pytest.mark.parametrize("m,p", [
    ("claude-haiku-4-5-20251001", llm.PROFILE_LEGACY),
    ("claude-haiku-4-5", llm.PROFILE_LEGACY),
    ("claude-sonnet-4-6", llm.PROFILE_LEGACY),
    ("claude-opus-4-6", llm.PROFILE_LEGACY),
    ("claude-opus-4-7", llm.PROFILE_OPUS_4X),
    ("claude-opus-4-8", llm.PROFILE_OPUS_4X),
    ("claude-opus-5", llm.PROFILE_OPUS_4X),
    ("claude-sonnet-5", llm.PROFILE_OPUS_4X),
    ("claude-haiku-5-5", llm.PROFILE_KNOWN_MODERN),
    ("claude-sonnet-5-5", llm.PROFILE_KNOWN_MODERN),
    ("claude-opus-5-5", llm.PROFILE_KNOWN_MODERN),
    ("claude-haiku-5-5-20261001", llm.PROFILE_KNOWN_MODERN),
    ("claude-sonnet-5-5-preview", llm.PROFILE_GENERIC),
    ("claude-haiku-5-6", llm.PROFILE_GENERIC),
    ("claude-opus-6", llm.PROFILE_GENERIC),
    ("claude-some-future-9", llm.PROFILE_GENERIC)])
def test_profile_for(m, p):
    assert llm.profile_for(m) == p


# ---- build_request ----
def test_legacy_exact_minimal():
    r = llm.build_request("fast", llm.HAIKU_4_5, max_tokens=300, messages=MSG)
    assert r == {"model": llm.HAIKU_4_5, "max_tokens": 300, "messages": MSG}


def test_legacy_exact_full():
    r = llm.build_request("score", llm.HAIKU_4_5, max_tokens=300, messages=MSG,
                          system="s", temperature=0.2, timeout=30)
    assert r == {"model": llm.HAIKU_4_5, "max_tokens": 300, "messages": MSG,
                 "system": "s", "temperature": 0.2, "timeout": 30}
    assert "thinking" not in r and "output_config" not in r


def test_legacy_sonnet_temperature_zero_forwarded():
    r = llm.build_request("deep", llm.SONNET_4_6, max_tokens=5, messages=MSG,
                          temperature=0)
    assert r["temperature"] == 0 and r["max_tokens"] == 5


def test_opus4x_drops_temperature():
    r = llm.build_request("extract_strong", llm.OPUS_4_8, max_tokens=300,
                          messages=MSG, temperature=0.5, system="s")
    assert r == {"model": llm.OPUS_4_8, "max_tokens": 300, "messages": MSG,
                 "system": "s"}


def test_modern_json_haiku():
    r = llm.build_request("score", "claude-haiku-5-5", max_tokens=300,
                          messages=MSG, temperature=0.3)
    assert "temperature" not in r
    assert r["output_config"] == {"effort": "low"}
    assert r["thinking"] == {"type": "disabled"}
    assert r["max_tokens"] == math.ceil(300 * 1.3) == 390


def test_modern_json_sonnet():
    r = llm.build_request("extract", "claude-sonnet-5-5", max_tokens=300,
                          messages=MSG)
    assert r["thinking"] == {"type": "between_tools"}


def test_modern_json_opus_no_thinking():
    r = llm.build_request("extract_strong", "claude-opus-5-5", max_tokens=300,
                          messages=MSG)
    assert "thinking" not in r and r["output_config"] == {"effort": "low"}


def test_modern_narrative():
    r = llm.build_request("deep", "claude-sonnet-5-5", max_tokens=300,
                          messages=MSG, timeout=9)
    assert "thinking" not in r
    assert r["max_tokens"] == 390 + 2048
    assert r["timeout"] == 9


# ---- parse_response ----
def test_text_after_thinking_block():
    r = llm.parse_response("fast", _resp([NS(type="thinking", thinking="x"), _t("hello")]), "m")
    assert r.text == "hello" and r.ok and r.error is None
    assert r.usage == {"input_tokens": 11, "output_tokens": 22}
    assert r.model == "m"


@pytest.mark.parametrize("role", ["fast", "score"])
@pytest.mark.parametrize("txt", ["", "   ", "\n\t"])
def test_empty_text_is_no_reply(role, txt):
    r = llm.parse_response(role, _resp([_t(txt)]), "m")
    assert r.text is None and not r.ok
    assert r.error.startswith("no text in reply (empty text")


def test_empty_block_then_real_text():
    r = llm.parse_response("fast", _resp([_t(""), _t("hello")]), "m")
    assert r.text == "hello"


def test_whitespace_text_kept_unstripped():
    r = llm.parse_response("fast", _resp([_t("  hi \n")]), "m")
    assert r.text == "  hi \n"


@pytest.mark.parametrize("role,flag,want", [
    ("fast", None, False), ("deep", None, False), ("fast", True, True),
    ("deep", True, True), ("fast", False, False), ("score", None, True),
    ("score", True, True),
])
def test_json_mode_table(role, flag, want):
    assert llm.json_mode(role, flag) is want


@pytest.mark.parametrize("role,flag", [
    ("score", False), ("extract", False), ("fast", 1), ("fast", 0), ("fast", "yes"),
])
def test_json_mode_rejects(role, flag):
    with pytest.raises(ValueError):
        llm.json_mode(role, flag)


@pytest.mark.parametrize("role", ["fast", "deep"])
def test_expects_json_drops_cut_off_reply(role):
    r = llm.parse_response(role, _resp([_t("{")], stop="max_tokens"), "m",
                           expects_json=True)
    assert r.text is None and r.truncated and r.error == "reply cut off at max_tokens"


def test_expects_json_drops_odd_stop():
    r = llm.parse_response("fast", _resp([_t("x")], stop="pause_turn"), "m",
                           expects_json=True)
    assert r.text is None and "pause_turn" in r.error


def test_expects_json_builds_json_shape():
    r = llm.build_request("fast", "claude-haiku-5-5", max_tokens=300, messages=MSG,
                          expects_json=True)
    assert r["thinking"] == {"type": "disabled"}
    assert r["max_tokens"] == math.ceil(300 * 1.3)
    r = llm.build_request("deep", "claude-sonnet-5-5", max_tokens=300, messages=MSG,
                          expects_json=True)
    assert r["thinking"] == {"type": "between_tools"}


def test_expects_json_legacy_byte_identical():
    a = llm.build_request("fast", llm.HAIKU_4_5, max_tokens=300, messages=MSG,
                          temperature=0, expects_json=True)
    b = llm.build_request("fast", llm.HAIKU_4_5, max_tokens=300, messages=MSG,
                          temperature=0)
    assert a == b


def test_complete_invalid_flag_returns_error_no_client():
    f = _Factory(_resp([_t("x")]))
    r = llm.complete("score", api_key="k", messages=MSG, max_tokens=5,
                     expects_json=False, client_factory=f)
    assert not r.ok and "ValueError" in r.error and f.calls == []


def test_complete_expects_json_drops_cut_off():
    f = _Factory(response=_resp([_t("{")], stop="max_tokens"))
    r = llm.complete("fast", api_key="k", messages=MSG, max_tokens=5,
                     expects_json=True, client_factory=f)
    assert r.text is None and r.truncated


def test_refusal_with_text_is_none():
    r = llm.parse_response("deep", _resp([_t("sorry")], stop="refusal"), "m")
    assert r.text is None and r.error == "refused"
    r = llm.parse_response("deep", _resp([_t("sorry")], stop="refusal",
                                         details=NS(category="cyber")), "m")
    assert r.error == "refused (cyber)"


def test_max_tokens_narrative_keeps_text():
    r = llm.parse_response("deep", _resp([_t("part")], stop="max_tokens"), "m")
    assert r.text == "part" and r.truncated and r.ok


def test_max_tokens_json_drops_text():
    r = llm.parse_response("score", _resp([_t("{")], stop="max_tokens"), "m")
    assert r.text is None and r.truncated and r.error == "reply cut off at max_tokens"


def test_unknown_stop_reason_per_kind():
    j = llm.parse_response("filter", _resp([_t("x")], stop="pause_turn"), "m")
    assert j.text is None and "pause_turn" in j.error
    n = llm.parse_response("fast", _resp([_t("x")], stop="pause_turn"), "m")
    assert n.text == "x" and n.error is None


@pytest.mark.parametrize("stop", [None, "end_turn", "stop_sequence"])
def test_normal_stops(stop):
    assert llm.parse_response("score", _resp([_t("x")], stop=stop), "m").text == "x"


def test_no_text_block():
    r = llm.parse_response("fast", _resp([NS(type="thinking")], stop="end_turn"), "m")
    assert r.text is None and "no text in reply" in r.error and "end_turn" in r.error


def test_no_usage():
    assert llm.parse_response("fast", _resp([_t("x")], usage=False), "m").usage is None


# ---- complete ----
class _Factory:
    def __init__(self, response=None, exc=None):
        self.calls = []
        self.requests = []
        self.beta_requests = []
        self.response = response
        self.exc = exc

    def __call__(self, **kw):
        self.calls.append(kw)
        outer = self

        class C:
            class messages:
                @staticmethod
                def create(**req):
                    outer.requests.append(req)
                    if outer.exc:
                        raise outer.exc
                    return outer.response

            class beta:
                class messages:
                    @staticmethod
                    def create(**req):
                        outer.beta_requests.append(req)
                        if outer.exc:
                            raise outer.exc
                        return outer.response
        return C()


def test_no_key_constructs_nothing():
    f = _Factory(_resp([_t("x")]))
    r = llm.complete("fast", api_key="", messages=MSG, max_tokens=5, client_factory=f)
    assert r.error == "no API key" and not r.ok and f.calls == []


def test_exception_redacts_key():
    f = _Factory(exc=RuntimeError("bad key sk-secret-123 here"))
    r = llm.complete("fast", api_key="sk-secret-123", messages=MSG, max_tokens=5,
                     client_factory=f)
    assert not r.ok
    assert "sk-secret-123" not in r.error and "***REDACTED***" in r.error
    assert r.error.startswith("RuntimeError: ")


def test_error_truncated():
    f = _Factory(exc=RuntimeError("x" * 1000))
    r = llm.complete("fast", api_key="k", messages=MSG, max_tokens=5, client_factory=f)
    assert len(r.error) == 300


def test_forwards_client_kwargs_and_timeout():
    f = _Factory(_resp([_t("ok")]))
    r = llm.complete("deep", api_key="k", messages=MSG, max_tokens=50, system="s",
                     timeout=7, client_kwargs={"max_retries": 0, "timeout": 3},
                     client_factory=f)
    assert r.text == "ok"
    assert f.calls == [{"api_key": "k", "max_retries": 0, "timeout": 3}]
    assert f.requests == [{"model": llm.SONNET_4_6, "max_tokens": 50,
                           "messages": MSG, "system": "s", "timeout": 7}]


def test_default_factory_is_anthropic(monkeypatch):
    import anthropic
    f = _Factory(_resp([_t("ok")]))
    monkeypatch.setattr(anthropic, "Anthropic", f)
    assert llm.complete("fast", api_key="k", messages=MSG, max_tokens=5).text == "ok"
    assert f.calls == [{"api_key": "k"}]


def test_concurrent_calls_independent():
    out = {}
    barrier = threading.Barrier(2)

    def run(name):
        f = _Factory(_resp([_t(name)]))
        barrier.wait()
        out[name] = llm.complete("fast", api_key="k", messages=MSG, max_tokens=5,
                                 client_factory=f)

    ts = [threading.Thread(target=run, args=(n,)) for n in ("a", "b")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out["a"].text == "a" and out["b"].text == "b"


# ---- resolve_anthropic_key ----
def _getter(d):
    return lambda section, key: d[(section, key)]


def test_key_env_wins():
    g = _getter({("anthropic", "api_key"): "sec"})
    assert llm.resolve_anthropic_key(g, {"ANTHROPIC_API_KEY": " envk "}) == "envk"


def test_key_nested_then_flat():
    assert llm.resolve_anthropic_key(_getter({("anthropic", "api_key"): "n"}), {}) == "n"
    assert llm.resolve_anthropic_key(_getter({(None, "ANTHROPIC_API_KEY"): "f"}), {}) == "f"


def test_key_raising_getter():
    def g(s, k):
        raise RuntimeError("no secrets.toml")
    assert llm.resolve_anthropic_key(g, {}) == ""
    assert llm.resolve_anthropic_key(g, {"ANTHROPIC_API_KEY": "e"}) == "e"


def test_key_whitespace_only():
    g = _getter({("anthropic", "api_key"): "  ", (None, "ANTHROPIC_API_KEY"): ""})
    assert llm.resolve_anthropic_key(g, {"ANTHROPIC_API_KEY": "   "}) == ""
    assert llm.resolve_anthropic_key(None, {}) == ""


def test_gate_list_contains_llm():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "pre_tool_checks.py"
    spec = importlib.util.spec_from_file_location("_ptc_llm", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert "stock_analyzer/llm.py" in m._GATE_FILES


# ---- review fixes (P1-b round 1) ----
def test_opus_5_request_is_forwarded_unchanged():
    """Opus 5 is in use today (Investigator's eval-passed menu); routing it
    through the helper must not add effort or token headroom."""
    msgs = [{"role": "user", "content": "q"}]
    r = llm.build_request("deep", "claude-opus-5", max_tokens=4000, messages=msgs,
                          system="s", temperature=0.2, timeout=90)
    assert r == {"model": "claude-opus-5", "max_tokens": 4000, "messages": msgs,
                 "system": "s", "timeout": 90}


@pytest.mark.parametrize("role", sorted(llm.PINNED_ROLES))
def test_pinned_role_rejects_a_different_model(role):
    with pytest.raises(ValueError):
        llm.resolve_model(role, "claude-haiku-5-5")


@pytest.mark.parametrize("role", sorted(llm.PINNED_ROLES))
def test_pinned_role_accepts_its_own_default(role):
    assert llm.resolve_model(role, llm.CODE_DEFAULTS[role]) == llm.CODE_DEFAULTS[role]


@pytest.mark.parametrize("role", sorted(llm.ROLES - llm.PINNED_ROLES))
def test_unpinned_role_accepts_an_explicit_model(role):
    assert llm.resolve_model(role, "claude-x") == "claude-x"


def test_complete_on_pinned_override_errors_without_calling():
    made = []

    def factory(**kw):
        made.append(kw)
        raise AssertionError("client must not be constructed")

    res = llm.complete("score", api_key="sk-test-key-123", model="claude-haiku-5-5",
                       messages=[{"role": "user", "content": "x"}], max_tokens=10,
                       client_factory=factory)
    assert res.text is None and "pinned" in (res.error or "")
    assert made == []


@pytest.mark.parametrize("key", [12345678, b"bytes-key-123", "abc"])
def test_complete_error_path_never_raises_on_odd_keys(key):
    def factory(**kw):
        raise RuntimeError("boom abc boom")

    res = llm.complete("fast", api_key=key, messages=[{"role": "user", "content": "x"}],
                       max_tokens=10, client_factory=factory)
    assert res.text is None and res.error.startswith("RuntimeError")
    if key == "abc":   # a key shorter than 6 characters is not used for redaction
        assert "abc" in res.error


def test_complete_keeps_the_http_status_of_an_sdk_error():
    class _StatusErr(Exception):
        status_code = 529

    class _Msgs:
        def create(self, **kw):
            raise _StatusErr("overloaded_error")

    class _Client:
        def __init__(self, **kw):
            self.messages = _Msgs()

    r = llm.complete(llm.ROLE_FAST, api_key="sk-test-123456", max_tokens=10,
                     messages=[{"role": "user", "content": "x"}], client_factory=_Client)
    assert not r.ok and r.status_code == 529
    assert "overloaded_error" in r.error


def test_complete_status_code_is_none_without_an_int_status():
    class _Msgs:
        def create(self, **kw):
            raise RuntimeError("boom")

    class _Client:
        def __init__(self, **kw):
            self.messages = _Msgs()

    r = llm.complete(llm.ROLE_FAST, api_key="sk-test-123456", max_tokens=10,
                     messages=[{"role": "user", "content": "x"}], client_factory=_Client)
    assert r.status_code is None


def test_complete_status_code_rejects_a_bool():
    class _BoolErr(Exception):
        status_code = True

    class _Msgs:
        def create(self, **kw):
            raise _BoolErr("x")

    class _Client:
        def __init__(self, **kw):
            self.messages = _Msgs()

    r = llm.complete(llm.ROLE_FAST, api_key="sk-test-123456", max_tokens=10,
                     messages=[{"role": "user", "content": "x"}], client_factory=_Client)
    assert r.status_code is None


# ---- Phase 2 (P2-a2): resolution with source ----
RS = llm.RoleSetting


def test_resolve_code_default_with_source():
    assert llm.resolve_model_with_source("fast", env={}) == (llm.HAIKU_4_5, "code")


def test_resolve_explicit_beats_env_and_setting():
    got = llm.resolve_model_with_source(
        "fast", "claude-a", setting={"fast": RS("claude-s")},
        env={"DRISHTA_LLM_MODEL_FAST": "claude-e"})
    assert got == ("claude-a", "explicit")


def test_resolve_env_beats_setting():
    got = llm.resolve_model_with_source(
        "fast", setting={"fast": RS("claude-s")},
        env={"DRISHTA_LLM_MODEL_FAST": " claude-e "})
    assert got == ("claude-e", "env")


def test_resolve_setting_beats_code():
    got = llm.resolve_model_with_source("deep", setting={"deep": RS("claude-s")}, env={})
    assert got == ("claude-s", "setting")


@pytest.mark.parametrize("bad", ["  ", "gpt-4", ""])
def test_resolve_bad_env_ignored(bad):
    got = llm.resolve_model_with_source(
        "fast", setting={"fast": RS("claude-s")}, env={"DRISHTA_LLM_MODEL_FAST": bad})
    assert got == ("claude-s", "setting")
    assert llm.resolve_model_with_source(
        "fast", env={"DRISHTA_LLM_MODEL_FAST": bad}) == (llm.HAIKU_4_5, "code")


@pytest.mark.parametrize("entry", ["claude-s", RS("gpt-4"), {"model": "claude-s"}, None])
def test_resolve_bad_setting_entry_ignored(entry):
    got = llm.resolve_model_with_source("fast", setting={"fast": entry}, env={})
    assert got == (llm.HAIKU_4_5, "code")


@pytest.mark.parametrize("role", sorted(llm.PINNED_ROLES))
def test_pinned_role_ignores_env_and_setting(role):
    got = llm.resolve_model_with_source(
        role, setting={role: RS("claude-s")},
        env={"DRISHTA_LLM_MODEL_" + role.upper(): "claude-e"})
    assert got == (llm.CODE_DEFAULTS[role], "code")
    with pytest.raises(ValueError):
        llm.resolve_model_with_source(role, "claude-other", env={})


def test_extract_strong_ignores_env_and_setting_explicit_wins():
    env = {"DRISHTA_LLM_MODEL_EXTRACT_STRONG": "claude-e"}
    st = {"extract_strong": RS("claude-s")}
    assert llm.resolve_model_with_source("extract_strong", setting=st, env=env) == (
        llm.OPUS_4_8, "code")
    assert llm.resolve_model_with_source("extract_strong", "claude-x", setting=st, env=env) == (
        "claude-x", "explicit")


def test_resolve_model_returns_the_string():
    assert llm.resolve_model("deep", setting={"deep": RS("claude-s")}, env={}) == "claude-s"


def test_resolve_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("DRISHTA_LLM_MODEL_FAST", "claude-x")
    assert llm.resolve_model_with_source("fast") == ("claude-x", "env")


def test_editable_roles():
    assert llm.EDITABLE_ROLES == {"fast", "deep", "extract"}


# ---- Phase 2: build_request ----
def test_known_modern_effort_passthrough_and_default():
    r = llm.build_request("fast", "claude-haiku-5-5", max_tokens=300, messages=MSG,
                          effort="medium")
    assert r["output_config"] == {"effort": "medium"}
    r = llm.build_request("fast", "claude-haiku-5-5", max_tokens=300, messages=MSG)
    assert r["output_config"] == {"effort": "low"}


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-some-future-9"])
def test_effort_above_high_rejected(model):
    with pytest.raises(ValueError):
        llm.build_request("fast", model, max_tokens=5, messages=MSG, effort="max")


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-some-future-9"])
def test_effort_not_in_capabilities_rejected(model):
    with pytest.raises(ValueError):
        llm.build_request("fast", model, max_tokens=5, messages=MSG, effort="high",
                          capabilities={"efforts": ["low", "medium"]})


def test_generic_shape():
    caps = {"efforts": ["low", "medium"], "max_tokens": 100000}
    r = llm.build_request("fast", "claude-some-future-9", max_tokens=300, messages=MSG,
                          temperature=0.3, capabilities=caps, effort="medium")
    assert "thinking" not in r and "temperature" not in r
    assert r["output_config"] == {"effort": "medium"}
    assert r["max_tokens"] == 390 + 2048
    # default effort low is listed
    r = llm.build_request("fast", "claude-some-future-9", max_tokens=300, messages=MSG,
                          capabilities=caps)
    assert r["output_config"] == {"effort": "low"}
    # not listed / no capabilities -> no output_config
    r = llm.build_request("fast", "claude-some-future-9", max_tokens=300, messages=MSG,
                          capabilities={"efforts": []})
    assert "output_config" not in r
    r = llm.build_request("fast", "claude-some-future-9", max_tokens=300, messages=MSG)
    assert "output_config" not in r


def test_generic_headroom_on_json_role():
    r = llm.build_request("score", "claude-some-future-9", max_tokens=300, messages=MSG)
    assert r["max_tokens"] == 390 + 2048 and "thinking" not in r


def test_generic_clamps_to_capabilities_max_tokens():
    r = llm.build_request("extract", "claude-some-future-9", max_tokens=8000, messages=MSG,
                          capabilities={"efforts": [], "max_tokens": 4096})
    assert r["max_tokens"] == 4096


def test_known_modern_clamp():
    r = llm.build_request("deep", "claude-sonnet-5-5", max_tokens=300, messages=MSG,
                          capabilities={"efforts": ["low"], "max_tokens": 1000})
    assert r["max_tokens"] == 1000


def test_legacy_ignores_effort_and_capabilities():
    a = llm.build_request("fast", llm.HAIKU_4_5, max_tokens=300, messages=MSG, temperature=0)
    b = llm.build_request("fast", llm.HAIKU_4_5, max_tokens=300, messages=MSG, temperature=0,
                          effort="max", capabilities={"efforts": [], "max_tokens": 1})
    assert a == b
    a = llm.build_request("deep", "claude-opus-5", max_tokens=300, messages=MSG)
    b = llm.build_request("deep", "claude-opus-5", max_tokens=300, messages=MSG,
                          effort="medium", capabilities={"efforts": [], "max_tokens": 1})
    assert a == b


@pytest.mark.parametrize("model,has", [
    ("claude-sonnet-5-5", True), ("claude-opus-5-5", True),
    ("claude-sonnet-5-5-20261001", True),
    ("claude-haiku-5-5", False), (llm.HAIKU_4_5, False), (llm.SONNET_4_6, False),
    ("claude-opus-4-8", False), ("claude-opus-5", False),
    ("claude-sonnet-5-5-preview", False), ("claude-some-future-9", False)])
def test_fallback_fields(model, has):
    r = llm.build_request("deep", model, max_tokens=5, messages=MSG)
    assert llm.uses_fallback(model) is has
    if has:
        assert r["betas"] == [llm.FALLBACK_BETA] and r["fallbacks"] == "default"
    else:
        assert "betas" not in r and "fallbacks" not in r


# ---- Phase 2: parse_response ----
def test_parse_served_model_and_requested():
    resp = _resp([_t("x")])
    resp.model = "claude-sonnet-5-5-20261001"
    r = llm.parse_response("fast", resp, "claude-sonnet-5-5")
    assert r.model == "claude-sonnet-5-5-20261001"
    assert r.requested_model == "claude-sonnet-5-5"
    assert r.fell_back is False


def test_parse_no_response_model_uses_passed():
    r = llm.parse_response("fast", _resp([_t("x")]), "m")
    assert r.model == "m" and r.requested_model == "m"


def test_parse_fell_back_via_block():
    r = llm.parse_response("fast", _resp([NS(type="fallback"), _t("served")]), "m")
    assert r.fell_back and r.text == "served"


def test_parse_fell_back_via_usage_iterations():
    resp = _resp([_t("x")])
    resp.usage.iterations = [NS(type="message"), NS(type="fallback_message")]
    assert llm.parse_response("fast", resp, "m").fell_back is True
    resp.usage.iterations = [NS(type="message")]
    assert llm.parse_response("fast", resp, "m").fell_back is False


def test_text_before_fallback_block_is_never_returned():
    r = llm.parse_response(
        "deep", _resp([_t("declined part"), NS(type="fallback"), _t("served")]), "m")
    assert r.text == "served"
    r = llm.parse_response("deep", _resp([_t("declined"), NS(type="fallback")]), "m")
    assert r.text is None and not r.ok and r.fell_back


def test_json_mode_unknown_role_fails_closed():
    assert llm.json_mode("nope", None) is True
    with pytest.raises(ValueError):
        llm.json_mode("nope", False)


# ---- Phase 2: complete (beta path, setting) ----
def test_complete_known_sonnet_uses_beta_client():
    f = _Factory(_resp([_t("ok")]))
    r = llm.complete("deep", api_key="k", messages=MSG, max_tokens=50,
                     model="claude-sonnet-5-5", client_factory=f)
    assert r.ok and r.model_source == "explicit" and r.requested_model == "claude-sonnet-5-5"
    assert f.requests == [] and len(f.beta_requests) == 1
    assert f.beta_requests[0]["betas"] == [llm.FALLBACK_BETA]
    assert f.beta_requests[0]["fallbacks"] == "default"


def test_complete_legacy_uses_plain_client():
    f = _Factory(_resp([_t("ok")]))
    r = llm.complete("fast", api_key="k", messages=MSG, max_tokens=50, client_factory=f)
    assert r.model_source == "code" and f.beta_requests == [] and len(f.requests) == 1


def test_complete_haiku_5_5_uses_plain_client():
    f = _Factory(_resp([_t("ok")]))
    llm.complete("fast", api_key="k", messages=MSG, max_tokens=50,
                 model="claude-haiku-5-5", client_factory=f)
    assert f.beta_requests == [] and len(f.requests) == 1


def test_complete_setting_passes_effort():
    f = _Factory(_resp([_t("ok")]))
    r = llm.complete("fast", api_key="k", messages=MSG, max_tokens=50, client_factory=f,
                     setting={"fast": RS("claude-haiku-5-5", "medium")})
    assert r.model_source == "setting"
    assert f.requests[0]["output_config"] == {"effort": "medium"}


def test_complete_explicit_ignores_setting_effort():
    f = _Factory(_resp([_t("ok")]))
    llm.complete("fast", api_key="k", messages=MSG, max_tokens=50, client_factory=f,
                 model="claude-haiku-5-5",
                 setting={"fast": RS("claude-haiku-5-5", "high")})
    assert f.requests[0]["output_config"] == {"effort": "low"}


def test_complete_error_result_carries_source():
    f = _Factory(exc=RuntimeError("boom"))
    r = llm.complete("deep", api_key="k", messages=MSG, max_tokens=5, client_factory=f)
    assert r.requested_model == llm.SONNET_4_6 and r.model_source == "code"


# ---- Phase 2: Models API helpers ----
def _full_info():
    def sup(v):
        return NS(supported=v)
    effort = NS(supported=True, low=sup(True), medium=sup(True), high=sup(True),
                xhigh=sup(False), max=sup(True))
    caps = NS(effort=effort,
              thinking=NS(types=NS(adaptive=sup(True), enabled=sup(False))),
              structured_outputs=sup(True))
    return NS(capabilities=caps, max_tokens=64000, max_input_tokens=200000)


def test_normalize_capabilities_full():
    assert llm.normalize_capabilities(_full_info()) == {
        "efforts": ["low", "medium", "high", "max"],
        "thinking_types": ["adaptive"],
        "structured_outputs": True,
        "max_tokens": 64000,
        "max_input_tokens": 200000,
    }


def test_normalize_capabilities_missing_everything():
    assert llm.normalize_capabilities(NS()) == {
        "efforts": [], "thinking_types": [], "structured_outputs": False,
        "max_tokens": None, "max_input_tokens": None}
    assert llm.normalize_capabilities(None)["efforts"] == []
    info = NS(capabilities=NS(effort=NS(supported=False, low=NS(supported=True))))
    assert llm.normalize_capabilities(info)["efforts"] == []


class _ModelsFactory:
    def __init__(self, info=None, ids=(), exc=None):
        self.calls = []
        self.info, self.ids, self.exc = info, ids, exc

    def __call__(self, **kw):
        self.calls.append(kw)
        outer = self

        class C:
            class models:
                @staticmethod
                def retrieve(mid):
                    if outer.exc:
                        raise outer.exc
                    return outer.info

                @staticmethod
                def list(limit=None):
                    if outer.exc:
                        raise outer.exc
                    return iter(NS(id=i) for i in outer.ids)
        return C()


def test_retrieve_model_ok_error_nokey():
    f = _ModelsFactory(info=_full_info())
    caps, err = llm.retrieve_model("k", "claude-x", client_factory=f)
    assert err is None and caps["max_tokens"] == 64000
    assert f.calls == [{"api_key": "k", "timeout": 10}]
    f = _ModelsFactory(exc=RuntimeError("bad key sk-secret-123 here"))
    caps, err = llm.retrieve_model("sk-secret-123", "claude-x", client_factory=f)
    assert caps is None and "sk-secret-123" not in err and "***REDACTED***" in err
    f = _ModelsFactory()
    assert llm.retrieve_model("", "claude-x", client_factory=f) == (None, "no API key")
    assert f.calls == []


def test_list_model_ids_ok_error_nokey():
    f = _ModelsFactory(ids=["claude-a", "claude-b"])
    assert llm.list_model_ids("k", client_factory=f) == (["claude-a", "claude-b"], None)
    f = _ModelsFactory(exc=RuntimeError("bad key sk-secret-123 here"))
    ids, err = llm.list_model_ids("sk-secret-123", client_factory=f)
    assert ids is None and "sk-secret-123" not in err
    assert llm.list_model_ids("", client_factory=_ModelsFactory()) == (None, "no API key")


# ---- Phase 2: price table ----
def test_approx_cost_usd():
    u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert llm.approx_cost_usd("claude-opus-5-5", u) == pytest.approx(24.0)
    assert llm.approx_cost_usd("claude-haiku-5-5-20261001", u) == pytest.approx(0.6)
    assert llm.approx_cost_usd(llm.HAIKU_4_5, u) == pytest.approx(6.0)
    assert llm.approx_cost_usd("claude-unknown-9", u) is None
    assert llm.approx_cost_usd("claude-opus-5-5", {"input_tokens": None, "output_tokens": 1}) is None
    assert llm.approx_cost_usd("claude-opus-5-5", None) is None
    with pytest.raises(TypeError):
        llm.PRICES_PER_MTOK["x"] = (1, 1)


# ---- Phase 2 (P2-b): provider, served model after a fallback ----
def _patch_provider(monkeypatch, setting):
    from stock_analyzer import llm_settings
    calls = []

    def fake():
        calls.append(1)
        return setting
    monkeypatch.setattr(llm_settings, "current_setting", fake)
    return calls


def test_complete_uses_provider_setting(monkeypatch):
    _patch_provider(monkeypatch, {"fast": RS("claude-haiku-5-5", "medium")})
    f = _Factory(_resp([_t("ok")]))
    r = llm.complete("fast", api_key="k", messages=MSG, max_tokens=50, client_factory=f)
    assert r.ok and r.model_source == "setting"
    req = f.requests[0]
    assert req["model"] == "claude-haiku-5-5"
    assert req["output_config"] == {"effort": "medium"}


@pytest.mark.parametrize("role,model", [("score", None), ("extract_strong", None),
                                        ("filter", None), ("fast", "claude-x")])
def test_provider_not_consulted(monkeypatch, role, model):
    calls = _patch_provider(monkeypatch, {"fast": RS("claude-haiku-5-5")})
    f = _Factory(_resp([_t("ok")]))
    llm.complete(role, api_key="k", messages=MSG, max_tokens=50, model=model,
                 client_factory=f)
    assert calls == []
    assert llm.resolved(role, model) is not None
    assert calls == []


def test_resolved_follows_setting_but_not_for_pinned(monkeypatch):
    _patch_provider(monkeypatch, {"fast": RS("claude-haiku-5-5"),
                                  "score": RS("claude-haiku-5-5")})
    assert llm.resolved("fast") == "claude-haiku-5-5"
    assert llm.resolved_with_source("fast") == ("claude-haiku-5-5", "setting")
    assert llm.resolved("score") == llm.HAIKU_4_5
    assert llm.resolved("fast", "claude-x") == "claude-x"


def test_provider_exception_means_code_default(monkeypatch):
    from stock_analyzer import llm_settings
    monkeypatch.setattr(llm_settings, "current_setting", lambda: 1 / 0)
    assert llm.resolved_with_source("fast") == (llm.HAIKU_4_5, "code")


def test_complete_env_source(monkeypatch):
    monkeypatch.setenv("DRISHTA_LLM_MODEL_FAST", "claude-haiku-5-5")
    f = _Factory(_resp([_t("ok")]))
    r = llm.complete("fast", api_key="k", messages=MSG, max_tokens=50, client_factory=f)
    assert r.model_source == "env" and f.requests[0]["model"] == "claude-haiku-5-5"


def test_setting_with_effort_max_is_an_error_result_not_a_raise():
    f = _Factory(_resp([_t("ok")]))
    r = llm.complete("fast", api_key="k", messages=MSG, max_tokens=50, client_factory=f,
                     setting={"fast": RS("claude-haiku-5-5", "max")})
    assert not r.ok and r.error and r.model_source == "setting"
    assert f.calls == []


def test_setting_capabilities_clamp_end_to_end():
    f = _Factory(_resp([_t("ok")]))
    llm.complete("fast", api_key="k", messages=MSG, max_tokens=800, client_factory=f,
                 setting={"fast": RS("claude-haiku-5-5", "low", {"max_tokens": 500})})
    assert f.requests[0]["max_tokens"] == 500


def test_served_model_after_fallback_prefers_iteration_model():
    resp = _resp([NS(type="fallback", to=NS(model="claude-block")), _t("served")])
    resp.model = "claude-requested"
    resp.usage.iterations = [NS(type="message", model="claude-requested"),
                             NS(type="fallback_message", model="claude-iter")]
    r = llm.parse_response("deep", resp, "claude-requested")
    assert r.fell_back and r.model == "claude-iter"
    assert r.requested_model == "claude-requested"


def test_served_model_after_fallback_uses_block_when_no_iterations():
    resp = _resp([NS(type="fallback", to=NS(model="claude-b1")),
                  NS(type="fallback", to=NS(model="claude-b2")), _t("served")])
    resp.model = "claude-requested"
    r = llm.parse_response("deep", resp, "claude-requested")
    assert r.fell_back and r.model == "claude-b2"


def test_served_model_after_fallback_with_nothing_else_is_response_model():
    resp = _resp([NS(type="fallback"), _t("served")])
    resp.model = "claude-requested"
    assert llm.parse_response("deep", resp, "m").model == "claude-requested"


def test_no_fallback_ignores_iteration_model():
    resp = _resp([_t("x")])
    resp.model = "claude-top"
    resp.usage.iterations = [NS(type="message", model="claude-other")]
    assert llm.parse_response("deep", resp, "m").model == "claude-top"
