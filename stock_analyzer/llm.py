"""LLM role -> model registry, request builder and the one Anthropic SDK call.

Pure apart from the SDK call in ``complete``. No Streamlit import and no
module-level mutable state (constants and frozen tables only).

Phase 1 (docs/plans/llm-model-routing.md, section 11): every role resolves to
today's model and the legacy/opus-4x profiles forward exactly what the caller
passed, so no request changes. The ``modern`` profile is built and tested now
and used from Phase 3.
"""
from __future__ import annotations

import math
import os
import types
from dataclasses import dataclass

# --- Model IDs -------------------------------------------------------------
HAIKU_4_5 = "claude-haiku-4-5-20251001"
SONNET_4_6 = "claude-sonnet-4-6"
OPUS_4_8 = "claude-opus-4-8"

# --- Roles -----------------------------------------------------------------
ROLE_FAST = "fast"
ROLE_DEEP = "deep"
ROLE_EXTRACT = "extract"
ROLE_EXTRACT_STRONG = "extract_strong"
ROLE_EXTRACT_TRIGGER = "extract_trigger"
ROLE_FILTER = "filter"
ROLE_SCORE = "score"

ROLES = frozenset({
    ROLE_FAST, ROLE_DEEP, ROLE_EXTRACT, ROLE_EXTRACT_STRONG,
    ROLE_EXTRACT_TRIGGER, ROLE_FILTER, ROLE_SCORE,
})
PINNED_ROLES = frozenset({ROLE_SCORE, ROLE_FILTER, ROLE_EXTRACT_TRIGGER})
NARRATIVE_ROLES = frozenset({ROLE_FAST, ROLE_DEEP})
JSON_ROLES = ROLES - NARRATIVE_ROLES

# Equal to today's models: Phase 1 changes no model.
CODE_DEFAULTS = types.MappingProxyType({
    ROLE_FAST: HAIKU_4_5,
    ROLE_FILTER: HAIKU_4_5,
    ROLE_SCORE: HAIKU_4_5,
    ROLE_EXTRACT_TRIGGER: HAIKU_4_5,
    ROLE_DEEP: SONNET_4_6,
    ROLE_EXTRACT: SONNET_4_6,
    ROLE_EXTRACT_STRONG: OPUS_4_8,
})

# --- Profiles --------------------------------------------------------------
PROFILE_LEGACY = "legacy-sampling"
PROFILE_OPUS_4X = "opus-4x"
PROFILE_MODERN = "modern"

MODERN_TOKEN_FACTOR = 1.3  # (provisional; tuned in Phase 3 from live checks)
THINKING_HEADROOM_TOKENS = 2048  # (provisional; tuned in Phase 3 from live checks)

_REDACTED = "***REDACTED***"
_ERROR_MAX_CHARS = 300


def resolve_model(role: str, model: str | None = None) -> str:
    """Explicit non-empty ``model`` wins; otherwise the role's code default.

    Unknown role raises ValueError (a programming error). Phase 2 will add the
    env / App Settings override between these two; pinned roles
    (PINNED_ROLES) will ignore both.
    """
    if role not in ROLES:
        raise ValueError(f"unknown LLM role: {role!r}")
    if isinstance(model, str) and model:
        # A pinned role's model is decided here, in reviewed code, so no
        # caller can choose a different one: the score / filter /
        # extract_trigger outputs feed the composite and Act Today, and
        # their callers are not all review-gated files.
        if role in PINNED_ROLES and model != CODE_DEFAULTS[role]:
            raise ValueError(
                f"role {role!r} is pinned to {CODE_DEFAULTS[role]!r}; "
                f"change CODE_DEFAULTS in a reviewed commit instead of passing {model!r}")
        return model
    return CODE_DEFAULTS[role]


def profile_for(model: str) -> str:
    """Request-shape profile for a model id; unknown/future ids are modern."""
    m = model or ""
    if m.startswith("claude-haiku-4-5") or m in ("claude-sonnet-4-6", "claude-opus-4-6"):
        return PROFILE_LEGACY
    # Forward-unchanged, no sampling: Opus 4.7/4.8, plus Opus 5 (in use today
    # through the Investigator's eval-passed menu) and Sonnet 5: no temperature
    # is sent, and they are not covered by the 5.5 rules the modern profile
    # applies.
    if m in ("claude-opus-4-7", "claude-opus-4-8", "claude-opus-5", "claude-sonnet-5"):
        return PROFILE_OPUS_4X
    return PROFILE_MODERN


def build_request(role, model, *, max_tokens, messages, system=None,
                  temperature=None, timeout=None) -> dict:
    """Pure: the kwargs for ``client.messages.create``."""
    profile = profile_for(model)
    narrative = role in NARRATIVE_ROLES
    req: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system is not None:
        req["system"] = system
    if timeout is not None:
        req["timeout"] = timeout

    if profile == PROFILE_LEGACY:
        if temperature is not None:
            req["temperature"] = temperature
        return req
    if profile == PROFILE_OPUS_4X:
        # Opus 4.8 rejects sampling parameters.
        return req

    # modern: never temperature; effort low; thinking off for JSON roles.
    req["output_config"] = {"effort": "low"}
    if not narrative:
        if "haiku" in model:
            req["thinking"] = {"type": "disabled"}
        elif "sonnet" in model:
            req["thinking"] = {"type": "between_tools"}
    req["max_tokens"] = math.ceil(max_tokens * MODERN_TOKEN_FACTOR) + (
        THINKING_HEADROOM_TOKENS if narrative else 0)
    return req


@dataclass(frozen=True)
class LLMResult:
    text: str | None
    stop_reason: str | None
    error: str | None
    model: str | None
    usage: dict | None
    truncated: bool = False
    # HTTP status of an SDK APIStatusError (429, 529, 401...), else None.
    status_code: int | None = None

    @property
    def ok(self) -> bool:
        return self.text is not None


def parse_response(role, response, model) -> LLMResult:
    """Pure: turn an SDK response into an LLMResult."""
    stop = getattr(response, "stop_reason", None)
    u = getattr(response, "usage", None)
    usage = None
    if u is not None:
        usage = {"input_tokens": getattr(u, "input_tokens", None),
                 "output_tokens": getattr(u, "output_tokens", None)}

    text = None
    content = getattr(response, "content", None)
    for block in (content if content is not None else []):
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            break

    narrative = role in NARRATIVE_ROLES
    error = None
    truncated = False

    if stop == "refusal":
        cat = getattr(getattr(response, "stop_details", None), "category", None)
        error = "refused" + (f" ({cat})" if cat else "")
        text = None
    elif stop == "max_tokens":
        truncated = True
        if not narrative:
            text = None
            error = "reply cut off at max_tokens"
    elif stop not in (None, "end_turn", "stop_sequence"):
        if not narrative:
            text = None
            error = f"unexpected stop_reason: {stop}"

    if text is None and error is None:
        error = f"no text in reply (stop_reason={stop})"

    return LLMResult(text=text, stop_reason=stop, error=error, model=model,
                     usage=usage, truncated=truncated)


def complete(role, *, api_key, messages, max_tokens, system=None, model=None,
             temperature=None, timeout=None, client_kwargs=None,
             client_factory=None) -> LLMResult:
    """Resolve model, build request, call the SDK, parse. Never raises."""
    resolved = None
    try:
        resolved = resolve_model(role, model)
        if not api_key:
            return LLMResult(text=None, stop_reason=None, error="no API key",
                             model=resolved, usage=None)
        request = build_request(role, resolved, max_tokens=max_tokens,
                                messages=messages, system=system,
                                temperature=temperature, timeout=timeout)
        factory = client_factory
        if factory is None:
            import anthropic
            factory = anthropic.Anthropic
        client = factory(api_key=api_key, **(client_kwargs or {}))
        response = client.messages.create(**request)
        return parse_response(role, response, resolved)
    except Exception as e:  # noqa: BLE001 - never raises by contract
        msg = f"{type(e).__name__}: {e}"
        if isinstance(api_key, str) and len(api_key) >= 6:
            msg = msg.replace(api_key, _REDACTED)
        status = getattr(e, "status_code", None)
        return LLMResult(text=None, stop_reason=None,
                         error=msg[:_ERROR_MAX_CHARS], model=resolved,
                         usage=None,
                         status_code=status if isinstance(status, int) and not isinstance(status, bool) else None)


def resolve_anthropic_key(secrets_getter=None, environ=None) -> str:
    """Env ANTHROPIC_API_KEY -> [anthropic] api_key -> flat ANTHROPIC_API_KEY.

    ``secrets_getter(section, key)`` returns the secret string; ``section`` is
    None for a flat key. Any exception from it counts as "not set" (the cron
    has no secrets.toml). Returns "" when nothing is found.
    """
    env = os.environ if environ is None else environ
    v = env.get("ANTHROPIC_API_KEY")
    if isinstance(v, str) and v.strip():
        return v.strip()
    if secrets_getter is not None:
        for section, key in (("anthropic", "api_key"), (None, "ANTHROPIC_API_KEY")):
            try:
                v = secrets_getter(section, key)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""
