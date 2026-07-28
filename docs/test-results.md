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

## 1. Latest run — 2026-07-27 (post-roadmap health check)

**327 passed, 0 failed, 0 skipped** (`pytest tests/ -v`: 1.36s; with `--cov`
active: 2.90s). Python 3.14.6, pytest 8.4.2, pytest-cov 5.0.0, in the local
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
| **Total** | **327** | |

### Line coverage of the 13 targeted modules

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
| `thesis_red_team.py` | 84 | 11 | 87% |
| `structural_scanner.py` | 144 | 62 | 57% |
| `exit_advisor.py` | 191 | 131 | 31% |
| `portfolio.py` | 427 | 300 | 30% |
| `daily_briefing.py` | 773 | 546 | 29% |

**Whole-`stock_analyzer/` total: 13,773 stmts, 12,392 missed, 10%** (up from
7% same day, before this batch). Still dominated by ~65 modules with zero
tests at all — a same-day audit ranked the highest-priority remaining gaps
with real decision/gate logic: `risk.py` (position sizing, stops,
Sharpe/Sortino/VaR), `macro_playbook.py` (pullback-alert thresholds),
`portfolio_health.py`, `rebalancer.py`, `signal_hysteresis.py` (tied to the
still-parked deterioration-hysteresis queue item), `position_lifecycle.py`,
`tax_advisor.py`. Not a target to chase for its own sake per
`docs/plans/test-automation.md`'s "golden-value regression, not
coverage-chasing" principle — but `watchlist_advisor.py` and `valuation.py`
were prioritized THIS batch specifically because they contain an actual
portfolio-risk gate and a live scoring pillar, respectively, and had zero
coverage despite that. Track this section mainly to notice a SUDDEN drop in
a targeted module (a signal something broke), not to push the whole-package
number up for its own sake.

---

## 2. History

*(Newest first. Add a new entry above this line each time the suite is run
and the result is worth recording — at minimum, after any batch/module
addition or whenever a run fails.)*

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
