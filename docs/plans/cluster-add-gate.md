# New-correlation-cluster add-suppression gate (G-25)

**Status: SHIPPED 2026-10-06.** Designed (Opus `planner`, all 7 owner decisions confirmed same day) → built (`implementer`, Sonnet 5) → Opus `reviewer` round 1: FIX-FIRST, 1 blocking (three places still claimed the Structural alert banner was "awareness only" — the in-app User Guide, `docs/requirements.md` F-218, `docs/user-manual.md` — stale the moment this shipped; plus 6 non-blocking findings), all fixed same session → round 2: SHIP, 0 blocking. Full suite 7176 passed throughout; antipattern/constants-doc gates green. Memory `project_structural_cluster_add_gate`.

## Origin

A live Home screenshot showed the "Structural alert" banner firing for a newly-formed
warning-tier correlation cluster (CRM/WDAY/WK, 13.8% combined weight, new pairs
CRM-WK and WDAY-WK). Tracing the code found this banner is pure disclosure — only
**danger**-tier pairs (`CORR_DANGER_PAIRS_THRESHOLD`=0.80) trigger any actual
recommendation today (`portfolio.diversification_recommendations`'s `PAIR_RISK`
branch). A **warning**-tier cluster (any pair ≥`CORR_HIGH_PAIRS_THRESHOLD`=0.65)
gets zero downstream action — the Home banner itself says "Awareness only —
composite scores and gates are unaffected," and that's accurate today.

Four options were discussed (do nothing / a named trim suggestion / a soft
suppression gate / a hard new concentration ceiling). Owner chose the suppression
gate as Phase 1 — cheapest, lowest blast radius, reuses an already-reviewed
suppression pattern (same shape as G-02/G-04/G-09) — with a trim suggestion
explicitly deferred to a Phase 2 gated on evidence, not built today.

## What ships in Phase 1 (G-25)

When `structural_scanner.detect_new_clusters()` flags a newly-formed warning-tier
pair since the last acknowledged 🧬 Structural Scan, suppress Grow Today's
"add-to-winner" suggestion for the two tickers at the endpoints of that specific
new pair (not every transitive member of the broader cluster) — **only if** they
are already held. This is purely a suppression — **no trim/sell recommendation is
made**, and the hard concentration ceilings (`SINGLE_NAME_CEILING`,
`SECTOR_CEILING`) are completely unaffected and stay fully live regardless of this
gate's state.

## The 7 owner decisions (all confirmed 2026-10-06)

- **D1 — gate lifetime: "review-before-add," not a fixed expiry.** The suppression
  lasts until the owner either runs a fresh Structural Scan (which refreshes the
  baseline `detect_new_clusters()` diffs against) or the pair's live correlation
  drops back below 0.65 on its own. No new "first seen" date is tracked. Two
  disclosed caveats: if the Haiku narrative call is down, the owner can't
  acknowledge/lift it that day; if a scan already ran today, the button won't
  refresh again until tomorrow. A day-count expiry was considered and rejected —
  it needs new state nothing tracks today, and it inverts the incentive (neglecting
  to scan would turn the gate OFF, not on). A broader "suppress on any current
  cluster, new or not" option was considered and rejected as scope creep past
  Phase 1 — it would need its own separate design.
- **D2 — scope: only the verified new pair's two endpoints**, not every member of
  the broader cluster. Matches `detect_new_clusters()`'s own "never cite an
  unverified pair" discipline — every suppressed ticker has its own direct new
  edge ≥0.65. In the live CRM/WDAY/WK case this gives the identical result to the
  broader "every cluster member" reading, but will diverge on a larger cluster.
- **D3 — no new combined-weight floor, no new constant.** The gate exists so the
  banner and Grow Today never contradict each other; a floor would reopen exactly
  that contradiction for small clusters. Zero new `constants.py` values in this
  feature — `CORR_HIGH_PAIRS_THRESHOLD` is reused as-is.
- **D4 — fails OPEN on a baseline-data outage, with a visible caption.** Adds
  proceed normally, captioned "couldn't check." The hard ceilings (G-04 single-name,
  the sector cap) are unaffected by this gate's own health and stay fully live.
- **D5 — Phase 1 covers only the two Brief add-lanes** (`_grow_today`'s bull-day
  add loop, `_buy_candidates`'s add-to-winner lane). Rebalancer drift-ADD and
  Analysis's own add-sizing lane (the G-18 surface) are NOT covered — recorded as a
  known, disclosed gap in CLAUDE.md's queue, not silently missed.
- **D6 — a separate, PRE-EXISTING coordination gap found during design, NOT fixed
  here:** a danger-tier `PAIR_RISK` trim call (0.80+ correlation) is today not
  read by Grow Today's add-to-winner logic at all — `_trim_targets` only reads
  Risk Advisor beta/Sharpe recs. So after this ships, a brand-new 0.66 pair pauses
  adds, while a long-standing 0.85 pair with an active trim call doesn't. Tracked
  as a separate future gate (tentatively **G-26**, same shape as G-01), not
  bundled into this change.
- **D7 — Phase 2's trigger is an observation checklist, not a Ledger threshold.**
  G-25 sits last in an already-narrow bull-day lane (Strong Buy + score ≥65 +
  gap ≥`ADD_WINNER_MIN_GAP_PCT`) and its own lifetime is cut short every time the
  owner scans — it will likely bind far less often than the Gate Suppression
  Ledger's `GATE_LEDGER_MIN_CALLS`(8)/`GATE_LEDGER_MIN_TICKERS`(5) floors need
  (compare G-20: 5 rows in ~5 weeks). Phase 2 (a real trim suggestion for the
  cluster's weakest-conviction member) is revisited once G-25 has: fired for real,
  shown no flicker (see Risks below), read correctly on a live screenshot, and the
  owner's own judgment that it's worth building — not a data-floor number that may
  never arrive.

## Build spec

Full implementation detail (exact functions, call sites, data flow, gate ID,
Ledger wiring, test list) lives in the planner's design transcript, condensed
here for the `implementer` handoff:

- **New `stock_analyzer/cluster_add_gate.py`** (added to `_GATE_FILES`): pure
  functions `baseline_signature()`, `resolve_new_clusters()`,
  `add_block_map()` — three-state contracts throughout (`None`=couldn't check,
  `[]`/`{}`=checked clean, populated=firing), never collapsing "couldn't check"
  into "clean."
- **`structural_scanner.py`**: extract a `detect_new_clusters_strict()` core (no
  outer try/except) that the new module calls directly for its own three-state
  handling; the existing public `detect_new_clusters()` wraps it in the identical
  try/except it has today — unchanged contract, unchanged tests.
- **`db.py`**: new `load_structural_scan_baseline_state()` returning the
  three-state shape (`None` offline / `{"status": "none"}` / `{"status": "ok",
  scan_date, cluster_snapshot}`) — the existing loader is untouched.
- **`daily_briefing.py`**: `build_daily_briefing()` gains an additive
  `cluster_add_blocks: dict | None = None` parameter, threaded into `_grow_today`
  (checked immediately after G-09, before every other field is computed) and
  `_buy_candidates` (its own independent add-lane needs the same skip, confirmed
  by the planner as a real second leak path). New `gate_registry.py` entry `"G-25"`,
  new `gate_ledger.py` bucket-lane mapping (`"cluster_blocked_adds" ->
  "add_winner"`) following the G-20/G-24 precedent (a labelled bucket item, not a
  new standalone builder — this gate DOES flow through `grow_today`, unlike
  G-02/05/06/13/18 which needed separate builders because they don't).
- **`app.py`**: hoist the baseline read above `_synth_sig` and fold it into the
  signature (bump `_SYNTH_SCHEMA_VER`) so an acknowledged scan can't leave a stale
  cached Brief still suppressing; publish one shared object to
  `_structural_alert_cache` that both the banner and the gate read (never two
  independent computations of the same fact); reword the banner (the current
  "Awareness only — gates are unaffected" claim becomes false the moment this
  ships); add a Grow Today "🧬 Add Paused — New Correlation Cluster" disclosure
  block matching the sibling G-04/G-09 style, every interpolation through
  `_safe_html`.
- **CLAUDE.md**: sync the `_GATE_FILES` enumeration (new module) in the same
  commit as the hook edit, per house rule.

Ship as one atomic `feat(` commit — a half-wired gate (e.g. the suppression
without the matching banner reword, or without the `_buy_candidates` leak-path
fix) is worse than not shipping at all.

## Known limitations, disclosed not fixed

- A locked Brief keeps showing G-25 suppression after a scan until it's unlocked
  — consistent with how Brief-locking already works elsewhere, noted in the User
  Guide rather than special-cased.
- **Flicker risk at the 0.65 boundary**, same class this project already parked
  once for the deterioration ladder (`project_exit_discipline`'s hysteresis
  question): a pair oscillating 0.66 → 0.64 → 0.66 could make an add appear/
  disappear on alternate days. Not fixed now — watch for it during the
  observation window, same discipline as the deterioration-ladder precedent
  (don't build hysteresis off a single eyeballed instance).
- A brand-new, not-yet-held pick that would itself join a cluster isn't caught —
  `corr_df` only covers currently-held names. Out of Phase 1 scope.
- **Locked-Brief + D4 interaction (reviewer finding, 2026-10-06):** if a Brief is
  locked while the baseline read was offline, and the baseline later recovers
  and finds a real cluster, the Home banner (always freshly computed) can say
  "paused on X" while the still-locked Brief continues showing an ADD for X
  under a "couldn't check" caption — the two surfaces briefly disagree until
  the lock clears. Rare, and the same class of limitation as the pre-existing
  locked-Brief-shows-stale-G-25-after-a-scan item above; not fixed, disclosed.

## Mandatory review

`daily_briefing.py` is already a `_GATE_FILES` member and this is cross-feature
coordination (a Home/Intelligence-produced signal suppressing a Grow Today
recommendation) — a mandatory Opus `reviewer` pass is required before this ships,
per Hard Rule #4.

Memory: `project_structural_cluster_add_gate` (to be created at ship time).

---

## F-290 (2026-10-09) — G-25 shares a partial-coverage blind spot, now disclosed

**G-25's own behaviour is UNCHANGED by this** — no block map, return contract,
or suppression decision was touched, and no Gate Suppression Ledger rows are
added. Recorded here because the blind spot is G-25's as much as G-26's.

`portfolio._close_series_map` silently skips any held ticker whose price history
is `None`, empty, or missing a `Close` column, so it never enters `corr_df` and
can never appear in a `risk_pair` or a detected cluster. G-25 therefore reads
such a ticker as **"checked, and clean"**, and its existing "couldn't check"
caption does not fire, because from G-25's point of view the check succeeded.

Partner-side only: an unpriceable ticker is never an add candidate itself (it is
dropped from `port_df` as `no_price_data` before either add lane sees it). The
real case is a priced, add-eligible A clustering with an unpriceable B.

Fixed as **disclosure only** (F-290): a new `portfolio.correlation_unchecked()`
feeds `pair_add_gate.unchecked_disclosure()`, which names both gates in its copy
when both ran. G-25 still fails open here — decided, not overlooked, since
fail-closed would let one provider hiccup pause every add in the book. Full
reasoning and the review round: `docs/plans/pair-add-gate.md` §6.
