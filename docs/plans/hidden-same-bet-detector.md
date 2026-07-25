# D1 — Hidden Same-Bet Detector — Design Plan

**Date:** 2026-07-24
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** SHIP (revised after Opus design review — FIX-FIRST round resolved). Ready for
implementation.

> **Opus design review (round 1): FIX-FIRST** — 3 blocking findings, all fixed in this
> revision. **(1)** The original fabrication guard only protected against fake *tickers*
> in a cluster, not a fake *grouping* of real tickers — a spurious semantic pairing would
> pass ticker validation cleanly and then get stamped with the most alarming label. Fixed:
> the Haiku prompt must now **quote the specific thesis text** expressing the shared
> assumption for each cluster (grounding, mirroring P5's evidence-citation pattern), and the
> "🔴 Hidden" chip is downgraded to a non-alarming label — red in this app is earned by
> hard-data danger tiers, not a fuzzy LLM inference. **(2)** "Hidden" defaulted to true
> whenever price-correlation data was unavailable this session, which is fabricated
> confidence ("no numbers to check" ≠ "checked, and it's hidden"). Fixed: added a third
> state, `unverified`. **(3)** The section was nested inside `correlation_clusters()`'s own
> data-availability gates, so it would silently vanish (including the "<2 theses" message)
> whenever price data was thin — contradicting the stated degradation rule, which is about
> thesis coverage, not price data. Fixed: the section's gate is the thesis-corpus count
> only; price-correlation data is consumed for classification alone, and its absence
> produces `unverified`, not a vanished section. 3 non-blocking notes also folded in
> (partial-overlap sub-pair note, RLS added to the DDL, truncation length raised).

> **One-line spec:** A new "🧠 Hidden Same-Bet Detector" section on the existing 🧩
> Intelligence → 🧬 Structural Scan tab that has Haiku semantically cluster held
> positions' saved buy theses to find groups that secretly bet on the **same
> underlying assumption** (e.g. three names all implicitly long "AI capex keeps
> ripping"), then cross-references each cluster against P3's existing **price**
> correlation clusters (pure Python, no LLM) to classify it **unverified** (no price
> data to check against) / **possible** (sharing a thesis without sharing price
> correlation — nothing in the numbers would have caught it) / **confirmed** (also
> already price-correlated).

> **Roadmap context:** Priority 2 of [agentic-intelligence-roadmap-v2.md](agentic-intelligence-roadmap-v2.md)
> (paired with O1 as "one flagship per side" for Phase 2).

---

## Why this is the deepest genuine risk gap

P3's Structural Scanner (shipped 2026-07-24) finds positions that move together on
**price**. It cannot find positions that are secretly the **same bet wearing different
tickers** — three names in different sectors, low price correlation, that would all
take damage together the instant one shared assumption breaks (rates don't fall,
AI capex slows, a supply chain thesis reverses). That's concentration risk a
correlation matrix is structurally blind to. **Verified against HEAD (2026-07-24):** no
code anywhere reads thesis text across multiple positions at once —
`thesis_advisor.run_batch_review()` (`thesis_advisor.py:228-269`) loops positions and
calls `review_thesis()` on each independently; nothing ever compares one position's
thesis to another's. This is genuinely unbuilt ground, not a variant of something
that exists.

---

## The one risk that shapes the whole design: thesis coverage is optional

`user_thesis` is prompted at BUY as **"Investment thesis (optional — but
recommended)"** (`app.py:17576`), enforced nowhere, unbounded in length. Coverage is
whatever subset of currently-held tickers happened to get a thesis typed in — there is
no guarantee of coverage, and `cron_runner.py:595-597` already treats "zero held
positions have a thesis" as a no-op for a *different* feature (weekly thesis review).
**This plan must degrade explicitly, not assume coverage.** If fewer than 2 held
positions have a non-empty thesis, there is nothing to cluster — show a plain
"not enough saved theses yet to check for hidden overlaps" message, never force a
result.

---

## What already exists (reused, not rebuilt)

| Piece | Where | Status |
|---|---|---|
| Thesis storage | `user_thesis` column, `db.py:999-1003`; `load_trades()` returns it (`db.py:1006-1025`) | Shipped. Read-only reuse. |
| Price-correlation clusters | `portfolio_intelligence.correlation_clusters(corr_df, weights, threshold, danger_threshold) -> list[dict]` (`portfolio_intelligence.py:30-59`), returns `{"tickers": [...], "size", "avg_internal_corr", "combined_weight_pct", "tier"}` | Shipped. **Reused verbatim** as the cross-reference basis for the possible/confirmed/unverified classification — zero new correlation math. |
| Structural Scan tab | 🧩 Intelligence, 4th tab (`app.py:10153`, tab body `10382-10465`), existing button-gated, day-cached Haiku narrative (`structural_scanner.build_narrative_inputs`/`generate_structural_narrative`, `db.load_structural_scan_cache`/`save_structural_scan_cache`) | Shipped. This plan adds a **new, separately button-gated** section on the same tab — not folded into the existing narrative call (different inputs, different failure mode; keeps each concern independently reviewable, same separation P3 kept between pure-Python Blast Radius and its LLM narrative). |
| Sector data (for cross-reference context only) | `port_df["Sector"]` | Shipped. |

## What's genuinely new

1. **Thesis-corpus assembly** (pure Python): for every held ticker with a non-empty
   `user_thesis`, gather `{ticker, sector, thesis_text}`. Requires ≥2 qualifying
   tickers to proceed.
2. **One Haiku call/day** (button-gated, day-cached): given the corpus, identify
   groups of **2+** tickers whose theses rest on the same underlying assumption, each
   with a short plain-English label for the shared assumption **plus the specific
   quoted thesis span from each ticker's own text that expresses it** (grounding —
   mirrors P5's "cite only supplied evidence" pattern; a cluster whose cited quotes
   don't actually appear in the corresponding ticker's thesis text is dropped as
   unfounded). **Never forces a grouping** — "no shared assumption found" is a valid,
   expected output when theses are genuinely independent.
3. **Three-state cross-reference** (pure Python, zero LLM, zero fabrication risk): for
   each Haiku-identified cluster's ticker set, check whether an *existing*
   `correlation_clusters()` group already contains all of those tickers — **but only
   when price-correlation data is actually available this session.**
   - **Price-correlation data unavailable this session → "⚪ Unverified"** — we had no
     numbers to check against; this is honestly "don't know," never "hidden."
   - **Data available, no existing price-correlation cluster contains them → "🟠
     Possible shared assumption — review"** — a semantic-only finding; framed as a
     prompt to look closer, not a data-confirmed danger tier (red is reserved
     elsewhere in this app for hard-data-driven severity, e.g. Trade Review's Act
     Today / deterioration tiers — this is a fuzzier LLM inference and must not
     borrow that visual authority).
   - **Data available, an existing price-correlation cluster already contains all of
     them → "🟡 Confirmed — also visible in Correlation Clusters"** — corroboration
     from a second, independent signal; shown de-emphasized, not the headline.
   - Rendered alongside each verdict: any sub-pairs within the thesis group that ARE
     already price-correlated (even if the full group isn't a strict superset match),
     so a partial overlap isn't silently overstated as fully "possible/hidden."
4. **New cache table** `thesis_cluster_cache` (portfolio-wide, one row per `scan_date`
   — mirrors `structural_scan_cache`'s exact pattern, including "never cache a
   failed/empty result").
5. **A render section** on the existing 🧬 Structural Scan tab — a sibling section,
   not nested inside the existing Blast Radius / Structural Narrative's own
   data-availability gate (see design principle 8 / render spec below).

**Explicitly NOT built:** no change to `correlation_clusters()` itself, no new
correlation math, no scoring/gate of any kind, no forced clustering when theses are
genuinely unrelated, no cross-ticker thesis comparison outside this one clustering
call (Thesis Red Team / thesis_advisor stay single-position, unchanged).

---

## Design principles (non-negotiable)

1. **Strictly additive.** Nothing here touches the composite score, a gate, or the
   correlation-cluster tiers themselves. Pure diagnostic annotation.
2. **Graceful degradation on thin coverage.** <2 thesis-bearing positions → plain
   message, no button, no LLM call. This is the expected common case for newer
   portfolios, not an error state.
3. **Never fabricates a connection.** The Haiku prompt must explicitly permit "no
   shared assumption" as an answer, the same way P5's regime-scenario prompt permits
   "no coherent scenario emerges." Two coincidentally-similar-sounding theses that
   aren't really the same bet should not be forced together.
4. **Ticker validation, canonical labels — protects against fake tickers only.** Any
   ticker the LLM returns must be normalized-matched (`.strip().casefold()`) against
   the actual supplied corpus and rendered using the canonical stored ticker — never
   the LLM's echoed text. A cluster containing a ticker not in the supplied corpus is
   dropped entirely.
5. **Evidence-span citation — protects against fake groupings of real tickers.**
   Ticker validation alone doesn't stop the model from spuriously pairing two
   *genuinely held* tickers that don't actually share an assumption. Per Opus review,
   this is the sharper risk (semantic clustering over free prose is fuzzier than P5's
   "pick from a closed list of named indicators"). The prompt requires a quoted span
   from each ticker's own thesis text as evidence; a cluster whose quotes don't
   verifiably appear in that ticker's stored text is dropped.
6. **Button-gated, day-cached.** Same `st.tabs()`-executes-every-render lesson from
   P3/P5 — this is a *second*, independently gated button on the same tab, not tied to
   the existing narrative's button state.
7. **Never invents a classification, including "unverified."** The
   hidden/possible/confirmed/unverified state is a pure-Python check against
   `correlation_clusters()`'s real output (or its absence) — never an LLM judgment
   call. Absence of price-correlation data must yield `unverified`, never a false
   "possible/hidden" default (this was blocking finding #2 in review).
8. **The section's gate is thesis coverage, not price data.** Per review finding #3,
   the whole section — including the "<2 theses" degradation message — must render
   independent of whether `correlation_clusters()`'s inputs (`_corr_df_cache`,
   `risk_budget()`'s positions) are available this session. Price-correlation data is
   consumed only for the classification step; its absence changes the *label*
   (`unverified`), never removes the *section*.

---

## Spec — Haiku clustering call

**Input corpus** (per qualifying ticker): `ticker`, `sector`, `thesis_text` (as saved,
unbounded length — no `THESIS_MAX_LEN` constant exists today; truncate defensively at
call time to keep prompt size bounded, ~1500 chars per thesis — Haiku's context is
large and the operative sentence may sit late in a long thesis, per review finding #6.
A presentation truncation only, not a stored-data change; if any thesis was truncated
for the scan, note it in the render).

**Prompt task:** "Given these N positions' stated investment theses, identify any
groups of 2 or more that rest on the same underlying market/macro/sector assumption —
even if their sectors differ. For each group: name the shared assumption in one plain
sentence, AND quote the specific span from each member ticker's own thesis text that
expresses it. If no group shares a genuine underlying assumption, say so plainly —
do not force a connection between theses that merely sound superficially similar."

**Output validation (two layers, per review finding #1):**
1. Each returned ticker must normalized-match a ticker in the input corpus (drop any
   that don't); a group with fewer than 2 valid tickers after this step is discarded.
2. Each returned quoted span must verifiably appear (case-insensitive substring
   match) within that ticker's own stored `thesis_text`. A cluster member whose quote
   doesn't verify is dropped from the group; if the group falls below 2 verified
   members, the whole group is discarded. This is the fabrication guard for the
   *grouping itself*, not just the ticker identity.

**Cross-reference (pure Python, after validation) — three states, per review finding #2:**
- If `correlation_clusters()`'s inputs are unavailable this session → every validated
  group is labeled `unverified` (no numbers were checked).
- Else, for each validated group, check whether any `correlation_clusters()` group's
  `tickers` is a superset of this group's tickers → `confirmed` if yes, `possible` if
  no. Also compute and attach any already-price-correlated sub-pairs within the group
  (review finding #4) so a partial overlap renders honestly instead of as a blanket
  "possible."

## Spec — cache table (mirrors `structural_scan_cache`)

```sql
create table if not exists public.thesis_cluster_cache (
    scan_date      text        NOT NULL,
    clusters       jsonb        NOT NULL,  -- [{tickers, shared_assumption, quotes, state, corr_subpairs}, ...]
    thesis_snapshot jsonb       NOT NULL,  -- corpus used, for auditability
    created_at     timestamptz DEFAULT now(),
    PRIMARY KEY (scan_date)
);
alter table public.thesis_cluster_cache enable row level security;
drop policy if exists "Allow all (service role)" on public.thesis_cluster_cache;
create policy "Allow all (service role)" on public.thesis_cluster_cache
    for all to service_role using (true) with check (true);
```

*(RLS block added per review finding #5 — every table in this codebase gets it,
`structural_scan_cache` included; the plan's first draft omitted it.)*

`state` is one of `unverified` / `confirmed` / `possible` (see cross-reference spec
above) — never a bare boolean `hidden`, so the third state can't be silently coerced
into a binary.

Never cache a failed/empty LLM call. An explicit "no shared assumption found" *is* a
valid, cacheable result (distinct from a failure) — mirrors P5's honest-null handling.

## Spec — render (placement fixed per review finding #3)

A new section on 🧬 Structural Scan, gated **only** on the thesis-corpus count —
**independent of** `correlation_clusters()`'s own data-availability gates
(`_corr_df_cache`, `risk_budget()`'s positions). It must not be nested inside the
existing narrative's `else` block, or it silently disappears whenever price data is
thin, which is the wrong failure mode for a thesis-only signal (this was the exact bug
in the original draft).

- Section header "🧠 Hidden Same-Bet Detector" + one-line explainer.
- If <2 thesis-bearing held positions: plain caption ("not enough saved theses yet to
  check for hidden overlaps"), no button. **Always renders**, regardless of price data
  state.
- Else: button "🧠 Check for hidden shared bets" (or cached result if same-day row
  exists).
- Each surfaced cluster: tickers, the plain-English shared assumption, the quoted
  evidence span per ticker (collapsed under an expander — keeps the card scannable),
  and its state chip:
  - **⚪ Unverified** (no price-correlation data this session to check against)
  - **🟠 Possible shared assumption — review** (checked; not already price-correlated)
  - **🟡 Confirmed — also in Correlation Clusters** (checked; already price-correlated),
    rendered de-emphasized
  - Any already-price-correlated sub-pairs noted inline under a `possible` card.
- If Haiku found no shared assumption: plain "No shared assumption found in your saved
  theses today" message — a legitimate, calm result, not an error.
- If a thesis was truncated for the scan (>1500 chars), a small caption noting it.

---

## Resolved design questions (Opus rulings)

1. **Single Haiku call: sufficient, but only with the evidence-span citation from
   design principle 5.** A second clustering pass is not the fix for hallucination
   risk — grounding the model's output against the source text is. Accepted at
   realistic portfolio sizes (~2-8 thesis-bearing positions).
2. **Truncation: module-level, not `constants.py`** (prompt-sizing, not a decision
   threshold — matches `trade_review.py`'s `_ROLLING_WINDOW` precedent). Raised to
   ~1500 chars per review finding #6 (500 was needlessly aggressive and risked cutting
   the operative sentence in a long thesis).
3. **"Confirmed" clusters: show, de-emphasized.** Accepted — a second, independent
   signal (semantic + statistical) confirming a suspected concentration is a genuine
   corroboration, not noise.
4. **Factor Tilt cross-reference: out of scope for Phase 1.** Accepted — Factor
   Tilt's own documented caveats (`portfolio_intelligence.py:256-266`, "directional
   and noisy") make it too weak a second axis; revisit only if Phase 1 proves
   valuable.

## Non-goals

- Does not modify `correlation_clusters()`, `factor_tilt()`, or any existing
  Structural Scan content.
- Does not gate, score, or suppress anything.
- Does not compare theses outside this one clustering call (Thesis Red Team stays
  single-position).
- Does not require or prompt for thesis text at BUY — coverage stays optional, exactly
  as today.
