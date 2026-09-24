# DRISHTA — Product & Intelligence Review

*2026-09-24 · Senior product-strategist / UX-architect / investment-intelligence pass. Grounded in code: every claim carries a `file:line` or F-ID, and every consequential claim was re-verified by the lead directly against HEAD rather than taken on a research pass's word. No code was written or modified.*

> **Context.** The 2026-09-21 app-review and UX-review are **both fully closed** — every finding in each was fixed the same day. This review deliberately does not re-serve them, and does not re-propose anything from CLAUDE.md's "Genuinely not yet done" without engaging the recorded reasoning. Where it touches a parked or declined item, it says so explicitly.

---

## Verdict

DRISHTA is a genuinely strong decision engine wearing an increasingly incoherent measurement layer. The daily loop — Home → Act Today → Grow Today → Review — is tight, opinionated, and does exactly what the vision asks: it decides. The **offense and defense** halves of the vision are largely solved. What has drifted is the **"become a more disciplined investor over time"** half: **16 distinct retrospective surfaces** now span 4 nav groups, measuring overlapping questions with inconsistent windows, floors, and vocabularies — including one confirmed pair that grades the same exit episodes with **exactly opposite sign conventions** and never mentions each other. Meanwhile the single most consequential number the app has ever produced about its owner (Benchmark Mirror) is imported once, on tab 1 of 6 of a page nothing links to. Most urgently, three places on the daily path — including the **morning buy-recommendation email** — can render a confident all-clear built on a silently swallowed failure. The app is not short of intelligence; it is short of one place where its intelligence agrees with itself, and short of a reliable way to tell "checked, clear" from "never checked."

---

## 1 · Vision alignment

| Vision pillar | Verdict | Evidence |
|---|---|---|
| Turn data into **actionable intelligence** | **Strong** | 24 hard gates (`requirements.md` §2A.3); Analysis lands a literal call — `app.py:24738` renders `Buy {shares} @ ${lo}–${hi} · Stop ${stop} · Target ${base} · R:R {rr}:1` |
| **Identify opportunities** | **Strong** | Grow Today → Watchlist → Scanner funnel, tone- and gate-aware; every suppression surfaces a banner rather than silently filtering |
| **Manage portfolio risk** | **Strong** | Deterioration ladder, concentration gates, margin awareness (F-253/254/255), the `None`-on-failure contract |
| **Recognize trends & behavioral patterns over time** | **Weak — the central gap** | 16 overlapping measurement surfaces, 5 confusion clusters, no shared home, no cadence, one self-contradiction |
| **Support better decisions** | **Mixed** | 13 of 28 pages dead-end; the four pages generating the most judgment have **zero** outbound links |

**Honest headline:** the app has solved *"what should I do today."* It has **not** solved *"am I getting better, and how would I know"* — the pillar its own vision names, and the one it currently answers five different ways at once.

**Nothing here argues the app is over-built for its purpose.** Breadth is not the problem; 28 pages with a documented "only when you want depth" posture (`user-manual.md` §I.2) is a legitimate design. The problem is that the *learning* layer was never given the same editorial discipline as the *deciding* layer.

---

## 2 · Most important issues

Ranked by consequence, before the prioritised recommendations below.

1. **A swallowed failure can render as a confident green all-clear on three daily surfaces — one of which is an email that recommends buying.** (→ Top-5 #1)
2. **The app's broadest self-measurement — is the whole active strategy beating passive? — is invisible on both daily surfaces.** (→ Top-5 #2)
3. **The exit-side track record contradicts itself in opposite sign conventions across two pages.** This has already misled the project's own tooling once, on record. (→ Top-5 #3)
4. **The learning layer has no home, no cadence, and five overlapping question-clusters.** (→ Top-5 #4)
5. **Half the app dead-ends**, including every page that produces judgment but declines to issue a call. (→ Top-5 #5)
6. **Pervasive doc-count drift** — the standalone user manual is a page short and cites three different page counts; the in-app guide hides a whole shipped tab. (→ Defects)

---

## 3 · What's genuinely working

**Keep untouched if the app had to be cut in half:**

1. **`constants.py` as investment policy.** Every threshold named, documented, CI-enforced (`scripts/check_constants_documented.py`). A full programmatic diff of **278 numeric constant rows in `docs/architecture.md` and 23 in `docs/user-manual.md` found zero mismatches** — the constants tables are genuinely clean. The `POLICY_DECISION_IN_RENDER` ratchet keeps the gradient pointing toward extraction. This is the most load-bearing thing in the repo.
2. **The deterministic commit gates** — full pytest + antipattern + constants-doc, blocking on push. Per CLAUDE.md's own candour, the Opus-review *citation* check proves a string exists, not that a reviewer ran. **The pytest gate is real safety; the citation gate is an honesty mechanism with an audit trail.** Both are labelled as such in the project's own docs, which is rarer than it sounds. (§4 of Defects notes where the antipattern gate's coverage is now narrower than its green exit implies.)
3. **Pre-registered retirement criteria.** The Gate Suppression Ledger and Recommendation Outcomes each pre-committed, *before any data existed*, to retiring themselves if they produce no distinguishable verdict in 12 months (`app.py:32502`, `:32673`). Almost nobody does this. Do not soften it later.
4. **The `None`-on-failure contract where it was hand-applied** — `util.get_or_offline`, `risk_off_state()` (F-271), `factor_tilt_state`, the three-state `_structural_alert_cache`, `_broker_drift_cache`. The project repeatedly catches itself collapsing a sentinel and fixes the *class*. Top-5 #1 is about the sites that pattern hasn't reached yet.
5. **The canonical call shape.** `app.py:24738` — shares, entry zone, stop, target, R:R, one line. Every surface should aspire to it.
6. **📈 Performance Review as a coordination pattern.** It deliberately shows *counts, not verdicts*, for the two ledgers so the surfaces "can never disagree" (`app.py:37407`). This is the right generalisation for the whole learning layer and it already exists in-house.

**Closed loops that genuinely close** (decide → capture → measure → feed back): the BUY loop (`recommendations` → `recommendations_history.compute_outcomes` → Engine Track Record); the gate-suppression loop (F-259/F-259b); the risk-recommendation loop (F-273b/c).

**One surface is quietly unlike its siblings.** 🧑‍⚖️ The Judge's witness track record (`judgment_grading.track_record_summary`, fed to `synthesize()` at `app.py:11237`) is the **only** retrospective measurement that feeds back into live output — it re-weights witnesses. The other 15 are redline-awareness-only. The page badge says "NEVER GATES A RECOMMENDATION" (`app.py:11148`), true of the Judge's *output* but obscuring that a track record shapes its *input*. Worth one clarifying sentence.

---

## 4 · Top 5 next improvements

> Ranked by **value to decision quality**, not ease. Cheapness is noted, never rewarded.

---

### #1 — Close the "fabricated all-clear" class on the daily path

**What is wrong today.** In several places the app cannot distinguish *"checked, nothing found"* from *"never checked"*, and renders the reassuring one. Four confirmed instances, none of them baselined in `scripts/antipattern_baseline.json`:

- **The morning buy-list email silently drops its exit warnings.** `cron_runner.py:1550-1560`. The comment at `:1548-1549` states the purpose outright — *"so the email can warn the user to handle exits before entering any new position."* But it calls `db.load_exit_signals()`, whose own docstring (`db.py:3405-3421`) says it returns **an empty DataFrame both on exception and when `not has_db()`**; the `except` at `:1559` then logs to the cron log and leaves `exit_alerts = []`. **On a DB blip the owner receives a morning email recommending new positions with the "handle your exits first" block simply absent**, and the only trace is a log they never read. A safe sibling — `db.load_exit_signals_or_none()` (`db.py:3424`) — already exists and is already used at `app.py:40992` and `:43692`.
- **🧾 Summary paints a green all-clear on a failed earnings lookup.** `app.py:12239-12241` (`except: _sm_n_earnings_soon = 0`) → `app.py:12816-12817` renders **`"None soon"` in green `#22c55e`**. The block's own comment (`:12208-12210`) asserts it "Always shows a state … even when the answer is 'none soon'" — which is precisely the fabricated negative OP-03 forbids.
- **A producer writes a fabricated zero into a coordination cache.** `app.py:4113-4120`: on exception, `_playbook = []`, and `st.session_state["_earnings_posture_alerts_cache"]` is then set to `0`. Because the fabrication happens **producer-side**, no downstream consumer can recover the offline state however carefully it reads. The values suppressed are literally `EXIT` and `REDUCE` actions.
- **The Signals & Advice nav badge has no offline state, three lines from a sibling that does.** `app.py:2971-2973` correctly gives Catalyst Watch a grey `● ?` for `_risk_high_alerts_cache is None`; `app.py:2974-2976` then reads `_n_danger_cache`/`_n_warning_cache` with `or 0`, and the branch at `:3101` renders no dot at all when they're absent.

**Why it matters.** This is the app's own self-declared most dangerous failure mode, and OP-03 (`requirements.md` §2A.1) commits to the opposite: *"Data integrity failures fail loud… Fabricated fallbacks are never used."* The email instance is the most serious thing in this review — it is the one surface that **actively recommends deploying capital**, and the protective counterweight is the part that disappears.

**Related, but a different mechanism — worth pairing in the same conversation.** The deterioration ladder is **structurally dormant across the owner's actual holding window**, and only the per-position badge discloses it. `exit_advisor.py:199` and `:204` both read `return None if in_settling else TRIM` / `else WATCH`, where `in_settling = age_days < POSITION_SETTLING_DAYS` and `POSITION_SETTLING_DAYS = 10` (`constants.py:442`) — so **TRIM and WATCH, the entire early-warning half of the ladder, are silenced for a position's first 10 days**; only a deep EXIT survives (`:194`, correctly). Set against the app's own measurements — Roadmap **A4**'s ~7-day median hold over 139 closed round trips, and **W6**'s finding that **52% of closed losing round trips got no protective signal at all** and another 24% fired same-day only — those are three views of one fact: *for the median position, the early-warning ladder cannot fire before the position is sold.* The 🌱 Settling badge (`app.py:37009`) discloses the mechanism locally; **the aggregate consequence appears nowhere in the app**, only in `docs/plans/` and memory.

**What I recommend.**
- *Correctness half (a bug fix, not a design change):* migrate the four sites onto the safe idioms that already exist — `load_exit_signals_or_none()`, `util.get_or_offline`, an explicit `is None` branch — and give each a visible "not checked" state. The email is the priority. **Fix the class, not the four symptoms** (`feedback_upstream_downstream_impact_analysis`).
- *Disclosure half (needs a `planner` pass):* say what the settling badge implies — *"TRIM/WATCH held back — settling, N days remaining"* — using the state `position_lifecycle.classify_position_state` already computes; and surface the aggregate once, from the hold-time stats **already rendered** at `app.py:27664-27678`. **I am explicitly not proposing a change to `POSITION_SETTLING_DAYS`, the ladder floors, or the confirmation window** — those are investment-policy decisions, and CLAUDE.md's 2026-09-20 conclusion not to retune the ladder on current evidence stands.

**What the user should experience afterward.** Silence becomes readable. A quiet email or a green card means the app looked and found nothing — and when it couldn't look, it says so, in the same place it would have said everything's fine.

---

### #2 — Put the app's broadest self-measurement where decisions actually get made

**What is wrong today.** `stock_analyzer/benchmark_mirror.py` — which compares the owner's actual money-weighted returns against a shadow SPY/QQQ portfolio fed by the same cash flows — is imported **exactly once in the whole app**, at `app.py:41247`, inside 🎯 My Edge. Verified: **zero** occurrences anywhere in Home (`4319-11142`) or Summary (`11490-13304`). Summary carries 7 outbound pointers (→ Home, AI Insights, Catalyst Watch, Intelligence, Rec History, Risk Analysis, The Judge) and **My Edge is not among them**. Per CLAUDE.md:153 this surface currently reports the portfolio at **−40.3%/ann vs SPY +17.8%/ann over 87 days, beta-adjusted alpha −141.2pp, ~$1,113 behind the shadow counterfactual**.

**Why it matters.** For a retail investor, *"am I beating the thing I could have bought instead of doing any of this?"* strictly dominates every other measurement in the app. If the answer is durably no, nothing on the other 27 pages changes the right action. **The project has already accepted this principle**: F-277 (2026-09-20) put the Defense facet's −15.7% finding onto Summary's Act Today, and the 2026-09-21 review *widened* that disclosure rather than narrowing it. Benchmark Mirror's finding is broader in scope and larger in magnitude, and got neither.

**What I recommend.** Reuse the **existing F-277 disclosure pattern** — do not build a new mechanism. One standing line on 🧾 Summary reading the already-computed result, with F-277's established guards: render only on a same-day-fresh computation, state the measured number and its window, never editorialise, skip silently rather than show a stale verdict. Add My Edge to Summary's existing pointer set. **Carry the caveats onto the surface, not just into the docs:** 87 days is a short window, the book runs ~3.15× leverage, and the figure is money-weighted against a shadow portfolio — the window must be stated inline so the number cannot read as a settled verdict.

**What the user should experience afterward.** The daily surface stops being able to imply *"you're fine"* while the app's own broadest measurement says otherwise. The passive-alternative comparison appears at the cadence it matters — continuously — instead of only when the owner remembers to open tab 1 of an unlinked page.

---

### #3 — Reconcile the exit-side track record with itself

**What is wrong today.** Two surfaces grade substantially **the same ~17–19 protective exit episodes** using arithmetic that is the exact negation of the other:

| Surface | Formula | Sign meaning | Current reading |
|---|---|---|---|
| **Defense facet** (🧾 Summary card) | `protective_track_record.py:10-11` — `protect_alpha = spy_return − name_return` | **Positive = engine was right** | **−15.7%** → *the engine's exits destroyed value* |
| **🧭 Self vs Engine → Sell-Side** (🎯 My Edge) | `app.py:43673-43676` — `alpha = stock_return − spy_return` | **Negative = good exit** (stated on screen) | engine-called **+14.4pp** vs self **+5.6pp** → *the engine's exits beat yours* |

Same episodes. Opposite polarity. **Neither page names the other.** The Sell-Side tab spells out its sign convention explicitly; the Defense card never discloses that its convention is inverted. The buy-side sub-tab *does* carry a careful disambiguation paragraph (`app.py:43411-43418`) — but it disambiguates against the Engine Track Record card, so **the collision that got documented is not the one that actually bites.** The Defense card itself confesses the structural asymmetry: `app.py:12774` — *"Defense has no detail page yet."*

**This has already caused a real error, in this project, on record.** CLAUDE.md:153 documents an Opus `planner` pass treating the sell-side number as *independent corroboration* of the Defense finding, then being corrected to "non-independent (same episodes as A1, different angle)." If the project's own tooling misread it, a human reading two pages weeks apart certainly will.

**Why it matters.** This is the app's **trust instrument** — what the owner uses to calibrate how much authority to grant the engine's protective calls. Two opposite readings of one body of evidence is strictly worse than no measurement, because it lets whoever is reading pick the number that suits the decision they already wanted.

**What I recommend.** Pick **one** sign convention for "did a protective call work," state it identically in both places, and have each surface name the other plus the population overlap (*"these grade the same exit episodes from different angles; overlap n = N"*). Then resolve the asymmetry `app.py:12774` already admits: either give Defense the detail page it lacks, or fold Defense into Sell-Side as the single canonical home for exit quality. **No threshold changes and no new measurement** — this is reconciliation of two existing readouts.

**What the user should experience afterward.** One question — *"are my exits any good, and are the engine's better than mine?"* — with one answer, one sign, in one place.

---

### #4 — Give the learning layer a home, a cadence, and one answer per question

**What is wrong today.** **16 distinct retrospective surfaces** spread across **4 of the 5 nav groups** with no group of their own: RESEARCH holds Predictive Analytics, Model Lab, The Road Not Taken, Recommendation Outcomes (`app.py:3004-3010`); PORTFOLIO holds My Edge, Trade Review, Recommendations History (`:3015-3022`); MAIN holds Summary's Engine Track Record card; AI Insights holds the Research Scorecard. Five confusion clusters:

- **Cluster 1 — "did the engine's BUY calls work?" answered five times off one substrate, with four different default windows.** Summary card is all-time (`app.py:12271`, `date(2020,1,1)`); 📜 Recommendations History defaults to **Last 30 days** (`app.py:29834`, `index=1`); Predictive Analytics is all-time; the Monthly report is a trailing 28 days. The duplication is already *known* — `app.py:12249-12255` documents a shared price-cache key whose entire purpose is to stop the card and Rec History disagreeing. That workaround is the tell.
- **Cluster 3 — 🛑 The Road Not Taken and 🎯 Recommendation Outcomes are near-identical twins.** Verbatim-identical header captions (`app.py:32358` vs `:32515`), deliberately-identical "expect Building" copy, identical 8/15/5/30 floors in separate constant families. The real distinction — *a call the app suppressed* vs *a call the app made* — is stated on neither page, and neither links to the other.
- **Cluster 4 — three adjacent My Edge tabs grade the same closed trades at floors of 2, 8 and 10.** `DECISION_QUALITY_MIN_TRADES = 2` (`constants.py:1399`), `BEHAVIORAL_MIN_SAMPLE_N = 8` (`:1432`), `INVESTOR_MIRROR_MIN_CLOSED_LOTS = 10` (`:1475`). A user tabbing across sees one card render a grade on 2 trades while its neighbour says "need ≥10" about substantially the same population, with nothing explaining why.
- Plus **Cluster 2** (three separate classifiers for "was my instinct better than the app's" — Trade Review's self-reported `followed_signal` flag, Self-vs-Engine's independent re-derivation, and Behavioral Fingerprint's acted-vs-skipped cut) and **Cluster 5** (the sign collision, promoted to #3).

Naming reinforces the blur: **"Decision Quality" is a tab name twice** — `app.py:30840` (⚖️, Predictive Analytics: acted-vs-missed alpha by score band) and `app.py:41369` (📅, My Edge: monthly letter grades) — different questions, same name. **🎯 labels two simultaneously-visible nav entries** (`app.py:3010` Recommendation Outcomes, `:3017` My Edge). **🪞 labels both** the Trade Review page and the Investor Mirror tab.

**Why it matters.** The vision's fourth pillar is *recognizing trends and behavioral patterns over time*. That pillar currently costs the owner a scavenger hunt across four nav groups and rewards it with numbers using different windows, floors and vocabularies. Measurement the user can't reconcile isn't discipline — it's noise wearing a lab coat.

**What I recommend** — information architecture, not new features:
1. **A dedicated `LEARN` / `TRACK RECORD` nav group** collecting the retrospective surfaces out of RESEARCH and PORTFOLIO. Pure `_NAV_GROUPS` restructuring at `app.py:2992-3032`; the group machinery, accents and icons already exist.
2. **One canonical surface per question.** Cluster 1 gets a single owner; the rest become evidence views adopting **Performance Review's already-proven pattern** — *counts, not verdicts*, explicitly so surfaces "can never disagree" (`app.py:37407`).
3. **Align the default windows** across Cluster 1, or state the window inline on every figure.
4. **Fix the collisions** — rename one "Decision Quality"; de-duplicate 🎯 and 🪞.
5. Give the Cluster-3 twins one sentence each naming the other and the suppressed-vs-made distinction.

**On the adjacent parked idea — stated plainly.** The 2026-09-21 review proposed *pushing* ledger verdicts into the Weekly Debrief email, and the owner **parked it because the real scope was bigger than estimated.** This is the *pull* counterpart — findability and reconciliation, no email, no new computation — and is untried. It should not be read as a re-proposal of the parked item.

**What the user should experience afterward.** One question → one place → one number, with a defined rhythm for reviewing it, instead of sixteen surfaces and a memory test.

---

### #5 — Stop dead-ending the user on the pages that produce the most judgment

**What is wrong today.** The app's only page-jump mechanism is `st.session_state["_pending_page"]` (no `st.page_link`/`switch_page` exists anywhere). Outbound pointers per page range, counted directly:

| Page | Outbound `_pending_page` |
|---|---|
| 🧾 Summary (`11490-13304`) | **7** |
| 🧩 Intelligence (`16968-17797`) | **0** |
| 🏆 Health (`20332-20995`) | **0** |
| 📊 Predictive Analytics (`30439-31994`) | **0** |
| 🧑‍⚖️ The Judge (`11143-11489`) | **0** |

**13 of 28 pages have no outbound pointer at all.** Worse, several *name their destination in prose and leave it unclickable*: `stock_analyzer/portfolio_health.py:268-271` renders *"trim before adding new exposure. **Check the concentration section on Portfolio Overview**"* — as plain text. 🧩 Intelligence computes concentration, volatility attribution and thesis disagreement across the whole book, states *"Diagnostic only — composite score … and your own judgment still decide any action"* (`app.py:16973`), then ends on bull/bear chips with nowhere to go. 📊 Predictive Analytics runs `synthesize_directives` and produces genuinely action-typed directives — e.g. *"Treat signals below {thresh} as speculative — consider reducing size or skipping"* (`predictive_analytics.py:348-351`) — then offers no way to act on one.

**Why it matters.** This is the user's own "where do I go next," unanswered on exactly the pages where an answer is most valuable: the ones that produce a judgment but, by charter, decline to issue the call. "Diagnostic only" is a legitimate posture; "diagnostic only, and also you're stranded" is not.

**What I recommend.** Make the prose destinations clickable. Every one of these sites already names its target in text; `_pending_page` makes each a ~3-line change and Summary is the proven in-house pattern. Prioritise the four zero-link judgment pages plus the `portfolio_health.py` improvement copy. **Cheapest item in this review by a wide margin** — ranked last only because the user asked for value ordering, not because it should wait.

**What the user should experience afterward.** The chain *what needs attention → why it matters → what to do → where* completes on every page, instead of terminating in a chart on roughly half of them.

---

## 5 · Innovations worth considering

*Two weaker candidates were rejected before writing: a natural-language "portfolio health narrative" (🔎 Investigator and 💬 Ask already cover it and it originates nothing new), and a mobile/push surface (no evidence of need; a single user on Railway Hobby doesn't justify it).*

### A. A conviction ledger — grade the one decision the app never grades

**Thesis.** The app grades its BUY calls, its gates, its trims and its exits. The one decision it never grades is **position size** — yet A4's ~7-day median hold and ~3.15× leverage make sizing, not selection, a plausible location for where the real money moves. Everything needed is already captured: `rec_sizing_version`, the F-255 cap, `risk.position_sizing`, and realized per-lot outcomes.

**Smallest honest version.** One retrospective card: for closed round trips, bucket by *suggested size vs size actually taken* (undersized / matched / oversized) and show realized alpha per bucket. No new capture, no gate, no new constant beyond a sample floor mirroring the existing families.

**Falsifiable signal it isn't working.** If after ~6 months the three buckets are statistically indistinguishable, sizing discipline isn't the lever and the card retires — the same pre-registered-retirement discipline the Gate Ledger already models.

### B. Reconciliation-at-render — make disagreement a first-class, visible state

**Thesis.** Top-5 #3 isn't a one-off bug; it's what happens when N surfaces derive overlapping conclusions with no shared contract. The Judge already reconciles witnesses *within* a day. Nothing reconciles *across* the retrospective layer.

**Smallest honest version.** Do **not** build a framework. Take the single confirmed collision (Defense facet vs Self-vs-Engine sell-side), define one shared result shape — `{question, population, n, window, sign_convention, value}` — and render both surfaces from it. One collision, two call sites.

**Falsifiable signal it isn't working.** If a third surface later answers the same question without adopting the shape, the contract didn't hold, and the real fix is mechanical (an antipattern rule naming the documented measurement functions) rather than a shared module nobody is obliged to use. This is the same falsification the 2026-09-21 review applied to its own coordination-helper idea — which the owner then declined in favour of simply fixing the bugs. That precedent deserves respect here.

---

## 6 · Defects flagged

Separate from the opportunities above. All confirmed against HEAD.

**Doc / count drift**

1. **`docs/user-manual.md` cites three different page counts and omits a page.** Lines **22** and **90** claim *"all 23 pages"*; the tour body describes **27**; the app has **28** (`_NAV_GROUPS`, `app.py:2993-3031`; 28 dispatch branches, `app.py:4319`–`41240`). The missing page is **🎯 Recommendation Outcomes** — present in nav (`app.py:3010`), in `docs/architecture.md:3054`, and in the in-app guide (`app.py:37439`), but with **zero** occurrences in the standalone manual. The manual bills itself as "the single portable map," so an omitted owner-only measurement page is exactly what then goes unvisited.
2. **In-app User Guide hides a whole shipped surface.** `app.py:37426` describes 🎯 My Edge as *"five retrospective-only tabs"* and enumerates five; `app.py:41366-41373` declares **six** — the unlisted one is **🧭 Self vs Engine** (F-233), which is also half of Top-5 #3. `docs/user-manual.md:132` correctly says six. *This is the only user-facing item in this group.*
3. **`docs/architecture.md:3387` says "6 cron lanes"**; `system_health._LANES` (`system_health.py:219-258`) has **7**, and the same document's own §12.6 table (`:3740-3748`) lists all 7. The doc contradicts both the code and itself.
4. **`docs/user-manual.md:295`** cites *"30 tables (`architecture.md §6.1–6.30`)"*; `docs/architecture.md` defines **49** table sections, through `### 6.49 rec_events` at `:2901`.
5. **In-app glossary uses the retired pillar name.** `app.py:37758` — *"Composite … (Technical + **Fundamental** + Valuation + Sentiment)"*. `constants.py:780-791` names the pillar **`business_quality`** and records "fundamental" as the version-1 name **retired 2026-07-08**. The same guide's own scoring expander correctly says "Business Quality — 35%".
6. **`CLAUDE.md:79` line count stale** — states 43,729; actual **43,889**. The file itself instructs re-verifying before citing.
7. **`docs/user-manual.md` RESEARCH order contradicts its own "in nav order" promise** — App Settings (`:122`) precedes System Trust (`:124`); `app.py:3010-3011` has the reverse.

**Code**

8. **Four fabricated-all-clear sites** — detailed as Top-5 #1 (`cron_runner.py:1550-1560`; `app.py:12239-12241`→`:12816`; `app.py:4113-4120`; `app.py:2974-2976`→`:3101`). None is baselined.
9. **The antipattern gate's green exit currently proves less than it appears.** `OFFLINE_SENTINEL_COLLAPSE` matches `<attr>.get(...) or []` but **not** a bare *call* result on the left (`fetch_earnings_calendar(...) or []` at `app.py:3586`, `:3622`; `db.load_alert_state(...) or {}` at `cron_runner.py:320, 700, 1568, 1623, 1737`), and no rule covers `except: pass`. Neither `_earnings_posture_alerts_cache`, `_n_danger_cache` nor `_n_warning_cache` is in `_SENTINEL_KEYS`. Widening the rule to any Call on the left of `or []`/`or {}` would catch the 7 above at a cost of ~10 new baseline entries. *Framed as a question, not a threshold proposal — this is a gate-coverage decision.*
10. **Silent failure on The Judge's track record.** `app.py:11234-11235`: `except Exception: _jg_track_record_map = {}`. An empty map is the *same* input `synthesize()` receives in the legitimate pre-sample state, which the page caption (`:11153-11158`) explains as "every witness stays at equal, neutral weight" — so a DB failure renders identically to "not enough data yet." Consequence is bounded (the Judge gates nothing), and this shape isn't caught by rule 9's pattern either.
11. **A per-ticker Watchlist crash silently deletes that ticker.** `app.py:24971-24974` (`except: pass`); the guard at `:24976` only fires when *all* tickers fail. A partial failure drops a name with no counter and no caption — on the surface that issues `ENTER_NOW`.
12. **`ENTRY_TIMING_*` constants are self-declared provisional** (`constants.py:1366-1371`, *"fit to a single AMD anecdote"*) yet drive a user-visible tab on 📊 Predictive Analytics. **A question, not a recommendation:** should a provisional-by-its-own-comment band render user-facing divergence verdicts, or carry its provenance caveat on screen the way the factor-tilt line now does?
13. **Naming collisions** (detail in Top-5 #4): duplicate "Decision Quality" tab name (`app.py:30840`, `:41369`); 🎯 on two simultaneously-visible nav entries (`:3010`, `:3017`); 🪞 on both Trade Review and Investor Mirror.

**Verified clean — worth recording so it isn't re-hunted:** all 278 `docs/architecture.md` constant rows and 23 `docs/user-manual.md` §III.1 rows match `constants.py` exactly; every `docs/user-manual.md` tab count matches its real `st.tabs()` call; the System Trust check count (6) is correct in both docs and code; the cron-job table matches `cron_runner.py`; and every F-ID from F-270 upward holds against the code.

**Unverified, needs a live DB check:** the curated-universe size — `docs/user-manual.md:102` says *"88 tickers across 14 sectors"*, `app.py:37132` says *"~90 curated names"* for the same roster. Since F-262 Commit 3 deleted the in-code dicts, only Supabase can settle it.

---

## 7 · If you only do three things

1. **Fix the morning email's silent exit-section drop** (`cron_runner.py:1550-1560` → `load_exit_signals_or_none()`), then the other three fabricated-all-clear sites as one class. This is the only finding here where the current behaviour can actively cost money, and the safe helper already exists.
2. **Reconcile the two exit-side track records** into one sign convention with mutual cross-references, and resolve the "Defense has no detail page yet" asymmetry the code confesses at `app.py:12774`. This is the one place where the app's current state is worse than having no measurement at all.
3. **Put Benchmark Mirror's standing result on 🧾 Summary**, reusing the F-277 pattern with its caveats inline, and add My Edge to Summary's pointer set. Highest value-per-unit-of-work in the review: the mechanism exists and the principle is already accepted.

*Top-5 #4 (learning-layer IA) and #5 (dead-end pages) are both worth doing; #5 is nearly free and can ride along with anything. The settling-window disclosure folded into #1 needs its own `planner` pass — it changes how a protective surface describes itself, and the underlying ladder thresholds must stay untouched.*

---

## 8 · Prioritised backlog

*Added 2026-09-24 after the review. Priority = risk x user impact, NOT effort. "Gated" = touches `_GATE_FILES` ([pre_tool_checks.py:280-311](../../.claude/hooks/pre_tool_checks.py)) and therefore requires the mandatory Opus `reviewer` citation. Effort is a rough order of magnitude, not a commitment.*

### P0 — fix now (can cost money today)

| ID | Item | Where | Effort | Gated? |
|---|---|---|---|---|
| **A1** | ✅ **DONE 2026-09-24.** Morning buy-list email silently drops its exit-warning section on any DB hiccup. Swapped to the existing `load_exit_signals_or_none()`; added an explicit "COULD NOT CHECK FOR EXITS TODAY" banner (`notify._exit_check_unavailable_banner`), folded the check-failed state into the dedup fingerprint so a newly-failing check always re-fires. Opus `reviewer` (Opus 4.8): SHIP, 0 blocking. 4 non-blocking notes — 1 fixed same commit (signal_type column guard symmetry), 3 queued below as **J1/J2** (same bug class on other emails) and noted (harmless one-time fingerprint-shape re-fire on deploy). 13 new tests. | `cron_runner.py`, `stock_analyzer/notify.py` | S | Yes — reviewed |

Only item in this tier. It is the one place where current behaviour actively recommends deploying capital while suppressing the protective counterweight, with no user-visible trace.

### P1 — high (removes a live false-reassurance or a self-contradiction)

| ID | Item | Where | Effort | Gated? |
|---|---|---|---|---|
| **A2** | 🧾 Summary paints green "None soon" when the earnings lookup threw. Add a third "not checked" state. | `app.py:12239-12241` → `:12816` | S | No |
| **A3** | Earnings-playbook producer writes a fabricated `0` into `_earnings_posture_alerts_cache` — producer-side, so no consumer can recover the offline state. | `app.py:4113-4120` | S | No |
| **A4** | Signals & Advice nav badge has no offline state, 3 lines below a sibling that has one. | `app.py:2974-2976` → `:3101` | XS | No |
| **B1** | Reconcile the two exit-side track records onto one sign convention; cross-reference each from the other with the population overlap. | `app.py:12768-12774`, `:43673-43676`, `protective_track_record.py:10-11` | M | No |
| **B2** | Resolve "Defense has no detail page yet" — give it a page, or fold Defense into Self-vs-Engine sell-side as the canonical exit-quality home. | `app.py:12774` | M | No |
| **C1** | Put Benchmark Mirror's standing result on 🧾 Summary via the F-277 pattern, caveats inline; add 🎯 My Edge to Summary's pointer set. | `app.py` (Summary zone), `benchmark_mirror.py` | M | No |

**Bundle A2+A3+A4 into one commit** — they are one bug class, and the project's own `feedback_upstream_downstream_impact_analysis` calls for fixing the class rather than the symptoms. **B1 is blocked on an owner decision** (which sign convention wins).

### P2 — medium (real value; needs a design pass or is broader in scope)

| ID | Item | Where | Effort | Gated? |
|---|---|---|---|---|
| **D1** | Disclose settling-grace suppression per position ("TRIM/WATCH held back — N days remaining") using the state `position_lifecycle` already computes. **No threshold change.** | `app.py` (Act Today / position cards) | M | No — but wants a `planner` pass |
| **D2** | Surface the aggregate: share of closed positions sold inside the settling window, from the hold-stats already rendered. | `app.py:27664-27678` | S | No |
| **E1** | 4 zero-link judgment pages + `portfolio_health.py` improvement copy: make the named destinations clickable via `_pending_page`. | `app.py` (Intelligence, Health, Predictive Analytics, The Judge), `portfolio_health.py:268-271` | S | No |
| **E2** | Rename one of the two "Decision Quality" tabs; de-duplicate the 🎯 and 🪞 icon collisions. | `app.py:30840`, `:41369`, `:3010`, `:3017` | XS | No |
| **E3** | Cluster-3 twins: one sentence on each of 🛑 Road Not Taken / 🎯 Rec Outcomes naming the other and the suppressed-vs-made distinction. | `app.py:32358`, `:32515` | XS | No |
| **E4** | Align Cluster-1's four default windows, or state the window inline on every engine-track figure. | `app.py:12271`, `:29834`, Predictive Analytics, Monthly | M | No |
| **F1** | Watchlist per-ticker `except: pass` silently deletes a ticker from an `ENTER_NOW` surface; add a skipped-count caption. | `app.py:24971-24974` | S | No |
| **G1** | In-app User Guide calls 🎯 My Edge "five tabs"; it has six — the hidden one is 🧭 Self vs Engine. **User-facing.** | `app.py:37426` | XS | No |
| **G2** | In-app glossary still names the retired "Fundamental" pillar; code says `business_quality`. **User-facing**, and the same guide contradicts itself. | `app.py:37758` | XS | No |
| **J1** | Same swallowed-failure class as A1, on a different email: F-265's Watchlist "Ready to Enter" buy-announcement uses the unsafe `db.load_exit_signals()` to build `protective_tickers` — the set excluding names under an active EXIT/TRIM from being announced ENTER_NOW. A DB outage silently empties that set, so a flagged name could be announced anyway. Found by the A1 `reviewer` pass (2026-09-24), correctly scoped out of A1 itself. | `headless_alert_engine.py:859` | S | Unverified — not `_GATE_FILES`, confirm before assuming |
| **J2** | Same class, lower stakes: the weekly debrief email also reads `load_exit_signals(days_back=10)` — retrospective review, not a deploy-capital rec. Found by the same pass. | `cron_runner.py:1975` | S | **Yes** — `cron_runner.py` |

**E2 + E3 + G1 + G2 are all XS and independent** — good ride-alongs on any commit touching `app.py`. **J1 + J2 are the same bug class as A1** (`db.load_exit_signals()` vs the safe `..._or_none()` variant) — good to bundle together once picked up.

### P3 — low / opportunistic

| ID | Item | Where | Effort | Gated? |
|---|---|---|---|---|
| **H1** | `docs/user-manual.md` page census: 23 → 28, add 🎯 Recommendation Outcomes, fix RESEARCH ordering. | `user-manual.md:22, 90, 108-124` | S | No (docs) |
| **H2** | `docs/architecture.md:3387` says 6 cron lanes; code and its own §12.6 table say 7. | `architecture.md:3387` | XS | No (docs) |
| **H3** | `docs/user-manual.md:295` cites 30 tables / §6.1-6.30; architecture defines 49. | `user-manual.md:295` | XS | No (docs) |
| **H4** | `CLAUDE.md:79` line count 43,729 → 43,889. | `CLAUDE.md:79` | XS | No (docs) |
| **I1** | The Judge's `except: {}` renders a DB failure identically to the legitimate pre-sample state. Bounded (gates nothing). | `app.py:11234-11235` | S | No |
| **I2** | The Judge badge says "NEVER GATES" — true of its output, but a track record shapes its input. One clarifying sentence. | `app.py:11148` | XS | No |

### Blocked on an owner decision (not work items yet)

| ID | Question | Why it needs you |
|---|---|---|
| **Q1** | Which sign convention wins for "did a protective call work" — Defense's `spy − name`, or Sell-Side's `stock − SPY`? | Unblocks **B1/B2**. Presentation choice, but it determines which surface's existing copy has to change. |
| **Q2** | Should `OFFLINE_SENTINEL_COLLAPSE` widen from `<attr>.get(...) or []` to any *call* on the left, and should `except: pass` get a rule? Would catch 7 live sites at the cost of ~10 new baseline entries. | Gate-coverage policy. The gate's green exit currently proves less than its baseline size implies. |
| **Q3** | Should 📊 Predictive Analytics' Entry Timing tab carry its provenance caveat on screen, given `ENTRY_TIMING_*` is self-declared "fit to a single AMD anecdote" (`constants.py:1366-1371`)? | Whether a provisional band should render user-facing verdicts at all. |
| **Q4** | Does the **LEARN / TRACK RECORD** nav group go ahead (Top-5 #4)? Per `feedback_mockup_first_ux`, this should be a static HTML mock for approval before any code, and per `feedback_phased_ux_rollout_cadence`, one phase per deploy. | Largest single item here; restructures navigation. |
| **Q5** | Curated-universe size: manual says 88, in-app guide says ~90 for the same roster. Only a live Supabase read can settle it (F-262 deleted the in-code dicts). | Needs your DB access, not a code change. |

### Suggested sequencing

1. **A1** alone (gated, needs the Opus review; don't bundle it with anything).
2. **A2+A3+A4** as one class-fix commit.
3. **C1** — highest value-per-effort of the non-defect items.
4. Answer **Q1**, then **B1+B2**.
5. **E1+E2+E3+G1+G2** as a cheap consolidation pass.
6. **D1+D2** behind a `planner` pass; **Q4/E4** behind a mock.
7. **H1-H4** whenever a docs commit is already happening.
