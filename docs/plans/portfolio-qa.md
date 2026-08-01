# Portfolio Q&A (💬 Ask) — Design Plan

**Date:** 2026-08-01
**Author:** Ajay Kumar
**Analysis/build model:** Claude Sonnet 5
**Opus review:** Round 1 — FIX-FIRST (1 blocking: `save_recommendations`'s optional-column retry stripped ALL optional columns, incl. the already-working `s_score`/`avg_sent`, on any column-missing error — would have silently stopped sentiment persistence during the window before the pillar-score DDL is applied; fixed via a two-generation strip order in `_with_retry`). 3 non-blocking (unwired `QA_MAX_RANGE_DAYS`, BUY full-lot-mark ambiguity, fixed `period="1y"` misreporting on old recs) — all fixed same session. Round 2 (verification) — SHIP, 0 blocking (1 trivial non-blocking: hoist a `330`-day literal to a named constant — done).
**Status:** SHIPPED 2026-08-01.

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
