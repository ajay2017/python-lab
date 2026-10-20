# App trust verification — is the app computing what it claims?

**Status:** ITEM 2 BUILT 2026-10-20 — Q0 section E (E1 score_history recompute, E2 recommendations implied sentiment, E3 followed_signal against the opposite signal) + data_maintenance check 7 (held tickers missing from TICKER_SECTORS); Q0_VERSION `q0:fdbd2b2a9c28`. **Owner 2026-10-20: function re-applied; E3 first count = 11 of 184** (all SELLs marked followed_signal='yes' against a Buy/Strong Buy signal_seen, 06-03..10-16 — "followed" read as "followed my own rule/stop"), now FROZEN at 11. Items 1, 3, 4, 5 not started. (Was: PROPOSED 2026-10-19, lead, owner-requested.)

## Results so far
**Item 2, first run (2026-10-19, owner-run SQL):**
- `score_history` (held names, cron, 2026-09-14..10-19): **438 rows, 0 composite mismatches** against
  `round(0.25t + 0.35bq + 0.30val + 0.10s, 1)` with unmeasured pillars as the neutral 50, **0 pillars outside 0-100**.
  The stored arithmetic is sound.
- 201 rows (46%) had an unmeasured pillar, 114 at Buy level (≥ 65). **All 201 were SENTIMENT only** — technical,
  quality and valuation were measured on every row. By week: 09-14 0/86, 09-21 0/88, **09-28 37/87, 10-05 84/84,
  10-12 80/80**, 10-19 0/13. Exactly the Yahoo news-feed outage (fixed `593a26c` 2026-10-16; tripwire `e3290ac`
  turns the cron heartbeat red on a recurrence). So ~10-01..10-16 composites carried a placeholder 50 for sentiment
  (≤ ±5 composite points; matters only near the 65/75 lines). Already detected going forward; nothing to fix.
- `recommendations` (weights_version 2 rows with t/bq/val stored, 2026-08-10..10-19): **1,273 rows, 0 with an
  impossible implied sentiment** ((composite − 0.25t − 0.35bq − 0.30val)/0.10 outside 0-100). Weaker than the
  score_history check (it only catches a composite off by ≳ ±5 points), but clean.
- Not yet run: the detectors per recent bug class (and folding both checks into the weekly Q0).
- **Detector candidates found 2026-10-19:** (a) `trades.followed_signal` contradicting `signal_seen` (e.g. a SELL
  marked 'yes' against a "Buy" signal — MSFT 10-16) and mostly NULL on manual trades, so any "followed the app"
  statistic is unreliable; (b) held/traded tickers missing from `portfolio.TICKER_SECTORS` (WK, CF, ENVA).

## The question
After 4-5 months of live data and a run of sessions that each found bugs, the owner wants a way to **trust** the app.
Two different questions, kept apart on purpose:
1. **Correctness** — is the app computing what it claims (the right number, the right data, every page agreeing)?
2. **Efficacy** — is its advice any good? Already covered by the Gate Suppression Ledger (F-259/F-259b), Engine Track
   Record (F-229), Recommendation Outcomes (F-273b) and Self Track Record — these need time, not new work.

This plan is about **#1**.

## What already exists, and the gap
| Layer | Catches | Doesn't catch |
|---|---|---|
| ~9,560 tests + Opus review | logic bugs in `stock_analyzer/` | anything in `app.py` (no test imports it) |
| `scripts/check_antipatterns.py` | known bug classes coming back | new classes |
| 🩺 System Trust ①-⑦ (`system_health.py`) | dead cron lanes, stale stores, provider outages, failed writes, stale reference data | numbers that are fresh but wrong |
| Impossible-states SQL (`docs/sql/impossible-states-audit.sql`, weekly via ⑦) | contradictory database rows — found 5 real bugs on 2026-10-14 | what the screen tells the owner |

**What the recent bugs actually were** (2026-10-14..19): `$` eaten as LaTeX on Deep Dive; the Grow Today risk footer
showing ~$1,144 when ~$240 was at risk; Analysis offering MRVL an add while Grow Today paused it (G-24) and while
it already held ~23% of net capital; the same headline −0.34 on Home vs −0.25 on Deep Dive; UBR silently hidden on
the Watchlist; "Screened but Filtered Out (8)" listing 5; the "Anthropic key is off" belief. **Almost none were bad
database rows — they were the app showing something false or inconsistent.** That is the gap.

## Plan, in priority order
1. **Monthly broker reconciliation (highest trust per hour).** The Robinhood statement is the only ground truth the
   app doesn't control. On the 1st of each month compare the app vs the statement: positions + share counts, cash,
   account value, dividends, margin interest, realized P&L for the month; record differences and their cause. The
   September statement check (memory `project_q3_2026_return_review`, `project_performance_review_return_tile_redesign`)
   is the precedent. Lead prepares the comparison from app data; owner supplies the statement. ~30 min/month owner.
2. **Recompute stored scores from their parts — one SQL check over all history.** Wherever a table stores a composite
   AND its pillar scores, the composite must equal the weighted sum for its regime (`COMPOSITE_WEIGHTS_VERSION` /
   `recommendations.weights_version`), with withheld pillars handled per `pillar_availability.verdict_basis`.
   **First step: confirm which tables store both (likely `score_history`; verify `recommendations`) — do not assume
   columns.** Add one detector per bug class found in the last month that has a database footprint. Fold into the
   impossible-states Q0 so it runs weekly (edit the audit file → `scripts/gen_impossible_states_function.py` →
   `Q0_VERSION` → owner re-applies the function). Principle: **every fixed bug class gets a permanent detector.**
3. **Cross-page consistency check — 🩺 System Trust ⑧ (needs a planner design).** A pure function over this
   session's published caches comparing: the same ticker's composite across Home / Analysis / Watchlist; any share
   count shown in more than one place; Act Today counts; a headline's score across pages. Amber on disagreement.
   The only way to test `app.py`'s OUTPUT without a screenshot. Must respect the None-vs-empty sentinel contract and
   `coord_freshness` (a stale-but-legitimate difference is not a bug).
4. **Mechanical display checks in the antipattern gate.** The two renderer-mismatch bugs that keep returning
   (memory `feedback_streamlit_renderer_mismatch`): literal `**` inside `unsafe_allow_html` strings, and a bare `$`
   adjacent to a number in markdown (`st.markdown`/`st.metric`/`st.info`/`st.caption`). Baseline-gated like the
   other rules; prefer fixing at the source.
5. **Weekly visual sweep.** A fixed list of ~10 screens (Home top + Grow Today + Monitoring, Analysis Trade Plan +
   Deep Dive, Watchlist, Road Not Taken, Account, Summary, System Trust) — one line each: looks right / what's off.
   Formalises what the 2026-10-19 session did and catches what no automation will.

**Why this order:** 1-2 give confidence from INDEPENDENT evidence (the broker, arithmetic over real data); 3-4 put
permanent detectors on the recurring bug classes; 5 covers the rest. The goal is not "bug-free" but **"when
something is wrong, the owner finds out within days, not weeks"** (the news feed was dead ~4 weeks unnoticed).

## Not in scope
Efficacy measurement (already built, waiting on data); a full `/audit --full` (milestone-only per CLAUDE.md).
