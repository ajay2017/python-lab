"""Gate module: fold a larger same-name Review trim into the Act Today card
(BriefSkipSizeBlind Batch 2, docs/plans/brief-skip-size-blind-b1b2-spec.md).

`daily_briefing._review_list` used to skip every Review trim on a name that carried any
Act reduce card, so a SMALLER Act sell (the deterioration trim's 25% floor, or an
unsized "Reduce or exit" sell signal) silently hid a LARGER Review trim on the same name
(profit-lock, earnings-week, weak-large). Now the Review sections build those cards for
a name under a PARTIAL reduce (`PARTIAL_REDUCE_KINDS`), tag them with `FOLD_KEY`, and
this module settles them once, before the U6 merge (`brief_trim_merge`):

  fold(items, act_today, extra_candidates=None) -> (items_out, payloads)
  act_item_for(act_today, ticker)               -> the Act dict a payload belongs on
  apply_payload(act_item, payload)               -> grows that card's `min_trim`
  strip_carriers(items)                          -> drops the `_fold_*` keys
  shows_journal_context(item)                    -> bool (app.py: 📒 journal block)

A subject is a name with a tagged Review card OR an `extra_candidates` entry (the
deterioration TRIM floor `_act_today` drops under a Sell, Q7), so a Sell that sits
beside a deterioration TRIM is sized at the floor even with no other Review trim.

THE SHAPE: per subject T the winner is the candidate that sells the MOST shares (ties by
`_RANK`: profit-lock, weak-large, earnings, then the deterioration floor). When the Act
card's own count (`deterioration_trim.trim_shares`; None for the unsized `sell_signal`)
is smaller than the winner, the Act card gains `min_trim` = the winner's size and its
directive / why gain a tail (the share count stays: the card already prints "n of m
shares" and the 📒 button needs it). T's tagged Review cards then read exactly as they
did when the Review sections skipped the name: a profit-lock card becomes the stop-only
card with the under-reduce why, and a tagged earnings / weak-large trim or Watch is
dropped. So one sell card per name, selling the largest size.

Full exits (`stop_breach`, `deterioration_exit`) are never tagged: the Review sections
skip / stop-only them exactly as before. A `risk` card (its ticker is an attribution),
`critical_news` and `premortem_triggered` never make a name a subject; `act_item_for`
never picks a premortem card (the old `_first_act` could, which lost the size).

THE FAIL DIRECTIONS (every sell stays visible, the Act card is never made smaller):
no partial Act card on T, an invalid candidate count (not an int >= 1), or a tagged
lock / stop card without a usable `_u6_tighten` carrier -> T's tagged cards are left AS
BUILT beside the Act card and T gets no payload. A missing text carrier (score, label,
gap, P&L, drawdown) drops only that reason's first why sentence, never prints "nan" /
"None". A raising `fold` is caught by the caller, which strips the carriers and keeps
every card.

Pure: no streamlit, no I/O, `items` / `act_today` are never mutated by `fold` (only
`apply_payload` writes, onto the one Act dict it is handed). No `$`, `**` in any text.
"""
from __future__ import annotations

import math

from stock_analyzer import act_sell_cover
from stock_analyzer.constants import (
    DETERIORATION_TRIM_SUGGESTED_PCT,
    LARGE_POSITION_WEIGHT_PCT,
    STOP_PROFIT_LOCK_TRIM_PCT,
    WEAK_CONVICTION_SCORE,
    WEAK_LARGE_TRIM_TO_PCT,
)
from stock_analyzer.decision_bucket import _ticker
from stock_analyzer.score_text import composite_score_text
from stock_analyzer.text_fmt import share_word

FOLD_KEY = "_fold_subject"          # set by _review_list on a tagged card (value = upper ticker)
_FOLD_PREFIX = "_fold_"             # every fold carrier key starts with this; fold() strips them all
PARTIAL_REDUCE_KINDS = frozenset({"deterioration_trim", "sell_signal"})
# Tie order: U6's order, with the deterioration floor last.
_RANK = {"lock": 0, "weak_large": 1, "earnings": 2, "deterioration_floor": 3}

_PAYLOAD_KEYS = ("trim_shares", "trim_dollars", "from_weight", "target_weight", "reason_key")


def _finite(x) -> bool:
    if isinstance(x, bool):
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _sh(n) -> str:
    return f"{n} {share_word(n)}"


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _action(item) -> dict | None:
    a = item.get("action") if isinstance(item, dict) else None
    return a if isinstance(a, dict) else None


def _norm(t) -> str:
    return str(t).strip().upper() if t is not None else ""


def _strip_one(item: dict) -> dict:
    out = {k: v for k, v in item.items()
           if not (isinstance(k, str) and k.startswith(_FOLD_PREFIX))}
    a = out.get("action")
    if isinstance(a, dict):
        out["action"] = dict(a)
    return out


def strip_carriers(items) -> list:
    """`items` with every `_fold_*` key removed (copies; non-dicts pass through)."""
    return [_strip_one(it) if isinstance(it, dict) else it for it in (items or [])]


def act_item_for(act_today, ticker):
    """The FIRST Act item on `ticker` whose kind is a partial reduce, else None.

    A `premortem_triggered` / `critical_news` / `risk` card with the same ticker is never
    chosen, so the size can't land on a card that does not sell."""
    want = _norm(ticker)
    if not want:
        return None
    for a in (act_today or []):
        if (isinstance(a, dict) and a.get("kind") in PARTIAL_REDUCE_KINDS
                and _ticker(a) == want):
            return a
    return None


def _usable_tighten(item) -> dict | None:
    tg = item.get("_u6_tighten")
    if isinstance(tg, dict) and "new_stop" in tg and isinstance(tg.get("why"), str):
        return tg
    return None


def _candidate_from_item(item) -> dict | None:
    """A candidate dict for a tagged sized Review trim, None for any other tagged card."""
    a = _action(item)
    if a is None:
        return None
    t = a.get("type")
    if t == "TRIM_AND_TIGHTEN":
        w = item.get("weight")
        return {
            "reason_key": "lock", "trim_shares": a.get("trim_shares"),
            "trim_dollars": a.get("trim_dollars"),
            "from_weight": w if _finite(w) else None, "target_weight": None,
            "_gap": item.get("_fold_gap"), "_pnl": item.get("_fold_pnl"),
            "_sort": str(item.get("headline") or ""),
        }
    if t == "TRIM_TO_TARGET" and a.get("reason_key") in ("earnings", "weak_large"):
        return {
            "reason_key": a["reason_key"], "trim_shares": a.get("trim_shares"),
            "trim_dollars": a.get("trim_dollars"),
            "from_weight": a.get("from_weight"), "target_weight": a.get("target_weight"),
            "_label": item.get("_fold_label"), "_score": item.get("_u6_score"),
            "_has_score": "_u6_score" in item,
            "_sort": str(item.get("headline") or ""),
        }
    return None


def _win_key(c):
    return (-c["trim_shares"], _RANK[c["reason_key"]], c.get("_sort", ""))


def _move_text(from_w, target) -> str:
    """"12.0% → 10.0%" when both are known, else a lighter phrase (no "nan")."""
    if _finite(from_w) and _finite(target):
        return f"{float(from_w):.1f}% → {float(target):.1f}%"
    if _finite(target):
        return f"to {float(target):.1f}%"
    return ""


def _directive_tail(c: dict) -> str:
    n = c["trim_shares"]
    rk = c["reason_key"]
    if rk == "earnings":
        mv = _move_text(c.get("from_weight"), c.get("target_weight"))
        lead = f" At minimum, trim {mv}" if mv else " At minimum, trim"
        return f"{lead} before the earnings report ({_sh(n)})."
    if rk == "weak_large":
        mv = _move_text(c.get("from_weight"), WEAK_LARGE_TRIM_TO_PCT)
        return f" At minimum, trim {mv} ({_sh(n)})."
    if rk == "lock":
        return (f" At minimum, sell {STOP_PROFIT_LOCK_TRIM_PCT:.0f}% of the position "
                f"({_sh(n)}) to lock part of the gain.")
    return (f" At minimum, trim about {DETERIORATION_TRIM_SUGGESTED_PCT:.0f}% of the "
            f"position ({_sh(n)}) into the weakness.")


def _first_sentence(c: dict) -> str:
    """The winner's own why sentence, "" when a carrier it needs is missing."""
    rk = c["reason_key"]
    if rk == "weak_large":
        if (not c.get("_has_score") or not _finite(c.get("from_weight"))
                or composite_score_text(c.get("_score"), WEAK_CONVICTION_SCORE) == "n/a"):
            return ""
        return (f" Also your largest weak holding: weight {float(c['from_weight']):.1f}% ≥ "
                f"{LARGE_POSITION_WEIGHT_PCT:.0f}% with score "
                f"{composite_score_text(c['_score'], WEAK_CONVICTION_SCORE)} < "
                f"{WEAK_CONVICTION_SCORE}.")
    if rk == "earnings":
        label = c.get("_label")
        if (not isinstance(label, str) or not label.strip()
                or not _finite(c.get("from_weight")) or not _finite(c.get("target_weight"))):
            return ""
        return (f" Earnings {label}: the earnings-week rule trims "
                f"{float(c['from_weight']):.1f}% → {float(c['target_weight']):.1f}% "
                "before the report.")
    if rk == "lock":
        if not _finite(c.get("_gap")) or not _finite(c.get("_pnl")):
            return ""
        return (f" The stop is {float(c['_gap']):.1f}% away with P&L "
                f"{float(c['_pnl']):+.1f}%, so part of the gain is locked first.")
    dd = c.get("_fold_dd")
    if not _finite(dd):
        return ""
    return (f" It is also deteriorating: down {float(dd):.1f}% from its peak and below "
            "its trend, so the deterioration rule's trim floor applies.")


def _cover_text(c: dict) -> str:
    n = _sh(c["trim_shares"])
    rk = c["reason_key"]
    if rk == "lock":
        return f"the profit-lock trim of {STOP_PROFIT_LOCK_TRIM_PCT:.0f}% of the position ({n})"
    if rk == "weak_large":
        tg = c.get("target_weight")
        tg = tg if _finite(tg) else WEAK_LARGE_TRIM_TO_PCT
        return f"the low-score trim to {float(tg):.1f}% ({n})"
    if rk == "earnings":
        tg = c.get("target_weight")
        if _finite(tg):
            return f"the earnings-week trim to {float(tg):.1f}% ({n})"
        return f"the earnings-week trim ({n})"
    return f"the deterioration trim floor ({n})"


def _why_tail(winner: dict, losers: list[dict]) -> str:
    out = _first_sentence(winner)
    if losers:
        n = winner["trim_shares"]
        these = "this" if n == 1 else "these"
        ordered = sorted(losers, key=_win_key_rank)
        out += (f" This also covers {_join([_cover_text(c) for c in ordered])}; none of "
                f"them sells more than {these} {_sh(n)}.")
    return out


def _win_key_rank(c):
    return (_RANK[c["reason_key"]], c.get("_sort", ""))


def _settle_subject(group: list, extra: list, act_item: dict | None):
    """(payload | None,) for one subject, or None to leave the subject as built
    (fail-safe: see the module doc). A payload of None = the Act card already sells at
    least the largest candidate (or there is no candidate)."""
    if act_item is None:
        return None
    cands: list[dict] = []
    for it in group:
        a = _action(it)
        if a is None:
            continue
        if a.get("type") in ("TRIM_AND_TIGHTEN", "TIGHTEN_ONLY") and _usable_tighten(it) is None:
            return None                                   # a stop card with no carrier
        c = _candidate_from_item(it)
        if c is not None:
            cands.append(c)
    for e in extra or []:
        if not isinstance(e, dict) or e.get("reason_key") != "deterioration_floor":
            return None
        c = dict(e)
        c.setdefault("_sort", "")
        c.setdefault("target_weight", None)
        cands.append(c)
    shares = []
    for c in cands:
        n = act_sell_cover.valid_shares(c.get("trim_shares"))
        if n is None:
            return None                                   # invalid count: show every sell
        shares.append(n)
    for c, n in zip(cands, shares):
        c["trim_shares"] = n
    n_act = None
    if act_item.get("kind") == "deterioration_trim":
        n_act = act_sell_cover.valid_shares(act_item.get("trim_shares"))
    payload = None
    if cands:
        winner = min(cands, key=_win_key)
        m = winner["trim_shares"]
        if n_act is None or n_act < m:
            losers = [c for c in cands if c is not winner]
            payload = {k: winner.get(k) for k in _PAYLOAD_KEYS}
            payload["directive_tail"] = _directive_tail(winner)
            payload["why_tail"] = _why_tail(winner, losers)
    return (payload,)


def fold(items, act_today, extra_candidates=None) -> tuple[list, dict]:
    """(items_out, payloads). `payloads` = {T: min_trim dict} for each subject whose Act
    card must grow. See the module doc. Inputs are never mutated; the decision does not
    depend on the order of `items`."""
    src = list(items or [])
    extra_candidates = extra_candidates if isinstance(extra_candidates, dict) else {}
    extra_by_t = {_norm(k): v for k, v in extra_candidates.items() if _norm(k)}
    by_subject: dict[str, list[int]] = {t: [] for t in extra_by_t}
    for i, it in enumerate(src):
        if isinstance(it, dict):
            s = _norm(it.get(FOLD_KEY))
            if s:
                by_subject.setdefault(s, []).append(i)
    payloads: dict = {}
    dropped: set = set()
    replaced: dict[int, dict] = {}
    for t in sorted(by_subject):
        idxs = by_subject[t]
        group = [src[i] for i in idxs]
        extra = extra_by_t.get(t)
        extra = extra if extra is not None else []
        res = _settle_subject(group, list(extra), act_item_for(act_today, t))
        if res is None:
            continue                                      # fail-safe: left as built
        payload = res[0]
        if payload is not None:
            payloads[t] = payload
        for i in idxs:
            it = src[i]
            a = _action(it)
            typ = a.get("type") if a is not None else None
            if typ == "TRIM_AND_TIGHTEN":
                tg = _usable_tighten(it)
                new = dict(it)
                new["action"] = {"type": "TIGHTEN_ONLY", "new_stop": tg["new_stop"]}
                new["why"] = tg["why"]
                replaced[i] = new
            elif typ == "TIGHTEN_ONLY":
                tg = _usable_tighten(it)
                new = dict(it)
                new["why"] = tg["why"]
                replaced[i] = new
            else:                                         # earnings / weak-large trim or Watch
                dropped.add(i)
    out: list = []
    for i, it in enumerate(src):
        if i in dropped:
            continue
        if i in replaced:
            out.append(_strip_one(replaced[i]))
        elif isinstance(it, dict):
            out.append(_strip_one(it))
        else:
            out.append(it)
    return out, payloads


def apply_payload(act_item, payload) -> bool:
    """Grow `act_item`'s `min_trim` (+ directive / why / reason) from one `fold` payload.

    Every text is built before the dict is touched, so a bad payload changes nothing.
    Returns True when applied."""
    if not isinstance(act_item, dict) or not isinstance(payload, dict):
        return False
    try:
        mt = {k: payload.get(k) for k in _PAYLOAD_KEYS}
        directive = (act_item.get("directive") or "") + str(payload.get("directive_tail") or "")
        why = (act_item.get("why") or "") + str(payload.get("why_tail") or "")
        reason = f"{directive} {why}".strip()
    except Exception:  # noqa: BLE001 — never half-apply
        return False
    act_item["min_trim"] = mt
    act_item["directive"] = directive
    act_item["why"] = why
    # `reason` was synthesized in _consolidate_act_today before this; refresh it so its
    # readers (quick_research, premarket tooltip) see the minimum trim too.
    act_item["reason"] = reason
    return True


def shows_journal_context(item) -> bool:
    """True when the 📒 journal-context block belongs under an Act card: its `min_trim`
    came from the weak-large rule. A `min_trim` with no `reason_key` (a legacy memoized
    Brief) reads as weak-large, as it always did; any other reason shows no block."""
    if not isinstance(item, dict):
        return False
    mt = item.get("min_trim")
    if not mt or not isinstance(mt, dict):
        return False
    rk = mt.get("reason_key")
    return rk is None or rk == "weak_large"
