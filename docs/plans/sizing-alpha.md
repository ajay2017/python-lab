# O5 — Sizing Alpha — Design Plan

**Date:** 2026-07-26
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** DRAFT — pending Opus design review.

> **One-line spec:** A new full-width "📏 Sizing Alpha" section on 🪞 Investor Mirror
> (My Edge), below the existing Behavioral Biases grid. Splits your own closed-lot
> history into dollar-size terciles (Small/Medium/Large, based on YOUR OWN historical
> distribution, not a fixed threshold) and shows the real, share-weighted average
> realized gain % for each tercile — descriptive evidence for whether your biggest
> bets have actually captured your best outcomes, or whether sizing has been
> effectively flat regardless of how things turned out.

> **Roadmap context:** Priority 6 of [agentic-intelligence-roadmap-v2.md](agentic-intelligence-roadmap-v2.md)
> (Phase 4, paired with O6 as "the sizing/exit pair").

---

## HEAD audit — two scope corrections before this goes to review

The roadmap's one-line description is "Flat sizing across conviction tiers = alpha
left on the table," implying a bucket-by-**conviction** design (conviction tier →
does size track it). Verified against HEAD (2026-07-26), that literal design is not
honestly buildable, and the fallback that follows is:

**1. There is no reliable "conviction at the time of this specific BUY" field
anywhere in the schema.** Checked every candidate:
- `recommendations.conviction`/`composite_score` (`db.py:127-142`) is written only
  when a ticker is *surfaced* by the engine (`new_pick`/`add_winner`/`buy_candidate`)
  — joining it to a BUY trade by `(ticker, rec_date ≈ trade_date)` would have the
  same coverage gap O1 already hit and worked around: only a minority of BUYs
  correspond 1:1 to a same-day engine surfacing (many are manual buys, adds off
  personal research, or buys days/weeks after a rec first appeared). Forcing this
  join would silently exclude an unknown, unverified fraction of trade history and
  risk the exact "0-1 member buckets" wall that sank the original P6 (per O1's own
  documented 44-distinct-ticker finding).
- `decision_context` (`decision_context.py`) captures **portfolio-level** context at
  the trade-write moment (macro regime, portfolio beta, top sector, active-recs
  count) — it explicitly does NOT capture the traded ticker's own composite score or
  conviction label. Not usable for this purpose by design, not by omission.
- `analyst_coverage.composite_score_at_save` is captured on a **research save**
  event, unrelated to any specific trade.
- `investor_mirror.conviction_alignment()` (`investor_mirror.py:316`) — the existing
  "Conviction Alignment" section one card up — measures **today's** composite score
  against **today's** portfolio weight. It is a current-state snapshot, not a
  historical record of what the score was at any past entry.
**Correction: O5 does not attempt to reconstruct "conviction at buy time."** Instead
it uses the one thing that IS reliably recorded for every historical trade — the
**dollar size actually committed** (`shares × buy_price`) — as the sizing axis
directly, and asks the more honest question this data actually supports: *"did your
bigger bets, in practice, do better, worse, or about the same as your smaller ones?"*
If sizing tracked genuine conviction, bigger bets should show better realized
outcomes; if the tiers look the same, that is itself the "flat sizing" finding the
roadmap named — arrived at without needing the unavailable conviction join.

**2. Dollar size is not normalized for portfolio growth over time, and the render
must say so plainly.** No point-in-time total-portfolio-value history exists before
`daily_snapshots` (added 2026-06-09, per-ticker only, not a portfolio-total table
either) to compute "% of portfolio at the time of this specific buy." A $5,000
position bought early in the account's history may have been a much bigger relative
commitment than $5,000 today. **Correction: bucket by raw dollar size in terciles of
the user's OWN historical distribution** (adapts to whatever range the account's
trade history actually spans, unlike a fixed dollar cutoff) **and render an explicit
caveat** that this is absolute, not portfolio-relative, sizing — the same "directional,
not precise" honesty already used for Factor Tilt and Blast Radius elsewhere in this
app.

---

## What already exists (reused, not rebuilt)

| Piece | Where | Status |
|---|---|---|
| FIFO closed-lot builder | `investor_mirror.build_closed_lots()` (`investor_mirror.py:50`) | Shipped. Reused verbatim — same `_mi_lots` session-cached result the Behavioral Biases cards already use (`app.py:28836-28839`). Each row already carries `shares`, `buy_price`, `pnl_pct` — everything O5 needs, no new fields. |
| Share-weighted average helper | `investor_mirror._weighted_avg()` (`investor_mirror.py:41`) | Shipped, private within the module. Reused directly. |
| 🪞 Investor Mirror page + Sections 1/2 | `app.py:28606-28949` | Shipped. O5 adds a new **Section 3**, below the existing Behavioral Biases grid — does not renumber or restructure Sections 1/2. |

## What's genuinely new

1. **One new pure-Python function**, `investor_mirror.sizing_alpha(closed_lots, min_n)`:
   - Filter to lots with valid `shares`, `buy_price`, `pnl_pct` (dropna, same pattern
     as the module's other functions).
   - Compute each lot's **fragment dollar size** = `shares × buy_price` — the
     FIFO-matched fragment's own committed dollars, consistent with this module's
     existing atomic unit (a single original BUY split across multiple SELLs already
     produces multiple fragment rows in every other Investor Mirror metric; O5 keeps
     that same convention rather than inventing a different unit just for this
     feature — noted explicitly as a known simplification, see Non-goals).
   - Split into **terciles** by fragment dollar size (`pd.qcut` or an equivalent
     rank-based split — adapts to the account's own range, no fixed dollar cutoff).
   - Requires each tercile to have ≥ `INVESTOR_MIRROR_MIN_CLOSED_LOTS` lots (same
     floor already used per-group elsewhere in this module — total lots required ≥
     3 × that floor for a valid 3-way split).
   - Returns each tercile's share-weighted average `pnl_pct`, lot count, and the
     dollar range spanned (min/max fragment size in that tercile, for the caption).
2. **Section 3 — "📏 Sizing Alpha"** on 🪞 Investor Mirror, full-width below the
   Behavioral Biases grid, reusing the same `_mi_lots` (recomputed via the identical
   session-state cache key already used by Section 2 — no new computation, no new
   API cost).

**Explicitly NOT built:** no join to `recommendations`/`decision_context` for a
conviction proxy, no portfolio-relative (%-of-book) sizing metric, no change to
`build_closed_lots()` or any existing Investor Mirror function, no LLM call, no
cache table (same live-computed pattern as the rest of this page).

---

## Design principles (non-negotiable, carried from v1/v2)

1. **Strictly additive.** No gate, no score, no change to any existing section.
2. **Never fabricates a "conviction" data point that doesn't exist.** The entire
   redesign above exists to honor this — dollar size (real, recorded) replaces
   conviction (unrecorded, would require an unreliable join) as the axis.
3. **Explicit caveat on the one real limitation.** The render must plainly state that
   sizing is shown in absolute dollars, not adjusted for portfolio growth over the
   account's history — this is the honesty bar every "directional, not precise"
   metric in this app already meets (Factor Tilt, Blast Radius).
4. **Descriptive, not prescriptive.** A gap between Small/Medium/Large tercile
   outcomes is evidence to reflect on, not a rule to size up next time — some large
   positions are deliberate high-conviction bets that didn't work out, some small
   positions are toe-in-the-water buys that happened to run; a handful of trades
   don't prove a durable skill either way.
5. **Same gating floor as its neighbors.** Reuses `INVESTOR_MIRROR_MIN_CLOSED_LOTS` —
   no new, weaker floor invented to force a result to appear sooner.
6. **Graceful degradation.** Returns `None` (→ an "insufficient data" caption
   matching the existing cards' pattern) when total valid lots can't support a
   3-way split at the required floor — never raises, never forces a result.

## Non-goals

- Does not reconstruct or estimate "conviction at the time of purchase" from any
  join — ruled out during HEAD audit as unreliable/low-coverage.
- Does not normalize dollar size by portfolio value at the time of each trade (no
  point-in-time total-portfolio-value history exists to do this honestly).
- Does not treat a FIFO-matched fragment differently from a whole, unsplit position —
  a large original buy later sold in several smaller pieces will appear as several
  smaller-dollar fragments, each carrying the SAME realized pnl_pct. This is a known
  simplification consistent with how every other Investor Mirror metric already
  treats the fragment as its atomic unit (`win_loss_closure_ratio`'s own docstring
  makes the identical trade-off for sells spanning multiple buy lots).
- Does not touch `conviction_alignment()`, `disposition_effect()`,
  `win_loss_closure_ratio()`, or `breakeven_anchoring()`.
