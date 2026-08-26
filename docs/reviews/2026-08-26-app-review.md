---
review_date: 2026-08-26
mode: full-app product & engineering judgment pass (not a code audit)
scope: whole app — 25 page-dispatch branches in `app.py` (37,599 lines), 109 modules in `stock_analyzer/` (48,655 lines), 7 Railway cron lanes, 125 test files
prior_reference: docs/reviews/2026-08-24-review.md (incremental code audit; all its findings closed 2026-08-24/25 per docs/shipped-log.md)
method: read CLAUDE.md, docs/requirements.md, docs/architecture.md, docs/shipped-log.md, stock_analyzer/constants.py, the app.py page map, and the modules named below. Every numeric claim was re-derived from HEAD (a script diffed all 231 documented constant values against `constants.py`; `reference_shelf.shelf_status()` and `system_health._LANES` were executed, not recalled). Claims that could not be confirmed in code are marked **unverified**.
---

## Verdict

This is a genuinely disciplined system, and the discipline is load-bearing rather than decorative: 231 documented constant values match the code exactly, the provider layer really does fail over and cross-check, and the `None`-on-failure contract is honoured by the producers. The strongest single design in the repo is `reference_shelf.py` — it derives its own staleness horizon from the data, so the warning cannot disagree with the table it describes. The weakest structural fact is that `app.py` — 44% of the Python in the repo, holding the stop-breach gate, the reduce-call add gate and every net-capital cap call site — has zero unit-test coverage and is not in the hook's `_GATE_FILES`, so a change there that moves a real call passes every gate the project relies on. The app measures its buy calls, its protective calls, the user's own calls and analyst calls, but it has never once measured **the thing it does most often — suppress**; its entire caution apparatus, which is its stated product, is the one part with no feedback loop. And in a handful of places a check that never ran renders as a confident, itemised negative, which is exactly the failure mode the operating posture names as most dangerous.

---

## Part 1 — What's working

### Loops that actually close

Six surfaces produce a call, capture it, and grade it. Ranked by how much of the loop is really closed:

| Loop | Capture | Grading | Honest state |
|---|---|---|---|
| **Engine Offense (F-229)** | `recommendations` table, written by **both** the interactive app (`app.py:5644`) and the cron scan lane (`cron_runner.py:836`) | `recommendations_history.py` forward-alpha vs SPY | **Fully closed.** The only evidence base that accrues without the user opening the app. |
| **Self Track Record BUY / SELL / category (F-233, F-256, F-257)** | `trades` matched against `recommendations` / `exit_signals` | `self_track_record.py` | **Closed, with the coverage boundary encoded as policy** — `SELF_TRACK_RELIABLE_LOG_START = date(2026, 8, 6)` and `SELF_TRACK_SELL_RELIABLE_LOG_START = date(2026, 7, 21)` (`constants.py:1200,1206`). Putting the "before this date we cannot grade honestly" line in `constants.py` instead of a caption is the best integrity decision in the codebase. |
| **Analyst Research Scorecard (F-154c)** | `analyst_coverage` | `ANALYST_ACCURACY_*` (`constants.py:566-569`) | Closed; leaderboard floor `ANALYST_ACCURACY_LEADERBOARD_MIN_CALLS = 2` suppresses single-call noise. |
| **Predictive Shadow Layer (F-234)** | `model_predictions`, EOD cron writes + maturation | Brier/skill deferred to Phase 2 | Capture closed, scoring deliberately parked. Correct order. |
| **Engine Defense (F-229 addendum)** | `exit_signals`, cron-captured since 2026-07-21 | `protective_track_record.py` | **Loop exists, verdict withheld** — 3 matured calls against `PROTECT_TRACK_MIN_CALLS = 8` (`constants.py:1171`). |
| **The Judge (F-227)** | `judgment_opinions` (`app.py:4583,5951`) | `judgment_grading.py` | **Half-closed by design, and the honest half is missing.** Acquisitive dimensions grade; **every protective dimension is deliberately withheld** (`judgment_grading.py:19-37`) because naive sign-matching scores a correct caution as wrong. That refusal is right. It also means the protective side is ungraded everywhere. |

One structural caveat worth naming: `judgment_opinions` is written **only** from the Home render path (`app.py:4583`, `5951`) — no cron writes it, unlike `recommendations`. So The Judge's track record samples only the days the owner opened the app. The page's empty state is honest ("Open 🏠 Home first", `app.py:10457`), but the 365-day track-record read (`app.py:10465`) carries no equivalent coverage disclosure — the same class of problem F-233 solved explicitly for the BUY side.

### Where the architecture is genuinely load-bearing

- **Constants-as-policy is real, not aspirational.** I mechanically diffed every constant value documented in `docs/architecture.md` §4.0.1 against `stock_analyzer/constants.py`: **231 rows checked, zero mismatches.** For a decision app whose docs carry threshold values, that is the single most valuable thing in this review to be able to say.
- **The multi-source provider layer does what it claims.** `stock_analyzer/providers/` (orchestrator + finnhub/yfinance/fmp), with a genuinely two-tier cross-check — strict on the settled prior close (`DATA_XCHECK_PREVCLOSE_TOL_PCT = 0.5`) and loose on the live price (`DATA_XCHECK_LIVE_TOL_PCT = 3.0`), `orchestrator.py:336,342` — and the result is persisted to `price_xcheck_history` (`db.py:524`), so a provider that drifts leaves a trail. The comment at `data.py:118-120` explaining why `prev_close` is never fabricated as `prev == price` ("would disarm the cross-check's strict leg") is the kind of reasoning that keeps a safety net a safety net.
- **`reference_shelf.py` is the best design in the app.** It answers one question — which hand-maintained table is overdue — and it derives every horizon *from the table itself* (`_macro_static_horizon`, `reference_shelf.py:69`), deliberately taking the **minimum over per-series maxima** rather than the global max, because "the series are extended independently … a global max would let one freshly-extended series mask five expiring ones." That is not hypothetical: I ran it, and the FOMC series (covered to 2027-12-08) does mask CPI/NFP/GDP (Oct–Dec 2026). The design anticipated its own failure mode and defeated it.
- **`None`-on-failure is honoured by producers.** `_div_recs_cache = None` on exception with the comment "offline sentinel, not `[]` — matches sibling cache contract" (`app.py:5054`); `_corr_cov = None` with "never a fabricated count" (`app.py:5049`). The contract is real where it is written.
- **Two exemplary honest-degradation surfaces.** The Watchlist detects offline coordination state and renders a banner *naming the gates that cannot run* (`app.py:22279-22296`). The Judge distinguishes "cache absent" from "cache empty" and says so — "ℹ️ Coherence audit unavailable this run — reduce-call data not …" (`app.py:10522-10526`). These are the patterns the rest of the app should copy; see Part 2 #2.
- **Catalyst Watch refuses to be misread.** Its footer states "The engine does not recommend buying into earnings; proximity gates remain active on Grow Today" (`app.py:30585-30590`) — a feature that surfaces names *because* earnings are near, explicitly telling you it is not the buy surface. That is OP-04 coordination done with a sentence instead of a gate, correctly.

### Real safety vs. ceremony

**Real:**

- The commit hook running the full `pytest tests/` suite and blocking on failure (`.claude/hooks/pre_tool_checks.py:449`) — 125 test files, 3,834 tests, 76% coverage of `stock_analyzer` (`docs/test-results.md` §1). This is the actual pre-deploy net, and CLAUDE.md is right to say so.
- The independence argument for the `reviewer` subagent. CLAUDE.md is candid that the citation format proves a string, not a subagent invocation. I'd go further in its favour: the *value* was never the citation — it's that the reviewer re-derives from the diff in a separate context. That is genuinely load-bearing and no hook could replace it.
- `check_antipatterns.py` being baseline-gated (41 baselined instances, `scripts/antipattern_baseline.json`) rather than all-or-nothing.

**Ceremony that looks like safety — say it plainly:**

1. **`app.py` is outside every meaningful gate.** It is 37,599 lines (44% of the repo's Python), it is excluded from coverage (`pytest --cov=stock_analyzer`, `docs/test-results.md:16`), no test imports it (only `py_compile`, `tests/test_repo_hygiene.py:37`), and it is **not in `_GATE_FILES`** (`.claude/hooks/pre_tool_checks.py:220-245`). Yet it holds: the G-18 stop-breach suppression (`app.py:20617-20630`), the reduce-call add gate (`app.py:20629`), all eight `NET_CAPITAL_POSITION_CAP_PCT` call sites (`app.py:19877, 20028, 20910, 22142, 22536, 22714, 23356, 29633`), and the concentration Sankey's gate-basis flags (`app.py:15738-15748`). A commit that changes any of those clears the full gate stack — pytest (doesn't cover it), antipatterns (syntax only), and the review gate (file not listed). The mandatory-review discipline is real for `stock_analyzer/`; for the render layer it is a habit, not a mechanism.

2. **Consumer-side truthiness silently repeals the producer-side `None` contract.** A scripted scan found **18 sites across 8 pages** reading a Home-published coordination cache with `or {}` / `or []` / a `.get(k, {})` default. Producers do the right thing and the consumers throw it away. Most are safe because the page also carries a hard `_render_portfolio_not_loaded` + `st.stop()` guard — but four are not (Part 2 #2), and the 2026-08-24 audit's own systemic note ("a deterministic AST gate catches syntax, not semantics") predicted exactly this. `app.py:15694`'s `if _sg_recs:` is a live instance: the producer sets `None` on failure two thousand lines earlier and the consumer folds it into the same branch as "checked, nothing to show."

3. **The `_STATIC` macro backbone is a hard gate resting on a hand-maintained table with 64 days left.** G-07 is real and it works — but only while the table has rows. See Part 2 #4.

### What I'd keep if the app were cut in half

`stock_analyzer/constants.py` plus the review discipline around it; the provider orchestrator and its cross-check; `reference_shelf.py`; the `recommendations` table **with its cron writer** (the only unattended evidence accrual in the system); and `daily_briefing.py`'s split-defensive gate architecture. Everything else is downstream of those five.

---

## Part 2 — Next nice-to-haves (ranked by decision-quality value ÷ blast radius)

### 1. Gate Suppression Ledger — record what the gates blocked *(multi-session; `db.py`/`cron_runner.py` ⇒ reviewer required)*

**What.** An append-only table capturing every suppression the Brief already computes: `(rec_date, ticker, gate_id, price_at_suppress, composite, sector, reason)`. No new computation is needed — `_grow_today` already returns `macro_blocked_picks`, `sector_blocked_picks`, `risk_blocked_adds`, `concentration_blocked_adds`, `sector_blocked_adds`, `deterioration_blocked_adds`, `cooldown_adds` (`daily_briefing.py:799-803, 1432-1438`). The cron already **counts two of them into a log line and discards the rest** (`headless_alert_engine.py:505-506`). The evidence is generated daily and thrown away daily.

**Why it matters to this owner.** OP-01 is "the app would rather recommend nothing than recommend wrongly." That is the product. On a 3.15× book, a systematically mis-set suppression is not a neutral non-event — it's the difference between the leverage working and not, compounded daily. The app grades its buys (F-229), its exits (F-229 addendum), the owner's own trades (F-233/F-256) and outside analysts (F-154c). It has never graded its own restraint. Note the second-order effect: `judgment_grading.py:19-37` refuses to grade protective dimensions precisely because it lacks counterfactual data — this ledger is the missing input.

**Touches.** `db.py` (new table + writer), `cron_runner.py` (write on the `scan` lane, where the ledger accrues unattended like `recommendations` does), `daily_briefing.py` (return shape already correct — probably zero change). All three are `_GATE_FILES` ⇒ Hard Rule #4 review.

**Rough cost.** 1–2 sessions for capture. The readout is a separate, later build (Part 3 #1) — do not bundle them.

**What breaks if it's wrong.** Nothing decision-side: it is write-only until a readout exists, it changes no threshold, and it structurally cannot manufacture a buy because it records only what was *not* recommended. The live risk is DB hygiene — get the dedup key right first time. The `account_flows` unbounded-reinsert bug (2026-08-24 audit Finding #1, fixed 2026-08-25) is the exact precedent: an unguarded `.insert()` on a re-scanned window. Use a `unique (ticker, rec_date, gate_id)` constraint from day one, mirroring `recommendations_unique_per_day` (`db.py:192`).

**Explicitly not proposed:** any change to any threshold. This makes the thresholds falsifiable; it does not touch them.

### 2. Make "not checked" visually distinct from "checked, nothing found" on four unguarded surfaces *(1-session polish)*

The app's own stated worst failure mode, present in four places. Two verified instances:

**(a) 🔔 Catalyst Watch → 🧭 Entry Candidates.** `_cw_composites = st.session_state.get("_grow_composites") or {}` (`app.py:30528`). `_grow_composites` is written only inside the Home block (`app.py:4922, 4951, 4975`). With it empty, `build_earnings_catalyst_candidates` filters every ticker on `bundle is None` (`earnings_advisor.py:580-581`) and the tab renders:

> *"No watchlist names currently pass all filters (beat rate ≥ 70%, composite ≥ 65, reaction not bearish, earnings within 30 days)."* — `app.py:30554-30557`

An itemised, quantified, confident negative for a check that never executed. The page **has** a `_render_portfolio_not_loaded` guard — but it is scoped to the 📋 Positions tab only (`app.py:30387`), two tabs away.

**(b) 📈 Analysis has no guard at all.** It reads `_port_df_enriched` six times and `_reduce_calls` twice, and appears in neither `_render_portfolio_not_loaded`'s 11 call sites nor `_render_portfolio_stale_banner`'s 9. The reachable trigger is a market-data outage on the first Home load: Home `st.stop()`s at `app.py:4401`, **before** `_port_df_enriched` is published at `app.py:4404`. Navigate to Analysis on that session and a held ticker resolves `_sa_holding = None` (`app.py:20307-20310`), so the "**Already held:** N shares · … Sizing below is for adding to your existing position" branch (`app.py:20714`) never renders, and `_under_reduce` is False (`app.py:20629-20630`). The page presents a clean **new-position** trade plan with sizing for a name you already own and may be under an active Reduce call. Note the mechanical stop-breach half (G-18) *is* immune — it recomputes live — so the exposure is the deterioration-EXIT-above-stop and risk-off-trim cases.

**Fix.** The pattern already exists twice in this codebase (Watchlist `app.py:22279-22296`; The Judge `app.py:10522-10526`). Distinguish key-absent from key-empty and render one line. Nothing else changes.

**Rough cost.** One session. **Blast radius: minimal** — pure additive banner, no gate, no threshold, no data path.

**What breaks if it's wrong.** A banner shows when it shouldn't. That is strictly better than the current failure direction.

### 3. Shrink the ungated render surface — extract `app.py`'s gate decisions into pure modules *(multi-session, incremental)*

Adding `app.py` wholesale to `_GATE_FILES` would force an Opus review on every UI tweak and would be abandoned within a week. The workable move is the one this project already validated: on 2026-08-25 the outage-gate decision logic was extracted from `app.py` into `db.classify_load_result` + `stock_analyzer/outage_gate.py::decide`, shipped as a byte-identical refactor with 12 new tests and an Opus SHIP/0-blocking (commit `1b12779`). Do that again for the render-layer gate decisions — the stop-breach/reduce-call precedence at `app.py:20617-20680`, and the F-255 capital-cap wiring. Each extraction is individually reviewable as behaviour-preserving, converts untested render code into tested pure functions, and moves the logic *into* `_GATE_FILES` where the review gate already applies.

**What breaks if it's wrong.** A botched extraction changes a live gate — which is why each one must ship as its own byte-identical refactor with the reviewer pass, exactly as `1b12779` did.

### 4. The macro gate's input expires in 64 days, and the gate fails open without saying so *(the fix is 1 session; the chore recurs)*

Verified by executing `reference_shelf.shelf_status()`:

```
macro_event_calendar | warn | covered through 2026-10-29 — 64d of runway
                              (want >=90d) — earliest series to run out: GDP Advance Estimate
```

Per-series maxima (from `macro_calendar._STATIC`): Growth/GDP **2026-10-29**, Employment/NFP 2026-12-04, Inflation/CPI 2026-12-09, Fed Policy 2027-12-08.

G-07 (`MACRO_IMMINENT_DAYS = 3`) is a **hard** suppression of new picks in a sector facing an imminent HIGH-impact release. It iterates `_STATIC` (`daily_briefing.py:723`, `macro_calendar.py:701`). When a series runs out, the loop finds nothing — Grow Today renders picks with no macro banner, which is byte-for-byte identical to "checked, nothing imminent." Projecting forward, the row goes `down` ("EXPIRED 2026-10-29") on 2026-10-30.

Two separable actions, and only the second is durable:

- **(i)** Extend the table. This is the chore, and it is the only thing that restores the gate. `docs/architecture.md`'s BEA/BLS 2027 dependency is already tracked.
- **(ii) Carry the shelf verdict to the decision surface.** The app already *knows* (`reference_shelf`), but it only tells 🩺 System Trust — an owner-only page. The Brief should say "macro proximity gate: input expired" where the suppression banner would otherwise be. This turns a recurring silent fail-open into a recurring visible one, permanently.

**Also surfaced by the same run:** `discovery_universe` last refreshed 2026-05-29 against a 90-day shelf life — it flips to `warn` within days (confirmed `warn` at 2026-09-05). CLAUDE.md's "🩺 check ⑤ went 3 amber → 1" is true today and stops being true this week.

**What breaks if it's wrong.** (ii) could over-warn if the horizon function is misread. It has tests and the horizon is derived, so the risk is low.

### 5. Counterfactual grading for protective calls *(multi-week; new policy constants; `planner` + `reviewer`)*

`judgment_grading.py:19-37` states the problem better than I can: naive sign-matching "marks the witness WRONG for every risk that correctly didn't fire," and the proper fix "needs counterfactual grading … which needs data this module doesn't have access to yet (what the user actually did in response)." Two of the three inputs already exist (`exit_signals` since 2026-07-21; `trades`). #1 supplies the third for the entry side. This is the natural follow-on to #1, not an independent idea, and it should not start before #1 has accrued real rows. It needs a policy conversation about what "correct caution" means before any code — which is precisely why it belongs at the bottom of this list rather than the top.

**Session-size separation, explicitly:**

- **1-session polish:** #2, and #4(ii).
- **1–2 sessions but reviewer-required (touches `_GATE_FILES`):** #1's capture half.
- **Multi-week, `planner` + `reviewer`, policy constants:** #3 (incremental), #5.

---

## Part 3 — Innovations

I generated five and am presenting three. **Rejected before presenting:** (a) a *leverage-conditional threshold translator* showing every gate's distance in net-capital terms — too adjacent to the option-(c) recalibration the owner explicitly parked on 2026-08-25, and F-254 already covers the concentration case, so it would re-litigate a settled decision to add a fourth view of a number already on screen; (b) a *weekly LLM self-audit lane* writing a critique of the engine's own week — cheap and unfalsifiable, and it invites the LLM to opine on decision quality, which is the "narrates, never originates" redline read in spirit rather than letter.

### 1. The Road Not Taken — a regret ledger for the app's own restraint

**Thesis.** Caution you cannot audit is faith, not discipline. The app suppresses picks every single day under G-01/04/07/09/16/20 and has no idea whether that was worth it. This is the readout that makes Part 2 #1 pay.

**Smallest honest version.** One read-only card. For suppressions ≥30 trading days old, show forward return vs SPY bucketed by `gate_id`, with a hard "N too small" band mirroring the existing evidence discipline (`ENGINE_TRACK_MIN_CALLS = 8` / `PROTECT_TRACK_FIRM_CALLS = 15`, `constants.py:1162-1163`). No verdict below the floor — the same "building / early / firm" banding F-229 already uses. Zero gate change; it reads a log.

**Falsifiable signal that it isn't working.** If after ~40 suppressions no `gate_id` shows a consistent sign — i.e. blocked picks are neither systematically better nor systematically worse than the tape — then the ledger is measuring noise, and the honest move is to retire the card rather than keep displaying a number that means nothing. Say that up front, in the plan, before building.

**Redline check.** Cannot manufacture a buy (records only non-recommendations), does not loosen a gate, awareness only.

### 2. Replay the exit ladder against the owner's own realized losses

**Thesis.** `ATR_STOP_MULT = 2.0` and the `DETERIORATION_*` ladder have never been tested against a loss this owner actually took. CLAUDE.md's own Forward-Simulator Phase-2 entry concedes the current trigger is "a weak proxy, not a decider" — it waits on `PROTECT_TRACK_MIN_CALLS = 8` matured calls, which "can only accumulate in the regime where the question matters least." Meanwhile the data to answer a sharper version of the question already exists: closed losing round trips in `trades`, plus price history.

**Smallest honest version.** One closed losing round trip, one ticker. Replay `exit_advisor.classify_deterioration_tier` day-by-day across the actual held window and print two dates: when each tier *would have* fired, and when the owner *actually* sold. That is a script and a table, not a page. `forward_sim.py` (F-245) already replays the owner's rules against a *hypothetical* shocked book; this replays them against *what really happened*, which is strictly more falsifiable.

**Falsifiable signal that it isn't working.** If across the closed losers the engine's would-have-fired date is **not** systematically earlier than the actual sell date, then the premise ("the ladder would have saved me, I sold too late") is false, the exit ladder is not the lever, and the whole Forward-Simulator Phase-2 branch should be closed rather than waited on.

**Redline check.** Read-only historical measurement. Touches no gate. If it later argues for a threshold change, that is a separate policy conversation with the owner — the replay produces evidence, not a recommendation.

**Honest caveat.** Sample size will be small and survivorship-shaped (it can only see positions that were closed). State the N and the selection effect on the output itself, or it will read as more than it is.

### 3. Surface proprioception — a 6th 🩺 System Trust check

**Thesis.** F-235 gave the app proprioception for its **pipelines** (did each cron lane fire, is each store fresh). It has none for its **surfaces** (did this card's inputs actually exist when it rendered). Part 2 #2 fixes four known instances by hand; this converts the whole class from a bug you hunt into a number you watch.

**Smallest honest version.** A tiny helper wrapping the 18 collapsed coordination-cache reads that records `(surface, cache_key, was_populated)` into session state, plus one line on 🩺 System Trust: "N decision surfaces rendered on unverified inputs this session." No behaviour change, no banner, no gate — just a count. It also gives the Part 2 #2 fix a regression detector, so the fifth instance announces itself instead of waiting for the next review.

**Falsifiable signal that it isn't working.** If across a month of ordinary use the counter never exceeds 0, the class is theoretical on this deployment — delete the instrumentation and close the concern. That is a real outcome, not a face-saving one.

**Redline check.** Pure observation; changes nothing about any decision.

---

## Defects flagged

Ordered by how likely each is to mislead a reader who trusts the doc. Every one was verified against HEAD.

**D1 — `docs/requirements.md:28` names the wrong primary deploy.** Reads "Web browser via Streamlit Community Cloud (primary) or the Railway Hobby pilot (since 2026-07-24, same Supabase DB)." Railway has been primary since the 2026-08-15 cutover and Streamlit is a dormant cold fallback (`docs/architecture.md:29`, CLAUDE.md:9). The functional spec is the outlier.

**D2 — `docs/user-manual.md:295` has the same inversion.** "Deployment: primary is **Streamlit Community Cloud** … A **Railway Hobby pilot** … runs in parallel." CLAUDE.md's pointer table calls this file the "Single portable map of the whole app," so the portable map tells a reader to verify against the dormant deploy.

**D3 — `docs/requirements.md` §3.11 still describes a GitHub Actions cron that no longer exists.** All superseded by the 2026-08-07 migration to Railway Cron Job services (`docs/architecture.md` §12.6 is correct and thorough):

- `:573` — "A **headless runtime** (GitHub Actions, no Streamlit)".
- `:580` F-143 — "GitHub cron is UTC with no DST, so each job is scheduled on TWO UTC slots"; also names only `alert_state` rows 1–2, while the code has six (`cron_runner.py:91-96`).
- `:583` F-146 — "two DST UTC slots … **mapped to mode=scan via `github.event.schedule`**". The mapping mechanism is gone; lanes resolve from `ALERT_RUN_MODE`.
- `:667` NF-30 — "The headless cron reads the same secrets from **GitHub repo secrets**". It reads Railway service variables.
- `:647` NF-17 — still calls Railway "the pilot" whose "system clock has not been independently verified."

**D4 — `docs/architecture.md:2577-2579` documents a constant at a value the code no longer holds, and an investigation that has since closed.** The ⚠️ block states `_Lane("broker", …, fire_hours_et=(14, 19))` and "the live cron expression **has not been read yet**" and "that expectation and reality currently disagree." Verified by executing `system_health._LANES`: broker is now `fire_hours_et=(12, 18)`, and the code's own comment records that the dashboard schedule **was** read on 2026-08-24 (`"0 16,21 * * *"`, "measured, not guessed"). Both halves of the warning are stale, and this is the precise class — *a constant documented at a value it no longer holds, plus a resolved blocker still described as open* — that the review brief asks to be hunted deliberately.

**D5 — CLAUDE.md undercounts the cron lanes.** Line 9 says "6 as of the 2026-08-15 cutover (premarket/scan/intraday/eod/weekly thesis/weekly maintenance)." Verified: **7** (`system_health._LANES` → `premarket, scan, intraday, eod, thesis, maintenance, broker`). The `broker` lane has been live since 2026-08-18 (F-244). CLAUDE.md loads into every session, so this propagates further than any other entry here.

**D6 — the in-app User Guide misstates the cron schedule on two counts.** `app.py:31741` says "**Four runs each trading day**" — the `broker` lane is a fifth, and it runs weekends too (`docs/architecture.md:2571-2573`: "no in-code guard at all"). `app.py:31751` says "**Two more runs, Sunday only**" — but `maintenance` fires **Saturday** (`cron_runner.py:1525`, `if not force and now_et.weekday() != 5`; `system_health._LANES` → `fire_weekday=5`). Only `thesis` is Sunday.

**D7 — a live cron log line points the operator at the wrong system.** `cron_runner.py:1050`: `"thesis: INERT — no ANTHROPIC_API_KEY set. Add it to GitHub secrets to activate."` Since 2026-08-07 that would mean editing a repo that no longer schedules anything. (Same residue, comment-only and therefore lower priority: `docs/architecture.md:2536`, `bundle_loader.py:167`, `db.py:324,890`, `headless_alert_engine.py:6`, `notify.py:4`.)

**D8 — F-255's hard cap is missing from both canonical policy tables in `docs/requirements.md`.** `NET_CAPITAL_POSITION_CAP_PCT = 25.0` is a hard suppression, not a soft adjustment — `sizing_unavailable_reason` returns `"capital"` and `position_sizing` returns `None`, producing *no size at all*, when the cap cannot afford one share or net capital is ≤ 0 (`risk.py:82-86`). It has a full F-row (`requirements.md:270`) and a correct constants-table row (`architecture.md:201`), but **no row in §2A.2's decision-thresholds table and no G-ID in §2A.3's "Hard gates currently enforced" table** (which still ends at G-21). A reader consulting §2A.3 to learn what suppresses would not find the app's newest suppression. Index gap, not a hallucination — but §2A.3 is exactly where someone looks.

**D9 — `app.py:15694` collapses an offline sentinel the producer deliberately set.** `if _sg_recs:` folds `None` (correlation computation failed, `app.py:5054`) into the same branch as `[]` (checked, no gaps), so 🧭 Sector Gaps vanishes with no banner. Same shape as the 2026-08-24 audit's Finding #2 at a different site, and invisible to `check_antipatterns.py` because it is a bare truthiness test rather than the `or {}` form the detector matches. This is the third consecutive review to find the detector narrower than the class it is named for.

**D10 — `app.py:22290-22296`'s Watchlist offline banner under-names what it disabled.** It says "sector-overlap and active-risk-alert gates" cannot run. But the sector **ceiling** check (G-05) also silently falls back to `_wl_sec_wt = 0.0` when `_port_df_enriched` is empty (`app.py:22275`, comment: "the sector check falls back to 0.0 (inert, safe)"). Fail-open is a defensible choice; naming sector *overlap* (W-04) while the sector *ceiling* (G-05) is the one quietly off is not. One-line copy fix. This is the app's best honest-degradation surface, which is why the imprecision is worth correcting rather than tolerating.

**D11 — `docs/test-results.md` is 4 days and roughly 20 commits behind.** §1 "Latest run — 2026-08-22 … 3,834 passed / 76%." F-249 Phase 2 validation, F-250, F-251, F-255, F-256, F-257, F-237e and F-22d/F-23a have shipped since. The commit hook ran the suite for every one of those, so the runs happened — only the log was not appended, against the file's own instruction ("appended each time the suite is run"). Low severity; noted because this log is the project's only coverage-trend signal and a gap hides drift.

---

## If you only do three things

1. **Fix the four "silence reads as fine" surfaces** (Part 2 #2). One session, near-zero blast radius, and it closes the app's own stated most-dangerous failure mode at the two verified sites — 🔔 Catalyst Watch → Entry Candidates confidently listing four filters it never applied (`app.py:30554`), and 📈 Analysis presenting new-position sizing for a name you hold (`app.py:20307-20310, 20714`). The pattern to copy already exists at `app.py:22279` and `app.py:10522`.

2. **Start the Gate Suppression Ledger** (Part 2 #1) — capture only, no readout, `unique (ticker, rec_date, gate_id)` from day one. The data is generated and discarded every day (`headless_alert_engine.py:505-506`). Every month you wait is a month of evidence about the app's central claim that you cannot get back, and it is the missing input that `judgment_grading.py:19-37` says blocks grading the protective side at all.

3. **Repair the doc drift cluster D1–D8 in one pass**, then extend the macro table (Part 2 #4(i)). D4 in particular is a constant documented at a value the code no longer holds — the exact class this project has been bitten by repeatedly — and D5 sits in the file loaded into every session. The macro calendar has **64 days of runway** on a hard gate that fails open silently; that is a calendar deadline, not a backlog item.
