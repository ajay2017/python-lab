"""Regression tests for stock_analyzer/db.py's read-only-viewer backstop
(set_readonly/is_readonly) — 2026-08-04 audit finding: this used to be a
bare module global, racy across concurrent Streamlit sessions (Streamlit
runs each browser session's script in its own thread within the SAME
process, so a module global is shared/racy). Fixed to be session-scoped via
st.session_state, with the module global kept only as the fallback for
callers outside a Streamlit session (the headless cron never calls
set_readonly() at all).
"""
import sys
import types

import pytest

from stock_analyzer import db


class _FakeSessionState(dict):
    """Minimal stand-in for st.session_state — dict-like, supports `in`."""


class _FakeStreamlit(types.ModuleType):
    def __init__(self, session_state):
        super().__init__("streamlit")
        self.session_state = session_state


@pytest.fixture(autouse=True)
def _cleanup():
    """Reset the module-global fallback and remove any fake streamlit after
    each test, so tests can't leak state into each other or into the real
    suite (many other tests call db.* writer functions elsewhere)."""
    yield
    db._READONLY = False
    sys.modules.pop("streamlit", None)


def test_is_readonly_defaults_false_with_no_streamlit_session():
    sys.modules.pop("streamlit", None)  # simulate bare/no-Streamlit context
    assert db.is_readonly() is False


def test_set_readonly_true_then_false_via_module_fallback():
    """No fake streamlit installed -> falls through to the module global,
    exactly the pre-fix behavior (still correct for a single-session /
    headless-cron caller)."""
    sys.modules.pop("streamlit", None)
    db.set_readonly(True)
    assert db.is_readonly() is True
    db.set_readonly(False)
    assert db.is_readonly() is False


def test_set_readonly_writes_to_session_state_not_just_the_global():
    fake_state = _FakeSessionState()
    sys.modules["streamlit"] = _FakeStreamlit(fake_state)
    db.set_readonly(True)
    assert fake_state["_db_readonly"] is True


def test_is_readonly_prefers_session_state_over_stale_module_global():
    """The exact race this fix closes: two 'sessions' sharing one process —
    session A's module-global write must not leak into session B's
    session-state-scoped read."""
    db._READONLY = True  # simulate a stale/racing module-global from another session
    fake_state = _FakeSessionState({"_db_readonly": False})
    sys.modules["streamlit"] = _FakeStreamlit(fake_state)
    assert db.is_readonly() is False  # session state wins, not the stale global


def test_is_readonly_falls_back_to_global_when_session_state_key_absent():
    db._READONLY = True
    fake_state = _FakeSessionState()  # key never set for this session
    sys.modules["streamlit"] = _FakeStreamlit(fake_state)
    assert db.is_readonly() is True
