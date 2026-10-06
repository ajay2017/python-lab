"""Home's "⚠️ Alerts" expander — a one-line notices summary (P1 of the Home
redesign, docs/plans/home-redesign.md's 2026-10-06 "P1 + P2 designed" section).

Pure, no Streamlit/DB/network. Records what each of the 16 existing Alerts-
expander fill sites actually rendered (never a second opinion on severity,
never a new cache read) and rolls that up into one plain-text line that can
sit above the still-collapsed expander.

Critical design property carried over from the plan doc: this module never
reads a cache itself — every entry handed to `summarize_notices` is recorded
from INSIDE the branch of a fill site that actually drew something, so a
lossy 3-state producer (e.g. `_structural_alert_cache`'s None/[]/populated)
can never be misread here — it simply isn't asked a question it would answer
wrong. Absence of entries must always read as "nothing fired," never
"nothing checked" / an all-clear — callers must never synthesize all-clear
wording when this returns `None`.
"""

from __future__ import annotations

# Declaration order of the 16 `_alert_ph_*` placeholders inside Home's
# "⚠️ Alerts" expander (app.py). Drives `named`'s tie-break ordering —
# NOT call order, NOT alphabetical.
SOURCE_ORDER = (
    "dayshock", "xcheck", "split", "structural", "drift", "thesis",
    "systrust", "heldload", "stale", "dropped", "scorewithheld", "outage",
    "leverage", "pnldq", "crossasset", "debrief",
)

SOURCE_LABELS = {
    "dayshock":      "Day Shock",
    "xcheck":        "Price cross-check",
    "split":         "Stock split",
    "structural":    "Structural alert",
    "drift":         "Broker drift",
    "thesis":        "Thesis under pressure",
    "systrust":      "System Trust",
    "heldload":      "Load failures",
    "stale":         "Cached data",
    "dropped":       "Dropped holdings",
    "scorewithheld": "Score withheld",
    "outage":        "Data outage",
    "leverage":      "Leverage",
    "pnldq":         "Today's P&L data",
    "crossasset":    "Cross-asset",
    "debrief":       "Evening Debrief",
}

# Display enum only — ranks severity for the merge/sort below. NOT a policy
# value (no decision/gate reads this), so it does not belong in constants.py.
TIERS = ("error", "warning", "info", "caption")

_TIER_RANK = {t: i for i, t in enumerate(TIERS)}
_SOURCE_POS = {s: i for i, s in enumerate(SOURCE_ORDER)}


def _tier_rank(tier: str) -> int:
    """Unrecognized tier strings count as "warning" — fail loud, never
    silently dropped (an unexpected call site is more suspicious than a
    merely-unexpected-but-high-severity one)."""
    return _TIER_RANK.get(tier, _TIER_RANK["warning"])


def _normalized_tier(tier: str) -> str:
    return tier if tier in _TIER_RANK else "warning"


def _source_pos(source: str) -> int:
    # Unrecognized source ids sort after every known one, in first-seen
    # order among themselves (stable sort preserves that) — never dropped.
    return _SOURCE_POS.get(source, len(SOURCE_ORDER))


def summarize_notices(entries: list[tuple[str, str]] | None) -> dict | None:
    """Roll up this render's `(source, tier)` entries into one summary.

    Returns `None` for empty/`None` input — callers must treat `None` as
    "nothing fired," never render all-clear wording for it (several
    underlying producers have lossy "clean" states; the absence of an entry
    here must not be confused with "verified clean").

    Returns `{"tier": <highest tier present>, "named": [...], "n_notes": n}`.
    `named` lists every error- and warning-tier source's display label,
    errors first then warnings, each group ordered by `SOURCE_ORDER`
    position — never call order, never alphabetical. `n_notes` counts the
    number of DISTINCT info/caption-tier sources not already counted in
    `named`.
    """
    if not entries:
        return None

    # Merge by source, keeping the highest tier seen for that source.
    best: dict[str, str] = {}
    for source, tier in entries:
        norm = _normalized_tier(tier)
        prev = best.get(source)
        if prev is None or _tier_rank(norm) < _tier_rank(prev):
            best[source] = norm

    if not best:
        return None

    errors   = sorted((s for s, t in best.items() if t == "error"),   key=_source_pos)
    warnings = sorted((s for s, t in best.items() if t == "warning"), key=_source_pos)
    notes    = [s for s, t in best.items() if t in ("info", "caption")]

    named = [SOURCE_LABELS.get(s, s) for s in errors] + [SOURCE_LABELS.get(s, s) for s in warnings]

    if errors:
        overall = "error"
    elif warnings:
        overall = "warning"
    else:
        overall = "info"

    return {
        "tier": overall,
        "named": named,
        "n_notes": len(notes),
    }


def notices_headline(summary: dict | None) -> str | None:
    """Plain-markdown headline text for `summary` (no `$`, no raw HTML — this
    flows through `st.error`/`st.warning`/`st.caption`, never
    `unsafe_allow_html`). Returns `None` when `summary` is `None` — callers
    must render nothing in that case, never an all-clear line."""
    if not summary:
        return None

    named    = summary.get("named", [])
    n_notes  = summary.get("n_notes", 0)

    if named:
        text = f"⚠️ Needs a look: **{', '.join(named)}**"
        if n_notes:
            text += f" · {n_notes} more note{'s' if n_notes != 1 else ''}"
        return text

    # D4: show this even on an otherwise-quiet day — several real "couldn't
    # check" disclosures are caption-tier and would otherwise be invisible.
    return f"· {n_notes} note{'s' if n_notes != 1 else ''}"
