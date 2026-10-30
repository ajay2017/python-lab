# Grow Today owns every buy — retire `_buy_candidates` ("More Buy Candidates")

**Status: SHIPPED 2026-10-15 (F-305)** — P3b `4dc991b`, P1, P2+P3 and docs commits the same day; live-render check pending (see the CLAUDE.md queue entry). Original status: DESIGNED + OWNER-APPROVED 2026-10-15. Planner (Opus 5.5) two passes, PROCEED WITH CHANGES.
Mockup v2 approved: `docs/mockups/2026-10-15-grow-today-overflow-mockup.html`. Governing principle: OP-07
(`docs/requirements.md` §2A.1, "Gate the call, never the candidate"). The daily new-position budget
(memory `project_daily_new_position_budget`) ships AFTER this and must reuse `hold_reasons.py`.

## Why
`daily_briefing._buy_candidates` (~2756-3014) is the pre-Grow-Today buy list. Its scanner half labels
"BUY — Scanner Pick" on momentum alone (no composite floor, tone policy, `verdict_basis`, stale/ETF checks);
its add half duplicates Grow Today's own add loop (same entry rule — "Strong Buy" in Signal, `Score >=
COMPOSITE_BUY`, gap `>= ADD_WINNER_MIN_GAP_PCT` — and the same 8 suppression gates) except that it also
fires on flat/bear days. Owner SQL 2026-10-15: 951 `buy_candidate` rows (2026-05-26..10-15), 788 with
composite < 65 and verdict "conflicted" (the same rows), 8 NULL composite, ~13 add-half; 871/951 had no
same-day `new_pick`; of 176 BUY trades, 17 matched ONLY a `buy_candidate`. Bear day: Grow Today says
"Defer new entries" while BUY cards render below it. Consumers that took the label literally: Evening
Debrief (Go picks), Portfolio Thesis (`action_posture` "deploying"), Self Track Record (app_aligned).

## Owner decisions (all 2026-10-15)
- **D1/N1:** delete `_buy_candidates` ENTIRELY (both halves) and the `buy_candidates` brief key. Grow Today
  owns all new picks and adds. Adds stay bull-day only (unchanged Grow Today behaviour).
- **D2/N4:** names that cleared EVERY Grow Today check but lost only to pacing become one compact unsized
  line inside Grow Today: header "✔ Also cleared every check — not one of today's picks", chips with
  composite + ▶ Analyze, no share size, no trade button. ("not sized" is reserved for the budget.)
- **D3:** log them as `rec_type = "overflow_pick"` (sizing columns NULL, trusted composite, `hold_reason`).
  `buy_candidate` writes stop entirely → historical-only type.
- **D4 + OP-07:** flat-day held-back names get a visible box "⏸ Flat market — only fully confirmed setups
  today" (header renamed 2026-10-29 to "⏸ Mixed market — held back by today's higher bar") with each name's reason AND what would clear it. Scanner names Grow Today didn't evaluate (flat day:
  momentum below `COMPOSITE_BUY_FLAT_DAY` or beyond the flat-day working set; bear day: all) are a COLLAPSED,
  NAMED list "▸ Momentum leads — not checked today (N)" with ▶ Analyze, marked as carrying no verdict.
- **OP-07:** on flat/bear days, held names that WOULD qualify for an add show as a collapsed line
  "▸ Adds paused on a flat/down day (N) — X would qualify; adds resume on the next up day", no size.
- **D5:** Self Track Record `classify_buys`: an `overflow_pick` match counts as app_aligned. **D5(ii) revised:**
  a historical `buy_candidate` match counts as app_aligned ONLY if its stored `composite_score >= COMPOSITE_BUY`;
  below or NULL/NaN/non-numeric → self-initiated (tagged `rule_reclassified=True`).
- **N2 (queued, NOT this build):** Grow Today's add loop never calls `_cross_reference` (news/earnings).
- **N3:** the daily pick cap does not bind the Watchlist. The overflow chip tags Watchlist membership
  ("📋 On your Watchlist — check Ready to Enter there" — states membership only, never claims ENTER_NOW).
  The Watchlist row for a ticker that is both overflow and ENTER_NOW adds: "Cleared Grow Today's checks
  today but wasn't one of its picks (daily pick limit) — sized here from your Watchlist entry plan." It reads
  `grow_today["overflow_picks"]` from the already-published `_daily_brief` via `get_or_offline`; None
  (Home not built) → no caption, never "not overflow". The one-share-count-per-ticker rule is the BUDGET build.
- **N5:** DDL (owner applies BEFORE push): `recommendations.hold_reason text NULL` and `rec_budget_state
  text NULL`. The cron logs `overflow_pick` too. Overflow rows are saved in their OWN `save_recommendations`
  call (PostgREST rejects a batch with mixed keys / unknown columns); `db.py` includes `hold_reason` only in
  batches that carry it.
- **N6:** pre-registered measurement (readout built later): see §Measurement.
- **N7:** no "+ Watchlist" chip (it would route around the cap into the same-day Ready-to-Enter email).

## Build (all commits local, pushed TOGETHER after the DDL)
**P1 — `daily_briefing.py`, `act_today_view.py`, `pair_add_gate.py` (all `_GATE_FILES`; Opus review)**
- Delete `_buy_candidates` (both halves), its call, the `buy_candidates` key from `build_daily_briefing`'s
  result and from `act_today_view.offline_brief_stub` (stub keys must equal the real brief's keys).
- Delete the G-26 plumbing that existed only for the add half: `pair_skip_sink`, `_grow_today`'s
  `buy_lane_pair_skips` param and `pair_buy_lane_skips` return key, `pair_add_gate.buy_lane_only` (~372),
  the gate_ledger.py comment ~57. **KEEP the G-20 deterioration pre-pass (~1884-1911)** — it writes the
  flat-day G-20 ledger rows (`counterfactual=False`; the bear early return precedes it, so bear days never had them); update its comment only.
- `_grow_today` publishes, on EVERY return path incl. the bear early return:
  - `overflow_picks`: leftovers of `_confirmed_picks + _unverified_picks` not selected because of the cap
    (`pick_cap`) or the 1-per-sector rule (`sector_slot`), plus `_mover_picks` beyond `MOVER_MAX_PICKS`
    (`mover_cap`). Stocks only (ETF lane has its own cap). `sizing` STRIPPED. Each item: ticker, sector,
    composite, momentum, price, reason code, legacy verdict. Must be disjoint from `new_picks` and every
    held-out bucket; every item reconciled "go" and `verdict_basis == "equity"`.
  - `flat_day_held`: the curated picks the flat-day check (~1219-1220) currently drops silently, with reason
    and a "clears when" hint where derivable (e.g. earnings date).
  - `unevaluated_leads`: scanner names Grow Today did not evaluate (flat: momentum in
    [COMPOSITE_BUY, COMPOSITE_BUY_FLAT_DAY) or beyond the working set; bear: all ≥ COMPOSITE_BUY non-held),
    ticker + momentum only — explicitly no verdict.
  - `adds_paused`: on non-bull days, held names that would pass the add entry rule (Strong Buy, Score ≥
    COMPOSITE_BUY, gap ≥ ADD_WINNER_MIN_GAP_PCT) and are not otherwise blocked — ticker + composite only,
    no sizing. (Reuse the add loop's entry predicate; don't copy it.)
- `gate_ledger._BUCKET_LANES` must NOT gain these keys (pacing / no-verdict lists aren't gates). Pin by test.
- Update docstrings describing the old lane.

**P2 — `portfolio_thesis.py` (`_GATE_FILES`; Opus review) + its bundle wiring in app.py (~14352-14363)**
- `action_posture` keys off `grow_today["new_picks"]` / `["add_positions"]` (grow_today None → unavailable),
  not `buy_candidates`. Overflow never counts as deploying. Bump `"v"` 3 → 4; `grade_prior` returns
  `not_comparable` for `action_posture` only across v3→v4.

**P3 — app.py, `evening_debrief.py`, `self_track_record.py`, new pure modules `stock_analyzer/hold_reasons.py`
and an overflow rec-row builder (review required: decision surface + DB write)**
- Remove the whole "🟢 More Buy Candidates" render, its rec-log loop, the G-26 "Also paused here" caption,
  and the Evening Debrief tickers it added (~11676). Evening Debrief: drop the buy_candidate Go loop (~156-171).
- Render, inside Grow Today, per mockup v2: the overflow line (with Watchlist tag), the `flat_day_held` box,
  and the two collapsed lists. Add the new keys to `_grow_shown`. Missing key on a legacy cached/locked brief
  = "not computed", never "none". Bump `_SYNTH_SCHEMA_VER`.
- `hold_reasons.reason_text(code, ctx)` (pure, constants interpolated, never hardcoded):
  `pick_cap` "Daily pick limit reached ({GROW_MAX_PICKS_BULL} on an up day / {GROW_MAX_PICKS_DEFAULT} on a flat
  day)."; `sector_slot` "Another {sector} pick took today's one-per-sector slot."; `mover_cap` "Breakout limit
  reached ({MOVER_MAX_PICKS} per day)."; footer "Same checks as the picks above; held back only by pacing. No
  share size — not a second buy list." The budget build adds its codes here later. Test: pacing text never
  contains "sized".
- Overflow rec rows: `rec_type="overflow_pick"`, `hold_reason`, trusted composite from the gated pick, NULL
  sizing columns, separate save call + its own result/try-except.
- Self Track Record: `overflow_pick` app_aligned; D5(ii) rule; `self_vs_engine_summary` returns
  `n_rule_reclassified` (never hardcode 17). Caption on 🎯 My Edge → Self vs Engine next to the
  `n_coverage_limited` caption, shown whenever N > 0: "ℹ️ How app-aligned is counted: a buy counts as the app's
  call only if it matched a New Position or an "Also cleared" name — or, before the More Buy Candidates feed was
  retired ({last_bc_date}), a More Buy Candidates name whose composite was ≥ {COMPOSITE_BUY}. {N} earlier buy(s)
  matched only a lower-scored or unscored More Buy Candidates name — names the app was steering away from — so
  they count as self-initiated." `{last_bc_date}` = max(rec_date) of buy_candidate rows from data; if unreadable,
  drop the clause.
- Rec History: `overflow_pick` label + option (excluded by default); `buy_candidate` relabelled
  "Opportunity Watch (historical — retired {last_bc_date})".
- Watchlist overflow caption (N3). Gate (or reword) the `corr_unchecked` caption to bull days — no add lane runs
  otherwise. Grow Today add card label "Momentum {score}/100" → "Composite".

**P3b — `cron_runner.py`, `db.py` (`_GATE_FILES`; Opus review)**
- The scan lane logs `overflow_pick` rows via the same builder, separate save, isolated try/except.
- `db.save_recommendations` passes `hold_reason` (and later `rec_budget_state`) only when present.

**P4 — docs (lead):** requirements F-26/F-26a/F-26c retired, F-218a/F-289 "both add lanes" → one, new F-ID,
F-233 app_aligned wording, F-302/F-303/F-304 known-limits lines; architecture; User Guide; shipped-log;
CLAUDE.md queue (N2, budget-build N3 rule, the measurement readout); this plan's Status line.

## Tests (each paired and non-vacuous; mutation-check that each mutation landed)
TSLA shape (momentum 90, composite 48.8 Hold → `composite_skipped` on BOTH flat and bull days — corrected 2026-10-15 after the live render showed sub-bar names in the flat-day box; only names with a trusted composite ≥ COMPOSITE_BUY can be `flat_day_held`)
with a composite-80 control in `overflow_picks`; withheld (bq or `t_available=False`) and stale/ETF names never
in overflow; bear day `overflow_picks == []` and `add_positions == []` with `adds_paused` populated (control: same
fixture on a bull day sizes the add); gate parity on bull days for G-01/04/09/16/20/24/25/26 (nothing lost by
deleting the add half); G-20 pre-pass still writes its flat-day `counterfactual=False` rows (correction: a BEAR day returns before the pre-pass and never wrote G-20 rows — unchanged); overflow reason codes at
the cap boundary / same sector / mover cap; overflow ∩ new_picks = ∅; no sizing on overflow; ledger emits nothing
for the new keys; rec-row builder (separate batch, new_pick batch has no `hold_reason` key; cron too); Thesis v4 +
`grade_prior` boundary; `classify_buys` D5(ii) boundaries (65.0 app / 64.99 self / NULL/NaN/str self; a trade
matching both a below-bar buy_candidate and a new_pick stays app_aligned untagged; overflow_pick app_aligned;
`n_rule_reclassified` exact); AST: no `_buy_candidates`, `"buy_candidates"`, "BUY — Scanner Pick" or
`buy_lane_only` left in `stock_analyzer/` or `app.py`. Reverse (don't silently delete) the tests that pinned the
old behaviour, naming each in the commit body: `test_daily_briefing.py` `_buy_candidates` tests,
`test_evening_debrief.py` ~113-129, `test_portfolio_thesis.py` ~484, `test_grow_news_skip.py` ~203, any
`buy_lane_only` test.

## Measurement (pre-registered 2026-10-15; readout NOT built yet)
Question: did names that cleared every Grow Today check but were held back only by pacing (`overflow_pick`) earn
more, less, or indistinguishable forward alpha vs SPY than that same day's sized picks (`new_pick`)? Same-day
paired (arm O = that day's overflow not also picked; arm P = that day's new picks; statistic = mean over days of
mean-alpha(O) − mean-alpha(P) from `price_at_surface`); horizon `GATE_LEDGER_HORIZON_TRADING_DAYS` (30); unit =
ticker episode collapsed to its earliest day; all rows graded (counterfactual); split by `pick_cap` vs
`sector_slot` plus pooled; "building" until ≥ `GATE_LEDGER_MIN_CALLS` (8) matured paired days AND ≥
`GATE_LEDGER_MIN_TICKERS` (5) distinct overflow tickers, "firm" at ≥ `GATE_LEDGER_FIRM_CALLS` (15); directional
read only when firm, else "indistinguishable"; retire if no verdict 12 months after the first row (retiring is
success). Readings: overflow beats picks → selection order leaves better names behind (evidence only, no
retuning); lags → the cap selects usefully; indistinguishable → pure pacing. Per OP-07, judge across regimes.
