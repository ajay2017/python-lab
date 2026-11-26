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
  brief_sell_tickers(act_today, review) -> set[str]   (upper-cased; the key set of
      `act_sell_cover.sell_cover_summary`)
  tuneup_single_name_disposition(..., need) -> {"state": brief_owns|partial|unknown|
                                                earnings_note|show, "text"}
  rebalancer_trim_disposition(..., need)    -> same shape
  book_inputs(port_df, ticker)         -> {"weight_pct", "n_positions", "shares"}  (EW C3)
  view_sell_tickers(view)              -> set[str] | None  (None = Act Today view offline)
  reconcile_tuneup(cards, deferred, view_cover) -> {"cards", "deferred_texts"}
  risk_analysis_single_name_line(..., need) -> {"state", "tier", "text"}  (🔗 Risk Analysis)

DeferSizeBlind (docs/plans/defer-size-blind.md): a surface defers to an Act sell only
when that sell COVERS its own trim (`act_sell_cover.cover_state`); a sell that is
smaller, or whose size is unknown, leaves the card in place with a "do that sale first"
line (state `partial` / `unknown`). The `sell_cover` / `view_cover` arguments are cover
maps ({TICKER: {"exit", "shares"}}); None (or anything else) defers nothing.

`show` is the fail-toward-the-sell state: with no Brief sell on the name (or the
Brief did not run, or `reduce_calls_ok` is False) the standing card stays.
"""
from __future__ import annotations

import math

from stock_analyzer import act_sell_cover, trim_sizing
from stock_analyzer.constants import SINGLE_NAME_CEILING
from stock_analyzer.decision_bucket import EXIT_KINDS, _ACT_REVIEW_TYPES

# Act Today kinds whose `ticker` IS the name being reduced, beyond EXIT_KINDS.
# I.e. the Act reduce kinds whose ticker is the name sold (`decision_bucket.EXIT_KINDS`
# + deterioration_trim + risk_off_derisk).
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
    """Upper-cased tickers named by a Brief sell, over both lists (None = empty).
    The key set of `act_sell_cover.sell_cover_summary` (same membership as
    `brief_sell_names_ticker`)."""
    return set(act_sell_cover.sell_cover_summary(act_today, review))


def _when(days) -> str:
    return "today" if days == 0 else f"in {days}d"


def _brief_owns_text(t) -> str:
    return (f"{t} already has a sell in today's Act Today call; its single-name "
            f"ceiling card returns if {t} is still over {SINGLE_NAME_CEILING:.0f}% "
            f"after that.")


def tuneup_single_name_disposition(ticker, sell_cover, earnings_days,
                                   weight_pct, n_positions, shares, need) -> dict:
    """What the 🔧 Tune-up / 🔗 Risk Analysis single-name card does for `ticker`.

    `sell_cover` = a cover map (`act_sell_cover.sell_cover_summary`), or None = no
    deferral; `need` = the card's own trim in whole shares (0 = sizes no trade, None =
    unknown). State from `act_sell_cover.cover_state`:

    brief_owns    -> an Act sell COVERS the card: drop it, show `text`.
    partial       -> an Act sell is smaller than the card: keep the card at its full
                     size, `text` = the "do that sale first" line.
    unknown       -> an Act sell's size (or the card's) is unknown: keep the card,
                     `text` = the same kind of line.
    earnings_note -> in earnings week with NO earnings-week decision (below the
                     line): keep the card, add `text` (standing work).
    show          -> keep the card, no extra line. In the window with a decision
                     but no Brief sell (a 0-share trim, a Brief that did not run)
                     the card shows: fail toward the sell.
    """
    t = _norm(ticker)
    state = act_sell_cover.cover_state(act_sell_cover.cover_for(sell_cover, t), need)
    if state == act_sell_cover.STATE_COVERED:
        return {"state": "brief_owns", "text": _brief_owns_text(t)}
    if state in (act_sell_cover.STATE_PARTIAL, act_sell_cover.STATE_UNKNOWN):
        return {"state": state, "text": act_sell_cover.cover_note(state, t, "ceiling trim")}
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

    The key set of `act_sell_cover.view_sell_cover`: reads `view["active"]`, the
    post-demotion Act bucket, so a stop breach whose live price recovered (moved to
    `resolved`) never counts as a sell. `active` mixes both Brief lanes as shallow
    copies that keep their original shape (`kind` on an act item, `action` on a review
    item); an Act `risk` attribution item never counts. (EW C3 round 2, R3-B1.)"""
    cov = act_sell_cover.view_sell_cover(view)
    return None if cov is None else set(cov)


def _cover_map(view_cover) -> dict | None:
    """`view_cover` if it is a cover map, else None (nothing is deferred)."""
    return view_cover if isinstance(view_cover, dict) else None


def reconcile_tuneup(cards, deferred, view_cover) -> dict:
    """Settle the build-time Tune-up deferrals against the Act Today view.

    `cards` = the Brief's `portfolio_tuneup`; `deferred` = its
    `portfolio_tuneup_deferred` ({T: {"text", "card"}}); `view_cover` =
    `act_sell_cover.view_sell_cover(view)`. Returns {"cards": [...],
    "deferred_texts": {T: text}}.

    DeferSizeBlind: EVERY single-name card (kept or deferred) is re-decided against the
    view: covered -> the deferred text (a kept card that is now covered is dropped and
    gets the plain "already has a sell" text); partial / unknown -> the card (restored
    if it was deferred) as a COPY with `act_cover_note`; show -> the card as is.
    View cover None -> every deferred card is restored (fail toward showing the sell),
    no note, no caption. A restored card is appended after the existing ones, in ticker
    order. A card or payload with no `trim_shares` reads need None (unknown under a
    non-exit cover). The old `{T: str}` shape (a memoized / locked Brief from before
    round 2) has no card to restore: its text is kept only under an EXIT cover, else
    dropped, so there is never a false caption. Inputs are never mutated."""
    cover = _cover_map(view_cover)
    texts: dict[str, str] = {}
    kept: list = []
    for card in (list(cards) if isinstance(cards, (list, tuple)) else []):
        subject = card.get("subject") if isinstance(card, dict) else None
        if (cover is None or not isinstance(card, dict)
                or card.get("type") != "single_name_concentration"
                or not isinstance(subject, str) or not _norm(subject)):
            kept.append(card)
            continue
        state = act_sell_cover.cover_state(
            act_sell_cover.cover_for(cover, subject), card.get("trim_shares"))
        if state == act_sell_cover.STATE_COVERED:
            texts[_norm(subject)] = _brief_owns_text(_norm(subject))
        elif state in (act_sell_cover.STATE_PARTIAL, act_sell_cover.STATE_UNKNOWN):
            kept.append({**card, "act_cover_note":
                         act_sell_cover.cover_note(state, subject, "ceiling trim")})
        else:
            kept.append(card)
    if not isinstance(deferred, dict):
        return {"cards": kept, "deferred_texts": texts}
    restored: list = []
    for t in sorted(deferred, key=_norm):
        payload = deferred[t]
        card = payload.get("card") if isinstance(payload, dict) else None
        if not isinstance(card, dict):
            # Legacy {T: str} (or a card-less dict): text only, and only under an exit.
            entry = act_sell_cover.cover_for(cover, t)
            text = payload.get("text") if isinstance(payload, dict) else payload
            if (entry is not None and entry.get("exit") is True
                    and isinstance(text, str) and text):
                texts[t] = text
            continue
        if cover is None:
            restored.append(card)
            continue
        state = act_sell_cover.cover_state(
            act_sell_cover.cover_for(cover, t), card.get("trim_shares"))
        if state == act_sell_cover.STATE_COVERED:
            text = payload.get("text")
            if isinstance(text, str) and text:
                texts[t] = text
        elif state in (act_sell_cover.STATE_PARTIAL, act_sell_cover.STATE_UNKNOWN):
            restored.append({**card, "act_cover_note":
                             act_sell_cover.cover_note(state, t, "ceiling trim")})
        else:
            restored.append(card)
    return {"cards": kept + restored, "deferred_texts": texts}


def risk_analysis_single_name_line(ticker, sell_cover, earnings_days, weight_pct,
                                   n_positions, shares, need) -> dict:
    """The line 🔗 Risk Analysis -> 📋 Action Plan shows on a single-name card.

    -> {"state", "tier", "text"}; tier "info" / "caption" / None (nothing).
    sell_cover None    -> "unverified" (the Act Today cross-check couldn't run).
    covered            -> "brief_owns" (follow the Act Today call, no extra sell).
    partial / unknown  -> that state, tier "info": an Act sell smaller than (or of a
                          size we can't compare with) this card; the card stays.
    in window, no decision, known weight -> "earnings_note".
    else "show". `need` = the card's own trim in whole shares (`rec["trim_shares"]`).
    No `$` or `**` in any text (st.info / st.caption render markdown)."""
    t = _norm(ticker)
    if sell_cover is None:
        return {"state": "unverified", "tier": "caption",
                "text": (f"⚠️ Today's Act Today calls couldn't be checked against this card "
                         f"(🏠 Home's Act Today isn't available for the current book: not built "
                         f"yet, failed, or out of date), so it may repeat a sell Act Today "
                         f"already sizes for {t}.")}
    state = act_sell_cover.cover_state(act_sell_cover.cover_for(sell_cover, t), need)
    if state == act_sell_cover.STATE_COVERED:
        return {"state": "brief_owns", "tier": "info",
                "text": (f"{t} already has a sell in today's Act Today call (🏠 Home). "
                         f"Follow that call first; this ceiling trim is not an extra sell on top "
                         f"of it today, and applies again only if {t} is still over "
                         f"{SINGLE_NAME_CEILING:.0f}% afterwards.")}
    if state in (act_sell_cover.STATE_PARTIAL, act_sell_cover.STATE_UNKNOWN):
        return {"state": state, "tier": "info",
                "text": act_sell_cover.cover_note(state, t, "ceiling trim")}
    d = _earnings_note_or_show(t, earnings_days, weight_pct, n_positions, shares)
    if d["state"] == "earnings_note":
        return {"state": "earnings_note", "tier": "caption", "text": d["text"]}
    return {"state": "show", "tier": None, "text": None}


def rebalancer_trim_disposition(ticker, sell_cover, reduce_calls_ok, earnings_days,
                                directive, need) -> dict:
    """What a ⚖️ Rebalancer trim row does for `ticker`.

    brief_owns only when `reduce_calls_ok` is True (verified, online, fresh) AND an Act
    sell COVERS the row (`need` = the row's own whole-share trim, 0 = sizes no trade,
    None = unknown); an unverified / offline / stale `_reduce_calls` never defers (plan
    §6: fail toward showing the sell). A smaller or unknown-size sell leaves the row in
    place: state partial / unknown with the "do that sale first" line, which takes
    precedence over the earnings line.
    """
    t = _norm(ticker)
    if reduce_calls_ok is True:
        state = act_sell_cover.cover_state(act_sell_cover.cover_for(sell_cover, t), need)
        if state == act_sell_cover.STATE_COVERED:
            if isinstance(directive, str) and directive.strip():
                return {"state": "brief_owns", "text": f"Today's Act Today call: {directive}"}
            return {"state": "brief_owns", "text": "Today's Act Today call already sells this name (🏠 Home)."}
        if state in (act_sell_cover.STATE_PARTIAL, act_sell_cover.STATE_UNKNOWN):
            return {"state": state, "text": act_sell_cover.cover_note(state, t, "row")}
    d = (earnings_days or {}).get(t)
    if trim_sizing.in_earnings_window(d):
        return {"state": "earnings_note",
                "text": f"Earnings {_when(d)}: check 🏠 Home's Act Today before trading this name."}
    return {"state": "show", "text": None}
