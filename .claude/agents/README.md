# Agent roster — how we split work to optimize cost & quality

DRISHTA is a **correctness-bound** project: it issues actionable buy/sell calls,
so a wrong recommendation or a silently-broken gate costs far more than model
tokens. The savings come from **delegating the easy parts down** to cheaper models
while the lead orchestrates and the Opus reviewer guards the decision logic.

> **Lead model (2026-07-22):** formally set to **Sonnet 5** (main session).
> The `reviewer` remains pinned to **Opus** — that gate is non-negotiable
> regardless of what the lead is. See `docs/cost-routing.md` for the decision
> rationale and updated economics.

> **Deterministic-gates-first (2026-08-04 cost/quality pass).** The free,
> always-on gates — the `pre_tool_checks.py` commit/push hook (full pytest +
> `check_antipatterns.py`) and the suite's own `tests/test_repo_hygiene.py`
> (py_compile of the entrypoints + constants-doc) — are the real pre-deploy
> safety net. The **paid agents below are for judgment, not for re-checking what
> a hook already guarantees.** `test-runner` is now optional (gap-only) and
> `reviewer` is gated on change type. **CLAUDE.md "Review & test economy" is the
> source of truth** for when to spend an agent; this file describes the roster.

## The model tiers

| Tier | Model | Does the work that is… |
|------|-------|------------------------|
| **Lead** | Sonnet 5 (main session) | Orchestration: design, threshold/gate/coordination decisions, subtle debugging, planning, final review. Capable enough for this role; Opus stays as the mandatory review gate before anything that touches decision logic. |
| `planner` | **opus** | DESIGN pass for money-moving work *before code exists*: gate/threshold/scoring-formula changes, cross-feature coordination, a new decision surface, multi-phase features. Read-only; returns a plan + design verdict with the threshold/coordination decisions called out. The `opus` pin means policy design gets Opus scrutiny **regardless of the session model** — the design-side counterpart to `reviewer`. |
| `Plan` | **plan** (built-in) | Read-only architectural scaffolding with **no policy content**: structural layout of a new page, DB table design, session-state wiring. Returns a spec; the lead decides on any policy content inside it. **Inherits the session model (no pin)** — so use it only for structure separable from gate/threshold policy; policy design goes to `planner` above. |
| `reviewer` | **opus** | A focused review pass on changes touching decision logic / constants — read-only, returns SHIP / FIX-FIRST. This is a correctness premium (~67% cost uplift over the Sonnet 5 lead) that is always worth paying before committing anything that moves money. |
| `implementer` | **sonnet** | A scoped, already-decided edit: wire a constant, add a render block, mechanical refactor, clear-repro fix. Same tier as lead — value is scope isolation and context hygiene, not dollar savings. |
| `test-runner` | **haiku** | Verification checklist (`py_compile` → targeted pytest → `check_constants_documented.py` → full suite), report-only. **Optional/gap-only** as of 2026-08-04 — the pytest hook + `tests/test_repo_hygiene.py` already cover this deterministically for free; invoke only when the hook can't be relied on, or as a cheap pre-filter before an expensive review on a big change. |
| `doc-writer` | **haiku** | Cheap mechanical write-ups: a constants-table row, a Known-Behaviours row, an F/gate row, a code comment. Strong-saving lane (~67% vs Sonnet 5 lead at list price). |

Model is set per agent via the `model:` frontmatter (`opus` / `sonnet` /
`haiku`). The lead can also override it per-invocation when needed.

## The workflow: PLAN → ROUTE → BUILD → [VERIFY] → REVIEW → COMMIT

*(VERIFY is bracketed — it's now conditional, not a mandatory stage; see step 4.)*

1. **PLAN.** Decide *whether* to do it and *exactly how* — especially any
   constant/threshold/coordination call. **If the design carries policy risk
   (a gate/threshold/scoring-formula change, cross-feature coordination, a new
   decision surface), route it to the `planner` agent (opus) so the design gets
   Opus scrutiny regardless of the session model** — it returns a spec + a
   design verdict. For structural scaffolding with no policy content (page
   layout, table schema), use the built-in `Plan` instead and fold the result
   in. Output: a precise spec per chunk.
2. **ROUTE.** For each chunk, the lead picks the right agent:
   - ambiguous / decision-bearing / cross-feature → **keep it on the lead**
   - structural scaffolding (no gate/threshold policy) → **`Plan`** (read-only, returns spec)
   - scoped, decided edit → delegate to **`implementer`** (sonnet — context hygiene)
   - doc/comment write-up → delegate to **`doc-writer`** (haiku — strong-saving lane)
   - broad code search ("find every place that gates on sector") → **`Explore`**
     (built-in, fast read-only fan-out)
3. **BUILD.** Workers make the edit and `py_compile`-check. They do **not**
   commit, do **not** invent thresholds, and do **not** self-certify with
   pytest — if a worker hits a decision it isn't authorized to make, it
   reports back instead of guessing.
4. **VERIFY — deterministic, automatic (conditional agent).** The real
   pre-push gate is the `pre_tool_checks.py` hook: it runs the full suite (which
   now includes `tests/test_repo_hygiene.py`'s `py_compile` of `app.py`/
   `cron_runner.py` + the constants-doc check) and `check_antipatterns.py`, and
   blocks the commit/push on failure — deterministic, free, every time. This
   exists because Streamlit Cloud auto-redeploys from `main` regardless of CI,
   so the *local hook* — not CI — is the safety gate. Only invoke the Haiku
   `test-runner` agent in the gap cases (a session that started before the hook
   loaded; work done outside Claude Code's tools; a cheap pre-filter before an
   expensive review on a large change); otherwise the hook already covers it.
5. **REVIEW (Opus `reviewer`) — for decision/data-affecting changes.** Before
   committing anything that touches constants/gates/scoring, cross-feature
   coordination, DB-write/data-integrity, or a new user-facing decision surface,
   run the `reviewer`; it traces the data path against the hard rules and the
   calm-advisor posture and returns SHIP / FIX-FIRST. Mandatory for that class
   (Rule #4) regardless of the lead model. **Skip it** for docs/tests/comments/
   mechanical/pure-additive-not-yet-wired changes when the gates are green —
   don't pay Opus to review what can't move a recommendation. If a `test-runner`
   report was produced, hand it over; if not, the reviewer proceeds on the green
   deterministic gates (it never re-runs the suite itself).
6. **COMMIT (lead).** The lead commits/pushes once the review passes. Commit
   authority stays with the lead so the Opus review gate is never skipped on
   decision logic.

## Parallelism (a team of agents) — where it helps here

- ✅ **Investigation fan-out** — multiple `Explore` agents reading different
  parts of the codebase at once.
- ✅ **Genuinely independent workstreams** — unrelated fixes can run concurrently
  in isolated git worktrees (`isolation: worktree`).
- ⚠️ **Keep interdependent advisor-logic changes sequential.** Much of DRISHTA's
  work is coupled (one fix exposes the next), and there's a single deploy target
  (push → Streamlit Cloud), so parallel commits to `main` need careful
  sequencing. Don't parallelize work that touches the same decision path.

## Things to know about subagents (so the routing behaves)

- **They start fresh** — a subagent does NOT see this conversation's history.
  The lead must hand it full context in the task prompt (files, intent, the rule
  it must follow).
- **They DO load CLAUDE.md** — so the hard rules (no hardcoded thresholds, never
  disable RLS, never run locally, `_pending_page` nav) apply to them too. The
  agent prompts restate the load-bearing ones anyway.
- **They cannot spawn other subagents** — no nesting. The lead chains them.
- **Invoke them** by name in a request ("have the implementer wire this up"),
  by `@agent-<name>`, or the lead dispatches them via the Task/Agent tool with a
  per-call model override if desired.

## How to invoke

- Natural language: *"Route this to the implementer, then have the reviewer
  check it before we commit."*
- Explicit: `@agent-implementer` / `@agent-test-runner` / `@agent-reviewer` / `@agent-doc-writer`.
- Defaults & precedence: project agents in `.claude/agents/` (version-controlled,
  shared) override `~/.claude/agents/`. Filename is cosmetic; identity is the
  `name:` field.

## Tracking the savings

Routing decisions and the savings on delegated work are logged in
[`docs/cost-routing.md`](../../docs/cost-routing.md) — a running ledger appended
at commit time (one row per delegated task, plus decisions *not* to delegate).
It measures the delegated slice only, not total cost; the authoritative total is
the Anthropic Console / subscription usage view.

## TL;DR

Sonnet 5 leads and orchestrates; the **free deterministic gates** (pytest hook +
antipattern + repo-hygiene checks) verify every change automatically; Opus
`reviewer` reviews **decision/data-affecting** changes before they ship (skipped
for docs/tests/mechanical when the gates are green); Haiku `test-runner` is kept
for gap cases only; Haiku `doc-writer` writes up the docs. The Plan agent handles
structural scaffolding so the lead's context stays clean. The calls that move
money always pass through the Opus gate — regardless of what model runs the
session — but nothing else pays for an agent it doesn't need.
