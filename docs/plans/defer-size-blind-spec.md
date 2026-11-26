# DeferSizeBlind C1–C3: build spec (lead, 2026-11-26)

**Status:** SPEC, for the implementer. Design: `docs/plans/defer-size-blind.md` (Opus planner PROCEED WITH CHANGES; owner took every default Q1–Q8 2026-11-26). Read that file first: its tables (where each Act sell keeps its size; the `cover_state` table), wording and test list are part of this spec.

**Sequencing (lead's call):** C1 + C2 + C3 are built in ONE pass and reviewed once, then committed as two commits (core: C1 + C2; wiring: C3) and pushed together. Shipping C2 alone would show a kept ceiling-trim card beside a smaller Act Today sell with no "do that sale first" line, which invites a double sale on one name.

No constant changes value; no new constant. Nothing new is written to a history table.

## C1: new gate module `stock_analyzer/act_sell_cover.py` (pure)

Add to `_GATE_FILES` with a reason comment + one docs/review-policy.md line. Tests must not read docs/review-policy.md.

- `item_covers(item) -> dict[str, dict]`: the cover(s) one Act / Review item gives, `{TICKER: {"exit": bool, "shares": int | None}}` (empty dict = not a sell). Rules (plan §"Design" 1 + owner Q1 / Q2):
  - Act `kind` in (`stop_breach`, `deterioration_exit`) → `{ticker: exit}`.
  - Act `sell_signal` → shares = `min_trim.trim_shares` when a valid count (find the exact key on the item near daily_briefing.py ~3753), else None (unknown). Never exit (Q1).
  - Act `deterioration_trim` → shares = the item's new `trim_shares` (C2) when valid, else None.
  - Act `risk_off_derisk` → shares None always (Q2: never a full cover).
  - Review item with `action.type` in `decision_bucket._ACT_REVIEW_TYPES` → `action.trim_shares` when valid, attributed to `action.trim_ticker` when set (else `ticker`); if `ticker` is also set and differs from `trim_ticker`, that ticker gets `{"exit": False, "shares": None}`.
  - Anything else (kind `risk`, `critical_news`, `premortem_triggered`, macro Act items, WATCH / HOLD / TIGHTEN_ONLY review items, non-dicts) → `{}`. Membership must equal today's `held_trim_owner.brief_sell_names_ticker` exactly.
  - Valid count = a non-bool `int` ≥ 1, or a float that is finite, integral and ≥ 1 (cast to int); anything else (0, 1.5, True, "2", NaN, None) → None.
- `sell_cover_summary(act_today, review) -> dict`: combine per ticker (upper-cased): any exit → exit; else the LARGEST valid count; else None. A sized + an unsized item → the sized count.
- `view_sell_cover(view) -> dict | None`: None when `view` isn't a dict or `view["state"] == "offline"`; else `sell_cover_summary(view["active"] if a list else [], [])` (mirror `held_trim_owner.view_sell_tickers`).
- `cover_state(cover, need) -> "show" | "covered" | "partial" | "unknown"` per the plan's table: cover None → show; exit → covered; need == 0 → covered (Q8); both ints and shares ≥ need → covered; shares < need → partial; shares None or need None → unknown. `need` valid = non-bool int ≥ 0 (a float integral ≥ 0 cast); else None.
- `cover_note(state, ticker, noun) -> str | None`: the plan's partial / unknown sentences (Q3), `{noun}` = "ceiling trim" or "row"; None for other states. No `$`, no `**`, no share count.

## C2: producers + `held_trim_owner` + `act_sell_mention` + build time

- `risk_advisor.py` (~:910-916, the single-name concentration rec): add `rec["trim_shares"] = int(excess_dollar / _px)` when `_px` is a positive finite number, else None. It must be 0 exactly when `_trim_sized` is False (test).
- `daily_briefing.py` Act `deterioration_trim` item (~:3025-3067): add `"trim_shares": _det_n if (not _is_exit and d["shares"] > 0 and _det_n >= 1) else 0`; leave `shares` as is (None for TRIM; the sell-log gating reads `kind`). Pin that `exit_signals` capture doesn't persist `trim_shares`.
- `held_trim_owner.py`:
  - `brief_sell_tickers(act, review)` = `set(act_sell_cover.sell_cover_summary(act, review))`; `view_sell_tickers(view)` = `None` if `view_sell_cover(view)` is None else its key set. Test both equal the OLD implementation's output over the existing fixtures (keep the old logic as a test oracle).
  - `tuneup_single_name_disposition(ticker, sell_cover, earnings_days, weight_pct, n_positions, shares, need)`: `sell_cover` a cover map (or None = no deferral); state from `cover_state(sell_cover.get(T), need)`: covered → `brief_owns` (today's text); partial / unknown → state `partial` / `unknown`, `text` = `cover_note(..., "ceiling trim")`; show → the existing `_earnings_note_or_show`.
  - `risk_analysis_single_name_line(..., sell_cover, ..., need)`: None → `unverified` (as today); covered → `brief_owns` (as today); partial / unknown → tier "info", text = `cover_note(..., "ceiling trim")`; else the earnings note / show as today.
  - `rebalancer_trim_disposition(ticker, sell_cover, reduce_calls_ok, earnings_days, directive, need)`: deferral only when `reduce_calls_ok is True`; covered → `brief_owns` (as today); partial / unknown → state + `cover_note(..., "row")`; then the earnings note / show as today (the cover line takes precedence over the earnings line).
  - `reconcile_tuneup(cards, deferred, view_cover)`: re-decide EVERY single-name card (kept cards with `type == "single_name_concentration"` and a `subject`, plus every deferred payload card) against `view_cover`: covered → deferred text (the payload text; a kept card that is now covered gets today's `brief_owns` text); partial / unknown → a COPY of the card with `act_cover_note`; show → the card as is. `view_cover` None → every deferred card restored, no notes, no captions. Legacy shapes: a card / payload with no `trim_shares` reads need None (unknown under a non-exit cover); a legacy `{T: str}` payload keeps its text only when the cover is an exit, else dropped (no card to restore, never a false caption). Never mutates inputs (deep-copy test).
- `daily_briefing._portfolio_tuneup`: the single-name card gains `"subject": _sn` and `"trim_shares": rec.get("trim_shares")`; the param becomes `sell_cover` (the caller passes `act_sell_cover.sell_cover_summary` over the FINAL act / review lists, ~:4488); defer (write `deferred_out`, drop the card) ONLY when the state is `brief_owns`; partial / unknown cards are KEPT without a note (Home's `reconcile_tuneup` adds it from the view). The `_d0` earnings-note call uses an empty cover map. The Brief result key `portfolio_tuneup_deferred` keeps its shape.
- `act_sell_mention.py`: `verified_sell_cover(...)` with the same five gate legs as `verified_sells`, returning `view_sell_cover(view)`; `verified_sells(...)` = key set of it (None stays None). Test they agree, incl. None / offline.
- `app.py`: `_SYNTH_SCHEMA_VER` 29 → 30 (comment: single-name Tune-up cards carry `subject` / `trim_shares`, the Act deterioration trim carries `trim_shares`, and deferral is size-aware; was 29: …). Add a sibling of `_verified_act_today_sells()` returning the cover map from the SAME view and gate.

## C3: wiring (render-only app.py + rebalancer.py)

- `rebalancer.py` (~:205-262): rename the kwarg `sell_tickers` → `act_sell_cover` (update callers and tests on purpose); `need = shares_delta if price > 0 else None` (for an OK-status row, need 0); `brief_owns` rows as today (deferred list, out of the totals); partial / unknown rows STAY in `trims` at their full `shares_delta`, carry `act_cover_note` and the header-tag flag, and count in every total (Q4); return `act_partial_count`. Keep `priority` / `expanded` on a partial row.
- app.py, render only (no new comparison against a constants value):
  - 🏠 Home Tune-up: pass `act_sell_cover.view_sell_cover(_act_view)` to `reconcile_tuneup`; render a card's `act_cover_note` as a muted line inside the card HTML through `safe_html` (like `earnings_note`).
  - 🔗 Risk Analysis → 📋 Action Plan: the single-name card uses the cover-map helper and passes `need` = the rec's `trim_shares`; a partial / unknown line renders as `st.info` where the `brief_owns` line renders today.
  - ⚖️ Rebalancer: pass the cover map; a partial / unknown row gets an `st.info(act_cover_note)` at the top of its expander and the header suffix `act_sell_mention.card_tag`-style " · 🏠 Act Today sell" (Q5); the "Trim needed" help text becomes "Rows today's Act Today sell fully covers aren't counted; a row it only partly covers is counted at its own size." shown when the deferred count or `act_partial_count` > 0.
  - Find each site by grepping `reconcile_tuneup`, `risk_analysis_single_name_line`, `rebalancer_trim_disposition` / the rebalancer call, `_verified_act_today_sells` (planner's pointers: app.py ~10062, ~19501, ~23421, ~23532).

## Tests

Everything in the plan's "Tests the build must include" list (18 items), in a new `tests/test_act_sell_cover.py` plus updates to test_held_trim_owner.py, test_rebalancer_defer.py, test_earnings_week_consistency.py, test_act_sell_mention.py, test_brief_one_trim_per_name.py, test_daily_briefing.py. The end-to-end defect test is mandatory: a ≈ 30% holding with score ≥ 55 and a 1-share macro `PROTECTIVE_TRIM` on it → the Tune-up card is kept with the partial note after `reconcile_tuneup`, Risk Analysis reads partial, the Rebalancer row stays in `trims` with the note and counts in `trim_pct` / `trim_action_needed`. Grep tests/ for every changed string / kwarg / signature and update pins. Run the targeted files at most -n 4, plus `scripts/check_antipatterns.py` and `scripts/check_constants_documented.py`; re-run after the final edit. Do not commit. Tell me which files / hunks are C3 (wiring) vs C1 + C2 (core) so I can split the commits; if a clean split isn't possible (a signature change forces a caller edit in C2), say so and I'll ship them as one commit.
