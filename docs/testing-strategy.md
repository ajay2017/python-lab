# Testing Strategy — DRISHTA

Last updated: 2026-08-17. How this app is verified, end to end: what the automated
`pytest` suite covers, what it deliberately doesn't, and what manual checking fills
the gap. Read this before deciding "does my change need a test, and what kind?"

---

## 1. Why two layers, not one

DRISHTA has no staging environment and is never run locally (CLAUDE.md hard rule
#3) — every change ships by pushing to `main` and letting Railway
auto-redeploy (primary since the 2026-08-15 cutover; Streamlit Cloud is a
dormant cold fallback). Until 2026-07-27 that meant the *only* quality gate was
manual testing before push (docs/architecture.md §9.2, prior wording). That's
still true for anything that requires a browser or live infrastructure — this
repo has no Selenium/Playwright, and adding one hasn't been judged worth the
complexity for a single-user app. But the highest-blast-radius code — the
decision logic that turns a price/score into a BUY/TRIM/EXIT call — doesn't need
a browser to verify, and a wrong gate there is the most expensive kind of bug.
So the strategy splits in two:

- **Automated (`pytest`)** — pure decision logic in `stock_analyzer/*.py`:
  gates, thresholds, tier classifications, scoring, sizing math. Fast,
  deterministic, runs on every push via CI.
- **Manual** — everything that needs a running Streamlit session, a real
  browser, live network/Supabase, or a real scheduled trigger: UI rendering,
  navigation, session_state coordination, cron jobs, email delivery, and
  whether the recommendations are actually good (not just internally
  consistent).

Neither layer replaces the other. A change to `stock_analyzer/constants.py`
still needs an Opus review (CLAUDE.md rule #4) even though pytest will catch a
boundary regression; a UI-only change to `app.py` still needs pytest-covered
logic underneath it to be trustworthy even though pytest can't see the UI at
all.

---

## 2. Automated coverage (pytest)

185 tests across 6 modules, added 2026-07-27 in one session (`docs/plans/test-automation.md`
has the full batch-by-batch history, design principles, and one real
architectural finding it surfaced and fixed):

| Module | What's covered |
|---|---|
| `constants.py` (invariants only) | Ordering/consistency relationships between thresholds — not literal values, which change on legitimate policy edits |
| `scoring.py` | `recommendation()` label boundaries at every `COMPOSITE_*` cutoff, `combined_score()` weighting |
| `concentration.py` | `gating_denominator()` margin/cash/stale/over-levered branches, `assess_add_concentration()`, `high_beta_share()` |
| `exit_advisor.py` | `classify_deterioration_tier()` WATCH/TRIM/EXIT boundaries and their non-obvious interactions, `risk_off_regime()`, `market_risk_posture()` |
| `portfolio.py` | `protective_stop()`/`stop_ladder()` ratchet logic, `manual_stop_wins()`, `trim_allocation()`, `diversification_score()` |
| `risk_advisor.py` | The 7-metric recommendation engine's HIGH/MEDIUM/OK ladders, including the "dead zones" where no rec should fire at all |
| `daily_briefing.py` | `_buy_candidates()`'s 6 independent add-to-winner suppression guards, `_trim_targets()`, `_recently_added()`, `_cross_reference()`'s verdict chain |
| `signal_reconciliation.py` | `reconcile_signals()`'s 4-tier verdict precedence, `lookup_composite()`, `effective_verdict_bucket()` |

**Design principles** (full detail in `docs/plans/test-automation.md`): pure
logic only (no Streamlit, no Supabase, no live network — synthetic fixtures
via `tests/conftest.py`); boundary/golden-value tests over coverage-chasing;
ordering invariants preferred over literal mirrors so a deliberate threshold
change doesn't require touching the test.

**Running it:**
```
pip install -r requirements-dev.txt   # one-time
pytest tests/ -v
```

**Mechanically enforced, not just documented practice, as of 2026-07-27.**
`.claude/hooks/pre_tool_checks.py` (a Claude Code `PreToolUse` hook, registered
in `.claude/settings.json`) intercepts `git commit`/`git push` tool calls and:
- On `commit`, if any staged file is under `stock_analyzer/` or `tests/`, runs
  `pytest tests/ -q` and **blocks the commit** (exit 2, failure output printed)
  if it doesn't pass.
- On `push`, always runs the suite first and **blocks the push** the same way
  — this covers a commit that landed before the gate existed, or from another
  session/tool, so a known-failing suite can never reach `origin/main`.
- **Recurring-defect gate (added 2026-08-04):** on `commit` when a staged file
  is `app.py`/`cron_runner.py`/under `stock_analyzer/`, and always on `push`,
  runs `scripts/check_antipatterns.py` and **blocks** (exit 2) if a change
  introduces a NEW instance of a bug-class our audits keep re-finding
  (offline-sentinel collapse `.get(...) or []`, dynamic `unsafe_allow_html`,
  naive `utcnow()`/`date.today()`). It is baseline-gated
  (`scripts/antipattern_baseline.json`), so the existing tail passes and only
  new instances fail. Fix at the source (see the shared helpers
  `stock_analyzer/util.py` `get_or_offline`/`safe_html` and
  `stock_analyzer/market_time.py` `now_et`/`today_et`), or — if genuinely
  acceptable — regenerate the baseline deliberately
  (`python scripts/check_antipatterns.py --init`).
- Fails open on infra problems (missing `.venv`, pytest not installed, a
  120s timeout, a missing gate script) — warns but does not block, since that's
  an environment gap, not a code problem.

**Caveat, learned the hard way from the rule #4 citation hook**
(memory `feedback_hook_enforcement`): a hook edit takes effect for Claude Code
sessions that start or reload `settings.json` *after* the change lands — a
session already running when the hook file changes does not retroactively
pick it up mid-session. It also only fires for git commands run *through*
Claude Code's Bash/PowerShell tools; it is not a real `.git/hooks/` script,
so a manual `git commit`/`git push` from an external terminal is not covered.

**CI:** `.github/workflows/tests.yml` runs the suite on push/PR touching
`stock_analyzer/**` or `tests/**`; `.github/workflows/antipatterns-check.yml`
runs `check_antipatterns.py` on push/PR touching `app.py`/`cron_runner.py`/
`stock_analyzer/**` (a second, independent line behind the local hook, same as
the docs-sync tripwire). These are a **pre-push safety net, not a
deploy gate** — neither Streamlit Cloud nor Railway consults GitHub Actions,
so a red ❌ shows up on the commit but does not block the redeploy. Making it
a required branch-protection check is deliberately deferred until the suite
has a longer track record of not being flaky. (This CI check is now a second,
independent line of defense behind the local hook above, not the only one.)

---

## 3. What automated tests do NOT cover, and why

This is deliberate scope, not an oversight — re-read this before asking
"should we add a test for X":

- **`app.py` (Streamlit UI/orchestration, ~35,000 lines).** Too coupled to
  `st.session_state`, widget keys, and `_pending_page` navigation to unit-test
  cheaply, and it's not where the decision logic lives (that's the whole point
  of the `stock_analyzer/` split). A UI bug here needs a human looking at a
  rendered page, not a pytest assertion.
- **Live data-provider behavior** (yfinance/Finnhub/FMP actual responses, rate
  limits, real network failures). Every pytest fixture is synthetic by design
  — testing against live APIs would make the suite slow, flaky, and quota-
  burning. `stock_analyzer/providers/selftest.py` exists for this instead (§4).
- **Supabase read/write correctness in production** (RLS policies actually
  blocking/allowing as intended, real schema drift, actual cron write paths).
  Pytest never touches the real DB.
- **Cron/headless jobs** (Railway native Cron Job services → `cron_runner.py` →
  `headless_alert_engine.py`; 7 lanes as of 2026-08-18, migrated off GitHub
  Actions on 2026-08-07 — `.github/workflows/alerts.yml` is now
  manual-dispatch-only). These run on Railway's schedule infrastructure, not
  in-process — pytest can exercise the pure functions they call, but not "did
  the scheduled trigger actually fire and did the email arrive." The
  dead-man's-switch (`_notify_failure` + `cron_heartbeat` + 🩺 System Trust)
  exists precisely because this layer is untestable from here.
- **Whether the recommendations are actually good** (real alpha, not just
  internally consistent). Pytest can prove `recommendation(65) == "Buy"`
  forever; it can't prove that Buy-labeled picks have historically beaten SPY.
  That's a different, periodic kind of check — see §4.6.

### 3.1 The display/runtime layer — a demonstrated gap, not a theoretical one

Added 2026-08-17 after **three defects in one session** landed where neither
`pytest` nor the commit hook can reach. Worth stating concretely, because "the
suite is green" was doing more reassurance than it had earned:

1. **A `None` that became `+nan` on screen.** `risk.rate_sensitivity_per_ticker`
   correctly returned `None` for an unmapped sector, and the function-level tests
   proved it. But `pd.DataFrame(rows)` coerces a MIXED float/None column to
   `float64`, so `None` became `NaN`, and the display guard `if v is not None`
   silently rendered `"+nan"`. The tests asserted the *return value*; the bug was
   in the *rendering*. (An all-`None` column keeps object dtype and formats fine —
   which is exactly why it passes in isolation and breaks on a real book.)
2. **A `NaN` that made a Plotly bar vanish.** Same value on the 🌐 Macro chart: a
   NaN x-coordinate doesn't render badly, it removes the bar — the position looks
   *absent* rather than *unknown*. No test renders a figure.
3. **A `NameError` on a live page.** A call to `safe_html(...)` where `app.py`
   binds `_safe_html`. `py_compile` sees valid syntax; the suite never imports and
   renders `app.py`; the branch only executes when an unrated holding exists.

**The rule this yields:** when a change's effect is *what the user sees*, test at
the layer the bug can live in, or accept that review — not the suite — is the
gate. `tests/test_risk.py::test_mixed_none_column_formats_as_dash_not_nan` is the
worked example: it asserts the pandas coercion happens, asserts the naive guard
*does* produce `"+nan"`, then asserts the correct guard doesn't. It also states
honestly in its own docstring that it re-implements the formatter rather than
importing it, so it documents the bug class without regression-guarding the call
sites. Extracting a shared `fmt_signed` helper into `util.py` and testing that
would close the remaining gap — queued, not done.

**Corollary for the review economy:** this is the strongest argument for the
`reviewer` lane in CLAUDE.md. All three were caught by an Opus reviewer
re-deriving from the diff in a separate context, none by a deterministic gate.

---

## 4. Manual testing — what and when

### 4.1 Post-deploy smoke test (every push, ~2 minutes)

After every push, wait ~2 min for both Streamlit Cloud and Railway to
auto-redeploy, then hard-refresh (Ctrl+F5) and check:

- [ ] Home loads with no red exception traceback and no unexpected
      "Could not load" banners
- [ ] The sidebar "Data Health" expander isn't red (auto-expands if it is —
      see §4.4)
- [ ] Whatever page/feature the push actually touched renders as expected
- [ ] No new console/browser errors if the change touched client-side
      rendering (charts, custom HTML)

This generalizes the existing informal step in `DEVELOPMENT.md`'s "Coming
back to this project" checklist ("open the deployed app and click through
Home / Today's Brief / Watchlist / Stock Analysis to confirm everything
still loads") into something to actually run after *every* push, not just
after time away.

### 4.2 Feature-specific verification (when a UI/behavior change ships)

Click through the specific thing that changed, not just Home. For a new UI
element, follow the established mockup-first workflow (memory
`feedback_mockup_first_ux`): mock as static HTML → get approval → build →
verify the shipped page actually matches the approved mockup. This was used
for every card in the Summary-page pointer-cards work
(`docs/plans/summary-page-pointer-cards.md`) and is the standing convention
for any nav/layout change, not just that one feature.

### 4.3 Periodic full-nav sweep (occasional, or after a large change)

Visit every page in the sidebar nav at least once, watching for exceptions
or obviously broken layout — a lighter-weight version of the 12-row smoke
checklist in `docs/plans/railway-migration.md` (4c), which was built for the
Railway pilot cutover specifically (password gate, brute-force lockout, trade
journal BUY/SELL round-trip, AI Insights, Analyst Coverage, Macro calendar,
Data Health, cron email, read-only viewer). Reuse that table verbatim after
any change that could plausibly touch multiple pages at once (a shared
helper, a session_state key, a constants change with wide blast radius).

### 4.4 Data-provider / live-data spot checks

- **Passive:** the sidebar "Data Health" expander (`app.py` ~2096, backed by
  `stock_analyzer/api_health.py`) auto-expands whenever any source
  (Finnhub/Yahoo/FMP/FRED/Supabase/bundle-cache) is yellow or red — glance at
  it after any data-layer change.
- **Active, deeper diagnosis:** `python -m stock_analyzer.providers.selftest AAPL MSFT`
  — an offline CLI script that hits the live APIs directly and prints each
  provider's response shape so you can eyeball that the canonical schema is
  populated. Not exposed in the UI; a dev-only tool, run after touching
  `stock_analyzer/providers/` or the orchestrator in `data.py`.

### 4.5 Cron/headless job verification

`.github/workflows/alerts.yml` runs `cron_runner.py` on a schedule (10 UTC
slots straddling EST/EDT) and includes a dead-man's-switch step that emails a
failure notice via Resend if a step throws. Per its own in-file comment, this
**cannot** catch GitHub silently disabling the schedule after 60 days of repo
inactivity, nor a "ran green but fetched nothing" run — the workflow comment
itself suggests pairing it with an external heartbeat monitor (e.g.
healthchecks.io), which is **not yet set up**. Until it is, periodically:
- Check the Actions tab run history for `alerts.yml` for unexpected gaps or a
  string of green-but-suspiciously-fast runs.
- After changing anything `cron_runner.py`/`headless_alert_engine.py`/`notify.py`
  touches, don't just trust the pytest-covered pure functions — wait for (or
  manually trigger via `workflow_dispatch`) a real run and confirm the actual
  email arrives with correct content.

### 4.6 Periodic recommendation-quality / engine health check

A different cadence and purpose from everything above: not "did it crash" but
"is the engine's output still good." This is the Supabase-SQL-based
methodology already used twice this project (`project_rec_engine_evaluation`
memory, 2026-06-18 and 2026-07-26 checkpoints — the second one also found and
fixed a real blank-alpha bug, commit `51b2441`): query the `recommendations`
table for data-quality red flags (blank/NaN fields, contradictory signals
across buckets) and, once enough graded history exists, actual alpha by
verdict tier. Run this occasionally (roughly monthly, or whenever something
about the recommendations feels off) — not part of the per-push routine.

---

## 5. Quick decision guide

| Change touches | Needs |
|---|---|
| A pure function in `stock_analyzer/*.py` (gate, scoring, sizing) | pytest (add/extend a test in the matching `tests/test_*.py`) + run the full suite before committing |
| `stock_analyzer/constants.py`, a gate, or a scoring/rec formula | The above, **plus** an Opus review cited in the commit body (CLAUDE.md rule #4) |
| `app.py` UI/layout only, logic underneath already tested | §4.1 post-deploy smoke test + §4.2 feature click-through; mockup-first if it's a new element |
| A new page or nav change | §4.3 full-nav sweep, since session_state/nav-key issues tend to ripple |
| `providers/`, `data.py`, or anything data-source-related | §4.4 Data Health glance + `selftest.py` if something looks wrong |
| `cron_runner.py`, `headless_alert_engine.py`, `notify.py` | §4.5 — don't trust a green pytest run alone; confirm a real scheduled/dispatched run |
| Nothing code-related feels off, but the recs seem "off" lately | §4.6 — run a health-check pass, don't guess |

---

## Pointers

| Need | Where |
|---|---|
| Full pytest batch history, design principles, the verdict-divergence finding it surfaced | `docs/plans/test-automation.md` |
| Latest pytest pass/fail counts + coverage, and run history | `docs/test-results.md` |
| The Railway-pilot-specific 12-row smoke checklist | `docs/plans/railway-migration.md` §4c |
| Rec-engine health-check history and methodology | memory `project_rec_engine_evaluation` |
| Mockup-first UI convention | memory `feedback_mockup_first_ux` |
