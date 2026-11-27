# Brief consolidation: SectorCapCardMerged, RiskOffTrimShift, MacroTrimDefer (spec)

**Status:** SPEC 2026-11-27 (Opus planner `claude-opus-5-5`, PROCEED WITH CHANGES; read at HEAD `4c5ba332`). **Owner took every recommended answer Q1–Q9 on 2026-11-27** (incl. the F-22a and H6 reversals). Two rows reverse recorded decisions (F-22a "mechanical exit suppresses the risk card"; H6 2026-07-29 "risk-off never sits beside a Watch"), so Q1 and Q4 / Q5 are the owner's call. No constant changes value; no new threshold.

**Class rule:** one Brief card may push aside or silence another only when it really sells at least as much of that name, and wherever it does the user sees a line saying why. Cards that sell nothing (critical-news "Hold for now", a fired pre-commitment, the sector card's attribution ticker, a Watch, a stop-tighten) never move or drop another card's sale.

## 0. Corrected claims (checked at HEAD 4c5ba332)

- **SectorCapCardMerged.** The ticker is `rt[0]["ticker"]` at daily_briefing.py:3542 (`root_tickers` = the sector's top 3 by weight, risk_advisor.py:781-794). The mechanical branch that drops the card is 3611-3623; the attribution note in `_review_list` is 3740-3747. Signals: `sector_trigger_text.card_state` 56-83, `body()` hard_cap text 88-90. Narrower than registered: lost only under `stop_breach` / `sell_signal` (`_MECHANICAL_KINDS`, 3558); under a deterioration EXIT / TRIM or critical-news primary its flags merge onto that card (3625-3633). On a recovered breach BriefBreachRestore already brings it back. F-22a was deliberate ("user-chosen", for the old per-ticker beta card); today the sector card is the only HIGH, non-Tune-up risk rec that reaches Act Today (`_TUNEUP_RISK_TYPES`, 412), so F-22a's purpose is gone.
- **RiskOffTrimShift.** Brief: daily_briefing.py:4819-4823, `exclude_tickers=decision_bucket.all_flagged_tickers(act, review, ignore_news=True)`. Email: headless_alert_engine.py:561-566, `exclude_tickers=reduced` (stop breach + deterioration EXIT, built 406-559). Wider than registered: the Brief set includes real sells too. Third copy: forward_sim.py:521-539 excludes stop-breached plus ANY deterioration tier, citing H6. The breadth is H6's recorded decision (comment 4812-4818; F-25e requirements.md:541).
- **MacroTrimDefer.** `_act_tickers` at 3739; eligibility filter 4238-4241 (AVGO rationale 4231-4237). The AVGO contradiction is now settled downstream: `decision_bucket._reconcile_act` (122-159) drops a same-name "Hold for now" beside any reduce incl. a macro `PROTECTIVE_TRIM` and folds the headline onto it. "No line" is half true: the all-deferred WATCH has a why (4374-4379); a trim moved to another name has none. Review trims never push the macro trim (U6).

## 1. Decisions (pure functions)

### 1a. Row 1 build side (`daily_briefing._consolidate_act_today`)
- A `kind == "risk"` item with a ticker is grouped only with other `risk` items on that ticker (merge as today, "RISK — N flags on T", weight / P&L from port_df), emitted as its own card. Non-risk items consolidate as before; no stop / Sell / deterioration / critical-news card carries `risk_flags` any more.
- Docstring: the sector card's ticker is an attribution, so "you don't trim what you're exiting" doesn't apply.
- `decision_bucket._reconcile_act`: leave `kind == "risk"` out of `reduce_tickers` and out of the "annotate the first reduce card" loop.
- `breach_recovery._EXCLUDED_ACT_KINDS` (line 51) gains `"risk"` (else a recovered breach restores a duplicate); update its module doc.
- `risk_advisor.py` sector rec (~854): add `"sector_members": [{"ticker", "market_value"}]` for every holding in `top_sec`, sorted by ticker; daily_briefing ~3532-3538 copies `rec["sector"]` and `rec["sector_members"]` onto the flag (additive).
- Side effect: EWSiblings (6)/(7) can no longer occur; `critical_news_as_risk` stays for legacy memoized Briefs.

### 1b. Row 1 line + netting (NEW gate module `stock_analyzer/sector_cap_cover.py`)
```
decide(flag: dict | None, cover: dict | None) -> {
  "exited": [T], "also_selling": [T], "netted": bool, "covered": bool,
  "candidates": list,                 # trim_candidates; exited names removed only when netted
  "net_target_dollar": float | None,  # trim_target_dollar minus the exited names' market value, floored at 0
  "net_target_pp": float | None,      # net / trim_target_denom * 100 (None if denom <= 0)
  "line": str | None }
```
- `cover` = `act_sell_cover.view_sell_cover(_act_view)` (post-demotion view). `cover None` → no line, plan unchanged.
- Members from `flag["sector_members"]`; legacy flag falls back to `trim_candidates` + `trim_excluded_recent` tickers.
- Per member `act_sell_cover.cover_for`: `exit` True → `exited`; other entry → `also_selling`; None ignored; case-insensitive.
- Netting (Q2): only when every exited member has a finite market value > 0 and the target is finite: `net = max(0, target − Σ)`, `covered = (net == 0)`. Else `netted = False`, plan unchanged.
- Wording (names "A", "A and B", "A, B and C"; no `$`, no `**`, no share counts):
  - exits, netted, net > 0: `🏠 Today's Act Today call already sells all of {A} ({sector}). That sale counts toward this sector trim; the 🔎 Rebalance plan below sizes only the rest.`
  - exits, covered: `🏠 Today's Act Today call already sells all of {A}, which by itself brings {sector} down to the {SECTOR_ELEVATED:.0f}% level. No other {sector} trim is needed today.`
  - exits, not netted: `🏠 Today's Act Today call already sells all of {A} ({sector}). The 🔎 Rebalance plan below was sized before that sale; re-check it once the sale is logged.`
  - also_selling (own sentence; starts "🏠 " when alone): `{B} also {has|have} {its|their} own sell in today's Act Today call; the 🔎 Rebalance plan was sized before {that sale|those sales} and shrinks once {it is|they are} logged.`

### 2. Row 2: one risk-off pool for Home and the 08:00 email (NEW gate module `stock_analyzer/risk_off_pool.py`)
```
full_exit_names(items, deterioration) -> frozenset[str]
settle_brief(risk_off_cards, act, review) -> (cards_out, review_out)
```
- `full_exit_names`: upper-cased tickers of items with kind `stop_breach` or `deterioration_exit`, plus every deterioration payload with `tier == "EXIT"` (covers a composite-Sell name whose deterioration EXIT card is skipped). Non-list/tuple deterioration → no EXIT names. Nothing else is excluded.
- Single caller rule: Brief `full_exit_names(act, deterioration)`; email `full_exit_names(alerts, det)` (= today's `reduced`); forward_sim builds the same two inputs from `stop_breached` and the EXIT tier (implementer confirms the tier strings).
- `settle_brief` (Brief only, returns copies, never mutates):
  1. Per card, other same-name sells from `act_sell_cover.sell_cover_summary(act, review)` (risk-off cards aren't in `act` yet). `state = act_sell_cover.cover_state(entry, need=card["trim_shares"])`; append `act_sell_cover.beside_note(state, T, "trim")` to `why` when not None.
  2. Each Review item on T with `action.type` WATCH or DETERIORATION_WATCH, no `watch_kind == "news"`, no `covered_by` → `action["covered_by"] = T` (app.py `_fmt_action` 1572-1619 already prints "covered by today's Act Today trim of T; no separate trade."). Type kept (exit_signals WATCH capture unchanged).
  3. TIGHTEN_ONLY, premortem and sector cards untouched.
- `exit_advisor.assess_risk_off_derisk` (~702): additive `"trim_shares"` (the same int the directive prints); fix the docstring's "higher-priority reduce" wording.
- NEW `act_sell_cover.beside_note(state, ticker, noun) -> str | None`:
  - covered: `{T} also has a sell at least this large in today's Act Today call; that sale counts as this {noun}, so don't add this one on top of it.`
  - partial: `{T} also has a smaller sell in today's Act Today call; sell this {noun}'s size in total, not the two added together.`
  - unknown: `{T} also has its own sell in today's Act Today call, and its size couldn't be compared with this {noun}; treat this {noun} as the minimum, not an extra sale on top.`
  - show: None.
- `act_sell_cover.item_covers` keeps reading risk-off as unknown size (DeferSizeBlind Q2).

### 3. Row 3: macro pre-event trim target (NEW gate module `stock_analyzer/macro_trim_target.py`)
```
size_trim(weight, price, portfolio_value) -> {"reduction_pp", "trim_dollars", "trim_shares"}
pick(rows, act_cover, portfolio_value, exclude=()) -> {
  "target": row | None, "sizing": dict | None, "conviction_unmeasured": bool,
  "skipped_ahead": [{"ticker", "reason": "exit" | "covers"}],
  "all_covered": bool, "beside": "partial" | "unknown" | None }
all_covered_why(exposure_pct, names) -> str
skipped_sentence(ticker, reason) -> str
```
- `size_trim` moves daily_briefing.py 4299-4310 verbatim (numbers identical).
- `rows` = affected holdings (ticker, score, score_available, weight, price); `act_cover = act_sell_cover.sell_cover_summary(act_today, [])`, Act lane only, read AFTER the BriefSkipSizeBlind fold.
- Skip only when `cover_state(entry, need=its own trim_shares) == "covered"` (equality covers). `exclude` (breach alternate pass) skipped silently. Ranking unchanged: measured scores ascending then ticker, else weight descending then ticker.
- `skipped_ahead`: skipped names that would rank before the target.
- `beside`: the target's own cover state when partial / unknown.
- Wording: exit `Not {T}: today's Act Today call already sells all of it.`; covers `Not {T}: today's Act Today call already sells at least this much of it.`; beside via `beside_note(state, T, "pre-event trim")`; `all_covered_why`: `Affected-sector exposure {x:.1f}% > {MACRO_AFFECTED_TRIM_THRESHOLD_PCT:.0f}% trim threshold, but every affected holding already has a sale at least this large in today's Act Today call ({names via _affected_text}), so no separate pre-event trim is sized.`; the conviction-unmeasured why becomes "… not already sold in today's Act Today call …".
- Macro Affected list (closes MacroAff f/u leg 1): in `build_daily_briefing` (~4879) `_reduced = set(act_sell_cover.sell_cover_summary(act, review))` instead of `decision_bucket.reduce_call_tickers`.

## 2. Wiring sites (HEAD line numbers)
- C1: daily_briefing.py 3510-3548, 3574-3639; decision_bucket.py 122-159; breach_recovery.py 51 + doc; risk_advisor.py ~854-899.
- C1b: app.py `_render_act_card` 12837: after the flags loop (12888-12894), for a card with a `sector_concentration` flag `_sc = sector_cap_cover.decide(_rebal_flag, _db_view_cover)` (`_db_view_cover` at 10190), one line div via `_md_bold` / `safe_html`. Rebalance plan (13161-13290) uses `_sc["candidates"]`, `_sc["net_target_dollar"]`, `_sc["net_target_pp"]`; when `_sc["covered"]`, skip "Trim first" (else an empty allocation falls through to the legacy priority list at 13289), print the line, keep Redeploy.
- C3: daily_briefing.py 4202-4415. Move section 5 below the fold block (4417-4458) and above U6 (4460). Replace 4231-4241 and 4264-4310 with `macro_trim_target.pick` / `size_trim`; 4365-4379 why with `all_covered_why`; append skipped / beside sentences to the PROTECTIVE_TRIM why; `_reduced` at 4879.
- C2: daily_briefing.py 4805-4823 (rewrite the H6 comment, `exclude_tickers=risk_off_pool.full_exit_names(act, deterioration)`, then `settle_brief` in a try that keeps the unsettled lists + logs on exception, before the news folds at 4832); headless_alert_engine.py 561-566; forward_sim.py 521-539 + module text; exit_advisor.py ~702; act_sell_cover.py `beside_note`.
- `_SYNTH_SCHEMA_VER` (app.py:7577): bump to HEAD + 1 at commit time in C1, C2 and C3 with a "was N:" note; C1b is render-only.

## 3. Chunking (one commit each, Opus reviewer each, implementer builds)
1. C1 row 1 build side. 2. C1b row 1 line + netting. 3. C3 row 3 macro (`beside_note` lands here if before C2). 4. C2 row 2 risk-off (after sell-check C5 commits). 5. C5 docs (lead).

## 4. Tests the build must include
Row 1: `_consolidate_act_today` (stop_breach / sell_signal + sector risk card on AAA → 2 items, risk card keeps rec_type / trim_candidates / trim_target_*; deterioration_trim / exit / critical_news + risk → 2 items, no `risk_flags`; two risk items still merge; property: every non-risk non-premortem ticker appears at most once). End-to-end `build_daily_briefing` with a HIGH sector rec whose `root_tickers[0]` has Gap ≤ 0 → both cards; Signals parity (`sector_trigger_text.card_state(...)["band"] == "hard_cap"` and act_today holds that sector's flag). `_reconcile_act`: critical news + standalone sector card keeps both, no note on the risk card; plus a Review trim → news dropped, note on the trim. Update tests/test_critical_news_as_risk.py (`_consolidated()` returns 2). `act_today_view` with an active breach on the largest name → exactly 1 sector card; after recovery still exactly 1. `sector_cap_cover.decide`: cover None; no sold members; exited member with known value (net = target − value; 0 = covered; above target floors at 0); exited value None / NaN → no netting + "sized before"; partial seller → clause, no netting; sold non-member ignored; legacy flag; case-insensitive; no `$` / `**` / share-count digits; `portfolio.trim_allocation(candidates, net, denom)` totals the net; recovered breach → no line. risk_advisor `sector_members` covers `top_sec`.
Row 2: `full_exit_names` cases (stop_breach; deterioration_exit; tier EXIT with no item; TRIM / WATCH / sell_signal / critical_news / premortem / risk / Review not included; `{}` / None deterioration). Brief parametrised: top-beta AAA with each non-selling card stays a risk-off name; stop breach / deterioration EXIT excludes it and the pick moves. Parity: identical inputs through `compute_protective_alerts` (mock `_build_context`) and `build_daily_briefing` give identical risk-off tickers, `trim_shares`, directives (incl. Sell + deterioration-EXIT, deterioration-TRIM, Watch-only names). `settle_brief`: ≥ (equality) covered; < partial; Sell with no `min_trim` unknown; `trim_shares` 0 covered whenever another sell exists; no other sell no line; Watch gets `covered_by`; news Watch / TIGHTEN_ONLY untouched; inputs not mutated; raising settle keeps every card. forward_sim H6 test rewritten (WATCH / TRIM no longer excluded; EXIT / stop-out still are); tests/test_headless_alert_engine.py:511 asserts the shared function; `trim_shares` equals the directive's number.
Row 3: weakest affected name with critical news / premortem / sector card gets the trim (with critical news `split_defensive` drops the hold and the trim carries the headline); weakest with stop breach / deterioration EXIT skipped with "Not T: … sells all of it."; deterioration trim shares ≥ macro (equality covers) skipped, fewer stays with the partial sentence; Sell with no `min_trim` stays (unknown sentence), with a folded `min_trim` ≥ macro skipped; post-fold proof (deterioration 1 share + folded earnings `min_trim` 5 vs macro 3 → skipped); all covered → WATCH with the new why; exposure ≤ threshold and broad events unchanged. Regression: with no Act sells the output is byte-identical to HEAD (measured, unmeasured, ties); `size_trim` matches the old formula on a grid incl. sub-share; `macro_exclude` honoured; the macro Affected list keeps a name whose only Act card is the sector attribution.
General: no date logic changes. Grep tests/ for every reworded string.

## 5. Docs (lead)
requirements.md: F-22a (sector card kept beside a same-name exit; ticker = attribution), F-22c (🏠 line + netting), F-25e (exclusion = full exits only, same as F-140; same-name Watch reads covered; at least / smaller / couldn't compare line), F-25 PROTECTIVE_TRIM (skipped only when the name's own Act sale covers it, with the line), F-140 parity sentence, F-245 forward-sim exclusion; new G-id: "A Brief card defers to, or is skipped for, another card only when that card sells at least as much; the user sees why." architecture.md §4.0.3, the consolidation diagram (~859), forward-sim section (~3126-3140). User Guide Act Today paragraph. bug-register: close the three rows + EWSiblings (6)/(7) + MacroAff f/u leg 1; add §7 rows. review-policy reasons. CLAUDE.md queue line + `_GATE_FILES` count; shipped-log; memory; this Status line.

## 6. `_GATE_FILES` additions
`stock_analyzer/sector_cap_cover.py`, `stock_analyzer/risk_off_pool.py`, `stock_analyzer/macro_trim_target.py`; recommended (Q9) `stock_analyzer/decision_bucket.py`. Give the final count at commit.

## 7. Siblings
- Fixed: forward_sim.py:521-539, headless_alert_engine.py:561-566, daily_briefing.py:4879, breach_recovery.py:51, decision_bucket.py:122-159.
- New row **PremortemAttribSilence** (awareness only): daily_briefing.py:3443 (critical news dropped on a name with only a premortem card, against owner Q4 "premortem never suppresses"); :3739 / :3776 (deterioration Watch dropped for a name whose only Act card is the sector attribution or a premortem); :4156 / :4482 (news Watch, same cause). Fix: build those dedup sets without `risk` / `premortem_triggered`.
- New row **RiskAttribReduce**: decision_bucket.py:44-47 `_REDUCE_ACT_KINDS` includes `risk`, so `reduce_call_tickers` treats the attribution as "under a Reduce call" (Analysis banner, Opportunity Signals, Rebalancer ADD gate = EWSiblings (13)); fails safe.
- Leave: daily_briefing.py:1042 Grow Today `_act_blocked` (buy-check owns it).
- Existing rows: SplitFlagApp; MacroAff f/u leg 2 stays per BriefBreachRestore Q2.
- Cosmetic, accepted: sector card header shows the attribution name's weight / P&L; Summary compact cards don't show the C1b line; TIGHTEN_ONLY beside risk-off keeps "position still has room".

## 8. Risks / coordination
- Collisions: headless_alert_engine.py (sell-check C5, now committing); app.py (FracNew Trade Journal; sell-check C6 Summary / Risk Analysis / `_synth_sig`); daily_briefing.py: no edits inside `_grow_today` (859-~3148); C2's risk-off block (4805-4872) sits just above the `_grow_today(...)` call (4881-4898) where buy-check may add kwargs. Stage only this chunk's hunks (`git diff --binary` + `git apply --cached`).
- Grow Today coupling: `all_flagged_tickers(act, review)` blocks adds; after C2 / C3 risk-off and macro targets move to the right names on Home AND the 09:45 / 11:30 pick emails; names that used to get a shifted risk-off trim become eligible for adds again (consistent, fails safe).
- History meaning shifts (unversioned, EWSiblings (4) precedent): `exit_signals` RISK_OFF rows name the real top-beta names; `decision_context.act_today_n` +1 on sector-card days. State in commit bodies.
- Offline: cover None never defers / nets; a raising settle or pick keeps every card; the risk-off pool can't shrink on a deterioration outage.

## 9. Owner questions (recommended defaults)
- **Q1 (reverses F-22a):** keep the sector Hard Cap card in Act Today beside a same-name stop breach or Sell? Default yes.
- **Q2:** count a full exit inside the sector toward the 🔎 Rebalance plan (net it out)? Default yes, full exits only; partial sells named, not netted.
- **Q3:** if those exits alone bring the sector to `SECTOR_ELEVATED`, keep the card in Act Today with "No other trim is needed today"? Default keep.
- **Q4:** risk-off excludes only full exits (stop breach, deterioration EXIT), the same set as the 08:00 email? Default yes.
- **Q5 (reverses H6):** a Watch on a risk-off name reads "covered by today's Act Today trim of T" instead of the trim moving to a lower-beta name? Default yes.
- **Q6:** when the weakest affected name's own sale already covers the pre-event trim, move the trim to the next-weakest name (today's behaviour)? Default move (no loosening).
- **Q7:** a Sell with no size keeps the pre-event trim on that name, with "treat this as the minimum"? Default yes (today it moves away).
- **Q8:** PremortemAttribSilence: name now, fix in a follow-up? Default yes.
- **Q9:** add decision_bucket.py to `_GATE_FILES`? Default yes.

**What does not change:** every constant, every sale size, the scope of the pre-market email, `act_sell_cover.item_covers`, `act_trim_fold`, U6, all of `_grow_today`.
