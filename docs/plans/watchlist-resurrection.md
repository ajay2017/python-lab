# O4 — Watchlist Resurrection — Design Plan

**Date:** 2026-07-26
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** SHIP (revised after Opus design review — FIX-FIRST round resolved). Ready
for implementation. `WATCHLIST_STALE_DAYS` policy value confirmed by the user: **30**.

> **Opus design review (round 1): FIX-FIRST** — 1 blocking finding, fixed in this
> revision. **Blocking:** the original draft computed the KPI count and the per-ticker
> caption from two different implicit predicates. `_wl_recs` (built from `list(_wl)`)
> has no held-ticker exclusion, and the existing "Already in Portfolio" override
> (`app.py:16552-16565`) only intercepts `ENTER_NOW` — a held `NEAR_ENTRY` ticker isn't
> covered by it at all. Left as originally drafted, a held+stale+`ENTER_NOW` ticker
> would be *counted* in the summary line but never *captioned* (its card is skipped by
> the existing override before O4's logic ever sees it) — the exact double-surface
> mismatch class this codebase has hit before (`feedback_single_surface_priority`,
> AVGO/MSFT). **Fixed:** one predicate function, computed once, drives both the count
> and every caption; held tickers (both `ENTER_NOW` and `NEAR_ENTRY`) are excluded from
> it entirely — you cannot "resurrect" a name you already own. Several non-blocking
> corrections also folded in below (timezone, re-add semantics, rationale accuracy,
> framing, Definition-of-Done checklist).

> **One-line spec:** On the existing 👁️ Watchlist page, highlight watchlist tickers
> that are both **old** (added ≥ `WATCHLIST_STALE_DAYS` ago — new constant, policy
> value) and **currently actionable** (`ENTER_NOW` or `NEAR_ENTRY` per the page's
> already-computed recommendation). These are names the user very plausibly added and
> then mentally filed away — surfacing them fights forgetting, not the market.

> **Roadmap context:** Priority 5 of [agentic-intelligence-roadmap-v2.md](agentic-intelligence-roadmap-v2.md)
> (Phase 3, "cheapest item on the board," parallel slot alongside D3).

---

## HEAD audit — the roadmap's one-liner needs a scope correction

The roadmap table describes O4 as: "Dead watchlist names now at the setup you
**originally wanted**." Verified against HEAD (2026-07-26): the `watchlist` table
(`db.py:64-68`) has exactly two columns, `ticker` and `added_at` — **there is no stored
"original setup" of any kind** (no target price, no signal snapshot, no reason-for-add
note) to compare today's price/signal against. `load_watchlist()` (`db.py:1038-1046`)
doesn't even select `added_at` today — only `ticker`.

**Correction: O4 cannot literally check "is it back at what I originally wanted,"
because that was never recorded.** The honest, buildable version is looser but still
real and still useful: *"this name has sat on your watchlist a long time without
action, and right now it independently qualifies as actionable."* That's not a
downgrade in spirit — it's the same "your own inaction, not the market" framing the
calm rule requires, just without inventing a comparison to a number that was never
saved.

**A second, better-than-expected finding:** the 👁️ Watchlist page (`app.py:16450+`)
already computes exactly the actionability signal needed, for every watchlist ticker,
on every page load — `build_watchlist_recommendation()` (defined in
`stock_analyzer/watchlist_advisor.py`, not `app.py` itself — the page just calls it)
classifies each ticker into `ENTER_NOW` / `NEAR_ENTRY` / `WAIT_ENTRY` / `WAIT_CATALYST`
/ `HOLD_OFF_EARNINGS` / `REMOVE` (`_wl_recs`, sorted and KPI'd at `app.py:16525-16542`),
returning a dict with `ticker`, `action`, `priority`, `score`, `price`, `entry_lo`,
`entry_hi`, `stop`, `rr` per ticker. **`ENTER_NOW` already means "the setup you were
waiting for is here, right now"** — this is a better primitive than reconstructing a
composite-score check from scratch. O4 doesn't need new analysis; it needs to know
**how long a ticker has been waiting** and cross that against a classification that
already exists in memory on this exact page render.

**No reconstruction of "original setup" is possible from any other table either** —
verified there is no watchlist-add-keyed snapshot anywhere in the schema.
`recommendations` (surfacing events: `new_pick`/`add_winner`/`buy_candidate`) and
`analyst_coverage.composite_score_at_save` are both keyed to different events (when
the *app* surfaced something, or when the user saved *research* — not when the user
personally added a ticker to the watchlist) and joining either in would misrepresent
someone else's timestamp as "your original ask." The reframe below is the honest
ceiling of what this data supports, not a shortcut.

**Net effect: this really is the cheapest item on the board.** No new LLM call
(confirmed: nothing here needs generation, same as D3), no new page, no new
`_parallel_load_all` calls — purely: (a) load `added_at` (one new lightweight read),
(b) a date-diff, (c) a highlight condition over data the page already has in hand.

---

## What already exists (reused, not rebuilt)

| Piece | Where | Status |
|---|---|---|
| Watchlist page + per-ticker analysis | `app.py:16450-16542`, `build_watchlist_recommendation()` | Shipped. Fully reused — O4 adds a filter/highlight over `_wl_recs`, not a parallel computation. |
| `added_at` column | `watchlist` table (`db.py:66-67`) | **Column exists in the DB, unused by the app today.** Needs one new read (below); no DDL. |
| Held-ticker set | `_wl_held` set (`app.py:16460-16462`) | Shipped. Reused directly by O4's own predicate (see below) — the existing "Already in Portfolio" override at `app.py:16552-16565` only covers `ENTER_NOW`, so O4 does **not** inherit held-exclusion for free on `NEAR_ENTRY` and must apply `_wl_held` itself (fixed per review finding). |

## What's genuinely new

1. **One new db read**: `load_watchlist_added_dates() -> dict[str, str]` (ticker →
   ISO `added_at` date). A separate function, not a change to `load_watchlist()`'s
   existing `list[str]` return shape (that function has callers throughout `app.py`
   that expect a flat list — changing its shape is an unnecessary, riskier edit for
   zero benefit). Selects `ticker, added_at` from the same table; same
   fail-soft-to-empty-dict pattern as `load_watchlist()`'s fail-soft-to-default-list.
2. **New policy constant** `WATCHLIST_STALE_DAYS = 30` in `constants.py` — the "how
   long is forgotten" threshold, **confirmed by the user**. (Note:
   `ANALYST_COVERAGE_FRESH_DAYS = 30` elsewhere in `constants.py` is not real precedent
   for this value — that constant means the *opposite* thing, "still fresh," for a
   passively-accumulated surface. A watchlist is actively curated by the user, so the
   two 30s sharing a number is coincidence, not a transferable rationale. 30 stands
   on the user's own judgment call, not on that analogy.)
3. **One resurrection predicate, computed once, driving both the count and every
   caption** (the fix for the blocking review finding): a ticker qualifies when
   `action in ("ENTER_NOW", "NEAR_ENTRY")` **AND** `days_since_added >= WATCHLIST_STALE_DAYS`
   **AND** `ticker not in _wl_held`. The held-exclusion applies to *both* actions —
   the existing "Already in Portfolio" override (`app.py:16552-16565`) only intercepts
   `ENTER_NOW`, so `NEAR_ENTRY` held tickers must be excluded explicitly by this new
   predicate rather than assumed to inherit that override. You cannot "resurrect" a
   name you already own; that's a different, already-handled decision moment.
   `days_since_added` is computed in **America/New_York** (`added_at` is a UTC
   `timestamptz`; convert before taking `.date()` and diffing against `_today_et()`,
   per the codebase's existing NY-timezone convention — CLAUDE.md, Streamlit Cloud
   runs UTC).
4. **A highlight, not a new card type**: for each ticker satisfying the predicate,
   prepend a small caption to its existing card — e.g. "You've watched this since
   `{added_at}` ({N}d) and it currently qualifies to enter" (no new card, no new
   section, no reordering of the existing REMOVE→ENTER_NOW→NEAR_ENTRY sort). Tickers
   with no readable `added_at` are silently skipped from the highlight. Verified in
   review: this is a defensive fallback, not a real data gap — `save_watchlist`'s
   insert payload is just `{"ticker": t}`, so the column's `default now()` fires on
   every legitimate row. **No backfill needed;** silent-skip guards against an
   unreadable value, not a migration concern. **Re-add resets the clock, by design:**
   `save_watchlist`'s upsert (`db.py:2080-2114`) only sends `{"ticker": t}` on
   conflict, so `added_at` is preserved for untouched rows, but a removed-then-re-added
   ticker gets a fresh `INSERT` → a new `added_at`. That's the *correct* clock for a
   forgetting-jog ("days since you last actually re-engaged with this name"), not a
   bug to fix later.
5. **One summary line** in the existing KPI strip area (`app.py:16530-16542`), driven
   by the SAME predicate's count: if ≥1 ticker qualifies, a small caption above the
   cards — "👁️ {N} watchlist name(s) you've been sitting on just became actionable" —
   otherwise nothing renders (no "0 found" noise).

**Explicitly NOT built:** no new page/tab, no new LLM call, no new cache table, no
change to `build_watchlist_recommendation()`'s classification logic, no comparison to
a stored "original setup" (never existed), no removal-history tracking.

---

## Definition of Done (CLAUDE.md, run in the same session as the build)

1. `WATCHLIST_STALE_DAYS` → add a row to the `docs/architecture.md` constants table
   (mechanically enforced by `scripts/check_constants_documented.py`).
2. New user-facing surface (the resurrection caption + KPI line) → an F-ID in
   `docs/requirements.md`.
3. Shipped-item entry in `docs/shipped-log.md`, and update this roadmap's status table
   ([agentic-intelligence-roadmap-v2.md](agentic-intelligence-roadmap-v2.md)).
4. In-app User Guide (`app.py`, `elif page == "📖 User Guide":`) bullet for the new
   caption.
5. Memory update noting the design corrections made this session (double-surface fix,
   the "no original setup" reframe) for future-session context.

---

## Design principles (non-negotiable, carried from v1/v2)

1. **Strictly additive.** No change to `build_watchlist_recommendation()`, the sort
   order, or the KPI counts themselves — purely an annotation layer.
2. **Calm rule.** The framing is "you added this and it's been sitting," never "act
   now" or any urgency language beyond what `ENTER_NOW`/`NEAR_ENTRY` already carry on
   their own. This feature adds a memory jog, not a new call to action. Per review:
   phrasing must stay pointed at the user's own forgetting ("you've watched this
   since...") rather than opportunity/market language ("became actionable" reads
   slightly market-triggered) — keep the subject the user's inaction, not the setup.
3. **Never fabricates staleness.** A ticker with no recorded `added_at` gets no
   highlight — absence of data is never treated as "very stale."
4. **Graceful degradation.** If the new `added_at` read fails (table unreachable,
   column missing on an old row), the existing Watchlist page renders exactly as it
   does today — this is a pure addition, and its failure mode is "no highlight," never
   a broken page.
5. **No new work created.** Zero new position sizing, zero new gate — `ENTER_NOW`'s
   existing position-sizing and gate logic is untouched; O4 only decides whether to
   show the resurrection caption next to it.
6. **One predicate, two surfaces.** The KPI count and every per-card caption must
   read from the exact same computed predicate (ticker set), never two independently
   filtered passes over `_wl_recs` — this was the blocking finding from design review
   and is the same double-surface bug class this codebase has hit before.

## Non-goals

- Does not compare current price/signal to any stored "original setup" — that data
  was never captured and this plan does not retroactively invent it.
- Does not track or surface watchlist *removals* (a ticker taken off the list is just
  gone — no "you removed this and it later ran" retrospective; that's a distinct,
  separate feature this plan does not build).
- Does not change how or when a ticker gets added to/removed from the watchlist.
- Does not run on held positions — O4's own predicate explicitly excludes `_wl_held`
  tickers for both `ENTER_NOW` and `NEAR_ENTRY` (the existing "Already in Portfolio"
  override only covers the former, so O4 cannot rely on it alone).
