# C3 spec: weekly debrief on the shared entry-call rule

**Status:** SPEC 2026-11-18 (Opus planner at HEAD `50d3731`, verdict PROCEED WITH CHANGES; owner accepted UD-1 to UD-5 as recommended, 2026-11-18). Parent plan: `docs/plans/acted-rule-and-report-periods.md` (U1-U15, C2 decisions (a)-(d)). U15 DDL applied 2026-11-18. NEXT: C3a (implementer → Opus reviewer), then C3b, then C3c; push C3a + C3b together, ideally before the Sun 2026-11-22 debrief cron.

## Owner decisions (2026-11-18)
- **UD-1 YES.** Carried-over names count in the follow-through share of the week that resolves them, labelled "including N carried over".
- **UD-2 entry only.** The follower share covers entry signals only; protective responses are narrated separately.
- **UD-3 keep 80% as prompt text.** No new constant in C3.
- **UD-4 accept.** A call judged not acted in an emailed debrief whose BUY is logged later is not revisited (regenerate → preview → Replace still works).
- **UD-5 YES.** Strip AI-written entry bullets.

## C3a review round 1 (2026-11-18): Opus FIX-FIRST → changes to §2A
- **Blocking, fixed as follows (lead, per UD-1's "carried-over names"):** carry-over is decided BY NAME, matching the roll-up last week's debrief printed. A prior call is carried only when (i) it is still open at `week_start - 1` and (ii) its ticker's `per_name` status over LAST WEEK's calls (`week_start - 7 .. week_start - 1`, cutoff `week_start - 1`) is `still_open` (or `unknown`, see below). A name last week printed as acted (or not acted) is never carried. Repro that must become a test: XYZ `new_pick` Mon 11-02, BUY Tue 11-03, XYZ `add_winner` Fri 11-06 → week ending 11-08 "Acted on" (share 100%); week ending 11-15 must NOT carry XYZ.
- **`recs_load_start`** returns `min(current minimal start, week_start - 7)` so last week's calls are all loaded (Sunday 11-08 → loads from 10-26, not 10-29; the C3b cron wiring test uses this).
- **Unknown carry:** a prior call reading `unknown` at `week_start - 1` whose `window_end >= week_start`, on a name whose last-week status was `unknown`, is carried and re-judged at `week_ending` (it renders acted / not acted / "Not judged" as usual), so last week's "Not judged" name never disappears silently.
- **Strip guard:** `drop_ai_entry_bullets` matches only a bullet whose ticker is followed by a delimiter, e.g. `^\s*[•\-*]\s*\**\s*(?:T1|T2)\**\s*(?=[:(—–]|-\s)`; prose like "• A TRIM signal on TSLA fired" is kept. C3b applies it to `section_decisions` only, never Patterns.
- `_JUDGE_BY_JOURNAL_STATE` lookup must not KeyError on an unknown state (fall back to "trades_unreadable").
- **Round 2 (Opus FIX-FIRST, 1 blocking):** a carried row records the status its name had at `week_start - 1` (`carried_from: "still_open" | "unknown"`). Only `still_open` prints " (still open at last week's end)"; `unknown` prints " (its decision window ran into this week)", which is true whether the journal was unreadable or a buy date was. Plus a one-line comment that an unknown call on a still_open name can't arise under one journal.
- **Round 3: Opus SHIP, 0 blocking (2026-11-18).** C3a done. **C3b carry-forwards:** apply `drop_ai_entry_bullets` to `section_decisions` only; the cron wiring test loads from `week_start - 7` (Sunday 11-08 → 10-26); name in the commit body that `enter_now` rows now feed the conviction-tier pattern; decide explicitly whether the behavioural block keeps today's skip on an empty trades frame; optionally note in the requirements text the accepted limit that a BUY logged after last Sunday's email but dated inside last week leaves that name neither carried nor mentioned (same class as UD-4).
- Tests: XYZ two-week case; unknown-carry case; tie in both blocks (both acted → not in n_carried) pinned; a DST-change week (week ending 2026-11-08, call Fri 10-30); the strip prose case.

## 1. Today at HEAD (`debrief_advisor.py`, a `_GATE_FILES` member)
- Calls: :559-561 `rec_date >= week_start`, no upper bound; :563-564 keeps `new_pick` + `add_winner` only (drops `enter_now`).
- Acted: :566-569 any trade (BUY or SELL, any trigger) with ET date ≥ `week_start`, no upper bound (why #464, a SELL, credited MRVL). Per-name `acted` :588; `signals_ignored` :602.
- Latent offline collapse: `trades_df` None → every name "NOT acted on" (masked today because both callers refuse first).
- Behavioural block :664-703: `all_recs_df` else `recs_df`, two-type filter :679, acted = ticker ever bought (:681-690); `momentum_recency_pattern` called without `meaningful_delta_pp` (bare default 5.0 = `BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP`).
- Prompt :822-842; `_SYSTEM_PROMPT` §2 (:29-35) has the AI write entry bullets, §3 (:43-45) "≥80% Signal follower" / "Calm week". `generate_debrief` :950-1004 appends the unavailable notes (:986-992).
- Readers: cron `_run_debrief` (:2955 recs load, :3009 build, :3024 generate, :3052 save, :3077 email); app.py Weekly Debrief (:43584 recs load, :43615 build, :43626 generate, :43642 save, :43629-43640 preview, :43668 `_rpt_emailed_preview` (:42548), newest-row render :43688-43695 caption + :43796-43805 sections); email `notify.render_debrief_email` :1502 → `_email_section` :1124; app `report_display.report_markdown` :48. Not affected: monthly trajectory (perf/spy/alpha + `metrics_version`), app.py:46597, `scripts/account_snapshot_backfill.py:256`.
- Siblings to name in the commit body (re-grep line numbers at build time): app.py ~7696 (keep), ~32115 / ~38142 / ~50813 (Phase B), ~50953 + `personalized_discovery` ~75 (keep same-day); weekly view's lenient `load_weekly_debriefs` (app.py:43517) → C3c.

## 2. Changes per file

### A. New pure `stock_analyzer/debrief_entry_calls.py` (C3a; `_GATE_FILES` 97 → 98)
No Streamlit, DB or clock.

`recs_load_start(week_start, window_trading_days=None) -> date`: the earliest call date whose decision window still reaches `week_start`. Step back from `week_start - 1` while `entry_call_action.window_end(d - 1) >= week_start` (`advance_trading_days` never decreases). At N = 2: Mon 11-09 → 11-05; Mon 11-02 → 10-29; Mon 11-30 → 11-25.

`weekly_entry_calls(recs_df, trades_df, *, week_start, week_ending, window_trading_days=None) -> dict`:
```
{recs_status: "ok"|"unavailable", judge_state: "ok"|"trades_unavailable"|"trades_unreadable",
 this_week: [row], carried_over: [row], n_undated: int,
 follow: {n_acted, n_not_acted, n_still_open, n_unknown, n_carried, share_pct|None},
 matched_trade_ids: [...],   # for C5
 window_trading_days: N, acted_rule_version: entry_call_action.ACTED_RULE_VERSION}
row = {ticker, status, reason, rec_types, conviction, verdict, times_surfaced,
       call_dates[ISO], window_end ISO|None, matched_trade_date ISO|None, matched_trade_id}
```
- recs None → "unavailable"; a non-empty frame missing `ticker` / `rec_date` / `rec_type` → "unavailable" (never a quiet week); empty frame → "ok".
- Only `ENTRY_CALL_REC_TYPES` rows are judged (`classify_calls`); unreadable `rec_date` rows counted in `n_undated`, left out.
- This week: calls dated `week_start..week_ending`, cutoff = `week_ending`, rolled up with `per_name`. Calls after `week_ending` are never passed in (C2 contract).
- Carried over: calls dated before `week_start`, classified with cutoff = `week_start - 1`; keep those `still_open`, re-classify with cutoff = `week_ending`, then `per_name`. A prior-week call acted within its own week, or whose window closed by that Sunday, is never carried over.
- Excluded rows (already-held `enter_now`, other types) are not listed and not counted in `times_surfaced`.
- Unknown reason = the latest unknown call's reason. Conviction from the latest call; verdict = most common.
- **Share (UD-1, UD-2):** by name over this-week AND carried-over rows. A ticker in both blocks counts once, with `per_name` precedence (acted > still_open > unknown > not_acted). `share_pct` = acted / (acted + not_acted) × 100, rounded to 0 dp; None when no name was judged. Still-open and unknown names never count. `n_carried` = carried-over names that were judged (acted or not_acted) and counted.

`entry_signal_lines(entry) -> list[str]`: plain text (no `**`, `*`, `_`, `$`), every bullet starts "• ", dates from fixed name tuples (e.g. "Fri Nov 6", not strftime), N from the constant ("1 trading day" / "N trading days").
- Header `Entry signals this week:`.
- Call phrase: `on Fri Nov 6` (once) else `surfaced 3× this week, last on Fri Nov 6`; label New position / Add to winner / Enter Now joined " + "; conviction and verdict in parentheses when present.
- acted: `• NVDA: Enter Now call (Strong Buy, Confirmed) on Fri Nov 6. Acted on: you bought on Mon Nov 9.` + ` Ended the week +2.3%.` when `end_week_pct` known.
- not_acted, one call: `… Not acted on: no buy on the call day or the 2 trading days after it (window ended Wed Nov 4).`
- not_acted, k > 1: `… Not acted on: no buy within the decision window of any of its 3 calls (last window ended Fri Nov 13).`
- still_open: `• NVDA: Enter Now call on Fri Nov 6. Still open (decision window ends Tue Nov 10); next week's debrief says whether you acted.`
- unknown: `… Not judged: <reason>.` — `trades_unavailable` / `trades_unreadable` "the trade journal couldn't be read"; `trade_date_unreadable` "a buy of this name has a date that couldn't be read"; `no_call_date` "the call has no readable date".
- quiet week: `• No entry signals (new positions, adds to winners, Enter Now) were surfaced this week.`
- undated: `• N entry signal row(s) had no readable date and are not shown.`
- carried: header `Carried over from last week:` then e.g. `• NVDA: Enter Now call on Fri Nov 6 (still open at last week's end). Acted on: you bought on Mon Nov 9.`; not-acted / not-judged wording as above.
- recs unavailable → [] (the existing `RECS_UNAVAILABLE_NOTE` discloses it).

`behavioural_rows(hist_df, trades_df, *, cutoff) -> list[dict] | None`: None when trades or `hist_df` is None. Rows from `hist_df` dated ≤ cutoff with `acted_on` False / `acted_trade` None → `apply_to_matched(rows, hist_df, trades_df, cutoff=cutoff)`; keep arms per the C4 contract (acted with `credit_deduped` False → True; not_acted → False; all else dropped); return `{momentum_score, composite_score, acted_on}`.

`acted_rule_caption(v) -> str | None`: None when `float(v) >= ACTED_RULE_VERSION`; None / NaN / 1 / unreadable → "Entry signals in this debrief used the older acted rule: any trade in the name, a buy or a sell, from the week's start counted as acted, and Enter Now calls weren't listed. Newer debriefs count only a buy on the call day or within N trading days." (N from the constant).

`drop_ai_entry_bullets(text, entry_tickers, keep_tickers) -> str` (UD-5): drops AI bullet lines matching `^\s*[•\-*]\s*\**\s*TICKER\b` for entry names not in the protective or closed lists (`keep_tickers`). Pure; wired in C3b.

### B. `stock_analyzer/entry_call_action.py` (C3a)
`apply_to_matched`: replace `rid is not None and rid == rid` with a `pd.isna` check in `try/except (TypeError, ValueError)` so a `pd.NA` (Int64) id reads "not found" instead of raising.

### C. `debrief_advisor.py` (C3b)
- Replace :537-603 with `weekly_entry_calls(...)` in try/except (exception → `recs_status = "unavailable"`).
- Package: `recs_surfaced` = this_week rows + `end_week_pct` (`_end_week_pct`) + `acted` (status == acted); `signals_ignored` = not_acted only; new `entry_carried_over`, `entry_follow`, `entry_judge_state`, `entry_n_undated`, `entry_window_days`, `acted_rule_version`.
- Behavioural block: `_matched = behavioural_rows(_hist, trades_df, cutoff=week_ending)`; skip when None; pass `meaningful_delta_pp=BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP` (value unchanged).
- `_format_prompt` entry part:
  - Header: `Entry signals this week (new positions, adds to winners, Enter Now; K name(s)). The app has already written one bullet per name at the top of "Decisions you made". Do not write entry-signal bullets and do not restate whether they were acted on; use these for Patterns and One thing to watch only:`
  - Per name: `NVDA: Enter Now (Strong Buy) [Confirmed] (surfaced 2× this week) — still open (decision window ends 2026-11-10); ended week +3.1%` (acted on (bought D) / not acted on / still open (decision window ends D) / not judged (reason)).
  - `Entry-signal follow-through this week (by name, including N carried over; acted = a buy on the call day or within N trading days): acted on A of J judged (X%); S still open and U not judged are not counted.`
  - Trades unavailable: `Whether these were acted on couldn't be checked (the trade journal couldn't be read); do not say they were or weren't, and do not compute a follow-through share.`
  - `Carried over from last week (already written by the app):` block; quiet week: `No entry signals were surfaced this week (the app has written that line).`
  - Behavioural headers: "(all-time entry-signal history; acted = a buy within N trading days of the call; still-open calls not counted)".
  - `RECS_UNAVAILABLE_PROMPT_LINE` / `RECS_UNAVAILABLE_NOTE` say "new positions / adds to winners / Enter Now".
- `_SYSTEM_PROMPT` §2: "The app writes the entry-signal bullets itself (new positions, adds to winners and Enter Now calls: acted on, not acted on, still open or not judged) and places them at the top of this section. Do NOT write a bullet for an entry signal, and never restate or contradict whether one was acted on. Write bullets only for the protective WATCH/TRIM/EXIT signals and the closed positions below." Keep the protective example and snapshot / closed rules; drop the entry example. "None" rule: "If no protective signals fired, write '• No protective signals fired this week.' — only when the data says they were checked …". §3: "Signal follower: the entry-signal follow-through line shows ≥80% (it already leaves out still-open and not-judged signals; never count them yourself)" (UD-2: entry only; UD-3: 80% stays prompt text); Calm week adds "… and no entry signal is still open or not judged".
- `generate_debrief`: after parse + truncation note, strip AI entry bullets (`drop_ai_entry_bullets`, UD-5), then `section_decisions = "\n".join(entry_signal_lines(...))` + `"\n\n"` + AI text when present + existing notes; result gains `"acted_rule_version": ACTED_RULE_VERSION`. A package with no entry info yields no Python lines and its prompt falls to the recs-unavailable line, never "none".

### D. `db.py` (C3b): add `"acted_rule_version"` to `_WEEKLY_DEBRIEF_COLS` (required by `test_generated_debrief_carries_the_recs_note_alone`'s subset assert). Save unchanged.

### E. `cron_runner._run_debrief` (C3b): `load_recommendations_or_none(start_date=recs_load_start(week_start), end_date=week_ending)`; log the lookback start.

### F. app.py (C3b, render-only): :43584 same `recs_load_start`; after the newest-row "Decisions you made" markdown (:43796), `st.caption` of `acted_rule_caption(_wd.get("acted_rule_version"))` when set. No constant comparison in app.py.

### G. C3c: the weekly view reads `load_weekly_debriefs_or_none` and shows "couldn't read saved debriefs" on None instead of "No weekly debriefs yet" (render-only).

## 3. Offline behaviour
- Cron: trades None → lane already fails before the package (unchanged). Recs None → saved + emailed with no Python block and `RECS_UNAVAILABLE_NOTE`; lane red (unchanged). `all_recs` None → behavioural falls back to the widened `recs_df`. Module exception = recs None.
- App Generate: trades None → existing "Could not read trade journal" error; recs None → existing warning + note.
- If trades None ever reaches the package: every name "Not judged: the trade journal couldn't be read", share None, prompt forbids a share. Never "not acted".
- Archived view: null / NaN version → older-rule caption.

## 4. Time-context matrix
- NVDA (11-06 enter_now, BUY 11-09): week ending 11-08 "Still open (decision window ends Tue Nov 10)…", not in the share; week ending 11-15 loads from 11-05, "Carried over from last week … Acted on: you bought on Mon Nov 9.", counted in that week's share (UD-1).
- MRVL week ending 11-08: acted via #479 (earlier than #480); with only #454 / #464 → still open.
- Thanksgiving: Tue 11-24 call bought Fri 11-27 → acted in week ending 11-29. Wed 11-25 and Fri 11-27 calls still open in week ending 11-29 (windows end Mon 11-30 / Tue 12-01), carried into week ending 12-06: BUY 11-30 → acted; BUY 12-01 for the 11-25 call → not acted.
- Regenerated Mon / Wed for the past week: same week and cutoff, entry bullets byte-identical; an emailed row → U14 preview. Saturday visit → previous Sunday's week.
- After hours: Fri 20:30 ET BUY dates Friday; Sun 23:30 ET inside the week; trades logged after the 18:01 ET Sunday run aren't in that email.
- Late log: the journal's ET date decides; a provenance midnight-UTC import is re-anchored; a no-provenance midnight-UTC row reads as the ET day before; a late SELL never affects acted.
- Known limit (UD-4): a not-acted call in an emailed debrief is not revisited automatically.

## 5. Tests
- New `tests/test_debrief_entry_calls.py`: `recs_load_start` at the three dates + a sufficiency/minimality property over every Monday 2026-2027 with N ∈ {0,1,2,3,5}; NVDA two-week; MRVL #479-not-#464; SELL / BUY-before-call / BUY-after-`week_ending` never credit; calls after `week_ending` not listed and don't turn a name unknown; already-held enter_now excluded, blank judged; buy_candidate / overflow_pick never listed; carried-over exclusions; same ticker carried + called again (counted once in the share); UD-1 share includes carried names and `n_carried`; Thanksgiving / half-day; 20:30 ET; late-log / no-provenance; determinism across "now"; offline (trades None, recs None, missing columns, empty frame); pd.NA id through `classify_calls` and `apply_to_matched`; exact `entry_signal_lines` strings for every status + no `**` `*` `_` `$`; lines through `notify._email_section` (one `<li>` per bullet, no literal `**`) and `report_display.report_markdown`; `acted_rule_caption` for None, NaN, 1, 2, "2"; `behavioural_rows` drops still_open / unknown / credit_deduped, a SELL never acted, None on trades None; `drop_ai_entry_bullets` keeps protective / closed names and non-bullet text.
- `tests/test_entry_call_action.py`: pd.NA regression.
- C3b: `tests/test_debrief_advisor.py` rewrites (:555-615, `_base_package` prompt tests ~:1099, `_NO_SIGNALS_LINE`), CCC-call-with-SELL → not acted, generate ordering + strip + `acted_rule_version == 2` + keys ⊆ `_WEEKLY_DEBRIEF_COLS`, no-restate sentence, behavioural input excludes still-open; wiring `tests/test_debrief_period_engine_wiring.py` (cron + app call `recs_load_start`, its value is `start_date`; `_WEEKLY_DEBRIEF_COLS` has the column); `tests/test_cron_debrief_monthly_date_anchor.py` (Sunday 2026-11-08 loads recs 2026-10-29 → 11-08). Golden: `LLM_GOLDEN_REGEN=1 python -m pytest tests/test_llm_request_golden.py -q` (only `debrief_advisor.generate_debrief#0` should change).

## 6. Docs (C3b commit)
requirements F-152 "UPDATED (C3)" paragraph; architecture §6.10 (`acted_rule_version SMALLINT` + paragraph), module tree, and the stale F-3 row (~4514 "Sonnet 4.6 / 700 tok"; verify vs code); review-policy gate reason; bug register (DebriefEnterNow causes 1-2 fixed, 3 → C5; MRVLActed weekly leg fixed, monthly → C4; WeeklyViewOffline unless C3c lands); User Guide app.py ~46035 and ~42622; parent plan Status; CLAUDE.md DebriefEnterNow line; shipped-log; commit `docs/sql/weekly-debriefs-acted-rule-version.sql`.

## 7. Split
- **C3a** (implementer → Opus reviewer): new module + tests, the pd.NA fix + regression, `_GATE_FILES` / review-policy (lead). Unwired.
- **C3b** (implementer → Opus reviewer): debrief_advisor, db cols, cron + app wiring + caption + strip wiring, test rewrites, wiring tests, golden regen, §6 docs. Push with C3a.
- **C3c** (reviewer): weekly view offline read.
The first version-2 debrief (11-22) will show "Carried over" for week-ending-11-15 calls that the 11-15 version-1 email never called still open; the wording doesn't claim it did.
