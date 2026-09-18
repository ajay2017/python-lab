# 📄 Reports — Design Plan

**Status: BOTH PHASES SHIPPED 2026-09-18.** Phase 1 (Tax Report) and Phase 2 (Performance
Review) both built by `implementer`, both verified against the full suite (5923 passed after
Phase 2) and their own voluntary Opus `reviewer` pass (both SHIP, 0 blocking — see the ship
records below). Requirements: `docs/requirements.md` F-276/F-276b; architecture:
`docs/architecture.md`'s `stock_analyzer/tax_report.py`/`stock_analyzer/performance_review.py`
module sections. Nothing left queued from this feature's original two-report scope.

Opus `planner` verdict: **PROCEED** — build as a new "📄 Reports" TAB on the existing 💰 Account
page (alongside its current `💰 Account` / `📈 Brokerage Trend` tabs), hosting both report types
internally, tax report first. **Corrected 2026-09-18 from an earlier draft of this doc that
assumed a new standalone sidebar page** — a screenshot showed `Brokerage Trend` is itself a tab
inside Account, not a separate page, so "next to Brokerage Trend" means a third Account tab, not
new nav.

## Phase 1 ship record (Tax Report)

- **Build:** `stock_analyzer/tax_report.py` (new pure module), `tests/test_tax_report.py` (21
  tests), `app.py` render-only wiring (third Account tab). Built by `implementer` against this
  plan's spec — see the module's own docstring and `docs/architecture.md` for the exact FIFO/
  ET-boundary/reconciliation design as built.
- **Verification:** full suite 5901 passed; `check_antipatterns.py` and
  `check_constants_documented.py` both green (no new constant, no new antipattern instance).
- **Review:** not mechanically required (`tax_report.py` isn't a `_GATE_FILES` member, `app.py`
  change is render-only, no new constant) — but per this doc's own risk section, a voluntary
  Opus `reviewer` pass was run anyway given the real-world filing cost of a wrong ST/LT or
  wash-sale call. **Review = Opus reviewer (Opus 4.8): SHIP, 0 blocking**; non-blocking notes
  (a narrow reconciliation blind-spot on a fully-unmatched SELL with no `cost_basis`, a
  `price=0.0` BUY treated as a real zero cost) recorded in the module docstring/architecture.md,
  neither affects a real figure.
- **What's still open:** nothing — Phase 2 shipped same day, see "Phase 2 ship record" below.

## Phase 2 ship record (Performance Review)

- **Build:** `stock_analyzer/performance_review.py` (new pure module), `tests/test_performance_review.py`
  (22 tests), `app.py` render-only wiring (second radio option in the Reports tab, replacing the
  placeholder).
- **Verification:** full suite 5923 passed (5901 + 22 new); `check_antipatterns.py` and
  `check_constants_documented.py` both green (no new constant). Independently re-ran
  `tests/test_performance_review.py` (22/22) and the full suite myself rather than trusting the
  builder's own hand-verification, per this project's own "certifying run" discipline.
- **Review:** not mechanically required (new pure module, not `_GATE_FILES`, `app.py` change is
  render-only, no new constant) — a voluntary Opus `reviewer` pass was run anyway given the
  framing risk of a wrong return-vs-SPY comparison and the risk of silently re-deriving an
  already-graded verdict. **Review = Opus reviewer (Opus 4.8): SHIP, 0 blocking** — confirmed
  genuine delegation (no re-derivation) via an equivalence test against the real
  `rec_events_readout`/`gate_ledger_readout` functions, confirmed the below-floor
  trim-vs-diversify_add alpha distinction was actually implemented (not just described),
  confirmed the realized-P&L boundary is inclusive-both-ends with no double-count, confirmed
  `hold_days` survives output-filtering, confirmed per-section offline independence including the
  `spy_prices_by_date` None-vs-empty-dict distinction, and independently re-grepped every new
  `app.py` caption/markdown/warning/info call for the LaTeX `$`-pairing bug that had just shipped
  in Phase 1 — found clean (every dollar amount confined to `st.metric()`).
- **What's still open:** nothing from this feature's original two-report scope. Deferred-to-v2
  items (real PDF pipeline, multi-year tax comparison, an emailed report via `notify.py`) remain
  unstarted with no trigger set, per the original "Deferred to v2" section below.

**Post-ship fix, same day, from a live owner screenshot:** the shipped `return_vs_spy` section
showed SPY's period return as a **percentage** next to the owner's own figure as a **dollar
amount** — not actually comparable, so there was no way to read "did I beat the market" off the
screen despite both numbers being individually correct. Added `realized_return_pct` (realized
P&L ÷ `total_cost_basis`, the total cost basis of the shares closed that period — a denominator
owned entirely by the same trades the numerator already covers, deliberately not account equity)
and `delta_vs_spy_pp`, both `None` rather than a fabricated `0%` when the window's closed trades
carry no cost-basis data; pooled across multiple lots (dollar-weighted), not averaged per-trade.
Rendered as a third `st.metric` tile with the SPY-relative delta. 2 new tests plus an exact-ratio
assertion added to the existing basis/caption test. Small, mechanical, built directly on
already-reviewed data (`realized_pnl_total`, SPY return) — handled without a fresh Opus
`reviewer` pass this time, a judgment call given the narrow scope; full suite (5925 passed) +
antipattern/constants gates all green.

## Phase 2 design (2026-09-18) — Performance Review

Opus `planner` verdict: **PROCEED** — with one genuine owner scoping decision (below). Every
other design question was fully determined by exploring the actual code: every synthesis source
is pure and period-scopable by date-filtering its input before calling it, so
`performance_review.py` invents no number. Not a `_GATE_FILES` member, no new constant needed —
same footing as Phase 1: no mandatory Opus reviewer, a voluntary one recommended narrowly on the
return-vs-SPY framing.

### Grounding (verified against actual code, not assumed)

- `rec_events_readout.py`: `collapse_by_rec_ticker()` (L120), `enrich_and_grade()` (L297, matures
  by `fired_date + horizon` vs `today`), `grade_by_rec_type()` (L377 → `band`/`n_calls`/
  `n_distinct_tickers`), `readout_footnotes()` (L426), `earliest_fired_date()` (L452). **No
  date-range parameter anywhere** — period-scoping means filtering the input rows on `fired_date`
  before calling, using the same `_to_date` helper (L85) it already uses internally.
- `gate_ledger_readout.py`: same shape — `enrich_and_grade()` (L100, matures by `rec_date +
  horizon` vs `today`), `grade_by_gate()` (L214), `readout_footnotes()` (L311). No date-range
  parameter; filter `gate_rows` on `rec_date` before calling.
- `trade_analytics.py`: `compute_extended_stats()` (L51) returns a SELL-rows frame with `pnl_pct`,
  `_dt`, `month_str`, `hold_days` — hold-day matching walks the nearest *preceding* BUY (L90-102),
  so **the date-window filter must apply to the OUTPUT frame's `_dt` column, never to the input
  trades_df**, or a SELL's pre-window BUY match is destroyed. `build_trigger_breakdown()` (L109)/
  `build_monthly_trend()` (L156) then period-scope for free once handed a sliced frame.
- Return-vs-SPY / alpha sources: `self_track_record.py` and `protective_track_record.py` are
  **all-time behavioral measures with per-trade maturity floors, not sub-window measures** —
  period-scoping them would either mislead or just re-show a permanent "building" band.
  **Deliberately excluded from v1.** `benchmark_mirror.py`'s `price_on_or_before()` (L48, nearest
  close ≤ target) and `build_benchmark_curve()` (L131, SPY indexed-to-100 over `[start,end]`) ARE
  cleanly period-scopable and are the owned source for "SPY over a window." Reuse the SPY series
  `app.py` already builds via `_cached_spy` (L3369) — never `benchmark_mirror.fetch_benchmark_prices`
  (hits yfinance directly).
- Snapshot tables — **both loaders already accept a date range, no new `db.py` function needed**:
  `db.load_account_daily_snapshots(start_date, end_date)` (L4471 → `DataFrame | None`; columns
  incl. `net_equity`, `leverage`, `cushion`, `call_distance_pct`) and
  `db.load_portfolio_risk_snapshots(start_date, end_date)` (L4529 → `DataFrame | None`; columns
  incl. `portfolio_beta`, `top_sector_pct`, `max_single_name_pct`, `avg_pairwise_corr`,
  `corr_coverage_n`). `capital_vs_margin.py` is a heavy backward-reconstruction engine — do NOT
  invoke it here; a start-vs-end delta off `account_daily_snapshots`' already-recorded (F-266)
  columns is the clean, non-recomputing leverage-drift readout.
- Banding floors already in `constants.py`, none new: `REC_OUTCOME_MIN_CALLS`(8)/`FIRM_CALLS`(15)/
  `MIN_TICKERS`(5)/`HORIZON_TRADING_DAYS`(30)/`ACTION_WINDOW_TRADING_DAYS`(10);
  `GATE_LEDGER_MIN_CALLS`(8)/`FIRM_CALLS`(15)/`MIN_TICKERS`(5)/`HORIZON_TRADING_DAYS`(30).
- Existing wiring to mirror: 🛑 Road Not Taken's dependency-loading pattern (`app.py` ~L32060-32101,
  SPY→`spy_by_date`, `enrich_and_grade`, `grade_by_gate`); 🎯 Recommendation Outcomes' pattern
  (~L32207-32277, `load_portfolio_risk_snapshots`→`snap_by_date`, SPY, trades, protective tickers
  from `_reduce_calls` via `.get()` + explicit `is None → set()`, never `or {}`). The Performance
  Review placeholder to replace is at ~L34958-34964, inside the existing `📄 Reports` tab.

### Owner decisions, final (2026-09-18)

1. **Return vs SPY = option (b):** SPY period return (via `benchmark_mirror.price_on_or_before`,
   end/start − 1) shown against the **realized trade P&L closed inside the window** (from the
   windowed `trade_analytics` slice) — fully owned, no flow-caveat needed since it's not an
   account-equity figure; explicitly labeled "realized only — unrealized moves on positions still
   open during the window are not included."
2. **Below-floor framing confirmed as designed:** a period under the 8-call/5-ticker floors shows
   raw period counts + the matured-subset mean alpha as an explicitly-labeled descriptive stat,
   never a "building"/"early"/"firm" band — enforces this project's existing
   never-contradict-the-standalone-page convention rather than introducing a new one.

### The one owner decision (superseded by the decision above — kept for the reasoning trail)

**What "return vs SPY over the period" concretely means.** No existing readout computes a
money-weighted return for an arbitrary sub-window — both `account.money_weighted_return` and
`benchmark_mirror`'s own shadow-MWR are anchored to the all-time baseline, not a quarter. Three
options, all honoring "synthesis, not recomputation":

- **(a) SPY period return** (via `benchmark_mirror.price_on_or_before`, end/start − 1) shown next
  to **account net-equity change over the same window** (`account_daily_snapshots`'
  `net_equity[start] → net_equity[end]`), explicitly labeled `"net-equity change — includes
  deposits/withdrawals, NOT money-weighted."` Simplest; the flow caveat must be stated, never
  hidden. **Planner's recommendation.**
- **(b) Realized-trade P&L in the window** (from the windowed `trade_analytics` slice) vs the SPY
  period return — fully owned and clean, but realized-only (ignores unrealized moves inside the
  window).
- **(c) Defer the return-vs-SPY tile to v2** — ship recs/gates/trade-behavior/leverage-drift/
  risk-drift sections only.

**Second, smaller confirmation:** a period below the standalone pages' 8-call/5-ticker floors
shows RAW period counts + the matured-subset mean alpha as an explicitly-labeled *descriptive*
stat, and **never** re-renders a "building"/"early"/"firm" band that could visually contradict the
all-time verdict on 🛑 Road Not Taken / 🎯 Recommendation Outcomes — a framing choice, not a
threshold change. Confirm this default.

### Plan (supersedes the sketch below for Phase 2)

1. **New pure module `stock_analyzer/performance_review.py`** —
   `build_review(*, period_start, period_end, today, trades, rec_events_rows, gate_rows,
   account_snapshots_df, risk_snapshots_df, spy_prices_by_date, historical_close_fn,
   rec_risk_snapshot_by_date, protective_call_tickers, rec_min_calls, rec_firm_calls,
   rec_min_tickers, rec_horizon_days, rec_action_window_days, gate_min_calls, gate_firm_calls,
   gate_min_tickers, gate_horizon_days, composite_buy, gate_ids) -> dict | None`. All I/O
   injected by the `app.py` caller (mirrors the two existing readout call sites) — the module
   itself does no loading. Returns `None` only when `period_start > period_end`; otherwise always
   `{"period_start","period_end","return_vs_spy","trade_behavior","recs","gates",
   "leverage_drift","risk_drift"}`, each section carrying its OWN `"status"` in
   `{"offline","empty","ok"}` — `"offline"` iff its underlying loader arg was `None`, `"empty"`
   iff loaded-but-nothing-in-window, `"ok"` otherwise. One section being offline never forces
   another offline (Phase 1's offline-independence invariant).
   - `recs`: filter `collapse_by_rec_ticker(rec_events_rows)` to `fired_date ∈ [start,end]` →
     `enrich_and_grade()` → `grade_by_rec_type()` for descriptive counts, tagged
     `below_floor = n_calls < rec_min_calls or n_distinct_tickers < rec_min_tickers`; carry
     `readout_footnotes()`.
   - `gates`: same shape, filtering `gate_rows` on `rec_date`.
   - `trade_behavior`: `compute_extended_stats(FULL trades)` then slice the **output** ext_df by
     `_dt ∈ [start,end]` (ET) before `build_monthly_trend`/`build_trigger_breakdown`.
   - `return_vs_spy`: **option (b)** — SPY period return (`price_on_or_before`, end/start − 1) vs.
     realized trade P&L closed inside `[start,end]` (from the windowed `trade_analytics` slice,
     summing `pnl` on SELL rows whose `_dt` falls in-window), carrying an explicit
     `basis="realized_only"` label and caption disclosing that unrealized moves on positions still
     open during the window are excluded.
   - `leverage_drift`: `account_snapshots_df`'s `leverage`/`cushion`/`call_distance_pct` at first
     vs last in-window row (start, end, delta).
   - `risk_drift`: `risk_snapshots_df`'s `portfolio_beta`/`top_sector_pct`/`max_single_name_pct`/
     `avg_pairwise_corr` at first vs last in-window row, carrying `corr_coverage_n` at BOTH ends
     so a listwise sample-size shift is never misread as a real diversification change (mirrors
     `rec_events_readout`'s own leg-B caption, L284-289).
2. **Export formatters in the same module** — `format_review_markdown(review) -> str` and
   `format_review_csv(review) -> pd.DataFrame`, styled like `tax_report.format_ledger_markdown`/
   `format_ledger_csv` (period header, one section per key, "informational, not advice" footer,
   below-floor disclosure inline; CSV = one row per fired call, recs+gates combined).
3. **Render wiring in `app.py`**, replacing the placeholder (~L34958-34964, inside the existing
   `if _rpt_kind == "📈 Performance Review":` branch): date-range picker + Quarter/Year
   quick-presets; load dependencies with the exact patterns already at L32060-32101/L32207-32277
   (`db.load_trades_or_none()`, `db.load_rec_events()`, `db.load_gate_suppressions()`,
   `db.load_account_daily_snapshots(start,end)`, `db.load_portfolio_risk_snapshots(start,end)`,
   `_cached_spy`, `_cached_historical_close`, `_reduce_calls` with the `is None → set()` guard);
   call `build_review()`; render each section branching on `status` (offline → distinct caption,
   empty → "nothing in this period", ok → content + below-floor disclosure caption where tagged);
   two `st.download_button`s (CSV/markdown) mirroring the Tax Report's. Keep the existing
   `db.is_readonly()` gate wrapping the whole tab.
4. **Tests** `tests/test_performance_review.py` — see "Tests" section below.
5. **Doc sync same session** — this plan doc's status line, `docs/requirements.md` F-276 Phase 2
   entry, `docs/architecture.md` module section, `docs/shipped-log.md`. No constants-table row.

### Risks specific to Phase 2

- **Double-decide / drift from the standalone pages is the central hazard here, more so than in
  Phase 1.** 🛑/🎯 read ALL-TIME rows and band them against 8-call/5-ticker floors; a
  quarter-scoped review will almost always sit below those floors. Resolution: period counts +
  descriptive matured-subset alpha only, no independent "building/early/firm" band — banding logic
  stays entirely inside the reviewed readout modules, never reimplemented here.
- **Self/Protective track records are deliberately excluded from v1** (all-time measures, not
  sub-window) — noted so a later reader doesn't "helpfully" add them back in.
- **`hold_days` integrity** — filter the ext_df OUTPUT by `_dt`, never the input `trades_df`.
- **Offline contract** — never `... or []`/`or {}` on any of the four loaders (all return `None`
  on failure by contract); `protective_call_tickers` follows the existing `_reduce_calls` pattern
  exactly.
- **Calm-advisor posture** — archival/awareness only; no section may render as an Act-Today call.
- **Gate-file / review status** — same footing as Phase 1: no mandatory reviewer trigger. Voluntary
  Opus `reviewer` pass recommended narrowly on the `return_vs_spy` section's framing (the one place
  a wrong caption could mislead about real return), matching Phase 1's judgment call.

### Tests Phase 2 must include

- Period boundary is ET-based (a UTC-stored `fired_date`/`rec_date`/`traded_at` at a Dec
  31/Jan 1 boundary lands in the correct period) — containers run UTC, same class of test as
  Phase 1's tax-year boundary.
- Inclusive both ends: a row exactly on `period_start` and one exactly on `period_end` both
  included.
- Three-state independence per section: each of the six sections independently reads `offline`/
  `empty`/`ok`; one section offline never forces another offline.
- Below-floor invariant: a period under the floor sets `below_floor=True` and never emits a
  firm/early band as the headline.
- No-recomputation / delegation: the recs/gates section outputs equal what
  `gate_ledger_readout.grade_by_gate`/`rec_events_readout.grade_by_rec_type` return on the same
  date-filtered rows — asserts `performance_review.py` adds no independent alpha/band math.
- `hold_days` preserved for a SELL inside the window whose matching BUY is before the window
  (proves output-filtering, not input-filtering).
- `return_vs_spy` carries the explicit `basis="realized_only"` label/caption; SPY period return
  uses `price_on_or_before` on a start/end that fall on non-trading days; realized P&L sums only
  SELL rows whose `_dt` falls strictly in-window, excluding a SELL just outside either boundary.
- Invalid range (`period_start > period_end`) → `None`.
- Formatters render an offline section, an empty section, and a populated section without
  crashing, and never print a literal `**` (the renderer-mismatch class).

**Owner decisions, final:**
1. **FIFO** lot-matching for the ST/LT split, with a reconciliation line vs the stored
   average-cost `realized_pnl` total and a visible divergence note if they don't match.
2. **Owner-only gating** — same check as 🛑 The Road Not Taken / 🎯 Recommendation Outcomes.
3. **No estimated tax owed** in v1 — ST/LT realized totals + wash-sale flags only ("informational
   reconciliation, not tax advice").
4. **Custom date-range picker + Quarter/Year quick-presets** for the performance review.
5. **Nav placement — a new "📄 Reports" TAB on the existing 💰 Account page**, alongside its
   current `💰 Account` / `📈 Brokerage Trend` tabs (confirmed via screenshot: these are tabs
   inside the Account page, not separate sidebar pages) — not a new top-level sidebar entry. The
   Reports tab itself hosts both report types (a sub-selector for Tax Report vs Performance
   Review), built as an extensible container so future report types are added as new entries
   inside this one tab rather than as new pages or new top-level tabs.

## Why this, why now

DRISHTA has ~28 pages and no reporting/export surface at all — everything is a live re-render,
nothing is exportable, printable, or point-in-time-archivable. Two reports were identified as the
highest-value gap:

1. **Tax report** — realized gains/losses by holding period (ST/LT), wash-sale flags, for a given
   tax year. Real trigger: tax filing season.
2. **Quarterly / point-in-time performance & decision review** — return vs SPY, recommendations
   acted vs skipped, gates that fired, sector/leverage drift over a period — an archivable snapshot,
   distinct from the app's existing always-live analytics pages.

## What already exists (grounded in code, not assumed)

**Tax logic — `stock_analyzer/tax_advisor.py` (F-186):**
- Forward-looking only today: `build_tax_analysis()` (line 152) rates *open* positions ("sell now
  vs wait for LTCG"). Rendered as a tab inside 🥧 Portfolio Overview (`app.py:18862-18891`).
- Already owns FIFO tax-lot machinery: `_build_open_lots()` (line 82) replays BUY/SELL/SPLIT into
  open lots with per-lot `buy_date`/`days_held`/`split_ratio` (IRS split holding-period rule
  handled). `holding_period_status()` (line 352) gives per-ticker ST/LT/MIXED + `days_to_ltcg`.
  Threshold: `TAX_STCG_THRESHOLD_DAYS` (`constants.py`), `days_held >= threshold ⇒ LTCG` (line 217).
- Wash-sale detection already exists — **reuse, do not reinvent**: `wash_sale_risk()` (line 408,
  before-side, used at `app.py:25609`) and `wash_sale_violation_after_harvest()` (line 452,
  after-side, 3-state `violation`/`pending`/`clean`, never false-clean). Window:
  `TAX_WASH_SALE_DAYS`.
- Precedent to generalize: `harvest_outcomes_summary()` (line 524) already reconstructs "shares
  about to be sold" via `holding_period_status(ticker, prior_trades, today=sale_date)` and splits
  ST/LT for TAX_HARVEST-loss candidates only. The tax report is this pattern generalized to
  *every* SELL in a tax year, gains and losses.

**The real gap:** SELL rows store `realized_pnl` + `cost_basis` (`db.py:70-86`) computed via
**average-cost** basis (`trades.py:57`, `compute_realized_pnl`) — a single number with no ST/LT
split and no per-lot holding period. `trade_analytics.compute_extended_stats()` derives `hold_days`
only via nearest-preceding-BUY approximation (position-level, not lot-accurate). **A correct
realized tax report must do FIFO lot-matching itself** — no stored field gives the ST/LT split.

**Performance-review sources to synthesize (all pure, none in `_GATE_FILES`, reuse — don't
recompute):**
- `rec_events_readout.py` (🎯 Recommendation Outcomes), `gate_ledger_readout.py` (🛑 The Road Not
  Taken), `trade_analytics.py` (`compute_extended_stats`, `build_monthly_trend`,
  `build_trigger_breakdown`), `self_track_record.py` / `protective_track_record.py` /
  `benchmark_mirror.py` (return-vs-SPY / alpha), `account_daily_snapshots` +
  `portfolio_risk_snapshots` + `margin.py` + `capital_vs_margin.py` (💰 Account, F-266 leverage
  history).

**Export infra:** `st.download_button` markdown-export pattern already used at `app.py:24479`
("⬇️ Download Brief") and `app.py:40309`. **No PDF library in `requirements.txt`** — confirmed, no
reportlab/weasyprint/fpdf. Nav has 28 pages; no Reports/Export page today.

## Decisions needed (owner call before build starts)

1. **Lot-matching method for realized gains (the crux).** IRS default absent specific-ID is FIFO;
   Robinhood's default is FIFO. The app's *stored* `realized_pnl` uses average cost — the two
   totals agree over a fully-closed round trip but allocate gains differently across partial sales
   and across the 365-day ST/LT line. **Recommendation: FIFO reconstruction for the ST/LT split,
   plus a reconciliation line against the stored average-cost `realized_pnl` total, with a visible
   divergence note when they don't match — never silently pick one.** Confirm FIFO matches your
   actual brokerage election.
2. **Owner-only gating.** Reports contains raw realized dollars + tax data. Recommend gating the
   whole page behind the same owner-only check as 🛑 The Road Not Taken / 🎯 Recommendation
   Outcomes.
3. **Tax rates in the report.** Option A: reuse the existing bracket radio
   (`app.py:18875`, Low/Med/High → `TAX_RATE_*`) so the report and the live advisor never disagree.
   Option B (recommended): show ST/LT realized totals + wash-sale flags as the core, with
   estimated-tax-owed as an optional, clearly bracket-dependent add-on — the more defensible
   "informational reconciliation, not tax advice" posture.
4. **Performance-review period granularity.** Recommended: custom date-range picker with
   Quarter/Year quick-presets (calendar-quarter buttons + "this tax year"). Confirm quarterly is
   the primary framing.
5. **Nav placement.** ~~Recommended: insert "📄 Reports" in the owner-admin cluster, adjacent to
   💰 Account / 🎯 Recommendation Outcomes.~~ **Superseded by the owner's final call above: a third
   tab on the 💰 Account page itself, not a new sidebar page.**

## Plan

**Phase 1 — Tax Report (ships first):**
1. New pure module `stock_analyzer/tax_report.py` —
   `build_realized_lot_ledger(trades_df, tax_year, today=None) -> dict | None`. FIFO-replays the
   journal (reusing `tax_advisor._build_open_lots`'s exact BUY/SELL/SPLIT conventions — do not
   fork the split logic) into closed matched lots: `{ticker, buy_date, sell_date, shares, proceeds,
   cost, gain, days_held, term: "ST"|"LT"|"Unknown"}`, filtered to sales whose ET sale-date year
   equals `tax_year`. A SELL with no matching BUY history ⇒ `term="Unknown"`, never guessed.
   Returns `None` on failure (offline contract), a shaped-empty dict for a genuinely empty year.
   Aggregates `st_gain`/`lt_gain`/`unknown_gain`/`total_proceeds`/`total_cost` plus
   `stored_realized_total` (sum of stored `realized_pnl` for that year) for reconciliation.
2. Wash-sale flags on realized losses via the *existing* `wash_sale_violation_after_harvest()` per
   losing closed lot — surface only what it returns, invent nothing new.
3. Render the Tax Report sub-view inside the new "📄 Reports" tab on 💰 Account (render-only
   wiring in `app.py`, same `elif page == "💰 Account":` block, new third tab alongside
   `💰 Account` / `📈 Brokerage Trend`): year selector (populated from distinct SELL years actually
   present, not a free int), per-lot table, ST/LT/Unknown summary, reconciliation line + divergence
   note, wash-sale flag column, and a prominent "Not tax advice — informational reconciliation
   only; verify against your broker's 1099-B" banner.
4. Export: `st.download_button` for CSV (per-lot ledger) and markdown (formatted report), reusing
   the existing pattern.

**Phase 2 — Performance / Decision Review (synthesis, no new numbers):**
5. New pure module `stock_analyzer/performance_review.py` —
   `build_review(period_start, period_end, ...) -> dict | None`. Assembles a point-in-time snapshot
   entirely by **calling existing readouts** (rec_events, gate ledger, trade trend, benchmark
   mirror, account/leverage snapshots) — recomputes nothing a live page already owns, so the
   archived snapshot can never disagree with the live surfaces. `None` per-section on producer
   outage, not a whole-report failure.
6. Render the Performance Review sub-view (a second entry inside the same "📄 Reports" tab, next to
   Tax Report) + custom-range/quarter presets + markdown export + browser-print-friendly layout.

**Deferred to v2, explicitly not built now:** a real PDF pipeline (no new dependency warranted —
markdown/CSV + browser print is genuinely useful and $0 cost); specific-lot-ID method selection;
multi-year tax comparison; scheduling the quarterly review as an emailed report via `notify.py`
(reusable narrative pattern there, but a cron/email lane is its own review-required change).

## Risks / coordination

- **Double-decide risk (main hazard for Phase 2).** The performance review must be a *consumer* of
  existing readouts, never a second computation — recomputing return-vs-SPY or rec attribution
  independently would drift from My Edge / Recommendation Outcomes / Road Not Taken, the exact
  anti-pattern this project's coordination convention forbids. The only genuinely new thing here is
  the archival snapshot + period framing — that's also the answer to "why isn't this a 28th
  duplicate page."
- **Gate-file / review caveat.** As specified, all new logic lands in new pure modules
  (`tax_report.py`, `performance_review.py`) + render-only `app.py` wiring — not in `_GATE_FILES`,
  no new constant, so no mechanical Opus-`reviewer` trigger. Avoid two ways this could slip into
  review-required territory: (a) don't add a new read helper to `db.py` (a gate file) — read via
  existing `db.load_trades()` and existing snapshot loaders; (b) no new threshold/constant is
  needed (reuse `TAX_STCG_THRESHOLD_DAYS`, `TAX_WASH_SALE_DAYS`, `TAX_RATE_*`). **Judgment call:**
  although not mechanically required, recommend a **voluntary** Opus `reviewer` pass on
  `tax_report.py`'s FIFO/term/tax-year-boundary logic specifically — a wrong ST/LT allocation is a
  real-world cost even though it moves no in-app gate.
- **Offline contract.** Both builders return `None` on failure, shaped-empty dicts on genuinely
  empty input; render sites branch on `is None` (offline) vs empty (nothing this period) vs
  populated. Wash-sale `pending` must render distinctly from `clean`.
- **Calm-advisor posture.** These are awareness/archival surfaces, not Act Today — no banner here
  should read as a call to act. The tax report issues no recommendation; forward-looking harvest
  advice stays where it already lives (Portfolio Overview's tax tab).
- **"Unknown term" honesty.** SELLs without matching BUY history (broker-imported partial history)
  show as Unknown, never defaulted to ST or LT — matches existing `build_tax_analysis` handling.

## Tests the build must include

- FIFO share conservation: matched closed-lot shares == SELL shares with matching buys per
  ticker/year; unmatched excess surfaces as Unknown, never dropped.
- ST/LT boundary: a lot held exactly `TAX_STCG_THRESHOLD_DAYS` classifies LTCG; 364/365/366-day
  cases pinned as exact edge tests, not "assumed safe."
- Tax-year filter is ET-based: a sale at Dec 31 23:00 ET vs Jan 1 01:00 ET (UTC-stored `traded_at`)
  lands in the correct year — containers run UTC, test the boundary explicitly.
- Reconciliation: FIFO total vs `stored_realized_total`, both an agreeing case and a deliberately
  divergent one (partial average-cost vs FIFO), confirming the divergence note fires.
- Wash-sale flags only mirror the detector: violation / pending / clean cases via the existing
  `wash_sale_violation_after_harvest`, asserting no invented logic.
- Empty/offline: empty year ⇒ shaped-empty dict, no crash; simulated load failure ⇒ `None`;
  performance_review with one producer offline ⇒ that section `None`, others still render.
- SPLIT holding-period inheritance: a lot spanning a SPLIT keeps its original acquisition date for
  term classification.

All of the above is unit-testable because the logic lives in `tax_report.py` /
`performance_review.py`, not in `app.py` (which no test imports).
