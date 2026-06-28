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
Performance vs benchmark. Name the top contributors and detractors. Facts only.

Section 2 — DECISIONS YOU MADE (bullet list):
For each recommendation surfaced this week: did the investor act or not, and what happened to the name by week-end? Be specific. Example: "• NVDA: TRIM signal surfaced. You held. Name fell 6.8% by Friday."
If no recommendations were surfaced, write "• No recommendations were surfaced this week."

Section 3 — PATTERNS THIS WEEK (1–3 bullets, or "No clear pattern this week." if none):
What behavioural pattern, if any, showed up? Use only this week's data. Named patterns when applicable:
- Signal follower: acted on ≥80% of signals
- Selective actor: acted on BUY signals but ignored TRIM/EXIT (or vice versa)
- Concentration creep: added to a name already above single-name ceiling
- Calm week: no signals fired or all signals acted on cleanly

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
        "contributors":     [],
        "detractors":       [],
        "recs_surfaced":    [],
        "signals_ignored":  [],
        "broken_theses":    broken_theses or [],
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
    package["contributors"] = [p for p in pos_pnl if p["pnl"] > 0][:3]
    package["detractors"]   = sorted([p for p in pos_pnl if p["pnl"] < 0], key=lambda x: x["pnl"])[:3]
    _pnl_by_tk = {p["ticker"]: p for p in pos_pnl}

    # Recommendations surfaced this week
    if recs_df is not None and not (hasattr(recs_df, "empty") and recs_df.empty):
        date_col = "rec_date" if "rec_date" in recs_df.columns else (
            "surfaced_at" if "surfaced_at" in recs_df.columns else None
        )
        if date_col:
            week_recs = recs_df[
                recs_df[date_col].astype(str).str[:10] >= str(week_start)
            ].copy()

            week_trades = pd.DataFrame()
            if trades_df is not None and not (hasattr(trades_df, "empty") and trades_df.empty):
                week_trades = trades_df[
                    trades_df["traded_at"].astype(str).str[:10] >= str(week_start)
                ]
            acted_tickers = set(week_trades["ticker"].astype(str).str.upper()) if not week_trades.empty else set()

            for _, rec in week_recs.iterrows():
                tk      = str(rec.get("ticker", "")).upper()
                verdict = str(rec.get("verdict", ""))
                acted   = tk in acted_tickers
                end_pct = _pnl_by_tk.get(tk, {}).get("pct")
                item = {"ticker": tk, "verdict": verdict, "acted": acted, "end_week_pct": end_pct}
                package["recs_surfaced"].append(item)
                if not acted and verdict in ("TRIM", "EXIT", "WATCH"):
                    package["signals_ignored"].append(item)

    return package


def _format_prompt(package: dict) -> str:
    lines = [f"Week: {package['week_start']} to {package['week_ending']}", ""]

    if package.get("performance_pct") is not None:
        lines.append("Portfolio performance:")
        if package.get("week_start_value"):
            lines.append(f"  Start value: ${package['week_start_value']:,.0f}")
        if package.get("week_end_value"):
            lines.append(f"  End value:   ${package['week_end_value']:,.0f}")
        lines.append(f"  Portfolio return: {_pct(package.get('performance_pct'))}")
        lines.append(f"  SPY return:       {_pct(package.get('spy_pct'))}")
        lines.append(f"  Alpha:            {_pct(package.get('alpha_pct'))}")
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
    if package.get("recs_surfaced"):
        lines.append(f"Recommendations surfaced this week ({len(package['recs_surfaced'])} total):")
        for r in package["recs_surfaced"]:
            acted_str = "acted on" if r["acted"] else "NOT acted on"
            end_str   = f"; ended week {_pct(r['end_week_pct'])}" if r.get("end_week_pct") is not None else ""
            lines.append(f"  {r['ticker']}: {r['verdict']} — {acted_str}{end_str}")
    else:
        lines.append("No recommendations were surfaced this week.")

    if package.get("broken_theses"):
        lines.append("")
        lines.append(f"Positions with BROKEN thesis: {', '.join(package['broken_theses'])}")

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
