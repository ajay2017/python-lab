# Information Asymmetry Detector — Design Plan

**Date:** 2026-07-24
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** Plan SHIP 2026-07-24 (2 Opus rounds) — ready for implementation

> **One-line spec:** Persist the already-shipped, already-computed price cross-check
> result once per Eastern trading day per held ticker, then annotate the existing
> "sources disagree" banner with whether today's disagreement is *new* or has
> *widened* since the last time it was recorded — closing the one genuine gap in an
> already-shipped feature rather than building a new agent.

---

## Why this plan is much narrower than the roadmap's Idea #4

`docs/plans/agentic-intelligence-roadmap.md`'s Idea #4 section (written 2026-07-23)
imagined a 3-source divergence detector ("Finnhub vs. yfinance vs. FMP") that could
flag something like *"Three sources now disagree on NVDA's forward PE — this widened
in the last 48h."* Research for this plan found that example is **not achievable as
written**, for two independent reasons:

1. **Finnhub supplies live price only in this codebase** — `finnhub_provider.py`
   advertises `CAP_LIVE_PRICE` alone (no fundamentals capability at all). A genuine
   3-way comparison on forward PE is structurally impossible; only yfinance and FMP
   could ever report that field.
2. **FMP's forward PE is sometimes a derived formula, not an independent read**
   (`fmp_provider.py:432-449`) — when FMP's own endpoint lacks a forward PE, it's
   synthesized from `trailing PE / (1 + earnings growth)`. Comparing that against
   yfinance's analyst-consensus-based figure isn't always "two sources disagreeing,"
   it's sometimes "two different methodologies disagreeing" — a materially different,
   noisier signal than a genuine data-integrity fault.

**User decision (2026-07-24, confirmed via question):** scope P4 to the narrower,
lower-risk option — add historical persistence and a widening annotation to the
**already-shipped price cross-check** (`orchestrator.crosscheck_price`/`crosscheck_batch`,
live in production today, surfaced as a red banner on Home for held positions). No new
fundamentals comparison, no new provider capability, no LLM. The roadmap's fundamentals-
divergence idea is deferred indefinitely, not built here.

**What's actually new, given this scope:** persistent storage (currently zero — the
existing check is a 5-minute `st.cache_data` TTL with no history) and a day-over-day
trend comparison. Everything else — the comparison math, the tolerance constants, the
banner rendering — is reused unchanged.

---

## What already exists (reused, not rebuilt)

| Piece | Where | Status |
|---|---|---|
| Pairwise price cross-check (primary vs. one independent validator) | `stock_analyzer/providers/orchestrator.py::crosscheck_price()`/`crosscheck_batch()` | Shipped, unchanged by this plan |
| Tolerance constants | `constants.py`: `DATA_XCHECK_PREVCLOSE_TOL_PCT=0.5`, `DATA_XCHECK_LIVE_TOL_PCT=3.0`, `DATA_XCHECK_FIELDS={"price"}` | Shipped, reused as-is |
| Live red-banner rendering for held positions | `app.py:3019-3064`, the "Price cross-check guardrail" block on Home | Shipped — this plan **extends** this exact block |
| 5-minute cache of the live check result | `app.py:2109` (`_cached_price_xcheck()`) | Shipped, reused as the data source for the new write |

## What's genuinely new

1. **`price_xcheck_history` table** — persists one row per `(ticker, check_date)`, so a
   "did this widen?" comparison becomes possible for the first time.
2. **A trend annotation** on the existing red banner: when a ticker is failing
   cross-check today AND a prior day's row exists showing a smaller (or no) gap,
   append "— widened from X% to Y% since `<date>`" to that ticker's existing bullet.
3. **A tiny pure helper** (`divergence_widened()`) — a diff + threshold check, not a
   new agent, not an LLM call.

---

## Design principles (non-negotiable)

1. **Strictly additive.** The trend annotation is text appended to an existing banner
   line — it never changes whether the banner fires, never gates a stop/P&L decision,
   never modifies the underlying cross-check tolerance logic.
2. **Zero new API cost.** The write reuses `_cached_price_xcheck()`'s already-computed
   result (the same 5-min-cached dict already being rendered) — no new provider call,
   no new cross-check invocation. This is why the write happens from the **interactive
   app path** (Home page), not from `cron_runner.py`: research confirmed the premarket
   cron path never calls `crosscheck_price`/`crosscheck_batch` today, so wiring this
   into cron would mean a **new** per-ticker second-provider fetch every cron run —
   real, avoidable cost. The interactive path already pays for this computation; only
   the *persistence* is new.
3. **Day-deduped write, not gated by DB round-trip.** A `st.session_state` flag
   (`_price_xcheck_logged_date`) tracks whether this session has already written
   today's row — checked before the write, not via a Supabase read. Avoids redundant
   writes on every Streamlit rerun within a session without adding a network round-trip
   just to check "did I already log this."
4. **Graceful degradation.** If the table doesn't exist yet (DDL not applied), the
   write no-ops and the read returns nothing — the banner renders exactly as it does
   today, with no trend annotation, never a crash.
5. **Never fabricates.** No trend annotation is shown unless a genuine prior row
   exists in the database. First-time-seeing-this-ticker (or first day after the DDL
   is applied) is silently inert — no manufactured "this is the first time we've
   checked" caption.
6. **Scoped to held positions only, on the existing Home guardrail.** The Stock
   Analysis page's separate cross-check caption (for ad-hoc research tickers, not
   necessarily held) is out of scope for Phase 1 — held positions are the
   highest-stakes use of this check (stops/P&L), and extending non-held-ticker history
   would require deciding retention/scope questions this plan doesn't need to answer yet.
7. **Opus review required** before build (this plan) and before ship (code review).

---

## New pure function: `divergence_widened()`

Added to `stock_analyzer/data.py`, right next to the existing `crosscheck_price`/
`crosscheck_prices`/`crosscheck_validator_degraded` wrappers (not a new module file —
the feature is too small to warrant one, per the "proportional scope" lesson from
Structural Scanner's Phase 1 sizing).

```python
def divergence_widened(today_gap_pct: float | None, prior_gap_pct: float | None,
                        min_widen_pp: float = 1.0) -> bool:
    """
    True if today's cross-check gap is at least min_widen_pp percentage points
    larger than the prior recorded gap. False if either value is None (can't
    compare), or if the gap has narrowed/stayed flat. Never raises.

    min_widen_pp is a display-annotation threshold, not a policy/gate value —
    it decides whether to APPEND a sentence to an existing banner, nothing more.
    """
    try:
        if today_gap_pct is None or prior_gap_pct is None:
            return False
        # float() coerce defensively — Supabase can return numeric columns as
        # JSON strings under some client configs; a bare subtraction would then
        # raise and silently drop the annotation via the except below (Round 2
        # Opus non-blocking note). Coercing here keeps the annotation robust
        # without weakening the "never raises, display-only" contract.
        return (float(today_gap_pct) - float(prior_gap_pct)) >= min_widen_pp
    except Exception:
        return False
```

`min_widen_pp` default (1.0) lives as a function default, not `constants.py` — same
class of value as `structural_scanner.py`'s `BLAST_RADIUS_SHOCK_PCT` (a what-if/display
threshold, never a gate).

---

## Supabase table: `price_xcheck_history`

```sql
create table if not exists public.price_xcheck_history (
    ticker           text        NOT NULL,
    check_date       text        NOT NULL,  -- ET ISO date via _today_et()
    primary_source   text,
    validator_source text,
    prev_gap_pct     numeric,
    live_gap_pct     numeric,
    ok               boolean     NOT NULL,
    created_at       timestamptz DEFAULT now(),
    PRIMARY KEY (ticker, check_date)
);
alter table public.price_xcheck_history enable row level security;
drop policy if exists "Allow all (service role)" on public.price_xcheck_history;
create policy "Allow all (service role)" on public.price_xcheck_history
    for all to service_role using (true) with check (true);
```

**DDL delivery:** manually applied once in Supabase (house pattern — no
`ensure_schema()`, no programmatic execution). Until the table exists,
`load_price_xcheck_history` returns `None`/empty and `save_price_xcheck_history_batch`
no-ops silently — the banner renders exactly as it does today, no trend annotation.

**db.py functions** (mirror `save_analyst_target_snapshots_batch`/
`load_analyst_target_snapshots` exactly — same upsert-on-conflict, same fail-soft
contract, **including the `_READONLY` guard** — Round 1 Opus finding: the plan's
first draft omitted this. Every new DB writer must no-op under `_READONLY` per the
read-only-viewer fail-safe chokepoint; this is mandatory, not optional, and must not
be dropped during implementation):

```python
def save_price_xcheck_history_batch(rows: list[dict]) -> None:
    """Persist today's price cross-check result per held ticker.
    Idempotent: upserts on (ticker, check_date) — repeated writes same day are
    no-ops in effect (overwrite with the same day's latest value). Never raises."""
    if _READONLY:
        return
    if not rows:
        return
    if not has_db():
        return
    try:
        _client().table("price_xcheck_history").upsert(
            rows, on_conflict="ticker,check_date",
        ).execute()
    except Exception:
        pass

def load_price_xcheck_history(ticker: str, before_date: str, days_back: int = 21) -> dict | None:
    """Return the most recent row for `ticker` strictly before `before_date`,
    within the last `days_back` calendar days, or None if no such row exists
    (table absent, DB offline, or genuinely no prior history yet). Never raises.

    Query shape: .eq("ticker", ticker) + .lt("check_date", before_date) +
    .gte("check_date", <before_date minus days_back>) + .order("check_date",
    desc=True) + .limit(1) — the ORDER + LIMIT is what makes this "most recent,"
    not insertion order."""
    if not ticker or not has_db():
        return None
    try:
        from datetime import date, timedelta
        cutoff = (date.fromisoformat(before_date) - timedelta(days=days_back)).isoformat()
        rows = (
            _client()
            .table("price_xcheck_history")
            .select("*")
            .eq("ticker", ticker)
            .lt("check_date", before_date)
            .gte("check_date", cutoff)
            .order("check_date", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None
```

---

## `app.py` wiring — one integration point

**Required import addition (Round 1 Opus finding):** add `divergence_widened` to the
existing `from stock_analyzer.data import (crosscheck_price, crosscheck_prices, ...)`
tuple at `app.py:47-54`. There is no bare `data` module alias anywhere in `app.py` —
`divergence_widened(...)` must be called bare, never as `data.divergence_widened(...)`.

Inside the existing "Price cross-check guardrail" block (`app.py:3019-3064`), two
additions, both scoped to the `if held_tickers:` branch that already computes `_xc`:

**1. Day-deduped write**, right after `_xc = _cached_price_xcheck(...)` (line 3034):

```python
_xc_today_str = str(_today_et())
if st.session_state.get("_price_xcheck_logged_date") != _xc_today_str and _xc:
    _xc_rows = [
        {
            "ticker": t,
            "check_date": _xc_today_str,
            "primary_source": r.get("primary_source"),
            "validator_source": r.get("validator"),
            "prev_gap_pct": r.get("prev_gap_pct"),
            "live_gap_pct": r.get("live_gap_pct"),
            "ok": bool(r.get("ok", True)),
        }
        for t, r in _xc.items()
    ]
    db.save_price_xcheck_history_batch(_xc_rows)
    st.session_state["_price_xcheck_logged_date"] = _xc_today_str
```

**Import fix (Round 1 Opus finding):** `app.py` imports `data` module functions via
`from stock_analyzer.data import (crosscheck_price, crosscheck_prices, ...)` — there is
no bare `data` module alias bound anywhere in `app.py`. `divergence_widened` MUST be
added to that same `from stock_analyzer.data import (...)` tuple (`app.py:47-54`) and
called bare as `divergence_widened(...)`, never as `data.divergence_widened(...)` — the
dotted form would raise `NameError` at runtime, and because it only executes on the
rare failing-cross-check path, `py_compile` would not catch it — it would ship silently
broken and fail exactly on the integrity-fault render it exists to serve.

**2. Trend annotation**, inside the existing `for t, r in _xc_bad.items():` loop
(`app.py:3038-3050`) — append a widening note to `_bits` **per failing leg**
(Round 1 Opus finding: the original draft compared `live_gap_pct` unconditionally,
but a ticker can be in `_xc_bad` via a failing `prev_ok` leg with `live_gap_pct=None`
or a passing live gap — annotating the wrong leg either silently drops the note via
the None-guard or attaches it to a leg that isn't actually the one failing). Compare
whichever leg(s) actually failed:

```python
_prior = db.load_price_xcheck_history(t, _xc_today_str, days_back=21)
if _prior:
    if r.get("prev_ok") is False and divergence_widened(
        r.get("prev_gap_pct"), _prior.get("prev_gap_pct")
    ):
        _bits.append(
            f"prior-close gap widened from {_prior.get('prev_gap_pct')}% to "
            f"{r.get('prev_gap_pct')}% since {_prior.get('check_date')}"
        )
    if r.get("live_ok") is False and divergence_widened(
        r.get("live_gap_pct"), _prior.get("live_gap_pct")
    ):
        _bits.append(
            f"live-price gap widened from {_prior.get('live_gap_pct')}% to "
            f"{r.get('live_gap_pct')}% since {_prior.get('check_date')}"
        )
```

`days_back=21` (Round 1 Opus finding: 7 days is too tight — a week-long absence plus a
holiday can push the last prior row past a 7-day window and silently drop the
comparison basis entirely; the read only ever *finds* a prior row, never fabricates
one, so a wider window is strictly safer).

No other page, no new tab, no new nav entry. The existing `st.error(...)` banner text
and structure (`app.py:3051-3056`) are unchanged — the widening note is simply one more
clause inside an already-rendered bullet line.

---

## Cost model

| Item | Per session | Per month |
|---|---|---|
| Price cross-check compute | $0 (already paid — reused, not duplicated) | $0 |
| New DB write (1x/session/day) | 1 Supabase upsert | ~20 upserts/month (1 active session/day) |
| New DB read (only when a ticker is failing cross-check) | 0-N Supabase reads, N = failing tickers | Negligible — cross-check failures are rare by design (that's the point of the tolerance bands) |
| LLM | $0 — none in this feature | $0 |

Cheapest of the four Agentic Intelligence features shipped/planned so far — zero LLM
dependency, reuses an already-paid-for computation, and the DB cost is bounded by how
often the cross-check actually fails (rare).

---

## What NOT to build in this plan

- **Fundamentals/forward-PE cross-check.** Deferred indefinitely per the user's
  scope decision — Finnhub structurally can't participate, and FMP's derived forward
  PE makes "divergence" ambiguous between a real data fault and a methodology
  difference. Would need its own dedicated plan + Opus review if ever revisited.
- **A genuine 3-source comparison for price.** The existing `crosscheck_price`/
  `crosscheck_batch` are pairwise (primary vs. one validator) by design — this plan
  does not change that; it only persists what's already computed.
- **Cron-based logging.** Would add a new per-ticker second-provider fetch to the
  premarket cron run — real, avoidable cost given the interactive path already
  computes this for free. Not revisited unless the interactive-only approach proves
  insufficient (e.g., if the app is rarely opened and history stays too sparse).
- **A new LLM narrative explaining *why* sources diverge.** The roadmap's own Idea #4
  section calls this "optional... very low LLM dependency" — explicitly the lowest
  priority part of the original idea, and skipped entirely here to keep this the
  cheapest, simplest feature in the roadmap.
- **Extending to the Stock Analysis page's cross-check caption** (non-held research
  tickers). Deferred — see Design Principle 6.
- **A "widening" threshold in `constants.py`.** `min_widen_pp=1.0` is a display
  threshold governing whether one sentence gets appended to an existing banner — never
  a gate, never a policy value.

---

## Phased build

| Phase | Scope | Gate |
|---|---|---|
| **Phase 1** | `divergence_widened()` in `data.py` + `db.py` DDL/functions + the two `app.py` additions to the existing guardrail block | Opus plan review → implement → Opus code review → ship |
| **Phase 2 (maybe, not committed)** | Extend to the Stock Analysis page's cross-check caption for non-held tickers | Only if Phase 1 proves valuable in practice — not a planned certainty |

No Phase 3 is proposed — this feature is small enough that Phase 1 closes the entire
gap the roadmap identified (persistent history + widening awareness), without needing
further staged rollout.

---

## Open design questions — resolved in Round 1 Opus review

1. **Day-dedup via session-state — RESOLVED: sufficient.** Single-user app;
   concurrent-tab safety comes from the `(ticker, check_date)` upsert idempotency
   regardless of session-state races. No change needed.
2. **`days_back` lookback window — RESOLVED: widen to 21 (was 7).** 7 days was too
   tight — a week-long absence plus a holiday can push the last prior row past the
   window and silently drop the comparison basis. Since the read only ever *finds*
   a prior row (never fabricates one), a wider window is strictly safer. Applied
   throughout this plan (see `load_price_xcheck_history` default + the trend-annotation
   call site above).
3. **Scoping to currently-failing tickers only — RESOLVED: correct, keep as-is.**
   Annotating a ticker whose gap is growing but hasn't crossed the failure tolerance
   would surface a NEW banner state (not currently shown) — that violates "strictly
   additive to an existing banner line" and reintroduces exactly the premature/
   sub-threshold noise the calm-advisor posture (§2B) rejects. Keep strictly scoped
   to tickers already in `_xc_bad`, but track the FAILING LEG specifically within
   them (see the per-leg fix in the app.py wiring section above — the original draft
   compared only `live_gap_pct` regardless of which leg actually failed).

**Non-blocking note (Round 1, addressed but not blocking):** `load_price_xcheck_history`
runs once per failing ticker on every Home rerun while a fault is active, not once per
session — the cost model's "negligible" framing is true in aggregate (failures are
rare) but understates per-rerun cost during an active fault. Acceptable for Phase 1;
a session-level memo dict (keyed on ticker+date) is a straightforward follow-up if it
ever proves to matter in practice.

---

## Review log

| Round | Model | Verdict | Blocking findings |
|---|---|---|---|
| Round 1 | Claude Opus 4.8 | FIX-FIRST | 3 blocking (dotted `data.divergence_widened()` call would NameError — no bare `data` module alias in app.py, must add to the existing import tuple; widening compared only `live_gap_pct` regardless of which leg actually failed, silently missing/misattributing the prev-close integrity-fault case; `save_price_xcheck_history_batch` spec omitted the mandatory `_READONLY` guard) + 2 non-blocking (days_back=7 too tight for a usage gap, widened to 21; per-rerun read cost during an active fault, acceptable) — all resolved in v2 |
| Round 2 | Claude Opus 4.8 | SHIP | 0 blocking — all 5 Round 1 items verified resolved and consistent throughout; 2 trivial non-blocking notes (a line-ref nit, a float() coercion robustness suggestion) addressed in v3; plan ready to implement |
