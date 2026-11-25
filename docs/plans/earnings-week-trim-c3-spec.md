# EW C3 build spec (Tune-up + Risk Analysis defer to a Brief sell)

**Status:** SPEC 2026-11-24 (lead, Opus 5.5), from `docs/plans/earnings-week-trim-consistency.md` §3 / §4 C3 (owner defaults U2a, U2b a). C1 + C2 shipped in `ecfc352c` (spec `docs/plans/earnings-week-trim-c1c2-spec.md`). Implementer builds; one Opus review. **BUILT 2026-11-24: Opus FIX-FIRST (1 blocking: a recovered stop breach still counted as a sell) → round 2 → SHIP, 0 blocking.**

**Goal.** Home's Brief can show an Act Today earnings trim to ≈10% AND a 🔧 Tune-up "Trim … toward the 15% ceiling" card for the same name in the same render (plan §2 S2, worked example A); 🔗 Risk Analysis → 📋 Action Plan shows the same 15% card with no note (S5). After C3, a single-name card on a name the Brief already SELLS is dropped from the Tune-up with a visible caption, and annotated on Risk Analysis; an in-window name the Brief doesn't sell (under the earnings-week line, case B) keeps its card with an earnings line. Never a silent drop. No constant changes value; no new constant.

The "Brief sells this name" test is ALWAYS `held_trim_owner.brief_sell_names_ticker` / `brief_sell_tickers` (C1). Never raw `reduce_call_tickers` / `_reduce_calls` keys: an Act `risk` card's ticker is an attribution (plan §6).

Out of scope: the Rebalancer (C4), `rec_events` capture (U4a: `single_name_concentration` rows unchanged; don't touch `rec_events_capture.py`), docs (C5; the lead does CLAUDE.md / plan Status), the EWSiblings items.

---

## 1. `stock_analyzer/held_trim_owner.py` (gate file)

1. **Earnings-note wording + unknown weight.** In `tuneup_single_name_disposition`:
   - weight `None` / non-finite → `{"state": "show", "text": None}` when not `brief_owns` (we can't claim "under the line" for an unread weight). `brief_owns` still wins first.
   - `earnings_note` text becomes (the C2 review's lesson: "nothing is due" was false when another rule trims the name):
     `f"Earnings {when}: under the {floor:.1f}% earnings-week line, so the earnings-week rule sizes nothing before the report; this card is standing work."`
   - Update its tests.
2. **New `book_inputs(port_df, ticker) -> dict`**: `{"weight_pct": float | None, "n_positions": int, "shares": float | None}` from the row whose `Ticker` matches (case-insensitive): `Weight (%)` via the module's float coercion (NaN → None), shares via `trim_sizing.exact_shares(row)`, `n_positions = len(port_df)` (0 for None / empty). Ticker not found → weight / shares None. Pure.
3. **New `sell_tickers_from_reduce_calls(reduce_calls: dict | None) -> set[str] | None`**: None in → None out; else `{T.upper() for T, item in reduce_calls.items() if brief_sell_names_ticker(item, T)}`. (`_reduce_calls` maps ticker → its FIRST reduce item in split order; if a `risk` attribution item wins for a ticker that also has a review trim, this returns no deferral for it: the card stays, which fails toward showing the sell. Say so in the docstring.)
4. **New `risk_analysis_single_name_line(ticker, sell_tickers, earnings_days, weight_pct, n_positions, shares) -> dict`** → `{"state", "tier", "text"}`:
   - `sell_tickers is None` (the cross-check couldn't run: Home hasn't built this session, the Brief failed, or the book changed since) → state `"unverified"`, tier `"caption"`, text `f"⚠️ Today's Act Today calls couldn't be checked against this card (🏠 Home hasn't built since the book last changed), so it may repeat a sell Act Today already sizes for {T}."`
   - `T in sell_tickers` → state `"brief_owns"`, tier `"info"`, text `f"{T} already has a sell in today's Act Today call (🏠 Home). Follow that call; this ceiling trim is not an extra sell on top of it."`
   - else the `tuneup_single_name_disposition` earnings rule (in window + no decision + known weight) → state `"earnings_note"`, tier `"caption"`, same earnings-note text as (1).
   - else `{"state": "show", "tier": None, "text": None}`.
   - No `$` or `**` in any text (they render through st.info / st.caption).

## 2. `stock_analyzer/daily_briefing.py` (gate file)

- `_portfolio_tuneup(risk_recs, acted_tickers=None, sell_tickers=None, earnings_days=None, port_df=None)`: for a rec of type `single_name_concentration`, take `T = root_tickers[0]["ticker"]` (the card names one ticker; if `root_tickers` is empty, keep the card as today), `inp = held_trim_owner.book_inputs(port_df, T)`, `d = held_trim_owner.tuneup_single_name_disposition(T, sell_tickers or set(), earnings_days or {}, inp["weight_pct"], inp["n_positions"], inp["shares"])`:
  - `brief_owns` → drop the card; record `deferred[T] = d["text"]`.
  - `earnings_note` → keep the card with an extra key `"earnings_note": d["text"]`.
  - `show` → keep as today.
  - All other types and the beta / sharpe dedup: unchanged.
  Return shape: keep returning the list; add the deferred dict via a second return value OR an out-parameter, whichever keeps every existing caller / test simplest (grep callers; report the choice).
- At the call site (~:4258): `sell_tickers = held_trim_owner.brief_sell_tickers(act, review)` (act / review FINAL, i.e. after the risk-off append, same point as `_acted_final`); `earnings_days = trim_sizing.earnings_days_by_ticker(held_data, today)` (the ET `today` already in scope; check the variable names). Add `"portfolio_tuneup_deferred": deferred` (always a dict, `{}` when none) to `_result`.
- Fix the false comment at ~:399-403 ("vol/drawdown/tail/concentration recommend ADDING diversifiers … never restate a trim"): the single-name card DOES restate a trim; say it is deduped separately against a Brief sell that names its ticker via `held_trim_owner` (EW C3), and that the beta/sharpe chip filter keeps its own broader basis.
- Check whether any other path builds the Brief result (a locked snapshot, the offline / crashed path, the memo HIT path) and must carry the new key; consumers read it with a default (below), so a missing key renders nothing extra. Report what you found.

## 3. app.py (render only; no new comparison against a constants value)

- **Home Tune-up** (~:13487-13519):
  - `_db_tuneup_deferred = _daily_brief.get("portfolio_tuneup_deferred", {})` next to `_db_tuneup` (~:9814). Two-arg `.get`, never `or {}`.
  - In each card's HTML, when `_tu.get("earnings_note")` is a non-empty string, one more line under the recommendation, same muted style as the "Positions:" line, text through `safe_html` (house rule for interpolated HTML; the note has no `$` / `**`).
  - After the cards' closing caption (or on its own when `_db_tuneup` is empty but the dict isn't), one `st.caption` per deferred ticker with `"🔧 " + text`, sorted by ticker. It must show even when every Tune-up card was deferred (never a silent drop).
- **Risk Analysis → 📋 Action Plan** (the `for _rec in _sorted_recs:` loop ~:19025): for `_rtype == "single_name_concentration"`, inside the expander right after the Problem banner:
  - `_sn_t` = `_rec["root_tickers"][0]["ticker"]` (guard empty).
  - verified = `_coord_cache_state("_reduce_calls") == "ready"` and not `st.session_state.get("_daily_brief_offline", False)` and `coord_freshness.classify(st.session_state.get("_coord_stamps"), _coord_epoch(), "_reduce_calls") == coord_freshness.FRESH` (check the constant's name).
  - `sell = held_trim_owner.sell_tickers_from_reduce_calls(st.session_state.get("_reduce_calls")) if verified else None`.
  - book / earnings from `_get_or_offline(st.session_state, "_last_port_df")` / `"_last_held_data"` (check the exact keys and the ET-today helper `_today_et()`); `earnings_days = trim_sizing.earnings_days_by_ticker(held, _today_et())` (offline held → `{}`).
  - `line = held_trim_owner.risk_analysis_single_name_line(...)`; tier `"info"` → `st.info(text)`, `"caption"` → `st.caption(text)`, None → nothing.
  - Wrap the block in a try / except that renders nothing on failure (a cross-check must never take the Action Plan down), the way neighbouring optional blocks do.
- `_SYNTH_SCHEMA_VER` 23 → 24 (the memoized Brief result gains a key), comment prepended in the house pattern; update the tests that pin it.

## 4. Tests

- `tests/test_held_trim_owner.py`: new earnings-note text; weight None → show; `book_inputs` (found, case-insensitive, missing ticker, NaN weight, None / empty df); `sell_tickers_from_reduce_calls` (None → None; a `risk` item → not counted; review TRIM_TO_TARGET / exit / deterioration_trim → counted; lower-case keys); `risk_analysis_single_name_line` four states + no `$` / `**` in any text.
- `_portfolio_tuneup` (in the existing Tune-up test file; grep for `_portfolio_tuneup`): dropped + deferred for a same-ticker review TRIM_TO_TARGET (earnings trim, weak-large), an exit, a deterioration_trim, a risk_off_derisk; KEPT for a `risk` attribution card, a critical-news hold, an earnings WATCH; `earnings_note` in case B (N=8, w=16.0, earnings in 3d, no Brief sell); no note out of the window (days 8 / −1); unknown weight → card kept, no note; beta / sharpe behaviour unchanged (existing tests stay green untouched).
- `tests/test_earnings_week_consistency.py`: extend the matrix with S2: for in-window cells with w ≥ `SINGLE_NAME_CEILING` and score ≥ `WEAK_CONVICTION_SCORE`, the Tune-up single-name card is absent and deferred exactly when the Brief issues a sized earnings / standing trim on that name (via `build_daily_briefing`), present otherwise; plan §7 item 4 boundaries N=10 w=15.0 (card kept, note) / 15.1 (card deferred).
- `build_daily_briefing` result always has `portfolio_tuneup_deferred` as a dict.
- app.py grep tests: Home reads `portfolio_tuneup_deferred` with a two-arg `.get`, renders `earnings_note` through `safe_html`; the Risk Analysis single-name branch calls `held_trim_owner.risk_analysis_single_name_line` and its verified check includes `_coord_cache_state("_reduce_calls")`, `_daily_brief_offline` and `coord_freshness.classify`.

## 5. Run rules

Targeted pytest ≤ `-n 4` (files importing daily_briefing / held_trim_owner / risk_advisor, the Tune-up and synth-schema tests, `test_repo_hygiene.py`, `test_pre_tool_checks.py`); `python scripts/check_antipatterns.py`; `python scripts/check_constants_documented.py`; `python -m py_compile app.py`. No git add / commit / stash; no docs. Stop and report if the spec is wrong at HEAD.

Report: files changed (one line each), the `_portfolio_tuneup` return-shape choice, other Brief-result builders found, the exact `_last_port_df` / `_last_held_data` / `FRESH` names used, test counts.

---

## Round 2 — fixes from the Opus review (FIX-FIRST, 1 blocking, 2026-11-24)

### R3-B1 (blocking): decide against the Act Today view Home renders, not the raw Brief

A `stop_breach` whose live price has recovered is moved by `act_today_view` (`stock_analyzer/act_today_view.py` ~:183-205) into "⚠️ Stop Breach — Resolved", out of Act Today; the Brief is memoized on composition, not price. Both C3 deferrals read the raw Brief (`brief_sell_tickers(act, review)` at build; `_reduce_calls` on Risk Analysis), so a resolved breach still "sells" the name: the Tune-up card is dropped behind a false caption and Risk Analysis says "Follow that call" for a call Home no longer shows. Fix:

1. **`held_trim_owner.view_sell_tickers(view) -> set[str] | None`**: None when `view` is not a dict or `view.get("state") == "offline"`; else the upper-cased tickers for which `brief_sell_names_ticker(item, item_ticker)` is True over `view["active"]` (the post-demotion Act bucket; items from both lanes, the predicate already handles both shapes; check that and say so). Resolved breaches are NOT in `active`, so they never count.
2. **Build side (daily_briefing)**: keep deferring at build time as now, but the deferred payload carries the dropped card: `portfolio_tuneup_deferred = {T: {"text": str, "card": dict}}` (the card dict exactly as `_portfolio_tuneup` would have appended it, incl. `earnings_note` if any).
3. **New pure `held_trim_owner.reconcile_tuneup(cards, deferred, view_sells) -> dict`** → `{"cards": [...], "deferred_texts": {T: text}}`:
   - `view_sells is None` → every deferred card is restored (fail toward showing the sell), `deferred_texts` = {}.
   - else a deferred T stays deferred only if `T in view_sells`; otherwise its card is appended back to `cards` (after the existing ones, in ticker order) and it has no text.
   - Tolerates `deferred` in the old shape `{T: str}` (a memoized / locked Brief from before this round): treat as "no card to restore" and keep the text only if `T in view_sells`; else drop the text (no false caption).
   - Non-dict / None inputs → `{"cards": list(cards or []), "deferred_texts": {}}`.
4. **Home (app.py)**: right after `_db_tuneup` / `_db_tuneup_deferred` are read (~:9814; `_act_view` is already built at ~:9222 — confirm), call `reconcile_tuneup(_db_tuneup, _db_tuneup_deferred, held_trim_owner.view_sell_tickers(_act_view))` and REPLACE `_db_tuneup` with its `cards` (so the Tune-up count at ~:9940 and the render agree) and render the captions from `deferred_texts`.
5. **Risk Analysis (app.py)**: keep the liveness gate (`_coord_cache_state("_reduce_calls") == "ready"`, not `_daily_brief_offline`, `coord_freshness.classify(... "_reduce_calls") == FRESH`). When verified, build the view the way 📡 Signals does (~:16990-17003: Brief from `_home_synth_cache["bundle"]["_daily_brief"]` + its `_daily_brief_offline`, `_port_df_enriched`, `_live_prices`, `STOP_RECOVERY_MARGIN_PCT`, `_now_et()`) and `sell = held_trim_owner.view_sell_tickers(view)`; not verified → None. This also closes the first-reduce-item-per-ticker gap; delete `sell_tickers_from_reduce_calls` and its tests (no other caller; grep).
6. Tests: a resolved breach on a ≥ 15% name → card restored on Home, no caption, Risk Analysis line not `brief_owns`; an active breach → still deferred / `brief_owns`; an Act `risk` attribution + a review earnings trim on one ticker → Risk Analysis `brief_owns` (the old first-item gap); `reconcile_tuneup` None / old-shape / mixed cases; view offline → None.

### Non-blocking, fix now

- **R3-N1** `risk_analysis_single_name_line` "unverified" text → `f"⚠️ Today's Act Today calls couldn't be checked against this card (🏠 Home's Act Today isn't available for the current book: not built yet, failed, or out of date), so it may repeat a sell Act Today already sizes for {T}."`
- **R3-N2** partial trims (TRIM_AND_TIGHTEN, deterioration_trim, risk_off_derisk, macro PROTECTIVE_TRIM can leave the name over 15%):
  - Tune-up deferred text → `f"{T} already has a sell in today's Act Today call; its single-name ceiling card returns if {T} is still over {SINGLE_NAME_CEILING:.0f}% after that."` (app.py keeps prefixing "🔧 ").
  - Risk Analysis `brief_owns` text → `f"{T} already has a sell in today's Act Today call (🏠 Home). Follow that call first; this ceiling trim is not an extra sell on top of it today, and applies again only if {T} is still over {SINGLE_NAME_CEILING:.0f}% afterwards."`
- **R3-N3** "under the X% line" when w == floor (the decision is strict `>`): change to "at or under the …% earnings-week line" in every sentence of the class: the held_trim_owner earnings note; daily_briefing earnings WATCH (the ≥ ceiling one) and the `standing_in_window` why; portfolio.py `standing_in_window` trigger and `earnings_note`; earnings_advisor `standing_in_window` text 1. Update the pinning tests.
- **R3-N4** the Risk Analysis try / except: log the exception with the module's existing logging idiom (grep neighbouring blocks in app.py for how a swallowed render failure is logged) instead of a bare `pass`; still render nothing.

Same run rules as round 1. Report per item, the view-item shape check for (1), test counts.
