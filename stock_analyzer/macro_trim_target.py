"""Gate module: which affected holding the macro pre-event trim lands on, and how big it
is (brief-consolidation C3, row MacroTrimDefer, docs/plans/brief-consolidation-spec.md §1
row 3).

`daily_briefing._review_list` section 5 trims the weakest holding in an event's affected
sectors by `MACRO_AFFECTED_TRIM_REDUCTION_PP`. It used to leave out every name that had ANY
Act Today card (a critical-news "Hold for now", a premortem card, the sector Hard Cap
card's attribution ticker, a one-share sell), so the trim slid to a stronger name with no
line saying why. Now a name is passed over only when today's Act Today call really sells
at least as much of it as this trim would, and the card says so.

  size_trim(weight, price, portfolio_value) -> {"reduction_pp", "trim_dollars", "trim_shares"}
  pick(rows, act_cover, portfolio_value, exclude=()) -> {
      "target", "sizing", "conviction_unmeasured", "skipped_ahead", "all_covered", "beside"}
  all_covered_why(exposure_pct, names)      -> str
  skipped_sentence(ticker, reason)          -> str

`rows` = the affected holdings, each {"ticker", "score", "score_available", "weight",
"price"} (`score` None / NaN = withheld). `act_cover` = `act_sell_cover.sell_cover_summary(
act_today, [])`: the Act lane only, read AFTER the BriefSkipSizeBlind fold, so a folded
`min_trim` counts. A row is passed over ONLY when `cover_state(entry, need=its own
trim_shares) == "covered"` (a full exit, or an Act sale of at least as many whole shares;
equality covers). A `partial` or `unknown` Act sale keeps the row (the trim carries a
`beside_note`), as does a name whose only Act card sells nothing. `exclude` (the breach
alternate pass) is skipped silently and counts as covered when nothing else is left.

Ranking is unchanged: with any measured score among the rows still in play, measured
scores ascending then ticker; otherwise (conviction unmeasured, owner 2026-10-12) weight
descending then ticker. Pure (no streamlit, no I/O; inputs are never mutated).

THE FAIL DIRECTIONS: an `act_cover` that is not a dict (the Act lane could not be read)
covers nothing, so no name is passed over; a malformed entry reads unknown (kept). No
`$` or `**` in any produced text; the sentences carry no share count.
"""
from __future__ import annotations

import math

from stock_analyzer import act_sell_cover, trim_sizing
from stock_analyzer.constants import (
    MACRO_AFFECTED_TRIM_REDUCTION_PP,
    MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
)

REASON_EXIT = "exit"
REASON_COVERS = "covers"


def _norm(t) -> str:
    return str(t).strip().upper() if t is not None else ""


def _finite(x) -> bool:
    if isinstance(x, bool) or x is None:
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def size_trim(weight, price, portfolio_value) -> dict:
    """The pre-event trim for one holding: `MACRO_AFFECTED_TRIM_REDUCTION_PP` percentage
    points of the book, capped at the position's own weight, floored to whole shares, with
    the displayed dollars and pp reconciled to the rounded share count (so "N shares
    (~$X)" has X = N x price). Moved verbatim from `daily_briefing._review_list` section
    5 (numbers identical)."""
    reduction_pp = min(MACRO_AFFECTED_TRIM_REDUCTION_PP, weight)
    trim_dollars = round(portfolio_value * (reduction_pp / 100), 0)
    # Floored (owner 2026-10-23: never round a sub-share excess up to 1).
    trim_shares = trim_sizing.shares_for_dollars(trim_dollars, price)
    if price > 0 and trim_shares > 0:
        trim_dollars = round(trim_shares * price, 0)
        reduction_pp = (round(trim_dollars / portfolio_value * 100, 1)
                        if portfolio_value > 0 else reduction_pp)
    return {"reduction_pp": reduction_pp, "trim_dollars": trim_dollars,
            "trim_shares": trim_shares}


def _measured(row) -> bool:
    if not _finite(row.get("score")):
        return False
    avail = row.get("score_available", True)
    if avail is None or (isinstance(avail, float) and avail != avail):
        return False
    return bool(avail)


def _weight_key(row) -> tuple:
    w = row.get("weight")
    return (-float(w) if _finite(w) else math.inf, str(row.get("ticker")))


def _score_key(row) -> tuple:
    return (float(row["score"]), str(row.get("ticker")))


def pick(rows, act_cover, portfolio_value, exclude=()) -> dict:
    """The row the macro trim lands on. See the module doc. Returns
    {"target": row | None, "sizing": dict | None, "conviction_unmeasured": bool,
     "skipped_ahead": [{"ticker", "reason": "exit" | "covers"}],
     "all_covered": bool, "beside": "partial" | "unknown" | None}.

    `all_covered` = nothing is left to trim (every row was passed over for cover or
    excluded) while rows exist. `skipped_ahead` = passed-over rows that would have ranked
    before the target, in rank order. `beside` = the target's own cover state when the
    Act call sells it too but not enough to cover this trim."""
    out = {"target": None, "sizing": None, "conviction_unmeasured": False,
           "skipped_ahead": [], "all_covered": False, "beside": None}
    rows = [r for r in (rows or []) if isinstance(r, dict) and _norm(r.get("ticker"))]
    if not rows or not _finite(portfolio_value) or float(portfolio_value) <= 0:
        return out
    ex = {_norm(t) for t in (exclude or ())}
    pool = []          # [(row, sizing, state, entry)] for every row not silently excluded
    for r in rows:
        t = _norm(r.get("ticker"))
        if t in ex:
            continue
        w = r.get("weight")
        px = r.get("price")
        sz = size_trim(float(w) if _finite(w) else 0.0,
                       float(px) if _finite(px) else 0.0, float(portfolio_value))
        entry = act_sell_cover.cover_for(act_cover, t)
        pool.append((r, sz, act_sell_cover.cover_state(entry, sz["trim_shares"]), entry))
    eligible = [p for p in pool if p[2] != act_sell_cover.STATE_COVERED]
    if not eligible:
        out["all_covered"] = True
        return out
    measured = [p for p in eligible if _measured(p[0])]
    unmeasured_mode = not measured
    out["conviction_unmeasured"] = unmeasured_mode
    if unmeasured_mode:
        # Ticker as the tie-break so the same book always gets the same call
        # (weights are rounded to 0.1, so ties are real).
        key = lambda p: _weight_key(p[0])                                  # noqa: E731
        ranked = sorted(eligible, key=key)
        skipped_pool = [p for p in pool if p[2] == act_sell_cover.STATE_COVERED]
    else:
        key = lambda p: _score_key(p[0])                                   # noqa: E731
        ranked = sorted(measured, key=key)
        skipped_pool = [p for p in pool
                        if p[2] == act_sell_cover.STATE_COVERED and _measured(p[0])]
    target = ranked[0]
    out["target"], out["sizing"] = target[0], target[1]
    tk = key(target)
    ahead = sorted((p for p in skipped_pool if key(p) < tk), key=key)
    out["skipped_ahead"] = [
        {"ticker": str(p[0].get("ticker")),
         "reason": REASON_EXIT if (isinstance(p[3], dict) and p[3].get("exit") is True)
         else REASON_COVERS}
        for p in ahead]
    if target[2] in (act_sell_cover.STATE_PARTIAL, act_sell_cover.STATE_UNKNOWN):
        out["beside"] = target[2]
    return out


def all_covered_why(exposure_pct, names) -> str:
    """The WATCH why when every affected holding already has an Act Today sale at least as
    large as the pre-event trim. `names` = the affected holdings, either one ready-made
    string (the caller caps it with `_affected_text`) or an iterable of tickers."""
    if isinstance(names, str):
        text = names
    else:
        text = ", ".join(str(n) for n in (names or []))
    return (
        f"Affected-sector exposure {exposure_pct:.1f}% > "
        f"{MACRO_AFFECTED_TRIM_THRESHOLD_PCT:.0f}% trim threshold, but every affected "
        "holding already has a sale at least this large in today's Act Today call "
        f"({text}), so no separate pre-event trim is sized."
    )


def skipped_sentence(ticker, reason) -> str:
    """One passed-over name, said on the trim card: `reason` "exit" = the Act call sells the
    whole position, anything else = at least this much."""
    t = str(ticker)
    if reason == REASON_EXIT:
        return f"Not {t}: today's Act Today call already sells all of it."
    return f"Not {t}: today's Act Today call already sells at least this much of it."
