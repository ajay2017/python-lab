# EW C4 build spec (the ⚖️ Rebalancer defers its trims to an Act Today sell)

**Status:** SPEC 2026-11-24 (lead, Opus 5.5), from `docs/plans/earnings-week-trim-consistency.md` §3 (Rebalancer) / §4 C4 (owner U3a, U7 yes). C1 + C2 `ecfc352c`, C3 `90a4365b` shipped. Implementer builds; one Opus review. **BUILT 2026-11-24: Opus FIX-FIRST (1 blocking: the banner said Act Today "sized" names whose card gives no share count) → lead string fix → confirming round SHIP, 0 blocking.**

**Goal.** 🥧 Allocation → ⚖️ Rebalancing Advisor sizes trims to equal weight / custom targets and never looks at the Brief, so on a day Act Today sells a name (an earnings trim to ≈10%, an exit, a deterioration trim …) the Rebalancer can show its own "sell ~N shares" for the same name (plan §2 S6, worked example A: three sizes for one name). After C4, a Rebalancer row for a name the live Act Today view sells is a **deferred row** (no share count of its own, out of the trim totals) listed under a visible banner; an in-window name the Brief doesn't sell gets one earnings line on its trim row. Never a silent drop. No constant changes value; no new constant.

**C3's lesson applies.** The sell set is `held_trim_owner.view_sell_tickers(act_today_view(...))` (the post-demotion Act bucket: a recovered stop breach is not a sell), built the way 🔗 Risk Analysis builds it in C3 (app.py ~19090-19130) behind the same liveness gate. Never `_reduce_calls` keys (an Act `risk` card's ticker is an attribution).

Out of scope: the existing ADD gating (`reduce_call_set`, `risk_trim_set`, `add_verdicts`) stays exactly as is, including its own caption; the G-02 gate-ledger capture; `compute_drift`'s `int(shares)`; the urgency point values; docs (lead).

---

## 1. `stock_analyzer/rebalancer.py` (joins `_GATE_FILES` in this commit)

- `build_rebalance_plan(..., sell_tickers: set | None = None, earnings_days: dict | None = None)` (new keyword args, defaults keep every existing caller / test unchanged):
  - `sell_tickers is None` = the cross-check couldn't run → no deferral at all (fail toward showing the trim).
  - For each drift row, `d = held_trim_owner.rebalancer_trim_disposition(T, sell_tickers or set(), sell_tickers is not None, earnings_days or {}, None)` (directive `None`: the deferred row never quotes the Brief's share count, so it can't mix the Brief's numbers with a newer book; text = "Sized by today's Act Today call (🏠 Home).").
  - `brief_owns` and the row is overweight (`drift_pp > 0`, any status) → NOT appended to `trims`; appended to a new `deferred_trims` list: `{"ticker", "status", "current_pct", "target_pct", "drift_pp", "text"}`.
  - `brief_owns` and status `OK` → stays in `ok_list` AND is appended to `deferred_trims` (a custom target can read OK while Act Today sells the name; the banner must still show it).
  - `brief_owns` and underweight → unchanged path (the existing `reduce_call_set` gate already handles adds); not listed in `deferred_trims`.
  - `earnings_note` on an overweight row that stays a trim → the trim dict gains `"earnings_note": d["text"]`.
  - Return dict gains `"deferred_trims"` (sorted by ticker) and `"deferred_trims_count"`; also in the empty-`drift_df` early return (`[]`, 0). `trims`, `total_trim_value`, `trim_pct`, `trim_action_needed`, `rebalance_pct` are computed over the kept `trims` only, so deferred rows are excluded automatically (check `rebalance_pct` and say what it now means for a deferred overweight row).
- **Move the priority decision out of app.py** (~:23064-23067): each trim dict gains `"priority"`: `"HIGH"` when `urgency >= 40 or status == "TRIM"`, else `"MEDIUM"`, and `"expanded"`: `urgency >= 40` (verbatim move of the existing rule; the 40 stays the literal it is today, with a one-line comment that it is the urgency scale's "Sell signal" step; don't touch the urgency points).
- `inst_lens` (~:286-293): interpolate the constants instead of the "15%" / "5pp" literals: `f"Institutional position sizing rule: no single name above {SINGLE_NAME_CEILING:.0f}% of portfolio, regardless of conviction — and even high-conviction names should be trimmed back to target when they outrun it by {TOLERANCE_WATCH:.0f}pp or more. "` + the rest unchanged (also fix the missing space after "portfolio," in today's string). Grep the second `inst_lens` (~:436, the add side) for the same literals; interpolate if present.
- Docstring: the two new args, the deferred rows, the priority fields.

## 2. app.py Rebalancer page (render only; no new comparison against a constants value)

- Build the view and sell set right after the existing `_rb_reduce_verified` block (~:22927), the C3 Risk Analysis way:
  - verified = `_coord_cache_state("_reduce_calls") == "ready"` and not `_daily_brief_offline` and `coord_freshness.classify(st.session_state.get("_coord_stamps"), _coord_epoch(), "_reduce_calls") == coord_freshness.FRESH`.
  - when verified: Brief from `_home_synth_cache["bundle"]["_daily_brief"]` (+ its `_daily_brief_offline`), `act_today_view(..., _port_df_enriched, _live_prices, STOP_RECOVERY_MARGIN_PCT, _now_et())`, `_rb_sells = held_trim_owner.view_sell_tickers(view)`; else `_rb_sells = None`. Reuse the `_rb_brief` already read at ~:22947 if it is the same object (check; don't read the cache twice with different helpers).
  - `_rb_earn_days = _trim_sizing.earnings_days_by_ticker(held_data_for_this_page, _today_et())` (find the page's held-data source; offline → `{}`).
  - Pass `sell_tickers=_rb_sells, earnings_days=_rb_earn_days` to `build_rebalance_plan`.
- When `_rb_sells is None`, one caption under the existing ones: `"⚠️ Trim rows below aren't checked against today's Act Today calls (🏠 Home's Act Today isn't available for the current book: not built yet, failed, or out of date), so a trim here may repeat a sell Act Today already sizes."`
- **Deferred banner**: when `deferred_trims` is non-empty, directly under the "✂️ Trim Actions (Overweight)" heading (render the heading when there are deferred rows even if `trims` is empty), one `st.info` with the first line `"🏠 Sized by today's Act Today call, so not trimmed here:"` and one line per row `f"• {T}: {current:.1f}% vs target {target:.1f}% ({drift:+.1f}pp)"`, joined with `"  \n"`. Plain text, no `$`, no HTML.
- Trim rows: use `_tr["priority"]` / `_tr["expanded"]` from the plan (delete the app.py decision); when `_tr.get("earnings_note")` is a non-empty string, one `st.caption(...)` inside the expander after the Recommended Action block.
- "Trim needed" KPI: `_n_trim = len(_rb_plan["trims"])` stays (deferred rows are no longer in `trims`); add `help=` text `"Deferred rows (sized by today's Act Today call) aren't counted."` only when `deferred_trims_count > 0` (or always; your choice, say which).
- **Drift-chart legend** (~:23050-23055, a sibling literal site named in CLAUDE.md "Smaller open items"): build it from `TOLERANCE_OK` / `TOLERANCE_WATCH` (`f"🔴 Red = overweight >{TOLERANCE_WATCH:.0f}pp (trim) · 🟡 Amber = overweight {TOLERANCE_OK:.0f}–{TOLERANCE_WATCH:.0f}pp (watch) · 🔵 Blue = underweight >{TOLERANCE_OK:.0f}pp (drift; adds are Grow Today's call) · ⬛ Gray = within ±{TOLERANCE_OK:.0f}pp tolerance"`), keeping today's separators. Check `drift_bar_color` uses those same constants; report if not.

## 3. Gate list

Add `"stock_analyzer/rebalancer.py"` to `_GATE_FILES` (`.claude/hooks/pre_tool_checks.py`) after `held_trim_owner.py`, with a reason comment in the list's style ("sizes sells: the Rebalancer's trim share counts; a wrong call sells the wrong size or a second time beside an Act Today sell"). The lead writes the review-policy.md / CLAUDE.md lines.

## 4. Tests (plan §7 item 8, plus)

New `tests/test_rebalancer_defer.py` (or extend the existing rebalancer test file; grep):
- A sold overweight row: not in `trims`, in `deferred_trims` with no share field, excluded from `trim_pct`, `total_trim_value`, `trim_action_needed`; the remaining trims unchanged.
- `sell_tickers=None` → no deferral, plan identical to today's (compare to a call without the new args).
- A sold OK row (custom target) appears in `deferred_trims` and stays in `ok`.
- A sold underweight row: the ADD gating is unchanged (`reduce_blocked_adds` when in `reduce_call_set`), not in `deferred_trims`.
- In-window overweight row with no sell → `earnings_note` "Earnings in Nd: check 🏠 Home's Act Today …" ("today" at d == 0); out of window → none.
- `priority` / `expanded` match the old app.py rule on a urgency/status table (40, 39, TRIM vs DRIFTING).
- `inst_lens` contains no "15%" / "5pp" literal and contains the formatted constants.
- Integration via `build_daily_briefing` + the real `act_today_view` (reuse the C3 test helpers in `tests/test_earnings_week_consistency.py`): an earnings trim on a name → that name deferred on the Rebalancer; a recovered stop breach → not deferred; a `risk` attribution only → not deferred.
- app.py grep tests: the Rebalancer passes `sell_tickers=` from `held_trim_owner.view_sell_tickers` behind `_coord_cache_state("_reduce_calls")`, `_daily_brief_offline` and `coord_freshness.classify`; no `_tr_pri =` decision left in app.py; the legend interpolates `TOLERANCE_WATCH` / `TOLERANCE_OK`.

## 5. Run rules

Targeted pytest ≤ `-n 4` (rebalancer tests, held_trim_owner, earnings_week_consistency, `test_repo_hygiene.py`, `test_pre_tool_checks.py` (expects the new `_GATE_FILES` member to be a tracked file: it already is), any test grepping the Rebalancer page); `python scripts/check_antipatterns.py` (re-key a baselined site only if you edit its source text, never `--init`); `python scripts/check_constants_documented.py`; `python -m py_compile app.py`. No git add / commit / stash; no docs. Stop and report if the spec is wrong at HEAD.

Report: files changed (one line each), `rebalance_pct` meaning after the change, the held-data source used on the page, whether `_rb_brief` was reused, the KPI help choice, `drift_bar_color` vs the constants, test counts.
