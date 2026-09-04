# DRISHTA — Predictive Modeling: The Shadow Layer

**Date:** 2026-08-06
**Author:** Ajay Kumar
**Analysis model:** Claude Opus 4.8 (1M context)
**Status (updated 2026-09-04):** Phase 1 SHIPPED and LIVE since 2026-08-06 as **F-234**, accruing real predictions daily via the EOD cron. **Checkpoint reached 2026-09-04:** a real bug was found and fixed first (commit `f0eac96` — `db.load_model_predictions()` was silently truncated by PostgREST's 1000-row cap, undercounting live-matured rows; a follow-up commit `5ef6f72` fixed the resulting MAE-precision display bug and added plain-English captions to the Model Lab page). Once trustworthy, the page showed the **first live-only skill reading: +1.6%, n=28** — crosses the `PREDICTION_MIN_MATURED_N`≈20 floor for the first time, meeting Phase 2's trigger ("Phase 1 has real matured rows"). **Phase 2 is now DESIGNED (below, §Phase 2 design — earnings-move magnitude) but deliberately NOT STARTED — the user chose to document it and pick it up later, no trigger date set.** Phase 3 scope is unchanged and untouched.
**F-ID:** F-234 (assigned at build, per the original reservation note below — no longer "not shipped").

---

## The decision (what this is, and the redline)

DRISHTA moves — deliberately and slowly — from a **retrospective** posture (grade what already happened) toward a **forward-looking** one (model what is *likely* to happen). The move is gated behind a hard discipline the user set explicitly:

> **Model → validate in the open → only *then* consider wiring in.** Phase 1 produces predictions and scores them against reality. It changes **no gate, no recommendation, no composite, no threshold.** A prediction earns the right to touch a decision only after its *own track record* proves it beats a naive baseline across a real window — and that wiring, if it ever happens, is a separate future phase with its own Opus design + review.

This is not an abandonment of the app's founding redline. It is the same discipline the app already applies everywhere — *prove the alpha before you trust the signal* (Engine Track Record F-229, Self Track Record F-233, the `composite_score_at_save` calibration deferred until ≥20 rows) — **pointed forward for the first time.** The predictive layer is born *with* its track record, quarantined from every decision surface, and it stays quarantined until the numbers earn otherwise.

---

## Relationship to existing invariants and forward features

**This does NOT violate any standing invariant — by construction:**

- **§5.8 (no stock-level point-estimate expected return)** — binding, from `next-evolution-strategy.md` Pass #1 and reaffirmed in `next-evolution-2026-08-05.md`. This layer predicts **risk, not direction**: forward *volatility*, forward *drawdown probability*, earnings-move *magnitude* — never "stock X will return +Y%." A vol/risk forecast is categorically not an expected-return point estimate. **Compliance is the whole reason the opening target is risk, not price.** (See Recommendation 2.)
- **"The app decides, it does not inform"** — untouched, because Phase 1 *decides nothing*. It's an owner-only, clearly-experimental measurement surface that consumes nothing and feeds nothing. If a signal ever graduates (Phase 3), it enters as a **tightening-only** input to an existing protective gate — it can add a suppression, never manufacture a buy. The deciding posture is *strengthened*, never loosened.

**Forward-looking building blocks already exist** (this composes/extends, it doesn't start from zero):

- **F-224 Outcome Range** — per-name block-bootstrap Monte Carlo. Already a forward *distribution* (not a point estimate — §5.8-safe precedent). The new part here is a **scored, persisted track record** of forward estimates, which Outcome Range does not keep.
- **F-188 Regime-Conditional Targets** and the **macro/regime detection** — forward-*state* calls. The regime tag they produce is reused here to stratify calibration (a model can't earn trust on calm weather alone).
- **Pass #1 Experimental Track E1 (Forward Portfolio Simulator) and E3 (Tail-Drawdown Probability)** — both still open. **E3 overlaps this layer's Phase 2 (drawdown probability)** — treat this doc as the concrete, harness-first way to finally build E3. E1 (portfolio scenario engine) is adjacent but out of scope here.

---

## The three design recommendations (the spine)

### Recommendation 1 — The Phase-1 deliverable is the *scoring harness*, not a model

The single most valuable artifact is not a forecast — it's a **prediction ledger + calibration scorer** that can tell you, honestly and always, whether a model beats doing nothing. Every prediction is logged *before* its outcome is known (inherently out-of-sample), matured by a cron, and scored **against a naive baseline** (persistence: "next period ≈ last period"). A model that can't beat persistence is noise in a lab coat, and without the baseline logged *at prediction time* you will never see that. The harness is the product; the first model is just its first tenant.

### Recommendation 2 — Predict *risk*, not *direction*

| Axis | Predictability | Fit |
|---|---|---|
| **Volatility / drawdown** | Genuinely forecastable — vol clusters (GARCH-family works) | Protective; §5.8-safe; if wired later, only *tightens* |
| **Correlation / regime persistence** | Forecastable — regimes persist | Extends F-230 correlation-under-stress |
| **Earnings-move magnitude** | Forecastable (magnitude ≠ direction) | Extends Catalyst Watch |
| **Directional price** | Where retail models die — overfit, look-ahead, regime break | Resurrects the exact temptation the redline kills — **excluded** |

**Opening target: 20-day forward realized volatility.** Cleanest to score, most protective, §5.8-compliant, and even in an eventual wired-in state it can only add protection.

### Recommendation 3 — Classical statistics, not LLMs or deep learning

- **LLMs stay in their lane** (narration/extraction). An LLM numeric forecast is noise wearing fluent prose — the worst input for a decision app. No LLM touches this layer.
- **No deep learning.** One user, one portfolio, limited history → an LSTM/transformer overfits instantly. Wrong tool for the data shape.
- **Parsimonious, interpretable, cross-sectional.** EWMA/GARCH-lite for vol; quantile regression / gradient-boosted trees on strictly point-in-time features later; trained across many tickers (cross-sectional) with walk-forward OOS. Interpretable models have knowable failure modes — exactly what a quarantined-under-audit layer needs.

---

## Phase 1 — detailed design (infra + first model, ship together)

A harness with no predictions is untestable, so Phase 1 ships the ledger, the first model, the maturation cron, the scorer, and the quarantined surface as one unit.

### 1.1 Prediction ledger — new Supabase table `model_predictions`

| Column | Type | Notes |
|---|---|---|
| `id` | serial / uuid PK | |
| `model_name` | text | e.g. `vol_forecast_ewma` |
| `model_version` | text | e.g. `v1` — bump on any model change so old rows aren't mixed with new |
| `scope` | text | `ticker` \| `portfolio` |
| `ticker` | text (nullable) | null/`PORTFOLIO` sentinel for portfolio-scope |
| `made_at` | timestamptz | ET-aware, via `market_time.now_et` |
| `horizon_days` | int | trading days |
| `target_metric` | text | e.g. `realized_vol_20d_annualized` |
| `predicted_value` | numeric | the model's forecast |
| `predicted_low` / `predicted_high` | numeric (nullable) | interval, when the model emits one |
| `baseline_value` | numeric | the **naive baseline's** forecast, logged *now* — never recomputed at scoring |
| `regime_at_make` | text (nullable) | regime tag at prediction time (for stratified calibration) |
| `features_snapshot` | jsonb | point-in-time inputs — the leakage audit trail |
| `realized_value` | numeric (nullable) | written at maturation |
| `scored_at` | timestamptz (nullable) | written at maturation |
| `abs_error` / `baseline_abs_error` | numeric (nullable) | written at maturation |

- **Backward-compat:** new table → no legacy backfill. Any *future* column follows the ALTER-TABLE drop-and-retry pattern (memory `feedback_session_state_reload_after_write` / the `thesis_source` precedent).
- **RLS:** `FOR ALL TO service_role`, same as every other table (hard rule #2). Loader in `db.py`.

### 1.2 First model — forward volatility (pure logic, `stock_analyzer/vol_forecast.py`)

- **Target:** 20-trading-day forward realized volatility, annualized, per **held ticker** + **portfolio**.
- **Model v1:** EWMA of squared log-returns (RiskMetrics λ = 0.94) → forward vol. No fitting, no free parameters beyond λ, well-understood failure modes. (GARCH(1,1) is a candidate *v2* only after v1's harness is trusted.)
- **Baseline:** trailing 20-day realized vol (persistence) — logged into `baseline_value` at prediction time.
- **Cadence:** once daily (piggyback the existing Home `build_portfolio_df` / cron path; no new fetch — reuse cached bars).

### 1.3 Maturation cron (extends `cron_runner.py`)

Daily: select rows where `made_at + horizon_days ≤ today_et` (trading-day aware, `market_time.today_et` / `is_trading_day`) and `realized_value IS NULL`; compute the realized metric from actual bars over the elapsed window; write `realized_value`, `abs_error`, `baseline_abs_error`, `scored_at`. Realized is by definition past data at maturation, so this step cannot leak.

### 1.4 Scoring harness (pure logic, `stock_analyzer/prediction_scoring.py`)

Read-only over the ledger. Per (`model_name`, `model_version`):
- **Point metrics:** MAE, RMSE.
- **Skill score:** `1 − MAE(model) / MAE(baseline)`. **> 0 ⇒ beats persistence.** This one number is the go/no-go for any future wiring.
- **`n_matured`** and a `PREDICTION_MIN_MATURED_N` floor below which skill is shown as "not yet meaningful."
- **Regime-stratified** breakdown (calm vs stress) so a calm-only record can't masquerade as robust.
- Probabilistic metrics (Brier, reliability curve) deferred to Phase 2 when a probability target lands.

### 1.5 Quarantined surface — `🔬 Model Lab` (owner-only)

- New page. **Top banner, always:** *"EXPERIMENTAL — these forecasts feed NO gate, NO recommendation, NO composite. Measurement only."*
- Owner-only via the existing read-only-viewer whitelist guard (memory `project_readonly_viewer`) — a read-only viewer never sees it.
- Renders: the per-model calibration table (skill score, MAE, n_matured, regime split) + a realized-vs-predicted scatter / reliability plot. Consumes nothing from `st.session_state`; publishes nothing. **It is a dead-end by design.**

### 1.6 Leakage guards (the section that actually matters)

Look-ahead bias is the #1 killer of retail models; the harness is worthless if it leaks. Non-negotiable:

1. **Every feature strictly as-of `made_at`.** `features_snapshot` is the audit trail — if a value isn't in it, the model didn't legitimately have it.
2. **Baseline logged at prediction time**, never recomputed at scoring (kills hindsight-baseline inflation).
3. **Split-adjustment consistency** between prediction-time bars and maturation-time realized calc (memory `project_split_recalc_deferred` — features touching `action`/adjusted prices must stay split-aware).
4. **Out-of-sample only.** The ledger is inherently OOS; **no in-sample metric is ever computed or shown.**
5. **Regime tag stored at make-time**, so calibration is always stratifiable and a benign-weather track record is visibly labeled as such.

### 1.6b Backfill — CONFIRMED 2026-08-06 (user)

DRISHTA itself is ~3 months old, but that does **not** bound how far back this layer can backfill — clarified with the user after initial confusion:

- **Per-ticker scope: backfill is bounded only by public market-bar history, not by DRISHTA's age.** A backfilled row with `made_at = 2026-03-15` uses only bars up to 3/15 and a target computed from bars after 3/15 — both now safely in the past, so this is a correct point-in-time backtest, not a leak. **v1 (`vol_forecast_ewma`) has no fitted parameters** (λ=0.94 is the fixed RiskMetrics constant, not tuned on this data), so there is no in-sample/backtest-leakage risk the way there would be for a *fitted* model (flag this explicitly for any Phase 2 model design — GARCH-MLE or gradient-boosted trees would need a true walk-forward holdout if backfilled). **Depth: `PREDICTION_BACKFILL_PERIOD = "5y"`**, matching the existing `MC_HISTORY_PERIOD` constant and fetch path (`data.fetch_price_history`, `stock_analyzer/monte_carlo.py:56`) — reuses a precedent, not a new fetch pattern. A multi-year window very likely already contains a real stress episode "for free," which matters for the Phase 3 gate below.
- **`PORTFOLIO` scope: bounded to known holdings history — genuinely ~3 months (verify earliest `trades` row before locking).** Forecasting the *portfolio's* aggregate forward vol requires actual historical weights, which only exist as far back as the logged trade history (`db.load_trades()`), not 5 years. Per-ticker rows get the deep backfill; `PORTFOLIO`-scope rows do not, and will stay thin until more calendar time passes — same maturation pattern as every other portfolio-level calibration in this app (`composite_score_at_save`, Engine Track Record).
- **Every row is tagged `source = 'live' | 'backfill'`.** The scorer (§1.4) reports skill both blended and **live-only** — the headline skill number must never be quietly 100%-backfilled with zero live validation behind it.
- **Overlapping-window caveat, surfaced not hidden.** Backfilling (or live-logging) daily `made_at` points against a 20-day horizon means consecutive rows share ~19 of 20 days of window — nominal `n_matured` overstates independent information. The backfill script strides sampled `as_of` dates (e.g. every `VOL_FORECAST_HORIZON_DAYS / 4` trading days, not every single day) to reduce artificial overlap, and the scorer reports raw `n_matured` alongside a stride-based effective-n note rather than letting row count alone look more convincing than it is.

### 1.7 Constants to add (`stock_analyzer/constants.py`) — model params, NOT gates

`VOL_FORECAST_HORIZON_DAYS = 20`, `VOL_FORECAST_EWMA_LAMBDA = 0.94`, `PREDICTION_MIN_MATURED_N` (≈20), `PREDICTION_BACKFILL_PERIOD = "5y"` (§1.6b, per-ticker scope only). These are display/model parameters and gate nothing — but they still live in `constants.py` (hard rule #1) and still need a `docs/architecture.md` constants-table row at build (Definition-of-Done #1, mechanically enforced). Staging `constants.py` also trips the commit hook's Opus-review-citation requirement → consistent with §Governance below.

---

## Roadmap

- **Phase 1 (this design):** ledger + vol model + maturation cron + scorer + Model Lab. Quarantined. Touches no decision path.
- **Phase 2 (after Phase 1 has real matured rows):** trigger MET 2026-09-04 (live-only skill +1.6%, n=28). Target decided 2026-09-04 — **earnings-move magnitude**, scoped to ledger + baseline only (no new model; see full design below in §Phase 2 design). Drawdown probability (Pass #1 E3) remains a candidate for a LATER pass if this one is picked back up and another target is wanted. Still quarantined.
- **Phase 3 (gated; months out; full `planner` + `reviewer`):** **only if** skill score > 0 across a real window that includes **≥1 stress episode** and `n_matured ≥` a to-be-set floor → propose wiring **one** validated risk signal into **one** existing protective gate as a **tightening-only** input. Never loosens a gate; never manufactures a buy. This is where §5.8 and the whole redline get re-litigated with fresh explicit approval — not an implied green light from Phase 1.

---

## Governance (how this routes through the model tiers)

- **This design doc = the Opus design pass** (main session is Opus 4.8; the `planner` pin exists to guarantee Opus-grade design *even when the session is cheaper* — that condition is already met here). **Phase 3's policy design gets its own dedicated `planner` pass regardless** — do not treat this doc as covering it.
- **Phase 1 build → `implementer` (Sonnet)**, design being decided here. **BUT Phase 1 gets an Opus `reviewer` pass before ship** — not because it touches a gate (it doesn't), but on **DB-write / data-integrity** grounds: the leakage-correctness of the ledger + scorer is precisely where a silent bug would poison the entire future track record. Cite the review in the commit body (also mechanically required once `constants.py` is staged).
- **Phase 3 build → full `planner` (design) + `reviewer` (pre-ship)**, non-negotiable (hard rule #4 — it changes a gate).

---

## Forks — CONFIRMED 2026-08-06 (user)

1. **Opening target — 20-day forward volatility.** ✅ Confirmed. (Cleanest to score, most protective, §5.8-safe.) Drawdown-probability and correlation-regime remain the Phase-2 candidate pool.
2. **Surface — full quarantine in owner-only `🔬 Model Lab`.** ✅ Confirmed. A live "predicted range" annotation near existing analytics was explicitly declined on anchoring-risk grounds — stay quarantined until the skill score earns otherwise.

---

## Phase 2 design — earnings-move magnitude (DESIGNED 2026-09-04, NOT STARTED)

**Author of this design pass:** `planner` (Opus 4.8, 1M context), run 2026-09-04 after the Phase 1 checkpoint. Verdict: **PROCEED — conditionally**, with the model-vs-baseline differentiator problem below flagged as the load-bearing risk. The user reviewed the design, made three scoping decisions (all below), then chose to **document and defer** rather than build now — this section exists so a future session can pick this up without re-deriving any of it.

### Target definition

Predict the **absolute, unsigned, single-session close-to-close price move across the earnings print, in percent**:

```
realized_value = | close_after_print / close_before_print − 1 | × 100
```

`close_before_print` / `close_after_print` are the split-adjusted closes of the sessions immediately bracketing the report, chosen per BMO ("before open," so `close_before` = D−1, `close_after` = D) vs AMC ("after close," so `close_before` = D, `close_after` = D+1). Chosen over "max intraday move" (needs intraday bars the app's data layer doesn't carry) and "realized vol over [print, print+N]" (that's just the vol model again with an event-anchored window, not an orthogonal second target). **§5.8-safe by construction** — unsigned magnitude only, never a directional price call, same category as the Phase 1 vol forecast.

### Scope decision (user, 2026-09-04): ledger + baseline only — no new "model" yet

The vol model's persistence baseline ("assume next 20 days ≈ last 20") has no clean daily analogue for a quarterly event, and a naive "average of recent prints" baseline plus a v1 model that is *also* just averaging recent prints would make skill ≈ 0 by construction — measuring nothing. The app also has no options-implied-vol feed, which would have been the honest real differentiator. Rather than invent a placeholder model, the scoped-down plan **reuses the app's own existing `earnings_advisor._estimate_move` heuristic (VaR95×3, sector-adjusted, already live on the Earnings Playbook and already driving real pre-earnings trim recommendations) as `predicted_value`**, and a **trailing-K historical average of the ticker's own past realized earnings moves as `baseline_value`**. This reframing turns Phase 2 into a genuinely useful, non-degenerate measurement — *"does the heuristic the app already uses to recommend trims actually beat a naive 'assume it moves like it usually has' guess?"* — validating an existing live feature instead of measuring a strawman model. **No new forecasting model is designed or built in this scope.**

Parameters (defaults proposed by `planner`, not yet finalized against code — confirm at build time):
- **K = 6** prior prints, using the **median** (not mean — more robust to one blow-out quarter).
- **`EARNINGS_MOVE_LEAD_DAYS` = 3** trading days before the scheduled print — when a prediction row is written.

### Two more scope decisions (user, 2026-09-04)

- **Live-only — no backfill.** Historical earnings-date accuracy (yfinance's historical earnings dates are approximate/gappy for older prints) makes backfill a real risk of poisoning the ledger with mis-dated windows, not a free cushion the way 5-year vol backfill was (that only needed price bars, which are reliable for years; this needs *earnings dates*, which aren't). Accepted tradeoff: ~4-6 months minimum before crossing the live-only reporting floor (vs. vol's ~4 weeks), because live accrual is only ~52-68 events/year across held tickers (quarterly, not daily) — seasonally clustered around earnings months, not smooth.
- **Fully retrospective — no "upcoming, not yet matured" live preview.** A preview like *"NVDA earnings in 12 days — predicted move: X%"* was considered (it would leverage Catalyst Watch's existing earnings-date tracking) but declined because it re-opens the exact anchoring-risk redline Fork 2 (below) already closed for the vol model — a live forecast on screen before the outcome is known, even on this quarantined page. Model Lab's earnings-move section shows **matured, scored predictions only**, same posture as the vol model.

### Schema fit — no DDL change required

Every existing `model_predictions` column maps cleanly: `predicted_value` → the `_estimate_move` heuristic's output at make-time; `baseline_value` → the trailing-K median; `realized_value` → the actual move; `features_snapshot` (jsonb) → holds `event_date`, `bmo_amc`, `days_until_at_make`, and the K historical moves the baseline was computed from. `horizon_days` becomes advisory-display only (a nominal trading-day count to the expected print) — **the real maturation trigger is event-driven, not `made_at + horizon_days`**, so the maturation cron branches by `model_name`: the existing `made_at + horizon_days ≤ today` rule stays the *vol* path; a new event-date-driven rule is the earnings path. This is a `cron_runner.py` logic branch, not a schema change. (A first-class `event_date DATE` column would make "find overdue events" a cheap indexed query instead of a jsonb reach — deliberately deferred; revisit only if that scan becomes a real cost at this table's tiny scale.)

### Leakage guards specific to this target (in addition to every Phase 1 guard, §1.6)

1. **The post-print bar is the answer, never a feature** — `features_snapshot` must only contain bars strictly before `close_before_print`.
2. **Frozen event date** — the scheduled `event_date` used at make-time is frozen in `features_snapshot`. A later reschedule must **invalidate/withdraw** the row, never silently re-key it to the new date using knowledge that only exists after make-time.
3. **Split-across-print** — both `close_before` and `close_after` must be on a consistent split-adjusted basis (`project_split_recalc_deferred`); a split announced *at* earnings must not register as a spurious ~50% "move."
4. **Baseline excludes the scored print** — the trailing-K window is strictly prints *before* `made_at`, never inclusive of the event being measured.
5. **Survivorship** — a ticker with fewer than K historical prints (recent IPO, spinoff, newly-added holding) is **excluded**, never defaulted to a sector constant that would silently inflate apparent coverage.

### Visualization — second section on the same 🔬 Model Lab page

Not a new tab or page. A second collapsible section below the existing Forward Volatility Forecaster, reusing the skill banner / regime breakdown / live-only-skill caption / offline-vs-warming-vs-populated three-state scaffolding verbatim (`load_model_predictions("earnings_move_v1")` alongside the existing `"vol_forecast_ewma"` call). What's different:
- **Chart:** replace the predicted-vs-realized scatter (too sparse at ~50 events/year) with a **per-event ordered dot/bar plot** — three marks per matured print (predicted / baseline / realized), ordered by date, so a per-event win/loss is visible at low n instead of hidden in an aggregate.
- **"Recently matured" table** gains a `model_beat_baseline?` ✓/✗ column (`abs_error < baseline_abs_error` per row) — at low n, a visible per-event tally is more honest than a single skill %.
- No "upcoming" preview section (see scope decision above).

### Relationship to `earnings_advisor._estimate_move`

Under this scoped-down design, the relationship is closer than a mere side-by-side comparator: **`_estimate_move`'s live output literally becomes the thing being measured** (`predicted_value`). This is deliberate and safe because Phase 2 stays read-only/quarantined — it observes and scores an already-shipped heuristic's real-world calibration without touching it. **Never wire this back** — whether a validated result should ever change `_estimate_move`'s formula is a Phase 3 question (full `planner` + `reviewer`, fresh explicit approval), not implied by measuring it here.

### Ordered build chunks (for whenever this is picked up)

1. Add constants (`EARNINGS_MOVE_LEAD_DAYS`, `EARNINGS_MOVE_BASELINE_K`, a `target_metric` string, reusing `PREDICTION_MIN_MATURED_N` — do not fork it) — `stock_analyzer/constants.py` + `docs/architecture.md` constants table. Trips the commit hook's Opus-citation requirement.
2. New pure module (e.g. `stock_analyzer/earnings_move_forecast.py`) — trailing-K median baseline computation, the split-safe close-to-close realized-move calc, all pure and unit-tested at the boundaries listed below.
3. Extend `cron_runner.py`'s maturation path with the event-driven branch (frozen event date, BMO/AMC window selection, survivorship exclusion) — **DB-write/data-integrity → mandatory Opus `reviewer` pass**, same as Phase 1.
4. Generalize `prediction_scoring.py`'s `effective_n_note` so the overlap discount (tuned for daily, 20-day-overlapping vol predictions) doesn't spuriously deflate n for non-overlapping quarterly events (`prediction_scoring.py` is DB-integrity-adjacent → reviewer).
5. Add the second Model Lab section (`app.py`, reuse scaffolding + new per-event chart + new table column).
6. Docs sync in the same session, all 7 Definition-of-Done steps (requirements.md F-ID, architecture.md module + constants rows, shipped-log, this doc's own Status line, memory).

### Tests the build must include (invariants this design asserts)

- No post-print leakage into `features_snapshot`/baseline inputs.
- A rescheduled print does not silently re-key to the new date.
- BMO vs AMC close-before/close-after session selection is correct at the boundary.
- A split between make-time and maturation does not register as a spurious move.
- The baseline excludes the scored print itself, and excludes tickers with < K prior prints (dropped, never defaulted).
- The `None` (not 0) "withheld below floor" contract holds for `earnings_move_v1` exactly as it does for `vol_forecast_ewma`.
- `effective_n_note` for non-overlapping events reads close to raw n (the overlap discount must not misfire on a quarterly-not-daily cadence).

---

*Fact vs. assumption declaration: shipped-feature and invariant references (§5.8, F-224, F-188, F-229, F-233, E1/E3, the constants-doc + antipattern + commit-hook gates) are drawn from CLAUDE.md, MEMORY.md, and the two next-evolution plan docs as of 2026-08-06. Table/column/module/constant names below are **proposed**, not yet in code — verify against HEAD at build time. No predictive-accuracy claim is made or implied here; establishing whether any model has skill is the entire point of Phase 1.*
