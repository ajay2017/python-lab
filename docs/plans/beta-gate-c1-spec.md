# Beta gate C1 build spec: the shared G-06 gate, Watchlist only (no behaviour change)

**Status:** SPEC 2026-11-26 (lead, from `docs/plans/beta-gate-scan-age-intraday.md` §2a / §3 C1). Owner took the plan's defaults 2026-11-26, except U9 still open (only C4 needs it). Build = implementer; Opus review; the commit is `refactor(` (no behaviour change on any surface).

## Goal
Move the G-06 comparison into a new pure gate module so that C2 can apply the SAME predicate on 🏠 Grow Today and both cron emails. In C1 only the 📋 Watchlist calls it, and its output must be byte-identical for every readable input.

## New module `stock_analyzer/beta_fit_gate.py`
Pure: no clock, no I/O, never raises. Add it to `_GATE_FILES` (`.claude/hooks/pre_tool_checks.py`) and add one reason line to `docs/review-policy.md` (suggested: "the one G-06 portfolio-beta predicate (portfolio beta over `PORTFOLIO_BETA_CEILING` and ticker beta over `TICKER_BETA_CRITICAL`) and its couldn't-check decision, shared by the 📋 Watchlist and (from C2) 🏠 Grow Today and the cron emails; a wrong call lets a high-beta buy onto a high-beta book, or holds one that fits"). The module docstring states THE SHAPE and THE FAIL DIRECTIONS.

```python
from stock_analyzer.buy_check_state import beta_or_none, beta_leg_could_bind, FIT_READY, FIT_FLAT
from stock_analyzer.constants import PORTFOLIO_BETA_CEILING, TICKER_BETA_CRITICAL

STATE_PASS, STATE_BLOCKED, STATE_UNKNOWN = "pass", "blocked", "unknown"

def breached(port_beta, ticker_beta) -> bool:
    """Both legs readable (beta_or_none) AND port_beta > PORTFOLIO_BETA_CEILING AND
    ticker_beta > TICKER_BETA_CRITICAL (both strict). An unread leg is never a breach."""

def decide(*, port_beta, ticker_beta, fit_state) -> dict:
    """{"state": pass|blocked|unknown, "kind": None|"beta"|"beta_unknown"|"fit_unknown",
        "port_beta": float|None, "ticker_beta": float|None,
        "gate_value": ticker_beta if blocked else None,
        "gate_threshold": TICKER_BETA_CRITICAL if blocked else None}"""
```

`decide` order, mirroring the Watchlist's existing branches (corrected 2026-11-26 after the implementer's STOP: the Watchlist checks a readable breach FIRST, before the C2a fit branches, so step 3 below runs first):
1. `breached(...)` → blocked / `beta` (a readable breach binds first, as on the Watchlist).
2. `fit_state` not a dict, or its `state` not in (FIT_READY, FIT_FLAT) → unknown / `fit_unknown`.
3. FIT_FLAT → pass (an empty book has no portfolio beta to breach).
4. Both legs readable → pass.
5. `beta_leg_could_bind(port_beta, ticker_beta)` → unknown / `beta_unknown`; else pass.

`port_beta` / `ticker_beta` in the result are the `beta_or_none` values. Wrap everything in try/except, and on any exception return unknown / `fit_unknown` (fail closed, never raise).

`decide` is NOT called by the Watchlist in C1. It exists for C2 and is pinned by the parity test below. Do not add `hold_text` (that's C2).

## `stock_analyzer/watchlist_advisor.py` `_portfolio_risk_gate` (~204-221)
- Replace the inline condition `port_beta is not None and ticker_beta is not None and port_beta > PORTFOLIO_BETA_CEILING and ticker_beta > TICKER_BETA_CRITICAL` with `beta_fit_gate.breached(port_beta, ticker_beta)`. The f-string `reason` still formats `port_beta` / `ticker_beta`: inside the branch they are known readable, but format the `beta_or_none` floats so a numeric string formats with `:.2f`.
- Everything else (sector branches before, the C2a fit_unknown / beta_unknown branches after, soft concerns) is unchanged.
- **Known, accepted difference:** a non-numeric string beta used to raise `TypeError` at the `>`; it now reads as unread (`beta_or_none`) and falls to the C2a branch. Say so in a code comment and in the report. NaN / None / bool behave exactly as before. +inf does NOT: it used to hard-block (`inf > CEIL` is True) and now reads as unread, so with a read book it gets the C2a `beta_unknown` hold when the other leg could tip the gate (Opus C1 review: never a loosening on a production path, since both callers pass `fit_state`).

## Tests (new `tests/test_beta_fit_gate.py`; update `tests/test_buy_check_state.py`)
1. `breached` boundaries, all read from constants (CEIL = `PORTFOLIO_BETA_CEILING`, CRIT = `TICKER_BETA_CRITICAL`):
   - (CEIL, CRIT+1) → False; (CEIL+0.01, CRIT+0.01) → True; (CEIL+0.1, CRIT) → False;
   - any unread leg (None, NaN, inf, True / False, "n/a", object()) → False.
2. `decide` matrix:
   - **pass:** flat at any β; (CEIL, None); (None, CRIT); both read and not breached.
   - **blocked:** a breach, with gate_value / threshold.
   - **unknown (beta_unknown):** (None, CRIT+0.01), (CEIL+0.01, None), (None, None).
   - **unknown (fit_unknown):** fit_state None / {} / {"state": "unknown"} / "garbage".
   - The `port_beta` / `ticker_beta` fields are the cleaned floats.
   - It never raises (monkeypatch `beta_or_none` to raise → unknown / fit_unknown).
3. **Parity:** for a grid of `port_beta` × `ticker_beta` (values around both lines plus None / NaN) × fit_state (ready / flat / unknown), the kind `_portfolio_risk_gate` returns for the beta / fit branches must agree with `decide`:
   - `beta` ⇔ blocked;
   - `beta_unknown` ⇔ unknown / beta_unknown;
   - `fit_unknown` ⇔ unknown / fit_unknown;
   - no beta / fit kind ⇔ pass.
   
   Build `portfolio_ctx` so the sector branches don't fire (read `_portfolio_risk_gate`'s signature and its existing tests for the minimal ctx). Include the `"fit_state"` key and also the no-key case: with no `fit_state` key the Watchlist has no C2a branch, so only `beta` ⇔ blocked is compared there.
4. `tests/test_buy_check_state.py::test_beta_mirrors_the_advisor_comparison_exactly` (~304-311) pins the old inline string. Rewrite it to pin that `_portfolio_risk_gate` calls `beta_fit_gate.breached`, and that `breached` uses strict `>` on both constants (inspect source). Grep `tests/` for any other test pinning the old inline condition.
5. `stock_analyzer/beta_fit_gate.py` is in `_GATE_FILES`: assert on `_GATE_FILES` only, never read docs/review-policy.md in a test.

## Run
- Targeted only: `tests/test_beta_fit_gate.py tests/test_buy_check_state.py tests/test_watchlist_advisor.py` (and any test file that imports `_portfolio_risk_gate`; grep), plus `tests/test_pre_tool_checks.py`, with `-q -p no:cacheprovider`. Actually run them and report the real counts. NOT the full suite.
- Also run `python scripts/check_antipatterns.py`, `python scripts/check_constants_documented.py` and `python -m py_compile` on the touched modules.
- `git add` the two new files (gate files must be tracked). Don't commit.
- Report: files changed, test counts, and any spec-vs-code mismatch. STOP and report if the Watchlist output would change for a readable input.
