# Earnings-week trim consistency (EW + EW2)

**Status:** DESIGNED 2026-11-24 (Opus planner `claude-opus-5-5`, PROCEED WITH CHANGES). **Owner took every default 2026-11-24:** U1 (a) keep the standing trim to `SINGLE_NAME_CEILING` in earnings week for N ≤ 7; U1b yes; U2 (a) drop + caption; U2b (a) keep the card + earnings line, reword the Brief WATCH; U3 (a) defer + the earnings line on in-window rows; U4 (a) unchanged; U5 (a) skip only `_reduce_tickers`; U6 (a) separate small plan; U7 yes. No constant changes value. Nothing built; next C1 + C2 (implementer → one Opus review). Bug-register rows: EW, EW2.

Three groundwork premises were corrected by the planner: (1) the ⚖️ Rebalancer does NOT size to 15%: it sizes to equal weight (100/N) or the owner's custom targets, never checks earnings, and never defers its trims to a Brief reduce call (`_reduce_calls` / `risk_trim_set` gate only its adds); (2) the worst contradiction is on ONE surface: Home's own Brief can show an Act Today earnings trim to 10% and a 🔧 Tune-up "trim toward 15%" card for the same name in the same render, because `single_name_concentration` is in `_TUNEUP_RISK_TYPES` but not `_TUNEUP_TRIM_DRIVER_TYPES` (the comment justifying it is false); (3) no dedup may key on raw `reduce_call_tickers` / `_reduce_calls`: an Act Today `risk` card's ticker is an attribution (the largest holding of an over-ceiling sector), so keying on it would hide a real ceiling card (memory `card_ticker_semantics`). A fourth defect of the class (U5) and a day-8 → day-7 loosening in the Playbook were also found. No constant changes value under the recommended options.

## 1. The rule today (verified at HEAD)

- `trim_sizing.earnings_overweight_floor(n)` (trim_sizing.py:105-116) = `max(EARNINGS_OVERWEIGHT_TRIM_PCT, min(100/n + EARNINGS_OVERWEIGHT_TOLERANCE_PP, EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT))` = max(12, min(100/n + 5, 22)); n ≤ 0 → 12.
- `in_earnings_window(days)` (trim_sizing.py:143-145): 0 ≤ days ≤ `EARNINGS_IMMINENT_DAYS` (7); days are calendar days from the ET today.
- `earnings_week_trim(w, n, shares)` (trim_sizing.py:148-161): fires when w > floor (strict); trims to `EARNINGS_OVERWEIGHT_TRIM_TO_PCT` (10), whole shares floored.

| Constant | Value | constants.py line |
|---|---|---|
| `EARNINGS_OVERWEIGHT_TRIM_PCT` | 12.0 | 905 |
| `EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT` | 22.0 | 906 |
| `EARNINGS_OVERWEIGHT_TOLERANCE_PP` | 5.0 | 908 |
| `EARNINGS_OVERWEIGHT_TRIM_TO_PCT` | 10.0 | 909 |
| `EARNINGS_IMMINENT_DAYS` | 7 | 1139 |
| `SINGLE_NAME_CEILING` | 15.0 | 139 |
| `SINGLE_NAME_TRIM_TRIGGER` | 18.0 | 165 |
| `REBALANCE_TRIM_PNL_PCT` | 20.0 | 621 |
| `TOLERANCE_OK` / `TOLERANCE_WATCH` | 2.0 / 5.0 | 452-453 |
| `WEAK_CONVICTION_SCORE` | 55 | 493 |
| `WEAK_LARGE_TRIM_TO_PCT` | 8.0 | 927 |
| `EARNINGS_CRITICAL_DAYS` | 3 | 1148 |

Floor by position count: N=5 22.00 (capped) · 6 21.67 · 7 19.29 · 8 17.50 · 9 16.11 · 10 15.00 · 11 14.09 · 12 13.33 · ≥15 12.00. So the floor is above 18 for N ≤ 7, between 15 and 18 for N = 8–9, exactly 15 at N = 10, below 15 for N ≥ 11.

Weight basis is the same everywhere today: every surface reads `port_df["Weight (%)"]` = MV / ΣMV × 100, 0.1-rounded (portfolio.py:718-720); `risk_advisor`'s `_acct_f = pv / gate_denom` (risk_advisor.py:153-163) is 1.0 today (`gate_denom` = `portfolio.gate_basis` = ΣMV, portfolio.py:2541-2560, app.py:6745-6747; `total_val` = ΣMV, app.py:6719). The fix must not recompute weights on a second basis: coordinate by ticker and decision.

## 2. Every site that sizes or words a held-name trim in earnings week

| # | Surface | Site | Rule in earnings week |
|---|---|---|---|
| S1 | 🏠 Brief Review → Act Today | `daily_briefing._review_list` §2, :3409-3490 | Shared rule. Above the floor: TRIM_TO_TARGET 10% (review TRIM_TO_TARGET goes to the Act bucket, decision_bucket.py:36/136). Else WATCH "within tolerance … Existing sizing already conservative for the binary risk." Skips any ticker in `_act_tickers` (:3425, defined :3235): ANY Act card, including an attribution-only `risk` card (:3058-3068) or a critical-news hold. |
| S2 | 🏠 Brief 🔧 Tune-up | `risk_advisor` single-name card :904-979 → `daily_briefing._portfolio_tuneup` :413-453 → app.py:13487-13519 | Fires at w ≥ 15 with score ≥ 55 or unmeasured: "Trim by ≈(w−15)pp toward the 15% ceiling." No earnings check. Never deduped: not in `_TUNEUP_TRIM_DRIVER_TYPES` (:404); the comment at :399-403 wrongly says concentration cards "never restate a trim". |
| S3 | 📡 Signals | `portfolio.rebalance_actions` :967-1012 (app.py:7561-7564, ET `earnings_days`; render :17703-17730) | Only at w > 18 AND P&L > 20; then the shared rule if w > floor, else the 15% ceiling with "exceeds 18% threshold" and no earnings sentence. |
| S4 | 🔔 Catalyst Watch / 📅 Earnings Playbook | `earnings_advisor._recommend` :331-378 via `build_earnings_playbook` :561-643 (app.py:5683) | In the window: shared rule only. Days 8–30: `elif w > 18` → REDUCE to 15% (:357). For N ≤ 7 and w in (18, floor] it trims on day 8 and stops on day 7. Date parse `strptime(earn_str, "%Y-%m-%d")` (:594) differs from `trim_sizing.days_to_earnings` (`[:10]`): a timestamped date drops out of the Playbook only. |
| S5 | 🔗 Risk Analysis → 📋 Action Plan | app.py:18952+ (`_risk_advisor_recs_cache`) | Same 15% card as S2; the beta card has a "same action as an active Reduce/Exit call" note (app.py:19120-19160), the single-name card has none. |
| S6 | 🥧 Allocation → ⚖️ Rebalancing Advisor | `rebalancer.compute_drift` :39-94 / `build_rebalance_plan` :110-311; app.py:22730-22886, render :22981-23010 | Equal weight (:30-36) or custom; drift > 5pp TRIM, 2–5pp DRIFTING; sells `int(drift_$/price)`. No earnings check; trims not gated by `_reduce_calls`. `inst_lens` hardcodes "15%" / "5pp" (:286-293); the `_tr_pri` urgency decision sits in app.py:22984. Not in `_GATE_FILES`. |
| S7 | 📅 Calendar macro PROTECT | `macro_playbook._action_detail` :445-466 | Macro event, w > 15 → trim to 15%. Named only. |
| S8 | Brief macro PROTECTIVE_TRIM | daily_briefing ~:3700 | Excludes only `_act_tickers`; can sit beside an earnings trim. Named (U6). |
| S9 | Brief weak-large TRIM_TO_TARGET 8% (:3495-3606), stop profit-lock TRIM_AND_TIGHTEN (:3354-3380) | daily_briefing | Not deduped against the earnings item: two Act trims on one name. Named (U6). |
| S10 | rec_events capture | `rec_events_capture` :121-147 (`rebal_trim`, earnings-aware), :378-447 (`single_name_concentration`, mirror of S2, 15%) | History table; decision U4. |
| S11 | Emails | `headless_alert_engine.py:770-772` (Brief with `risk_recs=[]`, picks only); notify.py | No email site. |

### Worked examples ($100k book, price $100, score ≥ 55, earnings in 3 days)

- **A. N=8, 19.0% (190 sh), +25%. Floor 17.5.** S1 Act Today TRIM_TO_TARGET, sell 90 sh to ≈10% (priority medium, 3 ≤ `EARNINGS_CRITICAL_DAYS`). S3 90 sh "→ ≈10%" + earnings sentence. S4 REDUCE HIGH 90 sh. S2 (same Home render) and S5 "Trim by ≈4pp (≈$4,000) toward the 15% ceiling." S6 equal weight 🔴 TRIM +6.5pp "sell ~65 shares ≈ $6,500 … 19.0% → 12.5%". Three sell sizes for one name: 90 shares, ≈$4,000, 65 shares.
- **B. N=8, 16.0%, +25%.** S1 Awareness WATCH "within tolerance (≤ 17.5%) … already conservative". S3 nothing. S4 no REDUCE. S2 / S5 "Trim ≈1pp (≈$1,000) toward 15%." S6 🟡 DRIFTING +3.5pp "sell ~35 shares … → 12.5%". Home says "conservative" and "trim" about one name.
- **C (EW2). N=7, 19.0%, +25%. Floor 19.29.** S1 WATCH "within tolerance". S4 no REDUCE in the window, but on day 8 it was REDUCE 40 sh to 15%. S3 40 sh to 15%, no earnings sentence. S2 ≈4pp toward 15%. S6 DRIFTING +4.7pp, 47 sh to 14.3%.

## 3. Fix shape

**Principle.** In earnings week a held name's size is decided once, by one pure rule, on the highest-priority surface: Act Today. Earnings week never sizes looser than the week before. Standing surfaces (Tune-up, Risk Analysis, Rebalancer) defer to a Brief sell that names that ticker, with a visible line; never a silent drop.

**Rule (recommended U1a + U1b).** New `trim_sizing.earnings_week_decision(weight, n_positions, shares)`: `basis="earnings_week"`, target `EARNINGS_OVERWEIGHT_TRIM_TO_PCT`, when w > floor; else `basis="standing_in_window"`, target `SINGLE_NAME_CEILING`, when w > `SINGLE_NAME_TRIM_TRIGGER` (reachable only for N ≤ 7); else None. Also returns `floor_pct`, `trim_shares` (floored), `over_ceiling` (w ≥ `SINGLE_NAME_CEILING`). `earnings_week_trim` stays as the first branch for backward compatibility.

- **Brief (S1):** sizes every in-window decision; skips only names the Act card actually reduces (`_reduce_tickers`: exits + deterioration_trim, the set the stop and weak-large sections use), not `_act_tickers` (U5). WATCH text no longer says "already conservative" when `over_ceiling`: "under the X% earnings-week line (N=…), so nothing is due before the report; it is over the 15% single-name ceiling (🔧 Tune-up)."
- **Playbook (S4) and Signals (S3):** the same decision; inside the window Signals fires on it regardless of P&L (U1b), title and trigger per basis. Outside the window both unchanged.
- **Tune-up (S2):** the single-name card is dropped when its ticker carries a Brief sell that names it; the result gains `portfolio_tuneup_deferred` (ticker → reason); app.py renders one caption "X's single-name size is handled by today's Act Today call." When the card stays and the ticker is in the window with no earnings trim (case B), one line: "Earnings in Nd: below the X% earnings-week line, so nothing is due before the report; this is standing work."
- **Risk Analysis (S5):** same disposition; an info line on the single-name card via the verified-plus-fresh `_reduce_calls` idiom.
- **Rebalancer (S6):** a trim row for a ticker under a Brief sell that names it becomes a deferred row (quotes the Brief's directive, no own share count, excluded from "Trim needed" / "Trims would move" / `trim_action_needed`, under a banner); rows in any status are listed so a custom target reading OK still shows the Brief's call. When `_reduce_calls` is unverified or not fresh, nothing is deferred (fail toward showing the sell, caption extended to trims). In-window names with no Brief trim get one earnings line.

**"A sell that names the ticker"** = one pure predicate in a new gate module `stock_analyzer/held_trim_owner.py`: True for act kinds in `EXIT_KINDS` plus `deterioration_trim` and `risk_off_derisk`, and review types in `_ACT_REVIEW_TYPES` via `ticker` or `action.trim_ticker`; never for kind `risk` (attribution), `critical_news`, `premortem_triggered` or macro act items. The module also holds `tuneup_single_name_disposition(...)` and `rebalancer_trim_disposition(...)` returning a state (`brief_owns` / `earnings_note` / `show`) plus text, so app.py stays render-only.

## 4. Chunks

- **C1 (decision-bearing):** `trim_sizing.py` (gate) + new `held_trim_owner.py` (gate; `_GATE_FILES` + docs/review-policy.md in the same commit) + tests. `earnings_week_decision`, the predicate, the disposition functions. Pure-additive, reviewed (gate files).
- **C2 (decision-bearing):** `daily_briefing.py`, `earnings_advisor.py`, `portfolio.py` (gate) + the app.py Signals text branch. Wire S1/S3/S4 (EW2); S1 skip set → `_reduce_tickers` (U5); S1 WATCH wording; S4 date parse → `trim_sizing.days_to_earnings`; Signals per-basis title/trigger. History guard: the `rec_events_capture` `rebal_trim` population stays the pre-existing trigger (w > 18 and P&L > 20); low-gain in-window trims from U1b are filtered out with a pinning test (DoD #8). Replace `test_concentrated_book_band_pins_todays_behaviour` deliberately. One reviewer round for C1 + C2.
- **C3 (coordination):** `daily_briefing._portfolio_tuneup` + the false comment; app.py Tune-up caption and Risk Analysis single-name note (render via C1 functions). Brief result key read with a default (an old locked snapshot renders nothing extra). Reviewer.
- **C4:** `rebalancer.py` joins `_GATE_FILES` (U7; reason row in review-policy.md); app.py:22821-22995 wiring (ET `earnings_days_by_ticker(held_data, _today_et())`; verified-plus-fresh `_reduce_calls` via `coord_freshness.classify`); deferred rows + banner; `inst_lens` interpolates `SINGLE_NAME_CEILING` / `TOLERANCE_WATCH`; move the `_tr_pri` decision into rebalancer.py. Reviewer.
- **C5 (docs, Opus lead):** requirements row :78, F-25 / F-25a, F-12, F-13, a new G-ID (verify the next free one; G-32 is taken); architecture.md; User Guide (Tune-up, Rebalancer, earnings-week bullets); bug-register EW / EW2; queue; shipped-log; CLAUDE.md "Earnings-week trims". No constants-table change.
- **C6 (only if U6 = b):** one trim per ticker in the Brief Review list; the stricter target wins, the other becomes a `why` line.

One chunk per deploy, a screenshot check each (laptop only). Implementer builds; lead specs and verifies.

## 5. Owner decisions (defaults marked)

- **U1 (EW2): in earnings week, N ≤ 7, w in (18, floor].** **(a) DEFAULT:** keep the standing trim to `SINGLE_NAME_CEILING` on Brief (Act Today), Playbook and Signals (earnings week never loosens; the same trim the Playbook gives on day 8). (b) trigger = min(line, `SINGLE_NAME_TRIM_TRIGGER`), target 10% (tighter; largely undoes the N-aware 22% cap). (c) only the earnings line sizes, Signals stops trimming (loosening; keeps the day-8 → day-7 loosening). (d) leave. None changes a constant value; (b) via `EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT` 22 → 18 would — not recommended.
- **U1b:** inside the window Signals fires on the shared decision regardless of P&L (drops `REBALANCE_TRIM_PNL_PCT` in the window only). **DEFAULT yes** (history protected by the C2 capture guard).
- **U2: Tune-up / Risk Analysis single-name card when the Brief already sells that name** (never on a `risk` attribution). **(a) DEFAULT drop, with a caption line.** (b) keep with a "covered by today's Act Today call" line. (c) earnings-week sells only.
- **U2b: in the window, under the line, over 15% (N = 8–10).** **(a) DEFAULT keep the ceiling card + the earnings line; reword the Brief WATCH.** (b) hide the card until after the report (loosening).
- **U3: Rebalancer trim rows for a name the Brief sells.** **(a) DEFAULT defer** (Brief's directive, no own shares, banner). (b) annotate only. (c) earnings-week only. Plus the earnings line on in-window rows: **default yes**.
- **U4: `rec_events` `single_name_concentration` capture.** **(a) DEFAULT unchanged**; document that folded-into-Act days still record the ceiling condition. (b) mirror the deferral (changes history meaning from a date; pair with the queued "record earnings trims as `rec_events`" design + a version marker).
- **U5: Brief earnings skip set.** **(a) DEFAULT skip only names the Act card reduces (`_reduce_tickers`)** — today a sector `risk` attribution card or a critical-news hold silently removes the Brief's earnings trim while the Playbook and Signals trim to 10%. (b) leave.
- **U6: two trims on one name inside the Brief Review list in earnings week** (earnings 10% + weak-large 8% / profit-lock / macro PROTECTIVE_TRIM). **(a) DEFAULT name it now, separate small plan.** (b) fold in as C6.
- **U7: add `stock_analyzer/rebalancer.py` to `_GATE_FILES`** (it sizes sells). **DEFAULT yes.**

## 6. Risks / coordination

- **Attribution tickers:** `decision_bucket._is_reduce` counts kind `risk`; the Act `risk` card's ticker is root_tickers[0] (daily_briefing.py:3058-3068). Raw `reduce_call_tickers` / `_reduce_calls` must not drive any deferral: use the C1 predicate.
- **Offline / freshness:** Risk Analysis and the Rebalancer defer only when `_reduce_calls` is ready, `_daily_brief_offline` is False and `coord_freshness.classify(..., "_reduce_calls")` is fresh; otherwise their own trim + a caption (fail toward the sell).
- **One freshness per number:** a deferred row quotes the Brief's numbers and never mixes them with its own drift shares from a newer book.
- **Mixed bases:** every input stays on `Weight (%)` / ΣMV; if `_acct_f` ever becomes ≠ 1 the deferral still holds (keyed on ticker and decision). Test that S1/S3/S4/Tune-up read the same column.
- **Calm posture:** earnings and standing-in-window trims sit in Act Today (time-boxed); Tune-up and Rebalancer stay standing work.
- **New Act items from U1a (N ≤ 7 only):** raise the Act Today count, `_reduce_calls` (suppressing adds on those names, as intended), the Signals headline and the dock digest. Expected.
- **Day 0 (#25 class):** a before-open reporter on day 0 still gets a "before the report" trim after the report; out of scope, but every surface must say the same thing on day 0. Coordinate with #25's wording call.
- **Interim (before this ships):** MRVL (12-01) is in the window from 11-24, SNOW (12-02) from 11-25 (Yahoo dates; CRWD's Yahoo 11-23 is past, so standing rules apply). Floor 17.5% at N=8, 16.1% at N=9. If either is above the floor, Act Today's 10% trim is the call; the Tune-up ≈15% and Rebalancer sizes for that name are the defect.
- **Siblings named, not fixed here:** S7 macro_playbook 15% PROTECT; S8 / S9 (U6); `rebalancer.compute_drift` `int(shares)` (:87, display only); `forward_sim` / `portfolio_qa` int-`Shares` readers (don't size earnings-week trims); `daily_briefing` `is_critical = gap <= 3.0` (stop class).

## 7. Tests the build must include

1. Floor table for N = 0, 1, 5–12, 15, expected values computed from the constants.
2. Cross-surface agreement matrix: N ∈ {5…12} × w ∈ {floor−0.1, floor, floor+0.1, 15.0, 18.0, 18.1, 22.1} × days ∈ {−1, 0, 7, 8} × P&L ∈ {19, 21}; S1 action/target, S4 action/target, S3 basis/target (in window) and S2 card presence all equal `earnings_week_decision`.
3. "Earnings week never loosens": target at days = 7 ≤ target at days = 8, Playbook and Signals.
4. Boundaries: N=10 w=15.0 (earnings off, strict >; Tune-up on, ≥; card kept with note); N=10 w=15.1 (earnings trim; card deferred); N=8 w=17.5 → WATCH, 17.6 → trim; N=7 w=18.0 → none, 18.1 → standing trim to 15.
5. Tune-up dedup: dropped for a review TRIM_TO_TARGET or exit / deterioration_trim / risk_off on that ticker; KEPT when the only same-ticker card is a `risk` attribution, critical-news hold or earnings WATCH; deferred list populated; beta / sharpe dedup unchanged.
6. Brief WATCH wording: never "already conservative" when w ≥ `SINGLE_NAME_CEILING`; unchanged below.
7. U5: an attribution `risk` card or critical-news hold keeps the earnings trim; a stop-breach exit removes it.
8. Rebalancer: deferred row has no shares and is excluded from `trim_pct` / `trim_action_needed`; not deferred when unverified / offline / stale; an OK custom-target row for a sold name appears in the banner; add gating unchanged; `inst_lens` has no "15%" / "5pp" literal.
9. Time context: Saturday visit with a Monday report (days = 2); day 0 identical on every surface; a 21:00 ET / 02:00 UTC "today" passed explicitly; a timestamped earnings string read the same by Brief and Playbook; a SELL logged after the Brief build (stale coord epoch → no deferral).
10. History: `rec_events` `rebal_trim` rows unchanged by U1b; `single_name_concentration` rows unchanged (U4a).
11. app.py grep tests: Signals trim branch renders per-basis text; Rebalancer and Risk Analysis call the `held_trim_owner` functions behind the verified-plus-fresh idiom.

Gate membership at HEAD (104): `trim_sizing.py`, `risk_advisor.py`, `daily_briefing.py`, `earnings_advisor.py`, `portfolio.py`, `home_risk_synthesis.py`, `constants.py` are members; `rebalancer.py`, `rec_events_capture.py`, `decision_bucket.py`, `macro_playbook.py` and app.py are not.
