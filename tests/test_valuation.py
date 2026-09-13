"""
Tests for stock_analyzer/valuation.py::valuation_score() — one of the 4
composite scoring pillars, previously with zero test coverage despite
directly feeding the engine's Buy/Sell recommendations.
"""
from stock_analyzer.valuation import valuation_score
import pytest

pytestmark = pytest.mark.fast


def test_no_data_at_all_defaults_to_neutral_50():
    score, signals, _ = valuation_score({}, {}, None)
    assert score == 50.0
    assert signals == {}


# ─── Forward P/E ──────────────────────────────────────────────────────────────

def test_pe_cheap_scores_full_points():
    score, signals, _ = valuation_score({"forward_pe": 10.0}, {}, None)
    assert score == 100.0  # 25/25
    assert "Cheap" in signals["Forward P/E"]


def test_pe_fair_scores_19_of_25():
    score, signals, _ = valuation_score({"forward_pe": 20.0}, {}, None)  # _default: 15 < 20 <= 28
    assert score == round(19 / 25 * 100, 1)
    assert "Fair value" in signals["Forward P/E"]


def test_pe_moderately_expensive_scores_10_of_25():
    score, signals, _ = valuation_score({"forward_pe": 35.0}, {}, None)  # <= 45 (exp)
    assert score == round(10 / 25 * 100, 1)
    assert "Moderately expensive" in signals["Forward P/E"]


def test_pe_expensive_scores_2_of_25():
    score, signals, _ = valuation_score({"forward_pe": 60.0}, {}, None)
    assert score == round(2 / 25 * 100, 1)
    assert "Expensive" in signals["Forward P/E"]


def test_pe_zero_or_negative_excluded_from_scoring():
    score, signals, _ = valuation_score({"forward_pe": 0}, {}, None)
    assert score == 50.0  # max_points stayed 0 — never included
    assert "Forward P/E" not in signals

    score2, signals2, _ = valuation_score({"forward_pe": -5.0}, {}, None)
    assert score2 == 50.0
    assert "Forward P/E" not in signals2


def test_pe_is_sector_relative_same_pe_different_sectors():
    # P/E of 16: NOT cheap for "_default" (pe_cheap=15) -> Fair (19pts);
    # IS cheap for "Real Estate" (pe_cheap=25) -> 25pts. Same input number,
    # different sector norms must produce a different score.
    default_score, _, _ = valuation_score({"forward_pe": 16.0}, {}, None, sector="_default")
    re_score, _, _ = valuation_score({"forward_pe": 16.0}, {}, None, sector="Real Estate")
    assert default_score < re_score
    assert re_score == 100.0


def test_unknown_sector_falls_back_to_default_norms():
    a, _, _ = valuation_score({"forward_pe": 16.0}, {}, None, sector="Not A Real Sector")
    b, _, _ = valuation_score({"forward_pe": 16.0}, {}, None, sector="_default")
    assert a == b


# ─── FCF Yield ────────────────────────────────────────────────────────────────

def test_fcf_yield_excellent_scores_full_points():
    score, signals, _ = valuation_score({"fcf_yield": 6.0}, {}, None)
    assert score == 100.0  # 20/20
    assert "Excellent" in signals["FCF Yield"]


def test_fcf_yield_good_scores_15_of_20():
    score, signals, _ = valuation_score({"fcf_yield": 4.0}, {}, None)
    assert score == round(15 / 20 * 100, 1)


def test_fcf_yield_modest_scores_8_of_20():
    score, signals, _ = valuation_score({"fcf_yield": 2.0}, {}, None)
    assert score == round(8 / 20 * 100, 1)


def test_fcf_yield_low_nonneg_scores_3_of_20():
    score, signals, _ = valuation_score({"fcf_yield": 0.5}, {}, None)
    assert score == round(3 / 20 * 100, 1)


def test_fcf_yield_negative_scores_zero_but_still_counted():
    score, signals, _ = valuation_score({"fcf_yield": -2.0}, {}, None)
    assert score == 0.0  # 0/20, but max_points > 0 so NOT the neutral-50 default
    assert "Negative" in signals["FCF Yield"]


def test_fcf_yield_absent_excluded_from_scoring():
    score, signals, _ = valuation_score({}, {}, None)
    assert "FCF Yield" not in signals


# ─── PT Upside ────────────────────────────────────────────────────────────────
#
# 2026-09-13 (analyst-weight-audit Phase B option (c)): PT Upside is an
# analyst-only leg, so — after the renormalization-hole fix below — it can no
# longer be tested fully in ISOLATION; an isolated analyst-only call now
# correctly withholds (val_available=False, score=50.0) regardless of what
# tier the leg itself would have scored. Every test below adds `_PE_ANCHOR`
# (a Forward P/E of 10.0, which always scores a clean 25/25 per
# test_pe_cheap_scores_full_points) purely to unlock val_available=True, and
# the expected `score` is the resulting BLEND of the two legs, not the PT
# leg's own isolated percentage. The qualitative `signals["PT Upside"]` label
# still describes the PT leg alone and is unaffected.

_PE_ANCHOR = {"forward_pe": 10.0}   # always exactly 25/25 -- see note above


def test_pt_upside_strong_scores_full_points():
    score, signals, val_available = valuation_score(_PE_ANCHOR, {"avg_pt": 130.0}, 100.0)  # +30%
    assert val_available is True
    assert score == 100.0  # (25 P/E + 25 PT) / (25 + 25)
    assert "Strong upside" in signals["PT Upside"]


def test_pt_upside_good_scores_20_of_25():
    # +20%, >= GOOD(15) < STRONG(30) -> PT 20/25
    score, _, val_available = valuation_score(_PE_ANCHOR, {"avg_pt": 120.0}, 100.0)
    assert val_available is True
    assert score == round((25 + 20) / (25 + 25) * 100, 1)


def test_pt_upside_modest_scores_12_of_25():
    score, _, val_available = valuation_score(_PE_ANCHOR, {"avg_pt": 108.0}, 100.0)  # +8%
    assert val_available is True
    assert score == round((25 + 12) / (25 + 25) * 100, 1)


def test_pt_upside_neutral_scores_6_of_25():
    score, _, val_available = valuation_score(_PE_ANCHOR, {"avg_pt": 102.0}, 100.0)  # +2%
    assert val_available is True
    assert score == round((25 + 6) / (25 + 25) * 100, 1)


def test_pt_upside_near_scores_2_of_25():
    score, _, val_available = valuation_score(_PE_ANCHOR, {"avg_pt": 98.0}, 100.0)  # -2%, >= NEAR(-5)
    assert val_available is True
    assert score == round((25 + 2) / (25 + 25) * 100, 1)


def test_pt_upside_overvalued_scores_zero():
    score, signals, val_available = valuation_score(_PE_ANCHOR, {"avg_pt": 90.0}, 100.0)  # -10%
    assert val_available is True   # disambiguates from the fabricated-50 withhold sentinel below
    assert score == round((25 + 0) / (25 + 25) * 100, 1)   # == 50.0, but val_available=True here
    assert "overvalued" in signals["PT Upside"]


def test_pt_upside_falls_back_to_financials_analyst_target_when_no_db_coverage():
    # analyst_data.avg_pt absent -> falls back to financials["analyst_target"]
    score, signals, val_available = valuation_score(
        {**_PE_ANCHOR, "analyst_target": 130.0}, {}, 100.0,
    )
    assert val_available is True
    assert score == 100.0
    assert "PT Upside" in signals


def test_pt_upside_excluded_when_no_current_price():
    score, signals, _ = valuation_score({}, {"avg_pt": 130.0}, None)
    assert score == 50.0  # neutral default, nothing scored
    assert "PT Upside" not in signals


def test_pt_upside_excluded_when_price_is_zero():
    score, signals, _ = valuation_score({}, {"avg_pt": 130.0}, 0.0)
    assert "PT Upside" not in signals


# ─── Analyst consensus rating ─────────────────────────────────────────────────
#
# Same 2026-09-13 note as the PT Upside block above: consensus rating is also
# analyst-only, so these use `_PE_ANCHOR` to unlock val_available=True and
# assert the resulting BLEND, not the leg's isolated percentage.

def test_consensus_strong_buy_scores_full_points():
    score, signals, val_available = valuation_score(
        _PE_ANCHOR, {"consensus_label": "Strong Buy", "has_coverage": True}, None,
    )
    assert val_available is True
    assert score == 100.0   # both legs at 100% of their own leg -> blend is still 100%
    assert "Strong Buy" in signals["Analyst Consensus"]


def test_consensus_sell_scores_zero():
    from stock_analyzer.constants import VALUATION_CONSENSUS_PTS
    score, signals, val_available = valuation_score(
        _PE_ANCHOR, {"consensus_label": "Sell", "has_coverage": True}, None,
    )
    assert val_available is True
    max_consensus = max(VALUATION_CONSENSUS_PTS.values())
    assert score == round((25 + 0) / (25 + max_consensus) * 100, 1)
    assert "Analyst Consensus" in signals  # still counted, just 0 pts


def test_consensus_excluded_without_has_coverage():
    score, signals, _ = valuation_score(
        {}, {"consensus_label": "Strong Buy", "has_coverage": False}, None,
    )
    assert score == 50.0  # neutral default — nothing else scored
    assert "Analyst Consensus" not in signals


def test_consensus_label_absent_excluded_even_with_coverage_true():
    score, signals, _ = valuation_score(
        {}, {"consensus_label": None, "has_coverage": True}, None,
    )
    assert "Analyst Consensus" not in signals


def test_unrecognized_consensus_label_scores_zero_but_counted():
    # dict.get(label, 0) — an unrecognized label degrades to 0 pts, not a crash
    from stock_analyzer.constants import VALUATION_CONSENSUS_PTS
    score, signals, val_available = valuation_score(
        _PE_ANCHOR, {"consensus_label": "Neutral-ish", "has_coverage": True}, None,
    )
    assert val_available is True
    max_consensus = max(VALUATION_CONSENSUS_PTS.values())
    assert score == round((25 + 0) / (25 + max_consensus) * 100, 1)
    assert "Analyst Consensus" in signals


# ─── analyst-weight-audit Phase B (2026-09-13): leg-denominator invariant +
# weight compression — planner-designed, owner-approved D1/D2/D4 ────────────

def test_leg_invariant_top_consensus_tier_always_fills_its_own_leg_to_100pct():
    """The invariant chunk 1 restores: whichever VALUATION_CONSENSUS_PTS label
    scores the dict's own maximum must fill its leg to exactly 100%, regardless
    of the dict's absolute values — max_points is derived from the dict
    (`max(VALUATION_CONSENSUS_PTS.values())`), never a hardcoded literal that
    could silently drift out of sync with it. Before this fix, scaling the
    dict down without also scaling this denominator would have turned even
    the BEST rating into a partial-credit outcome on its own leg."""
    # _PE_ANCHOR unlocks val_available=True (analyst-only inputs alone now
    # withhold, Phase B option (c)) — both legs land at 100% of their OWN leg
    # here, so the blend is still 100% regardless of the anchor's presence.
    from stock_analyzer.constants import VALUATION_CONSENSUS_PTS
    top_label = max(VALUATION_CONSENSUS_PTS, key=VALUATION_CONSENSUS_PTS.get)
    score, _, val_available = valuation_score(
        _PE_ANCHOR, {"consensus_label": top_label, "has_coverage": True}, None,
    )
    assert val_available is True
    assert score == 100.0


def test_footprint_consensus_share_of_composite_pinned_to_5_3_pct():
    """Pins the 2026-09-13 compression's actual footprint (all four legs
    present) so a LATER edit to VALUATION_CONSENSUS_PTS that silently changes
    this share fails a test instead of passing unnoticed. Computed, not
    hardcoded: consensus's own max / total pillar max, weighted by the
    pillar's 0.30 composite weight."""
    from stock_analyzer.constants import VALUATION_CONSENSUS_PTS, COMPOSITE_WEIGHTS
    max_consensus = max(VALUATION_CONSENSUS_PTS.values())
    pillar_max = 25 + 20 + 25 + max_consensus   # P/E + FCF + PT Upside + consensus
    consensus_composite_share = (max_consensus / pillar_max) * COMPOSITE_WEIGHTS["valuation"]
    assert consensus_composite_share == pytest.approx(0.053, abs=0.001)


def test_footprint_strong_buy_vs_sell_composite_swing_pinned():
    """The Strong-Buy-vs-Sell composite-point swing (all four legs present)
    should be ~5.3 points post-compression, down from ~9.0 pre-compression —
    the concrete number D1 was approved against."""
    from stock_analyzer.constants import VALUATION_CONSENSUS_PTS, COMPOSITE_WEIGHTS
    max_consensus = max(VALUATION_CONSENSUS_PTS.values())
    pillar_max = 25 + 20 + 25 + max_consensus
    swing_pillar_pts = VALUATION_CONSENSUS_PTS["Strong Buy"] - VALUATION_CONSENSUS_PTS["Sell"]
    swing_composite_pts = (swing_pillar_pts / pillar_max) * 100 * COMPOSITE_WEIGHTS["valuation"]
    assert swing_composite_pts == pytest.approx(5.3, abs=0.1)


def test_sell_stays_a_full_drag_zero_points_unchanged():
    """Regression against ever loosening the protective floor: Sell must
    stay pinned at 0 points. The 2026-09-13 evidence (Sell's measured +alpha
    rested on a small, self-selected sample) explicitly argued AGAINST
    moving Sell up — this pins that decision so a future edit can't drift it
    without failing a test."""
    # _PE_ANCHOR unlocks val_available=True (analyst-only inputs alone now
    # withhold, Phase B option (c)) — the blended score isn't 0.0 anymore,
    # but the underlying dict value (what this test actually pins) is.
    from stock_analyzer.constants import VALUATION_CONSENSUS_PTS
    assert VALUATION_CONSENSUS_PTS["Sell"] == 0
    max_consensus = max(VALUATION_CONSENSUS_PTS.values())
    score, _, val_available = valuation_score(
        _PE_ANCHOR, {"consensus_label": "Sell", "has_coverage": True}, None,
    )
    assert val_available is True
    assert score == round((25 + 0) / (25 + max_consensus) * 100, 1)


def test_buy_and_mixed_remain_valid_keys_even_though_unreachable_in_production():
    """Finding 1 (analyst-weight-audit): a single-firm article can only ever
    produce Strong Buy/Hold/Sell — Buy/Mixed are mechanically unreachable
    given current usage (derive_consensus's own branching), but D4 keeps them
    as coherent, scoreable dict entries rather than removing them, in case a
    genuine multi-firm split ever does land in that band."""
    from stock_analyzer.constants import VALUATION_CONSENSUS_PTS
    assert "Buy" in VALUATION_CONSENSUS_PTS
    assert "Mixed" in VALUATION_CONSENSUS_PTS
    for label in ("Buy", "Mixed"):
        score, signals, _ = valuation_score(
            {}, {"consensus_label": label, "has_coverage": True}, None,
        )
        assert "Analyst Consensus" in signals
        assert 0.0 <= score <= 100.0


# ─── analyst-weight-audit Phase B option (c), 2026-09-13: the renormalization
# hole — val_available now requires at least ONE OBJECTIVE metric (Forward
# P/E or FCF Yield), analyst opinion alone is no longer sufficient ──────────

def test_analyst_only_no_objective_data_withholds_the_verdict():
    """The load-bearing boundary this fix exists for: a ticker with ONLY a
    saved consensus_rating (no forward_pe, no fcf_yield) used to renormalise
    to 100% analyst opinion and still report val_available=True — the pillar
    leaning hardest on its least-measured input at exactly the moment it has
    no objective data. Must now withhold (fabricated neutral 50, val_available
    False), the same G-15 contract the "no data at all" case already used."""
    score, signals, val_available = valuation_score(
        {}, {"consensus_label": "Strong Buy", "has_coverage": True}, None,
    )
    assert val_available is False
    assert score == 50.0
    # The analyst-only signal is still disclosed — a display fact about what
    # was captured, independent of whether the pillar is trustworthy to score.
    assert "Analyst Consensus" in signals


def test_pt_upside_only_no_objective_data_also_withholds():
    # Same boundary via the OTHER analyst-only leg (PT Upside instead of
    # consensus rating) — both analyst legs are equally "not objective."
    score, signals, val_available = valuation_score(
        {}, {"avg_pt": 130.0}, 100.0,
    )
    assert val_available is False
    assert score == 50.0
    assert "PT Upside" in signals


def test_both_analyst_legs_present_still_withholds_without_any_objective_metric():
    score, signals, val_available = valuation_score(
        {}, {"avg_pt": 130.0, "consensus_label": "Strong Buy", "has_coverage": True}, 100.0,
    )
    assert val_available is False
    assert score == 50.0
    assert "PT Upside" in signals
    assert "Analyst Consensus" in signals


def test_forward_pe_alone_is_sufficient_for_val_available():
    # One objective metric, no analyst data at all — the pre-existing
    # behavior for a purely-objective read must be unaffected.
    score, _, val_available = valuation_score({"forward_pe": 10.0}, {}, None)
    assert val_available is True
    assert score == 100.0


def test_fcf_yield_alone_is_sufficient_for_val_available():
    score, _, val_available = valuation_score({"fcf_yield": 6.0}, {}, None)
    assert val_available is True
    assert score == 100.0


def test_one_objective_metric_plus_analyst_legs_is_available_and_blends_correctly():
    # The boundary the fix must NOT break: as soon as ONE objective metric is
    # present, val_available flips back to True and the analyst legs blend
    # in normally (they are not excluded from the numerator/denominator,
    # only from the val_available GATE itself).
    financials = {"forward_pe": 10.0}   # 25/25 objective
    analyst = {"consensus_label": "Strong Buy", "has_coverage": True}   # top tier, full leg
    score, signals, val_available = valuation_score(financials, analyst, None)
    assert val_available is True
    assert score == 100.0   # both legs present score 100% each -> blended 100%
    assert set(signals.keys()) == {"Forward P/E", "Analyst Consensus"}


def test_no_data_at_all_is_a_special_case_of_the_same_withhold_not_a_different_one():
    # The pre-existing "nothing present at all" case is a SUBSET of the new
    # condition (objective_max_points > 0 implies max_points > 0), not a
    # separately-handled branch — confirms the generalisation didn't
    # introduce a second, divergent code path.
    score, signals, val_available = valuation_score({}, {}, None)
    assert val_available is False
    assert score == 50.0
    assert signals == {}


# ─── Combined pillars — graceful degradation & weighting ─────────────────────

def test_all_four_pillars_combine_as_weighted_average():
    financials = {"forward_pe": 10.0, "fcf_yield": 6.0}          # 25/25 + 20/20
    analyst = {
        "avg_pt": 130.0,                                          # 25/25
        "consensus_label": "Strong Buy", "has_coverage": True,    # 30/30
    }
    score, signals, _ = valuation_score(financials, analyst, 100.0)
    assert score == 100.0
    assert set(signals.keys()) == {"Forward P/E", "FCF Yield", "PT Upside", "Analyst Consensus"}


def test_partial_data_only_averages_present_metrics():
    # Only FCF yield present (excellent) -> should score 100, not diluted by
    # absent pillars (graceful degradation: absent metrics affect neither
    # numerator nor denominator).
    score, signals, _ = valuation_score({"fcf_yield": 6.0}, {}, None)
    assert score == 100.0
    assert list(signals.keys()) == ["FCF Yield"]


def test_mixed_strong_and_weak_signals_averages_correctly():
    # Forward P/E expensive (2/25) + FCF excellent (20/20) -> (2+20)/(25+20)*100
    score, _, _ = valuation_score({"forward_pe": 60.0, "fcf_yield": 6.0}, {}, None)
    expected = round((2 + 20) / (25 + 20) * 100, 1)
    assert score == expected
