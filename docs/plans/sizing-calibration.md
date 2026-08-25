# Sizing Calibration — Design Plan

**Date:** 2026-08-22
**Author:** Ajay Kumar
**Analysis model:** Claude Opus 5 (`planner` design pass + lead verification)
**Status:** **PHASE 1 SHIPPED 2026-08-23 (F-249).** **PHASE 2 CODE SHIPPED 2026-08-23 —
INERT until the DDL below is applied by hand in the Supabase dashboard.** User decisions 1, 2,
7 settled 2026-08-22, plus an 8th taken mid-build (ceiling-infeasible ⇒ fail closed) and a 9th
during Phase 2 (the strip cascade must be error-targeted, not positional). Phase 3 remains
gated — do not start; it needs decision (5) and the two measurements named there.

> **Design verdict: RECONSIDER** (planner, Opus 5). The feature as originally asked
> — "what did I leave on the table by buying 5 instead of the recommended 26?" —
> measures against a benchmark that is currently wrong in two independent ways. The
> dollar-regret number is **not built in any phase**. What ships instead is a
> coherence fix to the sizing engine (Phase 1), forward-only capture (Phase 2), and a
> take-rate calibration readout (Phase 3, gated).

> **One-line spec:** Phase 1 unifies two disagreeing sizing engines onto the
> ceiling-capped `risk.position_sizing()` and corrects a mislabelled capital
> directive; Phase 2 persists the recommended size going forward; Phase 3 renders a
> **take rate** ("what fraction of the suggested size do you actually take, and what
> risk-per-trade does that reveal") on 🎯 My Edge → 🧭 Self vs Engine.

---

## Origin

User question, 2026-08-22: *"ALB currently shows up under new buy with a
recommendation of 26 shares. However, I mostly buy 5 or 10. I'd like to understand
what amount I left on the table, or the projected amount lost? I don't think we
currently have this data."*

Correct — the data does not exist. The recommended share count is computed at render
time and discarded; `recommendations` has no `shares`, no `stop`, no
`portfolio_value` column. But the HEAD audit undertaken to scope the capture found
that the benchmark itself is broken, which changes the feature.

---

## HEAD audit — five findings

### 1. There are two sizing engines, and the one on the buy surface is uncapped

| | Analysis / Watchlist | **Grow Today (new buys)** |
|---|---|---|
| Function | `risk.position_sizing()` (`risk.py:33-72`) | `daily_briefing._suggest_size()` (`daily_briefing.py:476-498`) |
| Stop basis | ATR: `price − ATR_STOP_MULT × ATR(14)` | Hardcoded trend bucket: `price × (1 − 0.05\|0.07\|0.08)` |
| Concentration cap | `SINGLE_NAME_CEILING` at all 4 call sites | **None** |
| Call sites | `app.py:19268, 19416, 21451, 21834` | `daily_briefing.py:822` (new_pick), `:1175` (add_winner) |

`_suggest_size`'s suggested position size, as a fraction of the book, reduces to a
**price-independent constant**:

```
port_pct = total_cost / PV = RISK_PCT_PER_TRADE / stop_pct
```

| Trend bucket | `stop_pct` | Suggested % of book | vs `SINGLE_NAME_CEILING = 15.0` |
|---|---|---|---|
| Strong | 5% | **30.0%** | 2.00× |
| Uptrend | 7% | **21.4%** | 1.43× |
| Other | 8% | **18.75%** | 1.25× |

Every new-pick card on the primary buy surface suggests **1.25×–2× the app's own hard
single-name cap**, and prints the breach in its own caption (`app.py:7310` renders
`({port_pct}% of portfolio)`). The same module imports `SINGLE_NAME_CEILING` at
`daily_briefing.py:33` and enforces it at `:1143` and `:1840` — just not in the sizing
function. `_suggest_size` has **zero direct test coverage**.

Meanwhile `trade_review.py:478` counts the user's trades that breached
`SINGLE_NAME_CEILING` and reports them back as a discipline problem. **The app scolds
the user for a breach its own buy card recommends.**

### 2. The add-to-winner path hardcodes the worst bucket

`daily_briefing.py:1175`: `_suggest_size(price, "Strong Uptrend", portfolio_value)` —
the trend is a **literal**, not read from the row. So every add-to-winner suggestion
takes the 5% stop → **30% of book**, on positions that already carry weight. This is
strictly worse than the new-pick path, which at least reads a real trend.

### 3. `deploy_note` is a third, contradictory figure — on the same screen

`daily_briefing.py:1211-1227`:

```python
deploy = portfolio_value * RISK_PCT_PER_TRADE * n_trades
...
f"consider deploying ~${deploy:,.0f} today."
```

`deploy` is the **risk budget** (max loss if every stop hits), but the verb is
"deploying" — which reads as capital to commit. Worked at `portfolio_value = $50,000`,
3 setups, Strong trend:

| Figure | Source | Says |
|---|---|---|
| `deploy_note` | `daily_briefing.py:1226` | "consider deploying ~**$2,250** today" |
| The 3 pick cards, summed | `app.py:7310` | ~**$45,000** |
| What `position_sizing` would allow | `risk.py:51` | ≤ **$22,500** |

**Three mutually inconsistent answers to "how much should I buy", a 20× spread, two of
them on the same screen.** Note the two branches differ: the risk-flagged branch
(`:1220`) says *"1.5% risk per trade across N setups = ~$X"*, which is attached to
"risk per trade" and defensible; the normal branch (`:1224`) says *"consider deploying
~$X"*, which is not.

**Consequence for the original question:** the two defects cut in opposite directions
— the ceiling breach inflates the per-card number, `deploy_note` deflates the
headline. A user anchoring on "deploy ~$2,250" and buying 5–10 shares may be behaving
*consistently with one of the app's own figures*. The premise "I'm under-sizing"
cannot be evaluated until the app stops giving three answers. **This is the primary
reason not to build the regret number.**

### 4. Retroactive reconstruction is ruled out

Not because ATR is unrecoverable (ATR(14) at a past date genuinely is computable from
historical OHLC). The blockers:

- **The trend bucket at rec time is not recorded.** `recommendations` has no trend or
  signal column. The three buckets span a 1.6× size range. Unrecoverable.
- **Portfolio value at rec date is not recorded.** No such column;
  `decision_context.portfolio.value` exists only on *trades*, and only interactive
  ones (broker/CSV/split imports skip it by design — `decision_context.py:20-22`).
- **Decisive:** Phase 1 changes the sizing formula. Reconstructing pre-Phase-1
  recommendations would compute them under a policy that no longer exists, then plot
  them on the same axis as post-Phase-1 values. A hybrid is not a compromise — it is a
  time series that silently splices two investment policies.

**Forward-only, with a hard cutoff date.** Same precedent as Behavioral Fingerprint
(memory `project_behavioral_fingerprint_audit`, shipped forward-only at 12%
completeness) and `decision_context.py`'s own docstring: *"capture must start now."*

### 5. `match_recs_to_trades` is the wrong join — a better one already exists

`recommendations_history.match_recs_to_trades()` (`:75`) requires **same-day** AND
`trigger_type == 'RECOMMENDATION'`. But `self_track_record.py`'s own module docstring
(`:33-38`) states `trigger_type` "has ZERO influence" on its classification because
"a user can mis-tag trigger_type, or the field can be absent on older/imported rows."

`self_track_record.classify_buys()` (`:63`) already does a **lookback-window** join
(`SELF_TRACK_MATCH_LOOKBACK_DAYS = 3`, `constants.py:1159`) and emits an `app_aligned`
bucket — precisely the population Phase 3 needs, already built, already offline-guarded
(returns `None` when `recs_df is None`, `:87-88`). **Reuse it. Do not add a third join.**

---

## What already exists (reused, not rebuilt)

| Piece | Where | Status |
|---|---|---|
| Ceiling-capped sizing + `ceiling_capped`/`uncapped_shares` vocabulary | `risk.position_sizing()` (`risk.py:33-72`) | Shipped and correct. Phase 1 adopts it wholesale; no new sizing logic is written. |
| **ATR stop for every scanner candidate** | `bundle_loader.py:188` — `stop, atr_val = atr_stop_loss(df, ATR_STOP_MULT)` | Shipped. Returned as `["stop"]`/`["atr"]` on every bundle. **Already in scope** at the new-pick call site via `composites[ticker]`. Zero new I/O. |
| ATR-derived entry zone | `bundle_loader.py:189` — `entry_zone(price, atr_val)` → `["entry_lo"]`/`["entry_hi"]` | Shipped. Strictly better than `_suggest_size`'s `stop_pct × 0.40` heuristic (`daily_briefing.py:487-488`). |
| ATR for held positions | `held_data[ticker]["atr"]`/`["stop"]` | Shipped; the same bundle shape. Precedent read already in this file at `daily_briefing.py:1990`. Covers the add-winner path. |
| Lookback rec↔trade join + `app_aligned` bucket | `self_track_record.classify_buys()` (`:63`) | Shipped. Phase 3 adds no matching logic. |
| Additive-column drop-and-retry | `db.save_recommendations()` `_QA_PILLAR_COLS`/`_F179_COLS` (`db.py:2296-2306`) | Shipped. Phase 2 adds a **third generation set** per that block's documented rationale. |
| Render host | 🎯 My Edge → 🧭 Self vs Engine (`app.py:36092+`) | Shipped. Already loads `recs_df`, trades, SPY. Phase 3 adds a section; no new data load. |

### Composite coverage is guaranteed, so there is no fallback case

The candidate loop (`daily_briefing.py:738`) runs over `curated_rows + mover_rows`,
truncated to `max_picks × GROW_CANDIDATE_OVERFETCH` ≤ `GROW_CANDIDATE_POOL` (= 12,
`constants.py:209`) — the same pool the bundle prefetch populates (`app.py:5002-5025`;
cron: `headless_alert_engine.py:385-393`). The composite gate at `daily_briefing.py:911`
hard-stops anything without a bundle:

```python
if _composite_score is None:
    composite_unavailable.append({...}); continue
```

**Every row reaching the `pick` dict at `:944` provably has a non-empty `_comp_data`.**
No "surviving pick with no bundle" case exists, so Phase 1 needs no stop fallback —
and must not invent one, since a fabricated stop would silently resurrect the very
guesswork being removed.

---

## Phase 1 — Sizing coherence (F-249) — APPROVED, ships now

**User decision 1 (settled): unify on `position_sizing()`.** Retire `_suggest_size`'s
formula entirely rather than capping it in place — one engine, ATR-based, already
tested and already capped. This deletes the `0.05/0.07/0.08` literals rather than
hoisting them to `constants.py`, resolving the Hard Rule #1 violation by removal.

1. **New-pick path (`daily_briefing.py:822`).** Move the sizing computation **below
   line 924**, where `_comp_data` is defined (`:834`) and guaranteed non-empty. Then:
   ```python
   _stop  = _f(_comp_data.get("stop"))
   sizing = position_sizing(portfolio_value, RISK_PCT_PER_TRADE, price, _stop,
                            max_position_pct=SINGLE_NAME_CEILING) or {}
   ```
   `position_sizing` returns **`None`** (not `{}`) when `entry <= stop` — the `or {}`
   guard is mandatory. `sizing` is only consumed at `:957`, well after all gates, so
   moving it is safe.
2. **Add-winner path (`daily_briefing.py:1175`).** Same swap, sourcing the stop from
   `held_data[ticker]["stop"]`. Removes the hardcoded `"Strong Uptrend"` (finding 2).
3. **Shape reconciliation.** `position_sizing` returns `shares / risk_budget /
   actual_risk / risk_per_share / total_cost / portfolio_pct / risk_pct_actual` (+
   ceiling flags). The renderer additionally needs `stop_pct`, `port_pct`, `entry_lo`,
   `entry_hi`. Compute `stop_pct`/`port_pct` locally (or map `portfolio_pct` →
   `port_pct`); take `entry_lo`/`entry_hi` **from `_comp_data`**, not from a heuristic.
4. **Render the cap.** When `ceiling_capped`, the Grow Today caption states the size
   was reduced to the single-name ceiling and what the risk-budget size would have
   been — CLAUDE.md's never-silently-filter rule. Same for the cron emails
   (`notify.py:311, 395, 559`).
5. **Fix `deploy_note` (user decision 7, settled: it is a risk statement).** Re-word
   only; **no number changes**. `"At 1.5% risk per trade across N setups, you'd be
   risking ~$X if every stop hits."` Must ship in the **same commit** as the cap — at
   $50k/3 setups the note ($2,250) and the capped cards ($22,500) still differ 10×, so
   fixing the cap alone leaves a contradiction on screen.
6. **Add the per-idea-maximum sentence.** One static line: the suggested size is a
   per-idea maximum, not a portfolio plan — at 15% each a book funds ~6–7 concurrent
   full positions. This is the honest answer to the capital confound and is arguably
   the most decision-useful sentence in the feature.

**`SINGLE_NAME_CEILING = 15.0` is used unchanged** as the cap for suggested new
positions — consistent with all four existing `position_sizing` call sites. No new
constant, no threshold value changes.

**Phase 1 alone substantially answers the user's literal question.** The honest current
answer to "how is recommended position sizing working" is: *three ways, inconsistently,
and the one on the buy card exceeds your own cap.*

### Blast radius

Grow Today new-pick and add-winner cards (`app.py:7308, 7586`); premarket/scan cron
emails (`notify.py:311, 395, 559`); `tests/test_grow_dropoff.py:225` (asserts dropoff
rows carry no sizing keys — verify unaffected).

### Tests this must include

- **Invariant: `port_pct` never exceeds `SINGLE_NAME_CEILING`** — property test across
  a wide price range × several portfolio values × both paths. This is the exact
  boundary the current code violates; reasoning is not a substitute for a test at the line.
- **Boundary:** `port_pct == SINGLE_NAME_CEILING` renders; `>` never occurs.
- **`shares >= 1` after capping**, including a high-price / small-account case that
  would otherwise floor to 0. `position_sizing` guards this with `max(1, ...)` on
  `ceiling_shares` (`risk.py:51`) — **a port that omits it silently emits "0 shares".
  This is the most likely defect in the implementation.**
- **`position_sizing` returning `None`** (entry ≤ stop) degrades to `{}`, no raise.
- **New-pick and add-winner paths agree** on stop methodology for equivalent inputs.
- **No bare `0.05`/`0.07`/`0.08` literal remains** in the sizing path.
- **`deploy_note` wording** contains no "deploying" verb and its number is unchanged.

---

## Phase 2 — Start the clock — CODE SHIPPED 2026-08-23, AWAITING DDL

**ACTION REQUIRED — nothing is captured until this runs.** Paste into the Supabase SQL editor
(same one-time convention as `model_predictions` / `analyst_target_snapshots`):

```sql
ALTER TABLE public.recommendations ADD COLUMN IF NOT EXISTS rec_shares          numeric;
ALTER TABLE public.recommendations ADD COLUMN IF NOT EXISTS rec_stop            numeric;
ALTER TABLE public.recommendations ADD COLUMN IF NOT EXISTS rec_portfolio_value numeric;
ALTER TABLE public.recommendations ADD COLUMN IF NOT EXISTS rec_sizing_version  integer;
```

Until it runs, the writer strips these four columns and retries, so the recommendation log
keeps working exactly as before — no error, no lost rows, just no sizing captured. **Every day
before it runs is a day of Phase 3 substrate that cannot be recovered**, because the suggested
size is computed at render time and discarded.

**Decision (9), taken during the build — the cascade had to change.** The first
implementation peeled optional-column generations positionally, newest-first. That is wrong: a
`bq_score`-missing error would strip the sizing columns on the way past, discarding data that
works because something unrelated is absent — the precise loss this cascade exists to prevent,
reported as success (`saved=N, error=None`). Caught by the **pre-existing**
`test_save_recommendations_pgrst204_schema_cache_error_degrades_and_retries`, which encodes a
real 2026-08-07 production incident. The cascade now reads the column name PostgREST reports
and strips **only that generation**, repeating if a retry reveals a different one.

**Two scope corrections to what this section originally claimed:**
1. **`buy_candidate` rows carry no sizing at all** — only `new_picks` and `add_positions` get a
   `sizing` dict from `_grow_today`. Their four columns stay NULL by design, and **Phase 3 must
   scope to `new_pick`/`add_winner`** or it will divide by NULL.
2. **`portfolio_value` was not in scope at the writer** (passed as a kwarg, not held as a
   local). Rather than re-read a portfolio total at write time — which could disagree with the
   basis the size was computed from — the **sizing dict now carries its own `portfolio_value`
   and `sizing_version`**, making a mismatch structurally impossible.

**Opus review: SHIP, 0 blocking.** It hand-verified all 36 substring pairs across the now
9-member `_OPTIONAL_COLS` for collisions (none), and proved the cascade terminates in every
branch. Seven non-blocking items taken, three of which mattered:
1. **The "all four NULL" enumeration was NOT exhaustive** — a fourth state exists and is
   reachable **post-DDL**: a `new_pick`/`add_winner` row whose sizing *input* was unavailable
   (no bundle stop for a held name, or no portfolio value) produces no sizing dict, hence four
   NULLs. So **Phase 3 must filter on a non-null `rec_shares`, never on `rec_date`.** Another
   instance of a doc claim stronger than the code, which is why it was asked for explicitly.
2. **First writer of the day wins.** The review said that is usually the CRON, reasoning from
   the code; **measured 2026-08-23 against real `surfaced_at` values, it is usually the
   INTERACTIVE session.** On 2026-08-21 the batches were 09:26 / 09:31 / 09:32 / 09:49 / 11:37
   ET (all interactive) while the scan lane's own `scanner_cache` row is stamped 10:46 ET. The
   owner opens the app before the cron fires. This is *better* for Phase 3 — the captured size
   is what was actually on screen — but it means the value is **not reproducible from
   end-of-day prices**, and the interactive write has **no post-open gate** (the 09:26 batch is
   pre-open, so a size can be computed off the prior close, which the `scan` lane explicitly
   refuses to do). Corollary either way: a row written with a degraded input **blocks** that
   day's later capture for the same ticker.
3. **A last-resort strip-all floor was restored.** Error-targeting is a strict improvement,
   but the pre-targeting code had an unconditional strip-everything terminal stage, and
   dropping it would have been a resilience regression on a log the recommendation history
   depends on.
Also taken: provenance keys are now pinned **per sizing shape** (the renderer-contract test is
a subset check, so dropping `sizing_version` would have left the suite green while capture
silently went NULL); the cron writer's sizing pass-through is tested for the first time; the
`generation <= stripped` anti-infinite-loop guard has a test; the cron row is built once rather
than mutating `rows[-1]`; and `rec_stop`'s two bases by `rec_type` are documented. Noted but
not changed: `_REC_COLS` is unextended (matching the F-179/pillar precedent), so
`load_recommendations` returns a narrower frame on the empty path — **Phase 3 must guard with
`in df.columns` rather than discovering that live.**

Original spec follows.

Four nullable additive columns on `recommendations`: `rec_shares`, `rec_stop`,
`rec_portfolio_value`, `rec_sizing_version`. All four are already in scope at both
writers (`app.py:5364` reads the pick dict; `cron_runner._build_new_pick_rows:121`
reads `p.get("sizing")`). Inert until the DDL is applied by hand — same convention as
`model_predictions` / `analyst_target_snapshots`.

`rec_sizing_version` exists so a future formula change can never be silently compared
across the boundary — the exact failure HEAD-audit finding 4 rules out. Set it to `2`
at cutover (`1` = the retired `_suggest_size` era, never actually persisted).

**No UI in this phase.** The drop-and-retry must add a **third generation set**, not
extend `_QA_PILLAR_COLS` — `db.py:2296-2306` documents why: a column-missing error must
strip only the columns actually missing, or a missing new column stops already-working
ones from persisting with no error surfaced (`saved=N, error=None`).

---

## Phase 3 — Sizing Calibration readout (GATED — do not start)

A section at the bottom of 🧭 Self vs Engine, over `app_aligned` BUYs whose `rec_date
>= <Phase 2 DDL date>` and whose rec row carries `rec_shares`:

**Version boundary caveat:** `SIZING_FORMULA_VERSION` bumped 2→3 on 2026-08-25 (F-255 net-capital cap). Phase 3 must not straddle this boundary without disambiguating — any take-rate comparison across it compares apples to oranges (the formula changed). Filter on version, or exclude Phase 1 rows from Phase 3's dataset entirely.

- **Headline: median take rate** = `actual_shares / rec_shares`, plus n. A ratio, not a
  dollar figure — sign-free and symmetric by construction.
- **Revealed risk-per-trade**, computed from actual shares and the stored `rec_stop` /
  `rec_portfolio_value`, stated **beside** `RISK_PCT_PER_TRADE` with the constant named.
- **Three candidate explanations, rendered unranked:** a genuinely lower risk appetite;
  a portfolio-level capital constraint the app's per-position rule doesn't model; or
  disagreement with the suggested size on specific names. **The surface does not choose.**
- **No P&L axis, no outcome axis, no dollar counterfactual.**

**Before writing Phase 3 code, read two numbers** (the F-246 measure-first precedent):
the current count of `app_aligned` BUYs from `classify_buys`, and the fraction of BUY
trades carrying a non-null `decision_context`. **If `app_aligned` is near zero, Phase 3
has no population, not merely a thin one.**

Expect it to be dark for a long time. Reference point: F-229's Defense facet sits at 3
matured calls against a floor of 8 after a month. This is why Phase 1 must carry the
feature's value on its own.

---

## Design principles (non-negotiable)

1. **No dollar "left on the table" figure, in any phase.** One-directional by
   construction — under-sizing a loser *saved* money — and a standing nudge to size up
   in an app whose posture is "recommend nothing rather than recommend wrongly." O1's
   design established this safeguard (`missed-opportunity-pattern.md` principle 8, the
   FOMO-amplifier reasoning). This feature goes further: **Phase 3 has no outcome axis
   at all**, making the FOMO framing structurally impossible rather than discouraged.
2. **The no-outcome-axis rule dissolves the open-vs-closed problem.** A take rate is
   settled at trade time and never moves; there is no unrealized number to present as
   fact and nothing that flips daily. Any future P&L axis (not authorised here) must be
   closed-lots-only, matching O5's precedent.
3. **The surface never proposes a new `RISK_PCT_PER_TRADE` value.** It names the
   constant and shows the observed number. Changing it is an investment-policy decision
   taken with the user, per Hard Rule #1. **A specific number rendered in the UI is a
   proposal regardless of how it's captioned** — and the naive inference is wrong
   anyway: `5/26 ≈ 0.3%` is computed against the *uncapped* 26; post-Phase-1 the same
   behaviour reads ≈ 0.6%. A UI shipped pre-Phase-1 would have argued for a 5×
   recalibration on a 2× artifact.
4. **Clean separation from O5 Sizing Alpha.** O5 = *your* sizes vs *your* outcomes, no
   app reference (`sizing-alpha.md` HEAD audit deliberately rejected the recommendations
   join). This = *your* sizes vs *the app's* sizes, no outcome reference. Disjoint axes.
   This is why Phase 3 lives on 🧭 Self vs Engine, **not** next to O5 on 🪞 Investor
   Mirror — adjacency would invite conflation of two metrics that share a word and
   nothing else.
5. **Offline contract, inherited not re-implemented.** `classify_buys` returns `None`
   when `recs_df is None`. Phase 3 branches on `is None` and renders "unavailable",
   never a take rate. A failed recs load must never read as "you followed nothing."
6. **Forward-only, with the cutoff date rendered**, matching `self_track_record`'s
   `coverage_limited` / `SELF_TRACK_RELIABLE_LOG_START` disclosure discipline.
7. **Retrospective-only containment.** 🎯 My Edge's charter (`app.py:30950`) is "never
   scores anything that feeds a recommendation elsewhere." Phase 3 publishes nothing to
   `st.session_state` and gates nothing.

## Non-goals

- **No aggregate dollar counterfactual — infeasible, not merely caveated.** At the
  corrected 15% cap, full size on all 18 holdings requires 270% of the book. Summing
  `(rec_shares − actual_shares) × Δprice` prices a portfolio that could not have
  existed. The per-trade take rate is the only honest unit; the aggregate is not
  rendered at all.
- No change to `SINGLE_NAME_CEILING`, `RISK_PCT_PER_TRADE`, or `ATR_STOP_MULT` values.
- **No materiality/deviation threshold constant.** A take rate is continuous; bucketing
  it into "material" vs "not" needs a boundary nobody can justify. Recommend explicitly
  against adding one.
- No retroactive reconstruction of pre-Phase-2 recommended sizes.
- No change to `match_recs_to_trades`, `distinct_missed`, `sizing_alpha`, or any
  Recommendations History content.
- No new `st.session_state` key and no consumer wiring. Checked against CLAUDE.md's
  coordination rule: the only surfaces that decide *size* are `position_sizing` and
  `_suggest_size`, and Phase 1 **unifies** them rather than adding a third voice —
  that is the coordination fix, not a coordination risk.

---

## Outstanding user decisions

**Settled 2026-08-22:** (1) unify on `position_sizing()`; (2) accept the calibration
reframe over a dollar figure; (7) `deploy_note` is a risk statement — re-word only.

**Settled 2026-08-23, decision (8) — taken mid-build, not anticipated by this plan.**
The new invariant test failed on 5 cases and exposed a **pre-existing** defect in
`position_sizing()` itself: `ceiling_shares = max(1, int(...))`. When one share costs
more than the ceiling allows, `int(...)` is 0, the floor emits 1 share anyway, **and
`ceiling_capped` stays `False`** — because `risk_based_shares` is also 1, so the
`shares > ceiling_shares` comparison never fires. A $4,500 name against a $10,000 book
returned **1 share = 45% of portfolio with no disclosure at all**. Already live on
Analysis and Watchlist; reachable through `scan_movers` (which surfaces names outside
the curated universe) or any ticker typed into Analysis. Breach threshold is
`price > portfolio_value × SINGLE_NAME_CEILING%`.
**Decision: fail closed** — suppress the size, show a visible banner, matching
CLAUDE.md's "hard suppressions with visible banners, not soft warnings" and the
"recommend nothing rather than recommend wrongly" posture. `position_sizing` returns
`None` (all four existing callers already guard on a falsy result, so suppression
propagates to Analysis and Watchlist too); `_position_size_for_render` returns
`{ceiling_infeasible, one_share_pct}` with **no `shares` key**, because every renderer
gates its size text on `shares` and a `0` would print "0 shares". Rejected
alternatives: showing 1 share with a breach flag (informs rather than decides), and
scoping the invariant test to document the limit (ships a known silent breach).

**Still open:**
- **(4)** Approve Phase 2's four columns + the manual DDL, and confirm
  `rec_sizing_version = 2` at cutover.
- **(5)** Set `SIZING_CALIBRATION_MIN_N` (Phase 3 sample floor). No value invented
  here. Precedents span a wide range — `INVESTOR_MIRROR_MIN_CLOSED_LOTS = 10`,
  `PROTECT_TRACK_MIN_CALLS = 8`, `predictive_analytics min_n = 3` — so this is a
  genuine choice, not a lookup.

## Routing

1. **Phase 1** — decision-bearing, stays on the lead. **`reviewer` (Opus) required**:
   touches a scoring/recommendation formula and a user-facing decision surface, and
   `daily_briefing` is a `_GATE_FILES` entry so the commit hook enforces the citation.
   Files: `stock_analyzer/daily_briefing.py`, `app.py` (~7308, ~7586),
   `stock_analyzer/notify.py`, `tests/test_daily_briefing.py`,
   `docs/requirements.md` (**F-249**), `docs/architecture.md`, `docs/user-manual.md`,
   `docs/shipped-log.md`.
2. **Phase 2** — mechanical, delegable to `implementer`; `reviewer` still hook-required
   (`db.py` is a `_GATE_FILES` entry). Files: `stock_analyzer/db.py`, `cron_runner.py`,
   `app.py` rec-row builder (~5364), `docs/architecture.md` §6.12.
3. **Phase 3** — **do not start.** Gated on the two measurements above and decisions 4–5.
