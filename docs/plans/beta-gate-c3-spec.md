# Beta gate C3 build spec: scan freshness (ScanCacheAnyAge, a new G-34 leg)

**Status:** SPEC 2026-11-26 (lead, from `docs/plans/beta-gate-scan-age-intraday.md` §2b / §3 C3 / §4). Owner took U5 = S1 (only a scan taken at or after today's 09:30 ET open sizes picks), U6 = yes (today's scan stays fresh after hours until midnight ET, with a "priced at" line), U7 = no in-session age limit, U8 = movers held too. Build = implementer; Opus review; commit `feat(grow)`. Builds on C2 (`beta_fit` already wired through `build_daily_briefing`).

## Goal
🏠 Grow Today never sizes a new pick off a scan that isn't today's post-open scan. Before the open, on a weekend or holiday, or when the saved scan is older, the scan's names are listed unsized as leads with a banner saying why. The 11:30 cron lane uses the same rule instead of its date-only check.

## Rules
- The decision lives in a new pure module. `app.py` / `cron_runner.py` only call it (no comparisons against time or constants there).
- No clock inside the module: callers pass `now_et`.
- Use `zoneinfo.ZoneInfo("America/New_York")` for conversion (never a fixed offset).
- `scan_state=None` (not wired) leaves every pre-existing Grow Today key byte-identical. The new key `scan_hold` is always present (None when not wired or fresh).
- Plain text, no `$` pairs, `safe_html` on interpolated HTML.

## 1. `stock_analyzer/session_label.py`
Rename `_OPEN` to public `SESSION_OPEN` (a market fact, not policy) and update its internal uses; grep tests/ for `_OPEN` and update any pin.

## 2. New gate module `stock_analyzer/scan_freshness.py`
Pure, never raises. Joins `_GATE_FILES` (`.claude/hooks/pre_tool_checks.py`) with a reason line in `docs/review-policy.md` (suggested: "the one rule for whether the saved market scan may size today's new picks (only a scan taken at or after today's 09:30 ET open); a wrong call sizes picks off another session's prices, or hides fresh ones").

```python
STATE_FRESH, STATE_STALE, STATE_UNKNOWN = "fresh", "stale", "unknown"

def decide(scan_meta: dict | None, now_et: datetime) -> dict:
    # {"state", "why", "scanned_at_et": datetime | None,
    #  "scan_session": date | None, "next_session": date | None}
def should_replace(current_meta: dict | None, cached_meta: dict | None) -> bool
def hold_banner(decision: dict | None) -> str | None
def priced_at_label(decision: dict | None) -> str | None
```

### `decide` (in this order)
1. `scan_meta` not a dict, or no `scanned_at` → unknown / `no_meta`.
2. Parse `scanned_at`: an aware `datetime`, or an ISO string with an offset (`datetime.fromisoformat`, accept a trailing `Z`). Naive, date-only (e.g. `"2026-11-26"`, the old app meta), unparseable, or later than `now_et` by more than 5 minutes → unknown / `unreadable`. Convert to ET → `scanned_at_et`; `scan_session = scanned_at_et.date()`.
3. `now_et` must be aware; if naive → unknown / `unreadable` (never guess).
4. `today = now_et.date()`. If `scanned_at_et.date() == today` and `scanned_at_et.time() < SESSION_OPEN` → stale / `before_open_scan`.
5. `is_today, _ = session_label.price_move_session(now_et)`:
   - `is_today` and `scanned_at_et.date() == today` (at or after the open, by step 4) → **fresh** / `ok` (stays fresh after hours until midnight, U6).
   - `is_today` otherwise → stale / `older_session`.
   - not `is_today`, today is a trading day (before the open) → stale / `pre_open`.
   - not `is_today`, today isn't a trading day → stale / `closed_day`.
6. `next_session`: the first trading day on or after `today` whose open is still ahead or under way (today when today is a trading day and `now_et` is before midnight; else the next trading day after today), walking forward at most 10 days with `data.is_trading_day`; None if none found. For `fresh` it is None.
7. Any exception → unknown / `unreadable`.

### `should_replace(current, cached)`
True only when `cached`'s parsed `scanned_at` is strictly newer than `current`'s (a `current` with no readable `scanned_at` is replaced by any readable `cached`). Never raises (False on error).

### `hold_banner(decision)` (None when fresh / not a dict)
Format datetimes as "Wed Nov 25, 9:47 AM ET" (no zero-padded hour; don't use `%-I`, it fails on Windows) and dates as "Mon Nov 30".
- `older_session`: "⏸ No new-position sizes yet today. The latest saved scan is from {scanned_at_et}, not today's session, so its prices are that session's. New picks return after today's scan (about 9:45 AM ET) or a 🔄 Refresh Signals once the market is open."
- `before_open_scan`: "⏸ No new-position sizes yet today. The latest saved scan ran at {scanned_at_et}, before the 9:30 AM ET open, so its prices are the last session's. New picks return after today's scan (about 9:45 AM ET) or a 🔄 Refresh Signals once the market is open."
- `pre_open`: "⏸ No new-position sizes before the open. The market opens at 9:30 AM ET; the latest saved scan is from {scanned_at_et}. New picks return after today's scan (about 9:45 AM ET)."
- `closed_day`: "⏸ Market closed today, so no new-position sizes. The latest saved scan is from {scanned_at_et}; next scan {next_session} about 9:45 AM ET." (if `next_session` is None: "next scan at the next session's open")
- unknown: "⏸ No new-position sizes: couldn't tell when the saved scan ran. New picks return after the next scan or a 🔄 Refresh Signals once the market is open."
Build "9:30 AM" from `SESSION_OPEN`, not a literal.

### `priced_at_label(decision)`
Fresh only: "Sizes priced at the {9:47 AM ET} scan." Else None.

## 3. `stock_analyzer/daily_briefing.py`
- `build_daily_briefing(..., scan_state: dict | None = None)` → `_grow_today(..., scan_state=scan_state)`; docstrings like the others. `scan_state` is a `scan_freshness.decide` result.
- In `_grow_today`: `_scan_held = scan_state is not None and (not isinstance(scan_state, dict) or scan_state.get("state") != scan_freshness.STATE_FRESH)` (a non-dict reads as held).
- When `_scan_held`: `curated_rows` and `mover_rows` stay empty (not evaluated, U8), so no new pick, no overflow / flat-day pick, no `new_pick` budget use, no `_g32_pick_src`. Find where both lists are built (~1445-1491) and skip filling them. `unevaluated_leads` is then computed as today from the empty evaluated set (every qualifying scan name becomes a lead).
- The ETF lane and the add lanes are untouched (neither uses the scan).
- Return key `"scan_hold"` on EVERY path (bear early return and main): None when `scan_state` is None or fresh; else `{"state", "why", "scanned_at_et": iso str or None, "scan_session": iso str or None, "next_session": iso str or None, "banner": scan_freshness.hold_banner(scan_state)}` (strings, so the memoised / locked Brief stays JSON-safe).
- Golden fixture: regenerate (`GOLDEN_REGEN=1`); the only diff must be `"scan_hold": null` per scenario.

## 4. `app.py`
### 4a. One helper for every assignment of `scanner_results`
`_set_scanner_results(df, meta: dict)` sets `st.session_state.scanner_results`, `_scanner_results_meta` = meta, and bumps `_scanner_ver`. Use it at all three sites:
- the cold hydrate (~3484-3501): meta from the cache row as today, plus `"from_cache": True`;
- 🔄 Refresh Signals (~9759-9774): `scanned_at` = the real timestamp `pd.Timestamp.now(tz="UTC").isoformat()` (today it is a bare date), source "app", `"from_cache": False`;
- Market Scanner page (~26294): `scan_date` today ET, source `"app_subset"`, `scanned_at` real UTC timestamp, `"from_cache": False` (today it sets no meta, leaving a stale hydrated meta beside fresh subset results). Leave the subset-feeds-Grow-Today behaviour itself as it is (named sibling).
Keep the existing `_scanner_ver` bump semantics (the helper does it; remove the duplicated inline bumps).

### 4b. Re-hydrate a stale cached scan
After the cold-hydrate block (and before the Home build reads `scanner_results`): when `_scanner_results_meta` has `from_cache` True and `scan_freshness.decide(meta, _now_et())["state"] != "fresh"`, read `db.load_scanner_cache()` through a new `@st.cache_data(ttl=_SCAN_REHYDRATE_TTL_S, show_spinner=False)` wrapper (`_SCAN_REHYDRATE_TTL_S = 120` at module level in app.py: cache plumbing, not policy; if `scripts/check_constants_documented.py` or the antipattern gate objects, put it in `scripts/constants_doc_allowlist.txt` per its rules). If `scan_freshness.should_replace(current_meta, cached_meta)` → `_set_scanner_results(df, {… "from_cache": True})`. Never raises (try/except → keep the current scan). A scan the user ran in this session (`from_cache` False) is never replaced by the cache. Note: the cache_data wrapper's return must be pickle-safe (the DataFrame + strings is fine).

### 4c. Pass the decision and memo signature
- Compute `_scan_decision = scan_freshness.decide(st.session_state.get("_scanner_results_meta"), _now_et())` once per run before the Home synthesis (meta None → unknown, which holds; but when `scanner_results` itself is None there are no names anyway).
- Pass `scan_state=_scan_decision` at BOTH app `build_daily_briefing` calls (~7698 and ~8149). Headless: the 09:45 scan lane passes nothing (its scan is post-open by the lane guard); the intraday lane passes nothing either (it refuses a non-fresh cache before calling, §5).
- Add `(_scan_decision["state"], _scan_decision["why"])` to `_synth_sig` (the state can change at midnight or 09:30 with no new scan, which `_scanner_ver` doesn't see). `_SYNTH_SCHEMA_VER` 34 → 35 with the "bumped: … was 34: …" clause.
- Extend the AST test from C2 (`tests/test_beta_gate_grow_today.py` pins `beta_fit` at every caller) with a matching pin that both app calls pass `scan_state`.

### 4d. Render (🏠 Grow Today)
- When `grow.get("scan_hold")` is a dict: show its `banner` as an `st.info` (escape `$`), placed where the New Positions header / empty-state caption is; the empty-state "none of the scored candidates cleared …" caption must NOT render in that case (nothing was scored).
- `unevaluated_leads` under a scan hold: its heading / caption reads "from the {scan_session as Wed Nov 25} scan" (find the existing unevaluated-leads renderer; momentum only, no verdict, no size, unchanged otherwise).
- When the session's decision is fresh and `new_picks` is non-empty: one caption with `scan_freshness.priced_at_label(_scan_decision)` under the New Positions header (one freshness per number: holdings are live, pick sizes are scan-time).
- 🔄 Refresh Signals tooltip (~9669-9685): replace the `scan_date == today` test with the decision: fresh → "Signals from today's {auto-scan|scan} at {time}. Click to re-score on live prices intraday."; otherwise → "Signals from the {scanned_at_et} scan — click to score today's price action." (or the existing generic line when there's no meta). Keep the `_sig_ts` branch as is. Don't add a new `.get(...) or {}`.

## 5. `cron_runner.py` intraday lane (~2741)
Replace the date-only test with `scan_freshness.decide(cache, now_et)` (the `load_scanner_cache` dict carries `scanned_at`, a UTC ISO string): when `state != "fresh"` and not `force`, log `f"intraday: scanner_cache not usable ({why}; scanned {scanned_at_et or scanned_at!r}) — skip."` and return 0. `force` still bypasses. Ensure `now_et` passed is aware ET.

## 6. Tests (new `tests/test_scan_freshness.py`; boundaries from `SESSION_OPEN`)
`decide` time-context matrix (plan §4):
- Trading day 10:00 + 09:47 scan today → fresh. 09:31 + yesterday's → stale `older_session`. 08:00 + yesterday's → stale `pre_open`. 08:00 + a scan saved today at 07:55 → stale `before_open_scan`.
- Boundary: scanned 09:30:00 → fresh; 09:29:59 → stale `before_open_scan`.
- 18:00 + same-day 09:47 scan → fresh (U6). 23:59 same → fresh.
- Saturday 2026-11-28 + Friday's scan → stale `closed_day`, `next_session` Mon 2026-11-30.
- Thu 2026-11-26 (Thanksgiving) 12:00 + Wed's scan → stale `closed_day`, `next_session` Fri 2026-11-27. Fri 2026-11-27 14:00 (early close) + 09:47 scan → fresh.
- DST, Mon 2026-11-02: scanned 14:47Z → fresh at 15:00 ET; 13:47Z (08:47 ET) → stale `before_open_scan`. (Corrected at build: 2027-03-08 is still EST, DST starts 2027-03-14.) 2026-03-09 (first EDT Monday of 2026): 13:47Z (09:47 EDT) → fresh; 2027-03-08 13:47Z (08:47 EST) → stale; 2027-03-15 13:47Z → fresh.
- Naive datetime, date-only string, garbage, None, `{}` → unknown. A scan 10 minutes in the future → unknown. Naive `now_et` → unknown.
- `should_replace`: strictly newer only; equal → False; unreadable current + readable cached → True; unreadable cached → False.
- `hold_banner` / `priced_at_label` wording for each why; no `$`; "9:30 AM" comes from `SESSION_OPEN` (assert by monkeypatching or by formatting the constant in the test).
- Frozen-clock guard: `decide` reads no clock (monkeypatch `datetime.now` to raise, or assert the module source has no `.now(` / `today(`).
`_grow_today` via `build_daily_briefing` fixtures:
- stale state → `new_picks == []`, `overflow_picks == []`, no mover picks, `scan_hold["why"]` set and `banner` non-empty, `unevaluated_leads` lists the qualifying scan names, the ETF lane still produces its pick when eligible, add lanes unchanged vs fresh.
- fresh state → identical output to `scan_state=None` except `scan_hold` None.
- bear return carries `scan_hold`.
Cron: an intraday-lane test (follow the existing `test_cron_*` intraday tests) with a yesterday-scanned cache → skip line logged, no email; a today 09:47 cache → proceeds.
App source pins: the helper is used at all three sites (no other `scanner_results =` assignment remains in app.py apart from the `None` initialiser); `_synth_sig` contains the decision; the empty-state caption is skipped under a scan hold.
After any copy edit, grep tests/ for old strings (memory `grep_tests_after_text_edit`).

## 7. Report back
Files changed, tests added and results, the antipattern and constants-doc check results, every place the spec didn't match the code, and any other reader of `scanner_results` / `_scanner_results_meta` you found that assumes `scanned_at` is a date. Run affected test files only (`-n 4` max), `python scripts/check_antipatterns.py`, `python scripts/check_constants_documented.py`, `python -m py_compile app.py cron_runner.py`. Do not commit.
