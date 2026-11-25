# 📡 Signals and 🔔 Catalyst Watch name an Act Today sell on the same name (U6b)

**Status:** DESIGNED 2026-11-25 (Opus planner `claude-opus-5-5`, PROCEED WITH CHANGES). Parent: `docs/plans/brief-one-trim-per-name-u6.md` §5 U6-4 (a) (owner: a separate small plan after U6); bug register EWSiblings (9). Display only: no constant is added or changes value, neither page's own sizing or recommendation changes, nothing is written to a history table. **Owner took every default 2026-11-25 ~09:45 ET:** U6b-1 (a) every sell `held_trim_owner.view_sell_tickers` counts; U6b-2 (a) display only, each card keeps its size and Log button; U6b-3 (a) the green all-clear becomes a blue info naming the sold names; U6b-4 (a) no size in the line; U6b-5 (a) today means the ET build date, and C3 moves 🔗 Risk Analysis and the ⚖️ Rebalancer onto the shared gate; the Catalyst Watch "be prepared to exit at the open" stays (no veto). **C1 + C2 + C3 BUILT 2026-11-25** (implementer Sonnet 5.5; 81 + 31 new tests; lead decision: the Catalyst header keeps its label text, only the tag is appended). Implementer findings vs this plan: the SNOW 11-25 fixture is MONITOR only at a stop gap ≤ 5% (HOLD at 10%); `build_daily_briefing` takes no deterioration input, so the §7 5(e) case builds the Review list directly; `unverified_caption("risk")` takes the ticker; the Rebalancer sits on 🥧 Portfolio Overview; "cw" now reads a gate-tier key, so Catalyst's stale banner becomes a warning. C4 docs DONE (G-33, F-11, F-169a, architecture, bug register EWSiblings (9) fixed + (18)–(25), shipped-log, CLAUDE.md). **Opus review FIX-FIRST, 1 blocking (2026-11-25):** `"cw"` must not list `_reduce_calls` (false stale warning on Catalyst) → removed, tests re-pointed; non-blocking taken: HOLD_OR_ADD line adds "and it is not a reason to add to X today", review-policy row wording; logged: Signals' second green "No active alerts" (EWSiblings (25)). Same commit adds the 🔔 Catalyst Watch Hide-values fix (owner ask). Confirming round next.

**Planner's changes to the brief:** (1) The SNOW contradiction on 📡 Signals is the green headline, not a missing card: with no Signals card for SNOW and one earnings warning, the banner reads "✅ No signal-driven actions needed — 1 warning below to monitor." while 🏠 Act Today trims SNOW. U6b changes that banner (U6b-3). (2) The line says the name **has a sell** in today's Act Today call and points to 🏠 Home for the size. It does not quote a size or say which sale is larger (U6b-4). The Act sale can be the smaller one: a `deterioration_trim` name skips the Brief's earnings trim (U5), while Signals and Catalyst Watch still size it. (3) "Today's" must mean today. The C3 / C4 liveness gate checks the book epoch but not the build date, so a Brief built last night still counts the next morning in a tab left open overnight. The shared gate adds the date check that `_home_synth_cache["date"]` was stored for (U6b-5). (4) Under a Lock, the Signals / Risk Analysis / Rebalancer reads use the live Brief (`_home_synth_cache["bundle"]["_daily_brief"]` = `_daily_brief_raw`, app.py ~8520), not the locked snapshot. U6b accepts this, the same as EWSiblings (8); it goes away when Lock Setup is retired.

## 1. Verified at HEAD (`48e86a0f` + the uncommitted `covered_by` Watch)

- **One notion of "Act Today sells T"** is `held_trim_owner.view_sell_tickers(act_today_view(...))` (held_trim_owner.py:160-174). It reads `view["active"]` (post-demotion) through `brief_sell_names_ticker` (:48-62). It counts act kinds in `EXIT_KINDS` (stop_breach, sell_signal, deterioration_exit; decision_bucket.py:52) plus `deterioration_trim` / `risk_off_derisk`, and Review items whose `action.type` is in `_ACT_REVIEW_TYPES` (TRIM_AND_TIGHTEN, TRIM_TO_TARGET, PROTECTIVE_TRIM; decision_bucket.py:36), matched on `ticker` or `action.trim_ticker`. It never counts `risk` (attribution), `critical_news`, `premortem_triggered`, a macro act item, a WATCH / TIGHTEN_ONLY, or a U6 `covered_by` loser (now WATCH). **SNOW's weak-large `TRIM_TO_TARGET` (`reason_key="weak_large"`) is in the set.** `split_defensive` puts `_ACT_REVIEW_TYPES` in the act bucket, so it is in `view["active"]`. EW C4 was verified live on 11-24 with SNOW in the Rebalancer's deferred banner.
- **`sell_signal` is "Reduce or exit"** (daily_briefing.py ~2859-2863), not a full exit. So the line must not say "exits" / "sells all of"; "has a sell" is true for every kind. This is the C3 wording ("already has a sell in today's Act Today call").
- **Liveness gate (C3 app.py:19104-19126, C4 app.py:22948-23001):** `_coord_cache_state("_reduce_calls") == "ready"`, `not st.session_state.get("_daily_brief_offline", False)`, and `coord_freshness.classify(_coord_stamps, _coord_epoch(), "_reduce_calls") == FRESH`. When those pass, the view is built from `_home_synth_cache["bundle"]["_daily_brief"]` and the view is offline when the bundle flag is offline. There is no date check. The memo carries `"date": _today_et().isoformat()` (app.py ~8495, "lets cross-page readers … refuse a brief that is not today's"). The memo-HIT path doesn't restamp `_reduce_calls` (app.py ~8714-8731), so a cost-basis-only correction reads as not fresh. That fails toward the caption, which is acceptable.
- **📡 Signals** (app.py:16962+). It already builds `_sa_view` with no freshness gate, but only when not `_sa_offline` (:17009-17025), and uses it for `signals_headline.brief_context`. `signals_headline.decide` (gate) returns kind `clear` with "✅ … no signal-driven actions needed" whenever no actionable card exists (signals_headline.py ~98-106). Cards are `_actions_cache` = `portfolio.rebalance_actions` (trim / review / add, portfolio.py:928+). Each card is an expander with header `{icon} **{ticker}** — {title} · {urgency}`, expanded only at urgency "high" (app.py:17278-17281). A trim card is "medium", so it is collapsed. The trim card has a 📝 Log button pre-filled with its own `trim_shares` (:17811-17838). SNOW 11-25: no card (17.4% is at or under the 17.5% line, and the standing rule needs w > 18 and P&L > 20). One earnings warning chip, "📅 SNOW reports earnings in 7 days … review ahead of report" (portfolio.py:874-878; the alert dict has no `ticker` key).
- **🔔 Catalyst Watch** (`elif page == "🔔 Catalyst Watch"` app.py:45287 → `_render_holdings_earnings` :5514). The playbook is `build_earnings_playbook` (:5696), computed live on each render from `_last_port_df` / `_last_held_data`. Each card is an expander with header `{icon} **{label}** · {T} — {company} | {urg} {date} ({d}d) | Est. move …` (:5763-5772). It has an Action box (:5887-5896), ⚑ flag captions (:5898-5905), and for MONITOR an `st.info` "Re-evaluate after earnings … If the report is negative, be prepared to exit at the open." (:5922-5928). SNOW's card is MONITOR (the stop-vs-move rule, earnings_advisor.py:459-481: "Be prepared to exit at market open if the report is negative"). The KPI strip and the nav badge `_earnings_posture_alerts_cache` count EXIT / REDUCE (:5707, :5721-5733).
- **No email shows either page's advice.** `rebalance_actions` is called only from app.py:7574 and `rec_events_capture.py:150` (history, unchanged). `build_earnings_playbook` is called only from app.py:5696.
- **`coord_freshness.SURFACE_KEYS`** ("measured" per page; :34): `"aa"` (Signals) and `"cw"` (Catalyst) don't list `_reduce_calls`. Nor does `"ra"` (Risk Analysis), although C3 reads it there. coord_freshness.py is not a `_GATE_FILES` member.

## 2. Design

### 2.1 The shared gate: new gate module `stock_analyzer/act_sell_mention.py`

Pure, no streamlit. It reuses `held_trim_owner` for the sell set and never re-derives it.

- `verified_sells(*, reduce_state, session_brief_offline, freshness, built_date, today, view) -> set[str] | None`. Returns `None` unless all of these hold:
  - `reduce_state == "ready"`
  - `session_brief_offline` is falsy (the C3 / C4 reading)
  - `freshness == coord_freshness.FRESH`
  - `str(built_date) == today.isoformat()` (`today` = `_today_et()`; a `None` / malformed `built_date` → None)
  - `held_trim_owner.view_sell_tickers(view)` is not None

  When all hold it returns that set (upper-cased). This is the C3 / C4 gate plus the date leg.
- `unverified_caption(surface) -> str` (surface `"signals"` / `"catalyst"` / `"risk"` / `"rebalancer"`). The Signals / Catalyst text: "⚠️ This page couldn't be checked against today's Act Today calls (🏠 Home's Act Today isn't available for the current book: not built yet, failed, out of date, or built on an earlier day), so a card here may sit beside an Act Today sell on the same name." The C3 / C4 texts are kept verbatim, plus ", or built on an earlier day" inside the parenthesis if U6b-5 (a).
- `card_tag(ticker, sells) -> str`. Returns " · 🏠 Act Today sell" when T is in `sells`, else "". Used in both pages' expander headers, so a collapsed card shows it.
- `signals_card_line(ticker, card_type, sells) -> str | None`. Returns None when `sells` is None or T is not in it. For `trim` / `review`: "🏠 {T} has a sell in today's Act Today call (see 🏠 Home for the size). Follow that call first; once it is logged this card is re-sized from your new book, so it is not a second sale on top of it." For `add`: "🏠 {T} has a sell in today's Act Today call (see 🏠 Home). Follow that call first; this card is not a reason to add to {T} today."
- `catalyst_line(ticker, playbook_action, sells) -> str | None`. Returns None as above. For `EXIT` / `REDUCE`: the Signals sell text. For every other action: "🏠 {T} has a sell in today's Act Today call (see 🏠 Home for the size). Do that first; this card's {label} plan applies to any shares you keep." `label` = "Watch" for MONITOR, else the action's display word (Hold / Hold or add), the same mapping app.py:5752 uses (move that mapping into the module, render-only).
- `signals_headline(head, sells, card_tickers, breach_named) -> {"head": dict, "line": str | None}`:
  - `sells` None → head unchanged, line None (app.py renders `unverified_caption("signals")` instead).
  - `named` = sells − `breach_named` (tickers the headline / alerts already name with the Brief's sell clause, i.e. `active_breach` ∩ the tickers of breached alerts).
  - head kind `clear` and `named` non-empty → `{"kind": "act_elsewhere", "text": "ℹ️ No signal-driven trades on this page, but {names} {has/have} a sell in today's Act Today call (🏠 Home).", "note": None}`. The green "✅" never shows on a day Act Today sells a name.
  - any other kind → head unchanged. `line` = "🏠 Also in today's Act Today call: {names − card_tickers} {has/have} a sell (see 🏠 Home); this page has no card of its own for {it/them}.", or None when that set is empty.
  - Names are sorted and joined as "X", "X and Y", "X, Y and Z".

No `$`, `**` or HTML in any text: everything renders through `st.info` / `st.caption`, and expander labels are markdown. Hide values has nothing to mask, because no figure is quoted. The read-only viewer sees the same text; there are no buttons.

### 2.2 Wiring (app.py, render only; no new comparison against a constants value)

- A small app.py helper, `_verified_act_today_sells()`. It reads `_coord_cache_state("_reduce_calls")`, `st.session_state.get("_daily_brief_offline", False)`, `coord_freshness.classify(...)`, `_get_or_offline(memo, "date")` and `_today_et()`. It builds the `act_today_view` the C3 way (Brief + bundle offline flag from `_home_synth_cache`, `_port_df_enriched`, `_live_prices`, `STOP_RECOVERY_MARGIN_PCT`, `_now_et()`) and returns `act_sell_mention.verified_sells(...)`. Wrap it in try / except: on failure, log a warning (the C3 idiom, app.py:19139-19145) and return None.
- **📡 Signals:** compute `_sa_sells` once at the top of `_sa_tab1`, independent of `_sa_offline`. Pass it through `act_sell_mention.signals_headline(...)` after `signals_headline.decide`, and render the new kind `act_elsewhere` with `st.info`. Render `line` as `st.caption` under the headline (also when `_sa_offline`). Render `unverified_caption("signals")` when `_sa_sells is None` and Home's portfolio is loaded. On each action card, append `card_tag` to the expander label and render `signals_card_line` as `st.info` directly under the "WHAT TRIGGERED THIS" box. Every card's own size, text and Log button are unchanged (U6b-2 (a)).
- **🔔 Catalyst Watch:** compute the sells once in `_render_holdings_earnings` (or pass them in from the page block). Render `unverified_caption("catalyst")` above the 📋 Playbook when the sells are None and the playbook is non-empty. On each playbook card, add `card_tag` to the expander label and render `catalyst_line` as `st.info` directly under the Pre-Earnings Action box. The MONITOR "Re-evaluate after earnings … be prepared to exit at the open." stays: it is true for the shares kept, and the line says so. The KPI strip and the nav badge stay unchanged (named, §6).
- `coord_freshness.SURFACE_KEYS`: add `_reduce_calls` to `"aa"` and to `"ra"` (the C3 sibling), so their stale banner names "Reduce/Exit calls". **Not `"cw"` (Opus review 2026-11-25, blocking):** Catalyst's EXIT / REDUCE cards are built live from the current book and nothing there is held back by `_reduce_calls`, so a "from before your last trade … can SUPPRESS" warning would be false; on Catalyst a stale `_reduce_calls` shows only the unverified caption (the mention fails closed).

### 2.3 Why this shape

- **Display only, and the page keeps its own size (U6b-2 (a)).** Deferring Signals / Catalyst like the Rebalancer would hide their earnings-week size on days the Act sale is smaller: a `deterioration_trim` name has no Brief earnings trim (U5) but still gets one on Signals / Catalyst. That would loosen. The C3 Risk Analysis precedent is the same: annotate, keep the card, state the order.
- **Two sizes on one name (E + W, e.g. w 19%, score 50):** Signals / Catalyst say "trim to ≈10%, N shares" and Act Today says "to 8%, M ≥ N shares". U6b settles which call to follow (precedence line plus header tag), not the number. G-33 and the User Guide claim that "the Brief, 📡 Signals and 🔔 Catalyst Watch all show the same number". U6 already made that untrue in this case, so C4 corrects it.
- **No size in the line (U6b-4 (a)):** the C4 FIX-FIRST was a banner that claimed a size Act Today doesn't give. Act item shapes differ (target weight, a fraction of the position, points of the book, an exit). One number comes from one source: 🏠 Home.

## 3. Time context, freshness, offline, viewer

| State | Signals / Catalyst show |
|---|---|
| Home built today, book unchanged, T sold by Act Today | the tag, the line and (Signals, no card) the headline / caption |
| Home not built this session | the page's existing "not loaded" stop; nothing new |
| Brief crashed (`_daily_brief_offline` / bundle stub) | the unverified caption, no mention |
| Trade logged or imported since the build (epoch bump → `_reduce_calls` STALE) | the unverified caption plus the stale banner (now naming Reduce/Exit calls). After Home rebuilds, the mention follows the new Brief (SNOW at ≈8% has no W trim, so the mention goes) |
| Tab left open overnight / weekend visit after a Friday build | the date leg fails → unverified caption, until Home rebuilds today |
| Saturday or Sunday visit after a same-day Home build | the mention follows that build (the Brief runs on weekends; no market-hours logic here) |
| Lock Setup on | the mention follows the live Brief, not the locked snapshot (EWSiblings (8); accepted, moot once Lock Setup is retired) |
| Read-only viewer | the same text; nothing to lock |

The module has no date maths beyond comparing the ET ISO date. `today` is always passed in (`_today_et()`). The containers run UTC, so a 21:00 ET render (02:00 UTC the next day) must compare against the ET date.

## 4. Chunks

- **C1 (decision-bearing; implementer; Opus review shared with C2).**
  - New `stock_analyzer/act_sell_mention.py`, which joins `_GATE_FILES` (107 → 108; re-print to confirm) with its reason row in `docs/review-policy.md`: "decides when 📡 Signals / 🔔 Catalyst Watch say a held name has an Act Today sell, and the Signals all-clear headline; a wrong call hides a sell or names one that isn't there".
  - `tests/test_act_sell_mention.py`.
  - Pure-additive, not wired.
- **C2 (decision-bearing; implementer; one Opus round with C1; `feat(signals):` with Design = planner).**
  - app.py: the `_verified_act_today_sells` helper plus the Signals and Catalyst wiring (§2.2).
  - `coord_freshness.SURFACE_KEYS` "aa" / "cw" / "ra".
  - app.py grep tests and the integration tests (§7).
  - Fix any test that pins Signals' clear text on a day with an Act sell, and name each one.
- **C3 (U6b-5 (a) only; decision-bearing; implementer; same Opus round if ready, else its own).** Switch 🔗 Risk Analysis (app.py:19104-19126) and the ⚖️ Rebalancer (:22983-23007) to `_verified_act_today_sells()`, which adds the date leg. Rewrite their unverified captions to include "or built on an earlier day". Existing C3 / C4 tests stay green; add a day-old-memo case to each.
- **C4 (docs; Opus lead).**
  - `docs/requirements.md`: F-169a (Signals headline: the `act_elsewhere` state), F-11 (Catalyst playbook line), and G-33 (the "same number" sentence becomes "the same earnings-week number; when another Act Today sell on the name is larger, Act Today shows that one, and 📡 Signals / 🔔 Catalyst Watch name it").
  - User Guide earnings-week paragraph (app.py ~46595: the same correction, plus one sentence on the 🏠 Act Today sell tag).
  - `docs/architecture.md`: the coordination row, `act_sell_mention` beside `held_trim_owner`.
  - Bug register: EWSiblings (9) → fixed; add the new sibling rows from §6.
  - CLAUDE.md queue (EW "NEXT U6b"), shipped-log, this plan's and the U6 plan's Status, and a memory note ("has a sell, never a size; today means the ET build date").
  - No constants-table change.
- **Live check after the C2 push (laptop; SNOW is in its window 11-25 → 12-02 while the 8% Act trim fires):**
  - 📡 Signals headline is blue: "ℹ️ No signal-driven trades on this page, but SNOW has a sell in today's Act Today call (🏠 Home)." (not the green ✅).
  - 🔔 Catalyst Watch SNOW header ends "· 🏠 Act Today sell", and the line under the Action box reads "… Do that first; this card's Watch plan applies to any shares you keep."
  - After logging a trade elsewhere without revisiting Home: the unverified caption on both pages; 📡 Signals' stale banner names Reduce/Exit calls, 🔔 Catalyst Watch's stays the quiet composite-scores caption.
  - No literal double asterisks and no dollar sign anywhere in the new lines.

## 5. Owner decisions (defaults marked)

- **U6b-1 Which Act sells count. (a) DEFAULT:** every sell `held_trim_owner.view_sell_tickers` counts (exits, deterioration trims, risk-off de-risk, the Review trims incl. macro and profit-lock). That is the one notion 🔧 Tune-up, 🔗 Risk Analysis and the ⚖️ Rebalancer already defer to. (b) Weak-large only: a second notion, which the brief rules out.
- **U6b-2 Signals / Catalyst Watch's own size on a name Act Today sells. (a) DEFAULT:** display only. The card, its size and its Log button stay; a header tag plus one line say "Follow that call first; … not a second sale on top of it". (b) Defer like the ⚖️ Rebalancer (no own share count, no Log button). Not recommended: the Act sale can be smaller (U5 case), so the earnings-week size would vanish, which loosens.
- **U6b-3 Signals headline when Act Today sells a name. (a) DEFAULT:** the green "✅ … no signal-driven actions needed" becomes a blue info naming the names. Other headlines stay, plus one caption naming sold names with no Signals card. (b) Caption only; the green all-clear stays.
- **U6b-4 Quote Act Today's size in the line. (a) DEFAULT no:** "has a sell … see 🏠 Home for the size". (b) Quote the target weight when the item is a `TRIM_TO_TARGET` ("trims SNOW to 8.0%"). More informative, but per-kind text is the C4 FIX-FIRST class.
- **U6b-5 "Today's" means today. (a) DEFAULT:** the shared gate requires Home's build date = today ET, and C3 moves 🔗 Risk Analysis and the ⚖️ Rebalancer onto it. This tightens toward showing the sell: a Brief built yesterday no longer defers today's Rebalancer trims or Risk Analysis notes. (b) Date leg on the two new pages only; C3 / C4's missing date check is logged as a sibling row.
- Veto only: Catalyst Watch's MONITOR "If the report is negative, be prepared to exit at the open." stays as it is (it applies to the shares kept, and the line says so).

## 6. Risks, coordination, siblings (fix the class)

- **Coordination:** no new session key and no new producer, so there is nothing to register in `coord_freshness` beyond the `SURFACE_KEYS` reads. `_reduce_calls` and `_home_synth_cache` are read the C3 / C4 way. Never raw `_reduce_calls` keys: an Act `risk` card's ticker is an attribution (memory `card_ticker_semantics`).
- **Calm posture:** nothing becomes Act-level on these pages. The line points to the one decision on 🏠 Home. The headline change removes a false all-clear and adds no alarm (info, not warning).
- **Offline contract:** `verified_sells` returns None, never an empty set, when it couldn't check. An empty set means "checked, Act Today sells nothing", and only then is the green all-clear allowed.
- **Siblings, fixed here:** the `"ra"` `SURFACE_KEYS` gap (C2); with U6b-5 (a), C3 / C4's missing date leg (C3).
- **Siblings, named, not fixed (bug register rows in C4):**
  - 📈 Analysis's "under an active Reduce/Exit call" check reads raw `_reduce_calls` with no freshness or date leg (app.py ~27681-27688). It is pre-demotion, so a recovered stop breach still reads as a Reduce call.
  - 🥧 Portfolio Overview's EXIT / TRIM badge reads raw `_reduce_calls[T]` (`summary_view.position_status_badge`). Same pre-demotion read.
  - Catalyst Watch's "REDUCE signals — Trim before report" KPI and the nav badge (`_earnings_posture_alerts_cache`, app.py:5707 / :5731) still count a REDUCE that the line now ranks second to Act Today.
  - The 📅 Calendar macro PROTECT 15% trim (`macro_playbook._action_detail`, EW S7) has no mention.
  - EWSiblings (2) / (3) (the Signals checklist "reduce now", and Sell-signal names with three different calls) remain.
  - Catalyst Watch's "Market Value" / "Earnings Risk" metrics (app.py:5777 / :5781) don't appear to pass through `_m`. The implementer should confirm against the Hide values toggle and log the result, not fix it here.
  - 📋 Watchlist: the implementer greps whether any held-name trim or hold advice renders there without the reduce cross-check, and reports it. Don't assume.

## 7. Tests the build must include

1. **`verified_sells` legs.** Each single failing leg returns None:
   - reduce_state "missing" / "offline"
   - session offline True
   - freshness STALE / ABSENT
   - built_date yesterday / None / "garbage"
   - the view offline / None / not a dict

   With all legs passing it returns exactly `held_trim_owner.view_sell_tickers(view)`. Pin the empty set (checked, none) as distinct from None.
2. **One notion.** These go through the real `act_today_view` with no Act sell on the name, so no mention:
   - a `risk` attribution card only
   - a recovered stop breach (moved to `resolved`)
   - a U6 `covered_by` WATCH loser
   - a critical-news hold

   These do produce a mention: a weak-large `TRIM_TO_TARGET`, a macro `PROTECTIVE_TRIM` keyed by `trim_ticker`, a `deterioration_trim`, an active stop breach, a `sell_signal`, and `risk_off_derisk`. Lower-case tickers are handled.
3. **Signals headline.**
   - clear + sells {SNOW} → kind `act_elsewhere`, text names SNOW, no "✅" / "well-balanced"
   - clear + an empty set → the head is byte-identical to the input
   - clear + None → unchanged
   - act / attention / none / resolved / offline → head unchanged; `line` names the sold names with no card; a breach-named ticker is excluded; empty → None
   - one / two / three names join and agree in number ("has" / "have", "it" / "them")
4. **Card lines and tags.** Signals trim / review / add texts and Catalyst EXIT / REDUCE / MONITOR (label "Watch") / HOLD / HOLD_OR_ADD / unknown action. T not in sells → None and the empty string. No dollar sign, no double asterisk and no angle bracket in any text. The ticker is interpolated verbatim.
5. **End to end** (`build_daily_briefing` + `act_today_view` + `portfolio.rebalance_actions` + `build_earnings_playbook`; reuse the helpers in `tests/test_earnings_week_consistency.py` / `tests/test_brief_one_trim_per_name.py`):
   - (a) **SNOW 11-25:** N=8, w 17.4, score 50, earnings in 7 days. The Brief issues a weak-large Act trim; Signals has no SNOW card; the headline is `act_elsewhere` naming SNOW; the playbook SNOW action is MONITOR with the Watch line.
   - (b) **E + W:** w 19.0, score 50. Signals trim card (target 10) gets the sell line; the Catalyst REDUCE gets the sell line; the Act trim shares ≥ the Signals shares (the U6 proof, pinned here so the two sizes are known and intended).
   - (c) score 55 (no W) and w ≤ the line → no mention anywhere.
   - (d) earnings day 8 with W → a mention (W isn't earnings-gated).
   - (e) a `deterioration_trim` name in the window, where the Act sale is smaller than the Signals earnings-week trim → both lines present; the text makes no "larger" / "smaller" claim.
6. **Time context.**
   - built_date = today ET at 21:00 ET with the UTC date already tomorrow → verified.
   - A Friday build seen on Saturday → None.
   - A Saturday build seen on Saturday → verified.
   - A SELL logged after the build (epoch +1 → STALE) → None, so the unverified caption shows.
7. **`coord_freshness.SURFACE_KEYS`:** "aa", "cw" and "ra" contain `_reduce_calls`, and the existing registry tests pass.
8. **app.py grep tests.**
   - Signals and Catalyst call `act_sell_mention` through `_verified_act_today_sells`.
   - The helper passes `_coord_cache_state("_reduce_calls")`, `_daily_brief_offline`, `coord_freshness.classify`, the memo `"date"` and `_today_et()`.
   - The `act_elsewhere` kind is rendered with `st.info`.
   - The new lines render through `st.info` / `st.caption`, never inside `unsafe_allow_html`.
   - app.py makes no new comparison against a constants value (the antipattern gate).
9. **C3 (if taken).** Risk Analysis and the Rebalancer return no deferral or `brief_owns` for a day-old memo (the caption shows instead); every existing C3 / C4 test is unchanged.

Files (verified at HEAD by the planner):
- `stock_analyzer/held_trim_owner.py` (:48-83, :160-174)
- `stock_analyzer/act_today_view.py` (:123-218)
- `stock_analyzer/decision_bucket.py` (:31-53, :75-81, :211-232)
- `stock_analyzer/signals_headline.py` (:36-106)
- `stock_analyzer/portfolio.py` (alerts :852-878, `rebalance_actions` :928-1041)
- `stock_analyzer/earnings_advisor.py` (`_recommend_core` :272-571)
- `stock_analyzer/coord_freshness.py` (`classify` :276-290, `SURFACE_KEYS` :34 / :492-515)
- `stock_analyzer/daily_briefing.py` (sell_signal ~:2859)
- app.py:
  - Signals :16962-17040 and cards :17246-17843
  - Catalyst :5514-5928 and :45287-45312
  - Risk Analysis cross-check :19091-19145
  - Rebalancer cross-check :22940-23015
  - synth memo `date` / bundle ~:8489-8521
  - memo-HIT stamp note :8714-8731
  - User Guide earnings paragraph ~:46595
