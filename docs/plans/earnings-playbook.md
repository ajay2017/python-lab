# Earnings Playbook — Plan

**Feature:** Close the earnings-event lifecycle for held positions and watchlist candidates:
(1) enrich the existing pre-earnings playbook with curated beat-rate and reaction-history data
pasted from CNBC Pro articles; (2) after the print, paste results to trigger an F-1 thesis
checkpoint; (3) scanner for watchlist candidates (parked — ship after Phase 1+2 prove value).

**Status: FULLY SHIPPED 2026-07-13 — all 3 phases.**
- Phase 1 (CNBC extractor + playbook enrichment): SHIPPED commits `7d09857`→`cf6cc19`. `earnings_intel.py`, db functions, 3 new constants, enriched `build_earnings_playbook()`, paste UI on Ideas Inbox, Catalyst Watch CNBC badge/expander. DDL for `earnings_context` applied.
- Phase 2 (post-earnings Finnhub fetch + F-1 thesis checkpoint): SHIPPED same session. `fetch_recent_results()` (Finnhub-native, no LLM), `save_earnings_results()`, thesis checkpoint CTA on AI Insights. DDL for `earnings_results` applied.
- Phase 3 (Catalyst Scanner): SHIPPED 2026-07-13 (commit `0cac9ee`). `earnings_advisor.build_earnings_catalyst_candidates()`, 🎯 Entry Candidates tab on 🔔 Catalyst Watch (reqs F-37b; docs backfilled 2026-07-16).

Verified 2026-07-13 against current code — every constant, function signature, table schema, and existing-pattern claim checked out except two corrected above (playbook location, `COMPOSITE_BUY` naming).

---

## What already exists — do not rebuild

`stock_analyzer/earnings_advisor.py` is **already implemented and wired** into 🔔 Catalyst Watch
("📊 Your Holdings — Earnings" → "📋 Pre-Earnings Playbook"). It computes EXIT / REDUCE / MONITOR / HOLD / HOLD_OR_ADD for every
held position with earnings in the next 30 days, using:

- Composite score and Signal from `port_df`
- Analyst revision momentum (`net`, `upgrades_90d`, `downgrades_90d`) from `held_data[ticker]["revisions"]`
- Gap to stop and position weight from `port_df`
- Estimated earnings-day move: portfolio VaR × 3, falling back to `_SECTOR_DEFAULTS` per sector
- Sector-specific "what to watch" (`_SECTOR_WATCH` / `_DEFAULT_WATCH`)

The playbook already renders with a KPI strip (count / IMMINENT count / EXIT signals / REDUCE
signals) and per-position expandable cards. **Phase 1 enriches this output — it does not replace it.**

Existing earnings-window constants in `constants.py` (do not change these values):

| Constant | Value |
|---|---|
| `EARNINGS_IMMINENT_DAYS` | 7 |
| `EARNINGS_URGENCY_SOON_DAYS` | 14 |
| `EARNINGS_MANAGEABLE_DAYS` | 21 |
| `CATALYST_WATCH_WINDOW_DAYS` | 7 |
| `EARNINGS_OVERWEIGHT_TRIM_PCT` | 12.0 |
| `EARNINGS_OVERWEIGHT_TRIM_TO_PCT` | 10.0 |

---

## Design invariants

1. **The engine still decides.** Beat rate and reaction history are enrichment signals that can
   *strengthen* an existing REDUCE or HOLD_OR_ADD verdict — they cannot originate a new verdict
   on their own. If no CNBC context exists for a ticker, `build_earnings_playbook()` behaves
   exactly as today (graceful degradation).

2. **Strictly additive / zero runtime dependency.** If `earnings_intel.py` is offline, the API
   key is absent, or either DB table doesn't exist yet, every other page is unaffected. Ships
   inert until DDL is applied and an article is pasted. DDL applied 2026-07-13 — active.

3. **Zero-hallucination on a decision surface.** The LLM extracts only stated atomic facts (beat
   rate %, reaction pattern, consensus growth). All aggregates and posture decisions are computed
   in Python. The editable preview before save is the second guard.

4. **Thesis checkpoint is suggestion-only.** `generate_earnings_thesis_update()` returns a
   suggested status with a rationale; the user confirms or dismisses. It never auto-saves a
   `thesis_reviews` row.

5. **Awareness-only surfaces never gate.** `earnings_context` and `earnings_results` data annotate
   and enrich — they do not suppress, gate, or override any recommendation.

6. **All thresholds in `constants.py`.** New constants introduced in Phase 1 are investment-policy
   values; discuss with the user before changing them.

---

## Phase 1 — CNBC Paste Extractor + Playbook Enrichment

### New module — `stock_analyzer/earnings_intel.py`

Mirrors the AI-layer idiom from `analyst_intel.py` and `thesis_advisor.py`.

#### `extract_playbook(raw_text, article_date, api_key, model, max_tokens) -> list[dict] | None`

- Guard: `if not api_key or not raw_text.strip(): return None`
- `import anthropic` → `client.messages.create(...)` → parse strict JSON
- `except Exception: return None` — covers offline, API error, JSON parse failure
- `article_date: date` is passed as context so the LLM can resolve "Tuesday" → an absolute date
- Returns one dict per ticker found in the article; a 7-ticker weekly preview yields 7 records

**LLM extraction schema (per ticker):**

```json
{
  "ticker": "JPM",
  "company": "JPMorgan Chase",
  "earnings_date": "2026-07-15",
  "earnings_time": "pre_market",
  "beat_rate_pct": 81.0,
  "recent_reaction_summary": "fell after last four earnings releases",
  "recent_reaction_direction": "bearish",
  "consensus_growth_pct": 10.0,
  "what_to_watch_cnbc": "NII trajectory; management commentary on regulatory capital requirements"
}
```

- `earnings_time` enum: `pre_market` / `post_market` / `intraday` / `unknown`
- `recent_reaction_direction` enum: `bullish` / `bearish` / `mixed` / `unknown`
- `beat_rate_pct`: float extracted from "tops estimates 87% of the time"; `null` if not stated
- `consensus_growth_pct`: float from "earnings expected to grow ~10% YoY"; `null` if not stated
- `what_to_watch_cnbc`: the article's curated narrative (1-2 sentences); `null` if absent
- Analyst records extracted from "What to watch" quotes → passed to `analyst_intel.derive_consensus()`
  and dual-written to the existing `analyst_coverage` table (same flow as today — no new path)

#### `_playbook_system_prompt() -> str`

Built as a function (not a constant) so it can reference `COMPOSITE_BUY` from
`constants.py` at call time. Instructs the LLM:
- Extract ONLY facts stated in the article; `null` for anything not stated
- Resolve day-of-week references ("Tuesday", "Wednesday") to absolute dates using `article_date`
- `beat_rate_pct` comes only from Bespoke-style phrasing ("tops estimates X% of the time") — never
  invent from qualitative language
- Do NOT extract analyst price targets here; those are handled by `analyst_intel.extract_report()`

### New Supabase table — `earnings_context`

DDL applied 2026-07-13 — active. RLS on, `FOR ALL TO service_role`.

```sql
create table if not exists earnings_context (
    id                         bigint primary key generated always as identity,
    ticker                     text not null,
    company                    text,
    earnings_date              date,
    earnings_time              text,
    beat_rate_pct              numeric,
    recent_reaction_summary    text,
    recent_reaction_direction  text,
    consensus_growth_pct       numeric,
    what_to_watch_cnbc         text,
    article_date               date not null,
    article_source             text default 'cnbc_pro',
    created_at                 timestamptz default now()
);
create index if not exists earnings_context_ticker_idx on earnings_context (ticker);
create index if not exists earnings_context_article_date_idx on earnings_context (article_date desc);
```

### New `db.py` functions

- `save_earnings_context(records: list[dict]) -> None` — bulk upsert on `(ticker, article_date)`
- `load_earnings_context(ticker: str, max_age_days: int = 30) -> dict | None` — returns the most
  recent row for the ticker within the age window; `.limit(1)` pattern per `feedback_supabase_single_row`
- `load_earnings_context_batch(tickers: list[str], max_age_days: int = 30) -> dict[str, dict]` —
  fetches all matching rows in one query; returns `{ticker: row}`

### Paste UI — extend existing Ideas Inbox tab on 🧠 AI Insights

The "Ideas Inbox" tab on 🧠 AI Insights today has one mode (stock research). Add a **mode toggle**
at the top of the tab:

```
[ 📰 Stock Research ]  [ 📅 Earnings Playbook ]
```

**📅 Earnings Playbook mode:**

1. Article date picker — `st.date_input("Article date", value=today)` — needed for day-of-week inference
2. Paste area — same `st.text_area` pattern as stock research
3. Extract button → calls `extract_playbook(text, article_date, api_key)`
4. Editable N-card preview — one card per extracted ticker (same expander-card pattern as Analyst
   Coverage preview); each card shows: ticker, earnings_date, beat_rate, reaction_direction,
   consensus_growth, what_to_watch_cnbc; user can edit or remove cards
5. Save button → `db.save_earnings_context(confirmed_records)` + dual-write analyst rows via
   `analyst_intel.derive_consensus()` → `db.save_analyst_coverage()`

**Dual-write detail:** The extraction schema above does NOT include analyst fields (those come
from the "What to watch" analyst quotes). The implementation calls `extract_report()` on the same
raw text to get the analyst layer, then `extract_playbook()` for the earnings layer. Both run in
parallel (two API calls); results merge in the preview step. This keeps the two extractors
cleanly separated.

### Enrich `build_earnings_playbook()` in `earnings_advisor.py`

Add optional parameter:

```python
def build_earnings_playbook(
    port_df,
    held_data: dict,
    today: date | None = None,
    lookahead_days: int = 30,
    earnings_context: dict[str, dict] | None = None,   # NEW — {ticker: context_row}
) -> list[dict]:
```

Per-ticker, before calling `_recommend()`, look up context:

```python
ctx = (earnings_context or {}).get(ticker) or {}
beat_rate = ctx.get("beat_rate_pct")          # float | None
reaction   = ctx.get("recent_reaction_direction")  # str | None
```

Pass these to `_recommend()` as two new optional arguments. If both are `None`, `_recommend()`
behaves exactly as today.

**New logic in `_recommend()` (added BEFORE the existing HOLD_OR_ADD and HOLD cases):**

| Condition | Effect |
|---|---|
| `beat_rate < EARNINGS_BEAT_RATE_REDUCE_THRESHOLD` AND `score < COMPOSITE_BUY` | → REDUCE (MEDIUM priority); detail cites beat rate + composite weakness |
| `reaction == 'bearish'` AND `score < EARNINGS_BEARISH_REACTION_COMPOSITE_GATE` | → REDUCE (MEDIUM priority); detail cites reaction history |
| `beat_rate >= EARNINGS_BEAT_RATE_STRONG_THRESHOLD` AND `reaction == 'bullish'` AND `score >= 68` AND `net_rev >= 2` | Existing HOLD_OR_ADD case: detail text gains beat rate + reaction basis |

These conditions are checked **after** the existing EXIT and REDUCE (oversized/weak-fundamentals/
negative-revision) cases — CNBC data cannot override an already-determined EXIT verdict.

**Playbook card render additions** (in `app.py` existing playbook render block):

- If `earnings_context` row exists for this ticker: show a `📰 CNBC` badge in the expander header
- In the Analyst Expectations column: add beat_rate line ("Historical beat rate: 87%") and
  reaction pattern line ("Post-earnings reaction: fell after last 4 releases") when available
- Append `what_to_watch_cnbc` (if present) as the first bullet in the "What to watch" list,
  above the existing sector-generic bullets

**Caller change in `app.py`** (in the `_render_holdings_earnings()` block):

```python
_earn_ctx = db.load_earnings_context_batch(list(held_tickers), max_age_days=30)
_playbook = build_earnings_playbook(port_df, held_data, earnings_context=_earn_ctx)
```

No TTL caching needed — `load_earnings_context_batch` is a cheap single-table read.

### New constants in `constants.py`

```python
# Earnings Playbook — beat-rate and reaction-posture thresholds
EARNINGS_BEAT_RATE_REDUCE_THRESHOLD    = 60.0   # below this + weak composite → REDUCE pressure
EARNINGS_BEAT_RATE_STRONG_THRESHOLD    = 75.0   # above this + bullish reaction → strengthens HOLD_OR_ADD
EARNINGS_BEARISH_REACTION_COMPOSITE_GATE = 75   # bearish reaction history + composite < this → REDUCE
```

---

## Phase 2 — Post-Earnings Paste → F-1 Thesis Checkpoint

### Second extraction function in `earnings_intel.py`

#### `extract_results(raw_text, article_date, api_key, model, max_tokens) -> list[dict] | None`

Same guard + try/except pattern as `extract_playbook()`.

**LLM extraction schema (per ticker):**

```json
{
  "ticker": "JPM",
  "company": "JPMorgan Chase",
  "report_date": "2026-07-15",
  "actual_eps": 4.96,
  "estimated_eps": 4.61,
  "eps_beat": true,
  "eps_surprise_pct": 7.6,
  "actual_revenue": 45.3,
  "estimated_revenue": 44.1,
  "rev_beat": true,
  "guidance_direction": "maintained",
  "key_narrative": "Record trading revenue offset by higher credit reserves; management maintained 2026 NII guidance despite rate uncertainty."
}
```

- Revenue figures in billions (model normalises; Python never does arithmetic on LLM output)
- `guidance_direction` enum: `raised` / `lowered` / `maintained` / `withdrawn` / `unknown`
- `key_narrative`: 1-2 sentence management commentary summary; `null` if not available
- All numeric fields: `null` if not stated in the article — never fabricated

### New Supabase table — `earnings_results`

```sql
create table if not exists earnings_results (
    id                  bigint primary key generated always as identity,
    ticker              text not null,
    report_date         date not null,
    actual_eps          numeric,
    estimated_eps       numeric,
    eps_beat            boolean,
    eps_surprise_pct    numeric,
    actual_revenue      numeric,
    estimated_revenue   numeric,
    rev_beat            boolean,
    guidance_direction  text,
    key_narrative       text,
    article_source      text default 'cnbc_pro',
    created_at          timestamptz default now()
);
create unique index if not exists earnings_results_ticker_date_idx on earnings_results (ticker, report_date);
```

### New `db.py` functions

- `save_earnings_results(records: list[dict]) -> None` — upsert on `(ticker, report_date)`
- `load_earnings_result(ticker: str, lookback_days: int = 90) -> dict | None` — most recent result
  row within window; `.limit(1)` pattern

### Thesis checkpoint in `thesis_advisor.py`

#### `generate_earnings_thesis_update(ticker, user_thesis, earnings_result, api_key) -> dict | None`

```python
# earnings_result: the row dict from earnings_results
# user_thesis: trades.user_thesis text for the ticker

# Returns:
{
  "suggested_status": "WEAKENING",   # INTACT | WEAKENING | BROKEN
  "rationale": "Revenue beat but guidance was lowered — the thesis depended on accelerating NII growth, which management now signals is plateauing.",
  "earnings_signal": "mixed"         # beat | miss | mixed
}
```

- Prompt interpolates `user_thesis`, `earnings_result` fields (eps_beat, rev_beat, guidance_direction,
  key_narrative), and the INTACT/WEAKENING/BROKEN definitions from the existing thesis review prompts
- Returns `None` on failure — caller degrades gracefully (no checkpoint CTA shown)
- Does NOT write to `thesis_reviews` — that write happens only when the user explicitly confirms

### Paste UI — mode toggle on same tab

Extend the Phase 1 mode toggle to three modes:

```
[ 📰 Stock Research ]  [ 📅 Pre-Earnings ]  [ 📥 Post-Earnings Results ]
```

**📥 Post-Earnings Results mode:**

1. Same article date picker and paste area
2. Extract button → calls `extract_results(text, article_date, api_key)`
3. Editable N-card preview: ticker, report_date, eps_beat, rev_beat, guidance_direction, key_narrative
4. Save button → `db.save_earnings_results(confirmed_records)`

### F-1 thesis checkpoint — render on 🧠 AI Insights → Positions tab

After `db.save_earnings_results()`, held positions with a matching `earnings_results` row (report_date
within 14 days of today) surface a **"Earnings checkpoint"** CTA in the Positions tab card:

```
📥 Q2 results posted (Jul 15) — does this change your thesis?
[ Review thesis checkpoint ]
```

Clicking loads `generate_earnings_thesis_update()` and shows:

```
Suggested status: WEAKENING
Rationale: Revenue beat but guidance was lowered — thesis depended on accelerating NII…

[ Confirm — update thesis to WEAKENING ]   [ Dismiss ]
```

Confirm → writes a new `thesis_reviews` row with `status = "WEAKENING"`, `summary = rationale`,
and a `source = "earnings_checkpoint"` annotation (extend the reviews table with this optional text
column or embed in `summary`).

**Surface: AI Insights only.** No Home badge. The action consequence (WEAKENING/BROKEN F-1 status)
reaches Home naturally via the existing Brief pathway once the review row is written.

---

## Phase 3 — Catalyst Scanner (SHIPPED 2026-07-13, commit `0cac9ee`)

Shipped earlier than originally planned — the gap was observed before a full earnings season.

**Shipped scope:**

- `build_earnings_catalyst_candidates()` in `earnings_advisor.py`
- Universe: **watchlist only** (composite scores already loaded; no new bundle loads triggered)
- Filter: not held + `earnings_context` row exists + `beat_rate_pct ≥ EARNINGS_MIN_BEAT_RATE_ENTRY`
  (70.0) + composite ≥ `COMPOSITE_BUY` + `recent_reaction_direction ≠ 'bearish'`
- Ranking: `beat_rate × composite × reaction_multiplier` (1.2 bullish / 1.0 mixed)
- Render: 🎯 Entry Candidates tab on 🔔 Catalyst Watch; awareness only, never a Buy recommendation; "Analyse →" button routes to Analysis page
- `EARNINGS_MIN_BEAT_RATE_ENTRY = 70.0` added to `constants.py`
- Requires CNBC earnings context pasted via Ideas Inbox → Pre-Earnings to populate
- Reqs F-37b; docs backfilled 2026-07-16

---

## DDL work (one-time, applied manually in Supabase)

Both tables applied 2026-07-13 — active.

| Table | Phase | Status |
|---|---|---|
| `earnings_context` | Phase 1 | Applied 2026-07-13 |
| `earnings_results` | Phase 2 | Applied 2026-07-13 |

---

## UI placement — confirmed decisions

### Where articles are pasted
🧠 AI Insights → "Ideas Inbox" section → mode toggle at the top of the tab:
`[ 📰 Stock Research ]  [ 📅 Pre-Earnings ]  [ 📥 Post-Earnings Results ]`
No new page. Same paste → preview → save flow as today.

### Where enriched output appears
- **Pre-earnings playbook cards:** 🔔 Catalyst Watch → "📊 Your Holdings — Earnings" →
  "📋 Pre-Earnings Playbook" (already exists; Phase 1 enriches the existing cards with beat
  rate, reaction pattern, and CNBC "what to watch" context).
- **Post-earnings thesis checkpoint:** 🧠 AI Insights → Positions tab → per-position card.
- **Phase 3 entry candidates (parked):** 🔔 Catalyst Watch → "🔭 On Your Radar" section.

### Catalyst Watch nav badge (decided: Option 3)
When `build_earnings_playbook()` produces any EXIT or REDUCE signals, add a count badge to the
"🔔 Catalyst Watch" nav button — the same pattern used for the existing risk-alerts badge.

**Implementation:** After `build_earnings_playbook()` runs (inside the Home synthesis or the
Catalyst Watch render path), count `_pb_exit + _pb_reduce` and write the total to a new session
key `_earnings_posture_alerts_cache`. The nav sidebar reads this key alongside the existing
`_risk_high_alerts_cache` to compose the badge label, e.g. `(2 risk · 1 earnings)` or a combined
count — exact format is a render-time decision.

This keeps Home uncluttered while ensuring the user is nudged toward Catalyst Watch when earnings
action is pending. EXIT/REDUCE earnings signals do NOT surface on Home Act Today cards.

---

## Session state — one new key

| Key | Written by | Read by | Purpose |
|---|---|---|---|
| `_earnings_posture_alerts_cache` | Home synthesis (after playbook build) or CW render | Nav sidebar badge | Count of EXIT+REDUCE signals for the CW badge |

---

## Build sequence (COMPLETE — all steps shipped 2026-07-13)

1. ✅ `constants.py` — add the three Phase 1 constants
2. ✅ `stock_analyzer/earnings_intel.py` — `extract_playbook()` + `_playbook_system_prompt()`
3. ✅ `db.py` — `save_earnings_context()` + `load_earnings_context_batch()`
4. ✅ Supabase DDL — `earnings_context` table (applied 2026-07-13)
5. ✅ `earnings_advisor.py` — enrich `build_earnings_playbook()` with `earnings_context` param
6. ✅ `app.py` — mode toggle UI + Phase 1 paste flow + `load_earnings_context_batch` caller
7. *(Phase 2)*
8. ✅ `earnings_intel.py` — `fetch_recent_results()` (Finnhub-native; `extract_results()` also added but not wired to UI — replaced by Finnhub fetch)
9. ✅ `db.py` — `save_earnings_results()` + `load_earnings_result()`
10. ✅ Supabase DDL — `earnings_results` table (applied 2026-07-13)
11. ✅ `thesis_advisor.py` — `generate_earnings_thesis_update()`
12. ✅ `app.py` — Post-Earnings mode on paste tab + thesis checkpoint CTA on AI Insights
13. ✅ Phase 3 — `earnings_advisor.build_earnings_catalyst_candidates()` + 🎯 Entry Candidates tab on Catalyst Watch (commit `0cac9ee`)
