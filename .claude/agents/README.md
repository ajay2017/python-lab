# Agent roster — how we split work to optimize cost & quality

DRISHTA is a **correctness-bound** project: it issues actionable buy/sell calls,
so a wrong recommendation or a silently-broken gate costs far more than model
tokens. The savings come from **delegating the easy parts down** to cheaper models
while the lead orchestrates and the Opus reviewer guards the decision logic.

> **Lead model (2026-07-22):** formally set to **Sonnet 5** (main session).
> The `reviewer` remains pinned to **Opus** — that gate is non-negotiable
> regardless of what the lead is. See `docs/cost-routing.md` for the decision
> rationale and updated economics.

## The model tiers

| Tier | Model | Does the work that is… |
|------|-------|------------------------|
| **Lead** | Sonnet 5 (main session) | Orchestration: design, threshold/gate/coordination decisions, subtle debugging, planning, final review. Capable enough for this role; Opus stays as the mandatory review gate before anything that touches decision logic. |
| `Plan` | **plan** (built-in) | Read-only architectural scaffolding: structural layout of a new page, DB table design, session-state wiring, non-gate feature scaffolding. Returns a spec; the lead decides on any policy content inside it. Use for non-trivial structural questions separable from gate/threshold policy. |
| `reviewer` | **opus** | A focused review pass on changes touching decision logic / constants — read-only, returns SHIP / FIX-FIRST. This is a correctness premium (~67% cost uplift over the Sonnet 5 lead) that is always worth paying before committing anything that moves money. |
| `implementer` | **sonnet** | A scoped, already-decided edit: wire a constant, add a render block, mechanical refactor, clear-repro fix. Same tier as lead — value is scope isolation and context hygiene, not dollar savings. |
| `doc-writer` | **haiku** | Cheap mechanical write-ups: a constants-table row, a Known-Behaviours row, an F/gate row, a code comment. Strong-saving lane (~67% vs Sonnet 5 lead at list price). |

Model is set per agent via the `model:` frontmatter (`opus` / `sonnet` /
`haiku`). The lead can also override it per-invocation when needed.

## The workflow: PLAN → ROUTE → BUILD → REVIEW → COMMIT

1. **PLAN (Sonnet 5 lead).** Decide *whether* to do it and *exactly how* —
   especially any constant/threshold/coordination call. Output: a precise spec
   per chunk. For non-trivial structural scaffolding with no policy content
   (new page layout, table schema), optionally hand that sub-question to `Plan`
   first and fold the result into the spec.
2. **ROUTE.** For each chunk, the lead picks the right agent:
   - ambiguous / decision-bearing / cross-feature → **keep it on the lead**
   - structural scaffolding (no gate/threshold policy) → **`Plan`** (read-only, returns spec)
   - scoped, decided edit → delegate to **`implementer`** (sonnet — context hygiene)
   - doc/comment write-up → delegate to **`doc-writer`** (haiku — strong-saving lane)
   - broad code search ("find every place that gates on sector") → **`Explore`**
     (built-in, fast read-only fan-out)
3. **BUILD.** Workers make the edit and compile-check. They do **not** commit and
   do **not** invent thresholds — if a worker hits a decision it isn't
   authorized to make, it reports back instead of guessing.
4. **REVIEW (Opus `reviewer`).** Before committing anything that can affect a
   recommendation or a gate, run the `reviewer`. It traces the data path against
   the hard rules and the calm-advisor posture and returns SHIP / FIX-FIRST.
   This step is mandatory regardless of which model is running the lead session.
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

## Tracking the savings

Routing decisions and the savings on delegated work are logged in
[`docs/cost-routing.md`](../../docs/cost-routing.md) — a running ledger appended
at commit time (one row per delegated task, plus decisions *not* to delegate).
It measures the delegated slice only, not total cost; the authoritative total is
the Anthropic Console / subscription usage view.

## TL;DR

Sonnet 5 leads and orchestrates; Opus reviews every gate/decision-logic change
before it ships; Haiku writes up the docs (the strong-saving lane). The Plan
agent handles structural scaffolding so the lead's context stays clean. The
calls that move money always pass through the Opus gate — regardless of what
model is running the session.
