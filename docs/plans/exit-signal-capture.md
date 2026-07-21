# Plan: Exit Signal Forward Capture + Behavioral Fingerprint v2 (Concept A Exit-Side)

**Status: Phase 1 SHIPPED 2026-07-18 (commit `f86147d`); cron capture gap closed 2026-07-21 (see below). Phase 2 pending ≥30 days data accumulation.**
**Author:** Ajay Kumar
**Date:** 2026-07-18

---

## Context

Behavioral Fingerprint v1 (F-193, shipped 2026-07-17, commit `da59b00`) covers the Buy-side only:
three sample-gated patterns on 🎯 My Edge that answer *how the user behaves when entering positions*
(momentum chasing, conviction-tier response, opening-window timing).

The exit side was explicitly scoped out because exit signals — WATCH/TRIM/EXIT from
`exit_advisor.assess_holding()` and RISK_OFF from `exit_advisor.assess_risk_off_derisk()` — are
computed live each session inside `build_daily_briefing()` and **never persisted**. There is no
historical record to mine. This is a structural gap, not a data-quality gap.

This plan closes that gap in two phases:

- **Phase 1 — Exit Signal Forward Capture** (the prerequisite): instrument the Brief build to
  persist every signal it computes to a new `exit_signals` Supabase table. Silent background write;
  no UI. Starts the forward clock.
- **Phase 2 — Exit-Side Behavioral Patterns**: once enough data accumulates, add exit-side pattern
  cards to the 🧬 Behavioral Fingerprint tab on 🎯 My Edge that answer *how the user responds to
  exit signals*.

Together, Buy-side v1 + Exit-side v2 close the full decision-cycle loop: entry bias → exit bias.

---

## Why this matters

The app already computes exit signals. The unanswered question is whether the user acts on them —
and if so, how fast, and to what effect. Loss aversion (holding losers too long, not acting on
deterioration signals) is the single most common individual-investor return-destroyer. The only way
to surface this honestly is to log what the app said and then check what the user did.

Without Phase 1 running, Phase 2 can never be built. Phase 1 is also independently valuable: once
capture is live, a future surface could show "you have ignored 4 consecutive TRIM signals on TICKER"
directly in the Brief or on the position card.

---

## What exists today (verified against HEAD)

All values transcribed from source, not memory.

| Piece | File | Lines | Notes |
|---|---|---|---|
| Deterioration signal computation | `stock_analyzer/daily_briefing.py` | ~1073 | `deterioration_signals()` — calls `exit_advisor.assess_holding()` per ticker, returns `list[dict]` |
| Deterioration assess core | `stock_analyzer/exit_advisor.py` | 204–313 | `assess_holding()` returns dict with ticker/tier/dd_from_peak_pct/price/rel_strength/below_ma_count/pnl_pct/weight_pct/shares |
| Pure tier classifier | `stock_analyzer/exit_advisor.py` | 127–183 | `classify_deterioration_tier()` — pure scalar, no I/O |
| Risk-off derisk signals | `stock_analyzer/exit_advisor.py` | 399+ | `assess_risk_off_derisk()` — returns `list[dict]` with kind="risk_off_derisk" / ticker / weight / pnl_pct |
| Signal buckets | `app.py` | 4274–4275 | `split_defensive(_daily_brief["act_today"], _daily_brief["review_list"])` |
| Brief result | `daily_briefing.py` | (build fn) | `{"act_today": [...], "review_list": [...], ...}` — TRIM/EXIT in `act_today` (kind="deterioration_trim"/"deterioration_exit"/"risk_off_derisk"), WATCH in `review_list` (action={"type":"DETERIORATION_WATCH"}) |
| Behavioral Fingerprint v1 | `stock_analyzer/behavioral_fingerprint.py` | full | Buy-side only; 3 functions; no DB calls; pure computation over `match_recs_to_trades()` output |
| Buy-side rec matching | `stock_analyzer/recommendations_history.py` | ~74 | `match_recs_to_trades(recs_df, trades_df)` — matches by `trigger_type=="RECOMMENDATION"` on same day |
| No `exit_signals` table | `stock_analyzer/db.py` | — | Confirmed absent. RLS-protected tables: holdings/watchlist/trades/recommendations/manual_stops/fundamentals_cache/sector_cache/daily_snapshots |

---

## Phase 1 — Exit Signal Forward Capture

### 1.1 What to capture

One row per (ticker, signal_date, signal_type). Idempotent upsert so repeated Brief builds on the
same day don't duplicate rows.

| Column | Type | Source in the Brief dict | Notes |
|---|---|---|---|
| `ticker` | text | dict["ticker"] | |
| `signal_date` | date | today, NY ET | same convention as `rec_date` in recommendations |
| `signal_type` | text | mapped from kind/action.type | 'WATCH' \| 'TRIM' \| 'EXIT' \| 'RISK_OFF' |
| `composite_score` | numeric | must enrich from `port_df["Composite"]` at capture time | not in the assess_holding dict — see §1.3 |
| `price_at_signal` | numeric | dict["price"] (assess_holding) | for future outcome calculation in Phase 2 |
| `dd_from_peak_pct` | numeric | dict["dd_from_peak_pct"] | null for RISK_OFF (not applicable) |
| `pnl_pct` | numeric | dict["pnl_pct"] | available for both deterioration and risk_off |
| `below_ma_count` | int | dict["below_ma_count"] | deterioration only; null for RISK_OFF |
| `rel_strength` | numeric | dict["rel_strength"] | deterioration only; null for RISK_OFF |

**What NOT to capture:** atr_pct, dollar_pnl, dollar_risk, weight_pct, shares — these are portfolio
snapshots that change every session and aren't relevant to the behavioral question. The behavioral
analysis needs the signal identity and the context at firing, not position accounting.

### 1.2 Signal-type mapping

```
_daily_brief["act_today"]  item["kind"] == "deterioration_trim"   → signal_type = "TRIM"
_daily_brief["act_today"]  item["kind"] == "deterioration_exit"   → signal_type = "EXIT"
_daily_brief["act_today"]  item["kind"] == "risk_off_derisk"      → signal_type = "RISK_OFF"
_daily_brief["review_list"] item["action"]["type"] == "DETERIORATION_WATCH" → signal_type = "WATCH"
```

### 1.3 composite_score enrichment

`assess_holding()` does not carry `composite_score` — it is a purely position-health function that
knows nothing about the scoring engine. At the capture site in `app.py`, `port_df` is already built
(`_port_df_enriched` in session state after Home synthesis). Look up `composite_score` from
`port_df` by ticker before writing: `port_df.set_index("Ticker")["Composite"].get(ticker)`. If
`port_df` is unavailable (None), write `None` for composite_score — **never fabricate a default**.

### 1.4 DB schema (DDL)

```sql
create table if not exists exit_signals (
    id               bigint primary key generated always as identity,
    ticker           text not null,
    signal_date      date not null,
    signal_type      text not null,
    composite_score  numeric,
    price_at_signal  numeric,
    dd_from_peak_pct numeric,
    pnl_pct          numeric,
    below_ma_count   int,
    rel_strength     numeric,
    surfaced_at      timestamptz default now(),
    constraint exit_signals_unique unique (ticker, signal_date, signal_type)
);

alter table exit_signals enable row level security;
create policy "service role full access" on exit_signals
    for all to service_role using (true) with check (true);
```

One-time DDL to apply in Supabase SQL editor before the first deploy that includes Phase 1 code.
Feature is inert (the save function is a no-op) until the table exists — same pattern as
`decision_context` (F-186 Wave 1a).

### 1.5 Implementation footprint

Two file touches only:

**`stock_analyzer/db.py`** — add `save_exit_signals_batch(signals: list[dict]) -> None`:

```python
# signals = list of dicts, each with keys matching the table columns above
# Upsert on (ticker, signal_date, signal_type) — idempotent for repeated same-day Brief builds
```

Pattern exactly mirrors `save_recommendations()` (db.py ~1580): build a list of row dicts,
call `.table("exit_signals").upsert(rows, on_conflict="ticker,signal_date,signal_type").execute()`.
Wrap in `try/except Exception` — log a warning, never raise (Brief must never fail because a
capture write failed).

**`app.py`** — add one call after `build_daily_briefing()` returns, inside the Home MISS path
(where `_daily_brief` is freshly built, not replayed from cache). Extract signals from
`_daily_brief`, enrich with composite_score from `port_df`, call `db.save_exit_signals_batch()`.

This must be in the **MISS path only** (cold build), not the HIT path (cache replay) — the HIT
path would re-save the same rows on every page load, which the upsert handles safely but wastes
Supabase writes. Dedup at the DB level still covers any case where the miss path fires twice in
a session.

No new module. No new constants. No RLS change (the policy above uses service_role, matching
every other table).

### 1.6 read-back helper (for Phase 2)

Add `load_exit_signals(days_back: int = 365) -> pd.DataFrame` to `db.py`. Loads all rows within
`days_back` days of today. Same date-filter pattern as `load_recommendations()`. Returns an empty
DataFrame (not None) on failure — callers must handle the empty case gracefully.

---

## Phase 2 — Exit-Side Behavioral Patterns

**Build this only after Phase 1 has been running ≥ 30 days.** The same `BEHAVIORAL_MIN_SAMPLE_N = 8`
gate from v1 applies to every bucket — cards suppress to "insufficient data" automatically until
enough signals accumulate. Don't rush this.

### 2.1 "Acted on" matching — exit side

Buy-side v1 matches on `trigger_type == "RECOMMENDATION"` same-day in `match_recs_to_trades()`.
Exit-side cannot use the same mechanism: users often sell without tagging the reason, and the sell
may happen 1–5 days after the signal fires, not on the same day.

Matching rule for exit-side:
- A signal is "acted on" if a SELL trade exists for the same ticker **within
  `EXIT_SIGNAL_ACT_WINDOW_DAYS` calendar days** of `signal_date` (new constant — policy
  decision to set with user, suggested 7 days).
- "Not acted on" = no SELL within that window (regardless of what eventually happened to the price).

This is a deliberate behavioral window, not a same-day match. The window length is an
investment-policy constant because it defines what "responded to the signal" means.

New constant: `EXIT_SIGNAL_ACT_WINDOW_DAYS` → `constants.py`.

### 2.2 Pattern definitions

Three patterns — same structure as v1 (pure functions in `behavioral_fingerprint.py`, no DB
calls, no Streamlit, sample-gated):

---

**Pattern 1 — `signal_response_rate_pattern(exit_signals_df, trades_df, act_window_days, min_n)`**

*Question: Do you respond to exit signals? Does severity matter?*

Groups captured signals by `signal_type` (WATCH / TRIM / EXIT / RISK_OFF). For each group with
≥ `min_n` signals, computes:
- `n_signals` — total signals of that type
- `n_acted` — how many had a matching SELL within `act_window_days`
- `action_rate` — n_acted / n_signals

Returns a dict keyed by signal_type, or `None` for any group below `min_n`.

Expected insight: a well-calibrated investor should act on EXIT at a higher rate than TRIM, and
TRIM higher than WATCH. If EXIT action rate ≈ WATCH action rate, that's a loss-aversion signal.

---

**Pattern 2 — `signal_lag_pattern(exit_signals_df, trades_df, act_window_days, min_n)`**

*Question: When you do act on exit signals, how long do you wait?*

Restricted to signals that WERE acted on (a matching SELL exists within `act_window_days`). For
each signal_type bucket with ≥ `min_n` acted signals:
- `median_lag_days` — median days from `signal_date` to the SELL `traded_at` date
- `pct_acted_day1` — % acted within 1 day (same-day or next-day response)

Returns per signal_type dict, or `None` below `min_n`.

Expected insight: an EXIT signal responded to 6 days later (after most of the move has happened)
is a very different behavioral signature than same-day response.

---

**Pattern 3 — `escalation_ignored_pattern(exit_signals_df, trades_df, act_window_days, min_n)`**

*Question: Do you hold through escalating signals without acting?*

Finds tickers where a WATCH signal was followed by a TRIM or EXIT signal on a later date, with
no SELL trade in between. For each such "escalation sequence":
- Flags the ticker + escalation (WATCH→TRIM, WATCH→EXIT, TRIM→EXIT)
- Counts how many distinct escalation events occurred

Aggregate output: `n_escalations`, `n_without_sell` (the ignored-escalation count),
`ignored_rate` = n_without_sell / n_escalations.

This is the most direct loss-aversion measurement the app can produce. Requires ≥ `min_n`
escalation events (not individual signals — scarcest bucket, expect to wait longest).

---

### 2.3 Display

New section on 🎯 My Edge → 🧬 Behavioral Fingerprint tab, below the existing three Buy-side
cards. Section header: **"Exit Signal Response"** with a subtext: "How you respond to the app's
exit signals — an observed pattern in your decisions, not a directive."

Same card layout and "insufficient data" suppression as Buy-side cards. Each card renders only
when its pattern function returns a non-None result.

No new constants for display copy thresholds (unlike v1's `BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP`
/ `_ALPHA_DELTA_PP`) — the exit-side patterns are primarily directional/factual (action rate %,
lag days, escalation count) rather than "is this delta meaningful?" comparisons. Add display-copy
constants if the build reveals a need, but don't pre-invent them.

### 2.4 Sample expectations

Given the current trade cadence (~70 BUY trades logged all-time), SELL trades are likely fewer.
Exit-signal capture starts Day 1 of Phase 1 deploy. At current app usage:
- WATCH/TRIM/EXIT signals fire daily for held positions; capturing 30 days = potentially 30+
  signals per tier depending on portfolio size and market conditions.
- RISK_OFF fires infrequently (requires fragility + regime trigger simultaneously).
- Pattern 3 (escalation sequences) will be the last to reach min_n=8 — it requires a WATCH
  followed by a TRIM or EXIT with no sell, which needs time.

**Do not build Phase 2 until Pattern 1 alone has enough data** (≥ 8 acted signals across at least
two signal_type buckets). That's the minimum signal that there's something to show.

---

## What is NOT in scope

- **Outcome comparison pattern** (was acting vs. ignoring better? — requires price history lookups
  at analysis time, more compute; deferred to a potential v3).
- **Modifying any exit signal or gate** — capture is purely additive; `exit_advisor.py`,
  `classify_deterioration_tier()`, `split_defensive()` are untouched.
- **Per-signal "did you act?" annotation on the Brief** — that's a UX enhancement that could be
  built on top of the capture table later, but is out of scope for this plan.
- **Cron/headless capture** — ~~the GitHub Actions cron (`headless_alert_engine.py`) also computes
  exit signals. Wiring capture there is a v2 enhancement; Phase 1 captures only from the
  interactive app session (Home build path).~~ **DONE 2026-07-21.** `compute_protective_alerts()`
  now additionally returns `all_deterioration_signals`/`risk_off_signals` (composite_score-enriched,
  all tiers — additive, doesn't touch the existing EXIT-only `alerts` used for the protective
  email); `cron_runner._run_premarket()` writes them via the same `save_exit_signals_batch()`.
  Signal history is no longer gapped on days the app isn't opened.
- **Changing `match_recs_to_trades()`** — exit-side matching lives in new helpers, not in the
  existing buy-side function.

---

## Build order

1. **Apply DDL** in Supabase SQL editor (`exit_signals` table + RLS policy).
2. **Phase 1:** `db.py` → `save_exit_signals_batch()` + `load_exit_signals()`, then `app.py`
   capture call (MISS path). `py_compile` clean. Deploy. No Opus review required (no
   constants/gates/scoring touched).
3. **Wait ≥ 30 days.** Check row counts in Supabase.
4. **Phase 2:** new constant `EXIT_SIGNAL_ACT_WINDOW_DAYS` in `constants.py` (policy conversation
   required first — see §2.1), three pattern functions in `behavioral_fingerprint.py`, display
   section in `app.py`. **Opus review required** for the new constant (constants.py touch per
   CLAUDE.md hard rule #4).

---

## Policy constant (confirmed)

**`EXIT_SIGNAL_ACT_WINDOW_DAYS = 7`** — confirmed with user 2026-07-18.

7 calendar days: a SELL trade on the same ticker within 7 days of `signal_date` counts as
"acted on." Covers a full trading week; long enough to catch a considered response, short enough
that an unrelated sell weeks later doesn't contaminate the signal. This is an investment-policy
constant and lives in `constants.py`.
