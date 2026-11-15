# Account scorecard: is this account earning extra?

**Status (2026-11-15, shipping):** C1 SHIPPED (`e483c58`, Opus FIX-FIRST → SHIP). C2 SHIPPED and VERIFIED LIVE (`6c8bccb`; owner screenshots with privacy on and off; every figure cross-checked: $1,123 behind SPY, October Leverage cost you −$332 net, tax ≈ $750 on $2,026, matching the Tax Report total). F-318 in requirements.md.
- SHIPPED in the next commit (Opus review cited): the owner-approved wording fixes (wash-sale direction, plain spread sentence, drop "(0 this year)") plus the round-2 follow-ups (pre_coverage_reason caption, no nested parentheses); C3, the weekly debrief email block, via the shared `scorecard_inputs.compute`. Email leg VERIFIED live 2026-11-15: the scorecard box was already in that evening's Sunday email (cron-thesis run 18:01 ET, after the deploy; owner screenshots + cron log "debrief: scorecard since=ok lev=ok tax=ok").
- C3b SHIPPED (Opus review FIX-FIRST on docs → SHIP, cited in its commit):
  - "(live)" labels and a Live caption on 💰 Account and 🎯 My Edge;
  - the same-day-flow fix: `account.money_weighted_return` and `benchmark_mirror.compute_shadow_mwr` use (baseline, end]; `build_shadow_portfolio` and `build_benchmark_curve` share `_window_flows`, which invests the earliest baseline plus flows in (anchor, end];
  - the email's "couldn't be read this week" lines;
  - the User Guide and user-manual text.
- Summary wording VERIFIED live 2026-11-15.
- Still deferred: starting at the first month whose d0 is valid (fails safe today).

**Status (2026-11-15, build): MOCKUP APPROVED (`docs/mockups/2026-11-15-account-scorecard-mockup.html`).** Owner choices:
- **Placement:** Option A, full width below the pointer grid, above State of the Portfolio.
- **Headline:** "Is this account earning extra?" kept.
- **Tax caption:** "The same money left in SPY would owe nothing yet" kept.
- **Email:** the block goes in the Sunday weekly debrief.

C1 (`scorecard.py` + tests) is in build.

**Status (2026-11-15, latest): P0b RESOLVED by owner decision. The starting point is the existing `account_flows` baseline row, 2026-06-25 at $7,152.99: the day the app's tracking began. The account's earlier years of deposits are deliberately out of scope. No ledger change, and no fallback anchor needed. Design = planner PROCEED WITH CHANGES (§8, which overrides §4). Not approved for build; owner to say go (mockup first) or keep it for the ~2027-02-15 revisit.**

- **Consequence 1:** the planner's "skew" finding in §8 becomes a definitional choice, not a defect. My Edge, 💰 Account and the scorecard all measure from the owner's chosen anchor.
- **Consequence 2:** the 6/30 statement showed $5,455.83 of net equity, so the card's basis line ("starting point: $7,152.99 on Jun 25, when tracking began") must always be visible.
- **Remaining sibling work (C3b, smaller now):** live vs close labels, and keeping flows in (baseline, end].

**Status (2026-11-15, later): Opus `planner` (claude-opus-5-5) verdict PROCEED WITH CHANGES; see §8, which overrides §4 where they differ.**

**Status (2026-11-15): SPEC DRAFT, sent to the Opus `planner` for a design check.** Origin: `docs/reviews/2026-11-15-strategic-review.md` §4.3. The owner re-opened #3 the same day it was deferred ("partly built already, so something makes sense to build; analyze and plan/design before making changes").

## 1. The question

The owner runs this account as a hobby and side hustle to make **extra** money (the 401(k) is separate and out of scope). The owner is holding back new deposits until the account proves itself. The scorecard answers, without a click:
1. **Since the start:** how much has the account made vs the same money in SPY, in dollars?
2. **Leverage:** did the borrowed money pay for its interest?
3. **Tax:** what tax have realized gains already triggered (estimate)?
4. **(Phase 2) Capital rule:** is the owner's own pre-set rule for adding capital met?

## 2. What already exists (verified at HEAD `d583b1b`)

| Need | Existing piece | Gap |
|---|---|---|
| Since-start vs SPY, same deposit dates | 🎯 My Edge → Benchmark Mirror: `benchmark_mirror.build_shadow_portfolio`, `compute_shadow_mwr`, `account.money_weighted_return`, `account.baseline_anchor` | Behind a "📊 Load benchmark data" button (the SPY fetch was unmemoized). The end value mixes live prices (`_last_port_df`) with the last-synced `account_cash`. The headline is annualized, beta-adjusted pp |
| Cached SPY prices | `_cached_spy(period)` ([app.py:3985](../../app.py#L3985)), `st.cache_data(ttl=1800)`, used by The Judge and the Engine Track Record card | None |
| Period result vs SPY, breakdown, borrowed / own split, interest | `period_inputs.compute_period_core` → `recon` (`pos_pnl`, `leverage_split`, `income`), `leverage`, `account_return`; `period_attribution.split_moves`, `interest_posted`; untrusted rows masked by `account_row_trust.exclude_stale_rows` | Separate lines on 📄 Reports; nothing nets "borrowed share − interest" into one verdict. Coverage starts 2026-09-10 |
| Realized gains ST / LT with sell dates | `tax_report.build_realized_lot_ledger(trades_df, tax_year)` → `rows[].sell_date/gain/term`, `st_gain`, `lt_gain` | No tax-triggered figure |
| Tax rates | `TAX_RATE_SHORT_TERM = 0.37`, `TAX_RATE_LONG_TERM = 0.20` (top federal bracket estimates; owner decision 2026-09-15 for Recommendation Outcomes: use these, captioned, no personal-rate setting) | None |
| Summary slot | 📐 Benchmark Mirror pointer card, `util.benchmark_mirror_summary_state` ([app.py:15581](../../app.py#L15581)) | Reads "Not computed this session" unless My Edge's button was pressed today. **Defect:** its basis line prints My Edge's selected period ("Last 3 months") while the value is always since baseline |
| Emails | Weekly debrief (every Sunday) and monthly Decision Review (first Sunday; next 2026-12-06), both LLM-written and INERT without `ANTHROPIC_API_KEY` | No scorecard block |

## 3. Data facts (owner SQL, 2026-11-15)

- `account_daily_snapshots`: 47 rows, 2026-09-10 → 2026-11-13. That's every NYSE session in the range (15 + 22 + 10), so no gaps. `count(net_equity)` not seen.
- **`account_flows` (P0, seen 2026-11-15): exactly one row: 2026-06-25, `baseline`, 7152.99, note "Baseline". No deposit or withdrawal rows**, so the double-count risk below does not apply.
  - **New open point:** the Robinhood 6/30 statement shows net equity $5,455.83, and the trade journal starts 2026-05-27, before the baseline date. So $7,152.99 is probably contributed capital (or another basis), not the account's net value on 6/25.
  - The since-start line differs by about $1,700 depending on which is used. The owner needs to confirm what $7,152.99 is; the planner advises on the method.
- *Original P0 note, kept for the record:* A 2026-10-02 note recorded the baseline anchor as "Jun 28, with several deposits across the window". The owner says there was one initial deposit, on 2026-06-25, and none since.
  - `build_shadow_portfolio` invests EVERY baseline/deposit row regardless of date.
  - `money_weighted_return` weights flows by (end − flow) / (end − start), which exceeds 1 for a flow dated before the baseline.
  - So a deposit row dated before the baseline, whose money is already inside the baseline amount, would be double-counted on both sides.
  - Expected if clean: one baseline row in late June (amount = that day's account value — the 6/30 statement shows $5,455.83 — or the total deposit), and no deposit rows dated before it.
- The Q3 reconstruction (6/30 $5,455.83, statement-confirmed) is an independent cross-check for the starting value.

## 4. Proposed design (v1)

**Card on 🧾 Summary, replacing the Benchmark Mirror pointer card** (placeholders, not real figures):

```
🧭 Is this account earning extra?                as of Fri Nov 14 close
Since Jun 28:  +$X (+Y%)   ·   same money in SPY: +$Z (+W%)
→ You're $D ahead of SPY
Borrowed money since Sep 10: made $B, interest $I → net $N
Tax already triggered by your sales (estimate, 37% rate): about $T
→ 📄 Reports for the breakdown · 🎯 My Edge for the chart
```

### Decisions proposed

1. **Dollars first; no annualizing under 12 months.** Show the period % next to the dollars. The app's own `account.annualization_caveat` exists because annualizing a short, leveraged window exaggerates.
2. **One freshness for every number.** End value = the latest trusted `account_daily_snapshots.net_equity` (after `account_row_trust`), SPY close on that same date, and "as of <date> close" shown. Never live prices mixed with synced cash. The card and the email then agree with each other and with Reports.
3. **Since-start line** = the Benchmark Mirror maths (shadow SPY on the same flows), with the end value from decision 2, period not annualized. The start = `account.baseline_anchor`.
4. **Leverage line**, over `compute_period_core(start=first account row, end=latest)`: borrowed share = `split_moves(recon)["borrowed"]`; net = borrowed + `interest_posted(recon)`.
   - Disclose the coverage start (2026-09-10).
   - Disclose that the split covers held-at-prior-close moves only (first-day and same-day round-trip legs are unsplit).
   - "unknown", never $0, when `days_split == 0`.
5. **Tax line:** the sum of the ledger's ST gains × `TAX_RATE_SHORT_TERM` plus LT gains × `TAX_RATE_LONG_TERM`, for rows sold in the current tax year.
   - Floor at $0 with a "net loss so far" note.
   - Caption: estimate at the top federal bracket, not the owner's actual tax.
   - **Framed as tax triggered, not an after-tax race.** SPY bought in June 2026 would also be short-term if sold before June 2027.
6. **Missing inputs:** if SPY or the account row is missing, the card names which and shows no gap. If no baseline row exists, it reads "starting point not set" with a pointer to 💰 Account.
7. **Pure module:** a NEW `stock_analyzer/scorecard.py` (pattern: `outage_gate.py`, `coord_freshness.py`), with all decisions and display strings there. `app.py` only renders it, and the `POLICY_DECISION_IN_RENDER` ratchet stays clean. No DB write, no DDL, no new constant in v1.
8. **Email:** a fixed deterministic block outside the LLM prompt (no change to `test_llm_request_golden`). Carrier is an open question: the weekly debrief (sends every Sunday) or the monthly email (first Sunday).
9. **My Edge:** unchanged in v1. The pointer card's mislabel disappears with the card.

### Phase 2: the owner's capital rule (after the owner picks N)
- Rule shape (owner's call): for example "ahead of SPY for N straight full months" (monthly results from `compute_period_core`; first clean month is October 2026, so N=3 gives a first verdict after December closes).
- Awareness only: no gate, no sizing.
- One new owner-set constant; `scorecard.py` joins `_GATE_FILES` (new decision surface) with its reason in `docs/review-policy.md`.

## 5. Tests (time-context matrix, CLAUDE.md)
- **Calendar cases:** weekend / holiday visit (uses the last session's row); DST change day; a baseline dated on a weekend (SPY on-or-before).
- **Missing data:** a missing SPY bar (the YahooNaN case); the latest account row masked as untrusted (search back, disclose); NaN vs None in DataFrames.
- **Deposits:** none; a deposit after the baseline; a deposit dated before the baseline (must not double-count).
- **Leverage and tax:** `days_split == 0`; tax year boundary (a sale on Dec 31 vs Jan 2 in ET); a net realized loss.

## 6. Chunks and effort
| # | Chunk | Lane |
|---|---|---|
| P0 | Owner runs the `account_flows` query | Owner, 15 min |
| C1 | `scorecard.py` + tests | Implementer, ~1 session; reviewer |
| C2 | Summary card replaces the Benchmark Mirror pointer card | Implementer, ~½–1 session; owner screenshot |
| C3 | Email block | Implementer, ~½ session; checked on the next send |
| C4 | Docs: requirements F-ID, User Guide, shipped log, CLAUDE.md queue, this Status line | Lead, ~½ session |
| C5 | Phase 2 rule (later) | Planner-light + implementer + reviewer |

## 7. Open questions for the planner and owner
1. Is decision 2 (settled EOD row) right, or should the card use live value intraday like My Edge?
2. Is the leverage-line method sound and honestly labelled, given the split excludes first-day and round-trip legs?
3. Should the tax line stay out of v1 entirely, given the year-one timing point?
4. Email carrier: weekly or monthly?
5. Does anything here overlap an existing surface enough to violate single-surface priority (Summary's Engine Track Record card, the Reports headline)?
6. What does P0 need to show before the since-start line can be trusted, and what should the card do if the ledger is inconsistent?

## 8. Planner check (2026-11-15, Opus 5.5): PROCEED WITH CHANGES

Overrides §4 where they differ. Lead spot-checked the two load-bearing claims (`account.py` ~319 skips flows before the baseline; the 💰 Account baseline form invites "lifetime net deposits", app.py ~40609).

**Starting point (blocks the since-start line only)**
- The single `account_flows` row ($7,152.99, dated 2026-06-25) is most likely lifetime contributed capital. The 6/30 statement shows $5,455.83 of net equity, and trades start 2026-05-27.
- **This already skews today's 🎯 My Edge Benchmark Mirror and 💰 Account "Return since 2026-06-25" / "Annualized"** (the account side is effectively lifetime, while SPY's clock starts on 06-25). 💰 Account "Growth ($)" vs contributed capital is unaffected.
- **P0b (owner, data only):** find the real deposit dates and amounts in the Robinhood May / June statements or transfer history, plus any shares transferred in. Then rewrite the ledger: `baseline` = the first deposit on its own date, later deposits as `deposit` rows, none on the baseline date. If they sum to $7,152.99, contributed capital is unchanged. This one data fix corrects My Edge, 💰 Account and the scorecard with no code.
- **Fallback if the dates can't be found:** keep the row as contributed capital, and start the SPY comparison at the statement-confirmed 2026-06-30 net equity ($5,455.83), with a disclosure line. That anchor needs a stored home (not `constants.py`), e.g. a new `flow_type` that contributed capital, the shadow portfolio and the MWR all ignore. That is a DB-meaning change → reviewer.
- **Rejected:** starting at 2026-09-10 (it drops Q3).
- The card always prints its basis ("starting point: $X on DATE").

**Corrections to §4**
- **End value:** the latest row must also pass `performance_review.valid_account_snapshot_row` (cash read on the same ET day at or after `ALERT_EOD_HOUR_ET`), on trading-day rows only.
- **Flows:** read with `db.load_account_flows_for_dedup_check` (None on failure), with separate states for offline, no baseline, and ledger ambiguous. Keep flows in (baseline, end]. Today a same-day deposit is double-counted, and `build_shadow_portfolio` invests every row whatever its date.
- **SPY:** require the exact close on the end date, withheld otherwise (precedent: `spy_d1_price_pending`); look back on the start date only; use `_cached_spy("2y")`; note the ISO-string vs `date` key mismatch.
- **Leverage line:**
  - Complete months only, from October 2026 to the last month with posted interest (interest posts per cycle ending around the 24th, so a Sep 10 → today window flatters mid-month). "unknown" if `interest_coverage_gap_months` falls inside the window.
  - Start from `performance_review.first_measurable_period_start`.
  - Disclose the unsplit dollars: `split_moves()["unsplit"]` + `bought_leg` + `round_trip`.
  - Say "paid / didn't pay" only if the sign survives spreading the unsplit amount at the window's own borrowed fraction; otherwise "too close to call".
- **Tax line:** kept, on its own line, never subtracted from the SPY gap.
  - Net ST and LT before applying rates.
  - Unknown-term rows at the ST rate, disclosed.
  - Disclose the wash-sale count.
  - Label it "2026 sales".
  - Show the ledger's `reconciles` flag.
  - Format the rate from `TAX_RATE_SHORT_TERM`.
  - **The lead's "mostly timing" rationale was wrong:** buy-and-hold SPY owes nothing now and turns long-term after a year, so the asymmetry favours SPY. Suggested caption (owner call): "the same money left in SPY would owe nothing yet".
- **Email:** the weekly debrief (every Sunday; already fetches `fetch_spy("2y")`). The scorecard needs its own longer-window `load_engine_inputs`; render the block in `notify`, outside the LLM package. Owner flag: the lane is INERT without `ANTHROPIC_API_KEY` and returns before the email when the LLM fails, so the block inherits that dependency.
- **Retire the old Summary card's data flow in the same commit:** `_bm_summary_cache` producer (app.py ~49560), `util.benchmark_mirror_summary_state` and its tests, plus the CLAUDE.md and coordination-caches rows. Its mislabel defect is confirmed.
- **Summary gate:** the card doesn't need the live book; decide in the mockup whether it renders above the `_port_df_enriched` stop.
- **Caching:** `st.cache_data` keyed on (ET date, latest account row date, trade count and max id), not a new session_state cache.
- **Mockup first:** five lines don't fit the 2×2 pointer slot.
- **New sibling chunk C3b:** route 💰 Account "Return since"/"Annualized" and My Edge through the same anchor + flow helper, and label live figures "live".
- **`scorecard.py` joins `_GATE_FILES` in v1** (93 files), with its reason in docs/review-policy.md. The reviewer covers C1, C3, C3b and the fallback anchor. C2 render-only needs a screenshot. C4 docs.

**Effort (planner): ~4–4.5 sessions.** P0b 30–60 min (owner) · C1 ~1.5 · C2 (mockup + card + retirement) ~1 · C3 ~½–1 · C3b ~½ · C4 ~½.

**Extra tests:**
- Flow window: a flow on the baseline date (counted once); a flow before it (excluded); two baseline rows (refused); flows read = None (offline).
- SPY: missing on the end date (withheld) while a weekend start looks back.
- Account row: one that passes the mask but has a pre-16:00 cash read (searches back).
- Leverage verdict: an interest-gap month (unknown); the verdict sign flipping after the spread.
- Tax: an ST gain with an LT loss; Unknown-term rows; Dec 31 vs Jan 2 ET.

## 9. C1 review log and notes carried to C2/C3

- **C1 round 1 (Opus 5.5 reviewer): FIX-FIRST, 1 blocking.** `leverage_verdict` ignored position-days the engine dropped (`recon["unpriced"]`, `recon["journal_inconsistent"]`, `d1_short_of_period_end`), so it could print paid / cost on incomplete figures.
  - Fix: a new "incomplete" state with no verdict, plus a pure `leverage_window()` so callers load engine inputs over the right span.
  - Non-blocking items also applied: offline when `trades_df` is None; accurate left-out reasons; a net that rounds to $0 reads too close; the unknown-term tax count split; both tax rates read from the tax dict; a named 5-day SPY look-back; holiday and DST tests.
- **For C2 (app.py):**
  - Every money string carries raw `$`. Escape it through the existing `md=True` / `safe_html` path, or `$…$` pairs render as LaTeX.
  - `_cached_spy` has a 30-minute TTL. Key or refresh the SPY read on the latest account-row date, so a pre-16:00 fetch can't serve an intraday price as that day's close.
  - Build the leverage inputs with `period_inputs.load_engine_inputs(db, *leverage_window(...))`.
- **For C3 (cron):** same `leverage_window` rule. The debrief's own engine inputs are week-scoped and must not be reused for the leverage line.
- **For C4 (docs):** CLAUDE.md's `_GATE_FILES` count goes 92 → 93.
- **C1 round 2 (Opus 5.5 reviewer, confirming): SHIP, 0 blocking.** Non-blocking follow-ups, to take with C2 (each needs its own review because `scorecard.py` is a gate file):
  - The `start_uncovered` caption names the wrong cause when `pre_coverage_reason` is `no_valid_end_record` / `no_end_record_yet`. The verdict is still withheld; choose the clause from `pre_coverage_reason`.
  - A masked or invalid Sep 30 (October's d0) leaves windows starting Oct 1 permanently `incomplete`. That fails safe and is disclosed. Possible refinement: start at the first month whose d0 is valid.
  - Nested parentheses in the left-out info line when the `account_row_trust` reason has its own parentheses. Check it in the C2 screenshot.
