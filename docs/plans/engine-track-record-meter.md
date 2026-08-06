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

### Phase 2 — DESIGNED 2026-08-05 (planner/Opus design pass), awaiting mock approval before code

**Design verdict: PROCEED, no blocker.** Full spec below; design pass ID: `docs/plans/next-evolution-2026-08-05.md` follow-on. The originally-worried-about "acted counterfactual" trade-log join is NOT needed for the primary metric — deferred to an optional Phase 2b instead.

**Core formula (sign-flipped mirror of BUY-side alpha):**
```
protect_alpha_pct = spy_return_pct(signal_date → today) − name_return_pct(signal_date → today)
```
Anchor = `exit_signals.signal_date` / `price_at_signal` (already stored — no new fetch). **Positive** = the flagged name underperformed SPY after the warning ⇒ the caution was right. Reuses `_spy_return_pct()` verbatim. Measures "when the App warned, was it right?" — independent of whether the user acted, so it inherits `exit_signals`' **complete cron ROW coverage** (unlike BUY-side's visit-dependent `recommendations` log — one row per flagged ticker per day, regardless of whether the app was opened). **Correction (2026-08-05): row coverage and price coverage are DISTINCT claims.** The Defense facet's first live run surfaced a real bug — the interactive app's capture path had never carried `price_at_signal` (or `dd_from_peak_pct`/`below_ma_count`/`rel_strength`) forward at all, so every app-written row was price-NULL, and a same-day app write could clobber a good price cron had already captured. Fixed same day (`db.save_exit_signals_batch()` coalesce-on-write + the two capture-path field drops + a RISK_OFF price gap) — see `docs/shipped-log.md` and `docs/architecture.md` §6.21. The fix is forward-only; historical NULL rows stay NULL, so the Defense facet's sample will accrue slowly from the fix date forward, not retroactively.

**Critical dedup invariant (do not skip):** cron writes one row per day a name stays flagged — a 15-day EXIT episode is 15 rows. Averaging per-row double-counts and biases toward whichever name stayed flagged longest. **Must collapse to one row per distinct ticker** (earliest mature signal, highest-severity type reached) before averaging — the exact class of inflation `distinct_missed()` already guards against on the BUY side.

**Decisions LOCKED 2026-08-05 (with user):**
1. **Signal scope:** ✅ **EXIT + TRIM only.** WATCH excluded (awareness, not a call to act — mirrors excluding `buy_candidate`). RISK_OFF excluded (portfolio-wide macro call, not per-ticker — grading it per-ticker-vs-SPY would conflate two different objects).
2. **Window:** ✅ **Open-horizon** (signal date → today), maturity floor = reuse `REC_SCORE_MIN_DAYS` (=5) — symmetric with BUY-side, no new OHLC data needed.
3. **Sample-gate constants:** ✅ `PROTECT_TRACK_MIN_CALLS = 8`, `PROTECT_TRACK_FIRM_CALLS = 15` — same values as `ENGINE_TRACK_*` for cognitive consistency. **Flag:** distinct flagged tickers may accrue slower than buy calls; the card may sit in "Building" longer than the BUY facet did.
4. **Acted-dodge secondary line:** ✅ **Deferred to Phase 2b.** Ship the validation headline alone. (Would need an `exit_signals`→SELL join and risks reading as a duplicate of Behavioral Fingerprint's "Exit Signal Response," which measures a DIFFERENT dimension — your behavior, not the engine's call accuracy.)

**Placement:** fold into the EXISTING 🎯 Engine Track Record card as a second facet (⚔️ Offense = BUY alpha, already shipped; 🛡️ Defense = protective alpha, new) — NOT a new sibling card. Passes the "does this decrease decision load?" test; avoids competing "can I trust the engine?" surfaces. Each facet independently sample-gated. **No link-through for the Defense facet in v1** — no existing detail page to send it to (must NOT point to Behavioral Fingerprint; different dimension, would mislead).

**Honest-render rules (carried forward from F-229's 3 review rounds):**
- Never dress an absent/insufficient-data result as a negative — `exit_signals.load_exit_signals()` returns an **empty DataFrame, not `None`**, on DB failure, so the card cannot distinguish "offline" from "no deterioration yet." Both must render as neutral "Building" — caption must NEVER claim "no deterioration in your portfolio" (would be a false all-clear during an outage).
- Flat/negative real result → honest amber ("flagged names have mostly recovered — protective calls ran early"), never dressed green, never suppressed.
- The sample count driving the band must describe the SAME distinct-ticker population the alpha is averaged over (the exact F-229 Phase-1 bug — do not repeat it).
- Methodology caption must frame this as "what flagged names did *after* the warning" (forward calibration) — not a claim the engine predicted the initial weakness (selection-bias honesty).

**New pure-logic module (not `recommendations_history.py` — different substrate/semantics):** `stock_analyzer/protective_track_record.py` — `compute_protective_outcomes()`, `collapse_by_ticker()` (the dedup step), `protective_headline()` (mirrors `engine_trust_headline()`).

**Coordination:** does NOT collide with Behavioral Fingerprint's "Exit Signal Response" (`behavioral_fingerprint.py:247`) — that measures the RESPONSE dimension (did you act, how fast); this measures the ACCURACY dimension (was the call right). Dedupe by dimension per `feedback_single_surface_priority`, not by ticker.

**Mock reviewed and APPROVED 2026-08-05** — `docs/mockups/engine-track-record-phase2.html` (one round of correction: the Recommendations History link-through was mispositioned under Defense in the first draft, moved to Offense; Defense's "no link" state given precise placeholder copy distinguishing the Phase 2b acted-dodge metric from a hypothetical — never actually committed — Defense detail page). Design + visual are both locked. Built and shipped same day.

**Post-ship fixes, same day (2026-08-05):** live validation surfaced 3 rounds of real bugs, each fixed with its own design-pass-where-warranted + Opus review:
1. NaN leak — a legacy row's NULL price read back from pandas as `float('nan')`, not `None`, poisoning the aggregate. Fixed by reusing the existing `_f()` NaN-safe coercion helper + a defensive filter in `protective_headline()`.
2. Root-cause write-path bugs — traced past the NaN symptom to `exit_signals` price capture being genuinely broken (not a bounded historical gap): the interactive app path never carried `price`/`dd_from_peak_pct`/`below_ma_count`/`rel_strength` forward, and a same-day app write could clobber a good price cron had already captured (no coalesce on the upsert). Fixed forward-only — see `docs/shipped-log.md` and `docs/architecture.md` §6.21.
3. **Anchor-selection fix — `collapse_by_ticker()`'s earliest-row rule interacted badly with bug 2's historical NULLs.** Since the dedup logic always anchors a ticker to its globally earliest-dated row, any ticker whose earliest row was one of the historically-NULL ones was permanently stuck unpriced, even after later correctly-priced rows existed for it — fixing the write path alone couldn't unstick already-tracked tickers. Fixed by preferring the earliest row with a usable (truthy) `price_at_signal`, falling back to true-earliest only when nothing is priced. `since_date`/maturity now honestly reflects "first reliably measured," not "first flagged." 5 new tests; Opus review SHIP 0 blocking.

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
