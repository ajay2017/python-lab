"""
Portfolio Intelligence Report — F-4 AI Intelligence Layer (monthly retrospective).

Where the Weekly Debrief (F-3) looks back one week at *what happened*, this
monthly report looks back over a calendar month at *how decisions were made* — the slower
questions that only surface across weeks:

  Q0  Entry quality      — did the ENGINE pick well? (composite band → alpha vs SPY)
  Q1  Signal discipline  — did the USER act on what surfaced, and did acting help?
  Q3  Pattern & focus     — one systematic behavioural pattern + one focus for next month

Question 0 leads because every position originates from a recommendation that
cleared the gates and scored above the entry threshold (composite ≥ COMPOSITE_BUY);
everything downstream is conditioned on that pick. v1 ships Q0 + Q1 + a pattern;
thesis discipline (Q2) folds in once enough matured thesis reviews exist.

Design (same contract as the rest of the AI layer):
- The Python builder assembles every NUMBER from the rule-based scorecard in
  `recommendations_history.py` (compute_outcomes → rollups), with WHICH calls are
  judged and whether each was acted on decided by `monthly_entry_calls` on the
  shared entry-call rule (`entry_call_action`: a BUY on the call day or within
  `ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS` sessions). The LLM NARRATES that
  package — it cannot invent recommendations, trades, or outcomes.
- HARD BOUNDARY: the report may *surface* that a composite band underperforms, but
  it never states or changes a threshold/gate value — that stays an investment-policy
  decision with the user (CLAUDE.md hard rule #1). The system prompt enforces this.
- Returns None on any failure so callers surface an explicit offline state.

Entry points:
  build_report_package()  — assemble the structured scorecard package.
  generate_report()       — call the LLM, return a save-ready dict for db.save_monthly_report().
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from stock_analyzer import llm
from stock_analyzer.constants import LLM_REQUEST_TIMEOUT_SEC


# ── Prompt ──────────────────────────────────────────────────────────────────

def _system_prompt() -> str:
    """Build the system prompt. The entry-threshold value is interpolated from
    constants.COMPOSITE_BUY at call time (never a hand-transcribed literal) so the
    prompt can never narrate a stale threshold if that policy value changes. The
    acted-on window N (`ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS`) is read the same way."""
    from stock_analyzer.constants import COMPOSITE_BUY
    from stock_analyzer import entry_call_action as _eca
    from stock_analyzer import debrief_entry_calls as _dec
    _n_days = _dec._days_phrase(_eca._window(None))   # N, read from the constant now
    return f"""You are a disciplined portfolio analyst writing a MONTHLY intelligence report for an individual investor. You judge two things and surface one pattern — factually, in second person ("Your..."), neutral tone (observation, not praise or blame). Plain language, no jargon. Target 500–700 words.

This report judges BEHAVIOUR and ENGINE QUALITY over roughly the past month. It does NOT recommend trades and does NOT set, suggest, or change any numeric threshold or gate.

Background you must use correctly:
- A "New Position to Initiate" call is a recommendation that cleared the app's gates and scored above the entry threshold (composite ≥ {COMPOSITE_BUY}), surfacing as a high-conviction pick; the positions the investor holds began that way. The entry calls this report judges are the app's New Position, Add to Winner and Enter Now calls. So the engine's pick quality is the foundation everything else sits on.
- A recommendation's quality is judged on ALPHA — its return minus SPY over the same window. Beating the market in a down month is a win; trailing it in an up month is not. Lead with alpha, not raw return.
- "Acted on" means the investor BOUGHT the name on the call day or within {_n_days} after it (a sell never counts); "not acted on" (skipped) means no buy in that window. A call whose window runs past the period end is "still open"; a call the app couldn't judge is "not judged". Neither is ever counted as acted on or skipped, and you must never call them either. The "skipped-minus-acted average return gap" is the RAW-return gap (average missed return minus average acted return), not an alpha figure; positive means ignored names returned more than the ones acted on (money left on the table). Quote it as a raw-return gap, never as alpha.

Write EXACTLY three sections with these bold headers and nothing before the first header:

**Entry quality**
How well did the ENGINE pick this period? Use the composite-band and verdict breakdowns: did higher-composite / Confirmed recommendations actually convert and beat SPY? The per-band / per-verdict "engine alpha" figures given to you are across ALL graded names in that band (acted or not) — the exact numbers shown in the band chart the reader sees; quote those, never an acted-only subset, so your text matches the chart. Where did quality cluster or weaken (e.g. a band or sector)? If the engine's high-conviction band underperformed a lower band, you MAY flag that the entry threshold is worth the investor's own review — but NEVER state a new threshold number, NEVER recommend a specific change, and NEVER tell the investor to change a gate; that is their policy decision alone. If there are too few matured recommendations to grade, say exactly that in one sentence and move on.

**Signal discipline**
Did the investor ACT on what the engine surfaced, and did acting help? Use the action rate, acted-vs-missed average outcome and alpha, and the best/worst named outcomes. Was money left on the table by ignoring signals, or did acting add value? Be specific and factual; name tickers from the data.

**Pattern & focus**
One systematic behavioural pattern grounded only in this data (examples: "you acted on the highest-composite picks but skipped the rest," "your missed names outperformed the ones you acted on this month," "you acted on buys but not trims"), then ONE concrete thing to focus on next month.

Rules:
- Only use facts from the data package. Never invent recommendations, trades, prices, tickers, or events.
- Counts of names, recommendations, or outcomes are DISTINCT tickers and must match the figures in the data package exactly. NEVER state a count larger than the total names surfaced, and NEVER invent a separate "graded outcomes" tally — the reader sees the exact counts in a header and chart above your text, and your words must agree with them.
- A name listed in the data as bought outside the decision window was held by the investor: never say skipping it avoided a loss or missed a gain, and never call it ignored.
- Never recommend buying or selling a specific name. Never state, suggest, or change a threshold/gate value.
- No disclaimers, no preamble. End after the third section."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pct(v) -> str:
    # "—" not "N/A" — matches _band_line's own missing-value placeholder so
    # one report never mixes both tokens (2026-08-04 UX audit CA13).
    if v is None or (isinstance(v, float) and v != v):   # NaN != NaN
        return "—"
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return "—"


def classify_recommendations_read(recs_or_none, period_start=None, period_end=None) -> str:
    """Classify a recommendations read for the monthly-report generation gate.

    Returns one of:
      "outage" — the read itself failed (recs_or_none is None, e.g.
                 db.load_recommendations_or_none() saw a DB outage or
                 missing credentials). Must render distinctly from "empty" —
                 collapsing the two tells the owner to wait for the Daily
                 Brief to surface picks when the real problem is a live DB
                 outage worth investigating now.
      "empty"  — the read succeeded but no recommendations exist in the
                 requested window (a genuine quiet period, not a failure).
      "ready"  — recommendations exist; safe to build the report package.

    Pure function — extracted so the interactive "Generate Monthly Report"
    button in app.py (no test coverage of its own) is render-only wiring
    around a decision that IS tested."""
    if recs_or_none is None:
        return "outage"
    if recs_or_none.empty:
        return "empty"
    if (period_start is not None and period_end is not None
            and "rec_date" in recs_or_none.columns):
        # The load reaches back before `period_start` (an earlier call's window can
        # cover a buy in the period), so "empty" means no call DATED in the period.
        from stock_analyzer import entry_call_action as _eca
        ps, pe = _eca._call_date(period_start), _eca._call_date(period_end)
        if ps is not None and pe is not None:
            dated = (_eca._call_date(v) for v in recs_or_none["rec_date"])
            if not any(d is not None and ps <= d <= pe for d in dated):
                return "empty"
    return "ready"


def _band_line(row: dict) -> str:
    """One scorecard row → a compact prompt line. The band/verdict ALPHA reported here is
    `avg_alpha` — the engine's alpha across ALL graded names in the band (acted or not),
    the SAME figure the entry-quality band chart plots — so the narrative's per-band
    numbers match the chart. (Acted-vs-missed is an overall, not per-band, figure and
    lives in the signal-discipline inputs; quoting a per-band acted-only alpha here is
    what made the prose say a band was -3.0% while the chart showed it +0.8%.)"""
    name = row.get("band") or row.get("verdict") or "—"
    n    = row.get("n_total", 0)
    ar   = row.get("action_rate")
    npr  = row.get("n_priced", 0)
    al   = row.get("avg_alpha")   # ALL-graded band alpha — matches the band chart
    bits = [f"{name}: {n} rec(s)"]
    if ar is not None:
        bits.append(f"acted {ar:.0f}%")
    if npr:
        bits.append(f"{npr} matured")
        if al is not None:
            bits.append(f"engine alpha {_pct(al)}")
    return "  " + " · ".join(bits)


# ── Data package builder ──────────────────────────────────────────────────────

def build_report_package(
    period_start: date,
    period_end: date,
    recs_df,
    trades_df,
    current_prices: dict | None = None,
    spy_close_by_date: dict | None = None,
    weekly_rows: list[dict] | None = None,
    min_graded: int = 5,
    as_of: date | None = None,
) -> dict:
    """
    Assemble the structured scorecard package for the monthly report.

    Which calls are judged, and whether each was acted on, is decided by
    `monthly_entry_calls.build_month` on the shared entry-call rule
    (`entry_call_action`): an ENTRY CALL (New Position, Add to Winner, Enter
    Now; an already-held Enter Now is left out) is acted on when the investor
    BOUGHT the name on the call day or within `ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS`
    sessions after it. The awareness-only "More Buy Candidates" / overflow feed is
    excluded: the report judges the engine's actual entry calls and whether the
    investor acted on them. A call whose window runs past `period_end` is "still
    open" and a call that can't be judged is "not judged"; neither is ever counted
    as acted on or skipped, and both are listed by name (package `open_names` /
    `unknown_names`).

    The pipeline below `build_month` is unchanged: compute_outcomes(min_days=
    REC_SCORE_MIN_DAYS) -> summary_stats / by_composite_band / by_verdict. TWO views
    come back (UD-1 B): "judged" (the investor's own: bought calls priced from the
    FILL; acted vs skipped, best / worst, the Bought average) and "engine" (every
    call from its call-day price whether or not it was bought; the band / verdict
    alpha, `n_graded`, `q0_ready`). `n_graded` / `q0_ready` therefore count
    SURFACINGS (a name surfaced on several days counts once per day).

    The judging cut-off is `period_end`, never `as_of`, so a regenerated report
    reads the same; `as_of` only dates the outcome prices.

    recs_df:            recommendations surfaced in [period_start, period_end].
    trades_df:          all trades (to find the BUYs). None = unreadable journal.
    current_prices:     {ticker: price} for marking open positions (caller fetches).
    spy_close_by_date:  {date: close} SPY series for the regime/alpha benchmark.
    weekly_rows:        recent weekly_debriefs dicts (perf/spy/alpha) for trajectory.
    min_graded:         matured graded surfacings required before Q0 is narrated.
    as_of:              the date outcomes are measured TO (None = period_end). Stock
                        outcomes use live prices from the run date, so SPY, `days_since`
                        and `outcome_maturing` must be measured to that same date, not
                        to period_end (a calendar-month report is built after its end).
                        The caller's SPY series must reach it. Must not be before
                        `period_end` (`build_month` raises ValueError).

    Returns a dict consumed by generate_report(). `judge_state` is "ok" or the
    refusal state of `build_month` (recs / trades unavailable or unreadable): a
    non-ok state has_data=False and nothing else filled in, so the caller must read
    `judge_state` before treating has_data=False as "nothing to report". With "ok",
    has_data is True when at least one entry-call NAME surfaced (`n_surfaced > 0`),
    even if none is judged yet (all still open).
    """
    from stock_analyzer import entry_call_action as eca
    from stock_analyzer import monthly_entry_calls as mec
    from stock_analyzer.recommendations_history import summary_stats
    from stock_analyzer.constants import REC_SCORE_MIN_DAYS

    _as_of = as_of if as_of is not None else period_end

    package: dict = {
        "period_start":   str(period_start),
        "period_end":     str(period_end),
        "as_of":          str(_as_of),
        "rec_scope":      ", ".join(eca.ENTRY_CALL_REC_TYPES),
        "judge_state":    None,
        "has_data":       False,
        "q0_ready":       False,
        "min_graded":     min_graded,
        "viz":            None,
        "n_surfaced":     0,
        "n_total":        0,
        "n_acted":        0,
        "n_missed":       0,
        "n_open":         0,
        "n_unknown":      0,
        "open_names":     [],
        "unknown_names":  [],
        "bought_outside_names": [],
        "window_trading_days": eca._window(None),
        "n_graded":       0,
        "action_rate":    None,
        "engine_alpha_pct": None,
        "avg_acted_pct":  None,
        "avg_missed_pct": None,
        "missed_alpha":   None,
        "avg_acted_alpha":  None,
        "avg_missed_alpha": None,
        "best":  None,
        "worst": None,
        "band_rows":    [],
        "verdict_rows": [],
        "weekly_trajectory": [],
        "weekly_excluded_old_method": 0,
    }

    month = mec.build_month(
        recs_df, trades_df,
        period_start=period_start, period_end=period_end, as_of=_as_of,
        current_prices={} if current_prices is None else current_prices,
        spy_close_by_date={} if spy_close_by_date is None else spy_close_by_date,
        min_days=REC_SCORE_MIN_DAYS,
    )
    package["judge_state"] = month["judge_state"]
    package["window_trading_days"] = month["window_trading_days"]
    if month["judge_state"] != mec.JUDGE_OK:
        return package   # an unreadable input: never "nothing to report", never "all skipped"

    counts, viz = month["counts"], month["viz"]
    flow = viz["flow"]
    package["viz"]         = viz
    package["n_surfaced"]  = counts["n_surfaced"]
    package["n_open"]      = counts["n_open"]
    package["n_unknown"]   = counts["n_unknown"]
    package["open_names"]    = viz["entry"]["open_names"]
    package["unknown_names"] = viz["entry"]["unknown_names"]
    package["bought_outside_names"] = viz["entry"].get("bought_outside_names", [])
    # n_total = the JUDGED names (acted + skipped), the basis the flow chart uses.
    package["n_total"]     = flow["n_total"]
    package["n_acted"]     = flow["n_acted"]
    package["n_missed"]    = flow["n_missed"]
    _decided = flow["n_acted"] + flow["n_missed"]
    package["action_rate"] = (
        round(flow["n_acted"] / _decided * 100.0, 1) if _decided else None
    )
    package["has_data"]    = counts["n_surfaced"] > 0
    if not package["has_data"]:
        return package   # no entry-call names in the window

    # Aggregate means (the investor's view: acted from the fill) and the graded
    # count (the engine's view: every call from its call-day price). summary_stats
    # counts surfacings, used only for the graded-outcome aggregates (alpha, best /
    # worst), never for the headline name counts above.
    stats        = summary_stats(month["judged"])
    engine_stats = summary_stats(month["engine"])
    package["n_graded"]  = engine_stats["n_priced"]   # surfacings matured enough to grade (internal)
    package["engine_alpha_pct"] = stats["avg_acted_alpha"]   # the Bought tile (mean over acted calls, your fill)
    package["avg_acted_pct"]    = stats["avg_acted_pct"]
    package["avg_missed_pct"]   = stats["avg_missed_pct"]
    package["missed_alpha"]     = stats["missed_alpha"]
    package["avg_acted_alpha"]  = stats["avg_acted_alpha"]
    package["avg_missed_alpha"] = stats["avg_missed_alpha"]
    package["best"]   = stats["best"]
    package["worst"]  = stats["worst"]
    package["q0_ready"] = engine_stats["n_priced"] >= min_graded

    package["band_rows"]    = mec.band_rows(month["engine"], month["judged"])
    package["verdict_rows"] = mec.verdict_rows(month["engine"], month["judged"])

    if weekly_rows:
        # Only weeks measured the way the Weekly Debrief measures them today are
        # comparable; an older-method row (metrics_version missing/NULL/1) would
        # mix two different quantities in one trajectory.
        from stock_analyzer.debrief_advisor import METRICS_VERSION as _WD_VERSION
        for w in weekly_rows:
            if w.get("metrics_version") != _WD_VERSION:
                package["weekly_excluded_old_method"] += 1
                continue
            package["weekly_trajectory"].append({
                "week_ending":     str(w.get("week_ending", "")),
                "performance_pct": w.get("performance_pct"),
                "spy_pct":         w.get("spy_pct"),
                "alpha_pct":       w.get("alpha_pct"),
            })

    return package


def judge_state_error(package: dict, *, rebuilding: bool = False) -> "str | None":
    """The visible error for a package whose entry calls couldn't be judged, or
    None when `judge_state` is "ok". A missing key reads as not-ok (fail closed).
    The interactive Generate button renders it and saves nothing; the cron lane
    fails on the same condition. `rebuilding=True` words it for the live
    fallback that re-draws a saved report's visuals (nothing is being generated)."""
    state = package.get("judge_state")
    if state == "ok":
        return None
    if state in ("trades_unavailable", "trades_unreadable"):
        if rebuilding:
            return ("Could not read your trade journal, so this report's signal-flow "
                    "visuals can't be rebuilt right now. Try again in a moment.")
        return ("Could not read your trade journal — the database may be "
                "unreachable, so no report was generated. Try again in a moment.")
    if state in ("recs_unavailable", "recs_unreadable"):
        if rebuilding:
            return ("Could not read recommendation history, so this report's visuals "
                    "can't be rebuilt right now.")
        return ("Could not read recommendation history, so no report was "
                "generated. Try again in a moment.")
    if rebuilding:
        return ("This report's entry calls couldn't be judged, so its visuals can't "
                "be rebuilt right now.")
    return ("This period's entry calls couldn't be judged, so no report was "
            "generated. Try again in a moment.")


def _names_line(items, with_reason: bool) -> str:
    """"NVDA (window ends 2026-12-01), AMD (window ends ...)" for the prompt."""
    from stock_analyzer import debrief_entry_calls as dec
    bits = []
    for it in dec._rows_of(items):
        if with_reason:
            bits.append(f"{it.get('ticker')} ({dec.unknown_reason_text(it.get('reason'))})")
        else:
            wend = it.get("window_end")
            bits.append(f"{it.get('ticker')} (window ends {wend})" if wend
                        else f"{it.get('ticker')}")
    return ", ".join(bits)


def _bought_outside_line(items) -> str:
    """"MRVL (bought 2026-10-13, 2026-10-14; last call 2026-10-07), ..." for the prompt."""
    from stock_analyzer import debrief_entry_calls as dec
    bits = []
    for it in dec._rows_of(items):
        dates = ", ".join(str(d) for d in dec._rows_of(it.get("buy_dates")))
        last = it.get("last_call_date")
        bits.append(f"{it.get('ticker')} (bought {dates}"
                    + (f"; last call {last})" if last else ")"))
    return ", ".join(bits)


def _format_prompt(package: dict) -> str:
    from stock_analyzer import entry_call_action as eca
    from stock_analyzer import debrief_entry_calls as dec
    n_days = dec._days_phrase(eca._window(package.get("window_trading_days")))
    # A package without the new keys reads as "every surfaced name was judged".
    n_total = package["n_total"]
    n_surf  = package.get("n_surfaced", n_total)
    n_open  = package.get("n_open", 0)
    n_unk   = package.get("n_unknown", 0)
    lines = [
        f"Period: {package['period_start']} to {package['period_end']}",
    ]
    if package.get("as_of") and package["as_of"] != package["period_end"]:
        lines.append(f"Outcomes are measured to {package['as_of']}, not to the period end.")
    lines += [
        "",
        "SCOPE: these are the App's ACTIONABLE entry calls only — names surfaced as "
        "\"New Positions to Initiate\" (they cleared all gates, composite ≥ the entry "
        "threshold), Add to Winner calls and Enter Now calls (an Enter Now on a name "
        "the investor already held is left out). The awareness-only \"More Buy "
        "Candidates\" feed (names the App surfaced but flagged to skip) is EXCLUDED. So a "
        "high skip count is not awareness noise — every one is a name the App told you to "
        "act on. Do not reference the awareness feed.",
        "",
        f"Distinct entry-call names surfaced this period: {n_surf}. "
        f"You acted on {package['n_acted']} and did not act on {package['n_missed']}; "
        f"{n_open} still open at the period end (decision window runs past "
        f"{package['period_end']}); {n_unk} not judged. "
        "Every count here is a DISTINCT ticker — a name that surfaced on many days counts "
        "once. These are the only name counts that exist for this report.",
        "",
        "COUNT DISCIPLINE — the reader sees these exact distinct counts in a header and a "
        "flow chart directly above your text, so your words must agree with them:",
        f"  - surfaced = {n_surf}, acted on = {package['n_acted']}, "
        f"not acted on = {package['n_missed']}, still open = {n_open}, not judged = {n_unk}.",
        f"  - NEVER state a count of names, recommendations, or outcomes greater than "
        f"{n_surf}. There is NO separate, larger 'graded outcomes' tally — do "
        "not invent one. Refer to how many have matured in WORDS (e.g. 'most have matured', "
        "'only a few have matured'), never as a new number.",
        "  - Action rate = acted ÷ (acted + not acted); still-open and not-judged names "
        "are not in it.",
        "  - Percentages (alpha, outcome %, action rate) should be quoted as given; only "
        "raw NAME counts are constrained to the distinct figures above.",
    ]
    if package.get("open_names"):
        lines.append(
            "Still open at the period end (already listed by the app under Signal "
            "discipline; never call them skipped or acted on): "
            + _names_line(package["open_names"], False))
    if package.get("unknown_names"):
        lines.append(
            "Not judged (already listed by the app under Signal discipline; never "
            "call them skipped or acted on): "
            + _names_line(package["unknown_names"], True))
    if package.get("bought_outside_names"):
        lines.append(
            "Bought this month but not within a call's decision window (counted as "
            "skipped; the investor HELD these names, so never say skipping them avoided "
            "a loss or missed a gain, and never call them ignored; already listed by the "
            "app under Signal discipline): "
            + _bought_outside_line(package["bought_outside_names"]))
    if not package.get("q0_ready"):
        lines.append(
            f"NOTE: too few of these surfacings have matured enough to grade entry quality "
            f"fairly (the engine needs at least {package['min_graded']} matured surfacings "
            "before its pick quality can be judged). Keep the Entry quality section to one "
            "honest sentence and do not over-read the band table."
        )

    lines += [
        "",
        f"Band and verdict engine alpha price every call from its call-day price, "
        f"whether or not you bought; the acted averages use your fill price, up to "
        f"{n_days} after the call.",
        "Average outcomes across matured positions (alpha = return minus SPY, same window):",
        f"  Action rate: {package['action_rate']:.0f}%" if package.get("action_rate") is not None else "  Action rate: N/A",
        f"  Avg outcome — acted: {_pct(package.get('avg_acted_pct'))} · missed: {_pct(package.get('avg_missed_pct'))}",
        f"  Avg ALPHA vs SPY — acted: {_pct(package.get('avg_acted_alpha'))} · missed: {_pct(package.get('avg_missed_alpha'))}",
        f"  Skipped-minus-acted average return gap (raw return, NOT alpha; skipped minus acted; positive = the skipped names did better): {_pct(package.get('missed_alpha'))}",
    ]
    # A skipped name the investor bought outside the decision window was HELD:
    # "missed" there is the cue that made a report say skipping it avoided a loss.
    held_outside = {str(it.get("ticker")) for it in
                    dec._rows_of(package.get("bought_outside_names"))}

    def _tag(row) -> str:
        if row["acted_on"]:
            return "acted"
        if str(row.get("ticker")) in held_outside:
            return "skipped — bought outside the window, held"
        return "missed"

    if package.get("best"):
        b = package["best"]
        lines.append(f"  Best outcome: {b['ticker']} {_pct(b['outcome_pct'])} (alpha {_pct(b.get('alpha_pct'))}, {_tag(b)})")
    if package.get("worst"):
        w = package["worst"]
        lines.append(f"  Worst outcome: {w['ticker']} {_pct(w['outcome_pct'])} (alpha {_pct(w.get('alpha_pct'))}, {_tag(w)})")

    if package.get("band_rows"):
        lines.append("")
        lines.append(
            "By composite band — 'engine alpha' is across ALL graded names in the band "
            "(acted or not), the engine-quality view the band chart plots; quote these "
            "for entry quality (higher-conviction tiers should sit higher):"
        )
        for row in package["band_rows"]:
            lines.append(_band_line(row))

    if package.get("verdict_rows"):
        lines.append("")
        lines.append("By cross-check verdict (Engine-Confirmed = the App's actual recommendations; others = awareness it steered you away from):")
        for row in package["verdict_rows"]:
            lines.append(_band_line(row))

    if package.get("weekly_trajectory"):
        lines.append("")
        _n_old = package.get("weekly_excluded_old_method") or 0
        lines.append("Weekly performance trajectory (most recent first):"
                     + (f" ({_n_old} earlier week{'' if _n_old == 1 else 's'} used an older method — not shown)"
                        if _n_old > 0 else ""))
        for w in package["weekly_trajectory"]:
            lines.append(
                f"  week ending {w['week_ending']}: portfolio {_pct(w.get('performance_pct'))}, "
                f"SPY {_pct(w.get('spy_pct'))}, alpha {_pct(w.get('alpha_pct'))}"
            )

    return "\n".join(lines)


def _parse_response(text: str) -> dict:
    sections = {
        "section_entry_quality":     "",
        "section_signal_discipline": "",
        "section_patterns":          "",
    }
    headers = {
        "entry quality":     "section_entry_quality",
        "signal discipline": "section_signal_discipline",
        "pattern & focus":   "section_patterns",
        "pattern and focus": "section_patterns",
        "pattern":           "section_patterns",
    }
    current_key: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        stripped = line.strip().lower().lstrip("*# ")
        matched  = next((k for h, k in headers.items() if stripped.startswith(h)), None)
        if matched:
            if current_key and current_lines:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key   = matched
            current_lines = []
        elif current_key is not None:
            current_lines.append(line)

    if current_key and current_lines:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


# ── Public API ────────────────────────────────────────────────────────────────

def generate_report(
    package: dict,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 1000,
) -> dict | None:
    """
    Call the LLM to generate the monthly intelligence report.

    Returns a save-ready dict (for db.save_monthly_report) or None on any failure
    (no API key, no recs in the window, or the LLM call raised).
    """
    if not api_key or not package.get("has_data"):
        return None
    try:
        prompt   = _format_prompt(package)
        r = llm.complete(
            llm.ROLE_DEEP,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            system=_system_prompt(),
            messages=[{"role": "user", "content": prompt}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        if not r.ok:
            return None
        text     = r.text
        sections = _parse_response(text)
        # UD-3: the app-written still-open / not-judged bullets go at the END of
        # Signal discipline, after the model's text (the model never writes them).
        # Frozen with the row, so the app, the preview and the email all show them.
        from stock_analyzer import monthly_entry_calls as _mec
        _lines = _mec.signal_lines(package.get("viz"))
        if _lines:
            _ai = sections["section_signal_discipline"]
            sections["section_signal_discipline"] = (
                (_ai + "\n\n" if _ai else "") + "\n".join(_lines))
        return {
            "period_start":  package["period_start"],
            "period_end":    package["period_end"],
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "engine_alpha_pct": package.get("engine_alpha_pct"),
            "acted_count":   package.get("n_acted"),
            "missed_count":  package.get("n_missed"),
            # Freeze the visual snapshot so the report is an immutable dated artifact —
            # the chart re-renders verbatim later instead of drifting on a live recompute.
            "viz_json":      package.get("viz"),
            **sections,
            "section_thesis": None,   # Q2 deferred until enough matured thesis reviews
            "email_sent":    False,
        }
    except Exception:
        return None
