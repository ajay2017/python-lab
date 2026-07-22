# Analyst Research Accountability — Subscription ROI Tracker

**Feature scope:** Track whether pasted CNBC Pro analyst calls were directionally correct
and whether price targets were hit — making the value (or noise) of the paid subscription
measurable inside the app.

**Status:** PLANNING — 2026-07-22. Not yet built.

**Related:** [docs/plans/analyst-coverage.md](analyst-coverage.md) (Phase 1–3 already shipped),
`stock_analyzer/analyst_intel.py`, `stock_analyzer/db.py`, `app.py` (AI Insights page).

---

## Why this matters

Every article you paste feeds up to 55 pts of the Valuation pillar (PT Upside 25 pts +
Analyst Consensus 30 pts). But today there is **zero feedback loop**: you save a Buy call
at $50 PT, the score improves, the moment passes — and the app never tells you whether
that call was right. Over time this is two problems:

1. **Invisible subscription cost.** You can't answer "is CNBC Pro worth it?"
2. **Uncalibrated trust.** Without a firm-level track record, all analyst calls carry
   equal weight. A Goldman that's been right 70% of the time looks identical to a
   boutique that's been wrong 8 times in a row.

---

## Design principles (don't violate)

All principles from the existing `analyst-coverage.md` plan carry forward, plus one new one:

- **Accuracy is display-only — never tunes a gate.** Tracking that Goldman has an 80%
  directional accuracy does NOT change how much the Goldman consensus row contributes to
  the Valuation score. The engine stays rule-based. Accuracy data is retrospective
  awareness only.
- **Only evaluate calls past the measurement window.** A Buy call made 10 days ago is
  not a "miss" — it is "Pending." Never show a Pending call as wrong.
- **Sell date as natural exit.** If the ticker was sold after the article date, use the
  sell date as the measurement endpoint (not an arbitrary N-day window). This respects
  the actual decision window and avoids penalising a correct call on a stock you sold too
  early.

---

## What we already have (data audit)

| Existing asset | Notes |
|---|---|
| `analyst_coverage` table | `ticker`, `article_date`, `avg_pt`, `consensus_rating`, `analysts` JSONB (per-firm rating + PT), `created_at` |
| `db.save/load/delete_analyst_coverage` | Fully wired |
| `recommendations_history` table | `ticker`, `rec_date`, `composite_score`, `price_at_surface` — usable for engine-vs-analyst join |
| `trades` table | `ticker`, `traded_at`, `action='sell'` — provides natural sell-date exit point |
| yfinance `yf.download(ticker, start, end)` | Established pattern; used in `scanner.py`, `benchmark_mirror.py`, etc. |

**Critical gap:** `analyst_coverage` has **no anchor price** — there is no `price_at_article_date`
column. Without it we cannot compute return from article date or know where the PT stood
relative to market at the time of the call. This is the first thing to fix.

---

## Phase 1 — Data Foundation (DDL + backfill + auto-fill)

### 1a. New columns (additive DDL — backward-compatible)

```sql
-- Run once in Supabase SQL editor
ALTER TABLE analyst_coverage
    ADD COLUMN IF NOT EXISTS price_at_article_date NUMERIC,       -- close price on article_date (next trading day)
    ADD COLUMN IF NOT EXISTS composite_score_at_save NUMERIC;     -- engine composite score at save time (NULL if ticker not in portfolio)
```

No RLS change needed. Existing rows get NULL for both columns; backfill covers them.

### 1b. Backfill script — `scripts/backfill_analyst_prices.py`

Standalone script (not a Streamlit page). Reads all `analyst_coverage` rows where
`price_at_article_date IS NULL`, fetches the next trading-day close via yfinance, and
upserts the price back.

**Algorithm:**

```
for row in load_analyst_coverage(all rows, price_at_article_date IS NULL):
    hist = yf.download(row.ticker, start=row.article_date,
                       end=row.article_date + timedelta(days=7),
                       auto_adjust=True, progress=False)
    if hist is not None and not hist.empty:
        price = float(hist["Close"].iloc[0])
        supabase.update({"price_at_article_date": price}).eq("id", row.id).execute()
    else:
        log(f"WARN: no price found for {row.ticker} on {row.article_date} — skip")
```

`timedelta(days=7)` handles weekend/holiday article dates (e.g. a Saturday CNBC article
picks up Monday's open session close). `iloc[0]` = first available trading day on or
after `article_date`.

`composite_score_at_save` is **not backfillable** — historical composite scores are not
stored anywhere. Leave NULL for existing rows; only new saves will have it.

### 1c. Auto-fill at save time (`app.py` → `db.save_analyst_coverage`)

In the "Save to Inbox" flow (currently `app.py` lines 25230–25242), enrich the record
dict **before** calling `db.save_analyst_coverage`:

```python
# price_at_article_date: use last_price from the currently-loaded port_df or financials
# (already fetched — zero extra API cost for held tickers)
record["price_at_article_date"] = _resolve_price_at_save(ticker, financials)

# composite_score_at_save: read from port_df if ticker is currently held
record["composite_score_at_save"] = _resolve_score_at_save(ticker)
```

Helper `_resolve_price_at_save(ticker, financials)`:
- Check `st.session_state._port_df_enriched` for the ticker → `row["Price"]`
- Fallback: `financials.get("current_price")` (already in the bundle)
- Fallback: `None` (silent — backfill script will catch it)

Helper `_resolve_score_at_save(ticker)`:
- Check `st.session_state._port_df_enriched` for the ticker → `row["Score"]`
- Return `None` for watchlist/unknown tickers (not held, no live score available)

**No schema API change to `db.save_analyst_coverage`** — the function already passes
`record: dict` through to Supabase insert; the two new keys are picked up automatically
because `_ANALYST_COVERAGE_COLS` will be updated to include them.

### 1d. `db.py` changes (minimal)

- Add `"price_at_article_date"` and `"composite_score_at_save"` to `_ANALYST_COVERAGE_COLS`
  (the backfill list used in `load_analyst_coverage` for backward-compat None-filling).
- No change to `save_analyst_coverage` or `load_analyst_coverage` signatures.

### 1e. New constants

Add to `stock_analyzer/constants.py` in the existing `# ── Analyst coverage` block:

```python
ANALYST_ACCURACY_DIRECTION_DAYS = 30   # days after article_date to measure Buy/Sell directional accuracy
ANALYST_ACCURACY_PT_HIT_PCT     = 0.75 # price must reach ≥75% of avg_pt (INTRA-WINDOW HIGH, not endpoint close) to count as a PT "hit"
```

**Rationale for 30 days:** Confirmed by user (2026-07-23) — shorter, more relevant window
than the initially-proposed 60 days. Not a gate — purely a display window.

**Rationale for 75% PT hit, measured as intra-window HIGH (not endpoint close):**
User's initial preference was 75% on an endpoint-close basis, which Claude pushed back
on — a 30-day close only needs a modest bounce to cross 75% of target, which would
inflate the hit-rate metric and overstate subscription value. **Resolution (user
confirmed):** keep 75%, but check whether price ever reached ≥75% of `avg_pt` at any
point during the window (the window's high, not just the closing price on day 30 or the
sell-date close). This is more forgiving of the short window without loosening what
"hit" means — a genuine touch of 75% of target is a real event, whereas a lucky
endpoint price is noise-sensitive.

**Implication for computation:** `pt_hit` and `directional_hit`/`ret_pct` now use
*different* price series for the same row:
- `directional_hit` / `ret_pct` — endpoint price only (window-end close or sell-date
  close), because directional accuracy is about where the call ended up, not a fleeting
  touch.
- `pt_hit` — **max High** across the full OHLC window from `article_date` to the
  earlier of (sell date, `article_date + ANALYST_ACCURACY_DIRECTION_DAYS`).

---

## Phase 2 — Research Scorecard (UI, display-only)

### Placement

New **"📊 Research Scorecard"** section on 🧠 AI Insights, immediately below the existing
"Ideas Inbox" section. Same page, no new nav item.

Renders only when there is at least one row with `price_at_article_date IS NOT NULL`.
Otherwise: `st.info("No evaluable calls yet — prices will populate automatically when
you save new research or after the backfill script runs.")`.

### Accuracy computation (pure Python, no LLM, no DB writes)

Computed at render time from `load_analyst_coverage()` output:

```python
def _classify_call(row, trades_df, today_et) -> dict:
    """Returns accuracy classification for one analyst_coverage row."""
    if row.price_at_article_date is None:
        return {"status": "no_anchor"}

    # Determine measurement endpoint (used for directional accuracy / return %)
    sell_after = trades_df[
        (trades_df.ticker == row.ticker) &
        (trades_df.action == "sell") &
        (trades_df.traded_at.dt.date > row.article_date)
    ]
    if not sell_after.empty:
        window_end = sell_after.traded_at.dt.date.min()
        exit_price = _fetch_close_at_date(row.ticker, window_end)
        window = "sold"
    elif (today_et - row.article_date).days < ANALYST_ACCURACY_DIRECTION_DAYS:
        return {"status": "pending"}
    else:
        window_end = row.article_date + timedelta(days=ANALYST_ACCURACY_DIRECTION_DAYS)
        exit_price = _fetch_close_at_date(row.ticker, min(window_end, today_et))
        window = f"{ANALYST_ACCURACY_DIRECTION_DAYS}d"

    if exit_price is None:
        return {"status": "no_price"}

    ret_pct = (exit_price - row.price_at_article_date) / row.price_at_article_date * 100
    is_bullish = row.consensus_rating and any(
        w in row.consensus_rating.lower() for w in ("strong buy", "buy")
    )
    directional_hit = (is_bullish and ret_pct > 0) or (not is_bullish and ret_pct < 0)

    # PT hit uses the window's INTRA-PERIOD HIGH, not the endpoint price above —
    # a genuine 75%-of-target touch counts even if price pulled back by window_end.
    window_high = _fetch_max_high_in_window(row.ticker, row.article_date, window_end)
    pt_hit = bool(row.avg_pt and window_high and window_high >= row.avg_pt * ANALYST_ACCURACY_PT_HIT_PCT)
    pt_proximity = (window_high / row.avg_pt * 100) if (row.avg_pt and window_high) else None

    return {
        "status":          "hit" if directional_hit else "miss",
        "ret_pct":         ret_pct,
        "exit_price":      exit_price,
        "window":          window,
        "directional_hit": directional_hit,
        "pt_hit":          pt_hit,
        "pt_proximity":    pt_proximity,   # based on window HIGH, not endpoint — may exceed 100%
    }
```

`_fetch_max_high_in_window(ticker, start, end)` — one `yf.download(ticker, start=start,
end=end+1d, auto_adjust=True)` call, returns `df["High"].max()`. Cached alongside
`_fetch_close_at_date` (same underlying OHLC fetch can serve both — fetch once per
(ticker, start, end) tuple and derive close-at-end + max-high from the same frame,
rather than issuing two separate yfinance calls).

Price fetches are **cached** (`@st.cache_data(ttl=3600)`) — one fetch per (ticker, date)
pair, shared across all rows for the same ticker.

### Display blocks

**Block A — Summary KPI row (3 metrics)**

```
┌──────────────────────┬──────────────────────┬──────────────────────┐
│  Directional Acc.    │   PT Hit Rate        │   Evaluable Calls    │
│  68%  (13/19)        │   47%  (9/19)        │   19 of 24 total     │
│  ▲ Buy calls correct │  ≥95% of avg PT hit  │  5 pending (< 60d)   │
└──────────────────────┴──────────────────────┴──────────────────────┘
```

Only evaluable calls (status ∈ {hit, miss}) are in the denominator. Pending and
no-anchor rows are excluded with a caption explaining the exclusion count.

**Block B — Per-call accuracy table**

Columns: Ticker | Article Date | Consensus | Avg PT | Price @ Article | Exit Price |
Return % | PT Proximity % | Status | Window

Color coding: green row = directional hit, red = miss, grey = pending/no-anchor.
Sortable. Default sort: article_date DESC.

**Block C — Firm Leaderboard**

Aggregates across all rows in the `analysts` JSONB (not just the consensus —
the per-firm rating column). For each unique firm:

| Firm | Calls | Dir. Accuracy | PT Hit Rate | Avg Return % |
|---|---|---|---|---|
| Goldman Sachs | 8 | 75% | 50% | +12.3% |
| Baird | 5 | 80% | 60% | +18.1% |
| … | | | | |

Minimum 2 calls to appear in the leaderboard (suppress single-call noise).

This is the highest-value output — it tells you whose calls to weight most.

**Block D — "Best calls / Worst calls" highlights** (optional, show only when ≥5 evaluable)

Top 3 hits (highest return %) and bottom 3 misses (most negative return %), as cards.

---

## Phase 3 — Engine vs Analyst Calibration (deferred)

**Trigger to build:** when `composite_score_at_save IS NOT NULL` on ≥20 rows
(approximately 4–6 weeks of new saves after Phase 1 ships).

**The question:** When analyst consensus was Bullish and the engine score was below
`COMPOSITE_BUY = 65` (skeptic disagreement), who was right 60 days later?

**Display:** a 2×2 disagrement matrix:

```
              Engine ≥ 65      Engine < 65
Analyst Buy     ✅ Agree         ⚡ Disagreement
Analyst Sell    ⚡ Disagreement  ✅ Agree
```

In the disagreement quadrants, show: how many cases, average subsequent return,
how many the engine was right vs how many analyst was right.

This is the calibration signal for deciding how much to manually weight strong analyst
consensus when the engine is cautious. It does NOT change the engine's behavior —
awareness only, per the locked invariant.

**Requires:** `composite_score_at_save` data from Phase 1 (not backfillable).
**Deferred until:** enough data has accumulated. Do not build yet.

---

## Files to touch

| File | Change |
|---|---|
| `stock_analyzer/constants.py` | Add `ANALYST_ACCURACY_DIRECTION_DAYS`, `ANALYST_ACCURACY_PT_HIT_PCT` |
| `stock_analyzer/db.py` | Add 2 cols to `_ANALYST_COVERAGE_COLS` |
| `app.py` | (a) Enrich record at save time with price + score; (b) new Scorecard section |
| `scripts/backfill_analyst_prices.py` | **New file** — one-time backfill, run manually |
| `docs/architecture.md` | Updated DDL block (§6.15) + constants table (2 new rows) |
| `docs/requirements.md` | New F-row for Scorecard surface |

### Files NOT touched
- `stock_analyzer/analyst_intel.py` — extraction logic unchanged
- `stock_analyzer/valuation.py` — scoring unchanged (accuracy is awareness-only)
- `stock_analyzer/bundle_loader.py` — unchanged
- Any gate, composite score, or recommendation logic

---

## New constants summary

| Constant | Value | Rationale |
|---|---|---|
| `ANALYST_ACCURACY_DIRECTION_DAYS` | `30` | Measurement window for Buy/Sell directional accuracy |
| `ANALYST_ACCURACY_PT_HIT_PCT` | `0.75` | Fraction of avg_pt the window's intra-period HIGH must reach to count as "hit" (not the endpoint close — see Phase 1e) |

Both are display-only classification thresholds — not gates, not score inputs.
Any change to these values changes what shows as "Hit" vs "Miss" in the Scorecard,
which is a **display policy decision** (discuss with user before changing).

---

## Routing

- 🔴 **reviewer (Opus)** — pre-commit review required: touches `constants.py` (new
  threshold constants) + a new DB column + scoring-adjacent display logic. Verify:
  accuracy classification never leaks into scoring, new constants are display-only,
  backfill script is idempotent, no gate drift.
- 🔵 **implementer (Sonnet)** — build Phase 1 (DDL + backfill script + auto-fill +
  constants + db.py update) and Phase 2 (Scorecard UI) from this spec.
- 🟢 **doc-writer (Haiku)** — architecture DDL update (§6.15) + constants table rows +
  requirements F-row, after facts are pinned by Opus review.

---

## Decisions (confirmed 2026-07-23 — no longer open)

1. **`ANALYST_ACCURACY_DIRECTION_DAYS = 30`.** Confirmed.
2. **`ANALYST_ACCURACY_PT_HIT_PCT = 0.75`, measured against the window's intra-period
   HIGH, not the endpoint close.** User's initial ask was 75% on an endpoint-close basis;
   Claude pushed back (a 30-day close only needs a modest bounce to cross 75% of target,
   inflating the hit rate). Resolved: keep 75%, but check the window's max High instead
   of the day-30/sell-date close — genuine partial-target touches count, lucky endpoint
   noise doesn't. See Phase 1e / Phase 2 algorithm above.
3. **Firm leaderboard minimum call count = 2.** Confirmed.
4. **Backfill execution path = Supabase.** Confirmed — run `scripts/backfill_analyst_prices.py`
   from the Streamlit Cloud terminal (Manage app → Terminal) against the same Supabase
   instance the app uses; not a local run (per the never-run-locally rule, this script
   only touches Supabase, not the app itself, but is executed from the Cloud terminal for
   secrets-consistency).
5. **Fetched prices are written back to the DB** (`price_at_article_date` at backfill/save
   time; the Scorecard reads stored prices, not live yfinance calls, except for the
   per-row `exit_price`/`window_high` at render time which are cached
   `@st.cache_data(ttl=3600)` and not persisted — only the anchor price is persisted).
   Confirmed.

---

## Build order

1. Run DDL in Supabase (additive `ALTER TABLE` — safe to run during normal operation).
2. Build `scripts/backfill_analyst_prices.py` → run it from Streamlit Cloud terminal →
   verify `price_at_article_date` populated on existing rows.
3. Add the 2 constants to `constants.py`; add the 2 new columns to `_ANALYST_COVERAGE_COLS`
   in `db.py`.
4. Wire `_resolve_price_at_save` / `_resolve_score_at_save` into the "Save to Inbox" flow
   in `app.py`.
5. Build the Scorecard UI section in `app.py` (KPI row, per-call table, firm leaderboard,
   best/worst highlights) using the two-price-series classification algorithm above.
6. 🔴 Opus review (new constants + new DB columns + scoring-adjacent display logic —
   verify accuracy classification never leaks into `valuation_score`/gates).
7. Commit + push → auto-deploy; hard-refresh to verify.
8. 🟢 Doc-writer: architecture DDL update (§6.15) + constants table rows (2) +
   requirements F-row, after facts are pinned.
