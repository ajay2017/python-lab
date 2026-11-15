# DRISHTA — Strategic review: from investment intelligence to financial independence

**Status 2026-11-15: DEFERRED by the owner. No changes now; revisit ~2027-02-15.** Owner's words: "keep it documented and we can come back after a few months to make these changes… I believe the App has the capability and provides the investor data as expected." Nothing in this document is approved for build. When it is picked up: re-measure first (§7), then the owner picks the order, then each item goes through `planner` → `implementer` → `reviewer` as CLAUDE.md requires.

- **Date:** 2026-11-15, HEAD `d583b1b`.
- **Reviewer:** Claude Opus 5.5 (`claude-opus-5-5`), lead session. Assessment only: no code, schema or config changed.
- **Asked by:** the owner, "Strategic App Review: From Investment Intelligence to Financial Independence" (five dimensions: FI alignment, decision quality, risk and capital protection, time efficiency, UX and strategic gaps).
- **Method:** read the code at HEAD, plus the real-data analyses already on record (Robinhood statements, owner-run SQL, the 2026-09 Investor Maturity Roadmap). The live database was **not** queried for this review, so account figures are the latest recorded ones (up to 2026-11-09). Re-measure before acting.

---

## 1. Summary

DRISHTA is a strong daily decision engine for picking and managing individual stocks, and its entry calls measurably beat the owner's own. The data the investor needs is largely there.

What the review found is not missing data but missing **authority** in three places. The biggest measured risks to this account are leverage, one concentrated high-beta theme, and short holding periods. Those are exactly where the app informs rather than decides. Home says so in its own words: *"Awareness only; this never changes a recommendation"* ([app.py:8150](../../app.py#L8150)).

The September roadmap reached the same place: *"§2B omits the two highest-variance terms — leverage and concentration — which is why 'defensive' design choices throughout the app are calibrated against the wrong risk"* (`docs/plans/investor-maturity-roadmap.md`, §2).

---

## 2. Owner decisions (2026-11-15)

| Question | Owner's answer | What it means for the plan |
|---|---|---|
| What is this account for? | A hobby and a side hustle to make extra money toward financial independence; horizon "medium to long term" | Confirms `requirements.md` §2B; no persona change. The gap is between that stated horizon and the measured 7-day median hold (§3) |
| Other accounts? | A 401(k) and others exist, but they are independent of this app | No FI planner or multi-account layer. The yardstick for "extra money" is beating the same money in an index fund, after interest and tax |
| Leverage policy | Situational: "based on how the market is performing… control the leverage looking at the runway so as to avoid a margin call" | No fixed cap. A limit that moves with market conditions, with runway (distance to a margin call) as the hard floor. The shape in §4.1 is approved; the values are not set |
| "Direction" | Leaned "trade setup, since that's what makes the money" | Lead's reconciliation (not yet confirmed by the owner): the market and runway set the account's total limit; setup strength decides which trades get that room first. A strong setup should not raise the total limit (§4.1) |
| Fun budget for short trades | "I think yes" (tentative) | Part of §4.2; size and tagging to be designed |
| Broker fill record (`docs/plans/broker-fill-record.md`) | No: manual entry of broker trades is fine for now | Stays queued; not re-proposed without a fresh ask |
| Deposits | None since the initial deposit on 2026-06-25. Hesitant to add capital because growth hasn't been substantial | The deposits ledger is correct. The scorecard (§4.3) should answer this capital question directly |

---

## 3. Evidence: the investor using the app

Every figure is from a recorded analysis; date and source are given. None was re-queried for this review.

| Measure | Value | Source |
|---|---|---|
| Own capital | $7,582 at the start of October | 📄 Reports, October period, verified 2026-11-09 |
| Leverage (measured) | 3.15× on 2026-08-23 ($24,503 invested on $7,802 capital, $16,701 margin debit) | Margin-maintenance measurement, 2026-08-23 |
| Leverage (peak) | 3.73× on 2026-09-30, call distance −2.37% at the flat 25% maintenance assumption | Q3 review, 2026-10-01 |
| Leverage (latest recorded) | 3.04× on 2026-10-19 | Loss attribution, 2026-10-19 |
| Book-basis caps vs capital (at 3.15×) | 15% single-name cap = 43.2% of capital; 35% sector cap = 100.9% of capital | 2026-08-23 measurement |
| Median holding period | 7 calendar days; 63.3% of round trips closed within 10 days (139 lots); unchanged on 145 lots | A4, 2026-09-13; re-run 2026-09-17 |
| Who closes positions | 74% of exits self-initiated, at every holding length (N=80) | A4 re-run, 2026-09-17 |
| Q3 2026 return | +19.4% ($5,455.83 → $6,516.68, no deposits) vs SPY about +2.5% (the Reports tile read +2.52%) | Q3 review; Robinhood 6/30 statement confirmed |
| Late-September giveback | −18% in the final week (peak $7,964 on 9/23 → $6,517 on 9/30) | Q3 review |
| October 2026 | −8.47% (−$642) vs SPY −0.91% | 📄 Reports, verified 2026-11-09 |
| Week of 10-12 | Holdings −$683 net; all 10 losers AI / high-beta (SPCX, PLTR, TSLA, MRVL, HOOD, CRWD, DELL, NVDA, FSLR, META); equity $8,518 (10-09) → $6,643 (10-19), −22%; about 3.5× equity traded; the app's own buy calls (FSLR, MRVL, PLTR, CRM) also went into the same group | Loss attribution, 2026-10-19 |
| Turnover | About $95k of sells in Q3 on $5–8k of net capital | Q3 review |
| Holding period vs result (Q3) | 4–7 day holds −$1,489; 8+ day holds +$1,988 | Q3 review |
| Large losers (Q3) | 12 trades closed at −10% or worse, −$2,165 total; capping at −8% would have turned +$472 realized into +$1,422 | Q3 review |
| App vs owner (buys) | App-aligned buys +4.5 points vs SPY; self-initiated −0.3 points (56 graded) | 🎯 My Edge → Self Track Record, owner screenshot 2026-10-19 |
| Acted new picks | +14.4 points alpha over 23 matured calls | Engine Track Record, 2026-08-30 |
| Margin interest | $61.01 for the cycle ending 9/24 (average margin $14,479.56, 5.06%); about $75 a cycle expected at the 9/30 debit; $338.12 year to date through September | Robinhood September statement |

### The app at HEAD `d583b1b`

- **30 navigation pages** in 6 groups (`_NAV_GROUPS`, [app.py:3484](../../app.py#L3484)); 7 of them are under TRACK RECORD.
- **353 functional requirement IDs**, about 12,000 test functions, 92 `_GATE_FILES` members.
- **Build pace:** 2,267 commits since 2026-05-04. In the 30 days before this review, 374 commits landed on 28 of 30 days, with 140 `fix` vs 57 `feat`. "journal" was the most common scope, and about 69 commits reconciled the hand-typed journal, broker data and snapshots.
- **No page-visit telemetry,** so page usage is unknown.
- **No goal, contribution plan or progress concept** anywhere in `app.py` or `stock_analyzer/` (owner decision above: none is needed).

---

## 4. The plan (final, after the owner's answers)

### 4.1 Leverage limits set by market conditions and runway (P0)

**Today**
- Leverage is shown on 🏠 Home (a caption), 🧾 Summary (the Book Safety strip from `summary_view.book_safety`, and the F-287 leverage note with the book reduction to the 2.0× reference from `leverage_giveback.book_reduction_to_target`), 💰 Account (F-253) and the weekly / monthly emails.
- No sizing or gate module reads leverage or margin cushion. `LEVERAGE_REFERENCE_TARGET = 2.0` and `LEVERAGE_GIVEBACK_DRAWDOWN_PCT = -10.0` are "awareness only, read by no gate" ([constants.py:130-131](../../stock_analyzer/constants.py#L130-L131)).
- The capital-basis limits that exist cap single trades, not total exposure: `NET_CAPITAL_POSITION_CAP_PCT = 25.0` per position (F-255) and `DAILY_NEW_POSITION_BUDGET_PCT = 40.0` of net capital per day in new buys (F-306). The daily budget allows about 2× capital of new buys in a week.
- Book-basis caps (`SINGLE_NAME_CEILING = 15.0`, `SECTOR_CEILING = 35.0`, `PORTFOLIO_BETA_CEILING = 1.4`) read about 3× larger against own capital at ~3× leverage.
- A bear market tone already stops new picks in Grow Today and the morning email ([daily_briefing.py:1146](../../stock_analyzer/daily_briefing.py#L1146)). Nothing reduces leverage already carried.
- **Leverage rises by itself in a falling market.** A $22,500 book on a $15,000 loan is 3× on $7,500 of own money. A 10% fall with no trade leaves $20,250 on $5,250, which is 3.9×.

**With the plan (shape approved by the owner; values not set)**

| | Today | With the plan |
|---|---|---|
| Buy sizes (Grow Today, 📋 Watchlist, 📈 Analysis, morning email) | Ignore leverage | Shrink, or are withheld with a banner, when a buy would push leverage past today's limit |
| Market conditions | Bear tone stops new picks only | The limit moves with conditions the app already computes: market tone (bull / flat / bear), `exit_advisor.risk_off_regime`, the `leverage_giveback` state |
| Short runway to a margin call | Red strip and a reference figure on Summary, information only | 🔴 Act Today and the morning email: "Runway X% is under your floor. Sell about $Y, starting with Z." Must coordinate with the existing `beta_trim` recommendation (overlap flagged in the F-287 design) |
| Drawdown past the −10% line | A note on Summary | New buys pause until the account recovers or leverage is back under the limit |
| Trade setup | — | Decides who gets the room first: the sizing path already sizes the highest composite first (F-306, F-310). It does not raise the limit, because a strong setup doesn't protect against a whole theme falling together (week of 10-12) |

**Illustrative table (every value is the owner's call; Hard Rule #1):**

| Condition | Leverage limit |
|---|---|
| Bull tone, no drawdown, healthy cushion | Owner's upper limit (has usually run about 3×) |
| Flat or mixed tone | The owner's 2.0× reference |
| Bear tone or the risk-off regime | Below 2.0× |
| Drawdown past −10%, or runway below a floor | A reduce-leverage call, whatever the tone |

**Success criteria**
- Runway never below the owner's floor.
- Days above the day's limit trend toward zero.
- No drawdown past −10% without a reduce-leverage call having fired first.
- Down-month results closer to the market's than October's (−8.47% vs −0.91%).

**Complexity:** medium to high. It's a gate on the sizing path and a new Act Today item, so it needs the `planner` and the `reviewer`.
- Dependency: Robinhood's per-security maintenance requirement is not in the SnapTrade payload. The runway uses `MARGIN_MAINTENANCE_RATE = 0.25`, which is optimistic in a selloff, or an owner-entered figure.

**Open values:** the upper limit, the bear / risk-off limit, the runway floor, and confirmation of the "setup decides priority, not the limit" reconciliation.

### 4.2 Holding to the owner's own horizon, plus a fun budget (P1)

**Today**
- The app keeps its own nudges quiet on new positions: `POSITION_SETTLING_DAYS = 10`, `ADD_WINNER_COOLDOWN_DAYS = 10`.
- The owner's holding period is visible only after the fact (🎯 My Edge, 🪞 Trade Review, `scripts/holding_period_analysis.py`).
- Nothing comments when the owner sells a self-initiated pick within days, or adds to a position below cost. The app never suggests adding to a loser; add-to-winner only.

**With the plan**

| | Today | With the plan |
|---|---|---|
| Holding period | After the fact, deep in My Edge / Trade Review | A weekly card (Sunday email and Summary): median hold, turnover ÷ net capital, override share, each with its realized dollars, against the stated horizon |
| Selling a self-initiated pick within N days | Nothing | On the holding's card and when the sell is logged: days held, estimated short-term tax (`TAX_RATE_SHORT_TERM = 0.37`, top federal bracket estimate), and the owner's own net result on holds that short (from `investor_mirror.build_closed_lots`) |
| Adding below cost | Not flagged | Flagged |
| Buying pace | 40% of net capital per day | A weekly cap as well (extends F-306) |
| Short trades for fun | Mixed in | Tagged at entry, given their own slice of capital, measured separately. Not a second book (roadmap alternative #5 warns that two books is a bigger change than it looks) |

- **Guardrails are action-rate facts, not alpha.** They stay on the safe side of the line F-231 drew. The live alpha mirror was rejected 2026-08-06 and must not be re-proposed.
- **Limit:** the owner trades in Robinhood and logs afterwards. The "before you sell" line only helps if the holding is checked in the app first; the weekly card works either way.

**Success criteria:** median hold rising toward the stated horizon; quarterly turnover ÷ capital falling; holds under 8 days no longer net-negative; fewer overrides of HOLD-rated names.

**Complexity:** medium; mostly reuses `investor_mirror`, `scripts/holding_period_analysis.py` and `attribution_readiness.turnover()`. Any blocking guardrail is a gate (`planner` + `reviewer`).

**Open values:** N (days for "quick sell"), the weekly cap, the fun-budget size, and a target median hold.

### 4.3 Scorecard: is the side hustle beating an index fund, after interest and tax? (P1)

**Today (partly built)**
- 📄 Reports compares the account with SPY for any chosen period from 2026-09-10 (the first `account_daily_snapshots` row), with a breakdown that includes margin interest (F-276c, `period_attribution.py`). Its waterfall already splits the moves on positions held at the prior close into "on borrowed money (avg X.Xx leverage)" and "on your own money" (`period_attribution.split_moves`), next to "Margin interest posted". *(Corrected 2026-11-15, same day: an earlier draft of this review said the leverage question was answered "nowhere".)*
- Realized gains are already split into short- and long-term per tax year, with sell dates (`tax_report.build_realized_lot_ledger`).
- A 30-minute cached SPY download already exists (`_cached_spy`, [app.py:3985](../../app.py#L3985)), used by 🧑‍⚖️ The Judge and the Engine Track Record card.
- The weekly debrief email states the week's account result vs SPY.
- 🎯 My Edge → Benchmark Mirror compares since the baseline with SPY on the same cash flows (`benchmark_mirror.py`), but only after pressing its load button. Summary's card otherwise reads "Not computed this session" ([app.py:15581](../../app.py#L15581)).
- 🎯 My Edge → Self Track Record compares app-aligned buys with self-initiated ones.

**With the plan**

| | Today | With the plan |
|---|---|---|
| Since 2026-06-25 vs the same money in SPY | Button-gated | Always on Summary and in an email, using the existing cached SPY download. No new table: every other input is already stored. *(Corrected same day: an earlier draft proposed a nightly stored computation.)* |
| After tax | Nowhere as a figure (the gains split exists) | The estimated tax already triggered by realized gains. Until mid-2027, SPY bought in June would also be short-term if sold, so the difference is mostly timing, not rate |
| Did the borrowed money pay? | Reports shows the borrowed / own split and the interest, per period, as separate lines | One netted verdict: what the borrowed share made, minus the interest posted |
| App's calls vs the owner's | My Edge | The same figure, on the scorecard |
| Add more capital? | Owner's judgment | An owner rule set in advance and checked monthly, e.g. "ahead of the index after tax for N months". The same test applies to keeping the borrowed money (about 2× own capital at 5.06%) |

**Success criteria:** the headline is visible on every visit without a click; one number per question; the pre-set check happens on schedule.

**Complexity:** low; the maths exists in `benchmark_mirror.py`, `account.py`, `period_attribution.py` / `period_inputs.compute_period_core` and `tax_report.py`. No DB write and no DDL. The capital rule is a new decision surface, so it needs the `reviewer`. A follow-up design note (2026-11-15) sizes this at about 3 working sessions.

**Open values:** N, and what the rule does (for example, shrink the active slice or hold off on deposits).

### 4.4 Fewer pages (P2)

- Add a page-visit counter for 4–6 weeks before retiring anything; there's no usage data today.
- Then merge or retire unused pages (the target was about 12), and adopt one in, one out for new screens.
- The overlap candidates: three summary pages (🏠 Home, 🧑‍⚖️ The Judge, 🧾 Summary), three event pages (🌐 Macro, 🔔 Catalyst Watch, 📅 Economic Calendar), and seven TRACK RECORD pages.
- Since trading is a hobby, time in the app isn't purely a cost. The chores are: reconciliation, verification screenshots, and the 19 groups of pending live checks in CLAUDE.md at the time of review.

### 4.5 Dropped or parked

- **FI planner / goal layer:** dropped (the 401(k) is out of scope). At most an optional target line for this account inside the scorecard.
- **Broker fill record:** stays queued; the owner keeps manual entry (2026-11-15).

---

## 5. Already sufficient: don't rebuild

- **The daily decision loop** meets the five-minute bar: 🔴 Act Today vs 👁️ Monitoring, "Nothing to act on — you're set for today", the position settling period, and signal hysteresis.
- **Entry selection:** app-aligned buys beat self-initiated ones (+4.5 vs −0.3 points). Don't add more signals.
- **Hard gates with visible banners,** the three-state `None` contract, and fail-closed sizing.
- **Recent capital-protection work:** the held stop that only moves up (#26), one sizing path (F-310), and net capital taken as the lower of the journal and Robinhood (U7).
- **Measurement with criteria set in advance** (Gate Suppression Ledger, Recommendation Outcomes): let them mature.
- **AI:** "AI narrates, never originates" is right. Intelligence isn't the bottleneck.

## 6. What none of this can do

The app advises; it doesn't control the broker. Trades are placed in Robinhood and logged afterwards. So:
- §4.1 changes the app's own sizes and calls; it can't block an order.
- §4.2's at-the-moment line depends on checking the app before trading.
- §4.3 informs the capital decision; it doesn't make it.

## 7. At the revisit: re-measure first

Before choosing an order, read these against the figures in §3:

1. **Leverage:** its range and the lowest runway since 2026-11-15 (`account_daily_snapshots`), and how many days it ran above the 2.0× reference.
2. **Holding period:** median hold and the share closed within 10 days since 2026-11-15 (`scripts/holding_period_analysis.py`).
3. **Turnover:** quarterly turnover ÷ net capital.
4. **Returns:** monthly account return vs SPY for November to January (📄 Reports), and the total since 2026-06-25 vs the Benchmark Mirror.
5. **App vs owner:** app-aligned vs self-initiated alpha (🎯 My Edge → Self Track Record).
6. **Costs and capital:** margin interest paid since September, and whether any deposits were made.

If leverage stayed under the reference, holds lengthened and the account kept pace with SPY, §4.1 and §4.2 may matter less than this review assumed. Say so rather than build them anyway.

**Earlier trigger (suggestion, not a gate):** revisit sooner if 🧾 Summary's Book Safety strip goes red, or the leverage note reaches its giveback state.

## 8. Open owner values, collected

- **§4.1:** upper leverage limit; bear / risk-off limit; runway floor; confirm "setup decides priority, not the limit".
- **§4.2:** quick-sell N days; weekly buy cap; fun-budget size; target median hold.
- **§4.3:** months-ahead rule N, and what the rule triggers.
