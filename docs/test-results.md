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

## 1. Latest run — 2026-07-27 (post-roadmap health check, batch 3: `macro_playbook.py`)

**437 passed, 0 failed, 0 skipped** (`pytest tests/ -v`; with `--cov`
active: 3.58s). Python 3.14.6, pytest 8.4.2, pytest-cov 5.0.0, in the local
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
| **Total** | **437** | |

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
| `thesis_red_team.py` | 84 | 11 | 87% |
| `structural_scanner.py` | 144 | 62 | 57% |
| `exit_advisor.py` | 191 | 131 | 31% |
| `portfolio.py` | 427 | 300 | 30% |
| `daily_briefing.py` | 773 | 546 | 29% |

**Whole-`stock_analyzer/` total: 13,774 stmts, 11,973 missed, 13%** (up from
11% earlier the same day). Still dominated by ~63 modules with zero tests at
all — ranked remaining gaps with real decision/gate logic: `portfolio_health.py`,
`rebalancer.py`, `signal_hysteresis.py` (tied to the still-parked
deterioration-hysteresis queue item), `position_lifecycle.py`,
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

---

## 2. History

*(Newest first. Add a new entry above this line each time the suite is run
and the result is worth recording — at minimum, after any batch/module
addition or whenever a run fails.)*

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
