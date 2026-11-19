# C4 spec: monthly Decision Review on the shared entry-call rule

**Status:** SPEC 2026-11-18 (Opus planner at HEAD `6c992ff`, verdict PROCEED WITH CHANGES). Parent plan: `docs/plans/acted-rule-and-report-periods.md` (U1–U15; C2 decisions (a)–(d); C1 carry-forwards (a)–(d)). Sibling spec: `docs/plans/acted-rule-c3-spec.md`. **Owner answered 2026-11-18, all as recommended:** UD-1 (B) split by figure (engine figures at the call-day price, the owner's figures at the fill); UD-2 (A) still-open calls listed by name, counted nowhere, not revisited next month; UD-3 app-written bullets at the end of Signal discipline + counts in the tile caption and email footnote; UD-4 and UD-5 accepted.
- **C4a BUILT 2026-11-18** (implementer; new `monthly_entry_calls.py`, `_GATE_FILES` 99, `entry_call_action.arm()`, `behavioural_rows` on `arm()`; 62 new tests). **Opus SHIP, 0 blocking**; all 14 implementer deviations accepted (e.g. a refusal returns `counts: None`; "N trading days" via `_days_phrase`); the engine view keeps credit-deduped rows (per-surfacing band semantics; one observation per (ticker, date) would be an owner / planner call applied whether or not bought). **C4b carry-forwards (non-blocking):** (1) `build_month` guard `as_of >= period_end` and `period_start <= period_end` (raise ValueError); (2) the older-rule wording in `rule_note` / `acted_rule_caption` should say "a same-day trade tagged as a recommendation" (the old rule also credited a SELL and any surfacing); (3) skip already-held `enter_now` rows before counting `n_undated` (unreachable today); (4) harmonise "New position" / "New Position" capitalisation across bullets, `rule_note` and `scope_words`; (5) `n_graded` / `q0_ready` count surfacings (incl. same-day cross-type pairs and still-open calls): say "surfacings" in the prompt and band caption; (6) the live fallback must not append `signal_lines` / `rule_note` bullets to the stored v1 text: only Generate freezes them.
- NEXT: commit C4a, then C4b + C4c. Order: C4a (implementer → Opus reviewer), then C4b + C4c in one commit (implementer → Opus reviewer). Pushed by ~2026-12-01; the first real run is the Sun 2026-12-06 email (November 1–30).

**Planner's change to the parent plan:** the plan's C4 line says "`n_open` in `signal_flow` / `report_viz_snapshot`". Don't: `signal_flow` is shared with 📜 Recommendations History (app.py:36199). Filter the rows in a new gate module and add the still-open / not-judged block to the monthly's own `viz_json`. Every `rec_types=("new_pick",)` default stays as it is; the monthly passes `ENTRY_CALL_REC_TYPES` explicitly.

## Owner decisions (to answer)
- **UD-1. Which price grades an acted row** (today `compute_outcomes` uses the fill; at N = 2 the fill can come up to 2 sessions after the call).
  - (A) Fill price everywhere, as today, disclosed. This is the plan §4 default. The band chart then carries your timing.
  - **(B) Recommended.** Engine figures (band chart, per-band and per-verdict "engine alpha" in the prompt, `q0_ready`, `n_graded`) price every call from its call-day `price_at_surface`, whether or not you bought. Your figures (Bought tile = `engine_alpha_pct`, acted vs skipped averages, best / worst, Sankey outcome labels) use the fill price.
    - SPY stays measured from the call date, as today, and this is disclosed. Measuring SPY from the fill date needs a `compute_outcomes` option; that's for Phase B, not C4.
    - Why: the band chart is the one figure the report says can justify reviewing `COMPOSITE_BUY`. Under the new rule more acted rows are lagged fills, so (A) would feed your timing into a policy-review input.
  - (C) Call-day price everywhere. "Did acting help" then measures selection only, not your result.
- **UD-2. Calls still open at the month end** (window ends after `period_end`).
  - **(A) Recommended.** Listed by name in the month they were made. Never counted as bought or skipped, never in the Sankey, the tiles, the action rate, the skipped bar or the skipped average. They stay in the engine view (UD-1 B), because the engine made the call. Next month's report doesn't revisit them; the weekly debrief's "Carried over" block resolves them.
    - Fri 11-27 (half day) → window ends Tue 12-01: open in November.
    - Mon 11-30 → Wed 12-02: open.
    - Wed 11-25 → Mon 11-30 (11-26 is a holiday): judged inside November.
  - (B) Next month adds an uncounted "Resolved from last month" line. Needs last month's recs loaded and its own by-name rule; likely slips past 12-01.
  - (C) Counted in next month's figures, like weekly UD-1. Breaks "names surfaced this period".
- **UD-3. Where still-open / not-judged names appear.** **Recommended:** app-written bullets appended at the END of the Signal discipline text. They are frozen in the row, so they show in the app, the preview and the email. The counts also go in the tile caption and the email footnote. Option: caption / footnote only.
- **UD-4. Accept.** The "acted via ANY surfacing" safeguard (`intelligence_report.py` :215-222; the `signal_flow` / `distinct_missed` docstrings) is retired for the monthly. An entry call counts as acted only by a BUY in its own window.
  - Example: a `new_pick` on 11-02 bought on 11-12 under a `buy_candidate` surfacing now reads not acted.
  - Excluded rows (`buy_candidate`, `overflow_pick`, already-held `enter_now`) confer nothing. This is a consequence of U2–U4.
- **UD-5. Accept.** A name whose earlier call was not acted and whose latest call is still open reads **still open** (`per_name` precedence acted > still_open > unknown > not_acted, the same as the weekly). It is out of both counts that month. Option: count it skipped on its closed calls and list the open call separately.

## 1. Today at HEAD (`6c992ff`)
- `intelligence_report.build_report_package` :134 (not a gate file):
  - `match_recs_to_trades` (same UTC day, RECOMMENDATION trigger only) → `compute_outcomes(today=as_of)` (:223-227).
  - `report_viz_snapshot(enriched_all, rec_types=("new_pick",))` (:236); the default `rec_types` is at :143.
  - `skipped_names_avg_alpha` (:260). `summary_stats` / `by_composite_band` / `by_verdict` run on `scoped` (:253-273).
  - `has_data = flow.n_total > 0` (:240).
- `summary_stats` treats `not acted_on` as missed (:690-691). `signal_flow` (:926), `distinct_missed` (:827) and `skipped_names_avg_alpha` (:990) build `acted_tickers` from ANY row's `acted_on`. So `apply_to_matched` output (still-open / unknown / credit-deduped rows have `acted_on=False`; excluded rows keep the OLD credit) cannot be passed to them unfiltered.
- `missed_opportunity.build_missed_opportunity_corpus` (:97) and `collapse_recs_by_ticker` (:1175, :1322) are **not on the monthly path** (Recommendations History app.py:36338; `engine_trust_headline`; `predictive_analytics` :150). They go to Phase B.
- Prompt: `_system_prompt` :41 ("Acted on means the investor actually traded the name"); `_format_prompt` :294 (the SCOPE paragraph says New Positions only; the `as_of` line says "(the run date)" unconditionally (C1 carry-forward (a)); count discipline is on `n_total`).
- Readers:
  - cron `_run_monthly_report` :3130 (recs :3169 loaded `period_start..period_end`; strict trades :3170; package :3253; quiet skip on `has_data` False :3266; email :3287).
  - app monthly branch :43890-44311 (Generate :43941-44063, package :44023, preview store :44039-44048; frozen / live viz :44116-44188; tiles :44201-44223; Sankey :44228; bands :44254; missed bar :44281-44311).
  - `_rpt_emailed_preview` :42548.
  - `notify.render_intelligence_email` :1708.
  - `report_display.skipped_avg_state` / `monthly_tiles_caption_suffix`.
- The live fallback (:44129-44188) uses lenient `load_recommendations` (:44140), same-day `_mrm` and `rec_types=("new_pick",)` (:44185).
- `recommendations.already_held` arrives in recs (`select("*")`), so `apply_to_matched`'s lookup works.

## 2. Changes per file

### A. `entry_call_action.py` (C4a, gate): add `arm(row) -> "acted" | "passed" | None`
The C4 caller contract as code:
- `entry_call_status == "acted"` and not `credit_deduped` → "acted"
- `== "not_acted"` → "passed"
- anything else → None.

Docstring: "never split on `acted_on` / `rec_type`". `debrief_entry_calls.behavioural_rows` (:528-539) uses it with no behaviour change; the existing tests pin that. (Fix the class: one contract, one implementation.)

### B. New pure `stock_analyzer/monthly_entry_calls.py` (C4a; `_GATE_FILES` 98 → 99; review-policy line)
No Streamlit, DB or clock. Call recommendations_history through the module (`from stock_analyzer import recommendations_history as rh`; `rh.compute_outcomes(...)`) so the tests can wrap it.

`build_month(recs_df, trades_df, *, period_start, period_end, as_of, current_prices, spy_close_by_date, min_days, window_trading_days=None) -> dict`:
```
{judge_state: "ok"|"recs_unavailable"|"recs_unreadable"|"trades_unavailable"|"trades_unreadable",
 counts: {n_surfaced, n_acted, n_not_acted, n_open, n_unknown, n_undated},
 names: [ {ticker, status, n_calls, rec_types, last_call_date, window_end, reason} ],
 judged: [enriched rows],  engine: [enriched rows],  viz: dict|None}
```
1. **States.**
   - recs None → `recs_unavailable`.
   - A non-empty frame without `ticker` / `rec_date` / `rec_type` → `recs_unreadable`.
   - Journal state from `eca._prepare_journal(trades_df)[0]` (as `weekly_entry_calls` does) → `trades_unavailable` / `trades_unreadable`.
   - Any non-ok state: no counts, `judged` / `engine` = [], `viz` = None. **Never "all missed".**
   - An empty recs frame → ok with zeros.
2. `matched = rh.match_recs_to_trades(recs_df, trades_df)`. Used only as the row shape; the function itself is unchanged.
3. Keep rows with `rec_type in eca.ENTRY_CALL_REC_TYPES` and `period_start <= rec_date <= period_end`. An unreadable `rec_date` adds to `n_undated` and the row is dropped. A call after `period_end` is never passed on (C2 carry-forward).
4. `rows = eca.apply_to_matched(kept, recs_df, trades_df, cutoff=period_end)`. **The cutoff is `period_end`, never `as_of`** (U6). Drop `entry_call_status == "excluded"` (U8).
5. **Names.** `eca.per_name` over an adapter of `rows` minus `credit_deduped` rows. The adapter maps `{ticker, status=entry_call_status, rec_date, window_end=entry_call_window_end, rec_type, id, matched_trade=acted_trade, matched_trade_date=None, confirmed=False}`. Dropping deduped rows is safe because their (ticker, date) winner is acted. Without the drop, `per_name` can pick a deduped row and crash on `matched_trade["id"]`. An unknown name's `reason` = the latest unknown row's `entry_call_reason`.
6. **Judged (your view).** Rows with `arm(row)` set whose name status is acted or not_acted, with `acted_on = (arm == "acted")`, then `rh.compute_outcomes(..., today=as_of, min_days=min_days)`.
7. **Engine view.** Every row from step 4 (incl. still_open, unknown, credit_deduped).
   - UD-1 B: `compute_outcomes([dict(r, acted_on=False, acted_trade=None) for r in rows])`, i.e. the call-day price.
   - UD-1 A: `compute_outcomes(rows)`.
8. **Counts.**
   - `n_surfaced` = len(names).
   - `n_acted` / `n_not_acted` / `n_open` / `n_unknown` by name status.
   - `flow.n_acted` must equal `n_acted` (pinned by a test).
9. **viz.**
   - Start from `rh.report_viz_snapshot(judged, rec_types=eca.ENTRY_CALL_REC_TYPES)` (flow / missed / missed_split from your view).
   - Replace `bands` with `[{band, avg_alpha} for b in rh.by_composite_band(engine) if avg_alpha is not None]`.
   - `avg_skipped_names_alpha = rh.skipped_names_avg_alpha(judged, eca.ENTRY_CALL_REC_TYPES)` (always present when ok).
   - `as_of`; `acted_rule_version = eca.ACTED_RULE_VERSION`; `window_trading_days`.
   - `entry = {rec_types, n_surfaced, n_open, n_unknown, n_undated, price_basis: "call_day"|"fill", open_names: [{ticker, rec_types, n_calls, last_call_date ISO, window_end ISO}], unknown_names: [{ticker, rec_types, n_calls, last_call_date ISO, reason}]}`.
   - JSON-safe: ISO strings, plain ints, ids via `debrief_entry_calls._clean_id`.

`band_rows(engine, judged)` / `verdict_rows(engine, judged)`: `rh.by_composite_band` / `rh.by_verdict` over `engine` (n_total, n_priced, avg_alpha). `action_rate` is taken from the same-label bucket of the same function over `judged` (None when that bucket has no judged rows), so `_band_line`'s "acted X%" never counts a still-open call.

`signal_lines(viz) -> list[str]` (UD-3). Plain text with no `**`, `*`, `_` or `$`. Reuse `debrief_entry_calls._fmt_date`, `_type_label`, `_days_phrase` and `unknown_reason_text`; never copy them. Returns [] for a non-v2 viz.
- `Still open at the month's end (not counted as bought or skipped):` then `• NVDA: Enter Now call on Fri Nov 27. Decision window ends Tue Dec 1.` For a name with several calls: `… surfaced 3× this month, last on Fri Nov 27. …`
- `Not judged (not counted as bought or skipped):` then `• XYZ: New position call on Mon Nov 9. Not judged: a buy of this name has a date that couldn't be read.`
- When `n_undated` > 0: `• N entry call row(s) had no readable date and are not counted.`
- Empty when there is nothing to list.

`rule_note(viz) -> str | None`. None unless `viz.acted_rule_version >= 2`. Otherwise:
- `Acted on = a buy of that name on the call day or within N trading day(s) after it (New Position, Add to Winner and Enter Now calls; an Enter Now on a name you already held is left out). O still open at the month's end and U not judged are in neither count.`
- + (UD-1 B) `The band chart prices every call from its call-day price; the bought average uses your fill price, up to N trading days after the call.`
- + (U1) `📜 Recommendations History and the other pages that count acted calls still use the older rule (a same-day buy tagged as a recommendation), so their counts can differ.`
- N is always read from the constant. C6 deletes the U1 sentence.

`acted_rule_caption(viz, *, live: bool) -> str | None`. None for a frozen v2 viz.
- Frozen and missing / NaN / 1 / unreadable: `This report used the older acted rule: only a buy tagged as a recommendation on the call day counted as acted, and only New Position calls were counted. Newer reports count a buy on the call day or within N trading day(s), and include Add to Winner and Enter Now calls.`
- `live=True`: `Counts and charts were recomputed today with the current acted rule (a buy on the call day or within N trading day(s)). The text below and the bought average were saved when the report was generated, under the older rule, so they can differ.` This closes C1 carry-forward (d) on the wording side.

`scope_words(viz) -> dict` gives the version-aware UI phrases:
- v2: `names` = "distinct entry-call tickers (New Position, Add to Winner, Enter Now)"; `flow_title` = "This month's signal flow — entry calls → bought / skipped → outcome"; `missed_scope` = "entry calls (New Position, Add to Winner, Enter Now)".
- Older rows: today's New-Position wording.
- The bar caption's page name becomes "📜 Recommendations History" (it reads "📊" at HEAD).

### C. `intelligence_report.py` (C4b)
- `build_report_package`: remove the `rec_types` parameter (no caller passes it); `rec_scope` = ", ".join(ENTRY_CALL_REC_TYPES). Call `build_month(...)`.
  - Non-ok `judge_state` → package `judge_state`, `has_data=False`, nothing else filled in.
  - `has_data = n_surfaced > 0`.
  - `n_total` = judged names (= flow.n_total); new `n_surfaced`, `n_open`, `n_unknown`, `open_names`, `unknown_names`, `window_trading_days`.
  - `action_rate` = n_acted / (n_acted + n_missed), None when 0.
  - `summary_stats(judged)` → acted / missed means, best / worst, `engine_alpha_pct` (= avg_acted_alpha, your view).
  - `n_graded` / `q0_ready` from `summary_stats(engine)["n_priced"]`.
  - `band_rows` / `verdict_rows` from B.
  - `viz` from `build_month`. Weekly trajectory unchanged.
- `_system_prompt`:
  - Interpolate N from the constant at call time, as is done for `COMPOSITE_BUY`.
  - Replace the acted bullet with: `"Acted on" means the investor BOUGHT the name on the call day or within N trading day(s) after it (a sell never counts); "not acted on" (skipped) means no buy in that window. A call whose window runs past the period end is "still open"; a call the app couldn't judge is "not judged". Neither is ever counted as acted on or skipped, and you must never call them either.`
  - The first background bullet keeps the `COMPOSITE_BUY` sentence scoped to New Position calls, and adds that entry calls are New Position, Add to Winner and Enter Now. The reviewer checks every type claim against code.
- `_format_prompt`:
  - `as_of` line only when `as_of != period_end`: `Outcomes are measured to {as_of}, not to the period end.` (carry-forward (a)).
  - SCOPE: the three types and the already-held exclusion; awareness feed excluded.
  - Counts: `Distinct names with an entry call this period: S. Acted on A, not acted on M; O still open at the period end (decision window runs past {period_end}); U not judged. Every count is a DISTINCT ticker; these are the only name counts that exist.`
  - Count discipline on S (never state a count > S). `Action rate = acted ÷ (acted + not acted); still-open and not-judged names are not in it.`
  - `Still open at the period end (already listed by the app under Signal discipline; never call them skipped or acted on): NVDA (window ends 2026-12-01), …`; the same for not judged, with plain reason words.
  - UD-1 B: `Band and verdict engine alpha price every call from its call-day price, whether or not you bought; the acted averages use your fill price, up to N trading days after the call.`
  - Read every new key with `.get` and a default (the golden case is updated anyway).
- `generate_report`: after parsing, `section_signal_discipline = ai_text + "\n\n" + "\n".join(signal_lines(viz))` when there are lines (AI text first; lines alone if the AI section is empty). `viz_json` already carries `acted_rule_version`. No DDL, no `METRICS_VERSION` bump, stored rows never rewritten.
- Docstrings: :215-222 and `report_viz_snapshot`'s safeguard paragraph get a note that the monthly now passes only judged rows (comment only).

### D. `cron_runner._run_monthly_report` (C4b, gate)
- After the package: `if package.get("judge_state") != "ok"` → `_LAST_LANE_FAILURE_DETAIL = f"monthly_unjudgeable: {state}"`, notify, return 1. A missing key reads as not-ok (fail closed); update the test mocks.
- Log: `monthly: S name(s) · acted=A · skipped=M · open=O · unknown=U · graded=G · q0_ready=… · engine_alpha=… · rule=v2 cutoff={period_end}`.
- Loads unchanged under UD-2 A.

### E. `notify.render_intelligence_email` (C4b)
- Under the metric strip, a footnote `<div>` with `safe_html(rule_note(viz_json))` when it is not None.
- The still-open bullets arrive via Signal discipline (`_email_section`, one `<li>` per bullet).

### F. `report_periods.py` (C4b): carry-forwards (b), (c)
- `was_replaced(row)`: `email_sent` falsy and `email_sent_at` set.
- `preview_banner(kind, noun, period, when_text, replaced) -> str`:
  - unchecked: `Couldn't check whether the {noun} for {period} was already emailed, so nothing was saved. The regenerated version is shown below as a preview{when}.`
  - emailed: today's text.
  - emailed and replaced: `Preview only{when}: an earlier version of the {noun} for {period} was emailed; the saved version is kept. The regenerated version below is NOT saved.`
- Both Generate paths (weekly :43646, monthly :44041) store `"replaced": _rp.was_replaced(found_row)`; `_rpt_emailed_preview` renders `preview_banner(...)`.

### G. app.py (C4b, render-only; no constant comparison)
- **Frozen view.**
  - `acted_rule_caption(viz, live=False)` under the period caption.
  - Tile caption = `scope_words` names + the existing suffix + `rule_note(viz)` when set.
  - Sankey title / caption, missed-bar caption and the "New Positions" strings from `scope_words`.
  - UD-1 B band caption adds "Each call is measured from its call-day price, whether or not you bought it." (only when `entry.price_basis == "call_day"`).
- **Live fallback** (:44129-44188): `load_recommendations_or_none`; on None → `st.error("Could not read recommendation history, so this report's visuals can't be rebuilt right now.")`.
  - Viz from `monthly_entry_calls.build_month(..., period_end=row's period_end, as_of=today_et())["viz"]`.
  - A non-ok state → the existing "Could not read your trade journal" error.
  - `acted_rule_caption(None, live=True)`.
  - The bought tile keeps the stored `engine_alpha_pct` (the caption says so); skipped stays "not saved" (polish rule).
- **Generate:** `judge_state != "ok"` → st.error, nothing saved.
- **User Guide:** app.py ~46064 (monthly bullet) and ~42630 (guide bullet): the acted rule with N from the constant, still open, the three types, the call-day price basis (UD-1 B), and the Recommendations History difference.

## 3. Offline behaviour
- Cron: recs / trades None → the lane already fails at :3172; `judge_state` non-ok → fails (new). An empty recs frame → quiet skip (unchanged).
- App Generate: the existing errors, plus the new `judge_state` error. Live fallback: strict reads, errors shown, never "all skipped".
- A per-name unknown (`trade_date_unreadable`) doesn't refuse; it is listed under "Not judged".

## 4. Risks / coordination
- Two acted figures are on screen until Phase B (C6); the U1 sentence in `rule_note` is required on app and email.
- The cutoff is the period end, not the run date. On 12-06 a call from 11-27 bought 12-01 still reads "still open". That is by design (U6), and it is the boundary most likely to be "fixed" wrongly later.
- Per-surfacing acted multiplicity rises: one BUY can credit calls on up to N+1 consecutive dates. That is correct under the rule. The Bought average stays per-surfacing, matching the Skipped tile.
- UD-1 B: an engine row with no `price_at_surface` goes ungraded (`no_entry_reference`) where the fill would have graded it. All three writers set it (app.py:7455 / :7489, `recommendations_history` :157).
- `report_viz_snapshot` keeps its "matches Recommendations History" promise only for v1 rows. Comment it.
- No session_state caches are touched; nothing to add to `coord_freshness`.
- The existing `test_intelligence_report` fake-pipeline tests (`_fake_pipeline`, :123-224) monkeypatch rh internals around a string "matched"; rewrite them on the real pipeline. `_real_pipeline_package` (:448) is a call 2 days before period end, so it is still open. Its `compute_outcomes` wrapper sees two calls under UD-1 B: assert on the judged or engine call explicitly.

## 5. Time-context matrix (each a test)
- **Thanksgiving / half day:**
  - Tue 11-24 call, BUY Fri 11-27 → acted.
  - Wed 11-25 call: BUY Mon 11-30 → acted; no BUY → not acted (window ends 11-30 = cutoff).
  - Fri 11-27 call → still open (window ends Tue 12-01) even with a BUY 12-01 and `as_of` 12-06.
  - Mon 11-30 call → still open (window ends Wed 12-02).
- **Year end:** Thu 12-31 call → still open (window ends Tue 2027-01-05) in December's report, generated Sun 2027-01-03.
- **DST:** Fri 10-30 call → still open in the October report (window ends Tue 11-03); never in November's (outside the period, UD-2 A).
- **After hours / late log:**
  - A 20:30 ET BUY dates on its ET day.
  - A provenance midnight-UTC import is re-anchored.
  - A BUY logged late but dated in the window → acted.
  - A SELL in the window never credits.
  - A BUY after `period_end` never credits.
- **Live cases:**
  - MRVL October (calls 10-06 and 10-07; BUYs 10-13, 10-14, 10-15) → not acted at N = 2.
  - NVDA `enter_now` Fri 11-06 (`already_held` false), BUY Mon 11-09 → acted in November.
  - MRVL 11-06 → acted via #479 (earlier than #480), never #494 (11-13).
- **Regenerate:** the same inputs give an identical `build_month` result. A different `as_of` gives an identical `entry` block and flow `n_total` / `n_acted` / `n_missed`. An emailed row → preview (U14).

## 6. Tests
- **New `tests/test_monthly_entry_calls.py`:**
  - every §5 case;
  - the C2 carry-forward case: `add_winner` + already-held `enter_now`, same ticker and day, + a same-day RECOMMENDATION BUY. The name is acted via `add_winner`; the excluded row is in neither `judged` nor `engine`.
  - leak case: an already-held `enter_now` with old same-day credit + a `new_pick` 10 days earlier with no BUY in its window → the name reads not acted;
  - credit-deduped rows never in `judged` and never crash the name roll-up;
  - a call after `period_end` in `recs_df` is not judged and doesn't turn a name unknown;
  - UD-5 precedence (not_acted + still_open → still open);
  - `flow.n_acted == counts.n_acted`;
  - unknown (`trade_date_unreadable`) listed with its reason;
  - each of the five `judge_state` refusals (no counts, viz None);
  - a pd.NA id;
  - UD-1 B: an acted row's engine `alpha_pct` uses `price_at_surface` while its judged `alpha_pct` uses the fill; `band_rows` `action_rate` excludes still-open rows;
  - viz `json.dumps` round-trip;
  - exact `signal_lines` strings, with no `**` `*` `_` `$`;
  - `rule_note` / `acted_rule_caption` for viz None, a missing key, NaN, 1, 2, "2", and `live=True`;
  - N read from the constant (monkeypatch it to 3 and the text follows);
  - lines through `notify._email_section` and `report_display.report_markdown`.
- **`tests/test_entry_call_action.py`:** `arm()` for each status plus credit_deduped. **`tests/test_debrief_entry_calls.py`:** `behavioural_rows` unchanged.
- **C4b:**
  - `test_intelligence_report.py` rewrites: real pipeline; `has_data` on `n_surfaced`; `judge_state` refusal for trades None and for unreadable trades; prompt counts / still-open line / no "missed" for open names; `as_of` line only when it differs from period_end; the Python lines appended to Signal discipline; `viz_json.acted_rule_version == 2`.
  - `tests/test_cron_debrief_monthly_date_anchor.py`: mocks carry `judge_state` "ok"; a non-ok package → rc 1, no save, no email; the cutoff passed is `period_end`, not `as_of`.
  - `tests/test_notify.py`: footnote present for v2, absent for older rows, escaped.
  - `tests/test_report_periods.py`: `was_replaced`, `preview_banner` for all three variants.
  - Wiring test (grep / AST): the app live fallback calls `build_month`, not `match_recs_to_trades` / `report_viz_snapshot` directly.
- **Golden:** update `case_intelligence_report` (add `n_surfaced` / `n_open` / `n_unknown` / `open_names` / `window_trading_days`, `as_of` != period_end). Then run `LLM_GOLDEN_REGEN=1 python -m pytest tests/test_llm_request_golden.py -q`; only `intelligence_report.generate_report#0` should change.

## 7. Sibling sites at HEAD (fix the class; name these in the C4b commit body)
**Change in C4:**
- `intelligence_report.py`:143 (default `rec_types`), :223-273;
- app.py:44140 (lenient recs read) and :44177-44185 (same-day + new_pick live fallback);
- app.py:44306 caption ("📊" → "📜").

**Phase B (C6):**
- app.py:32115 (Trade Journal live fingerprint mirror);
- app.py:33607-33624 (💸 Opportunity Cost: new_pick, same-day, naive `date.today()`);
- app.py:36199 / :36227 / :36338 (Recommendations History flow, missed, O1 corpus);
- app.py:38142 (🧬 Fingerprint coverage);
- app.py:50989 (My Edge fingerprint, was ~50813);
- `recommendations_history.engine_trust_headline` :1322;
- `predictive_analytics` :150 (Engine Trust by Band);
- `investigator.py`:257.

**Keep:**
- app.py:7696 (Grow Today steady note);
- app.py:37718 / :37787 (Entry Timing, not an acted filter);
- app.py:51129 (was ~50953) + `personalized_discovery.py`:75 (same-day join by design);
- `scripts/offense_attribution.py`:308 / :382 / :484 and `scripts/predictive_analytics_audit.py`:333 / :410 / :547 (research scripts).

**Parent-plan corrections:**
- "n_open in signal_flow / report_viz_snapshot" → put it in the new module's `viz.entry` instead.
- C7's "F-3 / F-4" → requirements F-152 / F-153.
- The sibling line numbers listed under Phase B above.
- The monthly live fallback's lenient `load_recommendations` (app.py:44140) is an offline collapse C1 missed; fixed in C4b.

## 8. Docs (C4b commit, lead)
- requirements F-153 "UPDATED (C4)" paragraph;
- architecture §6.11 (the `viz_json` keys `acted_rule_version`, `window_trading_days`, `entry.*`; bands price basis), the module tree, and the F-4 row (~4520, "Sonnet 4.6": verify against `llm.ROLE_DEEP`);
- review-policy gate reason for `monthly_entry_calls.py`;
- bug register: MRVLActed monthly leg FIXED; MonthlyFallbackCost caption leg fixed (the cache leg stays open); add ActedRuleSiblings with §7's Phase B list;
- User Guide (implementer);
- parent plan Status; CLAUDE.md DebriefEnterNow line; shipped-log.

## 9. Split and timeline
- **C4a** (implementer → Opus reviewer), by ~11-24: §2A–B plus tests; `_GATE_FILES` / review-policy (lead). Unwired.
- **C4b + C4c** (implementer → Opus reviewer), by ~11-29: §2C–G, the test rewrites, the golden regen, §8.
- **Live checks after the deploy:**
  - Generate October 2026. It saves a v2 row if the calendar row wasn't emailed, else a preview. Expect MRVL counted as skipped, the v2 captions and no literal `**`.
  - From Tue 12-01, Generate November: NVDA counted as bought; any 11-27 / 11-30 calls under "Still open"; the U1 note.
  - Sun 12-06: the email footnote and Signal discipline bullets.
  - Older archived rows show the older-rule caption.
