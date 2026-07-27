# Thesis Red Team Agent — Phase 2 Design Plan

**Date:** 2026-07-27
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** Plan SHIP 2026-07-27 (6 Opus rounds) — ready for implementation.

## Review log

| Round | Model | Verdict | Blocking findings |
|---|---|---|---|
| Round 1 | Claude Opus 4.8 | FIX-FIRST | 2 blocking — (1) raw `signals_snapshot` passthrough would hand Haiku a hardcoded `pt_pts=7.0` placeholder and a possibly-bootstrap `comp_delta` as if real, inviting a grounded-looking claim on fabricated evidence; (2) "mirrors `generate_case_against()` exactly" collapses the valid-empty-list (`[]`) result into the call-failure (`None`) case everywhere falsiness is tested, silently breaking the day-cache cost bound. Both resolved in v2. 4 non-blocking notes also folded in (cache-miss render-lag fix, premortem omit-path clarity, Surface 2 must sit inside the existing `if _db_ticker:` block, signal_basis validation scope stated explicitly). |
| Round 2 | Claude Opus 4.8 | FIX-FIRST | 1 blocking — v2's fix for Round 1 blocking #1 excluded raw `pt_pts` but re-admitted the identical placeholder LAUNDERED inside `erosion_score`/`erosion_label` (`compute_erosion_score()` always bakes `pt_pts=7.0` into the aggregate, `thesis_red_team.py:47,49`), so the score/label passed into the prompt still carried synthetic weight — and the "every value is real" justification for shape-only `signal_basis` validation was therefore false. Resolved in v3 by dropping `erosion_score`/`erosion_label` from the prompt inputs entirely; the bear case is built only from primitive, individually-real signals (tier, RS, composite delta, price/entry/age). 5 non-blocking notes folded in (reuse the page's existing `_thesis_by_ticker`/`_trade_date_by_ticker` lookup instead of a duplicate map; source entry price/age from that same lookup with an explicit America/New_York age computation; corrected an inaccurate "matches Phase 1's bar" claim — v2 actually tightens it; noted the new import needed for `THESIS_EROSION_HAIKU_MIN` + the two new functions in `app.py`; noted Surface 2's per-render cache read as accepted overhead, not a defect). |
| Round 3 | Claude Opus 4.8 | FIX-FIRST | 1 blocking — v3's builder signature typed `price`/`entry_price`/`position_age_days` as non-Optional, but the plan's own call site provably passes `price=None` (`_rt_live_price` is `None` whenever `_rt_tk_close` is empty) — a held name with an empty price bundle but a firing `exit_signals` tier + stored thesis would crash the WHOLE AI Insights render with an unguarded `TypeError`, since the per-ticker compute loop (`app.py:27508-27578`) has no surrounding try/except. Resolved in v4 by typing all three `| None` and specifying the omit-when-None convention for `price` explicitly (mirroring the existing `composite_delta` convention), with `entry_price` covered defensively even though thesis-present effectively guarantees it's set. 3 non-blocking notes folded in (softened the "call failed" caption copy for the one-time same-day transition case where a Phase-1-only cached row predates the Phase 2 deploy; corrected the plan's own import note — `date` is already imported at `app.py:9`, no action needed; removed a harmless redundant `_db_ticker and` guard in the Surface 2 snippet). |
| Round 4 | Claude Opus 4.8 | FIX-FIRST | 2 blocking, both root-caused by `is None` being the wrong sentinel for values whose real producers fail via `NaT`-strings/`NaN`-floats, not `None` — (1) the age computation's `_rt_trade_date` comes from `str(_brow.get("traded_at",""))[:10]` (`app.py:25426`); a null/`NaT` `traded_at` stringifies to a TRUTHY `"NaT"`/`"None"`, passing the v4 `if _rt_trade_date else None` guard and then crashing `date.fromisoformat()` unguarded inside the try/except-free compute loop; (2) `_rt_live_price`/`_rt_entry_price`/`_rt_comp_delta` can each be a non-`None` `NaN` (an un-dropna'd trailing NaN Close bar, a `NaN` `Score`/`price` cell) that sails past every `is None` omit-check and reaches the prompt as literal `"$nan"` — the same meaningless-value-laundering class Round 2 blocked on `pt_pts`, one layer deeper. Resolved in v5 by (a) wrapping the date parse in `try/except (ValueError, TypeError)` and (b) coercing every one of these three values to `None` at the call site whenever non-finite (`math.isfinite` check), so the existing omit-when-`None` convention uniformly covers both "missing" and "unusable." 2 non-blocking notes folded in (pinned `_rt_comp_lag_found` to the exact inner branch that must set it, `app.py:27541-27543`, not the outer `if _rt_lag_row:`; confirmed `_rt_tier`/`_rt_rs` are already clean and need no further guard). |
| Round 6 | Claude Opus 4.8 | **SHIP** | 0 blocking. 3 non-blocking: (1) `thesis_red_team.py` needs its own new imports (`import json`, `from stock_analyzer.constants import LLM_REQUEST_TIMEOUT_SEC`, mirroring `premortem_advisor.py:29-32`) — the app.py import note didn't cover the module's own file; (2) if a future edit computes a "% since entry" ratio from `entry_price`, guard against exactly `0.0` (finite but a bad divisor) — the current plan only uses price/entry/age as standalone display values, so this is a forward-looking guardrail, not a current defect; (3) a cosmetic line-number mismatch in the Round 5 cost-quirk note (`_rt_comp_today` reads the map built at `app.py:27476` but is itself assigned at `app.py:27532` — the NaN-passthrough substance is correct, only the citation was imprecise). All three folded in below. |
| Round 5 | Claude Opus 4.8 | FIX-FIRST | 1 blocking — v5's own "confirmed `_rt_rs` already clean" claim (from Round 4) was FALSE: `compute_relative_strength()` (`exit_advisor.py:143-145`) divides with no zero-denominator guard, and a degenerate `0.0` Close bar (same bad-bar family as the NaN-Close failures in memory `project_bundle_load_resilience` — `.dropna()` removes NaN but not `0.0`) makes the division return `inf`/`nan` WITHOUT raising, so the function's "0.0 on any error" try/except never fires and `rs_vs_spy` can reach the prompt as literal `"+infpp"`/`"nanpp"` — the fourth instance of the same laundering class Rounds 2-4 kept finding, this time on the one value the plan had explicitly (and wrongly) signed off as safe. Resolved in v6 by giving `rs_vs_spy` the identical `math.isfinite()` call-site coercion as the other three values, typing it `float \| None` in the builder, and removing the false "already clean" claim. 4 non-blocking notes folded in (show `_rt_comp_lag_found = False` explicitly initialized in the snippet, next to `_rt_comp_lag = 0.0`, so an implementer can't hit a `NameError` if the lookback loop never enters its inner branch; state `generate_counter_evidence()`'s fail-open contract explicitly — api-key check + a wrapping try/except around the entire Anthropic call, mirroring `premortem_advisor.generate_case_against()` — rather than leaving it "by analogy"; acknowledged that a `NaN` `_rt_comp_today` can still inflate `comp_pts` to a spurious +25 in the erosion SCORE itself, which is Phase 1 scope and not a fabrication risk since the bad delta is separately excluded from the Haiku prompt, but worth a one-line note so it isn't mistaken for a leak; normalized `premortem_commitment` capture with the same `str(...).strip()` treatment as `user_thesis`, for symmetry, even though `load_trades()`'s None-backfill already makes this a theoretical-only gap). |

> **One-line spec:** Add a Haiku-generated bear-case narrative (2-3 grounded
> counter-arguments) to the erosion score already shipped in Phase 1, closing
> the loop with the Pre-Mortem Protocol by reading back the investor's own
> `trades.premortem_commitment` as context. Second surface: a read-only
> narrative expander on existing deterioration TRIM/EXIT cards.

---

## Gate status (confirmed before drafting this plan)

The original plan (`docs/plans/thesis-red-team-agent.md:371,374-377`) set two
gate conditions before Phase 2 could start:

1. **~1 week of production observation** (target ~2026-07-30). Today is
   2026-07-27, three days early.
2. **A dedicated Opus review of the Phase 2 Haiku prompt + output schema**
   (not yet run — this plan is what that review will cover).

**User decision (2026-07-27, confirmed explicitly):** proceed now rather than
wait 3 more days — same forward-only precedent as Multi-Agent Debate Phase 3.
Condition 2 is satisfied by this plan going through the same Opus
design-review gate every other phase in this codebase has used; condition 1
was a calendar buffer, not a correctness dependency — nothing in the Phase 2
build (below) actually depends on the 5-session composite-delta bootstrap
having cleared. **What the observation window WAS actually checking, and its
real status:**
- Composite-delta bootstrap clearing → **irrelevant to Phase 2.** The Haiku
  trigger condition is `erosion_score >= THESIS_EROSION_HAIKU_MIN`, computed
  fresh from whatever `signals_snapshot` values exist that day (bootstrap
  artifact or real delta) — Phase 1 already handles that gracefully, and nothing
  about the Haiku call cares whether the delta component is inert.
- Score distribution stays directionally sensible → **already independently
  confirmed** by Phase 1's own same-day production validation
  (`docs/plans/thesis-red-team-agent.md:359-364`): FSLR 44/Softening, TEAM
  38/Softening, four intact names at 7/Intact. FSLR and TEAM are both already
  above `THESIS_EROSION_HAIKU_MIN=30` — meaning Phase 2, once shipped, has
  real trigger targets from day one, not a cold start.

---

## What already exists (verified against HEAD, not assumed)

- **`thesis_red_team.py`** (Phase 1, shipped): `compute_erosion_score()` —
  pure, no Streamlit. Module docstring still says "Phase 2 ... is added
  separately after Phase 1 validates in production" — update once Phase 2
  ships.
- **`exit_advisor.compute_relative_strength()`** (`stock_analyzer/exit_advisor.py:127`):
  ticker 20-day return minus SPY 20-day return, pct-pts, 0.0-safe.
- **`thesis_erosion_cache` table** (`db.py:2429-2477`): PK `(ticker,
  score_date)`. Columns actually read/written by Phase 1:
  `erosion_score, erosion_label, counter_evidence, signals_snapshot`.
  `counter_evidence` column already exists (jsonb, nullable) — **no new DDL
  needed for Phase 2**, only `save_thesis_erosion_cache()`'s existing
  `counter_evidence=None` default gets populated.
- **`app.py:27443-27692`** (`with _ai_tab_rt:`, "⚠️ Red Team" tab): per held
  ticker, on cache miss: loads today's `exit_signals` tier, computes RS via
  `compute_relative_strength()`, reads `_port_df_enriched` composite,
  computes 5-session lag from its own cache, hardcodes `pt_pts=7.0` (PT
  revision inert — `analyst_target_snapshots` not yet wired in), calls
  `compute_erosion_score()`, builds `signals_snapshot = {composite_today,
  comp_delta, rs_vs_spy, tier, pt_pts}`, saves, renders. On cache hit: just
  renders `_rt_cached`. The `if _rr.get("counter_evidence") is None:` branch
  at `app.py:27688-27692` already shows a "Phase 1 — quantitative signals
  only" caption — this is the exact seam Phase 2 fills.
- **`_render_act_card()`** (`app.py:6611-6809`): renders deterioration
  TRIM/EXIT/stop-breach cards on 🏠 Home Act Today. The existing "⚔️ Challenge
  This Exit" block (`app.py:6708-6809`) is gated on
  `_db_item.get("kind") in ("deterioration_trim", "deterioration_exit")` —
  this is the correct, already-proven gate for "this card IS a judgment-call
  deterioration signal" (confirmed today: mechanical `stop_breach` cards
  correctly get no debate button, same reasoning applies to Surface 2 below).
- **`premortem_advisor.py`** (existing, F-187): the house template for a
  fail-open, strictly-validated Haiku counter-argument call — `generate_case_against()`
  returns `None` on ANY failure (no key, API error, malformed/short/generic
  JSON), `_parse_case_against()` enforces exact shape before anything reaches
  the UI. Phase 2 mirrors this shape exactly (see below), not the multi-round
  `debate_agent.py` pattern — this is a single-shot narrative call, not a
  Bull-vs-Bear exchange.
- **`trades.premortem_commitment` / `trades.user_thesis`** lookup pattern:
  the exact "most recent BUY row with a non-empty value, newest-first,
  falling back to the most recent BUY if none has one" fix already applied
  in D2's exit-debate handler (`app.py:6774-6788`, fixed 2026-07-27, commit
  `d19cf9b`) — Phase 2 reuses this identical lookup for `premortem_commitment`
  (added as a second field pulled from the same row, not a second query).
- **`THESIS_EROSION_HAIKU_MIN=30`** (`constants.py:885`, already shipped in
  Phase 1) — the Phase 2 trigger threshold is already in `constants.py`. No
  new constant needed for the trigger itself.

---

## Design principles (carried over from the Phase 1 plan, still binding)

1. Strictly additive — counter-evidence narrative never modifies the erosion
   score, composite score, gate decisions, or any recommendation.
2. Day-cached, ET-keyed — at most one Haiku call per ticker per trading day
   (enforced by the existing cache-miss branch; Phase 2 adds no new session
   counter since the day-cache already bounds cost, unlike the
   user-triggered debate buttons).
3. Graceful degradation — a failed/invalid Haiku response leaves
   `counter_evidence=None` and the score still renders exactly as Phase 1
   already does today.
4. Never fabricates — every counter-argument must cite a specific value from
   the supplied signal set (`signal_basis`); a generic argument is a
   validation failure, same bar as `premortem_advisor.py`.
5. Scoped to held positions only — unchanged from Phase 1.

---

## Haiku call — `generate_counter_evidence()` (new, in `thesis_red_team.py`)

Mirrors `premortem_advisor.generate_case_against()`'s signature and
fail-open contract exactly:

```python
def generate_counter_evidence(
    ticker: str,
    inputs: dict,
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 600,
) -> list[dict] | None:
    """Returns validated [{claim, severity, signal_basis}] (0-3 items) or
    None on any failure. Temperature 0 (structured output)."""
```

**Fail-open contract — stated explicitly, not left "by analogy" (Round 5
non-blocking fix):** because this function's sole call site
(`app.py`, cache-miss branch) sits inside the per-ticker compute loop that
has NO surrounding try/except (`app.py:27508-27578`), the ENTIRE feature's
crash-safety depends on this function itself never letting an exception
escape. It must mirror `premortem_advisor.generate_case_against()`'s full
shape, not just its signature:
```python
def generate_counter_evidence(ticker, inputs, api_key, model="claude-haiku-4-5-20251001", max_tokens=600):
    if not api_key:
        return None
    try:
        import anthropic
        client   = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model, max_tokens=max_tokens, temperature=0,
            system=inputs[0], messages=[{"role": "user", "content": inputs[1]}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        text = response.content[0].text.strip() if response.content else ""
        return parse_counter_evidence_response(text)
    except Exception:
        return None
```
The `try` block must wrap the entire API call + parse, exactly like
`premortem_advisor.py:274-298` — a Sonnet implementer who copies only the
signature and the JSON-parsing logic, but lets an import error, network
timeout, or malformed-response exception propagate, would crash the whole
AI Insights page on the very first API hiccup.

**Trigger conditions (all three, checked at the existing cache-miss site,
`app.py:27508-27578`):**
1. `user_thesis` non-empty for this ticker (most-recent-BUY lookup above)
2. `_rt_erosion["score"] >= THESIS_EROSION_HAIKU_MIN`
3. Cache miss for `(ticker, score_date)` — already the only branch this runs in

**Inputs (`build_counter_evidence_inputs()`, new, in `thesis_red_team.py`) —
explicit named parameters, NOT a raw `signals_snapshot` passthrough (Round 1
blocking finding):**
```python
def build_counter_evidence_inputs(
    ticker: str,
    price: float | None,                # None → empty price bundle for this
                                         # ticker; omit the price line entirely
    entry_price: float | None,          # None-safe defensively (see below);
                                         # effectively always set in practice
    position_age_days: int | None,      # None-safe defensively, same reason
    user_thesis: str,
    premortem_commitment: str | None,   # None/empty → omitted from prompt
    tier: str | None,                   # WATCH/TRIM/EXIT/None
    rs_vs_spy: float | None,            # None → non-finite at the call site
                                         # (see Round 5 fix below); omit
    composite_delta: float | None,      # None → 5-session cache not yet
                                         # bootstrapped, OR non-finite; omit,
                                         # don't pass 0 or a placeholder value
) -> tuple[str, str]:
```
**Note `erosion_score`/`erosion_label` are NOT parameters** — see the Round
2 fix below; this is deliberate, not an oversight.

**Round 3 fix — `price`/`entry_price`/`position_age_days` must be `None`-safe,
not typed as guaranteed-present:** the wiring code below computes
`_rt_live_price = float(_rt_tk_close.iloc[-1]) if not _rt_tk_close.empty else None`
— a held ticker with an empty price bundle in `_last_held_data` (data outage
for that name specifically) can still have a firing `exit_signals` tier and
a stored thesis, satisfying the Haiku trigger while `price=None`. The
per-ticker compute loop (`app.py:27508-27578`) has no surrounding
try/except, so an unguarded `f"${price:.2f}"` in the prompt-formatting body
would crash the entire AI Insights render for every ticker, not just this
one. **`build_counter_evidence_inputs()` must omit the price line from the
prompt whenever `price is None`** — same "state what's missing" convention
already used for `composite_delta`/absent macro context. `entry_price` and
`position_age_days` are effectively always set at the call site (a stored
`user_thesis` implies a BUY row exists, which is exactly what populates
`_entry_price_by_ticker`/`_trade_date_by_ticker` — see Wiring below), but
the builder should guard them the same defensive way regardless, so the
function's own contract doesn't silently depend on that invariant holding
forever.

**Three values excluded from the prompt entirely, not merely unused —
excluding them is the fix for Round 1 blocking finding #1 and Round 2
blocking finding #1:**
- **`pt_pts` is never passed.** It is a hardcoded `7.0` placeholder in
  today's shipped code (`app.py:27547`, "PT revision: inert (7=flat) until
  `analyst_target_snapshots` accumulates") — not a real signal yet. Handing
  it to a model instructed to ground every claim in "a specific value from
  the evidence" risks a citation like "flat analyst PT revision" built on a
  number that means nothing. No analyst-PT line appears in the prompt at all
  until PT wiring is real (separate, later work) — same "state what's
  missing" convention `_format_case_against_prompt` already uses for absent
  macro/earnings context (`premortem_advisor.py:190,201`).
- **`composite_delta` is passed as `None` (and omitted from the prompt) any
  time the 5-session lookback (`app.py:27536-27544`) did NOT find a real
  prior cache row** — i.e. exactly the same condition the Phase 1 tab itself
  already flags as a bootstrap artifact (`app.py:27681-27686`, "large
  positive delta because the 5-session history is still building"). The
  caller must track whether the lag lookup actually found a row (not just
  whether `comp_delta > 20`, which is a display heuristic, not the real
  bootstrap flag) and pass `None` through when it didn't.
- **`erosion_score`/`erosion_label` are never passed, at all (Round 2 fix
  — this is the important one).** Round 1's fix excluded raw `pt_pts` but
  Round 2 caught that the *aggregate* still launders it back in:
  `compute_erosion_score()` unconditionally adds `pt_pts` into the score
  (`thesis_red_team.py:47,49` — `pt_pts = max(0, min(15, 7.0)) = 7.0` every
  time until PT wiring is real, then `score = tier_pts + rs_pts + comp_pts +
  pt_pts`), so the score/label the model would see always carries a fixed
  +7 synthetic component and can sit one display-band too high near a
  boundary. Passing them would also make the bear case circular (explaining
  the erosion score BY CITING the erosion score) and would falsify the
  Validation-scope note below's "every value is real" claim. **The bear
  case is built only from the primitive, individually-real, fully
  decomposable signals** — tier, RS vs SPY, composite delta (when real),
  price/entry price/position age — never the rolled-up score/label. The
  Red Team tab UI still shows the score/label (that's Phase 1, unchanged);
  only the Haiku prompt excludes them.

Returns `(system_prompt, user_prompt)` — same split as
`_CASE_AGAINST_SYSTEM_PROMPT` / `_format_case_against_prompt` in
`premortem_advisor.py`.

**Forward-looking guardrail (Round 6 non-blocking note):** `price`/
`entry_price` are used here as standalone display values only (e.g. "current
price $X, entry $Y") — NOT combined into a computed ratio like "% since
entry." If a future edit ever adds such a ratio, `entry_price == 0.0` passes
the `math.isfinite()` coercion (it's finite) but is a bad divisor — guard
that separately if it's ever introduced. Not a defect in this plan as
written, since no ratio is computed here.

**System prompt (adapted from the Phase 1 plan's draft, tightened to match
`premortem_advisor.py`'s proven anti-generic bar):**

> "You are a bear-case analyst reviewing a position the investor already
> holds. Using ONLY the signals given below, identify the 2-3 strongest
> SPECIFIC counter-arguments the data currently supports against the
> original thesis. Every counter-argument MUST reference a specific value
> from the evidence given (a number, a date, a signal name) — a generic
> statement that could apply to any stock ('markets are unpredictable', 'risk
> exists') is a FAILURE of this task. If the investor's pre-mortem commitment
> is given and current evidence supports it, say so explicitly, quoting the
> commitment. If the signals are too weak to support any grounded
> counter-argument, return an empty array — do not invent one. Do not
> recommend selling and do not hedge with disclaimers; describe what the
> data currently shows, not what to do about it. Respond with ONLY a JSON
> array, no other text: `[{\"claim\": str, \"severity\": \"low\"|\"medium\"|\"high\", \"signal_basis\": str}]`
> — max 3 items, min 0."

**Output validation — `parse_counter_evidence_response()`, new, in
`thesis_red_team.py`** (same strip-fences / bracket-slice parse as
`premortem_advisor._parse_case_against`, adapted for a variable-length 0-3
array instead of a fixed 3):
- Must parse as a JSON list, length 0-3 (an empty list is a VALID, non-failure
  result — "no grounded bear case today" — distinct from `None`, which means
  the call itself failed or returned garbage)
- Each item: `claim` (non-empty str), `severity` ∈ {low, medium, high},
  `signal_basis` (non-empty str)
- Any item failing validation → the whole response is dropped, return `None`
  (don't save a partially-valid list — this TIGHTENS the original Phase 1
  plan's spec, which called for per-item dropping with partial survival
  possible, `docs/plans/thesis-red-team-agent.md:178`; all-or-nothing is the
  stricter, correct choice here since it matches the proven house template
  `premortem_advisor._parse_case_against`, which also returns `None` on any
  single bad item, `premortem_advisor.py:243,247` — Round 2 correction: the
  earlier wording in this plan mischaracterized this as "already matching"
  Phase 1's bar when it actually tightens it)

**Contract fix (Round 1 blocking finding #2 — do not skip):** the house
template this mirrors, `premortem_advisor.generate_case_against()`, does
`if not case_against: return None` (`premortem_advisor.py:290`) — a correct
choice THERE because that function always requires exactly 3 items, so an
empty result is never valid. Phase 2 is different: `[]` IS a valid outcome
(the model genuinely found no grounded bear case). **`generate_counter_evidence()`
and `parse_counter_evidence_response()` must therefore distinguish `[]` from
`None` explicitly — never `if not result:`, always `if result is None:` for
failure and `if result is not None:` for "the call completed" (including the
empty-list case).** This applies at every call site:
- the trigger/save logic in `app.py` (below)
- `save_thesis_erosion_cache(..., counter_evidence=result)` — persist `[]`
  as-is so the day-cache actually holds (an unpersisted `[]` would silently
  re-trigger the Haiku call on every page load that day, defeating the cost
  bound this plan claims)
- both render sites (Red Team tab + Surface 2 expander) — test
  `is not None` before iterating, and render an explicit "no grounded bear
  case found today" message for the `[]` case (distinct from "Bear case
  unavailable — call failed" for the `None` case)

**Validation-scope note (non-blocking, stated explicitly per Round 1;
strengthened per Round 2):** `signal_basis` validation here is a non-empty-
string shape check only — NOT a mechanical verification that the cited value
matches a real supplied number (unlike D1/O1's two-layer ticker+evidence-
quote guards, which apply to free-prose thesis clustering). This is
acceptable ONLY because, with `pt_pts`, bootstrap `comp_delta`, AND
`erosion_score`/`erosion_label` all excluded from the prompt (see the three
exclusions above — Round 2 added the third), every remaining value in the
prompt is genuinely real/primitive — there is nothing synthetic or
aggregate left for the model to cite. If a future input is ever added to
this prompt that isn't guaranteed real and primitive, this validation gap
must be revisited.

**Model:** `claude-haiku-4-5-20251001`. **Max tokens:** 600. **Temperature:** 0.

---

## Pre-Mortem loop (closes the loop with F-187)

`build_counter_evidence_inputs()` includes `premortem_commitment` text
whenever the most-recent-BUY row (same lookup as D2's exit-debate fix) has a
non-empty value. The system prompt explicitly instructs the model to quote
the commitment back when current evidence supports it — this is the "closes
the loop" behavior the original Phase 1 plan called out
(`docs/plans/thesis-red-team-agent.md:43-46`). If `premortem_commitment` is
empty (pre-dates the 2026-07-17 Pre-Mortem ship, or the position was opened
via a non-interactive path that never ran that gate), the field is simply
omitted from the prompt — same "state what's missing, don't invent" pattern
`_format_case_against_prompt` already uses for absent macro/earnings context.

---

## Wiring — `app.py:27443-27692` (Red Team tab)

**Thesis/premortem lookup: EXTEND the page's existing lookup, don't build a
new one (Round 1 non-blocking fix, corrected per Round 2):** AI Insights
already builds `_thesis_by_ticker` and `_trade_date_by_ticker` ONCE,
unconditionally, for every held ticker, before the tabs are even created
(`app.py:25416-25430` — the Red Team tab is nested inside this scope, so
both dicts are already in scope by the time `with _ai_tab_rt:` runs). Round
2 flagged that inventing a second, separate lookup (the v2 draft's
`_rt_thesis_map`, built via the D2 exit-debate's slightly different
`.str.upper() == "BUY"` match) risks the two page-level lookups silently
drifting apart over time. **Fix: extend the existing loop's body
(`app.py:25422-25430`) to also capture two more fields from the same
iteration:**
```python
_premortem_by_ticker: dict = {}
_entry_price_by_ticker: dict = {}
...
for _, _brow in _buys.iterrows():
    _bt = str(_brow["ticker"]).upper()
    if _bt not in _trade_date_by_ticker:
        _trade_date_by_ticker[_bt]   = str(_brow.get("traded_at", ""))[:10]
        _entry_price_by_ticker[_bt]  = _brow.get("price")   # truly-most-recent BUY, regardless of thesis
    if _bt not in _thesis_by_ticker:
        _th = _brow.get("user_thesis")
        if _th and str(_th).strip():
            _thesis_by_ticker[_bt] = str(_th).strip()
            _pm = _brow.get("premortem_commitment")
            if _pm and str(_pm).strip():
                _premortem_by_ticker[_bt] = str(_pm).strip()  # SAME row as the thesis
```
**Round 5 non-blocking symmetry fix:** normalize `premortem_commitment` with
the same `str(...).strip()` treatment as `user_thesis`, rather than storing
the raw cell value — `load_trades()`'s None-backfill (`db.py:1124-1129`)
already makes a stray float `NaN` here a theoretical-only gap, but matching
the existing `user_thesis` pattern exactly costs nothing and removes the
asymmetry entirely.
**Note the two captures are deliberately NOT from the same row when the most
recent BUY lacks a thesis** — `_entry_price_by_ticker`/`_trade_date_by_ticker`
always reflect the true most-recent BUY (for an accurate "position age"),
while `_thesis_by_ticker`/`_premortem_by_ticker` reflect the most recent BUY
that actually HAS a thesis (falling back to an older row if needed) — this
mirrors the existing, already-shipped distinction between these two dicts in
the unmodified code, not a new inconsistency introduced by Phase 2.
`position_age_days` — America/New_York via `_today_et()`, per CLAUDE.md's
date-math rule. **Round 4 fix: the parse must be exception-guarded, not
truthiness-guarded** — `_trade_date_by_ticker[ticker]` is
`str(_brow.get("traded_at",""))[:10]` (`app.py:25426`), and a null/`NaT`
`traded_at` (a legacy-backfilled or otherwise incomplete row) stringifies to
a TRUTHY `"NaT"` or `"None"`, which `date.fromisoformat()` cannot parse:
```python
try:
    _rt_age_days = (_today_et() - date.fromisoformat(_rt_trade_date)).days
except (ValueError, TypeError):
    _rt_age_days = None
```
Never rely on `if _rt_trade_date else None` alone — that check is truthy for
`"NaT"`/`"None"` strings and would let `fromisoformat` raise unguarded
inside the try/except-free compute loop.

Both the trigger logic (cache-miss branch) and the render loop (cache-hit
and cache-miss alike) read from these same three dicts, so captions are
consistent regardless of which branch a given ticker took — same benefit
the v2 draft's invented map was reaching for, without a second lookup.

**Trigger + save — inserted between the existing `compute_erosion_score()`
call (`app.py:27550-27552`) and the `save_thesis_erosion_cache()` call
(`app.py:27564-27570`), cache-miss branch only:**

```python
import math

_rt_thesis_text     = _thesis_by_ticker.get(_rt_ticker)
_rt_premortem_text  = _premortem_by_ticker.get(_rt_ticker)
_rt_entry_price     = _entry_price_by_ticker.get(_rt_ticker)
_rt_trade_date      = _trade_date_by_ticker.get(_rt_ticker)
_rt_counter_evidence = None   # None until a call is actually attempted
if _rt_erosion["score"] >= THESIS_EROSION_HAIKU_MIN and _rt_thesis_text:
    _rt_api_key = (st.secrets.get("anthropic") or {}).get("api_key", "")
    try:
        _rt_age_days = (_today_et() - date.fromisoformat(_rt_trade_date)).days
    except (ValueError, TypeError):
        _rt_age_days = None

    _rt_live_price = float(_rt_tk_close.iloc[-1]) if not _rt_tk_close.empty else None
    if _rt_live_price is not None and not math.isfinite(_rt_live_price):
        _rt_live_price = None
    if _rt_entry_price is not None and not math.isfinite(float(_rt_entry_price)):
        _rt_entry_price = None
    _rt_comp_delta_clean = _rt_comp_delta if _rt_comp_lag_found else None
    if _rt_comp_delta_clean is not None and not math.isfinite(_rt_comp_delta_clean):
        _rt_comp_delta_clean = None
    _rt_rs_clean = _rt_rs if math.isfinite(_rt_rs) else None   # Round 5 fix — see below

    _rt_inputs = build_counter_evidence_inputs(
        ticker=_rt_ticker,
        price=_rt_live_price,   # from _rt_tk_close, already computed above for RS (app.py:27524-27530)
        entry_price=_rt_entry_price, position_age_days=_rt_age_days,
        user_thesis=_rt_thesis_text, premortem_commitment=_rt_premortem_text,
        tier=_rt_tier, rs_vs_spy=_rt_rs_clean,
        composite_delta=_rt_comp_delta_clean,
    )
    _rt_counter_evidence = generate_counter_evidence(_rt_ticker, _rt_inputs, _rt_api_key)
    # _rt_counter_evidence is now: a list (0-3 items, valid — including [])
    # or None (call failed). Never test truthiness downstream — see the
    # None-vs-[] contract above. Note erosion_score/erosion_label are used
    # ONLY for the trigger comparison above — never passed into the builder.
```

**Round 4/5 fix — `is None` alone is the wrong sentinel for these FOUR
values; each must also be checked for `NaN`/`inf` before being treated as
"present":**
- `_rt_live_price` — `_rt_tk_close` (`app.py:27525-27529`) is NOT
  `.dropna()`'d before `.iloc[-1]`, so a trailing NaN Close bar (a known
  failure mode, see memory `project_bundle_load_resilience`) yields
  `float(nan)`, which is truthy-non-`None` and would otherwise reach the
  prompt as literal `"$nan"`.
- `_rt_entry_price` — `_brow.get("price")` on the BUY row can itself be
  `NaN` for a malformed/legacy row; same risk.
- `_rt_comp_delta` — `_rt_comp_today = float(_r.get("Score") or 0)`
  (`app.py:27476`) can be `NaN` when the enriched `Score` cell is `NaN`
  rather than absent; `_rt_comp_lag_found` alone only confirms a prior row
  existed, not that the resulting delta is finite.
- **`_rt_rs` (Round 5 fix — do not skip this one).** Round 4 asserted
  `compute_relative_strength()` was "already clean" — that was WRONG.
  `exit_advisor.py:143-145` divides `p.iloc[-1] / p.iloc[-(window+1)]` with
  no zero-denominator guard; a degenerate `0.0` Close bar (the same bad-bar
  family as the NaN-Close case above — `.dropna()` removes `NaN` but not
  `0.0`) makes the division return `inf` (or `inf - inf = nan`) WITHOUT
  raising, so the function's own "0.0 on any error" try/except
  (`exit_advisor.py:146-147`) never fires — it returns the `inf`/`nan`
  as if it were a real, finite relative-strength read. Uncoerced, this
  reaches the Haiku prompt as literal `"+infpp vs SPY"` or `"nanpp"`, and
  — since this is one of the "primitive, individually-real signals" the
  plan otherwise trusts unconditionally — it would have slipped past every
  other guard in this plan undetected.

Coercing all four to `None` via `math.isfinite()` before calling the
builder lets the existing "omit when `None`" convention handle both
"missing" and "unusable, numerically" uniformly — no separate NaN-branch
needed in the builder itself.

**`_rt_comp_lag_found` — exact location pin (Round 4 non-blocking):** this
new boolean must be set `True` only inside the innermost branch of the
existing 5-session lookback loop, right where `composite_today` is
confirmed present —
```python
_rt_comp_lag       = 0.0     # existing (app.py:27535)
_rt_comp_lag_found = False   # NEW — must be initialized here, before the loop
                              # (Round 5 non-blocking fix: the compute loop has
                              # no surrounding try/except, so an implementer
                              # who omits this init hits a NameError the first
                              # time the loop exhausts without a hit — same
                              # crash class as every blocking finding above)
for _rt_lag in range(5, 12):
    _rt_lag_date = str(_today_et() - timedelta(days=_rt_lag))
    _rt_lag_row  = db.load_thesis_erosion_cache(_rt_ticker, _rt_lag_date)
    if _rt_lag_row:
        _rt_snap = _rt_lag_row.get("signals_snapshot") or {}
        if isinstance(_rt_snap, dict) and _rt_snap.get("composite_today") is not None:
            _rt_comp_lag = float(_rt_snap["composite_today"])
            _rt_comp_lag_found = True   # ← HERE, not on the outer `if _rt_lag_row:`
            break
```
Setting it on the outer `if _rt_lag_row:` instead would mark a row "found"
even when that row's own `signals_snapshot` predates this field or is
malformed — reintroducing a bootstrap-artifact leak one level down.

`save_thesis_erosion_cache(..., counter_evidence=_rt_counter_evidence)` — the
parameter already exists in the function signature (`db.py:2455-2477`,
defaults to `None`); persist whatever `_rt_counter_evidence` holds, including
`[]`, so the day-cache actually holds the empty-result case too.

**Cache-miss append fix (Round 1 non-blocking finding):** the in-memory
`_rt_results.append({...})` block (`app.py:27572-27578`) must carry
`"counter_evidence": _rt_counter_evidence` — not a hardcoded `None` — so a
freshly computed bear case renders on the SAME page load that computed it,
not only on the next visit once the cache-hit path picks it up.

**Render (replaces the `app.py:27688-27692` "Phase 1 only" caption) —
uses the SAME `_thesis_by_ticker` lookup so cache-hit rows get accurate
captions too:**
```python
_rce = _rr.get("counter_evidence")   # None | [] | [1-3 validated items]
_r_thesis = _thesis_by_ticker.get(_rr["ticker"])
if _rce is not None and _rce:
    st.markdown("**Bear case** — since you bought, the data now shows:")
    for _ce in _rce:
        _sev_icon = {"high": "🔴", "medium": "🟠", "low": "🟡"}.get(_ce.get("severity"), "🟡")
        st.markdown(f"- {_sev_icon} {_ce['claim']}  \n  *({_ce['signal_basis']})*")
elif _rce is not None:   # [] — call completed, found nothing grounded
    st.caption("No grounded bear case found today — current signals don't support a specific counter-argument.")
elif not _r_thesis:
    st.caption("No thesis on record for this position — add one in AI Insights → Positions to enable the bear case.")
elif _rr.get("erosion_score", 0) >= THESIS_EROSION_HAIKU_MIN:
    st.caption("Bear case not available for today's read.")
else:
    st.caption("No material counter-evidence threshold reached today.")
```

**Copy note (Round 3 non-blocking, softened):** the original draft's wording
here ("Haiku call failed") over-asserts on the one-time transition day Phase
2 first ships — a ticker already scored earlier THAT SAME trading day under
Phase-1-only code has a cached row with `counter_evidence=None` from before
Phase 2 existed, which is not the same as "the call ran and failed today."
The softened, non-diagnostic wording above is accurate in both cases
(never-attempted or genuinely-failed) and self-heals automatically the next
trading day once every row is written by Phase 2-aware code.

**Required first-sentence framing rule (from the original Phase 1 plan,
still binding):** wherever Red Team output appears, lead with "Since you
bought, the data now shows…" phrasing — already present in the render above
("Bear case — since you bought...") — to keep this visibly distinct from the
Pre-Mortem's ex-ante framing.

**New imports required (Round 2 non-blocking note, corrected per Round 3):**
Phase 1 only imports `compute_erosion_score` from `thesis_red_team.py`
(`app.py:155`) — `compute_relative_strength` is imported separately from
`exit_advisor.py`. Phase 2 must add `generate_counter_evidence`,
`build_counter_evidence_inputs` to that import line, plus
`THESIS_EROSION_HAIKU_MIN` from `constants.py` (not currently imported
anywhere in `app.py` since nothing triggered the Haiku path in Phase 1).
**`date` needs no new import** — it's already imported module-level at
`app.py:9` (`from datetime import datetime, date, timedelta, timezone`);
Round 2's original note hedging this was incorrect, corrected here.

**`thesis_red_team.py` itself also needs new imports (Round 6 non-blocking
note):** the module currently has zero imports at HEAD. Adding
`generate_counter_evidence()` requires `import json` (for
`parse_counter_evidence_response()`'s parse) and
`from stock_analyzer.constants import LLM_REQUEST_TIMEOUT_SEC` (used in the
`client.messages.create(..., timeout=LLM_REQUEST_TIMEOUT_SEC)` call) —
mirroring `premortem_advisor.py:29-32` exactly.

---

## Surface 2 — Exit Advisor deterioration cards (`_render_act_card`, `app.py:6611-6809`)

**Pure cache read — no compute triggered from this site** (Round 1 confirmed
this against HEAD: `_render_act_card` only ever calls
`db.load_thesis_erosion_cache`, a pure Supabase read at `db.py:2429-2452`;
the sole compute/Haiku-call site is the Red Team tab loop under
`with _ai_tab_rt:`, which Home's render path never executes — visiting Home
cannot trigger a Haiku call before Red Team has run that day). Matches the
original Phase 1 plan's Surface 2 spec
(`docs/plans/thesis-red-team-agent.md:318-325`). Added immediately after the
existing "⚔️ Challenge This Exit" block, same `kind` gate, and **must sit
inside the existing `if _db_ticker:` block** (opens `app.py:6688`) since it
uses `_db_ticker` — the earlier draft's snippet floated outside that scope
(Round 1 non-blocking finding):

```python
# Already inside the existing `if _db_ticker:` block (app.py:6688) — no need
# to re-test `_db_ticker` here (Round 3 cleanup: earlier draft had a harmless
# but redundant `_db_ticker and` conjunct).
if _db_item.get("kind") in ("deterioration_trim", "deterioration_exit"):
    _rt_card_cached = db.load_thesis_erosion_cache(_db_ticker, str(_today_et()))
    _rt_card_ce = (_rt_card_cached or {}).get("counter_evidence")
    if _rt_card_ce is not None and _rt_card_ce:   # non-None, non-empty only
        with st.expander("⚠️ Red Team — since you bought"):
            for _ce in _rt_card_ce:
                _sev_icon = {"high": "🔴", "medium": "🟠", "low": "🟡"}.get(_ce.get("severity"), "🟡")
                st.markdown(f"- {_sev_icon} {_ce['claim']}  \n  *({_ce['signal_basis']})*")
```

**Does NOT show the erosion score chip** on this card — the original plan
flagged this as circular (the score is partially derived from the same
tier that fired the card in the first place) and that reasoning still holds.
If `_rt_card_cached` is absent or has no `counter_evidence` (Red Team tab not
yet visited today, or score was below the Haiku threshold), the expander is
simply omitted — no placeholder, no "visit Red Team tab" nudge (this is a
secondary surface; the primary is the tab itself).

**Accepted overhead (Round 2 non-blocking note):** this adds one
`db.load_thesis_erosion_cache` Supabase read per deterioration card on every
Home render, versus Phase 1 where this table was only ever read on the
"Challenge This Exit" button click. Cheap (a handful of TRIM/EXIT cards on
any given day, single-row `.limit(1)` read) and a pure read with no write —
accepted as-is, no caching/guard needed beyond what already exists.

---

## What NOT to build in this plan (carried over, still excluded)

- Phase 3 (Daily Brief "Thesis Under Pressure" annotation, cross-day delta
  detection) — separate phase, separate gate, not started here.
- Analyst PT revision wiring (`pt_pts` stays hardcoded `7.0` until
  `analyst_target_snapshots` has enough history — unrelated to this phase).
- Any auto-action, email, or gate change from the counter-evidence output.
- A manual "Refresh" button for the Red Team tab (mentioned in the original
  Phase 1 plan's surfacing spec but not actually shipped in Phase 1 — out of
  scope for Phase 2; flagged here only so it isn't silently assumed to exist).

---

## Cost model

| Item | Per ticker/day (triggered) | Per month (est., ~5 tickers/day crossing threshold) |
|---|---|---|
| Haiku counter-evidence | ~$0.0005 | ~$0.08 |

Bounded identically to the original Phase 1 estimate — only fires on
cache-miss + thesis-present + score ≥ 30.

**Acknowledged pre-existing quirk, Phase 1 scope, not fixed here (Round 5
non-blocking note, citation corrected per Round 6):** the score map built at
`app.py:27476` (`{... : float(_r.get("Score") or 0) ...}`) evaluates
`nan or 0` as `nan` (Python truthiness — `nan` is truthy); `_rt_comp_today`
(`app.py:27532`) reads that map, so a `NaN` enriched `Score` cell reaches
`_rt_comp_today` as `NaN`, not `0`.
Fed into `compute_erosion_score()`, `min(25, nan)` returns `25` (Python's
`min`/`max` with `NaN` is order-dependent and here resolves to the cap), so
a single `NaN` Score can silently inflate `comp_pts` to the full +25 and
push a name over `THESIS_EROSION_HAIKU_MIN=30` that shouldn't have crossed
it — a spurious (paid) Haiku trigger, not a fabrication risk (the bad
`comp_delta` is still excluded from the prompt via the `math.isfinite()`
coercion above, so the narrative itself stays grounded or returns `[]`).
Fixing the erosion SCORE'S own NaN-handling is Phase 1 scope ("strictly
additive... erosion score never modifies" — Phase 2 must not touch
`compute_erosion_score()`), so this is noted here only so a slightly
elevated Haiku-call rate isn't mistaken for a Phase 2 defect later.

---

## Review required before build

**Complete — plan SHIP 2026-07-27 after 6 Opus rounds** (see Review log
above). Ready to hand to a Sonnet implementer.
