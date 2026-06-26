---
name: audit
description: "Run a comprehensive independent code audit covering correctness & bugs, security, reliability, performance, architecture, maintainability, UX & accessibility, and operational concerns. Invoke with /audit. Auto-invoked when asked to audit, inspect, or assess code health. Reviews the full codebase unless a specific path is passed as an argument."
allowed-tools: Bash(git *), Read, Glob, Grep
argument-hint: "[file-or-path]"
---

You are conducting an independent code review of this application. Treat the code as if you have never seen it before — do not assume the original author's intent was correct. Your job is to find problems, not to validate decisions.

## Step 1 — Orient yourself first

Before reading any file, run these to understand what you're working with:

```
!`git log --oneline -5`
!`git diff --stat HEAD`
!`git status --short`
```

Then map the project structure — entry points, modules, data flow, external dependencies. Read files in dependency order where possible (utilities → models → services → controllers → entrypoints).

If a `CLAUDE.md` exists at the project root, read it first for project conventions.

If `$ARGUMENTS` is provided, scope the review to that file or path only. Otherwise review the entire codebase.

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

---

## Step 3 — Reporting format

Produce a single markdown report with these sections in this order:

### 1. Executive summary
5–10 lines. Overall health, top 3 risks, recommended next action.

### 2. Critical issues
Bugs or vulnerabilities that need fixing before this ships or stays in production.
For each: `file:line` — what's wrong — why it matters — suggested fix (minimal change, not a rewrite).

### 3. High-priority issues
Things that will cause incidents or pain within weeks. Same format as above.

### 4. Medium-priority issues
Quality and maintainability problems worth scheduling.

### 5. Low-priority / nits
Style, minor cleanup. Keep this section brief.

### 6. Patterns worth keeping
Call out what's done well so it gets preserved during refactors.

### 7. Systemic observations
Patterns that appear repeatedly (e.g. "error handling is inconsistent across all API routes"). More valuable than individual line findings because they point to root causes.

### 8. Open questions
Things that couldn't be determined from the code alone and need the author to clarify.

---

## Step 4 — Rules to follow throughout

- Cite specific file paths and line numbers for every finding. "Somewhere in the auth module" is not acceptable.
- Distinguish facts from opinions. "This will throw on empty input" is a fact. "This would be cleaner as a hook" is an opinion — say so.
- If uncertain whether something is a bug, say so and explain what you'd need to verify it.
- Do not invent issues to fill space. A short, accurate report is more useful than a long padded one.
- Do not rewrite the entire codebase. Suggest fixes only for issues you flag.
- When you suggest a fix, show the minimal change, not a full rewrite of the surrounding code.
- Flag any place where you can't tell what the code is supposed to do — that itself is a finding.
