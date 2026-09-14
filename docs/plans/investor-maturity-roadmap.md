# Investor Maturity Roadmap — raising confidence across Offense, Defense and Restraint

**Status 2026-09-13: A1/A2/A4 (read-only analyses) run against real production data — see
their RESULT blocks below. B1 (score-history capture) SHIPPED as F-269. B2 (gate-ledger
expansion) designed, decided, ready for implementer + reviewer, not yet built. No C1/C2
code, no D1 edit, no E1 edit.** This is a design/sequencing document, not a shipped feature
in itself — B1 is the first item to actually ship out of it.

**Owner decisions, 2026-09-13:** tiered evidence-first · risk basis = *disclose first,
decide later* · selloff gap = *make the blindness visible* · adaptation = **personalize the
interface, not the policy** · education = **contextual first, curriculum later** · strategy
alternatives = *record them, keep the build plan, and treat Track A as the test of its own
premise*.

---

## 1. Why this exists

A 2026-09-13 read-only analysis found three lanes of the app in very different evidence
positions:

- **Offense** (what to buy) — **+14.4pp alpha over 23 matured acted `new_pick`s** (live check
  2026-08-30), but not attributable to engine ranking skill vs owner selection.
- **Defense** (when to reduce) — **5 of 8** matured protective calls; has never produced a
  verdict. `scripts/exit_ladder_replay.py` (W6): **52% of 54 closed losing round trips got no
  protective signal at all**, 24% same-day only, 24% before the sale.
- **Restraint** (declining to act) — the Gate Suppression Ledger (F-259) is 3 weeks old
  against a 30-trading-day maturity horizon; **no row can have matured yet.**

**The load-bearing judgement:** only Defense is an engineering problem. Offense's gap is
attribution, answerable today from existing data with no new feature. Restraint has nothing
wrong with it structurally — it simply has no data yet, and a 30-trading-day horizon cannot
be accelerated. A plan that spent equal effort on all three would spend most effort where
effort does not change the answer.

**The honest target.** At ~7 matured acted calls/month (17 on 08-05 → 23 on 08-30), N=50 is
~4 months out and N=100 ~11; alpha variance on a ~20-name personal book will not yield
statistical proof of skill on any near-term horizon. The goal here is **high confidence in
the process, not statistical proof of the outcome**: every claim backed by its own honest
measurement, every known structural blind spot closed, and failure states visible rather
than silent. Time supplies the statistics; this roadmap supplies the instrumentation.

### 1a. §2B is unversioned, not stale — the corrected diagnosis

The initial instinct was that §2A/§2B (the investor persona, `docs/requirements.md`) had
gone stale. **The owner's reframe is the better diagnosis:** the app was built MVP-first and
extended as the investor learned — normal, correct practice. §2B isn't stale, it's
**unversioned**: nothing in the process re-reads a root assumption when a later measurement
contradicts it. That's a process property, not a design failure, and the fix (§5, item E1)
is to make the persona a tracked, evolving input rather than to "correct" it once.

**The reframe is visible in the code.** `app.py`'s `_TIPS` glossary (~line 639) holds 19
entries — every one a valuation or fundamentals metric (P/E, Forward P/E, FCF Yield, EPS,
growth, margins, D/E, ROE, short interest, ownership, analyst consensus, Sharpe, Sortino).
None cover beta, ATR stop, R:R, drawdown-from-peak, correlation, margin maintenance,
leverage, or the composite itself. The teaching layer covers what a beginner needs to pick a
stock and stops exactly where the investor has since advanced to — risk, exits, portfolio
construction, leverage. It is a fossil of the June 2026 investor.

**The structural gap this exposes:** six surfaces profile the owner's behaviour (Behavioral
Fingerprint, Investor Mirror, Self Track Record, Personalized Discovery, Decision Quality,
Lessons Learned) and every one is **awareness-only**. `personalized_discovery.py` states it
verbatim: *"Diagnostic/awareness only — never gates, re-scores, or re-ranks a
recommendation."* Correct for an MVP. It means the app can get better at markets and cannot
get better at its user — yet.

### 1b. Build order — why none of this was findable earlier

```
2026-05-05  scoring.py / risk.py / portfolio.py    — Offense exists
2026-05-26  recommendations_history.py             — grade Offense
2026-06-22  exit_advisor.py                        — Defense exists (+7wk)
2026-08-05  protective_track_record.py             — grade Defense (+6wk)
2026-08-24  margin.py                              — the loan becomes a concept at all (+16wk)
2026-08-27  gate_ledger.py                         — grade Restraint (last)
```

Every finding in this document is downstream of an instrument that already existed. §2B was
written 2026-06-02 and has not been touched since `margin.py` landed. Most individual
findings were already tracked in memory or CLAUDE.md; what was missing was **the join** —
nobody had put *defensive posture* next to *3.15x leverage* next to *52% no protective
signal* and seen one problem.

---

## 2. The premise may be wrong — read this before building anything

**This document is mostly a software plan. The alternatives best supported by the owner's
own data are allocation and behaviour changes that need little or no code.** That tension is
recorded here deliberately rather than resolved — the evidence to resolve it is exactly what
§4 Track A buys.

`project_leverage_history_capture` (memory) set a tripwire for this exact conversation,
after a real ~9–11% weekly capital loss at ~2.85x margin:

> RECOMMENDATION-tagged exits netted **+$62.80**; MANUAL "sell on dip" trades netted
> **−$180.92**. Realized P&L overall was small (−$118) — **the bulk of the capital loss was
> unrealized mark-to-market on the still-held book, amplified by leverage, not realized
> trading losses.** Before assuming "the app needs more/earlier signals," this data argues
> the existing signals were fine; the discretionary manual calls were the weaker leg.

**The actual strategy, since §2B does not name it:** as implemented, this is a **leveraged
(~3.15x), concentrated (18 names), quality-tilted, medium-horizon, long-only US equity** book
with mechanical risk overlays and discretionary execution. §2B omits the two
highest-variance terms — leverage and concentration — which is why "defensive" design
choices throughout the app are calibrated against the wrong risk.

### Six alternatives, ranked by support in the owner's own data

| # | Alternative | Evidence | Code needed |
|---|---|---|---|
| 1 | **Reduce leverage** | Strongest — the loss was leverage-amplified mark-to-market in a week SPY held above its 50-day average and VIX moved only 16→17.4 | **None** |
| 2 | **Fewer, bigger, longer** | Strong — most of 54 closed losers were held **0–10 days**; the ladder structurally needs weeks | Little |
| 3 | **Portfolio risk budget** | Strong & structural — `risk.position_sizing()` never consults the book's own beta/correlation/VaR, all of which the app already computes | Moderate |
| 4 | **Factor-first lens** | Good — 0.12 mean pairwise correlation → "Well Diversified" on an 18-name tech/semis book | Moderate |
| 5 | **Barbell / core-satellite** | Structural fit only; no data demonstrates need | Low |
| 6 | **Regime-first de-risking** | **Weakest** — the owner's own backtest says a regime nowcast would have stayed silent that week | High |

The ordering is roughly **inverse** to the software each requires.

**On institutional practice, honestly:** a firm like Goldman's returns come mostly from
financing, flow, fees and market-making — they are, structurally, the lender on the other
side of a margin position like this one. Copying "their strategy" copies the wrong business.
What genuinely transfers from institutional portfolio management is (a) factor budgeting
over stock picking, (b) a portfolio-level risk budget that position sizes fall *out of*, and
(c) an independent risk function with override authority — the app already has (c) in its
24 hard gates, where it is arguably more institutional than most retail tools. The activist
archetype's edge is **control**, not prediction, and is unavailable here. The prominent-
analyst archetype earns from prominence, not returns — the app's existing posture of
*grading* analysts (F-154c) rather than following them is already correct.

**Decisions taken:** develop **#2 now** (§4 A4 — cheapest, best-supported, and upstream of
the score-history capture, §4 B1), **#4 next** (§5 F1, Oct–Nov, awareness-only), **#3
explicitly gated on #2 and #4** (a risk budget set without knowing real holding period and
factor exposure is a guess, and it is a genuine investment-policy change), **#5 noted only**
(every gate/weight/concentration calc in the app assumes one book; two sleeves is a larger
change than it appears). **#1 is not a software decision and stays with the owner** — no
part of this roadmap should be read as a substitute for it.

---

## 3. Organizing principle: the next six months is HARVEST, not build

Do not invent a roadmap independent of this — one already exists in the data. **Policy
cannot be tuned until it can be graded, and every grader matures between October and
February.**

| When | What becomes knowable |
|---|---|
| **Now** | Defense may already have crossed `PROTECT_TRACK_MIN_CALLS` = 8 — first protective verdict ever |
| **~mid-Oct 2026** | Gate Suppression Ledger's first evaluable verdicts — what restraint costs |
| **~Dec 2026–Feb 2027** | Score history reaches 3–6mo → decay readout testable; Offense reaches N≈50 |
| **~Mar 2027** | E2 Alpha Attribution hits its 180-session floor (50 of 180 at 93% capture, 08-24) |
| **~Aug 2027** | Ledger retirement review (pre-registered; retiring is the SUCCESS condition) |

New capability should land **where an instrument is about to start producing evidence**,
never in parallel to it.

**Constraint worth naming:** options are out of scope. So the equity-only answer to downside
protection at 3.15x is necessarily *sizing + exits + cash* — no collars, no protective puts.
Defense work carries more weight here than it would in an options-enabled app; there is no
hedging escape hatch.

---

## 4. NOW — this month

### A. Evidence (read-only, no code shipped)
**Gate: §5 Tracks B/C scope is re-reviewed against these results before any build begins.**
Designing against a 3-week-old 52% figure would repeat `feedback_verify_against_live_data`.

- **A1 — Check the live Defense card** *(owner, on screen, free)*. 🧾 Summary → Engine Track
  Record → 🛡️ Defense facet. Was 5 of 8 on 2026-08-30 with ARM/APP already priced and aging;
  memory `project_engine_track_record` predicted a crossing "within roughly a week." **May
  already carry the first Defense verdict this app has produced.** Record the result there
  either way.
  **Raised in priority 2026-09-13:** a concurrent session's data-integrity pass (commit
  `fb9c695`) backfilled **21 of 32** NULL `price_at_signal` rows in `exit_signals` (13 via a
  direct `daily_snapshots` join, 8 via a genuine live fetch after confirming 5 tickers —
  AMD, FSLR, ISRG, NOW, TEAM — had zero priced EXIT/TRIM row at all). That is very likely
  enough to push the Defense facet's priced-and-mature count past
  `PROTECT_TRACK_MIN_CALLS = 8` on its own, independent of any new signal firing. **Check this
  before assuming A1 is still pending.**

  **RESULT, 2026-09-13 — RUN AGAINST REAL PRODUCTION DATA** (all 34 EXIT/TRIM
  `exit_signals` rows via one Supabase SQL pull — small enough for a single batch — run
  through the actual live functions: `protective_track_record.compute_protective_outcomes`
  → `collapse_by_ticker` → `protective_headline`, same call shape and `min_days`/`spy`
  construction as the live card at `app.py` ~12061). **The card has crossed BOTH floors, not
  just `PROTECT_TRACK_MIN_CALLS`:**

  ```
  n_mature = 17   (well past PROTECT_TRACK_MIN_CALLS=8 AND PROTECT_TRACK_FIRM_CALLS=15)
  band     = "firm"
  protect_alpha = -15.7%   (median -8.6%; robust to the single largest outlier: excluding
                             it, mean is still -9.1%)
  ```

  **This is the first Defense verdict this app has ever produced, and it is negative.**
  Recall the sign convention: positive `protect_alpha_pct` means the flagged name
  *underperformed* SPY after the warning (the caution was validated); negative means it
  *outperformed* (the call ran early, or was simply wrong). Of the 17 mature, priced,
  collapsed EXIT/TRIM calls: **only 4 (24%) validated positively; 13 (76%) came in
  negative.** The two largest: TEAM (flagged EXIT 2026-07-23 at $80.15 — the same day the
  owner's own trade log independently shows a $80.89 sell — now $179.70, a genuine +124%
  rally, protect_alpha ≈ −120.7%) and NOW (flagged 2026-07-22 at $95.46, now $132.53, +39%
  rally, protect_alpha ≈ −36.6%). Both were sanity-checked against real current prices
  before trusting the arithmetic — not a data artifact.

  **Read this precisely, per the existing badge-vocabulary discipline
  (`project_engine_track_record`): the honest label for this is "RAN EARLY," not
  "FAILED,"** and even that undersells TEAM's case — a call that has been wrong by over
  120pp for 7+ weeks and shows no sign of reverting is not usefully described as merely
  early. **This connects directly to A4's own finding**, not as a coincidence: A4 found the
  real median holding period is ~7 calendar days, while a protective call needs weeks to be
  judged right or wrong by this measurement — the same mismatch A4 flagged for the entry
  side now shows up, in the opposite direction, on the exit side: names get called for EXIT
  and then, freed of the position, run hard — precisely the shape W6 already described
  qualitatively (52% of losers got no signal at all; here, when a signal DID fire, its
  average call has so far been wrong more often than right).

  **What this changes for the roadmap, concretely:** raises the urgency (not the scope) of
  §4 B1 (score-history capture) and its future readout — this is now two independent,
  real-data findings (W6's timing miss, A1's directional miss) pointing at the same
  underlying gap: the ladder's price/trend-only mechanism may not be the right instrument
  for this holding pattern and this book. **Still evidence, not a policy change** — no gate,
  threshold or the ladder's own logic was touched by measuring this.

  **Caveats:** N=17 mature is real and past the "firm" floor by this measurement's own
  pre-registered bands, but 17 named calls is still a small sample by ordinary statistical
  standards, and could still be dominated by a handful of speculative names (TEAM, NOW,
  PLTR, MRVL are all high-beta/growth) rather than a book-wide pattern — worth re-checking
  after a market regime that isn't a persistent rally. The 3 immature rows (SHOP, COF, ON,
  all flagged within the last 4 days) are correctly excluded and will report in shortly
  regardless of any future action.
- **A2 — Offense attribution** *(new `scripts/offense_attribution.py`)*, modelled on
  `scripts/exit_ladder_replay.py`: read-only, explicit REDLINE, honest caveats printed on
  every run, falsifiable criterion pre-registered **before** the first run.
  **Question:** is +14.4pp engine *ranking* skill, or owner *selection* among its calls?
  **Method:** reuse `recommendations_history.compute_outcomes` / `distinct_missed` and
  `predictive_analytics.forward_alpha_at_horizon` — **do not write a second alpha** (F-259
  §5's rule). Bucket `new_pick` rows by composite band (65–69 / 70–74 / 75–79 / 80+), then
  report (a) does mean alpha rise with band across *all* calls (acted + skipped), (b) within
  each band, acted alpha vs skipped alpha.
  **Pre-register:** alpha rises with band AND acted ≈ skipped within band ⇒ engine skill is
  real, headline stands. Alpha flat across bands AND acted ≫ skipped within band ⇒ the
  engine is a candidate *generator* and the owner is the filter — **the Engine Track Record
  headline must be re-labelled to say so.** Print N per band; expect these to be small.
  **Caveats the script must print:** tiny per-band N; skipped alpha carries no execution
  cost; the acted set is contaminated by position sizing; survivorship applies as in W6.

  **RESULT, 2026-09-13 — RUN AGAINST REAL PRODUCTION DATA** (all 259 `new_pick` rows via 3
  manual Supabase SQL batches, plus `trigger_type` re-pulled in 3 more batches after the
  first attempt found 0 acted matches — see "process note" below). `match_recs_to_trades`
  found **29 acted / 230 skipped** of 259; `compute_outcomes` graded **239** as mature +
  priced + composite-scored (20 excluded: 3 rows with no recorded `composite_score`, the
  rest too young).

  | Band | N (all) | Lens 1 alpha (all) | acted N / alpha | skipped N / alpha | Lens 2 fwd-alpha (30d fixed) |
  |---|---|---|---|---|---|
  | 65–69 | 131 | 1.55 | 12 / **11.96** | 119 / 0.50 | 1.55 (N=81) |
  | 70–74 | 56 | −1.08 | 7 / **6.42** | 49 / −2.15 | −1.23 (N=26) |
  | 75–79 | 23 | 6.53 | 3 / **10.85** | 20 / 5.89 | 16.34 (N=11) |
  | 80+ | 1 | −15.74 | 0 / n/a | 1 / −15.74 | n/a (N=0) |

  **Alpha is NOT monotonic with band** (1.55 → −1.08 → 6.53 → −15.74) — both lenses agree on
  this shape (Lens 2, the age-confound-controlled fixed-horizon view, actually makes 75–79
  look *better*, not worse: 16.34 vs 6.53). **But acted beats skipped by a large, consistent
  margin in every populated band: +11.5pp (65–69), +8.6pp (70–74), +5.0pp (75–79).** That
  consistency — same direction, similar magnitude, across three differently-composed
  bands — is the signal, not noise from a single lucky band. Cross-check via
  `distinct_missed()`: 49 distinct missed tickers, mean alpha −3.67%, consistent with (not
  contradicting) the per-band skipped figures above. **The 80+ band is N=1 and unreadable —
  do not draw any conclusion from it.**

  **Verdict, per the pre-registered criterion, cleanly (not a borderline call): CANDIDATE
  GENERATOR, not a ranker.** The composite score, within the 65–79 range this book actually
  operates in, does not reliably separate better future performers from worse ones — what
  separates them is which of the engine's surfaced names the owner actually bought. **The
  existing +14.4pp Engine Track Record headline is real, but it is currently best read as
  crediting the owner's selection, not the composite's ranking ability** — per the
  pre-registered criterion, the card's framing should say so rather than imply the composite
  itself is what is being validated. This is evidence for that recommendation, not an
  instruction to change the card — the redline holds: no code was changed, and the actual
  card copy is the owner's call.

  **Scope limitation, important not to over-read:** this says nothing about whether the ≥65
  composite GATE itself matters — `new_pick` is already gated at ≥65 by construction, so this
  entire analysis is about differentiation *within* the population that already cleared the
  bar (mostly 65–79 in practice), not about whether clearing the bar at all is worth
  anything. The gate itself is untested by this result.

  **Caveats not yet resolved:** Lens 2 excluded **129 of 259 rows** as not-yet-matured (no
  30-trading-day window yet), so it draws from an older subset than Lens 1 — the two lenses
  agree in direction here, but that agreement is itself informative and shouldn't be assumed
  to hold automatically as more data accrues; acted rows are still contaminated by position
  sizing (a call sized bigger isn't necessarily a *better* call); survivorship applies as in
  W6 (only priced, tradeable tickers are counted).

  **Process note, for future re-runs:** the first attempt against real data returned 0 acted
  matches out of 259 — a red flag, since memory already recorded 23 acted `new_pick`s as of
  2026-08-30. Root cause: the `trades` pull for A4 only selected `id, ticker, action, shares,
  price, traded_at`, omitting `trigger_type`, which `match_recs_to_trades` requires to detect
  an acted-on pick. Fixed by a second, targeted `trigger_type`-only pull merged onto the
  existing trades set — a genuine miss on this session's part, caught by sanity-checking the
  intermediate matched count before trusting a printed result, not by the code itself.
- **A3 — Re-run `scripts/exit_ladder_replay.py`** for a current number on 52/24/24. Needs
  `SUPABASE_URL` / `SUPABASE_KEY` from Railway; run from a normal shell (its own docstring:
  the Railway console shell is not usable for this).
- **A4 — Holding-period / entry-discipline analysis** *(alternative #2 — run this FIRST of
  the four)*. Nearly free: `investor_mirror.build_closed_lots()` already returns
  FIFO-matched, SPLIT-aware rows carrying `ticker, buy_date, sell_date, shares, buy_price,
  sell_price, days_held, pnl_pct, pnl_abs, is_gain` from `trades_df` alone. Same read-only
  script shape as A2.
  **Report:** holding-period distribution, split by outcome (win/loss) and by position size.
  Optionally cross-reference `self_track_record.classify_sells()` for an app-aligned vs
  self-initiated split — but note its reliable window (`SELF_TRACK_SELL_RELIABLE_LOG_START =
  2026-07-21`) is much shorter than the full trade history, so that cut must be reported
  separately and dated, not blended into the primary holding-period distribution.
  **The question:** is the 0–10 day concentration a real strategy/design mismatch, or an
  artefact of small/deliberately-short trades?
  **Why it runs first:** the deterioration ladder needs 2-of-3 sessions below SMA50 plus a
  drawdown from peak — it needs *weeks*. If the real holding period is days, **the app's
  entire Defense apparatus is calibrated for a book that isn't being run** — the same class
  of mismatch as the leverage one. **This is upstream of B1.**

  **RESULT, 2026-09-13 — RUN AGAINST REAL PRODUCTION DATA (all 256 trade rows, manual
  Supabase SQL pull, 2026-05-27 through 2026-09-11).** `investor_mirror.build_closed_lots()`
  produced **139 completed round-trip lot fragments** (59 wins, 80 losses) — the full trade
  history, not a losers-only slice like W6.

  | Holding period | N | % |
  |---|---|---|
  | 0–3d | 34 | 24.5% |
  | 4–10d | 54 | 38.8% |
  | 11–20d | 25 | 18.0% |
  | 21–45d | 24 | 17.3% |
  | 46–90d | 2 | 1.4% |
  | 91d+ | 0 | 0.0% |

  **Median holding period: 7 calendar days. 63.3% of ALL 139 fragments (wins + losses) closed
  within 10 days.** By outcome: WIN median 12d (N=59), LOSS median 6d (N=80) —
  `investor_mirror.disposition_effect()` (reused, not re-derived): winners held 13.5d avg
  (share-weighted), losers 9.4d avg, ratio 0.69. **Losers are held SHORTER than winners here**
  — the opposite of the textbook disposition effect (holding losers too long) — so this is
  not that particular bias. By position size (dollar-exposure terciles): Small ($174–$825)
  median 7d, Medium ($835–$1,090) median 8d, Large ($1,100–$3,500) median 6d — **flat across
  sizes**, which argues against the "artefact of small, deliberately-short trades" reading:
  if short holds were concentrated in small toe-dip positions and large convictions were held
  longer, that pattern would show up here, and it doesn't.

  **Answer to A4's own question: this reads as a genuine strategy/design mismatch, not an
  artefact.** A median holding period of ~5 trading sessions (7 calendar days, roughly) is
  well short of what the deterioration ladder needs to even confirm a TRIM
  (`DETERIORATION_CONFIRM_DAYS`/`DETERIORATION_CONFIRM_REQUIRED` = 2-of-3 sessions) once a
  drawdown from peak has separately had time to develop — and the flat position-size cut
  rules out the most obvious alternative explanation. **This is now the strongest
  confirmation yet of the roadmap's own load-bearing worry: the app's weeks-scale Defense
  apparatus may be calibrated for a book that isn't the one actually being run.**

  **Consequence for B1 (§4 B, score-history capture):** per this item's own sequencing rule,
  **B1's capture may still start** (cheap, forward-only, loses nothing by starting the
  clock) — but **B1's future READOUT design must now explicitly confront this number**,
  not treat it as a remote caveat. A decay signal that needs weeks to show a trend is
  measuring a timescale this account mostly doesn't hold into. The readout design, when it
  is taken up (Dec–Feb per §6), should open by re-reading this result rather than assuming
  the original weeks-scale framing still fits.

  **Caveats carried forward, not resolved:** (1) `days_held` is CALENDAR days, not trading
  sessions — a 7-day calendar median is closer to ~5 trading sessions, a real but smaller
  gap against the ladder's session-based confirmation than the raw number alone suggests;
  (2) this is one account's ~3.5-month window (2026-05-27 to 2026-09-11) — real, but not
  proof the pattern is permanent; (3) the optional self-track (`classify_sells`) app-aligned
  vs self-initiated cut was **not run** — `exit_signals` data was not pulled this pass — so
  whether the short holds are disproportionately app-driven (stop-outs, mechanical exits) or
  self-initiated remains open; worth a follow-up pull if this number is acted on.

  **This is evidence, not a recommendation** — per this script's own redline, no gate,
  threshold or the ladder's confirmation window was touched. What to do with this finding
  (shorten the ladder's confirmation window, reconsider holding-period discipline, or treat
  it as expected/fine) is the owner's call, not one this analysis makes for them.

### B. Start the clocks (build — delay loses data permanently)
Both are **capture-only**: no readout, no card, no gate — the F-259 sequencing precedent.
Both touch `_GATE_FILES` members ⇒ **`planner` design pass + mandatory Opus `reviewer`**,
cited per Hard Rule #4.

- **B1 — Score history capture. SHIPPED 2026-09-13 as F-269.** Plan:
  `docs/plans/score-history-capture.md` (§1a design, now Status: SHIPPED). Opus `planner`
  PROCEED WITH CHANGES → `implementer` build → Opus `reviewer` SHIP/0-blocking. New
  `score_history` table, one row per held ticker per day, written from the `premarket`
  lane, zero extra API calls. **DDL still needs to be applied by hand** before the cron lane
  stops logging write failures — that is the one remaining step, not a design gap.
  Capture-only, as designed: no readout, no card, no gate reads this table yet.
  **Three rules that must not be lost:** (1) persist `val_score` / `bq_score` as **NULL,
  never the fabricated 50** — the 2026-09-13 analyst-weight fix *widened* when
  `val_available` goes False, so this fires more often than the original plan assumed;
  (2) **no coalesce-on-write**, deliberately unlike `save_exit_signals_batch` — NULL means
  "not measurable," which *is* the information; (3) **skip `stale_as_of` bundles**
  (precedent: `headless_alert_engine.py:268-274`) or cache staleness reads as stability. DDL
  before the first cron run.
  **Sequencing against A4 — run A4 first.** Composite decay manifests over *weeks*, the same
  timescale the ladder needs. If A4 confirms a days-long real holding period, the eventual
  decay **readout** may be answering a question that doesn't apply to this book. A4 does not
  block the **capture** itself (cheap, forward-only — starting the clock still wins), but
  **B1's readout design must not be committed to before A4 reports.**
- **B2 — Expand the Gate Suppression Ledger** to 5 gates currently uncaptured: **G-02**
  (Rebalancer ADD), **G-05** (sector → Watchlist downgrade), **G-06** (beta → downgrade),
  **G-13** (R:R → downgrade), **G-18** (breached stop blocks an Analysis add). **This is a
  scope boundary, not an oversight** — `gate-suppression-ledger.md` deliberately scoped
  itself to "the nine suppression sites" inside `_grow_today` only.
  **Opus `planner` design pass complete 2026-09-13: PROCEED, 2 owner decisions pending —
  full spec in `gate-suppression-ledger.md` §8.** Bigger than originally scoped in one real
  way, found by the pass, not assumed: `gate_ledger.build_suppression_rows` reads only
  `grow_today`, and **none of these 5 gates flow through it** (they live in
  `watchlist_advisor.py`, `rebalancer.py`, and inline in `app.py`'s Analysis page) — so this
  is 3 new pure builders + 3 new interactive write sites, not an extension of the existing
  function (which stays untouched). `db.py` needs zero changes. Resolved: `counterfactual =
  True` at all 5 sites (mirrors G-01's own precedent); two new `lane` values,
  `"downgrade"` (G-05/06/13) and `"add_suppressed"` (G-02/G-18), so the readout can never
  blend a downgrade's alpha with a true suppression's; no readout code change needed now.
  **Both owner decisions made 2026-09-13, both per the planner's recommendation** (§8d):
  (1) accept the 5 new "building" cards auto-appearing on the existing ledger readout page;
  (2) G-06's compound gate_value stores the ticker-beta leg, portfolio-beta narrated in text
  only. **Fully unblocked — ready for `implementer` + mandatory Opus `reviewer`** (confirmed
  `watchlist_advisor.py` is a `_GATE_FILES` member).

### D. Education — contextual, phase 1
Decides nothing ⇒ sits entirely outside the `_GATE_FILES` / mandatory-review machinery.
Cheapest and lowest-risk capability class in the app, and currently the thinnest.

- **D1 — Extend the glossary past its MVP boundary.** Add the ~12 risk/exit/leverage
  concepts `_TIPS` never covered: beta, ATR stop, R:R, drawdown-from-peak, SMA50 trend
  break, correlation, diversification score, margin maintenance, leverage ×, margin cushion
  / call distance, composite pillars, entry zone. Same house style (bands + "⚠" caveat +
  "Learn more"). Pure additive dict entries; no reviewer needed.

### E. Living persona
- **E1 — Make §2B versioned, not corrected.** Add a dated "investor state" note to §2B
  recording that the owner runs deliberate leverage (~3.15x, measured 2026-08-23) and that
  §2B's fail-safe posture was written 2026-06-02 for an unlevered book — so every future
  reader sees the tension rather than inheriting the assumption. **Add an explicit re-read
  trigger:** a new measurement that contradicts a §2B premise forces a §2B review. That
  trigger is the actual fix for the unversioned-assumption problem this whole document is
  about. Docs-only, no policy change, no reviewer needed.

---

## 5. OCT–NOV — visibility, and the first harvest

- **C1 — Make the risk-off blindness visible.** `exit_advisor.risk_off_regime` "degrades to
  not-tripped on missing/short data," so **a missing VIX/SPY read renders identically to a
  measured calm market.** Expose a **third state** so callers can render *"market regime not
  evaluated — VIX/SPY unavailable"* instead of nothing. Copy the `util.factor_tilt_state` /
  `factor_tilt_evidence_line` shape (2026-08-28): one classifier every consumer reads, three
  states — *not measured / measured-but-unusable / measured*.
  **Redline: awareness only — must never arm a trim on absent data**, which would invert the
  current, correct fail-safe. `exit_advisor.py` ⇒ mandatory Opus review.
- **C2 — Capital-equivalent risk disclosure.** No behaviour change, no gate, no constant.
  `RISK_PCT_PER_TRADE` keeps multiplying the gross book (`risk.py`'s `position_sizing`); we
  show what that means in capital terms beside it, so the ~4.7%-of-capital reality at
  current leverage is visible before any policy decision. Decision goes in a **pure
  function** (e.g. `risk.capital_equivalent_risk(...)`) returning `None` when net capital is
  unknown. **The caption must distinguish "not levered" from "leverage unknown"**
  (`feedback_sentinel_is_present`) — collapsing those prints a reassuring number built on
  absent data. Wire into the five existing F-255 sizing surfaces; reuse
  `margin.capital_basis_weight()` and the net capital already resolved once per Home render.
  `risk.py` ⇒ mandatory Opus review.
- **D2 — Gates that argue.** Once the ledger produces verdicts, gate banners gain a *why
  this rule exists* line and, where evaluable, *what it has actually done*. Turns 24 hard
  suppressions into 24 teachable moments. **Naturally gated on A/B harvest** — build when
  there is a record to cite, not before.
- **F1 — Factor-first lens** *(alternative #4, awareness-only)*. Promote factor exposure
  from a button-gated panel to a standing dimension: *"your real bet is momentum + tech
  beta, not 18 tickers."* The 0.12 mean pairwise correlation is a **sound** measurement
  (n_obs = 125, verified 2026-08-21) that is nonetheless **substantively misleading** —
  correlation of daily returns is not shared factor exposure, so "Well Diversified" on an
  18-name tech/semis book can be arithmetically true and wrong. Reuse
  `util.factor_tilt_state` / `factor_tilt_evidence_line`, which already carry the three-state
  contract. **Never gates.**
- **E2 — First §2B re-read against evidence**, using the ledger's first verdicts and A4.

---

## 6. DEC–FEB — the graders pay out

- **Score-history readout** (separately approved, as B1 requires): does composite decay
  precede price breaks? Strongest candidate for a genuinely *new* protective input, and the
  direct answer to the 52% finding.
- **Re-run A2** at N≈50. If the generator-vs-ranker verdict has firmed, act on it.
- **D3 — Lessons from your own book.** Surface findings no course can give the owner:
  signal-driven exits netted **+$62.80** vs discretionary dip-sells **−$180.92**; 52% of
  closed losers got no signal; most were held 0–10 days. Feeds the existing Lessons Learned
  (F-195) surface, which today is user-authored only.
- **D4 — Personalized leverage module.** F-266's `account_daily_snapshots` will have ~3–5
  months of real history by then. Cannot be built well before that — it shipped 2026-09-10
  and is forward-only, which is independently why "contextual first, curriculum later" was
  the only buildable choice now.
- **Policy tuning becomes possible** — via ledger evidence only (owner's steer), never via
  behavioural profile.
- **Portfolio risk budget (#3) becomes decidable, not before.** Gated on A4 and F1: a vol or
  drawdown budget set without knowing the real holding period and real factor exposure is a
  guess. If taken up, it is a genuine investment-policy change (Hard Rule #1) touching
  `risk.position_sizing()` and every sizing surface ⇒ `planner` + mandatory Opus `reviewer`.

---

## 7. What to expect — anticipated, so it isn't a surprise

- **A ledger verdict may say a gate costs more than it saves.** That is the first real
  evidence-backed policy decision this app has ever been able to make. Treat it as a normal
  `planner` + `reviewer` policy change, not an emergency.
- **The Defense verdict may be negative** ("RAN EARLY" / not validated). Bigger conversation
  than a fix; the honest labelling already exists to carry it.
- **A2 may say the engine is a generator, not a ranker.** Then the headline re-labels, and
  emphasis shifts from "trust the pick" to "trust the shortlist."
- **Further maturity will likely produce an override/policy-visibility ask** — seeing a
  threshold, its record, and proposing a change from inside the app. Out of scope now;
  expect it. ⚙️ App Settings (F-262) governs reference data, not policy constants.
- **Options may come into scope later.** Currently excluded; several Defense limitations
  have option-shaped answers, so record the boundary rather than re-deriving it each time.

---

## 8. Redlines — what this roadmap deliberately does NOT do

- **No threshold, gate, scoring or recommendation changes.** All §5 items are
  disclosure-only by decision; all §4 B items are capture-only.
- **No policy personalization.** Behavioural profiles never relax a gate — ~50 trades is far
  too small a sample, and the failure mode is protections quietly disappearing on exactly
  the names where habits are worst.
- **It does not make Offense statistically proven.** The target is high confidence in the
  *process*, not proof the book cannot produce.
- **It does not close the 52% finding.** B1 only starts recording what a future readout
  could act on.
- **It does not produce a Restraint verdict before ~mid-October.** Nothing can.
- **It does not decide the leverage level (#1).** That is the alternative the owner's own
  data supports most strongly and it requires no software; it stays with the owner. **Watch
  for the failure mode this roadmap invites:** building more measurement can become a way of
  deferring an allocation decision that is available today, while the score-history readout
  is four months out.

---

## 9. Sequencing note — this session

Design/documentation only, per owner instruction 2026-09-13: a separate concurrent session
was actively fixing earlier data-integrity findings in `app.py` / `stock_analyzer/util.py` /
`stock_analyzer/portfolio.py`. **No code from this roadmap (A2/A4 scripts, B1/B2, C1/C2,
D1, E1) was started until that session's changes were committed** and this roadmap was
explicitly kicked off. This document and its companion memory file
(`project_investor_maturity_roadmap`) are the durable record.

**Gate cleared 2026-09-13 — reviewed and incorporated, same day.** The concurrent session
landed 6 commits (`6b9d0fd`..`aee98c3`, all Opus-reviewed where required, all pushed to
`origin/main`) before A2/A4 were run against real data. None invalidate either result:

- **`fb9c695`** (exit_signals backfill) is now folded into A1 above — it likely moves the
  Defense facet past its evaluability floor independent of any new signal.
- **`aee98c3`** (withhold deterioration signals on an unconfirmed split) cannot have affected
  W6 or A4's numbers — the commit message confirms zero SPLIT rows have ever existed in
  `trades`, so the bug it fixes has never fired historically. It's also a useful precedent
  for this roadmap's own C1 item: same "withhold and disclose, never silently apply wrong
  data" shape this plan already calls for on the risk-off blindness.
  - **`2fbc0ce`** / **`ebef008`** (portfolio no-price disclosure; trade-journal write-outcome
  honesty) touch `app.py`/`portfolio.py` display and write-confirmation paths neither A2 nor
  A4 reads from — no effect on either result.
- **`6b9d0fd`** (price cross-check false-alarm fix outside trading hours) is a display-layer
  fix, no effect on either result.
- **A genuinely relevant question was checked and closed, not assumed:** the other session's
  own backlog (`docs/plans/data-integrity.md`, finding D8) flagged that some `new_pick`
  sub-pillar breakdown columns (`t_score`, `bq_score`, `val_score`) can come from a
  lightweight cached lookup rather than the full bundle. Traced directly in `app.py` (~5884
  vs ~5933): the `new_pick` writer's **`composite_score`** field — the only field A2's
  banding used — is read straight off the Grow Today engine's own live computation
  (`_p.get("composite_score")`), not the lightweight fallback. D8's concern is scoped to the
  sub-pillar columns A2 never touched. **A2's verdict is unaffected; no caveat needed.**

---

## 10. Verification (for when execution starts)

- **A** produces reports, not code; record outcomes in the relevant memory files
  (`project_engine_track_record`, `project_forward_portfolio_simulator`).
- **B1:** DDL applied before the first cron run; after the first `premarket` firing, confirm
  unavailable pillars persist as **NULL, not 50**.
- **B2:** `tests/test_gate_registry.py` green; confirm new ids appear in real
  `gate_suppressions` writes from both `source='cron'` and `source='app'`, with non-null
  `price` and `composite_score`.
- **C1/C2/D1:** full suite + `check_antipatterns.py` + `check_constants_documented.py`
  green. **C1, C2 and D1 all need a screenshot check** — per
  `feedback_streamlit_renderer_mismatch` this class is invisible to tests *and* review.
- **Deploy:** push to `main`, ~2 min Railway redeploy, Ctrl+F5 at `drishta.up.railway.app`.
  Never run locally.
- **Reviews:** every §4 B and §5 C commit touches a `_GATE_FILES` member or a DB-write path
  ⇒ Opus `reviewer` cited per Hard Rule #4; `feat(` commits need `Design =` / `Build =`
  trailers. **Verify the `pre_tool_checks.py` hook actually fired** — the 2026-09-09 app
  review found it silently not running in a VSCode-extension session, and two `feat(`
  commits shipped without trailers as a result.

## 11. Doc sync (Definition of Done — all 7 steps, when execution starts)

- **No new constants** in any item as scoped. If that changes, step 1 applies and
  `check_constants_documented.py` enforces it.
- New F-IDs in `docs/requirements.md` for C1, C2, D1; **§2A.3 rows for the 5 new gate ids**
  (anti-rot test enforced).
- **§2B gains its dated investor-state note and its re-read trigger (E1).**
- This plan's own **Status** line bumped at each stage; also bump
  `docs/plans/score-history-capture.md` and `docs/plans/gate-suppression-ledger.md`.
- `docs/shipped-log.md`, `CLAUDE.md` "What's queued", in-app 📖 User Guide (D1 changes
  user-visible text), memory files.

## 12. Diary dates

- **~mid-Oct 2026** — earliest evaluable Suppression Ledger verdicts.
- **~Mar 2027** — E2 Alpha Attribution reaches its 180-session floor.
- **~2027-08-27** — Ledger retirement review. **Retiring is the SUCCESS condition. Do not
  soften it.**
