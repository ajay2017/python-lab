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
# `producer` is load-bearing, not documentation. A key must be stamped ONLY by
# the page that actually recomputes it: 🏠 Home's blanket stamp would otherwise
# mark 🎯 My Edge's and 🧩 Intelligence's caches fresh merely because Home ran —
# a freshness LAUNDER, the same defect the 2026-08-28 review caught on the
# memo-HIT path. Producers were MEASURED by walking every
# `st.session_state[...] =` write in app.py, not inferred from the key name.
PORTFOLIO_DEPENDENT_KEYS: dict[str, dict] = {
    "_reduce_calls": {
        "producer": "home",
        "tier": TIER_GATE,
        "why": "suppresses ADD suggestions for names under a Reduce/Exit call; "
               "stale means an add can be proposed on a name flagged to trim",
    },
    "_structural_alert_cache": {
        "producer": "home",
        "tier": TIER_GATE,
        "why": "correlation-cluster formation; persists into portfolio_thesis, "
               "so a wrong value poisons next week's HELD/SHIFTED baseline",
    },
    "_port_risk_cache": {
        "producer": "home",
        "tier": TIER_DECISION,
        "why": "portfolio beta; feeds fragility, the risk advisor and the "
               "persisted decision_context row captured at trade time",
    },
    "_fragility_cache": {
        "producer": "home",
        "tier": TIER_DECISION,
        "why": "book fragility; a fabricated neutral drops a 25-point penalty "
               "into the 🏆 Health A-F grade shown as fact",
    },
    "_highbeta_share": {
        "producer": "home",
        "tier": TIER_DECISION,
        "why": "high-beta concentration; a graded 🏆 Health dimension",
    },
    "_corr_df_cache": {
        "producer": "home",
        "tier": TIER_DECISION,
        "why": "the correlation matrix itself; adding or removing one name "
               "genuinely moves it, and the diversification SCORE is derived",
    },
    "_grow_composites": {
        "producer": "home",
        "tier": TIER_DECORATIVE,
        "why": "per-ticker composites for Grow Today; network-bound, and the "
               "surfaces recompute or re-rank before display",
    },
    "_leverage_cache": {
        "producer": "home",
        "tier": TIER_DECORATIVE,
        "why": "margin/leverage awareness. Documented as read-only and NEVER "
               "gating (F-09d) — so a quiet surface here is a pure loss with "
               "no compensating safety benefit; disclose, never withhold",
    },
    "_acct_gate_cache": {
        "producer": "home",
        "tier": TIER_GATE,
        "why": "the concentration gate's own denominator. Listed even though "
               "the post-trade republisher DOES refresh it (F-260 Phase 0), so "
               "the registry is a complete map of portfolio-dependent caches "
               "rather than only the un-refreshed ones — and so a future "
               "regression that stops refreshing it shows up as stale rather "
               "than as a silent gap in the registry",
    },
    # ── Added 2026-08-28 (second pass). Phase 1's registry covered the seven
    # siblings §11 enumerated; the drift guard then proved it was never a
    # complete census. These 11 have CROSS-PAGE consumers and go stale after a
    # trade exactly like the originals.
    "_actions_cache": {
        "producer": "home", "tier": TIER_DECISION,
        "why": "rebalance actions; read by 📒 Trade Journal and 📡 Signals & Advice",
    },
    "_alert_list_cache": {
        "producer": "home", "tier": TIER_DECISION,
        "why": "the portfolio alert list behind 📡 Signals & Advice",
    },
    "_div_recs_cache": {
        "producer": "home", "tier": TIER_DECISION,
        "why": "diversification ADD suggestions; a stale one can propose a sector the trade just filled",
    },
    "_risk_high_alerts_cache": {
        "producer": "home", "tier": TIER_DECISION,
        "why": "drives the SIDEBAR NAV BADGE (module scope, app.py ~2804, so every "
               "page) AND 📋 Watchlist's active-risk-alert caution (~22736/22743). "
               "The widest-reach cache here; it is unmapped because Watchlist has "
               "no banner call, NOT because the nav badge is mere chrome",
    },
    "_risk_advisor_recs_cache": {
        "producer": "home", "tier": TIER_DECISION,
        "why": "risk-advisor recommendations incl. TRIM calls; read by 🔗 Risk Analysis and 🥧 Portfolio Overview",
    },
    "_dpnl_cache": {
        "producer": "home", "tier": TIER_DECISION,
        "why": "Tier-B day P&L; 🧾 Summary renders it rather than recomputing, so a stale value shows as today's number",
    },
    "_grow_today_sectors_cache": {
        "producer": "home", "tier": TIER_GATE,
        "why": "sector-overlap GATE input on 📋 Watchlist (app.py ~22736), which "
               "that page's own banner calls a gate on Ready-to-Enter. I first "
               "tiered this DECORATIVE as 'an adornment' — wrong, and the wrong "
               "direction: under this registry's own definition (can let through "
               "an action the app would otherwise withhold) it qualifies as gate. "
               "Inert only while Watchlist has no banner call at all",
    },
    "_mirror_orphans": {
        "producer": "my_edge", "tier": TIER_DECISION,
        "why": "Investor-Mirror orphans; consumed by 📈 Analysis. NOT Home-produced — see the producer note",
    },
    "_mirror_overexp": {
        "producer": "my_edge", "tier": TIER_DECISION,
        "why": "Investor-Mirror over-exposure; consumed by 📈 Analysis",
    },
    "_mirror_overhangs": {
        "producer": "my_edge", "tier": TIER_DECISION,
        "why": "Investor-Mirror overhangs; consumed by 📈 Analysis",
    },
    "_pi_factor_tilt_cache": {
        "producer": "intelligence", "tier": TIER_DECISION,
        "why": "style-factor exposure; consumed by 🔗 Risk Analysis and by both LLM narrative surfaces (F-260). Produced only by a BUTTON on 🧩 Intelligence",
    },
    "_div_score_cache":     {"producer": "home", "tier": TIER_DECORATIVE, "why": "derived from _corr_df_cache"},
    "_avg_corr_cache":      {"producer": "home", "tier": TIER_DECORATIVE, "why": "derived from _corr_df_cache"},
    "_risk_pairs_cache":    {"producer": "home", "tier": TIER_DECORATIVE, "why": "derived from _corr_df_cache"},
    "_div_label_cache":     {"producer": "home", "tier": TIER_DECORATIVE, "why": "derived from _corr_df_cache"},
    "_corr_coverage_cache": {"producer": "home", "tier": TIER_DECORATIVE, "why": "describes _corr_df_cache's sample"},
}

FRESH, STALE, ABSENT = "fresh", "stale", "absent"

# The remedy must name the page that can actually clear the staleness. Before
# 2026-08-28 the banner hard-coded "Revisit 🏠 Home", which became FALSE the
# moment non-Home producers entered the registry: on 🧩 Intelligence it pointed
# away from the only control that refreshes factor exposure, and the banner
# could never be cleared by following its own instruction. A banner asserting a
# remedy that does not work is the same defect class as the ENTER NOW copy that
# told the user to "use the position sizing below" when that panel had been
# withheld.
PRODUCER_LABELS = {
    "home":         "🏠 Home",
    "my_edge":      "🎯 My Edge",
    "intelligence": "🧩 Intelligence (reload factor exposure)",
}


def stale_producers(not_fresh: dict | None) -> set:
    """Producers whose caches are STALE — i.e. which pages can clear this.

    Exposed separately so a renderer can decide whether to offer a
    "Refresh from Home" button at all: offering it when no Home-produced key is
    stale sends the user somewhere that changes nothing.
    """
    return {
        PORTFOLIO_DEPENDENT_KEYS[k]["producer"]
        for k, v in (not_fresh or {}).items()
        if v == STALE and k in PORTFOLIO_DEPENDENT_KEYS
    }


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
    producers = stale_producers(stale)
    labels = [PRODUCER_LABELS[p] for p in sorted(producers) if p in PRODUCER_LABELS]
    if len(labels) == 1:
        msg += f" Revisit {labels[0]} to refresh."
    elif labels:
        msg += f" Revisit {' and '.join(labels)} to refresh."
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
    "_actions_cache": "rebalance actions",
    "_alert_list_cache": "portfolio alerts",
    "_div_recs_cache": "diversification",
    "_risk_high_alerts_cache": "portfolio alerts",
    "_risk_advisor_recs_cache": "risk recommendations",
    "_dpnl_cache": "today's P&L",
    "_grow_today_sectors_cache": "leading sectors",
    "_mirror_orphans": "Investor Mirror",
    "_mirror_overexp": "Investor Mirror",
    "_mirror_overhangs": "Investor Mirror",
    "_pi_factor_tilt_cache": "factor exposure",
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
    # 🧾 Summary reads three of these through the `_home_synth_cache` BUNDLE
    # (app.py ~11129) rather than by key name, so the block-grep regeneration
    # could not see them. Recorded because under-reporting is the unsafe
    # direction: a surface that reads a stale cache and is never told.
    "sm":    ("_alert_list_cache", "_div_label_cache", "_div_score_cache",
              "_dpnl_cache", "_fragility_cache", "_reduce_calls",
              "_structural_alert_cache"),
    "aa":    ("_actions_cache", "_alert_list_cache", "_div_recs_cache", "_grow_composites"),
    "ra":    ("_avg_corr_cache", "_corr_coverage_cache", "_corr_df_cache", "_div_label_cache",
              "_div_score_cache", "_fragility_cache", "_leverage_cache", "_pi_factor_tilt_cache",
              "_port_risk_cache", "_risk_advisor_recs_cache", "_risk_pairs_cache"),
    "pi":    ("_corr_df_cache", "_pi_factor_tilt_cache"),
    "pa":    ("_acct_gate_cache", "_div_recs_cache", "_reduce_calls", "_risk_advisor_recs_cache"),
    "ph":    ("_avg_corr_cache", "_div_score_cache", "_fragility_cache", "_highbeta_share",
              "_port_risk_cache"),
    "macro": (),
    "an":    ("_mirror_orphans", "_mirror_overexp", "_mirror_overhangs", "_port_risk_cache",
              "_reduce_calls"),
    "judge": ("_reduce_calls",),
    "tj":    ("_acct_gate_cache", "_actions_cache", "_grow_composites", "_highbeta_share",
              "_port_risk_cache"),
    "tr":    (),
    # NOT ("_leverage_cache",): app.py ~30041 states outright that 💰 Account
    # computes its panel live, "NOT from _leverage_cache". Claiming it here
    # would caption a figure recomputed this run as stale. Pre-existing Phase 1
    # error, found in review 2026-08-28.
    "acct":  (),
    "cw":    ("_grow_composites",),
    "ec":    (),
    "me":    ("_mirror_orphans", "_mirror_overexp", "_mirror_overhangs", "_port_risk_cache",
              "_reduce_calls"),
}


def keys_for_producer(producer: str) -> tuple:
    """Keys a given page is responsible for stamping.

    The stamp must be scoped to the producer, never blanket: 🏠 Home running
    does not make 🎯 My Edge's or 🧩 Intelligence's caches current, and stamping
    them anyway would launder stale data into a positive freshness claim — the
    exact failure the 2026-08-28 review caught on the memo-HIT path.
    """
    return tuple(sorted(
        k for k, v in PORTFOLIO_DEPENDENT_KEYS.items() if v.get("producer") == producer
    ))


def keys_for_surface(suffix: str):
    """Registry keys a surface reads, or None meaning "all of them".

    None (not an empty tuple) is the unknown-surface fallback, because an empty
    tuple legitimately means "this page reads none of them, stay silent" — the
    correct answer for 🪞 Trade Review, 📅 Economic Calendar and 🌐 Macro.
    Collapsing those two would either silence every unregistered surface or
    make three pages claim staleness in dimensions they never show.
    """
    return SURFACE_KEYS.get(suffix)
