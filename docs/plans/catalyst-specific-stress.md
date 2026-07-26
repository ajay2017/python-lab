# D4 — Catalyst-Specific Stress — Design Plan

**Date:** 2026-07-26
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** DRAFT — pending Opus design review.

> **One-line spec:** A new "📅 Catalyst-Specific Stress (Beta)" expander, sibling to
> P5's "🎯 Regime-Aware Adversarial Scenario" on the same 🔗 Risk Analysis → 🔥 Stress
> Testing tab. Where P5 asks "what ONGOING macro CONDITION would hurt this book
> most," D4 asks the event-driven twin: "what's the single nearest DATED EVENT
> (a HIGH-impact macro print/FOMC date, or a held position's own earnings report)
> that would hurt the SAME structural weak points P3/P5 already found?" Pure-Python
> ranking (no new stress math) feeds one Haiku call for the narrative — identical
> shape to P5's proven pattern.

> **Roadmap context:** Priority 8 (last) of [agentic-intelligence-roadmap-v2.md](agentic-intelligence-roadmap-v2.md)
> Phase 4 — explicitly "a P5 variant, not a new axis," the lowest-priority,
> least-audited item on the roadmap.

---

## HEAD audit — what's reused vs. genuinely new

The roadmap table's note ("reuses P5 synthesis pattern + Catalyst Watch calendar")
undersold the amount of real reuse available. Verified against HEAD (2026-07-26):

- **`macro_calendar.build_macro_calendar(port_df, fred_key, ...)`** (`macro_calendar.py:562`)
  already returns a full dated event list — `date`, `event`, `category`, `impact`
  (`HIGH`/`MEDIUM`/`LOW`, `macro_calendar.py:31-33`), `affected_tickers` (matched by
  category → sector, `_affected_tickers()`, `macro_calendar.py:303`) — for FOMC/CPI/NFP
  and other named macro events, real and already computed for the existing Economic
  Calendar page. **D4 needs zero new event-sourcing code for the macro side** — it
  filters this existing list to `impact == "HIGH"` within a window.
- **`app.py::_cached_catalyst_calendar(tracked_tuple, from_str, to_str)`** (`app.py:2343`)
  already returns per-ticker upcoming earnings dates (FMP calendar + yfinance
  per-name fallback, 24h-cached) — the exact function Catalyst Watch's own Radar tab
  calls. **D4 reuses this verbatim**, scoped to held tickers only (not the full
  watchlist+universe set Catalyst Watch tracks — D4 only cares about names already
  IN the book).
- **`structural_scanner.blast_radius()`** and **`portfolio_intelligence.correlation_clusters()`**
  — both already computed for P5's own expander (`app.py:10318-10325`) — the
  "structural weak points" substrate D4 cross-references against. Zero new
  computation; D4 reuses the SAME session-scoped results P5 just built, when P5's
  expander has already run this session (see render-order note below).
- **`regime_stress.generate_regime_scenario()`**'s exact shape (`regime_stress.py:173-274`)
  — single Haiku call, evidence-only citation, canonical-label validation for any
  item selected from a closed list, bare-`except Exception` degradation, day-cached,
  never caches a failed/empty call. **D4's Haiku call mirrors this pattern exactly** —
  same system-prompt discipline, same validation shape, different evidence.

**What's genuinely new:** one pure-Python ranking function that has no existing
equivalent — given a set of candidate dated events (macro + held-ticker earnings) and
the structural weak-point evidence (blast-radius top contributors, cluster members),
compute which candidate event's `affected_tickers`/own-ticker overlaps most with
those weak points, and rank candidates by that overlap. This numeric ranking is pure
arithmetic over already-computed weights/contributions — not a new stress model, not
a new shock formula, matching the roadmap's own "not a new axis" framing.

---

## Render-order dependency (a real constraint, not a nitpick)

P5's expander computes `_rs_blast`/`_rs_clusters` **locally inside its own `with
st.expander(...)`** — they are not published to `st.session_state` for reuse by a
sibling section. Two options were considered:
1. Have D4 recompute `blast_radius()`/`correlation_clusters()` independently
   (cheap, pure Python, no API cost — this is what P5 itself does from
   `_corr_df_cache`/`risk_budget()`, not from a cache either).
2. Nest D4 inside P5's `else:` block to reuse the local variables directly.

**Decision: recompute independently (option 1).** Nesting would make D4 wrongly
depend on P5's own render/button state (P5's expander gates on the SAME correlation
availability D4 would also need to check) and couples two otherwise-independent
features' failure modes. Recomputing `blast_radius()`/`correlation_clusters()` is
exactly as cheap as P5 already treats it (no caching, computed fresh every render) —
this is not a new cost, just a second call to the same pure functions.

---

## What already exists (reused, not rebuilt)

| Piece | Where | Status |
|---|---|---|
| Macro event calendar with impact + affected tickers | `macro_calendar.build_macro_calendar()` (`macro_calendar.py:562`) | Shipped. Reused verbatim, filtered to `impact == "HIGH"`. |
| Held-ticker earnings dates | `app.py::_cached_catalyst_calendar()` (`app.py:2343`) | Shipped. Reused verbatim, scoped to held tickers. |
| Structural weak-point evidence | `structural_scanner.blast_radius()`, `portfolio_intelligence.correlation_clusters()` | Shipped (P3/P5). Recomputed independently per the render-order decision above. |
| Haiku synthesis pattern + day-cache | `regime_stress.py`, `db.load_regime_scenario_cache`/`save_regime_scenario_cache` | Shipped (P5). D4 mirrors the exact shape with new module/table names. |
| 🔥 Stress Testing tab | `app.py:10305-10398` (P5's expander) | Shipped. D4 adds a sibling expander, same tab. |

## What's genuinely new

1. **One new pure-Python function**, `catalyst_stress.rank_catalyst_threats(macro_events, earnings_events, blast_radius_results, clusters, port_df) -> list[dict]`:
   - Build the "weak-point ticker set": blast-radius top contributors ∪ all
     correlation-cluster member tickers.
   - Candidate events = HIGH-impact macro events (within `EARNINGS_URGENCY_SOON_DAYS`
     — reused as the urgency window, no new constant) whose `affected_tickers`
     intersects the weak-point set, **plus** held-ticker earnings within the same
     window where that specific ticker IS in the weak-point set.
   - Score each candidate by the combined portfolio weight% of the overlapping
     tickers (macro event: sum of `affected_tickers ∩ weak-point set` weights;
     earnings event: that ticker's own weight%, plus its blast-radius contribution %
     if it's a top-3 contributor).
   - Return candidates sorted by score, descending. Empty list if no candidate has
     any weak-point overlap — a legitimate, expected "nothing catalyst-specific
     stands out" result, not an error.
2. **One new Haiku call**, `catalyst_stress.generate_catalyst_narrative(evidence, api_key, model)` —
   mirrors `regime_stress.generate_regime_scenario()`'s exact shape: given the
   top-ranked candidate event(s) plus the same blast-radius/cluster evidence P5 uses,
   write a 2-4 sentence narrative naming WHY this specific dated event threatens
   THESE SPECIFIC weak points — citing only supplied tickers/clusters/event details,
   never inventing a threat mechanism not evidenced. "No catalyst-specific threat
   stands out" is a valid, expected output when the ranking found no overlap.
3. **New cache table** `catalyst_stress_cache` — identical shape to
   `regime_scenario_cache` (one row per `scan_date`, snapshot fields for
   auditability, never caches a failed/empty result).
4. **A new expander**, "📅 Catalyst-Specific Stress (Beta)," rendered as a sibling to
   P5's expander on the same 🔥 Stress Testing tab — same button-gated,
   day-cached-once-generated pattern.

**Explicitly NOT built:** no new stress/shock formula, no new event-sourcing code
(macro or earnings), no change to `blast_radius()`/`correlation_clusters()`/
`build_macro_calendar()`/`_cached_catalyst_calendar()` themselves, no gate, no score
change.

---

## Design principles (non-negotiable, carried from v1/v2 — identical to P5's)

1. **Strictly additive.** No gate, no score, no change to any existing stress
   scenario or the Economic Calendar/Catalyst Watch pages themselves.
2. **Never fabricates a threat mechanism.** The Haiku prompt must explicitly permit
   "no catalyst-specific threat stands out" (mirrors P5's "if the evidence doesn't
   support a coherent... scenario, say so plainly").
3. **Ranking is pure arithmetic over already-computed weights/contributions** — not
   a new quantitative model. No new shock magnitude, no new correlation math.
4. **Button-gated, day-cached**, never auto-computed on tab render (same lesson as
   every prior LLM feature on this roadmap — `st.tabs()`/expanders execute their
   body on every rerun regardless of visibility).
5. **Canonical-label validation for anything the LLM selects from a closed list**
   (if the narrative names a specific affected ticker or cluster, it must be
   validated against the supplied evidence exactly like P5's indicator-watchlist
   matching) — never the LLM's own echoed text.
6. **Recomputed independently, not nested inside P5's render state** — per the
   render-order decision above, so D4 never silently depends on whether the user
   happened to open P5's expander first this session.

## Non-goals

- Does not predict WHEN a HIGH-impact event will actually move the market a
  particular direction — only which upcoming dated event has the most structural
  overlap with already-identified weak points, exactly as P5 never forecasts
  regime-change timing.
- Does not touch Catalyst Watch's own earnings-awareness pages (Positions/Radar/Entry
  Candidates tabs) — reuses their underlying fetch function only.
- Does not expand the earnings-event scope beyond HELD tickers (watchlist/universe
  earnings dates stay Catalyst Watch's own concern, not this feature's).
