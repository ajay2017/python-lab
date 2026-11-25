"""Gate module: when 📡 Signals / 🔔 Catalyst Watch say a held name has an Act Today
sell (U6b C1, docs/plans/signals-catalyst-act-trim-mention-u6b.md §2.1).

The decision: a standing page may name "this ticker has a sell in today's Act Today
call" only when Home's Act Today is verified for TODAY'S book, and it never quotes a
size (🏠 Home owns the number). Display only: no page's own sizing changes. Pure (no
streamlit, no I/O; inputs are never mutated) so app.py stays render-only.

  verified_sells(...)        -> set[str] | None
  unverified_caption(surface[, ticker]) -> str
  card_tag(ticker, sells)    -> str                (an expander-header suffix)
  signals_card_line(...)     -> str | None
  catalyst_line(...)         -> str | None
  signals_headline(...)      -> {"head": dict, "line": str | None}

THE None-vs-empty-set CONTRACT (the offline-sentinel rule):
  None       = COULDN'T CHECK (Home not built, the Brief failed, the book changed since
               the build, the build is from an earlier ET day, or the view is offline).
               Nothing is mentioned and the caller shows `unverified_caption`.
  set()      = CHECKED, Act Today sells nothing. Only then may 📡 Signals keep its green
               all-clear headline.
  {"SNOW"}   = checked and firing.
Every helper treats a None `sells` as "no mention" and never as an empty set.

The sell set is `held_trim_owner.view_sell_tickers` (never re-derived here): the one
notion of "Act Today sells T" the Tune-up, Risk Analysis and Rebalancer already defer
to. "Today" is the ET build date: `built_date` (the synth memo's `date`, stamped with
`_today_et()`) must equal `today` (also ET); the containers run UTC, so no clock is read
in this module.

No `$`, `**`, `<` or `>` in any produced text (rendered through st.info / st.caption and
markdown expander labels).
"""
from __future__ import annotations

from datetime import date, datetime

from stock_analyzer import coord_freshness, held_trim_owner

# 🔔 Catalyst Watch playbook action -> the word the card line uses. MONITOR is shown as
# "Watch" (Consistency #2). The playbook's internal action value is never changed.
CATALYST_ACTION_LABELS = {
    "EXIT": "Exit",
    "REDUCE": "Reduce",
    "MONITOR": "Watch",
    "HOLD": "Hold",
    "HOLD_OR_ADD": "Hold or add",
}
# An action we have no word for reads as this (never the raw internal value).
_GENERIC_ACTION_LABEL = "earnings"

# Playbook actions that are themselves a sell: the card gets the same line as a Signals trim.
_CATALYST_SELL_ACTIONS = frozenset({"EXIT", "REDUCE"})

_TAG = " · 🏠 Act Today sell"

_EARLIER_DAY = "out of date, or built on an earlier day"
_SIGNALS_CATALYST_CAPTION = (
    "⚠️ This page couldn't be checked against today's Act Today calls "
    f"(🏠 Home's Act Today isn't available for the current book: not built yet, failed, "
    f"{_EARLIER_DAY}), so a card here may sit beside an Act Today sell on the same name."
)
# The 🔗 Risk Analysis (EW C3) and ⚖️ Rebalancer (EW C4) captions as they read at HEAD,
# with the date clause added inside the parenthesis (U6b-5 (a)).
_RISK_CAPTION = (
    "⚠️ Today's Act Today calls couldn't be checked against this card "
    f"(🏠 Home's Act Today isn't available for the current book: not built yet, failed, "
    f"{_EARLIER_DAY}), so it may repeat a sell Act Today already sizes for {{who}}."
)
_REBALANCER_CAPTION = (
    "⚠️ Trim rows below aren't checked against today's Act Today calls "
    f"(🏠 Home's Act Today isn't available for the current book: not built yet, failed, "
    f"{_EARLIER_DAY}), so a trim here may repeat a sell Act Today already sizes."
)


def _norm(t) -> str:
    return str(t).strip().upper() if t is not None else ""


def _as_set(sells) -> set[str] | None:
    """An upper-cased copy of `sells`, or None (None / not a collection = couldn't check)."""
    if sells is None or isinstance(sells, (str, bytes, dict)):
        return None
    try:
        return {n for n in (_norm(x) for x in sells) if n}
    except TypeError:
        return None


def verified_sells(*, reduce_state, session_brief_offline, freshness, built_date, today,
                   view) -> set[str] | None:
    """The set of upper-cased tickers Act Today sells today, or None = couldn't check.

    None unless ALL hold: `reduce_state == "ready"` (`_coord_cache_state("_reduce_calls")`);
    `session_brief_offline` is falsy (`_daily_brief_offline`); `freshness` is
    `coord_freshness.FRESH` (the book hasn't changed since the Brief); `built_date` (the
    ET date Home's build was stored for) equals `today` (ET); and the Act Today `view`
    is online (`held_trim_owner.view_sell_tickers` is not None). An empty set means
    "checked, Act Today sells nothing"."""
    if reduce_state != "ready" or session_brief_offline:
        return None
    if freshness != coord_freshness.FRESH:
        return None
    if isinstance(today, datetime):
        today = today.date()
    if not isinstance(today, date) or built_date is None:
        return None
    if str(built_date).strip() != today.isoformat():
        return None
    return held_trim_owner.view_sell_tickers(view)


def unverified_caption(surface, ticker=None) -> str:
    """The caption shown INSTEAD of a mention when `verified_sells` is None.

    surface "signals" / "catalyst" (the new pages), "risk" (🔗 Risk Analysis single-name
    card; `ticker` names the card's ticker, "that name" when omitted) and "rebalancer"
    (⚖️ trim rows). An unknown surface gets the Signals / Catalyst text (never raises)."""
    if surface == "risk":
        who = _norm(ticker) or "that name"
        return _RISK_CAPTION.format(who=who)
    if surface == "rebalancer":
        return _REBALANCER_CAPTION
    return _SIGNALS_CATALYST_CAPTION


def card_tag(ticker, sells) -> str:
    """" · 🏠 Act Today sell" when `ticker` is in `sells`, else "" (also for None)."""
    s = _as_set(sells)
    return _TAG if s is not None and _norm(ticker) in s else ""


def _in(ticker, sells) -> bool:
    s = _as_set(sells)
    return s is not None and bool(_norm(ticker)) and _norm(ticker) in s


def _sell_text(t) -> str:
    return (f"🏠 {t} has a sell in today's Act Today call (see 🏠 Home for the size). "
            f"Follow that call first; once it is logged this card is re-sized from your "
            f"new book, so it is not a second sale on top of it.")


def signals_card_line(ticker, card_type, sells) -> str | None:
    """The st.info line under a 📡 Signals card for a name Act Today sells, else None.

    trim / review -> the "follow that call first, not a second sale" text; add -> "not a
    reason to add today"; any other card type -> a neutral "follow that call first"."""
    if not _in(ticker, sells):
        return None
    if card_type in ("trim", "review"):
        return _sell_text(ticker)
    if card_type == "add":
        return (f"🏠 {ticker} has a sell in today's Act Today call (see 🏠 Home). Follow "
                f"that call first; this card is not a reason to add to {ticker} today.")
    return (f"🏠 {ticker} has a sell in today's Act Today call (see 🏠 Home). Follow that "
            f"call first.")


def catalyst_line(ticker, playbook_action, sells) -> str | None:
    """The st.info line under a 🔔 Catalyst Watch card for a name Act Today sells, else None.

    EXIT / REDUCE -> the same text as a Signals trim. Any other action -> "Do that first;
    this card's {label} plan applies to any shares you keep" (label per
    `CATALYST_ACTION_LABELS`; an unmapped action reads "earnings")."""
    if not _in(ticker, sells):
        return None
    if playbook_action in _CATALYST_SELL_ACTIONS:
        return _sell_text(ticker)
    label = CATALYST_ACTION_LABELS.get(playbook_action, _GENERIC_ACTION_LABEL) \
        if isinstance(playbook_action, str) else _GENERIC_ACTION_LABEL
    # HOLD_OR_ADD: the same "not a reason to add" clause 📡 Signals gives an add card
    # on a sold name, so the two pages agree (Opus review 2026-11-25).
    tail = (f", and it is not a reason to add to {ticker} today"
            if playbook_action == "HOLD_OR_ADD" else "")
    return (f"🏠 {ticker} has a sell in today's Act Today call (see 🏠 Home for the size). "
            f"Do that first; this card's {label} plan applies to any shares you keep{tail}.")


def _join(names) -> str:
    n = sorted(names)
    if len(n) <= 1:
        return "".join(n)
    return ", ".join(n[:-1]) + " and " + n[-1]


def signals_headline(head, sells, card_tickers, breach_named) -> dict:
    """Fold the Act Today sells into the 📡 Signals headline -> {"head", "line"}.

    `head` = `signals_headline.decide(...)` (a dict with kind / text / note). `sells` =
    `verified_sells(...)`: None -> the head is returned unchanged (the very same object)
    and `line` is None (the caller shows `unverified_caption("signals")`).
    `breach_named` = tickers the headline / alerts already name with the Brief's sell
    clause; they are excluded so a stop breach isn't announced twice.

    kind `clear` with a named sold name -> a NEW head of kind `act_elsewhere` (blue
    info; the green all-clear never shows on a day Act Today sells a name), `line` None.
    A `clear` head with an empty `sells` set comes back unchanged (the same object).
    Every other kind -> the head unchanged (the same object), `line` = the sold names
    this page has no card for, or None when there are none."""
    s = _as_set(sells)
    if s is None:
        return {"head": head, "line": None}
    named = s - {n for n in (_norm(x) for x in (breach_named or ())) if n}
    kind = head.get("kind") if isinstance(head, dict) else None
    if kind == "clear":
        if not named:
            return {"head": head, "line": None}
        verb = "has" if len(named) == 1 else "have"
        return {"head": {"kind": "act_elsewhere", "note": None,
                         "text": (f"ℹ️ No signal-driven trades on this page, but {_join(named)} "
                                  f"{verb} a sell in today's Act Today call (🏠 Home).")},
                "line": None}
    have_card = {n for n in (_norm(x) for x in (card_tickers or ())) if n}
    rest = named - have_card
    if not rest:
        return {"head": head, "line": None}
    verb = "has" if len(rest) == 1 else "have"
    pron = "it" if len(rest) == 1 else "them"
    return {"head": head,
            "line": (f"🏠 Also in today's Act Today call: {_join(rest)} {verb} a sell (see "
                     f"🏠 Home); this page has no card of its own for {pron}.")}
