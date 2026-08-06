# DRISHTA — Predictive Modeling: The Shadow Layer

**Date:** 2026-08-06
**Author:** Ajay Kumar
**Analysis model:** Claude Opus 4.8 (1M context)
**Status:** DESIGN — approved direction, pre-build. Nothing built or wired as of this doc. This is the design pass; the build is Phase 1 below.
**Reserved F-ID:** F-234 (assign at build, not before — nothing user-facing has shipped yet).

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

### 1.7 Constants to add (`stock_analyzer/constants.py`) — model params, NOT gates

`VOL_FORECAST_HORIZON_DAYS = 20`, `VOL_FORECAST_EWMA_LAMBDA = 0.94`, `PREDICTION_MIN_MATURED_N` (≈20). These are display/model parameters and gate nothing — but they still live in `constants.py` (hard rule #1) and still need a `docs/architecture.md` constants-table row at build (Definition-of-Done #1, mechanically enforced). Staging `constants.py` also trips the commit hook's Opus-review-citation requirement → consistent with §Governance below.

---

## Roadmap

- **Phase 1 (this design):** ledger + vol model + maturation cron + scorer + Model Lab. Quarantined. Touches no decision path.
- **Phase 2 (after Phase 1 has real matured rows):** a second, orthogonal target — **drawdown probability** (finally builds Pass #1 E3) or earnings-move magnitude — plus probabilistic scoring (Brier / reliability). Still quarantined.
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

*Fact vs. assumption declaration: shipped-feature and invariant references (§5.8, F-224, F-188, F-229, F-233, E1/E3, the constants-doc + antipattern + commit-hook gates) are drawn from CLAUDE.md, MEMORY.md, and the two next-evolution plan docs as of 2026-08-06. Table/column/module/constant names below are **proposed**, not yet in code — verify against HEAD at build time. No predictive-accuracy claim is made or implied here; establishing whether any model has skill is the entire point of Phase 1.*
