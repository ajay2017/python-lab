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
State the account result: your account (net equity) moved X% ($Y) from the <d0> close to the <d1> close; SPY moved Z% over the same dates. If leverage is given, say once that at about L× leverage the account moves roughly L times the holdings' own move while SPY is unlevered — never call the gap skill. If the account result is unknown, say so in one sentence with the reason given; never estimate. Name contributors/detractors with their $: a position sold this week shows the move up to the sale, one bought this week the move from its buy price. If names are listed as not measured, say their P&L isn't included. If margin interest is listed as not recorded for a month, say the interest line may be understated. Do not speculate about the unexplained remainder. This is the same figure as 💰 Account → Reports → Last week. Facts only.

Section 2 — DECISIONS YOU MADE (bullet list):
For each actionable signal surfaced this week — both new-position entry signals AND protective WATCH/TRIM/EXIT signals — did the investor act or not, and what happened to the name by week-end? Be specific. When a conviction tier (Strong Buy / Buy) is given for an entry signal, name it.
Example (entry): "• NVDA: Strong Buy new position signal (confirmed, surfaced 3×). You did not act. Name fell 6.8% by Friday."
Example (protective): "• TSLA: TRIM signal (surfaced 2×). You held — not acted on. Position is down 9.2% from peak."
If a ticker appears under "Positions fully closed this week", write ONLY: "• TICKER: Position closed this week." — do NOT describe it as a price loss or wipeout; closing a position on a signal is correct behaviour.
If no signals of either kind were surfaced, write "• No actionable signals were surfaced this week."

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
- Positions listed under "Positions fully closed this week" were intentionally sold — in the Decisions bullets always narrate as "position closed", NEVER as a price loss, a wipeout or −100%. A closed position may still appear among the contributors/detractors with its real move (the move up to the sale).
- Do not recommend buying, selling, or any specific action.
- No disclaimers. End after the four sections."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pct(v: float | None) -> str:
    if v is None or (isinstance(v, float) and v != v):   # NaN != NaN
        return "N/A"
    return f"{v:+.1f}%"


def _traded_at_et_dates(trades_df):
    """ET calendar date for each `trades_df["traded_at"]` row.

    `traded_at` is a genuinely mixed-offset ISO string (mostly `+00:00`, plus
    `-04:00`/`-05:00` for imported rows `trade_time.py` has re-anchored to a
    wall-clock ET string) -- see that module's docstring. A raw
    `.astype(str).str[:10]` string-slice reads whatever calendar day happens
    to be embedded in the string (the UTC day for a real fill, the
    re-anchored ET day for an imported one) rather than the actual ET day,
    and misclassifies a genuine late-evening ET fill whose UTC-serialized
    date has already rolled to the next (or, near week_start, the previous)
    calendar day. `utc=True` + `format="ISO8601"` is required for the
    mixed-offset column (memory `feedback_pandas_mixed_tz_parsing`) --
    mirrors the idiom used throughout `tax_advisor.py`, `daily_briefing.py`,
    `risk_advisor.py`, `behavioral_fingerprint.py`, etc.
    """
    import pandas as pd
    return pd.to_datetime(
        trades_df["traded_at"], errors="coerce", utc=True, format="ISO8601"
    ).dt.tz_convert("America/New_York").dt.date


METRICS_VERSION = 2   # 1 = legacy equity-positions Mon→Fri close union marks (rows before 2026-10-20); 2 = period_attribution engine

_SHAPE_EPS = 1e-6


def _fnum(v) -> "float | None":
    """float or None; NaN/inf/non-numeric -> None (None means unknown, never 0)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def shape(shares_start, shares_end, bought_usd, sold_usd) -> "str | None":
    """How a position's week looked, from its share counts at the two period
    endpoints and the dollars bought/sold inside the window.

    closed (held at the start, none at the end) / opened (none at the start,
    held at the end) / round_trip (none at either end but traded in between) /
    traded, trimmed, added or held (held at both ends, by what traded).  None
    when neither held nor traded (no story to tell)."""
    s0 = (_fnum(shares_start) or 0.0) > _SHAPE_EPS
    s1 = (_fnum(shares_end) or 0.0) > _SHAPE_EPS
    bought = (_fnum(bought_usd) or 0.0) > 0
    sold = (_fnum(sold_usd) or 0.0) > 0
    if s0 and not s1:
        return "closed"
    if s1 and not s0:
        return "opened"
    if not s0 and not s1:
        return "round_trip" if (bought or sold) else None
    if bought and sold:
        return "traded"
    if sold:
        return "trimmed"
    if bought:
        return "added"
    return "held"


def _price_pct(rec: dict) -> "float | None":
    c0, c1 = _fnum(rec.get("close_start")), _fnum(rec.get("close_end"))
    if c0 is None or c1 is None or c0 <= 0:
        return None
    return (c1 / c0 - 1.0) * 100.0


def escape_md_dollars(text) -> str:
    """Escape `$` as a backslash-dollar for st.markdown (two bare `$` render as
    LaTeX).  Idempotent: an already-escaped backslash-dollar is left alone."""
    import re
    return re.sub(r"(?<!\\)\$", lambda _m: "\\$", str(text if text is not None else ""))


def signal_days_back(today, week_start) -> int:
    """`days_back` for a protective-signal read that must still reach the week's
    first day: measured from today, so a Friday click or a Saturday retry needs
    more than a fixed 10 days.  (today - week_start) + 1, never below 1."""
    return max(1, (today - week_start).days + 1)


def unknown_result_reason(recon, account_return) -> str:
    """Why the account result is unknown, branching on the stage that failed:
    the deposit/withdrawal read (account_return offline), an unmeasurable
    reconciliation (pre_coverage reason), an account return the engine could not
    compute while the reconciliation could, else a generic line."""
    from stock_analyzer import period_attribution as pa
    ar_status = account_return.get("status") if isinstance(account_return, dict) else None
    recon_status = recon.get("status") if isinstance(recon, dict) else None
    if ar_status == "offline":
        return "the deposit/withdrawal history couldn't be read, so the account's return can't be measured."
    if recon_status == "pre_coverage":
        return pa.pre_coverage_reason_text(recon)
    if ar_status == "pre_coverage":
        return ("the account's return couldn't be computed over this week's dates "
                "(no usable account records at both ends).")
    return "the account result couldn't be measured for this period."


def _names(rows) -> "list[str]":
    return sorted({str(r[0]) for r in (rows or []) if isinstance(r, (tuple, list)) and r})


def method_is_current(metrics_version) -> bool:
    """True when a saved debrief row was measured with the current method
    (METRICS_VERSION).  NULL / NaN / 1 (legacy equity-positions rows) -> False."""
    v = _fnum(metrics_version)
    return v is not None and int(v) == METRICS_VERSION


def numbers_behind(package: dict, core: dict | None) -> dict:
    """The legs behind a generated debrief, for the on-screen "Numbers behind
    this debrief" expander: every per-ticker leg, their sum, the engine's
    positions total and whether the two agree (to the cent).  Pure."""
    recon = core.get("recon") if isinstance(core, dict) else None
    recon = recon if isinstance(recon, dict) else {}
    per = recon.get("per_ticker")
    per = per if isinstance(per, dict) else {}
    pp = recon.get("pos_pnl")
    unmeasured_names = set(package.get("unmeasured", ()))
    legs = []
    for tk in sorted(per):
        rec = per[tk]
        legs.append({
            "ticker": tk, "total": _fnum(rec.get("total")),
            "shape": shape(rec.get("shares_start"), rec.get("shares_end"),
                           rec.get("bought_usd"), rec.get("sold_usd")),
            "held_through": _fnum(rec.get("held_through")), "sold_leg": _fnum(rec.get("sold_leg")),
            "bought_leg": _fnum(rec.get("bought_leg")), "round_trip": _fnum(rec.get("round_trip")),
            "measured": tk not in unmeasured_names,
        })
    legs_sum = sum(l["total"] for l in legs if l["total"] is not None)
    pos_total = _fnum(pp["total"]) if pp else None
    return {
        "legs": legs, "legs_sum": legs_sum if legs else None, "pos_total": pos_total,
        "legs_match": (pos_total is not None and round(legs_sum - pos_total, 2) == 0),
    }


# ── Data package builder ──────────────────────────────────────────────────────

def build_debrief_package(
    week_ending: date,
    core: dict | None,
    recs_df,
    trades_df,
    broken_theses: list[str] | None = None,
    all_recs_df=None,
    exit_signals_df=None,
) -> dict:
    """
    Assemble the structured data package for the LLM.

    core:         `period_inputs.compute_period_core(...)` -- {recon, leverage,
                  account_return} for the Mon-Sun week ending `week_ending`;
                  the SAME engine and figures as 💰 Account -> Reports -> Last
                  week.  None = the engine inputs could not be read (offline).
    recs_df:      DataFrame of recommendations surfaced during the week.
    trades_df:    DataFrame of all trades (to detect acted-on signals).
    broken_theses: list of tickers with BROKEN thesis status (from F-1).
    exit_signals_df: DataFrame from db.load_exit_signals() — WATCH/TRIM/EXIT
                  protective signals, the symmetric counterpart to recs_df's
                  entry signals. Optional; omitted gracefully when absent.

    Returns a dict consumed by generate_debrief().  `narratable` is False when
    the engine is offline -- generate_debrief() then writes nothing.
    """
    import pandas as pd
    from stock_analyzer import period_attribution as pa

    week_start = week_ending - timedelta(days=6)
    core = core if isinstance(core, dict) else None
    recon = (core or {}).get("recon")
    recon = recon if isinstance(recon, dict) else None
    ar = (core or {}).get("account_return")
    ar = ar if isinstance(ar, dict) else None
    lev = (core or {}).get("leverage")
    lev = lev if isinstance(lev, dict) else None

    package: dict = {
        "metrics_version":  METRICS_VERSION,
        "week_ending":      str(week_ending),
        "week_start":       str(week_start),
        "d0":               None,
        "d1":               None,
        "d1_short_of_period_end": False,
        "result_status":    "offline",
        "result_reason":    None,
        "performance_pct":  None,
        "spy_pct":          None,
        "spy_pending":      False,
        "alpha_pct":        None,
        "equity_start":     None,
        "equity_end":       None,
        "equity_change":    None,
        "dates_agree":      None,
        "return_d0":        None,
        "return_d1":        None,
        "max_leverage":     None,
        "leverage":         None,
        "waterfall":        None,
        "interest_gap_months": [],
        "unmeasured":       [],
        "contributors":     [],
        "detractors":       [],
        "closed_positions":       [],
        "recs_surfaced":          [],
        "signals_ignored":        [],
        "protective_signals":     [],
        "broken_theses":          broken_theses or [],
        "behavioral":             {},
        "decision_quality_month": None,
        "narratable":       False,
    }

    per_ticker: dict = {}
    if recon is not None:
        status = recon.get("status") or "offline"
        package["result_status"] = status
        package["d0"] = str(recon["d0"]) if recon.get("d0") else None
        package["d1"] = str(recon["d1"]) if recon.get("d1") else None
        package["d1_short_of_period_end"] = bool(recon.get("d1_short_of_period_end"))
        if status in ("ok", "partial"):
            package["equity_start"] = recon.get("equity_start")
            package["equity_end"] = recon.get("equity_end")
            package["equity_change"] = recon.get("equity_change")
        package["narratable"] = (status != "offline" and recon.get("pos_pnl") is not None)
        _pt = recon.get("per_ticker")
        per_ticker = _pt if isinstance(_pt, dict) else {}

        # Account result: the SAME flow-adjusted figure Reports shows (account_return).
        if ar is not None and ar.get("status") == "ok":
            package["performance_pct"] = ar.get("return_pct")
            package["spy_pct"] = ar.get("spy_return_pct")
            package["spy_pending"] = bool(ar.get("spy_d1_price_pending"))
            # the % is measured over account_return's own endpoints; keep them so the
            # label never borrows the reconciliation's dates when the two differ
            package["return_d0"] = str(ar["d0"]) if ar.get("d0") else None
            package["return_d1"] = str(ar["d1"]) if ar.get("d1") else None
            # the SAME leverage figure Reports' caveat uses (max of the two endpoint rows)
            package["max_leverage"] = ar.get("max_leverage")
            if package["performance_pct"] is not None and package["spy_pct"] is not None:
                package["alpha_pct"] = round(package["performance_pct"] - package["spy_pct"], 2)
            agree = (ar.get("d0") == recon.get("d0") and ar.get("d1") == recon.get("d1"))
            package["dates_agree"] = bool(agree)
            if not agree:
                # the % and the $ would describe different windows -- keep the %,
                # withhold the dollar change
                package["equity_change"] = None
                package["equity_start"] = None
                package["equity_end"] = None

        if package["performance_pct"] is None:
            package["result_reason"] = unknown_result_reason(recon, ar)

        if lev is not None and lev.get("status") == "ok":
            package["leverage"] = {
                "min": lev["min"]["leverage"], "max": lev["max"]["leverage"],
                "start": lev["start"]["leverage"], "end": lev["end"]["leverage"],
            }

        pp = recon.get("pos_pnl")
        inc = recon.get("income")
        res = recon.get("residual")
        fl = recon.get("flows")
        full = status in ("ok", "partial")
        package["waterfall"] = {
            "held_through": pp["held_through"] if pp else None,
            "sold_leg":     pp["sold_leg"] if pp else None,
            "bought_leg":   pp["bought_leg"] if pp else None,
            "round_trip":   pp["round_trip"] if pp else None,
            "flows_net":    fl["net"] if (full and fl is not None) else None,
            "dividends":    inc["dividends"] if (full and inc is not None) else None,
            "interest":     pa.interest_posted(recon) if full else None,
            "fees":         inc["fees"] if (full and inc is not None) else None,
            "unexplained":  res["total"] if (full and res is not None) else None,
        }
        gap = inc.get("interest_coverage_gap_months") if inc else None
        package["interest_gap_months"] = list(gap) if gap is not None else []

        unmeasured = set(_names(recon.get("unpriced"))) | set(_names(recon.get("journal_inconsistent")))
        package["unmeasured"] = sorted(unmeasured)

        rows = []
        for tk, rec in per_ticker.items():
            if tk in unmeasured:
                continue
            total = _fnum(rec.get("total"))
            if total is None:
                continue
            shp = shape(rec.get("shares_start"), rec.get("shares_end"),
                        rec.get("bought_usd"), rec.get("sold_usd"))
            rows.append({
                "ticker": tk, "pnl": total, "shape": shp,
                "price_pct": _price_pct(rec) if shp == "held" else None,
            })
        package["contributors"] = sorted(
            [r for r in rows if r["pnl"] > 0], key=lambda r: (-r["pnl"], r["ticker"]))[:3]
        package["detractors"] = sorted(
            [r for r in rows if r["pnl"] < 0], key=lambda r: (r["pnl"], r["ticker"]))[:3]

    # "Closed" = held going into the week and none left at the end.  A name
    # bought AND sold inside the week (round_trip) is not a closed position:
    # the prompt would otherwise say "Position closed this week" about a
    # name that was never held at the start.
    closed_tickers: set[str] = set()
    for tk, rec in per_ticker.items():
        if shape(rec.get("shares_start"), rec.get("shares_end"),
                 rec.get("bought_usd"), rec.get("sold_usd")) == "closed":
            closed_tickers.add(tk)
    package["closed_positions"] = sorted(closed_tickers)

    def _end_week_pct(tk: str):
        rec = per_ticker.get(tk)
        if rec is None or tk in closed_tickers:
            return None
        return _price_pct(rec)

    # Recommendations surfaced this week — actionable types only.
    # buy_candidate is an awareness-only feed; including it inflates the signal
    # count and makes action-rate comparisons misleading.
    if recs_df is not None and not (hasattr(recs_df, "empty") and recs_df.empty):
        date_col = "rec_date" if "rec_date" in recs_df.columns else (
            "surfaced_at" if "surfaced_at" in recs_df.columns else None
        )
        # Known, documented gap (data-foundation A5 follow-up, 2026-09-23):
        # this string-slice is CORRECT for the common "rec_date" path (a
        # plain date column, no tz offset) and deliberately NOT rewritten to
        # the utc=True/tz_convert idiom used elsewhere in this file -- doing
        # so would introduce a NEW off-by-one-day bug on every plain-date
        # row (midnight UTC is the prior evening in ET) to fix a rarer
        # fallback. The "surfaced_at" fallback path (a genuine timestamptz)
        # is unreachable in practice: db.py's loader always supplies
        # rec_date, so date_col never actually resolves to "surfaced_at" in
        # production. Left as-is; not queued as active work.
        if date_col:
            week_recs = recs_df[
                recs_df[date_col].astype(str).str[:10] >= str(week_start)
            ].copy()

            if "rec_type" in week_recs.columns:
                week_recs = week_recs[week_recs["rec_type"].isin(["new_pick", "add_winner"])]

            week_trades = pd.DataFrame()
            if trades_df is not None and not (hasattr(trades_df, "empty") and trades_df.empty):
                week_trades = trades_df[_traded_at_et_dates(trades_df) >= week_start]
            acted_tickers = set(week_trades["ticker"].astype(str).str.upper()) if not week_trades.empty else set()

            # Deduplicate by ticker — one entry with a times_surfaced count and
            # the dominant verdict across the week's appearances.
            by_ticker: dict[str, dict] = {}
            for _, rec in week_recs.iterrows():
                tk       = str(rec.get("ticker", "")).upper()
                if not tk:
                    continue
                verdict    = str(rec.get("verdict") or "")
                rec_type   = str(rec.get("rec_type") or "")
                conviction = str(rec.get("conviction") or "")
                if tk not in by_ticker:
                    by_ticker[tk] = {
                        "ticker":         tk,
                        "rec_type":       rec_type,
                        "conviction":     conviction,
                        "_verdicts":      [verdict] if verdict else [],
                        "times_surfaced": 1,
                        "acted":          tk in acted_tickers,
                        # Price % over the window (close_end / close_start − 1);
                        # None for a closed position or when either close is missing.
                        "end_week_pct":   _end_week_pct(tk),
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

    # ── Protective signals (WATCH/TRIM/EXIT) surfaced this week ──────────────
    # Symmetric counterpart to recs_surfaced above — without this, the debrief
    # only ever narrates entry-signal responsiveness, never exit-signal
    # responsiveness (did the investor act when told to TRIM/EXIT, or hold
    # through it?). Sourced from exit_signals, which the premarket cron has
    # captured daily since 2026-07-21. Tickers closed this week are excluded
    # here — closed_positions above already narrates them as "position closed"
    # and a second bullet from this block would conflict with that wording.
    if exit_signals_df is not None and not (hasattr(exit_signals_df, "empty") and exit_signals_df.empty):
        _week_sig = exit_signals_df[
            (exit_signals_df["signal_date"].astype(str).str[:10] >= str(week_start)) &
            (exit_signals_df["signal_date"].astype(str).str[:10] <= str(week_ending)) &
            (exit_signals_df["signal_type"].isin(["WATCH", "TRIM", "EXIT"]))
        ].copy()
        if not _week_sig.empty:
            week_sells: set[str] = set()
            if trades_df is not None and not (hasattr(trades_df, "empty") and trades_df.empty):
                _ta_dates4 = _traded_at_et_dates(trades_df)
                _week_sells_df = trades_df[
                    (_ta_dates4 >= week_start) &
                    (_ta_dates4 <= week_ending) &
                    (trades_df["action"].str.upper() == "SELL")
                ]
                week_sells = set(_week_sells_df["ticker"].astype(str).str.upper())

            _tier_rank = {"WATCH": 1, "TRIM": 2, "EXIT": 3}
            _prot_by_tk: dict[str, dict] = {}
            for _, sig in _week_sig.iterrows():
                tk = str(sig.get("ticker", "")).upper()
                if not tk or tk in closed_tickers:
                    continue
                tier = str(sig.get("signal_type") or "")
                if tk not in _prot_by_tk:
                    _prot_by_tk[tk] = {
                        "ticker":           tk,
                        "tier":             tier,
                        "times_surfaced":   1,
                        "sold":             tk in week_sells,
                        "pnl_pct":          sig.get("pnl_pct"),
                        "dd_from_peak_pct": sig.get("dd_from_peak_pct"),
                    }
                else:
                    _prot_by_tk[tk]["times_surfaced"] += 1
                    # Keep the most severe tier seen this week (EXIT > TRIM > WATCH).
                    if _tier_rank.get(tier, 0) > _tier_rank.get(_prot_by_tk[tk]["tier"], 0):
                        _prot_by_tk[tk]["tier"]             = tier
                        _prot_by_tk[tk]["pnl_pct"]           = sig.get("pnl_pct")
                        _prot_by_tk[tk]["dd_from_peak_pct"]  = sig.get("dd_from_peak_pct")
            package["protective_signals"] = sorted(
                _prot_by_tk.values(),
                key=lambda x: _tier_rank.get(x["tier"], 0),
                reverse=True,
            )

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


def _usd_plain(v) -> str:
    """Plain-dollar text for the LLM prompt (sign before the $); 'unknown' for None."""
    x = _fnum(v)
    if x is None:
        return "unknown"
    if round(abs(x)) == 0:
        return "$0"
    return f"{'-' if x < 0 else '+'}${abs(x):,.0f}"


_SHAPE_TEXT = {
    "closed":     "position sold this week — the move up to the sale",
    "opened":     "position bought this week — the move from its buy price",
    "round_trip": "bought and sold within the week",
    "traded":     "held at both ends, bought and sold in between",
    "trimmed":    "held at both ends, partly sold",
    "added":      "held at both ends, added to",
    "held":       "held all week",
}


def _leg_line(c: dict) -> str:
    pct = c.get("price_pct")
    pct_str = f"; price {_pct(pct)}" if pct is not None else ""
    return f"  {c['ticker']}: {_usd_plain(c['pnl'])} ({_SHAPE_TEXT.get(c.get('shape'), 'traded')}{pct_str})"


def _format_prompt(package: dict) -> str:
    lines = [f"Week: {package['week_start']} to {package['week_ending']}", ""]

    d0, d1 = package.get("d0"), package.get("d1")
    if package.get("performance_pct") is not None:
        r0, r1 = package.get("return_d0") or d0, package.get("return_d1") or d1
        lines.append(f"Account change measured {r0} close to {r1} close (net equity, flow-adjusted;"
                     " the same figure as the Account page's Reports tab, Last week):")
        lines.append(f"  Account change: {_pct(package.get('performance_pct'))}"
                     + (f" ({_usd_plain(package['equity_change'])})"
                        if package.get("dates_agree") and package.get("equity_change") is not None else ""))
        if package.get("spy_pct") is not None:
            lines.append(f"  SPY over the same dates: {_pct(package.get('spy_pct'))}")
            lines.append(f"  Account vs SPY: {package['alpha_pct']:+.1f} percentage points")
        elif package.get("spy_pending"):
            lines.append("  SPY over the same dates: not posted yet")
        else:
            lines.append("  SPY over the same dates: unknown")
    else:
        reason = package.get("result_reason") or "the account result couldn't be measured for this period."
        lines.append(f"Account result: unknown — {reason}")
        lines.append("  Do not estimate it; say only that it is unknown and why.")
    ml = _fnum(package.get("max_leverage"))
    if package.get("performance_pct") is not None and ml is not None and ml > 1:
        lines.append(f"  Leverage: up to {ml:.1f}x at the week's endpoints")

    wf = package.get("waterfall")
    if wf:
        lines.append("")
        lines.append("Where the account change came from:" if package.get("performance_pct") is not None
                     else "Position P&L (account result unknown):")
        for label, key in (
            ("Price moves on positions held through", "held_through"),
            ("Last-day moves on positions sold", "sold_leg"),
            ("First-day moves on positions bought", "bought_leg"),
            ("Same-day round trips", "round_trip"),
            ("Deposits / withdrawals", "flows_net"),
            ("Dividends", "dividends"),
            ("Margin interest posted", "interest"),
            ("Fees", "fees"),
            ("Unexplained remainder", "unexplained"),
        ):
            lines.append(f"  {label}: {_usd_plain(wf.get(key))}")
    if package.get("interest_gap_months"):
        lines.append("  Margin interest not recorded for: "
                     + ", ".join(package["interest_gap_months"]) + " (the interest line may be understated)")
    if package.get("d1_short_of_period_end") and d1:
        lines.append(f"  Note: the latest account record in the week is {d1}, before the week's end.")
    if package.get("unmeasured"):
        lines.append("  Not measured (P&L not included above): " + ", ".join(package["unmeasured"]))

    lines.append("")
    if package.get("contributors"):
        lines.append("Top contributors (by $ gain):")
        for c in package["contributors"]:
            lines.append(_leg_line(c))
    if package.get("detractors"):
        lines.append("Top detractors (by $ loss):")
        for d in package["detractors"]:
            lines.append(_leg_line(d))

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
            conviction  = r.get("conviction", "")
            conv_str    = f" ({conviction})" if conviction else ""
            lines.append(f"  {r['ticker']}: {label}{conv_str}{verdict_str}{times_str} — {acted_str}{end_str}")
    else:
        lines.append("No actionable signals were surfaced this week.")

    if package.get("protective_signals"):
        lines.append("")
        lines.append(
            f"Protective signals fired this week ({len(package['protective_signals'])} name(s),"
            " WATCH/TRIM/EXIT from the risk/exit engine — distinct from the entry signals above):"
        )
        for p in package["protective_signals"]:
            action_str = "sold" if p["sold"] else "held — NOT acted on"
            dd_str  = f"; {_pct(p.get('dd_from_peak_pct'))} from peak" if p.get("dd_from_peak_pct") is not None else ""
            pnl_str = f"; P&L {_pct(p.get('pnl_pct'))}" if p.get("pnl_pct") is not None else ""
            times     = p.get("times_surfaced", 1)
            times_str = f" (surfaced {times}× this week)" if times > 1 else ""
            lines.append(f"  {p['ticker']}: {p['tier']} signal{times_str} — {action_str}{dd_str}{pnl_str}")
    else:
        lines.append("")
        lines.append("No protective (WATCH/TRIM/EXIT) signals fired this week.")

    if package.get("closed_positions"):
        lines.append("")
        lines.append(f"Positions fully closed (sold) this week: {', '.join(package['closed_positions'])}")
        lines.append(
            "  These tickers were sold during the week. In the Decisions section say only"
            " that the position was closed; they may still appear among the contributors/"
            "detractors above with their real move up to the sale."
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
                     f"{h.get('n_acted', 0)} acted on ({h.get('action_rate', 0):.0f}% rate)")
        lines.append(f"  Low-momentum signals:  {lo.get('n', 0)} surfaced, "
                     f"{lo.get('n_acted', 0)} acted on ({lo.get('action_rate', 0):.0f}% rate)")
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
                     f"{sb.get('n_acted', 0)} acted on ({sb.get('action_rate', 0):.0f}% rate)")
        lines.append(f"  Buy signals:        {b.get('n', 0)} surfaced, "
                     f"{b.get('n_acted', 0)} acted on ({b.get('action_rate', 0):.0f}% rate)")
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
    if not api_key or not package.get("narratable"):
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
            "metrics_version":  package["metrics_version"],
            "generated_at":     datetime.now(timezone.utc).isoformat(),
            "performance_pct":  package.get("performance_pct"),
            "spy_pct":          package.get("spy_pct"),
            "alpha_pct":        package.get("alpha_pct"),
            **sections,
            "email_sent":       False,
        }
    except Exception:
        return None
