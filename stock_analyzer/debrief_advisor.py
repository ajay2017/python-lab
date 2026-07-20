"""
Portfolio Debrief Advisor — F-3 AI Intelligence Layer.

Generates a weekly retrospective: what happened to the portfolio, which signals
were acted on or ignored, and what behavioural patterns showed up.

Weekly cadence: runs Sunday evening via the cron (same slot as thesis reviews).
Also callable on-demand from the app's AI Insights page.

Design: LLM narrates only what is in the data package — it cannot invent trades,
prices, or events. Returns None on any failure so callers surface an offline state.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from stock_analyzer.constants import LLM_REQUEST_TIMEOUT_SEC


# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a disciplined portfolio advisor helping an individual investor reflect on the past week. Write a structured weekly debrief in four sections. Be factual, concise, and neutral — observation not judgement. Write in second person ("Your portfolio..."). Plain language, no jargon. Target 400–500 words total.

Section 1 — WHAT HAPPENED (2–3 sentences):
Performance vs benchmark. Name the top contributors and detractors from the data package (these already exclude closed positions). Facts only.
IMPORTANT — metric framing: the "Portfolio return" figure is the week's equity-position value change (Mon–Fri), not a money-weighted investment return. Say "equity positions gained/fell X%" or "portfolio value moved X% this week" — never "the portfolio returned X%" or imply it is a true investment-return metric. Do NOT reference or compare against the Account page's all-time figure; they measure different things.

Section 2 — DECISIONS YOU MADE (bullet list):
For each actionable signal surfaced this week: did the investor act or not, and what happened to the name by week-end? Be specific.
Example: "• NVDA: New position signal (confirmed, surfaced 3×). You did not act. Name fell 6.8% by Friday."
If a ticker appears under "Positions fully closed this week", write ONLY: "• TICKER: Position closed this week." — do NOT describe it as a price loss or wipeout; closing a position on a signal is correct behaviour.
If no signals were surfaced, write "• No actionable signals were surfaced this week."

Section 3 — PATTERNS THIS WEEK (1–3 bullets, or "No clear pattern this week." if none):
What behavioural pattern, if any, showed up? Combine this week's signals with any "Behavioral pattern" and "Decision quality" blocks in the data package:
- If a momentum tendency is provided: name whether the investor chases momentum (higher action rate on already-hot signals) or fades it (contrarian lean toward overlooked names), and quote the pp gap.
- If a conviction-tier pattern is provided: name whether action rate is higher on Strong Buy vs plain Buy signals, or the reverse, and what that suggests.
- If a Decision Quality grade is provided: include a single concise line (e.g., "Month-to-date decision quality: B (72/100) across 8 trades").
Other named patterns (use only when applicable to this week):
- Signal follower: acted on ≥80% of actionable signals this week
- Concentration creep: added to a name already above single-name ceiling
- Calm week: no signals fired or all signals acted on cleanly
Note: a low raw action rate alone is NOT a named pattern — only flag it if it tracks a specific dimension like conviction tier or momentum.

Section 4 — ONE THING TO WATCH (1–2 sentences):
One forward-looking observation grounded in the data. A position approaching a gate, a weakening/broken thesis, a pattern worth monitoring.

Format your response with exactly these bold headers:
**What happened**
[text]

**Decisions you made**
[bullets]

**Patterns this week**
[bullets or "No clear pattern this week."]

**One thing to watch**
[text]

Rules:
- Only use facts from the data package. Never invent trades, prices, or events.
- Positions listed under "Positions fully closed this week" were intentionally sold — always narrate as "position closed", NEVER as a price loss or total wipeout.
- Do not recommend buying, selling, or any specific action.
- No disclaimers. End after the four sections."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:+.1f}%"


# ── Data package builder ──────────────────────────────────────────────────────

def build_debrief_package(
    week_ending: date,
    snapshots_df,
    recs_df,
    trades_df,
    spy_week_pct: float | None = None,
    broken_theses: list[str] | None = None,
    all_recs_df=None,
) -> dict:
    """
    Assemble the structured data package for the LLM.

    snapshots_df: DataFrame with columns snapshot_date, ticker, shares, close_price
                  covering the week (ideally 5 trading days).
    recs_df:      DataFrame of recommendations surfaced during the week.
    trades_df:    DataFrame of all trades (to detect acted-on signals).
    spy_week_pct: SPY % return for the same period (optional).
    broken_theses: list of tickers with BROKEN thesis status (from F-1).

    Returns a dict consumed by generate_debrief().
    """
    import pandas as pd

    week_start = week_ending - timedelta(days=6)

    package: dict = {
        "week_ending":      str(week_ending),
        "week_start":       str(week_start),
        "has_snapshots":    False,
        "days_available":   0,
        "performance_pct":  None,
        "spy_pct":          spy_week_pct,
        "alpha_pct":        None,
        "week_start_value": None,
        "week_end_value":   None,
        "net_flow":         None,
        "week_had_trades":  False,
        "contributors":     [],
        "detractors":       [],
        "closed_positions":       [],
        "recs_surfaced":          [],
        "signals_ignored":        [],
        "broken_theses":          broken_theses or [],
        "behavioral":             {},
        "decision_quality_month": None,
    }

    if snapshots_df is None or (hasattr(snapshots_df, "empty") and snapshots_df.empty):
        return package

    snap = snapshots_df.copy()
    snap["snapshot_date"] = snap["snapshot_date"].astype(str).str[:10]
    week_dates = sorted(snap["snapshot_date"].unique())
    package["days_available"] = len(week_dates)

    if len(week_dates) < 5:
        return package

    package["has_snapshots"] = True
    start_date = week_dates[0]
    end_date   = week_dates[-1]
    start_snap = snap[snap["snapshot_date"] == start_date]
    end_snap   = snap[snap["snapshot_date"] == end_date]

    # Portfolio value
    start_val = float((start_snap["shares"] * start_snap["close_price"]).sum()) if not start_snap.empty else 0.0
    end_val   = float((end_snap["shares"] * end_snap["close_price"]).sum())   if not end_snap.empty else 0.0
    package["week_start_value"] = start_val if start_val > 0 else None
    package["week_end_value"]   = end_val   if end_val   > 0 else None

    if start_val > 0 and end_val > 0:
        perf = ((end_val - start_val) / start_val) * 100
        package["performance_pct"] = round(perf, 2)
        if spy_week_pct is not None:
            package["alpha_pct"] = round(perf - spy_week_pct, 2)

    # Flag weeks with trades so the LLM can caveat the return figure.
    # A clean cash-flow-adjusted return requires daily cash/margin history
    # (not available — snapshots are equity-only). Margin buys in particular
    # can't be treated as external capital flows.
    week_had_trades = False
    if trades_df is not None and not (hasattr(trades_df, "empty") and trades_df.empty):
        _wt = trades_df[
            (trades_df["traded_at"].astype(str).str[:10] >= start_date) &
            (trades_df["traded_at"].astype(str).str[:10] <= end_date) &
            trades_df["action"].str.upper().isin(["BUY", "SELL"])
        ]
        week_had_trades = not _wt.empty
    package["net_flow"] = None
    package["week_had_trades"] = week_had_trades

    # Per-position P&L
    start_val_by_tk: dict[str, float] = {
        str(r["ticker"]).upper(): float(r["shares"]) * float(r["close_price"])
        for _, r in start_snap.iterrows()
    }
    end_val_by_tk: dict[str, float] = {
        str(r["ticker"]).upper(): float(r["shares"]) * float(r["close_price"])
        for _, r in end_snap.iterrows()
    }

    pos_pnl = []
    for tk in set(list(start_val_by_tk) + list(end_val_by_tk)):
        sv = start_val_by_tk.get(tk, 0.0)
        ev = end_val_by_tk.get(tk, 0.0)
        pnl = ev - sv
        pct = ((ev - sv) / sv * 100) if sv > 0 else None
        pos_pnl.append({"ticker": tk, "pnl": pnl, "pct": pct})

    pos_pnl.sort(key=lambda x: x["pnl"], reverse=True)

    # Detect positions fully closed (sold) this week — their end-of-week snapshot
    # value is $0 not because the price collapsed but because the SELL removed them.
    # Exclude from contributors/detractors so the LLM doesn't narrate them as losses.
    closed_tickers: set[str] = set()
    if trades_df is not None and not (hasattr(trades_df, "empty") and trades_df.empty):
        _week_sells = trades_df[
            (trades_df["traded_at"].astype(str).str[:10] >= start_date) &
            (trades_df["traded_at"].astype(str).str[:10] <= end_date) &
            (trades_df["action"].str.upper() == "SELL")
        ]
        for _stk in _week_sells["ticker"].astype(str).str.upper().unique():
            if end_val_by_tk.get(_stk, 0.0) == 0.0:
                closed_tickers.add(_stk)
    package["closed_positions"] = sorted(closed_tickers)

    package["contributors"] = [p for p in pos_pnl if p["pnl"] > 0 and p["ticker"] not in closed_tickers][:3]
    package["detractors"]   = sorted(
        [p for p in pos_pnl if p["pnl"] < 0 and p["ticker"] not in closed_tickers],
        key=lambda x: x["pnl"]
    )[:3]
    _pnl_by_tk = {p["ticker"]: p for p in pos_pnl}

    # Recommendations surfaced this week — actionable types only.
    # buy_candidate is an awareness-only feed; including it inflates the signal
    # count and makes action-rate comparisons misleading.
    if recs_df is not None and not (hasattr(recs_df, "empty") and recs_df.empty):
        date_col = "rec_date" if "rec_date" in recs_df.columns else (
            "surfaced_at" if "surfaced_at" in recs_df.columns else None
        )
        if date_col:
            week_recs = recs_df[
                recs_df[date_col].astype(str).str[:10] >= str(week_start)
            ].copy()

            if "rec_type" in week_recs.columns:
                week_recs = week_recs[week_recs["rec_type"].isin(["new_pick", "add_winner"])]

            week_trades = pd.DataFrame()
            if trades_df is not None and not (hasattr(trades_df, "empty") and trades_df.empty):
                week_trades = trades_df[
                    trades_df["traded_at"].astype(str).str[:10] >= str(week_start)
                ]
            acted_tickers = set(week_trades["ticker"].astype(str).str.upper()) if not week_trades.empty else set()

            # Deduplicate by ticker — one entry with a times_surfaced count and
            # the dominant verdict across the week's appearances.
            by_ticker: dict[str, dict] = {}
            for _, rec in week_recs.iterrows():
                tk       = str(rec.get("ticker", "")).upper()
                if not tk:
                    continue
                verdict  = str(rec.get("verdict") or "")
                rec_type = str(rec.get("rec_type") or "")
                if tk not in by_ticker:
                    by_ticker[tk] = {
                        "ticker":         tk,
                        "rec_type":       rec_type,
                        "_verdicts":      [verdict] if verdict else [],
                        "times_surfaced": 1,
                        "acted":          tk in acted_tickers,
                        # Suppress end_week_pct for closed positions — their -100%
                        # is a closure accounting artifact, not a price event.
                        "end_week_pct":   None if tk in closed_tickers
                                          else _pnl_by_tk.get(tk, {}).get("pct"),
                    }
                else:
                    by_ticker[tk]["times_surfaced"] += 1
                    if verdict:
                        by_ticker[tk]["_verdicts"].append(verdict)

            for entry in by_ticker.values():
                verdicts = entry.pop("_verdicts")
                entry["verdict"] = max(set(verdicts), key=verdicts.count) if verdicts else ""
                package["recs_surfaced"].append(entry)
                if not entry["acted"]:
                    package["signals_ignored"].append(entry)

    # ── Behavioral fingerprint patterns (all-time, buy-side) ─────────────────
    # Uses the full rec history (all_recs_df) rather than the week-scoped recs_df
    # so the pattern reflects the investor's durable tendencies, not just 7 days.
    try:
        from stock_analyzer.behavioral_fingerprint import (
            momentum_recency_pattern as _mrp,
            conviction_tier_pattern  as _ctp,
        )
        from stock_analyzer.constants import (
            BEHAVIORAL_MIN_SAMPLE_N as _BMIN,
            COMPOSITE_STRONG_BUY    as _CSB,
        )
        _hist = all_recs_df if (all_recs_df is not None and not getattr(all_recs_df, "empty", True)) else recs_df
        if _hist is not None and not getattr(_hist, "empty", True) and \
                trades_df is not None and not getattr(trades_df, "empty", True):
            _ar = _hist[_hist["rec_type"].isin(["new_pick", "add_winner"])].copy() \
                  if "rec_type" in _hist.columns else _hist.copy()
            _buy_set = set(
                trades_df[trades_df["action"].str.upper() == "BUY"]["ticker"]
                .astype(str).str.upper()
            )
            _matched: list[dict] = [
                {
                    "momentum_score":  r.get("momentum_score"),
                    "composite_score": r.get("composite_score"),
                    "acted_on":        str(r.get("ticker", "")).upper() in _buy_set,
                }
                for _, r in _ar.iterrows()
                if str(r.get("ticker", "")).strip()
            ]
            _beh: dict = {}
            _mp = _mrp(_matched, min_n=int(_BMIN))
            if _mp:
                _beh["momentum"] = _mp
            _cp = _ctp(_matched, strong_buy_floor=float(_CSB), min_n=int(_BMIN))
            if _cp:
                _beh["conviction"] = _cp
            package["behavioral"] = _beh
    except Exception:
        pass  # behavioral enrichment is optional — never block the debrief

    # ── Decision quality — current calendar month's running grade ─────────────
    try:
        from stock_analyzer.decision_quality import build_monthly_grades as _bmg
        if trades_df is not None and not getattr(trades_df, "empty", True):
            _grades = _bmg(trades_df)
            _cur_month = str(week_ending)[:7]
            _cur_grade = next((g for g in _grades if g.get("month_str") == _cur_month), None)
            if _cur_grade:
                package["decision_quality_month"] = {
                    "month_str":       _cur_grade["month_str"],
                    "grade_letter":    _cur_grade["grade_letter"],
                    "composite_score": _cur_grade["composite_score"],
                    "trade_count":     _cur_grade["trade_count"],
                    "win_rate":        _cur_grade.get("win_rate"),
                    "profit_factor":   _cur_grade.get("profit_factor"),
                }
    except Exception:
        pass  # decision quality enrichment is optional — never block the debrief

    return package


def _format_prompt(package: dict) -> str:
    lines = [f"Week: {package['week_start']} to {package['week_ending']}", ""]

    if package.get("performance_pct") is not None:
        lines.append("Portfolio performance (equity positions only — cash and margin excluded):")
        if package.get("week_start_value"):
            lines.append(f"  Start value: ${package['week_start_value']:,.0f}")
        if package.get("week_end_value"):
            lines.append(f"  End value:   ${package['week_end_value']:,.0f}")
        lines.append(f"  Portfolio return: {_pct(package.get('performance_pct'))}")
        lines.append(f"  SPY return:       {_pct(package.get('spy_pct'))}")
        lines.append(f"  Alpha:            {_pct(package.get('alpha_pct'))}")
        if package.get("week_had_trades"):
            lines.append("  Note: Trades (buys/sells) occurred this week. The return above reflects"
                         " equity market-value change and may include position-size effects"
                         " (e.g. margin-funded buys), not pure price appreciation.")
    else:
        lines.append("Portfolio performance: insufficient snapshot data for this period.")

    lines.append("")
    if package.get("contributors"):
        lines.append("Top contributors (by $ gain):")
        for c in package["contributors"]:
            pct_str = f" ({_pct(c['pct'])})" if c.get("pct") is not None else ""
            lines.append(f"  {c['ticker']}: +${c['pnl']:,.0f}{pct_str}")
    if package.get("detractors"):
        lines.append("Top detractors (by $ loss):")
        for d in package["detractors"]:
            pct_str = f" ({_pct(d['pct'])})" if d.get("pct") is not None else ""
            lines.append(f"  {d['ticker']}: -${abs(d['pnl']):,.0f}{pct_str}")

    lines.append("")
    _rec_type_label = {"new_pick": "New position", "add_winner": "Add to winner"}
    if package.get("recs_surfaced"):
        lines.append(
            f"Actionable signals surfaced this week ({len(package['recs_surfaced'])} unique names,"
            " new_pick and add_winner only — buy_candidate awareness feed excluded):"
        )
        for r in package["recs_surfaced"]:
            acted_str  = "acted on" if r["acted"] else "NOT acted on"
            end_str    = f"; ended week {_pct(r['end_week_pct'])}" if r.get("end_week_pct") is not None else ""
            times      = r.get("times_surfaced", 1)
            times_str  = f" (surfaced {times}× this week)" if times > 1 else ""
            label      = _rec_type_label.get(r.get("rec_type", ""), r.get("rec_type", "Signal"))
            verdict    = r.get("verdict", "")
            verdict_str = f" [{verdict}]" if verdict else ""
            lines.append(f"  {r['ticker']}: {label}{verdict_str}{times_str} — {acted_str}{end_str}")
    else:
        lines.append("No actionable signals were surfaced this week.")

    if package.get("closed_positions"):
        lines.append("")
        lines.append(f"Positions fully closed (sold) this week: {', '.join(package['closed_positions'])}")
        lines.append(
            "  These tickers were sold during the week. Their end-of-week snapshot value"
            " is $0 — an accounting artifact of the sell, not a market-price collapse."
            " They are excluded from the contributor/detractor table above."
        )

    if package.get("broken_theses"):
        lines.append("")
        lines.append(f"Positions with BROKEN thesis: {', '.join(package['broken_theses'])}")

    beh = package.get("behavioral") or {}
    if beh.get("momentum"):
        mp = beh["momentum"]
        h  = mp.get("high", {})
        lo = mp.get("low",  {})
        lines.append("")
        lines.append("Behavioral pattern — momentum tendency (all-time buy-side signal history):")
        lines.append(f"  High-momentum signals: {h.get('n', 0)} surfaced, "
                     f"{h.get('n_acted', 0)} acted on ({h.get('action_rate', 0)*100:.0f}% rate)")
        lines.append(f"  Low-momentum signals:  {lo.get('n', 0)} surfaced, "
                     f"{lo.get('n_acted', 0)} acted on ({lo.get('action_rate', 0)*100:.0f}% rate)")
        lines.append(f"  Pattern direction: {mp.get('direction', 'flat')} "
                     f"(delta {mp.get('delta_pp', 0):+.1f}pp, "
                     f"positive = chases momentum, negative = contrarian/fades)")

    if beh.get("conviction"):
        cp = beh["conviction"]
        sb = cp.get("strong_buy", {})
        b  = cp.get("buy", {})
        lines.append("")
        lines.append("Behavioral pattern — conviction tier (all-time buy-side signal history):")
        lines.append(f"  Strong Buy signals: {sb.get('n', 0)} surfaced, "
                     f"{sb.get('n_acted', 0)} acted on ({sb.get('action_rate', 0)*100:.0f}% rate)")
        lines.append(f"  Buy signals:        {b.get('n', 0)} surfaced, "
                     f"{b.get('n_acted', 0)} acted on ({b.get('action_rate', 0)*100:.0f}% rate)")
        lines.append(f"  Delta: {cp.get('delta_pp', 0):+.1f}pp "
                     f"(positive = higher action rate on Strong Buy, negative = reverse)")

    dq = package.get("decision_quality_month")
    if dq:
        lines.append("")
        lines.append(f"Decision quality — {dq['month_str']} month to date ({dq['trade_count']} trades):")
        lines.append(f"  Grade: {dq['grade_letter']} (composite {dq['composite_score']:.0f}/100)")
        if dq.get("win_rate") is not None:
            lines.append(f"  Win rate: {dq['win_rate']*100:.0f}%")
        if dq.get("profit_factor") is not None:
            lines.append(f"  Profit factor: {dq['profit_factor']:.2f}")

    return "\n".join(lines)


def _parse_response(text: str) -> dict:
    sections = {
        "section_facts":     "",
        "section_decisions": "",
        "section_patterns":  "",
        "section_watchnext": "",
    }
    headers = {
        "what happened":      "section_facts",
        "decisions you made": "section_decisions",
        "patterns this week": "section_patterns",
        "one thing to watch": "section_watchnext",
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

def generate_debrief(
    package: dict,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 700,
) -> dict | None:
    """
    Call the LLM to generate a weekly portfolio debrief.

    Returns a save-ready dict (for db.save_weekly_debrief) or None on any failure.
    """
    if not api_key or not package.get("has_snapshots"):
        return None
    try:
        import anthropic
        client   = anthropic.Anthropic(api_key=api_key)
        prompt   = _format_prompt(package)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        text     = response.content[0].text if response.content else ""
        sections = _parse_response(text)
        return {
            "week_ending":      package["week_ending"],
            "generated_at":     datetime.now(timezone.utc).isoformat(),
            "performance_pct":  package.get("performance_pct"),
            "spy_pct":          package.get("spy_pct"),
            "alpha_pct":        package.get("alpha_pct"),
            **sections,
            "email_sent":       False,
        }
    except Exception:
        return None
