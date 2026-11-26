# BriefBreachRestore Batch 1 build spec (A-C1 + A-C2 + A-C3)

**Status:** SPEC 2026-11-26 (lead, from `docs/plans/brief-breach-restore-and-fold.md` §2, owner Q1–Q3 + Q10 defaults). Build = implementer; one Opus review over C1–C3; docs (A-C4) after.

**The bug:** a stop breach on T makes the Brief build skip every other per-name call on T (Act lane: sell_signal, deterioration EXIT / TRIM, critical news, the T-attributed risk card; Review lane: earnings-week trim, weak-large trim, deterioration Watch, news Watch). When the live price recovers, `act_today_view` demotes the breach to "Resolved", and the memoized Brief has nothing else on T, so a name with a deterioration EXIT or a composite Sell shows no sell at all.

**The fix:** on a breach day the build runs a second, counterfactual pass with R's breach rows skipped and stores T's would-be cards under `brief["breach_alternates"]`. Only `act_today_view` reads that key, and only for a ticker whose breach it demotes. The raw `act_today` / `review_list` lists are unchanged, and so is every raw reader (`_reduce_calls`, `all_flagged_tickers`, `exit_signals` capture, `brief_sell_tickers`, the ledgers).

No constant changes value and no new constant. Never write a threshold literal: read `STOP_RECOVERY_MARGIN_PCT` etc. from `constants.py`, and in tests too.

---

## A-C1 New gate module `stock_analyzer/breach_recovery.py`

It is pure: no streamlit, no I/O, and it never mutates its inputs. Register it in `_GATE_FILES` (`.claude/hooks/pre_tool_checks.py`, next to `stock_analyzer/act_sell_cover.py`) and add a one-line reason to `docs/review-policy.md` in the same style as the 2026-11-26 `act_sell_cover.py` line. Suggested reason: "what a recovered stop breach restores: the per-name Act / Review cards the breach suppressed at build, read only by `act_today_view` for a ticker whose breach it demoted; a wrong call shows no sell on a recovered deterioration-exit name, or two sells on one name". Write a module docstring that states THE SHAPE and THE FAIL DIRECTIONS, like `act_sell_cover.py` does.

```python
ALTERNATES_KEY = "breach_alternates"
RESTORED_KEY   = "_restored_after_recovery"
_EXCLUDED_ACT_KINDS = frozenset({"stop_breach", "premortem_triggered", "macro", "risk_off_derisk"})

def breach_tickers(act_today) -> frozenset[str]
    # Upper-cased tickers of items with kind == "stop_breach". Non-list / non-dict items are skipped.

def alternates_for(breach, act_alt, review_alt) -> dict
    # {T: {"act": [...], "review": [...]}}, with EVERY T in `breach` present (empty lists = checked, nothing else).
    # act:    items of act_alt where decision_bucket._ticker(it) == T and kind not in _EXCLUDED_ACT_KINDS.
    # review: items of review_alt where decision_bucket._ticker(it) == T and the item's
    #         action (when a dict) has no truthy "trim_ticker" (a macro pre-event trim is a
    #         cross-name pick and stays as built, owner Q2).
    # Items are copy.deepcopy'd (the alternate lists are discarded, but the result is memoized).
    # Order: the order the items had in act_alt / review_alt.

def augment(brief, resolved) -> dict | None
    # resolved = the set of upper-cased tickers whose breach the view demoted this render.
    # Returns None when there is nothing to do: `resolved` is empty, `brief` is not a dict,
    #   or ALTERNATES_KEY is absent (a legacy Brief). The view is then byte-identical to today.
    # Otherwise returns {"act": list | None, "review": list | None, "unchecked": [sorted T], "restored": [sorted T]}:
    #   alts = brief[ALTERNATES_KEY]; when it is not a dict, every T in resolved is unchecked.
    #   Per T in sorted(resolved), find the entry by a case-insensitive key match. It is valid
    #   only if it is a dict whose "act" and "review" are both lists of dicts. Invalid / missing → unchecked.
    #   No valid T → "act" / "review" are None (the view keeps its first split and only records unchecked).
    #   Otherwise:
    #     act    = [it for it in brief["act_today"] (or []) if not (it.kind == "stop_breach" and _ticker(it) in valid_T)]
    #              + [{**a, RESTORED_KEY: True} for T in sorted(valid_T) for a in entry[T]["act"]]
    #     review = list(brief["review_list"] or []) + [{**r, RESTORED_KEY: True} ... same for review]
    #     restored = sorted T in valid_T whose entry has at least one act or review item.
    #   Removing the resolved breach items BEFORE the re-split matters: otherwise
    #   decision_bucket._reconcile_act folds a restored critical-news note onto a demoted
    #   breach copy that never renders.
    #   Never mutate `brief` or the lists / dicts in it (new lists, new outer dicts).

def restored_tag(item) -> str | None
    # None unless item.get(RESTORED_KEY) is True. Otherwise:
    # f"↩️ Shown because {T}'s stop breach recovered (live price back above the stop); this call was in today's Brief behind that sell."
    # where T = decision_bucket._ticker(item). Returns None if T is empty.

def resolved_card_line(view, ticker) -> str
    # The last line of a Resolved stop-breach card on Home. In order:
    # 1. act_sell_cover.cover_for(act_sell_cover.view_sell_cover(view), T) is not None
    #    → f"{T} still has its own Act Today call above; follow that, not this resolved stop."
    # 2. T in view.get("recovery_unchecked") (case-insensitive)
    #    → f"{T}'s other checks (earnings week, low score, sell signals) couldn't be rebuilt after the stop recovered; open 📈 Analysis before trading {T}."
    # 3. else today's text, verbatim: "Monitor the position; re-evaluate if price returns to or below the stop."
    # A non-dict view → branch 3.

def summary_resolved_caption(view) -> str | None
    # None when view["resolved"] is empty or missing. Otherwise today's 🧾 Summary caption text verbatim:
    #   f"ℹ️ {n} stop breach(es) resolved — live price recovered above stop: {tks} → see 🏠 Home for detail."
    #   (n = len(resolved), tks = ", ".join(sorted unique non-empty upper tickers)), then, when they apply:
    #   + f" {', '.join(still)} still {'has its' if len(still) == 1 else 'have their'} own Act Today call."
    #     (still = sorted resolved tickers that pass branch 1 above)
    #   + f" {', '.join(unch)}: other checks couldn't be rebuilt after the recovery; open 📈 Analysis before trading."
    #     (unch = sorted resolved tickers in recovery_unchecked and not in still)
```

Text rules for every string the module produces: no `$`, no `**`, and no share count or dollar figure. 🙈 Hide values masks shares, and these strings print in raw HTML and in `st.caption`.

## A-C2 Builder + view

### `stock_analyzer/daily_briefing.py`

1. `_act_today(..., skip_breach=None)`: a new trailing kwarg. In section 1 (stop-loss breaches), skip a row when `skip_breach` is truthy and `str(row["Ticker"]).upper() in skip_breach`. Nothing else changes. With `None`, the output must be byte-identical. Update the docstring.
2. `_review_list(..., macro_exclude=None)`: a new trailing kwarg. At the `_macro_eligible` filter (~:3942), exclude `_act_tickers | {str(t).upper() for t in (macro_exclude or ())}`. This is the only use. With `None`, the output must be byte-identical. Update the docstring.
3. `build_daily_briefing`: just before `_result = {...}` (after the risk-off append, `_rewrite_macro_affected`, Grow Today and the Tune-up, so that `act` is final), compute:
   ```python
   _breach_alts = _breach_alternate_pass(...)
   ```
   and add `"breach_alternates": _breach_alts` to `_result`, so the key is always present on a new build. The new private helper `_breach_alternate_pass(port_df, alert_list, risk_recs, news_items, macro_events, held_data, today, portfolio_value, act, deterioration, premortem_triggers, news_ts, news_label)` does the following:
   - `R = breach_recovery.breach_tickers(act)`. If R is empty, return `{}` and make NO second `_act_today` / `_review_list` call.
   - Inside `try`:
     - `act_alt = _act_today(port_df, alert_list, risk_recs, news_items, macro_events, today, deterioration=deterioration, premortem_triggers=premortem_triggers, news_window_start_ts=news_ts, news_window_label=news_label, skip_breach=R)`;
     - `review_alt = _review_list(port_df, news_items, macro_events, held_data, today, portfolio_value=portfolio_value, act_today=act_alt, deterioration=deterioration, news_window_start_ts=news_ts, news_window_label=news_label, macro_exclude=R)`;
     - `return breach_recovery.alternates_for(R, act_alt, review_alt)`.
   - `except Exception`: `_log.warning("brief breach-alternate pass failed, nothing restorable on a recovery: %s: %s", ...)` and return `None`. The main lists must stay unchanged.
   - Add a comment on why this pass exists: cross-name picks (macro pre-event trim, risk-off, macro affected list, Grow Today, Tune-up) stay as built (owner Q2), and the raw lists are never touched.
   - Confirm from the code that `_act_today` / `_review_list` don't mutate `port_df`, `held_data`, `deterioration`, `news_items`, `premortem_triggers` or `risk_recs`. (`_review_list` DOES mutate its `act_today` argument's sell_signal dict for `min_trim`; act_alt is a fresh list of fresh dicts, so that is fine. Say so in the comment.) If you find a mutation of a shared input, stop and report it rather than work around it.

### `stock_analyzer/act_today_view.py`

- Move the demotion loop into a local helper (`_demote(items) -> (active, resolved)`) without changing its behaviour.
- After the first split and demotion:
  - `RT = {upper ticker of each resolved item}`;
  - `aug = breach_recovery.augment(daily_brief, RT)`;
  - when `aug` is not None and `aug["act"]` is not None: `split2 = decision_bucket.split_defensive(aug["act"], aug["review"])`; `active, _ = _demote(split2["act"])` (the remaining, non-recovered breaches get the same state as before); `aware = split2["aware"]`; keep `resolved` from the FIRST pass.
- `recovery_unchecked = aug["unchecked"] if aug else []`.
- The macro refresh (`refresh_macro_card`) then runs on the final active / aware exactly as today.
- Return dict: add `"recovery_unchecked": [...]` and `"restored": aug["restored"] if aug else []` to BOTH the offline return (`[]`) and the normal return. Document both keys in the docstring's Returns block.
- `n_active = len(active)` as today. It can rise on a recovery day, and that is intended (Summary and the dock read the same view).

### `app.py`

- `_SYNTH_SCHEMA_VER` 30 → 31. Prefix the comment with "bumped: the Brief result carries breach_alternates and act_today_view restores a recovered breach's suppressed per-name cards (BriefBreachRestore); was 30: ..." and keep the rest of the chain.

## A-C3 app.py render (ships with C2)

- `_render_defensive_card` (~:13593): before dispatching, `_rt = breach_recovery.restored_tag(_item)`; when it is not None, show `st.caption(_rt)`. Do this for both the Act and Monitoring lanes. Confirm that the Monitoring / Awareness loop (~:13784) renders through `_render_defensive_card`; if it doesn't, add the same two lines there.
- Resolved card (~:13715): replace the hardcoded `"Monitor the position; re-evaluate if price returns to or below the stop."` with `{_sh(breach_recovery.resolved_card_line(_act_view, _rb_tk))}`. `_act_view` is the Home view computed at ~:9478; check that it is in scope at that point.
- 🧾 Summary (~:15734): replace the inline caption build with `_sm_cap = breach_recovery.summary_resolved_caption(_sm_act_view)` and `st.caption(_sm_cap)` when it is not None. The caption text for a plain resolved day must not change.
- Put the import with the other `stock_analyzer` imports at the top of app.py (module def order: app.py runs top to bottom).
- No other app.py changes. Do not touch `_reduce_calls` (~:3036; a named sibling, out of scope).

## Tests (new `tests/test_breach_recovery.py`; extend existing act_today_view / daily_briefing tests where they live)

Build fixtures the way existing daily_briefing tests do (grep `tests/` for `build_daily_briefing(` and `_act_today(` helpers and reuse them).

1. **Alternate equivalence.** Fixture: held T with gap ≤ 0 AND a deterioration EXIT (or TRIM) AND earnings within `EARNINGS_IMMINENT_DAYS` AND (separate case) Signal "Sell" + weak-large weight; NO macro events. `alternates[T]` must equal T's non-excluded items from a build where T's gap is set well above `APPROACHING_STOP_GAP_PCT` (no breach, no approaching-stop card). Compare by the fields that matter (kind / action type, directive, why, trim_shares, min_trim), or by whole dicts minus `reason` if the dicts are otherwise equal.
2. Main `act_today` / `review_list` are identical with the pass on vs monkeypatched to return `{}`. Non-R subjects in the alternates are absent.
3. **Main-list invariant:** no item in the main review list has `decision_bucket._ticker(it)` in R.
4. Exactly ONE `_act_today` call and ONE `_review_list` call on a no-breach day (monkeypatch counters); `breach_alternates == {}`.
5. The pass raises (monkeypatch `_act_today` to raise on the `skip_breach` call) → `breach_alternates is None`, the main lists are unchanged, and a warning is logged.
6. `skip_breach=None` / `macro_exclude=None` give byte-identical results. With `macro_exclude={T}` the macro pre-event trim never picks T (fixture: a sector-concentrated HIGH macro event 1–3 days out where T is the natural pick).
7. **View boundaries:** live gap in {−1, 0, `STOP_RECOVERY_MARGIN_PCT` − 0.01, `STOP_RECOVERY_MARGIN_PCT`, `STOP_RECOVERY_MARGIN_PCT` + 0.01, None}. Restore happens only where `stop_recovery_state` says "recovered" (read that function for the exact boundary; don't assume). Otherwise the view is identical to the pre-change view and the breach stays active.
8. **Re-breach:** the same Brief object is viewed recovered → breached → recovered: restore / no restore / restore. The Brief is deep-equal to its pre-view copy after every call (no mutation).
9. **Critical news on a recovered T:** the restored critical_news card appears in the lane `BUCKET_CRITICAL_NEWS_IS_ACT` decides, its why has no "Also flagged on recent negative headlines" fold, and no restored item lands in `resolved`.
10. **Legacy Brief** (no key) → the view equals the old behaviour (`active` without restored items, `recovery_unchecked == []`, `restored == []`). Key None → nothing restored and `recovery_unchecked == [T]`. A malformed entry (T missing, "act" not a list) → T unchecked, other valid tickers still restored.
11. **DeferSizeBlind end to end:** a recovered T with a restored `deterioration_trim` → `act_sell_cover.view_sell_cover(view)[T]["shares"] == trim_shares`; with no recovery, the breach is an exit cover; a recovered T with nothing restored → T is absent.
12. `resolved_card_line`: all three branches, plus a non-dict view. `summary_resolved_caption`: None / plain (text equal to today's) / still-sold / unchecked, singular and plural. `restored_tag`: True / missing / False / empty ticker.
13. **Text rules:** no `$`, no `**` and no digits from a share count in any breach_recovery string (parametrize over tickers).
14. **Pins:** `_SYNTH_SCHEMA_VER = 31` in app.py. `"breach_alternates"` / `ALTERNATES_KEY` appear only in `stock_analyzer/daily_briefing.py`, `stock_analyzer/breach_recovery.py` and tests: app.py and cron_runner.py never read the key (nothing new is persisted, and no raw reader sees restored items). `stock_analyzer/breach_recovery.py` is in `_GATE_FILES` (assert on `_GATE_FILES` only; never read docs/review-policy.md in a test, since it isn't a suite input).
15. **Time context:** a weekend / holiday visit where the live price equals the build price (gap ≤ 0 unchanged) → no recovery and no restore.

Existing tests that pin the old Summary caption line, the Resolved card's "Monitor the position" string, or `_SYNTH_SCHEMA_VER = 30` will need updating. Grep `tests/` for each old string after editing (memory: a late text edit has broken a pinned test before).

## Run

- `python -m pytest tests/test_breach_recovery.py tests/test_act_today_view.py <the daily_briefing test files you touched> -q -p no:cacheprovider` and every test file that greps app.py for the strings you changed. Do NOT run the full suite (the commit hook does, and two at once exhaust memory).
- `python scripts/check_antipatterns.py` and `python scripts/check_constants_documented.py`.
- `python -m py_compile app.py`.
- Don't commit. Report: the files changed, the test counts, any shared-input mutation you found, and anything in this spec that didn't match the code.
