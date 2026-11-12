# Pending Imports verdicts (TJDate U9)

**Status:** U9a + U9a′ BUILT 2026-11-12 (pure `pending_import_verdict.py`, joins `_GATE_FILES`; strict `db.load_snaptrade_pending_imports_or_none`; not wired into app.py; Opus FIX-FIRST (1 blocking: the twin rule could flip a LINKED_DIFFERS row, whose own txn is already on a journal row, to "not in your journal" with Log enabled) → SHIP, 0 blocking). Fixed in that round as well: a twin listed in this render's pending list counts as pending even when the fills read says dismissed; a flipped row keeps the matcher's text after `TWIN_TEXT`. NEXT: U9b (wiring, reviewer + screenshots), then U9c docs. Earlier: DESIGNED 2026-11-12 (Opus planner `claude-opus-5-5`: PROCEED WITH CHANGES, at HEAD `6058b8b`); owner decisions UD-1–UD-5 taken 2026-11-12 (all as recommended).

**Carried into U9b from the U9a review:** (1) the app.py helper wraps the load + `decide_pending_imports` call and renders the offline banner on any exception (only the matcher call is wrapped inside the module); (2) the U9b commit names, next to the UD-5 note, that a dismissed unlogged twin of a linked row (LINKED_DIFFERS or already linked) is not shown on Pending Imports and is visible only through `scripts/journal_broker_match_dry_run.py` until U8; (3) a seeded twin generator for the property test stays deferred (the twin rule rests on named fixtures).

Parent plans: [trade-entry-guard.md](trade-entry-guard.md) (U9, ~line 78), [journal-broker-match-c5.md](journal-broker-match-c5.md) (C5 matcher, owner D1–D6; U9 notes ~lines 157-169).

## Why

📥 Pending Imports (💰 Account → Broker Sync, app.py ~41560-41703) tells the user "use Already logged" from a ticker-level share-count match and a weak third matcher (`broker_sync.find_pending_match_candidates`). Net share counts can match while two fills are wrong (GOOGL 10-30: SELL 5 vs 6 plus an unlogged BUY 1), and that advice led to 172 dismissals. The C5 matcher (`journal_broker_match.py`) now gives a per-fill verdict; U9 shows it and gates the two buttons on it.

## Planner changes to the briefed shape

- (a) "Keep Already logged only for AMBIGUOUS / TOO_MANY / UNCHECKED / NOT_YET" means keep the *unconditional* advice only there, not hide the button elsewhere: LINK / RECONCILED / REDATE rows can only leave the queue through "Already logged" until Phase 2's one-click exists.
- (b) Identical-twin hole: a pending fill the matcher calls LINK can be the logged copy of two identical fills whose other copy was dismissed and is unlogged. The twin rule below turns it into "not in your journal".
- (c) The pending list read is lenient (`db.load_snaptrade_pending_imports` returns `[]` on any exception, db.py ~6640), so an outage renders "✓ No pending imports". U9 switches the section to a strict loader.

## Owner decisions 2026-11-12

- **UD-1 (yes):** "Log This Trade" needs a tick when the matcher names a journal row that could already be this fill: UNLOGGED with `nearby_journal_ids`, AMBIGUOUS / TOO_MANY members, a NOT_YET_COMPARED row that absorbed the fill. D0 precedent: one prominent panel + one tick.
- **UD-2 (tick, not disabled):** "Already logged" on UNLOGGED needs a tick. A disabled button would strand matcher blind spots (BRK.B / BRK-B aliases, raw-SQL rebaseline rows, a split in the window).
- **UD-3 (disabled):** on kinds where the fill is already in the journal (LINK, RECONCILED, REDATE, SPLIT, FORWARD, PRICE_FIX, MERGED_FILLS, QTY_MISMATCH, LINKED_DIFFERS, already linked) "Log This Trade" is DISABLED with a visible reason. Escape hatch: a hand log in 📒 Trade Journal, still guarded by D0.
- **UD-4 (retire all three):** F-244a(1) "counts match… use Already logged", F-244c "App X sh · Robinhood Y sh", F-244a(2) "Possible match" hint.
- **UD-5 (U8):** non-pending fills (dismissed-but-unlogged, JOURNAL_ONLY, LINKED_DIFFERS) are listed by U8, not U9. Until U8 ships, a previously dismissed unlogged fill is visible only via `scripts/journal_broker_match_dry_run.py`; say so in the U9b commit.
- No new constant. The window wording interpolates `JOURNAL_MATCH_LOOKBACK_DAYS`; everything else reuses the matcher's `JOURNAL_MATCH_*`.

## Chunks

### U9a: pure decision module (implementer → reviewer)

Files: new `stock_analyzer/pending_import_verdict.py`; new `tests/test_pending_import_verdict.py`; `.claude/hooks/pre_tool_checks.py` (`_GATE_FILES` += the module); `docs/review-policy.md` (dated reason line + the Review REQUIRED list); CLAUDE.md `_GATE_FILES` count 86 → 87.

Imports: `journal_broker_match` (as `_jbm`) for `match_journal_to_broker`, `match_coverage`, `KIND_*` and the privates `_session_on_or_after`, `_sync_et`, `_sync_when`; `trade_entry_checks` (as `_tec`) for `_txn_text`, `_as_date`, `_num`, `_raw_price`, `_same_shares`, `_same_price`, `_fmt_date`, `_fmt_shares`, `_fmt_price`, `_aware_et`; `JOURNAL_MATCH_LOOKBACK_DAYS` from constants. No streamlit, db or data import; no clock read. (Verify each name exists at HEAD before use; if one doesn't, stop and report rather than inventing a substitute.)

API:

```python
def decide_pending_imports(pending, journal, fills, synced_at, now_et) -> dict
def log_allowed(row: dict, ticked: bool) -> bool      # log_enabled and (not log_needs_tick or ticked)
def dismiss_allowed(row: dict, ticked: bool) -> bool  # same with the dismiss fields
```

Inputs: `pending` = strict pending list or None; `journal` = `db.load_trades_or_none()`; `fills` = `db.load_snaptrade_fills_or_none(<matcher's fills-read start>)`; `synced_at` = `snaptrade_config.last_txn_sync_at` read; `now_et` aware (naive → ValueError via `_aware_et`).

Return: `{"list_state": "offline"|"empty"|"rows", "list_text", "compare_state": "ok"|"offline"|"skipped", "compare_text", "coverage_text", "summary_text", "rows": [...]}`.

Each row: `pending_id`, `txn_id`, `headline` (e.g. "BUY 3 CSCO at 102.00, placed Wed Nov 4"; no `$`), `verdict`, `kind` (matcher kind or None), `reason`, `tone` ("ok"|"warning"|"neutral"), `label`, `text`, `journal_ids`, `nearby_journal_ids`, `log_enabled`, `log_primary`, `log_needs_tick`, `log_tick_label`, `log_disabled_reason`, `dismiss_primary`, `dismiss_needs_tick`, `dismiss_tick_label`, `dismiss_note`.

Short-circuits:
- `pending is None` → list_state offline, "⚠️ Pending imports couldn't be read this render." Never "No pending imports".
- `pending == []` → empty; the matcher is NOT run. Text: "✓ No pending imports as of the last completed transaction sync ({`_sync_when`})", or the matcher's unknown wording when `synced_at` is None.

Compare:
- journal None, fills None, or the matcher returns None → compare offline; the banner names which part failed ("your journal" / "Robinhood's records" / "the comparison").
- The matcher call is wrapped in try/except Exception → offline "the comparison couldn't run". An exception never becomes a verdict.
- `coverage_text` = `_jbm.match_coverage(synced_at)["text"]` whenever compare_state is ok.

Row lookup: index findings by `_txn_text(txn)`, falling back to `pending_ids` equality via `_num`. If a txn appears in more than one finding (broken invariant), take the most restrictive mapping (Log disabled if any finding disables it; a tick if any asks for one). An unknown kind maps like AMBIGUOUS / TOO_MANY with both ticks required.

Verdict table:

| Verdict (tone) | Kinds | Log This Trade | Already logged | Text |
|---|---|---|---|---|
| IN_JOURNAL (ok) | LINK, RECONCILED, or no finding with the txn already on a journal row (txn→id map from the same `journal`) | disabled; reason "this fill is already in your journal (#N), so logging it would count it twice" | primary, no tick | LINK / RECONCILED: the matcher's text. Already linked: "Journal row #N is already linked to this Robinhood record." |
| DIFFERS (warning) | REDATE, SPLIT, FORWARD, PRICE_FIX, MERGED_FILLS, QTY_MISMATCH, LINKED_DIFFERS | disabled, same reason; MERGED_FILLS adds "If these were separate trades, #N needs correcting first." | primary, no tick; dismiss_note "clears this row only; it doesn't change your journal" | the matcher's text |
| NOT_IN_JOURNAL (warning) | UNLOGGED | primary; tick only when `nearby_journal_ids` (UD-1): "I've checked #N: this fill is a separate trade" | tick (UD-2): "I've checked: this fill is already in my journal under a row the comparison didn't match" | the matcher's text, incl. its check-before-logging sentences |
| NOT_IN_JOURNAL, twin (warning) | twin rule | as UNLOGGED | as UNLOGGED | "Robinhood has another identical fill (same day, shares and price) that no journal row matches, so one of the two isn't in your journal: logging this one covers it." |
| UNCLEAR (neutral) | AMBIGUOUS, TOO_MANY | not primary; tick naming the component's `journal_ids` (UD-1) | no tick | the matcher's text |
| NOT_YET (neutral) | NOT_YET_COMPARED that absorbed the fill | not primary; tick naming the row | no tick | the matcher's text + " It may be this fill: check it before logging this one." |
| UNCHECKED (neutral) | UNCHECKED | plain | plain | the matcher's text + " Check it in Robinhood and 📋 History before using either button." |
| OPTION (neutral) | SKIPPED_NON_EQUITY | disabled ("the journal tracks stocks and ETFs") | primary | — |
| OUTSIDE_WINDOW (neutral) | no finding, not linked, fill session < today − `JOURNAL_MATCH_LOOKBACK_DAYS` | plain | plain | "older than the {N}-day comparison window …" |
| NOT_COMPARED (neutral) | no finding, in window (read race), or an unreadable `trade_date` | plain | plain | — |
| compare offline | every row | plain, no ticks | plain, no ticks | label "❔ Not compared"; no verdict wording |

Twin rule (decision-bearing; the 2026-08-30 DCA-twin class): applies to a pending fill whose verdict is IN_JOURNAL or DIFFERS when an UNLOGGED finding exists on an interchangeable fill (same ticker, action and session; `_same_shares`, `_same_price` on per-fill values from `fills`) whose `fill_statuses[0] != "pending"`. Such a row flips to NOT_IN_JOURNAL (twin). Flip at most as many pending rows as there are non-pending unlogged twins, lowest txn text first. Never flip when the twin is itself pending (both rows are already visible; flipping would invite two logs).

Texts are plain: no `$`, no `**`, no line starting with `#`.

### U9a′: strict pending-list loader (implementer → reviewer; db.py is a gate file)

- `db.load_snaptrade_pending_imports_or_none()`: `status = 'pending'`, no date bound, the same select columns as `load_snaptrade_pending_imports`, order `trade_date` desc then `id` desc, `_read_all_pages(..., _PENDING_FILLS_PAGE_SIZE, dedupe_key="id")`; None on no DB or any page failure.
- Tests in `tests/test_broker_txn_sync_stamp.py`, mirroring the existing loader tests: status filter, no date filter, paging, `[]` vs None, any page failure → None.
- Update the `dismiss_snaptrade_pending_import` docstring (db.py ~6821): it says a mistaken dismiss "surfaces later via Position Drift"; name the U9 tick and that C5 compares dismissed fills (D3).
- The lenient `load_snaptrade_pending_imports` stays for `data_maintenance`.

### U9b: wiring and retirement (implementer → reviewer + screenshots)

- Files: app.py ~41560-41703 and the User Guide paragraph (~45458); `stock_analyzer/broker_sync.py`; `tests/test_broker_sync.py`.
- Load the four inputs only when the strict pending list is non-empty, in one small app.py helper U8 can reuse; call `decide_pending_imports` and render only: `list_text`, `compare_text`, `summary_text`, `coverage_text` (once per section); per row the headline, label and text via `st.warning` (tone warning) or `st.caption`, and a tick checkbox `_snap_logack_{pending_id}` / `_snap_dismack_{pending_id}` before the buttons.
- "Log This Trade →" `disabled=not log_allowed(row, ticked)`, `type="primary"` only when `log_primary`, `log_disabled_reason` as a caption when disabled; "Already logged" likewise. Click handlers re-check `*_allowed` before acting.
- Unchanged: the prefill dict, `_pending_page`, the read-only "🔒 view-only" caption.
- "Already logged" help → "Clears this row from Pending Imports. Never logs or edits a trade." (drop the "1-cent settlement difference" invitation). Rewrite the section caption to describe the verdicts.
- Delete the call sites app.py ~41573-41615 and their captions; delete `find_pending_match_candidates` (broker_sync.py ~729), `annotate_pending_reconciliation` (~667), module-docstring lines ~18-24 and their tests (test_broker_sync.py ~447-600, ~1527-1620); delete `ticker_share_counts` (~250) only if a build-time grep shows no other caller.
- No `fn() or []`, no `unsafe_allow_html`, `market_time.now_et()` read once.
- U9 publishes no session key → no `coord_freshness` change. U8 owns any cache and its registration.
- Name in the commit (or relabel, display): the banner at app.py ~41436 shows `last_full_sync_at` in UTC while the coverage line shows `last_txn_sync_at` in ET.

### U9c: docs (lead)

requirements.md new F-244d "Pending Imports verdicts (TJDate U9)", F-244a / F-244c marked superseded in place, F-244 (3) cross-reference; comment-only edit of the `JOURNAL_MATCH_WINDOW_SESSIONS` comment (constants.py ~2164, "(Pending Imports hint)" → the matcher) and architecture.md ~645 / ~2999; Status lines here, in trade-entry-guard.md and journal-broker-match-c5.md; bug register TJDate row; CLAUDE.md queue item 3; shipped-log.md.

## Tests the build must include

- **Mapping coverage:** every key of `_jbm._SEVERITY` maps to an explicit verdict (a new matcher kind fails the test); an unknown kind gets both ticks.
- **Invariant (property test, the matcher's seeded generator):** Log is never enabled for a pending fill in LINK / RECONCILED / REDATE / SPLIT / FORWARD / PRICE_FIX / MERGED_FILLS / QTY_MISMATCH / LINKED_DIFFERS or already linked, unless the twin rule fires; dismiss needs a tick exactly when the verdict is NOT_IN_JOURNAL.
- **Fixtures:** MRVL 10-30 both pending → IN_JOURNAL; GOOGL 10-30 BUY 1 @ 280 → NOT_IN_JOURNAL, `dismiss_allowed(row, False)` False, SELL 6 → DIFFERS (QTY), Log disabled; D0 gap (8 @ 196.99 combined + a re-log of the 5): 5 → IN_JOURNAL, 3 → Log tick naming #8; CSCO 13903 → IN_JOURNAL; OKTA → DIFFERS PRICE_FIX; MRVL #419 → MERGED_FILLS, Log disabled; AMBIGUOUS → Log tick, dismiss plain; NOT_YET absorbed → Log tick; UNCHECKED and option rows; already linked and still pending → IN_JOURNAL "#N".
- **Twin rule:** a dismissed UNLOGGED twin flips one pending LINK row; two pending twins + one dismissed twin → exactly one flip; a pending twin → no flip.
- **Window boundary:** session == today − LOOKBACK → compared; one day earlier with no kept finding → OUTSIDE_WINDOW.
- **Three-state:** pending None → list offline; `[]` → empty with the matcher monkeypatched to assert not called, text names the sync (or the unknown wording when `synced_at` is None); journal None / fills None / matcher returns None / matcher raises → compare offline, rows plain, no verdict text; a list row missing from the fills read → NOT_COMPARED.
- **Time-context matrix:** Saturday 11-14 and Thanksgiving 11-26 visits; DST end 2026-11-01 with a 10-30 fill; 07:00 vs 20:30 ET same date → identical output; a 6-day-stale `synced_at` leaves UNLOGGED unchanged, a recent nearby hand log → NOT_YET with a Log tick; late hand log BUY and SELL → REDATE, Log disabled; back-dated BUY and SELL → FORWARD, Log disabled; naive `now_et` → ValueError.
- **Strings and purity:** every label / text / reason / tick label non-empty, no `$`, no `**`, not starting with `#`; AST purity pin (no streamlit / db / data import); reuse pins (`pv._jbm._session_on_or_after is jbm._session_on_or_after`, `_tec._same_price`, …).

## Risks

- **Duplicate log (HoldDup):** disabled Log on IN_JOURNAL / DIFFERS, the Log tick on nearby / ambiguous rows, the twin rule; `trade_confirm`'s already-linked hard block stays as a second defence.
- **One freshness:** verdicts come only from this render's strict journal read + strict fills read; the pending list only picks which rows to show and supplies the prefill. `st.session_state.trades_df`, `holdings_df` and live positions are no longer read by this section. A row in the list but missing from the fills read is NOT_COMPARED, never guessed.
- **Calm posture:** nothing goes to Act Today. Dismissing a DIFFERS row doesn't hide the difference (C5 compares dismissed fills, D3). Don't claim Home shows it until U8 ships.
- **Wording overclaims:** don't say the next sync will link a LINK row; don't instruct delete-and-re-log (a Trade History Delete is invisible to N1; corrections are Phase 2).
- **Performance:** the matcher runs only when something is pending; memoize in U8, not here.

## Sibling sites

- Fixed in U9: app.py ~41636, ~41654, ~41671 (advice), ~41694-41699 (button help), ~45458 (User Guide); broker_sync.py ~24, ~667, ~729, ~746, ~807; db.py ~6821 docstring; app.py ~41569 (lenient list read).
- Named, kept: `trade_confirm.LINE_HARD_NEXT` (trade_confirm.py:56; correct: an already-linked txn should be dismissed); journal_broker_match.py:766 (status sentence, shown only for non-pending fills); app.py ~27546; data_maintenance.py ~438 (lenient read, already disclosed there); broker_sync.py ~562 Tier-2 key uses the placement date (broker-fill-record plan).

## Live screenshot checks (after U9b)

- Each currently pending row reads its verdict; CSCO 13903, if still pending, reads "Already in your journal" with Log disabled and the reason shown.
- The coverage line under the section; the empty queue names the last completed transaction sync in ET.
- On the next real unlogged fill: Log primary, Already logged greyed until the tick.
- Read-only viewer still sees "🔒 view-only". No literal `**`, no `$` as LaTeX. User Guide paragraph updated.
- Rare: the offline banner.
