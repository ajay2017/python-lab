# DRISHTA · Beyond Noise — User Manual & System Reference

*The single portable map of the app: what every surface is for, how to use it day-to-day, and how it decides behind the scenes.*

> **What this document is.** A navigable master reference that ties together **usage** (Part I) and **mechanics** (Part II), with a grounded **reference** section (Part III). It is a *map*, not a replacement for the authoritative deep docs — where a section needs full depth it points you there:
> - [`docs/requirements.md`](requirements.md) — the functional spec (every F-ID) and operating policy.
> - [`docs/architecture.md`](architecture.md) — data flow, scoring model, DB schema (§6), caching, deployment, AI layer.
> - [`stock_analyzer/constants.py`](../stock_analyzer/constants.py) — **the single source of truth for every threshold.** If a number here ever disagrees with that file, the file wins — fix this doc.
> - [`DEVELOPMENT.md`](../DEVELOPMENT.md) — dev context, secrets architecture.
> - [`docs/shipped-log.md`](shipped-log.md) — full feature changelog.
>
> There is also an **in-app User Guide** (`📖 User Guide` page, four tabs: 🚀 Start Here / ⚙️ How It Works / 🗺️ Features / 📚 Reference) — the same material in short form, read *inside* the app.

---

## Table of contents

- **Part 0 — What DRISHTA is** (philosophy, who it's for)
- **Part I — Using it day-to-day**
  - I.1 First run (get your data in — the one step that everything else trusts)
  - I.2 The daily loop
  - I.3 Page-by-page tour (all 23 pages, in nav order)
- **Part II — How it works behind the scenes**
  - II.1 The composite score (4 pillars)
  - II.2 The daily brief & decision buckets
  - II.3 The new-position pipeline (offense gates)
  - II.4 Exit discipline (defense ladder)
  - II.5 Concentration, sizing & risk
  - II.6 Macro & regime
  - II.7 The data layer (providers, failover, resilience)
  - II.8 Cross-feature coordination
  - II.9 Cron jobs & email alerts
  - II.10 The AI intelligence layer
  - II.11 Persistence (database) & deployment
- **Part III — Reference**
  - III.1 Key policy values (grounded quick-table)
  - III.2 Glossary
  - III.3 Where to go deeper

---

# Part 0 — What DRISHTA is

**A personal portfolio-intelligence advisor for a single user. It *decides*, it does not merely inform.** Recommendations are issued as clear, actionable calls; suppressions are shown with a visible banner explaining what was hidden and why — never silently filtered. When in doubt, it recommends *nothing* rather than recommending wrongly.

**It is built for a medium-term, quality-first investor — not a day trader.** You should not need to watch the screen all day. The app deliberately stays quiet when there's no genuine decision to make ("✅ you're set for today"). A stream of technically-correct-but-trivial prompts is treated as a *bug*, not a feature. (This is the "§2B calm-advisor" posture referenced throughout the code.)

**Two hard architectural commitments shape everything:**

1. **Every decision threshold lives in [`constants.py`](../stock_analyzer/constants.py).** Nothing that gates a recommendation is hardcoded inline. Changing a value there is an *investment-policy decision*, not code tuning.
2. **AI never tunes a gate.** The LLM layer (thesis review, narrative, Q&A) is advisory only — it explains and reflects, it never moves a threshold or originates a call the deterministic engine didn't already make.

⚠️ *Not financial advice. Algorithmic analysis on delayed/third-party data — verify before acting.*

---

# Part I — Using it day-to-day

## I.1 First run — get your data in first

**This is the single most important step, and it comes before trusting anything the app says.** The app *decides* — it issues confident buy/sell/trim calls. If it doesn't know what you actually hold, or has the wrong cost basis, it will be **confidently wrong**: recommending you buy what you already own, miscomputing concentration and position sizing, setting a stop off a bad basis, or misstating P&L. **Garbage in → confident garbage out.**

**Order of operations:**

1. **Log your positions** — `📒 Trade Journal → 📝 Log Trade`. Enter every **BUY** (and any **SELL**) with ticker, shares, **cost basis (price paid)**, and **trade date**. This is the source of truth: holdings, realized P&L, and each position's *age* (the 🌱 settling / lifecycle badges) all derive from these trades and their dates.
   - On a **BUY** you can also write — or ✨ AI-draft — an *investment thesis*; the AI layer then checks weekly whether it still holds.
   - A live BUY also asks you to **🔍 Run Pre-Mortem** (an optional AI-built case *against* the trade) and requires one line: *what would make you wrong.* This friction is by design (F-187 / F-228), never on SELL, never on an imported/backfilled trade.
   - **Faster:** `📥 Import from broker statement` parses a Robinhood account-activity CSV into an editable preview, flags anything already logged so nothing double-counts, and syncs on confirm. (Buy/Sell only for now; log cash events separately.)
2. **Reconcile against your broker.** Confirm share counts and average cost match *exactly*. The Trade Journal runs a **drift check** and flags mismatches (a SELL with no prior BUY, or a stored P&L that disagrees with replayed history); use **🔄 Rebuild holdings & realized P&L** to preview and apply a correction. Wrong basis → wrong P&L → wrong trim/stop advice.
3. **(Optional) Add Watchlist names** — `📋 Watchlist` — anything you track but don't yet hold, so the scanner and brief include them.
4. **Refresh signals** — `🏠 Home → Refresh Signals` — let live prices, composite scores, and risk metrics populate.
5. **Now read the Brief / Grow Today / Risk.** Only after 1–4 is the intelligence computed on *complete, correct* data.

*Persistence:* trades save to your Supabase database and carry across sessions. With no database connected they last only the current session.

## I.2 The daily loop

A normal day is short:

1. **Open `🏠 Home`.** Read the market-tone header, the freshness chip, and the **Today's Brief**. If it says "✅ you're set for today," you're done — that's the app working, not a gap.
2. **Act Today (defense)** — the urgent column. Genuine same-day trade decisions: a stop breach, a deterioration TRIM/EXIT, a critical-news flag. Each item carries a concrete directive (trim X%, raise stop to $Y).
3. **Grow Today (offense)** — new-position setups, market-tone-aware (fewer or none on flat/bear days by design).
4. **Review Before Close** — non-urgent housekeeping/awareness (approaching stops, weak large positions).
5. Drill into anything via the **Analyze** buttons → `📈 Analysis`, or open a specialized page (Risk, Macro, My Edge) only when you want depth.

Pre-market and end-of-day, the **email cron** may have already sent you a protective alert or a morning buy-list — the app and the emails share one engine, so they never contradict.

## I.3 Page-by-page tour

23 pages, grouped exactly as the sidebar groups them. For each: *what it's for · when to look · key tabs · what to do.*

### Group: MAIN

**🏠 Home** — *The command center.* The daily brief (Act Today / Grow Today / Review), market-tone header, live price strip, pre-market intel, and awareness banners (Day Shock F-217, Structural alert F-218). **Look here first, every day.** No sub-tabs — it's a single synthesized surface (memoized via `_home_synth_cache`). What to do: act on Act Today, consider Grow Today, ignore the rest if quiet.

**🧑‍⚖️ The Judge** (F-227) — *Portfolio-level coherence check.* Reconciles the engine's own opinions across dimensions (quality, momentum, position-health, structural-risk, concentration) and flags contradictions. **Read-only, audit-only — it has no authority to gate or override** (Phase 4 scoped to a coherence check after 3 of 4 protective dimensions were found already enforced elsewhere). Look when you want to know "does the engine agree with itself about my book?"

**🧾 Summary** (F-204) — *The at-a-glance dashboard.* KPI tiles, Act Today, holdings table (moved here from Home, F-01), plus pointer cards: Engine Track Record (F-229), State of the Portfolio standing thesis (F-232). Look for a fast status read without the full Home synthesis.

**💰 Account** — *Cash, account value, and capital trend.* Account-baseline: cash + total account value (F-03b), a flows ledger for growth-vs-contributions (F-03c), and a Capital Trend chart with a net-capital-contributed overlay (F-03d). Leverage/margin awareness (F-09d) lives here too — **read-only, never gates.** Look when reconciling real account value or checking margin.

**🔍 Market Scanner** — *Find new names.* Scans the curated universe (88 tickers across 14 sectors, F-60/F-240) plus a Movers discovery lane (F-60a), scores each 0–100 (F-61), ranks the top picks (F-62). Look when hunting for candidates beyond what the brief surfaced.

**📖 User Guide** — *In-app help.* Four tabs (🚀 Start Here / ⚙️ How It Works / 🗺️ Features / 📚 Reference). The short-form companion to this manual.

### Group: RESEARCH

**📈 Analysis** — *Deep-dive one or more tickers.* One outer tab per analyzed ticker; each ticker has inner tabs: **📋 Trade Plan** (Buy/Strong Buy) *or* **🚪 Exit Plan** (Sell/Strong Sell) — the label flips on verdict | **📈 Chart** (candlestick + Bollinger/SMA/RSI) | **⚖️ Risk** (Sharpe/Sortino/drawdown/beta/vol vs SPY) | **🔬 Deep Dive** | **🏦 Analyst Coverage** | **🧾 Prior Trades** (F-237 — *your own* past round trips in this name: entry/exit avg, realized P&L, vs-SPY over each trip's own window, and an expander replaying the thesis, pre-mortem and lesson you wrote at the time; topped by a two-panel journey chart of price+your fills over P&L-while-held, with a dashed "had you held" line after your last exit. Label carries a count. Journal gaps and splits raise visible banners instead of printing a confident wrong number. **Awareness only — never gates.** Phase 2, F-237c: a one-line factual note on the **Trade Plan** / **Exit Plan** tab itself surfaces the same history at the decision moment — round-trip count, net realized, last exit vs today. Facts only: no alpha figure and no verdict on your instinct in the name, which is the deliberately-excluded class. F-237e, 2026-08-25: the "what you wrote at the time" expander also shows the buy's situational-category tag if one was logged.). Includes the 5-chip **gate checklist** (F-163) and a "what would change this signal?" expander (F-162). This is a *research/judgement* surface — some caveats (like entry R:R) are shown as notes, not hard blocks, because here **you decide**.

**⚖️ Compare** — *Two tickers side by side* (F-208). Cites specific tie-break evidence (FCF yield, beta, Sharpe gaps) when composites are close; **never fabricates a pick** (F-209) and shows portfolio-fit awareness without gating (F-210).

**📋 Watchlist** — *Names you track but don't hold.* Composite + key metrics per name (F-101); advisor recs REMOVE / HOLD_OFF_EARNINGS / ENTER_NOW (F-102). **ENTER_NOW hard-gates on composite ≥ 65 (F-103) and a portfolio-risk gate (F-104).** Actionable-first layout (F-219); a "Log Planned Trade" prefill (F-106); resurrection nudge for forgotten-but-now-actionable names (F-203). Offline warning when data is unavailable (F-105).

**🌐 Macro** — *Regime & sector-rotation awareness.* A manual-load regime read (TLT/SPY/VIX proxy, F-211), sector-rotation playbook + portfolio macro-alignment (F-213). ⚠️ Landmine (F-212): this ETF-proxy read is **not** the FRED-based 7-signal regime detector used elsewhere — don't confuse them.

**📊 Predictive Analytics** (F-178) — *Your personal edge map.* Six tabs: 🎯 Score Calibration | ⚖️ Decision Quality | 🏷️ Signal Breakdown | 🌐 Sector Alpha | 🧭 Sentiment Alignment (F-179) | ⏱️ Entry Timing (F-220). Measurement-only — none of it feeds back into the engine. Look to learn where *your* edge actually comes from.

**🔬 Model Lab** (F-234) — *Owner-only, experimental.* Quarantined forward-volatility shadow layer: 20-day EWMA vol forecast per held ticker + portfolio, scored against a naive persistence baseline. Feeds no gate, no recommendation, no composite. Dead-end by design — a measurement harness to validate before any signal is ever wired in.

**🩺 System Trust** (F-235) — *Owner-only, pipeline-health diagnostic.* Answers "can I trust what the app told me today?" Five live checks at page load: ① Cron liveness (did each Railway lane fire?), ② Data stores (does every expected table exist with fresh data — catches unapplied DDL and silent write failures), ③ Data providers (source health this session), ④ In-session caches (what loaded this run), ⑤ Reference data — is any hand-maintained ticker list overdue for a refresh (F-238). Check ⑤ is deliberately **excluded** from the Home chip: it is a standing chore that stays amber for weeks until a human acts, and a permanent amber would train you to ignore the chip that also reports dead cron lanes. Each row is green/amber/red. A one-line chip appears at the top of 🏠 Home only when something is degraded; invisible when healthy. Reports only — changes no recommendation, no gate, nothing.

### Group: PORTFOLIO

**🥧 Portfolio Overview** — *Composition & rebalancing.* Tabs: 📊 Overview (sector pie, P&L bar, Composition Sankey F-06a) | ⚖️ Rebalancing (target weights, trim/add, F-13) | 💰 Tax (per-lot analysis + HARVEST, F-16/F-186) | 📈 Performance (attribution, F-10) | 📈 Analytics (Relative Strength F-171, Sector Rotation Heatmap F-172, Rankings F-173, Portfolio vs S&P 500 real-sector tilt F-223).

**🏆 Health** (F-182) — *Portfolio Construction Health Score.* A single graded read on how well-built the book is (diversification, sizing, concentration).

**🎯 My Edge** — *Are you a good investor, and how?* Six tabs: 📐 Benchmark Mirror (beta-adjusted alpha, F-183) | 🔬 Workflow ROI (does in-app research before a buy pay off? F-184) | 📅 Decision Quality (graded timeline, F-185) | 🧬 Behavioral Fingerprint (your buy-side patterns, F-193; mirrors live at log-time, F-231) | 🪞 Investor Mirror (conviction alignment + behavioral biases, F-194) | 🧭 Self vs Engine (**Self Track Record** F-233 — is *your own instinct* good vs following the app's calls?).

**🔗 Risk Analysis** — *Portfolio risk.* Tabs: 📊 Dashboard (Sharpe/Sortino/drawdown/beta/correlation, F-09; fragility gauge F-09a; risk-posture dial F-09b; Cross-Asset Pulse F-09c; rate-sensitivity F-87a) | 📋 Action Plan (risk advisor flags F-12; Regime Fit F-188) | 🔥 Stress Testing (macro scenarios F-14, historical replay F-14a/F-168, adversarial F-200) | 🎲 Outcome Range (block-bootstrap Monte Carlo, F-224) | 🧯 After My Rules (F-245 — replays the app's own stops/ladder/risk-off overlay against a shocked book and reports the surviving portfolio; read-only diagnostic, issues no directive). The three scenario tabs read in order: 🔥 price damage → 🎲 outcome spread → 🧯 what your own rules then do about it.

**🧩 Intelligence** — *Structural portfolio intelligence.* Tabs: 🕸️ Correlation Clusters (F-189) | ⚖️ Risk Budget (F-190) | 📐 Factor Tilt (returns-based style analysis, F-191) | 🧬 Structural Scan (vulnerability scanner F-198; hidden same-bet detector F-199) | 🧭 Signal Coherence (F-202).

**📒 Trade Journal** — *Log trades & learn from them.* Tabs: 📝 Log Trade (with a trade-date picker F-80a, Decision Context capture F-82, Pre-Mortem F-187, Lessons Learned library F-195) | 📊 Performance | 📋 History. Includes SELL integrity guard (F-81a), double-submit dedupe (F-81b), broker import (F-87), Opportunity Cost expander (F-164), Engine Trust by Band (F-165).

**🪞 Trade Review** — *Retrospective behavioral lens.* Buckets your past trades (e.g. panic-day trades) to surface patterns and costly deviations (F-84/F-85).

**📜 Recommendations History** — *Did the engine's calls work?* Tabs: 📊 Summary | 📈 Trends | 📋 Full Table. Grades surfaced recs on realized outcome/alpha once matured (≥5 days, `REC_SCORE_MIN_DAYS`); younger recs show "maturing." Signal-flow Sankey (F-160a), Missed Opportunity analysis (F-161/F-201).

### Group: SIGNALS

**📡 Signals & Advice** — *Standalone advice feed.* Tabs: 📡 Active Signals (alerts, custom price alerts F-169/F-170) | 🧩 Diversification (gaps + ADD recs, F-13a/F-222). Fed by the standalone risk/diversification caches.

**🔔 Catalyst Watch** (F-37a) — *Forward earnings awareness.* Tabs: 📋 Positions (held names reporting soon) | 📡 Radar | 🧭 Entry Candidates (Earnings Catalyst Scanner, F-37b). Awareness only — it does **not** recommend buying into earnings.

**📅 Economic Calendar** — *Upcoming macro events.* Tabs: 📅 Calendar (FOMC/CPI/NFP/GDP ahead, F-90) | 📋 Pre-Event Playbook | 📊 Post-Event Results. Event-relevance-to-holdings (F-91).

### Group: AI

**🧠 AI Insights** — *The LLM advisory layer.* Seven tabs: 🩺 Positions (thesis review INTACT/WEAKENING/BROKEN, F-151; behavioral KPI strip F-166) | 📅 Debriefs (weekly F-152) | 🏦 Research (Ideas Inbox F-154) | 📊 Scorecard (analyst call accuracy F-154c — KPI row, per-call table, firm leaderboard, best/worst calls, plus a **Phase 3 "⚖️ Engine vs Analyst Calibration" 2×2** showing whether saved analyst consensus or the engine composite called it right when the two disagreed at save time) | ⚠️ Red Team (thesis counter-evidence, F-196) | ⚔️ Debate Log (multi-agent debate, F-197) | 💬 Ask (Portfolio Q&A, F-225). **Every surface here is advisory — it narrates and reflects, it never originates or gates.**

---

# Part II — How it works behind the scenes

## II.1 The composite score (4 pillars)

Every ticker gets a **0–100 composite** (`scoring.combined_score`) from four weighted pillars (`COMPOSITE_WEIGHTS`, must sum to 1.0):

| Pillar | Weight | What it measures |
|---|---|---|
| Business quality | **0.35** | Revenue/earnings growth, margins, debt (`fundamentals.business_quality_score`) |
| Valuation | **0.30** | Forward P/E, FCF yield, analyst PT upside + consensus (`valuation.valuation_score`) |
| Technical | **0.25** | Momentum/trend (labelled **"Technical"**, never "Momentum" — that collides with the scanner) |
| Sentiment | **0.10** | VADER news sentiment, LLM-rescored (bounded ±0.5, `SENTIMENT_LLM_MAX_SWING`) |

The composite maps to a label everyone downstream imports (`scoring.recommendation`):

| Composite | Label |
|---|---|
| ≥ **75** (`COMPOSITE_STRONG_BUY`) | Strong Buy |
| ≥ **65** (`COMPOSITE_BUY`) | Buy — *the entry & add-to-winner gate* |
| 44–64 | Hold |
| 30–43 | Sell |
| < **30** (`COMPOSITE_SELL`) | Strong Sell |

**Fundamentals gate:** if fewer than `FUNDAMENTALS_GATE_MIN_METRICS` (=1) core BQ metrics are available anywhere (yfinance sparse *and* no failover backfill), the verdict is **WITHHELD** with a visible note rather than emitting a fabricated neutral-50 (F-43a). A last-known-good fundamentals cache serves real-but-aged data up to `FUNDAMENTALS_CACHE_MAX_AGE_DAYS` (=7) before withholding.

## II.2 The daily brief & decision buckets

`daily_briefing.build_daily_briefing()` synthesizes the Home brief and splits into buckets via `decision_bucket.classify_bucket`:

- **Act Today** — a genuine trade decision today (stop breach, deterioration TRIM/EXIT, critical news). Two policy flags govern borderline items: stop-raise nudges → Awareness (`BUCKET_TIGHTEN_ONLY_IS_ACT=False`); critical-news → Act Today (`BUCKET_CRITICAL_NEWS_IS_ACT=True`). Unknown kinds default to Awareness (calm).
- **Grow Today** — new-position offense (see II.3).
- **Review Before Close** — non-urgent awareness (approaching stops, weak large positions).

Market tone from the S&P 500 daily move selects the offense posture: **bull** > +0.5% (`MARKET_TONE_BULL_PCT`), **bear** < −0.5% (`MARKET_TONE_BEAR_PCT`), else flat. This single source is shared by the interactive app *and* the headless cron so they can't drift.

**Calm-advisor damping:** signal hysteresis tags a pick "steady vs yesterday" when its composite moved ≤ `HYSTERESIS_COMPOSITE_DELTA` (=4) points (F-25c); position lifecycle suppresses routine nudges on positions held < `POSITION_SETTLING_DAYS` (=10) (F-25b).

## II.3 The new-position pipeline (offense gates)

Grow Today recommends **at most** `GROW_MAX_PICKS_BULL` (=3) new positions on bull days, `GROW_MAX_PICKS_DEFAULT` (=1) on flat days, and **zero on bear days** (capital-preservation). Candidates are over-fetched (`GROW_CANDIDATE_POOL`=12) so enough survive the gates. A candidate must clear a stack of hard gates (surfaced as the 5-chip checklist, F-163):

1. **Composite ≥ 65** (`COMPOSITE_BUY`), raised to **78** on flat days (`COMPOSITE_BUY_FLAT_DAY`).
2. **Data freshness** — fundamentals no older than `GROW_TODAY_MAX_FUND_AGE_DAYS` (=2 days), stricter than held-position display (F-180).
3. **Macro-sector suppression** — a HIGH-impact macro event ≤ `MACRO_IMMINENT_DAYS` (=3) in the candidate's sector hard-suppresses it (F-39a).
4. **Concentration** — suppressed at/above the sector cap (F-39e); add-to-winner blocked above the single-name ceiling (see II.5).
5. **Entry reward:risk** — the Watchlist ENTER_NOW path hard-gates on R:R ≥ `RR_ENTRY_MIN` (=2.0); Analysis surfaces it as a caveat, not a block.

Movers (breakouts outside the tracked universe, ≥ `MOVER_MIN_DAY_GAIN_PCT`=5% one-day) get their own small allowance (`MOVER_MAX_PICKS`=3) and are exempt from the flat-day high-conviction suppression but still respect the macro/composite/bear gates.

## II.4 Exit discipline (defense ladder)

The missing middle between "Hold" and a score-collapse "Sell" — a held name can bleed 15–25% while its composite sits in Hold (44–64) and nothing fires. `exit_advisor.assess_holding` runs a 3-tier drawdown-from-peak + trend-break ladder (F-25d):

| Tier | Trigger (base) | Bucket |
|---|---|---|
| **WATCH** | drawdown ≥ `DETERIORATION_WATCH_DD_PCT` (6%) + close < SMA50 | Awareness only |
| **TRIM** | drawdown ≥ `DETERIORATION_TRIM_DD_PCT` (8%), ATR-scaled, confirmed | Act Today |
| **EXIT** | drawdown ≥ `DETERIORATION_EXIT_DD_PCT` (12%) *or* unrealized loss ≥ `DETERIORATION_EXIT_DOLLAR_LOSS` ($250) | Act Today, reduce aggressively |

TRIM/EXIT floors are **ATR-scaled** (a quiet name trips tight, a jumpy one gets room) but capped by ceilings (`..._CEILING`) so volatility can't disable the stop on the high-beta names that cause the biggest losses. TRIM needs `DETERIORATION_CONFIRM_REQUIRED` (=2) of `DETERIORATION_CONFIRM_DAYS` (=3) sessions below the MA; a deep EXIT does not.

**Stops & profit-lock:** initial/trailing stop = price − `ATR_STOP_MULT` (2.0) × ATR; tightening uses 1.5× (`STOP_TIGHTEN_ATR_MULT`). A **profit-lock ratchet** (`STOP_RATCHET_LEVELS`) floors the stop as gains grow — protect 40% of a gain at +75%, 25% at +50%, 10% at +25%, breakeven guard at +10%.

**Risk-off overlay (Phase 2):** in a genuine market-wide risk-off *regime* (SPY < 200-day MA `RISK_OFF_TREND_MA`, or VIX ≥ `RISK_OFF_VIX_LEVEL`=25) **and** a fragile book, promote awareness to a concrete per-holding TRIM on the top-`RISK_OFF_TRIM_TOP_N` (=3) high-beta contributors (β ≥ 1.2). Fires only in a regime, never on a single down day (that would sell the dip).

## II.5 Concentration, sizing & risk

**Concentration caps** (`concentration.py`): single-name ceiling **15%** (`SINGLE_NAME_CEILING` — no add-to-winner above), sector ceiling **35%** (`SECTOR_CEILING`), soft warns at 25%. A position that grew past the ceiling on price appreciation triggers a soft trim at 18% (`SINGLE_NAME_TRIM_TRIGGER`). Gates use a **"tighter-of-both" basis**: margin (net cash < 0) tightens the caps to a net-capital denominator; cash never loosens them. A cash figure older than `ACCOUNT_CASH_STALE_DAYS` (=7) degrades to equity-basis. The "Other" bucket (`UNCLASSIFIED_SECTOR`) is a grab-bag, not a real sector — gates exclude it.

**Position sizing:** `RISK_PCT_PER_TRADE` = **1.5%** of portfolio risked per trade (Moderate). Add-to-winner requires the position sit ≥ `ADD_WINNER_MIN_GAP_PCT` (8%) above its stop, and a 10-day cooldown after an add (`ADD_WINNER_COOLDOWN_DAYS`) to avoid re-nudging a buy you just made.

Every surface that suggests a share count — Grow Today new picks, add-to-winner cards, Analysis, Watchlist, and the cron emails — routes through **one** function, `risk.position_sizing()`, off the same ATR stop (`price − ATR_STOP_MULT × ATR(14)`). Size is the risk budget divided by the per-share risk, then **capped at `SINGLE_NAME_CEILING` (15%)**; when the cap binds, the card says so and states what the uncapped risk-budget size would have been. **A suggested size is a per-idea maximum, not a portfolio plan** — at 15% each, a book funds ~6 concurrent full positions, so the sizes are not meant to be taken simultaneously across every idea on screen. (Before F-249, Grow Today used a separate uncapped estimator and suggested 18.75–30% of the book; see the F-249 row in [requirements.md](requirements.md).)

**One caveat on adds — the cap is applied to the add in isolation, not to the resulting position.** For an add-to-winner the 15% ceiling bounds *the shares being added*, not (ceiling − current weight), so adding a full-size tranche to a position already near the ceiling can leave you above it. This is a known gap recorded in [architecture.md](architecture.md); it is a strict improvement over the previous uncapped behaviour, but do not read "capped at 15%" as a guarantee that an add cannot take a single name past 15%. The concentration gates on 🔗 Risk Analysis and the Rebalancer still measure the *resulting* position and will flag it.

**Four** cases suggest **no size at all**, each with a caption saying which: a price at/below the name's ATR stop (no room between entry and stop to size against); **the app not knowing your portfolio value in this session** (any share count would be a guess — open 🏠 Home to load it); one share already exceeding the 15% single-name ceiling (an account-size constraint — no stop change fixes it); or one share exceeding the separate 25% net-capital cap. None of them ever renders a blank size line silently. The app refuses to propose a size rather than propose a wrong one — the same rule that makes it withhold a smaller size when a cap cannot afford a single share.

The Grow Today footer line states **risk**, not capital: "at 1.5% risk per trade across N setups, you'd be risking ~$X if every stop hits." That figure is the total you lose if every stop triggers — it is *not* the cash to deploy, which is the sum of the individual card costs and is far larger.

**Portfolio risk** (`risk.py` / `risk_advisor.py`): beta target 1.0, soft-warn > 1.3 (`PORTFOLIO_BETA_ELEVATED`), hard breach > 1.4 (`PORTFOLIO_BETA_CEILING`). Sharpe/volatility/drawdown/tail-risk each fire a tiered HIGH/MEDIUM action or a congratulatory OK card off their own constant ladders (e.g. Sharpe: HIGH < 0.4, action < 0.8, OK ≥ 1.0). **Fragility gauge** (F-09a) measures how a routine −10% pullback (`FRAGILITY_PULLBACK_PCT`) would hit *this* book — exposure, not a forecast of timing.

## II.6 Macro & regime

Two distinct regime reads (don't confuse them):

- **FRED-based 7-signal classifier** (`macro_calendar.detect_macro_regime`) — CPI YoY ladder (controlled ≤ 2.5%, elevated ≥ 3.0%, hot > 4.0%), Fed Funds trend, 2s10s curve, unemployment delta, HY credit spreads, SPY 20-day, VIX. Produces rate_cut / neutral / inflation_fight / recession_fear / stagflation_risk. Feeds **Regime-Conditional Targets** (F-188): a diagnostic beta-ceiling and cash-floor per regime (`REGIME_BETA_CEILING`, `REGIME_CASH_FLOOR_PCT`) — **never gates/resizes**.
- **ETF-proxy legacy read** (`macro.detect_macro_regime_legacy`, TLT/SPY/VIX) — the manual-load `🌐 Macro` page read (F-211). Landmine F-212: not the FRED detector.

Macro-event protective trims fire only when an affected sector is concentrated (> `MACRO_AFFECTED_TRIM_THRESHOLD_PCT`=30%) and a HIGH-impact event is imminent; portfolio-wide events (> 60%, `MACRO_BROAD_EXPOSURE_PCT`) downgrade to an awareness WATCH instead of pre-event churn. Cross-Asset Pulse (F-09c) scores 0–5 stress signals (HYG, copper, DXY, VIX term structure, yield curve).

## II.7 The data layer (providers, failover, resilience)

Multi-source, orchestrated in `data.py` over `providers/` (`DATA_MULTISOURCE_ENABLED=True`):

- **General data** (history/bundle/indices/risk-free): order `yahoo_finance → finnhub → fmp` (`DATA_PROVIDER_ORDER`). Yahoo primary (free, unquota'd, only free-tier history source).
- **Live price:** order `finnhub → yahoo_finance → fmp` (`DATA_LIVE_PRICE_ORDER`) — Finnhub primary because its free tier serves *real-time* US quotes (yfinance is ~15-min delayed).
- **Cross-check** (`DATA_XCHECK_FIELDS={"price"}`): prev_close must match within 0.5% (strict — a breach means a real fault: missed split, wrong symbol, poisoned feed); live price checked loosely within 3% (latency-tolerant). F-123/F-123a.
- **Resilience:** last-known-good bundle cache serves real-but-aged data up to `BUNDLE_CACHE_MAX_AGE_DAYS` (=5) with a staleness banner rather than "Could not load." Provider circuit-breaker skips a red provider for `PROVIDER_RL_COOLDOWN_SEC` (=120s). Refresh buttons cool down for `REFRESH_COOLDOWN_SEC` (=60s). FMP free tier soft-capped at 220/250 calls/day. Concurrency held to `DATA_LOAD_MAX_WORKERS` (=2) with staggered starts to stay under Yahoo's burst throttle.

**Market calendar:** hardcoded NYSE holidays + early closes, 2026–2028 (`MARKET_CALENDAR_LAST_YEAR`=2028). ⚠️ **Extend before 2029** or the UI warns `calendar_stale`. All date math is America/New_York via `market_time`.

## II.8 Cross-feature coordination

Features that own state publish to `st.session_state`; downstream features read and gate. **When a producer fails, the cache is set to `None` (not an empty container)** so consumers detect "offline" rather than silently disabling gates — read via `stock_analyzer.util.get_or_offline`, never `.get(...) or []`. Example keys: `_port_df_enriched`, `_reduce_calls` (tickers under an active Reduce/Exit, consumed by Analysis/My Edge/Opportunity Signals/Rebalancer to suppress conflicting ADD suggestions), `_home_synth_cache` (Home synthesis memoization — new Home inputs *must* join its signature or ship stale), `_dpnl_cache` (Today's P&L, so Summary matches Home). Full list in [CLAUDE.md](../CLAUDE.md#coordination-pattern).

**Rule:** any new advisor must check whether its decision overlaps another feature's; if so, wire coordination via the same publish/consume pattern.

## II.9 Cron jobs & email alerts

Headless runs (`cron_runner.py`, shares the app's exact data + credential path). Historically these always exited 0 so a failure could never block — **F-239 changed that deliberately**: a lane that cannot reach Supabase now emails the owner, records `status="failed"` on its heartbeat, and **exits 1**, so a silent outage can't look like a quiet day. Everything else still fails safe.

| Job | Purpose |
|---|---|
| `_run_premarket` | Pre-market protective alert email (exit-discipline Phase 3, F-140) |
| `_run_eod` | End-of-day snapshot + reactive pullback email (F-141/F-142); writes `daily_snapshots` + `sentiment_history` |
| `_run_scan` | Mid-morning scan → morning buy-list email ("offense," F-146/F-147); persists `new_pick` recs even with no interactive session |
| `_run_intraday` | Intraday pullback-entry alert lane (Phase 3) |
| `_run_thesis` | Weekly thesis review (INTACT/WEAKENING/BROKEN, F-151) |
| `_run_debrief` | Weekly Portfolio Debrief email (F-152) |
| `_run_monthly_report` | Monthly Intelligence Report (F-153) |
| `_run_maintenance` | Saturday upkeep lane: ⓪ ticker-liveness sweep across all three reference rosters (F-241), ① analyst-coverage anchor-price backfill, ② `model_predictions` historical backfill. ⓪ runs FIRST on purpose — it needs no DB, and ① can return early on a Supabase outage |
| `_run_test_email` | Delivery smoke test |

Emails delivered via Resend with per-ET-day idempotency + DST safety (F-143/F-144). Protective email targets `ALERT_EMAIL_HOUR_ET` (=8am ET); EOD fires after `ALERT_EOD_HOUR_ET` (=4pm ET) and only on a ≥ 3% index down day (`PULLBACK_ALERT_INDEX_PCT`).

## II.10 The AI intelligence layer

Advisory-only LLM surfaces (`architecture.md §12`), each with a hard boundary: **it never tunes a gate or originates a call.** Tiered by cost:

- **Haiku (fast track):** extraction & scoring — Ideas Inbox fact extraction (F-154), sentiment rescore (Phase 2b), thesis-red-team counter-evidence (F-196).
- **Claude (deep track):** reflection & narrative — thesis authoring/review (F-5/F-151), weekly debrief (F-152), monthly report (F-153), Portfolio Q&A (F-225, retrospective — narrates history, never a live session reader), multi-agent debate (F-197).

Per-request timeout `LLM_REQUEST_TIMEOUT_SEC` (=30s); a timeout just yields the offline/None fallback. Monthly report suppresses narration below `MONTHLY_REPORT_MIN_GRADED` (=5) matured entries rather than commenting on 1–2 data points.

## II.11 Persistence (database) & deployment

**Database:** Supabase Postgres, 30 tables (`architecture.md §6.1–6.30`). Core: `holdings`, `watchlist`, `trades` (source of truth), `manual_stops`, `daily_snapshots`, `recommendations`, `analyst_coverage`, `exit_signals`, plus caches (`bundle_cache`, `scanner_cache`, `sector_cache`, `fundamentals_cache`) and feature tables (`thesis_reviews`, `judgment_opinions/_grades`, `debate_cache`, `structural_scan_cache`, etc.).

- **New columns must be backward-compatible** — `db.load_trades()` backfills `None` for legacy rows; additive columns are dropped-and-retried by the writer until the DDL is applied.
- **RLS is always on** — every table is `FOR ALL TO service_role`. The Streamlit secret `[supabase] key` must be the **service-role/secret** key. "RLS blocking" errors → swap secrets & reboot, never disable RLS.

**Deployment:** primary is **Railway Hobby** (`drishta.up.railway.app`), auto-deploying from `main` — cut over 2026-08-15. **Streamlit Community Cloud** is kept as a **dormant cold fallback** on the same Supabase DB; it still auto-deploys, but it is not the deploy you verify against. **Never run locally** — both deploys assume hosted secrets delivery. To ship a change: push to `main`, wait ~2 min for auto-redeploy, hard-refresh (Ctrl+F5). Single-user, gated by a password screen; all secrets in the Cloud dashboard.

---

# Part III — Reference

## III.1 Key policy values (grounded quick-table)

The decision-critical constants, transcribed from [`constants.py`](../stock_analyzer/constants.py). This is a *curated* subset — the file is the full, authoritative source (and `docs/architecture.md` carries the complete constants table).

| Constant | Value | Governs |
|---|---|---|
| `COMPOSITE_STRONG_BUY` | 75 | Strong Buy boundary |
| `COMPOSITE_BUY` | 65 | Buy / entry / add-to-winner gate |
| `COMPOSITE_HOLD` | 44 | Hold floor (below = Sell zone) |
| `COMPOSITE_SELL` | 30 | Strong Sell floor |
| `COMPOSITE_BUY_FLAT_DAY` | 78 | Stricter flat-day entry bar |
| `COMPOSITE_WEIGHTS` | BQ .35 / Val .30 / Tech .25 / Sent .10 | Composite pillar weights |
| `SINGLE_NAME_CEILING` | 15% | Hard single-name cap |
| `SINGLE_NAME_TRIM_TRIGGER` | 18% | Soft trim trigger |
| `SECTOR_CEILING` / `SECTOR_ELEVATED` | 35% / 25% | Sector cap / soft warn |
| `PORTFOLIO_BETA_TARGET/_ELEVATED/_CEILING` | 1.0 / 1.3 / 1.4 | Portfolio beta bands |
| `RISK_PCT_PER_TRADE` | 1.5% | Risk per trade (sizing) |
| `RR_ENTRY_MIN` | 2.0 | Min entry reward:risk (ENTER_NOW gate) |
| `ATR_STOP_MULT` / `STOP_TIGHTEN_ATR_MULT` | 2.0 / 1.5 | Stop width / tighten |
| `DETERIORATION_WATCH/TRIM/EXIT_DD_PCT` | 6 / 8 / 12% | Exit ladder drawdown floors |
| `DETERIORATION_EXIT_DOLLAR_LOSS` | $250 | TRIM→EXIT escalation |
| `POSITION_SETTLING_DAYS` | 10 | Settling grace (suppress nudges) |
| `ADD_WINNER_MIN_GAP_PCT` / `_COOLDOWN_DAYS` | 8% / 10 | Add-to-winner gap / cooldown |
| `GROW_MAX_PICKS_BULL/_DEFAULT` | 3 / 1 | New-pick cap (bull / flat; bear = 0) |
| `GROW_TODAY_MAX_FUND_AGE_DAYS` | 2 | New-position data freshness |
| `MARKET_TONE_BULL/BEAR_PCT` | +0.5 / −0.5% | Bull/bear/flat S&P tone |
| `FUNDAMENTALS_GATE_MIN_METRICS` | 1 | Withhold verdict below this |
| `RISK_OFF_TREND_MA` / `RISK_OFF_VIX_LEVEL` | 200d / 25 | Risk-off regime gate |
| `DAY_SHOCK_PCT` | 5% | Day-shock awareness banner |
| `REGIME_CPI_CONTROLLED_MAX` | 2.5% | Rate-cut regime gate ceiling |
| `DATA_XCHECK_PREVCLOSE/LIVE_TOL_PCT` | 0.5% / 3% | Price cross-check tolerances |
| `BUNDLE_CACHE_MAX_AGE_DAYS` | 5 | Max staleness before "Could not load" |
| `MARKET_CALENDAR_LAST_YEAR` | 2028 | ⚠️ extend before 2029 |

## III.2 Glossary

- **Composite** — the 0–100 four-pillar quality+value+technical+sentiment score; the spine of every verdict.
- **Act Today / Grow Today / Review Before Close** — the three daily-brief buckets (urgent defense / offense / non-urgent).
- **Deterioration ladder** — WATCH → TRIM → EXIT, the drawdown-from-peak exit-discipline tiers.
- **Fragility gauge** — how a routine −10% pullback would hit *your* book (exposure, not timing).
- **Regime** — market environment (rate-cut / inflation-fight / recession-fear / …); the FRED 7-signal read is authoritative, the ETF-proxy read is the manual Macro-page version.
- **Gate** — a hard suppression of a recommendation, always shown with a visible banner.
- **Settling grace** — routine management nudges are suppressed on positions held < 10 days.
- **Offline sentinel** — a producer that fails sets its cache to `None`; consumers detect it and don't silently drop a gate.
- **§2B** — the calm-advisor operating posture (medium-term, not day-trading; silence when nothing matters).

## III.3 Where to go deeper

| You want… | Go to |
|---|---|
| The exact wording of any feature (F-ID) | [`docs/requirements.md`](requirements.md) |
| Full scoring model, DB schema, data flow, AI layer | [`docs/architecture.md`](architecture.md) |
| Every threshold + its rationale | [`stock_analyzer/constants.py`](../stock_analyzer/constants.py) |
| The complete constants table | `docs/architecture.md` constants table |
| Dev setup, secrets architecture | [`DEVELOPMENT.md`](../DEVELOPMENT.md) |
| Full feature changelog | [`docs/shipped-log.md`](shipped-log.md) |
| What's automated vs manual testing | [`docs/testing-strategy.md`](testing-strategy.md) |
| Project rules for anyone (incl. Claude) working here | [`CLAUDE.md`](../CLAUDE.md) |

---

*This manual is a synthesis layer. When a feature or threshold changes, the authoritative docs above are updated first; re-sync the affected row here in the same pass so the map never drifts from the territory.*
