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

`show` is the fail-toward-the-sell state: with no Brief sell on the name (or the
Brief did not run, or `reduce_calls_ok` is False) the standing card stays.
"""
from __future__ import annotations

from stock_analyzer import trim_sizing
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
                "text": f"{t}'s single-name size is handled by today's Act Today call."}
    d = (earnings_days or {}).get(t)
    n = n_positions or 0                      # None must not raise (floor = the no-N floor)
    if (trim_sizing.in_earnings_window(d)
            and trim_sizing.earnings_week_decision(weight_pct, n, shares) is None):
        floor = trim_sizing.earnings_overweight_floor(n)
        return {"state": "earnings_note",
                "text": (f"Earnings {_when(d)}: below the {floor:.1f}% earnings-week line, "
                         f"so nothing is due before the report; this is standing work.")}
    return {"state": "show", "text": None}


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
        return {"state": "brief_owns", "text": "Sized by today's Act Today call (🏠 Home)."}
    d = (earnings_days or {}).get(t)
    if trim_sizing.in_earnings_window(d):
        return {"state": "earnings_note",
                "text": f"Earnings {_when(d)}: check 🏠 Home's Act Today before trading this name."}
    return {"state": "show", "text": None}
