"""Tests for stock_analyzer/coord_freshness.py (F-260 Phase 1, 2026-08-28).

The mechanism exists because `_refresh_portfolio_cache_after_trade` writes
`_holdings_sig_at_home_build` — the sole input to `_portfolio_snapshot_stale()`
— and thereby SILENCED the app's only cross-page staleness warning on 9 pages
while the portfolio-derived caches still described the pre-trade book.
"""
from stock_analyzer import coord_freshness as cf


class TestClassify:
    def test_three_states_are_distinct(self):
        stamps = {"_reduce_calls": 1, "_leverage_cache": 2}
        assert cf.classify(stamps, 2, "_leverage_cache") == cf.FRESH
        assert cf.classify(stamps, 2, "_reduce_calls") == cf.STALE
        assert cf.classify(stamps, 2, "_fragility_cache") == cf.ABSENT

    def test_absent_never_collapses_into_stale(self):
        """The load-bearing distinction. "I never computed this" and "I have an
        older answer" license different copy — describing a never-computed
        figure as out of date is its own fabrication."""
        assert cf.classify({}, 5, "_reduce_calls") == cf.ABSENT
        assert cf.classify(None, 5, "_reduce_calls") == cf.ABSENT
        assert cf.classify({"_reduce_calls": 4}, 5, "_reduce_calls") == cf.STALE
        assert cf.ABSENT != cf.STALE

    def test_a_stamp_at_or_ahead_of_the_epoch_is_fresh(self):
        assert cf.classify({"_reduce_calls": 3}, 3, "_reduce_calls") == cf.FRESH
        # >= not ==: a stamp ahead of the epoch would mean the epoch went
        # backwards, which cannot happen, and treating it as stale would warn
        # about a cache that is if anything newer than the book.
        assert cf.classify({"_reduce_calls": 4}, 3, "_reduce_calls") == cf.FRESH

    def test_malformed_stamp_reads_absent_not_fresh(self):
        """Fail toward disclosure. A junk stamp must never be believed."""
        for junk in (None, "x", [], {}):
            assert cf.classify({"_reduce_calls": junk}, 1, "_reduce_calls") == cf.ABSENT


class TestNotFreshKeys:
    def test_reports_only_non_fresh_registered_keys(self):
        stamps = {k: 5 for k in cf.PORTFOLIO_DEPENDENT_KEYS}
        assert cf.not_fresh_keys(stamps, 5) == {}
        stamps["_reduce_calls"] = 4
        assert cf.not_fresh_keys(stamps, 5) == {"_reduce_calls": cf.STALE}

    def test_narrowing_to_page_keys_never_warns_about_unshown_dimensions(self):
        stamps = {k: 4 for k in cf.PORTFOLIO_DEPENDENT_KEYS}
        out = cf.not_fresh_keys(stamps, 5, ["_leverage_cache"])
        assert set(out) == {"_leverage_cache"}

    def test_unregistered_keys_are_ignored_rather_than_guessed_at(self):
        assert cf.not_fresh_keys({}, 1, ["_not_a_real_cache"]) == {}


class TestDecideStaleBanner:
    def test_all_fresh_renders_nothing(self):
        assert cf.decide_stale_banner({}) is None
        assert cf.decide_stale_banner(None) is None

    def test_absent_alone_is_silent(self):
        """A cache never produced this session is the normal state of a page
        visited before Home. Warning on it would fire every cold session and
        train the user to ignore the banner."""
        assert cf.decide_stale_banner({"_reduce_calls": cf.ABSENT}) is None

    def test_gate_bearing_stale_escalates_to_warn(self):
        sev, msg = cf.decide_stale_banner({"_reduce_calls": cf.STALE})
        assert sev == "warn"
        assert "SUPPRESS" in msg

    def test_decorative_only_stays_a_quiet_caption(self):
        """The user's explicit 2026-08-28 decision: tier by cache type so the
        banner is not churn. _leverage_cache is documented awareness-only and
        never gates, so it must never shout."""
        sev, msg = cf.decide_stale_banner({"_leverage_cache": cf.STALE})
        assert sev == "caption"
        assert "SUPPRESS" not in msg

    def test_one_gate_key_among_decoratives_still_warns(self):
        sev, _ = cf.decide_stale_banner(
            {"_leverage_cache": cf.STALE, "_reduce_calls": cf.STALE}
        )
        assert sev == "warn"

    def test_message_names_dimensions_not_session_state_keys(self):
        """A banner reading `_corr_df_cache` tells a user nothing actionable."""
        _, msg = cf.decide_stale_banner({"_corr_df_cache": cf.STALE})
        assert "_corr_df_cache" not in msg
        assert "correlation" in msg.lower()

    def test_holdings_are_stated_current_so_the_two_banners_do_not_contradict(self):
        """This branch runs only when _portfolio_snapshot_stale() is False, so
        the copy must not imply the holdings themselves are out of date — the
        stronger branch above it owns that claim."""
        _, msg = cf.decide_stale_banner({"_reduce_calls": cf.STALE})
        assert "holdings are current" in msg.lower()

    def test_singular_and_plural_agree(self):
        _, one = cf.decide_stale_banner({"_leverage_cache": cf.STALE})
        _, two = cf.decide_stale_banner(
            {"_leverage_cache": cf.STALE, "_port_risk_cache": cf.STALE}
        )
        assert "still describes" in one
        assert "still describe " in two

    def test_derived_correlation_keys_collapse_to_one_dimension_name(self):
        _, msg = cf.decide_stale_banner({
            "_div_score_cache": cf.STALE, "_avg_corr_cache": cf.STALE,
            "_risk_pairs_cache": cf.STALE,
        })
        assert msg.lower().count("diversification") == 1


class TestRegistryIntegrity:
    """Bidirectional. A portfolio-dependent key MISSING from the registry reads
    as permanently fresh — a confident false negative inside the mechanism
    built to prevent them."""

    def test_every_key_has_a_valid_tier_and_a_rationale(self):
        for key, meta in cf.PORTFOLIO_DEPENDENT_KEYS.items():
            assert meta["tier"] in (cf.TIER_GATE, cf.TIER_DECISION, cf.TIER_DECORATIVE), key
            assert meta.get("why"), f"{key} has no rationale"

    def test_every_key_has_a_human_dimension_label(self):
        """Without one the banner falls back to 'Some figures', which is the
        vague copy this feature exists to replace."""
        for key in cf.PORTFOLIO_DEPENDENT_KEYS:
            assert key in cf._DIMENSION_LABELS, f"{key} would render as 'Some figures'"

    def test_the_known_gate_bearing_caches_are_registered_as_such(self):
        """Pins the classification that drives severity. Demoting any of these
        to decorative silently downgrades a warning to a caption."""
        for key in ("_reduce_calls", "_structural_alert_cache", "_acct_gate_cache"):
            assert cf.tier_of(key) == cf.TIER_GATE, key

    def test_leverage_is_decorative_because_it_never_gates(self):
        """F-09d documents _leverage_cache as awareness-only. If it ever gates,
        this test should fail and force the tier to be reconsidered."""
        assert cf.tier_of("_leverage_cache") == cf.TIER_DECORATIVE

    def test_unregistered_key_has_no_tier(self):
        assert cf.tier_of("_totally_unregistered") is None


class TestSurfaceNarrowing:
    """Review finding ① — without narrowing, three surfaces that read NONE of
    these caches would render a 14-dimension warning about figures they never
    display, including a suppression clause that is false on a page showing no
    suggestions. A banner that fabricates its own scope is the defect class
    this feature exists to close."""

    _STAMPS_ALL_STALE = None  # built per test

    def _stale(self, epoch=2):
        return {k: epoch - 1 for k in cf.PORTFOLIO_DEPENDENT_KEYS}

    def test_empty_tuple_means_read_nothing_not_read_everything(self):
        """THE trap. `keys or PORTFOLIO_DEPENDENT_KEYS` would turn "this page
        reads none of them" into "report all 14" — the falsy-collapse class,
        reintroduced inside this feature's own helper. It must be `is None`."""
        assert cf.not_fresh_keys(self._stale(), 2, ()) == {}
        assert cf.decide_stale_banner(cf.not_fresh_keys(self._stale(), 2, ())) is None

    def test_none_means_all_keys(self):
        assert len(cf.not_fresh_keys(self._stale(), 2, None)) == len(cf.PORTFOLIO_DEPENDENT_KEYS)

    def test_surfaces_reading_nothing_stay_silent(self):
        """🪞 Trade Review, 📅 Economic Calendar and 🌐 Macro."""
        for suffix in ("tr", "ec", "macro"):
            keys = cf.keys_for_surface(suffix)
            assert keys == (), suffix
            assert cf.decide_stale_banner(
                cf.not_fresh_keys(self._stale(), 2, keys)
            ) is None, suffix

    def test_unknown_surface_falls_back_to_all_not_to_silence(self):
        """Over-reporting is the safe direction: a new surface that forgets to
        register gets a broader banner, never a silent one."""
        assert cf.keys_for_surface("brand_new_page") is None
        assert cf.decide_stale_banner(
            cf.not_fresh_keys(self._stale(), 2, cf.keys_for_surface("brand_new_page"))
        )[0] == "warn"

    def test_a_surface_reading_a_suppressor_still_warns(self):
        """📈 Analysis and 🎯 My Edge read _reduce_calls, so the suppression
        clause is TRUE there and must survive narrowing."""
        for suffix in ("an", "me"):
            sev, msg = cf.decide_stale_banner(
                cf.not_fresh_keys(self._stale(), 2, cf.keys_for_surface(suffix))
            )
            assert sev == "warn", suffix
            assert "SUPPRESS" in msg, suffix

    def test_a_decorative_only_surface_stays_a_caption(self):
        """💰 Account reads only _leverage_cache, documented awareness-only."""
        sev, msg = cf.decide_stale_banner(
            cf.not_fresh_keys(self._stale(), 2, cf.keys_for_surface("acct"))
        )
        assert sev == "caption"
        assert "SUPPRESS" not in msg

    def test_every_registered_surface_lists_only_real_registry_keys(self):
        for suffix, keys in cf.SURFACE_KEYS.items():
            for k in keys:
                assert k in cf.PORTFOLIO_DEPENDENT_KEYS, f"{suffix} lists unknown {k}"

    def test_narrowing_never_reports_a_key_the_surface_does_not_read(self):
        for suffix, keys in cf.SURFACE_KEYS.items():
            reported = set(cf.not_fresh_keys(self._stale(), 2, keys))
            assert reported <= set(keys), suffix


# ── Registry-drift guard (F-260 §12 item 3, 2026-08-28) ──────────────────────
# The failure this prevents: a portfolio-derived coordination cache that is
# MISSING from PORTFOLIO_DEPENDENT_KEYS reads as permanently fresh — a
# confident false negative inside the mechanism built to prevent them, and one
# nothing else would ever surface.
#
# check_antipatterns.py's _SENTINEL_KEYS is the app's existing roster of
# documented coordination caches. Every key on it must be classified here:
# tracked for freshness, or explicitly excluded with a reason. Adding a cache
# without classifying it fails the suite, which is the point.

import importlib.util as _ilu
import pathlib as _pl


def _sentinel_keys():
    spec = _ilu.spec_from_file_location(
        "cap", _pl.Path(__file__).resolve().parents[1] / "scripts" / "check_antipatterns.py"
    )
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod._SENTINEL_KEYS)


# Refreshed by _refresh_portfolio_cache_after_trade itself, so they never go
# stale relative to the book — and their staleness is ALREADY owned by the
# stronger `_portfolio_snapshot_stale()` banner branch. Tracking them here too
# would double-report the same fact in two branches of one banner.
_REFRESHED_BY_REPUBLISHER = {
    "_last_port_df", "_port_df_enriched", "_last_held_data", "_last_held_tickers",
}

# Genuinely not portfolio-derived — they describe the MARKET, and a trade does
# not make them wrong.
_NOT_PORTFOLIO_DERIVED = {
    "_leading_sectors_cache",   # leading sector names from Grow Today
    "_market_tone_cache",       # market tone
}

# Self-invalidating: `_home_synth_cache`'s own signature contains the
# (ticker, shares) frozenset, so a book change misses the memo by construction.
_SELF_INVALIDATING = {"_home_synth_cache"}

# PORTFOLIO-DERIVED BUT NOT YET TRACKED — a real, bounded gap, recorded rather
# than hidden. These go stale after a trade exactly like the tracked ones; the
# banner simply does not mention them yet. Each needs a tier, a dimension label
# and a per-surface mapping before it can move into PORTFOLIO_DEPENDENT_KEYS,
# which is a judgement call per cache rather than a bulk edit. Shrinking this
# set is the follow-up; GROWING it should be deliberate and explained.
_PORTFOLIO_DERIVED_UNTRACKED = {
    "_actions_cache", "_alert_list_cache", "_div_recs_cache",
    "_risk_high_alerts_cache", "_risk_advisor_recs_cache",
    "_dpnl_cache", "_day_shock_cache",
    "_grow_today_sectors_cache", "_grow_composites_coverage",
    "_mirror_orphans", "_mirror_overexp", "_mirror_overhangs",
    "_pi_factor_tilt_cache", "_broker_drift_cache",
}


def test_every_documented_coordination_cache_is_classified():
    """No cache may be silently unclassified. A new one must be tracked or
    explicitly excluded with a reason — never left to default to 'fresh'."""
    from stock_analyzer import coord_freshness as _cf
    classified = (set(_cf.PORTFOLIO_DEPENDENT_KEYS) | _REFRESHED_BY_REPUBLISHER
                  | _NOT_PORTFOLIO_DERIVED | _SELF_INVALIDATING
                  | _PORTFOLIO_DERIVED_UNTRACKED)
    unclassified = _sentinel_keys() - classified
    assert not unclassified, (
        f"Unclassified coordination cache(s): {sorted(unclassified)}. Each must go "
        "in PORTFOLIO_DEPENDENT_KEYS (with a tier + dimension label + surface "
        "mapping) or in one of the exclusion sets here, with a reason. An "
        "unclassified portfolio-derived cache reads as permanently fresh."
    )


def test_no_freshness_key_is_a_typo():
    """Every tracked key must be a real documented coordination cache. A typo'd
    or renamed entry would silently monitor nothing while looking correct."""
    unknown = set(cf.PORTFOLIO_DEPENDENT_KEYS) - _sentinel_keys()
    assert not unknown, f"not documented coordination caches: {sorted(unknown)}"


def test_the_exclusion_sets_do_not_overlap_the_tracked_set():
    """A key claimed as both tracked and excluded means one of the two
    rationales is wrong and nobody would notice which."""
    for name, excluded in (("refreshed", _REFRESHED_BY_REPUBLISHER),
                           ("not-portfolio", _NOT_PORTFOLIO_DERIVED),
                           ("self-invalidating", _SELF_INVALIDATING),
                           ("untracked", _PORTFOLIO_DERIVED_UNTRACKED)):
        overlap = excluded & set(cf.PORTFOLIO_DEPENDENT_KEYS)
        assert not overlap, f"{name} set also claims to be tracked: {sorted(overlap)}"


def test_the_untracked_gap_does_not_grow_silently():
    """Pins the size of the known gap. Shrinking it is the follow-up; growing
    it must be a deliberate, explained edit rather than drift."""
    assert len(_PORTFOLIO_DERIVED_UNTRACKED) == 14
