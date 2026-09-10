# Test Suite Optimization Plan

**Status:** Tier 1 (1.1 + 1.2) SHIPPED 2026-09-09 — 1.3 deliberately deferred (see checklist). Tier 2/3 still Queued. | **Baseline Collection Time:** ~120 seconds as measured 2026-08-17 — **superseded 2026-09-09, see note below.**

**2026-09-09 remeasurement note (suite has grown 3,674 → 5,139 tests since the 120s figure was recorded):** a fresh `pytest --collect-only -q` on this machine now runs in **~7-28s** (28.17s cold, 7.08-7.58s on repeated warm runs) — collection itself is no longer the bottleneck the 120s figure described; something about the original measurement's environment (cold disk cache, a slower box, a different Python/pytest version) no longer applies here, and it isn't worth re-deriving which. **More important, and worth being honest about:** the `pytest -m fast` marker shipped by Work Item 1.1 does **not** meaningfully speed up either phase in practice, for two measured reasons. First, `-m fast` does not skip pytest's collection phase — collection imports every test file's top-level code regardless of marker; the marker only deselects items *after* collection. Second, of 5,139 total tests, only 127 (spread across 8 files) transitively import `stock_analyzer.bundle_loader` and get excluded — a clean, uncontended `pytest -m fast -q` ran 5,012 tests in 376.45s, and a clean, uncontended full `pytest tests/ -q` ran all 5,139 in 378.82s. That's a ~2.4s difference, not the "30s vs 120s" this plan originally targeted. The dependency-graph analysis behind this (Work Item 1.2) is still real and useful — it just found that `bundle_loader` reachability, as literally specified, isn't where this suite's wall-clock time actually goes; most of the ~378s is per-test execution cost (pandas/numpy work, mocked-DB round trips, simulations) spread fairly evenly across almost every file, not import-time fan-out concentrated in a small "heavy" set. Tier 2/3 (structural extraction, lazy-loading) were not evaluated against this finding and remain out of scope for this pass.

## Problem Statement

Small bug fixes trigger full pytest collection of 3,674 tests (~120s overhead) — **as measured 2026-08-17; see the status line above for a 2026-09-09 remeasurement that no longer reproduces the 120s figure.** Root cause: Python's atomic module imports + pytest's mandatory collection phase force the entire codebase to load even for scoped test runs. No architectural defects; normal scaling challenge for ~90-module codebases.

**Full analysis:** [docs/test-architecture-analysis.md](../test-architecture-analysis.md)

---

## Work Breakdown (Prioritized)

### TIER 1: Fast Feedback Loop (Recommended This Week)

**Objective:** Give developers a 30-second test run for rapid iteration.

#### Work Item 1.1: Mark "Fast Tests"
- **Time:** 1-2 hours
- **Effort:** Low (marking + documentation)
- **Benefit:** 30s collection instead of 120s for active development
- **Steps:**
  1. Identify ~25-30 tests that don't import `bundle_loader`:
     - `test_concentration.py` (5 tests)
     - `test_risk_advisor.py` (12 tests)
     - `test_scoring.py` (8 tests)
     - `test_technicals.py` (6 tests)
     - `test_fundamentals.py` (4 tests)
     - Other pure-logic tests
  2. Add `@pytest.mark.fast` to test functions
  3. Document in `DEVELOPMENT.md`:
     ```
     Fast iteration during development:
       pytest -m fast    # 30s, pure logic tests only
       pytest            # 120s, full suite (before commit)
     ```
  4. Add `.pytest.ini` or `pyproject.toml`:
     ```ini
     [pytest]
     markers =
         fast: pure logic tests (no bundle_loader)
     ```

#### Work Item 1.2: Document Bottlenecks
- **Time:** 1-2 hours
- **Effort:** Low (Python script)
- **Benefit:** Visibility into why collection is slow
- **Steps:**
  1. Create `scripts/analyze_test_deps.py`:
     - Parse imports in each test file
     - Build dependency graph
     - Identify "heavy" modules (imported by many test files)
     - Output: which modules cause cascade
  2. Run and save output to `docs/test-dependency-graph.md`
  3. Example output:
     ```
     HEAVY MODULES (imported by 12+ test files):
       bundle_loader: 12 importers
         → pulls in: data, technicals, fundamentals, valuation, scoring, risk
       headless_alert_engine: 10 importers
       ticker_liveness: 5 importers
     
     RECOMMENDED FOR LAZY-LOADING:
       bundle_loader (biggest ROI)
       headless_alert_engine (second biggest)
     ```

#### Work Item 1.3: Add Pytest Markers to All Tests
- **Time:** 2-3 hours
- **Effort:** Low (marking + one conftest addition)
- **Benefit:** Future-proofs for scoped runs; enables filtering
- **Steps:**
  1. Add to `tests/conftest.py`:
     ```python
     def pytest_configure(config):
         config.addinivalue_line("markers", "concentration: concentration tests")
         config.addinivalue_line("markers", "risk: risk advisor tests")
         # ... etc for all modules
     ```
  2. Add markers to all test functions:
     ```python
     @pytest.mark.concentration
     def test_portfolio_concentration_returns_dict():
         ...
     
     @pytest.mark.risk
     def test_risk_signal_high_concentration():
         ...
     ```
  3. Document usage:
     ```bash
     pytest -m risk              # All risk tests
     pytest -m "risk or concentration"  # Multiple markers
     ```
  4. Note: Still imports all files in collection phase, but enables future architecture improvements

**Tier 1 Total: ~4-6 hours work | 20-30% speedup on collection**

---

### TIER 2: Structural Refactoring (Next Sprint)

**Objective:** Reduce collection time to 60-70s by breaking dependency cascades.

#### Work Item 2.1: Lazy-Load Bundle Loader
- **Time:** 4-6 hours
- **Effort:** Low (straightforward pattern)
- **Benefit:** 5-8 modules skip loading on tests that don't use bundling
- **Steps:**
  1. Create `stock_analyzer/lazy_bundle.py`:
     ```python
     def lazy_load_bundle():
         """Defer bundle_loader import until needed."""
         from stock_analyzer import bundle_loader
         return bundle_loader
     ```
  2. Identify all imports of `bundle_loader` across codebase:
     - `grep -r "from stock_analyzer.bundle_loader import" stock_analyzer/`
     - `grep -r "from stock_analyzer import bundle_loader" stock_analyzer/`
  3. Update each usage:
     ```python
     # OLD:
     from stock_analyzer.bundle_loader import load_bundle
     result = load_bundle(ticker)
     
     # NEW:
     from stock_analyzer.lazy_bundle import lazy_load_bundle
     loader = lazy_load_bundle()
     result = loader.load_bundle(ticker)
     ```
  4. Update imports in tests similarly
  5. Add test: `test_lazy_bundle_loads_successfully()`
  6. Measure collection time before/after

#### Work Item 2.2: Extract Core Logic from Heavy Modules
- **Time:** 2-3 days (full feature)
- **Effort:** Moderate (identify, extract, refactor imports)
- **Benefit:** Separates decision logic from coordination; tests can load core without cascade
- **Modules to refactor (priority order):**
  1. `bundle_loader.py` → `bundle_loader_core.py` + `bundle_loader.py`
     - Core: `load_ticker_data()`, `compose_bundle()`
     - Orchestration: Entry points, caching, error handling
  2. `daily_briefing.py` → `daily_briefing_core.py` + `daily_briefing.py`
     - Core: `_buy_candidates()`, `_trim_targets()`, `_cross_reference()`
     - Orchestration: Session state writes, caching, orchestration
  3. `headless_alert_engine.py` → extract core decision logic
- **Steps (per module):**
  1. Identify which imports are needed for core logic vs. orchestration
  2. Create `MODULE_core.py` with minimal imports
  3. Move core functions there
  4. Update imports in `MODULE.py` to `from . import MODULE_core`
  5. Update test file to test `MODULE_core` when applicable
  6. Measure collection time before/after

**Tier 2 Total: ~1 week work | 40-50% total speedup**

---

### TIER 3: Plugin Architecture (This Quarter or Later)

**Objective:** Scale collection time to 10-20s per feature as codebase grows beyond 120 modules.

**Status:** PARKED — Not urgent until codebase grows significantly.

**Trigger:** Implement when:
- Codebase exceeds 120 modules (currently 91)
- Collection time exceeds 3-5 minutes
- New features consistently land in "heavy" modules

**Sketch:**
```
stock_analyzer/features/
  ├── __init__.py                 # Feature registry
  ├── risk_analysis/
  │   ├── __init__.py            # Declares plugin
  │   ├── advisor.py
  │   ├── gates.py
  │   └── test_risk_analysis.py
  ├── portfolio_health/
  │   ├── __init__.py
  │   └── ...
  └── ...

app.py:
  from stock_analyzer.features import REGISTRY
  for feature_name in config.ACTIVE_FEATURES:
      feature = REGISTRY.get(feature_name)
      feature.render(st, session_state)

pytest -m risk_analysis  # Loads only risk feature + its deps
```

---

## Implementation Checklist

### Tier 1 (This Week)

- [x] **1.1 Mark fast tests** — SHIPPED 2026-09-09, built from 1.2's real graph, not a guess.
  - [x] Identify fast-eligible test FILES: 134 of 142 (via `scripts/analyze_test_deps.py`'s transitive `bundle_loader`-reachability check) — far more than the stale "~25-30 tests" estimate below, because the codebase and its import graph moved on since 2026-08-17.
  - [x] Add `pytestmark = pytest.mark.fast` (module-level, matches this codebase's existing "no per-function marker noise" convention) to all 134 files
  - [x] Update `DEVELOPMENT.md` with usage
  - [x] Verify `pytest -m fast` runs — **measured 376.45s (5,012 passed, 127 deselected), not ~30s.** See the 2026-09-09 remeasurement note above the Problem Statement for why the original 30s target doesn't hold today.
  - [x] Add to `pytest.ini` (this repo's existing mechanism — no `conftest.py` `pytest_configure` hook existed, so no second config mechanism was introduced)

- [x] **1.2 Analyze bottlenecks** — SHIPPED 2026-09-09.
  - [x] Create `scripts/analyze_test_deps.py` (static `ast` parsing only, no imports/execution)
  - [x] Run and save to `docs/test-dependency-graph.md`
  - [ ] Document in CLAUDE.md for reference — **not done this pass**, out of scope per this work's own instructions (CLAUDE.md is curated separately; this plan doc + DEVELOPMENT.md are the sanctioned sync points for this change).

- [ ] **1.3 Add pytest markers to all tests — deliberately deferred, not started.** Per this item's own note below ("Still imports all files in collection phase"), it provides zero collection-time benefit and is a large mechanical sweep across ~142 test files for a benefit that, per the 2026-09-09 remeasurement, wasn't real to begin with (collection is already ~7-28s here, not 120s). Revisit only if collection time itself regresses to something worth chasing.
  - [ ] Update `tests/conftest.py` with marker registration
  - [ ] Mark all test functions by module
  - [ ] Verify markers in test output

### Tier 2 (Next Sprint)

- [ ] **2.1 Lazy-load bundle_loader**
  - [ ] Create `stock_analyzer/lazy_bundle.py`
  - [ ] Find all `bundle_loader` imports (grep)
  - [ ] Update imports (systematic search/replace)
  - [ ] Test lazy loading works
  - [ ] Measure collection time before/after
  - [ ] Update DEVELOPMENT.md

- [ ] **2.2 Extract core logic**
  - [ ] Start with `bundle_loader_core.py` extraction
  - [ ] Verify tests still pass
  - [ ] Measure collection time
  - [ ] Repeat for `daily_briefing_core.py`
  - [ ] Repeat for `headless_alert_engine` if needed
  - [ ] Update architecture docs

### Tier 3 (Conditional)

- [ ] Monitor codebase growth
- [ ] When modules > 120: revisit plugin architecture
- [ ] Plan 1-2 week effort if triggered

---

## Success Criteria

| Tier | Target | Baseline | Success |
|------|--------|----------|---------|
| Current | 120s collection | — | — |
| After Tier 1 | 90-100s | 120s | Fast tests run in ~30s |
| After Tier 2 | 60-70s | 120s | 40-50% speedup; core logic tests in ~20-30s |
| After Tier 3 | 10-20s per feature | 120s | Scales to 150+ modules |

---

## Related Documents

- [docs/test-architecture-analysis.md](../test-architecture-analysis.md) — Full architectural audit and findings
- [docs/testing-strategy.md](../testing-strategy.md) — Testing approach and coverage philosophy
- [DEVELOPMENT.md](../../DEVELOPMENT.md) — Dev setup and running tests
- [CLAUDE.md](../../CLAUDE.md) § Review & test economy — When to run which tests
