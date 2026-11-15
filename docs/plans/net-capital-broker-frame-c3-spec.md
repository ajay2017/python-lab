# U7 C3 (+C4 folded) build spec — net capital from the lower of journal and Robinhood

**Status:** SPEC 2026-11-14 (Opus planner `claude-opus-5-5`, re-located at HEAD `6e41f04`; DESIGN VERDICT: PROCEED WITH CHANGES). **Owner accepted D1–D5 as defaulted, 2026-11-14.** Parent plan: `docs/plans/net-capital-broker-frame.md` (U1–U10 accepted; C1 `b461060`, C2 `e9128cd`). No threshold changes, no new constants (`BROKER_FRAME_PAIR_MAX_MINUTES` 10 and `NET_CAPITAL_BROKER_GAP_NOTE_PCT` 1.0 already approved).

Planner changes vs the parent plan: (a) fold C4 into the same push so app and emails never disagree; (b) one new pure module is the only builder of budget inputs, so obligation 1 holds by structure, not just a test; (c) the status-line suffix is driven from the budget result only, with two cause-specific wordings; (d) two correctness guards the plan didn't cover: a split logged since the sync, and a frame with no prices for the holdings; (e) push both commits together (commit 1 alone gives the old app call sites an "unchecked" suffix).

## Owner decisions (2026-11-14, all as defaulted)

- **D1** Suffix driven from the budget result only, worded S1 / S2 (§2.3). (Per-ticker split rejected: after a correctly logged BUY the budget doesn't bind, so no suffix is needed.)
- **D2** Fold C4 (the three emails + `run_daily_budget`) into this build.
- **D3** Split guard: a ticker with a SPLIT row AFTER or UNKNOWN relative to the sync is excluded from the broker adjust and listed.
- **D4** A hand-saved cash note equal to the sync note is saved as "Entered by hand"; the field pre-fills blank over a synced note.
- **D5** Trade Journal buy preview gets no suffix (awareness; its % is already truthful).

## §1 Call sites at HEAD 6e41f04

**app.py decision consumers (all exist, all moved):** `_publish_new_position_budget` def :2830-2845; `_budget_downstream_plan` def :2867-2883; `_budget_price_map` :2848.
1. `_refresh_portfolio_cache_after_trade` (def :2886) budget republish :2982-2998 (`_rb_acct` / `resolve_net_capital` :2984 → `_publish_new_position_budget(...)` :2995) — the plan's ":2893"; this is the real sixth resolve site.
2. Home: drift block :2132-6176 with a NESTED `@st.cache_data(ttl=120) _cached_broker_snapshot` :6144-6146, `_render_broker_drift(_alert_ph_drift, _drift_verdict)` :6171; F-255 :6308-6314 (`_f255_acct`, `resolve_net_capital(total_val, ...)`); `build_daily_briefing(net_capital=_f255_net_cap)` :6637 (HIT-path freshness rebuild) and :7061 (MISS); budget postpass :6675-6678 and :7089-7092; `_SYNTH_SCHEMA_VER = 21` :6480; `_synth_sig` :6481-6500; Grow Today status captions `_budget_status_captions` :9586-9598.
3. Analysis: F-255 :25686-25692; `size_position`/`position_sizing` :25873-25894; `_budget_downstream_plan(...)` :25898-25901; status/over/lag captions :25939-25952; further `_f255_net_cap`/`_f255_basis` reads :27380, :27487 (CER), :27588, :27592.
4. Watchlist: F-255 :29704-29710; asks :29719-29725; plan :29726-29727; status captions :29741-29753; CER :30132.
5. Trade Journal buy preview :31365-31386 (`resolve` :31373, `budget_inputs` :31378, `buy_preview`).
6. Trade Journal add-concentration net-capital leg :31510-31525 (`resolve` :31515).
- The three `budget_view` `budget_basis` calls: `postpass` :178-181, `_plan` :731-734, `buy_preview` :895-898.

**Awareness siblings (stay journal figure = C6; AST pin allowlists them):** 18259-18273 (`_bl_net_cap`, the only remaining `resolve_net_capital` besides 19954), 19953-19955 (`_mg_net_cap`), 5964/5992 `_leverage_cache` (cash read, no resolve), 18813 regime cash, 40142+ Account totals + 40385 held-over-cap list, 49439 My Edge, cron_runner.py:924 (N1 EOD row).

**Headless / cron (C4):** stock_analyzer/headless_alert_engine.py F-255 :661-679; `build_daily_briefing(net_capital=...)` :726; snapshot read for drift :761-785 (AFTER F-255 today → must be hoisted); hand-built `budget_inputs` dict :817-824, payload :825-829; Watchlist email :1325-1360 (`resolve` :1342; `_e["net_capital"]`/`["capital_basis"]` :1354-1355); `_run_daily_budget` basis :1488-1497, budget dict :1583-1593, crash dict in `run_daily_budget` :1441-1446. cron_runner.py: scan payload :2005, budget call :2096-2101; intraday payload :2430, budget call :2489-2493. notify.py `_budget_banner` :84-111.

**New consumers since 11-07:** the C2 Account shadow (app.py:40233-40254, calls `db.load_broker_position_snapshot()` directly); `budget_view.lag_uncertain_text` (RecTime C3) reads cache fields only — unaffected. No other new `resolve_net_capital` / `budget_basis` / `budget_inputs` caller.

## §2 Design per obligation

### 2.0 New pure module `stock_analyzer/effective_capital.py`
`_GATE_FILES` (91 → 92), `tests/test_margin.py` `_GATE_MODULES`. Must NOT import margin, streamlit or db. Imports: `broker_frame`, `daily_budget`, `sync_timing`, `constants.BROKER_FRAME_PAIR_MAX_MINUTES`. Does NOT import `budget_view` (cycle).
- `budget_inputs(gross_book, net_capital, basis_state, acct_rec, frame=None) -> dict` — moved from `budget_view` (same 5 keys) + `"broker_frame": frame`; `budget_view.budget_inputs = effective_capital.budget_inputs` re-export alias (existing tests keep working).
- `reconciled_basis(inputs, trades_df, day, now) -> dict` — `basis = daily_budget.budget_basis(bi["gross_book"], bi["net_capital"], bi["basis_state"] or "unknown", bi["cash_balance"], bi["cash_as_of"], trades_df, day, now)`; returns `broker_frame.reconcile_budget_basis(basis, bi["gross_book"], bi["cash_balance"], bi.get("broker_frame"))`. The ONLY budget-basis entry point for `budget_view` (3 sites) and `_run_daily_budget`.
- `split_tickers_since(trades_df, captured_at) -> frozenset` — upper-cased tickers with an action containing "SPLIT" whose `sync_timing.row_codes` is AFTER or UNKNOWN (positional lookup, as `broker_sync.drift_excuses`); frozenset() on None / any failure.
- `resolve(gross_book, cash_rec, journal_net, journal_state, snapshot, holdings_df, price_map, now, *, trades_df=None) -> dict` (never raises): frame = `broker_frame.broker_frame(snapshot, cash_rec, holdings_df, price_map, now, pair_max_minutes=BROKER_FRAME_PAIR_MAX_MINUTES, exclude_tickers=split_tickers_since(trades_df, snapshot captured_at))` (exception → None); rec = `broker_frame.reconcile_net_capital(journal_net, journal_state, frame)` (exception → the unchanged-journal dict); returns `{"net": rec["net"], "state": rec["state"], "rec": rec, "frame": frame, "acct": cash_rec, "cash_as_of": cash_rec.get("updated_at") or None, "captured_at": snapshot.get("captured_at") or None, "budget_inputs": budget_inputs(gross_book, journal_net, journal_state, cash_rec, frame=frame)}` (None-safe for cash_rec / snapshot).
- `broker_fields(basis) -> dict` — `{"basis_source": basis.get("source") or "journal", "broker_gap": basis.get("gap"), "broker_rec": {k: basis.get(k) for k in ("state","source","gap","checked","journal_net","frame_state","frame_adjust")}}` (primitives only, no Timestamp).
- `log_line(eff) -> str` — `"net capital source={source} gap={gap:.2f|n/a} frame={frame_state|none} state={state}"`, e.g. `net capital source=journal gap=0.00 frame=ok state=levered`.
- `budget_log_suffix(budget) -> str` — `" · basis_source={s|n/a} broker_gap={g:.2f|n/a}"`.

### 2.1 Obligation 1 — the budget gets the RAW journal net
`budget_inputs` is built in exactly one production place: inside `resolve`, from `journal_net` / `journal_state`. The reconciled net is exposed only as `["net"]` / `["state"]` (F-255 inputs). app.py has NO `budget_inputs(` call; headless loses its hand-built dict. `_publish_new_position_budget` and `_budget_downstream_plan` take the whole helper dict (`ncap`) and read `ncap["budget_inputs"]` — no site can hand a net figure to budget code. `reconcile_budget_basis` docstring adds: "basis_result MUST come from `daily_budget.budget_basis` fed the RAW journal net/state (`margin.resolve_net_capital`); never feed `reconcile_net_capital`'s net: a BUY logged after the sync would be subtracted twice (G + C − 2K)." Integration test: §5 T1.

### 2.2 Obligation 2 — one read per number
app.py module level (after `_publish_brief_coord_caches`, BEFORE `def _publish_new_position_budget`; not inside the `_budget_downstream_plan` → `_refresh_portfolio_cache_after_trade` segment — a test asserts "trades_df" is absent there):
```python
@st.cache_data(ttl=120, show_spinner=False)
def _cached_broker_snapshot():
    return db.load_broker_position_snapshot()

_NC_UNSET = object()
def _effective_net_capital(gross_book, *, port_df=None, holdings_df=_NC_UNSET,
                           acct_rec=_NC_UNSET, book_known=True):
```
Body: `_now = _now_et()`; acct_rec = `db.load_account_cash()` unless passed (try → None); `_margin_mod.resolve_net_capital(gross_book, acct_rec, ACCOUNT_CASH_STALE_DAYS, _now)` when `book_known` else `(None, "unknown")` (exception → `(None, "unknown")`); `_cached_broker_snapshot()` (try → None); holdings_df defaults to `st.session_state.get("holdings_df")`; price map = `_budget_price_map(port_df if port_df is not None else st.session_state.get("_port_df_enriched"))` (the same prices the gross book was valued at); return `_effective_capital.resolve(..., trades_df=st.session_state.get("trades_df"))`. Delete Home's nested loader (:6144-6146); the drift block calls the module-level `_cached_broker_snapshot()`; the Account shadow uses the helper's frame. One snapshot object per 120 s.

### 2.3 Obligation 3 (decided, D1) — suffix from the budget result only
After a correctly logged post-sync BUY, the budget's journal basis (lag-subtracted) == the frame basis → gap = 0 → no suffix; F-255 tightens by the buy's cost silently and truthfully (owner U8). `broker_frame.disclosure_text` — replace the "checked" branch:
- **S1**, when `max(0, frame_adjust) + 1.0 >= gap` (the books themselves differ): `" · net capital taken from Robinhood's positions: ~{$gap} below your journal's figure (see 💰 Account → Net capital check)"`
- **S2**, otherwise (the lag part dominates: a sell logged as after the sync is already in it): `" · net capital taken from Robinhood's positions: ~{$gap} below your journal's figure, because a sell logged as after Robinhood's last sync is already in it (check that trade's fill time)"`
- The 1.0 is a module-local `_WORDING_ROUND_USD = 1.0` commented "display rounding, not policy" (as `broker_frame_view._SAME_BELOW_USD`).
- Unchecked string unchanged: `" · sizes not cross-checked against Robinhood's positions ({why})"`.
- `budget_view.status_line_text`: `line = hold_reasons.budget_status_line(...) + broker_frame.disclosure_text(cache.get("broker_rec"), NET_CAPITAL_BROKER_GAP_NOTE_PCT)` THEN mask (the suffix gets masked + escaped by the existing call sites). Missing `broker_rec` → "".
- `notify._budget_banner` (state ok) appends the same suffix to the status line (HTML-escaped, not masked, as today).
- `hold_reasons.py` needs NO change.

### 2.4 Obligation 4 — masking / escaping
`disclosure_text` docstring: "callers MUST (C3) mask with `budget_view._USD_RE` and escape `$` for markdown." Satisfied structurally by 2.3; the `$` escape is already done by all three app call sites (existing test pins it).

### 2.5 Obligation 5 — implausible rule + a no-prices guard (edits `broker_frame.py`, gate)
Replace the empty-snapshot test with: `j_held = {t for t,v in journal.items() if v > BROKER_DRIFT_SHARE_TOL}`; `b_held = {t for t,v in broker.items() if v > BROKER_DRIFT_SHARE_TOL}`; `if j_held and not (j_held & b_held): return _res(STATE_IMPLAUSIBLE)`. New `STATE_NO_PRICES = "no_prices"` checked right after: `if j_held and not any(t in px for t in j_held)` (an empty price map must not read "checked, same"); `_WHY` "no prices for your holdings"; `broker_frame_view._REASON` "no prices for your holdings yet". `_WHY[implausible]` → "Robinhood's snapshot shows none of the positions your journal holds". Edge cases (all tested): journal empty → never implausible; dust ≤ tolerance → not held; broker ticker at 0 → not held; a single holding fully sold unlogged with broker empty → implausible (accepted detection loss, unchecked, disclosed); one shared ticker → ok. New kwarg `exclude_tickers=()`: a differing excluded ticker goes to a new `excluded` list, not to `adjust` / `rows`; `_result` gains `"excluded": []`.

### 2.6 Obligation 6 — `checked` only on a usable basis
`reconcile_budget_basis`: return unchecked when `state not in ("ok","called") or adj is None or gb is None or cash is None or basis is None` (basis also NaN/inf → unchecked); then `res.update(checked=True, gap=0.0, frame_adjust=adj)`. The initial update gains `"frame_adjust": None`.

### 2.7 Obligation 7 — manual cash note (D4)
`broker_frame_view` (non-gate): `HAND_CASH_NOTE = "Entered by hand"`; `cash_note_default(stored)` → "" when `stored.strip() == BROKER_CASH_NOTE`, else stored or ""; `cash_note_to_save(typed)` → None blank, `HAND_CASH_NOTE` when equal to `BROKER_CASH_NOTE`, else stripped. app.py:40481-40483 `value=_broker_frame_view.cash_note_default(_acct.get("note") if _acct else None)`; the saves at :40500, :40502 (and via the pending tuple at :40514) use `_broker_frame_view.cash_note_to_save(_new_note)`.

### 2.8 Obligation 8
`budget_view.over_line_text` docstring → "'Logged buys alone are X% of net capital — above the daily budget' when logged buys alone exceed it; else None."

### 2.9 Other C3 display
`broker_frame_view` wording (C2's "shadow" becomes false): prefix `"🧪 Net capital check: "`; Robinhood lower `" · gap $X (Robinhood lower — new-position sizes are capped at Robinhood's figure)"`; higher `" · gap $X (Robinhood higher — sizing keeps the journal figure)"`; not compared `"🧪 Net capital check: Robinhood positions not compared — {reason} (sizing uses the journal figure)"`; excluded rows: Price "—", value "split since the sync — left out"; module docstring updated. Drift clause: `broker_frame_view.drift_sizing_clause(frame)` → "New-position sizes are capped at Robinhood's lower figure until this is fixed." when state ok and `adjust >= _SAME_BELOW_USD`, else None; `_render_broker_drift(placeholder, verdict, sizing_clause=None)` appends `" " + clause` after "(full reconciliation)." in the drift branch only.

## §3 Fold C4 — YES (D2)
Without C4 the 09:45 email locks sizes from the looser journal basis while the app shows "Reserved" at a smaller F-255 count, and the cron–app parity test fails by construction.

headless_alert_engine.py — compute_morning_picks: hoist `_price_map` and `_bsnap = db.load_broker_position_snapshot()` (each own try → {} / None) above :661; F-255: `_f255_acct = db.load_account_cash(); _j_net, _j_state = resolve_net_capital(...)` (except → None, "unknown"); `_f255_eff = _effective_capital.resolve(portfolio_value, _f255_acct, _j_net, _j_state, _bsnap, ctx.get("holdings_df"), _price_map, _now, trades_df=ctx.get("trades_df"))`; `_f255_net_cap, _f255_basis = _f255_eff["net"], _f255_eff["state"]`; the drift block reuses `_bsnap` / `_price_map`; `budget_inputs = _f255_eff["budget_inputs"]` (delete the dict :817-824); payload gains `"net_capital_check": _effective_capital.log_line(_f255_eff)`. compute_watchlist_entries :1339-1346: same pattern (own snapshot read; the function's built ctx for holdings/trades); `_e["net_capital"]` / `["capital_basis"]` take the reconciled values. `_run_daily_budget`: `basis = _effective_capital.reconciled_basis(bi, trades_df, today, now)` inside the existing try; budget dict gets `**_effective_capital.broker_fields(basis)`; crash dict gets `"basis_source": None, "broker_gap": None, "broker_rec": None`.

cron_runner.py: after :2005 and :2430 `_log(payload.get("net_capital_check") or "net capital source=n/a frame=not computed")`; append `_effective_capital.budget_log_suffix(_b)` / `(_id_b)` to the existing budget log lines at :2099 / :2491. notify.py: per §2.3.

## §4 Versioning and caches
`daily_budget.BUDGET_SIZING_VERSION` 5 → 6 and `daily_briefing.SIZING_FORMULA_VERSION` 5 → 6, comment row "6 = U7 C3: F-255 net capital and the F-306 budget basis are the LOWER of the journal figure and Robinhood's positions snapshot (broker_frame); rec_shares and budget_allocations.basis_dollars can be lower than the journal alone gives." Stamps: `daily_budget.apply_to_sizing` (:395/:402/:407), `daily_briefing` (:222-314) into the sizing dict; writers copy it to `rec_sizing_version` (app.py:7310, cron_runner.py:223); no reader branches on the value. Test pin tests/test_cron_daily_budget.py:549 `== 5` → `== 6`. `_SYNTH_SCHEMA_VER` 21 → 22 (reason "memo signature carries the cash and positions-snapshot stamps (U7 C3)"); append `str(_f255_nc["cash_as_of"]), str(_f255_nc["captured_at"]),` inside the `_synth_sig` tuple, keeping its closing `\n    )\n` shape (tests/test_held_stop_switch.py:120 parses it); pins 21 → 22 at test_chunk7_render.py:53, test_held_news_rescore.py:278, test_held_stop_switch.py:122. `_new_position_budget_cache` (postpass :208-237) and the `plan_downstream` summary (:831-842) gain `basis_source`, `broker_gap`, `broker_rec`; no new key, no `coord_freshness` change (same producer / tier / None semantics); docs/coordination-caches.md notes the three fields and that the cache depends on the cash + snapshot stamps (a sync doesn't bump the epoch, as today).

## §5 Tests the build must include
**Pure** (tests/test_broker_frame.py updates; new tests/test_effective_capital.py):
- **T1** obligation-1 integration through real `budget_basis` via `resolve` → `budget_view.postpass` AND `run_daily_budget`: holdings AAA 20 / BBB 80 @ $100 (G = 10,000), cash −4,000 BROKER note updated 2026-11-16T16:15Z, snapshot {AAA:10, BBB:80} @ 16:14Z, BUY AAA 10 @100 at 17:00Z kind fill → F-255 net 5,000; budget basis 5,000 (NOT 4,000); source journal; gap 0; no suffix.
- **T2** log-time SELL: holdings AAA 10 / BBB 70 (G = 8,000), cash −3,000, snapshot {AAA:10, BBB:70}, SELL BBB 10 at 17:00Z kind fill → F-255 5,000 unchanged; raw basis 6,000 → 5,000, source broker, gap 1,000, S2 suffix. Same sell with kind "recorded" same ET day → raw 5,000, no bind, `lag_uncertain` 1,000, no suffix.
- **T3** log-time BUY: holdings AAA 20 / BBB 80, cash −5,000, snapshot equal, BUY at 17:00Z fill → basis 4,000 journal (fail-safe); F-255 5,000. Unknown-timed BUY → 4,000 journal, `lag_uncertain` 1,000.
- **T4** time-context: Monday 2026-11-16 09:45 ET with a Sunday 11-15 16:15 ET pair + a Friday 15:30 unlogged BBB sell 10 + a Monday 09:35 ET logged BUY AAA 10 (fill) → F-255 5,000, budget 5,000, source broker, gap 1,000 (the sell only, not the buy), S1. Holiday 2026-11-26 pair with a Wednesday 15:30 unlogged sell → binds on 11-26 and on Friday 11-27 09:45. An unlogged 16:30 ET sell after the 16:15 sync → no bind at 16:45; binds after the next day's 11:15 pair; the logged 16:30 version (fill) → no bind before or after. Day-late log both directions (BUY / SELL with a real fill time before the sync, logged next day) → no bind.
- Implausible / no_prices / split edge cases per §2.5 (split excluded on AFTER and UNKNOWN, not BEFORE; trades None → no exclusion).
- Obligation 6: ok frame with basis None/NaN → checked False, gap None; "called" with a numeric basis → checked True. Extra key set becomes {source, gap, checked, journal_net, frame_state, frame_adjust} (update test :361).
- Disclosure: S1 vs S2 boundary at `adjust = gap − 1.0` / `gap − 1.01`; exactly 1.0% shows / 0.99% hides, at `status_line_text` level too; masked → no digit after `~`; update the pinned strings (:458, :491).
- Tightening-only grid end to end: `resolve` net ≤ journal net, postpass basis ≤ journal basis, over shares × prices × states.
- `effective_capital` imports no margin / streamlit / db; add to `_GATE_MODULES`. `log_line` / `budget_log_suffix` formats incl. n/a.

**budget_view / cron:** cache + summary carry the 3 fields; books agree (ok frame, adjust 0) → basis / left / per_ticker byte-identical to frame=None (suffix differs: "" vs the unchecked suffix); update existing status-line expectations that now get the unchecked suffix (prefer giving those fixtures an ok frame); tests/test_cron_daily_budget.py:674 `budget_inputs` equality now includes `broker_frame` (assert the 5 keys + `["broker_frame"]["state"] == "no_snapshot"`); cron–app parity: same `resolve` output, trades and allocs through `postpass` and `run_daily_budget` (stub loads + save) → equal `basis_dollars` / cache basis, `basis_source`, `broker_gap`, `left`, per-ticker shares, with a binding frame; email `_budget_banner` shows S1 on a binding budget, escaped.

**AST pins (new tests/test_u7_c3_wiring.py):** app.py `resolve_net_capital(` exactly 3× (inside `def _effective_net_capital`, the `_bl_net_cap` and `_mg_net_cap` assignments); no `budget_inputs(` in app.py; `_cached_broker_snapshot` module-level, never nested, called by Home's drift block and the helper; `_effective_capital.resolve(` in the helper; `_publish_new_position_budget(raw_brief, ncap, price_map=None)` and `_budget_downstream_plan(ncap, asks, …)` read `ncap["budget_inputs"]`; all 6 sites call `_effective_net_capital(`; `_render_broker_drift(` gets `sizing_clause=`; `_synth_sig` carries both stamps; the Account form uses `cash_note_default` / `cash_note_to_save` at both saves; budget_view: `effective_capital.reconciled_basis(` ×3 and `daily_budget.budget_basis(` ×0; headless: `_effective_capital.resolve(` inside compute_morning_picks and compute_watchlist_entries, `reconciled_basis` inside `_run_daily_budget`, no `"net_capital":  _f255_net_cap` dict literal; `resolve`'s `budget_inputs(` call has args[1:3] = `journal_net`, `journal_state`; cron_runner logs `net_capital_check` at both lanes; version pins == 6, schema == 22.

## §6 Chunking — one push, two commits; implementer builds, lead verifies, Opus reviewer on both
- **Commit 1** `feat(capital): size from the lower of journal and robinhood net capital` (pure + cron = C3 core + C4, decision-bearing): stock_analyzer/effective_capital.py (NEW, gate), broker_frame.py (gate), budget_view.py (gate), daily_budget.py (gate), daily_briefing.py (gate), headless_alert_engine.py (gate), cron_runner.py (gate), notify.py, .claude/hooks/pre_tool_checks.py (`_GATE_FILES` += effective_capital → 92), docs/review-policy.md reason line, tests.
- **Commit 2** `feat(sizing): wire the robinhood net capital check into every page`: app.py (review-required) — loader, helper, 6 sites, the Home move above `_render_broker_drift`, memo signature/schema, drift clause, Account shadow via `_effective_net_capital(_equity, port_df=_acc_pdf, acct_rec=_acct)["frame"]`, cash note, User Guide, drop the now-unused `_broker_frame` / `BROKER_FRAME_PAIR_MAX_MINUTES` imports; broker_frame_view.py (non-gate); wiring + wording tests; schema-pin updates.
- Push both together. Trailers: `Design = planner (claude-opus-5-5): PROCEED WITH CHANGES`, `Build = implementer (...)`, `Review = ...`.
- **Docs (lead, Opus; policy text):** requirements F-255 / F-306 / G-22 / G-27 (definition becomes "the lower of the journal figure and Robinhood's positions"; replace C2's "shadow" sentence), User Guide (F-255, F-306, Account check), architecture.md (`effective_capital` module, `rec_sizing_version` row 6, fix its stale "currently 2" at :1250), coordination-caches.md, bug-register (U7 fixed; add U7b = unlogged buy not counted as used; aux-account masking; awareness siblings + the `gate_basis` concentration sibling, named per "fix the class"), plan Status, daily-new-position-budget.md :148 accepted limit closed (while the frame is paired), shipped-log, CLAUDE.md queue item 4 + `_GATE_FILES` count 92.
- **Live checks:** Home Grow Today / Watchlist / Analysis status lines unchanged on a clean day (no suffix); 💰 Account "🧪 Net capital check: … · same" (no "shadow"); cash note pre-fills blank; Trade Journal buy preview unchanged; after the next real logged BUY before a sync: no suffix, slightly smaller F-255 sizes; the next 09:45 email no suffix and scan log "net capital source=journal gap=0.00 frame=ok state=…" + "basis_source=journal broker_gap=0.00"; no literal `**`; no `$` as LaTeX.

## Risks / coordination
- One owner per decision: F-255 and F-306 stay the owners; nothing new decides; Home's drift banner stays awareness, gaining a clause driven by the same frame. No new session key.
- Skipped shadow week: the only reading is a weekend with no trades; the first real exercise is Monday. Bounded by tightening-only + the disclosure suffix + the Account detail rows.
- Cached-snapshot window: up to 120 s after a sync the cash is fresh while the cached snapshot is old → "unpaired" → journal figure + "not cross-checked" suffix (safe). A cached None also lasts ≤ 120 s.
- Accepted, disclosed limits: the snapshot covers all linked accounts while cash is the selected account only (an auxiliary holding can mask an unlogged sell — detection loss, never looser); U7b; a failed trades read disables the split guard (over-tight, not loose).
- The concentration-gate denominator sibling (`portfolio.gate_basis`) stays journal-only (disclosed by the drift banner); name it, don't fix it here.
- Offline contract: every failure → frame None or a non-"ok" state → journal figure + visible suffix; the budget's None cache unchanged; `resolve` never raises.

Invariants needing boundary tests: never above the journal figure (grid); post-sync logged BUY never subtracted twice (T1); never "called" from an empty / disjoint / no-price snapshot; suffix at exactly 1.0%; app == cron (parity).
