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

## 1. Latest run — 2026-07-28 (post-ranked-list, batch 13: `earnings_advisor.py`)

**965 passed, 0 failed, 0 skipped** (`pytest tests/ -v`; with `--cov`
active: 10.87s). Python 3.14.6, pytest 8.4.2, pytest-cov 5.0.0, in the local
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
| `test_rebalancer.py` | 33 | `rebalancer.py` (drift classification, trim/add urgency + rationale, News Intelligence / Risk Advisor coordination gates) |
| `test_signal_hysteresis.py` | 32 | `signal_hysteresis.py` (calm-advisor "steady vs yesterday" annotator) |
| `test_position_lifecycle.py` | 29 | `position_lifecycle.py` (held-position lifecycle classifier: exit/at_risk/settling/winning/established) |
| `test_tax_advisor.py` | 59 | `tax_advisor.py` (FIFO tax-lot reconstruction, STCG/LTCG classification, harvest/wait action ladder, holding-period + wash-sale awareness helpers) |
| `test_decision_quality.py` | 68 | `decision_quality.py` (monthly/quarterly Decision Quality grades, Workflow ROI prep-tier classification) |
| `test_comparison.py` | 57 | `comparison.py` (2-ticker Compare page: per-row winner picking, composite-first/sub-factor-tiebreak verdict, portfolio-fit notes) |
| `test_decision_journal.py` | 31 | `decision_journal.py` (signal-followed vs. ignored accuracy, costly-deviation/good-override lists, lessons library, behavioral insight) |
| `test_earnings_advisor.py` | 56 | `earnings_advisor.py` (Pre-Earnings Playbook: EXIT/REDUCE/MONITOR/HOLD/HOLD_OR_ADD ladder, watchlist earnings-catalyst scanner) |
| **Total** | **965** | |

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
| `rebalancer.py` | 122 | 2 | **98%** |
| `signal_hysteresis.py` | 46 | 0 | **100%** |
| `position_lifecycle.py` | 14 | 0 | **100%** |
| `tax_advisor.py` | 191 | 4 | **98%** |
| `decision_quality.py` | 231 | 8 | **97%** |
| `comparison.py` | 147 | 5 | **97%** |
| `decision_journal.py` | 67 | 0 | **100%** |
| `earnings_advisor.py` | 145 | 3 | **98%** |
| `thesis_red_team.py` | 84 | 11 | 87% |
| `structural_scanner.py` | 144 | 62 | 57% |
| `exit_advisor.py` | 191 | 131 | 31% |
| `portfolio.py` | 427 | 300 | 30% |
| `daily_briefing.py` | 773 | 546 | 29% |

**Whole-`stock_analyzer/` total: 13,794 stmts, 10,111 missed, 27%** (up —
`earnings_advisor.py` moving from 0% to 98%, the 4th fresh-prioritization
pick from the same Explore-agent survey). ~53 modules remain with zero
tests; `perf_advisor.py`/`news_intelligence.py` are the survey's remaining
lower-priority candidates. Not a target to chase for its own sake per
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

## 2. History

*(Newest first. Add a new entry above this line each time the suite is run
and the result is worth recording — at minimum, after any batch/module
addition or whenever a run fails.)*

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
