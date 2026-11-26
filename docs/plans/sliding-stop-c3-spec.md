# SlidingStop C3 — wiring spec (lead, 2026-11-26)

**Status:** SPEC, for the implementer. Parent plan: `docs/plans/sliding-stop.md` (§3, §5, §7 item 5, §8, the Status line's NBs for C3). C1 `held_stop_carry.py` and C2 (`db.load_held_stop_carry_or_none` / `save_held_stop_carry` / `delete_held_stop_carry`, DDL applied) are shipped; C2b dry run GATE: PASS. Land before ≈ 2026-12-10 (ANT's young-listing window starts at the 12-10 close).

One push, two commits. Commit 1 (decision path): items 1–6. Commit 2 (display): items 7–8. No constant changes value; no new constant.

## 1. `stock_analyzer/held_stop_wiring.py`

1. **`build_held_stops(..., floors, carry, fetch_range=None)`**: new REQUIRED keyword `carry` with no default (mirror `floors`). `carry` = `db.load_held_stop_carry_or_none`'s result: `{TICKER: row}`; `{}` = read, none saved; `None` (or any non-dict) = couldn't be read. Pass it to `_one`. Update the module docstring (and drop "Not wired as of the commit that added it").
2. **`_one`**: add a helper `carried(out, frame)` next to `floored`:
   - `readable = isinstance(carry, dict)`; `row = carry.get(t) if readable else None`;
   - `journal_readable = trades_df is not None`; `jsig = hsc.journal_buy_sig(trades_df, t)` (None when unknown);
   - `return hsc.apply_carry(out, row, as_of=as_of, bars=frame, avg_cost=avg_cost, journal_sig=jsig, journal_readable=journal_readable, policy_sig_now=hsf.policy_sig(), carry_readable=readable)` (it never raises and never lowers; still wrap the jsig / policy_sig computation in the same try so a fault there becomes the C1 FAILED shape, i.e. call `apply_carry` with whatever you have, or reproduce its FAILED dict exactly).
   - Apply it AFTER `floored(...)` with the SAME frame on the three floored returns: `return carried(floored(_wrap(...), df), df)` etc. (the status != ok branch, the long-frame branch, the bundle fallback branch).
   - **Never** on `_buy_day(...)` returns or `_unavailable(...)` returns. Additionally tag every `_buy_day` return with an additive key `"buy_day_path": True` (set on the dict `_buy_day` returns, in `_one`, not by changing the existing `buy_day` key, whose meaning other code relies on) so a test can pin that the carry never ran on it (no `"carry"` key).
3. **`episode_status`**: `_wrap` gets an always-present key `"episode_status"` (default None). In `_one`, every returned dict (buy-day, unavailable, the three floored+carried paths) carries `ep.get("status")` (set on the final dict; copy, don't mutate a shared object). `build_held_stops`' exception path leaves it None. `held_stop_carry.notice_tier` (C4) reads it.
4. **U6 (owner, default yes): status != ok branch frame order** = `fetch_history(t, "1y")` FIRST (unadjusted, as buy-day does), the 6-month bundle only when that raises / returns an unusable frame. The rest of the branch is unchanged.
5. **Label**: new `LABEL_CARRIED = "Carried Stop"`. After `apply_carry`, when `result["carry"]["state"] == hsc.APPLIED`, set `result["label"] = LABEL_CARRIED` (in the wiring, after the call; `held_stop_carry.py` is not edited). Reason: `label_for("prior_session", "trailing")` reads "Prior-close ATR", which is false for a carried trailing stop.
6. **Notes**:
   - `_note_for`, basis prior_session, episode status "ok" (the young-listing case): replace "This position's start isn't usable (…)" with `f"No part of the full stop can be computed yet ({reason}), so the stop is the last completed close minus {mult:g} x ATR."` + the same "It does not trail, and it can move down as the price falls." sentence. A non-ok episode keeps today's wording. If `reason` is empty use "too little price history before the entry".
   - After `apply_carry`, when the carry state is APPLIED or NOT_BINDING and the note contains `"It does not trail, and it can move down as the price falls."`, replace that sentence with `"It does not trail on its own; a saved stop keeps it from moving down while your trades in this position don't change."` (both strings as module constants; a test pins both).
   - `full_note(h)`: append `carry.note` after the floor note (same join rule: space, skip empty). `degraded_notes` and `young_holding_stop.plain_notes` then carry it with no other change (check `plain_notes` really goes through `full_note`/`degraded_notes`; if not, wire it).

## 2. `stock_analyzer/stop_breach_text.py`

`stop_noun("Carried Stop")` → `"carried stop"` (not "profit-lock stop (Carried Stop)"). Test like `test_no_label_a_floor_can_produce_makes_stop_noun_print_saved`.

## 3. `stock_analyzer/db.py`

New `load_held_stop_carry_tickers_or_none() -> set[str] | None`: every saved ticker (one `select("ticker")`, no filter), upper-cased; None on ANY failure (no DB, missing table, error; record a Supabase error except for the missing table, like the loader); `set()` when the read succeeded and nothing is saved. Never raises. For the EOD sweep only.

## 4. `stock_analyzer/headless_alert_engine.py` (`_build_context`)

Next to the floor read (~:200-215):
- `held_stop_carry = db.load_held_stop_carry_or_none(held_tickers)` in try/except → None; `held_stop_carry_readable = held_stop_carry is not None`; pass `carry=held_stop_carry` to `build_held_stops`.
- Publish in the ctx dict (additive keys, comment like the floor ones): `held_stop_carry` (the rows as read, the writer's `prior_rows`), `held_stop_carry_readable`, `held_stop_journal_readable` (`trades_strict is not None`), `held_stop_journal_sigs` (`{T: journal_buy_sig(trades_strict, T)}` for each key of `held_stops`), `held_stop_session_closes` (`{T: Close}` of the bar in `held_data[T]["df"]` whose ET date == `today`; absent when there is no such bar or the Close is NaN / ≤ 0; use `held_stop._et_dates` for the index; never the last bar blindly).
- Pass all five through in `compute_protective_alerts`' return (~:524) and `compute_eod`'s return (~:1922), beside `held_stop_floors_readable`.

## 5. `cron_runner.py`

- **Premarket** (after the floor block ~:659-667): `_carry_fail = _held_stop_carry.lane_failure(payload.get("held_stops"), payload.get("held_stop_carry_readable") is True)`; on a failure append `f"held_stop_carry: {_carry_fail}; stops may be lower than the carried stops (protective alerts that were computed were still sent)"`; elif readable is False, log one line (mirror the floor's).
- **New `_eod_held_stop_carry(payload, now_et) -> list[str]`**, called in `_run_eod` right after `_eod_held_stop_floors`. NEVER raises (outer try → one failure line). Steps:
  1. `as_of = now_et.date()`; not a trading day → log, `[]`. `held_stops` not a dict → log, `[]`.
  2. `held_stop_carry_readable is not True` → log "NOT written … (if the table is new: apply docs/sql/held-stop-carry.sql)", return that failure. `held_stop_journal_readable is not True` → log + failure "held_stop_carry: the trade journal could not be read; not written" (fail closed, plan §3).
  3. `session_closes = payload["held_stop_session_closes"]` only when `now_et.hour >= ALERT_EOD_HOUR_ET`; otherwise `{}` and log "forced before the close: no new carried stop is seeded today" (a forced mid-session run would pass a partial bar; with `{}` the seed guard blocks seeds and valid rows still advance).
  4. `rows = _held_stop_carry.rows_to_write(held_stops, as_of, journal_sigs=…, avg_costs={}, prior_rows=payload["held_stop_carry"] or {}, session_closes=…, journal_readable=True, carry_readable=True)`.
  5. `carry_check_failures(held_stops)` → one log line per name + failure `"held_stop_carry: carry check failed for X, Y"` (red, like C3cFloorBug).
  6. One log line per name whose carry state is APPLIED: `"held_stop_carry: {t} carried stop bound ({basis}): carried {value:.2f} vs computed {computed:.2f} (+{diff:.2f})"`.
  7. Save via `db.save_held_stop_carry(rows)` when non-empty; log "wrote N row(s) (A, B)" / "nothing to write"; a failed save → failure line with the detail.
  8. Sweep: `held = {str(t).strip().upper() for t in payload["held_tickers"]}` only when `held_tickers` is a list, else None; `saved = db.load_held_stop_carry_tickers_or_none()`; `to_del = _held_stop_carry.sweep_tickers(saved, held)`; None → log "sweep skipped (held or saved set unknown)"; non-empty → `db.delete_held_stop_carry(sorted(to_del))`, log, failure line on (False, detail). Sweep runs after the save and even when no row was written.
- Import `held_stop_carry as _held_stop_carry`.

## 6. `app.py`

Mirror `_cached_held_stop_floors` / `_held_stop_floors_for` (~:2820-2840): `_cached_held_stop_carry(tickers: tuple, et_date: str)` (`ttl=86400`, RAISES when the loader returns None so "couldn't check" is never cached) and `_held_stop_carry_for(tickers)` → dict or None. In `_held_stops_for`, read it ONCE before the thread fan-out with the same ticker set as the floors, and pass `carry=_carry` to `build_held_stops`. Nothing else in app.py in commit 1.

## 7. Commit 2 — display

- `portfolio.build_portfolio_df`: columns `"Stop Carried"` / `"Stop Carried On"` set ONLY when `carry.state == "applied"` (value, ISO set_on), else None; same pattern as `"Stop Saved"` (~:586-600, :704-705).
- `held_stop_view.from_row`: `"carried"` / `"carried_on"` from those columns. A `_carried_part(h)` like `_saved_part` (reads `carry` dict state applied, else the from_row keys).
- `component_rows`: a `"carried"` row when carried: label `"Carried Stop"`, text `f"The stop carried from {on}. It is held here because your trades in this position haven't changed since then and today's figure came out lower."` (omit " from {on}" when unknown).
- `what_if` / `driver_label`: when the floor did not bind but the carry did, `saved` / `saved_label` carry the carried value and `"Carried Stop"` (so `driver_label` names it when it is above every rebuilt part). `what_if` already returns None on prior_session; this matters for a carried partial result.

## 8. Tests (new `tests/test_held_stop_carry_wiring.py`; update existing call sites)

Every existing `build_held_stops(` test call gets `carry={}` (18 sites in test_held_stop_floor_wiring / _switch / _wiring / test_young_holding_stop). Then:

1. `carry` is a required keyword with no default (signature check, like `test_floors_is_a_required_keyword_with_no_default`).
2. Inert golden: `carry={}` gives the same core fields as before on full / partial / prior_session / buy-day / unavailable scenarios (only the additive keys differ); `carry=None` changes only the carry state (unreadable) and the degraded note on partial / prior_session names, never the stop.
3. Mismatch path (status != ok): a valid saved row above today's prior-close figure binds: stop = saved, label "Carried Stop", carry state applied, the note swap; below it: not_binding, stop unchanged, note swap applied.
4. Young-listing ok prior_session path (enough bars for a stop, < 60 warm-up bars before the buy) gets the carry too, and its note uses the new "No part of the full stop can be computed yet" wording.
5. Full basis: carry state n/a, stop unchanged even with a higher saved row.
6. Buy-day paths (normal fill and the rejected-fill fallback): `buy_day_path` True and no `"carry"` key; `rows_to_write` writes nothing for them.
7. `episode_status` present on every path (ok / mismatch / no_history / unreadable / buy-day / unavailable / exception = None).
8. U6: status != ok calls `fetch_history(t, "1y")` before touching the bundle; the bundle is used only when it raises or returns None.
9. **Slow-bleed invariant end to end** (plan §8): a mismatched holding falling 0.5 ATR a session; simulate day by day: EOD `build_held_stops` → `rows_to_write` → feed the rows as `carry` to the next day's premarket build; the first premarket breach (`is_stop_breached(last close, stop)`) is exactly the morning after the first close ≤ the stop in force, never a day earlier; with `carry={}` every day it never breaches.
10. Time-context: a Saturday `as_of` and Thanksgiving 2026-11-26 read Friday's / Wednesday's row and the EOD writer writes nothing; the DST date 2027-03-14 (a Sunday: no write); a missed EOD on D−1 (window term recovers).
11. `stop_noun("Carried Stop") == "carried stop"`; `full_note` includes the carry note.
12. Headless: `_build_context` passes `carry=` (AST pin, mirror `test_held_stop_floor_wiring`), publishes the five keys, `held_stop_session_closes` picks only the bar dated `today` (a frame whose last bar is yesterday gives no entry).
13. Cron premarket: red only with unreadable carry AND a partial / prior_session name; one log line otherwise.
14. Cron EOD: writes on readable; nothing + failure on unreadable carry or journal; nothing on a non-trading day; forced before `ALERT_EOD_HOUR_ET` → `session_closes={}` (a would-be seed is not written, a valid row still advances); carry_check_failures → failure line; sweep deletes saved − held, skipped (no delete call) when held or saved is None; `_run_eod` calls `_eod_held_stop_carry` (AST pin).
15. App: `_held_stops_for` passes `carry=` (AST pin, like the floors one); `_cached_held_stop_carry` raises on None.
16. Commit 2: `"Stop Carried"` columns only when applied; explainer row; `driver_label` names "Carried Stop" when it binds above every part.

Run the targeted held-stop / cron / headless test files plus `python scripts/check_antipatterns.py` and `python scripts/check_constants_documented.py`; report counts. Do not commit.
