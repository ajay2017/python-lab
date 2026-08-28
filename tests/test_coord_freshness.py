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
        """📈 Analysis, 🎯 My Edge and 🧑‍⚖️ The Judge read _reduce_calls, so the
        suppression clause is TRUE there and must survive narrowing.

        `judge` added 2026-08-28: The Judge feeds _reduce_calls straight into
        audit_coherence as the set it checks protective vetoes against, so a
        stale one makes the audit report "no gap" against yesterday's reduce
        set — a confident false negative on the page whose only authority IS
        the audit. It read the cache from the start; what it lacked was any
        banner call, so the staleness was real but unsayable."""
        for suffix in ("an", "me", "judge"):
            sev, msg = cf.decide_stale_banner(
                cf.not_fresh_keys(self._stale(), 2, cf.keys_for_surface(suffix))
            )
            assert sev == "warn", suffix
            assert "SUPPRESS" in msg, suffix

    def test_a_decorative_only_surface_stays_a_caption(self):
        """🔔 Catalyst Watch reads only _grow_composites (decorative).

        This used 💰 Account until 2026-08-28, when review found that mapping
        was a FALSE claim — app.py ~30041 says Account computes its leverage
        panel live, "NOT from _leverage_cache" — so `acct` is now () and the
        test's premise no longer held. The failure was correct and is recorded
        rather than papered over: a surface list is a claim about what a page
        reads, and a wrong one captions a live figure as stale."""
        sev, msg = cf.decide_stale_banner(
            cf.not_fresh_keys(self._stale(), 2, cf.keys_for_surface("cw"))
        )
        assert sev == "caption"
        assert "SUPPRESS" not in msg

    def test_watchlist_warns_because_its_sector_gate_is_gate_tier(self):
        """📋 Watchlist is the ENTER NOW / sizing surface. It reads
        _grow_today_sectors_cache, whose own copy in app.py calls it a
        sector-overlap GATE on Ready-to-Enter, so staleness there must
        escalate to a warning rather than a quiet caption."""
        sev, msg = cf.decide_stale_banner(
            cf.not_fresh_keys(self._stale(), 2, cf.keys_for_surface("wl"))
        )
        assert sev == "warn"
        # The suppression clause is what makes this a warning rather than a
        # caption, and it is TRUE here: a stale sector-overlap gate can let
        # through a Ready-to-Enter the app would otherwise hold back.
        assert "SUPPRESS" in msg
        # Copy discipline: a banner on the sizing surface must name dimensions,
        # never session_state keys.
        for _k in cf.keys_for_surface("wl"):
            assert _k not in msg
        assert set(cf.keys_for_surface("wl")) == {
            "_grow_today_sectors_cache", "_port_risk_cache", "_risk_high_alerts_cache",
        }

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

# Portfolio-derived, but read ONLY by the page that produces them (measured
# 2026-08-28 by mapping every write and read in app.py). A page reading its own
# cache is not a cross-surface staleness problem — it recomputed it moments
# earlier in the same run — so the banner has nothing to say about them. Kept as
# a NAMED bucket rather than left unclassified, so the drift guard stays exact.
_SELF_CONSUMED_BY_PRODUCER = {
    "_day_shock_cache",          # Home only
    "_grow_composites_coverage", # Home only
    "_broker_drift_cache",       # Home only
}

# Was 14 on 2026-08-28 when the drift guard first exposed the gap. Now empty:
# 11 were classified into PORTFOLIO_DEPENDENT_KEYS (with a producer, tier,
# dimension label and measured per-surface mapping) and 3 into
# _SELF_CONSUMED_BY_PRODUCER. Kept as an explicit empty set rather than deleted,
# because the NEXT unclassified cache should land here and be argued about
# rather than quietly widening the tracked set.
_PORTFOLIO_DERIVED_UNTRACKED: set = set()


def test_every_documented_coordination_cache_is_classified():
    """No cache may be silently unclassified. A new one must be tracked or
    explicitly excluded with a reason — never left to default to 'fresh'."""
    from stock_analyzer import coord_freshness as _cf
    classified = (set(_cf.PORTFOLIO_DEPENDENT_KEYS) | _REFRESHED_BY_REPUBLISHER
                  | _NOT_PORTFOLIO_DERIVED | _SELF_INVALIDATING
                  | _PORTFOLIO_DERIVED_UNTRACKED | _SELF_CONSUMED_BY_PRODUCER)
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
                           ("untracked", _PORTFOLIO_DERIVED_UNTRACKED),
                           ("self-consumed", _SELF_CONSUMED_BY_PRODUCER)):
        overlap = excluded & set(cf.PORTFOLIO_DEPENDENT_KEYS)
        assert not overlap, f"{name} set also claims to be tracked: {sorted(overlap)}"


def test_the_untracked_gap_is_closed_and_stays_closed():
    """Was 14 when the guard first exposed it; now 0. Growing it again must be
    a deliberate, explained edit rather than drift."""
    assert _PORTFOLIO_DERIVED_UNTRACKED == set()


class TestProducerScoping:
    """The invariant that stops a freshness LAUNDER (2026-08-28, second pass).

    Measuring the 14 previously-untracked caches showed 4 are NOT produced by
    🏠 Home — the three `_mirror_*` keys come from 🎯 My Edge and
    `_pi_factor_tilt_cache` from a BUTTON on 🧩 Intelligence. Home's blanket
    `_stamp_coord()` would have marked all four current merely because Home ran,
    claiming pre-trade data was fresh. That is the same defect the review caught
    on the memo-HIT path, and it would have been introduced by the fix that was
    supposed to close the gap.
    """

    def test_every_key_declares_a_producer(self):
        for key, meta in cf.PORTFOLIO_DEPENDENT_KEYS.items():
            assert meta.get("producer"), f"{key} has no producer — Home would stamp it by default"

    def test_the_producers_partition_the_registry(self):
        """Every key belongs to exactly one producer, and the producers together
        cover the whole registry — otherwise a key is either never stamped
        (permanently stale) or stamped twice by different pages."""
        # NOTE: `_acct_gate_cache` genuinely has TWO writers — 🏠 Home and the
        # module-scope post-trade republisher — which this single-`producer`
        # model cannot express. Benign, because the republisher stamps that key
        # explicitly itself; recorded so the partition below is not mistaken for
        # ground truth about write sites.
        producers = {m["producer"] for m in cf.PORTFOLIO_DEPENDENT_KEYS.values()}
        covered = [k for p in producers for k in cf.keys_for_producer(p)]
        assert sorted(covered) == sorted(cf.PORTFOLIO_DEPENDENT_KEYS)
        assert len(covered) == len(set(covered)), "a key is claimed by two producers"

    def test_home_does_not_claim_the_caches_it_does_not_produce(self):
        """THE launder guard. If any of these ever appears in Home's stamp set,
        a Home visit would certify a My Edge / Intelligence cache it never
        recomputed."""
        home = set(cf.keys_for_producer("home"))
        for key in ("_mirror_orphans", "_mirror_overexp", "_mirror_overhangs",
                    "_pi_factor_tilt_cache"):
            assert key not in home, f"Home must not stamp {key}"

    def test_the_non_home_producers_are_named_and_non_empty(self):
        assert cf.keys_for_producer("my_edge") == (
            "_mirror_orphans", "_mirror_overexp", "_mirror_overhangs")
        assert cf.keys_for_producer("intelligence") == ("_pi_factor_tilt_cache",)

    def test_an_unknown_producer_claims_nothing(self):
        assert cf.keys_for_producer("no_such_page") == ()


class TestSurfaceMapCoversTheWidenedRegistry:
    def test_every_surface_key_is_registered(self):
        for suffix, keys in cf.SURFACE_KEYS.items():
            for k in keys:
                assert k in cf.PORTFOLIO_DEPENDENT_KEYS, f"{suffix} lists unknown {k}"

    def test_the_newly_tracked_keys_actually_reach_a_surface(self):
        """A key tracked but mapped to no surface is inert — it would never be
        reported. These four have known cross-page consumers, measured from
        app.py, so each must appear in at least one surface's list."""
        mapped = {k for keys in cf.SURFACE_KEYS.values() for k in keys}
        for key in ("_dpnl_cache", "_risk_advisor_recs_cache",
                    "_mirror_orphans", "_pi_factor_tilt_cache"):
            assert key in mapped, f"{key} is tracked but reaches no banner surface"

    def test_summary_surface_tracks_leverage_for_the_safety_strip(self):
        """F-204 (2026-08-28): the redesigned Summary opens with a colour-coded
        Book Safety strip built from _leverage_cache, so a post-trade epoch bump
        must be able to tell that surface its leverage figure is stale. Pinned
        because under-reporting is the unsafe direction here: the strip renders
        an affirmative safety colour, not a passive figure."""
        assert "_leverage_cache" in cf.SURFACE_KEYS["sm"]

    def test_broker_drift_is_deliberately_not_portfolio_dependent(self):
        """_broker_drift_cache is EXTERNAL broker ground truth, refreshed by the
        broker sync lane rather than derived from the book, so a portfolio epoch
        bump must NOT mark it stale. Recorded as an assertion so a future
        "complete the registry" pass cannot quietly add it."""
        assert "_broker_drift_cache" not in cf.PORTFOLIO_DEPENDENT_KEYS

    def test_tracked_but_unmapped_keys_are_deliberate_not_forgotten(self):
        """Every tracked key now reaches at least one banner surface. This was
        {_risk_high_alerts_cache, _grow_today_sectors_cache} until 2026-08-28,
        when 📋 Watchlist gained a banner — both were unmapped for the same
        single reason, that their cross-page consumer had no
        _render_portfolio_stale_banner call at all. Kept as an equality
        assertion rather than deleted: a NEW tracked key that reaches no
        surface is inert, and this is what says so."""
        mapped = {k for keys in cf.SURFACE_KEYS.values() for k in keys}
        unmapped = set(cf.PORTFOLIO_DEPENDENT_KEYS) - mapped
        assert unmapped == set()


class TestStampScopingCannotLaunder:
    """BLOCKING review finding, 2026-08-28. `_stamp_coord` used
    `for k in (keys or PORTFOLIO_DEPENDENT_KEYS)`, so an EMPTY producer tuple —
    the honest answer for "this page owns nothing yet" — fell through `or` and
    stamped ALL keys fresh. That is the launder the `producer` field exists to
    prevent, in the helper the whole mechanism trusts, and this change is what
    made it reachable: before it, every call site passed None or a non-empty
    literal, never a computed value.

    The helper lives in app.py (untestable), so these pin the DATA-SIDE
    invariant that makes the bug impossible to express: an empty producer set
    must never resolve to "everything".
    """

    def test_an_unknown_producer_yields_an_empty_tuple_not_the_whole_registry(self):
        empty = cf.keys_for_producer("no_such_page")
        assert empty == ()
        assert empty != tuple(cf.PORTFOLIO_DEPENDENT_KEYS)

    def test_empty_and_none_are_different_arguments(self):
        """The distinction `or` destroys: () means "stamp nothing", None means
        "stamp everything". If these ever compare equal the bug is back."""
        assert cf.keys_for_producer("no_such_page") is not None
        assert () != None  # noqa: E711 — the point is that they are distinguishable

    def test_not_fresh_keys_already_honours_the_same_rule(self):
        """Its sibling got this right; the stamp helper did not. Pinned so the
        two cannot drift apart again."""
        stale = {k: 0 for k in cf.PORTFOLIO_DEPENDENT_KEYS}
        assert cf.not_fresh_keys(stale, 1, ()) == {}
        assert len(cf.not_fresh_keys(stale, 1, None)) == len(cf.PORTFOLIO_DEPENDENT_KEYS)


class TestApplyStampsRefusesTheOfflineSentinel:
    """BLOCKING review finding, 2026-08-28, found on the first change to route a
    GATE-tier key to a banner (📋 Watchlist).

    `_stamp_coord` stamped on PRESENCE (`k in st.session_state`). `None` is the
    offline sentinel for every key in this registry, and a sentinel is present —
    so a FAILED producer was recorded as a freshness fact, and after the next
    epoch bump `classify` read it as STALE. That is ABSENT collapsed into STALE,
    which this module's docstring forbids outright.

    It was visible on Watchlist because that page also renders the
    `_wl_brief_offline` banner: one said the gate could not run, the other said
    it ran against an older book. Both on screen, contradicting each other, on
    the ENTER NOW / sizing surface.
    """

    _KEY = "_grow_today_sectors_cache"   # gate tier
    _OTHER = "_port_risk_cache"          # decision tier

    def test_a_published_none_is_never_stamped(self):
        out = cf.apply_stamps({}, {self._KEY: None}, [self._KEY], 0)
        assert self._KEY not in out
        assert cf.classify(out, 0, self._KEY) == cf.ABSENT

    def test_a_real_value_is_stamped(self):
        out = cf.apply_stamps({}, {self._KEY: {"Tech"}}, [self._KEY], 3)
        assert out[self._KEY] == 3
        assert cf.classify(out, 3, self._KEY) == cf.FRESH

    def test_an_empty_container_still_counts_as_an_answer(self):
        """The sentinel contract's other half: `[]`/`{}` mean "checked, nothing
        found", which IS a real answer and must be stamped. Only `None` is the
        failure. Collapsing these would be the mirror-image defect."""
        for empty in ([], {}, set(), 0, 0.0, False, ""):
            out = cf.apply_stamps({}, {self._KEY: empty}, [self._KEY], 1)
            assert out.get(self._KEY) == 1, f"{empty!r} was treated as a failure"

    def test_a_later_failure_clears_an_earlier_stamp(self):
        """The second route to the same bug. Stamps persist across runs, so a
        key that published a real value earlier and whose producer failed later
        would keep the old stamp and read STALE after the next book change."""
        good = cf.apply_stamps({}, {self._KEY: {"Tech"}}, [self._KEY], 0)
        assert good[self._KEY] == 0
        after_failure = cf.apply_stamps(good, {self._KEY: None}, [self._KEY], 0)
        assert self._KEY not in after_failure
        # Epoch bumps (a trade); the key must read ABSENT, never STALE.
        assert cf.classify(after_failure, 1, self._KEY) == cf.ABSENT

    def test_the_reproduction_that_produced_the_contradictory_banner(self):
        """End to end: Home ran, its Brief threw (two sentinels published), the
        user logged a trade elsewhere, then opened 📋 Watchlist. Before the fix
        this warned that all three dimensions "still describe the book from
        before your last trade" — about two values that never existed."""
        values = {
            "_grow_today_sectors_cache": None,   # producer failed
            "_risk_high_alerts_cache": None,     # producer failed
            "_port_risk_cache": {"beta": 1.2},   # genuinely computed
        }
        stamps = cf.apply_stamps({}, values, cf.keys_for_surface("wl"), 0)
        nf = cf.not_fresh_keys(stamps, 1, cf.keys_for_surface("wl"))
        assert nf == {
            "_grow_today_sectors_cache": cf.ABSENT,
            "_risk_high_alerts_cache": cf.ABSENT,
            "_port_risk_cache": cf.STALE,
        }
        # Only the genuinely-stale one speaks, and _port_risk_cache is
        # decision-tier, so it is a quiet caption -- NOT a warning claiming a
        # suppressor is behind. The offline banner above it owns the other two.
        sev, msg = cf.decide_stale_banner(nf)
        assert sev == "caption"
        assert "SUPPRESS" not in msg
        assert "leading sectors" not in msg.lower()

    def test_scope_is_honoured_so_a_producer_cannot_clear_another_s_stamp(self):
        pre = {self._KEY: 0, "_mirror_orphans": 0}
        out = cf.apply_stamps(pre, {self._KEY: None, "_mirror_orphans": None},
                              [self._KEY], 0)
        assert self._KEY not in out
        assert out["_mirror_orphans"] == 0, "cleared a key outside its scope"

    def test_none_scope_means_every_registered_key(self):
        pre = {k: 0 for k in cf.PORTFOLIO_DEPENDENT_KEYS}
        out = cf.apply_stamps(pre, {k: None for k in cf.PORTFOLIO_DEPENDENT_KEYS},
                              None, 1)
        assert out == {}

    def test_unregistered_keys_are_ignored(self):
        out = cf.apply_stamps({}, {"_not_real": 1}, ["_not_real"], 0)
        assert out == {}

    def test_a_malformed_prior_map_does_not_crash_or_wipe(self):
        for junk in (None, "x", 5, []):
            out = cf.apply_stamps(junk, {self._KEY: 1}, [self._KEY], 0)
            assert out == {self._KEY: 0}


class TestBannerRemedyNamesTheRightPage:
    """Second BLOCKING finding. The banner hard-coded "Revisit 🏠 Home to
    refresh", which became FALSE for the four non-Home keys this change added —
    on 🧩 Intelligence it pointed away from the only control that refreshes
    factor exposure, so the banner could never be cleared by following its own
    instruction. Same class as the ENTER NOW copy that told the user to use a
    sizing panel the app had withheld."""

    def test_home_key_names_home(self):
        _, msg = cf.decide_stale_banner({"_reduce_calls": cf.STALE})
        assert "🏠 Home" in msg

    def test_intelligence_key_names_intelligence_not_home(self):
        _, msg = cf.decide_stale_banner({"_pi_factor_tilt_cache": cf.STALE})
        assert "🧩 Intelligence" in msg
        assert "🏠 Home" not in msg, "sends the user somewhere that cannot help"

    def test_my_edge_key_names_my_edge_not_home(self):
        _, msg = cf.decide_stale_banner({"_mirror_orphans": cf.STALE})
        assert "🎯 My Edge" in msg
        assert "🏠 Home" not in msg

    def test_mixed_producers_name_both(self):
        _, msg = cf.decide_stale_banner(
            {"_reduce_calls": cf.STALE, "_mirror_orphans": cf.STALE})
        assert "🏠 Home" in msg and "🎯 My Edge" in msg

    def test_stale_producers_drives_the_refresh_button(self):
        """app.py offers "Refresh from Home" only when this contains "home" —
        otherwise the button visibly does nothing."""
        assert cf.stale_producers({"_pi_factor_tilt_cache": cf.STALE}) == {"intelligence"}
        assert "home" not in cf.stale_producers({"_mirror_orphans": cf.STALE})
        assert "home" in cf.stale_producers({"_reduce_calls": cf.STALE})

    def test_absent_keys_do_not_contribute_a_remedy(self):
        assert cf.stale_producers({"_reduce_calls": cf.ABSENT}) == set()

    def test_every_producer_has_a_label(self):
        """An unlabelled producer does not fail loudly — the remedy sentence
        silently VANISHES and the banner ends mid-thought with no instruction
        at all. Safe direction (it states nothing false) but useless, and it is
        the one shape the tests above miss. Verified by simulation in review."""
        producers = {m["producer"] for m in cf.PORTFOLIO_DEPENDENT_KEYS.values()}
        missing = producers - set(cf.PRODUCER_LABELS)
        assert not missing, (
            f"producers with no PRODUCER_LABELS entry: {sorted(missing)} — the "
            "banner would tell the user nothing about how to clear it."
        )
