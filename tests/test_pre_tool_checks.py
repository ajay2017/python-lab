"""Tests for `.claude/hooks/pre_tool_checks.py` -- the commit/push safety-net
hook every other gate in this project depends on (pytest full-suite gate,
antipattern gate, Hard Rule #4 review-citation gate, Hard Rule #5 provenance
trailers). Added 2026-10-02 per docs/reviews/2026-10-02-review.md Medium #4
("the hook has zero test coverage"), bundled with the Critical #2 fix (the
verified-tree marker could be written without either gate having actually
run) and the Medium #5 bypass fixes (an unstaged pathspec argument on the
commit command, and a `-C`/`-c` global option between `git` and the
subcommand).

FIX-FIRST follow-up (2026-10-02, same review pass, 4 blocking findings on
the above): `_has_git_subcommand` had regressed past the old literal regex
it replaced (Blocking 1); the verified-tree marker's write condition had two
more gaps, at commit time and at push time (Blocking 2); a `-C`/`--git-dir`/
`--work-tree` redirect was detected but every OTHER helper still silently
evaluated the hook's own cwd instead (Blocking 3); and the pathspec-bypass
fix only caught a literal file path, not a directory/`.`/`./`-prefixed path/
`--pathspec-from-file` (Blocking 4). See `.claude/hooks/pre_tool_checks.py`'s
own module docstring and each touched function's docstring for the full
reasoning; new test classes below are grouped by which blocking finding they
cover.

The hook lives outside the `stock_analyzer` package (it's invoked as a
PreToolUse subprocess reading JSON off stdin, per .claude/settings.json), so
it's loaded by path -- same pattern tests/test_check_antipatterns.py and
tests/test_repo_hygiene.py already use for scripts/ files. Its logic is
cleanly unit-testable by importing the module's own functions directly. Most
tests here call extracted pure functions directly, never `main()` (which
reads real stdin, spawns real pytest/antipattern subprocesses, and calls
`sys.exit`) -- the one exception is `TestMainMarkerWiring`, which exercises
`main()` itself with `_run_gates_concurrently` stubbed out, specifically to
cover the wiring the pure-function tests cannot (see that class's own
docstring).
"""
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast

_HOOK_PATH = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "pre_tool_checks.py"
_spec = importlib.util.spec_from_file_location("pre_tool_checks", _HOOK_PATH)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


# ---------------------------------------------------------------------------
# Critical #2: the verified-tree marker's write condition
# ---------------------------------------------------------------------------

class TestBothGatesPassed:
    """`_both_gates_passed` is the sole gate on whether the verified-tree
    marker may be written -- confirms the marker is NOT written when no gate
    ran, IS written (True) when both gates ran and succeeded, and is NOT
    written when a gate ran but failed/couldn't be invoked."""

    def test_false_when_neither_gate_was_needed(self):
        # The exact Critical #2 scenario: a docs-only commit, no gate runs.
        assert hook._both_gates_passed(False, False, {}) is False

    def test_false_when_only_pytest_was_needed_and_passed(self):
        # A tests/-only commit: need_antipattern is False, so even a clean
        # pytest pass must not be treated as "both gates verified this tree".
        results = {"pytest": (True, "")}
        assert hook._both_gates_passed(True, False, results) is False

    def test_false_when_only_antipattern_was_needed_and_passed(self):
        results = {"antipattern": (True, "")}
        assert hook._both_gates_passed(False, True, results) is False

    def test_true_when_both_gates_ran_and_passed(self):
        results = {"pytest": (True, ""), "antipattern": (True, "")}
        assert hook._both_gates_passed(True, True, results) is True

    def test_false_when_pytest_failed(self):
        results = {"pytest": (False, "boom"), "antipattern": (True, "")}
        assert hook._both_gates_passed(True, True, results) is False

    def test_false_when_antipattern_failed(self):
        results = {"pytest": (True, ""), "antipattern": (False, "new instance")}
        assert hook._both_gates_passed(True, True, results) is False

    def test_false_when_pytest_could_not_be_invoked(self):
        # ok is None -- a fail-CLOSED case for pytest that still never
        # reaches this point in practice (main() exits(2) first), but the
        # pure function must be correct regardless of caller behaviour.
        results = {"pytest": (None, "missing venv"), "antipattern": (True, "")}
        assert hook._both_gates_passed(True, True, results) is False

    def test_false_when_antipattern_could_not_be_invoked(self):
        # ok is None -- antipattern's non-blocking WARN case. This is the
        # literal Critical #2 "didn't block but didn't verify either" gap.
        results = {"pytest": (True, ""), "antipattern": (None, "script not found")}
        assert hook._both_gates_passed(True, True, results) is False

    def test_false_when_results_missing_a_key_entirely(self):
        results = {"pytest": (True, "")}
        assert hook._both_gates_passed(True, True, results) is False


# ---------------------------------------------------------------------------
# Blocking 2 (2026-10-02 FIX-FIRST pass): the marker-write condition had two
# MORE gaps past `_both_gates_passed` -- a dirty unstaged tested path at
# commit time (2a), and an unconditional push-time rewrite after a full run
# regardless of whether it actually passed (2b).
# ---------------------------------------------------------------------------

class TestShouldWriteCommitTimeMarker:
    """`_should_write_commit_time_marker` -- the sole gate on whether the
    commit-time `write-tree` result may be recorded as verified. Uses the
    real `tmp_git_repo` fixture (defined further down this file) since the
    dirty-check it wraps shells out to real git."""

    def test_false_when_both_gates_passed_is_false(self, tmp_git_repo):
        tokens = hook._tokens('git commit -m "x"')
        assert hook._should_write_commit_time_marker(False, tokens) is False

    def test_false_when_commit_is_not_plain(self, tmp_git_repo):
        tokens = hook._tokens('git commit -am "x"')
        assert hook._should_write_commit_time_marker(True, tokens) is False

    def test_true_on_a_clean_tree_with_only_staged_changes(self, tmp_git_repo):
        # The exact "about to be committed" case: a staged-but-uncommitted
        # change under a tested path is the EXPECTED difference from the
        # pre-commit index, not extra dirt -- must not block the write.
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "stock_analyzer/x.py"], cwd=tmp_git_repo, check=True)
        tokens = hook._tokens('git commit -m "x"')
        assert hook._should_write_commit_time_marker(True, tokens) is True

    def test_false_when_an_unstaged_tested_path_change_exists(self, tmp_git_repo):
        # The literal Blocking 2a scenario: write-tree (the INDEX) would be
        # clean, but pytest ran against a WORKING tree that also has this
        # unstaged edit -- the commit-time run cannot vouch for it.
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "stock_analyzer/x.py"], cwd=tmp_git_repo, check=True)
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 3\n", encoding="utf-8")
        tokens = hook._tokens('git commit -m "x"')
        assert hook._should_write_commit_time_marker(True, tokens) is False

    def test_false_when_an_untracked_tested_path_file_exists(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "new_module.py").write_text("y = 1\n", encoding="utf-8")
        tokens = hook._tokens('git commit -m "x"')
        assert hook._should_write_commit_time_marker(True, tokens) is False


class TestShouldWritePushTimeMarker:
    """`_should_write_push_time_marker` -- the sole gate on whether a
    push-time full-run may rewrite the marker. Pure (no git I/O), unlike its
    commit-time sibling above."""

    def test_true_when_both_gates_ran_and_passed(self):
        results = {"pytest": (True, ""), "antipattern": (True, "")}
        assert hook._should_write_push_time_marker(results) is True

    def test_false_when_antipattern_could_not_be_invoked(self):
        # The literal Blocking 2b scenario: a WARN-only antipattern outcome
        # (ok is None) does not block the push, but it also verified nothing
        # -- the pre-fix code rewrote the marker here anyway.
        results = {"pytest": (True, ""), "antipattern": (None, "script not found")}
        assert hook._should_write_push_time_marker(results) is False

    def test_false_when_pytest_could_not_be_invoked(self):
        results = {"pytest": (None, "missing venv"), "antipattern": (True, "")}
        assert hook._should_write_push_time_marker(results) is False


class TestVerifiedTreeMarkerRoundtrip:
    """`_write_verified_tree`/`_read_verified_tree` -- isolated from the real
    machine-local marker file via monkeypatching `_VERIFIED_TREE_MARKER`, so
    these tests never touch the actual .claude/hooks/.last_verified_tree.json
    this repo's own hook uses."""

    def test_read_returns_none_when_file_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hook, "_VERIFIED_TREE_MARKER", str(tmp_path / "marker.json"))
        assert hook._read_verified_tree() is None

    def test_write_then_read_roundtrips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hook, "_VERIFIED_TREE_MARKER", str(tmp_path / "marker.json"))
        hook._write_verified_tree("deadbeefcafe")
        assert hook._read_verified_tree() == "deadbeefcafe"

    def test_read_returns_none_on_malformed_json(self, tmp_path, monkeypatch):
        marker = tmp_path / "marker.json"
        marker.write_text("not valid json", encoding="utf-8")
        monkeypatch.setattr(hook, "_VERIFIED_TREE_MARKER", str(marker))
        assert hook._read_verified_tree() is None

    def test_read_returns_none_when_tree_field_missing(self, tmp_path, monkeypatch):
        marker = tmp_path / "marker.json"
        marker.write_text('{"verified_at": "2026-10-02T00:00:00"}', encoding="utf-8")
        monkeypatch.setattr(hook, "_VERIFIED_TREE_MARKER", str(marker))
        assert hook._read_verified_tree() is None


# ---------------------------------------------------------------------------
# Medium #5, bypass 2: `-C`/`-c` between `git` and the subcommand
# ---------------------------------------------------------------------------

class TestHasGitSubcommand:
    def test_plain_commit_detected(self):
        assert hook._has_git_subcommand('git commit -m "x"', "commit") is True

    def test_plain_push_detected(self):
        assert hook._has_git_subcommand("git push origin main", "push") is True

    def test_unrelated_command_not_detected(self):
        assert hook._has_git_subcommand("git status", "commit") is False

    def test_dash_capital_C_global_option_still_detected(self):
        # The exact bypass 2 scenario: `git -C <dir> commit` previously
        # matched neither the old literal-adjacency regex nor the tokenizer.
        assert hook._has_git_subcommand('git -C /some/dir commit -m "x"', "commit") is True

    def test_dash_lowercase_c_global_option_still_detected(self):
        assert hook._has_git_subcommand(
            'git -c user.name=test commit -m "x"', "commit"
        ) is True

    def test_dash_capital_C_before_push_still_detected(self):
        assert hook._has_git_subcommand("git -C /some/dir push origin main", "push") is True

    def test_multiple_global_options_before_commit_still_detected(self):
        assert hook._has_git_subcommand(
            'git -c user.name=test -C /some/dir commit -m "x"', "commit"
        ) is True

    def test_boolean_global_flag_before_commit_still_detected(self):
        assert hook._has_git_subcommand('git --no-pager commit -m "x"', "commit") is True

    def test_chained_shell_command_still_detected(self):
        assert hook._has_git_subcommand('echo hi && git commit -m "x"', "commit") is True

    # -- Blocking 1 (2026-10-02 FIX-FIRST pass): the tokenized-only version of
    # this function was a real regression past the OLD literal regex it
    # replaced. Each test below reproduces a scenario that returned False on
    # that pre-fix code (confirmed manually before writing this fix) and must
    # now return True.

    def test_heredoc_body_apostrophe_causes_shlex_failure_but_still_detected(self):
        # shlex.split raises ValueError on this (unterminated quote from the
        # apostrophe in "it's") -- the tokenized-only leg alone returns False.
        command = "git commit -F - <<'EOF'\nthis fix handles it's edge case\nEOF"
        assert hook._has_git_subcommand(command, "commit") is True

    def test_powershell_here_string_apostrophe_still_detected(self):
        # Same underlying shlex-failure mechanism as the heredoc case, via
        # PowerShell's `@'...'@` here-string syntax instead of bash's `<<'EOF'`.
        command = "git commit -F - @'\nsome notes, it's fine\n'@"
        assert hook._has_git_subcommand(command, "commit") is True

    def test_path_qualified_git_still_detected(self):
        # shlex tokenizes "/usr/bin/git" as ONE token, distinct from the bare
        # "git" the tokenized scan requires -- the old literal regex matched
        # this (a `\bgit\b` word-boundary inside the path), the tokenizer
        # alone does not.
        assert hook._has_git_subcommand('/usr/bin/git commit -m "x"', "commit") is True

    def test_bash_dash_c_wrapped_commit_still_detected(self):
        # shlex tokenizes the whole `"git commit -m x"` string as a SINGLE
        # quoted token (the argument to `bash -c`), so the tokenizer never
        # sees a bare "git" token at all.
        assert hook._has_git_subcommand('bash -c "git commit -m x"', "commit") is True


class TestTokensTolerantOfGlobalOptions:
    def test_tokens_scopes_to_git_commit_segment(self):
        tokens = hook._tokens('echo hi && git commit -m "x"')
        assert tokens[0] == "git"
        assert "commit" in tokens

    def test_tokens_includes_global_option_prefix(self):
        tokens = hook._tokens('git -C /some/dir commit -m "x"')
        assert tokens[:4] == ["git", "-C", "/some/dir", "commit"]


# ---------------------------------------------------------------------------
# Blocking 3 (2026-10-02 FIX-FIRST pass): detecting `-C <dir>` only fixed
# WHETHER a redirected commit/push is recognised -- every other helper in
# the hook still reads its OWN cwd, so a real redirect would silently gate
# the WRONG repo. `_cwd_redirect_mismatch` must BLOCK on a mismatch rather
# than attempt to gate the other directory.
# ---------------------------------------------------------------------------

class TestCwdRedirectMismatch:
    def test_no_global_opts_is_never_a_mismatch(self):
        tokens = hook._tokens('git commit -m "x"')
        assert hook._cwd_redirect_mismatch(tokens) is False

    def test_empty_tokens_is_never_a_mismatch(self):
        # Nothing to check -- a `-C` can't even be detected without real
        # tokens, so this is the same "can't tell" gap `_has_git_subcommand`
        # already discloses (module docstring); it is NOT this function's
        # job to also fail closed on a totally unparseable command.
        assert hook._cwd_redirect_mismatch([]) is False

    def test_dash_capital_C_to_a_different_dir_is_a_mismatch(self, monkeypatch):
        # The real scenario this fix closes: `git -C <dir> commit` where
        # <dir> is NOT the hook's own cwd -- must BLOCK, not silently gate
        # the wrong repo. `_toplevel` is stubbed rather than shelling out to
        # real git for a second repo.
        tokens = hook._tokens('git -C /some/other/dir commit -m "x"')

        def fake_toplevel(extra_opts):
            return "/some/other/dir" if extra_opts else "/here/checkout"

        monkeypatch.setattr(hook, "_toplevel", fake_toplevel)
        assert hook._cwd_redirect_mismatch(tokens) is True

    def test_dash_capital_C_to_the_same_dir_is_not_a_mismatch(self, monkeypatch):
        tokens = hook._tokens('git -C /here/checkout commit -m "x"')
        monkeypatch.setattr(hook, "_toplevel", lambda extra_opts: "/here/checkout")
        assert hook._cwd_redirect_mismatch(tokens) is False

    def test_dash_capital_C_mismatch_also_detected_for_push(self, monkeypatch):
        tokens = hook._tokens('git -C /some/other/dir push origin main', subcommand="push")

        def fake_toplevel(extra_opts):
            return "/some/other/dir" if extra_opts else "/here/checkout"

        monkeypatch.setattr(hook, "_toplevel", fake_toplevel)
        assert hook._cwd_redirect_mismatch(tokens) is True

    def test_unresolvable_toplevel_fails_closed_as_a_mismatch(self, monkeypatch):
        tokens = hook._tokens('git -C /some/dir commit -m "x"')
        monkeypatch.setattr(hook, "_toplevel", lambda extra_opts: None)
        assert hook._cwd_redirect_mismatch(tokens) is True

    def test_global_opts_extracted_correctly(self):
        tokens = hook._tokens('git -C /some/dir -c user.name=x commit -m "x"')
        assert hook._git_global_opts(tokens) == ["-C", "/some/dir", "-c", "user.name=x"]

    def test_global_opts_empty_for_bare_commit(self):
        tokens = hook._tokens('git commit -m "x"')
        assert hook._git_global_opts(tokens) == []


# ---------------------------------------------------------------------------
# Medium #5, bypass 1: an unstaged pathspec argument on the commit itself
# ---------------------------------------------------------------------------

class TestPathspecArgs:
    def test_no_positional_args_returns_empty(self):
        tokens = hook._tokens('git commit -m "msg"')
        assert hook._pathspec_args(tokens) == []

    def test_single_trailing_pathspec_captured(self):
        tokens = hook._tokens('git commit -m "msg" stock_analyzer/foo.py')
        assert hook._pathspec_args(tokens) == ["stock_analyzer/foo.py"]

    def test_message_value_not_mistaken_for_a_pathspec(self):
        # "msg" must be consumed as the message flag's VALUE, not treated as
        # a pathspec -- across the short form, the long form, AND a
        # clustered short flag (non-blocking #3, 2026-10-02 FIX-FIRST pass).
        for command in (
            'git commit -m "msg" stock_analyzer/foo.py',
            'git commit --message msg stock_analyzer/foo.py',
            'git commit -qm msg stock_analyzer/foo.py',
        ):
            tokens = hook._tokens(command)
            pathspecs = hook._pathspec_args(tokens)
            assert "msg" not in pathspecs, command
            assert pathspecs == ["stock_analyzer/foo.py"], command

    def test_pathspec_from_file_long_form_not_mistaken_for_a_pathspec(self):
        tokens = hook._tokens("git commit -m msg --pathspec-from-file=list.txt")
        assert hook._pathspec_args(tokens) == []

    def test_pathspec_from_file_space_form_not_mistaken_for_a_pathspec(self):
        tokens = hook._tokens("git commit -m msg --pathspec-from-file list.txt")
        assert hook._pathspec_args(tokens) == []

    def test_multiple_pathspecs_captured(self):
        tokens = hook._tokens("git commit -m msg stock_analyzer/a.py stock_analyzer/b.py")
        assert hook._pathspec_args(tokens) == ["stock_analyzer/a.py", "stock_analyzer/b.py"]

    def test_dashdash_separator_pathspecs_captured(self):
        tokens = hook._tokens("git commit -m msg -- stock_analyzer/a.py stock_analyzer/b.py")
        assert hook._pathspec_args(tokens) == ["stock_analyzer/a.py", "stock_analyzer/b.py"]

    def test_clustered_am_flag_still_consumes_message_value(self):
        tokens = hook._tokens("git commit -am msg stock_analyzer/a.py")
        assert hook._pathspec_args(tokens) == ["stock_analyzer/a.py"]

    def test_file_flag_value_not_mistaken_for_a_pathspec(self):
        tokens = hook._tokens("git commit -F .git/COMMIT_MSG.txt stock_analyzer/a.py")
        assert hook._pathspec_args(tokens) == ["stock_analyzer/a.py"]

    def test_non_commit_command_yields_empty(self):
        tokens = hook._tokens("git status")
        assert hook._pathspec_args(tokens) == []


class TestPathspecFromFileArg:
    def test_long_form_with_equals(self):
        tokens = hook._tokens("git commit -m msg --pathspec-from-file=list.txt")
        assert hook._pathspec_from_file_arg(tokens) == "list.txt"

    def test_space_separated_form(self):
        tokens = hook._tokens("git commit -m msg --pathspec-from-file list.txt")
        assert hook._pathspec_from_file_arg(tokens) == "list.txt"

    def test_absent_returns_none(self):
        tokens = hook._tokens('git commit -m "msg" stock_analyzer/foo.py')
        assert hook._pathspec_from_file_arg(tokens) is None


@pytest.fixture
def tmp_git_repo(tmp_path, monkeypatch):
    """A minimal real git repo (not a mock) so `_get_staged_files` and
    `_tested_paths_dirty`'s real `git diff`/`git ls-files` subprocess calls
    have somewhere safe to run -- never the real project checkout."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *args: subprocess.run(args, cwd=repo, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "test@example.com")
    run("git", "config", "user.name", "Test")
    (repo / "stock_analyzer").mkdir()
    (repo / "stock_analyzer" / "x.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "README.md").write_text("notes\n", encoding="utf-8")
    run("git", "add", ".")
    run("git", "commit", "-q", "-m", "init")
    monkeypatch.chdir(repo)
    return repo


class TestGetStagedFilesPathspecBypass:
    """Integration-level regression test for bypass 1: a real unstaged edit,
    committed via a positional pathspec argument, must be visible to
    `_get_staged_files` (and therefore to every gate scoped off `staged`)
    even though `git diff --cached` alone would miss it."""

    def test_unstaged_file_named_as_a_commit_pathspec_is_seen_as_staged(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 2\n", encoding="utf-8")
        command = 'git commit -m "fix" stock_analyzer/x.py'
        staged = hook._get_staged_files(command)
        assert "stock_analyzer/x.py" in staged

    def test_truly_unstaged_file_not_named_on_the_command_is_not_seen(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 2\n", encoding="utf-8")
        command = 'git commit -m "fix"'
        staged = hook._get_staged_files(command)
        assert "stock_analyzer/x.py" not in staged

    # -- Blocking 4 (2026-10-02 FIX-FIRST pass): the bypass-1 fix above only
    # ever matched a pathspec that WAS ITSELF an exact relative file path --
    # a directory, `.`, or a `./`-prefixed path all still bypassed the gate.
    # Each test reproduces a scenario confirmed to miss the real changed file
    # on the pre-this-fix code and must now see it.

    def test_directory_pathspec_resolves_to_the_real_changed_file(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 2\n", encoding="utf-8")
        command = 'git commit -m "fix" stock_analyzer'
        staged = hook._get_staged_files(command)
        assert "stock_analyzer/x.py" in staged

    def test_dot_pathspec_resolves_to_the_real_changed_file(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 2\n", encoding="utf-8")
        command = 'git commit -m "fix" .'
        staged = hook._get_staged_files(command)
        assert "stock_analyzer/x.py" in staged

    def test_dot_slash_prefixed_pathspec_resolves_to_the_real_changed_file(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 2\n", encoding="utf-8")
        command = 'git commit -m "fix" ./stock_analyzer/x.py'
        staged = hook._get_staged_files(command)
        assert "stock_analyzer/x.py" in staged

    def test_pathspec_from_file_resolves_to_the_real_changed_file(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 2\n", encoding="utf-8")
        (tmp_git_repo / "list.txt").write_text("stock_analyzer/x.py\n", encoding="utf-8")
        command = "git commit -m fix --pathspec-from-file=list.txt"
        staged = hook._get_staged_files(command)
        assert "stock_analyzer/x.py" in staged


class TestTestedPathsDirty:
    """`_tested_paths_dirty` backs the push-time "is the marker still
    trustworthy" check -- confirms it's False on a genuinely clean tree and
    True the moment a tested path has an uncommitted/untracked change."""

    def test_clean_repo_is_not_dirty(self, tmp_git_repo):
        assert hook._tested_paths_dirty() is False

    def test_unstaged_edit_under_a_tested_path_is_dirty(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 999\n", encoding="utf-8")
        assert hook._tested_paths_dirty() is True

    def test_untracked_file_under_a_tested_path_is_dirty(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "new_module.py").write_text("y = 1\n", encoding="utf-8")
        assert hook._tested_paths_dirty() is True

    def test_untracked_file_outside_tested_paths_is_not_dirty(self, tmp_git_repo):
        (tmp_git_repo / "CHANGELOG.md").write_text("notes\n", encoding="utf-8")
        assert hook._tested_paths_dirty() is False

    def test_staged_but_uncommitted_edit_under_a_tested_path_is_dirty(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 3\n", encoding="utf-8")
        subprocess.run(["git", "add", "stock_analyzer/x.py"], cwd=tmp_git_repo, check=True)
        assert hook._tested_paths_dirty() is True

    # -- Blocking 2a's building block: `include_staged=False` is what the
    # commit-time marker-write check actually uses -- a staged change is the
    # EXPECTED difference from the pre-commit index, not extra dirt.

    def test_staged_only_change_is_not_dirty_when_staged_excluded(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 3\n", encoding="utf-8")
        subprocess.run(["git", "add", "stock_analyzer/x.py"], cwd=tmp_git_repo, check=True)
        assert hook._tested_paths_dirty(include_staged=False) is False

    def test_unstaged_change_is_still_dirty_when_staged_excluded(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 3\n", encoding="utf-8")
        assert hook._tested_paths_dirty(include_staged=False) is True

    def test_untracked_file_is_still_dirty_when_staged_excluded(self, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "new_module.py").write_text("y = 1\n", encoding="utf-8")
        assert hook._tested_paths_dirty(include_staged=False) is True


class TestIsTestedPath:
    def test_matches_named_entrypoint_files(self):
        assert hook._is_tested_path("app.py")
        assert hook._is_tested_path("cron_runner.py")

    def test_matches_scanned_prefixes(self):
        assert hook._is_tested_path("stock_analyzer/foo.py")
        assert hook._is_tested_path("tests/test_foo.py")

    def test_does_not_match_docs_or_scripts(self):
        assert not hook._is_tested_path("docs/requirements.md")
        assert not hook._is_tested_path("scripts/check_antipatterns.py")

    def test_matches_the_hook_directory_itself(self):
        """2026-10-02 review M4: the hook was just added to _GATE_FILES, so
        a hook-only commit must be treated as a tested path too -- a
        citation requirement alone isn't the same guarantee as "pytest
        actually ran" (see _touches_tested_code's own docstring)."""
        assert hook._is_tested_path(".claude/hooks/pre_tool_checks.py")
        assert hook._is_tested_path(".claude/hooks/hook_liveness_check.py")


class TestTouchesTestedCode:
    """_touches_tested_code is what actually decides whether pytest runs for
    a given commit -- a SEPARATE inline check from _is_tested_path above
    (feeds the push-time dirty-check instead), so each needs its own
    coverage; neither implies the other stays in sync."""

    def test_hook_only_commit_touches_tested_code(self):
        assert hook._touches_tested_code([".claude/hooks/pre_tool_checks.py"])

    def test_hook_only_commit_does_not_touch_scanned_code(self):
        """The hook isn't an antipattern-gate TARGET (it's not app.py/
        cron_runner.py/stock_analyzer/) -- a hook-only commit should run
        pytest but not the antipattern scan."""
        assert not hook._touches_scanned_code([".claude/hooks/pre_tool_checks.py"])


# ---------------------------------------------------------------------------
# Medium #4: the citation-trailer regex and commit-type detection
# ---------------------------------------------------------------------------

class TestReviewCitationRegex:
    def test_accepts_well_formed_citation(self):
        msg = "feat(x): y\n\nReview = Opus reviewer (Opus 5): SHIP, 0 blocking; looks good"
        assert hook._has_review_citation(msg) is True

    def test_rejects_commit_with_no_citation_at_all(self):
        assert hook._has_review_citation("feat(x): y\n\nno citation here") is False

    def test_rejects_bare_habit_phrase_with_no_verdict_or_count(self):
        # The pre-2026-08-15 bug this regex was hardened to close.
        msg = "Review = Opus reviewer, looks fine"
        assert hook._has_review_citation(msg) is False

    def test_rejects_missing_blocking_count(self):
        msg = "Review = Opus reviewer (Opus 5): SHIP"
        assert hook._has_review_citation(msg) is False

    def test_accepts_verdict_and_count_wrapped_across_a_line(self):
        msg = "Review = Opus reviewer (Opus 5): SHIP,\n0 blocking; notes"
        assert hook._has_review_citation(msg) is True

    def test_accepts_model_name_containing_nested_parens(self):
        msg = "Review = Opus reviewer (Opus 4.8 (1M context)): SHIP, 0 blocking"
        assert hook._has_review_citation(msg) is True

    def test_accepts_fix_first_verdict(self):
        msg = "Review = Opus reviewer (Opus 5): FIX-FIRST, 1 blocking; fixed same commit"
        assert hook._has_review_citation(msg) is True


class TestProvenanceTrailers:
    def test_feature_commit_detected(self):
        assert hook._is_feature_commit("feat(x): add a thing") is True

    def test_non_feature_commit_not_detected(self):
        assert hook._is_feature_commit("fix(x): correct a thing") is False

    def test_both_trailers_missing(self):
        assert set(hook._missing_provenance_trailers("feat(x): y")) == {"Design", "Build"}

    def test_no_trailers_missing_when_both_present(self):
        msg = "feat(x): y\n\nDesign = lead -- small change\nBuild = lead -- small change"
        assert hook._missing_provenance_trailers(msg) == []

    def test_only_design_missing(self):
        msg = "feat(x): y\n\nBuild = implementer (Sonnet 5)"
        assert hook._missing_provenance_trailers(msg) == ["Design"]


class TestCommitTypeDetection:
    def test_plain_commit_is_plain(self):
        tokens = hook._tokens('git commit -m "x"')
        assert hook._is_plain_commit(tokens) is True

    def test_dash_a_commit_is_not_plain(self):
        tokens = hook._tokens('git commit -a -m "x"')
        assert hook._is_plain_commit(tokens) is False

    def test_clustered_am_commit_is_not_plain(self):
        tokens = hook._tokens('git commit -am "x"')
        assert hook._is_plain_commit(tokens) is False

    def test_amend_commit_is_not_plain(self):
        tokens = hook._tokens('git commit --amend -m "x"')
        assert hook._is_plain_commit(tokens) is False

    def test_dash_capital_C_commit_is_still_plain_when_no_a_or_amend(self):
        # The global `-C <dir>` option is unrelated to `-a`/`--amend` --
        # confirms the new global-option tolerance didn't accidentally make
        # every such commit read as non-plain.
        tokens = hook._tokens('git -C /some/dir commit -m "x"')
        assert hook._is_plain_commit(tokens) is True

    def test_empty_tokens_is_not_plain(self):
        # Blocking 1 (2026-10-02 FIX-FIRST pass): empty/unparseable tokens
        # (e.g. shlex.split raised ValueError) is UNKNOWN, not "no flags
        # present" -- the pre-fix code returned True here, which could let an
        # unparseable commit still write the verified-tree marker.
        assert hook._is_plain_commit([]) is False


# ---------------------------------------------------------------------------
# Non-blocking #2 (2026-10-02 FIX-FIRST pass): an end-to-end test through
# `main()` itself, not just the extracted pure functions -- confirms the
# ACTUAL WIRING (main() calling `_should_write_commit_time_marker` with the
# real args at the real call site) behaves correctly, which no test above
# covers since all of them call the extracted function directly.
# ---------------------------------------------------------------------------

class TestMainMarkerWiring:
    """`_run_gates_concurrently` is stubbed in every test here so this never
    spawns a real pytest/antipattern subprocess -- these tests are about the
    WIRING around the gate results, not the gates themselves."""

    def _invoke_main(self, monkeypatch, command: str) -> int:
        payload = json.dumps({"tool_input": {"command": command}})
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        with pytest.raises(SystemExit) as exc_info:
            hook.main()
        return exc_info.value.code

    def test_docs_only_commit_does_not_write_marker(self, monkeypatch, tmp_git_repo):
        # Nothing staged/changed in tmp_git_repo -- need_pytest/need_antipattern
        # both resolve False, so the gates must never even be invoked.
        written = {}
        monkeypatch.setattr(
            hook, "_write_verified_tree", lambda tree: written.setdefault("tree", tree)
        )
        monkeypatch.setattr(
            hook,
            "_run_gates_concurrently",
            lambda need_pytest, need_antipattern: pytest.fail(
                "gates must not run for a commit that needs neither"
            ),
        )
        code = self._invoke_main(monkeypatch, 'git commit -m "docs: update readme"')
        assert code == 0
        assert "tree" not in written

    def test_both_gates_pass_on_a_clean_tree_writes_marker(self, monkeypatch, tmp_git_repo):
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "stock_analyzer/x.py"], cwd=tmp_git_repo, check=True)

        monkeypatch.setattr(
            hook,
            "_run_gates_concurrently",
            lambda need_pytest, need_antipattern: {
                "pytest": (True, ""),
                "antipattern": (True, ""),
            },
        )
        written = {}
        monkeypatch.setattr(
            hook, "_write_verified_tree", lambda tree: written.setdefault("tree", tree)
        )

        code = self._invoke_main(monkeypatch, 'git commit -m "fix(x): y"')
        assert code == 0
        assert "tree" in written

    def test_push_full_rerun_on_dirty_tree_does_not_write_marker(self, monkeypatch, tmp_git_repo):
        """Confirmation-pass non-blocking #1: the push-time full-run marker
        REWRITE must also respect `dirty` -- pytest ran against the WORKING
        tree (with an unstaged tested-path edit present), not HEAD in
        isolation, so even a clean pass here must not vouch for HEAD's tree.
        Isolated from any real marker state (this session's own earlier
        real commits) via explicit monkeypatches, not just the fixture's
        tmp repo -- `_read_verified_tree` is forced to None so the test
        depends only on `dirty`, never on accumulated real state."""
        (tmp_git_repo / "stock_analyzer" / "x.py").write_text("x = 2\n", encoding="utf-8")
        # Deliberately left UNSTAGED -- this is what makes `_tested_paths_dirty()`
        # (include_staged defaults True at push time) report dirty.

        monkeypatch.setattr(hook, "_read_verified_tree", lambda: None)
        monkeypatch.setattr(
            hook,
            "_run_gates_concurrently",
            lambda need_pytest, need_antipattern: {
                "pytest": (True, ""),
                "antipattern": (True, ""),
            },
        )
        written = {}
        monkeypatch.setattr(
            hook, "_write_verified_tree", lambda tree: written.setdefault("tree", tree)
        )

        code = self._invoke_main(monkeypatch, "git push origin main")
        assert code == 0
        assert "tree" not in written


# ── _GATE_FILES integrity (2026-10-02 review, non-blocking finding on the
# hook-self-gating commit) ───────────────────────────────────────────────────
#
# No test previously asserted anything about _GATE_FILES's own CONTENTS --
# every other test exercises the generic membership-check logic against a
# fabricated path. A typo'd or stale-renamed entry (e.g. after a module is
# renamed/moved) would silently stop gating that file forever, with nothing
# here to catch it.

class TestGateFilesIntegrity:
    _REPO_ROOT = _HOOK_PATH.parent.parent.parent

    def test_every_gate_files_entry_is_a_real_tracked_file(self):
        result = subprocess.run(
            ["git", "ls-files"], cwd=self._REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        tracked = set(result.stdout.splitlines())
        untracked_or_typo = [p for p in hook._GATE_FILES if p not in tracked]
        assert untracked_or_typo == [], (
            f"_GATE_FILES contains path(s) git doesn't track: {untracked_or_typo} "
            "-- a typo or a stale rename would silently stop gating that file"
        )

    def test_hook_itself_is_a_gate_files_member(self):
        """Pins the 2026-10-02 review M4 decision: the hook must require a
        review citation on its own future changes, same as any other
        decision-engine-core/DB-write file."""
        assert ".claude/hooks/pre_tool_checks.py" in hook._GATE_FILES


class TestSubprocessEncodingExplicit:
    """2026-10-02 review M4 follow-up (see feedback_subprocess_text_mode_
    locale_codec): every text-mode subprocess call in this file must pair
    an explicit encoding= -- without it, Python decodes with the LOCALE
    codec (cp1252 on Windows), which can crash on real git output
    containing non-ASCII bytes (this repo's own commit messages routinely
    do). A structural scan over the hook's own source, not one test per
    call site, so a future 7th call site added without encoding= is caught
    automatically rather than needing someone to remember this convention.

    Known scope limits (2026-10-02 review confirmation pass) -- this file
    today only ever calls `subprocess.run`/`subprocess.Popen` via a bare,
    unaliased `import subprocess`, and only ever uses `text=True` (never
    `universal_newlines=True`, `check_output`/`check_call`, or an aliased
    import like `import subprocess as sp`) -- this scan covers exactly
    that real shape. It would NOT catch a future call site using one of
    those other forms; widen it then, not speculatively now."""

    def test_every_text_true_subprocess_call_pairs_an_explicit_encoding(self):
        import ast

        tree = ast.parse(_HOOK_PATH.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_subprocess_call = (
                isinstance(func, ast.Attribute)
                and func.attr in ("run", "Popen")
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
            )
            if not is_subprocess_call:
                continue
            kwarg_names = {kw.arg for kw in node.keywords if kw.arg}
            is_text_mode = "text" in kwarg_names or "universal_newlines" in kwarg_names
            if is_text_mode and "encoding" not in kwarg_names:
                offenders.append(node.lineno)
        assert offenders == [], (
            f"subprocess call(s) at line(s) {offenders} use text=True with no "
            "explicit encoding= -- add encoding=\"utf-8\", errors=\"replace\""
        )
