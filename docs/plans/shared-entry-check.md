**CHUNK 7 RE-PLANNED 2026-10-18 (planner Opus 5.5, PROCEED; owner decisions DECIDED 2026-10-18: **N-A Option 2** — same bar as a new buy (avg ≤ −0.15) or any Act Today card; **N-B yes incl. N-B2** — G-30 on news-paused adds AND new-pick `news_skipped` (chunk 7e) to measure whether news earns its keep; **N-C defer** macro on adds until G-07's first verdict); **mockup `docs/mockups/2026-10-18-chunk7-adds-mockup.html` APPROVED as-is 2026-10-18** (always-open "Adds held — entry check" block, "Also in Review" note on add cards, caption for names with a card today, "📰 News checked" on every add card, entry-held names left out of flat-day "Adds paused"). 7a COMMITTED `13ce7b2` (Opus SHIP after 1 FIX-FIRST); 7b G-30 Opus SHIP (incl. N-B2 new-pick Skips; post-review footnote/requirements wording fixes + a mean-pin test by the lead); 7c render next; 7a+7b+7c push together. **Queued, pre-existing, found in the 7b review:** a MOVER with modest scanner momentum (< `COMPOSITE_BUY`), a Buy composite and news ≤ −0.15 reconciles to "go" and is sized despite the news (`signal_reconciliation` ~145 requires momentum ≥ COMPOSITE_BUY for the negative-news Skip) — not skipped, not in G-30. Was: 7a in build (review round 1 FIX-FIRST: card labels must name the Home section the card shows in — Review-list trims render in Act Today). **For 7d dated notes (reviewer):** names held back only by a Review news WATCH now reach G-20…G-25 on bull days, so G-24/G-16/G-01/G-04/G-09/G-26/G-25 get rows that were silent before, their G-20 rows flip counterfactual False→True, and they start being logged as `add_winner` rec rows; entry-held names get no mention on flat/bear days (owner-approved mockup point 5). **For 7b:** the app Brief's news can be LLM-filtered while the cron's is raw VADER (F-308 D-H) — moot in prod (no key) but a G-30 row could come from one source only if a key is set; only the binding (first) hold gets a ledger row, so stale-then-news never yields G-30 — supersedes the 10-17 note's recommendation below.** Confirmed in code: `_act_blocked = all_flagged_tickers(act, review)` (daily_briefing ~877) includes Review news WATCHes, so since F-308 D-C one in-window headline ≤ −0.05 silently ACT-blocks a held name's add (bare `continue`, no bucket/line/ledger row); earnings blocks adds only via the Review earnings card; adds have no stale check; emails carry no adds. **Correction:** the add lane should NOT read `held_news_check` — that owns the PROTECTIVE question (watch / tighten stop); an add is new money, an ENTRY decision, owned by `entry_eligibility.check_news` (average ≤ `NEWS_SENTIMENT_NEGATIVE`, same as new buys). Design: `_add_block_gate` order CARD (Act/Review cards; under N-A Option 2 excluding Review news WATCH; incl. sold today) → existing G-20…G-25 → ENTRY last (`entry_check` with exactly check_stale, check_news, check_earnings; crash fails closed); new buckets `card_blocked_adds` and `entry_held_adds`; add cards gain an "Also in Review" news note; flat/bear: CARD/ENTRY-held names stay out of `adds_paused`. Chunks: 7a decision (daily_briefing, decision_bucket, golden) + 7b ledger G-30 (if N-B) + 7c render (app.py, mockup first) push together; 7d docs; 7e optional G-30 on new-pick `news_skipped`. **Owner decisions:** N-A what news pauses an add (Option 1 any news card — today, visible; **Option 2 recommended: same bar as a new buy (avg ≤ −0.15) or any Act Today card**; Option 3 tighten buys+adds together); N-B G-30 ledger id for news add-pauses (recommended; honest caveat: likely too few rows to reach a verdict in 12 months; N-B2 optional: also log new-pick news Skips under G-30 to actually measure news); N-C macro on adds (recommended: keep deferring until G-07's first verdict).

# Shared new-entry check — Watchlist, its email and Analysis get Grow Today's entry checks; Grow Today adds get a news check

**STATUS 2026-10-17 — chunk 7 must be RE-PLANNED before it is built (held-news coverage, `docs/plans/held-news-coverage.md`, F-308).** Every holding's headlines now reach the Brief, and owner decision D-C gives any in-window headline ≤ −0.05 on a held name a Review news WATCH — so a single tier-1 −0.40 headline is already ACT-blocked in `_add_block_gate` (via `all_flagged_tickers`) before chunk 7's planned `news_blocked_adds` would run, and chunk 7's "one tier-1 −0.40 now holds" test would pass through the ACT path, not its own bucket. Planner recommendation: the add lane reads `held_news_check`'s per-ticker verdict (one owner of "this held name has negative news") instead of a second average-compound derivation; chunk 7's remaining job is to make the ACT block VISIBLE (today it is silent) and to add the explicit earnings + stale checks. Its golden master is unaffected (it feeds act/review as fixtures).

**Status: DESIGNED + OWNER-APPROVED 2026-10-16 (all 12 recommendations, D1-D12, plus additions A1-A4).
Mockup v1 APPROVED 2026-10-16: `docs/mockups/2026-10-16-shared-entry-check-mockup.html`. Chunk 1 (golden
master) DONE 2026-10-16 (`35ea1df`); chunk 2 DONE (`400b01c`, pushed); chunk 3 DONE (advisor ENTRY_HELD +
G-28/G-29 capture, `f71164b`); chunk 4 DONE (Watchlist page wiring, `469d7a6`); chunk 5 DONE (email + cron, `82c27ad`); chunk 6 DONE (Analysis); chunks 3-6 pushed together; next chunk 8 docs, then chunk 7. Scope
sweep DONE (results + owner decisions S-A..S-D below).**

**Chunk 1 findings that bind later chunks:** (1) `_grow_today` takes the tone as an INPUT — the ±0.5 rule
lives in app.py/headless — so the golden cannot prove `classify_tone`; chunk 2 needs its own −0.5/−0.49 and
+0.5/+0.49 unit tests. (2) Earnings never blocks an add INSIDE `_grow_today`: the only block is upstream (the
Review earnings card routes the name to "ACT" in `_add_block_gate`). A held name with earnings tomorrow and no
Review card IS added (golden `N2EARN1`). Chunk 7 must make the earnings check explicit in the add path rather
than rely on the Review card existing. (3) The only wall-clock read reachable from `_grow_today` is
`daily_briefing.py` ~931 (`_dt.now(ET)`). Planner (Opus 5.5), one pass,
PROCEED WITH CHANGES. Governing principle: OP-07 (`docs/requirements.md` §2A.1, "Gate the call, never the
candidate"). Covers two CLAUDE.md queue items: F-305 follow-up (b) "shared new-entry check" and N2.

## Why
Seen live 2026-10-16, a bear day in Protect Mode: 🏠 Home said "Defer new entries" while 📋 Watchlist showed
BKNG (composite 80.8) READY TO ENTER sized 9 sh, and 📈 Analysis showed Strong Buy / 9 suggested. Grow
Today checks stale data, the G-07 macro calendar, negative news and bear-day tone before it calls a new buy.
`watchlist_advisor`'s ENTER_NOW, the Watchlist "Ready to Enter" email (F-265) and Analysis's sizing lane
check none of these. Owner calls 2026-10-15: stale YES, macro YES, news YES, bear-day DEFER with a visible
card, Grow Today's flat-day bar 78 NO (stays Grow-Today-only).

## What each surface checks today (planner, from code at `c72e7b8`; app.py lines approximate)

| Check | Grow Today new picks (`daily_briefing.py`) | Grow Today adds | Watchlist ENTER_NOW (`watchlist_advisor.py`) | Watchlist email (`headless_alert_engine.py`) | Analysis sizing (`app.py`) |
|---|---|---|---|---|---|
| Bear-day tone | early return 1049-1105; one `__MARKET__` G-23 ledger row | bull days only (2079) | none | none — tone is computed only inside `compute_morning_picks` (493-511), never passed to `compute_watchlist_entries` (761) | none |
| Tone fetch failed | silently treated as "flat" (app ~6368/~6549; headless 493/510) | — | — | — | — |
| Flat-day bar / hold | 1108; 1315-1352 | — | none (stays so) | none | none |
| Macro G-07 | map 909-947, applied 1258-1276 | **none** | none | none | none |
| Stale data | `stale_as_of` 1391; fund age 1404-1411 | — | none (only `verdict_basis`, 334) | none | none |
| Negative news | `_cross_reference` avg ≤ `NEWS_SENTIMENT_NEGATIVE` (516-526) → `reconcile_signals` skip → `news_skipped` 1513-1535 | indirect only: Act Today critical news or Review warning news → `_add_block_gate` "ACT" (silent) | none | none | none |
| Earnings ≤ `EARNINGS_IMMINENT_DAYS` | reconciled caution → `earnings_waiting` 1541 | **already blocked, silently** (Review earnings card → "ACT") | `HOLD_OFF_EARNINGS` 482 | inherited | "Earnings ✗" tile shown **but the size still renders** |
| Budget (F-306) | `budget_view.postpass` | same | asks keyed on `action == "ENTER_NOW"` → `_wl_budget_plan_cache` | `_emailed_items` = entries | `_an_asks` = every result |

**N2, corrected:** earnings already blocks adds (no visible reason). The news gap is *inverted* — a single
tier-1 headline at −0.40 does not block an add (Act Today needs ≥ 2 such headlines; Review only covers
(−0.25, −0.05]), while a mild −0.10 headline does. Held news is capped at 20 headlines book-wide
(`data.py` 469/511). Macro never checks adds.

## Design

**New pure module `stock_analyzer/entry_eligibility.py`** — `_GATE_FILES` + CLAUDE.md's enumeration in the
same commit. No clock reads (now/today passed in), no app/db imports.

| Function | Content |
|---|---|
| `classify_tone(sp500_pct)` | the bull/bear/flat rule now copied in app ~6331 and headless 501; bear = `≤ MARKET_TONE_BEAR_PCT` |
| `resolve_tone_state(home_ctx, home_is_today, home_built_at, live_pct, live_ok)` | `{tone, sp500_pct, available, source: "home"\|"live"\|None, as_of}` — today's Home brief, else live read, else unavailable |
| `macro_block_map(macro_events, today, now_et)` / `macro_hold(sector, map)` | 909-947 + 1258-1262 lifted verbatim, `now_et` injected; `macro_events is None` = unchecked |
| `data_trust_hold(bundle)` | 1391-1411 verbatim |
| `news_compound(items)` / `is_negative_news(c)` | one derivation (raw VADER over `news_raw`, same as Grow Today candidates); predicate identical to `reconcile_signals` line 118 |
| `entry_check(...)` | `{eligible, holds: [{check, gate_id, reason, clears_when, gate_value, gate_threshold}], binding, unchecked: [{check, why}]}`; never `None`; precedence stale → tone → macro → news → earnings; every applicable hold listed, the ledger records only `binding` |

Grow Today keeps its own order and buckets and calls the lifted pieces in place — behaviour proved unchanged
by a golden-master test (chunk 1).

**Surfaces**
- **Watchlist:** `build_watchlist_recommendation(..., entry_ctx=None)` — `None` is the legacy path,
  byte-identical. With a context, the check runs in the ENTER_NOW branch after the existing hard fit gates; a
  held name becomes new action **`ENTRY_HELD`** (with `hold_kind`), never ENTER_NOW. That one choice keeps it
  out of the budget asks, the `enter_now` capture, the email list and Analysis's mirror.
- **Analysis:** not-held Buy/Strong Buy sizing only — size withheld, excluded from `_an_asks`, reasons in the
  existing Gate-checks strip; verdict label kept.
- **Email:** `compute_morning_picks` returns its `entry_context` (market context + macro events); `_run_scan`
  passes it into `compute_watchlist_entries`, so both emails in one run share one tone read.
- **Adds (N2):** news check added LAST in `_add_block_gate`, new `news_blocked_adds` bucket, so every existing
  gate's bucket and ledger rows are unchanged.

**Input unavailable**

| Check | Missing input | Behaviour | Why |
|---|---|---|---|
| Stale | keys absent (old bundles) → pass | hold (closed) when flagged | same as Grow Today |
| Tone | fetch failed / `tone_available` False | **open, visible "bear-day check couldn't run"** | Grow Today runs that day as flat; closed would make them disagree |
| Macro | `None` / raised | open + disclosure; `[]` = checked, none | static calendar; same as Grow Today cron |
| News | no headlines (indistinguishable from an outage) | open; card says "no recent headlines to check" | same as Grow Today |
| Earnings | date missing | open (current behaviour everywhere) | — |
| `entry_check` crashes | — | **closed**: no ENTER_NOW, "entry check couldn't run" | it is a gate |

New `tone_available` flag on the market context: today a failed index fetch is recorded as "flat" (a
fabricated reading). Grow Today keeps treating it as flat (D1) but gets a render-only caption.

**Coordination:** Watchlist and Analysis read tone and macro events from today's `_home_synth_cache` bundle
(`_market_context`, `_macro_events`, app ~7084) — the same object Home renders; no new cache key. `ENTRY_HELD`
never reaches the budget asks, emailed items or `new_position_budget_allocations`.

## Owner decisions (2026-10-16 — all planner recommendations accepted)

| # | Decision |
|---|---|
| D1 | Tone unreadable → **fail open with disclosure** on every surface; render-only caption on Grow Today too |
| D2 | Tone source for Watchlist/Analysis → **today's Home brief first, then a live read**, source + time labelled |
| D3 | **G-28** = Watchlist macro hold (sibling of G-07; reusing G-07 would collide on the ledger unique key (ticker, rec_date, gate_id, source)). **G-29** = Watchlist bear-day deferral, one row per ticker, lane "downgrade", counterfactual=True (reusing G-23 would break its `__MARKET__` readout). **No ledger rows** for stale (data abstention), news (F-302 precedent), Analysis holds, add-news holds |
| D4 | Cron writes Watchlist ledger rows too (G-05/06/13/28/29, `source="cron"`) — so deferral costs are graded on days the Watchlist isn't opened |
| D5 | Deferred names are not captured as `enter_now`; the email re-announces them on the next ready day with "(deferred yesterday)" |
| D6 | Every Watchlist name deferred → **send no email** |
| D7 | Analysis applies the full check incl. earnings, not-held names only; held-name add sizing stays the existing D5 gap |
| D8 | G-07 macro on Grow Today adds → **out of scope**, queued as its own decision |
| D9 | Watchlist stale check → **ENTER_NOW only**; REMOVE untouched |
| D10 | `enter_now` population changes at cutover → **dated footnote** on the ~Nov base-rate readout, no version column |
| D11 | **No new constants.** `GROW_TODAY_MAX_FUND_AGE_DAYS` (2), `MACRO_IMMINENT_DAYS` (3), `MARKET_TONE_BEAR_PCT` (−0.5), `NEWS_SENTIMENT_NEGATIVE` (−0.15) now govern every entry surface; names kept, docs say so |
| D12 | Held cards sit in the Actionable view directly under ENTER NOW (confirm on mockup) |

## Card / banner wording intent (mockup is authoritative once approved)
- **Bear-day Watchlist banner:** "🛡️ Protect Mode — S&P −0.8% (risk-off ≤ −0.5%). New entries deferred,
  matching 🏠 Home. N names would otherwise be Ready: BKNG…" + source line ("from Home's Brief, 09:42 ET" /
  "live read").
- **Bear-day card:** "Ready on its own numbers — deferred: market risk-off". Clears on a session where the S&P
  isn't down 0.5% or more; re-checked every load. No size, no trade/log button, ▶ Analyze only.
- **Macro card:** "Held: CPI in 2d (Consumer)" — clears after the release.
- **News card:** "Held: negative news (avg −0.22, n headlines) '…'" — clears when the average rises above −0.15.
- **Stale card:** "Held: score uses cached data from <date>" / "fundamentals N days old" — refresh to re-check.
- Watchlist KPI "0 Enter Now · 3 held today"; held/sold-today overrides catch `ENTRY_HELD`; Watchlist
  Resurrection excludes it.
- **Analysis:** banner under the verdict strip; composite label kept; Shares "—".
- **Email footer:** "Market tone at 09:45 ET: flat (S&P −0.2%); the app re-checks when opened and defers if the
  market turns risk-off." + any couldn't-run lines.

## Build plan

| # | Chunk | Type | Files | Tests |
|---|---|---|---|---|
| 0 | This doc + mockup → owner approval | lead | docs | — |
| 1 | Golden-master of `_grow_today` at HEAD: tone × macro (none / 3d / 4d / `__ALL__` / day-0 released / FOMC same day before+after time) × stale/fresh/fund-age 2 vs 3/keys absent × news −0.15/−0.149/none × earnings 0/7/8; clock frozen | implementer, tests only | tests | itself |
| 2 | `entry_eligibility.py`; Grow Today onto the lifted fns; `classify_tone` wired into app + headless; `tone_available`; `_GATE_FILES` + CLAUDE.md | implementer → reviewer | new module, daily_briefing, headless, app (2 lines), pre_tool_checks, CLAUDE.md | golden unchanged; −0.5 bear / −0.49 not; macro day 3 holds, day 4 not; `None` vs `[]` both reachable; resolver order; news leg == `reconcile_signals` skip over a matrix |
| 3 | Watchlist `ENTRY_HELD` + G-28/G-29 ledger builder + registry + §2A.3 rows | implementer → reviewer | watchlist_advisor, gate_ledger, gate_registry, requirements | `entry_ctx=None` byte-identical; held never ENTER_NOW; `build_enter_now_rows` → 0 rows; no sizing; registry anti-rot |
| 4 | Watchlist page wiring (render) + pure `watchlist_asks()` | implementer → reviewer | app, budget_view | asks exclude `ENTRY_HELD`; screenshot (no literal `**`, no `$`-LaTeX) |
| 5 | Email: pass `entry_context`; footer; cron ledger rows (D4) | implementer → reviewer | headless, cron_runner, notify | bear → entries [], no `enter_now` rows, no allocation; app and cron give the same card for identical inputs |
| 6 | Analysis: pure `analysis_entry_hold`; withhold size; Gate-checks rows | implementer → reviewer | new pure fn, app | held → no size, excluded from asks; earnings day 7 → no size |
| 7 | N2: news check last in `_add_block_gate`; `news_blocked_adds`; earnings pin test | implementer → reviewer | daily_briefing, app render | old buckets identical; `add_positions` loses exactly the news-held names; single tier-1 −0.40 now holds; earnings tomorrow never reaches `add_positions` (paired with a fixture that does — not vacuous); mutation-check each |
| 8 | Docs | lead | requirements F-307 + §2A.3 G-28/G-29 + F-264/265/306/305 lines; architecture; user-manual; User Guide; shipped-log; CLAUDE.md queue; this Status line; memory | `check_constants_documented` |

**Chunks 4, 5 and 6 push together** — any one alone creates a new cross-surface disagreement. Chunk 7 can
ship any time after chunk 2.

## Additions agreed after the mockup (owner, 2026-10-16)
- **A1 — emailed, then deferred (chunk 4).** A name the 09:45 email sized on a flat market, which the app
  later defers (market turned risk-off), shows on the Watchlist as "Emailed at 9:45 AM with 6 sh — now
  deferred: market risk-off". Its allocation in `new_position_budget_allocations` **stays locked** (stays
  counted against today's budget) — the conservative direction; nothing is released. Same treatment for an
  emailed name later held for macro/news/stale. Test: an allocated + `ENTRY_HELD` name renders the emailed
  line, is still counted in `used`, and gets no new size.
- **A2 — Trade Journal awareness (chunk 4).** The BUY confirm line adds "Note: <TICKER> is held today —
  <reason>" when the ticker is `ENTRY_HELD` in today's Watchlist plan or deferred/held by Grow Today. Awareness
  only — never blocks or alters the trade (same posture as the F-306 budget line). If the hold state can't be
  read, say nothing (no false "not held" claim either).
- **A3 — pre-registered review rule for G-29 (Watchlist bear-day deferral), written before any data.** Graded
  by the existing Gate Suppression Ledger readout at `GATE_LEDGER_HORIZON_TRADING_DAYS`, deferred names' forward
  alpha vs SPY. **No review before** the readout's "firm" band (`GATE_LEDGER_FIRM_CALLS` matured calls and
  `GATE_LEDGER_MIN_TICKERS` distinct tickers) **and** rows from at least 3 distinct bear days (one episode is
  one sample, not many). **If** the deferred names' alpha is positive and distinguishable from zero by the
  readout's own rule → deferring cost money → the lead brings a proposal to drop or narrow the Watchlist
  bear-day deferral (an owner decision, never automatic), read side by side with G-23's verdict so Grow Today
  and Watchlist are decided together and don't split again. Zero/negative/indistinguishable → keep. Not firm
  within 12 months of the first row → keep, no change. Do not soften this later.
- **A4 — room for more checks (chunk 2).** `entry_check` takes its checks as an ordered list of small pure
  check functions (each returns a hold, an unchecked note or nothing), so a future check — e.g. a
  margin-cushion floor, if the owner reopens that decision (open item 7 in
  memory `project_next_session_plan_2026_10_16`) — is one function plus one list entry. This decides no
  policy; no leverage check is built.
- **A5 — optional, not scheduled:** show "held N days" on a card held several sessions running.
- **Scope sweep before chunk 3:** a read-only Opus sweep of every surface that hands out a share count or a
  buy call (incl. Diversification Advisor ADD cards, beta-repair adds, Rebalancer ADD) vs every cap/gate. Open
  question it feeds: should a risk-REDUCING defensive add be deferred on a bear day like any other buy (lead's
  view: no; owner's call).

## Chunk 2 review follow-ups (Opus reviewer SHIP, 0 blocking, 2026-10-16) — fold into chunk 3
1. `check_stale` with `bundle=None` passes silently → return an `unchecked` note; test.
2. `resolve_tone_state` accepts a Home ctx that never set `tone_available` → require `is True`, AND add
   `"tone_available": False` to `compute_daily_brief`'s fallback ctx (`daily_briefing.py` ~3529).
3. `entry_check` crash → fail closed is the CALLERS' job (chunks 3/5/6): put it in each spec + test it.
4. (before chunk 5) pin headless `tone_available` with a real `compute_morning_picks` test: fetch raises →
   False; S&P row missing → False; present → True.
5. After a `compute_daily_brief` crash, app.py ~6547 overwrites a successfully-read tone with a flat fallback
   (pre-existing) and now sets `tone_available: False`, so the D1 caption wrongly says the tone "couldn't be
   read" → keep the already-read `_market_context` in that except block (also removes the fake S&P +0.00%).
6. (disclose in chunk 5) two pre-existing fabricated-flat paths keep `tone_available: True`: a single-bar
   series (`yfinance_provider.py` ~261-263, `prev = price` → 0.0%) and a NaN `change_pct`.
7. AST purity test for `entry_eligibility` (no streamlit / db / app / margin imports, no `now(`/`today(`).
8. `check_macro` holds on the reason string's truthiness → decide on set membership.
9. `tests/test_grow_today_golden.py` docstring cites the old clock line (~931, `.time()`).

## Chunk 3 review follow-ups (Opus reviewer SHIP, 0 blocking, 2026-10-16) — fold into chunk 4
1. `test_entry_ctx_none_is_byte_identical` compares new code with itself — freeze the 12 fixtures' expected
   `repr` as a golden (or keep a HEAD-snapshot comparison) BEFORE chunks 4-6 touch the advisor.
2. `_entry_held_card(...)` / `list(_chk["unchecked"])` sit outside the try: a hold missing a key raises, and
   the caller's per-ticker except drops the name entirely (fails safe but not OP-07-visible). Wrap so it
   becomes a check_failed held card, or read hold fields with defaults.
3. `check_macro` with a None/"" sector passes silently when specific sectors are blocked — chunks 4/5 must
   pass the curated `sector_gate_context` sector (same as G-05); add an `unchecked` note for a falsy sector.
4. `check_macro` indexes `bm["sectors"]` — `{}` now raises (→ check_failed); feed only `macro_block_map`
   output or read with defaults.
5. Key names differ: eligible card `entry_unchecked`, held card `unchecked` — unify.
6. ENTRY_HELD drops `portfolio_caution` / `deterioration_warning` — pass them through.
7. `gate_ledger.build_watchlist_suppression_rows` docstring still says G-05/06/13 / NEAR_ENTRY only.
8. App wiring (chunk 4): Actionable filter (~27839), held + sold-today overrides (~27938/27954), sizing runs
   for every row (~28008-28024 → withhold on ENTRY_HELD, S-A), KPI "N held today" (~27746), label/icon/badge.
   `$`: existing card text renders through `_md_bold` inside raw HTML (safe); any hold text routed through
   st.info/caption/metric/plain markdown must escape `$`; "S&P" in hold text must go through safe_html.

## Chunk 4 notes (Opus reviewer FIX-FIRST/1 → SHIP, 0 blocking, 2026-10-16) — carry into chunks 5-8
- Round 1 blocking (fixed): the Protect banner said "matching 🏠 Home" when Home's own index read had failed
  (Home ran the day as flat and sized picks) and the Watchlist's live read was bear. D2 stands (Home first,
  then live; a live bear still defers); the banner now words each path truthfully via
  `entry_eligibility.home_tone_state` (see `tone_source_line`).
- **For chunk 5:** stamp `tone_read_at` on the headless market context too; decide whether G-28/G-29 rows
  are written for ENTRY_HELD names already in the portfolio (no held filter in
  `build_watchlist_suppression_rows`, same as G-05/06/13) — app and cron must match.
- **Decided by lead 2026-10-16 (measurement integrity, chunk 5):** G-28/G-29 rows are NOT written for
  tickers already in the portfolio — a held name cannot be a deferred NEW entry, so counting it would grade
  the gate on calls that were never going to be made. Applies in the app and the cron alike, via
  `build_watchlist_suppression_rows(held_tickers=…)`, which filters only the G-28/G-29 kinds; G-05/06/13
  behaviour is unchanged. Owner may reverse.
- **Open — owner decision, must be settled before any G-28/G-29 readout can leave "building" (months
  away):** with the cron writing Watchlist rows (D4), an app row and a cron row for the same ticker and day
  count as TWO calls in `gate_ledger_readout.grade_by_gate`'s `n_matured_evaluable` (the alpha mean and the
  distinct-ticker floor already use one row per ticker). New skew for G-05/06/13 (app-only until now) and
  for G-28/G-29; for G-29 it would reach A3's "firm" bar up to ~2× sooner, softening a rule written as "do
  not soften". Options: (a, lead's recommendation) count distinct (ticker, rec_date) for EVERY gate — also
  the optional G-07 fix already noted in CLAUDE.md; (b) a dated readout footnote per gate; (c) count A3's
  firm bar on distinct days for G-29 only. Raw rows keep `source`, so any option is a read-time change.
- Known, accepted: held news cards omit the mockup's "Worst: '…'" headline line; the held card is several
  stacked boxes (escaping rule); the A2 Trade Journal note reads only the Watchlist's `_wl_held_cache`.
- Nits (optional): `home_tone_state(None, True)` → "tone failed" (unreachable today); the held card's
  deterioration caption ends "Shown so you enter with eyes open".
- Still showing an F-255 size on non-ENTER_NOW Watchlist rows (NEAR_ENTRY / WAIT / HOLD_OFF_EARNINGS /
  REMOVE) — by current design; S-D.

## Scope sweep result (planner Opus 5.5, 2026-10-16, read-only, HEAD `c72e7b8`)
Beyond the five surfaces above (+ the 11:30 intraday email, confirmed to re-run `compute_morning_picks` and
re-read tone live), **seven more places hand out a share count or dollar amount**; four apply neither F-255 nor
F-306:

| Surface | Hands out | Caps | Entry checks |
|---|---|---|---|
| Diversification Advisor ADD (portfolio.py 2268-2296; app ~15860-16057) | sector "Suggested add $X" = gap to `DIVERSIFY_ADD_TARGET_PCT` × gross book (≈ 26% of net capital per sector at 2.63×) + ✅ "genuine entry" banner | none | none |
| Rebalancer ADD (rebalancer.py 266-420) | shares + $ from drift; custom target can reach 100%; fires on unscored names | none | news warning only |
| Signals & Advice "High-Conviction Undersized" (portfolio.py 882-899; app ~15697-15735) | Log button pre-fills BUY = the CURRENT holding's share count (doubles the position) | none | none, no `_reduce_calls` |
| Econ Calendar pre-event OPPORTUNITY (macro_playbook.py 337-340, 440-446) | "N shares (~$X)" = 10% of holding within 14 days of a release | none | inverted vs G-07 |
| Catalyst Watch earnings HOLD_OR_ADD (earnings_advisor.py 350-369) | "small add 5-10%" (no count); can fire ≤ `EARNINGS_IMMINENT_DAYS` | none | contradicts earnings gate |
| Watchlist non-ENTER_NOW rows | F-255 sizing panel (no Log button) | F-255 only | none |
| Analysis non-Buy verdicts | scorecard "→ N suggested" on Sell/Hold; Hold-verdict "📒 Add" prefill; exported brief "Trade: Buy N" for any verdict | F-255 + F-306 | none |

Also: Grow Today adds and Analysis held-name adds apply both caps to the NEW shares only (a name at 14.9% can
end ~30% of the book) — disclosed in the User Guide, but the largest structural gap at real leverage.

**Corrections to this plan's map:** (1) the Watchlist size panel renders for every row with a size (app ~27957-
27966, ~28132) — chunk 4 must explicitly withhold it on `ENTRY_HELD`; (2) Analysis sizes Hold/Sell names and
exports "Trade: Buy N" for any verdict — D7 covered Buy/Strong Buy only; (3) Grow Today adds have NO stale
check (the table's "—" understated it); (4) `_run_intraday` is a second caller of `compute_morning_picks` —
chunks 2/5 need a test for it.

**Bear-day defensive adds (open question):** the sweep disagrees with the lead's "don't defer". Evidence: the
Diversification ADD reduces correlation, not beta, and can suggest beta-raising names; the beta-repair panel
never sizes anything; and on a margin book an add cannot reduce absolute risk (gross grows against the same
equity — `beta_repair.py` 41-50's own example: beta 1.88→1.76 while exposure-to-equity 5.90×→6.08×). The
risk-reducing trade is the trim/swap, which is never deferred. Lead now agrees: defer defensive adds like any
buy.

**Owner decisions on the sweep (2026-10-16, all accepted):**
- **S-A — folded into this build:** chunk 4 withholds the Watchlist size panel on `ENTRY_HELD`; chunk 6 gives
  NO size on any non-Buy verdict in Analysis (scorecard, Hold-verdict "📒 Add" prefill, exported "Trade: Buy
  N"), not just applies the entry check to Buy/Strong Buy; chunk 7 adds the stale-data check (`data_trust_hold`)
  to the add path alongside news and an explicit earnings check; chunk 2/5 add a test for `_run_intraday` as
  a second caller of `compute_morning_picks`.
- **S-B:** a risk-reducing / defensive add is deferred on a bear day like any other buy (no carve-out).
- **S-C — done now, before chunk 2:** the Signals & Advice "High-Conviction Undersized" card no longer
  pre-fills a BUY for the current share count; it routes to Analysis for a sized add. Its "earnings in −Nd"
  past-date text fixed in the same commit.
- **S-D — after this build:** one planner design so EVERY surface that hands out a share count or dollar
  amount goes through the same caps (F-255, `SINGLE_NAME_CEILING`, F-306) and entry checks: Diversification
  ADD $, Rebalancer ADD (+ its G-25/G-26 gap, CLAUDE.md D5), Econ Calendar OPPORTUNITY, Catalyst Watch
  HOLD_OR_ADD, Watchlist non-ENTER_NOW rows, Analysis held-name adds, and caps that today apply to the new
  shares only.

## Known limits (accepted)
- Tone from Home's brief is as old as the brief (≤ 30 min auto-refresh, longer if locked); the source time is
  printed so the Watchlist agrees with what Home shows.
- Grow Today drops a candidate headline duplicating a held name's; Watchlist computes per ticker, so the two
  can differ on a shared headline.
- Analysis's news tab is LLM-rescored; the entry check uses raw VADER (as Grow Today candidates do). The hold
  reason quotes the headline and its score.
- Pre-market tone is yesterday's close — inherited from Home.
