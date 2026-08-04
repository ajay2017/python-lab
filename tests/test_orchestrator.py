"""Regression test for stock_analyzer/providers/orchestrator.py — 2026-08-04
audit finding: _failover_single's generic `except Exception` also caught
NotImplementedError, which providers/base.py's own docstring defines as "a
programming error -- the orchestrator never calls a method whose capability
the provider didn't advertise." Swallowing it into the routine
try-next-provider path could mask a real regression as a benign data hiccup.
"""
import pandas as pd
import pytest

from stock_analyzer.providers import orchestrator
from stock_analyzer.providers.base import ProviderUnavailable


class _BrokenProvider:
    name = "broken"

    def price_history(self, ticker, period="6mo"):
        raise NotImplementedError


class _FlakyProvider:
    name = "flaky"

    def price_history(self, ticker, period="6mo"):
        raise ProviderUnavailable("no key configured")


class _GoodProvider:
    name = "good"

    def price_history(self, ticker, period="6mo"):
        return pd.DataFrame({"Close": [1.0, 2.0]})


def test_not_implemented_error_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(orchestrator, "_providers_for", lambda cap: [_BrokenProvider()])
    with pytest.raises(NotImplementedError):
        orchestrator._failover_single("history", "price_history", "AAPL")


def test_not_implemented_error_on_primary_stops_before_trying_next_provider(monkeypatch):
    # A real programming-error regression must surface immediately, not be
    # masked by falling through to a provider that would have succeeded.
    monkeypatch.setattr(orchestrator, "_providers_for", lambda cap: [_BrokenProvider(), _GoodProvider()])
    with pytest.raises(NotImplementedError):
        orchestrator._failover_single("history", "price_history", "AAPL")


def test_provider_unavailable_still_falls_through_to_next_provider(monkeypatch):
    # Unrelated regression guard: the routine try-next-provider path must
    # still work for genuine data hiccups.
    monkeypatch.setattr(orchestrator, "_providers_for", lambda cap: [_FlakyProvider(), _GoodProvider()])
    result = orchestrator._failover_single("history", "price_history", "AAPL")
    assert not result.empty
