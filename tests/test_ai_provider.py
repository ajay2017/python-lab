"""
Tests for stock_analyzer/ai_provider.py (shared multi-provider LLM registry +
dispatch, extracted for 🔎 Portfolio Investigator — see
docs/plans/portfolio-investigator.md).

Fake-module helpers mirror tests/test_portfolio_qa.py's
`_install_fake_anthropic` (itself mirroring tests/test_news_intelligence.py)
so the real success path is exercised, not just the except-fallback path.
"""
import sys
import types

import pytest

from stock_analyzer.ai_provider import (
    AI_PROVIDERS,
    capable_models,
    resolve_key,
    call_llm,
)

pytestmark = pytest.mark.fast


# ── fake anthropic module (mirrors test_portfolio_qa.py) ────────────────────

class _FakeAnthBlock:
    def __init__(self, text):
        self.text = text


class _FakeAnthResponse:
    def __init__(self, text):
        self.content = [_FakeAnthBlock(text)]


class _FakeAnthMessages:
    def __init__(self, response_text=None, raise_exc=None):
        self._response_text = response_text
        self._raise_exc = raise_exc

    def create(self, **kwargs):
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeAnthResponse(self._response_text)


class _FakeAnthClient:
    def __init__(self, response_text=None, raise_exc=None, **kwargs):
        self.messages = _FakeAnthMessages(response_text, raise_exc)


def _install_fake_anthropic(response_text=None, raise_exc=None):
    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = lambda **kwargs: _FakeAnthClient(response_text, raise_exc)
    sys.modules["anthropic"] = fake_mod


class _FakeAnthThinkingBlock:
    """A block with no .text attribute at all -- matches the real SDK's
    ThinkingBlock/RedactedThinkingBlock shape closely enough to reproduce the
    live claude-opus-5 crash (content[0] was a thinking block, not text)."""


def _install_fake_anthropic_with_leading_thinking_block(response_text):
    class _Response:
        def __init__(self, text):
            self.content = [_FakeAnthThinkingBlock(), _FakeAnthBlock(text)]

    class _Messages:
        def create(self, **kwargs):
            return _Response(response_text)

    class _Client:
        def __init__(self, **kwargs):
            self.messages = _Messages()

    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = lambda **kwargs: _Client()
    sys.modules["anthropic"] = fake_mod


# ── fake openai module (covers both "OpenAI" and "Groq (Free tier)", which
#    both dispatch through the openai SDK shape) ─────────────────────────────

class _FakeOAIMessage:
    def __init__(self, content):
        self.content = content


class _FakeOAIChoice:
    def __init__(self, content):
        self.message = _FakeOAIMessage(content)


class _FakeOAIChatCompletion:
    def __init__(self, content):
        self.choices = [_FakeOAIChoice(content)]


class _FakeOAICompletions:
    def __init__(self, response_text=None, raise_exc=None):
        self._response_text = response_text
        self._raise_exc = raise_exc

    def create(self, **kwargs):
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeOAIChatCompletion(self._response_text)


class _FakeOAIChat:
    def __init__(self, response_text=None, raise_exc=None):
        self.completions = _FakeOAICompletions(response_text, raise_exc)


class _FakeOAIClient:
    def __init__(self, response_text=None, raise_exc=None, **kwargs):
        self.chat = _FakeOAIChat(response_text, raise_exc)


def _install_fake_openai(response_text=None, raise_exc=None):
    fake_mod = types.ModuleType("openai")
    fake_mod.OpenAI = lambda **kwargs: _FakeOAIClient(response_text, raise_exc)
    sys.modules["openai"] = fake_mod


# ── fake google.generativeai module ──────────────────────────────────────────

class _FakeGenResponse:
    def __init__(self, text):
        self.text = text


class _FakeGenModel:
    def __init__(self, model, system_instruction=None, response_text=None, raise_exc=None):
        self._response_text = response_text
        self._raise_exc = raise_exc

    def generate_content(self, user, request_options=None):
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeGenResponse(self._response_text)


def _install_fake_genai(response_text=None, raise_exc=None):
    fake_google = types.ModuleType("google")
    fake_genai = types.ModuleType("google.generativeai")
    fake_genai.configure = lambda **kwargs: None
    fake_genai.GenerativeModel = lambda model, system_instruction=None: _FakeGenModel(
        model, system_instruction, response_text, raise_exc
    )
    fake_google.generativeai = fake_genai
    sys.modules["google"] = fake_google
    sys.modules["google.generativeai"] = fake_genai


@pytest.fixture(autouse=True)
def _cleanup_fake_modules():
    yield
    sys.modules.pop("anthropic", None)
    sys.modules.pop("openai", None)
    sys.modules.pop("google", None)
    sys.modules.pop("google.generativeai", None)


# ─── AI_PROVIDERS shape ──────────────────────────────────────────────────────

def test_ai_providers_covers_all_four():
    assert set(AI_PROVIDERS.keys()) == {
        "Claude (Anthropic)", "OpenAI", "Gemini (Google)", "Groq (Free tier)",
    }


def test_every_model_entry_has_a_valid_tier():
    for provider, cfg in AI_PROVIDERS.items():
        for model_id, entry in cfg["models"].items():
            assert entry["tier"] in ("fast", "capable"), (provider, model_id)
            assert entry["label"]


# ─── capable_models ──────────────────────────────────────────────────────────

def test_capable_models_filters_to_capable_tier_only():
    result = capable_models("Claude (Anthropic)")
    assert set(result.keys()) == {"claude-sonnet-4-6", "claude-opus-5"}
    assert result["claude-sonnet-4-6"]["tier"] == "capable"
    assert result["claude-opus-5"]["tier"] == "capable"


def test_capable_models_every_provider_has_at_least_one_capable_entry():
    # Not required by the contract, but true of the current registry, and
    # doubles as a check that the filter isn't accidentally empty everywhere.
    for provider in AI_PROVIDERS:
        assert capable_models(provider), provider


def test_capable_models_unknown_provider_returns_empty_dict_not_crash():
    assert capable_models("Nonexistent Provider") == {}


# ─── resolve_key ─────────────────────────────────────────────────────────────

_CLAUDE_CFG = AI_PROVIDERS["Claude (Anthropic)"]


def test_resolve_key_secrets_wins_over_env():
    result = resolve_key(
        _CLAUDE_CFG,
        secrets_getter=lambda section, field: "sk-ant-from-secrets",
        environ={"ANTHROPIC_API_KEY": "sk-ant-from-env"},
    )
    assert result == "sk-ant-from-secrets"


def test_resolve_key_env_wins_when_secrets_absent():
    result = resolve_key(
        _CLAUDE_CFG,
        secrets_getter=lambda section, field: None,
        environ={"ANTHROPIC_API_KEY": "sk-ant-from-env"},
    )
    assert result == "sk-ant-from-env"


def test_resolve_key_empty_string_when_neither_present():
    result = resolve_key(
        _CLAUDE_CFG,
        secrets_getter=lambda section, field: None,
        environ={},
    )
    assert result == ""


def test_resolve_key_empty_string_secrets_treated_as_absent():
    # An empty-string secret (e.g. a blank field in st.secrets) must fall
    # through to env, not be treated as "present".
    result = resolve_key(
        _CLAUDE_CFG,
        secrets_getter=lambda section, field: "",
        environ={"ANTHROPIC_API_KEY": "sk-ant-from-env"},
    )
    assert result == "sk-ant-from-env"


# ─── call_llm — Claude (Anthropic) ───────────────────────────────────────────

def test_call_llm_claude_success_returns_text():
    _install_fake_anthropic(response_text="hello from claude")
    from stock_analyzer import ai_provider
    result = ai_provider.call_llm(
        "Claude (Anthropic)", "claude-haiku-4-5-20251001", "sk-ant-x",
        "sys prompt", "user prompt", max_tokens=100,
    )
    assert result == "hello from claude"
    assert ai_provider.LAST_CALL_ERROR is None


def test_call_llm_claude_skips_leading_non_text_block():
    """Reproduces a real live crash: claude-opus-5 returned a ThinkingBlock
    (no .text attribute) as content[0], and the code's old blind [0].text
    raised AttributeError instead of finding the actual text in content[1]."""
    _install_fake_anthropic_with_leading_thinking_block(response_text="the real answer")
    from stock_analyzer import ai_provider
    result = ai_provider.call_llm(
        "Claude (Anthropic)", "claude-opus-5", "sk-ant-x",
        "sys prompt", "user prompt", max_tokens=100,
    )
    assert result == "the real answer"
    assert ai_provider.LAST_CALL_ERROR is None


def test_call_llm_claude_exception_returns_none_and_sets_error():
    _install_fake_anthropic(raise_exc=RuntimeError("rate limited"))
    from stock_analyzer import ai_provider
    result = ai_provider.call_llm(
        "Claude (Anthropic)", "claude-haiku-4-5-20251001", "sk-ant-x",
        "sys prompt", "user prompt", max_tokens=100,
    )
    assert result is None
    assert "rate limited" in ai_provider.LAST_CALL_ERROR
    assert "RuntimeError" in ai_provider.LAST_CALL_ERROR


# ─── call_llm — OpenAI ───────────────────────────────────────────────────────

def test_call_llm_openai_success_returns_text():
    _install_fake_openai(response_text="hello from gpt")
    from stock_analyzer import ai_provider
    result = ai_provider.call_llm(
        "OpenAI", "gpt-4o-mini", "sk-x", "sys prompt", "user prompt", max_tokens=100,
    )
    assert result == "hello from gpt"
    assert ai_provider.LAST_CALL_ERROR is None


def test_call_llm_openai_exception_returns_none_and_sets_error():
    _install_fake_openai(raise_exc=ConnectionError("network down"))
    from stock_analyzer import ai_provider
    result = ai_provider.call_llm(
        "OpenAI", "gpt-4o-mini", "sk-x", "sys prompt", "user prompt", max_tokens=100,
    )
    assert result is None
    assert "network down" in ai_provider.LAST_CALL_ERROR


# ─── call_llm — Gemini ───────────────────────────────────────────────────────

def test_call_llm_gemini_success_returns_text():
    _install_fake_genai(response_text="hello from gemini")
    from stock_analyzer import ai_provider
    result = ai_provider.call_llm(
        "Gemini (Google)", "gemini-2.0-flash", "AIza-x", "sys prompt", "user prompt",
        max_tokens=100,
    )
    assert result == "hello from gemini"
    assert ai_provider.LAST_CALL_ERROR is None


def test_call_llm_gemini_exception_returns_none_and_sets_error():
    _install_fake_genai(raise_exc=ValueError("bad key"))
    from stock_analyzer import ai_provider
    result = ai_provider.call_llm(
        "Gemini (Google)", "gemini-2.0-flash", "AIza-x", "sys prompt", "user prompt",
        max_tokens=100,
    )
    assert result is None
    assert "bad key" in ai_provider.LAST_CALL_ERROR


# ─── call_llm — Groq (dispatches via the openai SDK shape) ──────────────────

def test_call_llm_groq_success_returns_text():
    _install_fake_openai(response_text="hello from groq")
    from stock_analyzer import ai_provider
    result = ai_provider.call_llm(
        "Groq (Free tier)", "llama-3.1-8b-instant", "gsk-x", "sys prompt", "user prompt",
        max_tokens=100,
    )
    assert result == "hello from groq"
    assert ai_provider.LAST_CALL_ERROR is None


def test_call_llm_groq_exception_returns_none_and_sets_error():
    _install_fake_openai(raise_exc=TimeoutError("timed out"))
    from stock_analyzer import ai_provider
    result = ai_provider.call_llm(
        "Groq (Free tier)", "llama-3.1-8b-instant", "gsk-x", "sys prompt", "user prompt",
        max_tokens=100,
    )
    assert result is None
    assert "timed out" in ai_provider.LAST_CALL_ERROR


# ─── call_llm — unknown provider ─────────────────────────────────────────────

def test_call_llm_unknown_provider_returns_none_never_raises():
    from stock_analyzer import ai_provider
    result = ai_provider.call_llm(
        "Made Up Provider", "some-model", "key", "sys", "user", max_tokens=100,
    )
    assert result is None
    assert "Made Up Provider" in ai_provider.LAST_CALL_ERROR
