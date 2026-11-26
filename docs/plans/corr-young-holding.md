# Correlation with a young holding (CorrYoung)

**Status:** ANALYSED 2026-11-25 (lead, from an owner screenshot of 🔗 Risk Analysis). Not designed, nothing built. **Owner 2026-11-25: pick it up in a new session after RiskListwiseYoung C4 is finished** (`docs/plans/risk-young-holding.md`). Next step: the owner answers D1–D4 (§5), then planner → implementer → reviewer (gate files). **Owner decisions 2026-11-25 (after RiskListwiseYoung C4 shipped; lead verified §3's code claims at HEAD `f081d926`; the "toward zero" sentence is now at app.py ~19072):** **D1 = leave a young holding out, named** (fewer than `BETA_MIN_RETURNS` returns; the rest measured on their real shared history); **D2 = (b) keep the add-pause on a thin sample but word it "correlation couldn't be measured", never "these move together"** (the lead's (a) loosened a buy gate; (b) matches the owner's 2026-11-25 entry-fail-closed U1 "a buy check that couldn't run holds the buy" and the A-H2 rationale in `pair_add_gate.py`); **D3 = an unverified diversification score shows "Check Incomplete"** on 🏠 Home instead of escalating; danger alerts still escalate; **D4 = leave cluster chaining as is**. DESIGNED 2026-11-25 (§7: Opus planner PROCEED WITH CHANGES; owner took Q1 = B, Q2 hold own add, Q3 pairwise, Q4 trusted-sample rule). **C1 BUILT 2026-11-25** (implementer `claude-sonnet-5-5`; Opus SHIP 0 blocking, local commit only, NOT pushed alone: C1 by itself mislabels ANT's reason, leaves its adds unpaused and makes the Risk Analysis caption false): `risk_window.classify_holdings`, new gate module `corr_window.py`, `portfolio.correlation_matrix` / `correlation_coverage` delegate; every production `held_data` frame has a DatetimeIndex (reviewer traced all providers + the cache). **C1 review: what later chunks must cover.** C2: notes read `coverage["left_out"]` for reasons (ANT "only N daily returns (20 needed)", not "price history couldn't be loaded": `util.correlation_unchecked_note`, `pair_add_gate.unchecked_disclosure`); an empty matrix with `left_out` (fewer than 2 counted) names the left-out holdings instead of "unknown this session"; `util.correlation_record_withheld_note` cap clause rebuilt from `left_out`; tolerate a memoised pre-C1 coverage dict (no `left_out` / `n_obs_max`); disclose the left-out name beside Portfolio Health's diversification sub-score (now shown on a trusted rest-of-book reading). C3: Q4 in `classify_risk_banner` + the producer withholding `div_score` on thin (closes the C1-only path where a thin subset score escalates to Monitor, home_risk_synthesis.py ~388-391); block `"sample"` tagging global or per-pair (n_obs is a book-wide minimum; per-pair needs a `pair_n_obs` map); `risk_metric_history` `corr_left_out`. C3b: the Q2 hold keys off `coverage["left_out"]`, not `corr_unchecked` (None when fewer than 2 counted). C4: rewrite app.py ~19052-19063 ("shared by all N of your M", "shortest … caps the sample") and ~20950 for the new n_obs meaning; remove "toward zero" (~19071); `_SYNTH_SCHEMA_VER` 29. C5: dated note on `corr_coverage_n` (db.py:958, architecture, Performance Review start→end); `_close_series_map` now has no production caller (delete or fix comments at pair_add_gate.py ~292 / ~357, portfolio_thesis.py ~92); the coverage docstring calls it "awareness" though n_obs feeds gates; `corr_window` imports private `risk_window._weight_pct` (make public); CLAUDE.md `_GATE_FILES` count (113). NEXT: C2.

Line numbers below are at HEAD `f7ee4564`; re-check them before building (app.py moves).

## 1. What the owner saw (2026-11-25)

- 🧩 Portfolio Intelligence → Correlation Clusters: "🕸️ 9 positions move together: AAPL, ABBV, AMZN, ANT, DELL, REGN, SNOW, SPCX, TGT — avg internal correlation 0.41, 84.8% combined weight", with no warning about the sample.
- 🔗 Risk Analysis → Dashboard: the ⛔ box "Computed on 4 daily returns shared by all 11 of your 11 holdings with usable price history — shortest is ANT (5 bars vs 129 for the longest) … not reliable. A thin sample pushes correlations toward zero …". Directly under it, 15 "These positions move together" cards (10 red, 5 yellow), e.g. REGN × TGT 0.99 (healthcare vs consumer staples). Diversification score 36/100 "Moderate", avg correlation 0.28.

## 2. Evidence (lead, read-only script, Yahoo daily closes, 2026-11-25)

The app's 4 shared returns are 11-20, 11-23, 11-24 and 11-25. Two of them were big market-wide days (11-20 nearly everything down, DELL −11.0%; 11-23 everything up, DELL +20.5%).

| Pair | App (4 returns) | ~6 months, ANT left out (115 returns) | Last 60 days |
|---|---|---|---|
| REGN × TGT | 0.99 | 0.27 | 0.29 |
| AMZN × SNOW | 0.98 | 0.32 | 0.46 |
| ABBV × TGT | 0.93 | 0.21 | 0.32 |
| DELL × SPCX | 0.93 | 0.34 | 0.54 |
| AAPL × SNOW | 0.88 | 0.15 | 0.24 |
| ABBV × REGN | 0.88 | 0.30 | 0.32 |
| DELL × SNOW | 0.87 | 0.42 | 0.49 |
| AMZN × DELL | 0.83 | 0.17 | 0.33 |
| SNOW × SPCX | 0.81 | 0.37 | 0.53 |
| AAPL × AMZN | 0.80 | −0.04 | 0.26 |
| AMZN × SPCX | 0.70 | 0.26 | 0.44 |
| AAPL × REGN | 0.67 | 0.04 | −0.03 |
| AAPL × SPCX | 0.66 | 0.12 | 0.10 |

Over ~6 months, the 28 pairs of the 8 long-history names in that script: **0 pairs ≥ 0.65**, average 0.09, highest DELL × SNOW 0.42. (The book's other 2 holdings were not in the script.) **None of the 15 flagged pairs is real.**

Chance alone (lead simulation, 20,000 draws of two independent normal series):

| Shared returns | P(r ≥ 0.65) | P(r ≥ 0.80) | Expected false pairs of 55 (11 holdings), ≥ 0.65 / ≥ 0.80 |
|---|---|---|---|
| 4 | 0.177 | 0.102 | 9.7 / 5.6 |
| 8 | 0.040 | 0.008 | 2.2 / 0.4 |
| 12 | 0.012 | 0.001 | 0.6 / 0.0 |
| 19 | 0.001 | 0.000 | 0.1 / 0.0 |
| 20 | 0.001 | 0.000 | 0.0 / 0.0 |

At 4 returns the chance distribution of r is close to flat on −1..1. The observed 15 / 10 is about what chance plus two shared market days predicts.

## 3. Root cause (three layers; ANT is only the trigger)

1. **Trigger: one holding with very few price days.** Today ANT (5 bars). The same happens with any recently listed holding (SPCX has 116 bars, so it would have done this if held in its first weeks) or with one holding's price download coming back short on a bad provider day (the code's own note, `portfolio.py:1578-1585`).
2. **Amplifier: listwise cut.** `portfolio.correlation_matrix` (`portfolio.py:1575`) does `pd.DataFrame(series).dropna()` (:1590), so every one of the 55 pairs is measured on the youngest holding's few days.
3. **Missing guard, built on a wrong premise.** The app assumes "a thin sample pushes correlations toward zero" (printed on screen, app.py:19011-19012). That is true only at 0 shared days, where the matrix is all-NaN and `diversification_score` falls back to 0.0 / score 50. With 1–10 days it is the opposite: noise in both directions, often high. The owner-ratified rule "escalation is honest evidence even from a partial / unverified matrix; only All Clear needs a verified one" (`home_risk_synthesis.py:357-375`; `pair_add_gate.py:165-187`, audit A-H2) rests on that premise.
4. Secondary (not a cause): `portfolio_intelligence.correlation_clusters` (`portfolio_intelligence.py:68-100`) links names step by step (single linkage), which is how 9 names from different industries became one cluster averaging 0.41.

Same class as RiskListwiseYoung (book risk series cut to the youngest holding), fixed by `risk_window.py` with the `BETA_MIN_RETURNS` = 20 rule.

## 4. Blast radius (what a thin sample does today)

| Surface | Today | Direction |
|---|---|---|
| 🔗 Risk Analysis pairs list (app.py:19035-19043) and heatmap | Lists every chance pair as "These positions move together" under the ⛔ box | False information |
| 🧩 Correlation Clusters (app.py:21321-21331) | Shows the cluster with no sample warning (the sample is checked only on the no-cluster branch, :21310) | False information, undisclosed |
| G-25 pair add-pause (`pair_add_gate.add_block_map`, :188) and the new-cluster add-pause (`cluster_add_gate.resolve_new_clusters` :108, `add_block_map` :198) | A map that fired is **kept** on a thin sample (only an empty map becomes "couldn't check"), so adds are paused on chance pairs | Cautious (missed adds, never a sell); Grow Today was in Protect Mode on 11-25 |
| 🏠 Home risk banner (`home_risk_synthesis.classify_risk_banner`, :381-384) | An unverified diversification score below `DIVERSIFY_WELL_PCT` (42) escalates to "Monitor" (36 on 11-25) | False alarm |
| Diversification score tile on Risk Analysis | 36/100 "Moderate" shown as a number | False information (disclosed by the ⛔ box) |
| Already safe | PAIR_RISK trims are withheld on a thin sample (`PAIR_RISK_WITHHELD`); `risk_metric_history` blanks `avg_pairwise_corr` / `diversification_score` via `correlation_claim_verified` (:154-168); the Structural Scan baseline save is refused (`cluster_add_gate.baseline_write_allowed`); Portfolio Health's diversification sub-score is withheld; no cron / email reads the matrix | — |

The bug-register CorrYoung row said before this analysis that "G-25 / G-26 read 'couldn't check'"; that holds only when nothing fires.

**If nothing changes:** ANT reaches 20 returns ≈ 12-17 and this stops by itself, then recurs with the next recently listed holding or a short download. The cost is false screens and random add-pauses for ≈ 3 weeks per occurrence, plus the trust cost of confident false "danger" pairs.

## 5. Fix options and owner decisions (open)

- **D1. Leave young holdings out of the matrix (lead recommends).** Same rule as RiskListwiseYoung: a holding with fewer than `BETA_MIN_RETURNS` (20) returns is left out of `correlation_matrix`, named ("ANT: not measured yet, 4 of 20 returns"), and the rest are measured on their real shared history. Precedent: `monte_carlo.build_return_matrix`, `risk_window.book_returns`. Alternative: each pair on its own overlapping dates (bigger change; the matrix stops being internally consistent). Planner sub-question: does a left-out, disclosed young holding still make `correlation_claim_verified` False (records withheld until ≈ 12-17), and how do `correlation_unchecked` / G-25's "couldn't check" disclosure treat it?
- **D2. Add-pauses on a thin sample** (after D1, mostly a data-failure case): (a) thin → "couldn't check", existing caption, no pause on chance pairs, single-name / sector ceilings unaffected (lead recommends; loosens a gate, owner's call); (b) keep the pause but word it "correlation couldn't be measured", never "these move together".
- **D3. Home banner:** an unverified diversification score shows "Check Incomplete" instead of escalating to "Monitor" / "Action Required" (lead recommends); danger alerts still escalate.
- **D4. Cluster chaining:** leave as is (lead recommends); with real data no pair crosses 0.65.

Option weighed 2026-11-25 and not taken: a small display-only fix first (thin sample → no pair cards / clusters on Risk Analysis and 🧩 Clusters + wording fix, no gate files). The owner chose to do the whole item in one pass after RiskListwiseYoung C4.

## 6. Build outline (for the planner to confirm)

- **C1** D1 in the pure matrix code (`portfolio.py`, gate file) + coverage naming the left-out holding.
- **C2** one pure "can this correlation reading be shown" decision read by every surface in §4 (thin → one box, no pair cards, clusters or score).
- **C3** D2 + D3 in `pair_add_gate.py`, `cluster_add_gate.py`, `home_risk_synthesis.py` (gate files → reviewer).
- **C4** replace the "pushes correlations toward zero" sentence (app.py:19011-19012) with a true one, e.g. "A sample this small makes correlations unreliable in both directions: on 4 days, two unrelated stocks read above 0.65 about 1 time in 6."; User Guide; requirements F-ID; architecture; bug register; this plan's Status line.

Tests must include: a young holding with 1, 4, 19 and 20 returns; an all-NaN matrix (the 0-day case the old premise came from); a data-failure short download; a pair that is genuinely ≥ 0.80 over the long window staying flagged after D1.

## 7. Design (Opus planner `claude-opus-5-5`, 2026-11-25: PROCEED WITH CHANGES) and owner answers

Planner checked every claim at HEAD `78ded044`; lead re-checked the headline ones (both thresholds 20: `BETA_MIN_RETURNS` constants.py:36, `CORR_MIN_OBS_TRUSTED` :296; listwise cuts portfolio.py :1590 / :1621; `classify_risk_banner` home_risk_synthesis.py :335; `_add_block_gate` daily_briefing.py :1118; the "toward zero" premise also in requirements F-246 (:317) and architecture (~3070)).

**Planner's changes to the owner's D1–D4:**
1. Stop the listwise cut among the COUNTED holdings too: literal D1 would measure all 55 pairs on ANT's 20 days the day it counts (on 20 returns a real ≈ 0.45 pair reads ≥ 0.65 about 1 time in 9). → Q3.
2. D3 keys on the SAMPLE, not on `correlation_claim_verified` (a trusted rest-of-book score with ANT left out must still escalate). → Q4.
3. The per-holding cut is `CORR_MIN_OBS_TRUSTED` (a counted holding must never make the sample thin by itself); same value as `BETA_MIN_RETURNS` today; tripwire test that they are equal.
4. Thin-sample pauses (D2b) are "a check couldn't run" holds: not ledgered as G-25 / G-26, and bind after ENTRY (entry-fail-closed Q5 and its planner change (b)).
5. Hold the left-out holding's own add. → Q2.
6. The Home producer withholds `div_score` / `avg_corr` (None, label "Unavailable") on a thin sample, so every consumer is safe by construction; `diversification_recommendations` reads only `risk_pairs` (PAIR_RISK / PAIR_RISK_WITHHELD unchanged).

**Owner answers 2026-11-25 (all recommended defaults):**
- **Q1 = B:** a reading with a named young holding left out stays UNVERIFIED until it counts (no change to `correlation_claim_verified`; ANT appears in `correlation_unchecked`). Screens and gates show / use the rest's real pairs; records (`risk_metric_history`), Home "All Clear", Structural Scan saves and the weekly thesis posture stay withheld ≈ 4 weeks per IPO, as today. No cap, no new constant.
- **Q2:** hold the LEFT-OUT holding's own Grow Today add ("T's correlation with your other holdings can't be measured: only N daily returns of price history (20 needed). Add held until it can be."), no ledger row, binding after every real gate; partners' adds proceed with the caption (F-290). Disclosed consequence: on a short-download day for X, a real X×Y danger pair no longer pauses Y by chance.
- **Q3:** each pair of counted holdings on its own shared days (pairwise). No consumer of `_corr_df_cache` inverts the matrix (heatmap, both gates, clusters, `detect_new_clusters_strict`, F-230, blast radius, forward-sim max pair checked); identical to today on an aligned book.
- **Q4:** `classify_risk_banner` order: `n_danger` → Action Required; score < `DIVERSIFY_MODERATE_PCT` and sample TRUSTED → Action Required; `n_warning` or (score < `DIVERSIFY_WELL_PCT` and TRUSTED) → Monitor; not verified → Check Incomplete; else All Clear. A THIN or UNKNOWN sample never escalates on the score. `risk_banner_record_label` unchanged.

**Planner choices without a question:** `corr_coverage_n` now means "fewest shared returns behind any measured pair, young holdings left out" (same value on an aligned book; dated note in db.py comment + architecture, no version column); no new cache key (`_corr_coverage_cache` gains keys); `_SYNTH_SCHEMA_VER` 28 → 29.

**Wording (plain text):**
- Left out: "Correlations measured without ANT (3.6% of the book: 4 trading days since its first close, 20 needed); its pairs are not shown and it is not part of the score." (weight part omitted when no weights passed; one phrase helper shared with `risk_window`).
- Thin, 🔗 Risk Analysis (replaces the ⛔ box, pair cards, heatmap and score): "⛔ Correlations can't be measured on this sample: only N shared daily returns (20 needed). A sample this small is unreliable in both directions: unrelated stocks can read as moving together and a real pair can read as independent, so no pairs, clusters or diversification score are shown. Adds on any pair that crossed the line stay paused until it can be measured." Also replaces the false "toward zero" sentence (app.py ~19071-19072); fix F-246 and architecture ~3070.
- G-26 thin (`describe_reason`, ≤ 300 chars): "Correlation with X couldn't be measured: only N shared daily returns (20 needed). Add paused until it can be; this is not a finding that they move together, and no trim is recommended."
- G-25 thin: "New-cluster check with X couldn't be measured: only N shared daily returns (20 needed). Add paused until it can be (a Structural Scan can't be saved on this sample); this is not a finding that they move together."
- Home Structural banner thin: "🔍 Correlation couldn't be measured this run (only N shared daily returns, 20 needed), so adds to A, B are paused until it can be. This is not a finding that they move together."
- Unchecked-holding captions (`util.correlation_unchecked_note`, `pair_add_gate.unchecked_disclosure`, `util.correlation_record_withheld_note`) take optional `corr_coverage`: a short-history name reads "has only N daily returns (20 needed)"; "price history couldn't be loaded" only for a truly missing name.

**Chunks (one push of separately reviewed commits; C1 alone would mislabel ANT):**
- **C1 matrix builder** (gate files): public `risk_window.classify_holdings(held_data, tickers, *, min_returns)` (counted session-dated closes + left-out with reasons `short_history` / `no_prices`; `_book_returns` calls it; parity test); new module `stock_analyzer/corr_window.py` (counted set → per-name returns → pairwise `.corr()` `.round(3)`; coverage keeps old keys + `left_out`, `n_obs_max`, `min_returns`, optional `weights`; fewer than 2 usable series → None; fewer than 2 counted → dict with `n_obs` 0); `portfolio.correlation_matrix` / `correlation_coverage` delegate with the same signatures; `_GATE_FILES` + review-policy line.
- **C2 disclosure / view decision:** `corr_window.view_state(corr_df, corr_coverage, corr_unchecked)` → full / partial / withheld (THIN) / none + notes (UNKNOWN keeps today's caption); note functions take reasons from coverage; `diversification_label_text` "Unavailable · sample too thin". Files: corr_window.py, util.py, pair_add_gate.py.
- **C3 gates:** `pair_add_gate` / `cluster_add_gate` tag each block with `"sample"` (a fired map is never dropped; F-294 order unchanged), thin `describe_reason`; `home_risk_synthesis` producer withholds the score on thin + Q4 in `classify_risk_banner` (docstring ~352-386 rewritten); `risk_metric_history` diagnostics `corr_left_out`.
- **C3b Grow Today** (`daily_briefing.py`): `_add_block_gate` (~1118-1150) binds G-26 / G-25 in place only for non-thin entries; new `CORR_UNMEASURED` after ENTRY for thin entries + the Q2 hold, bucket `gate_id` None (no ledger row), `clears_when`; `_adds_paused_list` treats it as a hold; new kwarg `corr_coverage=None` on `build_daily_briefing` / `_grow_today` (None = today's behaviour, cron unchanged). Keep clear of entry-fail-closed C3 / C4 edits.
- **C4 app.py:** 🔗 Risk Analysis ~19019-19142 (`view_state`, notes, pairs / heatmap hidden on thin, false sentence removed); 🧩 Clusters ~21362-21397; Home Structural banner (~8780+) thin wording; pass `_corr_cov` into the Brief; render the new Grow Today bucket; 📈 Analysis multi-ticker matrix (~30228-30232) through `corr_window` + caption; forward-sim stop-cluster note (~20934-20960) names a left-out name; `_SYNTH_SCHEMA_VER` 29; optional cron log line (cron_runner ~1173) only after SlidingStop C3.
- **C5 docs (lead):** new F-ID, F-246 premise fixed, G-25 / G-26 rows (thin wording, unledgered, after ENTRY), G-34 mention (Q2); architecture ~3043-3086 + `corr_coverage_n` comment (also db.py:958); coordination-caches (`_corr_coverage_cache` keys, `_corr_unchecked_cache` holds young names, `_div_score_cache` None on thin); User Guide (Risk Analysis, Clusters, Home banner); bug register; `docs/plans/gate-suppression-ledger.md` dated note (G-25 / G-26 rows 2026-11-19 → ship date may rest on a thin sample) + read-only SQL count for the ~12-10 look; Status / shipped-log / CLAUDE.md / memory.

**Same-pattern sites:** in scope portfolio.py :1575-1592 / :1595-1630, home_risk_synthesis.py :135-171, risk_metric_history.py :137-168, app.py ~30228-30232, ~20934. Out of scope (named): `portfolio_intelligence.risk_budget` :358-373 (RiskBudgetYoung, needs a PD covariance; stays RiskListwiseYoung C4b, should use `classify_holdings`); custom stress `.corr()` app.py ~21486 and `stress_test.stress_correlation_matrix` :480-490 (historical windows, gated by `too_short`); `correlation_to_portfolio` / `beta_repair` tz join (already logged). New sibling: CorrNoneProceeds (bug register).

**Coordination:** two disclosures on 🔗 Risk Analysis (risk dashboard and correlation, one phrase helper so names / N agree). SlidingStop C3: no `headless_alert_engine.py` overlap; if CorrYoung isn't at SHIP by ≈ 12-07, land SlidingStop C3 first. EnterNowInProtect readout: no population effect (`watchlist_advisor` reads no correlation input; G-25 / G-26 / Q2 act only on in-app Grow Today adds; the cron passes no maps).

**Tests the build must include:** young holding at 1 / 4 / 19 / 20 returns (19 left out and named, 20 counts); 0-day all-NaN (n_obs 0, thin, score None, view withheld, empty maps → None, Home Check Incomplete); short-download failure (long-held name with 5 bars left out, neutral wording, rest trusted, own add held, a real danger pair among the rest still pauses and is ledgered); a ≥ 0.80 long-window pair still danger; aligned US book identical to the old builder; no re-entry cliff (a counted 25-return name pairs on 25 while other pairs keep ≈ 125); `risk_window` / `corr_window` parity (same left-out set and N); tripwire `CORR_MIN_OBS_TRUSTED == BETA_MIN_RETURNS`; the Q4 table (thin 36 → Check Incomplete; UNKNOWN 25 → Check Incomplete; trusted + ANT unchecked 36 → Monitor; trusted full 25 → Action Required; thin + n_danger → Action Required; thin + n_warning → Monitor; verified 50 → All Clear); `risk_banner_record_label` unchanged; D2b (thin fired map kept + tagged, binds after ENTRY, no ledger row, "couldn't be measured", never "move together"; trusted fired map still ledgered); invariants (no thin text contains "move together"; "toward zero" never returns to app.py; `claim_verified` False with a young holding left out, True on a full trusted book; `risk_metric_history` withholds and logs `corr_left_out`); time-context (weekend / holiday visit, flip day 19 completed + live bar = 20 same as `risk_window`, a Seoul-tz name lines up with US sessions, intraday-stamped bar merges, US holiday with a non-US name trading); antipatterns. About 15 existing tests with short-history fixtures to be rewritten with misaligned ≥ 21-bar names (about 32 test files touch these builders). Before C4: read-only spot check on the live book (expect 0 pairs ≥ 0.65).
