# RecTime: remember whether a fill time was typed

**Status:** DESIGNED 2026-11-13 (Opus planner PROCEED WITH CHANGES; owner answered Q1, Q2, Q4, Q6 the same evening). C1 BUILT 2026-11-13 (implementer), in Opus review. Bug-register row: RecTime (section A). Today's two rows were repaired by hand: `docs/sql/journal-fix-2026-11-13-fill-times.sql` (Opus SHIP after one FIX-FIRST round).

## The defect

When the 📒 Trade Journal fill time is left blank on a market-open day, `trade_entry_checks.resolve_traded_at` stores `now_et`, the moment Record was pressed (C2a, F-81f, by design). `trade_time.normalize_traded_at` then marks every hand log `traded_at_time_known = True`, so no reader can tell a typed fill time from a record time. Live 2026-11-13: #494 BUY 5 MRVL filled 10:21 ET, saved 14:30:50 ET; the 11:19:14 ET broker sync already included it; `capital_vs_margin.adjust_anchor_for_late_trades` took its $750 off the anchor a second time and 💳 Capital vs Margin's cash self-check failed. `daily_budget.trades_after` → `budget_basis` took the same $750 off the F-306 budget basis a second time.

**Second sibling class (planner, verified at HEAD):** new import and broker-prefill rows are written at 16:00 ET by `et_anchor_iso`, so `normalize_traded_at` never re-anchors them and they come back `traded_at_time_known = True`. A same-day row logged from Pending Imports sits at 16:00, after an 11:19 sync, and every sync comparison reads it as "after the sync": the same double count. The `capital_vs_margin.py` ~294–312 docstring saying broker rows are `False` is stale since `fef5c59` (2026-08-23). Closed by `kind = "placeholder"` below.

## Key idea

A blank-time row is not fully unknown: the blank default is offered only while the market is open, and the fill happened at or before Record was pressed, so the stored record time is an UPPER BOUND on the fill time. "Logged at or before the sync" means certainly before it; only "logged after the sync" is unknown. Each consumer resolves "unknown" in its own protective direction.

## Owner decisions (2026-11-13)

- **Q1 Capital vs Margin:** (a) don't adjust the anchor for an unknown row; show an ℹ️ note naming it and its $ amount. Accepted cost: a promptly logged blank-time afternoon trade after the ~11:15 sync can make the self-check fail (fail-closed, visible) until the next sync.
- **Q2 Budget:** lower basis (unknown BUYs subtracted, unknown SELLs not added; tightening-only) plus a "basis may be up to $X low" caption on the budget surfaces.
- **Q4:** extend U1's "same ticker, same ET date ⇒ fill time required" to today's market-open blank default (at HEAD the `d == today` branch returns default-now before `same_ticker_same_day` is consulted, `trade_entry_checks.py` ~323–328).
- **Q6:** new column `trades.fill_time_typed` plus the microsecond fallback for legacy rows.
- **Lead defaults, owner may overturn:** Q3 accept the "Logging as … (no fill time: the time you pressed Record)" line and the stronger caption; no "(logged)" mark in Trade History / the duplicate card for now. Q5 the drift share-count check applies to unknown rows only.

## Spec

### S1. Storage (C1)
- `trades.fill_time_typed boolean`, nullable, no default. `True` = typed by the owner (or set by an owner SQL repair with a real fill time); `False` = not typed (blank → record time or 16:00 anchor; any import; broker prefill; SPLIT row); `NULL` = not recorded (legacy, or saved before the ALTER).
- DDL: `docs/sql/trades-fill-time-typed.sql` (model: `docs/sql/snaptrade-config-txn-sync.sql`): `alter table public.trades add column if not exists fill_time_typed boolean;` plus a read-only verify.
- `db.py`: add to `_TRADE_COLS`, to the `load_trades_or_none` None-backfill loop, and to `save_trade`'s `_optional` tuple (reads NULL → fallback when the column is absent). Architecture §db column list. **Ordering rule: the owner applies the ALTER and runs its verify query BEFORE C2 ships.** `save_trade`'s retry is drop-ALL, not drop-the-named-column: a save of a record carrying `fill_time_typed` against a table without it would be saved but silently lose `broker_txn_id`, `idempotency_key`, `asset_type`, `situational_category` and the premortem fields. **Separate follow-up (latent class, own reviewed change, not part of this plan):** make the retry drop only the column(s) the error names, retrying up to `len(_optional)` times, and keep drop-all only for an unnamed PGRST204. C1 pins today's drop-all in `tests/test_db_save_trade.py`.
- Do NOT change `traded_at_time_known` (its meaning and the asymmetry note in `trade_time.py` stay; it also drives `et_display` and `trade_confirm._when`).

### S2. Classification (C1), pure, in `trade_time.normalize_traded_at`
Derived column `traded_at_time_kind` ∈ {`"fill"`, `"recorded"`, `"placeholder"`}, vectorized. First match wins:
1. `"placeholder"`: re-anchored here (`_touch`), OR the ET wall clock is 16:00:00.000000 and `fill_time_typed is not True` (a typed 16:00 is a fill).
2. `fill_time_typed is True` → `"fill"`.
3. `fill_time_typed is False` → `"recorded"`.
4. NULL (legacy) fallback, value-only: microsecond ≠ 0 → `"recorded"`; microsecond == 0 → `"fill"` (status quo).

The no-provenance fail-closed path emits the kind too; an unparseable time → `"placeholder"`. The value-only fallback rewrites nothing (unlike the date rule `trade_time.py` rejects); fingerprint: `st.time_input` (step 60) and SQL repairs give microsecond 0, `now_et().isoformat()` / DB `now()` carry microseconds. Linking never upgrades a time (SnapTrade gives a date only; `backfill_trade_broker_txn_id` writes only `broker_txn_id`). A classification failure must stay isolated (emit no kind column → consumers read `"fill"`, status quo, never a crash).

### S3. One comparison helper (C1): new `stock_analyzer/sync_timing.py` (pure; add to `_GATE_FILES` with its reason in `docs/review-policy.md` in the same commit)
`after_sync(traded_at, kind, sync_at) -> True | False | None` (None = can't tell); UTC instants, ET dates via `trade_time.et_trade_date`.
- `"fill"`: `ts > sync_at` strict (a tie = already reflected, the existing convention).
- `"recorded"`: `ts <= sync_at` → False; else ET date > the sync's ET date → True; else None.
- `"placeholder"`: ET date > sync date → True; earlier → False; same → None.
- Missing kind column → `"fill"`.

`classify_vs_sync(trades_df, sync_at)` → per-row after / before / unknown plus per-ticker signed net shares and cash deltas (via `daily_pnl.today_trade_cash_delta`) for the after and unknown sets; None when the frame is unreadable.

### S4. Writers (C2)
| Writer | Stores |
|---|---|
| Trade Journal manual BUY / SELL (incl. a C2b-linked manual entry) | `fill_time_typed = (_tj_ft is not None)` in the staged record |
| Broker prefill (Pending Imports "Log This Trade") | `False` |
| CSV import | `False` |
| RH text / screenshot import | `False` |
| SPLIT synthetic row | `False` |
| Edits | n/a (`traded_at` is not editable) |
| Owner SQL repairs that set a fill time | future templates set `true` |

Pin with a wiring test that every `db.save_trade(` record in app.py carries `fill_time_typed` (pin both ends), plus a `save_trade` test that a DB error naming the column retries without it.

### S5. Consumers (C3, C4)
| Consumer | New rule for an unknown row | Direction |
|---|---|---|
| Capital vs Margin late-trade anchor (`capital_vs_margin.py` ~372–377) | adjust only for `after is True`; return additive `late_trade_unknown_count` / `_tickers` / `_delta`; render the ℹ️ note when > 0; fix the stale docstring | fails closed at `validate_reconstruction` |
| 💰 Account cash caption (app.py ~40100–40145) | decision moves to `sync_timing`; certain set keeps today's sentence; unknown set named apart, never summed into "roughly $N" | one freshness per number |
| 🏠 Home drift excuse + cron email (`broker_sync.tickers_traded_since`, `split_awaiting_sync`, `decide_drift_banner`; app.py ~6115, `headless_alert_engine.py` ~776) | R-D: excused if any certain-after row (status quo, incl. SPLIT), or only unknown rows AND the drift equals their net signed shares within `BROKER_DRIFT_SHARE_TOL`; additive `awaiting_untimed` ⊆ `awaiting_sync`; unreadable → `[]` | protective: an unknown row never excuses a gap it doesn't exactly explain |
| F-306 budget basis (`daily_budget.trades_after` → `budget_basis`; `budget_view.py`, `headless_alert_engine.py` ~1485) | unknown BUYs subtracted, unknown SELLs not added; additive `lag_uncertain`; caption when > 0 | tightening-only |
| `sell_replay_finding` text (`trade_entry_checks.py` ~650) | wording: a blank-time row logged the same day with the market open sits at its record time | wording |
| `logging_as_text` / `fill_time_caption` | "(no fill time: the time you pressed Record)"; "Leave blank only if you're logging right after the fill; otherwise type Robinhood's fill time." | wording |
| `fill_time_requirement` | Q4: same ticker already logged today ⇒ required | stricter |
| replay order, matcher, Pending verdicts, `fill_late_logged_baseline`, OUTSIDE_RTH | no change | |

## Chunks
1. **C1** DDL file; `db.py` column plumbing; `traded_at_time_kind` in `normalize_traded_at`; new `sync_timing.py`; `_GATE_FILES` + review-policy reason; architecture §db + Known behaviours; tests. Nothing wired. → reviewer.
2. **C2** writers set `fill_time_typed`. **Ships only after the owner has applied `docs/sql/trades-fill-time-typed.sql` and run its verify query** (the save retry is drop-all, see S1). → reviewer (DB write).
3. **C3** Capital vs Margin rule + note; Account caption via `sync_timing`; budget lower bound + `lag_uncertain` + caption. → reviewer.
4. **C4** drift R-D (`broker_sync`, `headless_alert_engine`, Home caption). → reviewer.
5. **C5** wording + Q4; docs (requirements F-81f / F-306 / Capital vs Margin F-IDs, User Guide, bug register, shipped-log, `trade-entry-guard.md` Status).

C3 and C4 are independent. **Notes from the C1 review:** (a) `sync_timing.classify_vs_sync` is a per-row Python loop (the reviewer measured ~0.29 s on 3,000 rows); vectorize it or pre-filter before wiring it into Home / the budget: e.g. a cheap "fill / recorded with ts <= sync -> before" pass, or restrict to rows from the sync's ET date onward. (b) C4: a same-day SPLIT row is a placeholder, so it lands in `unknown` (not `after`); the status-quo "a certain-after row, including a SPLIT, excuses the drift" rule must therefore look SPLIT rows up by index in the `after` AND `unknown` lists, or a split day raises a false drift alarm. (c) `classify_vs_sync` returns rows with an unparseable `traded_at` in a separate `unreadable` list, never in `unknown`; an unreadable row must never excuse a drift gap (as `broker_sync.tickers_traded_since` already does for NaT rows). No new constants (reuse `BROKER_DRIFT_SHARE_TOL`, `IMPORTED_TRADE_ANCHOR_ET_HOUR`); do not add a "logged within N minutes" window. Consider adding `capital_vs_margin.py` to `_GATE_FILES` (its self-check withholds verdicts; lead call at C3).

## Tests the build must include
Classification (typed / blank / 16:00 anchor with False or NULL / typed 16:00 / NULL with and without microseconds / legacy midnight import / no-provenance / mixed precision with zero NaT / empty frame); `after_sync` boundaries (tie, recorded at / after / later date, placeholder same / earlier / later, missing kind); time-context matrix (weekend visit after a Friday blank-time row, NYSE holiday, DST 2026-03-08 and 2026-11-01, after hours, a trade logged a day late with a blank time, a trade dated a day early or late, BUY and SELL); the #494 replay (typed SELL 09:56, recorded BUY 14:30, sync 11:19:14 → no adjustment, unknown count 1, delta −750; the same BUY typed → adjusted; a same-day 16:00 prefill row → not adjusted); budget (unknown BUY subtracted, unknown SELL not added, `lag_uncertain`, unreadable → "unknown"); drift R-D (unknown BUY 5 with drift +5 excused, +3 not, tolerance boundary, app_only / rh_only, certain-after and SPLIT status quo, unreadable → nothing excused, the headless path agrees); the Account caption decision; writers wiring; wording.
