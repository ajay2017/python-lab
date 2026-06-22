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

**Material-add re-anchor** (`MATERIAL_ADD_RESET_THRESHOLD = 25.0`) — *declared but
wiring deferred (Phase 1.1).* Intent: when a recent lot adds ≥25% to the
position, re-anchor the high-water-mark window (and cost basis) to that add so
averaging down can't leave a stale pre-add peak triggering a false EXIT. The
`assess_holding(peak_window_days=...)` hook exists for this; the producer does
not yet compute/pass it, so the peak window currently spans the whole holding
(oldest lot). Acceptable now (no current holding has averaged down into a
deteriorating name); wire before that pattern appears.

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

## Out of scope (deferred)
- **Phase 2** — risk-off protective de-risk (fragility → per-holding trim on
  market-wide down days; the Nasdaq-pulldown bucket).
- **Phase 3** — out-of-app email alerts (GitHub Actions cron) so the above reach
  the user without opening the app.
- No auto-execution — directives only; the user decides.
- **Hysteresis** on deterioration cards (suppress an unchanged tier) — deferred;
  revisit if the rebuilt-snapshot cards feel repetitive.
- **Material-add re-anchor wiring** (Phase 1.1) — the constant + `peak_window_days`
  hook exist; the producer doesn't compute the re-anchored window yet.
- Action Log Phase B (log the trim/exit, stop re-nagging) threads in alongside
  but the full trim/exit logging UI is its own task.
