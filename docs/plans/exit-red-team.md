# D2 — Exit Red-Team ("Challenge This Exit") — Design Plan

**Date:** 2026-07-24
**Author:** Ajay Kumar
**Analysis model:** Claude Opus 4.8
**Status:** SHIP (revised after Opus design review — FIX-FIRST round resolved). Ready for
implementation.

> **Opus design review (round 1): FIX-FIRST** — 2 blocking findings, both fixed in this
> revision: **(B1)** the plan targeted the wrong renderer — deterioration TRIM/EXIT cards
> render through `_render_act_card` (`app.py:6299`), gated on `_db_item["kind"]`, **not**
> `_render_review_card` (`app.py:6717`), which only ever sees WATCH tier; the `_trim_tkr`
> reuse was a red herring (deterioration items always carry a real ticker). **(B2)** the
> Bear's "deterioration signals" are not structured on the card (only baked into the `why`
> display string), so `build_exit_corpus` must be passed the `exit_advisor.assess_holding`
> payload explicitly or the Bear would fabricate. Also folded in: `_format_corpus` must be
> extended to *render* the new keys (else silently dropped); the Bull's Round-1 exit prompt
> must forbid sunk-cost reasoning; all 4 open questions resolved (below). Posture/additivity
> ruled clean.

> **One-line spec:** Add a `⚔️ Challenge This Exit` button to deterioration
> **TRIM/EXIT** cards that runs the *existing* 5-Haiku Bull-vs-Bear debate machinery in
> `debate_type="exit"` mode — a Bull agent defends *continuing to hold* (anchored on the
> original buy thesis) against a Bear agent that argues the *already-fired exit signal*.
> It is a **second opinion before you act**; it never suppresses the card, never changes
> the deterioration tier, never modifies the composite score.

> **Roadmap context:** Priority 1 of [agentic-intelligence-roadmap-v2.md](agentic-intelligence-roadmap-v2.md);
> also the long-parked "Phase 2" of [multi-agent-debate.md](multi-agent-debate.md). This
> plan supersedes that doc's Surface-2 sketch with the verified, build-ready design.

---

## Why this is priority 1

It closes the **single most dangerous asymmetry in the app**: we red-team your reasons to
*own* a position continuously (P1 Thesis Red Team, the deterioration tiers themselves),
but we have never adversarially challenged your reasons to *sell*. Panic and premature
selling destroy more portfolios than bad entries — and a TRIM/EXIT card is exactly the
high-stakes, emotionally-loaded moment where a structured second opinion is worth most.
And per the asymmetry rule (roadmap v2), protecting against a bad *sell* is defense, which
we prioritize.

It is also the **cheapest high-value item on either roadmap**, because most of it already
exists (below).

---

## What already exists (verified against HEAD 2026-07-24)

| Piece | Where | State |
|---|---|---|
| 5-Haiku debate runner | `debate_agent.run_debate(corpus, debate_type, api_key, model)` | Shipped. Already takes `debate_type`; returns `{transcript, verdict, key_dispute, bull_case_score, bear_case_score, grounded, partial, error}`. **Reused as-is except round prompts (below).** |
| Corpus→prompt formatter | `debate_agent._format_corpus(corpus, debate_type)` | Shipped. **Already branches** `debate_type` for the context line ("Hold-vs-exit decision"). Needs exit-specific evidence fields rendered. |
| Exit corpus builder | `debate_agent.build_exit_corpus(...)` | **Stub** (returns only `{ticker, debate_type:"exit"}`, `debate_agent.py:247-249`). **This plan fills the body AND revises the signature** to carry the deterioration payload (see Spec 1 / B2). |
| Day-cache table | `debate_cache` (has `debate_type text 'entry'|'exit'` column) | Shipped. `db.load_debate_cache(ticker, debate_type, date)` (`db.py:2567`) / `save_debate_cache(..., debate_type=...)` (`db.py:2540`) already parameterized. **Zero DDL — `debate_type="exit"` works today.** |
| **Deterioration TRIM/EXIT card renderer** | **`_render_act_card(_db_item, in_act=False)` (`app.py:6299`)** — dispatched by `_render_defensive_card` (`app.py:6989`) for act-bucket items | Shipped. Items built in `daily_briefing._act_today` with `kind ∈ {"deterioration_trim","deterioration_exit"}` (`daily_briefing.py:1250-1282`), always a real `ticker`. Button hooks here (button row `app.py:6376-6383`, free 3rd column `_act_cols[2]`). |
| Entry-debate button pattern to mirror | `app.py:5841-5926` (cache-hit render, session ceiling, spinner, never-cache-failure) | Shipped. The exit button mirrors this exactly. |
| Bull's thesis anchor | `user_thesis` column on trades (`db.py:1002`, populated on BUY only — Bull anchor present only when a thesis was logged) | Shipped. Plan handles the empty case. |
| Deterioration signal payload (the Bear's ammunition) | `exit_advisor.assess_holding()` → `deterioration_signals()` (tier, `dd_from_peak_pct`, `rel_strength`, `below_ma_count`, `trim_floor`/`exit_floor`, `sma`) (`exit_advisor.py:318-336`) | Shipped, but **NOT present on the rendered card item** (only baked into the `why` string) — must be passed into `build_exit_corpus` (B2). |

## What's genuinely new (the whole build)

1. **Fill `build_exit_corpus`** with exit-relevant evidence (Spec 1) — **revised signature
   carries the `assess_holding` deterioration payload** so the Bear cites real signals (B2).
2. **Extend `_format_corpus` to RENDER the new exit keys** (`debate_agent.py:63-106`
   silently drops any key it doesn't explicitly handle — so filling the corpus without
   this step means the fields never reach the LLM).
3. **Branch the 4 round *user-prompts* in `run_debate` on `debate_type`** — currently
   entry-worded ("case FOR entering {ticker}", `debate_agent.py:291` etc.). The *system*
   prompts (`_BULL_SYSTEM`/`_BEAR_SYSTEM`, "for/against the position") are reusable as-is;
   only the round instructions need an exit variant. **Round-1 Bull exit prompt must
   explicitly forbid sunk-cost reasoning** (see Spec 2).
4. **Wire the `⚔️ Challenge This Exit` button** into `_render_act_card` (`app.py:6299`),
   gated on `_db_item.get("kind") in ("deterioration_trim","deterioration_exit")`.
5. **A framing note + verdict rendering** that makes the "second opinion, signal stands"
   posture unmistakable.

No new table, no new constant, no new external fetch, no change to the composite score or
any gate.

---

## Design principles (non-negotiable)

1. **Strictly additive — the exit signal STANDS.** A `bull_wins` verdict does **not**
   suppress the card, cancel the TRIM/EXIT, downgrade the deterioration tier, or feed any
   score. The card and its recommendation render identically whether or not a debate was
   run. The debate is deliberation support, not a gate. (§2A: the app decides; this is a
   *second opinion the user weighs*, not a competing decision-maker.)
2. **Never fabricates.** The Bear cites the *actual* deterioration signals that fired; the
   Bull cites the *actual* thesis text + current data. If evidence is too thin to form a
   corpus (e.g. no thesis, sparse bundle), degrade — show the button only when there's
   enough to debate, else omit it silently (mirror the entry button's guard).
3. **Cost-bounded.** Reuses the shared per-session debate ceiling
   (`DEBATE_SESSION_CEILING=3`) and the day-cache; a failed/empty run is never cached and
   never consumes a session slot (mirror `app.py:5910-5925`).
4. **Calm, not churny.** Scoped to TRIM/EXIT only — never WATCH (low-conviction; a formal
   debate there would be noise, per multi-agent-debate.md).

---

## Spec 1 — `build_exit_corpus` evidence fields

**Revised signature (B2):** add a `deterioration_payload` param carrying the ticker's
`exit_advisor.assess_holding()` / `deterioration_signals()` output (looked up by ticker
from the brief's already-computed deterioration list, or recomputed via `assess_holding`
inside the builder). Without it the Bear has no real ammunition.

```
build_exit_corpus(ticker, port_df_row, held_data_bundle, erosion_cache_row,
                  trade_row, deterioration_payload) -> dict
```

Each field individually `try/except`-ed and excluded silently if unavailable (mirror
`build_entry_corpus`). All are already computed on the Home render — this assembles, it
does not fetch.

| Corpus key | Source | Purpose |
|---|---|---|
| `ticker`, `debate_type:"exit"` | args | identity |
| `current_price` | `held_data_bundle` history Close | context |
| `unrealized_pnl_pct` | already on the act-card item (`_db_item["pnl_pct"]`, `app.py:6311`) | the emotional stake — the number the user is reacting to. **Include, with anti-sunk-cost guard in the Bull prompt (Spec 2).** |
| `deterioration_tier` | `deterioration_payload` tier (`TRIM`/`EXIT`) — **also directly available as `_db_item["kind"]`** | what the Bear defends |
| `deterioration_signals` | `deterioration_payload`: `dd_from_peak_pct`, `rel_strength`, `below_ma_count`, `trim_floor`/`exit_floor`, `sma` (`exit_advisor.py:318-336`) | the Bear's factual ammunition — **cite, never invent (B2)** |
| `thesis_erosion_score` | `erosion_cache_row` (0–100) if present | corroborating pressure |
| `composite_score`, `composite_label` | `port_df_row` current score | current engine read |
| `rs_vs_spy_20d_pp` | `exit_advisor.compute_relative_strength()` (reused) | is it lagging the market? |
| `momentum_5d_pct`, `momentum_20d_pct` | bundle history | trajectory |
| `days_held` | `trade_row.traded_at` → today | holding-period context |
| `stop_distance_pct` | current price vs protective stop, if available | how close to the mechanical stop |
| `sector` | `port_df_row` / bundle info | context |
| `user_thesis` | `trade_row.user_thesis` (BUY-logged only) | **the Bull's Round-1 anchor** |

**`_format_corpus` extension (required — see build item 2):** add explicit rendering for
each new key above; keys it doesn't handle are silently dropped (`debate_agent.py:63-106`).
When `user_thesis` is present, render it as a labeled block ("Original buy thesis: …") so
the Bull can anchor on it and the Bear is instructed to argue against *both* the thesis and
the data.

## Spec 2 — exit round-prompt branching (in `run_debate`)

Keep `_BULL_SYSTEM`/`_BEAR_SYSTEM` as-is. Branch the four round *user-prompts* on
`debate_type`:

| Round | Entry (existing) | Exit (new) |
|---|---|---|
| 1 Bull | "strongest case FOR entering {ticker}" | "strongest case for **CONTINUING TO HOLD** {ticker} despite the exit signal — anchor on the original thesis if supplied. **Do NOT argue to hold merely because the position is underwater; argue only from the thesis and current data.**" |
| 2 Bear | "case AGAINST this position" | "the exit signal has fired — make the strongest case for **EXITING NOW**, citing the specific deterioration signals in evidence. **This is closing an existing long, not opening a short.**" |
| 3 Bull | "rebut Bear's objection" | "rebut the exit case — is the deterioration temporary/noise, or does it break the thesis?" |
| 4 Bear | "one remaining concern" | "the ONE reason the exit should still stand despite the Bull's defense" |

The **anti-sunk-cost instruction in Round 1** is a decision-quality safeguard, not
cosmetic: a Bull that argues "hold because we're down X%" is the exact loss-aversion
fallacy this app exists to counter. The **Round-2 "closing a long, not shorting" note**
tidies the reused `_BEAR_SYSTEM` short-seller persona (non-blocking cosmetic per review).

Judge prompt (`_JUDGE_SYSTEM`) is **reused unchanged** — `bull_case_score` vs
`bear_case_score` + `DEBATE_WIN_MARGIN` already produce `bull_wins`/`bear_wins`/`contested`;
the semantics map cleanly (bull_wins = the hold is defensible; bear_wins = the exit is
well-supported; contested = genuine uncertainty).

## Spec 3 — button surface + gating (re-targeted per B1)

- **Placement:** inside **`_render_act_card` (`app.py:6299`)**, in the existing button row
  (`app.py:6376-6383`), using the free third column `_act_cols[2]` beside the `▶ Analyze`
  button.
- **Subject ticker:** `_db_item["ticker"]` — deterioration items always carry a real
  ticker (`daily_briefing.py:1260`); no `trim_ticker`/`ticker=None` handling needed (that
  was the PROTECTIVE_TRIM red herring).
- **Gate:** show **only** when `_db_item.get("kind") in ("deterioration_trim",
  "deterioration_exit")`. **Do NOT** substring-match `"EXIT"` on `_db_item["action"]` —
  the EXIT card's action string is `"REDUCE — Deterioration Exit"` (`daily_briefing.py:1262`)
  and TRIM/other strings would false-match. Gate on `kind`, which is authoritative.
  This gate also excludes PROTECTIVE_TRIM (different `kind`/schema) for free — see Q2.
- **Key:** `f"debate_exit_{_db_ticker}"`.
- **Data assembly inside `_render_act_card`:** the function currently receives only
  `_db_item`. The build must assemble `port_df_row` / `held_data_bundle` / `trade_row` /
  `deterioration_payload` from session caches (`_port_df_enriched`, the held-data bundle,
  `trades_df`, and the brief's deterioration list) by ticker before calling
  `build_exit_corpus`.
- **Cache-hit / run / ceiling / never-cache-failure:** mirror `app.py:5846-5926` exactly,
  with `debate_type="exit"` and the shared `_debate_runs_this_session` counter (Q1).

## Spec 4 — rendering

Mirror the entry debate's verdict block (verdict chip, Bull/Bear score metrics, key
dispute, `grounded:false` amber note, transcript expander), with one addition at the top:

> **Framing note:** *"This debate challenges the exit signal — it does not change it. The
> {tier} recommendation stands; this is a structured second opinion before you act."*

---

## Resolved design questions (Opus rulings)

1. **Session ceiling: shared.** Reuse the same `_debate_runs_this_session` counter and
   `DEBATE_SESSION_CEILING=3` across entry+exit, no bump. It's a second opinion, not a
   gate; a disabled button when the pool is spent is acceptable (mirrors entry at
   `app.py:5886`).
2. **PROTECTIVE_TRIM: excluded** — the trim reason there is book-level risk, not
   single-name deterioration, so a "defend the thesis" debate is a category mismatch. The
   `kind`-based gate (Spec 3) excludes it for free.
3. **`unrealized_pnl_pct`: include, with the Round-1 anti-sunk-cost instruction** (Spec 2).
   P&L is legitimate context (the Bear can cite "down X% and still deteriorating"), but the
   Bull is explicitly barred from arguing "hold because we're underwater."
4. **P1 thesis-erosion coupling: flag-only, out of scope.** P1 Phase 2 isn't shipped;
   noting the future coupling so we don't later design them into a contradiction.

## Non-goals

- Does not suppress, reorder, or alter any deterioration card or tier.
- Not shown on WATCH cards, the Daily Brief, or the portfolio overview.
- No AI Insights debate-log surface (that's the separate, later Phase 3 of the debate
  feature).
