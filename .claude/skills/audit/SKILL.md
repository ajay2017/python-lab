---
name: audit
description: "Run an independent code audit covering correctness & bugs, security, reliability, performance, architecture, maintainability, UX & accessibility, and operational concerns. Invoke with /audit. Auto-invoked when asked to audit, inspect, or assess code health. DEFAULTS to an INCREMENTAL audit (only what changed since the last audit, plus blast radius); pass --full for a whole-codebase sweep, or a path to scope to that path."
allowed-tools: Bash(git *), Read, Glob, Grep, Task
argument-hint: "[--full | <file-or-path>]"
---

You are conducting an independent code review of this application. Treat the code as if you have never seen it before — do not assume the original author's intent was correct. Your job is to find problems, not to validate decisions.

---

## Step 0 — Pick the review mode (do this first)

Parse `$ARGUMENTS`:

- **Contains `--full`** → **FULL sweep**: review the entire codebase. Use for the quarterly / milestone deep pass that catches latent bugs in stable code and systemic drift — the things incremental review structurally cannot see.
- **A file or path (and no `--full`)** → **PATH-SCOPED**: review only that path. (Still apply the blast-radius step below.)
- **Empty (the default)** → **INCREMENTAL**: review only what changed since the last audit, plus its blast radius. This is the cheap, routine mode and should be the common case.

This tiering exists to control cost. Most days you want INCREMENTAL; reserve FULL for a calendar cadence. Per-change review is a separate, cheaper tool (`/code-review` on the working diff) — assume it already runs, so `/audit` does not need to re-do line-level diff review.

### Resolving the incremental base commit

For INCREMENTAL (and to scope PATH-SCOPED to recent change), find the commit the last audit covered:

1. Find the latest **code-audit** report: the newest file matching `docs/reviews/YYYY-MM-DD-review.md`, **excluding** `*-UX-review.md` (those are UX audits from a different skill).
2. Read its YAML frontmatter `audited_commit:` → that is the base.
3. **Legacy fallback** (older reports have no frontmatter): use `git log -1 --format=%H -- <that-report-path>` — the commit that introduced the report — as the base. Note in your report that the base was inferred.
4. **No prior report at all** → fall back to a FULL sweep for this run (and still record the anchor, per Step 3, so the next run is incremental).

Capture the current code state up front: run `git rev-parse HEAD` and remember it — that value is the `audited_commit` you will record in the report (the HEAD you reviewed, *before* the report commit advances it).

---

## Step 1 — Orient yourself

```
!`git log --oneline -8`
!`git status --short`
```

For INCREMENTAL / PATH-SCOPED, get the changed-file set:

```
!`git diff --stat <base>..HEAD`
```

Read `CLAUDE.md` at the project root first for project conventions, then map the part of the project in scope — entry points, modules, data flow, external dependencies. Read files in dependency order where possible (utilities → models → services → controllers → entrypoints).

### Blast-radius expansion (INCREMENTAL and PATH-SCOPED)

A diff is not the review surface — a small change can break distant code. Before reviewing, widen the changed-file set:

- For each changed source symbol (function / constant / session-state key), `Grep` for its **importers and consumers** and pull those into scope. The review surface is "what changed" **plus** "what depends on what changed."
- **A change to `stock_analyzer/constants.py` is ALWAYS a full fan-out of the gates** — a single threshold edit ripples to every feature that imports it, and in a "decides, not informs" app that is precisely where a wrong value does damage.
- Cross-feature coordination is a known blast vector here: if a producer that writes a `st.session_state` cache key changed, review every consumer that reads/gates on it (CLAUDE.md lists the keys).
- Docs-only / test-only changes don't need the code dimensions — but still flag doc↔code drift (a feature change that didn't sync `docs/requirements.md`).

If blast radius pulls in most of the codebase, say so and treat the run as effectively FULL.

---

## Step 2 — What to evaluate

**1. Correctness & bugs**
- Logic errors, off-by-one, incorrect conditionals, wrong operator precedence
- Race conditions, async/await misuse, unhandled promise rejections
- State mutation bugs, stale closures, incorrect dependency arrays
- Edge cases: empty inputs, null/undefined, zero, negative numbers, very large inputs, unicode, timezone boundaries
- Error paths: what happens when each external call fails, times out, or returns malformed data

**2. Security**
- Input validation and sanitization (injection, XSS, SSRF, path traversal)
- Authentication and authorization gaps — every protected route, every privileged action
- Secret handling: hardcoded keys, credentials in logs, secrets in client bundles
- CSRF, CORS misconfiguration, insecure cookie flags
- Dependency vulnerabilities (flag suspicious or outdated packages)
- PII handling, logging of sensitive data

**3. Reliability**
- Error handling: caught and swallowed vs caught and reported vs uncaught
- Retry logic: present where needed, and bounded
- Timeouts on every external call
- Idempotency for any operation that could be retried
- Graceful degradation when dependencies are unavailable
- Resource cleanup (connections, file handles, intervals, listeners)

**4. Performance**
- N+1 queries, unnecessary loops, redundant work
- Memory leaks, unbounded caches, retained references
- Bundle size, unused imports, code that ships to client unnecessarily
- Render performance: unnecessary re-renders, missing memoization where it actually matters (not cargo-cult memoization)
- Database query patterns, missing indexes implied by query shape

**5. Architecture & design**
- Separation of concerns; modules doing too much
- Coupling that will hurt later (circular deps, leaky abstractions)
- Inconsistent patterns across the codebase (three ways to do the same thing)
- Premature abstraction vs missing abstraction
- Data model choices that constrain future changes

**6. Maintainability**
- Naming clarity, function length, cyclomatic complexity hotspots
- Dead code, commented-out code, TODO/FIXME debt
- Test coverage: what's tested, what's not, what's tested badly (tests that always pass, tests that test the mock)
- Type safety gaps (`any`, unknown casts, `@ts-ignore`, missing types on API boundaries)
- Documentation where it would actually help (non-obvious decisions, not what the code already says)

**7. UX & accessibility**
- Loading states, error states, empty states
- Keyboard navigation, focus management, ARIA where needed
- Color contrast, text scaling
- Mobile/responsive behavior
- Form validation feedback timing and clarity

**8. Operational concerns**
- Logging: enough to debug production issues, not so much it's noise or leaks data
- Observability: metrics, traces, health checks
- Configuration: env vars vs hardcoded, documented vs not
- Deployment assumptions baked into code

> Note for this project: it is a single-user Streamlit + Python app, so dimension 4 (bundle size / re-renders / client code) and parts of 7 (ARIA / mobile) mostly do not apply — de-weight them and lean into correctness (1), reliability (3), architecture (5), operational (8). The crown jewels are the decision logic: `stock_analyzer/constants.py`, the scoring/gate path, the advisors, and the headless cron runtime.

---

## Step 2.5 — Prior-findings regression check (INCREMENTAL only)

Open the previous code-audit report. For each of its **Critical / High** findings (and any notable Medium):

- Verify it is still fixed (not reverted) in the current code.
- **Check the fix's *class* didn't resurface on a sibling path.** A real example: the 2026-05-27 audit fixed an `unsafe_allow_html` XSS on news headlines; the same class survived on the `notes`/`thesis` render sites and was re-found on 2026-06-28. Fixing the instance is not fixing the class.

Report any regression or partial-fix-class-survival in the "Prior-findings status" section.

---

## Step 3 — Reporting format

Produce a single markdown report and **write it to `docs/reviews/<YYYY-MM-DD>-review.md`** using today's date in America/New_York timezone.

Begin the file with a YAML frontmatter anchor block (this is what makes the next incremental run deterministic — do not omit it):

```
---
audit_date: <YYYY-MM-DD ET>
mode: incremental | full | path
base_commit: <sha the last audit covered, or "none (full sweep)">
audited_commit: <git rev-parse HEAD captured in Step 0>
scope: <e.g. "9 files changed since b8ac38d + blast radius (14 files reviewed)" | "full codebase">
---
```

Then the report sections, in this order:

### 1. Executive summary
5–10 lines. Overall health, top 3 risks, recommended next action. State the mode and what was (and was not) in scope.

### 2. Prior-findings status (INCREMENTAL only; omit for full/path)
One line per prior Critical/High: fixed-and-holding / regressed / class-resurfaced-elsewhere (with `file:line`).

### 3. Critical issues
Bugs or vulnerabilities that need fixing before this ships or stays in production.
For each: `file:line` — what's wrong — why it matters — suggested fix (minimal change, not a rewrite).

### 4. High-priority issues
Things that will cause incidents or pain within weeks. Same format.

### 5. Medium-priority issues
Quality and maintainability problems worth scheduling.

### 6. Low-priority / nits
Style, minor cleanup. Keep this section brief.

### 7. Patterns worth keeping
Call out what's done well so it gets preserved during refactors.

### 8. Systemic observations
Patterns that appear repeatedly (e.g. "a guard applied to the primary path but not its sibling"). More valuable than individual line findings because they point to root causes.

### 9. Open questions
Things that couldn't be determined from the code alone and need the author to clarify.

After writing the file, commit and push it:

```
git add docs/reviews/<YYYY-MM-DD>-review.md
git commit -F .git/COMMIT_MSG.txt   # write message to that file first (see below)
git push
```

Commit message format (write to `.git/COMMIT_MSG.txt` first — per CLAUDE.md, this dodges PowerShell here-string mangling):
```
docs(review): <YYYY-MM-DD> code audit (<mode>) — <N> critical, <N> high, <N> medium findings

Full findings in docs/reviews/<YYYY-MM-DD>-review.md.

Co-Authored-By: Ajay with Claude Opus 4.8 <ajay.x.ku@accenture.com>
```

---

## Step 4 — Rules to follow throughout

- Cite specific file paths and line numbers for every finding. "Somewhere in the auth module" is not acceptable.
- Distinguish facts from opinions. "This will throw on empty input" is a fact. "This would be cleaner as a hook" is an opinion — say so.
- If uncertain whether something is a bug, say so and explain what you'd need to verify it.
- Do not invent issues to fill space. A short, accurate report is more useful than a long padded one.
- Do not rewrite the entire codebase. Suggest fixes only for issues you flag.
- When you suggest a fix, show the minimal change, not a full rewrite of the surrounding code.
- Flag any place where you can't tell what the code is supposed to do — that itself is a finding.
- **Transcribe every threshold / constant / `file:line` from the source, not from memory** — this is a decision-making app and a wrong value in a review erodes trust. This applies to the anchor SHA you record too.

### Cost discipline (scale effort to the surface)

- **Small incremental surface (a handful of files) → do it inline in one pass. Do NOT fan out subagents** — the parallel multi-reviewer pattern is for FULL sweeps and large blast radii, where its token cost is justified.
- **FULL sweep or large blast radius → delegate breadth to subagents** (one per area: e.g. security, cron/reliability, data layer, the large orchestration file), then synthesize. Each returns findings with `file:line`; you triage and write the single report.
- **Model routing** (per the project's tiered convention): keep verification of anything touching `constants.py`, gates, scoring, cross-feature coordination, or the Daily Brief on the Opus lead — never delegate a policy-value check. Breadth/mechanical sub-reviews may go to Sonnet-tier subagents; the prose write-up may go to a Haiku doc-writer only after the facts are pinned.
- A scoring/gate-affecting finding must be re-verified against HEAD by the lead before it goes in the report.
