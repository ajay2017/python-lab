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
# deterioration_exit / deterioration_trim are included: their card language is
# "→ ACT: Reduce aggressively" which belongs in Act Today, not Monitoring.
# premortem_triggered (docs/plans/premortem-enforcement.md, added 2026-08-03)
# is a genuine same-day decision too — the investor's OWN stated exit
# condition has fired and they haven't acted — but it is NOT in
# _REDUCE_ACT_KINDS below: its directive is "reconsider or recommit," not a
# reduce, so it deliberately does not suppress a same-ticker "hold" card the
# way an actual reduce card does (user-confirmed Q4: shows alongside other
# cards on the same ticker, never merges/suppresses).
_ACT_KINDS = frozenset({
    "stop_breach", "sell_signal", "risk", "risk_off_derisk",
    "deterioration_exit", "deterioration_trim", "premortem_triggered",
})
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

# ── Severity split WITHIN the reduce set — the single source of truth ──────────
# EXIT = "close the position"; TRIM = "cut it back". Consumers that need a badge
# or a bucket label (summary_view.position_status_badge, bucket_act_by_type)
# import these rather than re-declaring a parallel copy: TRIM is DERIVED as the
# remainder, so a kind added to _REDUCE_ACT_KINDS lands in TRIM automatically
# instead of silently falling through to WATCH in each copy.
EXIT_KINDS = frozenset({"stop_breach", "sell_signal", "deterioration_exit"})
TRIM_KINDS = _REDUCE_ACT_KINDS - EXIT_KINDS


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
        _note = ("⚠ Also flagged on recent negative headlines — already factored "
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
    NB: `deterioration_exit`/`_trim` and review-origin trims all classify to the
    ACT bucket. A name carrying BOTH will have the deterioration item first (act_today
    is iterated before review_list); first-per-ticker wins. The gate is unaffected
    (suppression fires regardless); only the banner's displayed reason differs.
    Pure; safe on None/empty.
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


def bucket_act_by_type(act_items: list[dict] | None) -> dict:
    """Group Act Today items into EXIT / TRIM / WATCH buckets.

    Reuses the existing kind / action.type canon so chips match the cards.
    Operates on the ALREADY-SPLIT act bucket (output of split_defensive's
    "act" key), not on the raw act_today / review_list lists.

    Buckets read the module-level canon (EXIT_KINDS / TRIM_KINDS derived from
    _REDUCE_ACT_KINDS, and _ACT_REVIEW_TYPES) — no parallel copy lives here, so
    a kind added to the canon cannot silently become WATCH.

    EXIT kinds : EXIT_KINDS  (stop_breach, sell_signal, deterioration_exit)
    TRIM kinds : TRIM_KINDS  (the rest of _REDUCE_ACT_KINDS) + _ACT_REVIEW_TYPES
    WATCH      : premortem_triggered, critical_news, and anything else that
                 landed in the act bucket but is not a reduce directive

    Returns
    -------
    dict with keys:
      EXIT   : list of items
      TRIM   : list of items
      WATCH  : list of items
      counts : {"EXIT": N, "TRIM": N, "WATCH": N}
    """
    exit_items: list[dict] = []
    trim_items: list[dict] = []
    watch_items: list[dict] = []

    for item in (act_items or []):
        src = item.get("_source")
        if src == "act":
            kind = str(item.get("kind", ""))
            if kind in EXIT_KINDS:
                exit_items.append(item)
            elif kind in TRIM_KINDS:
                trim_items.append(item)
            else:
                watch_items.append(item)
        elif src == "review":
            # isinstance guard rather than `(x or {}).get(...)`: act-origin cards
            # carry a STRING `action` (see _ticker's note), and `"str" or {}`
            # evaluates to the string, so .get() on it would raise. Unreachable
            # today because this branch is review-only, but the string-action
            # shape is real and one line away.
            _act = item.get("action")
            atype = str(_act.get("type", "")) if isinstance(_act, dict) else ""
            if atype in _ACT_REVIEW_TYPES:
                trim_items.append(item)
            else:
                watch_items.append(item)
        else:
            watch_items.append(item)

    return {
        "EXIT":   exit_items,
        "TRIM":   trim_items,
        "WATCH":  watch_items,
        "counts": {
            "EXIT":  len(exit_items),
            "TRIM":  len(trim_items),
            "WATCH": len(watch_items),
        },
    }


def is_news_only_card(item: dict) -> bool:
    """True for a card that exists only because of negative headlines: the Act
    critical-news card or a Review news WATCH (docs/plans/held-news-coverage.md)."""
    return str(item.get("kind", "")) == "critical_news" or item.get("watch_kind") == "news"


def all_flagged_tickers(
    act_today: list | None,
    review_list: list | None,
    *,
    ignore_news: bool = False,
) -> set[str]:
    """Every ticker carrying ANY card in either Brief lane — not just reduce cards.

    Broader than `reduce_call_tickers` by design (2026-07-29 audit finding H6,
    originally for the risk-off de-risk exclusion set): a WATCH-type review card
    ("not an action yet") must still block a same-render contradictory "add to
    this position" pick, not just a TRIM/EXIT. Uses `_ticker()` so ticker=None /
    action.trim_ticker items (macro cards) resolve correctly. Pure; safe on
    None/empty.

    NOTE (2026-10-18, chunk 7a): the held-name ADD lane no longer uses this set
    -- it reads `add_blocking_cards` (a Review news WATCH excluded, owner N-A).
    New picks, leads and the ETF held-group check keep this broad set.

    `ignore_news=True` skips news-only cards (critical_news / news WATCH): used
    by the risk-off de-risk pool and the Tune-up filter, where a headline alone
    must not move a protective trim off its beta-ranked name (D-D / D-I). The
    default is unchanged for every other caller.
    """
    return {
        _ticker(it)
        for it in (act_today or []) + (review_list or [])
        if not (ignore_news and is_news_only_card(it))
    } - {""}


def _card_label(item: dict, source: str) -> str:
    """Short, true label for the card that pauses an add. The PREFIX names the
    Home section the card actually renders in -- derived from `classify_bucket`
    (a Review-list trim sits in "Act Today", not "Review"); the suffix is derived
    from the card's own fields. Falls back to the bare section name."""
    act = item.get("action")
    probe = {**item, "_source": source}
    if source == "review" and not isinstance(act, dict):
        # classify_bucket reads a review card's action as a dict; a malformed
        # (string) action is read as its type rather than crashing the Brief.
        probe["action"] = {"type": str(act or "")}
    try:
        bucket = classify_bucket(probe)
    except Exception:
        bucket = ""
    prefix = {"act": "Act Today", "aware": "Monitoring"}.get(bucket, "Card")
    suffix = ""
    if source == "act":
        kind = str(item.get("kind", ""))
        suffix = (
            "EXIT" if kind in EXIT_KINDS
            else "TRIM" if kind in TRIM_KINDS
            else "critical news" if kind == "critical_news"
            else "pre-mortem trigger" if kind == "premortem_triggered"
            else "macro event" if kind == "macro"
            else ""
        )
    else:
        atype = str(act.get("type", "")) if isinstance(act, dict) else ""
        head = str(item.get("headline") or "")
        if atype in _ACT_REVIEW_TYPES:
            suffix = "TRIM"
        elif atype == "TIGHTEN_ONLY":
            suffix = "tighten stop"
        elif head.startswith("earnings "):
            # "earnings in 2d (2026-10-17), weight 5.0%" -> "earnings in 2d"
            suffix = head.split(" (", 1)[0]
        elif atype == "WATCH":
            suffix = "WATCH"
    return f"{prefix} · {suffix}" if suffix else prefix


def _probe_item(item: dict, source: str) -> dict:
    """Copy of `item` tagged with `_source`, with a malformed (non-dict) review
    `action` normalised exactly as `_card_label` does, so `_is_reduce` never
    raises on it."""
    probe = {**item, "_source": source}
    act = item.get("action")
    if source == "review" and not isinstance(act, dict):
        probe["action"] = {"type": str(act or "")}
    return probe


def add_blocking_cards(
    act_today: list | None,
    review_list: list | None,
    sold_today: set | None = None,
) -> dict[str, str]:
    """`{TICKER: label}` for every held name whose add-to-winner is paused by a
    card in today's Brief (or a sale today) -- the CARD half of the add gate.

    Broader than reduce calls (any Act Today card, including critical news; any
    Review card) but a Review NEWS WATCH (`watch_kind == "news"`) does NOT pause
    an add: news is an ENTRY decision (`entry_eligibility.check_news`), and the
    add card carries a note instead (owner decision N-A Option 2, 2026-10-18).
    First card per ticker wins (Act Today before Review); a sold-today ticker
    with no card gets "sold today". Pure; safe on None/empty.
    """
    out: dict[str, str] = {}
    # A critical-news card never outranks a reduce card for the label: Home
    # folds the news card into the reduce card (`_reconcile_act`), so naming the
    # news card would point at a card that isn't on screen (chunk 7c).
    _news_labelled: set[str] = set()
    for src, items in (("act", act_today), ("review", review_list)):
        for it in (items or []):
            if src == "review" and it.get("watch_kind") == "news":
                continue
            t = _ticker(it)
            if not t:
                continue
            is_news = src == "act" and str(it.get("kind", "")) == "critical_news"
            if t not in out:
                out[t] = _card_label(it, src)
                if is_news:
                    _news_labelled.add(t)
            elif t in _news_labelled and _is_reduce(_probe_item(it, src)):
                out[t] = _card_label(it, src)
                _news_labelled.discard(t)
    for s in (sold_today or ()):
        t = str(s).upper()
        if t and t not in out:
            out[t] = "sold today"
    return out


def review_news_watch_by_ticker(review_list: list | None) -> dict[str, dict]:
    """`{TICKER: card}` for Review negative-news WATCH cards (the cards that
    `add_blocking_cards` deliberately skips), first per ticker. Pure."""
    out: dict[str, dict] = {}
    for it in (review_list or []):
        if it.get("watch_kind") == "news":
            t = _ticker(it)
            if t and t not in out:
                out[t] = it
    return out


def suppress_orphans_under_reduce_call(
    orphans: list[dict] | None, reduce_calls: dict[str, dict] | None
) -> tuple[list[dict], list[dict]]:
    """Split Orphan Conviction candidates into (actionable, suppressed).

    An orphan (high conviction, underweight -> "size up") that also carries an
    active Reduce/Exit call from today's Brief is a direct contradiction — same
    ticker-matching canon as `reduce_call_items`. Suppressed items carry the
    reduce call under "_reduce_call" so the caller can render an explanation
    (never silently filter — CLAUDE.md UI-suppression rule). Pure; safe on
    None/empty.
    """
    actionable: list[dict] = []
    suppressed: list[dict] = []
    for o in orphans or []:
        rc = (reduce_calls or {}).get(str(o.get("Ticker", "")).upper())
        if rc:
            suppressed.append({**o, "_reduce_call": rc})
        else:
            actionable.append(o)
    return actionable, suppressed
