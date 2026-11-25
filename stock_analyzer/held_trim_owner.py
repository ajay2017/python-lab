"""Gate module: which Brief sell "names" a held ticker, and what a standing surface
says when it defers to that sell (EW C1, docs/plans/earnings-week-trim-consistency.md §3).

In earnings week a held name's size is decided once, on the highest-priority
surface (Act Today). The standing surfaces (🔧 Tune-up, 🔗 Risk Analysis, the
⚖️ Rebalancer) defer to a Brief sell that names the ticker, with a visible line;
never a silent drop. Everything here is pure (no streamlit, no I/O) so app.py stays
render-only:

  brief_sell_names_ticker(item, ticker) -> bool
      True only for an item whose directive is to SELL that very ticker: an Act
      Today item of kind `decision_bucket.EXIT_KINDS` / `deterioration_trim` /
      `risk_off_derisk`, or a Review item whose action type is in
      `decision_bucket._ACT_REVIEW_TYPES` (matched on `ticker` or
      `action.trim_ticker`). NEVER kind `risk`: that card's ticker is an
      attribution (the largest holding of an over-ceiling sector), not a name
      being sold, so keying on it would hide a real ceiling card
      (memory card_ticker_semantics). Also never `critical_news`,
      `premortem_triggered`, a macro act item, or a WATCH / HOLD review item.
  brief_sell_tickers(act_today, review) -> set[str]   (upper-cased)
  tuneup_single_name_disposition(...)  -> {"state": brief_owns|earnings_note|show, "text"}
  rebalancer_trim_disposition(...)     -> same shape
  book_inputs(port_df, ticker)         -> {"weight_pct", "n_positions", "shares"}  (EW C3)
  view_sell_tickers(view)              -> set[str] | None  (None = Act Today view offline)
  reconcile_tuneup(cards, deferred, view_sells) -> {"cards", "deferred_texts"}
  risk_analysis_single_name_line(...)  -> {"state", "tier", "text"}  (🔗 Risk Analysis)

`show` is the fail-toward-the-sell state: with no Brief sell on the name (or the
Brief did not run, or `reduce_calls_ok` is False) the standing card stays.
"""
from __future__ import annotations

import math

from stock_analyzer import trim_sizing
from stock_analyzer.constants import SINGLE_NAME_CEILING
from stock_analyzer.decision_bucket import EXIT_KINDS, _ACT_REVIEW_TYPES

# Act Today kinds whose `ticker` IS the name being reduced, beyond EXIT_KINDS.
# Mirrors daily_briefing._review_list's `_reduce_tickers` plus risk_off_derisk.
_EXTRA_SELL_KINDS = frozenset({"deterioration_trim", "risk_off_derisk"})


def _norm(t) -> str:
    return str(t).strip().upper() if t is not None else ""


def brief_sell_names_ticker(item, ticker) -> bool:
    """True when this Brief item is a sell of exactly `ticker` (see module doc)."""
    want = _norm(ticker)
    if not want or not isinstance(item, dict):
        return False
    kind = item.get("kind")
    if kind:
        if kind in EXIT_KINDS or kind in _EXTRA_SELL_KINDS:
            return _norm(item.get("ticker")) == want
        return False
    action = item.get("action")
    if isinstance(action, dict) and action.get("type") in _ACT_REVIEW_TYPES:
        return (_norm(item.get("ticker")) == want
                or _norm(action.get("trim_ticker")) == want)
    return False


def brief_sell_tickers(act_today, review) -> set[str]:
    """Upper-cased tickers named by a Brief sell, over both lists (None = empty)."""
    out: set[str] = set()
    for it in list(act_today or []) + list(review or []):
        if not isinstance(it, dict):
            continue
        kind = it.get("kind")
        if kind:
            if kind in EXIT_KINDS or kind in _EXTRA_SELL_KINDS:
                t = _norm(it.get("ticker"))
                if t and brief_sell_names_ticker(it, t):
                    out.add(t)
            continue
        action = it.get("action")
        if isinstance(action, dict) and action.get("type") in _ACT_REVIEW_TYPES:
            for t in (_norm(it.get("ticker")), _norm(action.get("trim_ticker"))):
                if t and brief_sell_names_ticker(it, t):
                    out.add(t)
    return out


def _when(days) -> str:
    return "today" if days == 0 else f"in {days}d"


def tuneup_single_name_disposition(ticker, sell_tickers, earnings_days,
                                   weight_pct, n_positions, shares) -> dict:
    """What the 🔧 Tune-up / 🔗 Risk Analysis single-name card does for `ticker`.

    brief_owns    -> a Brief sell names it: drop the card, show `text`.
    earnings_note -> in earnings week with NO earnings-week decision (below the
                     line): keep the card, add `text` (standing work).
    show          -> keep the card, no extra line. In the window with a decision
                     but no Brief sell (a 0-share trim, a Brief that did not run)
                     the card shows: fail toward the sell.
    """
    t = _norm(ticker)
    if t in {_norm(x) for x in (sell_tickers or ())}:
        return {"state": "brief_owns",
                "text": (f"{t} already has a sell in today's Act Today call; its single-name "
                         f"ceiling card returns if {t} is still over {SINGLE_NAME_CEILING:.0f}% "
                         f"after that.")}
    return _earnings_note_or_show(t, earnings_days, weight_pct, n_positions, shares)


def _earnings_note_or_show(t, earnings_days, weight_pct, n_positions, shares) -> dict:
    """The in-window / no-decision rule shared by the Tune-up and Risk Analysis.
    An unread weight (None / non-finite) is never called "under the line": it shows."""
    d = (earnings_days or {}).get(t)
    n = n_positions or 0                      # None must not raise (floor = the no-N floor)
    if (trim_sizing.in_earnings_window(d)
            and _f(weight_pct) is not None
            and trim_sizing.earnings_week_decision(weight_pct, n, shares) is None):
        floor = trim_sizing.earnings_overweight_floor(n)
        return {"state": "earnings_note",
                "text": (f"Earnings {_when(d)}: at or under the {floor:.1f}% earnings-week line, "
                         f"so the earnings-week rule sizes nothing before the report; "
                         f"this card is standing work.")}
    return {"state": "show", "text": None}


def _f(v) -> float | None:
    """Finite float or None (None / NaN / inf / non-numeric)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def book_inputs(port_df, ticker) -> dict:
    """{"weight_pct", "n_positions", "shares"} for `ticker` from the enriched book.

    Weight = the row's `Weight (%)` (None when missing / NaN), shares =
    `trim_sizing.exact_shares(row)` (None when the ticker isn't found),
    n_positions = len(port_df) (0 for a None / empty book). Ticker match is
    case-insensitive. Pure."""
    out = {"weight_pct": None, "n_positions": 0, "shares": None}
    try:
        if port_df is None or len(port_df) == 0:
            return out
        out["n_positions"] = int(len(port_df))
        want = _norm(ticker)
        if not want or "Ticker" not in port_df.columns:
            return out
        for _, row in port_df.iterrows():
            if _norm(row["Ticker"]) == want:
                out["weight_pct"] = _f(row["Weight (%)"]) if "Weight (%)" in port_df.columns else None
                out["shares"] = trim_sizing.exact_shares(row)
                break
    except Exception:
        return {"weight_pct": None, "n_positions": 0, "shares": None}
    return out


def view_sell_tickers(view) -> set[str] | None:
    """Upper-cased tickers a Brief sell names in the Act Today VIEW Home renders
    (`act_today_view.act_today_view(...)`), or None when that view could not be
    built (not a dict, or `state == "offline"`: the cross-check can't run).

    Reads `view["active"]`, the post-demotion Act bucket, so a stop breach whose
    live price recovered (moved to `resolved`) never counts as a sell. `active`
    mixes both Brief lanes as shallow copies that keep their original shape
    (`kind` on an act item, `action` on a review item), which is exactly what
    `brief_sell_names_ticker` / `brief_sell_tickers` read; an Act `risk`
    attribution item never counts. (EW C3 round 2, R3-B1.)"""
    if not isinstance(view, dict) or view.get("state") == "offline":
        return None
    active = view.get("active")
    return brief_sell_tickers(active if isinstance(active, list) else [], [])


def reconcile_tuneup(cards, deferred, view_sells) -> dict:
    """Settle the build-time Tune-up deferrals against the Act Today view.

    `cards` = the Brief's `portfolio_tuneup`; `deferred` = its
    `portfolio_tuneup_deferred` ({T: {"text", "card"}}); `view_sells` =
    `view_sell_tickers(view)`. Returns {"cards": [...], "deferred_texts": {T: text}}.

    view_sells None -> every deferred card is restored (fail toward showing the
    sell) and no caption. Otherwise a deferral stands only for a T in view_sells;
    any other card is appended back after the existing ones, in ticker order.
    The old `{T: str}` shape (a memoized / locked Brief from before round 2) has
    no card to restore: its text is kept only when T is in view_sells, else
    dropped, so there is never a false caption."""
    base = list(cards) if isinstance(cards, (list, tuple)) else []
    if not isinstance(deferred, dict):
        return {"cards": base, "deferred_texts": {}}
    live = ({_norm(x) for x in view_sells} if isinstance(view_sells, (set, frozenset, list, tuple))
            else None)
    texts: dict[str, str] = {}
    restored: list = []
    for t in sorted(deferred, key=_norm):
        payload = deferred[t]
        if live is not None and _norm(t) in live:
            text = payload.get("text") if isinstance(payload, dict) else payload
            if isinstance(text, str) and text:
                texts[t] = text
        elif isinstance(payload, dict) and isinstance(payload.get("card"), dict):
            restored.append(payload["card"])
    return {"cards": base + restored, "deferred_texts": texts}


def risk_analysis_single_name_line(ticker, sell_tickers, earnings_days, weight_pct,
                                   n_positions, shares) -> dict:
    """The line 🔗 Risk Analysis -> 📋 Action Plan shows on a single-name card.

    -> {"state", "tier", "text"}; tier "info" / "caption" / None (nothing).
    sell_tickers None  -> "unverified" (the Act Today cross-check couldn't run).
    ticker in sells    -> "brief_owns" (follow the Act Today call, no extra sell).
    in window, no decision, known weight -> "earnings_note".
    else "show". No `$` or `**` in any text (st.info / st.caption render markdown)."""
    t = _norm(ticker)
    if sell_tickers is None:
        return {"state": "unverified", "tier": "caption",
                "text": (f"⚠️ Today's Act Today calls couldn't be checked against this card "
                         f"(🏠 Home's Act Today isn't available for the current book: not built "
                         f"yet, failed, or out of date), so it may repeat a sell Act Today "
                         f"already sizes for {t}.")}
    if t in {_norm(x) for x in sell_tickers}:
        return {"state": "brief_owns", "tier": "info",
                "text": (f"{t} already has a sell in today's Act Today call (🏠 Home). "
                         f"Follow that call first; this ceiling trim is not an extra sell on top "
                         f"of it today, and applies again only if {t} is still over "
                         f"{SINGLE_NAME_CEILING:.0f}% afterwards.")}
    d = _earnings_note_or_show(t, earnings_days, weight_pct, n_positions, shares)
    if d["state"] == "earnings_note":
        return {"state": "earnings_note", "tier": "caption", "text": d["text"]}
    return {"state": "show", "tier": None, "text": None}


def rebalancer_trim_disposition(ticker, sell_tickers, reduce_calls_ok, earnings_days,
                                directive) -> dict:
    """What a ⚖️ Rebalancer trim row does for `ticker`.

    brief_owns only when `reduce_calls_ok` is True (verified, online, fresh) AND a
    Brief sell names the ticker; an unverified / offline / stale `_reduce_calls`
    never defers (plan §6: fail toward showing the sell).
    """
    t = _norm(ticker)
    if reduce_calls_ok is True and t in {_norm(x) for x in (sell_tickers or ())}:
        if isinstance(directive, str) and directive.strip():
            return {"state": "brief_owns", "text": f"Today's Act Today call: {directive}"}
        return {"state": "brief_owns", "text": "Today's Act Today call already sells this name (🏠 Home)."}
    d = (earnings_days or {}).get(t)
    if trim_sizing.in_earnings_window(d):
        return {"state": "earnings_note",
                "text": f"Earnings {_when(d)}: check 🏠 Home's Act Today before trading this name."}
    return {"state": "show", "text": None}
