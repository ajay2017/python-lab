# #26 C3c: persisted held-stop floor (spec)

**Status (2026-11-16): C3c-1 BUILT** (pure `stock_analyzer/held_stop_floor.py`, unwired; Opus FIX-FIRST (an in-span missing ref / bind session was skipped, not failed) → SHIP; owner refined U4 to partial only). Carried to C3c-4: `_wrap` must pass `floor` / `stop_set_on` / `label` through (the writer fails closed, so a missed pass-through writes no rows); log saved − computed when a floor binds on a full day. Carried to C3c-2: report ref and bind close drift separately, with the bind bar's age. **C3c-2 BUILT and RUN by the owner 2026-11-16** (`--floor`; 13 holdings, today + `--bundle-start 2026-06-24`): full-day binds 0 / 13; fallback binds ABBV +$0.06, WAL +$0.39; NEWLY_BREACHED_ON_FALLBACK none; ref drift 0.00% on all; bind drift max 0.61% (WAL, 9 sessions old) vs the 2.0% tolerance, so U1 stands; U8 none; rollover: AAPL / ABBV lose the entry anchor but are trailing-bound, so the fallback stop moves by cents only. Rollover date corrected: the anchor needs the bar 60 sessions before entry (2026-06-23 for the 09-17 episodes) and the live 6-month bundle started 2026-05-14, so the anchor is lost from about 2026-12-23, not 12-08 (the 12-07 deadline and 12-02 push target are kept as slack). **C3c-3 BUILT 2026-11-16** (`docs/sql/held-stop-floors.sql`, `docs/sql/readonly-held-stop-floors-check.sql`, `db.load_held_stop_floors_or_none` / `db.save_held_stop_floors`; Opus FIX-FIRST (the upsert never refreshed `written_at`) → SHIP). The DDL is tighter than §1's table: `< 'Infinity'` on every positive numeric column and NOT NULL on `stop_set_on`, `split_ratio`, `avg_cost`, `bind_session`, `bind_close` (the writer always emits them); only `first_buy_price`, `last_buy`, `ratchet_label` stay nullable. The save returns `(ok, detail)` and sets `written_at` on every row. Carried to C3c-4: pass a real ticker list (`None` = couldn't check); any `(False, detail)` from the save is a lane failure and "wrote N" only from a True detail. **DDL APPLIED by the owner 2026-11-16** (verify block: relrowsecurity true, policy "Allow all (service role)" {service_role} ALL, service_role INSERT and UPDATE true, 0 floors). NEXT: C3c-4 + C3c-5 (one push by 2026-12-02, after an EOD).
**Earlier status (2026-11-15): DESIGNED; owner ACCEPTED U1–U11 as recommended (2026-11-15).** NEXT: C3c-1 (pure core, unwired) → implementer → Opus reviewer; then C3c-2 dry run (owner runs it, read together before C3c-4). Go-live: owner applies `docs/sql/held-stop-floors.sql` first, push C3c-4 + C3c-5 by Wed 2026-12-02 after an EOD. Opus planner (claude-opus-5-5): PROCEED WITH CHANGES. Parent plan: `docs/plans/held-trailing-stop.md` (C3 requirement 4, D1). Deadline 2026-12-07 (AAPL / ABBV episodes from 2026-09-17 outgrow the 6-month frame about 2026-12-08). Lead checked two anchors at HEAD: the fallback is always forced to `partial` (held_stop_wiring.py:442-454) and C1's invariant text (held_stop.py:59-64). Every other file:line below is the planner's, to be re-checked by the implementer and reviewer.

## Verdict and the three changes

The plan's one-liner "floor = max(last full-basis stop per (ticker, episode start), today's)" is unsafe as written. Three changes:

- **(a) Trade-and-policy fingerprint.** The floor protects exactly C1's own invariant, "with no trade in between, stop(D+1) ≥ stop(D)" (held_stop.py:59-64). So it must lapse on an add, a split row, a fill edit or a policy change, which C1 permits to lower the stop.
- **(b) Price-history check.** Each saved row carries the closes it was built from. The floor applies only while today's frame still agrees with them. Without this, an unrecorded split, or a bad tick later corrected, turns the floor into a false "sell all at the next open". That is a worse failure than the one C3c fixes.
- **(c) Writer timing.** The EOD lane writes stop(D), built from sessions before D, never stop(D+1) from a just-closed bar that may still be revised.

Cheaper alternatives (a 1y bundle, a persisted frame) cost more and protect less.

### How the date risk works

- The long fetch is primary. The 6-month bundle is the fallback and is always forced to `partial` (held_stop_wiring.py:453-454).
- So the risk appears only on a long-fetch-failure day. Past the coverage date, AAPL / ABBV then lose the entry anchor ("not enough history before entry", held_stop.py:358-359). The trailing ATR is seeded from a different bar, and the stop can drop.
- There is no evidence the floor would bind on a normal day. It is insurance against an outage landing on the rollover, and C3c-2 measures it before go-live.

## 1. Storage (DDL applied by the owner)

`docs/sql/held-stop-floors.sql`, styled on `new-position-budget-allocations.sql`. Table `held_stop_floors`, PRIMARY KEY (ticker, episode_start).

| Column | Meaning / constraint |
|---|---|
| `ticker` | text, upper-case, not blank |
| `episode_start` | DATE, not null |
| `as_of` | DATE, not null: the ET session the stop was in force for, built from sessions < as_of |
| `stop` | NUMERIC, not null, CHECK > 0 AND < 'Infinity' |
| `stop_set_on` | DATE: the first as_of at which this value was reached (what the note shows) |
| `binding` | CHECK IN ('entry','trailing','ratchet') |
| `ratchet_label` | text, nullable |
| `first_buy_price` | NUMERIC, nullable (a SPLIT-seeded episode has none) |
| `split_ratio` | NUMERIC > 0 |
| `last_buy` | DATE, nullable |
| `avg_cost` | NUMERIC > 0 |
| `ref_session` / `ref_close` | DATE / NUMERIC, not null, > 0 |
| `bind_session` / `bind_close` | nullable |
| `policy_sig` | text, not null |
| `source` | CHECK IN ('cron:eod') |
| `written_at` | timestamptz default now() |

- **Table-level CHECKs:** `ref_session < as_of`, `episode_start < as_of`, `stop_set_on <= as_of`.
- **RLS:** `ENABLE ROW LEVEL SECURITY`; policy "Allow all (service role)" `FOR ALL TO service_role USING (true) WITH CHECK (true)`.
- **Verify block:** `relrowsecurity`, `pg_policies`, `has_table_privilege(...INSERT)`, row count.
- **Episode identity:** a same-day sell-all and re-buy can't collide on (ticker, start). A row needs a full-basis day (start < as_of), and the next episode starts after the old one ends. The fingerprint columns cover any journal edit.
- **Old-episode rows are kept** as an audit trail (U9).
- **`policy_sig` is the regime version** for this history table (DoD #8).

**Before the DDL is applied:**
- The reader gets `None` for the missing table, and behaviour is exactly today's. Degraded notes gain one disclosure sentence (§4).
- The writer fails the EOD lane red with "apply docs/sql/held-stop-floors.sql" (precedent: cron_runner.py:949-952).
- The owner therefore applies the DDL BEFORE the wiring push.

## 2. Writer

- **EOD lane only:** `_run_eod` (cron_runner.py:773), with `ALERT_EOD_HOUR_ET` = 16 and a trading day.
- **What it writes:** the EFFECTIVE (already floored) AUTO result from `build_held_stops`, with `as_of` = `now_et.date()`. It writes `payload["held_stops"]`, NEVER `port_df["Stop"]`, so a manual stop can't leak in.
- **Why stop(D), not stop(D+1):** bar D at 16:0x may be a preliminary close.
- **The app never writes.** `db.save_*` still starts with `if is_readonly()`.

**`rows_to_write` (pure) writes a row only when:**
- basis == "full" (never partial, prior_session, buy_day or unavailable);
- the stop is finite and > 0, and the episode, ref and bind fields are present;
- `as_of` is a trading day (`write_guards.split_non_trading_rows`), so a forced weekend run writes nothing;
- the new `as_of` is not earlier than the stored row it read.

**What the row holds:**
- When the saved floor bound, the row keeps the saved `stop`, `binding`, `bind_*` and `stop_set_on`, and refreshes `ref_session` / `ref_close` (the floor was just verified on this frame).
- When the floor was invalid, the fresh value overwrites it. This is how a corrected bad tick or a split heals at the next full EOD.

**Write mechanics and failures:**
- One batched upsert, `on_conflict="ticker,episode_start"`, so a same-evening re-run writes the same row.
- If the floor READ failed (`None`), the write is skipped (it can't overwrite rows it never saw) and a failure is added.
- A write error adds `"held_stop_floor: ..."` to `failures` (red heartbeat).
- The writer never raises and never blocks the lane's other steps.

## 3. Reader: the floor rule

`effective = max(computed, row.stop)` only when ALL of these hold:

1. **Basis:** basis ∈ {full, partial} and the computed stop is not None. Never on prior_session, unavailable or buy_day.
2. **Row lookup:** `floors[TICKER][current episode_start]` exists and `row.as_of <= as_of` (never a future row).
3. **Fingerprint (`match_row`)** equals today's:
   - `first_buy_price` (round 6), `split_ratio` (round 9), `last_buy`, `avg_cost` at cents, and `policy_sig`;
   - `policy_sig` = `ATR_STOP_MULT`, `HELD_TRAIL_ATR_MULT`, `HELD_TRAIL_START`, `HELD_STOP_WARMUP_BARS`, `HELD_STOP_MIN_BARS`, `HELD_STOP_FILL_TOL_PCT`, `HELD_STOP_SHARES_TOL`, `repr(STOP_RATCHET_LEVELS)`, plus "v1";
   - avg cost is average-method (db.py:3069-3077) and unchanged by a sell, so a partial sell keeps the floor (matches C1).
4. **Close check (`verify_closes`)** against the SAME frame that produced today's result:
   - take the dates {ref_session, bind_session} present in `completed_bars(frame, as_of)`;
   - at least one must be present, and each present one must have |close/saved − 1| × 100 ≤ `HELD_STOP_FLOOR_CLOSE_TOL_PCT`;
   - a bind_session older than the frame's first bar is skipped.

   This catches splits (≥ 33%) and corrected ticks, and passes the dividend drift of the adjusted bundle fallback.

| Event | Result |
|---|---|
| Full sell / rebuy | New key, no row |
| SPLIT row | `split_ratio` changes, invalid (dropped, not rescaled) |
| Unrecorded split | Close check fails, invalid |
| Share-count mismatch | prior_session, not applied |
| Late BUY that moves the start | New key |
| Late add | `last_buy` / `avg_cost` change, invalid |
| Late partial sell | Still valid |
| Fill-price edit | Invalid |
| Policy change | Invalid |
| Floor above today's price | A breach, by design (U10) |

- **No "floor vs price" sanity bound.** An uncorrected bad tick affects today's full compute identically; a corrected one fails the close check.
- **Manual stop precedence is unchanged:** `compose_with_manual` runs after the floor (portfolio.py:599; Analysis via `analysis_manual_override`).

## 4. Basis, label, banner (owner approves the wording: U6)

- **Basis is unchanged.** A floored fallback stays `partial` (degraded, banner, lane rules). The "lower bound" claim stays true.
- **Label:** the row's own component label via `label_for(basis, row.binding, row.ratchet_label)`. Don't add a new "Saved Stop" label: `stop_breach_text.stop_noun` (stop_breach_text.py:85-103) would print "profit-lock stop (Saved Stop)".
- **The result gains** `floor = {state: applied|not_binding|none|invalid|unreadable|n/a, value, set_on, reason}`.

Proposed note sentences (escaped `\\$`):
- **Applied (degraded day):** "The stop is held at \$X, the full-history stop saved on {set_on}, because your trades in this position haven't changed since then and the saved prices still match. Today's figure from the shorter history was \$Y."
- **Invalid (degraded day only):** "A saved full-history stop (\$X from {set_on}) wasn't used because {your trades in this position changed since then | the price history no longer matches the prices it was saved from (for example a stock split) | the stop rules changed since then}."
- **Unreadable and the name is degraded:** "The saved full-history stop couldn't be read, so this stop may be lower than on earlier days."
- **Full day:** no note, no banner; a cron log line when the floor binds.
- **No new "only moves up" claim.**
- **Offline is three-state:** `floors=None` = couldn't check, `{}` = read with no rows.

## 5. Both sites switch together

- **Signature:** `build_held_stops(holdings, held_data, trades_df, as_of, *, fetch_history, floors, fetch_range=None)`, with `floors` REQUIRED and no default.
- **Applied once** inside `_one` (held_stop_wiring.py:410-458) with the frame that produced the result: `long_df` (full path) or `frame` (fallback).
- **Cron:** `_build_context` (headless_alert_engine.py:197-201) reads `db.load_held_stop_floors_or_none(held_tickers)` once.
- **App:** `_held_stops_for` (app.py:2779-2813) reads floors once before the thread fan-out.
- **One push for both.**

## 6. Time-context matrix (each a test)

| Case | Expected |
|---|---|
| Saturday or holiday visit | Friday's row applies (ref Thu in frame) |
| Thanksgiving | 2026-11-27 uses the 11-25 row |
| DST | 2026-11-02 and 2027-03-15 |
| After hours (20:00 ET) | Same-day or D-1 row |
| EOD at 20:30 EST = 01:30 UTC | Writes the ET date from `now_et` |
| Late-logged add | Invalid, fresh row at EOD |
| Late first BUY that moves the start | No match |
| Late SELL closing the episode | Key changes |
| Late partial SELL | Floor still valid |
| A bar dated as_of in the frame | Bit-identical result and row |

## 7. Sibling sites

**Read the floored value automatically:**
- `portfolio.build_portfolio_df` (portfolio.py:564-675; new "Stop Saved" / "Stop Saved On" columns, set only when applied);
- `analysis_manual_override` (app.py:25734);
- `brief_breached_set` / `held_sig` (held_stop_wiring.py:529-563; no `_SYNTH_SCHEMA_VER` bump unless the implementer finds a structural change);
- `held_stop_view.display_stop` / `held_exit_stop_caption` / `basis_caption`;
- the `portfolio.alerts` stop bands, the ADD_WINNER gap gate, the risk advisor and the email alerts;
- `degraded_notes`.

**Need edits:**
- `held_stop_view.from_row` / `component_rows` (:36-108): a "saved" row, shown only when it binds (U5);
- `driver_label` / `what_if` (:111-155);
- app `_render_stop_ladder` (app.py:1801-1802, :1816-1821);
- the headless `_build_context` return (:245-281) and the `compute_eod` payload (:1770-1790);
- the cron premarket failures (:587-596) and the EOD writer step;
- the User Guide;
- `scripts/held_stop_dryrun.py`.

**Deliberately unchanged:**
- daily_briefing.py:2522-2523 (the add-sizing entry stop);
- the `held_stops_failed` rule (headless :211-221);
- `scripts/exit_ladder_replay.py`.

## 8. Where the decision lives

- **New gate module `stock_analyzer/held_stop_floor.py`** (pure, reads constants at call time):
  - `policy_sig() -> str`
  - `episode_fingerprint(ep, avg_cost) -> dict`
  - `match_row(row, ep, avg_cost, as_of) -> (bool, reason)`
  - `verify_closes(row, frame, as_of, *, tol_pct) -> (bool, reason)`
  - `apply_floor(result, ticker_rows, *, ep, avg_cost, frame, as_of, readable) -> dict` (no input mutation)
  - `rows_to_write(held_stops, as_of) -> list[dict]`
  - `lane_failure(held_stops, readable) -> str | None`
- **C1 additive** (held_stop.py `_result` :272-284 and the returns :328-433): `last_close`, `bind_session`, `bind_close`.
- **`_wrap`** (:156-176) gains `episode` {start, first_buy_price, split_ratio, last_buy}.
- **`_GATE_FILES`** adds `held_stop_floor.py`, with its review-policy.md line in the same commit. `held_stop_view.py` stays non-gate.
- **No new `st.session_state` key** (the app uses `st.cache_data`), so nothing to register in coord_freshness.

## 9. Constants

- **One new (owner, U1):** `HELD_STOP_FLOOR_CLOSE_TOL_PCT = 2.0`.
- **Why 2.0:** above the adjusted-bundle dividend drift for closes one to a few sessions old, far below any split. C3c-2 confirms it.
- **Not in `policy_sig`.**
- **Gets an architecture constants-table row.**

## 10. Chunks (C3c-4 and C3c-5 in one push, after an EOD close)

1. **C3c-1. Pure core, unwired (reviewer).**
   - held_stop.py additive fields, `held_stop_floor.py`, the constant, its architecture row, `_GATE_FILES` + review-policy.md, `tests/test_held_stop_floor.py`.
2. **C3c-2. Read-only dry run** (implementer; no review, read-only script).
   - Adds a `--floor` mode to `scripts/held_stop_dryrun.py`, with tests.
   - Per holding: replay the writer over the last N sessions, then today on the long frame vs the bundle-only fallback.
   - Reports: binds-on-full / binds-on-fallback Δ, NEWLY_BREACHED_ON_FALLBACK, close-check % diffs vs the tolerance, and names permanently `partial` (U8).
   - `--bundle-start` simulates the post-12-08 rollover.
   - The owner runs it and the lead reads it with the owner before C3c-4.
3. **C3c-3. DDL + db.py (reviewer).**
   - `docs/sql/held-stop-floors.sql`.
   - `db.load_held_stop_floors_or_none(tickers)`: `_read_all_pages`, dedupe (ticker, episode_start); `[]` gives `{}` with no call; failure gives None.
   - `db.save_held_stop_floors(rows)`: read-only no-op, `_sanitize_for_write`, upsert, never raises.
   - The owner applies the DDL and runs the verify block.
4. **C3c-4. The switch (reviewer, combined diff with C3c-5).**
   - wiring (`floors=` required), the headless payload keys, cron premarket failure + EOD writer step with log lines;
   - app `_cached_held_stop_floors(tickers, et_date)` (cache_data ttl 86400, raises on None) + `_held_stop_floors_for`;
   - the portfolio columns;
   - existing tests pass `floors={}`.
5. **C3c-5. Display + docs.**
   - held_stop_view, app explainer, User Guide;
   - requirements F-315, architecture, plan Status, shipped-log, queue, CLAUDE.md, memory `project_atr_stop_never_fixed_26`.

## 11. Measurement and live checks

- **Expectation:** binding on full days by cents only; fallback-day amounts from C3c-2.
- **Live checks:**
  - (i) the first EOD log reads "held_stop_floor: wrote N";
  - (ii) `docs/sql/readonly-held-stop-floors-check.sql`;
  - (iii) next day, no new banner and no "saved" explainer row on a normal day;
  - (iv) no `held_stop_floor` failure in the premarket log.
- **The degraded-day leg can't be forced;** the dry run is the evidence.

## Owner decisions (planner's recommendation in brackets; ALL ACCEPTED by the owner 2026-11-15, U6 wording as written)

- **U1** Close-check tolerance [new `HELD_STOP_FLOOR_CLOSE_TOL_PCT = 2.0`, confirmed by C3c-2].
- **U2** Writer lane [EOD only, as_of D; alternative: also premarket].
- **U3** Apply the floor on full-basis days too [yes: keeps the explainer's "only moves up" claim true; otherwise false by cents].
- **U4** Premarket red heartbeat when floors are unreadable AND a name is degraded [yes; log-only otherwise]. **Refined by the owner 2026-11-16 (C3c-1 review): partial only.** Red only when floors are unreadable AND at least one name's basis is `partial`; a prior_session / unavailable name or a missing result doesn't count, because the floor can never lift those (`held_stop_floor.lane_failure`).
- **U5** Explainer "saved" row [only when it binds].
- **U6** Approve the §4 note sentences.
- **U7** Go-live [DDL first, push by Wed 2026-12-02 after an EOD].
- **U8** Names permanently `partial` on the long frame never get a floor [accept; the dry run lists them].
- **U9** Keep old-episode rows, no pruning [keep].
- **U10** A floor-only breach on a fallback day is a hard "Sell all at the next open" [yes: the floor is a verified lower bound of the true stop].
- **U11** Use the floor on a journal-outage day [no: the journal and floors share one DB].

## Risks

- **Single writer (EOD), two readers (app + cron), no session key.** Before EOD the app has the D-1 row, which equals that session's value, so the app and the email agree.
- **The main new hazard is a false sell-all after an unrecorded split.** The close check guards it, and it must be tested. Residual: a corrected tick on a bind bar outside a fallback frame, healed at the next full EOD.
- **DDL not applied:** the reader is inert with a disclosure; EOD goes red until it's applied.
- **A floored stop shrinks add room** (ADD_WINNER gap) and can raise "at risk" bands on fallback days. Both point toward the true stop.

## Tests the build must include

- **Invariant property (key).** 40 synthetic sessions, no trades, EOD write each day, random fallback days with dividend-adjusted closes and a rolled window. The effective stop never drops. The same run without floors shows a drop.
- **Split guard.** A 2:1 split in the frame with no SPLIT row: floor not applied, and the name is not in `brief_breached_set`. With a SPLIT row: fingerprint invalid.
- **Close-check boundaries.** At the tolerance: valid; +0.01: invalid; a 0.8% dividend: valid; ref missing but bind present: verifies; neither present: invalid; a bind before the frame start: skipped.
- **Fingerprint.** Each field changed is invalid with the right reason; a sub-cent avg-cost change stays valid; each `policy_sig` constant monkeypatched is invalid; a late add is invalid; a late partial sell is valid; a moved start doesn't match; a future row is ignored.
- **Writer.** Only `full` is written; a non-trading as_of writes nothing; a floored result keeps `stop_set_on` / `bind_*`; a fresh higher value sets `stop_set_on` = as_of; floors None skips the write and adds a failure; a write error adds a failure; an AST pin that the writer reads `payload["held_stops"]`; a bar dated as_of gives an identical row.
- **Time matrix:** all of §6.
- **Inert golden.** `floors={}` is bit-identical on every existing fixture; `floors=None` changes only degraded notes.
- **Offline.** None vs `{}` are never collapsed; the `lane_failure` cases.
- **db.** None paths; `[]` gives `{}` with no call; a read-only save makes no call; pagination; the on_conflict string.
- **AST parity.** Both production calls pass `floors=`; the app reader is cached per ET date and raises on None; `floors` has no default.
- **Manual precedence with a floor.** A manual stop between computed and floor loses; above the floor wins; Analysis equals `build_portfolio_df`.
- **Wording.** No "only moves up" in a partial note; `stop_noun` never reads "profit-lock stop (…Saved…)"; `plain_text_note` strips `\$`.
