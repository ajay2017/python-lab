# SlidingStop C4: escalation spec (lead, 2026-11-26)

**Status:** SPEC, for the implementer. Parent plan: `docs/plans/sliding-stop.md` §3 "(c) Escalation", §7 item 6, owner answers U3 / U4 / U5 (§9). C3 is shipped (`47b2c99d` + `43550786`): every held-stop result carries `episode_status` and a `carry` dict, and `held_stop_carry.notice_tier(result)` returns "error" / "warning" / None.

**Goal.** When a fixable journal fault (mismatch / no_history / unreadable) leaves a holding on a prior-close stop, say so where the owner acts: Home's dock ⚠️ Notices (error when the carry is NOT holding the stop, warning when it is) and the premarket lane (red when not carried, a footer line when carried). Plus U3: a one-session note when a fixed journal gives a full stop below the carried one. Never Act Today (U5: a data fault, not a trade). No constant; no stop value changes in this chunk.

## 1. `stock_analyzer/held_stop_wiring.py` (additive)

- `_one`'s `tagged()` also sets `"episode_reason": ep.get("reason")` (None when absent); `_wrap` default `"episode_reason": None`.
- `carried()`: when a saved row exists for the ticker (`isinstance(carry, dict)` and `carry.get(t)` is a dict), add `"carry_row_seen": {"stop", "source_basis", "as_of", "stop_set_on"}` (values copied from the row; `stop` as float or None) to the returned result, on every carry state including n/a. Absent otherwise. Read-only use: the U3 note below. Never feeds a stop.

## 2. New gate module `stock_analyzer/stop_basis_notice.py` (pure; add to `_GATE_FILES` + one review-policy line)

Pure, no I/O, no clock. Reads `build_held_stops` results only.

- `FIX_POINTER = "📒 Trade Journal → ⚠️ Fix drift between trades and holdings"` (that expander's exact title is in app.py ~35375; a test pins the string against app.py).
- `rows(held_stops) -> list[dict]`, sorted by ticker, one per result where `held_stop_carry.notice_tier(r)` is not None: `{ticker, tier, status, reason, carried (tier == "warning"), carried_value, carried_on}` (carried_* from `carry` when its state is applied, else None). Non-dict input → `[]`.
- `drop_rows(held_stops) -> list[dict]` (U3): results with `basis == "full"`, a finite stop, `carry_row_seen.source_basis` in ("partial", "prior_session") and `round(carry_row_seen.stop, 2) > round(stop, 2)`: `{ticker, stop, carried_value, carried_on}` (carried_on = row `stop_set_on` or `as_of`).
- `overall_tier(rows, drops) -> "error" | "warning" | "info" | None`: error if any error row, else warning if any row, else "info" if any drop, else None.
- `home_markdown(rows, drops) -> str | None`. Header by tier: error `"⚠️ **Stop basis: the stop for X isn't protected against a slow fall.**"` (names joined "X and Y" / "X, Y and Z"), warning `"⚠️ **Stop basis: X is on a carried stop until the trade journal is fixed.**"`. One bullet per row:
  - cause: mismatch → `f"{reason}"` (already "journal nets N shares but holdings show M"); no_history → reason; unreadable → "the trade journal couldn't be read".
  - error row: `"its stop is the last close minus {mult:g} x ATR and isn't carried, so it moves down as the price falls and a slow decline won't trigger it."` (`mult = max(ATR_STOP_MULT, HELD_TRAIL_ATR_MULT)`, read from constants at call time).
  - warning row: `f"its stop is held at ${v:.2f}, carried from {on}, while your trades in this position don't change."`
  - Then one closing line: mismatch / no_history rows present → `f"Fix the journal in {FIX_POINTER}; the full stop returns once the shares agree."`; unreadable-only → "This usually clears on the next refresh; if it doesn't, see 🩺 System Trust."
  - Drops (U3), one bullet each, also usable alone (info tier): `f"**{t}**: its stop is back on your full trade history at ${stop:.2f}, below the carried ${v:.2f} from {on}, because the fixed journal gives a lower figure."`
  - Dollars escaped for Streamlit markdown with `young_holding_stop.md_dollars` (exactly one rule for `$`); dates as ISO `YYYY-MM-DD` like the carry notes.
- `email_lines(rows, drops) -> list[str]`: plain text, no markdown, no `\$`: when any mismatch / no_history row, ONE line `f"Stop basis: fix the trade journal for {names} ({FIX_POINTER}); until then {'their stops are' if n>1 else 'its stop is'} not on the full history."`; plus one line per error row `f"{t}: stop is not carried; a slow decline won't trigger it."`. Drops: one line each (same sentence as Home, plain). The per-name degraded notes already in the footer are NOT repeated.
- `lane_failure(rows) -> str | None`: None unless an error row exists; else `f"{len} holding(s) on an uncarried prior-close stop because of a trade-journal fault ({', '.join(f'{t}: {cause}')}); a slow decline on {it/them} won't trigger a stop"`.

## 3. `stock_analyzer/home_notices.py`

Add source `"stopbasis"` to `SOURCE_ORDER` right after `"journalmatch"` and `SOURCE_LABELS["stopbasis"] = "Stop basis"`. Update the module's "17" counts in comments / docstrings to 18 where they describe the source list; check tests that pin the count or the order and update them.

## 4. `app.py` (Home)

- Declare `_alert_ph_stopbasis = st.empty()` right after `_alert_ph_journalmatch` in the dock's Notices tab (same declare-early / fill-late pattern, comment).
- Fill it where Home calls `_render_held_stop_notices(_held_stops, ...)` (~:6633), BEFORE that call: `_sb_rows = stop_basis_notice.rows(_held_stops)`, `_sb_drops = stop_basis_notice.drop_rows(_held_stops)`, `_sb_tier = stop_basis_notice.overall_tier(...)`, `_sb_md = home_markdown(...)`; when `_sb_md`: `.error` / `.warning` / `.info` on the placeholder by tier, then `_home_notice("stopbasis", _sb_tier)` inside that branch only. Wrapped in try/except like the neighbouring fill sites (a failure renders nothing and never breaks Home). The body's "Some stops are not on a full price history" banner is unchanged.
- No Act Today, no Brief change (U5).

## 5. `cron_runner.py` (premarket only)

After the C3 carry heartbeat block: `_sb_rows = stop_basis_notice.rows(payload.get("held_stops"))`, `_sb_drops = drop_rows(...)`; `_sb_fail = lane_failure(_sb_rows)` → `failures.append("held_stop_basis: " + _sb_fail + " (protective alerts that were computed were still sent)")` and a log line; `email_lines(...)` appended to `_footer_lines` (after the held-stop notes); one log line naming the rows' tiers when any. EOD / intraday unchanged.

## 6. Tests (new `tests/test_stop_basis_notice.py`, plus wiring pins)

- `rows`: error vs warning by carry state (applied / not_binding → warning; none / lapsed / unreadable / failed → error); ok-episode young listing → no row; full / partial → no row; non-dict → [].
- `drop_rows`: full below a carried prior_session / partial row → one; a row from a full day → none; equal at the cent → none; no `carry_row_seen` → none.
- `overall_tier` precedence; `home_markdown` headers, the cause per status, the unreadable-only closing line, name joins (1 / 2 / 3), `$` escaped exactly once per amount (no `$…$` pair can render as LaTeX), no literal `**` outside the header / ticker bolding; `email_lines` has no `\` and no `**`, one fix line for several names, no line for warning-only names beyond the fix line.
- `lane_failure`: None with warning-only rows; text with error rows.
- `FIX_POINTER` appears verbatim in app.py.
- Wiring: `episode_reason` on every path; `carry_row_seen` present only when a row exists, on a full result too, and never changes `stop`.
- AST pins: app.py fills `_alert_ph_stopbasis` and calls `_home_notice("stopbasis", ...)`; cron_runner's premarket calls `stop_basis_notice.lane_failure`; home_notices has "stopbasis" after "journalmatch".
- Run targeted files (held_stop_*, home_notices*, cron premarket tests, young_holding_stop, the new file), `scripts/check_antipatterns.py`, `scripts/check_constants_documented.py`. Do not commit.
