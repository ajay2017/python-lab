"""Gate module: one Act trim per name in the 🏠 Brief Review list (U6,
docs/plans/brief-one-trim-per-name-u6.md).

`daily_briefing._review_list` builds its Review items in independent sections, so
one held name can carry several Act Today trims at once (an earnings-week trim to
10%, a low-score trim to 8%, a profit-lock trim of 25% of the position, a macro
pre-event trim), each with its own size, or an Act trim beside a Watch card that
says "sizes nothing" about the same name. This module settles that, once, on the
finished list:

  one_trim_per_name(items) -> list[dict]
      For each subject T (`decision_bucket._ticker`: the item's ticker, or a macro
      trim's `action.trim_ticker`), the sized Act trim that SELLS THE MOST SHARES
      wins and keeps its action dict exactly as it was (shares, dollars, from /
      target, stop are never recomputed). Every other T item becomes a non-trade
      Monitoring card with reworded text. The winner gains one "also covers"
      sentence when a sized trim was demoted.
  covered_action_text(action) -> str | None
      The render text of a Watch card whose action carries `covered_by` (app.py
      `_fmt_action` prints it instead of "no action today").
  strip_carriers(items) -> list[dict]
      Drops the `_u6_*` carrier keys (the fallback when the merge raises).

Why shares: each of the four trims is "sell at least N shares of T today" on the
same book in the same build, and each one's stated outcome is met by any larger
sale of T (a target trim: post-sale weight is at or under its target; profit-lock:
more gain locked; macro: the affected exposure is cut by at least its pp). So the
merged card never sells less than any card did before (plan §2.2).

Fail-safe (plan §2.2): when any Act-type item on T has a `trim_shares` that is not
an int >= 1, an unknown kind, or a loser lacks the carrier facts its reworded text
needs, T's items are left exactly as they were: nothing is demoted, every sell
stays visible. The act lane is never read or touched.

The producer (`_review_list`) hands over the facts it already has in `_u6_*` keys;
they never leave this module. Pure: no streamlit, no I/O, input never mutated,
the decision is independent of the input order.
"""
from __future__ import annotations

import math
from numbers import Integral

from stock_analyzer.constants import (
    LARGE_POSITION_WEIGHT_PCT,
    MACRO_AFFECTED_TRIM_THRESHOLD_PCT,
    STOP_PROFIT_LOCK_TRIM_PCT,
    WEAK_CONVICTION_SCORE,
)
from stock_analyzer.decision_bucket import _ACT_REVIEW_TYPES, _ticker
from stock_analyzer.score_text import composite_score_text
from stock_analyzer.text_fmt import share_word

_CARRIER_PREFIX = "_u6_"

# Tie-break order when two trims sell the same number of shares: the profit-lock
# card also carries the stop raise, then the low-score trim, the earnings trim,
# then macro (plan §2.1). Also the order the winner's "also covers" parts read in.
_RANK = {"lock": 0, "weak_large": 1, "earnings": 2, "macro": 3}


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _valid_shares(x) -> bool:
    """A real whole-share count: an int (not a bool) of at least 1."""
    return isinstance(x, Integral) and not isinstance(x, bool) and x >= 1


def _sh(n) -> str:
    return f"{n} {share_word(n)}"


def _action(item) -> dict | None:
    a = item.get("action") if isinstance(item, dict) else None
    return a if isinstance(a, dict) else None


def _kind(item) -> str | None:
    """lock | weak_large | earnings | macro for an Act-type review item, else None."""
    a = _action(item)
    if a is None or a.get("type") not in _ACT_REVIEW_TYPES:
        return None
    t = a.get("type")
    if t == "TRIM_AND_TIGHTEN":
        return "lock"
    if t == "PROTECTIVE_TRIM":
        return "macro"
    rk = a.get("reason_key")
    return rk if rk in ("weak_large", "earnings") else None


def _display_ticker(item) -> str:
    a = _action(item)
    tk = a.get("trim_ticker") if a is not None else None
    return str(item.get("ticker") or tk or "")


def _strip_one(item: dict) -> dict:
    out = {k: v for k, v in item.items()
           if not (isinstance(k, str) and k.startswith(_CARRIER_PREFIX))}
    a = out.get("action")
    if isinstance(a, dict):
        out["action"] = dict(a)
    return out


def strip_carriers(items) -> list:
    """`items` with every `_u6_*` key removed (copies; non-dicts pass through)."""
    return [_strip_one(it) if isinstance(it, dict) else it for it in (items or [])]


def covered_action_text(action) -> str | None:
    """Render text for a Watch card whose trade was folded into today's Act Today
    trim of the same name; None when the action isn't such a card."""
    if not isinstance(action, dict):
        return None
    t = str(action.get("covered_by") or "").strip()
    if not t:
        return None
    return f"covered by today's Act Today trim of {t}; no separate trade."


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _win_key(item):
    a = _action(item)
    return (-a["trim_shares"], _RANK[_kind(item)],
            str(item.get("_u6_event_date") or ""), str(item.get("event") or ""),
            str(item.get("headline") or ""))


def _carrier_str(item, key) -> str | None:
    v = item.get(key)
    return v if isinstance(v, str) and v.strip() else None


def _score_text(item) -> str | None:
    if "_u6_score" not in item:
        return None
    return composite_score_text(item.get("_u6_score"), WEAK_CONVICTION_SCORE)


def _merge_subject(group: list[dict]) -> list[dict] | None:
    """New dicts (same order as `group`) for one subject, or None to leave it as is."""
    acts = [it for it in group if _is_act_type(it)]
    if not acts:
        return None
    for it in acts:
        if _kind(it) is None or not _valid_shares(_action(it).get("trim_shares")):
            return None                                   # fail-safe: show every sell
    winner = min(acts, key=_win_key)
    m = _action(winner)["trim_shares"]
    t = _display_ticker(winner)
    sell = f"Today's Act Today trim of {t} sells {_sh(m)}"
    weak_present = any(
        _kind(it) == "weak_large" or it.get("_u6_kind") == "weak_large" for it in group)

    new_by_id: dict[int, dict] = {}
    parts: list[tuple] = []                               # (sort key, text) of sized losers
    for it in group:
        if it is winner:
            continue
        a = _action(it)
        a = a if a is not None else {}
        k = _kind(it)
        new = dict(it)
        if k is not None:                                 # a sized Act-trim loser
            n = a["trim_shares"]
            if k == "lock":
                tg = it.get("_u6_tighten")
                if (not isinstance(tg, dict) or not isinstance(tg.get("why"), str)
                        or "new_stop" not in tg):
                    return None
                new["action"] = {"type": "TIGHTEN_ONLY", "new_stop": tg["new_stop"]}
                new["why"] = tg["why"]
                text = (f"the profit-lock trim of {STOP_PROFIT_LOCK_TRIM_PCT:.0f}% of the "
                        f"position ({_sh(n)})")
            elif k == "weak_large":
                sc, w = _score_text(it), it.get("weight")
                if sc is None or not _finite(w) or not _finite(a.get("target_weight")):
                    return None
                new["action"] = {"type": "WATCH", "covered_by": t}
                new["why"] = (
                    f"Weight {float(w):.1f}% ≥ {LARGE_POSITION_WEIGHT_PCT:.0f}% AND score "
                    f"{sc} < {WEAK_CONVICTION_SCORE}. {sell}, no fewer than the {_sh(n)} a "
                    f"trim to {float(a['target_weight']):.1f}% would sell, so no separate trim.")
                new["trigger"] = f"Covered by today's Act Today trim of {t}."
                text = f"the low-score trim to {float(a['target_weight']):.1f}% ({_sh(n)})"
            elif k == "earnings":
                trig = _carrier_str(
                    it, "_u6_trigger_trimmed_no_reentry" if weak_present
                    else "_u6_trigger_trimmed")
                if trig is None or not _finite(a.get("target_weight")):
                    return None
                new["action"] = {"type": "WATCH", "covered_by": t}
                new["why"] = (
                    f"{sell}, no fewer than the {_sh(n)} a trim to "
                    f"{float(a['target_weight']):.1f}% would sell, so earnings week adds no "
                    "separate trade.")
                new["trigger"] = trig
                text = f"the earnings-week trim to {float(a['target_weight']):.1f}% ({_sh(n)})"
            else:                                         # macro
                if not _finite(a.get("from_exposure")):
                    return None
                new["action"] = {
                    "type": "WATCH", "from_exposure": a["from_exposure"],
                    "threshold": MACRO_AFFECTED_TRIM_THRESHOLD_PCT, "covered_by": t,
                }
                new["why"] = (
                    f"Affected-sector exposure {float(a['from_exposure']):.1f}% > "
                    f"{MACRO_AFFECTED_TRIM_THRESHOLD_PCT:.0f}% trim threshold. The pre-event "
                    f"trim would fall on {t} ({_sh(n)}); {sell[0].lower() + sell[1:]}, no "
                    "fewer, so no separate pre-event trim is sized.")
                text = f"the {it.get('event') or 'macro'} pre-event trim ({_sh(n)})"
            parts.append((_win_key(it), text))
        elif it.get("_u6_kind") == "earnings" and a.get("type") == "WATCH":
            trig = _carrier_str(
                it, "_u6_trigger_trimmed_no_reentry" if weak_present else "_u6_trigger_trimmed")
            if trig is None:
                return None
            new["action"] = {"type": "WATCH", "covered_by": t}
            new["why"] = f"{sell}; the earnings-week rule sizes no trade of its own."
            new["trigger"] = trig
        elif it.get("_u6_kind") == "weak_large" and a.get("type") == "WATCH":
            sc, w = _score_text(it), it.get("weight")
            if sc is None or not _finite(w):
                return None
            new["action"] = {"type": "WATCH", "covered_by": t}
            new["why"] = (
                f"Weight {float(w):.1f}% ≥ {LARGE_POSITION_WEIGHT_PCT:.0f}% AND score "
                f"{sc} < {WEAK_CONVICTION_SCORE}. {sell}; this check sizes no trade of its own.")
            new["trigger"] = f"Covered by today's Act Today trim of {t}."
        elif a.get("type") == "TIGHTEN_ONLY":
            tg = it.get("_u6_tighten")
            if isinstance(tg, dict) and isinstance(tg.get("why"), str):
                new["why"] = tg["why"]
            else:
                continue                                  # no carrier: leave it alone
        else:
            continue
        new_by_id[id(it)] = new

    if parts:
        parts.sort(key=lambda p: p[0][1:])                # rank, event date, event, headline
        these = "this" if m == 1 else "these"
        extra = (f"This sale also covers {_join([p[1] for p in parts])}; none of them "
                 f"sells more than {these} {_sh(m)}.")
        base = str(winner.get("why") or "").rstrip()
        wn = dict(winner)
        wn["why"] = f"{base} {extra}" if base else extra
        new_by_id[id(winner)] = wn
    if not new_by_id:
        return None
    return [new_by_id.get(id(it), it) for it in group]


def _is_act_type(item) -> bool:
    a = _action(item)
    return a is not None and a.get("type") in _ACT_REVIEW_TYPES


def one_trim_per_name(items) -> list:
    """The Review list with at most one Act trim per subject (see module doc).

    Items on subjects with no Act trim, and subjects whose merge can't be proved safe,
    come back as copies equal to the input minus the `_u6_*` carrier keys. List order
    follows the input; the decision does not depend on it."""
    src = list(items or [])
    idx_by_subject: dict[str, list[int]] = {}
    for i, it in enumerate(src):
        if isinstance(it, dict):
            s = _ticker(it)
            if s:
                idx_by_subject.setdefault(s, []).append(i)
    replaced: dict[int, dict] = {}
    for idxs in idx_by_subject.values():
        group = [src[i] for i in idxs]
        merged = _merge_subject(group)
        if merged is not None:
            for i, new in zip(idxs, merged):
                replaced[i] = new
    out: list = []
    for i, it in enumerate(src):
        if i in replaced:
            out.append(_strip_one(replaced[i]))
        elif isinstance(it, dict):
            out.append(_strip_one(it))
        else:
            out.append(it)
    return out
