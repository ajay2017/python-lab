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
_ACT_KINDS = frozenset({"stop_breach", "sell_signal", "risk"})
# review `action.type`s that are genuine trades (free/raise capital, reduce risk).
_ACT_REVIEW_TYPES = frozenset({"TRIM_AND_TIGHTEN", "TRIM_TO_TARGET", "PROTECTIVE_TRIM"})


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
    return {"act": act_items, "aware": aware_items}
