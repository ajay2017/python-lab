"""In-app (Home / Grow Today) wiring of the daily new-position budget.

The DECISION lives in `daily_budget` (allocate / apply_to_sizing); this module
is the pure glue the app calls so none of it sits untested in app.py:

  * `postpass`            -- budget a Daily Brief's Grow Today picks + adds and
                             build the `_new_position_budget_cache` value.
  * `card_note`           -- which per-card note a (budget-adjusted) sizing
                             dict gets, via `hold_reasons.budget_note`.
  * `status_line_text` / `over_line_text` -- the panel-level wording.
  * `rec_budget_state`    -- the `recommendations.rec_budget_state` code a
                             budget-adjusted sizing carries (mirror of
                             cron_runner._rec_budget_state, so an app-written
                             row and a cron-written row agree).

The app only READS allocations (the emails write them). Candidate order is the
shared `daily_budget.candidate_sort_key` (emailed allocations are locked and
consume nothing new; everything else is sized from what is left, composite
first, ETFs last), so the cron and the app agree on every emailed ticker's
shares.

NEVER mutates the brief it is given: the app memoizes the raw brief in
`_home_synth_cache`, which must stay budget-free (the budget depends on today's
trades and allocations, neither of which is in that memo's signature). New
dicts are built via `apply_to_sizing`.

Never raises: a failure withholds every budget-consuming size
(status budget_unknown) and publishes a `None` cache (the offline sentinel --
"couldn't check", never `{}`).

Decision-bearing, so it is a `_GATE_FILES` member like `daily_budget`.
"""
from __future__ import annotations

import math
import re

from stock_analyzer import daily_budget, hold_reasons

# Sizing flags a budget-adjusted sizing dict can carry (the Grow Today card
# blocks must branch on every one -- tests/test_grow_sizing_note_markers.py).
BUDGET_CARD_FLAGS = ("budget_unknown", "budget_exhausted", "budget_capped",
                     "budget_reserved")

_WHY_CRASH = "the budget check failed"
_MASK = "••••••"
_USD_RE = re.compile(r"\$[\d,]+")


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def rec_budget_state(sz) -> "str | None":
    """`recommendations.rec_budget_state` code for a budget-adjusted sizing
    dict; None when the budget did not touch it (a non-budget row keeps its
    pre-budget key set). Same table as cron_runner._rec_budget_state."""
    if not isinstance(sz, dict):
        return None
    for flag, code in (("budget_unknown", hold_reasons.BUDGET_UNKNOWN),
                       ("budget_exhausted", hold_reasons.BUDGET_EXHAUSTED),
                       ("budget_capped", hold_reasons.BUDGET_PARTIAL),
                       ("budget_reserved", hold_reasons.BUDGET_RESERVED)):
        if sz.get(flag):
            return code
    return None


def budget_inputs(gross_book, net_capital, basis_state, acct_rec) -> dict:
    """The dict `daily_budget.budget_basis` is fed from -- same keys the cron's
    compute_morning_picks builds. `acct_rec` None / non-dict -> no cash record
    (-> basis unknown); a failed resolution passes basis_state "unknown"."""
    ok = isinstance(acct_rec, dict)
    return {
        "gross_book":   gross_book,
        "net_capital":  net_capital,
        "basis_state":  basis_state or "unknown",
        "cash_balance": acct_rec.get("cash_balance") if ok else None,
        "cash_as_of":   acct_rec.get("updated_at") if ok else None,
    }


def _price_of(item, kind, tk, price_map):
    p = _num(item.get("price"))
    if p is not None and p > 0:
        return p
    pm = _num((price_map or {}).get(tk))
    if pm is not None and pm > 0:
        return pm
    sz = item.get("sizing")
    if isinstance(sz, dict):
        sh, tc = _num(sz.get("shares")), _num(sz.get("total_cost"))
        if sh and tc and sh > 0 and tc > 0:
            return tc / sh
    return None


def collect_candidates(grow, price_map=None) -> list:
    """daily_budget candidate dicts for Grow Today's new picks and adds.
    A pick's composite is `composite_score` (its momentum is `score`); an
    add-to-winner's `score` IS its composite (same convention as the rec-log
    rows). Adds COUNT toward the budget (owner decision 1)."""
    out = []
    if not isinstance(grow, dict):
        return out
    for kind, key in (("pick", "new_picks"), ("add", "add_positions")):
        lst = grow.get(key)
        for it in (lst if isinstance(lst, list) else []):
            if not isinstance(it, dict):
                continue
            tk = str(it.get("ticker") or "").strip().upper()
            if not tk:
                continue
            if kind == "pick":
                comp, mom = it.get("composite_score"), it.get("score")
            else:
                comp, mom = it.get("score"), None
            out.append({
                "ticker": tk, "composite": comp, "momentum": mom,
                "is_etf": it.get("asset_type") == "etf",
                "price": _price_of(it, kind, tk, price_map),
                "sizing": it.get("sizing"),
            })
    return out


def _withhold_all(brief, why):
    """Brief copy with every real size withheld (budget_unknown); the BUY
    calls themselves are untouched."""
    if not isinstance(brief, dict) or not isinstance(brief.get("grow_today"), dict):
        return brief
    grow = brief["grow_today"]
    new_grow = dict(grow)
    for key in ("new_picks", "add_positions"):
        lst = grow.get(key)
        if not isinstance(lst, list):
            continue
        new_list = []
        for it in lst:
            sz = it.get("sizing") if isinstance(it, dict) else None
            if isinstance(sz, dict) and "shares" in sz:
                it = dict(it)
                it["sizing"] = daily_budget.apply_to_sizing(
                    sz, {"status": daily_budget.BUDGET_UNKNOWN, "reason": why})
            new_list.append(it)
        new_grow[key] = new_list
    out = dict(brief)
    out["grow_today"] = new_grow
    return out


def postpass(brief, inputs, trades_df, allocs, today, now, price_map=None) -> dict:
    """Budget `brief`'s Grow Today picks + adds. Returns
    {"brief": new brief (the input is never mutated), "cache": dict | None,
     "crashed": str | None}.

    `inputs` is `budget_inputs(...)`; `trades_df` is the STRICT trades read
    (None = unreadable) and `allocs` the strict allocations read (None =
    unreadable) -- either None withholds every budget-consuming size. `today`
    a plain ET date, `now` a tz-aware datetime. `brief` may be None / an
    offline stub (no `grow_today` dict): the budget is still computed (the
    other surfaces read it) and the brief is returned untouched.

    `cache` is None whenever the budget could not be checked (state unknown or
    a crash) -- the offline sentinel; a dict only for state "ok"/"called"."""
    try:
        bi = inputs if isinstance(inputs, dict) else {}
        logged = daily_budget.buys_on_et_day(trades_df, today)
        basis = daily_budget.budget_basis(
            bi.get("gross_book"), bi.get("net_capital"),
            bi.get("basis_state") or "unknown", bi.get("cash_balance"),
            bi.get("cash_as_of"), trades_df, today, now)
        grow = brief.get("grow_today") if isinstance(brief, dict) else None
        cands = collect_candidates(grow, price_map)
        res = daily_budget.allocate(basis, logged, allocs, cands)
        per = res["per_ticker"]

        new_brief = brief
        if isinstance(grow, dict):
            new_grow = dict(grow)
            for key in ("new_picks", "add_positions"):
                lst = grow.get(key)
                if not isinstance(lst, list):
                    continue
                new_list = []
                for it in lst:
                    sz = it.get("sizing") if isinstance(it, dict) else None
                    if isinstance(sz, dict) and sz:
                        tk = str(it.get("ticker") or "").strip().upper()
                        it = dict(it)
                        it["sizing"] = daily_budget.apply_to_sizing(sz, per.get(tk))
                    new_list.append(it)
                new_grow[key] = new_list
            new_brief = dict(brief)
            new_brief["grow_today"] = new_grow

        if res["state"] not in ("ok", "called"):
            return {"brief": new_brief, "cache": None, "crashed": None}
        cache = {
            "state": res["state"],
            "reason": res.get("reason") or basis.get("reason"),
            "basis": basis.get("basis"),
            "lag_adjustment": basis.get("lag_adjustment"),
            "budget": res.get("budget"),
            "used_logged": res.get("used_logged"),
            "used_allocated": res.get("used_allocated"),
            "used_list": res.get("used_list"),
            "left": res.get("left"),
            "pct_used": res.get("pct_used"),
            "over_budget": bool(res.get("over_budget")),
            "over_line_pct": _over_line_pct(res, basis),
            "per_ticker": per,
            "day": today.isoformat() if hasattr(today, "isoformat") else str(today),
            "computed_at": now.isoformat() if hasattr(now, "isoformat") else str(now),
        }
        return {"brief": new_brief, "cache": cache, "crashed": None}
    except Exception as exc:  # never raise into a render
        return {"brief": _withhold_all(brief, _WHY_CRASH), "cache": None,
                "crashed": f"{type(exc).__name__}: {str(exc)[:120]}"}


def _over_line_pct(res, basis):
    """Logged buys ALONE above the budget (same rule as the email path)."""
    b = _num(basis.get("basis"))
    if (res.get("state") == "ok" and b and b > 0 and res.get("budget") is not None
            and (res.get("used_logged") or 0.0) > res["budget"] + 1e-9):
        return res["used_logged"] / b * 100.0
    return None


def card_note(sz, kind, called=False) -> "str | None":
    """Note text for a (budget-adjusted) sizing dict, or None when the budget
    left it alone. `kind` is "pick" (a new-position card: an exhausted pick
    reads "One of today's picks -- not sized...") or "add" (an add-to-winner:
    plain "Not sized..."); `called` is the cache's margin-called state."""
    if not isinstance(sz, dict):
        return None
    if sz.get("budget_unknown"):
        return hold_reasons.budget_note(
            hold_reasons.BUDGET_UNKNOWN, {"why": sz.get("budget_reason")})
    if sz.get("budget_exhausted"):
        return hold_reasons.budget_note(
            hold_reasons.BUDGET_EXHAUSTED,
            {"is_pick": kind == "pick", "called": bool(called)})
    if sz.get("budget_capped"):
        return hold_reasons.budget_note(
            hold_reasons.BUDGET_PARTIAL,
            {"n": sz.get("shares"), "m": sz.get("budget_uncapped_shares")})
    if sz.get("budget_reserved"):
        return hold_reasons.budget_note(hold_reasons.BUDGET_RESERVED)
    return None


def status_line_text(cache, mask=False) -> "str | None":
    """Panel status line, or None when the budget couldn't be checked / is
    called (the per-card notes carry the reason). `mask` hides the dollar
    amounts (privacy mode); percentages stay."""
    if not isinstance(cache, dict) or cache.get("state") != "ok":
        return None
    line = hold_reasons.budget_status_line(
        cache.get("pct_used"), cache.get("used_logged"),
        cache.get("used_allocated"), cache.get("used_list"), cache.get("left"),
        list_label="this list")
    return _USD_RE.sub(_MASK, line) if mask else line


def over_line_text(cache) -> "str | None":
    """'Today's new buys are X% ... above the daily budget' when logged buys
    alone exceed it; else None."""
    if not isinstance(cache, dict) or cache.get("state") != "ok":
        return None
    pct = cache.get("over_line_pct")
    return hold_reasons.budget_over_line(pct) if pct is not None else None
