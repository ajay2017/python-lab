# Multi-asset-type support (ETF first) — architecture review + phased plan

**Status: ANALYSIS COMPLETE + PHASES 0 AND 1 SHIPPED, all 2026-09-27 (F-279, F-280).**
Opus `planner` architecture review, then a second `planner` design pass per phase,
`implementer` built each, Opus `reviewer` before every commit (Phase 0: FIX-FIRST/1
blocking → fixed same session → SHIP/0 blocking; Phase 1: SHIP/0 blocking, first
pass). **The DDL for Phase 1's new columns has NOT been applied to production yet —
see the Phase 1 record below for the exact SQL; nothing in Phase 1 requires it to
ship (every write path degrades gracefully pre-DDL), but apply it when convenient
to close a small non-blocking window the reviewer flagged.** Phases 2-4 below are
DESIGNED, NOT STARTED — each needs its own fresh `planner`/`reviewer` pass and, for
Phase 2, explicit owner sign-off on new policy constants before any code is written.
This doc is the design-of-record; do not start a later phase from memory without
reconfirming this file still matches the code.

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
finding:** until the DDL below is applied, a broker-confirmed trade write strips
`asset_type` alongside `broker_txn_id`/`idempotency_key` together (the existing
optional-column cascade drops the whole set on any one miss) — low practical risk
for a single-user, low-frequency confirm action; apply the DDL soon to close this
window, not because anything breaks without it. 156 tests passed across the new +
touched test files; antipattern and constants-doc gates both clean.

**DDL for the owner to run in Supabase (not applied by any agent — no live
credentials were used or available; every write path already degrades gracefully
without this, so there is no urgency beyond closing the note above):**

```sql
ALTER TABLE public.holdings        ADD COLUMN IF NOT EXISTS asset_type text;
ALTER TABLE public.trades          ADD COLUMN IF NOT EXISTS asset_type text;
ALTER TABLE public.recommendations ADD COLUMN IF NOT EXISTS asset_type text;
ALTER TABLE public.broker_position_snapshot ADD COLUMN IF NOT EXISTS kinds jsonb;
```

**ETF registry seeding — do this via the App Settings UI, not a raw SQL hash guess:**
insert a placeholder row for `"etf_registry"` (payload can be anything valid, e.g.
`{"Broad Market": ["SPY", "VOO", "IVV"]}`) directly in `reference_tables`, then open
⚙️ App Settings → the new "ETF metadata registry" table → make any trivial edit and
Save (or edit it to the real desired categories directly) so `save_reference_table`
stamps `payload_hash`/`as_of` correctly itself — this is the same two-step sequence
already used for the Industrials/Utilities seeds (`docs/plans/scan-universe-refresh`
precedent), safer than hand-computing a hash to match `reference_data.canonicalize`.

**What this does NOT do (deliberately, Phase 2+ territory):** nothing consumes
`asset_type`/`etf_facts`/the registry for a decision yet. An ETF's composite is
still withheld (Phase 0), not scored — Phase 2 is what builds the actual ETF
scoring strategy.
