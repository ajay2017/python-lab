# One trim per name in the 🏠 Brief Review list (U6)

**Status:** DESIGNED 2026-11-24 (Opus planner `claude-opus-5-5`, PROCEED WITH CHANGES). Parent: `docs/plans/earnings-week-trim-consistency.md` §5 U6 (owner chose (a) "separate small plan"). **Owner took every default 2026-11-24 evening:** U6-1 (a) the largest sell by shares wins; U6-2 (a) losers stay visible as reworded Monitoring cards + one "also covers" line on the winner; U6-3 (a) a macro pick already sold harder stands down; U6-4 (a) Signals / Catalyst Watch mention = separate U6b after U6; and OK to drop the "Beat → re-enter post-print" clause when the sale is a low-score trim. No constant is added or changes value. **C1 + C2 BUILT + C3 docs DONE 2026-11-24** (implementer; Opus SHIP, 0 blocking; lead bumped `_SYNTH_SCHEMA_VER` 24 → 25). Reviewer siblings added to §8: a missing Score column reads as score 0 (weak) in `_review_list` (test-harness only; production frames carry Score); a winning earnings trim beside an UNSIZED weak-large Watch (portfolio value or price 0) keeps its "re-enter" clause. **VERIFIED live 2026-11-25** (owner screenshots: SNOW's 📅 card reads "Watch: covered by today's Act Today trim of SNOW; no separate trade."; the 8% Act trim unchanged at 2 shares ≈ $542). **Same day, §8 sibling fixed:** SNOW's 👁️ `DETERIORATION_WATCH` "no action today" sat beside that Act trim; `brief_trim_merge` now adds `covered_by` to a same-name `DETERIORATION_WATCH` beside a winning sized trim (type, headline, Why and Trigger unchanged, so the `exit_signals` WATCH capture and the judgment opinion still fire) and `_fmt_action` prints the covered text; `_SYNTH_SCHEMA_VER` 25 → 26. Next: U6b.

**Planner's four changes to the parent plan's option (b):** (1) compare by **shares sold**, not target weight (a profit-lock is a fraction of the position and a macro trim is pp of the book; only shares put all four on one scale); (2) losers stay visible in Monitoring as reworded Watch / stop-only cards, not a bare `why` line (keeps F-39c journal context, the earnings stop-vs-move Trigger and the rating-change flag); (3) the same pass fixes the live SNOW case (an Act weak-large trim to 8% beside a 📅 earnings WATCH that says "sizes nothing before the report … standing work" and whose `trimmed=False` Trigger can say "no pre-event action needed" / "no pre-event trim"; the generic `_fmt_action` WATCH label "no action today" is also false on a covered card); (4) a macro trim whose name is already sold harder by another card stands down, it doesn't move to a new name.

## 1. The defect, verified at HEAD (`21c07d5c`)

`daily_briefing._review_list` (daily_briefing.py:3240-3974) builds Review items in five independent sections. Three review `action.type`s are Act Today trades (`decision_bucket._ACT_REVIEW_TYPES`, decision_bucket.py:36; `classify_bucket` :178-181): `TRIM_AND_TIGHTEN`, `TRIM_TO_TARGET`, `PROTECTIVE_TRIM`. Sections defer to `_reduce_tickers` (Act exits + `deterioration_trim`, :3284-3291), never to each other:

| Section | Lines | Act trim it can issue | Size |
|---|---|---|---|
| 1 Approaching stop | :3330-3446 | `TRIM_AND_TIGHTEN` when gap ≤ 3.0 (bare literal :3369) and P&L ≥ `STOP_PROFIT_LOCK_PNL_PCT` (25.0, constants.py:508) | `fraction_trim_shares(shares, STOP_PROFIT_LOCK_TRIM_PCT)` (25.0, :509) + raise stop |
| 2 Earnings ≤ `EARNINGS_IMMINENT_DAYS` (7, :1139) | :3448-3559 | `TRIM_TO_TARGET` `reason_key="earnings"` from `trim_sizing.earnings_week_decision` | to `EARNINGS_OVERWEIGHT_TRIM_TO_PCT` (10.0, :909), or `SINGLE_NAME_CEILING` (15.0, :139) on the `standing_in_window` basis (N ≤ 7, w > `SINGLE_NAME_TRIM_TRIGGER` 18.0, :165) |
| 3 Weak large | :3561-3675 | `TRIM_TO_TARGET` `reason_key="weak_large"` when w ≥ `LARGE_POSITION_WEIGHT_PCT` (10.0, :492), score < `WEAK_CONVICTION_SCORE` (55, :493), score available | to `WEAK_LARGE_TRIM_TO_PCT` (8.0, :927) |
| 5 Macro (1–`MACRO_IMMINENT_DAYS` = 3 days, :1151) | :3734-3945 | `PROTECTIVE_TRIM` (ticker None, subject in `action.trim_ticker`) when exposure in (`MACRO_AFFECTED_TRIM_THRESHOLD_PCT` 30.0, `MACRO_BROAD_EXPOSURE_PCT` 60.0) | `MACRO_AFFECTED_TRIM_REDUCTION_PP` (5.0, :943) of the book, floored to shares; eligibility excludes only `_act_tickers` (:3770-3772), not Review trims |

Act-lane collisions are already closed (sections 1–3 skip `_reduce_tickers`, macro skips `_act_tickers`, risk-off excludes every flagged name :4222-4226, `_consolidate_act_today` :3134 dedupes the act lane). **Only Review-vs-Review collisions on one subject remain.**

### Reachable collisions (subject = `decision_bucket._ticker(item)`)

- **E sized + W sized** whenever w > the earnings line and score < 55. W always sells ≥ E: `shares_to_trim(s, w, t) = floor(s(w−t)/w)` falls as t rises, and 8 < 10 ≤ 15. Two Act sizes for one name.
- **E unsized (WATCH) + W sized** — the LIVE case: SNOW 11-25, N=8, line 17.5, w=17.4, score 50. Act trims to 8%; Monitoring says "the earnings-week rule sizes nothing before the report … (standing work)" and its Trigger (`trimmed=False`) can say "no pre-event action needed" / "no pre-event trim". Not two trims, but a same-render contradiction.
- **E or W + profit-lock.** Lock sells floor(0.25·s). W beats lock iff w > 10.67; E (to 10%) beats it iff w > 13.33; E standing (to 15%) iff w > 20.
- **Macro + W / E / lock.** Macro picks the lowest-score affected name, usually the W name. Macro sells more than W iff w − 8 < 5, i.e. w < 13.
- **Macro + macro.** Two HIGH sector events on one name (app.py:13193-13194 already notes "two macro cards trimming the same name").
- **Stop TIGHTEN_ONLY + any Act trim.** Not a second size, but its text ("position still has room" / "only the stop is tightened") is not the existing under-reduce wording (:3398-3406).

## 2. Design

### 2.1 Rule (U6-1 default)

For each subject T, gather T's sized Act-trim Review items (types in `_ACT_REVIEW_TYPES`, `trim_shares` an int ≥ 1). **Winner** = the item with the most `trim_shares`; ties go to `TRIM_AND_TIGHTEN` (it also carries the stop raise), then weak-large, then earnings, then macro (earliest event date, then event name). **The winner's action dict is copied unchanged** (shares, dollars, from/target, stop). **Every other T item becomes a non-trade Monitoring item** (§2.3).

Why shares: each of the four is "sell at least N shares of T today" on the same book in the same build, and each one's stated outcome is met by any larger sale of T (target trims: post-sale weight ≤ target; profit-lock: more gain locked; macro: affected exposure cut by ≥ its pp).

Rejected: prevent at source (later sections skip names an earlier section trimmed) — order-dependent, and the earlier card can be the smaller sell, so the merged card could sell less (fails §2.2).

### 2.2 The merged card never sells less (proof)

Let S = the multiset of T's sized Act-trim shares before the merge. After it, T has exactly one Act-trim item, selling max(S): ≥ every element of S and ≤ the largest card the owner sees today. Holds because losers become WATCH / TIGHTEN_ONLY (zero shares), no item's shares are recomputed, the act lane is never touched.

**Fail-safe:** if any Act-type item on T has a `trim_shares` that is not a finite int ≥ 1, T's items are left exactly as today (the proof can't be made, nothing is demoted). If the merge raises, `_review_list` logs a warning and keeps the unmerged list. Both fail toward showing every sell (the C3 / C4 posture).

### 2.3 Losers (U6-2 default: stay visible, reworded)

`M` = the winner's shares, `N` = the loser's own shares. Plain text: no `$`, no `**`. Scores via `score_text.composite_score_text(score, WEAK_CONVICTION_SCORE)`. "share/shares" follows the count.

- **Winner** (only when at least one loser was sized): append to `why` — "This sale also covers {parts}; none of them sells more than these {M} shares." Parts joined "X", "X and Y", "X, Y and Z": "the earnings-week trim to {target:.1f}% ({N} shares)", "the low-score trim to {8.0}% ({N} shares)", "the profit-lock trim of {25}% of the position ({N} shares)", "the {event} pre-event trim ({N} shares)". No suffix when the only other T items were unsized (SNOW: the Act card is byte-identical to today's).
- **Earnings item** (sized or unsized) → `{"type": "WATCH", "covered_by": T}`; headline, priority, `revision_flag_line` kept. Sized why: "Today's Act Today trim of {T} sells {M} shares, no fewer than the {N} shares a trim to {X:.1f}% would sell, so earnings week adds no separate trade." Unsized: "Today's Act Today trim of {T} sells {M} shares; the earnings-week rule sizes no trade of its own." Trigger = the `trimmed=True` text (T IS trimmed today); with a weak-large item on T it uses a new `reentry=False` variant that drops "Beat → re-enter post-print if composite holds;" (don't advise re-entering a name sold for a low score).
- **Weak-large item** (sized or unsized) → `{"type": "WATCH", "covered_by": T}`; icon 🔍 and ticker kept so `_render_journal_context` still renders (F-39c). Sized why: "Weight {w:.1f}% ≥ 10% AND score {s} < 55. Today's Act Today trim of {T} sells {M} shares, no fewer than the {N} shares a trim to 8.0% would sell, so no separate trim." Unsized: "… Today's Act Today trim of {T} sells {M} shares; this check sizes no trade of its own." Trigger: "Covered by today's Act Today trim of {T}."
- **Stop item** (a `TRIM_AND_TIGHTEN` loser, or any `TIGHTEN_ONLY` on T beside a winner) → `{"type": "TIGHTEN_ONLY", "new_stop": …}` with the existing under-reduce why: "Gap {gap:.1f}% above stop; P&L {pnl:+.1f}%. Today's Brief already says to reduce {T}, so this only tightens the stop on the shares you keep." Built by ONE helper that the `_under_reduce` branch (:3398-3406) also calls. Mark Done still renders.
- **Macro `PROTECTIVE_TRIM` loser** (U6-3 default: stand down) → `{"type": "WATCH", "from_exposure", "threshold", "covered_by": T}`, `trim_ticker` removed. Why: "Affected-sector exposure {X:.1f}% > 30% trim threshold. The pre-event trim would fall on {T} ({N} shares); today's Act Today trim of {T} sells {M} shares, no fewer, so no separate pre-event trim is sized." Trigger (playbook) unchanged; `_rewrite_macro_review_items` then lists T under "(1 affected name with its own Act Today call not listed)", which is true.
- **Covered WATCH label:** `_fmt_action` (app.py:1547-1551) prints "no action today — see Trigger below"; for an action with `covered_by` it prints the text from a new pure `brief_trim_merge.covered_action_text(action)`: "covered by today's Act Today trim of {T}; no separate trade." Render-only; Home cards (app.py:13035) and Summary rows (app.py:15573) both read it.

### 2.4 Where it lives

New gate module `stock_analyzer/brief_trim_merge.py`: `one_trim_per_name(items) -> list[dict]` (pure, never mutates input, output independent of input order, strips its own `_u6_*` carrier keys) and `covered_action_text(action) -> str | None`. Joins `_GATE_FILES` (106 at HEAD → 107; re-print to confirm) with a docs/review-policy.md row in the same commit.

`_review_list` carries the facts each section already has as `_u6_*` keys: section 1 the TIGHTEN_ONLY alternative (action + why from the shared helper); section 2 `_u6_trigger_trimmed` / `_u6_trigger_trimmed_no_reentry` + target / shares; section 3 `_u6_score`; section 5 event name and date. The merge runs once, right before the final-pass news dedup (:3947), inside try/except (fallback = unmerged).

`earnings_stop_cover.brief_earnings_trigger` (earnings_stop_cover.py:71, gate) gains keyword `reentry: bool = True`; with the default every current caller is byte-identical.

## 3. Interplay, verified

- **Sell sets unchanged.** `held_trim_owner.brief_sell_names_ticker` / `brief_sell_tickers` (held_trim_owner.py:48-83) and `view_sell_tickers` (:160-174) read `_ACT_REVIEW_TYPES` by `ticker` / `trim_ticker`; T keeps exactly one such item, so the Tune-up deferral (daily_briefing.py:4296-4304), 🔗 Risk Analysis and the ⚖️ Rebalancer deferral (app.py:22960-22999) see the same set. Pin with a test.
- **`reduce_call_tickers` unchanged; `reduce_call_items[T]` becomes the winner** (decision_bucket.py:211-232, first reduce per ticker; today it is whichever trim sorts first), so the 📈 Analysis banner quotes the largest sell. Set-keyed consumers (Opportunity Signals, Rebalancer ADD gate, My Edge) see no change. `_reduce_calls` is published at app.py:2975-2984.
- **`all_flagged_tickers` / `add_blocking_cards` unchanged as sets** (T still carries cards); Grow Today (incl. the cron's picks) unchanged. Pin with a test.
- **Counts:** `act_today_view.n_active` (act_today_view.py:211) drops by (Act trims on T − 1) per colliding name; the Home badge, Summary pill and dock digest (`home_dock_digest.py:99-102`) all read it, so they agree. Intended.
- **📡 Signals headline not affected** (reads `_actions_cache` = `portfolio.rebalance_actions`, app.py:7563-7566).
- **Other folds:** `_consolidate_act_today` touches the act lane only. `_reconcile_act` / `critical_news_as_risk` (decision_bucket.py:91-159): a critical-news card on T now folds its note onto the single winner (a WATCH loser can't receive it: `_is_reduce` is False for WATCH).
- **History (DoD #8): no table records Brief Review trims.** `rec_events_capture` rec_types are rebal_trim / beta_trim / diversify_add / single_name_concentration / sector_concentration; the `exit_signals` capture (app.py:8404-8423) and judgment opinions (app.py:8592-8604) read only `DETERIORATION_WATCH`; `recommendations` takes only new_pick / add_winner / enter_now / overflow_pick; `decision_context.act_today_n` counts `_actions_cache` (app.py:33755), not the Brief. No version marker needed.

## 4. Time context, offline, viewer

- No date logic in the merge; it acts on which items exist (E for days 0–7, macro for days 1–3, from the same ET `today`). Tests: day 0 / 7 / 8 (no E item → today's single W card unchanged), a Saturday visit with a Monday report (days = 2), macro on days 1 / 3. Day-0 "before the report" wording (#25) untouched; the new text never says "before the report".
- Late trade: a trade logged after the build bumps the epoch, the Brief rebuilds, the merge re-runs (one build = one freshness).
- Withheld score: W never fires (:3567-3576). Unread weight: `_f(NaN)` → 0.0, W off, E a WATCH (pre-existing overclaim, siblings). A bad `trim_shares` leaves T untouched (§2.2).
- No portfolio value / price: W becomes an unsized WATCH while E can still size (:3582 vs :3488); the merge rewords W's WATCH ("sizes no trade of its own"), true either way.
- Read-only viewer: no difference (build-time); losers have no 📒 Log button; Mark Done stays on the stop card under its existing viewer lock. Brief offline stub: `review_list` is `[]`, merge is a no-op.

## 5. Owner decisions (defaults marked)

- **U6-1 precedence. (a) DEFAULT:** the largest sell by shares wins (never less than any card today, never more than the largest). (b) Stricter target among target trims only; lock and macro stay separate (two sells remain). (c) Sum: over-sells against overlapping goals; not recommended.
- **U6-2 losers. (a) DEFAULT:** stay visible in Monitoring as reworded Watch / stop-only cards, plus one "also covers" sentence on the winner (keeps F-39c journal context, the earnings Trigger, the rating flag). (b) Drop them and fold into the winner's `why` (needs an app.py journal-context change; loses the Trigger / flag).
- **U6-3 macro pick already sold harder by another card. (a) DEFAULT:** stand down to Watch on that name (the exposure cut is already met; no extra selling). (b) Move the pre-event trim to the next affected name not already trimmed (more total selling; matches the act-lane rule at :3763-3772).
- **U6-4 EWSiblings (9)** (📡 Signals / 🔔 Catalyst Watch don't mention the Act weak-large trim). **(a) DEFAULT: a separate follow-up plan (U6b), sequenced right after U6** — cross-page, needs the verified-plus-fresh `_reduce_calls` / `act_today_view` idiom + an unverified caption (C3/C4 pattern), touches other gate files (`portfolio.py`, `earnings_advisor.py`) + app.py; U6 is one pure build-time pass with no freshness question. U6 makes (9) easier (`_reduce_calls[T]` becomes the one call to quote) and widens it (in the E + W case Signals / Catalyst say "to 10% (N)" while Act Today says "to 8% (M)"). (b) Fold it in.
- Also for the owner to veto: the earnings Trigger drops "Beat → re-enter post-print if composite holds" when the sale is a low-score trim (§2.3).

## 6. Chunks

- **C1 (decision-bearing; implementer; Opus review shared with C2):** new `stock_analyzer/brief_trim_merge.py`; the `_GATE_FILES` entry + review-policy.md row; `earnings_stop_cover.brief_earnings_trigger(..., reentry=True)`; tests `tests/test_brief_trim_merge.py` (hand-built items) + `tests/test_earnings_stop_cover.py` (default byte-identical, `reentry=False` variants). Pure-additive, not wired.
- **C2 (decision-bearing; implementer; one Opus round with C1; `feat(` with Design = planner):** `daily_briefing._review_list` (`_u6_*` carriers, the shared under-reduce helper, the merge call + fallback); app.py `_fmt_action` `covered_by` branch via `covered_action_text` (render-only); tests `tests/test_brief_one_trim_per_name.py` (integration), extended `tests/test_earnings_week_consistency.py`, app.py grep tests; any existing test pinning two Act trims on one subject changed deliberately and named.
- **C3 (docs; Opus lead):** requirements F-25 / F-25a (+ a new rule ID if chosen; verify the next free one); architecture Brief review-list section; User Guide Act Today / Monitoring bullet; bug register (EW row "Open from it: U6" → fixed; EWSiblings (9) note widened); CLAUDE.md queue; shipped-log; this plan's and the parent's Status; a memory note on the share-count rule. No constants-table change.
- **Live check after the C2 push (laptop):** SNOW while in its window (11-25 → 12-02): Act Today keeps the 8% trim unchanged; the 📅 Monitoring card reads "Watch: covered by today's Act Today trim of SNOW; no separate trade."; its why has no "sizes nothing", no "standing work", no "no pre-event action/trim"; its Trigger has no "re-enter"; no literal `**`; `$` as text.

**Interim (before C2 ships):** from 11-25 the Act Today weak-large trim to 8% is SNOW's call; the 📅 earnings card's "sizes nothing … standing work" wording (and its Trigger if it reads "no pre-event action/trim") is the defect.

## 7. Tests the build must include

1. **Pair and triple matrix through the real `_review_list`:** E sized at 10%, E standing at 15%, E WATCH, W sized, W WATCH (pv = 0), lock, TIGHTEN_ONLY, macro, macro × 2. Assert exactly one `_ACT_REVIEW_TYPES` item per subject; its `trim_shares` == max over the standalone sections (pre-merge list via a monkeypatched identity merge); the winner's action dict equals its standalone dict.
2. **Seeded property test:** w ∈ [5, 30], score ∈ {40, 54.9, 55, 70, withheld}, gap ∈ {None, 2.9, 3.0, 3.1, 8.0}, P&L ∈ {7.9, 8, 24.9, 25, 40}, days ∈ {−1, 0, 3, 7, 8}, macro on/off, fractional shares (37.5). Assert at most one Act trim per subject; winner ≥ every pre-merge sized trim and ≤ their max; `brief_sell_tickers`, `reduce_call_tickers`, `all_flagged_tickers(…, ignore_news=True)` and `add_blocking_cards` keys unchanged; no item removed; the act lane untouched.
3. **Crossovers and tie-breaks** (100 shares): W vs lock at w = 10.6 (W 24 < lock 25 → lock), 10.7 / 10.8 (tie 25 → lock), 11.0 (W 27 wins); W vs macro at w ≈ 13; E vs lock at 13.33; E vs W ties at tiny share counts (W wins); boundaries w = 10.0 / 9.9, score 54.9 / 55, gap 3.0 / 3.1, P&L 25.0 / 24.9, N=8 w = 17.5 / 17.6.
4. **No collision → no change:** with no same-subject pair, `_review_list` output deep-equals the identity-merge output (no `_u6_*` key leaks); a single sized trim is never altered.
5. **Fail-safe:** an Act item with `trim_shares` None / NaN / 0 / "3" leaves T untouched; a raising merge returns the unmerged list (monkeypatch).
6. **Order independence:** shuffled input → same output; inputs not mutated.
7. **Wording:** every template pinned; numbers in the text equal the action fields; no item on a subject with an Act trim contains "sizes nothing", "already conservative", "no pre-event action", "no pre-event trim", "within tolerance", "position still has room" or "only the stop is tightened"; with W on T, no "re-enter"; no `$`, no `**`; score text via `composite_score_text` (54.95 never prints "55 < 55").
8. **Time context:** days 0 / 7 / 8, Saturday visit with a Monday report, macro on days 1 / 3, "today" passed explicitly as an ET date at 21:00 ET.
9. **Coordination through `build_daily_briefing` end to end:** `split_defensive` per-subject Act count == 1; `act_today_view.n_active` drops exactly by the collisions; `reduce_call_items[T]` is the winner; `portfolio_tuneup_deferred` keys unchanged; a critical-news card on T folds onto the winner; `_rewrite_macro_review_items` lists T as "not listed" after a macro stand-down.
10. **EW matrix variant:** score 50 in `tests/test_earnings_week_consistency.py`; S1's 📅 item is WATCH `covered_by` whenever W sized; the Act trim for AAA is ≥ the decision's shares.
11. **app.py grep tests:** `_fmt_action` routes `covered_by` through `brief_trim_merge.covered_action_text`; no new comparison against a constants value in app.py.

## 8. Risks, coordination, siblings (fix the class)

- Calm posture: Act Today loses duplicates; Monitoring gains at most the reworded losers for that name (where no-trade items belong).
- Attribution tickers: the merge never reads the act lane, so a `risk` card's attribution ticker can't drive it.
- Siblings, named and not fixed here: the weak-large headline "largest weak holding" fires for every weak large holding (daily_briefing.py:3644, :3659), so does the sell_signal fold "Also your largest weak holding" (:3614); the W why prints `{score:.0f}` (:3670) so 54.5–54.99 reads "55 < 55"; E reads a NaN weight as "0.0% within tolerance" (:3474); `is_critical = gap <= 3.0` is a bare literal (:3369); a `DETERIORATION_WATCH` "Not an action yet" (:3304-3317) can sit beside a Review trim on the same name (persisted to `exit_signals` as WATCH, so only ever reword it, never change its type); the `add_blocking_cards` label is the first card per ticker and can read "Monitoring · …" for a name under an Act trim (decision_bucket.py:404-443, display only); W's sizing needs portfolio value > 0 and price > 0 while E's doesn't (:3582 vs :3488-3490); EWSiblings (9) becomes U6b.

Files (verified at HEAD by the planner): `stock_analyzer/daily_briefing.py` (`_review_list` :3240-3974, `build_daily_briefing` :4078-4320), `stock_analyzer/decision_bucket.py` (:36, :122-232, :404-443), `stock_analyzer/held_trim_owner.py` (:48-83, :160-174), `stock_analyzer/act_today_view.py` (:123-218), `stock_analyzer/trim_sizing.py` (:78-104, :166-195), `stock_analyzer/earnings_stop_cover.py` (:71-122), app.py (`_fmt_action` :1541-1584, review card :13022-13210, `_publish_brief_coord_caches` :2975-2984, Rebalancer cross-check :22960-23009), `stock_analyzer/constants.py`, `tests/test_earnings_week_consistency.py` (matrix uses score 70, so no W; unaffected as written). The parent plan's S8/S9 line numbers are stale (weak-large now :3561-3675, the stop profit-lock :3407-3419, macro PROTECTIVE_TRIM :3873-3895).
