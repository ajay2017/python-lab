"""Gate module: the "Stop basis" notice (SlidingStop C4; pure, no I/O, no clock).

A holding whose trade journal can't date it (the journal's share count disagrees with
the broker's, no history, an unreadable journal) gets the prior-close stop, which is
rebuilt from the latest close every day and so chases a falling price down. C3 saves
and re-applies a carried stop for those names; this module says so where the owner
acts, from the `build_held_stops` results alone (never Act Today: a data fault, not a
trade; owner answers U3 / U4 / U5, docs/plans/sliding-stop.md section 9):

  rows(held_stops)             one row per name `held_stop_carry.notice_tier` flags
                               (tier "error" = the carry is NOT holding the stop,
                               "warning" = it is)
  drop_rows(held_stops)        U3: the stop is FULL again but below the carried one
                               (a fixed journal, a recovered history fetch, or a young
                               listing reaching its warm-up)
  overall_tier(rows, drops)    "error" / "warning" / "info" / None
  home_markdown(rows, drops)   Home's dock Notices text (Streamlit markdown)
  email_lines(rows, drops)     the premarket email footer (plain text)
  lane_failure(rows)           the premarket lane's red clause (an uncarried name only)

No stop value is changed here. Policy values are read from `constants` at call time.
A wrong call here stays silent on a stop that slides down with the price, or says a
stop is held when it is not.
"""
from __future__ import annotations

import math

from stock_analyzer import constants as _C
from stock_analyzer import held_stop_carry as hsc
from stock_analyzer.young_holding_stop import md_dollars

# The expander that fixes a journal / holdings mismatch (a test pins the title against app.py).
FIX_POINTER = "📒 Trade Journal → ⚠️ Fix drift between trades and holdings"

_UNREADABLE_CLOSE = "This usually clears on the next refresh; if it doesn't, see 🩺 System Trust."
_FIXABLE_IN_JOURNAL = ("mismatch", "no_history")
_CARRY_BASES = ("partial", "prior_session")


# ── small helpers ────────────────────────────────────────────────────────────
def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _day(v) -> str | None:
    """An ISO `YYYY-MM-DD` from a date / datetime / ISO string; None when absent."""
    if v is None:
        return None
    s = str(v).strip()
    return s[:10] if s else None


def _mult() -> float:
    return max(float(_C.ATR_STOP_MULT), float(_C.HELD_TRAIL_ATR_MULT))


def _cause(row: dict) -> str:
    """Why the stop is on a prior-close basis, in the owner's words."""
    status = row.get("status")
    reason = str(row.get("reason") or "").strip()
    if status == "unreadable":
        return "the trade journal couldn't be read"
    if reason:
        return reason
    if status == "mismatch":
        return "the journal's share count doesn't match your holdings"
    return "there is no trade history for this position"


def _join_names(names: list[str]) -> str:
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _items(held_stops):
    if not isinstance(held_stops, dict):
        return []
    return sorted(((str(t).strip().upper(), r) for t, r in held_stops.items()
                   if isinstance(r, dict)), key=lambda kv: kv[0])


# ── the rows ─────────────────────────────────────────────────────────────────
def rows(held_stops) -> list[dict]:
    """One dict per held-stop result with a Stop-basis notice (`notice_tier` not None),
    sorted by ticker: {ticker, tier, status, reason, carried, carried_value, carried_on,
    carry_state}. `carried` = the carry is holding the stop (tier "warning");
    carried_value / carried_on come from the result's `carry` when it is applied or
    not binding (the saved stop and the date it was set), else None. Non-dict input = []."""
    out: list[dict] = []
    for t, r in _items(held_stops):
        tier = hsc.notice_tier(r)
        if tier is None:
            continue
        carry = r.get("carry")
        state = carry.get("state") if isinstance(carry, dict) else None
        held = state in (hsc.APPLIED, hsc.NOT_BINDING)
        out.append({
            "ticker": t,
            "tier": tier,
            "status": r.get("episode_status"),
            "reason": r.get("episode_reason"),
            "carried": tier == "warning",
            "carried_value": _f(carry.get("value")) if held else None,
            "carried_on": _day(carry.get("set_on")) if held else None,
            "carry_state": state,
        })
    return out


def drop_rows(held_stops) -> list[dict]:
    """U3: names whose stop is FULL again but lower than the carried one saved while the
    journal was broken: {ticker, stop, carried_value, carried_on}. The saved row must be
    from a partial / prior_session day (a full day's row is the journal's own stop and
    not "carried"), and the comparison is to the cent. Never changes a stop."""
    out: list[dict] = []
    for t, r in _items(held_stops):
        if r.get("basis") != "full":
            continue
        stop = _f(r.get("stop"))
        seen = r.get("carry_row_seen")
        if stop is None or not isinstance(seen, dict) or seen.get("source_basis") not in _CARRY_BASES:
            continue
        saved = _f(seen.get("stop"))
        if saved is None or not round(saved, 2) > round(stop, 2):
            continue
        out.append({"ticker": t, "stop": stop, "carried_value": saved,
                    "carried_on": _day(seen.get("stop_set_on")) or _day(seen.get("as_of"))})
    return out


def overall_tier(rows_, drops) -> str | None:
    """error if any error row, else warning if any row, else info if any drop, else None."""
    rows_ = rows_ if isinstance(rows_, list) else []
    if any(r.get("tier") == "error" for r in rows_):
        return "error"
    if rows_:
        return "warning"
    return "info" if drops else None


# ── wording ──────────────────────────────────────────────────────────────────
def _usd(x: float) -> str:
    return f"${x:.2f}"


def _from(on) -> str:
    return f" from {on}" if on else ""


def _drop_sentence(d: dict) -> str:
    return (f"its stop is back on your full trade history at {_usd(d['stop'])}, below the "
            f"carried {_usd(d['carried_value'])}{_from(d.get('carried_on'))}; the full-history "
            "figure is lower, so the carried stop no longer applies.")


def _row_sentence(r: dict) -> str:
    if r.get("tier") == "error":
        return (f"its stop is the last close minus {_mult():g} x ATR and isn't carried, so it "
                "moves down as the price falls and a slow decline won't trigger it.")
    v = r.get("carried_value")
    if v is None:
        return "its stop is held by a carried stop while your trades in this position don't change."
    if r.get("carry_state") == hsc.NOT_BINDING:
        return (f"a carried stop of {_usd(v)}{_from(r.get('carried_on'))} backs its stop while "
                "your trades in this position don't change.")
    return (f"its stop is held at {_usd(v)}, carried{_from(r.get('carried_on'))}, while your "
            "trades in this position don't change.")


def _header(tier: str, rows_: list[dict]) -> str:
    if tier == "error":
        names = _join_names([r["ticker"] for r in rows_ if r.get("tier") == "error"])
        return f"⚠️ **Stop basis: the stop for {names} isn't protected against a slow fall.**"
    if tier == "warning":
        tickers = [r["ticker"] for r in rows_]
        names = _join_names(tickers)
        verb = "is on a carried stop" if len(tickers) == 1 else "are on carried stops"
        return f"⚠️ **Stop basis: {names} {verb} until the trade journal is fixed.**"
    return "ℹ️ **Stop basis: a stop moved back onto your full trade history.**"


def home_markdown(rows_, drops) -> str | None:
    """Home's Notices text as Streamlit markdown (None when there is nothing to say).
    Every `$` is escaped once, over the whole text, with `md_dollars`."""
    rows_ = rows_ if isinstance(rows_, list) else []
    drops = drops if isinstance(drops, list) else []
    tier = overall_tier(rows_, drops)
    if tier is None:
        return None
    lines = [_header(tier, rows_), ""]
    for r in rows_:
        lines.append(f"- **{r['ticker']}**: {_cause(r)}; {_row_sentence(r)}")
    for d in drops:
        lines.append(f"- **{d['ticker']}**: {_drop_sentence(d)}")
    if rows_:
        lines.append("")
        if any(r.get("status") in _FIXABLE_IN_JOURNAL for r in rows_):
            lines.append(f"Fix the journal in {FIX_POINTER}; the full stop returns once the "
                         "shares agree.")
        else:
            lines.append(_UNREADABLE_CLOSE)
    return md_dollars("\n".join(lines).rstrip())


def email_lines(rows_, drops) -> list[str]:
    """The premarket email footer lines: plain text, no markdown, no backslashes. One fix
    line when any name needs a journal fix, one line per uncarried name, one per drop.
    The per-name degraded notes already in the footer are not repeated."""
    rows_ = rows_ if isinstance(rows_, list) else []
    drops = drops if isinstance(drops, list) else []
    out: list[str] = []
    fix = [r["ticker"] for r in rows_ if r.get("status") in _FIXABLE_IN_JOURNAL]
    if fix:
        verb = "their stops are" if len(fix) > 1 else "its stop is"
        out.append(f"Stop basis: fix the trade journal for {', '.join(fix)} ({FIX_POINTER}); "
                   f"until then {verb} not on the full history.")
    for r in rows_:
        if r.get("tier") == "error":
            out.append(f"{r['ticker']}: stop is not carried; a slow decline won't trigger it.")
    for d in drops:
        out.append(f"{d['ticker']}: {_drop_sentence(d)}")
    return out


def lane_failure(rows_) -> str | None:
    """The premarket lane's red clause: None unless a name is on an UNCARRIED prior-close
    stop because of a journal fault (error rows only; a carried name never goes red)."""
    errs = [r for r in (rows_ if isinstance(rows_, list) else []) if r.get("tier") == "error"]
    if not errs:
        return None
    detail = ", ".join(f"{r['ticker']}: {_cause(r)}" for r in errs)
    which = "it" if len(errs) == 1 else "them"
    return (f"{len(errs)} holding(s) on an uncarried prior-close stop because of a "
            f"trade-journal fault ({detail}); a slow decline on {which} won't trigger a stop")
