# O4 — Watchlist Resurrection — Design Plan

**Date:** 2026-07-26
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** DRAFT — pending Opus design review. One open policy question for the user
(the staleness threshold) flagged below.

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
on every page load — `build_watchlist_recommendation()` classifies each ticker into
`ENTER_NOW` / `NEAR_ENTRY` / `WAIT_ENTRY` / `WAIT_CATALYST` / `HOLD_OFF_EARNINGS` /
`REMOVE` (`_wl_recs`, sorted and KPI'd at `app.py:16525-16542`). **`ENTER_NOW` already
means "the setup you were waiting for is here, right now"** — this is a better
primitive than reconstructing a composite-score check from scratch. O4 doesn't need
new analysis; it needs to know **how long a ticker has been waiting** and cross that
against a classification that already exists in memory on this exact page render.

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
| Held-ticker exclusion | `_wl_held` set (`app.py:16460-16462`), and the existing "Already in Portfolio" override at `app.py:16552-16565` | Shipped — already prevents an `ENTER_NOW` card from double-counting a ticker that's already been bought. O4 inherits this for free since it operates on the same `_wl_recs` loop. |

## What's genuinely new

1. **One new db read**: `load_watchlist_added_dates() -> dict[str, str]` (ticker →
   ISO `added_at` date). A separate function, not a change to `load_watchlist()`'s
   existing `list[str]` return shape (that function has callers throughout `app.py`
   that expect a flat list — changing its shape is an unnecessary, riskier edit for
   zero benefit). Selects `ticker, added_at` from the same table; same
   fail-soft-to-empty-dict pattern as `load_watchlist()`'s fail-soft-to-default-list.
2. **New policy constant** `WATCHLIST_STALE_DAYS` in `constants.py` — the "how long is
   forgotten" threshold. **Open question for the user** (see below); proposing `30`
   as a starting point (matches the existing `ANALYST_COVERAGE_FRESH_DAYS = 30`
   precedent for "how long is something still fresh/top-of-mind" elsewhere in this
   codebase), adjustable before ship.
3. **A highlight, not a new card type**: for each ticker in `_wl_recs` where
   `action in ("ENTER_NOW", "NEAR_ENTRY")` AND `days_since_added >= WATCHLIST_STALE_DAYS`,
   prepend a small "🪄 Resurrected — added `{added_at}`, {N} days ago" caption to the
   existing card (no new card, no new section, no reordering of the existing
   REMOVE→ENTER_NOW→NEAR_ENTRY sort). Tickers with no `added_at` on record (pre-existing
   rows from before this feature, or a failed read) are silently skipped from the
   highlight — never treated as "infinitely stale" or "never stale," just not
   annotated.
4. **One summary line** in the existing KPI strip area (`app.py:16530-16542`): if ≥1
   ticker qualifies, a small caption above the cards — "🪄 {N} watchlist name(s) you
   added a while ago just became actionable" — otherwise nothing renders (no "0 found"
   noise).

**Explicitly NOT built:** no new page/tab, no new LLM call, no new cache table, no
change to `build_watchlist_recommendation()`'s classification logic, no comparison to
a stored "original setup" (never existed), no removal-history tracking.

---

## Open policy question for the user (per CLAUDE.md rule 1)

`WATCHLIST_STALE_DAYS` is a threshold decision, not an engineering detail. Proposing
**30 days** as the default (long enough that a user genuinely forgot, short enough to
still be timely), but this is the user's call before ship — same treatment as every
other new constant on this roadmap.

---

## Design principles (non-negotiable, carried from v1/v2)

1. **Strictly additive.** No change to `build_watchlist_recommendation()`, the sort
   order, or the KPI counts themselves — purely an annotation layer.
2. **Calm rule.** The framing is "you added this and it's been sitting," never "act
   now" or any urgency language beyond what `ENTER_NOW`/`NEAR_ENTRY` already carry on
   their own. This feature adds a memory jog, not a new call to action.
3. **Never fabricates staleness.** A ticker with no recorded `added_at` gets no
   highlight — absence of data is never treated as "very stale."
4. **Graceful degradation.** If the new `added_at` read fails (table unreachable,
   column missing on an old row), the existing Watchlist page renders exactly as it
   does today — this is a pure addition, and its failure mode is "no highlight," never
   a broken page.
5. **No new work created.** Zero new position sizing, zero new gate — `ENTER_NOW`'s
   existing position-sizing and gate logic is untouched; O4 only decides whether to
   show the "🪄 Resurrected" caption next to it.

## Non-goals

- Does not compare current price/signal to any stored "original setup" — that data
  was never captured and this plan does not retroactively invent it.
- Does not track or surface watchlist *removals* (a ticker taken off the list is just
  gone — no "you removed this and it later ran" retrospective; that's a distinct,
  separate feature this plan does not build).
- Does not change how or when a ticker gets added to/removed from the watchlist.
- Does not run on held positions — the existing "Already in Portfolio" override
  already handles that case before O4's highlight logic would ever see it.
