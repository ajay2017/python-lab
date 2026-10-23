# Grow Today R:R gate (G-32): entry reward:risk ≥ 2:1 on new picks AND adds

**Status:** C1 BUILT 2026-10-23 (`stock_analyzer/entry_rr.py`, `scripts/grow_rr_dryrun.py`; Opus FIX-FIRST/2 → SHIP/0).
Nothing wired. **Dry run DONE 2026-10-23, readout below. Next: the owner rules on U1–U13.** (Was: the owner runs the dry run (`python scripts/grow_rr_dryrun.py --days 30 --csv grow_rr.csv`, then
`python scripts/grow_rr_dryrun.py --today`, from a shell with `SUPABASE_URL` / `SUPABASE_KEY`); the lead records the readout
here; the owner rules on U1–U13.) Enforcement (C3–C5) waits for that; C4 ships before or with C3.
C1 review fixes worth knowing when reading the readout: the retro target uses the same 6-month window as the live bundle
(52-week high/low from the longer frame), and `--today` measures picks, adds and overflow (near-exact: bundles re-fetched).

**Owner decision (2026-10-23):** Grow Today must require entry R:R ≥ `RR_ENTRY_MIN` (2.0) for new picks AND adds to
held winners, only after a read-only dry run shows how many it would have removed. Principle: one entry bar
everywhere new money goes in at today's price (memory `project_rr_entry_policy_2026_10_23`). Today Grow Today
(`daily_briefing.py`) has no R:R check; Watchlist ENTER_NOW does (G-13); the Diversification / Rebalancer headline does
since `fc9e6af` (`rr_readout.redeploy_headline`).

## What the planner found that shapes the numbers

- `targets.compute_price_targets` (~106-119): when price ≥ the chosen analyst target, base = min(resistance,
  momentum, price × `TARGETS_MODEST_UPSIDE_MULT`), so a technical base is at most +10%. The bundle stop is
  close − `ATR_STOP_MULT` (2.0) × ATR (`risk.py` ~25-30). So for a name at/above consensus, R:R ≤ 5 / ATR%: the gate
  passes only if ATR ≤ 2.5% of price. With an analyst base it needs upside ≥ 4 × ATR%.
- So G-32 will mostly act as a **volatility filter on momentum winners above consensus** (the add lane). The common
  failure is 0 < R:R < 2 on a technical, often "flat placeholder", base, not a non-positive R:R. The readout must
  split by `base_source`.
- `targets.risk_reward` (~175-180) rounds to 1 decimal before downstream comparisons, so a true 1.95 passes as 2.0
  on the redeploy headline and the Analysis gate tile; the Watchlist rounds to 2 decimals, so [1.995, 2.0) passes
  there. A naive "compare unrounded" also fails a true 2:1 on float noise ((100.1, 95.1, 110.1) → 1.9999999999999973).

## Design (summary; full spec in the 2026-10-23 planner hand-back, reproduced in the chunk briefs)

- **New pure module `stock_analyzer/entry_rr.py`** (a `_GATE_FILES` member from C1): `measure(entry, stop, target)` →
  `{"rr", "state": ok | no_upside | unknown, "why"}`, rr = round((target − entry)/(entry − stop), 6) (float-noise
  cleanup only); `from_bundle(bundle)` reads `current_price`, `stop`, `targets.base`; `clears(m, rr_min=RR_ENTRY_MIN)`.
  `targets.risk_reward` stays as is, documented as display only.
- **Predicate:** hold iff `not entry_rr.clears(entry_rr.from_bundle(bundle))`. Same bundle triple the Watchlist and
  the Diversification headline use, so a ticker's R:R is identical on every surface. Zero new fetches.
- **Placement:** new picks after the earnings-wait check, before sizing; adds last, after sizing and G-31 (G-31's
  pre-registered population must not shift); ETF lane after eligibility/macro (unledgered, `etf_screened_out`
  kind `rr`). Bear day: the G-23 early return still wins.
- **Suppression is visible:** G-32 in `gate_registry.py` + requirements §2A.3; ledger buckets `rr_blocked_picks`
  (`new_pick`) and `rr_blocked_adds` (`add_winner`), `counterfactual=True`, `gate_value` = rr (NULL if unknown),
  `gate_threshold` = `RR_ENTRY_MIN`; Home banners "📐 Not an Entry at Today's Price — Reward:Risk Below 2:1 (N)" and
  "📐 Add Held — Reward:Risk Below 2:1"; the email and Home agree because both call `build_daily_briefing`.
  `sizing_path._PAUSE_BUCKETS` gains `rr_blocked_adds`, so the Rebalancer "⏸ Not adding" and Analysis "No add call
  today" show the reason. No new session cache.
- **Unknown R:R → hold** (G-13 parity), ledgered with NULL; non-positive → hold with its own wording.

## Chunks

1. **C1** — `entry_rr.py` + tests + `scripts/grow_rr_dryrun.py` (retro over the last N trading days of
   `recommendations` new_pick / add_winner / overflow_pick rows, plus an exact `--today` mode on `scanner_cache`) +
   `summarize` tests; `_GATE_FILES` + review-policy line. Not wired. Reviewer. Precedent:
   `scripts/valuation_source_dryrun.py` (read-only, `db.set_readonly(True)`, Anthropic key dropped; the owner runs it
   from a normal shell with `SUPABASE_URL` / `SUPABASE_KEY`).
   **OWNER CHECKPOINT:** run the script; the lead records the readout here; the owner rules on U1–U13.
2. **C2 (optional, U9)** — measure-only shadow field + a "G-32 shadow:" cron diag line, no routing change.
3. **C4 (before or with C3)** — move Watchlist G-13, both headline callers and the Analysis gate tile / F-43b caveat
   onto `entry_rr` (unrounded). Also a standalone fix for today's 1.95–1.99 cross-surface inconsistency.
4. **C3** — enforcement in both lanes + ETF, ledger, registry, sizing_path, grow_dropoff, evening_debrief, cron diag.
5. **C5 (same push as C3)** — Home banners, empty state, Analysis / Rebalancer / Signals text, User Guide.
6. **C6** — docs (new F-312, F-43b / F-305 notes, architecture "used by" row, coordination-caches "no new key",
   shipped-log, queue, this Status). Push C3–C5 after 16:00 ET or at a weekend so no day's rows straddle regimes.

## Dry-run readout (owner ran C1, 2026-10-23)

`python scripts/grow_rr_dryrun.py --days 30` (rec_date 2026-09-11 … 2026-10-22; 103 rows, 0 skipped) and `--today`.

- **New picks: 96 rows over 22 days, 44 would be held (46%).** Pass 52, below 44, no_upside 0, unknown 0.
  - **By name:** 40 distinct names. 13 always held, 22 always pass, 5 mixed (CVX, TER, ESTC, MRVL, ABBV). The held rows are concentrated: HPE 11 (R:R 1.0–1.6, ATR 5–7%), TWLO 7, AMD 4, ABNB / FTNT / AAPL 2 each, then SMCI, PLTR, OKTA, FIVN, SPOT, DELL and WFC once each.
  - **Median ATR%** held 4.6 vs pass 3.2. So the gate acts as a volatility filter, as the planner expected, but on the analyst base, not mainly the technical one.
  - **`base_source`:** analyst 91 (39 held), resistance 4 (all held), flat 1 (held). The U3 technical-fallback question touches 5 of 96 rows, all held under either answer.
  - **Near misses in [1.5, 2.0): 10.** One rounding disagreement: CVX 2026-09-17, 1.98, shown as "2.0" by the 1-dp `risk_reward`. Stop-basis flips between the bundle ATR stop and `rec_stop`: 4 of 96 (ESTC 10-15 pass → below; ABBV, CVX, SPOT 09-17 below → pass).
  - **Days left with no passing pick: 2 of 22** (10-14, 10-21). Counting passing overflow names too, still 2 of 22.
- **Adds: 3 rows (ON 09-21, ON 09-22, MU 09-14), all pass.** Too few to judge the lane the planner expected G-32 to bind on.
- **Overflow: 4 rows, 2 pass** (OXY 10-20, CRM 10-19), so a freed slot could have been refilled (U5).
- **Asset type:** no ETF rows.
- **`--today`: no rows.** The 10-23 cron scan was present (scan_date 2026-10-23, source cron), but Grow Today produced no picks, adds or overflow, and the retro has no 10-23 rows either. The script doesn't print `compute_morning_picks` errors, so a quiet day can't be told apart from a failed build.
- **Approximation that matters most:** 95 of 96 pick rows use today's provider target (`approx_today`); only held names have a dated snapshot. Targets that moved since 09-11 shift those R:Rs in an unknown direction.

## Outcome analysis (lead, 2026-10-23, owner-requested before ruling on U1–U13)

Scratch analysis, not repo code: forward prices from yfinance over the dry-run pick rows, plus the owner's `trades` rows
since 2026-09-11 (owner SQL).

- **All 96 picks (price path), alpha vs SPY, close to close from rec_date.** At the 2.0 bar the gate doesn't separate:
  - kept: −0.7pp at 5 days (46 matured) and −0.3pp at 10 days (40);
  - held: −0.4pp at 5 days (37) and −1.0pp at 10 days (34);
  - per name: kept 0.0pp over 25 names, held −1.2pp over 16.
  - Alpha does not rise with R:R. The 1–1.5 band was +2.5pp at 5 days (7 of 7 beat SPY); the 2–2.5 band +2.2pp (6 of 8);
    3–5 was the worst at −1.8pp (8 of 24).
  - A 2.5 bar would also remove the 2–2.5 band: TER, PM, CRM, ABBV and ESTC won; COP and one CVX lost. Its kept set
    averaged −1.3pp at 5 days.
  - "First touch" (stop vs target) is geometry, not skill: low-R:R targets sit a few % above entry.
- **The owner's actual Grow-Today-triggered buys (13 lots).** Sold lots are valued at their sale price, open lots at the
  2026-10-23 close.
  - The 5 lots the gate would have held made +$63 on $5,037 (+1.3%): AAPL +4.7%, ABBV +9.0%, PLTR +0.9% (all open);
    DELL −5.9% and HPE −3.0% (both sold).
  - The 8 passing lots made +$581 on $7,160 (+8.1%): ON +14.0% / +15.6% (add), SAP +11.8%, ISRG +10.9%, MU +6.7%
    (add) and CRM +3.6% (overflow) gained; NVDA −6.5% / −4.8% lost.
  - Excluded: manual buys, the MRVL Diversify-add (R:R 1.8 on the 10-06/07 rows, −$236), and the MU 09-30 and
    CRM 09-15 buys (no matching dry-run row).
- **Read:** the two lenses disagree. Both are small, sit in one choppy regime and use approximate targets, and the
  trade lens is dominated by exit timing. Neither measures an edge for R:R ≥ 2 as an entry filter. The lead's
  recommendation is the C2 shadow (exact R:R recorded per pick, no routing change) before enforcement.

## Owner decisions needed (after the dry run)

- **U1** price basis: bundle triple (recommended) vs the lane's sizing price.
- **U2** add stop: bundle ATR stop (recommended) vs trailing / ratchet / manual (looser).
- **U3** technical-fallback base: counts as measured (recommended, G-13 parity) vs "no validated target" → hold.
  Decide after seeing the `base_source` split.
- **U4** unknown R:R: hold + ledger with NULL (recommended) vs unledgered abstention.
- **U5** a slot freed by G-32 goes to the next fully qualifying candidate (recommended) vs left empty.
- **U6** apply to the ETF lane, unledgered (recommended).
- **U7** ratify moving G-13 / the headline / the Analysis tile to unrounded R:R ([1.995, 2.0) and [1.95, 2.0) then fail).
- **U8** Analysis not-held Buy: keep the F-43b caveat (recommended) vs withhold the size below 2:1.
- **U9** dry-run window (recommend 30 trading days); whether to wait for ≥ 5 bull days of shadow (C2) first.
- **U10** G-32 pre-registered review rule (recommend G-31's wording).
- **U11** optional `rec_rr` / `rec_target` columns (recommend defer).
- **U12** optional "would clear at or below $X" line (X = base − 4·ATR on the bundle basis).
- **U13** a G-32-held morning pick does not re-fire in the intraday pullback lane (recommended for now).

## Risks

- Silent filtering if C3 ships without C5; a 1.95–1.99 contradiction across surfaces if C3 ships without C4.
- After go-live, new_pick / add_winner rows are all ≥ 2:1: a population shift for Recommendation Outcomes (~12-16)
  and E2. Record the go-live date; the G-32 ledger rows keep the removed names.
- More no-email mornings: expected.
- app.py has no tests: banners and empty states are screenshot-only (check literal `**` and `$`-LaTeX).
