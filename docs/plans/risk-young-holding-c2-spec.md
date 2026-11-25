# RiskListwiseYoung C2 — wiring spec (lead, 2026-11-26)

Parent plan: `docs/plans/risk-young-holding.md` §8 item 2, §9, §10. C1 (`stock_analyzer/risk_window.py`, `22365ca0`) is the decision; C2 makes both book-return builders use it. No app.py display (that is C3) except the memo schema bump. Push before the Wed 12-02 close.

## 1. `stock_analyzer/risk_window.py` (gate file)

- Add one public helper `session_closes(frame) -> pd.Series | None` = `_closes_by_session({"df": frame})` (one row per session date, sorted, NaN closes dropped, duplicate date keeps the last). Docstring: for a benchmark frame (SPY) that must join the book series on the same session-date index. No other change to the module's decision.

## 2. `stock_analyzer/risk.py` (gate file)

- `compute_portfolio_risk_metrics(port_df, held_data, spy_df=None, risk_free_annual=0.045, *, coverage_out: dict | None = None)`:
  - Replace the whole series build (the `series` dict, `prices ... dropna()`, `< 10` check, `daily_returns`, the weights dict / renormalisation, the `port_returns` loop) with `port_returns, coverage = risk_window.book_returns(port_df, held_data)`.
  - If `coverage_out` is a dict: `coverage_out.clear(); coverage_out.update(coverage)` BEFORE any return (so it is filled on the None path too).
  - `port_returns is None` → return `None` (the offline contract; never `{}` and never a dict of Nones).
  - Everything after (Sharpe, Sortino, vol, VaR, CVaR, drawdown, cum_ret) is unchanged.
  - Beta: SPY returns from `risk_window.session_closes(spy_df)` then `.pct_change().dropna()` (reviewer NB from C1: SPY's index must go through the same session-date normalisation as the book series, else an intraday-stamped or tz-shifted SPY bar never joins). Keep the existing guards (`spy_df` not None / not empty / has `Close`; `session_closes` None → beta None). Replace `len(combined) >= 20` with `>= BETA_MIN_RETURNS`.
  - Return dict gains one key `"coverage": coverage` (additive).
  - Docstring: say the series comes from `risk_window.book_returns` (young holdings left out and disclosed in `coverage`).
- Value-neutral class fix: the bare `20` in `beta_vs_market` (`len(combined) < 20`) and `pearson_corr_vs_benchmark` (`len(combined) < 20`) → `BETA_MIN_RETURNS`; import it from constants.

## 3. `stock_analyzer/portfolio.py` (gate file)

- `portfolio_return_series(port_df, held_data)`: body becomes `series, _cov = risk_window.book_returns(port_df, held_data); return series` (keep the early `port_df is None or empty or not held_data → None`). Update the docstring: same builder as `compute_portfolio_risk_metrics`, so the book-beta window and `beta_repair.aligned_beta`'s window are one series by construction. Import `risk_window` lazily inside the function if a top-level import would create a cycle (check: `risk_window` imports only pandas / constants, so top-level should be fine).
- `correlation_to_portfolio(..., min_overlap: int = 20)` → default `BETA_MIN_RETURNS` (value-neutral).

## 4. `stock_analyzer/beta_repair.py`

- `aligned_beta(..., min_obs: int = 20)` → default `BETA_MIN_RETURNS` (value-neutral). Fix its docstring sentence that says portfolio beta "regresses a listwise intersection across every holding's history, so one short/degraded holding can truncate the window for the WHOLE book" → now the book series comes from `risk_window.book_returns` (young holdings left out, not truncating); `on_index` is still that series' index. Is beta_repair.py a `_GATE_FILES` member? Check; either way the reviewer sees it.

## 5. `stock_analyzer/headless_alert_engine.py` (gate file)

- In `_build_context`: `_risk_cov: dict = {}`; call `compute_portfolio_risk_metrics(port_df, held_data, spy_6mo, rfr, coverage_out=_risk_cov)`; on exception keep `port_risk = None` and leave `_risk_cov` as whatever it holds (possibly `{}`). Add ctx key `"risk_coverage": _risk_cov or None` (None = couldn't build coverage at all) with a short comment (additive; the young-holding coverage of the book risk series; `None` = not built).
- `compute_protective_alerts` return dict and the `compute_eod` payload (~line 1888, beside `"port_risk"`) each carry `"risk_coverage": ctx.get("risk_coverage")`. Check every return path of `compute_protective_alerts` that has an ok ctx.
- C2a `beta_unknown` (watchlist_advisor) needs NO change in this chunk (plan says "may name the share": skip, keep wording stable).

## 6. `cron_runner.py`

- Premarket lane (`_run_premarket`, after the existing `_footer_lines` build): `_cov = payload.get("risk_coverage")`; if it is a dict: log one line `portfolio risk coverage: state=<state> left_out=<TICKER:reason:n_returns:weight_pct, ...> unmeasured_pct=<x> window=<window_returns>`; and if `_cov.get("state") == "unmeasured"`, append `risk_window.coverage_note(_cov)` (when not None) to `_footer_lines`. If `_cov` is None, log `portfolio risk coverage: not built`.
- EOD lane: the same log line next to the `portfolio_risk_snapshot` write (from `payload.get("risk_coverage")`). No footer (EOD sends no protective email). No change to what is written to `portfolio_risk_snapshots` (owner Q4: a partial-coverage beta is written as is).
- Use a small helper (in `risk_window`, pure: `coverage_log_text(coverage) -> str`) so both lanes print the same text and it is unit-tested; cron_runner only calls it. Never compare the state against a constant in cron_runner (POLICY_DECISION_IN_RENDER); `== "unmeasured"` on a state string is fine.

## 7. `app.py`

- Bump `_SYNTH_SCHEMA_VER` 26 → 27, prepend to its comment: `bumped: port_risk carries "coverage" and the book risk series leaves young holdings out (RiskListwiseYoung C2); was 26: ...`. Nothing else in app.py.

## 8. Tests (see plan §10; all must pass under the full suite)

- `tests/test_risk.py` ~673-736: the old fixtures with 10–15 closes now return None (fewer than `BETA_MIN_RETURNS` returns per holding). Where a test means "computes", lengthen its frames to ≥ 21 closes (≥ 20 returns) and keep its assertions; where it means "too short → None", keep it and assert `coverage_out["state"] == "unmeasured"`. Don't weaken assertions.
- New (in `tests/test_risk_window.py` or a new `tests/test_risk_young_holding_wiring.py`):
  1. No-op regression: a 125-close 3-holding book with no young name → `compute_portfolio_risk_metrics` gives exactly the values the OLD listwise code gives (inline a copy of the old builder in the test as the oracle; `assert_series_equal` on `cum_returns` up to the index normalisation, equal beta / vol / Sharpe / VaR / drawdown), and `coverage["state"] == "full"`.
  2. ANT-like: same book + a 12-close holding at 3.6% → metrics not None, beta not None, window length the same as the no-young book, coverage partial listing the young name `short_history`; at 15% → None and `coverage_out["state"] == "unmeasured"`.
  3. 19 returns → left out; 20 → counted; and the first counted day equals the first day `risk.beta_vs_market` is non-None for that holding vs a full SPY frame.
  4. SPY intraday / tz-aware stamps: SPY index tz-aware America/New_York with a partial last bar stamped 10:15 → beta still computed on the same number of days as with a midnight-stamped SPY.
  5. SPY missing → beta None, other metrics and coverage present.
  6. `portfolio.portfolio_return_series` and `compute_portfolio_risk_metrics(...)["cum_returns"]` share one index (and the return series equals what `risk_window.book_returns` returns).
  7. Headless `_build_context` (monkeypatched loaders, follow the existing fixtures in `tests/test_watchlist_entries_headless.py` / the headless tests): ANT-like 12-close bundle at 3.6% → `port_risk["beta"]` not None, fragility computed, `ctx["risk_coverage"]["state"] == "partial"`; through `compute_watchlist_entries`, `beta_read` True and no `beta_unknown`; at 15% → port_risk None, `risk_coverage["state"] == "unmeasured"`, a β 1.9 name held as `beta_unknown`.
  8. `coverage_log_text` for full / partial / unmeasured / None-ish input; premarket footer gets the note only on "unmeasured" (unit-test the helper; a cron_runner test only if an existing premarket test harness makes it cheap).
  9. Value-neutral: `beta_vs_market` / `pearson_corr_vs_benchmark` return None at 19 overlapping returns and a value at 20 (pins the constant).
- Clock-free: nothing in the touched pure code reads `now_et` / `today_et`.

## 9. Not in C2

app.py captions (Risk Analysis, Home fragility chip), User Guide, requirements / architecture / bug-register / coordination-caches docs, the two plan dated lines: all C3 (lead). Siblings (RiskOffUnchecked, risk_budget, Analysis beta envelope, CorrYoung): C4.
