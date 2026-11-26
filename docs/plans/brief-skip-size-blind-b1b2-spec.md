# BriefSkipSizeBlind Batch 2, B-C1 + B-C2 build spec (the fold)

**Status:** SPEC 2026-11-26 (lead, from `docs/plans/brief-breach-restore-and-fold.md` §3, owner Q4–Q7 defaults). Build = implementer; one Opus review over B-C1 + B-C2. B-C3 (beta / sharpe Tune-up, `tuneup_cover.py`) is a separate chunk and NOT in this spec. Docs (B-C4) come after.

**The bug:** inside `daily_briefing._review_list`, `_reduce_tickers` contains every Act reduce kind. So a SMALLER Act sell on a name silently skips a LARGER Review trim on the same name:
- `deterioration_trim` is a 25% floor; `sell_signal` is unsized ("Reduce or exit").
- Either one skips the earnings-week trim (~:3642) and the weak-large trim (~:3774), and turns the profit-lock stop card into a tighten-only card (~:3586).

Example: 100 shares at 13.33% weight with earnings in 3 days. The earnings rule sells to 10% (25 shares) and the deterioration floor also sells 25. At 20% weight the earnings rule sells 50, but today the card says 25.

Today only one partial fix exists: for a `sell_signal`, the weak-large size rides onto the Act card as `min_trim`. It goes through `_first_act`, which picks the first Act item with the ticker; that can be a passthrough `premortem_triggered` card, so the size is silently lost (a latent bug).

**The fix (owner Q4):** a larger Review trim is folded INTO the Act card's `min_trim`, so there is one sell card per name and it sells the largest size. The Review list for that name stays exactly what it is today.

No constant changes value; no new constant. Read every value from `constants.py`, tests included.

---

## Kinds

- **Full exits:** `stop_breach`, `deterioration_exit`. Unchanged: the Review sections skip / stop-only exactly as today.
- **Partial reduces:** `PARTIAL_REDUCE_KINDS = frozenset({"deterioration_trim", "sell_signal"})` (define in the new module). The Review sections now BUILD these names' cards normally and tag them; the fold then settles them.
- `_exit_tickers` (used for the ⛔ Exit lifecycle badge, `classify_position_state(has_exit_signal=)`) is unchanged: it still contains `sell_signal`.
- A `risk` card still never counts (its ticker is an attribution), and neither do `critical_news` or `premortem_triggered`.

## B-C1 New gate module `stock_analyzer/act_trim_fold.py` + `act_sell_cover` change

Pure: no streamlit, no I/O, inputs never mutated. Add it to `_GATE_FILES` (`.claude/hooks/pre_tool_checks.py`), with a one-line reason in `docs/review-policy.md`. Suggested reason: "folds a larger same-name Review trim (profit-lock, earnings-week, weak-large, the deterioration floor under a Sell) into the Act Today card's `min_trim`; a wrong call sells less than a rule asked, or shows two sells on one name". Its docstring states THE SHAPE and THE FAIL DIRECTIONS, like `act_sell_cover.py` / `brief_trim_merge.py`.

```python
FOLD_KEY = "_fold_subject"          # set by _review_list on a tagged card (value = upper ticker)
_FOLD_PREFIX = "_fold_"             # every fold carrier key starts with this; fold() strips them all
PARTIAL_REDUCE_KINDS = frozenset({"deterioration_trim", "sell_signal"})
_RANK = {"lock": 0, "weak_large": 1, "earnings": 2, "deterioration_floor": 3}   # tie order (U6's order + the floor last)

def fold(items, act_today, extra_candidates=None) -> tuple[list, dict]:
    """Returns (items_out, payloads). payloads = {T: min_trim dict} for each subject whose Act card must grow."""
```

**Per subject T** (the upper-cased `FOLD_KEY` values found in `items`):
1. `act_item` = the FIRST item in `act_today` whose `decision_bucket._ticker` is T AND whose `kind` is in `PARTIAL_REDUCE_KINDS`. This fixes `_first_act`: a `premortem_triggered` card is never chosen. If there is none, the fail-safe applies.
2. Candidates:
   - every tagged item with action type `TRIM_AND_TIGHTEN` → reason `lock`;
   - every tagged `TRIM_TO_TARGET` with `reason_key` `earnings` or `weak_large`;
   - plus `extra_candidates.get(T, [])`, already-shaped `deterioration_floor` candidates (below).
   - Each candidate's `trim_shares` must satisfy `act_sell_cover.valid_shares` (an int ≥ 1, never bool or NaN). If ANY candidate's count is invalid, apply the fail-safe for T.
3. `N_A` = `act_sell_cover.valid_shares(act_item.get("trim_shares"))` for `deterioration_trim`; always None for `sell_signal` (unsized).
4. M = the winning candidate: most `trim_shares`, ties by `_RANK`. When there are no candidates, M is None.
5. A payload exists only when M is not None and (N_A is None or N_A < M's shares):
   ```
   payloads[T] = {
       "trim_shares", "trim_dollars", "from_weight", "target_weight", "reason_key",  # from the winner
       "directive_tail", "why_tail",                                                 # text, below
   }
   ```
   `target_weight` is None for `lock` / `deterioration_floor`.
6. T's tagged items in `items_out`:
   - a tagged `TRIM_AND_TIGHTEN` becomes `{"type": "TIGHTEN_ONLY", "new_stop": it["_u6_tighten"]["new_stop"]}`, with `why = it["_u6_tighten"]["why"]`;
   - a tagged `TIGHTEN_ONLY` keeps its action, with `why = it["_u6_tighten"]["why"]`;
   - a tagged `TRIM_TO_TARGET` or `WATCH` (earnings / weak-large) is DROPPED.
   This is exactly today's output for a reduced name: a stop-only card with the under-reduce why, and no earnings / weak-large card. It holds whether or not a payload exists.
7. **Fail-safe for T:** no `act_item`, an invalid candidate count, or a tagged lock / stop card missing a usable `_u6_tighten` (a dict with `new_stop` and a str `why`) → T's tagged items are left AS BUILT (every sell stays visible, beside the Act card) and there is no payload for T.
8. Every item in `items_out` has its `_fold_*` keys removed (copies). Untagged items pass through unchanged. The order of `items_out` follows `items`, and the decision does not depend on input order.

**Text** (no `$`, no `**`; the share count stays, since the card already prints "(n of m shares)" and the 📒 button needs it; `share_word` for 1 vs many):
- `directive_tail`, by the winner's reason:
  - `earnings`: `f" At minimum, trim {from_w:.1f}% → {target:.1f}% before the earnings report ({N} {share_word(N)})."`
  - `weak_large`: `f" At minimum, trim {from_w:.1f}% → {WEAK_LARGE_TRIM_TO_PCT:.1f}% ({N} {share_word(N)})."`
  - `lock`: `f" At minimum, sell {STOP_PROFIT_LOCK_TRIM_PCT:.0f}% of the position ({N} {share_word(N)}) to lock part of the gain."`
  - `deterioration_floor`: `f" At minimum, trim about {DETERIORATION_TRIM_SUGGESTED_PCT:.0f}% of the position ({N} {share_word(N)}) into the weakness."`
- `why_tail`, by the winner's reason (carriers below):
  - `weak_large`: `f" Also your largest weak holding: weight {from_w:.1f}% ≥ {LARGE_POSITION_WEIGHT_PCT:.0f}% with score {composite_score_text(score, WEAK_CONVICTION_SCORE)} < {WEAK_CONVICTION_SCORE}."` (today's sentence, unchanged)
  - `earnings`: `f" Earnings {label}: the earnings-week rule trims {from_w:.1f}% → {target:.1f}% before the report."`
  - `lock`: `f" The stop is {gap:.1f}% away with P&L {pnl:+.1f}%, so part of the gain is locked first."`
  - `deterioration_floor`: `f" It is also deteriorating: down {dd:.1f}% from its peak and below its trend, so the deterioration rule's trim floor applies."`
  - then, when other VALID candidates lost: `f" This also covers {_join(texts)}; none of them sells more than {'this' if N == 1 else 'these'} {N} {share_word(N)}."` The texts, in `_RANK` order:
    - lock → `"the profit-lock trim of {STOP_PROFIT_LOCK_TRIM_PCT:.0f}% of the position ({n} {sw})"`
    - weak_large → `"the low-score trim to {target:.1f}% ({n} {sw})"`
    - earnings → `"the earnings-week trim to {target:.1f}% ({n} {sw})"`
    - deterioration_floor → `"the deterioration trim floor ({n} {sw})"`
    
    Use the same "and" join as `brief_trim_merge._join` (import it or duplicate the three-line helper).
- If a carrier the winner's why needs is missing or not finite (score / label / gap / pnl / dd), drop only that reason's first sentence and keep the directive tail and the "also covers" clause. Never print "nan" or "None".

**`act_sell_cover.item_covers`, kind `deterioration_trim`:** shares = the max of `valid_shares(item["trim_shares"])` and `valid_shares(item["min_trim"]["trim_shares"])` (when `min_trim` is a dict), with None only when both are None. Update the module docstring line. `sell_signal` is unchanged (it already reads `min_trim`).

## B-C2 `stock_analyzer/daily_briefing.py` `_review_list`

1. Next to `_exit_tickers` / `_reduce_tickers` (~:3442), add:
   - `_full_exit_tickers` = Act tickers whose kind is `stop_breach` or `deterioration_exit`;
   - `_fold_tickers` = Act tickers whose kind is in `act_trim_fold.PARTIAL_REDUCE_KINDS`, minus `_full_exit_tickers`.
   
   Keep `_reduce_tickers` and `_exit_tickers` defined (the lifecycle badge reads `_exit_tickers`). Grep every remaining use of `_reduce_tickers` in the function and report any you leave.
2. **Section 1** (approaching stop): `_under_reduce` now means `ticker in _full_exit_tickers` (today's tighten-only path, unchanged for full exits). For `ticker in _fold_tickers`, build the card through the normal branches (TRIM_AND_TIGHTEN when the lock fires, else its TIGHTEN_ONLY branch) and add `FOLD_KEY: T`, `"_fold_gap": gap`, `"_fold_pnl": pnl_pct`. The `_u6_tighten` carrier is already there.
3. **Section 2** (earnings): skip when `ticker in _full_exit_tickers` (was `_reduce_tickers`). For `_fold_tickers`, build normally and add `FOLD_KEY: T`, `"_fold_label": label`.
4. **Section 3** (weak-large): replace the whole `if ticker.upper() in _reduce_tickers:` block, including the inline `min_trim` attach and `_first_act`, with:
   - `ticker in _full_exit_tickers` → `continue` (dropped, as today);
   - `ticker in _fold_tickers` → build the WATCH or TRIM_TO_TARGET card normally and add `FOLD_KEY: T` (the `_u6_score` carrier is already there).
5. **Q7 carrier:** build `extra = {T: [cand]}` for each T in `_fold_tickers` whose Act item kind is `sell_signal` AND which has a `deterioration` entry with tier `"TRIM"`. Mirror `_act_today` section 2.5:
   - `n = _trim_sizing.fraction_trim_shares(d["shares"], DETERIORATION_TRIM_SUGGESTED_PCT)`, only when `d["shares"] > 0 and n >= 1`;
   - `cand = {"reason_key": "deterioration_floor", "trim_shares": n, "trim_dollars": round(n * price, 0), "from_weight": d.get("weight_pct"), "target_weight": None, "_fold_dd": d.get("dd_from_peak_pct")}`, with price = `d.get("price")` or the book row's Price, and `trim_dollars` None if neither is finite.
   
   A deterioration EXIT under a Sell is NOT a candidate (Q7: a precedence call, separate row).
6. Run the fold right BEFORE the U6 `one_trim_per_name` try block (~:4123), in its own try:
   ```python
   try:
       items, _fold_payloads = act_trim_fold.fold(items, act_today, extra_candidates=extra)
   except Exception as e:
       _log.warning("brief act-trim fold failed, keeping every Review trim: %s: %s", ...)
       items = act_trim_fold.strip_carriers(items); _fold_payloads = {}
   ```
   Add `strip_carriers(items)` to the module: it removes the `_fold_*` keys (copies). Then apply each payload to the SAME `act_item` that fold chose: export `act_trim_fold.act_item_for(act_today, T)` and use it in both places.
   - `act_item["min_trim"] = {k: payload[k] for k in ("trim_shares", "trim_dollars", "from_weight", "target_weight", "reason_key")}`
   - `directive += directive_tail`, `why += why_tail`, `reason = f"{directive} {why}".strip()`.
   
   This still mutates `act_today`, as today's weak-large attach does; update the docstring's "Side effect" paragraph.
7. `_SYNTH_SCHEMA_VER` 31 → 32 (app.py). Prefix the comment with "bumped: a larger same-name Review trim (profit-lock, earnings-week, weak-large, the deterioration floor under a Sell) folds into the Act Today card's min_trim, which now carries reason_key (BriefSkipSizeBlind); was 31: ...".

## app.py (render, ships with B-C2)

- ~:12707: `_render_journal_context` runs only when `(_db_item.get("min_trim") or {}).get("reason_key") == "weak_large"`. A min_trim with no `reason_key` (a legacy memoized Brief) keeps today's behaviour, so treat missing as weak_large. Put that decision in `act_trim_fold.shows_journal_context(item) -> bool`; app.py only calls it.
- Update the comment at ~:12764 ("Minimum trim attached to a sell_signal card …") to say any partial reduce card and any folded reason. The 📒 Log this trim button already reads `min_trim.trim_shares`.
- No other app.py change.

## Tests (new `tests/test_act_trim_fold.py`; extend `test_act_sell_cover.py` and `test_daily_briefing.py`)

1. **Fold matrix (pure):** for each Act kind {deterioration_trim with N_A ∈ {None/0, 25}, sell_signal} × candidate sets {none, lock, earnings, weak_large, earnings + weak_large, all three, deterioration_floor (sell_signal only)}, check the payload / no payload, the winner, the tie order, and that the tagged items in `items_out` equal today's under-reduce output.
2. **Crossovers (100 shares, through `_review_list`, using the real sizing helpers, `make_port_df`):** a deterioration floor of 25 vs the earnings trim to 10% at weights just under / at / over 13.33% (no payload at or below; a payload above); weak-large at 10.67%; the standing trim to `SINGLE_NAME_CEILING` at 20%; the lock vs the floor (both 25% → tie → no payload).
3. **"Never sells less" property:** for every fixture, the Act card's cover from `act_sell_cover.item_covers` is ≥ every candidate's `trim_shares` and ≥ N_A, and the Review list carries no TRIM_AND_TIGHTEN / TRIM_TO_TARGET on T.
4. **Fail-safes:** an invalid `trim_shares` (0, 1.5, True, NaN, "2"); no partial Act item on T; a missing `_u6_tighten`; `fold` raising (monkeypatch) → T's cards stay as built, carriers stripped, Act card unchanged, warning logged.
5. **Premortem ordering:** `act_today = [premortem_triggered on T, sell_signal on T]` plus a weak-large T → `min_trim` lands on the sell_signal, never the premortem card (the `_first_act` bug).
6. **Regressions:**
   - Sell + weak-large: the same `min_trim` shares / dollars / weights as before, plus `reason_key="weak_large"`. The directive's "≈ $…" is gone (Q5: update the pinned tests in `test_daily_briefing.py` / `test_trim_floor_migration.py` on purpose, and grep `tests/` for the old "≈ $" min_trim text).
   - Full exits (stop_breach, deterioration_exit): the Review output is byte-identical to before.
   - A no-reduce day: the Review output is byte-identical.
7. **Stop card under a partial reduce:** T has a deterioration_trim and a critical-gap profit-lock → the Review list has the TIGHTEN_ONLY with the under-reduce why (as today), and the lock's shares are a fold candidate.
8. **`act_sell_cover`:** deterioration_trim with `trim_shares` 25 and `min_trim` 40 → 40; `min_trim` invalid → 25; both invalid → None. sell_signal unchanged.
9. **Text rules:** no `$`, no `**`, no "nan" / "None" in any tail; singular / plural; a missing carrier drops only its sentence.
10. **Breach interplay:** `tests/test_breach_recovery.py` still passes (the alternate pass runs the fold too). Add one case: a recovered T with a sell_signal + earnings trim restores ONE sell card carrying the earnings `min_trim`.
11. **Pins:**
    - `_SYNTH_SCHEMA_VER = 32`.
    - `min_trim` is not written by the `exit_signals` capture (AST / grep the capture block in app.py: it reads named keys, and `min_trim` isn't one).
    - `stock_analyzer/act_trim_fold.py` is in `_GATE_FILES` (assert `_GATE_FILES` only; never read docs/review-policy.md in a test).
    - `_reduce_calls` / `all_flagged_tickers` / `reduce_call_tickers` sets are unchanged on the matrix fixtures.

## Run

- Only the targeted files: `tests/test_act_trim_fold.py tests/test_act_sell_cover.py tests/test_daily_briefing.py tests/test_trim_floor_migration.py tests/test_breach_recovery.py tests/test_brief_trim_merge.py` (if it exists), plus every test that greps app.py or pins `_SYNTH_SCHEMA_VER` (grep `tests/` for `_SYNTH_SCHEMA_VER`). Use `-q -p no:cacheprovider`. NOT the full suite.
- `python scripts/check_antipatterns.py`, `python scripts/check_constants_documented.py`, `python -m py_compile app.py`.
- `git add` the new module and the new test file, but don't commit (`_GATE_FILES` members must be tracked).
- Report: files changed, test counts, every remaining `_reduce_tickers` use, and anything in this spec that didn't match the code. STOP and report instead of improvising if the code contradicts the spec (e.g. U6 relies on a tagged card in a way this breaks).
