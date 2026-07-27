# Structural Vulnerability Scanner — Phase 2 Design Plan

**Date:** 2026-07-27
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** Plan SHIP 2026-07-27 (3 Opus rounds) — ready for implementation

> **One-line spec:** A 🏠 Home "Structural alert" banner that fires when today's
> live correlation clusters contain a ticker pairing that was NOT present in the
> most recent `structural_scan_cache` snapshot on or before today — i.e., a
> genuinely new co-movement relationship has formed among held positions since
> the Structural Scan narrative was last generated.

---

## Gate status (confirmed before drafting this plan)

Phase 1 shipped 2026-07-24 (commit `9b47117`). The plan's own phased-build gate
(`docs/plans/structural-vulnerability-scanner.md:404`) requires BOTH:

1. **Phase 1 stable in production ≥3 days** — satisfied (today is 2026-07-27).
2. **≥2 days of `structural_scan_cache` history accumulated** — satisfied, verified
   directly against Supabase: rows exist for `scan_date` = `2026-07-24` and
   `2026-07-27` (user-run query, 2026-07-27).

**Non-obvious wrinkle this confirms, and this plan must design around:** there is
a **gap** — no rows for 2026-07-25 or 2026-07-26. This is expected, not a bug —
the narrative (and the `structural_scan_cache` row it writes) is button-gated,
not automatic, so a row only lands on a day the user actually opens 🧩 Intelligence
→ 🧬 Structural Scan and clicks "🧬 Generate structural narrative." **Phase 2
therefore cannot compare "today vs. `scan_date = today - 1 day`"** — that would
find nothing on any day following a gap, silently disabling the whole feature.
It must compare **"today vs. the most recent prior scan, whatever calendar date
that is."**

---

## Design principles (carried forward from Phase 1, still non-negotiable)

1. **Strictly additive.** Nothing here modifies the composite score, a gate, or
   any recommendation. Awareness only, matching Day Shock's precedent
   (`app.py:3224-3265`) — the most recent Home-page banner of this exact kind.
2. **Reuse, don't reimplement.** `portfolio_intelligence.correlation_clusters()`
   is called exactly as the 🧬 Structural Scan tab already calls it — same
   inputs (`_corr_df_cache`, a `{ticker: weight_pct}` map), no forked logic.
3. **Zero new data fetches, zero new LLM calls.** This is a pure-Python diff
   between two already-computed cluster lists (today's live one + the stored
   prior snapshot). Cheapest item on the whole Agentic Intelligence roadmap —
   even cheaper than Phase 1's Blast Radius, which at least runs a Haiku call
   for the narrative. Matches O4 Watchlist Resurrection / D3 Signal Coherence's
   precedent of a zero-LLM, zero-new-cache-table roadmap item.
4. **Never fabricates.** The banner cites the exact new ticker pair(s) that
   triggered it — never a vague "risk detected" claim with no basis, matching
   the house rule that a recommendation/alert must show its basis.
5. **Graceful degradation.** No prior snapshot exists → banner doesn't render
   (nothing to compare against; showing "everything is new" on the very first
   comparison would be a false-positive flood, not a real finding). Live
   correlation data unavailable this session → banner doesn't render (mirrors
   the existing "revisit Home to compute it" pattern used elsewhere).
6. **Asymmetry + calm rule (from the v2 roadmap's own governing principles).**
   Flag only NEW risk formation — a cluster **losing** a member (decorrelation)
   is not flagged; that's risk *reducing*, not forming, and isn't this banner's
   job. See "What counts as new" below for why this rules out naive whole-set
   comparison.
7. **Opus review required** before build (this plan) and before ship (code
   review) — same process as every other item on this roadmap.

---

## What counts as "new" — the core design decision

`correlation_clusters()` returns clusters as `{"tickers": [...], "size", "avg_internal_corr",
"combined_weight_pct", "tier"}` (`portfolio_intelligence.py:30-60`). Comparing
whole ticker-sets directly is too brittle: correlations wobble day to day, so a
cluster gaining or losing one borderline member would look like an entirely
"new" cluster even though most of its risk relationship already existed and was
already narrated once.

**Resolved approach: compare at the ticker-PAIR level, not the whole-cluster-set
level.**

- Build `prior_pairs` = the set of every ticker pair that co-occurred inside any
  cluster in the prior snapshot (all pairwise combinations within each stored
  cluster's `tickers` list).
- For each cluster in today's live list, compute its own pairs the same way.
- A cluster is flagged **new** if it contains at least one pair not in
  `prior_pairs` — i.e., at least one specific pair of held tickers that is
  correlated together today (≥`CORR_HIGH_PAIRS_THRESHOLD`, already enforced by
  `correlation_clusters()` itself) but was NOT correlated together as of the
  last scan.

This correctly:
- Flags a brand-new 2-ticker cluster where neither name was clustered before.
- Flags an existing cluster that **gained** a new member (the new pairs it forms
  with existing members are genuinely new information).
- Does **NOT** flag a cluster that only **lost** a member, or an identical
  cluster re-detected with no membership change — neither is "new risk," so
  neither should interrupt the user (calm rule).

---

## New pure function: `structural_scanner.detect_new_clusters()`

**Revised after Round 1 Opus review (FIX-FIRST, 3 blocking — see Review log).**

Lives in the **same** `stock_analyzer/structural_scanner.py` module (not a new
file) — this is a direct extension of Phase 1's domain, not a separate concept,
matching how other roadmap items extended an existing module for their own
Phase 2 (e.g., Debate's exit-debate Phase 2 extended `debate_agent.py` rather
than forking a new module).

**Round 1 finding, fixed:** `correlation_clusters()` builds clusters via
*transitive* connected components (`portfolio_intelligence.py:82-99`) — a
cluster's member set can contain pairs that are only linked through a common
third name, not directly correlated themselves. Enumerating
`combinations(members, 2)` and citing all of them in the banner would
fabricate a direct-co-movement claim for pairs that were never actually
checked against each other (e.g., citing "A-C" when only A-B and B-C are real
edges). We do not have the prior day's raw correlation matrix stored (only
`cluster_snapshot`'s member lists), so we cannot verify whether a pair was a
*direct* edge as of the prior scan — but we CAN verify whether it's a direct
edge **today**, using the already-available `corr_df`. Fix: only cite a pair
in `new_pairs` when (a) it wasn't already co-clustered in the prior snapshot
(verifiable from `cluster_snapshot`) AND (b) it IS a direct edge today
(verifiable from today's own data). This never claims anything about the
pair's correlation history — only an honest, checkable statement about today.

**Round 2 finding, fixed:** the first attempt at (b) used `abs(corr_df.loc[a,b])
>= threshold`, which reintroduced a fabrication of the same class — just via
sign instead of transitivity. `correlation_clusters()` forms an edge on
**signed** `corr >= threshold` (positive co-movement only —
`portfolio_intelligence.py:78,120`; `blast_radius()`'s own `abs(corr)` gate at
`structural_scanner.py:37` serves a different purpose, cascade-*magnitude*
eligibility with signed comove, and doesn't justify `abs` here). Using `abs()`
would let a **negatively**-correlated pair through: along a transitive chain
A-B-D-C where each consecutive link is a real positive edge, A and C can be
sharply anti-correlated yet still land in the same cluster — citing "new
pairing: A-C" for two names that move *opposite* each other is exactly the
false co-movement claim this fix exists to prevent. Corrected to the signed
check, `corr_ab >= threshold` (no `abs`). Never under-flags: the genuinely-new
direct edge that causes a member to join a cluster is always positive
`>= threshold` by `correlation_clusters()`'s own construction, so the real
driver behind any new membership is always retained — only anti-correlated
transitive pairs are (correctly) excluded from citation.

```python
def detect_new_clusters(today_clusters: list[dict], prior_cluster_snapshot: list[dict] | None,
                         corr_df, threshold: float = CORR_HIGH_PAIRS_THRESHOLD) -> list[dict]:
    """
    Compare today's live correlation_clusters() output against the most recent
    structural_scan_cache snapshot's cluster_snapshot (see
    db.load_structural_scan_baseline() -- "most recent scan on or before
    today", not strictly "prior", per the Round 1 fix below). Returns the
    subset of today's clusters containing at least one ticker PAIR that (a)
    was NOT already co-clustered in the baseline snapshot and (b) IS a direct
    positive edge today (corr_df.loc[a,b] >= threshold, SIGNED not abs() --
    matches correlation_clusters()'s own edge condition) -- pair-level
    comparison restricted to verifiable direct positive edges, so a cluster
    losing a member (decorrelation) is never flagged, an anti-correlated
    transitive pair is never cited as if it were a real co-movement pairing,
    and a transitively-linked pair that was never actually checked against
    each other is never cited as if it were.

    prior_cluster_snapshot: the "cluster_snapshot" field of the row returned by
    db.load_structural_scan_baseline() -- or None if NO scan has ever been
    generated (returns [] in that case; a first-ever comparison with nothing
    to diff against is not "everything is new"). An empty list [] (a real
    scan ran and found zero clusters that day) is a DIFFERENT, meaningful
    state -- see the resolved open question below -- and is NOT treated the
    same as None.

    Returns a list of dicts, each a copy of the matching today_clusters entry
    plus one new key:
        "new_pairs": [[tickerA, tickerB], ...]  -- the specific, verified-
                     direct pair(s) driving the "new" flag, sorted, for
                     citation in the banner (never a bare "new cluster" claim
                     with no basis, and never citing an unverified
                     transitively-linked pair).
    Never raises -- degrades to [] on any missing/malformed input.
    """
```

**Implementation:**

```python
from itertools import combinations

def detect_new_clusters(today_clusters, prior_cluster_snapshot, corr_df,
                         threshold=CORR_HIGH_PAIRS_THRESHOLD):
    try:
        if prior_cluster_snapshot is None or not today_clusters:
            return []
        if corr_df is None or getattr(corr_df, "empty", True):
            return []

        prior_pairs = set()
        for c in prior_cluster_snapshot:
            members = sorted(c.get("tickers") or [])
            for a, b in combinations(members, 2):
                prior_pairs.add(frozenset((a, b)))

        flagged = []
        for c in today_clusters:
            members = sorted(c.get("tickers") or [])
            new_pairs = []
            for a, b in combinations(members, 2):
                if frozenset((a, b)) in prior_pairs:
                    continue  # already co-clustered as of the baseline
                if a not in corr_df.index or b not in corr_df.columns:
                    continue
                try:
                    corr_ab = float(corr_df.loc[a, b])
                except Exception:
                    continue
                if corr_ab != corr_ab:  # NaN
                    continue
                if corr_ab >= threshold:  # SIGNED, not abs() -- see Round 2 fix note below
                    new_pairs.append(sorted((a, b)))
            if new_pairs:
                flagged.append({**c, "new_pairs": new_pairs})

        return flagged
    except Exception:
        return []
```

Every genuinely new member's arrival in a cluster is, by `correlation_clusters()`'s
own construction, driven by at least one real direct edge to an existing
member — so this filter never silently drops a cluster that should be flagged,
it only prevents citing an unverified transitive pair instead of the real one.

**Resolved open question (was open pre-Round-1, now decided):**
`prior_cluster_snapshot is None` (no scan has ever run) suppresses the
feature entirely — nothing to diff against, and treating a first-ever
comparison as "everything is new" would be a false-positive flood. But
`prior_cluster_snapshot == []` (a real scan ran and found zero clusters) is
**not** the same state and must **not** be suppressed — going from zero
clusters to some clusters is itself the cleanest possible new-formation
signal, and the save path only ever writes `cluster_snapshot` after a
successful scan with usable data (`app.py:11081-11138`), so a stored `[]` is
never a fabricated "we don't know" placeholder. The `is None` check (not
`not prior_cluster_snapshot`) implements this correctly — an empty
`prior_pairs` set from `[]` naturally causes every one of today's real
clusters to flag as new, with no special-cased branch needed.

---

## New `db.py` function: fetch the comparison baseline scan

**Revised after Round 1 Opus review (blocking finding #2).** The original
draft used `.lt("scan_date", before_date)` — strictly *before* today. That
has a real bug: once the user generates today's own narrative (saving a
`scan_date = today` row), a strict `.lt` still skips right past it and keeps
returning 07-24 as the comparison point — the banner would keep flagging the
same clusters the user just reviewed for the rest of the day, and the copy
would print a **false** "since your last Structural Scan (2026-07-24)" when
today's own scan is the user's actual most recent one. Fix: use `.lte` (on or
before today) so today's own snapshot becomes the baseline once it exists —
live clusters then compare against themselves, correctly producing zero new
pairs, and the banner clears for the rest of the day exactly as it should
once the user has reviewed today's scan. Renamed from `load_prior_...` to
`load_structural_scan_baseline` since "prior" was no longer accurate.

`db.load_structural_scan_cache(scan_date)` only fetches an exact-date row —
there's no existing way to ask "what's the most recent row on or before
today." New function, mirroring the existing one's guard/error pattern
exactly:

```python
def load_structural_scan_baseline(as_of_date: str) -> dict | None:
    """Return the most recent structural_scan_cache row with scan_date <=
    as_of_date, or None (no scan has ever run, table absent, or DB offline).

    Deliberately <=, not < -- once today's own narrative has been generated,
    today's own snapshot IS the correct baseline (comparing live clusters
    against themselves correctly yields zero new pairs, clearing the Home
    banner for the day). Using strict < would keep comparing against a stale
    prior day even after the user has reviewed today's scan.

    Only cluster_snapshot + scan_date are needed by Phase 2 -- narrative and
    the other JSONB columns from that historical row are not read here.
    Never raises.
    """
    if not as_of_date or not has_db():
        return None
    try:
        rows = (
            _client()
            .table("structural_scan_cache")
            .select("scan_date,cluster_snapshot")
            .lte("scan_date", as_of_date)
            .order("scan_date", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    except Exception:
        return None
```

Uses `.limit(1).execute().data`, not `.maybe_single()` — the latter is
unavailable on Streamlit Cloud and fails silently (house lesson, already
burned once elsewhere). No new table, no new RLS policy — reads the existing
`structural_scan_cache` (system cache, not `_READONLY`-gated, same as Phase 1).

---

## `app.py` wiring — 🏠 Home

### Placement

**After** the block that publishes `_corr_df_cache` for the current render
converges (both the `_home_synth_cache` HIT branch, `app.py:3792`, and the
cold-load MISS branch, `app.py:3898` — both set `st.session_state["_corr_df_cache"]`
before this point). This matters: the existing Day Shock banner
(`app.py:3224-3265`) renders **before** that publish point in the current
file, so if this new banner were placed there instead, it would read whichever
`_corr_df_cache` value survived from the *previous* rerun rather than the one
this render just computed. Placing it after the HIT/MISS branches converge
avoids that staleness class of bug (the same "fixed in one place, stale
elsewhere" pattern Opus has caught on this roadmap before — P5's Round 2
review, D1's `.iloc[-1]` fix). Exact line TBD at implementation — confirm
against HEAD at build time, not against this plan's line numbers.

### Logic

**Revised after Round 1 Opus review:** `corr_df` now threaded into
`detect_new_clusters()` (blocking finding #1); baseline lookup uses the
renamed, `.lte`-based `load_structural_scan_baseline()` (blocking finding
#2); `_structural_alert_cache` is `None` on the offline path rather than `[]`,
distinguishing "correlation data unavailable" from "checked, found nothing" —
matching CLAUDE.md's coordination-pattern convention (non-blocking finding).

```python
_sa_corr_df = st.session_state.get("_corr_df_cache")
if _sa_corr_df is not None and not _sa_corr_df.empty:
    _sa_weights = dict(zip(port_df["Ticker"], port_df["Weight (%)"]))
    _sa_clusters_today = portfolio_intelligence.correlation_clusters(_sa_corr_df, _sa_weights)
    _sa_baseline = db.load_structural_scan_baseline(str(_today_et()))
    _sa_baseline_snapshot = _sa_baseline.get("cluster_snapshot") if _sa_baseline else None
    _sa_new_clusters = structural_scanner.detect_new_clusters(
        _sa_clusters_today, _sa_baseline_snapshot, _sa_corr_df
    )
else:
    _sa_baseline = None
    _sa_new_clusters = None  # offline -- distinct from "[] checked, found nothing"

st.session_state["_structural_alert_cache"] = _sa_new_clusters  # new coordination-pattern key

if _sa_new_clusters:
    _sa_baseline_date = _sa_baseline.get("scan_date")
    st.warning(
        f"🧬 **Structural alert — {len(_sa_new_clusters)} new correlation "
        f"cluster{'s' if len(_sa_new_clusters) != 1 else ''} formed since your "
        f"last Structural Scan ({_sa_baseline_date})**  \n"
        "Awareness only — composite scores and gates are unaffected. "
        "See 🧩 Intelligence → 🧬 Structural Scan for the full picture."
    )
    for c in _sa_new_clusters:
        _pairs_str = ", ".join(f"{a}-{b}" for a, b in c["new_pairs"])
        st.caption(
            f"**{', '.join(c['tickers'])}** ({c['tier']}, "
            f"{c['combined_weight_pct']:.1f}% combined weight) — new pairing: {_pairs_str}"
        )
```

Copy deliberately says **"since your last Structural Scan ({date})"**, not
"since yesterday" — the gap in the data (07-25/07-26 had no row) makes "since
yesterday" a false-precision claim the app must not make (documentation/UI
copy is held to the same zero-fabrication bar as docs, per house convention).
Because the baseline is now `.lte`-based, `{_sa_baseline_date}` correctly
shows **today's own date** once the user has generated today's narrative —
never a stale prior date after today's scan exists.

**New coordination-pattern cache key:** `_structural_alert_cache` — published
every Home render: `None` when correlation data is unavailable this session
(producer-failure convention), `[]` when checked and nothing new was found,
or the non-empty flagged-clusters list. Nothing currently consumes it
downstream, but it's published proactively per the "producer publishes to
session_state" pattern CLAUDE.md's coordination section requires, and to
leave the door open for a future consumer (e.g., if 🧩 Intelligence wants to
show "already alerted on Home" instead of re-deriving the same thing). Must
be added to CLAUDE.md's cache-key list at ship time (Definition of Done).

**Known, deliberate scope limitation (non-blocking, Round 1 review):** an
existing pair whose correlation *intensifies* (e.g., escalates from
"warning" to "danger" tier without any membership change) is not caught by
this design — only genuinely new pairings are. This is intentional ("new
pairing," not "intensifying pairing") but should be recorded in memory/
shipped-log at ship time so it isn't later mistaken for a bug.

### Why this is safe to compute unconditionally, unlike the narrative

The narrative required button-gating because it's an LLM call that would fire
on every rerun without it (Design Principle 4, Phase 1 plan). `detect_new_clusters`
and `correlation_clusters` are both pure Python, already re-computed on every
Home render for other purposes (`corr_df`/`div_score` etc. at `app.py:3884-3901`)
— this adds no new cost class, exactly like Phase 1's Blast Radius Map being
safe to compute unconditionally while its narrative was not.

---

## What NOT to build in this plan

- **A user-facing "dismiss" or snooze control.** Day Shock has none either;
  the banner simply stops appearing once the underlying condition clears (a
  later scan's snapshot no longer produces new pairs against the one before
  it). Keeps this Phase 2 exactly as simple as its Phase 1 sibling behavior.
- **Retroactively backfilling the 07-25/07-26 gap.** Not possible (the data
  was never computed) and not necessary — the "most recent scan on or before
  today" baseline design handles gaps naturally without needing complete
  daily coverage.
- **A severity-based mute (e.g., only "danger"-tier clusters).** Both tiers
  are eligible for the "new pairing" check; tier is shown in the banner body
  for context, not used as a pre-filter. A newly-forming "warning"-tier
  cluster is still new information, and the asymmetry rule favors catching
  formation early over waiting for it to escalate to "danger."
- **Changing `structural_scan_cache`'s schema.** Phase 2 only adds a new read
  function against the existing table; no new column, no new table.

---

## Cost model

| Item | Per portfolio/day |
|---|---|
| `correlation_clusters()` recompute | $0 (already computed elsewhere this render) |
| `detect_new_clusters()` diff | $0 (pure Python, O(clusters × cluster size²)) |
| `load_structural_scan_baseline()` | $0 (one Supabase read, no LLM) |

Zero marginal cost — the cheapest item shipped on this whole roadmap.

---

## Definition of Done checklist for this feature (per CLAUDE.md)

1. No new/changed `constants.py` entries — nothing to add to the architecture
   constants table (reuses `CORR_HIGH_PAIRS_THRESHOLD` transitively via
   `correlation_clusters()`, doesn't introduce a new one).
2. New user-facing surface (the Home banner) → needs a new F-ID row in
   `docs/requirements.md` at ship time.
3. Not a previously-queued "What's queued" item being closed via shipped-log —
   it IS the queued item (`CLAUDE.md`'s "Structural Vulnerability Scanner
   Phase 2 (F-198)" line) — remove that line from "What's queued" and add a
   `docs/shipped-log.md` entry once shipped.
4. User-visible behavior changed → add a short mention to the in-app User
   Guide's Home section.
5. Non-obvious rationale (pair-level vs. whole-set comparison; "since last
   scan" vs. "since yesterday" copy; gap-handling) → capture in memory
   `project_agentic_intelligence_roadmap` once shipped.
6. No further phase gated on a future trigger — this closes the last open
   phase of the three named in CLAUDE.md's "What's queued."

---

## Review log

| Round | Model | Verdict | Blocking findings |
|---|---|---|---|
| Round 1 | Claude Opus (reviewer subagent) | FIX-FIRST | 3 blocking: (1) `new_pairs` citation could include transitively-linked pairs that were never directly correlated — fabrication risk; fixed by threading `corr_df` into `detect_new_clusters` and only citing pairs verified as a direct edge today. (2) `.lt(before_date)` never resolves to today's own scan once it exists, so the banner would keep flagging already-reviewed clusters and misdate the "since your last scan" copy; fixed by switching to `.lte` and renaming the function `load_structural_scan_baseline`. (3) `prior_cluster_snapshot == []` (a real scan found zero clusters) must NOT be suppressed like `None` (no scan ever ran) — fixed via `is None` guard. + 4 non-blocking, folded in: `_structural_alert_cache` now `None`-on-offline distinct from `[]`-on-checked; escalating-tier limitation now documented; cache-key CLAUDE.md reminder retained; NameError risk on `_sa_prior`/`_sa_baseline` reference confirmed safe. |
| Round 2 | Claude Opus (reviewer subagent) | FIX-FIRST | 1 blocking: Fix #1's direct-edge check used `abs(corr_ab) >= threshold`, but `correlation_clusters()` forms edges on **signed** `corr >= threshold` (positive co-movement only) — `abs()` let an anti-correlated transitive pair through, fabricating a "new pairing" claim for two names that move opposite each other (same fabrication class Round 1 flagged, via sign instead of transitivity). Fixed: changed to signed `corr_ab >= threshold` (no `abs`). Confirmed correct: `.lte` self-comparison clears the banner once today's own scan exists; `is None` guard correctly distinguishes `[]` from `None`; index/columns guard correct for `.loc[a,b]`; no downstream consumer of `_structural_alert_cache` to break on `None` vs `[]`. + 2 non-blocking doc-name nits (stale `load_prior_structural_scan_cache()` references) — fixed. |
| Round 3 | Claude Opus (reviewer subagent) | SHIP | 0 blocking. Confirmed `detect_new_clusters`'s `new_pairs` path uses signed `corr_ab >= threshold`, matching `correlation_clusters()`'s edge condition; no `abs()` remains in the computation path; NaN/index guards intact. Fixes from Rounds 1-2 all verified resolved. **Ready for implementation** — the resulting code still requires the standard pre-ship Opus code review per CLAUDE.md Hard Rule #4 before it ships. |
