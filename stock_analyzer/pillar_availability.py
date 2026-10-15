"""Single source of truth for "can this bundle back a verdict?".

Pure, no I/O, no streamlit.

Contract
--------
`bundle_loader.load_bundle` keeps `total` / `etf_total` NUMERIC even when a
pillar could not be measured (the composite then carries a fabricated neutral
50 for that leg). Availability flags say which legs are real:

  * `fundamentals_available` (Business Quality) and `val_available` (Valuation)
    -- equity composite.
  * `t_available` (Technical; enough bars + every required leg, floor
    `constants.TECHNICAL_MIN_BARS`) -- BOTH the equity and the ETF composite.
  * `etf_available` (expense ratio known) + a finite `etf_total` -- ETF
    composite, only for `asset_type == "etf"`.

`verdict_basis` is the ONE function every decision consumer asks, so the
duplicated `fundamentals_available and val_available` expressions cannot drift:

  "equity"   -> bq, val and technical all measured: the equity composite stands.
  "etf"      -> equity basis unavailable, but an ETF with a real etf_total and
                a measured technical pillar: the ETF composite stands.
  "withheld" -> otherwise: no verdict is issued.

Precedent: exactly the existing Business Quality / Valuation withhold -- the
number stays in the bundle, a flag plus this function decides. Equity is
checked first (same precedence portfolio.py used before this module existed).

Fail-open on a missing key: every flag defaults to True (etf_available to
False, as before), so a legacy bundle written before a flag existed behaves
exactly as it did. Composite-keyed protective calls going dormant for a
withheld name is ACCEPTED (same as bq/val today); price-based protection
(stops, concentration, the deterioration ladder, risk-off) never reads this.

Accepted edge: a >= TECHNICAL_MIN_BARS history whose last 20 closes are flat
can lose the Bollinger leg (zero band width; pandas rolling-std residue makes
it path-dependent) and so read unmeasured. That is ACCEPTED as a withhold -- a
halted / forward-filled quote is not a reading worth a verdict -- and is not
engineered around.
"""
from __future__ import annotations

import math

from stock_analyzer import asset_type
from stock_analyzer.constants import TECHNICAL_MIN_BARS


def _is_etf(b: dict) -> bool:
    return b.get("asset_type") == asset_type.ASSET_TYPE_ETF


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError, OverflowError):
        return False


def _t_ok(b: dict) -> bool:
    return bool(b.get("t_available", True))


def _fund_ok(b: dict) -> bool:
    # EXACTLY the expression portfolio.py used before this module existed.
    return bool(b.get("fundamentals_available", True) and b.get("val_available", True))


def _etf_ok(b: dict) -> bool:
    return _is_etf(b) and bool(b.get("etf_available", False)) and _finite(b.get("etf_total"))


def verdict_basis(bundle: "dict | None") -> str:
    """Return "equity", "etf" or "withheld" (see module docstring)."""
    if not isinstance(bundle, dict) or not bundle:
        return "withheld"
    t_ok = _t_ok(bundle)
    if _fund_ok(bundle) and t_ok:
        return "equity"
    if _etf_ok(bundle) and t_ok:
        return "etf"
    return "withheld"


def unmeasured_pillars(bundle: "dict | None") -> "list[str]":
    """Ordered human labels of the pillars that could not be measured."""
    if not isinstance(bundle, dict) or not bundle:
        return []
    out: list[str] = []
    if not _t_ok(bundle):
        out.append("Technical")
    if _is_etf(bundle):
        if not bool(bundle.get("etf_available", False)):
            out.append("Expense ratio")
    else:
        if not bundle.get("fundamentals_available", True):
            out.append("Business Quality")
        if not bundle.get("val_available", True):
            out.append("Valuation")
    return out


def technical_history_short(t_bars) -> bool:
    """Is a withheld Technical pillar explained by too little price history?

    `t_available` is False either for fewer than `TECHNICAL_MIN_BARS` bars OR
    for a history at/above the floor where a required leg could not be
    computed (e.g. a flat tail: zero Bollinger width, RSI 0/0). Only a KNOWN
    bar count at/above the floor says "not a history-length problem"; unknown
    or unparseable counts keep the "too short" reading (the common cause)."""
    try:
        n = int(t_bars)
    except (TypeError, ValueError, OverflowError):
        return True
    return n < TECHNICAL_MIN_BARS


def technical_clause(t_bars) -> str:
    """Shared plain-language clause for a withheld Technical pillar, so the
    Watchlist, Quick Research and banner wording cannot drift. Branches on
    `technical_history_short`. `t_bars` may be None/garbage -> the clause
    omits the count rather than printing "None"."""
    if not technical_history_short(t_bars):
        return ("a required technical reading couldn't be computed from recent "
                "prices (e.g. a flat or halted quote)")
    try:
        n = int(t_bars)
    except (TypeError, ValueError, OverflowError):
        n = None
    if n is None or n < 0:
        return (f"the price history is too short ({TECHNICAL_MIN_BARS} sessions "
                "needed) to measure the technical trend")
    return (f"the price history is too short ({n} sessions; {TECHNICAL_MIN_BARS} "
            "needed) to measure the technical trend")


def withheld_split(bundle: "dict | None") -> "tuple[bool, bool]":
    """(technical_missing, other_missing) for a bundle that is withheld.
    `other_missing` is True whenever the withhold is not purely technical --
    including the case where no specific label is available -- so callers fall
    back to today's fundamentals/valuation wording."""
    missing = unmeasured_pillars(bundle)
    tech = "Technical" in missing
    other = (not tech) or any(m != "Technical" for m in missing)
    return tech, other


def withheld_heading(bundle: "dict | None") -> str:
    """Plain-text phrase naming EXACTLY which pillar(s) are unmeasured, for a
    "Verdict withheld — <phrase>" heading. "fundamentals" is used only when
    Business Quality itself is missing (Valuation rides along under it);
    a Valuation-only gap says "valuation". With no usable label it falls
    back to today's "fundamentals unavailable"."""
    missing = unmeasured_pillars(bundle)
    tech = "Technical" in missing
    if "Business Quality" in missing:
        other = "fundamentals"
    elif "Valuation" in missing:
        other = "valuation"
    elif "Expense ratio" in missing:
        other = "expense ratio"
    else:
        other = ""
    if tech and other:
        return f"{other} and technical trend unavailable"
    if tech:
        return "technical trend not measured"
    return f"{other or 'fundamentals'} unavailable"


def withheld_lead(bundle: "dict | None", ticker: str) -> str:
    """Plain-text first sentence(s) of the STOCK withheld banner, true for the
    case: Business Quality missing -> the "couldn't get fundamental data" text;
    Valuation-only missing (Business Quality measured) -> no objective
    valuation metric, never a claim that fundamentals couldn't be fetched.
    Caller escapes. (Technical is appended by the caller via technical_clause.)"""
    missing = unmeasured_pillars(bundle)
    if "Business Quality" in missing or "Valuation" not in missing:
        return (
            f"We couldn't get {ticker}'s fundamental data from any source right now "
            "(Yahoo Finance returned nothing and the failover couldn't backfill it). "
            "The composite needs the Business Quality leg to issue a trustworthy call — "
            "without it the score defaults to a neutral 50 and produces a misleading "
            "verdict, so the app is holding the recommendation rather than guessing."
        )
    return (
        f"No objective valuation metric (Forward P/E or FCF Yield) was usable for "
        f"{ticker} — analyst opinion alone isn't enough to score the Valuation pillar. The composite "
        "needs the Valuation leg to issue a trustworthy call — without it the score "
        "defaults to a neutral 50 and produces a misleading verdict, so the app is "
        "holding the recommendation rather than guessing."
    )
