# RiskListwiseYoung C2 — wiring spec (lead, 2026-11-25)

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

---

# RiskListwiseYoung C3 — implementer part (lead, 2026-11-25)

Display + the two small follow-ups from the C2 review. The lead writes the policy docs (requirements, architecture, bug register, constants comment, User Guide wording is given verbatim below).

## A. `stock_analyzer/risk_window.py` — one pure decision for the display (gate file)

- `display_note(coverage) -> str | None`: the note a screen shows. Returns `coverage_note(coverage)` when `coverage` is a dict whose `state` is `"partial"` or `"unmeasured"`; None for `"full"`, a non-dict, or a dict with no `state` (memoised pre-C2 bundles). Tested at each state.
- No other change.

## B. `app.py` — 🔗 Risk Analysis "Portfolio Risk Dashboard"

1. Under the existing caption "All metrics derived from 6-month weighted daily portfolio returns. …" (~18547): `_ra_cov_note = risk_window.display_note(_pr.get("coverage"))`; when not None, `st.caption("ℹ️ " + _ra_cov_note)`. `coverage_note` text has no `$` and no `**` (plain text), so `st.caption` is safe.
2. In the `else:` (unavailable) branch (~18907, after the existing `st.warning`): call `_ra_live_series, _ra_live_cov = risk_window.book_returns(port_df, held_data)` inside try/except (any exception → skip), and when `_ra_live_series is None` and `risk_window.display_note(_ra_live_cov)` is not None, `st.caption("Checked now on this page (not 🏠 Home's risk build): " + note)` (label as built after the C3 review). Only on the None path (labelled because its freshness differs from Home's build).

## C. `app.py` — 🏠 Home fragility chip (~10073-10180)

1. `_frag_ok` path, inside `with _fg_right:` AFTER the existing `st.markdown(...)`: `_fg_cov_note = risk_window.display_note((st.session_state.get("_port_risk_cache") or {}).get("coverage"))` — careful: use `util.get_or_offline` if the antipattern gate flags `or {}`; otherwise `_pr_c = st.session_state.get("_port_risk_cache")` then `_pr_c.get("coverage") if isinstance(_pr_c, dict) else None`. When not None: `st.caption("ℹ️ " + _fg_cov_note)`. (Not inside the raw-HTML block: no new dynamic `unsafe_allow_html`.)
2. `else` path (beta couldn't be computed): keep the existing static line; after it, compute `risk_window.book_returns(port_df, held_data)` (try/except; Home's `held_data` / `port_df` are in scope — verify the names at that point) and if the series is None and `display_note(cov)` is not None, `st.caption(note)`. This replaces nothing; it says WHY when the reason is a young / unpriced holding.
- Import: `from stock_analyzer import risk_window` near the other `from stock_analyzer import …` lines (~82-94).

## D. `stock_analyzer/headless_alert_engine.py` (gate file) — reviewer note 4

In `_build_context`'s `except Exception:` around `compute_portfolio_risk_metrics`, also reset `_risk_cov = {}` so `risk_coverage` becomes None ("not built") and never says full / partial beside a None `port_risk`. Add a test (monkeypatch `compute_portfolio_risk_metrics` to fill `coverage_out` then raise → ctx `risk_coverage` is None, `port_risk` None).

## E. Tests

- `tests/test_risk.py::test_compute_portfolio_risk_metrics_no_matching_weights_returns_none` (~693): lengthen its frames from 15 to 25 closes so AAPL is countable and the test proves the None comes from the missing weight / prices, not from short history; keep its assertion.
- `display_note` unit tests (full → None, partial → text, unmeasured → text, None / {} / {"beta": 1} → None).
- AST/text pins: if any test pins the Risk Analysis caption or the Home chip text, update honestly. Grep tests/ for "6-month weighted daily portfolio returns" and "Pullback-exposure read unavailable".

## F. User Guide (app.py, `elif page == "📖 User Guide":`), verbatim

In the 🔗 Risk Analysis bullet (~47158), after the first sentence ending "…naming your top beta contributors on a breach.", insert:

" A holding with fewer than 20 trading days since its first close (a recent IPO, say) is left out of the portfolio risk figures and its weight is spread over your other holdings; a note under the dashboard names it. If such holdings add up to more than 10% of your book, the figures read unavailable instead of being computed on too little of it."

Write 20 and 10 from `BETA_MIN_RETURNS` / `PORTFOLIO_RISK_UNMEASURED_MAX_PCT` via f-string if the User Guide block is an f-string at that point; if it is a plain string, keep the literals and add a test in the new test file that asserts the guide text contains `str(BETA_MIN_RETURNS)` and `f"{PORTFOLIO_RISK_UNMEASURED_MAX_PCT:g}%"` (so a constant change breaks the test, not the doc silently).

## Checks

py_compile app.py; the touched tests + tests/test_risk*.py + headless tests (`-n 4` max); `python scripts/check_antipatterns.py` (no new instances; `POLICY_DECISION_IN_RENDER` must stay clean: no comparison against a constants value in app.py); no full suite; no commit.
