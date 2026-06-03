# Agent roster — how we split work to optimize cost & quality

DRISHTA is a **correctness-bound** project: it issues actionable buy/sell calls,
so a wrong recommendation or a silently-broken gate costs far more than model
tokens. The savings therefore come **not** from downgrading the model that does
the hard thinking, but from **delegating the easy parts down** to cheaper models
while a capable lead plans and reviews.

## The model tiers

| Tier | Model | Does the work that is… |
|------|-------|------------------------|
| **Lead** | Opus 4.8 (main session) | Reasoning-dense: design, threshold/gate/coordination decisions, subtle debugging, planning, final review. |
| `reviewer` | **opus** | A focused review pass on changes touching decision logic / constants — read-only, returns SHIP / FIX-FIRST. |
| `implementer` | **sonnet** | A scoped, already-decided edit: wire a constant, add a render block, mechanical refactor, clear-repro fix. |
| `doc-writer` | **haiku** | Cheap mechanical write-ups: a constants-table row, a Known-Behaviours row, an F/gate row, a code comment. |

Model is set per agent via the `model:` frontmatter (`opus` / `sonnet` /
`haiku`). The lead can also override it per-invocation when needed.

## The workflow: PLAN → ROUTE → BUILD → REVIEW → COMMIT

1. **PLAN (Opus lead).** Decide *whether* to do it and *exactly how* —
   especially any constant/threshold/coordination call. This is where judgment
   lives and where Opus earns its cost. Output: a precise spec per chunk.
2. **ROUTE.** For each chunk, the lead picks the cheapest model that can do it
   safely:
   - ambiguous / decision-bearing / cross-feature → **keep it on the lead**
   - scoped, decided edit → delegate to **`implementer`** (sonnet)
   - doc/comment write-up → delegate to **`doc-writer`** (haiku)
   - broad code search ("find every place that gates on sector") → **`Explore`**
     (built-in, fast read-only fan-out)
3. **BUILD.** Workers make the edit and compile-check. They do **not** commit and
   do **not** invent thresholds — if a worker hits a decision it isn't
   authorized to make, it reports back instead of guessing.
4. **REVIEW (Opus `reviewer`).** Before committing anything that can affect a
   recommendation or a gate, run the `reviewer`. It traces the data path against
   the hard rules and the calm-advisor posture and returns SHIP / FIX-FIRST.
5. **COMMIT (lead).** The lead commits/pushes once the review passes. Commit
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
- Explicit: `@agent-implementer` / `@agent-reviewer` / `@agent-doc-writer`.
- Defaults & precedence: project agents in `.claude/agents/` (version-controlled,
  shared) override `~/.claude/agents/`. Filename is cosmetic; identity is the
  `name:` field.

## TL;DR

Opus thinks and reviews; Sonnet builds the decided thing; Haiku writes it up.
Cost drops because the cheap work moves to cheap models — while the calls that
move money never leave the model that's best at not getting them wrong.
