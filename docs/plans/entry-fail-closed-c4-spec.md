# C4 spec: a market-tone read that couldn't run holds new buys (+ ToneNoAge)

**Status:** **C4a SHIPPED `1b0ea74f`; C4b SHIPPED 2026-11-27 (both implementer passes; Opus FIX-FIRST on the redeploy headline → SHIP, 0 blocking).** DESIGNED 2026-11-27 (Opus planner `claude-opus-5-5`, PROCEED in two chunks C4a / C4b). **Owner took every default U1–U4 on 2026-11-27** (U1 30 min + same session, new alias constant; U2 hold ETF picks and adds pre-open / closed days; U3 red heartbeat + failure email; U4 macro-unreadable hold = separate chunk C4m after C4). C4a implementer started 2026-11-27. Parent plan `docs/plans/entry-fail-closed.md` §2 / §6 item 6; folds in bug-register row ToneNoAge. Line anchors verified at HEAD `9f6cf773`; the lead spot-checked the key claims the same day (`BRIEF_AUTO_REFRESH_MINUTES` = 30 at constants.py:1680, `session_label.price_move_session` / `move_when_phrase`, the watchdog's after-hours skip at app.py ~9686-9690, `gate_ledger.build_suppression_rows` bear branch, `yfinance_provider.py:405` one-bar `prev = price`, `sizing_path.py:358` tone reasons).

Changes from the 11-25 plan: (1) Grow Today keeps the plan's early return, not a late hold: a late hold would keep G-28 / G-16 / G-30 / G-06 pick rows built on a made-up "flat" tone, and every pick path would need a leak-proof hold; the cost is that a tone-unread day writes no gate-ledger rows (an outage-driven loss, ≈ 0 bias). (2) Binding precedence goes inside `entry_check` (Watchlist / Analysis / cron), where a G-28 row can be lost. (3) Three readers of `grow["tone"]` must check `entry_hold` first, or a held day gets false reasons ("adds only on an up day"). (4) The tone logic goes in a NEW gate module `tone_freshness.py`, not `buy_check_state.py` (CLAUDE.md: prefer a new module). (5) Plan Q1 (macro calendar unreadable holds; owner yes 11-25) is in no chunk → U4.

## Owner decisions (recommended default first)

- **U1 ToneNoAge rule.** Other pages reuse 🏠 Home's read only while it is **under 30 minutes old AND from the same session** (a pre-open read stops counting at 09:30); otherwise they read the S&P live; Home's read too old AND the live read fails → **hold** (11-25 U1), never fall back to the old read. New constant `TONE_HOME_READ_MAX_AGE_MINUTES = BRIEF_AUTO_REFRESH_MINUTES` (30, an alias: a page agrees with Home while Home's read is inside Home's own freshness promise). Alternative: always live first, Home only as fallback (no constant; the Watchlist can disagree with a fresh Home for up to 30 min).
- **U2 Pre-open and closed days.** Before today's 09:30 open, and on weekends / holidays, Grow Today also holds **adds and ETF picks** (C3 of the beta batch already holds the scan picks); today, after an up day, Home shows sized ETF picks and adds pre-open from yesterday's move. 📋 Watchlist / 📈 Analysis keep the last session's read but say which session. If no: C4b drops the `tone_not_today` leg and a bug-register row names it.
- **U3 Cron.** A 09:45 scan or 11:30 run whose S&P read failed sends no buy emails AND turns the lane's heartbeat red + sends the failure email (news-outage precedent, `cron_runner.py:2681-2690`).
- **U4 Scope.** The macro-calendar-unreadable hold (plan Q1) fails open today in three places (Watchlist / Analysis notes; `_grow_today` reads None as "nothing blocked", `daily_briefing.py:1064`; the cron feeds Grow Today `[]` on a failure, `headless_alert_engine.py:735-750`). Default: a separate small chunk **C4m** straight after C4, its own review.

No existing constant changes value; the only new one is U1's alias.

## C4a: Watchlist / Analysis / emails / redeploy headline (decision-bearing; one implementer pass; Opus review)

1. **New `stock_analyzer/tone_freshness.py`** (joins `_GATE_FILES` 123 → 124 + review-policy reason line). Pure: no clock, no streamlit, no db; imports `session_label` + `constants`; must NOT import `entry_eligibility` (cycle).
   - `home_read_state(home_ctx, home_is_today, now_et) -> {"state", "read_at", "age_min"}`; state ∈ `usable` (built today, `tone_available is True`, finite `sp500_pct`, a tone, an aware `tone_read_at` with `0 <= now_et - read_at < TONE_HOME_READ_MAX_AGE_MINUTES*60` s — strict, matching the watchdog's rebuild at ≥ 30, app.py:9686 — and `price_move_session(read_at) == price_move_session(now_et)`), `not_today`, `failed` (Home's own read failed or pct not finite), `aged`, `other_session`, `no_read_time` (key absent), `unreadable` (naive / garbage `read_at`, or later than `now_et`: same server clock, so a future time is bad data; no slack constant). Aware-datetime subtraction (DST-safe).
   - Wording: `unread_reason(ts)` → "today's market move couldn't be read"; `unread_detail(ts)` → one sentence per `why` (e.g. "Home's read from 9:42 AM ET is more than 30 minutes old and a fresh S&P 500 read failed.", 30 from the constant); `session_phrase(ts)` → `session_label.move_when_phrase(as_of)` when the move isn't today's.
2. **constants.py:** `TONE_HOME_READ_MAX_AGE_MINUTES = BRIEF_AUTO_REFRESH_MINUTES` with a coupling comment; architecture constants-table row; run `scripts/check_constants_documented.py`.
3. **entry_eligibility.py (gate file):**
   - `resolve_tone_state` (:48-74) gains keyword `now_et=None` (None = today's behaviour: cron + tests); with `now_et`, the Home leg needs `home_read_state(...)["state"] == "usable"`. Output adds `why` (`None` | `home_not_today` | `home_aged` | `home_other_session` | `home_failed` | `home_unreadable` | `live_failed`), `home_read_at`, and `as_of` for the live leg (new keyword `live_read_at=None`).
   - `check_tone` (:198-213): unavailable / non-dict `ts` → HOLD `{"kind": "hold", "check": "tone_unread", "gate_id": None, "reason": unread_reason(ts), "clears_when": "the S&P 500 read succeeds — re-checked every time you open this page", "gate_value": None, "gate_threshold": None}`. Bear unchanged.
   - `entry_check` (:345-351) binding precedence: `_ABSTENTION_CHECKS = frozenset({"tone_unread"})` (C3 adds `"earnings_unknown"`; whichever ships first creates it); `binding` = first hold whose `check` is not in the set, else `holds[0]`; `holds` order unchanged; `macro_unknown_sector` stays out of the set (no existing binding changes).
   - `HELD_KIND_LABELS` (:575): `"tone_unread": "market move couldn't be read"`.
   - `tone_source_line` (:433-454): ":450-451 (its picks were not deferred)" → "its new picks are held until it can"; live leg worded from `why` ("Market read live at 2:14 PM ET (Home's read from 9:42 AM ET is over 30 minutes old)."); append `session_phrase` when the move is the last session's.
   - `watchlist_banner` (:457-499): delete "entries are shown, not deferred" (:470-471); return `"unread": {title, body}` (title "Market move couldn't be read — new entries held"; body "N names would otherwise be Ready: …" from ENTRY_HELD cards whose holds are all `tone_unread`, minus `exclude`, same mechanics as the bear list, then `unread_detail`, then the clear condition); return `"tone_line"` (one caption on every bull / flat day with read time + source from `tone_source_line`; omitted when protect shows — fixes "no read time on bull/flat days"). Macro note stays (U4).
   - `analysis_gate_tiles` (:732): `_checks_for["tone"] = ("tone", "tone_unread")` (else the tile reads "Market not risk-off").
   - `analysis_hold_banner` (:777): "deferred: " only for `"tone"`; `tone_unread` reads "held: …"; source line for `check in ("tone", "tone_unread")`, `unread_detail` in the unread case.
   - `analysis_notes` (:803-814): drop the tone fail-open note; add the `tone_line` caption.
   - `email_footer_lines` (:622-623): "Market tone couldn't be read — no name is listed as Ready to Enter this run." (near-unreachable: an all-held run sends no email).
   - `emailed_then_held_line` (:570-572): "now deferred" only when `hold_kind == "tone"`, else "now held".
4. **entry_posture.py (gate file):** `tone_state_for_page` (:93-115) gains `now_et=None`: Home usable → Home's read; Home built today with a failed read → unavailable `why="home_failed"` (unchanged; mirrors Grow Today's hold); else unavailable with a `why`, and the live leg when supplied. New `page_needs_live(state) -> bool` (unavailable and `why != "home_failed"`). `redeploy_call` unknown text (:264-268): "Grow Today treats an unread market as a flat day" → "Grow Today holds new picks until the market move can be read". `stricter_tone_state` / Lock Setup unchanged.
5. **app.py (render only):**
   - New reader beside `_cached_market_indices` (:4400), raising on a missing S&P row so a failure is never cached:
     ```python
     @st.cache_data(ttl=45, show_spinner=False)
     def _cached_sp500_read():
         idx = fetch_market_indices(); sp = next((i for i in idx if i.get("short") == "S&P 500"), None)
         if sp is None: raise RuntimeError("S&P 500 row missing")
         return {"pct": float(sp["change_pct"]), "read_at": _now_et()}
     ```
   - 📋 Watchlist (:31032-31056) and 📈 Analysis (:27322-27345): `resolve_tone_state(..., now_et=_now_et())` with no live read; if unavailable, `_cached_sp500_read()` (exception = live failed) and call again with `live_pct` / `live_ok` / `live_read_at`. Render the `unread` block in place of the notes tone line (Watchlist :31247-31262); `tone_line` as `st.caption`.
   - 📡 Signals redeploy (:18511-18530): pass `now_et`; fetch live when `_entry_posture.page_needs_live(_sg_tone)`.
   - Home redeploy (:13385): pass `now_et=None` explicitly (Home reads its own build on purpose, matching its Grow Today); the AST pin allowlists this one site.
6. **Cron Watchlist** (`headless_alert_engine.py:1604-1614`, `cron_runner.py:491-512`): `_checks_held` gains `"tone_unread"` (not-held ENTRY_HELD cards with `hold_kind == "tone_unread"`); the log helper's kinds tuple adds it.
7. **Docs, same commit:** requirements G-29 (replace "Tone unreadable → fail open … (owner D1)" with the hold, D1 reversed, the tone-source rule), G-34 (C4a leg), F-307 "Unavailable inputs"; User Guide (Watchlist, Analysis); `entry-regime-check.md` §4 population line; bug register ToneNoAge fixed for the pages.

## C4b: Grow Today, Home, cron picks (decision-bearing; two implementer passes — pure + cron, then app render — committed and pushed together so Home never shows a hold without its banner; Opus review)

8. **`tone_freshness.grow_entry_hold(market_context, today) -> dict | None`** (no clock read; compares the read's session with the passed `today`): `tone_unread` when `tone_available is False`, or `True` with a non-finite `sp500_pct`, or a present but unreadable `tone_read_at`; `tone_not_today` (only if U2 = yes) when `price_move_session(read_at) != (True, today)` — reason "today's session hasn't opened yet — the latest move is {Wed Nov 25}'s", clears "after the 9:30 AM ET open" or "on {Mon Nov 30} after the open"; None otherwise, incl. key absent (legacy / tests). Returns strings only `{"check", "reason", "clears_when", "banner", "session"}` (JSON-safe for the Lock snapshot). View builders: `grow_header_view(grow, tone)` (subtitle; whether to show "Cleared all 5 checks"), `tone_card_view(market_context, now_et)` (label / tone key / S&P line / note; an unread read never says "Hold Steady — Mixed Market" or "+0.00%"; replaces the weekday-only last-close sum at app.py:10196-10211 with `price_move_session`, which knows holidays), `grow_count_text(grow)` ("held", not "0").
9. **`daily_briefing._grow_today` (gate file):** `_entry_hold = tone_freshness.grow_entry_hold(market_context, today)` after the scan-hold block (:1352). Order: unread → early return; bear → existing branch (:1355) unchanged incl. its G-23 row; `tone_not_today` → early return. The early return copies the bear return's shape (:1361-1424) with `"tone": tone` (still "flat" on an unread day), `"sp500_pct": None` when unread, `"entry_hold": _entry_hold`, `"message": None`, every bucket `[]`, `adds_paused=_adds_paused_list()`, `unevaluated_leads=_unevaluated_leads(set())`, and the `scan_hold` / `corr_unchecked` / `risk_banner` / `macro_coverage_expired` passthroughs. Add `"entry_hold": None` to the bear and normal returns (~:2989-3045). Bear message (:1366): "today" → the session phrase when `tone_read_at` is an earlier session (GrowTodayStaleToday, Grow Today leg). `build_daily_briefing` (:4655): the default ctx already sets `tone_available False`, so it now holds — keep.
10. **`gate_ledger.build_suppression_rows` (:108-119):** `if isinstance(grow.get("entry_hold"), dict): return []` before the bear branch (no G-23 row on a tone-unread day whatever `tone` the caller passes).
11. **Readers of `grow["tone"]`:** `sizing_path.held_add_verdict` (:358, gate file) → `not_called` with the hold reason (also fixes the Rebalancer); `hold_reasons.adds_paused_label` / `adds_paused_note` / `leads_note` (:52-76) gain `hold=None` with held wording; `headless_alert_engine` diag (:915-937) adds `"entry_hold": (grow.get("entry_hold") or {}).get("check")`.
12. **Provider** (`providers/yfinance_provider.py:404-405`): `if len(col) < 2: continue` (a one-bar series no longer makes up a 0.0% move; the row is omitted → `tone_available False` → C4 hold). Update the KNOWN LIMIT comment at `notify.py:1450-1454`.
13. **cron_runner:** the 0-pick log (:2550-2577) branches on `d.get("entry_hold")` first ("market move couldn't be read — new picks held (G-34)"); (U3) scan appends `"tone_unread: the S&P 500 read failed — no buy-list or Watchlist Enter Now this run"` to `failures`; intraday (:2752-2800): when `payload["diag"]["entry_hold"]` is set, log it and (U3) append to `_id_failures` instead of "none pulled back enough".
14. **app.py (render only):** Grow Today render (:10833-10950): header / subtitle from `grow_header_view`; new branch before `bear_msg` (:10943) when `grow.get("entry_hold")` is a dict → its banner via `st.info` (`$` escaped); with `entry_hold` present, suppress the `scan_hold` banner (:11164-11168) — one banner; then `_render_grow_pacing_disclosures(grow, tone)` and return. Pacing disclosures (:10728-10760) pass the hold to the `hold_reasons` labels. Tone card (:10175-10266) from `tone_card_view`; delete the "treated as a flat day" note (:10260-10263). Action card (:10304) `grow_count_text`. `_market_tone_cache` (:10171): publish `None` when `tone_available is False` (consumers already handle None: :33780, :48607, `decision_context.py:151`; else a failed read is saved as a "flat" regime into thesis reviews). `_synth_sig` (:7558-7583): add `session_label.price_move_session(_now_et())`; bump `_SYNTH_SCHEMA_VER` 36 → 37 (or the next value at ship time) with the reason prepended. User Guide (Grow Today / Home).
15. **Docs, same commit:** requirements G-23 (tone-unread holds, no `__MARKET__` row, D1 reversed), G-34 (C4b leg), F-307, Grow Today F-IDs; `docs/coordination-caches.md` `_market_tone_cache` None semantics; population lines; bug register (ToneNoAge, GrowTodayStaleToday Grow Today leg, new sibling rows); shipped-log; parent plan Status.

**C4b must also fix the `entry_posture.page_needs_live` docstring ("Home then holds its picks" → true only after C4b) and must flip these C4a strings to the held wording** (C4a keeps them true to today's behaviour, where Home's Grow Today still runs an unread day as flat): `entry_posture.redeploy_call` unknown text, `entry_eligibility.tone_source_line` home_failed leg, `tone_freshness.unread_detail` home_failed, the User Guide sentence (Watchlist / Analysis paragraph), G-34.

**C4m** (if U4 = default): the macro-calendar-unreadable hold, a separate chunk; out of this spec.

## Time-context matrix (ToneNoAge; all via `market_time.now_et` / the passed `today`)

| Case | Home read | Page does |
|---|---|---|
| Pre-open: Home 08:00, page 08:20 | same session, 20 min | Home's read, labelled "the last session's move (Wed Nov 25)" |
| Home 09:20, page 09:35 | other_session | live (today's) |
| Home 10:00, page 14:00 | aged | live; "Home's read from 10:00 AM ET is over 30 minutes old" |
| Home 15:00, page 16:30 (after close) | aged | live = the closing move |
| Sat: Home 10:00, page 10:20 / 10:31 | same session (Fri) | Home's read / live (still Friday's move) |
| Thanksgiving Thu 11-26 | session Wed 11-25 | as the weekend |
| Fri 11-27 early close: Home 12:50, page 13:10 | same session, 20 min | Home's read |
| DST Sun 2026-11-01: read 01:50 EDT, page 01:10 EST | 20 min by aware math | usable (wall-clock math would read it as future) |
| Home aged + live fails | — | HOLD `tone_unread` (U1) |
| Home's read failed + live ok | — | Watchlist / Analysis: live, "Home couldn't read…"; Signals headline: unavailable (mirrors Home, unchanged) |

Cron / intraday have no ToneNoAge exposure: each run reads fresh (`_read_market_context`, `headless_alert_engine.py:602`), and the scan and Watchlist emails share that one read. A forced pre-open run holds under U2.

## Risks / coordination

- `grow["tone"]` stays "flat" on an unread day; the three readers that would give false reasons (`sizing_path.py:358`, `headless_alert_engine.py:915`, app render :10918) are handled in C4b.
- Fail-open risk: the early return precedes every pick / ETF / add path; a test pins every lane at zero.
- Lock Setup (retirement paused): a pre-C4 snapshot has no `entry_hold` and renders as today; `stricter_tone_state` already withholds a lock written on an unread read.
- No new session key → no `coord_freshness` registration (`_cached_sp500_read` is `st.cache_data`).
- `_GATE_FILES`: new `tone_freshness.py`; edited members `entry_eligibility`, `entry_posture`, `daily_briefing`, `sizing_path`, `headless_alert_engine`, `cron_runner`, `constants`; not members: `app.py`, `gate_ledger.py` (a ledger write path; membership = lead's call), `hold_reasons.py`, `notify.py`, `yfinance_provider.py`.
- Antipatterns: every comparison against the new constant lives in `tone_freshness.py`.
- Calm-advisor: after ToneNoAge the Watchlist follows an intraday turn the way Home already does while open (G-29 working, not churn).
- Cost: after 30 min each Watchlist rerun makes one live read (cached 45 s); during a Yahoo outage every rerun retries (slower page). Accepted.
- Parent plan §5 changes under U2 ("pre-open reads unchanged" no longer true for Grow Today).
- C3 interplay: C3 adds `earnings_unknown` to `_ABSTENTION_CHECKS`.

## Sibling sites

Fixed in C4: `yfinance_provider.py:405`; `notify.py:1450`; app.py:10196-10211 (holiday as a session); `daily_briefing.py:1366`; app.py:10171; `sizing_path.py:358`; `hold_reasons.py:52-76`; `headless_alert_engine.py:915`; app.py:10304; `entry_posture.py:264-268`; `entry_eligibility.py:450-451`, `:470-471`, `:570`, `:732`, `:809-810`.

Named, not fixed (new bug-register rows): **HomeToneAfterClose** (app.py:9689: the watchdog skips after-hours runs, so a mid-session Brief keeps its intraday tone and Grow Today after the close; fix = one post-close rebuild); **IndexBarLag** (just after 09:30 Yahoo's 2-day download can still end on the previous session's bar; fix = stamp the last bar's date in the provider); **ToneCacheNoAge** (`_market_tone_cache` has no age; Trade Journal draft context, awareness only); app.py:14727 (AI snapshot raw index read, display only); the rest of GrowTodayStaleToday's "today" sweep (Signals, emails).

## Measurement integrity

No existing population line covers tone (the C2b line at `entry-regime-check.md` §4:61 is fit / beta only). Add dated lines (precedent D10, criteria unchanged) before the first live day:
- **`entry-regime-check.md` §4 (C4a) — NOT ≈ 0 bias:** (i) a tone-unread day holds every Enter Now, app and cron (outage-driven, ≈ 0 bias); (ii) ToneNoAge: an app Watchlist visit after Home's read is 30 min old, or after the open on a pre-open read, now reads live; on days the S&P turned down ≥ 0.5% after the morning read it defers names (G-29) the app used to call and log first; those days skew Weakening / Falling, so they bear on rule A. Split matured episodes at the ship date. The cron 09:45 population is unaffected.
- **`gate-suppression-ledger.md`** (C4a + C4b): more app G-29 rows on intraday sell-offs; no G-23 / Grow Today rows on a tone-unread day; (U2) no app Grow Today rows from a pre-open / closed-day build after a non-bear session.
- **`grow-today-rr-gate.md` (G-32 shadow), `recommendation-outcomes-measurement.md`, `engine-track-record-meter.md`** (C4b): no app `new_pick` / `add_winner` rows on tone-unread days and (U2) none from pre-open / closed-day ETF picks and adds.
- DoD #8: no constant feeds a history table → no version column.

## Tests the build must include

- **tone_freshness:** 29m59s usable / 30m00s aged; future `read_at` → unreadable; naive / garbage → unreadable; absent → `no_read_time`; not today; failed; NaN pct; 09:29:59 read vs 09:30:00 now → `other_session` at 1 s of age; every matrix row incl. Thanksgiving 11-26, the 11-27 early close, the 2026-11-01 repeated hour, Mon 2026-11-02 / Mon 2027-03-08 09:29 vs 09:31 (proves zoneinfo); frozen-clock guard (no function reads the clock).
- **`resolve_tone_state(now_et=…)`:** each `why`; `now_et=None` identical to before (pins cron).
- **`tone_state_for_page` / `page_needs_live`:** `home_failed` → no live; `aged` / `other_session` / `not_today` → live.
- **`check_tone`:** unavailable dict / None / non-dict → `tone_unread` hold, `gate_id` None; bear unchanged; flat / bull → None.
- **Binding:** `tone_unread` + G-28 → binding macro, `suppression_kind` `entry_macro`, `build_watchlist_suppression_rows` emits the G-28 row; `tone_unread` + news → news; `tone_unread` alone → `hold_kind` `tone_unread`, no ledger row; stale + `tone_unread` → stale.
- **Watchlist / Analysis:** `watchlist_banner` unread block lists only all-`tone_unread` cards, respects `exclude`, never "shown, not deferred"; `tone_line` on bull / flat with the time, not beside protect; Analysis no size, "— (held)", tone tile held, banner "held:", no "sizes are shown" note; footer line; `emailed_then_held_line` "now held".
- **Cron:** `compute_watchlist_entries` with `tone_available False` → `entries []`, `checks_held["tone_unread"]` populated; the log line; (U3) failure appended for scan and intraday; the 0-pick log names the hold.
- **`grow_entry_hold`:** unread (False / NaN / unreadable `read_at`); absent key → None; post-open today → None; (U2) pre-open, Saturday (next session Monday), Thanksgiving.
- **`_grow_today` / `build_daily_briefing`:** unread with tone "bull", eligible ETFs, strong scan names, qualifying adds → every pick / add / ETF / overflow / flat-held list `[]`, `entry_hold.check == "tone_unread"`, tone "flat"; `build_suppression_rows(grow, tone="flat")` and `(…, tone="bear")` both `[]`; `market_context=None` → held; an available day byte-identical (golden master + `entry_hold: None`); bear unchanged (G-23 row written); (U2) pre-open bull → `tone_not_today`, pre-open bear → bear branch; bear message weekend / pre-open "in the last session (…)".
- **Readers / views:** `held_add_verdict` with `entry_hold` → the hold reason; `hold_reasons` held wording; `tone_card_view` unread → no "Mixed Market", no "+0.00%"; holiday last-close.
- **Provider:** one-bar `^GSPC` frame → S&P row omitted → both producers give `tone_available False`.
- **AST pins, both ends:** (1) every `resolve_tone_state(` call in app.py has a `now_et` keyword, count pinned at 3, only the Home-redeploy site may pass None; (2) every `tone_state_for_page(` call in app.py passes `now_et`; (3) in app.py and `headless_alert_engine.py`, every dict literal with a `"tone_available"` key whose value isn't the constant False also has `"tone_read_at"` (producer end); (4) `DEFAULT_CHECKS` contains `check_tone` and `_grow_today` calls `grow_entry_hold` (consumer end); (5) `_render_grow_today` reads `entry_hold` before `bear_msg`, the tone card calls `tone_card_view`; (6) `_read_market_context` returns an aware `tone_read_at`.
- After every wording edit, grep `tests/` for the old strings (known: `test_brief_chips.py`, `test_watchlist_entry_cron.py`, `test_watchlist_entry_page.py`; ≈ 13 tests build an unavailable tone state).
