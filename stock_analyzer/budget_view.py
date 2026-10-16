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

import pandas as pd

from stock_analyzer import act_today_view, daily_budget, hold_reasons
from stock_analyzer.constants import DAILY_NEW_POSITION_BUDGET_PCT

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
            # Every Grow Today candidate (pick or add) this render sized, so the
            # Watchlist / Analysis can apply the one-share-count-per-ticker rule.
            # None = no Grow Today list was available (offline stub / no brief):
            # "unknown", never "no picks" ([] is the checked-and-empty answer).
            # An offline-stub brief carries `grow_today: {}` (a dict) but was
            # never built -- that is UNKNOWN, not "checked, no picks".
            "grow_tickers": (None if (not isinstance(grow, dict)
                                      or act_today_view.is_offline_brief(brief))
                             else sorted({c["ticker"] for c in cands})),
            "grow_state": ("offline" if act_today_view.is_offline_brief(brief)
                           else ("ok" if isinstance(grow, dict) else "none")),
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


def status_line_text(cache, mask=False, list_label="this list") -> "str | None":
    """Panel status line, or None when the budget couldn't be checked / is
    called (the per-card notes carry the reason). `mask` hides the dollar
    amounts (privacy mode); percentages stay. `list_label` names what the
    "listed" amount covers on that surface."""
    if not isinstance(cache, dict) or cache.get("state") != "ok":
        return None
    line = hold_reasons.budget_status_line(
        cache.get("pct_used"), cache.get("used_logged"),
        cache.get("used_allocated"), cache.get("used_list"), cache.get("left"),
        list_label=list_label)
    return _USD_RE.sub(_MASK, line) if mask else line


def over_line_text(cache) -> "str | None":
    """'Today's new buys are X% ... above the daily budget' when logged buys
    alone exceed it; else None."""
    if not isinstance(cache, dict) or cache.get("state") != "ok":
        return None
    pct = cache.get("over_line_pct")
    return hold_reasons.budget_over_line(pct) if pct is not None else None


# ---------------------------------------------------------------------------
# Watchlist / Analysis (chunk 5): sized from what is LEFT after the emailed
# allocations and today's Grow Today picks + adds (plan decision 4).
# ---------------------------------------------------------------------------
GROW_TODAY_SIZED_NOTE = "Sized in Grow Today today — see 🏠 Home"
GROW_TODAY_MIRROR_NOTE = "Same size as today's Grow Today card — see 🏠 Home"
HOME_NOT_LOADED_WHY = "today's Grow Today list isn't loaded — open 🏠 Home first"
GROW_OFFLINE_WHY = "today's Grow Today list couldn't be built"
WL_MIRROR_NOTE = "Same size as on 📋 Watchlist"
WL_MISSING_NOTE = ("Doesn't yet count 📋 Watchlist's Ready-to-Enter names — open "
                   "📋 Watchlist to include them.")
WL_REQUIRED_WHY = "📋 Watchlist's Ready-to-Enter names aren't counted yet — open 📋 Watchlist first"

# Analysis without a fresh Watchlist plan: False (owner default (a)) = show
# Analysis's own size with WL_MISSING_NOTE; True (b) = withhold it instead.
# One switch -- tests pin both settings.
ANALYSIS_WITHHOLD_WITHOUT_WATCHLIST = False

_SIZED_STATUSES = (daily_budget.SIZED, daily_budget.BUDGET_PARTIAL)


def _iso(d):
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def grow_list_known(cache, today) -> bool:
    """True only when Home published a budget cache DESCRIBING TODAY that
    carries a Grow Today ticker list. A cache from an earlier ET day, a None
    (couldn't check / Home not built), or one built without a brief is "we do
    not know today's Grow Today list" -- never trusted, never read as "no picks"."""
    return (isinstance(cache, dict) and cache.get("day") == _iso(today)
            and isinstance(cache.get("grow_tickers"), list)
            and isinstance(cache.get("per_ticker"), dict))


def scale_ps(ps, new_shares) -> dict:
    """Copy of a `risk.position_sizing` dict re-expressed at `new_shares`
    (shares, total_cost, actual_risk and the % fields scale together; the
    per-share figures and the cap flags are untouched). Input never mutated."""
    out = dict(ps) if isinstance(ps, dict) else {}
    old, new = _num(out.get("shares")), _num(new_shares)
    if not old or old <= 0 or new is None or new <= 0 or new == old:
        return out
    ratio = new / old
    out["shares"] = int(new)
    for key, nd in (("total_cost", 2), ("actual_risk", 2), ("portfolio_pct", 1),
                    ("capital_pct", 1), ("risk_pct_actual", 2)):
        v = _num(out.get(key))
        if v is not None:
            out[key] = round(v * ratio, nd)
    return out


def _ps_result(status, ps=None, note=None):
    return {"status": status, "ps": ps, "note": note}


def _exhausted_note(called):
    return hold_reasons.budget_note(
        hold_reasons.BUDGET_EXHAUSTED, {"is_pick": False, "called": bool(called)})


def _unknown_note(why):
    return hold_reasons.budget_note(hold_reasons.BUDGET_UNKNOWN, {"why": why})


def _from_entry(ps, entry, called):
    """Result for one non-Grow-Today ask from its `allocate` per_ticker entry."""
    entry = entry if isinstance(entry, dict) else {}
    status = entry.get("status")
    if status is None:  # no budget verdict at all: never the full F-255 size
        return _ps_result(daily_budget.BUDGET_UNKNOWN, None,
                          _unknown_note("no budget result for this ticker"))
    if status in (daily_budget.PASSTHROUGH, daily_budget.SIZED):
        return _ps_result(status, dict(ps))
    if status == daily_budget.BUDGET_UNKNOWN:
        return _ps_result(status, None, _unknown_note(entry.get("reason")))
    if status == daily_budget.BUDGET_EXHAUSTED:
        return _ps_result(status, None, _exhausted_note(called))
    sh = int(entry.get("shares") or 0)
    if sh < 1:  # defensive: never show a zero-share "size"
        return _ps_result(daily_budget.BUDGET_EXHAUSTED, None, _exhausted_note(called))
    out = scale_ps(ps, sh)
    if status == daily_budget.BUDGET_PARTIAL:
        f255 = int(ps["shares"])
        out["budget_capped"] = True
        out["budget_uncapped_shares"] = f255
        return _ps_result(status, out, hold_reasons.budget_note(
            hold_reasons.BUDGET_PARTIAL, {"n": sh, "m": f255}))
    out["budget_reserved"] = True
    return _ps_result(status, out, hold_reasons.budget_note(hold_reasons.BUDGET_RESERVED))


def _gt_result(ps, entry, called, mirror):
    """Result for an ask whose ticker is one of today's Grow Today candidates
    (one share count per ticker per day). Watchlist (`mirror` False) shows no
    count; Analysis (`mirror` True) shows Grow Today's own size, capped by its
    own F-255 size."""
    if not mirror:
        return _ps_result("grow_today", None, GROW_TODAY_SIZED_NOTE)
    entry = entry if isinstance(entry, dict) else {}
    status = entry.get("status")
    gt_sh = _num(entry.get("shares"))
    if status in (daily_budget.SIZED, daily_budget.BUDGET_PARTIAL,
                  daily_budget.BUDGET_RESERVED) and gt_sh and gt_sh >= 1:
        shown = min(int(gt_sh), int(ps["shares"]))
        out = scale_ps(ps, shown)
        note = (GROW_TODAY_MIRROR_NOTE if shown == int(gt_sh) else
                f"Grow Today's card shows {int(gt_sh)}; this page's own sizing "
                f"allows {shown}")
        return _ps_result("grow_today_mirror", out, note)
    if status == daily_budget.BUDGET_EXHAUSTED:
        return _ps_result(status, None, _exhausted_note(called))
    if status == daily_budget.BUDGET_UNKNOWN:
        return _ps_result(status, None, _unknown_note(entry.get("reason")))
    return _ps_result(daily_budget.PASSTHROUGH, dict(ps))


def _wl_result(ps, wl_entry, called):
    """Analysis ask whose ticker the Watchlist already sized today: show the
    Watchlist's own count (one share count per ticker per day), capped by this
    page's own F-255 size."""
    status = (wl_entry or {}).get("status")
    wl_sh = _num((wl_entry or {}).get("shares"))
    if status in (daily_budget.SIZED, daily_budget.BUDGET_PARTIAL,
                  daily_budget.BUDGET_RESERVED) and wl_sh and wl_sh >= 1:
        shown = min(int(wl_sh), int(ps["shares"]))
        out = scale_ps(ps, shown)
        note = (WL_MIRROR_NOTE if shown == int(wl_sh) else
                f"Watchlist shows {int(wl_sh)}; this page's own sizing allows {shown}")
        return _ps_result("watchlist_mirror", out, note)
    if status == daily_budget.BUDGET_EXHAUSTED:
        return _ps_result(status, None, _exhausted_note(called))
    if status == daily_budget.BUDGET_UNKNOWN:
        return _ps_result(status, None, _unknown_note("the Watchlist couldn't size it"))
    # Not reachable via _plan (it only routes the statuses above here); if it is
    # ever reached, withhold -- never hand back the unbudgeted F-255 size.
    return _ps_result(daily_budget.BUDGET_UNKNOWN, None,
                      _unknown_note("the Watchlist's result couldn't be read"))


# Watchlist statuses Analysis may mirror / honour. Anything else (passthrough,
# a Watchlist label, a missing status) is budgeted normally -- never copied.
_WL_MIRROR_STATUSES = (daily_budget.SIZED, daily_budget.BUDGET_PARTIAL,
                       daily_budget.BUDGET_RESERVED, daily_budget.BUDGET_EXHAUSTED,
                       daily_budget.BUDGET_UNKNOWN)


def wl_plan_cache(asks, today, epoch) -> "dict | None":
    """The value the Watchlist publishes as `_wl_budget_plan_cache`:
    {"day", "epoch", "asks": [{"ticker", "composite", "price", "ps"}]} -- the
    Watchlist's ENTER_NOW ASKS (its F-255 sizings as it passed them), NOT its
    results. 📈 Analysis re-runs the same joint plan over these asks against ITS
    OWN current basis / trades / allocations / Home cache, so the budget part is
    always current. The asks' own F-255 sizes and prices are the ones the
    Watchlist last rendered: if the portfolio value has moved since, the
    Watchlist's next render can differ until it is revisited.
    None when the page never built its asks; an empty list is a real "checked,
    nothing"."""
    if not isinstance(asks, list):
        return None
    out = []
    for a in asks:
        if not isinstance(a, dict):
            continue
        tk = str(a.get("ticker") or "").strip().upper()
        if not tk:
            continue
        ps = a.get("ps")
        out.append({"ticker": tk, "composite": a.get("composite"),
                    "price": a.get("price"),
                    "ps": dict(ps) if isinstance(ps, dict) else None})
    return {"day": _iso(today), "epoch": epoch, "asks": out}


def home_needed(plan) -> bool:
    """True when a plan's sizes are withheld only because 🏠 Home hasn't
    published today's Grow Today list (the caller then offers a Home button)."""
    return (isinstance(plan, dict) and plan.get("state") == "unknown"
            and plan.get("reason") == HOME_NOT_LOADED_WHY)


def needs_watchlist(plan) -> bool:
    """True when an Analysis plan carries the "doesn't yet count 📋 Watchlist"
    caveat (or withheld a size for that reason) -- the caller then offers a
    Watchlist button."""
    if not isinstance(plan, dict):
        return False
    res = plan.get("results")
    for r in (res.values() if isinstance(res, dict) else ()):
        if r.get("caveat") or WL_REQUIRED_WHY in str(r.get("note") or ""):
            return True
    return False


def wl_plan_known(wl_cache, today, epoch) -> bool:
    """The Watchlist's published asks count only when they describe TODAY and
    the CURRENT book generation (asks are a function of the book; a trade bumps
    the epoch) and are well formed. Nothing else is checked on purpose: the
    asks carry no sizes of their own, so the basis, trades, allocations and
    Home cache are all taken fresh when Analysis re-runs the plan."""
    return (isinstance(wl_cache, dict) and wl_cache.get("day") == _iso(today)
            and wl_cache.get("epoch") == epoch
            and isinstance(wl_cache.get("asks"), list)
            and all(isinstance(a, dict) and a.get("ticker")
                    for a in wl_cache["asks"]))


def _norm_asks(asks):
    """{TICKER: ask}; the first ask for a ticker wins."""
    out = {}
    for a in (asks if isinstance(asks, list) else []):
        if not isinstance(a, dict):
            continue
        tk = str(a.get("ticker") or "").strip().upper()
        if tk and tk not in out:
            out[tk] = a
    return out


def _is_sized_ps(ps):
    return isinstance(ps, dict) and "shares" in ps and (_num(ps.get("shares")) or 0) >= 1


def plan_downstream(inputs, trades_df, allocs, cache, today, now, asks,
                    mirror_gt=False, independent=False, need_wl=False,
                    wl_cache=None, epoch=None) -> dict:
    """Size Watchlist / Analysis asks from what is LEFT of today's budget.

    Order (plan decision 4): emailed allocations (locked) -> Grow Today picks +
    adds (taken from Home's published `cache`, only when it describes TODAY)
    -> these asks, composite-first. NEVER writes and consumes nothing for any
    other surface; `independent=True` evaluates each ask alone against the same
    leftover (Analysis: a ticker you merely looked at must not eat the room of
    another). `inputs` = `budget_inputs(...)`; `trades_df` / `allocs` the STRICT
    reads (None = unreadable). Each ask: {"ticker", "composite", "price", "ps"}
    with `ps` the F-255 `position_sizing` dict (None = no F-255 size).

    Returns {"state": "ok"|"called"|"unknown", "reason", "summary": a
    status-line-compatible dict (None when unknown), "results": {TICKER:
    {"status", "ps", "note"}}}. `ps` is the budget-adjusted sizing, or None when
    the size is withheld (the `note` says why -- the BUY call itself is never
    touched). Never raises: a crash withholds every size with a visible reason.

    `need_wl=True` (Analysis) makes the Watchlist's published plan `wl_cache`
    part of the order: its sized/partial dollars are charged after Grow Today's,
    and a ticker it sized MIRRORS the Watchlist's count. When `wl_cache` is not
    today's / this epoch's, each size carries `caveat` (WL_MISSING_NOTE), or is
    withheld when ANALYSIS_WITHHOLD_WITHOUT_WATCHLIST is True."""
    asks_n = _norm_asks(asks)
    if independent:
        results = {}
        for a in asks_n.values():
            sub = plan_downstream(inputs, trades_df, allocs, cache, today, now,
                                  [a], mirror_gt=mirror_gt, need_wl=need_wl,
                                  wl_cache=wl_cache, epoch=epoch)
            results.update(sub["results"])
        base = plan_downstream(inputs, trades_df, allocs, cache, today, now, [],
                               mirror_gt=mirror_gt, need_wl=need_wl,
                               wl_cache=wl_cache, epoch=epoch)
        base["results"] = results
        return base
    try:
        return _plan(inputs, trades_df, allocs, cache, today, now, asks_n,
                     mirror_gt, need_wl, wl_cache, epoch)
    except Exception:
        res = {}
        for tk, a in asks_n.items():
            res[tk] = (_ps_result(daily_budget.BUDGET_UNKNOWN, None, _unknown_note(_WHY_CRASH))
                       if _is_sized_ps(a.get("ps"))
                       else _ps_result(daily_budget.PASSTHROUGH, a.get("ps")))
        return {"state": "unknown", "reason": _WHY_CRASH, "summary": None,
                "results": res}


def _plan(inputs, trades_df, allocs, cache, today, now, asks_n, mirror_gt,
          need_wl=False, wl_cache=None, epoch=None):
    bi = inputs if isinstance(inputs, dict) else {}
    logged = daily_budget.buys_on_et_day(trades_df, today)
    basis = daily_budget.budget_basis(
        bi.get("gross_book"), bi.get("net_capital"),
        bi.get("basis_state") or "unknown", bi.get("cash_balance"),
        bi.get("cash_as_of"), trades_df, today, now)
    known = grow_list_known(cache, today)
    gt_set = {str(t).upper() for t in cache["grow_tickers"]} if known else set()
    per_gt = ({str(k).upper(): v for k, v in cache["per_ticker"].items()}
              if known else {})
    called_gt = bool(known and cache.get("state") == "called")
    wl_known = bool(need_wl and wl_plan_known(wl_cache, today, epoch))
    per_wl: dict = {}
    if wl_known:
        # Decision-4 order: re-run the Watchlist's own plan, with the SAME call
        # shape the Watchlist page uses (joint, not mirrored), against THIS
        # call's basis / trades / allocations / Home cache -- never a stored
        # result. Its sized/partial dollars are charged below, ahead of Analysis.
        wl_res = _plan(inputs, trades_df, allocs, cache, today, now,
                       _norm_asks(wl_cache["asks"]), False)
        if wl_res["state"] != "unknown":
            for wtk, r in wl_res["results"].items():
                wps = r.get("ps")
                per_wl[wtk] = {
                    "status": r.get("status"),
                    "shares": wps.get("shares") if isinstance(wps, dict) else None,
                    "dollars": wps.get("total_cost") if isinstance(wps, dict) else None,
                }
    # Real emailed allocation + no logged buy: its size is the locked one, even
    # for a Grow Today ticker (the email may have landed after Home rendered).
    real_alloc = {str(k).upper() for k in (allocs or {})}
    logged_keys = {str(k).upper() for k in (logged or {})}

    results: dict = {}
    cands: list = []
    for tk, a in asks_n.items():
        ps = a.get("ps")
        if not _is_sized_ps(ps):
            results[tk] = _ps_result(daily_budget.PASSTHROUGH, ps)
        elif (known and tk in gt_set
              and not (mirror_gt and tk in real_alloc and tk not in logged_keys)):
            results[tk] = _gt_result(ps, per_gt.get(tk), called_gt, mirror_gt)
        elif (wl_known and tk in per_wl
              and (per_wl[tk] or {}).get("status") in _WL_MIRROR_STATUSES
              and not (tk in real_alloc and tk not in logged_keys)):
            results[tk] = _wl_result(ps, per_wl[tk], called_gt)
        else:
            cands.append({"ticker": tk, "composite": a.get("composite"),
                          "momentum": None, "is_etf": False,
                          "price": a.get("price"), "sizing": ps})

    # Without today's Grow Today list the leftover is unknowable (its picks may
    # already have used the room). Only a checkable, un-called basis reaches
    # this -- an unknown/called basis keeps its own, more fundamental reason.
    if basis["state"] == "ok" and not known:
        offline = (isinstance(cache, dict) and cache.get("day") == _iso(today)
                   and cache.get("grow_state") == "offline")
        why = GROW_OFFLINE_WHY if offline else HOME_NOT_LOADED_WHY
        for c in cands:
            results[c["ticker"]] = _ps_result(
                daily_budget.BUDGET_UNKNOWN, None, _unknown_note(why))
        return {"state": "unknown", "reason": why, "summary": None,
                "results": results}

    # Grow Today's own non-locked sizes consume room ahead of these asks. They
    # enter `allocate` as pseudo-allocations (locked), unless a logged buy or a
    # real emailed allocation already accounts for the ticker (a logged buy
    # replaces its allocation; never double-counted).
    allocs_aug = None
    pseudo_total = 0.0
    if allocs is not None:
        allocs_aug = dict(allocs)
        have = {str(k).upper() for k in allocs_aug}
        done = {str(k).upper() for k in (logged or {})}
        for tk, e in per_gt.items():
            if (isinstance(e, dict) and e.get("status") in _SIZED_STATUSES
                    and tk not in have and tk not in done):
                usd, sh = _num(e.get("dollars")), _num(e.get("shares"))
                if usd and usd > 0 and sh and sh >= 1:
                    allocs_aug[tk] = {"shares": sh, "dollars": usd}
                    pseudo_total += usd
        # The Watchlist's own sized/partial dollars come next in the order
        # (Analysis only): charged like Grow Today's, never double-counted.
        have = {str(k).upper() for k in allocs_aug}
        for tk, e in per_wl.items():
            if (isinstance(e, dict) and e.get("status") in _SIZED_STATUSES
                    and tk not in have and tk not in done):
                usd, sh = _num(e.get("dollars")), _num(e.get("shares"))
                if usd and usd > 0 and sh and sh >= 1:
                    allocs_aug[tk] = {"shares": sh, "dollars": usd}
                    pseudo_total += usd
    res = daily_budget.allocate(basis, logged, allocs_aug, cands)
    called = res["state"] == "called"
    for c in cands:
        tk = c["ticker"]
        results[tk] = _from_entry(c["sizing"], res["per_ticker"].get(tk), called)
        if need_wl and not wl_known and results[tk]["ps"] is not None:
            if ANALYSIS_WITHHOLD_WITHOUT_WATCHLIST:
                results[tk] = _ps_result(daily_budget.BUDGET_UNKNOWN, None,
                                         _unknown_note(WL_REQUIRED_WHY))
            else:
                results[tk]["caveat"] = WL_MISSING_NOTE
    summary = {
        "state": res["state"],
        "pct_used": res.get("pct_used"),
        "used_logged": res.get("used_logged"),
        "used_allocated": max(0.0, (res.get("used_allocated") or 0.0) - pseudo_total),
        "used_list": (res.get("used_list") or 0.0) + pseudo_total,
        "left": res.get("left"),
        "budget": res.get("budget"),
        "over_line_pct": _over_line_pct(res, basis),
    }
    if res["state"] == "unknown":
        summary = None
    # `wl_counted`: the Watchlist's asks were re-run and charged here, so the
    # status line's "list" figure includes Watchlist dollars (label it so).
    return {"state": res["state"], "reason": res.get("reason"), "summary": summary,
            "results": results, "wl_counted": bool(wl_known and per_wl)}


def grow_sector_tickers(grow) -> "dict | None":
    """{sector: {TICKER}} over a Daily Brief's Grow Today picks + adds (the same
    population `_grow_today_sectors_cache` is built from), for the Watchlist's
    sector soft-caution same-ticker exclusion. None when there is no Grow Today
    dict (unknown -- the caution then keeps firing)."""
    if not isinstance(grow, dict):
        return None
    out: dict = {}
    for key in ("new_picks", "add_positions"):
        lst = grow.get(key)
        for it in (lst if isinstance(lst, list) else []):
            if not isinstance(it, dict):
                continue
            sec = str(it.get("sector") or "").strip()
            tk = str(it.get("ticker") or "").strip().upper()
            if sec and tk:
                out.setdefault(sec, set()).add(tk)
    return out


def _et_date_of(ts):
    """ET calendar date of a timestamp (naive = UTC), or None if unreadable."""
    try:
        t = pd.to_datetime(ts, utc=True)
        return None if pd.isna(t) else t.tz_convert("America/New_York").date()
    except Exception:
        return None


def buy_preview(inputs, trades_df, pending_dollars, today, now,
                pending_traded_at=None) -> dict:
    """Trade Journal BUY-confirm line: today's logged BUYs PLUS the buy being
    confirmed, as a % of the lag-corrected net-capital basis. Awareness only --
    never blocks, never sizes. {"state": "ok", "pct", "budget_pct", "over",
    "text"} or {"state": "unknown", "text"}; never raises. A buy back-dated to
    another ET day (`pending_traded_at`) is not one of TODAY's new buys:
    {"state": "not_today", "text": None} (the caller says nothing)."""
    why = None
    if pending_traded_at is not None and _et_date_of(pending_traded_at) != today:
        return {"state": "not_today", "text": None}
    try:
        bi = inputs if isinstance(inputs, dict) else {}
        pend = _num(pending_dollars)
        logged = daily_budget.buys_on_et_day(trades_df, today)
        basis = daily_budget.budget_basis(
            bi.get("gross_book"), bi.get("net_capital"),
            bi.get("basis_state") or "unknown", bi.get("cash_balance"),
            bi.get("cash_as_of"), trades_df, today, now)
        b = _num(basis.get("basis"))
        if pend is None or pend < 0:
            why = "this entry's cost couldn't be read"
        elif logged is None:
            why = "the trade log couldn't be read"
        elif basis.get("state") == "called":
            why = daily_budget.CALLED_REASON
        elif basis.get("state") != "ok" or not b or b <= 0:
            why = basis.get("reason") or "net capital couldn't be determined"
        else:
            used = sum(float(v) for v in logged.values()) + pend
            pct = used / b * 100.0
            over = pct > DAILY_NEW_POSITION_BUDGET_PCT + 1e-9
            text = (f"Today's new buys including this one: {pct:.1f}% of net "
                    f"capital (daily budget {DAILY_NEW_POSITION_BUDGET_PCT:g}%)"
                    + (" — above the budget; nothing further is sized today."
                       if over else "."))
            return {"state": "ok", "pct": pct,
                    "budget_pct": DAILY_NEW_POSITION_BUDGET_PCT,
                    "over": over, "text": text}
    except Exception:
        why = _WHY_CRASH
    return {"state": "unknown",
            "text": f"Daily new-buy budget couldn't be checked ({why}) — "
                    "this entry is not blocked."}


def watchlist_asks(recs, held, sold_today, portfolio_value, sizing_fn) -> list:
    """The Watchlist's budget asks: ONLY `action == "ENTER_NOW"` cards that
    aren't held / sold today. ENTRY_HELD (a deferred or held name) and every
    other action never ask -- a held name can't take budget room.

    `sizing_fn(price, stop)` returns the F-255 `position_sizing` dict (the app
    supplies it); any exception, a non-positive price/stop gap or a missing
    portfolio value leaves `ps` None, exactly as the page did inline."""
    asks = []
    held = held or ()
    sold_today = sold_today or ()
    for r in (recs or []):
        if (r.get("action") != "ENTER_NOW" or r.get("ticker") in held
                or r.get("ticker") in sold_today):
            continue   # same rows the per-ticker loop replaces with a warning
        ps = None
        if (r.get("price") and r.get("stop") and r["price"] > r["stop"]
                and (portfolio_value or 0) > 0):
            try:
                ps = sizing_fn(r["price"], r["stop"])
            except Exception:
                pass
        asks.append({"ticker": r["ticker"], "composite": r.get("score"),
                     "price": r.get("price"), "ps": ps})
    return asks
