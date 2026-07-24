# Regime-Aware Adversarial Stress Testing — Design Plan

**Date:** 2026-07-24
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** Plan SHIP 2026-07-24 (3 Opus rounds) — ready for implementation

> **One-line spec:** A new "🎯 Regime-Aware Adversarial Scenario" expander at the bottom
> of the existing 🔥 Stress Testing tab that synthesizes three already-shipped systems —
> Structural Scanner's weakest-link/cluster data, the FRED macro regime detector, and
> Cross-Asset Pulse's USD signal — into a one-paragraph "here's the compound scenario
> that would hurt THIS portfolio most" narrative, with an honest current-regime
> confidence readout (not a fabricated 90-day probability) and a real-data early
> indicator watchlist.

---

## Why this plan differs from the roadmap's original framing

`docs/plans/agentic-intelligence-roadmap.md`'s Idea #5 section (written 2026-07-23)
imagined a "scenario plausibility score: how likely is this custom scenario in the next
90 days." Research for this plan found that number **cannot be honestly computed
today**: the only historical regime persistence (`daily_regime`, shipped 2026-07-21)
has ~3 days of accumulated history — nowhere near enough to derive a genuine base rate
like "this regime has historically preceded a drawdown X% of the time." Any such
percentage would be fabricated, violating the AI layer's "never fabricates" invariant
that every other Agentic Intelligence feature this session has upheld.

**User decision (2026-07-24, confirmed via question):** drop the fabricated
probability. Show the regime detector's own real, already-computed **confidence score**
(0–100, produced fresh every time `detect_macro_regime()` runs) instead — reframed
honestly as "how confident the detector is in its current regime read right now," not
a forecast of anything 90 days out.

**A second correction, found during research:** there are **two different
`detect_macro_regime` functions** in this codebase — `macro_calendar.py`'s FRED-based
7-signal detector (persisted to `daily_regime`, used by the existing Regime Fit
feature) and an older, unrelated `macro.py`'s ETF-return-based one (different label
taxonomy, no confidence score, feeds a separate sector-rotation playbook). **This plan
uses `macro_calendar.detect_macro_regime()` exclusively** — the one already imported in
`app.py` as `detect_macro_regime_fred` and already cached per trading day at two
existing call sites (Risk Analysis's Regime Fit section, Economic Calendar's Post-Event
tab). `macro.py`'s function is out of scope and must not be confused with it.

**What's actually new, given these corrections:** a synthesis narrative combining data
that already exists across three separate systems, plus a small amount of new
plumbing to read that data efficiently. **Zero new quantitative modeling.**

---

## What already exists (reused, not rebuilt)

| Piece | Where | Status |
|---|---|---|
| Weakest-link tickers + cascade estimate | `stock_analyzer/structural_scanner.py::blast_radius()` (shipped 2026-07-24) | Shipped — this plan reuses its OUTPUT directly as the "estimated damage" figure, no new stress math |
| Correlation clusters + factor tilt | `stock_analyzer/portfolio_intelligence.py::correlation_clusters()`/`factor_tilt()` | Shipped, reused as narrative evidence |
| Macro regime detection (rate/inflation/curve/credit/VIX, 7 signals) | `stock_analyzer/macro_calendar.py::detect_macro_regime()`, imported in `app.py` as `detect_macro_regime_fred` | Shipped, already day-cached in `st.session_state` at two existing call sites — this plan reuses that cache key, never re-fetches |
| USD/dollar strength stress signal | `stock_analyzer/cross_asset.py::compute_cross_asset_signals()`, wrapped by `app.py::_cached_cross_asset()` | Shipped, `@st.cache_data(ttl=1800)`-memoized, zero-arg — free to call again |
| Named historical + custom-SPY-move stress scenarios | `stock_analyzer/stress_test.py`, rendered on 🔗 Risk Analysis → 🔥 Stress Testing tab | Shipped, unchanged by this plan — this is scenario-first (fixed shock → portfolio); the new feature is portfolio-first (structure → synthesized scenario), a different direction, additive not overlapping |

## What's genuinely new

1. **A synthesis narrative** (1 Haiku call/portfolio/day) combining structural weak
   points + current regime signals + USD stress into a named compound scenario.
2. **An honest confidence readout** — the regime detector's real `confidence` field,
   relabeled clearly, never framed as a forecast probability.
3. **An early indicator watchlist** — 2–3 of the regime detector's own real
   sub-indicator readings (from its `signals` list), selected by the LLM but validated
   so only genuinely-supplied indicator names can appear (never invented).
4. **A small new cache table** (`regime_scenario_cache`) — day-cached, button-gated,
   mirroring `structural_scan_cache`'s exact pattern (including its "never cache a
   failure" fix from Structural Scanner's own pre-ship review).

---

## Design principles (non-negotiable)

1. **Strictly additive.** Nothing here modifies the composite score, a gate, or any
   recommendation. This is a diagnostic narrative appended to an existing stress-test
   surface.
2. **Zero new quantitative modeling.** The "estimated damage" figure is
   `structural_scanner.blast_radius()`'s existing output, recomputed cheaply on this
   page (same pure-Python call, `_corr_df_cache` + `risk_budget()` already available on
   Risk Analysis) — not a new regime-calibrated shock formula. Inventing a "how much
   bigger should the shock be in this regime" multiplier would itself be a fabricated
   number with no empirical basis; this plan deliberately does not do that.
3. **Reuse the existing regime cache, never re-fetch — key derivation must match
   ALL THREE existing fallback sources, not just two (Round 1 Opus finding).**
   `app.py` already caches `detect_macro_regime_fred()`'s result under
   `st.session_state[f"_macro_regime_{_today_et()}_{bool(fred_key)}"]` at two call
   sites (Risk Analysis Regime Fit, Economic Calendar Post-Event), and both derive
   `fred_key` via a THREE-source fallback: `st.secrets["fred"]["api_key"]` →
   `os.environ["FRED_API_KEY"]` → `st.session_state.get("_ec_fred_key", "")` (the
   in-app-entered key from the Economic Calendar UI). This plan's `fred_key`
   derivation must be byte-for-byte identical to those two sites, including the
   third fallback — omitting it produces a different `bool(fred_key)` for users who
   added their key via the in-app UI rather than secrets/env, missing the shared
   cache key entirely and showing a degraded 0-confidence regime read on the same
   page where Regime Fit shows the real one. This plan reads the shared key if
   present; only calls `detect_macro_regime_fred()` fresh if absent (mirrors the
   existing cache-or-compute pattern exactly, no new FRED API cost beyond what the
   app already incurs once/day).
4. **Reuse the existing Cross-Asset cache.** `_cached_cross_asset()` is a zero-arg,
   `st.cache_data(ttl=1800)`-memoized function — calling it again here is free on a
   cache hit within the 30-minute window.
5. **Button-gated narrative, day-cached.** Same lesson as Structural Scanner's Round 1
   Opus finding: `st.tabs()`/expanders execute their body on every rerun regardless of
   visual selection, so the Haiku call must only fire on an explicit button click,
   never automatically.
6. **Never fabricates.** The confidence score is real (never invented). The indicator
   watchlist is validated post-hoc: entries are matched against supplied `signals`
   labels via a normalized (`.strip().casefold()`) comparison, and the CANONICAL label
   from the signals tuple is displayed — never the LLM's own echoed text — so a
   casing/spacing difference can never either fabricate a fake indicator or silently
   drop a legitimately-selected real one. If the evidence doesn't support a coherent
   compound scenario, the narrative says so plainly rather than manufacturing a
   concern (same bar as `structural_scanner`'s narrative prompt).
7. **A failed/empty narrative is never cached.** Mirrors Debate's and Structural
   Scanner's day-cache-poisoning fix — a transient Haiku failure must be retryable
   immediately, not stuck showing a false result until the next day.
8. **Additive UI placement — new expander, not a selectbox branch.** The existing
   Stress Testing tab's scenario selector (`SCENARIOS` + "Custom Scenario", 10 entries)
   has a single, uniform render pipeline built around `run_scenario()`'s output shape
   (`rows`, `most_exposed`, `any_gainers`, etc.). This feature's output shape is
   entirely different (a narrative + confidence + indicator list, not a per-position
   P&L table), so it is added as a **new, separate expander at the bottom of the tab**
   — zero risk of forking or destabilizing the existing 9-scenario+custom rendering
   logic that's already shipped and working.
9. **Opus review required** before build (this plan) and before ship (code review).

---

## New pure functions: `stock_analyzer/regime_stress.py` (new module)

A new small module — this is a genuinely new synthesis "agent" concept (distinct from
`structural_scanner.py`, despite reusing its data), matching the precedent of
`debate_agent.py` getting its own file rather than being folded into an existing module.

```python
def build_regime_scenario_inputs(
    blast_radius_results: list[dict],   # structural_scanner.blast_radius() output
    clusters: list[dict],               # portfolio_intelligence.correlation_clusters() output
    regime_data: dict,                  # detect_macro_regime_fred()'s return dict
    cross_asset_data: dict,             # compute_cross_asset_signals()'s return dict
    factor_tilt: dict | None = None,    # optional, session-scoped, never auto-fetched
) -> dict:
    """
    Assembles the evidence dict for the Haiku prompt. Never raises — degrades
    gracefully, omitting any section that's empty/None. Returns a dict with
    keys: "blast_radius", "clusters", "regime" (label, fed_trend, cpi_yoy,
    confidence, signals list), "cross_asset" (label, score, per-signal detail),
    "factor_tilt" (or None).
    """

def generate_regime_scenario(
    evidence: dict, api_key: str, model: str = "claude-haiku-4-5-20251001",
) -> dict | None:
    """
    Single Haiku call. Returns {"scenario_narrative": str,
    "indicator_watchlist": list[str]} or None on any failure (no key, timeout,
    malformed response). indicator_watchlist entries are matched against
    evidence["regime"]["signals"] labels via a normalized (.strip().casefold())
    comparison, and the CANONICAL label from the signals tuple is returned —
    never the LLM's own echoed text. Entries with no normalized match are
    dropped; if all are dropped, the key is an empty list (never fabricated,
    never causes the whole call to fail).
    Never raises.
    """
```

### Haiku prompt (system message, used verbatim)

```
You are a portfolio structural-macro analyst. Given the portfolio's structural
weak points (correlated clusters, cascade-shock estimates) and the current macro
regime evidence below, name the SINGLE compound macro scenario — combining 2-3
concurrent macro conditions (e.g. rate direction, dollar strength, credit stress)
— that would do the most damage to the SPECIFIC weak points identified. Cite the
specific tickers, clusters, and regime readings supplied — never invent a macro
condition not evidenced below. Then select 2-3 indicators from the supplied
regime signals list that would be the earliest sign this scenario is developing
— select ONLY from the supplied list, never invent a new indicator name. If the
evidence doesn't support a coherent compound scenario (e.g. regime is neutral
and no structural weak point stands out), say so plainly instead of manufacturing
one. Output ONLY valid JSON: {"scenario_narrative": "2-4 sentences", "indicator_watchlist": ["<exact signal label>", ...]}
```

**Model:** `claude-haiku-4-5-20251001`. **Max tokens:** 400. **Temperature:** 0.3
(some narrative variation acceptable; nothing downstream parses the narrative text
structurally — only `indicator_watchlist` is validated).

### Validation (`_parse_regime_scenario_response`, internal helper)

- Must parse as valid JSON object with both keys present.
- `scenario_narrative` must be a non-empty string.
- `indicator_watchlist` entries are matched against `evidence["regime"]["signals"]`
  labels using a **normalized comparison** (`.strip().casefold()` on both sides) —
  Round 1 Opus finding: real signal labels contain special characters/digits/spacing
  (e.g. `"2s10s Spread"`, `"Unemployment Δ3m"`, `"SPY 20d Return"`) that a Haiku call
  may re-normalize even when instructed to copy verbatim, and an exact case-sensitive
  match would silently drop a legitimately-selected indicator over a casing/spacing
  difference. **On a normalized match, the CANONICAL label from the signals tuple is
  returned — never the LLM's own echoed text** — so the displayed indicator name is
  always verbatim real data, staying within the "never fabricate" invariant even
  though the match itself is fuzzy. Entries with no normalized match are silently
  dropped, never causing the whole response to be rejected (an over-eager LLM
  inventing one bad indicator name shouldn't discard an otherwise-good narrative).
- If `scenario_narrative` is missing/empty after parsing, the whole call is
  treated as failed (returns `None`) — a narrative-less response has no value.

---

## Supabase table: `regime_scenario_cache`

```sql
create table if not exists public.regime_scenario_cache (
    scan_date            text        NOT NULL,
    scenario_narrative   text,
    indicator_watchlist  jsonb,
    blast_radius_snapshot jsonb      NOT NULL,
    regime_snapshot      jsonb       NOT NULL,
    cross_asset_snapshot jsonb       NOT NULL,
    created_at           timestamptz DEFAULT now(),
    PRIMARY KEY (scan_date)
);
alter table public.regime_scenario_cache enable row level security;
drop policy if exists "Allow all (service role)" on public.regime_scenario_cache;
create policy "Allow all (service role)" on public.regime_scenario_cache
    for all to service_role using (true) with check (true);
```

**DDL delivery:** manually applied once in Supabase (house pattern — no
`ensure_schema()`). Until the table exists, `load_regime_scenario_cache` returns
`None` and `save_regime_scenario_cache` no-ops — the expander still shows the
generate button, just can't persist across reruns.

**Intentional divergence from the `structural_scan_cache` precedent (Round 1 Opus
note, non-blocking):** `blast_radius_snapshot`/`regime_snapshot`/`cross_asset_snapshot`
are marked `NOT NULL` here, stricter than `structural_scan_cache`'s equivalent
columns. This is satisfiable — all three are always-populated dicts/lists at the
single call site that invokes `save_regime_scenario_cache()` (only reached after
`_rs_blast`/`_rs_regime`/`_rs_cross_asset` are already successfully computed earlier
in the same block) — so the stricter constraint is safe, not a bug; called out here
so the divergence from precedent is a documented choice, not an oversight.

**db.py functions** (mirror `save_structural_scan_cache`/`load_structural_scan_cache`
exactly — single row per `scan_date`, `_READONLY`-gated, fail-soft):

```python
def save_regime_scenario_cache(scan_date, scenario_narrative, indicator_watchlist,
                                blast_radius_snapshot, regime_snapshot, cross_asset_snapshot) -> None:
    """Upsert regime scenario row. Best-effort — never raises. Caller must only
    invoke this when scenario_narrative is non-None (a successful Haiku call) —
    never cache a failed/empty result."""

def load_regime_scenario_cache(scan_date: str) -> dict | None:
    """Return cached regime scenario result for scan_date or None. Never raises."""
```

---

## `app.py` wiring — one integration point

Inside the existing 🔥 Stress Testing tab (`_ra_tab_stress`, `app.py:9639-9912`),
**after** the existing "📊 Compare all scenarios at a glance" expander (ends ~line
9905) and **before** the closing methodology `st.info` (~line 9907-9911), add a new
expander:

```python
    with st.expander("🎯 Regime-Aware Adversarial Scenario (Beta)", expanded=False):
        st.caption(
            "Combines your portfolio's structural weak points with the current "
            "macro regime to name the single compound scenario most likely to "
            "hurt this specific book. The confidence score reflects how "
            "confident the regime detector is in its CURRENT read — it is not "
            "a forecast of anything happening in the next 90 days."
        )

        _rs_corr_df = st.session_state.get("_corr_df_cache")
        if _rs_corr_df is None or (hasattr(_rs_corr_df, "empty") and _rs_corr_df.empty):
            st.info("Correlation data isn't available this session — revisit 🏠 Home to compute it.")
        else:
            _rs_weights = dict(zip(port_df["Ticker"], port_df["Weight (%)"]))
            _rs_rb = portfolio_intelligence.risk_budget(held_data, _rs_weights)
            _rs_clusters = portfolio_intelligence.correlation_clusters(_rs_corr_df, _rs_weights)

            if not _rs_rb["positions"]:
                st.info("Not enough price history to compute a regime-aware scenario this session.")
            else:
                _rs_blast = structural_scanner.blast_radius(_rs_corr_df, _rs_rb["positions"])

                # Reuse the existing regime cache — never re-fetch if already
                # computed this session (Regime Fit / Economic Calendar may have
                # already populated it). CRITICAL (Round 1 Opus finding): the
                # fred_key derivation MUST be byte-for-byte identical to both
                # existing writer sites (Regime Fit at app.py:9487-9491,
                # Economic Calendar at app.py:22398-22401), including the
                # third fallback to the in-app-entered key
                # (`_ec_fred_key`, session state) — omitting it means a user
                # who added their FRED key via the Economic Calendar UI (the
                # documented way to add one, app.py:22403-22418) produces a
                # DIFFERENT bool(fred_key) here than at the other two sites,
                # missing the shared cache key entirely and silently
                # re-running detect_macro_regime_fred(None) — a degraded,
                # 0-confidence read shown on the SAME page as Regime Fit's
                # real read just above it. This is exactly the two-surfaces-
                # disagree failure mode a "decides, not informs" app must
                # never produce.
                _rs_fred_key = (
                    st.secrets.get("fred", {}).get("api_key")
                    or os.environ.get("FRED_API_KEY", "")
                    or st.session_state.get("_ec_fred_key", "")
                )
                _rs_regime_cache_key = f"_macro_regime_{_today_et()}_{bool(_rs_fred_key)}"
                if _rs_regime_cache_key not in st.session_state:
                    with st.spinner("Detecting macro regime…"):
                        try:
                            st.session_state[_rs_regime_cache_key] = detect_macro_regime_fred(
                                str(_rs_fred_key).strip() if _rs_fred_key else None
                            )
                        except Exception:
                            st.session_state[_rs_regime_cache_key] = {
                                "regime": "neutral", "label": "Data-Dependent",
                                "confidence": 0, "signals": [], "fed_trend": "unknown",
                                "cpi_yoy": None, "source": "fallback",
                            }
                _rs_regime = st.session_state[_rs_regime_cache_key]
                _rs_cross_asset = _cached_cross_asset()

                _rs_col1, _rs_col2 = st.columns(2)
                _rs_col1.metric("Current regime", _rs_regime.get("label", "—"))
                _rs_col2.metric("Regime confidence", f"{_rs_regime.get('confidence', 0)}/100")

                _rs_scan_date = str(_today_et())
                _rs_cached = db.load_regime_scenario_cache(_rs_scan_date)

                if _rs_cached and _rs_cached.get("scenario_narrative"):
                    st.markdown(_rs_cached["scenario_narrative"])
                    _rs_watchlist = _rs_cached.get("indicator_watchlist") or []
                    if _rs_watchlist:
                        st.markdown("**Early indicators to watch:**")
                        for _rs_ind in _rs_watchlist:
                            st.markdown(f"- {_rs_ind}")
                    st.caption(f"Computed {_rs_scan_date} ET.")
                else:
                    if st.button("🎯 Generate regime-aware scenario", key="_rs_gen_btn"):
                        with st.spinner("Synthesizing regime-aware scenario…"):
                            _rs_api_key = (st.secrets.get("anthropic") or {}).get("api_key", "")
                            _rs_factor_cache = st.session_state.get("_pi_factor_tilt_cache")
                            _rs_evidence = regime_stress.build_regime_scenario_inputs(
                                _rs_blast, _rs_clusters, _rs_regime, _rs_cross_asset, _rs_factor_cache
                            )
                            _rs_result = regime_stress.generate_regime_scenario(_rs_evidence, _rs_api_key)
                        if _rs_result and _rs_result.get("scenario_narrative"):
                            db.save_regime_scenario_cache(
                                scan_date=_rs_scan_date,
                                scenario_narrative=_rs_result["scenario_narrative"],
                                indicator_watchlist=_rs_result.get("indicator_watchlist", []),
                                blast_radius_snapshot=_rs_blast,
                                regime_snapshot=_rs_regime,
                                cross_asset_snapshot=_rs_cross_asset,
                            )
                            st.rerun()
                        else:
                            st.warning("Scenario generation failed — API unavailable or rate-limited. Try again.")
```

**Import additions:** `from stock_analyzer import regime_stress` (new module) and
`from stock_analyzer import portfolio_intelligence, structural_scanner` if not already
imported on this code path (both are already imported at the top of `app.py` for the
🧩 Intelligence page — confirm at build time whether a re-import is needed inside this
block or whether the top-level import already covers it).

---

## Cost model

| Item | Per portfolio/day | Per month |
|---|---|---|
| Blast radius + cluster recompute (pure Python) | $0 | $0 |
| Regime detection (FRED) | $0 extra — reuses existing once/day cache | $0 |
| Cross-asset signals | $0 extra — reuses existing 30-min cache | $0 |
| Narrative (1 Haiku call/day, button-gated) | ~$0.0005 | ~$0.01 |

Comparable to Structural Scanner's cost profile — cheapest tier of the roadmap's
features, since almost everything is reused rather than newly computed.

---

## What NOT to build in this plan

- **A fabricated 90-day probability.** Replaced with the regime detector's real
  confidence score, explicitly relabeled to avoid the forecast framing.
- **A new regime-calibrated shock magnitude.** The "estimated damage" figure reuses
  `blast_radius()`'s existing -20%/top-3 output verbatim — no new "how much worse in
  this regime" multiplier, which would itself be an invented number.
- **Modifying `stress_test.py`'s existing 9 named scenarios or the Custom Scenario
  slider.** Fully untouched — this feature is a new, separate expander.
- **A new selectbox entry mixed into the existing scenario picker.** Deliberately
  avoided (see Design Principle 8) to prevent forking the existing render pipeline.
- **Using `macro.py`'s `detect_macro_regime`.** Out of scope — `macro_calendar.py`'s
  FRED-based detector is the sole regime source for this feature.
- **A Home page banner or new nav entry.** Scoped entirely to the existing 🔗 Risk
  Analysis → 🔥 Stress Testing tab.

---

## Phased build

| Phase | Scope | Gate |
|---|---|---|
| **Phase 1** | `regime_stress.py` (new module) + `db.py` DDL/functions + new expander on the existing Stress Testing tab | Opus plan review → implement → Opus code review → ship |

No Phase 2 is proposed — this feature closes the entire scoped gap (narrative +
honest confidence + real-data indicator watchlist) in one pass, similar to how the
Information Asymmetry Detector needed only one phase.

---

## Open design questions — resolved in Round 1 Opus review

1. **Reusing `blast_radius()`'s fixed -20%/top-3 shock — RESOLVED: not confusing, a
   feature.** Verified the new expander's wiring renders only regime label/confidence
   metrics + narrative + watchlist — it does NOT re-render the per-shock metric cards
   🧩 Intelligence already shows. Feed `blast_radius` to the prompt/audit snapshot only,
   never a duplicate visible table. Identical computation means the figures referenced
   in the narrative can never disagree with 🧩 Intelligence's numbers.
2. **Session-state regime-cache reuse across pages — RESOLVED: safe, once the
   key-derivation fix is applied.** `st.session_state` is global, not page-scoped —
   two shipped call sites already read `_macro_regime_*` opportunistically from
   unrelated pages. This is sound specifically because the key-derivation blocking fix
   (Design Principle 3, above) makes the `bool(fred_key)` component consistent across
   all three read/write sites.
3. **`indicator_watchlist` validation — RESOLVED: normalized match, canonical
   label returned.** See the Validation section above — exact case-sensitive match was
   too brittle given real signal labels contain special characters/spacing; fixed to
   `.strip().casefold()` comparison while always displaying the canonical stored label,
   never the LLM's echo.

---

## Review log

| Round | Model | Verdict | Blocking findings |
|---|---|---|---|
| Round 1 | Claude Opus 4.8 | FIX-FIRST | 1 blocking (`_rs_fred_key` derivation omitted the third fallback source `st.session_state.get("_ec_fred_key", "")` that both existing regime-cache writer sites use — would produce a `bool(fred_key)` mismatch for users who added their FRED key via the in-app UI, causing this expander to silently miss the shared cache and show a degraded 0-confidence regime read on the same page where Regime Fit shows the real one) + 2 non-blocking (indicator_watchlist exact-match too brittle against real signal-label special characters, fixed to normalized match + canonical-label return; DDL NOT NULL divergence from precedent, confirmed safe and documented) — all resolved in v2 |
| Round 2 | Claude Opus 4.8 | FIX-FIRST | 1 blocking (the Round 1 normalized-matching fix was applied in the Validation section and Open Question #3 but stale "verbatim match" language survived in two other sections — Design Principle 6 and the `generate_regime_scenario` docstring — the exact "fixed in one place, stale elsewhere" failure mode caught in an earlier plan this session) — resolved in v3, all sections now consistently describe normalized matching + canonical-label return |
| Round 3 | Claude Opus 4.8 | SHIP | 0 blocking — all Round 2 fixes verified correct and consistent across all 4 relevant sections; document fully internally consistent; plan ready to implement |
