#!/usr/bin/env python3
"""Pre-tool-use hook: mechanically enforce hard rules #3, #4 and #5 from
CLAUDE.md, plus a regression-test gate on `git commit`/`git push`
(docs/testing-strategy.md).

  #3  never run the app locally
  #4  decision-engine-core / DB-write commits need an Opus review citation
  #5  `feat(` commits need Design = / Build = provenance trailers

What this CANNOT do: prove a reviewer subagent actually ran. It verifies a
correctly-formatted citation is present. See CLAUDE.md "Review & test economy"
for the honesty caveat and the SubagentStop-hook upgrade path.

Pipeline-efficiency pass (2026-09-28, docs/plans/test-suite-optimization.md +
that day's CI/CD analysis session). Three changes here attack real, measured
waste in this same gate without weakening what it proves:
  - pytest now runs under `pytest-xdist` (-n auto --dist=loadgroup) whenever
    the venv has it installed, falling back to a correct serial run otherwise
    (a missing plugin is an environment gap, not a reason to fail the gate).
    Measured on this machine the day this shipped: 6397 tests, 474.37s serial
    vs ~190s parallel, IDENTICAL pass count both ways -- a real ~2.5x cut.
    First attempt (`-n auto` alone, default `--dist=load`) only reached
    306.45s: it silently defeated an in-process cache 5 slow tests in
    tests/test_check_antipatterns.py relied on (each real-repo AST scan is
    genuinely expensive, and a per-process cache can't be shared across
    xdist's separate worker processes without `--dist=loadgroup` +
    `@pytest.mark.xdist_group`, which is what actually closed the gap from
    306s to ~190s -- see that test file's own comments). Disclosed as
    measured, not assumed, per this project's own doc-integrity standard.
  - The pytest and antipattern subprocesses are now started together and only
    then waited on, so their wall-clock cost overlaps instead of stacking
    (previously fully sequential within one commit or push). Costs nothing:
    the antipattern scan's own ceiling is ~60s, almost always far less, so by
    the time pytest's own wait returns its result is already sitting there.
  - A `git push` immediately following a commit that already passed BOTH
    gates in full, with no further edits, no longer re-runs them. The
    verified-tree marker is written ONLY when a PLAIN commit (no `-a`/
    `--amend` -- write-tree reflects the INDEX only, which isn't provably
    what those two forms actually commit) determined BOTH the pytest and
    antipattern gates were necessary for it AND both genuinely ran and
    passed -- never merely because the commit was plain (fixed 2026-10-02,
    docs/reviews/2026-10-02-review.md Critical #2: a docs-only, or any other
    gate-skipping, commit used to write this marker too, letting it silently
    "vouch" for a tree it never actually gated). At push time the marker's
    tree is compared against `HEAD^{tree}` AND the working tree is checked
    for any staged-uncommitted/unstaged/untracked file under a tested path
    (`stock_analyzer/`, `tests/`, `app.py`, `cron_runner.py`) -- a
    byte-identical tree is necessary but not sufficient, since pytest runs
    against the WORKING tree on disk, not the index the marker records (the
    second gap Critical #2 found: "same tree means same verdict" was false
    whenever uncommitted/untracked files differed from the index). Only when
    both checks agree does re-running prove nothing new. Any mismatch
    (further edits, an -a/--amend commit, a commit from outside this hook,
    or a dirty tested path) falls back to the original always-verify
    behaviour, unchanged.

Bypass hardening (2026-10-02, same review, Medium #5): `git commit -m "..."
<file>` with `<file>` unstaged now has `<file>` unioned into the effectively-
staged list (pathspecs are committed directly by git regardless of the
index -- `_get_staged_files` used to only read the index); and `git -C <dir>
commit` / `git -c <k>=<v> commit` (a git *global* option between `git` and
the subcommand) is now recognised as a commit/push invocation, where before
it matched neither the literal-adjacency regex nor this file's own tokenizer
and so skipped every gate silently.

FIX-FIRST follow-up (2026-10-02, same review pass, 4 blocking findings on the
above two changes -- see docs/reviews/2026-10-02-review.md Critical #2 /
Medium #4 / Medium #5):
  - `_has_git_subcommand` had REGRESSED past what the literal regex it
    replaced used to catch: it returned False outright on a `shlex.split`
    `ValueError` (an apostrophe inside a heredoc/here-string BODY, e.g.
    `git commit -F - <<'EOF'\n...it's...\nEOF`), on a path-qualified
    `/usr/bin/git commit`, and on a `bash -c "git commit ..."` wrapper --
    all of which the OLD literal-adjacency regex matched correctly. Now ORs
    three independent legs (literal regex, a global-option-tolerant regex,
    and the tokenized scan) -- any one saying True is enough, so the
    detector can only get WEAKER if all three miss, not if tokenization
    alone fails. `_is_plain_commit` likewise now returns False (not
    provably plain) on empty/unparseable tokens, rather than silently
    reading "I don't know" as "yes, plain".
  - The verified-tree marker's write condition had TWO more gaps past the
    commit-type/both-gates check: (a) at commit time, `write-tree` reflects
    the INDEX, but pytest runs the WORKING tree -- an unstaged/untracked
    tested-path change at the moment of commit meant the marker could vouch
    for a tree the suite never actually ran against; (b) at push time, a
    full re-run's marker-rewrite was unconditional on "the run was
    attempted", not on "both gates genuinely passed" -- a WARN-only
    antipattern outcome (couldn't be invoked) still rewrote the marker as
    if verified. Both closed: `_should_write_commit_time_marker`/
    `_should_write_push_time_marker` are the sole gates on each write site.
  - `-C <dir>` / `--git-dir` / `--work-tree` detection (Medium #5) only
    fixed WHETHER a redirected commit/push is recognised -- every other
    helper here (`_get_staged_files`, `_write_tree`, `_head_tree`,
    `_tested_paths_dirty`, pytest's own `tests/` relative path, the
    antipattern scan's `os.getcwd()`) still reads the HOOK's own cwd, never
    the redirected target, so the gates would silently evaluate the WRONG
    repo. `_cwd_redirect_mismatch` resolves both toplevels via
    `git ... rev-parse --show-toplevel` and BLOCKS outright (never attempts
    to gate the other repo) on any mismatch or unresolvable comparison.
    **Confirmed coverage, NOT exhaustive (2026-10-02 confirmation-pass
    finding, non-blocking #2):** this genuinely catches `-C <dir>`. It does
    NOT catch a bare `--git-dir <other>/.git` with no `--work-tree` --
    `--show-toplevel` resolves to the hook's own cwd in that shape, so the
    comparison trivially matches and the commit proceeds against the other
    repo's index while the gates check this one. `cd <dir> && git ...` is
    the same underlying risk and is also NOT caught. Both are separate,
    not-yet-built follow-ups -- do not read this function as closing every
    cwd-redirect shape.
  - The pathspec-bypass fix only caught a LITERAL repo-relative file-path
    argument -- a directory (`git commit -m x stock_analyzer`), `.`,
    `./`-prefixed paths, and `--pathspec-from-file=<file>` all still bypassed
    the gate exactly as before. `_get_staged_files` now additionally asks
    GIT what a pathspec/pathspec-file would actually touch
    (`git diff --name-only HEAD -- <pathspec>`), appended to (never
    replacing) the literal list.
"""
import datetime
import json
import os
import re
import shlex
import subprocess
import sys

# Seconds the full pytest suite may take before the hook calls it a hang. Sized
# at ~3x the WORST observed runtime so a merely-growing suite never blocks a
# commit as a false hang. See _finish_pytest.
#
# Re-tuned 2026-08-28: 300 was set against ~110s / 3573 tests on 2026-08-15.
# The suite is now 4500 tests, and observed runs that day ranged 75-130s (the
# 130s was under concurrent load, which is exactly the case that would trip a
# tight limit). At 300 the margin had fallen to ~2.3x.
#
# Why this matters more than it looks: the failure mode is not a slow commit,
# it is a PASSING suite being reported as a hang and blocking every commit —
# the precise false-block the 2026-08-27 fail-closed flip was designed to avoid,
# arriving by a different route. Under-sizing this quietly converts the safety
# net into an outage.
#
# 3x of 130s is 390; rounded to 450 for runway, because the suite grew ~26% in
# under two weeks and re-tuning a timeout weekly is its own failure mode. The
# cost of the larger value is bounded and one-directional: a GENUINE hang wastes
# 7.5 minutes once, rather than a false one blocking work indefinitely.
#
# 2026-09-28 note, not yet acted on: this run against 6397 tests measured
# 474.37s serial / ~190s parallel with -n auto --dist=loadgroup (see module
# docstring) — comfortably back over a 2x margin against 450, so the parallel
# change if anything widened this margin rather than thinning it. Left
# unchanged here anyway because re-tuning a threshold from one measurement is
# exactly the guessing this file's own history warns against; revisit if a
# future session observes parallel runs actually approaching 450s.
_PYTEST_TIMEOUT_SEC = 450

# Durable record of a pytest-gate fail-open (2026-08-27 finding): the single
# stderr line _finish_pytest prints when ok is None is easy to miss and
# leaves no trace once the terminal scrolls -- which is how a fail-open can
# go unnoticed (see the 77205a5 incident). Gitignored: a machine-local
# diagnostic, not repo content.
_VENV_FAIL_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv_fail_open.log")

# Records the last `git write-tree` / `HEAD^{tree}` SHA that already passed
# whichever of the pytest/antipattern gates a PLAIN commit determined it
# needed. Gitignored (machine-local, and content-addressed so it's only ever
# useful on the checkout that produced it).
_VERIFIED_TREE_MARKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_verified_tree.json")


def _log_venv_fail_open(detail: str) -> None:
    """Append one line recording a pytest-gate fail-open. Never raises --
    a logging failure must not compound an already-degraded run."""
    try:
        with open(_VENV_FAIL_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat()}\t{os.getcwd()}\t{detail}\n")
    except Exception:
        pass


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    # Hard rule #3: Never run the app locally
    if re.search(r"\bstreamlit\s+run\b", command, re.IGNORECASE):
        print(
            "BLOCKED (Hard rule #3): App runs on Railway (drishta.up.railway.app), not locally.\n"
            "Push to `main` and wait ~2 min for auto-redeploy, then hard-refresh (Ctrl+F5).",
            file=sys.stderr,
        )
        sys.exit(2)

    is_commit = _has_git_subcommand(command, "commit")
    is_push = _has_git_subcommand(command, "push")

    # cwd/`-C`-redirect mismatch (2026-10-02 review, Blocking 3): every git
    # helper below reads the HOOK's own cwd, never a `-C`/`--git-dir`/
    # `--work-tree` redirect target -- so if one is present and points
    # somewhere else, every subsequent check would silently evaluate the
    # WRONG repo. Refuse outright rather than attempt to gate the other
    # directory. See _cwd_redirect_mismatch's docstring for what this does
    # NOT catch (a plain `cd <dir> &&`).
    if is_commit and _cwd_redirect_mismatch(_tokens(command, "commit")):
        print(
            "BLOCKED (workflow gates): this `git commit` redirects to a different "
            "working tree than the one this hook is running in (a `-C <dir>` / "
            "`--git-dir` / `--work-tree` global option) -- every gate here reads "
            "ITS OWN cwd, so it would silently evaluate the wrong checkout. Run "
            "this command from inside the target checkout instead.\n"
            "(Note: `cd <dir> && git commit ...` is the same underlying risk and "
            "is NOT caught by this check -- a separate, not-yet-built follow-up.)",
            file=sys.stderr,
        )
        sys.exit(2)
    if is_push and _cwd_redirect_mismatch(_tokens(command, "push")):
        print(
            "BLOCKED (workflow gates): this `git push` redirects to a different "
            "working tree than the one this hook is running in (a `-C <dir>` / "
            "`--git-dir` / `--work-tree` global option) -- every gate here reads "
            "ITS OWN cwd, so it would silently evaluate the wrong checkout. Run "
            "this command from inside the target checkout instead.\n"
            "(Note: `cd <dir> && git push ...` is the same underlying risk and "
            "is NOT caught by this check -- a separate, not-yet-built follow-up.)",
            file=sys.stderr,
        )
        sys.exit(2)

    # Hard rule #4: Commits touching gate files require an Opus review citation
    if is_commit:
        staged = _get_staged_files(command)
        triggered = _gate_files_staged(staged)
        message = _commit_message_text(command)
        tokens = _tokens(command)

        # An unresolvable message (editor-driven commit, `-F -` heredoc, a
        # mis-encoded file) fails CLOSED for the citation gate but OPEN for the
        # provenance gate -- "" doesn't start with "feat". Say so out loud
        # rather than let a policy check evaporate silently.
        if not message.strip():
            print(
                "WARNING (workflow gates): could not read this commit's message "
                "(editor-driven commit, `-F -` heredoc, or an unreadable/mis-encoded "
                "file), so the feature-provenance check was SKIPPED. Use "
                "`-F <file>` or `-m` if this is a feat( commit.",
                file=sys.stderr,
            )

        if triggered:
            if not _has_review_citation(message):
                print(
                    f"BLOCKED (Hard rule #4): Staged gate file(s) {triggered} require an Opus review before commit.\n"
                    "Invoke the `reviewer` subagent first, then cite its verdict in the commit body as:\n"
                    "  Review = Opus reviewer (<resolved model>): SHIP|FIX-FIRST, N blocking; <notes>\n"
                    "e.g.  Review = Opus reviewer (Opus 5): SHIP, 0 blocking; verified offline sentinels\n"
                    "The model, verdict and blocking count are all required -- copy the reviewer's own\n"
                    "MODEL: line rather than assuming a version.\n"
                    "(`--amend --no-edit` cannot be verified since the message is unreadable here --\n"
                    "re-supply the existing body via `-F` if HEAD already carries a valid citation.)",
                    file=sys.stderr,
                )
                sys.exit(2)

        # Plan/build provenance (2026-08-15): a `feat(` commit must state who
        # designed it and who built it, so the workflow split is auditable in
        # git history rather than only in a session transcript. "lead" is an
        # accepted answer -- this forces a deliberate statement, not a handoff.
        if _is_feature_commit(message):
            missing = _missing_provenance_trailers(message)
            if missing:
                print(
                    f"BLOCKED (workflow provenance): feature commit is missing {' and '.join(missing)} trailer(s).\n"
                    "Add to the commit body:\n"
                    "  Design = planner (<model>): <verdict>   OR   Design = lead -- <why no planner>\n"
                    "  Build  = implementer (<model>)          OR   Build  = lead -- <why no implementer>\n"
                    "Per CLAUDE.md: new user-facing features route design through `planner` (Opus) and\n"
                    "the build through `implementer` (Sonnet) so the author is not the reviewer.",
                    file=sys.stderr,
                )
                sys.exit(2)

        # Regression-test + recurring-defect gates (docs/testing-strategy.md):
        # block the commit if `pytest tests/` fails or check_antipatterns.py
        # finds a new instance, scoped to commits that actually touch
        # in-scope code so an unrelated docs-only commit isn't slowed down.
        # Started together (not one-after-the-other) so their wall-clock cost
        # overlaps -- see the module docstring's 2026-09-28 note.
        need_pytest = _touches_tested_code(staged)
        need_antipattern = _touches_scanned_code(staged)
        both_gates_passed = False
        if need_pytest or need_antipattern:
            results = _run_gates_concurrently(need_pytest, need_antipattern)
            _apply_gate_results(results, "commit", "committing")
            # _apply_gate_results exits(2) on any blocking outcome, so reaching
            # this line means nothing blocked -- but "didn't block" is NOT the
            # same as "both gates ran and passed" (see _both_gates_passed).
            both_gates_passed = _both_gates_passed(need_pytest, need_antipattern, results)

        # Record the tree as "verified" ONLY when this commit determined BOTH
        # gates were necessary for it AND both genuinely ran and passed --
        # never on a bare plain-commit-type basis alone. A commit that needed
        # no gate at all (e.g. docs-only), needed only one of the two, or saw
        # the antipattern gate fail to invoke, must not "vouch" for the tree
        # at push time (2026-10-02 review, Critical #2). Also still requires a
        # PLAIN commit (write-tree reflects the index only, which `-a`/
        # `--amend` don't provably match), AND a working tree with no
        # unstaged/untracked tested-path change right now -- write-tree
        # reflects the INDEX, but pytest just ran against the WORKING tree on
        # disk, so a dirty tested path means this commit's own gate run does
        # not actually describe the tree about to be recorded (2026-10-02
        # review FIX-FIRST pass, Blocking 2a). See _should_write_commit_time_marker.
        if _should_write_commit_time_marker(both_gates_passed, tokens):
            tree = _write_tree()
            if tree:
                _write_verified_tree(tree)

    # Always re-check before push, regardless of which files are in the
    # commits being pushed -- push sends whatever HEAD currently is, so one
    # suite run against the working tree covers it. Catches the case where a
    # commit landed before this gate existed, or from another session/tool
    # (true again as of 2026-10-02: a gate-skipping commit can no longer
    # overwrite the marker to vouch for an ungated tree -- see Critical #2).
    # EXCEPT: if HEAD's tree is byte-identical to the tree a commit-time run
    # already verified BOTH gates for in full, AND the working tree has no
    # staged-uncommitted/unstaged/untracked file under a tested path, running
    # again proves nothing new (see module docstring) -- skip and say so.
    # Tree-identity alone is NOT sufficient: pytest runs against the WORKING
    # tree on disk, not the index `write-tree` recorded, so a dirty tested
    # path can diverge from what the marker actually verified even with an
    # unchanged HEAD.
    if is_push:
        head_tree = _head_tree()
        verified_tree = _read_verified_tree()
        dirty = _tested_paths_dirty()
        if head_tree and verified_tree and head_tree == verified_tree and not dirty:
            print(
                "INFO (workflow gates): HEAD's tree already passed the pytest+antipattern "
                "gates IN FULL at commit time (both gates ran and passed, not merely "
                "skipped), and no tested path has an uncommitted/untracked change since -- "
                "skipping a second, identical full run. Any further edit, an -a/--amend "
                "commit, a commit from outside this hook, or a dirty tested path forces a "
                "full re-run.",
                file=sys.stderr,
            )
        else:
            results = _run_gates_concurrently(True, True)
            _apply_gate_results(results, "push", "pushing")
            # Only vouch for HEAD's tree when both gates GENUINELY ran and
            # passed -- not merely because the full run was attempted.
            # `_apply_gate_results` already exits(2) on any blocking outcome,
            # but a WARN-only antipattern result (ok is None, couldn't be
            # invoked) does not block, and previously still rewrote the
            # marker as if verified (2026-10-02 review FIX-FIRST pass,
            # Blocking 2b). Also require `not dirty` (confirmation-pass
            # non-blocking #1): pytest here ran against the WORKING tree,
            # which `dirty` (computed above, before this full run) already
            # confirmed was NOT clean on a tested path -- without this check,
            # a dirty-but-passing push would vouch for HEAD's tree even
            # though HEAD itself, in isolation, was never actually tested
            # (e.g. the dirt gets reverted/stashed before a retried push,
            # which would then wrongly skip re-verification).
            if head_tree and not dirty and _should_write_push_time_marker(results):
                _write_verified_tree(head_tree)

    sys.exit(0)


def _both_gates_passed(need_pytest: bool, need_antipattern: bool, results: dict) -> bool:
    """True ONLY when a commit determined BOTH the pytest and antipattern
    gates were necessary for it AND both genuinely ran and returned a literal
    `True` -- not merely "didn't block". `_apply_gate_results` already exits
    the process on any blocking outcome, but "reached past that call" is a
    weaker claim than "both gates verified this tree": the antipattern
    gate's `ok is None` (couldn't be invoked at all, e.g. a missing
    interpreter) only WARNS, it never blocks, and a commit that only needed
    ONE of the two gates never ran the other at all. This is the sole
    condition under which the verified-tree marker may be written (2026-10-02
    review, Critical #2) -- extracted to its own function so the decision is
    unit-testable without invoking `main()`/spawning real gate subprocesses."""
    return (
        need_pytest and need_antipattern
        and results.get("pytest", (None, None))[0] is True
        and results.get("antipattern", (None, None))[0] is True
    )


def _should_write_commit_time_marker(both_gates_passed: bool, tokens: list) -> bool:
    """Whether a commit-time run may vouch for the tree via `write-tree`.

    Requires ALL THREE: both gates genuinely ran and passed
    (`both_gates_passed`, see `_both_gates_passed`), a PLAIN commit
    (`_is_plain_commit` -- `write-tree` reflects the INDEX only, which `-a`/
    `--amend` don't provably match), AND a working tree with no unstaged/
    untracked change under a tested path right now
    (`_tested_paths_dirty(include_staged=False)` -- staged-but-uncommitted
    changes are deliberately excluded here, since they're exactly what's
    being committed and are expected to differ from the pre-commit state;
    only UNSTAGED/UNTRACKED dirt means pytest, which ran against the WORKING
    tree on disk, saw something `write-tree`'s INDEX snapshot does not
    record). Extracted to its own function so this decision is unit-testable
    without invoking `main()` (2026-10-02 review FIX-FIRST pass, Blocking 2a).
    """
    return (
        both_gates_passed
        and _is_plain_commit(tokens)
        and not _tested_paths_dirty(include_staged=False)
    )


def _should_write_push_time_marker(results: dict) -> bool:
    """Whether a push-time full gate run may vouch for HEAD's tree -- only
    when BOTH gates genuinely ran and passed (`_both_gates_passed`), never
    merely because the full run was attempted. `_apply_gate_results` already
    exits(2) on any blocking outcome, so by the time this is checked the
    push itself has already succeeded past that call; this only governs
    whether the marker gets (re)written. A WARN-only antipattern outcome
    (couldn't be invoked at all) does not block the push, but it also did
    not verify anything, so it must not write a marker either (2026-10-02
    review FIX-FIRST pass, Blocking 2b)."""
    return _both_gates_passed(True, True, results)


def _apply_gate_results(results: dict, noun: str, gerund: str) -> None:
    """Prints BLOCKED/WARNING messages for gate results already computed
    (by _run_gates_concurrently) and exits 2 if anything blocks. Decoupled
    from execution so the same reporting logic serves the commit and push
    paths without duplicating message text."""
    blocking = False

    if "pytest" in results:
        ok, detail = results["pytest"]
        if ok is False:
            print(
                f"BLOCKED (regression suite failing): `pytest tests/` did not pass.\n"
                f"{detail}\n"
                f"Fix the failure (or update the test if this is a deliberate policy "
                f"change) before {gerund}.",
                file=sys.stderr,
            )
            blocking = True
        elif ok is None:
            # Fail-CLOSED (2026-08-27, superseding the prior warn-only behaviour):
            # a suite that couldn't run at all is not a softer case than one that
            # ran and failed -- it's the SAME "I have zero signal" state, and this
            # project's own stated position is that the deterministic gates ARE
            # the real pre-deploy safety net.
            print(
                f"BLOCKED (no verified environment): could not run the regression suite -- "
                f"this {noun} cannot proceed.\n"
                f"{detail}\n"
                f"The app's only pre-deploy safety net cannot verify this change at all before "
                f"{gerund} -- not \"probably fine\", genuinely unknown.\n"
                f"Fix: run `pip install -r requirements-dev.txt` in a `.venv` reachable from "
                f"here (the main checkout's .venv is used automatically from a git worktree), "
                f"then retry.",
                file=sys.stderr,
            )
            blocking = True

    if "antipattern" in results:
        ok, detail = results["antipattern"]
        if ok is False:
            print(
                f"BLOCKED (new anti-pattern introduced): scripts/check_antipatterns.py "
                f"found a NEW instance of a recurring bug-class.\n"
                f"{detail}\n"
                f"Fix at the source (see the script's guidance), or — if genuinely "
                f"acceptable — regenerate the baseline deliberately "
                f"(python scripts/check_antipatterns.py --init) before {gerund}.",
                file=sys.stderr,
            )
            blocking = True
        elif ok is None:
            print(f"WARNING: could not run the anti-pattern gate ({detail}) -- not blocking {noun}.", file=sys.stderr)

    if blocking:
        sys.exit(2)


def _run_gates_concurrently(need_pytest: bool, need_antipattern: bool) -> dict:
    """Starts whichever of the pytest/antipattern gates are needed at (almost)
    the same time, so their wall-clock cost overlaps instead of stacking. The
    two are independent subprocesses with no shared state; the antipattern
    scan's own ceiling (~60s, usually far less) is well under pytest's, so
    nothing is lost even when pytest fails slowly -- by the time pytest's own
    wait returns, the antipattern process has almost always already finished.

    Returns {'pytest': (ok, detail), 'antipattern': (ok, detail)} for
    whichever gates were requested; missing keys mean "not requested," not
    "passed" -- callers must gate on `need_pytest`/`need_antipattern`, not on
    key presence.
    """
    pytest_proc = pytest_start_err = None
    anti_proc = anti_start_err = None
    if need_pytest:
        pytest_proc, pytest_start_err = _start_pytest()
    if need_antipattern:
        anti_proc, anti_start_err = _start_antipatterns()

    results: dict = {}
    if need_pytest:
        results["pytest"] = _finish_pytest(pytest_proc, pytest_start_err)
    if need_antipattern:
        results["antipattern"] = _finish_antipatterns(anti_proc, anti_start_err)
    return results


def _xdist_available(py: str) -> bool:
    """Whether pytest-xdist is importable in this interpreter -- checked live
    rather than assumed from requirements-dev.txt, since a .venv created
    before pytest-xdist was added won't have it until the next
    `pip install -r requirements-dev.txt`. Missing it is an environment gap,
    not a code problem, so the gate falls back to a correct serial run
    instead of failing `-n auto` as a pytest usage error."""
    try:
        r = subprocess.run([py, "-c", "import xdist"], capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _pytest_args(py: str) -> list:
    args = [py, "-m", "pytest", "tests/", "-q"]
    if _xdist_available(py):
        # --dist=loadgroup: behaves exactly like the default `load` balancing
        # for the vast majority of (ungrouped) tests, but additionally honours
        # `@pytest.mark.xdist_group(...)` so a handful of tests that share an
        # in-process cache (tests/test_check_antipatterns.py's real-repo scan)
        # land on the same worker instead of each re-paying that cost --
        # `-n auto` alone (--dist=load, the xdist default) ignores the marker
        # entirely and silently defeats that caching (found 2026-09-28).
        args += ["-n", "auto", "--dist=loadgroup"]
    return args


def _start_process(args: list):
    try:
        # encoding/errors explicit (2026-10-02 review M4 follow-up, see
        # feedback_subprocess_text_mode_locale_codec): text=True alone
        # decodes with the LOCALE codec (cp1252 on Windows), which can
        # crash on a pytest failure message containing non-ASCII bytes --
        # these call sites only decide pass/fail on returncode today, so a
        # crash here wouldn't flip a verdict, but it would lose the actual
        # failure detail printed to the user. Applied to all subprocess
        # call sites in this file for the same reason, not just this one.
        return subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
        )
    except Exception:
        return None


def _finish_process(proc, timeout: int):
    """Waits on an already-started Popen up to `timeout` seconds. Returns
    (returncode, stdout, stderr); returncode is None on a timeout (stderr is
    the sentinel "__timeout__") or a wait-time error (stderr carries detail)."""
    if proc is None:
        return None, "", "process failed to start"
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout or "", stderr or ""
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        return None, "", "__timeout__"
    except Exception as e:
        return None, "", f"error: {e}"


def _start_pytest():
    """Starts the pytest gate without waiting on it. Returns (Popen | None,
    detail-if-None)."""
    py = _find_python()
    if not py:
        detail = ".venv python not found -- run `pip install -r requirements-dev.txt` in .venv"
        _log_venv_fail_open(detail)
        return None, detail
    return _start_process(_pytest_args(py)), None


def _finish_pytest(proc, start_detail: str | None):
    """Returns (True, "") on pass, (False, detail) on failure or timeout,
    (None, detail) when the suite couldn't be invoked at all (missing venv).

    A timeout blocks deliberately (it is NOT downgraded to a warning): at
    _PYTEST_TIMEOUT_SEC the suite has a large margin over its normal runtime
    (see that constant's own comment for the measured baseline), so exceeding
    it means a genuine hang, worth stopping a commit for.
    """
    if proc is None:
        return None, start_detail
    rc, out, err = _finish_process(proc, _PYTEST_TIMEOUT_SEC)
    if rc is None:
        if err == "__timeout__":
            return False, (
                f"pytest timed out after {_PYTEST_TIMEOUT_SEC}s -- investigate a hang "
                "before proceeding (see _PYTEST_TIMEOUT_SEC's comment for the measured baseline)"
            )
        detail = f"could not invoke pytest ({err})"
        _log_venv_fail_open(detail)
        return None, detail
    if rc == 0:
        return True, ""
    tail = "\n".join((out or "").strip().splitlines()[-15:])
    return False, tail


def _start_antipatterns():
    script = os.path.join(os.getcwd(), "scripts", "check_antipatterns.py")
    if not os.path.isfile(script):
        return None, "scripts/check_antipatterns.py not found"
    py = _find_python() or sys.executable  # pure stdlib -- any python works
    return _start_process([py, script]), None


def _finish_antipatterns(proc, start_detail: str | None):
    """Returns (True, "") when clean, (False, detail) on a new instance,
    (None, detail) when the gate couldn't run (missing script/python)."""
    if proc is None:
        return None, start_detail
    rc, out, err = _finish_process(proc, 60)
    if rc is None:
        if err == "__timeout__":
            return None, "anti-pattern gate timed out after 60s"
        return None, f"could not invoke the gate ({err})"
    if rc == 0:
        return True, ""
    return False, "\n".join((out or "").strip().splitlines()[-20:])


def _is_plain_commit(tokens: list) -> bool:
    """True when this commit carries neither `-a`/`--all` nor `--amend` --
    the only case where `git write-tree` (which reflects the current INDEX)
    is provably the tree the resulting commit will actually have. `-a`
    stages tracked-but-unstaged changes as part of the commit itself, and
    `--amend` combines the index with HEAD's own commit in a way this hook
    doesn't reproduce -- both fall back to always-verify-on-push instead of
    risking a false "already verified" match.

    Empty/unparseable `tokens` (e.g. `shlex.split` raised `ValueError`) is
    UNKNOWN, not "no flags present" -- unknown must never be read as
    provably plain (2026-10-02 review FIX-FIRST pass, Blocking 1): silently
    returning True here let an unparseable commit still write the
    verified-tree marker."""
    if not tokens:
        return False
    return not _has_flag(tokens, "a", "--all") and "--amend" not in tokens


def _write_tree() -> str | None:
    """The SHA `git commit` would give the resulting tree for a PLAIN commit
    of the current index. None on any git error -- callers must treat that as
    "don't record a verification," never as an empty-but-valid tree."""
    try:
        r = subprocess.run(
            ["git", "write-tree"], capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        out = r.stdout.strip()
        return out if r.returncode == 0 and out else None
    except Exception:
        return None


def _head_tree() -> str | None:
    """The current HEAD commit's tree SHA. None if there's no HEAD yet (a
    brand-new repo) or on any git error -- callers must treat that as
    "can't compare, fall back to a full run," never as a match."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^{tree}"], capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        out = r.stdout.strip()
        return out if r.returncode == 0 and out else None
    except Exception:
        return None


def _read_verified_tree() -> str | None:
    try:
        with open(_VERIFIED_TREE_MARKER, encoding="utf-8") as f:
            tree = json.load(f).get("tree")
        return tree if isinstance(tree, str) and tree else None
    except Exception:
        return None


def _write_verified_tree(tree: str) -> None:
    """Records that `tree` (a git tree SHA) already passed whichever of the
    pytest/antipattern gates were determined necessary for it. Best-effort --
    a failure to write must never block or fail a commit/push; it just means
    the next push re-verifies in full, the original, safe default."""
    try:
        with open(_VERIFIED_TREE_MARKER, "w", encoding="utf-8") as f:
            json.dump({"tree": tree, "verified_at": datetime.datetime.now().isoformat()}, f)
    except Exception:
        pass


# Paths the pytest/antipattern gates actually scan -- mirrors
# _touches_tested_code/_touches_scanned_code's own definition of "in scope".
# Used only to decide whether a push-time marker match is still trustworthy
# (see _tested_paths_dirty); it is deliberately the UNION of both gates'
# scopes (tests/ included even though the antipattern gate never scans it)
# since either gate's result could be stale if its own inputs changed.
_TESTED_PATH_FILES = ("app.py", "cron_runner.py")
_TESTED_PATH_PREFIXES = ("stock_analyzer/", "tests/", ".claude/hooks/")


def _is_tested_path(path: str) -> bool:
    return path in _TESTED_PATH_FILES or any(path.startswith(p) for p in _TESTED_PATH_PREFIXES)


def _tested_paths_dirty(include_staged: bool = True) -> bool:
    """True when a file under a path the gates actually scan has an
    unstaged or untracked change right now -- i.e. the CURRENT working tree
    (what `pytest` actually runs against) no longer matches what a
    commit-time gate run verified, even when `HEAD^{tree}` (the INDEX at
    commit time) is unchanged. Without this check, "byte-identical tree"
    silently stood in for "byte-identical working tree", which are
    different claims whenever an uncommitted edit or a new untracked file
    exists (2026-10-02 review, Critical #2's second gap).

    `include_staged` controls whether a staged-but-uncommitted change also
    counts as "dirty" (default True -- the PUSH-time caller's need: a
    staged file under a tested path means the commit-time marker, if any,
    can't speak to it). The COMMIT-time caller passes `include_staged=False`
    (2026-10-02 review FIX-FIRST pass, Blocking 2a): staged changes are
    exactly what's about to be committed right now, so they are the
    EXPECTED difference from the pre-commit index, not extra dirt relative
    to what pytest, which just ran against the working tree, actually saw --
    counting them would make this permanently True for any real commit and
    defeat the marker entirely.

    `_git_names` already fails open to `[]` on any git error, matching this
    module's existing helpers' style (`_write_tree`/`_head_tree` do the
    same) -- a git-level failure here reads as "nothing dirty found," the
    same permissive default every other helper in this file already uses.
    """
    changed = (
        _git_names("diff", "--name-only")
        + _git_names("ls-files", "--others", "--exclude-standard")
    )
    if include_staged:
        changed += _git_names("diff", "--cached", "--name-only")
    return any(_is_tested_path(f) for f in changed)


def _git_names(*args: str) -> list[str]:
    try:
        r = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
        )
        return r.stdout.strip().splitlines() if r.returncode == 0 else []
    except Exception:
        return []


def _get_staged_files(command: str = "") -> list[str]:
    """Files this commit will actually contain.

    `git commit -a` stages tracked modifications AS PART OF the commit, so at
    PreToolUse time `git diff --cached` is still empty -- which silently voided
    the review-citation, pytest and antipattern gates for any `-am` commit
    (2026-08-15 review finding; it compounded with `-am` not being recognised
    as `-m`). When -a/--all is present we union in tracked-but-unstaged files
    so the gates see the real contents. `--amend` likewise pulls in HEAD's own
    files, since the resulting commit carries them. A pathspec argument on the
    command itself (`git commit -m "..." <file>`) is unioned in too, since git
    commits it directly regardless of the index -- `git diff --cached` alone
    is blind to that case (2026-10-02 review, Medium #5 / bypass 1).

    Widened (2026-10-02 review FIX-FIRST pass, Blocking 4): a literal pathspec
    ARGUMENT only ever matched when it happened to BE an exact relative file
    path -- a DIRECTORY (`git commit -m x stock_analyzer`), `.`, a
    `./`-prefixed path, or a bulk list via `--pathspec-from-file=<file>` all
    bypassed the gate exactly as before that fix, since none of those equal
    the real changed file's own path string. Now we additionally ask GIT what
    a pathspec would actually touch (`git diff --name-only HEAD -- <pathspec>`),
    appended to -- never replacing -- the literal list below: `_git_names`
    already fails open to `[]` on any git error, so this can only ADD names,
    never silently drop the literal fallback. `--pathspec-from-file`'s VALUE
    is NOT threaded through to `git diff` as the same flag -- confirmed via a
    real invocation that `git diff` rejects `--pathspec-from-file` outright
    ("error: invalid option") even though `git commit`/`git add` accept it --
    so its named file's own LINES are read and fed in as regular pathspec
    arguments to the same `git diff -- <pathspec>` call instead.
    """
    staged = _git_names("diff", "--cached", "--name-only")
    tokens = _tokens(command)
    if _has_flag(tokens, "a", "--all"):
        staged += _git_names("diff", "--name-only")
    if "--amend" in tokens:
        staged += _git_names("show", "--pretty=", "--name-only", "HEAD")

    pathspecs = _pathspec_args(tokens)
    pfx_file = _pathspec_from_file_arg(tokens)
    if pfx_file:
        pathspecs = pathspecs + _read_pathspec_file(pfx_file)

    staged += pathspecs
    if pathspecs:
        staged += _git_names("diff", "--name-only", "HEAD", "--", *pathspecs)

    return sorted(set(staged))


# Files whose presence in a commit requires an Opus review citation (CLAUDE.md
# Hard Rule #4: constants, a gate, or a scoring/recommendation formula). This is
# the decision-engine core — a change to any of these can move a real buy/sell
# call, so the citation is required regardless of how small the diff looks (the
# 2026-08-04 Critical was a one-char boundary bug a design review had called
# harmless). Peripheral files that merely *display* or *consume* a score are
# intentionally NOT here — gating all of them would add friction without
# protecting a formula. Broadened 2026-08-04 from the original 5 to match Rule
# #4's written scope (was constants/risk_advisor/exit_advisor/daily_briefing/
# portfolio only; scoring formulas in scoring.py/pillars/ranking/targets were
# unguarded).
_GATE_FILES = {
    "stock_analyzer/constants.py",
    "stock_analyzer/risk_advisor.py",
    "stock_analyzer/exit_advisor.py",
    "stock_analyzer/daily_briefing.py",
    "stock_analyzer/portfolio.py",
    # scoring / recommendation formulas
    "stock_analyzer/scoring.py",        # composite assembly + weight application
    "stock_analyzer/valuation.py",      # valuation pillar
    "stock_analyzer/technicals.py",     # technical pillar
    "stock_analyzer/fundamentals.py",   # business-quality pillar
    "stock_analyzer/ranking.py",        # pick ranking/sort (Grow Today)
    "stock_analyzer/targets.py",        # price targets + R:R feeding ENTER_NOW
    "stock_analyzer/risk.py",           # portfolio risk metrics behind risk gates
    "stock_analyzer/bundle_loader.py",  # verdict assembly + availability gates
    "stock_analyzer/watchlist_advisor.py",  # emits REMOVE / ENTER_NOW verdicts
    # F-260 Phase 3 (added 2026-08-31): Home's risk/fragility/correlation
    # producer orchestration, extracted from app.py. More decision-bearing
    # than coord_freshness.py/outage_gate.py (deliberately NOT gated) --
    # this module SELECTS the offline sentinels a downstream gate (e.g.
    # Watchlist's ENTER_NOW beta check) keys on for safety.
    "stock_analyzer/home_risk_synthesis.py",
    # DB-write / data-integrity + pipeline-trust (added 2026-08-15). CLAUDE.md's
    # review policy already listed "DB-write / data-integrity" as review-required,
    # but the hook didn't implement it -- so the Railway-cutover work (db.py,
    # cron_runner.py, system_health.py) went through a review only because it was
    # flagged by hand. Prose and hook now agree. Deliberately NOT extended to
    # every module that merely *calls* db: db.py is the write choke-point, and
    # gating consumers would add friction without protecting an invariant.
    "stock_analyzer/db.py",             # every persisted write goes through here
    "cron_runner.py",                   # unattended scheduled writer + email
    "stock_analyzer/system_health.py",  # the surface that proves the pipeline ran
    # Added 2026-09-12: three real data-integrity bugs in this one file in
    # 24 hours (373b267, c0d03ae, 2295b95 -- cross-path income-event
    # dedup), each a genuine root cause, not a variant of the same bug.
    # Classifies/dedupes every SnapTrade transaction before it reaches
    # db.py's writers -- squarely "DB-write / data-integrity" per CLAUDE.md's
    # prose policy, which the mechanical list had never actually caught.
    "stock_analyzer/broker_sync.py",
    # Added 2026-09-15 (beta-repair Phase 2): the beta card's lever
    # arithmetic (trim/swap/add dollar targets, relief-per-dollar ranking,
    # margin/leverage side-effect) -- a recommendation-formula module per
    # CLAUDE.md's prose policy from day one, gated mechanically now rather
    # than relying on whoever edits it next to remember the prose rule
    # (the exact gap CLAUDE.md's own "Review & test economy" section warns
    # against -- two 2026-07-15 commits shipped without a required citation
    # for precisely this reason).
    "stock_analyzer/beta_repair.py",
    # Added 2026-10-02 (review M6): CLAUDE.md's own "Review REQUIRED" prose
    # already covers these three -- a scoring/recommendation formula, a
    # hard-gate feeder, and the module deciding what gets emailed as a BUY/
    # protective call -- but the mechanical list had never caught up, so
    # enforcement was honor-system only (every real commit touching them
    # this window got a voluntary review anyway; nothing slipped through
    # yet, per the audit's own note -- this closes the gap before it does).
    "stock_analyzer/etf_scoring.py",          # the ETF composite formula
    "stock_analyzer/sector_fit.py",           # feeds the hard SECTOR_CEILING gate (app + cron)
    "stock_analyzer/headless_alert_engine.py",  # decides the emailed BUY/protective lists
    # Added 2026-10-04 (ETF Phase 2b, docs/plans/etf-multi-asset-support.md
    # "Phase 2b" section): decides whether an ETF becomes a NEW Grow Today
    # buy candidate at all -- the macro/AUM/held-group/composite-bar
    # eligibility gate plus the TRAP-B-safe shadow-composite resolution --
    # a decision surface from day one, same posture as etf_scoring.py.
    "stock_analyzer/etf_candidates.py",
    # Added 2026-10-06 (G-25, docs/plans/cluster-add-gate.md): decides which
    # held tickers get their Grow Today "add-to-winner" suggestion suppressed
    # when a new correlation cluster forms -- a cross-feature coordination
    # surface (a Home/Intelligence-produced signal gating a Grow Today
    # recommendation), same posture as etf_candidates.py from day one.
    "stock_analyzer/cluster_add_gate.py",
    # Added 2026-10-06 (owner-approved, same treatment as etf_candidates.py):
    # the single source of truth for "how many Act Today items are there right
    # now" across every render surface (Home's badge/chip/section headers,
    # Summary's pill/chips) -- a decision surface from day one (what counts as
    # an active vs. demoted/resolved item, and the offline-vs-clear
    # distinction that prevents a crashed Brief build from reading as a false
    # all-clear).
    "stock_analyzer/act_today_view.py",
    # Added 2026-10-06 (Home redesign P2, docs/plans/home-redesign.md): decides
    # offline/clear/act for the top-of-page Act Today pointer -- reads
    # act_today_view()'s already-gated count (never recomputes it) but is
    # itself the decision surface for what that pointer shows, same posture
    # as act_today_view.py.
    "stock_analyzer/act_today_pointer.py",
    # Added 2026-10-02 (review M4, owner decision): this hook is the single
    # choke-point enforcing every gate above -- Critical #2 (the push-gate
    # marker bug) shipped here and took 3 implementation rounds to fully
    # close, which is exactly the "a mistake here silently defeats
    # everything else" risk a reviewer pass is cheapest insurance against.
    # Gating itself on itself is intentional, not circular -- but a citation
    # requirement is a DIFFERENT guarantee from "pytest actually ran": the
    # citation check (below) runs BEFORE the pytest/antipattern gates (later
    # in main()), and this path wasn't covered by _touches_tested_code/
    # _TESTED_PATH_PREFIXES or tests.yml's CI filters, so a hook-only commit
    # ran zero tests anywhere until both were widened in the same commit
    # that added this entry (2026-10-02 review FIX-FIRST pass on this very
    # change -- see _touches_tested_code's own docstring for the full story).
    ".claude/hooks/pre_tool_checks.py",
    # Added 2026-10-07 (owner decision, on the second Opus review's advice):
    # the native-git fallback that enforces the citation + provenance gates
    # when THIS file's PreToolUse registration is inert -- which it currently
    # is, because Accenture's managed settings set allowManagedHooksOnly=true
    # and that blocks every project-declared hook. So git_hook_adapter.py is
    # presently the ONLY live enforcement entry point, which is precisely the
    # "a mistake here silently defeats everything else" argument that put
    # pre_tool_checks.py itself on this list. Earned it the hard way: two
    # review rounds found six blocking defects in it, including one that
    # reproduced real repo corruption (git's GIT_INDEX_FILE leaking into a
    # pytest subprocess whose fixtures then wrote to the real repo) and two
    # silent-pass bypasses the author's own tests had certified as working.
    ".claude/hooks/git_hook_adapter.py",
}


def _gate_files_staged(staged: list[str]) -> list[str]:
    return [f for f in staged if f in _GATE_FILES]


def _touches_tested_code(staged: list[str]) -> bool:
    """Files a passing suite actually says something about.

    app.py and cron_runner.py are included (2026-08-28) even though no test
    imports them: tests/test_repo_hygiene.py byte-compiles BOTH, so the suite
    is the only gate that catches a syntax error in the two entrypoints -- and
    app.py is the largest file in the repo with no unit coverage of its own.
    Leaving them out meant an app.py-only commit ran no suite here AND none in
    CI (.github/workflows/tests.yml path filters omitted them too, fixed in the
    same commit), so the single push-time run was the only execution -- in the
    local .venv, which does not match production's pinned dependency set.

    .claude/hooks/ added 2026-10-02 (review M4 follow-up): this hook was just
    added to _GATE_FILES, so its own commits now require a review citation --
    but a citation requirement alone is NOT the same guarantee as "pytest
    actually ran." Without this line, a hook-only commit triggered zero gates
    here (this exact path wasn't in _TESTED_PATH_PREFIXES either) AND zero in
    CI (tests.yml's path filters didn't cover it), so the hook that enforces
    every other gate could ship with no test run at all -- the same shape of
    false reassurance Critical #2 found in a different corner of this file.
    """
    return any(
        f == "app.py" or f == "cron_runner.py"
        or f.startswith("stock_analyzer/") or f.startswith("tests/")
        or f.startswith(".claude/hooks/")
        for f in staged
    )


def _touches_scanned_code(staged: list[str]) -> bool:
    """Files the anti-pattern gate scans (mirrors TARGETS in check_antipatterns.py)."""
    return any(
        f == "app.py" or f == "cron_runner.py" or f.startswith("stock_analyzer/")
        for f in staged
    )


_SHELL_SEPARATORS = ("&&", "||", ";", "|", "&")

# Git GLOBAL options (ones that can appear BETWEEN `git` and the subcommand,
# e.g. `git -C <dir> commit`, `git -c <k>=<v> commit`) that consume a
# SEPARATE following token as their value. `-C`/`-c` are the two the
# 2026-10-02 review found bypass every gate here (Medium #5 / bypass 2) --
# before this fix, `git -C <dir> commit` matched neither the old literal
# `\bgit\s+commit\b` regex nor this file's own "git" immediately-followed-by
# "commit" scan, so it silently skipped every check. A few other common
# value-taking global options are included so the same gap doesn't recur for
# them.
_GIT_GLOBAL_OPTS_WITH_VALUE = (
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
    "--config-env",  # added 2026-10-02 review FIX-FIRST pass, non-blocking #1
)


def _skip_git_global_opts(toks: list, i: int) -> int:
    """Given `toks[i] == "git"`, returns the index of the SUBCOMMAND token
    (e.g. "commit", "push"), skipping over any global options in between --
    `git -C <dir> commit`, `git -c user.name=x commit`, `git --no-pager
    commit`, etc. A bare boolean global flag (no value) is skipped one token
    at a time; `len(toks)` is returned if the command runs out before a
    subcommand token appears."""
    j = i + 1
    while j < len(toks):
        t = toks[j]
        if not t.startswith("-"):
            return j
        if t in _GIT_GLOBAL_OPTS_WITH_VALUE and j + 1 < len(toks):
            j += 2
            continue
        if any(t.startswith(opt + "=") for opt in _GIT_GLOBAL_OPTS_WITH_VALUE):
            j += 1
            continue
        j += 1
    return j


def _find_subcommand_start(toks: list, subcommand: str) -> int | None:
    """Index of the `git` token that begins a `git <subcommand>` invocation
    within `toks`, tolerating global options between `git` and the
    subcommand. `None` if no such invocation is found. Mirrors the original
    scan's "last match wins" precedence when `git <subcommand>` appears more
    than once (e.g. chained/piped commands)."""
    found = None
    for i, t in enumerate(toks):
        if t == "git":
            sub_i = _skip_git_global_opts(toks, i)
            if sub_i < len(toks) and toks[sub_i] == subcommand:
                found = i
    return found


def _literal_subcommand_re(subcommand: str) -> "re.Pattern":
    """A literal-adjacency regex (`git <subcommand>`), unaffected by shlex's
    quoting rules -- catches a path-qualified `/usr/bin/git commit`, a
    `bash -c "git commit ..."` wrapper, and any heredoc/here-string whose
    BODY contains an apostrophe (which makes `shlex.split` raise
    `ValueError` on an unterminated quote). None of these need real
    tokenization; they only need the literal substring to appear somewhere
    in the raw command text. Its only false-positive mode is matching
    "git <subcommand>" appearing inside a commit MESSAGE string, which is
    the safe failure direction -- an unnecessary gate run, never a missed
    one (2026-10-02 review FIX-FIRST pass, Blocking 1)."""
    return re.compile(rf"\bgit(?:\.exe)?\b\s+{re.escape(subcommand)}\b")


def _tolerant_subcommand_re(subcommand: str) -> "re.Pattern":
    """Matches `git <subcommand>` across git GLOBAL options (`-C <dir>`,
    `-c <k>=<v>`, `--no-pager`, etc.) between `git` and the subcommand,
    WITHOUT needing shlex tokenization -- so it still fires when
    `shlex.split` can't parse the command at all (the same
    apostrophe-in-heredoc-body case `_literal_subcommand_re` also covers,
    kept as a second, overlapping leg since this one additionally tolerates
    the `-C`/`-c` case the literal regex alone does not). Each `-OPT [VALUE]`
    pair is matched permissively (a bare option token, optionally followed by
    a non-dash value token) rather than validated against the real global-
    option vocabulary -- regex backtracking resolves the ambiguity when a
    trailing bare flag's optional value-slot could otherwise swallow the
    subcommand token itself (2026-10-02 review FIX-FIRST pass, Blocking 1)."""
    return re.compile(
        rf"\bgit(?:\.exe)?\b(?:\s+-\S+(?:\s+[^\s-]\S*)?)*\s+{re.escape(subcommand)}\b"
    )


def _has_git_subcommand(command: str, subcommand: str) -> bool:
    """True if `command` contains a `git <subcommand>` invocation.

    Fails CLOSED toward detection (biases toward "yes, this is a
    commit/push", which only costs an unnecessary gate run) via THREE
    independent legs, ORed together -- any one saying True is enough:
      1. `_literal_subcommand_re` -- a plain-text regex, unaffected by
         shlex's quoting rules.
      2. `_tolerant_subcommand_re` -- additionally tolerates git global
         options between `git` and the subcommand, still without real
         tokenization.
      3. The tokenized scan (`_find_subcommand_start`) -- the most PRECISE
         of the three (correctly handles multiple/chained global options,
         quoted values containing spaces, etc.) but returns nothing useful
         whenever `shlex.split` can't parse the command at all.

    A prior version of this function used (3) ALONE, which was a real
    regression (2026-10-02 review FIX-FIRST pass, Blocking 1): it silently
    stopped detecting a path-qualified `/usr/bin/git commit`, a
    `bash -c "git commit ..."` wrapper, and any heredoc/here-string whose
    BODY contains an apostrophe -- all of which the OLD literal-adjacency
    regex this replaced (2026-10-02 review, Medium #5 / bypass 2) used to
    catch correctly. Never trust the tokenized leg alone again."""
    if _literal_subcommand_re(subcommand).search(command):
        return True
    if _tolerant_subcommand_re(subcommand).search(command):
        return True
    try:
        toks = shlex.split(command, posix=True)
    except ValueError:
        return False
    return _find_subcommand_start(toks, subcommand) is not None


def _tokens(command: str, subcommand: str = "commit") -> list:
    """Tokens of the `git <subcommand>` SEGMENT only, not the whole compound
    command (defaults to "commit" -- the only subcommand every other caller
    in this file actually scopes to).

    Scoping matters: option scanning over an entire `A && git commit -m "..."`
    string lets an EARLIER segment's flags win. Verified cases this prevents --
    `sort -m a.txt && git commit -m "feat(x): y"` would collect BOTH -m values,
    so the joined message no longer starts with "feat" and the provenance gate
    silently passes with no warning (the message isn't empty); and
    `ls -la && git commit` would see the `-a` from `ls` and union unstaged
    files. Same fail-open class as the `-am` bug, just lower probability.
    """
    try:
        toks = shlex.split(command, posix=True)
    except ValueError:
        return []

    start = _find_subcommand_start(toks, subcommand)
    toks = toks[start:] if start is not None else toks

    for i, t in enumerate(toks):
        if t in _SHELL_SEPARATORS and i > 0:
            return toks[:i]
    return toks


def _git_global_opts(tokens: list) -> list:
    """The GLOBAL options between `git` and the subcommand in a tokenized
    `git <subcommand> ...` segment (as `_tokens` returns it) -- e.g. for
    `["git", "-C", "/some/dir", "commit", "-m", "x"]` returns
    `["-C", "/some/dir"]`. Empty when `tokens` doesn't start with `git`
    (including empty/unparseable `tokens`) -- see `_cwd_redirect_mismatch`,
    which reads "no global options" as "nothing to check", the safe
    default (2026-10-02 review FIX-FIRST pass, Blocking 3)."""
    if not tokens or tokens[0] != "git":
        return []
    sub_i = _skip_git_global_opts(tokens, 0)
    return tokens[1:sub_i]


def _toplevel(extra_opts: list) -> str | None:
    """The working-tree root `git <extra_opts> rev-parse --show-toplevel`
    would resolve to -- lets GIT ITSELF resolve any stacked/relative `-C`/
    `--git-dir`/`--work-tree` options rather than re-implementing that
    logic here. None on any failure (git not found, bad `-C` target, not a
    repo, etc.) -- callers must treat that as "can't confirm, block", never
    as "no redirect" (2026-10-02 review FIX-FIRST pass, Blocking 3)."""
    try:
        r = subprocess.run(
            ["git", *extra_opts, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        out = r.stdout.strip()
        return out if r.returncode == 0 and out else None
    except Exception:
        return None


def _cwd_redirect_mismatch(tokens: list) -> bool:
    """True when a git global option in `tokens` (e.g. `-C <dir>`,
    `--git-dir=...`, `--work-tree=...`) redirects git to operate on a
    DIFFERENT working tree than the one this hook process itself is running
    in. **Confirmed-covered shape is `-C <dir>` — a bare `--git-dir=<other>`
    with NO accompanying `--work-tree` is NOT reliably caught**: `rev-parse
    --show-toplevel` in that shape can still resolve to the hook's OWN cwd
    (there's no working-tree redirect to detect), so the comparison below
    can trivially "match" while the actual commit/push still targets the
    other repo's index (2026-10-02 confirmation-pass non-blocking #2 — the
    module docstring previously overstated this as fully closed; it isn't).
    Every other git-querying helper in this file (`_get_staged_files`,
    `_write_tree`, `_head_tree`, `_tested_paths_dirty`, pytest's own
    relative `tests/` path, the antipattern scan's `os.getcwd()`) still
    reads the HOOK's own cwd, never a redirected target -- so if this
    returns True and the caller proceeds anyway, every gate would silently
    evaluate the WRONG repo, and a push-time "HEAD's tree already passed...
    IN FULL" message could even describe the wrong checkout (2026-10-02
    review FIX-FIRST pass, Blocking 3). Callers must BLOCK on True, not
    attempt to gate the other directory -- that's the deliberately simple
    fix here.

    Returns False (no redirect / nothing to check) when there are no global
    options -- the common case, and the only one `_git_global_opts` can
    even detect (an unparseable command yields empty tokens, so this is
    also the same known gap `_has_git_subcommand`'s tokenized leg has: a
    `cd <dir> && git ...` redirect, or any command whose tokens couldn't be
    resolved at all, is NOT caught here either -- see the module docstring).
    Fails CLOSED (True, i.e. "block") when either toplevel can't be
    resolved, since an unresolvable comparison is not a confirmed match."""
    extra = _git_global_opts(tokens)
    if not extra:
        return False
    target = _toplevel(extra)
    here = _toplevel([])
    if not target or not here:
        return True
    t1, t2 = os.path.realpath(target), os.path.realpath(here)
    if os.name == "nt":
        t1, t2 = t1.casefold(), t2.casefold()
    return t1 != t2


def _is_short_opt(token: str, letter: str) -> bool:
    """True for a short option carrying `letter`, INCLUDING inside a cluster.

    `-am` is one token, not two, so a whole-token `== "-m"` comparison misses
    it entirely. That gap let `git commit -am "feat(x): y"` skip the provenance
    gate outright (2026-08-15 review finding). Matches `-m` and `-am`, and for
    value-taking options only when `letter` is LAST in the cluster -- `-am`
    takes its value as the next argv element, whereas in `-ma` the `a` would be
    consumed as the message text by git itself.
    """
    return bool(re.fullmatch(rf"-[A-Za-z]*{letter}", token))


def _has_flag(tokens: list, letter: str, *long_forms: str) -> bool:
    """True if a short flag (bare or clustered) or any long form is present."""
    for t in tokens:
        if t in long_forms or re.fullmatch(rf"-[A-Za-z]*{letter}[A-Za-z]*", t):
            return True
    return False


# `git commit`'s OWN options (as opposed to the global ones in
# _GIT_GLOBAL_OPTS_WITH_VALUE, which appear BEFORE the subcommand) that
# consume a separate following token as their value. Short forms resolved via
# _is_short_opt (handles clustering, e.g. a trailing `-m` in `-qm`); long
# forms via the `--opt value` / `--opt=value` convention. Deliberately NOT
# exhaustive of every git-commit option -- scoped to the value-taking ones,
# since anything else is either a no-value flag (skip one token) or a
# positional pathspec (keep).
_VALUE_SHORT_OPTS = ("m", "F", "c", "C", "t")  # -m/-F/-c/-C/-t all take a value
_VALUE_LONG_OPTS = (
    "--message", "--file", "--reuse-message", "--reedit-message",
    "--fixup", "--author", "--date", "--template", "--cleanup", "--squash",
    # Added 2026-10-02 (review FIX-FIRST pass, non-blocking #1): these fail
    # CLOSED today (over-including -- their value-token is skipped as an
    # option, never mistaken for a pathspec, so missing them was never a
    # gate-bypass, just noise), but cheap to close in the same pass.
    "--trailer", "--pathspec-from-file",
)


def _commit_args(tokens: list) -> list:
    """`tokens` minus the leading `git` + any global options + the `commit`
    subcommand itself -- i.e. just commit's own options and pathspecs. Empty
    if `tokens` doesn't actually start with a `git commit` invocation."""
    if not tokens or tokens[0] != "git":
        return []
    sub_i = _skip_git_global_opts(tokens, 0)
    if sub_i >= len(tokens) or tokens[sub_i] != "commit":
        return []
    return tokens[sub_i + 1:]


def _pathspec_args(tokens: list) -> list[str]:
    """Positional (non-option) arguments on a `git commit` invocation --
    these are PATHSPECS, which git stages and commits directly regardless of
    the index: `git commit -m "..." <file>` commits `<file>` even when it was
    never `git add`-ed. `_get_staged_files`'s index-only read (`git diff
    --cached`) is blind to this, so a commit made entirely this way used to
    skip the citation gate and pytest/antipattern gates outright (2026-10-02
    review, Medium #5 / bypass 1)."""
    args = _commit_args(tokens)
    out = []
    i = 0
    while i < len(args):
        t = args[i]
        if t == "--":
            out.extend(args[i + 1:])
            break
        if t.startswith("--"):
            if t in _VALUE_LONG_OPTS and i + 1 < len(args):
                i += 2
            else:
                i += 1  # bare long flag, or an `--opt=value` form (one token)
            continue
        if t.startswith("-") and t != "-":
            if any(_is_short_opt(t, letter) for letter in _VALUE_SHORT_OPTS) and i + 1 < len(args):
                i += 2
            else:
                i += 1
            continue
        out.append(t)
        i += 1
    return out


def _read_pathspec_file(path: str) -> list[str]:
    """The pathspec entries named by a `--pathspec-from-file=<path>` value --
    read directly rather than threaded through to `git diff` as the same
    flag, since `git diff` rejects `--pathspec-from-file` outright on this
    project's git version (confirmed via a real invocation: "error: invalid
    option"), even though `git commit`/`git add` accept it. `-` (stdin) is
    unresolvable here, same as `_commit_message_text`'s `-F -` case -- the
    hook has no way to see stdin, so it returns `[]` rather than guessing.
    Empty on any read error, matching this file's existing fail-open style
    for anything pathspec-shaped (2026-10-02 review FIX-FIRST pass,
    Blocking 4)."""
    if path == "-":
        return []
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception:
        return []


def _pathspec_from_file_arg(tokens: list) -> str | None:
    """The value of `--pathspec-from-file[=<file>]` on a `git commit`
    invocation, if present -- a bulk pathspec-list file, same bypass class
    as a positional pathspec argument: git commits whatever it names
    directly, regardless of the index (2026-10-02 review FIX-FIRST pass,
    Blocking 4). `--pathspec-from-file` is itself added to `_VALUE_LONG_OPTS`
    so `_pathspec_args`'s own scan skips over it (and its value) correctly
    rather than misreading the value as a literal pathspec."""
    args = _commit_args(tokens)
    for i, t in enumerate(args):
        if t.startswith("--pathspec-from-file="):
            return t.split("=", 1)[1]
        if t == "--pathspec-from-file" and i + 1 < len(args):
            return args[i + 1]
    return None


def _read_message_file(path: str) -> str:
    """Read a commit-message file. `utf-8-sig` transparently strips a BOM, and
    errors='replace' keeps a mis-encoded file (e.g. UTF-16 from PowerShell 5.1
    Out-File) from collapsing to "" and silently skipping the provenance gate."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def _commit_message_text(command: str) -> str:
    """Best-effort recovery of the full commit message from the git command.

    Covers `-F <file>` / `--file <file>` / `--file=<file>` (the project's
    convention is `-F .git/COMMIT_MSG.txt`) and `-m` / `--message` /
    `--message=`, each including clustered short forms like `-am`.

    Returns "" when the message genuinely cannot be resolved -- an unreadable
    named file, or `-F -` (heredoc piped to stdin, which the hook cannot see).
    Callers must treat "" as "cannot verify" and WARN, because it fails CLOSED
    for the review-citation gate but OPEN for the provenance gate (an empty
    string doesn't start with "feat"). That asymmetry is why `main()` prints an
    explicit warning rather than relying on the empty value alone.
    """
    tokens = _tokens(command)

    for i, t in enumerate(tokens):
        if t.startswith("--file="):
            return _read_message_file(t.split("=", 1)[1])
        if (_is_short_opt(t, "F") or t == "--file") and i + 1 < len(tokens):
            path = tokens[i + 1]
            # `-F -` reads the message from stdin (heredoc). git succeeds; the
            # hook has no way to see it. Unresolvable, not empty-and-fine.
            return "" if path == "-" else _read_message_file(path)

    msgs = []
    for i, t in enumerate(tokens):
        if t.startswith("--message="):
            msgs.append(t.split("=", 1)[1])
        elif (_is_short_opt(t, "m") or t == "--message") and i + 1 < len(tokens):
            msgs.append(tokens[i + 1])
    if msgs:
        # git joins repeated -m blocks with a blank line
        return "\n\n".join(msgs)
    return ""


# Hardened 2026-08-15. The old check was a bare `Review = Opus reviewer`
# substring, which any commit body could satisfy by accident or habit. It now
# requires the reviewer's RESOLVED model in parens, an explicit verdict, and a
# blocking count -- i.e. the three facts you only have after actually reading a
# reviewer's output. This narrows lazy/accidental citations; it cannot stop a
# deliberately fabricated one (THIS hook cannot prove a subagent ran -- a
# SubagentStop hook could, and CLAUDE.md names that upgrade path as available
# but unbuilt). An honesty mechanism, not a guarantee -- see CLAUDE.md.
#   e.g. Review = Opus reviewer (Opus 5): SHIP, 0 blocking; ...
#
# Two subtleties, both found in review rather than by reading:
#   • the model group is `[^\n]+` (greedy, line-bounded), NOT `[^)\n]+` -- a
#     resolved MODEL: line can itself contain parens, e.g.
#     "(Opus 4.8 (1M context))", and Hard Rule #4 tells the author to copy that
#     line verbatim. The stricter class rejected a legitimate citation, which
#     would be an unexplainable false block.
#   • the verdict→count span uses `[\s\S]{0,120}?`, not `[^\n]*?` -- at 72-col
#     wrapping "SHIP,\n0 blocking" is as likely as "SHIP, 0 blocking", and a
#     newline-intolerant span made passing a lottery decided by line width.
_REVIEW_CITATION_RE = re.compile(
    r"Review\s*=\s*Opus reviewer\s*\(\s*[^\n]+\s*\)\s*:\s*"
    r"(?:SHIP|FIX-FIRST)\b[\s\S]{0,120}?\b\d+\s+blocking\b",
    re.IGNORECASE,
)

# Feature commits must state who designed and who built, so the plan/build/review
# split is auditable in git history forever rather than living only in a session
# transcript. "lead" is an ACCEPTED answer -- the point is a deliberate statement,
# not a forced handoff. Both are self-attested, same caveat as the review citation.
#   Design = planner (Opus 5): <verdict>      |  Design = lead -- <why no planner>
#   Build  = implementer (Sonnet 5)           |  Build  = lead -- <why no implementer>
_DESIGN_TRAILER_RE = re.compile(r"^\s*Design\s*=\s*\S+", re.MULTILINE | re.IGNORECASE)
_BUILD_TRAILER_RE = re.compile(r"^\s*Build\s*=\s*\S+", re.MULTILINE | re.IGNORECASE)
_FEAT_COMMIT_RE = re.compile(r"^\s*feat[(!:]", re.IGNORECASE)


def _has_review_citation(message: str) -> bool:
    return bool(_REVIEW_CITATION_RE.search(message))


def _is_feature_commit(message: str) -> bool:
    return bool(_FEAT_COMMIT_RE.match(message.lstrip("﻿")))


def _missing_provenance_trailers(message: str) -> list[str]:
    missing = []
    if not _DESIGN_TRAILER_RE.search(message):
        missing.append("Design")
    if not _BUILD_TRAILER_RE.search(message):
        missing.append("Build")
    return missing


def _venv_candidates(root: str) -> tuple:
    return (
        os.path.join(root, ".venv", "Scripts", "python.exe"),  # Windows
        os.path.join(root, ".venv", "bin", "python"),          # POSIX
    )


def _find_python() -> str | None:
    """Locate the project .venv's python.

    Checks the current directory first (the fast path -- unchanged from
    before, zero extra cost when it hits). Falls back to the MAIN checkout's
    .venv via `git rev-parse --git-common-dir` when not found there.

    Why the fallback: `.venv/` is gitignored by design (venvs are never
    committed), so a git worktree -- used by this project's agent isolation
    and by ad-hoc verification checkouts -- structurally has no .venv of its
    own. But a worktree shares the exact same requirements-dev.txt as the
    main checkout, so borrowing its interpreter runs the real pinned suite,
    not a downgrade. Verified from an actual worktree, 2026-08-27.
    """
    for candidate in _venv_candidates(os.getcwd()):
        if os.path.isfile(candidate):
            return candidate

    try:
        r = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
        )
        if r.returncode == 0 and r.stdout.strip():
            main_root = os.path.dirname(os.path.abspath(r.stdout.strip()))
            for candidate in _venv_candidates(main_root):
                if os.path.isfile(candidate):
                    return candidate
    except Exception:
        pass

    return None


if __name__ == "__main__":
    main()
