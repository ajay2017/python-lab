"""In-text price check for the Pre-Mortem trigger (owner U1-U4, 2026-11-01).

`premortem_monitor.extract_trigger` asks the AI for {checkable, direction,
price_level} from the investor's BUY-time commitment. That price is saved
without confirmation and becomes permanent: it feeds `detect_premortem_triggers`
and is the R-Multiple declared stop. A model that echoes the prompt's own
example ({"checkable": true, "direction": "below", "price_level": 150.0}) could
plant a fabricated stop. This module refuses any checkable price that is not
WRITTEN, as a numeral, in the investor's commitment.

Tightening-only: the guard can turn a checkable trigger into "no trigger"; it
never creates or changes a price or direction. The same `price_mentions`
extractor defines "invented" in the model eval (`llm_role_eval.classify_trigger`),
so the eval applies the same rule production does (both tolerances are $0.01).

Known limits (accepted): a price written in words ("one fifty") has no numeral
and is refused; "140,150" with no space reads as 140150 and fails closed;
"USD150" and "below150" (a letter straight before the number) are refused, also
failing closed. A number that is also a non-price number can still match: "S&P
500" with a space, "hold 150 trading days", "the 50 and 200 day", a bare year
("by 2027"); and "1.5k" reads as 1.5.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

from stock_analyzer.constants import PREMORTEM_TRIGGER_PRICE_TOL

_NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")
# After the number (optional whitespace; a `$` does not exempt it): a percentage.
_PCT_AFTER = re.compile(r"\s*(?:%|(?:percent|per\s+cent|pct)\b)", re.IGNORECASE)
# After a number WITHOUT a `$`: a time unit (optional `-` or space) or an `x` multiple.
_UNIT_AFTER = re.compile(
    r"[-\s]?(?:days?|d|dma|sma|ma|weeks?|wk|months?|mo|years?|yr|quarters?)\b",
    re.IGNORECASE)
_MULT_AFTER = re.compile(r"x(?![A-Za-z0-9])", re.IGNORECASE)


def price_mentions(text) -> list[float]:
    """Every numeral in `text` that could be a price, commas stripped.

    Excluded: a percentage (`%`, percent, per cent, pct after optional
    whitespace, even with a `$`); and, only without a `$` prefix, a time unit
    straight after the number (day(s), d, dma, sma, ma, week(s), wk, month(s),
    mo, year(s), yr, quarter(s)), an `x` multiple ("2x"), or a letter
    immediately before the number (Q3, S&P500). None / "" gives []."""
    if not text:
        return []
    text = str(text)
    out: list[float] = []
    for m in _NUM_RE.finditer(text):
        raw = m.group(0).rstrip(",")
        end = m.start() + len(raw)
        has_dollar = raw.startswith("$")
        if _PCT_AFTER.match(text, end):
            continue
        if not has_dollar:
            if m.start() > 0 and text[m.start() - 1].isalpha():
                continue
            if _UNIT_AFTER.match(text, end) or _MULT_AFTER.match(text, end):
                continue
        try:
            out.append(float(raw.lstrip("$").replace(",", "")))
        except ValueError:
            continue
    return out


def price_in_text(price, text, tol: float = PREMORTEM_TRIGGER_PRICE_TOL) -> bool:
    """True when any price mention in `text` is within `tol` (<=) of `price`."""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(p):
        return False
    return any(abs(n - p) <= tol for n in price_mentions(text))


def guard_trigger(parsed, text) -> tuple[dict | None, float | None]:
    """Refuse a checkable price that isn't in the commitment text.

    Returns (parsed, None) for None or a not-checkable result (untouched) and for
    a checkable price found in the text; (None, price) for a checkable price NOT
    in the text. Never creates or changes a price or direction."""
    if not isinstance(parsed, Mapping) or not parsed.get("checkable"):
        return parsed, None
    try:
        price = float(parsed.get("price_level"))
    except (TypeError, ValueError):
        return None, None
    if price_in_text(price, text):
        return parsed, None
    return None, price


def refused_note(price, direction) -> str:
    """The visible explanation shown on the confirm screen after a refusal."""
    p = f"${float(price):,.2f}"
    # Only a "below" trigger would have become the R-Multiple declared stop.
    consequence = ("it won't be monitored or used as your declared stop."
                   if direction == "below" else "it won't be monitored.")
    article = "an" if str(direction).startswith(("a", "e", "i", "o", "u")) else "a"
    return (f"The AI read {article} {direction} trigger of {p} from your commitment, but {p} "
            "isn't in what you wrote, so no automatic trigger is saved for this buy: "
            f"{consequence} To have it "
            "monitored, Cancel and write the price as a number "
            "(e.g. \"closes below $150\").")


def trigger_caption(trigger) -> str | None:
    """"Monitored trigger: closes below $150.00" for a checkable trigger, else None."""
    if not isinstance(trigger, Mapping) or not trigger.get("checkable"):
        return None
    if trigger.get("direction") not in ("below", "above"):
        return None
    try:
        price = float(trigger.get("price_level"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return f"Monitored trigger: closes {trigger['direction']} ${price:,.2f}"
