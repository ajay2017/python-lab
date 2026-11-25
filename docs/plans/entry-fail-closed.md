# Entry fail-closed: a buy check that didn't run holds the buy

**Status:** DESIGNED 2026-11-25 (Opus planner `claude-opus-5-5`, PROCEED WITH CHANGES). Owner decisions U1–U3 final (below); **owner took every §8 default 2026-11-25** (Q1 macro unreadable holds; Q2 news unreadable does NOT hold, note kept; Q3 beta holds only when the unknown leg could bind; Q4 partial-book sector hold; Q5 no ledger rows, G-34 documentation id only; Q6 no email when every name is held, log line; Q7 self-build also when Home's frame is stale; 📈 Analysis included). C0 BUILT 2026-11-25 (implementer + a lead edit: an empty book, ctx reason `no_holdings`, reads as checked); **Opus reviewer SHIP, 0 blocking** (commit after office hours). Non-blocking notes carried into C2a, which replaces C0's `fit_read` / `beta_read` booleans with §2's `fit_state` (ready / flat / unknown): (1) harden `fit_read` to `port_df is not None and not port_df.empty` (tests feed `ok: True` with an empty frame); (2) parametrize the not-read test over `db_unavailable` / `no_bundles` / `empty_port_df` so only `no_holdings` is pinned as read; (3) box wording "couldn't be loaded or priced" for `no_bundles` / `empty_port_df`; (4) a partly priced book still gets the "sector check still applied" beta box (Q4 / C5 sibling); (6) the header comment on `beta_read` should say "nothing to compute" for an empty book. **P0 probe** `scripts/earnings_calendar_probe.py` BUILT; first smoke run 2026-11-25 (implementer, default list): AAPL / MSFT → dict with a dated "Earnings Date" list; ANT (IPO 11-19) → dict with `Earnings Date = []` (the clean `no_date` shape); VOO / SPY / QQQ and the bogus ZZZZQX → a bare `{}`; nothing raised. So a bare `{}` alone can't mean "failed" (ETFs return it too). Lead's proposed mapping for C1, pending the owner's larger run over the watchlist and scan names: ETF → `not_applicable` whatever the shape; stock + dict carrying the key with an empty list → `no_date`; stock + bare `{}`, a non-dict, or a raise → `failed` (hold). **C0 SHIPPED `5efddaee` 2026-11-25. C1 BUILT 2026-11-25** (implementer; `earnings_status` on every bundle via the provider / orchestrator / `load_bundle`, new gate module `buy_check_state.earnings_state`, cron log "watchlist earnings read: …"; no behaviour change): **Opus reviewer SHIP, 0 blocking** (checked against yfinance 1.3.0 `scrapers/quote.py`: the "Earnings Date" key exists only when Yahoo returned an earnings object; an HTTP failure gives a bare `{}`; so `no_date` comes only from a real answer). Notes for C3: (1) also list the `no_date` tickers in the cron log, so the owner can see only genuine new listings land there before C3 lets them pass; (2) `asset_type` comes from live `.info["quoteType"]`, so on a sparse-info day an ETF reads as a stock and its bare `{}` reads `failed` → a false hold; C3 needs an ETF fallback (ETF roster, cached quoteType or the broker kind); (3) keep `clears_when` consistent with `load_all`'s 30-minute cache; (4) orchestrator.py ~284 sets "date" on any truthy fallback value (harmless: `earnings_state` parses it) — parse there or reword the comment; (5) `fmp_provider.bundle` stamps no status (dormant; if FMP rejoins the bundle chain, its stocks without a date read `failed`). Tier 0 item 1 of the owner's 2026-11-25 pick order (`docs/bug-register.md` "Order agreed 2026-11-25"). Bug-register rows: EarnUnknownPass, EntryFailOpen, CronSectorClaim.

**Owner decisions (2026-11-25):**
- **U1:** when a buy check couldn't run (earnings lookup failed, portfolio weights / fit not loaded, today's market move unreadable) the buy is HELD (no Watchlist Enter Now, no Grow Today pick, no 09:45 email pick), with a visible banner naming the check and when it clears. Same as the existing unknown-sector hard hold.
- **U2:** the earnings read splits three ways: ETF / fund = not applicable (passes); stock whose lookup succeeded with no announced date = passes; lookup failed = held per U1.
- **U3:** the 📋 Watchlist builds the portfolio frame it needs itself when Home hasn't; if that self-build fails → U1 hold.

**Planner's required changes before any build:** (a) a read-only probe of Yahoo calendar shapes + failure rate before the earnings hold goes live (P0); (b) the new "couldn't run" holds never become the binding hold over a real policy gate (else G-28 / G-30 ledger rows are lost); (c) the Watchlist's self-built frame stays local, never published; (d) the reversal of owner decision D1 (2026-10-16, a failed tone read = "flat") is written into G-23 / G-29; (e) a dated population note in the EnterNowInProtect plan before the first live day; (f) Q1–Q4 answered.

## 1. Inventory: buy surfaces × checks, today's behaviour on a missing input

| Surface | Check | Today on a missing / failed input | File:line |
|---|---|---|---|
| 📋 Watchlist Enter Now (app) | Earnings | **Open, silent**: `_earn_days_until` → None; HOLD_OFF_EARNINGS needs `earn_soon`; `check_earnings` returns None (pass); the card even says "No imminent earnings risk" | watchlist_advisor.py:97-103, :539, :789; entry_eligibility.py:287-296 |
| | Tone | **Open + note** ("entries are shown, not deferred") | entry_eligibility.py:198-202, :469-471; app.py:30436-30475 |
| | Sector fit (G-05) | **Open + banner** "this one fails OPEN" when `_port_df_enriched` not ready: weight 0.0 | app.py:30338, :30372-30393; sector_fit.py:100-106 |
| | Beta (G-06) | **Open + banner** when `_port_risk_cache` not ready; ticker β None also passes silently | app.py:30406-30411; watchlist_advisor.py:188-189 |
| | Sector unknown | **Closed** (NEAR_ENTRY, no ledger row): the precedent | watchlist_advisor.py:173-185, :606-647 |
| | Macro calendar | **Open + note** | entry_eligibility.py:496-498; G-28 text (requirements.md:134) |
| | News unreadable | Unchecked note | entry_eligibility.py:272-275 |
| | Entry check crash | **Closed** (`check_failed`) | watchlist_advisor.py:735-756 |
| Watchlist 09:45 email (cron) | Same advisor, same checks | Same as app | headless_alert_engine.py:1092-1206 |
| | Fit when `_build_context` not ok | **Open**: `port_df=None` → weight 0.0, `gate_degraded=True`, name still emailed | :1040-1069, :1179-1192 |
| | Email box | **False claim** "The concentration/sector check below still applied" | notify.py:1214-1224 |
| 🌱 Grow Today picks (app + 09:45 morning email + intraday lane, all via `_grow_today`) | Earnings | **Open**: `_cross_reference` `earn_days` None → `reconcile_signals` `earnings_imminent` False → "go" | daily_briefing.py:652-669, :747-758; signal_reconciliation.py:113 |
| | Tone | **Open (owner D1)**: failed read = "flat" + `tone_available` False; picks at the flat-day bar | app.py:7800, :7845, :7851, :8056; headless_alert_engine.py:546, :600-606; daily_briefing.py:974, :1254-1255 |
| | G-16 sector cap | Closed on no book (cron returns no picks when ctx not ok, :588-590); **open for a partially priced book**: an unpriced holding drops out of the weights (portfolio.py:525-549, `attrs["dropped_holdings"]` :737) | daily_briefing.py:1046-1059 |
| | Macro | **Open, silent**: None → "nothing blocked" | daily_briefing.py:1028-1031 |
| Grow Today adds | Earnings via `_entry_verdict` | **Open** (same `check_earnings`) | daily_briefing.py:1076-1115 |
| | Tone | Closed in effect (adds are bull-day only, so a failed read = flat pauses them), wrong reason text ("bull days only") | :2262, :2722 |
| 📈 Analysis not-held Buy size | Earnings / tone / macro | Same `entry_check` (earnings open; tone / macro open + note "sizes are shown, not deferred") | entry_eligibility.py:685-708, :800-811 |
| | Holdings / fit | **Closed already** (D4: `holdings_state` "unknown" → no size) | sizing_path.py:426-442; app.py:26871-26880 |
| ⚖️ Rebalancer ADD | Derived | Defers to Grow Today's add call (S-D C5) | rebalancer.py:411-450 |
| Redeploy headline | Tone | **Closed already** ("an unreadable tone withholds") | entry_posture.py:11-13 |
| Market Scanner badge | Earnings | "✅ Go … Cleared to act" on an unknown date (display) | app.py:25912-25919 |
| Quick Research / Compare / Catalyst | Earnings | Inform-only, no size, no recorded call; siblings named, not changed: `next_earnings` swallows to None (yfinance_provider.py:346-359), FMP `_next_earnings` the same (fmp_provider.py:525-530) | |

Corrections to the brief: (1) the orchestrator earnings fallback (orchestrator.py:265-282) never fires today: Yahoo is the only bundle-capable provider (finnhub_provider.py:42 is live-price only; FMP left the chain), so the earnings date comes from Yahoo's `t.calendar` alone (yfinance_provider.py:286-294, every exception swallowed). (2) A Supabase-cached bundle carries `earnings: {}` (db.py:2224), but the stale check already holds it first.

## 2. Design

**New pure module `stock_analyzer/buy_check_state.py`** (joins `_GATE_FILES` + review-policy reason line; no clock, no streamlit, no db):
- `earnings_state(bundle) -> {"state", "date"}`, state ∈ `not_applicable | date | no_date | failed`. ETF / fund (`asset_type == ASSET_TYPE_ETF`) → `not_applicable` even with a stray date (a deliberate loosening per U2: today a stray ETF date ≤ 7 days holds it). A parseable date → `date`. `earnings_status == "no_date"` → `no_date`. `"failed"`, a stale cached bundle, or **no `earnings_status` key and no date** → `failed`. Legacy semantics: absent key = couldn't tell = hold; safe because `load_bundle` is the only production producer (app `load_all`, cron, Grow composites, ETF candidates all go through it) and a test pins that it always stamps the key. Cost: fixture churn (≈36 Watchlist / 18 `build_daily_briefing` test files); C1 measures it; if huge, fall back to "absent = legacy pass" with the producer pin as the guard.
- `fit_state(holdings_rows, port_df) -> {"state": ready|flat|unknown, "unpriced_sectors": set|None}` reusing `portfolio.refresh_outcome` semantics (portfolio.py:2607-2628): empty holdings → `flat` (weight 0 is true); holdings + empty frame → `unknown`. `unpriced_sectors` = curated `resolve_sector` of each `dropped_holdings` row with reason `no_price_data`; an unresolvable one → `{"__ALL__"}`.
- `beta_leg_could_bind(port_beta, ticker_beta) -> bool` (Q3): False when the known leg alone proves G-06 can't fire (ticker β ≤ `TICKER_BETA_CRITICAL`, or portfolio β ≤ `PORTFOLIO_BETA_CEILING`); True when the unknown leg could tip it or both are unknown. Existing constants only.
- `tone_unread(market_context) -> bool`: True only on explicit `tone_available is False` (the same `is False` rule as the D1 caption, app.py:9997).
- Banner / label text builders: one page-level line per unrun check, naming affected tickers + when it clears.

**Gate-file edits (minimal):**
- `entry_eligibility.check_earnings` reads `earnings_state(ctx["bundle"])`: `failed` → hold `{"check": "earnings_unknown", "gate_id": None, "reason": "earnings date couldn't be read", "clears_when": "the earnings date can be read — re-checked on a later load (analysis data refreshes every 30 minutes)"}`; `no_date` → "passed" note "no earnings date announced"; `not_applicable` → None.
- `check_tone`: unavailable → hold `"tone_unread"` ("today's market move couldn't be read", clears when "the S&P 500 read succeeds — re-checked every time you open this page").
- **`entry_check` binding precedence:** policy holds (`stale`, `tone`, `macro`, `news`, `earnings`) bind before abstentions (`tone_unread`, `earnings_unknown`, the fit kinds). Otherwise a `tone_unread` hold at tone's slot would bind ahead of a G-28 macro hold and `_HOLD_SUPPRESSION_KIND` (watchlist_advisor.py:1197) would write no G-28 ledger row. `holds` order unchanged; only `binding` changes.
- `watchlist_advisor._portfolio_risk_gate`: three hard abstentions **after** the G-06 check (so G-05 / G-06 still bind and are ledgered when they can evaluate): `fit_unknown` (state unknown), `fit_unpriced` (ticker's curated sector, or `__ALL__`, in `unpriced_sectors`), `beta_unknown` (the beta leg could bind). Each NEAR_ENTRY with its own copy like `sector_unknown`, no `_WATCHLIST_KIND` entry (no ledger row). Labels added to `HELD_KIND_LABELS`. The clean card's condition line becomes "No earnings date announced" for `no_date`.
- `_grow_today` (daily_briefing.py): (i) **tone unread**: an early return shaped like the bear branch (:1188) but `tone` stays "flat" and it adds `entry_hold = {"check": "tone_unread", reason, clears_when}` → `new_picks == []`, `adds_paused` with the real reason, no G-23 `__MARKET__` row. Distinct from bear: different copy ("couldn't read", not "risk-off"), different clear condition, and G-23 / G-29 measure real bear days; `tone` stays "flat" so no new tone value reaches every tone consumer (entry_posture, `_market_tone_cache`, the tone card). (ii) **earnings unknown on a new pick**: new bucket `earnings_unknown_picks`, placed **after** `earnings_waiting` (:1706-1717), so a known date, a G-30 news skip and every data-quality gate keep their own bucket and ledger rows. (iii) Adds pick up `earnings_unknown` through `_entry_verdict` → `entry_held_adds`. (iv) Q4: G-16 also holds a pick / add in an `unpriced_sectors` sector.
- `reconcile_signals` gains `earnings_known: bool | None = None` (None = today's behaviour); False turns a "go" into "🔍 Verify — earnings date unknown" (Market Scanner badge, Grow Today one-liner).

**Earnings plumbing:** `yfinance_provider.bundle` sets `earnings_status` ∈ {`"date"` / `"no_date"` when the calendar call returned a dict, `"failed"` when it raised or wasn't a dict; P0 decides how an empty `{}` reads}. The orchestrator fallback updates the status only from a fallback that returned without raising. `bundle_loader.load_bundle` passes it through and forces `"failed"` on the Supabase-cache fallback (:146-155).

**Watchlist self-build (U3):** when `_coord_cache_state("_port_df_enriched") != "ready"`, or the frame is empty while `holdings_df` isn't, or (Q7) `_portfolio_snapshot_stale()` says the book changed since Home's build, the page builds a LOCAL frame from `st.session_state.holdings_df` (a real read: the outage gate stops the page if holdings are unreadable, app.py:3498-3531, :4101-4115): `_parallel_load_all(held)` (process-wide `st.cache_data`, TTL 1800 s, app.py:4633, warm after any Home visit) → `build_portfolio_df(holdings, bundles, manual_stops={}, held_stops=None)` → `attach_gate_weight` → `risk.compute_portfolio_risk_metrics(port_df, bundles, spy_6mo, rfr)` for beta. Same builder as the cron's `_build_context` (headless_alert_engine.py:236-245), so app ⇄ cron parity. **Never published, never in session_state:** its Stop columns are deliberately blank, and publishing would mark Home's `_holdings_sig_at_home_build` / epoch "ready" from a frame Home never built, silencing the stale banner; not a session cache, so no `coord_freshness` registration. **Freshness:** weights use bundle prices (≤ 30 min) where Home uses live-merged prices (app.py:6565-6568); the banner says so ("Sector weights from your holdings at bundle prices, built on this page at 10:42 AM ET"), per one-freshness-per-number. **Failure:** `fit_state` "unknown" → U1 hold with one page banner, replacing the "fails OPEN" text (app.py:30382-30393) and the beta banner (:30406-30411).

**Cron:** `compute_watchlist_entries` computes `fit_state` (ctx ok → `ready` + `unpriced_sectors` from `held_tickers` minus frame tickers; ctx `reason == "no_holdings"` → `flat`; any other not-ok reason → `unknown`) and returns `fit_state` / `beta_read` in place of the overloaded `gate_degraded` (kept as a derived bool for one release). The email box is worded from those facts (CronSectorClaim). All names held → no email (D6 precedent); scan log "watchlist: N held, a check couldn't run (earnings_unknown: X, Y)".

**One banner, not a wall:** a Yahoo outage on every name sends the whole Watchlist / Grow Today to held; acceptable under the posture if announced once per page ("Earnings dates couldn't be read for 9 of 11 names; no Enter Now for them today") with a per-card badge only.

## 3. Measurement integrity

- **EnterNowInProtect readout** (pre-registered population = every logged `enter_now` call, episode gap 3). 30-session cells (criteria 1–3): untouched at both reads if the first live day is ≥ 11-27 (calls mature at 30 sessions by 12-15 only up to ~11-03, by 01-07 only up to ~11-24; verify with `market_time`'s calendar). 10-session cells (criterion 4) include post-ship calls (to ~12-01 at the first read, ~12-21 at the confirming read). The removal mechanism is an outage, not the market: a tone-unread day removes a whole day whatever its S&P label; earnings / fit failures remove single tickers; expected bias ≈ 0, small sample loss. **Mitigation (precedent D10, shared-entry-check.md:110, "dated footnote, no version column"):** before the first live day add a dated "population event" line to `docs/plans/entry-regime-check.md` §4 naming the commit and date, criteria unchanged. Same footnote on the Enter Now base rate / Recommendation Outcomes checkpoint (~12-16) and the G-32 readout (early Dec), since Grow Today pick rows change too.
- **DoD #8:** no constant changes value → no version column.
- **Ledger:** keep precedent D3 (no rows for data abstentions, like `sector_unknown` / `macro_unknown_sector` / `check_failed`). Add **G-34** as a requirements-table id only ("A buy check couldn't run → every new-entry surface: hold"), not in `gate_registry.GATE_IDS` since it emits no rows (precedent G-10, G-22, G-27). G-34 is the next free id (G-32 / G-33 taken). Alternative in Q5.
- Held cards produce no `enter_now`, `new_pick` or budget rows (`build_enter_now_rows` filters on ENTER_NOW, recommendations_history.py:136).

## 4. Constants

None. The holds compare against `EARNINGS_IMMINENT_DAYS`, `TICKER_BETA_CRITICAL`, `PORTFOLIO_BETA_CEILING`, `SECTOR_CEILING` exactly as today; no new boundary.

## 5. Edge cases

- Weekend / holiday / pre-open / after hours: the tone read returns the last session's move (existing staleness behaviour, unchanged); only a *failed* read holds. Earnings days stay calendar days from `today_et` (existing); `earnings_state` reads no clock. DST: no new clock comparison.
- Late-logged trade: the self-build reads the current `holdings_df`; Q7 covers a stale Home frame.
- ETF stray date → passes (test it). New listing → `no_date` passes (U2); usually fails `verdict_basis` first.
- Every-name outage → one banner per page, nothing emailed, log line.
- Cached bundles: the Supabase-cache fallback reads `failed` but `stale` binds first; a failed calendar read stays cached in `st.cache_data` up to 30 min (the clear text says so); in-memory caches don't survive a deploy, so no legacy bundle reaches production.

## 6. Chunks (each one implementer pass + one Opus review unless marked)

0. **P0 probe** (read-only script, owner runs, no review): `yf.Ticker(t).calendar` shapes for a stock with a known date, a recent IPO, an ETF, a bogus symbol, a forced exception; plus today's share of watchlist / scan names whose earnings read is None. Decides `{}` = `no_date` vs `failed`.
1. **C0 CronSectorClaim** [decision-bearing]: box text from `fit_state` / `beta_read`; headless_alert_engine.py, notify.py. May ship alone.
2. **C1 earnings plumbing, no behaviour change** [decision-bearing]: provider, orchestrator, bundle_loader, `buy_check_state.earnings_state`, scan-log count; new module joins `_GATE_FILES`.
3. **C2a pure fit / beta state + cron wiring** [decision-bearing]: `fit_state`, `beta_leg_could_bind`, `_portfolio_risk_gate` branches, `compute_watchlist_entries`.
4. **C2b Watchlist self-build** [app.py wiring; screenshot]: build, banners, macro calendar fed the built frame.
5. **C3 earnings hold** [decision-bearing]: `check_earnings`, binding precedence, Grow Today `earnings_unknown_picks` + render, `reconcile_signals` param, Scanner badge, labels. Live only after P0 + a few C1 log days.
6. **C4 tone-unread hold** [decision-bearing]: `check_tone`, `watchlist_banner` / `analysis_notes` / `email_footer_lines` wording, `_grow_today` `entry_hold` branch, Home tone card (no "Flat" on a failed read), Grow Today banner.
7. **C5 (if Q4 = yes) partial-book hold** in G-16 and the Watchlist.
8. **C6 docs** [lead, not doc-writer; policy values]: requirements G-34, G-23 / G-28 / G-29 amended (D1 reversed), F-307, an F-ID for the self-build line; architecture (module, fields, `earnings_status`); User Guide (Watchlist / Grow Today / Analysis); bug register (three rows closed; siblings named: `next_earnings`, FMP `_next_earnings`, Quick Research's silent unknown date, Analysis self-build as a later convenience); shipped-log; review-policy reason; the entry-regime-check §4 footnote (lands before C3 / C4 go live). Each chunk syncs its own requirements row in the same session.

## 7. Tests the build must include

- `earnings_state`: ETF with a date ≤ 7 d → n/a / pass; `date` at 0 and 7 d → hold, 8 d → pass, past date → pass; `no_date` → pass + note; `failed` → hold; absent key + no date → hold; absent key + date → date; Supabase-cache fallback → `failed`. Producer pin: `load_bundle` always sets the status (incl. when the calendar raises and on the cache path).
- Binding: `tone_unread` + G-28 → binding = macro, G-28 ledger row kept; `earnings_unknown` + negative news → news binds (G-30 row kept on adds; a news-skipped pick stays in `news_skipped`).
- Fit: unknown → NEAR_ENTRY `fit_unknown`, no ledger row; flat (empty book) → ENTER_NOW allowed; G-05 boundary unchanged (35.0 holds, 34.99 passes) and still binds before `fit_unknown`; unpriced holding in the same sector holds, another sector passes, unresolvable sector holds all.
- Beta could-bind: port β None + ticker β 1.81 → hold, 1.80 → pass; port β 1.41 + ticker β None → hold, 1.40 → pass; both None → hold.
- Tone: `tone_available` False → Watchlist hold, Analysis no size, Grow Today `new_picks == []` + `entry_hold`, no G-23 row; key absent → today's flat behaviour; `build_daily_briefing(market_context=None)` → held (default ctx, daily_briefing.py:4212); an available flat day unchanged.
- Cron: `no_holdings` → entries flow; `no_bundles` → all held, entries `[]`, no email; the box never says "still applied" when fit is unknown; pins at both ends (app and cron both pass `entry_ctx` and `fit_state`; memory `ast_test_pins_one_end`).
- No clock: `earnings_state` / `fit_state` under a frozen-clock guard; a Saturday visit with earnings Monday → hold (calendar days).

## 8. Owner questions (default recommended)

- **Q1** Macro calendar unreadable (today fails open with a note): hold too? **Default yes** (same class; rare thanks to the static fallback).
- **Q2** News unreadable: **default no hold**, keep the note (a 4-week Yahoo news outage in Oct 2026 would have blacked out every buy; news is the low-weight check G-30 is still measuring).
- **Q3** Beta: hold only when the unknown leg could bind (incl. the new "ticker β unknown while portfolio β > 1.4" hold)? **Default yes.**
- **Q4** Partially priced book: hold names in an unpriced holding's sector, on the Watchlist and Grow Today's G-16? **Default yes.**
- **Q5** No ledger rows + G-34 as a documentation id (D3 / D10 precedent)? **Default yes.** (Alternative: G-34 ledger rows, measured only, never retired on alpha, to cost the fail-closed policy.)
- **Q6** A scan run that holds names because a check couldn't run: send nothing (D6) + a log line? **Default yes.**
- **Q7** Self-build also when Home's frame is stale against the current holdings? **Default yes.**
- **Scope:** 📈 Analysis follows automatically (it shares `entry_check`; G-28 / G-29 already cover it). Say if it should not.

## 9. Risks / coordination

- Reverses owner D1 (2026-10-16); G-23 / G-29 must say so.
- If Yahoo's calendar fails often, Enter Now / Grow Today go dark; P0 + the C1 log size this before C3 goes live.
- EnterNowInProtect (rule A, C3 deferred) untouched; its own U8 ("unknown read at go-live: fail closed") is consistent with this batch.
- No overlap with HideValuesText, Lock Setup, DeferSizeBlind.
