# 📄 Reports — Design Plan

**Status: PHASE 1 (TAX REPORT) SHIPPED 2026-09-18.** Built by `implementer`, verified against
the full suite (5901 passed) and a voluntary Opus `reviewer` pass (SHIP, 0 blocking — see
"Phase 1 ship record" below). Phase 2 (Performance Review) is a placeholder only in the tab UI,
not yet built. Requirements: `docs/requirements.md` F-276; architecture:
`docs/architecture.md`'s `stock_analyzer/tax_report.py` module section.

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
- **What's still open:** Phase 2 (Performance Review) — unstarted, placeholder text only in the
  tab. See "Plan" section below, unchanged from the original design.

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
