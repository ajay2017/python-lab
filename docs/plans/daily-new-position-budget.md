# Daily new-position budget — cap the SUM of a day's new buys at 40% of net capital

**Status: DESIGNED + OWNER-APPROVED 2026-10-15, build starting.** Planner (Opus 5.5) PROCEED WITH CHANGES; owner
accepted all 8 recommendations. Memory `project_daily_new_position_budget`. Ships after F-305 (Grow Today owns every
buy) and reuses `stock_analyzer/hold_reasons.py`. Governing principles: OP-07 (gate the call, never the candidate),
F-09d (leverage is awareness, never a gate — this caps SIZING against net capital, like F-255, and never blocks a call).

## Why
`NET_CAPITAL_POSITION_CAP_PCT` (25%, F-255) applies PER new position; nothing caps the SUM of one day's new-position
sizes. Live 2026-10-15 (flat day): three Grow Today breakout picks, each sized at ~24% of net capital → ~72% if all
taken. 2026-09-22: CVX+PAY ≈ 48%. Book ~3.7× leverage on 2026-09-30.

## Owner decisions (2026-10-15)
- **A + B:** show today's new-position total as % of net capital, AND a daily budget: **`DAILY_NEW_POSITION_BUDGET_PCT
  = 40.0`** of net capital; later suggestions are sized from what's LEFT; when used up → "not sized — budget used",
  visible, never dropped.
- **Ordering:** highest composite first.
- **(1) Adds count** toward the budget (F-255 already scopes "new/added position"). Logged BUYs count regardless of
  opening vs add. The budget never trims or resizes a holding.
- **(2) Budget can't be checked** (trades unreadable, allocations unreadable, cash record stale/missing) → the BUY call
  still shows; every budget-consuming size is withheld with a visible reason (F-261 precedent).
- **(3) Basis:** F-255's net capital, LAG-CORRECTED: subtract today's logged BUY cost and add back logged SELL proceeds
  for trades timestamped after the cash record's `updated_at` (holdings update on trade log; cash only at the broker sync).
  **Clarified 2026-10-15 (chunk-1 review, B2):** "today's" was shorthand that only holds when the sync runs daily. The
  rule is EVERY logged BUY/SELL timestamped after the cash record's `updated_at`, whatever its date — otherwise a missed
  sync or hand-entered cash (trusted up to `ACCOUNT_CASH_STALE_DAYS`) leaves earlier post-cash buys uncorrected and
  OVERSTATES the budget (probe: 60% too high). Same stated intent, fail-closed reading; told to the owner.
- **(4) In-app order:** emailed allocations (locked at their emailed size) → Grow Today new picks + adds, composite-first,
  ETFs last → Watchlist ENTER_NOW not already emailed, composite-first → Analysis, sized from what's left.
- **(5) Sells do NOT give budget back** (gross deployment).
- **(6)** Grow Today F-255 note gap — DONE (`d2ef355`).
- **(7)** No "release allocation" button in v1 (allocations reset next trading day).
- **(8)** `recommendations.rec_budget_state` — DDL ALREADY APPLIED (text NULL), writer support already in `db.py`
  (`_hold_keys`, `4dc991b`).
- From F-305 N3: one ticker gets at most ONE share count per day — if it's a Grow Today new pick, the Watchlist shows its
  ENTER_NOW verdict but "Sized in Grow Today today — see 🏠 Home" (needs `_grow_today_tickers_cache`, registered in
  `coord_freshness`); fix Watchlist's sector soft-caution (~231-244) to exclude the same ticker.

## Cross-surface, cross-time rule
"Sizes already pushed to you today are locked; a logged buy replaces its allocation; everything later is sized from
what's left, highest composite first at that moment." Composite-first can't hold across time (a 09:45 email can't rank
later candidates) — this is the honest version.
- **Push surfaces (emails) WRITE allocations; the app only READS** (app reruns would fill the record with stale offers).
- **New table `new_position_budget_allocations`** (owner applies DDL; SQL recorded in db.py's docstring and
  `docs/sql/`): `budget_date date, ticker text, dollars numeric > 0, shares numeric, price numeric, composite_score numeric,
  source text ('scan:morning'|'scan:watchlist'|'intraday'), basis_dollars numeric, budget_pct numeric (constant frozen at
  write), created_at timestamptz default now()`, `PRIMARY KEY (budget_date, ticker)`, RLS `FOR ALL TO service_role`.
  First writer wins (upsert ignore-duplicates). Weekend/holiday rows dropped via `write_guards.split_non_trading_rows`.
  Not reusing `recommendations` (an allocation means "a size was pushed", a different fact — overloaded-producer class).
- **Accounting:** used = Σ logged BUY $ today + Σ allocation $ for tickers with no logged BUY today (a logged buy replaces
  its allocation — never double-counted). left = max(0, budget − used). Buy for less/more than allocated → the logged $
  counts; bought twice → summed; shown but not acted → never counted.
- **Allocation:** candidates whose sizing already carries a no-size marker (ceiling/capital/stop infeasible, portfolio
  unknown) pass through and consume nothing. Sort key `(is_etf, −composite, −momentum, ticker)` (missing composite last;
  ticker tiebreak makes cron and app agree). A candidate with an existing allocation shows `min(allocated_shares,
  current F-255 shares)` and consumes nothing new. Others get `min(F-255 $, left)` rounded down to whole shares; < 1 share
  → budget used.
- **Scan lane order (cron_runner):** compute_morning_picks → Go picks → compute_watchlist_entries (own try; failure →
  entries empty, that email skipped) → JOINT allocation over Go picks ∪ watchlist entries (disjoint via
  scanner_go_tickers) → allocate the non-emailed new_picks from what's left → write allocations → rec-log capture (so
  rec_shares is the shown size) → send emails. Allocation write fails → emails still go (sizes correct as of then), footer
  "today's budget record couldn't be saved — later suggestions may not account for these", lane fails (red heartbeat).
  Intraday pullback email: reuse existing allocations; a new name is allocated from what's left and written.
- **Strict reads:** cron reads trades via `db.load_trades_or_none()` (not the lenient `load_trades()`); the app does its own
  strict read (not `st.session_state.trades_df`, which collapses a failed read to empty), cached against `_coord_epoch`.
- **Budget-used / can't-check are NOT ledgered gates** (the BUY call stands; no gate_id). The rec row is still written with
  `rec_shares` NULL, `rec_sizing_version` 4 and `rec_budget_state` set.

**Chunk-3 contract for a failed allocation save (chunk-2 review, 2026-10-15):** treat the write as failed unless
`out.get("error") is None and out.get("saved") == n_intended and not out.get("dropped_invalid")` — checking `error` alone
misses the no-DB path (error None, saved 0) and dropped rows (an emailed size never recorded). `out["inserted"]` (rows
actually inserted, best-effort) can flag an emailed size that lost to an earlier writer. Pass dates as plain ET `date`
objects (a naive datetime is rejected; a UTC datetime after 20:00 ET would otherwise read tomorrow's row).

**Chunk-3 review notes for chunks 4/5 (2026-10-16):** (a) SHIP CONDITION — chunk 3 must NOT be pushed (deployed) before
chunks 4 and 5 (local commits on main are fine): `SIZING_FORMULA_VERSION` is 4 everywhere, so app-written rows would be stamped v4 while un-budgeted, and Home /
Watchlist would show an un-budgeted size for a name the email sized smaller. (b) "a logged buy replaces its allocation"
also unlocks an allocation made AFTER that day's buy (an add) — unreachable in cron, relevant once the app's add lanes
are budgeted. (c) Intraday writes allocations for fully-sized names whose share count the intraday email doesn't show;
chunk 4 must not call those "Allocated in today's email" unless the email showed the size (owner question). **Owner decided 2026-10-16: (a) the intraday email SHOWS the share count for every name it
allocates**, so an allocation always means "a size was shown to you" and "Allocated in today's email" stays true.

**Chunk-4 review notes for chunk 6 docs (2026-10-16):** disclose that (1) before the 09:45 scan email lands, Home ranks every
pick and add composite-first, so the #1 pick it sizes (and the rec row it writes first) can differ from the size the email
later locks; (2) for up to 5 minutes after the email (allocations cache bucket) and after a trade logged outside the normal
refresh path (trades cache bucket), Home can show a pre-update size. Also fix the stale "Allocated in today's/this
morning's email" wording below (now "Reserved by today's email run") and cron_runner._run_intraday's docstring (the intraday
email now shows share counts).

## Basis states (`daily_budget` must NOT import `stock_analyzer.margin` — awareness-only invariant,
`tests/test_margin.py` `_GATE_MODULES`; callers resolve `resolve_net_capital` and pass numbers in, as `risk.py` does)
levered → net capital, lag-corrected; unlevered with a cash record (cash ≥ 0) → gross book + cash, lag-corrected; no cash
record or stale (> `ACCOUNT_CASH_STALE_DAYS`) → unknown; called (net ≤ 0) → budget 0 (F-255 already marks every pick
capital_infeasible → passthrough); portfolio value unknown → F-261 already withholds sizing.

## Wording (add to `hold_reasons.py`; constants interpolated; "not one of today's picks" never appears in these)
Status line on every sizing surface: "New positions today: X% of net capital (logged $A · allocated $R · this list $S) —
budget {DAILY_NEW_POSITION_BUDGET_PCT}%, $L left". Per-item: `budget_partial` "Sized from what's left of today's budget —
{n} of {m} shares."; `budget_exhausted` "One of today's picks — not sized: today's new-position budget
({DAILY_NEW_POSITION_BUDGET_PCT}% of net capital) is already used. Resets tomorrow if it still qualifies.";
`budget_reserved` "Allocated in this morning's email"; `budget_unknown` "Size withheld — today's new-position budget
couldn't be checked ({why})."; over budget from logged buys alone: "Today's new buys are {x}% of net capital — above the
{DAILY_NEW_POSITION_BUDGET_PCT}% daily budget; nothing further sized today." Calm tone; nothing goes into Act Today.

## Constant / versioning
`DAILY_NEW_POSITION_BUDGET_PCT = 40.0` next to `NET_CAPITAL_POSITION_CAP_PCT` (comment per the owner decision; additive to
F-255/SINGLE_NAME_CEILING; never trims a holding; not a leverage gate). Architecture constants row. No other constant
(partial sizes floor at 1 share, same as F-255). **`SIZING_FORMULA_VERSION` 3 → 4** (`daily_briefing.py` ~137 + docstring
"4 = adds the daily new-position budget") — stored rec_shares now depends on other candidates and today's trades.

## Build chunks (all local, pushed together at the end; each Opus-reviewed)
1. `constants.py` + NEW `stock_analyzer/daily_budget.py` (pure: `buys_on_et_day`, `trades_after`, `budget_basis`,
   `allocate`, `apply_to_sizing`, `candidate_sort_key`, `status_line`/`note_text` or via hold_reasons; never mutates
   inputs; takes now/today as params; extract a shared `_et_trade_dates` helper with `sold_today.py` so both agree on
   "today") + tests + `_GATE_FILES` (+ CLAUDE.md list) + `test_margin._GATE_MODULES` + architecture constants row.
2. `db.py`: allocation table DDL in docstring + `docs/sql/`, `save_budget_allocations` (first writer wins, read-only no-op,
   write_guards, `_sanitize_for_write`), `load_budget_allocations_or_none(day)`.
3. cron/headless/email: scan-lane restructure, strict trades read, intraday allocation + write, budget branches in
   `notify._sizing_cap_note` + the Watchlist-email chain + a status banner on all three emails, version bump.
4. app Home/Grow Today: post-pass after both `build_daily_briefing` paths AND on cache hits, BEFORE the rec-log capture;
   publish `_new_position_budget_cache` (None on failure; `coord_freshness` TIER_GATE); republish in
   `_refresh_portfolio_cache_after_trade`; status line in the New Positions header (no new top banner); markers in both card
   blocks; budget must stay OUTSIDE `_home_synth_cache` (fresh post-pass each render; non-mutation test).
5. Watchlist (rank 3; one-share-count rule; sector soft-caution fix), Analysis (4 sites, sized from what's left), Trade
   Journal confirm line ("today's new buys are now X% of net capital (budget 40%)", never blocks).
6. Docs (lead): requirements §2A.2 row + §2A.3 gate row (next G-ID) + F-ID, architecture module section, User Guide,
   user-manual, shipped-log, this Status line, memory.

## Tests (paired, non-vacuous; mutation-check each)
binding vs non-binding day (24%+24% → second sized to 16%; 10%+10% → unchanged except version 4); Σ new allocations ≤ left,
never > F-255 size, left ≥ 0; boundaries (used == budget; left = price − 0.01 → used; left = price → 1 share); ordering
(higher composite first; reversed input order gives an identical result — the cross-surface proof; ETF after stock; missing
composite last; ticker tiebreak); logged-buy deduction incl. ET midnight boundary and allocation-replaced-by-buy (2,000
alloc + 1,500 logged → used 1,500); fail-safe (each unknown input → every consumer budget_unknown, paired with a readable
control that sizes); margin-called → budget 0, passthrough; allocation reuse + idempotent rerun; lag correction (buy after
cash timestamp subtracted, before not; sell after added back); cron and app agree on emailed tickers' shares; Watchlist
failure isolation; no input mutation; every no-size marker rendered by every renderer (producer + renderer AST, both ends);
DB first-writer-wins / weekend drop / read-only no-op / None vs empty; `daily_budget` doesn't import margin.

## Accepted limits (disclose in docs)
A buy made from the app screen and not logged isn't counted (status line says it counts "what was emailed and what you've
logged"). Prices drift after an email: allocated dollars reflect email-time price while the shown share count is locked.
F-255's own per-name cap has the same net-capital lag (separate decision; not fixed here).
