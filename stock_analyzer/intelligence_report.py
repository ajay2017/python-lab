"""
Portfolio Intelligence Report — F-4 AI Intelligence Layer (monthly retrospective).

Where the Weekly Debrief (F-3) looks back one week at *what happened*, this
monthly report looks back ~4 weeks at *how decisions were made* — the slower
questions that only surface across weeks:

  Q0  Entry quality      — did the ENGINE pick well? (composite band → alpha vs SPY)
  Q1  Signal discipline  — did the USER act on what surfaced, and did acting help?
  Q3  Pattern & focus     — one systematic behavioural pattern + one focus for next month

Question 0 leads because every position originates from a recommendation that
cleared the gates and scored above the entry threshold (composite ≥ COMPOSITE_BUY);
everything downstream is conditioned on that pick. v1 ships Q0 + Q1 + a pattern;
thesis discipline (Q2) folds in once enough matured thesis reviews exist.

Design (same contract as the rest of the AI layer):
- The Python builder assembles every NUMBER from the existing rule-based scorecard
  in `recommendations_history.py` (match → compute_outcomes → rollups). The LLM
  NARRATES that package — it cannot invent recommendations, trades, or outcomes.
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

from stock_analyzer.constants import LLM_REQUEST_TIMEOUT_SEC


# ── Prompt ──────────────────────────────────────────────────────────────────

def _system_prompt() -> str:
    """Build the system prompt. The entry-threshold value is interpolated from
    constants.COMPOSITE_BUY at call time (never a hand-transcribed literal) so the
    prompt can never narrate a stale threshold if that policy value changes."""
    from stock_analyzer.constants import COMPOSITE_BUY
    return f"""You are a disciplined portfolio analyst writing a MONTHLY intelligence report for an individual investor. You judge two things and surface one pattern — factually, in second person ("Your..."), neutral tone (observation, not praise or blame). Plain language, no jargon. Target 500–700 words.

This report judges BEHAVIOUR and ENGINE QUALITY over roughly the past month. It does NOT recommend trades and does NOT set, suggest, or change any numeric threshold or gate.

Background you must use correctly:
- Every position the investor holds began as a recommendation that cleared the app's gates and scored above the entry threshold (composite ≥ {COMPOSITE_BUY}), surfacing as a high-conviction "New Position to Initiate." So the engine's pick quality is the foundation everything else sits on.
- A recommendation's quality is judged on ALPHA — its return minus SPY over the same window. Beating the market in a down month is a win; trailing it in an up month is not. Lead with alpha, not raw return.
- "Acted on" means the investor actually traded the name; "missed" means it surfaced but they did not. Positive "missed alpha" means ignored names beat the ones acted on (money left on the table).

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
- Never recommend buying or selling a specific name. Never state, suggest, or change a threshold/gate value.
- No disclaimers, no preamble. End after the third section."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pct(v) -> str:
    if v is None or (isinstance(v, float) and v != v):   # NaN != NaN
        return "N/A"
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return "N/A"


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
    rec_types: tuple = ("new_pick",),
) -> dict:
    """
    Assemble the structured scorecard package for the monthly report.

    Reuses the SAME rule-based pipeline as the Recommendations History page so the
    monthly report and the on-page scorecard can never disagree:
        match_recs_to_trades → compute_outcomes(min_days=REC_SCORE_MIN_DAYS)
        → summary_stats / by_composite_band / by_verdict

    `rec_types` scopes the report to ACTIONABLE recommendations. Default =
    ("new_pick",): only names surfaced as "New Positions to Initiate" (they cleared
    all gates). The awareness-only "More Buy Candidates" feed (Conflicted / Unverified
    names the App steers you away from) is excluded — the report judges the engine's
    actual entry picks and whether you acted on them, not the awareness feed. Pass
    rec_types=None to include every surfacing.

    recs_df:            recommendations surfaced in [period_start, period_end].
    trades_df:          all trades (to detect acted-on signals).
    current_prices:     {ticker: price} for marking open positions (caller fetches).
    spy_close_by_date:  {date: close} SPY series for the regime/alpha benchmark.
    weekly_rows:        recent weekly_debriefs dicts (perf/spy/alpha) for trajectory.
    min_graded:         matured graded recs required before Q0 is narrated.

    Returns a dict consumed by generate_report(). has_data=False when no actionable
    recs surfaced in the window (caller surfaces "nothing to report yet").
    """
    from stock_analyzer.recommendations_history import (
        match_recs_to_trades, compute_outcomes, summary_stats,
        by_composite_band, by_verdict, report_viz_snapshot,
    )
    from stock_analyzer.constants import REC_SCORE_MIN_DAYS

    package: dict = {
        "period_start":   str(period_start),
        "period_end":     str(period_end),
        "rec_scope":      ", ".join(rec_types) if rec_types else "all",
        "has_data":       False,
        "q0_ready":       False,
        "min_graded":     min_graded,
        "viz":            None,
        "n_total":        0,
        "n_acted":        0,
        "n_missed":       0,
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
    }

    if recs_df is None or (hasattr(recs_df, "empty") and recs_df.empty):
        return package

    # Compute outcomes over the FULL window (every rec_type) FIRST. The visual snapshot
    # and the distinct acted/missed counts depend on "acted on via ANY surfacing"
    # detection that SPANS rec_types — a name bought on a day it surfaced as a buy-
    # candidate must not read as a missed New Position. (Pre-filtering to new_pick here,
    # as an earlier version did, silently dropped that safeguard and made the saved
    # counts disagree with the live Recommendations-History view.) The aggregate MEANS
    # are scoped to actionable rec_types afterwards, so Q0/Q1 still judge only the
    # engine's real entry picks, not the awareness feed.
    matched      = match_recs_to_trades(recs_df, trades_df)
    enriched_all = compute_outcomes(
        matched, current_prices or {}, today=period_end,
        spy_close_by_date=spy_close_by_date or {}, min_days=REC_SCORE_MIN_DAYS,
    )
    if not enriched_all:
        return package

    # Visual snapshot (decision-flow Sankey + alpha-by-band bar + ranked missed bar).
    # DISTINCT-by-ticker counts (a name recurring across days counts once — the basis
    # the chart uses, never the misleadingly large surfacing count). FROZEN with the
    # report (db viz_json) and re-rendered verbatim; the app shares this exact helper on
    # its live-fallback path so a frozen report and a recompute can never diverge.
    viz  = report_viz_snapshot(enriched_all, rec_types=rec_types)
    flow = viz["flow"]
    package["viz"]         = viz
    package["has_data"]    = flow["n_total"] > 0
    package["n_total"]     = flow["n_total"]
    package["n_acted"]     = flow["n_acted"]
    package["n_missed"]    = flow["n_missed"]
    package["action_rate"] = (
        round(flow["n_acted"] / flow["n_total"] * 100.0, 1) if flow["n_total"] else None
    )
    if not package["has_data"]:
        return package   # no actionable (New Position) names surfaced in the window

    # Aggregate means / bands / verdicts — scoped to actionable rec_types (the report's
    # Q0/Q1 subject). summary_stats counts surfacings, used only for the graded-outcome
    # aggregates (alpha, best/worst), never for the headline name counts above.
    scoped = [r for r in enriched_all if rec_types is None or r.get("rec_type") in rec_types]
    stats  = summary_stats(scoped)
    package["n_graded"]   = stats["n_priced"]   # surfacings matured enough to grade (internal)
    package["engine_alpha_pct"] = stats["avg_acted_alpha"]   # Q0 headline (mean over acted picks)
    package["avg_acted_pct"]    = stats["avg_acted_pct"]
    package["avg_missed_pct"]   = stats["avg_missed_pct"]
    package["missed_alpha"]     = stats["missed_alpha"]
    package["avg_acted_alpha"]  = stats["avg_acted_alpha"]
    package["avg_missed_alpha"] = stats["avg_missed_alpha"]
    package["best"]   = stats["best"]
    package["worst"]  = stats["worst"]
    package["q0_ready"] = stats["n_priced"] >= min_graded

    package["band_rows"]    = by_composite_band(scoped)
    package["verdict_rows"] = by_verdict(scoped)

    if weekly_rows:
        for w in weekly_rows:
            package["weekly_trajectory"].append({
                "week_ending":     str(w.get("week_ending", "")),
                "performance_pct": w.get("performance_pct"),
                "spy_pct":         w.get("spy_pct"),
                "alpha_pct":       w.get("alpha_pct"),
            })

    return package


def _format_prompt(package: dict) -> str:
    lines = [
        f"Period: {package['period_start']} to {package['period_end']}",
        "",
        "SCOPE: these are the App's ACTIONABLE entry recommendations only — names "
        "surfaced as \"New Positions to Initiate\" (they cleared all gates, composite "
        "≥ the entry threshold). The awareness-only \"More Buy Candidates\" feed "
        "(names the App surfaced but flagged to skip) is EXCLUDED. So a high miss "
        "count is not awareness noise — every one is a name the App told you to "
        "consider initiating. Do not reference the awareness feed.",
        "",
        f"Distinct New-Position names surfaced this period: {package['n_total']}. "
        f"You acted on {package['n_acted']} and did not act on {package['n_missed']}. "
        "Every count here is a DISTINCT ticker — a name that surfaced on many days counts "
        "once. These are the only name counts that exist for this report.",
        "",
        "COUNT DISCIPLINE — the reader sees these exact distinct counts in a header and a "
        "flow chart directly above your text, so your words must agree with them:",
        f"  - surfaced = {package['n_total']}, acted on = {package['n_acted']}, "
        f"not acted on = {package['n_missed']}.",
        f"  - NEVER state a count of names, recommendations, or outcomes greater than "
        f"{package['n_total']}. There is NO separate, larger 'graded outcomes' tally — do "
        "not invent one. Refer to how many have matured in WORDS (e.g. 'most have matured', "
        "'only a few have matured'), never as a new number.",
        "  - Percentages (alpha, outcome %, action rate) should be quoted as given; only "
        "raw NAME counts are constrained to the distinct figures above.",
    ]
    if not package.get("q0_ready"):
        lines.append(
            f"NOTE: too few of these names have matured enough to grade entry quality "
            f"fairly (the engine needs at least {package['min_graded']} matured before its "
            "pick quality can be judged). Keep the Entry quality section to one honest "
            "sentence and do not over-read the band table."
        )

    lines += [
        "",
        "Average outcomes across matured positions (alpha = return minus SPY, same window):",
        f"  Action rate: {package['action_rate']:.0f}%" if package.get("action_rate") is not None else "  Action rate: N/A",
        f"  Avg outcome — acted: {_pct(package.get('avg_acted_pct'))} · missed: {_pct(package.get('avg_missed_pct'))}",
        f"  Avg ALPHA vs SPY — acted: {_pct(package.get('avg_acted_alpha'))} · missed: {_pct(package.get('avg_missed_alpha'))}",
        f"  Missed-alpha (missed minus acted; positive = money left on the table): {_pct(package.get('missed_alpha'))}",
    ]
    if package.get("best"):
        b = package["best"]
        lines.append(f"  Best outcome: {b['ticker']} {_pct(b['outcome_pct'])} (alpha {_pct(b.get('alpha_pct'))}, {'acted' if b['acted_on'] else 'missed'})")
    if package.get("worst"):
        w = package["worst"]
        lines.append(f"  Worst outcome: {w['ticker']} {_pct(w['outcome_pct'])} (alpha {_pct(w.get('alpha_pct'))}, {'acted' if w['acted_on'] else 'missed'})")

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
        lines.append("By cross-check verdict (Confirmed = the App's actual recommendations; others = awareness it steered you away from):")
        for row in package["verdict_rows"]:
            lines.append(_band_line(row))

    if package.get("weekly_trajectory"):
        lines.append("")
        lines.append("Weekly performance trajectory (most recent first):")
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
    model: str = "claude-sonnet-4-6",
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
        import anthropic
        client   = anthropic.Anthropic(api_key=api_key)
        prompt   = _format_prompt(package)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_system_prompt(),
            messages=[{"role": "user", "content": prompt}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        text     = response.content[0].text if response.content else ""
        sections = _parse_response(text)
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
