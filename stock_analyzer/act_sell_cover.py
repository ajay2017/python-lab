"""Gate module: how many shares today's Act Today call commits to selling per name
(DeferSizeBlind C1, docs/plans/defer-size-blind.md).

The standing trim surfaces (🔧 Tune-up, 🔗 Risk Analysis, the ⚖️ Rebalancer) used to
defer a ceiling / drift trim whenever the ticker was in a SET of Act Today sell
tickers, however small that sell was: a 1-share macro trim on a 30% holding hid the
ceiling trim to `SINGLE_NAME_CEILING` (about half the position). This module reads
each Act sell as the FEWEST shares it commits to selling (a lower bound), so a surface
defers only to a sell that is at least as large as its own trim. Pure (no streamlit,
no I/O; inputs are never mutated).

  item_covers(item)              -> {TICKER: {"exit": bool, "shares": int | None}}
  sell_cover_summary(act, review) -> {TICKER: {"exit": bool, "shares": int | None}}
  view_sell_cover(view)          -> that map | None   (None = Act Today view offline)
  cover_for(cover, ticker)       -> one ticker's entry | None  (None = not sold)
  cover_state(entry, need)       -> "show" | "covered" | "partial" | "unknown"
  cover_note(state, ticker, noun) -> str | None

THE SHAPE: absent from the map = the name is not sold. `exit` True = the whole
position goes (stop breach, deterioration exit). `shares` = the largest whole-share
count any one sell on the name commits to (U6: "at least N" sells do not add), None =
sold but no structured count (unknown). Per ticker: any exit wins; else the largest
valid count; else unknown.

Never a sell (same membership as `held_trim_owner.brief_sell_names_ticker`): Act kind
`risk` (its ticker is an attribution), `critical_news`, `premortem_triggered`, a macro
Act item, a WATCH / HOLD / TIGHTEN_ONLY review item. `risk_off_derisk` is a sell of
unknown size (owner Q2: never a full cover, since its smallest sale is zero). A
`sell_signal` covers only up to its `min_trim` shares (owner Q1), else unknown.

THE FAIL DIRECTIONS (the card is never hidden on a guess): cover None never defers;
an unknown size never fully defers; a malformed entry reads unknown. The card's own
size is NOT reduced by the Act sale: that would mix a Brief-time count with a
render-time book (one freshness per number).

No `$`, `**` or share count in any produced text (🙈 Hide values masks shares).
"""
from __future__ import annotations

import math
import numbers

from stock_analyzer.decision_bucket import EXIT_KINDS, _ACT_REVIEW_TYPES

# Act kinds that sell the whole position. `sell_signal` is in EXIT_KINDS but is a
# "reduce or exit" call with no count, so it is read separately (owner Q1).
_FULL_EXIT_KINDS = frozenset(EXIT_KINDS) - {"sell_signal"}

STATE_SHOW = "show"
STATE_COVERED = "covered"
STATE_PARTIAL = "partial"
STATE_UNKNOWN = "unknown"


def _norm(t) -> str:
    return str(t).strip().upper() if t is not None else ""


def _count(v, minimum: int) -> int | None:
    """A whole-share count >= `minimum`, or None. A non-bool int, or a finite integral
    float (cast to int); anything else (bool, "2", NaN, inf, 1.5, None) is None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, numbers.Integral):
        n = int(v)
    elif isinstance(v, numbers.Real):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(f) or f != math.floor(f):
            return None
        n = int(f)
    else:
        return None
    return n if n >= minimum else None


def valid_shares(v) -> int | None:
    """A valid Act sell count: a whole number of shares >= 1, else None (unknown)."""
    return _count(v, 1)


def valid_need(v) -> int | None:
    """A valid card / row size: a whole number of shares >= 0 (0 = the card sizes no
    trade), else None (unknown)."""
    return _count(v, 0)


def item_covers(item) -> dict:
    """The cover(s) one Act / Review item gives: {TICKER: {"exit", "shares"}}.

    Empty dict = the item is not a sell. Membership equals
    `held_trim_owner.brief_sell_names_ticker` (see module doc)."""
    if not isinstance(item, dict):
        return {}
    kind = item.get("kind")
    if kind:
        t = _norm(item.get("ticker"))
        if not t:
            return {}
        if kind in _FULL_EXIT_KINDS:
            return {t: {"exit": True, "shares": None}}
        if kind == "sell_signal":
            mt = item.get("min_trim")
            n = valid_shares(mt.get("trim_shares")) if isinstance(mt, dict) else None
            return {t: {"exit": False, "shares": n}}
        if kind == "deterioration_trim":
            return {t: {"exit": False, "shares": valid_shares(item.get("trim_shares"))}}
        if kind == "risk_off_derisk":
            return {t: {"exit": False, "shares": None}}      # owner Q2: never a full cover
        return {}
    action = item.get("action")
    if not (isinstance(action, dict) and action.get("type") in _ACT_REVIEW_TYPES):
        return {}
    n = valid_shares(action.get("trim_shares"))
    t_item = _norm(item.get("ticker"))
    t_trim = _norm(action.get("trim_ticker"))
    out: dict = {}
    # A macro trim carries its subject in `trim_ticker`; the shares belong to it.
    subject = t_trim or t_item
    if subject:
        out[subject] = {"exit": False, "shares": n}
    if t_item and t_item != subject:
        out[t_item] = {"exit": False, "shares": None}
    return out


def _merge(into: dict, t: str, cov: dict) -> None:
    cur = into.get(t)
    if cur is None:
        into[t] = {"exit": bool(cov.get("exit")), "shares": cov.get("shares")}
        return
    if cur["exit"] or cov.get("exit"):
        into[t] = {"exit": True, "shares": None}
        return
    a, b = cur["shares"], cov.get("shares")
    if a is None:
        best = b
    elif b is None:
        best = a
    else:
        best = max(a, b)                                     # U6: "at least N" sells do not add
    into[t] = {"exit": False, "shares": best}


def sell_cover_summary(act_today, review) -> dict:
    """Combine every Act / Review item into one {TICKER: {"exit", "shares"}} map."""
    out: dict = {}
    for it in list(act_today or []) + list(review or []):
        for t, cov in item_covers(it).items():
            _merge(out, t, cov)
    return out


def view_sell_cover(view) -> dict | None:
    """The cover map for the Act Today VIEW Home renders (`act_today_view(...)`), or None
    when that view could not be built (not a dict, or `state == "offline"`).

    Reads `view["active"]`, the post-demotion Act bucket, so a stop breach whose live
    price recovered (moved to `resolved`) never covers anything."""
    if not isinstance(view, dict) or view.get("state") == "offline":
        return None
    active = view.get("active")
    return sell_cover_summary(active if isinstance(active, list) else [], [])


def cover_for(cover, ticker):
    """One ticker's entry from a cover map (case-insensitive), or None = not sold.

    A non-dict map (None / a stale caller's set) is None: nothing is deferred. An entry
    that is present but not a dict reads as sold with an unknown size."""
    if not isinstance(cover, dict):
        return None
    want = _norm(ticker)
    if not want:
        return None
    if want in cover:
        entry = cover[want]
    else:
        entry = None
        found = False
        for k, v in cover.items():
            if _norm(k) == want:
                entry, found = v, True
                break
        if not found:
            return None
    return entry if isinstance(entry, dict) else {"exit": False, "shares": None}


def cover_state(entry, need) -> str:
    """show | covered | partial | unknown for one card / row against one cover entry.

    entry None (not sold)              -> show (the caller's own rules apply)
    exit                               -> covered
    need == 0 (the card sizes no trade) -> covered (owner Q8)
    shares >= need (both known)        -> covered (equality covers: floored whole shares)
    shares < need                      -> partial (keep the card at its full size + a line)
    shares or need unknown             -> unknown (keep the card + a line)

    Invariant: covered => exit, or need == 0, or shares >= need."""
    if entry is None:
        return STATE_SHOW
    if not isinstance(entry, dict):
        entry = {"exit": False, "shares": None}
    if entry.get("exit") is True:
        return STATE_COVERED
    n = valid_need(need)
    if n == 0:
        return STATE_COVERED
    s = valid_shares(entry.get("shares"))
    if s is None or n is None:
        return STATE_UNKNOWN
    return STATE_COVERED if s >= n else STATE_PARTIAL


_NEW_BOOK = ("once it is logged this {noun} is re-sized from your new book, "
             "so it is not a second sale on top of it.")


def cover_note(state, ticker, noun) -> str | None:
    """The line a kept card / row shows beside an Act Today sell that does not fully
    cover it. `noun` = "ceiling trim" (Tune-up / Risk Analysis) or "row" (Rebalancer).
    None for any other state. No `$`, `**` or share count."""
    t = _norm(ticker) or "that name"
    tail = _NEW_BOOK.format(noun=noun)
    if state == STATE_PARTIAL:
        return (f"🏠 Today's Act Today call also sells {t}, but fewer shares than this "
                f"{noun}. Do that sale first (🏠 Home); {tail}")
    if state == STATE_UNKNOWN:
        return (f"🏠 Today's Act Today call also sells {t} (🏠 Home), and its size couldn't "
                f"be compared with this {noun}. Do that sale first; {tail}")
    return None
