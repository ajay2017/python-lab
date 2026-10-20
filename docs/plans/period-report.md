# Weekly / monthly report — where the money went, and who made the call

**Status:** DESIGNED 2026-10-20 (planner, Opus 5.5) — verdict **PROCEED WITH CHANGES** (design below). Mockup
`docs/mockups/2026-10-20-period-report-mockup.html` **APPROVED 2026-10-20** ("build as shown"). **Owner decisions 2026-10-20: all 13 §D recommendations accepted** — incl. #4 days-above level = the existing `LEVERAGE_REFERENCE_TARGET` 2.0× (no new constant) and #9 the Weekly Debrief switches to this engine + its email gets the block (C7/C8). Build order: C0 owner SQL · C2 · C3 · C4 · C5 · C6 · C7 · C8.
Earlier: QUEUED 2026-10-19 (owner agreed the approach).

## Why
On 2026-10-19 a manual, SQL-driven analysis answered "where do my losses come from?" (memory
`project_loss_attribution_2026_10_19`): the AI sell-off week's losses were one high-beta AI/growth pocket × ~3× leverage
× manual trading inside the drop; app-aligned buys beat self-initiated ones (+4.5 vs −0.3pp alpha); exits are a
trade-off (owner's post-exit timing better, app's realized P&L better); and the stored data passed a recompute check
(`docs/plans/app-trust-verification.md`). The owner wants this as a recurring weekly/monthly report instead of an ad-hoc
SQL session.

## Approach (agreed)
**Extend the existing Performance Review** (💰 Account → Reports, F-276b, `stock_analyzer/performance_review.py`) rather
than build a new page. Add period presets *Last week · Last month · Quarter · Custom* and five sections:

1. **Period result & reconciliation** — change in net equity split into holdings' price moves, leverage effect, flows
   (`account_flows`), margin interest (`snaptrade_income_events`), and the trading-timing remainder.
2. **Where it came from** — top contributors/detractors by ticker and sector (`portfolio.TICKER_SECTORS`, fallback
   chain), top-5 share of losses, the high-beta pocket's share of gains vs losses.
3. **Leverage & margin** — leverage range, the closest margin-call distance in the period, days above a leverage level
   (`account_daily_snapshots`).
4. **Who made the call** — realized P&L app-called vs manual (`trades.trigger_type`), buys into names the app rated
   Hold, turnover vs net equity.
5. **Your edge vs the engine** — buy-side and sell-side Self Track Record alpha, with the sign spelled out (sell side:
   positive = sold early). Note these are all-time measures today; decide period-scoping in design.

Plus a **data-trust strip** (last weekly impossible-states/recompute result, any feed outage in the period). Then a
**weekly email** digest (the "emailed report" v2 item already listed for F-276), and optionally stored weekly
snapshots for trends.

## Design requirements (from the 10-19 analysis)
- All arithmetic in a new pure, tested module (e.g. `stock_analyzer/period_attribution.py`); `app.py` render-only.
- **Reconcile to net equity:** use trade prices for a position's buy day and sell day — close-to-close marks miss
  both (the 10-19 "−$1,314 vs −$683" gap).
- **Don't use `trades.followed_signal`** (text, mostly NULL on manual trades, contradicts `signal_seen` at least once);
  classify from the verdict stored at trade time (`signal_seen` / `decision_context`) — verify what is reliably stored.
- Respect the sentinels: a missing day/cash record is "unknown", never a fabricated zero; disclose coverage gaps.
- Renderer traps: `$` amounts in `st.metric` or escaped (`\\$`); compare like units (the F-276b %-vs-$ lesson).
- Display-only: no gate, threshold or recommendation changes. Mockup first (memory `feedback_mockup_first_ux`).


---

## Design (planner, Opus 5.5, 2026-10-20) — PROCEED WITH CHANGES

**Four changes before any build:** (a) the existing 🗓️ **Weekly Debrief (F-3)** already emails a weekly "how the week went +
contributors/detractors" (`debrief_advisor.py` ~138, ~243-275; cron_runner ~2600-2715) from close-to-close union marks, and it is
**defective**: a position bought mid-week counts its whole end value as a "contributor", partial adds count as gains, closed positions
are excluded and `performance_pct` moves with net buying — persisted to `weekly_debriefs` and emailed. The new engine becomes the one
source and its email folds into the Debrief (C7/C8), never a third weekly number. (b) "Who made the call" headline = the Self Track
Record evidence classifiers (`self_track_record.classify_buys`/`classify_sells`); `trigger_type` = a second "stated" lens with a
disagreement count. (c) Two `db.py` reads must stop collapsing failure first: `load_snaptrade_income_events` (failure → `[]`) and the
**unpaginated** `load_trades_or_none` (~1366; past the PostgREST cap it would silently drop the OLDEST trades — app-wide latent
hazard). (d) Leverage is an "of which" split under price moves, never an additive bucket.

**Reconciliation identity** (d0 = NYSE session before the period, same as `account_return`; d1 = latest valid row ≤ period end):
ΔE = PosPnL + flows + dividends + interest + fees + R, R = R_cash + R_book; R_cash = ΔC − [(S−B) + F + I]; R_book = ΔG − ΔV (journal-replay
book V vs recorded book G; report the book gap at each end). Per ticker per interval [p→d]: q1 = min(q_sell, s_p), q2 = q_sell − q1;
held_through = (s_p − q1)(c_d − c_p); sold_leg = q1(f_sell − c_p); round_trip = q2(f_sell − f_buy); bought_leg = (q_buy − q2)(c_d − f_buy)
(VWAP per side; exact). Shares from `capital_vs_margin.holdings_by_date` (raw, split-aware); closes from `daily_snapshots` (its `shares`
is int-truncated — closes only), else a historical-close fetch, refused for a ticker split in the window; no close → listed as unpriced,
never zero. Leverage: borrowed_share = (held_through + sold_leg) × max(0, −C(p))/G(p) on intervals with a valid account row at p.
Unknowns: E(d0) invalid → `pre_coverage` (holdings-only, no residual); income/flows offline → that line "unknown", no residual.
Reuse: `performance_review._valid_snapshot_row` (promote public), `capital_vs_margin.window_income_events`/`interest_partition`,
`broker_sync.dedupe_income_events`, `daily_pnl.compute_positions_day_pnl` (agreement oracle), `margin.call_distance` sign.

**Sections:** S1 period result + waterfall (held-through / ↳ of which borrowed / sold last day / bought first day / round trips / flows /
dividends / interest / fees / unexplained) with the legacy close-to-close figure as a footnote; S2 where it came from (ticker, sector
with curated/provider/unclassified source, top-5 share of losses, high-beta pocket); S3 leverage & margin (range, closest call, days above
a level, borrowed share vs interest; maintenance rate is an estimate); S4 who made the call (evidence / stated lenses, realized $ and W/L,
buys into Hold-rated names via `score_history` → recs → "not scored", turnover); S5 edge vs engine (all-time = My Edge exactly via ONE
shared input helper; period figure only at n ≥ `BEHAVIORAL_MIN_SAMPLE_N`; sell-side sign spelled out); data-trust strip (own coverage
+ residuals + System Trust "now" — no stored history exists for Q0/heartbeat/feeds, so it must not claim "no outage in the period").
Existing Performance Review sections move into a "More from this period" expander, unchanged.

**Module:** `stock_analyzer/period_attribution.py` (pure; not a gate file; import-isolated from risk/exit_advisor/risk_advisor/scoring/
ranking): `resolve_period`, `reconcile_period`, `attribution_breakdown`, `leverage_section`, `who_called`, `edge_vs_engine`,
`trust_strip`, `build_period_report`, `format_period_csv`/`_markdown`. Tests 1-16 in the planner report; non-negotiable: exact identity,
the synthetic −$1,314-vs-−$683 week (A held −20, B sold −50, C bought −20, D round trip −20, interest −5, R = 0; legacy = −20; and the
Debrief union method books C as a +$180 "contributor"), the daily_pnl oracle, sentinels, leverage parts sum, `signal_seen`/
`followed_signal` have zero influence, the n = 7/8 floor, import isolation.

**Chunks:** C0 owner-run read-only SQL audit (trades count; per month × action × trigger_type × imported with null signal_seen /
decision_context; decision_context start + late-logged count; fractional fills; account snapshots null cash; account_flows max date by
source; income events by type × month × sign — settles the fee sign; daily_snapshots days since 09-10) · C1 mockup (owner) · C2
`period_attribution.py` S1+S3 (implementer; voluntary Opus) · C3 db.py `load_snaptrade_income_events_or_none` + trades pagination
(mandatory Opus; before C4) · C4 app.py render S1/S3 + trust strip + export · C5 S2 + beta injection · C6 S4 + S5 + shared My Edge helper
(voluntary Opus) · C7 Weekly Debrief consumes the engine (owner OK first) · C8 Debrief email block (mandatory Opus, cron_runner) · C9
optional stored weekly snapshots (columns on `weekly_debriefs`).

## D. Owner decisions — ALL ACCEPTED as recommended, 2026-10-20 (recommendation in brackets)
1. Headline "who made the call" lens [evidence classifiers; stated lens as a 2nd column + disagreement count].
2. Stated mapping [buys app = RECOMMENDATION/WATCHLIST_ENTRY/DIVERSIFY_ADD; sells app = RECOMMENDATION/STOP_HIT/REBAL_TRIM/TAX_HARVEST;
   REBALANCE sell = app, buy = unknown; MANUAL with import provenance = "not stated"; STOP_HIT footnoted as possibly your manual stop].
3. High-beta pocket [beta ≥ `PORTFOLIO_BETA_ELEVATED` 1.3, 1-yr daily beta at report time; unknown beta its own bucket].
4. "Days above" leverage level [`LEVERAGE_REFERENCE_TARGET` 2.0 — will read "every day" at ~3×; any other level = a new constant you set].
5. Period-edge alpha horizon [trade date → today, as My Edge].
6. Period-edge minimum n [`BEHAVIORAL_MIN_SAMPLE_N` 8 per bucket; else counts + the all-time figure; never a verdict sentence].
7. Residual [show $ and % of start; no reconciled/not label].
8. "Last week" [last completed Mon–Sun calendar week].
9. Email [a "Where the money went" block in the Sunday Debrief email + switch the Debrief's numbers to the engine; no second email].
10. Layout [five sections on top; Leverage Drift merges into S3; the rest into "More from this period"].
11. Missing close [historical fetch, except split tickers → unknown].
12. Turnover [(Σ bought + Σ sold) ÷ mean recorded net equity].
13. Trades pagination [fix `load_trades_or_none` in this work (db.py, reviewed)].

## C0 audit — owner-run 2026-10-20 (`docs/sql/period-report-c0-audit.sql`)
- trades 322 (under the PostgREST page cap; pagination fix is preventive); 0 fractional fills.
- account_daily_snapshots 28 rows since 09-10, 0 null cash / net_equity; daily_snapshots 28 sessions since 09-10 = every NYSE
  session 09-10..10-19 (full coverage).
- account_flows: only the 06-25 `baseline` row — no deposit/withdrawal ever recorded. **Owner 2026-10-20: no withdrawals since
  06-25** (deposits not stated). An unrecorded deposit would land in "unexplained" (cash side).
- snaptrade_income_events: fees stored negative (cash out); interest negative = charged, positive = earned. **Margin interest has no
  rows after 2026-08** although the September statement shows MINT charged → capture gap (statement CSV not imported). **Owner imported the September statement 2026-10-20**; October stays a gap until its statement (~11-01, trust item 1). The report
  flags months in the period with no interest row while levered (`interest_coverage_gap_months`), never "$0".
- trigger_type: most trades are imports and imports default to MANUAL (Oct: 14 of 19 MANUAL rows imported) → the stated lens has a
  large "not stated" bucket, confirming decision #1 (evidence lens leads). signal_seen missing mainly May-Jul buys; decision_context
  on every trade since 2026-08 (first 2026-07-17); 20 rows late-logged (context captured on a different ET day than the trade).

## C3 review follow-ups (Opus SHIP/0, 2026-10-20) — not built, unreachable below 1,000 rows
- N1: de-duplicate by `id` after joining pages in `load_trades_or_none` / `load_snaptrade_income_events_or_none` (a trade logged
  between page reads shifts newest-first offsets → the last row of page 1 repeats on page 2 → a replay double-count). The
  recommendations loader has the same shape.
- N2: every paginated loader stops on a short page; if the server's max-rows were ever set below 1000 a full page would read as
  short and truncate silently. Sturdier: stop only on an empty page (one extra call).
- N3: `load_snaptrade_income_events_or_none` docstring says None on "read-only/no creds" — `has_db()` doesn't check read-only;
  say "no creds".
- N5: the old `load_snaptrade_income_events` stays unpaginated (unchanged on purpose).
