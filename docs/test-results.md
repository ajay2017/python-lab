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

## 1. Latest run — 2026-07-27

**185 passed, 0 failed, 0 skipped** (`pytest tests/ -v`, no coverage
instrumentation: 0.66–0.83s across repeated runs; with `--cov` active: ~2.9s).
Python 3.14.6, pytest 8.4.2, pytest-cov 5.0.0, in the local `.venv`.

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
| **Total** | **185** | |

### Line coverage of the 8 targeted modules

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
| `signal_reconciliation.py` | 86 | 4 | **95%** |
| `risk_advisor.py` | 175 | 13 | **93%** |
| `exit_advisor.py` | 191 | 131 | 31% |
| `portfolio.py` | 427 | 300 | 30% |
| `daily_briefing.py` | 773 | 546 | 29% |

**Whole-`stock_analyzer/` total: 13,654 stmts, 12,683 missed, 7%.** This
number is dominated by the ~45 modules with zero tests at all (untouched
territory outside this suite's scope — `app.py`-adjacent helpers,
`providers/`, `trade_review.py`, `thesis_advisor.py`, etc.) and is expected to
stay low; it is not a target to chase per `docs/plans/test-automation.md`'s
"golden-value regression, not coverage-chasing" principle. Track it here
mainly to notice a SUDDEN drop in one of the 8 targeted modules above (a
signal something broke), not to push the whole-package number up.

---

## 2. History

*(Newest first. Add a new entry above this line each time the suite is run
and the result is worth recording — at minimum, after any batch/module
addition or whenever a run fails.)*

### 2026-07-27 — Batches 1–6 complete, 185/185 passing

First recorded run. Batches 1–6 of `docs/plans/test-automation.md` all
shipped this session (constants/scoring/concentration → exit_advisor →
portfolio → risk_advisor → daily_briefing buy-candidate funnel +
`high_beta_share` → signal_reconciliation + `_cross_reference`), plus the
`effective_verdict_bucket()` fix. All 185 tests green, no failures recorded
yet in this log's history. See §1 above for the full breakdown.
