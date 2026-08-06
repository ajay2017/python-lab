# Correlation Under Stress (Pass #1 Deferred Concept D2)

**Status:** DESIGNED 2026-08-05 (planner/Opus design pass), decisions locked with user, awaiting mock approval before code.

**Origin:** `docs/plans/next-evolution-strategy.md`'s Deferred Concepts D2 — "The portfolio's correlation structure in calm markets differs from stress... cheap, decision-relevant comparison." Picked up as the next thread after the Engine Track Record arc closed 2026-08-05.

---

## The idea

Names that show near-zero correlation in a calm 6-month window can converge to 0.9+ during an actual selloff — exactly when diversification is supposed to protect you and doesn't. This feature compares the portfolio's existing calm correlation structure (already computed, already clustered, already on screen) against the same holdings' correlation during a real historical crash window.

## Verification finding — the brainstorm's framing was overstated

Initial assumption: "cheap, reuses existing stress-test + correlation-cluster infrastructure." **Half true.** The *calm* correlation matrix + clustering (`portfolio.correlation_matrix()`, `portfolio_intelligence.correlation_clusters()`) and the two correlation-tier constants already exist and are directly reusable. But the *stress* half does not — `stress_test.fetch_historical_drawdowns()` fetches real historical daily prices for held tickers during a past crash, but collapses each ticker independently to a scalar peak-to-trough %; it never aligns multiple tickers into one frame or computes `.corr()`. No code anywhere in the repo computes correlation over two different regimes and compares them. Building the stress-side correlation matrix is genuinely new logic, not a display wrapper.

## Design verdict: PROCEED

No blocker. No new constant needed — reuses `CORR_HIGH_PAIRS_THRESHOLD` (0.65) / `CORR_DANGER_PAIRS_THRESHOLD` (0.80) for both calm and stress cluster-forming and for the "newly converged" boundary. (Explicitly does NOT reuse `REDEPLOY_CORR_CORRELATED_MIN`=0.70 — a different, unrelated rebalancer-feature constant.)

## Decisions LOCKED 2026-08-05 (with user)

1. **Stress window:** ✅ **Default COVID 2020 crash**, user-switchable via selectbox to the 2022 rate shock or GFC 2008 (the 3 windows already defined in `stress_test.HISTORICAL_WINDOWS`). Not all 3 at once — triples fetch cost and buries the one insight under three tables, failing the "does this decrease decision load?" test.
2. **Placement:** ✅ **Extends the existing 🧩 Intelligence → 🕸️ Correlation Clusters tab**, as a new section below the calm clusters already rendered there — NOT the 🔥 Stress Testing tab. The delta is only legible sitting next to its calm baseline; the Stress Testing tab has no calm-correlation baseline on screen to compare against.
3. **Output emphasis:** ✅ **Lead with a sorted "newly converged pairs" table** (pairs below the warning threshold in calm, at/above it in stress, sorted by delta) **+ one headline avg-calm→avg-stress delta number**, supported by the stress cluster list (same tiering the calm block already uses). No dual heatmap — keeps it scannable.

## The critical honesty requirement — calm-advisor framing, not alarmism

A short, sharp selloff (COVID's window is ~25 trading days) **mechanically inflates correlation independent of any real structural link** — in a fast crash everything drops together. The UI **must** state the window length (`n_window_days`) and frame the finding as *"in that crash your book moved as one"* (awareness), never *"these names are secretly linked"* (which would overclaim a hidden causal relationship the data can't support). This mirrors the app's existing calm-advisor posture (`feedback_calm_advisor_not_daytrading`).

**Coverage honesty:** a ticker that didn't exist yet during an older window (e.g. GFC 2008 for a 2015 IPO) is excluded from that window's matrix — the caption must say "X of Y held names traded through this window; Z excluded," never silently drop them with no explanation.

## Architecture

**New producer** `fetch_stress_window_returns(scenario_id, tickers) -> pd.DataFrame | None` — `stock_analyzer/stress_test.py`, alongside `fetch_historical_drawdowns` (same file owns `HISTORICAL_WINDOWS` + the yfinance date-range fetch pattern). Mirrors the existing per-ticker fetch but **retains** each `Close` series instead of collapsing to a scalar; excludes any ticker with <5 valid closes in the window (same guard as the existing drawdown fetch); builds an aligned daily-return frame. Returns `None` on total failure (offline contract — never an empty DataFrame as a silent "nothing wrong" signal). Does NOT refactor `fetch_historical_drawdowns` to share the download — kept standalone so the already-shipped drawdown comparison can't regress.

**New pure fn** `stress_correlation_matrix(scenario_id, tickers) -> pd.DataFrame | None` — thin wrapper: fetch + `.corr().round(3)`; `None` propagates. Same file.

**New pure fn** `correlation_regime_delta(calm_corr, stress_corr, weights=None) -> dict | None` — `stock_analyzer/portfolio_intelligence.py`, next to `correlation_clusters` (reuses it, doesn't reimplement). Returns `None` if either input is `None`. Restricts BOTH matrices to the ticker intersection first (correctness-critical — every average and pair delta must be computed over the identical set, or a partial-coverage ticker could skew the headline). Computes `avg_calm`/`avg_stress` off-diagonal means; `newly_converged` = pairs where `calm < CORR_HIGH_PAIRS_THRESHOLD` AND `stress >= CORR_HIGH_PAIRS_THRESHOLD`, sorted by delta descending; calls the existing `correlation_clusters(stress_corr_intersected)` for the stress cluster list; carries `n_window_days` and a `coverage` dict (`included`/`excluded`/`total`).

**UI** — `app.py`, under the calm clusters block (~line 12854): guards on `_corr_df_cache is None` (offline branch, reusing the existing "revisit Home" message); a window selectbox (COVID 2020 default); a lazy **"Load stress correlations"** button (cloning the existing `_hist_stress_{id}` session-cache + spinner + Refresh pattern verbatim, so this doesn't fetch on every rerun); renders the headline delta, the newly-converged table, the stress cluster list, the coverage caption, and the mandatory framing caveat.

## Coordination note

This is the SAME dimension (correlation structure) viewed under a second regime, deliberately co-located with and contrasted against the calm view — not a silent second opinion (per `feedback_single_surface_priority`, which requires deduping by dimension). Nothing downstream consumes this output; it's terminal display, awareness-only. `correlation_clusters()` is confirmed pure (no session_state writes, no input mutation), so calling it a second time with the stress matrix cannot perturb the existing calm call site.

## Tests the build must include

- Offline sentinel: `fetch_stress_window_returns` returns `None` (not an empty DataFrame) when every download fails.
- Missing-ticker exclusion: a ticker with <5 valid closes is dropped and counted in `coverage.excluded`.
- **"Newly converged" boundary** (test the boundary, don't reason it safe — per the 2026-08-04 audit precedent): a pair at exactly `stress == CORR_HIGH_PAIRS_THRESHOLD` with `calm` just below IS flagged; the same pair at `stress == threshold − ε` is NOT.
- Intersection integrity: the headline and every pair delta are computed only over `calm ∩ stress` tickers.
- `None`-propagation: `correlation_regime_delta` returns `None` when either matrix is `None`; a well-formed empty payload (no raise) when the intersection has <2 tickers.
- Purity regression guard: running `correlation_clusters` on the stress matrix leaves the calm matrix object and its own cluster output unchanged.

## Routing

New decision-adjacent surface + touches `portfolio_intelligence.py` → **Opus `reviewer` REQUIRED** before commit (CLAUDE.md hard rule #4 + new-surface trigger).

## Addendum — custom date-range picker (2026-08-05, designed before mock approval)

**Trigger:** the user wants to test a real, personally-observed sector-specific selloff (e.g. a semiconductor/memory drawdown hitting MU/AMD/Intel-style names) that isn't one of the 3 fixed macro-crash presets. Rather than hardcode an unverified date range from memory (this app's own zero-hallucination doc-integrity rule), the design adds a **custom date-range picker alongside the 3 presets** — reusable for this event and any future one.

**Existing precedent found and mirrored:** the Research Track Record page already ships a near-identical "preset selectbox + Custom reveals a tuple date_input" picker (`_rh_custom_range`, `app.py` ~23771-23796) — but it silently falls back on invalid input, violating this app's own "never silently filter, always show a visible banner" rule. This addendum fixes that gap rather than repeating it: a disabled Load button + an explicit inline reason caption, not a silent fallback.

**Decisions LOCKED 2026-08-05 (with user):**
1. **Minimum stress-window length:** ✅ **10 trading days (~2 calendar weeks)**, measured on the aligned-return row count (not calendar days — a calendar range spanning holidays/sparse overlap could still be short on actual trading rows). All 3 presets comfortably exceed this (COVID ≈ 25 trading days) — **the floor only ever bites custom ranges; presets can never regress.**
2. **Earliest allowed custom start date:** ✅ **2007-01-01** — just before the oldest preset (GFC starts 2007-10-09), giving a little headroom while staying consistent with what the app already supports elsewhere.

**Finalized function signatures (date-based, one path serves both presets and custom — decoupled from scenario_id lookup):**
```python
fetch_stress_window_returns(tickers: list[str], start: str, end: str) -> pd.DataFrame | None
stress_correlation_matrix(tickers: list[str], start: str, end: str) -> pd.DataFrame | None
correlation_regime_delta(calm_corr, stress_corr, weights=None) -> dict | None
    # payload gains: n_window_days (aligned-return row count), too_short: bool
```
The UI resolves `HISTORICAL_WINDOWS[scenario_id]` to concrete ISO date strings BEFORE calling the fetch — presets and custom ranges hit the identical code path. This is strictly simpler than the originally-designed scenario_id-coupled signature (no code shipped yet, so no backward-compat cost to getting this right the first time).

**Validation guardrails (layered by cost — widget-level free checks first, pure-function boundary second):**
- `st.date_input`'s own `min_value=_EARLIEST_STRESS_START` / `max_value=today_et()` (from `stock_analyzer.market_time` — NOT naive `date.today()`) blocks future dates and pre-2007 starts for free, no branch code.
- A `_valid` check handles the transient 1-tuple state (user has only picked a start date so far — mirrors how the shipped `_rh_custom_range` already handles this) and `start < end` strictly.
- A cheap pre-fetch calendar-span check (`(end-start).days < ~14`) gives immediate "pick a longer range" feedback before the user clicks Load and waits on a fetch just to be told it's too short.
- The AUTHORITATIVE floor is `correlation_regime_delta`'s `too_short` flag (aligned trading-day count, post-fetch) — when true, the payload still returns cleanly (no raise), and the UI withholds the converged-pairs table with a banner instead of showing a statistically meaningless matrix. Matches "recommend nothing rather than wrongly."
- The existing per-ticker `<5 valid closes` exclusion (already designed for presets) applies unchanged to custom ranges — no new logic, coexists cleanly with the new whole-window floor (per-ticker guard drops individual names; whole-window floor decides if the aligned frame as a whole is long enough to correlate at all).
- Error UX: Load button `disabled=not _valid`, with an inline `st.caption`/`st.warning` naming the SPECIFIC reason (future date / too short / inverted range) — never a silent fallback.

**UI mechanics:** `"Custom range"` added as a final option in the existing window selectbox (COVID 2020 remains the default). Selecting it reveals a tuple `st.date_input` (mirroring the Research Track Record precedent) defaulting to a recent 30-day span.

**Cache-key scheme:** preset hits keep `_stress_corr_{scenario_id}` (unchanged); custom ranges get `_stress_corr_custom_{start_iso}_{end_iso}` — each distinct custom range memoizes independently within the session; switching ranges never clobbers a prior result; Refresh clears only the active key.

**Constants — confirmed no `constants.py` entry.** Both floors are measurement/display floors that only decide whether awareness-only info is shown vs. withheld — never a gate, never moves a recommendation — matching the established local-module-constant precedent (`account._ANNUALIZE_CAVEAT_MAX_DAYS`). Added to `stock_analyzer/stress_test.py`: `_MIN_STRESS_WINDOW_DAYS = 10`, `_EARLIEST_STRESS_START = date(2007, 1, 1)`.

**Additional tests (on top of the original 6):** date-based signature behavior-preservation for presets; the exact `too_short` boundary (10 vs. 9 trading days); custom cache-key uniqueness + no collision with preset keys; validation invariants (inverted range, future end date via the pure validator not just the widget); the `<5 closes` exclusion re-parametrized over a custom window.

## Addendum 2 — cross-reference to 🔥 Stress Testing (2026-08-05, small, mechanical)

The existing Stress Testing tab already answers "what's the P&L impact of this crash on my portfolio?" for the same 3 canned scenarios. This new section answers a different question ("what happens to my diversification during it?") using the same underlying window — two lenses on one scenario, previously unconnected. Add a one-line caption below the loaded results, ONLY when the active window is one of the 3 presets (not shown for a custom range, since Stress Testing has no P&L lookup for an arbitrary user-picked date range): *"For the P&L impact of this scenario, see 🔥 Stress Testing."* Pure discoverability text — no new computation, no coupling, no session_state read/write between the two tabs.

## SHIPPED 2026-08-05

Built, Opus-reviewed (SHIP, 0 blocking; 1 non-blocking test-coverage gap on a NaN correlation cell closed same-session before commit), full suite 3323 passed. See F-230 in `docs/requirements.md` and `docs/shipped-log.md`.
