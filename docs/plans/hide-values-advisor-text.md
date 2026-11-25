# 🙈 Hide values, phase B: money inside advisor sentences

**Status:** DESIGNED 2026-11-25 (planner, Opus 5.5: PROCEED WITH CHANGES). **QUEUED by the owner 2026-11-25: not a priority now; don't start without a fresh ask.** Nothing built. Owner answers Q1–Q5 below before C0 (unanswered; the planner's defaults: Q1 per-share visible, anything × shares or from the account hidden incl. its sign; Q2 emails unmasked; Q3 AI-written prose deferred to a phase C and disclosed; Q4 🔔 Catalyst Watch moves to money-only; Q5 sidebar help becomes "Hide or show your money (values, P&L, trade sizes). Per-share prices stay visible."). Phase A (app.py `_m` wrapping + the `_privacy` default, implementer) is separate and ships first. Lead note: the owner's 2026-11-25 rule "hide only money, keep per-share prices" already applies; 🔔 Catalyst Watch's Action text still masks every `$` (prices too) until C4.

## 1. Problem and owner rule

Owner decision 2026-11-25: 🙈 Hide values (`st.session_state["_privacy"]`, default True) hides only the OWNER'S MONEY (position value, P&L $, trim / sell / add dollars, cost, risk $, VaR / CVaR $, drift $, tax $, realized $, cash, portfolio value, margin debit, alpha / opportunity cost $, macro impact $) and keeps PER-SHARE PRICES visible (stops, current / live price, entry zones, peaks, targets, EPS, $/share, ATR stop).

Phase A covers figures app.py formats itself. Phase B covers sentences built in `stock_analyzer/` that mix money and prices and are rendered by app.py through `_md_bold` (`util.md_bold_to_html`), raw HTML, or markdown. A regex at render time cannot tell them apart: "≈" precedes money ("(≈$542)") and prices ("tighten the stop to ≈$45.10", "(≈$312/share)"), and money also appears unmarked ("on a $1,355 position", "**$X (Y%)**").

## 2. Decision: mechanism A, an invisible money marker set by the producer

Rejected:
- **B (structured fields):** changes the return shape of ~10 producers and every consumer, including `stop_breach_text`, `snapshot_context` and the email builders. Too large for a display feature, and strings composed across helpers lose the sidecar.
- **C (context regex per producer):** fails silently on the next wording edit; "≈$" is ambiguous today (rebalancer "≈$1,082" money vs exit_advisor:697 "≈$45.10" price).
- **D (render-side knowledge per surface):** the render site doesn't know which `$` in a sentence is which; it's C again, spread over 31 sites.

**Design.** The producer, which is the only place that knows what a number means, wraps each MONEY figure in a pair of invisible Unicode format characters. The app's render boundary masks or strips the pair. Every sink that leaves the app (LLM, email, DB) strips it.

New pure helpers in `stock_analyzer/text_fmt.py` (not a `_GATE_FILES` member; keep it that way):
- `MONEY_OPEN = "⁣"`, `MONEY_CLOSE = "⁤"` (category Cf: invisible, `str.isspace()` is False, untouched by `html.escape`, `str.split()` and regex `\s`). These are plumbing, not thresholds, so they don't go in `constants.py`.
- `money(v, fmt=",.0f", *, sign="")`: returns `MONEY_OPEN + sign + "$" + format(v, fmt) + MONEY_CLOSE`. A missing or non-finite value returns "—", unmarked. The sign ("±", "+", "-") sits INSIDE the span, so it is masked too, as `_m` does. Leading "≈" / "(" / "**" stay outside.
- `mark(s)`: wraps an already formatted money string (for `signed_dollars(...)` output and similar).
- `mask_money(text, private, mask="••••••")`: if `private`, replace each `OPEN … CLOSE` span with `mask`; an OPEN with no CLOSE (text cut by a `[:N]` slice) masks to the end of its token (fail closed). Then remove every stray marker character in both modes. `None` reads "". With `private=False` the output is byte-identical to the text before the marker existed.
- `strip_money_marks(text)` = `mask_money(text, False)`.

**Order at the render boundary: mask first, then escape / bold.** `mask_money` runs on the raw producer text, then `md_bold_to_html` / `safe_html` / `safe_text`. The mask "••••••" carries no HTML, markdown or `$` hazard, and masking removes `$` characters, so it can only shrink the `$`-pair LaTeX risk. For markdown sites that escape `$` as `\$`, mask before the `.replace("$", "\$")`.

### Failure modes and their guards

| Failure | Effect | Guard |
|---|---|---|
| A money figure formatted without `money()` | Shown under Hide values (status quo, fail-open) | Ratchet in C0: a new `check_antipatterns.py` rule plus a baseline |
| A render site that doesn't call `mask_money` | Money shown; markers invisible (status quo) | `md_bold_to_html(private=True)` default plus an AST test that every app.py `_md_bold(` call passes `private=`; non-`_md_bold` sites listed in §3 |
| Marker leaks into an LLM prompt | Invisible noise in the prompt; golden drift | Strip in `llm.complete` (the only `messages.create` caller in `stock_analyzer/`) |
| Marker leaks into an email | Invisible chars, or a box in an old client | Strip `subject` and `html` in `notify.send_email_resend` |
| Marker stored in the DB (`trades.notes` / `signal_context`; Brief `directive` / `why` reach `_render_trade_button`, app.py ~12537 / ~12558, sliced `[:140]` / `[:240]`) | Invisible chars stored; a slice can split a pair | Strip before the slice in `_render_trade_button`, and again for every str value (including nested `decision_context`) in `db.save_trade` |
| Text equality or hashes (`snapshot_context.calls_hash`, `_home_synth_cache` signature, cross-surface text parity tests) | Markers are deterministic, so marked equals marked | None needed. Don't compare marked text with stripped text |
| Test pins on producer strings | Substrings like "≈$1,234" stop matching | Each pin compares `strip_money_marks(text)`; wording is unchanged |
| CSV downloads (app.py has 8 `download_button` sites) | A marker in a CSV cell | C1 confirms none export advisor text; if one does, strip there |

**Why not Phase A's `_m` inside `_fmt_action` (urgent for Phase A).** `_fmt_action`'s output goes into the AI Snapshot's LLM prompt and its cache hash (app.py ~14266 to ~14268: `brief_calls_view` / `brief_calls_lines(..., _fmt_action)` → `calls_hash`). Masking it with `_m` would:
- send "••••••" to the model whenever Hide values is on;
- flip `calls_hash` on every toggle, so the card falsely says the Brief calls have changed.

**Phase A must leave `_fmt_action` unmasked; C2 converts it to `money()`.** The same rule holds for every Phase A site: `_m` only at the final render, never in a string that is cached, stored, emailed or sent to an LLM.

## 3. Where the masking happens (app.py render boundary)

- **`_md_bold`, all ~31 sites.** Do NOT redefine `_md_bold` as a local `def` in app.py. `scripts/check_antipatterns.py` `escaping_names()` grants XSS-escaping credit only to the IMPORTED binding and drops any name also bound in the module (the shadow subtraction). A local wrapper would therefore re-flag every `_md_bold` `unsafe_allow_html` site as dynamic HTML. Instead:
  - give `util.md_bold_to_html` (not a gate file; app.py is its only caller) a keyword `private: bool = True` that runs `text_fmt.mask_money(value, private)` BEFORE its escape step;
  - keep the app.py import alias `md_bold_to_html as _md_bold` unchanged;
  - pass `private=_hv()` at every call site, where `_hv()` returns `st.session_state.get("_privacy", True)`, read at call time.

  The default is True, so a call site that forgets the argument fails CLOSED: it masks even when values are shown, which is visible and safe. An AST test pins that every `_md_bold(` call in app.py passes `private=`. Text with no markers is unchanged either way, so price-only sites behave as today.
- **`_mm(text)`:** the same masking without the HTML step, for markdown / `st.caption` / `st.info` / expander labels / dataframe cells that show producer text (the perf_advisor table cells, §4).
- **Raw sites that skip escaping today** (the escaping sibling, folded in here):
  - the Review card's `_act_text` (from `_fmt_action`, ~13212 before Phase A renumbering);
  - ⚖️ Rebalancer `_tr['rationale']` / `action_detail` (~23205 / ~23214) and the underweight text (~23367);
  - G-20 reasons (~11776; prices only, escaping only).
  
  Route each through `_md_bold`, and check first that each was authored as `**`-markdown, not HTML.
- **`snapshot_context.section_html(view, *, private=True)`:** it does its own `safe_text` escaping, so it masks inside, before escaping. `calls_hash` and `brief_calls_lines` stay unmasked: the prompt and the hash never depend on the toggle.
- **`_fmt_action`:** its `trim_dollars` figures (TRIM_AND_TIGHTEN, TRIM_TO_TARGET, PROTECTIVE_TRIM) become `money(...)`. `new_stop` is a price and stays.
- **🔔 Catalyst Watch (~5948):** replace `_mask_plain_usd(_pb_detail)` with masking through `_md_bold`. This ships in the SAME commit that marks `earnings_advisor` (C4); otherwise money is unmasked in between. Keep `text_fmt.mask_plain_usd` while any caller remains, and delete it with its last caller.

## 4. Producer inventory (from HEAD 2026-11-25; M = money → `money()`, P = price, left alone)

| Module (gate?) | Money sites (M) | Price-only (P, no change) |
|---|---|---|
| `daily_briefing.py` (gate) | 3638 "(N shares ≈ $X)"; 3898 "trim (≈$X)" | 3899 "(≈$312/share)" (formatted `,.0f`, so it needs the ratchet's price pragma); 2250, 2317, 2919, 2924, 3327 peak; 2977, 2983, 2984 pre-mortem trigger / current |
| `risk_advisor.py` (gate) | all 24 `$` f-strings: 333–336, 354, 356, 366, 386, 391, 531, 532, 554, 657, 659, 678, 679, 845, 863, 870, 872, 942, 955, 960, 967 | none |
| `rebalancer.py` (gate) | 266, 321, 328, 448 drift $ | none |
| `earnings_advisor.py` (gate) | 346–348, 371–373, 395–397 (position $, ±move $, trim $) | none |
| `macro_playbook.py` | 455, 466 "sell N (≈$X)" | none |
| `perf_advisor.py` | 292, 330, 414 (table cells "+$X" / "-$X"); 307, 419, 420, 425, 426, 432 (text) | none |
| `watchlist_advisor.py` (gate) | none | entry zones, limit / alert prices (630–1210) |
| `portfolio.py` (gate) | none | alert msgs 814, 819, 839 stop; 912 targets |
| `stop_breach_text.py`, `exit_advisor.py` (gate) 697 | none | stops / live price |
| app.py-built (Phase A, not B) | 📡 Signals `action_text` `~\$half_val` / `(≈$ts*price)` (~17790–17850); the Rebalancer expander label (~23205) | Phase A owns these: confirm they're covered |

Other `stock_analyzer/` modules with `$…,.Nf` formatting, out of Phase B and baselined by the ratchet: `tax_report` 9, `period_attribution` 4 (has its own `mask_usd`), `performance_review` 4, `trade_review` 3, `broker_frame_view` 3, `trigger_price_check` 2, `decision_journal` 2, `account` 2, and 1 each in `valuation_target`, `trade_confirm`, `sync_timing`, `scorecard`, `report_display`, `hold_reasons`, `held_stop_floor`, `debrief_advisor`, `comparison`, `budget_view` (has `mask=`), `broker_frame`. `notify.py` (7) is email: excluded, never masked. Log these as bug-register row **HideValuesSweep** (fix the class: each one gets classified and burned down when its page is touched).

## 5. Owner questions (recommended default in bold)

- **Q1 classification rule.** **Anything per share stays visible; anything multiplied by a share count or taken from the account (position $, trim / sell $, ±move $, risk $, VaR / CVaR $, drift $, savings $, P&L $, alpha $) is hidden, including its sign.** Edge cases under this rule:
  - "(≈$312/share)" visible;
  - "tighten the stop to ≈$45.10" visible;
  - "R:R reaches 2:1 at about $45" visible;
  - "±$217 in a single session" hidden;
  - "±$X per day" hidden.
- **Q2 emails.** **Unmasked (they go to your own inbox; the cron has no toggle).** Markers are stripped before sending.
- **Q3 AI-written prose** (the AI Snapshot narrative, debriefs, the Q&A answers). The model sees real dollars, and its free text can't carry markers. **Defer to Phase C and disclose it in the User Guide.** Phase C option: under Hide values, AI prose uses `mask_plain_usd`, which over-masks and hides its prices too.
- **Q4 🔔 Catalyst Watch.** Today Hide values masks every `$` in its Pre-Earnings Action (prices too, since F-11 2026-11-25). **Switch it to money-only, so stops and entry prices reappear, matching the rule.**
- **Q5 sidebar help text.** **"Hide or show your money (values, P&L, trade sizes). Per-share prices stay visible."** (today, app.py ~3845: "Hide or show all dollar amounts — useful in public"). It belongs with Phase A or C6.

No `constants.py` value changes, and no gate, size, branch or wording changes (wording is byte-identical once markers are stripped).

## 6. Chunks (in order; one page group per deploy)

Phase A ships first. Its implementer is told now: **don't `_m` `_fmt_action`** (§2), and never `_m` a string that is cached, stored, emailed or sent to an LLM.

| # | Chunk | Kind | Files | Review |
|---|---|---|---|---|
| C0 | Helpers + ratchet. `money` / `mark` / `mask_money` / `strip_money_marks` in `text_fmt`. A new `check_antipatterns.py` rule `UNMARKED_MONEY_FSTRING`: in `stock_analyzer/*.py` except `text_fmt.py` and `notify.py`, an f-string whose literal part ends in a dollar sign (optionally after a ± / + / - sign) directly before a formatted value whose spec contains a comma. The inline pragma `# hv: price` exempts a per-share figure (daily_briefing 3899). Baseline every current site. Nothing is wired yet, so there's no visible change. | mechanical | `stock_analyzer/text_fmt.py`, `scripts/check_antipatterns.py`, `scripts/antipattern_baseline.json`, `tests/test_text_fmt.py`, `tests/test_check_antipatterns.py` | skip review (pure additive, not on a decision path); suite + antipattern green |
| C1 | Sinks strip markers. `llm.complete` (system + every message's text); `notify.send_email_resend` (subject + html); `db.save_trade` (every str value, recursing into the `decision_context` dict); app.py `_render_trade_button` strips `signal_context` / `notes` BEFORE the `[:140]` / `[:240]` slices. Identity on today's text (no markers exist yet). C1 also confirms none of the 8 `download_button` sites exports advisor text. | **decision-bearing** (DB-write + LLM path) | `stock_analyzer/llm.py`*, `stock_analyzer/db.py`*, `stock_analyzer/notify.py`, `app.py` | Opus reviewer |
| C2 | Render boundary. `util.md_bold_to_html(private=)`, `private=_hv()` at every `_md_bold(` site, `_mm` for markdown / caption / label / cell sites, the raw sites routed through `_md_bold` (escaping sibling), `snapshot_context.section_html(private=)`, `_fmt_action` money via `money()`. The first visible effect is Brief Review / 🧾 Summary trim dollars masked via `_fmt_action`. | **decision-bearing** (feeds the Snapshot prompt and its hash; XSS escaping) | `stock_analyzer/util.py`, `stock_analyzer/snapshot_context.py`, `app.py` | Opus reviewer |
| C3 | 🏠 Home group: mark `daily_briefing` 3638 / 3898 and all of `risk_advisor`. This covers the Brief, 🔧 Tune-up, 🧾 Summary, 🔗 Risk Analysis and the AI Snapshot card in one deploy, because `risk_advisor` feeds all of them. | **decision-bearing** (gate files; text only) | `daily_briefing.py`*, `risk_advisor.py`*, their tests | Opus reviewer |
| C4 | 🔔 Catalyst + 📅 Macro: mark `earnings_advisor` and `macro_playbook`; the same commit switches Catalyst from `mask_plain_usd` to `_md_bold(private=)`, so prices reappear (Q4). | **decision-bearing** (gate file) | `earnings_advisor.py`*, `macro_playbook.py`, `app.py` ~5948, tests | Opus reviewer |
| C5 | ⚖️ Rebalancer + 📊 Performance: mark `rebalancer` and `perf_advisor`; perf table cells go through `_mm`. | **decision-bearing** (gate file) | `rebalancer.py`*, `perf_advisor.py`, `app.py` table site, tests | Opus reviewer |
| C6 | Docs (lead; requirements is policy-adjacent). New **F-321 "🙈 Hide values hides your money, not prices"** (no F-ID exists today: Hide values is only mentioned inside F-11 / F-244b / F-255 / F-276d / F-306 / F-318). User Guide sidebar sentence + the Q3 AI-prose limit. Sidebar help text (Q5) if Phase A didn't do it. Bug-register row HideValuesSweep (§4 list). Shipped-log, this Status line. | docs | `docs/requirements.md`, `app.py` User Guide, `docs/bug-register.md`, `docs/shipped-log.md` | none |

\* = `_GATE_FILES` member. The hook forces a `Review =` citation.

**Reviewer checklist, C3–C5 (keep the rounds cheap):**
- `strip_money_marks(new) == old` byte for byte on every pinned string;
- no number, branch, threshold or size changed;
- every marked figure is money under Q1, and no price is marked;
- the ratchet baseline shrank by exactly the marked sites.

A planner + reviewer chain is warranted only because the sinks (C1) and the shared escaper (C2) touch the DB-write, LLM and XSS paths. The producer chunks are text-only, and their review exists because the hook requires it for gate files.

## 7. Tests the build must include

- **T1 `text_fmt` (C0).**
  - `mask_money("Trim 12 shares (≈" + money(1234) + ") AND raise stop to $98.40", True)` == "Trim 12 shares (≈••••••) AND raise stop to $98.40".
  - The same call with `False` returns the exact legacy string.
  - `money(217, sign="±")` masks to "••••••" (sign hidden); `money(None)` / NaN gives "—", unmarked.
  - A string cut mid-span (a `[:N]` slice through the figure) masks to the end of its token (fail closed); stray markers are always removed.
  - Marker properties: not `isspace()`, unchanged by `html.escape`, `safe_text`, `md_bold_to_html(private=False)`, and split / join (`snapshot_context._clean`).
- **T2 ratchet (C0).** The rule flags the money f-string shapes (≈ then dollar then a comma spec; ± then dollar then a comma spec). It does not flag `money(x)`, a `# hv: price` line, or `notify.py`.
- **T3 sinks (C1).** With the anthropic client mocked as in `test_llm_request_golden.py`, a marked message reaches `messages.create` with no marker. With `requests.post` mocked, `send_email_resend` posts no marker in subject or html. `save_trade` with a marked `notes` and a nested `decision_context` writes no marker. `tests/fixtures/llm_requests_golden.json` is unchanged.
- **T4 render boundary (C2).**
  - An AST test: every `_md_bold(` call in app.py passes `private=`.
  - The import alias `md_bold_to_html as _md_bold` is unchanged and `_md_bold` is never bound elsewhere in app.py, so the escaper credit holds; pin it.
  - `md_bold_to_html(marked, private=True)` masks before escaping, and bold still converts.
  - **Invariant "toggling Hide values never changes the AI Snapshot prompt or hash":** `brief_calls_lines` and `calls_hash` are identical for one view regardless of `private`, and `section_html(view, private=True)` masks. A `_fmt_action` trim reads "≈••••••" under `private=True` and the legacy text under False.
- **T5 producers (C3–C5), per module.**
  - (a) Existing pins compare `strip_money_marks(text)`, with wording unchanged.
  - (b) A mixed-string test asserts `mask_money(text, True)` keeps the price and hides the money: daily_briefing 3898/3899 keeps "/share" price and hides the trim; `earnings_advisor` hides the position and the ± move.
  - (c) After masking with `True`, the module's output on its test fixtures contains no dollar-digit figure outside its documented price set.
- **T6 Catalyst (C4).** No caller of `mask_plain_usd` is left in app.py; delete the function or keep it with its own test.
- **Not applicable:** the time-context matrix (no date logic) and one-freshness-per-number (no figure changes).

## 8. Live checks (laptop, after each deploy; no literal `**`, dollar signs as text)

- **C2:** the 🏠 Brief Review card reads "Trim N shares (≈••••••, P% of position) AND raise stop to" a visible price with Hide values on, and the real figure after 👁 Show values; 🧾 Summary the same; the AI Snapshot doesn't say its calls changed after a toggle.
- **C3:** a Brief ⚠️ risk flag, 🔧 Tune-up and 🔗 Risk Analysis cards show •••••• for every dollar amount and keep any stop / price; the next morning email still shows real dollars.
- **C4:** 🔔 Catalyst Pre-Earnings Action reads "a •••••• position = ••••••" and shows its stop price; 📅 Macro playbook reads "sell N shares (≈••••••)".
- **C5:** ⚖️ Rebalancer rationale "(≈••••••)"; 📊 Performance opportunity cost and the "$ Added" cells masked.
- **After C1:** a trade logged from a Brief card has notes with no invisible characters (read-only: compare `length(notes)` with the visible text on the newest row).

## 9. Risks / coordination

- **Phase A overlap:** `_fmt_action` is shared with the AI Snapshot prompt (see §2). The Signals `action_text` and the Rebalancer expander label are app.py-built: Phase A owns them, so confirm they're covered there and not double-handled here.
- **XSS ratchet:** don't shadow `_md_bold` (§3). Raw sites moved onto `_md_bold` burn down the dynamic-HTML baseline, which is a gain, but re-key the baseline keys and never `--init` (memory `baseline_rekey_on_rename`).
- **Fail direction:**
  - an unmarked figure shows money (status quo), guarded by the ratchet;
  - a forgotten `private=` masks even when shown (visible, safe), guarded by the AST test;
  - a marker past a sink is stripped at all three exits.
- **Out of scope** (Phase C on owner ask): the HideValuesSweep modules, AI prose (Q3), and the `period_attribution.mask_usd` / `budget_view` mask paths, which already work and stay as they are.
