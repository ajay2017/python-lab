# Multi-Agent Debate — Phase 3 Design Plan

**Date:** 2026-07-27
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** Plan SHIP 2026-07-27 (2 Opus rounds) — ready for implementation

> **One-line spec:** A new "⚔️ Debate Log" 6th tab on 🧠 AI Insights showing
> every stored entry/exit debate chronologically — the only part of the
> original Multi-Agent Debate plan (`docs/plans/multi-agent-debate.md`) still
> unbuilt. Phase 1 (entry debates) shipped 2026-07-23; Phase 2 (exit debates,
> "Challenge This Exit") shipped 2026-07-24.

---

## Gate status and the data-volume decision (confirmed before drafting this plan)

The original plan's own phased-build table (`docs/plans/multi-agent-debate.md:346-357`)
gates Phase 3 on "Phase 2 stable **and enough debate history to be worth
showing**." Phase 2 has been stable since 2026-07-24 (no fixed day-count, and
today is 3 days later). The data half is genuinely thin: as of a same-day
rec-engine check, `debate_cache` held only **4 rows total — all `entry`-type,
all from one day, zero `exit`-type debates ever generated.**

**User decision (2026-07-27, confirmed via question):** build now, forward-only
— the same precedent as Behavioral Fingerprint (Concept A), which shipped with
low initial sample size and fills in with real usage rather than waiting idle.
This plan is written on that basis: the tab must render an honest, non-alarming
state at 4 rows (or 0) just as well as it will at 400, never a hidden/gated
tab, never an artificial "not enough data yet" wall on the tab itself (that
framing was for the DECISION to build now vs. later — it does not become a
rendering condition once built).

---

## What already exists (verified against HEAD, not assumed)

- **`debate_cache`** (`stock_analyzer/db.py:370-383`, `docs/architecture.md`
  §6.25): one row per `(ticker, debate_type, debate_date)` — PRIMARY KEY on
  that triple. `debate_type` ∈ {`entry`, `exit`}. Every column a log needs is
  already there: `verdict` (`bull_wins`/`bear_wins`/`contested`), `key_dispute`,
  `bull_case_score`/`bear_case_score` (0-100), `grounded` (bool), `transcript`
  (`[{round, agent, text}]`, all 4 rounds), `corpus_snapshot` (audit-only, not
  needed for display), `created_at`. A row is only ever written when
  `transcript` is non-empty (`app.py:6185`, `app.py:6758`) — a failed run is
  never cached, so every stored row is a genuine completed debate.
- **No browsable history exists anywhere today.** A debate result is only
  rendered on the exact card where it was triggered, and only for that same
  ET calendar day (the cache key includes `debate_date`; the next day's
  `load_debate_cache` call misses and the button reappears). The row still
  exists in the table but nothing in the UI shows it again — the ONLY other
  reader is D3 Signal Coherence (under 🧩 Intelligence's `_pi_tab_coherence`
  tab, `app.py:10895-10897`; the `load_debate_verdicts()` call itself is at
  `app.py:11331`), which pulls just the
  `verdict` string via `db.load_debate_verdicts()`, not a real log.
- **Existing render blocks are near-duplicates.** Entry-debate cache-hit
  render (`app.py:6123-6159`) and exit-debate cache-hit render
  (`app.py:6651-6690`) both: pick a verdict icon, show two `st.metric` score
  columns, show `key_dispute`, warn if `grounded is False`, then loop
  `transcript` rendering `Round N — Bull/Bear: text` with bull=green/bear=red
  coloring. They differ only in **label wording** — entry says "🟢 Bull wins" /
  "🔴 Bear wins" / "Bull score" / "Bear score"; exit says "🟢 Hold defensible" /
  "🔴 Exit supported" / "Hold case" / "Exit case" (same underlying
  `bull_case_score`/`bear_case_score` fields, reframed for what "the Bull"
  argues in an exit context — defending the hold, not a new position).
- **🧠 AI Insights** currently has 5 tabs (`app.py:25451-25453`):
  `🩺 Positions`, `📅 Debriefs`, `🏦 Research`, `📊 Scorecard`, `⚠️ Red Team`.
  This plan adds a 6th, positional `st.tabs()` call — no `key` argument on the
  existing call, so a 6th entry is safe (same reasoning used for Red Team's
  and Signal Coherence's own added tabs).

---

## Design principles (carried forward from Phase 1/2 of this same feature)

1. **Strictly additive, diagnostic only.** A read-only history view. Nothing
   here re-runs a debate, changes a verdict, or feeds any gate/score.
2. **Zero new LLM calls, zero new cache table, zero new `constants.py`
   entries.** Purely reads the existing `debate_cache` — the cheapest class of
   addition on this whole roadmap (same class as D3 Signal Coherence / O4
   Watchlist Resurrection, which needed neither).
3. **Reuse, don't reimplement — extract, don't triplicate.** The per-debate
   render block (verdict icon, score metrics, key dispute, grounded warning,
   transcript loop) would otherwise exist a THIRD time if copy-pasted again
   for the log tab. Extracted into two shared module-level functions instead
   (see below) — hoisted `def`s, not closures, for the same reason
   `_fmt_action()`/the holdings-table builder were hoisted for the Summary
   page: Streamlit only executes the matching `if page ==`/`elif page ==`
   branch per rerun, so a function defined inside Grow Today's block would
   not exist when AI Insights' branch runs.
4. **Never fabricates.** Shows exactly the stored fields — no synthesized
   "pattern across debates" narrative and no aggregate chart in this phase
   (the original plan's own "What NOT to build" section explicitly defers a
   "historical debate chart" to "Phase 3+" — even a chart is premature at
   real-today's volume; this phase is the literal "browsable list" spec only).
5. **No artificial data-sufficiency gate on the tab itself.** The "is there
   enough history" question was for the BUILD decision (resolved above, by
   the user); the shipped tab must render a clear, honest state at any volume
   — 0 rows, 4 rows, or eventually hundreds — never conditionally hidden.
6. **Opus review required** before build (this plan) and before ship (code
   review) — same process as every other item on this roadmap.

---

## New `db.py` function: fetch all debates for the log

`load_debate_cache` is single-row (exact ticker/type/date); `load_debate_verdicts`
returns verdict-only, filtered to a specific ticker list (D3's use case). The
log needs every stored debate, full display fields, most recent first.

```python
def load_all_debates(limit: int = 200) -> list[dict]:
    """Return up to `limit` most recent debate_cache rows, most recent first
    (debate_date, then created_at as a tiebreak within the same date -- see
    Round 1 review), for the AI Insights Debate Log tab.

    Excludes corpus_snapshot (large audit-only payload, not needed for
    display -- same exclusion load_debate_cache already makes for its own
    single-row read). Never raises -- degrades to [] on any failure (table
    absent, DB offline), which the tab renders as "no debates yet."
    """
    if not has_db():
        return []
    try:
        rows = (
            _client()
            .table("debate_cache")
            .select("ticker,debate_type,debate_date,verdict,key_dispute,"
                     "bull_case_score,bear_case_score,grounded,transcript")
            .order("debate_date", desc=True)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
        )
        return rows or []
    except Exception:
        return []
```

Mirrors the exact guard/error convention already used by every other reader in
this file (`has_db()`, `.limit().execute().data`, bare `except` → safe empty
default) — not `.maybe_single()` (unavailable on Streamlit Cloud, fails
silently, per house lesson already burned once).

**Resolved (Round 1 review, was an open question pre-review):** `debate_date`
is date-granularity only (written as `str(_today_et())`, `app.py:6189`), so
same-day collisions are not hypothetical — two different tickers, or an entry
+ exit on the same ticker/day, already collide, and PostgREST gives no defined
tie order on a single sort column. Added a secondary
`.order("created_at", desc=True)` — `created_at TIMESTAMPTZ DEFAULT now()` is
a confirmed, server-populated default (`docs/architecture.md:1130`;
`save_debate_cache` never sets it explicitly, so the DB default is
authoritative) and doesn't need to be added to the `select` projection to sort
on it. Zero cost, removes a real non-determinism for a surface whose entire
spec is "chronological."

---

## Extracted shared render function: `_render_debate_result()`

**Revised after Round 1 Opus review (FIX-FIRST, 2 blocking).** The original
sketch had two real defects: (B1) it silently dropped the exit card's
calm-advisor disclaimer ("this debate challenges the exit signal — it does
not change it," `app.py:6666-6670`, exit-only, rendered unconditionally
inside the expander before the failure branch) — refuting the plan's own
"copied verbatim" claim and risking a §2B posture regression if extracted
as originally sketched. (B2) it tried to both render the body AND return the
verdict icon for the caller's expander *title* — but both existing sites need
the icon BEFORE the expander opens (Streamlit requires the title string
up front), so a function that computes the icon only after rendering the body
is unusable at either site. Fixed by splitting icon derivation into its own
tiny pure function, used by every caller for the title, with
`_render_debate_result()` doing body-rendering only (including the
now-restored exit disclaimer).

Two new/changed module-level functions in `app.py` (hoisted, per Design
Principle 3 above), placed near the other hoisted display helpers (e.g.
`_fmt_action()`).

```python
def _debate_verdict_icon(verdict: str | None, debate_type: str) -> str:
    """Verdict icon + label text for a debate's expander title -- must be
    computed BEFORE the expander opens (Streamlit needs the title string
    up front), so this is deliberately separate from body rendering.

    debate_type: "entry" or "exit" -- selects label wording only ("Bull
    wins"/"Bear wins" vs "Hold defensible"/"Exit supported"); same verdict
    values (bull_wins/bear_wins/contested/None) drive both.
    """
    if debate_type == "exit":
        return (
            "🟢 Hold defensible" if verdict == "bull_wins" else
            "🔴 Exit supported"  if verdict == "bear_wins" else
            "⚖️ Contested"        if verdict == "contested" else
            "⚠️ Error"
        )
    return (
        "🟢 Bull wins" if verdict == "bull_wins" else
        "🔴 Bear wins" if verdict == "bear_wins" else
        "⚖️ Contested"  if verdict == "contested" else
        "⚠️ Error"
    )


def _render_debate_result(row: dict, debate_type: str) -> None:
    """Render one stored debate's body: exit-only calm-advisor disclaimer,
    scores, key dispute, grounded warning, and full transcript. Shared by the
    entry-debate cache-hit render, the exit-debate cache-hit render, and the
    AI Insights Debate Log tab (Phase 3) -- extracted here so the same markup
    doesn't exist a third time. Caller is responsible for the expander itself
    and its title (use _debate_verdict_icon() for that, computed first).

    row: a debate_cache row (dict) with verdict/key_dispute/bull_case_score/
         bear_case_score/grounded/transcript keys -- as returned by
         db.load_debate_cache() or db.load_all_debates().
    debate_type: "entry" or "exit" -- selects label wording (score-column
         labels) AND whether the exit-only disclaimer renders.
    """
    _v   = row.get("verdict")
    _kd  = row.get("key_dispute")
    _bsc = row.get("bull_case_score")
    _brc = row.get("bear_case_score")
    _grd = row.get("grounded")
    _trn = row.get("transcript") or []
    _failed = _v is None and not _trn

    if debate_type == "exit":
        _bull_label, _bear_label = "Hold case", "Exit case"
        # Restored per Round 1 review (B1) -- this disclaimer previously only
        # existed at app.py:6666-6670 and would have been silently dropped by
        # the original extraction sketch. Rendered unconditionally, matching
        # the existing site's placement (before the _failed branch).
        st.caption(
            "This debate challenges the exit signal — it does not change "
            "it. The recommendation stands; this is a structured second "
            "opinion before you act."
        )
    else:
        _bull_label, _bear_label = "Bull score", "Bear score"

    if _failed:
        st.caption("Debate could not complete (API unavailable or rate-limited). Try again later.")
    else:
        if _bsc is not None and _brc is not None:
            _c1, _c2 = st.columns(2)
            _c1.metric(_bull_label, f"{_bsc}/100")
            _c2.metric(_bear_label, f"{_brc}/100")
        if _kd:
            st.markdown(f"**Key dispute:** {_kd}")
        if _grd is False:
            st.warning("One or both agents relied on generic arguments — treat this debate with lower confidence.")
    if _trn:
        st.markdown("**Transcript:**")
        for _tr in _trn:
            _ag = (_tr.get("agent") or "").title()
            _ag_color = "#22c55e" if _tr.get("agent") == "bull" else "#ef4444"
            st.markdown(
                f"<span style='color:{_ag_color}'>**Round {_tr.get('round')} — {_ag}:**</span> {_tr.get('text', '')}",
                unsafe_allow_html=True,
            )
```

Existing call sites become:
```python
# entry site (was app.py:6133-6159)
_dv_icon = _debate_verdict_icon(_deb_cached.get("verdict"), "entry")
with st.expander(f"⚔️ Debate — {_dv_icon}", expanded=False):
    _render_debate_result(_deb_cached, "entry")

# exit site (was app.py:6659-6690) -- distinct title PREFIX ("Exit debate",
# not "Debate") must be preserved; _debate_verdict_icon() only returns the
# icon/label, not the prefix, so the caller's f-string still carries it.
_xicon = _debate_verdict_icon(_dbx_cached.get("verdict"), "exit")
with st.expander(f"⚔️ Exit debate — {_xicon}", expanded=False):
    _render_debate_result(_dbx_cached, "exit")
```

**Refactor scope, called out explicitly for the reviewer:** this plan
proposes refactoring the two EXISTING, already-shipped render sites
(`app.py:6123-6159` entry, `app.py:6651-6690` exit) to call these two shared
functions instead of their current inline copies. **Risk:** these are live,
working, already-shipped production render paths on Home's Grow Today and Act
Today cards — a mechanical extraction still touches code with no pytest
coverage (`app.py` is out of pytest's scope per house convention; correctness
here rests on visual before/after parity, not a test run — confirm both sites
render identically to before, including the exit disclaimer, by inspection at
build time). **Alternative considered and rejected:** skip the refactor, write
the Debate Log tab's render loop as an independent third copy. Round 1 review
confirmed extraction is still the lower-risk long-term choice **given it's
done faithfully this time** — a third un-linked copy would be exactly the
"duplicated logic drifts" class of latent risk this house has hit before
(D1's `.iloc[-1]` bug, the verdict-divergence header/card mismatch), with no
test to ever catch the drift.

---

## `app.py` wiring — 🧠 AI Insights, new 6th tab

```python
_ai_tab_pos, _ai_tab_deb, _ai_tab_res, _ai_tab_score, _ai_tab_rt, _ai_tab_dlog = st.tabs(
    ["🩺 Positions", "📅 Debriefs", "🏦 Research", "📊 Scorecard", "⚠️ Red Team", "⚔️ Debate Log"]
)
```

**Inside `with _ai_tab_dlog:`:**

```python
st.caption(
    "Every Bull vs Bear debate you've run, most recent first — both "
    "entry candidates (📈 Grow Today) and exit challenges (🔴 Act Today's "
    "deterioration cards). A second opinion, never a re-scored recommendation."
)
_dlog_rows = db.load_all_debates()

if not _dlog_rows:
    st.info(
        "No debates yet. Run one from a Grow Today candidate (⚔️ Debate) or "
        "a deterioration card (⚔️ Challenge This Exit) to see it here."
    )
else:
    for _dl_row in _dlog_rows:
        _dl_ticker = _dl_row.get("ticker", "—")
        _dl_type   = _dl_row.get("debate_type", "entry")
        _dl_date   = _dl_row.get("debate_date", "—")
        _dl_type_label = "Exit" if _dl_type == "exit" else "Entry"
        with st.expander(f"{_dl_ticker} · {_dl_type_label} · {_dl_date}", expanded=False):
            _render_debate_result(_dl_row, _dl_type)
    if len(_dlog_rows) >= 200:
        st.caption("Showing the 200 most recent debates.")
```

Note the expander title here shows **ticker/type/date** (a log needs to
identify WHICH debate you're looking at across many), whereas the two
existing single-card sites show the **verdict icon** in their expander title
via `_debate_verdict_icon()`, called separately before the expander opens —
since on those cards the ticker and context are already obvious from the
surrounding card. Both are valid title choices for their own context; not a
duplicated-logic concern, a genuine per-site difference in what's already
known to the reader, and made cleanly possible now that icon derivation
(B2 fix) is decoupled from body rendering.

**Exit-disclaimer-in-the-log, decided explicitly (tie-in to B1):** because
`_render_debate_result()` renders the "does not change the signal" caption
whenever `debate_type == "exit"`, every exit-type row in the Log tab will also
show it. This is a deliberate choice, not an accident of the extraction — the
disclaimer is exactly as relevant to someone browsing an old exit debate as to
someone looking at today's card; a browsed exit debate is just as easy to
misread as "the app is telling me to hold" without it.

---

## What NOT to build in this plan

- **A historical debate chart / aggregate stats** (win rate, verdict
  distribution over time). The original plan's own "What NOT to build"
  section defers this to "Phase 3+" — genuinely a Phase 4+ idea once real
  volume exists (4 rows is not a distribution). Revisit only once usage
  materially accumulates.
- **Ticker/type/date filter widgets.** At today's volume (a handful of rows),
  a filter UI would be dead weight with nothing to filter. A flat
  chronological list is the entire spec ("AI Insights tab showing all stored
  debates chronologically" — `docs/plans/multi-agent-debate.md:352`). Natural
  follow-up once the list is long enough that scrolling it is the complaint,
  not built pre-emptively.
- **Pagination UI.** A `.limit(200)` cap with a one-line "showing most recent
  200" caption is enough at any volume this app will plausibly reach for a
  single user; a "load more" control is unnecessary complexity for now.
- **Any change to `debate_cache`'s schema, `run_debate()`, `build_entry_corpus()`,
  or `build_exit_corpus()`.** Phase 3 is a pure read/render addition.

---

## Cost model

| Item | Per view |
|---|---|
| `db.load_all_debates()` | $0 (one Supabase read, no LLM) |
| `_render_debate_result()` per row | $0 (pure Streamlit rendering) |

Zero marginal cost — same class as D3/O4, the cheapest tier of this roadmap.

---

## Definition of Done checklist for this feature (per CLAUDE.md)

1. No new/changed `constants.py` entries.
2. New user-facing surface (the 6th AI Insights tab) → new F-ID in
   `docs/requirements.md` at ship time.
3. Closes the last open item of the ENTIRE Multi-Agent Debate feature (F-197)
   — remove its line from CLAUDE.md's "What's queued" and add a
   `docs/shipped-log.md` entry once shipped. This also closes the last of the
   THREE originally-tracked Agentic Intelligence Roadmap gated phases (Thesis
   Red Team Phase 2 will be the only one left after this ships).
4. User-visible behavior changed → add a short mention to the in-app User
   Guide's 🧠 AI Insights bullet.
5. Non-obvious rationale (the forward-only build-now decision at 4 rows; the
   extraction-vs-triplication call on `_render_debate_result`) → capture in
   memory `project_agentic_intelligence_roadmap` once shipped.
6. New `db.py` reader function should also get a one-line mention in
   `docs/architecture.md`'s `debate_cache` table description (§6.25) —
   noting it alongside the existing `load_debate_cache`/`save_debate_cache`/
   `load_debate_verdicts` mentions there, not a schema change so no DDL edit.

---

**Hoist-order reminder (non-blocking, Round 1 review):** `_debate_verdict_icon()`
and `_render_debate_result()` must be defined above their earliest caller — the
Grow Today branch near `app.py:6116` — well before 🧠 AI Insights at
`app.py:25451`. `py_compile` will not catch a def-after-use here (house lesson,
memory `feedback_module_def_order`); confirm placement visually at build time.

---

## Review log

| Round | Model | Verdict | Blocking findings |
|---|---|---|---|
| Round 1 | Claude Opus 4.8 | FIX-FIRST | 2 blocking: (B1) the extraction sketch dropped the exit card's calm-advisor disclaimer (`app.py:6666-6670`, exit-only, unconditional) — refuted the plan's "copied verbatim" claim and risked a §2B posture regression; fixed by restoring it inside `_render_debate_result()` gated on `debate_type == "exit"`, and made an explicit decision that it also appears in the Log tab (deliberate, not accidental). (B2) `return _icon` for the expander title was circular — both existing sites need the icon BEFORE the expander opens, so a function returning it only after rendering the body was unusable at either site; fixed by splitting into a separate pure `_debate_verdict_icon()` helper, with `_render_debate_result()` doing body-only rendering. + resolved both open questions: added a secondary `.order("created_at", desc=True)` to `load_all_debates()` (real non-determinism on same-day collisions, zero cost to fix); confirmed extraction is the correct call over triplication, given app.py's zero-pytest-coverage design makes a third un-linked copy pure latent drift risk. + non-blocking: a stale line-citation for the D3 reader (fixed) and a hoist-order reminder (carried forward, nothing new). |
| Round 2 | Claude Opus 4.8 | SHIP | 0 blocking. Re-verified against HEAD: B1 exit disclaimer restored with correct unconditional placement before the `_failed` branch (matches `app.py:6666-6670`); B2 icon/body split removes the circularity; Q4 secondary `.order("created_at", desc=True)` applied (`created_at` default confirmed at `db.py:381`). 3 non-blocking, all fixed in this revision: D3 `load_debate_verdicts()` call is at `app.py:11331` (was mis-cited ~11406); the exit site's distinct `"⚔️ Exit debate — "` title prefix must survive the extraction (`_debate_verdict_icon()` only returns the icon, not the prefix) — clarified in the illustrative snippet; Design Principle 3 updated from "one shared function" to "two." **Ready for implementation** — verify both existing render sites by visual before/after parity at build time; resulting code still requires the standard pre-ship Opus review per CLAUDE.md Hard Rule #4 (this touches app.py UI only, no gate/scoring/constants — so per docs/testing-strategy.md §5 the required step is §4.1/§4.2 manual click-through, not a mandatory Opus code review, but the extraction touching two live production sites makes a review worthwhile regardless). |
