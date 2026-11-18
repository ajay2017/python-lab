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

from stock_analyzer import debrief_entry_calls as _dec
from stock_analyzer import entry_call_action as _eca
from stock_analyzer import llm
from stock_analyzer.constants import (
    BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP,
    ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS,
    LLM_REQUEST_TIMEOUT_SEC,
)


# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a disciplined portfolio advisor helping an individual investor reflect on the past week. Write a structured weekly debrief in four sections. Be factual, concise, and neutral — observation not judgement. Write in second person ("Your portfolio..."). Plain language, no jargon. Target 400–500 words total.

Section 1 — WHAT HAPPENED (2–3 sentences):
State the account result: your account (net equity) moved X% ($Y) from the <d0> close to the <d1> close; SPY moved Z% over the same dates. If leverage is given, say once that at about L× leverage the account moves roughly L times the holdings' own move while SPY is unlevered — never call the gap skill. If the account result is unknown, say so in one sentence with the reason given; never estimate. Name contributors/detractors with their $: a position sold this week shows the move up to the sale, one bought this week the move from its buy price. If names are listed as not measured, say their P&L isn't included. If margin interest is listed as not recorded for a month, say the interest line may be understated. Do not speculate about the unexplained remainder. This is the same figure as 📄 Reports → Performance Review → Last week. Facts only.

Section 2 — DECISIONS YOU MADE (bullet list):
The app writes the entry-signal bullets itself (new positions, adds to winners and Enter Now calls: acted on, not acted on, still open or not judged) and places them at the top of this section. Do NOT write a bullet for an entry signal, and never restate or contradict whether one was acted on. Write bullets only for the protective WATCH/TRIM/EXIT signals and the closed positions below.
For each protective signal surfaced this week, did the investor act or not, and what happened to the name by week-end? Be specific.
Example (protective): "• TSLA: TRIM signal (surfaced 2×). You held — not acted on. When it fired on 2026-07-22 the position was 9.2% below its peak, P&L −4.1%."
A protective signal's drawdown and P&L are a snapshot from the day it fired — always say that day, and never present them as the week-end figure or as a distance "from peak" with a plus sign.
If a ticker appears under "Positions fully closed this week", write ONLY: "• TICKER: Position closed this week." — do NOT describe it as a price loss or wipeout; closing a position on a signal is correct behaviour.
If no protective signals fired, write "• No protective signals fired this week." — but only when the data says protective signals were checked and none fired; if the data says a kind of signal couldn't be loaded, never write that none were surfaced for it.

Section 3 — PATTERNS THIS WEEK (1–3 bullets, or "No clear pattern this week." if none):
What behavioural pattern, if any, showed up? Combine this week's signals with any "Behavioral pattern" and "Decision quality" blocks in the data package:
- If a momentum tendency is provided: name whether the investor chases momentum (higher action rate on already-hot signals) or fades it (contrarian lean toward overlooked names), and quote the pp gap.
- If a conviction-tier pattern is provided: name whether action rate is higher on Strong Buy vs plain Buy signals, or the reverse, and what that suggests.
- If a Decision Quality grade is provided: include a single concise line (e.g., "Month-to-date decision quality: B (72/100) across 8 trades").
Other named patterns (use only when applicable to this week):
- Signal follower: the entry-signal follow-through line shows ≥80% (it already leaves out still-open and not-judged signals; never count them yourself)
- Concentration creep: added to a name already above single-name ceiling
- Calm week: no signals fired or all signals acted on cleanly, and no entry signal is still open or not judged (only when every signal kind was loaded; never when the data says a kind couldn't be loaded)
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


# The protective-signal read (exit_signals) can fail while the debrief is still
# worth writing.  A failed read is not a quiet week: both the model prompt and
# the saved/emailed/in-app text must say so.  Wording only -- no policy value.
PROTECTIVE_UNAVAILABLE_PROMPT_LINE = (
    "Protective signals (WATCH/TRIM/EXIT) couldn't be loaded for this week; "
    "do not say there were none.")
PROTECTIVE_UNAVAILABLE_NOTE = (
    "Protective signals couldn't be loaded for this week, so this section is "
    "missing (not a quiet week).")


def protective_signals_status(exit_signals_df, unavailable: bool = False) -> str:
    """How the protective-signal input stands: "unavailable" (the caller's read
    failed -- the debrief must disclose it), "ok" (a frame was supplied, possibly
    empty = a genuinely quiet week), "not_supplied" (no frame, no failure flag --
    the legacy optional path)."""
    if unavailable:
        return "unavailable"
    return "ok" if exit_signals_df is not None else "not_supplied"


# Statuses that mean "we have no verified read of the protective signals": a
# failed read, or no frame supplied at all (the legacy path).  Neither may ever
# be narrated as "no signals fired".
_PROTECTIVE_UNVERIFIED = ("unavailable", "not_supplied")


def protective_unavailable_note(package) -> "str | None":
    """The visible line for a package whose protective-signal read failed (or was
    never supplied), else None.  Appended to the saved Decisions text so the
    stored debrief, its email and the in-app view all carry it (the narrative is
    where protective signals are discussed)."""
    if isinstance(package, dict) and package.get("protective_signals_status") in _PROTECTIVE_UNVERIFIED:
        return PROTECTIVE_UNAVAILABLE_NOTE
    return None


# The recommendations read can fail the same way.  An empty frame is a genuinely
# quiet week; a failed read (None) is not, and must never print "No actionable
# signals were surfaced this week."  Wording only -- no policy value.
RECS_UNAVAILABLE_PROMPT_LINE = (
    "Entry signals (new positions / adds to winners / Enter Now) couldn't be "
    "loaded for this week; do not say there were none.")
RECS_UNAVAILABLE_NOTE = (
    "Entry signals (new positions / adds to winners / Enter Now) couldn't be "
    "loaded for this week, so this section is missing (not a quiet week).")
# The read worked but the app's own entry-signal check failed: say so, never blame
# the database.
RECS_ENTRY_ERROR_NOTE = (
    "Entry signals (new positions / adds to winners / Enter Now) couldn't be "
    "loaded for this week (the app's entry-signal check failed, not the "
    "database), so this section is missing (not a quiet week).")


def recs_status(recs_df, unavailable: bool = False) -> str:
    """How the entry-signal (recommendations) input stands: "unavailable" (the
    caller's read failed, or no frame at all -- a None frame never reads as zero
    signals) or "ok" (a frame was supplied, possibly empty = a genuinely quiet
    week)."""
    if unavailable or recs_df is None:
        return "unavailable"
    return "ok"


def recs_unavailable_note(package) -> "str | None":
    """The visible line for a package whose recommendations read failed, else
    None.  Appended to the saved Decisions text alongside the protective note."""
    if isinstance(package, dict) and package.get("recs_status") == "unavailable":
        return RECS_ENTRY_ERROR_NOTE if package.get("entry_error") else RECS_UNAVAILABLE_NOTE
    return None


def unknown_result_reason(recon, account_return, excluded=None) -> str:
    """Why the account result is unknown, branching on the stage that failed:
    the deposit/withdrawal read (account_return offline), an unmeasurable
    reconciliation (pre_coverage reason), an account return the engine could not
    compute while the reconciliation could, else a generic line.

    `excluded` (`account_row_trust.exclude_stale_rows`' list, N1 C6b) lets a
    pre_coverage reason say that a record in the window was LEFT OUT rather than
    leave it reading as if no record existed."""
    from stock_analyzer import period_attribution as pa
    from stock_analyzer import account_row_trust
    ar_status = account_return.get("status") if isinstance(account_return, dict) else None
    recon_status = recon.get("status") if isinstance(recon, dict) else None
    if ar_status == "offline":
        return "the deposit/withdrawal history couldn't be read, so the account's return can't be measured."
    if recon_status == "pre_coverage":
        note = account_row_trust.pre_coverage_left_out_note(recon, excluded, see_note=False)
        return pa.pre_coverage_reason_text(recon) + (" " + note if note else "")
    if ar_status == "pre_coverage":
        text = ("the account's return couldn't be computed over this week's dates "
                "(no usable account records at both ends).")
        note = account_row_trust.period_left_out_note(recon, excluded, see_note=False)
        return text + (" " + note if note else "")
    return "the account result couldn't be measured for this period."


def _as_list(v) -> list:
    """A list from a package list field; an absent (None) field reads as no rows."""
    return [] if v is None else list(v)


def _names(rows) -> "list[str]":
    return sorted({str(r[0]) for r in (rows or []) if isinstance(r, (tuple, list)) and r})


def _times_str(p: dict) -> str:
    times = p.get("times_surfaced", 1)
    return f" (surfaced {times}× this week)" if times and times > 1 else ""


def _signal_snapshot_str(p: dict) -> str:
    """A protective signal's drawdown and P&L, stated as what they are: a
    snapshot taken WHEN the signal fired, not the week-end.  dd_from_peak_pct
    is stored as a positive size ("Down 14.1% from its peak"); printing it with
    the signed _pct gave "+14.1% from peak", and the model then mislabelled it
    two different ways on two runs (2026-10-20, HOOD)."""
    dd, pnl = _fnum(p.get("dd_from_peak_pct")), _fnum(p.get("pnl_pct"))
    parts = []
    if dd is not None:
        parts.append(f"{abs(dd):.1f}% below its peak")
    if pnl is not None:
        parts.append(f"P&L {_pct(pnl)} vs its cost")
    if not parts:
        return ""
    when = p.get("signal_date")
    lead = f"when it fired on {when}" if when else "when it fired"
    return f"; {lead}: " + ", ".join(parts) + " (a snapshot then, not the week-end)"


def generated_et_date(ts) -> str:
    """The ET calendar date of a stored `generated_at` (written in UTC), or "—".
    Slicing the ISO string gave the UTC day, so an evening ET run read as tomorrow."""
    import pandas as pd
    try:
        t = pd.to_datetime(ts, utc=True)
    except (TypeError, ValueError):
        return "—"
    if t is None or pd.isna(t):
        return "—"
    return str(t.tz_convert("America/New_York").date())


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
    exit_signals_unavailable: bool = False,
    recs_unavailable: bool = False,
) -> dict:
    """
    Assemble the structured data package for the LLM.

    core:         `period_inputs.compute_period_core(...)` -- {recon, leverage,
                  account_return, account_rows_excluded} for the Mon-Sun week ending `week_ending`;
                  the SAME engine and figures as 📄 Reports -> Performance Review -> Last
                  week.  None = the engine inputs could not be read (offline).
    recs_df:      DataFrame of recommendations surfaced during the week.
    trades_df:    DataFrame of all trades (to detect acted-on signals).
    broken_theses: list of tickers with BROKEN thesis status (from F-1).
    exit_signals_df: DataFrame from db.load_exit_signals() — WATCH/TRIM/EXIT
                  protective signals, the symmetric counterpart to recs_df's
                  entry signals. Optional; omitted gracefully when absent.
    exit_signals_unavailable: True when the caller's protective-signal read
                  FAILED (as opposed to exit_signals_df=None = not supplied).
                  The frame is then ignored, the package records
                  protective_signals_status="unavailable", and the prompt and
                  the generated text say the section is missing, never "none".
                  A legacy call with no frame and no flag ("not_supplied") is
                  disclosed the same way -- it can never read as "none fired".
    recs_unavailable: True when the caller's recommendations read FAILED.
                  recs_df is then ignored (the recs-derived lists stay empty),
                  the package records recs_status="unavailable", and the prompt
                  and the generated text say the entry signals are missing.
                  recs_df=None is treated as unavailable too.

    Returns a dict consumed by generate_debrief().  `narratable` is False when
    the engine is offline -- generate_debrief() then writes nothing.
    """
    import pandas as pd
    from stock_analyzer import period_attribution as pa
    from stock_analyzer import account_row_trust

    week_start = week_ending - timedelta(days=6)
    core = core if isinstance(core, dict) else None
    recon = (core or {}).get("recon")
    recon = recon if isinstance(recon, dict) else None
    ar = (core or {}).get("account_return")
    ar = ar if isinstance(ar, dict) else None
    lev = (core or {}).get("leverage")
    lev = lev if isinstance(lev, dict) else None
    excluded = (core or {}).get("account_rows_excluded")
    if excluded is None:
        excluded = []

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
        "waterfall_rows":   [],
        "interest_gap_months": [],
        "unmeasured":       [],
        "excluded_account_line": account_row_trust.excluded_line(
            excluded, week_start, week_ending),
        "contributors":     [],
        "detractors":       [],
        "closed_positions":       [],
        "recs_surfaced":          [],
        "signals_ignored":        [],
        "protective_signals":     [],
        "protective_signals_status": protective_signals_status(
            exit_signals_df, exit_signals_unavailable),
        "recs_status":            recs_status(recs_df, recs_unavailable),
        # the entry-call block (debrief_entry_calls.weekly_entry_calls); None / []
        # until it has run, so a package without it reads "no entry info"
        "entry_carried_over":     [],
        "entry_follow":           None,
        "entry_judge_state":      None,
        "entry_n_undated":        0,
        "entry_window_days":      None,
        "acted_rule_version":     _eca.ACTED_RULE_VERSION,
        # exception type when the entry-call module itself failed (not the read)
        "entry_error":            None,
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
            package["result_reason"] = unknown_result_reason(recon, ar, excluded)

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
        # The Reports page's own rows (one function builds both), over the recon's window.
        package["waterfall_rows"] = pa.waterfall_rows(recon)
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

    # Entry calls (new_pick / add_winner / enter_now), judged by the shared rule
    # (docs/plans/acted-rule-c3-spec.md): a call is acted on only by a BUY on the
    # call day or within the decision window, judged as of the week's end; a name
    # whose window ran past last Sunday is carried over. buy_candidate is an
    # awareness-only feed and is never listed. Any failure of the module reads as
    # "entry signals unavailable", never as a quiet week.
    if recs_unavailable:
        # a frame flagged unavailable is never read: no recs-derived list, and no
        # fall-back to it in the behavioural block below
        recs_df = None
    try:
        _entry = _dec.weekly_entry_calls(
            recs_df, trades_df, week_start=week_start, week_ending=week_ending)
    except Exception as _e:
        _entry = None
        if not recs_unavailable:
            # only blame the module when the database read worked: on a double
            # fault the plain "couldn't be loaded" note stays true
            package["entry_error"] = type(_e).__name__
    if _entry is None:
        package["recs_status"] = "unavailable"
    else:
        package["recs_status"] = _entry["recs_status"]
    if _entry is not None and _entry["recs_status"] == _dec.RECS_OK:
        _rows = []
        for _r in _entry["this_week"]:
            _row = dict(_r)
            _row["end_week_pct"] = _end_week_pct(_row["ticker"])
            _row["acted"] = _row["status"] == _eca.STATUS_ACTED
            _rows.append(_row)
        package["recs_surfaced"] = _rows
        package["signals_ignored"] = [
            r for r in _rows if r["status"] == _eca.STATUS_NOT_ACTED]
        package["entry_carried_over"] = list(_entry["carried_over"])
        package["entry_follow"] = dict(_entry["follow"])
        package["entry_judge_state"] = _entry["judge_state"]
        package["entry_n_undated"] = _entry["n_undated"]
        package["entry_window_days"] = _entry["window_trading_days"]
        package["acted_rule_version"] = _entry["acted_rule_version"]

    # ── Protective signals (WATCH/TRIM/EXIT) surfaced this week ──────────────
    # Symmetric counterpart to recs_surfaced above — without this, the debrief
    # only ever narrates entry-signal responsiveness, never exit-signal
    # responsiveness (did the investor act when told to TRIM/EXIT, or hold
    # through it?). Sourced from exit_signals, which the premarket cron has
    # captured daily since 2026-07-21. Tickers closed this week are excluded
    # here — closed_positions above already narrates them as "position closed"
    # and a second bullet from this block would conflict with that wording.
    if (not exit_signals_unavailable and exit_signals_df is not None
            and not (hasattr(exit_signals_df, "empty") and exit_signals_df.empty)):
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
                        # the day those two figures were measured -- a snapshot
                        # at the signal, not the week-end (HOOD 2026-10-15)
                        "signal_date":      str(sig.get("signal_date") or "")[:10] or None,
                    }
                else:
                    _prot_by_tk[tk]["times_surfaced"] += 1
                    # Keep the most severe tier seen this week (EXIT > TRIM > WATCH).
                    if _tier_rank.get(tier, 0) > _tier_rank.get(_prot_by_tk[tk]["tier"], 0):
                        _prot_by_tk[tk]["tier"]             = tier
                        _prot_by_tk[tk]["pnl_pct"]           = sig.get("pnl_pct")
                        _prot_by_tk[tk]["dd_from_peak_pct"]  = sig.get("dd_from_peak_pct")
                        _prot_by_tk[tk]["signal_date"]       = str(sig.get("signal_date") or "")[:10] or None
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
        # An empty journal is skipped here (as before): it would read every call
        # not acted on, and a pattern built on that is worse than none.
        if _hist is not None and not getattr(_hist, "empty", True) and \
                trades_df is not None and not getattr(trades_df, "empty", True):
            # the shared entry-call rule, judged as of the week's end; still-open,
            # not-judged and credit-deduped calls are left out of both arms
            _matched = _dec.behavioural_rows(_hist, trades_df, cutoff=week_ending)
            if _matched is not None:
                _beh: dict = {}
                _mp = _mrp(_matched, min_n=int(_BMIN),
                           meaningful_delta_pp=float(BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP))
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


_ENTRY_TYPE_LABELS = (("new_pick", "New position"),
                      ("add_winner", "Add to winner"),
                      ("enter_now", "Enter Now"))


def _trading_days_phrase(n) -> str:
    n = ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS if n is None else int(n)
    return "1 trading day" if n == 1 else f"{n} trading days"


def _entry_status_text(r: dict) -> str:
    status = r.get("status")
    if status == _eca.STATUS_ACTED:
        bought = r.get("matched_trade_date")
        return f"acted on (bought {bought})" if bought else "acted on"
    if status == _eca.STATUS_NOT_ACTED:
        return "not acted on"
    if status == _eca.STATUS_STILL_OPEN:
        wend = r.get("window_end")
        return f"still open (decision window ends {wend})" if wend else "still open"
    return f"not judged ({_dec.unknown_reason_text(r.get('reason'))})"


def _entry_prompt_line(r: dict, *, carried: bool) -> str:
    have = {str(t) for t in _as_list(r.get("rec_types"))}
    label = " + ".join(lbl for key, lbl in _ENTRY_TYPE_LABELS if key in have) or "Entry"
    conviction = r.get("conviction") or ""
    verdict = r.get("verdict") or ""
    conv_str = f" ({conviction})" if conviction else ""
    verdict_str = f" [{verdict}]" if verdict else ""
    times = r.get("times_surfaced") or 1
    times_str = (f" (surfaced {times}×{'' if carried else ' this week'})"
                 if times > 1 else "")
    end_str = ""
    if not carried and r.get("end_week_pct") is not None:
        end_str = f"; ended week {_pct(r['end_week_pct'])}"
    return (f"  {r['ticker']}: {label}{conv_str}{verdict_str}{times_str} — "
            f"{_entry_status_text(r)}{end_str}")


def _entry_prompt_lines(package: dict) -> "list[str]":
    """The entry-signal part of the model prompt. The app writes the bullets
    itself (debrief_entry_calls.entry_signal_lines); the model only reads these
    lines for Patterns and One thing to watch. A package with no entry-call info
    (recs unavailable, or handwritten) says so, never "none"."""
    if package.get("recs_status") != "ok" or package.get("entry_follow") is None:
        return [RECS_UNAVAILABLE_PROMPT_LINE]
    this_week = _as_list(package.get("recs_surfaced"))
    carried = _as_list(package.get("entry_carried_over"))
    follow = package["entry_follow"]
    n_win = package.get("entry_window_days")
    out: list[str] = []
    if this_week:
        out.append(
            "Entry signals this week (new positions, adds to winners, Enter Now; "
            f"{len(this_week)} name(s)). The app has already written one bullet per "
            "name at the top of \"Decisions you made\". Do not write entry-signal "
            "bullets and do not restate whether they were acted on; use these for "
            "Patterns and One thing to watch only:")
        out.extend(_entry_prompt_line(r, carried=False) for r in this_week)
    elif package.get("entry_n_undated"):
        out.append("Some entry-signal rows had no readable date and are not shown "
                   "(the app has noted that).")
    else:
        out.append("No entry signals were surfaced this week (the app has written "
                   "that line).")
    if this_week or carried:
        if package.get("entry_judge_state") != "ok":
            out.append(
                "Whether these were acted on couldn't be checked (the trade journal "
                "couldn't be read); do not say they were or weren't, and do not "
                "compute a follow-through share.")
        elif follow.get("share_pct") is not None:
            judged = follow["n_acted"] + follow["n_not_acted"]
            out.append(
                "Entry-signal follow-through this week (by name, including "
                f"{follow.get('n_carried', 0)} carried over; acted = a buy on the "
                f"call day or within {_trading_days_phrase(n_win)}): acted on "
                f"{follow['n_acted']} of {judged} judged ({follow['share_pct']}%); "
                f"{follow.get('n_still_open', 0)} still open and "
                f"{follow.get('n_unknown', 0)} not judged are not counted.")
        else:
            out.append(
                "No entry signal could be judged as acted on or not yet "
                f"({follow.get('n_still_open', 0)} still open, "
                f"{follow.get('n_unknown', 0)} not judged); do not compute a "
                "follow-through share.")
    if carried:
        out.append("Carried over from last week (already written by the app):")
        out.extend(_entry_prompt_line(r, carried=True) for r in carried)
    return out


def _format_prompt(package: dict) -> str:
    lines = [f"Week: {package['week_start']} to {package['week_ending']}", ""]

    d0, d1 = package.get("d0"), package.get("d1")
    if package.get("performance_pct") is not None:
        r0, r1 = package.get("return_d0") or d0, package.get("return_d1") or d1
        lines.append(f"Account change measured {r0} close to {r1} close (net equity, flow-adjusted;"
                     " the same figure as 📄 Reports → Performance Review → Last week):")
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
    if package.get("excluded_account_line"):
        # N1 C6b: stale account records were masked out of the figures above; the
        # narrative must not state or imply a figure for those days.
        lines.append("  Note: " + package["excluded_account_line"]
                     + " The account change above already leaves these days out; do not "
                       "state an account value for those dates.")
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
    lines.extend(_entry_prompt_lines(package))

    if package.get("protective_signals_status") in _PROTECTIVE_UNVERIFIED:
        lines.append("")
        lines.append(PROTECTIVE_UNAVAILABLE_PROMPT_LINE)
    elif package.get("protective_signals"):
        lines.append("")
        lines.append(
            f"Protective signals fired this week ({len(package['protective_signals'])} name(s),"
            " WATCH/TRIM/EXIT from the risk/exit engine — distinct from the entry signals above):"
        )
        for p in package["protective_signals"]:
            action_str = "sold" if p["sold"] else "held — NOT acted on"
            lines.append(f"  {p['ticker']}: {p['tier']} signal{_times_str(p)} — {action_str}"
                         f"{_signal_snapshot_str(p)}")
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
    _beh_basis = ("all-time entry-signal history; acted = a buy within "
                  f"{_trading_days_phrase(package.get('entry_window_days'))} of the "
                  "call; still-open calls not counted")
    if beh.get("momentum"):
        mp = beh["momentum"]
        h  = mp.get("high", {})
        lo = mp.get("low",  {})
        lines.append("")
        lines.append(f"Behavioral pattern — momentum tendency ({_beh_basis}):")
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
        lines.append(f"Behavioral pattern — conviction tier ({_beh_basis}):")
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
            lines.append(f"  Win rate: {dq['win_rate']:.0f}%")  # already in percent
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

def _entry_bullet_lines(package: dict) -> "list[str]":
    """The app-written entry-signal bullets for a package, [] when it carries no
    entry-call info (recs unavailable: RECS_UNAVAILABLE_NOTE discloses it)."""
    if package.get("recs_status") != "ok" or package.get("entry_follow") is None:
        return []
    return _dec.entry_signal_lines({
        "recs_status":          "ok",
        "judge_state":          package.get("entry_judge_state"),
        "this_week":            _as_list(package.get("recs_surfaced")),
        "carried_over":         _as_list(package.get("entry_carried_over")),
        "n_undated":            package.get("entry_n_undated") or 0,
        "follow":               package["entry_follow"],
        "window_trading_days":  package.get("entry_window_days"),
    })


def generate_debrief(
    package: dict,
    api_key: str,
    model: str | None = None,
    # 700 cut the 2026-10-18 debrief off mid-sentence: the engine package (exact
    # legs, closed / not-measured names, 13 decision bullets) runs longer than
    # the old one. 1500 leaves room; a reply that still hits it is disclosed below.
    max_tokens: int = 1500,
) -> dict | None:
    """
    Call the LLM to generate a weekly portfolio debrief.

    Returns a save-ready dict (for db.save_weekly_debrief) or None on any failure.
    """
    if not api_key or not package.get("narratable"):
        return None
    try:
        prompt   = _format_prompt(package)
        r = llm.complete(
            llm.ROLE_DEEP,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        if not r.ok:
            return None
        text     = r.text
        sections = _parse_response(text)
        _cut_last = None
        if r.truncated:
            # never let a cut-off sentence read as the model's whole thought
            _cut_last = next((k for k in reversed(list(sections)) if sections.get(k)), None)
        # UD-5: the app writes the entry-signal bullets, so a model bullet that opens
        # with an entry name (other than a protective / closed one) is dropped; only
        # the Decisions section, never Patterns.
        _entry_names = [r_["ticker"] for r_ in _as_list(package.get("recs_surfaced"))
                        + _as_list(package.get("entry_carried_over"))]
        _keep_names = ([p_["ticker"] for p_ in _as_list(package.get("protective_signals"))]
                       + _as_list(package.get("closed_positions")))
        sections["section_decisions"] = _dec.drop_ai_entry_bullets(
            sections.get("section_decisions"), _entry_names, _keep_names).strip()
        if _cut_last is not None:
            sections[_cut_last] = (
                sections[_cut_last].rstrip()
                + " … (cut off — the debrief hit its length limit)").strip()
        # The deterministic entry bullets lead the Decisions section, then the model's
        # text, then the disclosure notes (a stored debrief that silently lacks a
        # signal section reads as a quiet week).
        _parts = []
        _entry_lines = _entry_bullet_lines(package)
        if _entry_lines:
            _parts.append("\n".join(_entry_lines))
        if (sections.get("section_decisions") or "").strip():
            _parts.append(sections["section_decisions"].strip())
        for _note in (protective_unavailable_note(package),
                      recs_unavailable_note(package)):
            if _note:
                _parts.append(_note)
        sections["section_decisions"] = "\n\n".join(_parts)
        return {
            "week_ending":      package["week_ending"],
            "metrics_version":  package["metrics_version"],
            "generated_at":     datetime.now(timezone.utc).isoformat(),
            "performance_pct":  package.get("performance_pct"),
            "spy_pct":          package.get("spy_pct"),
            "alpha_pct":        package.get("alpha_pct"),
            **sections,
            "acted_rule_version": _eca.ACTED_RULE_VERSION,
            "email_sent":       False,
        }
    except Exception:
        return None
