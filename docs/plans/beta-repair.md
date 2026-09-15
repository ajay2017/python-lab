# Beta card repair — lever comparison + candidate ranker

**Status: ALL 3 PHASES + THE PREREQUISITE SHIPPED, 2026-09-15.** Prerequisite `23252ff`
(stress-shock coverage), Phase 1 `e64aad3` (honest arithmetic), Phase 2 `1b8d3cd` (leverage
disclosure), Phase 3 `4061176` (candidate ranker replaces the old panel). 4 Opus reviewer
passes across the 3 feature commits (Phase 3 needed a confirming second pass after a
page-crash bug was found and fixed), all SHIP/0-blocking on the final pass for each commit.
`docs/requirements.md` F-274; `docs/architecture.md`'s module tree entry; memory
`project_beta_repair`. The separate "make Utilities recommendable" commit this plan's design
phase anticipated **also shipped the same day** (`a9a33ee`), including the manual Supabase
`sector_candidates` roster seed (owner-executed SQL merge + a no-op App Settings Save) — see
§5, updated below.

---

## 1. Why

The owner's real book sat at portfolio beta 1.88 against `PORTFOLIO_BETA_ELEVATED = 1.3`,
driven by three names at beta ~4.15/~3.36/~3.10. The beta recommendation on 🔗 Risk Analysis
was the app's answer to "what do I do about it" — and it was wrong twice over:

1. **The stated arithmetic didn't close.** "Adding 8-10% in a defensive sector... reaches
   target beta 1.3" was false by ~8x — a 10% cash add at a typical defensive beta (~0.60)
   only reaches ~1.76; reaching 1.3 that way needs ~83% of the book.
2. **The only candidate surface picked stocks in the wrong order.** "🔍 Find Defensive
   Recommendations" sorted `scanner_results["Score"]` (momentum only) descending, took the
   top 5, THEN fetched composite scores — so a 6th-momentum-ranked name with a good
   composite was structurally unreachable. Neither hardcoded "defensive" bucket was reliably
   low-beta (`"Consumer Staples & Retail"` is mostly discretionary retail — TJX/NKE/LULU/
   CMG/SBUX/ULTA/ROST — which is exactly why its own `SECTOR_ETF` is XRT, not XLP). The
   panel never showed a candidate's beta or the resulting portfolio beta at all, inside a
   card whose only purpose is beta.

The user's own framing of the problem — "give me 2 stocks from the 5 and tell me why" — was
close but not quite the right ask: the real gap was a missing **lever comparison**
(trim vs swap vs add), not just a missing ranking among a fixed candidate set. A `planner`
design pass (invoked mid-session) reframed the ask around that lever question and found the
swap lever — sell the high-beta position, buy a low-beta name with the proceeds — needs only
~16% of the book (~3x more capital-efficient than the cash add), and no swap model existed
anywhere in the codebase before this work.

---

## 2. Decisions made before any code

| Decision | Choice | Why |
|---|---|---|
| Scope | Full lever comparison + candidate-selection fix, not either alone | The lever question is what actually answers "how much"; the candidate fix alone would leave the false 83%-of-book claim standing |
| Sub-gate candidates | Rank + disclose, never recommend | `COMPOSITE_BUY` stays a hard floor for what counts as an ACT NOW call; a defensive trade below it is real but must never be issued as a buy signal (§2A: recommend nothing rather than wrongly) |
| Surface | Rework the beta card in place, not a new panel | 4 surfaces already issue trim/add opinions (Home sector-concentration, Signals ADD, Portfolio Overview drift, this card); a 5th would be a 3rd independent opinion on trim-what/add-what. The beta card already owns the beta target/bands/remedy, so it's beta's highest-priority surface (`feedback_single_surface_priority`) |
| Trim ordering | By beta contribution, composite always shown | The objective is beta relief, not conviction — but a high-conviction trim must never be silent |
| Volatility card | Left completely untouched | Beta arithmetic isn't the remedy for volatility; no beta-target claims belong there |
| `_GATE_FILES` | `beta_repair.py` added in Phase 2 | A recommendation-formula module from day one — gated mechanically rather than left to the prose rule alone |
| New constants | Zero across all 3 phases | Every threshold used (`PORTFOLIO_BETA_ELEVATED`, `COMPOSITE_HOLD`, `COMPOSITE_BUY`, `REDEPLOY_CORR_DIVERSIFIER_MAX`, `DIVERSIFY_SCAN_CAP`, `DIVERSIFY_DISPLAY_TOP`) already existed |
| Stress-shock gap | Fixed first, own commit | The new candidate ranker's derived sector set includes Communications/Industrials, which had zero stress-scenario coverage — shipping the ranker first would have let it recommend into a sector the adjacent Stress Testing tab modeled as risk-free |

---

## 3. Two real architectural findings, discovered mid-implementation

**`_reduce_calls` is downstream of `build_risk_advisor_recommendations`'s own output.**
`home_risk_synthesis.py` calls the risk advisor, whose recs are then passed into
`build_daily_briefing` as `risk_recs`; `_reduce_calls` is derived FROM that brief's own
`act_today`/`review_list` output via `reduce_call_items(...)`. So the producer cannot read
`_reduce_calls` to defer a trim recommendation without a circular dependency — the check has
to happen at render time in `app.py`, not inside `risk_advisor.py`. This also meant Phase 2's
leverage disclosure (which needs live account-cash access the producer doesn't have) and the
reduce-call cross-check naturally belong in the same place.

**A trade is equity-neutral at execution — basic double-entry accounting.** Selling $X of
stock converts $X of exposure into $X of cash/paid-down debit; buying does the reverse.
`net_capital` (equity) is unaffected by the trade itself, only by subsequent price moves. So
the direction of `leverage_side_effect` for a lever is determined entirely by whether
`gross_book` grows (an ADD, against a fixed equity — the ratio always worsens), shrinks (a
TRIM — the ratio always improves), or holds constant (a SWAP — also always improves, and more
so, since beta itself falls without shrinking exposure). This resolved what had been designed
as a "funding source" question (cash vs margin) into a mechanical fact about the lever KIND —
no funding-source parameter was needed in the final `leverage_side_effect` signature.

---

## 4. Phase log

**Prerequisite (`23252ff`).** `stress_test._SECTOR_SHOCKS` was missing Communications/
Enterprise Tech/Industrials/Utilities from all 6 sector-targeted scenarios — a position in
them modeled as losing NOTHING under any of them (`est_move = 0.0` fallthrough). 24 values,
each derived from real historical sector performance for the closest analog event, signed off
with the owner before writing. New coverage test asserts every `_DIVERSIFYING_SECTORS` member
has a value in every targeted scenario. Opus review: SHIP, 0 blocking (one improvement
applied — Enterprise Tech's 2020 COVID value was skewed mild, corrected from -22.0 to -28.0).

**Phase 1 (`e64aad3`).** New `stock_analyzer/beta_repair.py`: `expected_beta_after_trim`
generalizes the inline 50%-trim formula (bit-identical, pinned by a characterization test
written BEFORE the refactor); `expected_beta_after_swap`/`dollars_to_target_{add,swap,trim}`/
`aligned_beta` (candidate beta regressed on the SAME date window the book's own portfolio
beta uses, closing a lookback mismatch). `risk_advisor.py`'s beta rec replaces the false claim
with an honest trim-to-target dollar figure computed from data already available (no invented
candidate beta — that waits for Phase 3's real candidates) and extends the same-day-BUY
exclusion the sector-concentration rec already had. Opus review: SHIP, 0 blocking (two
doc-accuracy notes fixed same commit — a docstring falsely claimed the module calls
`portfolio.expected_beta_after_add`, and a referenced import-isolation test didn't exist yet;
both fixed).

**Phase 2 (`1b8d3cd`).** New `leverage_side_effect` (four states: not_levered/stale/called/
measured — never collapsing "no debt" into "can't measure"; for kind="trim", "called" does
NOT withhold the lever, since trimming is exactly right when margin-called). New structured
`beta_levers` payload on the rec dict so the render layer gets raw numbers, not a parsed
sentence. Rendered at RENDER time in `app.py` (see §3). `beta_repair.py` added to
`_GATE_FILES`, CLAUDE.md's enumeration updated same commit. Opus review: SHIP, 0 blocking (one
cosmetic line-reference fix in a comment).

**Phase 3 (`4061176`).** New `portfolio.beta_diversifying_sectors()` (derives
`_DIVERSIFYING_SECTORS ∩ {corr < REDEPLOY_CORR_DIVERSIFIER_MAX}`, reusing the existing
threshold rather than inventing one — Utilities joins automatically once/if added to
`_DIVERSIFYING_SECTORS`, no code change here). New `rank_defensive_candidates` in
`beta_repair.py`: classifies `reachable`/`meets_floor`/`actionable` per candidate, NEVER
filters a row (a caller discloses counts, never hides data), NEVER issues a buy call below
`COMPOSITE_BUY`. `app.py`'s beta card replaced with a two-stage bounded pipeline (window-
aligned beta for the whole pool via a cached price-history wrapper, zero `load_all` calls in
stage 1; composite/corr resolved only for the top `DIVERSIFY_SCAN_CAP` by beta, cache-first)
plus a display-only rate-sensitivity annotation. Volatility card untouched.

**First Opus review pass on Phase 3 found a blocking page-crash bug:**
`_ru_sector_candidates`/`_ru_discovery_universe` were referenced in the new code but only ever
assigned inside the Home page's own `if` branch — since Streamlit reruns the whole script
fresh per page and `if`/`elif` page branches are mutually exclusive, a Risk Analysis render
always hit a `NameError` whenever a beta rec-type card was present, crashing the entire page.
Fixed same commit by resolving both locally via `_resolve_ref_universe(...)`, the same pattern
every other consumer page already uses. A second, confirming Opus pass verified the fix (plus
a caption-wording cleanup for a case where a displayed candidate could be simultaneously
"shown" and counted as "excluded") and re-confirmed the first pass's other findings were
undisturbed: SHIP, 0 blocking.

---

## 5. Deliberately not done here (at the time this plan was first written)

- ~~**Utilities becomes a recommendable sector**~~ — **SHIPPED same day, commit `a9a33ee`**:
  `beta_diversifying_sectors()` and `rank_defensive_candidates` picked it up automatically
  with zero further code change once it landed in `_DIVERSIFYING_SECTORS`, exactly as
  anticipated. The Supabase `sector_candidates` roster (DUK/SO/D/AEP/EXC) — the one piece a
  coding session couldn't do itself — was seeded by the owner directly: a `jsonb ||` merge
  UPDATE against the live `reference_tables` row (verified by a read-back before and after),
  followed by a no-op Save through ⚙️ App Settings to re-stamp `payload_hash`/`as_of`
  correctly. Nothing left open on this item.
- **`risk_advisor.py:231`'s hardcoded `_tf = 0.50`** (the fixed "sell 50%" fraction) — a
  pre-existing Hard-Rule-#1 candidate found during this work, deliberately deferred to its
  own `constants.py` commit, unrelated to this feature's scope.
- **Two cosmetic Phase-3 review notes, both explicitly "no behavioural issue," left as-is:**
  a displayed candidate with `reachable is None` (unmeasured beta) gets no per-row
  explanatory caption (the metric already shows "β n/a"); `beta_repair._pos_float`'s name
  implies a sign filter it deliberately doesn't apply (documented in its own docstring).
