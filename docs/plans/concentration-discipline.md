# Plan: Concentration / Position-Sizing Discipline

**Status: SHIPPED 2026-07-09** (commits `4b89680`→`ff5b1ac` concentration.py + Hard-Cap-Breach
rebalance plan; `a3fe9c1` gate-basis reversal to equity). Opus-reviewed ×2. Approved
2026-06-23 (scope: Part 1 + standing read). Sequenced before exit-discipline Phase 2.

## Why this first (from the trade-log review)
The realized losses were amplified by **concentration**: SPCX reached **23% of
the book**, several names ran 10–15%+, and the book is a correlated high-beta
tech cluster (NVDA/MRVL/MU/AVGO/PATH/ESTC/SNOW…) that falls together on risk-off
days. Professional risk is managed **ex-ante** (sizing + diversification) more
than reactively (stops/de-risk). Position sizing shrinks *every* loss at the
source; reactive de-risk (Phase 2) only helps on the red day.

## The gap: enforcement asymmetry (grounded in code)
Ceilings gate **recommendations** everywhere — Grow Today new picks
(`daily_briefing.py:528`), add-to-winner (`:887`), Watchlist ENTER_NOW
(`watchlist_advisor.py:92`), `position_sizing` cap (`risk.py:38`). But the
**manual "Log a Trade" form has ZERO concentration checks** (`app.py:12228–12339`
validates only price/ticker/SELL). All data is in session (`_last_port_df`
weights, `_portfolio_value`, sector map) — just unused. That asymmetry is how
SPCX → 23% with no friction.

## Part 1 — Entry-time concentration guard (the core fix)
A **non-blocking** nudge in the Log-a-Trade BUY path (the journal records trades
already executed at the broker, so a hard block would prevent honest
record-keeping — this warns + educates instead):
- After a BUY is recorded, compute the **resulting** single-name weight and
  sector weight (existing + this buy, treating the buy as added exposure).
- Surface a warning when the resulting weight breaches a ceiling, with the
  trim-back math:
  - single-name ≥ `SINGLE_NAME_CEILING` (15%) → "⚠ {ticker} is now ~X% of your
    book (ceiling 15%). Consider trimming ~N shares to get back under."
  - sector ≥ `SECTOR_CEILING` (35%) hard-cap warn; ≥ `SECTOR_ELEVATED` (25%)
    elevated warn.
- Pure logic in `stock_analyzer/concentration.assess_add_concentration(...)`
  (unit-testable); `app.py` renders the warning in the BUY success branch.

## Part 2 — Standing concentration read (close two surfacing gaps)
- **Pure single-name overweight flag** (`risk_advisor.py`): today's "weak-large"
  only fires when a big position is *also* weak (score < `WEAK_CONVICTION_SCORE`
  55). A **strong** name at 23% slips through. Add a conviction-independent
  `single_name_concentration` rec for any held name ≥ `SINGLE_NAME_CEILING` →
  MEDIUM (Portfolio Tune-up awareness lane, not Act-Today churn — structural, not
  same-day). Dedup: skip a ticker already captured by weak-large / sector-root.
- **High-beta cluster line** (`concentration.high_beta_share(...)`): a one-line
  standing read under the Home fragility gauge — "🔗 X% of your book is in
  high-beta (β ≥ `PORTFOLIO_BETA_ELEVATED` 1.3) names — they tend to fall
  together on risk-off days." A cheap, honest proxy for correlated-cluster risk
  (beta already in `held_data`), NOT a full correlation matrix.

## Constants (mostly reuse)
Reuse `SINGLE_NAME_CEILING` (15), `SECTOR_CEILING` (35), `SECTOR_ELEVATED` (25),
`LARGE_POSITION_WEIGHT_PCT` (10), `PORTFOLIO_BETA_ELEVATED` (1.3). One new:
`CONCENTRATION_HIGHBETA_SHARE_WARN` (60.0) — the high-beta-share level above
which the cluster line renders as a warning vs neutral info (operational
display threshold).

## Where it lives
- `stock_analyzer/concentration.py` (NEW) — pure: `assess_add_concentration`,
  `high_beta_share`. No Streamlit/IO; unit-tested.
- `stock_analyzer/risk_advisor.py` — `single_name_concentration` MEDIUM rec.
- `app.py` — Part 1 warning in the BUY success branch; Part 2b caption under the
  fragility gauge. Read-only guard already covers the save path (`db.save_trade`).

## Coordination / single-surface
The single-name overweight rec must dedup vs weak-large + sector-concentration
root tickers (same dimension). The entry-time warning is its own surface (the
form) and doesn't collide. Reuses the established risk-flag merge.

## Out of scope (DEFERRED — do not re-chase without explicit discussion)
Full correlation-matrix cluster analytics / true diversification score; auto-trim
execution; hard-blocking manual entries; cash / buying-power tracking; sizing the
entry suggestion from the risk-budget (we show the ceiling trim-back, not a full
position-sizing recommendation at log-time).

## Routing
Opus plan (this) → build inline → **mandatory Opus review** (it shapes user
decisions + touches the trade-entry validation surface + a new rec) → push →
Streamlit Cloud validate. Logged in `docs/cost-routing.md`.
