# Analyst Coverage / Ideas Inbox — Plan

**Feature:** Capture professional analyst research (CNBC Pro, JPMorgan/Goldman/BofA/Morgan Stanley notes) into structured, queryable intelligence that (a) surfaces **new tickers** as watchlist candidates and (b) **enriches** tickers already in the held/watchlist universe.

**Status:** Phase 1 (Foundation) — spec locked, pre-build.

---

## Design principles (invariants)

1. **The engine still decides.** Analyst PTs and ratings are *awareness context only* — they never modify a composite score, never gate, never override a verdict. The premium value is the **"Wall Street vs. Your Engine"** tension: seeing that Goldman rates INIO Buy ($42 PT) while the engine scores it below `COMPOSITE_BUY = 65` is *more* useful than either signal alone.
2. **Strictly additive / zero runtime dependency.** If the LLM is offline or the `analyst_coverage` table doesn't exist yet, every other page is unaffected; the feature degrades to an offline notice / empty inbox. Same contract as the rest of the AI layer.
3. **Zero-hallucination on a decision surface.** A wrong price target erodes trust in the app. Two defenses: (a) the LLM extracts **only atomic per-firm facts** — the app computes all aggregates (avg/high/low PT, consensus) in Python, so no arithmetic hallucination; (b) the extraction is shown in an **editable preview** before save, and the **raw pasted text is stored** for re-processing.
4. **Ideas funnel INTO the engine, not around it.** A new ticker in the inbox gets a **"▶ Analyze"** button that routes to the existing 📈 Analysis page (existing 5-gate scoring) and an **"➕ Add to Watchlist"** button. No automatic buys, no automatic watchlist adds.

---

## Phase 1 scope

### New module — `stock_analyzer/analyst_intel.py`

Pure logic + the one LLM call. Mirrors the existing AI-layer idiom exactly (see `thesis_advisor.py`).

- `extract_report(raw_text: str, api_key: str, model: str = "claude-sonnet-4-6", max_tokens: int = 1500) -> dict | None`
  - Guard: `if not api_key or not raw_text or not raw_text.strip(): return None`
  - Inside `try`: `import anthropic` → `anthropic.Anthropic(api_key=api_key)` → `client.messages.create(model=..., max_tokens=..., system=_system_prompt(), messages=[{"role":"user","content":raw_text}], timeout=LLM_REQUEST_TIMEOUT_SEC)`
  - Read `response.content[0].text`, parse strict JSON.
  - `except Exception: return None` (covers missing package, API error, JSON parse error → offline/None fallback).
  - Returns a dict of **atomic facts only** (no aggregates).
- `_system_prompt() -> str` — built as a **function** (not a constant) so it interpolates `COMPOSITE_BUY` from `constants.py` at call time (matches `intelligence_report._system_prompt`). Instructs: extract ONLY stated facts; the **primary** stock the article analyzes (guard against sidebar/mention tickers like an AMZN widget); never fabricate a number; `null` for anything not stated; do NOT compute averages.
- `derive_consensus(analysts: list[dict]) -> dict` — **pure**, no LLM. Computes `consensus_rating` (from the rating distribution), `avg_pt`, `high_pt`, `low_pt`. Rating normalization map: Buy/Overweight/Outperform → bullish; Neutral/Hold/Equal-Weight/Market-Perform → neutral; Sell/Underweight/Underperform → bearish.

**Extraction JSON schema** (LLM output):
```json
{
  "ticker": "INIO",
  "company": "Innio N.V.",
  "article_date": "2026-07-03",
  "report_type": "initiation",
  "analysts": [
    {"firm": "Baird", "analyst": "Ben Kallo", "rating": "Buy", "price_target": 50, "upside_pct": 35}
  ],
  "thesis": ["Data centers shifting from grid to onsite gas engines", "..."],
  "catalysts": ["AI data-center capex", "$4.8B backlog execution"],
  "risks": ["Demand slowdown vs. gas turbines", "Capacity/supply-chain"]
}
```
`report_type` enum: `initiation | upgrade | downgrade | reiteration | pt_change | other`.

### New Supabase table — `analyst_coverage` (one-time DDL, applied manually)

Ships **inert** until the DDL is run in Supabase (load returns empty on missing table). RLS on, `FOR ALL TO service_role` per project rule.

```sql
create table if not exists analyst_coverage (
    id               bigint primary key generated always as identity,
    ticker           text not null,
    company          text,
    article_date     date not null,
    report_type      text,
    analysts         jsonb not null default '[]'::jsonb,
    consensus_rating text,
    avg_pt           numeric,
    high_pt          numeric,
    low_pt           numeric,
    thesis           jsonb default '[]'::jsonb,
    catalysts        jsonb default '[]'::jsonb,
    risks            jsonb default '[]'::jsonb,
    raw_text         text,
    source           text default 'cnbc_pro',
    created_at       timestamptz default now()
);
alter table analyst_coverage enable row level security;
create policy "service_role_all_analyst_coverage" on analyst_coverage
    for all to service_role using (true) with check (true);
```

### `stock_analyzer/db.py` additions

- `_ANALYST_COVERAGE_COLS = [...]` (all columns above).
- `save_analyst_coverage(record: dict) -> bool` — `if _READONLY: return False` first line, then `has_db()`, then `_client().table("analyst_coverage").insert(record).execute()` (append-only), `except` → `api_health.record(...)` + `return False`. (Read-only viewer guard mandatory.)
- `load_analyst_coverage(ticker: str | None = None, days: int | None = None, limit: int = 100) -> pd.DataFrame` — `select("*")`, optional `.eq("ticker", ...)` and `.gte("article_date", cutoff)`, `.order("article_date", desc=True)`, backfill-None against `_ANALYST_COVERAGE_COLS`, empty DataFrame on any failure/missing table.
- `delete_analyst_coverage(row_id) -> bool` — read-only guarded, for inbox cleanup.

### `stock_analyzer/constants.py` additions

New section after the news-sentiment block. **Awareness knobs, not gates** (flagged as such):
```python
# ── Analyst coverage (awareness layer — NOT a gate) ──────────────────────────
ANALYST_COVERAGE_FRESH_DAYS = 30   # a report is "recent" in the Ideas Inbox for this many days
ANALYST_MIN_UPSIDE_PCT      = 15   # Phase-2 Brief chip threshold (avg-PT upside); unused in Phase 1
```

### `app.py` — new sub-section on 🧠 AI Insights (after the F-4 block, ~line 17672+)

Matches house style: `st.divider()` → `st.subheader("📋 Analyst Coverage — Ideas Inbox")`.

1. **Capture:** `st.text_area("Paste analyst article")` + **"Extract"** button (`disabled=not _ai_api_key`). On click → spinner → `analyst_intel.extract_report(...)`. `None` → `st.error("Extraction failed — LLM offline or unparseable.")`.
2. **Editable preview:** show parsed fields in editable widgets — ticker/company/date/type text inputs, per-firm rows (firm/rating/PT/upside), thesis/catalysts/risks text areas. Python re-runs `derive_consensus` on the (possibly edited) analysts. **"Save to Inbox"** → build record → `db.save_analyst_coverage`.
3. **Inbox library:** `db.load_analyst_coverage(days=ANALYST_COVERAGE_FRESH_DAYS)`, newest first. One card per report: consensus chip, firm count, avg/high/low PT, thesis bullets, expandable raw text. **Held/watchlist tickers highlighted** (cross-reference `st.session_state.holdings`/`watchlist`). Per-card **"▶ Analyze {ticker}"** (sets `_pending_page="📈 Analysis"` + `_analysis_ticker` + `st.rerun()`) and **"➕ Add to Watchlist"** (`st.session_state.watchlist.append` + `db.save_watchlist`, read-only guarded). 🗑 delete per card.

---

## Out of scope for Phase 1 (later phases)

- **Phase 2 (enrichment):** Analyst Coverage panel on the 📈 Analysis drill-down; inject consensus into F-1 Thesis Advisor evidence; Brief awareness chip for held tickers with ≥ `ANALYST_MIN_UPSIDE_PCT` upside + fresh coverage.
- **Phase 3 (Grow Today):** annotate new-position cards with analyst consensus; optional soft tie-breaker (never a gate).

## Docs to sync at build commit
- `docs/requirements.md` — new F-row under §3.12 (AI layer) or §3.5 (News Intelligence).
- `docs/architecture.md` — module tree (`analyst_intel.py`), constants table (2 new rows), DB schema (`analyst_coverage`).

## Routing
- 🔵 **implementer** (Sonnet) — build the module, db functions, constants, and page section from this spec.
- 🔴 **reviewer** (Opus) — pre-commit review (new module + db writes + AI-layer page; verify offline degradation, read-only guard, no gate leakage, extraction correctness).
- 🟢 **doc-writer** (Haiku) — requirements F-row + architecture rows *after* facts are pinned.
