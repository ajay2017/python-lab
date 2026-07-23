# Thesis Red Team Agent — Design Plan

**Date:** 2026-07-23
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 4.6
**Opus review:** Round 1 — FIX-FIRST (5 blocking). Round 2 pending.
**Status:** PLANNING — v3 incorporates all Round 1 + Round 2 findings. Proceeding to Phase 1 build; Opus code review before Phase 1 merges.

> **One-line spec:** A persistent adversarial agent that continuously re-attacks each
> held position's bull thesis using existing quantitative signals + optional Haiku
> counter-evidence, producing a daily "thesis erosion score" and structured bear case
> that updates automatically — without any human trigger.

---

## Review log

| Round | Model | Verdict | Blocking findings |
|---|---|---|---|
| Round 1 | Claude Opus 4.8 | FIX-FIRST | 5 blocking (data sourcing, composite delta, timezone, constants, phase scope) — all resolved in v2 |
| Round 2 | Claude Opus 4.8 | FIX-FIRST | 4 blocking (SPY path, bundle price path, composite delta source, exit_signals filter) — all resolved in v3 |

---

## Differentiation from Pre-Mortem (F-187)

This is the most important thing to get right, or the feature will read as a duplicate.

| Pre-Mortem (F-187, existing) | Thesis Red Team Agent (new) |
|---|---|
| User-authored: you write "what would make me wrong" | LLM-generated: agent reads current data and finds what *is* going wrong |
| One-time: fires at buy, never again | Continuous: refreshes every trading day the page loads |
| Gate: blocks the BUY write if the field is empty | Advisory only: never gates anything |
| Stored in `trades.premortem_commitment` | Stored in new `thesis_erosion_cache` table |
| Reflects your *ex-ante* beliefs | Reflects *current market data* vs. the thesis |
| No composite trend awareness | Reads composite trend, RS vs SPY, tier, analyst PT revisions |

**UI differentiation rule:** wherever the Red Team output appears, the first sentence
must say "Since you bought, the data now shows…" — making it explicit this is
retrospective/continuous, not a repeat of the pre-mortem prompt.

**Closed loop (Phase 2):** the agent reads `trades.premortem_commitment` as Haiku context.
If you wrote "I'd exit if margins compress" at buy time, the agent can now say "Your
pre-mortem said margin compression would invalidate this thesis — gross margin has fallen
4pp since your entry." This closes the loop Pre-Mortem opened.

---

## Design principles (non-negotiable)

1. **Strictly additive.** Erosion score never modifies composite score, gate decisions,
   or any recommendation. It is awareness-only.
2. **Quantitative score = $0 LLM cost.** The erosion score (0–100) derives entirely
   from existing computed signals — no API call needed for Phase 1. Haiku is Phase 2.
3. **Day-cached, ET-keyed.** Maximum one Haiku call per ticker per Eastern calendar day.
   `score_date` = `_today_et()` result (`app.py:4149`), not `date.today()` (UTC).
   Pattern: `(ticker, score_date)` composite key, same structure as `sentiment_llm_cache`.
4. **Graceful degradation.** If the Haiku call fails, the erosion score still shows.
   If score computation fails for a ticker, that ticker is skipped silently.
   The feature never crashes other surfaces.
5. **Never fabricates.** If a ticker has no stored thesis, no Haiku call is made.
   Haiku prompt instructs: cite only evidence from the supplied signal set; a generic
   or invented counter-argument is a failure (matches `premortem_advisor.py` bar).
6. **Scoped to held positions.** Watchlist and candidates are out of scope for Phase 1–3.
7. **Trading-day only.** Gate the compute+cache on `is_trading_day()` to avoid writing
   weekend rows that corrupt cross-day delta calculations in Phase 3.

---

## Erosion Score — derivation (no LLM, fully quantitative)

Score range: 0 (thesis fully intact) → 100 (thesis effectively broken).

### Data sources (v2 — Opus-corrected)

**Critical fix from Round 1:** `assess_holding()` returns `None` for any position not
already in WATCH/TRIM/EXIT, making it useless for the "catch erosion early" goal.
The erosion score must work for *all* held positions, including intact ones.

| Component | Correct data source | Weight | Maps to score |
|---|---|---|---|
| **Deterioration tier** | `exit_signals` table, filtered to `signal_date == _today_et()` for this ticker. Take the most severe `signal_type` in that day's rows. Absent = tier `None` = 0 pts. Tier is 0 if Home hasn't run today yet — explicitly acceptable; tier is a bonus signal not the foundation. | 30 pts | None=0, WATCH=10, TRIM=22, EXIT=30 |
| **Momentum vs. benchmark** | New `compute_relative_strength(price_series, spy_series, window=20)` helper in `exit_advisor.py`. Ticker series: `_last_held_data[ticker]["df"]["Close"]`. SPY series: `_cached_spy("6mo")["Close"]` (`app.py:1977`) — NOT `_last_held_data["SPY"]` (SPY is not in that dict unless the user holds it). | 30 pts | `max(0, min(30, -rs × 1.5))` — negative RS contributes linearly |
| **Composite trend** | Self-referential from `thesis_erosion_cache.signals_snapshot`. "Today" = `_port_df_enriched["Score"]` (always available in session state). Store in `signals_snapshot["composite_today"]`. "5 sessions ago" = read own cache row from 5 ET-trading-days back, extract `signals_snapshot["composite_today"]`. Inert (delta=0) for the first 5 trading days — same bootstrap pattern as `analyst_target_snapshots`. NOT from `recommendations_history` (only covers engine-surfaced names). | 25 pts | `max(0, min(25, -delta × 2.5))` — falling composite contributes |
| **Analyst PT revision** | `analyst_target_snapshots`. Earliest snapshot vs. today's. Inert (0 pts) for most tickers until late August — table started 2026-07-21. Explicitly acceptable; stated so the score isn't mis-read during Phase 1 validation. | 15 pts | Upward revision → 0pts; flat → 7pts; cut → 15pts |

**Weight redistribution from Round 1:** tier weight dropped from 40 → 30; RS weight
increased from 25 → 30; composite trend increased from 20 → 25. This shifts more weight
toward signals available for *all* held tickers (RS + composite), not just flagged ones.

**Total:** 0–100. Display labels (display only — no gate fires at any level):
- 0–24: **Intact**
- 25–49: **Softening**
- 50–74: **Eroding**
- 75–100: **Breaking**

### New helper required: `compute_relative_strength()`

Add to `stock_analyzer/exit_advisor.py`:

```python
def compute_relative_strength(price_series: pd.Series, spy_series: pd.Series, window: int = 20) -> float:
    """Return name 20-day return minus SPY 20-day return in pct-pts. Returns 0.0 on any error."""
```

Pure function. No Streamlit dependency. Callable from `thesis_red_team.py` directly.
Verified: no name collision with existing functions in `exit_advisor.py`.

**Caller in `app.py` (inside the Red Team tab):**
```python
_ticker_close = st.session_state.get("_last_held_data", {}).get(ticker, {}).get("df", pd.DataFrame()).get("Close", pd.Series())
_spy_close    = _cached_spy("6mo").get("Close", pd.Series())   # app.py:1977 helper
_rs = compute_relative_strength(_ticker_close, _spy_close)
```

`_last_held_data["SPY"]` does NOT exist unless the user holds SPY — always use
`_cached_spy()` for the benchmark series.

### Constants — new additions to `constants.py` (Opus finding #5)

These are behavioral boundary values that control API cost and a decision-adjacent surface.
Per CLAUDE.md hard rule #1, they belong in `constants.py`:

```python
THESIS_EROSION_HAIKU_MIN  = 30   # minimum erosion score to trigger Haiku counter-evidence call
THESIS_EROSION_BRIEF_MIN  = 50   # erosion score threshold for Daily Brief annotation
THESIS_EROSION_BRIEF_JUMP = 15   # same-day score jump that triggers Brief annotation regardless of absolute level
```

### Weights — module-level, NOT in `constants.py`

The 30/30/25/15 component weights and 25/50/75 display-band thresholds live in
`thesis_red_team.py` as module-level defaults. They drive a display label only — no gate,
no recommendation path. Tune against live WATCH/TRIM/EXIT positions for ~1 week in
production, then adjust without Opus review. If any weight ever feeds a gate, escalate
to `constants.py` immediately.

---

## Haiku counter-evidence call (Phase 2 only)

**Trigger conditions (all must be true):**
1. `user_thesis` non-empty for this ticker (stored in `trades`)
2. `erosion_score >= THESIS_EROSION_HAIKU_MIN` (from `constants.py`)
3. No fresh ET-day cache row for `(ticker, score_date)` in `thesis_erosion_cache`

**Inputs to Haiku:**
```
- ticker, current price, entry price, position age (days)
- Original thesis text (trades.user_thesis)
- Pre-mortem commitment text (trades.premortem_commitment) — included if non-empty
- Erosion score + component breakdown (which signals are firing and by how much)
- Deterioration tier (if any): WATCH/TRIM/EXIT
- 5-session composite score delta (numeric, negative = falling)
- Relative strength vs. SPY last 20 sessions (numeric, pct-pts)
- Analyst PT revision since entry (if available: direction + magnitude)
```

**Prompt structure:**
- System: "You are a bear-case analyst. Given the investor's stated bull thesis and the
  quantitative signals below, identify the 2–3 strongest specific counter-arguments the
  *data currently supports*. Every counter-argument MUST reference specific evidence from
  the supplied signal set — a generic or invented argument is a failure. If signals are
  insufficient to form a grounded bear case, return an empty array. Output valid JSON only."
- Output schema: `[{"claim": str, "severity": "low"|"medium"|"high", "signal_basis": str}]`
  — max 3 items, min 0. `signal_basis` must be a value from the supplied signal data
  (e.g. `"rs_vs_spy = -4.2pp"`, `"composite fell 8 pts over 5 sessions"`,
  `"analyst PT cut from $180 → $155"`). Implementation must validate this before saving.

**Model:** `claude-haiku-4-5-20251001`
**Max tokens:** 600 (bumped from 400; 400 is tight for 3 structured JSON objects with 3 fields each and risks truncation → parse failure)
**Temperature:** 0 (structured output)

**Output validation in `parse_counter_evidence_response()`:**
- Must parse as valid JSON list
- Each item must have `claim` (str, non-empty), `severity` (∈ {low, medium, high}), `signal_basis` (str, non-empty)
- Items failing validation are dropped; if all fail, return `None` (don't save garbage)

**Read-only viewer guard:** `save_thesis_erosion_cache` is a system-cache write (computed
data, not user input). Same classification as `save_sentiment_llm_cache` (ungated in
`db.py:2112`). No `_READONLY` guard needed — consistent with the existing pattern.

---

## Supabase table: `thesis_erosion_cache`

```sql
CREATE TABLE IF NOT EXISTS thesis_erosion_cache (
    ticker         text        NOT NULL,
    score_date     text        NOT NULL,  -- ET ISO date, e.g. '2026-07-23' via _today_et()
    erosion_score  numeric     NOT NULL,  -- 0–100
    erosion_label  text        NOT NULL,  -- 'Intact' | 'Softening' | 'Eroding' | 'Breaking'
    counter_evidence jsonb,               -- [{claim, severity, signal_basis}] or null (Phase 2+)
    signals_snapshot jsonb      NOT NULL, -- raw component values for auditability
    created_at     timestamptz DEFAULT now(),
    PRIMARY KEY (ticker, score_date)
);
```

**DDL delivery:** added to `db.py` as `_THESIS_EROSION_DDL` constant, called from
`db.ensure_schema()`. Same pattern as every other DDL in the file.

**db.py functions needed:**
- `load_thesis_erosion_cache(ticker: str, score_date: str) → dict | None`
- `save_thesis_erosion_cache(ticker, score_date, erosion_score, erosion_label, counter_evidence, signals_snapshot) → None` (upsert, best-effort, never raises)

---

## Implementation — module structure

### New file: `stock_analyzer/thesis_red_team.py`

Pure logic, no Streamlit imports. Exports:

```python
compute_erosion_score(
    tier: str | None,           # from exit_signals table; None for intact names
    rs_vs_spy: float,           # from compute_relative_strength()
    composite_delta_5s: float,  # today's composite minus 5-session-ago composite
    pt_revision_pts: float,     # 0, 7, or 15 based on PT direction
) -> dict
# Returns: {"score": float, "label": str, "components": dict}

build_counter_evidence_inputs(
    ticker, thesis, premortem,  # strings
    erosion_result: dict,       # from compute_erosion_score()
    rs_vs_spy: float,
    composite_delta: float,
    pt_revision_desc: str,      # human-readable, e.g. "cut from $180 → $155"
) -> tuple[str, str]
# Returns (system_prompt, user_prompt)

parse_counter_evidence_response(raw_json: str) -> list[dict] | None
# Returns validated [{claim, severity, signal_basis}] or None
```

### `stock_analyzer/exit_advisor.py` addition

```python
def compute_relative_strength(price_series: pd.Series, spy_series: pd.Series, window: int = 20) -> float:
    ...
```

### `stock_analyzer/constants.py` additions

Three new constants (see §Erosion Score above):
`THESIS_EROSION_HAIKU_MIN`, `THESIS_EROSION_BRIEF_MIN`, `THESIS_EROSION_BRIEF_JUMP`

### `db.py` additions

DDL + `load_thesis_erosion_cache` + `save_thesis_erosion_cache`

### `app.py` wiring

Two integration points in Phase 1; a third added in Phase 3 (see Surfacing).

---

## Surfacing

### Surface 1 — AI Insights: new "⚠️ Red Team" tab (primary surface)

**Change:** Add 5th tab to `st.tabs()` at `app.py` line **23870**:

```python
_ai_tab_pos, _ai_tab_deb, _ai_tab_res, _ai_tab_score, _ai_tab_rt = st.tabs(
    ["🩺 Positions", "📅 Debriefs", "🏦 Research", "📊 Scorecard", "⚠️ Red Team"]
)
```

5th tab addition is safe — `st.tabs()` is positional with no `key` argument; no widget-state collision risk.

**Cold-load guard (non-blocking Opus finding):**
`_last_held_data` and `_port_df_enriched` are populated by Home's `build_portfolio_df`.
On direct nav to AI Insights in a fresh session they are `None`. Guard at the top of
`with _ai_tab_rt:`:
```python
if not st.session_state.get("_last_held_data"):
    st.info("Visit Home first to load price data, then return here.")
    st.stop()  # or return — don't render partial cards
```
Mirror the `_portfolio_snapshot_stale` banner pattern.

**Data flow on page load (inside `with _ai_tab_rt:`):**

For each held ticker:
1. Guard: `if not is_trading_day(): show last cached result, skip compute.`
2. `score_date = _today_et()`
3. Check `thesis_erosion_cache` for `(ticker, score_date)` → cache hit: render stored result.
4. Cache miss:
   - Pull today's tier: load `exit_signals` rows, filter to `signal_date == score_date` + `ticker`, take most-severe `signal_type`. None if no row (intact name or Home not yet run today).
   - Compute `rs_vs_spy` via `compute_relative_strength(_last_held_data[ticker]["df"]["Close"], _cached_spy("6mo")["Close"])`.
   - Today's composite: `_port_df_enriched.loc[ticker, "Score"]` (or equivalent row lookup).
   - 5-session-ago composite: read own `thesis_erosion_cache` row from 5 ET-trading-days back → `signals_snapshot["composite_today"]`. Zero if no row yet (first 5 days).
   - PT revision: `analyst_target_snapshots` earliest vs. today. Zero if absent.
   - Call `compute_erosion_score(tier, rs_vs_spy, composite_delta, pt_revision_pts)`.
   - Build `signals_snapshot` dict including `composite_today` (for future 5-session lookback).
   - **Phase 2 only:** if `erosion_score >= THESIS_EROSION_HAIKU_MIN` and thesis stored → call Haiku → validate → include in save.
   - Call `save_thesis_erosion_cache(...)`.
5. Render from result dict.

**Content (Phase 1):**
- Header + one-sentence explanation.
- Portfolio summary bar: N Breaking / N Eroding / N Softening / N Intact.
- Per-ticker cards sorted by erosion score descending:
  - Ticker + erosion score chip (red ≥75, amber 50–74, yellow 25–49, green <25)
  - Erosion label
  - Expander "Signal breakdown": raw component values from `signals_snapshot`
  - If no `_last_held_data` for ticker: "Price data unavailable — score not computed today."
- Manual "Refresh" button: clears today's cache rows for all held tickers, re-runs compute. Disabled after one use per session.

**Additional content in Phase 2:**
- If `counter_evidence` present: 2–3 bullet points with severity badge + signal basis.
- If thesis not stored: "No thesis on record — add one in AI Insights → Positions to enable the bear case."
- If `erosion_score < THESIS_EROSION_HAIKU_MIN`: "No material counter-evidence threshold reached today."

### Surface 2 — Exit Advisor WATCH/TRIM/EXIT cards (Phase 2 only)

**Change:** Add `st.expander("⚠️ Red Team")` to existing deterioration cards.

**Shows:** narrative counter-evidence bullets only (Phase 2 content). **Does NOT** show
the erosion score chip — the chip is circular on these cards (score partially derived
from the same tier that fired the card). Leading sentence: "Since you bought, the data
now shows…" This is a pure cache read; no compute is triggered from here.

### Surface 3 — Daily Brief annotation (Phase 3)

**Change:** After existing Brief sections, add "Thesis Under Pressure" for any ticker where:
- `erosion_score >= THESIS_EROSION_BRIEF_MIN` AND yesterday's score was below that threshold
- OR single-day jump `>= THESIS_EROSION_BRIEF_JUMP` regardless of absolute level

Cross-day delta: two cache reads per ticker (`today` and `yesterday` ET dates).
If yesterday absent (first run), surface only if absolute score `>= THESIS_EROSION_BRIEF_MIN`.
Gate on `is_trading_day()` same as the compute step.

One-line format: "NVDA thesis erosion jumped to 62 (Eroding) — see Red Team tab for details."

---

## Phased build

| Phase | Scope | Gate for next phase |
|---|---|---|
| **Phase 1** | Erosion score only (no Haiku). "⚠️ Red Team" tab on AI Insights. Score, label, signal breakdown expander, per-ticker cards. `compute_relative_strength()` helper. 3 new `constants.py` entries. | Observe score distribution against real holdings for ~1 week. Verify intact positions (not in WATCH/TRIM/EXIT) get non-zero RS + composite components. Verify score moves in expected direction. |
| **Phase 2** | Haiku counter-evidence. Pre-mortem loop (`premortem_commitment` as context). Counter-evidence bullets in Red Team tab + Exit Advisor card expanders. Opus plan re-review required before this phase. | Counter-evidence renders for tickers with stored thesis; graceful blank without. |
| **Phase 3** | Daily Brief "Thesis Under Pressure" annotation. Cross-day delta detection. | Brief annotation fires on correct conditions; no false positives on weekends. |

**Phase split rationale (Opus Round 1 recommendation, accepted 2026-07-23):** Findings
1–3 leave the score's real-world distribution unproven for intact names. Wiring a paid
Haiku call against an unvalidated score risks the LLM only firing on positions Exit
Advisor already flags — no new signal value. Ship and validate Phase 1 first.

---

## Cost model

| Item | Per ticker/day | 20 held tickers/day | Per month |
|---|---|---|---|
| Erosion score (Phase 1) | $0 | $0 | $0 |
| Haiku counter-evidence (Phase 2, when triggered) | ~$0.0005 | ~$0.01 | ~$0.30 |
| Day-cache hits | $0 | $0 | $0 |

Phase 2 Haiku cost is bounded by: only fires on cache miss + thesis stored + score ≥ 30.
Real-world cost will be materially lower than the model above.

---

## What NOT to build in this plan

- **Erosion score for watchlist / candidates.** Out of scope for Phase 1–3.
- **Composite score modification.** Erosion score is display-only, forever.
- **Auto-actions or alert emails.** No gate fires, no email from this feature alone.
- **Historical erosion chart.** Data will accumulate in the cache; charting is Phase 4+.
- **Intraday refresh.** Day-cache is sufficient for a medium-term advisor.
- **Erosion score as a composite score pillar.** Permanently excluded.

---

## Design decisions — locked (2026-07-23)

1. **Weights (30/30/25/15):** Tunable calibration in `thesis_red_team.py`, NOT `constants.py`.
   Escalate to `constants.py` only if a weight ever feeds a gate.

2. **Composite delta source:** Self-referential from `thesis_erosion_cache.signals_snapshot`.
   Store `composite_today = _port_df_enriched["Score"]` in each row's `signals_snapshot`.
   Read 5-ET-trading-days-back row's `signals_snapshot["composite_today"]` for the lag.
   Inert (delta=0) for first 5 trading days. NOT from `recommendations_history` (only
   covers engine-surfaced names, not manually held positions).

3. **Analyst PT revision:** `analyst_target_snapshots`. Will score 0 for most tickers until
   late August (table started 2026-07-21). This is acceptable and stated explicitly so the
   score isn't mis-read during Phase 1 validation.

4. **Tab name:** "⚠️ Red Team" — confirmed.

5. **Pre-mortem loop:** Phase 2. Read `premortem_commitment` from most recent BUY row per
   ticker. Include in Haiku context when non-empty.

6. **score_date timezone:** America/New_York via `_today_et()` (`app.py:4149`).
   NOT `date.today()` (UTC) — UTC rolls at 20:00 ET and would mismatch the cross-day delta.

7. **Phase split:** Phase 1 (score only) ships first. Phase 2 (Haiku) requires 1-week
   production observation of Phase 1 score distribution, then a second Opus review.

---

## Review required before build

**Phase 1:** Sonnet implementer can proceed after Opus Round 2 clears this plan.
**Phase 2:** Requires a third Opus review of the Haiku prompt + output schema before build.

**Round 2 checklist for Opus:**
- [ ] Erosion score data sourcing — does `_last_held_data["SPY"]["Close"]` exist in session state at AI Insights page load, or does it need to be fetched separately?
- [ ] `compute_relative_strength()` placement in `exit_advisor.py` — no side effects on existing callers?
- [ ] `exit_signals` table query pattern — is there an existing `db.load_exit_signals(ticker)` function, or does the plan need to spec one?
- [ ] 3 new `constants.py` entries — appropriate values for `THESIS_EROSION_HAIKU_MIN=30`, `THESIS_EROSION_BRIEF_MIN=50`, `THESIS_EROSION_BRIEF_JUMP=15`?
- [ ] `is_trading_day()` guard — sufficient to prevent weekend rows?
