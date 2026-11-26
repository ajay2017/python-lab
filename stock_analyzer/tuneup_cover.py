"""Gate module: settles the 🔧 Tune-up beta / Sharpe trim cards against the live Act Today
sell sizes (BriefSkipSizeBlind B-C3, docs/plans/brief-skip-size-blind-b3-spec.md).

The beta card ("Sell 50% of X: portfolio beta drops ...") and the Sharpe card (the drag
names, "trim 40-50%" under conditions) are the two Tune-up types that name a concrete
TRIM target, so they can restate a sale Act Today already makes (both rank the high-beta
names). They used to be filtered at BUILD time against any non-news card on the name,
silently and whatever its size (a Watch, or a 1-share Act sell, hid "Sell 50% of X"),
and a stop breach that later recovered kept the card hidden. They now pass the build
intact (`daily_briefing._portfolio_tuneup`) and are settled here, on Home, against the
Act Today VIEW's cover map (`act_sell_cover.view_sell_cover`), the same pattern as
`held_trim_owner.reconcile_tuneup` for the single-name card. The volatility / drawdown /
tail cards recommend ADDING diversifiers, a different action, and are never touched here.
Pure (no streamlit, no I/O; inputs are never mutated).

  reconcile_trim_drivers(cards, view_cover) -> {"cards": [...], "notes": [str, ...]}

THE SHAPE: `view_cover` = `act_sell_cover.view_sell_cover(view)` ({TICKER: {"exit",
"shares"}}), or None when the view is offline. The Exit set E = names whose cover entry
has `exit` True (the whole position goes).
  - beta card: its chips lose every name in E. Its SUBJECT is the name its text sells
    (`beta_levers.trim.ticker`, not `root_tickers[0]`); against the subject's cover entry
    and the card's own whole-share `trim_shares` (`act_sell_cover.cover_state`): covered
    -> the card is dropped with a note; partial / unknown -> the card is kept with an
    `act_cover_note`; show -> kept. No subject -> kept (chips only).
  - Sharpe card: its chips lose every name in E; dropped with a note only when it had
    chips and none are left. A non-exit (trim) sell never removes a chip: the Sharpe
    card says "trim 40-50%" under conditions, so it is not a sized restatement of it.
  - every other card passes through unchanged, in place.

THE FAIL DIRECTIONS (a card is never hidden on a guess): a view cover of None returns
every card unchanged with no notes; an unknown size (the sell's or the card's) never
fully covers, so the card stays with a "do that sale first" line; `trim_shares` 0 (a
sub-share position, the card sizes no trade) is covered by any sell of the name; a
malformed card passes through untouched. The card's own size is NOT reduced by the Act
sale (one freshness per number).

No `$`, `**` or share count in any produced text (🙈 Hide values masks shares).
"""
from __future__ import annotations

from stock_analyzer import act_sell_cover

_BETA = "beta"
_SHARPE = "sharpe"


# The beta card asks for a fixed 50% of the name, so after the Act sale it is not
# "re-sized" (that ending fits a trim to a ceiling): it is re-checked against the new
# book's beta, and only shows again if beta is still high (Opus review 2026-11-26).
_BETA_AFTER = ("once that sale is logged, the beta card is re-checked against your new "
               "book's beta, so it is not a second sale on top of it.")


def _norm(t) -> str:
    return str(t).strip().upper() if t is not None else ""


def _exit_set(view_cover: dict) -> set:
    return {_norm(k) for k, v in view_cover.items()
            if isinstance(v, dict) and v.get("exit") is True and _norm(k)}


def _chips(card: dict, exits: set) -> list:
    raw = card.get("tickers")
    names = list(raw) if isinstance(raw, (list, tuple)) else []
    return [t for t in names if _norm(t) not in exits]


def _beta_covered_note(subject: str) -> str:
    return (f"🔧 {subject} already has a sell in today's Act Today call at least as large "
            f"as the beta card's trim; the beta card returns if portfolio beta is still "
            f"high after that sale.")


def reconcile_trim_drivers(cards, view_cover) -> dict:
    """Settle the Tune-up beta / Sharpe cards against the Act Today view's sell cover.

    Returns {"cards": [...], "notes": [str, ...]}; see the module doc for the rules.
    `notes` are visible reasons for a dropped card (Home renders each, never a silent
    drop). Inputs are never mutated: a changed card is a copy."""
    card_list = list(cards) if isinstance(cards, (list, tuple)) else []
    if not isinstance(view_cover, dict):
        return {"cards": card_list, "notes": []}           # view offline: fail toward showing
    exits = _exit_set(view_cover)
    out: list = []
    notes: list[str] = []
    for card in card_list:
        ctype = card.get("type") if isinstance(card, dict) else None
        if ctype not in (_BETA, _SHARPE):
            out.append(card)
            continue
        chips = _chips(card, exits)
        if ctype == _SHARPE:
            raw = card.get("tickers")
            had = [t for t in (raw if isinstance(raw, (list, tuple)) else []) if _norm(t)]
            if had and not chips:
                names = ", ".join(sorted({_norm(t) for t in had}))
                notes.append(
                    f"🔧 The Sharpe card's drag names ({names}) all have an exit in "
                    f"today's Act Today call; it returns if they are still held after that.")
                continue
            out.append({**card, "tickers": chips})
            continue
        subject = card.get("subject")
        if isinstance(subject, str) and _norm(subject):
            s = _norm(subject)
            state = act_sell_cover.cover_state(
                act_sell_cover.cover_for(view_cover, s), card.get("trim_shares"))
            if state == act_sell_cover.STATE_COVERED:
                notes.append(_beta_covered_note(s))
                continue
            if state in (act_sell_cover.STATE_PARTIAL, act_sell_cover.STATE_UNKNOWN):
                out.append({**card, "tickers": chips,
                            "act_cover_note": act_sell_cover.cover_note(
                                state, s, "beta trim", tail=_BETA_AFTER)})
                continue
        out.append({**card, "tickers": chips})
    return {"cards": out, "notes": notes}
