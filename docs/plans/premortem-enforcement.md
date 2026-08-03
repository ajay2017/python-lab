# Pre-Commitment Enforcement — Design Plan

**Date:** 2026-08-03
**Status:** SHIPPED 2026-08-03 (F-228, `docs/requirements.md`). Opus code
review: SHIP, 0 blocking, 2 non-blocking (one cosmetic wording fix applied,
one confirmed-harmless pre-existing convention, one confirmed-unreachable
defensive guard — see status log). Design was fully resolved first: all 4
open questions answered by the user, Opus design review returned FIX-FIRST
(3 blocking, 2 fix-first, 2 non-blocking), all resolved in this doc before
any code was written.

> **One-line spec:** actively monitor a user's own stated Pre-Mortem exit
> commitment (`trades.premortem_commitment`, free text captured at BUY) against
> live price data, and confront them with a dedicated alert when it appears to
> have fired while they're still holding — instead of only ever quoting it back
> as passive narrative context, which is all that happens today.

## Where this came from

Item #3 of a ranked list of "where I'd point the innovation energy" from a
2026-08-03 morning brainstorm (the same conversation that produced "The Judge,"
F-227, now fully shipped). The other two ranked ideas turned out to already be
covered by existing features (self-calibrating gates → the pre-existing Monthly
Intelligence Report F-153/F-153a; weekly-alpha attribution → the existing
Weekly Debrief's per-position contributor/detractor breakdown,
`stock_analyzer/debrief_advisor.py:178-217`). This is the one genuinely unbuilt
item.

Original framing: "the app should actively monitor those exit conditions and
confront you when one triggers and you haven't acted... That's the app holding
you to your own stated discipline — the purest expression of 'decides, not
informs.'"

## Verified current state (no guessing — checked against code 2026-08-03)

- **Schema:** `trades.premortem_commitment` (nullable `text`) and
  `trades.premortem_case_against` (nullable `jsonb`, 3 LLM-generated
  counterarguments) — DDL comment block at `stock_analyzer/db.py:107-121`.
  Both additive/optional columns; `save_trade` drops-and-retries if missing
  (`db.py:1158-1179`).
- **Write path:** `app.py:19983` ("🔍 Pre-Mortem — before you buy") →
  `app.py:20075-20087`, a single `st.text_area` — **genuinely unstructured free
  text**, no price/condition/numeric field of any kind. Placeholder text:
  "e.g. If Q3 guidance disappoints, or if this sector rotates out of favor
  within 60 days…". Hard-gated as required before a BUY can be recorded
  (`app.py:20416`).
- **Every current read site** — all passive, none check anything:
  - `stock_analyzer/portfolio_qa.py:601,702-703` — quoted back verbatim in a
    retrospective Q&A answer.
  - `app.py:27130-27151` → `stock_analyzer/thesis_red_team.py:140,155,158` —
    passed to the LLM as prompt context; the model is instructed to "quote it
    explicitly" if current evidence happens to support it — a qualitative
    judgment call by the LLM, not a deterministic trigger check.
  - `app.py:19576` — pre-save BUY-confirmation summary display only.
  - **Confirmed zero reads** in `exit_advisor.py`, `decision_bucket.py`, or
    `daily_briefing.py` — nothing today evaluates the commitment against
    live data at all.
- **Act Today card mechanism:** cards are produced in
  `stock_analyzer/daily_briefing.py::_act_today()` (`daily_briefing.py:1243-
  1394`+); each card is a dict shaped `{priority, icon, ticker, kind, action,
  directive, why, trigger, weight, pnl_pct}` (+ optional `shares`,
  `dollar_risk`, `risk_flags`). `decision_bucket.py`'s `_ACT_KINDS` frozenset
  (`decision_bucket.py:24-27`) currently recognizes `stop_breach, sell_signal,
  risk, risk_off_derisk, deterioration_exit, deterioration_trim` (+ conditional
  `critical_news`) — a new `premortem_triggered` kind would need to join this
  set to render correctly and be classified consistently.
- **Structured-extraction-from-free-text precedent:** already exists —
  `stock_analyzer/analyst_intel.py::extract_report()` (`analyst_intel.py:78-
  95`) turns pasted analyst-report text into a `list[dict] | None` of atomic
  facts, `None` on any failure, with all arithmetic done afterward in pure
  Python (never by the LLM) — the template this feature should follow.

## The core technical problem

`premortem_commitment` is a free-text sentence, not a structured condition. A
sentence like "if Q3 guidance disappoints" cannot be deterministically
evaluated against a price feed — there is no number to check. Some
commitments ARE numeric ("if it breaks $150" / "if it falls below $XX on
volume"), most are probably qualitative. **The feature only works at all for
the numeric subset**, and must be honest about the rest rather than inventing
a check that doesn't exist (zero-hallucination discipline — CLAUDE.md).

## Proposed architecture (3 pieces, same shape as the Judge's own three-part
design: extract → persist → check)

**Opus 4.8 design review (2026-08-03): FIX-FIRST — 3 blocking, 2 fix-first, 2
non-blocking.** All incorporated below before any code is written. See the
status log for the full original finding list; the resolutions are folded
into the architecture itself so this doc stays the single source of truth.

1. **Extraction (LLM, once per trade, cached).** New function, e.g.
   `stock_analyzer/premortem_monitor.py::extract_trigger(commitment_text)` —
   same shape as `analyst_intel.extract_report()`. Returns
   `{"checkable": bool, "direction": "below"|"above"|None, "price_level":
   float|None}` or `None` on API failure. **Conservative bias is the whole
   safety net here:** any commitment without an explicit stated number →
   `checkable=False`, never inferred or guessed. Runs once (at BUY-save time,
   or lazily on first Act Today build) since the source text never changes
   after save — not a repeated LLM cost. **Confirmed safe against staleness
   (non-blocking finding, resolved):** the trades grid is delete-only —
   every non-`Delete?` column is `disabled=True` and `premortem_commitment`
   isn't even displayed there (`app.py:21225-21248`) — so the only mutation
   path is delete-and-re-add, which creates a fresh row and triggers a fresh
   extraction. "Extract once, cache forever" is a safe assumption *today*;
   this doc records the assumption explicitly so it gets revisited if trade
   editing is ever added.
2. **New persisted fields.** Two new nullable `trades` columns —
   `premortem_trigger_price` (numeric), `premortem_trigger_direction` (text)
   — via the same additive-DDL-as-SQL-comment pattern `premortem_case_against`
   already uses. Ships inert until DDL is manually applied (established house
   pattern — same as `judgment_opinions`/`analyst_target_snapshots`).
3. **Lot-scoping (blocking finding #2, resolved).** The original "held ticker,
   no SELL after the BUY" rule was wrong — `port_df` is one row per ticker,
   but the trigger + commitment + BUY date live per-BUY-`trades`-row. A
   sell-all-then-rebuy makes "still held at ticker level" true again while the
   ORIGINAL lot (and its commitment) is actually closed — the naive rule
   could resurface an unrelated, already-closed lot's trigger against a brand
   new position. **Fix: reuse `stock_analyzer/tax_advisor.py::_build_open_lots
   (ticker, trades_df, today)` verbatim** — it already FIFO-replays the trade
   journal into currently-open lots (`{shares, buy_date, days_held}`),
   correctly closing a lot once its shares are fully sold. The governing
   commitment for a ticker is the trades row matching `(ticker, buy_date)` of
   the MOST RECENT still-open lot; a commitment whose lot has gone fully flat
   is never surfaced. (Same same-day-multiple-BUYs date-only-key limitation
   `investor_mirror.py`/`portfolio_qa.py`'s `_closed_shares_by_buy()` already
   accept elsewhere in this codebase — not solved anew here, same documented
   approximation.)
4. **Split-safety (blocking finding #1, resolved).** A stored
   `premortem_trigger_price` of $150 becomes a permanent false trigger after
   a 4:1 split drops the stock to ~$40 — and `_build_open_lots` already proves
   the fix exists: its SPLIT-row branch (`tax_advisor.py:113-121`) computes
   `ratio = new_total_shares / old_total_shares` and pro-rata-adjusts every
   open lot's share count by that ratio, while preserving each lot's original
   `buy_date`. **Fix: extend the same replay to also track each open lot's
   cumulative split ratio since its `buy_date`**, and divide
   `premortem_trigger_price` by that ratio before comparing against (already
   split-adjusted, since yfinance auto-adjusts) current price history — the
   inverse of the shares adjustment (shares ×ratio, so price level ÷ratio).
   Multiple splits compound correctly for free, since the replay already
   applies each SPLIT row in trade-date order. This is a small, additive
   extension to a single already-trusted function — not new math invented for
   this feature.
5. **Temporal semantic (blocking finding #3, resolved).** The original "fired
   if crossed at ANY point since BUY" is a stale-nag generator: a stock dips
   to $140 once, recovers to $200, stays there for months — the card would
   still say "it happened 90 days ago, you're still holding" forever, which is
   exactly the noise `feedback_calm_advisor_not_daytrading` exists to
   suppress. **Fix: the card's *presence* is gated on the MOST RECENT daily
   close still being beyond the trigger level in the stated direction** — a
   recovered stock stops firing the next trading day its close moves back
   across the line, no acknowledge/dismiss action required. Basis is daily
   close (not intraday low/high), matching the existing deterioration
   ladder's own close-basis precedent (`DETERIORATION_CONFIRM_DAYS`=3,
   `DETERIORATION_CONFIRM_REQUIRED`=2 — the "2-of-3 sessions below MA"
   confirmation rule). The card's copy still cites the FIRST date the level
   was breached (found by walking price history forward from the lot's
   `buy_date`) for the "it happened {N} days ago" framing — but that's
   narrative only; the gate that decides whether to show the card at all is
   always "is it *still* beyond the level today."

   **Corollary found during live validation, 2026-08-03 (not a bug — a
   direct consequence of the close-basis design above, undocumented until
   now): a trigger can never fire on the SAME DAY its BUY was logged.** The
   price-history comparison filters to `closes.index.date >= buy_date` and
   reads the last available row; on the entry day itself, that day's
   session hasn't closed yet, so there is no qualifying close and
   `detect_premortem_triggers()` correctly finds nothing to compare —
   regardless of how far the live price has already moved intraday. The
   earliest a genuinely-fired trigger can surface is the **next trading day**
   after the BUY, once a completed close for the entry day (or later) exists
   in the price history. This is the same reason a live "does it fire
   immediately" smoke test on a same-day BUY will always show nothing — not
   a broken pipeline, just the close-basis semantic working as designed one
   day earlier than an intraday check would.
6. **Plumbing (fix-first finding #4, resolved).** `_act_today()`
   (`daily_briefing.py:1243`) has no access to `trades_df` or price history
   today. Follow the EXACT existing precedent for feeding it a new
   pre-computed fact set: `deterioration_signals(port_df, held_data, spy_df)`
   is computed once at `daily_briefing.py:2335` and threaded in as the
   `deterioration=` parameter (also consumed by `_buy_candidates`,
   `_consolidate_act_today`). A new producer function — e.g.
   `stock_analyzer/premortem_monitor.py::detect_premortem_triggers(trades_df,
   held_data, today) -> list[dict]` (internally calling the extended
   `_build_open_lots` from #3/#4) — gets computed at the same call site and
   threaded into `_act_today()` as a new `premortem_triggers=` parameter,
   exactly mirroring `deterioration=`. `premortem_triggered` joins
   `decision_bucket.py`'s `_ACT_KINDS` frozenset (`decision_bucket.py:24-27`)
   alongside the existing kinds so it classifies consistently.

## Open questions

**Q1 — Placement. RESOLVED 2026-08-03: new Act Today card**, co-equal with
WATCH/TRIM/EXIT — not a buried expander. Matches the original framing ("the
purest expression of decides, not informs").

**Q2 — Re-nag cadence. REVISED by the design review (fix-first finding #5).**
The original "persistent daily, no ack state" default was flagged as
conflicting with the calm-advisor/anti-noise principle — an unacknowledgeable
daily nag on a position the user *consciously* chose to keep is exactly the
churn that principle exists to prevent. **The temporal-semantic fix above
(#5 in the architecture) substantially changes this trade-off**, though:
because the card's presence is now gated on "still beyond the level today,"
not "ever crossed," a persistent-daily card is no longer nagging about a
stale, already-resolved event — it's accurately reporting a genuinely
*ongoing*, unresolved breach, the same way an EXIT card persists daily only
while its own deterioration condition remains true. *Recommend (pending
explicit confirmation): persistent daily is now fine as-is* — no new
acknowledge/snooze state, no new constant — because the auto-resolving
close-basis check means it only ever shows while the trigger condition is
actually still true today. **RESOLVED 2026-08-03 — user confirmed:
persistent daily, no new state.**

**Q3 — Non-numeric/vague commitments. RESOLVED 2026-08-03: nothing extra for
v1.** Leave them exactly as today — passive LLM context in Red Team/Q&A only.
The new mechanism is scoped strictly to the checkable numeric subset.

**Q4 — Coexistence with existing deterioration cards.** If a ticker already
has a same-day WATCH/TRIM/EXIT card from `exit_advisor`, does the new
`premortem_triggered` card show alongside it, or fold into one combined card?
*Recommend (pending explicit confirmation):* show both —
`feedback_single_surface_priority` dedupes by *dimension*, and "your own
stated condition fired" is a genuinely different dimension from "the
algorithm's deterioration tier fired," not a restatement of the same fact.
**RESOLVED 2026-08-03 — user confirmed: show both separately.**

**New `constants.py` entries:** still none required — the temporal-semantic
fix (close-basis "still beyond the level today") replaces what would have
been a lookback/snooze constant with a self-resolving check that reuses the
existing close-basis convention rather than introducing a new threshold.

**Module naming (non-blocking finding, resolved).** `stock_analyzer/
premortem_advisor.py` already exists (Concept C — generates the LLM
"case against" narrative at BUY time). The new `premortem_monitor.py` is a
deliberately separate module: `_advisor` is generative (LLM, at BUY-time,
produces `premortem_case_against`), `_monitor` is extractive + deterministic
(LLM extraction once, then daily pure-Python price checks). Both modules'
docstrings should cross-reference each other to make this split explicit
when the code is written.

## Status log

- **2026-08-03** — Scoping started. Verified current schema/read-sites/card
  mechanism against code (no guessing). Proposed 3-part architecture
  (extract → persist → check) mirroring `analyst_intel.extract_report()`'s
  existing precedent. Four open questions raised for the user.
- **2026-08-03** — Q1 (placement → new Act Today card) and Q3 (vague
  commitments → nothing extra for v1) resolved via explicit user choice.
- **2026-08-03** — Opus 4.8 design review: **FIX-FIRST**. 3 blocking: (1)
  stock-split staleness on `premortem_trigger_price` had no resolution and no
  cited precedent, even though one exists (`tax_advisor.py`'s SPLIT-ratio
  math); (2) "held ticker, no SELL after the BUY" is the wrong lot-scoping
  rule — a sell-all-then-rebuy could resurface an unrelated closed lot's
  trigger; (3) "crossed at any point since BUY" is a stale-nag generator once
  a dip recovers. 2 fix-first: (4) the plumbing sketch underspecified how a
  new per-ticker fact reaches `_act_today()` when a clean existing precedent
  (`deterioration=` parameter) already shows exactly how; (5) the Q2 default
  (persistent daily nag) was flagged as conflicting with the calm-advisor
  principle. 2 non-blocking: module-naming collision risk with the existing
  `premortem_advisor.py`; the "extract once, cache forever" assumption should
  be stated explicitly rather than left implicit. **All resolved same
  session** by reusing existing, already-trusted code rather than inventing
  new logic: `tax_advisor.py::_build_open_lots()` extended to also track
  per-lot split ratio (fixes #1 and #2 together — FIFO lot resolution and
  split-ratio math come from the same replay), the temporal semantic changed
  to "still beyond the trigger today" on a close-price basis (fixes #3, and
  substantially de-risks #5's calm-advisor tension since a resolved dip
  naturally stops firing), and the `deterioration=`-parameter pattern named
  explicitly for #4. Module split documented for the naming note; the
  cache-staleness assumption confirmed safe today (trades grid is
  delete-only, `premortem_commitment` isn't even editable) and stated
  explicitly.
- **2026-08-03** — Q2 (revised: persistent daily is fine given the
  self-resolving fix) and Q4 (show both cards separately) confirmed by the
  user. **Design is now complete — all 4 questions resolved, all review
  findings resolved. Clear to move to build on explicit go-ahead.**
- **2026-08-03 — Built, same session, on explicit user go-ahead ("lets build
  it now").** All pieces from the resolved design:
  - `tax_advisor.py::_build_open_lots()` extended with a per-lot
    `split_ratio` key (cumulative product of every SPLIT ratio applied since
    the lot's `buy_date`), purely additive — verified existing callers only
    read by dict key, 5 new regression tests, all 64 tax_advisor tests pass.
  - Two new nullable `trades` columns (DDL comment in `db.py`, plus
    `_TRADE_COLS`/`load_trades` backfill/`save_trade` optional-column retry,
    matching `premortem_case_against`'s exact existing pattern).
  - New `stock_analyzer/premortem_monitor.py`: `extract_trigger()` (mirrors
    `premortem_advisor.generate_case_against()`'s exact LLM-call shape —
    Haiku, `LLM_REQUEST_TIMEOUT_SEC`, strict JSON parse, fails open to
    `None`) and `detect_premortem_triggers()` (pure, reuses the extended
    `_build_open_lots()` for both the lot-scoping and split-adjustment
    fixes). 29 new tests covering both, including all 3 of the design
    review's named false-trigger classes.
  - BUY-submission wiring in `app.py`: extraction runs once, synchronously,
    inside the existing `if submitted:` handler (never on every rerun) —
    fails open to unmonitored (`None`/`None`) on any error, no key, or an
    empty commitment.
  - `daily_briefing.py`: new `_act_today()` section "2.6" builds the card;
    `build_daily_briefing()` gained a `trades_df` param (both `app.py` call
    sites updated) and computes `premortem_triggers` at the same call site as
    `deterioration_signals()`. `premortem_triggered` added to `_KIND_RANK`
    (tier 3, alongside `deterioration_trim`) and `decision_bucket._ACT_KINDS`
    (correctly buckets as "act," not "aware") — but deliberately NOT added to
    `_REDUCE_ACT_KINDS`, so it never suppresses a same-ticker "hold"
    critical-news card the way an actual reduce does.
  - **A real coexistence bug was caught and fixed while wiring this, not by
    the design review** (a design review reads the plan, not the existing
    consolidation code): `_consolidate_act_today()` collapses ALL same-ticker
    Act Today items down to ONE surviving card (or drops everything but a
    mechanical exit) — which would have silently swallowed the premortem
    card on any ticker also carrying a deterioration card, directly
    violating user-confirmed Q4. Fixed by exempting `premortem_triggered`
    from the ticker-consolidation entirely (same passthrough treatment as
    ticker-less macro cards), verified with a manual end-to-end scenario and
    2 dedicated regression tests (coexists with `deterioration_trim`;
    coexists even with a mechanical `stop_breach`, which normally wins
    outright and drops everything else).
  - 10 new tests in `tests/test_premortem_act_today.py` covering the card
    build, the coexistence fix, and `decision_bucket` classification/dedup
    interactions (`classify_bucket` returns "act"; `_is_reduce` is False;
    `_reconcile_act` does not fold a same-ticker critical-news card).
  - Verified: py_compile clean on all 5 touched/new files, 3076 → 3120 tests
    pass (44 new, 0 regressions), `check_constants_documented.py` passes (no
    new constants).

  **Opus 4.8 code review: SHIP, 0 blocking.** Real scrutiny, not a rubber
  stamp — specifically traced: the split-ratio math is the correct inverse
  of the shares adjustment (2:1 split → $150 becomes $75, matches how the
  real stock price halves) and compounds correctly across multiple splits;
  the lot-scoping fix correctly binds to the governing (most-recently-opened)
  lot and a sell-then-rebuy correctly uses the NEW lot's trigger, not the
  old one; the self-resolving temporal gate cannot diverge from its own
  first-breach walk; the `_consolidate_act_today()` exemption is complete —
  checked every OTHER by-ticker grouping in the file plus
  `decision_bucket._reconcile_act()` and confirmed none of them can still
  swallow a premortem card; schema/backfill/retry tuples are symmetric
  across all 3 sites; the BUY-submission wiring only fires once per
  submission, never per rerun. **2 non-blocking findings:** (1) the card's
  wording read awkwardly for the "above" direction ("broke $50.00 above")
  — fixed to word-before-number for both directions ("broke below $150.00"
  / "broke above $50.00"), re-verified all 10 Act Today tests + full suite
  still pass. (2) a UTC-vs-exchange-local date-boundary edge case in the
  price-history window filter (same pre-existing convention used elsewhere
  in this codebase, confirmed harmless — only trims the earliest edge of
  the window, never affects the active-trigger gate) — left as-is per the
  reviewer's own assessment, not a real gap. A second test-runner agent
  independently confirmed 3120/3120 passing, 0 regressions, before this
  review landed.

  **This closes F-228 — Pre-Commitment Enforcement is shipped.** The one
  item from the 2026-08-03 morning brainstorm's ranked list that was
  genuinely unbuilt is now live.

- **2026-08-03 — DDL applied by user; live validation same session.** User
  logged a real SPOT BUY ($486) with a qualitative commitment ("the composite
  score's reliance on that spike will prove misleading") expecting an
  immediate same-day Act Today card. None appeared. Investigated (Explore
  agent, read the actual code, not guessed) and confirmed via a direct
  Supabase query on the trade row: `premortem_trigger_price=NULL`,
  `premortem_trigger_direction='not_checkable'` — **the extraction pipeline
  is working correctly**; the commitment simply had no explicit numeric
  price to extract, so it was correctly marked unmonitorable rather than
  guessed. Separately confirmed the close-basis design (architecture point
  #5 above) has a real, previously-undocumented corollary: a trigger can
  never fire on the same day its BUY is logged, since that day's close
  doesn't exist yet in price history when the check runs — the earliest a
  fired trigger can surface is the next trading day. Ruled out Home's
  `_home_synth_cache` (its signature does pick up a new trade via a
  `(Ticker, Shares)` hash) and the BUY-confirmation rerun flow (extraction
  correctly runs and persists before any rerun) as causes — this was purely
  the close-basis semantic behaving as designed, one day earlier than an
  intraday check would have shown something. **Added a UI help-text hint**
  on the Pre-Mortem commitment `st.text_area` (`app.py`, near line 20075)
  explicitly telling the user to include an explicit price if they want the
  commitment to be actively monitorable — a purely qualitative one is saved
  but never auto-checked. No logic changed, so no Opus review required (copy
  only); verified `py_compile` clean.
