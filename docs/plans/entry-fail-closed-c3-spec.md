# C3 spec: an earnings date that can't be confirmed holds new buys (with StaleEarningsDate)

**Status:** DESIGNED 2026-11-27 (Opus planner `claude-opus-5-5`, PROCEED WITH CHANGES; anchors read at HEAD `4c5ba332` plus the working tree). Parent plan `docs/plans/entry-fail-closed.md` (U1 / U2, §2 (ii)/(iii), C1 reviewer notes 1–5). Bug rows: StaleEarningsDate, EarnUnknownPass, CatalystEarnDates (mostly closed by C3a). The owner waived the "few days of log" precondition (2026-11-27). Day 1 (11-27) read 88 date / 0 no_date / 0 failed / 1 n/a, but that count can't see StaleEarningsDate (correction 2). Required changes: (1) resolve the earnings date ONCE in the producer (`bundle_loader.load_bundle`) so every reader agrees; (2) a read-only FMP probe P1 before the hold goes live (plumbing values only; the policy stays fail-closed whatever P1 shows). **Owner took every recommended answer Q1–Q9 on 2026-11-27** (`EARNINGS_REPORTED_CLEAR_DAYS` = 45, `EARNINGS_XCHECK_HORIZON_DAYS` = 90); the owner runs P1 (`scripts/earnings_xcheck_probe.py`) and pastes the output before C3a.

## What is true at HEAD (corrections to the brief)

1. Every bundle carries `earnings_status` (bundle_loader.py:139-163, :495). The orchestrator fallback (orchestrator.py:272-295) never fires (`DATA_PROVIDER_ORDER = ["yahoo_finance", "finnhub"]`, Finnhub has no `earnings`), so the date is Yahoo `t.calendar` alone (yfinance_provider.py:298-313).
2. The 11-27 log's "0 failed" doesn't measure StaleEarningsDate: `buy_check_state.earnings_state` reads any parseable date as `date`, incl. a past one (buy_check_state.py:78-82). CRWD's 11-23 counted as a good read.
3. Yahoo's "Earnings Date" can hold two dates (an estimated range); only `dates[0]` is kept (yfinance_provider.py:302-304), so [past, future] reads as past.
4. FMP serves the earnings calendar AND the profile refill (`DATA_INFO_BACKFILL_ORDER = ["fmp"]`). The calendar is display-only today ("never a gate source", app.py:4559-4567) and a failed read returns `[]` (orchestrator.py:317-319, a baselined `or []`); a gate needs a None-on-failure variant. The app already fetches [today, today+90] (app.py:5706-5708, :16291-16294), so a 90-day window works on the free plan; whether rows are capped is unknown (the "MU case", app.py:4565-4566) → P1.
5. `fetch_next_earnings` (yfinance_provider.py:366-380) also returns past dates; via the Catalyst backfill (app.py:5712) it brings the stale date back, and the earnings-move forecast fallback reads it (earnings_move_forecast.py:64-69).
6. Plan §2 put `earnings_unknown_picks` straight after `earnings_waiting` (daily_briefing.py:1979-1989), BEFORE the G-06 beta block (:1990-2025), so a name both earnings-unknown and beta-blocked would lose its G-06 ledger row. The bucket goes AFTER the beta block, before the conviction tier (≈ :2026).
7. Plan §2 also added `reconcile_signals(earnings_known=False)` inside Grow Today; that changes `verdict_reconciled` (feeds `JUDGMENT_VERDICT_SIGNAL_MAP`, constants.py:2057, and the emails' "go" filter, cron_runner.py:2477, :2938). C3 wires `earnings_known` to the Market Scanner badge only (app.py:26510); Grow Today holds on the pure check.
8. Held adds on 📈 Analysis and ⚖️ Rebalancer already defer to Grow Today's add call (`sizing_path._PAUSE_BUCKETS` includes `entry_held_adds`, sizing_path.py:269-281): no extra wiring.
9. The redeploy headline never checks earnings (entry_posture.py:268-306, RedeployHeadlineGates): a "🎯 genuine redeploy entry" naming CRWD on 11-27 is possible today.
10. `_SYNTH_SCHEMA_VER`: take HEAD + 1 at ship time (sell-check C6 is taking 39).
11. Fixture churn under "absent key = hold": 22 Watchlist ENTER_NOW test files (1 sets `earnings_status`), 46 Grow Today / `build_daily_briefing` files (0). Taken: no production stamp = today's behaviour, guarded by a producer pin + an AST pin.
12. Every decision reader reads `bundle["earnings"]` from `load_bundle` bundles (`held_data`, `grow_composites` → `earnings_lookup`, daily_briefing.py:4755-4765), so resolving in the producer fixes the class.

## Design

### Resolution rule (NEW pure gate module `stock_analyzer/earnings_date.py`)

`resolve(*, is_fund, stale_copy, yahoo_dates, yahoo_status, xcheck, today) -> dict`:
`{"state": "not_applicable"|"date"|"reported"|"no_date"|"failed", "date": iso|None, "source": "yahoo"|"fmp"|"both"|None, "reason": None|"read_failed"|"past_unconfirmed"|"past_unchecked"|"stale_copy", "yahoo_date": iso|None, "last_reported": iso|None, "disagree": {"yahoo": iso, "fmp": iso}|None}`

Rules, in order:
1. `is_fund` → `not_applicable`.
2. `stale_copy` (Supabase last-good copy) → `failed`, `stale_copy`.
3. Yahoo dates ≥ today and FMP upcoming dates in [today, today + `EARNINGS_XCHECK_HORIZON_DAYS`] (only when `xcheck.state == "ok"`): if either has one → `date` = the EARLIEST (Q2); `source` "both" when they agree, else the supplier; `disagree` when both have a date and differ.
4. Cross-check ok, FMP shows a report with an ACTUAL EPS in [today − `EARNINGS_REPORTED_CLEAR_DAYS`, today − 1], no future date → `reported` (passes; NVDA).
5. Any Yahoo date past and none future → `failed`: `past_unconfirmed` (cross-check ok) / `past_unchecked` (cross-check unreadable) (Q1 / Q4).
6. `yahoo_status == "no_date"` → `no_date` (passes, U2).
7. Otherwise → `failed`, `read_failed`.

Helpers: `yahoo_dates(raw) -> list[str]` (sorted unique ISO; list / str / None; never raises); `xcheck_for(index, ticker)` (upper-case + "." ↔ "-" variant; None / not-ok index → `{"state": "failed", "upcoming": [], "reported": None}`); `display(resolved, today) -> {"date_text", "days", "urgency", "note"}` (urgency imminent / soon / ahead / reported / unconfirmed / none / n/a; cut-offs `EARNINGS_IMMINENT_DAYS` and `EARNINGS_URGENCY_SOON_DAYS`, replacing literal 7 / 14 at app.py:5821-5823, no value change); `held_unconfirmed_line(ticker, resolved, weight, floor_pct)` (Q6). Pure: no clock, streamlit or db.

Why earliest wins: the buy hold switches on at the earliest plausible report; a wrong early date costs a few extra held days, never a missed report. The same date drives the G-33 trim.
Why an actual EPS is required for `reported`: a shared wrong vendor estimate on both sources would otherwise pass a phantom report. Cost: the morning after a report, a name with no future date stays held until FMP fills the actual (usually same / next day).

### Cross-check fetch (NEW gate module `stock_analyzer/earnings_xcheck.py`; I/O, never raises)

- `calendar_index(today) -> {"state": "ok"|"failed", "as_of_day", "fetched_at" (ET iso), "error", "by_ticker": {T: {"upcoming": [iso], "reported": iso|None}}}`.
- Windows past [today − R, today − 1] and future [today, today + H], chunked by `EARNINGS_XCHECK_CHUNK_DAYS` (from P1). ANY failed chunk → the whole index `failed` (a partial index never reads ok).
- `reported` keeps only rows with a finite `eps_actual`.
- Process-wide cache keyed by ET date (refetch at ET midnight); success kept `EARNINGS_XCHECK_CACHE_TTL_SEC`, failure `EARNINGS_XCHECK_FAIL_RETRY_SEC`; a `threading.Lock` so `load_all`'s threads fetch once.
- `_fetch_rows(from, to)` = the one network seam; `last_index()` / `log_line(index)` for the cron log. No FMP key → `failed`, "FMP_API_KEY not set".
- `orchestrator.get_earnings_calendar_rows_or_none(from, to) -> list | None` (first `DATA_EARNINGS_CALENDAR_ORDER` provider; None on exception / no provider; ignores the FMP soft cap like today).
- `fmp_provider.earnings_calendar` (:373-393) gains `"eps_actual"` (from `epsActual`; P1 confirms). Additive.
- Cost: per process per TTL ≈ 1–2 past + 1–3 future calls; cron lanes ≈ 15–25 calls / day; app ≈ 8–20; inside 250 / day. Each call ≤ 10 s (`_TIMEOUT`). First Home load after a refresh pays ≈ 2–5 s once. Optional C3c: `_cached_held_earnings_dates` reads its FMP leg from the index.

### Producer wiring (bundle_loader.py, gate file; after `_asset_type`, :423-428)

```
_today = today_et()
_xc = earnings_xcheck.calendar_index(_today)
_res = earnings_date.resolve(
    is_fund=(_asset_type == ETF) or (not _quote_type and ticker.upper() in _KNOWN_FUNDS),
    stale_copy=_from_cache,
    yahoo_dates=earnings_date.yahoo_dates(bundle.get("earnings_dates") or bundle.get("earnings")),
    yahoo_status=_earnings_status, xcheck=earnings_date.xcheck_for(_xc, ticker), today=_today)
```
In a try; on exception the stamp is `{"state": "failed", "reason": "read_failed", "date": None, ...}`. Return keys: `"earnings": _res["date"]` (resolved NEXT date; None when unknown or just reported), `"earnings_yahoo"` (raw), `"earnings_status"` (unchanged raw stamp), `"earnings_resolved": _res`. `_KNOWN_FUNDS = frozenset(portfolio.SECTOR_ETF.values()) | frozenset(FACTOR_ETF_TICKERS.values())` (C1 note 2; residual: an `etf_registry` fund outside both rosters with a missing quoteType still reads `failed`, named in the cron log).
Providers: `yfinance_provider.bundle` adds `"earnings_dates"` (full parsed list); `next_earnings` returns the earliest date ≥ today (ET) else None (sibling fix for Catalyst + the earnings-move fallback); orchestrator fallback parses with `earnings_date.yahoo_dates` before stamping "date" (C1 note 4).

### The buy hold (buy_check_state.py, gate file)

- `earnings_state(bundle)`: reads `earnings_resolved` when a dict (adds `STATE_REPORTED`, returns `reason`); else the C1 rules (log only).
- `CHECK_EARNINGS_UNKNOWN = "earnings_unknown"`.
- `earnings_unknown_hold(bundle, earnings_days) -> dict | None`: non-dict / no `earnings_resolved` → None (legacy); `not_applicable` / `no_date` / `reported` → None; `date` with days None → hold "the earnings date couldn't be placed on the calendar"; `date` with days < 0 → hold "the earnings date on file ({Mon Nov 23}) has passed since the data loaded"; `failed` → hold by reason: `read_failed` "the next earnings date couldn't be read"; `past_unconfirmed` "the earnings date on file ({Mon Nov 23}) has passed and the next one couldn't be confirmed"; `past_unchecked` "the earnings date on file ({Mon Nov 23}) has passed and the earnings calendar couldn't be read to confirm the next one"; `stale_copy` "a saved copy of the data can't show the earnings date". Shape `{"kind": "hold", "check": "earnings_unknown", "gate_id": None, "reason", "clears_when": "the next earnings date is confirmed (re-checked each time the data reloads, about every 30 minutes)", "gate_value": None, "gate_threshold": None}`; dates `f"{d:%a %b} {d.day}"`.
- `earnings_pass_note(bundle) -> str | None`: `no_date` "no earnings date announced yet"; `reported` "reported on {Wed Nov 19}; the next date isn't on the calendar yet"; `disagree` "Yahoo shows {Thu Dec 3}, the FMP calendar {Tue Dec 1}; the earlier date is used."

## Wiring sites (HEAD)

**C3a (resolution; changes which date every reader sees):** providers yfinance_provider.py:298-313, :366-380; orchestrator.py:272-295 + new function after :320; fmp_provider.py:373-393; bundle_loader.py:139-163, :423-428, :494-495; buy_check_state.py:68-88. Cron log: headless_alert_engine.py:1057-1070 `earnings_states_summary` (add `reported`, split `failed` by reason, list `no_date` tickers, C1 note 1); cron_runner.py:502-519 `_log_watchlist_earnings_read` → "watchlist earnings read: N date, N reported, N no_date (X), N failed (past_unconfirmed: CRWD; read_failed: …), N n/a" + `earnings_xcheck.log_line(...)` "earnings cross-check: ok, N upcoming / M reported rows (fetched 9:44 AM ET)" or "earnings cross-check: couldn't be read (…); names whose date on file has passed are held". Readers fixed with no edit: watchlist_advisor.py:99-104, :447-453; daily_briefing.py:649-671, :1143-1148, :3937-3945, :4755-4765, :4906; trim_sizing.py:135-142 (G-33); earnings_advisor.py:619-630; portfolio.py:876-895; evening_debrief.py:323-335; revision_flag.py:179; rec_events_capture.py:579; app.py:5712, :16297, :17860, :26490, :26634, :27684, :29343, :33898.

**C3b (the hold):**
- entry_eligibility.py :334-343 `check_earnings` (imminent policy hold first, unchanged; then `earnings_unknown_hold(ctx["bundle"], d)`; then the pass note as `{"kind": "passed", "check": "earnings", "detail": note}`); :355 `_ABSTENTION_CHECKS` += `"earnings_unknown"`; :721-735 `HELD_KIND_LABELS["earnings_unknown"] = "earnings date unconfirmed"`; :579-644 `watchlist_banner` new key `"earnings_unknown"`: `{"title": "Earnings date unconfirmed — new entries held", "body": "{n} name(s) would otherwise be Ready: X, Y. Their next earnings date couldn't be confirmed, and a buy just before a report is what this check stops. Clears when the date is confirmed (re-checked each time the data reloads, about every 30 minutes)."}` (names held only by it, minus `exclude`).
- watchlist_advisor.py:901: "No imminent earnings risk" → the pass note when there is one.
- daily_briefing.py `_grow_today`: new list `earnings_unknown_picks` (near :1545) in both early-return dicts (≈ :1397-1408, :1457-1470) and the final dict (≈ :3093-3121); check right after the beta block's `continue` (≈ :2025): `_eu = buy_check_state.earnings_unknown_hold(_comp_data, xref.get("earn_days"))`; when held append `{ticker, sector, score, momentum_score, composite_score, is_mover, reason, clears_when, gate_id: None, price}` and `continue`. Adds need no code (`_entry_verdict` :1130-1170 → `entry_held_adds`; G-30 stays when news binds, :2848-2870).
- Readers of the new bucket (mirror `beta_unknown_picks`): app.py:10735-10745, :10900-10905, :11275-11283, :11876-11884, :12412-12425 ("Earnings date unconfirmed", same pause styling); grow_dropoff.py:72, :163, :210-218 (after `beta_unknown_picks`, before `overflow_picks`); evening_debrief.py:232-241; headless_alert_engine.py:982 (diag `earnings_unknown`); gate_ledger.py:74 (comment: deliberately not listed).
- Cron Watchlist: headless_alert_engine.py:1655-1675 `_checks_held["earnings_unknown"]`; cron_runner.py:536 kinds tuple += `"earnings_unknown"`. No email change.
- Redeploy (Q9): entry_posture.py:268-306 gains `today=None`; with `kind == "open"` and a bundle, an imminent date (0..`EARNINGS_IMMINENT_DAYS`) or `earnings_unknown_hold` → "**{t}** clears the Buy gate … but {earnings in Nd | its next earnings date couldn't be confirmed}, so it isn't called as an entry here." Callers (app.py ≈ :13242, ≈ :18506) pass `_today_et()`.
- Sell side (Q6): daily_briefing.py earnings section (`if not earn_date … continue` :3938-3943) builds Brief key `earnings_unconfirmed` for held names with `earnings_resolved.state == "failed"`, not in `_full_exit_tickers`, and `trim_sizing.earnings_week_decision(weight, n, shares)` trim_shares ≥ 1: `{ticker, on_file, weight, floor_pct, reason}`, one Review caption: "Earnings date unconfirmed for {T}: the date on file ({Mon Nov 23}) has passed and no next date could be confirmed. If it reports this week, its {w:.1f}% is above your {floor:.1f}% earnings-week line; confirm the date on the company's investor-relations page." No trade, no Act card.
- `_SYNTH_SCHEMA_VER`: HEAD + 1, reason prepended.

**C3c (display, app.py):** 🔔 Catalyst Watch `_render_holdings_earnings` (:5687-5850) via `earnings_date.display(...)` (backfill only fills a name with no stamp); KPI "Recently passed" → "Reported recently" + "Unconfirmed"; caption (:5688-5693) "Dates from Yahoo Finance, checked against the Financial Modeling Prep calendar; a date on file that has passed with no confirmed next date shows as Unconfirmed." 🧾 Summary card (:16287-16312) counts unconfirmed held names as a disclosed gap. 📈 Analysis earnings label (:27684-27690) via the display helper (no "(past)"). Market Scanner badge (:26486-26520) `reconcile_signals(..., earnings_known=not earnings_unknown_hold(...))`; signal_reconciliation.py after the imminent caution (≈ :188): "🔍 Verify — earnings date unconfirmed" / "… the next earnings date couldn't be confirmed. Check it before acting." (default None = unchanged). User Guide (Watchlist, Grow Today, Catalyst Watch, Analysis).

## Constants (each new value is an owner call)

| Name | Proposed | Kind | Owner Q |
|---|---|---|---|
| `EARNINGS_REPORTED_CLEAR_DAYS` | 45 | policy (quarterly gap ≥ ~60 d; 45 + 7 < 60) | Q3 |
| `EARNINGS_XCHECK_HORIZON_DAYS` | 90 | policy (matches the 90-day Catalyst window) | Q5 |
| `EARNINGS_XCHECK_CACHE_TTL_SEC` | 21600 | plumbing | doc row |
| `EARNINGS_XCHECK_FAIL_RETRY_SEC` | 300 | plumbing (failed sector read precedent) | doc row |
| `EARNINGS_XCHECK_CHUNK_DAYS` | from P1 (≤ 90) | plumbing | doc row |

No existing constant changes value. DoD #8: no version column; dated population notes instead.

## Owner questions (recommended default first)

- **Q1** A Yahoo date that has passed with no confirmed next date: hold new buys and adds? Default yes (CRWD held even if FMP lacks 12-01).
- **Q2** Both sources give a future date and disagree: default the earlier (buy holds + G-33 trim), disagreement shown on Catalyst Watch and Analysis.
- **Q3** "Just reported" = an FMP report with an actual EPS within 45 days → passes. Default: actual EPS required. Alternative: also accept "both show the same past date" (faster, can pass a shared wrong estimate).
- **Q4** FMP calendar unreadable: names whose Yahoo date has passed are held (default). Alternative: today's behaviour (pass).
- **Q5** Cross-check horizon 90 days (default).
- **Q6** Sell side, a held name with an unconfirmed date whose earnings-week rule would trim: default one Review caption, no trade.
- **Q7** A genuine `no_date` (new listing, e.g. ANT): default pass (U2 stands); FMP upgrades it to `date` when listed.
- **Q8** A failed cross-check in the cron: default log line only, lane not red.
- **Q9** Redeploy headline gets the earnings leg (imminent + unknown) in C3b: default yes.

## Chunks

0. **P1 probe** (read-only `scripts/earnings_xcheck_probe.py`; lead / owner; no review): one call [today, today+90], one [today−45, today−1]: row counts, min / max date, one row's keys, whether past rows carry `epsActual`, any cap sign; for CRWD, NVDA, MU, ANT, 000660.KS and every holding + watchlist name: FMP upcoming / reported and Yahoo's raw list. Sets `EARNINGS_XCHECK_CHUNK_DAYS` + the field name; no actuals → Q3's alternative returns to the owner.
1. **C3a resolution plumbing** (implementer → Opus): `earnings_date.py`, `earnings_xcheck.py`, providers, orchestrator, bundle_loader, `earnings_state`, cron log lines, a conftest autouse stub of `earnings_xcheck._fetch_rows` (raises unless a test opts in), `_GATE_FILES` + review-policy. Live effect: CRWD reads 12-01 everywhere (G-10, Grow Today earnings-wait, G-33, Catalyst); a past date reads as no date. Safe to push alone.
2. **C3b the hold** (implementer → Opus).
3. **C3c display** (app.py; screenshot; review only for the scanner wording); push with C3b.
4. **C3d docs** (lead): requirements G-10, G-33, G-34, F-37a, F-25; architecture (two modules, bundle keys `earnings` / `earnings_yahoo` / `earnings_resolved` / `earnings_dates`, constants rows); bug register (StaleEarningsDate, EarnUnknownPass, CatalystEarnDates FIXED; residual rows); parent Status + §1; shipped-log; user-manual; this Status.

Collisions: cron_runner.py / headless_alert_engine.py / app.py shared with sell-check C5 / C6 and FracNew (no function edited by both). Stage with `git diff --binary` + `git apply --cached`.

## Risks / coordination

- Sell-side change (C3a): names whose stale Yahoo date hid a report now get G-33 trims, Playbook cards and alerts. Intended; the reviewer treats it as a trim-path change.
- Blackout risk: after earnings season many names show past Yahoo dates; with FMP down (Q4) or no actuals (Q3), Enter Now / Grow Today can go dark for them. One banner per page; the cron log sizes it.
- Measurement integrity (dated population lines, criteria unchanged): `entry-regime-check.md` §4, `earnings-revision-shadow.md`, `gate-suppression-ledger.md` (no new rows; G-30 / G-06 / G-28 kept by binding + bucket order), `grow-today-rr-gate.md`, `recommendation-outcomes-measurement.md`, earnings-trim `rec_events`.
- `_GATE_FILES` +2: `earnings_date.py`, `earnings_xcheck.py`.
- Residual rows: NoBundleAddPass (`check_stale` / `check_earnings` with `bundle=None`); ReadinessUnknownEarn (watchlist_advisor.py:1256, display); an `etf_registry` fund outside the static rosters with a missing quoteType; EarnMoveFmpPerTicker (earnings_move_forecast.py:47-63); Quick Research / Compare show the resolved date only.

## Tests the build must include

- `resolve` rule table: fund → n/a; stale copy → failed; Yahoo future only; FMP future only; equal → "both"; differ → earlier + `disagree`; Yahoo [past, future] → future; CRWD (Yahoo ["2026-11-23"], today 11-27, FMP upcoming ["2026-12-01"]) → 12-01 fmp; CRWD FMP failed → past_unchecked; FMP ok no row → past_unconfirmed; NVDA (Yahoo 11-17, FMP reported 11-17 with actual) → reported, without actual → failed; reported boundary today−45 / −46; horizon boundary +90 / +91; `no_date` + nothing → no_date, + FMP future → date; `read_failed` + FMP future → date; date == today → date.
- `earnings_xcheck`: one failed chunk → failed; exception → failed, never raises, never `[]`; no key; cache per ET day (UTC-03:00 clock; DST 2026-11-01, 2027-03-14); failure retried after FAIL_RETRY; lock → one fetch; rows without actuals never set `reported`.
- Producer pin: `load_bundle` stamps `earnings_resolved` on success, cache (stale_copy) and resolver exception; `bundle["earnings"]` never past. AST pin: no production module except bundle_loader calls `fetch_ticker_bundle` (scripts excepted).
- Hold boundaries: stamp absent → None; days 0 / 7 → existing `earnings` hold, 8 → pass; −1 → "passed since the data loaded"; failed → each reason's wording; no `$` / `**`.
- Binding: + negative news → news binds (G-30 kept on adds); + G-28 → macro binds; + bear → tone binds (G-29); alone → earnings_unknown, no ledger row.
- Grow Today: bucket never in new_picks / overflow / sizing / `_g32_pick_src` / email go list; + G-06 → `beta_blocked_picks`; imminent known → `earnings_waiting`; add → `entry_held_adds` check earnings_unknown, gate_id None; key in all three return shapes; `held_add_verdict` paused; `derive_dropoffs` attributes it.
- Watchlist / Analysis / cron: ENTER_NOW → ENTRY_HELD "Held: earnings date unconfirmed"; banner lists only names held solely by it, honours `exclude`; Analysis no size + "held: …"; `checks_held["earnings_unknown"]` + scan-log line; earnings-read log with `reported` + failed reasons.
- Time-context matrix (today injected): Fri 11-27 CRWD days 4 held; Sat / Sun days 3 / 2 held; Thanksgiving held; 12-01 BMO / AMC day 0 held all day; 12-02 with actual → reported, without → held; resolved 23:50 read 00:10 → held until reload; DST days (frozen-clock guard); late-logged trade n/a (docstring).
- Sell side: CRWD 9.5% with 12-01 → no trim; held over the line unconfirmed → one `earnings_unconfirmed` item, no Act card; fully exited → skipped.
- Redeploy (Q9): imminent / unknown → not an entry call; beyond window → unchanged.
- Display: urgency boundaries 0 / 7 / 8 / 14 / 15, reported, unconfirmed; Catalyst literals replaced, no value change.
- Grep tests/ for "No imminent earnings risk", "Passed", "Recently passed" after wording edits.
