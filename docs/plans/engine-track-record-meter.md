# Engine Track Record — Standing Calibration Meter

**Status:** PLANNING — 2026-08-05. Not built. **Awaiting user review of this plan + the mockup at [docs/mockups/engine-track-record-meter.html](../mockups/engine-track-record-meter.html) before any code.**

**Origin:** First zoom-in from [next-evolution-2026-08-05.md](next-evolution-2026-08-05.md) (Brainstorm Pass #2), Lens 2/3 — "the app's own track record as a standing surface."

**Analysis model:** Claude Opus 4.8 (1M context), as session lead. Verified against code at HEAD via two investigations 2026-08-05 (data model + logging completeness; nav + existing surfaces).

---

## The one-line idea

A **standing, always-in-view trust headline** — "Of the App's mature BUY calls, N beat the S&P by X pp on average" — that **surfaces the alpha/hit-rate already computed in `recommendations_history.py`**, so the engine's credibility is visible in the daily flow, not only during a manual self-audit on the 📜 Recommendations History page.

It is **awareness-only**. It never gates, ranks, or tunes anything. The engine stays the sole ranker (hard invariant, carried from `next-evolution-strategy.md` §3 step 10).

---

## What the verification established (facts at HEAD, 2026-08-05)

**The measurement engine already exists — this is a presentation layer, not new math.**
`stock_analyzer/recommendations_history.py` (pure logic, no Streamlit/DB) already computes:
- `compute_outcomes()` — per-rec `outcome_pct`, `spy_return_pct`, and **`alpha_pct = outcome_pct − spy_return_pct`**; per-$1k `outcome_dollars` for missed recs; `days_since`; a maturity gate via `REC_SCORE_MIN_DAYS` (=5, a measurement knob, NOT a gate).
- `summary_stats()` — n_total, n_acted, action_rate, n_wins/n_losses (hit-rate substrate), **avg_acted_alpha / avg_missed_alpha vs SPY**, best/worst.
- `by_verdict()` (Confirmed vs Conflicted), `by_composite_band()`, `engine_trust_by_band()` (action-rate + alpha by band + a plain-English "engine was right / over-trusting" verdict), `signal_flow()`.
- Surfaced today on the **📜 Recommendations History** page (`app.py:23215`), which builds the SPY-by-date series and calls these helpers. Also reused by the monthly cron report (`intelligence_report.py`).

**Data source + its honesty constraints:**
- **BUY-side log = `recommendations` table** (`db.py:179-194`): `ticker, rec_date, rec_type ∈ {new_pick, add_winner, buy_candidate}, surfaced_at, price_at_surface, composite_score, verdict, …`. Written **only on a Home/Brief page render** (`app.py:4821`) — **not by cron.** ⇒ **Coverage is visit-dependent (sparse):** a row exists for a day only if the owner opened Home that ET day.
- `price_at_surface` is permanently NULL for ~419 pre-2026-07-26 `buy_candidate` rows (fixed forward-only in `51b2441`) — historical Conflicted/Hold/Sell alpha is unrecoverable. New-pick rows are unaffected.
- **Protective-side log = `exit_signals` table** (`db.py:2096`): WATCH/TRIM/EXIT/RISK_OFF with `price_at_signal`. **Complete cron coverage** (`cron_runner.py:148`). But **no outcome/alpha rollup helper exists for it** — measuring "did the protective call avoid drawdown?" is new work.

**Placement precedent:** 23 nav pages already exist. The **🧾 Summary page** is explicitly built as read-only pointer-cards that "never [make] a second independent computation of anything another page already owns" (`app.py:9731-9733`), and already hosts a Tier-3 "Alpha vs SPY (Nd)" metric + a "🧭 Elsewhere in DRISHTA" pointer grid. This is the natural, discipline-consistent home.

**Adjacent surfaces that already exist (the meter POINTS to these, doesn't duplicate them):**
- **📊 Predictive Analytics → 🎯 Score Calibration** — avg alpha by score band.
- **🧑‍⚖️ The Judge → 📊 Track record** — grades each *witness's* past calls (not the composite engine).
- **🧠 AI Insights → 📊 Scorecard** — third-party *analyst* accuracy (not the engine).

---

## Design principles (do not violate)

1. **Never recompute — read the helpers.** The headline number is sourced from `summary_stats()`/`by_verdict()` exactly as the 📜 Recommendations History page uses them. If the meter and that page ever disagree, the meter is a bug. (This is the whole point of putting it on the pointer-discipline Summary page.)
2. **Judge on alpha, not raw %** (`feedback_analytics_integrity` rule 3).
3. **Actionable scope for the headline = `new_pick`** (gate-cleared entries). Exclude the awareness-only `buy_candidate` feed — skipping those is the *correct* call, not a miss (`feedback_analytics_integrity` rule 1). `add_winner` may be shown as a separate secondary line, not folded into the entry-quality headline.
4. **Maturity-gated** — only recs ≥ `REC_SCORE_MIN_DAYS` old count (already in the helpers).
5. **Sample-transparent, and honest about coverage.** Always show `n` mature calls + the "since <date>" window. The caption must state the meter measures *"the calls the App surfaced to you"* (not every theoretical call), because the BUY log is visit-dependent. Below a minimum sample, show a **"building history"** empty state — never a confident verdict on a handful of calls (same discipline as Behavioral Fingerprint's sample gate and the Judge's "building history").
6. **Per-$1k, never a portfolio-% counterfactual** if any dollar figure appears (`feedback_analytics_integrity` rule 2).
7. **Awareness-only, backward-looking.** No gating, no ranking, no stock-level forward point-estimate (`next-evolution-strategy.md` §5.8 invariant — satisfied, this is a track record).

---

## Scope

### MVP (Phase 1) — BUY-side engine trust headline
Pure presentation over `recommendations_history.py`. **The headline metric = Confirmed `new_pick` average alpha vs SPY (mature only), with hit-rate and sample.** ("Confirmed" per `project_rec_engine_evaluation`: judge the engine on the Confirmed row's alpha, not raw %.)

The card shows:
- **Headline:** e.g. *"App's BUY calls: +2.3 pp vs S&P on average"* + hit-rate *"11 of 18 beat the S&P"*.
- **Sample + window:** *"18 mature calls since May 28 · calls older than 5 days"*.
- **Plain-English verdict:** sourced from / consistent with `engine_trust_by_band()` (calm tone, e.g. *"Working as intended — acting on Confirmed calls has beaten the benchmark."* / *"Too early to tell."*).
- **Honest coverage caption:** *"Measures the calls the App surfaced to you, not every possible call."*
- **Link-through:** *"See the full breakdown → 📜 Recommendations History"* (and optionally → Predictive Analytics Score Calibration).
- **Empty state:** below min sample → *"Building the App's track record — N more mature calls needed."*

### Phase 2 (deferred, flagged as NEW measurement — not MVP)
**Protective-side track record:** "Did WATCH/TRIM/EXIT calls avoid further drawdown?" Uses the cron-complete `exit_signals` log (better coverage than BUY-side), but needs a **new outcome-rollup** (drawdown-avoided vs a hold counterfactual). New measurement ⇒ its own design pass + Opus review. Named here so it isn't lost; do not build in MVP.

---

## Placement (for review)

**Recommendation: 🧾 Summary page**, as a compact **"🎯 Engine Track Record"** pointer-card — either promoted into the Tier-3 row beside "Alpha vs SPY (Nd)" or added to the "🧭 Elsewhere in DRISHTA" pointer grid (`app.py:9729-9842`). Rationale: it's seen regularly, it obeys the page's read-only pointer discipline, and it links through rather than duplicating.

**Not a new nav page** — 23 already exist; a 24th adds decision-load for a surface that is a synthesis of existing pages.

**🏠 Home (optional, deferred):** a single calm one-liner in Home's synthesis area is possible, but any new Home input **must join the `_home_synth_cache` signature or it ships stale** (`project_home_synth_memoization`). Given Home's density, MVP is Summary-only; Home is a later call.

---

## Constants (policy touch — flag)

Likely one new **display-only** constant: a minimum mature-call count below which the card shows "building history" instead of a verdict (e.g. `ENGINE_TRACK_MIN_CALLS`). This is a *display-policy* threshold, not an investment gate — but it still lives in `stock_analyzer/constants.py` (hard rule #1), must be documented in the architecture constants table (`check_constants_documented.py`), and adding it to `constants.py` trips the commit hook's **mandatory Opus-review citation**. Value to be set with the user. (`REC_SCORE_MIN_DAYS` already handles the *maturity* gate; this is the *sample-count* gate.)

---

## Files this would touch (sketch — not a line-by-line spec yet)

| File | Change |
|---|---|
| `app.py` (🧾 Summary block, ~`9455`–`9842`) | New "🎯 Engine Track Record" pointer-card reading `recommendations_history` outputs; link-through button (reuse the `_pending_page` nav indirection). |
| `stock_analyzer/recommendations_history.py` | Likely **read-only reuse**; add a thin `trust_headline()` convenience wrapper ONLY if the Summary card would otherwise duplicate assembly logic from the Rec History page — preferred over copying. |
| `stock_analyzer/constants.py` | `ENGINE_TRACK_MIN_CALLS` (display-only) if a sample gate is added. |
| `docs/architecture.md` | Constants-table row (if constant added). |
| `docs/requirements.md` | New F-row (new user-facing surface — judgment item, DoD #2). |

**Not touched:** any gate, composite/scoring formula, `ranking.py`, `daily_briefing.py` decision logic. The meter only reads outcomes.

---

## Routing (per CLAUDE.md review economy)

- 🔴 **reviewer (Opus) — REQUIRED before ship.** This is a *new user-facing decision surface* where a wrong/misleading number erodes trust in the entire engine (the review-required category, even though it's display-only). Verify: no recompute-divergence vs Rec History page; alpha-not-raw; `new_pick` scope; sample gate honored; coverage caption present; no leak into any gate/score. If `constants.py` is touched, review is also mechanically required.
- 🔵 **implementer (Sonnet)** — builds the card from the approved spec + mockup.
- 🟢 **doc-writer (Haiku)** — architecture constants row + requirements F-row, after facts are pinned.
- **This design pass** was done by the Opus session lead directly (no separate `planner` call needed — the lead is already Opus).

---

## Decisions (LOCKED 2026-08-05, with user)

1. **Headline metric** — ✅ **Confirmed `new_pick` avg alpha vs SPY + hit-rate.** (`add_winner` NOT folded into the headline; may appear as a separate secondary line at most.)
2. **Placement** — ✅ **🧾 Summary only** (Tier-3 row / pointer grid). **No Home one-liner in MVP** — avoids the `_home_synth_cache` signature dependency entirely.
3. **Scope** — ✅ **BUY-side only for MVP.** Protective-side (`exit_signals` drawdown-avoided) is **Phase 2**, deferred — new measurement + its own Opus review.
4. **Sample gate** — ✅ **Add display-only `ENGINE_TRACK_MIN_CALLS` to `constants.py`.** Accepts the mandatory Opus-review-on-`constants.py` cost. Card states: below floor → "Building"; just above → "Too Early" (softened verdict); comfortably above → full verdict.

## Final build decisions (2026-08-05, confirmed after seeing real data)

The real `by_rec_type()` numbers — **New Position: 148 total / 17 acted / acted α +5.2pp / missed α −1.0pp** — refined decision #1:

- **Headline = ACTED `new_pick` α vs SPY** (+5.2pp today), NOT a blended all-matured average. Blending all 148 matured calls (131 skipped, −1.0pp) collapses the headline to ≈flat and misleadingly understates the engine — you cannot act on 148 names (capital + concentration caps + gates bind). **This SUPERSEDES the earlier "all-matured" lean.** Skipped α (−1.0pp) shown as an honest contrast line.
- **Two display-only constants:** `ENGINE_TRACK_MIN_CALLS = 8` (below → "Building", no verdict) and `ENGINE_TRACK_FIRM_CALLS = 15` (8–14 → "Early read", softened; ≥15 → firm verdict). With 17 acted today, the card shows a firm verdict immediately.
- **Negative case is honest:** at ≥ firm sample, a positive acted α → "WORKING" (green); a flat/negative acted α → a calm caution label (amber), never dressed green.
- **Placement mock:** [engine-track-record-pointer.html](../mockups/engine-track-record-pointer.html) is the current design. The earlier [engine-track-record-meter.html](../mockups/engine-track-record-meter.html) full-card mock is **SUPERSEDED for placement** (kept only as a state-variants reference).

## Implementation spec (for `implementer`)

**New pure helper** in `stock_analyzer/recommendations_history.py` — `engine_trust_headline(recs, spy_by_date, today_et, min_days)` → dict `{acted_alpha, missed_alpha, n_acted_mature, since_date, band}` for the `new_pick` scope only. Built on the EXISTING `compute_outcomes()` + `by_rec_type()` chain (do not reimplement outcome math). `band ∈ {building, early, firm}` derived from `n_acted_mature` vs the two constants. Pure/unit-testable; add a focused test.

**Parity requirement (correctness-critical):** the pointer's acted α / n must MATCH the New Position row on the 📜 Recommendations History page. Load recommendations over the **same all-time range Rec History uses** (NOT the 30-day `load_recommendations` default) and build the SPY-by-date series the same way (`fetch_spy`/cached). If numbers can drift from Rec History, it's a bug.

**Summary card** in the "🧭 Elsewhere in DRISHTA" pointer grid (`app.py` ~9729–9842), matching the existing pointer-card style: hero `+X.X pp vs S&P` + "when you acted on the App's new-position calls" + skipped-α contrast line + `N matured calls · measures calls surfaced to you` caption + link-through to 📜 Recommendations History via the `_pending_page` nav indirection. Band → badge: building (no number, "N more to go"), early (amber, softened), firm+positive (green "WORKING"), firm+flat/negative (amber caution, honest wording). Exclude `buy_candidate` and `add_winner` from the headline.

---

## Distinction from a nearby deferred item (so we don't conflate them)

This is **engine-vs-SPY track record**. It is NOT the deferred **"Research Scorecard Phase 3 — Engine vs Analyst Calibration"** (`analyst-research-accountability.md`), which is a 2×2 engine-vs-*analyst* disagreement matrix gated on `composite_score_at_save ≥ 20 rows`. They're complementary; neither blocks the other.
