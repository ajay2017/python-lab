"""#34c C1: the real `snaptrade_client.list_connections` body.

Every other test sees it through the autouse conftest stub (no test may reach the
live SnapTrade API), so this file loads a PRIVATE copy of the module from its
source file (its own globals; the real module and the stub are never touched) and
drives that copy's `list_connections` with a fake SDK client."""
import importlib.util
import types

import pytest

from stock_analyzer import snaptrade_client as _real_module

pytestmark = pytest.mark.fast


@pytest.fixture
def stc():
    """A fresh, unstubbed copy of stock_analyzer/snaptrade_client.py."""
    spec = importlib.util.spec_from_file_location("_snaptrade_client_unstubbed", _real_module.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def real_list_connections(stc):
    return stc.list_connections


class _FakeSdk:
    def __init__(self, body=None, raises=None):
        self.calls = 0
        outer = self

        def list_brokerage_authorizations():
            outer.calls += 1
            if raises is not None:
                raise raises
            return types.SimpleNamespace(body=body)

        self.connections = types.SimpleNamespace(
            list_brokerage_authorizations=list_brokerage_authorizations)


def _configure(monkeypatch, stc, sdk, *, configured=True):
    monkeypatch.setattr(stc, "has_snaptrade", lambda: configured)
    monkeypatch.setattr(stc, "_client", lambda: sdk)
    # Keep the shared in-memory api_health untouched; tests that check the
    # recording re-patch these after this call.
    monkeypatch.setattr(stc, "_record_success", lambda: None)
    monkeypatch.setattr(stc, "_record_error", lambda exc: None)


def test_returns_the_body_unchanged(real_list_connections, stc, monkeypatch):
    body = [{"id": "a", "disabled": False}, {"id": "b", "disabled": True}]
    sdk = _FakeSdk(body=body)
    _configure(monkeypatch, stc, sdk)
    assert real_list_connections() is body
    assert sdk.calls == 1


def test_goes_through_call_with_timeout_with_the_module_timeout(real_list_connections, stc, monkeypatch):
    seen = {}

    def _spy(fn, args, kwargs, timeout):
        seen["args"], seen["kwargs"], seen["timeout"] = args, kwargs, timeout
        return fn(*args, **kwargs)

    sdk = _FakeSdk(body=[])
    _configure(monkeypatch, stc, sdk)
    monkeypatch.setattr(stc, "_call_with_timeout", _spy)
    assert real_list_connections() == []
    assert seen["timeout"] == stc.SNAPTRADE_REQUEST_TIMEOUT_SEC
    assert seen["args"] == () and seen["kwargs"] == {}


def test_none_when_the_sdk_call_raises_and_the_error_is_recorded(real_list_connections, stc, monkeypatch):
    recorded = []
    sdk = _FakeSdk(raises=RuntimeError("boom"))
    _configure(monkeypatch, stc, sdk)
    monkeypatch.setattr(stc, "_record_error", lambda exc: recorded.append(exc))
    assert real_list_connections() is None
    assert len(recorded) == 1


def test_none_when_the_call_times_out(real_list_connections, stc, monkeypatch):
    def _timeout(fn, args, kwargs, timeout):
        raise TimeoutError("timed out")

    _configure(monkeypatch, stc, _FakeSdk(body=[]))
    monkeypatch.setattr(stc, "_call_with_timeout", _timeout)
    assert real_list_connections() is None


def test_none_when_not_configured_and_no_call_is_made(real_list_connections, stc, monkeypatch):
    sdk = _FakeSdk(body=[{"id": "a"}])
    _configure(monkeypatch, stc, sdk, configured=False)
    assert real_list_connections() is None
    assert sdk.calls == 0


def test_success_is_recorded_on_the_api_health_path(real_list_connections, stc, monkeypatch):
    ok = []
    _configure(monkeypatch, stc, _FakeSdk(body=[]))
    monkeypatch.setattr(stc, "_record_success", lambda: ok.append(True))
    real_list_connections()
    assert ok == [True]
