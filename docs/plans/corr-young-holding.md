# Correlation with a young holding (CorrYoung)

**Status:** ANALYSED 2026-11-25 (lead, from an owner screenshot of 🔗 Risk Analysis). Not designed, nothing built. **Owner 2026-11-25: pick it up in a new session after RiskListwiseYoung C4 is finished** (`docs/plans/risk-young-holding.md`). Next step: the owner answers D1–D4 (§5), then planner → implementer → reviewer (gate files). **Owner decisions 2026-11-25 (after RiskListwiseYoung C4 shipped; lead verified §3's code claims at HEAD `f081d926`; the "toward zero" sentence is now at app.py ~19072):** **D1 = leave a young holding out, named** (fewer than `BETA_MIN_RETURNS` returns; the rest measured on their real shared history); **D2 = (b) keep the add-pause on a thin sample but word it "correlation couldn't be measured", never "these move together"** (the lead's (a) loosened a buy gate; (b) matches the owner's 2026-11-25 entry-fail-closed U1 "a buy check that couldn't run holds the buy" and the A-H2 rationale in `pair_add_gate.py`); **D3 = an unverified diversification score shows "Check Incomplete"** on 🏠 Home instead of escalating; danger alerts still escalate; **D4 = leave cluster chaining as is**. NEXT: Opus planner on §6 with these answers (C1–C4), then implementer → reviewer.

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
