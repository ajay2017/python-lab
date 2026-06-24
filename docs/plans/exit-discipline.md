# Plan: Exit Discipline — Held-Position Deterioration Exit

**Status:** approved 2026-06-22 (Phase 1 scope, user-chosen). Trigger = drawdown-
from-peak + trend break, 3-tier WATCH/TRIM/EXIT. Build in progress.

## Problem (from the trade-log review)
A trade-history analysis of ~3 weeks showed the realized bleed lives almost
entirely in positions the app **never flagged**:

| Exit type | Loss-exits | Realized on losers |
|---|---|---|
| App said sell (RECOMMENDATION) | 2 | **−$22.70** |
| User bailed manually (app silent) | 19 | **−$1,464.66** |

When the engine *did* issue a sell it lost ~nothing. The gap is a missing
**middle layer between "Hold" and a score-collapse "Sell (<30)"**: a held name
can fall 15–25% while the composite drifts inside Hold (44–64) and nothing fires.
The slow-bleed bucket (ESTC −161, INTU −82, PINS −70, SE −70, SLB −64) is
idiosyncratic deterioration; the user exits on **trend** ("downturn", "down turn
trend") at modest drawdowns (3–7%), not on a deep stop.

Confirmed missing in code: no trailing/peak-tracking per holding, no
drawdown-from-peak exit, no volatility-adjusted stop. (The Nasdaq-pulldown
bucket — PATH/WDAY/MU, −$396 in one risk-off day — is **out of Phase 1 scope**;
that is market-wide and belongs to Phase 2, the fragility de-risk dial.)

## Design — 3-tier deterioration signal (per held position)
Inputs (all already reachable): current price, SMA20/SMA50 (in `df` indicators),
ATR% (`held_data[t]["atr"]`/price), cost basis & P&L% & weight (`port_df`),
position age (`held_data[t]["position_age_days"]`), high-water mark since the
position opened (max Close over the holding window), relative strength vs SPY
over `REL_STRENGTH_LOOKBACK_DAYS`.

**WATCH** (awareness lane, no action demanded) when:
- `drawdown_from_peak ≥ DETERIORATION_WATCH_DD_PCT` (6%) AND `close < SMA50`.

**TRIM** (Act Today) when:
- `drawdown_from_peak ≥ max(DETERIORATION_TRIM_DD_PCT, ATR_MULT_TRIM · ATR%)`,
  **capped at** `DETERIORATION_TRIM_DD_CEILING` (so a hyper-volatile name can't
  push the trigger out to 20%), AND
- `close < SMA50` for `DETERIORATION_CONFIRM_REQUIRED` of last
  `DETERIORATION_CONFIRM_DAYS` sessions (2 of 3), AND
- relative strength vs benchmark is negative (idiosyncratic weakness, not a
  market-wide down day — that's Phase 2).

**EXIT / reduce aggressively** (Act Today) when TRIM is active AND any of:
- `current price < cost basis`, OR
- unrealized **dollar** loss ≥ `DETERIORATION_EXIT_DOLLAR_LOSS`, OR
- `drawdown_from_peak ≥ max(DETERIORATION_EXIT_DD_PCT, ATR_MULT_EXIT · ATR%)`
  capped at `DETERIORATION_EXIT_DD_CEILING`.

**Refinement #1 (correctness):** the deep-drawdown EXIT branch
(`dd ≥ max(EXIT_DD_PCT, …)`) fires **without** the 2-of-3 trend confirmation —
depth IS confirmation. A one-session gap-down past the deep threshold must not
wait for "2 of 3 below SMA50". Decouples the deep EXIT from confirmation lag.

## Suppression (single-surface dedup + calm-advisor)
Suppress the deterioration signal when:
- a `stop_breach` is already active for the ticker, OR
- a composite `Sell`/`Strong Sell` (`sell_signal`) is already active, OR
- the position is inside the **settling grace** window
  (`age_days < POSITION_SETTLING_DAYS`) — but **only WATCH/TRIM are silenced;
  a deep EXIT is danger and is NEVER silenced by age** (mirrors
  `classify_position_state` precedence), OR
- *(deferred — not in the Phase 1 build)* the same signal tier was already
  shown and has not **materially worsened** (hysteresis). Defensible to defer:
  the brief is a rebuilt snapshot, not a notification stream, and a persisting
  EXIT on a still-deteriorating name is correct, not churn. Revisit if the cards
  feel repetitive in practice.

**Material-add re-anchor** (`MATERIAL_ADD_RESET_THRESHOLD = 25.0`) — *SHIPPED
(Phase 1.1).* When a NON-initial lot is ≥25% of the position, the peak window is
clipped to "since that add" (`exit_advisor.material_add_window_days(lots)` →
`assess_holding(peak_window_days=...)`), so averaging down can't measure
drawdown from a stale pre-add high (false EXIT). Computed in app.py alongside
`position_age_days` (`bundle["material_add_age_days"]`) and consumed in
`deterioration_signals`. **Cost basis stays BLENDED (deliberately NOT
re-anchored):** every EXIT path is already gated by the re-anchored drawdown, so
the `price < avg_cost` escalation only bites once there's genuine post-add
deterioration — at which point blended cost is the honest "are you underwater"
measure. Re-anchoring cost would only ever loosen the exit, so the cautious
default is kept.

## Act-Today priority (extends `_consolidate_act_today` ordering)
`stop_breach > composite Sell > deterioration EXIT > deterioration TRIM >
deterioration WATCH`, then by **dollar risk descending**. WATCH never enters
Act-Today — it renders in the awareness/Review lane.

## Constants (investment-policy — set with the user; live in constants.py)
| Constant | Default | Controls |
|---|---|---|
| `DETERIORATION_WATCH_DD_PCT` | 6.0 | drawdown-from-peak that arms WATCH |
| `DETERIORATION_TRIM_DD_PCT` | 8.0 | base TRIM drawdown floor |
| `DETERIORATION_EXIT_DD_PCT` | 12.0 | base EXIT drawdown floor |
| `DETERIORATION_ATR_MULT_TRIM` | 2.5 | ATR-scaled TRIM widening |
| `DETERIORATION_ATR_MULT_EXIT` | 3.5 | ATR-scaled EXIT widening |
| `DETERIORATION_TRIM_DD_CEILING` | 14.0 | cap so vol can't disable TRIM (refinement #2) |
| `DETERIORATION_EXIT_DD_CEILING` | 20.0 | cap so vol can't disable EXIT |
| `DETERIORATION_EXIT_DOLLAR_LOSS` | 250.0 | $ unrealized loss that escalates to EXIT |
| `DETERIORATION_TREND_MA` | 50 | trend reference MA |
| `DETERIORATION_CONFIRM_DAYS` | 3 | trend-confirmation window |
| `DETERIORATION_CONFIRM_REQUIRED` | 2 | sessions below MA required (TRIM only) |
| `REL_STRENGTH_LOOKBACK_DAYS` | 20 | relative-strength lookback vs SPY |
| `MATERIAL_ADD_RESET_THRESHOLD` | 25.0 | % add that re-anchors peak/cost baseline |

Benchmark for relative strength = **SPY** (already cached via `_cached_spy`);
made a constant, revisit (QQQ/sector) if it misfires.

## Where it lives
- `stock_analyzer/exit_advisor.py` (NEW) — pure tier logic + per-holding
  extraction. No Streamlit/I-O; unit-testable.
- `stock_analyzer/daily_briefing.py` — new producer feeding `_act_today`
  (TRIM/EXIT) and the awareness/Review lane (WATCH); extend
  `_consolidate_act_today` priority.
- `app.py` — pass SPY series + (already-present) `position_age_days` through to
  the brief; render the new `kind` values (reuse the existing directive/why/
  trigger card — minimal render change).

## Validation
Calibrate the two base drawdown thresholds against the 19 manual loss-exits:
confirm the rule flags ESTC/INTU/PINS/SE/SLB at/just before where the user
bailed, and does NOT whipsaw the dip-buy winners (NVDA adds, AVGO). Tune
`*_DD_PCT` to match demonstrated instinct. WATCH=6% intentionally stays quiet on
sub-6% wobbles (anti-churn, §2B).

## Routing
Opus plan (this) → inline/Sonnet build the decided edits → **mandatory Opus
review** (exit recommendation logic + new policy constants) → push → Streamlit
Cloud validate. Logged in `docs/cost-routing.md`.

## Phase 2 — Risk-off protective de-risk (SHIPPED 2026-06-23)

Closes the market-wide down-day bucket Phase 1's relative-strength filter
deliberately skips (the −$396 Nasdaq-pulldown day, 2026-06-09). Promotes the
existing Fragility gauge + Protect-Mode tone from awareness → a concrete
per-holding TRIM directive. **Industry-grounded** (user asked to follow PM
standards, not bespoke): regime-based trigger + risk-budgeting action.

**Trigger — ALL of:**
- **Fragile book:** `_fragility_cache.severity ∈ {caution, fragile}` (already
  encodes elevated portfolio beta, so no separate beta knob).
- **Market risk-off REGIME** — either leg (NOT a single-day price drop, to avoid
  selling the dip; vol-targeting is documented to *reduce* panic selling):
  - **Trend:** SPY below its `RISK_OFF_TREND_MA` (200)-day MA. *Basis: Faber,
    "A Quantitative Approach to Tactical Asset Allocation" (SSRN 962461) —
    10-month/200-day trend; below = de-risk.*
  - **Vol:** VIX ≥ `RISK_OFF_VIX_LEVEL` (25). *Basis: regime literature —
    <15 complacent, 15–20 normal, 20–30 elevated, 30+ stress; dynamic-allocation
    studies use ≥25 as the high-vol cut.*

**Selection:** rank holdings by **beta-contribution** (β × weight%; reuse the
`risk_advisor.py:123` pattern); take top `RISK_OFF_TRIM_TOP_N` (3) with
β ≥ `RISK_OFF_NAME_MIN_BETA` (1.2); **exclude any ticker already carrying a
higher-priority reduce** (stop/sell/deterioration/weak-large/macro-trim).

**Action (per name):** `🛡️ TRIM — Risk-Off` Act-Today card — suggest trim
~`RISK_OFF_TRIM_PCT` (25%) **or** tighten the stop to the `STOP_TIGHTEN_ATR_MULT`
level ("don't sell into weakness" option). directive names the β driver + book's
implied pullback move; trigger = deepen→reduce / stabilize→hold.

**Coordination:** new kind `risk_off_derisk`, **lowest-priority reduce** in
`_KIND_RANK` (after `deterioration_trim`) + added to `_REDUCE_ACT_KINDS`.
Computed in `build_daily_briefing` AFTER act+review are built, excluding
already-reduced tickers → single-surface guaranteed (no double-reduce).

**Constants (investment-policy, grounded):**
| Constant | Default | Basis |
|---|---|---|
| `RISK_OFF_TREND_MA` | 200 | Faber 10-month/200-day trend rule |
| `RISK_OFF_VIX_LEVEL` | 25.0 | high-vol regime cut (20–30 elevated) |
| `RISK_OFF_NAME_MIN_BETA` | 1.2 | only genuinely high-beta drivers |
| `RISK_OFF_TRIM_TOP_N` | 3 | top beta contributors |
| `RISK_OFF_TRIM_PCT` | 25.0 | modest reduction |

**Data deps (small):** the 200-day MA needs ~1y SPY history (currently cache
6mo → add a 1y fetch for the trend check); VIX must be threaded into the brief
(available in `macro_calendar`). Pass `fragility` (+ VIX/SPY-1y) into
`build_daily_briefing` like `spy_df`.

**Posture:** a LIGHT overlay, not a market-timing engine — consistent with §2B
and the evidence that aggressive tactical de-risking underperforms after
whipsaw/taxes. Most risk stays managed at entry (sizing + concentration caps).

## Out of scope (deferred)
- Full **volatility-targeting** leverage scaling / **beta-target optimizer** /
  sector-overlay selection (names in the leading-down sectors) — Phase 2.x.
- **Phase 3** — out-of-app email alerts (GitHub Actions cron) so the above reach
  the user without opening the app.
- No auto-execution — directives only; the user decides.
- **Hysteresis** on deterioration cards (suppress an unchanged tier) — deferred;
  revisit if the rebuilt-snapshot cards feel repetitive.
- ~~**Material-add re-anchor wiring** (Phase 1.1)~~ — SHIPPED (see above).
- Action Log Phase B (log the trim/exit, stop re-nagging) threads in alongside
  but the full trim/exit logging UI is its own task.
