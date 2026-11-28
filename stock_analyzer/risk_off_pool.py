"""Gate module: ONE risk-off trim pool for 🏠 Home and the 08:00 premarket email
(brief-consolidation C2, docs/plans/brief-consolidation-spec.md section 2, row
RiskOffTrimShift).

The risk-off de-risk (`exit_advisor.assess_risk_off_derisk`) ranks the book's top beta
contributors and trims them modestly. It used to be told to skip a DIFFERENT set of
names depending on who asked: the Brief skipped every name carrying any Act / Review
card (a Watch, a smaller trim, an unsized Sell), the email skipped only stop breaches and
deterioration EXITs, and the forward simulator skipped stop-outs plus every deterioration
tier. So a Watch on the biggest beta name moved Home's trim onto a lower-beta name while
the email named the real one. This module settles that once:

  full_exit_names(items, deterioration) -> frozenset[str]
      The upper-cased names whose WHOLE position is already being sold: an item of kind
      `stop_breach` or `deterioration_exit`, plus every deterioration payload whose tier
      is EXIT (a composite-Sell name's deterioration EXIT card is skipped upstream, so
      the payload is the only trace). The one `exclude_tickers` input for Home, the
      email and the forward simulator. Nothing else is excluded: a smaller sell, a Watch,
      a critical-news hold, a premortem, the sector card's attribution ticker all leave
      the name a risk-off candidate.

  settle_brief(risk_off_cards, act, review) -> (cards_out, review_out)
      Brief only. Reconciles each kept risk-off card with the same name's other cards
      (never moves the trim to another name): another Act / Review sell on the name
      adds `act_sell_cover.beside_note` to the card's `why` (covered / partial / unknown
      against the card's own `trim_shares`); a non-news Watch on the name gains
      `action["covered_by"]` so it reads "covered by today's Act Today trim of T; no
      separate trade." (its type is kept: exit_signals reads it). A news Watch, a
      TIGHTEN_ONLY card, a premortem and the sector card are untouched.

Owner decisions 2026-11-27: Q4 (risk-off excludes only full exits, the same set as the
email) and Q5 (reverses the 2026-07-29 audit's H6: a Watch beside a risk-off trim reads
"covered by" instead of the trim moving to a lower-beta name).

Fail-safe: a card with no ticker, an unreadable `trim_shares` or a non-dict item reads
unknown / is left alone; the cards are never dropped. Pure: no streamlit, no I/O, inputs
are never mutated (copies are returned). Callers wrap `settle_brief` in a try and keep
the unsettled lists on any exception.
"""
from __future__ import annotations

from stock_analyzer import act_sell_cover
from stock_analyzer.decision_bucket import _ticker

# Item kinds that sell the whole position. Deliberately NOT `sell_signal`: a Sell is a
# "reduce or exit" call with no guaranteed size, so a risk-off trim may sit beside it.
_FULL_EXIT_KINDS = frozenset({"stop_breach", "deterioration_exit"})
# The deterioration tier that closes the position (`exit_advisor.EXIT`).
_EXIT_TIER = "EXIT"
# Review action types that are "not an action yet" and read as covered beside the trim.
_WATCH_TYPES = frozenset({"WATCH", "DETERIORATION_WATCH"})


def _norm(t) -> str:
    return str(t).strip().upper() if t is not None else ""


def full_exit_names(items, deterioration) -> frozenset:
    """Upper-cased tickers whose whole position is already being sold (see module doc).

    `items` = the Act Today items (or the email's alerts); `deterioration` = the
    deterioration payloads (dicts with `ticker` / `tier`). A non-list / tuple
    `deterioration` (None, {}) contributes no EXIT names."""
    out: set = set()
    if isinstance(items, (list, tuple)):
        for it in items:
            if isinstance(it, dict) and it.get("kind") in _FULL_EXIT_KINDS:
                t = _norm(it.get("ticker"))
                if t:
                    out.add(t)
    if isinstance(deterioration, (list, tuple)):
        for d in deterioration:
            if isinstance(d, dict) and d.get("tier") == _EXIT_TIER:
                t = _norm(d.get("ticker"))
                if t:
                    out.add(t)
    return frozenset(out)


def settle_brief(risk_off_cards, act, review):
    """(cards_out, review_out): the risk-off cards and Review list reconciled with each
    other. Returns copies; the inputs are never mutated. See module doc."""
    cards = [c for c in (risk_off_cards or []) if isinstance(c, dict)]
    review_in = list(review or [])
    if not cards:
        return [dict(c) if isinstance(c, dict) else c for c in (risk_off_cards or [])], review_in

    cover = act_sell_cover.sell_cover_summary(act, review_in)
    ro_tickers: set = set()
    cards_out = []
    for c in (risk_off_cards or []):
        if not isinstance(c, dict):
            cards_out.append(c)
            continue
        new = dict(c)
        t = _norm(c.get("ticker"))
        if t:
            ro_tickers.add(t)
            entry = act_sell_cover.cover_for(cover, t)
            state = act_sell_cover.cover_state(entry, c.get("trim_shares"))
            note = act_sell_cover.beside_note(state, t, "trim")
            if note:
                why = str(c.get("why") or "").strip()
                new["why"] = f"{why}  {note}".strip()
        cards_out.append(new)

    review_out = []
    for it in review_in:
        a = it.get("action") if isinstance(it, dict) else None
        if (isinstance(a, dict) and a.get("type") in _WATCH_TYPES
                and it.get("watch_kind") != "news"
                and not a.get("covered_by")):
            t = _ticker(it)
            if t and t in ro_tickers:
                new_it = dict(it)
                new_it["action"] = {**a, "covered_by": t}
                review_out.append(new_it)
                continue
        review_out.append(it)
    return cards_out, review_out
