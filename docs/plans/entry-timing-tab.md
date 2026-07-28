# Entry Timing tab (Predictive Analytics) — Plan

**Feature:** A 6th tab, "⏱️ Entry Timing," on the existing 📊 Predictive Analytics page. Answers
a narrower question than the page's other 5 tabs: does **technical momentum running ahead of the
composite consensus at the moment a `new_pick` fires** predict a rough first few days — even when
the pick is right over its real weeks-to-months horizon? Distinguishes bad entry timing from
ordinary short-term noise.

**Status: SCOPED, NOT STARTED.** This document is the design; no code has been written.
`constants.py`, `stock_analyzer/predictive_analytics.py`, and `app.py` are all untouched pending
an explicit go-ahead in a follow-up session.

---

## Motivation — the AMD case study

Pulled live from the deployed app's 📜 Recommendations History (2026-07-28): AMD fired as a
`new_pick` **five separate times in two weeks** (07-09, 07-10, 07-14, 07-15, 07-22). Composite
was only **69–71** each time — barely above the `COMPOSITE_BUY` (65) floor, nowhere near Strong
Buy. Momentum was **pegged at 94–100** every time. Every firing shows a **~9–11% loss, alpha −7pp
to −10pp vs SPY** — consistent, not noisy, and the SPY-relative alpha rules out "the market was
just down that day."

That combination — momentum maxed out while composite is only marginally above the buy gate — is
the real signal, not high momentum in isolation. A high-momentum stock with composite 85+
(fundamentals and everything else agreeing) is a different animal from momentum=100 riding on a
composite that just barely cleared 65. This plan calls that gap the **divergence**.

A prior engine-health checkpoint (`project_rec_engine_evaluation` memory, 2026-06-18) had already
flagged, at much thinner sample (n=8), that `new_pick` acted −10.1% vs missed −0.3%, "likely
entry-timing/regime, revisit once n grows" — and at n=14 that watch-item flipped positive. AMD is
a much cleaner, larger echo of the same underlying question, worth turning into a standing report
instead of an ad hoc finding.

---

## What already exists — do not rebuild

The 📊 Predictive Analytics page (`app.py:22305` onward) already has 5 tabs doing adjacent but
distinct work — **this is additive, not a new page**:

| Tab | What it answers |
|---|---|
| 🎯 Score Calibration | Does a higher composite score deliver more alpha, in your history? |
| ⚖️ Decision Quality | Acted vs. missed alpha by verdict |
| 🏷️ Signal Breakdown | By conviction tier / rec type |
| 🌐 Sector Alpha | Which sectors have you actually made money in |
| 🧭 Sentiment Alignment | Does sentiment agreement predict better outcomes |

None of these look at **entry-day technical conditions** (momentum vs. composite) or **horizons
shorter than "rec_date → today."** That's the actual gap Entry Timing fills.

Shared infrastructure this tab reuses as-is, no changes needed:
- `st.session_state["_pac_enriched"]` — the shared dataset all 5 tabs already read
  (`app.py:22348-22409`), built from `recommendations_history.compute_outcomes()`
  (`stock_analyzer/recommendations_history.py:141-246`). Every field the divergence metric needs
  — `ticker`, `rec_date`, `composite_score`, `momentum_score`, `rec_type`, `price_at_surface`,
  `alpha_pct`, `outcome_maturing` — is already on every row. **No new `recommendations` table
  columns required.**
- The page's coverage strip, `< 10-graded-outcomes` stop-gate (`app.py:22462-22470`), and
  `PREDICTIVE_MIN_BAND_N` / `PREDICTIVE_SCORE_BAND_SIZE` constants (`constants.py:788-798`).
- `_pac_spy_by_date` — a full 1-year `{date: close}` SPY dict already built at
  `app.py:22385-22399` for the existing rec_date→today alpha calc. The Day+1/Day+5 SPY leg reuses
  this directly (same closest-date-on-or-before lookup style as `_spy_return_pct`,
  `recommendations_history.py:49-70`) — **no new SPY fetch**.
- `synthesize_directives()` (`predictive_analytics.py:310-518`) — takes pre-computed band lists as
  args and appends `{type, text, source_tab}` directive dicts to the page-level "What This Means
  For You" panel. The Sentiment Alignment block (lines 494-514) is the template for plugging in a
  new source: `if <new_data> is not None and <not insufficient>: directives.append(...)`.

---

## Design invariants

1. **Diagnostic only — never auto-tunes.** This tab must never feed back into the composite score
   or the 5-gate new-position pipeline. If it ever produced strong enough evidence to justify a
   real gate change, that is a separate, explicit `constants.py` policy decision requiring the
   user's sign-off and an Opus review — not something this tab does on its own.
2. **Alpha, not raw %, on every horizon.** Day+1/Day+5/Day+20 are all computed as stock return
   minus SPY's own return over the identical window — same convention as every other tab on this
   page, and the specific thing this app got burned by twice before (reading raw-%, trending-
   market noise as signal). No exceptions.
3. **The tab alone does not prevent a loss.** It is retrospective analytics a user has to check.
   The only thing with a plausible causal path to actually protecting capital is the Phase 2 idea
   below — explicitly gated on this tab validating the pattern first (see Phase 2 section).
4. **All new thresholds in `constants.py`, and provisional until validated against real data.**
   The band cutpoints in this plan (15 / 25 divergence) are illustrative — fit to the AMD anecdote
   in a mockup, not derived from the actual historical distribution. They must be re-checked once
   the function runs against production data, before being treated as tuned values.

---

## Phase 1 — the tab itself

### Data prep — new pure functions in `stock_analyzer/predictive_analytics.py`

**`dedupe_repeated_tickers(enriched, window_days=ENTRY_TIMING_DEDUP_WINDOW_DAYS)`**

Same-ticker `new_pick` firings that recur within a rolling window are the *same opportunity*
measured multiple times (AMD fired 5× in 2 weeks purely because the daily scanner re-evaluates
the whole universe and AMD wasn't held yet — correct engine behavior, not a bug; see Design
invariant discussion in conversation history). Left undeduped, a single repeatedly-firing name
would dominate a band's statistics and read as broad evidence when it's one event.

- Group by `ticker`, sort by `rec_date`.
- Within each ticker's firings, collapse any firing that falls within `window_days` of a prior
  kept firing into that cluster; **keep the first firing** in each cluster (the moment the
  pattern first appeared — later re-firings are continued qualification, not new information).
- Default `window_days` = 5 (trading days). Provisional — validate against real cluster lengths
  once run against production data.
- Scope: `rec_type == "new_pick"` only for v1. `add_winner` carries a different risk story (adding
  to existing exposure vs. fresh-capital timing risk) and is deliberately not pooled in — a
  future pass could add it as its own separate cut, never blended into the same band average.

**`divergence_at_entry(rec) -> float`**

`divergence = momentum_score − composite_score`. Only positive divergence (momentum running ahead
of the composite) is in scope — a stock with high composite and low momentum is a different
question (early/unconfirmed setup vs. value trap) and is out of scope for this specific
"immediate drawdown risk" analysis.

**`forward_alpha_at_horizon(ticker, rec_date, horizon_trading_days, spy_close_by_date) -> float | None`**

For Day+1 and Day+5 (Day+20+ already exists via the current rec_date→today mechanism once a rec
is mature). Needs the stock's close price at `rec_date + N` trading days — this is genuinely new
fetching, unlike the SPY leg:

- Building block already exists and is proven: `YFinanceProvider.historical_close(ticker, start,
  end)` / orchestrator `get_historical_close(ticker, start, end)`
  (`stock_analyzer/providers/yfinance_provider.py:106-123`,
  `stock_analyzer/providers/orchestrator.py:134-148`) returns the first close on/after `start`
  within `[start, end]`. Already used today by `analyst_intel.fetch_anchor_price()`
  (`stock_analyzer/analyst_intel.py:344-375`) for the exact same "anchor price at date+N" shape.
- **No caching exists for it today.** A new `@st.cache_data(ttl=...)`-wrapped helper in `app.py`
  (e.g. `_cached_historical_close(ticker, start, end)`), mirroring the existing `_cached_spy`
  pattern (`app.py:2272-2274`), is needed before calling this per-rec — otherwise every page load
  re-fetches.
- `daily_snapshots` (`stock_analyzer/db.py:194-201`) is **not** usable as a shortcut here — it
  only covers currently-held tickers at EOD, and a missed `new_pick` (never bought) will almost
  never appear in it.

**`by_divergence_band(deduped, min_n=PREDICTIVE_MIN_BAND_N)`**

Follows the same shape as the existing `by_sector_alpha` / `by_rec_type_stats` pattern (group →
compute stats → sort → post-hoc `min_n` filter), extended with per-horizon values instead of one
`avg_alpha`. Returns `list[dict]`, one row per band, each with: `band_label`, `n`,
`day1_alpha`, `day1_pct_red`, `day5_alpha`, `day5_pct_red`, `day20_alpha` (reuses the existing
mature-outcome `alpha_pct`), `p_positive_alpha`.

### New constants (`constants.py`) — provisional, Opus review required before ship

| Constant | Draft value | Note |
|---|---|---|
| `ENTRY_TIMING_DEDUP_WINDOW_DAYS` | 5 | Trading days; validate cluster lengths against real data before finalizing |
| `ENTRY_TIMING_DIVERGENCE_ALIGNED_MAX` | 15 | Illustrative (fit to AMD), not yet fit to the real distribution |
| `ENTRY_TIMING_DIVERGENCE_DIVERGING_MAX` | 25 | Same caveat |

Reuses existing `PREDICTIVE_MIN_BAND_N` for band-size gating — no new min-n constant needed.

### UI — `app.py`, 6th tab on the existing page

- Add `"⏱️ Entry Timing"` to the `st.tabs([...])` list (`app.py:22507-22513`), unpack a 6th
  `_pa_tab6`.
- Compute this tab's deduped + forward-alpha dataset **lazily, cached in its own
  `session_state` key**, only when the tab is first opened — not on every page load. The new
  per-ticker historical-close fetches are heavier than the page's existing shared load; don't tax
  every visit to Predictive Analytics for users who never open this tab. Mirrors the existing
  `_pac_enriched` cache-and-refresh-button pattern.
- Chart: Plotly grouped bar (`go.Figure(go.Bar(...))`, `template="plotly_dark"`), one group per
  divergence band, one bar per horizon (Day+1/Day+5/Day+20) — same hover/customdata + `add_hline`
  pattern as the existing Score Calibration tab (`app.py:22548-22599`).
- Stat-card sidebar next to the chart pulling out each band's headline number (e.g. "83% chance of
  a red Day+1" for the top band) — `st.columns` layout, same idiom as the Sentiment Alignment
  tab's side-by-side metric groups (`app.py:22963-23002`).
- "📋 Exact values" `st.expander` below the chart with the raw per-band table — same pattern as
  the Score Calibration tab's raw-dataset expander (`app.py:22602-22632`).
- Directive: a single `caution`/`watch` directive (never `action`) fed into
  `synthesize_directives()`, gated on the top band clearing `PREDICTIVE_MIN_BAND_N`, phrased as
  awareness ("historically rougher in the first few days, fades by Day+20") — not a sizing
  prescription, since the app has no position-sizing mechanism to act on that framing yet.

### Explicitly out of scope for v1

- **RSI-at-entry.** `rsi` is computed in `_cross_reference()` (`daily_briefing.py:222`) but goes
  fully out of scope there — it is not currently threaded into the `new_pick` dict at all (unlike
  `trend`, which *is* captured at `daily_briefing.py:858` but still isn't forwarded to
  `save_recommendations`). Wiring real RSI-at-entry through touches 5 locations across
  `daily_briefing.py`, `app.py`'s `_rec_rows` construction (`app.py:4433-4446`), and `db.py` (new
  column + payload whitelist) — more than a one-line change. Not part of this build.
- **Earnings-proximity-at-entry.** Not computed anywhere for general `new_pick`s today (only the
  Catalyst Scanner does this, and only for watchlist entries). Would need new capture logic + a
  new column, populating only going forward. Not part of this build.
- **`add_winner` pooling.** Analyzed later as its own separate cut if ever revisited — never
  blended into the `new_pick` band averages above.

---

## Phase 2 (future — not part of this build, but must stay tracked)

The tab above is retrospective: a user has to open it and manually decide to act on what it
shows. That alone does not prevent a future loss. The only mechanism with a plausible causal path
to actually protecting capital is a **separate, later phase**: annotate the same divergence metric
**live, directly on the Grow Today pick card**, at the moment a `new_pick` fires — not in a
history page the user has to remember to check. Same pattern already used elsewhere in this app
(Analyst Coverage's Grow Today caption, Day Shock's Home banner): surface context at the actual
decision point, not just in hindsight.

- **Gate: do not build until Phase 1 has validated the pattern.** Specifically, until the
  deduped divergence-band statistics above show a durable effect over a real sample (not just the
  6-row AMD echo that motivated this plan). Building the live annotation first would mean
  surfacing an unvalidated pattern at the exact moment a user is about to act on it — worse than
  not building it at all.
- **Even once built, it stays a caution, not a block.** Consistent with the "never auto-tunes"
  invariant above — the user can still buy into a high-divergence name; the annotation just puts
  the information in front of them instead of leaving it buried in a separate report.
- **Tracking requirement:** once Phase 1 ships, this Phase 2 gate must be added to CLAUDE.md's
  "What's queued" section at that time (Definition-of-Done rule #6) — not left to live only in
  this plan doc or in memory. This is the exact failure mode CLAUDE.md already warns about: three
  prior Agentic Intelligence Roadmap phases went untracked for weeks because a future phase lived
  only in memory instead of the always-loaded CLAUDE.md.

---

## Governance checklist (apply when Phase 1 actually ships)

1. Any of the 3 new constants above → Opus-review citation in the commit body (CLAUDE.md hard
   rule #4 — applies to any `constants.py` touch, not just live gates).
2. F-ID in `docs/requirements.md` for the new tab (user-facing surface).
3. Constants-table rows in `docs/architecture.md` for the 3 new constants.
4. Phase 2 gate added to CLAUDE.md's "What's queued" section (see above).
5. In-app User Guide update noting the new tab.
