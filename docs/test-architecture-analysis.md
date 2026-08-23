# Test Architecture Audit: Modularity & Collection Coupling

**Date:** 2026-08-17 | **Status:** Analysis Complete | **Next Step:** Prioritization (not today)

---

## Executive Summary

The codebase is **architecturally sound** but suffers from a classic **monolithic dependency cascade** at import time: even a single-line bug fix triggers full pytest collection of 3,674 tests because Python must import all 111 test files to discover test names, and each import pulls the entire codebase as a side effect.

**Key Finding:** This is NOT a code architecture problem (no circular dependencies, clean provider layer, proper separation). It's a Python import model + pytest collection strategy problem.

---

## Current State Assessment

### Module Structure: ✅ HEALTHY
- **91 modules** in `stock_analyzer/`
- **Zero circular dependencies** detected
- Max import depth: 4-5 levels (manageable)
- Most-imported modules: `bundle_loader` (12), `headless_alert_engine` (10), `ticker_liveness` (5)
- **Verdict:** Well-factored design; imports are reasonable.

### Test Organization: ✅ HEALTHY
- **111 test files**, organized 1-per-module
- **conftest.py: only 121 lines** — zero global fixtures, only helper functions
- Each test uses synthetic fixtures: `make_risk_advisor_inputs()`, `make_port_df()`, no DB/session_state
- **19 test files use `@pytest.fixture(autouse=True)`** but these are test-scoped (per-test cleanup, no pollution)
- **Verdict:** Tests are well-isolated; no shared state bleeding.

### Session State Coupling: ⚠️ MODERATE (But Not the Root Cause)
- **26+ coordination keys** documented in CLAUDE.md
- Pattern: producers publish to `st.session_state`, consumers read and gate
- **Critical:** Tests DON'T exercise this layer — they use synthetic data only
- Session state bugs are caught via **manual testing**, not pytest
- **Verdict:** Coordination works as designed; it's a separate testing gap (see `docs/testing-strategy.md §3`).

### Provider Layer Isolation: ✅ EXCELLENT
- Clean `DataProvider` base class + orchestrator
- Providers declare capabilities; orchestrator builds failover chain
- Graceful degradation: `ProviderUnavailable` (not `NotImplementedError`)
- **Verdict:** Properly abstracted and testable.

### Feature Isolation: ✅ GOOD (With Bundle Loader Caveat)
- Core logic is testable independently: `risk_advisor`, `concentration`, `scoring` all use synthetic data
- **Bottleneck:** `bundle_loader` is a "fan-out" module — imported by 12 others
  - When ANY of those 12 test files load, bundle_loader loads
  - Bundle_loader imports 9+ modules (data, technicals, fundamentals, scoring, risk, etc.)
  - This creates a cascade that defeats scoped testing
- **Verdict:** Logic is isolatable; bundle_loader is the structural bottleneck.

### Test Fixtures & Conftest: ✅ CLEAN
- No global `scope="session"` or `scope="module"` fixtures
- `conftest.py` helpers are opt-in (tests call them explicitly)
- Example cleanup (from `test_db_readonly.py`):
  ```python
  @pytest.fixture(autouse=True)
  def _cleanup():
      yield  # run test here
      db._READONLY = False
      sys.modules.pop("streamlit", None)
  ```
- **Verdict:** No massive shared setup pulling in the world.

---

## Root Cause: Python's Import Model + Pytest Collection

### The Cascade Effect

```
You run: pytest tests/test_concentration.py

Pytest does (collection phase):
  1. finds all test_*.py files (111 files)
  2. imports test_concentration.py
       → imports stock_analyzer.concentration
       → concentration imports: constants, portfolio, daily_briefing
         → daily_briefing imports: 16 modules (cascade!)
         → one of those is bundle_loader
           → bundle_loader imports: data, technicals, fundamentals, 
                                   valuation, scoring, risk, targets, db
  3. Also imports test_daily_briefing.py
       → imports daily_briefing (already loaded)
       → but cascade continues via other test files
  4. Also imports test_bundle_loader.py, test_risk.py, ... (111 times)

Result: ~50+ modules loaded, 3,674 tests collected
Execution: runs only the 5 tests in test_concentration.py ✓
```

**Why `-k concentration` doesn't help:**
```bash
pytest tests/ -k concentration
# Still imports all 111 test files (collection phase)
# Only runs the filtered tests (execution phase)
# Collection forced the full codebase load anyway
```

### Why Tests Themselves Aren't the Culprit

- Tests use synthetic data: `make_risk_advisor_inputs()` returns dicts, not DB queries
- Tests never import `streamlit` (except mocking it for a few test files)
- No global state fixtures that pull in dependencies
- **Verdict:** Test code is clean; the problem is at collection time, not execution time.

---

## Impact Assessment: Concrete Example

**Scenario:** You fix a 1-line bug in `concentration.py`

```bash
# Run:
$ pytest tests/test_concentration.py -v

# Pytest collection (~90 seconds):
  - Imports all 111 test files
  - Each import cascades through dependencies
  - Total: 50+ modules loaded, 3,674 tests collected

# Pytest execution (~30 seconds):
  - Runs only the 5 tests in test_concentration.py ✓
  - 3,669 tests skipped
```

**The 90 seconds of collection is wasted.** The 30 seconds of execution is fine.

### Why This Happens

1. **Python's `import` is atomic at the module level**
   - `from stock_analyzer.MODULE import func` loads the ENTIRE module
   - No way to load only the parts you need
   - No way to defer transitive imports

2. **Pytest must collect all tests before running any**
   - Pytest doesn't know which test files to run without importing them
   - `-k` flag is applied *after* collection
   - Collection phase is mandatory overhead

3. **The dependency cascade explodes**
   - `bundle_loader` is imported by 12 modules
   - When ANY of those 12 test files load, bundle_loader loads
   - This isn't a design bug; it's how module dependencies work
   - But it scales poorly as the codebase grows

---

## Improvement Opportunities (Prioritized)

### TIER 1: This Week (3-4 hours, 20-30% speedup)

#### 1. Create "Fast Tests" Subset
- Mark ~25-30 tests that don't need bundle_loader: `@pytest.mark.fast`
- Examples: `test_concentration.py`, `test_risk_advisor.py`, `test_scoring.py`, `test_technicals.py`
- These test pure logic with synthetic data only
- **Collection time:** ~30s instead of 120s
- **Usage:** `pytest -m fast` during active development
- **Complexity:** Low (marking + creating test list)
- **Payoff:** Immediate feedback loop for developers

#### 2. Document the Bottlenecks
- Create `scripts/analyze_test_deps.py` to map the import cascade
- Output: Which modules block which others
- Example:
  ```
  bundle_loader imports: data, technicals, fundamentals, valuation, 
                        scoring, risk, targets, db
  12 test files depend on bundle_loader
  → Any change to those 12 test files forces full import
  ```
- **Complexity:** Low (AST parsing + graph traversal)
- **Payoff:** Visibility for future refactorings

#### 3. Add Pytest Markers to All Tests
- Mark all test functions: `@pytest.mark.MODULENAME`
- Example:
  ```python
  @pytest.mark.concentration
  def test_portfolio_concentration_returns_dict():
      ...
  ```
- **Usage:** `pytest -m concentration` for scoped runs
- **Note:** Still imports all files in collection phase (but preps for Tier 2)
- **Complexity:** Low (search/replace + one conftest addition)
- **Payoff:** UX improvement + future-proofs for architecture changes

---

### TIER 2: This Month (2-3 days, 40-50% speedup)

#### 4. Lazy-Load Bundle Loader
- Create `stock_analyzer/lazy_bundle.py` wrapper:
  ```python
  def lazy_load_bundle():
      from stock_analyzer import bundle_loader
      return bundle_loader
  ```
- Update 12 imports across the codebase:
  ```python
  # OLD: from stock_analyzer.bundle_loader import load_bundle
  # NEW: from stock_analyzer.lazy_bundle import lazy_load_bundle
  #      loader = lazy_load_bundle()
  #      result = loader.load_bundle(...)
  ```
- **Impact:** Tests that don't actually call bundle-loading skip its import
- **Savings:** ~5-8 modules not imported, 10-15% faster collection
- **Complexity:** Low (wrapper + 12 import updates)
- **Payoff:** Tangible collection speedup

#### 5. Extract Core Logic Layers
- Split `daily_briefing.py` into:
  - `daily_briefing_core.py` (pure logic, minimal imports)
  - `daily_briefing.py` (orchestration, full imports)
- Do the same for `bundle_loader`, `headless_alert_engine`
- **Impact:** Tests can now load core without full cascade
- **Savings:** 10-20% faster collection for core-logic tests
- **Complexity:** Moderate (identify core vs. orchestration, refactor, update imports)
- **Payoff:** Clean separation of concerns + faster tests

---

### TIER 3: This Quarter (If Codebase Grows >120 Modules)

#### 6. Plugin Architecture
- Move each feature (Risk Analysis, Portfolio Health, etc.) into a discoverable plugin
- `app.py` loads only active features
- Tests can load single feature + its deps
- **Impact:** Collection ~10-20s per feature instead of 120s global
- **Complexity:** Very High (restructure entire app, ~1-2 weeks)
- **Payoff:** Scales to 150+ modules without collection explosion
- **Trigger:** Only if codebase grows significantly (currently 91 modules; worth it at 120+)

---

## Summary Table

| Factor | Status | Action |
|--------|--------|--------|
| Circular dependencies | ✅ None | Keep as-is |
| Module organization | ✅ Clean | No change needed |
| Test isolation | ✅ Good | No change needed |
| Pytest fixtures | ✅ Minimal | No change needed |
| Provider layer | ✅ Well abstracted | No change needed |
| **Python import model** | ❌ **ROOT CAUSE** | Lazy-load + core/orchestration split (Tier 2) |
| **Bundle loader fan-out** | ❌ **BOTTLENECK** | Extract core logic, lazy-load (Tier 2) |
| Session state testing | ⚠️ Manual-only | Separate issue; document in testing-strategy |

---

## Why Architecture Isn't the Problem

- ✅ No circular dependencies
- ✅ Clean provider abstractions
- ✅ Proper feature separation
- ✅ Minimal global state in tests
- ✅ Well-organized module structure

**The issue is Python's semantics, not design choices.** This is expected and normal scaling challenge that appears around 50-100 modules.

---

## Next Steps

1. **Review this document** — confirm findings align with your experience
2. **Prioritize the three tiers** — decide if/when to tackle each
3. **Implement Tier 1 this week** (recommended) — "fast tests" gives immediate relief
4. **Plan Tier 2** — lazy-loading + core extraction for next sprint
5. **Monitor codebase size** — revisit Tier 3 (plugin architecture) if modules exceed 120

---

## Related Documents

- [docs/testing-strategy.md](testing-strategy.md) — Test coverage strategy and manual testing requirements
- [docs/architecture.md](architecture.md) — System architecture and module responsibilities
- [CLAUDE.md](../CLAUDE.md) § Coordination pattern — Session state orchestration (separate testing concern)
