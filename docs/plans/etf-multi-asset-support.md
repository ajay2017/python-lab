# Multi-asset-type support (ETF first) — architecture review + phased plan

**Status: THE ETF/MULTI-ASSET ARCHITECTURE INITIATIVE, AS ORIGINALLY SCOPED,
IS COMPLETE — Phases 0 through 4 ALL SHIPPED, all 2026-09-27 (F-279 through
F-284). Phase 3a's `etf_lookthrough_cache` DDL was APPLIED by the owner in
Supabase 2026-09-27** (not independently verified against a live query in
this session — the owner's own report is the source for "applied," same
posture as the Phase 1 DDL confirmation). **One thing remains
deliberately unscoped, by explicit owner choice, no trigger date: whether
ETFs should ever become eligible as NEW Grow Today buy candidates** (today
only existing/held ETFs get a real verdict) — this is a separate, bigger
policy question needing its own discovery-universe design, not a natural
extension of any phase above. See the final "Where the initiative stands
now" section at the bottom of this doc for the complete picture.
Opus `planner` architecture review, then a second `planner` design pass per phase,
`implementer` built each, Opus `reviewer` before every commit (Phase 0: FIX-FIRST/1
blocking → fixed same session → SHIP/0 blocking; Phase 1: SHIP/0 blocking, first
pass). **The Phase 1 DDL was applied by the owner directly in Supabase 2026-09-27**
(same day as ship) — `asset_type`/`kinds` now exist on all four tables, closing the
reviewer's one non-blocking note (a broker-confirmed trade write no longer strips
`broker_txn_id`/`idempotency_key` alongside `asset_type`). Phase 3b (top-holdings
overlap detector) and Phase 4 below are DESIGNED, NOT STARTED — each needs its own
fresh `planner`/`reviewer` pass and, where noted, explicit owner sign-off on new
policy constants before any code is written. This doc is the design-of-record; do
not start a later phase from memory without reconfirming this file still matches
the code.

---

## 1. Why

DRISHTA has so far been built exclusively around individual US equities. The owner
is considering adding ETF support (starting with index ETFs, e.g. S&P 500 trackers)
and asked for an architecture readiness review before any implementation: is the
foundation modular enough to add ETFs (and later other asset types) without
repeated redesign, or would a naive bolt-on break the app's core "the app decides,
it does not inform" posture?

An Opus `planner` pass reviewed the data layer, scoring engine, portfolio
construction, broker sync, and nav with file:line citations (no fabricated provider
capabilities). Full findings below; this file is the design-of-record for anything
that follows.

## 2. Readiness verdict

**The plumbing is ready; the decision engine is not.** The instrument-key layer
(`ticker` as a bare string, no asset-type discriminator anywhere in the schema),
price/history fetch, `technicals.py`, `risk.py` (beta/ATR/correlation/sizing), and
`portfolio.py`'s weight/stop construction are already asset-agnostic and proven on
ETF tickers today (SPY/TLT/VIX/sector-ETF benchmarks are fetched constantly). But
the composite scoring engine and the fundamentals/valuation gate are structurally
welded to single-company equities:

- The composite is `technical 0.25 + business_quality 0.35 + valuation 0.30 +
  sentiment 0.10` (`constants.py` `COMPOSITE_WEIGHTS`). Business quality
  (`fundamentals.py::business_quality_score`) and valuation
  (`valuation.py::valuation_score`) both score fields that don't exist for a fund
  (revenue growth, earnings growth, margins, debt/equity, forward P/E, FCF yield) —
  **65% of the composite is structurally unmeasurable for an ETF.**
- `business_quality_score`'s own docstring: "When NONE are present... returns a
  fabricated neutral 50 — so consumers gate on the count, not the raw score."
  `count_core_metrics(financials) >= FUNDAMENTALS_GATE_MIN_METRICS` is 0 for an ETF
  → `bq_available=False`, `val_available=False`. **This part already works
  correctly** — the availability flags correctly identify an ETF as "can't measure
  this."
- Two different consumers of those flags disagree, and that disagreement is the
  live bug (§3):
  - `stock_analyzer/quick_research.py:211-227` (the 🔍 Analysis single-ticker page)
    DOES check both flags and renders "❔ Verdict withheld — fundamentals couldn't
    be sourced" — correct behavior, wrong wording for an ETF (it implies a data
    outage, not "this is a fund, not a company").
  - `stock_analyzer/portfolio.py::build_portfolio_df` (`portfolio.py:534-535`) does
    **not** check either flag — it sets `"Score": r["total"]` and
    `"Signal": f"{r['rec']['icon']} {r['rec']['label']}"` directly from the raw
    (fabricated-neutral-tainted) composite. `port_df["Score"]`/`"Signal"` is the
    shared spine read by Home, Watchlist, Summary Top Positions, `risk_advisor`,
    `exit_advisor`, the Rebalancer, and every gate that keys off "the composite for
    a held ticker."
- Sector taxonomy (`TICKER_SECTORS`, single-sector-per-ticker) is semantically
  wrong for a fund — SPY has no single sector, falls into "Other," which the
  sector-concentration gate (`SECTOR_CEILING`) explicitly excludes. This hides both
  the ETF's own diversification value and its real overlap with single-name
  holdings.
- `broker_sync.py` already defines `_EQUITY_INSTRUMENT_KINDS = {"stock", "etf",
  "adr"}` (`broker_sync.py:92`) and treats all three identically for drift
  purposes — `_position_ticker` reads SnapTrade's `instrument.kind` only to decide
  whether to keep the row, then **discards** the kind. So an ETF synced from the
  broker enters the book exactly like a stock, with no record anywhere that it's a
  fund.

## 3. The live risk this plan's Phase 0 closes

Because `build_portfolio_df` never consults `fundamentals_available`/
`val_available`, **any ETF held in the book today is already being scored,
displayed, and acted on as if the fabricated-neutral composite were a real
measurement** — a wrong call dressed as a real one, on every surface that reads
`port_df["Score"]`/`"Signal"` (Home, Watchlist, Summary, `risk_advisor`,
`exit_advisor`, Rebalancer). This is not a future risk to design around; it is live
today for any ETF position synced via SnapTrade or logged as a manual trade.

Phase 0 (this session) closes it with the minimum change that stops the silent
mis-scoring without inventing new asset-type infrastructure or touching any scoring
formula: see `docs/plans/etf-multi-asset-support.md#4-phase-0` below for the exact
design once the `planner` pass for it lands (recorded in the same section after
implementation).

## 4. Reusable vs. asset-specific, component by component

| Component | Verdict |
|---|---|
| `data.fetch_price_history` / provider `bundle`/`price_history` | Reuse as-is |
| `technicals.py` | Reuse as-is (pure price/volume) |
| `risk.py` (ATR stop, beta, Sharpe, correlation, sizing) | Reuse as-is (pure returns math) |
| `portfolio.build_portfolio_df` weight/stop construction | Reuse as-is (asset-agnostic once Score/Signal are gated — see Phase 0) |
| broker sync drift/cash/income-event logic | Reuse as-is |
| Nav / page dispatch | Reuse, type-aware panels only |
| `data.fetch_financials_from_info` | Needs an ETF-facts sibling |
| Composite scoring + fundamentals/valuation gate | Needs an ETF-specific strategy + gate |
| Sector taxonomy / concentration gate | Needs look-through or an explicit "Broad Market" bucket |
| `business_quality_score` / `valuation_score` internals | Stay stock-only — never route an ETF through these with fabricated inputs |
| Earnings-proximity / analyst-revision alerts | Stay stock-only |
| `reference_data.py` (F-262 pattern) | Reuse as the template for an ETF metadata registry |

## 5. What ETF intelligence actually needs (decision value, not completeness)

- **High value:** expense ratio (direct, permanent drag on return — clean decidable
  call), look-through exposure (turns an ETF from an opaque "Other" blob into
  something the concentration/overlap engine can use — the single biggest lever),
  index/benchmark identity + tracking difference, distribution yield.
- **Medium value:** AUM/liquidity (matters for thin/niche funds, not SPY-class),
  premium/discount to NAV (matters for bond/international/leveraged ETFs).
- **Skip:** fund flows (institutional noise, not a personal-portfolio decision
  input), deep tracking-error statistics (one tracking-difference number already
  captures the decision).

## 6. Data readiness

Price/technical/risk data: ready now, proven daily on ETF tickers already in use
(SPY/TLT/VIX/sector ETFs). ETF-specific fields (expense ratio, AUM, yield,
category) are likely exposed via yfinance `.info`/FMP but **currently uncaptured**
by `fetch_financials_from_info` and **unverified against live ETF tickers** — must
be empirically probed before Phase 2 design commits to a field list, never assumed.
Look-through/constituent holdings: **unproven** — nothing in the current provider
stack fetches this; likely needs a dedicated source. Tracking error, NAV
premium/discount, fund flows: no source in the current stack at all.

## 7. Proposed architecture

**Not** a full `Asset → Stock | ETF | Future` class hierarchy — `bundle_loader`
returns plain dicts read by key throughout the app, and a class refactor would
ripple through the 44k-line, untested `app.py` for no decision-quality gain on a
solo app adding one type. **Not** `if is_etf:` branches sprinkled through the
scoring code either — that reintroduces fabricated-neutral scores or a permanent
withhold, both breaking "the app decides."

**Recommended: a thin `asset_type` discriminator + a per-type scoring/gate
strategy, living entirely in `stock_analyzer/`:**

1. Nullable `asset_type` column on `holdings`/`trades`/`recommendations`, NULL→
   `"stock"` backward-compat (same pattern as existing legacy-column backfills in
   `db.load_trades()`). `db.py` change → mandatory Opus review.
2. ETF metadata registry via the existing F-262 `reference_data.py` pattern
   (owner-editable, no code deploy needed to add a new ETF).
3. Split `fetch_financials_from_info` into stock vs. ETF variants; same bundle
   dict shape, new keys — existing readers keep working.
4. New sibling module `stock_analyzer/etf_scoring.py` (deliberately a NEW module,
   not grown inside `scoring.py`/`fundamentals.py`, so it doesn't inherit their
   `_GATE_FILES` blast radius until it's actually reviewed on its own merits) with
   its own composite + its own availability gate — decidable on expense ratio +
   benchmark identity, never on EPS/P&E.
5. Make the withhold/new-pick gate (`daily_briefing.py`) and `build_portfolio_df`'s
   Score/Signal assignment asset-type-aware.
6. Sector layer: look-through distribution, or a v1 explicit "Broad Market" bucket
   the concentration gate understands (instead of "Other").
7. Nav: no new top-level pages for v1 — type-aware panels inside existing
   Portfolio/Analysis/Watchlist pages.

## 8. Future extensibility

The discriminator + strategy design generalizes: mutual funds reuse the ETF
strategy almost verbatim (expense ratio/NAV/category, minus intraday
premium/discount); bonds get a third strategy keyed on yield/duration/credit
instead of the composite; cash becomes a portfolio-weight element that's never
scored. A rigid two-way stock/ETF branch would foreclose all three — the one
`asset_type → strategy` indirection does not.

## 9. What to refactor first vs. explicitly leave alone

**Refactor (Phase 0/1):** `build_portfolio_df`'s Score/Signal assignment (§3);
split `fetch_financials_from_info`; add the `asset_type` column + backfill.

**Explicitly do NOT touch:** `technicals.py`, `risk.py`, the stop ladder
(`portfolio.py` stop/ratchet math), `build_portfolio_df`'s weight math,
`business_quality_score`/`valuation_score` internals (route ETFs around them,
never through them), `COMPOSITE_WEIGHTS`/`COMPOSITE_WEIGHTS_VERSION` (ETFs get
their own weights, not a repurposed equity mix).

## 10. Risks of extending as-is (why Phase 0 can't wait)

1. Silent mis-scoring, happening today (§3).
2. Permanent WITHHELD verdict on the Analysis page with no explanation that it's
   by-design (an "inform" dead zone inside a "decide" app) once Phase 0's wording
   fix lands, until Phase 2 ships a real ETF verdict.
3. Concentration blindness / false diversification via the "Other" sector bucket.
4. A fabricated-neutral composite could still cross `COMPOSITE_BUY` on technicals
   alone, manufacturing a buy call with no real fundamental basis.
5. Scattered `if is_etf` branches in the untested `app.py` would be unverifiable
   by construction — decision logic belongs in `stock_analyzer/`.

## 11. Recommended sequence

- **Phase 0 (this session):** stop the silent mis-scoring — see §3/§4 once
  implemented, status updated below.
- **Phase 1:** `asset_type` column + backfill; ETF metadata registry; split the
  financials extractor.
- **Phase 2 (decision-bearing, needs fresh `planner` + `reviewer`, owner sign-off
  on new constants):** `etf_scoring.py` composite + ETF availability gate; wire the
  gate branch into `daily_briefing.py` and `build_portfolio_df`.
- **Phase 3:** ETF look-through into the sector/concentration engine, gated on the
  §6 data-readiness probe actually succeeding.
- **Phase 4:** type-aware UI panels on Portfolio/Analysis/Watchlist; suppress
  earnings/analyst-revision cards for funds.

## 12. Final answer

**Targeted architectural changes first — not a bolt-on, not a larger domain
restructure.** The spine (data/technicals/risk/portfolio construction) is
genuinely asset-agnostic already; a full polymorphic rewrite would solve a problem
the app doesn't have. But 65% of the composite is unmeasurable for a fund and one
consumer of the availability flags (`build_portfolio_df`) doesn't check them at
all — a bolt-on would either fabricate a score or withhold forever, both breaking
the core operating principle. The right-sized fix is the thin `asset_type`
discriminator + per-type strategy pattern above, built in the phased order in §11.

**Policy decisions that remain the owner's, not the engine's, to make (Phase 2+):**
ETF composite weights (new constant + its own `weights_version` lineage per
Definition-of-Done #8), expense-ratio decision bands by category, whether
AUM/liquidity is a hard gate or a soft awareness flag, whether v1 sector treatment
is full look-through or a "Broad Market" bucket (changes the concentration gate's
denominator), and confirming ETFs get an actionable call rather than an info
panel.

---

## Phase 0 — implementation record

**Shipped 2026-09-27 as F-279** (`docs/requirements.md` F-279). Commit follows this
doc update.

**Design chain:** Opus `planner` scoped the exact fix (traced every consumer of
`port_df["Score"]`/`["Signal"]` for blast radius before any code was written) →
`implementer` (Sonnet) built it → Opus `reviewer`, two rounds.

**What shipped:**
- `stock_analyzer/portfolio.py::build_portfolio_df` now checks
  `fundamentals_available`/`val_available` (same fail-open `.get(..., True)`
  pattern as `quick_research.py`/`daily_briefing.py`) before trusting
  `r["total"]`/`r["rec"]`. Withheld rows get `Signal = WITHHELD_SIGNAL`
  (`"❔ Verdict Withheld"`), `Score = None` (coerced to real float64 NaN via
  `pd.to_numeric`, never a surviving object-dtype `None` on an all-withheld
  book), and a new always-present `"Score Available"` boolean column. A new
  `df.attrs["score_withheld"]` list mirrors the existing `dropped_holdings`
  attrs pattern.
- `stock_analyzer/util.py::score_withheld_banner_text()` (mirrors
  `dropped_holdings_banner_text`) + two `app.py` render sites (`st.info`, calm
  tone — this is not a warning, price/weight/stops/risk still apply normally).
- Three companion guards so downstream consumers don't misread the new
  withheld state instead of the old fabricated-50: `daily_briefing.py`'s
  weak-large-position card (closes a **confirmed live false-fire** — the
  fabricated neutral 50 was already below `WEAK_CONVICTION_SCORE`, so a held
  ETF at ≥`LARGE_POSITION_WEIGHT_PCT` weight was already triggering a false
  "weak conviction, trim" Act Today card), `rebalancer.py`'s TRIM/ADD rationale
  (drops the score-driven urgency leg and any "Sell zone"/numeric-score wording
  for a withheld row — the trim/add action itself, share count included, is
  unchanged), and `portfolio.diversification_recommendations`'s REDUCE/PAIR_RISK
  cards (found by the reviewer's first pass, not the original planner spec —
  see below).
- No `constants.py` entry, no DB/schema change, no scoring-formula change.
  `exit_advisor.py` untouched (correctly still fires technical deterioration
  signals on ETFs — momentum/entry timing/stops are still measurable even when
  the composite isn't).

**Review = Opus reviewer (Opus 4.8): SHIP, 0 blocking; first pass was FIX-FIRST,
1 blocking** — `diversification_recommendations`'s REDUCE (weakest-candidate
sort) and PAIR_RISK (weaker/stronger determination) both read `port_df["Score"]`
directly and were missed by the original `planner` spec's blast-radius trace.
Fixed in the same session: REDUCE now filters out score-withheld rows before
ranking "weakest" trim candidates (an all-withheld sector still gets its REDUCE
rec, just with an empty candidate list); PAIR_RISK now skips a correlated pair
entirely — rather than inventing an untested "always trim the unmeasured side"
heuristic — whenever either side is withheld, per this app's own "recommend
nothing rather than recommend wrongly" posture. Confirming pass verified the fix
against the live diff (not the summary) and re-confirmed nothing else in the
original blast-radius trace was affected.

**Tests:** new coverage in `tests/test_portfolio.py`, `test_daily_briefing.py`,
`test_rebalancer.py`, `test_util.py` for every boundary above (withheld vs.
available, both-flags-required, fail-open on a legacy bundle/frame missing the
new keys, the None→NaN dtype coercion, the Signal-substring invariant, the
REDUCE/PAIR_RISK withheld-exclusion). Full suite: **6241 passed**. Antipattern
gate: clean (baseline regenerated for 2 new accepted `... or []` instances of the
already-accepted `dropped_holdings`-class idiom, applied to the new
`score_withheld` attrs list — plus 1 stale entry removed for a `sentiment.py`
pattern already fixed by a prior commit, `aa83f3c`, whose baseline update had
been missed at the time). Constants-doc gate: clean, no new constant.

**What this does NOT fix (deliberately, Phase 1+ territory):** an ETF still gets
no real ETF-specific verdict — it's now honestly withheld instead of wrongly
scored, which is the entire scope of this phase. Phase 2 is what gives it an
actual decidable ETF composite.

## Phase 1 — implementation record

**Shipped 2026-09-27 as F-280** (`docs/requirements.md` F-280).

**Design chain:** Opus `planner` scoped Phase 1 exactly (reading `reference_data.py`,
`db.py`, `broker_sync.py`, `bundle_loader.py`, `data.py` first) → `implementer`
(Sonnet) built all 6 chunks → Opus `reviewer`, SHIP/0 blocking on the first pass.

**Two corrections the planner found vs. this doc's original §7/§11 guess** (worth
keeping so a future session doesn't re-derive them): (1) `holdings` is a DERIVED
artifact rebuilt from `trades` via `recalculate_from_trades()` — there is no
independent write path to stamp it directly, so Phase 1 deliberately does NOT touch
`recalculate_from_trades`; the persisted `asset_type` column on `holdings` exists
for schema symmetry but has no Phase-1 writer of its own yet. (2) SnapTrade's
ground-truth `instrument.kind` lives on the POSITIONS payload, which is a different
API call than the ACTIVITIES payload that builds `trades` — so capturing the
broker's real classification required a deliberate bridge (`resolve_trade_asset_type`
at pending-import promotion), not just "read the kind and save it."

**What shipped:**
- New `stock_analyzer/asset_type.py` — see `docs/architecture.md`'s module section.
- `stock_analyzer/data.py::fetch_etf_facts_from_info()` — new sibling of the
  untouched `fetch_financials_from_info`; field list verified against a LIVE probe
  of real SPY/TLT `.info` output (not assumed from memory) — `netExpenseRatio` is
  the real field (`expenseRatio`/`beta` are always `None` for a fund), and
  `netExpenseRatio` is PERCENT-unit while `yield`/`trailingAnnualDividendYield` are
  FRACTION-unit — do not compare them directly without converting.
- `stock_analyzer/bundle_loader.py::load_bundle` — purely additive `quote_type`/
  `asset_type`/`etf_facts` keys; a regression test independently recomputes a stock
  bundle's `total`/`rec`/`bq_available`/`val_available` and asserts byte-identical
  output to before this change.
- `stock_analyzer/db.py` — nullable `asset_type` on `holdings`/`trades`/
  `recommendations` (NULL→"stock" backfilled at read; every writer degrades
  gracefully pre-DDL via the existing optional-column drop-and-retry pattern), and
  a nullable `kinds` jsonb column on `broker_position_snapshot` with the same
  pre-DDL resilience on both read and write.
- `stock_analyzer/broker_sync.py::position_kinds()` (sibling of `normalize_positions`,
  same offline-sentinel contract: `None`→`None`, `[]`→`{}` real-empty) and
  `::resolve_trade_asset_type()` (promotion-time precedence: broker snapshot
  `kinds` → an in-scope live bundle's `asset_type` → `"stock"` fail-safe — every
  path routes through `asset_type.normalize`, so an unrecognized input can never
  resolve to `"etf"` by accident).
- `cron_runner.py` computes the kinds map alongside the existing
  `normalize_positions` call at zero extra SnapTrade API cost.
- `app.py` — the broker-trade confirm-record path stamps `asset_type` via
  `resolve_trade_asset_type()`; a manually-logged trade is untouched (defaults to
  `"stock"` at read via the DB backfill — accepted, Phase 0's fundamentals gate
  already protects the actual decision regardless of this label); a new
  `"etf_registry"` row added to the App Settings `_AS_TABLES` list.
- New owner-editable **ETF registry** reference table (`"etf_registry"`,
  category→ticker-list), reusing the F-262 `reference_data.py` pattern with ZERO
  changes to that file — confirmed its `TICKER_SECTORS`-coverage check is
  name-scoped to `sector_candidates`/`sector_universe`/`discovery_universe` only,
  so `etf_registry` falls through untouched (an ETF has no single GICS sector).
  Deliberately NOT added to `reference_shelf.py`'s staleness tracker — that's a
  refresh-cadence policy decision, correctly deferred, not an oversight.

**Review = Opus reviewer (Claude Opus 4.8 (1M context)): SHIP, 0 blocking.** Every
pre-DDL write-resilience claim traced line-by-line and confirmed real (not just
described); every read-path offline-sentinel-vs-backfill distinction confirmed
correct; the fail-safe classification direction confirmed (nothing can accidentally
resolve to `"etf"`); `fetch_financials_from_info` confirmed byte-for-byte untouched;
`load_bundle`'s existing scoring behavior confirmed unperturbed. **One non-blocking
finding, since CLOSED:** until the DDL was applied, a broker-confirmed trade write
would have stripped `asset_type` alongside `broker_txn_id`/`idempotency_key`
together (the existing optional-column cascade drops the whole set on any one
miss) — low practical risk for a single-user, low-frequency confirm action, and
moot now that the columns exist. 156 tests passed across the new + touched test
files; antipattern and constants-doc gates both clean.

**DDL — APPLIED by the owner directly in Supabase, 2026-09-27** (no agent ran this;
none had live production credentials):

```sql
ALTER TABLE public.holdings        ADD COLUMN IF NOT EXISTS asset_type text;
ALTER TABLE public.trades          ADD COLUMN IF NOT EXISTS asset_type text;
ALTER TABLE public.recommendations ADD COLUMN IF NOT EXISTS asset_type text;
ALTER TABLE public.broker_position_snapshot ADD COLUMN IF NOT EXISTS kinds jsonb;
```

Not yet independently verified against a live query (e.g. `information_schema.columns`)
in this session — the owner's own report is the source for "applied." If a future
session needs to confirm the columns are actually live (not just that the SQL was
run without error), that's a quick Supabase check, not a re-run of this DDL.

**ETF registry seeding — CLOSED 2026-09-27.** This step was under-delivered
at Phase 1 ship time: the implementer correctly described the two-step
sequence (seed via raw SQL, then a real Save through the App Settings UI so
`save_reference_table` recomputes `payload_hash`/`as_of` itself) but no
runnable SQL was actually handed to the owner, so opening "ETF metadata
registry" in App Settings surfaced `resolve_universe`'s
`ReferenceDataUnavailable` error (a missing row reads identically to "the DB
is unreachable" or "RLS is misconfigured," by design — a fail-loud message,
not an empty editable table) until this was caught and fixed. The owner ran:

```sql
INSERT INTO public.reference_tables (name, payload, payload_hash, as_of, updated_by)
VALUES (
  'etf_registry',
  '{"Broad Market": ["SPY", "VOO", "IVV"]}'::jsonb,
  'seed',
  CURRENT_DATE,
  'seed_migration'
);
```

The placeholder `'seed'` hash doesn't need to be a real sha256 — the next
Save through the App Settings UI recomputes `payload_hash`/`as_of` correctly
and overwrites it, the same two-step sequence already used for the
Industrials/Utilities seeds (`docs/plans/scan-universe-refresh` precedent).

**What this does NOT do (deliberately, Phase 2+ territory):** nothing consumes
`asset_type`/`etf_facts`/the registry for a decision yet. An ETF's composite is
still withheld (Phase 0), not scored — Phase 2 is what builds the actual ETF
scoring strategy.

## Phase 3 data-readiness probe — RESOLVED POSITIVE, 2026-09-27

The earlier assumption in this doc (§6, §11) that look-through/constituent
data was "unproven — likely NOT in `.info` at all" is **corrected**: it isn't
in `.info` (that part was right), but a live probe of `yfinance.Ticker(sym)
.funds_data` — a DIFFERENT API surface Phase 1 never used — returns real data:

- `sector_weightings` — a dict of 11 GICS-like sector keys → fraction of fund
  (verified on SPY, XLK, ARKK, VTI, all real and internally consistent —
  XLK's `technology` key alone is `1.0`, SPY's 11 keys sum to ~1.0).
- `top_holdings` — a DataFrame of roughly the top 10 constituent tickers +
  weight (not the FULL holdings list — a real depth limit).
- A bond ETF (TLT) correctly returns an EMPTY `sector_weightings` dict and an
  empty `top_holdings` frame — no equity sector exposure to report, which is
  the accurate answer, not a failure. **Must not be read as "diversified" or
  defaulted to a fabricated bucket** — same offline-sentinel discipline as
  everywhere else in this app.
- An invalid/delisted ticker raises an `HTTPError` — needs the same
  provider-failure handling as every other yfinance call in this codebase.
- yfinance's sector vocabulary (`consumer_cyclical`, `consumer_defensive`,
  `basic_materials`, `realestate`, etc.) does NOT match `portfolio.py`'s own
  `TICKER_SECTORS`/`_SECTOR_PROFILES` naming — a translation table is needed
  before this can feed the existing concentration gate.

**This unblocks Phase 3**, but it is still a NEW, previously-unused data
fetch path (not wired into `fetch_etf_facts_from_info`/`bundle_loader.py`
today) with real open design questions: does a partial-exposure ETF (e.g.
XLK at 5% of the book, 100% tech-weighted) count as a full 5% tech exposure
toward `SECTOR_CEILING`, or something more conservative? Where does the
extra network call happen without slowing every portfolio load? How does an
ETF with NO equity sector data (a bond fund) get treated by the gate
(excluded, same as today, not silently zero-weighted)? These need a proper
`planner` design pass before any code — not assumed from this probe alone.

## Phase 2 — implementation record

**Shipped 2026-09-27 as F-281** (`docs/requirements.md` F-281).

**Design chain:** Opus `planner` proposed the composite shape + every candidate
policy value with reasoning (not final numbers to adopt) → owner walked through
and explicitly approved all 10 decision points in a structured review, at the
planner's recommended default on every single one → `implementer` (Sonnet) built
to the approved spec → Opus `reviewer`, SHIP/0 blocking, first pass.

**The 10 approved decisions (all at the planner's recommended default):**
1. Composite = technical 70% + cost 30% (not 60/40, not a 3-pillar mix with sentiment).
2. Sentiment excluded from the ETF composite entirely (generic macro noise for a fund, not fund-specific signal).
3. Availability gate = `net_expense_ratio is not None` only (no technical-only fallback verdict).
4. Expense-ratio bands: cheap ≤ 0.20%, expensive ≥ 0.75% (deliberately wide/round — moderate-high confidence on rough magnitude, low confidence on the exact cutoff).
5. Cost-score floor = 25/100, not 0 (a high fee is a real drag, not a disqualifier).
6. AUM: soft awareness flag at $50M, never a gate.
7. Sector/concentration: ETFs stay excluded from `SECTOR_CEILING` like "Other" — no registry-category wiring into the gate (a single category label can't distinguish "broad/diversifying" from "sector-concentrated" without real constituent weights).
8. Thresholds: reuse the equity `75/65/44/30` bands and label vocabulary — no new ETF-specific thresholds this phase.
9. New-pick scope: existing/held ETFs only; `daily_briefing.py` gained an explicit, independent guard so an ETF can never become new-pick-eligible regardless of what the availability flags say.
10. `ETF_COMPOSITE_WEIGHTS_VERSION = 1` stamped for future lineage (Definition-of-Done #8) even though nothing persists an ETF composite to a DB history table yet — confirmed by the reviewer as genuinely unwired, not a half-finished feature.

**What shipped:** new `stock_analyzer/etf_scoring.py` (`etf_available`,
`expense_ratio_score`, `etf_composite`, `etf_recommendation`, `etf_aum_thin` — see
`docs/architecture.md`'s module section for full detail); 6 new `constants.py`
values with matching `docs/architecture.md` rows; additive `bundle_loader.py`
keys; `portfolio.build_portfolio_df`'s three-way `fund_ok`/`etf_ok`/withheld
resolution (the reviewer's top-risk item — confirmed byte-identical for the
`fund_ok=True` stock path, and confirmed `etf_ok` can never be `True` for
`asset_type != "etf"` even on a malformed bundle); the `daily_briefing.py`
new-pick guard; an in-app User Guide addendum explaining the ETF verdict shape
and its two known gaps (no new-pick eligibility yet, no sector look-through yet).

**Review = Opus reviewer (Claude Opus 4.8 (1M context)): SHIP, 0 blocking.**
Verified the cost/composite arithmetic by hand (not just trusting the tests),
confirmed the asymmetry the design was built for actually holds (technical=80:
cheap fund composite 86.0 clears Buy, expensive fund composite 63.5 lands in
Hold — same technical reading, different verdict), confirmed the stock scoring
path is genuinely byte-identical, confirmed `etf_aum_thin` and
`ETF_COMPOSITE_WEIGHTS_VERSION` are both truly unwired (not silently touching
any DB write path), confirmed zero scope creep into `technicals.py`/`risk.py`/
`business_quality_score`/`valuation_score`/`resolve_sector`/any new-pick path
beyond the one guard. Two non-blocking notes, left as-is (cosmetic): a minor
defensive-check style inconsistency in `daily_briefing.py`'s new guard (matches
an adjacent existing pattern, not a new risk), and the implementer's stated
rationale for a `bundle_loader.py` micro-refactor was slightly inaccurate
(the refactor itself is still confirmed behavior-neutral). Full suite 6355
passed; antipattern and constants-doc gates both clean.

**What this does NOT do (deliberately, Phase 3/4 territory):** no look-through
sector exposure (still gated on an unverified constituent-holdings data probe),
no ETF new-pick/Grow Today eligibility, no UI caption for `etf_aum_thin`, no
ETF-specific BUY/HOLD/SELL thresholds (reused the equity ones — revisit only if
they prove loose once eyeballed against real ETF holdings).

**CORRECTION, found and fixed 2026-09-28 via a real owner screenshot:** Phase
2's own scope claim above — "the new verdict flows through the EXISTING
`port_df["Score"]`/`["Signal"]` render paths... nothing new to render" — was
WRONG for two standalone per-ticker research surfaces that never read
`port_df` at all: the 📈 Analysis page (three separate inline duplicates of
the OLDER `fund_ok`-only withhold check — a single-ticker summary banner, the
Scorecard table, and the "Detailed Analysis" per-ticker banner, all in
`app.py`) and 🏠 Home's "🔍 Research a Stock" widget
(`quick_research.py::research_ticker`, a different feature from the Analysis
page — confirmed by tracing the real call site at `app.py:7998`, not assumed).
None of these four sites were ever updated to check `etf_ok`, so every one of
them showed "🚫 Verdict withheld" for any ETF — including SPY, which clearly
has a known expense ratio and should have gotten a real verdict. This was a
genuine blast-radius miss by both the original Phase 2 `planner` design pass
and its `reviewer` pass — neither traced these two standalone per-ticker
research surfaces, only `portfolio.build_portfolio_df` (Home/Watchlist/
Summary) and `daily_briefing.py`'s new-pick gate. **Fixed same day** by
extending the exact same already-approved `fund_ok`/`etf_ok`/withheld
three-way resolution to all four sites — no new policy value, no new
`constants.py` entry, `ETF_COMPOSITE_WEIGHTS` reused (never hardcoded) for the
new ETF-branch pillar-breakdown captions. Opus `reviewer`: SHIP, 0 blocking —
verified the stock path is byte-identical at every site (the equity branches
were either untouched or only had an `elif` inserted before them), verified
the Scorecard/Detailed-Analysis agreement invariant holds (same ticker can
never show conflicting states across the two sites), verified the new dynamic
HTML is genuinely escaped (`_safe_html`/`_md_bold`, not just gate-shaped),
verified the ETF withhold-wording tweak ("expense ratio unavailable")
accurately names the real failure mode rather than reusing stock language
that doesn't fit. Full suite 6397 passed. Memory
[[feedback_verify_render_paths_not_assumed]]: **when a phase's design
explicitly assumes "the existing render paths already cover this," verify
that claim against every REAL render call site, not just the one the design
pass happened to trace** — `app.py` has no test suite to catch a missed
consumer, so this class of gap is invisible to everything except a live
screenshot.

**SECOND FOLLOW-ON, same day, found by re-checking the SAME screenshot more
carefully:** the first fix above patched the visible banner but deliberately
used a separate parallel `elif etf_ok:` block rather than reassigning the
shared `rec = r["rec"]` local variable the REST of the per-ticker tab's
~2000 lines also reads directly — so the top banner correctly showed "Buy
70.6/100" for SPY, but the SAME tab's "Trade Plan" section below it
contradicted it with "Mixed signals — not a high-conviction entry," still
driven by the unfixed raw `rec["label"]`. Investigation found roughly a
dozen more affected reads in the SAME loop (Trade Plan branch selection,
exit-urgency flags, entry-quality messaging, Trade Journal integration), an
equity-pillar-specific "What would change this signal?" expander with no
ETF equivalent, a SEPARATE independent bug in the Scorecard (its R:R column
still gated on the raw equity label even after the first fix corrected its
Score/Signal columns), a SEPARATE independent "📋 Analysis Summary"
export loop with the identical bug, and — the one genuine data-integrity
finding — a Gate Suppression Ledger (G-18) DB write persisting the WRONG
(fabricated stock) composite score for an ETF's stop-suppression event into
a real historical grading table.

**Fixed by reassigning at the source** (right after `rec = r["rec"]`:
`_da_etf_ok` computed once, `rec` reassigned to `r["etf_rec"]` only when
true, a new local `_da_display_total` for direct `r["total"]` reads) rather
than patching each read site individually — this automatically fixed every
downstream `rec[...]` consumer for free. The bundle dict `r["total"]`/
`r["rec"]` themselves are NEVER mutated (verified by the reviewer) — only
fresh per-iteration locals are reassigned, preserving Phase 2's own
anti-mutation design (overwriting the cached bundle would leak the ETF score
into every OTHER reader of that same cached object on a later rerun). The
equity-pillar expander is wrapped in `if not _da_etf_ok:` and suppressed
entirely for a fund, rather than showing fabricated-neutral pillar "analysis."
The Gate Suppression Ledger write now sources `composite_score=
_da_display_total`. The Scorecard and export-loop bugs each got their own
independent, scope-isolated fix (`_sc_etf_ok`, `_ap_etf_ok`/
`_ap_display_rec`/`_ap_display_total`).

**A real escaping gap surfaced and closed along the way, not routed around:**
the fix's own new interpolations tripped `check_antipatterns.py`'s
`UNSAFE_HTML_DYNAMIC` rule on a PRE-EXISTING (already-baselined) unescaped
f-string it happened to touch. Rather than accept the implementer's flagged
open question by blindly re-keying the baseline, the lead actually closed the
escaping gap (`_safe_html` on every interpolated value, restructured to avoid
a ternary the gate's own AST rule can't credit — see `check_antipatterns.py`'s
`_is_dynamic_html` docstring for exactly why a ternary/BinOp wrapping an
escaper call earns no credit) and then deliberately regenerated the baseline
via `--init` — confirmed via `git diff --stat` to be an exact 1-line deletion
(the newly-escaped instance dropping out), nothing else moved.

Second Opus reviewer pass: SHIP, 0 blocking. Confirmed the bundle-mutation
guarantee holds, confirmed every downstream consumer resolves correctly,
confirmed the G-18 write is a precise one-argument fix with no schema change,
confirmed the equity-pillar expander's internal logic is byte-identical
(only re-indented under the new guard), confirmed the escaping fix is
genuinely safe (re-derived the rendered output by hand for both the empty-
and populated-`_rc_why` cases) rather than gate-shaped, confirmed the
baseline diff is exactly the one expected line. One non-blocking note, left
as-is: the "📋 Analysis Summary" export's "Trade" line still uses
stock-style entry-zone/stop math alongside the now-ETF-aware verdict header
— pre-existing, not introduced by this fix, worth a glance in a future pass.
Full suite 6397 passed.

## Phase 3a — implementation record

**Shipped 2026-09-27.** Design chain: Opus `planner` resolved the Phase 3
data-readiness probe (above) into an approved policy set (full fractional
blending, a new `ETF_LOOKTHROUGH_CACHE_MAX_AGE_DAYS = 30` constant,
awareness-only — zero `SECTOR_CEILING`/`sector_exposure` touch), the owner
signed off, `implementer` (Sonnet 5) built to spec, Opus `reviewer` SHIP/0
blocking on the first pass.

**Review = Opus reviewer (Claude Opus 4.8 (1M context)): SHIP, 0 blocking.**
Confirmed the single most important thing to verify — the hard-gate
boundary — is real, not just claimed: every `SECTOR_CEILING` call site
across the whole codebase lives in files NOT in this changeset, and
`SECTOR_CEILING`/`sector_exposure`/`TICKER_SECTORS`/`resolve_sector` appear
in the diff only inside new comments/docstrings. Traced the three-state
sentinel end-to-end (fetch → cache → bundle → consumer) and confirmed no
`None`/pandas-NaN collapse anywhere. Confirmed the GICS-11 target vocabulary
is character-for-character identical between the new alias table and the
existing one (no "Healthcare"/"Health Care" drift). Independently re-derived
the fractional split arithmetic and confirmed the read-site defensive-check
change (`.get(key, {})` vs the original `(loaded_data.get(t) or {})`) cannot
introduce a crash, since a failed ticker is genuinely absent from
`loaded_data` rather than present-with-`None` (verified against both
populate sites). **One non-blocking correction to this section's own earlier
wording:** the new function's stock-path output is functionally equivalent
to `real_sector_exposure()` but not literally byte-identical — it always
calls `.reset_index(drop=True)` (the original doesn't), ties on exactly-equal
`Pct` values can sort in a different row order, and the `Value` column is
always float64 vs. the original's int64-preserving dtype. Cosmetic for the
single awareness-chart consumer (both differences are neutralized by the
comparison test's own `reset_index` on both sides), but "byte-identical" was
an overstatement — corrected here. Full suite 6380 passed; antipattern and
constants-doc gates both clean.

**What shipped (code + tests, pending review):**
- `stock_analyzer/data.py::fetch_etf_lookthrough(ticker)` — a NEW fetch via
  `yf.Ticker(ticker).funds_data` (a different API surface than `.info`, never
  merged into `fetch_etf_facts_from_info`). Three-state contract: provider
  failure -> `None`; fetched with no equity exposure (a bond fund) -> a
  present dict with `sector_weightings={}`/`top_holdings=[]`; fetched with
  real data -> the full shape. Broad `except Exception: return None`, mirrors
  every other provider call in this codebase.
- `stock_analyzer/db.py::save_etf_lookthrough_cache`/`load_etf_lookthrough_cache`
  — new persistent cache, byte-for-byte mirroring `save_fundamentals_cache`/
  `load_fundamentals_cache`'s structure (has_db() guard, `.limit(1)`,
  swallow-all-exceptions, upsert on `ticker`). NOT `_READONLY`-gated — a
  system cache, not user data, same posture as `save_sector_cache`. Added to
  `tests/test_db_readonly.py::_UNGATED_BY_DESIGN` and to `db.py`'s own
  exemption comment (mechanically enforced — see Hard Rule discussion in
  `tests/test_db_readonly.py`).
- `stock_analyzer/constants.py::ETF_LOOKTHROUGH_CACHE_MAX_AGE_DAYS = 30` — a
  cache-age policy value, deliberately checked CACHE-FIRST (the reverse order
  from `FUNDAMENTALS_CACHE_MAX_AGE_DAYS`'s live-first pattern), since a fund's
  sector composition moves far more slowly than a stock's fundamentals.
  `docs/architecture.md` constants table updated; `check_constants_documented.py`
  clean.
- `stock_analyzer/bundle_loader.py::load_bundle` — additive `etf_lookthrough`
  key, `None` for a stock bundle (regression-tested), populated for an ETF
  bundle via the cache-first resolve/write-through described above. Never
  raises; the live fetch and both cache calls all already degrade to
  `None`/no-op on any failure.
- `stock_analyzer/portfolio.py` — new `_ETF_LOOKTHROUGH_SECTOR_ALIASES` (a
  SEPARATE dict from `_PROVIDER_SECTOR_ALIASES`, not merged: the two source
  vocabularies use different naming conventions — yfinance `funds_data`'s
  snake_case/compound keys like `realestate` vs `.info["sector"]`'s
  space-separated strings like `"real estate"` — an explicit 1:1 mapping is
  safer than a shared string-normalization step) mapping all 11 live-probed
  `funds_data.sector_weightings` keys onto the exact same GICS-11 target
  vocabulary `_PROVIDER_SECTOR_ALIASES` already uses. New
  `real_sector_exposure_with_lookthrough(port_df, loaded_data)` — same output
  shape as `real_sector_exposure()`; a stock holding is scored the same way
  (functionally equivalent on a stock-only portfolio, confirmed by a
  side-by-side comparison test — see the reviewer note below on the two
  cosmetic dtype/row-order differences that keep this from being literally
  byte-identical); a held ETF's market value is split FRACTIONALLY
  across GICS buckets per its real `etf_lookthrough["sector_weightings"]`; an
  ETF with `etf_lookthrough=None` or a present-but-empty `sector_weightings`
  (a bond fund) contributes ZERO to every bucket — explicitly excluded, never
  defaulted to any bucket (including "Other"), never read as "diversified."
  Does NOT touch `sector_exposure()`, `resolve_sector()`, `TICKER_SECTORS`,
  or `SECTOR_CEILING` — zero change to the hard concentration gate.
  Deliberately used the two-arg `.get(key, {})` form (not `.get(key) or {}`)
  at both new read sites, per `scripts/check_antipatterns.py`'s
  `OFFLINE_SENTINEL_COLLAPSE` rule's own documented distinction — this
  codebase's producers never store an explicit `None` for a present key, so
  the two-arg default preserves the same offline-sentinel discipline without
  growing the antipattern baseline.
- `app.py` — the existing F-223 "🏛️ Portfolio vs. S&P 500 (Real Sector)"
  chart (Analytics tab) gained a checkbox, "Include ETF look-through"; when
  checked, it calls `real_sector_exposure_with_lookthrough` instead of
  `real_sector_exposure` and shows an explanatory caption. Render-only wiring,
  no new decision logic in `app.py` itself — same chart, same taxonomy, one
  toggle between two readouts that can never disagree on a sector's name.
- **Phase 3b is intentionally NOT built here** — the fetched/cached payload
  already carries `top_holdings` (index=symbol, `Holding Percent`-derived
  weight) precisely so a future top-holdings overlap detector can reuse this
  same cache without a second `funds_data` fetch.

**Tests added:** `tests/test_data_etf_lookthrough.py` (5 — success shape, bond
fund present-but-empty, `Ticker()` construction raising, `.funds_data`
property raising, `top_holdings=None` handled), `tests/test_db_etf_lookthrough_cache.py`
(8 — round-trip, missing ticker, DB offline for both read/write, table-missing
for both read/write, non-dict payload rejected, blank ticker), 10 new/extended
cases in `tests/test_bundle_loader_asset_type.py` (stock bundle's
`etf_lookthrough` is `None` in two existing tests + 4 new Phase 3a cases:
fresh-cache-hit-skips-live-fetch, no-cache-live-fetch-writes-through,
stale-cache-falls-through-to-live, live-failure-with-no-cache-stays-`None`),
8 new cases in `tests/test_portfolio.py` (alias coverage of all 11 live-probed
keys + unrecognized-key fallthrough, broad-ETF fractional split, single-sector
100% bucket, `None`-lookthrough zero-footprint-without-corrupting-other-rows,
empty-`sector_weightings` bond-fund zero-footprint, stock-only
byte-identical-to-`real_sector_exposure` comparison, empty-portfolio). Full
suite: **6380 passed** (up from 6355 pre-Phase-3a). Antipattern gate: clean
(no baseline growth needed — see the two-arg `.get()` fix above). Constants-doc
gate: clean.

**DDL — APPLIED by the owner directly in Supabase, 2026-09-27** (no agent has
live production Supabase credentials, same convention as every prior DDL in
this plan):

```sql
CREATE TABLE IF NOT EXISTS public.etf_lookthrough_cache (
    ticker     text PRIMARY KEY,
    payload    jsonb,
    updated_at timestamptz
);
```

Before this was applied, `load_etf_lookthrough_cache`/`save_etf_lookthrough_cache`
degraded to `None`/`False` (the `_client().table("etf_lookthrough_cache")` call
raised, caught by the broad `except Exception`), so `bundle_loader.load_bundle`
fell through to a live `fetch_etf_lookthrough` call every time with no
write-through persisting — functionally correct throughout (an ETF bundle
still got a real look-through reading), just uncached until the table
existed. Now that the table is live, a fresh ETF bundle's look-through data
persists and gets reused for `ETF_LOOKTHROUGH_CACHE_MAX_AGE_DAYS` (30 days)
before the next live re-fetch.

**What this does NOT do (deliberately, Phase 3b/4 territory):** no
top-holdings overlap detector (Phase 3b — reuses this same fetch/cache, not
built this pass); no wiring of the look-through readout into
`SECTOR_CEILING`/`sector_exposure`/any gate (approved policy point 7 from
Phase 2 still stands: ETFs stay excluded from the hard concentration gate);
no UI caption/gate change on Watchlist/Analysis/Portfolio Overview beyond the
one Analytics-tab toggle.

## Phase 3b — implementation record

**Shipped 2026-09-27 as F-283** (`docs/requirements.md` F-283). Design chain:
the exact function shape and policy (reuse `SINGLE_NAME_CEILING`, no new
constant, reuse Phase 3a's already-fetched/cached data, no new fetch) was
already fully specified during the original Phase 3 design pass — no fresh
`planner` round was needed for this phase, since there was no new policy
value to decide. `implementer` (Sonnet) built directly to that spec; Opus
`reviewer` SHIP/0 blocking, first pass.

**What shipped:** `stock_analyzer/portfolio.py::combined_name_exposure(port_df,
loaded_data)` — for every ticker with any direct-holding or ETF-look-through
exposure, computes `direct_pct` + `lookthrough_pct` (summed across every held
ETF whose top-~10 disclosed holdings include that ticker) = `combined_pct`,
flags `over_ceiling` at the existing `SINGLE_NAME_CEILING` (15.0, no new
constant), and names which ETF(s) contributed (`via_etfs`). A new expander
"🔍 True Single-Name Exposure (incl. ETF look-through)" on the same Analytics
tab as Phase 3a's toggle, showing only rows where an ETF actually adds
something to the picture, with three required disclosure captions: the
top-~10-only coverage limit (never implies full-holdings coverage), which
held ETF(s) currently have no look-through data (`etf_lookthrough is None`,
distinguished from a legitimate bond-fund empty result), and a calm
"no overlap detected" message when nothing qualifies. Zero changes to
`risk_advisor.py`'s existing `single_name_concentration` check — this is a
separate, read-only awareness surface that never gates or feeds any
recommendation.

**Review = Opus reviewer (Claude Opus 4.8 (1M context)): SHIP, 0 blocking.**
Confirmed no new `constants.py` value, confirmed `risk_advisor.py` has zero
diff lines, traced the offline-sentinel handling (a bond-fund's legitimately
empty `top_holdings` is never confused with a `None`/missing-data state, at
both the pure-function and the render-caption layer), re-derived the
multi-ETF summation arithmetic by hand from the test's own numbers, and
confirmed the denominator matches `sector_exposure()`'s existing convention
(sum of `port_df["Market Value"]`) so this feature's percentages can't
silently disagree with every other exposure percentage in the app. Two minor
non-blocking notes, left as-is: the zero-exposure omission technically checks
the ROUNDED value (a position under ~0.05% of book could in principle be
dropped) rather than a stricter "truly zero" check — negligible in practice
for an awareness surface, and no test exists yet for the specific case of a
held ETF also appearing as a top-holding constituent of a DIFFERENT held ETF
(the code's separate direct/look-through tallies make this safe by
construction, just untested directly). Full suite 6390 passed; antipattern
gate confirmed clean (the implementer's own first draft tripped the
`OFFLINE_SENTINEL_COLLAPSE` rule on an `or []` collapse and fixed it to the
two-arg `.get(key, [])` form before handback — the reviewer independently
re-derived that this specific fix is semantically safe, not just
gate-shaped, since Phase 3a's producer never stores an explicit `None` for a
present `top_holdings` key).

**What this does NOT do:** no new fetch, no new cache table (reuses Phase
3a's `etf_lookthrough` bundle data as-is), no new `constants.py` value, no
change to any gate or existing recommendation. **This closes Phase 3
entirely** (3a + 3b) — only Phase 4 (type-aware UI polish, incl. surfacing
the still-unwired `etf_aum_thin` flag from Phase 2, and deciding ETF new-pick
eligibility as a possible 2b) remains designed, not started.

## Phase 4 — implementation record (closes the initiative as originally scoped)

**Shipped 2026-09-27 as F-284** (`docs/requirements.md` F-284).

The owner was asked to disambiguate what "finish Phase 4" meant, since it had
bundled two unrelated things: a small display item (surface the `etf_aum_thin`
flag) and a much bigger, genuinely undecided policy question (ETF new-pick
eligibility on Grow Today). **Owner chose to scope this pass to the AUM-thin
caption only** — the new-pick eligibility question remains explicitly out of
scope, unscoped, no trigger date.

**What shipped:** `stock_analyzer/quick_research.py`'s existing "Key Context"
bullet (bullet 4 — already surfaces earnings proximity, analyst-revision
spikes, short interest for a stock) gained one more clause: when
`asset_type == "etf"` and `etf_aum_thin` is `True` on the bundle, it shows
"⚠ Small fund by assets under management — verify liquidity/spread before
sizing a position." `etf_aum_thin` has been computed on every ETF bundle
since Phase 2 but was never wired into any UI until now. Absent/`False` for
every stock and for an ETF whose AUM is unknown or above the floor, so this
is a no-op for the overwhelming majority of tickers — confirmed by a
dedicated regression test that a stock bundle (missing both keys entirely)
never satisfies the clause.

**A second finding closed a loose end from the very first architecture
review without any code change being needed:** the original Phase 0 review
(§4) anticipated needing to explicitly suppress the earnings-proximity and
analyst-revision alerts in `portfolio.alerts()` for ETFs ("type-aware
panels... hide the earnings/analyst-revision cards"). Reading the actual code
this pass confirmed that's already true by construction — `alerts()`'s
earnings branch only fires `if earn:` (an ETF bundle's `earnings` key is
never populated by any provider), and its analyst-revision branch reads
`rev.get("downgrades_90d", 0)`/`net` from a `revisions` dict that's likewise
never populated for a fund (no analyst-coverage data source exists for ETFs
in this app). No suppression code was needed; both already correctly no-op.

**Review:** deterministic gates only (full suite 6393 passed, antipattern and
constants-doc gates both clean) — no Opus `reviewer` pass, per this repo's
own review-economy rule: `quick_research.py` is not a `_GATE_FILES` member,
no new `constants.py` value was added, and the change is pure-additive
display text reading an already-approved, already-computed Phase 2 flag.
3 new tests (clause fires when thin, clause absent when not-thin, clause
absent for a stock bundle missing both keys entirely).

---

## Where the ETF/multi-asset initiative stands now (all phases, final)

**Shipped, all 2026-09-27:** Phase 0 (F-279, fundamentals-withhold
consistency fix — closed a live mis-scoring bug), Phase 1 (F-280, asset-type
classification + broker capture plumbing, DDL applied to production same
day), Phase 2 (F-281, the actual ETF scoring composite + gate, 6 owner-approved
constants), Phase 3a (F-282, real sector look-through on the existing GICS-11
diagnostic — deliberately NOT wired into the hard concentration gate, whose
own taxonomy is incompatible with ETF sector data), Phase 3b (F-283, a
holdings-overlap detector reusing Phase 3a's data and the existing
`SINGLE_NAME_CEILING`, no new constant), Phase 4 (F-284, the AUM-thin
caption). Five Opus reviewer passes across the phases that touched
`_GATE_FILES` (one FIX-FIRST/1 blocking on Phase 0, fixed same session; SHIP/0
blocking on Phases 1, 2, 3a, 3b); Phase 4 correctly used the deterministic
gates alone.

**THIRD follow-on, same day 2026-09-28** — a further screenshot (scrolled
further down the SAME SPY tab) found the "Gate checks" row's "Data Quality"
tile showing a red ✗ ("BQ + Valuation metrics available") directly under
the already-fixed "Buy 70.6/100" banner — the same bug class a third time,
in code the first two fixes' `rec[`/`r["total"]` greps didn't cover, since
this reads `bq_available`/`val_available` directly. A broader grep for those
two flags across the whole of `app.py` found 4 raw reads: this one real
contradiction (fixed) and 3 lower-severity display-completeness gaps
(a Rebalancer action-plan message, the Portfolio Overview "Score Breakdown"
tiles, and the Analysis page's "Deep Dive" pillar tiles — all three show an
honest "❔ Withheld" for an ETF's Business Quality/Valuation, just never show
its real Cost pillar instead). **Owner explicitly chose to defer the 3
lower-severity sites to a later pass** rather than let scope keep expanding
— they're incomplete, not wrong, so no urgency. Fixed: `_gate_bq_ok =
_da_etf_ok or (the original expression)`, reusing the SAME `_da_etf_ok`
from the second fix (so a stock's behavior is provably byte-identical), plus
an ETF-appropriate detail string ("Expense ratio available") and a small
wording fix ("Strong fund" vs "Strong stock" in the adjacent R:R-quality
warning). Opus reviewer (fourth pass): SHIP, 0 blocking — confirmed the gate
now correctly evaluates True for a real ETF regardless of the structurally-
False equity flags (closing the actual contradiction, not just the wording),
confirmed the stock path reduces to exactly the original expression, and
confirmed the 3 deferred sites are untouched. Full suite 6397 passed, no new
`constants.py` value.

**FOURTH follow-on, same day 2026-09-28 — closes the 3 deferred display-
completeness sites, plus 2 more real gaps found while scoping them.** An
Opus `planner` design pass, given the exact 3 sites, found the scope was
bigger than "add a Cost tile": (1) the Rebalancer's evidence panel could
print an affirmative FALSE claim like "🟡 business quality remains solid"
for a fund with no business quality at all — the exact bug class this whole
campaign started from; (2) Home's Position Drill-Down "Composite Score"
metric showed the raw fabricated equity composite, contradicting the correct
ETF verdict already shown in a caption just above it on the SAME page. Both
folded into the fix as necessary, not optional.

**New consistent vocabulary** (`stock_analyzer/util.py`): `ETF_PILLAR_NA_SHORT
= "➖ n/a for funds"` / `ETF_PILLAR_NA_REASON`, deliberately distinct from the
existing "❔ Withheld"/"❔ not measured" (which means "a transient data gap")
— this new state means "structurally not part of this asset type's composite
by design." The two must never share a glyph. **New classifier**
(`stock_analyzer/etf_scoring.py::cost_urgency_high`): reuses the EXISTING
`COMPOSITE_HOLD` constant (owner-approved reuse, confirmed via a direct
question before building — not a new policy value) as the cutoff for
whether a fund's cost pillar counts as "weak enough to act on" alongside a
technical review signal, mirroring exactly how the stock case already uses
the same constant for `bq_score`. Extracted into `stock_analyzer/` rather
than compared inline in `app.py`, specifically to avoid tripping
`check_antipatterns.py`'s `POLICY_DECISION_IN_RENDER` rule.

**Fixed:** the Rebalancer evidence panel + action message (dims list and
diagnosis become ETF-aware; a new cost-framed urgency message), Home's
`d4` Composite Score metric (now reads `etf_total`/`etf_rec` for a real
ETF, closing the contradiction) and its 4-tile row (Technical corrected to
its real 70% weight, a real "Cost" tile added, Valuation/Sentiment show the
new NA state — critically, Sentiment no longer falsely claims a "+X pts
(10%)" contribution, since Phase 2 excludes it from the ETF composite
entirely), and the Analysis Deep Dive tab's pillar tiles (reuses the
already-in-scope `_da_etf_ok` from an earlier fix, Business-Quality slot
repurposed to Cost, Valuation slot shows the NA state). Opus reviewer
(fifth pass): SHIP, 0 blocking — confirmed every equity/stock branch at all
3 sites is provably byte-identical (either untouched, or reachable only via
a new branch inserted before/around it), confirmed the NA-vs-Withheld glyph
distinction holds everywhere, confirmed the `COMPOSITE_HOLD` reuse is real
(no bare threshold literal introduced), confirmed dollar-sign escaping
matches the existing convention in the new branches. Full suite 6401
passed. Two non-blocking notes, left as-is: the Deep Dive Sentiment tab
still shows a numeric score header before its new NA caption for an ETF
(honest, just slightly mixed messaging — worth an owner eyeball on a live
screenshot); and a PRE-EXISTING (not introduced by this diff) unescaped
`~${tv:,.0f}` in an adjacent, unrelated Rebalancer "trim" branch, flagged
for awareness only. **A concurrent peer session was active in the same repo
during this fix** (`python-lab-b9`, unrelated test-suite-optimization work)
— the lead independently verified via `git diff` on each of the 4 touched
files, before staging, that this fix's changes were cleanly isolated from
the peer's in-progress files, and committed only the 4 files by exact name.

**Genuinely still open, deliberately out of scope for every phase above, no
trigger date:**
- **ETF new-pick/Grow Today eligibility** — a real, unscoped policy question
  (needs a discovery universe + threshold recalibration decision), explicitly
  declined for Phase 4 by the owner. Pick up only via a fresh explicit ask.

The `etf_lookthrough_cache` DDL (Phase 3a) was applied by the owner
2026-09-27, closing that non-blocking item. Nothing else from the original
architecture review remains unaddressed beyond the one deliberately-deferred
item above.
