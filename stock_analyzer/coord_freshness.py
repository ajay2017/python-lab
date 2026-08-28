"""Freshness bookkeeping for the portfolio-dependent coordination caches.

WHY THIS EXISTS (F-260 Phase 1, 2026-08-28)
-------------------------------------------
`app.py`'s `_refresh_portfolio_cache_after_trade` rebuilds the portfolio keys
after a logged trade, but it cannot cheaply rebuild the caches DERIVED from the
portfolio — risk/beta, fragility, correlation, Reduce/Exit calls, composites.
Those are produced inline in the 🏠 Home branch and are network- or
compute-bound.

The damage was not that they go stale. It is that the republisher also writes
`_holdings_sig_at_home_build`, the sole input to `_portfolio_snapshot_stale()`
— so it SILENCED the app's only cross-page staleness warning on 9 pages while
those caches still described the pre-trade book. Before that function existed,
those pages warned. That is a suppression with no banner, which the house rules
forbid outright.

The obvious alternative — setting the un-refreshed caches to `None` — was
designed and REJECTED. It closes none of the known findings, because those
findings ARE the ~20 call sites that collapse the offline sentinel
(`st.session_state.get(k) or {}`): `None` and absent render identically at
every one. What it would change is the benign case, turning a mildly
out-of-date beta into a fabricated `{}`. It only becomes correct after the
consumer-side patches, which is the mass-edit this project has explicitly
ruled out.

So: stamp, don't delete. Nothing is nulled; the existing banner learns to say
which dimensions are behind.

THE MODEL
---------
* `epoch`  — an int bumped every time the book itself changes (a logged trade).
* `stamps` — {cache_key: epoch_at_which_it_was_published}.

A key is `fresh` when stamped at the current epoch, `stale` when stamped at an
older one, and `absent` when never stamped at all. **Those three must never
collapse into two** — "I checked and it is current", "I have an older answer"
and "I never had one" are different claims, and conflating the last two is the
defect class this whole feature exists to keep open.
"""
from __future__ import annotations

# Tiers drive banner SEVERITY only — never suppression, never a gate.
#   "gate"       the cache suppresses or gates a recommendation. Its staleness
#                can let through an action the app would otherwise withhold.
#   "decision"   feeds a displayed number a user reasons from (a grade, a beta).
#   "decorative" awareness-only by documented design; never gates anything.
TIER_GATE = "gate"
TIER_DECISION = "decision"
TIER_DECORATIVE = "decorative"

# The registry. Every portfolio-DEPENDENT coordination cache, with why it is
# here. Adding a key here is cheap; a portfolio-dependent key MISSING from here
# reads as permanently fresh, which is a confident false negative inside the
# mechanism built to prevent them — hence the bidirectional test in
# tests/test_coord_freshness.py and the planned check_antipatterns rule.
PORTFOLIO_DEPENDENT_KEYS: dict[str, dict] = {
    "_reduce_calls": {
        "tier": TIER_GATE,
        "why": "suppresses ADD suggestions for names under a Reduce/Exit call; "
               "stale means an add can be proposed on a name flagged to trim",
    },
    "_structural_alert_cache": {
        "tier": TIER_GATE,
        "why": "correlation-cluster formation; persists into portfolio_thesis, "
               "so a wrong value poisons next week's HELD/SHIFTED baseline",
    },
    "_port_risk_cache": {
        "tier": TIER_DECISION,
        "why": "portfolio beta; feeds fragility, the risk advisor and the "
               "persisted decision_context row captured at trade time",
    },
    "_fragility_cache": {
        "tier": TIER_DECISION,
        "why": "book fragility; a fabricated neutral drops a 25-point penalty "
               "into the 🏆 Health A-F grade shown as fact",
    },
    "_highbeta_share": {
        "tier": TIER_DECISION,
        "why": "high-beta concentration; a graded 🏆 Health dimension",
    },
    "_corr_df_cache": {
        "tier": TIER_DECISION,
        "why": "the correlation matrix itself; adding or removing one name "
               "genuinely moves it, and the diversification SCORE is derived",
    },
    "_grow_composites": {
        "tier": TIER_DECORATIVE,
        "why": "per-ticker composites for Grow Today; network-bound, and the "
               "surfaces recompute or re-rank before display",
    },
    "_leverage_cache": {
        "tier": TIER_DECORATIVE,
        "why": "margin/leverage awareness. Documented as read-only and NEVER "
               "gating (F-09d) — so a quiet surface here is a pure loss with "
               "no compensating safety benefit; disclose, never withhold",
    },
    "_acct_gate_cache": {
        "tier": TIER_GATE,
        "why": "the concentration gate's own denominator. Listed even though "
               "the post-trade republisher DOES refresh it (F-260 Phase 0), so "
               "the registry is a complete map of portfolio-dependent caches "
               "rather than only the un-refreshed ones — and so a future "
               "regression that stops refreshing it shows up as stale rather "
               "than as a silent gap in the registry",
    },
    "_div_score_cache":     {"tier": TIER_DECORATIVE, "why": "derived from _corr_df_cache"},
    "_avg_corr_cache":      {"tier": TIER_DECORATIVE, "why": "derived from _corr_df_cache"},
    "_risk_pairs_cache":    {"tier": TIER_DECORATIVE, "why": "derived from _corr_df_cache"},
    "_div_label_cache":     {"tier": TIER_DECORATIVE, "why": "derived from _corr_df_cache"},
    "_corr_coverage_cache": {"tier": TIER_DECORATIVE, "why": "describes _corr_df_cache's sample"},
}

FRESH, STALE, ABSENT = "fresh", "stale", "absent"


def classify(stamps: dict | None, epoch: int, key: str) -> str:
    """FRESH / STALE / ABSENT for one cache key.

    ABSENT must never collapse into STALE. "I never computed this" and "I have
    an older answer" license different copy: the first cannot be described as
    out of date, and telling a user their figures are stale when none were ever
    produced is its own fabrication.
    """
    if not stamps or key not in stamps:
        return ABSENT
    try:
        stamped = int(stamps[key])
    except (TypeError, ValueError):
        return ABSENT
    return FRESH if stamped >= int(epoch) else STALE


def not_fresh_keys(stamps: dict | None, epoch: int, keys=None) -> dict[str, str]:
    """{key: STALE|ABSENT} for every registered key that is not fresh.

    `keys` narrows to the caches a given page actually reads, so a page is
    never warned about a dimension it does not show.
    """
    # `is None`, NOT `keys or ...`: an EMPTY tuple legitimately means "this
    # surface reads none of these caches, stay silent", and `or` would flip
    # that into "report all 14" — the exact falsy-collapse class this feature
    # exists to close, reintroduced inside its own helper.
    candidates = PORTFOLIO_DEPENDENT_KEYS if keys is None else keys
    considered = [k for k in candidates if k in PORTFOLIO_DEPENDENT_KEYS]
    out = {}
    for k in considered:
        state = classify(stamps, epoch, k)
        if state != FRESH:
            out[k] = state
    return out


def tier_of(key: str) -> str | None:
    entry = PORTFOLIO_DEPENDENT_KEYS.get(key)
    return entry["tier"] if entry else None


def decide_stale_banner(not_fresh: dict | None) -> tuple | None:
    """(severity, message) or None. Pure — the renderer does no deciding.

    Severity is tiered rather than uniform, per an explicit user decision
    (2026-08-28): every logged trade makes these caches technically stale, but
    a 3-share add to an 18-name book does not move beta or correlation, and a
    banner that always shouts is the churn the calm-advisor rule exists to
    prevent. So only a GATE-bearing cache escalates to a warning; everything
    else is a caption. No policy constant is involved, deliberately — the
    alternative design needed a materiality threshold, which would have been a
    new investment-policy value.

    Only STALE keys are reported. ABSENT is deliberately silent here: a cache
    that was never produced this session is the normal state of a page visited
    before 🏠 Home, and the existing `_render_portfolio_not_loaded` /
    "visit Home" affordances already cover it. Warning about it would fire on
    every cold session and train the user to ignore the banner.
    """
    stale = {k: v for k, v in (not_fresh or {}).items() if v == STALE}
    if not stale:
        return None

    gate_bearing = sorted(k for k in stale if tier_of(k) == TIER_GATE)
    severity = "warn" if gate_bearing else "caption"

    dims, plural = _dimension_names(stale)
    msg = (
        "Your holdings are current. "
        f"{dims} below still {'describe' if plural else 'describes'} the book "
        "from before your last trade."
    )
    if gate_bearing:
        msg += (
            " That includes a check that can SUPPRESS a suggestion, so an "
            "action may be shown here that the app would otherwise hold back."
        )
    msg += " Revisit 🏠 Home to refresh."
    return severity, msg


_DIMENSION_LABELS = {
    "_reduce_calls": "Reduce/Exit calls",
    "_structural_alert_cache": "correlation-cluster alerts",
    "_port_risk_cache": "portfolio risk and beta",
    "_fragility_cache": "book fragility",
    "_highbeta_share": "high-beta share",
    "_corr_df_cache": "correlation",
    "_grow_composites": "composite scores",
    "_leverage_cache": "leverage",
    "_div_score_cache": "diversification",
    "_avg_corr_cache": "diversification",
    "_risk_pairs_cache": "diversification",
    "_div_label_cache": "diversification",
    "_corr_coverage_cache": "diversification",
    "_acct_gate_cache": "concentration limits",
}


def _dimension_names(stale: dict) -> tuple:
    """(names, is_plural) — human dimension names, deduped and stably ordered.

    Names WHAT is behind rather than printing session-state keys — a banner
    reading `_corr_df_cache` tells a user nothing they can act on.
    """
    seen, names = set(), []
    for k in sorted(stale):
        label = _DIMENSION_LABELS.get(k)
        if label and label not in seen:
            seen.add(label)
            names.append(label)
    if not names:
        return "Some figures", True
    if len(names) == 1:
        return names[0].capitalize(), False
    return ", ".join(names[:-1]).capitalize() + f" and {names[-1]}", True


# ── Per-surface narrowing (F-260 Phase 1, review finding ①) ──────────────────
# Which registry caches each banner-bearing surface ACTUALLY reads, keyed by the
# `key_suffix` its _render_portfolio_stale_banner call already passes — so
# narrowing needs no change at the 14 call sites.
#
# MEASURED, not guessed: derived by walking each `elif page ==` block in app.py
# and recording which registry keys appear in it. Three surfaces read NONE, and
# without this map they would have rendered a 14-dimension warning about
# figures they do not display — including the clause "an action may be shown
# here that the app would otherwise hold back", which is simply false on a page
# that shows no suggestions. A banner that fabricates its own scope is the
# defect class this feature exists to close, so it must not be introduced by
# the fix.
#
# An unknown suffix falls back to the FULL registry (see keys_for_surface):
# over-reporting is the safe direction, and a new surface that forgets to
# register here gets a broader banner rather than a silent one.
SURFACE_KEYS: dict[str, tuple] = {
    "sm":    ("_fragility_cache", "_reduce_calls", "_structural_alert_cache"),
    "aa":    ("_grow_composites",),
    "ra":    ("_avg_corr_cache", "_corr_coverage_cache", "_corr_df_cache",
              "_div_label_cache", "_div_score_cache", "_fragility_cache",
              "_leverage_cache", "_port_risk_cache", "_risk_pairs_cache"),
    "pi":    ("_corr_df_cache",),
    "pa":    ("_acct_gate_cache", "_reduce_calls"),
    "ph":    ("_avg_corr_cache", "_div_score_cache", "_fragility_cache",
              "_highbeta_share", "_port_risk_cache"),
    "macro": (),
    "an":    ("_port_risk_cache", "_reduce_calls"),
    "tj":    ("_acct_gate_cache", "_grow_composites", "_highbeta_share", "_port_risk_cache"),
    "tr":    (),
    "acct":  ("_leverage_cache",),
    "cw":    ("_grow_composites",),
    "ec":    (),
    "me":    ("_port_risk_cache", "_reduce_calls"),
}


def keys_for_surface(suffix: str):
    """Registry keys a surface reads, or None meaning "all of them".

    None (not an empty tuple) is the unknown-surface fallback, because an empty
    tuple legitimately means "this page reads none of them, stay silent" — the
    correct answer for 🪞 Trade Review, 📅 Economic Calendar and 🌐 Macro.
    Collapsing those two would either silence every unregistered surface or
    make three pages claim staleness in dimensions they never show.
    """
    return SURFACE_KEYS.get(suffix)
