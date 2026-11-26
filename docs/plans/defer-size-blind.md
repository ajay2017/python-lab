# DeferSizeBlind: a deferral to an Act Today sell must know that sell's size

**Status:** DESIGNED 2026-11-26 (Opus planner `claude-opus-5-5`, PROCEED WITH CHANGES). **Owner took every default 2026-11-26** (Q1 `sell_signal` covers only up to its `min_trim` shares, else unknown; Q2 `risk_off_derisk` never a full cover; Q3 the U6b-pattern wording; Q4 a partial Rebalancer row counted at its full size; Q5 the header tag; Q6 / Q7 the Brief-internal siblings as separate rows with their own design, queued right after; Q8 need 0 covered by any Act sell). **C1–C3 BUILT 2026-11-26** (spec `docs/plans/defer-size-blind-spec.md`; implementer `claude-sonnet-5-5`; Opus reviewer `claude-opus-5-5` SHIP, 0 blocking; one commit: the signature changes force the Home / Rebalancer caller edits into the core, and a kept card must never show without its line). New gate module `act_sell_cover.py`; `held_trim_owner` dispositions + `reconcile_tuneup` size-aware; `risk_advisor` card `trim_shares`; Act `deterioration_trim` `trim_shares`; `act_sell_mention.verified_sell_cover`; Rebalancer partial rows counted + `act_partial_count`; `_SYNTH_SCHEMA_VER` 30. Reviewer follow-ups (non-blocking): the Tune-up can show both the earnings note and the cover note while the Rebalancer shows only the cover note (match later); `rebalancer.py` ~226 computes `shares_delta` before the OK-row `continue` (theoretical non-finite `total_val`); the unknown line under `risk_off_derisk` says "also sells" though that call allows tightening instead (same as U6b's wording); a review item whose `ticker` differs from its `trim_ticker` would give that ticker an unknown-cover line (no producer emits it). Next: C4 docs; then the two Brief-internal siblings (Q6 / Q7) to the planner. Build starts on a clean tree after entry-fail-closed C2b lands (both touch app.py / `pre_tool_checks.py`). Bug-register row DeferSizeBlind.

## Defect

`held_trim_owner.tuneup_single_name_disposition` (~:102), `risk_analysis_single_name_line` (~:224) and `rebalancer_trim_disposition` (~:245, from `rebalancer.py` ~:224) defer a standing trim card whenever the ticker is in a SET of Act Today sell tickers. A 1-share macro trim on a ≈ 30% holding hides that name's ceiling trim to `SINGLE_NAME_CEILING` (≈ half the position) on 🔧 Tune-up, 🔗 Risk Analysis and the ⚖️ Rebalancer while the Act sell stands, breaking U6's "the merged card never sells less" across surfaces.

## Planner's required changes

(a) Read each Act sell as the FEWEST shares it commits to selling (lower bound), not its headline. (b) The cover data comes from the producers; three Act kinds carry no structured share count today. (c) Home's `reconcile_tuneup` re-decides every single-name card, kept or deferred. (d) No share count in the new lines (🙈 Hide values masks shares).

## Where each Act sell's size lives (verified at HEAD by the planner)

| Item | Size | Structured |
|---|---|---|
| Review trims (TRIM_TO_TARGET, TRIM_AND_TIGHTEN, PROTECTIVE_TRIM) | `action.trim_shares` (daily_briefing.py ~3551, ~3634, ~3815, ~4032) | yes, int ≥ 1 |
| `stop_breach` / `deterioration_exit` | `shares` = whole position (~2961 / ~3067) | yes (exit) |
| `deterioration_trim` | directive text only (`fraction_trim_shares` ~3006; `shares` None by design) | no → add `trim_shares` |
| `risk_off_derisk` | directive text only (exit_advisor.py ~691-708), offers "or tighten the stop" | no |
| `sell_signal` | none ("Reduce or exit"); optional `min_trim.trim_shares` (~3753) | partly |

Hidden card's own size: Tune-up / Risk Analysis card = `risk_advisor.py` ~910-916 computes `excess_dollar` / price but stores only `trim_sized` → add `rec["trim_shares"] = int(excess_dollar / px)` (None when no price). Rebalancer row = `shares_delta = int(abs(drift) / price)` (None when price ≤ 0).

## Design

1. **New gate module `stock_analyzer/act_sell_cover.py`** (pure): `item_cover(item)`, `sell_cover_summary(act, review)`, `view_sell_cover(view)` (None when the view is offline; reads `view["active"]`), `cover_state(cover, need)`. Shape `{TICKER: {"exit": bool, "shares": int | None}}`; absent = not sold. Per ticker: any exit → exit; else the largest valid count (U6: "at least N" sells don't add); else None (unknown). Never a sell (as today): kind `risk`, `critical_news`, `premortem_triggered`, macro Act items, WATCH, TIGHTEN_ONLY. Shares of a macro trim belong to `action.trim_ticker`. `held_trim_owner.brief_sell_tickers` / `view_sell_tickers` and `act_sell_mention.verified_sells` become the key sets of the maps (contracts kept); `act_sell_mention` gains `verified_sell_cover` behind the same five gate legs.
2. **`cover_state(cover, need)`**, one rule for all three surfaces:

   | cover | need | state |
   |---|---|---|
   | None (not sold) | any | show (earnings rules as today) |
   | exit | any | covered (defer; today's text) |
   | non-exit | 0 | covered (the card sells nothing) |
   | shares ≥ need (both known) | | covered |
   | shares < need | | partial: keep the card at its full size + a line |
   | shares or need unknown | | unknown: keep the card + a line |

   Equality covers (both floored whole shares). The card's size is NOT reduced by the Act sale (that would mix a Brief-time count with a render-time book: one freshness per number). Fail directions: a None map never defers; unknown never fully defers; legacy shapes (no `trim_shares`) read unknown → restored with the unknown line; a legacy `{T: str}` deferral text is kept only under an exit cover.
3. **`reconcile_tuneup(cards, deferred, view_cover)`** re-decides every single-name card: covered → deferred text; partial / unknown → a copy gains `act_cover_note`; show → as is; None → all restored, no notes; never mutates the memoized Brief. `_portfolio_tuneup` sets `subject` / `trim_shares` on the card and defers only when covered. `_SYNTH_SCHEMA_VER` 29 → 30 (or the next free number).
4. **Wording (default, U6b pattern; no `$`, `**` or share count):** partial: "🏠 Today's Act Today call also sells {T}, but fewer shares than this {noun}. Do that sale first (🏠 Home); once it is logged this {noun} is re-sized from your new book, so it is not a second sale on top of it." Unknown: "🏠 Today's Act Today call also sells {T} (🏠 Home), and its size couldn't be compared with this {noun}. Do that sale first; once it is logged this {noun} is re-sized from your new book, so it is not a second sale on top of it." `{noun}` = "ceiling trim" (Tune-up / Risk Analysis), "row" (Rebalancer). Tune-up: muted line in the card HTML via `safe_html`; Risk Analysis: `st.info` after the Problem banner; Rebalancer: `st.info` at the top of the expander + header suffix " · 🏠 Act Today sell".
5. **Rebalancer totals:** a partial row stays in `trims` at its full `shares_delta` and counts in the totals; new `act_partial_count`; "Trim needed" help: "Rows today's Act Today sell fully covers aren't counted; a row it only partly covers is counted at its own size."
6. **Out of scope:** U6b Signals / Catalyst mentions (never hide a card or quote a size; `verified_sells` must stay the same set, tested). History: `rec_events` (independent capture), G-02 ledger, `exit_signals` (pin that the new Act key isn't persisted): no change; DoD #8 n/a.

## Owner questions (defaults recommended)

- **Q1** `sell_signal` ("Reduce or exit", no count): covers only up to its `min_trim` shares, else unknown. [default] Alt: treat as an exit (defers everything). Matters mainly on the Rebalancer.
- **Q2** `risk_off_derisk` (≈ 25% trim or tighten instead): never a full cover (its smallest sale is zero; a weeks-long risk-off would hide a ceiling trim for weeks). [default] Alt: cover up to its N shares (structured count in `exit_advisor`).
- **Q3** Partial-line wording: the U6b pattern above. [default] Alt: "Selling this trim covers that sale too".
- **Q4** Rebalancer totals: a partial row counted at its full size. [default] Alts: leave it out; count it net (not recommended).
- **Q5** Rebalancer header tag " · 🏠 Act Today sell" on partial rows. [yes]
- **Q6** Stop-breach-recovery sibling (a recovered breach keeps the earnings-week trim out of the Brief Review list; daily_briefing.py ~3518 / ~3610 / ~3742, ~3926): separate row + planner design, queued right after. [default]
- **Q7** The same size-blind class inside the Brief (`_reduce_tickers` ~3426-3433 includes `deterioration_trim` (25% floor) and `sell_signal` (unsized), skipping a larger earnings-week / weak-large trim and leaving the stop card tighten-only; the beta Tune-up card dropped when its main name has any flagged card, ~460-467): new row + own design (needs a cross-lane U6 merge). [default]
- **Q8** need 0 (a card / row that sizes no trade) is covered by any Act sell on the name, sized or not. [yes, today's behaviour]

## Chunks

1. **C1** `act_sell_cover.py` + tests; `_GATE_FILES` + review-policy line; unwired.
2. **C2** producers + shared decision: `risk_advisor` `trim_shares`; Act `deterioration_trim` `trim_shares`; `_portfolio_tuneup`; `held_trim_owner` dispositions take the cover map + need, sets derived, `reconcile_tuneup` with notes; `act_sell_mention.verified_sell_cover`; `_SYNTH_SCHEMA_VER`. Opus review after C2 (with C1).
3. **C3** wiring: `rebalancer.py` (need, partial rows, totals, count; kwarg renamed `act_sell_cover`); app.py render-only (Tune-up note, Risk Analysis line, Rebalancer tag / info / KPI help, the verified-map helper). Opus review.
4. **C4** docs (lead): requirements G-33, F-25 / F-25a, F-12, F-13; architecture; User Guide; bug register (DeferSizeBlind fixed, new rows Q6 / Q7); shipped-log; CLAUDE.md; EW plan §3 "Rule" made size-aware.

## Tests the build must include

The full `cover_state` table with the invariant (covered ⇒ exit, or need == 0, or n ≥ need); `item_cover` for every kind incl. invalid `trim_shares` (0, 1.5, True, "2", NaN, None) → unknown; combining (exit wins, largest wins, sized + unknown = sized, `trim_ticker` attribution); set consistency (`view_sell_tickers`, `verified_sells`); the defect end to end (≈ 30% holding, score ≥ 55, 1-share macro PROTECTIVE_TRIM → Tune-up kept with the partial note, Risk Analysis partial, Rebalancer row in `trims` and totals); full cover still defers (EW worked example A; EW / U6b tests green); recovered breach restores with no note; unknown never hides (legacy shapes); a None map never defers; no mutation of the memoized Brief; text rules (no `$`, `**`, share count; ceiling from `SINGLE_NAME_CEILING`); Rebalancer edges (price ≤ 0, need 0, earnings note yields, priority / expanded kept); fractional positions; `risk_advisor.trim_shares`; history pins (`rec_events`, `exit_signals`); `_SYNTH_SCHEMA_VER` pin; app.py grep pins; a weekend visit with an earlier-day Brief (None → nothing deferred).
