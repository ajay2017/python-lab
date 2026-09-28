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
  - A `git push` immediately following a commit that already passed both
    gates, with no further edits, no longer re-runs them. `git write-tree`'s
    SHA is recorded after a successful *plain* commit (no `-a`/`--amend` --
    write-tree reflects the INDEX only, which isn't provably what those two
    forms actually commit) and compared against `HEAD^{tree}` at push time.
    A match means byte-identical tree content, which for this deterministic,
    network-free suite means an identical verdict -- re-running proves
    nothing new. Any mismatch (further edits, an -a/--amend commit, a commit
    from outside this hook) falls back to the original always-verify
    behaviour, unchanged.
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

    is_commit = bool(re.search(r"\bgit\s+commit\b", command))
    is_push = bool(re.search(r"\bgit\s+push\b", command))

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
        if need_pytest or need_antipattern:
            results = _run_gates_concurrently(need_pytest, need_antipattern)
            _apply_gate_results(results, "commit", "committing")

        # Everything this commit needed (if anything) just passed. Record the
        # tree so an immediately-following `git push` with no further edits
        # doesn't pay for the identical pytest+antipattern run a second time
        # -- only for a PLAIN commit (write-tree reflects the index only, which
        # `-a`/`--amend` don't provably match). See module docstring.
        if _is_plain_commit(tokens):
            tree = _write_tree()
            if tree:
                _write_verified_tree(tree)

    # Always re-check before push, regardless of which files are in the
    # commits being pushed -- push sends whatever HEAD currently is, so one
    # suite run against the working tree covers it. Catches the case where a
    # commit landed before this gate existed, or from another session/tool.
    # EXCEPT: if HEAD's tree is byte-identical to the tree a commit-time run
    # already verified in full, running again proves nothing new (see module
    # docstring) -- skip and say so.
    if is_push:
        head_tree = _head_tree()
        verified_tree = _read_verified_tree()
        if head_tree and verified_tree and head_tree == verified_tree:
            print(
                "INFO (workflow gates): HEAD's tree already passed the pytest+antipattern "
                "gates at commit time and nothing has changed since -- skipping a second, "
                "identical full run. Any further edit, an -a/--amend commit, or a commit "
                "from outside this hook changes the tree and forces a full re-run.",
                file=sys.stderr,
            )
        else:
            results = _run_gates_concurrently(True, True)
            _apply_gate_results(results, "push", "pushing")
            if head_tree:
                _write_verified_tree(head_tree)

    sys.exit(0)


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
        return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
    risking a false "already verified" match."""
    return not _has_flag(tokens, "a", "--all") and "--amend" not in tokens


def _write_tree() -> str | None:
    """The SHA `git commit` would give the resulting tree for a PLAIN commit
    of the current index. None on any git error -- callers must treat that as
    "don't record a verification," never as an empty-but-valid tree."""
    try:
        r = subprocess.run(["git", "write-tree"], capture_output=True, text=True, timeout=10)
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


def _git_names(*args: str) -> list[str]:
    try:
        r = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=5,
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
    files, since the resulting commit carries them.
    """
    staged = _git_names("diff", "--cached", "--name-only")
    tokens = _tokens(command)
    if _has_flag(tokens, "a", "--all"):
        staged += _git_names("diff", "--name-only")
    if "--amend" in tokens:
        staged += _git_names("show", "--pretty=", "--name-only", "HEAD")
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
    """
    return any(
        f == "app.py" or f == "cron_runner.py"
        or f.startswith("stock_analyzer/") or f.startswith("tests/")
        for f in staged
    )


def _touches_scanned_code(staged: list[str]) -> bool:
    """Files the anti-pattern gate scans (mirrors TARGETS in check_antipatterns.py)."""
    return any(
        f == "app.py" or f == "cron_runner.py" or f.startswith("stock_analyzer/")
        for f in staged
    )


_SHELL_SEPARATORS = ("&&", "||", ";", "|", "&")


def _tokens(command: str) -> list:
    """Tokens of the `git commit` SEGMENT only, not the whole compound command.

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

    start = 0
    for i, t in enumerate(toks):
        if t == "git" and i + 1 < len(toks) and toks[i + 1] == "commit":
            start = i
    toks = toks[start:]

    for i, t in enumerate(toks):
        if t in _SHELL_SEPARATORS and i > 0:
            return toks[:i]
    return toks


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
