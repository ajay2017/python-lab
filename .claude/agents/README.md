# Agent roster — how we split work to optimize cost & quality

DRISHTA is a **correctness-bound** project: it issues actionable buy/sell calls,
so a wrong recommendation or a silently-broken gate costs far more than model
tokens. The savings come from **delegating the easy parts down** to cheaper models
while the lead orchestrates and the Opus reviewer guards the decision logic.

> **Model pins (2026-10-07, owner decision):** `planner` + `reviewer` =
> **`claude-opus-5-5`**, `implementer` + `doc-writer` = **`claude-sonnet-5`**,
> `test-runner` = **`haiku`** (Haiku 4.5). `doc-writer` moved off Haiku on
> 2026-10-07 — the saving was 50%, not the ~67-80% the old ladder claimed, and
> did not cover this lane's required re-verification; see that agent's frontmatter
> comment. **All pins verified by a fresh-session `MODEL:` probe, 2026-10-07**
> (`reviewer` → Opus 5.5, `implementer` → Sonnet 5).
>
> **The LEAD is not pinned, and that is now the main exposure.** It is whatever
> the session runs — Opus 5 observed 2026-10-07, but the **org default is
> `claude-sonnet-4-6`**, and `claude-opus-5-5` is absent from the org
> `availableModels` list so it cannot be chosen from the `/model` picker at all.
> The pins protect design and review; every "done inline as lead" judgment call
> is unprotected. Fix = an allowlist request to Accenture. Until then
> **`claude-opus-5` is the best selectable lead** — allowlisted, and what a
> manually-set session already runs. **Not `opusplan`:** its Opus leg most
> likely resolves via `ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-4-8` (older than
> `claude-opus-5`) and it drops to Sonnet for execution, where most inline lead
> judgment happens. Unverified — a reason for caution, not a measurement.
> (Note `availableModels` does *not* gate
> agent frontmatter — the `claude-opus-5-5` pin resolves despite being absent
> from it. It gates the picker only.)
>
> The pins are exact IDs because the `opus` alias inherited the lead's
> model, or fell to the org's `ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-4-8` under
> a Sonnet lead, which made the review version depend on the session. With the
> pins, the review gate no longer depends on the lead. See `docs/cost-routing.md`
> for economics.

> **Workflow enforcement (2026-08-15).** The plan → build → review split is no
> longer convention-only. `pre_tool_checks.py` now blocks a commit that (a) stages
> a decision-engine-core **or DB-write** file without a properly-formatted Opus
> review citation (resolved model + verdict + blocking count), or (b) is a `feat(`
> commit missing its `Design =` / `Build =` provenance trailers. `lead` is an
> accepted answer on both trailers — the requirement is a deliberate, permanently
> recorded statement of who designed and who built, so the split is auditable in
> git history rather than only in a session transcript. Separately — a
> *convention*, not a hook check — the `reviewer` is now invoked
> **automatically** when a change is review-required, rather than on request
> (CLAUDE.md Hard Rule #4).
> **Honesty caveat:** the hook proves a citation is *present and well-formed*; it
> cannot prove a subagent ran. The real protection is that the reviewer is a
> separate instance with its own context, re-deriving from the diff.

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
| **Lead** | session model — **unpinned**; Opus 5 observed 2026-10-07, org default is `claude-sonnet-4-6` | Orchestration: design, threshold/gate/coordination decisions, subtle debugging, planning, final review. Capable enough for this role; Opus stays as the mandatory review gate before anything that touches decision logic. |
| `planner` | **claude-opus-5-5** | DESIGN pass for money-moving work *before code exists*: gate/threshold/scoring-formula changes, cross-feature coordination, a new decision surface, multi-phase features. Read-only; returns a plan + design verdict with the threshold/coordination decisions called out. The `opus` pin means policy design gets Opus scrutiny **regardless of the session model** — the design-side counterpart to `reviewer`. |
| `Plan` | **plan** (built-in) | Read-only architectural scaffolding with **no policy content**: structural layout of a new page, DB table design, session-state wiring. Returns a spec; the lead decides on any policy content inside it. **Inherits the session model (no pin)** — so use it only for structure separable from gate/threshold policy; policy design goes to `planner` above. |
| `reviewer` | **claude-opus-5-5** | A focused review pass on changes touching decision logic / constants — read-only, returns SHIP / FIX-FIRST. **Corrected 2026-10-07: this is no longer a cost premium at all.** Opus 5.5 is $4/$20 — cheaper than Opus 5/4.8/4.7 ($5/$25) and only 1.33× a `claude-sonnet-4-6` lead. Always worth paying before committing anything that moves money. |
| `implementer` | **claude-sonnet-5** | A scoped, already-decided edit: wire a constant, add a render block, mechanical refactor, clear-repro fix. Same tier as lead — value is scope isolation and context hygiene, not dollar savings. |
| `test-runner` | **haiku** | Verification checklist (`py_compile` → targeted pytest → `check_constants_documented.py` → full suite), report-only. **Optional/gap-only** as of 2026-08-04 — the pytest hook + `tests/test_repo_hygiene.py` already cover this deterministically for free; invoke only when the hook can't be relied on, or as a cheap pre-filter before an expensive review on a big change. |
| `doc-writer` | **claude-sonnet-5** | Cheap mechanical write-ups: a constants-table row, a Known-Behaviours row, an F/gate row, a code comment. **Moved off `haiku` 2026-10-07** — the real saving was 50% (~$0.05/row), not the ~67% claimed against an Opus-at-$5/$25 lead, and it never covered this lane's mandatory re-verification. Policy-bearing doc edits still stay on the Opus lead. |

Model is set per agent via the `model:` frontmatter: an exact ID for the Opus
and Sonnet lanes, and the `haiku` alias for `test-runner` (the only Haiku lane
left since `doc-writer` moved to Sonnet 5 on 2026-10-07). Resolution order,
first match wins (sub-agents docs): per-call `model` → frontmatter →
`CLAUDE_CODE_SUBAGENT_MODEL` → the lead's model. So a per-call `model: "opus"`
would OVERRIDE the exact pin with the alias; don't pass one to `reviewer`/`planner`.

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
   - doc/comment write-up → delegate to **`doc-writer`** (sonnet 5 — 50% vs the Opus gate; policy-bearing doc edits still stay on the lead)
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

The lead orchestrates; the **free deterministic gates** (pytest hook +
antipattern + repo-hygiene checks) verify every change automatically; Opus
`reviewer` reviews **decision/data-affecting** changes before they ship (skipped
for docs/tests/mechanical when the gates are green); Haiku `test-runner` is kept
for gap cases only; Sonnet `doc-writer` writes up the docs. The Plan agent handles
structural scaffolding so the lead's context stays clean. The calls that move
money always pass through the Opus gate — regardless of what model runs the
session — but nothing else pays for an agent it doesn't need.
