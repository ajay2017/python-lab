"""
Decision bucketing — split the Brief's defensive items into "Act Today"
(a genuine trade decision the user should make today) vs "Monitoring /
Awareness" (FYI; nothing to execute). This is the calm-advisor layer: it keeps
the urgent list to real decisions so the app reads like an advisor, not a
churning watchlist (§2B persona).

Pure logic — no Streamlit / no I/O. The renderer keys off the added `_source`
field ("act"|"review") to pick the right card template, while the bucket
("act"|"aware") decides which section the item appears in. The two are
independent: e.g. a review-origin TRIM_TO_TARGET renders as a review card but
sits in the Act bucket; an act-origin macro item renders as an act card but
sits in Awareness.
"""

from stock_analyzer.constants import (
    BUCKET_TIGHTEN_ONLY_IS_ACT,
    BUCKET_CRITICAL_NEWS_IS_ACT,
)

# act_today `kind`s that are genuine same-day trade decisions.
_ACT_KINDS = frozenset({"stop_breach", "sell_signal", "risk", "risk_off_derisk"})
# review `action.type`s that are genuine trades (free/raise capital, reduce risk).
_ACT_REVIEW_TYPES = frozenset({"TRIM_AND_TIGHTEN", "TRIM_TO_TARGET", "PROTECTIVE_TRIM"})

# Act-lane items whose directive is to REDUCE a position (trim / exit / sell /
# stop-out). When one of these is live for a ticker, a same-ticker "hold /
# monitor" critical-news card is contradictory clutter ("hold for now" next to
# "trim 23%→8%" — the SPCX split-brain): the actionable reduce wins and the
# headline folds into it. Reduce cards from BOTH streams count (act-origin
# stop/sell/risk/deterioration + review-origin trim variants).
_REDUCE_ACT_KINDS = frozenset(
    {"stop_breach", "sell_signal", "risk", "deterioration_exit", "deterioration_trim",
     "risk_off_derisk"}
)


def _ticker(item: dict) -> str:
    # Fall back to action.trim_ticker: a macro PROTECTIVE_TRIM card carries
    # ticker=None with its real subject in action.trim_ticker (matches the
    # producer-side macro dedup convention), so the reconciler can still match
    # it against a same-ticker news card. NB: act-origin cards (critical_news,
    # macro) carry a STRING `action`, not a dict — guard with isinstance so the
    # fallback never does .get() on a string.
    t = item.get("ticker")
    if t:
        return str(t).upper()
    act = item.get("action")
    if isinstance(act, dict):
        return str(act.get("trim_ticker") or "").upper()
    return ""


def _is_reduce(item: dict) -> bool:
    """True when the item's directive is to reduce the position."""
    if item.get("_source") == "act":
        return str(item.get("kind", "")) in _REDUCE_ACT_KINDS
    if item.get("_source") == "review":
        return str((item.get("action") or {}).get("type", "")) in _ACT_REVIEW_TYPES
    return False


def _reconcile_act(items: list[dict]) -> list[dict]:
    """Collapse the contradictory hold-vs-reduce split-brain in the Act bucket.

    The Act lane is fed by two un-cross-deduped streams (act_today +
    review_list); a ticker can therefore land BOTH an actionable reduce (trim /
    exit / sell / stop) AND a softer "MONITOR — Critical News: hold for now"
    card. They contradict. Rule: if a ticker has any reduce card, drop that
    ticker's critical-news card and fold a one-line flag into the reduce card's
    `why` (the headline detail stays one Analyze-click away). Tickers with a
    news card but NO reduce card are untouched (a winner with a single headline
    keeps its monitor card). Genuinely distinct, compatible cards are preserved —
    only the hold↔reduce contradiction is collapsed.
    """
    reduce_tickers = {_ticker(it) for it in items if _is_reduce(it) and _ticker(it)}
    out: list[dict] = []
    folded: set = set()
    for it in items:
        t = _ticker(it)
        if str(it.get("kind", "")) == "critical_news" and t in reduce_tickers:
            folded.add(t)          # drop the contradictory "hold" card
            continue
        out.append(it)
    # Annotate the (first) reduce card per folded ticker so the news isn't lost.
    if folded:
        _note = ("⚠ Also flagged on a negative headline today — already factored "
                 "into this reduce; it isn't a separate 'hold'.")
        for it in out:
            t = _ticker(it)
            if t in folded and _is_reduce(it):
                it["why"] = f"{it.get('why', '').strip()}  {_note}".strip()
                folded.discard(t)
    return out


def classify_bucket(item: dict) -> str:
    """Return "act" or "aware" for one defensive item.

    Reads `_source` ("act" = from act_today, "review" = from review_list) plus
    `kind` / `action.type`. Borderlines (critical_news, TIGHTEN_ONLY) are
    governed by the constants flags. Anything unrecognised → "aware" (fail to
    the calm bucket; never invent an Act).
    """
    src = item.get("_source")
    if src == "act":
        kind = str(item.get("kind", ""))
        if kind in _ACT_KINDS:
            return "act"
        if kind == "critical_news":
            return "act" if BUCKET_CRITICAL_NEWS_IS_ACT else "aware"
        return "aware"  # macro and anything else
    if src == "review":
        atype = str((item.get("action") or {}).get("type", ""))
        if atype in _ACT_REVIEW_TYPES:
            return "act"
        if atype == "TIGHTEN_ONLY":
            return "act" if BUCKET_TIGHTEN_ONLY_IS_ACT else "aware"
        return "aware"  # WATCH and anything else
    return "aware"


def split_defensive(act_today: list | None, review_list: list | None) -> dict:
    """Split act_today + review_list into {"act": [...], "aware": [...]}.

    Each returned item is a shallow copy of the original with `_source` added
    (so every original field — directive/why/trigger/risk_flags/action/headline
    and the data the Analyze / Mark-Done buttons need — is preserved). Order is
    act_today items first, then review items, within each bucket (matching the
    pre-split top-to-bottom reading order).
    """
    act_items: list[dict] = []
    aware_items: list[dict] = []
    for it in (act_today or []):
        x = {**it, "_source": "act"}
        (act_items if classify_bucket(x) == "act" else aware_items).append(x)
    for it in (review_list or []):
        x = {**it, "_source": "review"}
        (act_items if classify_bucket(x) == "act" else aware_items).append(x)
    # Cross-stream reconciliation: a ticker being reduced shouldn't also show a
    # contradictory "hold/monitor" news card in the same lane (the SPCX case).
    act_items = _reconcile_act(act_items)
    return {"act": act_items, "aware": aware_items}


def reduce_call_items(act_today: list | None, review_list: list | None) -> dict[str, dict]:
    """Map ticker → its reduce-call item (directive / why / tier preserved),
    across BOTH Brief lanes.

    Same canon as `reduce_call_tickers` (which returns just the keys), for a
    consumer that needs the REASON — e.g. the Analysis page's "under a Reduce/
    Exit call" reconciliation banner reads the item's `action`/`why`. First
    reduce item per ticker wins in split order: the `act` bucket, then `aware`.
    NB: `deterioration_exit`/`_trim` are act-ORIGIN but classify to the AWARE
    bucket (they aren't in `_ACT_KINDS`), while review-origin trims classify to
    ACT — so a name carrying BOTH keeps the review trim's item. The gate is
    unaffected (suppression fires regardless); only the banner's displayed reason
    differs. Pure; safe on None/empty.
    """
    _split = split_defensive(act_today, review_list)
    out: dict[str, dict] = {}
    for it in (_split["act"] + _split["aware"]):
        if _is_reduce(it):
            t = _ticker(it)
            if t and t not in out:
                out[t] = it
    return out


def reduce_call_tickers(act_today: list | None, review_list: list | None) -> set[str]:
    """Tickers under an active Reduce/Exit call, across BOTH Brief lanes.

    For consumers OUTSIDE the Brief (e.g. the Overview's Opportunity Signals)
    that must not surface an "add on a pullback" for a name the Brief is telling
    you to reduce. Uses the SAME `_is_reduce` / `_ticker` canon as the Act-bucket
    reconciler — act-origin reduce `kind`s (stop / sell / risk / deterioration /
    risk-off) AND review-origin trim `action.type`s — so the two surfaces can
    never drift out of agreement, and it survives split_defensive re-bucketing
    (scans both act + aware). Pure; safe on None/empty.
    """
    return set(reduce_call_items(act_today, review_list).keys())
