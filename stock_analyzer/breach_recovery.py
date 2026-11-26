"""Gate module: what a RECOVERED stop breach restores (BriefBreachRestore Batch 1,
docs/plans/brief-breach-restore-and-fold.md section 2, spec
docs/plans/brief-breach-restore-batch1-spec.md).

THE BUG: a stop breach on T makes the Brief build skip every other per-name call on T
(Act lane: sell_signal, deterioration EXIT / TRIM, critical news, the T-attributed risk
card; Review lane: earnings-week trim, weak-large trim, deterioration Watch, news Watch).
When the live price recovers, `act_today_view` demotes the breach to "Resolved", and the
memoized Brief has nothing else on T, so a name with a deterioration EXIT or a composite
Sell would show no sell at all.

THE SHAPE: on a breach day the build runs a second, counterfactual pass with the breach
rows skipped (`daily_briefing._breach_alternate_pass`) and stores T's would-be cards under
`brief["breach_alternates"]` as `{T: {"act": [...], "review": [...]}}` (every breached T
present; empty lists = checked, nothing else). ONLY `act_today_view` reads that key, and
only for a ticker whose breach it demotes this render. The raw `act_today` / `review_list`
lists are unchanged, and so is every raw reader (`_reduce_calls`, `all_flagged_tickers`,
the `exit_signals` capture, `brief_sell_tickers`, the ledgers). Cross-name picks (macro
pre-event trim, risk-off, Grow Today, Tune-up) stay as built: a recovered name never
becomes the pre-event / risk-off pick mid-day (owner Q2).

  breach_tickers(act_today)               -> frozenset of upper-cased breach tickers
  alternates_for(breach, act_alt, review_alt) -> {T: {"act": [...], "review": [...]}}
  augment(brief, resolved)                -> {"act", "review", "unchecked", "restored"} | None
  restored_tag(item)                      -> str | None
  resolved_card_line(view, ticker)        -> str
  summary_resolved_caption(view)          -> str | None

THE FAIL DIRECTIONS: no alternates key (a legacy Brief) -> `augment` returns None and the
view is byte-identical to before. A None / malformed alternates entry -> that ticker is
reported `unchecked` (a visible line says its other checks couldn't be rebuilt) and
nothing is restored for it. A breach whose live gap is unavailable never reaches this
module (the view keeps it active). Pure: no streamlit, no I/O, the Brief and its lists are
never mutated (new lists, new outer dicts).

No `$`, `**` or share count in any produced text (🙈 Hide values masks shares, and these
strings print in raw HTML and in `st.caption`).
"""
from __future__ import annotations

import copy

from stock_analyzer import act_sell_cover
from stock_analyzer import decision_bucket

ALTERNATES_KEY = "breach_alternates"
RESTORED_KEY = "_restored_after_recovery"

# Act kinds that are NOT per-name calls hidden behind the breach: the breach itself, a
# pass-through personal commitment, the cross-name macro card and the risk-off de-risk.
_EXCLUDED_ACT_KINDS = frozenset(
    {"stop_breach", "premortem_triggered", "macro", "risk_off_derisk"})

_PLAIN_LINE = "Monitor the position; re-evaluate if price returns to or below the stop."


def _norm(t) -> str:
    return str(t).strip().upper() if t is not None else ""


def breach_tickers(act_today) -> frozenset:
    """Upper-cased tickers of the `stop_breach` items in an Act list. A non-list input
    or a non-dict item is skipped."""
    if not isinstance(act_today, list):
        return frozenset()
    out = set()
    for it in act_today:
        if not isinstance(it, dict) or it.get("kind") != "stop_breach":
            continue
        t = _norm(it.get("ticker"))
        if t:
            out.add(t)
    return frozenset(out)


def alternates_for(breach, act_alt, review_alt) -> dict:
    """{T: {"act": [...], "review": [...]}} for EVERY T in `breach`.

    act: items of `act_alt` on T whose kind is not in `_EXCLUDED_ACT_KINDS`.
    review: items of `review_alt` on T whose action (when a dict) names no
    `trim_ticker` (a macro pre-event trim is a cross-name pick and stays as built).
    Items are deep-copied (the alternate lists are discarded, but the result is
    memoized in the Brief). Order is the order the items had in the input lists."""
    out: dict = {}
    for t in sorted(_norm(b) for b in (breach or ()) if _norm(b)):
        act: list = []
        for it in (act_alt or []):
            if not isinstance(it, dict):
                continue
            if decision_bucket._ticker(it) != t:
                continue
            if it.get("kind") in _EXCLUDED_ACT_KINDS:
                continue
            act.append(copy.deepcopy(it))
        review: list = []
        for it in (review_alt or []):
            if not isinstance(it, dict):
                continue
            if decision_bucket._ticker(it) != t:
                continue
            action = it.get("action")
            if isinstance(action, dict) and action.get("trim_ticker"):
                continue
            review.append(copy.deepcopy(it))
        out[t] = {"act": act, "review": review}
    return out


def _valid_entry(entry) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("act"), list)
        and isinstance(entry.get("review"), list)
        and all(isinstance(i, dict) for i in entry["act"])
        and all(isinstance(i, dict) for i in entry["review"])
    )


def augment(brief, resolved) -> dict | None:
    """The Act / Review lists `act_today_view` re-splits after demoting recovered breaches.

    `resolved` = the set of upper-cased tickers whose breach the view demoted this render.
    None when there is nothing to do (empty `resolved`, `brief` not a dict, or the
    alternates key absent = a legacy Brief): the view is then unchanged.

    Otherwise {"act": list | None, "review": list | None, "unchecked": [sorted T],
    "restored": [sorted T]}. "act" / "review" are None when no resolved ticker has a
    valid alternates entry (the view keeps its first split and only records `unchecked`).
    The resolved breach items are removed BEFORE the re-split: otherwise
    `decision_bucket._reconcile_act` would fold a restored critical-news note onto a
    demoted breach copy that never renders. Restored copies carry RESTORED_KEY."""
    tickers = sorted({_norm(t) for t in (resolved or ()) if _norm(t)})
    if not tickers or not isinstance(brief, dict) or ALTERNATES_KEY not in brief:
        return None
    alts = brief[ALTERNATES_KEY]
    by_key: dict = {}
    if isinstance(alts, dict):
        for k, v in alts.items():
            by_key.setdefault(_norm(k), v)
    valid: dict = {}
    unchecked: list = []
    for t in tickers:
        entry = by_key.get(t)
        if _valid_entry(entry):
            valid[t] = entry
        else:
            unchecked.append(t)
    if not valid:
        return {"act": None, "review": None, "unchecked": unchecked, "restored": []}

    main_act = brief.get("act_today")
    main_review = brief.get("review_list")
    act = [
        it for it in (main_act if isinstance(main_act, list) else [])
        if not (isinstance(it, dict) and it.get("kind") == "stop_breach"
                and decision_bucket._ticker(it) in valid)
    ]
    review = list(main_review) if isinstance(main_review, list) else []
    restored: list = []
    for t in sorted(valid):
        entry = valid[t]
        for a in entry["act"]:
            act.append({**a, RESTORED_KEY: True})
        for r in entry["review"]:
            review.append({**r, RESTORED_KEY: True})
        if entry["act"] or entry["review"]:
            restored.append(t)
    return {"act": act, "review": review, "unchecked": unchecked, "restored": restored}


def restored_tag(item) -> str | None:
    """The line under a restored card, or None when the item is not a restored copy."""
    if not isinstance(item, dict) or item.get(RESTORED_KEY) is not True:
        return None
    t = decision_bucket._ticker(item)
    if not t:
        return None
    return (f"↩️ Shown because {t}'s stop breach recovered (live price back above the "
            f"stop); this call was in today's Brief behind that sell.")


def _still_sold(view, ticker) -> bool:
    return act_sell_cover.cover_for(act_sell_cover.view_sell_cover(view), ticker) is not None


def _unchecked_set(view) -> set:
    if not isinstance(view, dict):
        return set()
    u = view.get("recovery_unchecked")
    return {_norm(t) for t in u if _norm(t)} if isinstance(u, (list, tuple, set)) else set()


def resolved_card_line(view, ticker) -> str:
    """The last line of a Resolved stop-breach card on Home."""
    t = _norm(ticker)
    if isinstance(view, dict) and t:
        if _still_sold(view, t):
            return f"{t} still has its own Act Today call above; follow that, not this resolved stop."
        if t in _unchecked_set(view):
            return (f"{t}'s other checks (earnings week, low score, sell signals) couldn't be "
                    f"rebuilt after the stop recovered; open 📈 Analysis before trading {t}.")
    return _PLAIN_LINE


def summary_resolved_caption(view) -> str | None:
    """The 🧾 Summary caption for resolved stop breaches, or None when there are none."""
    if not isinstance(view, dict):
        return None
    resolved = view.get("resolved")
    if not isinstance(resolved, list) or not resolved:
        return None
    tks = sorted({_norm(r.get("ticker")) for r in resolved if isinstance(r, dict)} - {""})
    text = (f"ℹ️ {len(resolved)} stop breach(es) resolved — live price "
            f"recovered above stop: {', '.join(tks)} → see 🏠 Home for detail.")
    still = [t for t in tks if _still_sold(view, t)]
    unch = [t for t in tks if t in _unchecked_set(view) and t not in still]
    if still:
        text += (f" {', '.join(still)} still "
                 f"{'has its' if len(still) == 1 else 'have their'} own Act Today call.")
    if unch:
        text += (f" {', '.join(unch)}: other checks couldn't be rebuilt after the "
                 f"recovery; open 📈 Analysis before trading.")
    return text
