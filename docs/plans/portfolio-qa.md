# Portfolio Q&A (💬 Ask) — Design Plan

**Date:** 2026-08-01
**Author:** Ajay Kumar
**Analysis/build model:** Claude Sonnet 5
**Opus review:** Round 1 — FIX-FIRST (1 blocking: `save_recommendations`'s optional-column retry stripped ALL optional columns, incl. the already-working `s_score`/`avg_sent`, on any column-missing error — would have silently stopped sentiment persistence during the window before the pillar-score DDL is applied; fixed via a two-generation strip order in `_with_retry`). 3 non-blocking (unwired `QA_MAX_RANGE_DAYS`, BUY full-lot-mark ambiguity, fixed `period="1y"` misreporting on old recs) — all fixed same session. Round 2 (verification) — SHIP, 0 blocking (1 trivial non-blocking: hoist a `330`-day literal to a named constant — done).
**Status:** Tier 1 usability pass SHIPPED 2026-09-05 — three new snapshot intents (`holding_lookup`, `portfolio_summary`, `sector_composition`), read-only over the already-computed `port_df`, no gate/threshold/DB change. `portfolio_summary` was deliberately scoped to a snapshot of right now, not a time-boxed return (a specific-past-period question routes to `unsupported` rather than reinventing the app's existing Modified-Dietz period-return methodology under the same name). **Same-day live screenshot review caught two real defects, both fixed:** a tz-mismatch bug that had silently broken `rec_outcome`'s forward price-move calculation for every query since 2026-08-01 (not just old recs — `fetch_price_history`'s tz-aware index vs a tz-naive comparison Timestamp raised inside a broad except-block), and a narrator hallucination (summed several given dollar figures into a new total, then mislabeled it 1000x too large — "$14.9M" instead of ~$14,860) that also surfaced a second gap (the narrator confidently called a 2.5-month-old recommendation "very recent" despite never being told today's date). `_NARRATE_SYSTEM_PROMPT` gained three guardrails as a result: never combine facts into a new total or rescale a dollar figure, never characterize date recency, use the facts' own terminology (sector ≠ position). **Fixing the tz bug immediately surfaced a third, dependent crash** (masked by the tz bug since 2026-08-01): `price_at_horizon` computing while `price_at_surface`/`pct_move` stayed `None` (no starting price on record) crashed `facts_to_text()`'s `:+.1f` format spec on `None` — fixed by formatting each independently instead of assuming they travel together. **A fourth gap, same day:** `rec_outcome` never mentioned a ticker currently held — the only holding-adjacent check was `acted_on` (a BUY within the Pre-Mortem match window of THIS recommendation), a different question from "is this held right now." Added an optional `port_df` param + `currently_held`/`current_shares` facts, narrated as a standalone line independent of `acted_on`. Full detail in `docs/requirements.md` F-225. Original v1/v1.1/v2/v2.1 SHIPPED 2026-08-01/2026-08-02 below.

> **One-line spec:** A retrospective natural-language Q&A tab over trade history and
> past recommendations — NOT a live `session_state` reader — that answers two
> question shapes: "how many trades did I make in a date range, and what was the
> gain/loss on each" and "why did a past recommendation lose/gain money."

---

## Why retrospective, not live-cache

This was originally brainstormed as a prompt bar reading whatever's already in
`session_state` this session (port_df, risk cache, alerts, etc.). The user's actual
example questions turned out to be different and harder — questions over **history**:
trades in a past date range, and why a specific past recommendation's outcome
diverged from its score. Those aren't answerable from live caches; they need the
`recommendations` and `trades` tables.

## A real data gap it surfaced

`recommendations` only ever stored a **bare `composite_score`** — the 4-pillar
breakdown (technical / business-quality / valuation / sentiment) that explains *why*
a score landed where it did was computed in memory at brief-build time
(`app.py`'s `t_score`/`bq_score`/`val_score`/`s_score`) and discarded, never
persisted. Decided with the user: **add pillar persistence now** (three new nullable
columns, forward-only — existing rows are not backfilled) so future "why" answers
aren't capped at composite-only forever.

## Architecture

Two-step LLM pattern, deliberately **not** an agentic tool-calling loop — the query
shapes are fixed and few, so a tool loop would add cost/failure surface for no
benefit. Same "fail open, never invent a fact" discipline as `thesis_red_team.py`.

1. **`parse_question()`** (Haiku) — free text → structured
   `{intent, ticker, start_date, end_date, horizon_days}`. Today's ET date is passed
   in explicitly so relative phrases ("last week") resolve against the app's actual
   calendar. A question that doesn't fit either shape, or a `rec_outcome` question
   with no resolvable ticker, returns `intent: "unsupported"` — no fuzzy guessing.
2. **Deterministic query** (plain Python, zero LLM):
   - `trades_in_range()` — SELLs report the already-stored `realized_pnl` directly;
     BUYs report unrealized P&L against the ticker's current price only if still
     held (caller supplies `current_prices` from the session's `_port_df_enriched`,
     keeping this a pure function with no fetch of its own); a BUY whose position
     has since been fully sold is labelled `"position_closed"` rather than
     attempting new FIFO-lot matching (its realized P&L lives on the matching SELL
     row instead).
   - `recommendation_outcome()` — exact ticker/date match against `recommendations`
     (never guesses the nearest row); returns `found: False` explicitly when
     nothing matches. Pulls `composite_score`/`conviction`/`thesis` and the new
     pillar columns (`None` for pre-2026-08-01 rows). Given a price-history
     DataFrame, computes the % move to `horizon_days` trading days after surfacing.
3. **`narrate_answer()`** (Haiku) — turns the result into plain English, instructed
   to use ONLY the given facts and state plainly when a pillar sub-score wasn't
   recorded rather than inferring a reason.

The parsed query is **shown back to the user as a caption before the answer**
(e.g. "Looking at: AAPL, surfaced 2026-07-20, +5 trading days") — cheap insurance
against a silent misparse producing a confidently wrong answer.

## Companion change: pillar-score persistence

`recommendations` gains `t_score`, `bq_score`, `val_score` (nullable; `s_score`
already existed). Captured at the same three `_rec_rows.append({...})` sites
(`new_pick`/`add_winner`/`buy_candidate`) that already capture `s_score`/`avg_sent`,
via three sibling helpers mirroring the existing lookup pattern proven in the
Rebalancer's `_trim_basis` "capped by <pillar>" caption. `save_recommendations`
drops these columns and retries on a schema-cache-miss error, same as every other
optional/inert-until-DDL column in this app — the recommendation log keeps working
exactly as before until the DDL is applied.

```sql
ALTER TABLE public.recommendations ADD COLUMN IF NOT EXISTS t_score  numeric;
ALTER TABLE public.recommendations ADD COLUMN IF NOT EXISTS bq_score numeric;
ALTER TABLE public.recommendations ADD COLUMN IF NOT EXISTS val_score numeric;
```

## Explicit scope decisions

- **Item #5 (auto-drafted periodic portfolio letter) is out of scope** — the user
  opposed enhancing `intelligence_report.py` and asked to leave it as-is.
- **No new table for the Q&A itself.** Fully reuses `recommendations` (+ the new
  columns) and `trades`. Logging Q&A history is a natural fast-follow, not built.
- **No fuzzy ticker/company-name resolution in v1** — only an explicitly named
  ticker is accepted; anything else returns `"unsupported"`.
- **Model tier: Haiku** for both LLM calls, matching every other interactive
  (non-batch) AI surface in the app. No day-caching of answers — open-ended free
  text has low reuse, unlike ticker×date sentiment.

## Files

- `stock_analyzer/portfolio_qa.py` — new module (parse/query/narrate).
- `stock_analyzer/db.py` — `_REC_COLS`-adjacent payload + optional-column retry
  logic in `save_recommendations`; new DDL doc block.
- `app.py` — three pillar-capture helpers at the `recommendations` save site;
  new "💬 Ask" 7th tab on 🧠 AI Insights.
- `stock_analyzer/constants.py` — `QA_REC_OUTCOME_DEFAULT_HORIZON_DAYS` (5),
  `QA_MAX_RANGE_DAYS` (365).
- `tests/test_portfolio_qa.py` — 31 tests covering the parse validator, both
  deterministic query functions, the narration text builder, and the fail-open
  contract on every LLM call.

**Never gates, scores, or modifies any recommendation — display-only, same class
as the rest of AI Insights.**

---

## v1.1 — live-use fixes and a third question shape (same day)

Real usage right after ship surfaced three issues, all fixed same day:

1. **Silent LLM failure (commit `2394791`).** `parse_question()`/`narrate_answer()`
   caught and swallowed every exception with no logging anywhere — a live failure
   was completely undiagnosable. Added `LAST_PARSE_ERROR`/`LAST_NARRATE_ERROR`
   module-level globals (mirrors the pre-existing `analyst_intel.LAST_EXTRACT_ERROR`
   pattern), reset at the start of each call and set to the real reason on any
   failure path. The Ask tab now shows a "Details: ..." caption under the
   error/warning whenever a call returns `None`.

2. **JSON-parser bug (commit `efd1b12`).** The `LAST_PARSE_ERROR` diagnostics
   immediately caught a real one: Haiku opened a `` ```json `` fence, emitted valid
   JSON, then kept writing an explanation afterward with **no closing fence** — the
   parser's brace-hunting fallback only ran when the cleaned string did NOT already
   start with `{`, so this well-formed-looking case slipped through and broke
   `json.loads` on the trailing prose. Replaced with `_extract_json_object()`, a
   depth-counted balanced-brace scan that always runs regardless of what precedes
   or follows the JSON object. **`thesis_red_team.py`'s array-JSON parser
   (`parse_counter_evidence_response`) has the identical gap — not yet observed
   failing live, but the same bug class if it ever does.**

3. **Opaque declines + a missing third intent.** Testing showed 2 of 3 real
   questions silently declined with a generic "doesn't match" message and zero
   indication why:
   - *"What was my trade on CrowdStrike?"* — no date range, and the only two v1
     intents both require one dimension CrowdStrike's question didn't have
     (`trades_in_range` needs dates; `rec_outcome` needs "why did this
     recommendation..." framing). **Added a third intent, `trade_lookup`**
     ("what was my trade on X", no date needed) via a new `trades_for_ticker()`
     function — refactored `trades_in_range`'s per-row P&L logic into a shared
     `_row_outcome()` helper so both functions use identical logic, no duplication.
   - *"Why did Robin Hood lose money after being recommended?"* — "Robin Hood"
     (two words) wasn't confidently mapped to ticker `HOOD` by the parser, per the
     deliberate "never guess a ticker" rule. **This rule was kept as-is** — the
     fix is telling the user why it declined, not loosening the anti-hallucination
     guard.

   Every `unsupported` result now carries a `reason` string: Python-determined for
   the two structural gaps (`_REASON_NO_DATE_RANGE`, `_REASON_NO_TICKER` — precise
   and reliable, since Python already knows exactly which required field is
   missing), or the model's own short explanation for anything else that doesn't
   fit any of the three shapes (validated non-empty, capped to 200 chars — this is
   self-explanation of a parse decision, not a financial fact, so a plausible-but-
   imprecise model explanation carries low risk compared to fabricating a number).
   Shown directly in the UI instead of the old static three-bullet fallback.

New/changed files: `stock_analyzer/portfolio_qa.py` (`_row_outcome`, `_real_trades`,
`trades_for_ticker`, `_extract_json_object`, `LAST_PARSE_ERROR`/`LAST_NARRATE_ERROR`,
`reason` field, `trade_lookup` intent), `app.py` (Details captions, `trade_lookup`
branch, reason display), `tests/test_portfolio_qa.py` (grew from 31 to 50 tests,
including regression tests reproducing both live failures verbatim). No
`constants.py`/gate/scoring-formula change in this round, so no Opus review
required per CLAUDE.md hard rule #4 — full suite passing throughout.

---

## v1.2 — dollar-sign rendering bug (same day, commit follows af956c0)

Live test of the new `trade_lookup` intent (a real HOOD round-trip question)
answered correctly — the underlying arithmetic and P&L labeling were exactly
right — but part of the narration rendered in a garbled monospace font instead
of plain prose. Root cause: **Streamlit's `st.markdown` treats a `$...$` pair as
inline LaTeX math**, and the answer mentioned two dollar amounts ("$117.77" ...
"$110.00"), so everything between the first and second `$` was silently
swallowed into a math span.

Fixed inside `narrate_answer()` itself (not at the two `app.py` call sites) —
every literal `$` in the model's response is escaped to `\$` before the text is
returned, so any caller rendering it via `st.markdown` is safe regardless of how
many dollar amounts the narration mentions. Added a fake-`anthropic`-module test
helper (mirrors `tests/test_news_intelligence.py`'s `_install_fake_anthropic`)
so the escaping — and the real success/exception paths of both `parse_question`
and `narrate_answer`, previously only exercised via their no-api-key fail-fast
branch — now have genuine regression coverage. 7 new tests (50 → 57).

**Same latent bug likely exists everywhere else in the app that renders raw LLM
narration via `st.markdown`** (Weekly Debrief, Thesis Review, catalyst/regime
scenario narratives, Red Team counter-evidence, Monthly Report summary) — none
of those sites escape `$` either. Not fixed here (out of scope for this feature,
and those surfaces are already shipped/reviewed) — worth a dedicated audit if
raised again.

---

## v1.3 — business-language display, not raw dict keys (same day)

Live use flagged that "Show the underlying trades" rendered the fact dicts'
raw internal keys (`pnl`, `pnl_label`, `traded_at`, ...) directly as column
headers, and `pnl_label`'s raw enum values (`"realized"`, `"position_closed"`)
directly as cell content — developer-facing names, not something to show a
user. Added `format_trades_table()`, a display-only formatter (renames columns
to `Ticker`/`Action`/`Shares`/`Price ($)`/`Date`/`Gain/Loss ($)`/`Status`, maps
each `pnl_label` value to a plain-English status) called only at the render
site — the underlying fact-dict schema consumed by `narrate_answer()` and the
rest of the pipeline is unchanged. 3 new tests (57 → 60).

---

## v1.4 — mixed-format dates coerced to NaT (same day)

A user cross-check between the Ask tab's CRWD trade list and the Trade
Journal's own History table (ground truth) caught a real data bug: the
oldest CRWD row — a broker text-import trade whose `traded_at` is a bare
date (`"2026-07-07"`, no time/offset) saved alongside rows with full ISO
timestamps+offsets — showed literal `"NaT"` in the Date column.

Root cause, and the fix, are **already documented in this exact codebase**:
`app.py`'s own Trade History table (`_tj_tab_hist`, ~line 20688) carries a
comment explaining that `pd.to_datetime` infers a single format from the
*first* row of a column and silently coerces every row that doesn't match to
`NaT` via `errors="coerce"` — unless `format="ISO8601"` is passed, which
accepts any valid ISO 8601 variant per-row instead of locking to one inferred
shape. `portfolio_qa.py`'s `_real_trades()` was missing that argument.
Reproduced directly: `pd.to_datetime(["2026-07-07", "2026-07-14T14:22:00+00:00", ...], utc=True, errors="coerce")` turns the two full-timestamp rows to `NaT`
(the bare date lands first and its inferred format doesn't match the others);
adding `format="ISO8601"` parses all three correctly.

One-line fix (`format="ISO8601"` added to the one `pd.to_datetime` call in
`_real_trades()`, shared by both `trades_in_range()` and `trades_for_ticker()`)
+ 1 new regression test reproducing the exact mixed-format sequence (61 total).
**Lesson: any new module that parses `trades_df.traded_at` with `pd.to_datetime`
needs `format="ISO8601"` — this is now the second place in the codebase that
needed it, after `app.py`'s Trade History table.**

---

## v2 — "Complete the circle": reasoning, multi-turn, Pre-Mortem cross-reference (2026-08-02)

After v1.1–v1.4 shipped and the user confirmed the feature was working well,
three more ideas from the same brainstorm were approved to "complete the
circle." A fourth idea (a forward-looking "what's the current call on X"
intent) was explicitly considered and declined — see the **Parked** section
below; nothing was built for it.

**1. Reasoning surfacing.** `_row_outcome()` (shared by `trades_in_range()`
and `trades_for_ticker()`) now reads a trade row's `user_thesis`/`notes`/
`lesson` fields, joins whichever are non-empty into one short `reasoning`
line (`None` when nothing was recorded — most trades), and `_trade_list_lines()`
appends it inline. `format_trades_table()` renames it to a "Notes" column
(`"—"` when absent), keeping the same "never leak raw dict keys to the user"
discipline `format_trades_table` already had from v1.3.

**2. Multi-turn conversation.** The Ask tab is now built with
`st.chat_message`/`st.chat_input` — the first use of this Streamlit pattern
anywhere in the app — backed by `st.session_state["_qa_history"]` (a list of
round dicts: question, answer, intent, facts, plus whatever caption/error
state a round needs to re-render identically from history). A single render
function (`_qa_render_round`) draws both historical and just-answered turns,
avoiding a second, divergent rendering path. `parse_question()` and
`build_parse_prompt()` gained an optional `history` param — a bounded list of
`{"question","answer"}` pairs (last `QA_HISTORY_TURNS` = 3), assembled by the
caller from its own richer turn objects so the pure-function signature stays
Streamlit-free. **`narrate_answer()` deliberately still receives no
history** — conversation context only helps the parser resolve *what's being
asked* (e.g. "what about MSFT instead?"); it must never change *what's true*
in an answer, which stays scoped to the current query's facts alone.

**3. Pre-Mortem cross-reference on `rec_outcome`.** New
`_find_buy_trade_for_rec(trades_df, ticker, rec_date, window_days)` finds the
BUY trade a recommendation was plausibly acted on by — same ticker, action
BUY, `traded_at` within `[rec_date, rec_date + QA_PREMORTEM_TRADE_MATCH_WINDOW_DAYS]`
(earliest match if several) — returning the **full raw trade row**, unlike
`recommendations_history.match_recs_to_trades()`'s narrow same-day-only
projection (confirmed via research: that function isn't reusable here without
losing the premortem/notes fields). `recommendation_outcome()` gained an
optional `trades_df` param that merges in a **tri-state** `acted_on` (`None`
= trades_df not supplied/not checked, `False` = checked, no matching BUY,
`True` = found) plus the matched trade's `user_thesis`, `trade_notes`,
`trade_lesson`, `premortem_case_against` (list of `{"angle","argument"}` —
confirmed via research to be a *different* shape from `thesis_red_team.py`'s
`{"claim","severity","signal_basis"}`, not to be conflated), and
`premortem_commitment`. `facts_to_text()`'s `rec_outcome` branch narrates
these when present, and `_NARRATE_SYSTEM_PROMPT` gained one added instruction:
assess *retrospectively* whether the stated risk case/commitment materialized
("say 'unclear' if the facts don't clearly show it either way... never
suggest any future action") — the concrete, in-prompt enforcement of the
redline decision below.

**New constants:** `QA_HISTORY_TURNS` (3), `QA_PREMORTEM_TRADE_MATCH_WINDOW_DAYS`
(3) — both touch `constants.py`, so hard rule #4 applies (Opus review cited
in the shipping commit, same as v1).

**Tests:** 61 → 84 (23 new), covering reasoning inclusion/omission, the
bounded history block in `build_parse_prompt` (including that history is
actually sent in the system prompt via a capturing fake client), and
`_find_buy_trade_for_rec`/`recommendation_outcome` across all three
`acted_on` states plus the earliest-of-multiple-BUYs and outside-window
cases. Full suite 3072 passing; `check_constants_documented.py` PASS.

---

## v2.1 — closed-lot P&L mislabeling (same day, live bug found via screenshot)

Live validation of v2's reasoning-surfacing column caught something unrelated
but more serious in the same screenshot: an AAPL trade history with two
complete sell-then-rebuy round trips plus a fresh re-buy showed **all three**
historical BUY rows labeled "Unrealized (still held)" — including the two
already fully closed weeks earlier. Each showed a fabricated P&L computed
against *today's* price, duplicating value already correctly reported on
the matching SELL rows.

**Root cause:** `_row_outcome()`'s BUY branch only ever checked "is this
ticker held **anywhere** in the portfolio right now" (`current_prices.get
(ticker)` — a ticker-level check), never "is **this specific BUY's lot**
still open." Since AAPL was genuinely held again (the fresh re-buy), every
historical BUY row for AAPL passed that check, regardless of whether that
particular lot had been sold out entirely in the meantime.

**Fix:** new `_closed_shares_by_buy(trades_df)` calls the existing,
already-tested `investor_mirror.build_closed_lots()` FIFO BUY-to-SELL
matcher (already used elsewhere for Behavioral Fingerprint/Personalized
Discovery — reused rather than reimplementing lot matching a third time in
this codebase) over the ticker's **full** trade history, not the
date-range-filtered subset a query is displaying — a BUY inside a queried
range can be closed by a SELL outside it, and FIFO consumption order
depends on every earlier trade regardless of the query window. Builds a
`{(ticker, buy_date_str): total_closed_shares}` map; `_row_outcome()` now
checks it before falling back to the old ticker-level logic — a BUY row
whose own share count is fully covered by `total_closed_shares` is labeled
`"position_closed"` (no fabricated `pnl`) even if the ticker is currently
held again via a later re-buy. **Deliberately scoped to the fully-closed
case only** — a genuinely partially-sold lot still marks against the full
original share count, which the UI already carries an explicit caveat
caption for; that's a documented approximation, not the bug being fixed.

Robustness: `_closed_shares_by_buy` synthesizes a sequential `id` column
when the input lacks one (`build_closed_lots` sorts by `(timestamp, id)`;
test fixtures elsewhere in this file often omit `id`, real `db.load_trades()`
output always has it) and wraps the call in try/except returning `{}` on
any error — fail-open, so a Q&A answer never crashes over this.

4 new tests: the exact reproduced screenshot scenario, a partially-closed
lot still marking unrealized (confirming the deliberate scope boundary), a
closing SELL falling outside the queried range still being detected, and a
missing-`id` fixture not crashing. Tests 84 → 88; full suite 3076 passing
(one pre-existing test, `test_debate_agent.py`'s anthropic-import-failure
test, broke transiently mid-session because `anthropic` had been installed
into this dev venv for an unrelated Pyright/IDE reason, defeating that
test's reliance on the real import failing in this venv — uninstalled and
the suite is back to green; unrelated to this fix, noted for the record).

**No constants.py/gate/scoring-formula change, so CLAUDE.md hard rule #4
didn't strictly require an Opus review — got one anyway given the
P&L-correctness stakes: SHIP, 0 blocking** (2 non-blocking: softened an
over-precise docstring claim — a same-day multi-BUY-same-ticker collision
can, in principle, hide a still-open lot as closed, inheriting the same
date-only-key limitation `investor_mirror.build_closed_lots`/
`tax_advisor._build_open_lots` already have elsewhere in this codebase,
accepted rather than solved since it fails in the conservative direction
and is a rare shape; a reverse-split edge case also noted as an accepted
fail-open gap, not fixed).

### Parked — forward-looking "current status" intent (NOT built)

A fourth brainstormed idea — a `current_status` intent letting the Ask tab
narrate the engine's *already-decided* live state for a ticker (composite
tier, active WATCH/TRIM/EXIT, an active reduce call) — was explicitly
discussed and **declined for this pass**: "let's keep the redline intact and
not jump over it." Recorded here only as a provision so the idea isn't lost.

If ever picked up, the safe version is narration-only of state the engine has
already published (`_reduce_calls`, `exit_signals`' deterioration tier,
existing composite/pillar scores) — never a newly-computed verdict or an
action the engine hasn't already surfaced. This is a materially different
trust boundary from items 1–3 above (those narrate *history*; this would
narrate *live current state*), so it needs its own explicit go-ahead and its
own Opus design review before any code is written. **Do not build this from
this doc alone — treat it as requiring a fresh, explicit approval, not an
implied one just because it's documented here.** Trigger to revisit: an
explicit user re-ask, not a data/engineering milestone (a pure policy call,
unlike this app's other "parked" items which mostly wait on data volume or
observation windows).
