"""LLM role -> model registry, request builder and the one Anthropic SDK call.

Pure apart from the SDK call in ``complete``. No Streamlit import and no
module-level mutable state (constants and frozen tables only); the App
Settings snapshot lives in ``llm_settings`` and is reached through a lazy
import (``_provider_setting``).

Phase 1 (docs/plans/llm-model-routing.md, section 11): every role resolves to
today's model and the legacy/opus-4x profiles forward exactly what the caller
passed, so no request changes.

JSON handling is decided by the role, and a call can add it with
``expects_json=True`` (Phase 2, section 13.2 A).

Phase 2 (section 13.2 B-D): an editable role (fast / deep / extract) resolves
explicit ``model`` -> env ``DRISHTA_LLM_MODEL_<ROLE>`` -> the ``setting`` entry
-> the code default, and reports which one won. A model the code knows the
thinking-off form of (the 5.5 family, exact id) gets the ``known-modern``
request shape; any other non-legacy id gets ``generic`` (no ``thinking`` field,
effort only when its stored capabilities list it, thinking headroom always).
Known Sonnet / Opus 5.5 requests go through the beta client with the refusal
fallback.
"""
from __future__ import annotations

import math
import os
import re
import types
from collections.abc import Mapping
from dataclasses import dataclass, replace

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
# The roles App Settings / env may re-point (pinned roles and extract_strong
# stay in reviewed code).
EDITABLE_ROLES = frozenset({ROLE_FAST, ROLE_DEEP, ROLE_EXTRACT})

# Where a resolved model came from.
SOURCE_CODE = "code"
SOURCE_SETTING = "setting"
SOURCE_ENV = "env"
SOURCE_EXPLICIT = "explicit"
ENV_PREFIX = "DRISHTA_LLM_MODEL_"  # env var = prefix + role.upper()

# B3: the thinking-off forms are a 400 above ``high``, so an editable role's
# effort is limited to these.
ALLOWED_EFFORTS = ("low", "medium", "high")

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
PROFILE_KNOWN_MODERN = "known-modern"
PROFILE_GENERIC = "generic"

# N6: exact match only; a preview / future id is generic.
_KNOWN_MODERN_RE = re.compile(r"^claude-(haiku|sonnet|opus)-5-5(-\d{8})?$")
# Family -> the thinking-off form for a JSON role (opus: no field).
_KNOWN_THINKING_OFF = types.MappingProxyType({
    "haiku": {"type": "disabled"},
    "sonnet": {"type": "between_tools"},
    "opus": None,
})
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Exact model id -> (input $, output $) per million tokens. Source:
# scripts/session_cost.py PRICE (Anthropic API price list, cached 2026-10-06).
PRICES_PER_MTOK = types.MappingProxyType({
    HAIKU_4_5: (1.0, 5.0),
    SONNET_4_6: (3.0, 15.0),
    OPUS_4_8: (5.0, 25.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
})

MODERN_TOKEN_FACTOR = 1.3  # (provisional; tuned in Phase 3 from live checks)
THINKING_HEADROOM_TOKENS = 2048  # (provisional; tuned in Phase 3 from live checks)

_REDACTED = "***REDACTED***"
_ERROR_MAX_CHARS = 300


@dataclass(frozen=True)
class RoleSetting:
    """One validated App Settings entry for an editable role. ``capabilities``
    is the normalized Models API snapshot (see ``normalize_capabilities``)."""
    model: str
    effort: str | None = None
    capabilities: Mapping | None = None


def _claude_id(v) -> bool:
    return isinstance(v, str) and v.startswith("claude-")


def resolve_model_with_source(role: str, model: str | None = None, *,
                              setting: Mapping | None = None,
                              env: Mapping | None = None) -> tuple[str, str]:
    """(model, source). Unknown role raises ValueError (a programming error).

    Roles outside EDITABLE_ROLES (the pinned roles and extract_strong) never
    read env or the setting. An editable role resolves explicit ``model`` ->
    env ``DRISHTA_LLM_MODEL_<ROLE>`` -> ``setting[role]`` -> code default.
    """
    if role not in ROLES:
        raise ValueError(f"unknown LLM role: {role!r}")
    explicit = isinstance(model, str) and bool(model)
    if role not in EDITABLE_ROLES:
        if explicit:
            # A pinned role's model is decided here, in reviewed code, so no
            # caller can choose a different one: the score / filter /
            # extract_trigger outputs feed the composite and Act Today, and
            # their callers are not all review-gated files.
            if role in PINNED_ROLES and model != CODE_DEFAULTS[role]:
                raise ValueError(
                    f"role {role!r} is pinned to {CODE_DEFAULTS[role]!r}; "
                    f"change CODE_DEFAULTS in a reviewed commit instead of passing {model!r}")
            return model, SOURCE_EXPLICIT
        return CODE_DEFAULTS[role], SOURCE_CODE
    if explicit:
        return model, SOURCE_EXPLICIT
    environ = os.environ if env is None else env
    ev = environ.get(ENV_PREFIX + role.upper())
    if isinstance(ev, str):
        ev = ev.strip()
        if ev and _claude_id(ev):
            return ev, SOURCE_ENV
    if isinstance(setting, Mapping):
        entry = setting.get(role)
        if isinstance(entry, RoleSetting) and _claude_id(entry.model):
            return entry.model, SOURCE_SETTING
    return CODE_DEFAULTS[role], SOURCE_CODE


def resolve_model(role: str, model: str | None = None, *,
                  setting: Mapping | None = None,
                  env: Mapping | None = None) -> str:
    """The model string of ``resolve_model_with_source``."""
    return resolve_model_with_source(role, model, setting=setting, env=env)[0]


def _provider_setting(role, model) -> Mapping | None:
    """The App Settings snapshot for an editable role with no explicit model,
    else None. Pinned roles and ``extract_strong`` never trigger a DB read.
    Never raises."""
    if role not in EDITABLE_ROLES or (isinstance(model, str) and model):
        return None
    try:
        from stock_analyzer import llm_settings
        return llm_settings.current_setting()
    except Exception:  # noqa: BLE001
        return None


def resolved_with_source(role: str, model: str | None = None) -> tuple[str, str]:
    """(model, source) as ``complete`` would resolve it now (env, the App
    Settings snapshot, then the code default)."""
    return resolve_model_with_source(role, model,
                                     setting=_provider_setting(role, model))


def resolved(role: str, model: str | None = None) -> str:
    """The model string of ``resolved_with_source``."""
    return resolved_with_source(role, model)[0]


def _known_family(model: str) -> str | None:
    m = _KNOWN_MODERN_RE.match(model or "")
    return m.group(1) if m else None


def profile_for(model: str) -> str:
    """Request-shape profile for a model id; an id the code doesn't know is generic."""
    m = model or ""
    if m.startswith("claude-haiku-4-5") or m in ("claude-sonnet-4-6", "claude-opus-4-6"):
        return PROFILE_LEGACY
    # Forward-unchanged, no sampling: Opus 4.7/4.8, plus Opus 5 (in use today
    # through the Investigator's eval-passed menu) and Sonnet 5: no temperature
    # is sent, and they are not covered by the 5.5 rules the known-modern
    # profile applies.
    if m in ("claude-opus-4-7", "claude-opus-4-8", "claude-opus-5", "claude-sonnet-5"):
        return PROFILE_OPUS_4X
    if _KNOWN_MODERN_RE.match(m):
        return PROFILE_KNOWN_MODERN
    return PROFILE_GENERIC


def uses_fallback(model: str) -> bool:
    """True for a known Sonnet / Opus 5.5 id (never Haiku: a ``fallbacks``
    list is a 400 there; never legacy, opus-4x or generic)."""
    return _known_family(model) in ("sonnet", "opus")


def json_mode(role: str, expects_json: bool | None = None) -> bool:
    """True when the reply must be treated as JSON (thinking off on modern
    models, a cut-off or odd-stop reply is a failure, not text).

    The role decides, and a call can only ADD JSON handling: ``expects_json=True``
    turns it on for a narrative role (a ``fast`` / ``deep`` site that parses
    JSON or a trailing verdict line). ``False`` on a non-narrative role is a
    programming error, and so is any non-bool value other than None. An
    unknown role fails closed to JSON handling.
    """
    if expects_json is None:
        return role not in NARRATIVE_ROLES
    if expects_json is True:
        return True
    if expects_json is False:
        if role not in NARRATIVE_ROLES:
            raise ValueError(
                f"role {role!r} always expects JSON; expects_json=False is not allowed")
        return False
    raise ValueError(f"expects_json must be True, False or None, not {expects_json!r}")


def _clamp_to_capabilities(tokens: int, capabilities) -> int:
    if isinstance(capabilities, Mapping):
        cap = capabilities.get("max_tokens")
        if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
            return min(tokens, cap)
    return tokens


def build_request(role, model, *, max_tokens, messages, system=None,
                  temperature=None, timeout=None, expects_json=None,
                  effort=None, capabilities=None) -> dict:
    """Pure: the kwargs for ``client.messages.create`` (``client.beta.messages
    .create`` when the request carries ``betas``)."""
    profile = profile_for(model)
    narrative = not json_mode(role, expects_json)
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

    # Both modern profiles: never temperature; effort is capped (B3) and, when
    # the stored capabilities list levels, must be one of them.
    if effort is not None and effort not in ALLOWED_EFFORTS:
        raise ValueError(f"effort must be one of {ALLOWED_EFFORTS}, not {effort!r}")
    listed = capabilities.get("efforts") if isinstance(capabilities, Mapping) else None
    if (effort is not None and isinstance(listed, (list, tuple))
            and effort not in listed):
        raise ValueError(f"model {model!r} does not list effort {effort!r}")
    eff = effort or "low"

    if profile == PROFILE_KNOWN_MODERN:
        family = _known_family(model)
        req["output_config"] = {"effort": eff}
        if not narrative:
            off = _KNOWN_THINKING_OFF[family]
            if off is not None:
                req["thinking"] = dict(off)
        tokens = math.ceil(max_tokens * MODERN_TOKEN_FACTOR) + (
            THINKING_HEADROOM_TOKENS if narrative else 0)
        if uses_fallback(model):
            req["betas"] = [FALLBACK_BETA]
            req["fallbacks"] = "default"
    else:
        # generic: no thinking field (the model's default; thinking may be
        # on, so every role gets headroom), effort only when it is listed.
        if isinstance(listed, (list, tuple)) and eff in listed:
            req["output_config"] = {"effort": eff}
        tokens = math.ceil(max_tokens * MODERN_TOKEN_FACTOR) + THINKING_HEADROOM_TOKENS
    req["max_tokens"] = _clamp_to_capabilities(tokens, capabilities)
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
    # ``model`` is the model that SERVED the reply (N3); the one asked for is
    # ``requested_model``. ``fell_back`` is True when a refusal fallback ran.
    requested_model: str | None = None
    fell_back: bool = False
    model_source: str | None = None

    @property
    def ok(self) -> bool:
        return self.text is not None


def parse_response(role, response, model, *, expects_json=None) -> LLMResult:
    """Pure: turn an SDK response into an LLMResult."""
    stop = getattr(response, "stop_reason", None)
    u = getattr(response, "usage", None)
    usage = None
    if u is not None:
        usage = {"input_tokens": getattr(u, "input_tokens", None),
                 "output_tokens": getattr(u, "output_tokens", None)}

    content = getattr(response, "content", None)
    blocks = list(content) if content is not None else []
    fell_back = any(getattr(b, "type", None) == "fallback" for b in blocks)
    iterations = getattr(u, "iterations", None)
    if isinstance(iterations, (list, tuple)) and any(
            getattr(it, "type", None) == "fallback_message" for it in iterations):
        fell_back = True
    # Text before the last fallback block was written by the model that
    # declined, so only the blocks after it count.
    last_fb = max((i for i, b in enumerate(blocks)
                   if getattr(b, "type", None) == "fallback"), default=-1)

    # First text block with real content; empty / whitespace-only text is no
    # reply. The kept text is unstripped, exactly as returned.
    text = None
    saw_text_block = False
    for block in blocks[last_fb + 1:]:
        if getattr(block, "type", None) == "text":
            saw_text_block = True
            t = getattr(block, "text", None)
            if isinstance(t, str) and t.strip():
                text = t
                break

    narrative = not json_mode(role, expects_json)
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
        if saw_text_block:
            error = f"no text in reply (empty text, stop_reason={stop})"
        else:
            error = f"no text in reply (stop_reason={stop})"

    served = getattr(response, "model", None)
    if fell_back:
        # On a mid-stream fallback the SDK keeps the primary's top-level
        # ``model``, so prefer the fallback iteration, then the last
        # ``fallback`` block's ``to.model``.
        fb_model = None
        if isinstance(iterations, (list, tuple)):
            for it in iterations:
                if getattr(it, "type", None) == "fallback_message":
                    m = getattr(it, "model", None)
                    if isinstance(m, str) and m:
                        fb_model = m
        if fb_model is None:
            for b in blocks:
                if getattr(b, "type", None) == "fallback":
                    m = getattr(getattr(b, "to", None), "model", None)
                    if isinstance(m, str) and m:
                        fb_model = m
        if fb_model is not None:
            served = fb_model
    return LLMResult(text=text, stop_reason=stop, error=error,
                     model=served if isinstance(served, str) and served else model,
                     usage=usage, truncated=truncated, requested_model=model,
                     fell_back=fell_back)


def _err_text(e: Exception, api_key) -> str:
    msg = f"{type(e).__name__}: {e}"
    if isinstance(api_key, str) and len(api_key) >= 6:
        msg = msg.replace(api_key, _REDACTED)
    return msg[:_ERROR_MAX_CHARS]


def complete(role, *, api_key, messages, max_tokens, system=None, model=None,
             temperature=None, expects_json=None, timeout=None, setting=None,
             client_kwargs=None, client_factory=None) -> LLMResult:
    """Resolve model, build request, call the SDK, parse. Never raises."""
    resolved = None
    source = None
    try:
        if setting is None:
            setting = _provider_setting(role, model)
        resolved, source = resolve_model_with_source(role, model, setting=setting)
        json_mode(role, expects_json)  # invalid flag -> error result, no client
        if not api_key:
            return LLMResult(text=None, stop_reason=None, error="no API key",
                             model=resolved, usage=None, requested_model=resolved,
                             model_source=source)
        effort = capabilities = None
        if source == SOURCE_SETTING:
            entry = setting[role]
            effort, capabilities = entry.effort, entry.capabilities
        request = build_request(role, resolved, max_tokens=max_tokens,
                                messages=messages, system=system,
                                temperature=temperature, timeout=timeout,
                                expects_json=expects_json, effort=effort,
                                capabilities=capabilities)
        factory = client_factory
        if factory is None:
            import anthropic
            factory = anthropic.Anthropic
        client = factory(api_key=api_key, **(client_kwargs or {}))
        if "betas" in request:
            response = client.beta.messages.create(**request)
        else:
            response = client.messages.create(**request)
        return replace(parse_response(role, response, resolved, expects_json=expects_json),
                       model_source=source)
    except Exception as e:  # noqa: BLE001 - never raises by contract
        status = getattr(e, "status_code", None)
        return LLMResult(text=None, stop_reason=None,
                         error=_err_text(e, api_key), model=resolved,
                         usage=None, requested_model=resolved, model_source=source,
                         status_code=status if isinstance(status, int) and not isinstance(status, bool) else None)


# --- Models API helpers (never raise) ---------------------------------------
_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")


def _supported(node) -> bool:
    return getattr(node, "supported", None) is True


def _int_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def normalize_capabilities(info) -> dict:
    """Pure: a Models API ``ModelInfo``-like object -> a plain dict snapshot.
    A missing attribute anywhere counts as unsupported / None."""
    caps = getattr(info, "capabilities", None)
    effort = getattr(caps, "effort", None)
    efforts = []
    if _supported(effort):
        efforts = [lvl for lvl in _EFFORT_ORDER if _supported(getattr(effort, lvl, None))]
    types_node = getattr(getattr(caps, "thinking", None), "types", None)
    thinking = [t for t in ("adaptive", "enabled") if _supported(getattr(types_node, t, None))]
    return {
        "efforts": efforts,
        "thinking_types": thinking,
        "structured_outputs": _supported(getattr(caps, "structured_outputs", None)),
        "max_tokens": _int_or_none(getattr(info, "max_tokens", None)),
        "max_input_tokens": _int_or_none(getattr(info, "max_input_tokens", None)),
    }


def _models_client(api_key, client_factory, timeout):
    factory = client_factory
    if factory is None:
        import anthropic
        factory = anthropic.Anthropic
    return factory(api_key=api_key, timeout=timeout)


def retrieve_model(api_key, model_id, *, client_factory=None,
                   timeout=10) -> tuple[dict | None, str | None]:
    """(normalized capabilities, None) or (None, error). Never raises."""
    if not api_key:
        return None, "no API key"
    try:
        client = _models_client(api_key, client_factory, timeout)
        return normalize_capabilities(client.models.retrieve(model_id)), None
    except Exception as e:  # noqa: BLE001
        return None, _err_text(e, api_key)


def list_model_ids(api_key, *, client_factory=None,
                   timeout=10) -> tuple[list[str] | None, str | None]:
    """(model ids this key can use, None) or (None, error). Never raises."""
    if not api_key:
        return None, "no API key"
    try:
        client = _models_client(api_key, client_factory, timeout)
        ids = [getattr(m, "id", None) for m in client.models.list(limit=100)]
        return [i for i in ids if isinstance(i, str)], None
    except Exception as e:  # noqa: BLE001
        return None, _err_text(e, api_key)


def approx_cost_usd(model, usage) -> float | None:
    """Approximate USD cost of one call from PRICES_PER_MTOK, or None when the
    model isn't priced or the token counts aren't ints."""
    if not isinstance(model, str):
        return None
    price = PRICES_PER_MTOK.get(model)
    if price is None:
        price = PRICES_PER_MTOK.get(re.sub(r"-\d{8}$", "", model))
    if price is None or not isinstance(usage, Mapping):
        return None
    i, o = usage.get("input_tokens"), usage.get("output_tokens")
    if _int_or_none(i) is None or _int_or_none(o) is None:
        return None
    return (i * price[0] + o * price[1]) / 1_000_000


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
