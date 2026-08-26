---
name: app-review
description: "Product & engineering review of DRISHTA — what's genuinely working, the highest-value next nice-to-haves, and bigger innovations worth considering, plus any doc/code drift defects found along the way. Grounded in the code, and constrained so it never re-proposes work the owner has already parked or declined. Writes findings to docs/reviews/<date>-app-review.md. Invoke with /app-review."
allowed-tools: Read, Glob, Grep, Bash(git *), Write
argument-hint: "[area-or-page to scope to]"
---

You are reviewing DRISHTA · Beyond Noise — a single-user personal portfolio
intelligence app (Streamlit + Supabase, deployed on Railway at
drishta.up.railway.app, ~37.6k lines in `app.py`, ~109 modules in
`stock_analyzer/`, 27 pages, 6 scheduled cron lanes).

Its operating posture is: **the app decides, it does not inform.** Recommendations
are actionable calls; gates are hard suppressions with visible banners, not soft
warnings. When in doubt it recommends nothing rather than recommending wrongly.

I want three things: (1) an honest read on what is genuinely working, (2) the
highest-value nice-to-haves to build next, (3) genuine innovations worth
considering. **Do NOT write code.** This is a judgment pass, not an implementation.

If `$ARGUMENTS` names a specific area, page, or module, scope the review to that
area and its immediate blast radius. Otherwise review the full app.

---

## Step 1 — Ground yourself (do not skip, do not work from memory)

Read, in this order:

1. `CLAUDE.md` — especially "Operating posture", "Hard rules", and the full
   "What's queued" section.
2. `docs/requirements.md` — the functional spec; F-IDs are the unit of feature.
3. `docs/architecture.md` — data flow, scoring model, DB schema, known behaviours.
4. `docs/shipped-log.md` — what already exists. Skim, but actually check it before
   calling anything "missing".
5. `stock_analyzer/constants.py` — every decision threshold lives here.
6. `app.py`'s page list and the module names in `stock_analyzer/` — for the map.

Every claim you make must be traceable to a `file:line` or an F-ID. If you cannot
confirm something in the code, say **unverified** rather than asserting it. A wrong
value in a review of a decision-making app is worse than a missing one.

---

## Step 2 — Hard constraints on your recommendations

- **Nothing in "What's queued → Genuinely not yet done" is a new idea.** Several
  entries there are explicitly PARKED, DECLINED, or REJECTED with a stated revisit
  trigger (e.g. option (c) margin gate recalibration, the Portfolio Q&A
  `current_status` intent, the Self Track Record decision-moment mirror, a Utilities
  sector, deterioration-card hysteresis). If you want to raise one of those, you
  must engage the recorded reasoning and argue the trigger has fired — otherwise
  leave it alone.
- **Do not propose threshold or gate changes as suggestions.** Those are
  investment-policy decisions made with the owner. You may flag "this threshold
  looks like it may be mis-calibrated because \<evidence\>", framed as a question.
- **Respect the redlines**: AI narrates, never originates. Leverage/margin data is
  awareness, never a gate. Producers publish `None` (not an empty container) on
  failure so consumers can detect offline. Any proposal that violates one of these
  must say so explicitly and justify it.
- Prefer depth over breadth. Ten well-grounded findings beat forty shallow ones.

---

## Step 3 — Part 1: What's working (be specific, and be willing to be unimpressed)

Assess against the app's own stated goal, not generic best practice:

- Which decision surfaces actually **close a loop** — produce a call, capture what
  happened, and feed the outcome back? Name them.
- Where is the architecture genuinely load-bearing? (the publish/consume
  `st.session_state` coordination pattern, constants-as-policy discipline, the
  deterministic commit gates, multi-source provider failover, the
  `None`-on-failure contract, the review/provenance trailers.)
- Which of these are **real safety**, and which are **ceremony that only looks like
  safety**? Say so plainly — `CLAUDE.md` itself is candid that the commit hook
  proves a citation exists, not that a reviewer ran.
- What would you keep untouched if you had to cut the app in half?

---

## Step 4 — Part 2: Next nice-to-haves

For each, give: **what**, **why it matters** to a single retail investor running a
~3.15x-leveraged 18-name book, **what it touches**, **rough cost**, and **what
breaks if it's wrong**. Rank by (value to decision quality) ÷ (blast radius).

Bias toward:

- Gaps where the app *decides* but doesn't yet *measure whether it was right*.
- Surfaces that exist but are hard to act on (friction, not absence).
- Coordination gaps: two features that could contradict each other on the same
  ticker on the same day without either knowing.
- Anything where the app is currently **silent in a way a user could misread as
  "checked and fine"** — the app's most dangerous failure mode.

Explicitly separate "this is a 1-session polish" from "this is a multi-week,
policy-constant, `planner`+`reviewer` build."

---

## Step 5 — Part 3: Innovations

Bigger swings. Constrain them: single user, no team, Railway Hobby + Supabase, the
LLM budget is real, and the owner will not accept a feature that manufactures a buy
signal or loosens a protective gate.

For each innovation give the thesis, the **smallest honest version that would test
it**, and the **falsifiable signal** that would tell you it isn't working. Reject
your own weakest two before you present.

---

## Step 6 — Also flag: defects, not opportunities

Anything you found that is a *defect* rather than an opportunity — stale docs, a
claim in `docs/requirements.md` or the in-app User Guide that the code no longer
supports, a constant documented at a value it no longer holds, a queue entry
describing a blocker that has since been resolved. This class of drift has bitten
the project repeatedly, so look for it deliberately rather than incidentally.

---

## Step 7 — Output

Write to `docs/reviews/<YYYY-MM-DD>-app-review.md` (date from the session context,
America/New_York). Structure:

- **Verdict** — 5 sentences max, the honest headline.
- **Part 1 — What's working**
- **Part 2 — Next nice-to-haves** (ranked)
- **Part 3 — Innovations**
- **Defects flagged**
- **If you only do three things** — a closing shortlist.

Cite `file:line` or F-ID throughout. Mark every unverified claim as **unverified**.
Do not commit the file; leave it in the working tree for review.
