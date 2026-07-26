# D3 — Signal Coherence Auditor — Design Plan

**Date:** 2026-07-26
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** SHIP (Opus design review: no blocking findings; 5 non-blocking refinements
folded into this revision below). Ready for implementation.

> **Opus design review: SHIP, 0 blocking.** Verified all code claims in this plan
> against HEAD (signal_reconciliation.py, debate_agent.py, thesis_advisor.py,
> regime_targets.py, db.py, app.py) and confirmed each accurate, including the
> load-bearing but non-obvious fact that `run_debate`'s exit-type Bull argues to
> *continue holding* and Bear argues to *exit* — so `bull_wins` stays bullish-aligned
> in both entry and exit debates; polarity does not flip. 5 non-blocking refinements
> (all incorporated below): (1) state that polarity invariant explicitly so an
> implementer doesn't "fix" it into a bug; (2) define a tiebreak when a ticker has both
> an entry and an exit debate on the same or differing dates; (3) define precisely
> that `_composite_class`'s `unknown` counts as present-but-neutral (can satisfy the
> ≥2 gate, can never itself create a contradiction); (4) tag a stale entry-type debate
> as "buy-time" when it predates the position's most recent BUY, so currency is
> explicit rather than left to date arithmetic; (5) keep always rendering all present
> chips (already the spec) so a 2-vs-1 split is visibly distinct from a 1-vs-1 split.

> **One-line spec:** A new 5th tab, "🧭 Signal Coherence," on 🧩 Intelligence. For every
> held ticker, mechanically joins three of the app's own independent advisory
> surfaces — the composite score's own directional class, the weekly Thesis Red
> Team's erosion status, and the most recent Bull/Bear debate verdict — and surfaces
> only the names where they **disagree**. Pure Python, zero LLM calls, zero new
> fabrication risk: this feature doesn't generate anything, it just diffs data that
> already exists in three different tables/columns.

> **Roadmap context:** Priority 4 of [agentic-intelligence-roadmap-v2.md](agentic-intelligence-roadmap-v2.md)
> (Phase 3, paired with O4).

---

## HEAD audit — one scope correction before this goes to review

The roadmap table's one-line description of D3 named four inputs to reconcile:
"composite / debate verdict / thesis-erosion / regime-fit." Verified against HEAD
(2026-07-26), three of those are real, per-ticker, directional signals. The fourth is
not:

- **`reconcile_signals()`** (`signal_reconciliation.py:62`) is a **momentum-vs-composite**
  conflict resolver built for scanner movers/candidates (its only current call site,
  `app.py:13817`, compares a scanner momentum score against a separately-fetched
  composite for **Market Scanner Top Picks**, not held positions). For an already-held
  ticker, `port_df["Score"]`/`["Signal"]` **is** the composite — there is no separate
  "momentum score" sitting next to it to reconcile against. Reusing this function for
  held names would silently compare a number to itself. **Correction: for held tickers,
  D3 uses the composite's own directional class** (buy/hold/sell), via
  `signal_reconciliation._composite_class()` — already exactly the right primitive,
  just consumed differently.
- **Thesis erosion** is real and per-ticker: `thesis_reviews` (`db.py:1169`), written
  weekly by `cron_runner.py:642` → `thesis_advisor.run_batch_review()`, status ∈
  `{INTACT, WEAKENING, BROKEN}` (`thesis_advisor.py:120-138`). Confirmed real, sparse
  by design (only tickers with a saved thesis, refreshed weekly).
- **Debate verdict** is real and per-ticker: `debate_cache` (`db.py:2592-2647`), verdict
  ∈ `{bull_wins, bear_wins, contested}` (`debate_agent.py:41`), written on-demand by
  either the entry-side "⚔️ Debate" button (`app.py:5894`, run on Grow Today
  candidates — may or may not still be held) or D2's exit-side "⚔️ Challenge This Exit"
  button (`app.py:6457`, run on TRIM/EXIT deterioration cards for currently-held
  names). Confirmed real, sparse by design (only tickers the user chose to debate).
- **Regime-fit** (`regime_targets.regime_position_gap()`, `regime_targets.py:22`) is
  **portfolio-level, not per-ticker** — it returns one beta/cash gap for the whole
  book, not a directional signal on any single name. Its `top_contributors` list does
  name up to 3 tickers, but as *beta-contribution magnitude*, not a bullish/bearish
  direction — there is nothing to agree or disagree with. **Correction: regime-fit is
  dropped from this feature.** Forcing a non-directional, portfolio-wide number into a
  per-ticker "do my signals agree" join would be the exact kind of imprecision the
  HEAD-audit step exists to catch (same lesson as O1's `_rh_enriched` vs
  `_rh_enriched_all` and D1's fabrication-guard gap — verify the primitive actually
  does what the one-liner assumed before designing around it).

**Net effect: this feature needs zero new LLM calls.** All three inputs already exist
as stored/computed data; D3 is a mechanical join-and-diff over them, run live on page
load (no day-cache table needed — cheaper than every roadmap item shipped so far, D1
and O1 included).

---

## What already exists (reused, not rebuilt)

| Piece | Where | Status |
|---|---|---|
| Composite direction classifier | `signal_reconciliation._composite_class(signal, score) -> "buy"\|"hold"\|"sell"\|"unknown"` (`signal_reconciliation.py:38`) | Shipped, private. This plan adds a thin public wrapper (see below) — no logic change. |
| Thesis erosion status | `db.load_thesis_reviews()` → `thesis_reviews` DataFrame, `status` column | Shipped. Read-only reuse; same "latest row per ticker" reduction already done at `app.py:24419-24425`. |
| Debate verdict | `debate_cache` table, `verdict`/`debate_type`/`debate_date` columns | Shipped (D2). Needs one new read function (below) — no schema change. |
| 🧩 Intelligence page + tab scaffold | `app.py:10155-10172` (`_pi_tab_clusters, _pi_tab_risk, _pi_tab_factor, _pi_tab_structural = st.tabs([...])`) | Shipped. This plan adds a 5th tab to the same `st.tabs()` call. |
| Held-position universe | `st.session_state._port_df_enriched` (already the page's load-gate at `app.py:10163-10167`) | Shipped. |

## What's genuinely new

1. **A public composite-direction wrapper** in `signal_reconciliation.py`:
   `classify_composite_direction(signal, score) -> str` — a 2-line function that calls
   the existing `_composite_class()`. (Avoids importing a leading-underscore name
   across modules; zero behavior change.)
2. **One new db read**: `load_debate_verdicts(tickers: list[str]) -> pd.DataFrame` —
   selects `ticker, debate_type, debate_date, verdict, grounded` from `debate_cache`
   where `ticker in (tickers)`, for the held-ticker set (mirrors the existing
   `.in_("ticker", ...)` pattern at `db.py:1510`). Reduced in Python to "most recent row
   per ticker, either debate_type" — same "latest row" idiom already used for thesis
   reviews.
3. **The join + contradiction rule** (pure Python, no LLM, no cache table): for each
   held ticker, gather whichever of the three directional signals are present
   (composite direction is always present when `Score`/`Signal` exist; erosion and
   debate are frequently absent — that's expected, not an error). A ticker qualifies
   for review only when **≥2 signals are present**; among those, only tickers where
   the present signals **don't all point the same direction** get rendered. Full
   agreement produces no card — this keeps the tab a short list of real disagreements,
   never a wall of "everything's fine" noise (the roadmap doc itself flagged this tab
   as "the item most at risk of becoming noise").
4. **A 5th tab**, "🧭 Signal Coherence," on 🧩 Intelligence.

**Explicitly NOT built:** no new LLM call, no new cache table, no change to
`reconcile_signals()`, `_composite_class()`, `thesis_advisor`, `debate_agent`, or
`regime_targets` themselves. No gate, no score, no suppression.

---

## Direction mapping (the actual diff logic)

| Signal | Bullish-aligned | Neutral / unclear | Bearish-aligned |
|---|---|---|---|
| Composite direction | `buy` | `hold` / `unknown`* | `sell` |
| Thesis erosion status | `INTACT` | `WEAKENING` | `BROKEN` |
| Debate verdict (latest, either type) | `bull_wins` | `contested` | `bear_wins` |

A **contradiction** = at least one present signal maps to bullish-aligned AND at least
one present signal maps to bearish-aligned, for the same ticker. Neutral values never
trigger a contradiction on their own (a `hold` composite next to a `WEAKENING` erosion
status is not a disagreement — both are cautious, not opposed) — they only matter as
context shown alongside a genuine bull/bear conflict.

*`_composite_class`'s `unknown` (no signal/score available at all) counts as
**present-but-neutral** for the "≥2 signals present" gate, exactly like `hold` — it
can help a ticker qualify for evaluation, but it can never itself supply the bullish
or bearish half of a contradiction. Composite is realistically almost always present
for a held ticker with a `Score`, so this is a rare edge case, not the common path.

**Verified polarity invariant (do not invert):** traced `run_debate`
(`debate_agent.py:458-509`) — in an **exit**-type debate the Bull argues to *continue
holding* and the Bear argues to *exit now*, the same polarity as an **entry**-type
debate (Bull = bullish case, Bear = bearish case). So `bull_wins` is bullish-aligned
and `bear_wins` is bearish-aligned **in both debate types, without exception**. This is
the single most bug-prone spot in this feature — an implementer inverting exit-debate
polarity "because it's about selling" would silently corrupt every exit-debate row's
classification. Do not invert.

**Debate selection — which row wins when both types exist:** select the most recent
row by `debate_date desc` across both types; **if a ticker has both an entry and an
exit debate on the same date, prefer the exit debate** — it addresses the current
hold decision, whereas an entry debate may predate the purchase and address a
different decision moment. **Currency tagging:** if the selected debate is `entry`
type and its `debate_date` predates the ticker's most recent BUY (`_trade_date_by_ticker`,
already built at `app.py:24398-24408`), label it "buy-time debate" in the render rather
than just showing a bare date — makes the currency gap explicit instead of requiring
the user to do date arithmetic themselves. (The analogous check for thesis erosion —
flagging a review as stale if the thesis text was edited after `reviewed_at` — is not
buildable today: `trades.user_thesis` has no separate edit timestamp, only the
BUY's `traded_at`. Out of scope for this phase; would need a new column.)

Every displayed signal chip shows its own type/date so staleness is visible, not
asserted away (mirrors the existing debate-expander convention already in this app,
e.g. `app.py:5866`), matching `feedback_recommendation_transparency`'s "show basis, not
a bare value" convention even though this surface makes no recommendation to justify.

---

## Spec — render

New tab `"🧭 Signal Coherence"` added to the existing `st.tabs([...])` call at
`app.py:10170-10172`.

- Header + one-line explainer: "Where the app's own signals disagree with each other
  on a name you hold — composite score, thesis review, and debate verdict, side by
  side. Diagnostic only; nothing here gates or recommends."
- If zero held tickers have ≥2 present signals: plain caption — "Not enough overlapping
  signals yet to audit. This fills in as you save theses (⚙️ weekly review) and run
  debates (⚔️ buttons on candidates and deterioration cards)." Always renders; no
  button, no spinner (there's nothing to compute — this is pure data presence, known
  instantly).
- Else, for each ticker with a genuine contradiction: a compact card — ticker, then
  **all** present signals (2 or 3, never fewer than what's actually present, so a
  2-vs-1 split is visibly distinguishable from a 1-vs-1 split) as small labeled chips
  (e.g. "Composite: Buy 72" · "Thesis: 🔴 BROKEN (reviewed 2026-07-20)" · "Debate: 🟢
  Bull wins — exit, 2026-07-18"), with the conflicting ones color-coded (green/red)
  and neutral ones muted gray. A selected debate that is `entry`-type and predates the
  ticker's most recent BUY renders as "Debate: 🟢 Bull wins — **buy-time**, 2026-07-18"
  so the currency gap is explicit. No synthesized prose sentence is generated — the
  chips *are* the finding; templating a "these disagree because…" sentence would
  either restate the obvious or invent a causal story neither signal actually asserts.
- If every ticker with ≥2 present signals is in full agreement: "No disagreements
  found among your signals today" — a legitimate, calm result.

---

## Design principles (non-negotiable, carried from v1/v2)

1. **Strictly additive.** No gate, no score change, no gate gets its numbers from this
   join.
2. **Zero LLM, zero fabrication surface.** This entire feature is a mechanical
   presence-check + direction-map over three tables that already exist. There is
   nothing here to hallucinate.
3. **Graceful degradation is the common case, not an edge case.** Most held tickers
   will have only 1 of 3 signals present (composite alone) — that's normal, not a
   partial failure; they simply don't qualify for the join.
4. **Only render disagreement, never agreement.** Per the roadmap's own noise warning
   for this exact item — a tab that lists "everything's fine" for every ticker would
   be the least calm, least useful surface shipped this cluster.
5. **Show basis, not just a chip.** Every displayed signal carries its own date/type so
   staleness is visible, not asserted away (mirrors the existing debate-expander
   convention already in this app).

## Non-goals

- Does not touch `reconcile_signals()`'s existing scanner-vs-composite use, or any of
  its 4 existing call sites.
- Does not compute or display anything portfolio-level (regime beta/cash gap) — that
  stays on 🔗 Risk Analysis, unchanged.
- Does not backfill or trigger a thesis review or a debate on the user's behalf — it
  only reads whatever already exists.
- Does not rank or score the size of a disagreement; it is a binary present/absent
  finding, not a severity tier.
