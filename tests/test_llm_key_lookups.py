"""Anthropic key lookups after P1-d: bundle_loader reads only the [anthropic]
secret section; cron lanes read env only and log INERT on a blank key."""
import sys
import types

import pytest

from stock_analyzer import bundle_loader, llm

pytestmark = pytest.mark.fast


def _fake_streamlit(monkeypatch, secrets):
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets=secrets))


def _key(environ):
    return llm.resolve_anthropic_key(bundle_loader._anthropic_section_secret, environ=environ)


def test_flat_secret_is_never_read(monkeypatch):
    _fake_streamlit(monkeypatch, {"ANTHROPIC_API_KEY": "flat"})
    assert _key({}) == ""


def test_section_secret_is_stripped(monkeypatch):
    _fake_streamlit(monkeypatch, {"anthropic": {"api_key": " k "}})
    assert _key({}) == "k"


def test_env_wins_over_secret(monkeypatch):
    _fake_streamlit(monkeypatch, {"anthropic": {"api_key": "secret"}})
    assert _key({"ANTHROPIC_API_KEY": "envkey"}) == "envkey"


def test_raising_secrets_is_not_set(monkeypatch):
    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("no secrets.toml")

    _fake_streamlit(monkeypatch, _Boom())
    assert _key({}) == ""


@pytest.mark.parametrize("lane,prefix", [
    ("_run_thesis", "thesis"),
    ("_run_debrief", "debrief"),
    ("_run_monthly_report", "monthly"),
])
def test_blank_env_key_is_inert_in_cron(monkeypatch, lane, prefix):
    import cron_runner
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    logs = []
    monkeypatch.setattr(cron_runner, "_log", lambda m, *a, **k: logs.append(str(m)))
    assert getattr(cron_runner, lane)(None, True) == 0
    assert any(f"{prefix}: INERT" in m for m in logs)


def test_load_bundle_passes_the_section_only_getter():
    """The getter is only half the protection: the call site must pass it.
    A getter that also read the flat ANTHROPIC_API_KEY secret could switch on
    LLM news scoring (which moves the composite) on a deploy whose secrets carry
    only the flat key. The behaviour tests above only exercise the getter."""
    import ast
    from pathlib import Path
    src = Path(bundle_loader.__file__).read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "resolve_anthropic_key"]
    assert calls, "bundle_loader no longer calls llm.resolve_anthropic_key"
    for c in calls:
        assert not c.keywords, ast.unparse(c)
        assert len(c.args) == 1, ast.unparse(c)
        assert isinstance(c.args[0], ast.Name) and c.args[0].id == "_anthropic_section_secret", ast.unparse(c)
