# TJDate C5: journal vs Robinhood matcher (build spec)

**Status:** U9 (Pending Imports shows these verdicts) BUILT 2026-11-12, F-244d, `docs/plans/pending-imports-verdicts-u9.md`; the Risks note on `find_pending_match_candidates` below is closed (retired in U9b). U8 DESIGNED 2026-11-12 (`docs/plans/journal-vs-robinhood-u8.md`; note the 35-day window ages a finding out unfixed, so U8 shows each finding's "leaves the comparison on" date). U8a BUILT 2026-11-12 (pure `stock_analyzer/journal_match_view.py`, `_GATE_FILES` 88, plus the matcher's additive `differs_on` field; Opus FIX-FIRST → SHIP; nothing on screen yet); NEXT: U8b. Earlier: SPEC 2026-11-10 (Opus planner `claude-opus-5-5`, PROCEED WITH CHANGES, read at HEAD `5b90d09`). Owner decisions 2026-11-10: **D1** lag rule as recommended (a row with no Robinhood record is judged only once the last completed transaction sync is on a later ET date AND at least `JOURNAL_MATCH_BROKER_LAG_SESSIONS` = 2 trading sessions after it); **D3** dismissed fills ARE compared (fills of every status are broker facts); **D6** the read-only dry-run script is built with C5. **D2** (Phase 2, stated to the owner, no C5 code): a REDATE / SPLIT-back one-click must make the owner confirm Robinhood's "Filled" date, plus a Phase-2 "journal is right, link as is" kind; C5 only sets `requires_fill_date_check`. **D4:** count the Phase-2 gate with the dry-run script now, a broker-lane log line with U8 later. **D5:** rank order as below. Owner answer 2026-11-10: **no overnight-session (20:00–04:00 ET) or weekend orders.** So C5's weekend / holiday → next-session shift is only a safety net, and not shifting evening placements is right; an evening placement filling the next morning would be a resting extended-hours order, still covered by REDATE's two-possibility wording.

**Addendum 2026-11-11 (owner's first dry run on real data, MRVL #419):** a new verdict **MERGED_FILLS** and a wider "check before logging" net on UNLOGGED, built on top of `fb7b221`. See §D (tiers) and §E (fields, rank 95, text, `nearby_journal_ids`).

Parent plan: [trade-entry-guard.md](trade-entry-guard.md). Phase 1, READ-ONLY: computes verdicts, writes nothing, wires no UI. U9 (Pending Imports verdicts) and U8 (Home line) are later chunks; C6 (correction planner) / C7 (DB function) are Phase 2.

## Why the plan's C5 bullet changed

1. SnapTrade's `trade_date` is the ORDER PLACEMENT time (P0), so REDATE means "dates differ", not "the journal is wrong"; it says so, and its later one-click needs a confirmed fill date.
2. The broker side includes fills of every status; "linked" is decided only by `trades.broker_txn_id`, never by the pending status.
3. A new strict all-status loader (C5b).
4. `JOURNAL_MATCH_BROKER_LAG_SESSIONS` gets a defined meaning (D1).
5. "One verdict per row and per fill" needs kinds the plan didn't list: RECONCILED, NOT_YET_COMPARED, UNCHECKED, LINKED_DIFFERS, SKIPPED_NON_EQUITY.
6. A weekend / holiday placement maps to the next trading session.
7. C2b's `pending_fill_findings` stays separate (different question: one staged entry vs pending fills, tick severities, shipped and reviewed). Shared primitives plus cross-consistency tests keep the two from drifting.

## Chunks

- **C5a** (decision-bearing, Opus reviewer): `stock_analyzer/constants.py` (4 constants), new `stock_analyzer/journal_broker_match.py`, new `tests/test_journal_broker_match.py`, `docs/architecture.md` (4 constant rows; update the "planned second consumer" text in the `JOURNAL_MATCH_PRICE_TOL_USD` / `JOURNAL_MATCH_WINDOW_SESSIONS` rows), `.claude/hooks/pre_tool_checks.py` (`_GATE_FILES` += the module), `docs/review-policy.md` (dated reason line + the module in the "Review REQUIRED" enumeration). CLAUDE.md's `_GATE_FILES` count 85 → 86.
- **C5b** (db.py is a gate file, reviewer): `db.load_snaptrade_fills_or_none(since_date)` + tests appended to `tests/test_broker_txn_sync_stamp.py` mirroring its existing loader tests.
- **C5c** (owner decision D6; read-only, mechanical): `scripts/journal_broker_match_dry_run.py` prints findings by kind and rank plus `match_coverage` text; calls the C5b loader, `db.load_trades_or_none`, `db.load_snaptrade_txn_synced_at_or_none`, `match_journal_to_broker`; `db.set_readonly(True)` first; never writes; owner-run like `scripts/account_snapshot_backfill.py`.
- **Docs (lead):** plan Status, bug register TJDate, CLAUDE.md queue. No F-ID yet (no surface until U9).

## A. Module and reuse

`stock_analyzer/journal_broker_match.py` is pure: no streamlit, no db, no network, no `stock_analyzer.data` import, no clock read.

Imports: `json`, `math`, `itertools`, `datetime` (date, timedelta), `pandas`; `from stock_analyzer import trade_entry_checks as _tec, trade_time`; from constants `JOURNAL_MATCH_WINDOW_SESSIONS`, `JOURNAL_MATCH_LOOKBACK_DAYS`, `JOURNAL_MATCH_BROKER_LAG_SESSIONS`, `JOURNAL_MATCH_MAX_FILLS`, `JOURNAL_MATCH_MAX_CANDIDATES`, `ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS`, `BROKER_DRIFT_SHARE_TOL` as module-level names (tests monkeypatch `journal_broker_match.<NAME>`).

Reuse from `trade_entry_checks` without copying or editing that file: `_is_trading_day`, `_previous_trading_day`, `_sessions_between`, `_as_date`, `_num`, `_aware_et`, `_to_et_datetime`, `_same_shares` (`BROKER_DRIFT_SHARE_TOL`, 9 dp), `_same_price` (`JOURNAL_MATCH_PRICE_TOL_USD`, 6 dp), `_txn_text`, `_raw_price`, `_fmt_date`, `_fmt_dt_clock`, `_fmt_shares`, `_fmt_price`, `fills_since`. (Verify each exists at HEAD; if one is missing or named differently, report it rather than inventing a substitute.)

The only new calendar helper: `_session_on_or_after(d)` = `d` if `_tec._is_trading_day(d)`, else step forward one day at a time until it is.

## B. Public API

```python
def fills_read_since(now_et) -> date
def match_coverage(synced_at) -> dict
def match_journal_to_broker(journal, fills, synced_at, now_et) -> "list | None"
```

- `fills_read_since(now_et)`: `start = _aware_et(now_et).date() - timedelta(days=JOURNAL_MATCH_LOOKBACK_DAYS)`; returns the day after the trading day before `pool_start` (`pool_start = _tec.fills_since(start)`, i.e. `start` stepped back `JOURNAL_MATCH_WINDOW_SESSIONS` sessions), so a weekend / holiday placement whose session is `pool_start` is still read (review 2026-11-10). The caller passes `.isoformat()` to the C5b loader.
- `match_coverage(synced_at)` → `{"state": "known" | "unknown", "synced_at_et": datetime | None, "judged_through": date | None, "text": str}`. Parse `synced_at` exactly as `_tec.broker_check_coverage` does (aware datetime / `.isoformat()`; naive read as UTC; unreadable → unknown). `judged_through` = the latest calendar date `d` with `_judged(d, sync_date)` True, searching back from `sync_date - 1 day` at most 30 days; None if none. `_judged(d, s) = s is not None and s > d and _tec._sessions_between(d, s) >= JOURNAL_MATCH_BROKER_LAG_SESSIONS`.
  - known: "Journal rows dated after {_fmt_date(judged_through)} aren't compared with Robinhood yet (last completed sync {Day Mon D, h:mm AM/PM} ET)."
  - unknown: "The time of the last completed Robinhood sync couldn't be read, so journal rows with no Robinhood record aren't flagged."
- `match_journal_to_broker(journal, fills, synced_at, now_et)`: `journal` = `db.load_trades_or_none()` frame or None; `fills` = C5b rows (any status) or None; `synced_at` = `db.load_snaptrade_txn_synced_at_or_none()`; `now_et` tz-aware (ValueError via `_aware_et` otherwise). Returns None (couldn't check), `[]` (checked, nothing to say), or findings.

## C. Inputs

**Whole result None when:** `journal` is None or not a DataFrame; a NON-empty journal lacks any of `id`, `ticker`, `action`, `shares`, `price`, `traded_at`, `broker_txn_id`; `fills` is None or not a list / tuple; any fill element is not a dict; any fill has a blank ticker or an action not in {BUY, SELL} after strip / upper. An empty journal frame (with or without columns) is valid.

**Journal rows:** `df = trade_time.normalize_traded_at(journal)` (idempotent). `linked_ids` = `{_tec._txn_text(v)}` over the WHOLE frame (not windowed), ignoring None. Per row: action strip / upper, keep exactly BUY / SELL (skip SPLIT and anything else); ticker upper / strip, skip if blank; `id = _num(id)`; `date = trade_time.et_trade_date(traded_at)`; `ts` = UTC Timestamp of `traded_at` (sorting); `shares` / `price` = `_num`, must be > 0; `linked = _txn_text(broker_txn_id) is not None`. A row is unreadable when id, date, shares or price is None / ≤ 0; it marks its group (ticker, action) UNCHECKED when its date is ≥ pool_start or unreadable. Pool journal rows = unlinked, readable, `date >= pool_start`. Linked rows never enter the pool.

**Fills (all statuses: pending, dismissed, logged; owner D3):** status is the owner's workflow state, not the truth. Linked-ness is ONLY `txn in linked_ids` (also covers the broker-fill-record plan's later rule that a link pointing at a deleted trade reads unlogged again; when `linked_trade_id` lands, extend `linked_ids` with pending rows whose `linked_trade_id` is an existing trade id). Dedupe by txn text: sort by (`_num(id)` or inf, txn), keep the first. `raw` = `raw_json` (dict, or JSON string via `json.loads`; failure → None). If `raw` is a dict and `_txn_text(raw.get("option_symbol"))` is not None → non-equity: goes to SKIPPED_NON_EQUITY, never matched. `placed` = `_as_date(row["trade_date"])` (the stored column, ET date since C4; same source as `unlinked_fills`). `session = _session_on_or_after(placed)` (weekend / holiday → next session; no shift for evening times). `placed_at` = `_tec._to_et_datetime(raw["trade_date"])` when present and parseable, else None (display only). `shares` = `_num(row["shares"])` > 0. `price` = `_tec._raw_price(raw_json)` (exact) else `_num(row["price"])`, > 0. `status` = `str(status).strip().lower()` or None. A fill with unreadable placed / shares / price marks its group UNCHECKED. Pool fills = unlinked, equity, readable, `session >= pool_start` (the fill's session, not its placement date; review 2026-11-10).

**Windows:** `today = _aware_et(now_et).date()`; `start = today - JOURNAL_MATCH_LOOKBACK_DAYS`; `pool_start = _tec.fills_since(start)`; `repair_cutoff = today - ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS`. Rows in `[pool_start, start)` take part in matching, but a finding is kept only if `max(journal_dates + fill_dates) >= start`. The matcher filters by pool_start itself (the loader's `since` is only an optimisation).

**Coverage:** `sync_date` = ET date of `synced_at` per `match_coverage`; None when unknown.

## D. Matching, per group (ticker, action), groups sorted

Notation: `Dj`, `Sj`, `Pj` = journal row date, shares, price. `dist(j, f) = _tec._sessions_between(Dj, f.session)`. For a fill set S: Σ = sum of shares, V = Σ(shares × price) / Σ with exact prices. "shares ok" = `_same_shares(Σ, Sj)`; "price ok" = `_same_price(V, Pj)`.

**Step 0, LINKED_DIFFERS (not grouped).** For each fill (any status) whose txn is in `linked_ids` and whose `session >= start`, compare with the journal row carrying that txn (unique index guarantees one): ticker, action, shares (`_same_shares`), price (`_same_price` against the exact price), date (journal ET date ∈ {placed, session}). Any mismatch → LINKED_DIFFERS. Skip silently when the linked journal row is unreadable.

**Step 1, UNCHECKED.** A group with any unreadable row or fill (per C) becomes one UNCHECKED finding listing every pool journal id and fill txn of the group, readable or not. Nothing else in the group is matched.

**Step 2, LINK (greedy; mirrors the Tier-2 key newest-first in `classify_transactions`).** Journal rows in order (ts desc, id desc). Each takes, from the remaining pool fills with `f.session == Dj`, `_same_shares(f.shares, Sj)` and `_same_price(f.price, Pj)`, the one with the smallest |price gap|, then the lowest (txn text, pending id). Both consumed. Two identical journal rows and one fill → one LINK (the newer row); the other row goes on (the CSCO fixture).

**Step 3, explanation loop over remaining rows R and fills F.** `C(j) = {f ∈ F : dist(j, f) <= JOURNAL_MATCH_WINDOW_SESSIONS}`; `Cd(j) = {f ∈ C(j) : f.session == Dj}`. Explanations E(j), each with a tier and kind:
- **Tier 1, RECONCILED:** the whole-day set `Cd(j)` with `|Cd| >= 2`, shares ok and price ok, ALWAYS evaluated whatever its size; plus subsets `S ⊆ Cd(j)` with `2 <= |S| <= JOURNAL_MATCH_MAX_FILLS`, shares ok and price ok.
- **Tier 2, date kinds:** `S ⊆ C(j)`, `1 <= |S| <= MAX_FILLS`, not every `f.session == Dj`, shares ok and price ok. Any `f.session > Dj` → FORWARD; otherwise `|S| == 1` → REDATE, `|S| >= 2` → SPLIT.
- **Tier 2, price kind:** `S ⊆ Cd(j)` (1..MAX_FILLS, plus the whole-day set), shares ok and NOT price ok → PRICE_FIX.
- **Tier 3, MERGED_FILLS (added 2026-11-11, owner's dry run, MRVL #419: journal 5 @ 260.00 = 2 + 1 @ 260.00 on 10-14 and 2 @ 235.00 on 10-15, average 250.00):** `S ⊆ C(j)`, `1 <= |S| <= MAX_FILLS`, not every `f.session == Dj`, shares ok, NOT price ok, and ANCHORED: at least one fill in S has `_same_price(f.price, Pj)` (review of 2026-11-11: a bare share-count match could explain away a real JOURNAL_ONLY + UNLOGGED pair, e.g. a 10 @ 95 row against an unlogged 10 @ 120 two sessions later; the anchor also makes |S| = 1 impossible, since one anchored fill with the right shares is already REDATE / FORWARD; an unanchored row falls back to JOURNAL_ONLY / UNLOGGED plus the nearby net). The date + price analogue of SPLIT / REDATE; same-day shares-ok-price-not stays PRICE_FIX. Evidence is a share-count match only: stronger than one same-price fill, weaker than an exact price, so it is its own tier.
- **Tier 4, QTY_MISMATCH (was tier 3):** `S = {f}` with `f ∈ Cd(j)`, `_same_price(f.price, Pj)` and not shares ok; or `S = Cd(j)` with `|Cd| >= 2`, price ok on V and not shares ok.

**TOO_MANY(j)**, recomputed every iteration: (a) `|C(j)| > JOURNAL_MATCH_MAX_CANDIDATES` and no whole-day tier-1 explanation (no subset search); or (b) E(j) has no tier-1 or tier-2 explanation (empty, or tier-3 only; Opus review 2026-11-10, blocking: a lone same-day fill at the row's price is weak evidence, and the SELL variant otherwise put 6 phantom UNLOGGED SELLs at rank 10), `|C(j)| > MAX_FILLS` and `Σ(C(j)) >= Sj - BROKER_DRIFT_SHARE_TOL` (a bigger combination could exist, so the row must not fall to JOURNAL_ONLY + UNLOGGED, which invites duplicate logging).

Best tier b(j) = lowest tier with an explanation (a TOO_MANY row has b = 1). Signature = (kind, sorted tuple of (f.session, round(f.shares, 6), round(f.price, 6))). Unique = exactly one distinct signature among E at b(j). `chosen(j)` = among the unique-signature explanations, the one whose fills sort lowest by (session, txn text) (interchangeable copies share a signature). `claims_t(k)` = every fill in k's explanations with tier ≤ t; for a TOO_MANY row, all of C(k) at every t.

```
loop:
  recompute E, TOO_MANY for all j in R over F
  for t in (1, 2, 3, 4):
    Res = [j in R : not TOO_MANY(j) and b(j) == t and unique(E_t(j))
                    and fills(chosen(j)) ∩ ⋃_{k in R, k != j} claims_t(k) == ∅]
    if Res: resolve every j in Res (emit its kind, consume the row and its chosen fills); restart loop
  if nothing resolved: break
```

Terminates (each pass consumes at least one row); order-independent (rows resolved in one pass are disjoint by construction). A tier-3 claim never blocks a tier-1/2 resolution; an ambiguous tier-1 row blocks any weaker story on its fills.

**Step 4, leftovers.** Stuck rows (non-empty E or TOO_MANY) form a bipartite graph with fills: edge (j, f) iff `f ∈ claims_4(j)` (or `C(j)` when TOO_MANY). Each connected component → one finding: TOO_MANY if any member is TOO_MANY, else AMBIGUOUS; lists all its rows and fills. Rows with empty E, not TOO_MANY: `_judged(Dj, sync_date)` → JOURNAL_ONLY, else NOT_YET_COMPARED. Each NOT_YET row absorbs every unclaimed group fill with `dist <= WINDOW` (a fill near several withheld rows goes to the smallest dist, then the lowest journal id): this stops an unmatched fill from inviting a duplicate log. Remaining fills → UNLOGGED, one per fill.

**Step 5, SKIPPED_NON_EQUITY:** one info finding listing the in-window non-equity txns (none expected).

**Invariant (one verdict each):** every unlinked in-window journal row (readable or not) and every in-window equity fill appears in the `journal_ids` / `txn_ids` of exactly one finding. A linked pair appears only in LINKED_DIFFERS, or nowhere. `nearby_journal_ids` and `possible_duplicate_of` are references, not verdicts.

## E. Finding fields

Every finding is a dict with: `kind`; `severity` ("differs": UNLOGGED, JOURNAL_ONLY, QTY_MISMATCH, PRICE_FIX, REDATE, SPLIT, MERGED_FILLS, FORWARD, LINKED_DIFFERS; "unresolved": AMBIGUOUS, TOO_MANY; "unchecked": UNCHECKED, NOT_YET_COMPARED; "info": LINK, RECONCILED, SKIPPED_NON_EQUITY); `rank` (int); `ticker`, `action`; `journal_ids` (list[int], ascending) + `journal_dates` (parallel); `txn_ids` (list[str], sorted by (session, txn)) + parallel `pending_ids`, `fill_dates` (session), `placed_dates`, `fill_statuses`; `journal_shares` (sum or None); `journal_price` (one row: its price; several: VWAP; or None); `broker_shares` (sum or None); `broker_price` (VWAP of exact prices or None); `holdings_effect` (float, journal-over shares: BUY group journal − broker, SELL group broker − journal; positive = journal overstates holdings; 0.0 for date / price kinds; None for unresolved / unchecked / info); `correction_shape` ("link" | "redate" | "split" | "price" | "split_same_day" | None); `one_click_eligible` (bool); `one_click_block` (str | None); `requires_fill_date_check` (bool); `nearby_journal_ids` (list[int]); `possible_duplicate_of` (int | None); `differs_on` (list[str]: for LINKED_DIFFERS the fields that differ, in the order "ticker", "action", "shares", "price", "date"; `[]` for every other kind; added 2026-11-12 by TJDate U8a because `holdings_effect` is 0.0 for a linked row on the wrong ticker or action with equal shares); `text` (plain, no `$`, caller escapes).

**Rank (owner D5):** 10 UNLOGGED SELL · 15 QTY_MISMATCH with `holdings_effect > 0` · 20 JOURNAL_ONLY BUY · 30 UNLOGGED BUY · 35 QTY_MISMATCH with `holdings_effect < 0` · 40 JOURNAL_ONLY SELL · 70 PRICE_FIX · 80 REDATE · 90 SPLIT · 95 MERGED_FILLS · 100 FORWARD · 110 LINKED_DIFFERS · 120 AMBIGUOUS · 130 TOO_MANY · 140 UNCHECKED · 150 NOT_YET_COMPARED · 160 LINK · 170 RECONCILED · 180 SKIPPED_NON_EQUITY. Sort key: (rank, −max(journal_dates + fill_dates).toordinal(), ticker, first journal id or inf, first txn id or "").

**One-click (pre-filter only; C6 re-validates and is the authority, incl. an oversell check in the full replay):**
- LINK: eligible iff the fill has a txn id; shape "link"; no fill-date check.
- REDATE: eligible iff it moves back, the txn exists and `session >= repair_cutoff`; shape "redate"; `requires_fill_date_check` True.
- SPLIT: eligible iff all sessions ≤ Dj, every txn exists, earliest session ≥ repair_cutoff; shape "split"; `requires_fill_date_check` True.
- PRICE_FIX: eligible iff every txn exists; shape "price" (one fill) / "split_same_day" (several); no fill-date check.
- RECONCILED: not eligible, block "needs_multi_fill_link" (one `broker_txn_id` can't hold several fills; waits for the broker-fill-record plan's `linked_trade_id`).
- Everything else: block "display_only"; FORWARD's block is "forward".
- Block reasons: "no_txn_id", "outside_repair_lookback", "forward", "display_only", "needs_multi_fill_link".

`possible_duplicate_of` (JOURNAL_ONLY only): the lowest id of another journal row with the same ticker, action and ET date, `_same_shares` and `_same_price`, that is linked or got a LINK / RECONCILED verdict in this run.

**`nearby_journal_ids` on UNLOGGED (widened 2026-11-11):** every journal row of the same group within `JOURNAL_MATCH_WINDOW_SESSIONS` of the fill's session whose own verdict leaves it not fully matched: JOURNAL_ONLY (sentence "... is nearby and matches no fill: check it before logging this one."), members of an AMBIGUOUS / TOO_MANY component, and linked rows whose LINKED_DIFFERS differs on shares (sentence "... is nearby and isn't fully matched to Robinhood: check it before logging this one."). NOT_YET_COMPARED rows are never listed because they absorb every unclaimed fill within the window. References only: the one-verdict invariant is unchanged.

**Text templates** (prices `_fmt_price`, shares `_fmt_shares`, dates `_fmt_date`; check `_fmt_price`'s output contains no `$`, and if it does, render prices without it):
- LINK: "Your journal's {ACT} {sh} {TK} on {Dj} (#id) matches Robinhood's record of this order but isn't linked to it yet."
- RECONCILED: "Your journal's {ACT} {sh} {TK} on {D} (#id) is Robinhood's {n} fills that day combined (average {vwap})."
- REDATE: "Robinhood's record of {ACT} {sh} {TK} at {px} is dated {Df}[, placed {clock} ET], the day the order was placed; your journal (#id) has it on {Dj}. Check the 'Filled' date in Robinhood: if it filled on {Df}, the journal date is wrong; if the order waited and filled on {Dj}, the journal is right." When placed ≠ session add "(placed {placed}, so it filled {session} or later)".
- FORWARD: "Your journal (#id) has {ACT} {sh} {TK} at {px} on {Dj}, but Robinhood's record of this order is dated {Df}, the day it was placed, so it can't have filled on {Dj}. Check the 'Filled' date in Robinhood."
- SPLIT: "Your journal's {ACT} {sh} {TK} at {px} on {Dj} (#id) matches {n} Robinhood fills dated {dates}, average {vwap}. Robinhood dates each fill by when its order was placed; check the 'Filled' dates."
- PRICE_FIX: "Robinhood's record of {ACT} {sh} {TK} on {D} is at {bpx}, not {jpx} (#id)." Several fills: "Robinhood's {n} fills of … average {vwap}, not {jpx}".
- MERGED_FILLS: "Your journal's {ACT} {sh} {TK} at {jpx} on {Dj} (#id) matches {n} Robinhood fill(s) dated {placed dates} totalling {sh shares} at an average of {vwap}, not {jpx}." plus " (placed {placed}, so it filled {session} or later)" per fill whose placed date differs from its session, then " Robinhood dates each fill by when its order was placed; check the 'Filled' dates. If these were separate trades, a Robinhood fill is missing from your journal." `holdings_effect` 0.0, `correction_shape` "split", never one-click (block "forward" if any fill session > Dj, else "display_only"), `requires_fill_date_check` True. TOO_MANY (b) is unchanged: it still fires unless a tier-1 or tier-2 explanation exists, so a tier-3 MERGED_FILLS does not block it.
- QTY_MISMATCH: "Robinhood's record of {TK} {ACT} on {D} at {px} is {bsh} shares; your journal (#id) has {jsh}, so it counts {|Δ|} {more|fewer} {TK} shares."
- UNLOGGED: "Robinhood {bought|sold} {sh} {TK} on {D} at {px}, and no journal row matches it." Then, in order: dismissed " You marked it 'Already logged' in Pending Imports."; logged " Pending Imports shows it as logged, but no journal row carries it (the row may have been deleted)."; SELL " Your journal still counts these shares."; nearby " Journal row #N ({ACT} {sh} on {D}) is nearby and matches no fill: check it before logging this one."
- JOURNAL_ONLY: "Your journal's {ACT} {sh} {TK} at {px} on {D} (#id) matches no Robinhood record." With a duplicate: " #M has the same trade that day and does match Robinhood: this row may have been logged twice."
- AMBIGUOUS: "Your journal's {TK} {ACT} rows #… and Robinhood's records dated … could match in more than one way; check them in Robinhood."
- TOO_MANY: "Too many Robinhood {TK} {ACT} fills near {D} ({n}) to compare with journal row #id automatically."
- UNCHECKED: "Couldn't compare {TK} {ACT} with Robinhood: {journal row #N | Robinhood record T} has an unreadable date, share count or price."
- NOT_YET_COMPARED: "Your journal's {ACT} {sh} {TK} on {D} (#id) isn't compared with Robinhood yet: the last completed sync was {when}." Unknown: "…the time of the last completed broker sync couldn't be read."
- LINKED_DIFFERS: "Journal row #id is linked to Robinhood's record of this order, but they differ: {list}."
- SKIPPED_NON_EQUITY: "Robinhood records {n} look like option trades and weren't compared (the journal tracks stocks and ETFs)."

## F. Constants

Already present: `JOURNAL_MATCH_PRICE_TOL_USD` 0.01, `JOURNAL_MATCH_WINDOW_SESSIONS` 3, `BROKER_DRIFT_SHARE_TOL` 0.001, `ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS` 35. Add after `TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS`, in a block headed "Journal vs Robinhood comparison (journal_broker_match.py, TJDate C5), owner-agreed 2026-11-02 as U4; lag meaning owner-decided 2026-11-10 (D1)":

```
JOURNAL_MATCH_LOOKBACK_DAYS       = 35  # calendar days of journal rows and Robinhood fills compared; must stay <= ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS (test-pinned) so a proposed correction lands inside the automatic account-row repair
JOURNAL_MATCH_BROKER_LAG_SESSIONS = 2   # a journal row with no Robinhood record reads "not in Robinhood" only once the last completed transaction sync is on a later date and at least this many trading sessions after it; before that, "not compared yet"
JOURNAL_MATCH_MAX_FILLS           = 6   # most fills tried as the combination behind one journal row (a whole day's fills are always tried together, whatever their number)
JOURNAL_MATCH_MAX_CANDIDATES      = 12  # more unmatched fills than this near one journal row: no subset search, reads "too many fills"
```

## G. C5b loader

`db.load_snaptrade_fills_or_none(since_date: str) -> list | None`: strict, mirroring `load_snaptrade_pending_fills_or_none` but with NO status filter; selects `id,snaptrade_txn_id,ticker,action,shares,price,trade_date,raw_json,status`; `.gte("trade_date", since)`, order trade_date desc then id desc; `_read_all_pages(..., <same page size constant>, dedupe_key="id")`; None on no DB or any page failure. The C2b loader stays unchanged.

## Risks and siblings (named)

- A permanent REDATE on a correct resting-order / overnight log: display-only, worded with both possibilities; U8 must count only severity "differs" and should consider leaving REDATE out of the Home count (D2's Phase-2 "link as is").
- Don't derive a fill date from `settlement_date` (T+1, bank holidays like 11-11 / 10-12 are NYSE sessions): Phase 2 only.
- Duplicate-log risk (HoldDup class): an UNLOGGED that is really part of a journal row invites a double log. Guards: NOT_YET absorption, TOO_MANY (b), `nearby_journal_ids`, components instead of separate UNLOGGED + JOURNAL_ONLY. U9 must keep a "check before logging" line on UNLOGGED with nearby ids.
- D0-gap case (combined 8 @ 196.99 + a re-log of its 5 @ 200): LINK (5), JOURNAL_ONLY (8, nearby), UNLOGGED (3 @ 191.96, nearby #8). Pinned as a test.
- One journal trade logged as two rows for one fill (3 + 2 vs 5) → AMBIGUOUS. Accepted.
- Window edge: a row in `[pool_start, start)` can claim an in-window fill → only AMBIGUOUS, never a false UNLOGGED. Accepted.
- Noise C5 can't see: raw-SQL rebaseline rows within 35 days, journal trades from another account, symbol aliases (BRK.B / BRK-B), a stock split in the window → JOURNAL_ONLY + UNLOGGED. Named only.
- A stale sync gives an honest `[]`: U8 / U9 must always show `match_coverage(...)["text"]` beside "no differences", never "matches Robinhood".
- `classify_transactions`' Tier-2 key (broker_sync.py ~562) still uses the raw placement date, so a weekend-placed fill never auto-links at sync. Named for the broker-fill-record plan; don't change the cron write path in C5.
- (Closed 2026-11-12: retired in U9b.) `find_pending_match_candidates` (broker_sync.py ~729) is a third, weaker matcher (pending only, 2-dp key, no sums); U9 should replace its hint with C5's verdict.
- U8 later: one key, three-state, registered in `coord_freshness.py` in the same commit; dedupe against `_broker_drift_cache`; never in Act Today; UNLOGGED SELL's `holdings_effect` is U7's day+1 input.
- U9 later: keep "Already logged" for AMBIGUOUS / TOO_MANY / UNCHECKED / NOT_YET_COMPARED.
- Phase 1 writes nothing: RbPartial (`journal_rebuild`) and N1 (`_late_prepped`) untouched; restate if a later chunk adds a write.
- Weekend placement before the pool start (review NB 2026-11-10): the pool filters on the fill's SESSION, and `fills_read_since` reads from the day after the trading day before pool_start, so a Sat/Sun placement belonging to pool_start's session isn't dropped (it would otherwise leave a row reading a false JOURNAL_ONLY).
- A linked row covering several fills (e.g. 8 @ 196.99 linked to the 5 @ 200 txn) reads LINKED_DIFFERS (rank 110) plus an UNLOGGED SELL / BUY with no nearby row for the rest. No app path writes such a link today (the prefill locks shares). C6 must never link one txn to a row covering several fills; U9 should list a shares-differing LINKED_DIFFERS row as "nearby" for UNLOGGED fills in its group. Named (review NB5).
- An UNCHECKED finding whose bad item has no readable date is still dropped by the window rule when every other date in it is before `start`. Latent (the loader's `gte` excludes null dates; only a NaT `traded_at` triggers it). Named (review NB7).
- Performance: ≤ 2509 subsets per row per pass (C(12, 1..6)); 40 rows × 12 candidates measured 0.6 s. Fine for the script; cache when U8 puts it on Home.
- First run: CSCO 13902 (logged, unlinked) should read LINK with the RECOMMENDATION row; the other 5 logged-unlinked fills (August) are outside the lookback.

## Tests (`tests/test_journal_broker_match.py`; `now_et` fixed and tz-aware; `synced_at` well past unless stated)

**Contract:** None for a None / non-frame / column-missing journal, None or non-list fills, a non-dict fill, a blank-ticker fill, a fill action not BUY / SELL; `[]` for an empty journal with no fills and when every pair is linked; a naive `now_et` raises ValueError; AST purity pin (no `streamlit`, `stock_analyzer.db`, `stock_analyzer.data` import); reuse pins (`jbm._tec._sessions_between is tec._sessions_between`, likewise `_same_shares`, `_same_price`, `_raw_price`, `fills_since`); `_session_on_or_after` agrees with `data.is_trading_day` over 2026-10-01 → 2027-01-31; constants: `JOURNAL_MATCH_LOOKBACK_DAYS <= ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS`, ints, `MAX_CANDIDATES >= MAX_FILLS >= 2`, `LAG >= 0`.

**Fixtures (exact kinds, ids, ranks):**
- MRVL 10-30: journal BUY 8 @ 196.99; same-day fills 5 @ 200.00 + 3 @ 191.96 (VWAP 196.985) → one RECONCILED, no "differs".
- #444–446 (before the 11-02 fix): #444 GOOGL BUY 5 @ 310.60 at `2026-10-29T14:56:05Z`, #445 MSFT 3 @ 526.00 at `15:09:25Z`, #446 OKTA 7 @ 221.72 at `15:14:44Z`. Fills: MSFT 2 @ 527 (10-28), 1 @ 524 (10-29); OKTA 5 @ 224.406 + 2 @ 215 (10-28); GOOGL 3 @ 323 + 1 @ 300.99 (10-28), 1 @ 283 (10-29), plus 1 @ 280 (10-30). Expected: three SPLIT, each eligible with `requires_fill_date_check` (VWAPs 526.00 / 221.7186 / 310.598), and UNLOGGED BUY GOOGL 1 @ 280 (alternative subsets average 309.998 / 306.4, proving uniqueness).
- GOOGL 10-30 (after the 10-28 fix): BUY rows link; journal SELL #452 5 @ 278.82 vs fill SELL 6 @ 278.82 → QTY_MISMATCH rank 15 (`holdings_effect` +1) ahead of UNLOGGED BUY 1 @ 280 (rank 30); every fill dismissed, so the UNLOGGED text carries the 'Already logged' sentence.
- OKTA 11-02: journal BUY 3 @ 171.88 at 17:08 EST vs fill 3 @ 189.14 → PRICE_FIX, shape "price", eligible; combined with #446 present, no contest.
- CSCO 11-04: (a) two unlinked journal rows 3 @ 102 + a MANUAL 5 @ 103; fills 13902 (3 @ 102, logged) and 13903 (5 @ 103, pending) → LINK on the NEWER 3 @ 102 row and on 5 @ 103, JOURNAL_ONLY on the older row with `possible_duplicate_of` = the newer row's id; (b) the duplicate row linked to 13902 → JOURNAL_ONLY on the RECOMMENDATION row with `possible_duplicate_of` set; (c) today's state (duplicate deleted) → two LINKs.
- AMBIGUOUS: tier 1, journal 2 @ 100 with same-day fills 1 @ 99, 1 @ 101, 1 @ 99.5, 1 @ 100.5; tier 2, j1 on D and j2 on D+2 (5 @ 50 each) with one fill 5 @ 50 on D+1 → one AMBIGUOUS component listing both rows.
- Blocking across tiers: an ambiguous tier-1 row doesn't block an unrelated uncontested tier-2 row elsewhere in the group.
- TOO_MANY: 12 candidates → searched; 13 → TOO_MANY; a 7-fill cross-day exact split → TOO_MANY by (b), never JOURNAL_ONLY + 7 UNLOGGED; a 7-fill same-day combined row → RECONCILED (whole-day rule).
- FORWARD: journal BUY Mon 11-02, fill placed Tue 11-03 → FORWARD, not eligible, block "forward"; and the SELL variant.
- REDATE both directions: BUY and SELL logged one session late → REDATE eligible; pre-open (journal 11-05 07:30 ET, fill placed 11-04 20:30 ET) → REDATE with `requires_fill_date_check` and the two-possibility text.
- Repair cutoff: a REDATE whose session is before the cutoff → `outside_repair_lookback`.
- D0 gap: the expected output above.
- LINKED_DIFFERS: linked row 5 shares vs fill 6 → found; identical → nothing; fill not in the input → nothing.
- SKIPPED_NON_EQUITY: an option fill is never UNLOGGED.
- UNCHECKED: one unreadable fill price → whole group UNCHECKED, other groups still matched.

**Lag / stale sync (D1 boundaries):** journal BUY Mon 11-02, no fill: sync Tue 11-03 → NOT_YET_COMPARED; Wed 11-04 → JOURNAL_ONLY; a sync on 11-02 itself → NOT_YET. Fri 11-20: sync Mon 11-23 → NOT_YET; Tue 11-24 → judged. Thanksgiving: Wed 11-25 row, sync Fri 11-27 → NOT_YET; Mon 11-30 → judged. `synced_at` None / unreadable → NOT_YET with the unknown text. NOT_YET absorbs a nearby unmatched SELL fill (no UNLOGGED); a far fill is still UNLOGGED. `match_coverage.judged_through` checked for each. With the lag monkeypatched to 0, `_judged(d, s)` equals `tec.broker_check_coverage(d, s)["state"] == "covered"` over a date range.

**Time-context matrix (C5 judges ET dates only, never time of day):** evening log `2026-11-05T00:12:00Z` (11-04 19:12 EST) vs a fill on 11-04 → LINK, not REDATE. DST: fill on 10-30 placed `2026-10-31T00:30:00Z` (EDT 20:30, ET 10-30) vs a journal 16:00 EST anchor Mon 11-02 (`21:00Z`) → one session apart → REDATE. Weekend: fill placed Sat 11-07 vs journal Mon 11-09 → LINK with `placed_dates` [11-07]; journal Sat 11-07 vs fill Fri 11-06 → REDATE; journal Sun 11-08 vs fill placed Sat 11-07 → FORWARD. Thanksgiving: fill placed 11-26 vs journal 11-27 → LINK; journal 11-26 vs fill 11-25 → REDATE. Early close: journal 11-27 13:30 ET vs fill 11-27 → LINK.

**Invariants:** seeded property test (random small groups, ≤ 6 rows and 8 fills, dates within ±4 sessions): every in-window unlinked journal row and every in-window equity fill appears in exactly one finding; no id in two findings; output identical when rows and fills are shuffled. Window: a finding dated entirely before `start` is dropped; rows in `[pool_start, start)` still match; `fills_read_since` is the day after the trading day before `tec.fills_since(today - LOOKBACK)`. Every `text` is a non-empty str with no `$` and no `**`.

**Cross-consistency with C2b:** when `pending_fill_findings` returns MATCH_SAME_DAY with txn T, the matcher on journal + that entry returns LINK pairing them; MRVL: C2b `[]`, C5 RECONCILED; OKTA: C2b PRICE_DIFFERS, C5 PRICE_FIX.

**C5b loader (mock, style of the existing `tests/test_broker_txn_sync_stamp.py` loader tests):** no status filter; select includes `status`; `gte` on trade_date; pages until an empty page; `[]` is a list; any page failure → None; no DB → None; output feeds `match_journal_to_broker`.
