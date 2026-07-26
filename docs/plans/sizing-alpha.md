# O5 — Sizing Alpha — Design Plan

**Date:** 2026-07-26
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** SHIP (revised after Opus design review — FIX-FIRST round resolved). Ready
for implementation.

> **Opus design review (round 1): FIX-FIRST** — 2 blocking findings, fixed in this
> revision. The conviction→dollar-size pivot itself was confirmed correct and
> well-grounded (all HEAD-audit claims verified against source). **Blocking #1 —
> wrong unit of analysis:** bucketing by FIFO-matched SELL FRAGMENT (as the module's
> other cards do) is self-defeating specifically for a dollar-sizing feature, and the
> bias runs in one damaging direction: a single large, successful bet that gets
> scaled out in pieces (exactly the trim discipline this app already encourages) gets
> shredded into several small-dollar fragments, artificially inflating the "Small"
> tercile with pieces of what was actually one large winning bet — pushing the result
> toward a false "flat sizing" conclusion. **Fixed: size by the ORIGINATING BUY LOT**
> (group closed-lot rows by `(ticker, buy_date, buy_price)`, one dollar-size and one
> weighted outcome per group), not the sell fragment. **Blocking #2 — unspecified tie
> behavior:** `pd.qcut` on a series with many identical values (a common real
> pattern — round-dollar buys) either raises or, with `duplicates="drop"`, silently
> collapses to fewer than 3 buckets while still being presented as a 3-way
> comparison. **Fixed: rank-based tie-robust split** (`pd.qcut(series.rank(method="first"), 3, ...)`),
> with an explicit guard returning `None` (→ the existing "insufficient data"
> caption) when fewer than 3 distinct dollar values exist. 3 non-blocking
> corrections also folded in below (dollar-weighting instead of raw shares;
> re-confirmed floor after the unit change; the "absolute, not portfolio-relative"
> caveat is now an explicit pre-ship-review checklist item, not just prose).

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
   - **Group by the ORIGINATING BUY LOT, not the sell fragment** — `groupby(["ticker", "buy_date", "buy_price"])`
     over the closed-lot rows. For each group: `dollar_size = buy_price × Σ(shares)`
     across its fragments; `outcome_pnl_pct` = the dollar-weighted average `pnl_pct`
     across its fragments (weight = each fragment's own `shares × buy_price`, i.e.
     dollar-weighted, not share-weighted — see below). **This fixes the round-1
     blocking finding**: bucketing at the sell-fragment level would shred a single
     large, later-scaled-out winning bet into several small-dollar fragments,
     systematically inflating the "Small" tercile with pieces of large winners and
     biasing the whole comparison toward a false "flat sizing" conclusion. Grouping
     by the originating buy lot restores one dollar-size + one outcome per actual
     sizing decision.
   - Split into **terciles** by buy-lot dollar size using a **tie-robust rank split**:
     `pd.qcut(dollar_size.rank(method="first"), 3, labels=["Small","Medium","Large"])`
     — guarantees exactly 3 equal-count groups even when many buy lots share an
     identical dollar amount (a common real pattern, e.g. round-number buys), which
     plain `pd.qcut` would either raise on or silently collapse via `duplicates="drop"`.
     **Explicit guard:** if fewer than 3 distinct dollar values exist across all
     qualifying buy lots, return `None` (→ the existing "insufficient data" caption)
     rather than force a degenerate split. **This fixes the round-1 blocking finding**
     on unspecified tie behavior.
   - Requires each tercile to have ≥ `INVESTOR_MIRROR_MIN_CLOSED_LOTS` buy lots (same
     floor already used per-group elsewhere in this module; note the population is
     now buy lots, not fragments — a smaller, more honest count post-fix, see the
     re-confirmed-floor note below).
   - Returns each tercile's **dollar-weighted** average `outcome_pnl_pct` (weight =
     each buy lot's own `dollar_size` — chosen over share-weighting because raw share
     counts aren't comparable across tickers at very different prices, e.g. 1000
     shares of a $2 stock vs. 10 shares of a $500 stock; dollar-weighting is the
     coherent choice for a feature whose entire axis is dollar size), lot count, and
     the dollar range spanned per tercile (min/max — noting in the render that
     adjacent tercile ranges may occasionally overlap or coincide under the
     rank-based tie split, a cosmetic edge case, not a data error).
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
3. **Explicit caveat on the one real limitation — verify it actually renders at
   pre-ship review, not just appears in this plan.** The render must plainly state
   that sizing is shown in absolute dollars, not adjusted for portfolio growth over
   the account's history — this is the honesty bar every "directional, not precise"
   metric in this app already meets (Factor Tilt, Blast Radius). Per design review,
   this is the one real honesty gate in this feature and is easy to drop during
   implementation — pre-ship review must confirm the caption text is present, not
   just planned.
4. **Descriptive, not prescriptive.** A gap between Small/Medium/Large tercile
   outcomes is evidence to reflect on, not a rule to size up next time — some large
   positions are deliberate high-conviction bets that didn't work out, some small
   positions are toe-in-the-water buys that happened to run; a handful of trades
   don't prove a durable skill either way.
5. **Same gating floor as its neighbors — re-confirmed after the unit-of-analysis
   fix.** Reuses `INVESTOR_MIRROR_MIN_CLOSED_LOTS` (no new constant invented, which
   would itself trip CLAUDE.md's Opus-review requirement for a `constants.py`
   change). The population this floor now applies to is **buy lots**, not sell
   fragments — a smaller, more honest count than a fragment-level count would have
   been. This means the section may stay dark (insufficient data) for longer on a
   modest trade history than a fragment-level count would suggest — accepted as
   correct per design review: graceful degradation is preferable to a metric built
   on a systematically biased unit.
6. **Graceful degradation.** Returns `None` (→ an "insufficient data" caption
   matching the existing cards' pattern) when total valid buy lots can't support a
   3-way split at the required floor, or when fewer than 3 distinct dollar sizes
   exist for the tercile split — never raises, never forces a result.

## Non-goals

- Does not reconstruct or estimate "conviction at the time of purchase" from any
  join — ruled out during HEAD audit as unreliable/low-coverage.
- Does not normalize dollar size by portfolio value at the time of each trade (no
  point-in-time total-portfolio-value history exists to do this honestly).
- Does not use the sell-fragment as its atomic unit (unlike every other Investor
  Mirror metric) — deliberately groups by the originating buy lot instead, per the
  round-1 design-review fix, since the sizing question specifically requires one
  dollar-size per actual sizing decision. Two residual, accepted simplifications
  remain: **(1)** a buy lot that is only PARTIALLY sold by the data's end sizes and
  scores only its realized portion (the still-open remainder isn't counted — no
  unrealized-P&L estimate is introduced); **(2)** two separate BUY trades on the same
  ticker, same date, at an identical price will merge into one group under the
  `(ticker, buy_date, buy_price)` key — an accepted, rare edge case, not a
  data-integrity issue (their combined dollars and outcome are still both real).
- Does not touch `conviction_alignment()`, `disposition_effect()`,
  `win_loss_closure_ratio()`, or `breakeven_anchoring()`.
