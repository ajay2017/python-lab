# Regression Test Automation — Design Plan

**Date:** 2026-07-27
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** Original 4-batch plan COMPLETE 2026-07-27 (119 tests). Batch 5
(`daily_briefing.py`'s buy-candidate suppression funnel + weak-large flag,
`concentration.py`'s `high_beta_share()`) added 2026-07-27, same session (148
tests). Batch 6 (`signal_reconciliation.py` + `daily_briefing._cross_reference()`)
added 2026-07-27, same session, at the user's request to cover the
"informational labeling" candidate too. **Surfaced a real, currently-shipping
finding while writing it — see Batch 6 below.** 180 tests total, all passing
locally in `.venv` (Python 3.14.6).

## Why

The repo had zero tracked test files (confirmed via `git ls-files`) despite
`stock_analyzer/` driving real allocation decisions on a real portfolio, with
every push going straight to Streamlit Cloud / Railway — no staging
environment. The Opus-review requirement (CLAUDE.md hard rule #4) is a human
judgment control on changes to gates/scoring; it does not catch a change to
one gate silently breaking an unrelated one weeks later. This plan adds a
lightweight `pytest` regression suite as that missing safety net — not for
coverage-chasing, but targeted at the highest blast-radius pure-logic
functions first.

## Design principles

- **Pure logic only.** Tests exercise `stock_analyzer/*.py` functions directly
  — no Streamlit, no `st.session_state`, no Supabase, no live network/yfinance
  calls. This mirrors the existing "logic in `stock_analyzer/`, UI in `app.py`"
  split (CLAUDE.md coding conventions) — `app.py` itself is out of scope for
  this suite.
- **Golden-value / boundary regression, not coverage-chasing.** ~20-30
  targeted tests across the highest-blast-radius functions, not an attempt at
  broad coverage %. A test pins a known input to a known output at and around
  a real decision boundary (e.g. `recommendation(COMPOSITE_BUY)` must be
  exactly "Buy", one-tenth below must not be).
- **Ordering/consistency invariants over literal mirrors.** Where possible,
  tests assert *relationships* between constants (e.g. `DETERIORATION_WATCH_DD_PCT
  < DETERIORATION_TRIM_DD_PCT < DETERIORATION_EXIT_DD_PCT`) rather than just
  restating a literal value from constants.py. A literal-mirror test breaks on
  every legitimate policy edit and catches nothing a diff review wouldn't; an
  invariant test catches a fat-finger reorder that silently makes a tier
  unreachable, and does NOT need touching when a threshold is deliberately
  moved (as long as the ordering holds).
- **Additive tooling.** `pytest`/`pytest-cov` live in `requirements-dev.txt`,
  separate from `requirements.txt`, so the Streamlit Cloud / Railway install
  footprint is unchanged.
- **CI is a pre-push safety net, not a deploy gate.** `.github/workflows/tests.yml`
  runs on push/PR touching `stock_analyzer/**` or `tests/**`. Neither Streamlit
  Cloud nor Railway consults GitHub Actions, so this doesn't block a bad deploy
  by itself yet — it gives a red ❌ on the commit before a hard-refresh finds
  the regression by hand. Wiring a branch-protection required-check is a
  deliberate later step once the suite has run clean for a while (not done as
  part of Batch 1).
- **A threshold edit that breaks a boundary test is a feature, not friction.**
  Since gate/threshold changes are already required to go through Opus review
  (CLAUDE.md rule #4) and be cited in the commit body, a failing boundary test
  forces the same commit to update its golden expectation — making the policy
  change mechanically visible in the diff.

## Batches (priority = blast radius, not file order)

1. **Constants invariants + `scoring.py` + `concentration.py` — SHIPPED 2026-07-27.**
   `tests/test_constants_invariants.py` (ordering invariants across
   composite/deterioration/regime/sentiment/beta ladders), `tests/test_scoring.py`
   (`recommendation()` boundary behavior at every COMPOSITE_* cutoff — this is
   the exact function that had a real 72/58-vs-75/65 drift bug historically —
   plus `combined_score()` weight application), `tests/test_concentration.py`
   (`gating_denominator()` margin/cash/stale/over-levered branches,
   `assess_add_concentration()` single-name/sector breach + elevated + trim-math
   boundaries). 31 tests, all passing locally in `.venv` (Python 3.14.6).
2. **`exit_advisor.py` — SHIPPED 2026-07-27.** `tests/test_exit_advisor.py` (32
   tests): `_trim_floor`/`_exit_floor` ATR-scaling + ceiling caps;
   `classify_deterioration_tier()` boundaries for all three tiers plus the
   non-obvious interactions the docstring calls out — WATCH is
   relative-strength-independent but requires `trend_broken_now`; TRIM does
   NOT require `trend_broken_now` (only the 2-of-3 `below_ma_count` history
   matters); a high-ATR name gets a wider floor before TRIM fires at the same
   raw drawdown %; EXIT's three escalation paths (underwater vs. cost, $250+
   loss, deep-drawdown shortcut) and that escalation is never silenced by
   settling grace while base TRIM/WATCH are; `risk_off_regime()`'s two
   independent legs (trend break, VIX spike) degrading to "not tripped" on
   short history rather than fabricating a read; `market_risk_posture()`'s
   `armed` flag requiring BOTH fragility ≥ caution AND regime risk-off (fragile
   alone does not arm the de-risk action).
3. **`portfolio.py` stop/sizing — SHIPPED 2026-07-27.** `tests/test_portfolio.py`
   (28 tests): `protective_stop()` ratchet-rung boundaries and the "ATR stop
   can still beat a modest ratchet floor" nuance; `manual_stop_wins()`'s
   tight-vs-loose boundary (a manual stop wins only when at least as tight —
   the split-brain closed 2026-07-07 this predicate exists to prevent);
   `stop_ladder()`'s `auto_source` "which number actually binds" logic
   (ratchet vs. ATR), manual-override gating, `stopped_out` flag, and
   ratchet-rung/next-tier bookkeeping; `trim_allocation()`'s greedy
   full-exit-then-partial-trim distribution and shortfall reporting;
   `diversification_score()`'s score formula (caught a wrong assumption while
   writing the test — zero correlation scores 50, the MIDPOINT of the
   anti-correlated(-1)=100 .. lockstep(+1)=0 scale, not 100 — fixed the test,
   not the source, since the source is correct and this is exactly the kind
   of easy-to-misread scale a regression test should pin) and its danger/
   warning risk-pair threshold classification.
4. **`risk_advisor.py` — SHIPPED 2026-07-27.** `tests/test_risk_advisor.py`
   (28 tests), built against a reusable `make_risk_advisor_inputs()` fixture
   factory added to `tests/conftest.py` (a "safe" default portfolio where every
   metric overridable is otherwise designed not to trigger a rec — each test
   varies only the one field it's checking). Covers the HIGH/MEDIUM/(OK)
   priority ladder for all 5 portfolio-level metrics (beta, Sharpe, volatility,
   drawdown, tail-risk/CVaR-VaR ratio) plus sector and single-name
   concentration. Specifically pins the "dead zones" between a metric's action
   ladder and its OK band where NO recommendation should fire at all (Sharpe
   0.8-1.0, drawdown -20%..-10%) — these are easy to silently break in a
   refactor since there's no rec object to assert against, only an absence.
   Also pins that the "Other" unclassified-sector bucket is excluded from the
   top-sector pick (a real sector overweight could otherwise hide behind it)
   and that single-name overweight is gated to `score >= WEAK_CONVICTION_SCORE`
   so it never double-fires with daily_briefing's separate weak-large flag.

5. **`daily_briefing.py` buy-candidate funnel + weak-large flag, `concentration.py`'s
   `high_beta_share()` — SHIPPED 2026-07-27.** `tests/test_daily_briefing.py`
   (24 tests) + 5 tests appended to `tests/test_concentration.py`, using a new
   `make_port_df()` fixture builder in `tests/conftest.py`. Covers `_trim_targets()`
   (only beta/sharpe HIGH/MEDIUM recs qualify as a trim conflict — volatility/
   drawdown/tail-risk recs recommend diversifying, not trimming, so they must
   NOT suppress an add-to-winner), `_recently_added()`'s cooldown boundary, and
   — the main piece — `_buy_candidates()`'s add-to-winner block, which has SIX
   independent suppression `continue` guards (act-today, recent-add cooldown,
   risk-advisor trim conflict, single-name ceiling, drift-trim overweight,
   deterioration WATCH) each pinned with its own test, since a refactor
   dropping just one guard would be invisible without a test targeting that
   exact path. Also verifies the scanner-pick price capture (regression for
   this session's earlier price_at_surface fix) and, via `_review_list()`,
   that the weak-large flag fires ONLY when `score < WEAK_CONVICTION_SCORE` —
   proving from the daily_briefing side what Batch 4 already proved from the
   risk_advisor side, that the two "overweight" surfaces partition cleanly on
   that score boundary and never double-fire on the same ticker.
   `high_beta_share()`'s None-weight/None-beta exclusion from both numerator
   and denominator, and its inclusive `>=` threshold boundary.

6. **`signal_reconciliation.py` + `daily_briefing._cross_reference()` —
   SHIPPED 2026-07-27**, at the user's explicit request to cover the
   "informational labeling" candidate Batch 5 had deliberately left out.
   `tests/test_signal_reconciliation.py` (21 tests): `classify_composite_direction()`'s
   label-beats-score precedence and score-boundary fallback;
   `reconcile_signals()`'s 4-tier verdict ladder (`skip`/`caution`/`verify`/`go`)
   — several tests exist specifically to pin the CHECK ORDER, not just each
   condition alone, since the composite-conflict skip check runs before the
   negative-news skip check, which runs before the earnings-caution check,
   which runs before the missing-composite verify check; `lookup_composite()`'s
   port_df-before-composites-dict source priority and its "empty match must
   fall through, not false-positive" edge case. `tests/test_daily_briefing.py`
   gained 11 more tests for `_cross_reference()`'s OWN verdict chain
   (`confirmed`/`mixed`/`conflicted`/`caution`/`unverified` — a DIFFERENT
   5-tier system from `reconcile_signals()`'s 4-tier one), including the
   documented earnings+conflict ESCALATION the source comment calls out.

   **Real finding surfaced while writing this batch (not a bug fix — documented,
   not yet decided whether to address):** `_cross_reference()` computes TWO
   independently-derived verdicts in the same returned dict — the legacy
   `verdict` field (its own if/elif chain, includes an analyst-revisions
   layer that reconcile_signals has no concept of) and `verdict_reconciled`
   (from `reconcile_signals()`, which only sees momentum/composite/earnings/
   news). `app.py` uses BOTH live: the legacy `verdict` drives sorting/
   coloring/grouping in most surfaces (e.g. the "confirmed" bucket split at
   app.py ~22648), while `verdict_reconciled['one_liner']` is rendered as the
   prominent explanation text (app.py ~5897, ~7479). `test_cross_reference_legacy_and_reconciled_verdicts_can_diverge`
   pins a concrete, realistic case: a held position with a Strong-Buy
   composite that agrees with technical momentum (no earnings/news conflict)
   but a recent analyst-revisions downgrade — legacy `verdict` = `"mixed"`
   (revisions count as a legacy-only conflict) while `verdict_reconciled['verdict']`
   = `"go"` (reconcile_signals never sees revisions at all). A user could see
   a card sorted/colored as "Mixed" while its prominent one-liner reads "Go —
   All Signals Agree" — a real, currently-shipping inconsistency, not a test
   artifact. **Not fixed here** — flagged for the user to decide whether/how
   to reconcile (e.g. teach `reconcile_signals()` about revisions, or drop the
   legacy `verdict` field in favor of `verdict_reconciled` everywhere).

## Explicitly out of scope

- `app.py` — Streamlit orchestration/UI, too coupled to `st.session_state` and
  live secrets to unit-test cheaply; not where the pure decision logic lives.
- Any live network/Supabase-hitting test — everything runs on synthetic
  fixtures so the suite stays fast and deterministic in CI.
- Making the GitHub Actions run a required branch-protection check — deferred
  indefinitely; revisit if the suite ever flakes or a real regression slips
  through despite a green run.

## What's next (not part of this plan, noted for a future session)

All 6 batches shipped, 180 tests. No further candidates are currently queued.
The one open item is the Batch 6 finding above (legacy vs. reconciled verdict
divergence) — a product/architecture decision for the user, not a testing gap.
