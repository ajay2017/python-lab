# EW C1 + C2 build spec (earnings-week trim consistency)

**Status:** SPEC 2026-11-24 (lead, Opus 5.5), from `docs/plans/earnings-week-trim-consistency.md` (owner took every default U1–U7). Implementer builds C1 + C2 together; one Opus review covers both. **BUILT 2026-11-24: Opus FIX-FIRST (2 blocking) → round 2 → SHIP, 0 blocking.**

Read the plan first (§1 rule, §2 site table, §3 fix shape, §6 risks, §7 tests). This file pins the exact contracts and wording for C1 + C2 only. C3 (Tune-up / Risk Analysis wiring), C4 (Rebalancer) and C5 (docs) are NOT in scope; C1 builds their pure functions, nothing calls them yet.

No constant changes value. No new constant. Every threshold below is imported from `stock_analyzer/constants.py` by name (never a literal).

---

## C1 — pure decisions (gate files)

### C1a `stock_analyzer/trim_sizing.py`: `earnings_week_decision(weight_pct, n_positions, shares) -> dict | None`

The caller has already checked `in_earnings_window(days)`; this function does not take days.

```
w = _num(weight_pct); if w is None -> None
floor = earnings_overweight_floor(n_positions)
if w > floor:                       basis "earnings_week",      target EARNINGS_OVERWEIGHT_TRIM_TO_PCT
elif w > SINGLE_NAME_TRIM_TRIGGER:  basis "standing_in_window", target SINGLE_NAME_CEILING
else: None
return {"basis", "floor_pct": floor, "target_pct", "weight_pct": w,
        "trim_shares": shares_to_trim(shares, w, target),   # may be 0
        "over_ceiling": w >= SINGLE_NAME_CEILING}
```

Both comparisons strict `>` (same as `earnings_week_trim` today). `earnings_week_trim` stays unchanged (other callers may exist; grep and leave them). The `standing_in_window` branch is reachable only when floor < w, i.e. N ≤ 7 (floor > 18).

### C1b new gate module `stock_analyzer/held_trim_owner.py`

Pure: no streamlit, no I/O. Module docstring in the house style (see `young_holding_stop.py`): what it decides, why (plan §3 "A sell that names the ticker"), the attribution trap (memory: a `risk` card's ticker is the sector's largest holding, not a name being sold).

1. `brief_sell_names_ticker(item: dict, ticker: str) -> bool` — case-insensitive ticker match.
   - Act Today item (has `kind`): True iff `kind` in `decision_bucket.EXIT_KINDS | {"deterioration_trim", "risk_off_derisk"}` AND `item["ticker"]` == ticker. Import `EXIT_KINDS` from `decision_bucket`; do not copy the set.
   - Review item (has `action`): True iff `action["type"]` in `decision_bucket._ACT_REVIEW_TYPES` AND (`item["ticker"]` == ticker OR `action.get("trim_ticker")` == ticker).
   - Always False for kind `risk`, `critical_news`, `premortem_triggered`, any macro act kind, a WATCH / HOLD review item, a missing / blank ticker, a non-dict.
   - If `_ACT_REVIEW_TYPES` is private, import it anyway (same package) or expose a public alias in decision_bucket ONLY if decision_bucket is not a gate file; report which you did.
2. `brief_sell_tickers(act_today, review) -> set[str]` — upper-cased tickers for which (1) is True over both lists. `None` inputs = empty.
3. `tuneup_single_name_disposition(ticker, sell_tickers, earnings_days, weight_pct, n_positions, shares) -> dict` → `{"state": "brief_owns" | "earnings_note" | "show", "text": str | None}`:
   - `brief_owns` when ticker.upper() in `sell_tickers` → text `f"{T}'s single-name size is handled by today's Act Today call."`
   - else `earnings_note` when `in_earnings_window(d)` (d = `earnings_days.get(T)`) and `earnings_week_decision(...)` is None → text `f"Earnings {when}: below the {floor:.1f}% earnings-week line, so nothing is due before the report; this is standing work."` with `when` = "today" for d == 0 else f"in {d}d", floor from `earnings_overweight_floor(n_positions)`.
   - else `show`, text None. (In the window with a non-None decision but no Brief sell, e.g. a 0-share trim or a Brief that didn't run, the card shows: fail toward the sell.)
4. `rebalancer_trim_disposition(ticker, sell_tickers, reduce_calls_ok: bool, earnings_days, directive: str | None) -> dict` → same shape:
   - `brief_owns` only when `reduce_calls_ok` is True AND ticker in `sell_tickers` → text `f"Today's Act Today call: {directive}"` when `directive` is a non-blank string, else `"Sized by today's Act Today call (🏠 Home)."`
   - else `earnings_note` when in the window → `f"Earnings {when}: check 🏠 Home's Act Today before trading this name."`
   - else `show`, None.
   - `reduce_calls_ok` False (unverified / offline / stale) never defers (plan §6).

Register: add `"stock_analyzer/held_trim_owner.py"` to `_GATE_FILES` in `.claude/hooks/pre_tool_checks.py` (alphabetical position as the list uses). The lead writes the review-policy.md and CLAUDE.md lines; don't edit those docs.

---

## C2 — wire S1 / S3 / S4 (gate files + one app.py render branch)

### S1 Brief, `daily_briefing._review_list` section "2 — Earnings within 7 days" (~:3409-3490)

- Skip set (U5): replace `if ticker in _act_tickers: continue` with `if str(ticker).upper() in _reduce_tickers: continue`. Leave `_act_tickers` in place for its other users.
- Replace `_trim_sizing.earnings_week_trim(...)` with `_trim_sizing.earnings_week_decision(...)` (call it `_edec`). Branches:
  1. `_edec` and `trim_shares >= 1` and basis `earnings_week` → today's TRIM_TO_TARGET item, unchanged text, `target_weight` from `_edec`.
  2. `_edec` and `trim_shares >= 1` and basis `standing_in_window` → TRIM_TO_TARGET, `target_weight` = `_edec["target_pct"]`, same keys as (1). `reason_key`: keep `"earnings"` unless a consumer of `reason_key == "earnings"` would then print a 10% / earnings-line claim; grep every reader of `reason_key` and report. why =
     `f"Weight {weight:.1f}% is under your {floor:.1f}% position-count-adjusted earnings-week line (N={_n_pos} positions) but over the {SINGLE_NAME_TRIM_TRIGGER:.0f}% single-name trigger, so the standing trim to {SINGLE_NAME_CEILING:.0f}% applies before the report: earnings week never sizes looser than the week before."`
     trigger = `_esc.brief_earnings_trigger(..., trimmed=True)`.
  3. `_edec` with `trim_shares == 0` → WATCH. why: for `earnings_week` keep today's "over the … line by less than one share" text; for `standing_in_window`: `f"Weight {weight:.1f}% is over the {SINGLE_NAME_TRIM_TRIGGER:.0f}% single-name trigger by less than one share at today's price, so no trim is sized (N={_n_pos} positions)."`
  4. `_edec` None → WATCH. If `weight >= SINGLE_NAME_CEILING`:
     `f"Weight {weight:.1f}% is under the {floor:.1f}% position-count-adjusted earnings-week line (N={_n_pos} positions), so nothing is due before the report; it is over the {SINGLE_NAME_CEILING:.0f}% single-name ceiling (🔧 Tune-up)."`
     else today's "within tolerance … already conservative" text unchanged.
- `floor` = `_edec["floor_pct"]` when present else the existing `_overweight_floor`. Check `_dynamic_overweight_floor` equals `earnings_overweight_floor`; if not, report (don't change it).
- Priority, icon, headline, revision flag: unchanged.

### S4 Playbook, `earnings_advisor._recommend` (~:331-378) and `build_earnings_playbook` (~:594)

- In the window: `r = earnings_week_decision(weight, n_positions or 0, _sz)`; if `r` and `r["trim_shares"] >= 1` → REDUCE HIGH. `earnings_week` basis: today's two texts unchanged. `standing_in_window` basis: text 1 =
  `f"Position is **{weight:.0f}% of your portfolio** — under the {r['floor_pct']:.1f}% earnings-week line, but over the {SINGLE_NAME_TRIM_TRIGGER:.0f}% single-name trigger. An estimated ±{est_move:.0f}% earnings move on a ${market_value:,.0f} position = ±${...:,.0f} in a single session. **Trim to ≈{SINGLE_NAME_CEILING:.0f}% weight: sell {_sh(trim_sh)} (≈${trim_val:,.0f})** before earnings. Keep the core position for the upside scenario — just cap the binary risk."`
  text 2 = today's ceiling-branch institutional text (the `elif weight > SINGLE_NAME_TRIM_TRIGGER` one). Keep the existing `$` style of this module (check how its output is rendered: if it goes into markdown, the existing strings already decide the escaping; follow them exactly).
- A 0-share or None decision falls through to the next rules, as today. Outside the window: unchanged.
- Date parse (:594): replace `_datetime.strptime(earn_str, "%Y-%m-%d")` with `trim_sizing.days_to_earnings(earn_str, today)`; None → continue. Check `today`'s type there is a `date` (convert a datetime with `.date()` if needed; it must stay the ET date the caller passes).

### S3 Signals, `portfolio.rebalance_actions` (~:967-1012) + app.py render (~:17703-17730)

- New order inside the row loop:
  ```
  _ed = (earnings_days or {}).get(T); _in = in_earnings_window(_ed)
  _dec = earnings_week_decision(w, len(portfolio_df), shares) if _in else None
  if _dec is not None:          # U1b: no P&L condition in the window
      target/basis from _dec; floor_pct = _dec["floor_pct"]
  elif w > SINGLE_NAME_TRIM_TRIGGER and pnl > REBALANCE_TRIM_PNL_PCT:
      today's ceiling branch, basis "ceiling", floor_pct None
  else: no trim action
  ```
  (In the window a None decision implies w ≤ 18, so the elif can't fire there; no extra guard needed. Assert it in a test.)
- Title per basis: `earnings_week` → `"Oversized Going Into Earnings"`; `standing_in_window` → `"Over the Single-Name Ceiling Going Into Earnings"`; `ceiling` → unchanged `"Oversized Position with Strong Gain"`.
- Trigger per basis (`when` = "today" / f"in {_ed}d"):
  - `earnings_week`: `f"Weight {w:.1f}% with earnings {when}, above the {floor:.1f}% earnings-week line, so the earnings-week rule trims to {target:.0f}%"`
  - `standing_in_window`: `f"Weight {w:.1f}% with earnings {when}: under the {floor:.1f}% earnings-week line but over the {SINGLE_NAME_TRIM_TRIGGER:.0f}% single-name trigger, so the standing trim to {SINGLE_NAME_CEILING:.0f}% applies"`
  - `ceiling`: unchanged.
- New action keys: `"floor_pct"` and `"earnings_note"` (str or None), built here, not in app.py:
  - `earnings_week`: `f"Earnings {when} — sized by the same earnings-week rule the 🏠 Brief and 🔔 Catalyst Watch use."` (today's app.py sentence, moved)
  - `standing_in_window`: `f"Earnings {when}: under the {floor:.1f}% earnings-week line, so the standing {SINGLE_NAME_CEILING:.0f}% ceiling trim applies before the report; the 🏠 Brief and 🔔 Catalyst Watch make the same call."`
  - `ceiling`: None.
- app.py trim branch: the "This locks in a portion of the {pnl}% gain while keeping the core position." sentence only when `act.get("basis") == "ceiling"` (P&L may be ≤ 20 or negative in the window); replace the `if act.get("basis") == "earnings_week":` block with "append `act['earnings_note']` on a new markdown line when it is a non-empty string". No new comparison against a constant in app.py (`POLICY_DECISION_IN_RENDER`).
- Grep every consumer of `"_actions_cache"` and of trim actions (`type == "trim"`, `act["title"]`, `act["pnl"]`) in app.py / stock_analyzer / cron_runner and report any that assumes P&L > 20 or the old title. Don't change them unless they'd print something false; report first.

### History guard, `rec_events_capture._build_rebal_trim_rows` (~:121-147; not a gate file, DB-write path)

- Skip any trim action unless `weight > SINGLE_NAME_TRIM_TRIGGER and pnl > REBALANCE_TRIM_PNL_PCT` (the pre-existing trigger, constants imported). A skipped one emits no row AND joins neither `skipped_out` nor any other dedup set, so `single_name_concentration` rows are unchanged too.
- If any written field records `basis` (or the title), check that pre-existing rows keep their old value: a `standing_in_window` action that would have been a `ceiling` action before must write the same row as before. Report what you find.
- Docstring: one line on why (DoD #8: U1b must not change the history table's meaning).

### Tests (new and changed; plan §7)

Put C1 tests in `tests/test_trim_sizing.py` and new `tests/test_held_trim_owner.py`; C2 cross-surface tests in a new `tests/test_earnings_week_consistency.py`.

1. Floor table N = 0, 1, 5–12, 15 (expected values computed from the constants in the test, not literals).
2. `earnings_week_decision` boundaries: N=8 w=17.5 → None, 17.6 → earnings_week; N=7 w=18.0 → None, 18.1 → standing_in_window target 15; N=10 w=15.0 → None, 15.1 → earnings_week; N=5 w=22.0 → standing, 22.1 → earnings_week; `over_ceiling` at 15.0 True / 14.9 False; bad weight → None; 0 shares → trim_shares 0.
3. Cross-surface matrix: N ∈ {5…12} × w ∈ {floor−0.1, floor, floor+0.1, 15.0, 18.0, 18.1, 22.1} × days ∈ {−1, 0, 7, 8} × P&L ∈ {19, 21}: S1 action type + target, S4 action + target, S3 basis + target all equal what `earnings_week_decision` says in the window (and today's behaviour outside it). Build minimal port_df / held_data fixtures; reuse existing fixture helpers in the briefing / earnings tests where they exist.
4. "Earnings week never loosens": for every matrix cell, the target at days = 7 ≤ the target at days = 8 on S3 and S4 (no trim counts as 100).
5. S1 WATCH wording: never contains "already conservative" when w ≥ `SINGLE_NAME_CEILING`; unchanged text below it.
6. U5: an Act `risk` attribution card or a `critical_news` hold on the ticker keeps the earnings trim; a `stop_breach` exit or `deterioration_trim` removes it.
7. Time context: a Saturday `today` with a Monday report (days = 2) in S1 and S4; day 0 identical on S1/S3/S4; a timestamped earnings string `"2026-12-01 16:00:00"` read the same by S1 and S4 (S4 used to drop it).
8. History: `_build_rebal_trim_rows` output identical to the pre-change logic across the matrix (compute the expected rows with the old rule inside the test); a low-gain in-window trim produces no row and is not in `skipped_out`.
9. `held_trim_owner`: predicate True/False table (each kind in plan §3 and §7 item 5, review types incl. `trim_ticker`, WATCH, blank ticker, lower-case ticker); both dispositions' three states incl. `reduce_calls_ok=False`, day 0 "today", blank directive.
10. Replace `test_concentrated_book_band_pins_todays_behaviour` (tests/test_trim_sizing.py:400) deliberately with the new behaviour; say in its docstring that it was replaced by EW C2 and why.
11. app.py grep test: the Signals trim branch reads `earnings_note` and gates the gain sentence on `basis == "ceiling"`.

### Running checks (laptop, possibly on battery)

- Targeted pytest only, `-n 4` at most: the files above plus every existing test file that imports `trim_sizing`, `daily_briefing`, `earnings_advisor`, `portfolio`, `rec_events_capture`, and `tests/test_repo_hygiene.py`. Never the full suite (the commit hook runs it).
- `python scripts/check_antipatterns.py`; `python scripts/check_constants_documented.py`; `python -m py_compile app.py`.
- If `LLM_GOLDEN` or any golden fixture breaks, report it; don't regenerate without saying so.
- Don't commit, don't stage, don't touch docs other than this spec (none needed).

### Report back

Files changed with a one-line why each; the consumer greps asked above (reason_key readers, `_actions_cache` / trim consumers, rec_events basis field, `_dynamic_overweight_floor`); test counts; anything in the spec you found wrong at HEAD (stop and say so rather than improvising a policy choice).

---

## Round 2 — fixes from the Opus review (FIX-FIRST, 2026-11-24)

### Blocking

**R2-B1. A sector-breach flag can vanish with a dropped critical-news card.** `_consolidate_act_today` (daily_briefing ~:3147-3161) can put a same-ticker `risk` card's `risk_flags` (e.g. a sector_concentration HIGH flag + its rebalance-plan payload) on a `kind="critical_news"` primary. `decision_bucket._reconcile_act` (~:85-110) then drops that card whenever the ticker has a reduce card (now reachable via U5: the earnings TRIM_TO_TARGET no longer skips a critical-news ticker; already reachable via weak-large / TRIM_AND_TIGHTEN), and the risk-off fold (daily_briefing ~:4216-4223) drops it the same way. Fix the class with ONE helper in `decision_bucket.py` (not a gate file):

- `critical_news_as_risk(item) -> dict | None`: None when the item is not `kind="critical_news"` or carries no non-empty `risk_flags`; otherwise a NEW dict (input not mutated) that is a risk card: `kind="risk"`, `action` rebuilt in the SAME format risk cards already use (find where daily_briefing builds a risk card's `action`, ~:3058-3068 and the consolidation's `f"RISK — {n} flags on {ticker}"` for n > 1; reuse, don't invent a third format), the news "hold for now" directive / news-only fields dropped (inspect a critical_news item's keys and a risk item's keys; keep `ticker`, `priority`, `risk_flags`, weight / pnl fields, `_source`; drop what renders the hold directive). Put `"⚠ Also flagged on recent negative headlines."` style context into the risk card only if a risk card has a field for it; otherwise rely on the existing fold note on the reduce card.
- `_reconcile_act`: where it now drops a same-ticker critical_news card, append `critical_news_as_risk(it)` instead when that is not None (still add the ticker to `folded`, so the reduce card keeps its fold note).
- The risk-off fold (~:4216-4223): same: when `critical_news_as_risk(_it)` is not None, keep the converted card in `_kept_act` (still fold the headline into the risk-off card).
- Check the converted card renders in app.py's Act Today renderer exactly like a consolidated risk-primary card (grep how `kind == "risk"` / `risk_flags` render); report the render path you checked.
- Tests: (a) consolidated critical_news+risk(sector flag) + an earnings review TRIM_TO_TARGET on the same ticker → the Act bucket keeps a risk card with that flag AND the trim; (b) the same through weak-large TRIM_TO_TARGET; (c) the risk-off fold keeps the flag; (d) a critical_news card with no flags is still dropped exactly as before (fold note unchanged); (e) the helper doesn't mutate its input.

**R2-B2. False Brief WATCH sentence.** daily_briefing ~:3492-3497 (in window, no decision, weight ≥ `SINGLE_NAME_CEILING`): with score < `WEAK_CONVICTION_SCORE` the weak-large section issues an Act trim to 8% on the same name and the Tune-up fires no single-name card, so "nothing is due before the report" and "(🔧 Tune-up)" are false. New text, true in every case:
`f"Weight {weight:.1f}% is under the {_floor:.1f}% position-count-adjusted earnings-week line (N={_n_pos} positions), so the earnings-week rule sizes nothing before the report; it is over the {SINGLE_NAME_CEILING:.0f}% single-name ceiling (standing work)."`
Update the test that pins it.

### Non-blocking, fix now

- **R2-N1** portfolio.py ~:1007-1011 standing `earnings_note` → `f"Earnings {when}: under the {floor:.1f}% earnings-week line, so the standing {SINGLE_NAME_CEILING:.0f}% ceiling trim applies before the report (the same earnings-week rule the 🏠 Brief and 🔔 Catalyst Watch use)."` (a rule, not "the same call": the Brief skips a name with an exit / deterioration card and the Playbook returns EXIT first).
- **R2-N2** earnings_advisor.py ~:351-357 standing branch, second text (the lens) → `f"Institutional position sizing rule for binary events: the earnings-week line scales with how many positions you hold (N={n_positions or 0}: {r['floor_pct']:.1f}%), and earnings week never sizes looser than the week before, so a position over {SINGLE_NAME_TRIM_TRIGGER:.0f}% is still trimmed to {SINGLE_NAME_CEILING:.0f}% going into the report, regardless of conviction. "` followed by the existing "The reason is not that you're wrong — …" sentences unchanged.
- **R2-N3** held_trim_owner.py ~:100-101: `n_positions or 0` before `earnings_overweight_floor` (None must not raise); test.
- **R2-N4** fix-the-class date parse in files already in this change: `portfolio.py` ~:862 (`alerts()`) and `earnings_advisor.py` ~:791 → `trim_sizing.days_to_earnings(...)` (None → skip, same as today's except branch). Keep every other behaviour of those blocks; tests with a timestamped string. Leave app.py :17358 / 26830 / 27726 / 28186 / 28488 and `watchlist_advisor.py:101` (the lead logs them).
- **R2-N5** app.py Signals checklist ~:17344-17352: when `act["stop"]` or `act["gap"]` is None / NaN, render one `_check("⚪", "No stop level available for this name.", "#9ca3af")` line instead of the three gap branches (today a no-stop young holding reads "Stop at $nan … Not in immediate danger"). Don't touch the gap thresholds (pre-existing literals; out of scope).
- **R2-N6** app.py:7324 `_SYNTH_SCHEMA_VER` 22 → 23, prepending to its comment: "bumped: Signals trim actions carry floor_pct / earnings_note and in-window trims fire at any P&L (EW C2); was 22: …" (keep the rest of the comment).

Same run rules as round 1 (targeted pytest ≤ -n 4, antipattern + constants-doc checks, py_compile; no git add / commit; no docs). Report: what changed per item, the render path checked for R2-B1, test counts.
