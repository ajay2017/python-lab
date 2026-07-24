# Autonomous Pattern Discovery — Design Plan

**Date:** 2026-07-24
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** SHELVED 2026-07-24 — not building. See "Resolution" section at the bottom.

> **One-line spec (as designed, never shipped):** A new "🔍 Your Current Blind Spots"
> section at the top of the existing 🪞 Trade Review page that would unify Trade
> Review's own 9 diagnostics with Behavioral Fingerprint's 6 patterns into one
> cross-surface ranked list. **This design did not survive Opus review — see below.**

---

## Why this plan differs from the roadmap's original framing

`docs/plans/agentic-intelligence-roadmap.md`'s Idea #6 (2026-07-23) envisioned an
agent that runs **open-ended, unsupervised combinatorial search** over the trade
history to discover a wholly new pattern — e.g. "you underperform when you buy
semiconductor names with >3 consecutive green days before entry" — plus a fabricated
"blind-spot score" (surprise vs. stated beliefs) and an "anti-pattern recommendation."
The roadmap itself flagged this as the single riskiest idea on the list ("highest
statistical validity risk"), gated on ≥30 completed trades.

**Two research findings, both confirmed 2026-07-24, changed the design:**

1. **The account now has 66 SELL / 75 BUY trades — the ≥30 gate is cleared.** But a
   second, more important number: only **17 total trades (7 BUY + 10 SELL) have
   `decision_context` populated** (that snapshot only started capturing 2026-07-17).
   `decision_context` — regime-at-entry, portfolio-state-at-entry — is the only
   genuinely untapped dimension in the codebase; 17 rows is far too thin to test even
   a single two-bucket split on it safely, let alone several. This is a hard data
   ceiling, not a design choice.

2. **`stock_analyzer/trade_review.py` (🪞 Trade Review nav page) already exists**,
   predates both Behavioral Fingerprint (F-193, 2026-07-17) and the roadmap itself
   (2026-07-23), and was not accounted for when the roadmap's P6 section was written —
   it still describes Autonomous Pattern Discovery as wholly novel. Trade Review ships
   **9 fixed, single-dimension diagnostics**, each with its own inline sample-size gate:
   holding-period imbalance (≥3 wins + ≥3 losses), signal-defying bias (≥3 defying),
   vs-SPY drag (≥3 closed trades), re-entered tickers (≥2 buys same ticker), trigger-type
   effectiveness (≥6 trades, ≥2 types ≥3 each, ≥20pp spread), lesson-capture rate (≥6
   judged trades), day-of-week timing (≥10 judged trades, ≥2 weekdays ≥2 each, ≥30pp
   spread), position-size discipline (≥3 trades), sector mix (≥4 trades).

Combined with Behavioral Fingerprint's 6 patterns (momentum-recency, conviction-tier,
opening-window, signal-response-rate, signal-lag, escalation-ignored — each gated at
`BEHAVIORAL_MIN_SAMPLE_N = 8` per bucket), **the account already runs 15 pre-named
single-dimension outcome-correlation tests.** Nearly every dimension a fresh
"unsupervised discovery" pass would plausibly propose (day-of-week, holding-period,
position-size, sector, momentum, conviction, entry-timing, signal-response, re-entry,
trigger-type, lesson-category) is already covered somewhere. Running an open-ended
combinatorial search across the *remaining* thin cross-section would be exactly the
multiple-comparisons trap the roadmap flagged: with few genuinely unexplored
dimensions left and small per-bucket samples, a generic "try many field combinations"
engine would reliably surface noise that looks like signal.

**User decision (2026-07-24, confirmed via question):** don't build the original
open-ended search now. Instead, **rescope P6 to a cross-surface ranking layer** — a
small, safe, genuinely-new capability that reuses all 15 existing, already-validated
diagnostics verbatim and adds the one thing that doesn't exist today: a unified view
across both surfaces. Revisit the richer `decision_context`-driven version as a future
phase once that table has materially more populated rows (no specific date set —
accumulates only on new interactive trades).

---

## What already exists (reused, not rebuilt)

| Piece | Where | Status |
|---|---|---|
| 9 single-dimension trade diagnostics + severity tiers (`critical`/`watch`/`good`) | `stock_analyzer/trade_review.py` (`_diag_*` functions, `build_recommendations`) | Shipped, predates this plan — reused verbatim, no new stats |
| 6 single-dimension buy/exit patterns | `stock_analyzer/behavioral_fingerprint.py` | Shipped 2026-07-17 (F-193) — reused verbatim, no new stats |
| Trade Review page's existing data plumbing (trades_df, priced positions, SPY history) | `app.py` ~line 19412 onward | Shipped, unchanged — this plan renders one new section on this page, doesn't touch its existing diagnostics |
| Behavioral Fingerprint's DB reads (`db.load_recommendations()`, `db.load_exit_signals()`) | `stock_analyzer/db.py` | Shipped, cheap (no price/SPY fetch) — safe to call again from Trade Review's page |

## What's genuinely new

1. **One small aggregator function** (`stock_analyzer/trade_review.py` or a new
   tiny module — TBD at implementation) that calls both `trade_review.build_recommendations()`
   (already computed on this page render) and `behavioral_fingerprint`'s pattern
   functions (fresh call — cheap, DB-only), normalizes each into a common shape
   (`source`, `label`, `severity`, `narrative`, `sample_n`), filters to
   `severity == "critical"` (Trade Review's top tier) / the equivalent
   "acted_on gap" tier in Behavioral Fingerprint, and sorts by `sample_n` descending
   (larger sample = more statistically trustworthy = surfaced first).
2. **No fabricated cross-domain magnitude score.** Ranking is ordinal only: severity
   tier (already-computed, not new) as the primary key, sample size as the tiebreaker.
   Two diagnostics reporting incompatible units (a 30pp day-of-week win-rate spread vs.
   a signal-lag day-count) are never forced into one fabricated number.
3. **A render block** at the top of 🪞 Trade Review — "🔍 Your Current Blind Spots" —
   listing every critical-tier finding across both surfaces, each in its own native
   narrative/severity (no rewritten text), or a plain "No active blind spots right now"
   message when nothing clears the bar. Reuses the existing severity chip style
   (🔴/🟡/🟢) already on this page.
4. **A one-line cross-link** on 🎯 My Edge → Behavioral Fingerprint tab pointing to
   🪞 Trade Review for the unified view, so a user who only visits My Edge knows the
   fuller ranking exists.

**Explicitly NOT built:** no new dimension/statistic, no `decision_context`-based
pattern (data too thin — see above), no fabricated "blind-spot score" or "surprise vs.
stated beliefs" metric, no anti-pattern recommendation text (each diagnostic's
existing narrative already frames the beneficial vs. harmful axis; nothing new to
invent), no new DB table, no new LLM call.

---

## Design principles (non-negotiable)

1. **Strictly additive.** Nothing here touches a gate, a score, or a recommendation.
   Pure retrospective display, reusing outputs that are already awareness-only.
2. **No new statistical risk.** Every number shown already passed its own existing
   sample-size gate in its source module. This plan adds a filter + sort, not a new test.
3. **Graceful degradation.** If neither source has a critical-tier finding, show a
   plain "no active blind spots" message — never force a result to appear.
4. **Never invents a comparability that doesn't exist.** Ordinal ranking only (severity
   tier, then sample size) — no synthetic composite score across heterogeneous units.
5. **Zero new cost.** No new LLM calls, no new external data fetches, no new cache
   table. This is the cheapest Agentic Intelligence idea shipped so far.

---

## Open design questions (for Opus review)

1. Does `behavioral_fingerprint.py` expose its 6 pattern functions in a way that's
   cheap to call a second time from the Trade Review page render (i.e., are its DB
   reads already cached in `st.session_state` from elsewhere, or would this be a
   fresh, uncached load on every Trade Review page visit)? If uncached, should this
   plan add a session-state cache key for it (following the existing coordination
   pattern), or is a fresh load on this page acceptable given it's DB-only (no
   external API calls)?
2. Behavioral Fingerprint's patterns don't use Trade Review's `critical/watch/good`
   vocabulary — they're framed as "meaningful delta" (`BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP`/`_ALPHA_DELTA_PP`). What's the correct mapping from Behavioral Fingerprint's own
   internal significance signal to Trade Review's `critical` tier, so the two surfaces'
   "critical" bars are honestly comparable rather than one being looser than the other?
3. Confirm placement: top of 🪞 Trade Review (this plan's proposal) vs. a new small
   section on 🎯 My Edge (would require plumbing Trade Review's outputs across pages via
   session-state, since My Edge doesn't already load price/SPY data) — is the
   Trade-Review-page placement clearly preferable, or is there a reason to prefer My Edge
   despite the extra plumbing?

---

## Resolution (2026-07-24) — SHELVED, not building

Opus design review returned **FIX-FIRST** on the rescoped plan above, with findings
that closed off the remaining viable scope rather than just requiring edits:

1. **`behavioral_fingerprint.py` has no severity/"critical" tier at all.** All six
   pattern functions return raw stat dicts (`delta_pp`, `direction`, `action_rate`,
   `ignored_rate`) with no severity field — the plan's premise that it could be
   "reused verbatim" into a severity-filtered ranking was factually wrong. Only one of
   the six (`momentum_recency_pattern`) even has a binary meaningful/flat signal
   (`BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP=5.0`, a display threshold, not a graded
   scale).
2. **Answering open question #2 honestly ("you can't")** undercut the ranking design:
   Trade Review's `critical` tier sits atop a real 3-tier ladder with strict, per-diagnostic
   bars (e.g. `_diag_vs_spy_drag` critical = beat-rate <50%, `_diag_re_entered_tickers`
   critical = net ≤ -$200). Mapping Behavioral Fingerprint's "meaningful" (a 5pp wobble)
   onto that same `critical` label would be dramatically looser — dishonest ranking, not
   a fixable bug.
3. **Posture violation:** promoting Behavioral Fingerprint's neutral momentum/conviction
   cards into a "🔍 Blind Spots" list contradicts that tab's own on-screen promise
   (`app.py:27602-27603`) that these are "not verdicts, not biases you're being accused
   of, and never something the engine acts on." Re-labeling a deliberately neutral
   correlation as a "blind spot" is a §2A/§2B operating-posture regression.
4. A `severity=='critical'` filter mechanically reaches only 7 of Trade Review's 9
   diagnostics — `position_size_discipline`/`sector_mix` carry no severity field either.
5. The "zero new cost" claim was wrong as long as Behavioral Fingerprint's uncached
   `db.load_recommendations()`/`load_exit_signals()` reads were pulled onto the Trade
   Review page — a real per-render latency cost, not a paid-API cost, but still an
   inaccurate claim in the original plan.

**A second, independent finding (follow-up check, not from Opus) closed the remaining
fallback scope too:** with Behavioral Fingerprint correctly dropped from the merge (per
finding #1's safest fix), the only remaining content would be Trade Review's own 7
severity-bearing diagnostics — but Trade Review **already renders these as a
severity-sorted list** in its existing "🎯 Course-Correction Recommendations" section
(`app.py:19819-19824`, comment: "Severity-sorted (critical first) so the most important
course correction is at the top"). There is nothing left to build; the fallback design
would have been a pure duplicate of an existing section.

**User decision (2026-07-24, confirmed via question):** shelve P6 outright rather than
ship a diminished or redundant feature. The Agentic Intelligence Roadmap closes at
P1-P5 shipped; P6 is evaluated-and-shelved, not deferred to a future phase with an open
gate. See `docs/plans/agentic-intelligence-roadmap.md` and memory
`project_agentic_intelligence_roadmap` for the closing status.

**If revisited in the future:** the only genuinely new, currently-untested ground is
`decision_context` (regime-at-entry, portfolio-state-at-entry) — 17 populated rows as
of 2026-07-24, far too thin to test safely. Any future attempt should wait for
materially more accumulation and design a fresh single-dimension test on that data,
not attempt open-ended combinatorial search (the multiple-comparisons risk that sank
the original roadmap framing) or a cross-surface merge with Behavioral Fingerprint
(the posture/severity-mismatch risk that sank this plan).
