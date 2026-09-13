# Score History Capture — persist the daily composite the app already computes and discards

**Status: DESIGNED 2026-09-12, NOT STARTED. No code written, no DDL applied.**
Capture-only by design: **no readout, no card, no gate** ships in this change — the readout
is a separate, separately-approved decision, exactly as the Gate Suppression Ledger (F-259)
was sequenced. Requires an Opus `planner` design pass and a **mandatory** Opus `reviewer`
before commit (touches `db.py` + `cron_runner.py`, both `_GATE_FILES` members).

---

## 1. Why this exists

A 2026-09-12 investor-POV analysis found the app is strong at measuring **the present** and
grading **its own past**, and structurally blind to **change**. Several daily-computed
assets are discarded or written and never read (`scanner_cache` overwritten every run;
`sentiment_history` has no loader at all; `load_daily_regime` has zero callers;
`fundamentals_cache` is one overwritten row per ticker).

**The most consequential instance is the app's own central number.** The composite is
computed for every held name every day, and persisted **only** when something has already
gone wrong:

- `recommendations` — only when a pick is surfaced.
- `exit_signals` — only for tickers that already tripped WATCH/TRIM/EXIT/RISK_OFF.

Verified: zero hits for `score_history` / `score_trend` / `composite_trend` anywhere in
`stock_analyzer/`. So **a healthy holding's decay is invisible until it crosses a
threshold**, and "has my score on CRM been sliding for six weeks?" cannot be asked.

**Why it matters on this book specifically.** `scripts/exit_ladder_replay.py` (the W6
measurement) found that of 54 closed losing round trips, **52% got no protective signal at
all**, and most were held 0–10 days. That is not a defect — the deterioration ladder is
built on *price*: drawdown from peak plus 2-of-3 sessions below SMA50, which structurally
needs weeks. A composite that has been decaying for three weeks is an earlier, quieter
signal. **That hypothesis cannot be tested today because no history exists to test it on.**

**Why it must be decided now rather than well.** Forward-only; **no backfill is possible**.
This is the same wall F-266 hit — the leverage backtest the owner wanted was impossible
because `account_cash` had only ever been a single overwritten row (memory
`project_leverage_history_capture`). Every week of delay is evidence permanently lost.

**The capture is nearly free.** `headless_alert_engine.py:257-261` already builds
`composite_map = port_df.set_index("Ticker")["Score"].to_dict()` for **every** held name and
uses it only to enrich deteriorating signals. `bundle_loader.load_bundle`
(`bundle_loader.py:213-232`) already returns `t_score` / `bq_score` / `val_score` /
`s_score` **plus** `bq_available` / `val_available`, and `ctx["held_data"]` is already in
scope. **Zero extra API calls, zero extra bundle loads.**

---

## 2. Scope

**In:** a new `score_history` table, a write from the `premarket` cron lane, and the two
`db.py` accessors. Nothing reads it.

**Out:** any readout, chart, banner, gate, alert, or constant. Watchlist names (see §4.4).

---

## 3. Table `score_history`

DDL applied by hand in Supabase by the owner (this repo has no migrations) and documented in
`docs/architecture.md` §6 alongside every other table. **The table ships inert** — the code
degrades to a logged write failure until the DDL exists, same as every prior table here.

Key: **unique `(ticker, score_date)`** — idempotent upsert, safe if an interactive session
ever also captures the same day.

| Column | Notes |
|---|---|
| `ticker`, `score_date` | composite key |
| `composite` | `port_df["Score"]` — the number the app acted on |
| `t_score`, `bq_score`, `val_score`, `s_score` | the four pillars |
| `bq_available`, `val_available` | **load-bearing, see §4.1 — not decoration** |
| `price` | contextualises a score move against a price move |
| `source` | `'cron'` / `'app'` |

RLS `FOR ALL TO service_role`, matching every existing table (Hard Rule #2).

---

## 4. The design decisions that matter

### 4.1 Store `NULL`, never the fabricated neutral 50
`valuation_score` returns a **fabricated neutral 50 with zero signal behind it** when no
metric had data — its own docstring says so (`valuation.py:26-31`), and that fabrication was
itself a 2026-08-04 audit finding. `business_quality_score` has the same shape.

⇒ **When `val_available` is False, persist `val_score = NULL`, not 50.** Same for
`bq_score`/`bq_available`. Persisting the 50 would put fabricated neutrals into the history
**indistinguishable from real readings**, and any future decay analysis would read them as
genuine mid-range scores. This is the `feedback_sentinel_is_present` class applied to a new
store, and it is the single easiest way to silently poison this table for good.

Store the availability flags *anyway*, even though a NULL implies them — an explicit flag
survives a future schema reader who doesn't know the convention.

### 4.2 NO coalesce-on-write — a deliberate departure from the `exit_signals` template
`save_exit_signals_batch` (`db.py:3011-3090`) does a pre-read merge so a same-day rebuild
can't clobber a previously-captured non-null with a NULL ("last-NON-NULL wins").

**That logic must NOT be copied here.** In `exit_signals` a NULL means "this build didn't
derive it." In `score_history` a NULL means **"not measurable this run"** — which is
information, and precisely the signal §4.1 exists to preserve. Coalescing would resurrect
an earlier run's `val_score` over a later run's honest NULL and manufacture a reading.

Last write wins, NULLs included. **State this reasoning in the function docstring**, or a
future editor "harmonising" it with its sibling will reintroduce the bug.

### 4.3 Skip stale-cache-served bundles — copy the precedent next door
`headless_alert_engine.py:268-274` already does exactly this for
`analyst_target_snapshots`, with the reason in a comment: *"Skips stale-cache-served bundles
so persisted history never mixes in a bundle_cache fallback value (would contaminate a
future day-over-day comparison — see the INTC staleness incident precedent)."*

```python
if bundle.get("stale_as_of") is not None:
    continue
```

**This is not optional here — it is the whole feature's integrity.** `bundle_cache` serves
up to `BUNDLE_CACHE_MAX_AGE_DAYS` (5) old, so without the skip a run of cache-served days
would persist an unchanged score as if freshly measured, and a decay analysis would read
**cache staleness as stability** — or worse, read the snap-back when the cache refreshes as
a sudden deterioration event that never happened.

A skipped ticker simply has no row that day. **Do not write a placeholder row** — a gap and
a measured value must stay distinguishable.

### 4.4 Held names only, v1
Watchlist composites are computed on a different path. Including them doubles the surface
for a question that can't be answered yet. Revisit only if the readout is ever built and
wants pre-purchase trend.

### 4.5 `premarket` lane, not `eod`
Premarket already computes the composites (it is where `composite_map` is built). EOD would
need a second bundle load. The write goes beside the existing `exit_signals` block
(`cron_runner.py:335-366`).

### 4.6 Open decision for the `planner` pass — do NOT decide this solo
Whether to also persist the **as-of recommendation label** (`port_df["Signal"]`, e.g.
"Buy"). For: it preserves what the app actually *said* that day even if `COMPOSITE_BUY` is
retuned later. Against: it is derivable from `composite` via the pure
`scoring.recommendation()`, and a stored copy can drift from it. Not decided here.

---

## 5. Write path

1. **`stock_analyzer/headless_alert_engine.py`** — `build_protective_payload` gains an
   additive `"score_history"` key on its return dict (`:325-335`), built from the existing
   `composite_map` + `ctx["held_data"]`, applying §4.1 and §4.3. Strictly additive: it must
   never influence `alerts`, exactly as `analyst_target_snapshots` doesn't.
2. **`cron_runner.py::_run_premarket`** — one new write beside the `exit_signals` block,
   copying that block's **explicit success/failure logging** (`cron_runner.py:363-366`).
   A silent "captured" log on a failed write is the exact
   log-collapses-failure-into-success shape called out in `save_exit_signals_batch`'s own
   docstring and in memory `project_data_integrity_audit_2026_08`.
3. **`stock_analyzer/db.py`** — `save_score_history_batch()` / `load_score_history()`,
   modelled on `save_exit_signals_batch` / `load_exit_signals` (`db.py:3011`, `:3093`) for:
   - the `is_readonly()` viewer guard **first** (three SnapTrade writers shipped without it
     and the reviewer caught it — memory `project_snaptrade_broker_integration`);
   - never raising (`warnings.warn`, return `False`);
   - **returning `True` iff the upsert actually executed** — not "iff we tried";
   - **but NOT the coalesce-on-write pre-read** (§4.2).
   - `load_score_history` takes an explicit `limit` well above any realistic row count —
     the default-`limit=100` truncation class has already bitten this repo twice
     (`db.load_analyst_coverage`, `db.load_model_predictions`).

---

## 6. Pre-registered retirement criterion — write this into the plan BEFORE any data exists

Adopting the Gate Suppression Ledger's discipline (`docs/plans/gate-suppression-ledger.md`
§5), which pre-registered its own retirement before a single row landed:

> **After ~6 months, replay score-decay against the same closed round trips
> `scripts/exit_ladder_replay.py` already uses. If score decay is not systematically
> earlier than the existing deterioration ladder, retire the table and the idea.**

Retiring is the **success condition** of that criterion, not a failure. Do not soften it
later to save the feature.

Two honesty notes to carry into that replay:
- The sample will be small and **survivorship-shaped** — it can only see closed positions.
  State N and the selection effect on the output, per the W6 script's own caveat.
- Expect **"insufficient data" for months**. That is the designed primary state, same as
  the Gate Ledger's ~2 months of "building" — not a bug to chase.

---

## 7. Redlines

- **Capture only.** No gate, no recommendation, no banner, no `session_state` key that any
  gate consumes, no composite change. Nothing reads this table in this change.
- **No new constant.** If one appears it is a policy value the owner sets (Hard Rule #1).
- Must not alter `alerts`, the Brief, or any existing write. A capture failure must never
  break the premarket lane — same contract as `exit_signals`.

---

## 8. Review path

`stock_analyzer/db.py` and `cron_runner.py` are both **`_GATE_FILES` members**
(`.claude/hooks/pre_tool_checks.py:280-310`), so the commit hook will **block** without an
Opus review citation. Beyond the mechanical requirement, this is a new persisted store on
the data-integrity path and warrants it on merit.

**Required: `planner` (Opus) design pass → build → `reviewer` (Opus) → cite in the commit
body** per Hard Rule #4. `headless_alert_engine.py` is not itself a gate file, but it ships
in the same commit.

---

## 9. Verification

- Full `pytest`; `scripts/check_antipatterns.py`; `scripts/check_constants_documented.py`
  (no-op — no new constant).
- **Verify the gates actually ran.** Memory `feedback_hook_enforcement`: the `PreToolUse`
  hook was observed **not firing at all** in a VSCode-extension session on 2026-09-09.
- **Apply the DDL before the first cron run**, or the lane logs a write failure nightly.
- Never run locally (Hard Rule #3). Push to `main`, ~2 min for Railway.
- **This feature has no visible surface** — confirm it by querying `score_history` directly
  after the first premarket run, the same way F-249 Phase 2 and F-252 were validated. Check:
  one row per held ticker; `val_score IS NULL` exactly where `val_available` is false; no
  row at all for any ticker whose bundle was stale-cache-served.
- Re-run the lane the same day and confirm the upsert is a no-op (idempotency), and that a
  later NULL is **not** back-filled from the earlier run (§4.2).

### Tests
- `save_score_history_batch`: readonly → `False`; empty → `False`; no-db → `False`;
  upsert raising → `False` (**not** `True`).
- A row with `val_available=False` carries `val_score is None`, **never 50**.
- A stale-cache-served bundle produces **no row**, not a placeholder.
- A same-day re-run with a NULL does not inherit the earlier non-null (the direct
  regression against copying `exit_signals`' coalesce).

### Docs to sync in the SAME session (Definition of Done)
`docs/architecture.md` §6 (new table DDL + the `headless_alert_engine`/`cron_runner` module
notes) · `docs/requirements.md` (new F-ID — this is a new capability, unlike the analyst
audit which extends F-154c) · `docs/shipped-log.md` · **this file's own `**Status:**` line**
(step 7) · a memory file recording the forward-only start date and the retirement criterion.

---

## 10. Related

- `docs/plans/gate-suppression-ledger.md` §5 — the capture-first + pre-registered-retirement
  pattern this copies.
- `scripts/exit_ladder_replay.py` — the replay harness §6's criterion reuses.
- Memory `project_leverage_history_capture` — the F-266 precedent for "the backtest was
  impossible because nothing was ever recorded."
- Memory `project_forward_portfolio_simulator` — the W6 result (52% of losing round trips
  got no protective signal) that motivates the decay hypothesis.
- Memory `project_data_integrity_audit_2026_08` — the write-success/failure logging rule.
- `docs/plans/analyst-weight-audit.md` — the sibling plan from the same analysis; its §7
  notes that this table's `bq_available`/`val_available` columns would also permanently
  answer how often the valuation pillar scores on a reduced denominator.
