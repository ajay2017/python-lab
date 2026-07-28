# Test Results Log — DRISHTA

A running log of `pytest tests/` outcomes, appended each time the suite is
run, so pass/fail count and coverage drift are visible over time without
re-deriving them from scratch. See [docs/testing-strategy.md](testing-strategy.md)
for what this suite covers and doesn't, and [docs/plans/test-automation.md](plans/test-automation.md)
for the full batch-by-batch build history.

**How to update this log:** run the two commands below, transcribe the real
numbers (never estimate/recall — this file is held to the same
zero-hallucination bar as any other doc), and add a new dated entry at the
top of §2. Keep §1 ("Latest run") in sync with the newest entry.

```
pytest tests/ -v
pytest tests/ --cov=stock_analyzer --cov-report=term-missing -q
```

---

## 1. Latest run — 2026-07-28 (post-roadmap health check, batch 5: `portfolio_health.py`)

**600 passed, 0 failed, 0 skipped** (`pytest tests/ -v`; with `--cov`
active: 8.90s). Python 3.14.6, pytest 8.4.2, pytest-cov 5.0.0, in the local
`.venv`.

### Per-file breakdown

| Test file | Tests | Module(s) under test |
|---|---|---|
| `test_constants_invariants.py` | 12 | `constants.py` (ordering/consistency invariants, not literal mirrors) |
| `test_scoring.py` | 8 | `scoring.py` |
| `test_concentration.py` | 16 | `concentration.py` |
| `test_exit_advisor.py` | 32 | `exit_advisor.py` |
| `test_portfolio.py` | 28 | `portfolio.py` |
| `test_risk_advisor.py` | 28 | `risk_advisor.py` |
| `test_daily_briefing.py` | 35 | `daily_briefing.py` (incl. `_cross_reference()`) |
| `test_signal_reconciliation.py` | 26 | `signal_reconciliation.py` |
| `test_structural_scanner.py` | 19 | `structural_scanner.py` (incl. `blast_radius()` backfill) |
| `test_thesis_red_team.py` | 30 | `thesis_red_team.py` (Phase 1 `compute_erosion_score()` backfill + Phase 2 Haiku contract) |
| `test_valuation.py` | 31 | `valuation.py` |
| `test_decision_bucket.py` | 27 | `decision_bucket.py` |
| `test_watchlist_advisor.py` | 35 | `watchlist_advisor.py` (incl. `_portfolio_risk_gate()`) |
| `test_risk.py` | 43 | `risk.py` (position sizing, ATR stops, Sharpe/Sortino/VaR/beta) |
| `test_macro_playbook.py` | 67 | `macro_playbook.py` (Pre-Event Macro Playbook: PROTECT/WATCH/HOLD/OPPORTUNITY action classifier, post-event scenario classification) |
| `test_headless_alert_engine.py` | 57 | `headless_alert_engine.py` (cron protective-alert / morning-picks / EOD engine: `_build_context`, `compute_protective_alerts`, `compute_morning_picks`, `compute_eod`, `_assess_pullback`) |
| `test_portfolio_health.py` | 92 | `portfolio_health.py` (Portfolio Construction Health Score: 5 sub-scores + A-F grade, Portfolio Dynamics tenure/cohort/alignment) |
| **Total** | **600** | |

### Line coverage of the 15 targeted modules

Via `pytest --cov=stock_analyzer --cov-report=term-missing`. **Read this with
the design principle in mind: the suite targets boundary/golden-value
behavior on the highest-blast-radius PURE functions, not exhaustive coverage**
— a module sitting well below 100% here usually means an untested
pandas/I-O extraction layer around an already-tested pure core (e.g.
`exit_advisor.assess_holding()`, `portfolio.build_portfolio_df()`), not a gap
in the tested logic itself. Batch-by-batch scope is in
`docs/plans/test-automation.md`.

| Module | Stmts | Miss | Cover |
|---|---|---|---|
| `constants.py` | 219 | 0 | **100%** |
| `scoring.py` | 15 | 0 | **100%** |
| `concentration.py` | 47 | 0 | **100%** |
| `valuation.py` | 64 | 0 | **100%** |
| `watchlist_advisor.py` | 109 | 0 | **100%** |
| `signal_reconciliation.py` | 86 | 4 | **95%** |
| `decision_bucket.py` | 75 | 2 | **97%** |
| `risk_advisor.py` | 175 | 13 | **93%** |
| `risk.py` | 171 | 7 | **96%** |
| `macro_playbook.py` | 252 | 13 | **95%** |
| `headless_alert_engine.py` | 252 | 26 | **90%** |
| `portfolio_health.py` | 242 | 11 | **95%** |
| `thesis_red_team.py` | 84 | 11 | 87% |
| `structural_scanner.py` | 144 | 62 | 57% |
| `exit_advisor.py` | 191 | 131 | 31% |
| `portfolio.py` | 427 | 300 | 30% |
| `daily_briefing.py` | 773 | 546 | 29% |

**Whole-`stock_analyzer/` total: 13,794 stmts, 11,087 missed, 20%** (up from
18% the prior day — `portfolio_health.py` moving from 0% to 95%). Still
dominated by ~61 modules with zero tests at all — ranked remaining gaps with
real decision/gate logic: `rebalancer.py`, `signal_hysteresis.py` (tied to the
still-parked deterioration-hysteresis queue item), `position_lifecycle.py`,
`tax_advisor.py`. Not a target to chase for its own sake per
`docs/plans/test-automation.md`'s "golden-value regression, not
coverage-chasing" principle. Track this section mainly to notice a SUDDEN
drop in a targeted module (a signal something broke), not to push the
whole-package number up for its own sake.

**Correction (2026-07-27):** an earlier same-day audit mislabeled
`macro_playbook.py` as containing `compute_protective_alerts()`/
`_assess_pullback()`/`PULLBACK_ALERT_INDEX_PCT` (the pullback-awareness
feature's threshold logic). Verified against source before writing this
batch's tests: those actually live in `headless_alert_engine.py`, a
DIFFERENT and still zero-coverage module. `macro_playbook.py` is the
Pre-Event Macro Playbook (NFP/CPI/FOMC/GDP/PPI/Retail-Sales scenario
analysis with its own real PROTECT/WATCH/OPPORTUNITY thresholds, several
explicitly reconciled against `constants.py`'s `SINGLE_NAME_CEILING`/
`COMPOSITE_HOLD`) — a different, equally-real decision-adjacent module,
tested as found. `headless_alert_engine.py` remains an open gap.

**`risk.py`'s batch found and fixed a real production bug, not just a
coverage gap** (see the dated history entry below for the full writeup):
`sharpe_ratio()`/`sortino_ratio()`/`compute_portfolio_risk_metrics()`'s
"no volatility → 0.0" fallback used an exact `std == 0` check, which missed
~1e-19-scale floating-point noise from averaging the risk-free-rate constant
across many identical rows — a flat/halted position could show a Sharpe of
±3.4e16 instead of 0.0. Fixed with a `_ZERO_VOL_EPS = 1e-9` tolerance check.
Opus-reviewed: SHIP, 0 blocking.

**`headless_alert_engine.py`'s batch also found and fixed a real production
bug** (see the dated history entry below): its `_f()` float-parsing helper
returned NaN itself instead of falling back to `default` when a "Stop
Unavailable" ticker's `gap_to_stop=None` got pandas-coerced to NaN by a
mixed-dtype column — the stop-breach loop's `gap is None` guard never caught
it, and NaN > 0 is also False, so a ticker whose stop was unknown/uncomputable
would fire a bogus "SELL — Stop Breached" cron alert. Fixed by making `_f()`
NaN-aware (`math.isnan` check). Opus-reviewed: SHIP, 0 blocking, 2
non-blocking (log-only NaN-vs-None guards elsewhere in the same file,
outside the alert-firing path — not yet actioned, see history entry).

**`portfolio_health.py`'s batch found and fixed a real UI-copy logic bug**
(see the dated history entry below): the Portfolio Health "Improvements"
card's concentration callout, when BOTH single-name and sector concentration
were meaningfully elevated (>60% of their respective caps), was supposed to
show both details but instead duplicated whichever one fired first — the
"is this dimension already shown" check tested for the literal substring
`"worst_name"`/`"worst_sector"` in the rendered HTML text, which never
appears there (the text contains the actual ticker/sector value, not the
dict key name), so the check was always true and the sector detail was
silently dropped whenever the name was the dominant driver. Fixed by
tracking which one was added with explicit booleans instead of string
sniffing. Not Opus-reviewed — this only affects the supporting detail line
under an already-computed recommendation (the sub-score, grade, and action
text are all unaffected), not a gate/scoring formula per CLAUDE.md hard
rule #4.

---

## 2. History

*(Newest first. Add a new entry above this line each time the suite is run
and the result is worth recording — at minimum, after any batch/module
addition or whenever a run fails.)*

### 2026-07-28 — `portfolio_health.py` backfilled (92 tests), found + fixed a real UI-copy duplication bug, 600/600 passing

Continuation of the post-roadmap health check, next module on the ranked
priority list. `portfolio_health.py` is pure computation (no I/O, no
Streamlit) — the Portfolio Construction Health Score (5 sub-scores:
concentration, sector_balance, diversification, factor_exposure,
signal_integrity → weighted average → A-F grade) and Portfolio Dynamics
(per-position tenure/cohort/engine-alignment for the Portfolio Overview
page). 92 tests: each sub-score helper's None-guards and boundary math
(`_concentration_score()`'s three-zone name/sector scoring incl. the
`Gate Weight (%)` column fallback; `_sector_balance_score()`'s Shannon-entropy
calc incl. the 2-sector 55-point cap; `_diversification_score_sub()`'s
avg-corr rescaling and div_score_val fallback; `_factor_exposure_score()`'s
severity-tier base score + high-beta-share penalty tiers; `_signal_integrity_
score()`'s NaN-score exclusion from the weighted average), `_build_specific()`'s
per-dimension callout text, `_build_improvements()`'s top-2-worst selection
and low/mid bucket split, `compute_health_score()`'s end-to-end averaging
and "?" no-data grade, and `compute_portfolio_dynamics()`'s FIFO-aware
tenure lookup via `_build_open_lots()` (incl. the re-entry-resets-the-clock
case), cohort boundaries (Fresh <1mo / Growing 1-6mo / Established >6mo),
the BUY/HOLD/WATCH/EXIT verdict ladder against `COMPOSITE_BUY`/`_HOLD`/
`_SELL`, and vitality/alignment aggregation.

**Found and fixed a real bug, not a test-writing mistake:** `_build_specific()`'s
concentration callout has a "show both name and sector when both are
meaningfully elevated (>60% of their cap)" fallback, intended to fire when
only one of the two was added by the primary branches above it. That
fallback checked `"worst_name" not in parts[0]` / `"worst_sector" not in
parts[0]` — testing for the literal substring `"worst_name"`/`"worst_sector"`
(the dict key names) inside the ALREADY-RENDERED HTML text, which never
contains those literal strings (it contains the actual ticker/sector value
interpolated in). That check is therefore always true, so whenever the
single-name detail fired first (`name_ratio >= sector_ratio`), the fallback
re-added a DUPLICATE name line instead of the sector detail the user was
supposed to see — e.g. a name at 14%/15% cap and its sector at 30%/35% cap
(both >60% elevated) rendered "AAPL at 14% ... AAPL at 14%" instead of ever
surfacing the sector info. The mirror case (sector firing first) happened to
work by coincidence, since adding "name" second was the intended completion
either way. **Fixed** by tracking which detail was actually added with
explicit `added_name`/`added_sector` booleans instead of the broken string-
containment check. Not Opus-reviewed: this only changes the supporting
detail line under an already-computed recommendation card — the sub-score
math, grade, and action text are untouched, so it isn't a gate/scoring-
formula change under CLAUDE.md hard rule #4. Now 95% covered (242 stmts,
11 missed).

### 2026-07-27 — `headless_alert_engine.py` backfilled (57 tests), found + fixed a real NaN/stop-breach bug, 494/494 passing

Continuation of the same-day post-roadmap health check, next module on the
ranked priority list — and the module the `macro_playbook.py` batch's
correction had pointed at: this is the actual home of
`compute_protective_alerts()`/`_assess_pullback()`/`PULLBACK_ALERT_INDEX_PCT`
(the pullback-awareness feature), plus `compute_morning_picks()` (the Grow
Today equivalent for the cron) and `compute_eod()` (the Today's-P&L snapshot
+ reactive pullback read). Unlike prior batches this module hard-imports
`streamlit` (via `db.py`) and `vaderSentiment` (via `bundle_loader.py` →
`sentiment.py`) at module load time even though it never touches the
Streamlit runtime itself — the dev venv is deliberately bare of both (see
`project_python314_blocker` memory), so the test file stubs them into
`sys.modules` before import; every real call into `db.*`/`load_bundle` is
mocked directly in the tests, so neither stub's behavior is ever exercised.
57 tests: `_f()`/`_vix_level()`/`_assess_pullback()` as pure-ish units, then
`_build_context()`'s ok/errors short-circuiting (no DB, load_holdings
exception, no holdings, all bundles fail to load, empty port_df, rfr/SPY
fetch failures that degrade gracefully) via mocked `db`/`fetch_spy`/
`fetch_vix`/`load_bundle`/`build_portfolio_df`/`compute_portfolio_risk_metrics`/
`run_scenario`/`assess_fragility`, then `compute_protective_alerts()`'s
stop-breach > deterioration-EXIT > risk-off priority and single-surface
dedup (a ticker already alerted via stop breach is excluded from both the
EXIT-tier check and `assess_risk_off_derisk`'s `exclude_tickers`), the
analyst-target-snapshot stale-skip, and `compute_morning_picks()`'s
tone-gated composite bar (`COMPOSITE_BUY` bull / `COMPOSITE_BUY_FLAT_DAY`
flat / no bar on bear) and diagnostic counts, and `compute_eod()`'s
snapshot-row filtering.

**Found and fixed a real bug, not a test-writing mistake:** `portfolio.py:302-303`
explicitly sets `gap_to_stop = None` for a ticker whose stop couldn't be
computed ("Stop Unavailable" — reachable for real, e.g. a beaten-down name
whose ATR is large enough that `atr_stop_loss()` in `risk.py` returns a
non-positive stop). Once that `None` sits in the same `port_df` DataFrame
column as any other ticker's real numeric gap, pandas silently promotes the
whole column to float64 and coerces the `None` to `NaN`. `_f()`'s original
`try: return float(v) except (TypeError, ValueError): return default` does
NOT raise on `float(nan)`, so it returned `NaN` itself instead of `default`.
Downstream, `compute_protective_alerts()`'s stop-breach loop does
`gap = _f(row.get("Gap to Stop (%)")); if gap is None or gap > 0: continue`
— `NaN is None` is False and `NaN > 0` is also False, so the row fell
through and fabricated a bogus "SELL — Stop Breached" cron alert for a
ticker whose stop was actually unknown, not breached. **Fixed** at the root
(the shared `_f()` helper, not the one call site) by adding `import math`
and changing it to `f = float(v); return default if math.isnan(f) else f`.
Per CLAUDE.md hard rule #4 (this changes whether a protective SELL fires):
**Opus reviewer: SHIP, 0 blocking.** Confirmed reachability (traced
`gap_to_stop=None`'s producer in `portfolio.py` back to a real negative-ATR-
stop case in `risk.py`, not a contrived scenario), confirmed the fix is
complete across all 8 of the file's `_f()` call sites (one is a bonus fix —
`int(_f(shares, 0) or 0)` previously crashed with `ValueError` on a NaN
Shares value; now correctly falls back to `0`), and confirmed no other
`_f()` caller's behavior regresses. **Two non-blocking follow-ups flagged by
the review, not yet actioned** (both outside the alert-firing path so no
live decision is affected today): `headless_alert_engine.py:237`'s
`if target_mean is None: continue` (analyst-target-snapshot capture) doesn't
catch a NaN `analyst_target` from yfinance — log-only Phase 1, but could
poison a future day-over-day comparison; and `:155`'s `if beta is not None:`
admits a NaN beta into `run_scenario`/`assess_fragility` (fragility-only,
harmless today). Added regression tests for both the direct `_f(NaN)` case
and the full pandas-coercion path through `compute_protective_alerts()`. Now
90% covered (252 stmts, 26 missed — remaining gaps are mostly redundant
exception-branch pairs already exercised once each, e.g. the SPY-1y/vix/
bundle-load/material-add-age/run_scenario try/excepts, plus a couple of
`compute_morning_picks()`'s local-import exception branches).

### 2026-07-27 — `macro_playbook.py` backfilled (67 tests), 437/437 passing

Continuation of the same-day post-roadmap health check, next module on the
ranked priority list. **Correction found before writing any tests:** the
originating audit described this module as containing the
pullback-awareness feature's `compute_protective_alerts()`/
`_assess_pullback()`/`PULLBACK_ALERT_INDEX_PCT` — verified against actual
source first (reading the whole file, not trusting the prior description)
and found that's wrong; those live in `headless_alert_engine.py`, a
different, still-untested module. `macro_playbook.py` is actually the
Pre-Event Macro Playbook: for each upcoming HIGH-impact macro event
(NFP/CPI/FOMC/GDP/PPI/Retail Sales), classifies each held position into
PROTECT/WATCH/HOLD/OPPORTUNITY via `_pre_event_action()`, using thresholds
several of which are explicitly imported from `constants.py`
(`SINGLE_NAME_CEILING`, `COMPOSITE_HOLD`, `COMPOSITE_BUY`) rather than
duplicated as literals — real, decision-adjacent logic, tested as found.
67 tests covering the action classifier's full branch order (Sell/Strong
Sell always PROTECT-HIGH regardless of other fields; low-score+bear-exposure
and oversized-position+bear-exposure PROTECT-HIGH; deep-loss-near-event
PROTECT-MEDIUM gated on a ≤7-day window; OPPORTUNITY gated on score+Buy
signal+bull-exposure+≤14-day window; the WATCH-MEDIUM vs WATCH-LOW split;
the HOLD fallback), `_build_rationale()`/`_action_detail()`/
`_post_event_rules()`'s narrative branches, `classify_scenario()`'s
higher-is-bull vs lower-is-bull post-event classification (including the
FRED-string parsing via `_parse_number()` and the `implied_base` fallback),
and `build_event_playbooks()`/`build_post_event_analysis()`'s event
filtering (HIGH-impact only, future-dated only, known-scenario only),
per-position sector-exposure filtering, exposure-level bucketing, and
sort ordering. 3 of the initially-written tests failed on the first run —
all 3 were the AUTHOR's (this session's) own fixture-math errors (picked a
sector/event combination whose actual bear-move value didn't land in the
threshold band the test intended), not bugs in the source; corrected
against the real `_SCENARIOS` dict values and re-verified. No production
bug found this batch (unlike the `risk.py` batch immediately before it).
Now 95% covered. `risk.py`'s test coverage brought forward from the
previous history entry: 96%.

### 2026-07-27 — `risk.py` backfilled (43 tests), found + fixed a real Sharpe/Sortino bug, 370/370 passing

Continuation of the same-day post-roadmap health check, next module on the
ranked priority list: `risk.py` (position sizing, ATR stops,
Sharpe/Sortino/VaR/beta — the highest-priority remaining zero-coverage
module with real decision logic). While writing the flat-price edge-case
test, found a genuine production bug, not a test-writing mistake: for a
truly flat/no-volatility position, `sharpe_ratio()`'s `excess.std()`
computed a tiny non-zero float (~8e-20, confirmed empirically at
`n=29/30/50/100` rows — but exactly `0.0` at `n=14/252`, an inconsistent
floating-point artifact of averaging the repeating-binary-fraction
risk-free-rate constant across many identical rows) instead of exact
`0.0`. The code's `if std == 0` exact-equality check missed this noise and
divided by it, blowing the ratio up to roughly ±3.4e16 instead of the
clearly-intended "0.0, no signal" fallback. The same root cause also hit
`sortino_ratio()` and a third, structurally-identical inline check inside
`compute_portfolio_risk_metrics()` (its own portfolio-level sortino calc)
— confirmed to reproduce there too before fixing. **Fixed** with a new
module-level `_ZERO_VOL_EPS = 1e-9` tolerance constant (a floating-point
numerical-stability guard, not an investment-policy threshold — deliberately
kept in `risk.py`, not `constants.py`) replacing the three exact
comparisons. Two similar-looking checks elsewhere in the same file —
`beta_vs_market()`'s `mkt_var == 0` and `compute_portfolio_risk_metrics()`'s
`std_ret > 0` — were traced and confirmed NOT vulnerable (they guard
variance of RAW returns, not `returns - a_repeating_fraction`, so a flat
price gives them an exact `0.0` with no accumulation noise) and were
deliberately left unchanged. Per CLAUDE.md hard rule #4 (this touches a
scoring formula): **Opus reviewer: SHIP, 0 blocking** — confirmed the
epsilon's bounds (~1e11x above the noise floor, ~4+ orders of magnitude
below any real volatility including an ultra-short treasury ETF), confirmed
the fix is complete (grepped the whole repo; `risk.py` is the sole compute
site, everything else only consumes the already-computed value), and
confirmed the tests pin the fix for the right reason (not a coincidence —
two of the new tests' fixtures had to be rewritten mid-review after the
original smooth-exponential-compounding fixtures turned out to have
genuinely zero return-variance by construction, coincidentally passing
pre-fix for the wrong reason: a huge blown-up ratio that still happened to
have the correct sign). **Bonus, unplanned repair:** this fix also corrects
a downstream symptom in `risk_advisor.py`'s HIGH/MEDIUM Sharpe alert
ladder — a flat/halted position's garbage ±3.4e16 pre-fix value could land
in either risk band at random; post-fix it correctly reads `0.0` and lands
HIGH ("risk not being rewarded"). **New follow-up item flagged by the
Opus review, not yet actioned:** `risk_advisor.py:256-257` hardcodes the
Sharpe alert-ladder thresholds (`< 0.8` MEDIUM / `< 0.4` HIGH) inline
instead of importing from `constants.py` — a genuine CLAUDE.md hard-rule-#1
concern, pre-existing and out of scope for this commit, noted for a
separate follow-up.

### 2026-07-27 — Post-roadmap health check: 3 zero-coverage modules backfilled, 327/327 passing

Prompted by a same-day post-ship audit (after the Agentic Intelligence
Roadmap closed) that found only 10 of 81 `stock_analyzer` modules had any
real test coverage. Ranked the gaps and prioritized 3 with actual
decision/gate logic: `valuation.py` (one of the 4 composite scoring
pillars — untested despite being load-bearing), `watchlist_advisor.py`
(contains `_portfolio_risk_gate()`, an actual gate that can downgrade an
ENTER_NOW call), and `decision_bucket.py` (the documented source-of-truth
for Act Today vs Awareness bucketing, including the "SPCX split-brain"
reduce-vs-hold reconciliation). All three now at 97–100% coverage. `pytest
tests/ -v`: 327 passed (up from 185 same day, +142 across this batch plus
the earlier same-day `structural_scanner`/`thesis_red_team` additions — see
§1's per-file table). No regressions; no test failures at any point.
**One real product-copy gap found while writing the `watchlist_advisor`
tests, not fixed (flagged for discussion, not a mechanical bug):** a stock
priced exactly at its entry zone but lacking a validated R:R (no target
price) falls through to a NEAR_ENTRY card reading "Approaching Entry Zone
(+0.0% above zone)" / "watch for a small pullback" — misleading, since
price isn't approaching the zone, it's already there; the real blocker is
a missing price target. Not a wrong-buy risk (conservatively downgraded
either way), but the displayed reasoning doesn't match the actual
situation. Needs a product decision (a distinct message/state), not a
one-line fix.

### 2026-07-27 — Batches 1–6 complete, 185/185 passing

First recorded run. Batches 1–6 of `docs/plans/test-automation.md` all
shipped this session (constants/scoring/concentration → exit_advisor →
portfolio → risk_advisor → daily_briefing buy-candidate funnel +
`high_beta_share` → signal_reconciliation + `_cross_reference`), plus the
`effective_verdict_bucket()` fix. All 185 tests green, no failures recorded
yet in this log's history. See §1 above for the full breakdown.
