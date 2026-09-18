"""
Shared multi-provider LLM registry + dispatch, extracted from 🤖 AI Snapshot's
existing `_AI_PROVIDERS` dict and its `_call_ai_brief` dispatch block
(`app.py` ~10685-10830) for reuse by 🔎 Portfolio Investigator
(docs/plans/portfolio-investigator.md).

Deliberately does NOT touch AI Snapshot's own inline copy, nor any of the
~19 other hardwired-Anthropic-only call sites elsewhere in `app.py` /
`bundle_loader.py` — that migration is separately-scoped, pre-existing
technical debt (see the plan doc's "architecture debt" section). This module
is net-new and is used only by the Investigator.

Pure logic — no Streamlit imports, no app.py imports. `resolve_key()` takes
a plain `secrets_getter` callable and a plain `environ` dict so it can be
unit-tested and used outside a running Streamlit session; the caller (app.py)
supplies a wrapper around `st.secrets.get(...)` and `os.environ`.

Fail-open convention for `call_llm()` mirrors `portfolio_qa.py`'s
`parse_question()` / `LAST_PARSE_ERROR`: on any failure, return `None` and
set the module-level `LAST_CALL_ERROR` with a short description — never
raise out of the function.
"""

# ─── Provider registry ───────────────────────────────────────────────────────
# Same shape as AI Snapshot's `_AI_PROVIDERS`, plus a per-model "tier" field
# ("fast" | "capable") used for display/filtering only — it does NOT gate
# which models are offered in the UI (all tiers stay selectable; a separate,
# required refusal eval decides which specific models are actually enabled
# for the Investigator — see the plan doc's ratified decision 2).
AI_PROVIDERS = {
    "Claude (Anthropic)": {
        "models": {
            "claude-haiku-4-5-20251001": {"label": "Haiku 4.5 — fast & cheap", "tier": "fast"},
            "claude-sonnet-4-6":         {"label": "Sonnet 4.6 — more capable", "tier": "capable"},
            "claude-opus-5":             {"label": "Opus 5 — most capable", "tier": "capable"},
        },
        "secrets_path": ("anthropic", "api_key"),
        "env_var":      "ANTHROPIC_API_KEY",
        "key_hint":     "sk-ant-...",
        "key_url":      "https://console.anthropic.com",
    },
    "OpenAI": {
        "models": {
            "gpt-4o-mini": {"label": "GPT-4o mini — fast & cheap", "tier": "fast"},
            "gpt-4o":      {"label": "GPT-4o — more capable", "tier": "capable"},
        },
        "secrets_path": ("openai", "api_key"),
        "env_var":      "OPENAI_API_KEY",
        "key_hint":     "sk-...",
        "key_url":      "https://platform.openai.com/api-keys",
    },
    "Gemini (Google)": {
        "models": {
            "gemini-2.0-flash":                {"label": "Gemini 2.0 Flash — fast (free tier)", "tier": "fast"},
            "gemini-2.5-flash-preview-04-17":  {"label": "Gemini 2.5 Flash — most capable", "tier": "capable"},
        },
        "secrets_path": ("google", "api_key"),
        "env_var":      "GOOGLE_API_KEY",
        "key_hint":     "AIza...",
        "key_url":      "https://aistudio.google.com/app/apikey",
    },
    "Groq (Free tier)": {
        "models": {
            "llama-3.1-8b-instant": {"label": "Llama 3.1 8B — fastest", "tier": "fast"},
            "mixtral-8x7b-32768":   {"label": "Mixtral 8x7B — smarter", "tier": "capable"},
        },
        "secrets_path": ("groq", "api_key"),
        "env_var":      "GROQ_API_KEY",
        "key_hint":     "gsk_...",
        "key_url":      "https://console.groq.com/keys",
    },
}


def capable_models(provider: str) -> dict:
    """Returns AI_PROVIDERS[provider]["models"] filtered to tier == "capable"
    only. A convenience filter for a caller that wants capable-only models
    for a specific use case — it does NOT mean the app only ever offers
    capable-tier models (the Investigator's own UI keeps all tiers
    selectable per the plan's ratified decision 2). Safe (returns {}) if the
    provider has no capable-tier entries or is unknown."""
    models = AI_PROVIDERS.get(provider, {}).get("models", {})
    return {mid: entry for mid, entry in models.items() if entry.get("tier") == "capable"}


def resolve_key(provider_cfg: dict, secrets_getter, environ: dict) -> str:
    """Resolution order: secrets -> env -> "". `secrets_getter` is a callable
    taking (section, field) and returning the resolved value or None/"".
    `environ` is a plain dict (the caller passes os.environ, or a fake dict
    in tests). Streamlit-free by design — the caller wraps st.secrets.get."""
    section, field = provider_cfg["secrets_path"]
    key = secrets_getter(section, field)
    if key:
        return key
    key = environ.get(provider_cfg["env_var"])
    if key:
        return key
    return ""


# ─── Dispatch ────────────────────────────────────────────────────────────────

LAST_CALL_ERROR: str | None = None


def call_llm(provider: str, model: str, api_key: str, system: str, user: str,
             max_tokens: int, timeout: float = 30.0) -> str | None:
    """Dispatches to whichever of Claude/OpenAI/Gemini/Groq `provider` names,
    mirroring the exact request-building shape already used by AI Snapshot's
    `_call_ai_brief` (app.py ~10798-10830). Fails open, never raises: on any
    exception (network error, bad key, rate limit, malformed response, or an
    unrecognized provider) it returns None and sets LAST_CALL_ERROR with a
    short description — mirrors portfolio_qa.py's parse_question() /
    LAST_PARSE_ERROR convention."""
    global LAST_CALL_ERROR
    LAST_CALL_ERROR = None
    try:
        if provider == "Claude (Anthropic)":
            import anthropic as _anth
            c = _anth.Anthropic(api_key=api_key)
            r = c.messages.create(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
                timeout=timeout,
            )
            return r.content[0].text
        elif provider == "OpenAI":
            from openai import OpenAI as _OAI
            c = _OAI(api_key=api_key, timeout=timeout)
            r = c.chat.completions.create(
                model=model, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system},
                          {"role": "user",   "content": user}],
            )
            return r.choices[0].message.content
        elif provider == "Gemini (Google)":
            import google.generativeai as _genai
            _genai.configure(api_key=api_key)
            _gm = _genai.GenerativeModel(model, system_instruction=system)
            r = _gm.generate_content(user, request_options={"timeout": timeout})
            return r.text
        elif provider == "Groq (Free tier)":
            from openai import OpenAI as _OAI
            c = _OAI(api_key=api_key, base_url="https://api.groq.com/openai/v1", timeout=timeout)
            r = c.chat.completions.create(
                model=model, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system},
                          {"role": "user",   "content": user}],
            )
            return r.choices[0].message.content
        else:
            LAST_CALL_ERROR = f"unknown provider: {provider}"
            return None
    except Exception as e:
        LAST_CALL_ERROR = f"{type(e).__name__}: {e}"[:300]
        return None
