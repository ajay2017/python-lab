# CpiYoyGap + CalLabels: date-keyed FRED changes and the calendar's "done" decision

**Status (2026-11-16): C1–C4 BUILT and reviewed** (implementer claude-sonnet-5-5; Opus reviewer claude-opus-5-5: SHIP, 0 blocking, over C1–C4 together; C5 docs synced the same day). Live check: §7 (12-10 after 08:30 ET; before then the regime banner reads "CPI 3.7% (Sep 2026)"). Reviewer non-blockers logged in the bug register (ApplyTransformOracle, FedLookbackConsts, FomcCompletedHeld). Earlier: **DESIGNED.** Opus planner (claude-opus-5-5): PROCEED WITH CHANGES. Owner accepted U1–U4 as recommended (2026-11-16). Next: C1 → implementer → (C2) → Opus reviewer over C1 + C2. Target: C1–C4 live before the CPI print on 2026-12-10 08:30 ET.

Bug-register rows: **CpiYoyGap**, **CalLabels** (`docs/bug-register.md`).

## 0. Problem

`macro_calendar._fred_obs` drops FRED's "." rows, so the observation dates are lost, and every change is computed by position (`vals[0]` vs `vals[12]` / `vals[1]` / `obs[2]` / `obs[3]`). FRED CPIAUCNS and UNRATE have no 2025-10 value (BLS never collected October 2025), so every CPI "YoY" from the 2025-11 print to the 2026-10 print was a 13-month change, and the regime's unemployment trend can straddle the gap. Today the harm is display only (band ELEVATED and the rate-cut hard gate hold either way), but it was wrong in Jan/Feb/Apr/Sep 2026 and `None` (an uncomputable CPI) skips the rate-cut hard gate, which would be a loosening.

## 1. Sibling sites (positional-lookback class), planner-checked at HEAD

`stock_analyzer/macro_calendar.py` (gate file):

| Site | Verdict |
|---|---|
| `_fred_obs` ~626-665 (drops "." rows) | **Fix:** add `_fred_obs_dated` (None on no key / HTTP error / exception; `[]` when FRED returns no rows). Keep `_fred_obs` for level-only regime signals. |
| `_apply_transform` yoy ~676 (CPIAUCNS) | **Fix** (the confirmed bug) |
| `_apply_transform` mom_pct ~679 (PPIACO, RSAFS) | **Fix** (a gap gives a 2-month change; no gap today) |
| `_apply_transform` mom_diff ~682 (PAYEMS) | **Fix** (same) |
| `_apply_transform` level ~674 | Not affected for the current value |
| `_fetch_fred` ~841-844 `obs_shifted = obs[1:]` (past-event "previous") | **Fix:** previous = reading date minus one step (1 month; 3 for GDP) |
| `_fetch_fred` ~881 `released = date <= today and actual` | **Fix:** released = FRED has posted the period (so a not-computable actual still reads ✓ Released). `entry_eligibility.py:109` unaffected in practice (event day: the 08:30 clock already lifts the hold). |
| `detect_macro_regime` CPI ~1111-1114 | **Fix:** §2 fallback |
| `detect_macro_regime` FEDFUNDS ~1091-1094 `obs[0]-obs[2]` | **Fix** by date (no gap; values unchanged). Keep the 2-month behaviour; the "3mo" comment / docstring mismatch is logged, not changed. |
| `detect_macro_regime` UNRATE ~1159-1162 `obs[0]-obs[3]` | **Fix.** UNRATE 2025-10 IS blank (lead, FRED CSV 2026-11-16). Base month missing → drop the signal, "not computable" pill. |
| `detect_macro_regime` DGS2/DGS10 ~1135-1139 | **Fix (one freshness per number):** use the newest date both series have. |
| HY ~1180-1183 | Not affected (level) |
| SPY/VIX ~1219/~1234 | Not this class (trading-day positions). Side finding: `period="25d"` + `iloc[0]` ≈ 17 sessions, not "20d"; log only. |

Elsewhere: `decision_quality.py:119` `iloc[max(0,i-12):i]` and `trade_analytics.py:249` `.tail(12)`: months with no trades are absent, so "12 months" spans more than 12 calendar months. **Log as OvertradeMonths, don't fix now** (also the bare `>= 2.0` near trade_analytics.py:253). `app.py` 3-period rolling grade and radar closure: not affected. No other `shift(12)` / `pct_change(12)` / `iloc[-13]` sites; no FRED fetcher outside macro_calendar.

Named only (bug register): the regime cache key `_macro_regime_{today}_{bool}` keeps a pre-08:30 CPI all day on 12-10 (the month label makes it visible); no clock staleness bound on CPI's newest month; a past event whose FRED update hasn't landed shows the prior period as its actual.

## 2. Regime CPI fallback (owner U1, U2)

New pure `fred_periods.cpi_yoy_for_regime(obs, max_fallback)`: reading month = newest FRED row (a "." row counts); for k = 0..`REGIME_CPI_FALLBACK_MAX_MONTHS`, the first month (reading month − k) with both its own value and the value 12 months earlier wins. Returns value, month, `basis` ("latest" | "fallback" | "unavailable") and the missing month. The regime dict gains `cpi_yoy_month` and `cpi_yoy_basis`; `cpi_yoy` is the value actually used.

| Case | Gate input | Rate-cut gate |
|---|---|---|
| True 12-month change for the newest month | that value | blocks Rate-Cut Optimism if > `REGIME_CPI_CONTROLLED_MAX` (2.5), as today |
| Base or newest month missing, a month within the bound has one | that month's true 12-month change | same; never `None` |
| Nothing within the bound, series down, no key | unknown | **U1 (owner: YES): Rate-Cut Optimism can't be selected** (existing exclusion branch, existing fall to neutral) |

Loosening check: missing data is strictly tighter (rate_cut has the loosest targets). Values are a correction: today 3.91 → 3.74 (Sep 2026), both ELEVATED and > 2.5, so identical outcome today. From 12-10 the date-keyed and positional actuals agree; only Prev differs until the Jan 2027 print.

**U2 (owner: 1):** new constant `REGIME_CPI_FALLBACK_MAX_MONTHS = 1` (investment-policy constant; architecture.md constants row). The regime's CPI fetch row count derives from it.

Wording:
- Regime banner tag (app ~45126): latest `· CPI 3.7%`; fallback `· CPI 3.7% (Sep 2026)`; unknown `· CPI unavailable`.
- Signal pill (`signals` tuple, also reaches the regime_stress prompt): latest `3.7%`; fallback `3.7% (Sep 2026; Oct 2026 has no 12-month figure)`; unknown `("CPI YoY", "unavailable (Rate-Cut Optimism can't be selected)", "⚠️", None)`.
- Calendar and Post-Event value, month AFTER the number: `Prev: **CPI YoY: +3.74%** (Sep 2026)`.
- Not computable: `Prev: **CPI YoY (Oct 2026): no 12-month figure**: FRED has no Oct 2025 value` (true for any gap; never "never collected").
- Post-Event with a not-computable Actual: `actual` stays `None`, no auto scenario, existing manual selectbox.
- `regime_stress.py:137`: append ` (Sep 2026, newest month with a 12-month figure)` only on fallback; pass `cpi_yoy_month` / `cpi_yoy_basis` through at :68. Golden fixture unchanged (no month key).
- Crons / emails: none render CPI or the regime; headless_alert_engine reads only date / impact / category / `released`.

**U3 (owner: doc note only):** `daily_regime.cpi_yoy` rows from Dec 2025 until the ship date are 13-month figures; rows from the ship date until 12-10 use Sep 2026. Note in architecture.md's daily_regime row + bug register. No DDL (`load_daily_regime` has no reader; a new column would break the EOD upsert if code shipped before DDL).

## 3. Calendar wording (owner U4: accepted as proposed)

- A same-day event moves to ✅ Completed once FRED confirms it OR its release time (e.g. 08:30 ET) has passed; unknown time on the same day = not done.
- Its Completed tag reads "today", never "0d ago".
- "this week" = the current Mon–Fri trading week (Sat/Sun visit: the coming week); the week after = "next week". Compare Monday dates, never ISO week numbers.
- Every FRED value shows its reading month.
- "Next major event" and the KPI counts use not-done events only; the 📊 Post-Event Results filter uses the same `is_done`.

## 4. Chunks

**C1. Pure core (gate; unwired, no live change).** New `stock_analyzer/fred_periods.py`: `parse_dated_obs` (FRED JSON → `(date, float|None)`; "." → None; an absent row = "."), month arithmetic, `change_by_period(obs, transform, ref, step_months)` (status `ok|base_missing|ref_missing|no_data`; zero base = not computable), `current_and_previous`, `cpi_yoy_for_regime`, `format_period` ("Oct 2026" / "Q3 2026"). Constant `REGIME_CPI_FALLBACK_MAX_MONTHS = 1` in constants.py. Add `fred_periods.py` to `_GATE_FILES` (95 → 96; it computes the rate-cut gate's input) with its reason in docs/review-policy.md; architecture.md constants row. Tests: tests/test_fred_periods.py.

**C2. Wire into macro_calendar (gate).** `_fred_obs_dated`; `_fetch_fred` by date, adding `actual_period`, `previous_period`, `actual_note`, `previous_note` (None defaults in `build_macro_calendar` rows), keeping the exact `"LABEL: +x.xx%"` shape of `actual` / `previous`; `released` per §1; `_FRED_MAP["limit"]` becomes a row count (yoy ≥ 26; mom/level ~6), GDP `step_months: 3`; `detect_macro_regime` CPI fallback + U1, FEDFUNDS / UNRATE / DGS by date; regime_stress passthrough. Move the `_fetch_fred` tests that mock `_fred_obs` (tests/test_macro_calendar.py ~287-460) to `_fred_obs_dated`. **Opus review over C1 + C2.**

**C3. Pure calendar view (display).** New `stock_analyzer/econ_calendar_view.py`: `is_done(ev, now_et)` (date < today, or same day with `released` or `macro_release.release_passed(...) is True`), `split_events`, `kpis(events, now_et)`, `upcoming_day_tag`, `completed_day_tag`, `card_value_lines(ev)`, `fred_enriched_count` (counts notes too). Not a gate file. Tests: tests/test_econ_calendar_view.py.

**C4. app.py wiring (display).** Calendar KPI (~44620-44633), split (~44729), tags (~44746-44755, ~44820), both card value rows (~44772, ~44837); Post-Event `_past_events` filter (~45069) via `is_done`, Previous / Actual lines, regime banner CPI tag (~45126); User Guide calendar paragraph (~45990); docs/requirements.md Economic Calendar + regime F-IDs. No constants comparison in app.py. Fold into the C2 review if shipped together.

**C5. Docs (lead).** Close CpiYoyGap, CalLabels; add OvertradeMonths, the FEDFUNDS "3mo" wording, the SPY "20d" window, the same-day regime cache, CPI clock staleness, the stale past-event actual. architecture.md daily_regime note (U3), shipped-log, CLAUDE.md queue line.

## 5. Risks

1. **Parser hazard:** `macro_playbook._parse_number` (~667-684) takes the first number in the string; any month / note text before the value makes `classify_scenario` read 2026 as CPI (a bear / PROTECT read). Months and notes go in separate fields and render after the value. A test pins it.
2. `entry_eligibility.macro_block_map` and the Brief's `release_signature` keep their behaviour; a test pins that a not-computable same-day release lifts the hold no earlier than today.
3. `_GATE_FILES` 95 → 96; no session_state keys, so `coord_freshness` untouched.

## 6. Tests the build must include

**C1 fixture: FRED CPIAUCNS, transcribed by the lead 2026-11-16 from `fredgraph.csv?id=CPIAUCNS` (blank = no value):**

```
2024-09-01,315.301   2025-05-01,321.465   2026-01-01,325.252
2024-10-01,315.664   2025-06-01,322.561   2026-02-01,326.785
2024-11-01,315.493   2025-07-01,323.048   2026-03-01,330.213
2024-12-01,315.605   2025-08-01,323.976   2026-04-01,333.020
2025-01-01,317.671   2025-09-01,324.800   2026-05-01,335.123
2025-02-01,319.082   2025-10-01,          2026-06-01,333.952
2025-03-01,319.799   2025-11-01,324.122   2026-07-01,333.918
2025-04-01,320.795   2025-12-01,324.054   2026-08-01,334.980
                                          2026-09-01,336.940
                                          2026-10-01,337.511
```

UNRATE 2025-10-01 is also blank (same CSV source, same day); 2025-07 4.3, 2025-08 4.3, 2025-09 4.4, 2025-11 4.5, 2026-07 4.1, 2026-08 4.1, 2026-09 4.2, 2026-10 4.1.

- fred_periods (C1): Oct 2026 → base_missing (Oct 2025); Sep 2026 → 336.940 / 324.800 − 1 = +3.74%; Nov 2025 → vs Nov 2024 (315.493), differs from the positional result; regime → fallback, Sep 2026. A gap a year back vs a gap in the current month, for both "." rows and absent rows. Bound: k = MAX → fallback, k = MAX + 1 → unavailable. January boundary: yoy Jan 2027 → Jan 2026; mom Jan → prior December. The 12-10 print: with data through Nov 2026 the actual is the true 12-month change (equal to positional) and the previous is base_missing (`previous` None). Other transforms: a mom gap → not computable (not a 2-month change); GDP previous = −3 months; zero base. No-gap equivalence: date-keyed == old positional for every transform.
- macro_calendar (C2): offline `_fred_obs_dated` None vs `[]`. Gate fixture (risk-on: calm VIX, SPY up, calm HY): CPI latest 2.0 → rate_cut can win; exactly 2.5 → allowed; 2.51 → blocked; fallback 3.0 → blocked; fallback 2.0 → allowed; unavailable / series None → blocked (U1). Invariant: `regime == "rate_cut"` ⇒ numeric `cpi_yoy ≤ REGIME_CPI_CONTROLLED_MAX`. Parser guard: for every `actual` / `previous` string `_fetch_fred` can produce, `_parse_number` returns the value; a not-computable actual → `classify_scenario` None and the past event still `released` True.
- econ_calendar_view (C3, time-context matrix): same day 08:29 not done, 08:30 done (`>=`), unknown time not done, 20:00 ET done; DST: 2026-03-09 12:29Z vs 12:31Z (EDT), 2026-11-02 13:29Z vs 13:31Z (EST); Sat → Mon "this week", Sun → Mon TOMORROW; Tue 11-10 → Tue 11-17 "next week"; Fri → Mon "next week"; Wed 2026-12-30 → Fri 2027-01-01 "this week", Mon 2027-01-04 "next week"; KPI "Next major event" skips a released-today CPI and counts exclude it; Completed tag "today"; Post-Event filter excludes today's CPI before 08:30, includes it after.

## 7. Live check (12-10 after 08:30 ET)

Before 12-10: the regime banner reads `CPI 3.7% (Sep 2026)`. On 12-10 after 08:30: the CPI card sits in ✅ Completed with `Actual: CPI YoY: +x.xx% (Nov 2026)` and Prev `CPI YoY (Oct 2026): no 12-month figure`; the banner drops "(Sep 2026)" once its cache refreshes (next day at the latest).
