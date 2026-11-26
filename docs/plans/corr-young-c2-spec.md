# CorrYoung C2 — disclosure and view decision (lead spec, 2026-11-25)

Parent: `docs/plans/corr-young-holding.md` §7 (owner Q1 = B, Q2, Q3 pairwise, Q4) and the C1 review list in its Status line. C1 (local commit, not pushed) built `corr_window.corr_matrix` / `corr_coverage` with `left_out` [{ticker, reason short_history|no_prices, n_returns, weight_pct?}], `n_obs` (fewest shared returns behind any measured pair), `n_obs_max`, `min_returns`. C2 is pure code only: no app.py (C4), no gates / banner / Grow Today (C3, C3b). Nothing is pushed until C1–C5 are reviewed.

All texts plain: no `**`, no `$`, no markdown. A memoised pre-C1 coverage dict (no `left_out` / `n_obs_max` / `min_returns`) must be tolerated everywhere: treat `left_out` as [] and `min_returns` as `CORR_MIN_OBS_TRUSTED`.

## A. One phrase helper shared with risk_window (gate files)

- `risk_window.py`: add public `holdings_phrase(items, *, need) -> str` that returns exactly what `_names(...)` returns today for items given `_need` (refactor `_names` / `_coverage_note` to call it; texts unchanged; all existing risk_window tests pass untouched). Items without `weight_pct` (or None) read "size unknown" today; ADD a keyword `with_size: bool = True` — when False the share part is omitted entirely: "ANT (4 trading days since its first close, 20 needed)".
- `corr_window.py`: make `_weight_pct` use public (rename the import per the C1 review: expose `risk_window.weight_pct` publicly, keep `_weight_pct` as an alias if anything else imports it).

## B. `corr_window.py` (gate module) — the view decision and notes

- `left_out_note(coverage) -> str | None`: None when no `left_out`. Else: "Correlations measured without " + holdings_phrase(left_out, need=min_returns, with_size=<True only if every item has a numeric weight_pct>) + "; its pairs are not shown and it is not part of the score." (plural: "their pairs are not shown and they are not part of the score."). Example: "Correlations measured without ANT (3.6% of the book: 4 trading days since its first close, 20 needed); its pairs are not shown and it is not part of the score." A no_prices item reads "no price history" via the phrase helper (neutral wording, true for a download failure).
- `thin_note(coverage) -> str`: "⛔ Correlations can't be measured on this sample: only N shared daily returns (20 needed). A sample this small is unreliable in both directions: unrelated stocks can read as moving together and a real pair can read as independent, so no pairs, clusters or diversification score are shown. Adds on any pair that crossed the line stay paused until it can be measured." N = int(n_obs), 20 = min_returns (or `CORR_MIN_OBS_TRUSTED`).
- `view_state(corr_df, corr_coverage, corr_unchecked) -> {"state", "notes": [str], "left_out": [...]}`:
  - `"withheld"` when `portfolio.correlation_sample_state(corr_coverage) == CORR_SAMPLE_THIN`, OR the matrix is empty/None while coverage has a non-empty `left_out` (fewer than 2 counted). notes = [thin_note] (+ left_out_note when present). Screens show only these notes: no pairs, heatmap, clusters, score.
  - `"none"` when the matrix is None/empty and there is no `left_out` (fewer than 2 holdings with prices: today's "Need at least 2 holdings…" path; notes []).
  - `"unknown"` when coverage is not a dict (sample state UNKNOWN): show as today; notes = [today's unknown caption text is rendered by the app as now — return notes [] and let C4 keep the existing caption].
  - `"partial"` when TRUSTED and (`left_out` non-empty or `corr_unchecked` is a non-empty list): notes = [left_out_note] (and nothing for names already covered by left_out; names in corr_unchecked NOT in left_out are the old "price history failed to load" case → keep `util.correlation_unchecked_note` text for those, C4 renders it).
  - `"full"` when TRUSTED and nothing left out / unchecked: notes [].
  - Never raises; bad input → `"unknown"`.
- Import note: `corr_window` must not import `portfolio` at module level if that creates a cycle (portfolio imports corr_window): use a function-local import or move the sample-state call to compare `n_obs` with `min_returns` directly (same rule as `correlation_sample_state`: non-dict / missing / non-integer n_obs → unknown; n_obs < floor → thin). Prefer calling `portfolio.correlation_sample_state` via a local import so there is one rule.

## C. `util.py` notes (read coverage reasons)

- `correlation_unchecked_note(corr_unchecked, corr_coverage=None)`: new optional arg. For tickers in `corr_unchecked` that appear in `corr_coverage["left_out"]` with reason short_history: say "T has only N daily returns of price history (20 needed)" (plural form for several); for the rest keep today's "price history failed to load" wording. Keep the closing clause that the classification describes the OTHER holdings only. When `corr_unchecked is None` but coverage has a non-empty `left_out` (fewer than 2 counted): name the left-out holdings with their reasons instead of the "unknown this session — open 🏠 Home" text. Existing callers that pass one arg behave exactly as today.
- `correlation_record_withheld_note(corr_unchecked, corr_coverage, *, subject)`: the "missing from the correlation matrix" clause splits: short_history left-out names → "ANT has only 4 daily returns (20 needed)" (plural "…have only …"), other names → today's "missing from the correlation matrix" clause. The thin clause drops the "capped by X's short price history" branch entirely (unreachable/false after C1): thin always reads "only N shared daily returns (needs 20), because the holdings' price histories only overlap on those days". Still returns None exactly when `correlation_claim_verified` is True (called, not re-derived).
- `diversification_label_text(div_label, corr_unchecked, corr_coverage=None)`: when the sample is THIN return "Unavailable · sample too thin" regardless of `div_label` (the C3 producer will also publish "Unavailable"; this keeps any stale label honest). Unchanged otherwise.

## D. `pair_add_gate.unchecked_disclosure(unchecked, pair_checked, cluster_checked, corr_coverage=None)`

Same split as C: a short_history left-out name reads "T has only N daily returns of price history (20 needed), so its pairs couldn't be checked" instead of "price history couldn't be loaded"; others unchanged; `unchecked is None` with a coverage `left_out` names them. Existing 3-arg callers unchanged.

## E. `portfolio_health.py` diversification sub-score

Where it returns a TRUSTED score, add `detail["left_out_note"] = corr_window.left_out_note(corr_coverage)` when not None (C4 renders it beside the sub-score). No change to the score or the withheld paths.

## Tests

- `holdings_phrase` reproduces `_names` byte-for-byte on the existing risk_window cases; `with_size=False` omits the share.
- `left_out_note` singular/plural, with and without weights, no_prices wording; never contains "move together", `**`, `$`.
- `view_state` at every state: thin (n_obs 4), all-NaN (n_obs 0), fewer-than-2-counted (empty matrix + left_out) → withheld; empty + no left_out → none; coverage None → unknown; trusted + ANT left out → partial with the note; trusted + an unpriced name only in corr_unchecked → partial, note list empty from corr_window (util text covers it); trusted full → full; pre-C1 coverage dict (no left_out) trusted → full; bad input → unknown.
- `thin_note` contains "unreliable in both directions" and never "move together" / "toward zero".
- util / pair_add_gate notes: short_history vs failed-load wording, mixed list, None + left_out, one-arg/three-arg legacy calls byte-identical to before (snapshot the old outputs in the test first), record note never contains "capped by", still None iff `correlation_claim_verified`.
- `diversification_label_text` thin → "Unavailable · sample too thin".
- portfolio_health trusted + left_out → `detail["left_out_note"]`; full → no key.
- Clock-free; antipatterns.

Checks: py_compile; tests/test_corr_window.py, tests/test_risk_window*.py, tests/test_util*.py, tests/test_pair_add_gate.py, tests/test_portfolio_health*.py and any test that pins these note texts (grep tests/ for "price history failed to load", "couldn't be loaded", "capped by", "sample too thin", "unknown this session") at `-n 4`; `python scripts/check_antipatterns.py`. No full suite, no commit. Don't touch app.py, daily_briefing.py, home_risk_synthesis.py, cluster_add_gate.py, CLAUDE.md.
