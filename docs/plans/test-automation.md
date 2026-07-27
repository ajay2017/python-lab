# Regression Test Automation — Design Plan

**Date:** 2026-07-27
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** Batch 1 (constants invariants + `scoring.py` + `concentration.py`) SHIPPED 2026-07-27.

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
2. **`exit_advisor.py` — pending.** `classify_deterioration_tier()` (WATCH/TRIM/
   EXIT boundaries — the tier this book's own hysteresis gap already lives
   against), `risk_off_regime()`, `market_risk_posture()`, `_trim_floor`/`_exit_floor`.
3. **`portfolio.py` stop/sizing — pending.** `protective_stop()`,
   `manual_stop_wins()`, `stop_ladder()` (a known trap already exists here per
   `project_stop_ladder_and_display` memory — raw `stop` vs. the ratcheted
   `_sa_holding['Stop']` — a good regression-test candidate since it's already
   caused one bug), `trim_allocation()`, `diversification_score()`.
4. **`risk_advisor.py` — pending.** `build_risk_advisor_recommendations()`
   against a fixed synthetic portfolio fixture in `tests/conftest.py`.

## Explicitly out of scope

- `app.py` — Streamlit orchestration/UI, too coupled to `st.session_state` and
  live secrets to unit-test cheaply; not where the pure decision logic lives.
- Any live network/Supabase-hitting test — everything runs on synthetic
  fixtures so the suite stays fast and deterministic in CI.
- Making the GitHub Actions run a required branch-protection check — revisit
  once Batches 2-4 exist and the suite has a track record of not being flaky.
