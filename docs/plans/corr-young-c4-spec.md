# CorrYoung C4 — app.py wiring (lead spec, 2026-11-25)

Parent: `docs/plans/corr-young-holding.md` §7 (owner Q1 = B, Q2, Q3, Q4) and Status line; the C1 / C2 / C3 review must-cover lists (plan Status line; `docs/plans/corr-young-c3-spec.md` "Notes for C4" and "C3 Opus review … what C4 / C5 must cover"). C1–C3 are local commits (pure code). C4 makes the screens use them. Line numbers are approximate: re-find each site.

Rules for every item: render only — no comparison against a `constants.py` value in app.py (`POLICY_DECISION_IN_RENDER`); decisions come from `corr_window.view_state`, `util` / `pair_add_gate` notes and the block entries' `"sample"` tag. Texts are plain (`st.caption` / `st.info` / `st.warning` with no `**` and no `$`); no new dynamic `unsafe_allow_html` (use `util.safe_html` if an existing HTML block must carry dynamic text). Variables must be in scope on every path (Home MISS vs memo HIT; pages reached without a Home visit). Bump `_SYNTH_SCHEMA_VER` 28 → 29 (comment prefix: "bumped: correlation coverage carries left_out / n_obs_max and the matrix leaves young holdings out (CorrYoung); was 28: …").

## 1. Brief wiring (Q2 live)

Pass `corr_coverage=_corr_cov` (the coverage from this run's correlation bundle; on the memo-HIT path the cached one) to BOTH `build_daily_briefing` calls (~7600 stale-composite rebuild, ~8052 MISS). Verify `_corr_cov` is defined on both paths before the call.

## 2. Home Grow Today "Adds held" rows (~11765-11800)

`entry_held_adds` rows with `check == "corr_unmeasured"`: render under their own heading "🔗 Adds held — correlation couldn't be measured" (other rows keep "📰 Adds held — entry check"). For every row: strip one trailing period from `reason` before the renderer adds ".", and strip a leading "when " from `clears_when` (so "Clears when: the correlation sample reaches 20 shared daily returns."). Don't change the existing ENTRY rows' text.

## 3. Home Structural banner (~8858-8888)

When a pair / cluster block entry has `sample == "thin"`, don't say "new correlation cluster formed", "paused until you review … Structural Scan" or "standing danger-tier correlated pair (G-26)". Use: "🔍 Correlation couldn't be measured this run (only N shared daily returns, 20 needed), so adds to A, B are paused until it can be. This is not a finding that they move together." (N = the entry's `n_obs`, 20 = coverage `min_returns` / `CORR_MIN_OBS_TRUSTED` passed as a value, never a literal). Trusted entries keep today's text. If thin and trusted entries both exist (can't today: tag is book-wide) render each kind with its own text.

## 4. 🔗 Risk Analysis correlation section (~19019-19142)

Call `corr_window.view_state(corr_df, _corr_cov, corr_unchecked)` once and branch on its state:
- `withheld`: show ONLY the view_state notes (thin box; + left_out_note when present) as `st.warning` / `st.caption`. No score tiles, no pair cards, no heatmap, no "These positions move together", no "Classification:" caption, and NOT the util unchecked note (its "other holdings only" tail is false here; name failed loads from `unchecked_rest` in plain words only if non-empty: "Price history couldn't be loaded this run for X.").
- `partial`: today's tiles / pairs / heatmap from the counted holdings, plus `left_out_note` (once), plus `util.correlation_unchecked_note(unchecked_rest, _corr_cov)` ONLY for `unchecked_rest` names (never the left-out names, so ANT isn't named twice).
- `full`: as today; if `corr_unchecked is None` keep today's util "unknown this session" caption.
- `unknown`: as today (existing unverified caption).
- `none`: today's "Need at least 2 holdings with price history to compute correlations." — but if the producer crashed (corr_df empty and `_corr_cov is None` and `corr_unchecked is None` while 2+ holdings are held), say "The correlation reading couldn't be built this run — open 🏠 Home and click 🔄 Refresh Signals." instead of the "need 2 holdings" line.
- Replace the coverage block "Computed on N daily returns shared by all K of your M holdings … shortest is X … usually what caps the sample" with the pairwise meaning: "Computed on each pair's own shared days: the thinnest pair has N daily returns, the longest N_max." (+ left_out_note in partial). Remove the false sentence "A thin sample pushes correlations toward zero, which shows up as diversification you may not have." (~19071-19072) entirely; add a test that "toward zero" never returns to app.py.
- Pass `_corr_cov` to every `correlation_unchecked_note(...)` and `correlation_record_withheld_note(...)` call in this section.

## 5. 🧩 Correlation Clusters (~21362-21397)

Use the same `view_state`: `withheld` → only the notes, no clusters; `partial` → clusters from counted holdings + left_out_note; the existing no-cluster branch keeps its sample check. Pass `_corr_cov` to the record-withheld notes there (~21376, ~21890, ~21935) and Structural Scan run/save notes.

## 6. Other note call sites

- `pair_add_gate.unchecked_disclosure(...)` (~12025): pass `_corr_cov`.
- Portfolio Health (~24880 / ~25133): render `detail["left_out_note"]` beside the diversification sub-score when present; pass `_corr_cov` to its "holding(s) not in the matrix" context if it calls the util note.
- 📈 Analysis multi-ticker matrix (~30228-30232): build via `corr_window.corr_matrix` (session-dated, young names left out) and show `corr_window.left_out_note(corr_window.corr_coverage(...))` as a caption when someone is left out.
- Forward-sim stop-cluster note (~20934-20960): when a stop name is left out of the matrix, say so ("X isn't in the correlation reading: …") instead of dropping it silently; reuse `left_out_note` wording.
- `app.py ~20950` reads coverage keys with the old meaning: update to the pairwise meaning.

## 7. User Guide (app.py `elif page == "📖 User Guide":`)

🔗 Risk Analysis bullet: one sentence: "A holding with fewer than 20 trading days since its first close is left out of the correlation pairs, clusters and diversification score and named under them; each other pair is measured on all the days both stocks traded. When even that leaves too few shared days, no pairs, clusters or score are shown and adds that crossed the line stay paused until correlation can be measured." 🧩 Clusters: same idea, one clause. Home banner: "the diversification score only raises Home's banner when it rests on enough shared days." Pin the 20 to `CORR_MIN_OBS_TRUSTED` with a test (guide string is plain, so test `str(CORR_MIN_OBS_TRUSTED)` appears in that sentence).

## Tests

Source-pin / AST tests (no app.py import): both `build_daily_briefing` calls pass `corr_coverage=`; the corr_unmeasured heading exists; "toward zero" absent from app.py; Risk Analysis calls `view_state` and, in the withheld branch, renders no pair cards / heatmap (pin the branch structure); `_SYNTH_SCHEMA_VER` 29 and its pins updated. Antipattern + POLICY_DECISION_IN_RENDER clean.

Checks: `python -m py_compile app.py`; tests that pin app.py text / structure for these areas (grep tests/ for "These positions move together", "Adds held", "usually what caps", "toward zero", "_SYNTH_SCHEMA_VER", "Structural", "Need at least 2 holdings") plus tests/test_corr_*.py at `-n 4`; `python scripts/check_antipatterns.py`. No full suite, no commit. Don't touch cron_runner.py, headless_alert_engine.py, CLAUDE.md.
