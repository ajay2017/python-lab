"""Tests for the native-git enforcement adapter.

WHY THIS FILE MATTERS MORE THAN A NORMAL TEST MODULE. Claude Code's PreToolUse
hooks are permanently dead in this org (`allowManagedHooksOnly: true`, exception
declined 2026-10-08), so `.claude/hooks/git_hook_adapter.py` is the ONLY live
enforcement of the commit gates. It shipped 2026-10-07 with zero test coverage.

The highest-stakes behaviour here is `_scrub_git_env()`. v1 of the adapter
spawned pytest from a git hook WITHOUT scrubbing git's exported environment, and
review reproduced a corrupted index, a rewritten `.git/config`, and ~57-deep
hook recursion against the real repository. These tests exercise the scrub
through real child processes, because the property that matters is what a CHILD
sees -- asserting on the dict alone would pass even if the scrub were a no-op
for subprocesses.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

_HOOKS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".claude", "hooks"
)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import git_hook_adapter as adapter  # noqa: E402


_DANGEROUS = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE")


def _child_git_dir(env=None) -> str:
    """What a freshly-spawned git child resolves as its repo."""
    r = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    return (r.stdout or r.stderr).strip()


def _git_dir_with_clean_env() -> str:
    """The answer a child SHOULD get once the scrub has run.

    Computed, never assumed: in a linked worktree this is
    `.../.git/worktrees/<name>`, not `.../.git`. This repo runs agent-isolation
    worktrees, and `core.hooksPath` is set in the SHARED config, so these hooks
    (and therefore this test) really do execute there.
    """
    env = {
        k: v for k, v in os.environ.items()
        if k not in set(adapter._GIT_ENV_FALLBACK)
    }
    return _child_git_dir(env=env)


def test_scrub_removes_gits_exported_variables(monkeypatch):
    for name in _DANGEROUS:
        monkeypatch.setenv(name, "/some/bogus/path")

    removed = adapter._scrub_git_env()

    for name in _DANGEROUS:
        assert name not in os.environ, f"{name} survived the scrub"
        assert name in removed, f"{name} was not reported as removed"


def test_scrub_redirects_a_real_child_back_to_the_real_repo(monkeypatch, tmp_path):
    """The v1 corruption property, tested end to end.

    Asserts BOTH directions: the bogus GIT_DIR must actually capture a child
    first, and the scrub must then release it. Without the 'before' assertion
    this would pass against a scrub that did nothing, which is exactly the
    vacuous-comparison trap this repo has hit twice before.
    """
    expected = _git_dir_with_clean_env()
    bogus = tmp_path / "nonexistent-git-dir"
    monkeypatch.setenv("GIT_DIR", str(bogus))
    monkeypatch.setenv("GIT_INDEX_FILE", str(bogus / "index"))

    before = _child_git_dir()
    assert "nonexistent-git-dir" in before.replace("\\", "/"), (
        "precondition failed: the bogus GIT_DIR never captured the child, so "
        "this test cannot prove the scrub released it"
    )

    adapter._scrub_git_env()
    after = _child_git_dir()

    assert after == expected
    assert before != after


def test_scrub_still_removes_the_dangerous_set_when_the_git_query_fails(monkeypatch):
    """A failing `git rev-parse --local-env-vars` must degrade to the hard-coded
    fallback, never to scrubbing nothing -- a silent no-op here reinstates the
    exact corruption path the scrub exists to close."""
    for name in _DANGEROUS:
        monkeypatch.setenv(name, "/some/bogus/path")

    class _Failed:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(adapter, "_git", lambda *a, **k: _Failed())

    removed = adapter._scrub_git_env()

    for name in _DANGEROUS:
        assert name not in os.environ
        assert name in removed


def test_fallback_list_covers_what_this_git_actually_exports():
    """Guards against a future git adding a redirect variable the hard-coded
    fallback does not know about. If this fails, add the new name to
    `_GIT_ENV_FALLBACK` -- do not delete the assertion."""
    r = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        pytest.skip("git could not report --local-env-vars")
    live = {n.strip() for n in r.stdout.split() if n.strip()}
    missing = live - set(adapter._GIT_ENV_FALLBACK)
    assert not missing, f"not in _GIT_ENV_FALLBACK: {sorted(missing)}"


def test_every_hook_shim_invokes_its_own_mode():
    """A shim that passes the wrong mode silently runs the wrong gate. v2 of
    this adapter shipped exactly that class of argv bug.

    The mapping is asserted LITERALLY, not derived from `_HOOKS`. Comparing each
    shim against the dict that generated it is circular -- swapping two values
    would satisfy it while installing the wrong gate under each name.
    """
    assert adapter._HOOKS == {
        "commit-msg": "commit",
        "pre-commit": "pre-commit",
        "pre-push": "pre-push",
    }
    for hook_name, mode in adapter._HOOKS.items():
        shim = adapter._SHIM.format(mode=mode)
        assert f'"$ADAPTER" {mode} "$@"' in shim, hook_name
        assert shim.startswith("#!/bin/sh"), hook_name


def test_install_writes_all_three_hooks(monkeypatch, tmp_path):
    hooks_dir = tmp_path / ".git" / "hooks"

    class _Ok:
        returncode = 0
        stdout = str(hooks_dir)
        stderr = ""

    monkeypatch.setattr(adapter, "_git", lambda *a, **k: _Ok())
    assert adapter._install() == 0

    for hook_name, mode in adapter._HOOKS.items():
        path = hooks_dir / hook_name
        assert path.is_file(), f"{hook_name} was not installed"
        assert f'"$ADAPTER" {mode} "$@"' in path.read_text()


def test_clean_message_cuts_the_verbose_diff_below_the_scissors():
    """`git commit -v` appends the staged diff. This repo's own docs contain
    literal review-citation lines, so a diff left in place can forge a citation
    the author never wrote -- reproduced in the 2026-10-07 review."""
    import pre_tool_checks as ptc

    raw = (
        "chore(x): a commit with no citation\n"
        "\n"
        "# ------------------------ >8 ------------------------\n"
        "diff --git a/docs/cost-routing.md b/docs/cost-routing.md\n"
        "+Review = Opus reviewer (claude-opus-5): SHIP, 0 blocking; forged\n"
    )
    # Precondition: the forged line WOULD satisfy the gate if left in place.
    assert ptc._has_review_citation(raw)

    cleaned = adapter._clean_message(raw)
    assert "forged" not in cleaned
    assert not ptc._has_review_citation(cleaned)


def test_pre_commit_skips_both_gates_when_no_relevant_paths_are_staged(monkeypatch, capsys):
    """A docs-only commit must not pay the suite's runtime -- and must SAY that
    nothing ran, rather than printing a bare pass line that reads as cover."""
    monkeypatch.setattr(adapter, "_changed_files", lambda: ["docs/architecture.md"])

    def _explode(*a, **k):
        raise AssertionError("the gates must not be started for a docs-only commit")

    sys.path.insert(0, _HOOKS_DIR)
    import pre_tool_checks as ptc

    monkeypatch.setattr(ptc, "_run_gates_concurrently", _explode)

    assert adapter._run_test_gates("pre-commit") == 0
    err = capsys.readouterr().err
    assert "N/A" in err and "pre-commit" in err


def _stub_gates(monkeypatch, result, record=None):
    """Replace the real pytest/antipattern runner with a canned verdict."""
    import pre_tool_checks as ptc

    def _fake(need_pytest, need_antipattern):
        if record is not None:
            record["need_pytest"] = need_pytest
            record["need_antipattern"] = need_antipattern
            record["env_clean"] = "GIT_INDEX_FILE" not in os.environ
        return result

    monkeypatch.setattr(ptc, "_run_gates_concurrently", _fake)


def test_staged_files_are_resolved_before_the_scrub(monkeypatch):
    """The ordering inside `_run_test_gates` is load-bearing and invisible.

    `git diff --cached` needs the inherited GIT_INDEX_FILE to see a `git commit
    -a` temporary index; pytest must NOT inherit it. Scrubbing first makes the
    staged list come back EMPTY, so both gates are skipped and the hook prints a
    clean pass -- a silent no-op. Nothing in the control flow signals this, so
    it is pinned here.
    """
    monkeypatch.setenv("GIT_INDEX_FILE", "/bogus/index")
    seen = {}

    def _fake_changed():
        seen["env_at_changed_files"] = "GIT_INDEX_FILE" in os.environ
        return ["stock_analyzer/scoring.py"]

    monkeypatch.setattr(adapter, "_changed_files", _fake_changed)
    record = {}
    _stub_gates(monkeypatch, {"pytest": (True, ""), "antipattern": (True, "")}, record)

    assert adapter._run_test_gates("pre-commit") == 0
    assert seen["env_at_changed_files"] is True, (
        "the staged list was resolved AFTER the scrub -- `-a` commits will "
        "silently skip their gates"
    )
    assert record["env_clean"] is True, (
        "pytest was spawned with git's environment still set -- this is the v1 "
        "repo-corruption path"
    )


@pytest.mark.parametrize(
    "pytest_verdict, expected_exit, why",
    [
        ((True, ""), 0, "a passing suite allows the commit"),
        ((False, "3 failed"), 1, "a failing suite blocks"),
        ((None, ".venv python not found"), 1, "an UNRUNNABLE suite must also block"),
    ],
)
def test_pre_commit_blocks_unless_pytest_actually_passed(
    monkeypatch, pytest_verdict, expected_exit, why
):
    monkeypatch.setattr(
        adapter, "_changed_files", lambda: ["stock_analyzer/scoring.py"]
    )
    _stub_gates(monkeypatch, {"pytest": pytest_verdict, "antipattern": (True, "")})
    assert adapter._run_test_gates("pre-commit") == expected_exit, why


def test_pre_commit_blocks_on_a_new_antipattern(monkeypatch):
    monkeypatch.setattr(
        adapter, "_changed_files", lambda: ["stock_analyzer/scoring.py"]
    )
    _stub_gates(
        monkeypatch,
        {"pytest": (True, ""), "antipattern": (False, "NEW: util.py:1 bare or {}")},
    )
    assert adapter._run_test_gates("pre-commit") == 1


def test_pre_commit_requests_pytest_for_a_tested_path(monkeypatch):
    """Guards the inverse of the skip test: a real code path must actually ASK
    for the suite, not merely fail to skip."""
    monkeypatch.setattr(adapter, "_changed_files", lambda: ["app.py"])
    record = {}
    _stub_gates(monkeypatch, {"pytest": (True, ""), "antipattern": (True, "")}, record)

    assert adapter._run_test_gates("pre-commit") == 0
    assert record["need_pytest"] is True
    assert record["need_antipattern"] is True


def _stub_stdin(monkeypatch, text):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_pre_push_runs_the_suite_for_a_real_push(monkeypatch):
    _stub_stdin(
        monkeypatch, "refs/heads/main abc123 refs/heads/main def456\n"
    )
    monkeypatch.setattr(adapter, "_push_tree_mismatch", lambda refs, ptc: [])
    record = {}
    _stub_gates(monkeypatch, {"pytest": (True, "")}, record)

    assert adapter._run_test_gates("pre-push") == 0
    assert record["need_pytest"] is True, "pre-push must actually request the suite"


def test_pre_push_blocks_when_the_suite_fails(monkeypatch):
    _stub_stdin(monkeypatch, "refs/heads/main abc123 refs/heads/main def456\n")
    monkeypatch.setattr(adapter, "_push_tree_mismatch", lambda refs, ptc: [])
    _stub_gates(monkeypatch, {"pytest": (False, "1 failed")})
    assert adapter._run_test_gates("pre-push") == 1


def test_pre_push_skips_a_deletion_only_push(monkeypatch, capsys):
    """An all-zero LOCAL sha is a branch deletion -- no code is going out, so
    paying the suite's runtime would be pure cost."""
    _stub_stdin(
        monkeypatch,
        "(delete) " + "0" * 40 + " refs/heads/old " + "0" * 40 + "\n",
    )

    def _explode(*a, **k):
        raise AssertionError("the suite must not run for a deletion-only push")

    import pre_tool_checks as ptc

    monkeypatch.setattr(ptc, "_run_gates_concurrently", _explode)

    assert adapter._run_test_gates("pre-push") == 0
    assert "deletions only" in capsys.readouterr().err


def test_pre_push_warns_when_the_tree_is_not_what_is_being_pushed(monkeypatch, capsys):
    """A green suite describes the WORKING TREE. When that is not what is going
    out, the pass must say so -- otherwise `--no-verify` a broken commit, fix it
    in the tree without committing, and push ships the broken one silently."""
    _stub_stdin(monkeypatch, "refs/heads/main abc123 refs/heads/main def456\n")
    monkeypatch.setattr(
        adapter, "_push_tree_mismatch",
        lambda refs, ptc: ["tested files are modified in your working tree"],
    )
    _stub_gates(monkeypatch, {"pytest": (True, "")})

    assert adapter._run_test_gates("pre-push") == 0
    assert "MISMATCH WARNING" in capsys.readouterr().err


def test_parse_push_refs_drops_deletions_and_keeps_real_refs(monkeypatch):
    _stub_stdin(
        monkeypatch,
        "refs/heads/main aaa refs/heads/main bbb\n"
        "(delete) " + "0" * 40 + " refs/heads/gone " + "0" * 40 + "\n"
        "\n",
    )
    assert adapter._parse_push_refs() == [("refs/heads/main", "aaa")]


class _Ptc:
    """Stand-in for pre_tool_checks with a pinned dirty-tree answer."""

    def __init__(self, dirty):
        self._dirty = dirty

    def _tested_paths_dirty(self):
        return self._dirty


@pytest.mark.parametrize(
    "dirty, refs, expect_substring",
    [
        (True, [("refs/heads/main", "HEADSHA")], "working tree"),
        (False, [("refs/heads/other", "OTHER")], "not the checked-out HEAD"),
    ],
)
def test_push_tree_mismatch_names_each_reason(monkeypatch, dirty, refs, expect_substring):
    monkeypatch.setattr(adapter, "_git_lines", lambda *a: ["HEADSHA"])
    reasons = adapter._push_tree_mismatch(refs, _Ptc(dirty))
    assert any(expect_substring in r for r in reasons), reasons


def test_push_tree_mismatch_is_silent_for_a_clean_head_push(monkeypatch):
    monkeypatch.setattr(adapter, "_git_lines", lambda *a: ["HEADSHA"])
    assert adapter._push_tree_mismatch([("refs/heads/main", "HEADSHA")], _Ptc(False)) == []


def test_push_tree_mismatch_ignores_tag_refs(monkeypatch):
    """An annotated tag's local sha is the TAG OBJECT's, never a commit sha, so
    comparing it against HEAD would warn on every single tag push. A warning
    that fires when nothing is wrong stops being read."""
    monkeypatch.setattr(adapter, "_git_lines", lambda *a: ["HEADSHA"])
    assert adapter._push_tree_mismatch([("refs/tags/v1.0", "TAGOBJ")], _Ptc(False)) == []


def test_unreadable_push_stdin_runs_the_suite_instead_of_skipping(monkeypatch):
    """`None` means "I don't know what's being pushed" and must never collapse
    into "nothing is being pushed" -- that would exit 0 having checked nothing."""
    class _Exploding:
        def read(self):
            raise OSError("stdin is gone")

    monkeypatch.setattr(sys, "stdin", _Exploding())
    assert adapter._parse_push_refs() is None

    monkeypatch.setattr(sys, "stdin", _Exploding())
    monkeypatch.setattr(adapter, "_push_tree_mismatch", lambda refs, ptc: [])
    record = {}
    _stub_gates(monkeypatch, {"pytest": (True, "")}, record)

    assert adapter._run_test_gates("pre-push") == 0
    assert record["need_pytest"] is True, "an unreadable ref list must not skip the suite"


def test_main_fails_closed_when_a_gate_mode_raises(monkeypatch, capsys):
    """The fail-closed wrapper must cover the new modes too, not just `commit`."""
    def _boom(_mode):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(adapter, "_run_test_gates", _boom)
    monkeypatch.setattr(sys, "argv", ["git_hook_adapter.py", "pre-push"])

    with pytest.raises(SystemExit) as exc:
        adapter.main()
    assert exc.value.code == 1
    assert "NOT a pass" in capsys.readouterr().err
