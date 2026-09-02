# 🧾 Prior Trades tab (Analysis page) — Plan

**Status:** ✅ **PHASE 1 + PHASE 2 SHIPPED 2026-08-14** (tab, journey chart, Trade Plan
pointer F-237c, vs-SPY window guard F-237d). ✅ **F-237e (situational-category tag)
SHIPPED 2026-08-25** — see §7. ✅ **The two remaining §7 carry-overs (cropped-chart
caption, per-rerun recompute cost) both CLOSED 2026-09-02** — nothing outstanding on
this feature. Design questions resolved with the user 2026-08-14 (see §0).
**Proposed F-ID:** F-237
**Mockup:** [docs/mockups/prior-trades-tab.html](../mockups/prior-trades-tab.html)
**Originating ask (2026-08-14):** DELL surfaced under New Positions to initiate; the
user had traded DELL before, but the Analysis page showed nothing about that prior
history — no dates, no P&L, no record of what they'd said or learned last time.

---

## 0. Decisions taken (2026-08-14)

| Question | Decision | Consequence |
|---|---|---|
| Unit of display | **Round trip** — one card per 0 → held → 0 cycle; multiple buys average into one entry price | Individual fills are not shown in v1. If the averaged entry ever hides something the user needs (e.g. "was that a good add?"), the fallback is an expandable per-fill ledger inside each card — noted, not built. |
| Phase 2 pointer on Trade Plan | **Separate deploy**, gated on Phase 1 numbers verified against real live data | The tab ships alone. The count badge in the tab label is the only discovery affordance in Phase 1. |
| Tab label | **`🧾 Prior Trades`**, with a count suffix (`🧾 Prior Trades (2)`) only when history exists | Plural; count omitted at zero so an untraded name reads as `🧾 Prior Trades`. |
| Chart | **Two stacked panels** (price + fills, and P&L-while-held), sharing one x-axis | See §4a. Not a dual-axis single plot. |
| Chart placement | **Top of the tab**, KPI stats merged inline into the chart card's header | Replaces the separate 4-tile KPI row. See §4a. |

## 1. The gap this closes

The app already stores everything needed — `trades` rows carry `traded_at`,
`price`, `shares`, `cost_basis`, `realized_pnl`, `trigger_type`, `followed_signal`,
`deviation_reason`, `user_thesis`, `thesis_source`, `lesson`, `lesson_category`,
`premortem_*`, and `decision_context` — but **no surface answers "what happened
the last time I owned THIS name."** Existing consumers are all portfolio-wide or
pattern-level:

| Existing surface | Unit of analysis | Overlap? |
|---|---|---|
| Trade Review `_diag_re_entered_tickers` | portfolio-wide, "tickers you re-entered that are net-negative" | Aggregate only — never per-ticker on Analysis |
| Decision Journal → Pattern Library | `lesson_category` counts across all names | Category-level |
| Behavioral Fingerprint (F-193) / Investor Mirror (F-194) | behavioural patterns | Pattern-level |
| Pre-Mortem (F-187, `app.py:1700-1720`) | feeds ticker-exact `lesson_category` strings **into the LLM prompt** at BUY | Invisible input, not a readable history |

So the per-ticker history view is a genuinely new surface, and per
`feedback_single_surface_priority` the Analysis page is its correct home — it's
where the ticker-level decision is actually read.

## 2. What it is (and firmly is not)

- **Is:** a factual, read-only record of the user's own past positions in this ticker.
- **Is not:** a signal, a score input, a gate, or a modifier of the composite/verdict.
  It never suppresses or annotates a recommendation. The Trade Plan tab remains the
  decision surface.
- **Tone rule:** state facts (dates, prices, P&L, what you wrote). Never render a
  verdict on the user's skill in the name ("you're bad at DELL"). This is the line
  drawn by the **Self Track Record decision-moment rejection** (memory
  `project_self_track_record`): ticker-specific *facts* at the decision moment are the
  accepted class (F-195 already does it); portfolio-wide *alpha judgment* at the
  decision moment is the rejected class.

## 3. Placement

6th tab on the per-ticker Analysis tab strip (`app.py:18728`), after
🏦 Analyst Coverage:

```
📋 Trade Plan | 📈 Chart | ⚖️ Risk | 🔬 Deep Dive | 🏦 Analyst Coverage | 🧾 Prior Trades (2)
```

Tab always renders (consistent structure across tickers); the count suffix appears
only when history exists. On a Sell verdict the plan tab becomes 🚪 Exit Plan — the
Prior Trades tab is unchanged in either case.

## 4. Content (see mockup for the visual)

**Headline strip (4 tiles):** Round Trips · Net Realized · Record (W·L) · Avg Hold.

**Summary line:** one neutral sentence — how many times held, net realized, and the
same-window SPY comparison.

**Episode cards, newest first.** One card per *round trip* (position goes 0 → >0 →
back to 0). Each card carries: entry avg price + share count + number of buys, exit
avg price + number of sells, realized $/%, vs-SPY over that exact window, hold days,
and badges for `trigger_type` (engine / self-initiated / stop) and `followed_signal`.

**"Since you exited" line:** last exit price → today's price. A fact, framed
neutrally — no "you left money on the table."

**Expander — "What you wrote at the time":** `user_thesis`, `premortem_case_against`
+ `premortem_commitment` + trigger price/direction, `lesson` + `lesson_category`,
`deviation_reason`, `notes`, plus a conditions-at-entry line rendered from
`decision_context` (macro regime, market tone, portfolio value, position count, top
sector weight, portfolio beta). This is the highest-value block: it is the only place
in the app where the user's own pre-mortem is replayed against the outcome.

**Open position:** if currently held, an OPEN card sits on top with unrealized P&L;
vs-SPY reads `—` until closed. Realized stats stay closed-trips-only, labelled as such.

## 4a. The journey chart (added 2026-08-14 on user request)

Sits between the KPI tiles and the round-trip cards. Plotly (`plotly.graph_objects`,
the house library — `app.py:7`), dark theme, native hover tooltips.

**Form: two stacked panels sharing one x-axis** — NOT one plot with two y-scales.
A dual-axis chart aligns two arbitrary scales and invents a correlation that isn't
in the data; it's the single most-flagged charting defect. Two panels cost nothing
and are unambiguous.

- **Panel 1 — price + your fills.** Thin 2px neutral price line (context, not the
  story). `add_vrect` bands shading each holding period, tinted green/red by that
  trip's outcome at ~0.08 opacity — matching the existing in-house `add_vrect`
  convention at `app.py:1317-1321`. Buy = green ▲, signal-exit = blue ▼, stop-out =
  red ▼; ≥10px markers with a 2px surface-colour ring so overlapping fills stay
  readable. Today's price gets a dot + direct label.
- **Panel 2 — position P&L % while held.** One arc per episode from 0% at entry,
  green/red by outcome, with gaps where flat. Solid brighter zero line. A faded
  dashed continuation after the last exit shows what the position would have done
  had it been held (see §8 — this is the anchoring-risk element).

**Percent, not dollars, on panel 2.** Two trips of different position size are only
comparable in %. The $ figures are already on the cards and in the tooltip.

**Labelling:** direct-label selectively — entry price on each episode's first buy,
exit price on the final sell, realized % at each arc end, today's price. Never a
number on every point. Gridlines are solid hairlines, never dashed (the dashed
stroke is reserved for the hypothetical continuation, where it carries meaning).

**Accessibility:** every value plotted is also written out on the round-trip cards
directly below, so the chart is an enhancement and never the only way to read a
number — the "table-view twin" requirement is satisfied by the cards themselves.
Buy/sell identity is carried by marker *shape* as well as colour, so it survives
colour-vision deficiency.

**Data cost — the one real gotcha.** The Analysis page's `r["df"]` is fetched at
whatever the sidebar **History period** selector says (`app.py:18134`, options
`3mo|6mo|1y|2y`, default `6mo`). A round trip older than that window would be
silently cropped out of the chart. So the tab must fetch its own history at the
smallest span that actually covers the oldest entry (`6mo → 1y → 2y → 5y`), via a
dedicated `@st.cache_data(ttl=1800)` helper — independent of the sidebar control.
One extra cached price fetch per ticker per 30 min, bounded to ≤4 cache entries per
ticker. On fetch failure, degrade to `r["df"]`'s window and caption which trips
fall outside it — never silently truncate.

**Chosen (2026-08-14):** the two-panel version. The P&L-only and price-only
alternatives were shown and rejected — P&L-only drops the "where does today's price
sit against everything I've paid" anchor, which is the actual decision question.

**Placement — the KPI strip is merged into the chart card's header** (decided
2026-08-14), rather than sitting as a separate 4-tile row above it. Rationale: the
tiles and the chart say overlapping things, and stacking them pushed the chart ~150px
down the page. One card, header line = title + date span, second line = the four
stats inline (`2 round trips · +$948 net realized · 1W·1L · 57d avg hold`), then the
legend, then the two panels. The chart is now the first thing on the tab.

Side-by-side layouts (stats left / chart right, or chart left / cards right) were
considered and rejected: they cost the chart a third of its width for 9 months of
daily bars, and — the deciding factor — **Streamlit columns don't stick**, so a
side-by-side chart scrolls away with the cards anyway. It buys a shorter page, not a
persistent chart.

## 5. Edge states (all shown in the mockup, all required for v1)

| State | Handling |
|---|---|
| Never traded | Empty state: "This would be your first position in this name." |
| SELL with no matching BUY (journal gap: rebaseline, pre-import history) | Amber banner naming the date, and stats scoped to only what the journal accounts for. Mirrors `recalculate_from_trades`'s existing `warnings` behaviour and the `project_fundamentals_gate` posture — **withhold rather than fabricate.** |
| `action='SPLIT'` row inside a round trip | Amber banner: prices shown as-recorded, not split-adjusted; compare P&L not per-share prices. (`recalculate_from_trades` treats SPLIT as a state overwrite — `db.py:1789`.) |
| Held but never closed | Info banner; realized stats appear after first exit. |
| SPY history doesn't cover the episode window | vs-SPY renders `—`, never 0. |

## 6. Implementation shape

**New pure module `stock_analyzer/ticker_history.py`** (domain logic stays out of
`app.py` per CLAUDE.md):

```
build_ticker_history(trades_df, ticker, current_price, spy_history_df, today) -> dict
  {
    "episodes": [ {entry_date, exit_date, hold_days, entry_avg, exit_avg,
                   shares, n_buys, n_sells, realized_pnl, realized_pct,
                   vs_spy_pct, status: open|closed, trigger_types,
                   followed_signal, journal: {...}, context: {...}}, ... ],
    "totals":   {n_round_trips, n_open, net_realized, net_realized_pct,
                 wins, losses, avg_hold_days, total_days_in_name, vs_spy_pct},
    "warnings": [ {"kind": "orphan_sell"|"split_in_window", "date":..., "msg":...} ],
    "spy_available": bool,
  }
```

Returns `None` (not `{}`/`[]`) when trade history is unavailable — the offline-sentinel
contract enforced by `check_antipatterns.py`.

**Cost-basis convention:** episode realized P&L sums the **stored `realized_pnl` on
SELL rows** (authoritative, weighted-average basis — the same convention
`db.recalculate_from_trades` writes), recomputing only when the stored value is null.
This guarantees the tab never disagrees with the Portfolio page. Do **not** re-derive
via `trade_review._pair_sells_to_buys` — that's FIFO-lot matching, a different unit
than a round trip, and would produce numbers that differ from the journal's own.

**Reuse:** `trade_review._build_spy_returns` / `_spy_return_between` for the vs-SPY
math; `_cached_spy(period)` (`app.py:2414`) for SPY history, with the period picked
as the smallest of `6mo|1y|2y|5y` covering the oldest episode — bounds the cache to
4 entries (relevant to `project_perf_cache_bounding`).

**Data source:** `st.session_state.trades_df`, already loaded at bootstrap for every
session (`app.py:2028`) — no new fetch, no new DB call.

**No new `constants.py` entries.** Display caps (episodes shown before "show all")
live as module-local constants in `ticker_history.py`, matching the precedent in
`trade_review.py:36` ("Module-local analytics tuning"). This is presentation, not
investment policy — so Hard Rule #1 / #4 aren't engaged and the constants-doc check
isn't burdened.

**Tests** — `tests/test_ticker_history.py`: single round trip; multi-buy averaged
entry; partial sells; re-entry after full exit; currently-open; orphan SELL; SPLIT
inside window; empty/None trades; SPY unavailable; timezone-mixed `traded_at`
(`feedback_pandas_mixed_tz_parsing` — parse with `utc=True, format='ISO8601'`, same
as `recalculate_from_trades`).

## 7. Phasing

Per `feedback_phased_ux_rollout_cadence` — one phase per deploy, pause for live review.

- **Phase 1 — the tab.** Everything in §4/§5. Ships alone so it can be reviewed
  against real DELL data before anything points at it.
- **Phase 1 carry-over — four accepted deferrals** (Opus re-review 2026-08-14 confirmed
  none can produce a wrong number today). Listed here rather than only in a memory,
  because a gate that lives in one place drifts — the class
  `feedback_doc_integrity_zero_hallucination` warns about:
  1. ~~**SPY-window guard**~~ — ✅ **DONE 2026-08-14**, shipped with Phase 2 as F-237d.
     Closed via `_spy_covers()` in `ticker_history.py`, checked **per episode** (an old
     trip reports `—` while a recent one still gets a real figure) rather than the
     all-or-nothing app-side skip originally sketched here. Was the only remaining path
     by which `vs_spy_pct` could be *wrong* rather than absent, so it was pulled forward
     to ship alongside the pointer that makes that number more prominent.
  2. ~~**Cropped-chart caption.**~~ — ✅ **DONE 2026-09-02.** New pure
     `ticker_history.chart_start_gap(first_entry_date, px_start_date)` decides whether
     the widen attempt in §4a actually reached back far enough; when it didn't, the
     existing span caption now reads "chart may be missing older round trips — price
     history couldn't be widened back to your first entry" instead of the previously
     unconditional (and, in this case, wrong) "chart shows a wider price window for
     context". No `unsafe_allow_html`, no gate/score touch — `app.py` is not a
     `_GATE_FILES` member and Prior Trades is awareness-only, so no reviewer required.
  3. ~~**`_m()` privacy mask**~~ — ✅ **DONE 2026-08-25.** `decision_context.
     portfolio_value` in the conditions-at-entry line now wraps through `_m()`,
     matching the same figure's masking on Home/Summary. Scoped deliberately narrow:
     the round trip's OWN entry/exit/realized/unrealized figures on this tab are
     unchanged — those are the ticker-specific facts the tab exists to show, not the
     ambient portfolio-wide total this one line borrows from `decision_context`.
     Widening the mask to the round-trip figures themselves would be a real UX
     decision (arguably counter to "your own history in this name" tone), not a bug
     fix, and was not made here.
  4. ~~**Per-rerun recompute cost.**~~ — ✅ **DONE 2026-09-02.** New pure
     `ticker_history.trades_fingerprint(trades_df, ticker)` — a cheap, order-stable
     fingerprint of one ticker's trade rows (excludes free-text fields like notes/
     lesson/thesis, which don't feed the PnL/chart math) — keys a single-slot
     `st.session_state["_prior_trades_render_cache"]` memo (not a growing per-ticker
     dict, per `project_perf_cache_bounding`'s bounding discipline) covering the
     widen/SPY-rebuild/`build_pnl_series`/figure-construction chain. Keyed on
     `(ticker, trades_fingerprint, price)` — live price is included deliberately, so
     unrealized figures on an open position still update when the price cache
     refreshes, rather than freezing for the rest of the session.
- **Two NEW defects found live 2026-08-25, neither part of the original 4 above — both
  FIXED same day.** Found via two real screenshots (CRWD, MRVL) showing the
  "What you wrote at the time" expander missing content that should have been there:
  1. ~~**"Conditions at entry" caption never rendered, for ANY trade, ever.**~~ — ✅
     **FIXED.** `decision_context.build_snapshot()` (`stock_analyzer/decision_context.py`)
     nests its output under `market.{macro_regime,tone}` and
     `portfolio.{value,beta,highbeta_share_pct,n_positions,top_sector}`, but the render
     code at this expander read FLAT top-level keys (`_pt_cx.get("macro_regime")`,
     `.get("portfolio_value")`, etc.) that never existed in the real snapshot shape —
     a schema mismatch between the producer and the one consumer, present since this
     line shipped 2026-08-14 and never caught because no live trade with a populated
     snapshot was screenshotted until now. Every `.get()` in that block silently
     returned `None`, so `_pt_cbits` was always empty and the caption never appeared —
     not a data-capture gap, a pure display bug. Fixed by reading the correct nested
     paths (`_pt_cx.get("market")`/`.get("portfolio")`, each defended with an explicit
     `isinstance(..., dict)` check rather than `... or {}`, since the AST-based
     `check_antipatterns.py` gate correctly flags the latter as
     `OFFLINE_SENTINEL_COLLAPSE` even here). **Lesson: a feature that only degrades
     gracefully (never crashes, never warns) can silently render nothing forever — the
     "3 date-time-bomb"/"disprovable by construction" checks above only catch *wrong*
     numbers, not an always-empty optional block.**
  2. ~~**Pre-mortem case-against rendered as a raw Python list-of-dicts.**~~ — ✅
     **FIXED.** `premortem_case_against` is `jsonb`, always either `None` or a
     `list[dict]` of exactly 3 `{"angle","argument"}` items by construction
     (`premortem_advisor.generate_case_against` — pillar/portfolio/macro), never a
     plain string. It was in the same generic `f"{_lbl}  \n> {_pt_v2}"` string-loop as
     `user_thesis`/`notes`/etc., which just `str()`-interpolated the whole list —
     visibly showing `[{'angle': 'pillar', 'argument': '...'}, ...]` verbatim on a real
     CRWD trade card. Pulled out of the generic loop into its own block that iterates
     the 3 items and renders each with the same friendly angle label
     (📊 Pillar concern / 📦 Portfolio impact / 🌐 Macro / earnings) the live
     Log Trade pre-mortem preview already uses, via native `st.markdown` blockquotes
     (no `unsafe_allow_html`, matching this tab's own no-raw-HTML convention — the
     LLM-generated `argument` text is not escaped for HTML since it never goes through
     `unsafe_allow_html` here).
- **Phase 2 — the Trade Plan pointer.** ✅ **SHIPPED 2026-08-14** as F-237c. A one-line
  `st.info` after the R:R caveat banner (Buy branch) and after the ATR-level caption
  (Exit branch), so the history is discoverable at the decision moment rather than
  requiring a tab click. Factual only — count, net realized dollars, first-entry month,
  last exit vs today; **no alpha figure and no verdict on the user's skill in the name**
  (the rejected class — see §2). Never gates. Its gate cleared the same day: DELL
  produced 3 closed round trips + 1 open, and the open position's unrealized −$36.96
  reconciled to the cent with the Summary Scorecard (4 sh @ $499.68 avg vs $490.44 =
  −1.8%), confirming the "cannot disagree with Portfolio" property on real data.
- **F-237e — situational-category tag.** ✅ **SHIPPED 2026-08-25.** The "What you wrote
  at the time" expander now shows the entry's `situational_category` (F-257's locked
  vocabulary — Institutional Flow / Earnings Catalyst / Technical Read / Macro-News /
  Other) as a `:blue-background[📍 …]` badge, read off the opening BUY row via
  `ticker_history._build_journal`. Blocked until F-257 shipped the column (2026-08-25) —
  not a missed deferral, the field didn't exist before then. Purely additive display,
  no new decision content, no reviewer required (not a `_GATE_FILES` touch, pure-additive
  wiring of an already-vocabulary-locked field per CLAUDE.md's review-economy skip rule).
  A SELL-side analogue is not applicable — `situational_category` is a BUY-only field by
  F-257's own design, so there is nothing on the SELL leg to surface here.

## 8. Known judgment call, flagged for the user

The "since you exited, it's up 29%" line and the prior-entry price are **anchoring
fuel** — a name you once owned at $118 feels expensive at $160 even when the engine
says otherwise. Showing it is still right (it's the truth, and hiding it would be the
bigger distortion), but Phase 1 should be watched for whether it starts driving
"I'll wait for it to come back" behaviour. If it does, the fix is a neutral caption,
not removal.

## 9. Definition of Done (per CLAUDE.md)

1. No `constants.py` change → constants-doc check unaffected.
2. `docs/requirements.md` — add F-237.
3. `docs/shipped-log.md` — entry; remove nothing from the queue (this is new work).
4. In-app User Guide (`app.py`, `📖 User Guide`) — describe the tab.
5. `docs/architecture.md` — module row for `ticker_history.py`.
6. `docs/user-manual.md` — Analysis-page section.
7. Memory — `project_prior_trades_tab`.
8. This plan doc's own `**Status:**` line bumped on ship (the drift class called out
   in `feedback_doc_integrity_zero_hallucination`).
9. `reviewer` (Opus) pass before commit — new user-facing surface reading trade data;
   citation in the commit body.
