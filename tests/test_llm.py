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
    ("claude-haiku-5-5", llm.PROFILE_MODERN),
    ("claude-sonnet-5-5", llm.PROFILE_MODERN),
    ("claude-opus-5-5", llm.PROFILE_MODERN),
    ("claude-some-future-9", llm.PROFILE_MODERN)])
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
