# O6 — Premature-Exit Cost — Design Plan

**Date:** 2026-07-26
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** DRAFT — pending Opus design review.

> **One-line spec:** A 4th card ("⏱️ Premature-Exit Cost") slotted into the existing
> 2×2 Behavioral Biases grid on 🪞 Investor Mirror (My Edge). Among your own **winning**
> closed lots, splits them into "quick exit" (held less than half your own average
> winner-hold time) vs "patient" (the rest), and shows the real, share-weighted
> average realized gain % for each group — a descriptive comparison of what actually
> happened, never a speculative "you would have made $X more" estimate.

> **Roadmap context:** Priority 6 of [agentic-intelligence-roadmap-v2.md](agentic-intelligence-roadmap-v2.md)
> (Phase 4, paired with O5 as "the sizing/exit pair").

---

## HEAD audit — one scope correction before this goes to review

The roadmap's one-line description is "Winners sold early vs. your own avg
winner-hold" — a fair description of the COMPARISON, but the feature's name
("Premature-Exit **Cost**") invites building a dollar/percent estimate of what a
quick-exit winner *would have* gained had it been held longer. That estimate is not
buildable honestly: it requires reconstructing what the stock's price *would have
been* on a hypothetical later date — a forecast, not a fact, and exactly the kind of
fabricated-precision number this app's posture forbids (`feedback_recommendation_transparency`,
the "never fabricate" design constraint carried through every item on this roadmap).
**Correction: this feature reports a REAL, ALREADY-REALIZED gap — the share-weighted
average gain% of your quick-exit winners vs. your patient winners — never a
counterfactual "if you'd held" dollar estimate.** The word "cost" in the roadmap name
is descriptive shorthand for that real percentage-point gap, not a license to forecast
a specific position's alternate history.

**Verified against HEAD (2026-07-26):**
- `investor_mirror.build_closed_lots(trades_df)` (`investor_mirror.py:50`) already
  returns exactly the fields needed per matched lot: `ticker`, `days_held`, `pnl_pct`,
  `pnl_abs`, `shares`, `is_gain`. No new data plumbing needed.
- `investor_mirror.disposition_effect()` (`investor_mirror.py:148`) already computes
  `winner_avg_days` (share-weighted) as part of a different comparison (winner vs.
  loser hold time). O6 needs "half of the user's own average winner-hold" as its
  split point — this can be read directly off a fresh `disposition_effect()` call
  (or an equivalent lightweight internal average), not duplicated math.
- The render site, 🪞 Investor Mirror → Behavioral Biases (`app.py:28821-28949`), is
  a 2-column `st.columns(2)` grid (`_mi_bc1`, `_mi_bc2`) with 3 cards placed today:
  Card A (Disposition Effect) + Card B (Win/Loss Closure Ratio) in `_mi_bc1`; Card C
  (Breakeven Anchoring) alone in `_mi_bc2`. **`_mi_bc2` has exactly one open slot** —
  O6 is Card D, placed there, keeping the grid balanced 2×2. `_mi_lots` (the
  `build_closed_lots()` result) is already computed once per render
  (`app.py:28836-28839`, cached by trade-count key) and reused by all three existing
  cards — O6 reuses that same in-memory result, zero new computation cost beyond its
  own bucketing.
- No point-in-time portfolio-value history exists to normalize hold-time or size by
  (out of scope for O6 specifically — sizing normalization is O5's concern, not
  this one; O6 only needs `days_held` and `pnl_pct`, both already correct per-lot).

---

## What already exists (reused, not rebuilt)

| Piece | Where | Status |
|---|---|---|
| FIFO closed-lot builder | `investor_mirror.build_closed_lots()` (`investor_mirror.py:50`) | Shipped. Reused verbatim — same `_mi_lots` session-cached result the other 3 cards already use. |
| Share-weighted average helper | `investor_mirror._weighted_avg()` (`investor_mirror.py:41`) | Shipped, private within the module. Reused directly (O6 lives in the same module). |
| Winner-average-hold computation | `investor_mirror.disposition_effect()` (`investor_mirror.py:148`) | Shipped. O6 needs the same `winner_avg_days` figure — computed independently inside O6's own function (see below) rather than taking a dependency on `disposition_effect()`'s return shape, since that function can return `None` under different gating criteria (needs a populated LOSER group too, which O6 doesn't care about). |
| Behavioral Biases card grid | `app.py:28821-28949`, `_mi_bc1`/`_mi_bc2` columns | Shipped. O6 is a new 4th `st.container(border=True)` card in `_mi_bc2`, sibling to Card C. |

## What's genuinely new

1. **One new pure-Python function**, `investor_mirror.premature_exit_cost(closed_lots, min_n)`:
   - Filter to `is_gain == True` lots with valid `days_held`/`pnl_pct`/`shares`.
   - Compute the share-weighted average `days_held` across ALL winners (this module's
     own copy of the "average winner hold" — independent of `disposition_effect()`'s
     gating on a populated loser group).
   - Split into **quick** (`days_held < PREMATURE_EXIT_RATIO × avg_winner_days`) and
     **patient** (the rest).
   - Requires both groups to have ≥ `min_n` lots (reuses `INVESTOR_MIRROR_MIN_CLOSED_LOTS`,
     same floor already required per-group in `disposition_effect()` — no new floor
     invented).
   - Returns share-weighted average `pnl_pct` for each group, plus counts and the
     computed `avg_winner_days` split-point — never a dollar or "left on the table"
     estimate.
2. **New constant** `PREMATURE_EXIT_RATIO = 0.5` in `constants.py` — the "how much
   shorter than your own average counts as a quick exit" threshold. Same class as the
   sibling `DISPOSITION_CONCERN_RATIO`/`WINLOSS_CONCERN_RATIO`/`BREAKEVEN_ANCHOR_DWELL_RATIO`
   (a tuning ratio for an awareness-only behavioral lens, not a decision gate) — still
   a policy-ish value worth the user's confirmation before ship, per house convention.
3. **Card D** on 🪞 Investor Mirror's Behavioral Biases grid, in the open `_mi_bc2`
   slot, matching the existing 3 cards' exact style (`st.container(border=True)`,
   `st.metric` headline, a caption explaining the comparison, an insufficient-data
   caption when gated).

**Explicitly NOT built:** no counterfactual/forecast price estimate of any kind, no
dollar "cost" figure, no change to `build_closed_lots()`/`disposition_effect()`
themselves, no new page, no LLM call, no cache table (same live-computed, zero-cost
pattern as the 3 existing Behavioral Biases cards).

---

## Design principles (non-negotiable, carried from v1/v2)

1. **Strictly additive.** No gate, no score, no change to any existing card's logic
   or the grid layout beyond adding the 4th card.
2. **Never fabricates a counterfactual.** This is the central discipline for this
   specific feature (see HEAD-audit correction above) — report only the real,
   already-realized average gain% gap between quick-exit and patient winners, never a
   speculative "you'd have made $X more."
3. **Descriptive, not prescriptive.** A gap between the two buckets is context, not a
   directive to "always hold longer" — some quick exits are deliberate, correct risk
   management (a thesis broke early, a stop got hit on a name that happened to still
   be a "winner" by a hair). Caption language must reflect that, mirroring the
   existing cards' "observed patterns — not verdicts" framing (`app.py:28824-28828`).
4. **Same gating floor as its neighbors.** Reuses `INVESTOR_MIRROR_MIN_CLOSED_LOTS`
   per group — no new, weaker floor invented just to force a result to appear sooner.
5. **Graceful degradation.** Returns `None` (→ "insufficient data" caption, matching
   the other 3 cards' exact pattern) when either bucket is below the floor, or when
   `closed_lots` is empty — never raises, never forces a result.

## Non-goals

- Does not estimate a dollar figure, a "cost," or any forecasted alternate-history
  price for any position.
- Does not touch `build_closed_lots()`, `disposition_effect()`, `win_loss_closure_ratio()`,
  or `breakeven_anchoring()` — purely additive, a new sibling function.
- Does not normalize by position size or portfolio weight at entry (that question
  belongs to O5, not this item) — O6 is about **hold-time relative to your own
  average**, not about how large the position was.
- Does not distinguish deliberate risk-management exits from impulsive ones — it
  cannot know intent, only the realized pattern; the caption says so explicitly.
