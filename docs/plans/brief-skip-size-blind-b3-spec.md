# BriefSkipSizeBlind Batch 2, B-C3 build spec (beta / Sharpe Tune-up cards)

**Status:** SPEC 2026-11-26 (lead, from `docs/plans/brief-breach-restore-and-fold.md` §3 "Beta / sharpe Tune-up", owner Q8 + Q9 defaults). Build = implementer; own Opus review; may ship alone. B-C1 + B-C2 (the fold) shipped just before this.

**The bug** (`daily_briefing._portfolio_tuneup` ~:427-466). The 🔧 Tune-up beta card ("Sell 50% of X: portfolio beta drops …") and the Sharpe card are filtered at BUILD time against `acted_tickers` = `decision_bucket.all_flagged_tickers(act, review, ignore_news=True)`, i.e. any non-news card on the name:
- the beta card is dropped WHOLE when `root_tickers[0]` has any card, even a Watch (Q8) or a tiny Act sell;
- the Sharpe card loses chips for any flagged name and is dropped when none remain;
- both drops are silent;
- the beta card's real subject is the name its text sells (`beta_levers.trim.ticker`, which skips names bought today), not `root_tickers[0]` (Q9);
- the drop is not size-aware, so a 1-share Act sell hides "Sell 50% of X" (the DeferSizeBlind class);
- the decision is made at build, so a stop breach that later recovers keeps the card hidden.

**The fix:** stop filtering beta / Sharpe at build. Carry the beta card's subject and whole-share size. Settle both card types on Home against the live Act Today VIEW's sell cover (`act_sell_cover.view_sell_cover(_act_view)`), with a visible 🔧 line whenever one is dropped. This is the same pattern as `held_trim_owner.reconcile_tuneup` for the single-name card.

No constant changes value; no new constant. The `_tf = 0.50` literal in risk_advisor (~:259) is a named sibling row and is NOT changed here.

## 1. `stock_analyzer/risk_advisor.py`, the beta rec

- Add `"trim_shares"` to the beta rec dict (~:329): `int(_trim_dollar / _px)` where `_px = tr_map[trim_ticker]["price"]`, when `trim_row is not None` and `_px` is a finite number > 0; else None. Use whatever key `tr_map` already exposes for price (`"price"`, ~:178).
  - This is the 50% trim the card's text names, in whole shares: floor, never round up.
  - 0 is possible on a sub-share position, and means "sizes no trade". `act_sell_cover.valid_need` accepts 0.
- Add the same `"trim_shares"` key to `beta_levers["trim"]` (or document that it lives top-level only). Pick one: top-level `rec["trim_shares"]` is the one `_portfolio_tuneup` reads.
- `ok_beta` / no-candidate recs: `trim_shares` None.
- Nothing else in the rec changes; the recommendation text is byte-identical.

## 2. `stock_analyzer/daily_briefing.py`, `_portfolio_tuneup` and the build

- Remove the build-time trim-driver filter: the `if acted and rtype in _TUNEUP_TRIM_DRIVER_TYPES and tickers:` block. Remove the `acted_tickers` parameter, the `_TUNEUP_PRIMARY_ANCHORED_TYPES` constant (if it becomes unused), and the `_acted_final` computation plus argument in `build_daily_briefing` (~:4597-4606).
- Keep `_TUNEUP_TRIM_DRIVER_TYPES` only if something still reads it; move its comment's reasoning to the new module. Update the docstring.
- Beta card annotations (Q9):
  - `card["subject"]` = upper of `rec["beta_levers"]["trim"]["ticker"]` when that dict exists with a non-empty ticker, else None;
  - `card["trim_shares"]` = `rec.get("trim_shares")`.
- Sharpe card: no subject. `tickers` (the chips) = every root ticker, unfiltered.
- Single-name cards and their EW C3 / DeferSizeBlind path: unchanged.
- `_SYNTH_SCHEMA_VER` 32 → 33. Prefix the comment with "bumped: the Tune-up beta card carries subject / trim_shares and beta / Sharpe cards are settled on Home against the Act Today view, not filtered at build (BriefSkipSizeBlind B-C3); was 32: ...".

## 3. New gate module `stock_analyzer/tuneup_cover.py`

Pure: no streamlit, no I/O, inputs never mutated. Add it to `_GATE_FILES`, with a one-line reason in `docs/review-policy.md`. Suggested reason: "settles the 🔧 Tune-up beta / Sharpe trim cards against the live Act Today sell sizes; a wrong call hides a beta trim behind a smaller sell or shows a second sale beside a covering one". Module docstring with THE SHAPE + THE FAIL DIRECTIONS.

```python
def reconcile_trim_drivers(cards, view_cover) -> dict:
    """{"cards": [...], "notes": [str, ...]}"""
```

- `view_cover` = `act_sell_cover.view_sell_cover(view)` (a map, or None).
  - **None** (view offline) → every card returned unchanged, `notes == []` (fail toward showing).
- Cards whose `type` is not `beta` / `sharpe` pass through unchanged, in place.
- **Exit-covered set** E = tickers whose cover entry has `exit is True`.
- **beta card:**
  1. `chips` = `card["tickers"]` minus E (case-insensitive; keep the original order and spelling).
  2. When `subject` is a non-empty str: `state = act_sell_cover.cover_state(act_sell_cover.cover_for(view_cover, subject), card.get("trim_shares"))`.
     - `covered` → drop the card; note `f"🔧 {S} already has a sell in today's Act Today call at least as large as the beta card's trim; the beta card returns if portfolio beta is still high after that sale."`
     - `partial` / `unknown` → keep a COPY with `tickers = chips` and `act_cover_note = act_sell_cover.cover_note(state, S, "beta trim")`.
     - `show` → keep a copy with `tickers = chips`.
  3. With no subject: keep a copy with `tickers = chips`.
- **sharpe card:**
  - `chips` = `tickers` minus E. When the original `tickers` was non-empty and `chips` is empty → drop; note `f"🔧 The Sharpe card's drag names ({', '.join(sorted names)}) all have an exit in today's Act Today call; it returns if they are still held after that."`
  - Otherwise keep a copy with `tickers = chips`. A non-exit (trim) sell does not remove a Sharpe chip: the Sharpe card says "trim 40–50%" under conditions, so it is not a sized restatement.
- A `trim_shares` that is invalid (bool, NaN, "2", negative, non-integral) reads as need None, which is unknown under a non-exit cover (`act_sell_cover.valid_need` semantics). Never raise on a malformed card: a non-dict card passes through.
- Text: no `$`, `**` or share count (🙈 Hide values). `cover_note` already obeys this.

## 4. `app.py`, Home (~:10083)

- After `held_trim_owner.reconcile_tuneup(...)`:
  ```python
  _db_tuneup_td = tuneup_cover.reconcile_trim_drivers(_db_tuneup, act_sell_cover.view_sell_cover(_act_view))
  _db_tuneup = _db_tuneup_td["cards"]
  _db_tuneup_driver_notes = _db_tuneup_td["notes"]
  ```
  Compute `view_sell_cover` once and reuse it.
- Render each note as `st.caption(note)`, right after the existing `🔧` deferred-text captions (~:13872), so a dropped card is never silent. The notes already start with "🔧"; don't prefix again. They are plain text, markdown-safe (no `$`).
- The card render already shows `act_cover_note` (~:13861). No other change.
- Import `tuneup_cover` with the other `stock_analyzer` imports at the top.

## 5. Siblings to fix in this chunk

`stock_analyzer/held_trim_owner.py:48` comment "Mirrors daily_briefing._review_list's `_reduce_tickers` plus risk_off_derisk": `_reduce_tickers` no longer exists. Reword it to "the Act reduce kinds whose ticker is the name sold (`decision_bucket.EXIT_KINDS` + deterioration_trim + risk_off_derisk)". It is a gate file, so the comment ships under this chunk's review. No code change there.

## Tests

New `tests/test_tuneup_cover.py`; update `tests/test_daily_briefing.py` (~:1730-1960 pin the old build-time filter; rewrite those as "no build-time drop" plus the new annotations); risk_advisor tests for `trim_shares`.

1. **risk_advisor:** `trim_shares` = floor(50% of market value / price): a normal case, a sub-share (0), price 0 / NaN / missing → None, no trim candidate → None, a bought-today skip (the subject is the next contributor and `trim_shares` is that name's). The recommendation text is unchanged.
2. **`_portfolio_tuneup`:**
   - beta / Sharpe cards are never dropped or chip-filtered at build, whatever the Act / Review lists hold (incl. a Watch-only card on the subject, Q8);
   - `subject` = `beta_levers.trim.ticker`, not `root_tickers[0]`, when they differ (Q9);
   - single-name behaviour unchanged (existing tests stay green).
3. **`reconcile_trim_drivers`:**
   - view None → identity;
   - beta covered / partial / unknown / show;
   - covered by an exit vs by a large enough trim;
   - `trim_shares` 0 → covered by any sell (need 0);
   - `trim_shares` None → unknown under a non-exit sell, covered under an exit;
   - `risk_off_derisk` (unknown size) → kept with the unknown note;
   - chips: exit-covered names removed, trim-sold names kept;
   - Sharpe: all chips exit-covered → dropped with note, some → kept with fewer chips, a trim-only sell → unchanged;
   - non-beta / Sharpe cards untouched, in order;
   - inputs not mutated; malformed cards pass through;
   - text rules (no `$`, `**`, digits from a share count).
4. **End to end through `build_daily_briefing` + `act_today_view`:**
   - a deterioration_trim of N on X beside a beta card "Sell 50% of X" (K > N shares) → the beta card stays with the partial note;
   - with K ≤ N → dropped with the 🔧 note;
   - a stop breach on X that recovered (view demotes it) → the beta card is back (a restored Batch-1 card may cover it: assert against the view's cover, whichever applies).
5. **Pins:**
   - `_SYNTH_SCHEMA_VER = 33`;
   - `stock_analyzer/tuneup_cover.py` in `_GATE_FILES` (assert on `_GATE_FILES` only);
   - app.py calls `tuneup_cover.reconcile_trim_drivers` exactly once (AST / grep), and renders the notes.

## Run

- Targeted tests only:
  - `tests/test_tuneup_cover.py`, `tests/test_daily_briefing.py`, `tests/test_act_sell_cover.py`;
  - the risk_advisor test file(s) (grep `tests/` for `build_risk_advisor_recommendations`);
  - `tests/test_breach_recovery.py`, `tests/test_act_trim_fold.py`;
  - every file pinning `_SYNTH_SCHEMA_VER`.
  
  Use `-q -p no:cacheprovider`. NOT the full suite. Afterwards, grep `tests/` for any old string you changed.
- Also run `python scripts/check_antipatterns.py`, `python scripts/check_constants_documented.py` and `python -m py_compile app.py`.
- `git add` the new module and the new test file. Don't commit.
- Report: files changed, test counts (actually run them, not just collect), and any spec-vs-code mismatch. STOP and report rather than improvise if the code contradicts the spec.
