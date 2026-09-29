# Plan: Exit Discipline — Held-Position Deterioration Exit

**Status: FULLY SHIPPED — all 3 phases.**
- Phase 1 (3-tier WATCH/TRIM/EXIT deterioration): SHIPPED.
- Phase 2 (risk-off de-risk): SHIPPED 2026-06-23.
- Phase 3 (email cron protective alerts): SHIPPED 2026-06-24 (commits `9add28f`→`cb37862`; own plan `email-alerts-cron.md`).
- **PARKED (re-analyzed 2026-09-29, verdict unchanged — DO NOT BUILD yet):** deterioration-card hysteresis — see body note below for the full analysis. Re-park trigger changed from "someone eyeballs a flicker" to "a measured flicker rate." **Phase 0.5 (the measurement script) SHIPPED 2026-09-29** (`scripts/deterioration_flicker_scan.py`, no reviewer needed — read-only, pure-additive, not wired into any decision path, same precedent as `exit_ladder_replay.py`/`exit_early_cost_analysis.py`); **FIRST REAL RUN 2026-09-29: 1 flicker in 209 ticker-days (WATCH-only), verdict unchanged, re-check ~mid/late Nov 2026.**
- **WATCHING, not started (2026-09-29):** a companion, currently-unmeasured flicker question on the *mechanical* exit path (`stop_breach`/`sell_signal`, not the deterioration tier) — see "Mechanical-exit (stop_breach/sell_signal) intraday flicker" under Out-of-scope below. Triggered by a live SPCX EXIT card appearing and clearing within an hour; the deterioration tier can't move that fast (daily-close inputs only), so this is a structurally different, entirely unlogged signal family. No capture exists for it today — nothing to build yet, just watching for a second instance.
- **DEFERRED:** Action Log Phase B (log the trim/exit UI).

Approved 2026-06-22 (Phase 1 scope, user-chosen). Trigger = drawdown-from-peak + trend break, 3-tier WATCH/TRIM/EXIT.

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

## Out of scope / remaining open items

- Full **volatility-targeting** leverage scaling / **beta-target optimizer** /
  sector-overlay selection (names in the leading-down sectors) — **DEFERRED**.
- ~~**Phase 3** — out-of-app email alerts (GitHub Actions cron)~~ — **SHIPPED 2026-06-24** (see `email-alerts-cron.md`).
- No auto-execution — directives only; the user decides.
- **Hysteresis on deterioration cards — RE-ANALYZED 2026-09-29 (owner re-ask, not an observed flicker). Opus `planner` verdict: DO NOT BUILD the stateful mechanism yet; build a cheap measurement instead.**

  **The old "per-ticker day-over-day tier state (none today)" premise is FALSE — corrected here.** `cron_runner.py::_run_premarket` (lines ~366-399) has captured one `exit_signals` row per fired tier per ticker per trading day, unconditionally, since 2026-07-21 (`db.save_exit_signals_batch`, idempotent on `(ticker, signal_date, signal_type)`) — `stock_analyzer/exit_velocity.py` already reads this exact table for a different purpose (WATCH-tier deterioration-velocity detection). **The data gap is closed. It just doesn't create a need to build the mechanism** — that's a separate question, and the planner's re-analysis answered it "not yet."

  **Why NOT to build it now, even with data available:** TRIM/EXIT are already heavily damped (the 2-of-3-below-MA confirmation is itself two-sided trend hysteresis; the deep-EXIT shortcut needs a 12%+ swing, not ordinary noise) — and the trailing-peak mechanic itself (`peak = close.tail(window).max()`, sticky upward) is *already* a free asymmetric clear-band on `dd_from_peak_pct`, which the original 2026-06-28 parking note never credited. That leaves only **WATCH** (awareness-only, lowest stakes) as plausibly flicker-prone. Against that speculative, low-stakes upside: building stateful hysteresis touches `exit_advisor.py`+`daily_briefing.py` (both `_GATE_FILES`, mandatory Opus review) and needs a new owner-approved constant — real cost to fix a toggle nobody has reported in 3+ months.

  **Phase 0.5 SHIPPED 2026-09-29:** `scripts/deterioration_flicker_scan.py`, a read-only diagnostic (sibling to `exit_ladder_replay.py`/`exit_early_cost_analysis.py`) that measures the REAL flicker rate per tier from the existing `exit_signals` history. **Correction from the original recommendation:** `cron_heartbeat` cannot actually do the cross-check as first proposed — it keeps only the single LATEST row per lane (an upsert on the `lane` primary key), so it can answer "is the premarket lane healthy right now" but not "did it run on some date three weeks ago." The script instead uses `score_history` (written unconditionally, once per HELD ticker per trading day that ticker's bundle was fresh enough to score, since roadmap B1) as the per-ticker per-day "this day was actually covered" ground truth — finer-grained than `cron_heartbeat` would have been anyway, since it's ticker-specific, not just lane-specific. Only (ticker, date) pairs with a `score_history` row go on that ticker's timeline; every other day is simply absent, so a real gap from a position being sold and re-bought later reads as one long low-confidence span, never a manufactured flicker. Reports, per ticker, every ACTIVE→CLEAR→ACTIVE round trip (same-tier vs cross-tier split, since only same-tier WATCH round trips are in the contingent Phase 1 design's scope) with its length in confirmed-covered days — turning the re-park trigger from "eyeball it" into "measured rate," same discipline as Entry Timing's n=20 gate. 14 new unit tests on the pure timeline/run/gap helpers (`tests/test_deterioration_flicker_scan.py`); full suite 6471 passed; antipattern + constants-doc gates green; no Opus reviewer needed (pure-additive, not wired into any decision path). **FIRST REAL RUN, 2026-09-29 (owner-run, real Supabase data):** 24 tickers, 209 confirmed-covered ticker-days (the full `score_history` window to date, ~2.5 weeks since its 2026-09-13 start) — **1 gap event total**: `APP`, WATCH→CLEAR→WATCH, a single confirmed day (2026-09-22), same-tier. Zero TRIM/EXIT round trips of any kind. **Consistent with, not contradicting, the planner's prediction** (the one event that exists is exactly the WATCH-same-tier shape the contingent design targets; TRIM/EXIT show zero, matching "already damped") — but N=1 in 209 ticker-days is not a rate, and building anything off a single instance would repeat the exact "someone eyeballed a flicker" reasoning the 2026-06-28 parking already rejected, just with the eyeball replaced by a script. **Verdict unchanged: still DO NOT BUILD.** **Re-check trigger, so this doesn't drift into an open-ended "keep watching forever":** re-run once `score_history` has accumulated roughly 2-3x today's window (~60-90 confirmed-covered ticker-days per actively-held ticker, i.e. ~mid-to-late Nov 2026 at the current ~24-ticker book size) — only revisit the DO-NOT-BUILD verdict if that re-run shows multiple same-tier WATCH gaps, not on a single additional instance either. Run: `python scripts/deterioration_flicker_scan.py` (optionally `--ticker MU` / `--max-gap 5`).

  **Contingent mechanism spec, fully worked out so it doesn't need re-designing IF Phase 0.5 ever shows real flicker** (Phase 1, needs its own explicit go-ahead + Opus review):
  - Extend the pure `classify_deterioration_tier` with an additive `prev_tier: str | None = None` param — default reproduces today's behavior exactly (existing callers, e.g. `candidate_deterioration_flag`, untouched).
  - Buffer applies ONLY to the `dd_from_peak_pct` comparison (not the discrete legs — those already have their own damping): `dd >= (arm_T − DETERIORATION_CLEAR_BUFFER_PCT)` when `prev_tier` is at or above `T`. Proposed default `DETERIORATION_CLEAR_BUFFER_PCT = 1.5` (percentage points) — **not final, owner sign-off required per Hard Rule #1**.
  - Scope to **WATCH only** (recommended) — narrowest, can never suppress an Act-Today call. Widen to TRIM only if Phase 0.5 shows TRIM-specific flicker.
  - **Deep-EXIT shortcut explicitly EXEMPTED** — a 12%+ swing doesn't flicker, and stickiness there would just create stale urgency after real danger has passed.
  - The impure `prev_tier` resolver lives in a NEW module (`stock_analyzer/deterioration_hysteresis.py`), deliberately outside `exit_advisor.py`'s pure core and outside the `_GATE_FILES` review surface where possible — resolves a 3-state result (ACTIVE / CLEAR / UNKNOWN) from `exit_signals` + `cron_heartbeat`, never collapsing `None`→CLEAR.
  - **Unknown-yesterday-state → treat as "was active" (sticky).** Safe specifically because the buffer only ever makes a tier MORE persistent, never less — structurally incapable of manufacturing a false all-clear even under a data gap. The **monotonic-stickiness invariant** (`classify_deterioration_tier(prev_tier=X)` never returns a weaker result than `prev_tier=None` for identical inputs) is the load-bearing safety property and needs its own test, not just this reasoning.
  - Required test invariants (Phase 1): the monotonic-stickiness invariant; exact buffer-edge boundaries (at `arm`, at `arm − buffer + ε`, at `arm − buffer − ε`, both with and without `prev_tier`); deep-EXIT non-stickiness (clears immediately once `dd` recovers, no lag); settling-grace still wins over the buffer for a young position; the data-gap fail-safe under all three UNKNOWN triggers; the resolver's offline-sentinel contract; byte-for-byte backward compatibility with `prev_tier=None`.

  Full design session: Opus `planner` pass, 2026-09-29 (memory `project_exit_discipline`'s 2026-09-29 entry has the complete reasoning). Do not re-derive this design from scratch on a future pickup — it's already fully specified above; only Phase 0.5's measurement result determines whether Phase 1 is worth building at all.

- **Mechanical-exit (`stop_breach`/`sell_signal`) intraday flicker — WATCHING, not started, opened 2026-09-29 from a live SPCX observation.** The owner saw SPCX show an Act-Today EXIT card, then clear, within roughly an hour of the same trading day. Traced the two Act-Today "mechanical exit" kinds built in `daily_briefing._act_today` (`daily_briefing.py:1748-1796`):
  - `stop_breach` — fires when live `Gap to Stop (%) ≤ 0` against the **current live quote**.
  - `sell_signal` — fires when the **live composite Score** (recomputed from the live price on every render/rerun) lands in the Sell zone (`Signal` contains "Sell"/"Avoid"/"Weak Hold").

  **This is a genuinely different mechanism from the deterioration tier above, not a variant of the same flicker question.** The deterioration tier's inputs (drawdown-from-daily-close peak, trend-vs-MA, below-MA count) only update once per trading day at cron time — it cannot round-trip inside an hour by construction, which itself is strong evidence the SPCX case was NOT a deterioration-tier flicker. `stop_breach`/`sell_signal` recompute from the live quote on every page render, so a thin/high-beta name crossing a stop or Sell-zone line and back on ordinary intraday noise is enough to produce exactly this symptom.

  **The gap this surfaces: `stop_breach`/`sell_signal` have zero historical capture of any kind.** `cron_runner.py`'s `exit_signals` batch write (`cron_runner.py:371-393`) only ever writes deterioration tiers (WATCH/TRIM/EXIT) + `RISK_OFF` — confirmed by reading the capture loop directly. There is no table, no `score_history`-equivalent, and no flicker-scan analog for the mechanical-exit path. Unlike the deterioration tier, this family has never had *any* measurement discipline applied to it.

  **Decision (2026-09-29): watch, don't build.** Same discipline as the 2026-08-01 CRWD deep-EXIT-shortcut anecdote (below) and the deterioration-hysteresis parking above — one instance on one thin, highly volatile name is not a pattern, and building a capture mechanism for a signal family that has never been measured before is a bigger, riskier lift than the deterioration-tier case (no existing table to reuse; would need a genuinely new intraday-granularity capture point, likely touching `_GATE_FILES`, since `stop_breach`/`sell_signal` currently have no write path at all).

  **Trigger to revisit:** a second/third instance of a `stop_breach` or `sell_signal` Act-Today card appearing and clearing within the same trading day — ideally on a different ticker, so a single illiquid name's ordinary noise isn't mistaken for a systemic issue. If it recurs, the first cheap step (mirroring Phase 0.5's approach) is likely a lightweight, read-only, session-scoped log of Act-Today membership changes within a day — NOT a new gate or hysteresis mechanism — before considering anything that touches decision logic.
- ~~**Material-add re-anchor wiring** (Phase 1.1)~~ — SHIPPED (see above).
- Action Log Phase B (log the trim/exit, stop re-nagging) — **DEFERRED**.
