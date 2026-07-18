# DRISHTA — Next Evolution: Strategic Product Plan

**Date:** 2026-07-17  
**Author:** Ajay Kumar  
**Analysis model:** Claude Sonnet 4.6 (first pass)  
**Review:** Claude Opus 4.8 (second pass, completed 2026-07-17) — REVISE-FIRST verdict; 30 findings; all incorporated in this version  
**Status:** Phase 1 (Waves 1-3: E-capture, F, C, D) SHIPPED 2026-07-17. Phase 2 (Concept B — all 3 panels: Correlation Clusters, Risk Budget Gauge, Factor Tilt Heatmap) SHIPPED 2026-07-17. Concept A (Phase 3) not started. Concept A (Phase 3) not started.

> **Scope:** This is a product strategy document, not an implementation spec. Nothing here should be built until concepts are reviewed together and approved. It is the starting point for a structured product discovery conversation.

---

## Review Log

| Pass | Model | Verdict | Material corrections |
|---|---|---|---|
| First draft | Claude Sonnet 4.6 | — | Original analysis |
| Second pass | Claude Opus 4.8 | REVISE-FIRST (30 findings) | Pre-mortem scope bug (C), Behavioral Fingerprint statistical self-contradiction (A), Decision Reconstruction sequencing error (E must capture in Phase 1), forward-return internal contradiction (§1.1 vs §5.8), tax-awareness gap (largest missing concept), factor method rewrite (B), Concept D governance flag, posture critique narrowed (§1.5), What Not to Build additions, 90-day plan corrections |

---

## Build Log (post-approval — actual execution, tracked here rather than retrofitting Part 6)

Build order chosen with the user after live scoping: **E-capture + F (Wave 1) → C (Wave 2) → D (Wave 3, blocked on policy conversation)** — differs from Part 4's literal priority order (C → F → D → E-capture → B → A) once verification showed F was ~80% already built on the existing `tax_advisor.py`, making it a cheap pull-forward alongside E-capture.

| Wave | Concept | Status | Commits | Reqs | Memory |
|---|---|---|---|---|---|
| 1a | E — Decision Reconstruction (capture only) | **SHIPPED** 2026-07-17 | `8c9c97a` | — (invisible/no UI; not a user-facing surface) | `project_decision_context_capture` |
| 1b | F — Tax-Aware Exit/Harvest Lens | **SHIPPED** 2026-07-17, Opus SHIP 0 blocking | `e97658b` | F-186 | `project_tax_aware_lens` |
| 2 | C — Pre-Mortem Protocol | **SHIPPED** 2026-07-17, Opus SHIP 0 blocking; post-ship bug found+fixed same day | `c467c92` → `f6a9d44` | F-187 | `project_premortem_protocol` |
| 3 | D — Regime-Conditional Targets | **SHIPPED** 2026-07-17, Opus SHIP 0 blocking; post-ship bug found+fixed same day (`09be8bd`) | `0c6df7a` | F-188 | `project_regime_conditional_targets` |
| 4 (Phase 2) | B — Correlation Clusters (panel 1 of 3) | **SHIPPED** 2026-07-17 | `5c980b3` | F-189 | `project_portfolio_intelligence` |
| 5 (Phase 2) | B — Risk Budget Gauge (panel 2 of 3) | **SHIPPED** 2026-07-17 | `698fcd5` | F-190 | `project_portfolio_intelligence` |
| 6 (Phase 2) | B — Factor Tilt Heatmap (panel 3 of 3, final) | **SHIPPED** 2026-07-17, Opus FIX-FIRST→SHIP (2 blocking fixed on re-review) | `a4c95cb` | F-191 | `project_portfolio_intelligence` |
| — | A (Phase 3) | Not started, per plan's own phasing | — | — | — |

**UI design pivot from Part 2's Concept C spec (worth noting for anyone re-reading §"UX" under Concept C):** the plan called for an `st.dialog` modal. Shipped instead as an outside-the-form pre-condition section (mirrors F-5's "Draft thesis" button placement) with the required commitment enforced as one more validation gate — functionally equivalent friction, zero `st.dialog` risk (it would have been the first in the codebase), and avoided forcing a duplicate of the ~120-line holdings-sync/concentration-nudge block that intercepting the write would have required. See `project_premortem_protocol` memory for the full reasoning.

---

## Context

DRISHTA has reached a professionally functional stage after approximately 35 days of intensive development: ~646 commits, ~70,600 LOC, a 4-pillar composite scoring engine, multi-source data stack, full exit-discipline ladder, AI Insights (thesis tracking, weekly debrief, monthly report, thesis authoring), Catalyst Watch (earnings playbook + entry candidates), My Edge retrospective analytics (Benchmark Mirror, Workflow ROI, Decision Quality Timeline A–F), analyst coverage Ideas Inbox, headless alert email cron, stress testing with historical scenarios, trade journal, and a comprehensive gate system enforcing concentration, beta, macro, and fundamental constraints.

The app is already a hybrid: hard gates suppress with explicit banners; many surfaces (My Edge, Catalyst Watch, analyst coverage, stress test) are explicitly awareness-only. This distinction matters for how the challenges in Part 1 should be read.

This document defines where the application goes next.

---

## Part 1: Challenging the App

### 1.1 Problems the app may still not solve

**The portfolio-level forward view is missing.**  
Every major feature operates on individual positions. The composite score rates a stock. The deterioration ladder rates a holding. The concentration gates cap a position. There is no answer to the question a portfolio manager asks every Monday: *"Given my current positioning, what are the scenarios in which I lose meaningfully, and am I appropriately positioned for the range of outcomes ahead?"*  
The stress test uses historical analogues but does not model what the portfolio looks like AFTER the app's own mechanical rules (stops, gates, trims) fire.

> **Reconciliation with §5.8:** The forward-return gap identified here refers to a portfolio-level scenario range — which is sound and addressed by Experimental Track E1 (Forward Portfolio Simulator). It is NOT a call for stock-level price point forecasts, which are prohibited in §5.8 and elevated to an invariant there. These two positions are consistent once scoped correctly.

**Opportunity cost is directional, not precise.**  
The app has an opportunity cost expander showing missed picks. It does not close the decision-cost loop: "Holding through a deterioration signal cost you [directionally] additional drawdown relative to acting on the TRIM." This should be framed directionally — not as a spurious precise dollar figure — because computing the true counterfactual (what you would have deployed the capital into) is speculative.

**Hidden factor exposures are invisible.**  
The app tracks sector allocation and beta. It does not track factor exposures: momentum tilt, growth/value tilt, quality factor exposure. A portfolio of NVDA, AAPL, MSFT, META, and AMZN might look sector-diversified but is deeply momentum-long and growth-long. In a factor rotation, the entire book can move together in ways the sector view misses.

**The portfolio is entirely tax-blind. (Largest missing gap for this taxable account.)**  
Every exit signal, TRIM directive, and opportunity-cost calculation is tax-blind. This is the single biggest real-dollar gap for a taxable Robinhood account. An EXIT signal issued 3 days before a position becomes long-term-eligible represents an avoidable 15–17pp tax-rate differential. A TRIM that lands inside a 30-day wash-sale window on a recent add loses the tax deduction. The opportunity-cost expander does not net transaction costs. None of this requires new investment-policy constants — it is awareness-only context layered onto existing signals.

**Cash is never managed as an allocation.**  
The app tracks portfolio equity and margin but treats cash as a residual, not a managed position with a regime-conditional floor. In Risk-Off regimes, a well-managed book holds meaningful cash — the app has no mechanism to detect that cash is dangerously low relative to the current regime posture.

**Position sizing is rule-based but not conviction-adjusted.**  
The app uses 1.5% portfolio risk per trade (`RISK_PCT_PER_TRADE` in `constants.py`) — a reasonable baseline. It does not vary sizing by regime, by conviction (all four composite pillars aligned vs. a marginal 65 pass), or by opportunity set (if Grow Today has four Strong Buys simultaneously, there is no portfolio-fit ranking to prioritize them).

**No explicit investment time horizon.**  
The composite weights, deterioration thresholds, and stop logic are calibrated without a stated investment time horizon. A TRIM trigger makes very different sense for a 6-month trader versus a long-horizon compounder. This assumption should be made explicit and user-configurable.

---

### 1.2 Areas of potential false confidence

**Composite score precision.**  
A score of 67 versus 71 implies a level of precision that does not exist. Both land in the same "Buy" band. The score is a useful ranking tool; it is not a calibrated probability estimate. The app does not communicate this uncertainty.

**"Act Today" urgency framing (partially addressed).**  
The 2026-07-17 terminology sweep and the calm-advisor Tier 1+2 work already addressed a significant part of this. The remaining gap: a WATCH signal on a 6% drawdown from peak is not a same-day crisis for a long-horizon investor, and some Act Today items carry more urgency in their label than the underlying signal warrants. This should be scoped to specific remaining surfaces, not treated as an unclosed systemic problem.

**Benchmark comparison without risk adjustment.**  
My Edge Benchmark Mirror shows raw return vs. SPY/QQQ. If the portfolio ran 1.4 beta in a bull period and outperformed SPY by 2%, that is not alpha — it is beta. The comparison should show both raw return and risk-adjusted return (Sharpe-equivalent or beta-adjusted), or it risks confirming skill that is actually exposure.

**Earnings beat rates treated as stable forward probabilities.**  
The Catalyst Scanner gates on beat_rate ≥ 70%. Historical beat rates vary by market regime and earnings cycle. Using a 3-year historical beat rate as a forward probability in a different earnings cycle is a known bias in quantitative earnings research.

**LLM-generated thesis status has an omission blind spot.**  
The INTACT/WEAKENING/BROKEN assessment cannot detect contradictory evidence that was never entered into the system. If the user never pastes the news that a key catalyst was delayed, the thesis remains INTACT by omission. The system should state this limitation explicitly in the UI.

---

### 1.3 Features that could overwhelm without improving decisions

**Decision Quality Timeline at small trade counts.**  
At fewer than ~30 trades per period, the A–F grade has very wide confidence intervals. A C grade on 4 trades in a month has no statistically meaningful signal — but the grade looks authoritative. The same statistical discipline applies to the planned Behavioral Fingerprint (see Concept A). Suppress or heavily disclaim all per-slice metrics below a minimum sample threshold.

**Rate sensitivity table.**  
TLT correlation computed over 20+ days for primarily equity names produces noisy, time-varying numbers. Unless the output maps clearly to a decision (the current rendering does not), it is noise dressed as signal.

**Multiple simultaneous buy signals without portfolio-level allocation logic.**  
When Grow Today shows three new picks and two add-to-winners simultaneously, the investor has no framework for prioritizing. Without a rank-ordering that accounts for marginal sector exposure and correlation to the existing book, the most emotionally salient name wins — not the best portfolio fit.

---

### 1.4 Capabilities professionals use that retail investors rarely receive

- **Risk budgeting:** Allocating a fixed portfolio volatility budget across positions, not just capital. Professionals cap the volatility CONTRIBUTION per position to the portfolio, not just its dollar share.
- **Scenario-conditional portfolio positioning:** In regime X, what should the portfolio look like? The app detects regimes but does not translate detection to target portfolio profiles.
- **Downside probability estimation:** What is the probability of a 15%+ portfolio drawdown over the next 3 months? This is directional and range-based at the portfolio level (sound), not a stock-level point forecast (unsound — see §5.8).
- **Realized alpha decomposition:** How much of the return was beta? Sector? Stock selection? Sizing? None of the current My Edge metrics decompose return at this level.
- **Tax-aware exit management:** Overlay holding-period context onto exit signals. Standard practice in private wealth management; absent from all retail platforms the author is aware of.
- **Pre-mortem analysis:** A structured protocol for asking "what would have to be true for this to be my worst decision this year?" before committing capital. Not a disclaimer — a decision discipline.

---

### 1.5 Product assumptions to reconsider

**"The app decides, it does not inform" — correct for hard gates, a nuanced question for discretionary recommendations.**  
The app is already a hybrid: hard gates (stop breached, sector ceiling, beta ceiling, macro event) suppress with explicit banners and no discretion. Many surfaces (My Edge, Catalyst Watch, analyst coverage, stress test) are explicitly awareness-only. The assumption to reconsider is narrower: for *discretionary Buy recommendations in Grow Today*, there is a case that the investor's edge accumulates more from engaging with the evidence than from receiving an actionable call. The refined posture: *"The app decides what to suppress; it presents evidence for what the investor should decide about new entries."*

**Governance note:** Changing this operating posture edits a CLAUDE.md hard rule (the "decides not informs" line). This is a policy decision the user must explicitly own — it cannot be resolved by a plan document or a code change.

**The user's risk tolerance is constant.**  
The constants are fixed policy thresholds. But risk tolerance and time horizon shift — with portfolio size, with life events, with market experience accumulation. The app has no mechanism for the investor to recalibrate their own posture, nor does it detect when actual behavior has drifted from stated posture.

**More intelligence is always better.**  
Every feature added creates more signals. A well-designed intelligence system should make the investor take fewer, higher-quality decisions — not more decisions. This constraint must be applied to every concept in Part 2.

---

## Part 2: Innovative Concepts

---

### Concept A — The Behavioral Fingerprint

**Investor problem:**  
Every investor has specific, repeatable behavioral biases that cost money — but they don't know which ones, how severe they are, or when they fire. Generic advice about "avoiding FOMO" is useless. Directional, personalized pattern observation is not.

**How it works:**  
Every time the investor overrides, ignores, or delays an app recommendation, the system records: signal issued, action taken, timing, market context, outcome at 30/60/90 days. Over time it surfaces directional behavioral patterns. Examples (these are illustrative patterns, not statistics derived from any real dataset):

- *Disposition effect:* "You tend to act on positive TRIM signals faster than negative ones — a classic disposition pattern."
- *Loss aversion timing:* "TRIM signals on losing positions sit unacted-on longer than TRIM signals on winning positions."
- *Time-of-day pattern:* "Trades entered in the first 30 minutes of market open have a directionally worse track record in your journal."
- *Recency bias:* "Buy signals accepted immediately after a 3-day up-move appear more frequently than those after down-moves, at comparable composite scores."

**Critical design constraint:** Every bias pattern is suppressed until a minimum per-slice sample threshold is met (directional reporting, not quantified scores). No bias is labeled with a score until the sample is statistically meaningful for that slice. For a single retail investor making 5–10 trades per month, most sub-slices will be insufficient for months — the system must say "insufficient data" clearly rather than present a pseudo-precise score.

**Regime-conditioning requirement:** Not all patterns are biases. Selling winners fast in a topping tape or delaying a TRIM during a confirmed V-shaped recovery can be adaptive behavior, not disposition effect. The fingerprint must condition on regime or frame explicitly as "correlation observed in your behavior, not a verdict on it."

**Coordination note:** Behavioral Fingerprint builds on the same `signal_flow`/`recommendations_history` data as My Edge's Decision Quality Timeline. These must be wired via publish/consume, not built as a parallel logger — two competing "your discipline" numbers would erode trust.

**Why meaningfully different:**  
No retail platform shows investors which specific biases they personally have, observed in their actual decisions, with trend direction. This is what institutional investors pay behavioral finance consultants for.

**Status:** New. No retail platform does this at the individual-decision level.

**Data required:** Recommendation log (partially exists; must audit completeness), user action timestamps, portfolio state at decision time (session state + DB), 30/60/90-day price outcomes.

**AI methods:** Pattern detection over sparse decision sequences; statistical tests with strict sample-size gates; behavioral finance taxonomy (Kahneman/Thaler); no LLM needed.

**UX:** A "Decision Profile" page. Cards per detected bias (only when sample ≥ threshold): directional strength indicator, trend direction (improving/worsening), one concrete example from recent history. "Insufficient data — check back after N more decisions" when below threshold. Observation only — no prescriptions.

**Expected benefit:** If an investor can identify a specific repeatable pattern in their own behavior, they can precommit to a different rule. Precommitment devices are the most evidence-backed behavioral intervention available to retail investors.

**Key risks:** Sub-slice sample sizes may be insufficient for months given a single retail investor's trade frequency. Statistical discipline (suppress below threshold) is mandatory. The system must clearly distinguish observation from verdict.

**Risk of false confidence:** MEDIUM (corrected from Sonnet's LOW — the directional-but-not-quantified framing still risks premature pattern labeling at small N).

**Implementation complexity:** MEDIUM. The data model (recommendation log vs. actual action) requires careful definition and a completeness audit of historical data.

**Differentiation:** VERY HIGH. Not available in Robinhood, Schwab, Wealthfront, or any mainstream retail platform.

---

### Concept B — Portfolio-as-One Positioning Intelligence

**Investor problem:**  
The app manages positions one at a time. A portfolio manager manages the book as a single entity: a risk budget, a factor profile, a correlation structure, a return objective. DRISHTA has the underlying data but surfaces it in fragmented form across separate pages. The investor has no single view of what their ownership MEANS in aggregate.

**How it works:**  
A unified "Portfolio Snapshot" answering:

1. **Risk budget status:** Which positions consume the most portfolio volatility (not just capital)? Which have the worst risk-per-unit-of-expected-return?
2. **Factor tilt map:** A heatmap showing directional exposures to the four major equity factors — momentum, value, quality, growth. **Method: returns-based style analysis.** Regress/correlate held positions against factor-proxy ETFs (MTUM, VLUE, QUAL, USMV, VUG) over a trailing window, using the existing correlation infrastructure already in the app. This is technically sound, cheaper to build, and honestly directional rather than false-precise. Do NOT use FMP `.info` style tags summed across positions — that is not factor analysis and will produce garbage.
3. **Correlation cluster map:** Not a full heatmap. Positions grouped into natural correlation clusters: "These 4 positions tend to move together; when one falls, watch the others." This is the cheapest, most decision-relevant piece of Concept B and can be pulled forward independently of the factor tilt map.
4. **Regime fit score:** Given the current macro regime, is the portfolio positioned correctly? "In the current Risk-Off regime, your book is directionally misaligned: high-beta, growth-tilted. Regime-aligned portfolios tend toward lower-beta, quality-tilted."
5. **Highest-leverage action:** One clear sentence per panel: "The highest-leverage portfolio adjustment is [specific action]."

**Why meaningfully different:**  
Most tools show you what you own. This tells you what your ownership MEANS in aggregate — what risks you've taken on that you may not have intended.

**Status:** Partially available (correlation + concentration already exist in fragments). The unified view with factor tilts (via returns-based analysis) is new.

**Data required:** Position data (already in app), factor ETF return series (yfinance fetch, same infrastructure), macro regime (already detected), volatility (already fetched).

**AI methods:** Returns-based style analysis (standard quant method, pure Python/pandas); hierarchical or k-means clustering for position groupings; rule-based regime-fit scoring.

**UX:** A dedicated "Portfolio Intelligence" page. Three panels: Risk Budget Gauge / Factor Tilt Heatmap / Correlation Clusters. One action recommendation per panel. The correlation cluster map is buildable first and independently.

**Key risks:** Factor tilt estimates for a 12–15 position portfolio are inherently noisy over short windows. Must be framed as directional indicators, not precise measurements. This must be stated prominently.

**Risk of false confidence:** MEDIUM. Uncertainty framing required; returns-based analysis is noisy at small portfolio sizes.

**Implementation complexity:** MEDIUM. Returns-based style analysis is well-understood; the correlation infrastructure already exists.

**Differentiation:** HIGH. Most retail platforms do not do factor-level portfolio diagnosis at all.

---

### Concept C — The Pre-Mortem Protocol

**Investor problem:**  
Retail investors make decisions in the heat of the moment — market is open, a position is moving, there's an earnings event. They do not use structured pre-commitment devices. Professional investors use pre-mortems: before making a decision, they ask "what would have to be true for this to be the worst decision I made this year?" This disciplines decision-making before capital is committed.

**How it works:**  
Before a **prospective live Buy decision** — specifically: acting from a Grow Today recommendation, or entering a new Buy in the Trade Journal in real time — the app presents a structured pre-mortem:

1. **The case FOR (engine's view):** "The app recommends this based on composite 71, improving fundamentals, and sector underweight." (Already exists as recommendation basis.)
2. **The case AGAINST (app-generated, position-specific):** 3 counterarguments grounded in the position's own data — stock-level, regime-level, and portfolio-level. The counterargument MUST cite: (a) which composite pillar is driving the score and any concern with it (e.g., momentum-heavy scores revert faster when breadth narrows), (b) the portfolio's current factor/sector tilt and how this position changes it, (c) relevant macro or earnings context. Generic counterarguments ("stocks can go down") are a failure of this feature and must be caught in the LLM prompt design.
3. **Investor pre-commitment:** One line — "what would make me wrong about this." Required field; cannot submit without it. Stored with the trade record; surfaced during F-1 thesis review.
4. **Outcome linkage:** 90 days later, the pre-mortem question surfaces alongside the actual outcome.

**Scope constraint (Opus Finding 1 — critical):** The pre-mortem modal applies ONLY to prospective live Buy decisions. It must be explicitly exempt from:
- Retroactive journal entries (adding historical trades not yet in the system)
- Broker CSV / text imports (`broker_import.py`, `recalculate_from_trades`)
- SELL entries of any kind (exit friction is bad; entry friction is good)
- Any automated or batch write path

A mandatory modal on a broker import or retroactive entry is nonsensical and would block those workflows.

**Why meaningfully different:**  
This is not a warning label. It is a structured thinking protocol built into the transaction workflow. It takes 30 seconds. The outcome linkage converts it from a point-in-time friction into a learning instrument.

**Status:** New in this form. Thesis authoring (F-5) captures the positive case; the pre-mortem captures the negative case. Together they bracket every prospective new entry.

**Data required:** Existing composite decomposition, macro regime, portfolio state, earnings calendar — all already in the app. No new APIs.

**AI methods:** Haiku LLM to generate position-specific counterarguments from structured inputs. The quality bar is high: specificity to the position's own composite decomposition and portfolio context is mandatory.

**UX:** A `st.dialog` modal at prospective Buy Journal entry or Grow Today "act on this." Three sections: Engine Says / Pre-mortem / Your Risk (one required text field). 30-second completion target. Note: `st.dialog` reruns the script on interaction; every iteration without local testing requires a deploy. Budget accordingly.

**Expected benefit:** Reduces impulsive buy decisions. Forces explicit engagement with uncertainty before capital is committed. Creates a durable record that enables post-hoc learning.

**Risk of false confidence:** LOW. The pre-mortem is explicitly adversarial to the recommendation.

**Implementation complexity:** LOW-MEDIUM. The engineering is simple; the prompt quality is the challenge.

**Differentiation:** VERY HIGH. No retail platform has a mandatory pre-mortem protocol embedded in the transaction flow.

---

### Concept D — Regime-Conditional Position Targets

**Investor problem:**  
The app detects market regimes and uses them to block certain recommendations. It never answers: "Given this regime, what should my portfolio LOOK LIKE?" The investor knows the regime; they don't know whether their positioning is correct for it.

**How it works:**  
For each detected regime, define a target portfolio profile — not specific tickers, but measurable characteristics. Illustrated (not final) example:

| Regime | Target Avg Beta | Growth Tilt | Quality Tilt | Cash Floor |
|---|---|---|---|---|
| Risk-On, CPI Controlled | ≤ 1.2 | Moderate | Low | ~5% |
| Risk-Off, VIX Elevated | ≤ 0.9 | Low | High | ~15% |
| Stagflation | ≤ 1.0 | Very Low | High | ~20% |
| Rate-Cut Optimism | ≤ 1.1 | Moderate | Moderate | ~8% |

The app computes the current portfolio profile against the regime target and shows the gap diagnostically: "You are running beta 1.28 against a regime-aligned target of ≤ 0.9. Three adjustments close this gap most efficiently: [ranked, actionable list]."

This is a diagnostic, not a mandate. The investor decides whether and how quickly to move toward the regime target.

**Governance requirement (Opus Finding 6 — mandatory before ship):**  
The target table values above are illustrative only. The actual values are **new investment-policy thresholds** that must:
- Live in `constants.py` (hard rule #1)
- Be set through an explicit conversation with the user (they are policy decisions, not technical choices)
- Be cited in the commit body with old→new values
- Be reviewed by Opus (hard rule #4) before the commit ships

This is not optional. Do not ship Concept D with hardcoded values in the rendering code.

**UX (corrected from Sonnet's Home-card proposal):**  
The regime gap analysis lives in Risk Analysis — a slow-moving diagnostic does not warrant daily prominence on Home. Home may receive a calm, change-only annotation ("Regime posture changed — see Risk Analysis") when the detected regime *changes*, not a persistent daily gap gauge. A persistent gap number on Home would invite regime-chasing churn.

**Why meaningfully different:**  
The regime detection already exists. The target profile converts detection into actionable portfolio-level guidance — the difference between a weather forecast ("it's going to rain") and a dressed-for-the-weather recommendation.

**Status:** Regime detection shipped (Phase 4 macro cluster). Portfolio target profiles and gap analysis are new.

**Data required:** Current portfolio beta (already computed), sector allocation (already computed), factor tilts (from Concept B), macro regime (already detected).

**AI methods:** Rule-based target profiles per regime (constants-based); linear gap analysis. No ML needed.

**Key risks:** Regime assignment is probabilistic and can be wrong. Target profiles are investment-policy heuristics, not optimization results. Both limitations must be stated.

**Risk of false confidence:** MEDIUM. Regime uncertainty must be surfaced alongside the gap.

**Implementation complexity:** MEDIUM. The target profile constants require a policy-setting conversation before any code is written.

**Differentiation:** HIGH.

---

### Concept E — The Decision Reconstruction Ledger

**Investor problem:**  
Months after a decision, the investor cannot reconstruct the state of the world as it was when they made it. Hindsight bias corrupts retrospectives. The portfolio is a sequence of decisions; learning from those decisions requires seeing the CONTEXT in which they were made — not the context you have now.

**How it works — decoupled build (Opus Finding 10 — most important sequencing correction):**

**Phase 1 (ship immediately): Passive context capture.**  
On every prospective Trade Journal write (Buy, Sell, or significant override), the app appends a timestamped context snapshot as a `jsonb` blob to the trade record:
- Composite score and pillar decomposition at that moment
- Macro regime
- Portfolio heat metrics (beta, sector concentration, factor tilts if available)
- Active app recommendations at that moment
- Thesis text on file
- Analyst coverage on file
- Most recent news sentiment

The capture is automatic, silent, and costs almost nothing to implement. It requires no UI. Schema-versioned for forward compatibility. None-safe (per CLAUDE.md backward-compat rule for legacy rows).

**Why capture must start now:** Past composite decompositions, regimes, and recommendation states cannot be reconstructed retroactively after the fact. Every month of deferred capture is a month of decision context permanently lost. The capture must start in Phase 1; the viewer UI can wait until Phase 3.

**Phase 3 (build when history exists): Retrospective viewer.**  
On the Trade Journal, each trade row gains a "View context" expander showing the frozen snapshot from the decision date — like opening a newspaper from the day you made the call. Six months after capture begins, the investor can review: what the world looked like at that moment, what the actual outcome was, and what differed.

**Why meaningfully different:**  
Retrospective learning is fundamentally limited if you can only see WHAT you did and not WHY the world appeared to support it at the time. This is the context-preservation problem, and it has not been solved for retail investors.

**Status:** New in this form. Individual data points exist; the timestamped snapshot per decision does not.

**Data required:** All data already computed and displayed in the app, captured at trade timestamp.

**AI methods:** No AI for capture. LLM could narrate the decision context in plain English on retrospective review (on-demand, not automatic — LLM cost must be bounded).

**Key risks:** Schema must be forward-compatible and schema-versioned from day one. Viewer must handle snapshots from previous schema versions gracefully.

**Risk of false confidence:** LOW. Historical record, not prediction.

**Implementation complexity:** LOW for capture (Phase 1); HIGH for the retrospective viewer (Phase 3).

**Differentiation:** VERY HIGH. Genuinely novel for retail investors.

---

### Concept F — Tax-Aware Exit/Harvest Lens

**Investor problem:**  
This is a taxable Robinhood account. Every exit signal, TRIM directive, and opportunity-cost calculation is completely tax-blind. An EXIT signal issued 3 days before long-term capital gains eligibility represents a real avoidable tax cost — potentially 15–17pp of additional rate differential on a meaningful gain. A TRIM landing inside a 30-day wash-sale window on a recent add loses the tax deduction entirely. The opportunity-cost expander does not net transaction costs. None of this requires changing any investment decision — tax-awareness is pure context layered on top of existing signals.

**How it works:**

1. **Holding-period chip on every held position:** Shows `LONG-TERM`, `SHORT-TERM`, or `LT in N days` alongside the existing position metrics. Purely informational.

2. **Exit signal tax annotation:** When Act Today (EXIT) or Review Before Close (TRIM) fires on a position within a user-defined window of long-term eligibility (e.g., 30 days), the card gains an amber annotation: "This position becomes long-term capital gains eligible in N days. Waiting may meaningfully reduce tax drag on a substantial gain." The investment signal is unchanged — this is a visible context note, never a suppression.

3. **Wash-sale awareness:** If the investor is about to SELL a position within 30 days of a recent add-to-winner on the same ticker, flag the wash-sale window. Awareness-only; the sale is not blocked.

4. **Net-of-cost opportunity cost:** The existing opportunity-cost expander frames missed picks as raw returns. Add a "net of estimated transaction costs" note. Do not estimate taxes in this section — too many unknowns (full-year income, state tax, other losses).

**Governance note:** This concept introduces no new investment-policy thresholds — it is awareness-only and never gates or suppresses an investment signal. G-08 (HARVEST gate) already handles the case where tax harvesting conflicts with a Buy signal. This concept is the complementary exit-direction read. Any constants introduced (e.g., `TAX_LONGTERM_WINDOW_DAYS = 30`, `TAX_RATE_SHORT_TERM`, `TAX_RATE_LONG_TERM`) must go in `constants.py`, flagged as display-only policy, not investment-policy.

**Why meaningfully different:**  
No retail platform overlays holding-period context onto an AI-generated exit signal. Professional private wealth managers do this routinely. For a taxable account, this is likely the highest real-dollar-impact awareness feature available.

**Status:** New. G-08 is the closest existing capability (tax HARVEST suppressed on Buy/Strong Buy). This is the exit-direction complement.

**Data required:** Trade dates and acquisition costs (already in trade journal), current price (live prices), holding period per lot (derived from trades). Tax rate assumptions are constants — never hardcoded values, never computed from the investor's actual tax situation.

**AI methods:** Pure arithmetic. No ML, no LLM. Holding period = today − acquisition date per lot. Wash-sale check = scan trade history for 30-day windows.

**UX:** Holding-period chip (same chip style as other metrics). Amber annotation on relevant exit signal cards. One-line note in the opportunity-cost expander.

**Expected benefit:** For a taxable account with significant embedded gains, avoiding a premature EXIT that triggers short-term vs. long-term rate differential is a real dollar benefit that can dwarf any composite-score-driven alpha in a given year.

**Key risks:** Tax calculation is an estimate. Actual tax depends on full-year income, state tax, other realized losses, and lot-level accounting method (FIFO/SpecID). Must always be framed as directional guidance, never as a precise tax liability. Liability disclaimer should be prominent.

**Risk of false confidence:** LOW-MEDIUM. The estimate is straightforward but materially incomplete without the investor's full tax picture.

**Implementation complexity:** LOW-MEDIUM. Holding period is pure arithmetic from trade dates; wash-sale requires scanning the trade history.

**Differentiation:** HIGH. Not available in Robinhood, Schwab, or any mainstream retail platform as of mid-2026.

---

### Deferred Concepts Worth Naming

These did not make the primary 5 but deserve a named slot so they are not lost.

**D1: Portfolio Tail/Drawdown Probability**  
What is the directional probability of a drawdown exceeding 15% over the next 3 months, given the portfolio's current composition and trailing volatility? Awareness-only; reuses stress test + correlation infra. This is the natural completion of E1 (Forward Portfolio Simulator) in the experimental track. Build E1 first; D1 is the probabilistic extension.

**D2: Correlation Under Stress**  
The portfolio's correlation structure in calm markets differs from stress. Momentum-heavy positions that show near-zero calm-market correlation can converge to 0.9+ in a drawdown. "Your calm-market diversification vs. your stress-tested diversification" is a cheap, decision-relevant comparison that reuses the stress-test infra and is extractable from Concept B's correlation cluster map. Consider pulling this forward as a low-cost addition to the stress test view.

**D3: Precommitment Rules-Contract**  
The Behavioral Fingerprint diagnoses biases (Concept A). The Pre-Mortem adds entry friction (Concept C). Neither lets the investor precommit to a rule they will hold themselves to. Example rules: "Flag any TRIM signal I have ignored for > 5 days and require a written override to continue holding"; "Require a written justification to add to a position with a WEAKENING thesis." Precommitment is the most effective behavioral intervention known — the gap between diagnosing a bias and operationalizing a rule against it. Deferred because it requires the Behavioral Fingerprint to be running first.

**D4: Conviction/Regime-Scaled Position Sizing**  
Vary the `RISK_PCT_PER_TRADE` constant based on: (a) composite conviction tier (all 4 pillars aligned vs. marginal pass), and (b) macro regime (Risk-Off → smaller new positions). This touches a policy constant and requires an explicit user decision + Opus review. Deferred; named here so it is not forgotten.

---

## Part 3: The Investor Intelligence Loop

The current app has most individual capabilities. What is missing is their integration into a learning system that makes the investor progressively more capable.

| Step | Description | Current State | Gap |
|---|---|---|---|
| 1. Observe | Portfolio state + market context | ✓ Complete | Extend to factor tilts (Concept B) |
| 2. Detect | Meaningful changes surface as signals | ✓ Complete | Regime-conditional relevance filtering (Concept D) |
| 3. Interpret | Why the change matters in portfolio context | ⚠ Partial — basis shown but not portfolio-aware | Portfolio-aware recommendation narrative |
| 4. Estimate impact | What does this mean for portfolio-level risk/return? | ⚠ Partial — concentration gate + stress test | Portfolio-as-One view (Concept B); tax-aware exit context (Concept F) |
| 5. Recommend | Possible actions or inaction | ✓ Complete | Pre-Mortem gates prospective buy entries (Concept C) |
| 6. Explain uncertainty | Trade-offs and data quality communicated | ⚠ Partial — fundamentals gate, staleness annotations | Composite score uncertainty ranges; regime uncertainty flagging |
| 7. Record | Decision captured with full context | ⚠ Partial — Trade Journal exists; context snapshot absent | Decision Reconstruction capture starts Phase 1 (Concept E) |
| 8. Evaluate | Outcome measured against expectation | ⚠ Partial — win rate, profit factor, alpha by band | Outcome linked to pre-mortem hypotheses; note: evaluation is directional and benchmark-relative, not a precise error signal — the counterfactual (what would not-trading have returned?) is inherently speculative in a single-portfolio world |
| 9. Learn from history | Patterns detected in individual behavior | ✗ Missing | Behavioral Fingerprint (Concept A) |
| 10. Improve future support | Intelligence adapts to investor's known biases | ✗ Missing | Bias-aware framing/emphasis only — adaptation NEVER touches recommendation ranking or gating (engine stays sole ranker; hard rule) |

**The learning flywheel that does not currently close:** Steps 7 through 10. Every session starts with the same posture regardless of what the investor has learned. This is the primary gap between DRISHTA as it exists and a genuinely adaptive intelligence system.

**Step 10 invariant:** When bias-aware framing is introduced (Phase 3), it can amplify the salience of a signal type that matches a known bias ("Your last 3 delayed TRIM decisions on losing positions led to additional drawdown — act now or record your override"). It must NEVER re-order recommendations, change composite scores, or gate investment actions. The engine is the sole ranker; that is a hard rule.

**Progressive improvement trajectory:**

- Months 1–3: Investor learns the vocabulary of the system. Pre-Mortem adds deliberation. Regime Fit makes positioning visible.
- Months 4–6: Decision context snapshots accumulate. Behavioral log builds. Tax-aware annotations surface on exit decisions.
- Months 7–12: Behavioral Fingerprint starts surfacing directional patterns where sample is sufficient. Portfolio-as-One view matures.
- Year 2: Decision Reconstruction Ledger gives 12+ months of retrospectives. Investor quality improves — not because the app made better recommendations, but because the investor learned to engage with uncertainty more rigorously.

The goal is an investor who, after 18 months, needs the app's hard gates less often because their intuition has been trained by the learning loop.

---

## Part 4: Prioritization Matrix

**Weighting rationale:** Investor value and portfolio risk-reduction weighted highest (the app's stated purpose). Uniqueness and feasibility determine build-vs-defer. Development effort and operating cost determine viability for a single-developer project. Regulatory risk is low-weighted as this is a personal tool. E-capture is scored on capture-only (Phase 1 scope) — its Phase 3 viewer is a separate evaluation.

| Concept | Investor Value /5 | Portfolio Risk /5 | Return Potential /5 | Uniqueness /5 | Feasibility /5 | Data Available /5 | Explainability /5 | Dev Effort /5 (↑=easy) | Op Cost /5 (↑=low) | **Weighted Score** |
|---|---|---|---|---|---|---|---|---|---|---|
| C: Pre-Mortem Protocol | 5 | 4 | 2 | 5 | 5 | 5 | 5 | 4 | 5 | **4.5** |
| D: Regime-Conditional Targets | 4 | 5 | 4 | 4 | 4 | 5 | 4 | 3 | 5 | **4.1** |
| F: Tax-Aware Exit/Harvest Lens | 5 | 4 | 4 | 4 | 5 | 5 | 5 | 4 | 5 | **4.4** |
| A: Behavioral Fingerprint | 5 | 4 | 3 | 5 | 3 | 3 | 4 | 3 | 4 | **3.9** |
| B: Portfolio-as-One | 5 | 5 | 3 | 4 | 3 | 4 | 4 | 3 | 4 | **4.0** |
| E: Decision Reconstruction (capture) | 5 | 3 | 2 | 5 | 5 | 5 | 5 | 5 | 5 | **4.3** |

**Revised priority order: C → F → D → E-capture(passive) → B → A**

**Rationale for key moves:**
- **F (Tax-Aware) moves up:** Pure arithmetic, no new APIs, no new policy constants beyond display-only parameters, directly reduces real dollars lost for a taxable account. Should be in Phase 1.
- **E-capture moves up to Phase 1:** Passive append-only write; no UI required; every month of deferred capture is permanently unrecoverable history. Score above reflects capture-only scope.
- **A (Behavioral Fingerprint) moves down:** Statistically blocked by trade volume for a single retail investor. A solo investor making 5–10 trades/month needs 5–10 months to reach 50+ decision records in a single bias sub-slice. The feature is correct in concept; it is calendar-gated by sample accumulation, not engineering difficulty.
- **B stays mid-ranked:** The factor tilt map requires a returns-based style analysis rewrite from the original FMP `.info` approach. The correlation cluster map (cheapest, most decision-relevant piece) can be extracted and built independently at lower effort.

---

## Part 5: What Not to Build

### 5.1 Options flow intelligence
Noisy for retail interpretation. Professional interpretation requires delta-adjusted exposure, borrow costs, and conviction sizing. Retail "unusual activity" tracking consistently produces poor outcomes and encourages exactly the impulsive chasing the app is designed to prevent.

### 5.2 Social sentiment (Reddit / X / StockTwits)
A contrarian indicator for concentrated stock risk, not a useful signal for patient fundamental investing. The LLM-rescored VADER news sentiment already uses institutional-quality sources. Adding retail social sentiment contradicts the calm-advisor philosophy.

### 5.3 Real-time technical charting
Already done better by TradingView, Yahoo Finance, and every brokerage. Duplicates a commodity feature and encourages short-term pattern-matching behavior the app is designed to discourage.

### 5.4 Automated trade execution
Removes the final human check in the investment process. The Pre-Mortem Protocol deliberately inserts entry friction. Automated execution would eliminate the most important behavioral intervention in the system. Correctly deferred; should remain deferred.

### 5.5 Insider trading / SEC filing alerts
Widely available and adds little edge without institutional-level filtering. Executive sales happen for many reasons. Signal is weak without the professional context to interpret it correctly.

### 5.6 Crypto integration
The existing portfolio is US equities. The app's risk models (beta, sector concentration, earnings calendar, stop logic) do not apply to crypto. Building crypto-aware versions of every module serves a completely different investment thesis.

### 5.7 Short interest tracking
High short interest is an ambiguous signal. Professionals interpret it through borrow costs, squeeze probability, and fundamental disagreement. A retail investor who sees "25% short interest" may read it as either a contrarian opportunity or a danger signal — neither necessarily correctly.

### 5.8 Predictive price forecasting at the individual stock level (Invariant)
Any model claiming to predict future stock prices for individual names makes a claim decades of financial research cannot support. **This is elevated to a product invariant:** no feature in DRISHTA may present a point-estimate expected return for an individual stock. The existing composite score is a relative quality ranking, not a forecast — and it must remain so. Portfolio-level scenario ranges (E1) are sound; stock-level point forecasts are not. This invariant resolves the apparent tension between §1.1 (forward view gap) and this prohibition.

### 5.9 PDF upload for analyst research
The Ideas Inbox already supports paste-based LLM extraction. PDF parsing introduces significant edge-case handling (formatting variability, OCR, multi-column layouts) without proportional benefit. Explicitly deferred; remains deferred.

### 5.10 Tax-loss-harvesting automation
Tax-awareness (Concept F) is a should-build — surfacing holding-period context on exit signals is valuable and safe. Automating tax-loss-harvesting is a different and should-not-build: it changes investment behavior based on tax outcomes, potentially in conflict with the investment thesis (OP-05 already governs this for the Buy side). The distinction: awareness = good; behavioral automation driven by tax = bad.

### 5.11 Multi-account or multi-user generalization
The single-user architecture (single Supabase RLS policy, single secret key, single portfolio) is a load-bearing constraint. Multi-account generalization would require a redesign of the auth/RLS architecture, the concentration gate basis, the portfolio-level calculations, and the AI insights personalization layer. Out of scope and should remain so.

---

## Part 6: Phased Product Roadmap

---

### Phase 1 — High-Value Practical Enhancements
**Target:** 0–60 days  
**Intended outcome:** Every Buy decision carries entry friction and tax context. The passive learning infrastructure is live from day one. The investor takes fewer impulsive decisions and starts accumulating the historical record needed by later features.

**Capabilities:**
- **Pre-Mortem Protocol (Concept C):** Modal on prospective live Buy decisions only — explicitly scoped out of retroactive entries and broker imports. Counterarguments must be position-specific, not generic.
- **Tax-Aware Exit/Harvest Lens (Concept F):** Holding-period chips on positions; amber annotation on exit signals near LT eligibility; wash-sale awareness; net-of-cost note in opportunity-cost expander.
- **Decision Reconstruction capture — passive (Concept E Phase 1):** Append-only context snapshot on every prospective trade write. No UI. Schema-versioned from day one.
- **Behavioral decision log initialization:** Start capturing recommendation-vs-action records systematically. Audit existing historical log completeness before assuming the dataset is ready.
- **Composite score uncertainty indicator:** Surface data-quality context alongside the score ("fundamentals from cache, N days old; sentiment uncertain").
- **Regime-Conditional Targets (Concept D) — policy-setting first:** Before any code, set the target table values with the user in a dedicated conversation. Only after values are agreed, stored in `constants.py`, and Opus-reviewed does the rendering work begin.

**Dependencies:** All data already available. No new APIs.

**Implementation risks:**
- Pre-mortem `st.dialog` is fiddly without local testing — every iteration is a deploy cycle. Budget extra time.
- Concept D requires a policy-setting conversation and an Opus review before the constants commit; schedule that review cycle explicitly.
- Behavioral log audit (week 1): if historical recommendation logs are incomplete, A's timeline slips. Verify before committing to the A schedule.

**Success metrics:**
- Pre-mortem completion rate on prospective Buys (target: >80%)
- Context snapshot capture rate (target: 100% — it is automatic)
- Tax annotation views on exit signal cards
- Time between Grow Today recommendation and Trade Journal entry (should increase slightly — more deliberation)

**Validation approach:** After 4 weeks, compare trade quality for pre-mortem-completed vs. skipped entries.

---

### Phase 2 — Differentiating Intelligence
**Target:** 60–180 days (or when Phase 1 sample thresholds are met)  
**Intended outcome:** Investor can understand portfolio as a unified entity. Tax-aware context is routine. Regime fit is a standing diagnostic.

**Capabilities:**
- **Portfolio-as-One (Concept B — correlation clusters first):** Build the correlation cluster map independently before the full factor tilt view. Cheaper, faster, and immediately decision-relevant.
- **Portfolio-as-One (Concept B — factor tilt map):** Returns-based style analysis against factor ETFs (MTUM/VLUE/QUAL/USMV/VUG). Do NOT use FMP `.info` style tags.
- **Risk budget tracking:** Volatility contribution per position alongside capital contribution.
- **Benchmark comparison — risk-adjusted:** My Edge Benchmark Mirror adds Sharpe-equivalent or beta-adjusted comparison alongside raw return.
- **Regime-Conditional Targets (Concept D) — gap analysis UI:** Only buildable after Phase 1 policy-setting and constants commit.

**Dependencies:** Phase 1 (context snapshot accumulating, behavioral log running, D constants set and reviewed).

**Data requirements:** Factor ETF return series (yfinance, same infra). All other data already available.

**Implementation risks:** Factor tilt estimates are noisy at small portfolio sizes — must be communicated as directional throughout.

**Success metrics:**
- Reduction in portfolio beta exceeding regime target
- Improved Decision Quality Timeline grade trend
- Reduction in entries that increase an already-elevated factor tilt

---

### Phase 3 — Adaptive AI-Driven Capabilities
**Target:** 6–12 months (or when sufficient history has accumulated)  
**Intended outcome:** The system surfaces patterns in the investor's historical behavior. Decisions improve because the system can reference the investor's own record.

**Capabilities:**
- **Behavioral Fingerprint (Concept A):** Activates bias cards only when per-slice sample ≥ minimum threshold. Regime-conditioned. Wired via publish/consume over existing `signal_flow`/`recommendations_history` data.
- **Decision Reconstruction viewer (Concept E Phase 3):** Retrospective "view context" expander on trade rows. Only viable once 6+ months of snapshots have accumulated.
- **Bias-aware framing (not ranking):** If Behavioral Fingerprint shows strong disposition effect, TRIM signals on losing positions gain a context note citing the investor's own record. NEVER re-orders recommendations or changes the composite.
- **Calibration tracking:** Confidence rating at decision time (new optional field on trade record); calibration curve over time; Brier score.
- **Pre-mortem outcome linkage:** 90-day retrospective surfacing of pre-mortem questions alongside actual outcomes.

**Dependencies:** Phase 1 (context snapshots + behavioral log accumulating); Phase 2 (Fingerprint patterns emerging from sufficient history).

**Data requirements:** Context snapshots (Phase 1 capture), trade confidence ratings (new optional field), 90+ days of pre-mortem records.

**Implementation risks:** LLM narration for Decision Reconstruction must be on-demand and cost-bounded. Behavioral Fingerprint must enforce sample-size suppression rigorously — premature pattern labeling erodes trust.

**Success metrics:**
- Behavioral Fingerprint bias trend (target: investor's top-2 biases improving over 6 months)
- Calibration improvement (Brier score declining)
- Reduction in "regret trades" (Buy entries that generate an override or sell within 5 days)
- Pre-mortem accuracy (% of pre-mortem hypotheses that predicted the actual risk)

---

### Experimental Track — High Potential, Validation Required
*Parallel to Phase 2/3. Explicit validation gates before full development.*

**E1: Forward Portfolio Simulator**  
Investor defines (or selects) a forward scenario; app simulates which positions would hit their stops, what gates would fire, and what the surviving portfolio would look like. Genuinely novel; high complexity; high false-precision risk. **Prototype with a single scenario first** ("SPY drops 20% over 6 weeks") applied to the current book before building full multi-scenario UI.

**E2: Personal Alpha Attribution**  
Decompose total return into beta contribution, sector contribution, and pure stock-selection alpha. Requires sufficient trade history and a clean returns series. **Validate on real trade data before building the feature** — beta attribution for a small concentrated portfolio with high turnover is noisy.

**E3: Portfolio Tail/Drawdown Probability (D1)**  
Directional probability of a portfolio drawdown exceeding 15% over 3 months, given current composition and trailing volatility. Awareness-only. Build E1 first; E3 is the probabilistic extension.

---

## Part 7: Measurable Success

| Metric | Definition | Target Direction | How to Measure |
|---|---|---|---|
| Avoidable drawdown rate | % of Act Today SELL signals ignored AND resulting in > EXIT threshold further loss | Decreasing | Recommendation log + outcome tracking |
| Alpha attribution — risk-adjusted | Return in excess of a beta-adjusted SPY benchmark | Increasing | Enhanced My Edge benchmark comparison |
| Concentration discipline | Months where single-name or sector ceiling was breached | Decreasing | Gate breach log |
| Exit discipline adherence | % of TRIM/EXIT signals acted on within next trading session | Increasing | Signal log + Trade Journal cross-reference |
| Tax-avoidable exit rate | % of EXIT/TRIM signals acted on that were within LT-eligibility window when acted vs. when signaled | Decreasing (fewer premature exits in window) | New tax-aware annotation log |
| Impulsive trade frequency | Trades with no preceding app recommendation AND no completed pre-mortem | Decreasing | Trade Journal entries with no linked signal |
| Alert action rate | % of Act Today signals resulting in a logged trade within 2 sessions | Target 70–80% (100% = over-compliance) | Signal log + Trade Journal |
| Noise reduction | % of Act Today items correctly dismissed (no subsequent stop breach) | Increasing | Signal log + price outcomes |
| Decision quality grade trend | Rolling 3-period trend of Decision Quality Timeline grade | Improving over 6-month window | Existing My Edge module |
| Behavioral bias scores | Top 2 bias directional indicators from Behavioral Fingerprint | Improving trend over 6-month window | New behavioral tracking |
| Pre-mortem completion rate | % of prospective Buy entries with completed pre-mortem | Increasing toward 90% | New pre-mortem table |
| Context snapshot capture rate | % of prospective trade writes with attached context snapshot | 100% target (automatic) | New snapshot table |
| Portfolio beta vs. regime target | Gap between current portfolio beta and regime-conditional target | Decreasing | New regime-fit calculation |
| Thesis deterioration accuracy | % of WEAKENING/BROKEN signals that preceded > 8% drawdown | Increasing | Thesis reviews + price outcomes |

**Metrics to explicitly avoid:** page views, sessions per week, time-in-app, number of signals generated, alert volume. These measure engagement, not investor improvement.

---

## Part 8: Final Deliverable

### The five strongest concepts (revised)

1. **Pre-Mortem Protocol (C)** — Highest immediate behavioral impact at lowest build cost. No new data. No new APIs. Addresses the most leveraged point in the investor behavior chain.
2. **Tax-Aware Exit/Harvest Lens (F)** — Largest real-dollar gap for a taxable account. Pure arithmetic. No new APIs. No policy constants beyond display-only parameters. Should have been in the original Sonnet analysis.
3. **Decision Reconstruction Ledger (E — capture phase)** — The cheapest Phase 1 write in this entire plan, and the most time-critical. Every month of deferred capture is permanently unrecoverable history.
4. **Regime-Conditional Position Targets (D)** — Closes the gap between regime detection (already shipped) and actionable portfolio-level guidance. Low marginal build cost once policy constants are set.
5. **Behavioral Fingerprint (A)** — Most differentiated capability in the set. Correct concept; timeline governed by trade volume accumulation, not engineering difficulty.

### The single most differentiated concept

**The Behavioral Fingerprint.** No retail platform personalizes behavioral pattern observation at the individual-decision level. This is what institutional investors pay behavioral finance consultants for. Done correctly — with strict sample suppression, regime conditioning, and directional (not scored) framing — it makes DRISHTA a genuine learning system rather than a recommendation engine.

### The most practical capability to build next

**The Pre-Mortem Protocol**, immediately followed by **the passive Decision Reconstruction capture**. The pre-mortem is visible and user-facing. The capture is invisible to the user but permanent — and the clock is already running.

### The capability most likely to reduce portfolio losses

**Tax-Aware Exit/Harvest Lens (F)** and **Regime-Conditional Position Targets (D)** are the co-leaders. Tax-awareness is the highest real-dollar impact for a taxable account in any single year when significant gains exist. Regime-conditional targets are the highest expected value for preventing structural misalignment losses in a regime shift.

### The capability most likely to improve investor knowledge

**The Decision Reconstruction Ledger.** No better teacher than seeing your own reasoning at decision time alongside the actual outcome — without hindsight bias. After 12 months of capture, the investor will have a forensic record of every significant decision and what the world looked like when they made it.

### The idea that should be prototyped before full development

**The Forward Portfolio Simulator (E1).** The value is real — seeing how your mechanical rules interact with a bear market scenario before it happens is genuinely useful. The false-precision risk is also real. Prototype a single scenario ("SPY drops 20% over 6 weeks"), show which stops would fire and what the surviving portfolio would look like, and validate the concept is useful and interpretable before building multi-scenario modeling.

### The major product risk

**Over-recommendation and alert fatigue.** Every feature added creates more signals. If the signal-to-noise ratio deteriorates, the investor will start ignoring alerts — including the ones that matter. The product risk is not that the app becomes wrong; it is that it becomes unignorable-but-ignored. Every new concept in this plan must be evaluated not just for "does it add value?" but for "does it increase or decrease the total decision load on the investor?"

### Recommended 90-day product discovery and validation plan

**Days 1–14: Audit + foundation**
- Week 1: Audit historical recommendation log completeness (what percentage of signals issued in the last 90 days have matching Trade Journal entries?). This determines whether Behavioral Fingerprint data exists historically or must be built from scratch.
- Week 1 also: Have the Concept D policy-setting conversation. Agree on regime target constants. Schedule the Opus review for the `constants.py` commit.
- Week 2: Design the pre-mortem modal and the passive context snapshot capture. Confirm scope exemptions (retroactive entries, broker imports exempt from pre-mortem).

**Days 15–30: Pre-mortem + capture**
- Week 3: Build and ship the Pre-Mortem Protocol. Ship the passive context snapshot capture simultaneously (they share a Trade Journal write event).
- Week 4: Run both for 10–15 prospective trades. Review pre-mortem counterargument quality — are they position-specific or generic? Calibrate the Haiku prompt. Confirm snapshot captures are complete and schema-versioned.

**Days 31–60: Tax awareness + regime targets**
- Week 5: Build and ship Tax-Aware Exit/Harvest Lens (F). Holding-period chips first; exit signal annotations second; wash-sale detection third. Each is independently shippable.
- Week 6: Begin Concept D implementation (after constants are Opus-reviewed and committed). Regime gap analysis on Risk Analysis page first; change-only Home annotation second.
- Weeks 7–8: Build correlation cluster map (the extractable piece of Concept B). Validate: does it surface any non-obvious position cluster?

**Days 61–90: Validation + Phase 2 prioritization**
- Week 9: Review 4 weeks of pre-mortem data. Which counterargument types proved accurate? Refine the prompt based on outcome patterns.
- Week 10: Assess behavioral log completeness. If 50+ prospective decision records exist, begin Behavioral Fingerprint design. If not, set a sample-gated milestone: "build Behavioral Fingerprint when sample reaches 50 prospective records" — do not calendar-gate it.
- Weeks 11–12: Prioritize Phase 2 roadmap based on learnings. If pre-mortem completion rate > 70%: accelerate Behavioral Fingerprint. If factor tilt analysis proves too noisy: simplify to cluster-only view. If D's regime targets prove too prescriptive after 30 days: relax to directional guidance only.

**Go/no-go gates (sample-gated, not calendar-gated):**
- Pre-mortem completion rate > 70% after 15+ prospective entries → proceed to Behavioral Fingerprint design
- Correlation cluster map surfaces at least one non-obvious position grouping → proceed to full Concept B
- Regime target gap analysis produces ≥ 1 actionable adjustment suggestion in first month → D is delivering value; continue
- Historical recommendation log is ≥ 80% complete → Behavioral Fingerprint can use historical data; otherwise build forward-only from Phase 1 capture

---

*Fact vs. assumption declaration: This analysis is based on the shipped feature set as described in CLAUDE.md and requirements.md (facts), established behavioral finance research and quantitative portfolio management practice (research-grounded assumptions), and directional estimates of differentiation and feasibility for a single-developer Streamlit application (hypotheses requiring validation). No performance claims, vendor capability claims, or model accuracy estimates should be treated as factual without independent validation. Mock statistics used in examples (e.g., illustrative behavioral patterns in Concept A) are illustrative only and are not derived from any real dataset.*

---

**Status:** Revised incorporating all Opus second-pass findings. Ready for discussion and approval.  
**Next action:** Review this document with the user. Agree on Phase 1 scope. Set Concept D policy constants. Begin only after explicit approval.
