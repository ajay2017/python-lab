# D4 — Catalyst-Specific Stress — Design Plan

**Date:** 2026-07-26
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** SHIP (revised after Opus design review — FIX-FIRST round resolved). One
open policy question for the user (the new window constant) flagged below.

> **Opus design review (round 1): FIX-FIRST** — 3 blocking findings, fixed in this
> revision. All reuse claims verified accurate (macro calendar shape, earnings
> calendar shape, P5's render-order/local-variable behavior, blast_radius/
> correlation_clusters recompute cost, graceful degradation to empty). **Blocking
> #1 — the ranking metric was structurally rigged toward macro events, and
> anti-informative for the worst offenders.** Summing the weight% of a macro event's
> `affected_tickers` against a single earnings event's one-ticker weight means a
> broad macro event will almost always outscore a specific name — and two macro
> categories (`Fed Policy`/FOMC, `Growth`/GDP) use an `__ALL__` sentinel that returns
> *every* held ticker from `_affected_tickers()`, guaranteeing they score the
> maximum possible overlap by construction, precisely because they say nothing
> about *which* structural weak point is exposed. Left as designed, this feature
> would predictably always surface "the next CPI/FOMC" — uninformative, and a direct
> §2B calm-advisor violation (a correct-but-undifferentiated prompt that doesn't
> change week to week). **Fixed: two separate candidate lists** (macro, earnings),
> never cross-scored against each other — see the revised ranking spec below.
> `__ALL__`-category macro events are excluded from the structural-overlap ranking
> entirely (they threaten everything equally, so they can't discriminate a weak
> point — the opposite of this feature's purpose). **Blocking #2 — an undefined
> field reference** ("blast-radius contribution %") that doesn't exist under that
> name in `blast_radius()`'s actual return shape — **fixed: pinned to the exact
> field**, `portfolio_impact_pct` from the entry where the ticker is the
> `shocked_ticker`. **Blocking #3 — an ambiguous "weak-point ticker set" definition**
> (`blast_radius()` has two ticker roles — `shocked_ticker` and cascade
> `contributing_tickers[].ticker` — and the plan didn't say which, or both) —
> **fixed: pinned explicitly to the union of both**, plus every ticker appearing in
> any `correlation_clusters()` cluster. 2 non-blocking corrections also folded in
> (a distinct new window constant instead of reusing `EARNINGS_URGENCY_SOON_DAYS`,
> to avoid a silent cross-feature policy coupling; per-ticker earnings dedup to the
> soonest date).

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

1. **One new pure-Python function**, `catalyst_stress.rank_catalyst_threats(macro_events, earnings_events, blast_radius_results, clusters) -> dict`:
   - **Weak-point ticker set (pinned exactly, per review finding #3):** the union of
     (a) every `shocked_ticker` across `blast_radius_results`, (b) every
     `contributing_tickers[].ticker` across the same results (the cascade names —
     exposed via correlation even though not directly shocked), and (c) every
     ticker appearing in any `correlation_clusters()` cluster's `tickers` list.
   - **Two SEPARATE candidate lists, never cross-scored (per review finding #1):**
     - *Macro candidates:* HIGH-impact events from `build_macro_calendar()` within
       `CATALYST_STRESS_WINDOW_DAYS`, **excluding any event whose category resolves
       to the `__ALL__` sentinel** (`Fed Policy`/FOMC, `Growth`/GDP) — these threaten
       every sector equally and provide zero discrimination about *which* weak point
       is exposed, the opposite of this feature's purpose. Score = combined
       portfolio weight% of `affected_tickers ∩ weak-point set`.
     - *Earnings candidates:* held tickers with an upcoming earnings date (soonest
       date only per ticker — per review finding on dedup, `_cached_catalyst_calendar()`
       can return more than one row per ticker) within the same window, where that
       ticker IS in the weak-point set. Score = that ticker's own portfolio weight%
       **plus, if it appears as a `shocked_ticker` in any `blast_radius_results`
       entry, that entry's own `portfolio_impact_pct`** (the exact field pinned per
       review finding #2 — no other "contribution" number is invented).
   - Returns `{"macro": [...sorted desc...], "earnings": [...sorted desc...]}` — both
     lists may be empty (a legitimate "nothing catalyst-specific stands out" result,
     not an error). The two lists are never merged or compared against each other;
     if both have a top candidate, both are passed to the Haiku step as two distinct
     threats (a ticker can legitimately face both a sector-wide macro exposure and
     its own earnings date in the same window — these are genuinely separate risks,
     not one compounded event, per review finding on the double-surface edge case).
2. **One new Haiku call**, `catalyst_stress.generate_catalyst_narrative(evidence, api_key, model)` —
   mirrors `regime_stress.generate_regime_scenario()`'s exact shape: given the
   top macro candidate (if any) AND the top earnings candidate (if any), plus the
   same blast-radius/cluster evidence P5 uses, write a 2-4 sentence narrative naming
   WHY each present candidate threatens the SPECIFIC weak points it overlaps with —
   citing only supplied tickers/clusters/event details, never inventing a threat
   mechanism not evidenced, and never merging two distinct candidates into a single
   fabricated compound event. "No catalyst-specific threat stands out" is a valid,
   expected output when both lists are empty.
3. **New cache table** `catalyst_stress_cache` — identical shape to
   `regime_scenario_cache` (one row per `scan_date`, snapshot fields for
   auditability, never caches a failed/empty result — the day-cache guard lives at
   the call site, mirroring P5's exact `if result and result.get("...")` pattern,
   per review finding that this guard is NOT provided by the module itself).
4. **New constant** `CATALYST_STRESS_WINDOW_DAYS = 14` in `constants.py` — a
   dedicated window, deliberately NOT reusing `EARNINGS_URGENCY_SOON_DAYS` (per
   review finding: that constant's documented meaning is the Catalyst Watch
   earnings-playbook "SOON" display tier; reusing it here would silently couple two
   unrelated features to one tunable knob, and a macro print and an earnings report
   have genuinely different natural look-ahead horizons anyway). **Open question for
   the user** (see below).
5. **A new expander**, "📅 Catalyst-Specific Stress (Beta)," rendered as a sibling to
   P5's expander on the same 🔥 Stress Testing tab — same button-gated,
   day-cached-once-generated pattern.

### Open policy question for the user (per CLAUDE.md rule 1)

`CATALYST_STRESS_WINDOW_DAYS` is a threshold decision. Proposing **14 days** as the
default (same numeric value as `EARNINGS_URGENCY_SOON_DAYS`, since that horizon was
already reasoned through for a similar "how far ahead is this still decision-relevant"
question — but as its own independent constant, not a shared reference), adjustable
before ship.

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
