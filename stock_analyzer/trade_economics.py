"""Trade-level dollar economics — display arithmetic, not sizing policy.

WHY THIS IS ITS OWN MODULE, NOT A GROWTH OF risk.py
----------------------------------------------------
`risk.py` is a `_GATE_FILES` member — every commit touching it needs the
mandatory Opus review Hard Rule #4 requires for a sizing/gate change. Nothing
here sizes a position or gates a recommendation; it turns an already-decided
entry/stop/reward:risk into the plain dollars-and-percentage arithmetic a
reader needs to judge a trade for themselves. Per CLAUDE.md's coding
conventions ("prefer a NEW module over growing a `_GATE_FILES` one"), that
belongs beside `outage_gate.py` and `coord_freshness.py`, not inside `risk.py`.

WHY NO CONFIDENCE NUMBER
-------------------------
The owner asked for a "confidence this will make money" percentage. This
project's own Engine Track Record study (N=239 real recommendation rows,
memory `project_engine_track_record`) measured that the composite score does
NOT rank forward alpha within the 65-79 band — so a manufactured confidence %
would be exactly the fabricated-value bug class this repo has already had to
retract (commit `38c50e3`, "withhold fabricated pillar scores instead of
showing them as real"). What this module returns instead is honest, derived
arithmetic: how many dollars are at risk, how many are at target, and the
win rate the trade's own reward:risk ratio REQUIRES to break even — a
REQUIREMENT, not a forecast. Callers must render it that way (see
notify.render_watchlist_entries_email: "needs >N% win rate to break even",
never "confidence"/"probability"/"odds").

NaN SAFETY
----------
Every numeric coercion here uses the `x != x` self-comparison idiom (NaN is
the only float that is not equal to itself), never a bare `is None` or
truthy check. A NaN value read off a DataFrame column passes `is not None`
AND is truthy — this exact trap has already shipped two live bugs in this
project (memory `project_engine_track_record`,
`feedback_none_sentinel_meets_pandas`).
"""
from __future__ import annotations


def _num(v) -> "float | None":
    """Coerce to float, or None on anything unparseable — including NaN."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def breakeven_hit_rate(rr) -> "float | None":
    """Fraction of trades that must win to break even at this reward:risk.

    breakeven = 1 / (1 + rr) — e.g. a 2.2:1 reward:risk needs a 31.25% win
    rate to break even across many trades of the same shape.

    Returns None (never a fabricated number) when `rr` is None, NaN, or
    <= 0 — a non-positive reward:risk has no meaningful breakeven.
    """
    r = _num(rr)
    if r is None or r <= 0:
        return None
    return 1.0 / (1.0 + r)


def trade_economics(price, stop, rr) -> dict:
    """Dollar/percentage arithmetic for one entry — an explicit-state dict,
    never a bare None. Mirrors `risk.capital_equivalent_risk`'s (F-272)
    explicit-state shape and the reasoning behind it (memory
    `feedback_sentinel_is_present`, `feedback_none_sentinel_meets_pandas`): a
    bare None collapses "not computed" and "computed, nothing to show" into
    one falsy value a caller can silently mis-handle.

    Returns one of four states, always with the same 5 numeric keys present
    (populated or None — never a fabricated 0.0 standing in for "unknown"):

      {"state": "unpriced", ...all None...}
          `price` or `stop` is missing, non-positive, or NaN. No arithmetic
          is derivable from either half.
      {"state": "no_stop_room", ...all None...}
          price and stop are both valid numbers, but price <= stop — there
          is no room between entry and stop to measure risk against.
      {"state": "no_rr", "risk_per_share": <float>, "risk_pct": <float>,
       "gain_per_share": None, "target": None, "breakeven_pct": None}
          price/stop are usable so the dollar-RISK half is derivable, but
          `rr` is missing/NaN/<=0 — the target/breakeven half is not.
      {"state": "ok", "risk_per_share": <float>, "risk_pct": <float>,
       "gain_per_share": <float>, "target": <float>, "breakeven_pct": <float>}
          every input usable; the full picture is derivable.

    Formulas (all off the raw, unsigned inputs — callers choose how to sign
    them for display; risk_pct/gain_per_share are magnitudes here, not
    pre-signed as a loss/gain):
      risk_per_share = price - stop
      risk_pct       = risk_per_share / price * 100
      target         = price + rr * risk_per_share
      gain_per_share = target - price
      breakeven_pct  = breakeven_hit_rate(rr) * 100
    """
    _empty = {"risk_per_share": None, "risk_pct": None,
              "gain_per_share": None, "target": None, "breakeven_pct": None}

    p = _num(price)
    s = _num(stop)
    if p is None or s is None or p <= 0 or s <= 0:
        return {"state": "unpriced", **_empty}
    if p <= s:
        return {"state": "no_stop_room", **_empty}

    risk_per_share = p - s
    risk_pct = risk_per_share / p * 100.0

    # Validated here, not inferred from breakeven_hit_rate's return: a local
    # invariant, not a cross-function one that becomes a None-multiply below
    # if that helper's own guard ever loosens.
    r = _num(rr)
    if r is None or r <= 0:
        return {
            "state": "no_rr",
            "risk_per_share": risk_per_share,
            "risk_pct": risk_pct,
            "gain_per_share": None,
            "target": None,
            "breakeven_pct": None,
        }

    be = breakeven_hit_rate(r)
    target = p + r * risk_per_share
    gain_per_share = target - p
    return {
        "state": "ok",
        "risk_per_share": risk_per_share,
        "risk_pct": risk_pct,
        "gain_per_share": gain_per_share,
        "target": target,
        "breakeven_pct": None if be is None else be * 100.0,
    }


def entry_tier(deterioration_warning) -> str:
    """"caution" when `deterioration_warning` is a non-empty string, else
    "clean".

    A deterioration-flagged watchlist name (a broken chart) previously got
    the identical green "READY TO ENTER" header as a clean one, with the
    warning buried a few lines down — this drives a visibly distinct header
    instead. See notify.render_watchlist_entries_email.
    """
    return "caution" if isinstance(deterioration_warning, str) and deterioration_warning != "" else "clean"
