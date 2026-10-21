# Valuation target source — provider first, saved research as fallback (item C, F-311)

**Status:** C1 SHIPPED 2026-10-21 (`637c0e9`, chooser + dry-run, not wired). C2 (wire into scoring, mandatory Opus review) built 2026-10-21. C3 (Analysis page labels) and the D-6 follow-up NOT started.

## Problem

The Valuation pillar's PT Upside leg (25 points) scored the owner's saved `analyst_coverage` targets first (90-day window, `derive_consensus` averaging every row, the same firm counted more than once) and only fell back to the data provider's consensus target. On SPCX one 71-day-old saved $140 target scored 0/25 while Yahoo Finance's 31-analyst mean target was unused on the same page; when an older saved $230 aged out of the window the leg fell 20/25 → 0/25 with no new information. Saved research has no measured directional edge (F-154c; 48% accuracy, n=442).

## Owner decisions (2026-10-21)

- **Options 1 + 4:** provider target first, saved research only as a fallback; always label which target scored.
- **D-1:** the Analyst Consensus (rating) leg keeps reading saved research, labelled. Not switched to the provider's `recommendationMean` — that would add a new scored input to nearly every ticker, which the 2026-09-13 analyst-weight audit already declined (`docs/plans/analyst-weight-audit.md` §8). It retires itself as the 90-day window empties.
- **D-2:** provider target needs ≥ 3 analysts (`VALUATION_PT_MIN_ANALYSTS`); a count the provider doesn't report → not scored.
- **D-3:** saved fallback = newest target per firm, ≥ 3 distinct firms.
- **D-4:** not scored above 3.0 × price (`VALUATION_PT_MAX_TARGET_RATIO`), upper side only. Planner's rationale, shown to the owner with the option: guard against a target that hasn't caught up with a stock split (it would otherwise score 25/25); no lower bound because anything under 0.95 × price already scores 0/25. In the dry-run the 8 rejections were small caps with genuine (speculative) targets, not split leftovers — the owner kept 3× knowing that.
- **D-5:** `COMPOSITE_WEIGHTS_VERSION` not bumped (weights unchanged); dated cutover note in `constants.py` and the docs instead.
- **D-6:** `targets.py` (Trade Plan) and `quick_research` still read the raw provider target — moving them onto the same chooser is a separate reviewed follow-up.
- **D-7:** run the dry-run before C2; push C2 after 16:00 ET or at the weekend.

## Dry-run (owner, 2026-10-21, `scripts/valuation_source_dryrun.py`)

370 names (held ∪ watchlist ∪ in-window saved research); 16 could not be loaded (foreign / delisted symbols). SPCX's old Valuation reproduced the live 21.5. 68 composites changed; 14 crossed 44/65/75. Held: SPCX 46.6 → 58.2 (Hold), WDAY 66.0 → 68.6 (Buy), ISRG 65.1 → 61.4 (Buy → Hold), PLTR 63.9 → 61.8, TSLA 54.4 → 49.5. Up to Buy/Strong Buy: BZUN, GLW, MRVL, ONON. Rejections: 17 too few firms, 8 too few analysts, 8 above 3× price (all small caps), 3 no target.

## Chunks

- **C1** `stock_analyzer/valuation_target.py` + tests + dry-run script (pure, not wired).
- **C2** `constants.py` (two constants + cutover note), `bundle_loader.py` (one call per bundle, `val_pt_choice`, `upside` from the chosen target), `valuation.py` (fallback removed, finite > 0 guard, source tags in the signal text), `_GATE_FILES`. Mandatory Opus review.
- **C3** app.py labels: Analysis Valuation tile ("🎯 PT Upside target: …"), hide the second unlabelled "Analyst Target" in the Business Quality tile, Coverage tab (fetched date + "Used in scoring", the reconciliation caption's two bare `$` (LaTeX), the false "analyst targets never move a gate or the engine's verdict" footer), Home chips "your saved research … awareness only" (~9858, ~11491), User Guide Valuation lines, and a source on the "X% upside to analyst target ($Y)" text at ~24122 / ~24774 / ~25441 (since C2 it can be the saved-research mean; `bundle["upside_tag"]` is published for this and has no reader yet). Also: `pt_label`'s "no saved research target in the last 90 days" hardcodes `VALUATION_COVERAGE_FRESH_DAYS` and is on screen since C2 (Deep Dive "Not scored" line) — pass the constant in; `provider_name` names the provider that served the bundle, not the one that wrote a fundamentals-cache row; a test pinning the as_of branch order (fundamentals-cache date beats a stale bundle's date — reviewer's mutation survived). Screenshot-verified. (C2 already shows "Not scored — <reason>" in the Deep Dive Valuation list when the leg isn't scored, and short source tags on scored legs.)
- **D-6** follow-up, **no later than C3**: Trade Plan / Quick Research on the same chooser. Until then a target the chooser rejects still sets `targets["base"]` (`targets.py` ~82-100), which feeds the R:R check in `watchlist_advisor`'s ENTER_NOW gate (`RR_ENTRY_MIN`) — so it can still help a name pass a buy gate, and C3's labels would contradict the Trade Plan on screen.

## Risks to watch after deploy

- A ticker whose analyst count moves between 2 and 3, or whose target sits near 3× price, switches the leg on and off; its composite can flicker across 65.
- Names that crossed 65 can show as "newly ready" in the next Watchlist email (a one-time cold-start effect).
- FMP-served or FMP-backfilled info sets `targetMeanPrice` but never `numberOfAnalystOpinions` (`fmp_provider.py` ~426), so on such a bundle the provider target is "analysts_unknown" and not scored — a day when yfinance's info is sparse becomes a source of score flicker. Low impact while FMP returns 402.
- A cached bundle (bundle cache) doesn't keep `_source`, so its label reads "data provider" rather than "Yahoo Finance".
