#!/usr/bin/env python
"""Native-git enforcement of DRISHTA's workflow gates.

WHY THIS EXISTS (2026-10-07, scope widened 2026-10-08). `pre_tool_checks.py` is
registered as a Claude Code `PreToolUse` hook in `.claude/settings.json`. That
registration is INERT: Accenture's managed settings set
`allowManagedHooksOnly: true`, which per Anthropic's docs means "hooks in your
user, project, and local settings files don't run and aren't listed." Verified
live -- a Bash command containing `streamlit run` executed instead of being
blocked. The script is healthy; only the Claude-Code-side INVOCATION is blocked.

**That is now PERMANENT: Accenture's Claude admin declined the allowlist request
on 2026-10-08. There is no exception coming, so this file is not a stopgap --
it is the enforcement layer.** Here git is the caller, so no managed-settings
policy can switch it off.

================== WHAT THIS DOES AND DOES NOT COVER ==================
`commit-msg` -- TEXT gates, on an ordinary `git commit`:
  * Hard Rule #4 -- an Opus review citation when a `_GATE_FILES` member is staged
  * Hard Rule #5 -- `Design =` / `Build =` trailers on a `feat(` commit, and a
    `Build =` trailer on a `fix(` / `refactor(` / `perf(` commit

`pre-commit` -- CODE gates, when the staged set touches the relevant paths. They
run in a temporary detached worktree checked out from a snapshot of the INDEX
(write-tree -> commit-tree -> worktree add), so they test what is being
committed, not whatever is on disk (audit C-H1). If the snapshot cannot be built
they fall back to the working tree with a loud stderr WARNING; they never block
on snapshot plumbing alone (pytest itself still fails closed):
  * the full pytest suite      (`_touches_tested_code`)
  * scripts/check_antipatterns.py  (`_touches_scanned_code`)

`pre-push` -- a second full pytest run, for any push that is not purely
deletions, EXCEPT when every commit being pushed is already covered (2026-10-30,
to stop paying the full suite twice per push). It is the only gate that fires at
all for a commit `pre-commit` never saw (made with --no-verify, by
cherry-pick/revert/rebase/`am`, an amend that staged no tested code, or before
these hooks were installed). THE SKIP RULE: `pre-commit` appends the tree sha of
every snapshot it tested GREEN to `<git common dir>/drishta-tested-trees` (never
committed; shared by linked worktrees). At push time each new commit (those not
on the push's remote; tag refs are processed like branches) is "covered" if
(a) its tree sha is stamped there, or (b) it is a single-parent commit that
touches no tested path (`_touches_tested_code`) AND no suite-input doc
(`_is_push_suite_input`: docs the tests read but the gate does not scan), on a
parent that is itself covered or already pushed. Only if EVERY new commit is
covered does the hook skip the run. An unstamped merge or root commit, an
unstamped code-touching or suite-input-doc commit (--no-verify, cherry-pick,
rebase, amend, `am`), any git error, or more than 200 new commits means NOT
covered, and the full run happens as before. GitHub CI re-runs the suite only
for pushes matching tests.yml's path filters, and it does not gate the deploy,
so the skip message must not claim otherwise.

  **READ THIS BEFORE CALLING IT A BACKSTOP.** pytest runs against the WORKING
  TREE, never against the commits being pushed. When the tree does not match
  what is going out -- tested paths dirty, or a refspec pushing something other
  than HEAD -- a PASS says nothing about the pushed commits, so the hook prints
  an explicit MISMATCH warning naming the reason. It does not block on that:
  blocking every push with unrelated WIP in the tree would train everyone to
  reach for --no-verify, which costs more than it buys. **The uncovered case is
  real:** commit broken code with --no-verify, fix it in the tree without
  committing, push -- the suite passes on the fixed tree and the broken commit
  ships. The warning is the only thing that surfaces that.

STILL NOT GATED, by design:
  * cherry-pick / revert / rebase / `git am` run NO `pre-commit` and NO
    `commit-msg` -- `pre-push` is the only thing that sees them.
  * A clean `git merge` / `git pull` merge commit runs NEITHER -- git calls
    `pre-merge-commit`, which is not installed. (A merge finished by hand after
    conflicts DOES run `pre-commit`, with the correct file set.)
  * `git commit --amend` runs `pre-commit`, but `diff --cached` there shows only
    what is NEWLY staged against the commit being amended -- so a message-only
    amend runs NO pytest. Its `commit-msg` TEXT gates are also indistinguishable
    from an ordinary commit's (see "WHY NO AMEND DETECTION"), so an amend that
    drops a review citation stays a manual-verification case.
  * Hard Rule #3 (never run the app locally) has no native-git equivalent at
    all; it was only ever enforceable at the PreToolUse layer, which is dead.
    It is now a text rule only.

Each mode prints what it DID and DID NOT run, on both pass and block, so a
BLOCKED message is never mistaken for full cover.

It reads the WORKING-TREE copies of this file and of `pre_tool_checks.py`, not
the staged ones -- so an unstaged local edit weakening `_GATE_FILES` weakens the
gate for the commit in progress. The PreToolUse hook has the same exposure.
=======================================================================

WHY NO AMEND DETECTION. Two Opus review rounds (2026-10-07) both found amend
handling broken, the second time reproducing it. The prepare-commit-msg
approach cannot work: git passes `source=message` for BOTH `--amend -m` and
`--amend -F`, which is this project's own convention, so an amend is
indistinguishable from a normal commit at that layer. Only `--amend --no-edit`
and editor amends pass `commit`. A correct implementation needs a
`reference-transaction` hook, which was judged not worth the complexity for a
stopgap. **So: an amend that drops a review citation will NOT be caught here.**
Treat `git commit --amend` on a gate-file commit as a manual-verification case.

THE ENV-SCRUB, AND WHY IT IS LOAD-BEARING. v1 of this file shelled out to
`pre_tool_checks.py`, which spawned pytest, whose `tmp_git_repo` fixture then ran
`git init/add/commit` against the REAL repository -- because git exports
`GIT_INDEX_FILE` (absolute, for `-a` and pathspec commits) and `GIT_DIR`
(worktrees) into the hook environment. Review reproduced an index pointing at
non-existent blobs, `.git/config` rewritten to `user.email=test@example.com`,
and ~57-deep hook recursion. This repo makes that worse than the general case:
`core.hooksPath` is an ABSOLUTE path into `.git/hooks`, so a child git command
redirected by an inherited `GIT_DIR` finds these very hooks and re-enters them.

**Never spawn pytest from a git hook without scrubbing
`git rev-parse --local-env-vars` from the child environment.** The `commit-msg`
mode still spawns nothing but git queries, which SHOULD inherit `GIT_INDEX_FILE`
(at commit-msg time it correctly names the real commit index). The `pre-commit`
and `pre-push` modes DO spawn pytest, so they call `_scrub_git_env()` first.

ORDER MATTERS IN `pre-commit`, AND IT IS NOT INCIDENTAL. The staged-file list is
resolved BEFORE the scrub, because `git diff --cached` needs the inherited
`GIT_INDEX_FILE` to see the right index (with `-a`, git stages into a TEMPORARY
index and points that variable at it; scrubbing first would silently read the
on-disk index instead and miss every `-a` change). pytest is spawned AFTER, when
the variables are gone. Reversing these two steps silently UNDER-gates: the
staged list comes back empty, both gates are skipped, and the hook prints a
clean pass. Nothing in the control flow makes the ordering self-evident, so it
is pinned by `test_staged_files_are_resolved_before_the_scrub`, which records
whether `GIT_INDEX_FILE` was still set at each step. Reorder these and that test
fails -- it is the only thing standing between here and a silent no-op gate.

NO LOGIC IS DUPLICATED -- the regexes, `_GATE_FILES` and the predicates are
imported from `pre_tool_checks.py`, so the two entry points cannot drift.

FAILS CLOSED, with ONE deliberate exception. Every error path blocks: an
unreadable message, a failing git query, a stripspace failure, an unknown mode,
any exception, and a pytest run that could not be invoked at all. `--no-verify`
is the single escape hatch. Failing open is the exact silent-gate bug this file
exists to fix, so there is no "benefit of the doubt" path -- EXCEPT the
antipattern gate, which fails open (a missing script or a timeout warns and
allows), matching `pre_tool_checks.py`'s long-standing behaviour for it.

INSTALL / RE-INSTALL:  python .claude/hooks/git_hook_adapter.py --install
Installs all three hooks. `.git/hooks/` is outside the work tree, so this is
PER-CLONE and must be re-run after a fresh clone. It is NOT per-worktree: linked
worktrees SHARE the clone's hooks directory, so one install covers them all --
what a worktree additionally needs is a checkout new enough to contain this
file, which is why the shim checks for it. Re-running is safe and idempotent.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

_TESTED_TREES_NAME = "drishta-tested-trees"
_TESTED_TREES_KEEP = 2000
_PUSH_COVER_MAX_COMMITS = 200

# Files the TEST SUITE reads although `pre_tool_checks._touches_tested_code`
# deliberately does not treat them as tested code (docs, SQL, the commit
# template). A commit touching one is NOT "docs-only" for the push skip: it can
# change a test's verdict. Pinned by `test_every_non_tested_repo_file_a_test_reads_is_a_suite_input`
# in tests/test_git_hook_adapter.py, which scans tests/*.py for repo-file reads.
_PUSH_SUITE_INPUT_FILES = (
    "docs/requirements.md", "docs/architecture.md", ".gitmessage.txt",
)
_PUSH_SUITE_INPUT_PREFIXES = ("docs/sql/",)


def _is_push_suite_input(path: str) -> bool:
    return path in _PUSH_SUITE_INPUT_FILES or any(
        path.startswith(p) for p in _PUSH_SUITE_INPUT_PREFIXES
    )

_HERE = os.path.dirname(os.path.abspath(__file__))

# hook name -> this script's mode argument. The mode is passed BEFORE "$@" so
# git's own hook arguments keep their documented positions relative to it; see
# `_run_gates`'s argv comment for why that offset is easy to get wrong.
_HOOKS = {
    "commit-msg": "commit",
    "pre-commit": "pre-commit",
    "pre-push": "pre-push",
}

# The existence check is not defensive padding. `.git/hooks/` is SHARED by every
# linked worktree and survives any checkout, so these hooks also fire in trees
# that predate this file (old branches, a bisect, an agent worktree created
# before it landed). Without the check the user sees python's "can't open file"
# and no hint that --no-verify is the right answer there.
_SHIM = """#!/bin/sh
# DRISHTA workflow gates -- native-git enforcement.
# Generated by .claude/hooks/git_hook_adapter.py; see that file for scope.
# Bypass with --no-verify, same as any git hook.
ADAPTER=".claude/hooks/git_hook_adapter.py"
if [ ! -f "$ADAPTER" ]; then
  echo "BLOCKED (workflow gates): $ADAPTER is not present in this checkout." >&2
  echo "  .git/hooks/ is shared across worktrees and checkouts, so this hook" >&2
  echo "  fires even where the adapter does not exist (an older commit, a" >&2
  echo "  bisect, a worktree created before it landed)." >&2
  echo "  Re-run the gates by hand, or use --no-verify if that is deliberate." >&2
  exit 1
fi
exec python "$ADAPTER" {mode} "$@"
"""

# Scrubbed from the environment before any non-git child is spawned. Taken from
# `git rev-parse --local-env-vars` at runtime, UNION this hard-coded copy so a
# failed git query still removes the dangerous ones rather than silently
# scrubbing nothing. Captured from git 2.x on 2026-10-08.
_GIT_ENV_FALLBACK = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT", "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE", "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
    "GIT_SHALLOW_FILE", "GIT_COMMON_DIR", "GIT_INDEX_VERSION", "GIT_NAMESPACE",
)

# `git commit -v` appends the staged diff below a scissors line. That diff can
# contain text satisfying these very gates -- docs/cost-routing.md and several
# docs/plans/*.md hold REAL citation lines, and the docs-sync rule makes
# doc+gate-file commits routine. `git stripspace --strip-comments` removes only
# the marker line itself, not the diff beneath it, so the message must be cut
# here first. Reproduced in review: an editor-driven `git commit -v` with
# constants.py staged passed with no citation of its own.
_SCISSORS_RE = re.compile(r"^\S+ -+ >8 -+\s*$", re.MULTILINE)


def _git(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


_BLOCK_LOG_NAME = "last-hook-block.log"


def _record_block(mode: str, text: str) -> str | None:
    """Also write a BLOCKED message to `git rev-parse --git-path
    last-hook-block.log` (`.git/` in the main checkout, `.git/worktrees/<name>/`
    in a linked worktree), so it survives a caller that filtered or truncated
    stderr. A 2026-10-12 one-off pre-commit block could not be diagnosed
    afterwards because its output had been grep-filtered (memory
    feedback_never_filter_hook_output). Every BLOCKED path in this file calls
    it. Overwrites: it holds the LAST block only, and a later PASS does not
    clear it -- read its timestamp. Never committed. Returns the path written,
    or None -- called only after the verdict is fixed, and must never change it."""
    try:
        r = _git("rev-parse", "--git-path", _BLOCK_LOG_NAME)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        path = os.path.abspath(r.stdout.strip())
        import datetime
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                     f"[{mode}]\n{text}\n")
        return path
    except Exception:  # noqa: BLE001 - diagnostics only, never block on it
        return None


def _git_lines(*args: str) -> list[str]:
    """Run a git query, or RAISE. A silently-empty result disables the gate."""
    r = _git(*args)
    if r.returncode != 0:
        raise RuntimeError(f"`git {' '.join(args)}` failed: {r.stderr.strip()}")
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def _install() -> int:
    r = _git("rev-parse", "--git-path", "hooks")
    hooks_dir = r.stdout.strip() if r.returncode == 0 else os.path.join(".git", "hooks")
    hooks_dir = os.path.abspath(hooks_dir)
    if f"{os.sep}.git{os.sep}" not in hooks_dir + os.sep:
        print(
            f"WARNING: core.hooksPath resolves outside .git ({hooks_dir}); "
            "installing there may overwrite hooks you did not create.",
            file=sys.stderr,
        )
    os.makedirs(hooks_dir, exist_ok=True)
    for hook_name, mode in sorted(_HOOKS.items()):
        path = os.path.join(hooks_dir, hook_name)
        note = ""
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                existing = fh.read()
            if "git_hook_adapter" not in existing:
                # Back it up rather than destroy it. `pre-push` in particular is
                # the hook git-lfs installs, and this repo has lfs filters
                # configured -- silently replacing it would break lfs with no
                # trace. A warning printed AFTER an overwrite is not a remedy.
                # Never clobber an earlier backup: a second --install over a
                # second unrelated hook would otherwise destroy the first one's
                # only copy, which is the exact loss the backup exists to stop.
                backup = path + ".bak"
                n = 2
                while os.path.exists(backup):
                    backup = f"{path}.bak{n}"
                    n += 1
                with open(backup, "w", encoding="utf-8", newline="") as fh:
                    fh.write(existing)
                note = f"  (replaced an unrelated hook; original saved to {backup})"
                print(
                    f"WARNING: {path} already held an unrelated hook. It has been "
                    f"backed up to {backup} -- if you need both, chain them by hand.",
                    file=sys.stderr,
                )
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_SHIM.format(mode=mode))
        os.chmod(path, 0o755)
        print(f"installed {path}{note}")
    print()
    print("commit-msg : review citation + provenance trailers (feat/fix/refactor/perf)")
    print("pre-commit : pytest + antipattern check, when staged paths need them")
    print("pre-push   : full pytest suite, for any push that adds commits, unless")
    print("             every pushed commit's tree already passed at pre-commit, or")
    print("             is a docs-only commit (no tested path, no suite-input doc)")
    print("             sitting on a covered or already-pushed parent -- then skipped")
    print()
    print("NOT gated: cherry-pick / revert / rebase / am and a clean merge skip")
    print("BOTH commit-msg and pre-commit -- only pre-push sees them. `--amend`")
    print("runs pre-commit, but only over newly-staged changes, and its text")
    print("gates are indistinguishable from an ordinary commit's.")
    print()
    print("When pre-push does run, pytest uses the WORKING TREE, not the pushed")
    print("commits; it prints a MISMATCH warning when those differ.")
    return 0


def _clean_message(raw: str) -> str:
    """Cut the `-v` diff, then strip `#` comments. Over-cutting fails CLOSED."""
    m = _SCISSORS_RE.search(raw)
    if m:
        raw = raw[: m.start()]
    r = subprocess.run(
        ["git", "stripspace", "--strip-comments"], input=raw,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"`git stripspace` failed: {r.stderr.strip()}")
    return r.stdout


def _changed_files() -> list[str]:
    """Files this commit will contain.

    At commit-msg time the index is FINAL -- git has already applied `-a` and
    any pathspec -- so `diff --cached` alone is complete here, unlike the
    PreToolUse path which must infer them from the command string.

    `--no-renames` because rename detection reports only the NEW path: a
    `git mv stock_analyzer/constants.py constants_old.py` would otherwise drop
    the gate-file name and skip the citation gate (reproduced in review).

    `-z` because `core.quotePath` (on by default) renders a non-ASCII path as a
    C-quoted string like `"stock_analyzer/\\303\\251.py"`. Every consumer here
    matches with `in _GATE_FILES` or `.startswith(...)`, so a quoted path misses
    silently -- the gate would skip rather than fire. NUL-separated output is
    never quoted.
    """
    r = _git("diff", "--cached", "--name-only", "--no-renames", "-z")
    if r.returncode != 0:
        raise RuntimeError(f"`git diff --cached` failed: {r.stderr.strip()}")
    return sorted({p for p in r.stdout.split("\0") if p})


def _scrub_git_env() -> list[str]:
    """Strip git's per-invocation variables from THIS process's environment so
    that every child spawned afterwards -- pytest above all -- cannot be
    redirected back onto the real repository.

    This is the guard against the v1 corruption class described in the module
    docstring, not a tidiness measure. Returns the names actually removed.

    Queries git for the authoritative list and UNIONs the hard-coded fallback,
    so a failing query degrades to "scrub the known-dangerous set" instead of
    "scrub nothing" -- the latter is the exact silent-no-op that let v1 rewrite
    `.git/config` while reporting success.
    """
    names = set(_GIT_ENV_FALLBACK)
    r = _git("rev-parse", "--local-env-vars")
    if r.returncode == 0:
        names.update(n.strip() for n in r.stdout.split() if n.strip())
    removed = []
    for name in sorted(names):
        if name in os.environ:
            del os.environ[name]
            removed.append(name)
    return removed


def _parse_push_refs():
    """Read the pre-push ref list from stdin. -> list | None

    git writes one `<local ref> <local sha> <remote ref> <remote sha>` line per
    ref; a DELETION carries an all-zero local sha. Returns [(local_ref,
    local_sha, remote_sha)] (remote_sha is "" if git omitted it) for the rows that actually add something -- an empty list means
    there is no code to verify (nothing queued, or deletions only).

    Reading this rather than discarding it is what lets the hook tell a push
    that ships code from one that only removes a remote branch, and lets it
    notice a refspec pushing something other than HEAD. Both are invisible if
    stdin is merely drained.

    Returns None (NOT []) if stdin cannot be read at all -- "I don't know what
    is being pushed" must never collapse into "nothing is being pushed", which
    would skip the suite and exit 0. The caller runs the suite in that case.
    """
    try:
        raw = sys.stdin.read()
    except Exception:
        return None
    refs = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        local_ref, local_sha = parts[0], parts[1]
        remote_sha = parts[3] if len(parts) >= 4 else ""
        if set(local_sha) == {"0"}:  # all-zero local sha == deletion
            continue
        refs.append((local_ref, local_sha, remote_sha))
    return refs


def _peel_commit(sha: str) -> str:
    """The commit `sha` points at (peeling an annotated tag), or `sha` itself
    if git can't resolve it -- an unresolvable sha then compares unequal to
    HEAD and warns, which is the safe direction for a warning."""
    try:
        return _git_lines("rev-parse", f"{sha}^{{commit}}")[0]
    except Exception:
        return sha


def _push_tree_mismatch(refs: list, ptc) -> list:
    """Reasons a PASSING suite would not actually describe the pushed commits.

    pytest runs on the working tree. When the tree is not what is going out, a
    green run is still green -- it just answers a different question. These are
    reported loudly rather than blocked: refusing every push that has unrelated
    work-in-progress would make --no-verify the habit, which removes the gate
    entirely instead of qualifying it.

    MUST be called before `_scrub_git_env()` -- it runs git queries that need
    the inherited environment.
    """
    reasons = []
    try:
        if ptc._tested_paths_dirty():
            reasons.append(
                "tested files are modified in your working tree but not committed"
            )
    except Exception as exc:  # noqa: BLE001 - report, don't mask
        reasons.append(f"could not check for uncommitted changes ({exc})")

    try:
        head = _git_lines("rev-parse", "HEAD")[0]
    except Exception:
        head = ""
    if head:
        # Tags are skipped: an annotated tag's local sha is the TAG OBJECT's,
        # and a tag push ships no new code, so warning on it would cry wolf.
        # Everything else is compared by the COMMIT it points at (audit C-M1,
        # 2026-10-12). This used to keep only `refs/heads/` local refs, but
        # for `git push origin HEAD~2:refs/heads/older` git reports the local
        # ref as the expression `HEAD~2` -- so the one push most likely to
        # ship something other than HEAD printed no warning at all.
        off_head = sorted({
            ref for ref, sha, *_rest in refs
            if not ref.startswith("refs/tags/") and _peel_commit(sha) != head
        })
        if off_head:
            reasons.append(
                "pushing " + ", ".join(off_head) + ", which is not the checked-out HEAD"
            )
    return reasons


def _stamp_path() -> str | None:
    """Absolute path of the tested-trees stamp file, or None if git can't say.

    Built from `--git-common-dir`, NOT `--git-path`: `--git-path` only maps a
    fixed list of names to the shared dir, so an unknown file name resolves into
    the per-WORKTREE git dir -- a linked worktree would then never see the
    stamps its sibling wrote. The common dir is shared by all of them. Resolve
    BEFORE any chdir (git prints it relative to the cwd). Never raises.
    """
    try:
        r = _git("rev-parse", "--git-common-dir")
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return os.path.join(os.path.abspath(r.stdout.strip()), _TESTED_TREES_NAME)
    except Exception:  # noqa: BLE001
        return None


def _read_stamps(path: str | None) -> set:
    if not path:
        return set()
    try:
        with open(path, encoding="utf-8") as fh:
            return {ln.strip() for ln in fh if ln.strip()}
    except FileNotFoundError:
        return set()


def _record_tested_tree(path: str | None, tree: str) -> None:
    """Append `tree` to the stamp file, keeping the last _TESTED_TREES_KEEP lines.
    Any failure prints one note and is swallowed -- it must never fail the gate
    (a missing stamp only costs a full suite run at push time)."""
    try:
        if not path:
            raise RuntimeError("git common dir unavailable")
        try:
            with open(path, encoding="utf-8") as fh:
                lines = [ln.strip() for ln in fh if ln.strip()]
        except FileNotFoundError:
            lines = []
        lines.append(tree)
        lines = lines[-_TESTED_TREES_KEEP:]
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[native git hook: pre-commit] note: could not record the tested "
            f"tree ({type(exc).__name__}: {exc}); pre-push will re-run the suite.",
            file=sys.stderr,
        )


def _push_already_covered(refs: list, ptc, remote: str = "") -> tuple[bool, str, tuple]:
    """-> (covered, reason, (stamped, docs_only)).

    True only if EVERY commit being pushed was already tested green at
    pre-commit (its tree is stamped: `stamped`) or is a single-parent commit
    touching no tested path and no suite-input doc (`_is_push_suite_input`) on a
    covered / already-pushed parent (`docs_only`). Any doubt -> False, i.e. run
    the suite. MUST be called before `_scrub_git_env()` (git queries).

    "Already pushed" == not among the new commits, which the rev-list
    `--not <remote sha>` / `--not --remotes[=<remote>]` construction guarantees
    (`remote` is git's first pre-push hook argument, the remote name -- the
    adapter's sys.argv[2]; empty -> every remote). Tag refs
    are processed like branches (rev-list peels the tag), so a branch+tag push
    with an untested tagged commit is NOT covered; a tag whose commit is already
    pushed simply contributes no new commits.
    """
    zero = (False, "", (0, 0))
    try:
        stamps = _read_stamps(_stamp_path())
        stamped_set: set = set()
        docs_set: set = set()
        remotes_arg = f"--remotes={remote}" if remote else "--remotes"
        for ref, local_sha, *rest in refs:
            remote_sha = rest[0] if rest else ""
            if remote_sha and set(remote_sha) != {"0"}:
                new = _git_lines("rev-list", "--reverse", "--topo-order",
                                 local_sha, "--not", remote_sha)
            else:
                new = _git_lines("rev-list", "--reverse", "--topo-order",
                                 local_sha, "--not", remotes_arg)
            if not new:
                if ref.startswith("refs/tags/"):
                    continue  # tag on an already-pushed commit: nothing new
                return False, f"{ref}: no new commits found (cannot verify)", (0, 0)
            if len(new) > _PUSH_COVER_MAX_COMMITS:
                return False, f"{ref}: {len(new)} new commits (> {_PUSH_COVER_MAX_COMMITS})", (0, 0)
            new_set = set(new)
            covered: set = set()
            for sha in new:  # oldest first
                tree = _git_lines("rev-parse", f"{sha}^{{tree}}")[0]
                if tree in stamps:
                    covered.add(sha)
                    stamped_set.add(sha)
                    continue
                parents = _git_lines("rev-list", "--parents", "-n", "1", sha)[0].split()[1:]
                if len(parents) != 1:
                    return False, f"{sha[:10]} is an unstamped merge/root commit", (0, 0)
                parent = parents[0]
                r = _git("diff", "--name-only", "--no-renames", "-z", parent, sha)
                if r.returncode != 0:
                    return False, f"`git diff` failed for {sha[:10]}", (0, 0)
                files = [f for f in r.stdout.split("\0") if f]
                if ptc._touches_tested_code(files):
                    return False, f"{sha[:10]} touches tested code and was not tested at pre-commit", (0, 0)
                if any(_is_push_suite_input(f) for f in files):
                    return False, f"{sha[:10]} touches a suite-input doc and was not tested at pre-commit", (0, 0)
                if parent in new_set and parent not in covered:
                    return False, f"{sha[:10]}'s parent {parent[:10]} is not covered", (0, 0)
                covered.add(sha)
                docs_set.add(sha)
        docs_set -= stamped_set
        if not stamped_set and not docs_set:
            return False, "no branch commits to verify", (0, 0)
        return True, "all new commits covered", (len(stamped_set), len(docs_set))
    except Exception as exc:  # noqa: BLE001 - fail toward running the suite
        return False, f"could not verify coverage ({type(exc).__name__}: {exc})", (0, 0)


def _write_tree() -> tuple[str | None, str]:
    """Snapshot the index being committed as a tree object. -> (tree, reason).

    MUST run BEFORE `_scrub_git_env()`, for the same reason `_changed_files()`
    must: with `git commit -a` or a pathspec commit, git stages into a TEMPORARY
    index and points the inherited `GIT_INDEX_FILE` at it. Scrubbing first would
    snapshot the on-disk index instead -- a tree that is not the commit.

    Never raises: a failure returns (None, <reason>), and the caller falls back
    to testing the working tree WITH A WARNING rather than blocking on plumbing.
    """
    try:
        # 120 s: under heavy load the default 30 s timed out (2026-10-19) and the
        # gates silently fell back to the working tree.
        r = _git("write-tree", timeout=120)
        if r.returncode != 0:
            return None, f"`git write-tree` failed: {r.stderr.strip()}"
        tree = r.stdout.strip()
        if not tree:
            return None, "`git write-tree` returned nothing"
        return tree, ""
    except Exception as exc:  # noqa: BLE001 - plumbing failure -> fallback
        return None, f"`git write-tree` raised {type(exc).__name__}: {exc}"


def _add_snapshot_worktree(tree: str) -> tuple[str, str]:
    """Check `tree` out into a throwaway detached worktree. -> (parent, worktree).

    MUST run AFTER `_scrub_git_env()`: with `GIT_INDEX_FILE` still inherited,
    `worktree add` could write through the in-progress commit's index.

    The commit object is dangling (no ref), so HEAD, branches and the main index
    are untouched. The worktree is `<parent>/wt` -- a not-yet-existing child of a
    fresh mkdtemp directory, since `worktree add` wants to create its target.
    RAISES on any failure; the caller treats that as "snapshot unavailable".
    """
    r = _git(
        "-c", "user.name=drishta-gate", "-c", "user.email=gate@localhost",
        "-c", "commit.gpgsign=false",
        "commit-tree", tree, "-m", "drishta pre-commit gate snapshot",
        timeout=120,  # see write-tree: 30 s was too tight under load
    )
    if r.returncode != 0:
        raise RuntimeError(f"`git commit-tree` failed: {r.stderr.strip()}")
    sha = r.stdout.strip()
    if not sha:
        raise RuntimeError("`git commit-tree` returned nothing")

    parent = tempfile.mkdtemp(prefix="drishta-gate-")
    wt = os.path.join(parent, "wt")
    try:
        r = _git("worktree", "add", "--detach", wt, sha, timeout=180)
        if r.returncode != 0:
            raise RuntimeError(f"`git worktree add` failed: {r.stderr.strip()}")
    except BaseException:
        _remove_snapshot(parent, wt)
        raise
    return parent, wt


def _remove_snapshot(parent: str | None, wt: str | None) -> None:
    """Remove OUR worktree and temp dir. NEVER raises, NEVER fails the gate.

    Deliberately no `git worktree prune`: this repo has other live worktrees
    (`.claude/worktrees/`) and cleanup must touch only the one we made. No
    junctions or symlinks anywhere -- Windows `rmtree` has historically followed
    them and could delete the real `.venv`. Must be called from OUTSIDE `wt`
    (a process cannot remove its own cwd on Windows).

    `--force` TWICE: a `worktree add` killed by its timeout mid-checkout leaves
    the registration LOCKED ("initializing"), which a single `--force` refuses
    (git: "use 'remove -f -f'"). The rmtree below would then delete the
    directory and strand a locked entry in `.git/worktrees/` that `prune` never
    clears. Safe to force that hard: `wt` is always our own mkdtemp child.
    """
    if wt and os.path.isdir(wt):
        try:
            r = _git("worktree", "remove", "--force", "--force", wt, timeout=120)
            if r.returncode != 0 and os.path.isdir(wt):
                print(
                    f"[native git hook: pre-commit] note: could not remove the "
                    f"snapshot worktree {wt}: {r.stderr.strip()}",
                    file=sys.stderr,
                )
        except Exception as exc:  # noqa: BLE001 - cleanup must not fail the gate
            print(
                f"[native git hook: pre-commit] note: snapshot worktree cleanup "
                f"raised {type(exc).__name__}: {exc} ({wt})",
                file=sys.stderr,
            )
    if parent:
        try:
            shutil.rmtree(parent, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


def _run_test_gates(mode: str) -> int:
    """The CODE gates: pytest and the antipattern scan.

    `pre-commit` narrows by staged path; `pre-push` always runs the full suite
    because it is the backstop for every commit the `pre-commit` gate never saw
    -- EXCEPT when `_push_already_covered` proves every pushed commit's exact tree
    already passed at pre-commit (stamp file), or is a docs-only commit (no tested
    path, no suite-input doc) on a covered / already-pushed parent, in which case
    it skips. pre-commit stamps a tree only when pytest was required, ran against
    the SNAPSHOT, and passed.
    """
    sys.path.insert(0, _HERE)
    import pre_tool_checks as ptc  # noqa: E402 - path set above

    mismatch: list = []
    tree: str | None = None
    tree_reason = ""
    stamp_file: str | None = None

    if mode == "pre-commit":
        # RESOLVED BEFORE THE SCRUB, deliberately: `git diff --cached` needs the
        # inherited GIT_INDEX_FILE to see a `-a` commit's TEMPORARY index.
        # Scrubbing first would read the on-disk index and miss every `-a`
        # change -- a silent under-gate, which is the failure mode this whole
        # file exists to remove.
        changed = _changed_files()
        need_pytest = ptc._touches_tested_code(changed)
        need_antipattern = ptc._touches_scanned_code(changed)
        if not (need_pytest or need_antipattern):
            print(
                f"[native git hook: pre-commit] {len(changed)} staged file(s), none "
                "under a tested or scanned path -- pytest and the antipattern "
                "check are both N/A here. Text gates run at commit-msg.",
                file=sys.stderr,
            )
            return 0
        scope = f"{len(changed)} staged file(s)"
        # ALSO BEFORE THE SCRUB, same reason: a `-a` commit's TEMPORARY index is
        # only reachable through the inherited GIT_INDEX_FILE. Failure -> None,
        # and the working-tree fallback below (loudly).
        tree, tree_reason = _write_tree()
        stamp_file = _stamp_path()  # absolute; resolved before any chdir
    else:
        refs = _parse_push_refs()
        if refs is None:
            # Unreadable stdin is NOT "nothing to push" -- fail toward running.
            print(
                "[native git hook: pre-push] Could not read the ref list from "
                "stdin; running the suite rather than assuming there is nothing "
                "to check.",
                file=sys.stderr,
            )
            refs = []
        elif not refs:
            print(
                "[native git hook: pre-push] Nothing being added (empty push, or "
                "deletions only) -- no code is going out, so the suite was not run.",
                file=sys.stderr,
            )
            return 0
        need_pytest, need_antipattern = True, False
        scope = "the working tree"
        # Resolved before the scrub -- these are git queries.
        # The shim passes git's own args after the mode: argv = [script,
        # "pre-push", <remote name>, <url>]. Guarded on argv[1] so a unit test
        # (argv = pytest's) never mistakes a pytest flag for a remote.
        remote = ""
        if sys.argv[1:2] == ["pre-push"] and len(sys.argv) > 2 and not sys.argv[2].startswith("-"):
            remote = sys.argv[2]
        covered, why, (n_stamped, n_docs) = _push_already_covered(refs, ptc, remote)
        if covered:
            docs_note = (
                f", {n_docs} touch no tested path or suite-input doc on top of a "
                "covered or already-pushed parent" if n_docs else ""
            )
            print(
                f"[native git hook: pre-push] {n_stamped} commit(s) passed the "
                f"full suite at pre-commit (exact tree){docs_note} -- skipping "
                "the local re-run. GitHub CI re-runs the suite only for pushes "
                "matching tests.yml's path filters, and it does not gate the "
                "deploy.",
                file=sys.stderr,
            )
            return 0
        mismatch = _push_tree_mismatch(refs, ptc)

    _scrub_git_env()

    # Test a SNAPSHOT of the commit, not the working tree (audit C-H1): files on
    # disk can differ from the index (a staged-then-fixed-on-disk file, or a
    # staged test importing an untracked module), so a green run against the
    # tree said nothing about what is actually committed. Built AFTER the scrub.
    orig_cwd = os.getcwd()
    parent = wt = None
    if mode == "pre-commit":
        reason = tree_reason
        if tree:
            try:
                parent, wt = _add_snapshot_worktree(tree)
                os.chdir(wt)
            except Exception as exc:  # noqa: BLE001 - never block on plumbing
                reason = f"{type(exc).__name__}: {exc}"
                try:
                    os.chdir(orig_cwd)
                except Exception:  # noqa: BLE001
                    pass
                _remove_snapshot(parent, wt)
                parent = wt = None
        if wt:
            scope += ", tested against a snapshot of the commit"
        else:
            print(
                "[native git hook: pre-commit] WARNING: could not build a "
                f"snapshot of this commit ({reason or 'unknown'}); testing the "
                "WORKING TREE instead, which may not match what you are "
                "committing.",
                file=sys.stderr,
            )
            scope += ", tested against the WORKING TREE -- snapshot unavailable"

    try:
        # `_start_process` passes no cwd, so the gate children inherit this one;
        # `_run_gates_concurrently` waits on both before returning.
        results = ptc._run_gates_concurrently(need_pytest, need_antipattern)
    finally:
        if wt:
            try:
                os.chdir(orig_cwd)
            finally:
                _remove_snapshot(parent, wt)

    failures: list[str] = []
    ran: list[str] = []

    if need_pytest:
        ok, detail = results.get("pytest", (None, "gate did not run"))
        if ok is True:
            ran.append("pytest PASSED")
        elif ok is False:
            failures.append("pytest FAILED:\n" + (detail or "(no detail captured)"))
        else:
            # Fails CLOSED (the 2026-08-27 decision): "could not run" and "ran
            # and failed" are the same zero-signal state.
            failures.append(
                "pytest COULD NOT RUN: " + (detail or "unknown") + "\n"
                "  Blocking deliberately -- a suite that did not run proves nothing."
            )

    if need_antipattern:
        ok, detail = results.get("antipattern", (None, "gate did not run"))
        if ok is True:
            ran.append("antipattern check CLEAN")
        elif ok is False:
            failures.append(
                "antipattern check FAILED:\n" + (detail or "(no detail captured)")
            )
        else:
            # Still fails OPEN, matching pre_tool_checks.py.
            ran.append(f"antipattern check SKIPPED ({detail or 'unknown'}) -- fails open")

    if failures:
        text = f"BLOCKED ({mode} gates, scope: {scope}):\n\n" + "\n\n".join(failures)
        print(text, file=sys.stderr)
        logged = _record_block(mode, text)
        print(
            f"\n[native git hook: {mode}] Bypass with --no-verify if certain."
            + (f" Full message saved to {logged}" if logged else ""),
            file=sys.stderr,
        )
        return 1

    if mode == "pre-commit" and need_pytest and wt and tree:
        _record_tested_tree(stamp_file, tree)

    print(
        f"[native git hook: {mode}] {'; '.join(ran)} (scope: {scope}). "
        + (
            "Text gates run separately at commit-msg."
            if mode == "pre-commit"
            else "Text gates are NOT re-checked at push time."
        ),
        file=sys.stderr,
    )
    if mismatch:
        print(
            "\n  ** MISMATCH WARNING -- this PASS may not describe what you are "
            "pushing **\n  "
            + "\n  ".join("- " + m for m in mismatch)
            + "\n  pytest ran against the working tree. Commit (or stash) first "
            "if you want\n  the result to describe the commits actually going "
            "out.",
            file=sys.stderr,
        )
    return 0


def _run_gates(argv: list[str]) -> int:
    # Shim is `... git_hook_adapter.py commit "$@"`, and git passes commit-msg
    # exactly one argument. So argv == [script, "commit", <message file>].
    # (v2 got this mapping wrong for a second hook and the bug survived a
    # hand-written unit test that skipped the shim entirely. Test THROUGH it.)
    msg_file = argv[2] if len(argv) > 2 else ""
    if not msg_file or not os.path.isfile(msg_file):
        text = (
            f"BLOCKED (workflow gates): commit message file not readable "
            f"({msg_file!r}); cannot verify the required trailers."
        )
        print(text, file=sys.stderr)
        _record_block("commit-msg", text)
        return 1

    sys.path.insert(0, _HERE)
    import pre_tool_checks as ptc  # noqa: E402 - path set above

    with open(msg_file, encoding="utf-8", errors="replace") as fh:
        message = _clean_message(fh.read())

    changed = _changed_files()
    failures: list[str] = []

    gated = ptc._gate_files_staged(changed)
    if gated and not ptc._has_review_citation(message):
        failures.append(
            "Hard Rule #4 -- decision-engine / DB-write file(s) staged with no "
            "Opus review citation:\n    " + "\n    ".join(gated) + "\n"
            "  Add to the commit body:\n"
            "    Review = Opus reviewer (<resolved model>): SHIP, 0 blocking; <notes>\n"
            "  on its own line, naming an Opus model; after a FIX-FIRST, send the\n"
            "  fix back for a confirming round rather than citing FIX-FIRST."
        )

    if ptc._is_feature_commit(message):
        missing = ptc._missing_provenance_trailers(message)
        if missing:
            failures.append(
                "Hard Rule #5 -- feature commit missing "
                + " and ".join(missing) + " trailer(s).\n"
                "  Design = planner (<model>): <verdict>  OR  Design = lead -- <why>\n"
                "  Build  = implementer (<model>)         OR  Build  = lead -- <why>"
            )

    if ptc._is_build_attested_commit(message) and ptc._missing_build_trailer(message):
        failures.append(
            "Hard Rule #5 -- fix(/refactor(/perf( commit missing Build trailer.\n"
            "  Build  = implementer (<model>)         OR  Build  = lead -- <why>"
        )

    if failures:
        text = "\n\n".join("BLOCKED (workflow gates): " + f for f in failures)
        print(text, file=sys.stderr)
        logged = _record_block("commit-msg", text)
        print(
            "\n[native git hook: commit-msg] This stage checks TEXT ONLY. The "
            "code gates are pre-commit's job -- which may legitimately have run "
            "nothing (a docs-only commit) or not run at all (a clean merge). "
            "Bypass with --no-verify."
            + (f" Full message saved to {logged}" if logged else ""),
            file=sys.stderr,
        )
        return 1

    # Say so on success too, and name which checks were APPLICABLE rather than
    # just "passed" -- a bare pass line reads as "a citation was verified" even
    # on a commit where no gate file was staged (including the amend gap, where
    # nothing was checked at all). Silence, or vagueness, is the inverse of the
    # stale "trust the hook" advice this whole effort exists to correct.
    cite = (
        f"citation: checked ({len(gated)} gate file(s))" if gated
        else "citation: N/A (no gate files staged)"
    )
    if ptc._is_feature_commit(message):
        trailers = "trailers: checked (Design + Build)"
    elif ptc._is_build_attested_commit(message):
        trailers = "trailers: checked (Build)"
    else:
        trailers = "trailers: N/A (not a feat(/fix(/refactor(/perf( commit)"
    print(
        f"[native git hook: commit-msg] Text gates passed -- {cite}; {trailers}. "
        "Code gates are pre-commit's job (N/A for some commits; not run at all "
        "for a clean merge). Text gates do NOT run for cherry-pick / revert / "
        "rebase / am, and cannot detect --amend.",
        file=sys.stderr,
    )
    return 0


def main() -> None:
    if "--install" in sys.argv:
        sys.exit(_install())

    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode in ("commit", "pre-commit", "pre-push"):
        try:
            if mode == "commit":
                sys.exit(_run_gates(sys.argv))
            sys.exit(_run_test_gates(mode))
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 - FAIL CLOSED, see docstring
            text = (
                f"BLOCKED (workflow gates): the gate adapter itself failed "
                f"({type(exc).__name__}: {exc}).\n"
                "This is NOT a pass. Fix the adapter, or use --no-verify if certain."
            )
            print(text, file=sys.stderr)
            # "commit" is the shim's argv name for the commit-msg hook; log
            # it under the hook's real name, as _run_gates does.
            _record_block("commit-msg" if mode == "commit" else mode, text)
            sys.exit(1)

    text = f"BLOCKED (workflow gates): unknown adapter invocation {sys.argv[1:]!r}."
    print(text, file=sys.stderr)
    _record_block(mode or "unknown", text)
    sys.exit(1)


if __name__ == "__main__":
    main()
