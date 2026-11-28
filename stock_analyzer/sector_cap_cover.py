"""Gate module: what today's Act Today call already does about the sector Hard Cap card
(brief-consolidation C1b, docs/plans/brief-consolidation-spec.md section 1b).

The sector Hard Cap card (`risk` kind, rec_type `sector_concentration`) is its own Act
Today card (C1): its ticker is only an attribution (the sector's largest holding), so it
sits beside a same-name stop breach / Sell instead of being dropped by it. That leaves
two cards that can both sell the same shares. This module reads the post-demotion Act
view's sell cover (`act_sell_cover.view_sell_cover`) against the sector's member holdings
and returns the one line the card shows and, when a member is sold WHOLE, the trim target
net of it (owner Q2: net full exits only; a partial seller is named, never netted).

  decide(flag, cover) -> {"exited", "also_selling", "netted", "covered", "candidates",
                          "net_target_dollar", "net_target_pp", "post_pct", "held_back",
                          "line"}

`exited` / `also_selling` are upper-cased member tickers (exit True / any other sell).
Netting happens only when EVERY exited member has a finite market value > 0 and the
target is finite and > 0: then `net_target_dollar = max(0, target - sum of exited values)`,
`covered = (net == 0)` (the exits alone bring the sector to SECTOR_ELEVATED, owner Q3),
and `candidates` drops the exited names (they are already being sold in full). Otherwise
`netted` is False and `net_target_*` / `candidates` equal the flag's own (the plan is
unchanged, the line says it was sized before the sale). `cover` None (the Act Today view
could not be built), a missing flag, or no sold member -> no line, plan unchanged: a
sale that couldn't be read never shrinks a trim.

Members come from `flag["sector_members"]`; a legacy memoized flag without it falls back
to `trim_candidates` + `trim_excluded_recent`. Pure (no streamlit, no I/O; inputs are
never mutated). `post_pct` = the sector's weight if the exits' proceeds are kept as cash
(the target assumes reinvestment, so a covered line claims "down to the warn level" only
when it is at or below SECTOR_ELEVATED; None when unknown); `held_back` = the flag's
`trim_excluded_recent` minus the exited names. No `$`, `**` or share count in the line
(🙈 Hide values masks shares).
"""
from __future__ import annotations

import math
import numbers

from stock_analyzer import act_sell_cover
from stock_analyzer.constants import SECTOR_ELEVATED


def _norm(t) -> str:
    return str(t).strip().upper() if t is not None else ""


def _finite(v) -> float | None:
    """A finite float, or None (bool, str, NaN, inf, None)."""
    if isinstance(v, bool) or not isinstance(v, numbers.Real):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _list(v) -> list:
    """`v` when it is a list, else [] (a missing / malformed key is just empty)."""
    return list(v) if isinstance(v, list) else []


def _members(flag: dict) -> list[dict]:
    """Every member holding as {"ticker" (upper), "market_value"}, deduped, in order."""
    raw = flag.get("sector_members")
    if not isinstance(raw, list):
        raw = _list(flag.get("trim_candidates")) + _list(flag.get("trim_excluded_recent"))
    out: list[dict] = []
    seen: set[str] = set()
    for m in raw:
        if not isinstance(m, dict):
            continue
        t = _norm(m.get("ticker"))
        if not t or t in seen:
            continue
        seen.add(t)
        out.append({"ticker": t, "market_value": m.get("market_value")})
    return out


def _names(ts: list[str]) -> str:
    """"A", "A and B", "A, B and C"."""
    if len(ts) <= 1:
        return "".join(ts)
    return ", ".join(ts[:-1]) + " and " + ts[-1]


def _pp(net: float, denom) -> float | None:
    d = _finite(denom)
    if d is None or d <= 0:
        return None
    return net / d * 100.0


def _post_pct(target: float, sold: float, denom) -> float | None:
    """The sector's weight of invested holdings if the exited names' value `sold` is kept
    as cash: the target assumes reinvestment (fixed denominator `denom`), so the sector
    holds S = target + SECTOR_ELEVATED% x denom before the sale and (S - sold) of
    (denom - sold) after it. None when denom is unknown or not above `sold`."""
    d = _finite(denom)
    if d is None or d <= sold:
        return None
    return (target + SECTOR_ELEVATED / 100.0 * d - sold) / (d - sold) * 100.0


def decide(flag, cover) -> dict:
    """See the module doc. `flag` = the sector card's `sector_concentration` risk flag;
    `cover` = `act_sell_cover.view_sell_cover(act_view)` (None = view offline)."""
    f = flag if isinstance(flag, dict) else {}
    cands = _list(f.get("trim_candidates"))
    out = {
        "exited": [], "also_selling": [], "netted": False, "covered": False,
        "candidates": cands,
        "net_target_dollar": f.get("trim_target_dollar"),
        "net_target_pp": f.get("trim_target_pp"),
        "post_pct": None,
        "held_back": _list(f.get("trim_excluded_recent")),
        "line": None,
    }
    if not f or not isinstance(cover, dict):
        return out

    members = _members(f)
    exited: list[dict] = []
    also: list[str] = []
    for m in members:
        entry = act_sell_cover.cover_for(cover, m["ticker"])
        if entry is None:
            continue
        if entry.get("exit") is True:
            exited.append(m)
        else:
            also.append(m["ticker"])
    out["exited"] = [m["ticker"] for m in exited]
    out["also_selling"] = also
    # A same-day buy that Act Today exits in full is not "held back to settle".
    out["held_back"] = [r for r in out["held_back"]
                        if not (isinstance(r, dict)
                                and _norm(r.get("ticker")) in set(out["exited"]))]
    if not exited and not also:
        return out

    sector = str(f.get("sector") or "").strip() or "sector"
    ex_names = out["exited"]
    parts: list[str] = []

    if exited:
        target = _finite(f.get("trim_target_dollar"))
        values = [_finite(m["market_value"]) for m in exited]
        if (target is not None and target > 0
                and all(v is not None and v > 0 for v in values)):
            net = max(0.0, target - sum(values))
            out["netted"] = True
            out["covered"] = net == 0
            out["net_target_dollar"] = net
            out["net_target_pp"] = _pp(net, f.get("trim_target_denom"))
            out["post_pct"] = _post_pct(target, sum(values), f.get("trim_target_denom"))
            drop = set(ex_names)
            out["candidates"] = [c for c in cands
                                 if not (isinstance(c, dict)
                                         and _norm(c.get("ticker")) in drop)]
        post = out["post_pct"]
        if out["covered"] and post is not None and post <= SECTOR_ELEVATED:
            parts.append(
                f"🏠 Today's Act Today call already sells all of {_names(ex_names)}, which by "
                f"itself brings {sector} down to the {SECTOR_ELEVATED:.0f}% level. No other "
                f"{sector} trim is needed today.")
        elif out["covered"]:
            # The target assumes the proceeds are reinvested in other sectors (fixed
            # denominator); kept as cash the sector would sit above the warn level.
            txt = (f"🏠 Today's Act Today call already sells all of {_names(ex_names)}, which "
                   f"covers this card's whole {sector} trim target, so no other {sector} trim "
                   "is needed today.")
            if post is not None:
                txt += (f" The target assumes the sale's proceeds go into other sectors "
                        f"(Redeploy in the 🔎 Rebalance plan); kept as cash, {sector} would still be about "
                        f"{post:.1f}% of your invested holdings.")
            parts.append(txt)
        elif out["netted"]:
            parts.append(
                f"🏠 Today's Act Today call already sells all of {_names(ex_names)} ({sector}). "
                "That sale counts toward this sector trim; the 🔎 Rebalance plan below sizes "
                "only the rest.")
        else:
            parts.append(
                f"🏠 Today's Act Today call already sells all of {_names(ex_names)} ({sector}). "
                "The 🔎 Rebalance plan below was sized before that sale; re-check it once the "
                "sale is logged.")

    if also:
        one = len(also) == 1
        sent = (f"{_names(also)} also {'has' if one else 'have'} "
                f"{'its' if one else 'their'} own sell in today's Act Today call; the "
                f"🔎 Rebalance plan was sized before {'that sale' if one else 'those sales'} "
                f"and shrinks once {'it is' if one else 'they are'} logged; for "
                f"{'that name' if one else 'those names'}, sell the larger of the two "
                "amounts, not the two added together.")
        parts.append(sent if parts else "🏠 " + sent)

    out["line"] = " ".join(parts)
    return out
