# S-D — One sizing path for every share count

**Status:** C9 DONE 2026-10-23 (docs): User Guide + user-manual "adds count what you already hold" replaces the stale "cap bounds only the added shares" caveat; architecture known-behaviour row marks that gap closed; new architecture section for `sizing_path.py` / `trade_caption.py`; requirements F-310 lists C5/C6/C8/C9. **S-D is complete except optional C7** (D5 dry run on post-trade `SECTOR_CEILING`). C8 BUILT 2026-10-23 (tests-only, no review needed): `tests/test_sizing_ratchet.py` pins direct `position_sizing(` calls per file (sizing_path 1, daily_briefing adapter 1, app.py 3: Analysis not-held Buy, Watchlist budget lambda, Watchlist ENTER_NOW panel), bans aliases / passing it around / imports outside app.py and daily_briefing, pins the single definition in risk.py, and pins the Watchlist panel call behind `_wl_may_size` (the C4 review note) and the budget lambda into `watchlist_asks`; self-tests on synthetic source, mutation-checked. NEXT: C9 docs (C7 dry run optional). C6 BUILT 2026-10-23 (Opus SHIP/0): Diversification "Gap to 10% target ≈$X" (a gap, not a buy size; help text points to 📈 Analysis); Signals "High-Conviction Position Undersized" gives no size or tranche advice, only a pointer to Analysis ("▶ Analyze X: today's add call"); the trim card drops "reinvest the proceeds"; dead `add_val` + orphaned `REBALANCE_ADD_TARGET_WEIGHT_PCT` (8.0) removed. Screenshot pending. NEXT: C7 (optional D5 dry run), C8, C9. Found in review, separate: the Signals oversized-trim size was understated by w/100; fixed 2026-10-23 (`fix(portfolio)`). C5 BUILT 2026-10-22 (Opus FIX-FIRST/1 → SHIP/0): `build_rebalance_plan(add_verdicts=)` reads `sizing_path.held_add_verdict` (same inputs as 📈 Analysis); a paused verdict moves the row to `grow_paused_adds` (after reduce-call and Risk Advisor; no new ledger rows); surviving underweight rows carry no share count and show drift + Grow Today's verdict; news flags describe only; KPIs "Underweight" (incl. suppressed) / "Trims would move" (`trim_pct`). Screenshot pending. NEXT: C6. Not taken: the shared "its size is on 🏠 Home" wording (also in Analysis) can overclaim when F-306's budget leaves an add unsized. C4 BUILT 2026-10-22 (Opus FIX-FIRST/2 → SHIP/0): (0b), (a) Watchlist `may_show_size` + `watchlist_no_size_caption`, (b) Econ/Catalyst no counts + in-window HOLD (Econ falls through to WATCH; defers to Grow Today, N-C still deferred), three literals named (values unchanged), Analysis mismatch caption. NEXT: C5. C3 BUILT 2026-10-22 — the Grow Today risk footer is `budget_view.risk_footer` over the post-budget sizes (Opus FIX-FIRST/2 → SHIP/0); `deploy_note` removed from the brief. Screenshot pending (Railway paused): the footer in its sized / partial / none-sized states, two `$` and no LaTeX. C1 + C2 BUILT 2026-10-20 (F-310; `7d59019` + the C2 commit, each Opus-reviewed to SHIP/0) — add sizes count the holding, G-31, Analysis shows Grow Today's add call, D4. **Live-verified 2026-10-20:** Analysis held + flat day (FSLR: "No add call today", no sizing, Concentration ✗ 27.6% of net capital) and not-held buy (MRVL after the owner's 10-20 sale: "2.4% of book · 7.5% of net capital after the buy", budget-sized). Still to see: the paused / add / unavailable boxes, Grow Today's room note + block. Verify on the first live renders: the Grow Today "Position Already at the Cap" block and "Capped by what you already hold" note; the Analysis add box in each state (MRVL → paused, no size, no 📒 Add); no literal `**` / `$`-LaTeX. Earlier: DESIGNED 2026-10-19 (planner, Opus 5.5) — verdict **PROCEED WITH CHANGES**. **Owner decided 2026-10-19:
all recommendations** — D1 yes (caps bind on the whole post-trade position), D2-A (Analysis shows Grow Today's add
verdict), D3a (`room_blocked_adds` + new gate G-31), D4 yes (holdings unknown → no size), D5 deferred to the C7 dry
run, D6-X (no buy-sized numbers on secondary surfaces; bare literals named as constants, values unchanged, each
signed off at its chunk), D7 left as is; DoD #8 version bump 4 → 5 accepted. Nothing built yet.
Origin: queued as S-D in `docs/plans/shared-entry-check.md` (~line 280).

## Why (seen live 2026-10-19)

- **📈 Analysis on MRVL, a held name** (7 sh, ~23% of net capital): offered a **7-share add**, "sized to 7 shares
  (~23% of your net capital)", with "Concentration ✓ 8.8% of portfolio". `position_sizing` (`risk.py`, called at
  app.py ~24794) never sees the existing holding, so `SINGLE_NAME_CEILING` (15.0, gross) and
  `NET_CAPITAL_POSITION_CAP_PCT` (25.0, net capital) bind on the NEW shares only. Following it → ~17.6% gross,
  ~46% of net capital.
- **The same morning, Grow Today paused MRVL adds under G-24** ("Added within the last 10 days (5d ago)"). Analysis
  ignores every add-lane suppression.
- **Grow Today's risk footer** said "~$1,144 if every stop hits" across 4 setups; only 2 were sized, and capped —
  ~$240 actually at risk (`daily_briefing.py` 2488-2502 builds it before caps/budget).

**Design change from the queued framing:** don't wire five more surfaces into sizing — remove share counts / $ amounts
from the secondary surfaces so only one sizing path remains (D6). Wiring each would recreate the drift S-D removes.

## 1. Sweep at HEAD `0305833` (10-16 rows re-verified)

Ceiling = `SINGLE_NAME_CEILING` 15.0 (gross); NetCap = `NET_CAPITAL_POSITION_CAP_PCT` 25.0 (net capital; inert when
basis is unlevered/stale, margin.py 110-117); Budget = `DAILY_NEW_POSITION_BUDGET_PCT` 40.0; Risk =
`RISK_PCT_PER_TRADE` 0.015. app.py lines from `grep -n`.

| # | Surface (file:line) | vs 10-16 | Hands out | Caps | Counts existing? | Checks honoured |
|---|---|---|---|---|---|---|
| 1 | Grow Today new picks (daily_briefing 1646-1652 → `_position_size_for_render` 143-270) | — | shares | Risk, Ceiling, NetCap, Budget | n/a (held excluded 1078, 1234) | bear return, flat bar 78, G-07, stale/fund age, `verdict_basis`, news Skip, earnings wait, sector gate (current weight) |
| 2 | Grow Today ETF pick (1992-1998) | — | shares | as #1 | held group screened out | etf_candidates gates |
| 3 | **Grow Today adds** (2436-2443) | still true | shares | all on NEW shares only | **No** (G-04 2281 checks current gross ≥ 15 only; no net-capital check) | bull only (2134), `_add_entry_ok` 676-687, CARD, G-20, G-24, G-16, G-01, G-04, G-09, G-26, G-25, ENTRY. No macro (N-C deferred) |
| 4 | Cron morning picks + 11:30 pullback email (headless 666; cron 2154) | separate pullback-add flow **gone** | shares, new only | as #1 + locked allocations | n/a | as #1 |
| 5 | Watchlist ENTER_NOW (app 28426, 28565) | changed (F-307) | shares | Risk, Ceiling, NetCap, Budget | held → "Already in Portfolio", no size (28479-28492) | stale, bear, G-28, news, earnings |
| 6 | Watchlist email (headless 1284) | — | shares | as #5 | held captured, not emailed | as #5 |
| 7 | **Watchlist non-ENTER_NOW rows** (28560-28570; panel 28786-28800) | ENTRY_HELD now excluded | shares + $ | Ceiling, NetCap; no budget | **No** — held NEAR_ENTRY/WAIT/… rows get a fresh-entry size | none |
| 8 | Analysis, not-held Buy (24776-24788) | changed (S-A) | shares, 📒 prefill (26553) | Ceiling, NetCap, Budget | n/a | F-307 bear/macro/news/stale/earnings; flat bar not applied (owner 10-15) |
| 9 | **Analysis, held-name add** (entry_eligibility 656-658; app 24776; prefill 26553-26560) | still true — **MRVL** | shares + prefill | new shares only | **No** | only stop breach (25545) + `_reduce_calls` (25578/26255). None of CARD, G-20, **G-24**, G-16, G-01, G-04, G-09, G-25, G-26, ENTRY, tone, Strong-Buy requirement. "Concentration ✓" tile (26201/26210) judges current weight |
| 10 | Diversification "Suggested add $X" (portfolio.py 2269-2299; app 16084) | still true | $ = gross × (10 − current)% | none | candidates not held | none on the $ |
| 11 | Rebalancer ADD (rebalancer.py 266-420; app 20844/20921; custom target `max_value=100.0` 20833) | still true | shares + $ | **none** | yes (total target) | reduce set, G-02, news (non-blocking). No G-04/G-16/G-20/G-24/G-25/G-26/ENTRY/tone/budget (CLAUDE.md D5) |
| 12 | Signals "High-Conviction Undersized" (portfolio.py 882-899; app 15883-15912) | changed (S-C) | text "8–10% weight… first tranche 4–5%" | none; **bare literals** | — | inherits #9 |
| 13 | Econ Calendar OPPORTUNITY (macro_playbook 337-340, 440-446; app 41186) | still true | "N shares" = max(1, int(shares × 0.10)) | none | yes | fires within 14 days of a HIGH event — covers the G-07 window, the reverse of G-07. Bare 0.10/14/7 |
| 14 | Catalyst HOLD_OR_ADD (earnings_advisor 349-369) | still true | "small add (5–10%…)" text | none (REDUCE only > `SINGLE_NAME_TRIM_TRIGGER` 18) | yes | 30-day lookahead fires inside `EARNINGS_IMMINENT_DAYS` 7. Bare `net_rev >= 2` |
| 15 | Trade Journal BUY confirm (app 29893; 30019 `concentration.assess_add_concentration`) | new since 10-16 | none (awareness) | Budget line + post-trade Ceiling/NetCap/sector | **yes** | never blocks — after an MRVL add it would say "~46% of net capital", contradicting Analysis |
| 16 | Grow Today risk footer `deploy_note` (daily_briefing 2488-2502; app 9049, 10434) | new | "$ at risk" | none (pv × 1.5% × n) | n/a | counts unsized picks, ignores caps/budget |
| 17 | Beta-repair panel | still true | never sizes | — | — | — |

## 2. Design

**2a. `risk.py`:** add `existing_value: float = 0.0` to `position_sizing` and `sizing_unavailable_reason`.
`ceiling_shares = int((pv·ceiling/100 − existing_value)/entry)`, `capital_cap_shares = int((net·cap/100 −
existing_value)/entry)`; denominators stay pre-trade. New reasons `room_ceiling` / `room_capital` (existing > 0, room
< 1 share); existing above a cap → no size, never negative, never a trim. New keys when existing > 0:
`existing_value`, `post_portfolio_pct`, `post_capital_pct`, `room_capped`, `room_binding`. `existing_value == 0` ⇒
byte-identical to HEAD (golden test). risk.py still never imports margin.

**2b. New `stock_analyzer/sizing_path.py`** (pure; joins `_GATE_FILES` + CLAUDE.md list in the same commit):
- `held_value(ticker, port_df, holdings_state)` → float | None (`0.0` = not held; `None` = couldn't check).
- `size_position(*, entry, stop, gross_book, net_capital, existing_value, risk_pct=RISK_PCT_PER_TRADE)` → always a
  dict: `{"state": "sized", "ps", "binding", "text"}` or `{"state": "no_size", "code", "text", "existing_gross_pct",
  "existing_capital_pct"}`; `existing_value is None` → `holding_unknown` (fail closed). Every no-size carries a
  sentence to render.
- `held_add_verdict(ticker, grow_raw, budget_cache, today, epoch)` → `add | paused | not_called | unknown` with reason,
  gate_id and source line, READ from today's raw brief buckets (`add_positions`, `*_blocked_adds`, `cooldown_adds`,
  `card_blocked_adds`, `entry_held_adds`, `adds_paused`); stale/offline/other epoch → `unknown` ("open 🏠 Home").
  No new cache key.
- `analysis_concentration_tile(...)` ("now X% → after Y%"), `may_show_size(surface, action)`.
- `_position_size_for_render(..., existing_value=0.0)` passes through; a `room_infeasible` marker has no `shares`,
  so `daily_budget` treats it as passthrough; `notify` needs a default case.

**2c. Order:** risk shares → Ceiling (total) → NetCap (total) → F-306 budget (`allocate`, unchanged).

**2d. Grow Today adds:** add loop passes `existing_value = row["Market Value"]`; the room check runs LAST (after
ENTRY) so every gate keeps its bucket and ledger rows. Partial room → note "capped by what you already hold (X% → Y%
of net capital)"; zero room → new `room_blocked_adds` bucket inside the concentration-paused block.

**2e. Risk footer:** drop `deploy_note` from the brief; new pure `budget_view.risk_footer(grow_after_postpass,
act_risk_flag)` on both memo paths: Σ (total_cost − shares × stop) over sized items, names unsized items and why;
can't compute → say so. Escape `$` at app 10435 (two amounts → LaTeX risk).

**Sentinels:** holdings unknown → no size (`holding_unknown`); NetCap inert on stale/unlevered as F-255 today
(budget surfaces already withhold on stale); brief not today's/offline/other epoch → Analysis held add `unknown`;
sizing_path crash → callers render no size + "sizing couldn't run".

## 3. Owner decisions (OPEN)

- **D1 (policy reinterpretation, no number change):** apply `SINGLE_NAME_CEILING` and `NET_CAPITAL_POSITION_CAP_PCT`
  to the **whole position after the add**. Planner recommends yes (matches G-04 "no add-to-winner above this" and
  the Trade Journal check). Rewording the constants.py 134-139 comment ⇒ reviewed constants commit. Alternative: a
  separate add-only cap (new constant).
- **D2 (coordination):** (A, recommended) Analysis shows Grow Today's add verdict + size and never decides an add
  itself — covers G-24/G-20/G-25/G-26/G-01/G-04/G-09/CARD/ENTRY/bull-only/Strong-Buy at once; MRVL → no size + reason.
  (B) Analysis re-runs the add lane (would drift). (C) caps + entry check only (G-24 stays broken).
- **D3 (zero room in Grow Today):** (a, recommended) `room_blocked_adds` with new gate **G-31** so it is measured,
  G-04 unchanged (DoD #8); (b) same bucket, no ledger row; (c) add card with no size.
- **D4:** holdings context unknown → withhold every size (today Analysis sizes as fresh with a warning, app
  24861-24867). Recommended yes.
- **D5:** should `SECTOR_CEILING` (35.0) also bind post-trade? Changes new-pick sizes; decide after a read-only dry
  run (C7).
- **D6 (secondary surfaces):** (X, recommended) no share counts/$ as buy instructions on Diversification
  ("gap to 10% target"), Rebalancer (drift in pp/$ labelled as drift), Econ OPPORTUNITY / Catalyst HOLD_OR_ADD /
  Undersized ("size on 📈 Analysis"); no add advice inside the G-07 (≤ `MACRO_IMMINENT_DAYS` 3) or earnings
  (≤ `EARNINGS_IMMINENT_DAYS` 7) windows. (Y) wire each through sizing_path + budget. Either way, name the bare
  literals as constants (macro_playbook 0.10/14/7, earnings_advisor `net_rev >= 2`, Signals "8–10%/4–5%"), values
  unchanged, each an owner sign-off.
- **D7 (out of S-D):** not-held flat-day divergence (Analysis Buy at 65 vs Grow Today flat bar 78) — owner decided
  10-15/16 the flat bar stays Grow-Today-only; reopen only if wanted. The MRVL case was held, so D2-A covers it.
- **DoD #8:** `add_winner` rows persist `rec_shares` (app 6874-6891) whose meaning changes → bump
  `SIZING_FORMULA_VERSION` (daily_briefing 140) and `daily_budget.BUDGET_SIZING_VERSION` (43) 4 → 5 together, with a
  test they stay equal; dated note on any sizing-calibration readout.

## 4. Chunks

1. **C1a** total-exposure arithmetic + sizing_path core — risk.py, sizing_path.py, daily_briefing (adapter, add loop,
   version), daily_budget (version), notify (marker default), `_GATE_FILES`, CLAUDE.md; G-31 files if D3a. Implementer
   → Opus reviewer.
2. **C1b** render for C1a, pushed with it — Analysis (held add via `held_value` interim, no-size text, post-trade tile,
   📒 prefill only with a size), Grow Today room note + room-blocked block. Mockup first. Reviewer.
3. **C2** Analysis held add = Grow Today verdict (D2-A) — closes the G-24 finding. Push right after C1. Reviewer.
4. **C3** risk footer from final sizes — budget_view, daily_briefing, app 9049/10434. Reviewer.
5. **C4** quick wins — **(00) and (0) SHIPPED 2026-10-21 (`deb5c2e`); (0) reads "log the sale from 📒 Trade Journal", not "open 🏠 Home to sell" — Home drops an unpriced holding, so it has no Sell button for it either.** **(0b) QUEUED 2026-10-21 (owner-agreed), seen live on SPCX:** the not-held Sell caption "Not in your portfolio — nothing to sell. 📒 Trade buttons appear here for names you hold." offers no path for a buy already made at the broker (owner bought SPCX at Robinhood against the Sell 42.6) — add a pointer such as "Bought it at Robinhood? Log it from 💰 Account → 📥 Pending Imports (exact broker date/price) or 📒 Trade Journal."; still no trade button there (it would attribute the trade to a call never issued). (00) seen live 2026-10-20 on FSLR (held, Buy, flat day): the "Already held" info box still says "Earnings in 9 days — consider reduced add-on size or wait until after the report" right above "➕ No add call today"; show that add-on advice only when the held-add verdict is `add`, otherwise just "Earnings in N days"; (0) from the C2 review: a held name 🏠 Home couldn't price, on a Sell verdict, still says "Not in your portfolio — nothing to sell" beside the new "holding couldn't be priced" warning (app.py Analysis Sell branch; reword to "holding couldn't be priced — open 🏠 Home to sell" when the ticker is in `_an_unpriced`); (a) no Watchlist size panel on non-ENTER_NOW rows via `may_show_size`; (b) no Catalyst
   HOLD_OR_ADD / Econ OPPORTUNITY add advice inside earnings/G-07 windows, no count outside. Reviewer recommended.
6. **C5** Rebalancer ADD per D6 — drop/clamp the count; read `held_add_verdict` for G-24/G-25/G-26 (closes D5 of
   CLAUDE.md's G-25 entry). Reviewer.
7. **C6** Diversification $ relabel + Undersized text (portfolio.py is a gate file → reviewer).
8. **C7** (optional, D5) post-trade sector room: dry run first.
9. **C8** ratchet: AST test — only risk.py, sizing_path.py and the daily_briefing adapter call `position_sizing(`.
10. **C9** docs: requirements (new F-ID; F-255/F-306/F-307 lines; G-31), architecture constants + module section,
    user-manual, User Guide, shipped-log, this Status line, CLAUDE.md queue, memory.

## 5. Tests the build must include

- **MRVL regression (C1a):** held 7 sh, price $238.86, existing $1,672.02, gross $19,000, net capital $7,300
  (levered), stop $215.00 (fixture). HEAD → 7 sh. New: capital room int((1,825 − 1,672.02)/238.86) = **0** →
  `no_size room_capital`, text names 22.9% now / 26.2% with one more share. Gross room alone = 4 sh → assert NetCap
  binds. Non-vacuous pair: existing $1,400 → ≥ 1 share and `room_capped`.
- **Byte-identity:** `existing_value=0` equals frozen HEAD over a grid; Grow Today new-pick golden unchanged.
- **Room boundaries:** exactly 1 share of room; 0.999 → 0; existing at the cap; above the cap (ALB/OXY shape) → no
  size, never negative, never a trim; ceiling binds when unlevered.
- **Sentinels:** `existing_value=None` → `holding_unknown`; `net_capital ≤ 0` → "capital" unchanged; crash → no size.
- **Trade Journal coherence:** a sizing_path size never makes `assess_add_concentration` report `name_breach` /
  `capital_breach` (pin the `>=` / post-trade-denominator equality case).
- **Budget:** `room_infeasible` consumes 0; both version constants = 5 and equal.
- **C2:** cooldown → paused (G-24 reason, no size); flat day → not_called (up-day wording); Buy-not-Strong-Buy →
  not_called; stale/offline/epoch → unknown; `add_positions` → same shares as the Grow Today card; each bucket
  mutated away → test fails.
- **C3:** 4 setups / 2 sized → count + risk from final sizes; all unsized wording; act-risk variant; partial budget
  sizes use scaled shares.
- **C4:** non-ENTER_NOW row never sized (incl. held); HOLD_OR_ADD earnings day 7 vs 8; OPPORTUNITY macro day 3 vs 4.
- **C8:** AST ratchet fails on a new direct `position_sizing(` call.

## 6. Risks

- **Push C1 and C2 together** — C1 alone leaves the G-24 divergence; C2 alone mirrors an add size that still ignores
  existing exposure.
- **Composite mismatch:** Analysis's fresh composite can differ from the brief's — C2 shows the source ("Grow
  Today's call, built HH:MM, score X"). **Unverified:** whether `_refresh_portfolio_cache_after_trade` rebuilds the
  brief's add buckets after a logged buy; if not, C2 reads `unknown` until Home rebuilds.
- **G-31 will bind often:** at ~2.6× leverage any held name above ~9.6% gross is already ≥ 25% of net capital.
- **NetCap inert on stale cash** stays acceptable only because budget surfaces withhold on stale and C4/C6 remove the
  non-budget counts — say so in requirements.
- **Screenshot-only:** Analysis MRVL (no size, reason, tile, no prefill); Grow Today room note/block; footer with two
  `$` and no literal `**`; Watchlist non-ENTER_NOW rows; Rebalancer/Diversification relabel; Econ/Catalyst wording.
- **Pre-existing Hard-Rule-#1 violations** (bare decision literals listed in D6) — flagged, not fixed here.
- **Interim rule until shipped:** trust a size only from a Grow Today new pick or a Watchlist ENTER_NOW; for any add
  check total weight on 💰 Account against the 25%-of-net-capital cap.
