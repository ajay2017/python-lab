# Entry fail-closed C2b: the 📋 Watchlist builds its own fit frame (lead spec, 2026-11-26)

**Status:** SPEC, for the implementer. Parent: `docs/plans/entry-fail-closed.md` §2 "Watchlist self-build (U3)", §6 item 4, owner U1 / U3 / Q3 / Q7 (2026-11-25). C2a (`d705c95e`) built `buy_check_state.fit_state` / `beta_leg_could_bind` / `beta_or_none` and the `fit_unknown` / `beta_unknown` abstentions in `watchlist_advisor._portfolio_risk_gate`, active only when the portfolio ctx carries a `fit_state` key. The cron passes it; the app's Watchlist (app.py ~30670-30890) does not, so today it still fails OPEN (sector weight 0.0 with a "this one fails OPEN" banner when Home's frame is missing; beta None passes with a banner). C2b makes the app match the cron.

**Goal.** On the 📋 Watchlist, the sector-concentration (G-05) and portfolio-beta (G-06) checks always run against a holdings frame built from the current holdings, or the name is HELD (U1) with one page banner. No constant changes; no ledger change (abstentions write no rows, D3).

## 1. New gate module `stock_analyzer/watchlist_fit_source.py` (pure; add to `_GATE_FILES` with a reason comment + one docs/review-policy.md line)

No streamlit, no db, no clock (callers pass times).

- `SOURCE_HOME = "home"`, `SOURCE_SELF = "self_build"`, `SOURCE_FLAT = "flat"`.
- `decide_source(*, holdings_empty: bool | None, home_frame_state: str, home_frame_empty: bool, home_risk_state: str, home_stale: bool) -> str`:
  - `holdings_empty is True` → `"flat"` (an empty book: weight 0 is true, nothing to build).
  - `holdings_empty is None` (holdings unknown) → `"self_build"` (the build then fails → unknown → hold; never trust Home's frame for a book we couldn't read).
  - else `"home"` only when ALL hold: `home_frame_state == "ready"`, `not home_frame_empty`, `home_risk_state == "ready"`, `not home_stale`; otherwise `"self_build"`.
  Reason for the risk-state leg (lead's call): weights and beta must come from the same build (CLAUDE.md "one freshness per number"), so a missing / failed `_port_risk_cache` rebuilds both rather than mixing Home's weights with a local beta.
- `banner(*, source: str, fit_state: dict | None, built_at_et: str | None, n_holdings: int | None) -> tuple[str, str] | None` → `(tier, markdown)` or None:
  - fit state `"unknown"` (or unreadable) → `("warning", "⚠️ **Enter Now is held on this page today:** your holdings couldn't be loaded or priced here, so how each name would fit your book (sector concentration and portfolio beta) couldn't be checked. That's a reason to pause, not a green light. It re-runs the next time this page loads.")`
  - source `"self_build"` with fit `"ready"` → `("info", f"ℹ️ Sector weights and portfolio beta for the checks below were built on this page at {built_at_et} from your {n} holding(s) at the last loaded prices (refreshed every 30 minutes), not from 🏠 Home's live prices.")` (omit " at {built_at_et}" when None; "holding" singular for 1).
  - `"home"` or `"flat"` → None.
- `beta_note(*, fit_state: dict | None, portfolio_beta) -> str | None`: when fit is `"ready"` and `buy_check_state.beta_or_none(portfolio_beta) is None` → `"⚠️ **Portfolio beta couldn't be computed** — a name whose own beta is above the limit is held (the portfolio-beta check couldn't run for it); other names are unaffected."`; else None. (Uses `TICKER_BETA_CRITICAL` only via the advisor; no number in the text.)

## 2. `app.py` Watchlist wiring (render-only; every decision above)

In the "Build portfolio-fit context" block (~:30670-30755):

1. `_wl_hold_df = st.session_state.get("holdings_df")`; `holdings_empty` = None when it is not a DataFrame, else `.empty`. Rows = `_wl_hold_df.to_dict("records")` when it is a DataFrame.
2. `_wl_home_df = st.session_state.get("_port_df_enriched")`; `home_frame_empty` = True unless it is a non-empty DataFrame with a "Sector" column.
3. `_wl_source = watchlist_fit_source.decide_source(holdings_empty=…, home_frame_state=_coord_cache_state("_port_df_enriched"), home_frame_empty=…, home_risk_state=_coord_cache_state("_port_risk_cache"), home_stale=_portfolio_snapshot_stale())`.
4. **home** → `_wl_port_df = _wl_home_df`, `_wl_port_beta = _port_risk_cache["beta"]` (as today).
   **self_build** → in one try: held tickers from the rows; `_bundles = _parallel_load_all(held, period="6mo")` (drop None values); `_frame = build_portfolio_df(rows, _bundles, manual_stops={}, held_stops=None)`; `_frame = attach_gate_weight(_frame)`; beta: `compute_portfolio_risk_metrics(_frame, _bundles, _cached_spy("6mo"), _get_rfr())` in its own inner try (failure → beta None, frame kept), `_wl_port_beta = _f(port_risk.get("beta"))` style (None when absent / non-finite via `buy_check_state.beta_or_none`). Any exception in the frame part → `_frame = None`. `_wl_port_df = _frame if a DataFrame else pd.DataFrame()`. `_wl_built_at = _now_et().strftime("%-I:%M %p ET")` (use the same time format the app already uses for "built at" captions; check for an existing helper; on Windows `%-I` fails locally but the app runs on Linux: prefer an existing helper).
   **flat** → `_wl_port_df = pd.DataFrame()`, `_wl_port_beta = None`.
   **Never** write the self-built frame, its beta or bundles to `st.session_state` or any coordination cache (plan §2: its Stop columns are blank and publishing would mark Home's frame ready). Comment that at the site.
5. `_wl_fit_state = buy_check_state.fit_state(rows if holdings known else None, _wl_port_df if source != "flat" else None)`. For `home`, pass Home's frame. For `flat`, rows are `[]` → flat.
6. Add `"fit_state": _wl_fit_state` to every `_wl_pctx` (this switches on the C2a abstentions in the app). `portfolio_beta` = `_wl_port_beta`.
7. Banners:
   - Render `watchlist_fit_source.banner(...)` (st.warning / st.info by tier) right where the "fails OPEN" text was.
   - Remove the "**Also off: the sector ceiling check (G-05)** … this one fails OPEN." clause from the Brief-offline banner and the `_wl_gate_blind` variable + its long COUPLING comment (the G-05 check no longer fails open; the new banner covers an unbuilt frame). Keep the rest of the Brief-offline banner (Grow Today / Risk Advisor coordination) unchanged.
   - Replace the "Portfolio beta unavailable this session … Visit 🏠 Home to compute it." banner with `watchlist_fit_source.beta_note(...)` (st.warning), so it reflects the beta actually used (Home's or the self-built one).
   - `_render_portfolio_stale_banner(key_suffix="wl")` stays (it also qualifies the Grow Today / risk-alert coordination read from Home).
8. The macro-calendar build on this page (`build_macro_calendar(_wl_port_df, …)`) receives the frame actually used (it already reads `_wl_port_df`; make sure it's the self-built one when self-building).

## 3. Docs (lead does these after review; the implementer leaves them)

requirements F-ID / G-34 line for the app Watchlist; User Guide Watchlist paragraph; a dated "population event" line in `docs/plans/entry-regime-check.md` §4 (app Watchlist Enter Now calls can now be held by a fit / beta abstention; ~0 bias, outage-driven); plan Status.

## 4. Tests (new `tests/test_watchlist_fit_source.py` + AST pins)

- `decide_source`: flat on empty holdings; self_build on holdings None; home only with ready frame + non-empty + ready risk + not stale; each failing leg alone → self_build (frame missing / offline, empty frame, risk missing / offline, stale).
- `banner`: unknown → warning text (no "fails OPEN", says "held"); self_build + ready → info with time and singular / plural holdings; home / flat → None; no `$` in any text; `**` only as bold pairs.
- `beta_note`: ready + beta None / NaN → text; ready + beta 1.2 → None; unknown / flat → None.
- Advisor end-to-end through `build_watchlist_recommendation` with the app-shaped ctx: fit unknown → NEAR_ENTRY kind `fit_unknown`; ready + port beta None + ticker beta 1.81 → `beta_unknown`; 1.80 → passes; G-05 at 35.0 still binds before `fit_unknown`; flat → ENTER_NOW allowed.
- AST / source pins on app.py: the Watchlist block calls `watchlist_fit_source.decide_source` and `buy_check_state.fit_state`; `"fit_state"` is a key of the `_wl_pctx` dict literal; the string "fails OPEN" no longer appears in the Watchlist block; no `st.session_state[...] =` assignment of the self-built frame inside the block (pin that the self-build names never reach session_state).
- Run the targeted files (the new test, test_watchlist*, test_buy_check_state*, test_entry_eligibility*, any app-AST Watchlist pins: grep tests/ for "fails OPEN" and "_wl_pctx"), plus `scripts/check_antipatterns.py` and `scripts/check_constants_documented.py`. Tests must not read docs/review-policy.md (not a suite input). Do not commit.
