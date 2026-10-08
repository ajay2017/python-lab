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
  * Hard Rule #5 -- `Design =` / `Build =` trailers on a `feat(` commit

`pre-commit` -- CODE gates, when the staged set touches the relevant paths:
  * the full pytest suite      (`_touches_tested_code`)
  * scripts/check_antipatterns.py  (`_touches_scanned_code`)

`pre-push` -- a second full pytest run, for any push that is not purely
deletions. It is the only gate that fires at all for a commit `pre-commit` never
saw (made with --no-verify, by cherry-pick/revert/rebase/`am`, or before these
hooks were installed).

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
import subprocess
import sys

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


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )


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
    print("commit-msg : review citation + provenance trailers (ordinary commit)")
    print("pre-commit : pytest + antipattern check, when staged paths need them")
    print("pre-push   : full pytest suite, for any push that adds commits")
    print()
    print("NOT gated: cherry-pick / revert / rebase / am and a clean merge skip")
    print("BOTH commit-msg and pre-commit -- only pre-push sees them. `--amend`")
    print("runs pre-commit, but only over newly-staged changes, and its text")
    print("gates are indistinguishable from an ordinary commit's.")
    print()
    print("pre-push runs pytest against the WORKING TREE, not the pushed")
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
    local_sha)] for the rows that actually add something -- an empty list means
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
        if set(local_sha) == {"0"}:  # all-zero local sha == deletion
            continue
        refs.append((local_ref, local_sha))
    return refs


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
        # Branches only. An annotated tag's local sha is the TAG OBJECT's, never
        # a commit sha, so comparing it to HEAD warns on every tag push -- a
        # false alarm, and a warning that cries wolf stops being read.
        off_head = sorted({
            ref for ref, sha in refs
            if ref.startswith("refs/heads/") and sha != head
        })
        if off_head:
            reasons.append(
                "pushing " + ", ".join(off_head) + ", which is not the checked-out HEAD"
            )
    return reasons


def _run_test_gates(mode: str) -> int:
    """The CODE gates: pytest and the antipattern scan.

    `pre-commit` narrows by staged path; `pre-push` always runs the full suite
    because it is the backstop for every commit the `pre-commit` gate never saw.
    """
    sys.path.insert(0, _HERE)
    import pre_tool_checks as ptc  # noqa: E402 - path set above

    mismatch: list = []

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
        mismatch = _push_tree_mismatch(refs, ptc)

    _scrub_git_env()
    results = ptc._run_gates_concurrently(need_pytest, need_antipattern)

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
        print(
            f"BLOCKED ({mode} gates, scope: {scope}):\n\n"
            + "\n\n".join(failures),
            file=sys.stderr,
        )
        print(
            f"\n[native git hook: {mode}] Bypass with --no-verify if certain.",
            file=sys.stderr,
        )
        return 1

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
        print(
            f"BLOCKED (workflow gates): commit message file not readable "
            f"({msg_file!r}); cannot verify the required trailers.",
            file=sys.stderr,
        )
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
            "    Review = Opus reviewer (<resolved model>): SHIP|FIX-FIRST, "
            "N blocking; <notes>"
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

    if failures:
        print(
            "\n\n".join("BLOCKED (workflow gates): " + f for f in failures),
            file=sys.stderr,
        )
        print(
            "\n[native git hook: commit-msg] This stage checks TEXT ONLY. The "
            "code gates are pre-commit's job -- which may legitimately have run "
            "nothing (a docs-only commit) or not run at all (a clean merge). "
            "Bypass with --no-verify.",
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
    trailers = (
        "trailers: checked" if ptc._is_feature_commit(message)
        else "trailers: N/A (not a feat( commit)"
    )
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
            print(
                f"BLOCKED (workflow gates): the gate adapter itself failed "
                f"({type(exc).__name__}: {exc}).\n"
                "This is NOT a pass. Fix the adapter, or use --no-verify if certain.",
                file=sys.stderr,
            )
            sys.exit(1)

    print(
        f"BLOCKED (workflow gates): unknown adapter invocation {sys.argv[1:]!r}.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
