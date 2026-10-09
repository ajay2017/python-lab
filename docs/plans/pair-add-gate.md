# G-26 — Standing Danger-Tier Correlated-Pair Add Suppression

**Status: G-26 SHIPPED 2026-10-08 (`35b5bf4`); F-290 disclosure 2026-10-09 (`0f7bf67`); F-291 persisted-claim guard (§7), F-292 EOD history (§8) and F-293 on-screen disclosure (§9) ALL SHIPPED 2026-10-09 — the 3-commit sequence is COMPLETE: records withhold, screens disclose. DEPLOYED AND DORMANT as of 2026-10-09 —
the non-firing path is screenshot-confirmed, the FIRING path is still unobserved
and cannot be forced (see §4a).** Designed by the Opus
`planner` (verdict: PROCEED WITH CHANGES — it rejected two details of the
original §D6 framing, see §2), built by `implementer` (Sonnet 5), all three open
policy decisions ratified by the owner the same day. Requirements: `docs/requirements.md`
F-289 + the §2A.3 G-26 row. Memory: `project_pair_add_gate`.

---

## 1. The gap this closes

Recorded as **D6** in [cluster-add-gate.md](cluster-add-gate.md) during G-25's
design, deliberately not fixed there:

> a danger-tier `PAIR_RISK` trim call (0.80+ correlation) is today not read by
> Grow Today's add-to-winner logic at all — `_trim_targets` only reads Risk
> Advisor beta/Sharpe recs. So after this ships, a brand-new 0.66 pair pauses
> adds, while a long-standing 0.85 pair with an active trim call doesn't.

Verified independently before the design pass: `PAIR_RISK` appears only in
`portfolio.py` (the producer) and one comment in `rec_events_capture.py`.
Nothing in `daily_briefing.py` reads it.

So the app was **stricter about a weak new signal than a strong standing one** —
the inversion G-25 created by shipping first. D6's stated trigger was "an
explicit owner ask"; that was given 2026-10-08.

---

## 2. Two details of §D6's framing the design pass rejected

§D6 proposed "same shape as G-01", i.e. keyed on the emitted `PAIR_RISK` trim
call. Building it that way would have shipped two defects:

1. **Keying on the trim call lets a data outage switch the gate off.**
   `portfolio.py`'s `PAIR_RISK` producer `continue`s past any pair where either
   name lacks `Score Available` — an ETF, or a stock mid-fundamentals-outage.
   So a fundamentals outage on one leg would silently lift the pause on the
   other. **A gate that switches itself off during a data outage is exactly what
   this project's hard rules forbid.** Correlation is price-only and keeps
   working through that outage.

2. **`risk_pairs == []` is not a safe offline tell.** `home_risk_synthesis.py`'s
   `except` branch sets BOTH `corr_df = pd.DataFrame()` and `risk_pairs = []`,
   so an empty list is ambiguous between "checked, clean" and "correlation
   failed". `div_recs` doesn't disambiguate either — if correlation failed the
   rec builder still runs and returns REDUCE/ADD items, so its list is non-`None`
   and merely has no `PAIR_RISK` entries. **The one reliable signal is an empty
   `corr_df`.** This is the sentinel-collapse class this project has shipped and
   fixed repeatedly (`feedback_sentinel_is_present`).

A third correctness detail, found in the same pass: `diversification_score`
stores `"corr": round(c, 2)` but classifies `"level"` from the **unrounded**
value. A 0.799 pair is stored and displayed as `0.80` yet is warning-tier.
**The gate must branch on `level == "danger"`, never on `corr >= 0.80`** — a
naive numeric check fires on a pair the app itself classified as warning.

---

## 3. Owner decisions (ratified 2026-10-08)

- **D1 — Trigger: the pair itself, not the trim call.** Fire on any danger-tier
  pair where both names are held, regardless of whether a trim card exists.
  Covers everything the trim card covers, plus the outage case above.
  Consequence accepted: G-26 can pause an add on a pair with no visible trim
  card. The banner discloses which case applies, via a per-partner
  `named` / `not_named` / `unknown` tag.
- **D2 — No acknowledge/override, no expiry.** The pause lasts while the pair
  stays danger-tier and both names are held. **A partial trim of the weaker name
  does NOT lift it**, because trimming does not change correlation — the
  `PAIR_RISK` trim card behaves the same way. So a pair the owner deliberately
  keeps has adds paused on both names indefinitely. G-25 has a natural release
  (running a Structural Scan acknowledges it); G-26 has none, and building one
  would need new stored state. **Deferred, not rejected** — the trigger to
  revisit is a pair the owner deliberately holds actually binding in practice.
- **D3 — G-26 is checked before G-25.** A brand-new pair that is also ≥0.80
  records under G-26. Chosen to leave the older gates' Gate Suppression Ledger
  history untouched ahead of their first evaluable verdicts, and to stop a
  single pause being recorded as G-25 one day and G-26 the next purely because
  the owner ran a Structural Scan. G-25 had two days of history at ship time, so
  almost nothing is reattributed. Disclosed via `readout_footnotes`.
- **D4 — No new constant.** Reuses `CORR_DANGER_PAIRS_THRESHOLD` (0.80) as-is;
  it appears only as the ledger row's `gate_threshold`. No weight floor, same
  reasoning as G-25's D3.
- **D5 — Pure suppression.** No trim is ever recommended — the same boundary as
  G-25's deliberately-deferred Phase 2.

---

## 4. What shipped

New pure module `stock_analyzer/pair_add_gate.py` (joined `_GATE_FILES`):

| function | contract |
|---|---|
| `add_block_map(corr_df, risk_pairs, div_recs, held_tickers)` | `None` = couldn't check · `{}` = checked, clean · populated = firing |
| `describe_reason(entry)` | ≤300 chars, one clause per partner type, always ends "This pause adds no recommendation of its own." |
| `split_scan_liftable(cluster_blocks, pair_blocks)` | splits G-25's banner list into scan-liftable vs. also-pair-blocked |
| `buy_lane_only(pair_blocked_adds, buy_lane_skips)` | `None` passes through |

Wired into both Brief add-lanes in `daily_briefing.py`, registered in
`gate_registry.py`, captured to the ledger via `gate_ledger.py`'s
`"pair_blocked_adds": "add_winner"` lane. **`pair_buy_lane_skips` is
deliberately NOT a ledger lane** — it is disclosure plumbing only, and a stray
entry there would corrupt G-26's own forward-alpha measurement.

Render surfaces: a "🔗 Add Paused — Standing Correlated Pair (≥0.80)" block on
Grow Today, a caption under "More Buy Candidates", an also-pair clause on Home's
Structural alert banner (so G-25's "until you review this cluster" copy stops
being a false promise for a ticker whose pause will survive the scan), and a
caption on the `PAIR_RISK` trim card in 📡 Signals & Advice.

---

## 4a. Production observation, 2026-10-09 (deploy day + 1)

**Deployed and DORMANT — the non-firing path is confirmed, the firing path is
not.** Verified by live screenshot, not inferred:

📡 Signals & Advice → 🧩 Diversification → "Reduce / Rebalance" held exactly
ONE card (an Enterprise Tech sector REDUCE, 22.2% → 15.0%) before the "Add for
Diversification" header began. `PAIR_RISK` recs render in that same list
(`app.py` filters `type in ("REDUCE", "PAIR_RISK")`), so their absence there is
conclusive: **no pair in the book is at ≥0.80 today.** Max pairwise correlation
is still under the danger tier, consistent with the 0.77 CRM×SAP measurement
and with the 2026-10-06 cluster having fired at the 0.65 (warning) tier.

So G-26 correctly suppressed nothing, and the Grow Today banner / partner text
/ nav-button logic have **never rendered with real data.** Do not read "it
shipped and the app is fine" as "the feature was seen working" — `app.py` has
no test coverage, so until a pair actually crosses 0.80 this is a
100%-test-passing feature that is still only presumed correct on screen. Same
posture as the ETF Phase 2b checklist. Cannot be forced.

**Still outstanding, and the one check that is diagnostic even on a quiet
book:** 🏠 Home → Grow Today must NOT show
`🔗 (couldn't check for correlated-pair add pauses this run)`. Correlation data
is demonstrably live (the Diversification tab computed pairwise correlations
the same session), so the gate should return `{}` ("checked, clean") and render
nothing at all. That caption appearing would mean the gate received `None`
despite working correlation data — a real wiring bug in how `corr_df` reaches
it through the Home synthesis cache. **Not yet confirmed either way as of this
writing.**

**What to check on the first real firing** (all four unobserved):
1. The row names the PARTNER and the correlation, not just a reason string.
2. The "→ 📡 Signals & Advice" button appears ONLY when a partner is `named`.
3. The "🔗 Also paused here … not shown above" caption repeats no ticker
   already rendered in the Sector Hard Cap or WATCH blocks above it.
4. Home's Structural banner separates scan-liftable tickers from also-pair ones.

## 5. Known limitations, stated deliberately

- **Scope matches G-25's D5.** Rebalancer drift-ADD and Analysis's own
  add-sizing lane (the G-18 surface) are NOT covered — a ticker paused here can
  still be suggested as an ADD from either. Known and disclosed, not missed.
  Watchlist and the Diversification Advisor only suggest names not held, so they
  cannot trigger this gate.
- **Thin correlation sample under-fires this gate.** A thinned sample pulls
  correlations toward zero, so a pair can drop below danger tier for sampling
  reasons. That is the fail-open direction; F-246's coverage caption is the
  existing tripwire. No coverage threshold was added.
- **Flicker at the 0.80 boundary is not damped.** The gate flips on exactly the
  days the trim card does, so the two can never disagree; hysteresis would need
  a new constant and is out of scope.
- **A single holding produces an empty `corr_df`**, which reads as "couldn't
  check". Harmless — there is no pair to gate.
- **Locked Brief:** same disclosed limitation as G-25.
- **The cron path never produces G-26 rows** — it passes no map, and add
  suggestions are never emailed. Matches G-01 and G-25.

---

## 6. F-290 — the shared partial-coverage blind spot, now disclosed (2026-10-09)

**Shipped the day after G-26, as its own reviewed commit.** Found by G-26's Opus
review and deliberately not bundled into it, because fixing it also touches
G-25's surface.

**The defect.** `portfolio._close_series_map` silently skips any held ticker
whose history is `None`, empty, or missing a `Close` column. That ticker never
enters `corr_df`, so it can never appear in a `risk_pair`, so **both** add-gates
return "nothing found" for it — indistinguishable from "checked, and clean."
Neither gate's existing "couldn't check" caption fires, because from each
gate's point of view the check succeeded.

**The scope correction that shaped the fix.** The planner rejected the original
framing ("an unpriceable holding reads as clean"). An unpriceable ticker is
never an add candidate itself — a `None` bundle never reaches `held_data`
(`app.py` ~4765), and a bundle without `current_price` is dropped by
`build_portfolio_df` as `no_price_data` (`portfolio.py` ~493-514); both add
lanes iterate `port_df`. **The blind spot is purely partner-side:** priced,
add-eligible A sits on a ≥0.80 pair with unpriceable B, and A's add proceeds
with no disclosure.

**Fail-closed was rejected as incoherent, not merely risky.** Blocking B does
nothing (B is never an add candidate); blocking everyone who could pair with B
means one provider hiccup pauses every add in the book.

**What shipped:** `portfolio.correlation_unchecked()` (three-state) +
`pair_add_gate.unchecked_disclosure()`, produced once in
`build_correlation_bundle`, carried in the `_home_synth_cache` bundle
(`_SYNTH_SCHEMA_VER` 11→12) and the Brief as a pure passthrough, rendered as one
caption. **Neither gate's block map or return contract changed** and the Gate
Suppression Ledger gains no rows. Deliberately NOT an extension of
`correlation_coverage`, which only sees `held_data` and is therefore
structurally blind to a fully-failed load.

**Owner decision:** the caption shows whenever a gate ran and any holding was
unchecked, not only when an add is displayed.

### The review round worth remembering

Opus reviewer: **FIX-FIRST, 1 blocking — and the blocking finding was in the
TESTS, not the production code.** Both "non-suppression invariant" tests were
**vacuous**: they passed `pair_add_blocks=_pab()`, which blocks `AAA`, the
fixture's only add candidate, so both runs compared `add_positions == []`
against `[]`. The reviewer proved it by mutation — inserting
`if corr_unchecked: continue` into the add loop kills every add, and **both
tests still passed.**

Fixed by running the invariant with both gates `{}` (checked-and-clean, which is
also the real defect shape) and asserting a live add is present in BOTH runs
before comparing. **Re-mutated after the fix: the same mutation now fails three
tests.** Two further cases were added — the unchecked ticker naming the add
candidate itself (pins that a future "fix" routing unchecked tickers into a
block map would be caught), and the original blocked variant kept as a second
case with an explicit precondition assertion.

This is [[feedback_hook_enforcement]]'s own "assert the independent variable
actually varies" rule failing again, in a test written specifically to guard the
most important property of the change. **A rewritten test is not evidence until
it has been observed to fail.**

Two non-blocking findings also fixed: the caption said "couldn't be priced",
which can contradict a price shown elsewhere on Home (a ticker with an empty
history can still carry a live `current_price`) — now "its price history
couldn't be loaded this run", with a test pinning it; and an AST guard was added
for the cache-HIT restore of `_corr_unchecked`, whose deletion would be a
`NameError` on render rather than a missing caption.

### Still unverified in production

The caption has never rendered — it needs a real partial price-history failure,
which cannot be forced. Track it like §4a: a passing suite is not evidence for
anything that renders in `app.py`.

---

## 7. F-291 — persisted claims withhold (2026-10-09, commit 1 of 3)

F-290 made the blind spot visible on screen. This stops it being written into a
**durable record**. Shipped as the first of three sequenced, separately-reviewed
commits.

**The governing rule, worth stating once: screens disclose, records withhold.**
A caption is corrected by the next render; a graded baseline is not.
`portfolio_thesis._classify_correlation` persists its verdict via
`save_portfolio_thesis`, and that row is next week's HELD/SHIFTED comparison
baseline — the module's own comment already named it "the only claim in this
module that can poison a durable record", and it guarded the offline-cluster
case for exactly that reason while never guarding coverage.

**The second defect, which is why there is an `n_obs` floor.** Reproduced in the
venv with the real functions (AAA/BBB at 60 bars, CCC at 1 bar):

| step | result |
|---|---|
| `correlation_matrix` | non-empty 3×3, **entirely NaN** |
| `diversification_score` | `{'score': 50.0, 'avg_correlation': 0.0, 'risk_pairs': []}` |
| vs `DIVERSIFY_WELL_PCT` (42) | → label **"Well Diversified"** |
| `correlation_unchecked` | `[]` — every ticker IS a column |
| `correlation_coverage` | **`n_obs = 0`** |

So the app can assert "Well Diversified" from zero measurements, and a
coverage-only guard does not catch it. The cause is
`diversification_score`'s `avg_corr … else 0.0` fallback turning "no data" into
"average correlation 0.0".

**What shipped.** `portfolio.correlation_claim_verified(corr_unchecked,
corr_coverage) -> bool` — true only for an empty list/tuple AND a real `int`
`n_obs >= CORR_MIN_OBS_TRUSTED`, rejecting `bool`/`None`/`str`/NaN, never
raising. Deliberately NOT `not corr_unchecked`, which would read `None`
("couldn't check") as verified. `correlation_unchecked` was also hardened: an
empty normalized held set now returns `None` rather than a vacuously-clean `[]`.
Thesis `schema_version` 1 → 2.

**Owner decisions:** include the floor; **strict ordering** (withhold even when a
new cluster was detected, giving the one-sentence invariant *the correlation
claim is only ever persisted from a matrix that covered every holding*); keep
`CORR_MIN_OBS_TRUSTED` in `portfolio.py` as a data-quality call.

### Review

Opus reviewer **SHIP, 0 blocking**. Three non-blocking findings fixed before
commit, two of which are lessons rather than typos:

1. **The predicate's own docstring stated the inverted rule** — "a non-empty
   `corr_unchecked` is not sufficient to withhold" when the code withholds on
   exactly that. The decision function's own contract, so it fell under the
   doc-integrity rule.
2. **The type-guard tests were vacuous — the third instance of this class in
   three commits.** With the real floor at 20, `{"n_obs": True}` passes because
   `True == 1 < 20`, and `{"n_obs": "125"}` passes because the comparison raises
   a TypeError the function already swallows. **Deleting the `bool` guard left
   all seven original cases green.** Fixed by monkeypatching the floor to 1 so
   only the guard itself can reject `True`; **mutation-verified** — removing the
   guard now fails the new test while the old ones stay green.
3. **`numpy.int64` is rejected** (verified). That is the safe direction, but
   silent: a future refactor of `correlation_coverage` to return a numpy count
   would turn every weekly claim "unavailable" with no test failing. Pinned with
   a producer-side `type(...) is int` assertion.

### Known and deliberate

- **One write per ISO week.** A partial-coverage first Summary visit costs that
  whole week's correlation claim (`not_comparable` the next week). Same shape as
  the existing offline-cluster guard; a rewrite-on-recovery would mean changing
  the write guard, out of scope.
- **Historical rows cannot be distinguished retroactively** — nothing recorded
  the held set or coverage. `schema_version` 1 means coverage was unverified.
  No backfill; mutating history to guard against a rare case would discard a
  mostly-true record.
- **The thesis prose says "Correlation structure is unavailable this week."
  without a reason** (missing holding vs thin sample vs offline scan). Commit 3
  covers the on-screen side.
- **Still open, separate decision: the gate-side twin.** G-25/G-26 read that same
  all-NaN matrix as "checked, clean" (`risk_pairs == []`, no clusters), and
  F-290's caption stays silent. Fixing it at source would collapse
  `build_correlation_bundle` into its except path and change the gates'
  "couldn't check" state — it needs its own design pass. **How a 1-bar or
  non-overlapping history actually arises in production is an unverified
  hypothesis**; real `n_obs` has been 69-125.

**Commits 2 and 3 still queued:** the EOD history write (needs `held_tickers`
plumbed through `headless_alert_engine`, since `held_data` there has already
lost the failed loads and would be circular), and the three on-screen
"Well Diversified" surfaces via one shared function.

---

## 8. F-292 — the EOD history write withholds too (2026-10-09, commit 2 of 3)

Same rule as §7, applied to the daily cron: **screens disclose, records
withhold.** `risk_metric_history.build_portfolio_risk_snapshot` persisted
`avg_pairwise_corr`/`diversification_score` from whatever matrix it was given,
so a partial or thin one became an **unflagged** history row. Both are now
`NULL` when `correlation_claim_verified()` fails.

**`corr_coverage_n` is still recorded on a withheld day.** It is a true fact
about the inputs rather than a derived claim, and it is the thing that lets a
later reader distinguish "we could not measure this" from "this row is blank for
some unknown reason".

### The circularity this commit exists to defeat

In the cron, `held_data` and `port_df` have **already lost** any failed bundle
load. Deriving the expected ticker set from either is circular — it would always
report "nothing missing" while looking perfectly correct. The only honest source
is `headless_alert_engine._build_context`'s own `held_tickers`, built from the
raw `holdings_df`, which until now was a local that never left the function.
Plumbing it out through `compute_eod`'s payload is most of this commit.

The `held_tickers=` kwarg deliberately has **no `or` fallback**: an `or` would
convert a legitimate `None` into `held_data`'s keys and silently restore the
circularity.

### Operational constraints that shaped it

- **The row's key set must not change.** `db.save_portfolio_risk_snapshot`
  upserts the whole dict, so one extra key makes PostgREST reject the write,
  losing the entire day's snapshot and reddening the heartbeat.
- **A withheld reading is not a failure.** It never appends to `failures` — per
  that lane's own convention, a legitimately conditional outcome must not light
  the heartbeat red on a working day.
- **Per-metric isolation holds:** beta, top-sector and max-single-name are
  completely unaffected.

### Scope the review surfaced that the brief had missed

`_risk_row` also feeds `rec_events`, so on a withheld day
`diversify_add.metric_before` and `rec_events_readout`'s
`leg_b_avg_corr_before`/`_after` are NULL as well. All None-safe, and consistent
with "records withhold" — that readout's caption already discloses
`corr_coverage_n` at both ends precisely so a sample-size shift is never misread
as a real diversification change. Recorded rather than silently inherited.

### Review

Opus reviewer **SHIP, 0 blocking** — and it earned it by running **seven runtime
mutations** through a scratch pytest plugin rather than reading the tests. Six
were caught. The one that survived, and what was done about it:

- **M2: adding an `or list(held_data)` fallback in the cron survived every
  test**, because in all currently-reachable states the two behave identically.
  So the code comment claiming the missing `or` protected something was
  asserting an untested property. Fixed with a test that constructs the state
  the `or` would actually corrupt (a `None` `held_tickers` alongside a
  **non-empty** `held_data`); **re-mutated afterwards — M2 now fails it.**
- It also found the row-key-set test pinned a hand-written literal that would
  not fail if a column were added to the DDL. Now parsed from `db.py`'s own
  `create table` block; **mutation-verified** by adding a column to the DDL,
  which fails the new assertion while the old hardcoded test still passes.

**A process note worth keeping:** the first attempt at that DDL mutation proved
nothing — a `grep -c` returning `0` exits non-zero, which broke the `&&` chain
so the mutation never applied, and the subsequent green run looked like a pass.
A test that "passes" for a harness reason is indistinguishable from one that
passes for the real reason. Verify the mutation actually landed before trusting
either outcome.

### Still open after this commit

- **Commit 3:** the three on-screen "Well Diversified" surfaces. The 💰 Account
  Risk Trend caption also needs updating — it currently says a gap means the
  metric "couldn't be computed", which is now incomplete.
- **`CORR_MIN_OBS_TRUSTED` lives in `portfolio.py`, not `constants.py`.** It now
  decides whether a persisted cron write keeps its correlation fields, which is
  arguably Hard Rule #1 territory. Flagged by review; owner decision pending.
- **Production watch:** check the first post-deploy Railway `eod` log for the
  "correlation reading withheld" line. If it fires every day — e.g. a held
  ticker whose bundle routinely fails — the diversification history goes NULL
  indefinitely, and that would show up only in logs and chart gaps. The line
  also fires benignly on fewer-than-2-histories days.

---

## 9. F-293 — the screens disclose (2026-10-09, commit 3 of 3)

The sequence is complete: **records withhold** (§7 thesis, §8 EOD history),
**screens disclose** (here).

Three surfaces assert a diversification classification — 🔗 Risk Analysis,
🏠 Home's Diversification KPI, 🧾 Summary's 🧬 card — and none of them said when
the classification could not actually evaluate the whole book. All three now
render through two shared pure functions (`util.correlation_unchecked_note`,
`util.diversification_label_text`), so they cannot drift into three different
answers about one matrix.

**Two independent gaps, and both show when both are true:**

| state | label |
|---|---|
| all measured | `Well Diversified` |
| a holding missing from the matrix | `Well Diversified · 1 not checked` |
| sample below `CORR_MIN_OBS_TRUSTED` | `Well Diversified · sample too thin` |
| both | `Moderate · sample too thin · 2 not checked` |
| coverage unknown | unqualified — "couldn't check" belongs in the caption |

`_corr_unchecked_cache` is published on both Home paths and registered in
`coord_freshness` (`TIER_DECORATIVE`, both `SURFACE_KEYS["ra"]`/`["sm"]`).
**That registration is required in the same commit** — a key missing from the
registry reads as permanently fresh. Risk Analysis deliberately never
recomputes: its only local expected-ticker set is `list(_ra_hd.keys())`, which
is circular.

Also fixed a caption §8 had made wrong: the 💰 Account Risk Trend chart said a
gap meant the metric "couldn't be computed", which since F-292 can also mean
computed-but-withheld.

### The blocking review finding, and why it is a brief problem not a build problem

Opus reviewer **FIX-FIRST/1 → SHIP/0** on the confirmation pass.

The brief said the all-NaN case was "in scope", but the mechanism it specified —
qualify the label on `corr_unchecked` — **cannot catch that case**, because
`corr_unchecked` is `[]` in exactly that state (every ticker genuinely IS a
column while `n_obs == 0`). So Home and Summary would have shown
"Well Diversified" while Risk Analysis's existing coverage block showed
"⛔ not reliable" **from the same matrix** — the precise double-surface
contradiction the shared function exists to prevent. The build did what was
asked; the gap was in the asking.

Fixed by passing `corr_coverage` as a second input. A follow-on review note then
caught that showing only the more severe clause **hid** the "N not checked" fact
on the two screens that have no caption beneath them — so both clauses now
render. Hiding one true fact behind another is the opposite of this change's
purpose.

### The mutation that two existing AST tests missed

Review mutation-tested the wiring guards and found one survivor: moving the
collapse from the session_state write to the **local** assignment
(`_corr_unchecked = _crb["corr_unchecked"] or []`). The bare name still reaches
`session_state`, so both existing tests pass — **and `check_antipatterns` cannot
see it either**, because its sentinel rule matches a `.get()` CALL, not a
subscript.

Now pinned by a never-a-`BoolOp` guard, re-mutated to confirm it fails. Worth
recording that the first attempt at that guard asserted "the value must be a
Subscript" and failed immediately, because two legitimate sites use `.get()` —
the property that actually matters was narrower than the shape.

### Deliberately not done

- `isinstance` hardening on inputs the producer cannot emit — a test that can
  only exercise an impossible input is close to vacuous itself. Reviewer agreed.
- A `help=` tooltip naming the tickers on Home's KPI — polish, deferred per
  `feedback_ui_polish`. Reviewer agreed.

### Still unverified in production

None of the three surfaces has rendered a qualifier, and none can be forced —
it needs a real partial price-history failure or a genuinely thin sample. Real
`n_obs` has been 69-125. Track it as §4a/§6 track their own.
