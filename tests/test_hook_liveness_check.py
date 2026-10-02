"""Tests for .claude/hooks/hook_liveness_check.py -- the SessionStart check
that detects whether pre_tool_checks.py's Hard Rule #4/#5 gates are
ACTUALLY enforcing in this runtime (2026-09-09 finding: "configured in
settings.json" and "actually invoked" are different facts).

Focus: the 2026-10-02 review M4 confirmation-pass finding -- adding
pre_tool_checks.py itself to _GATE_FILES makes this scan re-evaluate commits
made BEFORE that rule existed, which would retroactively (and falsely) flag
a legitimate pre-rule commit (fe3c78e) on every SessionStart until it ages
out of the 40-commit scan window. _PRE_RULE_EXEMPT exists to prevent that
known-false alarm from masking a REAL liveness failure in the same window.
"""
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast

_HOOK_PATH = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "hook_liveness_check.py"
_spec = importlib.util.spec_from_file_location("hook_liveness_check", _HOOK_PATH)
hlc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hlc)


def _commit(sha, message, files):
    return (sha, message, files)


# ── _PRE_RULE_EXEMPT ──────────────────────────────────────────────────────────

def test_fe3c78e_is_in_the_exempt_set():
    """Pins the specific SHA the confirmation-pass finding named -- a typo'd
    or dropped entry here would silently reopen the false-alarm gap."""
    assert "fe3c78e8d60bab8d83a2e656fd63279d8c13ee34" in hlc._PRE_RULE_EXEMPT


def test_exempt_commit_touching_a_gate_file_with_no_citation_is_not_flagged(monkeypatch, capsys):
    sha = "fe3c78e8d60bab8d83a2e656fd63279d8c13ee34"
    monkeypatch.setattr(hlc, "_recent_commits", lambda: [
        _commit(sha, "fix(hooks): add 3 decision modules", [".claude/hooks/pre_tool_checks.py"]),
    ])
    assert ".claude/hooks/pre_tool_checks.py" in hlc._ptc._GATE_FILES  # sanity: real entry present
    with pytest.raises(SystemExit) as exc:
        hlc.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() == ""  # silent on success, same as "nothing found"


def test_non_exempt_commit_touching_a_gate_file_with_no_citation_is_flagged(monkeypatch, capsys):
    """Same shape as the exempt case above (a gate file, no citation) but a
    DIFFERENT sha -- proves the exemption is keyed on the specific commit,
    not on "touches this file" in general, which would silently swallow a
    real future violation on the same file."""
    monkeypatch.setattr(hlc, "_recent_commits", lambda: [
        _commit("deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                "fix(hooks): some other unreviewed change",
                [".claude/hooks/pre_tool_checks.py"]),
    ])
    with pytest.raises(SystemExit) as exc:
        hlc.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() != ""
    payload = json.loads(out)
    assert "HOOK LIVENESS" in payload["systemMessage"]
    assert "deadbee" in payload["hookSpecificOutput"]["additionalContext"]


def test_commit_with_valid_citation_is_never_flagged(monkeypatch, capsys):
    monkeypatch.setattr(hlc, "_recent_commits", lambda: [
        _commit(
            "cafecafecafecafecafecafecafecafecafecafe",
            "fix(db): some gate change\n\n"
            "Review = Opus reviewer (Opus 5): SHIP, 0 blocking; looks fine\n",
            ["stock_analyzer/db.py"],
        ),
    ])
    with pytest.raises(SystemExit) as exc:
        hlc.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == ""


def test_no_recent_commits_exits_clean(monkeypatch, capsys):
    monkeypatch.setattr(hlc, "_recent_commits", lambda: [])
    with pytest.raises(SystemExit) as exc:
        hlc.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == ""


# ── per-commit crash isolation (2026-10-02 review, final confirmation pass) ──
#
# Real production bug found and fixed in the same pass: a commit message that
# failed to decode under the locale codec (cp1252 on Windows) came back as
# `.stdout is None`, which _ptc._is_feature_commit(None) crashed on
# (`None.lstrip(...)`) -- caught by main()'s OUTER try/except, which
# discarded every violation already collected from EARLIER commits in the
# scan and silently exited 0. Fixed at the root (explicit utf-8/errors=
# "replace" decoding in _recent_commits, so a message is never None) AND
# defensively (an inner per-commit try/except in main()'s loop, so even an
# unanticipated future crash on one commit can't erase the others' results).

def test_recent_commits_never_returns_a_none_message(monkeypatch):
    """Root-cause regression: a subprocess whose .stdout decodes to None
    (the exact shape an undecodable byte under the locale codec produced
    pre-fix) must come back as "" from _recent_commits, never None --
    _ptc._is_feature_commit(None) would crash on None.lstrip(...)."""
    class _FakeResult:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout

    calls = {"n": 0}

    def _fake_run(cmd, **kw):
        # Pins the root-cause fix itself -- without this assertion, dropping
        # encoding="utf-8" would still pass this test via the `or ""`
        # defensive fallback alone, silently reopening the exact exposure
        # that caused the real 934ebc8 crash (and degrading Rule #5
        # detection for any genuinely empty-message feat( commit).
        assert kw.get("encoding") == "utf-8"
        if cmd[:2] == ["git", "log"] and "-1" in cmd:
            calls["n"] += 1
            return _FakeResult(None)  # simulates the undecodable-message case
        if cmd[:2] == ["git", "log"]:
            return _FakeResult("abc123\n")
        return _FakeResult(None)  # diff-tree call -- also simulate None
    monkeypatch.setattr(hlc.subprocess, "run", _fake_run)

    out = hlc._recent_commits()

    assert calls["n"] == 1
    assert out == [("abc123", "", [])]


def test_a_malformed_commit_entry_does_not_erase_other_violations(monkeypatch, capsys):
    """Simulates the exact pre-fix crash shape (message=None mid-scan)
    sandwiched between two real violations -- BOTH must still be reported,
    not thrown away by main()'s own exception handling. Covers the exact
    mechanism the bug had: a crash on commit N erasing violations already
    collected from commits BEFORE it (not just ones after)."""
    monkeypatch.setattr(hlc, "_recent_commits", lambda: [
        _commit("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "fix(hooks): an earlier unreviewed change",
                [".claude/hooks/pre_tool_checks.py"]),
        _commit("0000000000000000000000000000000000000a", None, []),  # the crash-shaped entry
        _commit("deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                "fix(hooks): some other unreviewed change",
                [".claude/hooks/pre_tool_checks.py"]),
    ])
    with pytest.raises(SystemExit) as exc:
        hlc.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() != ""
    payload = json.loads(out)
    assert "aaaaaaa" in payload["hookSpecificOutput"]["additionalContext"]
    assert "deadbee" in payload["hookSpecificOutput"]["additionalContext"]


def test_every_commit_failing_to_evaluate_is_surfaced_not_silent(monkeypatch, capsys):
    """The one case the per-commit try/except above still can't recover
    from on its own (every commit raises) must not look identical to a
    genuinely clean scan -- that would be the same silent-failure shape
    this whole fix chain was closing, just moved one level up."""
    monkeypatch.setattr(hlc, "_recent_commits", lambda: [
        _commit("1111111111111111111111111111111111111111", None, []),
        _commit("2222222222222222222222222222222222222222", None, []),
    ])
    with pytest.raises(SystemExit) as exc:
        hlc.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() != ""
    payload = json.loads(out)
    assert "could not evaluate" in payload["systemMessage"]
