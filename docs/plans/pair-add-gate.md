# G-26 — Standing Danger-Tier Correlated-Pair Add Suppression

**Status: BUILT 2026-10-08, pending Opus review + commit.** Designed by the Opus
`planner` (verdict: PROCEED WITH CHANGES — it rejected two details of the
original §D6 framing, see §2), built by `implementer` (Sonnet 5), all three open
policy decisions ratified by the owner the same day. Requirements: `docs/requirements.md`
F-289 + the §2A.3 G-26 row. Memory: `project_pair_add_gate`.

---

## 1. The gap this closes

Recorded as **D6** in [cluster-add-gate.md](cluster-add-gate.md) during G-25's
design, deliberately not fixed there:

> a danger-tier `PAIR_RISK` trim call (0.80+ correlation) is today not read by
> Grow Today's add-to-winner logic at all — `_trim_targets` only reads Risk
> Advisor beta/Sharpe recs. So after this ships, a brand-new 0.66 pair pauses
> adds, while a long-standing 0.85 pair with an active trim call doesn't.

Verified independently before the design pass: `PAIR_RISK` appears only in
`portfolio.py` (the producer) and one comment in `rec_events_capture.py`.
Nothing in `daily_briefing.py` reads it.

So the app was **stricter about a weak new signal than a strong standing one** —
the inversion G-25 created by shipping first. D6's stated trigger was "an
explicit owner ask"; that was given 2026-10-08.

---

## 2. Two details of §D6's framing the design pass rejected

§D6 proposed "same shape as G-01", i.e. keyed on the emitted `PAIR_RISK` trim
call. Building it that way would have shipped two defects:

1. **Keying on the trim call lets a data outage switch the gate off.**
   `portfolio.py`'s `PAIR_RISK` producer `continue`s past any pair where either
   name lacks `Score Available` — an ETF, or a stock mid-fundamentals-outage.
   So a fundamentals outage on one leg would silently lift the pause on the
   other. **A gate that switches itself off during a data outage is exactly what
   this project's hard rules forbid.** Correlation is price-only and keeps
   working through that outage.

2. **`risk_pairs == []` is not a safe offline tell.** `home_risk_synthesis.py`'s
   `except` branch sets BOTH `corr_df = pd.DataFrame()` and `risk_pairs = []`,
   so an empty list is ambiguous between "checked, clean" and "correlation
   failed". `div_recs` doesn't disambiguate either — if correlation failed the
   rec builder still runs and returns REDUCE/ADD items, so its list is non-`None`
   and merely has no `PAIR_RISK` entries. **The one reliable signal is an empty
   `corr_df`.** This is the sentinel-collapse class this project has shipped and
   fixed repeatedly (`feedback_sentinel_is_present`).

A third correctness detail, found in the same pass: `diversification_score`
stores `"corr": round(c, 2)` but classifies `"level"` from the **unrounded**
value. A 0.799 pair is stored and displayed as `0.80` yet is warning-tier.
**The gate must branch on `level == "danger"`, never on `corr >= 0.80`** — a
naive numeric check fires on a pair the app itself classified as warning.

---

## 3. Owner decisions (ratified 2026-10-08)

- **D1 — Trigger: the pair itself, not the trim call.** Fire on any danger-tier
  pair where both names are held, regardless of whether a trim card exists.
  Covers everything the trim card covers, plus the outage case above.
  Consequence accepted: G-26 can pause an add on a pair with no visible trim
  card. The banner discloses which case applies, via a per-partner
  `named` / `not_named` / `unknown` tag.
- **D2 — No acknowledge/override, no expiry.** The pause lasts while the pair
  stays danger-tier and both names are held. **A partial trim of the weaker name
  does NOT lift it**, because trimming does not change correlation — the
  `PAIR_RISK` trim card behaves the same way. So a pair the owner deliberately
  keeps has adds paused on both names indefinitely. G-25 has a natural release
  (running a Structural Scan acknowledges it); G-26 has none, and building one
  would need new stored state. **Deferred, not rejected** — the trigger to
  revisit is a pair the owner deliberately holds actually binding in practice.
- **D3 — G-26 is checked before G-25.** A brand-new pair that is also ≥0.80
  records under G-26. Chosen to leave the older gates' Gate Suppression Ledger
  history untouched ahead of their first evaluable verdicts, and to stop a
  single pause being recorded as G-25 one day and G-26 the next purely because
  the owner ran a Structural Scan. G-25 had two days of history at ship time, so
  almost nothing is reattributed. Disclosed via `readout_footnotes`.
- **D4 — No new constant.** Reuses `CORR_DANGER_PAIRS_THRESHOLD` (0.80) as-is;
  it appears only as the ledger row's `gate_threshold`. No weight floor, same
  reasoning as G-25's D3.
- **D5 — Pure suppression.** No trim is ever recommended — the same boundary as
  G-25's deliberately-deferred Phase 2.

---

## 4. What shipped

New pure module `stock_analyzer/pair_add_gate.py` (joined `_GATE_FILES`):

| function | contract |
|---|---|
| `add_block_map(corr_df, risk_pairs, div_recs, held_tickers)` | `None` = couldn't check · `{}` = checked, clean · populated = firing |
| `describe_reason(entry)` | ≤300 chars, one clause per partner type, always ends "This pause adds no recommendation of its own." |
| `split_scan_liftable(cluster_blocks, pair_blocks)` | splits G-25's banner list into scan-liftable vs. also-pair-blocked |
| `buy_lane_only(pair_blocked_adds, buy_lane_skips)` | `None` passes through |

Wired into both Brief add-lanes in `daily_briefing.py`, registered in
`gate_registry.py`, captured to the ledger via `gate_ledger.py`'s
`"pair_blocked_adds": "add_winner"` lane. **`pair_buy_lane_skips` is
deliberately NOT a ledger lane** — it is disclosure plumbing only, and a stray
entry there would corrupt G-26's own forward-alpha measurement.

Render surfaces: a "🔗 Add Paused — Standing Correlated Pair (≥0.80)" block on
Grow Today, a caption under "More Buy Candidates", an also-pair clause on Home's
Structural alert banner (so G-25's "until you review this cluster" copy stops
being a false promise for a ticker whose pause will survive the scan), and a
caption on the `PAIR_RISK` trim card in 📡 Signals & Advice.

---

## 5. Known limitations, stated deliberately

- **Scope matches G-25's D5.** Rebalancer drift-ADD and Analysis's own
  add-sizing lane (the G-18 surface) are NOT covered — a ticker paused here can
  still be suggested as an ADD from either. Known and disclosed, not missed.
  Watchlist and the Diversification Advisor only suggest names not held, so they
  cannot trigger this gate.
- **Thin correlation sample under-fires this gate.** A thinned sample pulls
  correlations toward zero, so a pair can drop below danger tier for sampling
  reasons. That is the fail-open direction; F-246's coverage caption is the
  existing tripwire. No coverage threshold was added.
- **Flicker at the 0.80 boundary is not damped.** The gate flips on exactly the
  days the trim card does, so the two can never disagree; hysteresis would need
  a new constant and is out of scope.
- **A single holding produces an empty `corr_df`**, which reads as "couldn't
  check". Harmless — there is no pair to gate.
- **Locked Brief:** same disclosed limitation as G-25.
- **The cron path never produces G-26 rows** — it passes no map, and add
  suggestions are never emailed. Matches G-01 and G-25.
