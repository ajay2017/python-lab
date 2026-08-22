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

## 1. Latest run — 2026-08-22 (F-247 readiness + F-248 + three withheld-basis disclosure fixes)

**3831 passed, 0 failed, 0 skipped** (`pytest -q --cov=stock_analyzer
--cov-report=term`: 80.91s — TOTAL 17715 stmts, 4170 missed, **76%** overall
coverage). Python (local `.venv`). Transcribed from the run, not recalled.

**F-248 added 16** — why matured recommendations drop out of the Predictive
Analytics working set. Two are worth naming. `test_..._adds_exactly_one_key_and_
changes_no_other` asserts `compute_outcomes`' exact output key set, because that
function has 8 consumers and the change is only safe if it is genuinely
additive; the Opus review verified that by tracing all 8, and this test keeps it
verified. `test_alpha_unavailable_reason_no_spy_series_is_not_reported_as_out_of_
range` pins the blocking defect that review caught: `_spy_return_pct` returns
`None` for three physically different causes and `app.py` swallows a failed SPY
fetch into an empty dict, so a provider outage would have told the user every
matured rec was "dated outside the SPY series" — the total reconciling while the
stated reason was false. It runs both `None` and `{}` as the series.

**The last +3** (2026-08-22) are the 🧭 Self vs Engine head-to-head disclosure,
found by a targeted Sonnet sweep for the "asserts a conclusion, withholds a
basis it already computed" class. Two of the three are the interesting ones:
one asserts the graded in/out-of-scope split sums to `self_graded["n"]`, and one
asserts the *graded* split excludes immature rows where the raw counts do not —
which is the whole reason the new fields exist rather than reusing the counts
already returned. `n_self_in_scope` had been returned since F-233 and read by
nothing but tests.

**+32 over the 3780 entry below** for the two 08-21 parts. **F-247** added 25
(`tests/test_attribution_readiness.py`, new) — the readiness audit that counts
distinct captured snapshot dates against NYSE sessions instead of a calendar
span. Its own review caught two defects the first draft shipped with, and both
now have tests: SPLIT rows counted as traded notional (the tell was an `action`
column the fixture built and the function never read), and an annualisation
blow-up that the original assertion (`> 20.0`) *documented* rather than caught.
A loose assertion is worse than no assertion — it looks like coverage while
permitting any value above the floor.

The remaining **7** are this session's follow-up fix, and both groups exist
because reading the live panel found something the tests could not: 3 in
`tests/test_attribution_readiness.py` for the turnover legs — including
`test_turnover_accumulation_and_churn_differ_in_the_legs_not_the_total`, which
asserts that a book being *built* and a book being *churned* produce an
**identical** total turnover figure and differ only in the split (that
indistinguishability is why the legs are reported, and the shipped panel had
rendered only the sum) — plus one pinning `window_days` as inclusive so it
agrees with `span_days` rather than printing 74 and 73 for one interval. And 4
in `tests/test_predictive_analytics.py` for the Decision Quality directive's
sample-size disclosure, one of which pins that an `avm` carrying only
`edge`/`edge_pp` renders clean prose instead of "None acted vs None passed".

---

## 1a. Previous run — 2026-08-21 (F-245 Forward Portfolio Simulator + F-246 correlation coverage)

**3780 passed, 0 failed, 0 skipped** (`pytest -q --cov=stock_analyzer
--cov-report=term`: 77.47s — TOTAL 17583 stmts, 4158 missed, **76%** overall
coverage). Python (local `.venv`). Transcribed from the run, not recalled.

**F-246** added 9 tests (6 in `tests/test_portfolio_intelligence.py`, 3 in
`tests/test_forward_sim.py`). The `n_obs` arithmetic ones exist because the
figure's entire purpose is stating a correct sample size, so an off-by-one there
would be self-defeating — including the interior-NaN case, which pins that
listwise deletion removes the NaN row *before* `pct_change`, so frame-dropna and
pct_change-dropna never differ by more than the single leading bar. The
`max_pairwise_corr` ones pin the review finding that a per-pair threshold cannot
be applied to a mean: one 0.77 pair among near-zero pairs averages below 0.65,
so judging "these are not the same bet" on the mean under-alarms.

**+114 tests over the 3657 entry below.** 64 of those are F-245's
(`tests/test_forward_sim.py`, new); the remaining ~50 shipped with work logged
in `docs/shipped-log.md` between 08-17 and 08-21 without a test-results entry.
F-245's own count went 36 → 64 across two Opus review rounds: the review found
a real defect the original 36 could not have caught (the identity test used the
same frame as both position and benchmark, leaving relative strength entirely
unconstrained), so the added tests are the direct product of that finding, not
padding. Transcribed from `pytest --collect-only -q`, not from the delta — a
first pass recorded 61 by adding 3 to the previous count, missing that one of
the four new tests was a *rename* of an existing one.

**Coverage moved 77% → 76% — a real 1pp drop, not a rounding artifact, and
worth stating plainly rather than presenting as flat.** Total statements grew
+659 while missed grew +269. F-245's own module is well covered by its 64
tests; the dilution is app.py-side render code and the other 08-17→08-21 work,
which added statements faster than tests. Not a regression in any existing
module's coverage — but the trend is the thing this log exists to make visible.

Tests added by the work this entry is named for:
- **F-245** (`tests/test_forward_sim.py`, new, 64). The load-bearing one is
  `test_zero_shock_matches_assess_holding`: `forward_sim` must re-extract the
  deterioration scalars at a substituted price, which duplicates
  `assess_holding`'s peak-window / trend-MA / below-MA math. That duplication is
  the module's single biggest risk — a silently drifted replay would report a
  portfolio the app would never actually produce, and report it
  authoritatively — so at zero shock the two must agree on the tier and 7
  scalars, parametrized across every peak-window branch
  (`peak_window_days ∈ {None,1,3,14,60}` × `age_days ∈ {None,30,400}`) on a
  frame longer than `DETERIORATION_PEAK_FALLBACK_BARS`, plus a NaN-Close bar.
  Verified non-vacuous (the fixture yields a real EXIT at 12.0% off peak, not a
  `None`-vs-`None` match).
  **A cautionary note worth keeping.** The first version of this test passed the
  *same* frame as both the position and the benchmark, which made relative
  strength 0.0 on both sides and left the entire RS path unconstrained — and
  that is precisely the path where the Opus review then found a live defect.
  A convenient fixture can silently un-test the thing it exists to test; the
  benchmark is now a genuinely different series and the test asserts RS is
  non-zero before comparing it.
  Also pinned: the day1-vs-confirmed bracket and its monotonicity across 6 shock
  magnitudes, RS being additive (the engine's real reading survives into the
  replay rather than being replaced), a missing stop reading `None` rather than
  a falsy "no breach", the Brief's exact rounded breach test,
  `mean_pairwise_corr` returning `None` (never `0.0`) when unresolvable and
  surviving duplicate `corr_df` labels, a WATCH name never coexisting with a
  risk-off card (the H6 invariant), an offline `_fragility_cache` reading as
  "unknown" rather than a calm book, a TRIM never being liquidated as an exit,
  and surviving beta staying `None` rather than fabricating 1.0.

---

## 1b. Previous run — 2026-08-17 (reference-roster refreshes F-240/F-241/F-242 + rate-sensitivity honesty fix)

**3657 passed, 0 failed, 0 skipped** (`pytest tests/ --cov=stock_analyzer
--cov-report=term-missing -q`: 125.84s — TOTAL 16904 stmts, 3899 missed,
**77%** overall coverage). Python (local `.venv`). Transcribed from the run,
not recalled.

**+508 tests and +2pp coverage over the 3149 entry below**, which was the last
logged run (2026-08-04) — so this entry closes a 13-day logging gap, and most
of that delta shipped in features logged in `docs/shipped-log.md` rather than
here. The log itself had drifted; that is the drift class this file exists to
prevent, and it is worth noting rather than quietly back-filling.

Tests added by the work this entry is named for:
- **F-240** (`tests/test_scanner.py`, +5): the macro-gate coverage invariant
  with a deliberately EMPTY exception allowlist, a per-category `_SECTOR_IMPACT`
  invariant whose allowlist also fails if it outlives its own debt, pick-path vs
  held-path `resolve_sector` agreement, and a `GOOG`-absent regression guard.
- **F-241** (`tests/test_ticker_liveness.py`, new, 13): batch-health boundary
  asserted exactly at the threshold, rate-limit-is-not-a-dead-verdict,
  multi-source rescue, dead-ticker-does-not-fail-the-lane, sub-job ordering,
  exception containment, and the `None` vs `"inconclusive"` sentinel split.
- **F-242** (`tests/test_portfolio.py`, +7): every roster ticker has a curated
  sector (this one FAILED on HEAD — `F`, `GM`, `LCID`), roster key matches
  `TICKER_SECTORS` value, every diversifying sector can actually produce
  candidates, and the sub-scale-tail exclusion.
- **Rate-sensitivity fix** (`tests/test_risk.py`, +6): unmapped sector reports
  `None` not a fabricated `0.0`, "Unknown" instead of a confident
  "rate-neutral" from no data, unknown rows sort last, and one test at the
  **DataFrame layer** pinning the `pd.DataFrame` None→NaN coercion that the
  function-level tests structurally could not see.

**Coverage note, stated honestly:** `stock_analyzer/ticker_liveness.py` is at
**72%** — the lowest of the new modules. The uncovered lines are the real-provider
default paths (`fetch_batch`/`fetch_live` defaults, the timeout branch), which the
tests deliberately inject around rather than exercise, since covering them would
mean live network calls inside the suite. That is the correct trade — but it is
also exactly why three display/runtime defects in this session were caught by
review rather than by the suite.


## 2. History

### 2026-08-04 (morning-picks cron bug fix + trades-idempotency catch-up)

**3149 passed, 0 failed, 0 skipped** (`pytest tests/ --cov=stock_analyzer
--cov-report=term-missing -q`: 24.89s — TOTAL 15177 stmts, 3852 missed,
**75%** overall coverage). Python (local `.venv`). +8 tests over the 3141
entry below, none of which is itself stale-doc drift, not new-this-run:
**+5** are `tests/test_db_save_trade.py` (commit `8fdeb7e`, the DB-level
trades-idempotency fix) which shipped after the 3141 entry was recorded but
never got its own log entry; **+3** are new with this run's own fix —
`test_build_daily_briefing_top_level_keys_exclude_grow_today_fields` and
`test_build_daily_briefing_bear_tone_grow_today_omits_sp500_pct` in
`test_daily_briefing.py`, plus `test_morning_picks_bear_tone_sp500_pct_
falls_back_to_market_context` in `test_headless_alert_engine.py`.

This run's own fix: `compute_morning_picks()` in `stock_analyzer/
headless_alert_engine.py` was reading `tone`/`sp500_pct`/`new_picks`/
`sector_blocked_picks`/`macro_blocked_picks`/`composite_skipped`/
`composite_unavailable` directly off the top-level dict returned by
`build_daily_briefing()` — but those fields only ever exist nested under
`brief["grow_today"]` (confirmed via `app.py`'s own reads of the same brief).
Found investigating why the 9:45 ET "New Positions to Initiate" email had
never fired; introduced in commit `3eae985` (2026-06-26), broken for the
entire 5+ week life of both the morning buy-list email and the ~11:30 ET
intraday pullback-entry email (both share this function). Fixed by reading
all 7 fields from `grow = brief.get("grow_today") or {}` instead. Also fixed
two `tests/test_headless_alert_engine.py` mocks (`_run_morning_picks()` and
`test_morning_picks_market_tone_fetch_failure_falls_back_to_flat`) that had
been mocking `build_daily_briefing` with the wrong (flat) shape — the exact
reason the test suite stayed green while the real integration was broken.
Opus review (Opus 4.8, 1M context): SHIP, 0 blocking, 1 non-blocking applied
— the bear-tone early return in `_grow_today` omits `sp500_pct` entirely
(unlike the bull/flat path), so `diag["sp500_pct"]` now falls back to
`market_context`'s own fetched value on a bear day instead of logging a real
risk-off move as "n/a"; a second non-blocking suggestion (cover the bear-
branch key shape in the new contract test) was also applied.

## 1a. Prior run — 2026-08-04 (post-Medium-fix baseline, same session as the audit)

**3141 passed, 0 failed, 0 skipped** (`pytest tests/ --cov=stock_analyzer
--cov-report=term-missing -q`: 30.97s — TOTAL 15174 stmts, 3909 missed,
**74%** overall coverage). Python (local `.venv`). +5 tests over the
3136 mid-session entry below — regression coverage for 2 of the 17 Medium
findings fixed same day (`docs/reviews/2026-08-04-review.md` §9): 3 in
`test_technicals.py` (Bollinger zero-guard corrected + 1 new zero-volume
guard test) and updates/additions in `test_targets.py` (nearest-by-distance
support/resistance).

## 1b. Prior run — 2026-08-04 (post-High-fix baseline, same session as the audit)

**3136 passed, 0 failed, 0 skipped** (`pytest tests/ --cov=stock_analyzer
--cov-report=term-missing -q`: 35.45s — TOTAL 15133 stmts, 3909 missed,
**74%** overall coverage). Python (local `.venv`). +16 tests over the
3120 baseline earlier this same session (§2 entry below) — regression
coverage for the Critical + all 9 High findings fixed same day
(`docs/reviews/2026-08-04-review.md`), including 2 new test files
(`test_db_readonly.py`, `test_providers_util.py`).

## 1c. Prior run — 2026-08-04 (baseline refresh ahead of full-codebase audit)

**3120 passed, 0 failed, 0 skipped** (`pytest tests/ -v`: 27.84s; `pytest
tests/ --cov=stock_analyzer --cov-report=term-missing -q`: 27.93s — TOTAL
15112 stmts, 3913 missed, **74%** overall coverage). Python (local `.venv`).
Run to refresh the baseline before the 2026-08-04 full audit (`docs/reviews/2026-08-04-review.md`)
— 5 days and several decision-adjacent features (The Judge Phases 3-4, F-228
Pre-Mortem enforcement) had shipped since the last recorded run with no
fresh pass/fail check in between. The 2909→3120 delta (+211) is organic
test growth from those sessions, not independently itemized here; see git
log for the per-commit test additions.

**Note on the jump from 1108 (2026-07-28) to 2909:** the large majority of
this growth is from the test-coverage-backlog project (memory
`project_test_coverage_backlog` — all ~46 modules backfilled across 8
batches, closed 2026-07-30 in a session prior to this one) whose individual
runs were never logged here as they landed. This entry is the first to
capture the resulting total. **This session's own, precisely-known
contribution is 6 new tests in `test_portfolio.py`** (28 → 34, covering the
new `real_sector_exposure()`/`sector_benchmark_tilt()` functions — see the
dated entry below). The per-file table and the "15 targeted modules"
coverage table immediately below predate the backlog project's closure and
are now stale for everything outside `test_portfolio.py` — treat the
per-file counts and the 30%-ish `portfolio.py`/`daily_briefing.py`/etc.
coverage figures below as **outdated pending a fuller refresh**, not as
current fact. Don't extend or hand-edit them further without re-running
the full per-file collection (`pytest tests/ --collect-only -q`) and a
fresh coverage pass first.

### Per-file breakdown

| Test file | Tests | Module(s) under test |
|---|---|---|
| `test_constants_invariants.py` | 12 | `constants.py` (ordering/consistency invariants, not literal mirrors) |
| `test_scoring.py` | 8 | `scoring.py` |
| `test_concentration.py` | 16 | `concentration.py` |
| `test_exit_advisor.py` | 32 | `exit_advisor.py` |
| `test_portfolio.py` | 34 | `portfolio.py` (incl. 6 new tests for `real_sector_exposure()`/`sector_benchmark_tilt()`, 2026-07-30) |
| `test_risk_advisor.py` | 44 | `risk_advisor.py` (incl. 16 boundary-exact regression pins across the Sharpe/volatility/drawdown/tail-risk alert ladders and their per-ticker selection cutoffs) |
| `test_daily_briefing.py` | 35 | `daily_briefing.py` (incl. `_cross_reference()`) |
| `test_signal_reconciliation.py` | 26 | `signal_reconciliation.py` |
| `test_structural_scanner.py` | 19 | `structural_scanner.py` (incl. `blast_radius()` backfill) |
| `test_thesis_red_team.py` | 30 | `thesis_red_team.py` (Phase 1 `compute_erosion_score()` backfill + Phase 2 Haiku contract) |
| `test_valuation.py` | 31 | `valuation.py` |
| `test_decision_bucket.py` | 27 | `decision_bucket.py` |
| `test_watchlist_advisor.py` | 45 | `watchlist_advisor.py` (incl. `_portfolio_risk_gate()` and the in-zone-R:R-not-validated NEAR_ENTRY copy fix) |
| `test_risk.py` | 43 | `risk.py` (position sizing, ATR stops, Sharpe/Sortino/VaR/beta) |
| `test_macro_playbook.py` | 67 | `macro_playbook.py` (Pre-Event Macro Playbook: PROTECT/WATCH/HOLD/OPPORTUNITY action classifier, post-event scenario classification) |
| `test_headless_alert_engine.py` | 59 | `headless_alert_engine.py` (cron protective-alert / morning-picks / EOD engine: `_build_context`, `compute_protective_alerts`, `compute_morning_picks`, `compute_eod`, `_assess_pullback`; +2 NaN-guard regression tests) |
| `test_portfolio_health.py` | 92 | `portfolio_health.py` (Portfolio Construction Health Score: 5 sub-scores + A-F grade, Portfolio Dynamics tenure/cohort/alignment) |
| `test_rebalancer.py` | 33 | `rebalancer.py` (drift classification, trim/add urgency + rationale, News Intelligence / Risk Advisor coordination gates) |
| `test_signal_hysteresis.py` | 32 | `signal_hysteresis.py` (calm-advisor "steady vs yesterday" annotator) |
| `test_position_lifecycle.py` | 29 | `position_lifecycle.py` (held-position lifecycle classifier: exit/at_risk/settling/winning/established) |
| `test_tax_advisor.py` | 59 | `tax_advisor.py` (FIFO tax-lot reconstruction, STCG/LTCG classification, harvest/wait action ladder, holding-period + wash-sale awareness helpers) |
| `test_decision_quality.py` | 68 | `decision_quality.py` (monthly/quarterly Decision Quality grades, Workflow ROI prep-tier classification) |
| `test_comparison.py` | 57 | `comparison.py` (2-ticker Compare page: per-row winner picking, composite-first/sub-factor-tiebreak verdict, portfolio-fit notes) |
| `test_decision_journal.py` | 31 | `decision_journal.py` (signal-followed vs. ignored accuracy, costly-deviation/good-override lists, lessons library, behavioral insight; avg-loss-sign copy fix) |
| `test_earnings_advisor.py` | 56 | `earnings_advisor.py` (Pre-Earnings Playbook: EXIT/REDUCE/MONITOR/HOLD/HOLD_OR_ADD ladder, watchlist earnings-catalyst scanner) |
| `test_perf_advisor.py` | 46 | `perf_advisor.py` (per-position performance attribution vs SPY/sector ETF, Alpha Generator/Sector Rider/Alpha Destroyer recommendation ladder) |
| `test_news_intelligence.py` | 76 | `news_intelligence.py` (significance scoring, negative-news alerts, opportunity detection + Reduce/Exit suppression, sector digest, suppress-only + bidirectional LLM rescore helpers) |
| **Total** | **1114** | *(this table's original ~28-file scope only — see the note above §1; full current suite is 2909)* |

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
| `watchlist_advisor.py` | 115 | 0 | **100%** |
| `signal_reconciliation.py` | 86 | 4 | **95%** |
| `decision_bucket.py` | 75 | 2 | **97%** |
| `risk_advisor.py` | 175 | 13 | **93%** |
| `risk.py` | 171 | 7 | **96%** |
| `macro_playbook.py` | 252 | 13 | **95%** |
| `headless_alert_engine.py` | 252 | 26 | **90%** |
| `portfolio_health.py` | 242 | 11 | **95%** |
| `rebalancer.py` | 122 | 2 | **98%** |
| `signal_hysteresis.py` | 46 | 0 | **100%** |
| `position_lifecycle.py` | 14 | 0 | **100%** |
| `tax_advisor.py` | 191 | 4 | **98%** |
| `decision_quality.py` | 231 | 8 | **97%** |
| `comparison.py` | 147 | 5 | **97%** |
| `decision_journal.py` | 67 | 0 | **100%** |
| `earnings_advisor.py` | 145 | 3 | **98%** |
| `perf_advisor.py` | 116 | 0 | **100%** |
| `news_intelligence.py` | 140 | 0 | **100%** |
| `thesis_red_team.py` | 84 | 11 | 87% |
| `structural_scanner.py` | 144 | 62 | 57% |
| `exit_advisor.py` | 191 | 131 | 31% |
| `portfolio.py` | 427 | 300 | 30% |
| `daily_briefing.py` | 773 | 546 | 29% |

**Whole-`stock_analyzer/` total: 13,810 stmts, 9,854 missed, 29%** (stmt
count ticked up slightly — the `watchlist_advisor.py` NEAR_ENTRY copy fix
added a new branch). ~51 modules remain with zero tests, none flagged by
the original survey as having real decision/gate logic worth chasing. Not
a target to chase for its own sake per `docs/plans/test-automation.md`'s
"golden-value regression, not coverage-chasing" principle. Track this
section mainly to notice a SUDDEN drop in a targeted module (a signal
something broke), not to push the whole-package number up for its own
sake.

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

**`decision_journal.py`'s batch found and fixed a real CRASH bug** (see the
dated history entry below) — not a subtle one: `compute_patterns()`'s
`costly`/`good` DataFrames were built by boolean-masking `ignored` with the
result of a fresh `.apply()` call; when `ignored` has zero rows (i.e. a user
who has never logged an override — every trade `followed_signal="yes"`),
`.apply()` on an empty Series can't infer a return dtype and yields an
`object`-dtype (not `bool`) empty Series, which pandas then can't use as a
boolean mask — the indexed result collapses to a columnless `(0, 0)`
DataFrame, and the very next `.sort_values("_pnl")` crashes with
`KeyError: '_pnl'`. This is reachable for one of the MOST common real
scenarios (a disciplined user with zero logged overrides), not a contrived
edge case. Fixed by skipping the `.apply()`-based filter entirely when
`ignored` is empty. Not Opus-reviewed — a crash fix in retrospective display
analytics (Lessons Learned page), not a gate/scoring-formula change.

---


*(Newest first. Add a new entry above this line each time the suite is run
and the result is worth recording — at minimum, after any batch/module
addition or whenever a run fails.)*

### 2026-08-04 (later still, same session) — Medium-fix regression tests, +5, 3141/3141 passing

Fixed 17 of the 19 Medium findings from the same-session audit
(`docs/reviews/2026-08-04-review.md` §9). Most were pure constants.py
extractions (no test changes needed — same values, same behavior). Real
behavior changes got test coverage: `test_technicals.py`'s existing
Bollinger zero-guard test was re-asserted from the old buggy value (0.0)
to the corrected neutral-50, plus a new zero-volume-guard test; 3
`test_targets.py` tests added/updated for `support_resistance()`'s
nearest-by-distance fix, including updating 2 existing bear-floor tests
whose own reference recomputation needed the same `current_price` fix the
production code got. Opus review (Opus 4.8): SHIP, 0 blocking, 2
non-blocking (both addressed).

### 2026-08-04 (later same session) — Audit-fix regression tests, 16 new, 3136/3136 passing

Fixed the Critical + all 9 High findings from the full audit run earlier
this same session (`docs/reviews/2026-08-04-review.md` §9 Resolution
status). +16 tests over the 3120 baseline: 1 in `test_premortem_monitor.py`
(same-day-fire regression, for the Critical fix, landed before this entry's
starting point but counted in this delta), 2 in `test_quick_research.py` +
4 in `test_watchlist_advisor.py` (the Valuation `val_available` gate and
its new `DATA_UNAVAILABLE` watchlist action), and 2 new files —
`test_db_readonly.py` (5 tests, the read-only-viewer session-scoping fix)
and `test_providers_util.py` (4 tests, the Finnhub key-redaction fix).
`test_risk.py` had 3 existing tests renamed/re-asserted (`== {}` →
`is None`) and `test_valuation.py` had all 34 call sites updated for the
new 3-tuple return — no net-new test functions in either file. Opus review
(Opus 4.8): SHIP, 0 blocking, 2 non-blocking (both applied).

### 2026-08-04 — Baseline refresh + full-codebase audit, 3120/3120 passing

No test changes this session — this is a pure baseline refresh. Full suite
re-run (3120 passed, 74% coverage, see §1) ahead of a full-codebase `/audit
--full` sweep (`docs/reviews/2026-08-04-review.md`), triggered by 5 days
without an independent test/audit check spanning two decision-adjacent
feature builds (The Judge Phases 3-4, F-228 Pre-Mortem enforcement — see
`git log`). The audit's findings are tracked in the review doc, not here.

### 2026-07-30 — Sector-diversification feature (F-222/F-223): 6 new tests, full suite re-run at 2909/2909 passing

Session shipped the 3-phase sector-diversification feature (Sector Gaps
pointer + widened diversifier coverage, F-222; real-sector S&P 500
benchmark tilt, F-223 — see `docs/shipped-log.md`). Added 6 regression
tests to `tests/test_portfolio.py` (28 → 34) for the two new pure functions
`real_sector_exposure()` and `sector_benchmark_tilt()`: provider-sector
alias normalization, unmapped-sector fallback to `"Other"`, missing-sector
fallback, empty-portfolio handling, and outer-join tilt-sign correctness
for a benchmark sector held at 0%. Opus pre-ship review: SHIP, 0 blocking.

Also caught and fixed a self-inflicted mid-session bug: an `Edit` call
meant to insert the new tests matched a shorter substring than intended and
silently split an existing test (`test_diversification_score_ignores_nan_pairs`)
into two, orphaning its second assertion at module scope. Caught via a
stray "`result` is not defined" Pyright diagnostic, fixed before commit, and
independently re-verified by the Opus reviewer as fully repaired (single
correct definition, no orphaned/duplicated lines).

Ran the FULL suite (not just the new file) as part of this wrap-up:
**2909 passed, 0 failed, 0 skipped**, 74% overall `stock_analyzer/`
coverage (14148 stmts, 3739 missed) — see the note in §1 above for why this
total is much larger than the last-logged 1108 (2026-07-28): almost all of
the growth is from the separately-tracked test-coverage-backlog project
(closed 2026-07-30, prior session), not from this session's own 6-test
addition.

### 2026-07-28 — Closed the last 2 low-urgency follow-ups: `headless_alert_engine.py` NaN guards, `app.py` Max-Drawdown caption constant-wiring, 1108/1108 passing

User asked to clear the last 2 remaining low-urgency items too, before
starting anything new — a deliberate "fresh start" with nothing left
outstanding.

**`headless_alert_engine.py` — both non-blocking NaN-guard follow-ups from
the 2026-07-27 Opus review, now fixed:**
- `_build_context()`'s `beta = port_risk.get("beta") if port_risk else None`
  didn't route through the file's own NaN-aware `_f()` helper (the same one
  that fixed the stop-breach bug in that review), so a NaN beta could pass
  the `is not None` guard and get admitted into `run_scenario`/
  `assess_fragility` instead of correctly skipping fragility. Fixed to
  `beta = _f(port_risk.get("beta")) if port_risk else None`.
- The analyst-target-snapshot capture's `target_mean =
  fin.get("analyst_target")` had the same gap — a NaN target from yfinance
  would pass the `is None` guard and get persisted, which could poison a
  future day-over-day analyst-target comparison (log-only Phase 1 today,
  nothing reads this table yet, but the gap would have been silent until
  something did). Fixed to `target_mean = _f(fin.get("analyst_target"))`.

Added 2 regression tests to `tests/test_headless_alert_engine.py` (59
total, was 57): a NaN-beta case confirming `fragility` stays `None`, and a
NaN-analyst-target case confirming the snapshot list stays empty. Not
Opus-reviewed — both are the SAME NaN-aware-parsing bug class already
Opus-reviewed and shipped for the stop-breach case in this file
(2026-07-27), applied here via the identical existing `_f()` helper with
no new logic, no gate-firing behavior changed. Coverage unchanged (252
stmts, 26 missed, 90%).

**`app.py` — wired the Max Drawdown metric-caption thresholds to the
`risk_advisor.py` constants they numerically mirror:** the 🔗 Risk
Analysis page's Max Drawdown metric card computed its own "Modest / Normal
/ Significant / Severe" label from bare `-10`/`-20`/`-30` literals that
happen to exactly match all 3 of `risk_advisor.py`'s
`PORTFOLIO_DRAWDOWN_OK_MIN`/`_ACTION_MAX`/`_HIGH_MAX` constants (added
earlier the same session) — a genuine, unambiguous 1:1 duplicate, not a
coincidence. Imported the 3 constants at the top of `app.py` and replaced
the 3 literals so a future constant retune can't silently drift the
caption out of sync with the actual gate it's describing. **Deliberately
left the Volatility/Sharpe/Beta metric captions alone** — checked each
against its `risk_advisor.py` counterpart and found they are NOT genuine
duplicates: Volatility's caption uses a 4-tier Low/Moderate/Elevated/High
taxonomy at 15/20/30 where only the top boundary (30) coincidentally
matches `PORTFOLIO_VOL_HIGH_PCT`, the other two (15/20) have no
`risk_advisor.py` counterpart at all (`PORTFOLIO_VOL_MEDIUM_PCT` is 25,
not 20); Sharpe's caption (0.5/1.0/1.5) doesn't match the Sharpe ladder
(0.4/0.8/1.0) anywhere; Beta's label thresholds (1.2/0.8) don't match
`PORTFOLIO_BETA_ELEVATED` (1.3), only its `delta_color` line's `>1.4`
happens to match `PORTFOLIO_BETA_CEILING`. Wiring a ladder where only one
of three boundaries is a real duplicate would leave the code MORE
confusing (why import a constant for one tier and hardcode its siblings?)
without meaningfully reducing drift risk, since the tiers that matter
(the top HIGH boundary) is the only one a `constants.py` reader would ever
retune anyway — left as local, legitimately-independent display literals.
Compile-checked (`py_compile`, since `app.py` can't run locally per
CLAUDE.md — Streamlit/plotly aren't in the dev venv). Not Opus-reviewed —
display-caption-only, doesn't touch `constants.py` (only imports existing
constants into `app.py`) or change any gate/scoring formula.

**This closes every item on the session's test-coverage-and-hardening
effort with nothing outstanding.** 1108 tests total, all passing.

### 2026-07-28 — Cleared the remaining flagged-findings backlog: `watchlist_advisor.py` NEAR_ENTRY copy fix, `comparison.py` dead import, `decision_journal.py` avg-loss-sign copy fix, 1106/1106 passing

User asked to close out the last 3 outstanding items accumulated across
this session's whole test-coverage effort, to clear the list.

**`watchlist_advisor.py` — fixed the flagged NEAR_ENTRY copy gap
(2026-07-27 finding, fixed 2026-07-28):** a stock priced ALREADY inside its
entry zone but lacking a validated R:R (either no target price at all, or
a computed R:R below `RR_ENTRY_MIN`) fell through `ENTER_NOW`'s gate and
into the generic NEAR_ENTRY branch, which renders "Approaching Entry Zone
(+0.0% above zone)" / "watch for a small pullback" — actively misleading,
since `pct_above` is always `<= 0` when price is in-zone (the stock isn't
approaching anything, it already arrived) and the real blocker is the
missing/insufficient R:R, unrelated to price distance. **Fixed** by adding
a new branch immediately before the generic NEAR_ENTRY check:
`score >= COMPOSITE_BUY and in_zone and (rr is None or rr < RR_ENTRY_MIN)`
renders a distinct card ("In Entry Zone, R:R Not Yet Validated") that
names the actual blocker (no target price, or "R:R is only X:1 — below
the 2:1 minimum") and recommends refreshing analyst targets rather than
"wait for a pullback." Same `action="NEAR_ENTRY"` string, priority, and
sort rank as before (verified `app.py`'s only two `NEAR_ENTRY` consumers —
the count/pill/emoji lookup, and a `"Portfolio Fit" in title` check for
the unrelated hard-breach downgrade case — are both unaffected), so this
is a display-copy fix, not a decision/priority change. Added 3 regression
tests to `tests/test_watchlist_advisor.py` (45 total): the missing-target
case, the known-but-low-R:R case, and a guard test confirming the
ordinary "price above zone" NEAR_ENTRY copy still renders unchanged when
NOT in-zone. Not Opus-reviewed — same reasoning as the earlier
`portfolio_health.py`/`decision_journal.py` copy fixes this session: the
action, priority, and gating boundary are byte-identical for every input;
only the explanatory text for an already-decided NEAR_ENTRY outcome
changed, so this isn't a gate/scoring-formula change under CLAUDE.md hard
rule #4. Now 100% covered (115 stmts, 0 missed; stmt count rose from 109
with the new branch).

**`comparison.py` — removed the flagged dead import (2026-07-27 finding,
fixed 2026-07-28):** `PORTFOLIO_BETA_ELEVATED`/`PORTFOLIO_BETA_CEILING`
were imported but never referenced anywhere in the file (re-confirmed via
grep before removing). Deleted both from the import block. No behavior
change; no test change needed (nothing referenced them). Still 97% covered
(147 stmts, 5 missed — unchanged, import-line removal didn't shift the
gap).

**`decision_journal.py` — fixed the avg-loss-sign copy nit (2026-07-27
finding, fixed 2026-07-28):** the costly-deviations behavioral-insight
message read "...with an avg loss of $-150 per trade" — `avg_cost` is
always negative by construction (the list is pre-filtered to
`realized_pnl < 0`), so the raw signed value next to the word "loss" read
oddly. **Fixed** with a one-line `abs()` around the interpolated value.
Updated the one existing test assertion that had pinned the old (buggy)
"$-150" text to instead assert the corrected "$150" appears and "$-150"
does not. Not Opus-reviewed — display-copy only, no gate/scoring change.
Still 100% covered (67 stmts, 0 missed).

**This clears every item on the flagged-but-not-yet-fixed list accumulated
across the whole session's test-coverage effort.** Two low-urgency
non-blocking items remain, both explicitly deferred (not "open bugs"): the
2 `headless_alert_engine.py` NaN-guard follow-ups from the 2026-07-27 Opus
review (outside the alert-firing path, no live decision affected), and
`app.py`'s Max-Drawdown/volatility metric-caption literals that now
numerically mirror the new `risk_advisor.py` constants but aren't wired to
them (display-only, never gates).

### 2026-07-28 — `risk_advisor.py` full hardcoded-threshold sweep (CLAUDE.md hard-rule-#1 fix), 1103/1103 passing

Follow-up to the Sharpe-only fix immediately below: the prior review had
flagged that `risk_advisor.py` still had more hardcoded thresholds outside
its scope, and the user asked to close all of them in one pass. Extracted
10 more constants to `constants.py`, all pure 1:1 extractions of
previously-inline literals — values and comparison operators unchanged, no
behavior change: `SHARPE_DRAG_RELATIVE_MAX = 0.7` /
`SHARPE_DRAG_MIN_WEIGHT_PCT = 3.0` (the per-ticker Sharpe-drag root_cause
selection cutoffs — selection-only, don't gate whether the Sharpe rec
fires), `PORTFOLIO_VOL_HIGH_PCT = 30.0` / `PORTFOLIO_VOL_MEDIUM_PCT = 25.0`
(the volatility priority ladder), `PORTFOLIO_DRAWDOWN_ACTION_MAX = -20.0` /
`PORTFOLIO_DRAWDOWN_HIGH_MAX = -30.0` / `PORTFOLIO_DRAWDOWN_OK_MIN = -10.0`
(the drawdown priority ladder) plus `DRAWDOWN_CONTRIB_MAX = -15.0` (the
per-ticker drawdown-contributor selection cutoff, same selection-only
pattern as the Sharpe-drag one), and `TAIL_RATIO_ACTION_MIN = 1.7` /
`TAIL_RATIO_HIGH_MIN = 2.2` (the tail-risk priority ladder). Documented all
10 in `docs/architecture.md`'s constants table. Added 12 new boundary-exact
regression tests to `tests/test_risk_advisor.py` (44 total now), importing
the real constants rather than re-hardcoding the literals — including a
dual-gate test for the Sharpe-drag selection (weight floor AND relative
Sharpe floor both required; neither alone is sufficient) and a
per-ticker-contributor-selection boundary test for drawdown. Per CLAUDE.md
hard rule #4: **Opus reviewer: SHIP, 0 blocking** — verified all 10 values
and all 8 changed comparison operators are exact 1:1 matches with no
`<`→`<=`/`>`→`>=` flip anywhere, grepped the repo confirming the only other
vol/drawdown/sharpe/tail classification logic (`app.py`'s metric-card
captions) is cosmetic display text with different boundaries that never
gates a recommendation — not a missed sibling ladder, traced each new
boundary test against the actual source's strict-inequality direction
(not just trusting test names) and confirmed each lands on the correct
side, and endorsed leaving the beta-trim simulation math (`0.50` sell
fraction, `0.3` new-beta floor, `0.85` fallback factor) and narrative-copy
percentages out of scope since those are recommendation-body math/copy
that runs only after a ladder has already decided to fire, not the
priority-gating class of literal this sweep targeted. **New non-blocking
follow-up flagged, not yet actioned:** `app.py`'s Max-Drawdown/volatility
metric-caption thresholds are bare literals that now numerically mirror
several of today's new constants (display-only, never gate) — a future
constant retune could silently drift the caption out of sync with the
actual gate; worth sourcing from `constants.py` in a later pass.

**This closes the entire class of finding** first surfaced during the
2026-07-27 `risk.py` Sharpe/Sortino review — every hardcoded priority-ladder
and selection-cutoff threshold in `risk_advisor.py` now lives in
`constants.py`.

### 2026-07-28 — `risk_advisor.py` Sharpe alert-ladder thresholds extracted to `constants.py` (CLAUDE.md hard-rule-#1 fix), 1091/1091 passing

Not a coverage-backfill batch — closes an open finding flagged by the
2026-07-27 Opus review of the `risk.py` Sharpe/Sortino bug fix:
`risk_advisor.py:256-257` hardcoded the Sharpe alert-ladder thresholds
(`< 0.8` → an action fires; `< 0.4` → HIGH vs MEDIUM) as inline literals
instead of importing them from `constants.py`, a direct CLAUDE.md hard-rule-#1
gap. Added three new constants to `constants.py`: `SHARPE_HIGH_RISK_MAX = 0.4`,
`SHARPE_MEDIUM_RISK_MAX = 0.8`, `SHARPE_STRONG_MIN = 1.0` (the third closes
the same ladder's OK-card threshold at `:318`, previously also an inline
`>= 1.0` literal). Replaced all three call sites 1:1 in `risk_advisor.py` —
values unchanged, this is a pure refactor with NO behavior change, not a
policy change. Documented the 3 new constants in `docs/architecture.md`'s
constants table. Added 4 new boundary-exact regression tests to
`tests/test_risk_advisor.py`, importing the real constants (not
re-hardcoding the literals) to pin: just-below-`SHARPE_HIGH_RISK_MAX` → HIGH,
exactly-at-`SHARPE_HIGH_RISK_MAX` → MEDIUM, exactly-at-`SHARPE_MEDIUM_RISK_MAX`
→ no rec (dead-zone floor), exactly-at-`SHARPE_STRONG_MIN` → OK card. Per
CLAUDE.md hard rule #4 (touches a scoring/recommendation gate file):
**Opus reviewer: SHIP, 0 blocking** — verified the 1:1 value match, grepped
the whole repo confirming no sibling Sharpe-ladder check was missed
elsewhere, confirmed the per-ticker "drag" selection logic just below
(`sharpe * 0.7`, `w >= 3.0`) reads the still-local `sharpe` variable and was
correctly untouched, and endorsed the naming (`_MAX`/`_MIN` suffixes read
more clearly than mirroring the `PORTFOLIO_BETA_*` triplet's naming, since
Sharpe is lower-is-worse — the inverse direction from beta). **New
follow-up flagged by the review, not yet actioned:** `risk_advisor.py:266`'s
per-ticker drag-selection literals (`0.7` relative-Sharpe multiplier, `3.0`
min-weight-to-be-named-a-drag) are themselves genuine hard-rule-#1
candidates, deliberately left out of this change's scope (a separate ladder
with its own boundary tests, bundling would obscure the "pure refactor, no
behavior change" guarantee this commit rests on) — belongs in the same
eventual sweep as the still-open volatility (30/25), drawdown (-20/-30/-10),
and tail-risk (1.7/2.2) literals in the same file, none of which are fixed
yet.

### 2026-07-28 — `news_intelligence.py` backfilled (76 tests, 100% coverage), 1087/1087 passing, no bug found — Explore survey now exhausted

Sixth and final fresh-prioritization pick from the same post-ranked-list
Explore survey. `news_intelligence.py` powers the News Intelligence
surface: `_significance()`'s tier×|sentiment|×position-weight×recency
scoring, `build_news_intelligence()`'s negative-news alert classification
(critical requires ALL THREE of compound≤-0.25, weight≥8.0, tier≤2 — any
single gate failing downgrades to warning), positive-news opportunity
detection gated on `NEWS_OPPORTUNITY_COMPOUND_MIN`/`NEWS_OPPORTUNITY_SCORE_MIN`
with the Reduce/Exit-ticker suppression split (a name under an active
trim/exit call is pulled OUT of `opportunities` into
`opportunities_suppressed` so a green "add on a pullback" card never
contradicts the Daily Brief's protect-capital directive), the 2+-aligned-
item sector digest (negative direction wins over positive when a sector
has both), and the full held-news feed — plus the two LLM-rescore helpers:
`rescore_news_items_llm()` (suppress-only — a Haiku score is only accepted
when it moves STRICTLY higher than VADER's, formalized as "equal is also
rejected, not just lower") and `rescore_headlines_llm()` (fully
bidirectional, per-headline swing capped at `SENTIMENT_LLM_MAX_SWING` in
either direction, ticker-aware prompt). Both LLM functions lazily
`import anthropic` inside a try/except and the dev venv has no `anthropic`
installed (per CLAUDE.md "never run locally") — so the test file installs
a fake `anthropic` module into `sys.modules` before each LLM test (a
minimal fake `Anthropic`/`Timeout`/`messages.create` chain returning a
scripted response or raising a scripted exception) to exercise the real
success/parsing/validation logic, not just the except-fallback path;
separate tests also confirm the genuine "anthropic not installed at all"
fallback path (no fake module installed) correctly returns the original
items unchanged / `None` per each function's documented contract. 76
tests, all passing on the first run except one coverage gap caught on the
first `--cov` pass (the markdown-code-fence-stripping + idx/score
validation branches inside `rescore_headlines_llm()` weren't hit by the
initial test set — added directly, not a source bug). No production bug
found. Now 100% covered (140 stmts, 0 missed).

**This exhausts the Explore-agent survey's ranked candidate list**
(`decision_quality.py` → `comparison.py` → `decision_journal.py` →
`earnings_advisor.py` → `perf_advisor.py` → `news_intelligence.py`, all
6 backfilled this session). Any further test-coverage work from here is a
fresh prioritization call over the ~51 remaining zero-coverage modules, none
of which the original survey flagged as containing real decision/gate
logic worth the effort.

### 2026-07-28 — `perf_advisor.py` backfilled (46 tests, 100% coverage), 1011/1011 passing, no bug found

Fifth fresh-prioritization pick from the same post-ranked-list Explore
survey. `perf_advisor.py` powers the Performance Attribution page:
`compute_attribution()` computes each held position's return over a
selected lookback window vs. SPY and vs. its sector ETF (via
`portfolio.SECTOR_ETF`), categorising it Alpha Generator (beats SPY by
≥5% AND beats its sector ETF by ≥3%) / Sector Rider (beats SPY by ≥5% but
not its sector — the classic "confusing beta with alpha" case, including
when no sector-ETF return data is available at all) / Alpha Destroyer
(trails SPY by ≥5%) / In Line, plus a dollar-alpha figure (opportunity
cost/benefit vs. holding SPY at the same weight); `build_perf_recommendations()`
then turns each category into a ranked, narrative recommendation card
(HIGH/MEDIUM/MONITOR/OK), with the Alpha Destroyer branch further splitting
into a 3-tier thesis assessment keyed off the composite score against
`COMPOSITE_HOLD` (score≥60 "hold with a 30-day review trigger" /
score≥`COMPOSITE_HOLD` "borderline, trim 40-50%" / below-floor "broken
thesis, exit or reduce to minimum"). 46 tests: `_f()`/`_opt()`'s NaN/None/
unparseable handling (incl. `_opt`'s must-not-treat-0.0-as-missing case),
`compute_attribution()`'s full empty/short-circuit ladder (empty port_df,
empty/None spy_df, insufficient SPY or per-holding history, missing
weight/market-value/held-data/close-column, zero-or-negative market value,
tz-aware index localization), all 4 category boundaries plus the
no-sector-data-defaults-to-Sector-Rider case, dollar-alpha sort order, and
the unmapped-sector-falls-back-to-SPY-ETF case; `build_perf_recommendations()`'s
empty/None/zero/negative-portfolio-value bail-outs, each category's
priority and narrative content, the Alpha Destroyer HIGH/MEDIUM split at
the -15% alpha boundary and all 3 thesis tiers (pinned exactly at the
`COMPOSITE_HOLD` boundary using the real imported constant, not a
hardcoded literal, per CLAUDE.md's doc/test-integrity discipline), the
Sector Rider title's alpha-vs-sector-unavailable fallback copy, and the
HIGH→MEDIUM→MONITOR→OK sort order. One initial test failure was the
author's own fixture mistake (used a sector string with no `SECTOR_ETF`
mapping, so it silently fell back to the "SPY" ETF instead of the intended
sector ETF — fixed by using a sector that's actually in the map), not a
source bug. No production bug found. Now 100% covered (116 stmts, 0 missed).

### 2026-07-28 — `earnings_advisor.py` backfilled (56 tests), 965/965 passing, no bug found

Fourth fresh-prioritization pick from the same post-ranked-list Explore
survey. `earnings_advisor.py` powers the Pre-Earnings Playbook: for each
held position with earnings in the next 30 days, `_recommend()`'s
10-branch priority ladder (EXIT on a Sell/Strong-Sell signal — beats
everything, even an oversized high-conviction position; REDUCE-oversized
when weight exceeds `SINGLE_NAME_TRIM_TRIGGER`, trimming back to
`SINGLE_NAME_CEILING`; REDUCE-weak-fundamentals; REDUCE-negative-revisions
— checked, and wins, before the poor-beat-rate REDUCE even when both
conditions hold; REDUCE-poor-beat-rate (CNBC enrichment, requires
`beat_rate` actually present); REDUCE-bearish-reaction-history;
MONITOR-stop-unavailable; MONITOR-stop-close-to-estimated-move;
HOLD_OR_ADD for high-conviction+positive-revisions with optional beat-rate/
bullish-reaction narrative extras; HOLD fallback), `_estimate_move()`'s
VaR×3 clamp-with-sector-fallback, `build_earnings_playbook()`'s date-window
filtering + urgency tiers (IMMINENT/SOON/AHEAD) + sector-specific watch
lists + the `gap_to_stop=None`-must-stay-None (not defaulted to 0)
data-integrity guard, and `build_earnings_catalyst_candidates()`'s
multi-gate watchlist scanner (not held, has CNBC context, beat-rate ≥
`EARNINGS_MIN_BEAT_RATE_ENTRY`, reaction not bearish, within the lookahead
window, composite ≥ `COMPOSITE_BUY`) with its bullish-reaction rank-score
multiplier. All 56 tests passed on the first run — no fixture-math
mistakes this time. No production bug found. Now 98% covered (145 stmts,
3 missed — the 3 lines are `_today_et()`'s default-today branch in each of
the module's 3 public/near-public functions, already exercised via the
equivalent explicit-`today=` pattern used throughout the rest of the batch).

### 2026-07-28 — `decision_journal.py` backfilled (31 tests, 100% coverage), found + fixed a real crash bug, 909/909 passing

Third fresh-prioritization pick from the same post-ranked-list Explore
survey. `decision_journal.py`'s `compute_patterns()` powers the Lessons
Learned / Decision Journal page: signal-followed vs. -ignored win/loss
accuracy, the costly-deviations and good-overrides lists, the free-text +
structured-category lessons library, and a one-line behavioral insight. 31
tests covering the empty/short-circuit paths (`None`/empty `trades_df`, no
`action` column, no `followed_signal` column, no SELL rows, no valid
yes/no-tagged rows), the followed/ignored accuracy math (incl. the
`None`-pnl-counts-as-neither-win-nor-loss case and case/whitespace
normalization of `followed_signal`), the costly-deviation (worst-first) and
good-override (best-first) sort orders, the lessons library's free-text-OR-
category inclusion rule and most-recent-first sort, the lesson-category
cross-tab's BROADER scope (all SELL rows with a category set, not just
rows with a valid yes/no `followed_signal` — deliberately wider than the
"lessons" list and the accuracy stats), and the full behavioral-insight
branch order (≥2 costly deviations always wins regardless of accuracy;
otherwise "signals working" / "overrides outperforming" / "similar" based
on a >10-point accuracy gap; `None` when data is too thin for either
signal). Most of the initial test run's 13 failures turned out to be
downstream fallout of the crash bug below (masked until it was fixed), not
independent test mistakes; the one genuine test-authoring slip that
remained after the fix — an expectation that didn't account for the
avg-loss figure always being negative — was corrected before the batch
finished.

**Found and fixed a real, NOT-subtle bug (not a test-writing mistake):**
`costly = ignored[ignored["_pnl"].apply(lambda x: x is not None and x < 0)].copy()`
(and the mirror `good =` line) crashes with `KeyError: '_pnl'` whenever
`ignored` (the ignored/overridden-signal subset) has zero rows — reproduced
independently with the simplest possible realistic input: a SINGLE trade
with `followed_signal="yes"` and nothing else. Root cause: `Series.apply()`
on a zero-row Series can't infer a return dtype from zero calls, so it
returns an `object`-dtype (not `bool`) empty Series; pandas then can't
recognize that as a boolean mask, and indexing a DataFrame with it collapses
the result to a columnless `(0, 0)` frame instead of the expected
"0 rows, same columns." The very next `.sort_values("_pnl")` then raises
`KeyError` because the column is gone. This fires for one of the most
common real scenarios imaginable — any user who has never logged an
override (100% `followed_signal="yes"`) — meaning the ENTIRE Decision
Journal / Lessons Learned page would have crashed with an unhandled
exception for exactly the kind of disciplined user this feature is meant to
reward. **Fixed** by skipping the `.apply()`-based filter entirely when
`ignored.empty` (using `ignored.copy()` directly, which already has zero
rows and the correct columns). Traced for the identical vulnerability
elsewhere in the same function: `followed`/`ignored`'s `_wins`/`_losses`
computations also call `.apply()` on possibly-empty slices, but those
results are immediately `.sum()`'d rather than used to index a DataFrame —
`sum()` of an empty Series is `0` regardless of dtype, so those call sites
are NOT vulnerable and were correctly left unchanged. Not Opus-reviewed per
CLAUDE.md hard rule #4 — this is a crash fix in retrospective display
analytics (Lessons Learned page), not a change to any gate, threshold, or
scoring/recommendation formula. **One minor, pre-existing UI-copy nit
noticed but NOT fixed** (flagged, not a functional bug): the
"costly deviations" behavioral-insight message reads "...with an avg loss
of $-150 per trade" — the negative sign is redundant/awkward next to the
word "loss" (the value is always negative by construction). A one-line
`abs()` fix, but out of scope for a test-coverage-plus-crash-fix commit;
left for a product-copy pass. Now 100% covered (67 stmts, 0 missed).

### 2026-07-28 — `comparison.py` backfilled (57 tests), 878/878 passing, no bug found

Second fresh-prioritization pick from the same post-ranked-list Explore
survey. `comparison.py` is the 2-ticker side-by-side Compare page engine:
`_winner()`'s tolerance-banded a/b/tie/None picker (reused across every
row), `_signal_winner()`/`_trend_winner()`'s label-rank tables, the
formatting helpers (`_fmt_pct`/`_fmt_money`/`_fmt_num`), `build_comparison()`'s
8-section assembly (Headline/Overview/Technicals/Business Quality/
Valuation/Sentiment & Analyst/Risk/Setup, incl. the R:R-ratio computation
that requires price > stop), `_compute_verdict()`'s composite-gap-first
logic (a <3-point gap always returns "tie" — with sub-factor tiebreaker
reasons cited when FCF-yield/beta/Sharpe deltas clear their own thresholds,
or a plain "decide on portfolio fit" fallback when even those are close;
confidence is "high" at a ≥10-point gap, "medium" otherwise), and
`_portfolio_fit()`'s already-held + sector-ceiling/elevated notes (incl.
the `Gate Weight (%)`-column fallback to `Weight (%)`). One of my own test
assertions was wrong (picked a 15-point gap and expected "medium"
confidence, when the code's own `>= 10 -> "high"` rule made it "high") —
a test-authoring mistake, not a source bug, fixed before the batch
finished. No production bug found. **One pre-existing, harmless
observation, not fixed:** `PORTFOLIO_BETA_ELEVATED`/`PORTFOLIO_BETA_CEILING`
are imported at the top of the file but never referenced anywhere in it —
dead import, no functional effect, out of scope for a test-coverage
commit to clean up unrequested. Now 97% covered (147 stmts, 5 missed).

### 2026-07-28 — `decision_quality.py` backfilled (68 tests), 821/821 passing, no bug found — first post-ranked-list pick

With the original ranked post-roadmap test-coverage list complete, used an
Explore-agent survey of ~20 remaining zero-coverage `stock_analyzer/`
modules to pick the next candidate: `decision_quality.py` ranked #1 —
richest decision-logic surface of the group (grade ladders, prep-tier
classification) and fully pure (pandas only, no Streamlit/DB/network at
module or function level), unlike several other candidates that are
mostly LLM-prompt builders (`premarket_stance.py`, `earnings_intel.py`,
`analyst_intel.py`, etc. — poor regression-test targets since their core
value is the LLM call, not testable branch logic).

Retrospective investor-improvement analytics with two responsibilities:
Feature B (`build_monthly_grades()`/`build_quarterly_grades()` — A-F grades
from win-rate + profit-factor + optional alpha-vs-SPY subscores, with an
overtrading penalty tier) and Feature C (`classify_trade_prep()`/
`classify_all_buys()`/`build_workflow_roi()` — per-BUY prep-tier
classification joined to realized outcomes). 68 tests covering the grade
letter/label/color mappings and boundaries, `_profit_factor()`'s
all-winner-is-None vs. all-loser-is-zero distinction, `_monthly_overtrading()`'s
rolling-12-month baseline (first 2 months always `None` — insufficient
baseline, not "not overtrading"), `_alpha_subscore()`'s clamped ±scale
mapping, `build_monthly_grades()`'s full assembly (win-rate subscore,
profit-factor subscore incl. the all-winner full-score case, the
has-alpha/no-alpha 3-subscore-vs-2-subscore composite, the >=2.0/>=1.5
overtrading penalty tiers, month sorting, the "Unknown"-month and
below-`DECISION_QUALITY_MIN_TRADES` exclusions), `build_quarterly_grades()`'s
trade-count-weighted composite averaging and malformed-month-str skip,
`build_spy_monthly_returns()`'s first/last-price-per-month return calc,
and `classify_trade_prep()`'s full tier ladder — including a non-obvious
rule worth pinning explicitly: thesis is a GATEKEEPER, not one of three
equally-weighted signals, so a trade with both analyst AND earnings
research saved but no thesis still classifies as "Cold Entry" (tier 0), not
"Thorough." Dates use plain `datetime.date` objects throughout (not ISO
strings) so the module's own ET-localizing `_parse_dt` short-circuits via
its `isinstance(date, not datetime)` passthrough, avoiding a UTC→ET
day-shift trap that a string date would otherwise introduce. Two of my own
early assertions were wrong (a profit-factor arithmetic slip: 200/100=2.0,
not 1.0; and a `numpy.bool_ is True` identity-comparison mismatch, fixed
with a `bool()` cast) — both test-authoring mistakes, not source bugs, both
caught and fixed before the batch finished. No production bug found. Now
97% covered (231 stmts, 8 missed).

### 2026-07-28 — `tax_advisor.py` backfilled (59 tests), 753/753 passing, no bug found — ranked post-roadmap test-coverage list now COMPLETE

Final module on the ranked post-roadmap test-coverage priority list. The
Tax Efficiency Advisor: `_build_open_lots()`'s FIFO tax-lot reconstruction
(BUY accumulation, SELL consuming oldest-lot-first across single/multiple
lots, SPLIT pro-rata share adjustment that preserves each lot's original
acquisition date, and the SPLIT-with-no-prior-lots seed-synthesis case),
`build_tax_analysis()`'s STCG/LTCG/MIXED classification with share-weighted
apportioned tax estimates, the HARVEST/HOLD_FOR_SIGNAL/WAIT/HOLD_FOR_LTCG/
LTCG_ELIGIBLE/MONITOR action ladder (incl. the harvest-blocked-by-conviction
rule — a Buy/Strong-Buy-rated position is never eligible for tax-loss
harvesting regardless of the unrealized loss), and the sort order; plus the
two awareness-only helpers used elsewhere on EXIT cards —
`holding_period_status()`'s near-LTCG flag and `wash_sale_risk()`'s 30-day
same-ticker-BUY-before-a-SELL check (boundary-inclusive, same-day-inclusive).
59 tests, all passing on the first run — no fixture-math mistakes this
time. No production bug found. Now 98% covered (191 stmts, 4 missed — the
4 lines are `_earliest_buy()`'s all-dates-invalid edge and the `if today is
None: today = _today_et()` defaulting line in `holding_period_status()`/
`wash_sale_risk()`, both already exercised in `build_tax_analysis()`'s
equivalent test and not worth a redundant third copy per the
"golden-value regression, not coverage-chasing" principle).

**This completes the ranked post-roadmap test-coverage priority list**
(started 2026-07-27 with `valuation.py`/`watchlist_advisor.py`/
`decision_bucket.py`, through `risk.py`, `macro_playbook.py`,
`headless_alert_engine.py`, `portfolio_health.py`, `rebalancer.py`,
`signal_hysteresis.py`, `position_lifecycle.py`, and now `tax_advisor.py`).
753 tests total, 3 real production bugs found and fixed along the way
(`risk.py`'s Sharpe/Sortino zero-volatility check, `headless_alert_
engine.py`'s NaN stop-breach fabrication, `portfolio_health.py`'s
concentration-callout duplication). Any further test-coverage work from
here onward is a fresh prioritization call, not a continuation of a
pre-ranked queue — see memory `project_test_automation` for the full
batch history and the still-open flagged (not fixed) findings.

### 2026-07-28 — `position_lifecycle.py` backfilled (29 tests, 100% coverage), 694/694 passing, no bug found

Continuation of the post-roadmap health check, next module on the ranked
priority list. `position_lifecycle.py` is pure logic (no I/O) — the
calm-advisor layer's held-position lifecycle classifier: `classify_
position_state()` returns one of exit/at_risk/settling/winning/established
via a strict precedence ladder where danger always beats age (a
freshly-opened position already breaching its stop is "exit", not
"settling"), and the critical rule that `age_days=None` (no trade-journal
history) must NEVER yield "settling" — missing data shouldn't silence
management. 29 tests covering every precedence interaction (exit beats
at_risk beats settling beats winning beats established), the exact
boundary at each of the 3 constants (`POSITION_AT_RISK_GAP_PCT`,
`POSITION_SETTLING_DAYS`, `POSITION_WINNING_PNL_PCT` — steady/threshold
value included vs. just past it), the `age_days=None` critical-rule cases,
and `lifecycle_badge()`'s per-state metadata incl. "established" being
intentionally un-badged. No production bug found. Now 100% covered
(14 stmts, 0 missed).

### 2026-07-28 — `signal_hysteresis.py` backfilled (32 tests, 100% coverage), 665/665 passing, no bug found — plus a naming-collision correction

Continuation of the post-roadmap health check, next module on the ranked
priority list. `signal_hysteresis.py` is pure logic (no I/O) — the
calm-advisor layer's Tier 2/Phase 2C "steady vs yesterday" annotator:
purely additive/cosmetic, it NEVER suppresses, reorders, or adds a Grow
Today pick, only attaches a `_hysteresis` marker when a pick's composite
score is within `HYSTERESIS_COMPOSITE_DELTA` of yesterday's AND its verdict
hasn't flipped. 32 tests covering `_pick_composite()`'s 3-key fallback chain
(`composite_score` → `score` → `total`, skipping non-positive/unparseable
values), `_pick_verdict()`'s `xref.verdict` priority over a bare `verdict`
key, and `apply_hysteresis()`'s full guard chain: ticker-not-in-snapshot,
missing/unparseable composites on either side, the exact delta boundary
(steady AT the band edge, not steady just past it), a custom `delta`
override, the verdict-mismatch block (only when BOTH sides are known and
differ — an unknown verdict on either side never blocks), case-insensitive
ticker/verdict matching, and the mutate-in-place/return-same-list contract.
No production bug found. Now 100% covered (46 stmts, 0 missed).

**Correction: `signal_hysteresis.py` is NOT the module tied to CLAUDE.md's
parked "Deterioration-card hysteresis" queue item**, despite the shared
word "hysteresis" — the two prior batches' "still-queued, ranked" notes
(in this file and in memory) wrongly conflated them. Verified against
CLAUDE.md directly: the parked queue item is specifically about
`exit_advisor.classify_deterioration_tier`'s WATCH/TRIM/EXIT tier
flip-flop damping (a deterioration-card display concern, gated on "a
deterioration card seen toggling on/off across days" being observed) —
completely unrelated to this module, which annotates fresh Grow Today buy
picks, not held-position deterioration tiers. Same "verify before
recommending from memory" discipline this app's own CLAUDE.md preaches for
docs, applied to this session's own prior note.

### 2026-07-28 — `rebalancer.py` backfilled (33 tests), 633/633 passing, no bug found

Continuation of the post-roadmap health check, next module on the ranked
priority list. `rebalancer.py` is pure computation (no I/O) — the Portfolio
Rebalancing Advisor: `compute_drift()` classifies each position OK/WATCH/
TRIM/ADD against `TOLERANCE_OK`/`TOLERANCE_WATCH` bands, and
`build_rebalance_plan()` generates the trim/add action lists with urgency
scoring, branch-specific rationale text, and two coordination gates — ADD
suppression when Risk Advisor's `risk_trim_set` already flags the ticker for
a reduce, and a News Intelligence critical/warning flag that either
suppresses ADD urgency to the floor or just attaches a warning. 33 tests:
`compute_drift()`'s status boundaries (exactly `TOLERANCE_OK`=2.0pp and
`TOLERANCE_WATCH`=5.0pp), the missing-target-defaults-to-current no-op case,
descending drift sort; `build_rebalance_plan()`'s TRIM urgency/rationale
3-way branch (Sell signal > below-`COMPOSITE_HOLD` score > "winner running"),
the WATCH-status urgency floor, the `shares_delta` floor of 1 even when
price is invalid, the ADD urgency/rationale 2-way branch
(`COMPOSITE_BUY`+Buy-signal vs. generic), the risk-trim-set suppression
(case-insensitive ticker match), the critical-vs-warning news-flag branch
(critical caps urgency at 5, warning leaves urgency unchanged), and the
totals/`rebalance_pct` aggregation. No production bug found this batch —
straightforward boundary-condition logic with no NaN/pandas-coercion traps
this time. Now 98% covered (122 stmts, 2 missed — the remaining `elif` at
`:255-256` is only reachable for an ADD/TRIM row whose `abs(drift_pp))` is
between `TOLERANCE_OK` and `TOLERANCE_WATCH`, which `compute_drift()` itself
never produces for ADD/TRIM status since those statuses require
`abs(drift_pp) > TOLERANCE_WATCH` — dead in practice unless a caller feeds
`build_rebalance_plan()` a hand-built `drift_df`, not chasing it further per
the "not coverage-chasing" principle).

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
