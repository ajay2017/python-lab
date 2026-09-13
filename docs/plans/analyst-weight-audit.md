# Analyst Weight Audit — is sell-side consensus earning its share of the composite?

**Status: Items 1 and 2 SHIPPED 2026-09-12, committed and pushed directly to `main`**
(commit `966a24a` — this repo is single-branch, direct-to-`main`, no feature branches).
Phase A (the free measurement) is **DONE**, recorded in §2 — it did **not** meet its own
pre-registered close condition, which justified the build. **Item 1c was SCOPED BUT NOT
BUILT** — while implementing, found its own design bucketed by `window_end - article_date`,
which is fixed at exactly 30 days for ~95% of rows (only "sold" rows vary, a small and
differently-biased subset), so it would have rendered a fake gradient on almost no real
variation; testing genuine decay needs new price fetches per row (31-60d, 61-90d windows),
contradicting the "no new fetch" premise — a bigger, deferred piece of work, not built. Item
1b remains analysis/record only (no build), per §7. Two `limit=100` loader defects from §9
were fixed in the same commit (both now `limit=5000`, matching the Scorecard's own existing
convention). All three deterministic gates green: full `pytest` (5231 passed),
`check_antipatterns.py`, `check_constants_documented.py` (no-op — no new constant). Extends
**F-154c** (Research Scorecard) — no new F-ID. **First live production reading, 2026-09-13
(§8a): confirms n=0 for the "Buy" tier is a genuine, verified data-pattern fact, not a bug
— traced to a deterministic proof, an exhaustive grep, and a live on-screen sort check —
and shows the return ordering is INVERTED at the extremes (Sell +4.2% beats Strong Buy
+1.8%, on n=22/n=395, not tiny samples), sharper evidence than Phase A's flat 48%.**
**SPY-relative alpha SHIPPED 2026-09-13 (§8b, same day)** — closes §8a's biggest open
caveat (absolute return, not SPY-relative) by adding a per-tier `avg_alpha_pct`/`n_alpha`/
`alpha_verdict_shown` to `ladder_performance()`, rendered as each tile's `st.metric` delta.
One SPY history fetch total (not per-row), 15 new tests, full suite 5241 passed, gates
green, no new constant. **Read live same day (§8b's closing subsection): the inversion
SURVIVES and slightly WIDENS on alpha** — Strong Buy −0.3% vs SPY, Hold −2.1%, Sell +3.5%
(Sell-vs-Strong-Buy gap: 2.9pp raw → 3.8pp on alpha). Not a market-regime artifact. Evidence
package is now complete; the Sell-selection-bias caveat remains and must travel into the
`planner` brief. **Commissioning the Opus `planner` design pass for Phase B now** — see the
task notification / this file's next update for the verdict.

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

## 8a. Consensus-Ladder Performance — first live production reading (2026-09-13)

Measured directly on the deployed `drishta.up.railway.app` render, the day after Items 1/2
shipped, via screenshots + hover tooltips (raw text quoted verbatim from the `help=` string
each `st.metric` call renders):

| Tier | Points paid | n | % positive | avg forward return |
|---|---|---|---|---|
| Strong Buy | 30 | **395** | 45% | **+1.8%** |
| Buy | 24 | **0** | — | — |
| Hold | 15 | **38** | 53% | **−0.8%** |
| Mixed | 9 | <5 (below floor) | — | — |
| Sell | 0 | **22** | 64% | **+4.2%** |

(Sum of tiers ≈ 455, vs. the "442 of 653" total quoted in §2 — expected drift, not an
inconsistency: a full day passed between the two reads, and calls mature out of "pending"
once they cross the 30-day window, often in small same-day batches.)

### Finding 1 — the "Buy" tier is structurally dead on this data, confirmed not a bug

`n=0` for Buy was investigated end-to-end before accepting it, not assumed:

1. **Deterministic proof, not a hypothesis.** Traced every branch of
   `derive_consensus()` (`analyst_intel.py:224-242`): for a single-firm row (`n_rated=1`,
   the dominant shape — one analyst quoted in one pasted article), `bull_frac` can only be
   `0.0` or `1.0`. `1.0` clears `ANALYST_CONSENSUS_STRONG_BUY_FRAC` (0.80) → **Strong Buy**;
   `0.0` falls through to the bear-fraction check → **Sell** or **Hold**. **"Buy" and
   "Mixed" are only reachable when `n_rated >= 2` and the split lands in the narrow
   50%–79.9% bullish band** — genuine multi-firm disagreement on one saved record.
2. **No alternate code path could produce a different string shape.** Grepped the whole
   package: `derive_consensus()` (`analyst_intel.py:242`) is the **only** site that ever
   constructs a `consensus_rating` value. No legacy format, no second writer — so
   `consensus_tier()`'s string matching cannot be missing an alternate shape.
3. **Live-verified on the real table, not just derived.** Block B's per-call table (built
   from the FULL 653-row `_sc_results`, not just the evaluable subset — confirmed at
   `app.py:37857-37866`, no filter) was sorted ascending by Consensus. The first row after
   the last "—" (no-rating) row was `Hold (0 Buy / 1 Hold / 0 Sell)` — if any bare `Buy (`
   row existed anywhere in the 653, it would sort alphabetically before `Hold` and would
   have to appear at exactly that boundary. It didn't. **Confirmed zero `Buy (` rows exist
   in the full saved-research library**, not just among evaluable ones.
4. **A live example of why:** the project's own first proof of this feature (2026-07-04,
   INIO) was a genuine 5-firm multi-analyst case — and all 5 were Buy, landing in Strong Buy
   (100% bullish), not the middle band. Even real multi-firm coverage in this app's actual
   usage tends toward near-unanimity (CNBC-style "top picks" roundups are a curated-agreement
   article genre, not a random cross-section of sell-side opinion).

**Conclusion: the composite's 24-point "Buy" tier is not measurement-thin, it is
structurally unreachable given how this feature is actually used** (paste one article,
extract per-firm ratings). Any future Phase B redesign should treat `VALUATION_CONSENSUS_PTS`
as an **effective 3-tier ladder** (Strong Buy / Hold / Sell) on this data, not a genuine
5-tier one — a design defect independent of whatever the return-ordering question below
decides.

**One side-observation from the same investigation, not yet acted on:** several rows with a
real `Avg PT` (QCOM, DELL, SNDK, 6082-HK) still showed "No rating data." `derive_consensus`
excludes a firm from the bull/neutral/bear tally when its rating text doesn't match any
recognized word (`BULLISH_RATINGS`/`BEARISH_RATINGS`/the neutral set), so a PT can be
captured while `n_rated` stays 0. Minor, worth a future look at what wording is slipping
through unrecognized — not scoped here.

### Finding 2 — the return ordering is inverted at the extremes, on real sample sizes

Sell (0 points, **n=22**) beat Strong Buy (30 points, **n=395**): **+4.2% vs +1.8%**. Hold
(15 points, **n=38**) was negative. This is sharper than §2's "no measurable edge" — it's
not flat, it runs **opposite** to what the point ladder assumes, and n=22/n=38 clear the
`ANALYST_CALIBRATION_MIN_CASES` floor by a comfortable margin, not by 1 or 2.

**Two honest caveats, not resolved, do not drop them if this is cited later:**
- **Absolute return, not SPY-relative.** All three figures sit inside whatever the market
  did over their own measurement windows; this alone doesn't prove alpha in either
  direction.
- **Selection effect on Sell specifically.** These 22 Sell-rated calls are not a random
  sample of every Sell rating in the market — they're the ones the owner specifically chose
  to paste. Pasting a bearish note often reflects curiosity about whether the bears are
  *wrong*, which could bias this specific sample toward Sell-rated names that outperformed,
  in a way that wouldn't generalize. Strong Buy (≈90% of the whole library) is far less
  subject to this same cherry-picking, since there's no reason to think only weak Strong-Buy
  calls get pasted.

**Combined effect on Phase B:** strengthens (b) over (a) specifically — the plan's original
framing of (b) was "Strong Buy is ubiquitous and uninformative"; this reading says it's
worse than uninformative, it's currently the tier Sell beats. Finding 1 (Buy is dead) is
independent evidence for restructuring regardless of what any accuracy number says. Neither
finding authorizes touching `constants.py` — still full `planner` + mandatory `reviewer` +
the owner's explicit sign-off, unchanged from §8's requirement.

---

## 8b. SPY-relative alpha added to the ladder table (2026-09-13, built same day as §8a)

The single biggest caveat on §8a's Finding 2 was that everything measured was **absolute**
return — all three tiers sit inside whatever the market did over their own windows, so
"+4.2% vs +1.8%" could partly just be "the market was up and Sell-rated names happened to
sit in a rally too." Rather than commission the `planner` design pass on that caveat still
open, closed it first: cheap, no gate touch, same review-exempt posture as Items 1/2.

**What shipped:** `ladder_performance()` gained an optional `spy_close_by_date: dict | None`
parameter (backward-compatible — omitting it leaves every existing field unchanged and
every new alpha field reads as "not computed," never a fabricated value). Per row, a new
private `_spy_return_pct(spy_close_by_date, start_d, end_d)` benchmarks SPY over the SAME
`(article_date, window_end)` window `classify_call` already used for the stock's own
`ret_pct` — same nearest-close-on-or-before lookup semantics as
`recommendations_history._spy_return_pct`, deliberately kept as its own small copy per this
codebase's existing convention (`trade_review.py` already has a third independent variant
of the same idea) rather than a new cross-module import.

**A real cost consideration surfaced and designed around before writing any code:** fetching
SPY per-ROW (reusing the render's existing per-row cached OHLC fetcher with `ticker="SPY"`)
would have created up to ~450 distinct new cache keys — one network fetch per unique
`(start, end)` window across the library, since most rows have a different `article_date`.
Instead, SPY's history is fetched **once** via the existing `_cached_spy(period)` helper
(the same one Recommendations History already uses for its own `_rh_spy_by_date`, `app.py`
~28686-28699) and turned into one `{date: close}` dict for O(1) lookups per row — one fetch
total, not up to 450. **Period is `"2y"`, not the `"6mo"`/`"1y"` used elsewhere on this
page** — several saved `analyst_coverage` article_dates go back over a year (one excluded
row seen during the Buy=0 investigation dated 2025-07-09), and a shorter window would have
silently starved alpha coverage for the library's oldest calls.

**Each tier's output gained three fields**, each with its own independent floor
(reusing `ANALYST_CALIBRATION_MIN_CASES`, same constant-reuse discipline as the raw-return
verdict): `n_alpha` (count of rows in this tier with a usable SPY benchmark — can be LESS
than `n`, since a row's own window may fall outside the fetched SPY history even when its
own `ret_pct` is known), `avg_alpha_pct` (`ret_pct − spy_return_pct`, averaged over just
those `n_alpha` rows), and `alpha_verdict_shown`. **A tier can clear the raw-return floor
while its alpha floor stays unmet** — pinned by a dedicated regression test — so the two
verdicts are never conflated.

**Render:** the alpha reads as the `st.metric` `delta` on each tier tile (raw return stays
the primary `value`), using the default `delta_color="normal"` deliberately — alpha is a
benefit metric in the same direction for every tier here, so there is no sign-flip case a
non-default `delta_color` would be needed for (see memory
`feedback_metric_delta_color_sign_trap`). 15 new tests (5 for `_spy_return_pct`, 5 for the
new `ladder_performance` alpha fields, plus fixture updates). Full suite 5241 passed;
antipattern + constants-doc gates green; no new constant.

### Live reading, 2026-09-13 (later same day) — the inversion SURVIVES, and slightly widens

Read directly off `drishta.up.railway.app` via screenshot immediately after this shipped:

| Tier | Raw return | Alpha vs SPY |
|---|---|---|
| Strong Buy (30 pts) | +1.5% | **−0.3%** |
| Buy (24 pts) | — | — |
| Hold (15 pts) | −0.9% | **−2.1%** |
| Mixed (9 pts) | — | — |
| Sell (0 pts) | +4.4% | **+3.5%** |

(Raw numbers drifted slightly from §8a's +1.8%/−0.8%/+4.2% — expected, a day passed and
more calls matured, same explanation as the earlier n-drift.)

**Sanity check on the benchmark itself:** the implied SPY return behind each tier
(raw − alpha) comes out to roughly +1.8%, +1.2%, +0.9% across the three tiers — a narrow,
consistent band. Had these implied differed wildly per tier, that would have cast doubt on
the benchmarking mechanism itself; a tight band is exactly what a working implementation
should produce.

**The result: this was NOT a market-regime artifact.** The gap between Sell and Strong Buy
is 2.9 points on raw return (+4.4% − +1.5%) but **3.8 points on alpha** (+3.5% − (−0.3%)) —
wider, not narrower, once the market's own move is stripped out. Strong Buy (the composite's
most expensive tier, 30 points) is a market-*matching* holding at best; Sell (0 points) is
the one tier that actually produced alpha. Hold is worst on both bases.

**What remains open, and cannot be closed by more measurement:** the Sell-selection-bias
caveat from §8a Finding 2 — these 22 names are research the owner specifically chose to
paste, not a random draw of every Sell rating in the market. This has to travel into the
`planner` brief as an acknowledged limit, not something resolved by this alpha check.

**This closes the evidence-gathering phase.** Commissioning the Opus `planner` design pass
for Phase B next, per the sequence agreed with the owner (build alpha → read live → THEN
commission planner — not before).

---

## 9. Found in passing — NOT part of this change

**Both `limit=100` items below were FIXED in the same commit as Items 1/2 (2026-09-12,
`966a24a`)** — left described here in the original past tense as a record of what was found,
but see the top `**Status:**` line for the current state; don't read this section as still
open.

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
