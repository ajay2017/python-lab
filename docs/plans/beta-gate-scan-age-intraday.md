# Beta gate, scan age, intraday stop: one buy gate on every buy surface

**Status:** DESIGNED 2026-11-26 (Opus planner `claude-opus-5-5`, PROCEED WITH CHANGES; checked against HEAD `702a79b7`). **Owner 2026-11-26: took the defaults U1–U8 and U10–U13; U9 = (a) the stop check only (no new constant now), plus a log of each 11:30 candidate's drop from the open in ATRs so a cap can be set from data later (lead recommendation: with a 1.0-ATR cap the 1.5% trigger leaves no window for any name whose ATR is under ~1.5% of price).** C1 in build (spec `docs/plans/beta-gate-c1-spec.md`; `decide` order corrected to breach-first, the Watchlist's order). Bug-register rows BetaGateWatchlistOnly, ScanCacheAnyAge, IntradayBelowStop (pick (1) of the order agreed 2026-11-26). Builds on entry-fail-closed C0–C2b (`docs/plans/entry-fail-closed.md`) and reuses `buy_check_state`, `watchlist_fit_source`, `session_label`. Planner correction to the brief: the Watchlist email's scanner-Go dedup (`headless_alert_engine.py:1420`) only filters cards that already qualify as ENTER_NOW, and a beta-blocked Watchlist card is NEAR_ENTRY, so the Watchlist email never sends it; the leak is the pick email's own `hi` set (`cron_runner.py:2332-2334`), which becomes consistent once Grow Today holds the name.

## 0. Changes required before any build
1. **Gate order in Grow Today.** G-06 is checked after every existing policy and data-quality gate (after `earnings_waiting`, daily_briefing.py:1788-1799; after `ENTRY` in `_add_block_gate`), so G-07 / G-16 / G-30 / G-01 rows and buckets stay byte-identical and the G-06 counterfactual covers only names that would otherwise have been picks.
2. **Scan freshness is keyed on `scanned_at` at or after today's 09:30 ET open, not on `scan_date`.** A pre-open 🔄 Refresh Signals saves today's `scan_date` on yesterday's close (app.py:9750-9751).
3. **Every place that assigns `scanner_results` sets the meta**, through one helper. Today the Market Scanner page (app.py:26219) sets no meta, and Refresh Signals stores `scanned_at` as a bare date (app.py:9752-9755).
4. **Every reader of a Grow Today bucket list gets the new buckets in the same commit:** `sizing_path._PAUSE_BUCKETS` (sizing_path.py:269-280), app.py `_grow_shown` (10633-10639), `grow_dropoff` (grow_dropoff.py:115-121), `gate_ledger._BUCKET_LANES` (gate_ledger.py:40-73), the bear early return (daily_briefing.py:1276-1333) and `tests/fixtures/grow_today_golden.json`.
5. **Intraday:** checking the live price against the pick's own stop is mandatory. A maximum drop is a new constant, so it is the owner's call (U9).

## 1. Verified current behaviour
| Leak | Code | Fact |
|---|---|---|
| G-06 Watchlist only | watchlist_advisor.py:204-221 (breach), :229-268 (C2a `fit_unknown` / `beta_unknown`), :667 (ticker β = `data["risk_metrics"]["beta"]`) | `PORTFOLIO_BETA_CEILING` (1.4, constants.py:16) and `TICKER_BETA_CRITICAL` (1.8, :131) are read in no Grow Today path. |
| Grow Today beta | daily_briefing.py:855 `_grow_today`; :496-523 `_trim_targets` | Adds: G-01 holds only the top-3 β×weight contributors of a HIGH/MEDIUM beta rec. New picks, movers and ETFs: no beta check. Cron passes `risk_recs=[]` (headless_alert_engine.py:838), so G-01 can't fire there. |
| Both cron emails | headless_alert_engine.py:624 `compute_morning_picks` → :837 `build_daily_briefing`; cron_runner.py:2308 (scan), :2746 (intraday) | Both lanes go through `_grow_today`, so one fix covers both emails. |
| Scan any age | app.py:3484-3501 (hydrate once per session, `_scanner_cache_checked`); db.py:3209-3240 (row id=1, no date filter); daily_briefing.py:1463 (`price = row["Price"]`), :1842-1859 (stop / zone / size from it) | A session opened before 09:45 keeps yesterday's scan for the whole session. The app's pre-09:45 `new_pick` rows win the `(ticker, rec_date, rec_type)` first-writer upsert (architecture.md:1277). |
| Cron intraday age check | cron_runner.py:2741 | `scan_date != today`: date only, no open-time check. |
| Intraday | intraday_entry.py:78-99 | Only the trigger `drop <= -dip_pct`, no lower bound, sorted biggest drop first. Stop / zone / shares come from the scan-time price. notify.py:1000+ prints "now $X · stop $Y" unguarded, and its footer claims "cleared all gates this morning". |

## 2. Decisions

### 2a. BetaGateWatchlistOnly (G-06 extended; G-34 leg for "couldn't check")
- **Owner:** new gate module `stock_analyzer/beta_fit_gate.py` (pure, no clock, never raises; joins `_GATE_FILES` plus a review-policy line).
  ```python
  def breached(port_beta, ticker_beta) -> bool
      # both legs readable via buy_check_state.beta_or_none, then
      # port_beta > PORTFOLIO_BETA_CEILING and ticker_beta > TICKER_BETA_CRITICAL (strict)
  def decide(*, port_beta, ticker_beta, fit_state) -> dict
      # {"state": "pass"|"blocked"|"unknown",
      #  "kind": None|"beta"|"beta_unknown"|"fit_unknown",
      #  "port_beta": float|None, "ticker_beta": float|None,
      #  "gate_value": ticker_beta if blocked else None,
      #  "gate_threshold": TICKER_BETA_CRITICAL if blocked else None}
      # breached -> blocked (first, as on the Watchlist); unknown / unreadable fit -> unknown(fit_unknown); flat -> pass;
      # both read, not breached -> pass;
      # a leg unread and buy_check_state.beta_leg_could_bind -> unknown(beta_unknown); else pass
  def hold_text(decision, ticker, *, lane) -> {"reason", "clears_when"} | None   # lane "pick" | "add" | "etf"
  ```
- **Single source of truth: yes.** `_portfolio_risk_gate` swaps its inline G-06 comparison (watchlist_advisor.py:205-206) for `beta_fit_gate.breached(...)`. This is behaviour-identical for every finite beta; NaN compared False before and reads unread now. Two accepted changes (C1 review): +inf used to hard-block and now reads unread (the C2a `beta_unknown` hold applies with a read book), and a non-numeric string no longer raises at the breach comparison. It keeps `buy_check_state.beta_leg_could_bind`. A parity test runs one (pb, tb, fit) matrix through both `decide` and `_portfolio_risk_gate`. Wording stays per surface.
- **Inputs and freshness:**
  - **Ticker β:** the same `load_all` / `load_bundle` 6mo bundle's `risk_metrics.beta` that the Watchlist reads. For picks and movers that is `composites[t]`; for ETFs, `_ebundle`; for adds, `held_data[t]`. App cache ≤ 30 min; the cron fetches fresh per lane.
  - **Portfolio β:** app = the same Home build's `_port_risk["beta"]` (published at app.py:7840; memoised with the Brief, so the two never disagree). Cron = `ctx["port_risk"]["beta"]`, the same read `compute_watchlist_entries` uses (:1162-1163).
  - **Fit state:** `buy_check_state.fit_state(holdings, port_df)`.
- **Wiring:** `build_daily_briefing(..., beta_fit: dict | None = None)` passes `{"portfolio_beta", "fit_state"}` through to `_grow_today`. `None` means not wired (tests only). Both production callers pass it: app.py:8096 and headless_alert_engine.py:837. The AST test pins both ends.
- **Surfaces that hold:**
  - 🌱 **New picks (curated + movers):** check after `earnings_waiting` (:1799), before conviction and sizing.
    - `blocked` → new bucket `beta_blocked_picks` (gate_id "G-06", counterfactual True, gate_value = ticker β, threshold `TICKER_BETA_CRITICAL`, price, composite / momentum, no sizing).
    - `unknown` → `beta_unknown_picks` (no gate_id, no ledger row).
  - **Adds (U1):** `_add_block_gate` order becomes `… G-25 → ENTRY → G-06 → CORR_UNMEASURED → BETA_UNKNOWN`.
    - G-06 → `beta_blocked_adds` (lane add_winner).
    - beta_unknown → `entry_held_adds` with check "beta_unknown", no gate_id.
    - `_adds_paused_list` inherits the change through `_add_block_gate`.
  - **ETF lane (U2):** after F-303 (:2155-2162) → `etf_screened_out` kind "beta" / "beta_unknown", no ledger row (ETF-lane precedent, :2084-2086).
  - **09:45 pick email / 11:30 email:** inherited (the picks are absent from `new_picks`). Add `beta_blocked` / `beta_unknown` to `diag` (:907-923) and to the cron "dropped:" explainer (cron_runner.py:2562-2569).
  - 📋 **Watchlist:** unchanged.
- **Hold wording** (app; plain text, no `$` pairs):
  - Pick: "⛔ Not opened today — portfolio beta. Your book's beta is {pb:.2f}, above the {CEIL:.1f} limit, and {T}'s beta is {tb:.2f} (over {CRIT:.1f}), so this buy would add to the book's market sensitivity. The 📋 Watchlist holds it for the same reason. Clears when the book's beta is back at or under {CEIL:.1f}."
  - Unknown: "⏸ Held — portfolio beta couldn't be checked: {why}. That's a reason to pause, not a green light. Re-runs at the next Home build or scan."
  - Add: "Add paused — portfolio beta …" (same facts).
- **Couldn't check:** `beta_unknown` holds only when the unread leg could bind (the Q3 rule already adopted). An unknown fit never reaches Grow Today in practice: the app's outage gate stops the page, and the cron returns no picks when ctx isn't ok (:656-658). It is still handled (holds all).

### 2b. ScanCacheAnyAge (new G-34 leg)
- **Owner:** new module `stock_analyzer/scan_freshness.py` (pure; joins `_GATE_FILES`).
  ```python
  def decide(scan_meta: dict | None, now_et: datetime) -> dict
      # {"state": "fresh"|"stale"|"unknown",
      #  "why": "ok"|"pre_open"|"closed_day"|"older_session"|"before_open_scan"|"no_meta"|"unreadable",
      #  "scanned_at_et": datetime|None, "scan_session": date|None, "next_session": date|None}
      # fresh iff session_label.price_move_session(now_et) == (True, today)
      #   and scanned_at (tz-aware, converted with zoneinfo) falls on today's ET date at or after 09:30
      #   (U6: stays fresh after hours the same day)
      # naive / unparseable / missing -> unknown (holds, like stale)
  def should_replace(current_meta, cached_meta) -> bool   # re-hydrate only to a strictly newer scanned_at
  def hold_banner(decision) -> str | None
  def priced_at_label(decision) -> str | None   # "Sizes priced at the 9:47 AM ET scan."
  ```
  Promote `session_label._OPEN` (session_label.py:14) to a public `SESSION_OPEN`. That is a market fact, not a new policy constant.
- **Surfaces that hold:**
  - `_grow_today(..., scan_state=None)` (passed from `build_daily_briefing`). When the state isn't "fresh":
    - `curated_rows` and `mover_rows` are not evaluated (U8 movers).
    - `scan_hold = {state, why, scanned_at_et, next_session}` is returned on every path, including the bear return.
    - The stale scan's names reach `unevaluated_leads` (existing renderer, momentum only, no verdict, no size), labelled "from the {date} scan".
    - No `new_pick` rows and no budget use. The ETF lane and adds are untouched, since neither uses the scan.
  - **App:** pass `scan_freshness.decide(_scanner_results_meta, _now_et())`. Add `(state, scanned_at)` to `_synth_sig` (app.py:7496-7518). Render `hold_banner`, and `priced_at_label` when fresh (one freshness per number: holdings are live, pick sizes are scan-time).
  - **Cron intraday:** replace cron_runner.py:2741 with `decide(cache, now_et)["state"] != "fresh"` (adds the at-or-after-open rule; `force` still bypasses).
  - **Cron scan:** passes nothing. Its scan is post-open by the lane guard (:2258).
- **Re-hydrate:** at app.py:3484-3501, while the session's scan came from the cache and `decide` says not fresh, re-read `db.load_scanner_cache()` through a short-TTL `st.cache_data` wrapper (TTL is plumbing and goes on the allowlist). Replace only when `should_replace`, then bump `_scanner_ver`. One helper `_set_scanner_results(df, meta)` serves app.py:3493, :9742 (`scanned_at` = the real ET timestamp) and :26219 (source "app_subset").
- **Wording:** "⏸ No new-position sizes yet today. The latest saved scan is from {Wed Nov 25, 9:47 AM ET}, not today's session, so its prices are that session's. New picks return after today's scan (about 9:45 AM ET) or a 🔄 Refresh Signals once the market is open." Variants: pre-open ("the market opens at 9:30 AM ET"), weekend / holiday ("Market closed today; next scan {Mon Nov 30} about 9:45 AM ET"), unknown ("couldn't tell when the saved scan ran").

### 2c. IntradayBelowStop (new G-35, documentation id only, U13)
- **Owner:** `stock_analyzer/intraday_entry.py` joins `_GATE_FILES` (small, pure, and the 11:30 lane's decision). `compute_intraday_entries(go_picks, price_data, spy_data, dip_pct, spy_max_down, max_drop_atr=None)` returns `{"entries": [...], "excluded": [{"ticker", "why": "below_stop"|"breakdown"|"stop_unknown"|"atr_unknown"|"no_price", ...}]}`. Its one caller (cron_runner.py:2783) is updated.
- **Rule order:**
  1. SPY guard (unchanged).
  2. Trigger `drop <= -dip_pct` (`PULLBACK_ENTRY_DIP_PCT` 1.5, unchanged).
  3. `sizing.stop` missing or ≤ 0 → excluded `stop_unknown`.
  4. `current <= stop` → excluded `below_stop` (strict: stop + 0.01 passes).
  5. U9(b): `(open − current) > max_drop_atr × atr` → `breakdown`; ATR unreadable → `atr_unknown` (U12).
  6. No live price → `no_price` (logged, no longer a silent skip).
  7. Sort composite-first (U11).
- **Inputs and freshness:** live `fast_info` `current` / `open` against the stop from this morning's scan price. The pick dict gains an additive `"atr": _pick_atr` (daily_briefing.py:1876 pick construction; ETF :2222). The morning size is kept: the live price is lower, so the dollar risk is at or below plan.
- **Email:**
  - Each card prints "now ${cur} · stop ${stop} ({x}% below now)".
  - An `excluded` footer appears only when an email goes out anyway: "Not an entry: {T} is now ${cur}, below the ${stop} stop this morning's pick set." (U10).
  - The footer's "cleared all gates" sentence becomes a true list that includes portfolio beta.
  - Every lane run logs a "intraday excluded: …" line.

## 3. Chunks (one deploy each)
| # | Scope | Kind | Files | Review |
|---|---|---|---|---|
| C1 | `beta_fit_gate.py`; Watchlist predicate swap; parity test; update the pin at tests/test_buy_check_state.py:304-311 to the new predicate | decision-bearing, no behaviour change | new module, watchlist_advisor.py, tests, `_GATE_FILES`, review-policy.md | Opus |
| C2 | Grow Today G-06 + beta_unknown (picks, movers, adds U1, ETF U2); `beta_fit` kwarg at both callers; ledger lanes; `_PAUSE_BUCKETS`; `_grow_shown`; grow_dropoff reasons; diag + cron explainer; app render (beside the G-16 block at app.py:10785-10786); `_SYNTH_SCHEMA_VER` 33→34; golden fixture | decision-bearing (gate + coordination) | daily_briefing.py, headless_alert_engine.py, cron_runner.py, gate_ledger.py, sizing_path.py, grow_dropoff.py, app.py | Opus; screenshot |
| C3 | `scan_freshness.py`; Grow Today `scan_state` hold; meta helper at 3 sites; re-hydrate; synth sig; cron intraday refactor; `_SYNTH_SCHEMA_VER` 34→35 | decision-bearing | new module, daily_briefing.py, app.py, cron_runner.py, session_label.py | Opus; screenshot pre-open and after 09:45 |
| C4 | intraday guard; pick `atr`; notify card / footer; new constant if U9(b) plus its architecture.md row; `_SYNTH_SCHEMA_VER` 35→36 | decision-bearing (+ policy constant) | intraday_entry.py, daily_briefing.py, notify.py, cron_runner.py, constants.py | Opus; first live 11:30 email |
| C5 | docs (lead, not doc-writer) | policy text | requirements G-06 (extend surfaces) / G-34 (two legs) / G-35 + F rows (no existing F-ID found for the 11:30 lane: grep before adding), F-236 ③ pre-open note, architecture.md:4386 ("LIVE price" → scan price) / §6.13 / :238 / constants table, User Guide, bug register, shipped-log, review-policy, dated population footnotes (§5) | n/a |

Coord caches: no new session_state key. `_scanner_results_meta` is not portfolio-derived, so no `coord_freshness` entry. `_grow_today_sectors_cache` is unchanged (built from `new_picks`).

## 4. Tests the build must include (boundaries read from constants)
- **`breached` / `decide`:**
  - (CEIL, CRIT+1) → pass; (CEIL+0.01, CRIT+0.01) → blocked; (CEIL+0.1, CRIT) → pass.
  - (None, CRIT+0.01) → unknown; (None, CRIT) → pass; (CEIL+0.01, None) → unknown; (CEIL, None) → pass; (None, None) → unknown.
  - NaN / inf / bool / str read as unread. Flat → pass at any β. Fit unknown → unknown.
  - Parity matrix against `_portfolio_risk_gate`.
- **`_grow_today`:**
  - Book β 1.6 + pick β 2.0 → absent from `new_picks` / `overflow_picks`; in `beta_blocked_picks` with G-06 and no sizing; ledger row lane new_pick. Book β at CEIL → picked.
  - Precedence: G-16+G-06 → G-16 only; G-30+G-06 → news_skipped; earnings+G-06 → earnings_waiting; sub-Buy+G-06 → composite_skipped.
  - beta_unknown → no ledger row.
  - Adds: G-01+G-06 → G-01; ENTRY(news)+G-06 → entry_held_adds with G-30; G-06 add → `held_add_verdict` "paused".
  - ETF β 3 → screened_out "beta".
  - Bear return carries every new key as `[]` / `scan_hold`.
  - `beta_fit=None` → byte-identical to HEAD.
- **Cron:**
  - AST pin: app.py and `compute_morning_picks` both pass `beta_fit` (memory `ast_test_pins_one_end`).
  - Scan lane with a β 2.0 name in both the scan and the Watchlist, book β 1.6 → neither email lists it, no `new_pick` row. The intraday lane gives the same result.
- **`scan_freshness.decide` (time-context matrix):**
  - Trading day 10:00 + 09:47 scan today → fresh. 09:31 + yesterday's → stale. 08:00 + yesterday's → stale pre_open. 08:00 + a scan saved today at 07:55 → stale before_open_scan.
  - Boundary: scanned 09:30:00 → fresh; 09:29:59 → stale.
  - 18:00 same-day scan → fresh (U6).
  - Saturday + Friday's scan → stale closed_day; next_session = Monday.
  - Thu 2026-11-26 + Wed's scan → stale closed_day; next_session = Fri 11-27. Fri 11-27 14:00 (early close) + 09:47 scan → fresh.
  - DST, Mon 2026-11-02: 14:47Z → fresh; 13:47Z → stale. 2027-03-08: 13:47Z → fresh (proves zoneinfo, not a fixed offset).
  - Naive, garbage or None → unknown. `should_replace` only to a strictly newer scan.
  - `_grow_today` with a stale state → no `new_picks` from the scan or movers, ETF lane intact, leads labelled, no budget.
  - Frozen-clock guard: `decide` reads no clock.
- **Intraday:**
  - Drop exactly −`PULLBACK_ENTRY_DIP_PCT` → in.
  - current = stop → excluded; stop + 0.01 → in. Stop None → excluded.
  - U9(b) at exactly K×ATR → in; above it → breakdown. ATR None → excluded.
  - Composite-first order. SPY guard unchanged.
  - Render: no emailed card has `current <= stop`; footer lists the exclusions.
- After any copy edit, grep tests/ for the old strings (memory `grep_tests_after_text_edit`).

## 5. Measurement integrity
- G-32 shadow, Recommendation Outcomes (~12-16), the Engine Track Record and the Gate Suppression Ledger (~12-10): Grow Today `new_pick` populations change on the C2 / C3 ship dates. Add a dated "population event" line to each plan, criteria unchanged (precedent D10).
- C3 also stops stale-price app rows from winning the first-writer upsert.
- EnterNowInProtect is unaffected: its population is Watchlist `enter_now` only, and Watchlist G-06 is unchanged. No regime gate is wired.
- No constant changes value; U9(b) adds one. DoD #8: no version column needed.

## 6. Overlaps (named, not designed)
- **C3 earnings-unknown bucket:** slots after G-06 and before beta_unknown, so policy holds bind before abstentions.
- **C5 partial book:** `fit_state.unpriced_sectors` is already the `beta_fit` input.
- **C4 tone_unread:** independent early return.
- **SoldTodayLeak:** daily_briefing.py ~4466 reads None as nothing sold.
- **ToneNoAge:** the ETF lane on a pre-open bull read.
- **AnalysisSizesHeld** (app.py:29000 G-06 soft warning while the size prints): owner call.
- **ScannerGoBadge** (app.py ~26233), **RedeployHeadlineGates**, **OppSignalsNoConc**.

## 7. Sibling sites
| Site | Same class | Action |
|---|---|---|
| quick_research.py:162 (`tick > TICKER_BETA_CRITICAL and port > PORTFOLIO_BETA_ELEVATED`) | a different G-06 variant, inform-only | name it; owner aligns later |
| app.py:29000 | G-06 soft on Analysis | AnalysisSizesHeld (owner) |
| app.py:9752-9755 | meta `scanned_at` stored as a date | fix in C3 |
| app.py:26219-26223 | subset scan sets results with no meta and feeds Grow Today a subset | meta fix in C3; the subset feed named |
| app.py:9663-9667 | tooltip reads `scan_date` only | reuse `decide` in C3 |
| cron_runner.py:2741 | date-only age check | C3 |
| architecture.md:4386, §6.13 (~10:00 ET), :238 | doc claims "LIVE price" | C5 |
| notify.py `render_intraday_entry_email` footer | overclaims "cleared all gates" | C4 |
| intraday_entry.py:84-85 | missing live price skipped silently | C4 log line |
| F-236 ③ pre-open note | coexists with the scan hold | C5 wording |

## Owner questions (recommended default in brackets)
- U1: Apply G-06 to Grow Today adds too, placed after G-01 … ENTRY? [Yes]
- U2: Apply G-06 to the ETF new-pick lane (no ledger row)? [Yes]
- U3: Ledger Grow Today's G-06 holds as G-06 (lane new_pick / add_winner)? A Watchlist G-06 "downgrade" row for the same (ticker, date, source) collides on the upsert key and the first writer wins; in the cron that is Grow Today. [Yes, reuse G-06; the readout splits by lane. Alternative: a new id.]
- U4: Grow Today holds when the unread beta leg could bind (the Watchlist's Q3 rule)? [Yes]
- U5: Scan freshness rule: S1 = only a scan taken at or after today's 09:30 ET open sizes picks; pre-open, weekend, holiday and failed-scan days list the names unsized. S2 = the last session's post-open scan counts outside market hours. [S1]
- U6: Today's scan stays fresh after hours until midnight ET? [Yes, with the "priced at the 9:47 AM ET scan" line]
- U7: A maximum scan age within the session (a new constant)? [None now; disclosure line only]
- U8: Hold movers too when the scan isn't fresh? [Yes]
- U9: Intraday lower bound: (a) the pick's stop only; or (b) the stop plus a new constant `PULLBACK_ENTRY_MAX_DROP_ATR` (drop from the open, in ATRs). [(b). The value is the owner's call; the planner suggests 1.0. Note: for a low-ATR name, a 1.5% trigger can then leave no window.]
- U10: A pick that broke its stop shows as a footer line in an email that goes out anyway, with no standalone email? [Yes]
- U11: Intraday order: composite first, or biggest drop first? [Composite first]
- U12: Under U9(b), an unreadable ATR holds the entry? [Yes, G-34]
- U13: G-35 as a documentation id for the intraday guard, with no ledger rows? [Yes]

## Risks / coordination
- On a high-beta book, G-06 can empty Grow Today of high-beta names for weeks. That is intended (the 10-19 pocket), and the banner says so.
- Ledger key collision (U3).
- The bucket-reader lists (§0.4) must be updated in the same commit, or Analysis / Rebalancer read a beta-held add as "not called".
- The `_synth_sig` change and three schema bumps.
- Pin the `beta_fit` kwarg at both production callers.
- The scan hold turns pre-09:45 visits into "no sizes". The owner should confirm U5 before C3.
