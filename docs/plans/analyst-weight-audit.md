# Analyst Weight Audit — is sell-side consensus earning its share of the composite?

**Status: Items 1 and 2 SHIPPED 2026-09-12 (not yet committed — pending user sign-off on
this session's diff).** Phase A (the free measurement) is **DONE**, recorded in §2 — it did
**not** meet its own pre-registered close condition, which justified the build. **Item 1c
was SCOPED BUT NOT BUILT** — while implementing, found its own design bucketed by
`window_end - article_date`, which is fixed at exactly 30 days for ~95% of rows (only
"sold" rows vary, a small and differently-biased subset), so it would have rendered a fake
gradient on almost no real variation; testing genuine decay needs new price fetches per
row (31-60d, 61-90d windows), contradicting the "no new fetch" premise — a bigger, deferred
piece of work, not built. Item 1b remains analysis/record only (no build), per §7. Two
`limit=100` loader defects from §9 were fixed in the same pass (see §9 for detail — both
now `limit=5000`, matching the Scorecard's own existing convention). All three deterministic
gates green: full `pytest` (5231 passed), `check_antipatterns.py`, `check_constants_documented.py`
(no-op — no new constant). Extends **F-154c** (Research Scorecard) — no new F-ID.
Phase B (§8, any weight/window change) remains fully undecided.

---

## 1. Why this exists

A 2026-09-12 read-only investor-POV analysis asked what investment intelligence the app is
missing. It found that **sell-side analyst opinion is 16.5% of every composite score**, and
that the app separately maintains a ledger grading exactly how accurate that opinion is —
and has never connected the two.

Transcribed from source (per CLAUDE.md's zero-hallucination rule, not from memory):

- `COMPOSITE_WEIGHTS` (`stock_analyzer/constants.py:753-758`) — valuation pillar = **0.30**.
- `valuation_score` (`stock_analyzer/valuation.py:20-113`) max points: forward P/E **25** +
  FCF yield **20** + PT upside **25** + analyst consensus rating **30** = 100.
  Analyst-derived = **55 / 100**.
- ⇒ **0.30 × 0.55 = 16.5% of the composite.**
- The pillar renormalises over *available* metrics (`score = points / max_points`,
  `valuation.py:111`). With `forward_pe` **and** `fcf_yield` both absent, the pillar becomes
  100% analyst opinion ⇒ analyst opinion rises to **30% of the composite**, silently.
- `VALUATION_CONSENSUS_PTS` (`constants.py:610-616`): Strong Buy **30** / Buy **24** /
  Hold **15** / Mixed **9** / Sell **0**.
- `COMPOSITE_BUY` = **65** is a **hard gate** — G-13/F-103 (Watchlist ENTER_NOW) and F-39
  (Grow Today new picks).

So an input with no measured track record participates in a hard buy gate.

---

## 2. Phase A — DONE 2026-09-12. Measured live, result recorded.

Read off 🧠 AI Insights → 📊 Scorecard on the live Railway deploy:

| Metric | Reading |
|---|---|
| Directional Accuracy | **48%** |
| PT Hit Rate | **36%** |
| Evaluable Calls | **442 of 653** (167 pending < 30d · 44 excluded) |

**Statistical reading, stated honestly.** At n=442, SE ≈ 2.4pp ⇒ 95% CI ≈
**43.3% – 52.7%**. 50% sits inside that interval, so 48% is **not** statistically
distinguishable from a coin flip — but it is ~5 SE below 60%. The defensible claim is
**"no measurable directional edge,"** *not* "analysts are wrong." Do not overstate this
downstream.

**Benchmark caveat — load-bearing, read before quoting the 48%.** `classify_call`
(`analyst_intel.py:397`) computes `directional_hit` as raw return against **zero**, not
against SPY. Sell-side ratings skew bullish, so in a market that drifted up over most
30-day windows this population should have scored *above* 50% simply by being long. 48% is
therefore worse than it first reads. **The market base rate over the sample window has NOT
been measured** — pinning it is open work, and until it is, the ceiling of what this data
supports is "no measurable edge," not "actively harmful."

**PT Hit Rate context.** `ANALYST_ACCURACY_PT_HIT_PCT = 0.75`, measured against the
window's **intraday high**, not the closing price — a deliberately generous test. It clears
36% of the time, which bears on the 25-point PT-upside component specifically.

### Block E cannot corroborate any of this — and that is itself a finding
Cells: `buy_agree` 17 (+1.3%) · `buy_disagree` 3 (+0.8%) · `sell_disagree` 2 (−1.1%) ·
`sell_agree` 1 (+6.4%) = **23 classifiable of 442**. Caption: **383 excluded (no engine
composite recorded at save time)** = **87% of the sample**; 36 excluded Hold/Mixed.

Both disagreement cells sit below `ANALYST_CALIBRATION_MIN_CASES` = 5. The 2026-09-07
structural-starvation fix (see memory `project_analyst_coverage`) was forward-only, so at
any realistic save rate this is **years**, not months, from being decidable.
**Treat F-154c Phase 3 as dormant, not "building."** Do not read a verdict out of n=1/n=2.

### The pre-registered close condition — RESOLVED, not met
Phase A was registered as: *"if measured directional accuracy is meaningfully better than a
coin flip, the current weight is defensible and this closes permanently."*
**48% on n=442 ⇒ not met.** Recorded here rather than left open.

---

## 3. Item 1 — consensus-ladder measurement

`VALUATION_CONSENSUS_PTS` pays a 15-point spread between Strong Buy (30) and Hold (15).
That is ~4.5% of the whole composite, swinging purely on the label. **For that ladder to be
earned, higher tiers must produce better outcomes — and nothing measures this.** Block A
lumps every bullish call into one hit rate. This result decides whether the eventual fix
targets the ladder's *shape* or its *level*, which is why it runs before any weight change.

### 3.1 THE load-bearing design decision: do NOT reuse `directional_hit`

`classify_call` (`analyst_intel.py:396-397`):

```python
is_bullish = consensus.startswith(("strong buy", "buy"))
directional_hit = (is_bullish and ret_pct > 0) or (not is_bullish and ret_pct < 0)
```

It treats **every non-bullish rating as pseudo-bearish** — a **Hold** row scores a "hit"
when the stock goes **down**. Comparing tiers on `directional_hit` would pit *"did Buy calls
rise"* against *"did Hold calls fall"* — two different questions — and in a falling market
Hold would look brilliant for no reason at all.

`calibration_matrix`'s own docstring documents this exact trap and sidesteps it by excluding
neutrals entirely. **This feature cannot use that escape**, because including Hold/Mixed is
the whole point. So it must solve it instead:

> **Measure `ret_pct` — the same quantity for every tier.** Report average forward return
> and % positive, per tier. **`directional_hit` must not appear in this function at all.**

### 3.2 `consensus_tier(consensus_rating: str | None) -> str | None`
New pure function in `stock_analyzer/analyst_intel.py`, sibling to the existing
`consensus_side()` (`analyst_intel.py:416`) — same file, same style, same docstring
conventions. Returns one of the `VALUATION_CONSENSUS_PTS` keys, or `None`.

- **Match the LEADING label only.** `derive_consensus()` stores
  `"Label (N Buy / N Hold / N Sell)"`, so the parenthetical **always** contains the literal
  word "Buy". A bare substring check false-positives on every row — a trap both
  `classify_call` and `consensus_side` already call out in comments.
- **Test `"strong buy"` before `"buy"`** — `str.startswith` order is load-bearing here.
- A missing/empty rating returns `None` (not an implicit anything), mirroring
  `consensus_side`.

### 3.3 `ladder_performance(results: list[dict]) -> dict`
Reads the same evaluated rows Blocks A–E already display; only `status in ("hit", "miss")`.

Per tier: `n`, `avg_ret_pct`, `pct_positive`, `points` (from `VALUATION_CONSENSUS_PTS`, so
the table can sit the *paid* points beside the *measured* outcome), `verdict_shown`.
Plus a top-level `n_unrated` for rows `consensus_tier` could not place — **disclosed on
screen, never silently dropped** (CLAUDE.md's never-silently-filter rule).

- **Sample floor: reuse `ANALYST_CALIBRATION_MIN_CASES` (5).** Same "don't show a verdict
  from a handful" semantic. Reuse follows the F-263 precedent (memory
  `project_leverage_shock_modeling`), chosen there specifically so two surfaces reading the
  same idea can never disagree. If a distinct floor is wanted, that is a **new
  `constants.py` value the owner sets** (Hard Rule #1) — not a default picked by the
  implementer.
- `avg_ret_pct` is `None` when `n == 0`. Never raises on empty input.

### 3.4 Render — one table under Block A
`app.py` ~37775, immediately after the pending/excluded caption.

Columns: **Tier · Points awarded · n · Avg return · % positive**.

Putting `points` next to `avg_ret_pct` is the entire point — it makes any mismatch between
what the composite *pays* and what the tier *delivers* visible without arithmetic.

Below-floor tiers render their `n` plus "need ≥5 cases", matching Block E's existing
`_sc_cal_disagree_line` convention.

The caption must state, in the app's own voice, that this measures **average forward
return**, not the Block A hit rate, **and why** (a Hold is not a bearish call). Without
that sentence a reader will assume the two tables disagree.

---

## 4. Item 1c — does a rating decay? Accuracy by age of the call

**NOT BUILT (2026-09-12) — see the top `**Status:**` line.** The design below has a real
flaw discovered during implementation: for ~95% of rows, `window_end - article_date` is
fixed at exactly 30 days (only "sold" rows vary), so bucketing by it would render a fake
gradient rather than a real one. Left below as a record of the original (flawed) design and
the reasoning for not building it — not a spec to hand to an implementer as-is.

`VALUATION_COVERAGE_FRESH_DAYS = 90` (`constants.py:602`) means a **three-month-old**
pasted rating still carries all 30 points in today's composite. Nothing has ever tested
whether a 90-day-old rating still carries information.

Same 442 rows, no new fetch — each already carries `article_date` (confirmed: `app.py`'s
`_sc_no_anchor` filter reads `r.get("article_date")`, so the field survives into
`_sc_results`).

Either an age dimension on `ladder_performance` or a sibling
`accuracy_by_age(results, buckets)`: bucket by days between `article_date` and the row's own
`window_end`, in **0–30 / 31–60 / 61–90** bands; report `n`, `avg_ret_pct`, `pct_positive`
per band under the same `ANALYST_CALIBRATION_MIN_CASES` floor.

**Read it as a gradient, not a verdict.** Three bands over 442 rows is ~147 each at best,
and the bands are **not independent of market conditions** — an older call's window sits in
a different tape. Say so in the caption.

**Why this is worth having before Phase B:** if decay is visible, **shortening
`VALUATION_COVERAGE_FRESH_DAYS` is a materially smaller and safer fix than re-weighting the
pillar** — one constant, one meaning, no denominator change. Changing that window is still
`constants.py` ⇒ Opus `planner` + `reviewer`, owner sets the number. The *measurement*
touches no gate file.

---

## 5. Item 2 — correct Block E's caption (display-only)

`app.py` ~37967-37972 currently says the population "still skews toward Engine ≥ 65" and
that "this skew fades as more research is saved going forward."

With **23 classifiable of 442** and both disagreement cells below the floor of 5, that copy
is materially over-optimistic: this is not a skew, it is near-total absence, and the fix
that would populate it was forward-only. Reword to state the real position — how many rows
lack a composite, that the matrix is therefore **dormant** rather than filling in, and that
only newly-saved research counts toward it.

Keep it factual and derive every number from what `_sc_cal` already returns — do not
hardcode 383 or 23, which change as research accrues.

---

## 6. Redlines, review path, and scope

### Redlines
Display-only throughout. Never feeds `valuation_score`, `scoring`, or **any** gate —
identical posture to `calibration_matrix`. Pure functions, no I/O, no new DB read (reuses
the already-loaded `_sc_results` from `app.py:37652`, which correctly uses `limit=5000`).

**No new constant.** If one appears, it is a policy value for the owner to set.

### Review path — deliberate skip, stated because the hook cannot enforce it
`stock_analyzer/analyst_intel.py` is **not** in `_GATE_FILES` (verified against
`.claude/hooks/pre_tool_checks.py:280-302`), and this adds no gate, no recommendation and
no threshold — it is a measurement table on an already awareness-only page.
**Skipping the Opus `reviewer` per CLAUDE.md's "Review & test economy."** Recorded here
because the commit hook will not force it and the call is the author's.

### Tests — `tests/test_analyst_intel.py` (exists)
- `consensus_tier`: `"Strong Buy (…)"` → `Strong Buy`, **not** `Buy`; a
  `"Sell (3 Buy / 1 Sell)"` row → `Sell`, proving the parenthetical doesn't leak;
  `None`/`""` → `None`.
- `ladder_performance`: **a fixture where a Hold tier's stocks all fell must NOT score
  well** — the direct regression against ever reusing `directional_hit` here.
- Below-floor tiers set `verdict_shown=False`; `n_unrated` counts rather than drops.
- Empty input returns cleanly.
- `accuracy_by_age`: a row exactly on a bucket boundary lands in **exactly one** band — pin
  the inclusive/exclusive edge explicitly (this repo has been bitten by boundary ambiguity
  before, e.g. `EXIT_SIGNAL_ACT_WINDOW_DAYS`'s inclusive edge). Rows with a missing
  `article_date` are counted as unplaceable and disclosed, never silently dropped.

### Verification
- Full `pytest`; `scripts/check_antipatterns.py`; `scripts/check_constants_documented.py`
  (a no-op confirmation — no new constant).
- **Verify the gates actually ran.** Memory `feedback_hook_enforcement`: the `PreToolUse`
  commit hook was observed **not firing at all** in a VSCode-extension session on
  2026-09-09. Do not assume it fired.
- Never run locally (Hard Rule #3). Push to `main`, ~2 min for Railway, hard-refresh
  `drishta.up.railway.app` → 🧠 AI Insights → 📊 Scorecard.
- **On-screen check:** the new table's tier `n` values must sum to `442 − n_unrated`. If
  they don't, `consensus_tier` is dropping rows the parenthetical trap should have caught.

### Docs to sync in the SAME session (Definition of Done)
`docs/requirements.md` F-154c (extend, same feature) · `docs/architecture.md`'s
`analyst_intel.py` module section · `docs/shipped-log.md` · **this file's own `**Status:**`
line** (step 7) · memory `project_analyst_coverage` with the 48% / n=442 result.

---

## 7. Item 1b — the coverage asymmetry (ANALYSIS ONLY, no build in this change)

**Owner's question, 2026-09-12:** is pasted CNBC research adding value, or duplicating what
yfinance / Finnhub already provide? Traced end to end. The two analyst components are in
completely different positions.

**PT upside (25 pts) — largely duplicative.** `valuation.py:82-85` prefers the pasted
`avg_pt` but **falls back to `financials["analyst_target"]`** (yfinance `targetMeanPrice`,
`data.py:261`). The component scores either way. Pasting changes *whose* target is used,
not whether 25 points are awarded — and yfinance's mean is drawn from a wider analyst panel
than one article's firms.

**Consensus rating (30 pts) — NOT duplicative, because the free version is discarded.**
`valuation.py:104-109` requires `analyst_data["consensus_label"]` + `has_coverage`, with
**no fallback**; `_analyst_data` is built solely from `db.load_analyst_coverage(...)`
(`bundle_loader.py:127`). Meanwhile `data.py:266-267` fetches yfinance `recommendationMean`
+ `numberOfAnalystOpinions` on every load — `num_analyst_opinions` is read once
(`headless_alert_engine.py:284`) and **`recommendationMean` is consumed by nothing**
(verified by grep across `stock_analyzer/` + `app.py`). Same write-only class as
`daily_regime` / `sentiment_history`.

**The structural consequence.** Because `score = points / max_points`, coverage changes the
**denominator**:

| | max_points | Analyst share of pillar | Analyst share of composite |
|---|---|---|---|
| With a paste | 100 | 55/100 = **55%** | **16.5%** |
| Without | 70 | 25/70 = **36%** | **~10.7%** |

Worked example — same company, fair P/E (19/25), good FCF (15/20), modest PT upside
(12/25): uncovered **65.7** · +Strong Buy paste **76.0** · +Hold paste **61.0**. Valuation
is 0.30 of the composite ⇒ a ±3–4 composite-point swing on identical fundamentals, and
**`COMPOSITE_BUY` = 65**, so a paste can move a name across the buy gate in either
direction.

Against §2's 48%/n=442: **doing research makes a name's score more dependent on the one
input with no measured edge**, amplifying whatever the analysts said either way.

**The honest counter-argument, kept deliberately.** Pasted rows are per-firm, timestamped
and immutable — which is *why* the Research Scorecard can grade them at all. yfinance's
consensus is a rolling overwritten value with no history and could never be scored this
way. The pastes have real **accountability** value even where predictive value measures at
nil. And `recommendationMean` has **never been measured**, so no claim that it would be
better is available — **do not assert one.**

### Why this is not built here
An earlier draft proposed measuring "covered vs uncovered valuation-pillar distribution"
beside the ladder table. **That is not possible from this dataset** — every
`analyst_coverage` row is by definition covered, so there is no uncovered control group in
`_sc_results`; it would produce a comparison with one arm empty.

It also does not *need* measuring: the denominator difference is **arithmetic, provable
from `valuation.py:111`**, not a statistical effect. What is missing is **disclosure**, not
evidence. Nothing on any scoring surface says which denominator a name was scored on —
`val_available` (`app.py:21186, 21320, 21423, 22244`) is only a binary G-15 withhold gate
that fires when *no* metric had data, never when 3 of 4 did; `val_signals` renders as a
key/value list (`app.py:22703`) inside a detail panel, so the metric count is technically
visible but its effect on the weighting is never stated.

⇒ **Treated as Phase B option (c)** (§8), on the 📈 Analysis scorecard / Grow Today cards —
a different surface from this change. The inputs already exist in the bundle
(`val_analyst_data.has_coverage`, `val_signals` — `bundle_loader.py:215, 218`), so a
render-side disclosure needs **no `valuation.py` change**.

---

## 8. Phase B — justified, but scope it only AFTER Items 1 and 1c

`constants.py` / `valuation.py` ⇒ investment policy (Hard Rule #1) ⇒ Opus `planner` design
pass + **mandatory** Opus `reviewer` + **the owner sets the actual numbers.** Do not
pre-commit to any of these.

- **(a) Re-weight the pillar internally** — shift points from consensus/PT toward forward
  P/E and FCF yield. Simple, but picks numbers blind to the ladder's shape.
- **(b) Asymmetric consensus** — keep Sell at 0 (a downgrade is rare and informative),
  compress the Strong-Buy bonus (ubiquitous and, per §2, uninformative). Better motivated,
  more invasive.
- **(c) Close the renormalisation hole — independently justified, do regardless.** When
  `forward_pe` and `fcf_yield` are both absent the pillar becomes 100% analyst opinion, so
  the app leans hardest on its weakest-measured input at exactly the moment it has no
  objective data. Nothing discloses it. **G-15 is the precedent** (withhold rather than
  fabricate when fundamentals are absent); this applies the same principle one pillar over,
  and is far smaller than re-weighting.
- **Considered, NOT recommended yet:** giving the consensus component a `recommendationMean`
  fallback would close the denominator asymmetry for free (every ticker would score all 100
  points). But it fixes an unmeasured input by adding another unmeasured input.
  `recommendationMean` needs a track record of its own first.

**Recommended order: Items 1 + 1c → (c) regardless → their result decides between (a) and (b).**

---

## 9. Found in passing — NOT part of this change

- **Two `limit=100` loader defects.** `db.load_analyst_coverage` defaults to `limit=100`
  (`db.py:2123`). Two callers take the default:
  - `app.py:35761` — `_ac_df_all = load_analyst_coverage()   # total count for status header`.
    The comment says "total count"; the call caps at 100. This is why the AI Insights header
    reads `Research: 100 saved · 100 within 30d`.
  - `app.py:39220` — feeds `_dq.classify_all_buys(...)` for **My Edge → Workflow ROI**
    (F-184). Rows return `article_date DESC`, so only the newest 100 are seen and **analyst
    research attached to older trades is invisible**, demoting those trades toward "Basic"
    or "Cold Entry". The bias has a **direction**: older trades are also the ones most
    likely to be matured and graded, so the prep-tier comparison is tilted toward concluding
    prep doesn't help.
  - `app.py:3328` is a third, but its own docstring says annotate-only, never gate or rank —
    low stakes.
  - Same class as the `load_model_predictions` PostgREST truncation in memory
    `project_predictive_shadow_modeling`. Neither is a `_GATE_FILES` member, so the free
    deterministic gates suffice.
- **`self_track_record.classify_sells`** (`self_track_record.py:401-487`) buckets a SELL as
  `self_initiated` whenever no EXIT/TRIM signal exists in the window — conflating *the
  engine disagreed* with *the engine had no view yet* (position too young for the ladder to
  fire). Per `scripts/exit_ladder_replay.py`, most closed losing round trips are held 0–10
  days, so on this book that bucket is likely dominated by the second case — meaning F-256's
  headline comparison partly measures **hold duration** rather than **decision quality**.
- **The market base rate for §2's 48%** is still unmeasured. Pinning SPY's 30-day forward
  up-rate over the sample window would convert "no measurable edge" into a sharper claim.

---

## 10. Related

- `docs/requirements.md` F-154c — Research Scorecard + Phase 3 calibration.
- `docs/plans/analyst-research-accountability.md` — the Scorecard's own plan doc.
- Memory `project_analyst_coverage` — Ideas Inbox + Scorecard history, the 2026-09-07
  structural-starvation fix, the back-dated-anchor fix.
- Memory `project_leverage_shock_modeling` — the constant-reuse precedent cited in §3.3.
- Memory `feedback_hook_enforcement` — why §6's gates must be verified manually.
