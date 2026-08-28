"""Regression tests for stock_analyzer/db.py's read-only-viewer backstop
(set_readonly/is_readonly) — 2026-08-04 audit finding: this used to be a
bare module global, racy across concurrent Streamlit sessions (Streamlit
runs each browser session's script in its own thread within the SAME
process, so a module global is shared/racy). Fixed to be session-scoped via
st.session_state, with the module global kept only as the fallback for
callers outside a Streamlit session (the headless cron never calls
set_readonly() at all).
"""
import ast
import pathlib
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


# ─── Structural guard-coverage test (2026-08-28) ──────────────────────────────
# The tests above prove the FLAG works. They cannot prove it is APPLIED, which
# is the failure that actually happened: db.py's exemption comment named one
# ungated writer (save_fundamentals_cache, "harmless cache warming"), and by
# 2026-08-28 twelve writers were ungated — six of them LLM-narrative caches
# that cost a paid model call and persist model-authored prose the OWNER later
# reads as content. Two of those six were ALSO missing the UI `disabled=` their
# siblings carry, so both defence layers were absent at the same point.
#
# This test makes that drift impossible to repeat silently: every module-level
# write function must either carry an is_readonly() guard or be named below as
# a deliberate exemption. A new ungated writer fails the suite until someone
# adds it here on purpose — which is the point.

# Writers deliberately NOT gated: system caches that populate AUTOMATICALLY
# during a normal render, where warming on a viewer's visit is harmless and
# desirable, plus one cron-only writer no viewer path reaches. Adding to this
# list is a security decision — state why in the commit body.
_UNGATED_BY_DESIGN = {
    "save_fundamentals_cache",          # bundle_loader, on load
    "save_sector_cache",                # bundle_loader, on load
    "save_sentiment_llm_cache",         # bundle_loader, on load
    "save_price_xcheck_history_batch",  # app.py, on render; no LLM, idempotent
    "save_alert_state",                 # cron-only; no viewer code path
    # .rpc() counter. Gating this would be an active DEFECT, not merely
    # redundant: providers/fmp_provider.py calls it on eight real API-call
    # paths, so a guard would make the counter under-report calls that genuinely
    # happened and could mask an approaching rate limit.
    "increment_daily_quota",
}

# `rpc` is included because db.py mutates through Postgres functions too --
# increment_daily_quota writes via _client().rpc(...), which an insert/upsert/
# update/delete-only tuple misses entirely. Omitting it made this test's own
# assertion message ("these have no guard and are not exempt") a claim it could
# not back: a real ungated writer sat outside the sweep while the suite was
# green. `update` also matches dict.update(); the over-inclusion is deliberate
# -- a false positive here costs one allowlist review, a false negative costs a
# silent write hole. (2026-08-28 review.)
_WRITE_CALLS = ("insert", "upsert", "update", "delete", "rpc")


def _write_functions():
    """(name, is_guarded) for every module-level function in db.py that writes."""
    src = pathlib.Path(db.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    out = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        seg = ast.get_source_segment(src, node) or ""
        if not any(f".{w}(" in seg for w in _WRITE_CALLS):
            continue
        # `"if is_readonly()"`, not a bare `"is_readonly()"`: db.py carries 8
        # docstring/comment mentions of the form "is_readonly() resolves False
        # in the headless cron", so the loose match lets a new writer that
        # copies that boilerplate and forgets the guard LINE pass silently.
        # Verified free: strict and loose both mark 48/48 currently-guarded
        # writers, so this costs zero churn today.
        out.append((node.name, "if is_readonly()" in seg))
    return out


def test_every_db_writer_is_readonly_guarded_or_explicitly_exempt():
    ungated = {name for name, guarded in _write_functions() if not guarded}
    unexpected = ungated - _UNGATED_BY_DESIGN
    assert not unexpected, (
        "These db.py write functions have no is_readonly() guard and are not on "
        f"the deliberate-exemption list: {sorted(unexpected)}.\n"
        "A read-only viewer could mutate data through them. Either add "
        "`if is_readonly(): return ...` or, if the write is genuinely a "
        "harmless auto-warmed system cache, add it to _UNGATED_BY_DESIGN here "
        "AND to the exemption comment in db.py — and say why in the commit."
    )


def test_exemption_list_has_no_stale_entries():
    """The allowlist must not outlive its debt — if an exempt writer later gains
    a guard, this fails so the entry gets removed rather than quietly lingering
    and masking a future regression on that same name."""
    all_writers = dict(_write_functions())
    stale = {n for n in _UNGATED_BY_DESIGN if all_writers.get(n) is True}
    missing = {n for n in _UNGATED_BY_DESIGN if n not in all_writers}
    assert not stale, f"Now guarded — remove from _UNGATED_BY_DESIGN: {sorted(stale)}"
    assert not missing, f"No longer a db.py writer — remove from _UNGATED_BY_DESIGN: {sorted(missing)}"


def test_llm_narrative_caches_are_all_guarded():
    """Named explicitly, not just covered by the set test above: these seven are
    the paid-LLM-narrative class the 2026-08-28 fix was about — six
    button-triggered, one (save_thesis_erosion_cache) automatic-on-render. The
    criterion is that each pays for a model call and persists prose onto the
    owner's own decision surfaces; the TRIGGER is not the criterion, which is
    exactly the conflation that left the seventh exempt for a release."""
    guarded = dict(_write_functions())
    for name in (
        "save_debate_cache", "save_structural_scan_cache",
        "save_regime_scenario_cache", "save_catalyst_stress_cache",
        "save_thesis_cluster_cache", "save_missed_opportunity_cache",
        # Added 2026-08-28: automatic-on-render, but it pays for a Haiku call
        # and its prose feeds debate_agent.build_exit_corpus.
        "save_thesis_erosion_cache",
    ):
        assert guarded.get(name) is True, f"{name} lost its read-only guard"
