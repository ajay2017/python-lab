**Status update, 2026-09-12 — this review sat uncommitted and undiscussed for 3 days; triaged against current code before any further reading.** Of Part 2's four ranked findings: **#1 (grade Watchlist ENTER_NOW) is already done** — F-264 shipped the same day this review was written (2026-09-09, commit `e11cdb1`), independently of this review. **#3 (hook-liveness self-check) is now CLOSED** — built same day as this triage (`.claude/hooks/hook_liveness_check.py`, wired into `SessionStart`), and a dry run against real git history immediately found live, concrete evidence the underlying Defect #1 below is still real: two `feat(` commits from 2026-09-11 (`b73a504`, `2cdc42e`) shipped with no `Design=`/`Build=` trailers and no block. **#2 (Industrials/Defense discovery-bucket cross-card dedup) is now CLOSED too** — fixed same session immediately after #3 (`a1e75cd`), filtering `diversifying_candidate_pool()`'s discovery-bucket portion to `TICKER_SECTORS[ticker] == sector`. **Only #4 (measure Rebalancer/Diversification/Tax-Harvest outcomes) remains genuinely open** — it's the one item on this list that's actually large (needs its own `planner` design pass), not a quick pickup. Defect #2 (stale test-suite baseline figure) not re-checked this pass. Full detail: memory `project_app_review_2026_09_09`.

---

# DRISHTA App Review — 2026-09-09

Scope: full app. Compiled directly (not via a delegated sub-agent chain — an
earlier attempt this session fanned out into 4 recursive agents and produced
no output; grounding here is a mix of fresh reads today [CLAUDE.md in full,
`docs/architecture.md` §10 Known Behaviours, `docs/requirements.md`'s full
280-row F-ID map via an earlier survey pass, `docs/shipped-log.md` (partial),
the memory index, and a live grep of `app.py`'s 27-page nav] plus first-hand
work done earlier in this same session on `stock_analyzer/margin.py`,
`macro_calendar.py`, and `analyst_intel.py`/`app.py`'s Ideas Inbox save flow).

---

## Verdict

DRISHTA's genuine differentiator isn't any one feature — it's that the app
has built more *self-grading* infrastructure (Recommendations History, Engine
Track Record, two Self Track Records, a Research Scorecard with calibration,
a Gate Suppression Ledger, a forecast-calibration Model Lab) than almost any
personal tool of this kind, and that infrastructure keeps finding real bugs
in itself (structural starvation in the analyst-calibration matrix, a
reschedule-ordering bug that would have silently emptied a prediction ledger)
rather than just producing pretty charts. The architecture's load-bearing
discipline — constants-as-policy, the `None`-vs-empty-container sentinel, the
publish/consume session-state contract — is real and has repeatedly caught
real bugs (see memory `feedback_sentinel_is_present`,
`feedback_none_sentinel_meets_pandas`). The weakest link is verification
theater: the mechanical hooks CLAUDE.md calls "the real pre-deploy safety
net" turned out today to not even be *running* in this session, and nobody
would have known without deliberately testing it. The single highest-value
next investment is closing the gap between "the engine decides" and "the
engine measures itself deciding" for the handful of surfaces that still slip
through ungraded — Watchlist's `ENTER_NOW` chief among them.

---

## Part 1 — What's working

### Closed-loop decision surfaces (call → capture → grade)

These genuinely close the loop, mostly on **alpha vs SPY** over a maturity
window:

- **F-160/F-160a/F-161** (`docs/requirements.md:647-650`) — the substrate.
  Every `new_pick`/`buy_candidate` recommendation is persisted, cross-referenced
  against `trades`, and graded acted-vs-missed on realized alpha.
- **F-229** Engine Track Record (`:216`) — all-time acted alpha for
  `new_pick`s (Offense) plus a Defense facet grading whether protective
  EXIT/TRIM calls actually avoided drawdown (`protect_alpha_pct = SPY_return
  − flagged_name_return`).
- **F-233/F-256/F-257** Self Track Record, buy- and sell-side (`:215, :276,
  :277`) — re-derives whether a trade was app-aligned or self-initiated
  (never trusts the stored `trigger_type`) and grades both sides on the same
  alpha math.
- **F-154c** Research Scorecard + Phase 3 calibration (`:632`) — grades
  *pasted analyst calls*, not just the engine's own. The Phase 3 2×2
  disagreement matrix (analyst side × engine composite) had a real structural
  starvation bug (the "engine-skeptical, not-held" cell was unreachable) found
  and fixed 2026-09-07 — a good sign the grading layer is actually scrutinized,
  not just shipped and forgotten.
- **F-259/F-259b** Gate Suppression Ledger (`:279-280`) — grades the thing the
  app does *most*: declining to act. Pre-registers its own retirement
  criterion 12 months out if no gate ever produces an evaluable verdict — a
  genuinely rare discipline (most tools don't pre-commit to admitting a
  feature failed).
- **F-234** Model Lab (`:354`) — the only closed loop that isn't alpha-based:
  grades a vol-forecast and an earnings-move heuristic against MAE vs a naive
  baseline. Its own reviewer caught a reschedule-ordering bug pre-ship that
  would have kept the ledger *permanently empty* while logs showed plausible
  churn — exactly the kind of defect a "measure ourselves" feature is supposed
  to catch, caught in the measuring feature's own construction.

### Load-bearing architecture

- **Constants-as-policy** (`stock_analyzer/constants.py`, Hard Rule #1). Every
  threshold this review touched today — `MARGIN_MAINTENANCE_RATE`,
  `FRAGILITY_PULLBACK_PCT`, the six new `_SECTOR_IMPACT` severities — routes
  through one file, one review path. This is real, not decorative: it's the
  reason a margin-formula question today resolved to "the current code is
  provably correct" instead of a guessed patch.
- **The `None`-vs-empty-container sentinel.** `_SECTOR_IMPACT.get(sector, 0)`
  returning 0 for a genuinely missing sector is a *fail-open*, not a neutral
  default (`macro_calendar.py:438-445`) — and the project has hit this exact
  class repeatedly enough to have two dedicated memory files
  (`feedback_sentinel_is_present`, `feedback_none_sentinel_meets_pandas`).
  Real safety: it's caught production bugs, not just been discussed.
- **Multi-source provider failover** (Finnhub→yfinance→FMP) plus the
  price-cross-check guardrail (F-123). Genuinely load-bearing for a
  single-provider-API personal app.
- **The publish/consume `st.session_state` pattern.** The CLAUDE.md
  coordination-cache list is ~30 entries deep and growing — evidence the
  pattern scales, not evidence of tidiness. Real risk lives here too (see
  Defects).

### Real safety vs. ceremony — say it plainly

- **Real:** the full pytest suite (5,139 tests) gates commits *when it
  actually runs*. Verified today: `pytest -m fast` (5,012 of 5,139 tests)
  passed cleanly in 376s on a clean run.
- **Ceremony, confirmed live today, not theoretical:** CLAUDE.md's `Review =` /
  `Design =` / `Build =` citation system is candid on paper that it "cannot
  verify a subagent ran." What's new this session: **the mechanical
  `PreToolUse` hook (`.claude/hooks/pre_tool_checks.py`) that's supposed to
  block a malformed commit didn't fire at all** for three `git commit`
  invocations in this VSCode-extension session. A `feat(` commit went through
  with no `Design=`/`Build=` trailers and no BLOCKED message. Direct test
  (piping synthetic stdin straight at the hook script) proved the *script*
  is correct — it blocks exactly as designed when actually invoked. The gap is
  the harness never invoking it, silently. See Defects §1 — this is exactly
  the "ceremony that only looks like safety" the skill asked to name, and it
  cost nothing this time only because manual checks happened to be run anyway.

### What to keep untouched if the app were cut in half

Constants-as-policy, the `None`-sentinel/offline-detection contract, the
publish/consume coordination pattern, and multi-source provider failover.
Every other feature sits on top of these four; none of the newer LLM-layer
features (Debate Agent, Red Team, Judge) would be trustworthy without them.

---

## Part 2 — Next nice-to-haves (ranked by value ÷ blast radius)

### 1. Watchlist `ENTER_NOW` is completely open-loop — grade it

**What:** `watchlist_advisor.py`'s `ENTER_NOW` verdict (F-102/F-103,
`docs/requirements.md:570-571`) is a real, actionable BUY-timing call —
composite ≥ 65, R:R ≥ `RR_ENTRY_MIN`, a portfolio-risk gate — computed
entirely at render time. **Verified today: `watchlist_advisor.py` has zero
`save_recommendations`/`rec_type` calls, and `app.py` has no
`save_recommendations` call anywhere near its `ENTER_NOW` logic.** It is
structurally invisible to F-160's Recommendations History, F-229's Engine
Track Record, and the Gate Suppression Ledger — none of the app's extensive
grading infrastructure can ever see it. Contrast with `new_pick`/
`buy_candidate`, which get graded automatically once persisted.

**Why it matters:** ENTER_NOW is the single most decision-adjacent verdict on
the Watchlist page — a name the user is actively tracking, told "buy now" —
and it's the one entry point with *no* accuracy feedback at all. On an
18-name, 3.15x-leveraged book, an unmeasured entry-timing call is exactly the
kind of thing that could quietly cost more than the (well-measured) new-pick
funnel.

**What it touches:** `watchlist_advisor.py` (add a `save_recommendations`
call with a new `rec_type="enter_now"` or similar), `recommendations_history.py`
(extend `compute_outcomes`/`by_verdict` to include the new type), possibly a
small addition to F-229's Engine Track Record page.

**Rough cost:** 1-2 sessions. Doesn't touch a gate or a threshold — it's
*measurement*, not decision logic — but touches `daily_briefing.py`-adjacent
persistence, so scope the diff carefully and check `_GATE_FILES` membership
before committing.

**What breaks if wrong:** nothing gates on this. Worst case is a mis-tagged
`rec_type` polluting the scorecard's aggregate stats — annoying, not
dangerous. Low blast radius, real value. **Top pick.**

### 2. Close the Industrials/Defense discovery-bucket cross-card dedup gap

**What:** Already flagged in CLAUDE.md's own queue (2026-09-02 entry,
commit `ee0e0f6`): Industrials and Defense share one `"Industrials & Defense"`
discovery bucket with **no cross-card dedup** in
`diversifying_candidate_pool()`. The same non-held name (e.g. RTX/GD/CAT) can
surface under both the "Add Defense" and "Add Industrials" ADD cards, and
Defense names can appear mislabeled under the Industrials card.

**Why it matters:** two diversification ADD cards recommending the *same*
ticker under two different sector rationales, on the same day, with neither
card aware of the other — a direct instance of the "two features could
contradict each other on the same ticker on the same day" coordination gap
the review brief explicitly flags as high-value to find.

**What it touches:** `diversifying_candidate_pool()` (shared by both cards),
possibly a `TICKER_SECTORS`-based filter per card.

**Rough cost:** small, well-scoped — the finding itself already names the
fix shape ("filter each card's discovery-bucket slice to names whose
`TICKER_SECTORS` value equals that card's own sector, or give Industrials a
dedicated bucket"). Needs its own review since it touches shared
Defense-card behavior (already noted in the original finding).

**What breaks if wrong:** awareness-surface only (Risk Analysis/Signals, not
Act Today) — cannot suppress a protective call or break a gate. Safe to get
slightly wrong.

### 3. A hook-liveness self-check (motivated directly by today's finding)

**What:** `session_start_ci_check.py` already exists to bridge one class of
gap (git-remote CI state). Add a companion check — at `SessionStart`, fire a
synthetic stdin payload at `.claude/hooks/pre_tool_checks.py` directly (the
same test that surfaced today's finding) and warn loudly if it doesn't
respond as expected, rather than relying on the *next* malformed commit to
reveal a silently-inert hook.

**Why it matters:** today's finding means "the hook is configured in
`settings.json`" and "the hook is actually enforcing" are NOT the same fact,
and nothing currently distinguishes them without a manual test. This is
squarely "silent in a way that reads as checked and fine."

**What it touches:** a new hook script only; doesn't touch app code at all.

**Rough cost:** small — under a session. Zero blast radius (it's tooling,
not decision logic).

**What breaks if wrong:** nothing — false positives just mean an unnecessary
warning at session start.

### 4. Bigger, multi-week: measure Rebalancer / Diversification ADD / Tax-Harvest outcomes

**What:** F-13 (Rebalancer), F-13a (Diversification ADD cards), and F-16/F-186
(Tax Advisor HARVEST / Tax-Aware Exit) all issue concrete recommendations with
no dedicated "did this help" measurement — distinct from the engine's
buy-side `new_pick` funnel, which is graded.

**Why it matters:** these are exactly the "app decides but doesn't measure
whether it was right" gap the review brief asks to prioritize, and they're
larger in scope than #1/#2 above (rebalancer trims touch multiple tickers at
once; tax-harvest has its own holding-period/wash-sale logic).

**What it touches:** likely a new grading module analogous to
`recommendations_history.py`, cross-referencing `trades` against rebalancer/
harvest suggestions — much bigger surface area than a single new `rec_type`.

**Rough cost:** genuinely multi-week. Needs a `planner` design pass (how do
you attribute "outcome" to a multi-ticker rebalance?) before any
`implementer` work, and the design itself has real judgment calls (e.g., does
a HARVEST recommendation get graded on tax dollars saved, or on whether the
replacement position outperformed the sold lot?).

**What breaks if wrong:** nothing gates on it, but a badly-designed metric
could produce a misleading "the rebalancer doesn't help" or "definitely
helps" conclusion that then gets over-trusted. This is the one item on this
list where the measurement's own validity needs the same scrutiny the
Analyst Calibration matrix got in F-154c.

---

## Part 3 — Innovations

Constrained per the brief: single user, Railway Hobby + Supabase, real LLM
budget, never manufacture a buy or loosen a gate.

### Kept: Decision Provenance Timeline

**Thesis:** for any single ticker, nothing today shows *every* decision
surface's history in one place. `exit_signals` (deterioration tier over
time), `analyst_coverage` (saved research), `recommendations` (past
composite/verdict), `thesis_reviews` (INTACT/WEAKENING/BROKEN history), and
`judgment_grades` (F-227's per-witness grading) all exist as separate tables
each surfaced on separate pages. Prior Trades (F-237) is trade-history-centric,
not decision-surface-centric — it shows what you *did*, not what the *engine
said* over time.

**Smallest honest version:** a read-only expander/page, one ticker at a time,
that queries the existing tables above and renders them as one chronological
list — composite score trend, tier changes, saved analyst calls, thesis
review verdicts — no new write path, no new decision, no new LLM call. Purely
a read-side aggregation of data that already exists.

**Falsifiable signal:** track whether it's ever opened, and — more
importantly — whether it ever surfaces a cross-surface disagreement (e.g.
"thesis review said WEAKENING three weeks before the deterioration ladder
caught it") that wasn't visible on any single existing page. If, after a
defined window (say 90 days), it's neither used nor ever catches a
discrepancy the individual pages didn't already show, retire it — same
pre-registered-retirement discipline the Gate Suppression Ledger already
uses.

### Kept: Session Delta Digest

**Thesis:** F-110/F-111 (AI Snapshot) already narrates *current* state. What
doesn't exist is a narration of the *delta since the user's last visit* — new
gate suppressions, new tier changes, new analyst coverage saved by the app's
own cron lanes — for a single user who may not open the app every day. This
serves the calm-advisor/anti-noise posture better than a full snapshot: it
says only what's new, not everything that's still true.

**Smallest honest version:** a Haiku call that narrates a small,
Python-assembled diff package (same pattern as the Weekly Debrief and Monthly
Report — the LLM narrates only the package, never adds facts) comparing
`st.session_state`'s last-visit signature against today's persisted state
(exit_signals tier changes, new Gate Ledger suppressions, new saved analyst
coverage). Reuses the exact narrate-only-the-package discipline already
proven safe in F-152/F-153.

**Falsifiable signal:** if the delta is empty on most visits (the user opens
the app daily and nothing material changed), this adds no value over the
existing Home page and should not be built further. Cheapest test: log how
often the delta package is actually non-trivial before writing a single
prompt.

### Rejected: sizing modulated by the engine's own grading history

Not novel — this is already F-234's explicitly-scoped, not-yet-approved
Phase 3 ("wire ONE validated signal into ONE protective gate as
tightening-only... months out, full `planner`+`reviewer`, fresh explicit
approval"). Re-proposing it here would just restate an already-planned,
already-gated future phase.

### Rejected: external social/community sentiment overlay

No falsifiable signal for a single-user app (whose "community" would it
measure?), a new noisy data provider to maintain, and it cuts directly
against the calm-advisor anti-noise posture (§2B) the app has repeatedly
chosen to protect (e.g., the deliberately-declined Analyst Coverage Brief
chip, Self Track Record's decision-moment mirror rejection). Highest cost,
lowest confidence of the ideas considered.

---

## Defects flagged

### 1. The PreToolUse commit hook did not fire this session (confirmed, not theoretical)

`.claude/hooks/pre_tool_checks.py` is described in CLAUDE.md's "Review & test
economy" section as enforcing Hard Rules #3/#4/#5 mechanically and being "the
real pre-deploy safety net." **Verified today, live:** three `git commit`
invocations in this VSCode-extension/Agent-SDK session went through with no
hook interception at all — including a `feat(` commit that shipped with no
`Design=`/`Build=` trailers and no BLOCKED message. Root-caused by direct
test: piping a synthetic `{"tool_input": {"command": "..."}}` payload straight
at the hook script (matching the actual commit's shape, using a repo-relative
message path) produced the correct `BLOCKED` / exit 2 response — so the
*script* is not the bug. The harness simply never invoked the
`PreToolUse` hook for this session's Bash tool calls. **Recorded in memory
`feedback_hook_enforcement.md` (2026-09-09 entry) but CLAUDE.md's own "Review
& test economy" section still describes these gates without this caveat** —
a reader trusting that section at face value in a similar runtime would
believe a safety net is active when it may not be. No actual defect shipped
as a result this session (pytest/antipatterns were run manually, and none of
the three commits touched a `_GATE_FILES` member), but the exposure is real:
this is exactly the "ceremony that only looks like safety" pattern.

**Suggested fix:** add one sentence to CLAUDE.md's "Review & test economy"
section noting that hook enforcement is confirmed active for the standard
Claude Code CLI but has been found NOT to fire in at least one alternate
runtime (VSCode extension / Agent SDK) — and that Nice-to-Have #3 above (a
liveness self-check) would close this properly rather than relying on prose.

### 2. `docs/plans/test-suite-optimization.md`'s baseline collection-time figure is contradicted by a fresh measurement

The plan doc states "Baseline Collection Time: ~120 seconds" (line 3).
**Directly measured today:** `pytest --collect-only -q` completed in **7.37
seconds** (5,139 tests collected) — over 16x faster than the documented
baseline, too large a gap to attribute to codebase growth alone (the suite
grew from 3,674 to 5,139 tests over the same period, which should have made
collection *slower*, not 16x faster). Either the original 120s figure
conflated collection with something else (environment cold-start, a full
`pytest -q` run rather than `--collect-only`), or it was measured under a
materially different condition. **This may already be self-correcting** — a
test-suite-speedup task was in flight in this same session with instructions
to correct this exact figure if a fresh measurement disagreed; check whether
`docs/plans/test-suite-optimization.md`'s status line and baseline figure
were updated before treating this as still-open.

---

## If you only do three things

1. **Grade Watchlist `ENTER_NOW`** (Nice-to-Have #1) — the single actionable
   entry-timing call with zero accuracy feedback, cheapest fix relative to its
   value, no gate/threshold risk.
2. **Add the hook-liveness self-check** (Nice-to-Have #3) — near-zero cost,
   directly motivated by a real gap found and confirmed today, prevents this
   exact "safety net that wasn't" class from recurring silently.
3. **Close the Industrials/Defense discovery-bucket dedup** (Nice-to-Have #2)
   — already scoped in the existing queue, small, well-understood fix that
   closes a real (if low-stakes) cross-card contradiction.
