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
import shutil
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

_REAL_RECORD_BLOCK = adapter._record_block
_BLOCKS_RECORDED: list = []


@pytest.fixture(autouse=True)
def _no_real_block_log_by_default(monkeypatch):
    """Many tests below drive a real BLOCK from the real repo's cwd; without
    this each one would overwrite the repo's own .git/last-hook-block.log --
    the very file a real block is meant to leave behind. Calls are captured in
    _BLOCKS_RECORDED instead; the block-log tests opt back in."""
    _BLOCKS_RECORDED.clear()

    def _capture(mode, text):
        _BLOCKS_RECORDED.append((mode, text))
        return None

    monkeypatch.setattr(adapter, "_record_block", _capture)


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

    # A diff CONTEXT line (leading space) -- since 2026-10-12 the citation must
    # start its line (optionally indented), so a `+`-prefixed added line no
    # longer forges one, but an unchanged context line still would.
    raw = (
        "chore(x): a commit with no citation\n"
        "\n"
        "# ------------------------ >8 ------------------------\n"
        "diff --git a/docs/cost-routing.md b/docs/cost-routing.md\n"
        " Review = Opus reviewer (claude-opus-5): SHIP, 0 blocking; forged\n"
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


def _fake_git_lines(peel=None):
    """`rev-parse HEAD` -> HEADSHA; `rev-parse <sha>^{commit}` -> peel[sha]
    (default: the sha itself, i.e. it already IS a commit)."""
    peel = peel or {}

    def _lines(*args):
        if args == ("rev-parse", "HEAD"):
            return ["HEADSHA"]
        if args[0] == "rev-parse" and args[1].endswith("^{commit}"):
            sha = args[1][: -len("^{commit}")]
            return [peel.get(sha, sha)]
        raise AssertionError(f"unexpected git call {args}")
    return _lines


@pytest.mark.parametrize(
    "dirty, refs, expect_substring",
    [
        (True, [("refs/heads/main", "HEADSHA")], "working tree"),
        (False, [("refs/heads/other", "OTHER")], "not the checked-out HEAD"),
    ],
)
def test_push_tree_mismatch_names_each_reason(monkeypatch, dirty, refs, expect_substring):
    monkeypatch.setattr(adapter, "_git_lines", _fake_git_lines())
    reasons = adapter._push_tree_mismatch(refs, _Ptc(dirty))
    assert any(expect_substring in r for r in reasons), reasons


def test_push_tree_mismatch_is_silent_for_a_clean_head_push(monkeypatch):
    monkeypatch.setattr(adapter, "_git_lines", _fake_git_lines())
    assert adapter._push_tree_mismatch([("refs/heads/main", "HEADSHA")], _Ptc(False)) == []


def test_push_tree_mismatch_ignores_tag_refs(monkeypatch):
    """An annotated tag's local sha is the TAG OBJECT's, never a commit sha, so
    comparing it against HEAD would warn on every single tag push. A warning
    that fires when nothing is wrong stops being read."""
    monkeypatch.setattr(adapter, "_git_lines", _fake_git_lines())
    assert adapter._push_tree_mismatch([("refs/tags/v1.0", "TAGOBJ")], _Ptc(False)) == []


def test_push_tree_mismatch_warns_for_a_sha_expression_refspec(monkeypatch):
    """Audit C-M1, reproduced: for `git push origin HEAD~2:refs/heads/older`
    git reports the local ref as `HEAD~2`, which the old `refs/heads/`-only
    filter skipped -- the one push most likely to ship something other than
    HEAD printed no warning."""
    monkeypatch.setattr(adapter, "_git_lines", _fake_git_lines())
    reasons = adapter._push_tree_mismatch([("HEAD~2", "OLDERSHA")], _Ptc(False))
    assert any("HEAD~2" in r and "not the checked-out HEAD" in r for r in reasons), reasons


def test_push_tree_mismatch_compares_the_peeled_commit(monkeypatch):
    """A non-tag ref whose object peels to HEAD's commit is not a mismatch."""
    monkeypatch.setattr(adapter, "_git_lines", _fake_git_lines({"OBJ": "HEADSHA"}))
    assert adapter._push_tree_mismatch([("refs/heads/main", "OBJ")], _Ptc(False)) == []


def test_push_tree_mismatch_unresolvable_sha_warns(monkeypatch):
    """If git can't peel the sha, warn (the safe direction for a warning)."""
    def _lines(*args):
        if args == ("rev-parse", "HEAD"):
            return ["HEADSHA"]
        raise RuntimeError("bad object")
    monkeypatch.setattr(adapter, "_git_lines", _lines)
    reasons = adapter._push_tree_mismatch([("refs/heads/x", "BADSHA")], _Ptc(False))
    assert any("not the checked-out HEAD" in r for r in reasons), reasons


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


# ---------------------------------------------------------------------------
# Snapshot testing (audit C-H1, 2026-10-12): pre-commit gates run in a temporary
# worktree built from the INDEX, not against whatever is on disk.
# ---------------------------------------------------------------------------

_REAL_WRITE_TREE = adapter._write_tree


@pytest.fixture(autouse=True)
def _no_real_snapshot_by_default(monkeypatch):
    """Keep the pre-existing control-flow tests off the real repository: without
    this every `_run_test_gates("pre-commit")` call above would build (and tear
    down) a real worktree of the real index. Snapshot tests opt back in with
    `_use_real_snapshot`."""
    monkeypatch.setattr(
        adapter, "_write_tree", lambda: (None, "snapshot disabled in unit test")
    )


def _use_real_snapshot(monkeypatch):
    monkeypatch.setattr(adapter, "_write_tree", _REAL_WRITE_TREE)


def _sg(repo, *args):
    """Run git in `repo` with git's exported variables removed."""
    env = {k: v for k, v in os.environ.items() if k not in set(adapter._GIT_ENV_FALLBACK)}
    r = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _scratch_repo(tmp_path, monkeypatch):
    """A real throwaway repo with one committed file, cwd set into it, and git's
    inherited variables removed so nothing can reach the real repository."""
    for name in adapter._GIT_ENV_FALLBACK:
        monkeypatch.delenv(name, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    _sg(repo, "init", "-q")
    _sg(repo, "config", "user.name", "t")
    _sg(repo, "config", "user.email", "t@example.com")
    _sg(repo, "config", "commit.gpgsign", "false")
    (repo / "a.py").write_text("v0\n")
    _sg(repo, "add", "a.py")
    _sg(repo, "commit", "-q", "-m", "init")
    monkeypatch.chdir(repo)
    return repo


def _worktree_paths(repo):
    return [
        ln[len("worktree "):]
        for ln in _sg(repo, "worktree", "list", "--porcelain").splitlines()
        if ln.startswith("worktree ")
    ]


def _same(a, b):
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


def test_snapshot_is_written_before_the_scrub_and_worktree_added_after(monkeypatch, tmp_path):
    """Same silent-ordering hazard as the staged-list test: `write-tree` needs the
    inherited GIT_INDEX_FILE (a `-a` commit's temporary index), while `worktree
    add` must NOT see it (it could write through the in-progress commit's index)."""
    monkeypatch.setenv("GIT_INDEX_FILE", "/bogus/index")
    order = []
    env = {}

    def _wt():
        order.append("write_tree")
        env["write_tree"] = "GIT_INDEX_FILE" in os.environ
        return "TREESHA", ""

    real_scrub = adapter._scrub_git_env

    def _scrub():
        order.append("scrub")
        return real_scrub()

    def _add(tree):
        order.append("add")
        env["add"] = "GIT_INDEX_FILE" in os.environ
        assert tree == "TREESHA"
        return str(tmp_path), str(tmp_path)

    monkeypatch.setattr(adapter, "_write_tree", _wt)
    monkeypatch.setattr(adapter, "_scrub_git_env", _scrub)
    monkeypatch.setattr(adapter, "_add_snapshot_worktree", _add)
    monkeypatch.setattr(adapter, "_remove_snapshot", lambda p, w: order.append("remove"))
    monkeypatch.setattr(adapter, "_changed_files", lambda: ["app.py"])
    _stub_gates(monkeypatch, {"pytest": (True, ""), "antipattern": (True, "")})

    assert adapter._run_test_gates("pre-commit") == 0
    assert order == ["write_tree", "scrub", "add", "remove"]
    assert env["write_tree"] is True, "write-tree ran after the scrub: -a commits snapshot the wrong index"
    assert env["add"] is False, "worktree add ran with git's index variable still set"


def test_snapshot_holds_the_staged_content_not_the_working_tree(monkeypatch, tmp_path):
    """C-H1 case 1: stage v1, change the disk to v2 without staging."""
    repo = _scratch_repo(tmp_path, monkeypatch)
    (repo / "a.py").write_text("v1\n")
    _sg(repo, "add", "a.py")
    (repo / "a.py").write_text("v2\n")  # unstaged fix on disk

    tree, why = _REAL_WRITE_TREE()
    assert tree, why
    parent, wt = adapter._add_snapshot_worktree(tree)
    try:
        with open(os.path.join(wt, "a.py")) as fh:
            assert fh.read() == "v1\n", "snapshot must hold the STAGED version"
        assert (repo / "a.py").read_text() == "v2\n", "contrast: the working tree still holds v2"
    finally:
        os.chdir(repo)
        adapter._remove_snapshot(parent, wt)


def test_snapshot_omits_untracked_files_but_keeps_tracked_ones(monkeypatch, tmp_path):
    """C-H1 case 2: a staged test importing an UNTRACKED module must fail in the
    snapshot, because that module would not be in the commit."""
    repo = _scratch_repo(tmp_path, monkeypatch)
    (repo / "untracked_mod.py").write_text("x = 1\n")

    tree, why = _REAL_WRITE_TREE()
    assert tree, why
    parent, wt = adapter._add_snapshot_worktree(tree)
    try:
        assert os.path.isfile(os.path.join(wt, "a.py")), "contrast: a tracked file IS present"
        assert not os.path.exists(os.path.join(wt, "untracked_mod.py"))
        assert (repo / "untracked_mod.py").exists(), "contrast: it is still on disk"
    finally:
        adapter._remove_snapshot(parent, wt)


def test_gates_run_inside_the_snapshot_and_cwd_is_restored(monkeypatch, tmp_path):
    repo = _scratch_repo(tmp_path, monkeypatch)
    _use_real_snapshot(monkeypatch)
    monkeypatch.setattr(adapter, "_changed_files", lambda: ["app.py"])
    seen = {}
    real_add = adapter._add_snapshot_worktree

    def _spy(tree):
        parent, wt = real_add(tree)
        seen["wt"] = wt
        return parent, wt

    monkeypatch.setattr(adapter, "_add_snapshot_worktree", _spy)
    record = {}

    import pre_tool_checks as ptc

    def _fake(need_pytest, need_antipattern):
        record["cwd"] = os.getcwd()
        with open("a.py") as fh:
            record["a"] = fh.read()
        return {"pytest": (True, ""), "antipattern": (True, "")}

    monkeypatch.setattr(ptc, "_run_gates_concurrently", _fake)
    before = os.getcwd()
    assert adapter._run_test_gates("pre-commit") == 0
    assert _same(record["cwd"], seen["wt"]) and not _same(record["cwd"], repo)
    assert record["a"] == "v0\n"
    assert _same(os.getcwd(), before), "cwd must be restored afterwards"


def test_snapshot_is_cleaned_up_even_when_the_gates_raise(monkeypatch, tmp_path):
    repo = _scratch_repo(tmp_path, monkeypatch)
    other = tmp_path / "other_wt"
    _sg(repo, "worktree", "add", "--detach", str(other))
    _use_real_snapshot(monkeypatch)
    monkeypatch.setattr(adapter, "_changed_files", lambda: ["app.py"])
    seen = {}
    real_add = adapter._add_snapshot_worktree

    def _spy(tree):
        parent, wt = real_add(tree)
        seen["parent"], seen["wt"] = parent, wt
        assert any(_same(wt, p) for p in _worktree_paths(repo)), "contrast: it exists mid-run"
        return parent, wt

    monkeypatch.setattr(adapter, "_add_snapshot_worktree", _spy)
    import pre_tool_checks as ptc

    def _boom(*a, **k):
        raise RuntimeError("gates exploded")

    monkeypatch.setattr(ptc, "_run_gates_concurrently", _boom)
    before = os.getcwd()
    with pytest.raises(RuntimeError, match="gates exploded"):
        adapter._run_test_gates("pre-commit")

    assert _same(os.getcwd(), before)
    assert not os.path.exists(seen["parent"]), "temp dir must be removed"
    paths = _worktree_paths(repo)
    assert not any("drishta-gate-" in p for p in paths)
    assert any(_same(other, p) for p in paths), "an unrelated worktree must be untouched"
    assert other.is_dir()


def _raw_git(repo, *args):
    """Like `_sg` but returns the CompletedProcess instead of asserting rc == 0."""
    env = {k: v for k, v in os.environ.items() if k not in set(adapter._GIT_ENV_FALLBACK)}
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )


def test_a_failed_worktree_add_cleans_up_what_it_registered(monkeypatch, tmp_path):
    """`worktree add` can register the worktree and THEN fail (or time out).
    `_add_snapshot_worktree` must remove that registration and its temp dir
    itself, because the caller never learns the paths."""
    repo = _scratch_repo(tmp_path, monkeypatch)
    tree, why = _REAL_WRITE_TREE()
    assert tree, why

    made = []
    real_mkdtemp = adapter.tempfile.mkdtemp

    def _spy_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        made.append(d)
        return d

    monkeypatch.setattr(adapter.tempfile, "mkdtemp", _spy_mkdtemp)
    registered_mid_run = []
    real_git = adapter._git

    def _git_add_then_fail(*args, **kw):
        r = real_git(*args, **kw)
        if args[:2] == ("worktree", "add"):
            assert r.returncode == 0, r.stderr
            registered_mid_run.extend(p for p in _worktree_paths(repo) if "drishta-gate-" in p)
            return subprocess.CompletedProcess(r.args, 1, "", "simulated failure after registration")
        return r

    monkeypatch.setattr(adapter, "_git", _git_add_then_fail)
    with pytest.raises(RuntimeError, match="simulated failure"):
        adapter._add_snapshot_worktree(tree)

    assert registered_mid_run, "contrast: the add really did register a worktree"
    assert not any("drishta-gate-" in p for p in _worktree_paths(repo)), "registration leaked"
    assert made and not os.path.exists(made[0]), "temp dir leaked"


def test_cleanup_never_prunes_an_unrelated_stale_worktree(monkeypatch, tmp_path):
    """Pins "no `git worktree prune`": a live unrelated worktree can't tell
    (prune ignores it), so make one STALE (directory deleted) and check that its
    registration survives our cleanup."""
    repo = _scratch_repo(tmp_path, monkeypatch)
    stale = tmp_path / "stale_wt"
    _sg(repo, "worktree", "add", "--detach", str(stale))
    shutil.rmtree(stale)
    dry = _raw_git(repo, "worktree", "prune", "--dry-run", "-v")
    assert "stale_wt" in (dry.stdout + dry.stderr), "contrast: prune WOULD remove it"

    tree, why = _REAL_WRITE_TREE()
    assert tree, why
    parent, wt = adapter._add_snapshot_worktree(tree)
    adapter._remove_snapshot(parent, wt)

    paths = _worktree_paths(repo)
    assert not any("drishta-gate-" in p for p in paths), "contrast: ours is gone"
    assert any("stale_wt" in p for p in paths), "cleanup pruned an unrelated worktree"


def test_cleanup_removes_a_locked_snapshot_worktree(monkeypatch, tmp_path):
    """A `worktree add` killed by its timeout leaves the entry LOCKED; one
    `--force` refuses that, two remove it."""
    repo = _scratch_repo(tmp_path, monkeypatch)
    tree, why = _REAL_WRITE_TREE()
    assert tree, why

    # Contrast: a single --force really is refused on a locked worktree.
    p1, w1 = adapter._add_snapshot_worktree(tree)
    _sg(repo, "worktree", "lock", w1)
    assert _raw_git(repo, "worktree", "remove", "--force", w1).returncode != 0
    _sg(repo, "worktree", "remove", "--force", "--force", w1)
    shutil.rmtree(p1, ignore_errors=True)

    parent, wt = adapter._add_snapshot_worktree(tree)
    _sg(repo, "worktree", "lock", wt)
    adapter._remove_snapshot(parent, wt)
    assert not any("drishta-gate-" in p for p in _worktree_paths(repo)), "locked entry stranded"
    assert not os.path.exists(parent)


@pytest.mark.parametrize("how", ["write_tree_fails", "worktree_add_fails"])
def test_snapshot_failure_falls_back_to_the_working_tree_loudly(monkeypatch, tmp_path, capsys, how):
    repo = _scratch_repo(tmp_path, monkeypatch)
    _use_real_snapshot(monkeypatch)
    if how == "write_tree_fails":
        monkeypatch.setattr(adapter, "_write_tree", lambda: (None, "boom"))
    else:
        def _bad(tree):
            raise RuntimeError("boom")

        monkeypatch.setattr(adapter, "_add_snapshot_worktree", _bad)
    monkeypatch.setattr(adapter, "_changed_files", lambda: ["app.py"])
    import pre_tool_checks as ptc

    cwds = []
    verdict = {"pytest": (True, ""), "antipattern": (True, "")}

    def _fake(need_pytest, need_antipattern):
        cwds.append(os.getcwd())
        return verdict

    monkeypatch.setattr(ptc, "_run_gates_concurrently", _fake)

    assert adapter._run_test_gates("pre-commit") == 0
    err = capsys.readouterr().err
    assert len(cwds) == 1 and _same(cwds[0], repo), "gates must still run, in the working tree"
    assert "WARNING" in err and "could not build a snapshot" in err and "WORKING TREE" in err
    assert "boom" in err

    # Contrast: the fallback still FAILS CLOSED on a failing suite.
    verdict["pytest"] = (False, "1 failed")
    assert adapter._run_test_gates("pre-commit") == 1
    assert len(cwds) == 2
    assert "BLOCKED" in capsys.readouterr().err


def test_success_line_says_which_tree_was_tested(monkeypatch, tmp_path, capsys):
    _scratch_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(adapter, "_changed_files", lambda: ["app.py"])
    _stub_gates(monkeypatch, {"pytest": (True, ""), "antipattern": (True, "")})

    assert adapter._run_test_gates("pre-commit") == 0  # autouse: snapshot unavailable
    err = capsys.readouterr().err
    assert "tested against the WORKING TREE -- snapshot unavailable" in err
    assert "snapshot of the commit" not in err

    _use_real_snapshot(monkeypatch)
    assert adapter._run_test_gates("pre-commit") == 0
    err = capsys.readouterr().err
    assert "pytest PASSED" in err
    assert "(scope: 1 staged file(s), tested against a snapshot of the commit)" in err
    assert "WORKING TREE" not in err


def test_pre_push_still_tests_the_working_tree_without_a_snapshot(monkeypatch, tmp_path, capsys):
    _scratch_repo(tmp_path, monkeypatch)
    _use_real_snapshot(monkeypatch)
    monkeypatch.setattr(adapter, "_push_tree_mismatch", lambda refs, ptc: [])
    _stub_stdin(monkeypatch, "refs/heads/main abc refs/heads/main def\n")
    called = []
    monkeypatch.setattr(adapter, "_add_snapshot_worktree", lambda t: called.append(t))
    _stub_gates(monkeypatch, {"pytest": (True, "")})

    assert adapter._run_test_gates("pre-push") == 0
    assert called == [], "pre-push must not build a snapshot"
    assert "scope: the working tree)" in capsys.readouterr().err


def test_main_index_and_head_are_untouched_by_a_snapshot_cycle(monkeypatch, tmp_path):
    repo = _scratch_repo(tmp_path, monkeypatch)
    (repo / "a.py").write_text("v1\n")
    (repo / "b.py").write_text("new\n")
    _sg(repo, "add", "a.py", "b.py")
    _use_real_snapshot(monkeypatch)
    monkeypatch.setattr(adapter, "_changed_files", lambda: ["app.py"])
    _stub_gates(monkeypatch, {"pytest": (True, ""), "antipattern": (True, "")})

    tree_before, head_before = _sg(repo, "write-tree"), _sg(repo, "rev-parse", "HEAD")
    ran_snapshot = []
    real_add = adapter._add_snapshot_worktree

    def _spy(t):
        ran_snapshot.append(1)
        return real_add(t)

    monkeypatch.setattr(adapter, "_add_snapshot_worktree", _spy)

    assert adapter._run_test_gates("pre-commit") == 0
    assert ran_snapshot, "contrast: a snapshot cycle really happened"
    assert _sg(repo, "write-tree") == tree_before
    assert _sg(repo, "rev-parse", "HEAD") == head_before
    assert _sg(repo, "diff", "--cached", "--name-only").split() == ["a.py", "b.py"]


# ---------------------------------------------------------------------------
# Block log (2026-10-13): every BLOCKED message is also written to
# <git dir>/last-hook-block.log, so it survives a caller that filtered stderr
# (the undiagnosable 2026-10-12 one-off block).
# ---------------------------------------------------------------------------

def test_record_block_writes_the_last_block_into_the_git_dir(monkeypatch, tmp_path):
    repo = _scratch_repo(tmp_path, monkeypatch)
    path = _REAL_RECORD_BLOCK("pre-commit", "BLOCKED (pre-commit gates): first")
    git_dir = _sg(repo, "rev-parse", "--absolute-git-dir")
    assert path is not None
    assert os.path.normcase(os.path.dirname(path)) == os.path.normcase(os.path.abspath(git_dir))
    assert os.path.basename(path) == "last-hook-block.log"
    text = open(path, encoding="utf-8").read()
    assert "[pre-commit]" in text and "first" in text

    # Holds the LAST block only.
    _REAL_RECORD_BLOCK("commit-msg", "BLOCKED (workflow gates): second")
    text = open(path, encoding="utf-8").read()
    assert "second" in text and "first" not in text and "[commit-msg]" in text
    # Inside .git/, so it can never be staged.
    assert _sg(repo, "status", "--porcelain") == ""


def test_record_block_outside_a_repo_returns_none_and_never_raises(monkeypatch, tmp_path):
    for name in adapter._GIT_ENV_FALLBACK:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    monkeypatch.chdir(not_a_repo)
    assert _REAL_RECORD_BLOCK("pre-commit", "BLOCKED x") is None


def test_record_block_swallows_a_write_failure(monkeypatch):
    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr("builtins.open", _boom)
    assert _REAL_RECORD_BLOCK("pre-commit", "BLOCKED x") is None


def test_a_code_gate_block_records_the_full_message(monkeypatch):
    monkeypatch.setattr(adapter, "_changed_files", lambda: ["stock_analyzer/scoring.py"])
    _stub_gates(monkeypatch, {"pytest": (False, "FAILED tests/test_x.py::test_y"),
                              "antipattern": (True, "")})
    assert adapter._run_test_gates("pre-commit") == 1
    assert len(_BLOCKS_RECORDED) == 1
    mode, text = _BLOCKS_RECORDED[0]
    assert mode == "pre-commit"
    assert "BLOCKED (pre-commit gates" in text and "FAILED tests/test_x.py::test_y" in text


def test_a_passing_gate_records_nothing(monkeypatch):
    monkeypatch.setattr(adapter, "_changed_files", lambda: ["stock_analyzer/scoring.py"])
    _stub_gates(monkeypatch, {"pytest": (True, ""), "antipattern": (True, "")})
    assert adapter._run_test_gates("pre-commit") == 0
    assert _BLOCKS_RECORDED == []


def test_a_text_gate_block_records_the_full_message(monkeypatch, tmp_path):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("feat(x): no trailers\n", encoding="utf-8")
    monkeypatch.setattr(adapter, "_changed_files", lambda: ["docs/x.md"])
    assert adapter._run_gates(["adapter", "commit", str(msg)]) == 1
    assert len(_BLOCKS_RECORDED) == 1
    mode, text = _BLOCKS_RECORDED[0]
    assert mode == "commit-msg" and "Hard Rule #5" in text


def _commit_msg_run(monkeypatch, tmp_path, text):
    """Drive the commit-msg path the way the shim does: argv[1] == "commit"."""
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(text, encoding="utf-8")
    monkeypatch.setattr(adapter, "_changed_files", lambda: ["docs/x.md"])
    return adapter._run_gates(["adapter", "commit", str(msg)])


@pytest.mark.parametrize("subject", ["fix(x): y", "refactor(x): y", "perf!: y", "fix (s): y"])
def test_build_attested_commit_without_build_trailer_is_blocked(
        monkeypatch, tmp_path, capsys, subject):
    assert _commit_msg_run(monkeypatch, tmp_path, subject + "\n") == 1
    err = capsys.readouterr().err
    assert "fix(/refactor(/perf( commit missing Build trailer" in err
    assert "Design" not in err.split("Hard Rule #5")[1].split("Build  =")[0]
    assert len(_BLOCKS_RECORDED) == 1 and "missing Build trailer" in _BLOCKS_RECORDED[0][1]


@pytest.mark.parametrize("trailer", [
    "Build = lead -- one-line typo", "Build  = implementer (Sonnet 5.5)"])
def test_fix_with_build_trailer_and_no_design_passes(monkeypatch, tmp_path, capsys, trailer):
    assert _commit_msg_run(monkeypatch, tmp_path, f"fix(x): y\n\n{trailer}\n") == 0
    err = capsys.readouterr().err
    assert "trailers: checked (Build)" in err
    assert _BLOCKS_RECORDED == []


def test_feat_still_requires_design_and_build_and_reports_it(monkeypatch, tmp_path, capsys):
    assert _commit_msg_run(monkeypatch, tmp_path, "feat(x): y\n\nBuild = lead -- z\n") == 1
    assert "feature commit missing Design trailer(s)" in capsys.readouterr().err
    assert _commit_msg_run(
        monkeypatch, tmp_path, "feat(x): y\n\nDesign = lead -- z\nBuild = lead -- z\n") == 0
    assert "trailers: checked (Design + Build)" in capsys.readouterr().err


@pytest.mark.parametrize("subject", ["docs(x): y", "chore: y", "test(x): y", "fixup! fix(x): y"])
def test_other_commit_types_need_no_trailer(monkeypatch, tmp_path, capsys, subject):
    assert _commit_msg_run(monkeypatch, tmp_path, subject + "\n") == 0
    assert "trailers: N/A (not a feat(/fix(/refactor(/perf( commit)" in capsys.readouterr().err


def test_an_adapter_crash_records_the_block(monkeypatch):
    def _raise(mode):
        raise RuntimeError("boom")
    monkeypatch.setattr(adapter, "_run_test_gates", _raise)
    monkeypatch.setattr(sys, "argv", ["adapter", "pre-commit"])
    with pytest.raises(SystemExit) as exc:
        adapter.main()
    assert exc.value.code == 1
    assert len(_BLOCKS_RECORDED) == 1 and "NOT a pass" in _BLOCKS_RECORDED[0][1]


def test_an_unreadable_message_file_records_the_block(tmp_path):
    assert adapter._run_gates(["adapter", "commit", str(tmp_path / "missing")]) == 1
    assert len(_BLOCKS_RECORDED) == 1
    assert _BLOCKS_RECORDED[0][0] == "commit-msg" and "not readable" in _BLOCKS_RECORDED[0][1]


def test_an_unknown_invocation_records_the_block(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["adapter", "bogus-mode"])
    with pytest.raises(SystemExit) as exc:
        adapter.main()
    assert exc.value.code == 1
    assert len(_BLOCKS_RECORDED) == 1 and "unknown adapter invocation" in _BLOCKS_RECORDED[0][1]


def test_a_commit_msg_crash_is_logged_under_the_hooks_real_name(monkeypatch):
    def _raise(argv):
        raise RuntimeError("boom")
    monkeypatch.setattr(adapter, "_run_gates", _raise)
    monkeypatch.setattr(sys, "argv", ["adapter", "commit", "msgfile"])
    with pytest.raises(SystemExit):
        adapter.main()
    assert _BLOCKS_RECORDED[0][0] == "commit-msg"
