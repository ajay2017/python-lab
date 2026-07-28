"""
Tests for stock_analyzer/valuation.py::valuation_score() — one of the 4
composite scoring pillars, previously with zero test coverage despite
directly feeding the engine's Buy/Sell recommendations.
"""
from stock_analyzer.valuation import valuation_score


def test_no_data_at_all_defaults_to_neutral_50():
    score, signals = valuation_score({}, {}, None)
    assert score == 50.0
    assert signals == {}


# ─── Forward P/E ──────────────────────────────────────────────────────────────

def test_pe_cheap_scores_full_points():
    score, signals = valuation_score({"forward_pe": 10.0}, {}, None)
    assert score == 100.0  # 25/25
    assert "Cheap" in signals["Forward P/E"]


def test_pe_fair_scores_19_of_25():
    score, signals = valuation_score({"forward_pe": 20.0}, {}, None)  # _default: 15 < 20 <= 28
    assert score == round(19 / 25 * 100, 1)
    assert "Fair value" in signals["Forward P/E"]


def test_pe_moderately_expensive_scores_10_of_25():
    score, signals = valuation_score({"forward_pe": 35.0}, {}, None)  # <= 45 (exp)
    assert score == round(10 / 25 * 100, 1)
    assert "Moderately expensive" in signals["Forward P/E"]


def test_pe_expensive_scores_2_of_25():
    score, signals = valuation_score({"forward_pe": 60.0}, {}, None)
    assert score == round(2 / 25 * 100, 1)
    assert "Expensive" in signals["Forward P/E"]


def test_pe_zero_or_negative_excluded_from_scoring():
    score, signals = valuation_score({"forward_pe": 0}, {}, None)
    assert score == 50.0  # max_points stayed 0 — never included
    assert "Forward P/E" not in signals

    score2, signals2 = valuation_score({"forward_pe": -5.0}, {}, None)
    assert score2 == 50.0
    assert "Forward P/E" not in signals2


def test_pe_is_sector_relative_same_pe_different_sectors():
    # P/E of 16: NOT cheap for "_default" (pe_cheap=15) -> Fair (19pts);
    # IS cheap for "Real Estate" (pe_cheap=25) -> 25pts. Same input number,
    # different sector norms must produce a different score.
    default_score, _ = valuation_score({"forward_pe": 16.0}, {}, None, sector="_default")
    re_score, _ = valuation_score({"forward_pe": 16.0}, {}, None, sector="Real Estate")
    assert default_score < re_score
    assert re_score == 100.0


def test_unknown_sector_falls_back_to_default_norms():
    a, _ = valuation_score({"forward_pe": 16.0}, {}, None, sector="Not A Real Sector")
    b, _ = valuation_score({"forward_pe": 16.0}, {}, None, sector="_default")
    assert a == b


# ─── FCF Yield ────────────────────────────────────────────────────────────────

def test_fcf_yield_excellent_scores_full_points():
    score, signals = valuation_score({"fcf_yield": 6.0}, {}, None)
    assert score == 100.0  # 20/20
    assert "Excellent" in signals["FCF Yield"]


def test_fcf_yield_good_scores_15_of_20():
    score, signals = valuation_score({"fcf_yield": 4.0}, {}, None)
    assert score == round(15 / 20 * 100, 1)


def test_fcf_yield_modest_scores_8_of_20():
    score, signals = valuation_score({"fcf_yield": 2.0}, {}, None)
    assert score == round(8 / 20 * 100, 1)


def test_fcf_yield_low_nonneg_scores_3_of_20():
    score, signals = valuation_score({"fcf_yield": 0.5}, {}, None)
    assert score == round(3 / 20 * 100, 1)


def test_fcf_yield_negative_scores_zero_but_still_counted():
    score, signals = valuation_score({"fcf_yield": -2.0}, {}, None)
    assert score == 0.0  # 0/20, but max_points > 0 so NOT the neutral-50 default
    assert "Negative" in signals["FCF Yield"]


def test_fcf_yield_absent_excluded_from_scoring():
    score, signals = valuation_score({}, {}, None)
    assert "FCF Yield" not in signals


# ─── PT Upside ────────────────────────────────────────────────────────────────

def test_pt_upside_strong_scores_full_points():
    score, signals = valuation_score({}, {"avg_pt": 130.0}, 100.0)  # +30%
    assert score == 100.0  # 25/25
    assert "Strong upside" in signals["PT Upside"]


def test_pt_upside_good_scores_20_of_25():
    score, _ = valuation_score({}, {"avg_pt": 120.0}, 100.0)  # +20%, >= GOOD(15) < STRONG(30)
    assert score == round(20 / 25 * 100, 1)


def test_pt_upside_modest_scores_12_of_25():
    score, _ = valuation_score({}, {"avg_pt": 108.0}, 100.0)  # +8%
    assert score == round(12 / 25 * 100, 1)


def test_pt_upside_neutral_scores_6_of_25():
    score, _ = valuation_score({}, {"avg_pt": 102.0}, 100.0)  # +2%
    assert score == round(6 / 25 * 100, 1)


def test_pt_upside_near_scores_2_of_25():
    score, _ = valuation_score({}, {"avg_pt": 98.0}, 100.0)  # -2%, >= NEAR(-5)
    assert score == round(2 / 25 * 100, 1)


def test_pt_upside_overvalued_scores_zero():
    score, signals = valuation_score({}, {"avg_pt": 90.0}, 100.0)  # -10%
    assert score == 0.0
    assert "overvalued" in signals["PT Upside"]


def test_pt_upside_falls_back_to_financials_analyst_target_when_no_db_coverage():
    # analyst_data.avg_pt absent -> falls back to financials["analyst_target"]
    score, signals = valuation_score({"analyst_target": 130.0}, {}, 100.0)
    assert score == 100.0
    assert "PT Upside" in signals


def test_pt_upside_excluded_when_no_current_price():
    score, signals = valuation_score({}, {"avg_pt": 130.0}, None)
    assert score == 50.0  # neutral default, nothing scored
    assert "PT Upside" not in signals


def test_pt_upside_excluded_when_price_is_zero():
    score, signals = valuation_score({}, {"avg_pt": 130.0}, 0.0)
    assert "PT Upside" not in signals


# ─── Analyst consensus rating ─────────────────────────────────────────────────

def test_consensus_strong_buy_scores_full_points():
    score, signals = valuation_score(
        {}, {"consensus_label": "Strong Buy", "has_coverage": True}, None,
    )
    assert score == 100.0  # 30/30
    assert "Strong Buy" in signals["Analyst Consensus"]


def test_consensus_sell_scores_zero():
    score, signals = valuation_score(
        {}, {"consensus_label": "Sell", "has_coverage": True}, None,
    )
    assert score == 0.0
    assert "Analyst Consensus" in signals  # still counted, just 0 pts


def test_consensus_excluded_without_has_coverage():
    score, signals = valuation_score(
        {}, {"consensus_label": "Strong Buy", "has_coverage": False}, None,
    )
    assert score == 50.0  # neutral default — nothing else scored
    assert "Analyst Consensus" not in signals


def test_consensus_label_absent_excluded_even_with_coverage_true():
    score, signals = valuation_score(
        {}, {"consensus_label": None, "has_coverage": True}, None,
    )
    assert "Analyst Consensus" not in signals


def test_unrecognized_consensus_label_scores_zero_but_counted():
    # dict.get(label, 0) — an unrecognized label degrades to 0 pts, not a crash
    score, signals = valuation_score(
        {}, {"consensus_label": "Neutral-ish", "has_coverage": True}, None,
    )
    assert score == 0.0
    assert "Analyst Consensus" in signals


# ─── Combined pillars — graceful degradation & weighting ─────────────────────

def test_all_four_pillars_combine_as_weighted_average():
    financials = {"forward_pe": 10.0, "fcf_yield": 6.0}          # 25/25 + 20/20
    analyst = {
        "avg_pt": 130.0,                                          # 25/25
        "consensus_label": "Strong Buy", "has_coverage": True,    # 30/30
    }
    score, signals = valuation_score(financials, analyst, 100.0)
    assert score == 100.0
    assert set(signals.keys()) == {"Forward P/E", "FCF Yield", "PT Upside", "Analyst Consensus"}


def test_partial_data_only_averages_present_metrics():
    # Only FCF yield present (excellent) -> should score 100, not diluted by
    # absent pillars (graceful degradation: absent metrics affect neither
    # numerator nor denominator).
    score, signals = valuation_score({"fcf_yield": 6.0}, {}, None)
    assert score == 100.0
    assert list(signals.keys()) == ["FCF Yield"]


def test_mixed_strong_and_weak_signals_averages_correctly():
    # Forward P/E expensive (2/25) + FCF excellent (20/20) -> (2+20)/(25+20)*100
    score, _ = valuation_score({"forward_pe": 60.0, "fcf_yield": 6.0}, {}, None)
    expected = round((2 + 20) / (25 + 20) * 100, 1)
    assert score == expected
